---
title: 调试方法
---

# 07 调试：mailbox 的可观测性有多差，以及怎么绕

> **本篇的组织轴：按"你能拿到什么输出"排，而不是按机制分类。**
> 因为 mailbox 的调试有一个压倒一切的前提 ——
>
> **mailbox 核心几乎没有可观测性**：零 tracepoint、零 sysfs、零 debugfs、
> 全部日志只有 **11 处**（`03-internals.md` §C1），其中跟"申请通道失败"有关的
> 四个出口里三个是 debug 级、一个连日志都没有。
>
> ⇒ 所以本篇 §1–§2 先讲"把默认看不见的东西打开"，
> §3 讲"核心没有 tracepoint 时用什么替代"，
> §4–§5 讲唯一的两个真实观测面（`mailbox-test` 的 debugfs、几个控制器自建的 debugfs），
> §6 才是错误码分诊表。
>
> 格式约定同前：`mailbox.c` 指 `drivers/mailbox/mailbox.c`。
> arm64-only，ACPI PCC 已裁掉。本篇所有命令**未在真机上跑过**，
> 输出格式来自源码推导 —— 见 `99-evidence.md` §5。

---

## §0 分诊入口：按症状选章节

| 症状 | 先做什么 | 去哪节 |
|---|---|---|
| 消费者 probe 失败，`dmesg` 里什么都没有 | **先开 dynamic debug** | §2.3 |
| 消费者报 `-EPROBE_DEFER`、一直不起来 | 看 `devices_deferred` | §6.2、`06-devicetree.md` §8.2 |
| `mbox_send_message` 返回 `-ETIME` | 分清"没发出去"还是"没回来" | §6.3 |
| `dmesg` 里有 `Client can't run the TX ticker` | txdone 模式配错了 | §6.4 |
| `dmesg` 里有 `Try increasing MBOX_TX_QUEUE_LEN` | 队列打满了 | §6.5 |
| 上层框架（SCMI/BPMP/RTKit）报超时，mailbox 侧无任何日志 | 大概率是 DTS 方向写反 | §7.2、`06-devicetree.md` §5.3 |
| 想验证一个新写的控制器驱动 | 用 `mailbox-test` | §4 |
| 想在核心的关键路径上打点 | 核心零 tracepoint，只能 kprobe | §3 |
| 想看某条通道现在是不是被占着 | **没有任何接口** | §5.3 |

---

## §1 编译期：三道门 + 一个"没有的开关"

### §1.1 Kconfig 全景

`drivers/mailbox/Kconfig`（297 行）的结构是一个 `menuconfig` + 27 个控制器 config：

```
menuconfig MAILBOX            # Kconfig:2-7   bool  "Mailbox Hardware Support"
  if MAILBOX                  # :9
      config APPLE_MAILBOX    # :11
      config ARM_MHU          # :23
      …
      config MAILBOX_TEST     # :167  唯一的调试用 config
      …
  endif
```

三点值得注意：

| 观察 | 依据 | 含义 |
|---|---|---|
| `MAILBOX` 是 **`bool` 而不是 `tristate`** | `Kconfig:3` | 核心不能编成模块；控制器驱动可以（27 个里绝大多数是 `tristate`） |
| **没有 `CONFIG_MAILBOX_DEBUG` 之类的调试开关** | 全文件 `grep -i debug` 只命中 `MAILBOX_TEST` 的帮助文本 | ⇒ **本子系统没有"编译期打开更多检查"这条路** |
| `menuconfig` 的帮助文本只有 3 行 | `:4-7` | 官方定位见 `Documentation/driver-api/mailbox.rst` |

和 gpio 有 `CONFIG_DEBUG_GPIO`、clk 有 `CONFIG_DEBUG_FS` 下的完整 clk_summary 不同，
**mailbox 一个调试专用 Kconfig 都没有**。这条要记进 `99-evidence.md` §6。

### §1.2 唯一的调试 config：`MAILBOX_TEST`

```
config MAILBOX_TEST              # drivers/mailbox/Kconfig:167
	tristate "Mailbox Test Client"    # :168
	depends on OF                     # :169
	depends on HAS_IOMEM              # :170
	help
	  Test client to help with testing new Controller driver implementations.
```

⇒ **三道门**：`CONFIG_MAILBOX=y` → `CONFIG_MAILBOX_TEST=y/m` → **DTS 里有 `mailbox-test` 节点**。
第三道最容易漏：`04-extensions.md` §1.5 已确认**全树零 in-tree DTS 实例**，
必须自己加节点或 overlay（写法见 `06-devicetree.md` §6.3）。

### §1.3 arm64 上真正会被编出来的控制器

`Kconfig` 里 27 个控制器，按 `depends on` 过滤 arm64（忽略 `COMPILE_TEST`，
那只是给编译覆盖用的，不代表能跑）：

| Kconfig | 行 | `depends on` | arm64 平台 |
|---|---|---|---|
| `APPLE_MAILBOX` | `:11-13` | `ARCH_APPLE` | ✅ Apple Silicon（`default ARCH_APPLE`，`:14`） |
| `ARM_MHU` / `ARM_MHU_V2` | `:23`/`:31` | `ARM_AMBA` | ✅ Juno 等 ARM 参考板 |
| `IMX_MBOX` | `:38-40` | `ARCH_MXC` | ✅ i.MX8 |
| `OMAP2PLUS_MBOX` | `:74-76` | `ARCH_OMAP2PLUS \|\| ARCH_K3` | ✅ `ARCH_K3` 那半是 arm64（AM65x/AM64x） |
| `ROCKCHIP_MBOX` | `:92-94` | `ARCH_ROCKCHIP` | ✅ |
| `TI_MESSAGE_MANAGER` | `:136-138` | `ARCH_KEYSTONE \|\| ARCH_K3` | ✅ |
| `HI3660_MBOX` / `HI6220_MBOX` | `:147`/`:157` | `ARCH_HISI` | ✅ |
| `QCOM_APCS_IPC` / `QCOM_IPCC` | `:187`/`:287` | `ARCH_QCOM` | ✅ |
| `TEGRA_HSP_MBOX` | `:195-197` | `ARCH_TEGRA` | ✅ Tegra186/194/234 |
| `XGENE_SLIMPRO_MBOX` | `:204-206` | `ARCH_XGENE` | ✅ |
| `BCM_FLEXRM_MBOX` | `:222-225` | **`depends on ARM64`** + `ARCH_BCM_IPROC` | ✅ **唯一显式写 `ARM64` 的** |
| `BCM_PDC_MBOX` | `:214-216` | `ARCH_BCM_IPROC` | ✅ |
| `MTK_ADSP_MBOX` / `MTK_CMDQ_MBOX` | `:241`/`:250` | `ARCH_MEDIATEK` | ✅ |
| `ZYNQMP_IPI_MBOX` | `:260-262` | `ARCH_ZYNQMP && OF` | ✅ |
| `SUN6I_MSGBOX` | `:270-272` | `ARCH_SUNXI` | ✅ |
| `SPRD_MBOX` | `:279-281` | `ARCH_SPRD` | ✅ |
| `POLARFIRE_SOC_MAILBOX` | `:175-178` | `SOC_MICROCHIP_POLARFIRE` | ❌ RISC-V |
| `PCC` | `:101-103` | `ACPI` | ❌ **已裁**（`99-evidence.md` §2.7） |
| `STM32_IPCC` | `:233-235` | `MACH_STM32MP157` | ❌ arm32 |
| `ALTERA_MBOX` / `PL320_MBOX` / `PLATFORM_MHU` / `BCM2835_MBOX` / `STI_MBOX` / `ARMADA_37XX_RWTM_MBOX` | — | 见各自 `depends` | ⚠️ 多为 arm32 或 nios2 |

