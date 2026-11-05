---
title: 取证附录
---

# cpufreq 文档集取证与方法附录

**这一篇的定位不是内容，是「可信度说明书」。** 它回答三个问题：

1. 前面九篇里的数字和行号是怎么来的、怎么重跑（§1、§3、§8）
2. 哪些结论是**读过代码**的，哪些是**只看了符号表**的，哪些是**推断**的（§2、§5）
3. 写作过程中哪些初始假设被源码推翻了（§7）

⚠️ **本篇的失效方式是"过期"而不是"写错"。** 只要树被更新，§1 和 §3 的每一个数字
都必须重跑；有一个对不上，整篇就从"取证"降级成"回忆"。

---

## §1 基线

### §1.1 版本与树状态

```bash
git log -1 --format='%H %s'
# f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158
```

**本地树整体是脏的**（大量子系统有本地修改），所以必须逐子系统确认。
判定命令和结果：

```bash
git status --porcelain -- \
    drivers/cpufreq include/linux/cpufreq.h include/linux/sched/cpufreq.h \
    kernel/sched/cpufreq.c kernel/sched/cpufreq_schedutil.c \
    Documentation/cpu-freq Documentation/admin-guide/pm \
    Documentation/devicetree/bindings/cpufreq
```

⇒ **输出 0 行。cpufreq 子系统及其文档没有被任何本地补丁改动。**
所以本文档集描述的就是**上游 6.1.158 的原样行为**，行号可以直接对照
`git show v6.1.158` 或 kernel.org 的 6.1.y 源码。

### §1.2 规模（一等证据，命令可重跑）

```bash
wc -l drivers/cpufreq/cpufreq.c include/linux/cpufreq.h \
      kernel/sched/cpufreq_schedutil.c kernel/sched/cpufreq.c \
      include/linux/sched/cpufreq.h \
      drivers/cpufreq/freq_table.c drivers/cpufreq/cpufreq_stats.c \
      drivers/cpufreq/cpufreq_governor.c drivers/cpufreq/cpufreq_governor_attr_set.c \
      drivers/cpufreq/cpufreq_ondemand.c drivers/cpufreq/cpufreq_conservative.c \
      drivers/cpufreq/cpufreq_performance.c drivers/cpufreq/cpufreq_powersave.c \
      drivers/cpufreq/cpufreq_userspace.c \
      drivers/cpufreq/Kconfig drivers/cpufreq/Kconfig.arm
```

| 文件 | 行数 |
|---|---|
| `drivers/cpufreq/cpufreq.c` | **2998** |
| `include/linux/cpufreq.h` | **1269** |
| `kernel/sched/cpufreq_schedutil.c` | **895** |
| `drivers/cpufreq/cpufreq_governor.c` | 580 |
| `drivers/cpufreq/cpufreq_ondemand.c` | 487 |
| `drivers/cpufreq/freq_table.c` | 370 |
| `drivers/cpufreq/Kconfig.arm` | 359 |
| `drivers/cpufreq/cpufreq_conservative.c` | 344 |
| `drivers/cpufreq/Kconfig` | 325 |
| `drivers/cpufreq/cpufreq_stats.c` | 290 |
| `drivers/cpufreq/cpufreq_userspace.c` | 142 |
| `drivers/cpufreq/cpufreq_governor_attr_set.c` | **76** |
| `kernel/sched/cpufreq.c` | **74** |
| `drivers/cpufreq/cpufreq_performance.c` | 45 |
| `drivers/cpufreq/cpufreq_powersave.c` | 39 |
| `include/linux/sched/cpufreq.h` | **38** |
| 合计（上表） | 8331 |

驱动目录规模：

```bash
ls drivers/cpufreq/*.c | wc -l      # 89
cat drivers/cpufreq/*.c | wc -l     # 39396
```

⇒ 驱动目录 **89 个 `.c` / 39396 行**，是核心（8331 行）的 4.7 倍。
这个比例直接决定了 §2.2 的取样策略：**驱动只能讲契约 + 读代表**。

### §1.3 子系统类型判定

```bash
grep -rn 'struct bus_type' drivers/cpufreq/ include/linux/cpufreq.h | wc -l   # 0
```

⇒ **0 处，判定为「框架型」子系统**（无自己的 `bus_type`）。

但它和别的框架型子系统（reset、mailbox）有一个重要区别：
**它有一套完整的 sysfs 界面**（`/sys/devices/system/cpu/cpufreq/`，15 个 policy
属性 + 4 个 stats 文件 + 1 个全局 `boost`）。
⇒ 所以 07 篇的分量比一般框架型子系统重得多，02 篇的"总线关系图"则改成了
"核心抽象 ↔ 消费者"图。

### §1.4 Documentation 覆盖登记（三等证据）

⚠️ 内核文档偶尔落后于代码。**凡文档与代码冲突处一律以代码为准**，
并在正文显式指出（本篇 §7 有实例）。

读了哪些：

| 文件 | 行数 | 纳入到哪 | 备注 |
|---|---|---|---|
| `Documentation/cpu-freq/core.rst` | 113 | 03 篇的通知链、governor 契约 | 概念仍准确 |
| `Documentation/cpu-freq/cpu-drivers.rst` | 286 | 05 篇的驱动骨架、回调必选性 | 最有用的一篇 |
| `Documentation/cpu-freq/cpufreq-stats.rst` | 136 | 07 篇的 `stats/` 语义 | `reset` 的延迟语义文档里没写，靠读码补 |
| `Documentation/cpu-freq/index.rst` | 39 | —— | 只是目录 |
| `Documentation/admin-guide/pm/cpufreq.rst` | **708** | 04 篇 governor 家族、07 篇 sysfs 与 tunable | **手工登记**：`check_doc_coverage.sh` 的探测覆盖不到 `admin-guide/pm/` 下的单文件 |
| `Documentation/admin-guide/pm/cpufreq_drivers.rst` | 274 | **未纳入** | 标题自称 "Legacy Documentation"，内容是 AMD PowerNow! / x86 老驱动，按 §2.7 全部裁掉 |
| `Documentation/devicetree/bindings/cpufreq/` | 12 篇 | 06 篇 §8.1 的绑定文档表 | 见下面的 ⚠️ |

⚠️ **`Documentation/devicetree/bindings/cpufreq/` 里没有"通用属性"文档。**
12 篇全是平台专属（`cpufreq-dt.txt` 除外，而它也只讲"去看 opp 绑定"）：
真正的通用属性（`operating-points-v2`、`opp-hz`、`opp-shared`、`opp-microvolt`、
`turbo-mode`、`dynamic-power-coefficient`）**全部定义在
`Documentation/devicetree/bindings/opp/` 下**。
⇒ 这一条是 06 篇的一个结构性结论：**查 cpufreq 的 DT 属性要去 opp 目录查**。

`check_doc_coverage.sh` 的执行记录见 §8.2。

---

## §2 证据等级与阅读范围

