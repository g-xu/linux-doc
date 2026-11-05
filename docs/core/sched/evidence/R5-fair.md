# R5 证据:CFS 公平调度类深读(供 sub/fair/*)

文件除注明外均 kernel/sched/fair.c。

## 0. 版本确认
- 6.1 CFS = min_vruntime + 红黑树,**无 EEVDF**。sched_entity(include/linux/sched.h:547-581)无 deadline/vlag/slice → 未 backport EEVDF。
- vruntime 比较 entity_before(fair.c:583);min_vruntime 由 update_min_vruntime(fair.c:592)推进。
- 本树 backport PREEMPT_RT/PREEMPT_LAZY,fair.c 全部抢占置位用 resched_curr_lazy()(见 §8)。

## 1. 核心数据结构
### struct sched_entity(include/linux/sched.h:547-581)
load 549 / run_node(rb_node)550 / on_rq 552 / exec_start 554 / sum_exec_runtime 555 / vruntime 556 / prev_sum_exec_runtime 557 / avg(sched_avg,SMP)579
### struct cfs_rq(kernel/sched/sched.h:550-)
load 551 / nr_running 552 / min_vruntime 558 / tasks_timeline(rb_root_cached)568 / curr 574 / next/last/skip(buddies)575-577 / avg(SMP)587
### struct sched_avg:include/linux/sched.h:493

## 2. 五回调实现(起止已核对到 })
| 回调 | 函数 | 行号 |
|---|---|---|
| enqueue | enqueue_task_fair | 6093-6177 |
| dequeue | dequeue_task_fair | 6186-6254 |
| pick_next | pick_next_task_fair | 7793-7934(__pick_next_task_fair 7936-7940) |
| tick | task_tick_fair 11870-11887→entity_tick 5069-5101 | |
| put_prev/set_next | put_prev_task_fair 7944-7953 / set_next_task_fair 12100-12121 | |

## 3. vruntime 机制
| 机制 | 函数 | 行号 |
|---|---|---|
| 累加运行→vruntime | update_curr | 882-920(vruntime+=calc_delta_fair@908,update_min_vruntime@909) |
| 新/唤醒 vruntime 初值 | place_entity | 4648-4703(START_DEBIT 加 sched_vslice@4658-4659;唤醒减 thresh@4666-4677;溢出保护@4689-4700) |
| 取最左=最小 | __pick_first_entity | 638-646(rb_first_cached) |
| 红黑树插删 | __enqueue_entity 628-631(rb_add_cached __entity_less)/__dequeue_entity 633-636(rb_erase_cached) | |
| vruntime 比较 | entity_before | 583-587((s64)(a->vruntime-b->vruntime)<0) |
| min_vruntime 推进 | update_min_vruntime | 592-618(curr 与最左 min,再 max_vruntime 保单调,u64_u32_store@616) |
辅助:max_vruntime 565、min_vruntime(helper)574。

## 4. 算法特征
- rb_root_cached(kernel/sched/sched.h:568)缓存最左 → pick=O(1)(__pick_first_entity:640),enqueue/dequeue=O(log n)
- 不变式:min_vruntime 单调不减(update_min_vruntime:616-617 max_vruntime);runnable se vruntime ≈≥ min_vruntime(place_entity 溢出保护@4689-4700)
- 退化:大量短任务唤醒→GENTLE_FAIR_SLEEPERS 只补 50%亏空(@4674-4677);cgroup 深嵌套→update_curr 逐层(se->depth include/linux/sched.h:562);nr_running 大→红黑树深+__sched_period 拉伸(@710-716)
- 参数(定义行 + 作用):
  - sysctl_sched_latency=6ms(**72**):目标调度周期,用于 __sched_period:715、place_entity thresh@4668、spread@4619。改大→吞吐↑延迟↑;改小→交互↑切换↑
  - sysctl_sched_min_granularity=0.75ms(**93**):最小运行片,__sched_period:713、check_preempt_tick:4932、sched_nr_latency@677-678。改小→抢占频繁开销↑
  - sysctl_sched_wakeup_granularity=1ms(**124**):唤醒抢占门槛,唯一用点 wakeup_gran:7584。改大→难被唤醒抢占(吞吐);改小→交互↑
  - 相关:sysctl_sched_idle_min_granularity(**102**)、sched_nr_latency(**107**,默认8)

## 5. 特性开关 features.h(CFS 相关)
| SCHED_FEAT | 行 | 默认 | 影响 |
|---|---|---|---|
| GENTLE_FAIR_SLEEPERS | 7 | true | place_entity(fair.c:4674)睡眠亏空补 50% |
| START_DEBIT | 13 | true | place_entity(fair.c:4658)新任务加 vslice |
| NEXT_BUDDY | 20 | false | check_preempt_wakeup(fair.c:7683)设 next buddy |
| LAST_BUDDY | 27 | true | fair.c:7755 设 last buddy |
| CACHE_HOT_BUDDY | 33 | true | task_hot(fair.c:8230) |
| WAKEUP_PREEMPTION | 38 | true | check_preempt_wakeup(fair.c:7710) |
| PREEMPT_LAZY | 52 | true(仅 PREEMPT_RT && PREEMPT_LAZY) | lazy 抢占语义 |
buddy 消费:set_last_buddy 7631、set_next_buddy 7642、__clear_buddies_last/next/skip 4797/4808/4819

## 6. 抢占决策
- 唤醒:check_preempt_wakeup **7662-7757**。辅助 wakeup_gran **7582-7601**(sysctl_sched_wakeup_granularity@7584)、wakeup_preempt_entity **7617-7629**。命中后 resched_curr_lazy@7742。NEXT_BUDDY@7683、WAKEUP_PREEMPTION 短路@7710、LAST_BUDDY@7755
- tick:check_preempt_tick **4909-4943**。ideal_runtime 超限@4918 resched_curr_lazy;delta_exec<min_granularity 提前返回@4932;vruntime 落后@4942 resched_curr_lazy。由 entity_tick(5069-5101,@5100)驱动

## 7. 观测点
- Tracepoints:trace_sched_stat_runtime(**914**,update_curr)、trace_sched_stick_numa(2263/2272/2280)、trace_sched_util_est_cfs_tp(4296/4312)、trace_sched_util_est_se_tp(4413)、trace_sched_overutilized_tp(6043)、trace_sched_cpu_capacity_tp(8948)
- schedstat:共 41 处。schedstat_enabled(897)、__schedstat_set(exec_max)(901)、schedstat_add(exec_clock)(906)、wait/sleep/block(933/950/961/976/993/1011)、schedstat_inc(nr_spread_over)(4620)

## 8. RT 差异
- fair.c 全部抢占置位改 resched_curr_lazy()(定义 core.c:1056;声明 kernel/sched/sched.h:2355,非 RT inline stub@2357)。落点:4918、4942(check_preempt_tick)、5088(entity_tick)、5237(HRTICK)、5988(enqueue 组带宽附近)、7742(check_preempt_wakeup)、11918、11945
- features.h:49-53 有 #ifdef CONFIG_PREEMPT_RT 分支,PREEMPT_LAZY(true)仅 CONFIG_PREEMPT_LAZY 下启用。与 git status arm64 thread_info.h/preempt.h/asm-offsets.c 改动一致(RESCHED_LAZY 标志位)
- fair.c 内无 #ifdef CONFIG_PREEMPT_RT 直接条件编译块,差异全经 resched_curr_lazy 间接体现
