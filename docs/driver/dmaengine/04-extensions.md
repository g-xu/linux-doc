---
title: 扩展机制
---

# 04 — dmaengine 的扩展框架

> 本篇讲 dmaengine 核心之外的六个**可选扩展**。
> 核心机制看 `03-internals.md`，API 清单看 `01-api.md`，DTS 看 `06-devicetree.md`。

## §0 本篇的组织轴：一节一个独立框架

**组织轴声明**：本篇的六节**互不调用**，是六个各自独立的可选层。
你可以只读跟你相关的那一节，不用按顺序读。这跟 `03-internals.md` 不一样 ——
03 是一条时间线，前后有依赖；本篇是六个平行的插件。

唯一的例外是 §2（async_tx）会用到 §1（virt-dma）以外的另一套记账，
两者的边界在 §7 汇总表里点明。

### §0.1 分诊表：arm64 上哪几个你真会碰到

| # | 框架 | 一句话 | 挂在 03 时间线哪一点 | arm64 相关性 |
|---|---|---|---|---|
| §1 | **virt-dma** 虚拟通道层 | 帮控制器驱动白拿"描述符队列 + tasklet 回调" | A2 注册前（`vchan_init`）、A4 全程 | **几乎必碰**。`drivers/dma/` 下 43 个 `.c` 用它 |
| §2 | **async_tx** / RAID 卸载 | 让 md-raid 把 XOR/PQ 计算丢给 DMA 引擎 | A1 percpu 通道表 + A4 | 只在有 RAID 引擎的 SoC 上（`MV_XOR_V2` / `BCM_SBA_RAID` / `XGENE_DMA` 三个都 `depends on ARM64`） |
| §3 | **dma-router** / dmamux | 请求线多于通道时，在中间加一层选路器 | A3 申请通道时（xlate 前面插一层） | ✗ **本树无 arm64 使用者**（见 §3.4 的全树普查） |
| §4 | **描述符 metadata** | 给描述符挂一块带外数据（协议头之类） | A4 prep 之后、submit 之前 | 只有 ti k3-udma 一族用；arm64 上是 K3 平台 |
| §5 | **dma_slave_map** 过滤映射 | 没有 DT 的平台怎么把"设备名+通道名"映射到 filter 参数 | A3 第 2 跳（DT 失败后的兜底） | ✗ 使用者全在 `arch/arm/` 和 `arch/m68k/` |
| §6 | **dmatest** 自测客户端 | 一个纯测试用的消费者模块 | 站在消费者位置，走完整个 A3→A4→A6 | 调新控制器驱动时的第一件事 |

读法建议：**§1 和 §6 是通用的**（写驱动/调驱动都要），
§2 看你的 SoC 有没有 RAID 引擎，§3/§5 在 arm64 上可以只扫标题，
§4 只有你在做 K3 才需要。

### §0.2 六个框架的位置

```
                     消费者（md-raid / uart / spi / dmatest …）
                        │              │
              §2 async_tx 层       直接用核心 API
              （XOR/PQ 语义）       （01-api.md §2~§6）
                        │              │
                        └──────┬───────┘
                               ▼
        ┌──────────────── dmaengine 核心（03-internals.md）────────────────┐
        │  §5 dma_slave_map 兜底映射        §4 metadata 三个转发函数        │
        │  （A3 第2跳）                     （dmaengine.c:1503-1574）       │
        └───────────────────────────┬──────────────────────────────────────┘
                                    │
                     §3 dma-router（A3：多插一层 xlate）
                                    │
                                    ▼
        ┌──────────── 控制器驱动 ────────────┐
        │  §1 virt-dma 可选层（43 个 .c 用） │  ← §6 dmatest 从消费者一侧压测
        └────────────────────────────────────┘
```

---

## §1 virt-dma：虚拟通道层

`drivers/dma/virt-dma.c`（142 行）+ `drivers/dma/virt-dma.h`（227 行）。
两个文件加起来不到 400 行，却是 `drivers/dma/` 里复用率最高的代码：
全树 51 个文件 `include` 了 `virt-dma.h`，其中 `drivers/dma/` 下的 `.c` 有 43 个。

Kconfig 开关：`DMA_VIRTUAL_CHANNELS`（`drivers/dma/Kconfig:47`），
由用它的控制器 `select`，用户不直接选。

### §1.1 它解决的到底是什么问题

硬件通道数是固定的（比如 8 个），但消费者数量不固定（十几个外设）。
两种做法：

| 做法 | 谁在做 | 后果 |
|---|---|---|
| 一个硬件通道 = 一个 `dma_chan` | `pl330` 等 | 通道用完就 `-EBUSY`；每个驱动自己写描述符队列 |
| 一个**虚拟**通道 = 一个 `dma_chan`，硬件通道按需绑定 | virt-dma 用户 | 消费者永远能拿到通道；排队逻辑由公共层提供 |

但要说清楚：**virt-dma 自己不做"虚拟通道到硬件通道的调度"**。
它只提供**五条描述符链表 + 一个 tasklet + 状态迁移的搬运函数**。
"什么时候把哪个虚拟通道的描述符压给哪个硬件通道"仍然是控制器驱动自己的事
（典型实现是在 `device_issue_pending` 里挑一个空闲硬件通道）。
把它当成"队列库"而不是"调度器"来理解才不会读错。