调试含义：**"我的 mailbox 驱动没起来"第一个要查的是 `.config` 里对应的 config 有没有开**，
因为 `depends on ARCH_*` 会让一个看起来存在的驱动在别的 defconfig 上根本不存在。

```bash
zcat /proc/config.gz | /usr/bin/grep -i 'MAILBOX\|MBOX\|MHU\|IPCC\|MSGBOX'
# 或
/usr/bin/grep -i 'MAILBOX\|MBOX' /boot/config-$(uname -r)
```

---

## §2 日志：核心只有 11 处，可见性分三档

### §2.1 全部 11 处日志的可见性等级

先说一个和其他子系统不同的结论：**核心里零 `_once` / 零 `_ratelimited`**
（`/usr/bin/grep -n '_once\|_ratelimited' drivers/mailbox/mailbox.c` 空输出）。
所以没有"第一次打、后面吞掉"这种陷阱 —— **每次触发都会打**。

| 行 | 消息 | 宏 | 默认可见？ | 触发条件 |
|---|---|---|---|---|
| `:179-180` | `Controller can't run the TX ticker` | `dev_err` | ✅ | 控制器调 `mbox_chan_txdone` 但没设 `txdone_irq` |
| `:200` | `Client can't run the TX ticker` | `dev_err` | ✅ | 客户端调 `mbox_client_txdone` 但通道不是 ACK 模式 |
| `:265` | `Try increasing MBOX_TX_QUEUE_LEN` | `dev_err` | ✅ | 发送队列满（20 条） |
| `:347` | `<func>: No owner device node` | `pr_debug` | ❌ | 消费者不是 DT 设备 |
| `:356` | `<func>: can't parse "mboxes" property` | `dev_dbg` | ❌ | `mboxes` 解析失败 |
| `:377` | `<func>: mailbox not free` | `dev_dbg` | ❌ | 通道被占（`-EBUSY`） |
| `:398` | `Unable to startup the chan (<err>)` | `dev_err` | ✅ | 控制器 `startup` 失败 |
| `:418` | `<func>() currently only supports DT` | `dev_err` | ✅ | `_byname` 遇到非 DT 设备 |
| `:423-424` | `<func>() requires an "mbox-names" property` | `dev_err` | ✅ | DTS 缺 `mbox-names` |
| `:434-435` | `<func>() could not locate channel named "<name>"` | `dev_err` | ✅ | 名字对不上 |
| `:503` | `last_tx_done method is absent` | `dev_err` | ✅ | 控制器设了 `txdone_poll` 却没实现 `last_tx_done` |

外加两个 `WARN_ON`（栈回溯，默认可见）：

| 行 | 条件 | 什么情况 |
|---|---|---|
| `:569` | `WARN_ON(!mbox \|\| !*mbox)` | `devm` 版 unregister 的 devres release 拿到空指针 |
| `:623` | `WARN_ON(devres_release(...))` | 手动调 `devm_mbox_controller_unregister` 时找不到对应 devres |

**三条 debug 级的分布不是随机的**：它们全部集中在
`mbox_request_channel`（`:337-406`）里，而这个函数正是 DTS 出错的唯一入口。
⇒ **DTS 配错时默认什么也看不到**，这是本子系统最大的可观测性缺陷。
`06-devicetree.md` §8 整节都在处理这个后果。

### §2.2 `dev_*` vs `pr_*`：`:347` 那一处的差别

注意 `:347` 用的是 `pr_debug` 而不是 `dev_dbg`：

```c
	if (!dev || !dev->of_node) {
		pr_debug("%s: No owner device node\n", __func__);   /* :347 */
		return ERR_PTR(-ENODEV);
	}
```

原因很直白：**这一行的前提就是"`dev` 可能是 NULL"**，所以不能用 `dev_dbg`。
但它带来两个后果：

1. **日志里没有设备名** —— 只有 `mbox_request_channel: No owner device node`，
   多个消费者同时出问题时无法区分是谁。
2. **dynamic debug 的过滤条件不一样**：`pr_debug` 只能按
   `file`/`line`/`func`/`module` 过滤，**不能按 `dev` 过滤**。

### §2.3 把三条 debug 日志打开（本篇最关键的一条操作）

```bash
# 前提：CONFIG_DYNAMIC_DEBUG=y（绝大多数发行版内核都开）
mount -t debugfs none /sys/kernel/debug 2>/dev/null

# 整个文件全开（推荐，只有 3 条，不会淹）
echo 'file drivers/mailbox/mailbox.c +p' > /sys/kernel/debug/dynamic_debug/control

# 想带上文件名/行号/函数名前缀（排查时很有用）：
echo 'file drivers/mailbox/mailbox.c +pflt' > /sys/kernel/debug/dynamic_debug/control
#   p=打开  f=函数名  l=行号  t=线程 ID  m=模块名

# 确认真的生效了（应看到 3 行，第三列是 =pflt 而不是 =_）
/usr/bin/grep 'drivers/mailbox/mailbox.c' /sys/kernel/debug/dynamic_debug/control
```

也可以在**启动参数**里开，用来抓 probe 早期的失败（这类问题最常见）：

```
dyndbg="file drivers/mailbox/mailbox.c +p"      # 核心编进内核时
mailbox.dyndbg=+p                               # ⚠️ 无效 —— MAILBOX 是 bool，不是模块
```

第二行**不要用**：`Kconfig:3` 的 `MAILBOX` 是 `bool`，
核心永远在 vmlinux 里，没有 `mailbox` 这个模块名。
用第一行的 `dyndbg=` 全局形式。

