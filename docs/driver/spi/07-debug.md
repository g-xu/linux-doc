---
title: 调试方法
---

# SPI 调试手段

> 本篇讲的是**去看哪个输出**。所有命令和路径都对应本树 6.1 的实际代码，
> 示例输出全部标注了是"实测格式"还是"按格式串推导"——**没有编造过的输出**。

---

## §0 按症状找章节

| 症状 | 去哪 |
|-----|------|
| dmesg 里什么都没有 | §1（开关没打开）+ §2（这条路径本来就不打印） |
| 想看每段传输的真实速率 | §3.3（`spi_transfer_start` 的 `effective_speed_hz`）→ 其实要看 §3.6 |
| 想看片选到底怎么动的 | §3.4 注意参数不是电平 |
| 想看实际收发的字节 | §3.5 tracepoint 会 dump 前 64 字节 |
| 传输返回 `-EINVAL`，dmesg 一片空白 | **§5.2**（20 个出口零日志，本篇最重要的一节） |
| `SPI transfer timed out` | §6 |
| 想知道跑了多少数据 / 有没有超时过 | §4 |
| 怀疑走了慢路径 / 队列积压 | §4.4 |
| 想在没有真实外设的情况下测通路 | §7.3 |
| 用户态想直接收发 | §7.1 |

---

## §1 编译期开关（有几道门）

### §1.1 门的顺序

```
CONFIG_SPI  (menuconfig, bool)                    ← 总开关，没它什么都没有
  ├── CONFIG_SPI_MASTER  (bool, default SPI)      ← 自动跟随，几乎不用管
  │     ├── CONFIG_SPI_MEM  (bool，不是 tristate)
  │     └── CONFIG_SPI_<厂商>  (tristate)          ← 具体控制器
  ├── CONFIG_SPI_SLAVE  (bool)
  ├── CONFIG_SPI_SPIDEV  (tristate)               ← /dev/spidevB.C
  ├── CONFIG_SPI_LOOPBACK_TEST  (tristate)        ← 自测模块
  └── CONFIG_SPI_DEBUG  (bool, depends on DEBUG_KERNEL)
```

`CONFIG_SPI_DYNAMIC` 不是可选项，是 `def_bool ACPI || OF_DYNAMIC || SPI_SLAVE`
（`03-internals.md` §B10）。

### §1.2 `CONFIG_SPI_DEBUG` 实际只做一件事

`drivers/spi/Kconfig:28-33` 的帮助文本说：

> Say "yes" to enable debug messaging (like dev_dbg and pr_debug),
> **sysfs, and debugfs support** in SPI controller and protocol drivers.

而代码里它的**唯一**用处是（`drivers/spi/Makefile:6`）：

```makefile
ccflags-$(CONFIG_SPI_DEBUG) := -DDEBUG
```

**就这一行。** 全树没有任何 `#ifdef CONFIG_SPI_DEBUG`：

```bash
grep -rn "CONFIG_SPI_DEBUG" --include=*.c --include=*.h --include=Makefile .
# 只有 drivers/spi/Makefile:6
```

所以：

| 帮助文本承诺 | 实际 | 结论 |
|-----------|-----|------|
| 打开 `dev_dbg` / `pr_debug` | ✓ 通过 `-DDEBUG` 对整个 `drivers/spi/` 生效 | 对 |
| 打开 **sysfs** 支持 | ✗ sysfs 统计是**无条件**编译的（§4） | 帮助文本过时 |
| 打开 **debugfs** 支持 | ✗ `drivers/spi/spi.c` **零 debugfs 调用** | 帮助文本过时 |

全 `drivers/spi/` 只有 4 个控制器驱动用 debugfs
（`spi-bcm2835.c`、`spi-bcm2835aux.c`、`spi-dw-core.c`、`spi-hisi-kunpeng.c`），
而且**都不受 `CONFIG_SPI_DEBUG` 控制**——是各自的私有开关。
**SPI 核心没有 debugfs 目录，别去 `/sys/kernel/debug/spi/` 找，它不存在。**

### §1.3 打开 `CONFIG_SPI_DEBUG` 之后核心里能多看到什么

**很少。** `drivers/spi/spi.c` 一共只有 **4 个 `dev_dbg`、0 个 `pr_debug`**：

| 行 | 内容 | 什么时候有用 |
|---|------|------------|
| 683 | `registered child %s` | 确认某个 DT 子节点变成了 spi_device |
| 1363 | `SPI transfer interrupted` | 区分"被信号打断"和"真失败" |
| 3192 | `registered %s %s`（控制器） | 确认控制器注册成功 |
| 3817 | `setup mode %lu, ...%u bits/w, %u Hz max --> %d` | **最有用的一条**：`spi_setup()` 最终定下来的模式/字长/速率 |

真正的价值在**控制器驱动**里：`drivers/spi/*.c` 一共 **328 个 `dev_dbg`，
分布在 64 个文件**。所以 `CONFIG_SPI_DEBUG` 的正确用法是"我在查某个具体
控制器驱动"，不是"我在查 SPI 核心"。

### §1.4 更细的办法：`dynamic_debug`

不想为了 4 条消息重编内核，用 dynamic_debug（需要 `CONFIG_DYNAMIC_DEBUG`）：

```bash
# 只打开 spi.c 的所有 dev_dbg
echo 'file drivers/spi/spi.c +p' > /sys/kernel/debug/dynamic_debug/control

# 只打开 spi_setup 那一条（第 3817 行）
echo 'file drivers/spi/spi.c line 3817 +p' > /sys/kernel/debug/dynamic_debug/control

# 打开某个控制器驱动的全部
echo 'file drivers/spi/spi-rockchip.c +p' > /sys/kernel/debug/dynamic_debug/control

# 关掉
echo 'file drivers/spi/spi.c -p' > /sys/kernel/debug/dynamic_debug/control
```

**dynamic_debug 和 `CONFIG_SPI_DEBUG` 不叠加**：`-DDEBUG` 让 `dev_dbg`
变成无条件 `printk`，dynamic_debug 让它变成可控开关。**同时开着的时候
`-DDEBUG` 赢**（`dev_dbg` 走的是 `#if defined(DEBUG)` 分支），
dynamic_debug 的 control 文件里就看不到这些点了。查一个驱动时**不要
两个一起开**。

---

