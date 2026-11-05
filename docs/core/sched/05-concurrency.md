# 05 · 全局锁序与并发约束(并发维度)

> 本篇是 sched 两层文档集的并发维度骨架,回答"这些锁一起用会不会死锁、哪些路径免锁、RT 树上有什么不同"。输入是 [`04-interactions.md`](04-interactions.md) §4 的共享字段总表。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**),arm64。

## §0 本篇覆盖范围

| 项 | 说明 |
|---|---|
| 锁的数量 | kernel/sched 内 raw_spinlock 类 9 组 + mutex 类 7 个(§1) |
| 是否打了 RT 补丁 | 是,`-rt58`(基线判据见 [`99-evidence.md`](99-evidence.md) §1) |
| 覆盖的上下文 | 进程上下文 + tick/软中断 + hrtimer;NMI 不涉及 sched 锁 |
| 未覆盖 | cpuset 互斥锁(在 `kernel/cgroup/cpuset.c`,经回调进入,不在本子系统);wait_queue 的 `spinlock_t`(属 wait 基础设施) |

关键结论(前置):**kernel/sched 核心数据结构的锁全部是 `raw_spinlock_t`**(`kernel/sched/sched.h:2663/2672` 的 `double_lock`/`double_lock_irq` 仅是接收 `spinlock_t*` 的泛型辅助器,不代表 sched 自身有 `spinlock_t` 成员)。因此 RT 树上这些锁语义不变——调度器自身不能睡眠是 RT 化必须保留的底线(见 [`references/methodology.md`](../../.qoder/skills/kernel-core-subsystem-docs/references/methodology.md) §F)。

## §1 锁清单

| 锁 | 类型 | 保护什么 | 定义位置 | 粒度 |
|---|---|---|---|---|
| `rq->__lock` | `raw_spinlock_t` | 单个 rq 全部调度状态(nr_running、curr、各类子队列) | `kernel/sched/sched.h:956` | per-CPU rq |
| `rt_bandwidth.rt_runtime_lock` | `raw_spinlock_t` | RT 带宽全局/组配额 | `kernel/sched/sched.h:280` | per rt_bandwidth(全局 `def_rt_bandwidth` `rt.c:13`) |
| `rt_rq.rt_runtime_lock` | `raw_spinlock_t` | 单 rt_rq 的 rt_time/rt_runtime | `kernel/sched/sched.h:688` | per-CPU per-tg rt_rq |
| `dl_bw.lock`(`rd->dl_bw.lock`) | `raw_spinlock_t` | root_domain 的 DL 总带宽 | `kernel/sched/sched.h:319`(实例 `root_domain` `kernel/sched/sched.h:845`) | per root_domain |
| `cfs_bandwidth.lock`(`cfs_b->lock`) | `raw_spinlock_t` | CFS 带宽配额(`CONFIG_CFS_BANDWIDTH`) | `kernel/sched/sched.h:344` | per task_group |
| `cfs_rq.removed.lock` | `raw_spinlock_t` `____cacheline_aligned` | 迁移移除的 load/util/runnable 平均值 | `kernel/sched/sched.h:592` | per cfs_rq |
| `root_domain.rto_lock` | `raw_spinlock_t` | RT push-IPI 的 rto_loop/rto_cpu | `kernel/sched/sched.h:862` | per root_domain |
| `cpudl.lock` | `raw_spinlock_t` | DL 最早/最晚 deadline 堆 | `cpudeadline.h:12` | per root_domain(cpudl) |
| `p->pi_lock` | `raw_spinlock_t` | 任务的调度属性(定义在 `include/linux/sched.h`) | — | per task |
| `sched_domains_mutex` | `struct mutex` | 调度域拓扑 | `topology.c:6` | 全局 |
| `sched_energy_mutex` | `struct mutex` | EAS perf_domain 重建 | `topology.c:210` | 全局 |
| `rt_constraints_mutex` | `struct mutex` | RT 组带宽 sysctl 校验 | `rt.c:2734` | 全局 |
| `cfs_constraints_mutex` | `struct mutex` | CFS 带宽 sysctl 校验 | `core.c:10731` | 全局 |
| `sched_core_mutex` | `struct mutex` | core scheduling 开关 | `core.c:307` | 全局 |
| `uclamp_mutex` | `struct mutex` | uclamp 配置 | `core.c:1336` | 全局 |
| `shares_mutex` | `struct mutex` | task_group CFS shares | `fair.c:12284` | 全局 |