打开之后重新触发一次消费者 probe：

```bash
D=/sys/bus/platform/drivers/<消费者驱动名>
echo <设备名> > $D/unbind && echo <设备名> > $D/bind
dmesg | tail -40
```

### §2.4 打开了还是一条都没有 ⇒ 走的是"零日志"出口

这是 mailbox 调试里最容易卡死的一种情况。
`mbox_request_channel` 有**四**个失败出口，只有三个打日志；
第四个（`mailbox.c:371-374`）什么都不打：

```c
	if (IS_ERR(chan)) {
		mutex_unlock(&con_mutex);
		return chan;              /* :373  零日志 */
	}
```

它覆盖两种完全不同的情形（`06-devicetree.md` §3.3 有完整分析）：

| 实际返回码 | 什么原因 | 怎么区分 |
|---|---|---|
| `-EPROBE_DEFER` | phandle 指的节点还没注册 mailbox 控制器（**或永远不会注册**） | 查 `/sys/kernel/debug/devices_deferred` |
| 控制器 `of_xlate` 自己的错误码 | cell 个数或值被驱动拒了 | 只能上 kprobe，见 §3.2 |

---

## §3 tracepoint：一个都没有，以及怎么补

### §3.1 零 tracepoint 的取证

```
/usr/bin/grep -rn 'trace_\|TRACE_EVENT\|DEFINE_EVENT' \
    drivers/mailbox/ include/linux/mailbox*.h include/linux/mailbox/
   → 零输出

ls include/trace/events/ | /usr/bin/grep -i 'mbox\|mailbox'
   → 零输出
```

⇒ **整个 `drivers/mailbox/` 目录（32 个 `.c`、17594 行）没有一处 tracepoint**，
`include/trace/events/` 下也没有 mailbox 的事件定义文件。

这个断言比其他子系统更"干净"：dma 那套里核心零 tracepoint 但有一个驱动
（`tegra20-apb-dma.c`）自带（`output/dmaengine/99-evidence.md` §4）；
**mailbox 连驱动侧的例外都没有**。

对比一下量级：

| 子系统 | tracepoint 数 | 出处 |
|---|---|---|
| clk | 24 | `include/trace/events/clk.h` |
| regulator | 11 | `include/trace/events/regulator.h` |
| i2c | 4 | `include/trace/events/i2c.h` |
| **mailbox** | **0** | — |

⇒ 后果：**没有任何办法在不改内核的前提下拿到"一次收发的时间线"**。
下面三种替代手段都有各自的代价。

### §3.2 替代一：kprobe / function tracer（最实用）

核心的 13 个导出符号全是普通的非 inline 函数，都能挂 kprobe。优先挂这五个：

| 符号 | 挂它能看到什么 | 备注 |
|---|---|---|
| `mbox_request_channel` | **返回值**——这是拿到"零日志出口"真实错误码的唯一办法 | 见 §2.4 |
| `mbox_send_message` | 每次发送 + 返回的队列下标令牌 | 返回值 ≥0 是令牌不是"成功"（`03-internals.md` §A5.2） |
| `mbox_chan_txdone` | 控制器什么时候声明发完了 | 只在 IRQ 模式下会被调 |
| `mbox_client_txdone` | 客户端什么时候声明发完了 | 只在 ACK 模式下 |
| `mbox_free_channel` | 谁在什么时候释放了通道 | 排查"释放后又用"时必挂 |

**function_graph 看调用关系与耗时**：

```bash
cd /sys/kernel/debug/tracing
echo 0 > tracing_on
echo function_graph > current_tracer
echo 'mbox_*' > set_ftrace_filter          # 通配符一次挂全部
echo 1 > tracing_on
# …触发一次收发…
echo 0 > tracing_on
cat trace | head -60
```

**kretprobe 拿返回值**（`mbox_request_channel` 的返回值是本篇最想要的东西）：

```bash
cd /sys/kernel/debug/tracing
echo 'r:mbox_req mbox_request_channel ret=$retval:x64' > kprobe_events
echo 1 > events/kprobes/mbox_req/enable
echo 1 > tracing_on
# 触发消费者 probe，然后
cat trace
echo 0 > events/kprobes/mbox_req/enable
echo > kprobe_events
```

判读：返回值是 `ERR_PTR`，所以看到的是接近 `0xffffffffffffff??` 的值。
换算：`-EPROBE_DEFER = -517 = 0xfffffffffffffdfb`、
`-EINVAL = -22 = 0xffffffffffffffea`、`-EBUSY = -16 = 0xfffffffffffffff0`、
`-ENODEV = -19 = 0xffffffffffffffed`。
更省事的是 `bpftrace`：

```bash
bpftrace -e 'kretprobe:mbox_request_channel { printf("%s -> %ld\n", comm, retval); }'
```

一个必须注意的坑：**`mbox_request_channel` 的核心逻辑整段在 `con_mutex` 里**
（`mailbox.c:351-404`，`02-datastructures.md` §8.1），
而 `startup` 回调也在这把锁内（`:394`）。
⇒ **在 kprobe handler 里做重活会拉长全局临界区**，
让"通道申请"这一步在多消费者并发时变慢。只打印，不要做别的。

### §3.3 替代二：`msg_submit` / `tx_tick` 这些静态函数

核心里真正的状态机是三个 **`static`** 函数（`03-internals.md` §A5、§A6）：

| 静态函数 | 行 | 为什么想看它 |
|---|---|---|
| `add_to_rbuf` | `mailbox.c:26-51` | 队列有多满 |
| `msg_submit` | `:53-95` | 真正调 `send_data` 的地方，在 `chan->lock` 里 |
| `tx_tick` | `:97-115` | 唯一会 `complete()` 和调 `tx_done` 的地方 |

它们是 `static`，但 **arm64 上默认没有被内联掉**，
所以通常仍能在 `/proc/kallsyms` 里找到、能挂 kprobe：

```bash
/usr/bin/grep -w 'msg_submit\|tx_tick\|add_to_rbuf' /proc/kallsyms
```

如果 grep 不到（编译器内联了、或开了 `CONFIG_LTO`），这条路就断了 ——
**这是"零 tracepoint"最难绕的一部分**：核心的状态机没有任何稳定的观测点。
记进 `99-evidence.md` §5（观测缺口）。

### §3.4 替代三：改控制器驱动打日志（最后手段）

因为源码树只读，本文档不给补丁。但要指出**打在哪一层最有信息量**：

