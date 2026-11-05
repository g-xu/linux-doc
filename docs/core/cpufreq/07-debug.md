---
title: 调试方法
---

# cpufreq 调试与观测

本篇讲**怎么看**：编译期要开哪些开关、内核会打印什么、有哪些 tracepoint、
sysfs / debugfs 里哪些文件能当探针、错误码怎么分诊。

⚠️ **本篇所有命令输出都是示意，不是从板子上粘的**（99 §5.1 已登记）。
文件名、tracepoint 名、错误码、打印字符串全部经过全树 grep 核对；
**具体数值不要当真**。

⚠️ 架构范围：arm64。x86 专属的观测手段（`aperf/mperf`、`intel_pstate` 的
`pstate_sample` tracepoint）一律不讲，裁剪记录见 99 §2.7。

---

## §0 按症状找工具

| 症状 | 先看 | 章节 |
|---|---|---|
| `/sys/.../cpufreq/` 整个目录不存在 | Kconfig + 驱动是否 probe | §1、§7.2 |
| 目录在，但 `scaling_driver` 不是预期的驱动 | 谁抢先注册 | §7.2 |
| `scaling_cur_freq` 读出来是 0 或纹丝不动 | arm64 上它的取值链只有两级 | §4.2 |
| 写 `scaling_max_freq` 没生效 / 被改回去 | freq_qos 是多方交集 | §4.3 |
| 想知道"内核到底下发了什么频率" | `cpu_frequency` tracepoint | §3.2 |
| 想知道"限值被谁改过" | `cpu_frequency_limits` tracepoint | §3.3 |
| 用 `fast_switch` 的板子上 trace 全空 | 快路径的 tracepoint 有门 | §3.4 |
| governor 切不过去 / 写 tunable 报错 | `-EBUSY` / `-EINVAL` 的四种来源 | §7.3 |
| 想看频率停留时间分布 | `stats/` | §4.4 |
| 想看 OPP 表 / Energy Model 建成什么样 | debugfs（**不在 cpufreq 下**） | §5 |
| dmesg 里什么都没有 | 本子系统 90% 的打印是 `pr_debug` | §2 |
| 想构造故障复现 | 无专用注入框架，只有替代手段 | §6 |

**不在本篇的内容**：

| 想找 | 去 |
|---|---|
| DTS 属性配错的定位 | 06 篇（尤其 §7.4 错误消息映射表） |
| 各 sysfs 文件的**用法**（而非调试用途） | 05 §7 |
| stats 的实现原理 | 04 §4 |
| 各 governor 的算法参数含义 | 04 §2 |

---

## §1 编译期开关

### §1.1 全部开关一览

所有开关都在 `drivers/cpufreq/Kconfig`（325 行）和
`drivers/cpufreq/Kconfig.arm`（359 行）里。

| 开关 | 位置 | 作用 | arm64 上该怎么设 |
|---|---|---|---|
| `CPU_FREQ` | `drivers/cpufreq/Kconfig:4` | 总开关。关掉之后**整个 `cpufreq/` 目录不存在**，`include/linux/cpufreq.h` 里的 API 退化成空壳 | Y |
| `CPU_FREQ_STAT` | `drivers/cpufreq/Kconfig:31` | 决定 `policyN/stats/` 这个目录**在不在**。默认帮助文字写的是 "If in doubt, say N"（`:36`） | Y（调试必开） |
| `CPU_FREQ_GOV_ATTR_SET` | `drivers/cpufreq/Kconfig:23` | 无 prompt 的隐含项（`:24` 只有 `bool`），被 governor 选中时自动开 | 自动 |
| `CPU_FREQ_GOV_COMMON` | `drivers/cpufreq/Kconfig:26` | 同上，dbs 框架公共代码 | 自动 |
| `CPU_FREQ_GOV_PERFORMANCE` | `drivers/cpufreq/Kconfig:111` | performance governor，`tristate` | Y |
| `CPU_FREQ_GOV_POWERSAVE` | `drivers/cpufreq/Kconfig:122` | powersave governor | 可选 |
| `CPU_FREQ_GOV_USERSPACE` | `drivers/cpufreq/Kconfig:133` | 决定 `scaling_setspeed` 有没有意义（05 §7.3） | 调试时开 |
| `CPU_FREQ_GOV_ONDEMAND` | `drivers/cpufreq/Kconfig:146` | 旧式采样 governor | 可选 |
| `CPU_FREQ_GOV_CONSERVATIVE` | `drivers/cpufreq/Kconfig:165` | 同上，更保守 | 可选 |
| `CPU_FREQ_GOV_SCHEDUTIL` | `drivers/cpufreq/Kconfig:190` | 注意它是 `bool`（`:191`）不是 `tristate` —— **不能编成模块** | Y |
| `CPUFREQ_DT` | `drivers/cpufreq/Kconfig:209` | 通用 DT 驱动（06 §2） | 按板子 |
| `CPUFREQ_DT_PLATDEV` | `drivers/cpufreq/Kconfig:221` | 无 prompt（`:222`），被 `CPUFREQ_DT` 选中 | 自动 |
| `ARM_SCMI_CPUFREQ` | `drivers/cpufreq/Kconfig.arm:286` | 固件路线（06 §3） | 按板子 |
| `ARM_QCOM_CPUFREQ_HW` | `drivers/cpufreq/Kconfig.arm:164` | 高通硬件自治（06 §4） | 按板子 |
| `ARM_QCOM_CPUFREQ_NVMEM` | `drivers/cpufreq/Kconfig.arm:153` | 高通 binning 预处理器（06 §5） | 按板子 |
| `ARM_IMX_CPUFREQ_DT` | `drivers/cpufreq/Kconfig.arm:114` | i.MX binning | 按板子 |
| `ARM_MEDIATEK_CPUFREQ_HW` | `drivers/cpufreq/Kconfig.arm:136` | MTK 硬件自治 | 按板子 |
| `ARM_TEGRA194_CPUFREQ` | `drivers/cpufreq/Kconfig.arm:334` | Tegra194 | 按板子 |

### §1.2 默认 governor 是编译期决定的

`drivers/cpufreq/Kconfig:38` 起是一个 `choice`，关键在 `:41`：

```
default CPU_FREQ_DEFAULT_GOV_SCHEDUTIL if ARM64 || ARM
```

⇒ **arm64 的默认 governor 是 schedutil**，不是 ondemand。
这条决定了 04 §2 讲的两条 governor 路线里，arm64 默认走的是调度器驱动那条。

六个 `CPU_FREQ_DEFAULT_GOV_*` 分别在 `drivers/cpufreq/Kconfig:48`、`:56`、
`:64`、`:73`、`:86`、`:99`。

⚠️ 编译期选的只是**默认值**，可以被命令行覆盖 —— 见 §1.3。

### §1.3 两个命令行参数（就这两个）

`drivers/cpufreq/cpufreq.c` 全文只有两个 `module_param`：

| 参数 | 位置 | 权限 | 作用 |
|---|---|---|---|
| `cpufreq.off=1` | `drivers/cpufreq/cpufreq.c:2996` | `0444`（只读） | 让 `cpufreq_disabled()` 为真。后果：`cpufreq_register_driver()` 直接返回 `-ENODEV`（`:2856`）、`cpufreq_core_init()` 返回 `-ENODEV`（`:2986`）⇒ **目录不存在，dmesg 里没有任何抱怨** |
| `cpufreq.default_governor=<name>` | `drivers/cpufreq/cpufreq.c:2997` | `0444` | 覆盖 §1.2 的编译期默认。变量 `default_governor` 在 `:51`；`:2991-2992` 处如果命令行没给就抄编译期默认的名字 |

