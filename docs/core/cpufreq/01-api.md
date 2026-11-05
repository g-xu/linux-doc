---
title: API 参考
---

# cpufreq 01 —— 代码地图与对外 API

本篇合并两件事：**「这个功能的代码在哪个文件」**和**「这个功能的入口函数叫什么」**。
两者是同一次查询，所以放同一篇。

表格统一三列 **API / 说明 / 备注**。签名只写函数名和形参名，不带类型；
返回值语义、上下文限制、`inline`/宏、arm64 是否涉及，全部进「备注」。
**位置只精确到文件**，行号集中在 `03-internals.md` 和 `99-evidence.md` 里维护。

| 想找什么 | 去哪一节 |
|---|---|
| 文件、头文件、Kconfig 的分工 | §0 |
| 名字里的 `__`、`_raw`、`quick_`、`driver_` 是什么意思 | §1 |
| 别的子系统怎么读当前频率 | §2 |
| 写一个 cpufreq 驱动要实现/调用什么 | §3 |
| 频率表怎么建、怎么查 | §4 |
| 写一个 governor 要实现/调用什么 | §5 |
| 频率变更的通知链和两段式协议 | §6 |
| boost、挂起恢复、限制刷新 | §7 |
| `CONFIG_CPU_FREQ=n` 时这些函数变成什么 | §8 |
| 错误码分别代表什么 | §9 |

---

## §0 代码地图

### 0.1 核心：这几个文件要全读

| 文件 | 职责 |
|---|---|
| `drivers/cpufreq/cpufreq.c` | 框架主体。policy 的创建/销毁、驱动注册、governor 切换、sysfs、通知链、`target` 与 `fast_switch` 两条下发路径、CPU 热插拔、挂起恢复 |
| `include/linux/cpufreq.h` | 全部数据结构 + 对外声明 + 大量 `static inline` 频率表查找函数。**比核心 `.c` 只少一半，本身就是主要阅读对象** |
| `drivers/cpufreq/freq_table.c` | 频率表的校验、排序、遍历、`scaling_available_frequencies` 属性 |
| `drivers/cpufreq/cpufreq_stats.c` | `stats/` 那一组 sysfs（`time_in_state`、`total_trans`、`trans_table`） |

### 0.2 governor：三条互不相同的路线

| 文件 | 职责 |
|---|---|
| `drivers/cpufreq/cpufreq_performance.c` · `cpufreq_powersave.c` | 最简 governor，各 45 / 39 行。**读它们就能看懂 governor 接口的最小形态** |
| `drivers/cpufreq/cpufreq_userspace.c` | 把决策交给用户态（`scaling_setspeed`） |
| `drivers/cpufreq/cpufreq_governor.c` · `cpufreq_governor.h` | **旧式采样型 governor 的公共骨架**（`dbs_` = demand-based switching），被 ondemand / conservative 共用 |
| `drivers/cpufreq/cpufreq_ondemand.c` · `cpufreq_conservative.c` | 两个采样型 governor 的策略部分 |
| `drivers/cpufreq/cpufreq_governor_attr_set.c` | governor 参数的 sysfs 引用计数容器（`gov_attr_set`） |
| `kernel/sched/cpufreq_schedutil.c` | **arm64 上的默认 governor**。它不住在 `drivers/cpufreq/`，因为它要读调度器内部的利用率信号 |
| `kernel/sched/cpufreq.c` · `include/linux/sched/cpufreq.h` | 调度器 ↔ cpufreq 的挂钩点（`update_util` 回调），74 + 38 行 |

**这是本子系统最反直觉的一处目录布局**：默认 governor 的代码不在子系统目录里。
原因见 `04-extensions.md` §3，大白话解释见 `08-feynman.md` §4。

### 0.3 驱动：89 个 `.c`，只读代表

`drivers/cpufreq/` 下共 **89 个 `.c` 文件、39396 行**，绝大多数是平台驱动。
arm64 上真正会碰到的几类：

| 文件 | 类型 |
|---|---|
| `drivers/cpufreq/cpufreq-dt.c` + `cpufreq-dt-platdev.c` | 通用 DT 驱动 + 白名单探测器。**大多数 arm64 SoC 走这条路** |
| `drivers/cpufreq/scmi-cpufreq.c` · `scpi-cpufreq.c` | 固件代理（经 mailbox，**回调会睡眠**） |
| `drivers/cpufreq/qcom-cpufreq-hw.c` · `mediatek-cpufreq-hw.c` | 硬件自治：频率由硬件状态机决定，驱动只写"期望档位" |
| `drivers/cpufreq/qcom-cpufreq-nvmem.c` · `sun50i-cpufreq-nvmem.c` · `ti-cpufreq.c` · `imx-cpufreq-dt.c` | 靠 efuse/nvmem 在多份 OPP 表里**挑一份** |
| `drivers/cpufreq/tegra186-cpufreq.c` · `tegra194-cpufreq.c` | 自带频率表、不走 OPP |
| `drivers/cpufreq/cppc_cpufreq.c` | ACPI CPPC ⇒ **本文档集裁掉**（见 `99-evidence.md` §2.7） |

