---
title: 数据结构
---

# 02 核心数据结构：dmaengine

> 本篇讲**结构体本身和它们之间的关系**，不讲流程（流程在 `03-internals.md`）。
> 开篇 §1.0 先把五组最容易混淆的关系否掉，再画图 —— 图补不上一个没被显式否掉的错误类比。
> 只讲 arm64。

---

## §1.0 先分清这五组关系

### 组 1：`dma_device` / `dma_chan` / `dma_chan_dev` —— 哪个才是 `struct device`？

> ❌ **错误类比**：「`struct dma_chan` 就是一个 device，注册到 dma 总线上」。

`struct dma_chan` **不内嵌 `struct device`**，它只有一个 `struct dma_chan_dev *dev`
指针（`include/linux/dmaengine.h:336`）；真正的 `struct device` 内嵌在
`struct dma_chan_dev` 里面（`:364`）。三者摊开：

| | `struct dma_device` | `struct dma_chan` | `struct dma_chan_dev` |
|---|---|---|---|
| 代表什么 | 一个 **DMA 控制器**（整块 IP） | 控制器上的**一条通道** | 通道在 sysfs 里的**门面** |
| 定义在 | `include/linux/dmaengine.h:851` | `:328` | `:362` |
| 内嵌 `struct device`？ | ❌ 只有 `struct device *dev` 指针（指向控制器的 platform device） | ❌ 只有 `dev` 指针 | **✅ 内嵌 `struct device device`** |
| 谁分配 | 控制器驱动（多数内嵌在自己的私有结构里） | 控制器驱动 | **核心** `kzalloc` |
| sysfs 里叫什么 | 沿用它的 platform device 名 | — | `dma<N>chan<M>`（`dev_set_name` 在 `drivers/dma/dmaengine.c:1069`） |
| 绑 driver 吗 | 它的 platform device 绑 platform_driver | ❌ | **❌ 只有 `.class`，没有 `.bus`** |

**"为什么通道不会被匹配 driver"有代码依据**：
`__dma_async_device_channel_register()` 给通道的 device 只赋了三样东西 ——
`.class = &dma_devclass`、`.parent = device->dev`、名字（`drivers/dma/dmaengine.c:1065-1069`），
**从头到尾没有给 `dev->bus` 赋值**。`device_register()` 对 `bus == NULL` 的设备
不走任何匹配流程，所以通道永远不会有 driver。

由此得到本篇第一条要背的结论：

> **dmaengine 有 sysfs class（`/sys/class/dma/`），但没有 `bus_type`。**
> 这和 clk（既没有 class 也没有 bus）、和 i2c/spi（有 bus_type）都不一样，
> 详见 §5。

### 组 2：`dma_slave_config` vs `dma_slave_caps` —— 方向完全相反

> ❌ **错误类比**：「两个都是描述通道参数的结构体，差不多」。

它们的**数据流向是反的**：

| | `struct dma_slave_config` | `struct dma_slave_caps` |
|---|---|---|
| 定义在 | `include/linux/dmaengine.h:437` | `:501` |
| 谁填 | **消费者驱动填** | **核心填**（`dma_get_slave_caps`） |
| 谁读 | 控制器驱动的 `device_config` 回调 | 消费者驱动 |
| 语义 | 「我**要**这样传」 | 「你**只能**这样传」 |
| 典型字段 | `src_addr`、`dst_addr`、`src_addr_width`、`dst_maxburst` | `src_addr_widths`（**复数**，是位掩码）、`directions`、`max_burst`、`residue_granularity` |
| 入口 API | `dmaengine_slave_config()` | `dma_get_slave_caps()` |

记住这个区分的**实用价值**：单数字段（`src_addr_width`）是"我选的这一个值"，
复数字段（`src_addr_widths`）是"可选值的集合"。写驱动时**先 `dma_get_slave_caps`
看集合，再往 `dma_slave_config` 里填一个集合内的值** —— 反过来（先填再查）
在多平台驱动里必踩。

还有一个**已被官方标废弃**的字段：`dma_slave_config.direction`
（`include/linux/dmaengine.h:389-392` 的 kerneldoc 逐字写着 DEPRECATED）。
方向应该在 `dmaengine_prep_*` 时传参，不要写进 config。

### 组 3：`dma_async_tx_descriptor` / `virt_dma_desc` / 驱动私有描述符 —— 三层套娃

> ❌ **错误类比**：「描述符就是硬件描述符，DMA 引擎直接读它」。

**不是。** `struct dma_async_tx_descriptor` 是**纯软件**结构，硬件从不读它。
用 virt-dma 的驱动是标准的三层内嵌：

```
   驱动私有描述符（如 struct mtk_uart_apdma_desc）
        └── struct virt_dma_desc vd;              ← virt-dma 层
                └── struct dma_async_tx_descriptor tx;   ← 核心层，消费者能看见的就是这一层
```

| | `dma_async_tx_descriptor` | `virt_dma_desc` | 驱动私有 |
|---|---|---|---|
| 定义在 | `include/linux/dmaengine.h:602` | `drivers/dma/virt-dma.h:15` | 各驱动自己 |
| 谁看得见 | **消费者**（prep 的返回值） | 只有控制器驱动 | 只有控制器驱动 |
| 关键字段 | `cookie`、`flags`、`chan`、`tx_submit`、`callback`/`callback_result`、`unmap` | `tx`、`tx_result`、`node`（挂链表用） | 真正的硬件描述符地址、长度、方向 |
| 谁负责推进 | 无（是被动数据） | virt-dma 的五条链表 + tasklet | 驱动的中断处理 |

