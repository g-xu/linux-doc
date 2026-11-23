---
title: 取证附录
---

# deadline · 99 取证附录

> 本篇记录 deadline 子模块(00/02/03)结论的可复现出处。行号指 6.1.158 源码,文件除注明外在 `kernel/sched/deadline.c`。宏/理论类结论标"盲区"或"策略型"并给复现命令/出处。顶层取证在 [`../../99-evidence.md`](../../99-evidence.md)。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §1 数据结构

| 符号 | 行 | 备注 |
|---|---|---|
| `struct sched_dl_entity` | `include/linux/sched.h:601` | 三参数 + 运行时状态 |
| `struct dl_rq` | `kernel/sched/sched.h:704` | 内嵌于 rq |
| `dl_rq->dl_nr_running` | `kernel/sched/sched.h:708` | |
| `struct dl_bw` | `kernel/sched/sched.h:318` | 准入带宽账本 |
| `struct dl_bandwidth` | `kernel/sched/sched.h:289` | 全局默认带宽 |
| `init_dl_bandwidth` | `deadline.c:494` | 带宽初始化 |

## §2 主流程函数区间(02 §2-§7)

| 函数 | 区间 | 覆盖点 |
|---|---|---|
| `enqueue_task_dl` | `deadline.c:1672-1710` | 入队入口 |
| `enqueue_dl_entity` | `deadline.c:1642` | 落队 |
| `__enqueue_dl_entity` | `deadline.c:1616` | `rb_add_cached` 按 deadline 插树 |
| `dequeue_task_dl` | `deadline.c:1763` | 出队入口;睡眠→`task_non_contending` |
| `pick_next_task_dl` | `deadline.c:2033-2042` | EDF 选任务 |
| `update_curr_dl` | `deadline.c:1309-1408` | CBS 记账 + 缩放/GRUB + 限流 + RT 反向记账 |
| `update_dl_entity` | `deadline.c:1018-1035` | CBS 唤醒判定/重置(调 `dl_entity_overflow`@1024) |
| `replenish_dl_entity` | `deadline.c:837-883` | 预算补充 |
| `dl_runtime_exceeded` | `deadline.c:1259` | 超支判定 |
| `dl_check_constrained_dl` | `deadline.c:1243` | 受约束截止修正 |
| `start_dl_timer` | `deadline.c:1052` | arm 补充定时器 |
| `dl_task_timer` | `deadline.c:1110-1215` | 周期补充回调 + 竞态处理 |
| `task_tick_dl` | `deadline.c:2067-2080` | tick 记账 |
| `select_task_rq_dl` | `deadline.c:1829-1875` | 唤醒选核 |
| `inc_dl_tasks` | `deadline.c:1508` | 计数 + 最早 deadline 维护 |
| `balance_dl` | `deadline.c:1931-1946` | pick 前均衡 |
| `push_dl_task` / `push_dl_tasks` | `deadline.c:2324-2400` / `2402-2407` | 推出 |
| `pull_dl_task` | `deadline.c:2409-2499` | 拉入 |

## §3 准入控制(03 §1.2 不变式 3 / §1.3)

| 结论 | 证据 |
|---|---|
| 准入入口 | `sched_dl_overflow`@deadline.c:2865-2921 |
| 由 setattr 触发 | `core.c:7782`(`sched_dl_overflow` 调用点) |
| 带宽和判据 | `__dl_overflow`@deadline.c:227(`Σbw ≤ 容量`) |
| DL 带宽复用 RT 预算 | 见 [`../../04-interactions.md`](../../04-interactions.md) §3(dl×rt);准入容量派生自 `sched_rt_runtime_us`/`period` |

## §4 EDF + CBS(03 §1)

| 结论 | 证据 |
|---|---|
| 按绝对 deadline 排序红黑树 | `__enqueue_dl_entity`@deadline.c:1616(`rb_add_cached`) |
| 选最左=最早 deadline | `pick_next_task_dl`@deadline.c:2033(`rb_first_cached`) |
| CBS 扣预算 | `update_curr_dl`@deadline.c:1309;超支 `dl_runtime_exceeded`@deadline.c:1259 |
| 周期补预算 + 推进 deadline | `dl_task_timer`@deadline.c:1110-1215;`replenish_dl_entity`@deadline.c:837 |
| 唤醒重置 deadline | `update_dl_entity`@deadline.c:1018-1035 |

## §4.1 活跃带宽与 0-lag 非连续态(02 §3/§6,03 §1.4 不变式 5)

