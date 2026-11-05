# rt · 03 算法与策略

> 本篇回答"RT 的算法是什么、什么时候退化、哪个参数能调"。流程"谁调用谁"在 [`02-internals.md`](02-internals.md);跨模块关系(push/pull 与 cpupri、DL 挤压 RT 带宽)上引 [`../../04-interactions.md`](../../04-interactions.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §0 设计故事:为什么 RT 用位图而不用红黑树

实时调度的第一诉求是**确定性**:选下一个任务的时间必须与就绪任务数无关(否则任务越多、调度越慢,实时性无从谈起)。POSIX 实时策略只有 100 个静态优先级(0-99),数量固定且小——于是内核用一个"位图 + 100 条链表"的定长结构,把"选最高优先级"退化成"找位图最低置位位",在 arm64 上就是一条位扫描指令,严格 O(1)。

代价是**放弃了公平性**:同优先级用 FIFO/RR 简单轮转,不做 CFS 那样的按权重虚拟时间核算。这是刻意取舍——实时任务要的是"谁优先级高谁先跑、且延迟可预测",不是"大家按比例分 CPU"。CFS 能容忍 O(log n) 是因为它的目标是吞吐与公平;RT 不能,因为它的目标是最坏情况延迟上界。

第二个设计张力是**RT 会饿死普通任务**:一个死循环的 FIFO 任务能独占 CPU 永不让出。内核用**带宽节流**兜底(每周期限一个 RT 运行预算),这不是实时性需要的,而是系统可用性的保险。

## §1 算法本体

### §1.1 数据结构(算法直接依赖)

| 结构 | 作用 | 定义位置 |
|---|---|---|
| `rt_prio_array->bitmap` | `MAX_RT_PRIO+1` 位,第 i 位=优先级 i 链表非空 | `kernel/sched/sched.h:664`(内 struct) |
| `rt_prio_array->queue[100]` | 每优先级一条 FIFO 链表 | 同上 |
| `rt_rq->rt_time` / `rt_runtime` | 本周期已耗 / 可用预算,节流判据 | `kernel/sched/sched.h:686` |
| `sched_rt_entity->time_slice` | RR 剩余时间片 | `include/linux/sched.h`(rt 段) |

### §1.2 关键不变式

| # | 不变式 | 维护点 | 违反后果 |
|---|---|---|---|
| 1 | 位图第 p 位置位 ⟺ `queue[p]` 非空 | 置位 `__enqueue_rt_entity`@rt.c:1460(链空→非空);清位 `__dequeue_rt_entity`@rt.c:1323(链非空→空) | 位图与链表不一致:选到空链(崩)或漏选就绪任务(饿死) |
| 2 | `sched_find_first_bit` 返回的最低位=系统最高优先级 | 优先级 0 映射到位 0,数值越小优先级越高 | 优先级反转,低优先级任务抢占高优先级 |
| 3 | `rt_time ≤ rt_runtime` 时不节流,超过即限流 | `update_curr_rt`@rt.c:1049 累加 + `sched_rt_runtime_exceeded`@rt.c:1002 判定 | 不判超支则 RT 死循环独占 CPU,普通任务饿死 |
| 4 | root domain 内 Σ`rt_runtime` 守恒(借用只搬运不凭空造) | 借出/借入成对增减 `do_balance_runtime`@rt.c:732-779;热插拔归还 `__disable_runtime`@rt.c:784(`WARN_ON_ONCE(want)`@rt.c:850) | 预算泄漏/凭空增加,全域 RT 占比突破 95% 上限,普通任务饿死 |

不变式取证:置位/清位均为代码显式(`__set_bit`/`__clear_bit` 各一处,与链表增删同函数);节流判定 `sched_rt_runtime_exceeded`@1002-1043 是独立函数;借用守恒见 §1.4。

### §1.3 算法主体

选任务(热路径,`_pick_next_task_rt`@rt.c:1793):
```
idx = sched_find_first_bit(bitmap)      // O(1),最低置位位=最高非空优先级
se  = queue[idx].first                  // 链表头
// 组调度:逐层重复,每层一次 find_first_bit
```

同优先级轮转(RR,`task_tick_rt`@rt.c:2647):
```
if policy == FIFO: return              // FIFO 不轮转,跑到主动让出
if --time_slice > 0: return
time_slice = sched_rr_timeslice         // 重置
requeue_task_rt(移到同优先级链尾)@rt.c:1583   // 位图不动(链仍非空)
resched_curr
```

带宽节流(`update_curr_rt`@rt.c:1049 记账):
```
rt_time += delta_exec
if sched_rt_runtime_exceeded():         // rt_time > rt_runtime
    rt_throttled = 1                    // 本队列本周期停跑,让位 fair
// 周期定时器到点补充 rt_runtime,清 throttled
```

### §1.4 带宽借用算法与 boosted 特例(高级机制)

单 CPU 各管各的 95% 预算过于死板:CPU0 上突发一批 RT、CPU1 却空着,CPU0 会无谓被限流。`RT_RUNTIME_SHARE` 特性让超支的队列先**跨 CPU 借预算**再判限流。算法(`do_balance_runtime`@rt.c:732,实现细节见 [`02-internals.md`](02-internals.md) §6):

```
weight = |root_domain.span|                  // 域内 CPU 数 n
for each sibling iter in span:
    spare = iter.rt_runtime - iter.rt_time    // 兄弟的空闲预算
    if spare > 0:
        take = spare / n                      // 每次只取兄弟空闲的 1/n(避免掏空一个)
        take = min(take, rt_period - self.rt_runtime)  // 借到手不超过一个 period(上界)
        iter.rt_runtime -= take               // 从兄弟划走
        self.rt_runtime += take               // 补给自己 —— Σrt_runtime 守恒(不变式 4)
        if self.rt_runtime == rt_period: break // 借满即止
```

两条关键上界共同保证收敛与公平:**每兄弟只取 1/n** 防止把某个 CPU 掏空;**自身封顶到 `rt_period`** 防止一个 CPU 无限吸预算(借满等于"这个 CPU 本周期不受节流")。热插拔或调度域重建时,`__disable_runtime`@rt.c:784 贪婪地把借出的预算收回,末尾 `WARN_ON_ONCE(want)` 断言没有预算悬空——这是不变式 4 的运行时守卫;`__enable_runtime`@rt.c:866 在 CPU 上线时把 `rt_runtime` 重置回配置值。

**boosted 特例(`rt_runtime == 0` 的组):** 一个配额为 0 的 RT 组本不该运行,但因优先级继承(PI)被临时提升(`rt_nr_boosted`)后跑了一小段,`rt_time` 会累加。此时"限流"没有意义——补充定时器补的也是 0ns,限了就永远解不开。算法的处理是:检测到 `rt_b->rt_runtime == 0` 时**不置 `rt_throttled`,直接把 `rt_time` 清零**(`02-internals.md` §6a)。算法意义:PI 提升是正确性需求(避免优先级反转),它凌驾于带宽核算之上——带宽机制主动为 PI 让路,而不是反过来卡死 PI。

## §2 复杂度

| 操作 | 平均 | 最坏 | 最坏触发 |
|---|---|---|---|
| pick(选下一个) | O(1) | O(1) | `sched_find_first_bit` 位扫描,与就绪数无关(rt.c:1780) |
| enqueue / dequeue | O(1) | O(1) | `list_add_tail`/`list_del` + 单 bit 翻转 |
| 组调度 pick 下溯 | O(depth) | O(depth) | depth = cgroup 层级,每层一次 O(1) |
| push / pull | O(1) 查 + O(迁移数) | O(nr_cpus) | cpupri 查目标 O(1);遍历 overloaded CPU 时与 CPU 数相关 |
| 带宽借用 `do_balance_runtime` | O(nr_cpus) | O(nr_cpus) | 遍历 root domain span 内每个兄弟 rt_rq(仅超支且 `RT_RUNTIME_SHARE` 开时走) |

RT 全流程无排序,是与 CFS(O(log n) 插删)最本质的复杂度区别。唯一非 O(1) 的是带宽借用(遍历兄弟 CPU),但它只在超支时偶发,不在选任务热路径上。

## §3 退化条件

| 退化场景 | 触发条件 | 表现 | 可观测指标 | 缓解手段 |
|---|---|---|---|---|
| RT 独占饿死普通任务 | FIFO 任务死循环不让出 | 普通任务永不运行 | `rt_rq->rt_throttled`、`rt_time` 逼近 `rt_runtime` | 带宽节流 950000/1000000 兜底(§4) |
| 节流误伤合法 RT | `rt_runtime` 调过小 | 合法实时任务被限流,延迟飙升 | 周期性 `rt_throttled` 置位 | 调大 `sched_rt_runtime_us` 或设 -1 关闭 |
| push/pull 抖动 | 多 CPU 频繁跨核迁移 RT | 缓存失效、迁移开销 | `sched_migrate_task` 频率 | 绑核 / `isolcpus` |
| 优先级反转(跨模块) | RT 等低优先级持有的锁 | RT 被间接阻塞 | — | PI(优先级继承)提升持锁者,见 [`../../04-interactions.md`](../../04-interactions.md) |
| 组调度深嵌套 | RT cgroup 层级深 | pick 逐层放大 | perf 热点在 `for_each_sched_rt_entity` | 控制层级 |

前两行触发与缓解均从代码/参数取证;优先级反转行标注了性质(跨 locking 模块)。

## §4 参数敏感性

| 参数 | 类型 | 默认值 | 影响 | 调大 | 调小 | 定义位置 |
|---|---|---|---|---|---|---|
| `sched_rt_period_us` | sysctl | 1000000 (1s) | 节流周期长度 | 节流粒度粗 | 节流频繁、开销↑ | `kernel/sched/rt.c:19`(`def_rt_bandwidth`@13) |
| `sched_rt_runtime_us` | sysctl | 950000 (0.95s) | 每周期 RT 可用预算;-1 关闭节流 | RT 占比↑、普通任务余量↓ | RT 更易被限流 | `kernel/sched/rt.c:25` |
| `sched_rr_timeslice_ms` | sysctl | RR_TIMESLICE(盲区,回查) | RR 时间片长度 | 轮转慢、切换少 | 轮转快、切换多 | `kernel/sched/rt.c`(sysctl 表@33) |

默认值取证:`def_rt_bandwidth` 初值 period=1000000@rt.c:19、runtime=950000@rt.c:25。运维视角(如何设、RT_GROUP_SCHED 分叉)见 [`../../06-tuning.md`](../../06-tuning.md) §5;本篇讲机理。95% 这个默认值的含义:RT 最多占 95% CPU,强制留 5% 给普通任务,是"实时性 vs 系统可用性"权衡的数字化。

## §5 设计权衡

| 候选方案 | 优 | 劣 | 内核为什么选/没选 |
|---|---|---|---|
| 位图 + 100 链表 | 选任务严格 O(1),延迟可预测 | 无公平性,仅 100 级 | 选了:实时要确定性,100 级够用 |
| 红黑树按优先级(如 CFS) | 支持任意多"优先级" | O(log n) 选择,延迟随任务数变 | 没选:破坏 O(1) 确定性 |
| 无节流的纯优先级 | 最纯粹的实时语义 | RT 死循环饿死一切 | 加了节流兜底,牺牲一点纯度换可用性 |

这些结论中"为什么 100 级""为什么 O(1)"属 POSIX 实时语义与内核设计约定,`Documentation/scheduler/sched-rt-group.rst` 是一等参考。节流的存在性是代码事实(`sched_rt_runtime_exceeded`),其"为什么 95%"的数值论证属策略型证据。

## §6 与 DL 的带宽耦合(跨模块,仅指路)

DL 类运行时会**先从 RT 带宽预算里扣**——即 DL 的准入与运行会挤压 RT 可用的 `rt_runtime`。这是本套文档发现的一处非显然耦合,完整论证在 [`../../04-interactions.md`](../../04-interactions.md) §3(dl×rt 格子)与 [`08-feynman.md`](../../08-feynman.md) 概念四。本篇只提示:调 RT 带宽时须连带考虑 DL 占用,否则 RT 实际可用预算比 `sched_rt_runtime_us` 显示的更少。
