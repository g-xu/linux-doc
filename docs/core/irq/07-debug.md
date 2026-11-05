---
title: 调试方法
---

# irq 07：调试手段

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径。所有统计都限定在 **`kernel/irq/*.c`**（**22 个 `.c`**、
> 14294 行；`kernel/irq/` 连 3 个 `.h` 一共 15044 行），不含 `drivers/irqchip/` 的 129 个驱动。每条统计都给了可重跑的命令。

---

## §0 按症状找章节

| 症状 | 去哪一节 |
|---|---|
| 我想先把该开的编译选项开全 | §1 |
| dmesg 里什么都没打，想加日志 | §2 |
| 我想用 ftrace 看中断延迟 / 谁在刷中断 | §3 |
| 我要看当前状态 / 层级域 / 四组状态位 | §4 |
| 我想手工触发一次中断验证通路 | §5 |
| 拿到一个错误码，想知道是哪一行返回的 | §6 |
| 我想统计中断频率 / 找出刷屏的那条 | §7 |
| 给我一页速查 | §8 |

---

## §1 编译期开关

### §1.1 三个必开的调试选项

| 选项 | 位置 | 没开的后果 | 怎么确认 |
|---|---|---|---|
| `GENERIC_IRQ_DEBUGFS` | `kernel/irq/Kconfig:128` | **`/sys/kernel/debug/irq/` 整个不存在** ⇒ 看不到层级域、看不到状态位、不能注入 | `ls /sys/kernel/debug/irq/` |
| `GENERIC_IRQ_SHOW` | `kernel/irq/Kconfig:18` | `/proc/interrupts` 只有计数，没有 chip 名字/hwirq/handler 名字 | `cat /proc/interrupts \| head -3` |
| `GENERIC_IRQ_EFFECTIVE_AFF_MASK` | `kernel/irq/Kconfig:26` | 没有 `effective_affinity` 文件 ⇒ 分不清"要求的"和"实际的" | `ls /proc/irq/1/` |

⚠️ **`GENERIC_IRQ_DEBUGFS` 默认不开**（Kconfig 帮助文本明确说是给内核开发者的）。
发行版内核通常没有 ⇒ **调 irq 问题的第一步是自己编一个开了它的内核**。

### §1.2 其它相关选项

| 选项 | 位置 | 作用 |
|---|---|---|
| `GENERIC_IRQ_SHOW_LEVEL` | `kernel/irq/Kconfig:22` | `/proc/interrupts` 多一列 `Level`/`Edge` |
| `GENERIC_IRQ_INJECTION` | `kernel/irq/Kconfig:43` | `irq_inject_interrupt()` + debugfs 的 `trigger`（§5） |
| `HARDIRQS_SW_RESEND` | `kernel/irq/Kconfig:47` | 上面那个的底层实现（04 §11） |
| `IRQ_FORCED_THREADING` | `kernel/irq/Kconfig:112` | `threadirqs` 命令行参数 |
| `DEBUG_SHIRQ` | 在 `lib/Kconfig.debug:1000` | ⚠️ **只在 `free_irq()` 路径上有效** —— `request_irq()` 那一半是死代码（03 §C1.3） |
| `IRQSOFF_TRACER` | `kernel/trace/Kconfig` | 测"中断关闭时长"的最大值 |

### §1.3 外部依赖的三个

| 选项 | 没开的后果 |
|---|---|
| `CONFIG_DYNAMIC_DEBUG` | **23 处 `pr_debug` 全部编译成空**（§2.2） |
| `CONFIG_DEBUG_FS` | §4 那一整套不存在 |
| `CONFIG_FTRACE` / `CONFIG_TRACEPOINTS` | §3 那一整套不可用 |

---

## §2 日志：可见性等级与分布

### §2.1 这个子系统靠 `WARN` 说话，不靠 `pr_err`

| 类别 | 次数 | 说明 |
|---|---|---|
| `WARN_ON(…)` | **76** | 打栈回溯，不带自定义消息 |
| `WARN(cond, "…")` | **19** | 打栈回溯 + 自定义消息 |
| `WARN_ON_ONCE(…)` | **28** | 只打一次 |
| `WARN_ONCE(cond, "…")` | 3 | 只打一次 + 消息 |
| **WARN 家族合计** | **126** | —— |
| `pr_err` | **14** | —— |
| `pr_warn` | 18 | —— |
| `pr_warn_once` | 1 | —— |
| `pr_info` | 8 | —— |
| `pr_debug` | **23** | 需要动态调试才可见（§2.2） |
| `_ratelimited` | **2** | 都在 `kernel/irq/cpuhotplug.c` |

```bash
for k in pr_err pr_warn pr_info pr_debug WARN_ON_ONCE WARN_ONCE pr_warn_once WARN_ON 'WARN(' _ratelimited; do
  printf "%-18s %s\n" "$k" "$(grep -roh "$k" kernel/irq/*.c | wc -l)"
done
```

