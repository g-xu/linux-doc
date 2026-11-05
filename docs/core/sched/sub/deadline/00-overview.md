---
title: 总览
---

# sub/deadline · 00 截止期类原理速览(EDF + CBS)

> 一屏看懂 dl 类:三参数模型是什么、凭什么保证"到点必跑"、结构长什么样、关键函数在哪、多核怎么 push/pull、准入控制为什么必须存在。深入顺着 §6 指针进顶层各篇。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。代码:`kernel/sched/deadline.c` + `kernel/sched/cpudeadline.c`。在优先级链上位于 stop 之下、rt 之上——是**用户可用的最高实时类**(见 [`../../00-overview.md`](../../00-overview.md) §3)。

## §1 核心思想:三参数 + EDF 选择 + CBS 隔离 + 准入把关

dl 类(`SCHED_DEADLINE`)服务硬实时:任务不声明优先级,而是声明一份**时间契约**——三个参数:

- `runtime`:每个周期需要的 CPU 时间;
- `deadline`:相对截止期,必须在此之前拿到 runtime;
- `period`:契约重复的周期。

在此之上叠三件事,共同保证"到点必跑":

1. **EDF(最早截止期优先)** 决定"选谁跑":谁的绝对截止期最近就先跑。这被证明在单核上是最优的——只要一组任务可调度,EDF 就能让它们都不误期。
2. **CBS(恒定带宽服务器)** 决定"能跑多久":给每个任务一个不断消耗的 runtime 预算,超支就把它的截止期推后(降级),从而**隔离**——一个任务超时不会拖垮别的任务。
3. **准入控制(admission)** 决定"能不能加进来":新任务的带宽会不会让总需求超过 CPU 容量?超了就直接拒绝(§5)。没有这道关,EDF 的"最优"前提(总带宽 ≤ 容量)就不成立。

一句话:**EDF 排序 + CBS 限量 + 准入设防**,缺一不可。

## §2 关键结构图:契约在实体里,红黑树按截止期排

```
   struct sched_dl_entity              include/linux/sched.h:601   (task 的 dl 契约)
   ┌──────────────────────────────────────────────┐
   │ dl_runtime / dl_deadline / dl_period          │  ← 用户声明的三参数
   │ runtime      剩余预算(CBS 递减,≤0 则降级)   │
   │ deadline     当前绝对截止期(EDF 排序键)      │
   └───────────────┬────────────────────────────────┘
                   │ 按 deadline 入树
                   ▼
   struct dl_rq                        kernel/sched/sched.h:704   (内嵌于 rq.dl)
   ┌──────────────────────────────────────────────┐
   │ root  红黑树(rb_root_cached)                 │  最左 = 最早 deadline
   │   ┌── 截止期近 ──────────── 截止期远 ──┐       │  → pick 直接取最左 O(1)
   │   │  A(t=10) → B(t=25) → C(t=90)      │       │
   │   └────────────────────────────────────┘       │
   │ dl_nr_running   :708   本队 dl 任务数           │
   └──────────────────────────────────────────────┘

   struct dl_bw (每 root_domain 一个)  kernel/sched/sched.h:318   ← 准入用
   ┌──────────────────────────────────────────────┐
   │ bw / total_bw   容量 / 已承诺带宽之和(§5)    │  dl_bw->lock (raw)
   └──────────────────────────────────────────────┘
   全局默认带宽参数 struct dl_bandwidth 见 kernel/sched/sched.h:289。
```

## §3 关键函数(增删查改 + tick)

| 动作 | 函数 | 位置 | 做什么 |
|---|---|---|---|
| 入队 | `enqueue_task_dl` | `deadline.c:1672` | 按绝对 deadline 插入红黑树 |
| 出队 | `dequeue_task_dl` | `deadline.c:1763` | 摘出实体 |
| 选任务 | `pick_next_task_dl` | `deadline.c:2033` | 取红黑树最左(最早截止期) |
| tick | `task_tick_dl` | `deadline.c:2067` | 更新记账,判是否需重调度 |
| 记账 | `update_curr_dl` | `deadline.c:1309` | 递减 runtime 预算(CBS 核心);耗尽则降级/节流 |
| 选核 | `select_task_rq_dl` | `deadline.c:1829` | 唤醒时选能满足其截止期的 CPU |