两个都是 `0444` ⇒ **运行期改不了**，只能重启。

⚠️ 排查"目录不存在"时先 `cat /proc/cmdline | tr ' ' '\n' | grep cpufreq` ——
这是最便宜的一步，而且 `cpufreq.off=1` 不留任何日志痕迹。

### §1.4 相邻子系统的开关（会影响 cpufreq 的观测）

| 开关 | 缺了会怎样 |
|---|---|
| `CONFIG_PM_OPP` + `CONFIG_DEBUG_FS` | `/sys/kernel/debug/opp/` 没有 ⇒ 看不到 OPP 表（§5.2） |
| `CONFIG_ENERGY_MODEL` | `/sys/kernel/debug/energy_model/` 没有 ⇒ 看不出 EAS 有没有起来（§5.3、06 §6.4） |
| `CONFIG_DYNAMIC_DEBUG` | 没有它，本子系统 90% 的打印永远看不到（§2） |
| `CONFIG_CPU_THERMAL` / `CONFIG_CPU_FREQ_THERMAL` | 没有 thermal 就没人来抢限值，"限值被改"的一大来源消失 |

---

## §2 日志可见性等级

**这是本子系统调试的第一个坑**：`drivers/cpufreq/cpufreq.c` 里的打印
**绝大多数是 `pr_debug`**。默认内核配置下它们一个字都不出现，
所以"dmesg 干净"绝不等于"一切正常"。

### §2.1 四个等级

| 等级 | 本子系统里的实例 | 默认可见？ |
|---|---|---|
| `pr_err` / `dev_err` | `pr_err("%s: Cannot %s BOOST!\n")` `drivers/cpufreq/cpufreq.c:625`；`pr_err("%s: ->get() failed\n")` `:1461`；`pr_err("%s: Policy frequency update failed\n")` `:2730` | ✅ 总是 |
| `pr_warn` | `pr_warn("Can't use %s governor as dynamic switching is disallowed. Fallback to %s governor\n")` `drivers/cpufreq/cpufreq.c:2378`；`pr_warn("Freeing active policy\n")` `:1300` | ✅ 总是 |
| `pr_info` | `pr_info("%s: CPU%d: Running at unlisted initial frequency: %u KHz, changing to: %u KHz\n")` `drivers/cpufreq/cpufreq.c:1500` | ✅ 总是 |
| **`pr_debug`** | 二十多处，见 §2.3 | ❌ 需要 dynamic_debug |

### §2.2 只有一处 `_once`，没有任何 `_ratelimited`

核心五个文件（`cpufreq.c` / `freq_table.c` / `cpufreq_stats.c` / `cpufreq_governor.c` /
`kernel/sched/cpufreq_schedutil.c`）加上全部 arm64 相关驱动，只有**一个** `_once` 变体：

```
pr_warn_once("cpufreq transition table exceeds PAGE_SIZE. Disabling\n");
```

位置 `drivers/cpufreq/cpufreq_stats.c:168`，紧接着 `return -EFBIG`（`:169`）。

这条的实际后果比字面严重得多：

- 它的语义是"**这一次读**放不下"，不是"永久关闭"（函数每次被读都会重跑）；
  字符串里的 "Disabling" 容易让人误以为统计功能被关了。
- 因为是 `_once`，**只在第一次 `cat trans_table` 时出现一次**。
  之后再 `cat` 只会拿到 `-EFBIG`（shell 显示 `File too large`），**日志里一个字都没有**。
- 触发条件是频率档位多到 N×N 表格超过一页 —— 档位多的 arm64 大核很容易撞上。

⇒ 排查 `cat trans_table` 报错时，**不要指望 dmesg**，直接数档位数量。

**零 `_ratelimited`** ⇒ 本子系统不存在"日志被限流吞掉"这种情况
（`drivers/cpufreq/` 全目录 0 处 `_ratelimited`，无需按架构收窄）。

⚠️ `_once` 那个"1 个"要注意范围：把 x86 / PowerPC 驱动也算进来，
`drivers/cpufreq/` 全目录共 **9 处** `_once`，另外 8 处分别在 `acpi-cpufreq.c`、
`amd-pstate.c`、`p4-clockmod.c`、`powernow-k8.c`、`powernv-cpufreq.c` 里 ——
按 99 §2.7 全部裁掉，arm64 上碰不到。

### §2.3 `pr_debug` 全开的做法

```bash
# 一次开完（需要 CONFIG_DYNAMIC_DEBUG）
for f in drivers/cpufreq/cpufreq.c drivers/cpufreq/freq_table.c \
         drivers/cpufreq/cpufreq_governor.c drivers/opp/core.c drivers/opp/of.c; do
	echo "file $f +p" > /sys/kernel/debug/dynamic_debug/control
done

# 关掉（输出很大，查完就关）
echo 'file drivers/cpufreq/cpufreq.c -p' > /sys/kernel/debug/dynamic_debug/control
```

开完之后值得盯的几条（按调试价值排）：

| 打印 | 位置 | 说明什么 |
|---|---|---|
| `FREQ: %u - CPUs: %*pbl` | `drivers/cpufreq/cpufreq.c:380` | 慢路径每次调频都打一条，还带 cpumask —— 一眼看出 policy 覆盖了哪几个核 |
| `notification %u of frequency transition to %u kHz` | `drivers/cpufreq/cpufreq.c:356` | 通知链发出去了 |
| `target for CPU %u: %u kHz, relation %u, requested %u kHz` | `drivers/cpufreq/cpufreq.c:2306` | **请求值和实际解析值同时打出来** —— 定位"为什么给我换到了别的档"的最佳一条 |
| `new min and max freqs are %u - %u kHz` | `drivers/cpufreq/cpufreq.c:2608` | 限值刷新的结果 |
| `updating policy for CPU %u` | `drivers/cpufreq/cpufreq.c:1148` | 限值刷新真的发生了 |
| `trying to register driver %s` | `drivers/cpufreq/cpufreq.c:2874` | 驱动到底有没有走到注册 |
| `%s: No CPU initialized for driver %s` | `drivers/cpufreq/cpufreq.c:2913` | 驱动注册了但所有 `->init()` 都失败 ⇒ 随后 `-ENODEV`（`:2912`） |
| `%s: suspend_freq not defined` | `drivers/cpufreq/cpufreq.c:1889` | 用了通用 suspend 但没写 `opp-suspend`（06 §1.1） |
| `%s: cpufreq BOOST %s` | `drivers/cpufreq/cpufreq.c:630` | 写全局 `boost` 成功了 |

注意 `:2306` 和 `:380` 是一对：前者是"要去哪"，后者是"到了哪"。
两条不一致就说明 `__resolve_freq()` 把请求改了 —— 这是**正常行为**，不是 bug。

### §2.4 WARN / BUG 清单

这几处一旦触发，说明**调用者用错了 API**，不是硬件问题。