三层之间靠 `container_of` 上溯：`to_virt_desc(tx)` 从 `tx` 拿到 `vd`
（`drivers/dma/virt-dma.c:14-17`），驱动再从 `vd` 拿到自己的结构。
和 clk 的 `clk_hw` → 私有结构是同一个套路。

**不用 virt-dma 的驱动就只有两层**：`drivers/dma/pl330.c` 自己维护描述符池，
它的私有描述符直接内嵌 `dma_async_tx_descriptor`。所以"三层"不是普遍事实，
是"用了 virt-dma 之后"的事实。

### 组 4：`dma_cookie_t` vs 描述符指针 —— cookie 不是句柄

> ❌ **错误类比**：「cookie 是描述符的 ID，可以用它把描述符取回来」。

`typedef s32 dma_cookie_t`（`include/linux/dmaengine.h:22`）—— 它就是个**递增的序号**，
`DMA_MIN_COOKIE` 为 1（`:23`），到 `INT_MAX` 后回绕
（`dma_cookie_assign()`，`drivers/dma/dmaengine.h:29`）。

| | 描述符指针 | cookie |
|---|---|---|
| 类型 | `struct dma_async_tx_descriptor *` | `s32` |
| `dmaengine_submit()` **之后**还能用吗 | **不能** —— 所有权已交给引擎 | **能**，而且这是**唯一**还能用的东西 |
| 能做什么 | submit 之前：设 callback、设 flags | 查状态（`dmaengine_tx_status`）、判错（`dma_submit_error`） |
| 能反查描述符吗 | — | **不能**。核心没有 cookie → 描述符的映射表 |

**这一组是消费者侧最致命的一组**。`Documentation/driver-api/dmaengine/client.rst`
明确规定：`dmaengine_submit()` 返回后描述符归引擎所有，消费者**再碰就是 UAF**。
所以正确的写法是**只保留 cookie，立刻丢弃描述符指针**。

virt-dma 里确实有个 `vchan_find_desc(vc, cookie)` 能按 cookie 找描述符
（`drivers/dma/virt-dma.c:66-77`），但它 (a) 是**控制器驱动私有**的，消费者用不到；
(b) **只搜 `desc_issued` 一条链表** —— 已经完成或已终止的查不到。

### 组 5：能力位（`dma_cap_mask_t`）vs 回调指针 —— 前者是声明，后者是实现

> ❌ **错误类比**：「设置了能力位就等于实现了功能，核心会校验」。

**只校验一半。** `dma_cap_mask_t` 是 `DECLARE_BITMAP(bits, DMA_TX_TYPE_END)`
（`include/linux/dmaengine.h:230`），共 16 个有效位。
`dma_async_device_register()` 里有九个 `if (dma_has_cap(...) && !device->device_prep_*)`
形式的检查（`drivers/dma/dmaengine.c:1153-1214`），覆盖 MEMCPY / XOR / XOR_VAL /
PQ / PQ_VAL / MEMSET / INTERRUPT / CYCLIC / INTERLEAVE。

**剩下的 5 个位没有门禁**：`DMA_MEMSET_SG`、`DMA_SLAVE`、`DMA_REPEAT`、
`DMA_LOAD_EOT`、`DMA_COMPLETION_NO_ORDER` 声明了却不实现，注册照样返回 0。
对 arm64 特别刺眼 —— 从设备驱动天天用的 `DMA_SLAVE` 恰恰是没门禁的那个。
（登记在 `99-evidence.md` §6）

反过来还有一个位是**核心自己置的**，驱动不许碰：`DMA_ASYNC_TX`。
头文件 `:48-49` 逐字写着「The DMA_ASYNC_TX capability is not to be set by drivers.
It is automatically set as dma devices are registered.」判据函数是
`device_has_all_tx_types()`（`drivers/dma/dmaengine.c:990`）。

---

## §2 结构关系总图

