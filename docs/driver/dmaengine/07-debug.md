---
title: 调试方法
---

# 07 调试：dmaengine 能看到什么、看不到什么

> arm64-only。本篇所有命令都**没有在真机上跑过**，因此不给示例输出 ——
> 编造 `dmesg` / `summary` 的样子会误导读者。见 `99-evidence.md` §5。

## 一页速查（先看这张，再往下读）

| 我想知道 | 去看 | 需要的开关 | 本篇 |
|---|---|---|---|
| 系统里有哪些 DMA 控制器、几条通道 | `/sys/kernel/debug/dmaengine/summary` | `CONFIG_DEBUG_FS` | §4 |
| 某条通道现在被谁占着 | 同上（第二列就是占用者） | `CONFIG_DEBUG_FS` | §4.2 |
| 某条通道搬过多少字节 | `/sys/class/dma/dma0chan0/bytes_transferred` | 无（永久在） | §3 |
| 某个外设驱动到底申请到通道了没 | `/sys/class/dma/*/` 下的 `slave` 符号链接 | 无 | §3.3 |
| 控制器注册为什么失败 | `dmesg`，找 `"Device claims capability ..."` | 无（`dev_err`） | §2.2 |
| 通道申请为什么失败 | 打开 dynamic debug 看 `dev_dbg` | `CONFIG_DYNAMIC_DEBUG` | §2.3 |
| 新写的控制器驱动对不对 | `dmatest` | `CONFIG_DMATEST` | §5 |
| 一笔传输的时间线 / 延迟 | **没有办法**，dmaengine 核心零 tracepoint | — | §7 |
| 拿到一个错误码想反查 | §6 的分诊表 | — | §6 |

---

## §0 先说结论：这个子系统的可观测性很薄

和 clk（有 `clk_summary` + 完整 tracepoint 事件集）、
pinctrl（有多个 debugfs 文件）比，dmaengine 的调试面小得反常：

| 手段 | dmaengine 有没有 | 说明 |
|---|---|---|
| tracepoint | **核心完全没有**（全树只有 tegra20-apb-dma 这一个驱动有，arm64 碰不到） | 见 §7，可以自己复现 |
| debugfs | 有，但**只有一个文件** | `summary`，`dmaengine.c:131-132` |
| sysfs | 有，**3 个只读属性 × 每通道** | `dmaengine.c:182`/`:204`/`:222` |
| 专用 Kconfig 调试开关 | **没有** | `drivers/dma/Kconfig` 里没有 `DMA_DEBUG` 之类 |
| 自带测试客户端 | 有，功能强 | `dmatest`，见 §5 |
| 故障注入 | **没有** | `dmatest` 的 `noverify`/`polled` 是模式切换，不是注入 |

所以**调 DMA 问题时，日志（§2）和 dmatest（§5）是主力**，
debugfs 只用来确认"通道存在且被谁占着"。

---

## §1 编译期开关：每一道门都要过

### §1.1 开关全门表

调试功能失效的最常见原因是"某一道门没开"。dmaengine 的门是这样串的：

| 你要的东西 | 门 1 | 门 2 | 门 3 | 全开了才可见 |
|---|---|---|---|---|
| `summary` 文件 | `CONFIG_DMA_ENGINE` | `CONFIG_DEBUG_FS`（`dmaengine.c:64`） | debugfs 已挂载 | `/sys/kernel/debug/dmaengine/summary` |
| `/sys/class/dma/` | `CONFIG_DMA_ENGINE` | — | — | 永久可见（`class_register` 在 `dmaengine.c:1649`） |
| `dev_dbg` 那一批日志 | `CONFIG_DYNAMIC_DEBUG` 或编译时 `-DDEBUG` | 逐文件/逐行 `+p` | — | §2.3 |
| `dmatest` | `CONFIG_DMA_ENGINE` | `CONFIG_DMATEST`（`drivers/dma/Kconfig:798`） | 通道有 `DMA_MEMCPY` 等能力 | §5 |
| `dbg_client_name`（占用者名字） | `CONFIG_DEBUG_FS`（`dmaengine.c:856`） | — | — | §4.2 |

`CONFIG_DMATEST` 的定义（`drivers/dma/Kconfig:798-804`）：

```
config DMATEST
        tristate "DMA Test client"        /* :799 */
        depends on DMA_ENGINE             /* :800 */
        select DMA_ENGINE_RAID            /* :801 */
```

注意 `select DMA_ENGINE_RAID`（`:801`）：**打开 dmatest 会顺带打开
`DMA_ENGINE_RAID`**（`drivers/dma/Kconfig:806-807`，是个没有 prompt 的
`bool`）。这会改变 `dma_async_device_register` 里 `has_all_tx_types`
的判定路径（`dmaengine.c:1237-1238`），所以**开了 dmatest 的内核和
产品内核在 async_tx 能力上并不完全等价**。做性能对比时要注意。

### §1.2 `CONFIG_DEBUG_FS` 关掉之后会发生什么

关掉不会编译错，因为核心备了空实现：

```c
/* :134 */ #else
/* :135 */ static inline void dmaengine_debugfs_init(void) { }
/* :136 */ static inline int dmaengine_debug_register(struct dma_device *dma_dev)
           ...
/* :141 */ static inline void dmaengine_debug_unregister(struct dma_device *dma_dev) { }
```

同时 `drivers/dma/dmaengine.h:185-199` 也给 `dmaengine_get_debugfs_root`
备了一份返回 NULL 的空实现（`:197`），连 `struct dentry;` 的前向声明
都补上了（`:193`）。