**WARN 家族 126 次 vs `pr_err` 14 次** —— 这个比例是本子系统的定性：
**irq 核心认为"出错就是内核 bug，应该打栈回溯"**，
而不是"返回错误码让调用者处理"。
⇒ ⚠️ **看到 irq 相关的 `WARNING:` 栈回溯，不要当成噪声** ——
栈顶那个函数就是"谁在错误地使用 irq API"。

### §2.2 `pr_debug` 分布（打开动态调试后能看到的）

| 文件 | `pr_debug` 数 |
|---|---|
| `kernel/irq/irqdomain.c` | **13** |
| `kernel/irq/timings.c` | 7 |
| `kernel/irq/cpuhotplug.c` | 2 |
| `kernel/irq/manage.c` | 1 |
| **合计** | **23** |

```bash
for f in kernel/irq/*.c; do n=$(grep -c pr_debug $f); [ "$n" != 0 ] && echo "$n $f"; done
```

**13 处集中在 `irqdomain.c`，这不是巧合** ——
域和映射是最容易配错的地方（06 §8.4）。
⇒ **调"中断映射不对"的标准第一步**：

```sh
echo 'file irqdomain.c +p' > /sys/kernel/debug/dynamic_debug/control
# 然后重新 probe 你的驱动（rmmod/insmod 或 unbind/bind）
echo <设备名> > /sys/bus/platform/drivers/<驱动名>/unbind
echo <设备名> > /sys/bus/platform/drivers/<驱动名>/bind
dmesg | tail -40
```

其它三个有用的开法：
```sh
C=/sys/kernel/debug/dynamic_debug/control
echo 'file manage.c +p'     > $C     # 申请/释放/亲和性
echo 'file cpuhotplug.c +p' > $C     # CPU 上下线时的中断迁移
echo 'func irq_domain_associate_locked +p' > $C   # 只看映射建立
```

### §2.3 五条最该认识的 `WARN` 消息

| 消息 | 位置 | 含义 |
|---|---|---|
| `irq %u handler %pS enabled interrupts` | `kernel/irq/handle.c:161` | **你的 handler 里开了中断**（05 §1.4） |
| `Trying to free IRQ %d from IRQ context!` | `kernel/irq/manage.c:1872` | **在中断上下文调了 `free_irq()`**（03 §A8） |
| `Unbalanced enable for IRQ %d` | `kernel/irq/manage.c` 的 `__enable_irq()` | `enable_irq()` 多调了（01 §2.1） |
| `WARN` 在 `gic_irq_domain_translate` 附近 | `drivers/irqchip/irq-gic-v3.c:1578-1579` | **DTS 的触发类型写了 0**（06 §3.1） |
| `WARN_ON(!rproc…)` 类的 `WARN_ON` 无消息版 | 76 处 | 只能看栈回溯定位 |

`kernel/irq/manage.c` 里 `WARN(` 有 **12 处**、`WARN_ON` 有 **19 处** ——
它是 WARN 密度最高的文件（因为它是消费者 API 的入口，负责挡住所有误用）。

```bash
grep -c 'WARN(' kernel/irq/manage.c      # → 12
grep -c 'WARN_ON' kernel/irq/manage.c    # → 19
```

### §2.4 `printk` 里唯一的"给用户的建议"

`kernel/irq/spurious.c:202`：

```
irq %d: nobody cared (try booting with the "irqpoll" option)
```

⚠️ **arm64 上不要照着这句话做**（04 §4.3）——
`irqpoll` 是给 x86 老 PCI 硬件设计的。
arm64 上这句话的真实含义是"**某个共享中断的 handler 返回值错了**"（01 §1.3）。

---

## §3 tracepoint：五个（+12 个 x86 专用）

### §3.1 全表

| tracepoint | 定义位置 | 参数 |
|---|---|---|
| `irq:irq_handler_entry` | `include/trace/events/irq.h:53` | `irq`、`name`（= `action->name`） |
| `irq:irq_handler_exit` | `include/trace/events/irq.h:83` | `irq`、`ret`（`handled` / `unhandled`） |
| `irq:softirq_entry` | `include/trace/events/irq.h:128` | `vec`（softirq 号） |
| `irq:softirq_exit` | `include/trace/events/irq.h:142` | 同上 |
| `irq:softirq_raise` | `include/trace/events/irq.h:156` | 同上 |
| `irq_matrix:*`（**12 个**） | `include/trace/events/irq_matrix.h` | **x86 专用**（04 §9），arm64 上不会触发 |

```bash
ls include/trace/events/ | grep -i irq          # → irq.h · irq_matrix.h · preemptirq.h
grep -c 'DEFINE_EVENT\|TRACE_EVENT' include/trace/events/irq_matrix.h   # → 12
```

