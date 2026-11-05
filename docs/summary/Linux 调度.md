# Linux 内核调度器深度解析

## 1、Linux 内核调度器总体框架

### 1.1 架构全景：三层抽象模型

Linux 调度器并不是一个单一的算法，而是一套**分层的、可插拔的调度框架**。其核心设计哲学是：**策略与机制分离**。

```
┌─────────────────────────────────────────────────────────┐
│                    用户态进程/线程                        │
└──────────────────────────┬──────────────────────────────┘
	                       │ syscall (sched_setscheduler 等)
┌──────────────────────────▼──────────────────────────────┐
│              调度器核心层  kernel/sched/core.c           │
│   __schedule() / schedule() / try_to_wake_up()          │
│   pick_next_task() / context_switch()                   │
└──────┬──────────┬──────────┬──────────┬─────────────────┘
       │          │          │          │
┌──────▼──┐ ┌────▼────┐ ┌───▼───┐ ┌───▼──────┐
│stop_sched│ │deadline │ │  rt   │ │   fair   │  ← 调度类（sched_class）
│stop_task │ │deadline │ │  rt   │ │   fair   │
│  .c      │ │  .c     │ │  .c   │ │   .c     │
└──────────┘ └─────────┘ └───────┘ └──────────┘
  优先级递减 ──────────────────────────────────────▶
  STOP > DEADLINE > RT > FAIR > IDLE
```

**核心设计：`sched_class` 是调度框架的灵魂**，它是一个函数指针表（类似 C++ 虚函数表），定义在 `kernel/sched/sched.h` 中，每个调度类必须实现这套接口。调度类通过DEFINE_SCHED_CLASS(xx)来定义各自调度类的具体实现接口

---

### 1.2 代码文件全景及功能描述

#### 1.2.1 核心框架层

| 文件 | 行数 | 核心职责 |
|------|------|---------|
| `kernel/sched/core.c` | **10,993行** | 调度器大脑，所有调度决策的入口 |
| `kernel/sched/sched.h` | **3,165行** | 核心数据结构定义（`rq`, `sched_class`, `task_struct`调度字段） |

**`core.c` 的关键函数调用链：**

```
用户/中断触发
    │
    ▼
schedule()                          ← 主动调度入口
    └── __schedule(SM_NONE)
            ├── deactivate_task()   ← 将当前任务移出运行队列
            ├── pick_next_task()    ← 核心：按优先级遍历 sched_class
            │       └── fair_sched_class.pick_next_task()  ← 通常走这里
            └── context_switch()   ← 切换 mm、切换寄存器上下文
                    ├── switch_mm_irqs_off()  ← 切换页表（TLB flush）
                    └── switch_to()           ← 汇编：切换栈/寄存器
```

---

#### 1.2.2 调度类实现层（按优先级从高到低）

**① `stop_task.c` — STOP 调度类（最高优先级）**

- **优先级**：凌驾于一切之上，不可抢占
- **用途**：CPU 热插拔、`stop_machine()` 机制
- **原理**：每个 CPU 有一个 `migration/N` 内核线程，专门用于执行必须在特定 CPU 上完成的操作（如迁移任务）
- **关键点**：这个类的任务**永远不会被其他任务抢占**，是内核的"紧急通道"

**② `deadline.c` — EDF 调度类（2,966行）**

- **算法**：Earliest Deadline First（最早截止时间优先）
- **适用**：硬实时任务，如音视频编解码、工业控制
- **三元组参数**：`runtime`（预算）/ `deadline`（截止时间）/ `period`（周期）
- **准入控制**：内核会做带宽检查，拒绝会导致系统过载的任务（`cpudeadline.c` 辅助）
- **数据结构**：`cpudeadline.c` 维护一个按 deadline 排序的堆，用于快速找到最近截止的任务

**③ `rt.c` — 实时调度类（2,909行）**

- **算法**：固定优先级，`SCHED_FIFO` 和 `SCHED_RR` 两种策略
- **优先级范围**：0-99（数字越大优先级越高，与 nice 值相反）
- **数据结构**：`cpupri.c` 维护 per-CPU 的优先级位图，用于快速找到最高优先级 RT 任务
- **RT 带宽限流**：`/proc/sys/kernel/sched_rt_runtime_us` 防止 RT 任务饿死普通任务（默认 95%）
- **关键风险**：RT 任务持锁时若被更高优先级 RT 任务抢占，会引发**优先级反转**，需要 PI（Priority Inheritance）锁解决

**④ `fair.c` — CFS 完全公平调度类（12,236行，最复杂）**

- **算法**：Completely Fair Scheduler，基于**虚拟运行时间（vruntime）**
- **数据结构**：**红黑树**（`rb_tree`），按 vruntime 排序，最左节点即下一个运行的任务
- **核心公式**：`vruntime += delta_exec × (NICE_0_LOAD / task_weight)`，nice 值低的任务权重大，vruntime 增长慢，因此获得更多 CPU 时间
- **组调度**：支持 cgroup，实现容器级别的 CPU 资源隔离
- **PELT**（`pelt.c`）：Per-Entity Load Tracking，追踪每个调度实体的历史负载，用于负载均衡决策

**⑤ `idle.c` — IDLE 调度类（最低优先级）**

- **用途**：CPU 无任务可运行时执行，调用 `cpu_idle_loop()` → `arch_cpu_idle()` → `WFI`（ARM64）或 `HLT`（x86）指令
- **与 cpufreq 联动**：idle 时触发 cpufreq governor 降频

---

#### 1.2.3 支撑子系统层

| 文件 | 功能 |
|------|------|
| `smp.h` / `topology.c` | **SMP 负载均衡**：定义 CPU 拓扑（Core/Die/NUMA Node），`load_balance()` 周期性在 CPU 间迁移任务 |
| `pelt.c` / `pelt.h` | **负载追踪**：PELT 算法，计算每个任务/cgroup 的历史平均负载（指数衰减），驱动负载均衡和 EAS |
| `cpufreq.c` / `cpufreq_schedutil.c` | **调度驱动的频率调节**：`schedutil` governor 根据 PELT 负载实时调整 CPU 频率，实现能效优化 |
| `autogroup.c` | **自动分组**：同一终端会话的进程自动归为一组，防止编译任务（`make -j`）饿死交互进程 |
| `loadavg.c` | **系统负载均值**：计算 `/proc/loadavg` 中的 1/5/15 分钟平均负载 |
| `clock.c` | **调度时钟**：`sched_clock()` 提供高精度时间戳，驱动 vruntime 计算 |
| `stats.c` / `stats.h` | **调度统计**：`/proc/schedstat`，暴露调度延迟、切换次数等性能数据 |
| `debug.c` | **调试支持**：`/proc/sched_debug` 输出每个 CPU 运行队列的详细状态 |
| `features.h` | **调度特性开关**：通过 `sched_feat()` 宏在运行时开关调度优化特性（debugfs 可调） |
| `isolation.c` | **CPU 隔离**：`isolcpus=` 内核参数支持，将 CPU 从通用调度中隔离出去 |
| `membarrier.c` | **内存屏障系统调用**：`sys_membarrier()`，用于无锁算法中强制 CPU 间内存可见性 |
| `wait.c` / `swait.c` | **等待队列**：`wait_queue` 和轻量级 `swait_queue`，任务睡眠/唤醒的基础设施 |
| `completion.c` | **completion 机制**：基于等待队列的同步原语，常用于驱动初始化同步 |
| `psi.c` | **压力失速信息**：Pressure Stall Information，检测 CPU/内存/IO 资源压力，Android 低内存杀手依赖此机制 |

---

### 1.3 最核心的数据结构

```c
/* kernel/sched/sched.h */

/* 每个 CPU 一个运行队列 —— 调度器的核心状态 */
struct rq {
    raw_spinlock_t      lock;        /* 保护整个 rq 的自旋锁 */
    unsigned int        nr_running;  /* 可运行任务数 */

    struct cfs_rq       cfs;         /* CFS 子运行队列（含红黑树） */
    struct rt_rq        rt;          /* RT 子运行队列 */
    struct dl_rq        dl;          /* Deadline 子运行队列 */

    struct task_struct  *curr;       /* 当前正在运行的任务 */
    struct task_struct  *idle;       /* idle 线程 */
    u64                 clock;       /* rq 本地时钟 */
    /* ... */
};

/* 调度类：策略与机制分离的核心接口 */
struct sched_class {
    void (*enqueue_task)(struct rq *rq, struct task_struct *p, int flags);
    void (*dequeue_task)(struct rq *rq, struct task_struct *p, int flags);
    void (*yield_task)(struct rq *rq);
    struct task_struct *(*pick_next_task)(struct rq *rq);
    void (*put_prev_task)(struct rq *rq, struct task_struct *p);
    void (*task_tick)(struct rq *rq, struct task_struct *p, int queued);
    /* ... 共约 20 个函数指针 */
};
```

---

### 1.4 调度触发时机全景

```
触发源                    路径
─────────────────────────────────────────────────────
时钟中断（HZ tick）  →  scheduler_tick() → task_tick() → 可能设置 TIF_NEED_RESCHED
系统调用返回         →  检查 TIF_NEED_RESCHED → schedule()
中断返回（用户态）   →  同上
主动睡眠             →  schedule() 直接调用
wake_up()            →  try_to_wake_up() → enqueue_task() → 可能抢占当前任务
```

---

## 2、`struct rq`：调度器的核心数据结构

### 2.1 设计哲学：**"每 CPU 一个宇宙"**

`rq`（Run Queue，运行队列）是 Linux 调度器最核心的数据结构，其设计遵循一个根本原则：

> **每个 CPU 拥有一个完全独立的 `rq`，调度决策尽量在本地完成，跨 CPU 操作是例外而非常态。**

这是一个**彻底的 Per-CPU 设计**，定义在 `kernel/sched/core.c`：

```c
DEFINE_PER_CPU_SHARED_ALIGNED(struct rq, runqueues);
```

`SHARED_ALIGNED` 意味着这个变量被强制对齐到 **Cache Line 边界**，防止不同 CPU 的 `rq` 之间产生 **False Sharing**（伪共享）——这是高性能内核数据结构设计的基本功。

---

### 2.2 `rq` 结构体的内存布局与分区解析

从源码来看，`rq` 的字段可以按职责分为 **5 个功能区**：

```
struct rq {
┌─────────────────────────────────────────────────────┐
│  区域 1：锁与基础计数                                  │
│  __lock / nr_running / nr_switches                  │
├─────────────────────────────────────────────────────┤
│  区域 2：三个调度类的子运行队列（核心！）               │
│  struct cfs_rq  cfs                                 │
│  struct rt_rq   rt                                  │
│  struct dl_rq   dl                                  │
├─────────────────────────────────────────────────────┤
│  区域 3：当前运行状态                                  │
│  *curr / *idle / *stop / clock / clock_task         │
├─────────────────────────────────────────────────────┤
│  区域 4：SMP 负载均衡                                  │
│  *rd / *sd / cpu_capacity / avg_rt / avg_dl         │
│  active_balance / misfit_task_load                  │
├─────────────────────────────────────────────────────┤
│  区域 5：统计与调试                                    │
│  sched_info / calc_load / hrtick_timer              │
└─────────────────────────────────────────────────────┘
```

---

### 2.3 关键字段深度剖析

#### 2.3.1 区域 1：锁设计——`raw_spinlock_t __lock`

