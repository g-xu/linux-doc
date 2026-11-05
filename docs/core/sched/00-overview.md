---
title: 总览
---

# 00 · 调度器子系统总览(原理导览)

> 本篇是整套文档的**学习主线**:先用三个核心决定讲清调度器的基本思想,再用五个调度类勾勒大体框架,然后把"一次调度怎么发生""多核怎么协作"两条主流程走一遍,最后才是边界、子模块划分与文档导航。想快速建立全局观,顺着 §1→§5 读一遍即可;想深入某个类,每节末尾都有"深入见"指针。
>
> 基线:Linux 6.1.158(**PREEMPT_RT `-rt58`**,已确认 `CONFIG_PREEMPT_LAZY`),arm64。代码根:`kernel/sched/`。

## §1 一句话定位与全局架构

调度器决定"此刻这颗 CPU 上跑哪个任务",并在多核间平衡负载。它是内核最核心的共享状态机:每颗 CPU 一个运行队列(`struct rq`),按 sched_class 优先级链(stop > dl > rt > fair > idle)挑任务,靠 tick、唤醒、主动让出三类事件驱动重新调度。

本树是 PREEMPT_RT 实时补丁树,调度器的实时性改动集中在**抢占时机**(lazy 抢占),而非锁语义——核心锁本就是 `raw_spinlock_t`。这个判断贯穿全套文档,细节见 [`05-concurrency.md`](05-concurrency.md) §7。

一张图把上面这段话讲完——三类事件驱动 `__schedule`,`__schedule` 沿 sched_class 优先级链选任务,任务来自 per-CPU 运行队列 `rq` 内嵌的各类子队列:

```
              ┌────────────────────────────────────────────────────────────────┐
   谁触发重调度 │  三类事件 → 置 need_resched / need_resched_lazy(RT)            │
              │   ① tick 周期    ② wakeup 唤醒抢占    ③ 主动让出/阻塞(schedule)│
              └───────────────────────────────┬────────────────────────────────┘
                                               │ 在抢占点/系统调用返回处触发
                                               ▼
                                     ┌────────────────────┐
   谁来选       │                     │   __schedule()     │  core.c:6557
                                     │   pick_next_task   │  按优先级链逐类询问
                                     └─────────┬──────────┘
                                               │
   选谁         优先级 高 ◄─────────────────────┴───────────────────────► 低
   ┌────────┐    ┌──────────┐    ┌────────┐    ┌──────────┐    ┌────────┐
   │  stop  │───►│ deadline │───►│   rt   │───►│   fair   │───►│  idle  │  .next 链
   └────────┘    └──────────┘    └────────┘    └──────────┘    └────────┘
   迁移/热插拔    EDF 截止期      FIFO/RR       CFS vruntime    swapper 兜底
   stop_task.c   deadline.c      rt.c          fair.c          idle.c
       ▲              ▲              ▲              ▲               ▲
       │       从哪取任务:每颗 CPU 一个 struct rq(kernel/sched/sched.h:954) │
   ┌───┴──────────────┴──────────────┴──────────────┴───────────────┴────────┐
   │ struct rq (per-CPU 主结构)   curr ─► 当前任务    __lock: raw_spinlock_t │
   │   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐               │
   │   │ cfs  (CFS)    │  │ rt   (RT)     │  │ dl   (DL)     │  内嵌值(非指针)│
   │   │ 红黑树        │  │ 优先级位图    │  │ 红黑树        │               │
   │   │ +min_vruntime │  │ +queue[100]   │  │ 按 deadline 排 │               │
   │   └───────────────┘  └───────────────┘  └───────────────┘               │
   │   clock / clock_task(双时钟)     avg_rt / avg_dl(PELT 负载信号)         │
   └──────────────────────────────────────────────────────────────────────────┘
```

图里三行分别对应下面的学习主线:"谁触发"是 §4 一次调度怎么发生,"怎么选"是 §2/§3 基本思想与框架,"从哪取"是 `rq` 内嵌子队列([`02-objects.md`](02-objects.md) 逐字段展开)。

## §2 基本思想:三个核心决定

理解调度器,先理解设计者做的三个关键决定——它们解释了后面所有结构为什么长这样。

**决定一:为什么每颗 CPU 一个运行队列?** 若全系统共用一条就绪队列,每次选任务都要抢同一把锁,核越多锁争用越致命。于是每颗 CPU 独享一个 `struct rq`(`kernel/sched/sched.h:954`),选任务只碰本地锁 `rq->__lock`(`:956`,`raw_spinlock_t`)。代价是负载会失衡,于是另设一层**负载均衡**(§5)把任务在 rq 间搬平——"本地快、全局补"是调度器的第一性权衡。