## §2 日志可见性等级

这一节的用途：**在 dmesg 里没看到某条消息，先确认它是不是"只打一次"或者
"根本不打印"，再去怀疑代码没走到。**

### §2.1 `drivers/spi/spi.c` 的全部日志点分类

| 宏 | 条数 | 可见性 | 说明 |
|---|-----|-------|------|
| `dev_err` | 42 | 始终输出 | 主力，大多在注册/校验路径 |
| `dev_warn` | 6 | 始终输出 | 其中 2 条是 bus-width 非法值（`06-devicetree.md` §3.4） |
| `dev_info` | 2 | 始终输出 | 见 §2.2 |
| `dev_dbg` | 4 | 需要 §1.2 / §1.4 | 见 §1.3 |
| `dev_err_once` | **1** | **整个系统生命周期只打一次** | `:1493` |
| `dev_warn_once` | **1** | 同上 | `:4185` |
| `WARN_ON` | 6 | 带栈回溯，始终输出 | 都是内部一致性断言 |
| `WARN_ON_ONCE` | 1 | 只一次 | `:2043` |
| `dev_err_ratelimited` / `dev_warn_ratelimited` / `pr_debug` / `BUG_ON` | **0** | — | 核心里一个都没有 |

验证：

```bash
for m in dev_err dev_err_once dev_warn dev_warn_once dev_info dev_dbg \
         pr_debug WARN_ON WARN_ONCE BUG_ON; do
  printf "%-18s %s\n" "$m" "$(grep -c "\b$m(" drivers/spi/spi.c)"
done
```

### §2.2 两条 `dev_info`——都是"配置提示"而不是错误

| 行 | 消息 | 含义 |
|---|------|------|
| 1962 | （`spi_set_thread_rt()` 里） | 该控制器的 pump 线程被提到了 RT 优先级。**不可逆**，见 `03-internals.md` §B9 |
| 3202 | `controller is unqueued, this is deprecated` | 这个控制器实现了裸 `transfer` 回调而不是 `transfer_one*`。全树只剩少数几个，见 `05-usage.md` §1.1 |

### §2.3 三条 `_once`——最容易被误判成"没走到"

| 行 | 宏 | 消息 / 条件 | 为什么值得单独说 |
|---|---|-----------|---------------|
| **1493** | `dev_err_once` | `cs_change` 延时降级 | **系统里第一个触发的设备打完，后面所有设备都不会再打**。看到这条要意识到"可能还有别的设备也在触发"。机制见 `03-internals.md` §B5.3 |
| **4185** | `dev_warn_once` | `Attempted to sync while suspend` | 挂起期间还有人调 `spi_sync()`。只打一次，但**每次都返 `-ESHUTDOWN`**（`:4186`）——所以"没有警告"不代表"没在发生" |
| **2043** | `WARN_ON_ONCE` | `xfer->ptp_sts && !xfer->timestamped` | 驱动声明了要打时间戳但没打。写控制器驱动时才会碰到 |

**实践规则**：查一个"偶发"问题时，`_once` 的消息**不能用来数次数**。
要数次数就去看 §4 的 sysfs 统计（`errors` / `timedout`）。

---

## §3 tracepoint

### §3.1 全部 9 个事件

`include/trace/events/spi.h`。全部在 `spi:` 这个 subsystem 下。

| 事件 | 定义行 | 触发时机 | 主要字段 |
|-----|-------|---------|---------|
| `spi_controller_idle` | 29 | pump 线程空转下来 | `bus_num` |
| `spi_controller_busy` | 37 | pump 线程开始忙 | `bus_num` |
| `spi_setup` | 45 | 每次 `spi_setup()` 返回 | `mode` / `bits_per_word` / `max_speed_hz` / `status` |
| `spi_set_cs` | 78 | 每次片选真的动 | `mode` / **`enable`** |
| `spi_message_submit` | 125 | 消息进队列 | `msg` 指针 |
| `spi_message_start` | 133 | 消息开始跑 | `msg` 指针 |
| `spi_message_done` | 141 | 消息完成 | `frame` / `actual` 长度 |
| `spi_transfer_start` | 220 | 每段开始 | `len` + **tx/rx 前 64 字节** |
| `spi_transfer_stop` | 228 | 每段结束 | 同上 |

**9 个事件里没有一个带 `TP_CONDITION`**：

```bash
grep -c "TP_CONDITION" include/trace/events/spi.h    # 0
```

**后果：一旦 enable，这条总线上所有设备的所有传输都记。** 高频 SPI
（比如 1kHz 采样的 ADC）会瞬间灌满 ring buffer。要过滤只能用 trace-cmd
的 `-f` 后置过滤（见 §3.6），事件本身不会帮你省。

### §3.2 三个 event class 的共享结构

```
DECLARE_EVENT_CLASS(spi_controller, :11)  →  spi_controller_idle / spi_controller_busy
DECLARE_EVENT_CLASS(spi_message,    :102) →  spi_message_submit / spi_message_start
DECLARE_EVENT_CLASS(spi_transfer,   :179) →  spi_transfer_start / spi_transfer_stop
```

**`spi_message_done` 不在 `spi_message` class 里**（它是独立的
`TRACE_EVENT`，`:141`），因为它多两个字段 `frame` / `actual`。
所以 **`actual_length` 只能从 `spi_message_done` 拿到**。

### §3.3 `spi_setup`：确认参数最终定成了什么

```
TP_printk 格式（include/trace/events/spi.h:67）：
spi%d.%d setup mode %lu, %s%s%s%s%u bits/w, %u Hz max --> %d
```

**这是 `06-devicetree.md` §3.2「速率被静默下夹」的唯一无侵入观测点**——
比 `dev_dbg` 那条（`:3817`）好用，因为不需要重编内核。

```bash
trace-cmd record -e spi:spi_setup &
# 触发一次（比如 rmmod/modprobe 设备驱动，或者 spidev 的 SPI_IOC_WR_MODE）
trace-cmd stop; trace-cmd report
```

### §3.4 `spi_set_cs` 的参数不是电平

```
TP_printk（include/trace/events/spi.h:96-101）：
spi%d.%d activate|deactivate[, cs_high]
```

字段是 `bool enable`，赋值来自 `spi_set_cs()` 的 `activate`
（`drivers/spi/spi.c:988`）：

