---
title: 调试方法
---

# 07 · 调试与可观测性

> 本篇是"出了问题从哪看"的地图:调度器暴露的所有观测面(/proc、debugfs、tracepoint、BPF、schedstat 计数),每个观测点在哪个文件实现、看的是哪个子模块的状态。运维旋钮见 [`06-tuning.md`](06-tuning.md);字段背后的对象结构见 [`02-objects.md`](02-objects.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**,已确认 `CONFIG_PREEMPT_LAZY`),arm64。

## §1 /proc 观测面

### §1.1 /proc/schedstat(runqueue + domain 级统计)

| 观测点 | 实现 | 子模块 | 说明 |
|---|---|---|---|
| 展示函数 | `show_schedstat`@stats.c:108 | stats | rq 级 + domain 级 |
| rq 级字段 | stats.c:125-131 | core(rq) | yld_count/sched_count/sched_goidle/ttwu_count/ttwu_local/rq_cpu_time/run_delay/pcount |
| domain 级字段 | stats.c:141-161(CONFIG_SMP) | fair(负载均衡)+topology | lb_*/alb_*/sbe_*/sbf_*/ttwu_wake_remote/ttwu_move_affine/ttwu_move_balance |
| seq 迭代器 | `schedstat_start`@stats.c:176 | stats | pos1=header,pos2=cpu0 |
| proc 注册 | `proc_schedstat_init`@stats.c:216(subsys_initcall@:221) | stats | `/proc/schedstat` |

domain 级 `lb_*` 是排查负载均衡问题的主入口——lb_failed/lb_imbalance 高说明均衡吃力。这些字段需 `schedstats=enable`(见 §5 门控)。

### §1.2 /proc/&lt;pid&gt;/sched(单任务全维度)

| 字段组 | 实现 | 子模块 |
|---|---|---|
| 展示函数 | `proc_sched_show_task`@debug.c:946 | debug |
| se 基础(exec_start/vruntime/sum_exec_runtime/nr_migrations) | debug.c:960-966 区 | fair(CFS 实体) |
| schedstat(sum_sleep/block_runtime/wait_*/sleep_*/block_*/exec_max/slice_max/iowait_sum/core_forceidle_sum) | debug.c:971-1018 区(`schedstat_enabled()` 门控) | stats |
| 切换计数(nr_switches/nr_voluntary/nr_involuntary) | debug.c:1022-1024 区 | core |
| PELT 平均(load_sum/runnable_sum/util_sum) | debug.c:1028-1030 区 | pelt |
| NUMA 统计 | `sched_show_numa`@debug.c:931(CONFIG_NUMA_BALANCING) | fair(NUMA) |
| 写(清零 max) | `proc_sched_set_task`@debug.c:1065 | debug |

看单个卡顿任务先看这里:`vruntime` 判公平、`wait_max`/`sleep_max`/`block_max` 判被谁挤,`nr_involuntary_switches` 高说明被频繁抢占。

## §2 /proc/sched_debug 与 debugfs

| 观测点 | 实现 | 子模块 |
|---|---|---|
| debugfs 根初始化 | `sched_init_debug`@debug.c:299 | debug |
| features 文件 | `sched_feat_show`@debug.c:56 / write@:123 / fops@:161 | core(sched_feat) |
| 抢占动态模式 | `sched_dynamic_show`@debug.c:245 / write@:221 | core(preempt dynamic) |
| latency_warn_ms / _once | debug.c:316-317 | core(resched latency) |
| sd_flags | `sd_flags_show`@debug.c:350 / `update_sched_domain_debugfs`@:394 | topology |
| /proc/sched_debug 顶层 | `sched_debug_show`@debug.c:836 | debug |
| 全局头(sysctl/jiffies/features) | `sched_debug_header`@debug.c:783 | debug |
| 单 CPU dump | `print_cpu`@debug.c:716 | debug |
| CFS rq 状态 | `print_cfs_rq`@debug.c:581 | fair |
| RT rq 状态 | `print_rt_rq`@debug.c:663 | rt |
| DL rq 状态 | `print_dl_rq`@debug.c:693 | deadline |
| CFS group 统计 | `print_cfs_group_stats`@debug.c:447(CONFIG_FAIR_GROUP_SCHED) | fair(task_group) |
| 单任务行 / rq 列表 | `print_task`@debug.c:530 / `print_rq`@debug.c:560 | fair |
| print_{cfs,rt,dl}_stats 分派 | debug.c:769-771 → fair.c:12490 / rt.c:3062 / deadline.c:3124 | fair/rt/deadline |
| sysrq 触发 | `sysrq_sched_debug_show`@debug.c:848 | debug(SysRq-t) |
| resched latency 告警 | `resched_latency_warn`@debug.c:1072 | core |