| 打在哪 | 能看到 | 代价 |
|---|---|---|
| 控制器的 `send_data` 回调 | 每条消息的载荷 + 时刻 | **在 `chan->lock` spinlock 内**（`mailbox.c:77`），不能睡、不能大量打印 |
| 控制器的中断处理函数 | 对端什么时候响应了 | 中断上下文，同样受限 |
| 控制器的 `last_tx_done` | POLL 模式下每次轮询的结果 | 会被 hrtimer 高频调用，必须限流 |

已经有 6 个控制器驱动自带 `dev_dbg` 覆盖了这三层，**优先用现成的**：
`imx-mailbox.c`（最详细，`:155`/`:179`/`:193`/`:365`/`:385`/`:432`/`:435` 等十余处
+ `:381` 的 `print_hex_dump_debug` 直接打载荷）·
`apple-mailbox.c:132`/`:160`（`> TX` / `< RX` 一行一条，格式最适合抓时序）·
`stm32-ipcc.c:101`/`:131`/`:151` · `rockchip-mailbox.c:67`/`:145` ·
`bcm2835-mailbox.c:74`/`:87` · `mailbox-sti.c:181`/`:260`/`:266`/`:285`。
打开方式同 §2.3，把文件名换掉即可。

但要小心**限流会吞日志**：`sprd-mailbox.c:114`/`:127`/`:166` 和
`imx-mailbox.c:229`/`:307`/`:415`/`:511`/`:530` 用的是 `dev_warn_ratelimited`，
`bcm-pdc-mailbox.c:650`/`:654` 用 `dev_err_ratelimited`/`dev_info_ratelimited`。
⇒ **这几个驱动的日志条数不能当计数用**，只能当"发生过"用。

---

## §4 `mailbox-test`：唯一的端到端观测面

### §4.1 为什么它是本篇的重头

核心自己零 debugfs、零 sysfs（下节取证），
所以 **`mailbox-test` 是唯一能从用户态完整驱动一次收发的东西**。
`04-extensions.md` §1 讲了它的实现，本节只讲**怎么用**。

两个 debugfs 文件（`mailbox-test.c:263-281`）：

```
/sys/kernel/debug/<dev_name>/message   0600  读+写  mbox_test_message_ops :254-261
/sys/kernel/debug/<dev_name>/signal    0200  只写   mbox_test_signal_ops  :82-86
```

目录名是 `dev_name(&pdev->dev)`（`:269`），也就是 DT 节点的
`<地址>.<节点名>` 形式，**不是** `mailbox-test`。所以第一步永远是先找到它：

```bash
ls -d /sys/kernel/debug/*/message 2>/dev/null
# 例如 /sys/kernel/debug/1000c000.mailbox-test/message
```

`debugfs_create_dir` 失败只 `dev_err`（`:271`）不中止 probe ——
所以"probe 成功但没有 debugfs 目录"是可能的，检查 `CONFIG_DEBUG_FS`
和 `debugfs_initialized()`（`:266`）。

### §4.2 发一条消息：`message` vs `signal` 怎么选

分工的判据是**DTS 里有没有 `reg`（MMIO 共享内存）**
（`04-extensions.md` §1.3 有完整表）：

| 情形 | 怎么发 | 实际传给控制器的 `mssg` |
|---|---|---|
| DTS **没有** `reg` | 只写 `message` | 那 128 字节缓冲区的指针 |
| DTS **有** `reg`，且先写了 `signal` | 先写 `signal`，再写 `message` | 那 8 字节 signal；128 字节由 `tx_prepare` 搬进共享内存 |

```bash
T=/sys/kernel/debug/1000c000.mailbox-test        # 换成你自己的

# ① 无共享内存型（门铃 / 寄存器直传）
echo -n "hello" > $T/message

# ② 共享内存 + 门铃型 —— 顺序不能反
printf '\x01\x02\x03\x04' > $T/signal
echo -n "payload-goes-into-shared-memory" > $T/message
```

两个必须知道的行为，**源码里有、文档里一个字都没有**：

1. **`signal` 是一次性的**：`mbox_test_message_write:149-151` 发完就
   `kfree(tdev->signal)` 并置 NULL ⇒ **每发一条带 signal 的消息都要重写一次 `signal`**。
2. **长度上限会直接报错**：`signal` > 8 字节（`MBOX_MAX_SIG_LEN`，`:24`）时
   `:59-64` 打 `Signal length %zd greater than max allowed %d` 并返回 `-EINVAL`；
   而 `message` 超过 128 字节（`MBOX_MAX_MSG_LEN`，`:24`）**行为不同** ——
   见 `05-usage.md` §6.2。

另一条错误出口：通道不能发时 `:55-56` 打 `Channel cannot do Tx` + `-EINVAL`。
⇒ **`echo` 报 "Invalid argument" 时先分清是这两条里的哪一条**，看 `dmesg`。

### §4.3 收消息：`read` 的三种结果

`mbox_test_message_read`（`:170-240`）：

```bash
cat $T/message              # 阻塞，直到有消息
timeout 5 cat $T/message    # 加超时
dd if=$T/message bs=4096 count=1 iflag=nonblock   # 非阻塞
```

三种结果，判读方法完全不同：

| 输出 | 出处 | 含义 |
|---|---|---|
| `<NO RX CAPABILITY>` | `:186` | **DTS 只给了一条通道，没有 RX** —— 不是收不到，是压根没申请 RX |
| 一屏 hex dump（8 行 × 16 字节） | `:216-226` | ✅ 正常收到了 |
| 阻塞不返回 / `-EAGAIN` | `:200-203` | 没消息。`-EAGAIN` 只在 `O_NONBLOCK` 下出现 |

hex dump 的形状是**固定的**，不随实际消息长度变：
`MBOX_BYTES_PER_LINE = 16`（`:25`）、`MBOX_HEXDUMP_LINE_LEN = 16*4+2 = 66`（`:26`）、
`MBOX_HEXDUMP_MAX_LEN = 66 * (128/16) = 528`（`:27-28`）。
⇒ **永远输出 8 行、528 字节**，`:216` 的 `while (l < MBOX_HEXDUMP_MAX_LEN)`
把整个 128 字节缓冲区都 dump 出来。
所以**尾部的 `00` 不代表对端发了 0**，只代表缓冲区没被填满
（`:228` 每次读完会 `memset` 清零）。

同一条消息**只能读到一次**：`:229` 把 ready 标志清掉。

### §4.4 `poll` / `SIGIO`：写一个等 RX 的用户态程序

`mailbox-test` 是**少数支持 `.poll` 和 `.fasync` 的 debugfs 文件**
（`:254-261`），所以可以不轮询：