**零 `TP_CONDITION`**：
```bash
grep -c TP_CONDITION include/trace/events/irq.h   # → 0
```
⇒ **这五个 tracepoint 一旦启用就无条件触发**，没有隐藏的过滤条件。

### §3.2 `irq_handler_entry/exit` 的四个触发点

| 位置 | 宿主流处理器 |
|---|---|
| `kernel/irq/handle.c:157` / `:159` | **`__handle_irq_event_percpu()` —— 绝大多数中断走这里** |
| `kernel/irq/chip.c:748` / `:753` | `handle_fasteoi_nmi()`（`kernel/irq/chip.c:739`） |
| `kernel/irq/chip.c:929` / `:931` | `handle_percpu_devid_irq()`（`kernel/irq/chip.c:912`） |
| `kernel/irq/chip.c:964` / `:966` | `handle_percpu_devid_fasteoi_nmi()`（`kernel/irq/chip.c:955`） |

```bash
grep -rn 'trace_irq_handler_entry\|trace_irq_handler_exit' kernel/irq/*.c
```

⚠️ **`handle_percpu_irq()`（`kernel/irq/chip.c:882`）没有 tracepoint** ——
它是"不共享、不统计"的极简路径（01 §4.3）。
⇒ 用它的中断**在 ftrace 里看不见**。

### §3.3 常用的三种玩法

**① 看某条中断的处理时长**
```sh
cd /sys/kernel/tracing
echo 1 > events/irq/irq_handler_entry/enable
echo 1 > events/irq/irq_handler_exit/enable
echo 'irq==42' > events/irq/irq_handler_entry/filter
echo 'irq==42' > events/irq/irq_handler_exit/filter
cat trace_pipe
```

**② 找出"谁在刷中断"（不知道是哪条时）**
```sh
cd /sys/kernel/tracing
echo 1 > events/irq/irq_handler_entry/enable
echo > trace
sleep 1
echo 0 > events/irq/irq_handler_entry/enable
# 按 name 统计
awk '{for(i=1;i<=NF;i++) if($i ~ /^name=/) print $i}' trace | sort | uniq -c | sort -rn | head
```

**③ 统计每条中断的处理时长直方图（histogram trigger）**
```sh
cd /sys/kernel/tracing
echo 'hist:keys=irq:vals=hitcount' > events/irq/irq_handler_entry/trigger
cat events/irq/irq_handler_entry/hist
```

`irq_handler_exit` 的 `ret` 字段只有两个值（`handled` / `unhandled`，
`include/trace/events/irq.h:83` 那个 `TP_printk`）——
⇒ **它是排查 `nobody cared` 的最直接工具**：
```sh
echo 'ret==0' > events/irq/irq_handler_exit/filter    # 只看返回 IRQ_NONE 的
```

### §3.4 没有 tracepoint 的地方用 function tracer

irq 的映射建立、亲和性变更、域分配**都没有 tracepoint**。

```sh
cd /sys/kernel/tracing
echo 'irq_domain_*' > set_ftrace_filter
echo 'irq_set_affinity*' >> set_ftrace_filter
echo '__setup_irq' >> set_ftrace_filter
echo function_graph > current_tracer
echo 1 > tracing_on
```

想测"中断关闭时长"（实时性问题）用专门的 tracer：
```sh
echo irqsoff > current_tracer      # 需要 CONFIG_IRQSOFF_TRACER
cat trace | head -30
```

---

## §4 状态查看

### §4.1 `/proc/interrupts`：一张大表

```
            CPU0       CPU1       CPU2       CPU3
   1:          0          0          0          0     GICv3  25 Level     vgic
  11:      12345      11111       9999       8888     GICv3  27 Level     arch_timer
  38:        142          0          0          0     GICv3 196 Level     bam_dma
  ▲          ▲                                        ▲     ▲   ▲         ▲
  virq    每 CPU 的计数                              chip  hwirq 触发类型  handler 名字
```

| 列 | 来自 | 位置 |
|---|---|---|
| virq | 行号 | `kernel/irq/proc.c:499` |
| 每 CPU 计数 | `desc->kstat_irqs` | `kernel/irq/proc.c:500-502` |
| chip 名字 | `chip->irq_print_chip` 优先，否则 `chip->name` | `kernel/irq/proc.c:505-514` |
| **hwirq** | `desc->irq_data.hwirq`，**只有 `irq_data.domain` 非空才打** | `kernel/irq/proc.c:515-518` |
| `Level` / `Edge` | 需要 `GENERIC_IRQ_SHOW_LEVEL` | `kernel/irq/proc.c:519-521` |
| `-<desc->name>` | 流处理器的名字（如果设了） | `kernel/irq/proc.c:522-523` |
| handler 名字（逗号分隔多个） | 遍历 `desc->action` 链 | `kernel/irq/proc.c:525-530` |
| 表头 | 只在 `i == 0` 时打 | `kernel/irq/proc.c:476-484` |
| 架构补充行（IPI / 异常计数） | `arch_show_interrupts()` | `kernel/irq/proc.c:472-473` |