### §1.2 挂载点：驱动要做的四件事

| # | 动作 | 代码 | 时机（对应 03 时间线） |
|---|---|---|---|
| 1 | 结构体内嵌 `struct virt_dma_chan` 而不是 `struct dma_chan` | `drivers/dma/virt-dma.h:22-37` | 定义期 |
| 2 | 每通道调一次 `vchan_init(vc, dmadev)`，同时给 `vc->desc_free` 赋值 | `virt-dma.c:123-138` | **A2 注册之前**（`dma_async_device_register` 会遍历 `channels` 链表，`vchan_init` 里的 `list_add_tail` 就是往那条链表挂） |
| 3 | 每个 `device_prep_*` 里用 `vchan_tx_prep` 包一层 | `virt-dma.h:56-74` | A4 ① |
| 4 | 实现 `device_free_chan_resources` 调 `vchan_free_chan_resources`、`device_synchronize` 调 `vchan_synchronize` | `virt-dma.h:187-200`、`:211-225` | A6 / A5 |

第 2 步里 `vchan_init`（`virt-dma.c:123-138`）做了四件容易漏的事：
`dma_cookie_init(&vc->chan)`（`:125`）、`spin_lock_init(&vc->lock)`（`:127`）、
五条链表 `INIT_LIST_HEAD`（`:128-132`）、`tasklet_setup(&vc->task, vchan_complete)`（`:134`）。
最后两行才是**注册的实质**：`vc->chan.device = dmadev`（`:136`）+
`list_add_tail(&vc->chan.device_node, &dmadev->channels)`（`:137`）——
也就是说 **`vchan_init` 顺手把通道挂进了 `dma_device.channels` 链表**，
用 virt-dma 的驱动因此不需要自己 `list_add`。
**忘了调它 = cookie 从 0 开始 + 锁未初始化 + tasklet 是野指针 + 通道压根没进链表**，
而这些都不会在注册时报错，只会在第一次传输时炸。

### §1.3 用与不用的实际差异

`03-internals.md` §B3 已经给了对照表，这里只补**代码量的量级**：

| | 用 virt-dma | 不用 |
|---|---|---|
| 代表驱动 | `drivers/dma/mediatek/mtk-uart-apdma.c`（656 行） | `drivers/dma/pl330.c` |
| 描述符队列 | 白拿五条链表 | 自己定义、自己加锁 |
| 回调派发上下文 | 白拿 tasklet（`virt-dma.c:83-110`） | 自己 `tasklet_init` + 自己调 `dmaengine_desc_get_callback_invoke`（`drivers/dma/dmaengine.h:159`） |
| `device_synchronize` | 一行 `vchan_synchronize` | 自己 `tasklet_kill` + 自己清队列 |

一个**不对称**值得记住：virt-dma 提供了 `vchan_synchronize`，
所以用它的驱动几乎都实现了 `device_synchronize`；
而不用它的驱动**很容易漏掉这个可选回调** ——
漏掉的后果是 `dmaengine_synchronize()` 变成静默空操作
（`03-internals.md` §A5.2 第一条）。**这是"用不用 virt-dma"里最有安全含义的一条差异。**

### §1.4 三个只在 virt-dma 里存在的坑

| # | 坑 | 代码 |
|---|---|---|
| | `vchan_get_all_descriptors` 把 `desc_completed` 也 splice 走 → terminate 时"已完成待回调"的描述符回调永不执行 | `virt-dma.h:177-185`（`desc_completed` 在 `:183`） |
| | `vchan_find_desc` **只遍历 `desc_issued`** → `device_tx_status` 查不到已进 `desc_completed` 的描述符 | `virt-dma.c:66-76`（循环在 `:71-73`） |
| | `vchan_complete` 在 `vc->cyclic == NULL` 时对 NULL 做指针算术 | `virt-dma.c:92`/`:101`；详见 `03-internals.md` §A4.3 |

---

## §2 async_tx：把 XOR/PQ 计算卸载给 DMA 引擎

这是 dmaengine 里**最容易被误认为已经死掉**的一层 —— 它没死，
但它的用户只有一个方向：软 RAID。

### §2.1 它是什么

`crypto/async_tx/`（6 个文件，2105 行）+ `include/linux/async_tx.h`（208 行）。
提供的不是"搬数据"，而是**"算校验"的异步语义**：

| API | 干什么 | 文件 |
|---|---|---|
| `async_memcpy` | 异步 memcpy | `crypto/async_tx/async_memcpy.c`（97 行） |
| `async_xor` / `async_xor_offs` / `async_xor_val` | RAID5 的 XOR 与校验 | `async_xor.c`（420 行） |
| `async_gen_syndrome` / `async_syndrome_val` | RAID6 的 P+Q 生成与校验 | `async_pq.c`（463 行） |
| `async_raid6_2data_recov` / `async_raid6_datap_recov` | RAID6 双盘/数据+P 恢复 | `async_raid6_recov.c`（594 行） |
| `async_trigger_callback` | 只挂依赖不做运算 | `async_tx.c:220-256` |
| `async_tx_quiesce` | 等一个 tx 彻底完成并可释放 | `async_tx.c:263-276` |