三级定义（本文档集通用）：

| 等级 | 定义 | 可信度 |
|---|---|---|
| **一等** | 逐行读过全文，且正文引用的行号都用 `sed` 打开核对过 | 可直接使用 |
| **二等** | 只读了**列出的那几段**（下面写明范围），其余靠符号表和调用关系推断 | 用前建议自查 |
| **三等** | 来自 `Documentation/`（可能落后于代码） | 与代码冲突时以代码为准 |

**一等和二等的分界线为什么这么划**：
判据是"这个文件里有没有我需要解释的**控制流分岔**"。
`cpufreq.c` 每一个 `if` 都可能决定 sysfs 里出不出现某个文件，所以必须全读；
而 `cpufreq_ondemand.c` 我只需要知道它的 tunable 有哪几个、默认值多少 ——
读符号表和那张 `default_attrs[]` 数组就够了。

### §2.1 一等证据（逐行读过）

| 文件 | 行数 |
|---|---|
| `drivers/cpufreq/cpufreq.c` | 2998（全文） |
| `include/linux/cpufreq.h` | 1269（全文） |
| `kernel/sched/cpufreq.c` | 74（全文） |
| `include/linux/sched/cpufreq.h` | 38（全文） |
| `drivers/cpufreq/freq_table.c` | 370（全文） |
| `drivers/cpufreq/cpufreq_stats.c` | 290（全文） |
| `drivers/cpufreq/cpufreq_governor_attr_set.c` | 76（全文） |
| `drivers/cpufreq/cpufreq_performance.c` | 45（全文） |
| `drivers/cpufreq/cpufreq_powersave.c` | 39（全文） |
| `drivers/cpufreq/Kconfig` | 325（全文） |

### §2.2 二等证据（只读了列出的范围）

⚠️ 这一节是本篇最该被认真读的部分 —— 它标出了**哪些结论我没有全上下文**。

| 文件 | 详读了什么 | 没读的部分 |
|---|---|---|
| `kernel/sched/cpufreq_schedutil.c`（895） | `sugov_should_update_freq` 及其阈值比较、三个 `sugov_update_*` 的入口和节流判断、`sugov_start` 的钩子安装与分支、`rate_limit_us` 的 show/store、`sugov_policy` 的 `freq_update_delay_ns` 字段、假带宽注释段 | **util 的具体计算**（`sugov_get_util` / `effective_cpu_util` 一带）—— 04 篇讲的是"它从调度器拿 util"，**没有**解释 util 怎么算出来的 |
| `drivers/cpufreq/cpufreq_governor.c`（580） | 错误码返回点、`WARN_ON(have_governor_per_policy())`、dbs 状态残留那条 `-EBUSY` 及其注释 | 采样定时器的完整生命周期 |
| `drivers/cpufreq/cpufreq_ondemand.c`（487） | `default_attrs[]` 数组和六个 tunable 的定义行、arm64 默认阈值 | 采样算法主体 |
| `drivers/cpufreq/cpufreq_conservative.c`（344） | 同上（六个 tunable + 数组） | 采样算法主体 |
| `drivers/cpufreq/cpufreq_userspace.c`（142） | `store_setspeed` 契约 | 其余 |
| `drivers/cpufreq/Kconfig.arm`（359） | 07 篇表里点名的 6 个 `config` 条目 | 其余 arm32 平台条目 |
| `include/trace/events/power.h` | `DECLARE_EVENT_CLASS(cpu, ...)`、`cpu_frequency`、`cpu_frequency_limits`、`pm_qos_update_target` 四处的字段与格式串 | 其余事件（x86 / PowerPC 相关的按 §2.7 裁掉） |
| `include/linux/energy_model.h` | `struct em_perf_domain` 及其注释、`em_cpu_get` / `em_dev_register_perf_domain` / `em_pd_get_efficient_state` / `em_cpu_energy` 的声明位置、空实现桩 | EM 的能量计算实现（在 `kernel/power/energy_model.c`，只读了 debugfs 那几处） |
| `drivers/base/arch_topology.c` | `arch_freq_scale` 的定义、`topology_set_freq_scale` 的入口与赋值行 | 其余（capacity 相关） |
| `drivers/opp/of.c` / `core.c` / `cpu.c` / `debugfs.c` | **只读了 cpufreq 会碰到的那些点**：`opp-shared` 判定、`opp-microvolt` 两条 `dev_err`、`turbo-mode` 读取、`dynamic-power-coefficient` 读取、`CPUFREQ_BOOST_FREQ` 打标、OPP 表查找失败的 `pr_debug`、三层 debugfs 目录创建 | **OPP 框架的绝大部分**。凡本文档集说"OPP 会做 X"，除上列各点外都是**契约级描述**，不是读码结论 |

**代表驱动的选法**（89 个驱动只读 5 个，选法登记）：

| 驱动 | 为什么选它 | 读到什么程度 |
|---|---|---|
| `cpufreq-dt.c` | 通用 DT 路线的**唯一**实现，arm64 上最常见 | 详读 probe / init / boost 判定 |
| `cpufreq-dt-platdev.c` | 决定"这块板子归不归通用路线"的白名单，**静默失败的源头** | 详读白名单机制 |
| `scmi-cpufreq.c` | 固件代理路线代表；唯一同时有 `fast_switch` 和 per-policy tunable 的代表 | 详读 probe / fast_switch / 共享判定 |
| `qcom-cpufreq-hw.c` | 硬件自治路线代表；频率表不来自 DTS | 详读 LUT 读取 / freq-domain / boost |
| `sun50i-cpufreq-nvmem.c` · `tegra194-cpufreq.c` | eFuse 分档 / 特殊平台，作为"还有这种形态"的存在性证据 | **只读符号表**，未详读 |

⇒ 未被覆盖的 84 个驱动里，凡本文档集提到的（`imx`、`mediatek`、`ti` 等）
**都只到 Kconfig 条目和文件名级别**，不构成行为断言。

### §2.7 明确裁掉了什么

**架构裁剪：arm64 only。** 以下内容**故意不写**，不是遗漏：

