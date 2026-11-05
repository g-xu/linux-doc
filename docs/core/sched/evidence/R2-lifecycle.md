# R2 证据:task 调度生命周期状态机(供 03-lifecycle)

基线 6.1.158,arm64。本树带 PREEMPT_LAZY 回移(resched_curr_lazy、clear_tsk_need_resched_lazy、TASK_FROZEN/TASK_FREEZABLE)。

## 1. 状态清单
### p->__state 位(include/linux/sched.h)
TASK_RUNNING 0x0@85 / TASK_INTERRUPTIBLE 0x1@86 / TASK_UNINTERRUPTIBLE 0x2@87 / __TASK_STOPPED 0x4@88 / __TASK_TRACED 0x8@89 / TASK_PARKED 0x40@95 / TASK_DEAD 0x80@96 / TASK_WAKEKILL 0x100@97 / TASK_WAKING 0x200@98 / TASK_NOLOAD 0x400@99 / TASK_NEW 0x800@100 / TASK_RTLOCK_WAIT 0x1000@101 / TASK_FREEZABLE 0x2000@102 / TASK_FROZEN 0x8000@104
复合:TASK_KILLABLE@115、TASK_IDLE@119、TASK_NORMAL@122;exit_state:EXIT_DEAD@90、EXIT_ZOMBIE@91

### 字段(struct task_struct)
__state@745 / on_cpu@765(SMP) / on_rq@781

### on_rq 取值(kernel/sched/sched.h)
TASK_ON_RQ_QUEUED=1@104 / TASK_ON_RQ_MIGRATING=2@105
helper:task_on_rq_queued@2070-2073 / task_on_rq_migrating@2075-2078 / task_is_running@include/linux/sched.h:131

### 状态判据
| 语义 | 判据 |
|---|---|
| runnable(在 rq) | __state==RUNNING && on_rq==QUEUED |
| running(在 CPU) | 上 + on_cpu==1 && rq->curr==p |
| sleeping | __state & (INTR|UNINTR) 且 on_rq==0 |
| 唤醒中转 | __state==TASK_WAKING(core.c:4287) |
| migrating | on_rq==TASK_ON_RQ_MIGRATING |
| 新建 | __state==TASK_NEW |
| 死亡 | __state==TASK_DEAD(do_task_dead 后) |

## 2. 迁移触发函数(core.c)
- __sched_fork@4489;sched_fork@4710(TASK_NEW@4718)
- wake_up_new_task@4833-4871(RUNNING@4839,activate_task@4857,check_preempt_curr@4859)
- try_to_wake_up@4191-4360;wake_up_process@4472-4476
- ttwu_queue@4007-4019→ttwu_do_activate@3792-3814(activate_task@3812,ttwu_do_wakeup@3813)
- ttwu_do_wakeup@3756-3789(check_preempt_curr@3759,WRITE_ONCE __state RUNNING@3760)
- ttwu_runnable@3841-3862
- __schedule@6557-6686(pick_next_task@6639)
- pick_next_task@6493-6496→__pick_next_task@5972-6008(fair 快路径@5986,for_each_class@6001)
- 阻塞出队:__schedule 内 deactivate_task(DEQUEUE_SLEEP|NOCLOCK)@6630
- do_task_dead@6688-6702(set_special_state TASK_DEAD@6691,__schedule(SM_NONE)@6696)
- schedule@6747-6758(__schedule@6754)
- enqueue_task@2088-2102(→sched_class->enqueue_task@2099);dequeue_task@2105-2120(@2119)
- activate_task@2122-2130(enqueue@2127,on_rq=QUEUED@2129);deactivate_task@2132-2137(on_rq=SLEEP?0:MIGRATING@2134,dequeue@2136)
- context_switch@5308(在 __schedule@6679 调用)
- resched_curr@1021-1043;resched_curr_lazy@1056

## 3. 逐跳交接
- fork→runnable:wake_up_new_task(core)→select_task_rq(core/负载均衡@4852)→activate_task→enqueue_task→sched_class->enqueue_task(**core→fair/rt/dl**@2099)
- 唤醒入队:try_to_wake_up(core)→select_task_rq(@4346)→ttwu_queue→ttwu_do_activate→activate_task→enqueue_task→回调
- 选任务:__schedule→pick_next_task→__pick_next_task:快路径 pick_next_task_fair(**core→fair**@5986);否则 for_each_class(stop→dl→rt→fair→idle)@6001-6005;全空 idle 兜底(BUG@6007)
- context_switch:__schedule 选出 next 且 prev!=next 后(@6679),switch_mm+switch_to,core 内部;返回经 finish_task_switch 回收 TASK_DEAD prev
- tick 抢占:scheduler_tick(core@5614)→curr->sched_class->task_tick(**core→fair**@5636)→task_tick_fair(fair.c:11870)→entity_tick(fair.c:5069)→check_preempt_tick(fair.c:4909)→resched_curr_lazy/resched_curr(**fair→core**)

## 4. 失败/回退路径
- 唤醒时仍在 rq:try_to_wake_up 中 `on_rq && ttwu_runnable goto unlock`@4254;ttwu_runnable 仅改 __state 回 RUNNING 不重入队@3849-3853
- 唤醒时仍在别 CPU(on_cpu):先置 TASK_WAKING@4300;on_cpu 真则 ttwu_queue_wakelist 走 IPI goto unlock@4310-4312;否则 smp_cond_load_acquire 自旋等@4327;自唤醒无锁快路径@4197-4215
- 唤醒迁移:选核后 task_cpu(p)!=cpu 置 WF_MIGRATED + set_task_cpu@4339-4349
- 迁移核心:set_task_cpu@3153,task_cpu!=new_cpu 调 sched_class->migrate_task_rq@3198-3199(**core→fair**:migrate_task_rq_fair)
- 抢占取消阻塞:阻塞出队受 `!(sched_mode & SM_MASK_PREEMPT) && prev_state` 守护@6606——抢占进入(SM_PREEMPT)即便 prev_state 非零也跳过 deactivate,保持 runnable;signal_pending_state 命中改回 RUNNING@6607-6609

## 5. 两条端到端路径
### (a) 普通唤醒
wake_up_process@4472→try_to_wake_up@4191→ttwu_state_match@4225→smp_rmb@4249→读 on_rq→(已在 rq:ttwu_runnable@4254/3841 改 RUNNING 结束)→否则 TASK_WAKING@4300→on_cpu 分支@4310/4327→select_task_rq@4346→set_task_cpu@4349→ttwu_queue@4353/4007→ttwu_do_activate@4017/3792→activate_task@3812→enqueue_task→回调→ttwu_do_wakeup@3813/3756:check_preempt_curr@3759→resched_curr→中断返回__schedule@6557 选中→context_switch@6679

### (b) tick 抢占
scheduler_tick@5614→task_tick@5636→task_tick_fair(fair.c:11870)→entity_tick(fair.c:5069)→check_preempt_tick(fair.c:4909)→时间片耗尽 resched_curr_lazy(fair.c:4918/4942)→resched_curr(core.c:1062→1021)set_tsk_need_resched+set_preempt_need_resched@1035-1036→返回抢占点 __schedule(SM_PREEMPT)@6557:SM_MASK_PREEMPT 置位 prev 不出队@6606→pick_next_task@6639→context_switch@6679

## __schedule 完整区间:core.c:6557-6686