**但这个访问器 `dmaengine_get_debugfs_root`（`drivers/dma/dmaengine.h:188-191`）
在本树里一个调用者都没有。** 复现：

```bash
/usr/bin/grep -rn 'dmaengine_get_debugfs_root' --include=*.c --include=*.h . \
        | /usr/bin/grep -v 'drivers/dma/dmaengine.h'      # 空
```

**注意这不等于"那个目录是空的"** —— 有三个驱动直接访问
`dma_dev.dbg_dev_root` 字段（`include/linux/dmaengine.h:943`），
绕开了访问器：

| 驱动 | 往里放什么 | arm64 上是否相关 |
|---|---|---|
| `drivers/dma/hisi_dma.c` | `regs` regset + 每通道一个 `channelN/` 子目录（`:906`/`:921`/`:939`） | **是**（`drivers/dma/Kconfig:248` `depends on ARCH_HISI`） |
| `drivers/dma/xilinx/xilinx_dpdma.c` | 一个 `testcase` 文件（`:458`） | **是**（ZynqMP） |
| `drivers/dma/ptdma/ptdma-debugfs.c` | `info` / `stats` / `q/`（`:93`/`:96`/`:102`） | 否（`drivers/dma/ptdma/Kconfig:4` `depends on X86_64`） |

所以准确说法是：**核心提供的那个访问器没人用，但目录本身有人用** ——
在 HiSilicon 和 ZynqMP 平台上
`/sys/kernel/debug/dmaengine/<设备名>/` 里确实有东西。
其余 140+ 个驱动那里是空目录。已记入 `99-evidence.md` §6 第 ⑯ 条。

---

## §2 日志：按可见性等级分

### §2.1 可见性等级总表

**这是本篇最需要先看懂的一张表。** 同一个子系统里，
`dev_err` / `dev_warn` / `WARN_ONCE` / `dev_dbg` 的可见性完全不同，
"日志里没看到"绝不等于"这个分支没走到"。

| 等级 | 默认可见 | 数量（`drivers/dma/dmaengine.c`） | 典型位置 |
|---|---|---|---|
| `pr_err` | ✅ 是 | 3 | `:286`、`:1059`、`:1147` |
| `dev_err` | ✅ 是 | 13 | `:1154`–`:1224` 的注册校验、`:529` 的 timeout、`:1590` |
| `dev_warn` | ✅ 是 | 2 | `:868`、`:870`（sysfs 符号链接创建失败） |
| `WARN_ONCE` | ✅ 是，但**只有第一次** | 2 | `:906`、`:1109` |
| `dev_dbg` | ❌ **否**，要 dynamic debug | 8 | `:626`、`:641`、`:646`、`:675`、`:679`、`:710`、`:952`、`:1229` |
| `pr_debug` | ❌ 否 | 1（在 `of-dma.c:40-41`） | 见 `06-devicetree.md` §8 |
| `BUG()` | ✅ 直接挂 | 1 | `:1397` |
| `BUG_ON()` | ✅ 直接挂 | 1 | `drivers/dma/dmaengine.h:54` |

**`WARN_ONCE` 这一栏最容易骗人**：`dmaengine.c:906`（
`dma_release_channel` 里检查 `chan->client_count != 1`）和 `:1109`
（`__dma_async_device_channel_unregister` 里检查
`!device->device_release && chan->client_count`）都是 `_ONCE`。
**第二次以后同样的 bug 完全静默。** 调多设备并发问题时，
看到一条 WARN 就要假设"可能还有 N 条被吃掉了"。

更要命的一条在 `:1229`：

```c
/* :1228 */ if (!device->device_release)
/* :1229 */         dev_dbg(device->dev,
/* :1230 */                  "WARN: Device release is not defined so it is not safe to unbind this driver while in use\n");
```

**文字里写着 `WARN:`，级别却是 `dev_dbg`。** 默认编译（不开
dynamic debug）下这句话根本不会出现。写驱动的人以为"没警告就是没问题"，
实际上这条路径下 unbind 是不安全的。已记入 `99-evidence.md` §6。

### §2.2 注册期错误：13 条 `dev_err` 里 9 条是同一句话

`dma_async_device_register` 的能力校验（`dmaengine.c:1153-1214`）
一共 9 个 gate，**打印的字符串完全一样**：

```c
dev_err(device->dev,
        "Device claims capability %s, but op is not defined\n",
        "DMA_MEMCPY");        /* :1156 起，能力名逐个不同 */
```

九个 gate 的能力名和行号：

| 能力名（`%s` 展开成这个） | 检查的回调 | 报错行 |
|---|---|---|
| `DMA_MEMCPY` | `device_prep_dma_memcpy` | `:1154-1156`，返回 `-EIO`（`:1157`） |
| `DMA_XOR` | `device_prep_dma_xor` | `:1161-1163`，`:1164` |
| `DMA_XOR_VAL` | `device_prep_dma_xor_val` | `:1168-1170`，`:1171` |
| `DMA_PQ` | `device_prep_dma_pq` | `:1175-1177`，`:1178` |
| `DMA_PQ_VAL` | `device_prep_dma_pq_val` | `:1182-1184`，`:1185` |
| `DMA_MEMSET` | `device_prep_dma_memset` | `:1189-1191`，`:1192` |
| `DMA_INTERRUPT` | `device_prep_dma_interrupt` | `:1196-1198`，`:1199` |
| `DMA_CYCLIC` | `device_prep_dma_cyclic` | `:1203-1205`，`:1206` |
| `DMA_INTERLEAVE` | `device_prep_interleaved_dma` | `:1210-1212`，`:1213` |

另外 4 条独立的：