| 裁掉的 | 为什么 | 会造成什么阅读差异 |
|---|---|---|
| `intel_pstate.c` | x86 | 它**整体替换 governor 层**（自带内部 governor），所以"governor 决策 / driver 执行"这个三层模型在 x86 上不成立。本文档集的三层模型只对 arm64 保证 |
| `amd-pstate.c` | x86 | 同上（CPPC 路线） |
| `acpi-cpufreq.c` | x86 + ACPI | —— |
| `p4-clockmod.c` · `powernow-k8.c` | x86 老驱动 | 只在 §3 的 `_once` 普查里作为"另外 8 处在哪"的说明出现 |
| `powernv-cpufreq.c` | PowerPC | 同上；它还带一个 `powernv_throttle` tracepoint |
| **ACPI 相关的全部内容** | 用户明确要求不写 | `_PSD`、CPPC、`cpufreq_boost` 的 ACPI 路径都不出现 |
| `arch_freq_get_on_cpu` 的 x86 实现（`aperf`/`mperf`） | x86 | **但它的缺席本身是 arm64 的一条关键结论**，所以在 07 篇里作为对照保留了一行 |
| `pstate_sample` tracepoint | x86（intel_pstate 专用） | 07 篇的 tracepoint 清单因此只剩 2 条 |
| `powernv_throttle` tracepoint | PowerPC | 同上 |
| `s3c24xx-cpufreq-debugfs.c` 的用法 | arm32 | 它的**存在**被保留了（用来说明"cpufreq 核心零 debugfs，唯一的 debugfs 在一个 arm32 驱动里"） |
| `Documentation/admin-guide/pm/cpufreq_drivers.rst` | 自称 legacy，内容全是 x86 | 见 §1.4 |
| arm32 专属的 Kconfig 条目 | 架构 | `Kconfig.arm` 只登记了 6 个 arm64 相关条目 |

**裁剪对 07 篇影响最大**：观测手段的数量（tracepoint 2 条、`_once` 1 处）
都是**arm64 范围内**的数字。不收窄范围的话，`_once` 是 9 处、tracepoint 是 4 条。

---

## §3 能力普查（全部可一条命令重跑）

### §3.1 07 篇的观测手段普查

⚠️ 下面每一行的"命令"都在 `drivers/cpufreq/` 或指定文件上跑；
**结果是 arm64 相关范围内的数字**（裁剪见 §2.7）。

| 普查项 | 结果 | 命令 |
|---|---|---|
| `cpufreq.c` 里 `return -E*` 总数 | **33** | `grep -c 'return -E' drivers/cpufreq/cpufreq.c` |
| 其中 `-EINVAL` | **22** | `grep -o 'return -E[A-Z]*' drivers/cpufreq/cpufreq.c \| sort \| uniq -c` |
| 其中 `-ENODEV` | 4 | 同上 |
| 其中 `-EIO` | 2 | 同上 |
| 其中 `-EBUSY` / `-ENXIO` / `-ENOMEM` / `-ENODATA` / `-EPROBE_DEFER` / `-EEXIST` | 各 1 | 同上 |
| `_once` 变体（核心 + arm64 驱动） | **1** | `grep -rn 'pr_warn_once\|pr_info_once\|pr_err_once' drivers/cpufreq/*.c` |
| `_once` 变体（含 x86/PPC 驱动） | 9 | 同上（另外 8 处见 §2.7） |
| `_ratelimited` 变体 | **0** | `grep -rn '_ratelimited' drivers/cpufreq/*.c` |
| 带 `trace_` 调用的驱动文件 | **4 个，全部是 x86/PPC** | `grep -rln 'trace_' drivers/cpufreq/*.c` |
| ⇒ arm64 驱动的 tracepoint 数 | **0** | 上一行的补集 |
| 核心自己的 tracepoint 调用点 | 2 处（都在 `cpufreq.c`） | 同上 |
| `power.h` 里的 `TP_CONDITION` | **0** | `grep -c 'TP_CONDITION' include/trace/events/power.h` |
| `drivers/cpufreq/` 下带 debugfs 的文件 | 只有 arm32 的 s3c24xx 那一组 + Kconfig/Makefile | `grep -rln 'debugfs' drivers/cpufreq/` |
| ⇒ cpufreq **核心**的 debugfs 数 | **0** | 上一行结果里没有 `cpufreq.c` |
| 故障注入设施 | **0** | `grep -rn 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR' drivers/cpufreq/ kernel/sched/cpufreq_schedutil.c include/linux/cpufreq.h` |
| `cpufreq.c` 的 `WARN_ON` / `BUG_ON` 总数 | **12** | `grep -c 'WARN_ON\|WARN(\|BUG_ON' drivers/cpufreq/cpufreq.c` |
| 核心的模块参数 | **2**（都是 `0444`） | `grep -n 'module_param' drivers/cpufreq/cpufreq.c` |
| `drivers/cpufreq/Kconfig` 的 `config` 条目 | **26**（07 篇整理了其中 18 个 + `.arm` 的 6 个） | `grep -c '^config ' drivers/cpufreq/Kconfig` |
| `cpufreq.c` 的 `EXPORT_SYMBOL` | 37 | `grep -c 'EXPORT_SYMBOL' drivers/cpufreq/cpufreq.c` |
| `freq_table.c` 的 `EXPORT_SYMBOL` | 8 | `grep -c 'EXPORT_SYMBOL' drivers/cpufreq/freq_table.c` |

**`EXPORT_SYMBOL` 的行号不能当定义行用。** 01 篇整理 API 时踩过这个：
`grep EXPORT_SYMBOL` 给的是导出宏所在行，函数定义在它上面几十行。
正确做法是拿到符号名之后再 `grep -n '^unsigned int foo\|^void foo'` 找定义行。
⇒ 01 篇里所有位置只写到**文件**（这也是格式规则要求的），行号只在 03/07/99 里出现。

### §3.2 05 篇的符号存在性验证

05 篇是唯一会被读者**直接抄进代码**的一篇，所以正文出现的每个符号都跑过存在性验证。
统一命令形式：

```bash
grep -rn '\b<符号名>\b' drivers/cpufreq/ include/linux/cpufreq.h \
                        include/linux/sched/cpufreq.h kernel/sched/
```

**空结果 = 这个符号是幻觉**，不管当时多确信。验证过的清单：

| 类别 | 验证过的符号 |
|---|---|
| 驱动注册 | `cpufreq_register_driver` · `cpufreq_unregister_driver` · `struct cpufreq_driver` |
| 驱动回调 | `->init` · `->exit` · `->verify` · `->target_index` · `->fast_switch` · `->get` · `->set_boost` · `->attr` · `->online` / `->offline` |
| 通用辅助 | `cpufreq_generic_init` · `cpufreq_generic_get` · `cpufreq_generic_frequency_table_verify`（在 `freq_table.c`）· `cpufreq_table_validate_and_sort` |
| 频率表 | `struct cpufreq_frequency_table` · `CPUFREQ_TABLE_END` · `CPUFREQ_ENTRY_INVALID` · `CPUFREQ_BOOST_FREQ` · `cpufreq_for_each_valid_entry` |
| policy 侧 | `struct cpufreq_policy` · `policy_is_shared` · `cpufreq_cpu_get` / `_put` · `cpufreq_cpu_get_raw` |
| governor 侧 | `struct cpufreq_governor` · `cpufreq_register_governor` · `->start` / `->stop` / `->limits` · `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` |
| 调度器接口 | `struct update_util_data` · `cpufreq_add_update_util_hook` · `cpufreq_remove_update_util_hook` · `cpufreq_this_cpu_can_update` · `map_util_freq` · `map_util_perf` |
| EM / OPP | `cpufreq_register_em_with_opp` · `dev_pm_opp_of_register_em` · `dev_pm_opp_set_rate` · `dev_pm_opp_of_get_sharing_cpus` |
| 标志 | `CPUFREQ_NEED_INITIAL_FREQ_CHECK` · `CPUFREQ_IS_COOLING_DEV` · `CPUFREQ_ASYNC_NOTIFICATION` · `CPUFREQ_CONST_LOOPS` |
| 内核打印的字符串 | `"Fast frequency switching not enabled"` · `"cpufreq transition table exceeds PAGE_SIZE. Disabling"` · `"opp-microvolt missing although OPP managing regulators"` · `"opp-microvolt wasn't expected"` · `"Freq table is unsorted"` · `"No CPU initialized for driver"` · `"trying to register driver"` |