每个 `async_*` 都有**同步回退路径**：拿不到 DMA 通道时就在 CPU 上算
（`async_tx_sync_epilog`，`include/linux/async_tx.h:132-137`）。
所以 md-raid 的代码里**不需要判断"有没有 DMA 引擎"** —— 这是这一层最大的价值。

Kconfig 全是 `tristate` 无提示符的内部符号（`crypto/async_tx/Kconfig:2-22`）：
`ASYNC_CORE` / `ASYNC_MEMCPY` / `ASYNC_XOR` / `ASYNC_PQ` / `ASYNC_RAID6_RECOV`，
由 `md/raid456` 一侧 `select`，用户在菜单里看不到它们。

### §2.2 唯一的消费者：md-raid

全树 include `async_tx.h` 的文件只有 11 个，其中真正的消费者三个：

| 消费者 | 用量 | 文件 |
|---|---|---|
| RAID4/5/6 主逻辑 | `async_*` 出现 84 处 | `drivers/md/raid5.c` |
| RAID5 日志（PPL） | 少量 | `drivers/md/raid5-ppl.c` |
| dm-integrity | 少量 | `drivers/md/dm-integrity.c` |

其余是 `crypto/async_tx/` 自己的 6 个文件 + 头文件 + `drivers/dma/ppc4xx/adma.c`（ppc，本篇不涉及）。

结论：**如果你不跑软 RAID，这一层在你的系统上完全不激活。**

### §2.3 它怎么找通道：percpu 通道表

async_tx **不用** `dma_request_chan`。它走的是 `03-internals.md` §B4 那套
**percpu 通道表**（"general-purpose allocator"）：

```
async_xor(...)
  └ async_tx_find_channel(submit, DMA_XOR, ...)     include/linux/async_tx.h:100-101（宏）
      └ __async_tx_find_channel(submit, tx_type)    crypto/async_tx/async_tx.c:42-53
          ├ 如果有 depend_tx 且它所在通道支持这个 cap → 复用同一个通道  :48-50
          │    这是"尽量把依赖链留在一个通道上"的优化
          └ 否则 async_dma_find_channel(tx_type)     include/linux/dmaengine.h:1346
                 └ dma_find_channel(DMA_ASYNC_TX)    drivers/dma/dmaengine.c:545-548
                      └ this_cpu_read(channel_table[tx_type]->chan)
```

注意 `include/linux/dmaengine.h:1346` 那个宏：
```c
#define async_dma_find_channel(type) dma_find_channel(DMA_ASYNC_TX)
```
**它把调用者传进来的 `type` 直接丢掉，永远查 `DMA_ASYNC_TX` 那一格。**
`:1348` 是另一个分支（`ASYNC_TX_ENABLE_CHANNEL_SWITCH=y` 时才按 `type` 查）。
所以在不开 channel-switch 的配置下，`async_xor` 和 `async_gen_syndrome`
**拿到的是同一个通道** —— 这正是 `DMA_ASYNC_TX` 这个"合成能力位"存在的理由：
核心在注册时用 `device_has_all_tx_types()` 判断一个控制器是否**同时**具备
memcpy/xor/pq 等全部类型，是的话才 `dma_cap_set(DMA_ASYNC_TX, ...)`
（`drivers/dma/dmaengine.c:1237-1238`）。

`DMA_ASYNC_TX` 的 kerneldoc 明确写了 **驱动不许自己设这一位**
（`include/linux/dmaengine.h:48-49` 的 `Note:`）—— 它是核心的推导结果，不是声明。

### §2.4 通道表要有人"开灯"：`dmaengine_get`

percpu 通道表默认是空的。填表的触发点是 `dmaengine_get()`
（`dmaengine.c:932-965`），它把 `dmaengine_ref_count` 加一，
第一次调用时触发 `dma_channel_rebalance()`（`:962-963`）。

所以链条是：**md-raid 模块加载 → `dmaengine_get()` → 通道表才有内容 →
`async_*` 才能真的用上硬件**。没有人调 `dmaengine_get()` 时，
`dma_find_channel()` 一律返回 NULL，所有 `async_*` 静默走 CPU 同步路径。
这也解释了 `03-internals.md` §B4.2 那条"slave 通道永远进不了这张表"：
`dma_channel_table_init` 在 `:273-275` 把 `DMA_PRIVATE` / `DMA_SLAVE` / `DMA_CYCLIC`
三位 `clear_bit` 掉了。

### §2.5 unmap pool：async_tx 的专属内存池

`async_*` 每次操作要 map 一堆 page（RAID6 可能上百个），
核心为此专门做了一个 mempool 家族：