```
 ┌──────────────────────────────── 全局（drivers/dma/dmaengine.c）─────────────────────────────┐
 │  dma_device_list  (LIST_HEAD, :60)          channel_table[DMA_TX_TYPE_END] (percpu, :260)   │
 │        │ 保护：dma_list_mutex (:58)               │ 只装【公共】通道，给 dma_find_channel 用  │
 │        │                                          ↓                                         │
 │        │                                   ┌─ dma_chan_tbl_ent { chan } ─┐  每 CPU 一份      │
 └────────┼──────────────────────────────────────────────────────────────────────────────────┘
          │ global_node
          ↓
 ┌────────────────────── struct dma_device（一个控制器，:851）──────────────────────┐
 │  kref ref                     ← 生命周期，dma_device_put 归零后调 device_release  │
 │  struct device *dev           ← 指向控制器的 platform device（缝合点）          │
 │  struct module *owner         ← 注册时从 dev->driver->owner 抄来                  │
 │  dma_cap_mask_t cap_mask      ← 16 位能力声明（组 5）                             │
 │  struct list_head channels ───┼──┐                                                │
 │  u32 chancnt / privatecnt     │  │                                                │
 │  struct ida chan_ida          │  │ device_node                                    │
 │  ── 从设备能力（给 dma_get_slave_caps 抄）──                                       │
 │  src_addr_widths / dst_addr_widths / directions / min_burst / max_burst           │
 │  max_sg_burst / descriptor_reuse / residue_granularity                            │
 │  ── 回调表（约 20 个 device_* 函数指针，见 §4）──                                  │
 │  device_alloc_chan_resources / device_prep_* / device_config / device_pause /      │
 │  device_resume / device_terminate_all / device_synchronize /                       │
 │  device_tx_status(必填) / device_issue_pending(必填) / device_caps /               │
 │  device_router_config / device_release / dbg_summary_show                          │
 └───────────────────────────────┼──┼───────────────────────────────────────────────┘
                                 │  │
                                 │  ↓ 每条通道一个
        ┌────────────────────────┴──────────────────────────────────────────┐
        │           struct dma_chan（一条通道，:328）                        │
        │  struct dma_device *device   ← 回指控制器                          │
        │  struct device *slave        ← dma_request_chan 成功后指向消费者  │
        │  dma_cookie_t cookie / completed_cookie   ← 一对递增序号（§3.4）    │
        │  int chan_id                 ← 来自 device->chan_ida               │
        │  struct dma_chan_dev *dev ───┼─→ ┌ struct dma_chan_dev (:362) ─┐   │
        │  const char *name            │   │  struct dma_chan *chan      │   │
        │  char *dbg_client_name       │   │  struct device device  内嵌 │   │
        │       （CONFIG_DEBUG_FS）    │   │  int dev_id                 │   │
        │  struct list_head device_node │  │  bool chan_dma_dev          │   │
        │  struct dma_chan_percpu __percpu *local                        │   │
        │  int client_count            │   └─────────────────────────────┘   │
        │  int table_count             │        ↑ /sys/class/dma/dma0chan1    │
        │  struct dma_router *router ──┼─→ ┌ struct dma_router (:304) ─┐     │
        │  void *route_data            │   │  struct device *dev       │     │
        │  void *private               │   │  void (*route_free)()     │     │
        └──────────────────────────────┘   └───────────────────────────┘     │
                     │                                                        │
                     │ 用 virt-dma 的驱动会把 dma_chan 内嵌进 virt_dma_chan   │
                     ↓                                                        │
        ┌── struct virt_dma_chan（drivers/dma/virt-dma.h:22）──────────────────┘
        │  struct dma_chan chan;        内嵌，不是指针
        │  struct tasklet_struct task;  ← 完成回调都从这里发出
        │  spinlock_t lock;             ← 保护下面五条链表
        │  desc_allocated / desc_submitted / desc_issued /
        │  desc_completed / desc_terminated              ← 五态队列（§3.5）
        │  struct virt_dma_desc *cyclic;
        └──────────────────────────────────────────────────────────────────────
```

**一张图看懂三个"count"**（这三个字段名字像但含义完全不同）：

| 字段 | 在哪个结构 | 含义 | 谁改 |
|---|---|---|---|
| `chancnt` | `dma_device` | 这个控制器一共注册了几条通道 | 核心，通道注册时 `++` |
| `privatecnt` | `dma_device` | 有几条通道被 private 方式占用 | 核心，`find_candidate` / `dma_release_channel` |
| `client_count` | `dma_chan` | **这一条通道**被几个客户端持有 | 核心，`dma_chan_get` / `dma_chan_put` |
| `table_count` | `dma_chan` | 这条通道被 `channel_table[]` 引用了几次 | 核心，`dma_channel_rebalance` |

`dma_release_channel()` 里那句 `WARN_ONCE(chan->client_count != 1,
"chan reference count %d != 1\n", ...)`（`drivers/dma/dmaengine.c:906`）
盯的就是第三行 —— 从设备通道正常情况下 `client_count` 永远是 1。

---

## §3 关键结构体逐个说明

### §3.1 `struct dma_device` 的回调表：谁必填、谁选填

> 定义：`include/linux/dmaengine.h:851-944`

这张表是控制器驱动的**全部契约**。第三列"缺了会怎样"是本节的核心信息 ——
它区分了三种后果：注册失败 / API 返回错误码 / **静默失效**。

