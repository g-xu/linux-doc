# deadline · 02 内部剖析

> 本篇深挖 DL 类的**实现细节**:CBS 记账为什么要做频率/算力缩放、GRUB 怎么回收空闲带宽、"活跃带宽"(running_bw/this_bw)与 **0-lag 非连续态定时器**这套连 DL 老手都容易讲漏的状态机、CBS 原始规则 vs 修订规则的密度判据、以及准入与迁移的带宽账本。最难的几段贴真源码逐句讲。算法层的不变式/复杂度/参数在 [`03-algorithm.md`](03-algorithm.md);跨模块关系上引 [`../../04-interactions.md`](../../04-interactions.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。行号指 `kernel/sched/deadline.c`(除注明)。RT 差异见 §10。

## §1 核心数据结构:三参数 + 两套带宽

| 结构/字段 | 作用 | 定义位置 |
|---|---|---|
| `struct sched_dl_entity` | DL 实体,三参数 + 运行时状态 | `include/linux/sched.h:601` |
| `dl_runtime/dl_deadline/dl_period` | CBS 配置:预算 / 相对截止 / 周期 | 同上 |
| `dl_se->runtime`(剩余) / `deadline`(绝对) | 运行时递减预算 / 当前绝对截止 | 同上 |
| `dl_se->dl_throttled/dl_yielded/dl_non_contending/dl_overrun` | 状态标志位 | 同上 |
| `struct dl_rq` | 每 CPU DL 就绪队列 | `kernel/sched/sched.h:704` |
| `dl_rq->root`(rb_root_cached) | 按**绝对 deadline** 排序,最左=最紧急 | `kernel/sched/sched.h`(dl_rq 内) |
| `dl_rq->running_bw` | **活跃带宽**:此刻真在 contending 的实体带宽和 | `kernel/sched/sched.h`(dl_rq 内) |
| `dl_rq->this_bw` | **保留带宽**:入队在此 rq 的实体带宽和(含非连续态) | 同上 |
| `struct dl_bw` | 每 root domain 的准入账本 `total_bw` | `kernel/sched/sched.h:318` |

**两套带宽是 DL 实现的关键分层**:`this_bw` 是"逻辑上属于这个 rq 的带宽"(任务只要没彻底离开就一直算),`running_bw` 是"此刻真在争用 CPU 的带宽"。二者的差 `this_bw - running_bw` = 睡着但带宽尚未释放的部分,正是 GRUB 回收算法(§7)的输入。它们的恒等关系(`running_bw ≤ this_bw ≤ total_bw`)是不变式,见 [`03-algorithm.md`](03-algorithm.md) §1.2。

## §2 入队 `enqueue_task_dl`(1672-1710)

入队三件事:定/刷 deadline、按 deadline 插树、维护带宽账。核心决策链:

| 步 | 代码 | 决策 |
|---|---|---|
| 1 | `enqueue_task_dl`@1672 | 区分:PI-boost 恢复 / 限流恢复 / 普通唤醒,分派不同 flags |
| 2 | 若从非连续态唤醒 | 取消 `inactive_timer`,`running_bw` 加回来(见 §6) |
| 3 | `update_dl_entity`@1018 | **CBS 唤醒判定**:溢出则重置 deadline/runtime(§5) |
| 4 | `dl_check_constrained_dl`@1243 | 受约束任务(deadline<period)的截止修正 |
| 5 | `enqueue_dl_entity`@1641→`__enqueue_dl_entity`@1616 | `rb_add_cached` 按绝对 deadline 插树 |
| 6 | `inc_dl_tasks`@1508 | `dl_nr_running++`;更新 cpudeadline 全局堆 |

## §3 出队 `dequeue_task_dl`(1763)与"带宽不随出队立即释放"

出队最反直觉的一点:**任务出队(睡眠)时带宽不立即从 `running_bw` 扣掉**,而是走 §6 的 0-lag 非连续态逻辑延迟释放。`dequeue_task_dl` 里:

```c
static void dequeue_task_dl(struct rq *rq, struct task_struct *p, int flags)
{
    update_curr_dl(rq);              // 先结算
    __dequeue_task_dl(rq, p);        // 从红黑树摘 + dec_dl_tasks
    if (p->on_rq == TASK_ON_RQ_MIGRATING || flags & DEQUEUE_SAVE)
        return;                      // 迁移/临时保存:不动带宽
    if (flags & DEQUEUE_SLEEP)
        task_non_contending(&p->dl); // ★ 睡眠:进入"非连续态",带宽延迟到 0-lag 时刻释放
}
```

为什么不立即释放?因为 CBS 的带宽保证是按 deadline 分摊的:任务在 deadline 之前提前睡了,它"预定"的那份带宽在 0-lag 时刻之前理论上仍可能被它用掉(它可能马上又醒)。立即释放会让别的任务超额准入,破坏可调度性。这就是 GRUB/0-lag 机制存在的根本原因。

## §4 选下一个 `pick_next_task_dl`(2033-2042)

```
if !dl_rq->dl_nr_running: return NULL
dl_se = rb_first_cached(&dl_rq->root)   // 最左=最早绝对 deadline,O(1)
p = dl_task_of(dl_se)
set_next_task_dl(rq, p)                  // 起 exec_start;若无 inactive_timer 在跑则按需 arm
return p
```

EDF 的"选最早 deadline"退化成读红黑树最左缓存,O(1)(复杂度见 [`03-algorithm.md`](03-algorithm.md) §2)。

## §5 CBS 记账 `update_curr_dl`(1309-1408)——DL 最重的函数

它做四件事:算增量、**按频率/算力缩放(或 GRUB 回收)**、扣预算判超支、**反向记账 RT 带宽**。最硬的是缩放这段:

```c
delta_exec = update_curr_common(rq);            // now - exec_start
...
if (dl_se->flags & SCHED_FLAG_RECLAIM) {
    scaled_delta_exec = grub_reclaim(delta_exec, rq, &curr->dl);  // GRUB:按空闲带宽放慢消耗
} else {
    int cpu = cpu_of(rq);
    unsigned long scale_freq = arch_scale_freq_capacity(cpu);     // 当前频率/额定
    unsigned long scale_cpu  = arch_scale_cpu_capacity(cpu);      // 本核算力/最强核
    scaled_delta_exec = cap_scale(cap_scale(delta_exec, scale_freq), scale_cpu);
}
dl_se->runtime -= scaled_delta_exec;            // 扣的是"归一化"后的时间
```

**为什么缩放?** CBS 的 `runtime` 是按"满频满算力"标定的预算。若 CPU 降频到 50% 跑了 1ms,实际只完成了 0.5ms 满速的工作量,只应扣 0.5ms 预算——否则降频会凭空吃掉 DL 任务的保证。`cap_scale(x, s) = (x * s) >> SCHED_CAPACITY_SHIFT`,两次缩放先后除以频率系数、算力系数。这是 DL 与 RT 的一个根本差异:**RT 不缩放(直接扣墙钟),DL 缩放**(RT 无带宽保证语义,只有节流兜底)。

超支与限流(1370 起):

```c
if (dl_runtime_exceeded(dl_se) || dl_se->dl_yielded) {   // runtime <= 0
    dl_se->dl_throttled = 1;
    if (unlikely(is_dl_boosted(dl_se) || !start_dl_timer(p)))
        ...                     // boosted 或定时器没起来:立刻 replenish 兜底
    __dequeue_task_dl(rq, p);   // 停跑,让位更晚 deadline 或其他类
}
```

**反向记账 RT 带宽(1395-1406,DL↔RT 耦合的落点):**

```c
if (rt_bandwidth_enabled()) {
    struct rt_rq *rt_rq = &rq->rt;
    raw_spin_lock(&rt_rq->rt_runtime_lock);
    if (sched_rt_bandwidth_account(rt_rq))
        rt_rq->rt_time += delta_exec;    // DL 消耗计入 RT 的已耗时间
    raw_spin_unlock(&rt_rq->rt_runtime_lock);
}
```

含义:DL 任务跑掉的时间,会算进 RT 的节流预算 `rt_time`。这就是"DL 挤压 RT 带宽"的机制原点——DL 收得多,RT 的 `rt_time` 涨得快、更早被节流。内核注释在此处提到未来要用"deadline servers"替代这种耦合。完整论证见 [`../../04-interactions.md`](../../04-interactions.md) §3(dl×rt)。

## §6 0-lag 与非连续态定时器:DL 最容易被讲漏的机制

一个 DL 任务在 deadline 之前就睡了,它"预定"的带宽不能立刻释放(§3)。DL 用一个三态机 + 一个 `inactive_timer` 精确地在 **0-lag 时刻**(此任务的历史欠账恰好归零的时刻)释放带宽:

```
   ACTIVE(contending)  ── 任务睡眠 dequeue_task_dl ──►  ACTIVE(non-contending)
        ▲                                                      │
        │  唤醒 task_contending                                 │ inactive_timer 到点
        │  (0-lag 未到,取消定时器,带宽已在账上)                 │ 或 0-lag 已过
        └──────────────────────────────────────────────  INACTIVE
                              (running_bw 扣除 dl_bw)
```

**0-lag 时刻怎么算(`task_non_contending`@392,真源码):**

```c
zerolag_time = dl_se->deadline -
     div64_long((dl_se->runtime * dl_se->dl_period),
                dl_se->dl_runtime);           // = deadline - runtime*period/runtime_conf
zerolag_time -= rq_clock(rq);                 // 转成"从现在起还有多久"

if ((zerolag_time < 0) || hrtimer_active(&dl_se->inactive_timer)) {
    sub_running_bw(dl_se, dl_rq);             // 0-lag 已过:立即释放活跃带宽
    ...
    return;
}
dl_se->dl_non_contending = 1;                 // 否则进入非连续态
get_task_struct(p);
hrtimer_start(timer, ns_to_ktime(zerolag_time), HRTIMER_MODE_REL_HARD);  // arm 到 0-lag
```

**0-lag 公式的含义**:`deadline - runtime*period/dl_runtime`——按任务的带宽比例 `dl_runtime/period` 把"剩余预算 runtime"折算成一段时间,从 deadline 往回推。到这个时刻,任务即便一直没跑,它对系统的带宽占用也已"名正言顺地"耗尽,才可以安全地从 `running_bw` 里扣掉。提前扣会让准入超收,推迟扣会浪费带宽。

**三个落点:**
- **睡眠**:`dequeue_task_dl`(§3)→ `task_non_contending`@392,arm `inactive_timer`,置 `dl_non_contending=1`。带宽**留在** `running_bw`。
- **0-lag 到点**:`inactive_task_timer`@1410 回调 → `sub_running_bw` + 若任务已不是 DL 还 `sub_rq_bw`/`__dl_sub`,状态转 INACTIVE。
- **非连续态中途唤醒**:`task_contending`@448 → 取消 `inactive_timer`,`dl_non_contending=0`;带宽从没离开过 `running_bw`,所以无需重新加(除非已转 INACTIVE 才 `add_running_bw`)。

**竞态 `dl_non_contending` 标志**:定时器"已触发但还没拿到 rq 锁"与"任务此时又醒了"会赛跑。代码在多处(如 288-334 的 `__sub_*` 路径、448 的 contending 路径)用 `dl_non_contending` + `hrtimer_active`/`hrtimer_try_to_cancel` 的返回值判断到底谁先到,避免带宽被加/减两次。这是 DL 并发中最细的一处,`inactive_timer` 无法取消时靠回调自己收尾。

## §7 GRUB:回收空闲带宽 `grub_reclaim`(1283-1303)

普通 DL 任务用固定预算;带 `SCHED_FLAG_RECLAIM` 的任务可以**回收系统里没被用掉的带宽**跑得更久(GRUB = Greedy Reclamation of Unused Bandwidth)。§5 的缩放分支里,`grub_reclaim` 取代了频率/算力缩放:

```c
static u64 grub_reclaim(u64 delta, struct rq *rq, struct sched_dl_entity *dl_se)
{
    u64 u_inact = rq->dl.this_bw - rq->dl.running_bw;   /* 睡着但未释放的带宽 = Utot - Uact */
    u64 u_act;
    u64 u_act_min = (dl_se->dl_bw * rq->dl.bw_ratio) >> RATIO_SHIFT;
    ...
    u_act = ...;                     // 有效活跃带宽 = max(Uact, u_act_min),再夹在容量内
    return (delta * u_act) >> BW_SHIFT;   // 按 u_act 比例"放慢"预算消耗
}
```

直觉:系统越空(`u_inact` 越大 → `u_act` 越小),回收型任务消耗预算越慢,等于免费蹭到空闲 CPU;但下界 `u_act_min` 保证它至少按自己的配额消耗,不会把别人的保证吃掉。这正是 §1 里 `this_bw` 与 `running_bw` 分层的用途——两者之差就是可回收的空闲量。

## §8 CBS 唤醒:原始规则 vs 修订规则 `update_dl_entity`(1018-1035)

任务唤醒时要决定:沿用旧 deadline/runtime,还是重置?判据是 `dl_entity_overflow`——**密度检查**:

```c
// dl_entity_overflow@909:剩余预算相对剩余时间是否"太满"?
left  = (dl_se->dl_deadline >> DL_SCALE) * (dl_se->runtime >> DL_SCALE);
right = ((dl_se->deadline - t) >> DL_SCALE) * (dl_se->dl_runtime >> DL_SCALE);
return dl_time_before(right, left);   // right<left ⟹ 溢出,需要新 deadline
```

交叉相乘避免除法,等价判断 `runtime/(deadline-t) > dl_runtime/dl_deadline`,即"要在剩余时间里花掉剩余预算,所需带宽超过了配置带宽"。溢出说明旧 deadline 太紧,继续用会抢占过度,于是:

- **原始 CBS 规则**:`deadline = now + dl_deadline`,`runtime = dl_runtime`(彻底重置,`update_dl_entity`@1018 的默认分支)。
- **修订规则 `update_dl_revised_wakeup`@957**(受约束任务 deadline<period):不整段重置,而是按"密度"给一个能保持带宽不超的新预算——

```c
// update_dl_revised_wakeup 核心:laxity = deadline - now;新 runtime 按密度折算
laxity = dl_se->deadline - rq_clock(rq);
dl_se->runtime = (dl_se->dl_density * laxity) >> BW_SHIFT;   // dl_density = dl_runtime/dl_deadline
```

修订规则解决的是:受约束任务(相对 deadline 小于周期)如果用原始规则,可能拿到超过其密度允许的预算,短时间内挤占过多。见 [`03-algorithm.md`](03-algorithm.md) §1 的 CBS 讨论。

## §9 周期补充与 `dl_task_timer` 竞态(1110-1215)

`dl_task_timer` 是限流任务的"周期到点补预算"回调,核心是 `replenish_dl_entity`@837:补满 `runtime = dl_runtime`、`deadline += dl_period`。但它有一处关键竞态处理:

```c
// dl_task_timer 内:定时器到点时任务可能已被别处出队
if (!dl_task(p) || !dl_se->dl_throttled)
    goto unlock;                    // 已不是 DL / 已不再限流:啥也不做
...
if (unlikely(!rq->online) || task_on_rq_migrating) {
    replenish_dl_entity(dl_se);     // 只补预算,不入队(任务不在本 rq)
    goto unlock;
}
// 正常:补预算 + enqueue_task_dl + 若该抢占则 resched
```

即:补充与"任务当前是否还在本队列/还需要限流"必须原子地一起判断,否则会把一个已经迁移走或已醒的任务错误地重新塞回队列。

## §10 准入与迁移的带宽账本

准入(`sched_dl_overflow`@2865,经 `core.c:7782` 由 `sched_setattr` 触发)判据 `__dl_overflow`@227:

```c
// 新任务能否收:考虑算力缩放后的总带宽不超容量
cap_scale(dl_b->bw, cap) < dl_b->total_bw - old_bw + new_bw   // 成立即拒绝(-EBUSY)
```

迁移时带宽在两个 rq 的账本间搬:`__dl_sub`@213 从源 rq 的 `total_bw` 减、`__dl_add`@220 给目标加;运行时活跃带宽用 `__add_running_bw`@234 / `__sub_running_bw`@247、rq 保留带宽用 `__add_rq_bw`@261 / `__sub_rq_bw`@271。`__add_running_bw` 每次改动都会 `cpufreq_update_util` 踢一下调频器——因为 DL 活跃带宽是 schedutil 计算 CPU 频率需求的输入之一(见 [`../../04-interactions.md`](../../04-interactions.md) §3.3)。

## §11 本子模块专属可观测点

- **/proc/sched_debug**:`print_dl_rq`@`kernel/sched/debug.c:693` 打印 `dl_nr_running`;分派 `print_dl_stats`@debug.c:771→`deadline.c:3124`。**`running_bw`/`this_bw` 可在此读到**,是判断带宽是否被非连续态"卡住"的关键。
- **限流可见性**:`dl_se->dl_throttled=1` 且 `runtime≤0` = 预算耗尽等补充;`dl_non_contending=1` = 睡眠但带宽尚未释放(0-lag 未到)。
- tracepoint 复用通用 `sched_switch`/`sched_wakeup`(DL 无独占,见 [`../../07-debug.md`](../../07-debug.md))。

## §12 RT 差异(本模块)

- **抢占置位**:DL 抢占走 `resched_curr`,与 RT 同属实时,即时抢占普通任务;PREEMPT_RT 的 lazy 变体不作用于 DL。取证 [`../../05-concurrency.md`](../../05-concurrency.md) §7。
- **锁**:`dl_bw->lock`@sched.h:319、`cpudl.lock`@cpudeadline.h:12 本就是 `raw_spinlock_t`,RT 下语义不变。
- **定时器**:`dl_task_timer`(§9)、`inactive_timer`(§6)均为 hrtimer,`inactive_timer` 用 `HRTIMER_MODE_REL_HARD`(硬中断上下文触发),RT 下不降级为软中断,保证 0-lag 释放的时序精度。