⚠️ **`intel_pstate.c`、`amd-pstate.c`、`acpi-cpufreq.c` 全部裁掉**（x86 / ACPI）。
它们不只是"另一个驱动"——`intel_pstate` 会**整体替换 governor 层**，
是一种和本文档描述的模型不同的架构。裁剪理由记在 `99-evidence.md` §2.7。

### 0.4 Kconfig：四个文件

| 文件 | 内容 |
|---|---|
| `drivers/cpufreq/Kconfig` | 框架本体 + 六个通用 governor + **默认 governor 的 `choice`** |
| `drivers/cpufreq/Kconfig.arm` | arm/arm64 平台驱动（359 行，比框架本身还长） |
| `drivers/cpufreq/Kconfig.x86` · `Kconfig.powerpc` | 裁掉 |

一个必须记住的默认值：`drivers/cpufreq/Kconfig` 里那个 `choice` 有一行
`default CPU_FREQ_DEFAULT_GOV_SCHEDUTIL if ARM64 || ARM`
—— **arm64 的默认 governor 是 schedutil**，不是 ondemand。
这一条决定了 03/04/07 三篇的重心放在哪。

---

## §1 命名法：看名字就能判断该不该调

这个子系统的函数名前缀信息量很大，先建立映射，后面几节就不用重复解释。

| 形态 | 含义 | 例 |
|---|---|---|
| `cpufreq_cpu_*` | 以 **CPU 号**为入口拿 policy | `cpufreq_cpu_get` |
| `cpufreq_driver_*` | **调用驱动回调**的核心函数（不是"给驱动用的函数"） | `cpufreq_driver_target` |
| `cpufreq_generic_*` | **给驱动直接填进 `cpufreq_driver` 的通用实现** | `cpufreq_generic_get` |
| `cpufreq_frequency_table_*` / `cpufreq_table_*` | 频率表操作 | `cpufreq_table_validate_and_sort` |
| `cpufreq_dbs_governor_*` | 采样型 governor 骨架，**只有 ondemand/conservative 用** | `cpufreq_dbs_governor_start` |
| `quick_get` | **不加锁读缓存值**，可能过期 | `cpufreq_quick_get` |
| `_raw` | **不加引用计数**，调用者必须自己保证 policy 活着 | `cpufreq_cpu_get_raw` |
| 双下划线 `__` 前缀 | **调用者必须已持有 `policy->rwsem`** | `__cpufreq_driver_target` |
| `*_acquire` / `*_release` | 成对使用，`acquire` 拿引用**并**上写锁 | `cpufreq_cpu_acquire` |

**最容易搞错的一组是 `cpufreq_driver_*`**：
`cpufreq_driver_target()` 不是"驱动要调的函数"，而是"**核心用来调驱动的函数**"，
调用方是 governor。同理 `cpufreq_driver_fast_switch()`、`cpufreq_driver_resolve_freq()`。
判断方法：这些函数的第一个参数都是 `policy`，而驱动自己的回调拿到的也是 `policy`
—— 谁在调谁，看 `03-internals.md` §A4 的调用链。

---

## §2 只想读一下当前频率：别的子系统用的接口

头文件：`include/linux/cpufreq.h`；实现：`drivers/cpufreq/cpufreq.c`。

### 2.1 读频率的四个函数（差别很大，选错会拿到过期值）

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_get(cpu)` | 返回该 CPU 当前频率（kHz），**会去问驱动** | 加 `policy->rwsem` 读锁并调 `->get()`，**可能睡眠**；没有 policy 返回 0 |
| `cpufreq_quick_get(cpu)` | 返回缓存的 `policy->cur`，**不问驱动** | 不加锁（只拿引用），值可能过期；驱动提供 `->setpolicy` 时改走 `->get()`。无 policy 返回 0 |
| `cpufreq_quick_get_max(cpu)` | 返回 `policy->max`（**当前生效的上限**） | 受用户态和热限制影响，会变 |
| `cpufreq_get_hw_max_freq(cpu)` | 返回 `policy->cpuinfo.max_freq`（**硬件上限**） | 与上一个的区别是"能不能被限制"，是 `08-feynman.md` §5 的主题 |

一句话选择规则：**能睡就用 `cpufreq_get()`，不能睡就用 `cpufreq_quick_get()`
并接受它可能过期**；要判断"这颗 CPU 最快能跑多少"用 `cpufreq_get_hw_max_freq()`，
要判断"现在允许跑多少"用 `cpufreq_quick_get_max()`。

### 2.2 拿 policy 对象

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_cpu_get(cpu)` | 拿 policy 并**加一次引用**（`kobject_get`） | 必须配 `cpufreq_cpu_put()`；policy 不活跃时返回 `NULL` |
| `cpufreq_cpu_put(policy)` | 放引用 | 与上配平 |
| `cpufreq_cpu_get_raw(cpu)` | 拿 policy **不加引用** | 只能在已知 policy 活着的上下文用（如驱动回调内部）。给驱动和 governor 用 |
| `cpufreq_cpu_acquire(cpu)` | 拿引用 **+ 上 `policy->rwsem` 写锁** | 配 `cpufreq_cpu_release()`；核心内部的"我要改这个 policy"入口 |
| `cpufreq_cpu_release(policy)` | 解写锁 + 放引用 | 与上配平 |
| `cpufreq_get_policy(policy, cpu)` | 把 policy 内容**拷贝**到调用者提供的结构里 | 拷贝语义，拿到的是快照；找不到返回 `-EINVAL` |
| `policy_is_inactive(policy)` | policy 的 `cpus` mask 是否为空 | `static inline` |
| `policy_is_shared(policy)` | 这个 policy 是否覆盖多个 CPU | `static inline`；判据是 `cpus` 里多于一个。**理解 policy 与 CPU 不是一对一的入口**，见 `08-feynman.md` §1 |