**"名字很像"是高危信号。** 本子系统里的近邻对：

| 一对 | 区别 |
|---|---|
| `cpufreq_cpu_get()` vs `cpufreq_cpu_get_raw()` | 前者拿引用（必须 `put`），后者不拿 |
| `policy->cpus` vs `policy->related_cpus` | 在线成员 vs 频率域全部成员 |
| `fast_switch_possible` vs `fast_switch_enabled` | 能力 vs 现状 |
| `scaling_cur_freq` vs `cpuinfo_cur_freq` | 记账值 vs 硬件值（07 篇的头号结论） |
| `scaling_min_freq` vs `cpuinfo_min_freq` | 限值（可写，是投票）vs 硬件下限（只读） |
| `cpufreq_driver_target()` vs `cpufreq_driver_fast_switch()` | 慢路 vs 快路，**代码完全分开** |
| `Documentation/cpu-freq/` vs `Documentation/admin-guide/pm/cpufreq.rst` | 两处都在讲 cpufreq，前者面向驱动作者，后者面向管理员 |

### §3.3 三个自检脚本的执行结果

见 §8.2（它们是收尾动作，结果和命令一起登记在那里）。

---

## §4 逐篇取证表

每篇列"关键论断 → 出处"，以及**自审阶段改掉了什么**。

### §4.1 00 篇（总览）

| 论断 | 出处 |
|---|---|
| 核心 2998 行 / 驱动 89 个 39396 行 | §1.2 的 `wc -l` |
| 框架型，无 `bus_type` | §1.3 的 grep（0 处） |
| arm64 默认 governor 是 schedutil | `drivers/cpufreq/Kconfig:41` 的 `default ... if ARM64 \|\| ARM` |
| schedutil 不能编成模块 | `drivers/cpufreq/Kconfig:191` 是 `bool` |
| 调度器耦合面 112 行 | `kernel/sched/cpufreq.c`（74）+ `include/linux/sched/cpufreq.h`（38） |
| 频率不变性接线 | `drivers/base/arch_topology.c:130`/`:150` · `arch/arm64/include/asm/topology.h:23` · `drivers/cpufreq/cpufreq.c:62`/`:65`/`:2902`/`:2934`/`:2967` |
| 初始频率核对会 `BUG_ON` | `drivers/cpufreq/cpufreq.c:1490-1500`（注释 `:1494-1498`） |

**§2 硬件背景一节全部是三等以下证据** —— 见 §5.4。

**自审修正**：初稿把 `cpufreq_governor_attr_set.c` 漏在规模表外（它 76 行，
是"tunable 的第二个 `-EBUSY`"的所在），补进了 §1.2 的一等证据清单。

### §4.2 01 篇（API 与代码地图）

| 论断 | 出处 |
|---|---|
| API 清单 | `include/linux/cpufreq.h` 全文 + `EXPORT_SYMBOL` 普查（§3.1） |
| 错误码语义 | `drivers/cpufreq/cpufreq.c` 的 33 个 `return -E*`（§3.1），逐个读上下文 |
| 位置只到文件 | 格式规则；行号在 03/07/99 维护 |

**自审修正**：`EXPORT_SYMBOL` 的行号被误当成定义行 —— 全部改为只写文件名
（做法见 §3.1 的 ）。

### §4.3 02 篇（数据结构）

| 论断 | 出处 |
|---|---|
| 三张 CPU 掩码及其注释原文 | `include/linux/cpufreq.h:60` / `:61` / `:62` |
| `policy->cpu` 是"代表"不是"主人" | `include/linux/cpufreq.h:66` 的注释 `must be online` + 代表核下线时的迁移路径（`drivers/cpufreq/cpufreq.c:1352` 的 `WARN_ON`） |
| policy 不是 `struct device`、不绑 driver | §1.3 的 0 处 `bus_type` + 结构体里无 `struct device` 成员 |
| 快慢标志拆两个字段的理由 | `include/linux/cpufreq.h:109-114` 的注释 |
| 调度器侧只有一个函数指针 | `include/linux/sched/cpufreq.h:16-18` + `kernel/sched/cpufreq.c:9` |

### §4.4 03 篇（实现剖析）

| 论断 | 出处 |
|---|---|
| 注册前置校验在 pr_debug 之前 | `drivers/cpufreq/cpufreq.c:2872` 早于 `:2874` |
| 五条校验各自的违规项 | `drivers/cpufreq/cpufreq.c:2865` / `:2866-2867` / `:2868-2869` / `:2870` / `:2871` |
| 慢路必须能睡 | `drivers/cpufreq/cpufreq.c:349` 的 `BUG_ON(irqs_disabled())` |
| 通知链两阶段 | `drivers/cpufreq/cpufreq.c:343` 起的函数体 + `:384` 的 tracepoint |
| 限值刷新路径 | `drivers/cpufreq/cpufreq.c:1153` → `:2561` → `:2596-2602` → `:2606` |
| 拆除路径 | `cpufreq_unregister_driver` + 静态键关闭 `:2934`/`:2967` |

**§C2 那十条"设计债"是推断，不是源码自陈** —— 见 §5.3。

### §4.5 04 篇（子框架）

| 论断 | 出处 |
|---|---|
| governor 家族 6 个文件 | §1.2 的文件清单 |
| schedutil 的三个 update 函数与选择条件 | `kernel/sched/cpufreq_schedutil.c:798-803`，函数体 `:352`/`:396`/`:457` |
| 节流阈值来源 | `kernel/sched/cpufreq_schedutil.c:721` → `:781` → `:104` 的比较 |
| EM 注册抄 policy 边界（通用 DT 路线） | `include/linux/cpufreq.h:1264-1268`，传的是 `policy->related_cpus` |
| boost 框架六步链 | `drivers/opp/of.c:945` → `drivers/opp/cpu.c:69-71` → `drivers/cpufreq/freq_table.c:25` → `drivers/cpufreq/cpufreq-dt.c:138`/`:140` → `drivers/cpufreq/cpufreq.c:2808` → `:2891-2895` |
| 三条硬件路线的 policy 边界来源 | `drivers/cpufreq/cpufreq-dt.c:232` · `drivers/cpufreq/scmi-cpufreq.c:169` · `drivers/cpufreq/qcom-cpufreq-hw.c:527-528` |