**决定二:为什么把任务分成调度类,连成优先级链?** 实时任务要求"到点必跑",普通任务要求"长期公平",两种诉求无法用一个算法同时满足。于是按诉求分成五个**调度类**,用 `.next` 指针连成一条硬优先级链(stop > dl > rt > fair > idle),`__schedule` 从高到低逐类询问"你有任务可跑吗",第一个应答的就赢。高类不空,低类就一直等——RT/DL 能压过普通任务正源于此。链的框架见 §3。

**决定三:为什么事件驱动、tick 不直接换人?** 调度器不主动轮询,只在三类事件发生时"打个标记"(`need_resched`),真正的换人推迟到最近的**抢占点**(中断返回、系统调用返回、主动 `schedule()`)才做。tick 只负责判断"该不该抢"并置标记,不在中断上下文里直接切任务——这让切换点可控、可关抢占保护。RT 树进一步把标记分成"立即抢"和"lazy 抢"两档(`resched_curr` vs `resched_curr_lazy`),普通任务用 lazy 档减少无谓切换。三类事件的时间线见 §4。

这三个决定是理解全篇的钥匙:每 CPU 一队(§5 均衡)、分类成链(§3 框架)、事件驱动(§4 流程)。想再直白些,见 [`08-feynman.md`](08-feynman.md) 的大白话讲解。

## §3 大体框架:五个调度类

优先级链上五个类,从高到低。每类只讲**核心思想 + 关键结构 + 深入指针**,细节下沉。

**① stop(最高)** — 不是给用户任务的,是内核用来"抢占一切"做迁移、CPU 热插拔的特权通道。每 CPU 一个 `migration/N` 线程,一旦就绪压过所有类。无内部排队结构(至多一个任务)。选任务 `pick_next_task_stop`(`stop_task.c:44`),注册 `DEFINE_SCHED_CLASS(stop)`(`stop_task.c:117`)。深入见 [`sub/idle-stop/00-overview.md`](sub/idle-stop/00-overview.md)。

**② deadline / DL(EDF)** — 面向硬实时:每个任务声明(运行时间 runtime、截止期 deadline、周期 period),内核用 EDF(最早截止期优先)+ CBS(恒定带宽服务器)保证"到点前拿到足额 CPU",并靠**准入控制**拒绝会超载的任务。关键结构 `struct dl_rq`(`kernel/sched/sched.h:704`),按 deadline 排的红黑树。选任务 `pick_next_task_dl`(`deadline.c:2033`)。深入见 [`sub/deadline/00-overview.md`](sub/deadline/00-overview.md)。

**③ rt(FIFO/RR)** — 面向软实时:100 个静态优先级,用**位图 O(1)** 选最高优先级的就绪队列,同级按 FIFO 或时间片轮转(RR)。为防实时任务饿死普通任务,设 **RT 带宽节流**(默认每 1s 最多跑 0.95s)。关键结构 `struct rt_rq`(`:663`)= 位图 `active`(`:664`)+ `queue[100]`。选任务 `pick_next_task_rt`(`rt.c:1818`)。深入见 [`sub/rt/00-overview.md`](sub/rt/00-overview.md)。

**④ fair / CFS(默认)** — 承载绝大多数普通任务的公平类:用**虚拟运行时间 vruntime** 度量"谁被亏待最多",红黑树按 vruntime 排序,永远选最左(vruntime 最小)的跑,跑一会 vruntime 增加就往右沉,从而长期公平。关键结构 `struct cfs_rq`(`:550`)= `rb_root_cached` 红黑树 + `min_vruntime`。选任务 `pick_next_task_fair`(`fair.c` 快路径 `core.c:5986`)。**本轮唯一完整展开的类**,深入见 [`sub/fair/00-overview.md`](sub/fair/00-overview.md)。

**⑤ idle(最低)** — 兜底:当且仅当上面四类全空,才跑每 CPU 的 `swapper/N` 空闲线程,进而进入省电的 idle 状态。选任务 `pick_next_task_idle`(`idle.c:447`)。深入见 [`sub/idle-stop/00-overview.md`](sub/idle-stop/00-overview.md)。

五类的注册即优先级顺序:`DEFINE_SCHED_CLASS` 于 `stop_task.c:117` / `deadline.c:2738` / `rt.c:2692` / `fair.c:12443` / `idle.c:499`,链接段逆序排布,`for_each_class` 按 stop→dl→rt→fair→idle 遍历。

## §4 一次调度怎么发生(时间维度)

把 §2 的"事件驱动"落到一条时间线上:从被触发到切走,`__schedule`(`core.c:6557`)内部按什么次序做事。下图纵向为时间,每步对应 [`03-lifecycle.md`](03-lifecycle.md) §3 的跳编号。