**hwirq 那一列（`kernel/irq/proc.c:516`）是把 DTS 和现实对起来的最快办法**（06 §8.2）：
DTS 写 `<GIC_SPI 164 …>` ⇒ 这一列应该是 **196**。

### §4.2 ⚠️ 两种"看不见"的中断

| 情况 | 判据 | 位置 |
|---|---|---|
| `IRQ_HIDDEN` 置位 | 驱动主动隐藏 | `kernel/irq/proc.c:488` 的 `irq_settings_is_hidden()` |
| **级联入口且计数为 0** | `irq_desc_is_chained(desc) && !any_count` | `kernel/irq/proc.c:496-497` |

⇒ **级联入口在 `/proc/interrupts` 里通常不显示** ——
所以"我的 GPIO 控制器挂在哪条 SPI 上"这个问题**不能靠 `/proc/interrupts` 回答**，
要看 debugfs 的域视图（§4.3）。

还有第三种：`irq_to_desc(i)` 为空（sparse 模式下那个 virq 根本没分配）。

### §4.3 debugfs：三个视图

```sh
mount -t debugfs none /sys/kernel/debug 2>/dev/null
cd /sys/kernel/debug/irq

# ① 所有域的一览
cat domains/default

# ② 单个域的详情 —— 会一路递归打印到最顶层的父域
ls domains/
cat domains/'irqchip@0000000017a00000'

# ③ 单条中断：四组状态位 + 每一层的 chip/hwirq/domain
cat irqs/42
```

| 视图 | 实现 | 位置 |
|---|---|---|
| ① + ② | `irq_domain_debug_show()` | `kernel/irq/irqdomain.c:1965` |
| ② 的递归 | `irq_domain_debug_show_one()` | `kernel/irq/irqdomain.c:1949`，递归那句在 `kernel/irq/irqdomain.c:1961` |
| ③ | `irq_debug_show()` | `kernel/irq/debugfs.c:162` |
| ③ 里的四组位表 | `irqchip_flags[]` / `irqdata_states[]` / `irqdesc_states[]` / `irqdesc_istates[]` | `kernel/irq/debugfs.c:50` / `:100` / `:137` / `:149` |
| ③ 里的亲和性掩码 | `irq_debug_show_masks()` | `kernel/irq/debugfs.c:30`（非 SMP 时空实现，`kernel/irq/debugfs.c:47`） |

**视图 ③ 是唯一能同时看到这四组东西的地方**：
`IRQ_*`（desc 状态）· `IRQD_*`（运行时状态）· `IRQS_*`（核心内部状态）·
`IRQCHIP_*`（chip 能力）—— 02 §6 那四组 flag 全在这里译成了人话。

**视图 ③ 还会沿 `parent_data` 逐层打印**（02 §5.2）：
一个 MSI 中断你会看到两层（ITS + GIC v3），每层的 `hwirq` 和 `chip` 都不同。

### §4.4 `/proc/irq/N/` 的三组亲和性文件

| 文件 | 语义 | 04 §5.2 |
|---|---|---|
| `smp_affinity` / `smp_affinity_list` | **你要求的**（RW） | —— |
| `effective_affinity` / `effective_affinity_list` | **硬件实际在往哪送**（RO） | —— |
| `affinity_hint` | **驱动给 irqbalance 的建议**（RO，不影响硬件） | —— |
| `node` | NUMA 节点 | —— |
| `spurious` | 乱中断统计 | 实现在 `kernel/irq/proc.c:288` |

**`cat /proc/irq/N/spurious` 是查 `nobody cared` 的第一站**：
它会打出 `count`（总数）、`unhandled`（未处理数）、`last_unhandled`（上次未处理的时间戳）。

---

## §5 故障注入：`echo trigger`

### §5.1 唯一的入口

```sh
echo trigger > /sys/kernel/debug/irq/irqs/42
```

| 环节 | 位置 |
|---|---|
| debugfs 写入解析 | `kernel/irq/debugfs.c:194` |
| `strncmp(buf, "trigger", size)` | `kernel/irq/debugfs.c:205` |
| 调 `irq_inject_interrupt()` | `kernel/irq/debugfs.c:206` |
| ⚠️ 认不出的字符串**静默成功** | `kernel/irq/debugfs.c:211` |
| `irq_inject_interrupt()` | `kernel/irq/resend.c:155` |
| 它调 `check_irq_resend(desc, true)` | `kernel/irq/resend.c:178` |
| 先试硬件重发 | `kernel/irq/resend.c:89` 的 `try_retrigger()` |
| 否则软件重发（tasklet） | `kernel/irq/resend.c:50` |

**前置条件**：`CONFIG_GENERIC_IRQ_INJECTION`（`kernel/irq/Kconfig:43`），
它依赖 `HARDIRQS_SW_RESEND`（`kernel/irq/Kconfig:47`）。