注册表 `DEFINE_SCHED_CLASS(dl)` 在 `deadline.c:2738`。回调如何被 core 驱动,见顶层 [`../../03-lifecycle.md`](../../03-lifecycle.md) §3。

## §4 push/pull 负载均衡(靠 cpudeadline 定位目标核)

与 rt 类似,dl 也用**即时 push/pull** 而非周期均衡,目标是"让截止期最紧的任务尽快拿到 CPU":

- **push**(`push_dl_task` `deadline.c:2324` / `push_dl_tasks` `:2402`):本 CPU 有排不上的紧任务,推给一颗截止期较宽松的 CPU。
- **pull**(`pull_dl_task` `deadline.c:2409`,经 `balance_dl` `deadline.c:1931` 在选任务前触发):本 CPU 要空了,从别处拉一个紧任务过来。

定位目标核由 **cpudeadline**(每 root_domain 一个"最晚截止期"堆)支撑:`cpudl_find`(`cpudeadline.c:116`)找一颗其当前任务截止期最晚(最该被抢)的 CPU,`cpudl_set`(`cpudeadline.c:213`)在 CPU 状态变化时更新堆。跨模块登记见 [`../../04-interactions.md`](../../04-interactions.md) §2。

## §5 准入控制与 CBS 带宽(EDF 成立的前提)

**准入**:改任务为 `SCHED_DEADLINE`(或改参数)时,`sched_dl_overflow`(`deadline.c:2865`,由 syscall 路径 `core.c:7782` 调用)先算这个任务的带宽 `runtime/period`,再用 `__dl_overflow`(`deadline.c:227`)检查:加上它之后,root_domain 上所有 dl 任务的**总带宽是否超过 CPU 容量**?超了就返回 `-EBUSY`,拒绝。这道关跨 CPU 共享 `dl_bw`(`kernel/sched/sched.h:318`,`root_domain` 内,`kernel/sched/sched.h:822`),用 `dl_bw->lock` 保护。全局默认带宽由 `init_dl_bandwidth`(`deadline.c:494`)初始化,并借用 RT 的全局参数作上界(见下)。

**CBS**:准入只保证总量不超,运行期还要防单个任务超支——`update_curr_dl`(`deadline.c:1309`)持续递减 `runtime`,归零就把该任务的 deadline 往后推(降级为下一周期),让出 CPU。这样单任务的 bug 不会击穿别的任务的保证。

**与 RT 的耦合**:DL 优先级高于 RT,DL 消耗的时间会计入 RT 带宽账(挤压),同时 DL 总带宽上界借用 RT 全局参数——一处强耦合,详见 [`../../04-interactions.md`](../../04-interactions.md) §3.8,大白话见 [`../../08-feynman.md`](../../08-feynman.md)。调参入口见 [`../../06-tuning.md`](../../06-tuning.md) §1.1。

> RT 树(`-rt58`)差异:dl 核心锁均为 `raw_spinlock_t`,语义不变;差异在抢占时机,见 [`../../05-concurrency.md`](../../05-concurrency.md) §7。

## §6 深入指针

| 想深入 | 去哪 |
|---|---|
| dl 在一次调度里的完整流转 | [`../../03-lifecycle.md`](../../03-lifecycle.md) §3 |
| DL↔RT 带宽挤压 + 准入耦合 | [`../../04-interactions.md`](../../04-interactions.md) §3.8、§6 依赖环 |
| dl 锁与 RT 树抢占差异 | [`../../05-concurrency.md`](../../05-concurrency.md) §7 |
| DL 带宽/准入调参 | [`../../06-tuning.md`](../../06-tuning.md) §1.1 |
| "DL 吃 RT 带宽"的大白话 | [`../../08-feynman.md`](../../08-feynman.md) |
| 官方文档 | `Documentation/scheduler/sched-deadline.rst` |