```c
	trace_spi_set_cs(spi, activate);
```

**`activate = 1` 表示"选中这个片"这个逻辑意图，不是"引脚上是高电平"。**
`, cs_high` 这个后缀也只是把 `spi->mode & SPI_CS_HIGH` 打出来——
而在 DT + GPIO 片选的情况下这个位**根本不参与决定电平**
（`06-devicetree.md` §4.2）。

**所以：`spi_set_cs` 能告诉你"核心认为片选该动了"，不能告诉你"引脚实际
是高还是低"。后者只有示波器。**

### §3.5 `spi_transfer_start` / `spi_transfer_stop` 会 dump 数据

这是 SPI tracepoint 最有用、也最容易被忽略的能力。
`DECLARE_EVENT_CLASS(spi_transfer, :179)` 里有两个 `__dynamic_array`：

```c
		__dynamic_array(u8, rx_buf,
				spi_valid_rxbuf(msg, xfer) ?
					(xfer->len < 64 ? xfer->len : 64) : 0)
		__dynamic_array(u8, tx_buf,
				spi_valid_txbuf(msg, xfer) ?
					(xfer->len < 64 ? xfer->len : 64) : 0)
```

| 事实 | 依据 | 含义 |
|-----|-----|------|
| 每段最多记 **64 字节** | `:190-196` | 长传输只能看到开头 |
| 哑缓冲**不会**被记 | `spi_valid_txbuf` / `spi_valid_rxbuf` 宏，`:174-177` | 直接印证 `03-internals.md` §B3.1：哑缓冲和真数据在这里就分开了 |
| `TP_printk` 用 `%*phD` | `:213-218` | 输出是 `tx=[aa-bb-cc] rx=[...]` 这种十六进制 |

**两个必须知道的坑**：

1. **`spi_transfer_start` 里的 `rx` 是脏的。** 两个事件共用同一个 class，
   `memcpy` 在 `TP_fast_assign` 里无条件执行（`:204-210`）——而
   `spi_transfer_start` 是在传输**之前**触发的，那时 `rx_buf` 里还是
   上一次的残留或未初始化内容。**要看收到的数据只能看
   `spi_transfer_stop`。**
2. **这会把 SPI 上的明文数据打进 trace buffer。** 挂着安全芯片、TPM、
   加密狗的总线上开这个事件，等于把通信内容记进一个非特权可读的地方
   （取决于 tracefs 权限）。**生产环境慎用。**

### §3.6 实用命令

```bash
# 最常用：看一条消息被拆成了几段、每段多长、内容是什么
trace-cmd record -e spi:spi_message_submit -e spi:spi_transfer_start \
                 -e spi:spi_transfer_stop  -e spi:spi_message_done
# ^C 之后
trace-cmd report

# 只看某条总线（bus_num 是所有事件都有的字段）
trace-cmd record -e 'spi:*' -f 'bus_num == 1'

# 只看片选和 setup（低频，可以长时间挂着）
trace-cmd record -e spi:spi_set_cs -e spi:spi_setup

# 不装 trace-cmd 的话用裸 tracefs
cd /sys/kernel/debug/tracing
echo 1 > events/spi/enable
cat trace_pipe
echo 0 > events/spi/enable
```

**`effective_speed_hz` 不在任何 tracepoint 字段里。**
我在 §0 的表里指向这里是因为大家都会先来找它——**它只在
`struct spi_transfer` 里，tracepoint 没有导出**。

```bash
grep -n "effective_speed_hz" include/trace/events/spi.h     # 空
```

要看真实速率只有三条路：
1. 控制器驱动自己的 `dev_dbg`（§1.3 说的 328 条里通常有）；
2. `spi_setup` tracepoint 看**上限**，加上驱动的分频规则手算；
3. 示波器。

### §3.7 两个导出符号

```c
EXPORT_TRACEPOINT_SYMBOL(spi_transfer_start);      /* drivers/spi/spi.c:40 */
EXPORT_TRACEPOINT_SYMBOL(spi_transfer_stop);       /* :41 */
```

用途：控制器驱动自己把一段拆成多个硬件操作时，可以自己发这两个事件。
**全树只有一个驱动这么用**：`drivers/spi/spi-mxs.c:378` / `:425`。

```bash
grep -rn "trace_spi_transfer_start" drivers/ | grep -v drivers/spi/spi.c
# 只有 spi-mxs.c
```

### §3.8 一个源码缺陷（不影响功能）

`include/trace/events/spi.h:236`：

```c
#endif /* _TRACE_POWER_H */
```

头保护宏是 `_TRACE_SPI_H`（`:5-6`），注释里写的是 `_TRACE_POWER_H`——
从 `power.h` 复制过来时忘了改。**只是注释，编译和运行都不受影响。**
放在这里是因为 grep `_TRACE_SPI_H` 时会漏掉这一行，容易以为文件被截断了。

---

## §4 sysfs

### §4.1 三个位置

```
/sys/bus/spi/devices/spiB.C/          ← 每个 SPI 设备
/sys/class/spi_master/spiB/           ← 每个 master 控制器
/sys/class/spi_slave/spiB/            ← 每个 slave 控制器（互斥）
```

`spi_master_class` 定义在 `drivers/spi/spi.c:2764`，`spi_slave_class` 在
`:2867`；`ctlr->dev.class` 的二选一在 `:2926` / `:2928`。

### §4.2 非统计类属性

| 路径 | 权限 | 作用 |
|-----|-----|------|
| `devices/spiB.C/modalias` | 0444 | `spi:<name>`。确认 `compatible` 砍前缀后变成了什么（`06-devicetree.md` §7.2 最后一行） |
| `devices/spiB.C/driver_override` | 0644 | 强制绑定到指定驱动，无视 match 表。调试"驱动没绑上"很好用 |
| `class/spi_slave/spiB/slave` | 0644 | **只有 slave 控制器有**。写 compatible 名字来创建从设备，写空字符串销毁。`slave_show()` `:2799` / `slave_store()` `:2811` |

属性组：`spi_dev_attrs[]` `:217` → `spi_dev_groups[]` `:264` → `spi_bus_type.dev_groups` `:473`；
控制器侧 `spi_controller_statistics_attrs[]` `:270` → `spi_master_groups[]` `:307` → `:2768`。

**`driver_override` 的用法**（比 unbind/bind 干净）：