### §5.2 它能把问题一刀切成两半

| 注入后 handler 被调了 | 结论 |
|---|---|
| **是** | 核心 → handler 这一段通 ⇒ 问题在**硬件到中断控制器**（DTS 的 hwirq 错、外设没使能中断输出、板级接线） |
| **否** | 问题在**核心侧**（handler 没注册、中断被 disable、亲和性指向下线 CPU） |

验证 handler 有没有被调：注入前后对比 `/proc/interrupts` 的计数，
或者开 `irq:irq_handler_entry` tracepoint（§3.3）。

### §5.3 ⚠️ 三个限制

| 限制 | 说明 |
|---|---|
| per-CPU 中断不能注入 | `irq_inject_interrupt()` 里会拒绝 |
| 软件重发走 tasklet | ⇒ handler 在 **softirq 上下文**跑，不是硬中断上下文（04 §11） |
| ⚠️ 写错字符串没有提示 | `kernel/irq/debugfs.c:211` 直接 `return count` |

### §5.4 另一条注入路径：`DEBUG_SHIRQ`

`CONFIG_DEBUG_SHIRQ`（`lib/Kconfig.debug:1000`）在 `free_irq()` 时
**假触发一次 handler**，验证"驱动在摘掉 handler 的过程中还能正确处理中断"。

⚠️ 但它在 `request_irq()` 那一半**是死代码** —— 03 §C1.3 详细记录了：
`kernel/irq/manage.c:2206` 用的 config 名是 `CONFIG_DEBUG_SHIRQ_FIXME`，
**全树没有这个 Kconfig 项**。
⇒ 所以"申请后立刻假触发一次"这个能力**在 6.1 上不存在**，不要指望它。

---

## §6 错误码分诊

### §6.1 全量普查

`kernel/irq/*.c` 里 `-E*` 一共出现 **208** 次：

| 错误码 | 出现次数 | 说明 |
|---|---|---|
| `-EINVAL` | **121**（58%） | 参数不合法 / flags 冲突 / virq 不存在 / 状态不对 —— **信息量最低** |
| `-ENOMEM` | 33 | 分配 `irq_desc` / `irqaction` / cpumask / 域失败 |
| `-EBUSY` | 14 | **共享冲突**，或亲和性变更被拒 |
| `-ENOSYS` | 9 | **"这一层没实现这个回调"**（§6.3） |
| `-ENOSPC` | 7 | 全在 `kernel/irq/matrix.c`（x86 专用，04 §9）+ `kernel/irq/msi.c` |
| `-ENOTCONN` | 6 | **"这条中断根本没接线"**（§6.4） |
| `-ENODEV` | 4 | 域或模块不存在 |
| `-EPERM` | 3 | 亲和性不许改 / 域的 `map()` 保护 |
| `-EEXIST` | 2 | 映射已存在 |
| `-ERANGE` · `-EOPNOTSUPP` · `-ENXIO` · `-ENOTSUPP` · `-ENOENT` · `-EIO` · `-EIDRM` · `-EFAULT` · `-EBADSLT` | 各 1 | §6.5 |

```bash
grep -rho '\-E[A-Z_]*' kernel/irq/*.c | sort | uniq -c | sort -rn
```

⚠️ **这 208 次里有一部分不是"返回点"**：

| 类别 | 个数 | 位置 |
|---|---|---|
| 出现在函数文档注释里 | **5** | `kernel/irq/irqdesc.c:660` · `:701` · `:742` · `kernel/irq/irqdomain.c:608` · `:1259` |
| 出现在**比较**里（`if (ret == -EXXX)`） | 至少 3 | `kernel/irq/manage.c:318` · `kernel/irq/irqdomain.c:612` · `kernel/irq/irqdomain.c:1306` |

```bash
grep -rn '^[[:space:]]*\*.*-E[A-Z]' kernel/irq/*.c      # 找出那 5 处注释
```

⇒ 实际的返回/赋值点约 **200** 个。本节后面的行号引用都是真实返回点。

### §6.2 按文件分布

| 文件 | 次数 | 说明 |
|---|---|---|
| `kernel/irq/manage.c` | **60** | 消费者 API 的入口，负责挡住所有误用 |
| `kernel/irq/irqdomain.c` | **39** | 域和映射 |
| `kernel/irq/ipi.c` | 20 | arm64 不走（04 §3） |
| `kernel/irq/irqdesc.c` | 20 | —— |
| `kernel/irq/chip.c` | 10 | 其中 5 处是 `-ENOSYS`（§6.3） |
| `kernel/irq/generic-chip.c` | 9 | —— |
| `kernel/irq/msi.c` · `kernel/irq/proc.c` · `kernel/irq/timings.c` | 各 8 | —— |
| `kernel/irq/matrix.c` · `kernel/irq/resend.c` | 各 7 | matrix 是 x86 专用 |
| `kernel/irq/irq_sim.c` | 5 | —— |
| `kernel/irq/devres.c` | 4 | —— |
| `kernel/irq/debugfs.c` · `kernel/irq/handle.c` · `kernel/irq/migration.c` | 各 1 | —— |