```c
raw_spinlock_t  __lock;   /* 注意：字段名是 __lock，不是 lock */
```

**为什么用 `raw_spinlock_t` 而不是普通 `spinlock_t`？**

这是一个极其重要的设计决策：
- 普通 `spinlock_t` 在 `PREEMPT_RT`（实时内核补丁）下会被替换为可睡眠的 mutex
- 但 `rq->lock` 在中断上下文中被持有，**绝对不能睡眠**
- `raw_spinlock_t` 即使在 RT 内核下也保持真正的自旋语义

字段名加 `__` 前缀是一个**刻意的命名警告**，提示开发者不要直接访问它，必须通过封装好的 `rq_lock()` / `rq_unlock()` 系列函数操作，这些函数会同时处理中断禁用和 lockdep 注解。

#### 2.3.2 区域 2：三个子运行队列的嵌入设计

```c
struct cfs_rq  cfs;   /* CFS 子队列：直接嵌入，不是指针！ */
struct rt_rq   rt;    /* RT 子队列 */
struct dl_rq   dl;    /* Deadline 子队列 */
```

**为什么是直接嵌入（embed）而不是指针？**

这是一个 **Cache 友好性**的设计决策：
- 指针需要额外一次内存寻址（pointer chasing），在高频调度路径上代价极大
- 直接嵌入使得 `rq`、`cfs_rq`、`rt_rq` 在内存上连续，大概率在同一或相邻 Cache Line 中
- 代价是 `rq` 结构体本身很大（通常超过 1KB），但这完全可以接受，因为它是 Per-CPU 的

#### 2.3.3 区域 3：双时钟设计

```c
u64  clock;                              /* rq 墙钟时间 */
u64  clock_task ____cacheline_aligned;   /* 任务运行时间（排除中断） */
u64  clock_pelt;                         /* PELT 专用时钟 */
```

**为什么要三个时钟？**

| 时钟 | 用途 | 特点 |
|------|------|------|
| `clock` | rq 的绝对时间基准 | 包含中断时间 |
| `clock_task` | 驱动 vruntime 计算 | **排除中断处理时间**，任务不应为中断"买单" |
| `clock_pelt` | PELT 负载追踪 | 有专门的衰减语义，与前两者不同步 |

`clock_task` 用 `____cacheline_aligned` 强制对齐，因为它在调度热路径中被频繁读写，独占 Cache Line 避免与 `clock` 产生写竞争。

#### 2.3.4 区域 4：SMP 拓扑感知

```c
struct root_domain      *rd;    /* 调度域的根，跨 NUMA 节点 */
struct sched_domain     *sd;    /* 调度域链表头（Core→Die→NUMA） */
unsigned long           cpu_capacity;       /* 当前 CPU 算力（考虑热降频） */
unsigned long           cpu_capacity_orig;  /* 原始算力（不降频时） */
unsigned long           cpu_capacity_inverted; /* EAS 用：大小核感知 */
```

`cpu_capacity` 是 **EAS（Energy Aware Scheduling）** 的基础——ARM big.LITTLE 架构中，大核和小核的 `cpu_capacity` 不同，调度器据此决定把任务放到哪个核上，在性能和功耗之间取得平衡。

---

### 2.4 三个子运行队列的数据结构对比

#### 2.4.1 `struct cfs_rq`：红黑树 + vruntime

```c
struct cfs_rq {
    struct load_weight      load;           /* 队列总权重 */
    unsigned int            nr_running;     /* 可运行任务数 */
    u64                     min_vruntime;   /* 队列最小虚拟时间（单调递增） */

    struct rb_root_cached   tasks_timeline; /* 红黑树！按 vruntime 排序 */
                                            /* _cached 表示缓存了最左节点 */

    struct sched_entity     *curr;  /* 当前运行的调度实体 */
    struct sched_entity     *next;  /* wake-affine 预选的下一个 */
    struct sched_entity     *last;  /* 刚被抢占的任务（防止立刻被抢回） */
    struct sched_entity     *skip;  /* yield 时跳过的任务 */

    struct sched_avg        avg;    /* PELT 负载追踪 */
};
```

**`rb_root_cached` 的精妙之处**：标准红黑树找最左节点需要 O(log n)，而 `rb_root_cached` 额外缓存了 `rb_leftmost` 指针，使 `pick_next_task_fair()` 找到下一个任务变成 **O(1)**。

**`min_vruntime` 的作用**：新任务入队时，其初始 vruntime 被设置为 `max(task->vruntime, cfs_rq->min_vruntime)`，防止新任务因 vruntime 为 0 而独占 CPU（饿死老任务）。

#### 2.4.2 `struct rt_rq`：优先级位图

```c
struct rt_rq {
    struct rt_prio_array  active;   /* 核心！100个优先级的位图+链表 */
    unsigned int          rt_nr_running;

    struct {
        int  curr;   /* 当前最高优先级 */
        int  next;   /* 第二高优先级（SMP 迁移用） */
    } highest_prio;

    u64  rt_time;      /* 本周期已消耗的 RT 时间 */
    u64  rt_runtime;   /* 本周期 RT 时间配额（防饿死 CFS） */
};
```

`rt_prio_array` 的结构是：

```c
struct rt_prio_array {
    DECLARE_BITMAP(bitmap, MAX_RT_PRIO + 1); /* 100位的位图 */
    struct list_head queue[MAX_RT_PRIO];     /* 每个优先级一个链表 */
};
```

**`pick_next_task_rt()` 的 O(1) 秘密**：用 `sched_find_first_bit(bitmap)` 找到最高优先级（一条 `BSF` 指令），再从对应链表取头部，整个过程 **O(1)**，这正是 RT 调度器的核心优势。

#### 2.4.3 `struct dl_rq`：双红黑树

```c
struct dl_rq {
    struct rb_root_cached  root;                    /* 按 deadline 排序 */
    struct rb_root_cached  pushable_dl_tasks_root;  /* 可迁移任务，也按 deadline */

    struct {
        u64  curr;   /* 当前运行任务的 deadline */
        u64  next;   /* 最近的下一个 deadline */
    } earliest_dl;   /* 缓存，用于快速判断是否需要迁移 */

    u64  running_bw;  /* 当前活跃任务的带宽占用 */
    u64  this_bw;     /* 分配给本 CPU 的总带宽 */
};
```

DL 调度器维护**两棵红黑树**：
- `root`：所有可运行的 DL 任务，按 deadline 排序，最左 = 最紧迫
- `pushable_dl_tasks_root`：可以被推送到其他 CPU 的任务，用于 SMP 负载均衡

---

### 2.5 调度类如何操作 `rq`：完整交互图

```
                    struct rq (per-CPU)
                    ┌──────────────────────────────┐
                    │  __lock                       │
                    │  nr_running                   │
                    │  curr ──────────────────────► task_struct
                    │  stop ──────────────────────► migration/N
                    │  idle ──────────────────────► swapper/N
                    │                               │
pick_next_task()    │  dl  ┌──────────────────┐    │
遍历顺序：          │      │ rb_root (EDF树)  │    │
                    │      └──────────────────┘    │
stop > dl > rt > fair > idle
                    │  rt  ┌──────────────────┐    │
                    │      │ rt_prio_array    │    │
                    │      │ bitmap[100]      │    │
                    │      └──────────────────┘    │
                    │                               │
                    │  cfs ┌──────────────────┐    │
                    │      │ rb_root_cached   │    │
                    │      │ (vruntime 红黑树) │    │
                    │      │ min_vruntime     │    │
                    │      └──────────────────┘    │
                    └──────────────────────────────┘
```

**`pick_next_task()` 的遍历逻辑**（`core.c`）：

```c
/* 优化路径：如果全是 CFS 任务（最常见情况），直接调用 */
if (likely(prev->sched_class <= &fair_sched_class &&
           rq->nr_running == rq->cfs.h_nr_running)) {
    p = pick_next_task_fair(rq, prev, rf);
    /* ... */
}

/* 通用路径：按优先级遍历所有调度类 */
for_each_class(class) {
    p = class->pick_next_task(rq);
    if (p)
        return p;
}
```

这里有一个**关键优化**：当系统中只有 CFS 任务时（生产环境的绝大多数情况），直接跳过 stop/dl/rt 的检查，避免无谓的函数调用。

---

### 2.6 `rq->lock` 的并发控制：最容易踩坑的地方

`rq->lock` 的持锁规则极其严格，稍有不慎就会死锁：

```
规则 1：操作本 CPU 的 rq，必须禁用中断后加锁
        local_irq_disable() + raw_spin_lock(&rq->lock)
        → 封装为 rq_lock(rq, &rf)

规则 2：同时操作两个 CPU 的 rq（负载均衡），必须按 CPU 编号顺序加锁
        if (src_cpu < dst_cpu)
            lock(src_rq) → lock(dst_rq)
        else
            lock(dst_rq) → lock(src_rq)
        → 违反此规则 = 死锁

规则 3：task_rq_lock() 会在加锁后验证任务是否还在该 rq 上
        （任务可能在加锁前被迁移走了）
```

## 3、STOP调度类分析
---

### 3.1 调度类（Scheduling Class）的本质：策略与机制分离

Linux 调度器的核心设计哲学是**策略与机制分离**。`struct sched_class` 就是这个设计的体现——它是一张**虚函数表（vtable）**，定义了调度器核心（`core.c`）与具体调度策略之间的**契约接口**。

```
调度器核心 (core.c)
        │
        │  调用统一接口
        ▼
struct sched_class  ←── 相当于 C 语言的"抽象基类"
        │
        ├── stop_sched_class   (最高优先级)
        ├── dl_sched_class     (Deadline)
        ├── rt_sched_class     (RT: FIFO/RR)
        ├── fair_sched_class   (CFS: 普通进程)
        └── idle_sched_class   (最低优先级)
```

这五个调度类通过 `DEFINE_SCHED_CLASS` 宏形成一条**静态链表**，优先级从高到低排列，`pick_next_task` 时从高到低遍历。

---

### 3.2 `struct sched_class` 接口全景解析

以 `stop_task.c` 末尾的 `DEFINE_SCHED_CLASS(stop)` 为索引，逐组分析每个接口的语义：

#### 3.2.1 队列管理接口：任务的入队与出队

```c
.enqueue_task  = enqueue_task_stop,
.dequeue_task  = dequeue_task_stop,
.yield_task    = yield_task_stop,
```

这三个接口管理**运行队列（runqueue）的增减**。

**`enqueue_task`** — 任务变为可运行时调用（如 `wake_up_process()`）：
```c
static void enqueue_task_stop(struct rq *rq, struct task_struct *p, int flags)
{
    add_nr_running(rq, 1);  /* 仅维护全局计数器，stop 任务本身就是 rq->stop */
}
```
> **关键设计**：stop 任务不需要真正的队列数据结构（红黑树、链表等），因为每个 CPU 只有**唯一一个** stop 任务（`rq->stop`），它本身就是"队列"。`add_nr_running` 只是告诉调度器核心"这个 CPU 上多了一个可运行任务"。

**`dequeue_task`** — 任务不再可运行时调用（如阻塞、退出）：
```c
static void dequeue_task_stop(struct rq *rq, struct task_struct *p, int flags)
{
    sub_nr_running(rq, 1);  /* 对称地减少计数 */
}
```