| 打印原文 | 位置 | 返回 |
|---|---|---|
| `DMAdevice must have dev` | `dmaengine.c:1147`（`pr_err`，因为还没有 `dev` 可用） | `-EIO`（`:1148`） |
| `Device tx_status is not defined` | `:1218` | `-EIO`（`:1219`） |
| `Device issue_pending is not defined` | `:1224` | `-EIO`（`:1225`） |
| `dmaengine dma_channel_table_init failure: %d` | `:286`（`pr_err`，`subsys_initcall` 期） | — |

**实践读法：看到 `"Device claims capability X, but op is not defined"`，
问题一定在你自己的驱动里 —— `dma_cap_set()` 设了 X，但对应的
`device_prep_dma_*` 忘了赋值。** 这是新写控制器驱动最常撞的一条，
`05-usage.md` §2.2 的骨架里有对应的次序要求。

但这 9 个 gate **只覆盖了 16 个能力位里的 9 个**。
`enum dma_transaction_type`（`include/linux/dmaengine.h:51-70`）一共 16 项
（`DMA_MEMCPY` `:52` 到 `DMA_LOAD_EOT` `:67`），没被检查的 7 项是：
`DMA_MEMSET_SG`（`:58`）、`DMA_PRIVATE`（`:60`）、`DMA_ASYNC_TX`（`:61`）、
**`DMA_SLAVE`（`:62`）**、`DMA_COMPLETION_NO_ORDER`（`:65`）、
`DMA_REPEAT`（`:66`）、`DMA_LOAD_EOT`（`:67`）。
`DMA_SLAVE` 恰好是 arm64 上最常用的那个。也就是说
「`dma_cap_set(DMA_SLAVE, ...)` 了但 `device_prep_slave_sg` 忘了写」
**注册期一声不响，运行到 `dmaengine_prep_slave_sg` 时才空指针**。
已记入 `99-evidence.md` §6。

### §2.3 通道申请失败：真相全在 `dev_dbg` 里

`dma_request_chan` 失败时**核心自己不打任何可见日志**。
错误原因全在 `private_candidate` / `find_candidate` 那一带的 8 条 `dev_dbg` 里
（其中 3 条文字完全相同，见表）：

| 打印原文（`%s` 是函数名） | 位置 | 含义 |
|---|---|---|
| `<func>: wrong capabilities` | `dmaengine.c:626` | 你要的能力位这个控制器没有 |
| `<func>: <chan> busy` | `:641` | 通道已被别人占（`client_count` 非 0） |
| `<func>: <chan> filter said false` | `:646` | filter 函数拒绝了这条通道 |
| `<func>: <chan> module removed` | `:675-676` | `dma_chan_get` 返回 `-ENODEV`：抢到通道但模块正在卸载，核心顺手把这个 device 从全局链表摘掉（`:677`） |
| `<func>: failed to get <chan>: (<err>)` | `:679-681` | 同一处的 else 分支：`dma_chan_get` 返回**别的**错误码 |
| `<func>: failed to get <chan>: (<err>)` | `:710-712` | 同一句话，但在 `dma_get_slave_channel`（`:697-724`）里 —— 指定通道拿不到 |
| `<func>: failed to get <chan>: (<err>)` | `:952-954` | 又是同一句话，在 `dmaengine_get`（`:932-965`）里 —— 建立全局引用时某条通道失败 |

打开方式：

```bash
echo 'file drivers/dma/dmaengine.c +p' > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/dma/of-dma.c   +p' > /sys/kernel/debug/dynamic_debug/control
# 触发一次消费者 probe
echo <设备> > /sys/bus/platform/drivers/<驱动>/unbind
echo <设备> > /sys/bus/platform/drivers/<驱动>/bind
dmesg | tail -60
```

**调 DMA 通道申请问题时，第一个动作就应该是打开这两个文件的
dynamic debug**，而不是往自己驱动里加打印 —— 核心已经把原因写好了，
只是默认藏着。

### §2.4 传输期的两条可见日志

| 打印原文 | 位置 | 触发条件 | 之后返回 |
|---|---|---|---|
| `<func>: timeout!` | `dmaengine.c:529`（`dev_err`） | `dma_sync_wait`（`:520-539`）轮询超时 | 循环退出，返回当前 `status` |
| `<func> timeout waiting for descriptor submission` | `:1590-1592`（`dev_err`） | `dma_wait_for_async_tx`（`:1581-1599`）自旋等 `tx->cookie` 脱离 `-EBUSY`（`:1588` 是**循环条件**，不是返回值）超时 | `DMA_ERROR`（`:1593`） |

注意这两条都在**同步等待**路径上。arm64 的 slave DMA 正常流程
（`prep → submit → issue_pending → 中断回调`）**一条日志都不打**。
传输本身出问题时，核心是完全沉默的 —— 只能靠驱动自己的
`dev_err` 或 `dmaengine_tx_status` 的返回值判断。

### §2.5 sysfs 符号链接失败的两条 `dev_warn`

`dma_request_chan` 成功之后会建两条符号链接（`dmaengine.c:866-870`）：

```c
/* :866 */ if (sysfs_create_link(&chan->dev->device.kobj, &dev->kobj,
/* :867 */                       DMA_SLAVE_NAME))
/* :868 */         dev_warn(dev, "Cannot create DMA %s symlink\n", DMA_SLAVE_NAME);
/* :869 */ if (sysfs_create_link(&dev->kobj, &chan->dev->device.kobj, chan->name))
/* :870 */         dev_warn(dev, "Cannot create DMA %s symlink\n", chan->name);
```