`/proc/sched_debug` 是全局快照:一次 dump 出每个 CPU 的 cfs/rt/dl 三队列状态 + runqueue 上所有任务。排查"某 CPU 空转/某队列积压"从这里入手。分派解耦(顶层 debug.c 调各类 `print_*_stats`)对应 [`04-interactions.md`](04-interactions.md) §3.1 的 sched_class 回调模式。

## §3 Tracepoints

定义于 `include/trace/events/sched.h`(全部 `TRACE_EVENT`/`DEFINE_EVENT`/`DECLARE_TRACE` 宏,索引盲区,经 rg 直取)。

### §3.1 核心调度事件

| Event | 行 | 代表触发点 | 子模块 |
|---|---|---|---|
| sched_switch | :222 | core.c:6675(`__schedule`) | core |
| sched_waking / sched_wakeup | :170 / :178 | core.c:4212 / :4214,:3761 | core |
| sched_wakeup_new | :185 | core.c:4858 | core |
| sched_migrate_task | :274 | core.c:3195 | core |
| sched_pi_setprio | :528 | core.c:7130 | core(PI) |
| sched_wait_task / process_wait | :340 / :347 | core.c:3450 | core |
| sched_process_{fork,exec,exit,free,hang} | :372/:400/:333/:326/:557 | — | core(进程生命周期) |
| sched_wake_idle_without_ipi | :671 | core.c:1042/:1161/:3900 | core |
| sched_update_nr_running_tp | :734 | core.c:11435 | core |

`sched_switch` + `sched_waking`/`sched_wakeup` 是延迟分析(wakeup latency)的黄金三件套——`perf sched` / `bpftrace` 都靠它们。

### §3.2 统计与 PELT / NUMA / EAS 事件

| Event | 行 | 触发点 | 子模块 |
|---|---|---|---|
| sched_stat_{wait,sleep,iowait,blocked} | :463/:471/:479/:486 | stats.c:37/:69/:90/:93 | stats(SCHEDSTAT 门控) |
| sched_stat_runtime | :520 | fair.c:914 / rt.c:1067 / deadline.c:1339 | fair/rt/deadline |
| sched_move_numa / stick_numa / swap_numa | :579/:651/:659 | core.c:9285 / fair.c:2263.. / core.c:3317 | fair(NUMA) |
| pelt_{cfs,rt,dl,thermal,irq,se}_tp | :694-714 | pelt.c:299/:313/:328/:354/:380/:410/:464 | pelt(裸 DECLARE_TRACE) |
| sched_cpu_capacity_tp | :718 | fair.c:8948 | fair |
| sched_overutilized_tp | :722 | fair.c:6043 | fair(EAS) |
| sched_util_est_{cfs,se}_tp | :726/:730 | fair.c:4296,:4312 / :4413 | fair(util_est) |
| sched_kthread_* | :16-119 | — | kthread / kthread_work |

裸 `*_tp`(include/trace/events/sched.h:694-734,`DECLARE_TRACE` 生成)没有 format 文件,是专为工具/BPF 设计的挂载面(见 include/trace/events/sched.h:692 注释)——PELT 曲线、EAS 过载判定都从这挂。

## §4 BPF 可挂载点

| 事项 | 证据 | 结论 |
|---|---|---|
| sched_ext(BPF 可扩展调度类) | `find kernel/sched -name '*ext*'` 无结果 | 6.1 **无 sched_ext**(6.12 才合入),不能用 BPF 写自定义调度类 |
| kernel/sched/ 内联 BPF 钩子 | `grep -rin bpf kernel/sched/` 零命中 | 调度器核心不含内联 BPF |
| 唯一 BPF 面 | §3 全部 tracepoint | 经通用 raw tracepoint / tp_btf 机制挂载 sched_* tracepoint(含裸 `*_tp`);无调度器专属 BPF 程序类型 |

结论:本树上 BPF 只能"观测"调度器(挂 tracepoint),不能"改写"调度决策。想用 sched_ext 需 6.12+。

## §5 schedstat 计数(门控与落点)