**`yield_task`** — 进程主动让出 CPU（`sched_yield()` 系统调用）：
```c
static void yield_task_stop(struct rq *rq)
{
    BUG();  /* stop 任务永远不应该主动让出 CPU，这是设计约束 */
}
```
> `BUG()` 在内核中会触发 `panic`，这是一种**防御性编程**：用运行时崩溃来暴露违反设计约束的调用者，比静默失败安全得多。

---

#### 3.2.2 抢占检查接口

```c
.check_preempt_curr = check_preempt_curr_stop,
```

当一个新任务被唤醒时，调度器核心调用此接口询问："当前正在运行的任务需要被抢占吗？"

```c
static void check_preempt_curr_stop(struct rq *rq, struct task_struct *p, int flags)
{
    /* we're never preempted */
}
```

**空函数体**，这是 stop 类最核心的特权声明：**stop 任务永远不会被抢占**。无论唤醒的是什么任务（哪怕是另一个 RT 任务），stop 任务都继续运行。这正是注释中说的 *"preempts everything and will be preempted by nothing"*。

---

#### 3.2.3 任务选择接口：调度器的核心决策

```c
.pick_next_task = pick_next_task_stop,
.put_prev_task  = put_prev_task_stop,
.set_next_task  = set_next_task_stop,
```

这是调度器最核心的三个接口，构成一次**上下文切换的完整生命周期**。

**`pick_next_task`** — "下一个该运行谁？"
```c
static struct task_struct *pick_next_task_stop(struct rq *rq)
{
    struct task_struct *p = pick_task_stop(rq);
    if (p)
        set_next_task_stop(rq, p, true);
    return p;
}

static struct task_struct *pick_task_stop(struct rq *rq)
{
    if (!sched_stop_runnable(rq))  /* 检查 rq->stop 是否可运行 */
        return NULL;
    return rq->stop;               /* 直接返回唯一的 stop 任务 */
}
```
> `sched_stop_runnable(rq)` 展开后检查 `rq->stop && task_on_rq_queued(rq->stop)`。选择逻辑极其简单：要么有（返回它），要么没有（返回 NULL 让核心去问下一个调度类）。

**`set_next_task`** — "记录任务开始执行的时间戳"
```c
static void set_next_task_stop(struct rq *rq, struct task_struct *stop, bool first)
{
    stop->se.exec_start = rq_clock_task(rq);  /* 记录开始执行的时钟 */
}
```
> `rq_clock_task` 返回的是**任务时钟**（task clock），它在任务实际运行时才推进（排除了中断处理时间），比 `rq_clock` 更精确地反映任务的实际执行时间。

**`put_prev_task`** — "当前任务即将被换下，做收尾统计"
```c
static void put_prev_task_stop(struct rq *rq, struct task_struct *prev)
{
    struct task_struct *curr = rq->curr;
    u64 delta_exec;

    delta_exec = rq_clock_task(rq) - curr->se.exec_start;
    if (unlikely((s64)delta_exec < 0))
        delta_exec = 0;

    schedstat_set(curr->stats.exec_max,
                  max(curr->stats.exec_max, delta_exec));

    curr->se.sum_exec_runtime += delta_exec;   /* 累计总执行时间 */
    account_group_exec_runtime(curr, delta_exec); /* cgroup CPU 记账 */

    curr->se.exec_start = rq_clock_task(rq);
    cgroup_account_cputime(curr, delta_exec);  /* cgroup 时间片记账 */
}
```
> 注意 `unlikely((s64)delta_exec < 0)` 的防御：时钟回绕（clock skew）在 TSC 不稳定的硬件上确实会发生，内核在这里做了保护。

---

#### 3.2.4 SMP 负载均衡接口

```c
#ifdef CONFIG_SMP
.balance        = balance_stop,
.pick_task      = pick_task_stop,
.select_task_rq = select_task_rq_stop,
.set_cpus_allowed = set_cpus_allowed_common,
#endif
```

**`select_task_rq`** — "这个任务应该在哪个 CPU 上运行？"
```c
static int select_task_rq_stop(struct task_struct *p, int cpu, int flags)
{
    return task_cpu(p);  /* stop 任务永远不迁移，返回它当前绑定的 CPU */
}
```
> 这是 stop 任务**CPU 亲和性**的实现：每个 stop 任务在创建时就被绑定到特定 CPU（见 `kernel/stop_machine.c`），调度器永远不会把它迁移走。

**`balance`** — SMP 负载均衡时的钩子：
```c
static int balance_stop(struct rq *rq, struct task_struct *prev, struct rq_flags *rf)
{
    return sched_stop_runnable(rq);  /* 告诉核心：stop 任务是否可运行 */
}
```

---

#### 3.2.5 时钟 tick 接口

```c
.task_tick = task_tick_stop,
```

```c
static void task_tick_stop(struct rq *rq, struct task_struct *curr, int queued)
{
    /* 空实现 */
}
```

每个 scheduler tick（通常 4ms 或 1ms）都会调用当前任务所属调度类的 `task_tick`。CFS 在这里更新 `vruntime`，RT 在这里做时间片轮转。stop 类**什么都不做**——它不需要时间片管理，运行到完成为止。

---

#### 3.2.6 类切换通知接口

```c
.prio_changed = prio_changed_stop,
.switched_to  = switched_to_stop,
.update_curr  = update_curr_stop,
```

```c
static void switched_to_stop(struct rq *rq, struct task_struct *p)
{
    BUG(); /* its impossible to change to this class */
}

static void prio_changed_stop(struct rq *rq, struct task_struct *p, int oldprio)
{
    BUG(); /* how!?, what priority? */
}

static void update_curr_stop(struct rq *rq)
{
    /* 空实现 */
}
```

两个 `BUG()` 再次体现了**设计约束的强制执行**：stop 任务的调度类和优先级在内核初始化时就固定了，任何试图修改的行为都是 bug。

---

### 3.3 `DEFINE_SCHED_CLASS` 宏的秘密

```c
DEFINE_SCHED_CLASS(stop) = { ... };
```

这个宏展开后大致是：

```c
const struct sched_class stop_sched_class
    __aligned(__alignof__(struct sched_class))
    __section("__stop_sched_class") = { ... };
```

关键点：
- **`__section`**：将各调度类放入特定的 ELF section，链接器脚本保证它们在内存中**按优先级顺序排列**
- **`__aligned`**：保证 Cache Line 对齐，避免伪共享（false sharing）
- 调度器核心通过 `sched_class_highest`（指向 stop 类）开始遍历，`next_class` 指针串联整个链表

---

### 3.4 stop 调度类的整体设计总结

| 特性 | 实现方式 | 设计原因 |
|------|---------|---------|
| **最高优先级** | `check_preempt_curr` 为空函数 | 永不被抢占 |
| **不可迁移** | `select_task_rq` 返回当前 CPU | 绑定到特定 CPU |
| **无队列结构** | `enqueue/dequeue` 只维护计数 | 每 CPU 唯一，`rq->stop` 即队列 |
| **无时间片** | `task_tick` 为空 | 运行到完成，不需要轮转 |
| **不可切换** | `switched_to/prio_changed` 触发 BUG | 设计约束强制执行 |
| **用途** | `stop_machine()` 机制 | CPU 热插拔、内核 ftrace patch 等 |

---

### 3.5 stop 任务的实际使用场景

stop 任务由 `kernel/stop_machine.c` 创建和使用。典型场景：

```
CPU 热插拔 (cpu_down)
    └── stop_machine()
            └── 向目标 CPU 的 stop 任务投递工作
                    └── stop 任务抢占一切，执行 CPU 下线操作
                            └── 保证操作期间该 CPU 上没有其他任务运行
```

这就是为什么 stop 任务必须是**最高优先级且不可抢占**——它需要在一个"干净"的 CPU 上执行某些需要独占 CPU 的关键操作（如 `text_poke` 修改内核代码、`ftrace` 打 patch 等）。

---

## 4、RT调度类分析


RT 调度类实现了 POSIX 标准的两种实时调度策略：**`SCHED_FIFO`**（先进先出）和 **`SCHED_RR`**（时间片轮转）。它比 stop 类复杂一个数量级，核心难点在于：**优先级队列设计**、**带宽控制（Throttling）**、**SMP 下的 Push/Pull 负载均衡**。

---

### 4.1 核心数据结构

#### 4.1.1 `rt_prio_array` — RT 调度的灵魂：O(1) 优先级队列

```c
/* kernel/sched/sched.h */
struct rt_prio_array {
    DECLARE_BITMAP(bitmap, MAX_RT_PRIO + 1); /* 位图，+1 作为哨兵 */
    struct list_head queue[MAX_RT_PRIO];     /* 100 个优先级链表 */
};
```

这是 RT 调度器最核心的数据结构，设计极其精妙：

```
bitmap:  [0][0][1][0][1][0]...[1]  ← 第 i 位为 1 表示优先级 i 有任务
                                        最后一位是哨兵（永远为 1）
queue:   [0]→空  [1]→空  [2]→[task_A]→[task_B]  [3]→空  [4]→[task_C]
                              ↑ 优先级 2 有两个任务（SCHED_RR 轮转）
```

**为什么这样设计？**

`pick_next_rt_entity` 的选择逻辑：
```c
static struct sched_rt_entity *pick_next_rt_entity(struct rt_rq *rt_rq)
{
    struct rt_prio_array *array = &rt_rq->active;
    int idx;

    idx = sched_find_first_bit(array->bitmap);  /* ← 一条 BSF 指令，O(1) */
    BUG_ON(idx >= MAX_RT_PRIO);

    queue = array->queue + idx;
    next = list_entry(queue->next, struct sched_rt_entity, run_list);
    return next;
}
```

`sched_find_first_bit` 在 x86 上编译为单条 `BSF`（Bit Scan Forward）指令，**无论有多少任务，选出最高优先级任务永远是 O(1)**。这正是 RT 调度器相比 CFS 红黑树 O(log n) 的核心优势——实时性要求不允许调度延迟随任务数增长。

> **历史背景**：这个设计来自 Linux 2.6 引入的 O(1) 调度器（Ingo Molnár 设计），后来 CFS 替换了普通进程的调度，但 RT 的这个位图+链表结构保留至今，因为它对实时场景来说是最优解。

#### 4.1.2 `rt_rq` — RT 运行队列

```c
struct rt_rq {
    struct rt_prio_array active;       /* 核心：位图+优先级链表 */
    unsigned int rt_nr_running;        /* 可运行任务总数 */
    unsigned int rr_nr_running;        /* SCHED_RR 任务数（用于时间片管理）*/

    /* 带宽控制 */
    u64 rt_time;                       /* 当前周期内已消耗的 RT 时间 */
    u64 rt_runtime;                    /* 当前周期内允许使用的 RT 时间 */
    raw_spinlock_t rt_runtime_lock;
    int rt_throttled;                  /* 是否被限流 */

    /* SMP 相关 */
    struct {
        int curr;   /* 当前最高优先级（数值越小优先级越高）*/
        int next;   /* pushable_tasks 中最高优先级 */
    } highest_prio;
    unsigned long rt_nr_migratory;    /* 可迁移任务数 */
    int overloaded;                   /* 是否过载（有多余 RT 任务需要推出）*/
    struct plist_head pushable_tasks; /* 可被推走的任务（按优先级排序）*/
};
```

---

### 4.2 入队与出队：`enqueue_task_rt` / `dequeue_task_rt`

#### 4.2.1 入队调用链