```
   时间↓   __schedule()   core.c:6557
   ──────────────────────────────────────────────────────────
   t0  │ 关中断; rq_lock; update_rq_clock;  prev = rq->curr
   t1  │ 处理 prev:睡眠?→ deactivate 出队 | 抢占?→ 留在队上
   t2  │ next = pick_next_task()  按优先级链 stop→dl→rt→fair→idle
   t3  │ clear_need_resched(prev)
   t4  │ prev≠next?→ rq->curr=next; context_switch(切 mm/寄存器)
        │           → finish_task_switch(回收 prev,若已 dead)
   ──────────────────────────────────────────────────────────
   触发来源(t0 之前已置好 need_resched):
     ① tick:scheduler_tick → task_tick_* → 判时间片 → resched
     ② wakeup:try_to_wake_up → 入队 → check_preempt_curr → resched
     ③ 主动:schedule()/阻塞/yield 直接进 __schedule
   RT 差异:①② 的置位在普通任务上走 resched_curr_lazy(§05 §7)
```

一句话:**三类事件先置标记,抢占点进 `__schedule`,按优先级链选出 next,再切换**。任务的完整生死状态机(fork→runnable→running→sleeping→dead 及迁移中转)见 [`03-lifecycle.md`](03-lifecycle.md)。

## §5 多核怎么协作(SMP 与负载均衡)

§2 决定一说"每 CPU 一队,靠均衡补失衡"。多核协作就是这层:

- **fair 的周期均衡**:tick 里触发 `run_rebalance_domains`,沿 `sched_domain` 层级(SMT→MC→NUMA)从最忙的组往本 CPU 拉任务,`imbalance_pct` 是每层的容忍阈值。域层级由 topology 子模块构建。
- **rt/dl 的 push/pull**:实时类不做周期均衡,改用即时的 push(本 CPU 多出高优先级任务就推给别人)/ pull(本 CPU 要空了就从别人那拉),靠 `cpupri`(RT)/ `cpudeadline`(DL)两个 CPU 优先级堆快速定位目标核。
- **RT 带宽 / DL 准入是全局的**:RT 节流预算、DL 准入判断跨 CPU 共享 `root_domain`(`kernel/sched/sched.h:822`)里的参数,DL 消耗还会挤占 RT 带宽账(一处强耦合)。

这些跨模块、跨 CPU 的关系,谁依赖谁、通过什么依赖,逐格登记在 [`04-interactions.md`](04-interactions.md)(空间维度骨架)。

至此全局观已建立:五类成链(§3)、一次调度的内部次序(§4)、多核如何补平(§5)。下面是查阅性内容。

## §6 边界:什么算调度器,什么不算

| 属于 | 不属于(相邻子系统) |
|---|---|
| `kernel/sched/` 全部:选任务、入/出队、负载均衡、带宽节流、PELT 负载跟踪、CPU 频率选择 hook(schedutil) | 定时器/hrtimer(`kernel/time/`)——只是 tick 的来源 |
| sched_class 五类实现 + 各自 runqueue | RCU(`kernel/rcu/`)——`rcu_nocbs` 等属它 |
| syscall(sched_setscheduler 等)与内核内 API | rtmutex/futex(`kernel/locking/`)——经 PI 与调度器耦合,但不在此 |
| /proc、debugfs、tracepoint 观测面 | cpuset/cgroup 框架本身(`kernel/cgroup/`)——调度器只提供 cpu controller |

跨边界的耦合点(PI 提优、cpufreq、cpuset 域重建等)在 [`04-interactions.md`](04-interactions.md) 逐条登记。

## §7 子模块划分(11 个)

按职责与文件聚类。本轮完整展开 fair 作样板,rt/deadline/idle-stop 各补一篇原理速览(`sub/*/00-overview.md`),其余给定位与文件锚点。

| # | 子模块 | 主要文件 | 职责 |
|---|---|---|---|
| 1 | core(核心) | core.c / core_sched.c / build_*.c | rq、`__schedule`、唤醒、抢占、syscall、cgroup、preempt dynamic/lazy |
| 2 | fair(CFS) | fair.c | 普通任务:vruntime 公平 + 负载均衡 + NUMA/EAS/util_est(**已展开**) |
| 3 | rt | rt.c | FIFO/RR 实时任务 + RT 带宽节流(**速览已补**) |
| 4 | deadline | deadline.c | EDF 截止期调度 + 准入控制(**速览已补**) |
| 5 | idle/stop | idle.c / stop_task.c | 最低(swapper)与最高(迁移/热插拔)两端类(**速览已补**) |
| 6 | pelt | pelt.c | Per-Entity Load Tracking,负载/利用率信号源 |
| 7 | topology | topology.c / isolation.c | sched_domain 层级构建、isolcpus/nohz_full housekeeping |
| 8 | cpupri/cpudeadline | cpupri.c / cpudeadline.c | RT/DL 的 CPU 优先级堆,支撑 push/pull |
| 9 | cpufreq(schedutil) | cpufreq.c / cpufreq_schedutil.c | 用调度负载驱动 CPU 调频 |
| 10 | 可观测(observability) | debug.c / stats.c / cpuacct.c / psi.c | /proc/sched_debug、schedstat、PSI 压力 |
| 11 | 工具/同步(utility) | wait.c / swait.c / completion.c / wait_bit.c / loadavg.c / clock.c / cputime.c / autogroup.c / membarrier.c | 等待队列、完成量、时钟、负载均值、autogroup、membarrier |