两条 warn 的**格式串一模一样**，只靠最后一个 `%s` 区分：
第一条展开成固定的 `slave`（宏 `DMA_SLAVE_NAME`，定义在 `dmaengine.c:146`），
第二条展开成 `dma:<name>`（`chan->name` 在 `:861` 由
`kasprintf(GFP_KERNEL, "dma:%s", name)` 生成）。
**这两条只是符号链接没建上，通道其实已经申请成功了** ——
看到它不要往"申请失败"的方向查。

---

## §3 sysfs：`/sys/class/dma/`

### §3.1 目录长什么样、名字从哪来

dmaengine 注册了一个 class（`static struct class dma_devclass`，
`dmaengine.c:240-244`，`.name = "dma"` 在 `:241`），
`class_register` 在 `dma_bus_init` 里（`dmaengine.c:1649`）。
**每条通道一个 device**，名字由 `__dma_async_device_channel_register` 里的
这一行决定：

```c
/* :1069 */ dev_set_name(&chan->dev->device, "dma%dchan%d",
/* :1070 */              device->dev_id, chan->chan_id);
```

所以路径形如 `/sys/class/dma/dma0chan0/`。
`dma0` 里的 `0` 是 `device->dev_id`（由 `dma_ida` 分配），
`chan0` 里的 `0` 是 `chan->chan_id`（由**每个 device 自己的**
`chan_ida` 分配，`:1057`）。同一个名字在内核里通过
`dma_chan_name()`（`include/linux/dmaengine.h:515`）取到，
日志里的 `<chan>` 就是它。

### §3.2 三个只读属性

| 文件 | 内容 | 实现 | 备注 |
|---|---|---|---|
| `memcpy_count` | 这条通道完成过多少笔 memcpy | `dmaengine.c:162-181`，`DEVICE_ATTR_RO` 在 `:182` | 把 per-cpu 的 `memcpy_count` 累加（`:174`） |
| `bytes_transferred` | 这条通道搬过多少字节 | `:184-203`，`DEVICE_ATTR_RO` 在 `:204` | 同样 per-cpu 累加（`:196`） |
| `in_use` | 当前 `client_count` | `:206-221`，`DEVICE_ATTR_RO` 在 `:222` | 0 = 空闲；非 0 = 有人占 |

三个都注册在 `dma_dev_attrs[]`（`:224-229`）+ `ATTRIBUTE_GROUPS(dma_dev)`（`:230`）。

**前两个计数器在 arm64 的 slave DMA 场景下基本永远是 0。**
原因：它们只在 async_tx 的 memcpy 路径上被累加，
slave 传输（`device_prep_slave_sg` → 中断回调）**不碰这两个字段**。
所以 —— **不要用 `bytes_transferred` 判断"我的 UART DMA 有没有在搬数据"**，
它只对 memcpy 引擎有意义。

三个 `_show` 都有同一个失败分支：`dev_to_dma_chan(dev)`（`:154-160`）
返回 NULL 时返回 `-ENODEV`（`:177`、`:199`、`:217`）。
读到 `Operation not supported`/IO 错误说明这条通道正在被注销。

### §3.3 两条符号链接：判断"谁占了这条通道"最快的办法

通道被 `dma_request_chan` 拿走时会建**双向**链接（`dmaengine.c:866-870`）：

```
/sys/class/dma/dma0chan0/slave        ->  申请者那个 device
/sys/devices/.../<申请者>/dma:rx      ->  /sys/class/dma/dma0chan0
```

- 第一条的名字固定是 `slave`（宏 `DMA_SLAVE_NAME`，`dmaengine.c:146`）
- 第二条的名字是 `dma:<dma-names 里的名字>`（`chan->name`，`:861` 由
  `kasprintf(GFP_KERNEL, "dma:%s", name)` 生成）

**实践价值很高**：`ls -l /sys/devices/platform/<你的外设>/ | grep '^l.*dma:'`
一眼就能看出"这个外设申请到了哪些通道、分别叫什么名字"。
比 debugfs 的 `summary` 更直接，而且不需要 `CONFIG_DEBUG_FS`。

---

## §4 debugfs：只有一个 `summary`

### §4.1 文件的来路

```c
/* :128 */ rootdir = debugfs_create_dir("dmaengine", NULL);
/* :130 */ /* /sys/kernel/debug/dmaengine/summary */
/* :131 */ debugfs_create_file("summary", 0444, rootdir, NULL,
/* :132 */                     &dmaengine_summary_fops);
```

`0444` —— **只读，不能写**。整个 dmaengine 没有任何可写的调试文件。
`dmaengine_summary_fops` 由 `DEFINE_SHOW_ATTRIBUTE(dmaengine_summary)`
（`:124`）生成。

另外每个 `dma_device` 注册时还会建一个**同名于 `dev_name()` 的子目录**
（`dmaengine_debug_register`，`:69-75`）。里面有没有东西**取决于控制器驱动**：
arm64 上 hisi_dma 和 xilinx_dpdma 会往里放文件，其余驱动那里是空的（§1.2 有表）。

### §4.2 内容的格式（逐行从代码推出来，不是抄输出）

外层 `dmaengine_summary_show`（`:102-123`）每个控制器打一行头：

```c
/* :108 */ seq_printf(s, "dma%d (%s): number of channels: %u\n",
/* :109 */            dma_dev->dev_id, dev_name(dma_dev->dev),
/* :110 */            dma_dev->chancnt);
```

然后二选一（`:112-115`）：**驱动自己实现了 `dbg_summary_show` 就用它，
否则用核心的默认实现**。

```c
/* :112 */ if (dma_dev->dbg_summary_show)
/* :113 */         dma_dev->dbg_summary_show(s, dma_dev);
/* :114 */ else
/* :115 */         dmaengine_dbg_summary_show(s, dma_dev);
```