```
enqueue_task_rt(rq, p, flags)
    └── enqueue_rt_entity(rt_se, flags)
            ├── dequeue_rt_stack(rt_se, flags)   ← 先从顶到底全部出队
            ├── for_each_sched_rt_entity(rt_se)
            │       └── __enqueue_rt_entity(rt_se, flags)  ← 再从底到顶入队
            └── enqueue_top_rt_rq(&rq->rt)       ← 更新全局计数
    └── enqueue_pushable_task(rq, p)             ← SMP：加入可推任务列表
```

**`__enqueue_rt_entity` 的核心逻辑：**

```c
static void __enqueue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct rt_prio_array *array = &rt_rq->active;
    struct list_head *queue = array->queue + rt_se_prio(rt_se);

    if (flags & ENQUEUE_HEAD)
        list_add(&rt_se->run_list, queue);       /* SCHED_FIFO 抢占后回队头 */
    else
        list_add_tail(&rt_se->run_list, queue);  /* 正常入队到队尾 */

    __set_bit(rt_se_prio(rt_se), array->bitmap); /* 置位，O(1) */
    rt_se->on_list = 1;
    rt_se->on_rq = 1;

    inc_rt_tasks(rt_se, rt_rq);  /* 更新计数、highest_prio、迁移统计 */
}
```

**关键设计：`ENQUEUE_HEAD` flag**

当一个 `SCHED_FIFO` 任务被高优先级任务抢占后重新入队，它会带着 `ENQUEUE_HEAD` 标志插入**队头**，保证它下次被选中时能立即继续执行，不会被同优先级的其他任务插队。这是 FIFO 语义的正确实现。

#### 4.2.2 `inc_rt_tasks` — 入队时的连锁更新

```c
void inc_rt_tasks(struct sched_rt_entity *rt_se, struct rt_rq *rt_rq)
{
    int prio = rt_se_prio(rt_se);

    rt_rq->rt_nr_running += rt_se_nr_running(rt_se);
    rt_rq->rr_nr_running += rt_se_rr_nr_running(rt_se);

    inc_rt_prio(rt_rq, prio);       /* 更新 highest_prio.curr */
    inc_rt_migration(rt_se, rt_rq); /* 更新迁移统计，触发 overload 标记 */
    inc_rt_group(rt_se, rt_rq);     /* 启动带宽计时器 */
}
```

`inc_rt_prio` 维护 `rt_rq->highest_prio.curr`：
```c
static void inc_rt_prio(struct rt_rq *rt_rq, int prio)
{
    int prev_prio = rt_rq->highest_prio.curr;
    if (prio < prev_prio)                    /* 数值小 = 优先级高 */
        rt_rq->highest_prio.curr = prio;
    inc_rt_prio_smp(rt_rq, prio, prev_prio); /* 更新全局 cpupri 表 */
}
```

`highest_prio.curr` 是 SMP 负载均衡的关键信息——其他 CPU 通过查询这个值来决定是否值得来"抢"任务。

---

### 4.3 入队/出队深度剖析

理解这块的关键是先搞清楚**三个层次**：

```
层次 1: enqueue_task_rt / dequeue_task_rt   ← 调度类接口，外部调用入口
层次 2: enqueue_rt_entity / dequeue_rt_entity  ← 处理组调度层级关系
层次 3: __enqueue_rt_entity / __dequeue_rt_entity  ← 真正操作位图和链表
```

---

#### 4.3.1 先理解数据结构，再看操作

#### 4.3.2 `rt_prio_array` 的物理形态

```
rt_rq->active:

bitmap:  位 0  位 1  位 2  位 3  ... 位 99  位 100(哨兵，永远=1)
         [ 0 ][ 0 ][ 1 ][ 0 ]  ...  [ 1  ][ 1 ]
                  ↑                    ↑
              prio=2 有任务          prio=99 有任务

queue[0]:  空链表
queue[1]:  空链表
queue[2]:  [rt_se_A] ↔ [rt_se_B]   ← 两个 prio=2 的任务（SCHED_RR 轮转）
queue[3]:  空链表
...
queue[99]: [rt_se_C]               ← 一个 prio=99 的任务
```

**`sched_rt_entity`（嵌在 `task_struct->rt` 里）的关键字段：**

```c
struct sched_rt_entity {
    struct list_head run_list;  /* 挂在 queue[prio] 链表上的节点 */
    int on_rq;                  /* 是否在运行队列中（逻辑上） */
    int on_list;                /* 是否在链表中（物理上） */
    /* 组调度相关 */
    struct rt_rq    *rt_rq;     /* 我所在的 rt_rq */
    struct rt_rq    *my_q;      /* 如果我是组实体，我管理的子 rt_rq */
    struct sched_rt_entity *parent; /* 父组实体 */
    struct sched_rt_entity *back;   /* dequeue_rt_stack 用的临时指针 */
};
```

> **`on_rq` vs `on_list` 的区别**：`on_rq=1` 表示逻辑上"在队列中可被调度"，`on_list=1` 表示物理上节点挂在链表里。两者通常一致，但在 `DEQUEUE_SAVE`（任务临时保存状态，如迁移）时会出现 `on_rq=0, on_list=1` 的中间态。

---

#### 4.3.3 层次 3：最底层的原子操作

#### 4.3.4 `__enqueue_rt_entity` — 把一个实体插入位图+链表

```c
static void __enqueue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct rt_rq *rt_rq = rt_rq_of_se(rt_se);
    struct rt_prio_array *array = &rt_rq->active;
    struct list_head *queue = array->queue + rt_se_prio(rt_se); /* 定位到对应优先级的链表 */

    /* ① 组调度特殊处理：子组被限流或为空时，不入队 */
    struct rt_rq *group_rq = group_rt_rq(rt_se);
    if (group_rq && (rt_rq_throttled(group_rq) || !group_rq->rt_nr_running)) {
        if (rt_se->on_list)
            __delist_rt_entity(rt_se, array); /* 如果已在链表，摘掉 */
        return;
    }

    /* ② 决定是否真的移动链表节点（move_entity 判断） */
    if (move_entity(flags)) {
        if (flags & ENQUEUE_HEAD)
            list_add(&rt_se->run_list, queue);      /* 插队头 */
        else
            list_add_tail(&rt_se->run_list, queue); /* 插队尾（正常路径）*/

        __set_bit(rt_se_prio(rt_se), array->bitmap); /* 置位 bitmap */
        rt_se->on_list = 1;
    }

    /* ③ 无论是否移动链表，逻辑状态都要更新 */
    rt_se->on_rq = 1;
    inc_rt_tasks(rt_se, rt_rq); /* 更新计数、highest_prio、迁移统计 */
}
```

**两个关键分支的含义：**

| `flags` | `move_entity` 返回 | 效果 |
|---|---|---|
| 正常入队（`ENQUEUE_WAKEUP` 等） | `true` | 节点插入链表 + bitmap 置位 |
| `DEQUEUE_SAVE`（临时保存） | `false` | 只更新 `on_rq`，不动链表 |
| `ENQUEUE_HEAD` | `true` | 插到队头（被抢占的 FIFO 任务回队） |

#### 4.3.5 `__dequeue_rt_entity` — 把一个实体从位图+链表摘除

```c
static void __dequeue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct rt_rq *rt_rq = rt_rq_of_se(rt_se);
    struct rt_prio_array *array = &rt_rq->active;

    if (move_entity(flags)) {
        __delist_rt_entity(rt_se, array); /* 从链表摘除，若链表空则清 bitmap */
    }
    rt_se->on_rq = 0;
    dec_rt_tasks(rt_se, rt_rq); /* 更新计数、highest_prio */
}

static void __delist_rt_entity(struct sched_rt_entity *rt_se, struct rt_prio_array *array)
{
    list_del_init(&rt_se->run_list); /* 从链表摘除，初始化为空链表 */

    /* 如果这个优先级的链表空了，清除 bitmap 对应位 */
    if (list_empty(array->queue + rt_se_prio(rt_se)))
        __clear_bit(rt_se_prio(rt_se), array->bitmap);

    rt_se->on_list = 0;
}
```

**`list_del_init` 而不是 `list_del` 的原因**：`list_del_init` 会把节点的 `prev/next` 指向自身，形成一个空链表节点。这样即使后续代码误操作这个节点，也不会产生野指针崩溃，是防御性编程的体现。

---

#### 4.3.6 层次 2：处理组调度层级 — 最难理解的部分

#### 4.3.7 为什么需要 `dequeue_rt_stack` + 重新入队？

这是整个入队/出队最难理解的地方。先看**没有组调度时**（`CONFIG_RT_GROUP_SCHED` 未开启）的简单情况：

```
task_A (prio=5)
    └── rt_se_A  →  挂在 rq->rt.active.queue[5] 上
```

这时 `for_each_sched_rt_entity(rt_se)` 只循环一次，`rt_se->parent = NULL`，逻辑很简单。

**开启组调度后**，层级变成这样：

```
root_rt_rq (rq->rt)
    └── queue[3]: [group_rt_se]          ← 组实体，代表整个 cgroup
                       │
                       └── my_q (子 rt_rq)
                               └── queue[5]: [task_rt_se_A]  ← 真正的任务
                               └── queue[7]: [task_rt_se_B]
```

**问题来了**：当 `task_rt_se_A` 的优先级从 5 变成 2 时，`group_rt_se` 在 `root_rt_rq` 里的位置也要跟着变（因为组的优先级 = 组内最高优先级任务的优先级）。

**如果直接修改 `task_rt_se_A` 的位置，`group_rt_se` 的位置就不对了！**

所以必须：
1. **先把整个层级从顶到底全部出队**（`dequeue_rt_stack`）
2. **再从底到顶重新入队**（`for_each_sched_rt_entity` 从叶子到根）

#### 4.3.8 `dequeue_rt_stack` 详解 — 从顶到底出队

```c
static void dequeue_rt_stack(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct sched_rt_entity *back = NULL;

    /* ① 遍历从当前实体到根，用 back 指针构建反向链 */
    for_each_sched_rt_entity(rt_se) {
        rt_se->back = back;
        back = rt_se;
    }
    /* 执行完后：back 指向最顶层（根）的实体
     * 链：root_se->back = NULL
     *      group_se->back = root_se
     *      task_se->back = group_se
     * back 指向 root_se（最顶层）
     */

    rt_nr_running = rt_rq_of_se(back)->rt_nr_running;

    /* ② 从顶到底逐层出队（沿 back 指针方向） */
    for (rt_se = back; rt_se; rt_se = rt_se->back) {
        if (on_rt_rq(rt_se))
            __dequeue_rt_entity(rt_se, flags);
    }

    dequeue_top_rt_rq(rt_rq_of_se(back), rt_nr_running);
}
```

**为什么必须从顶到底出队？**

因为父实体的优先级依赖子实体的状态。如果先出队子实体，父实体的 `highest_prio` 就会被错误更新，导致父实体在 `root_rt_rq` 里的位置计算错误。**从顶到底出队，保证每次出队时父实体的状态还是正确的**。

用图来说：

```
出队前：
root_rt_rq.queue[3]: [group_se]   ← group 的优先级=3（因为子任务 prio=3）
    └── child_rt_rq.queue[3]: [task_se]

正确顺序（顶→底）：
Step1: 先出队 group_se（从 root_rt_rq.queue[3] 摘除）
Step2: 再出队 task_se（从 child_rt_rq.queue[3] 摘除）

错误顺序（底→顶）：
Step1: 先出队 task_se → child_rt_rq 的 highest_prio 变成 MAX_RT_PRIO
Step2: 再出队 group_se → 但 group_se 的优先级已经被错误更新了！
```

