---
title: 取证附录
---

# rt · 99 取证附录

> 本篇记录 rt 子模块(00/02/03)结论的可复现出处。行号指 6.1.158 源码,文件除注明外在 `kernel/sched/rt.c`。宏/间接调用类结论标"盲区"并给复现命令。顶层取证在 [`../../99-evidence.md`](../../99-evidence.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §1 数据结构

| 符号 | 行 | 备注 |
|---|---|---|
| `struct rt_rq` | `kernel/sched/sched.h:663` | 内嵌于 rq |
| `rt_rq->active`(rt_prio_array) | `kernel/sched/sched.h:664` | bitmap + queue[100] |
| `rt_rq->rt_nr_running` | `kernel/sched/sched.h:665` | |
| `rt_rq->rt_runtime` | `kernel/sched/sched.h:686` | 节流预算 |
| `rt_rq->rt_runtime_lock` | `kernel/sched/sched.h:688` | raw_spinlock_t |
| `def_rt_bandwidth` | `rt.c:13` | 全局默认带宽 |
| `rt_se_prio` | `rt.c:990` | 取实体静态优先级 |

## §2 主流程函数区间(02 §2-§6)

| 函数 | 区间 | 覆盖点 |
|---|---|---|
| `enqueue_task_rt` | `rt.c:1538-1553` | 入队入口 |
| `enqueue_rt_entity` | `rt.c:1506-1516` | 拆栈重挂:`dequeue_rt_stack`→逐层 `__enqueue_rt_entity` |
| `__enqueue_rt_entity` | `rt.c:1434-1466`(`__set_bit`@1460) | 链空→非空置位;组节流跳过 `rt_rq_throttled`@1447 |
| `dequeue_task_rt` | `rt.c:1555-1563` | 出队入口 |
| `dequeue_rt_entity` | `rt.c:1518-1533` | 拆栈后只重挂非空组实体 |
| `__dequeue_rt_entity` | `rt.c:1468`(`__clear_bit`@1323) | 链非空→空清位 |
| `dequeue_rt_stack` | `rt.c:1486-1504` | 重挂前自顶向下摘栈(上层优先级依赖下层) |
| `move_entity` | `rt.c`(`!(flags & (DEQUEUE_SAVE\|DEQUEUE_MOVE))`) | 真移动 vs 原地保存(拆栈重挂用) |
| `rt_se_prio` | `rt.c:990-1000` | 组实体取子队列 `highest_prio.curr` |
| `requeue_task_rt` | `rt.c:1583-1592` | RR 移链尾 |
| `pick_next_task_rt` | `rt.c:1818-1826` | 选任务入口 |
| `_pick_next_task_rt` | `rt.c:1793-1801` | 逐层下溯 |
| `pick_next_rt_entity` | `rt.c:1773`(`sched_find_first_bit`@1780) | O(1) 选最高优先级 |
| `task_tick_rt` | `rt.c:2647-2679` | RR 时间片 + watchdog |
| `update_curr_rt` | `rt.c:1049-1089` | 记账 + 节流入口 |
| `select_task_rq_rt` | `rt.c:1603-1672` | 唤醒选核 |
| `inc_rt_tasks` / `dec_rt_tasks` | `rt.c:1279` / `1293` | 计数 + 最高优先级维护 |
| `balance_rt` | `rt.c:1701-1716` | pick 前均衡 |
| `push_rt_tasks` | `rt.c:2181-2186` | 推出 |
| `pull_rt_task` | `rt.c:2359-2468` | 拉入 |

## §3 带宽节流(03 §1.2 不变式 3 / §4)

| 结论 | 证据 |
|---|---|
| 记账累加 `rt_time` | `update_curr_rt`@rt.c:1049-1089 |
| 超支判定 | `sched_rt_runtime_exceeded`@rt.c:1002-1043 |
| 默认 period 1000000us | `def_rt_bandwidth` 初始化,`rt.c:19` |
| 默认 runtime 950000us | `rt.c:25` |
| sysctl 表注册 | `rt.c:33`(注册@:64) |
| 关闭节流写 -1 | 判定 `kernel/sched/sched.h:297,654`(RUNTIME_INF) |
| boosted 特例(rt_runtime=0 清 rt_time 不限流) | `sched_rt_runtime_exceeded`@rt.c:1024-1034 |
| 超支后摘队让位 fair | `sched_rt_rq_dequeue`(经 `rt_rq_throttled`@rt.c:1002) |
| 周期补充 hrtimer 回调 | `do_sched_rt_period_timer`@rt.c:905-988;定时器 `sched_rt_period_timer`@rt.c:70 |

## §3.1 带宽借用(03 §1.4 不变式 4)

| 结论 | 证据 |
|---|---|
| 借用总入口(超支时先借) | `balance_runtime`@rt.c:890-900;`sched_feat(RT_RUNTIME_SHARE)` 门控 |
| 跨 CPU 搬运预算 | `do_balance_runtime`@rt.c:732-779 |
| 每兄弟取 1/n | `rt.c:765`(`div_u64(diff, weight)`,weight=`cpumask_weight(rd->span)`@rt.c:739) |
| 借入封顶到一个 period | `rt.c:766-767`(`rt_rq->rt_runtime + diff > rt_period` 时截断) |
| 热插拔归还(守恒守卫) | `__disable_runtime`@rt.c:784-864(`WARN_ON_ONCE(want)`@rt.c:850) |
| 上线重置 | `__enable_runtime`@rt.c:866-888 |

## §4 位图 O(1)(03 §1.1 / §2)

| 结论 | 证据 |
|---|---|
| 置位仅在链空→非空 | `__enqueue_rt_entity`@rt.c:1460(`if list_empty` 后 `__set_bit`) |
| 清位仅在链非空→空 | `__dequeue_rt_entity`@rt.c:1323(`if list_empty` 后 `__clear_bit`) |
| 选最低置位位 | `pick_next_rt_entity`@rt.c:1780(`sched_find_first_bit`) |
| 100 级优先级 | MAX_RT_PRIO(盲区,见 §6) |

## §5 SMP / 优先级(00 §4 / 02 §6)

| 结论 | 证据 |
|---|---|
| cpupri O(1) 路由 | `cpupri.c`(cpupri_find/set,见 [`../../04-interactions.md`](../../04-interactions.md)) |
| DEFINE_SCHED_CLASS 实例 | `rt.c:2692`(优先级链 stop>dl>**rt**>fair>idle) |
| print_rt_rq 观测 | `kernel/sched/debug.c:663`;分派 `debug.c:770`→`rt.c:3062` |

## §6 索引盲区声明

以下走 rg + Read,不单靠代码图:

- 宏符号:`DEFINE_SCHED_CLASS`(rt.c:2692)、`for_each_sched_rt_entity`(组调度逐层循环,间接展开)、`MAX_RT_PRIO`/`RR_TIMESLICE`(数值须回查 `include/linux/sched/prio.h` / rt.c)、`sched_find_first_bit`(arch 位扫描,arm64 硬件指令)。
- 间接调用:sched_class 回调经函数指针分派;`resched_curr`/`resched_curr_lazy` 为 inline/宏(RT 差异见 [`../../05-concurrency.md`](../../05-concurrency.md) §7)。
- 数值未展开:`sched_rr_timeslice` 默认 = RR_TIMESLICE,引用数值须回查头文件。
- 复现命令:
  - `rg -n 'DEFINE_SCHED_CLASS' kernel/sched/rt.c`
  - `rg -n '__set_bit|__clear_bit|sched_find_first_bit' kernel/sched/rt.c`
  - `rg -n 'def_rt_bandwidth|sched_rt_runtime_exceeded' kernel/sched/rt.c`
  - `rg -n 'MAX_RT_PRIO|RR_TIMESLICE' include/linux/sched/`
