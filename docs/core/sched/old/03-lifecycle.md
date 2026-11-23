# 03 · 任务生命周期状态机(时间维度)

> 本篇是 sched 两层文档集的时间维度骨架,回答"一个任务从生到死流经哪些子模块,每一跳由谁交给谁"。它在 [`04-interactions.md`](04-interactions.md)(空间维度)之后写:04 给出"谁和谁有关系",本篇给出"关系在时间上怎么串起来"。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §0 本篇选定的核心对象

sched 的核心对象只有一个:任务(`task_struct` 的调度视图)。它是唯一被全部调度类和 core 共同读写、且有完整生命周期的对象。`struct rq`(per-CPU 主结构)是承载对象不是流动对象,其字段归属见 [`02-objects.md`](02-objects.md)。

| 对象 | 结构体 | 定义位置 | 为什么选它 | 本篇是否覆盖 |
|---|---|---|---|---|
| 任务 | `struct task_struct`(调度字段) | `include/linux/sched.h`(`__state`:745,`on_rq`:781,`on_cpu`:765) | 唯一流经全部子模块、有生死周期的对象 | 是(完整五节) |
| 运行队列 | `struct rq` | `kernel/sched/sched.h:954` | 承载容器,非流动对象 | 否 → [`02-objects.md`](02-objects.md) |
| 调度实体 | `struct sched_entity` | `include/linux/sched.h:547` | fair 私有,非跨模块 | 否 → [`sub/fair/02-internals.md`](sub/fair/02-internals.md) |

## §1 状态清单

状态必须有代码判据。`p->__state` 位定义在 `include/linux/sched.h`;`on_rq` 取值在 `kernel/sched/sched.h`。

| 状态 | 判据字段 | 判据表达式 | 定义位置 | 说明 |
|---|---|---|---|---|
| 新建 | `__state` | `p->__state == TASK_NEW` | `include/linux/sched.h:100`(0x800) | fork 中,尚不可被唤醒 |
| runnable(在 rq) | `__state`+`on_rq` | `__state==TASK_RUNNING && on_rq==TASK_ON_RQ_QUEUED` | `include/linux/sched.h:85`;`kernel/sched/sched.h:104` | 在就绪队列排队,尚未拿到 CPU |
| running(在 CPU) | 上 +`on_cpu`+`rq.curr` | runnable && `on_cpu==1` && `rq->curr==p` | `include/linux/sched.h:765`(on_cpu) | 正在执行 |
| sleeping | `__state`+`on_rq` | `__state & (TASK_INTERRUPTIBLE\|TASK_UNINTERRUPTIBLE)` && `on_rq==0` | `include/linux/sched.h:86/87` | 阻塞,已出队 |
| 唤醒中转 | `__state` | `p->__state == TASK_WAKING` | `include/linux/sched.h:98`(0x200) | ttwu 已认领、尚未入队(`core.c:4287/4300`) |
| migrating | `on_rq` | `on_rq == TASK_ON_RQ_MIGRATING` | `kernel/sched/sched.h:105`(=2) | 跨 rq 搬迁中 |
| dead | `__state` | `p->__state == TASK_DEAD` | `include/linux/sched.h:96`(0x80) | `do_task_dead` 后,等 `finish_task_switch` 回收 |

判据 helper:`task_on_rq_queued`(`kernel/sched/sched.h:2070`)、`task_on_rq_migrating`(`:2075`)、`task_is_running`(`include/linux/sched.h:131`)。RT 树新增状态位(`TASK_FROZEN` 0x8000 `:104`、`TASK_RTLOCK_WAIT` 0x1000 `:101`)不进主状态机,见 [`05-concurrency.md`](05-concurrency.md) §7。

## §2 状态机总图

节点 = §1 状态,边上标触发函数。

