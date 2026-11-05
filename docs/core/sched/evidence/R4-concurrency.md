# R4 证据:锁清单 + 锁序 + RT 差异(供 05-concurrency)

基线 6.1.158 **-rt58**。均以 kernel/sched/ 为根。

## 1. 锁清单
**结论:kernel/sched 核心结构的锁全部是 raw_spinlock_t**(除 double_lock/double_lock_irq 两个泛型辅助器形参是 spinlock_t*)。

| 锁 | 类型 | 保护 | 定义 | 粒度 |
|---|---|---|---|---|
| rq->__lock | raw_spinlock_t | 单 rq 全部调度状态 | kernel/sched/sched.h:956 | per-CPU rq |
| rt_bandwidth.rt_runtime_lock | raw_spinlock_t | RT 带宽配额 | kernel/sched/sched.h:280 | per rt_bandwidth(全局 def_rt_bandwidth rt.c:13 或 per tg) |
| rt_rq.rt_runtime_lock | raw_spinlock_t | 单 rt_rq 的 rt_time/rt_runtime | kernel/sched/sched.h:688 | per-CPU per-tg rt_rq |
| dl_bandwidth.dl_runtime_lock | raw_spinlock_t | (本树未定义 def_dl_bandwidth,成员未用) | kernel/sched/sched.h:290 | 仅类型 |
| dl_bw.lock(rd->dl_bw.lock) | raw_spinlock_t | root_domain DL 总带宽 | kernel/sched/sched.h:319(root_domain 实例 kernel/sched/sched.h:845) | per root_domain |
| cfs_bandwidth.lock(cfs_b->lock) | raw_spinlock_t | CFS 带宽配额(CFS_BANDWIDTH) | kernel/sched/sched.h:344 | per tg |
| cfs_rq.removed.lock | raw_spinlock_t ____cacheline_aligned | 迁移移除的 load/util/runnable avg | kernel/sched/sched.h:592 | per cfs_rq |
| root_domain.rto_lock | raw_spinlock_t | RT push-IPI rto_loop/rto_cpu | kernel/sched/sched.h:862 | per root_domain |
| cpudl.lock | raw_spinlock_t | DL deadline 堆 | cpudeadline.h:12 | per rd(cpudl) |
| sched_domains_mutex | mutex | 调度域拓扑 | topology.c:6 | 全局 |
| sched_energy_mutex | mutex | EAS perf_domain 重建 | topology.c:210 | 全局 |
| rt_constraints_mutex | mutex | RT 组带宽 sysctl 校验 | rt.c:2734 | 全局 |
| cfs_constraints_mutex | mutex | CFS 带宽 sysctl 校验 | core.c:10731 | 全局 |
| sched_core_mutex | mutex | core scheduling 开关 | core.c:307 | 全局 |
| uclamp_mutex | mutex | uclamp 配置 | core.c:1336 | 全局 |
| shares_mutex | mutex | tg CFS shares | fair.c:12284 | 全局 |

外部配对锁:p->pi_lock(raw_spinlock_t,include/linux/sched.h),task_rq_lock 系列先于 rq 锁取。

### 加锁器包装(非锁)
- __task_rq_lock:core.c(assert@611);已持 pi_lock 仅取 rq->__lock;extern kernel/sched/sched.h:1601
- task_rq_lock:core.c:630(pi_lock irqsave→rq->__lock)
- rq_lock/rq_lock_irq/rq_lock_irqsave:kernel/sched/sched.h:1642/1634/1626
- raw_spin_rq_lock_nested/trylock/unlock:core.c:530/553/578(经 __rq_lockp 选锁)
- rq_lockp/__rq_lockp:kernel/sched/sched.h:1220/1228(SCHED_CORE)、1316/1321(!SCHED_CORE);core-sched 开时返回 rq->core->__lock
- double_rq_lock/unlock:core.c:588(SMP)、kernel/sched/sched.h:2719/2736(!SMP);内联 kernel/sched/sched.h:2696
- double_lock_balance/_double_lock_balance:kernel/sched/sched.h:2648/2601(PREEMPTION)/2620
- double_raw_lock:kernel/sched/sched.h:2681(两 pi_lock,core.c:3256)

## 2. 锁序偏序(A→B)
| 偏序 | 等级 | 出处 |
|---|---|---|
| pi_lock→rq->__lock→hrtimer_cpu_base->lock | 一等(注释 "Lock order:") | core.c:441-446 |
| rq1->lock→rq2->lock(rq1<rq2 排序防 ABBA) | 一等(注释+结构注释 "ordered by ascending &runqueue") | core.c:448-449;kernel/sched/sched.h:950-952 |
| double_rq 排序:core-sched 先 core->cpu 再 cpu;否则 rq->cpu(rq_order_less) | 一等(注释 "AB-BA") | kernel/sched/sched.h:2562-2588;实现 core.c:588-600 |
| task_rq_lock:pi_lock(irqsave)→rq->__lock | 一等(函数注释)+二等(__task_rq_lock 内 lockdep_assert_held(pi_lock)) | core.c:628-637;assert core.c:611 |
| rt_bandwidth.rt_runtime_lock→rt_rq.rt_runtime_lock(皆 raw 嵌套) | 一等(注释 "nests inside the rq lock" kernel/sched/sched.h:279)+三等(do_balance_runtime/__disable_runtime) | kernel/sched/sched.h:279,687;代码 rt.c:798-799、880-881 |
| rq->__lock→rt_rq.rt_runtime_lock | 一等(注释 "Nests inside the rq lock") | kernel/sched/sched.h:687 |
| rq->__lock→cfs_b->lock(分发时不能嵌套) | 二等(lockdep_assert_held(cfs_b->lock) fair.c:5187,5847)+三等(fair.c:5561-5564) | fair.c:5187,5847;注释 5562 |
| src->pi_lock+dst->pi_lock(指针序 double_raw_lock)再 double_rq_lock | 三等(推断,migrate_swap_stop) | core.c:3256-3258 |
| rq->__lock→rd->rto_lock(push-IPI) | 三等(推断,tell_cpu_to_push) | rt.c:2299-2310 |
| sched_domains_mutex 最外层 | 一等(注释)+二等(lockdep_assert_held) | topology.c:2533;assert 1231,2542 |