| 观测点 | 实现 | 子模块 |
|---|---|---|
| 静态键 | `DEFINE_STATIC_KEY_FALSE(sched_schedstats)`@core.c:4599 | core |
| 命令行 / sysctl | `setup_schedstats`@core.c:4636 / `sysctl_schedstats`@core.c:4639,表项@:4665 | core |
| 门控宏 | `schedstat_enabled()`@stats.h:37;inc/add/set@:39/:41/:43;空实现@:75-81 | stats |
| CFS: exec_max/exec_clock | fair.c:901/:906(`update_curr`) | fair |
| CFS: nr_spread_over / slice_max | fair.c:4620 / :4975 | fair |
| CFS: nr_wakeups_affine* | fair.c:6479/:6483/:6484 | fair(wake affine) |
| RT: exec_max / sleep_block | rt.c:1064 / :1425,:1429(门控 :1346) | rt |
| DL: exec_max / sleep_block | deadline.c:1336 / :1607,:1611(门控 :1551) | deadline |

schedstat 默认关(静态键 false),`schedstat=enable` 或 `sysctl kernel.sched_schedstats=1` 才开;开了才有 §1.1 domain 统计和 §1.2 的 wait/sleep/block 明细。用 `static_branch_unlikely` 保证关时零开销。

## §6 RT(PREEMPT_RT)差异可观测点(强制)

本树确认 PREEMPT_RT(`CONFIG_PREEMPT_LAZY` 存在)。

| 观测点 | 实现 | 说明 |
|---|---|---|
| resched_curr_lazy(lazy preempt 核心) | core.c:1056(`#ifdef CONFIG_PREEMPT_LAZY`@:1045),sched_feat 门控@:1061 | RT 新增;fair 触发点 fair.c:4918/:4942/:5088/:5237/:5988/:7742/:11918/:11945 |
| preempt_lazy_count 初始化 | core.c:4767(CONFIG_HAVE_PREEMPT_LAZY) | fork 路径 |
| preempt_lazy_disable/enable | core.c:2266/:2298 | 上下文切换配对 |
| test/set_tsk_need_resched_lazy | core.c:1069/:1072 | NEED_RESCHED_LAZY 标志 |
| resched latency tracer | `cpu_resched_latency`@core.c:5562;阈值 `sysctl_resched_latency_warn_ms=100`@:138,once@:139;告警 `resched_latency_warn`@debug.c:1072;debugfs 旋钮 debug.c:316-317 | 非 RT 专属但与 RT 延迟观测强相关 |
| sched_switch prev_state 编码 | `__trace_sched_switch_state`@include/trace/events/sched.h:190,:245 | 捕获 TASK_RTLOCK_WAIT 等 RT 阻塞态,rtlock 阻塞在 sched_switch 可见 |

关键:RT 没有新增专属 tracepoint(sched.h 无 lazy 命名事件)。观测 RT 抢占行为靠已有 `sched_switch`(其 prev_state 编码含 RT 阻塞态)+ 上述 core.c 的 lazy 计数变量。lazy 抢占的机制解释见 [`08-feynman.md`](08-feynman.md) 概念三,并发语义见 [`05-concurrency.md`](05-concurrency.md) §7。

## §7 索引盲区声明

- `include/trace/events/sched.h` 全文的 `TRACE_EVENT`/`DEFINE_EVENT`/`DECLARE_EVENT_CLASS`/`DECLARE_TRACE` 均为宏,不产生常规符号,§3 全部经 `rg 'TRACE_EVENT|DEFINE_EVENT|DECLARE_.*EVENT|DECLARE_TRACE' include/trace/events/sched.h` 直取。
- 裸 `*_tp`(include/trace/events/sched.h:694-734)由 `DECLARE_TRACE` 生成,无 format 文件,BPF/自定义模块专用面(include/trace/events/sched.h:692 注释),同属宏盲区。
- `schedstat_inc/add/set`(stats.h:38-43)、`P_SCHEDSTAT`/`PN_SCHEDSTAT`(stats.c)、`P/PN/__PS`(debug.c)均为宏,字段名由 `#F` 字符串化,字段清单须读源码区块(本文按行区间标注),非索引可枚举。
- 复现命令:`rg -n 'TRACE_EVENT|DEFINE_EVENT|DECLARE_TRACE' include/trace/events/sched.h`;`rg -n 'trace_sched_|trace_pelt_' kernel/sched/`;`rg -rin 'bpf|sched_ext' kernel/sched/`;`rg -n 'schedstat_(inc|add|set|enabled)' kernel/sched/`。

