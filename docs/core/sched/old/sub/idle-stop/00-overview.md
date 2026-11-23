---
title: 总览
---

# sub/idle-stop · 00 两端退化类原理速览(stop / idle)

> 一屏看懂优先级链的两个端点:最高的 stop 和最低的 idle。它们都"退化"——几乎没有排队算法,却各自扮演不可替代的角色:stop 是内核抢占一切的特权通道,idle 是永不落空的兜底。深入顺着 §5 指针进顶层各篇。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。代码:`kernel/sched/stop_task.c` + `kernel/sched/idle.c`。分列优先级链两端(见 [`../../00-overview.md`](../../00-overview.md) §3)。

## §1 核心思想:为什么要有两个"没有算法"的类

调度器需要两个"确定性锚点"来夹住那三个真正做决策的类(dl/rt/fair):

- **上锚点 stop**——内核偶尔需要"立刻、无条件"占用某颗 CPU 做一件不能被打断的事(把任务从 A 核搬到 B 核、CPU 热插拔下线)。这件事必须压过包括 dl 在内的所有用户任务。于是设一个凌驾一切的 stop 类,它一就绪,`__schedule` 第一个就选中它。
- **下锚点 idle**——`pick_next_task` 沿链往下问,必须保证**总有人应答**,否则 CPU 无事可跑就"悬空"了。idle 类永远有一个 `swapper/N` 空闲线程可选,`pick_next_task` 因此永不返回空。

两者都"退化"到每 CPU 至多一个任务、无需排队结构——因为它们要的是**确定性**,不是公平或吞吐。

## §2 stop:最高优先级的特权通道

```
   优先级链顶端      stop_task.c
   ┌────────┐  一就绪即被选中,抢占 dl/rt/fair/idle 全部
   │  stop  │  载体:每 CPU 的 migration/N 内核线程(cpu_stopper)
   └────────┘  用途:任务迁移 active_balance、CPU 热插拔、stop_machine
```

- 选任务 `pick_next_task_stop`(`stop_task.c:44`):有 stopper 任务待跑就返回它,否则返回 NULL 让位给 dl。无位图、无红黑树——至多一个任务。
- 记账 `update_current_exec_runtime`(`stop_task.c:84`):只累计运行时间,不参与任何均衡或节流。
- 注册 `DEFINE_SCHED_CLASS(stop)`(`stop_task.c:117`),位于链接段最前 = 最高优先级。
- 谁来唤醒它:负载均衡的 active_balance 通过 `stop_one_cpu` 触发迁移(见 [`../../04-interactions.md`](../../04-interactions.md) §3 欠债表 LB→CO)。因为它抢占一切,持有它的临界区必须极短。

## §3 idle:最低优先级的兜底与省电入口

```
   优先级链底端      idle.c
   ┌────────┐  仅当 dl/rt/fair 全空才被选中
   │  idle  │  载体:每 CPU 的 swapper/N 线程
   └────────┘  被选中后进 idle loop → 选 C-state 省电,等中断/唤醒
```

- 选任务 `pick_next_task_idle`(`idle.c:447`):无条件返回本 CPU 的 idle 任务(`rq->idle`)。这是 `pick_next_task` 的最终兜底(顶层 [`../../00-overview.md`](../../00-overview.md) §4 的 t2 "全空 → idle")。
- idle loop `do_idle`(`idle.c:258`):被选中后不是空转,而是进入 cpuidle 选一个 C-state 让 CPU 睡下去省电,直到中断或有任务唤醒才退出、触发重调度。
- 顺带维护 SMT idle core 追踪 `update_idle_core`(`idle.c:436`),供 fair 唤醒选核时判断"哪颗核整核空闲"(fair↔idle 轻耦合,见 [`../../04-interactions.md`](../../04-interactions.md) §3 欠债表)。
- 注册 `DEFINE_SCHED_CLASS(idle)`(`idle.c:499`),链接段最后 = 最低优先级。

> 别混淆两个 "idle":这里的 idle **类**(挑中 swapper 线程)与 idle **loop**(选 C-state 省电)是两件事——类负责"选中谁",loop 负责"选中后干嘛"。

## §4 关键函数一览

| 类 | 动作 | 函数 | 位置 |
|---|---|---|---|
| stop | 选任务 | `pick_next_task_stop` | `stop_task.c:44` |
| stop | 记账 | `update_current_exec_runtime` | `stop_task.c:84` |
| stop | 注册 | `DEFINE_SCHED_CLASS(stop)` | `stop_task.c:117` |
| idle | 选任务 | `pick_next_task_idle` | `idle.c:447` |
| idle | idle loop | `do_idle` | `idle.c:258` |
| idle | SMT 追踪 | `update_idle_core` | `idle.c:436` |
| idle | 注册 | `DEFINE_SCHED_CLASS(idle)` | `idle.c:499` |

> RT 树(`-rt58`)差异:两类本身几乎不受 RT 补丁影响;真正相关的是抢占时机与 idle 退出路径,见 [`../../05-concurrency.md`](../../05-concurrency.md) §7。

## §5 深入指针

| 想深入 | 去哪 |
|---|---|
| idle 作为 pick 兜底、stop 在切换里的位置 | [`../../03-lifecycle.md`](../../03-lifecycle.md) §3、[`../../00-overview.md`](../../00-overview.md) §4 |
| stop 被 active_balance 触发、idle↔fair 选核耦合 | [`../../04-interactions.md`](../../04-interactions.md) §3 欠债表 |
| idle loop 与 RT 抢占、nohz | [`../../05-concurrency.md`](../../05-concurrency.md) §7、[`../../07-debug.md`](../../07-debug.md) |
| 优先级链整体框架 | [`../../00-overview.md`](../../00-overview.md) §3 |