#### 4.3.9 `enqueue_rt_entity` 详解 — 从底到顶重新入队

```c
static void enqueue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct rq *rq = rq_of_rt_se(rt_se);

    /* ① 先把整个层级全部出队（清理现场）*/
    dequeue_rt_stack(rt_se, flags);

    /* ② 从底到顶重新入队（for_each_sched_rt_entity 是从叶子到根的方向）*/
    for_each_sched_rt_entity(rt_se)
        __enqueue_rt_entity(rt_se, flags);

    /* ③ 更新顶层 rt_rq 的全局计数 */
    enqueue_top_rt_rq(&rq->rt);
}
```

**为什么入队是从底到顶？**

因为父实体的优先级 = 子 rt_rq 的 `highest_prio.curr`。必须先把子实体入队（更新子 rt_rq 的 `highest_prio`），父实体才能以正确的优先级插入父 rt_rq。

```
入队顺序（底→顶）：
Step1: task_se 入队 child_rt_rq.queue[2]
       → child_rt_rq.highest_prio.curr = 2
Step2: group_se 入队 root_rt_rq.queue[2]  ← 优先级从 child_rt_rq 读取，正确！
```

#### 4.3.10 `dequeue_rt_entity` 详解 — 出队但保留父组

```c
static void dequeue_rt_entity(struct sched_rt_entity *rt_se, unsigned int flags)
{
    struct rq *rq = rq_of_rt_se(rt_se);

    /* ① 同样先全部出队 */
    dequeue_rt_stack(rt_se, flags);

    /* ② 从底到顶，但只重新入队"还有其他任务的父组" */
    for_each_sched_rt_entity(rt_se) {
        struct rt_rq *rt_rq = group_rt_rq(rt_se);

        if (rt_rq && rt_rq->rt_nr_running)  /* 子 rt_rq 还有其他任务？ */
            __enqueue_rt_entity(rt_se, flags); /* 父组重新入队 */
        /* 如果子 rt_rq 空了，父组就不重新入队（整个组从调度器消失）*/
    }

    enqueue_top_rt_rq(&rq->rt);
}
```

**`enqueue_rt_entity` vs `dequeue_rt_entity` 的核心区别：**

| | `enqueue_rt_entity` | `dequeue_rt_entity` |
|---|---|---|
| **目的** | 把任务加入调度 | 把任务移出调度 |
| **Step1** | 全部出队（清理） | 全部出队（清理） |
| **Step2** | 从底到顶**全部**重新入队 | 从底到顶**有条件**重新入队（只有组内还有其他任务才重新入队父组）|

---

#### 4.3.11 层次 1：外部调用入口

#### 4.3.12 `enqueue_task_rt` — 完整入队流程

```c
static void enqueue_task_rt(struct rq *rq, struct task_struct *p, int flags)
{
    struct sched_rt_entity *rt_se = &p->rt;

    /* 从睡眠唤醒时，清除超时标记 */
    if (flags & ENQUEUE_WAKEUP)
        rt_se->timeout = 0;

    /* 核心：处理层级关系，插入位图+链表 */
    enqueue_rt_entity(rt_se, flags);

    /* SMP：如果不是当前运行任务，且可以迁移，加入 pushable_tasks */
    if (!task_current(rq, p) && p->nr_cpus_allowed > 1)
        enqueue_pushable_task(rq, p);
}
```

**`pushable_tasks` 的意义**：这是 SMP Push 机制的基础。一个任务如果正在运行（`task_current`），它不需要被推走；如果它只有一个 CPU 可用（`nr_cpus_allowed == 1`），也没法推。只有"在队列里等待 + 可以迁移"的任务才加入 `pushable_tasks`，供 Push 操作使用。

#### 4.3.13 `dequeue_task_rt` — 完整出队流程

```c
static void dequeue_task_rt(struct rq *rq, struct task_struct *p, int flags)
{
    struct sched_rt_entity *rt_se = &p->rt;

    /* 先更新执行时间统计（带宽控制需要）*/
    update_curr_rt(rq);

    /* 核心：处理层级关系，从位图+链表摘除 */
    dequeue_rt_entity(rt_se, flags);

    /* SMP：从 pushable_tasks 摘除 */
    dequeue_pushable_task(rq, p);
}
```

---

#### 4.3.14 完整流程图：一次任务唤醒的入队过程

以**无组调度**的简单场景为例，`task_A`（prio=5）从睡眠被唤醒：

```
wake_up_process(task_A)
    └── try_to_wake_up()
            └── enqueue_task(rq, task_A, ENQUEUE_WAKEUP)
                    └── enqueue_task_rt(rq, task_A, ENQUEUE_WAKEUP)
                            │
                            ├─ rt_se->timeout = 0  （清超时标记）
                            │
                            ├─ enqueue_rt_entity(rt_se, flags)
                            │       │
                            │       ├─ dequeue_rt_stack(rt_se, flags)
                            │       │       └─ 无组调度：rt_se->parent=NULL
                            │       │          for_each 只循环一次，back=rt_se
                            │       │          __dequeue_rt_entity(rt_se)
                            │       │          → 若 rt_se->on_rq=0，什么都不做
                            │       │
                            │       ├─ for_each_sched_rt_entity(rt_se)
                            │       │       └─ __enqueue_rt_entity(rt_se, flags)
                            │       │               ├─ queue = active.queue + 5
                            │       │               ├─ list_add_tail(&rt_se->run_list, queue)
                            │       │               │   → queue[5]: [rt_se_A]
                            │       │               ├─ __set_bit(5, bitmap)
                            │       │               │   → bitmap: ...1... (第5位置1)
                            │       │               ├─ rt_se->on_list = 1
                            │       │               ├─ rt_se->on_rq = 1
                            │       │               └─ inc_rt_tasks()
                            │       │                   ├─ rt_nr_running++
                            │       │                   ├─ highest_prio.curr = min(curr, 5)
                            │       │                   └─ 更新 cpupri 表（SMP）
                            │       │
                            │       └─ enqueue_top_rt_rq(&rq->rt)
                            │               └─ add_nr_running(rq, rt_nr_running)
                            │
                            └─ enqueue_pushable_task(rq, task_A)
                                    └─ plist_add(&task_A->pushable_tasks, ...)
```

---

#### 4.3.15 一个具体场景：SCHED_FIFO 任务被抢占后重新入队

这个场景最能体现 `ENQUEUE_HEAD` 的作用：

```
初始状态：
queue[5]: [task_A(FIFO)] ← 正在运行

事件：task_B(prio=3) 被唤醒，优先级更高，抢占 task_A

check_preempt_curr_rt:
    p->prio(3) < curr->prio(5) → resched_curr(rq)

上下文切换：
    put_prev_task_rt(rq, task_A)
        → task_A 还在 rt_rq 上（on_rq=1），加入 pushable_tasks

    pick_next_task_rt → 选出 task_B

task_A 重新入队时（它还在 queue[5] 里，没有被 dequeue）：
    实际上 task_A 在被抢占时并没有 dequeue，它仍然 on_rq=1
    只是不再是 curr，等 task_B 结束后会再次被 pick_next 选中
    
如果 task_A 主动让出（yield）：
    requeue_task_rt(rq, task_A, 0)  ← 移到队尾
    → queue[5]: [task_A] 移到末尾，其他同优先级任务排前面

如果 task_A 是被高优先级抢占后重新调度：
    它仍在 queue[5] 的原位置（FIFO 语义：不改变在同优先级中的顺序）
```

---

#### 4.3.16 总结：入队/出队的设计精髓

```
┌─────────────────────────────────────────────────────┐
│              RT 入队/出队核心设计                     │
├─────────────────────────────────────────────────────┤
│  底层操作（__enqueue/__dequeue）                      │
│  → 直接操作 bitmap + 链表，O(1)                       │
│  → on_rq（逻辑状态）与 on_list（物理状态）分离         │
│  → ENQUEUE_HEAD 保证 FIFO 语义                        │
├─────────────────────────────────────────────────────┤
│  中层操作（enqueue/dequeue_rt_entity）                │
│  → 解决组调度层级一致性问题                           │
│  → dequeue_rt_stack：顶→底出队（保证父依赖子的正确性）  │
│  → 重新入队：底→顶（子优先级确定后，父才能正确定位）    │
│  → dequeue 时：父组有其他任务则保留，否则整组消失       │
├─────────────────────────────────────────────────────┤
│  顶层操作（enqueue/dequeue_task_rt）                  │
│  → 入口：更新超时标记、维护 pushable_tasks（SMP）      │
│  → 出口：先更新时间统计（带宽控制），再出队             │
└─────────────────────────────────────────────────────┘
```

---

### 4.4 带宽控制（RT Throttling）— 防止 RT 任务饿死系统

#### 4.4.1 为什么需要带宽控制？

RT 任务优先级高于普通进程，如果一个 `SCHED_FIFO` 任务进入死循环，它会**永久占用 CPU**，导致系统完全失去响应（连 shell 都无法操作）。带宽控制就是为了解决这个问题。

#### 4.4.2 带宽控制的参数

```bash
# 默认配置：每 1 秒周期内，RT 任务最多使用 950ms（95%）
/proc/sys/kernel/sched_rt_period_us  = 1000000  (1秒)
/proc/sys/kernel/sched_rt_runtime_us = 950000   (950ms)
```

对应内核结构：
```c
struct rt_bandwidth {
    raw_spinlock_t rt_runtime_lock;
    ktime_t rt_period;          /* 周期，默认 1s */
    u64 rt_runtime;             /* 每周期允许的 RT 运行时间，默认 950ms */
    struct hrtimer rt_period_timer; /* 高精度定时器，周期到期时重置配额 */
    unsigned int rt_period_active;
};
```

#### 4.4.3 带宽控制的工作流程

```
每个 scheduler tick / 上下文切换时
    └── update_curr_rt(rq)
            ├── 计算 delta_exec（本次执行时间）
            ├── rt_rq->rt_time += delta_exec    ← 累计消耗
            └── sched_rt_runtime_exceeded(rt_rq)
                    ├── 若 rt_time > rt_runtime
                    │       ├── balance_runtime()  ← SMP：尝试从邻居借时间
                    │       ├── rt_rq->rt_throttled = 1
                    │       ├── sched_rt_rq_dequeue(rt_rq)  ← 从调度器摘除！
                    │       └── resched_curr(rq)   ← 强制重新调度
                    └── 触发带宽定时器

周期到期（sched_rt_period_timer 回调）
    └── do_sched_rt_period_timer()
            ├── rt_rq->rt_time -= min(rt_time, overrun * runtime)  ← 重置配额
            ├── rt_rq->rt_throttled = 0
            └── sched_rt_rq_enqueue(rt_rq)  ← 重新加入调度！
```

#### 4.4.4 SMP 下的运行时借用（Runtime Stealing）

这是一个非常精妙的设计：当某个 CPU 的 RT 配额耗尽时，它可以**从其他 CPU 借用剩余配额**：