| 断言 | 位置 | 含义 |
|---|---|---|
| `BUG_ON(irqs_disabled())` | `drivers/cpufreq/cpufreq.c:349` | 慢路径通知**不能在关中断上下文调**。这是 05 §2.2 那条"`->fast_switch` 才是原子上下文"的反面 |
| `BUG_ON(ret)` | `drivers/cpufreq/cpufreq.c:1499` | 开机频率不在表里且改不过去。注释 `:1494-1498` 明说了为什么宁可 panic |
| `WARN_ON(cpu >= nr_cpu_ids)` | `drivers/cpufreq/cpufreq.c:221` | 传了非法 CPU 号 |
| `WARN_ON(!policy)` | `drivers/cpufreq/cpufreq.c:256` | `cpufreq_cpu_put(NULL)` |
| `WARN_ON(!(... CPUFREQ_ASYNC_NOTIFICATION) ...)` | `drivers/cpufreq/cpufreq.c:419` | 驱动没声明异步通知却异步通知了 |
| `WARN_ON(!policy->transition_ongoing)` | `drivers/cpufreq/cpufreq.c:444` | POSTCHANGE 没有配对的 PRECHANGE |
| `WARN_ON(cpufreq_fast_switch_count <= 0)` | `drivers/cpufreq/cpufreq.c:523` | 计数器失衡 |
| `WARN_ON(cpufreq_fast_switch_count >= 0)` | `drivers/cpufreq/cpufreq.c:2097` | 注销 transition notifier 时计数器失衡（和上一条方向相反） |
| `WARN_ON(!cpumask_test_cpu(cpu, policy->related_cpus))` | `drivers/cpufreq/cpufreq.c:1352` | 上线了一个不属于本 policy 的 CPU |
| `WARN_ON(!cpufreq_verify_current_freq(...))` | `drivers/cpufreq/cpufreq.c:2684` | resume 后频率对不上 |
| `BUG_ON(!cpufreq_global_kobject)` | `drivers/cpufreq/cpufreq.c:2989` | 全局 kobject 建不出来 |
| `WARN_ON(have_governor_per_policy())` | `drivers/cpufreq/cpufreq_governor.c:421` 和 `kernel/sched/cpufreq_schedutil.c:704` | 驱动置了 `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` 却复用了全局 tunables ⇒ 随后 `-EINVAL`（`cpufreq_governor.c:422`） |

---

## §3 tracepoint

### §3.1 arm64 上只有两个可用的 tracepoint

`include/trace/events/power.h` 里和 CPU 频率相关的一共四个，但：

| tracepoint | 定义 | arm64 上有用？ |
|---|---|---|
| `power:cpu_frequency` | `include/trace/events/power.h:167` | ✅ 核心在用 |
| `power:cpu_frequency_limits` | `include/trace/events/power.h:174` | ✅ 核心在用 |
| `power:pstate_sample` | `include/trace/events/power.h:87` | ❌ 只有 `intel_pstate` 触发（x86） |
| `power:powernv_throttle` | `include/trace/events/power.h:65` | ❌ 只有 `powernv-cpufreq` 触发（PowerPC） |

⇒ **`drivers/cpufreq/` 下面所有 arm64 驱动都不自带 tracepoint**。
`cpufreq-dt.c` / `scmi-cpufreq.c` / `qcom-cpufreq-hw.c` 全文零个 `trace_` 调用。
想看驱动内部只能靠 §2.3 的 `pr_debug` 或 kprobe/function tracer。

⚠️ 这和 04 篇讲的"核心零 tracepoint"要分清：**核心确实有两个**（下面 §3.2/§3.3），
零的是**驱动层**。

### §3.2 `power:cpu_frequency`

它复用 `DECLARE_EVENT_CLASS(cpu, ...)`（`include/trace/events/power.h:16`），
和 `cpu_idle` 共用同一个字段布局：

| 字段 | 定义 | 含义 |
|---|---|---|
| `state` | `include/trace/events/power.h:23` | **频率（kHz）**，字段名叫 state 是因为类被 `cpu_idle` 共用 |
| `cpu_id` | `include/trace/events/power.h:24` | CPU 号 |

输出格式 `state=%lu cpu_id=%lu`（`include/trace/events/power.h:32-33`）。

**它是 per-CPU 展开的，不是 per-policy**。两个触发点：

| 触发点 | 所在函数 | 说明 |
|---|---|---|
| `drivers/cpufreq/cpufreq.c:384` | `cpufreq_notify_transition()`（`:343`） | 慢路径，`CPUFREQ_POSTCHANGE` 分支里 `for_each_cpu(cpu, policy->cpus)` 逐核打一条 |
| `drivers/cpufreq/cpufreq.c:2161` | `cpufreq_driver_fast_switch()`（`:2142`） | 快路径，同样逐核 —— 但外面套了一道门，见 §3.4 |

⇒ **一次调频会产生 N 条记录**（N = policy 里的在线 CPU 数）。
四核一簇的板子上看到四条同频记录是**正常的**，不是重复上报。

```bash
cd /sys/kernel/debug/tracing
echo 1 > events/power/cpu_frequency/enable
echo 1 > tracing_on
sleep 2
cat trace | head -30
echo 0 > events/power/cpu_frequency/enable
```

### §3.3 `power:cpu_frequency_limits`

| 字段 | 定义 | 取值 |
|---|---|---|
| `min_freq` | `include/trace/events/power.h:181` | `policy->min`（`:187`） |
| `max_freq` | `include/trace/events/power.h:182` | `policy->max`（`:188`） |
| `cpu_id` | `include/trace/events/power.h:183` | `policy->cpu`（`:189`） |

输出格式 `min=%lu max=%lu cpu_id=%lu`（`include/trace/events/power.h:192-194`）。

唯一触发点：`drivers/cpufreq/cpufreq.c:2606`，在 `cpufreq_set_policy()`（`:2561`）里，
**紧跟在两个 `WRITE_ONCE(policy->min/max, ...)` 之后**（`:2596-2602`）。

**这是排查"我设的限值被谁改了"的唯一利器**。它的关键性质：

- 它记的是**最终生效的交集结果**，不是某一方的请求值 ——
  所以看到 max 比你写进 `scaling_max_freq` 的值小，说明有别人（thermal / 其他 freq_qos
  请求者）也在压，见 §4.3。
- 和 `cpu_frequency` 不同，它是 **per-policy 一条**（`cpu_id` 只取 `policy->cpu`），
  不逐核展开。
- 它**不带触发者信息**。想知道是谁改的，配 `pm_qos_update_target`
  （`include/trace/events/power.h:454`）一起开。

```bash
cd /sys/kernel/debug/tracing
echo 1 > events/power/cpu_frequency_limits/enable
echo 1 > events/power/pm_qos_update_target/enable   # 谁在压限值
echo 1 > tracing_on
```

### §3.4 快路径上的 tracepoint 有一道额外的门

`drivers/cpufreq/cpufreq.c:2159`：

```c
if (trace_cpu_frequency_enabled()) {
	for_each_cpu(cpu, policy->cpus)
		trace_cpu_frequency(freq, cpu);
}
```

慢路径（`:384`）**没有**这层 `if`。

这个差异的意思是：快路径为了省时间，先用一次 static-key 判断整个 event 有没有开，
再决定是否进循环。**对使用者没有语义差别**（tracepoint 本身也是 static-key），
但它提示了一件事：`cpufreq_driver_fast_switch()` 是被当作**热路径**对待的，
在这里插 kprobe 会实打实地影响调度延迟。

⚠️ **本子系统的两个 tracepoint 都没有 `TP_CONDITION`** ——
`include/trace/events/power.h` 全文零个 `TP_CONDITION`。
所以打开即全量，不存在"开了却没记录"的过滤器问题；
唯一会让你看不到记录的原因见下表。