```
                        wake_up_new_task
       ┌────────┐   (activate_task)      ┌──────────────┐
       │ 新建   │ ─────────────────────> │  runnable    │ <──────────┐
       │TASK_NEW│                        │ (在 rq 排队) │            │
       └────────┘                        └──────────────┘            │
                                          │       ^                   │
                          pick_next_task  │       │ __schedule        │
                          (context_switch)│       │ (SM_PREEMPT       │
                                          v       │  抢占,不出队)    │
                                    ┌──────────┐  │                   │
              ┌──────────────────── │ running  │ ─┘                   │
              │  schedule()         │(在 CPU)  │                      │
              │  deactivate_task    └──────────┘                      │
              │  (DEQUEUE_SLEEP)      │    │                          │
              v                       │    │ do_task_dead             │
       ┌──────────────┐               │    v                         │
       │  sleeping    │               │  ┌────────┐                   │
       │ (不在 rq)    │               │  │ dead   │                   │
       └──────────────┘               │  │TASK_   │                   │
              │                        │  │DEAD    │                   │
              │ try_to_wake_up         │  └────────┘                   │
              │ (TASK_WAKING → select  │                               │
              │  → activate_task)      │  set_task_cpu                 │
              └────────────────────────┴──> [ migrating ] ────────────┘
                                              (TASK_ON_RQ_MIGRATING)
```

图注:`migrating` 是 runnable 的跨 CPU 中转子态(deactivate 非 SLEEP 时经过),回到目标 rq 的 runnable。`唤醒中转`(TASK_WAKING)是 sleeping→runnable 路径上的瞬时态,并入唤醒边。

上图是"状态怎么转"的静态视图。下图把一次 `__schedule` 调用在时间轴上展开(纵向为时间),回答"从被触发到切走,内部按什么次序做了什么"——每一步对应 §3 的跳编号:

```
   时间↓   __schedule(sched_mode)   core.c:6557                       跳
   ────────────────────────────────────────────────────────────────────
   t0   │ local_irq_disable(); rq_lock(rq); update_rq_clock(rq)        (进入)
        │   prev = rq->curr
        ▼
   t1   │ 处理 prev 状态:                                              #6/#7
        │   if (prev 非 RUNNING && !抢占模式)                          
        │       有信号 → 改回 RUNNING(不出队)      ── signal_pending
        │       否则   → deactivate_task(DEQUEUE_SLEEP)  prev 出队 →sleeping
        │   抢占模式(SM_PREEMPT) → 跳过出队, prev 保持 runnable
        ▼
   t2   │ next = pick_next_task(rq, prev)          core.c:6639        #4
        │   快路径: 全是 CFS → pick_next_task_fair(:5986)
        │   通用:   for_each_class  stop→dl→rt→fair→idle(:6001)
        │   全空 → idle 兜底(:5993)
        ▼
   t3   │ clear_tsk_need_resched(prev)
        ▼
   t4   │ if (prev != next):                                          #5
        │     rq->curr = next(RCU 发布)   nr_switches++
        │     context_switch(rq, prev, next)     core.c:6679
        │       ├ switch_mm  切地址空间(内核线程借 active_mm)
        │       └ switch_to  切寄存器/栈  (arm64: cpu_switch_to)
        │       └ finish_task_switch  回收 prev(若 TASK_DEAD)         #8
        │ else: 无需切换, 解锁返回
   ────────────────────────────────────────────────────────────────────
   RT 差异: t1 的置位路径用 resched_curr_lazy(见 [`05-concurrency.md`](05-concurrency.md) §7)
```

## §3 逐跳交接表

每条边一行。触发函数带行号;交出方/接手方是本篇关系落点,均能在 [`04-interactions.md`](04-interactions.md) §2 找到对应格子。