| 元素 | 代码 | 说明 |
|---|---|---|
| 池描述 | `dmaengine.c:1364-1369` | `struct dmaengine_unmap_pool`：size + name + mempool + kmem_cache |
| 池数组 | `:1371-1379` | 只有 `__UNMAP_POOL(2)` 是无条件的；16/128/256 三档**包在 `#if IS_ENABLED(CONFIG_DMA_ENGINE_RAID)` 里**（`:1374-1378`） |
| 选池 | `:1381-1400` | 按 `nr` 选最小够用的池；**`nr` 超过最大池时走 `BUG();`**（`:1397`） |
| 申请 | `dmaengine_get_unmap_data`（`:1476-1491`） | `mempool_alloc`（`:1480`）+ `kref_init`（`:1485`）+ `map_cnt = nr`（`:1487`） |
| 释放 | `dmaengine_unmap_put`（`:1427-1432`）→ `dmaengine_unmap`（`:1402`） | kref 归零时批量 `dma_unmap_page` 再还池 |

两条实用结论：
1. **不开 `CONFIG_DMA_ENGINE_RAID` 时只有 size=2 一个池**，
   任何 `dmaengine_get_unmap_data(dev, nr>2, ...)` 直接 `BUG()`。
   `DMA_ENGINE_RAID` 是个无提示符的 `bool`（`drivers/dma/Kconfig:806-807`），
   由 RAID 引擎驱动和 `DMATEST` 各自 `select`。
2. 这套池**不是 async_tx 专用的**：`drivers/dma/dmatest.c:735` 也在用它。
   所以 §6 的 dmatest 会 `select DMA_ENGINE_RAID`（`drivers/dma/Kconfig:801`）。

### §2.6 一个死配置：`CONFIG_ASYNC_TX_CHANNEL_SWITCH`

```c
/* include/linux/async_tx.h:77 */
#if defined(CONFIG_DMA_ENGINE) && !defined(CONFIG_ASYNC_TX_CHANNEL_SWITCH)
```

**全树没有名为 `ASYNC_TX_CHANNEL_SWITCH` 的 Kconfig 符号。**
真实存在的那个叫 `ASYNC_TX_ENABLE_CHANNEL_SWITCH`（`drivers/dma/Kconfig:38`，
在 `dmaengine.c:1497` 和 `mv_xor_v2` 等 8 处 Kconfig 里被 `select`）。
两个名字差了 `ENABLE_`。

后果：`async_tx.h:77` 的条件里 `!defined(...)` **恒为真**，
于是只要 `CONFIG_DMA_ENGINE=y`，就永远走上半个分支；
`:106-125` 那组"空实现 + `async_tx_find_channel` 返回 NULL"的代码
**永远编不进来**。反过来说：**这不是功能 bug**（想要的行为恰好就是上半支），
但那半个 `#else` 分支是彻底的死代码，而且名字看起来像开关、实际不是。
登记在 `99-evidence.md` §6。

### §2.7 arm64 上真会用到 async_tx 的三款引擎

| 驱动 | Kconfig | arm64 依赖 | 在树里的 DTS |
|---|---|---|---|
| `drivers/dma/mv_xor_v2.c` | `MV_XOR_V2`（`drivers/dma/Kconfig:461-467`） | `depends on ARM64`（`:463`） | Marvell Armada 7K/8K |
| `drivers/dma/bcm-sba-raid.c` | `BCM_SBA_RAID`（`:121-127`） | `depends on ARM64 \|\| COMPILE_TEST`（`:123`） | `arch/arm64/boot/dts/broadcom/stingray/stingray-fs4.dtsi` |
| `drivers/dma/xgene-dma.c` | `XGENE_DMA`（`:708-713`） | `depends on ARCH_XGENE`（arm64） | `arch/arm64/boot/dts/apm/apm-storm.dtsi` |

三者都 `select DMA_ENGINE_RAID`。所以 §2.5 那个 size=16/128/256 的池，
在 arm64 上确实会被编进来 —— 但只在这三种 SoC 的配置里。

---

## §3 dma-router / dmamux：请求线的选路器

### §3.1 它解决的问题

有些 SoC 的 DMA 请求线（外设侧）远多于控制器的通道数，
中间夹了一个可编程的多路选择器（mux / crossbar）。
消费者的 DT 里写的是"我要 mux 的第 37 号输入"，
但控制器只认"我的第 3 号通道" —— 中间那次翻译就是 router 干的。

### §3.2 挂载点：在 A3 的 xlate 前面插一层

**关键设计**：router **不是新增一个层次，而是把自己伪装成一个控制器**。
`of_dma_router_register`（`drivers/dma/of-dma.c:185-212`）把 `of_dma_xlate`
直接写成核心自带的 `of_dma_router_xlate`（`:202`）：

```c
	ofdma->of_node = np;                                    /* :201 */
	ofdma->of_dma_xlate = of_dma_router_xlate;              /* :202 固定 */
	ofdma->of_dma_route_allocate = of_dma_route_allocate;   /* :203 驱动的钩子 */
	ofdma->dma_router = dma_router;                         /* :204 */
```

所以 `03-internals.md` §A3 那条 `of_dma_request_slave_channel → ofdma->of_dma_xlate`
的调用**一个字都不用改**，router 对上层完全透明。真正的两级翻译在
`of_dma_router_xlate`（`of-dma.c:55-103`）里：