全树**只有一个驱动**实现了这个钩子：
`drivers/dma/ti/k3-udma.c:5394`（`ud->ddev.dbg_summary_show = udma_dbg_summary_show`）。
复现：

```bash
grep -rn 'dbg_summary_show *=' drivers/          # 只有 k3-udma.c:5394
```

核心的默认实现（`dmaengine_dbg_summary_show`，`:83-100`）：

```c
/* :88 */ list_for_each_entry(chan, &dma_dev->channels, device_node) {
/* :89 */         if (chan->client_count) {
/* :90 */                 seq_printf(s, " %-13s| %s", dma_chan_name(chan),
/* :91 */                            chan->dbg_client_name ?: "in-use");
/* :93 */                 if (chan->router)
/* :94 */                         seq_printf(s, " (via router: %s)\n",
/* :95 */                                    dev_name(chan->router->dev));
```

三条必须知道的读法：

1. **`:89` 只打印 `client_count` 非 0 的通道** ——
   **空闲通道在 `summary` 里根本不出现**。头一行说 "number of channels: 8"
   而下面只列了 2 行，是正常的，不是文件被截断。
2. 第二列 `chan->dbg_client_name` 的内容是
   `"<申请者 dev_name>:<dma-names 里的名字>"`（`:857-858` 的
   `kasprintf(GFP_KERNEL, "%s:%s", dev_name(dev), name)`）。
   它在 `#ifdef CONFIG_DEBUG_FS` 里（`:856`）—— 关掉 DEBUG_FS
   时字段不填，`?:` 会退化成固定字符串 `"in-use"`。
3. `:93-95` 的 `(via router: ...)` 后缀在 arm64 上**永远不会出现**
   （arm64 没有 router，见 `06-devicetree.md` §5）。

### §4.3 `summary` 能回答和不能回答的问题

| 问题 | 能吗 |
|---|---|
| 系统里有几个 DMA 控制器？各几条通道？ | ✅ 头一行 |
| 哪条通道被哪个外设占着？ | ✅ 第二列（需要 `CONFIG_DEBUG_FS`） |
| 空闲通道还剩几条？ | ⚠️ 只能用 `chancnt` 减去列出来的行数 |
| 这条通道现在正在传第几个描述符？ | ❌ | 
| 传输速率 / 延迟 / 出错次数？ | ❌ 完全没有 |
| 某条通道的 `dma_slave_config` 是什么？ | ❌ 核心不存也不显示 |

后三行是这个子系统可观测性的真实边界。要这些数据只有两条路：
自己在驱动里加 debugfs 文件，或者用 §7 的替代方案。

---

## §5 用户态工具：`dmatest`

这是 dmaengine 唯一自带的测试客户端，也是**验证一个新写的控制器驱动
最省事的办法**。代码 `drivers/dma/dmatest.c`（1360 行），
官方文档 `Documentation/driver-api/dmaengine/dmatest.rst`（232 行）。

### §5.1 先说清它测不了什么

| 能测 | 不能测 |
|---|---|
| `DMA_MEMCPY` | **`DMA_SLAVE`（外设 DMA）** |
| `DMA_MEMSET` | `DMA_CYCLIC`（音频常用） |
| `DMA_XOR` | `DMA_INTERLEAVE` |
| `DMA_PQ` | 任何和真实外设握手的路径 |

**这不是"暂未支持"，是机制上做不到**，有两个独立的原因：

一、dmatest 只申请这 4 种能力的通道 —— `dmatest.c` 里一共只有 4 次
`request_channels`：`:1111`（`DMA_MEMCPY`）、`:1112`（`DMA_MEMSET`）、
`:1113`（`DMA_XOR`）、`:1114`（`DMA_PQ`）。对应的线程创建也只按这 4 类
分派（`:1029`/`:1036`/`:1043`/`:1047` 的 `dma_has_cap` 检查，
线程函数在 `:613`/`:617`/`:622`/`:628`）。

二、更根本的是它走的是 `dma_request_channel(mask, filter, params)`
（`dmatest.c:1077`）这条**匿名申请**路径，而核心在
`dma_channel_table_init` 里就把三个能力位从全局 mask 里抹掉了：

```c
/* dmaengine.c:273 */ dma_cap_clear(DMA_INTERRUPT, dma_cap_mask_all);
/* dmaengine.c:274 */ dma_cap_clear(DMA_PRIVATE,   dma_cap_mask_all);
/* dmaengine.c:275 */ dma_cap_clear(DMA_SLAVE,     dma_cap_mask_all);
```

**`DMA_SLAVE` 在 `:275` 被清掉** —— slave 通道必须靠 DTS 的
`dmas`/`dma-names` 由具体消费者按名字要（`06-devicetree.md` §3），
匿名 mask 申请永远拿不到。

**arm64 上绝大多数 DMA 用途（UART/SPI/I2C/MMC/音频）都是 slave。
所以 `dmatest` 全绿 ≠ 你的 slave 路径没问题。** 官方文档
`Documentation/driver-api/dmaengine/dmatest.rst:19-22` 的 note
也把可测能力限定在这 4 项。

### §5.2 参数：注意 `module_param` 和 `module_param_cb` 是两种东西

`dmatest.c` 一共 15 个普通 `module_param`（`:25`、`:29`（`module_param_string`）、
`:33`、`:38`、`:43`、`:48`、`:53`、`:58`、`:63`、`:68`、`:72`、`:76`、`:80`、
`:84`、`:88`）+ 4 个带 setter/getter 的 `module_param_cb`。

