---
title: 取证附录
---

# 99 · 顶层取证附录

> 本篇记录顶层各篇(00-08)结论的可复现出处。fair 子模块的取证在 [`sub/fair/99-evidence.md`](sub/fair/99-evidence.md)。所有行号指 6.1.158 源码,文件除注明外均在 `kernel/sched/`;宏类结论标"盲区"并给 rg 复现命令。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**,`CONFIG_PREEMPT_LAZY` 确认),arm64。

## §1 版本与 RT 树判定

| 结论 | 证据 |
|---|---|
| 版本 6.1.158 | `./Makefile:2-4`(VERSION/PATCHLEVEL/SUBLEVEL) |
| PREEMPT_RT 树 `-rt58` | 三判据全中:localversion `-rt58`;`CONFIG_PREEMPT_RT`/`CONFIG_PREEMPT_LAZY` 存在;core.c/fair.c/features.h/sched.h 有 RT 专属块 |
| lazy 抢占核心 | `resched_curr_lazy`@core.c:1056(`#ifdef CONFIG_PREEMPT_LAZY`@:1045);kernel/sched/sched.h:2355(非 RT stub@2357) |

## §2 核心对象(00 §3 / 02)

| 符号 | 行 | 备注 |
|---|---|---|
| `struct rq` | kernel/sched/sched.h:954 | per-CPU 主结构 |
| `rq.__lock` | kernel/sched/sched.h:956 | **raw_spinlock_t** |
| `rq.nr_running` | kernel/sched/sched.h:962 | add@kernel/sched/sched.h:2411 / sub@2428 |
| `rq.nr_switches` | kernel/sched/sched.h:981 | |
| `rq.cfs/rt/dl` | kernel/sched/sched.h:990-992 | 内嵌各类 runqueue |
| `rq->curr` | kernel/sched/sched.h:1008 | `task_struct __rcu *`;RCU_INIT@core.c:6654,rcu_assign@core.c:9210 |
| `rq->idle/stop` | kernel/sched/sched.h:1009-1010 | |
| `rq.clock/clock_task` | kernel/sched/sched.h:1015/1017 | 双时钟故意不合并 |
| `struct cfs_rq` | kernel/sched/sched.h:550 | |
| `cfs_rq->curr` | kernel/sched/sched.h:574 | 裸 `sched_entity *`,与 rq->curr 同名不同物 |
| `cfs_rq->nr_running` | kernel/sched/sched.h:552 | 与 rq->nr_running 同名不同物 |
| `struct rt_rq` / `dl_rq` | kernel/sched/sched.h:663 / 704 | |
| `struct sched_class` | kernel/sched/sched.h:2148 | ops 回调表 |
| 同名陷阱三组 | curr(1008 vs 574)/nr_running(962 vs 552)/idle(1009 ptr vs 632 int) | 02 §3.1 |

## §3 sched_class 优先级链(00 §3 / 03 / 04)

`DEFINE_SCHED_CLASS` 实例:stop_task.c:117 / deadline.c:2738 / rt.c:2692 / fair.c:12443 / idle.c:499。链接序即优先级 stop > dl > rt > fair > idle(经 `.next` 或 section 排布)。分派解耦(core 调 `curr->sched_class->回调`)见 04 §3.1。

## §4 生命周期十跳(03)

关键落点:`__schedule`@core.c:6675(trace_sched_switch 同址);唤醒 `try_to_wake_up`→trace_sched_waking@core.c:4212 / wakeup@:4214,:3761;新任务 `wake_up_new_task`→trace_sched_wakeup_new@core.c:4858;迁移 trace_sched_migrate_task@core.c:3195;tick `scheduler_tick`(调 `curr->sched_class->task_tick`);fork/exit trace @include/trace/events/sched.h:372/:333。RT 抢占置位改 `resched_curr_lazy`(见 §1)。

## §5 并发:锁清单与偏序(05)

| 锁 | 行 | 类型 |
|---|---|---|
| `rq->__lock` | kernel/sched/sched.h:956 | raw_spinlock_t |
| `rt_bandwidth.rt_runtime_lock` | kernel/sched/sched.h:280 | raw |
| `rt_rq.rt_runtime_lock` | kernel/sched/sched.h:688 | raw |
| `dl_bw.lock` | kernel/sched/sched.h:319 | raw |
| `cfs_bandwidth.lock` | kernel/sched/sched.h:344 | raw |
| `cfs_rq.removed.lock` | kernel/sched/sched.h:592 | raw |
| `rto_lock` | kernel/sched/sched.h:862 | raw |
| `cpudl.lock` | cpudeadline.h:12 | raw |
| `pi_lock`(task) | — | raw |

偏序一等证据:注释 core.c:441-446,:448-449;double_rq 排序 kernel/sched/sched.h:2562-2588。RT 差异:`resched_curr_lazy`@core.c:1055-1082,`migrate_disable`@core.c:2254-2269,`SCHED_NR_MIGRATE_BREAK` RT=8/非=32@kernel/sched/sched.h:2442。全部核心锁为 raw_spinlock,RT 下语义不变。

