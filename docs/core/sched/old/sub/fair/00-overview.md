---
title: 总览
---

# fair · 00 子模块总览

> 本篇是 CFS 公平调度类子模块的入口。凡涉及 fair 与其他子模块关系的论断,一律上引顶层 [`../../04-interactions.md`](../../04-interactions.md),本篇只讲 fair 内部。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。主文件 `kernel/sched/fair.c:1-8008`(负载均衡段 8009-11760 属 load-balance 子模块,见 [`../../04-interactions.md`](../../04-interactions.md) §1)。

## §1 定位

fair 实现 `SCHED_NORMAL`/`SCHED_BATCH`/`SCHED_IDLE` 三种普通任务的调度,是系统里绝大多数任务的归宿。在 sched_class 优先级链 stop > dl > rt > **fair** > idle 中排第四,兜底之上、实时之下。

- 算法:完全公平调度(CFS)= 虚拟运行时间 `vruntime` + 每 cfs_rq 一棵红黑树,选最小 `vruntime` 的实体运行。
- 版本确认:本树 6.1 CFS 用 `min_vruntime` + `rb_root_cached`,尚无 EEVDF(EEVDF 是 6.6+;`sched_entity` 无 `deadline`/`vlag`/`slice` 字段,见 [`03-algorithm.md`](03-algorithm.md) §6)。

## §2 设计动机

CFS 要解决的核心问题:在没有固定时间片的前提下,让 N 个任务"看起来"同时以 1/N 速度前进。

- 手段:给每个任务记一个虚拟时间 `vruntime`,权重越高(nice 越小)`vruntime` 走得越慢;调度器永远挑 `vruntime` 最小的跑,自然把 CPU 匀给落后的任务。
- 与 O(1) 调度器的区别:不再有 140 个优先级队列和主动/过期数组切换,改用一棵红黑树按 `vruntime` 排序,取最左即最小,pick 为 O(1)、插删 O(log n)。
- 公平的粒度由 `sysctl_sched_latency`(目标调度周期)与 `sysctl_sched_min_granularity`(最小运行片)共同界定,机理见 [`03-algorithm.md`](03-algorithm.md) §4。

## §3 在顶层生命周期图占哪一段

对照 [`../../03-lifecycle.md`](../../03-lifecycle.md) 的状态机,fair 作为"接手方"承接以下跳:

| 顶层跳 # | 状态迁移 | fair 的职责 | 入口回调 |
|---|---|---|---|
| 跳 #2 | 新建 → runnable | 新任务入 CFS 红黑树 | `enqueue_task_fair` |
| 跳 #3 | sleeping → runnable | 唤醒任务入队 + 补偿 vruntime | `enqueue_task_fair` → `place_entity` |
| 跳 #4 | runnable → running | 从红黑树选最左实体 | `pick_next_task_fair` |
| 跳 #7 | running → sleeping | 出队(`DEQUEUE_SLEEP`) | `dequeue_task_fair` |
| 跳 #10 | tick 抢占前置 | 判时间片,置 need_resched(lazy) | `task_tick_fair` → `check_preempt_tick` |

fair 不参与跳 #1(fork 定类,core 职责)、#5(上下文切换)、#8(回收)。跳 #9(迁移)fair 提供 `migrate_task_rq_fair`,细节属 load-balance。

## §4 本子模块 API

sched_class 回调(注册在 `DEFINE_SCHED_CLASS(fair)`@fair.c:12443):

| 回调 | 实现函数 | 行号区间 | 一句话职责 |
|---|---|---|---|
| `enqueue_task` | `enqueue_task_fair` | 6093-6177 | 实体入 cfs_rq 红黑树,逐层向上 |
| `dequeue_task` | `dequeue_task_fair` | 6186-6254 | 实体出队,逐层向上 |
| `pick_next_task` | `pick_next_task_fair` | 7793-7934 | 取最左实体,处理组调度下溯 |
| `put_prev_task` | `put_prev_task_fair` | 7944-7953 | 把被切走的实体放回队列 |
| `set_next_task` | `set_next_task_fair` | 12100-12121 | 设定新选中实体为 curr |
| `task_tick` | `task_tick_fair` | 11870-11887 | tick 驱动 `entity_tick` 判抢占 |
| `check_preempt_curr` | `check_preempt_wakeup` | 7662-7757 | 唤醒时判是否抢占当前任务 |
| `update_curr` | `update_curr` | 882-920 | 累加运行时间到 vruntime |
| `task_fork` | `task_fork_fair` | — | fork 时初始化子任务 vruntime |

内部关键函数(非回调,内部剖析见 [`02-internals.md`](02-internals.md)):`place_entity`@4648、`__enqueue_entity`@628、`__dequeue_entity`@633、`__pick_first_entity`@638、`update_min_vruntime`@592、`entity_tick`@5069。

## §5 与其他子模块的接缝(只给指针,不展开)

| 关系 | 去哪篇看 |
|---|---|
| fair → pelt-load(喂负载信号) | [`../../04-interactions.md`](../../04-interactions.md) §3.2 |
| fair → schedutil(触发调频) | [`../../04-interactions.md`](../../04-interactions.md) §3.4 |
| fair → topology(选核读域) | [`../../04-interactions.md`](../../04-interactions.md) §3.6(LB 段) |
| fair ↔ core(分派 + resched) | [`../../04-interactions.md`](../../04-interactions.md) §3.1、§6 |
| fair 共享 `rq.curr`/`cfs.avg` 字段 | [`../../02-objects.md`](../../02-objects.md) §2.4、§2.6 |
| fair 抢占的 RT lazy 差异 | [`../../05-concurrency.md`](../../05-concurrency.md) §7 |

## §6 本篇边界

本篇不展开:vruntime 数学(→ [`03-algorithm.md`](03-algorithm.md))、逐段代码流程(→ [`02-internals.md`](02-internals.md))、任何跨模块关系(→ 顶层 04)。load-balance(fair.c:8009+)虽同文件但属独立子模块,不在本篇。
