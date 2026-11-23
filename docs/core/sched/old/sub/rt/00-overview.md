---
title: 总览
---

# sub/rt · 00 实时类原理速览(FIFO/RR)

> 一屏看懂 rt 类:它凭什么 O(1) 选任务、结构长什么样、几个关键函数在哪、多核怎么 push/pull、为什么要带宽节流。深入细节顺着 §6 指针进顶层各篇。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。代码:`kernel/sched/rt.c` + `kernel/sched/cpupri.c`。在优先级链上位于 dl 之下、fair 之上(见 [`../../00-overview.md`](../../00-overview.md) §3)。

## §1 核心思想:100 级静态优先级 + 位图 O(1) 选择

rt 类服务软实时任务(`SCHED_FIFO` / `SCHED_RR`)。与 fair 用 vruntime 算"谁最亏"不同,rt 的规则极简:**谁优先级数值高谁先跑**,同级里 FIFO 按到达顺序、RR 轮流跑一个时间片。

难点只有一个:100 个优先级里怎么 O(1) 找到"最高且非空"的那条队列?答案是一张**位图**——每个优先级一个 bit,置位表示该级有就绪任务,选任务时对位图做一次 `find_first_bit`(硬件指令级),直接定位最高非空级,再取该级队首。无论 100 级里塞了多少任务,选择恒定耗时。这是 rt 相对 fair(红黑树 O(log n))的本质区别:**牺牲公平,换确定性延迟**。

## §2 关键结构图:rt_rq = 位图 + 100 条队列

```
   struct rt_rq                         kernel/sched/sched.h:663   (内嵌于 rq.rt)
   ┌──────────────────────────────────────────────────────────────┐
   │ struct rt_prio_array active;                    :664           │
   │   ┌─────────────────────────────────────────────┐             │
   │   │ bitmap[]  ← 每优先级 1 bit,置位=该级非空    │  O(1) 选择  │
   │   │  99 98 97 ............................. 1 0  │  find_first │
   │   └─────────────────────────────────────────────┘   _bit      │
   │   │ struct list_head queue[100];                │             │
   │   │  queue[0]  → taskA → taskB   (同级 FIFO/RR) │             │
   │   │  queue[1]  → (空)                            │             │
   │   │  ...                                         │             │
   │   └──────────────────────────────────────────────┘            │
   │ rt_nr_running        :665   本队就绪 rt 任务数                 │
   │ rt_time / rt_runtime :  /686  已用时间 / 带宽预算(节流,§5)   │
   │ rt_runtime_lock      :688    raw_spinlock,护带宽账             │
   └──────────────────────────────────────────────────────────────┘
   注:优先级数值 = MAX_RT_PRIO-1-rt_priority,数值越小越优先(内部 0..99)。
       rt_prio_array 定义见 kernel/sched/sched.h:273。
```

## §3 关键函数(增删查改 + tick)

| 动作 | 函数 | 位置 | 做什么 |
|---|---|---|---|
| 入队 | `enqueue_task_rt` | `rt.c:1539` | 按优先级挂进 `queue[prio]`,置 `active` 位图对应 bit |
| 出队 | `dequeue_task_rt` | `rt.c:1555` | 摘出任务,该级空了则清 bit |
| 选任务 | `pick_next_task_rt` | `rt.c:1818` | 位图找最高非空级 → 取队首(O(1)) |
| tick | `task_tick_rt` | `rt.c:2647` | 仅 RR 递减时间片,耗尽则轮转到队尾并 resched;FIFO 不做时间片 |
| 记账 | `update_curr_rt` | `rt.c:1049` | 累计运行时间,喂给带宽节流(§5)与 PELT |
| 选核 | `select_task_rq_rt` | `rt.c:1603` | 唤醒时选一颗"能立即跑它"的 CPU(配合 §4) |

注册表 `DEFINE_SCHED_CLASS(rt)` 在 `rt.c:2692`。一次调度里这些回调如何被 core 驱动,见顶层 [`../../03-lifecycle.md`](../../03-lifecycle.md) §3。

## §4 push/pull 负载均衡(靠 cpupri 定位目标核)

rt 不做 fair 那种周期性域均衡,改用**即时 push/pull**,目标是"让最高优先级的 rt 任务尽快各占一颗 CPU":

- **push**(`push_rt_tasks` `rt.c:2181`):本 CPU 出现压不下的高优先级任务时,把它推给一颗正在跑更低优先级的 CPU。
- **pull**(`pull_rt_task` `rt.c:2359`,经 `balance_rt` `rt.c:1701` 在选任务前触发):本 CPU 快空了,主动从别的 CPU 拉一个高优先级 rt 任务过来,避免它在别处排队等待。

两者都要快速回答"哪颗 CPU 现在跑的优先级最低 / 能容纳这个优先级",这由 **cpupri**(每 root_domain 一个 CPU 优先级堆)支撑:`cpupri_find`(`cpupri.c:121`)/ `cpupri_find_fitness`(`cpupri.c:144`)查目标核,`cpupri_set`(`cpupri.c:210`)在 CPU 优先级变化时更新堆。跨模块登记见 [`../../04-interactions.md`](../../04-interactions.md) §2(RT↔TO 共享 `rq.rd.cpupri`)。

## §5 RT 带宽节流(防实时任务饿死普通任务)

rt 优先级恒高于 fair,一个死循环的 `SCHED_FIFO` 任务能让整颗 CPU 再也跑不了普通任务。为兜底,内核给 rt 设**带宽上限**:默认每个周期 1s(`sysctl_sched_rt_period = 1000000` us,`rt.c:19`)里 rt 最多累计跑 0.95s(`sysctl_sched_rt_runtime = 950000` us,`rt.c:25`),默认参数封装在 `def_rt_bandwidth`(`rt.c:13`)。

`update_curr_rt` 把运行时间累加到 `rt_rq->rt_time`,一旦 `sched_rt_runtime_exceeded`(`rt.c:1002`)判定超预算,就把整个 rt_rq **节流**(throttle)——本周期剩余时间让给 fair,直到下个周期补充预算再放行。`sched_rt_bandwidth_account`(`rt.c:720`)还被 DL 借用:DL 消耗的时间会计入 RT 带宽账(DL↔RT 强耦合,见 [`../../04-interactions.md`](../../04-interactions.md) §3.8)。调参入口见 [`../../06-tuning.md`](../../06-tuning.md) §5。

> RT 树(`-rt58`)差异:核心 rt 锁本就是 `raw_spinlock_t`,语义不变;实时性改动在**抢占时机**(普通任务的置位走 lazy 档),见 [`../../05-concurrency.md`](../../05-concurrency.md) §7。

## §6 深入指针

| 想深入 | 去哪 |
|---|---|
| rt 在一次调度里的完整流转 | [`../../03-lifecycle.md`](../../03-lifecycle.md) §3(逐跳交接) |
| rt 与 core/dl/cpupri/schedutil 的耦合 | [`../../04-interactions.md`](../../04-interactions.md) §2 矩阵、§3.8 DL↔RT |
| rt 锁与 RT 树抢占差异 | [`../../05-concurrency.md`](../../05-concurrency.md) §7 |
| RT 带宽调参、cgroup rt_runtime | [`../../06-tuning.md`](../../06-tuning.md) §5 |
| 与 fair 选任务思想的对照 | [`../fair/00-overview.md`](../fair/00-overview.md)、[`../../08-feynman.md`](../../08-feynman.md) |
| 官方文档 | `Documentation/scheduler/sched-rt-group.rst`、`sched-bwc.rst` |