```bash
echo spidev > /sys/bus/spi/devices/spi0.0/driver_override
echo spi0.0 > /sys/bus/spi/drivers/<原驱动>/unbind
echo spi0.0 > /sys/bus/spi/drivers/spidev/bind
# 恢复
echo "" > /sys/bus/spi/devices/spi0.0/driver_override
```

### §4.3 统计属性：**每个设备和每个控制器各 30 项**

统计目录：

```
/sys/class/spi_master/spiB/statistics/          ← 整条总线的累计
/sys/bus/spi/devices/spiB.C/statistics/         ← 单个设备的累计
```

**两套是独立累加的**（每个累加点都成对出现，比如 `:1519`/`:1520`），
所以**控制器的数 = 该总线上所有设备的数之和**——这个不变式可以用来
检查有没有漏统计的路径。

11 个标量（`include/linux/spi/spi.h:63-83`）：

| 文件名 | 含义 | 累加点 | 怎么用 |
|-------|-----|-------|-------|
| `messages` | 完成的消息数 | `spi.c:1519`/`:1520` | 分母 |
| `transfers` | 完成的段数 | `:1525`/`:1526`（经 `spi_statistics_add_transfer_stats()` `:312`） | `transfers/messages` > 1 说明有多段消息 |
| `errors` | 出错次数 | `:1551`/`:1553` | 第一个该看的 |
| `timedout` | 超时次数 | `:1391`/`:1392` | 见 §6 |
| `spi_sync` | `spi_sync()` 调用数 | `:4195`/`:4196` | 分母 |
| `spi_sync_immediate` | 走了同步快路径的数 | `:4210`/`:4211` | 见 §4.4 |
| `spi_async` | `spi_async()` 调用数 | `:4014`/`:4015` | — |
| `bytes` | 总字节 | `:312` 内 | 见下面的坑 |
| `bytes_rx` | 真实收字节（**排除哑缓冲**） | 同上 | — |
| `bytes_tx` | 真实发字节（**排除哑缓冲**） | 同上 | — |
| `transfers_split_maxsize` | 因超 `max_transfer_size` 被拆的段数 | `:3584`/`:3586` | 非 0 说明在走 `03-internals.md` §B4 的拆分路径 |

**`bytes` ≠ `bytes_tx + bytes_rx`，两个方向都不成立**（`:329-336`）：

```c
	u64_stats_add(&stats->bytes, xfer->len);
	if ((xfer->tx_buf) && (xfer->tx_buf != ctlr->dummy_tx))
		u64_stats_add(&stats->bytes_tx, xfer->len);
	if ((xfer->rx_buf) && (xfer->rx_buf != ctlr->dummy_rx))
		u64_stats_add(&stats->bytes_rx, xfer->len);
```

| 情况 | `bytes` | `bytes_tx` | `bytes_rx` |
|-----|--------|-----------|-----------|
| 全双工 N 字节 | +N | +N | +N | → **tx+rx = 2×bytes** |
| 只发 N 字节（无哑缓冲） | +N | +N | +0 | 相等 |
| 只发 N 字节（控制器 `MUST_RX`，有哑 rx） | +N | +N | **+0** | 哑缓冲被排除 |

**所以 `bytes` 是"线上时间"的度量，`bytes_tx`/`bytes_rx` 是"有效载荷"的度量。
算带宽用 `bytes`，算数据量用 `bytes_tx`/`bytes_rx`。**

### §4.4 一个数就能定位的延迟问题：`spi_sync_immediate / spi_sync`

```bash
cd /sys/class/spi_master/spi0/statistics
echo "immediate/sync = $(cat spi_sync_immediate) / $(cat spi_sync)"
```

| 比值 | 含义 | 该做什么 |
|-----|-----|---------|
| ≈ 1.0 | 全部走了同步快路径，**没有经过 pump 线程**，延迟最低 | 正常，别动 |
| ≈ 0.0 | 全部被丢给 pump 线程 | 说明总线上有并发（另一个设备在传，或者队列没空）。查还有谁在用这条总线 |
| 中间 | 时而快时而慢 | 抖动来源就是它 |

**这也是判断"设了 `spi->rt` 但延迟没变"的第一个检查点**：RT 优先级
只作用在 pump 线程上，如果比值 ≈ 1.0，流量根本不经过 pump，
提优先级当然没用（`03-internals.md` §B9）。

### §4.5 17 个长度直方图

```
transfer_bytes_histo_0-1    transfer_bytes_histo_2-3     transfer_bytes_histo_4-7
transfer_bytes_histo_8-15   transfer_bytes_histo_16-31   transfer_bytes_histo_32-63
transfer_bytes_histo_64-127          ... 逐级翻倍 ...
transfer_bytes_histo_32768-65535     transfer_bytes_histo_65536+
```

定义：`SPI_STATISTICS_TRANSFER_BYTES_HISTO()` `drivers/spi/spi.c:193`（实例 `:197-213`）。
分桶算法（`:316`）：

```c
	int l2len = min(fls(xfer->len), SPI_STATISTICS_HISTO_SIZE) - 1;
	if (l2len < 0) l2len = 0;
```

`len == 0` 和 `len == 1` 都落在第 0 桶（`fls(0) == 0` → `-1` → 夹到 0）。

用途：**看一眼就知道这条总线的流量形状**。

```bash
cd /sys/class/spi_master/spi0/statistics
grep . transfer_bytes_histo_* | grep -v ':0$'
```

| 形状 | 通常意味着 |
|-----|-----------|
| 集中在 `0-1` / `2-3` | 大量寄存器读写（传感器轮询）。每段的固定开销（片选、延时）占主导，优化方向是合并成多段消息 |
| 集中在 `256-511` / `4096-8191` | flash / 帧缓冲。优化方向是 DMA 和 `max_transfer_size` |
| 两头都有 | 混合负载，`transfers/messages` 会 > 2 |

---

## §5 错误码分诊

### §5.1 `drivers/spi/spi.c` 的错误码普查

```bash
for e in EINVAL ENOMEM ENODEV EBUSY EPROBE_DEFER ENOTSUPP ESHUTDOWN \
         ETIMEDOUT EINTR ENOENT EIO EOPNOTSUPP EAGAIN; do
  printf "%-14s %s\n" "-$e" "$(grep -c -- "-$e" drivers/spi/spi.c)"
done
```