**自审修正**：§3.5 指出 `cpufreq_dt_attr` 是静态全局这一处**风险判断是本文档集自己的判断**，
源码里没有对应注释 —— 已在 §5.3 登记。

### §4.6 05 篇（使用示例）

| 论断 | 出处 |
|---|---|
| 一行式 `->target_index` | 代表驱动 `cpufreq-dt.c` 的实现 |
| `->fast_switch` 四条约束 | `drivers/cpufreq/cpufreq.c:2142` 起的函数体用法 + `:349` 的 `BUG_ON` 反证 |
| notifier 与 fast switch 互斥 | `drivers/cpufreq/cpufreq.c:506-508`（占位者名单）+ `:2051`/`:2053`（反向的 `-EBUSY`） |
| 不要直写 `policy->min/max` | `drivers/cpufreq/cpufreq.c:1153` 会覆盖 |
| sysfs 文件清单 | `drivers/cpufreq/cpufreq.c` 的属性定义与 `default_attrs` |

篇首已声明**骨架未编译**（另见 §5.2）。所有符号存在性验证见 §3.2。

### §4.7 06 篇（设备树）

| 论断 | 出处 |
|---|---|
| cpufreq 核心不解析任何 DT 属性 | `drivers/cpufreq/cpufreq.c` 全文无 DT 解析；属性消费点全在 `drivers/opp/of.c` 和驱动里 |
| `opp-shared` 决定 policy 边界 | `drivers/opp/of.c:1353`（判定）← `drivers/cpufreq/cpufreq-dt.c:232`（询问） |
| 平台不在白名单 → 静默不起来 | `drivers/cpufreq/cpufreq-dt-platdev.c:179` 一带 |
| `opp-microvolt` 双向错误 | `drivers/opp/of.c:613` 和 `:623` 两条 `dev_err`，条件看 `drivers/cpufreq/cpufreq-dt.c:91` 有没有 `cpu-supply` |
| `dynamic-power-coefficient` 缺失 → EM 不建 | `drivers/opp/of.c:1511` |
| `clock-latency-ns` 缺失 → 延迟为 0 | `drivers/cpufreq/cpufreq.c` 的 `cpuinfo_transition_latency` 读取 |
| 绑定文档里没有通用属性文档 | §1.4 的 ⚠️ |

### §4.8 07 篇（调试）

| 论断 | 出处 |
|---|---|
| 全部普查数字 | §3.1 |
| arm64 的 `scaling_cur_freq` 是记账值 | `drivers/cpufreq/cpufreq.c:721` → `:726` → 全树只有 `arch/x86/kernel/cpu/aperfmperf.c:414` 实现 ⇒ arm64 落 `:716` 的 `__weak` 桩（`:718` 返回 0，声明 `include/linux/cpufreq.h:1243`）⇒ 只剩 `:731-732` 读 `policy->cur` |
| `stats/reset` 延迟生效 | `drivers/cpufreq/cpufreq_stats.c:112`/`:118`（置标志，注释 `:105-107`）→ `:275-276`（真正清零）→ `:62-63`（期间返回假的 0） |
| 写 min/max 是投票 | `drivers/cpufreq/cpufreq.c:739` 宏体只调 `:750`，返回 `:751` |
| 四种 `-EBUSY` | `drivers/cpufreq/cpufreq.c:971`/`:989` · `drivers/cpufreq/cpufreq_governor_attr_set.c:32` · `drivers/cpufreq/cpufreq.c:2053` · `drivers/cpufreq/cpufreq_governor.c:410`（注释 `:408`） |
| `-EINVAL` 五个语义组 | 22 处逐个读上下文（组别与行号见该篇 §7.3） |

**自审修正 3 处，见 §7.3。**

### §4.9 08 篇（费曼）

| 类比 | 背后的技术事实与出处 |
|---|---|
| 一层楼一台空调 = policy | `include/linux/cpufreq.h:60-62`、`:66` |
| 餐厅三角色 | `drivers/cpufreq/cpufreq.c:2303-2306` 的解析与打印 |
| 三份分区图 | `include/linux/cpufreq.h:61` · `include/linux/energy_model.h:55` · `drivers/opp/of.c:1353` |
| 排产系统自己报速度 | `include/linux/sched/cpufreq.h:16-18` · `kernel/sched/cpufreq.c:29`/`:54` · `kernel/sched/cpufreq_schedutil.c:798-803`/`:808` |
| 只听广播的实习生 | `drivers/cpufreq/cpufreq.c:506-508` · `:2051`/`:2053` · `include/linux/cpufreq.h:109-116` |
| 会议室三方投票 | `include/linux/cpufreq.h:86-88` · `drivers/cpufreq/cpufreq.c:739`/`:750`/`:751` |
| 电子油门 | `drivers/cpufreq/cpufreq.c:716-732` · `drivers/cpufreq/qcom-cpufreq-hw.c:267-270`/`:274` |
| 电梯总闸 | 六步链见 §4.5 |
| 财务系统不存规则 | §4.7 的第一行 |

每个类比都写了"什么时候会误导你"，且**至少指出一个真实技术差异**
（不是泛泛而谈"类比总有局限"）。

---

## §5 明确未验证项（四层分层）

⚠️ **这一节是本篇存在的主要理由。** 它标出的不是"我不确定的地方"，
而是**"读者必须自己再确认一次的地方"**。四层的排法是按"离观察有多远"：

| 层 | 含义 | 本子系统的量 |
|---|---|---|
| §5.1 | 命令输出未在真机上采集 | 07 篇**全部** |
| §5.2 | 代码未编译未运行 | 05 篇全部骨架 |
| §5.3 | 结论是推断，不是观察 | 03 §C2、04 §3.5、06 的部分症状 |
| §5.4 | 版本时效性 | 00 §2、行号全部 |

### §5.1 未在真机上采集的输出（最需要注意的一层）

**07 篇里所有 sysfs 内容、`dmesg` 片段、trace 输出、`stats/` 表格，
都是"示意"，不是从真机上抓的。** 07 篇篇首已经就此做了前向引用，
这里给出完整登记：

| 07 篇里的哪类内容 | 真实性质 | 怎么自己核实 |
|---|---|---|
| sysfs 文件**清单**与权限 | **不是示意**，从 `drivers/cpufreq/cpufreq.c` 的属性定义读出来的 | `ls -l /sys/devices/system/cpu/cpufreq/policy0/` |
| sysfs 文件的**内容示例**（频率数值、CPU 掩码值） | **示意**，数字是编的 | 自己 `cat` |
| `dmesg` 日志的**格式串** | **不是示意**，是源码里的字符串原文 | `grep` 源码可对照 |
| `dmesg` 日志里的**具体数值** | **示意** | 自己触发 |
| trace 输出的**字段名与顺序** | 从 `include/trace/events/power.h` 的 `TP_printk` 读出来的 | `cat /sys/kernel/tracing/events/power/cpu_frequency/format` |
| trace 输出的**具体行** | **示意** | 自己开 tracepoint |
| `stats/time_in_state` 的表格 | **示意** | 自己 `cat` |
| 各种 `-E*` 的触发方法 | **读码推导**，未逐条实际触发 | 见 §5.3 |