| 回调 | 位置 | 缺了会怎样 |
|---|---|---|
| `device_issue_pending` | `:939` | **注册失败** `-EIO`，打 `"Device issue_pending is not defined\n"` |
| `device_tx_status` | `:936` | **注册失败** `-EIO`，打 `"Device tx_status is not defined\n"` |
| `device_prep_dma_memcpy` | `:886` | 声明了 `DMA_MEMCPY` 才检查 → 注册失败 `-EIO` |
| `device_prep_dma_xor` / `_xor_val` | `:889` / `:892` | 同上（`DMA_XOR` / `DMA_XOR_VAL`） |
| `device_prep_dma_pq` / `_pq_val` | `:895` / `:899` | 同上（`DMA_PQ` / `DMA_PQ_VAL`） |
| `device_prep_dma_memset` | `:903` | 同上（`DMA_MEMSET`） |
| `device_prep_dma_interrupt` | `:909` | 同上（`DMA_INTERRUPT`） |
| `device_prep_dma_cyclic` | `:916` | 同上（`DMA_CYCLIC`） |
| `device_prep_interleaved_dma` | `:920` | 同上（`DMA_INTERLEAVE`） |
| `device_prep_slave_sg` | `:912` | **没有门禁**：声明 `DMA_SLAVE` 却不实现，注册成功；消费者调 `dmaengine_prep_slave_sg` 拿到 **NULL** |
| `device_prep_dma_memset_sg` | `:906` | 同样没有门禁（`DMA_MEMSET_SG`） |
| `device_prep_dma_imm_data` | `:923` | 无对应能力位，消费者拿到 NULL |
| `device_alloc_chan_resources` | `:882` | 可选；`dma_chan_get` 里判空后跳过 |
| `device_free_chan_resources` | `:884` | 可选 |
| `device_config` | `:929` | `dmaengine_slave_config()` 返回 **`-ENOSYS`** |
| `device_pause` / `device_resume` | `:931` / `:932` | 对应 API 返回 **`-ENOSYS`**；且 `dma_slave_caps.cmd_pause`/`cmd_resume` 报 false |
| `device_terminate_all` | `:933` | `dmaengine_terminate_all()` → `-ENOSYS`，`dmaengine_terminate_async()` → `-EINVAL`（不对称，见 `01-api.md` §4） |
| `device_synchronize` | `:934` | **静默失效**：`dmaengine_synchronize()` 什么都不做就返回，消费者以为"回调已经跑完了"，实际可能没有。这是本子系统最危险的可选回调 |
| `device_caps` | `:927` | 可选。有的话核心会用它**覆盖** `dma_get_slave_caps` 已经填好的全部字段 |
| `device_router_config` | `:883` | 只有 router 场景用（`of_dma_router_xlate` 里调） |
| `device_release` | `:940` | 只打 `dev_dbg`，注册照样成功。官方 kerneldoc（`:843-846`）承认「must be implemented to free resources however many existing drivers do not and are therefore not safe to unbind while in use」 |
| `dbg_summary_show` | `:942` | 可选；没有就用核心的默认实现打印通道摘要 |

把上表按后果重排，就得到"控制器驱动最容易漏的三件事"：

1. 漏 `device_synchronize` → 消费者的 use-after-free 无法防（**静默**）
2. 声明 `DMA_SLAVE` 但 `device_prep_slave_sg` 写错分支 → 消费者拿 NULL（**静默**）
3. 漏 `device_release` → `rmmod` 时 UAF（**只有 dev_dbg**）

### §3.2 `struct dma_slave_config` 与 `struct dma_slave_caps` 字段对照

> 定义：`include/linux/dmaengine.h:437-450` 与 `:501-513`

| `dma_slave_config`（消费者填） | 对应的 `dma_slave_caps`（核心报） | 关系 |
|---|---|---|
| `src_addr_width`（单个枚举值） | `src_addr_widths`（位掩码） | 填的值必须在掩码里 |
| `dst_addr_width` | `dst_addr_widths` | 同上 |
| `src_maxburst` / `dst_maxburst` | `min_burst` / `max_burst` | 必须落在 [min, max] 内 |
| — | `max_sg_burst` | 一次 sg 传输的段数上限，0 表示无限制 |
| `direction`（**DEPRECATED**） | `directions`（位掩码） | 方向改在 prep 时传 |
| `src_addr` / `dst_addr`（`phys_addr_t`） | — | 填**外设寄存器的物理地址**，不是 DMA 地址 |
| `src_port_window_size` / `dst_port_window_size` | — | 少数控制器的端口窗口 |
| `device_fc` | — | 由外设做流控（flow controller）而非 DMAC |
| `peripheral_config` / `peripheral_size` | — | 逃生舱：控制器私有的不透明配置块 |
| — | `cmd_pause` / `cmd_resume` / `cmd_terminate` | 核心从回调指针**推导**出来的（`drivers/dma/dmaengine.c:608-610`） |
| — | `residue_granularity` | 决定 `dmaengine_tx_status` 的 `residue` 可信到什么程度 |
| — | `descriptor_reuse` | 决定 `dmaengine_desc_set_reuse` 会不会返回 `-EPERM` |

`enum dma_slave_buswidth`（`:373-384`）除了 `DMA_SLAVE_BUSWIDTH_UNDEFINED`（0）之外，
取值是 **1 / 2 / 3 / 4 / 8 / 16 / 32 / 64 / 128 字节**（注意中间有个不是 2 的幂的 `_3_BYTES`）。
`src_addr_widths` 是这些的位掩码，所以正确写法是 `BIT(DMA_SLAVE_BUSWIDTH_4_BYTES)`。

> 顺手记一处**驱动侧的写法差异**：`drivers/dma/qcom/bam_dma.c:1349` 写的是
> `src_addr_widths = DMA_SLAVE_BUSWIDTH_4_BYTES`（**枚举值本身**，值为 4），
> 而 `drivers/dma/mediatek/mtk-uart-apdma.c` 写的是
> `BIT(DMA_SLAVE_BUSWIDTH_1_BYTE)`（**位掩码**）。
> 头文件 kerneldoc 说这个字段是 bit mask，所以前者读起来像笔误 ——
> 但因为 `4 == BIT(2)`，它恰好等价于"支持 4 字节位宽"，行为上没错。
> 这类"歪打正着"记入 `99-evidence.md` §6。