### 2.3 其它零散查询

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_get_current_driver()` | 返回当前注册的驱动名字符串 | 全系统只能有一个 cpufreq 驱动 |
| `cpufreq_get_driver_data()` | 返回驱动注册时填的 `driver_data` | 返回 `void *`，由驱动自己解释 |
| `cpufreq_supports_freq_invariance()` | 系统是否支持频率不变性（给调度器算力用） | arm64 上和 `arch_set_freq_scale()` 配套，见 `04-extensions.md` §5 |
| `have_governor_per_policy()` | 当前驱动是否要求 governor 参数按 policy 分开 | 由驱动 flag `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` 决定 |
| `get_cpu_idle_time(cpu, wall, io_busy)` | 读该 CPU 的累计空闲时间 | 采样型 governor 的输入源；`io_busy` 决定是否把 iowait 算成忙 |
| `cpufreq_show_cpus(mask, buf)` | 把一个 cpumask 格式化成 sysfs 用的字符串 | 给 `related_cpus` / `affected_cpus` 属性用 |
| `disable_cpufreq()` | 全局关掉 cpufreq，此后驱动注册直接失败 | 只给极少数早期启动路径用 |
| `cpufreq_scale(old, div, mult)` | `old * mult / div`，32 位上用 `do_div` | `static inline`，纯算术辅助 |

---

## §3 写一个 cpufreq 驱动

头文件：`include/linux/cpufreq.h`；实现：`drivers/cpufreq/cpufreq.c`。
可抄的骨架在 `05-usage.md` §2–§4。

### 3.1 注册与注销

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_register_driver(driver_data)` | 注册**全系统唯一**的 cpufreq 驱动 | 已有驱动时返回 `-EEXIST`；`disable_cpufreq()` 调过则返回 `-ENODEV`；`verify` 或 `init` 缺失返回 `-EINVAL`。成功后核心会**立即为每颗在线 CPU 建 policy** |
| `cpufreq_unregister_driver(driver_data)` | 注销 | 传入的指针必须和注册时一致，否则 `-EINVAL` |
| `cpufreq_driver_test_flags(flags)` | 查询当前驱动是否带某个 flag | `bool`，核心和 governor 都用 |

**"全系统唯一"是这个子系统的一条硬约束**：不像 reset 或 clk 可以注册任意多个
控制器，cpufreq 全局只有一个 `cpufreq_driver`。多簇（big.LITTLE）不是靠多个驱动，
而是靠**一个驱动创建多个 policy**。这一条是 `08-feynman.md` §1 的核心。

### 3.2 `struct cpufreq_driver` 的回调（要实现哪些）

| 回调 | 说明 | 备注 |
|---|---|---|
| `init(policy)` | **必须实现**。为一个 policy 填 `cpus`、`freq_table` 或 `min/max`、`cpuinfo`、`transition_delay_us` 等 | 这是驱动 90% 的代码所在 |
| `verify(policy)` | **必须实现**。校验并夹紧用户请求的 `min/max` | 有频率表的驱动直接填 `cpufreq_generic_frequency_table_verify` |
| `target_index(policy, index)` | 下发**频率表下标** | arm64 上最常用的一个。和下面三个是"四选一" |
| `target(policy, target_freq, relation)` | 下发**目标频率 + 靠拢方向** | 源码注释标了 `Deprecated`，新驱动不要用 |
| `setpolicy(policy)` | 驱动自己带 governor（把 `policy->policy` 交给硬件） | arm64 上没有驱动走这条，`intel_pstate` 走这条 ⇒ 已裁 |
| `fast_switch(policy, target_freq)` | **不可睡眠**的快速下发，供 schedutil 在调度上下文调用 | 返回**实际设定的频率**（不是错误码）；不支持就别实现 |
| `adjust_perf(cpu, min_perf, target_perf, capacity)` | `fast_switch` 的替代：传"性能等级 + 提示"而不是频率 | 给内部用抽象性能值的驱动；arm64 上 CPPC 类驱动会用 |
| `get(cpu)` | 读回真实当前频率 | 注释写 "should be defined, if possible"；不实现则 `cpufreq_get()` 退回缓存值 |
| `get_intermediate(policy, index)` + `target_intermediate(policy, index)` | 需要**先跳到一个安全中间频率**再切目标的平台用 | 成对实现；`get_intermediate` 返回 0 表示这次不需要中间频率。核心负责发通知 |
| `update_limits(cpu)` | 固件通知限制变了时被调 | 固件代理类驱动（SCMI）会实现 |
| `online(policy)` / `offline(policy)` | CPU 热插拔的轻量路径 | 与 `init`/`exit` 的区别：不重建 policy，只是让它进出活跃状态 |
| `exit(policy)` | 释放 `init` 里申请的东西 | |
| `suspend(policy)` / `resume(policy)` | 系统挂起恢复 | 通用实现是 `cpufreq_generic_suspend` |
| `ready(policy)` | policy **完全建好之后**回调一次 | 用于"必须等 sysfs 都建好才能做的事" |
| `set_boost(policy, state)` | 打开/关闭超频档 | 配 `boost_enabled` 字段，见 §7.1 |
| `register_em(policy)` | 注册 Energy Model | 用 `cpufreq_register_em_with_opp()` 一行即可，见 §3.4 |
| `bios_limit(cpu, limit)` | 读 BIOS/固件施加的上限 | x86 遗留，arm64 不涉及 |