### §3.5 「打开了 tracepoint 却一条记录都没有」

这是本子系统最常见的观测困惑，四种原因：

| 原因 | 怎么确认 | 说明 |
|---|---|---|
| 硬件自治驱动根本不走核心 | `cat scaling_driver` 是不是 `qcom-cpufreq-hw` | 06 §4：频率由硬件自己换，核心不知道 ⇒ 两个 tracepoint 都不会触发。**这是设计如此** |
| 用了 `->fast_switch` 但 governor 没在调频 | 看负载是否真的在变 | schedutil 有 `rate_limit_us` 节流（§4.5） |
| governor 是 performance / powersave | `cat scaling_governor` | 它们只在 `.limits` 时动一次，之后永远不换档 |
| 频率没有真的改变 | 对照 `scaling_cur_freq` | `__cpufreq_driver_target()` 在目标等于当前值时直接返回，不发通知 |

⚠️ 反过来也成立：**`cpu_frequency` 有记录不代表硬件真换了频率** ——
它记的是"内核认为下发成功了"。硬件实际值要靠 `cpuinfo_cur_freq`（§4.2）对照。

---

## §4 sysfs 当探针用

05 §7 讲的是**怎么用**这些文件；这里讲**它们能告诉你什么**，
以及写进去之后**语义和你想的不一样**的地方。

### §4.1 读写入口的公共行为

所有 `policyN/` 下的文件都过同一对函数：

| 函数 | 位置 | 行为 |
|---|---|---|
| `show()` | `drivers/cpufreq/cpufreq.c:967` | 无 `.show` ⇒ `-EIO`（`:974`）；policy 不活跃 ⇒ 保持初值 `-EBUSY`（`:971`） |
| `store()` | `drivers/cpufreq/cpufreq.c:984` | 无 `.store` ⇒ `-EIO`（`:992`）；policy 不活跃 ⇒ `-EBUSY`（`:989`） |

**`-EBUSY` 在这里的含义是"这个 policy 已经不活跃了"**（对应 CPU 全下线），
不是"忙，稍后重试"。判据是 `policy_is_inactive(policy)`（`:977`、`:995`）。
shell 里表现为 `Device or resource busy` —— 极易误判。

⇒ 遇到读任何文件都 `-EBUSY`：先 `cat affected_cpus`，很可能是空的。

### §4.2 arm64 上 `scaling_cur_freq` 的取值链只有两级

`show_scaling_cur_freq()`（`drivers/cpufreq/cpufreq.c:721`）是三级 fallback：

```
freq = arch_freq_get_on_cpu(policy->cpu);        // :726
if (freq)                       → 用它           // :727-728
else if (driver->setpolicy && driver->get)
                                → 调驱动 ->get   // :729-730
else                            → policy->cur    // :731-732
```

三条分支在 arm64 上的实际情况：

| 分支 | arm64 上 |
|---|---|
| `arch_freq_get_on_cpu()` | **永远返回 0**。全树只有 `arch/x86/kernel/cpu/aperfmperf.c:414` 实现了它；arm64 用的是 `drivers/cpufreq/cpufreq.c:716` 那个 `__weak` 空实现（`return 0`，`:718`）。声明在 `include/linux/cpufreq.h:1243` |
| `driver->setpolicy && driver->get` | ❌ arm64 上所有驱动都是 `target_index` 型或 `fast_switch` 型，**没有 `setpolicy`**（`setpolicy` 是 ACPI/intel_pstate 的路子，已裁剪） |
| `policy->cur` | ✅ **arm64 上实际走的就是这一条** |

结论：**`scaling_cur_freq` 在 arm64 上读的是内核的记账值，不是硬件寄存器**。

三个直接后果：

1. `policy->clk` 没设好导致 `policy->cur` 是 0 时，这个文件恒读 0（05 §1.2）。
2. 高通硬件自治路线上硬件自己降频，`policy->cur` 不变 ⇒
   **这个文件显示的是"内核以为的频率"，看不出硬件在限频**（06 §4.2）。
3. 想读硬件真实值只能用 `cpuinfo_cur_freq`（下一条）。

**`cpuinfo_cur_freq` 才是问硬件的那个文件**：
`show_cpuinfo_cur_freq()` 在 `drivers/cpufreq/cpufreq.c:760`，
权限是 `0400`（`cpufreq_freq_attr_ro_perm(cpuinfo_cur_freq, 0400)`，`:933`）
—— **只有 root 能读**，普通用户 `cat` 会 `Permission denied`。
它需要驱动实现 `->get()`；驱动没实现的话这个文件也不会出现。

⇒ **对照读法**：`cpuinfo_cur_freq` 和 `scaling_cur_freq` 不一致
= 内核记账和硬件脱节，去看 §2.3 那两条 `Warning: CPU frequency ... out of sync`。

### §4.3 写 `scaling_min_freq` / `scaling_max_freq` 不是赋值，是投票

`store_one()` 宏（`drivers/cpufreq/cpufreq.c:739`）展开出的两个 store
（`:754`、`:755`）做的事只有一件：

```c
ret = freq_qos_update_request(policy->object##_freq_req, val);   // :750
return ret >= 0 ? count : ret;                                  // :751
```

**它更新的是"cpufreq 用户态这一方"的 freq_qos 请求，不是 `policy->max` 本身**。
最终 `policy->max` 由所有请求方取交集算出（05 §6.2）。

调试上的三条推论：

| 现象 | 原因 |
|---|---|
| 写进去了，`cat` 回来还是原值 | 另一方（thermal）压得更低 ⇒ 交集不变。**不是写失败** |
| 写 `scaling_max_freq` 一个比 `cpuinfo_min_freq` 还小的值，没报错 | `freq_qos` 只做区间校验；真正的档位对齐在 `verify` 里做（06 §7.4） |
| 写了个不在频率表里的值，读回来变成了别的值 | ✅ 正常：`__resolve_freq()` 对齐到最近档位，打印见 §2.3 的 `:2306` |
| 返回 `-EINVAL` | `sscanf` 没解析出数字（`:748`） |

**验证"是谁在压"的标准动作**：

```bash
# 1) 记下当前交集
cat scaling_max_freq cpuinfo_max_freq

# 2) 开 trace 看限值变化 + 变化来源
cd /sys/kernel/debug/tracing
echo 1 > events/power/cpu_frequency_limits/enable
echo 1 > events/power/pm_qos_update_target/enable
echo 1 > tracing_on

# 3) 回来写一次，看 trace 里最终 max 是多少
echo <freq> > /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
cat /sys/kernel/debug/tracing/trace | tail -20
```

⚠️ **不要试图用 `policy->min/max` 之类的内核内写法绕过它** ——
`refresh_frequency_limits()`（`drivers/cpufreq/cpufreq.c:1153`）会把你写的值覆盖掉。

### §4.4 `stats/` 目录

`CONFIG_CPU_FREQ_STAT`（§1.1）开了才有。四个文件，
注册在 `drivers/cpufreq/cpufreq_stats.c:175`（`default_attrs[]`），
组名 `"stats"` 在 `:182-184`：