```c
static void do_balance_runtime(struct rt_rq *rt_rq)
{
    /* 遍历同一 root domain 内的所有 CPU */
    for_each_cpu(i, rd->span) {
        struct rt_rq *iter = sched_rt_period_rt_rq(rt_b, i);
        s64 diff;

        /* 从有剩余配额的 CPU 借用 1/n */
        diff = iter->rt_runtime - iter->rt_time;
        if (diff > 0) {
            diff = div_u64((u64)diff, weight);  /* 借 1/n */
            iter->rt_runtime -= diff;
            rt_rq->rt_runtime += diff;          /* 我的配额增加 */
        }
    }
}
```

**设计权衡**：这个特性由 `sched_feat(RT_RUNTIME_SHARE)` 控制。借用机制提高了 RT 任务的灵活性，但也意味着单个 CPU 的 RT 配额可能超过全局设定值。可通过 `echo NO_RT_RUNTIME_SHARE > /sys/kernel/debug/sched/features` 关闭。

---

### 4.5 `pick_next_task_rt` — 选择下一个任务

```c
static struct task_struct *_pick_next_task_rt(struct rq *rq)
{
    struct sched_rt_entity *rt_se;
    struct rt_rq *rt_rq = &rq->rt;

    /* 支持 RT 组调度时，需要逐层向下选择 */
    do {
        rt_se = pick_next_rt_entity(rt_rq); /* 位图 O(1) 找最高优先级 */
        if (unlikely(!rt_se))
            return NULL;
        rt_rq = group_rt_rq(rt_se);         /* 若是组实体，进入子队列 */
    } while (rt_rq);

    return rt_task_of(rt_se);
}
```

不启用组调度时，`group_rt_rq` 返回 NULL，循环只执行一次，极其高效。

---

### 4.6 `task_tick_rt` — SCHED_RR 时间片轮转

```c
static void task_tick_rt(struct rq *rq, struct task_struct *p, int queued)
{
    struct sched_rt_entity *rt_se = &p->rt;

    update_curr_rt(rq);  /* 更新执行时间，检查带宽 */
    update_rt_rq_load_avg(rq_clock_pelt(rq), rq, 1);

    /* SCHED_FIFO 没有时间片，直接返回 */
    if (p->policy != SCHED_RR)
        return;

    /* SCHED_RR：时间片递减 */
    if (--p->rt.time_slice)
        return;

    /* 时间片耗尽，重置并移到队尾（轮转） */
    p->rt.time_slice = sched_rr_timeslice;

    for_each_sched_rt_entity(rt_se) {
        if (rt_se->run_list.prev != rt_se->run_list.next) {
            requeue_task_rt(rq, p, 0);  /* 移到同优先级队列的队尾 */
            resched_curr(rq);
            return;
        }
    }
}
```

**`SCHED_FIFO` vs `SCHED_RR` 的本质区别**就在这里：
- `SCHED_FIFO`：`task_tick` 里直接 return，永远不会因时间片耗尽而让出 CPU
- `SCHED_RR`：时间片耗尽后 `requeue_task_rt` 移到队尾，同优先级任务轮转

---

### 4.7 SMP 负载均衡：Push/Pull 机制

RT 调度器的 SMP 均衡不使用 CFS 那套复杂的 load_balance 框架，而是采用**主动 Push + 被动 Pull** 的双向机制。

#### 4.7.1 整体架构

```
场景：CPU0 有 2 个 RT 任务（prio=10, prio=20），CPU1 空闲

Push（主动推）：CPU0 发现自己过载
    └── push_rt_task(rq0)
            ├── pick_next_pushable_task(rq0)  ← 找 prio=20 的任务（次高优先级）
            ├── find_lowest_rq(task)          ← 找优先级最低的 CPU（CPU1）
            └── migrate_task_to(task, cpu1)   ← 迁移过去

Pull（被动拉）：CPU1 的当前任务优先级降低时
    └── pull_rt_task(rq1)
            ├── 遍历 rto_mask（有过载 RT 任务的 CPU 集合）
            ├── 找到 CPU0 上比 CPU1 当前任务优先级更高的可迁移任务
            └── migrate_task_to(task, cpu1)
```

#### 4.7.2 `pushable_tasks` — plist 优先级链表

```c
/* 入队时，非当前运行的 RT 任务加入 pushable_tasks */
static void enqueue_pushable_task(struct rq *rq, struct task_struct *p)
{
    plist_del(&p->pushable_tasks, &rq->rt.pushable_tasks);
    plist_node_init(&p->pushable_tasks, p->prio);  /* 按优先级排序 */
    plist_add(&p->pushable_tasks, &rq->rt.pushable_tasks);

    if (p->prio < rq->rt.highest_prio.next)
        rq->rt.highest_prio.next = p->prio;  /* 维护次高优先级 */
}
```

`plist`（Priority List）是内核专为此场景设计的有序链表，插入 O(n) 但取最高优先级 O(1)。

#### 4.7.3 `find_lowest_rq` — 找最合适的目标 CPU

```c
static int find_lowest_rq(struct task_struct *task)
{
    /* 1. 通过 cpupri 表快速找到优先级最低的 CPU 集合 */
    ret = cpupri_find(&task_rq(task)->rd->cpupri, task, lowest_mask);

    /* 2. 优先选择 cache-hot 的上次执行 CPU */
    if (cpumask_test_cpu(cpu, lowest_mask))
        return cpu;

    /* 3. 按 sched_domain 拓扑选择最近的 CPU（NUMA 感知）*/
    for_each_domain(cpu, sd) {
        if (sd->flags & SD_WAKE_AFFINE) {
            /* 优先选 this_cpu（本地 CPU，切换开销最小）*/
            if (this_cpu != -1 && cpumask_test_cpu(this_cpu, ...))
                return this_cpu;
            /* 否则选 domain 内任意合适 CPU */
            best_cpu = cpumask_any_and_distribute(lowest_mask, ...);
        }
    }
}
```

选择顺序体现了三个层次的权衡：**实时性 > Cache 亲和性 > NUMA 拓扑**。

#### 4.7.4 `overload` 机制 — 全局过载感知

```c
/* 当某 CPU 有多个可迁移 RT 任务时，标记为过载 */
static void update_rt_migration(struct rt_rq *rt_rq)
{
    if (rt_rq->rt_nr_migratory && rt_rq->rt_nr_total > 1) {
        if (!rt_rq->overloaded) {
            rt_set_overload(rq);  /* 在 rd->rto_mask 中置位，原子增加 rto_count */
            rt_rq->overloaded = 1;
        }
    }
}
```

`rd->rto_mask` 是一个全局位图，记录哪些 CPU 处于 RT 过载状态。Pull 操作时只需扫描这个位图，避免遍历所有 CPU。

---

### 4.8 `check_preempt_curr_rt` — 抢占决策

```c
static void check_preempt_curr_rt(struct rq *rq, struct task_struct *p, int flags)
{
    /* 新任务优先级更高，直接抢占 */
    if (p->prio < rq->curr->prio) {
        resched_curr(rq);
        return;
    }

    /* 同优先级的特殊处理（SMP 场景）*/
    if (p->prio == rq->curr->prio && !test_tsk_need_resched(rq->curr))
        check_preempt_equal_prio(rq, p);
}
```

同优先级时的逻辑非常微妙：
```c
static void check_preempt_equal_prio(struct rq *rq, struct task_struct *p)
{
    /* 当前任务不可迁移，且有其他 CPU 可以接收它 → 触发重调度，尝试推走当前任务 */
    if (rq->curr->nr_cpus_allowed == 1 || !cpupri_find(..., rq->curr, NULL))
        return;

    /* 新任务可迁移，让它去别的 CPU → 不抢占 */
    if (p->nr_cpus_allowed != 1 && cpupri_find(..., p, NULL))
        return;

    /* 当前任务可以被推走，触发重调度腾出位置给 p */
    requeue_task_rt(rq, p, 1);
    resched_curr(rq);
}
```

---

### 4.9 整体架构总结

```
┌─────────────────────────────────────────────────────────┐
│                    RT 调度类全景                          │
├─────────────────────────────────────────────────────────┤
│  数据结构层                                               │
│  rt_prio_array: bitmap[100] + queue[100]                 │
│  → O(1) 选出最高优先级任务（BSF 指令）                    │
├─────────────────────────────────────────────────────────┤
│  调度策略层                                               │
│  SCHED_FIFO: 不限时间片，抢占后回队头，永不主动让出        │
│  SCHED_RR:   有时间片(默认100ms)，耗尽后移队尾轮转         │
├─────────────────────────────────────────────────────────┤
│  带宽控制层                                               │
│  rt_time > rt_runtime → throttled → 摘出调度器           │
│  hrtimer 周期到期 → 重置配额 → 重新入队                   │
│  SMP 下可向邻居借用剩余配额（RT_RUNTIME_SHARE）            │
├─────────────────────────────────────────────────────────┤
│  SMP 均衡层                                               │
│  Push: 过载 CPU 主动推次高优先级任务到低优先级 CPU         │
│  Pull: CPU 优先级降低时主动拉更高优先级的可迁移任务        │
│  cpupri 表: 全局 CPU 优先级索引，O(1) 找目标 CPU          │
│  rto_mask: 过载 CPU 位图，避免全局扫描                    │
└─────────────────────────────────────────────────────────┘
```

---

## 5、Deadline 调度类分析

Deadline 调度类是 Linux 调度器里**理论基础最严谨**的一个，基于两个经典实时调度理论：
- **EDF（Earliest Deadline First）**：最早截止期限优先，可证明是单处理器上最优的实时调度算法
- **CBS（Constant Bandwidth Server）**：恒定带宽服务器，解决 EDF 在任务超时时的级联失效问题

---

### 5.1 核心参数与数据结构

#### 5.1.1 用户配置的三个参数

通过 `sched_setattr()` 系统调用设置，每个 DL 任务有三个时间参数：

```
┌─────────────────────────────────────────────────────┐
│  dl_runtime  │←────── dl_deadline ──────────────→│  │
│  (预算/配额)  │                                    │  │
├──────────────┴────────────────────────────────────┤  │
│←──────────────── dl_period ──────────────────────→│  │
└─────────────────────────────────────────────────────┘
  t=0                                              t=T
```

- **`dl_runtime`**：每个周期内允许执行的最大时间（预算）
- **`dl_deadline`**：相对截止期限，任务必须在此时间内完成本次执行
- **`dl_period`**：任务的周期，通常 `dl_deadline <= dl_period`

**带宽（Bandwidth）**的定义：`U = dl_runtime / dl_period`，表示该任务占用 CPU 的比例。

#### 5.1.2 `sched_dl_entity` — DL 调度实体

```c
struct sched_dl_entity {
    struct rb_node rb_node;      /* 挂在红黑树上的节点，按 deadline 排序 */

    /* 用户配置的静态参数 */
    u64 dl_runtime;              /* 每周期最大运行时间 */
    u64 dl_deadline;             /* 相对截止期限 */
    u64 dl_period;               /* 周期 */
    u64 dl_bw;                   /* 带宽 = dl_runtime / dl_period（定点数）*/
    u64 dl_density;              /* 密度 = dl_runtime / dl_deadline */

    /* 运行时动态状态 */
    s64 runtime;                 /* 当前周期剩余预算（可为负！）*/
    u64 deadline;                /* 当前实例的绝对截止期限 */

    /* 状态标志 */
    unsigned int dl_throttled:1; /* 预算耗尽，被限流 */
    unsigned int dl_yielded:1;   /* 主动让出 */
    unsigned int dl_non_contending:1; /* 阻塞但 0-lag 时间未到 */
    unsigned int dl_overrun:1;   /* 发生了运行时超限 */

    /* 两个高精度定时器 */
    struct hrtimer dl_timer;      /* 带宽补充定时器（周期到期时触发）*/
    struct hrtimer inactive_timer; /* 0-lag 定时器（阻塞时使用）*/

    /* PI 优先级继承 */
    struct sched_dl_entity *pi_se; /* 被 boost 时指向 boosting 实体 */
};
```