```c
/* 未编译未运行，见 99 §5 */
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <unistd.h>

int main(void)
{
	int fd = open("/sys/kernel/debug/1000c000.mailbox-test/message", O_RDONLY);
	struct pollfd pfd = { .fd = fd, .events = POLLIN };
	char buf[1024];

	for (;;) {
		if (poll(&pfd, 1, -1) > 0 && (pfd.revents & POLLIN)) {
			ssize_t n = read(fd, buf, sizeof(buf));
			if (n > 0) { fwrite(buf, 1, n, stdout); fflush(stdout); }
			lseek(fd, 0, SEEK_SET);   /* 必须，否则第二次 read 返回 0 */
		}
	}
}
```

三个坑：

1. **`lseek` 回 0 是必须的** —— `:233` 用 `simple_read_from_buffer(…, ppos, …)`，
   它会推进 `*ppos`；不回绕的话第二次 `read` 直接返回 0。
2. `poll` 返回的是 `EPOLLIN | EPOLLRDNORM`（`:250`），
   但**没有 `EPOLLERR` / `EPOLLHUP`** —— 通道被释放时 `poll` 不会醒，会永久阻塞。
3. **`mbox_data_ready` 是文件级全局变量**（`mailbox-test.c:30`
   `static bool mbox_data_ready;`），不是 `struct mbox_test_device` 的成员。
   ⇒ 见下节。

### §4.5 `mailbox-test` 自身的三个缺陷（用它调试时会撞上）

| # | 缺陷 | 行 | 后果 |
|---|---|---|---|
| ① | **`mbox_data_ready` 是全局 `static bool`**，而 `rx_buffer`/`lock`/`waitq` 都是 per-instance（`:30` vs `:32-46`） | 写 `:299`、读 `:164`、清 `:229` | **两个 `mailbox-test` 实例互相干扰**：A 收到消息把全局标志置 1，B 的 `poll` 也会报可读；而 B 读完（`:229`）就把标志清了，A 的 reader 反而醒不过来 |
| ② | `:294` 是 `else if (message)` —— message 为 NULL 且没有 `rx_mmio` 时 **`rx_buffer` 不更新**，但 `:299` 照样置标志 | `:290-299` | 纯门铃型控制器（载荷是 NULL，如 Tegra BPMP）下读到的是**上一条消息的残留 / 全零**，不是"没消息" |
| ③ | `:297` 无条件 `memcpy(rx_buffer, message, MBOX_MAX_MSG_LEN)` = **固定拷 128 字节** | `:297` | 控制器只传了一个 `u32*`（多数门铃型控制器就是这样）时会**越界读 124 字节** |

实用结论：**`mailbox-test` 适合验"共享内存 + 门铃"型控制器，
不适合验"纯门铃、载荷是 NULL"型**（后者会撞上 ② 和 ③）。
三条都记进 `99-evidence.md` §6。

另外还有 `04-extensions.md` §1.4 已记的两条（`-EPROBE_DEFER` 被吞成 NULL、
RX=TX 别名导致同一通道 free 两次）。⇒ **`mailbox-test` 共 5 个已知问题**。

---

## §5 sysfs / debugfs 全景：核心零，控制器三家

### §5.1 核心零 sysfs 零 debugfs 的取证

```
/usr/bin/grep -rn 'DEVICE_ATTR\|sysfs_\|ATTRIBUTE_GROUPS\|class_create' drivers/mailbox/
   → 零输出

/usr/bin/grep -n 'debugfs' drivers/mailbox/mailbox.c
   → 零输出
```

⇒ **两条都是零**：mailbox 核心既不建 sysfs 属性，也不建 debugfs 目录，
连 `class` 都没有（这一点和它没有 `bus_type` 是一致的，`02-datastructures.md` §1.1）。

直接后果：**`/sys` 和 `/sys/kernel/debug` 下没有任何以 mailbox 命名的入口**。
你能间接看到的只有两处，都不是 mailbox 自己提供的：

| 路径 | 谁提供 | 能看到什么 |
|---|---|---|
| `/sys/firmware/devicetree/base/<节点>/` | 通用 OF 层 | DTS 里写了什么（`06-devicetree.md` §8.2） |
| `/sys/bus/platform/devices/<节点>/driver` | 驱动模型 | 控制器有没有被驱动绑上 |

### §5.2 三个自建 debugfs 的控制器

`/usr/bin/grep -rn 'debugfs_create' drivers/mailbox/` 只命中三个文件：

| 驱动 | 建的路径 | 内容 | arm64 |
|---|---|---|---|
| `mailbox-test.c` | `/sys/kernel/debug/<dev_name>/{message,signal}`（`:269`/`:275`/`:278`） | 端到端收发（§4） | ✅ 手工加节点 |
| `bcm-flexrm-mailbox.c` | `/sys/kernel/debug/<dev_name>/{config,stats}`（`:1608`/`:1611`/`:1615`） | 环配置 + 每环统计 | ✅ **本子系统唯一提供真实统计的控制器** |
| `bcm-pdc-mailbox.c` | `/sys/kernel/debug/bcm_pdc/<name>`（`:499`/`:502`，`0400` 只读） | SPU 队列统计 | ✅ BCM iProc |

`bcm-flexrm` 的两个文件都是 `debugfs_create_devm_seqfile`
（`:1611`/`:1615`），回调分别是 `flexrm_debugfs_conf_show:1154-1162`
和 `flexrm_debugfs_stats_show:1164-1172`。
它的建立在 probe 里是**可跳过**的：`:1604-1605`
`if (!debugfs_initialized()) goto skip_debugfs;` —— 没开 `CONFIG_DEBUG_FS`
时驱动照常工作，只是没有统计。

结论对读者的意义：**如果你在 Broadcom iProc/Stingray 上调 mailbox，
你有统计可看；在其他所有 arm64 平台上，你只有 `dmesg`。**

### §5.3 观测缺口清单（本子系统最值得记住的一节）

下面这些是**根本没有接口可查的东西**，不是"要打开某个开关"：

| 想知道 | 有没有接口 | 唯一办法 |
|---|---|---|
| 系统里注册了哪些 mailbox 控制器 | ❌ `mbox_cons` 全局链表（`mailbox.c:23`）**不导出、不 dump** | 只能 `crash`/`drgn` 读内存，或看各控制器 probe 的日志 |
| 某条通道现在被谁占着 | ❌ `chan->cl` 无任何出口 | kprobe `mbox_request_channel` / `mbox_free_channel` 自己记账 |
| 一条通道的发送队列现在有多深 | ❌ `chan->msg_count` 无出口 | 只有打满时才有一条 `dev_err`（§6.5） |
| 某条通道用的是哪种 txdone 模式 | ❌ `chan->txdone_method` 无出口 | 只能从控制器驱动源码里的 `txdone_irq`/`txdone_poll` 反推，**而且要查驱动有没有自己改写**（`02-datastructures.md` §1.4 列了 3 处） |
| 累计发了多少条、超时多少次 | ❌ 核心不做任何计数 | 上层框架自己的统计（如 SCMI 的） |
| 一次收发花了多久 | ❌ 零 tracepoint | kprobe 自己算（§3.2） |