```
of_dma_router_xlate(dma_spec, ofdma)                     of-dma.c:55
  ├ memcpy(&dma_spec_target, dma_spec, ...)              :64  先复制一份
  ├ route_data = ofdma->of_dma_route_allocate(&dma_spec_target, ofdma)   :65
  │     ↑ 驱动在这里：占一个 mux 通道，并【原地改写 dma_spec_target】
  ├ if (IS_ERR(route_data)) return NULL;                 :66-67 错误码在这里被吃掉
  ├ ofdma_target = of_dma_find_controller(&dma_spec_target)  :69
  │     （找不到 → route_free + ERR_PTR(-EPROBE_DEFER)）  :71-74
  ├ chan = ofdma_target->of_dma_xlate(&dma_spec_target, ofdma_target)  :77
  │     ↑ 真控制器的 xlate，返回真通道
  ├ chan->router = ofdma->dma_router;                    :84
  ├ chan->route_data = route_data;                       :85
  └ if (chan->device->device_router_config) ret = ...(chan)  :87-88
        失败 → dma_release_channel(chan)                  :90-93
```

归还路径在 `dma_chan_put`（`drivers/dma/dmaengine.c:510-514`）：
通道最后一个使用者走掉时，核心调 `chan->router->route_free(chan->router->dev, chan->route_data)`。
所以 **router 的占用/释放是绑在通道引用计数上的**，驱动不用自己管。

### §3.3 数据结构小到反常

```c
/* include/linux/dmaengine.h:304-307 */
struct dma_router {
	struct device *dev;
	void (*route_free)(struct device *dev, void *route_data);
};
```

只有两个字段，**没有 `route_allocate`**。
分配钩子是作为 `of_dma_router_register` 的**参数**传进去、
存在 `struct of_dma` 里（`of-dma.c:203`）的。
所以这一对函数**住在两个不同的结构里** —— 读代码时找不到 allocate 是正常的。

配套字段：`struct dma_chan` 里的 `router` 和 `route_data`
（`include/linux/dmaengine.h:348-349`）；
可选回调 `device_router_config`（`:883`，kerneldoc 在 `:809`）。

### §3.4 全树普查：本树的四个 router，一个都不在 arm64 上

| 驱动 | 行数 | route_allocate 实现 | Kconfig | 平台 |
|---|---|---|---|---|
| `drivers/dma/stm32-dmamux.c` | 403 | `stm32_dmamux_route_allocate`（`:84`） | `STM32_DMAMUX`（`drivers/dma/Kconfig:593`） | STM32MP1（arm32） |
| `drivers/dma/ti/dma-crossbar.c` | 477 | **两个**：`ti_am335x_xbar_route_allocate`（`:76`）、`ti_dra7_xbar_route_allocate`（`:238`） | `TI_DMA_CROSSBAR`（`drivers/dma/ti/Kconfig:61`） | AM335x / DRA7（arm32） |
| `drivers/dma/lpc18xx-dmamux.c` | 179 | `lpc18xx_dmamux_reserve` | `LPC18XX_DMAMUX`（`drivers/dma/Kconfig:379`） | LPC18xx（arm32） |
| `drivers/dma/dw/rzn1-dmamux.c` | 165 | `rzn1_dmamux_route_allocate`（`:39`） | `RZN1_DMAMUX`（`drivers/dma/dw/Kconfig:19`） | RZ/N1（arm32） |

**全树带 `dma-router` 节点的 DTS 只有 6 个文件，且全部在 `arch/arm/boot/dts/`**：
`arch/arm/boot/dts/r9a06g032.dtsi`、`arch/arm/boot/dts/stm32mp131.dtsi`、
`arch/arm/boot/dts/dra7-l4.dtsi`、`arch/arm/boot/dts/am33xx-l4.dtsi`、
`arch/arm/boot/dts/am437x-l4.dtsi`、`arch/arm/boot/dts/dm814x.dtsi`
（复现命令见 `99-evidence.md` §3）。
`arch/arm64/boot/dts/` 下**零命中**（`fsl-ls10xx` 那几个文件里的 `dmamux`
是 `clock-names = "dmamux0"`，与本框架无关）。

所以在 arm64 上你**读不到这条路径**，但它值得知道 —— 因为它解释了
`03-internals.md` §B5 那条"为什么 `of_dma_find_controller` 会被调用两次"。

### §3.5 router 的一个真实缺陷

`of_dma_router_xlate:66-67` 把 `of_dma_route_allocate` 的任何错误都收敛成 `return NULL`：

```c
	if (IS_ERR(route_data))
		return NULL;      /* :67 —— 错误码丢了 */
```

而调用它的 `of_dma_request_slave_channel` 在 `chan == NULL` 时
返回 `ERR_PTR(-EPROBE_DEFER)`（`of-dma.c:294`/`:306`）。
**后果**：mux 通道用完（典型错误码 `-ENODEV` / `-EBUSY`）时，
消费者驱动看到的是 `-EPROBE_DEFER` —— 它会**无限期地等一个永远不会到来的控制器**，
而 dmesg 里只有 router 驱动自己 `dev_err` 的那一行是真相。
以 stm32 为例，它在 `:99-102` 对越界的 `dma_spec->args[0]` 打了 `dev_err` 才返回 `-EINVAL`。
登记在 `99-evidence.md` §6 和 `03-internals.md` §C1 第 ⑩ 条。