#### 5.1.3 `dl_rq` — DL 运行队列

```c
struct dl_rq {
    struct rb_root_cached root;  /* 红黑树，按 deadline 排序，最左节点=最早截止 */

    /* 带宽统计 */
    u64 running_bw;   /* 当前"活跃竞争"任务的带宽之和（正在运行或刚阻塞）*/
    u64 this_bw;      /* 本 CPU 上所有 DL 任务的带宽之和 */
    u64 extra_bw;     /* GRUB 可回收的额外带宽 */
    u64 bw_ratio;     /* 1/Umax，用于 GRUB 计算 */

    /* SMP 相关 */
    struct {
        u64 curr;  /* 当前最早截止期限 */
        u64 next;  /* pushable 任务中最早截止期限 */
    } earliest_dl;
    int dl_nr_migratory;
    int overloaded;
    struct rb_root_cached pushable_dl_tasks_root; /* 可推任务红黑树（按 deadline）*/
};
```

> **与 RT 的关键区别**：RT 用 `plist`（按优先级数值排序的链表）管理 pushable 任务，DL 用**红黑树**（按 deadline 排序）。因为 DL 的"优先级"是动态变化的 deadline，红黑树能高效维护有序性。

---

### 5.2 EDF 调度：红黑树实现

#### 5.2.1 为什么用红黑树而不是位图？

RT 调度器用 `bitmap + queue[100]` 实现 O(1) 选择，因为优先级是**静态的离散值**（0-99）。

DL 调度器的"优先级"是**绝对截止期限**（一个 64 位纳秒时间戳），是连续变化的值，无法用位图表示。红黑树按 deadline 排序，最左节点（`rb_leftmost`）就是最早截止期限的任务，`pick_next` 是 O(1)（直接取最左节点），插入/删除是 O(log n)。

#### 5.2.2 `__enqueue_dl_entity` — 插入红黑树

```c
static void __enqueue_dl_entity(struct sched_dl_entity *dl_se)
{
    struct dl_rq *dl_rq = dl_rq_of_se(dl_se);

    BUG_ON(!RB_EMPTY_NODE(&dl_se->rb_node)); /* 不能重复入队 */

    /* 按 deadline 插入红黑树，自动维护 rb_leftmost 缓存 */
    rb_add_cached(&dl_se->rb_node, &dl_rq->root, __dl_less);

    inc_dl_tasks(dl_se, dl_rq);
}

/* 比较函数：deadline 更早（数值更小）的排在左边 */
static inline bool __dl_less(struct rb_node *a, const struct rb_node *b)
{
    return dl_time_before(__node_2_dle(a)->deadline,
                          __node_2_dle(b)->deadline);
}
```

`rb_add_cached` 是内核红黑树的带缓存版本，它在插入时自动维护 `root.rb_leftmost` 指针，使得取最小值（最早 deadline）始终是 O(1)。

#### 5.2.3 `pick_next_dl_entity` — O(1) 选出最早截止任务

```c
static struct sched_dl_entity *pick_next_dl_entity(struct dl_rq *dl_rq)
{
    struct rb_node *left = rb_first_cached(&dl_rq->root); /* 直接取缓存的最左节点 */
    if (!left)
        return NULL;
    return rb_entry(left, struct sched_dl_entity, rb_node);
}
```

**红黑树的物理形态：**

```
         [deadline=100ms]          ← rb_leftmost，最早截止，下次被选中
        /                \
  [deadline=80ms]    [deadline=150ms]
  ← 等等，这不对！

正确理解：数值小=时间早=优先级高，rb_leftmost 是 deadline 数值最小的节点

         [deadline=500ms]
        /                \
  [deadline=300ms]    [deadline=800ms]
  ↑ rb_leftmost = 300ms，这个任务最先被选中
```

---

### 5.3 CBS 算法：防止超时任务破坏系统

#### 5.3.1 纯 EDF 的致命缺陷

纯 EDF 算法有一个严重问题：如果一个任务**超出了它声明的运行时间**，它的 deadline 还是很早，会继续抢占其他任务，导致**级联失效**——一个任务的超时会让所有其他任务都错过截止期限。

CBS 的解决方案：**当任务耗尽预算时，把它的 deadline 推迟到未来**，使其在 EDF 排序中自动降低优先级，不再影响其他任务。

#### 5.3.2 CBS 的核心规则

**规则 1：新实例开始时**（`setup_new_dl_entity`）
```c
static inline void setup_new_dl_entity(struct sched_dl_entity *dl_se)
{
    /* 绝对截止期限 = 当前时间 + 相对截止期限 */
    dl_se->deadline = rq_clock(rq) + dl_se->dl_deadline;
    /* 预算重置为最大值 */
    dl_se->runtime = dl_se->dl_runtime;
}
```

**规则 2：任务唤醒时**（`update_dl_entity`）—— CBS 唤醒规则

```c
static void update_dl_entity(struct sched_dl_entity *dl_se)
{
    /* 情况 A：deadline 已过期，或剩余预算/剩余时间 > 分配带宽 */
    if (dl_time_before(dl_se->deadline, rq_clock(rq)) ||
        dl_entity_overflow(dl_se, rq_clock(rq))) {

        /* 对于 constrained deadline 任务（deadline < period），用修订版 CBS */
        if (unlikely(!dl_is_implicit(dl_se) && ...)) {
            update_dl_revised_wakeup(dl_se, rq);  /* 调整 runtime，不重置 deadline */
            return;
        }

        /* 原始 CBS：重置 deadline 和 runtime */
        dl_se->deadline = rq_clock(rq) + pi_of(dl_se)->dl_deadline;
        dl_se->runtime = pi_of(dl_se)->dl_runtime;
    }
    /* 情况 B：剩余预算充足，保留当前 deadline 和 runtime 继续使用 */
}
```

**`dl_entity_overflow` 的判断逻辑：**

```c
static bool dl_entity_overflow(struct sched_dl_entity *dl_se, u64 t)
{
    /* 检查：剩余预算/剩余时间 > dl_runtime/dl_deadline ? */
    /* 即：runtime / (deadline - t) > dl_runtime / dl_deadline */
    /* 变形为乘法避免除法：*/
    left  = dl_deadline * runtime;          /* 左边 */
    right = (deadline - t) * dl_runtime;    /* 右边 */
    return dl_time_before(right, left);     /* left > right 则溢出 */
}
```

这个检查的含义：如果任务用剩余预算在剩余时间内运行，会超过它声明的带宽，就必须重置。

**规则 3：预算耗尽时**（`replenish_dl_entity`）

```c
static void replenish_dl_entity(struct sched_dl_entity *dl_se)
{
    /* 不断把 deadline 推迟一个 period，直到 runtime 变正 */
    while (dl_se->runtime <= 0) {
        dl_se->deadline += pi_of(dl_se)->dl_period;  /* deadline 推迟 */
        dl_se->runtime += pi_of(dl_se)->dl_runtime;  /* 补充预算 */
    }
}
```

这就是 CBS 的精髓：**超时的任务通过推迟 deadline 来自动降低优先级**，在红黑树中被排到后面，不再影响其他任务。

---

### 5.4 入队与出队

#### 5.4.1 `enqueue_task_dl` — 完整入队流程

```c
static void enqueue_task_dl(struct rq *rq, struct task_struct *p, int flags)
{
    /* ① PI boost 处理：被优先级继承提升的任务，取消限流 */
    if (is_dl_boosted(&p->dl)) {
        if (p->dl.dl_throttled) {
            hrtimer_try_to_cancel(&p->dl.dl_timer);
            p->dl.dl_throttled = 0;
        }
    }

    /* ② constrained deadline 检查：deadline < period 的任务，
          若在 deadline 之后、下个 period 之前唤醒，直接限流 */
    if (!p->dl.dl_throttled && !dl_is_implicit(&p->dl))
        dl_check_constrained_dl(&p->dl);

    /* ③ 迁移/恢复时更新带宽统计 */
    if (p->on_rq == TASK_ON_RQ_MIGRATING || flags & ENQUEUE_RESTORE) {
        add_rq_bw(&p->dl, &rq->dl);       /* this_bw += dl_bw */
        add_running_bw(&p->dl, &rq->dl);  /* running_bw += dl_bw */
    }

    /* ④ 被限流的任务：只更新带宽统计，不入红黑树（等定时器补充预算）*/
    if (p->dl.dl_throttled && !(flags & ENQUEUE_REPLENISH)) {
        if (flags & ENQUEUE_WAKEUP)
            task_contending(&p->dl, flags); /* 更新 running_bw */
        return;  /* ← 注意：直接返回，不入红黑树！*/
    }

    /* ⑤ 核心：入队到红黑树 */
    enqueue_dl_entity(&p->dl, flags);

    /* ⑥ SMP：加入 pushable 红黑树 */
    if (!task_current(rq, p) && p->nr_cpus_allowed > 1)
        enqueue_pushable_dl_task(rq, p);
}
```

**`enqueue_dl_entity` 根据 flags 决定如何更新 deadline/runtime：**

```c
static void enqueue_dl_entity(struct sched_dl_entity *dl_se, int flags)
{
    if (flags & ENQUEUE_WAKEUP) {
        task_contending(dl_se, flags);  /* 更新 running_bw */
        update_dl_entity(dl_se);        /* CBS 唤醒规则：决定是否重置 deadline */
    } else if (flags & ENQUEUE_REPLENISH) {
        replenish_dl_entity(dl_se);     /* 预算补充：推迟 deadline，补充 runtime */
    } else if ((flags & ENQUEUE_RESTORE) && deadline_已过期) {
        setup_new_dl_entity(dl_se);     /* 全新开始 */
    }

    __enqueue_dl_entity(dl_se);         /* 插入红黑树 */
}
```

| `flags` | 触发场景 | deadline/runtime 处理 |
|---|---|---|
| `ENQUEUE_WAKEUP` | 任务从睡眠唤醒 | CBS 唤醒规则（可能重置，可能保留）|
| `ENQUEUE_REPLENISH` | `dl_timer` 回调，预算补充 | 推迟 deadline，补充 runtime |
| `ENQUEUE_RESTORE` | 任务迁移后恢复 | 若 deadline 已过则重新初始化 |

#### 5.4.2 `dequeue_task_dl` — 出队流程

```c
static void dequeue_task_dl(struct rq *rq, struct task_struct *p, int flags)
{
    /* ① 先更新执行时间统计（可能触发限流）*/
    update_curr_dl(rq);

    /* ② 从红黑树摘除 + 从 pushable 树摘除 */
    __dequeue_task_dl(rq, p, flags);

    /* ③ 迁移/保存时更新带宽统计 */
    if (p->on_rq == TASK_ON_RQ_MIGRATING || flags & DEQUEUE_SAVE) {
        sub_running_bw(&p->dl, &rq->dl);
        sub_rq_bw(&p->dl, &rq->dl);
    }

    /* ④ 任务阻塞时：启动 0-lag 定时器（inactive_timer）*/
    if (flags & DEQUEUE_SLEEP)
        task_non_contending(p);
}
```

---