**`manage.c` + `irqdomain.c` 占了 99/208（48%）** ——
和 §2.3 那条"`manage.c` 是 WARN 密度最高的文件"一致：
**消费者 API 和域 API 是两个"防守面"**。

### §6.3 `-ENOSYS` 的九处：层级域的"没实现"信号

| 位置 | 语义 |
|---|---|
| `kernel/irq/chip.c:1436` · `:1454` · `:1486` · `:1506` · `:1558` | 五个 `irq_chip_*_parent()` 沿层级往上找不到实现 |
| `kernel/irq/irqdomain.c:1480` · `:1786` | 域层面的回调缺失（`ops->alloc` / `ops->free`） |
| `kernel/irq/manage.c:656` | `irq_set_vcpu_affinity()`：chip 没实现 |
| `kernel/irq/manage.c:1507` | **`__setup_irq()`：chip 还是 `no_irq_chip`**（03 §A5） |

**拿到 `-ENOSYS` 的排查步骤**：
```sh
cat /sys/kernel/debug/irq/irqs/<virq>
# 看每一层的 chip 名字，然后去那个 chip 的定义里看缺了哪个回调
```

⇒ 常见的两种：
① 唤醒相关的 `irq_set_wake` 没实现，而 chip 该设 `IRQCHIP_SKIP_SET_WAKE` 却忘了（02 §6.4、05 §1.6）；
② 手工 `irq_alloc_descs()` 之后忘了 `irq_set_chip_and_handler()`（`kernel/irq/manage.c:1507`）。

### §6.4 `-ENOTCONN` 的两种完全不同的用法

| 用法 | 位置 | 语义 |
|---|---|---|
| **"这条中断没接线"** | `kernel/irq/manage.c:2147` · `:2253` · `:2305` | 三个申请函数（threaded / any_context / nmi）看到 `IRQ_NOTCONNECTED` 就返回它 |
| **层级域的"裁剪标记"** | `kernel/irq/irqdomain.c:1275`（`ERR_PTR(-ENOTCONN)`）+ `:1305-1306` 的检查 | `irq_domain_disconnect_hierarchy()` 用它标记"这一层往上断开了" |

**第一种是 PCI 的约定**：`include/linux/interrupt.h` 里 `IRQ_NOTCONNECTED` 附近的注释
说得很明白 —— PCI 设备的中断没接时把 `dev->irq` 设成 `IRQ_NOTCONNECTED`，
这样 `request_irq()` 返回 `-ENOTCONN` 而不是 `-EINVAL`，**驱动能区分这两种情况**。

⇒ 拿到 `-ENOTCONN` 时：**不是你的代码错了**，是这块板子上这个中断确实没连。
驱动应该"降级运行"（轮询模式）而不是 probe 失败。

### §6.5 九个"只有一处"的错误码

| 错误码 | 位置 | 语义 |
|---|---|---|
| `-EFAULT` | `kernel/irq/debugfs.c:203` | debugfs `trigger` 的 `copy_from_user` 失败 |
| `-EPERM` | `kernel/irq/irqdesc.c:650` | 相关的映射/句柄操作被拒 |
| `-ENOENT` | `kernel/irq/irqdomain.c:1388` | `irq_domain_set_hwirq_and_chip()`：那一层的 `irq_data` 不存在 |
| `-EIO` | `kernel/irq/proc.c:145` | 写 `smp_affinity` 时该中断不允许改亲和性 |
| `-EIDRM` · `-EBADSLT` · `-ERANGE` | `kernel/irq/timings.c:788` · `:802` · `:809` | 中断间隔预测内部（04 §8，arm64 默认不开） |
| `-ENXIO` | `kernel/irq/manage.c:849` | `irq_set_irq_wake()`：这条 virq 没有 `irq_desc` |
| `-EOPNOTSUPP` | `kernel/irq/manage.c:408` | 预留模式下不支持 `irq_update_affinity_desc()` |
| ⚠️ `-ENOTSUPP` | `kernel/irq/generic-chip.c:402` | **非标准错误码**（03 §C2 记录的风格不一致） |

**`kernel/irq/proc.c:145` 那个 `-EIO` 值得单独记**：
写 `/proc/irq/N/smp_affinity` 可能拿到 **两种** 错误码 ——
`-EIO`（这条中断不允许改，`kernel/irq/proc.c:145`）
或 `-EPERM`/`-EINVAL`（其它原因）。
⇒ ⚠️ 脚本里别只判 `-EPERM`。

### §6.6 `-EBUSY` 的六处（`manage.c`）