### §3.3 cookie 的一对计数器

`struct dma_chan` 里有两个 cookie 字段，很容易搞混：

| 字段 | 含义 | 谁改 |
|---|---|---|
| `cookie` | **最后一个被分配出去的** cookie（= last used） | `dma_cookie_assign()`（`drivers/dma/dmaengine.h:29`） |
| `completed_cookie` | **最后一个已完成的** cookie（= last complete） | `dma_cookie_complete()`（`:52`） |

判完成的算法在 `dma_async_is_complete()`（`include/linux/dmaengine.h:1457-1468`），
核心是一个带**回绕处理**的区间判断：

```
                   已完成                   在飞
        ├──────────────────────┤├─────────────────────┤
   ...  completed_cookie                        cookie
        ↑                      ↑                      ↑
     很久以前的 cookie      刚完成的            刚分配的
     → DMA_COMPLETE      → DMA_COMPLETE     → DMA_IN_PROGRESS
```

`dma_cookie_complete()` 里有一句 `BUG_ON(tx->cookie < DMA_MIN_COOKIE)`
（`drivers/dma/dmaengine.h:54`），紧接着把 `tx->cookie` 清零。
所以**对同一个描述符调两次 `dma_cookie_complete` 直接挂内核** ——
这是控制器驱动中断处理写错时的典型死法。

### §3.4 virt-dma 的五态队列

> 定义：`drivers/dma/virt-dma.h:22-37`

```
  prep 回调里 vchan_tx_prep()
        ↓
  ┌─ desc_allocated ─┐   ← 已 prep，还没 submit
        ↓ vchan_tx_submit()（消费者调 dmaengine_submit 触发）
  ┌─ desc_submitted ─┐   ← 已拿到 cookie，还没下发
        ↓ vchan_issue_pending()（消费者调 dma_async_issue_pending 触发）
  ┌─ desc_issued ────┐   ← 硬件在跑；vchan_find_desc() 只搜这一条
        ↓ vchan_cookie_complete()（驱动中断里调）
  ┌─ desc_completed ─┐   ← 等 tasklet 来发回调
        ↓ vchan_complete() tasklet
     回调发出 → vchan_vdesc_fini()
        ├─ 带 DMA_CTRL_REUSE → 回到 desc_allocated（可再次 submit）
        └─ 否则 → vc->desc_free(vd)

  另一条支路：
  desc_issued ──vchan_terminate_vdesc()──> ┌─ desc_terminated ─┐
                                                  ↓ vchan_synchronize()
                                            tasklet_kill + 释放
```

第五条链表 `desc_terminated` 是**整个 virt-dma 设计里最容易被忽略的一环**：
它存在的唯一目的就是让 `device_synchronize` 有东西可等。
`vchan_synchronize()`（`drivers/dma/virt-dma.h:211-225`）先 `tasklet_kill(&vc->task)`
再释放这条链表 —— 这就是"terminate 之后 buffer 才真的安全"那个保证的实现。

### §3.5 `struct dmaengine_unmap_data`：谁来 unmap

> 定义：`include/linux/dmaengine.h:552-565`

这是给 **async_tx / RAID 场景**准备的自动 unmap 机制：把 `dma_map_page` 的结果
存进一个引用计数结构，挂到 `desc->unmap`，完成时核心自动 `dma_unmap_page`。

| 字段 | 含义 |
|---|---|
| `map_cnt` | 总映射数。`CONFIG_DMA_ENGINE_RAID` 开时是 `u16`，否则 `u8` —— 这是这个宏在数据结构上的唯一影响 |
| `to_cnt` / `from_cnt` / `bidi_cnt` | 三个方向各几个，unmap 时按这三段依次用不同 direction 解映射 |
| `dev` | 用哪个 device 解映射 |
| `kref` | 引用计数 |
| `addr[]` | 柔性数组，存 `dma_addr_t` |

底层是 4 个 mempool（`drivers/dma/dmaengine.c:1372-1379`）：容量 2 / 16 / 128 / 256，
后三个**只在 `CONFIG_DMA_ENGINE_RAID` 开启时才编进去**。
`__get_unmap_pool()` 按 `get_count_order(nr)` 选池，最后一条路径是 **`BUG();`**
（`:1397`）—— 请求超过 256 个映射直接挂内核。

> **arm64 上的普通从设备驱动完全用不到这套东西**：它们自己 `dma_map_sg` /
> `dma_unmap_sg`，`desc->unmap` 一直是 NULL。这一节只在你读 async_tx / md-raid
> 相关代码时有用。

---

## §4 核心操作数据流图：一次从设备传输

dmaengine 是**框架型**子系统，所以这张图的形状是"消费者 → 核心 → 控制器回调 → 硬件"，
而不是 i2c/spi 那种"调用者 → 硬件"。注意核心在中间做的事**非常少** ——
绝大多数箭头是一行 `static inline` 转调。