**`init` 里最容易漏的三件事**：填 `policy->cpus`（决定这个 policy 覆盖哪些 CPU）、
填 `policy->freq_table`（否则 `verify` 的通用实现会失败）、
设 `policy->transition_delay_us`（不设则核心按 `transition_latency` 推算，见 §3.3）。

### 3.3 驱动可以直接拿去用的通用实现

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_generic_init(policy, table, transition_latency)` | 一次把 `freq_table`、`cpuinfo.transition_latency`、`policy->cpus` 填好 | 它把 `policy->cpus` 设成 `policy->related_cpus` ⇒ **只适合"所有相关 CPU 同频"的平台** |
| `cpufreq_generic_get(cpu)` | 通用 `->get()`：从 `policy->clk` 读 clk 频率 | 要求驱动在 `init` 里填了 `policy->clk` |
| `cpufreq_generic_suspend(policy)` | 通用 `->suspend()`：把频率切到 `policy->suspend_freq` | `suspend_freq` 为 0 时直接返回 0（什么都不做） |
| `cpufreq_generic_frequency_table_verify(policy)` | 通用 `->verify()`：按频率表夹紧 | 见 §4.2 |
| `cpufreq_generic_attr[]` | 通用的 sysfs 属性数组（含 `scaling_available_frequencies`） | 直接填进 `driver.attr` |
| `cpufreq_policy_transition_delay_us(policy)` | 算出两次频率切换之间的最小间隔（微秒） | 优先用驱动填的 `transition_delay_us`；否则由 `transition_latency` 推算，并**上限 10 毫秒**。schedutil 的采样周期就取自这里 |
| `cpufreq_register_em_with_opp(policy)` | 用 OPP 表注册 Energy Model | `static inline`，一行封装 `dev_pm_opp_of_register_em()` |
| `cpufreq_enable_fast_switch(policy)` / `cpufreq_disable_fast_switch(policy)` | 打开/关掉这个 policy 的 fast switch 通路 | 由 **governor** 调（schedutil 的 `init`），不是驱动调 |

### 3.4 限制夹紧（`verify` 里必用）

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_verify_within_limits(policy, min, max)` | 把 `policy->min/max` 夹进 `[min, max]` | `static inline`，**直接改传入的结构**，无返回值 |
| `cpufreq_verify_within_cpu_limits(policy)` | 用 `policy->cpuinfo.min_freq/max_freq` 夹 | 绝大多数驱动的 `verify` 就是这一行 + 频率表校验 |

⚠️ 注意这两个函数的参数类型是 **`struct cpufreq_policy_data *`**，不是
`struct cpufreq_policy *`。`policy_data` 是"待校验的请求"，只有 `cpuinfo`/`freq_table`/
`min`/`max`/`cpu`/`governor` 六个字段 —— 这个类型区分是防止 `verify` 回调乱改 policy。
两者的关系见 `02-datastructures.md` §1.2。

---

## §4 频率表

头文件：`include/linux/cpufreq.h`（**大量 `static inline` 查找函数在这里**）；
实现：`drivers/cpufreq/freq_table.c`。

### 4.1 建表与校验

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_table_validate_and_sort(policy)` | 核心在 policy 建立时调用：校验表、排序、算出 `cpuinfo.min/max` | 驱动**不用自己调**；表非法返回 `-EINVAL` |
| `cpufreq_frequency_table_cpuinfo(policy, table)` | 扫一遍表填 `cpuinfo.min_freq/max_freq` | 表里没有有效项返回 `-EINVAL` |
| `cpufreq_frequency_table_verify(policy, table)` | 按表夹紧请求的 `min/max` | |
| `cpufreq_generic_frequency_table_verify(policy)` | 上一个的封装，用 `policy->freq_table` | 直接填进 `driver.verify` |
| `dev_pm_opp_init_cpufreq_table(dev, table)` / `dev_pm_opp_free_cpufreq_table(dev, table)` | 从 OPP 表生成/释放 cpufreq 频率表 | 实现在 OPP 子系统（`drivers/opp/`），不在这里；`CONFIG_PM_OPP=n` 时是返回 `-EINVAL` 的桩 |

### 4.2 查表（一整族 `static inline`）

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_frequency_table_target(policy, target_freq, min, max, relation)` | **主入口**：按 `relation` 找下标 | `static inline`；内部按表的排序状态分派到下面那些函数 |
| `cpufreq_table_index_unsorted(policy, target_freq, min, max, relation)` | 表**未排序**时的线性查找 | 唯一一个真在 `.c` 里的查表函数；表乱序时才走它 |
| `cpufreq_table_find_index_l/h/c(policy, target_freq, efficiencies)` | 找 ≥ / ≤ / 最接近目标的档 | 各有 `_al/_dl`、`_ah/_dh`、`_ac/_dc` 两个变体，分别对应升序表和降序表 |
| `cpufreq_frequency_table_get_index(policy, freq)` | 精确匹配某个频率的下标 | 找不到返回 `-EINVAL` |
| `cpufreq_table_count_valid_entries(policy)` | 数有效项个数 | `static inline` |
| `cpufreq_is_in_limits(policy, idx)` | 该下标是否落在当前 `min/max` 内 | `static inline` |
| `cpufreq_table_set_inefficient(policy, frequency)` | 把某档标记为"低效"（性能不如更高档但更费电） | 表未排序时返回 `-EINVAL`；配 `CPUFREQ_RELATION_E` 使用 |
| `policy_has_boost_freq(policy)` | 表里有没有 `CPUFREQ_BOOST_FREQ` 项 | |