| 位置 | 语义 |
|---|---|
| `kernel/irq/manage.c:304` | 亲和性变更：`GENERIC_PENDING_IRQ` 下需要延迟到中断到来时才能改 |
| `kernel/irq/manage.c:318` | **这是个比较**（`if (ret == -EBUSY && !force)`），不是返回 |
| `kernel/irq/manage.c:416` · `:422` | `irq_update_affinity_desc()`：中断还在使用中 |
| `kernel/irq/manage.c:1671` | **`__setup_irq()`：共享冲突** —— 最常见的那个 |
| `kernel/irq/manage.c:1831` | 相关的资源占用检查 |

**`kernel/irq/manage.c:1671` 就是 01 §10.4 里那条"共享冲突"**：
双方 flags 不兼容（`IRQF_SHARED` 缺一方、触发类型不同、ONESHOT 不一致，03 §B5.1）。
⇒ 它**配套打了一条 `dev_err`**，所以拿到 `-EBUSY` 一定要看 dmesg。

### §6.7 高频错误的第一嫌疑（比 01 §10.4 更细）

| 拿到 | 第一嫌疑 | 怎么确认 |
|---|---|---|
| `request_irq()` → `-EBUSY` | 共享冲突 | dmesg 里配套的 `dev_err` 会说清是哪一项不兼容 |
| `request_irq()` → `-EINVAL`，dmesg 干净 | ① 级联入口（`IRQ_NOREQUEST`）；② `thread_fn` + NULL handler 缺 `IRQF_ONESHOT` | `cat /sys/kernel/debug/irq/irqs/<virq>` 看 `status` 里有没有 `IRQ_NOREQUEST` |
| `request_irq()` → `-ENOSYS` | chip 还是 `no_irq_chip` | `/proc/interrupts` 里那条中断的 chip 列显示 `None` 或 `-` |
| `request_irq()` → `-ENOTCONN` | **这块板子上这个中断没连** —— 不是代码错 | §6.4 |
| `request_irq()` → `-ENOMEM` | 建线程或分配 cpumask 失败 | 系统内存压力 |
| `platform_get_irq()` → `-EPROBE_DEFER` | 正常，控制器还没 probe | 透传返回值即可 |
| `irq_set_affinity()` → `-ENOSYS` | 层级里某层没实现 `irq_set_affinity` | debugfs 看层级 |
| `irq_set_affinity()` → `-EBUSY` | `GENERIC_PENDING_IRQ`：要等下一次中断才生效 | 不是错误，稍后回读 `effective_affinity` |
| 写 `smp_affinity` → `-EIO` 或 `-EPERM` | 托管中断 / `IRQF_NOBALANCING` / chip 不支持 | `cat /sys/kernel/debug/irq/irqs/<virq> \| grep MANAGED` |
| `enable_irq_wake()` → `-ENOSYS` | chip 缺 `irq_set_wake` 或该设 `IRQCHIP_SKIP_SET_WAKE` | §6.3 |
| **什么都没报，中断不来** | DTS 的 hwirq / `interrupt-parent` / MSI device-id 错 | 06 §8.4 那四条 |

---

## §7 统计与找出"刷屏的那条"

### §7.1 三种数中断的方法

```sh
# ① 快照对比（最简单）
cp /proc/interrupts /tmp/a; sleep 5; diff /tmp/a /proc/interrupts

# ② 每秒增量（找刷屏的）
watch -d -n1 'cat /proc/interrupts'

# ③ 精确统计一段时间内每条中断的次数（用 tracepoint，§3.3 玩法②）
```

### §7.2 单条中断的乱中断统计

```sh
cat /proc/irq/42/spurious
```

三个字段（实现在 `kernel/irq/proc.c:288`）：

| 字段 | 含义 | 什么时候要看 |
|---|---|---|
| `count` | 总中断数（= `desc->irq_count`） | —— |
| `unhandled` | 返回 `IRQ_NONE` 的次数 | **非 0 且在涨 ⇒ 共享中断的 handler 返回值有问题** |
| `last_unhandled` | 上一次未处理的时间戳（jiffies） | 判断"是不是最近才开始的" |

⇒ `nobody cared` 出现之前，这个文件就已经在报警了。
**定期巡检它比等 `nobody cared` 出现要早很多。**

### §7.3 中断关闭时长（实时性问题）

```sh
cd /sys/kernel/tracing
echo irqsoff > current_tracer      # 需要 CONFIG_IRQSOFF_TRACER
echo 0 > tracing_max_latency
# … 跑负载 …
cat tracing_max_latency            # 单位微秒
cat trace | head -40               # 看是谁关的
```

配合 `threadirqs` 命令行参数（01 §0.4、05 §6.4）对比：
线程化之后同一个 handler 的关中断时长应该显著下降。

### §7.4 ⚠️ `/proc/interrupts` 的两个统计口径