DT 绑定见 `Documentation/devicetree/bindings/dma/dma-router.yaml`（54 行），
写法在 `06-devicetree.md` 里展开。

---

## §4 描述符 metadata：给描述符挂带外数据

### §4.1 它是什么

有些 DMA 引擎（TI K3 UDMA 一族）在数据之外还要传一小块**协议元数据**
（packet descriptor 里的 PS-Words）。这块数据不属于 sg 表，
所以核心专门给了一套 attach/get/set 接口。

两种模式，**互斥**（`include/linux/dmaengine.h:282-286`）：

| 模式 | 谁分配缓冲区 | 用哪个 API |
|---|---|---|
| `DESC_METADATA_CLIENT`（`:284`） | **客户端** 分配，attach 给描述符 | `dmaengine_desc_attach_metadata()` |
| `DESC_METADATA_ENGINE`（`:285`） | **引擎** 分配，客户端问它要指针 | `dmaengine_desc_get_metadata_ptr()` + `dmaengine_desc_set_metadata_len()` |
| `DESC_METADATA_NONE`（`:283`） | 不支持 | — |

两种模式的完整使用序列（含 DEV_TO_MEM / MEM_TO_DEV 的不同步骤）
直接写在 kerneldoc 里：`include/linux/dmaengine.h:233-281`。
这是本子系统里少见的"文档比代码详细"的地方，**值得直接读原文**。

### §4.2 挂载点与三个转发函数

驱动侧要填两个字段：
- `dma_device.desc_metadata_modes`（`include/linux/dmaengine.h:859`）—— 声明支持哪些模式；
- `dma_async_tx_descriptor.metadata_ops`（`:614`）—— 三个函数指针
  （`struct dma_descriptor_metadata_ops`，`:569-577`：`attach` / `get_ptr` / `set_len`）。

核心侧三个导出函数都只是**带检查的转发**（`drivers/dma/dmaengine.c`）：

| API | 行号 | 检查顺序 |
|---|---|---|
| `dmaengine_desc_attach_metadata` | `:1519-1535`（EXPORT `:1536`） | `!desc` → `-EINVAL`；模式检查；`!metadata_ops->attach` → `-ENOTSUPP` |
| `dmaengine_desc_get_metadata_ptr` | `:1538-1554`（EXPORT `:1555`） | 同上，但错误用 `ERR_PTR()` 返回 |
| `dmaengine_desc_set_metadata_len` | `:1557-1573`（EXPORT `:1574`） | 同上 |

三者共用的模式检查是 `desc_check_and_set_metadata_mode`（`:1503-1517`），逻辑三行：

```c
	if (!desc->desc_metadata_mode) {                     /* :1507 首次使用 */
		if (dmaengine_is_metadata_mode_supported(desc->chan, mode))
			desc->desc_metadata_mode = mode;     /* :1509 锁定模式 */
		else
			return -ENOTSUPP;                    /* :1511 */
	} else if (desc->desc_metadata_mode != mode) {       /* :1512 已锁定 */
		return -EINVAL;                              /* :1513 换模式 = 拒绝 */
	}
```

这就是 kerneldoc 里那句"客户端必须二选一"的**执行点**：
**每个描述符的模式在第一次调用时被锁定，之后换模式一律 `-EINVAL`**。
注意它锁的是**描述符**而不是通道 —— 同一个通道上不同描述符可以用不同模式
（只要引擎的 `desc_metadata_modes` 两位都置了）。

`dmaengine_is_metadata_mode_supported`（`include/linux/dmaengine.h:1058-1064`）
是给消费者**提前问**的：`!!(chan->device->desc_metadata_modes & mode)`。

### §4.3 arm64 相关性与唯一实现

全树只有 `drivers/dma/ti/k3-udma.c` 实现了 `metadata_ops`
（除核心自己的 `dmaengine.c` 外零命中）。
arm64 上对应 TI K3（AM65x / J721e 等）。不做 K3 的话这一节可以跳过。

一个**语义提醒**：`dmaengine_desc_get_metadata_ptr` 返回的指针
在描述符生命周期的某些时点之后失效，kerneldoc 在
`include/linux/dmaengine.h:258-260` 用 `Note:` 说明了，但**代码里没有任何断言**。
这条属于"文档承诺、代码不查"的一类，登记在 `99-evidence.md` §6。

---

## §5 dma_slave_map：没有设备树时的映射表

### §5.1 挂载点：A3 的第 2 跳

`03-internals.md` §A3 里 `dma_request_chan` 的两条路：
DT 路径（`dmaengine.c:818-819`）失败之后，走的就是这条 filter-map 兜底
（`:833-848`）。它解决的是**板级代码（board file）时代的映射问题**：
消费者只知道自己叫什么、通道叫什么，不知道该给 filter 函数传什么参数。