**`relation` 的取值**（都在 `include/linux/cpufreq.h`）：
`CPUFREQ_RELATION_L`（不低于目标的最低档）、`_H`（不高于目标的最高档）、
`_C`（最接近的一档），再**按位或** `CPUFREQ_RELATION_E`（可能的话跳过低效档），
组合出 `_LE` / `_HE` / `_CE`。`_E` 是 `BIT(2)`，所以它是标志位而不是第四种取值
—— 这是这组常量最容易读错的地方。

### 4.3 遍历宏

| 宏 | 说明 | 备注 |
|---|---|---|
| `cpufreq_for_each_entry(pos, table)` | 遍历全部项（含无效项） | 终止条件是 `frequency == CPUFREQ_TABLE_END` |
| `cpufreq_for_each_entry_idx(pos, table, idx)` | 同上，额外给出下标 | |
| `cpufreq_for_each_valid_entry(pos, table)` | **跳过 `CPUFREQ_ENTRY_INVALID`** | 绝大多数场合该用这个 |
| `cpufreq_for_each_valid_entry_idx(pos, table, idx)` | 同上，额外给出下标 | |
| `cpufreq_for_each_efficient_entry_idx(pos, table, idx, efficiencies)` | 再额外跳过被标记低效的项 | `efficiencies` 为假时退化成上一个 |

⚠️ `CPUFREQ_ENTRY_INVALID` 是 `~0u`，`CPUFREQ_TABLE_END` 是 `~1u`
—— **两个都是"很大的数"，写错一个循环就跑飞**。

### 4.4 属性

| 符号 | 说明 | 备注 |
|---|---|---|
| `cpufreq_freq_attr_scaling_available_freqs` | `scaling_available_frequencies` 属性 | 是**变量**不是函数 |
| `cpufreq_freq_attr_scaling_boost_freqs` | `scaling_boost_frequencies` 属性 | 同上 |
| `cpufreq_generic_attr[]` | 上面两个之一打包成数组 | 填进 `driver.attr` |
| `cpufreq_freq_attr_ro(_name)` / `_ro_perm` / `_rw` / `_wo` | 定义一个 `struct freq_attr` | 宏，在 `include/linux/cpufreq.h` |
| `define_one_global_ro(_name)` / `define_one_global_rw(_name)` | 定义全局（非 per-policy）sysfs 属性 | 宏 |

---

## §5 写一个 governor

头文件：`include/linux/cpufreq.h`（`struct cpufreq_governor`、`gov_attr_set`）
+ `drivers/cpufreq/cpufreq_governor.h`（**仅采样型骨架**）；
实现：`drivers/cpufreq/cpufreq.c` + `cpufreq_governor.c` + `cpufreq_governor_attr_set.c`。