```
消费者驱动（如 8250 串口 / i2s 音频）
  │
  │ ① 申请通道
  ├─→ dma_request_chan(dev, "tx")
  │      └─→ of_dma_request_slave_channel()   ← 读 DTS 的 dmas/dma-names
  │             └─→ of_dma_find_controller()  ← 查 of_dma_list
  │                    └─→ ofdma->of_dma_xlate()  控制器驱动提供的翻译回调
  │                           └─→ dma_get_slave_channel() / __dma_request_channel()
  │                                  └─→ dma_chan_get()
  │                                         └─→ device_alloc_chan_resources()  回调
  │
  │ ② 查能力（可选，但强烈建议）
  ├─→ dma_get_slave_caps(chan, &caps)
  │      └─ 从 chan->device 的字段【抄】出来，不碰硬件
  │      └─→ device_caps()  可选回调，可覆盖上面抄出来的值
  │
  │ ③ 配参数
  ├─→ dmaengine_slave_config(chan, &cfg)
  │      └─→ device_config()  回调（缺 → -ENOSYS）
  │
  │ ④ 自己映射内存  用 dmaengine_get_dma_device(chan) 拿 device，不是自己的 dev
  ├─→ dma_map_sg(dmaengine_get_dma_device(chan), sgl, n, dir)
  │
  │ ⑤ 准备描述符
  ├─→ dmaengine_prep_slave_sg(chan, sgl, n, dir, DMA_PREP_INTERRUPT)
  │      └─→ device_prep_slave_sg()  回调（缺 → 返回 NULL，无日志）
  │             └─ 驱动内部：分配私有描述符，用 vchan_tx_prep() 入 desc_allocated
  │
  │ ⑥ 设回调
  ├─  desc->callback = my_done;  desc->callback_param = ctx;
  │
  │ ⑦ 提交（之后不得再碰 desc，只留 cookie）
  ├─→ cookie = dmaengine_submit(desc)
  │      └─→ desc->tx_submit(desc) == vchan_tx_submit()
  │             └─ dma_cookie_assign() + 入 desc_submitted
  │
  │ ⑧ 启动
  ├─→ dma_async_issue_pending(chan)
  │      └─→ device_issue_pending()  必填回调
  │             └─ vchan_issue_pending()：submitted → issued
  │             └─ 驱动写硬件寄存器 ──────────────────→ 【DMA 控制器硬件】
  │                                                            │
  │ ⑨ 查进度（可选）                                            │ 传输完成
  ├─→ dmaengine_tx_status(chan, cookie, &state)                │ 拉中断
  │      └─→ device_tx_status()  必填回调                      ↓
  │             └─ dma_cookie_status() + vchan_find_desc()   驱动的 irq handler
  │                                                              └─ vchan_cookie_complete(vd)
  │                                                                    └─ 入 desc_completed
  │                                                                    └─ tasklet_schedule
  │ ⑩ 回调                                                              ↓
  │  ← ← ← ← ← ← my_done(ctx) ← dmaengine_desc_callback_invoke ← vchan_complete() tasklet
  │                                          永远是 tasklet 上下文，绝不是硬中断
  │ ⑪ 收尾
  ├─→ dmaengine_terminate_sync(chan)   ← 出错/关闭时
  │      ├─→ device_terminate_all()  回调
  │      └─→ device_synchronize()    可选！缺了这一步就是静默空操作
  ├─→ dma_unmap_sg(...)              ← 必须在 synchronize 之后
  └─→ dma_release_channel(chan)
         └─→ dma_chan_put()
                ├─→ dmaengine_synchronize(chan)   ← 核心自己又同步了一次
                └─→ device_free_chan_resources()  回调
```

图里值得单独记的两处"核心其实什么都没做"：
第 ⑦ 步 `dmaengine_submit()` 的实现就是**一行裸调 `desc->tx_submit(desc)`**，
第 ⑧ 步 `dma_async_issue_pending()` 就是**一行裸调 `device_issue_pending(chan)`** ——
都没有 NULL 检查。所以第 ⑤ 步返回 NULL 却不判断，崩溃点会出现在第 ⑦ 步。

而第 ⑪ 步里 `dma_chan_put()` 自己又调了一次 `dmaengine_synchronize(chan)`
（`drivers/dma/dmaengine.c:505`）—— 这是核心给"忘了 synchronize 的消费者"留的
最后一道保险。但这道保险有**两个洞**：
(a) 它发生在 `device_free_chan_resources` **之前**、消费者 `dma_unmap_sg` **之后**，
救不了已经被 unmap/free 的 buffer；
(b) 它被包在 `if (!chan->client_count && chan->device->device_free_chan_resources)`
里（`:503`）—— **控制器没实现 `device_free_chan_resources` 时这一步根本不执行**。
（登记在 `99-evidence.md` §6）

---

## §5 dmaengine 与 platform 总线的关系

dmaengine **没有 `struct bus_type`**（一手证据见 `99-evidence.md` §1：
`drivers/dma/` 下唯一的 `struct bus_type` 是 `drivers/dma/idxd/bus.c:70` 的
`dsa_bus_type`，属于 Intel DSA，x86 专用，arm64 无关）。
但它**有一个 sysfs class**：

```c
static struct class dma_devclass = {
	.name		= "dma",
	.dev_groups	= dma_dev_groups,
	.dev_release	= chan_dev_release,
};
```
（`drivers/dma/dmaengine.c:240-244`，在 `dma_bus_init()` 里 `class_register`，`:1649`）

于是完整的形状是：