## §8 文档导航(按解决的问题查)

| 篇 | 解决什么问题 | 何时读 |
|---|---|---|
| [`00-overview.md`](00-overview.md)(本篇) | 原理导览:基本思想→框架→流程→导航 | 入口 / 复习 |
| [`01-api.md`](01-api.md) | syscall ABI + 内核内 API 有哪些、uapi 落点 | 接调度器接口时 |
| [`02-objects.md`](02-objects.md) | 核心对象 `struct rq` 的字段与同名陷阱、RCU | 读数据结构时 |
| [`03-lifecycle.md`](03-lifecycle.md) | 任务从 fork 到 exit、一次调度的 10 跳状态机 | 追一次调度全流程时 |
| [`04-interactions.md`](04-interactions.md) | 子模块间与跨子系统的耦合、依赖环 | 排查跨模块问题时 |
| [`05-concurrency.md`](05-concurrency.md) | 锁清单、加锁偏序、RT 差异 | 排查竞态/死锁时 |
| [`06-tuning.md`](06-tuning.md) | 可调项(sysctl/cgroup/boot/debugfs) | 调参/部署时 |
| [`07-debug.md`](07-debug.md) | 观测面(/proc/tracepoint/BPF/schedstat) | 出问题从哪看 |
| [`08-feynman.md`](08-feynman.md) | 四个最难跨模块概念的大白话 | 概念卡壳时 |
| [`99-evidence.md`](99-evidence.md) | 顶层所有结论的可复现出处 | 核实/复现时 |
| [`sub/fair/`](sub/fair/00-overview.md) | fair 子模块四篇(样板,完整展开) | 深入 CFS 时 |
| [`sub/rt/`](sub/rt/00-overview.md) · [`sub/deadline/`](sub/deadline/00-overview.md) · [`sub/idle-stop/`](sub/idle-stop/00-overview.md) | 三个实时/退化类的原理速览 | 深入对应类时 |

## §9 推荐阅读顺序

```
   ① 建立全局观(本篇一条龙)      ② 看清接口与对象        ③ 逐个子模块展开
   ┌────────────────────────┐   ┌──────────────┐        ┌──────────────────────┐
   │ 00 §1 架构图            │   │ 01 API       │        │ sub/fair(CFS 样板)   │
   │ 00 §2 三个核心决定      │──►│ 02 对象 rq   │───────►│ sub/rt  sub/deadline  │
   │ 00 §3 五个调度类        │   │ 03 生命周期  │        │ sub/idle-stop         │
   │ 00 §4 一次调度  §5 多核 │   │ 04 交互 05并发│       │ (各篇 §深入指针)      │
   └────────────────────────┘   └──────────────┘        └──────────┬───────────┘
        懂"跑哪个、谁来选、          懂"从哪调、                    │
         多核怎么补"                 字段怎么放"        运维/回味 ◄──┘
                                                        06 调参 07 观测 08 回味 99 取证
```

- 初次通读(上图主线):00 全篇 → 01 → 02 → 03 → 04/05 → sub/fair → sub/{rt,deadline,idle-stop} → 06/07 → 08 → 99。
- 只想懂原理:00 §2→§5 → 08-feynman 四概念 → 各类 `sub/*/00`。
- 排障速查:症状 → §8 表定位到篇 → 篇内 RT 差异节 → 99 复现。

## §10 全局约定(读所有篇通用)

- 行号格式 `文件:行` 或 `@行`,指向 6.1.158 源码;宏(SYSCALL_DEFINE、TRACE_EVENT、sched_feat 等)是索引盲区,行号指宏调用点,各篇末尾有盲区声明。
- 每篇有"RT 差异"节:本树是 `-rt58`,凡与主线不同处强制标注。
- 跨 ≥2 子模块的结论一律"上溢"到顶层 03/04/05;子模块篇只反向引用,不重复展开。
- 架构只跟 arm64;x86/ppc/arm32 差异忽略。