这张表也是 `08-feynman.md` 的一个取材点：
**mailbox 核心是内核里少见的"几乎完全不可观测"的框架** ——
它的设计假设是"控制器很简单、上层框架很聪明"，
所以把观测责任推给了两头。

---

## §6 错误码分诊：按语义分组

### §6.1 全部错误码的来源总表

核心一共只产出 **8 种**错误码。同一个码多处产出的按语义分组展开：

| 错误码 | 产出行 | 语义分组 | 谁看到 |
|---|---|---|---|
| `-ENODEV` | `:348` | 消费者不是 DT 设备 | `mbox_request_channel` |
| `-EPROBE_DEFER` | `:361`（**初始值**） | 控制器还没注册 / phandle 指错节点 | 同上 |
| `-EBUSY` | `:379`（+ `:58` 的内部初值） | 通道已被占 / 控制器模块正在卸载 | 同上 |
| `-EINVAL` | `:419` `:425` `:436` `:474` `:491` `:504` | **6 处、四种语义**，见 §6.6 | 申请与注册两侧 |
| `-ENOBUFS` | `:36` → 经 `:266` 返回 | **发送队列打满**（不是 `-EAGAIN`！） | `mbox_send_message` |
| `-ETIME` | `:282` | `tx_block` 等待超时 | 同上 |
| `-ENOTSUPP` | `:310` | 控制器没实现 `flush` | `mbox_flush` |
| `-ENOMEM` | `:596` | devres 分配失败 | `devm_mbox_controller_register` |

另有**两个"不是核心产出、但会从核心返回"的来源**，最容易误判：

| 来源 | 经哪一行透传 | 现象 |
|---|---|---|
| `of_parse_phandle_with_args` 的错误码 | `:356-357` 原样返回 | 通常是 `-EINVAL`（cell 个数不符）或 `-ENOENT`（没写 `mboxes`/index 越界） |
| 控制器 `of_xlate` 的错误码 | `:371-374` **零日志**返回 | 各驱动自定，多为 `-EINVAL`（`03-internals.md` §C4 列了 8 个校验 `args_count` 的驱动） |

⇒ **看到 `-EINVAL` 时，先分清是核心的 6 处、OF 的、还是 `of_xlate` 的**。
判据：核心的 6 处里有 5 处**都打 `dev_err`**（`:418` `:423` `:434` `:503` + `:490` 无日志），
所以 **"`-EINVAL` 但 `dmesg` 干净"⇒ 一定是 OF 或 `of_xlate`**。

### §6.2 `-EPROBE_DEFER`：最常见，也最容易误解

它是 `mailbox.c:361` 的**初始值**，不是某个失败分支主动返回的：

```c
	chan = ERR_PTR(-EPROBE_DEFER);                     /* :361 */
	list_for_each_entry(mbox, &mbox_cons, node)
		if (mbox->dev->of_node == spec.np) {           /* :363 */
			chan = mbox->of_xlate(mbox, &spec);
			…
		}
```

⇒ 拿到 `-EPROBE_DEFER` 只说明一件事：**`mbox_cons` 链表里没有 `of_node` 等于
`mboxes` 所指节点的控制器。** 三种完全不同的原因共用这一个码：

| 原因 | 会自愈吗 | 怎么确认 |
|---|---|---|
| 控制器驱动还没 probe（顺序问题） | ✅ 会 | 等一会，`devices_deferred` 里的条目会消失 |
| 控制器驱动没编进内核（`.config` 没开） | ❌ 永远不会 | §1.3 查 `.config` |
| `mboxes` 的 phandle 指向了一个**不是 mailbox 控制器**的节点 | ❌ 永远不会 | 反编译 dtb 核对（`06-devicetree.md` §8.2） |

```bash
cat /sys/kernel/debug/devices_deferred        # 唯一的可见出口
```

第三种最阴：`:363` 比较的是 **`of_node` 指针**，
不是 compatible，所以"节点存在但不是 mailbox"和"节点还没 probe"在核心里**完全无法区分**。

### §6.3 `-ETIME`：分清"没发出去"还是"没回来"

`-ETIME` 只有一个产出点（`mailbox.c:282`），条件很窄：

```c
	if (chan->cl->tx_block) {                       /* :271  只有阻塞模式才可能 */
		if (!chan->cl->tx_tout)                     /* :275 */
			wait = msecs_to_jiffies(3600000);       /* :276  "wait forever" = 1 小时 */
		else
			wait = msecs_to_jiffies(chan->cl->tx_tout);
		ret = wait_for_completion_timeout(&chan->tx_complete, wait);
		if (ret == 0) {
			t = -ETIME;                             /* :282 */
			tx_tick(chan, t);                       /* :283 */
		}
	}
```

它的含义**只有一个**：**在 `tx_tout` 毫秒内没有人调 `tx_tick` 声明发完**。
它**不**表示"对端没回响应"。

| 观察 | 结论 | 下一步 |
|---|---|---|
| `mbox_send_message` 返回 `-ETIME` | TX 侧问题：txdone 机制没闭环 | §6.4 + 查控制器的 IRQ / `last_tx_done` |
| `mbox_send_message` 返回 **≥0**，上层框架自己报超时 | RX 侧问题：发出去了但对端没回 | 查对端固件、查 `shmem` 布局、**查 DTS 方向有没有写反**（§7.2） |
| `mbox_send_message` 返回 `-ENOBUFS` | 队列打满，前面的消息都卡住了 | §6.5 |

两个容易踩的细节：

1. **`tx_block = false` 时永远不会有 `-ETIME`** —— 返回值是队列下标令牌（≥0），
   "发失败"只能从 `tx_done` 回调的 `r` 参数里知道。
2. **超时路径不 `complete()`**：`tx_tick:113` 是
   `if (r != -ETIME && chan->cl->tx_block)` —— 超时时**故意不**唤醒，
   否则下一次 `wait_for_completion_timeout` 会立刻假成功
   （`03-internals.md` §A6）。所以**一次超时之后通道状态是干净的**，可以继续用。
3. `tx_tout = 0` 不是"不等"，是"等 1 小时"（`:275-276`）——
   现象是**整个 probe 卡死一小时**，看起来像死机。

### §6.4 两条 "TX ticker" 消息：txdone 模式配错了

这两条是 mailbox 里**最能直接定位问题的日志**：