| 文件 | 实现 | 调试价值 |
|---|---|---|
| `total_trans` | `drivers/cpufreq/cpufreq_stats.c:58` | 换档次数。**判断"governor 到底在不在工作"的最快一个数** —— 跑一会儿负载再读，没涨就是没在调频 |
| `time_in_state` | `drivers/cpufreq/cpufreq_stats.c:69` | 每档停留时间（单位 10ms jiffies）。判断"是不是一直卡在最低档"或"一直顶在最高档" |
| `trans_table` | `drivers/cpufreq/cpufreq_stats.c:124` | N×N 转移矩阵。档位多时会 `-EFBIG`（§2.2） |
| `reset`（只写） | `drivers/cpufreq/cpufreq_stats.c:103` | 清零 |

**`reset` 的语义是"延迟清零"，不是立即清零**：

```c
WRITE_ONCE(stats->reset_time, local_clock());   // :112
smp_wmb();
WRITE_ONCE(stats->reset_pending, 1);            // :118
```

注释 `:105-107` 说明了原因（避免和记录路径竞争）。
真正的清零发生在下一次换档时 —— `cpufreq_stats_record_transition()` 里
`if (unlikely(READ_ONCE(stats->reset_pending)))` 才调
`cpufreq_stats_reset_table()`（`:275-276`）。

⇒ **写完 `reset` 立刻 `cat total_trans` 会看到 0，但那是 `show_total_trans()`
在 `reset_pending` 为真时直接返回的假 0**（`drivers/cpufreq/cpufreq_stats.c:62-63`），
底层数据还没清。⇒ 「reset 好像没生效」的实质是**在下一次换档之前，
读到的一直是这个假 0**。触发一次调频就正常了。

⚠️ `store_reset()` 的注释直说 "We don't care what is written to the attribute"
（`drivers/cpufreq/cpufreq_stats.c:102`）—— 写什么值都一样。

### §4.5 governor tunables

tunables 挂在**独立的 kobject** 上，不走 §4.1 那对函数，
而是 `governor_sysfs_ops`（`drivers/cpufreq/cpufreq_governor_attr_set.c:37-40`）。

**写 tunable 也有一个 `-EBUSY`，但含义完全不同**：

```c
ret = attr_set->usage_count ? gattr->store(attr_set, buf, count) : -EBUSY;
```

位置 `drivers/cpufreq/cpufreq_governor_attr_set.c:32`。
`usage_count == 0` ⇒ 这组 tunables 已经没有 policy 在用了（governor 正在被换掉）。
⇒ 和 §4.1 那个 `-EBUSY` 是**两个不同的原因，同一个错误码**。
区分方法：路径里带 governor 名字的是这一个。

各 governor 的 tunable 文件：

| governor | 文件 | 定义 |
|---|---|---|
| schedutil | `rate_limit_us` | `kernel/sched/cpufreq_schedutil.c:555`，属性数组 `:557-560` |
| ondemand | `sampling_rate` `io_is_busy` `up_threshold` `sampling_down_factor` `ignore_nice_load` `powersave_bias` | `drivers/cpufreq/cpufreq_ondemand.c:325-330`，数组 `:332` |
| conservative | `sampling_rate` `sampling_down_factor` `up_threshold` `ignore_nice_load` `down_threshold` `freq_step` | `drivers/cpufreq/cpufreq_conservative.c:253-258`，数组 `:260` |
| performance / powersave | 无 | —— |

arm64 默认 governor 是 schedutil（§1.2），所以**只有一个 tunable 可调**：

```bash
G=/sys/devices/system/cpu/cpufreq/policy0/schedutil
cat $G/rate_limit_us          # 默认值来自驱动，见下
echo 500 > $G/rate_limit_us   # 调紧节流，观察 total_trans 涨得快不快
```

`rate_limit_us` 的默认值不是硬编码，来自
`cpufreq_policy_transition_delay_us(policy)`（`kernel/sched/cpufreq_schedutil.c:721`）
—— 也就是**间接来自 DTS 的 `clock-latency-ns`**（06 §6.5）。
⇒ DTS 里漏写 `clock-latency-ns` 时这个默认值会不合理，
但 sysfs 上看不出异常，只能对照 `cpuinfo_transition_latency` 是不是 0。

⚠️ `rate_limit_us_store()` 用 `kstrtouint()` 解析（`kernel/sched/cpufreq_schedutil.c:544`），
非数字返回 `-EINVAL`。写 0 是**合法的**（不做下界检查）—— 相当于取消节流。

**tunables 目录的位置取决于驱动有没有置 `CPUFREQ_HAVE_GOVERNOR_PER_POLICY`**：

| 驱动 | 目录 |
|---|---|
| 置了（如 `scmi-cpufreq.c:290`） | `policyN/<governor>/` —— 每个 policy 一份 |
| 没置 | `/sys/devices/system/cpu/cpufreq/<governor>/` —— 全局一份，改一处影响所有 policy |

⇒ 大小核板子上「改了小核的 tunable，大核也变了」不是 bug，是驱动没置这个标志。

### §4.6 一眼判断 policy 拓扑

```bash
grep . /sys/devices/system/cpu/cpufreq/policy*/affected_cpus
grep . /sys/devices/system/cpu/cpufreq/policy*/related_cpus
```

`related_cpus`（`drivers/cpufreq/cpufreq.c:881`）含**全部**成员 CPU；
`affected_cpus`（`:889`）只含**在线**的。
⇒ 两者不一致 = 有 CPU 下线了，**不是配置错误**（06 §7.5）。

---

## §5 debugfs

### §5.1 cpufreq 核心没有任何 debugfs

`drivers/cpufreq/` 下唯一用 debugfs 的是
`drivers/cpufreq/s3c24xx-cpufreq-debugfs.c`（arm32 S3C24xx，
根目录名 `"s3c-cpufreq"`，`:144`）—— **arm64 上不存在**。

核心（`cpufreq.c` / `freq_table.c` / `cpufreq_stats.c` / `cpufreq_governor.c`）
和 `kernel/sched/cpufreq_schedutil.c` 全部零个 `debugfs_*` 调用。

⇒ **cpufreq 的"debugfs"实际上是相邻两个子系统的 debugfs。**

### §5.2 OPP 的 debugfs —— 看频率表和电压

根目录 `/sys/kernel/debug/opp/`，建于 `drivers/opp/debugfs.c:278`；
每张 OPP 表一个子目录（`:181`），每个 OPP 一个子目录（`:151`）。

```bash
ls /sys/kernel/debug/opp/
# 典型：cpu0  cpu4   （按 policy 边界分，可以直接验证 06 §6.1）
ls /sys/kernel/debug/opp/cpu0/
cat /sys/kernel/debug/opp/cpu0/opp:*/rate_hz
cat /sys/kernel/debug/opp/cpu0/opp:*/supply-0/volt_target 2>/dev/null
```

**这是验证 06 §6.1（`opp-shared` 有没有生效）最直接的地方**：
目录数等于簇数就对了，等于 CPU 数就是 `opp-shared` 漏了。

⚠️ 高通硬件自治路线上 OPP 表来自硬件 LUT 而不是 DTS，
这里的内容和任何 DTS 文件都对不上 —— **正常**（06 §4.2）。

### §5.3 Energy Model 的 debugfs —— 看 EAS 起没起

根目录 `/sys/kernel/debug/energy_model/`，建于 `kernel/power/energy_model.c:96`；
每个 perf domain 一个子目录（`:73`），里面有 `cpus`（`:76`）和 `flags`（`:79`），
每个性能状态一个子目录（`:42`）。

```bash
ls /sys/kernel/debug/energy_model/ || echo "EM 没建起来"
cat /sys/kernel/debug/energy_model/*/cpus
```