| # | 起状态 | 终状态 | 触发函数(带行号) | 交出方 | 接手方 | 失败/回退路径 |
|---|---|---|---|---|---|---|
| 1 | (无) | 新建 | `sched_fork` `core.c:4710`(`__state=TASK_NEW` `4718`,定 sched_class `4749-4753`) | core | core | fork 失败 → 任务从不入队,`__sched_fork` 初始化被丢弃 |
| 2 | 新建 | runnable | `wake_up_new_task` `core.c:4833`(置 RUNNING `4839`;`select_task_rq` `4852`;`activate_task` `4857`) | core | fair/rt/dl(经 `enqueue_task` `2099`) | 无可运行 CPU → select 兜底回本 CPU |
| 3 | sleeping | runnable | `try_to_wake_up` `core.c:4191`(TASK_WAKING `4300`;`select_task_rq` `4346`;`ttwu_do_activate` `4017`→`activate_task` `3812`) | core | fair/rt/dl(经 `enqueue_task`) | 已在 rq → `ttwu_runnable` `3841` 仅改 RUNNING 不重入队;目标仍在别 CPU 跑 → `smp_cond_load_acquire` 自旋等 `4327` 或 IPI 远端入队 `4310` |
| 4 | runnable | running | `__schedule` `core.c:6557`→`pick_next_task` `6639`→`__pick_next_task` `5972`(快路径 `pick_next_task_fair` `5986`;否则 `for_each_class` `6001`) | core | fair/rt/dl/idle-stop | 全类无任务 → idle 兜底(`5993`,BUG 保护 `6007`) |
| 5 | running | (切走) | `context_switch` `core.c:5308`(在 `__schedule` `6679` 调用,`switch_mm`+`switch_to`) | core | core(`finish_task_switch` 回收 prev) | switch 中不可失败;prev 若 TASK_DEAD 在此回收 |
| 6 | running | runnable | `__schedule(SM_PREEMPT)`:`SM_MASK_PREEMPT` 置位 → 不出队(`core.c:6606`),prev 保持 runnable | fair/rt/dl(经 `resched_curr(_lazy)`) | core | 抢占取消:`signal_pending_state` 命中 → 改回 RUNNING `6607-6609` |
| 7 | running | sleeping | `schedule` `core.c:6747`→`__schedule` 内 `deactivate_task(DEQUEUE_SLEEP\|NOCLOCK)` `6630`(→ `dequeue_task` `2119`) | core | fair/rt/dl(经 `dequeue_task`) | 出队受 `!(sched_mode & SM_MASK_PREEMPT) && prev_state` 守护 `6606`——抢占进入则跳过,不睡 |
| 8 | running | dead | `do_task_dead` `core.c:6688`(`set_special_state(TASK_DEAD)` `6691`;`__schedule(SM_NONE)` `6696`) | core | core(`finish_task_switch` 释放) | 不返回;`__schedule` 选出的 next 接管 CPU |
| 9 | runnable | migrating | `set_task_cpu` `core.c:3153`(`task_cpu!=new_cpu` → `migrate_task_rq` `3198`;`deactivate_task` 非 SLEEP → `on_rq=MIGRATING` `2134`) | core | fair(经 `migrate_task_rq_fair`) | 目标 rq 拒收 → 停原 CPU;负载均衡另择 |
| 10 | running | runnable(tick 抢占前置) | `scheduler_tick` `core.c:5614`→`task_tick` `5636`→`task_tick_fair`(`fair.c:11870`)→`check_preempt_tick`(`fair.c:4909`)→`resched_curr_lazy`(`fair.c:4918/4942`) | fair | core(置 need_resched,待跳 #6) | 时间片未耗尽 → 不置位,继续跑 |

## §4 完整路径走查

### §4.1 路径一:普通唤醒(sleeping → running,顺利路径)

触发场景:一个在等 IO/锁的任务,其等待条件满足,被 `wake_up_process`/`wake_up` 唤醒。

| 序 | 跳 | 发生了什么 | 关键代码 |
|---|---|---|---|
| 1 | 跳 #3 | `try_to_wake_up` 校验目标 `__state`,读 `on_rq` | `core.c:4191`;`ttwu_state_match` `4225`;`smp_rmb` `4249` |
| 2 | 跳 #3 | 置 TASK_WAKING,处理 on_cpu 分支,选核 | `core.c:4300`;`4310/4327`;`select_task_rq` `4346` |
| 3 | 跳 #3 | 入队 + 置 RUNNING + 抢占检查 | `ttwu_do_activate` `4017`→`activate_task` `3812`→`enqueue_task` 回调;`ttwu_do_wakeup` `3756`:`check_preempt_curr` `3759` |
| 4 | 跳 #4 | 中断/抢占点返回 → `__schedule` 选中被唤醒任务 | `core.c:6557`;`pick_next_task` `6639` |
| 5 | 跳 #5 | 上下文切换到该任务 | `context_switch` `core.c:6679` |

分叉点:跳 #3 若发现任务已在 rq(`on_rq` 非零),走 `ttwu_runnable`(`core.c:4254/3841`)直接改 RUNNING 结束,不入队不切换。

### §4.2 路径二:时钟 tick 抢占(running → runnable → 重新选,压力路径)

触发场景:一个 CFS 任务持续运行,时间片耗尽,tick 中断触发抢占。

| 序 | 跳 | 发生了什么 | 关键代码 |
|---|---|---|---|
| 1 | 跳 #10 | tick 中断 → 分派到当前类的 `task_tick` | `scheduler_tick` `core.c:5614`;`task_tick` `5636` |
| 2 | 跳 #10 | fair 判定时间片,置 need_resched(lazy) | `task_tick_fair` `fair.c:11870`→`entity_tick` `fair.c:5069`→`check_preempt_tick` `fair.c:4909`→`resched_curr_lazy` `fair.c:4918/4942` |
| 3 | 跳 #6 | 返回抢占点,`__schedule(SM_PREEMPT)`,prev 不出队保持 runnable | `core.c:6557`;`SM_MASK_PREEMPT` 守护 `6606` |
| 4 | 跳 #4 | `pick_next_task` 重新选(可能选回原任务或换人) | `core.c:6639` |
| 5 | 跳 #5 | 若换人则 `context_switch` | `core.c:6679` |

分叉点:跳 #6,若 `signal_pending`(`6607`)则任务保持 RUNNING;RT 树上 `resched_curr_lazy` 与 `resched_curr` 的差异决定抢占时机(见 [`05-concurrency.md`](05-concurrency.md) §7)。

## §5 异常与回退

| 异常 | 发生在哪一跳 | 对象状态如何 | 回退动作 | 谁负责清理 | 泄漏风险 |
|---|---|---|---|---|---|
| fork 失败 | 跳 #1 前 | 停在"未入队",无 rq 引用 | 释放 task_struct | fork 错误路径(非 sched) | 无(未曾入队) |
| 唤醒时任务仍在别 CPU 运行 | 跳 #3 | TASK_WAKING 瞬时态 | `smp_cond_load_acquire(&on_cpu)` 自旋等对方 `__schedule` 完成(`core.c:4327`),或 IPI 远端排队(`4310`) | try_to_wake_up 自身 | 无(有序等待) |
| 抢占取消阻塞 | 跳 #6/#7 | 本欲 sleeping,被抢占打断 | `SM_MASK_PREEMPT` 守护跳过 `deactivate_task`,保持 runnable(`core.c:6606`) | `__schedule` | 无(任务留在 rq) |
| signal 打断睡眠 | 跳 #7 | 出队前 `__state` 被改回 RUNNING | `signal_pending_state` 判定 `core.c:6607-6609` | `__schedule` | 无(不出队) |
| 迁移中目标 rq 变动 | 跳 #9 | TASK_ON_RQ_MIGRATING | 停在原 CPU,均衡器下轮重试 | load-balance | 低(migrating 态短暂;RT 树 migrate_disable 期间禁迁移,见 [`05-concurrency.md`](05-concurrency.md) §7) |
| prev 为 TASK_DEAD 未回收 | 跳 #5/#8 | dead,栈待释放 | `finish_task_switch` 在下次切换后回收 | 接手任务的 core 上下文 | 低(延迟一跳回收,设计如此) |