### §5.2 三个结构 + 一次线性查表

```c
/* include/linux/dmaengine.h:755-759 */
struct dma_slave_map {
	const char *devname;   /* 消费者设备名，如 "spi0.0" */
	const char *slave;     /* 通道名，如 "rx" */
	void *param;           /* 传给 filter 函数的不透明参数 */
};

/* :768-772 */
struct dma_filter {
	dma_filter_fn fn;                  /* 控制器自己的 filter 回调 */
	int mapcnt;
	const struct dma_slave_map *map;   /* 上面那个数组 */
};
```

控制器在 `dma_device.filter`（`:857`）里挂这张表。
核心侧的查表是最朴素的双 `strcmp` 线性扫描
（`dma_filter_match`，`drivers/dma/dmaengine.c:786-804`）：

```c
	if (!device->filter.mapcnt)                          /* :792 没表直接跳过 */
		return NULL;
	for (i = 0; i < device->filter.mapcnt; i++) {         /* :795 */
		const struct dma_slave_map *map = &device->filter.map[i];
		if (!strcmp(map->devname, dev_name(dev)) &&      /* :798 */
		    !strcmp(map->slave, name))                   /* :799 */
			return map;
	}
```

命中之后 `dma_request_chan` 把 `map->param` 交给 `find_candidate`
（`dmaengine.c:844`）。整条查表 + 申请**全程持着 `dma_list_mutex`**（`:833` 取、`:848` 放），
这跟 DT 路径不一样（见 `03-internals.md` §B1.1 后果二）。

### §5.3 arm64 相关性：零

全树 `dma_slave_map` 的使用者：

| 类别 | 文件 |
|---|---|
| 控制器驱动（3 个） | `drivers/dma/pl330.c`、`drivers/dma/sa11x0-dma.c`、`drivers/dma/pxa_dma.c` |
| 板级代码（11 个 `.c`） | `arch/arm/mach-s3c/`、`arch/arm/mach-pxa/`、`arch/arm/mach-omap1|2/`、`arch/arm/mach-davinci/`、`arch/m68k/coldfire/device.c` |

`arch/arm64/` 下 **0 个文件**用到它（`grep -rl dma_slave_map --include=*.c arch/arm64/ | wc -l` = 0）。
arm64 平台一律走 DT，所以这条兜底路径在 arm64 上是**永远走不到的死路**。
它值得知道的唯一理由是：**它解释了 `dma_request_chan` 里那段看起来多余的加锁代码。**

---

## §6 dmatest：站在消费者一侧的压测客户端

`drivers/dma/dmatest.c`（1360 行）。它不是核心的一部分，
而是**一个纯粹的消费者模块** —— 所以读它等于读一份"官方示范的消费者写法"。

Kconfig：`DMATEST`（`drivers/dma/Kconfig:798-804`），
`tristate`，`depends on DMA_ENGINE`，`select DMA_ENGINE_RAID`（`:801`）——
因为它要用 §2.5 那些大号 unmap 池。

### §6.1 它怎么被驱动

**没有 debugfs，全部走 module_param**（`/sys/module/dmatest/parameters/`）。
两类参数：

| 类 | 参数 | 特点 |
|---|---|---|
| 普通 `module_param`（`:25-89`） | `test_buf_size` `device` `threads_per_chan` `max_channels` `iterations` `dmatest` `xor_sources` `pq_sources` `timeout` `noverify` `norandom` `verbose` `alignment` `transfer_size` `polled` | 写了就生效于**下次** run |
| `module_param_cb`（有 setter/getter） | `run`（`:156`）、`channel`（`:171`）、`test_list`（`:178`，0444 只读）、`wait`（`:292`，0444 只读） | **写入即触发动作** |

`run` 是**动作型参数**，不是状态型：`dmatest_run_set`（`:1179-1215`）
在写入 `1` 时会 `add_threaded_test` + `start_threaded_tests`，
写入 `0` 时 `stop_threaded_test`。
所以 `echo 1 > .../parameters/run` 是"开始跑"，
而不是"把一个标志置 1"。`07-debug.md` 会给完整的操作序列。

### §6.2 它示范的消费者流程

| 步骤 | 代码 | 对应 03 时间线 |
|---|---|---|
| 申请通道（用 mask+filter，**不是** `dma_request_chan`） | `dma_request_channel(mask, filter, params)`（`:1077`） | A3（走 `__dma_request_channel` 而非 DT） |
| 每通道起 N 个线程 | `dmatest_add_channel`（`:1006`）→ `dmatest_add_threads`（`:955`） | — |
| 拿真实 DMA device 做 map | `dmaengine_get_dma_device(chan)`（`:609`） | 不是 `chan->device->dev`，见 `01-api.md` |
| 申请 unmap 数据 | `dmaengine_get_unmap_data(dma_dev, src->cnt + dst->cnt, ...)`（`:735`） | §2.5 的池 |
| prep → submit → issue → 等回调/轮询 | `dmatest_func`（`:567` 起） | A4 全程 |
| 释放 | `dma_release_channel(chan)`（`:1080`） | A6 |