### 5.5 带宽控制：两个定时器 + 三态模型

这是 DL 调度类最精妙也最复杂的部分，比 RT 的带宽控制复杂得多。

#### 5.5.1 任务的三种状态

```
                    wakeup / add_running_bw
         ┌─────────────────────────────────────┐
         │                                     ▼
  ┌──────┴──────┐   dequeue(sleep)   ┌──────────────────┐
  │  INACTIVE   │◄───────────────────│ ACTIVE contending│
  │(running_bw  │   t >= 0-lag 时    │  (在红黑树里，    │
  │ 不计入)     │   直接减 running_bw │   running_bw 计入)│
  └──────┬──────┘                    └────────┬─────────┘
         ▲                                    │ dequeue(sleep)
         │                                    │ t < 0-lag 时
         │  inactive_timer 触发               ▼
         │  sub_running_bw        ┌──────────────────────┐
         └────────────────────────│  ACTIVE non-contending│
                                  │  (已阻塞，但 running_bw│
                                  │   仍计入，等 0-lag 到期)│
                                  └──────────────────────┘
```

#### 5.5.2 为什么需要 0-lag 时间和 inactive_timer？

**问题**：任务阻塞时，能立即把它的带宽从 `running_bw` 中减去吗？

**不能！** 因为 CBS 保证：只要任务在 deadline 之前完成，它就不会影响其他任务。如果任务阻塞后立即释放带宽，其他任务可能会"借用"这个带宽，但如果阻塞的任务很快又唤醒，就会出现带宽超额。

**0-lag 时间**是 CBS 理论中的一个时间点，在这个时刻之后，任务的带宽才真正"归还"给系统：

```c
static void task_non_contending(struct task_struct *p)
{
    /* 0-lag 时间 = deadline - (runtime * period / dl_runtime) */
    zerolag_time = dl_se->deadline -
        div64_long((dl_se->runtime * dl_se->dl_period), dl_se->dl_runtime);

    zerolag_time -= rq_clock(rq);

    if (zerolag_time < 0) {
        /* 0-lag 已过：立即减少 running_bw */
        sub_running_bw(dl_se, dl_rq);
    } else {
        /* 0-lag 未到：启动定时器，到时再减 */
        dl_se->dl_non_contending = 1;
        hrtimer_start(timer, ns_to_ktime(zerolag_time), HRTIMER_MODE_REL_HARD);
    }
}
```

#### 5.5.3 `dl_timer` — 带宽补充定时器

当任务预算耗尽（`dl_throttled=1`），`dl_timer` 被设置到**下一个周期开始时**触发：

```c
static enum hrtimer_restart dl_task_timer(struct hrtimer *timer)
{
    /* 定时器触发 = 新周期开始，补充预算 */
    if (!task_on_rq_queued(p)) {
        /* 任务已阻塞：只补充预算，不入队，等唤醒时再入队 */
        replenish_dl_entity(dl_se);
        goto unlock;
    }

    /* 任务还在队列：补充预算并重新入队 */
    enqueue_task_dl(rq, p, ENQUEUE_REPLENISH);

    /* 检查是否需要抢占当前任务 */
    if (dl_task(rq->curr))
        check_preempt_curr_dl(rq, p, 0);
    else
        resched_curr(rq);

    /* 如果有可推任务，触发 Push */
    if (has_pushable_dl_tasks(rq))
        push_dl_task(rq);
}
```

#### 5.5.4 `update_curr_dl` — 每次调度时的预算消耗

```c
static void update_curr_dl(struct rq *rq)
{
    now = rq_clock_task(rq);
    delta_exec = now - curr->se.exec_start;

    /* GRUB 带宽回收：按实际利用率缩放消耗 */
    if (dl_se->flags & SCHED_FLAG_RECLAIM) {
        scaled_delta_exec = grub_reclaim(delta_exec, rq, &curr->dl);
    } else {
        /* 普通模式：按 CPU 频率和容量缩放 */
        scaled_delta_exec = cap_scale(delta_exec, scale_freq);
        scaled_delta_exec = cap_scale(scaled_delta_exec, scale_cpu);
    }

    dl_se->runtime -= scaled_delta_exec;  /* 消耗预算 */

    /* 预算耗尽：限流！*/
    if (dl_runtime_exceeded(dl_se) || dl_se->dl_yielded) {
        dl_se->dl_throttled = 1;
        __dequeue_task_dl(rq, curr, 0);   /* 从红黑树摘除 */
        if (!start_dl_timer(curr))        /* 启动补充定时器 */
            enqueue_task_dl(rq, curr, ENQUEUE_REPLENISH); /* 定时器已过期，立即补充 */

        if (!is_leftmost(curr, &rq->dl))
            resched_curr(rq);             /* 触发重新调度 */
    }
}
```

---

### 5.6 SMP 负载均衡：Push/Pull + cpudl

#### 5.6.1 与 RT 的关键区别

| | RT 调度器 | DL 调度器 |
|---|---|---|
| **pushable 数据结构** | `plist`（按优先级数值） | **红黑树**（按 deadline） |
| **全局索引** | `cpupri`（CPU 优先级表） | **`cpudl`**（CPU deadline 表）|
| **Push 目标** | 优先级最低的 CPU | **deadline 最晚**的 CPU |
| **Pull 触发** | 当前任务优先级降低 | 当前 DL 任务结束/阻塞 |

#### 5.6.2 `select_task_rq_dl` — 唤醒时选择 CPU

```c
static int select_task_rq_dl(struct task_struct *p, int cpu, int flags)
{
    curr = READ_ONCE(rq->curr);

    /* 需要换 CPU 的条件：
       当前 CPU 上有 DL 任务，且：
       - 当前任务不可迁移，或
       - 当前任务的 deadline 比 p 更早（p 优先级更低，应该去别处）*/
    select_rq = unlikely(dl_task(curr)) &&
                (curr->nr_cpus_allowed < 2 ||
                 !dl_entity_preempt(&p->dl, &curr->dl)) &&
                p->nr_cpus_allowed > 1;

    if (select_rq) {
        /* 找 deadline 最晚（优先级最低）的 CPU */
        int target = find_later_rq(p);

        /* 目标 CPU 的最早 deadline 比 p 的 deadline 更晚，值得去 */
        if (target != -1 &&
            (dl_time_before(p->dl.deadline,
                            cpu_rq(target)->dl.earliest_dl.curr) ||
             cpu_rq(target)->dl.dl_nr_running == 0))
            cpu = target;
    }
    return cpu;
}
```

**DL 的选 CPU 逻辑与 RT 相反**：RT 找"优先级最低"的 CPU（`find_lowest_rq`），DL 找"deadline 最晚"的 CPU（`find_later_rq`）——因为 deadline 越晚，优先级越低，越适合接收新的高优先级任务。

#### 5.6.3 `check_preempt_curr_dl` — 抢占决策

```c
static void check_preempt_curr_dl(struct rq *rq, struct task_struct *p, int flags)
{
    /* dl_entity_preempt：p 的 deadline 比当前任务更早？ */
    if (dl_entity_preempt(&p->dl, &rq->curr->dl)) {
        resched_curr(rq);  /* 抢占！*/
        return;
    }

    /* deadline 相同时的 SMP 处理（类似 RT 的 check_preempt_equal_prio）*/
    if (p->dl.deadline == rq->curr->dl.deadline && ...)
        check_preempt_equal_dl(rq, p);
}
```

---

### 5.7 准入控制（Admission Control）

这是 DL 调度类独有的机制，RT 和 CFS 都没有。

```c
/* 设置 SCHED_DEADLINE 时的检查 */
int dl_overflow(struct task_struct *p, int policy, const struct sched_attr *attr)
{
    /* 新带宽 = dl_runtime / dl_period */
    new_bw = to_ratio(attr->sched_period, attr->sched_runtime);

    /* 检查：系统总带宽 + 新任务带宽 <= CPU 总容量？ */
    /* 即：∑(U_i) + U_new <= 1.0（单 CPU）或 N（N 个 CPU）*/
    if (dl_policy(policy) && !task_has_dl_policy(p)) {
        /* 新增 DL 任务 */
        err = __dl_overflow(dl_b, cap, 0, new_bw);
    } else if (!dl_policy(policy) && task_has_dl_policy(p)) {
        /* 移除 DL 任务 */
        __dl_add(dl_b, p->dl.dl_bw, cpus);
    } else if (dl_policy(policy) && task_has_dl_policy(p)) {
        /* 修改 DL 任务参数 */
        err = __dl_overflow(dl_b, cap, p->dl.dl_bw, new_bw);
    }
}
```

**准入控制的意义**：如果允许所有 DL 任务的带宽之和超过 100%，就无法保证所有任务都能在 deadline 内完成。准入控制在 `sched_setattr()` 时就拒绝不可调度的配置，而不是等到运行时才发现问题。

---

### 5.8 GRUB 带宽回收（可选特性）

GRUB（Greedy Reclamation of Unused Bandwidth）是一个高级特性：当某些 DL 任务没有用完它们的带宽时，其他任务可以"借用"这些空闲带宽。

```c
static u64 grub_reclaim(u64 delta, struct rq *rq, struct sched_dl_entity *dl_se)
{
    u64 u_inact = rq->dl.this_bw - rq->dl.running_bw; /* 非活跃带宽 */

    /* 实际消耗 = delta * max(u/Umax, 1 - u_inact - u_extra) */
    /* 当有大量空闲带宽时，任务消耗预算的速度变慢，相当于"免费"获得更多运行时间 */
    u_act = BW_UNIT - u_inact - rq->dl.extra_bw;
    return (delta * u_act) >> BW_SHIFT;
}
```

---

### 5.9 整体架构总结

```
┌──────────────────────────────────────────────────────────────┐
│                  SCHED_DEADLINE 全景                          │
├──────────────────────────────────────────────────────────────┤
│  理论基础                                                     │
│  EDF：最早 deadline 优先，红黑树 O(log n) 插入，O(1) 选择     │
│  CBS：预算耗尽时推迟 deadline，隔离超时任务，防止级联失效      │
├──────────────────────────────────────────────────────────────┤
│  准入控制（独有）                                             │
│  sched_setattr() 时检查 ∑U_i <= N，拒绝不可调度配置          │
├──────────────────────────────────────────────────────────────┤
│  带宽管理（三态 + 两定时器）                                  │
│  dl_timer：周期到期时补充预算，推迟 deadline                  │
│  inactive_timer：任务阻塞时，0-lag 到期后释放 running_bw     │
│  三态：ACTIVE contending → non-contending → INACTIVE         │
├──────────────────────────────────────────────────────────────┤
│  SMP 均衡                                                     │
│  cpudl 表：全局 CPU deadline 索引（类比 RT 的 cpupri）        │
│  pushable 红黑树：按 deadline 排序（类比 RT 的 plist）        │
│  Push：找 deadline 最晚的 CPU（RT 找优先级最低）              │
│  GRUB：可选的带宽回收机制                                     │
└──────────────────────────────────────────────────────────────┘
```

---

DL 调度类的核心难点在于 **CBS 的三个规则**（新实例、唤醒、补充）和**三态带宽模型**（0-lag 时间）。理解了这两块，整个 DL 调度器的逻辑就清晰了。

接下来你是想深入 **CFS（完全公平调度器）**，还是想先聊聊 **DL 的实际使用场景和调优**（比如音视频实时处理、工业控制系统如何配置这三个参数）？