### 5.1 注册

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_register_governor(governor)` | 把 governor 挂进全局链表 | 同名已存在返回 `-EBUSY`；`governor` 为空返回 `-EINVAL`。**不会**立刻被使用 |
| `cpufreq_unregister_governor(governor)` | 摘下来 | 会先把用它的 policy 切回默认 |
| `cpufreq_governor_init(__governor)` | 宏：生成 `module_init`/`core_initcall` 注册代码 | 内建时用 `core_initcall`，模块时用 `module_init` |
| `cpufreq_governor_exit(__governor)` | 宏：生成 `module_exit` | **只在可做模块的 governor 里出现** |
| `cpufreq_default_governor()` | 返回本次编译选定的默认 governor | **由各 governor 自己按 Kconfig 条件实现**，全树有六个定义，最终只有一个被编进去 |
| `cpufreq_fallback_governor()` | 默认 governor 启动失败时的退路 | 弱符号（`__weak`）在核心里，**只有 performance governor 覆盖它** |

`cpufreq_default_governor()` 的实现方式是这个子系统一处很特别的设计：
**六个 governor 文件里各有一份定义，靠 Kconfig 保证只编进一个**。
所以"改默认 governor"是编译期的事，不是运行期的事（运行期只能靠 sysfs 或
内核参数改）。细节见 `03-internals.md` §A2、`07-debug.md` §2。

### 5.2 `struct cpufreq_governor` 的回调

| 回调 | 说明 | 备注 |
|---|---|---|
| `init(policy)` | 分配 governor 的私有数据、建参数 sysfs 目录 | 失败返回负值则整个 governor 切换失败 |
| `exit(policy)` | 释放 | |
| `start(policy)` | **开始工作**：装定时器（采样型）或挂 `update_util` 钩子（schedutil） |两条路线的区别全在这里 |
| `stop(policy)` | 停止工作 | 必须保证返回后不再有异步下发 |
| `limits(policy)` | 限制（`min`/`max`）变了，重新夹一次当前频率 | 通用实现是 `cpufreq_policy_apply_limits()` |
| `show_setspeed(policy, buf)` / `store_setspeed(policy, freq)` | 支撑 `scaling_setspeed` 属性 | **只有 userspace governor 实现**；不实现则该属性不出现 |
| `flags` | `CPUFREQ_GOV_DYNAMIC_SWITCHING` / `CPUFREQ_GOV_STRICT_TARGET` | 前者表示"我自己会动态调频"（受 `CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING` 驱动 flag 拒绝）；后者表示"请精确设到我要的值，不要往上取整" |

| 辅助 | 说明 | 备注 |
|---|---|---|
| `cpufreq_policy_apply_limits(policy)` | 当前频率超出 `[min,max]` 就往回拉 | `static inline`；内部用 `_HE`/`_LE` relation。多数 governor 的 `limits` 就填它 |
| `get_governor_parent_kobj(policy)` | governor 参数目录该挂在哪个 kobject 下 | 按 `have_governor_per_policy()` 返回 policy 的 kobj 或全局 kobj |
| `cpufreq_start_governor(policy)` / `cpufreq_stop_governor(policy)` | 核心内部启停 | **声明在公共头文件但没有 `EXPORT_SYMBOL`** ⇒ 只有内建代码（含 schedutil）能用 |

### 5.3 下发频率：governor 调用的三条路

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_driver_target(policy, target_freq, relation)` | **常规路径**：自己上 `policy->rwsem` 写锁再下发 | 可能睡眠 |
| `__cpufreq_driver_target(policy, target_freq, relation)` | 同上，但**调用者必须已持写锁** | 核心内部和已持锁的 governor 用 |
| `cpufreq_driver_fast_switch(policy, target_freq)` | **调度上下文路径**：不加锁、不发通知、不睡眠 | 返回**实际频率**，0 表示失败；调用前必须 `cpufreq_enable_fast_switch()` 成功过 |
| `cpufreq_driver_adjust_perf(cpu, min_perf, target_perf, capacity)` | `fast_switch` 的性能值版本 | 先用 `cpufreq_driver_has_adjust_perf()` 判断是否可用 |
| `cpufreq_driver_has_adjust_perf()` | 当前驱动是否实现了 `adjust_perf` | `bool` |
| `cpufreq_driver_resolve_freq(policy, target_freq)` | **只算不发**：返回"如果下发这个目标，实际会落到哪一档" | 给 schedutil 做速率限制判断用 |
| `cpufreq_policy_transition_delay_us(policy)` | 两次切换的最小间隔 | 见 §3.3 |

**`fast_switch` 和 `target` 不是"快慢"之分，而是"能不能睡"之分。**
`fast_switch` 路径**不发 `CPUFREQ_PRECHANGE`/`POSTCHANGE` 通知**
（因为通知链回调可能睡），这直接导致一部分观测手段在 schedutil 下失效
—— 详见 `07-debug.md` §4。

### 5.4 调度器挂钩（schedutil 专用）

头文件：`include/linux/sched/cpufreq.h`；实现：`kernel/sched/cpufreq.c`。

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_add_update_util_hook(cpu, data, func)` | 把一个回调挂到该 CPU 的调度器路径上 | 每次调度器更新利用率就会调 `func` |
| `cpufreq_remove_update_util_hook(cpu)` | 摘掉 | 内部有 `synchronize_rcu()`，**会阻塞** |
| `cpufreq_this_cpu_can_update(policy)` | 当前 CPU 能不能代表这个 policy 下发 | 多 CPU 共享 policy 时用来选"谁负责" |
| `map_util_freq(util, freq, cap)` | `freq * util / cap` | `static inline` |
| `map_util_perf(util)` | `util + util/4`（留 25% 余量） | `static inline`；这就是 schedutil 那个"1.25 倍"系数的来源 |
| `SCHED_CPUFREQ_IOWAIT` | 传给 `func` 的标志：这次唤醒来自 iowait | schedutil 的 iowait boost 就靠它 |
| `sched_cpufreq_governor_change(policy, old_gov)` | governor 变了通知调度器（重建能量感知调度域） | 只在 `CONFIG_ENERGY_MODEL` 下有实体，见 `kernel/sched/topology.c` |

### 5.5 采样型 governor 骨架（ondemand / conservative 专用）

头文件：`drivers/cpufreq/cpufreq_governor.h`（**不是公共头文件**）；
实现：`drivers/cpufreq/cpufreq_governor.c`。

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_dbs_governor_init(policy)` · `_exit(policy)` · `_start(policy)` · `_stop(policy)` · `_limits(policy)` | 五个回调的公共实现，直接填进 `cpufreq_governor` | ondemand/conservative 只需实现"看了负载之后要设多少频率"这一件事 |
| `dbs_update(policy)` | 采一次样，返回这段时间的**平均负载百分比** | 两个 governor 的策略函数都以它开头 |
| `gov_update_cpu_data(dbs_data)` | 参数改了之后重置每 CPU 的采样基准 | |
| `sampling_rate_store(attr_set, buf, count)` | `sampling_rate` 属性的公共 store 实现 | 是 sysfs store 而不是普通函数 |
| `gov_update_sample_delay(policy_dbs, delay_us)` | 改下一次采样的延迟 | `static inline` |
| `dbs_governor_of(policy)` · `to_dbs_data(attr_set)` | 容器转换 | `static inline`，`container_of` 封装 |
| `od_register_powersave_bias_handler(f, flags)` / `od_unregister_powersave_bias_handler()` | 让别的模块插手 ondemand 的决策 | 全树只有 x86 的 `amd_freq_sensitivity.c` 用 ⇒ **arm64 不涉及** |