| 结论 | 证据 |
|---|---|
| 三层带宽字段 | `running_bw`/`this_bw`@`kernel/sched/sched.h`(dl_rq 内);`total_bw`@`struct dl_bw`(sched.h:318) |
| 活跃带宽增减 | `__add_running_bw`@deadline.c:234 / `__sub_running_bw`@deadline.c:247 |
| rq 保留带宽增减 | `__add_rq_bw`@deadline.c:261 / `__sub_rq_bw`@deadline.c:271 |
| 准入总带宽增减 | `__dl_add`@deadline.c:220 / `__dl_sub`@deadline.c:213 |
| 睡眠→非连续态 | `task_non_contending`@deadline.c:392;置 `dl_non_contending=1`@deadline.c:443 |
| 0-lag 时刻公式 | `deadline.c:412-414`(`deadline - runtime*dl_period/dl_runtime`),转相对@420 |
| arm inactive_timer | `deadline.c:445`(`HRTIMER_MODE_REL_HARD`) |
| 0-lag 到点释放 | `inactive_task_timer`@deadline.c:1410;init@1452 |
| 非连续态中途唤醒 | `task_contending`@deadline.c:448 |
| running_bw 改动踢调频 | `__add_running_bw`@deadline.c:234(`cpufreq_update_util`) |

## §4.2 GRUB 与 CBS 修订规则(02 §7/§8,03 §1.4/§1.5)

| 结论 | 证据 |
|---|---|
| GRUB 回收缩放 | `grub_reclaim`@deadline.c:1283-1303;`u_inact = this_bw - running_bw`@1285 |
| GRUB 下界 u_act_min | `deadline.c:1287`(`dl_bw * bw_ratio >> RATIO_SHIFT`) |
| GRUB 入口(RECLAIM 分支) | `update_curr_dl`@deadline.c:1353(`SCHED_FLAG_RECLAIM` 判定) |
| 频率/算力缩放(非 RECLAIM) | `update_curr_dl`@deadline.c:1359-1365(`cap_scale(cap_scale(delta,freq),cpu)`) |
| CBS 密度溢出判据 | `dl_entity_overflow`@deadline.c:909;交叉相乘@935(`dl_time_before(right,left)`) |
| CBS 修订规则(受约束) | `update_dl_revised_wakeup`@deadline.c:957(`runtime = dl_density*laxity>>BW_SHIFT`) |
| DL↔RT 反向记账 | `update_curr_dl`@deadline.c:1395-1406(`rt_rq->rt_time += delta_exec`) |
| 准入缩放判据 | `__dl_overflow`@deadline.c:227(`cap_scale(dl_b->bw,cap) < total_bw-old+new`) |

## §5 SMP / 类实例(00 §4 / 02 §7)

| 结论 | 证据 |
|---|---|
| cpudeadline 堆路由 | `cpudeadline.c`(cpudl_find/set,见 [`../../04-interactions.md`](../../04-interactions.md)) |
| DEFINE_SCHED_CLASS 实例 | `deadline.c:2738`(优先级链 stop>**dl**>rt>fair>idle) |
| print_dl_rq 观测 | `kernel/sched/debug.c:693`;分派 `debug.c:771`→`deadline.c:3124` |

## §6 策略型结论(非源码可完整验证)

| 结论 | 出处类型 |
|---|---|
| EDF 单核最优性 | 可调度性理论 / `Documentation/scheduler/sched-deadline.rst` |
| CBS 带宽隔离正确性 | 同上(Abeni & Buttazzo CBS 论文) |
| 带宽和判据的可调度充分条件 | `Documentation/scheduler/sched-deadline.rst` §形式化部分 |

代码只实现机制,不含最优性/隔离性证明——上述标策略型,不混入一/二/三等源码证据。

## §7 索引盲区声明

以下走 rg + Read,不单靠代码图:

- 宏符号:`DEFINE_SCHED_CLASS`(deadline.c:2738)、`for_each_sched_dl_entity`、`to_ratio`(带宽比例宏)、`SCHED_FLAG_*`/`SCHED_FLAG_RECLAIM`(uapi)、`cap_scale`/`BW_SHIFT`/`RATIO_SHIFT`/`DL_SCALE`(定标移位宏,回查 sched.h/deadline.c)、`HRTIMER_MODE_REL_HARD`。
- 间接调用:sched_class 回调经函数指针分派;`dl_task_timer`/`inactive_task_timer` 经 hrtimer 回调注册,调用图不完整——0-lag 释放走 `inactive_timer` 回调,非直接调用链。
- 数值未展开:period min/max 默认值须回查 deadline.c sysctl 表(@29);`RUNTIME_INF` 回查 `kernel/sched/sched.h`。
- 复现命令:
  - `rg -n 'DEFINE_SCHED_CLASS' kernel/sched/deadline.c`
  - `rg -n 'sched_dl_overflow|__dl_overflow' kernel/sched/deadline.c kernel/sched/core.c`
  - `rg -n 'rb_add_cached|rb_first_cached' kernel/sched/deadline.c`
  - `rg -n 'dl_runtime_exceeded|replenish_dl_entity|update_dl_entity' kernel/sched/deadline.c`
  - `rg -n 'task_non_contending|task_contending|inactive_task_timer|grub_reclaim' kernel/sched/deadline.c`
  - `rg -n '__add_running_bw|__sub_running_bw|dl_entity_overflow|update_dl_revised_wakeup' kernel/sched/deadline.c`