`spinlock_t` 与 `raw_spinlock_t` 精确核对:`rg -nw 'raw_spinlock_t' kernel/sched/*.h` 命中上表全部;`rg -nw 'spinlock_t' kernel/sched/*.h` 仅命中 `double_lock`/`double_lock_irq` 形参(非成员)。计数纪律:子串包含会把 `raw_spinlock_t` 误计入 `spinlock_t`,必须带词边界。

加锁器(非锁本身):`task_rq_lock` `core.c:630`(`pi_lock`→`rq->__lock`)、`__task_rq_lock`(已持 pi_lock 仅取 rq 锁,assert `core.c:611`)、`rq_lock` 系列 `kernel/sched/sched.h:1626-1642`、`double_rq_lock` `core.c:588`、`double_lock_balance` `kernel/sched/sched.h:2648`、`double_raw_lock` `kernel/sched/sched.h:2681`、`rq_lockp`/`__rq_lockp`(core-sched 选锁 `kernel/sched/sched.h:1220/1316`)。

## §2 全局锁序偏序图

`A → B` 表示"持有 A 时可以再获取 B"。

```
                     sched_domains_mutex  (最外层,hotplug + 域重建)
                              │
                              v
   p->pi_lock ──> rq->__lock ──> hrtimer_cpu_base->lock
                     │  │
                     │  ├──> rt_rq.rt_runtime_lock ──> (嵌套) rt_bandwidth.rt_runtime_lock
                     │  ├──> cfs_b->lock            (仅非分发路径)
                     │  └──> rd->rto_lock           (push-IPI)
                     │
   double_rq_lock:  rq(cpu_a)->__lock ──> rq(cpu_b)->__lock   [cpu_a < cpu_b, 防 ABBA]
```

### §2.1 偏序关系取证表

| 边(A→B) | 证据等级 | 出处 | 依据 |
|---|---|---|---|
| `pi_lock → rq->__lock → hrtimer_cpu_base->lock` | 一等 | `core.c:441-446` | 显式注释 "Lock order:" |
| `rq1->lock → rq2->lock`(rq1<rq2) | 一等 | `core.c:448-449`;`kernel/sched/sched.h:950-952` | 注释 + 结构注释 "must be ordered by ascending &runqueue" |
| double_rq 排序规则(core-sched 先 `core->cpu` 再 `cpu`) | 一等 | `kernel/sched/sched.h:2562-2588`;实现 `core.c:588-600` | 注释 "not have {0,2},{1,3} turn into AB-BA" |
| `task_rq_lock`:`pi_lock`(irqsave)→ `rq->__lock` | 一等 + 二等 | `core.c:628-637`;assert `core.c:611` | 函数注释 + `lockdep_assert_held(&p->pi_lock)` |
| `rt_bandwidth.rt_runtime_lock → rt_rq.rt_runtime_lock` | 一等 + 三等 | 注释 `kernel/sched/sched.h:279,687`;代码 `rt.c:798-799,880-881` | 注释 "nests inside" + 调用链 `do_balance_runtime` |
| `rq->__lock → rt_rq.rt_runtime_lock` | 一等 | `kernel/sched/sched.h:687` | 注释 "Nests inside the rq lock" |
| `rq->__lock → cfs_b->lock`(仅非分发路径) | 二等 + 三等 | assert `fair.c:5187,5847`;注释 `fair.c:5562` | `lockdep_assert_held(&cfs_b->lock)` + "can't nest cfs_b->lock while distributing" |
| `src->pi_lock + dst->pi_lock`(指针序)→ `double_rq_lock` | 三等(推断) | `core.c:3256-3258` | `migrate_swap_stop` 调用链 |
| `rq->__lock → rd->rto_lock`(push-IPI) | 三等(推断) | `rt.c:2299-2310` | `tell_cpu_to_push` 在持 rq 锁上下文取 rto_lock |
| `sched_domains_mutex` 最外层 | 一等 + 二等 | 注释 `topology.c:2533`;assert `topology.c:1231,2542` | "Call with hotplug lock and sched_domains_mutex held" |

`double_rq_lock`/`double_lock_balance` 进入时要求 `lockdep_assert_irqs_disabled()`(`core.c:590`、`kernel/sched/sched.h:2650`)。

## §3 逐锁使用点