## §6 API / ABI(01)

- syscall(core.c,SYSCALL_DEFINE):sched_setscheduler@8059、setparam@8074、setattr@8085、getscheduler@8128、getparam@8157、getattr@8238、setaffinity@8413、getaffinity@8465、yield@8518、get_priority_max@8956、min@8983、rr_get_interval@9048(compat@9061)、nice@7288;setpriority/getpriority@kernel/sys.c:211/281。
- uapi:策略常量 uapi/linux/sched.h:114-123;SCHED_FLAG_*@128-142;`struct sched_param`@types.h:7;`struct sched_attr`@types.h:102(SIZE_VER0 48@11 / VER1 56@12)。
- 内核内 API(EXPORT 处):wake_up_process@4476、schedule@6759、set_user_nice@7251、set_cpus_allowed_ptr@3019、sched_set_fifo@7947、__cond_resched@8547、yield@8831 等(详见 [`.evidence/R6-api.md`](evidence/R6-api.md))。
- RT/DYNAMIC:migrate_disable def@2254/EXPORT@2269(仅 CONFIG_SMP@2231);__cond_resched guard@8523;preempt dynamic 8556-8792。

## §7 可调项(06)

- sysctl 表:rt.c:33(注册@:64)/ deadline.c:29(@:51)/ core.c:4662(@:4701,schedstats+uclamp)/ autogroup.c:12(@:27)/ topology.c:241(@:256)。RT 带宽默认 950000/1000000 us@rt.c:25,19。
- 6.1 关键事实:sched_latency/min_granularity/wakeup_granularity/migration_cost/nr_migrate **不在 sysctl**,仅 debugfs(debug.c:311-322)。
- cgroup cpu controller:`cpu_legacy_files[]`@core.c:11094 / `cpu_files[]`@core.c:11303 / `cpu_cgrp_subsys`@core.c:11355。
- features 全量@features.h(GENTLE_FAIR_SLEEPERS@7 … BASE_SLICE@106);debugfs `features`@debug.c:305/fops@:161。RT:TTWU_QUEUE 默认 false@features.h:50,PREEMPT_LAZY@:52。
- boot:isolcpus@isolation.c:246、nohz_full@:194、preempt=@core.c:8768、schedstats=@core.c:4636。
- RT throttling:`sched_rt_runtime_exceeded`@rt.c:1002,置位@:1025,调用@:1081;禁用写 -1(判定 kernel/sched/sched.h:297,654)。

## §8 可观测性(07)

- /proc/schedstat:`show_schedstat`@stats.c:108,rq 级字段@:125-131,domain 级@:141-161,注册@:216。
- /proc/&lt;pid&gt;/sched:`proc_sched_show_task`@debug.c:946(schedstat 区@971-1018 受 `schedstat_enabled()` 门控)。
- /proc/sched_debug:`sched_debug_show`@debug.c:836;print_cfs/rt/dl_rq@debug.c:581/663/693;分派 print_*_stats@debug.c:769-771→fair.c:12490 / rt.c:3062 / deadline.c:3124。
- tracepoint:定义 include/trace/events/sched.h(盲区);sched_switch@:222(触发 core.c:6675)、waking/wakeup@:170/:178、migrate@:274、pi_setprio@:528;裸 pelt_*_tp@:694-714。
- BPF:6.1 无 sched_ext(`find kernel/sched -name '*ext*'`=0);`grep -rin bpf kernel/sched/`=0;唯一面为通用 tracepoint。
- schedstat:静态键 `sched_schedstats`@core.c:4599,门控 `schedstat_enabled()`@stats.h:37。

## §9 索引盲区总声明

以下类别全套文档统一走 rg + Read,不单靠代码图索引:

- 宏定义符号:`SYSCALL_DEFINEn`(真实符号 sys_*/__se_sys_*/__do_sys_* 不字面出现)、`TRACE_EVENT`/`DEFINE_EVENT`/`DECLARE_TRACE`、`DEFINE_SCHED_CLASS`、`sched_feat()`、`schedstat_inc/add/set`、`P/PN/__PS/P_SCHEDSTAT`、`preempt_model_##mode` token-paste、`for_each_sched_entity`。
- 间接调用:sched_class 回调经函数指针分派,调用图不完整;`resched_curr_lazy` 为 inline/宏。
- 默认值宏未展开数值:RR_TIMESLICE、SCHED_CAPACITY_SCALE(1024)、SCHED_NR_MIGRATE_BREAK(32)、RUNTIME_INF —— 引用数值须回查头文件。
- CONFIG 门控:cgroup cftype、uclamp、CFS/RT 带宽项、schedstat 均受 `#ifdef` 包裹,未核对目标 defconfig。
- 复现命令汇总:
  - `rg -n 'DEFINE_SCHED_CLASS' kernel/sched/`
  - `rg -n 'SYSCALL_DEFINE' kernel/sched/core.c`
  - `rg -n 'TRACE_EVENT|DEFINE_EVENT|DECLARE_TRACE' include/trace/events/sched.h`
  - `rg -rin 'bpf|sched_ext' kernel/sched/`
  - `rg -n 'resched_curr_lazy|migrate_disable|PREEMPT_LAZY' kernel/sched/`
  - `rg -n 'schedstat_(inc|add|set|enabled)' kernel/sched/`