三种结果的含义：

| 看到什么 | 含义 |
|---|---|
| 目录不存在 | `CONFIG_ENERGY_MODEL` 没开，或 DTS 漏了 `dynamic-power-coefficient`（06 §6.4） |
| 目录在，但只有一个 perf domain | ⚠️ 大小核被并成一个 ⇒ EAS 没有可选择的空间，等于白开 |
| 目录数 = 簇数，`cpus` 分组正确 | ✅ |

**EM 的 perf domain 边界和 cpufreq 的 policy 边界是两回事**
（04 §7.2 的三边界问题，08 篇专门讲）。这里能看到 EM 那一侧的实际结果，
和 §4.6 看到的 policy 侧对照，就能确认两边有没有对齐。

### §5.4 通用 debugfs 手段

| 路径 | 用途 |
|---|---|
| `/sys/kernel/debug/dynamic_debug/control` | 开 `pr_debug`（§2.3）—— 本子系统最重要的一个 |
| `/sys/kernel/debug/tracing/` | tracepoint（§3） |
| `/sys/kernel/debug/sched/` | schedutil 是调度器的一部分，负载侧的问题在这里看 |

---

## §6 故障注入

### §6.1 本子系统没有故障注入框架

`drivers/cpufreq/`、`kernel/sched/cpufreq_schedutil.c`、`include/linux/cpufreq.h`
全部零个 `fault_inject` / `should_fail` / `DECLARE_FAULT_ATTR`。

⇒ 想制造故障只能用下面这些"正常接口的极端用法"。

### §6.2 可用的替代手段

| 想复现的故障 | 怎么做 | 会看到什么 |
|---|---|---|
| 整个子系统不存在 | 命令行加 `cpufreq.off=1` | 目录消失，dmesg 无痕迹（§1.3） |
| 换成别的默认 governor | `cpufreq.default_governor=powersave` | 开机就顶在最低档 |
| governor 切换失败 | 把 CPU 全下线后写 `scaling_governor` | `-EBUSY`（§4.1） |
| 限值被外部压住 | 加载 thermal 并 `echo <温度> > /sys/class/thermal/thermal_zoneN/emul_temp`（需 `CONFIG_THERMAL_EMULATION`） | `scaling_max_freq` 被压低，`cpu_frequency_limits` 有记录（§3.3） |
| policy 变成不活跃 | `echo 0 > /sys/devices/system/cpu/cpuN/online` 把一个 policy 的所有 CPU 下线 | `affected_cpus` 变空，读写都 `-EBUSY` |
| `trans_table` 溢出 | 需要档位足够多，无法人为构造 | `-EFBIG` + 一次性 `pr_warn_once`（§2.2） |
| 快路径被禁用 | 加载任意一个注册 transition notifier 的模块 | `pr_warn("CPU%u: Fast frequency switching not enabled\n")`（`drivers/cpufreq/cpufreq.c:506-507`）+ 罪魁列表（`:508`），05 §2.2 |
| notifier 注册在快路径已启用之后 | 在 schedutil 跑起来后再注册 transition notifier | `cpufreq_register_notifier()` 返回 **`-EBUSY`**（`drivers/cpufreq/cpufreq.c:2053`），门在 `:2051` |
| 开机频率不在表里 | 改 bootloader 设一个非表内频率 | ⚠️ `BUG_ON`（`:1499`）或 `pr_info` 改档（`:1500`），取决于驱动有没有置 `CPUFREQ_NEED_INITIAL_FREQ_CHECK` |

### §6.3 最有用的一条：CPU 热插拔

```bash
# 让 policy0 的从核下线，观察 policy 是否降级
cat /sys/devices/system/cpu/cpufreq/policy0/affected_cpus
echo 0 > /sys/devices/system/cpu/cpu1/online
cat /sys/devices/system/cpu/cpufreq/policy0/affected_cpus   # 少了 1
cat /sys/devices/system/cpu/cpufreq/policy0/related_cpus    # 不变
```

它同时验证三件事：`related_cpus` / `affected_cpus` 的区别（§4.6）、
policy 边界是否真的按簇（§4.6）、以及驱动的 `->online`/`->offline`
回调有没有实现（05 §3.3）。

⚠️ **不要把 policy 的第一个 CPU（`policy->cpu`）下线来做实验** ——
核心会把 policy 迁到另一个成员核上（`drivers/cpufreq/cpufreq.c:1352` 附近的
`WARN_ON` 就是这条路上的断言），现象会变复杂，不适合入门排查。

---

## §7 错误码分诊

### §7.1 错误码普查

`drivers/cpufreq/cpufreq.c` 里 `return -E*` 共 **33** 处，分布极不均匀：

| 错误码 | 处数 | 主要含义 |
|---|---|---|
| `-EINVAL` | **22** | 一码多义最严重的一个，见 §7.3 |
| `-ENODEV` | 4 | 子系统被禁用 / 没有任何 CPU 初始化成功 |
| `-EIO` | 2 | sysfs 属性没有对应的 show/store；驱动 `->get()` 返回 0 |
| `-EBUSY` | 1 | 注册 transition notifier 时快路径已启用 |
| `-ENXIO` | 1 | boost 需要频率表但 policy 没有 |
| `-ENOMEM` | 1 | —— |
| `-ENODATA` | 1 | `setpolicy` 型驱动收到了非 performance/powersave 的策略 |
| `-EPROBE_DEFER` | 1 | CPU0 的 `struct device` 还没建好 |
| `-EEXIST` | 1 | 已经有别的驱动注册了 |

其余核心文件：

| 文件 | 错误码 |
|---|---|
| `drivers/cpufreq/freq_table.c` | `-EINVAL` `:64` `:226` `:316`；`-ENODEV` `:112` `:240`；`-ENOENT` `:219` |
| `drivers/cpufreq/cpufreq_stats.c` | `-EFBIG` `:169`（唯一一个） |
| `drivers/cpufreq/cpufreq_governor.c` | `-EINVAL` `:54` `:422` `:521`；`-EBUSY` `:410`；`-ENOMEM` `:414` `:434` |
| `drivers/cpufreq/cpufreq_governor_attr_set.c` | `-EBUSY` `:32`（唯一一个） |

### §7.2 「`/sys/.../cpufreq/` 目录不存在」分诊树

这是 07 篇最该先解决的问题，因为**它的四种原因里三种不打任何日志**。

```
/sys/devices/system/cpu/cpufreq/ 存在吗？
├─ 不存在（连全局目录都没有）
│  └─ cpufreq_core_init() 就没跑成
│     ├─ CONFIG_CPU_FREQ=n         → Kconfig（§1.1）
│     └─ cpufreq.off=1             → -ENODEV @ drivers/cpufreq/cpufreq.c:2986
│                                     ⚠️ 静默；查 /proc/cmdline（§1.3）
└─ 存在，但没有 policyN 子目录
   └─ 没有任何驱动成功 init
      ├─ 驱动根本没注册（模块没加载 / probe 没被调）
      │  ├─ 通用 DT 路线：cpufreq-dt-platdev.c 没给这块板子造设备
      │  │   → 根节点 compatible 不在 allowlist（:18）
      │  │     且 cpu@0 没有 operating-points-v2（:179）
      │  │   ⚠️ 静默（06 §7.4 最后一行）
      │  ├─ SCMI 路线：cpu 节点的 clocks 没指向 perf 协议
      │  │   → -ENODEV @ scmi-cpufreq.c:355   ⚠️ 静默（06 §3.2）
      │  └─ vendor 驱动没进 blocklist，和 cpufreq-dt 撞了
      │      → 后注册的那个拿到 -EEXIST @ drivers/cpufreq/cpufreq.c:2882
      │        ⚠️ 只有 pr_debug（:2874 后面）
      └─ 驱动注册了，但所有 ->init() 都失败
          → -ENODEV @ drivers/cpufreq/cpufreq.c:2912
            有 pr_debug："No CPU initialized for driver %s"（:2913）
            每个 policy 的失败还有 pr_err：
              "Failed to initialize policy for cpu: %d (%d)"（:1532）
            ⇒ 这一支是唯一"开了 pr_debug 就能定位"的
```