| 锁 | 加锁函数 | 临界区做什么 | 可睡眠 | 中断上下文可用 | 典型持有时长 |
|---|---|---|---|---|---|
| `rq->__lock` | `rq_lock` / `rq_lock_irqsave` / `raw_spin_rq_lock` | 入队/出队/选任务/更新时钟 | 否 | 是(tick/hrtimer 内) | 短(单次调度决策) |
| `p->pi_lock` | `raw_spin_lock_irqsave`(经 `task_rq_lock`) | 保护任务调度属性、跨 rq 迁移前置 | 否 | 是 | 短 |
| `rt_rq.rt_runtime_lock` | `raw_spin_lock` | RT 带宽借还、rt_time 累加 | 否 | 是 | 短 |
| `cfs_b->lock` | `raw_spin_lock_irqsave` | CFS 带宽配额分发/回收 | 否 | 是(定时器) | 中(遍历 throttled 队列) |
| `dl_bw.lock` | `raw_spin_lock` | DL 带宽准入 | 否 | 是 | 短 |
| `cpudl.lock` / `rto_lock` | `raw_spin_lock` | DL 堆维护 / RT push-IPI 游标 | 否 | 是 | 短 |
| `sched_domains_mutex` | `mutex_lock` | 域树重建、rd 附着 | 是 | 否 | 长(重建整棵树) |

"可睡眠"列填的是非 RT 结论;RT 差异见 §7。上表 raw_spinlock 组在 RT 上"可睡眠=否"不变。

## §4 违例反例

### §4.1 反例:两个 rq 锁不按 CPU 序获取(ABBA 死锁)

```c
/* 错误示范,不要照抄 */
/* CPU0 上: */              /* CPU1 上同时: */
raw_spin_rq_lock(rq0);      raw_spin_rq_lock(rq1);
raw_spin_rq_lock(rq1);      raw_spin_rq_lock(rq0);  /* 交叉等待 */
```

- 为什么死锁:CPU0 持 rq0 求 rq1,CPU1 持 rq1 求 rq0,互相等待。
- lockdep 会报:`possible circular locking dependency detected`(未实测,依 lockdep 通用行为标注)。
- 正确写法:一律用 `double_rq_lock`(`core.c:588`),它按 `rq_order_less`(`kernel/sched/sched.h:2562-2588`)排序后再依次取锁——core-sched 下先按 `core->cpu` 再按 `cpu`,否则按 `rq->cpu`。这正是 §2.1 那条一等偏序的存在理由。

### §4.2 反例:分发 CFS 带宽时嵌套 cfs_b->lock

- 内核在 `fair.c:5561-5564` 显式注释 "we can't nest cfs_b->lock while distributing":分发 runtime 时已持 rq 锁遍历 throttled cfs_rq,若此时再取另一 cfs_b->lock 会与其他路径的 `cfs_b->lock → rq->__lock` 顺序冲突。
- 正确写法:分发路径只在持 rq 锁时操作本 rq 的 cfs_rq,不反向嵌套 cfs_b->lock。

## §5 无锁 / 弱同步路径

| 路径 | 免锁手段 | 为什么安全 | 代价 |
|---|---|---|---|
| `rq->curr` 读 | RCU(`task_struct __rcu *` `kernel/sched/sched.h:1008`) | 写侧 `RCU_INIT_POINTER`(`core.c:6653`)持 rq 锁发布;远程读者(membarrier `membarrier.c:474`、cputime `cputime.c:999`、选核 `fair.c:1921`)用 `rcu_dereference`。RCU 保证"指针指向的对象不被释放",不保证"curr 不被换"——读者读到的是某一时刻的合法快照 | 可能读到略旧的 curr |
| `rq->curr` 无锁快照 | `READ_ONCE`,注释 "unlocked access" | 唤醒抢占检查(`rt.c:1616`、`deadline.c:1841`)只需一致读一个指针,不需锁 | 同上 |
| PSI per-CPU 统计 | seqcount(`psi.c:186`) | 读侧(`psi.c:262-269`)读到撕裂值时 retry;写侧持 rq 锁(`psi.c:801` 等) | 读者偶尔重试 |
| vtime 记账 | seqcount(`vtime->seqcount`) | 读(`cputime.c:836`)retry,写(`cputime.c:721-822`)成对 | 读者偶尔重试 |
| `rd->rto_loop_next/start` | `atomic_t`,注释 "updated outside of a lock" | push-IPI 游标单字段原子推进(`rt.c:2246`) | 不能保护多字段一致 |
| migrate_disable 计数 | per-CPU + `preempt_disable`(非锁) | `this_rq()->nr_pinned++`(`core.c:2254-2269`)靠"只有本 CPU 访问"成立,RT 上尤其关键(见 §7) | 迁移期间任务钉在本 CPU |