二等补充(lockdep_assert_rq_held 反推持 rq 锁):deadline.c:238,251,265,275,1060;fair.c:5879,5898,8215,8321,8410,8432,8466,8599;core.c:733,1026,1567,1607,2360,3797,5000,5038,9375,9742;psi.c:789,1016;cpuacct.c:339;core_sched.c:248。lockdep_assert_held(pi_lock):core.c:611,1463,2604,3628;fair.c:7472。
double_rq_lock/double_lock_balance 进入时 lockdep_assert_irqs_disabled():core.c:590、kernel/sched/sched.h:2650。

## 3. 无锁/弱同步
| 路径 | 机制 | 位置 |
|---|---|---|
| rq->curr 读 | RCU(task_struct __rcu * kernel/sched/sched.h:1008),写 RCU_INIT_POINTER(core.c:6653) | 读 rcu_dereference:membarrier.c:289,362,375,474、cputime.c:999,1086、fair.c:1921,3933、core.c:3926,4454;idle rcu_assign_pointer core.c:9210 |
| rq->curr 无锁快照 | READ_ONCE 标 "unlocked access" | rt.c:1616、deadline.c:1841 |
| PSI per-CPU 统计 | seqcount 读侧 retry | init psi.c:186;读 262-269;写 801,855,879,1035,1041 |
| vtime 记账 | seqcount(vtime->seqcount) | 读 cputime.c:836,867,938,1028;写 721-822 |
| rd->rto_loop_next/start | atomic_t,注释 "updated outside of a lock" | kernel/sched/sched.h:866-869;rt.c:2246 |
| migrate_disable 计数 | per-CPU+preempt_disable,this_rq()->nr_pinned++ | core.c:2254-2269 |

## 4. RT 差异(-rt58,强制)
### 4.1 raw vs 普通 spinlock
kernel/sched 所有核心锁均 raw_spinlock_t(§1),RT 下不转 rtmutex,语义不变。kernel/sched/sched.h:2663/2672 的 double_lock/double_lock_irq 仅泛型辅助器接收 spinlock_t*,非 sched 成员。wait_queue_head.lock 是 spinlock_t(include/linux/wait.h:38,RT 下可睡眠)但属 wait 基础设施非 rq 状态。**台账"raw 语义 RT 不变"得证**。

### 4.2 PREEMPT_LAZY/RESCHED_LAZY 落点
- resched_curr_lazy 声明/桩:kernel/sched/sched.h:2354-2360
- 实现(PREEMPT_LAZY):core.c:1055-1082(set_tsk_need_resched_lazy+polling+IPI)
- __schedule 清 lazy 标志:core.c:6642 clear_tsk_need_resched_lazy(prev)
- preemptible_lazy()(RT 抢占决策):core.c:6864-6870
- SCHED_FEAT(PREEMPT_LAZY,true)仅 RT+LAZY:features.h:49-53
- lazy 计数初始化(fork/idle):core.c:4767、9221
- 宏 set/clear/test_tsk_need_resched_lazy、need_resched_lazy:include/linux/sched.h:2063-2091
- preempt_lazy_count/disable/enable:include/linux/preempt.h:211-291,335-336,474-479

### 4.3 migrate_disable/enable(RT 重点)
- migrate_disable():preempt_disable→this_rq()->nr_pinned++→migration_disabled=1→preempt_lazy_disable():core.c:2254-2269
- migrate_enable():配对递减+preempt_lazy_enable():core.c:2271-2301
- migrate_disable_switch()(切换改 cpus_allowed,注释 "Violates locking rules"):core.c:2240-2251;调用 core.c:6671
- rq_has_pinned_tasks/nr_pinned:core.c:2305-2308
- migrate-disable 语义大段注释:core.c:2700-2764
- RT/DL 对"migrate_disabled 后被抢占"处理注释:rt.c:2008、deadline.c:2272

### 4.4 其它 CONFIG_PREEMPT_RT 分支
- SCHED_NR_MIGRATE_BREAK:kernel/sched/sched.h:2442-2446(RT=8,非 RT=32)
- saved_state/rtlock 阻塞状态保存:core.c:3325 起、4048 起
- SM_MASK_PREEMPT/SM_RTLOCK_WAIT:core.c:6512-6520
- schedule_rtlock():core.c:6817-6826(RT 专有)
- TTWU_QUEUE 特性 RT 下关闭:features.h:49-50

### 未查到(如实)
- def_dl_bandwidth 全局在本树 kernel/sched 内不存在;DL 全局带宽由 per-rd dl_bw(rd->dl_bw,锁 dl_b->lock)承载;struct dl_bandwidth.dl_runtime_lock(kernel/sched/sched.h:290)仅类型无实例
- kernel/sched 内无直接引用 cpuset 互斥锁(在 kernel/cgroup/cpuset.c,经回调进入)
- 未依赖 lockdep 运行时报文;二等证据均来自静态 lockdep_assert_*
