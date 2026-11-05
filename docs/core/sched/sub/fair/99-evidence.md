---
title: 取证附录
---

# fair · 99 取证附录

> 本篇记录 fair 子模块三篇([`00`](00-overview.md)/[`02`](02-internals.md)/[`03`](03-algorithm.md))所有结论的可复现出处。顶层取证在 [`../../99-evidence.md`](../../99-evidence.md)。
>
> 基线:Linux 6.1.158,`-rt58`,arm64。文件除注明外均 `kernel/sched/fair.c`。

## §1 版本与代际确认(CFS,非 EEVDF)

| 结论 | 证据 | 命令/行号 |
|---|---|---|
| 本树为 CFS 代,非 EEVDF | `sched_entity` 无 deadline/vlag/slice 字段 | `sed -n '547,581p' include/linux/sched.h` |
| vruntime 纯有符号比较 | `entity_before` = `(s64)(a->vruntime - b->vruntime) < 0` | fair.c:583-587 |
| min_vruntime 单调维护 | `update_min_vruntime` 尾部 `max_vruntime` | fair.c:592-618(尾 616-617) |

EEVDF 是 6.6+ 引入;本树 `sched_entity` 字段级缺失即证未 backport。

## §2 五回调实现行号(已核对到函数结束 `}`)

| 回调 | 函数 | 行号区间 | 复核命令 |
|---|---|---|---|
| enqueue | `enqueue_task_fair` | 6093-6177 | `awk 'NR==6093,NR==6177' fair.c` |
| dequeue | `dequeue_task_fair` | 6186-6254 | 同法 |
| pick_next | `pick_next_task_fair` | 7793-7934(封装 `__pick_next_task_fair` 7936-7940) | 同法 |
| put_prev | `put_prev_task_fair` | 7944-7953 | 同法 |
| set_next | `set_next_task_fair` | 12100-12121 | 同法 |
| tick | `task_tick_fair` 11870-11887 → `entity_tick` 5069-5101 | | 同法 |
| 类注册 | `DEFINE_SCHED_CLASS(fair)` | 12443 | `rg -n 'DEFINE_SCHED_CLASS\(fair' fair.c` |

## §3 vruntime 机制取证

| 机制 | 函数 | 行号 |
|---|---|---|
| 运行时间累加 | `update_curr` | 882-920(vruntime+= @908,update_min_vruntime @909,trace @914) |
| vruntime 初值 | `place_entity` | 4648-4703(START_DEBIT @4658-4659;唤醒减 thresh @4666-4677;溢出保护 @4689-4700) |
| 取最小 | `__pick_first_entity` | 638-646(`rb_first_cached`) |
| 插/删红黑树 | `__enqueue_entity` 628-631 / `__dequeue_entity` 633-636 | |
| min_vruntime 推进 | `update_min_vruntime` | 592-618 |

## §4 参数默认值取证

| 参数 | 值 | 行号 | 用点 |
|---|---|---|---|
| `sysctl_sched_latency` | 6ms | 72 | `__sched_period`@715、`place_entity`@4668 |
| `sysctl_sched_min_granularity` | 0.75ms | 93 | `__sched_period`@713、`check_preempt_tick`@4932 |
| `sysctl_sched_wakeup_granularity` | 1ms | 124 | `wakeup_gran`@7584(唯一用点) |
| `sysctl_sched_idle_min_granularity` | 源码 | 102 | SCHED_IDLE |
| `sched_nr_latency` | 8 | 107 | latency 周期任务数阈值 |

## §5 特性开关取证(features.h)

| SCHED_FEAT | 行 | 默认 | 消费点 |
|---|---|---|---|
| GENTLE_FAIR_SLEEPERS | 7 | true | fair.c:4674 |
| START_DEBIT | 13 | true | fair.c:4658 |
| NEXT_BUDDY | 20 | false | fair.c:7683 |
| LAST_BUDDY | 27 | true | fair.c:7755 |
| CACHE_HOT_BUDDY | 33 | true | fair.c:8230(LB 段) |
| WAKEUP_PREEMPTION | 38 | true | fair.c:7710 |
| PREEMPT_LAZY | 52 | true(仅 CONFIG_PREEMPT_RT && CONFIG_PREEMPT_LAZY) | RT lazy 语义 |

buddy 消费:`set_last_buddy`@7631、`set_next_buddy`@7642、`__clear_buddies_last/next/skip`@4797/4808/4819。

## §6 抢占决策取证

| 决策 | 函数 | 行号 | 关键点 |
|---|---|---|---|
| 唤醒抢占 | `check_preempt_wakeup` | 7662-7757 | 命中 `resched_curr_lazy`@7742;NEXT_BUDDY@7683;WAKEUP_PREEMPTION 短路@7710;LAST_BUDDY@7755 |
| 辅助 | `wakeup_gran` 7582-7601 / `wakeup_preempt_entity` 7617-7629 | | sysctl_sched_wakeup_granularity@7584 |
| tick 抢占 | `check_preempt_tick` | 4909-4943 | ideal_runtime 超限@4918;min_granularity 早退@4932;vruntime 落后@4942 |

## §7 RT 差异取证(阶段 1 改动文件回访)

| 结论 | 证据 | 行号 |
|---|---|---|
| fair.c 全部抢占置位用 `resched_curr_lazy` | 落点枚举 | 4918、4942、5088、5237、7742、11918、11945 |
| `resched_curr_lazy` 定义/声明 | core.c / sched.h | core.c:1056;kernel/sched/sched.h:2355(非 RT stub@2357) |
| fair.c 无 `#ifdef CONFIG_PREEMPT_RT` 直接块 | 差异全经 resched_curr_lazy 间接体现 | `rg -n 'ifdef CONFIG_PREEMPT_RT' fair.c` = 0 |
| features.h RT 分支 | PREEMPT_LAZY 仅 CONFIG_PREEMPT_LAZY 启用 | features.h:49-53 |
| 与 arm64 backport 一致 | RESCHED_LAZY 标志位 | git status: arch/arm64 thread_info.h/preempt.h/asm-offsets.c |

## §8 观测点取证

Tracepoints:`trace_sched_stat_runtime`@914、`trace_sched_stick_numa`@2263/2272/2280、`trace_sched_util_est_cfs_tp`@4296/4312、`trace_sched_util_est_se_tp`@4413、`trace_sched_overutilized_tp`@6043、`trace_sched_cpu_capacity_tp`@8948。

schedstat 共 41 处,门控 `schedstat_enabled`@897;`exec_max`@901、`exec_clock`@906、wait/sleep/block@933/950/961/976/993/1011、`nr_spread_over`@4620。

## §9 索引盲区声明

以下结论已用 `rg` + Read 交叉核实,未单靠索引:

- `resched_curr_lazy` 是宏/inline,索引对间接抢占置位的调用图不完整——落点行号由 `rg -n 'resched_curr_lazy' fair.c` 逐个 Read 确认。
- `for_each_sched_entity` 是宏展开的循环,组调度逐层遍历不在调用图中——按宏定义与用点人工确认。
- 参数默认值经 Read 源码行确认,非索引推断。