## §10 分篇证据文件索引

顶层各篇的原始代理证据存于 `.evidence/`:

| 文件 | 覆盖篇 |
|---|---|
| [`.evidence/R1-objects.md`](evidence/R1-objects.md) | 02-objects |
| [`.evidence/R2-lifecycle.md`](evidence/R2-lifecycle.md) | 03-lifecycle |
| [`.evidence/R3-interactions.md`](evidence/R3-interactions.md) | 04-interactions |
| [`.evidence/R4-concurrency.md`](evidence/R4-concurrency.md) | 05-concurrency |
| [`.evidence/R5-fair.md`](evidence/R5-fair.md) | sub/fair/* |
| [`.evidence/R6-api.md`](evidence/R6-api.md) | 01-api |

06-tuning / 07-debug 的证据保存在本轮任务记录(R7/R8 代理输出),关键行号已并入本篇 §7/§8。

## §11 官方文档(`Documentation/scheduler/`)对照

内核自带的调度器文档是策略/设计层一等参考(非本树代码可完整验证的结论,如 CFS 公平性论证、EAS 能量模型,以其为准)。下表把每篇官方原文映射到本套文档的覆盖点;标"本轮未展开"者指该子模块不在本轮范围(顶层 + fair 样板),仅在顶层被提及,深入需读官方原文。

| 官方原文 | 主题 | 本套覆盖点 |
|---|---|---|
| `Documentation/scheduler/sched-design-CFS.rst` | CFS 设计(vruntime 公平) | [`sub/fair/03-algorithm.md`](sub/fair/03-algorithm.md)、[`08-feynman.md`](08-feynman.md) 概念一 |
| `Documentation/scheduler/sched-nice-design.rst` | nice 值 → 权重映射 | [`sub/fair/03-algorithm.md`](sub/fair/03-algorithm.md) §4(参数敏感性)、[`01-api.md`](01-api.md)(nice syscall) |
| `Documentation/scheduler/sched-capacity.rst` | CPU 算力模型 | [`02-objects.md`](02-objects.md) §2.5(`cpu_capacity`/`cpu_capacity_orig`) |
| `Documentation/scheduler/sched-energy.rst` | EAS 能量感知调度 | [`06-tuning.md`](06-tuning.md) §1.3(`sched_energy_aware`)、[`07-debug.md`](07-debug.md) §3.2(`sched_overutilized_tp`) |
| `Documentation/scheduler/sched-domains.rst` | 调度域层级 | [`02-objects.md`](02-objects.md) §1(`struct sched_domain`)、[`04-interactions.md`](04-interactions.md) §3.6 |
| `Documentation/scheduler/sched-bwc.rst` | CFS 带宽控制(quota/period/burst) | [`06-tuning.md`](06-tuning.md) §2(cgroup `cpu.cfs_*`)、§6 盲区(`default_cfs_period`) |
| `Documentation/scheduler/sched-rt-group.rst` | RT 组调度与带宽 | [`06-tuning.md`](06-tuning.md) §5(`RT_GROUP_SCHED` 分叉、`rt_nr_boosted`) |
| `Documentation/scheduler/sched-deadline.rst` | DL(EDF)调度与准入 | [`00-overview.md`](00-overview.md) §3(DL 类)、[`sub/deadline/00-overview.md`](sub/deadline/00-overview.md)(原理速览)、[`06-tuning.md`](06-tuning.md) §1.1(DL 带宽) |
| `Documentation/scheduler/sched-stats.rst` | schedstat 计数语义 | [`07-debug.md`](07-debug.md) §1.1、§5(门控与落点) |
| `Documentation/scheduler/sched-debug.rst` | `/proc/sched_debug` 输出 | [`07-debug.md`](07-debug.md) §2 |
| `Documentation/scheduler/sched-arch.rst` | 各架构调度接入约定 | [`05-concurrency.md`](05-concurrency.md) §7(arm64 RESCHED_LAZY 落点);arm64 视角,其余架构忽略 |
| `Documentation/scheduler/completion.rst` | completion 同步原语 | [`00-overview.md`](00-overview.md) §7 #11(utility/sync,`completion.c`) |
| `Documentation/scheduler/schedutil.rst` | schedutil 调频 | [`04-interactions.md`](04-interactions.md) §3.3、[`02-objects.md`](02-objects.md) §2.6(PELT 无锁读) |
| `Documentation/scheduler/text_files.rst` | 官方文档索引(无独立主题) | 汇总页,不单独对应章节 |