| 消息 | 行 | 谁触发 | 根因 |
|---|---|---|---|
| `Controller can't run the TX ticker` | `:179-180` | 控制器调了 `mbox_chan_txdone`，但通道不是 IRQ 模式 | 控制器忘了设 `txdone_irq = true` |
| `Client can't run the TX ticker` | `:200` | 客户端调了 `mbox_client_txdone`，但通道不是 ACK 模式 | 控制器设了 `txdone_irq` 或 `txdone_poll`，客户端却按 ACK 模式写 |

两条的共同后果：**打完日志就 `return`（`:181`/`:201`），`tx_tick` 不会被调** ⇒
`tx_complete` 永远不 complete ⇒ 阻塞客户端等到 `tx_tout` 超时（§6.3）。
**所以这两条日志后面通常紧跟一个 `-ETIME`**，看到 `-ETIME` 时先往上翻。

定位 txdone 模式的方法（因为没有任何运行期接口，§5.3）：

```bash
# 在控制器驱动里找这三行
/usr/bin/grep -n 'txdone_irq\|txdone_poll\|txdone_method' drivers/mailbox/<你的控制器>.c
```

判据（`mailbox.c:493-498`）：`txdone_irq` → IRQ；否则 `txdone_poll` → POLL；
**两个都没设 → ACK**。
但**必须再查驱动有没有自己改写 `chan->txdone_method`** ——
全树共 3 处这么干（`02-datastructures.md` §1.4），arm64 相关的两处是：
`drivers/mailbox/tegra-hsp.c:511`（在 `startup` 里强制改成 IRQ，只对共享邮箱、
门铃通道不改）和 `drivers/mailbox/omap-mailbox.c:376`（`send_no_irq` 时强制 ACK）；
第三处在已裁掉的 ACPI PCC 里。**忘了这一步会得出完全相反的结论**（这个坑本文档集踩过一次，
记在 `99-evidence.md` §4）。

### §6.5 `-ENOBUFS` + `Try increasing MBOX_TX_QUEUE_LEN`

```c
	t = add_to_rbuf(chan, mssg);
	if (t < 0) {
		dev_err(chan->mbox->dev, "Try increasing MBOX_TX_QUEUE_LEN\n");  /* :265 */
		return t;                                                        /* :266 */
	}
```

`add_to_rbuf:34-37` 在 `chan->msg_count == MBOX_TX_QUEUE_LEN` 时返回 `-ENOBUFS`。

三个要点：

1. **错误码是 `-ENOBUFS`（-105），不是 `-EAGAIN` / `-EBUSY`** ——
   这是最容易记错的一个（消费者代码里判 `-EAGAIN` 的写法是错的）。
2. **那句 `Try increasing MBOX_TX_QUEUE_LEN` 是不可操作的建议**：
   `MBOX_TX_QUEUE_LEN` 是 `include/linux/mailbox_controller.h:103` 的
   **硬编码 `20`**，既不是 Kconfig 也不是模块参数 ⇒ 只能改代码重编内核。
   同文件 `:90-103` 的 `REVISIT` 注释承认了这一点。记在 `99-evidence.md` §6。
3. 队列满的**真实根因几乎总是 txdone 没闭环**（§6.4）：
   20 条对任何正常场景都够用（`:97-99` 的注释解释了为什么），
   打满意味着已发出的消息没有一条被 `tx_tick` 消掉。
   ⇒ **看到这条日志不要去改 20，去查 txdone。**

### §6.6 `-EINVAL` 的四种语义（6 个产出点）

| 语义分组 | 行 | 判据 | 有日志？ |
|---|---|---|---|
| ① 消费者不是 DT 设备（`_byname` 侧） | `:419` | `cl->dev->of_node == NULL` | ✅ `:418` |
| ② DTS 缺 `mbox-names` | `:425` | `_byname` 找不到属性 | ✅ `:423-424` |
| ③ 名字对不上 | `:436` | 遍历完 `mbox-names` 没命中 | ✅ `:434-435` |
| ④ 通道下标越界 | `:474` | `of_mbox_index_xlate` 里 `args[0] >= num_chans` | ❌ **零日志**（走 `:371-374` 那个出口） |
| ⑤ 控制器注册参数不全 | `:491` | `!mbox \|\| !dev \|\| !ops \|\| !num_chans` | ❌ 无日志 |
| ⑥ POLL 模式缺 `last_tx_done` | `:504` | — | ✅ `:503` |

其中 ④ 是**唯一会让消费者拿到"零日志 `-EINVAL`"的核心内部原因** ——
它和"控制器自定义 `of_xlate` 拒了"在现象上完全一样，都只能靠 §3.2 的 kprobe 区分。

⑤ 完全没日志是个真实的可用性问题：控制器驱动 probe 失败只会看到自己打的
`Failed to register mailbox: -22`（例如 `drivers/mailbox/rockchip-mailbox.c:244`），
**核心不告诉你是四个字段里哪一个空的**。记在 `99-evidence.md` §6。

---

## §7 三个典型故障场景的完整走查

### §7.1 场景一：消费者 probe 失败，`dmesg` 完全干净

这是最常见、也最让人卡住的一种。固定五步：

```bash
# ① 打开 dynamic debug 并重触发（§2.3）
echo 'file drivers/mailbox/mailbox.c +pflt' > /sys/kernel/debug/dynamic_debug/control
D=/sys/bus/platform/drivers/<消费者驱动>
echo <设备名> > $D/unbind; echo <设备名> > $D/bind; dmesg | tail -30

# ② 还是干净 ⇒ 走的是零日志出口，先看 deferred
cat /sys/kernel/debug/devices_deferred | /usr/bin/grep <你的节点>

# ③ 在 deferred 里 ⇒ 查控制器驱动在不在
ls /sys/bus/platform/devices/<控制器节点>/driver     # 没有 driver 链接就是没 probe
zcat /proc/config.gz | /usr/bin/grep <控制器的 CONFIG>

# ④ 不在 deferred 里 ⇒ 是 of_xlate 拒了，上 kretprobe 拿真实码（§3.2）
bpftrace -e 'kretprobe:mbox_request_channel { printf("ret=%ld\n", retval); }'

# ⑤ 拿到 -EINVAL ⇒ 核对 cell 个数与值（06 §2）
hexdump -C /sys/firmware/devicetree/base/<消费者节点>/mboxes
hexdump -C /sys/firmware/devicetree/base/<控制器节点>/'#mbox-cells'
```

### §7.2 场景二：申请全部成功，上层框架报超时

特征：`dmesg` 里 mailbox 一条日志都没有，只有 SCMI/BPMP/RTKit 自己的超时。
按可能性排序：