常用的普通参数：

| 参数（= sysfs 文件名） | 默认值 | 含义 |
|---|---|---|
| `iterations` | 0 | 每个线程跑多少轮，0 = 无限 |
| `test_buf_size` | 16384 | 缓冲区大小。**注意文件名不是 `bufsize`** |
| `threads_per_chan` | 1 | 每条通道起几个线程 |
| `timeout` | 3000 | 单次传输的等待上限（ms） |
| `noverify` | false | 只测吞吐、不比对数据（**这不是故障注入**） |
| `polled` | false | 用轮询代替中断完成检测 |
| `max_channels` | 0 | 最多用几条通道，0 = 不限 |
| `alignment` | -1 | 强制对齐，-1 = 用驱动上报的能力 |
| `device` | 空 | 变量叫 `test_device`，但 sysfs 文件名是 `device`（`module_param_string`，`:29`） |

`device` 那一行是这一批参数里唯一名字和变量不同的 ——
`module_param_string(device, test_device, ...)` 的第一个参数才是对外名字。
其余参数的 sysfs 文件名就是变量名，所以缓冲区大小要写
`test_buf_size` 而不是 `bufsize`。

四个 `module_param_cb`：

| 参数 | 权限 | ops | 语义 |
|---|---|---|---|
| `run` | `0644`（`:156`） | `run_ops` `:151-155` | **写它是一个动作，不是存一个状态**：`dmatest_run_set`（`:1179`）会真的去起/停线程 |
| `channel` | `0644`（`:171`） | `multi_chan_ops` `:161` | 追加一条要测的通道 |
| `test_list` | **`0444`**（`:178`） | `test_list_ops` `:175` | 只读，读它列出当前挂了哪些通道/线程 |
| `wait` | **`0444`**（`:292`） | `wait_ops` `:288-291`，getter `:277` | 只读，读它会**阻塞到测试结束** |

**`run` 的 setter 语义是本节最重要的一条。** 它不是
"设置一个开关然后等下次生效"，而是 `echo 1 > run` 的瞬间就启动测试、
`echo 0 > run` 的瞬间就停。所以：
- **不要在测试跑着的时候改 `test_buf_size`/`iterations`** —— 改了不影响
  已经在跑的线程
- 正确次序永远是：先配所有普通参数 → 最后写 `run`

`test_list` 和 `wait` 是 `0444` —— sysfs 文件**根本没有写权限**，
`echo 1 > .../wait` 会被 VFS 直接拒掉（不是 dmatest 拒的）。
`wait` 是"读它会阻塞到测试结束"，不是"写它来开启等待"。

### §5.3 最小可用命令序列

两种用法，来自 `Documentation/driver-api/dmaengine/dmatest.rst`：

```bash
# 用法一：modprobe 时一把带上（rst:43）
modprobe dmatest channel=dma0chan0 timeout=2000 iterations=1 run=1

# 用法二：先配 sysfs 再启动（rst:47-51）
modprobe dmatest
echo dma0chan0 > /sys/module/dmatest/parameters/channel
echo 2000      > /sys/module/dmatest/parameters/timeout
echo 1         > /sys/module/dmatest/parameters/iterations
echo 1         > /sys/module/dmatest/parameters/run     # ← 这一步才真的跑
```

内建（非模块）时用内核命令行的 `dmatest.<参数>=<值>` 形式（`rst:55`）。
多通道用法从 `rst:57` 起（反复写 `channel` 追加）。
结果和进度：`dmesg` 里会有 "Added N threads using dma0chanX" 一类的行
（`rst:82`），结果格式说明在 `rst:127`，通道分配的规则在 `rst:150` 起的 Part 5。

本篇不贴示例输出（见篇首说明）。

### §5.4 两个容易踩的坑

1. **`late_initcall` 的次序**：内建时 `dmatest_init` 挂在
   `late_initcall`（`dmatest.c:1347`）。如果你的控制器驱动是模块，
   dmatest 初始化时通道还不存在 → 什么都不会测，也不报错。
   **对策：把 dmatest 编成模块（`=m`），手动 `modprobe`。**
2. **`select DMA_ENGINE_RAID` 的副作用**：见 §1.1 的 。
   开了 dmatest 的内核和产品内核在 async_tx 能力判定上不完全等价。

---

## §6 错误码分诊表

拿到一个错误码想反查"核心在哪一步拒绝了我"。下表按**语义**分组，
不是按行号罗列 —— 同一个码在 `dmaengine.c` 里出现十几次，但意思只有两三种。