| 中断类型 | 计数来自 | 说明 |
|---|---|---|
| 普通中断 | `desc->kstat_irqs`（per-CPU） | `kernel/irq/proc.c:500-502` |
| `handle_percpu_irq` 的中断 | **不统计**（01 §4.3） | ⇒ 计数栏可能一直是 0 |
| `handle_untracked_irq` 的中断 | **不统计** | 01 §4.3 |
| 架构自己的（IPI / 异常） | `arch_show_interrupts()` | `kernel/irq/proc.c:472-473` —— 在表格**末尾**单独几行 |

⚠️ **arm64 的 IPI 计数在 `/proc/interrupts` 的最后几行**（`IPI0`~`IPI6`），
不在正常的 virq 行里 —— 因为 arm64 的 IPI 不走 `irq_domain`（04 §3.1）。

---

## §8 一页速查

```
┌─ 先看现状 ────────────────────────────────────────────────────┐
│ cat /proc/interrupts                    ← virq/计数/chip/hwirq │
│ cat /proc/irq/<N>/effective_affinity    ← 实际送到哪个 CPU     │
│ cat /proc/irq/<N>/spurious              ← 乱中断统计           │
└───────────────────────────────────────────────────────────────┘

┌─ 必须先开 CONFIG_GENERIC_IRQ_DEBUGFS ─────────────────────┐
│ cd /sys/kernel/debug/irq                                       │
│ cat domains/default        ← 所有域一览                         │
│ cat domains/<name>         ← 递归打印层级（唯一窗口）       │
│ cat irqs/<N>               ← 四组状态位 + 每层 chip/hwirq   │
│ echo trigger > irqs/<N>    ← 注入一次中断                   │
└───────────────────────────────────────────────────────────────┘

┌─ tracepoint（五个，零 TP_CONDITION）─────────────────────────┐
│ cd /sys/kernel/tracing                                        │
│ echo 1 > events/irq/irq_handler_entry/enable                  │
│ echo 1 > events/irq/irq_handler_exit/enable                   │
│ echo 'irq==42' > events/irq/irq_handler_entry/filter          │
│ echo 'ret==0'  > events/irq/irq_handler_exit/filter   ← 只看 IRQ_NONE │
│ cat trace_pipe                                                │
└───────────────────────────────────────────────────────────────┘

┌─ 动态调试（23 处 pr_debug，13 处在 irqdomain.c）──────────────┐
│ C=/sys/kernel/debug/dynamic_debug/control                     │
│ echo 'file irqdomain.c +p'  > $C   ← 调映射问题第一步      │
│ echo 'file manage.c +p'     > $C   ← 申请/释放/亲和性          │
│ echo 'file cpuhotplug.c +p' > $C   ← CPU 上下线时的迁移        │
└───────────────────────────────────────────────────────────────┘

┌─ 没有 tracepoint 的地方用 function_graph ─────────────────────┐
│ echo 'irq_domain_*' > set_ftrace_filter                       │
│ echo '__setup_irq' >> set_ftrace_filter                       │
│ echo function_graph > current_tracer                          │
└───────────────────────────────────────────────────────────────┘

┌─ 实时性 ──────────────────────────────────────────────────────┐
│ echo irqsoff > current_tracer ; cat tracing_max_latency        │
│ 命令行加 threadirqs 对比                                       │
└───────────────────────────────────────────────────────────────┘
```

### §8.1 错误码 → 第一嫌疑（压缩版）

| 拿到 | 先查 |
|---|---|
| `-EBUSY` | 共享冲突 —— **看 dmesg 里配套的 dev_err** |
| `-EINVAL` + 无日志 | 级联入口 或 `IRQF_ONESHOT` 漏了 |
| `-ENOSYS` | 层级里某层的 chip 缺回调 —— 看 `debug/irq/irqs/<N>` |
| `-ENOTCONN` | **这个中断没接线，不是代码错** |
| `-EPROBE_DEFER` | 正常，透传 |
| `-EIO`（写 smp_affinity） | 这条中断不许改亲和性 |
| `-ENOTSUPP`（值 524，非标准） | `generic-chip` 的 `irq_setup_alt_chip()` |
| **无任何日志、中断不来** | 06 §8.4 四条静默失效 |

### §8.2 三条最有用的诊断动作（按顺序做）

| 顺序 | 动作 | 能排除什么 |
|---|---|---|
| ① | `cat /proc/interrupts` 看 **hwirq 列** | 排除 DTS 的 hwirq 换算错（06 §3.1） |
| ② | `cat /sys/kernel/debug/irq/irqs/<N>` | 排除 chip 挂错、层级不对、状态位不对 |
| ③ | `echo trigger > /sys/kernel/debug/irq/irqs/<N>` | 把"核心侧"和"硬件侧"一刀切开（§5.2） |

---

**下一篇**：08 篇用大白话和类比解释这个子系统里最难理解的几组关系。