⇒ 判据很简单：**格式来自源码（可信），数值来自我（不可信）**。

### §5.2 未编译未运行的代码

05 篇的所有骨架（cpufreq driver 骨架、governor 骨架、`->fast_switch` 实现、
notifier 注册示例）**都没有编译过**。05 篇篇首已声明。

已经做到的和没做到的：

| 做到了 | 没做到 |
|---|---|
| 每个符号都全树 grep 确认本版本存在（清单见 §3.2） | 没有编译，**参数个数和顺序可能有错** |
| 每个回调的原型都从头文件抄的，不是凭记忆写的 | 没有跑，**没有运行时验证** |
| 必选/可选回调的判定有源码依据 | 没有做过"故意漏一个回调看会怎样"的实验 |

⇒ 用法：**把 05 篇当"结构参考"，不要当"可编译模板"**。

### §5.3 推断而非观察的结论

这一层最容易被读者当成源码事实，所以逐条点名。

**（1）03 §C2 的十条"设计债"全部是本文档集的判断，不是源码自陈。**

判据是这个：

```bash
grep -rn 'FIXME\|TODO\|XXX\|HACK\|REVISIT' \
    drivers/cpufreq/cpufreq.c drivers/cpufreq/freq_table.c \
    drivers/cpufreq/cpufreq_stats.c drivers/cpufreq/cpufreq_governor.c \
    drivers/cpufreq/cpufreq_governor_attr_set.c include/linux/cpufreq.h
```

⇒ **六个核心文件里一条都没有。**
全子系统唯一一处"作者自己承认这里不干净"的地方，是 schedutil 里那段
关于"给 DL 任务塞一个假带宽值"的注释
（`kernel/sched/cpufreq_schedutil.c:607-610`）—— 它是本子系统里
**唯一的一等自陈证据**。

⇒ 所以 03 §C2 那十条应当读作：**"一个读完核心代码的人认为哪些地方有历史包袱"**，
而不是"内核维护者标注的待办"。

**（2）04 §3.5 关于 `cpufreq_dt_attr` 是静态全局的风险判断，同样是本文档集自己的判断。**
源码里没有任何注释或 `WARN` 提示这一点。事实部分（它确实是文件级静态数组、
确实被所有 policy 共享）是读码结论；"这在多 policy 平台上是个隐患"这个评价是推断。

**（3）06 篇的部分"配错了会怎样"是读码推导，不是实测。**
凡是写"⇒ 会静默失效"的地方，依据是**代码里那条路径上没有任何打印语句**，
而不是"我在板子上试过"。这个推导本身是可靠的（没有 `pr_*` 就不会有日志），
但**"症状具体长什么样"是推断**。

**（4）07 篇的错误码触发方法未逐条实测。**
`-EINVAL` 的 22 处、`-EBUSY` 的 4 处，都是读上下文推出"什么情况会走到这里"，
没有逐条构造出来。其中最可靠的是那几条能用 sysfs 直接触发的
（写非法 governor 名、写非法频率值），最不可靠的是需要特定驱动状态的那几条。

**（5）§2.2 里标 的"OPP 会做 X"全部是契约级描述。**
只读了 cpufreq 会碰到的那些点，OPP 框架内部的实现没读。

### §5.4 版本时效性

| 内容 | 时效性 |
|---|---|
| 所有 `文件:行号` | **只对 6.1.158 成立**。任何补丁都会移动行号 |
| 函数名、结构体字段名 | 比行号稳，但 6.6+ 已有改动（例如 EM 侧的接口） |
| 概念模型（三层、policy、快慢路、投票） | 长期稳定，跨大版本仍适用 |
| 00 §2 的硬件背景 | **和内核版本无关**（讲的是物理和 SoC 结构），但它也**不是从本树读出来的** —— 见下 |
| 07 篇的普查数字 | 必须重跑（命令见 §3.1） |

**00 §2（硬件背景，约 200 行）的证据等级要单独说明。**
它讲的是 DVFS 的物理基础（动态功耗与 V²·f、时序余量、PLL 重锁代价、
PMIC 摆率、簇共享时钟、DynamIQ 的 cluster ≠ 频率域）。
这些内容**不来自本树源码**，来自通用的处理器/电源设计常识。
⇒ 它在本文档集里的作用是**解释内核为什么这样设计**，凡是能落到代码上的部分
（OPP 表的存在、`cpufreq_policy` 的存在、两套回调、`clock-latency-ns`）
都在同一段里给了行号；**纯物理部分（那几个公式）请当作背景，不要当作本树取证结论**。

---

## §6 工具与环境版本

```bash
python3 --version    # Python 3.10.12
/usr/bin/grep -V     # GNU grep 3.7
git --version        # git 2.34.1
```

**本机 `grep` 不是 GNU grep。** 默认 `grep` 是 **ugrep 7.5.0**，
另外还装了 **ripgrep 13.0.0**。三者的差异踩过坑：

| 差异 | 后果 |
|---|---|
| ugrep 不支持 `\s` | 用 `\s` 的模式会静默匹配不到（不是报错）⇒ 一律写 POSIX `[[:space:]]` |
| ugrep 默认遵守 `.gitignore` | 在内核树里影响不大，但计数命令必须复核 |
| ripgrep 默认递归且跳过隐藏文件 | 计数结果可能与 `grep -rn` 不同 |

⇒ **§1 和 §3 里所有计数命令都以 `/usr/bin/grep`（GNU 3.7）的结果为准。**
读者若用别的 grep 实现重跑，个别数字可能差 1~2，请先确认实现。

⚠️ 另一条环境教训：对 `arch/arm64/boot/dts/` 这种超大目录做递归 grep
容易超时。06 篇取 DTS 实例时的做法是**先用 `ls` 缩到具体厂商目录再 grep**。

---

## §7 交叉验证实例与自我修正记录

### §7.1 为什么要有这一节

写文档最危险的不是"不知道"，而是**"记得一个似是而非的答案"** ——
它不会触发查证动作。下面五条都是"我本来很确信，结果源码说不是"的实例。
**它们是本篇最有信息量的部分**：每一条都对应一类会重犯的错误。

### §7.2 三类会重犯的错误

| 类型 | 实例编号 | 怎么防 |
|---|---|---|
| **把别的架构的知识当通用知识** | (b) (e) | 每个"默认值/数量"都要带范围限定词 |
| **把"我读到的那一处"当成"全部"** | (c) (d) | 全称断言必须全树 grep 并数调用点 |
| **把标志名字面意思当语义** | (a) | 标志一律读它的**消费点**，不读定义处 |