| # | 可能 | 验证 | 出处 |
|---|---|---|---|
| ① | **`mboxes` 的 TX/RX 顺序写反** | 对着控制器绑定文档核 —— SCMI 是 `tx ? 0 : 1`（`drivers/firmware/arm_scmi/mailbox.c:149`） | `06-devicetree.md` §5.3 |
| ② | `shmem` 指错了内存区 | mailbox 核心不读 `shmem`，全靠框架 | `06-devicetree.md` §5.1 |
| ③ | 载荷格式不对 | 核心从不解引用 `mssg`，格式错**零报错** | `04-extensions.md` §2.3 |
| ④ | 对端固件没跑 / 没使能 | 看对端的 remoteproc / firmware 加载日志 | — |
| ⑤ | 中断没接对（RX 中断进不来） | `cat /proc/interrupts` 看控制器的中断计数有没有涨 | 最便宜的一条，先做 |

**第 ⑤ 条应该第一个做**，因为它一条命令就能把问题一分为二：

```bash
grep -i '<控制器名>\|mhu\|hsp\|ipcc\|mailbox' /proc/interrupts
# 触发一次操作，再看一次；计数不涨 ⇒ RX 中断根本没来
```

### §7.3 场景三：验证一个新写的控制器驱动

固定顺序，每一步都能独立判定通过/失败：

| 步 | 做什么 | 通过的标志 |
|---|---|---|
| 1 | 只 probe 控制器（DTS 里先不放消费者） | 没有 `Failed to register mailbox` |
| 2 | 加 `mailbox-test` 节点（`06-devicetree.md` §6.3），只给 TX 一条通道 | `/sys/kernel/debug/<name>/message` 出现 |
| 3 | `echo -n x > .../message` | 不返回错误；`dmesg` 里没有 `-ETIME`、没有 TX ticker 消息 |
| 4 | 加第二条通道（RX），`cat .../message` | 不是 `<NO RX CAPABILITY>`（§4.3） |
| 5 | 对端回一条，`cat` 拿到 hex dump | ✅ 端到端通了 |
| 6 | 换成真实消费者 | 若 1–5 都过、这一步失败 ⇒ 问题在消费者或 DTS 的 cell 值 |

第 3 步是**最能暴露 txdone 配置错误**的一步：
`mailbox-test` 设了 `tx_block = true` + `tx_tout = 500`
（`mailbox-test.c:344`/`:346`），所以 txdone 没闭环时
**500ms 后必定报 `-ETIME`**，不会静默。
这是 `mailbox-test` 最大的价值：它把 §6.4 的问题变成一个 0.5 秒就能看到的现象。

---

## §8 一页速查

```bash
### 1. 把默认看不见的三条日志打开（第一步，永远先做）
echo 'file drivers/mailbox/mailbox.c +pflt' > /sys/kernel/debug/dynamic_debug/control
# 启动参数版： dyndbg="file drivers/mailbox/mailbox.c +p"    不要用 mailbox.dyndbg=

### 2. 谁卡在 probe 上（-EPROBE_DEFER 的唯一出口）
cat /sys/kernel/debug/devices_deferred

### 3. DTS 里到底写了什么（比看 .dts 可靠）
hexdump -C /sys/firmware/devicetree/base/<消费者>/mboxes
hexdump -C /sys/firmware/devicetree/base/<控制器>/'#mbox-cells'
cat /sys/firmware/devicetree/base/<消费者>/mbox-names | tr '\0' '\n'

### 4. 控制器有没有被驱动绑上
ls -l /sys/bus/platform/devices/<节点>/driver

### 5. 拿到零日志出口的真实错误码
bpftrace -e 'kretprobe:mbox_request_channel { printf("%s ret=%ld\n", comm, retval); }'

### 6. 看一次收发的调用时间线（核心零 tracepoint，只能这样）
cd /sys/kernel/debug/tracing
echo function_graph > current_tracer; echo 'mbox_*' > set_ftrace_filter; echo 1 > tracing_on

### 7. RX 中断到底有没有来（把问题一分为二，最便宜）
grep -i 'mhu\|hsp\|ipcc\|mailbox' /proc/interrupts

### 8. 端到端收发（需要 CONFIG_MAILBOX_TEST + DTS 节点）
T=$(dirname $(ls -d /sys/kernel/debug/*/message | head -1))
printf '\x01\x02\x03\x04' > $T/signal      # 仅共享内存型，且一次性
echo -n hello > $T/message
timeout 5 cat $T/message                   # <NO RX CAPABILITY> = DTS 没给 RX 通道

### 9. 控制器侧的详细日志（6 个驱动自带 dev_dbg）
echo 'file drivers/mailbox/imx-mailbox.c +p' > /sys/kernel/debug/dynamic_debug/control
# 其余：apple-mailbox.c / stm32-ipcc.c / rockchip-mailbox.c / bcm2835-mailbox.c / mailbox-sti.c

### 10. Broadcom iProc 独有的统计
cat /sys/kernel/debug/<dev_name>/stats     # bcm-flexrm
cat /sys/kernel/debug/bcm_pdc/<name>       # bcm-pdc
```

**错误码一行速记**：
`-ENODEV`=不是 DT 设备 · `-EPROBE_DEFER`=控制器没注册（**或 phandle 指错**）·
`-EBUSY`=通道被占 · `-EINVAL`=四种语义（§6.6，**零日志的那种是下标越界或 `of_xlate` 拒了**）·
`-ENOBUFS`=队列满 20 条（**不是 `-EAGAIN`**）· `-ETIME`=txdone 没闭环（**不是"对端没回"**）·
`-ENOTSUPP`=控制器没实现 `flush`。

---

## §9 读完本篇之后

| 想知道 | 去哪篇 |
|---|---|
| `txdone_method` 三种模式的完整状态机 | `03-internals.md` §A5、§A6 |
| `tx_tick` 为什么超时时不 `complete()` | `03-internals.md` §A6 |
| `MBOX_TX_QUEUE_LEN` 为什么是 20、`REVISIT` 说了什么 | `03-internals.md` §C3 |
| DTS 配错的完整定位手册（编译期 / 静态 / 动态三层） | `06-devicetree.md` §8 |
| 「错误消息 → 原因」映射表（DT 视角） | `06-devicetree.md` §9 |
| `mailbox-test` 的实现细节与它的 5 个已知问题 | `04-extensions.md` §1、本篇 §4.5 |
| 消费者 / 控制器骨架里该怎么处理这些错误码 | `05-usage.md` §2.5、§3.2 |
| 为什么 mailbox 核心设计成几乎不可观测 | `08-feynman.md` |
| 本篇每条普查数字的复现命令 | `99-evidence.md` §3 |