```
                         ┌──────────────── platform 总线 ────────────────┐
                         │                                                │
  DTS: dma-controller@… ─┼─→ platform_device ──绑定──→ platform_driver    │
  （compatible/reg/      │        │  （如 pl330 / bam_dma）                │
    interrupts/          │        │                                        │
    #dma-cells）         │        │ probe 里做两件注册：                    │
                         │        ├─→ dma_async_device_register()          │
                         │        │      └─ 挂进 dma_device_list（全局链表） │
                         │        │      └─ 每条通道 device_register()      │
                         │        │            只设 .class，不设 .bus     │
                         │        └─→ of_dma_controller_register()          │
                         │               └─ 挂进 of_dma_list（DT 查找表）    │
                         └────────────────────────────────────────────────┘
                                  │                    │
                                  ↓                    ↓
                  /sys/class/dma/dma0chan0     of_dma_list（内核内部，无 sysfs 呈现）
                  /sys/class/dma/dma0chan1
                        ↑
                        │ 每个通道 3 个只读属性（§6）
                        │
  DTS: 消费者节点 ───────┼─→ 它自己的 platform_device（也在 platform 总线上）
  （dmas / dma-names）   │        └─ probe 里 dma_request_chan(dev, "tx")
                         │              成功后核心建两条符号链接：
                         │                 /sys/class/dma/dma0chan0/slave  → 消费者 device
                         │                 <消费者 device>/dma:tx          → 通道 device
                         └────────────────────────────────────────────────
```

**三个必答问题的答案**：

| 问题 | 答案 | 代码依据 |
|---|---|---|
| 控制器 DT 节点由谁变成设备？ | platform 总线（`of_platform_populate`），dmaengine 自己不做设备创建 | 控制器驱动都是 `platform_driver` |
| 通道会不会被继续下钻成设备？ | **不会**。通道的 device 只有 class 没有 bus，`device_register` 不触发匹配 | `drivers/dma/dmaengine.c:1065-1069` 只赋 class/parent/name |
| 两条"总线"的连接点是哪一行？ | 两处：`chan->dev->device.parent = device->dev`（通道 → 控制器 platform device）和 `chan->slave = dev`（通道 → 消费者 device） | `:1066` 与 `dma_request_chan()` 的 `found:` 标签处 |

> 和 clk 对照能把这个形状钉准：**clk 既没有 bus_type 也没有 class，
> 时钟在 sysfs 里完全不可见；dmaengine 没有 bus_type 但有 class，
> 通道在 `/sys/class/dma/` 里看得见但永远不绑 driver。**
> 这是"框架型"子系统内部的两个不同档位。

---

## §6 全局变量、锁与 sysfs 属性

### §6.1 全局状态

| 符号 | 位置 | 作用 |
|---|---|---|
| `dma_list_mutex` | `drivers/dma/dmaengine.c:58` | **核心唯一的大锁**，保护下面三项 |
| `dma_ida` | `:59` | 分配 `dma_device->dev_id` |
| `dma_device_list` | `:60` | 所有已注册控制器的链表（RCU 遍历 + mutex 写） |
| `dmaengine_ref_count` | `:61` | 有多少个 `dmaengine_get()` 还没 put，决定公共通道要不要保持引用 |
| `rootdir` | `:67` | debugfs 根目录（`CONFIG_DEBUG_FS`） |
| `dma_devclass` | `:240` | sysfs class（§5） |
| `dma_cap_mask_all` | `:249` | 用于 `dma_channel_table_init` 的全 1 掩码 |
| `channel_table[DMA_TX_TYPE_END]` | `:260` | **percpu** 的"每种操作类型一条推荐通道"表 |
| `unmap_pool[]` | `:1372` | 4 个 unmap mempool（§3.5） |
| `of_dma_list` | `drivers/dma/of-dma.c:20` | 所有已注册的 DT 控制器 |
| `of_dma_lock` | `:21` | 保护上一项 |
| `last_index` | `:259` | 函数内 `static atomic_t`，给同名 `dma-names` 做轮转 |

`dma_channel_table_init()`（`:262`）注册为 **`arch_initcall`**（`:293`），
`dma_bus_init()`（`:1642`）也是 `arch_initcall`（`:1655`）——
所以 dmaengine 核心在**驱动初始化之前**就绪。

`channel_table` 初始化时会 `bitmap_fill` 全部能力位，然后**显式清掉三个**：
`DMA_INTERRUPT`、`DMA_PRIVATE`、`DMA_SLAVE`（`:273-275`）。
源码注释解释理由：这三个是「channel capabilities, but are not associated with
an operation」—— 它们不是"能做的操作"，所以不进"按操作类型找通道"的表。
**推论：`dma_find_channel(DMA_SLAVE)` 永远返回 NULL**，从设备通道必须走
`dma_request_chan()`。

### §6.2 锁的层级

| 锁 | 类型 | 保护什么 | 能睡吗 |
|---|---|---|---|
| `dma_list_mutex` | mutex | 控制器链表、`dmaengine_ref_count`、通道注册/注销、`client_count` | ✅ |
| `of_dma_lock` | mutex | `of_dma_list` | ✅ |
| `virt_dma_chan.lock` | spinlock | 一条虚拟通道的五条链表 + `cyclic` 指针 | ❌ |
| 各控制器驱动私有锁 | 多为 spinlock | 硬件寄存器 | ❌ |