### §7.3 五条自我修正（逐条）

**(a) `CPUFREQ_NEED_INITIAL_FREQ_CHECK` 的语义。**
初稿按名字理解成"驱动要求核心去读一次当前频率"。
读消费点才发现它的作用是**允许核心在发现硬件频率不在频率表里时把频率改掉**，
而不只是"检查"。核对的落点是 `drivers/cpufreq/cpufreq.c:1490-1500` 那一段
（注释在 `:1494-1498`），它明确写了这个标志缺失时会走 `BUG_ON`。
⇒ **教训：标志的语义在消费点，不在定义处。**

**(b) ondemand 的默认阈值。**
初稿凭 x86 时代的印象写了"默认 80"。
读 `drivers/cpufreq/cpufreq_ondemand.c` 的 tunable 定义才发现，
本树上 **arm64 相关配置下这个阈值是 95**。
⇒ **教训：所有"默认值"都是配置相关的，不能从记忆里取。**
这一条也是 §7.2 第一类的典型：x86 时代的资料在 arm64 上就是错的
（同类的还有"默认 governor 是 ondemand"，arm64 上是 schedutil，依据见 §4.1）。

**(c) arm64 上 boost 到底由谁开启。**
初稿写成"boost 是驱动自己决定的"。
全树查 `->set_boost` 和 boost 能力的置位点之后发现：**通用 DT 路线和高通路线
都要求 OPP 表里先声明 `turbo-mode`**（链条见 §4.5 和 08 §8），
`drivers/cpufreq/cpufreq-dt.c:140` 和 `drivers/cpufreq/qcom-cpufreq-hw.c:597`
是两条不同来路的同一件事，而 SCMI 路线**根本没有 boost**。
⇒ **教训：三条硬件路线必须分别查，"驱动决定"这种说法太粗。**

**(d) `_once` 变体的计数。**
07 篇初稿写"全子系统只有一个 `_once` 变体"。
写 §3.1 的普查时才发现：**这个"1"只在 arm64 相关范围内成立**，
`drivers/cpufreq/` 全目录是 **9 处**，另外 8 处分布在五个 x86/PowerPC 驱动里
（清单见 §2.7）。
⇒ 已在 07 篇原地收窄为「核心五个文件加上全部 arm64 相关驱动，只有一个」，
并加了一段 ⚠️ 点名另外 8 处在哪。
**教训：arm64 裁剪不只影响"写不写"，还影响"数字是多少"。
凡是普查数字，都必须带范围限定词。**

**(e) Kconfig 开关的数量。**
07 篇初稿写"18 个 Kconfig 开关"。普查时发现 `drivers/cpufreq/Kconfig`
里有 **26** 个 `config` 条目 —— **18 是我那张表的行数，不是文件里的条目数**。
⇒ 已改为「本篇整理 18 个」并补注文件全文 26 个。
**教训：不要把"我整理了多少"写成"一共有多少"。这是最隐蔽的一类过度断言，
因为两个数字都是真的。**

### §7.4 文档与代码冲突的实例（§1.4 前向引用的落点）

| 冲突 | 文档怎么说 | 代码怎么做 | 本文档集取谁 |
|---|---|---|---|
| `stats/reset` 的生效时机 | `Documentation/cpu-freq/cpufreq-stats.rst` 只说"清零" | 实际是**置一个标志、延迟清零**，期间读到的是假的 0（`drivers/cpufreq/cpufreq_stats.c:112`/`:118`/`:275-276`/`:62-63`） | **代码**，并在 07 篇显式指出文档没写这一点 |
| cpufreq 的 DT 属性文档在哪 | `bindings/cpufreq/` 目录名暗示在这里 | 通用属性全在 `bindings/opp/` 下（§1.4） | **代码/实际目录结构** |
| 老资料说的默认 governor | 大量 x86 时代资料说 ondemand | `drivers/cpufreq/Kconfig:41` 的 `default ... if ARM64 \|\| ARM` | **Kconfig** |

这三条都不是"内核文档写错了"，而是**"文档写的是另一个语境"** ——
这比明显的错误更难发现。

---

## §8 复现命令清单

### §8.1 一次性重跑本篇所有数字

按顺序跑完下面这一组，就能验证 §1 和 §3 的每一个数字。
**在源码树根目录执行。**

```bash
# ---- §1.1 基线 ----
git log -1 --format='%H %s'
git status --porcelain -- drivers/cpufreq include/linux/cpufreq.h \
    include/linux/sched/cpufreq.h kernel/sched/cpufreq.c \
    kernel/sched/cpufreq_schedutil.c Documentation/cpu-freq \
    Documentation/admin-guide/pm Documentation/devicetree/bindings/cpufreq

# ---- §1.2 规模 ----
wc -l drivers/cpufreq/cpufreq.c include/linux/cpufreq.h \
      kernel/sched/cpufreq_schedutil.c kernel/sched/cpufreq.c \
      include/linux/sched/cpufreq.h drivers/cpufreq/freq_table.c \
      drivers/cpufreq/cpufreq_stats.c drivers/cpufreq/cpufreq_governor.c \
      drivers/cpufreq/cpufreq_governor_attr_set.c \
      drivers/cpufreq/cpufreq_ondemand.c drivers/cpufreq/cpufreq_conservative.c \
      drivers/cpufreq/cpufreq_performance.c drivers/cpufreq/cpufreq_powersave.c \
      drivers/cpufreq/cpufreq_userspace.c \
      drivers/cpufreq/Kconfig drivers/cpufreq/Kconfig.arm
ls drivers/cpufreq/*.c | wc -l
cat drivers/cpufreq/*.c | wc -l

# ---- §1.3 类型判定 ----
/usr/bin/grep -rn 'struct bus_type' drivers/cpufreq/ include/linux/cpufreq.h | wc -l

# ---- §1.4 文档规模 ----
wc -l Documentation/cpu-freq/*.rst Documentation/admin-guide/pm/cpufreq*.rst
ls Documentation/devicetree/bindings/cpufreq/ | wc -l

# ---- §3.1 能力普查 ----
/usr/bin/grep -c 'return -E' drivers/cpufreq/cpufreq.c
/usr/bin/grep -o 'return -E[A-Z_]*' drivers/cpufreq/cpufreq.c | sort | uniq -c | sort -rn
/usr/bin/grep -rn '_once' drivers/cpufreq/*.c | wc -l
/usr/bin/grep -rn '_ratelimited' drivers/cpufreq/*.c | wc -l
/usr/bin/grep -rln 'trace_' drivers/cpufreq/*.c
/usr/bin/grep -c 'TP_CONDITION' include/trace/events/power.h
/usr/bin/grep -rln 'debugfs' drivers/cpufreq/
/usr/bin/grep -rn 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR' drivers/cpufreq/ | wc -l
/usr/bin/grep -c 'WARN_ON\|WARN(\|BUG_ON' drivers/cpufreq/cpufreq.c
/usr/bin/grep -n 'module_param' drivers/cpufreq/cpufreq.c
/usr/bin/grep -c '^config ' drivers/cpufreq/Kconfig
/usr/bin/grep -c 'EXPORT_SYMBOL' drivers/cpufreq/cpufreq.c drivers/cpufreq/freq_table.c

# ---- §5.3 设计债判据 ----
/usr/bin/grep -rn 'FIXME\|TODO\|XXX\|HACK\|REVISIT' \
    drivers/cpufreq/cpufreq.c drivers/cpufreq/freq_table.c \
    drivers/cpufreq/cpufreq_stats.c drivers/cpufreq/cpufreq_governor.c \
    drivers/cpufreq/cpufreq_governor_attr_set.c include/linux/cpufreq.h
```