**诊断顺序固定为**：

```bash
# 1) 全局目录在不在（区分上下两大支）
ls -d /sys/devices/system/cpu/cpufreq/ 2>/dev/null || echo "核心没起来 → 查 Kconfig / cmdline"

# 2) cmdline（最便宜，且是静默失败）
tr ' ' '\n' < /proc/cmdline | grep -i cpufreq

# 3) 驱动到底注册了没
grep -i cpufreq /proc/modules
ls /sys/bus/platform/drivers/ | grep -i cpufreq

# 4) 开 pr_debug 重新加载驱动（模块化时才可行）
echo 'file drivers/cpufreq/cpufreq.c +p' > /sys/kernel/debug/dynamic_debug/control
modprobe -r cpufreq_dt && modprobe cpufreq_dt && dmesg | tail -30
```

⚠️ 第 4 步只在驱动是模块时可行。`CPU_FREQ_GOV_SCHEDUTIL` 是 `bool`（§1.1），
但 `CPUFREQ_DT` / `ARM_SCMI_CPUFREQ` 是 `tristate`，可以编成模块专门用来排查。

### §7.3 `-EINVAL` 和 `-EBUSY` 的多重来源

这两个码在本子系统里被复用得最厉害。按**你刚做了什么操作**来分：

**`-EINVAL`（22 处）—— 五个语义组**

| 组 | 触发场景 | 位置 | 怎么区分 |
|---|---|---|---|
| ① 用户态输入没解析出来 | 写 sysfs 时值不是数字 / governor 名字太长 | `drivers/cpufreq/cpufreq.c:748`（min/max）、`:797`（governor）、`:905`（setspeed）、`:622`（boost 非 0/1） | 换一个明显合法的值再试，还报错就不是这一组 |
| ② 名字查不到 | governor 名字拼错或没编进内核 | `drivers/cpufreq/cpufreq.c:812`（`cpufreq_parse_governor` 失败）、`:804`（`setpolicy` 型驱动的策略名） | 先 `cat scaling_available_governors` 对照 |
| ③ 前提条件不满足 | 写 `scaling_setspeed` 但当前 governor 没有 `store_setspeed` | `drivers/cpufreq/cpufreq.c:901` | 这就是 05 §7.3 说的「只有 userspace governor 下才有意义」 |
| ④ 子系统被禁用 | `cpufreq.off=1` 时调 notifier 注册/注销 | `drivers/cpufreq/cpufreq.c:2045`、`:2089` | 内核内 API 才会碰到 |
| ⑤ 内核内调用参数非法 | notifier list 编号未知（`:2067`、`:2107`）；`cpufreq_register_governor(NULL)`（`:2471`）；`cpufreq_get_policy` 参数为空或 CPU 无 policy（`:2533`、`:2537`）；`cpufreq_unregister_driver` 传了别的驱动（`:2959`）；`cpufreq_enable_boost_support` 时还没有驱动（`:2803`）；驱动 ops 组合非法（`:2872`） | | 只出现在驱动开发阶段 |

**`:2872` 那一条最值得单独记**，它是 05 §8.2 那张必填字段表的执行者。
条件在 `drivers/cpufreq/cpufreq.c:2865-2871`，六个"或"里任意一个成立就 `-EINVAL`：

| 违规 | 条件所在行 |
|---|---|
| 没有 `->verify` 或没有 `->init` | `:2865` |
| 三个下发回调（`setpolicy` / `target_index` / `target`）**一个都没有** | `:2866-2867` |
| 有 `setpolicy` 却同时有 `target_index` 或 `target` | `:2868-2869` |
| `get_intermediate` 和 `target_intermediate` 只填了一个 | `:2870` |
| `online` 和 `offline` 只填了一个 | `:2871` |

⇒ 新写驱动时拿到 `-EINVAL` 且 dmesg 里连 `trying to register driver`
（`:2874`，pr_debug）都没有 ⇒ 一定是这五条之一（校验发生在那条 pr_debug **之前**）。

**governor 相关的 `-EINVAL` 还有两处**（换 governor 失败时看这里）：

| 位置 | 含义 |
|---|---|
| `drivers/cpufreq/cpufreq.c:2370` | policy 上压根没有 governor |
| `drivers/cpufreq/cpufreq.c:2382` | 驱动置了 `CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING`，要求的 governor 是动态型，而且**连 fallback governor 都拿不到**。⚠️ 能拿到 fallback 时不报错，而是 `pr_warn` 后偷偷换掉（`:2378-2380`）—— 这条 warn 是唯一线索 |
| `drivers/cpufreq/cpufreq.c:2387` | `try_module_get(governor->owner)` 失败（governor 模块正在卸载） |
| `drivers/cpufreq/cpufreq.c:2425` | stop 时 policy 上没有 governor |
| `drivers/cpufreq/cpufreq_governor.c:422` | 驱动置了 `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` 却撞上全局 tunables，伴随 `WARN_ON`（`:421`，§2.4） |

**`-EBUSY`（三个完全不同的来源）**

| 来源 | 位置 | 含义 | 怎么认出来 |
|---|---|---|---|
| ① policy 不活跃 | `drivers/cpufreq/cpufreq.c:971`（读）、`:989`（写） | 该 policy 的 CPU 全下线了 | `cat affected_cpus` 是空的 |
| ② tunables 已无人使用 | `drivers/cpufreq/cpufreq_governor_attr_set.c:32` | governor 正在被换掉 | 路径里带 governor 名字 |
| ③ 快路径已启用 | `drivers/cpufreq/cpufreq.c:2053`，门在 `:2051` | 想注册 transition notifier，但已经有驱动在用 `->fast_switch` | 内核内 API；05 §6.3 |
| ④ dbs governor 状态残留 | `drivers/cpufreq/cpufreq_governor.c:410` | `policy->governor_data` 非空（应当等价于 EXIT 态），注释 `:408` | 驱动/governor 开发阶段 |

⇒ **同一个 `Device or resource busy`，四种完全不同的处置**。
用户态只会碰到 ①②；③④ 是内核内的。

### §7.4 其余错误码逐条