| 错误码 | 出现次数 | 语义集中度 | 去哪一节 |
|-------|--------|----------|---------|
| `-EINVAL` | **37** | **极度分散**，而且大半不打日志 | **§5.2**（本篇最重要） |
| `-ENOMEM` | 16 | 单一：分配失败 | 不用分诊 |
| `-ENODEV` | 8 | 集中在注册/查找 | §5.3 |
| `-EBUSY` | 6 | 三种完全不同的原因 | §5.4 |
| `-EPROBE_DEFER` | 5 | 集中在中断号还没就绪 | §5.5 |
| `-ENOTSUPP` | 3 | slave/target 相关 | §5.6 |
| `-ESHUTDOWN` | 3 | 挂起期间访问 | §5.6 |
| `-ETIMEDOUT` | **1** | 只有一个出口 | §6 |
| `-EINTR` | 1 | 被信号打断 | §5.6 |
| `-ENOENT` | 1 | — | §5.6 |
| `-EIO` / `-EOPNOTSUPP` / `-EAGAIN` | **0** | 核心一个都不返 | 见到这三个就是控制器驱动返的 |

**最后一行很有用**：`spi_sync()` 返回 `-EIO` 时，不必在 `spi.c` 里找，
一定来自 `ctlr->transfer_one()` 或 `->exec_op()`——直接去看控制器驱动。

### §5.2 `-EINVAL`：37 个出口，20 个在 `__spi_validate()` 里且**零日志**

全部 37 行：

```bash
grep -n -- "-EINVAL" drivers/spi/spi.c | cut -d: -f1 | tr '\n' ' '
# 708 730 1062 1437 1444 1450 1464 2311 2646 2823 3040 3050 3067 3070 3182
# 3483 3643 3645 3717 3723 3745 3857 3901 3903 3905 3931 3946 3950 3963
# 3967 3970 3973 3978 3982 3985 3988 3992
```

**关键事实**：

```bash
sed -n '3845,3995p' drivers/spi/spi.c | grep -c "dev_err\|dev_warn\|dev_dbg"
# 0
```

**`__spi_validate()` 整个函数（`:3850`–`:3995`）一条日志都没有。**
它是 `spi_sync()` / `spi_async()` 的必经之路（`03-internals.md` §A5），
所以最常见的 SPI 故障现场就是：**调用返回 `-EINVAL`，`dmesg` 干净得像没发生过。**

下面这张表就是补这个缺口用的——按"你能自己检查什么"来读：

| 行 | 条件（代码原意） | 自己怎么查 |
|---|---------------|-----------|
| 3857 | `list_empty(&message->transfers)` | 消息里一段都没有。用 `spi_message_add_tail()` 之后忘了？还是 `spi_message_init()` 之后直接发了？ |
| **3901** | 同一段里既有 `tx_buf` 又有 `rx_buf`，**而链路是半双工** | 最常见的一个。见下面的 §5.2.0 |
| 3903 | 半双工链路 + 控制器有 `SPI_CONTROLLER_NO_TX` 标志，但给了 `tx_buf` | 是**控制器**标志（MOSI 引脚物理上没接出来），不是设备 mode 位 |
| 3905 | 半双工链路 + 控制器有 `SPI_CONTROLLER_NO_RX` 标志，但给了 `rx_buf` | 同上，MISO 缺失 |
| **3931** | `__spi_validate_bits_per_word()` 失败 | 两个子原因：`bits_per_word > 32`（`:3643`）或控制器的 `bits_per_word_mask` 不含这个值（`:3645`）。查法见下面的 §5.2.1 |
| **3946** | `xfer->len % w_size != 0`（`w_size` = 1/2/4，由 `bits_per_word` 决定） | 第二常见。`bits_per_word=16` 时 `len` 必须是偶数；`=32` 时必须是 4 的倍数。**`len` 是字节数不是字数** |
| 3950 | `xfer->speed_hz < ctlr->min_speed_hz` | 设的速率太**低**了。注意是低不是高——高的会被静默夹（见下） |
| 3963 | `SPI_NO_TX` 且 `xfer->tx_buf`（逐段再查一次） | 同 3903 |
| 3967 | `tx_nbits` 不是 1/2/4/8 中的一个 | 手填过 `xfer->tx_nbits`？不填的话核心会填 `SPI_NBITS_SINGLE`（`:3953`） |
| 3970 | `tx_nbits == DUAL` 但 `spi->mode` 里没有 `SPI_TX_DUAL\|SPI_TX_QUAD` | DTS 的 `spi-tx-bus-width` 和代码里的 `tx_nbits` 不一致 |
| 3973 | `tx_nbits == QUAD` 但 `mode` 里没有 `SPI_TX_QUAD` | 同上 |
| 3978 / 3982 / 3985 / 3988 | rx 侧的四条，与 3963/3967/3970/3973 一一对称 | 同上，换成 `rx_*` |
| 3992 | `_spi_xfer_word_delay_update()` 失败 | `word_delay` 的 `unit` 填了非法值（见 §5.2.2） |

**同一个循环里还有 4 处"静默改写你的参数"**（`:3919`–`:3928`），
不返错，但会让你后面看到的值和自己设的不一样：

```c
	xfer->effective_speed_hz = 0;                                    /* :3919 每次校验都清零 */
	if (!xfer->bits_per_word)
		xfer->bits_per_word = spi->bits_per_word;                 /* :3922 */
	if (!xfer->speed_hz)
		xfer->speed_hz = spi->max_speed_hz;                      /* :3925 */
	if (ctlr->max_speed_hz && xfer->speed_hz > ctlr->max_speed_hz)
		xfer->speed_hz = ctlr->max_speed_hz;                     /* :3928 静默下夹 */
```

**`:3928` 就是 `06-devicetree.md` §3.2「速率被静默下夹」在传输路径上的第二道**——
`spi_setup()` 夹一次（`:3761-3764`），每段传输再夹一次。**两道都不出声。**

#### §5.2.0 `:3901`：两个完全不同的原因都会让"全双工传输"失败

这三条（`:3901`/`:3903`/`:3905`）**共用一个入口条件**（`:3895-3896`）：

```c
	if ((ctlr->flags & SPI_CONTROLLER_HALF_DUPLEX) ||
	    (spi->mode & SPI_3WIRE)) {
```