virt-dma 的加锁风格有一处不一致值得注意：多数辅助函数用
`spin_lock_irqsave`（`drivers/dma/virt-dma.h:69`/`:119`/`:193`/`:218`、
`drivers/dma/virt-dma.c:26`/`:55`），但 tasklet 里的 `vchan_complete()`
用的是 `spin_lock_irq`（`drivers/dma/virt-dma.c:90`）——
因为 tasklet 上下文本来就是开中断的，不需要保存标志。

**核心没有"per-channel 锁"**。`dma_chan` 的 `client_count`、`table_count`
全靠 `dma_list_mutex` 保护，所以"申请/释放通道"是**全局串行**的。
这对启动期有成百上千个消费者的 SoC 是一个隐含的串行点，但因为都在 probe
阶段、单次开销很小，实践中没人抱怨。

### §6.3 通道的 sysfs 属性

每个 `/sys/class/dma/dma<N>chan<M>/` 下有三个**只读**属性
（`drivers/dma/dmaengine.c:162`/`:184`/`:206`，都是 `DEVICE_ATTR_RO`）：

| 属性 | 含义 | 数据来源 |
|---|---|---|
| `memcpy_count` | 这条通道做过多少次 memcpy | 累加所有 CPU 的 `chan->local->memcpy_count` |
| `bytes_transferred` | 累计字节数 | 累加所有 CPU 的 `chan->local->bytes_transferred` |
| `in_use` | `client_count` 是否非 0 | 只有 0/1，看不出被谁用了 |

前两个字段是 `struct dma_chan_percpu`（`include/linux/dmaengine.h:293-297`）
的成员，而**只有 async_tx / memcpy 路径会去累加它们**。
所以在纯从设备场景（arm64 的绝大多数情况）下，
**`memcpy_count` 和 `bytes_transferred` 永远是 0**，不能用来判断"这条通道有没有在跑"。
要看真实活动只能用 `/sys/kernel/debug/dmaengine/summary`（见 `07-debug.md`）。

---

## §7 命名约定速查

| 前缀/后缀 | 归谁 | 例子 |
|---|---|---|
| `dma_*` | 核心的对外 API | `dma_request_chan`、`dma_release_channel`、`dma_async_issue_pending` |
| `dmaengine_*` | **也是对外 API**，多为 `static inline` 包装 | `dmaengine_prep_slave_sg`、`dmaengine_submit`、`dmaengine_terminate_sync` |
| `device_*`（作为字段名） | `struct dma_device` 里的**回调**，由控制器驱动实现 | `device_prep_slave_sg`、`device_issue_pending` |
| `dma_cookie_*` | **提供方私有**（`drivers/dma/dmaengine.h`） | `dma_cookie_assign`、`dma_cookie_complete` |
| `dmaengine_desc_*`（在私有头里） | 提供方私有的回调调用封装 | `dmaengine_desc_get_callback_invoke` |
| `dmaengine_desc_*`（在公开头里） | 消费者的元数据/复用 API | `dmaengine_desc_attach_metadata`、`dmaengine_desc_set_reuse` |
| `vchan_*` | virt-dma 辅助层 | `vchan_tx_prep`、`vchan_cookie_complete`、`vchan_synchronize` |
| `of_dma_*` | DT 侧 | `of_dma_controller_register`、`of_dma_simple_xlate` |
| `to_virt_chan` / `to_virt_desc` | virt-dma 的 `container_of` 上溯宏 | — |
| `DMA_*`（能力位） | `enum dma_transaction_type` | `DMA_SLAVE`、`DMA_CYCLIC`、`DMA_MEMCPY` |
| `DMA_PREP_*` / `DMA_CTRL_*` | `enum dma_ctrl_flags`，prep 时传的标志 | `DMA_PREP_INTERRUPT`、`DMA_CTRL_REUSE` |
| `DMA_MEM_TO_DEV` 一族 | `enum dma_transfer_direction` | 注意还有个 `DMA_TRANS_NONE` |
| `DMA_SLAVE_BUSWIDTH_*_BYTES` | `enum dma_slave_buswidth` | 填 `caps` 时要 `BIT()` 包一层 |

> 一条**极易踩的命名陷阱**：`dmaengine_desc_*` 这个前缀**跨了两个头文件、
> 语义完全不同**。`include/linux/dmaengine.h` 里的是给消费者的（元数据、复用），
> `drivers/dma/dmaengine.h` 里的是给控制器驱动的（回调取出与调用）。
> 看到这个前缀先确认自己在读哪个头文件。

> 第二条：`device_*` 作为**字段名**是回调，但作为**函数名**（`dma_async_device_register`
> 里的 `device_has_all_tx_types`）就是核心的内部辅助函数。前者永远出现在
> `device->device_xxx(...)` 这种双 `device` 形式里。

---

## §8 去哪继续

| 想了解 | 去哪篇 |
|---|---|
| 每个 API 的签名、错误码、上下文约束 | `01-api.md` |
| 注册/申请/传输/终止的逐段代码剖析 | `03-internals.md` |
| virt-dma 层、router、dmatest 这些子框架 | `04-extensions.md` |
| 消费者与控制器驱动的代码骨架 | `05-usage.md` |
| DTS 属性与配错定位 | `06-devicetree.md` |
| debugfs / tracepoint / 错误码分诊 | `07-debug.md` |
| 三层描述符、cookie、五态队列的大白话解释 | `08-feynman.md` |