| 错误码 | 位置 | 含义与处置 |
|---|---|---|
| `-EIO` | `drivers/cpufreq/cpufreq.c:974` / `:992` | 这个属性只读或只写。不是硬件 I/O 错误 |
| `-EIO` | `drivers/cpufreq/cpufreq.c:1460` | 驱动 `->get()` 返回 0。伴随 `pr_err("%s: ->get() failed\n")`（`:1461`）。arm64 上头号原因是 `policy->clk` 没设（05 §1.2） |
| `-ENODEV` | `drivers/cpufreq/cpufreq.c:2301` | `cpufreq_driver_target()` 在子系统禁用时被调 |
| `-ENODEV` | `drivers/cpufreq/cpufreq.c:2474` | 注册 governor 时子系统已禁用 |
| `-ENODEV` | `drivers/cpufreq/cpufreq.c:2856` / `:2986` | `cpufreq.off=1`（§1.3） |
| `-ENODEV` | `drivers/cpufreq/cpufreq.c:2912` | 所有 `->init()` 都失败（§7.2） |
| `-ENODEV` | `drivers/cpufreq/freq_table.c:112` / `:240` | policy 没有频率表。用通用 `verify` / 通用 attr 但没填 `policy->freq_table` |
| `-ENOENT` | `drivers/cpufreq/freq_table.c:219` | 在频率表里找不到目标档位 |
| `-EEXIST` | `drivers/cpufreq/cpufreq.c:2882` | 已经有驱动注册了（`cpufreq_driver` 是全局单例）。这是 vendor 驱动没进 blocklist 的表现（06 §5.3、§7.2） |
| `-EPROBE_DEFER` | `drivers/cpufreq/cpufreq.c:2863` | `get_cpu_device(0)` 还是空 —— 注册太早了。注释 `:2858-2861` 说明了为什么必须有 `struct device` |
| `-ENXIO` | `drivers/cpufreq/cpufreq.c:2726` | 软件 boost 需要频率表，但 `policy->freq_table` 是空。⚠️ 只可能出现在自建表却漏填的驱动上 |
| `-ENODATA` | `drivers/cpufreq/cpufreq.c:1112` | `setpolicy` 型驱动收到非 performance/powersave 策略。arm64 上不会碰到（无 `setpolicy` 驱动，§4.2） |
| `-EFBIG` | `drivers/cpufreq/cpufreq_stats.c:169` | `trans_table` 超一页（§2.2） |
| `-ENOMEM` | `drivers/cpufreq/cpufreq.c:1365` / `:1421`；`drivers/cpufreq/cpufreq_governor.c:414` / `:434` | 字面含义 |

### §7.5 三个"看起来像错却不是错"的返回

| 现象 | 真相 |
|---|---|
| `cpufreq_start_governor()` 在 suspend 中直接返回 **0** | `drivers/cpufreq/cpufreq.c:2363-2364`：suspend 期间不启动 governor，这是**成功**。同样 stop 在 `:2421-2422` |
| `cpufreq_enable_boost_support()` 第二次调返回 **0** | `drivers/cpufreq/cpufreq.c:2805-2806`：已经支持就当成功，幂等 |
| `cpufreq_frequency_table_get_index()` 返回 `-EINVAL` 被当作正常分支 | `drivers/cpufreq/cpufreq.c:1490`：核心用它判断"开机频率不在表里"，然后主动改档（`:1491-1492`）。这不是错误处理，是**探测手段** |

---

## §8 一页速查

### §8.1 最常用的十条命令

```bash
P=/sys/devices/system/cpu/cpufreq/policy0
T=/sys/kernel/debug/tracing

# 1 全景（一条看完拓扑 + 驱动 + governor + 档位）
grep . $P/{affected_cpus,related_cpus,scaling_driver,scaling_governor} \
        $P/{cpuinfo_min_freq,cpuinfo_max_freq,scaling_min_freq,scaling_max_freq}

# 2 governor 在不在工作（跑一会儿负载再读一次，不涨就是没在调频）
cat $P/stats/total_trans

# 3 频率分布（判断卡在哪一档）
cat $P/stats/time_in_state

# 4 硬件真实频率 vs 内核记账值（需 root）
cat $P/cpuinfo_cur_freq $P/scaling_cur_freq

# 5 开 pr_debug（本子系统 90% 的打印在这里）
echo 'file drivers/cpufreq/cpufreq.c +p' > /sys/kernel/debug/dynamic_debug/control

# 6 看内核下发的频率
echo 1 > $T/events/power/cpu_frequency/enable; echo 1 > $T/tracing_on

# 7 看限值被谁改了
echo 1 > $T/events/power/cpu_frequency_limits/enable
echo 1 > $T/events/power/pm_qos_update_target/enable

# 8 OPP 表（验证 opp-shared 有没有生效）
ls /sys/kernel/debug/opp/

# 9 Energy Model（验证 EAS 起没起）
ls /sys/kernel/debug/energy_model/

# 10 目录不存在时的第一步
tr ' ' '\n' < /proc/cmdline | grep -i cpufreq
```

### §8.2 观测手段一览

| 手段 | 有没有 | 位置 | 说明 |
|---|---|---|---|
| Kconfig 开关 | ✅ 本篇整理 18 个 | `drivers/cpufreq/Kconfig`（全文 26 个 `config`）+ `.arm` | §1.1 |
| 命令行参数 | ✅ **2 个** | `drivers/cpufreq/cpufreq.c:2996-2997` | 都是 `0444` |
| `pr_err` / `pr_warn` / `pr_info` | ✅ 少量 | —— | §2.1 |
| `pr_debug` | ✅ **主力** | —— | §2.3，必须开 dynamic_debug |
| `_once` 变体 | ✅ **1 个**（arm64 相关范围内） | `drivers/cpufreq/cpufreq_stats.c:168` | §2.2 |
| `_ratelimited` 变体 | ❌ **0 个** | —— | 不存在日志被限流 |
| tracepoint（核心） | ✅ **2 个** | `include/trace/events/power.h:167` `:174` | §3 |
| tracepoint（arm64 驱动） | ❌ **0 个** | —— | §3.1 |
| `TP_CONDITION` | ❌ 0 个 | —— | 打开即全量 |
| sysfs（policy） | ✅ 15 个文件 | `drivers/cpufreq/cpufreq.c:933-946` | §4，05 §7.1 |
| sysfs（stats） | ✅ 4 个文件 | `drivers/cpufreq/cpufreq_stats.c:175-180` | §4.4 |
| sysfs（tunables） | ✅ 按 governor | §4.5 的表 | §4.5 |
| debugfs（cpufreq 自己） | ❌ **0 个**（arm64） | —— | §5.1 |
| debugfs（OPP） | ✅ | `drivers/opp/debugfs.c:278` | §5.2 |
| debugfs（Energy Model） | ✅ | `kernel/power/energy_model.c:96` | §5.3 |
| 故障注入框架 | ❌ **0 个** | —— | §6.1 |
| WARN / BUG | ✅ 12 处 | §2.4 的表 | 都是"调用者用错" |

### §8.3 三条最容易踩的观测陷阱

1. **dmesg 干净 ≠ 正常**。本子系统的关键打印几乎全是 `pr_debug`，
   而"目录不存在"的三种原因中有三种一个字都不打（§7.2）。
2. **arm64 上 `scaling_cur_freq` 读的是内核记账值**，
   不是硬件寄存器（§4.2）。硬件自治路线上它看不出硬件在限频。
3. **写 `scaling_max_freq` 是投票不是赋值**（§4.3），
   读回来不变很可能是别人压得更低，而不是写失败。

### §8.4 读完这篇去哪

| 下一步 | 去哪 |
|---|---|
| DTS 属性配错的定位（和本篇互补） | 06 §6、06 §7 |
| 为什么会有 policy / perf domain / OPP 三套边界 | 08 篇 |
| 各 governor 的算法和参数含义 | 04 §2 |
| 快速复习整个子系统 | 00 篇 |
| 本套文档的取证范围和未验证项 | 99 篇 |