**所以"半双工"有两个来源，症状一样但查法完全不同**：

| 来源 | 怎么确认 | 怎么改 |
|-----|--------|-------|
| 控制器驱动设了 `SPI_CONTROLLER_HALF_DUPLEX` | `grep -n "HALF_DUPLEX" drivers/spi/spi-<你的控制器>.c` | 改不了，硬件如此。**拆成两段传输**（先发后收） |
| **你的设备是 `SPI_3WIRE` 模式** | DTS 里有 `spi-3wire`（`06-devicetree.md` §3.5），或者代码里设了 `spi->mode \|= SPI_3WIRE` | 3wire 本来就只有一根数据线，同时收发在物理上不可能。同样拆两段 |

**第二种是最容易看漏的**：控制器完全是全双工的，是**你自己**在 DTS 里写了
`spi-3wire`，于是所有全双工传输一律 `-EINVAL`，而且**一句日志都没有**。

源码注释（`:3889-3894`）把这个设计意图说清楚了：

> Half-duplex links include original MicroWire, and ones with only one data
> pin like SPI_3WIRE (switches direction) or where either MOSI or MISO is
> missing.

#### §5.2.1 `bits_per_word` 被拒的查法

```bash
# 控制器支持哪些字长？mask 的第 (n-1) 位为 1 表示支持 n 位
grep -n "bits_per_word_mask" drivers/spi/spi-<你的控制器>.c
```

`SPI_BPW_MASK(n)` = `BIT(n-1)`，`SPI_BPW_RANGE_MASK(a,b)` 是区间。
**`bits_per_word_mask == 0` 表示"不限制"**（`:3640` 的 `if` 直接跳过），
不是"什么都不支持"——这个反直觉的语义每次都会绕人。

#### §5.2.2 `spi_delay` 的三个 `-EINVAL`

`spi_delay_to_ns()` / `spi_delay_exec()`：

| 行 | 条件 |
|---|-----|
| 1437 | `unit == SPI_DELAY_UNIT_SCK` 但 `xfer == NULL`（时钟周期数需要 xfer 才能换算） |
| 1444 | `unit == SCK` 但 `effective_speed_hz` 和 `speed_hz` 都是 0 → 除零保护 |
| 1450 | `unit` 是个未知值（`default:` 分支）。**`spi_delay.unit` 忘了赋值就是 0 = `SPI_DELAY_UNIT_USECS`，反而合法**——所以这条只会在填了垃圾值时命中 |
| 1464 | `spi_delay_exec(NULL, ...)` |

注意 `:1442` 用的是 `xfer->effective_speed_hz ?: xfer->speed_hz / 2`——
**没有实测速率时按"请求速率的一半"保守估**（注释在 `:1438-1441`）。所以
`SPI_DELAY_UNIT_SCK` 的延时在不同控制器上实际长度可以差一倍。

### §5.3 `-ENODEV`：8 个出口，全在"找不到"路径

| 行 | 场合 |
|---|-----|
| 64 | `spi_dev_uevent` / 属性路径的防御 |
| 404 | `spi_register_driver()` 的前置检查 |
| **658** | `__spi_add_device()`：控制器已经在拆除中 |
| 2562 / 2566 / 2572 | `of_register_spi_device()` 系列：节点无效 |
| 2670 | ACPI 路径（arm64 不涉及） |
| **3107** | `spi_alloc_device()` 之后 `spi_controller_get()` 失败——控制器正在消失 |

**`:658` 和 `:3107` 都指向同一类竞态**：一边 `rmmod` 控制器驱动一边
创建设备。看到 `-ENODEV` 先看有没有并发的 unbind。

### §5.4 `-EBUSY`：三种完全不同的原因

| 行 | 原因 | 有日志吗 | 怎么区分 |
|---|-----|---------|---------|
| **627** | 片选号已被占用（`spi_dev_check()`） | ✓ `chipselect %d already in use` `:650` | 查 DTS 有没有两个子节点写了同一个 `reg`。只在**同一控制器**内冲突（`:625` 按 controller 过滤），见 `06-devicetree.md` §3.1 |
| **2120** | `spi_stop_queue()` 等了 5 秒队列还没空 | ✗ **无日志** | 见 §6.2。发生在 unbind / suspend 时 |
| 2087 | `spi_start_queue()`：队列已经在跑 | ✗ **无日志** | 重复 start，通常是驱动自己的状态机错了 |
| 3124 / 3136 | `bus_num` 分配失败（`-ENOSPC` 被翻成 `-EBUSY`） | ✓ 注册失败会打 | DTS 里 `aliases` 指定的 bus 号撞了 |
| **4073** | `spi_async()` 时总线被 `spi_bus_lock()` 锁住 | ✗ **无日志** | 有人在用 `spi_bus_lock()` 做独占（`01-api.md` §5）。异步接口在锁期间**一律**被拒，同步接口会等 |

### §5.5 `-EPROBE_DEFER`：只有一个真实原因

| 行 | 场合 |
|---|-----|
| 422 / 423 / 432 / 433 | `spi_get_device_id()` 路径 / 中断号还没就绪 |
| 2576 | `of_register_spi_device()`：`irq` 解析返回 `-EPROBE_DEFER` |

**这不是错误**，是"中断控制器还没注册，晚点再来"。表现为设备一直不出现在
`/sys/bus/spi/devices/`。查法：

```bash
cat /sys/kernel/debug/devices_deferred      # 需要 CONFIG_DEBUG_FS
```

### §5.6 剩下的几个

| 错误码 | 行 | 含义 |
|-------|---|------|
| `-ENOTSUPP` | 2784 / 2795 | `spi_target_abort()`：控制器没实现 `target_abort` 回调。全树没有一个控制器实现它（`04-extensions.md` §4） |
| `-ENOTSUPP` | **4010** | `__spi_async()`：`ctlr->transfer == NULL`。走队列的控制器由核心填这个回调（`03-internals.md` §A4），所以只有**纯 `mem_ops` 控制器**（只支持 SPI-NOR 命令、不支持普通传输）会命中。注释原文在 `:4005-4008` |
| `-ESHUTDOWN` | **2166** | `__spi_queued_transfer()`：`ctlr->running == false`。队列已经被 `spi_stop_queue()` 停了（见 §6.2），后来的消息直接拒 |
| `-ESHUTDOWN` | 3341 | `__spi_check_suspended()` 这个 inline 助手的返回值本身（`:3339-3342`），**不是一个独立故障点**——全树只有 `:4184` 一个调用者 |
| `-ESHUTDOWN` | **4186** | `spi_sync()` 在挂起期间被调用（`:4184` 判定）。警告只打一次（`:4185`，见 §2.3），**错误码每次都返** |
| `-EINTR` | **1364** | **只有 slave/target 侧会返**——`:1361` 的 `if (spi_controller_is_slave(ctlr))` 里才用可打断的等待（`wait_for_completion_interruptible()` `:1362`）。master 侧走 `:1387` 的定时等待，永远不返 `-EINTR`。同一处有 `dev_dbg` `SPI transfer interrupted`（`:1363`，§1.3） |