⚠️ **`/usr/bin/grep` 的绝对路径不是多余的** —— 理由见 §6。

### §8.2 三个自检脚本的执行记录

三个脚本都在**源码树根目录**执行，`D=output/cpufreq`：

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/cpufreq
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh cpu-freq $D .
bash    $S/check_doc_coverage.sh cpufreq  $D .
```

**结果登记**：

| 脚本 | 结果 |
|---|---|
| `check_xrefs.py` | 跨篇 `0X §Y` 引用**全部命中**，0 条失效 |
| `check_line_refs.py` | **420 条唯一 `文件:行号` 引用**（消歧表 40 个基名 / 23 个前缀目录），0 条解析失败、0 条越界 |
| `check_doc_coverage.sh cpu-freq` | 必查 3 篇（`core` 22 次 · `cpu-drivers` 1 次 · `cpufreq-stats` 2 次），**0 篇 0 引用** |
| `check_doc_coverage.sh cpufreq` | 必查 0 篇（该路径下没有概念文档）；DT 绑定检出 3 篇 yaml，**3 篇全部被引用**（`cpufreq-mediatek-hw` 1 次 · `cpufreq-qcom-hw` 2 次 · `qcom-cpufreq-nvmem` 7 次） |

**为什么 `check_doc_coverage.sh` 要跑两次**：脚本的探测规则是按
`Documentation/<路径>/` 找目录，而 cpufreq 的官方文档分在**两个不同的路径**下：
`Documentation/cpu-freq/`（带连字符）和 `Documentation/devicetree/bindings/cpufreq/`（不带）。
只跑一次必然漏一半。
⇒ 另外 `Documentation/admin-guide/pm/cpufreq.rst` 是**单文件不是目录**，
两次都探测不到，所以在 §1.4 手工登记。

⚠️ **`check_doc_coverage.sh` 的"引用次数"不能当准确数字用。**
它用基名做子串匹配，所以 `core` 这个基名会把正文里所有出现"core"的地方都算进去
（22 次里绝大部分其实是英文单词 core）。
⇒ **只有"0 引用"这个信号是可靠的**：0 意味着这篇官方文档确实一次都没被参考到。
本次两轮都是 0 篇 0 引用。

⚠️ 上表 DT 绑定的"共 3 篇"是脚本只统计 `.yaml` 的结果。
`bindings/cpufreq/` 实际有 **12 个文件**，其余 9 个是老式 `.txt`（§1.4）。
⇒ 这不是脚本的错，是 cpufreq 绑定目录里大部分还没转 yaml。

### §8.3 两个 ignore 文件

```bash
ls -l output/cpufreq/.xref-ignore output/cpufreq/.lineref-ignore
```

⇒ **两个文件都不存在 —— 本文档集零豁免。**
所有脚本告警都是靠改文档解决的，没有一条是靠登记豁免绕过的。

其中最值得记下来的一次是 `check_line_refs.py` 的 **AMBIGUOUS** 告警：
写 00 篇时第一次引用了调度器侧那个同名的 `cpufreq.c`（在 `kernel/sched/` 下），
于是全套文档里所有**只写基名**的引用瞬间变成有歧义的 —— 因为文档集里
出现了两个同基名的完整路径。
⇒ 处理方式是**把 00 / 04 / 05 / 06 / 07 里 35 处只写基名的引用全部补成完整路径**
（改之前先确认没有一处落在 ASCII 框线里，免得改破对齐），不是登记豁免。
**教训：新增一条合法引用，可能让一批旧引用回溯性地变得有歧义。**

---

## §9 与 qoder 文档集的对比（rule-29）

**cpufreq 没有 qoder 对照文档集。**

```bash
ls output/qoder/
```

⇒ `output/qoder/` 下只有 `spi` 一套。本子系统**无对照**，
因此 rule-29（"对比 qoder 版本、吸收更好的做法、回写 skill"）在本套上**不适用**。

不过 spi 那次对比得到的做法已经内化进 skill，本套照做了，登记如下：

| 从 spi 对比里吸收的做法 | 本套的落点 |
|---|---|
| 00 篇加"硬件背景"章节（200+ 行，从物理事实推导内核设计） | 00 §2（另见 §5.4 的证据等级说明） |
| DTS 独立成篇，每条属性都要找到消费者 | 06 篇；找不到消费者的属性显式标出 |
| 06 篇篇首"按任务找章节"表 + §1.1 属性总表带"写在哪个节点"列 | 06 §1.1 |
| 07 篇日志可见性细化到 `_once` / `_ratelimited` 变体 | 07 §2.2（也正是 §7.3(d) 那条修正的来源） |
| 07 篇同码多源错误码按**语义**分组，不只罗列行号 | 07 §7.1 的 `-EINVAL` 五组 |
| 08 篇四段式 + "类比什么时候会误导你" | 08 篇九个选题全部照做 |
| 99 篇逐篇取证表设为强制项 | §4 |
| 未验证项四层分层 | §5 |
| 产出顺序 `01→…→08→00→99`（00 最后写变成纯浓缩） | 本套全程照做 |

### §9.1 本套自己新增、值得回写 skill 的两条

1. **普查数字必须带范围限定词。**
   §7.3 的 (d)(e) 两条是同一个病：一个数字在"我看的范围"里是对的，
   在"文件全文 / 全目录"里是错的。
   ⇒ 建议规则：**凡写数量，句子里必须出现范围**（"核心五个文件里"、"arm64 相关驱动里"、
   "本篇整理的"）。缺范围的数量断言按错处理。

2. **新增引用会让旧引用回溯性失效。**
   §8.3 记的 AMBIGUOUS 那一类。
   ⇒ 建议规则：**引用一个基名与已有文件重名的新文件时，先全套补全路径再往下写**，
   不要等脚本报警。判断动作是一条命令：写新引用前先看文档集里这个基名有没有别的路径。

---

## §10 一句话总结这一篇

**前九篇里能重跑的都在 §1/§3/§8，不能重跑的都在 §5。**
如果只看一节，看 §5.1 —— 它划出了"格式来自源码，数值来自我"这条线，
而这条线正是 07 篇能不能直接照着排查的分界。