| 参数容器 API | 说明 | 备注 |
|---|---|---|
| `gov_attr_set_init(attr_set, list_node)` | 初始化一组 governor 参数（引用计数 = 1） | 实现在 `drivers/cpufreq/cpufreq_governor_attr_set.c` |
| `gov_attr_set_get(attr_set, list_node)` | 又一个 policy 共用这组参数 | |
| `gov_attr_set_put(attr_set, list_node)` | 返回**剩余引用数**，为 0 时调用者负责释放 | 返回值不是错误码 |
| `to_gov_attr_set(kobj)` | kobject → `gov_attr_set` | `static inline` |
| `governor_sysfs_ops` | governor 参数目录的 sysfs 操作 | 是**变量**（`extern const`） |
| `struct governor_attr` | governor 参数的属性描述（`show`/`store`） | 定义在 `include/linux/cpufreq.h` |

**为什么 governor 参数要单独搞一层引用计数**：多个 policy 可以共用一份
`sampling_rate`（`have_governor_per_policy()` 为假时），于是参数对象的生命周期
和 policy 不同步。这是 `02-datastructures.md` §1.3 要摊开的一组关系。

---

## §6 频率变更通知与两段式协议

头文件：`include/linux/cpufreq.h`；实现：`drivers/cpufreq/cpufreq.c`。

### 6.1 通知链

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_register_notifier(nb, list)` | 注册通知回调 | `list` 取 `CPUFREQ_TRANSITION_NOTIFIER` 或 `CPUFREQ_POLICY_NOTIFIER`；传别的值返回 `-EINVAL` |
| `cpufreq_unregister_notifier(nb, list)` | 注销 | |

| 常量 | 含义 |
|---|---|
| `CPUFREQ_TRANSITION_NOTIFIER` | 关心**频率变化**，回调参数是 `struct cpufreq_freqs` |
| `CPUFREQ_POLICY_NOTIFIER` | 关心**policy 创建/销毁**，事件为 `CPUFREQ_CREATE_POLICY` / `CPUFREQ_REMOVE_POLICY` |
| `CPUFREQ_PRECHANGE` / `CPUFREQ_POSTCHANGE` | transition 通知的两个时机 |

⚠️ **通知只在 `target`/`target_index` 路径上发，`fast_switch` 路径不发。**
arm64 默认 governor 是 schedutil 且大多数驱动支持 `fast_switch`
⇒ **默认配置下 transition 通知几乎收不到**。依赖它做统计的代码要特别小心
（`07-debug.md` §4 有完整的"哪些观测点在哪条路径上有效"表）。

### 6.2 两段式协议（驱动侧）

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_freq_transition_begin(policy, freqs)` | 发 `PRECHANGE`，并**等待上一次转换结束** | 只有带 `CPUFREQ_ASYNC_NOTIFICATION` 的驱动需要自己调；用 `target_index` 的驱动由核心代劳 |
| `cpufreq_freq_transition_end(policy, freqs, transition_failed)` | 发 `POSTCHANGE`，唤醒等待者 | `transition_failed` 非 0 时会把 `freqs->new` 换回旧值 |

判断"我要不要自己调这一对"：**只有设了 `CPUFREQ_ASYNC_NOTIFICATION` flag
且频率变化是异步完成的驱动才需要**。arm64 上几乎没有 —— 见 `04-extensions.md` §2.4。

---

## §7 boost、挂起恢复、限制刷新

实现：`drivers/cpufreq/cpufreq.c`（boost 部分也用到 `freq_table.c`）。

### 7.1 boost（超频档）

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_enable_boost_support()` | 让**没有 `set_boost` 回调**的驱动也支持 boost | 只能在 `cpufreq_register_driver()` **之前**调；已有 `set_boost` 时返回 `-EBUSY` |
| `cpufreq_boost_enabled()` | 当前 boost 是否打开 | 返回 `int`（0/1） |
| `cpufreq_boost_trigger_state(state)` | 打开/关闭 boost | 供 `/sys/devices/system/cpu/cpufreq/boost` 使用 |
| `policy_has_boost_freq(policy)` | 频率表里有没有 boost 档 | 见 §4.2 |

### 7.2 挂起恢复

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_suspend()` / `cpufreq_resume()` | **系统级**挂起恢复，由 PM 核心调 | 不是驱动调的 |
| `cpufreq_generic_suspend(policy)` | 通用 `->suspend()` 实现 | 见 §3.3 |