---

## §6 超时

### §6.1 传输超时：预算是**理论时间的 2 倍 + 200 ms**

`__spi_pump_transfer_message()`，`drivers/spi/spi.c:1361-1396`：

```c
		if (!speed_hz)
			speed_hz = 100000;                       /* :1367-1368 兜底 100kHz */

		ms = 8LL * MSEC_PER_SEC * xfer->len;
		do_div(ms, speed_hz);
		ms += ms + 200;                                  /* :1376-1383 翻倍再加 200ms */

		if (ms > UINT_MAX)
			ms = UINT_MAX;

		ms = wait_for_completion_timeout(&ctlr->xfer_completion,
						 msecs_to_jiffies(ms));   /* :1387 */
```

| 事实 | 依据 | 含义 |
|-----|-----|------|
| 公式 = `8 × 1000 × len / speed_hz`，**乘 2**，再 **+200 ms** | `:1376-1383` | 8 = 每字节 8 个时钟。乘 2 是给控制器的调度/中断延迟留余量 |
| `speed_hz == 0` 时按 **100 kHz** 算 | `:1367-1368` | 一个没设速率的 4 字节传输，预算 = 8×1000×4/100000 = 0.32ms → ×2+200 ≈ **200 ms** |
| 超时后 `timedout` 统计 +1 | `:1391` / `:1392` | 两套统计都加，见 §4.3 |
| 打印 `SPI transfer timed out` | `:1393-1394`，`dev_err` | **始终输出**，不是 `_once` |
| 返回 `-ETIMEDOUT` | `:1395` | 核心里**唯一**的 `-ETIMEDOUT` 出口 |

**这套超时只在 master 侧生效。** `:1361` 的 `if (spi_controller_is_slave(ctlr))`
把 slave 侧分流到 `wait_for_completion_interruptible()`（`:1362`）——
**slave 永远不会超时，只会被信号打断**（返 `-EINTR`，见 §5.6）。
道理很直白：从设备等的是主设备什么时候来时钟，没有时限可言。

**实践含义**：这个预算非常宽松（2 倍余量）。**能超时说明控制器基本没在动**——
不是"慢了一点"，而是中断没上来、时钟没开、DMA 卡住、片选没释放。
去查的顺序是：clk 是否 enable → 中断计数（`/proc/interrupts`）→ 控制器驱动的
`dev_dbg`（§1.3）。

### §6.2 另一个 5 秒超时：`spi_stop_queue()`

`spi_stop_queue()`（`drivers/spi/spi.c:2099`）：

```c
	unsigned limit = 500;                                            /* :2102 */
	...
	while ((!list_empty(&ctlr->queue) || ctlr->busy) && limit--) {    /* :2113 */
		spin_unlock_irqrestore(&ctlr->queue_lock, flags);
		usleep_range(10000, 11000);                               /* :2115 */
		spin_lock_irqsave(&ctlr->queue_lock, flags);
	}
```

源码里的自评（`:2107-2112` 原文第一句）：

> This is a bit lame, but is optimized for the common execution path.

| 事实 | 依据 |
|-----|-----|
| 最多 500 次 × 10 ms = **约 5 秒** | `:2102` + `:2115` |
| 失败返回 `-EBUSY`，**不打任何日志** | `:2119-2120` |
| 触发场合 | `rmmod` 控制器驱动、系统挂起、`spi_unregister_controller()` |

**症状**：`rmmod` 卡住 5 秒然后失败，或者 suspend 卡住 5 秒。
**dmesg 里可能什么都没有**——错误码往上传给了 unbind/suspend 框架。
查法：`echo t > /proc/sysrq-trigger` 看 pump 线程栈在哪。

---

## §7 用户态工具

### §7.1 `spidev` + `tools/spi/spidev_test.c`

前提：`CONFIG_SPI_SPIDEV=m/y`，且 DTS 里的 `compatible` 在
`drivers/spi/spidev.c` 的白名单里（`:729` 一带，不能写 `"spidev"`，
会被 `:719` 拒掉——见 `06-devicetree.md` §5.3）。

```bash
# 编译（在内核源码树里）
make -C tools/spi

# 自发自收（MOSI 短接 MISO）
./spidev_test -D /dev/spidev0.0 -v -p "AA BB CC DD"

# 常用开关
#   -s <hz>   速率      -b <n>  bits_per_word
#   -H        CPHA=1    -O      CPOL=1（对应 SPI_MODE_x）
#   -L        LSB first -3      3wire      -N  no CS
#   -v        打印收发的十六进制
```

**`-v` 的输出和 §3.5 的 tracepoint 是互相印证的两个视角**：
`spidev_test -v` 看到的是**用户态缓冲区**，`spi_transfer_stop` 看到的是
**核心交给控制器的缓冲区**。两者不一致 → 问题在 `spidev` 的 ioctl 层；
两者一致但外设没反应 → 问题在控制器或线路。

### §7.2 `tools/spi/spidev_fdx.c`

比 `spidev_test` 小得多，专门演示**半双工 vs 全双工**两种 ioctl 用法：

```bash
./spidev_fdx /dev/spidev0.0        # 半双工：先写后读，两次 SPI_IOC_MESSAGE
./spidev_fdx -m 8 /dev/spidev0.0   # 全双工：一次 SPI_IOC_MESSAGE(2)，8 字节
```

用途：**验证 §5.2 表里 `:3901` 那条**。如果控制器是半双工的，
`-m` 那条会直接返 `-EINVAL`，而不带 `-m` 的能过——这是从用户态区分
"控制器是不是半双工"最快的办法。

