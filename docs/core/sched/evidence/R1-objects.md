# R1 证据:struct rq 字段级归属(供 02-objects)

定义锚点:`struct rq`@kernel/sched/sched.h:954;`struct sched_class`@kernel/sched/sched.h:2148。

## 1. rq 关键共享字段
| 字段 | 定义行 | 类型 | 作用 |
|---|---|---|---|
| nr_running | 962 | unsigned int | 本 CPU 可运行任务总数(三类经 add/sub_nr_running 汇总) |
| nr_switches | 981 | u64 | 上下文切换累计(仅 __schedule 写) |
| cfs | 990 | struct cfs_rq | 内嵌 CFS 队列 |
| rt | 991 | struct rt_rq | 内嵌 RT 队列 |
| dl | 992 | struct dl_rq | 内嵌 DL 队列 |
| nr_uninterruptible | 1006 | unsigned long | 全局 loadavg 分量 |
| curr | 1008 | task_struct __rcu * | 当前运行任务 |
| idle | 1009 | task_struct * | idle 任务(swapper) |
| stop | 1010 | task_struct * | stop/migration 任务 |
| clock | 1015 | u64 | rq 墙钟(update_rq_clock 刷新) |
| clock_task | 1017 | u64 | 扣 irq/steal 后任务时钟(PELT/vruntime 基准) |
| cpu_capacity | 1042 | unsigned long | 当前可用算力(SMP) |
| cpu_capacity_orig | 1043 | unsigned long | 满算力基准(SMP) |
| sd | 1040 | sched_domain __rcu * | 最低层 sched_domain |

注:6.1 已删 `cpu_load[]` 数组。负载信号落在 avg_rt(1063)/avg_dl(1064)/misfit_task_load(1050)。

## 2. 写入方取证(四形态)
### rq->curr(全经 RCU,无裸 =)
- 直接 `=`:0 命中
- RCU_INIT_POINTER:core.c:6654(__schedule 切 next)
- rcu_assign_pointer:core.c:9210(init_idle 设 idle)
- WRITE_ONCE:0 命中

### rq->nr_running(全经访问器)
- 访问器定义:add_nr_running@kernel/sched/sched.h:2407(体内 2411 写),sub_nr_running@kernel/sched/sched.h:2426(体内 2428 写)
- add_nr_running 调用点:stop_task.c:57 / rt.c:1122 / deadline.c:1515 / fair.c:5470 / fair.c:6154
- sub_nr_running 调用点:stop_task.c:63 / rt.c:1103 / deadline.c:1529 / fair.c:5385 / fair.c:6245
- 直接 `=`:core.c:9906(init 归零)
- idle 类不增减(idle 任务不计入 nr_running)

### rq->clock / clock_task
- rq->clock:core.c:747(`+= delta`,在 update_rq_clock@729,持锁 lockdep_assert_rq_held@733)
- rq->clock_task:core.c:720(在 update_rq_clock_task@673,被 update_rq_clock:748 调)
- 注:clock.c:161/277 是 scd->clock,不同载体

## 3. 同名字段陷阱
| 组 | 载体 A | 载体 B | 判别 |
|---|---|---|---|
| curr | rq->curr@1008 task_struct __rcu * | cfs_rq->curr@574 裸 sched_entity * | 前缀;A 需 rcu_dereference,B 直读;裸 `-> curr =` 只命中 cfs_rq(fair.c:4963/5065) |
| nr_running | rq->nr_running@962 CPU 级 | cfs_rq->nr_running@552 / rt_rq->rt_nr_running@665 / dl_rq->dl_nr_running@708 | 只有 cfs 与 rq 真同名;rt/dl 各有独立名 |
| idle | rq->idle@1009 task_struct * | cfs_rq->idle@632 int(task_group idle 标志) | 类型即判别;A 写 core.c:9209,B 写 fair.c:12365 |

## 4. RCU 字段(rq 内共 2 个)
| 字段 | 定义行 | 写侧 | 读侧(不持锁) | 原因 |
|---|---|---|---|---|
| curr | 1008 | RCU_INIT_POINTER core.c:6654、rcu_assign_pointer core.c:9210 | membarrier.c:474、cputime.c:999/1086、fair.c:1921/3933、core.c:3926(rcu_dereference) | 写持 rq lock,远程 CPU(membarrier IPI/cputime/选核)需不持目标 rq lock 读 curr |
| sd | 1040 | rcu_assign_pointer(topology.c,持 sched_domains_mutex) | rcu_dereference_check(负载均衡遍历域) | 拓扑可热重建 |

补充无锁读:READ_ONCE(rq->curr)@rt.c:1616、deadline.c:1841、fair.c:2710(注释 "unlocked access")。

## 5. sched_class 回调表(kernel/sched/sched.h:2148,声明顺序)
uclamp_enabled 2151(UCLAMP) / enqueue_task 2154 / dequeue_task 2155 / yield_task 2156 / yield_to_task 2157 / check_preempt_curr 2159 / pick_next_task 2161 / put_prev_task 2163 / set_next_task 2164 / balance 2167(SMP) / select_task_rq 2168(SMP) / pick_task 2170(SMP) / migrate_task_rq 2172(SMP) / task_woken 2174(SMP) / set_cpus_allowed 2176(SMP) / rq_online 2180 / rq_offline 2181 / find_lock_rq 2183 / task_tick 2186 / task_fork 2187 / task_dead 2188 / switched_from 2195 / switched_to 2196 / prio_changed 2197 / get_rr_interval 2200 / update_curr 2203 / task_change_group 2206(FAIR_GROUP)

- 宏 DEFINE_SCHED_CLASS@kernel/sched/sched.h:2232;extern 5 个@2249-2253;for_each_class@2244;链接段逆序(注释@2228)
- 实现:stop stop_task.c:117 / dl deadline.c:2738 / rt rt.c:2692 / fair fair.c:12443 / idle idle.c:499