| 错误码 | 出现次数 | 语义分组 | 具体位置与含义 |
|---|---|---|---|
| `-EIO` | 12 | **全部是"驱动注册期违反契约"** | `:1148`（没给 `dev`）+ §2.2 的 9 个能力 gate + `:1219`（缺 `tx_status`）+ `:1225`（缺 `issue_pending`）。**看到它就去改控制器驱动，和 DTS/消费者无关** |
| `-ENODEV` | 12 | 三种含义，要看来路 | ① sysfs 读：`:177`/`:199`/`:217`（通道正在注销）② 通道申请：`:674`（`find_candidate` 一个候选都没有）、`:887`/`:895`/`:947`（`dma_chan_get` 失败）、`:459`/`:463`（模块引用拿不到/`device_alloc_chan_resources` 失败）③ 注册期：`:1143`（`!device`）、`:1260`/`:1265`（`err_out` 路径） |
| `-EPROBE_DEFER` | — | **DT 路径的兜底码，信息量最低** | `of-dma.c:294`。它会**覆盖掉 xlate 返回 NULL 的一切真实原因**（`06-devicetree.md` §4 ）。看到它**不要**去查依赖顺序，先按 `06-devicetree.md` §7.3 的决策树走 |
| `-ENOMEM` | 5 | 纯内存不足 | `:280`（channel table）、`:1046`/`:1049`（per-cpu + `chan->dev`）、`:1350`、`:1472` |
| `-EINVAL` | 5 | 参数非法 | `:576`（`dma_get_slave_caps` 收到 NULL）、`:1513`（metadata 模式冲突）、`:1525`/`:1544`/`:1563`（metadata 三个接口的参数校验）。另外 `of-dma.c:124`/`:194` 是提供者注册参数错 |
| `-ENOTSUPP` | 4 | **"这个驱动没实现"，不是"参数错"** | `:1511`（metadata 模式驱动不支持）、`:1532`/`:1551`/`:1570`（三个 metadata 接口没实现）。**只出现在 metadata 路径上**，见 `04-extensions.md` |
| `-ENXIO` | 1 | 能力不匹配 | `:583`：`dma_get_slave_caps` 发现这条通道既不是 slave 也不是 cyclic |
| `-ENOSYS` | — | ⚠️ 见 99 §6 | `dmaengine_terminate_*` 在驱动没实现时的返回值与文档不一致 |
| `-EBUSY` | — | **不是返回值** | `:1588` 的 `-EBUSY` 是 `while (tx->cookie == -EBUSY)` 的**循环条件**，表示"描述符还没提交"。不要在 `dmaengine.c` 里找"返回 -EBUSY 的地方" |

复现整张表的命令：

```bash
/usr/bin/grep -n 'return -E\|= -E\|ERR_PTR(-E' drivers/dma/dmaengine.c
/usr/bin/grep -n 'ERR_PTR(-E' drivers/dma/of-dma.c
```

分诊的第一个动作应该是**判断错误码来自哪一层**：
- `dma_request_chan` 返回的 `-EPROBE_DEFER`/`-ENODEV` → DT 或通道占用问题（转 `06-devicetree.md`）
- `dma_async_device_register` 返回的 `-EIO` → 你的控制器驱动漏了回调（§2.2）
- `dmaengine_prep_*` 返回 NULL → **核心不产生错误码**，是控制器驱动内部拒绝（只能看驱动自己的日志）

### §6.1 一个没有错误码的故障：`residue` 读出来一直是 0

`dmaengine_tx_status`（`include/linux/dmaengine.h:1207-1209`）不报错，
但 `state.residue` 是 0 —— 这是音频/串口 DMA 里最常见的"无声故障"。
**三种原因，症状完全一样**，按这个顺序排除：

| # | 原因 | 怎么确认 |
|---|---|---|
| 1 | **控制器驱动的 `tx_status` 只调了 `dma_cookie_status`，没调 `dma_set_residue`** | `dma_cookie_status`（`drivers/dma/dmaengine.h:68-83`）**无条件把 `state->residue` 写成 0**（`:79`）。驱动必须在它之后自己调 `dma_set_residue`（`:85-88`）覆盖。查法：`/usr/bin/grep -n 'dma_set_residue' <你的控制器驱动>.c`，空 = 就是这个原因 |
| 2 | 硬件粒度就是"报不了" | 驱动声明的是 `DMA_RESIDUE_GRANULARITY_DESCRIPTOR`（`include/linux/dmaengine.h:454`）。kerneldoc 原话就是 "residue reporting is not supported … will always be 0"。查法：`dma_get_slave_caps` 读回来看 `residue_granularity` |
| 3 | 查的时机不对 | `SEGMENT` 粒度（`:458`）**只在每段/每个 period 完成时更新**；两次中断之间读到的是同一个值。cyclic 传输尤其明显 |

`residue_granularity` 是能力，不是配置 —— 由控制器驱动填死
（53 个驱动填了它），消费者只能读、不能要求。读的正路是
`dma_get_slave_caps`（`dmaengine.c:571-617`）；
注意它对非 slave / 非 cyclic 的通道直接返回 `-ENXIO`（`:582-583`）。

**判据一句话**：`residue` 恒 0 且驱动里搜不到 `dma_set_residue`
→ 是**驱动没实现**，不是你的用法错。核心在这条路上不打任何日志、不返回错误码。

---

## §7 tracepoint：核心一个都没有

### §7.1 先自己复现一遍

```bash
/usr/bin/grep -rn 'TRACE_EVENT\|DECLARE_EVENT_CLASS\|trace_' drivers/dma/ \
        --include=*.c --include=*.h                      # 3 行，全在 tegra20-apb-dma.c
ls include/trace/events/ | grep -i dma                   # 见下方说明
ls /sys/kernel/tracing/events/ | grep -i dma             # 运行时没有 dmaengine 目录
```

第一条命令的 3 行输出**全部**来自同一个驱动：
`drivers/dma/tegra20-apb-dma.c:657`（`trace_tegra_dma_complete_cb`）、
`:674`（`trace_tegra_dma_isr`）、`:939`（`trace_tegra_dma_tx_status`）。
事件定义在 `include/trace/events/tegra_apb_dma.h`（61 行）。
所以准确的说法是：

| 范围 | 有没有 tracepoint |
|---|---|
| 核心（`dmaengine.c` / `of-dma.c` / `virt-dma.c` / `include/linux/dmaengine.h`） | **零** |
| 147 个控制器驱动 | 只有 **1 个**（tegra20-apb-dma），3 个调用点 |
| arm64 上实际能用到的 | **零** —— 该驱动 `depends on ARCH_TEGRA \|\| COMPILE_TEST`（`drivers/dma/Kconfig:656`），四个 compatible 全是 Tegra20/30/114/148（`drivers/dma/tegra20-apb-dma.c:1663`/`:1666`/`:1669`/`:1672`），`arch/arm64/boot/dts/` 下没有任何节点用它们 |

