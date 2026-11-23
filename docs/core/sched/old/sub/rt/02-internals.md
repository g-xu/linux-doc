# rt · 02 内部剖析

> 本篇深挖 RT 类的**实现细节**:入/出队为什么要"拆栈重挂"、位图怎么与链表严格同步、节流不只是"停跑"而是一整套跨 CPU 借带宽 + hrtimer 补充的机制。最难的几段直接贴真源码逐句讲;其余用精简伪代码。算法层的复杂度/不变式/参数在 [`03-algorithm.md`](03-algorithm.md);跨模块关系上引 [`../../04-interactions.md`](../../04-interactions.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。行号指 `kernel/sched/rt.c`(除注明)。RT 差异见 §9。

## §1 核心数据结构与两个"实体视角"

| 结构 | 作用 | 定义位置 |
|---|---|---|
| `struct rt_rq` | 每 CPU(每 task_group)一个 RT 就绪队列 | `kernel/sched/sched.h:663` |
| `rt_rq->active`(`rt_prio_array`) | 100 条 FIFO 链 + `MAX_RT_PRIO+1` 位的位图 | `kernel/sched/sched.h:664` |
| `rt_rq->rt_nr_running` / `rt_nr_boosted` | 可运行数 / 被 PI 提升数(节流豁免用) | `kernel/sched/sched.h:665` |
| `rt_rq->highest_prio.curr` | 本队列当前最高优先级(cpupri 输入) | `kernel/sched/sched.h`(highest_prio 段) |
| `rt_rq->rt_time` / `rt_runtime` | 本周期已耗 / 可用预算 | `kernel/sched/sched.h:686` |
| `rt_rq->rt_throttled` | 本队列本周期是否已被限流 | `kernel/sched/sched.h`(rt_throttled 段) |
| `rt_rq->rt_runtime_lock` | 保护上面三个时间字段的 raw 锁 | `kernel/sched/sched.h:688` |
| `struct sched_rt_entity` | RT 实体,含 `run_list`/`on_list`/`on_rq`/`back`/`time_slice` | `include/linux/sched.h`(rt 段) |

**关键前提:`rt_se_prio` 对组实体返回的是"组内最高优先级"**(`rt.c:990-1000`):

```c
static inline int rt_se_prio(struct sched_rt_entity *rt_se)
{
    struct rt_rq *rt_rq = group_rt_rq(rt_se);   // 该实体自己带的子队列
    if (rt_rq)
        return rt_rq->highest_prio.curr;         // 组实体:取子队列最高优先级
    return rt_task_of(rt_se)->prio;              // 任务实体:取任务静态优先级
}
```

这一行决定了下面所有"拆栈重挂"的必要性:**上层组实体挂在哪条链、点亮哪个位,取决于它下层子队列的最高优先级**——下层一变,上层的归属就得重算。

## §2 入队:`enqueue_task_rt`(1539-1553)→ `enqueue_rt_entity`(1506-1516)

任务实体入队后,还要沿组层级自底向上把每一层组实体也挂好。核心难点在 `enqueue_rt_entity`:

```c
static void enqueue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    dequeue_rt_stack(rt_se, flags);              // ① 先把自己及所有上层从队列拆掉
    for_each_sched_rt_entity(rt_se)              // ② 再自底向上逐层重新挂回
        __enqueue_rt_entity(rt_se, flags);
    enqueue_top_rt_rq(&rq->rt);                  // ③ 顶层聚合计数
}
```

**为什么要先拆再挂(`dequeue_rt_stack`,1486-1504)?** 因为上层实体的优先级(§1 的 `rt_se_prio`)依赖下层。若不先拆,直接改下层,上层可能还挂在按旧优先级算出的错误链上。注释说得很直白:

```c
/*
 * Because the prio of an upper entry depends on the lower
 * entries, we must remove entries top - down.
 */
static void dequeue_rt_stack(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct sched_rt_entity *back = NULL;
    for_each_sched_rt_entity(rt_se) {            // 用 ->back 串成一条自底向上的链
        rt_se->back = back;
        back = rt_se;
    }
    ...
    for (rt_se = back; rt_se; rt_se = rt_se->back)   // 自顶向下(back 是最顶)遍历拆除
        if (on_rt_rq(rt_se))
            __dequeue_rt_entity(rt_se, flags);
    dequeue_top_rt_rq(rt_rq_of_se(back), rt_nr_running);
}
```

**落到具体队列 `__enqueue_rt_entity`(1434-1466)——位图同步与组节流跳过都在这里:**

```c
static void __enqueue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct rt_prio_array *array = &rt_rq->active;
    struct rt_rq *group_rq = group_rt_rq(rt_se);
    struct list_head *queue = array->queue + rt_se_prio(rt_se);

    /* 组被节流 / 组为空 → 不入队(否则会把一个跑不了的组挂进来) */
    if (group_rq && (rt_rq_throttled(group_rq) || !group_rq->rt_nr_running)) {
        if (rt_se->on_list)
            __delist_rt_entity(rt_se, array);
        return;
    }

    if (move_entity(flags)) {                    // ← 见下方"两种 flags"
        if (flags & ENQUEUE_HEAD)
            list_add(&rt_se->run_list, queue);   // 抢占场景插队头
        else
            list_add_tail(&rt_se->run_list, queue);
        __set_bit(rt_se_prio(rt_se), array->bitmap);   // 仅此一处置位
        rt_se->on_list = 1;
    }
    rt_se->on_rq = 1;
    inc_rt_tasks(rt_se, rt_rq);                   // 维护 nr_running/highest_prio/cpupri
}
```

**`move_entity(flags)` 是理解入/出队的钥匙**:它 = `!(flags & (DEQUEUE_SAVE | DEQUEUE_MOVE))`。当只是"临时保存位置稍后原样挂回"(如 `dequeue_rt_stack` 拆栈重挂过程中)时,`move_entity` 为假,链表位置和 `on_list`/位图**保持不动**,只更新计数——避免把还要挂回原位的实体反复摘链、反复翻位图。位图第 p 位的置位**只发生在 `move_entity` 为真且真正 list_add 时**,与 `__dequeue_rt_entity` 里 `__delist_rt_entity`→`__clear_bit`(链空时,`rt.c:1323`)严格配对(不变式见 03 §1.2)。

`enqueue_task_rt` 尾部:任务若可迁移(`nr_cpus_allowed > 1`)则 `enqueue_pushable_task` 挂入 pushable 红黑树,供 push 逻辑取用(§7)。

## §3 出队:`dequeue_task_rt`(1555-1563)

```c
static void dequeue_task_rt(struct rq *rq, struct task_struct *p, int flags)
{
    update_curr_rt(rq);                 // 先结算(记账+可能触发节流,见 §5)
    dequeue_rt_entity(rt_se, flags);
    dequeue_pushable_task(rq, p);
}
```

`dequeue_rt_entity`(1518-1533)同样先 `dequeue_rt_stack` 拆掉整条栈,再**只把"仍非空的组实体"重新挂回**(任务实体不重挂,因为它就是要走的那个):

```c
dequeue_rt_stack(rt_se, flags);
for_each_sched_rt_entity(rt_se) {
    struct rt_rq *rt_rq = group_rt_rq(rt_se);
    if (rt_rq && rt_rq->rt_nr_running)   // 组还有别的成员 → 组实体留在队列
        __enqueue_rt_entity(rt_se, flags);
}
```

`requeue_task_rt`(1583-1592):RR 时间片耗尽时把当前实体 `list_move_tail` 到同优先级链尾——**位图不动**(链仍非空),实现同优先级轮转。

## §4 选下一个:`pick_next_task_rt`(1818-1826)

```c
// _pick_next_task_rt(1793-1801):
do {
    rt_se = pick_next_rt_entity(rt_rq);          // 见下
    rt_rq = group_rt_rq(rt_se);                  // 组实体 → 下沉到子队列再选
} while (rt_rq);
return rt_task_of(rt_se);

// pick_next_rt_entity(1773):
idx = sched_find_first_bit(array->bitmap);       // O(1),最低置位位=最高优先级
return list_entry(array->queue[idx].next, ...);  // 该链表头
```

`sched_find_first_bit` 在 arm64 上是位扫描指令,常数时间——这是 RT "O(1) 选择"的字面来源(复杂度证明见 03 §2)。组调度下每层一次 `find_first_bit`,总代价 O(层数)。

## §5 记账与节流入口:`update_curr_rt`(1049-1089)

RT 版 `update_curr` 有两件事:累加 exec 时间(所有类都做),以及把时间**记进节流预算**(RT 独有)。注意它与 DL 的关键差异——**RT 不做算力/频率缩放**,直接累加墙钟增量:

```c
now = rq_clock_task(rq);
delta_exec = now - curr->se.exec_start;
...
update_current_exec_runtime(curr, now, delta_exec);   // 更新 exec_start/sum_exec_runtime

if (!rt_bandwidth_enabled())
    return;

for_each_sched_rt_entity(rt_se) {                     // 逐层组记账
    struct rt_rq *rt_rq = rt_rq_of_se(rt_se);
    if (sched_rt_runtime(rt_rq) != RUNTIME_INF) {
        raw_spin_lock(&rt_rq->rt_runtime_lock);
        rt_rq->rt_time += delta_exec;                 // 累加消耗
        exceeded = sched_rt_runtime_exceeded(rt_rq);  // 判超支(见 §6)
        if (exceeded)
            resched_curr(rq);                         // 超支立刻要求重调度
        raw_spin_unlock(&rt_rq->rt_runtime_lock);
        if (exceeded)
            do_start_rt_bandwidth(sched_rt_bandwidth(rt_rq));  // 确保补充定时器在跑
    }
}
```

`task_tick_rt`(2647-2679)在此基础上处理 RR:FIFO(`policy != SCHED_RR`)直接返回不轮转;RR 递减 `time_slice`,耗尽则重置为 `sched_rr_timeslice` 并 `requeue_task_rt` 移链尾 + `resched_curr`。

## §6 节流全机制:超支判定 + 跨 CPU 借带宽 + hrtimer 补充

这是 RT 最容易被讲浅的部分。它由三块拼成。

**(a) 超支判定 `sched_rt_runtime_exceeded`(1002-1043)——含"先借再判"与 boosted 特例:**

```c
static int sched_rt_runtime_exceeded(struct rt_rq *rt_rq)
{
    u64 runtime = sched_rt_runtime(rt_rq);
    if (rt_rq->rt_throttled)
        return rt_rq_throttled(rt_rq);           // 已限流(且未被 boost 豁免)
    if (runtime >= sched_rt_period(rt_rq))
        return 0;                                // runtime≥period 等于不限流

    balance_runtime(rt_rq);                      // ★ 先尝试从别的 CPU 借预算
    runtime = sched_rt_runtime(rt_rq);
    ...
    if (rt_rq->rt_time > runtime) {
        struct rt_bandwidth *rt_b = sched_rt_bandwidth(rt_rq);
        if (likely(rt_b->rt_runtime)) {
            rt_rq->rt_throttled = 1;             // 真限流
            printk_deferred_once("sched: RT throttling activated\n");
        } else {
            rt_rq->rt_time = 0;                  // ★ 组 rt_runtime=0 但因 boost 累了时间:清零不限流
        }
        if (rt_rq_throttled(rt_rq)) {
            sched_rt_rq_dequeue(rt_rq);          // 把整个 rt_rq 从上层摘掉,让位 fair
            return 1;
        }
    }
    return 0;
}
```

boosted 特例的意思:一个 `rt_runtime` 配额为 0 的组,本不该跑,但因 PI 提升(`rt_nr_boosted`)被迫跑了一点。对它"限流"没意义(补充也只会补 0ns),所以直接把 `rt_time` 清掉了事。

**(b) 跨 CPU 借带宽 `balance_runtime`→`do_balance_runtime`(732-779)——`RT_RUNTIME_SHARE` 特性:**

```c
static void do_balance_runtime(struct rt_rq *rt_rq)
{
    weight = cpumask_weight(rd->span);           // root domain 里 CPU 数
    for_each_cpu(i, rd->span) {
        struct rt_rq *iter = sched_rt_period_rt_rq(rt_b, i);
        ...
        diff = iter->rt_runtime - iter->rt_time; // 兄弟队列的空闲预算
        if (diff > 0) {
            diff = div_u64((u64)diff, weight);   // 取它 1/n 的空闲
            if (rt_rq->rt_runtime + diff > rt_period)
                diff = rt_period - rt_rq->rt_runtime;  // 但借到手不超过一个 period
            iter->rt_runtime -= diff;            // 从兄弟处划走
            rt_rq->rt_runtime += diff;           // 补给自己
            if (rt_rq->rt_runtime == rt_period)
                break;                           // 借满即止
        }
    }
}
```

`balance_runtime`(890-900)只在 `sched_feat(RT_RUNTIME_SHARE)` 开启且本队列确实超支时才调,且调用前后有解锁/重锁(跨队列取锁避免死锁)。这套机制让某个 CPU 上突发的 RT 负载能临时"挪用"其他 CPU 的空闲 RT 预算,而不是死板地各管各的 95%。热插拔/域重建时 `__disable_runtime`(784-864)会贪婪收回借出的预算(`WARN_ON_ONCE(want)` 保证不泄漏),`__enable_runtime`(866-888)重置。

**(c) 周期补充 `do_sched_rt_period_timer`(905-988)——hrtimer 回调:**每个 `rt_bandwidth` 挂一个 hrtimer(`sched_rt_period_timer`@70),到点对 span 内每个 rt_rq:`rt_time -= min(rt_time, overrun*runtime)` 扣掉一个(或多个 overrun)周期的量;若之前 `rt_throttled` 且现在 `rt_time < runtime`,清 `rt_throttled` 并 `sched_rt_rq_enqueue` 把队列重新挂回上层。这就是"下个周期到了,限流解除"的落点。

## §7 SMP:balance / push / pull

| 机制 | 入口 | 行 | 做什么 |
|---|---|---|---|
| balance | `balance_rt` | 1701-1716 | pick 前钩子:本 CPU 无 RT 可跑时尝试 `pull_rt_task` |
| 选核 | `select_task_rq_rt` | 1603-1672 | 唤醒时若目标 CPU 已在跑同/更高优先级 RT,找一个能立即运行的 CPU(避免排队) |
| push | `push_rt_tasks`/`push_rt_task` | 2181-2186 | 本 CPU 有多个 RT 且存在跑更低优先级的 CPU 时,把 pushable 任务推过去 |
| pull | `pull_rt_task` | 2359-2468 | 本 CPU 优先级降低时,从 overloaded 的 CPU 拉更高优先级 RT 过来 |
| 全局路由 | cpupri | `cpupri.c` | O(1) 位图查"哪些 CPU 跑着可被抢的更低优先级",push/pull 目标依据(见 [`../../04-interactions.md`](../../04-interactions.md)) |

push/pull 的目标选择靠 cpupri 把"每个优先级有哪些 CPU"维护成位图,查一次 O(1)。跨队列迁移时对两个 rq 取锁的顺序由 `double_lock_balance` 统一(锁序见 [`../../05-concurrency.md`](../../05-concurrency.md))。

## §8 本子模块专属可观测点

- **/proc/sched_debug**:`print_rt_rq`@`kernel/sched/debug.c:663` 打印 `rt_nr_running`/`rt_throttled`/`rt_time`/`rt_runtime`,分派 `print_rt_stats`@debug.c:770→`rt.c:3062`。
- **限流可见性**:`rt_rq->rt_throttled=1` + `rt_time` 逼近 `rt_runtime` 即本周期被限流;`printk` "RT throttling activated" 是首次触发的一次性告警。
- **借带宽可见性**:某 CPU 的 `rt_runtime` 偏离默认 950000(被借走/借入)说明 `RT_RUNTIME_SHARE` 生效中。
- tracepoint 复用通用 `sched_switch`/`sched_wakeup`(RT 无独占,见 [`../../07-debug.md`](../../07-debug.md))。

## §9 RT 差异(本模块)

- **抢占置位**:`update_curr_rt`/`task_tick_rt`/高优先级入队处的 `resched_curr`,PREEMPT_RT 下普通任务走 lazy 变体,但 RT 任务自身**不吃 lazy 折扣**——即时抢占。取证 [`../../05-concurrency.md`](../../05-concurrency.md) §7。
- **锁**:`rt_rq->rt_runtime_lock`@sched.h:688、`def_rt_bandwidth.rt_runtime_lock`@sched.h:280 本就是 `raw_spinlock_t`,RT 下语义不变;§6 的跨队列取锁在 RT 下也不睡眠。
- **hrtimer**:`sched_rt_period_timer` 是 hrtimer,RT 下 hrtimer 回调上下文有别,但补充逻辑不变。