RCU 那行的关键区别:`rcu_read_lock` 保护的是"curr 指向的 task_struct 不被释放",不保护"curr 字段不被改"。持 rq 锁的读者由锁提供顺序,无锁读者接受快照语义——这不是代码不一致。

## §6 上下文约束表

| 函数 | 进程上下文 | 软中断/tick | 硬中断 | NMI | 可持锁调用 | 可睡眠 |
|---|---|---|---|---|---|---|
| `__schedule` | 是 | 否(经 need_resched 延迟) | 否 | 否 | 取 rq->__lock | 否(判据:持 raw_spinlock) |
| `try_to_wake_up` | 是 | 是(可从中断唤醒) | 是 | 否 | 取 pi_lock+rq 锁 | 否 |
| `scheduler_tick` | — | 是(tick) | — | 否 | 取 rq 锁 | 否 |
| `enqueue_task_*` | 是 | 是 | 是 | 否 | 已持 rq 锁 | 否 |
| `sched_setscheduler`(经 mutex) | 是 | 否 | 否 | 否 | 取 mutex | 是(mutex 可睡眠) |

判据:raw_spinlock 临界区内 `lockdep_assert_rq_held`(如 `core.c:733,1026`;`fair.c:5879` 等)反推"该路径持 rq 锁 → 不可睡眠"。mutex 路径 `might_sleep` 反推可睡眠。

## §7 RT 差异(-rt58,强制)

| 项 | 非 RT | PREEMPT_RT `-rt58` | 影响哪些结论 |
|---|---|---|---|
| `raw_spinlock_t`(rq/rt_rq/cfs_b/dl_bw/rto/cpudl/pi_lock) | 关抢占忙等 | **不变** | §3 全表"可睡眠=否"、§6 |
| `spinlock_t` | 关抢占忙等 | 转 rtmutex 可睡眠 | kernel/sched 核心结构无此类成员(§1),故本篇不受影响;wait_queue_head.lock 属 wait 基础设施 |
| 抢占时机 | need_resched | 新增 `resched_curr_lazy`(`core.c:1055-1082`)+ `TIF_NEED_RESCHED_LAZY` | §2 偏序成立前提不变,但 fair 全部抢占置位改用 lazy 版(见下) |
| 迁移 | 无 migrate_disable | `migrate_disable/enable`(`core.c:2254-2301`)钉住任务 | §5 per-CPU 行、03 篇跳 #9 |
| 均衡持锁时长 | `SCHED_NR_MIGRATE_BREAK=32` | `=8`(`kernel/sched/sched.h:2442-2446`,降低持锁时长) | load-balance 每轮迁移数 |
| rtlock 阻塞 | 无 | `saved_state` 保存/恢复(`core.c:3325,4048`);`schedule_rtlock`(`core.c:6817`) | 任务状态机新增 `TASK_RTLOCK_WAIT` |

逐行回访(阶段 1 登记的 RT 改动文件:core.c/fair.c/features.h/sched.h):

- fair.c 全部抢占置位用 `resched_curr_lazy()` 而非 `resched_curr()`:落点 `fair.c:4918,4942`(check_preempt_tick)、`5088`(entity_tick)、`5237`(HRTICK)、`7742`(check_preempt_wakeup)。定义 `core.c:1056`,声明 `kernel/sched/sched.h:2355`(非 RT 时为 inline stub `kernel/sched/sched.h:2357`)。这是 fair 与 RT 补丁交互的唯一形态——fair.c 内无 `#ifdef CONFIG_PREEMPT_RT` 直接条件编译块,差异全经 `resched_curr_lazy` 间接体现。
- `features.h:49-53` 的 `SCHED_FEAT(PREEMPT_LAZY, true)` 仅在 `CONFIG_PREEMPT_RT && CONFIG_PREEMPT_LAZY` 下启用;`TTWU_QUEUE` 特性 RT 下关闭(`features.h:49-50`)。
- `__schedule` 中 `clear_tsk_need_resched_lazy(prev)`(`core.c:6642`);RT 抢占决策 `preemptible_lazy()`(`core.c:6864-6870`);`SM_MASK_PREEMPT`/`SM_RTLOCK_WAIT` 语义(`core.c:6512-6520`)。
- 与 git status 中 arm64 `thread_info.h`/`preempt.h`/`asm-offsets.c` 改动一致(`RESCHED_LAZY` 标志位 backport)。RT 的真实影响在抢占时机而非锁语义——符合核心子系统"关键锁多半已是 raw_spinlock"的规律(见 [`references/methodology.md`](../../.qoder/skills/kernel-core-subsystem-docs/references/methodology.md) §F)。

