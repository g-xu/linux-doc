# fair · 03 算法与策略

> 本篇回答"CFS 的算法是什么、什么时候退化、哪个参数能调"。流程"谁调用谁"在 [`02-internals.md`](02-internals.md);跨模块关系上引 [`../../04-interactions.md`](../../04-interactions.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。本树 CFS 用 min_vruntime + 红黑树,无 EEVDF(见 §6)。

## §1 算法本体

### §1.1 数据结构(算法直接依赖)

| 结构 | 作用 | 定义位置 |
|---|---|---|
| `cfs_rq->tasks_timeline` | 按 vruntime 排序的红黑树,`rb_root_cached` 缓存最左节点 | `kernel/sched/sched.h:568` |
| `sched_entity->vruntime` | 实体虚拟运行时间,排序键 | `include/linux/sched.h:556` |
| `cfs_rq->min_vruntime` | 队列 vruntime 下界,新实体入队基准 | `kernel/sched/sched.h:558` |
| `sched_entity->load` | 实体权重(由 nice 映射),决定 vruntime 步进速度 | `include/linux/sched.h:549` |

完整结构清单见 [`02-internals.md`](02-internals.md) §1。

### §1.2 关键不变式

| # | 不变式 | 维护点 | 违反的后果 |
|---|---|---|---|
| 1 | `cfs_rq->min_vruntime` 单调不减 | `update_min_vruntime`@592-618(尾部 `max_vruntime`@616-617 保单调) | 新入队实体基准倒退,vruntime 比较错乱,公平性崩溃 |
| 2 | runnable 实体的 vruntime ≈≥ min_vruntime | `place_entity`@4648-4703(溢出保护@4689-4700 钳制回 min 附近) | 长睡任务唤醒后 vruntime 远小于 min,抢占一切、饿死其他任务 |
| 3 | vruntime 比较用有符号差判序,容忍 u64 回绕 | `entity_before`@583-587(`(s64)(a->vruntime - b->vruntime) < 0`) | 直接 `<` 比较在回绕点排序反转 |

不变式取证:`update_min_vruntime` 尾部的 `max_vruntime` 调用、`place_entity` 的溢出保护块、`entity_before` 的强制有符号转型,均为代码显式实现(非注释推断)。

### §1.3 算法主体

CFS 一轮调度分四步:

步骤 1 — 累加运行时间到 vruntime(`update_curr`@882-920):
```
delta_exec = now - curr->exec_start           // 本次实际运行的物理时间
curr->vruntime += calc_delta_fair(delta_exec, curr)   // @908,按权重折算成虚拟时间
update_min_vruntime(cfs_rq)                     // @909,推进队列下界
```
`calc_delta_fair`:权重为 `NICE_0_LOAD`(nice 0)时 vruntime 步进 = 物理时间;权重越大步进越慢。

步骤 2 — 定新实体 vruntime 初值(`place_entity`@4648-4703):
```
vruntime = cfs_rq->min_vruntime
if (initial && START_DEBIT)   vruntime += sched_vslice(...)   // @4658-4659,新任务罚一个时间片,防 fork 炸弹
if (!initial)                 vruntime -= thresh              // @4666-4677,唤醒任务补偿睡眠亏空
    // GENTLE_FAIR_SLEEPERS 下 thresh 减半(只补 50%)@4674-4677
vruntime = max_vruntime(se->vruntime, vruntime)               // @4689-4700,溢出/倒退保护(不变式 2)
```

步骤 3 — 选下一个(`__pick_first_entity`@638-646):取红黑树最左节点 = 最小 vruntime,`rb_first_cached` 直读缓存指针,O(1)。

步骤 4 — 插/删维护有序(`__enqueue_entity`@628-631 `rb_add_cached` + `__entity_less` / `__dequeue_entity`@633-636 `rb_erase_cached`):O(log n)。

宏常量:`NICE_0_LOAD`(nice 0 权重基准)、`sysctl_sched_latency`=6ms(@72)、`sysctl_sched_min_granularity`=0.75ms(@93)、`sysctl_sched_wakeup_granularity`=1ms(@124)、`sched_nr_latency`=8(@107)。

## §2 复杂度

| 操作 | 平均 | 最坏 | 最坏触发条件 |
|---|---|---|---|
| pick(选下一个) | O(1) | O(1) | `rb_root_cached` 缓存最左,恒定(`__pick_first_entity`@640) |
| enqueue / dequeue | O(log n) | O(log n) | n = 本 cfs_rq 就绪实体数;红黑树深度决定 |
| 组调度 pick 下溯 | O(depth) | O(depth·log n) | 每层一次 O(log n) 选择,depth = cgroup 嵌套层数(`se->depth`) |
| `update_curr` 逐层 | O(depth) | O(depth) | 组调度深嵌套时自底向上逐层结算 |

n 是单个 cfs_rq 的可运行实体数,不是全系统任务数(每 CPU 每 task_group 各一棵树)。

## §3 退化条件

| 退化场景 | 触发条件 | 表现 | 可观测指标 | 缓解手段 |
|---|---|---|---|---|
| 短睡眠任务风暴 | 大量任务频繁短睡后唤醒 | 每次唤醒都补 vruntime 亏空,抢占频繁 | `nr_spread_over`(schedstat)、切换率 | `GENTLE_FAIR_SLEEPERS` 只补 50% 亏空(@4674-4677) |
| cgroup 深嵌套 | 组调度层级过深 | `update_curr`/pick 逐层放大,depth 倍开销 | perf 热点在 `for_each_sched_entity` | 控制层级;`se->depth` 限界 |
| 就绪实体数极大 | 单 cfs_rq 上 n 很大 | 红黑树变深 + `__sched_period` 拉伸调度周期(@710-716) | 单任务延迟上升 | `sysctl_sched_min_granularity` 兜底最小片 |
| 唤醒抢占抖动(参数) | `wakeup_granularity` 调过小 | 唤醒任务过易抢占,吞吐下降 | 上下文切换率飙升 | 见 §4 |
| PELT 快照滞后(跨模块) | 负载突变时调频读到旧 util | 调频反应慢 | schedutil 频率曲线滞后 | 属 fair→schedutil,见 [`../../04-interactions.md`](../../04-interactions.md) §3.5 |

前三行触发条件与缓解手段均从代码路径取证(行号见表内);后两行标注了性质。跨模块退化(末行)指向顶层 04 矩阵 SU→FA 格子。

## §4 参数敏感性

| 参数 | 类型 | 默认值 | 影响 | 调大 | 调小 | 定义位置 |
|---|---|---|---|---|---|---|
| `sysctl_sched_latency` | sysctl | 6ms | 目标调度周期,`__sched_period`@715、`place_entity` thresh@4668 | 吞吐↑、单任务延迟↑ | 交互性↑、切换开销↑ | fair.c:72 |
| `sysctl_sched_min_granularity` | sysctl | 0.75ms | 最小运行片,`__sched_period`@713、`check_preempt_tick`@4932 | 切换开销↓、公平粒度粗 | 抢占频繁、开销↑ | fair.c:93 |
| `sysctl_sched_wakeup_granularity` | sysctl | 1ms | 唤醒抢占门槛,唯一用点 `wakeup_gran`@7584 | 难被唤醒抢占(利吞吐) | 唤醒响应快(利交互) | fair.c:124 |
| `sysctl_sched_idle_min_granularity` | sysctl | (源码取证) | SCHED_IDLE 任务最小片 | — | — | fair.c:102 |
| `sched_nr_latency` | 宏/派生 | 8 | latency 周期内容纳的任务数阈值 | — | — | fair.c:107 |

默认值均从 fair.c 源码取证(行号见表)。运维视角(如何设、cgroup 落点)见 [`../../06-tuning.md`](../../06-tuning.md);本篇讲机理。

特性开关(features.h,影响算法行为):

| SCHED_FEAT | 行 | 默认 | 影响 |
|---|---|---|---|
| `GENTLE_FAIR_SLEEPERS` | 7 | true | 睡眠亏空只补 50%(place_entity@4674) |
| `START_DEBIT` | 13 | true | 新任务加 vslice(place_entity@4658) |
| `NEXT_BUDDY` | 20 | false | 唤醒设 next buddy(check_preempt_wakeup@7683) |
| `LAST_BUDDY` | 27 | true | 设 last buddy(@7755) |
| `WAKEUP_PREEMPTION` | 38 | true | 唤醒抢占总开关(@7710) |

## §5 设计权衡

| 候选方案 | 优 | 劣 | 内核为什么选/没选 |
|---|---|---|---|
| 红黑树按 vruntime | 选最左 O(1),有序自动维护 | 插删 O(log n) | 选了:pick 是热路径,O(1) pick 值得 O(log n) 插删 |
| O(1) 优先级数组(旧调度器) | 全 O(1) | 140 队列 + 主动/过期数组切换,交互性靠启发式 heuristics 打补丁 | 换掉了(CFS 2.6.23 起) |
| 固定时间片轮转 | 实现简单 | 无法按权重精确分配、交互性差 | 没选 |

这些结论属策略型证据(出处:CFS 设计文档 / commit 历史,非本树源码可完整验证)。`Documentation/scheduler/sched-design-CFS.rst` 是一等参考。未在本树代码注释中找到完整设计论证的部分,不替内核编造理由。

## §6 历史演进

| 版本 | 算法 | 换掉的原因 |
|---|---|---|
| ≤2.6.22 | O(1) 调度器 | 交互性靠启发式,难维护 |
| 2.6.23 – 6.5 | CFS(vruntime + 红黑树) | 本树 6.1.158 用此代 |
| 6.6+ | EEVDF(virtual deadline + lag) | 更好的延迟公平,本树未 backport |

本树 6.1.158 确认为 CFS 代:`sched_entity`(include/linux/sched.h:547-581)无 `deadline`/`vlag`/`slice` 字段,`entity_before` 仍用纯 vruntime 比较(@583)。此为策略型 + 源码双重取证。