### §7.3 `spi-loopback-test`：没有外设也能测通路

`CONFIG_SPI_LOOPBACK_TEST=m`，内核模块（不是用户态程序），但用 `modprobe`
参数驱动，用起来像工具。**15 个测试用例**，覆盖零长度、单字节、跨页、
超 `max_transfer_size`、vmalloc 缓冲、多段消息等。

```bash
# 关键技巧：用 compatible= 指定要测哪条总线上的哪个设备
modprobe spi-loopback-test compatible=rohm,dh2228fv
dmesg | tail -60

# 只跑某一个用例（编号见 dmesg 里的 "Running test %s"）
modprobe spi-loopback-test run_only_test=5
```

全部 9 个参数（`grep -n "module_param" drivers/spi/spi-loopback-test.c`）：

| 模块参数 | 权限 | 定义行 | 说明 |
|--------|-----|-------|------|
| `compatible` | **0000** | 379 | `module_param_string()`，直接改写 `spi_loopback_test_of_match[0].compatible`。**权限 0000 = sysfs 里连看都看不到**，只能 `modprobe` 时给。默认 `"loopback"` |
| `simulate_only` | 0 | 27 | 不真的执行 `spi_message`，只跑用例构造流程 |
| `dump_messages` | 0 | 32 | 三档：`1` = 只 dump `spi_message` 结构，`2` = 连数据一起，`3` = 执行前后各 dump 一次 |
| `loopback` | 0 | 39 | 声明"外部已用跳线短接 MOSI/MISO"，于是执行后校验 `rx_buf == tx_buf` |
| `loop_req` | 0 | 46 | 要求**控制器**打开内部回环（`SPI_LOOP`）；支持的话 MISO/MOSI 在芯片内部连起来 |
| `no_cs` | 0 | 52 | 不使用片选 |
| `run_only_test` | 0 | 58 | 只跑第 N 个用例。**0-based**，默认 `-1` = 全跑 |
| `use_vmalloc` | **0644** | 64 | 运行时可改：用 vmalloc 缓冲代替 kmalloc，测 DMA 映射的分散页路径 |
| `check_ranges` | **0644** | 70 | 运行时可改，**默认就是 1**：校验 `rx_buf` 的填充模式是否合法（抓越界写） |

**9 个参数里只有 `use_vmalloc` 和 `check_ranges` 是 0644**（运行时可改），
其余 6 个是 `0`（sysfs 不可见），`compatible` 是 `0000`——改了都要重新
`modprobe`。细节和用例清单见 `04-extensions.md` §6。

**`loopback` 和 `loop_req` 是两件事，最容易混**：`loopback=1` 是"我在外面
用线把 MOSI 和 MISO 短接了"，`loop_req=1` 是"要求控制器自己有内部回环
（`SPI_LOOP` mode bit）"。前者不需要控制器支持，后者需要。

**它测的是"核心 + 控制器驱动"这一段，不测外设。**
所以它过了不代表你的传感器能通；它不过就一定是核心或控制器的问题——
**这个方向性是它最大的价值**。

---

## §8 一页速查

```
【第 0 步：确认设备存在】
  ls /sys/bus/spi/devices/                    # 有 spiB.C 吗？
  ls -l /sys/bus/spi/devices/spi0.0/driver    # 绑上驱动了吗？
  cat /sys/kernel/debug/devices_deferred      # 没有 → 是不是 -EPROBE_DEFER？§5.5

【第 1 步：看有没有在动】
  cd /sys/class/spi_master/spi0/statistics
  grep . messages transfers errors timedout
  # messages == 0        → 根本没发起过，问题在上层
  # errors > 0           → §5 分诊
  # timedout > 0         → §6
  # 只有 messages 在涨   → 传输在跑，问题在数据内容

【第 2 步：看走的哪条路】
  grep . spi_sync spi_sync_immediate transfers_split_maxsize
  # immediate/sync ≈ 0   → 都走 pump 线程，§4.4
  # split_maxsize > 0    → 在被拆段

【第 3 步：看内容】
  trace-cmd record -e spi:spi_message_submit -e spi:spi_transfer_stop \
                   -e spi:spi_message_done
  trace-cmd report        # spi_transfer_stop 里有前 64 字节，§3.5
  # 不要看 spi_transfer_start 的 rx，那是脏数据

【第 4 步：看参数定成了什么】
  trace-cmd record -e spi:spi_setup      # 然后触发一次 setup
  # mode / bits_per_word / max_speed_hz 的最终值，§3.3
  # 这里的 max_speed_hz 是已经被夹过的值（:3761-3764）

【第 5 步：还是不行，隔离核心与控制器】
  modprobe spi-loopback-test compatible=<你的 compatible>   # §7.3
  # 过了 → 问题在外设/线路；不过 → 问题在核心或控制器驱动

【症状 → 直达】
  返回 -EINVAL 且 dmesg 空白 ........ §5.2（20 个零日志出口）
  len 是奇数 / bits_per_word=16 ...... §5.2 的 :3946
  同时给了 tx_buf 和 rx_buf 就失败 ... §5.2 的 :3901（半双工控制器）
  速率和设的不一样 .................. §5.2 的 :3928 + §3.3
  SPI transfer timed out ............ §6.1（预算很宽，是硬件没动）
  rmmod / suspend 卡 5 秒 ........... §6.2（无日志）
  挂起后所有传输失败 ................ §5.6 的 -ESHUTDOWN
  片选行为不对 ...................... §3.4 +『06-devicetree.md』§4
  想看真实速率 ...................... tracepoint 里没有，§3.6
  /sys/kernel/debug/spi/ 找不到 ..... §1.2（它不存在）
```

### §8.1 三个"别去找"的地方

| 想找的东西 | 为什么找不到 | 依据 |
|----------|-----------|------|
| `/sys/kernel/debug/spi/` | SPI 核心没有 debugfs，`Kconfig` 帮助文本骗人 | §1.2 |
| tracepoint 里的 `effective_speed_hz` | 没有导出这个字段 | §3.6 |
| 故障注入接口（`fail_spi` 之类） | SPI 核心**没有**故障注入。要模拟失败只能改控制器驱动，或用 `spi-loopback-test` | `grep -rn "fault_inject\|should_fail" drivers/spi/` 为空 |

---