`ls include/trace/events/ | grep -i dma` 会列出 4 个文件，**只有 1 个属于本子系统**：
`dma_fence.h`（drm 的）、`rdma_core.h` 和 `rpcrdma.h`（RDMA 网络的）、
以及 `tegra_apb_dma.h`（上面那个驱动私有的）。

对比一下同类子系统：clk 有 `include/trace/events/clk.h` 一整套事件，
可以 trace 每次 `set_rate`/`enable`；pinctrl、regulator 也都有。
**dmaengine 是这一批框架里核心唯一零 tracepoint 的。**

这条已记入 `99-evidence.md` §6 作为观测能力缺口。

### §7.2 后果：这些问题在本子系统里问不出答案

| 想知道 | 有 tracepoint 的话 | 现在的处境 |
|---|---|---|
| 一笔传输从 submit 到 complete 花了多久 | 两个事件相减 | ❌ 只能自己在驱动里加计时 |
| 某条通道的描述符队列积压多少 | 队列事件计数 | ❌ 核心不统计（`virt-dma.c` 的三条链表纯内部） |
| 哪个消费者把通道占满了 | 申请/释放事件 | ⚠️ 只能看 debugfs 的静态快照（§4） |
| `issue_pending` 到中断的延迟 | 两个事件相减 | ❌ 没有 |

### §7.3 替代方案（按代价从低到高）

1. **dynamic debug**（代价最低，先用这个）——
   `drivers/dma/virt-dma.c` 里有 2 条 `dev_dbg`：`:32`（描述符 submitted）
   和 `:59`（描述符 freeing）。**用 virt-dma 框架的驱动（arm64 上大多数都是）
   打开它就能看到每个描述符的提交/回收**：
   ```bash
   echo 'file drivers/dma/virt-dma.c +p' > /sys/kernel/debug/dynamic_debug/control
   ```
   这是目前最接近"传输时间线"的手段。
2. **sysfs 计数器**（§3.2）—— 只对 memcpy 有效。
3. **`dmatest`**（§5）—— 只能测 4 种能力，测不了 slave。
4. **kprobe / ftrace function tracer** —— 不需要改内核：
   ```bash
   echo 'p:dma_prep dmaengine_prep_slave_sg' > /sys/kernel/tracing/kprobe_events
   ```
   注意 `dmaengine_prep_slave_sg` 是 `static inline`
   （在 `include/linux/dmaengine.h` 里），**kprobe 挂不上**。
   能挂的是驱动里真实存在的函数符号（如 `pl330_prep_slave_sg`），
   先 `grep <驱动>_prep_slave_sg` 确认名字，再从
   `/proc/kallsyms` 确认它没被内联掉。
5. **在控制器驱动里加自己的 tracepoint** —— 代价最高但唯一可靠。
   树里已经有一份现成范本可以照抄：
   `include/trace/events/tegra_apb_dma.h`（61 行）+
   `drivers/dma/tegra20-apb-dma.c` 的三个调用点。

---

## §8 篇末一页速查

### §8.1 三条命令起手式

```bash
# 1) 有哪些控制器、几条通道、谁在占
cat /sys/kernel/debug/dmaengine/summary

# 2) 我这个外设到底拿到通道了没（不需要 DEBUG_FS）
ls -l /sys/devices/platform/<你的外设>/ | grep 'dma:'

# 3) 通道申请失败的真正原因（核心默认藏在 dev_dbg 里）
echo 'file drivers/dma/dmaengine.c +p' > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/dma/of-dma.c   +p' > /sys/kernel/debug/dynamic_debug/control
```

### §8.2 按症状选章节

| 症状 | 先看 |
|---|---|
| 控制器 probe 失败，`dmesg` 有 `"Device claims capability ..."` | §2.2 |
| 消费者 `dma_request_chan` 返回 `-EPROBE_DEFER` | §6 那一行 + `06-devicetree.md` §7.3 |
| 消费者 `dma_request_chan` 返回 `-ENODEV` | §2.3（开 dynamic debug 看真因） |
| `summary` 里看不到我的控制器 | 注册没成功 → §2.2 |
| `summary` 头一行说 8 条通道，下面只列 2 行 | 正常，§4.2 第 1 条 |
| `bytes_transferred` 一直是 0 | 正常（slave 路径不累加），§3.2 |
| unbind 驱动时机器挂了 / 内存踩坏 | §2.1 的 （`:1229` 那条 `dev_dbg` 说的就是这个） |
| `residue` 读出来一直是 0 | §6.1（先 grep 驱动有没有 `dma_set_residue`） |
| 想量传输延迟 | §7.3，先用 `virt-dma.c` 的 dynamic debug |
| 新写的控制器驱动想自测 | §5，但要记住测不了 slave（§5.1 ） |

---

## §9 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 这些日志/计数器背后的结构体字段是什么 | `02-datastructures.md` |
| `dev_dbg` 那些分支在主流程的哪一步 | `03-internals.md` Part A |
| `dbg_summary_show` / metadata 这类可选扩展 | `04-extensions.md` |
| 怎么写一个不会触发 §2.2 那些报错的控制器驱动 | `05-usage.md` §2 |
| DTS 配错导致的静默失效怎么查 | `06-devicetree.md` §7 |
| 为什么这个子系统的可观测性这么薄 | `08-feynman.md` |
| 本篇每条论断的出处和复现命令 | `99-evidence.md` §3 |
