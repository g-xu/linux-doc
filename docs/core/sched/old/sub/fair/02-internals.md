# fair · 02 内部剖析

> 本篇讲 CFS 主流程"谁调用谁"(算法"为什么这么算"在 [`03-algorithm.md`](03-algorithm.md))。跨模块关系一律上引 [`../../04-interactions.md`](../../04-interactions.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。文件除注明外均 `kernel/sched/fair.c`。

## §1 核心数据结构(本模块视角)

| 结构 | 定义位置 | 关键字段 | 说明 |
|---|---|---|---|
| `struct sched_entity` | `include/linux/sched.h:547-581` | `load`@549、`run_node`@550、`on_rq`@552、`vruntime`@556、`sum_exec_runtime`@555、`avg`@579 | 调度实体,任务或组的公共调度单元 |
| `struct cfs_rq` | `kernel/sched/sched.h:550` | `load`@551、`nr_running`@552、`min_vruntime`@558、`tasks_timeline`(`rb_root_cached`)@568、`curr`@574、`next/last/skip`@575-577、`avg`@587 | 每 CPU(及每 task_group)一棵 CFS 就绪队列 |
| `struct sched_avg` | `include/linux/sched.h:493` | load/util/runnable 平均 | PELT 产出,写回 `se->avg` 与 `cfs_rq->avg` |

`cfs_rq->curr`(裸 `sched_entity *`)与 `rq->curr`(`task_struct __rcu *`)同名不同物,判别见 [`../../02-objects.md`](../../02-objects.md) §3.1。组调度下 `se->depth`(include/linux/sched.h:562)记录嵌套层级,`pick_next_task_fair` 据此逐层下溯。

## §2 入队流程 `enqueue_task_fair`(6093-6177)

| 步骤 | 做什么 | 关键行 |
|---|---|---|
| 1 | 对任务所在实体自底向上遍历(组调度逐层) | `for_each_sched_entity`@6104 |
| 2 | 每层 `enqueue_entity` 入本层 cfs_rq | 内调 `enqueue_entity` |
| 3 | `update_curr` 先结算当前实体运行时间 | `update_curr`@882 |
| 4 | `place_entity` 定新实体 vruntime 初值 | `place_entity`@4648 |
| 5 | `__enqueue_entity` 插红黑树 | `__enqueue_entity`@628(`rb_add_cached`) |
| 6 | `update_load_avg` 更新 PELT(喂 pelt-load) | 见 [`../../04-interactions.md`](../../04-interactions.md) §3.2 |
| 7 | `add_nr_running` 增 rq 计数 | `add_nr_running`@fair.c:5470/6154 |

`place_entity` 的 vruntime 补偿逻辑(唤醒任务减 thresh、新任务 START_DEBIT 加 vslice)是算法核心,详见 [`03-algorithm.md`](03-algorithm.md) §1.3。

## §3 出队流程 `dequeue_task_fair`(6186-6254)

与入队对称,自底向上遍历:`update_curr` 结算 → `__dequeue_entity`@633(`rb_erase_cached`)从红黑树摘除 → `update_load_avg` 更新 PELT → `sub_nr_running`@fair.c:5385/6245 减计数。`DEQUEUE_SLEEP` 标志区分"睡眠出队"(承接顶层 [`../../03-lifecycle.md`](../../03-lifecycle.md) 跳 #7)与"迁移出队"。

## §4 选任务流程 `pick_next_task_fair`(7793-7934)

| 步骤 | 做什么 | 关键行 |
|---|---|---|
| 1 | 若无 CFS 任务,直接返回(交回 core 兜底) | 早退分支 |
| 2 | 从当前 cfs_rq 取最左实体 `pick_next_entity` | `__pick_first_entity`@638 |
| 3 | 组调度:逐层 `set_next_entity` 下溯到叶子任务 | `set_next_entity` |
| 4 | 处理 buddy(next/last/skip)偏好 | buddy 消费见 [`03-algorithm.md`](03-algorithm.md) §1 |
| 5 | 若发生 curr 切换,`put_prev_entity` 放回旧实体 | `put_prev_task_fair`@7944 |

`__pick_next_task_fair`@7936-7940 是给 core 快路径(`core.c:5986`)直呼的薄封装,绕过 `for_each_class` 遍历——这是"绝大多数时候只有 CFS 任务"的优化,见 [`../../04-interactions.md`](../../04-interactions.md) §3.1。

## §5 tick 与抢占

| 路径 | 函数 | 行号 | 承接顶层 |
|---|---|---|---|
| tick 入口 | `task_tick_fair` | 11870-11887 | [`../../03-lifecycle.md`](../../03-lifecycle.md) 跳 #10 |
| 逐层驱动 | `entity_tick` | 5069-5101 | — |
| tick 抢占判定 | `check_preempt_tick` | 4909-4943 | — |
| 唤醒抢占判定 | `check_preempt_wakeup` | 7662-7757 | 承接跳 #3 后半 |

`check_preempt_tick`:`ideal_runtime` 超限@4918 或 vruntime 落后@4942 时置位;`delta_exec < min_granularity` 提前返回@4932(防过度抢占)。`check_preempt_wakeup`:`WAKEUP_PREEMPTION` 短路@7710、`NEXT_BUDDY`@7683、`LAST_BUDDY`@7755。所有抢占置位在本树用 `resched_curr_lazy()`(不是 `resched_curr()`),RT 差异见 [`../../05-concurrency.md`](../../05-concurrency.md) §7 与本篇 §7。

## §6 本子模块专属可观测点

Tracepoints(fair 私有,全局面见 [`../../07-debug.md`](../../07-debug.md)):

| tracepoint | 行号 | 触发点 |
|---|---|---|
| `trace_sched_stat_runtime` | 914 | `update_curr` 结算运行时间 |
| `trace_sched_stick_numa` | 2263/2272/2280 | NUMA 粘性决策 |
| `trace_sched_util_est_cfs_tp` | 4296/4312 | util 估计更新 |
| `trace_sched_util_est_se_tp` | 4413 | 实体 util 估计 |
| `trace_sched_overutilized_tp` | 6043 | 域过载(EAS) |
| `trace_sched_cpu_capacity_tp` | 8948 | CPU 算力更新(LB 段) |

schedstat 计数共 41 处,受 `schedstat_enabled`@897 门控:`exec_max`@901、`exec_clock`@906、wait/sleep/block 各分量@933/950/961/976/993/1011、`nr_spread_over`@4620。

## §7 RT 差异(本模块)

fair.c 内无 `#ifdef CONFIG_PREEMPT_RT` 直接条件编译块;RT 差异全经 `resched_curr_lazy()` 间接体现。落点:`4918`、`4942`(check_preempt_tick)、`5088`(entity_tick)、`5237`(HRTICK)、`7742`(check_preempt_wakeup)、`11918`、`11945`。定义 `core.c:1056`,声明 `kernel/sched/sched.h:2355`(非 RT 时为 inline stub@2357)。语义与影响见 [`../../05-concurrency.md`](../../05-concurrency.md) §7。