两个容易漏的细节：
1. **入口是 `late_initcall(dmatest_init)`（`:1347`），不是 `module_init`** ——
   注释写明"when compiled-in wait for drivers to load first"（`:1346`）。
   编进内核时它必须等控制器驱动先注册完。
2. 它用的是**老式** `dma_request_channel(mask, filter, param)`，
   走 percpu 表之外的 `__dma_request_channel` 私有申请路径。
   **不要照抄这一句到你的外设驱动里** —— 外设驱动应该用
   `dma_request_chan(dev, name)`（`01-api.md` §2 已标注）。
   dmatest 用老 API 是因为它要"任意一个能 memcpy 的通道"，没有 DT 节点可依。

### §6.3 为什么调新驱动要先跑它

它覆盖了控制器驱动最容易写错的四件事：
- `device_prep_dma_memcpy` 的地址/长度对齐处理（`alignment` 参数）；
- 回调是否真的被调用（`timeout` 参数 + `wait`）；
- 数据是否真的搬对（`noverify=0` 时逐字节比对）；
- `device_tx_status` 与轮询模式是否一致（`polled` 参数）。

但它**测不到 slave 传输**（`dma_request_channel` 拿不到 `DMA_SLAVE` 通道 ——
`03-internals.md` §B4.2 那三个 `clear_bit`）。
所以**外设方向的 DMA 只能靠真实外设驱动来测**，dmatest 帮不上。

---

## §7 汇总表

| 框架 | 代码位置 | 行数量级 | 职责 | 挂在 03 时间线 | 谁必须动手 | arm64 |
|---|---|---|---|---|---|---|
| **virt-dma** | `drivers/dma/virt-dma.{c,h}` | 142 + 227 | 描述符队列 + tasklet 回调派发 | A2 前（`vchan_init`）、A4 全程、A5/A6 | 控制器驱动作者 | |
| **async_tx** | `crypto/async_tx/` + `include/linux/async_tx.h` | 2105 + 208 | XOR/PQ 的异步语义 + CPU 同步回退 | A1 percpu 表 + A4 | md-raid（消费者侧，你一般不动） | （三款 RAID 引擎） |
| **unmap pool** | `drivers/dma/dmaengine.c:1364-1491` | ~130 | 批量 page map/unmap 的 mempool | A4 前后 | async_tx 与 dmatest 的调用者 | 随 async_tx |
| **dma-router** | `drivers/dma/of-dma.c:55-103`/`:185-212` + 4 个 mux 驱动 | 49 + 28 + 1224 | 请求线 → 通道的两级翻译 | A3（xlate 前插一层） | mux 硬件的驱动作者 | ✗ 本树无 arm64 用户 |
| **metadata** | `dmaengine.c:1503-1574` + `include/linux/dmaengine.h:233-286`/`:569-577` | ~72 | 描述符带外数据，两模式互斥 | A4 prep 后 / submit 前 | K3 UDMA 与其消费者 | （仅 TI K3） |
| **dma_slave_map** | `include/linux/dmaengine.h:755-772` + `dmaengine.c:786-804` | ~37 | 无 DT 平台的 devname/slave → filter 参数映射 | A3 第 2 跳 | arm32/m68k 板级代码 | ✗ 零使用者 |
| **dmatest** | `drivers/dma/dmatest.c` | 1360 | 消费者侧压测客户端 | 走完 A3→A4→A6 | 调驱动的人 | |

### §7.1 三条跨框架的总结

1. **只有 virt-dma 和 dmatest 是"你多半会碰"的**。
   其余四个都是条件触发：async_tx 要软 RAID，router 要 mux 硬件，
   metadata 要 K3，dma_slave_map 要非 DT 平台。

2. **两套"找通道"的机制从头到尾不相交**：
   async_tx 走 percpu 通道表（`dma_find_channel`），
   外设驱动走 `dma_request_chan`。
   分界线是 `dma_channel_table_init` 里那三个 `clear_bit`
   （`dmaengine.c:273-275`）—— `DMA_SLAVE` 永远不进 percpu 表。
   这条是理解"为什么 dmatest 测不了 slave"和
   "为什么 md-raid 不用写 DT"的共同答案。

3. **四个框架都把错误码吃掉过至少一次**：router 的 `return NULL`（§3.5）、
   metadata 的"文档承诺代码不查"（§4.3）、
   unmap pool 的 `BUG()` 而非返回错误（§2.5）、
   async_tx 的 `panic()`（`crypto/async_tx/async_tx.c:271-272`）。
   排查这一层的问题时，**不要相信返回值，要去看 dmesg 里驱动自己打的那行**。

### §7.2 去哪继续

| 你想知道 | 去 |
|---|---|
| 核心的注册/申请/传输主流程 | `03-internals.md` |
| 这些框架各自的 API 签名 | `01-api.md` |
| 写一个 virt-dma 控制器驱动的骨架 | `05-usage.md` |
| router / mux 的 DTS 怎么写 | `06-devicetree.md` |
| dmatest 的完整操作序列、debugfs、日志级别 | `07-debug.md` |
| "虚拟通道"到底是什么意思（大白话） | `08-feynman.md` |
