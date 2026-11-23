# 06 · 可调项与运维视角

> 本篇是运维/调参视角的可调项地图:每个旋钮在哪个文件定义、默认值、影响哪个子模块、经哪个面暴露(sysctl / cgroup / boot cmdline / debugfs)。参数背后的算法敏感性见 [`sub/fair/03-algorithm.md`](sub/fair/03-algorithm.md) §4;并发/RT 语义见 [`05-concurrency.md`](05-concurrency.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §0 一个关键的 6.1 事实:CFS 核心参数不走 sysctl

6.1 里 `sched_latency` / `min_granularity` / `wakeup_granularity` / `migration_cost` / `nr_migrate` 这几个最常被"调参"的 CFS 旋钮,不再经 `/proc/sys/kernel/` 暴露,只在 debugfs(`/sys/kernel/debug/sched/`)下可写(见 §4)。老教程里 `sysctl kernel.sched_latency_ns` 的写法在本内核无效。真正还留在 sysctl 表里的是 RT/DL 带宽、uclamp、autogroup、energy_aware、schedstats 这几组(见 §1)。

## §1 sysctl 参数(`/proc/sys/kernel/sched_*`)

按注册的 sysctl 表分组。表项均经 `register_sysctl_init("kernel", …)`,`late_initcall` 生效。

### §1.1 RT / DL 带宽(rt.c / deadline.c)

| 参数 | 定义 | 默认 | 子模块 | 用点 |
|---|---|---|---|---|
| `sched_rt_period_us` | `rt.c:19` | 1000000 us | rt | `global_rt_period`@kernel/sched/sched.h:2045,`sched_rt_handler`@rt.c:2996 |
| `sched_rt_runtime_us` | `rt.c:25` | 950000 us | rt | `global_rt_runtime`/`rt_bandwidth_enabled`@kernel/sched/sched.h:297,654,2053 |
| `sched_rr_timeslice_ms` | `rt.c:28` | (MSEC/RR_TIMESLICE)/HZ | rt | `sched_rr_handler`@rt.c:3035-3053 |
| `sched_deadline_period_max_us` | `deadline.c:26` | 1<<22(~4s) | deadline | `sched_dl_period_max` 校验@deadline.c:3001 |
| `sched_deadline_period_min_us` | `deadline.c:27` | 100 us | deadline | @deadline.c:3002 |

表注册:`sched_rt_sysctls[]`@rt.c:33(注册@rt.c:64);`sched_dl_sysctls[]`@deadline.c:29(注册@deadline.c:51)。RT 带宽的运维含义见 §5。

### §1.2 uclamp / 其它核心(core.c)

| 参数 | 定义 | 默认 | 子模块 | 用点 |
|---|---|---|---|---|
| `sched_util_clamp_min` | `core.c:1339` | 1024(SCHED_CAPACITY_SCALE) | uclamp | `sysctl_sched_uclamp_handler`@core.c:1817 |
| `sched_util_clamp_max` | `core.c:1342` | 1024 | uclamp | 同上 |
| `sched_util_clamp_min_rt_default` | `core.c:1359` | 1024 | uclamp/rt | `__uclamp_update_util_min_rt_default`@core.c:1471 |
| `sched_schedstats` | (静态键 `core.c:4599`) | config 决定 | core/stats | `set_schedstats`@core.c:4601,表项@core.c:4665 |

表注册:`sched_core_sysctls[]`(schedstats + uclamp)@core.c:4662(注册@core.c:4701)。uclamp 的 min/max 是"利用率钳制"——给任务的 CPU 频率选择设下界/上界,`_rt_default` 让 RT 任务默认满钳(倾向高频)。

### §1.3 autogroup / energy_aware

| 参数 | 定义 | 默认 | 子模块 | 用点 |
|---|---|---|---|---|
| `sched_autogroup_enabled` | `autogroup.c:7` | 1 | autogroup | `setup_autogroup`@autogroup.c:223 |
| `sched_energy_aware` | `topology.c:209` | 1 | eas/topology | `sched_energy_aware_handler`@topology.c:223,`build_perf_domains` gate@topology.c:381 |

表注册:`sched_autogroup_sysctls[]`@autogroup.c:12(@:27);`sched_energy_aware_sysctls[]`@topology.c:241(@:256,`late_initcall`@:260)。`sched_energy_aware` 在 arm64 大小核平台尤其相关——关掉它会禁用 EAS(能量感知调度),回退到纯 CFS 负载均衡。

## §2 cgroup cpu controller 接口(core.c)

数组落点:v1 `cpu_legacy_files[]`@core.c:11094;v2 `cpu_files[]`@core.c:11303;subsys `cpu_cgrp_subsys`@core.c:11355(`.legacy_cftypes`@:11365,`.dfl_cftypes`@:11366)。

| 接口 | 读/写函数 | cftype | 子模块(config) |
|---|---|---|---|
| `cpu.shares`(v1) | `cpu_shares_read_u64`@10722 / `write`@10714 | @11097 | FAIR_GROUP_SCHED |
| `cpu.weight`(v2) | `cpu_weight_read_u64`@11184 / `write`@11193 | @11306 | FAIR_GROUP_SCHED |
| `cpu.weight.nice`(v2) | `read_s64`@11211 / `write`@11229 | @11312 | FAIR_GROUP_SCHED |
| `cpu.idle` | `read_s64`@11082 / `write`@11088 | v1@11102 / v2@11318 | FAIR_GROUP_SCHED |
| `cpu.cfs_quota_us`(v1) | `read_s64`@10905 / `write`@10911 | @11109 | CFS_BANDWIDTH |
| `cpu.cfs_period_us`(v1) | `read_u64`@10917 / `write`@10923 | @11114 | CFS_BANDWIDTH |
| `cpu.cfs_burst_us` / `cpu.max.burst` | `read_u64`@10929 / `write`@10935 | v1@11119 / v2@11332 | CFS_BANDWIDTH |
| `cpu.max`(v2) | `cpu_max_show` / `write`(经 `tg_set_cfs_bandwidth`@10740) | @11326 | CFS_BANDWIDTH |
| `cpu.stat`(v1) | `cpu_cfs_stat_show` | @11124 | CFS_BANDWIDTH |
| `cpu.rt_runtime_us` | `read`@11061 / `write`@11055 | @11130 | RT_GROUP_SCHED |
| `cpu.rt_period_us` | `read_uint`@11073 / `write`@11067 | @11135 | RT_GROUP_SCHED |
| `cpu.uclamp.min` | `show`@10700 / `write`@10663 | v1@11142 / v2@11340 | UCLAMP_TASK_GROUP |
| `cpu.uclamp.max` | `show`@10706 / `write`@10670 | v1@11148 / v2@11346 | UCLAMP_TASK_GROUP |

共用 setter:`cpu_uclamp_write`@10628;`tg_set_cfs_quota`@10828;`tg_set_cfs_period`@10857;`tg_set_cfs_burst`@10881。

要点:`cpu.shares`(v1)与 `cpu.weight`(v2)是同一件事(组权重)的两种 ABI 表达;CFS 带宽三件套(quota/period/burst)是把 [`sub/fair/`](sub/fair/00-overview.md) 之外的 cfs_bandwidth 子模块暴露给运维,节流逻辑与 `cfs_bandwidth.lock`(raw_spinlock)相关,见 [`05-concurrency.md`](05-concurrency.md) §1。每类接口都受对应 `CONFIG_*` 门控——defconfig 没开就整组不存在(见 §6 盲区)。

## §3 启动参数(boot cmdline)

| 参数 | 定义 | setup 函数 | 子模块 |
|---|---|---|---|
| `isolcpus=` | `isolation.c:246` | `housekeeping_isolcpus_setup` | isolation |
| `nohz_full=` | `isolation.c:194` | `housekeeping_nohz_full_setup` | isolation |
| `sched_verbose` | `topology.c:20`(early_param) | `sched_debug_setup` | topology/debug |
| `relax_domain_level=` | `topology.c:1425` | `setup_relax_domain_level` | topology |
| `sched_thermal_decay_shift=` | `fair.c:140` | `setup_sched_thermal_decay_shift` | fair/thermal PELT |
| `schedstats=` | `core.c:4636` | `setup_schedstats` | core/stats |
| `resched_latency_warn_ms=` | `core.c:5605` | `setup_resched_latency_warn_ms` | core |
| `preempt=` | `core.c:8768` | `setup_preempt_mode`(动态抢占) | core |
| `noautogroup` | `autogroup.c:227` | `setup_autogroup` | autogroup |
| `psi=` | `psi.c:154` | `setup_psi` | psi |
| `nohlt` / `hlt` | `idle.c:41` / `:49` | `cpu_idle_poll_setup` / `nopoll` | idle |

对 RT/低延迟部署最相关的是 `isolcpus=` + `nohz_full=`:两者经 housekeeping 掩码把指定 CPU 从常规负载均衡和 tick 里摘出来,给实时任务独占。`preempt=`(none/voluntary/full)在 `CONFIG_PREEMPT_DYNAMIC` 下启动期选抢占模型——但本树是 PREEMPT_RT,抢占语义已由 RT 决定(见 [`01-api.md`](01-api.md) §3、[`05-concurrency.md`](05-concurrency.md) §7)。`rcu_nocbs` 常与它们同用但属 RCU 子系统,不在 kernel/sched/。

## §4 调度 features(debugfs)

`sched/features` 文件读写整张特性位图;`features.h` 全量如下(默认值 = 宏第二参数)。挂载点:`debugfs_sched=debugfs_create_dir("sched",NULL)`@debug.c:303,`features` 文件@debug.c:305(`&sched_feat_fops`@debug.c:161)。

| 特性 | 行 | 默认 | 特性 | 行 | 默认 |
|---|---|---|---|---|---|
| GENTLE_FAIR_SLEEPERS | 7 | true | SIS_PROP | 66 | false |
| START_DEBIT | 13 | true | SIS_UTIL | 67 | true |
| NEXT_BUDDY | 20 | false | WARN_DOUBLE_CLOCK | 74 | false |
| LAST_BUDDY | 27 | true | RT_PUSH_IPI | 86 | true(HAVE_RT_PUSH_IPI) |
| CACHE_HOT_BUDDY | 33 | true | RT_RUNTIME_SHARE | 89 | false |
| WAKEUP_PREEMPTION | 38 | true | LB_MIN | 90 | false |
| HRTICK | 40 | false | ATTACH_AGE_LOAD | 91 | true |
| HRTICK_DL | 41 | false | WA_IDLE | 93 | true |
| DOUBLE_TICK | 42 | false | WA_WEIGHT | 94 | true |
| NONTASK_CAPACITY | 47 | true | WA_BIAS | 95 | true |
| TTWU_QUEUE | 60 / RT:50 | true / **false(RT)** | UTIL_EST | 100 | true |
| PREEMPT_LAZY | 52 | true(仅 RT&&LAZY) | UTIL_EST_FASTUP | 101 | true |
| — | | | LATENCY_WARN | 103 | false |
| — | | | ALT_PERIOD | 105 | true |
| — | | | BASE_SLICE | 106 | true |

RT 差异:`TTWU_QUEUE` 在 PREEMPT_RT 下默认 **false**(features.h:50,非 RT 为 :60 的 true)——RT 不把唤醒排到远端 CPU 队列以降延迟;`PREEMPT_LAZY`(features.h:52)仅在 `CONFIG_PREEMPT_RT && CONFIG_PREEMPT_LAZY` 下存在且默认开,是本树 lazy 抢占的总开关。

其它 debugfs 数值旋钮(均在 `debugfs_sched` 下,debug.c):`latency_ns`@311、`min_granularity_ns`@312、`idle_min_granularity_ns`@313、`wakeup_granularity_ns`@314、`latency_warn_ms`@316、`latency_warn_once`@317、`tunable_scaling`@320、`migration_cost_ns`@321、`nr_migrate`@322;`numa_balancing/` 子目录@330-336;`preempt`(动态抢占)@308;`verbose`@306;`debug`@339。这些就是 §0 说的"从 sysctl 挪到 debugfs"的 CFS 核心参数。

## §5 RT 差异:RT throttling 与 RT_GROUP_SCHED(强制)

| 机制 | 证据 | 语义 |
|---|---|---|
| RT throttling 判定 | `sched_rt_runtime_exceeded`@rt.c:1002,超限置 `rt_rq->rt_throttled=1`@rt.c:1025,调用点 `update_curr_rt`@rt.c:1081 | rt_time 超 rt_runtime 即节流 |
| 全局默认 95% | runtime 950000 / period 1000000@rt.c:25,19 | 每 1s 最多 0.95s 给 RT/DL |
| 关闭节流 | 判定 `sched_rt_runtime>=0`@kernel/sched/sched.h:297,654;写 -1(RUNTIME_INF),sysctl `extra1=SYSCTL_NEG_ONE`@rt.c:49 | `rt_runtime_us=-1` 关节流 |
| RT_GROUP_SCHED 分叉 | 组分支 rt.c:163,533-655;非组分支 rt.c:285-321,656-718 | 开启后 runtime 取 `rt_rq->rt_runtime`;`rt_rq_throttled` 含 `rt_nr_boosted`@rt.c:618(PI 提升豁免) |
| RT_RUNTIME_SHARE | features.h:89(默认 false);`balance_runtime`@rt.c:890 | 默认关:CPU 间不借调 RT 运行时 |
| cgroup RT 带宽 | `cpu.rt_runtime_us`/`rt_period_us` 仅 `CONFIG_RT_GROUP_SCHED`@core.c:11128 | 未开则组内无 RT 带宽接口 |
| uclamp RT 默认满钳 | `sched_util_clamp_min_rt_default`=1024@core.c:1359,`__uclamp_update_util_min_rt_default`@core.c:1471 | 仅影响 user_defined==false 的 RT 任务 |

运维要点:默认 95% 的 RT 带宽上限(`sched_rt_runtime_us=950000`)是防"跑飞的 FIFO 任务锁死系统"的安全网。硬实时部署常把它设为 -1(禁节流)并配合 `isolcpus`,但那样就必须自己保证 RT 任务会让出。`rt_nr_boosted` 的存在说明:被 PI(优先级继承)临时提升的任务不受节流,避免优先级反转下的死锁——这与 [`04-interactions.md`](04-interactions.md) 里 rtmutex↔sched 的 PI 耦合是同一件事。

## §6 索引盲区声明

- 默认值宏未展开:`RR_TIMESLICE`、`SCHED_CAPACITY_SCALE`(=1024)、`SCHED_NR_MIGRATE_BREAK`(=32)、`SCHED_TUNABLESCALING_LOG`(=1)、`RUNTIME_INF` 定义在头文件,正文引用其数值时需回查宏定义。
- CFS 带宽默认:`default_cfs_period`@fair.c:5141、`cfs_b->quota=RUNTIME_INF` 初值@fair.c:5827,具体常量(如 100ms period)未展开数字,须读函数体。
- `sched_rr_timeslice`(内核 jiffies@rt.c:7)与 `sysctl_sched_rr_timeslice`(ms@rt.c:28)经 `sched_rr_handler`@rt.c:3035 换算,边界(<=0 回退)须核 rt.c:3049-3053。
- 所有 cgroup cftype / uclamp sysctl / CFS 带宽项受 `#ifdef` 包裹;目标 defconfig 未启用对应 CONFIG(FAIR_GROUP_SCHED / CFS_BANDWIDTH / RT_GROUP_SCHED / UCLAMP_TASK[_GROUP] / SCHEDSTATS)则接口不存在。本文未核对具体 config 文件。
- `sysctl_sched_features` 双定义:`core.c:126`(SCHED_DEBUG 开)vs `kernel/sched/sched.h:2031`(关,编译期常量),运行期可写性依赖 `CONFIG_SCHED_DEBUG`。
- `numa_balancing` 可调项全局变量定义在 mm/ 或 core,本文只定位 debugfs 暴露点@debug.c:330-336。
- sysctl 表项、debugfs 注册、`__setup`/`early_param` 均为宏,行号指宏调用点;证据经 `rg` 直取。