### 7.3 限制刷新（外部因素改变了可用频率范围）

| API | 说明 | 备注 |
|---|---|---|
| `cpufreq_update_policy(cpu)` | 重新问驱动一次当前频率并按需修正 | 用于"频率被固件偷偷改了" |
| `cpufreq_update_limits(cpu)` | 重新计算 `min/max`（走 `->update_limits()` 或 `refresh_frequency_limits()`） | 用于"限制条件变了"，如热限制、`freq_qos` 请求 |
| `refresh_frequency_limits(policy)` | 重算并夹紧，**调用者需持写锁** | 导出符号，但基本只有核心和 thermal 侧用 |

这三个的区别是 07 篇一个常见困惑点：**`update_policy` 是"频率变了"，
`update_limits` 是"允许范围变了"**。热限制走的是 `freq_qos`
⇒ 最终落到 `refresh_frequency_limits()`，见 `04-extensions.md` §6。

---

## §8 `CONFIG_CPU_FREQ=n` 时会怎样

`include/linux/cpufreq.h` 里有一整套 `#else` 分支的桩函数。要点：

| 桩的行为 | 涉及哪些 |
|---|---|
| 返回 **0** | `cpufreq_get`、`cpufreq_quick_get`、`cpufreq_quick_get_max`、`cpufreq_get_hw_max_freq` |
| 返回 **`NULL`** | `cpufreq_cpu_get`、`cpufreq_cpu_get_raw` |
| 返回 **`-EINVAL`** | `cpufreq_register_notifier`、`cpufreq_unregister_notifier` |
| 返回 **`false`** | `cpufreq_supports_freq_invariance` |
| **空函数** | `cpufreq_cpu_put`、`cpufreq_suspend`、`cpufreq_resume`、`disable_cpufreq`、`arch_set_freq_scale` |
| `CONFIG_CPU_FREQ_STAT=n` 时空函数 | `cpufreq_stats_create_table` / `_free_table` / `_record_transition` |
| `CONFIG_CPU_FREQ=n` 时 `-EINVAL` | `cpufreq_boost_trigger_state`、`cpufreq_enable_boost_support`、`of_perf_domain_get_sharing_cpumask` |

一条实用后果：**`cpufreq_quick_get()` 返回 0 既可能是"没编 cpufreq"，
也可能是"这颗 CPU 没有 policy"**。调用方**无法区分**这两种情况
—— 这是 `08-feynman.md` §7 的一个例子。

---

## §9 错误码语义

完整分诊表（每个返回点 + 按语义分组）在 `07-debug.md` §5。这里只给速查：

| 错误码 | 在核心里出现的典型含义 |
|---|---|
| `-EINVAL` | **最多的一个**（`cpufreq.c` 里 22 处 `return -EINVAL`）：参数非法、频率表非法、找不到 policy、通知链类型不对、governor 名字不认识……**语义严重超载**，光看返回码定位不了原因 |
| `-ENODEV` | cpufreq 被 `disable_cpufreq()` 关掉了；或该 CPU 上驱动 `init` 拒绝接管 |
| `-EEXIST` | 已经有一个 cpufreq 驱动注册了（全系统唯一） |
| `-EBUSY` | governor 同名重复注册；`cpufreq_enable_boost_support()` 在已有 `set_boost` 时调用 |
| `-ENOMEM` | 分配 policy / 频率表失败 |
| `-EPROBE_DEFER` | 依赖（时钟、regulator、OPP）还没就绪，稍后重试 |
| `-ENODATA` | 该 CPU 没有对应的 policy 数据 |
| `-ENXIO` | sysfs 访问到一个已经不活跃的 policy |
| `-EIO` | 频率下发失败（驱动回调报错后的转译） |
| `-EFBIG` | 频率表项数超过 sysfs 缓冲能装下的量（`freq_table.c`） |
| `-ENOENT` | 频率表里找不到请求的那一档 |

**`-EINVAL` 的超载是这个子系统调试最大的障碍**：
它同时覆盖"你写错了"和"平台不支持"两类完全不同的问题。
实用办法是配合 `07-debug.md` §2 的动态调试开关，而不是盯着返回码猜。

---

## §10 去哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| 先建立全貌 | `00-overview.md` |
| 看 policy / driver / governor 三个结构体的字段和关系 | `02-datastructures.md` |
| 逐行读 policy 创建、governor 切换、频率下发 | `03-internals.md` |
| 看 governor 的三条路线和各类驱动的差异 | `04-extensions.md` |
| 抄一份能用的驱动或 governor 骨架 | `05-usage.md` |
| DTS 里 OPP 表、`clocks`、`cpu-supply` 怎么写 | `06-devicetree.md` |
| sysfs / tracepoint / 调频不生效怎么查 | `07-debug.md` |
| 大白话解释 policy 与 CPU 的关系、schedutil 为什么在 `kernel/sched/` | `08-feynman.md` |
| 每条结论的证据等级 | `99-evidence.md` |
