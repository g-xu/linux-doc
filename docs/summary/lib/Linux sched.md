## sched

Linux 内核调度器（Scheduler）是内核的“心脏”，负责决定**哪个进程（Process）或线程（Thread）在什么时候获得 CPU 的使用权，以及使用多久**。

在现代 Linux（2.6.23 之后），默认使用的是 **CFS（Completely Fair Scheduler，完全公平调度器）**。它的设计目标是在**交互响应速度**（低延迟）和**系统吞吐量**（高效率）之间找到平衡。

以下是从架构设计到实际调优的详细介绍。

---

### 1. 核心概念

在深入原理之前，必须理解调度器处理的基本对象和属性。

#### 1.1 调度对象：Task (`task_struct`)
在 Linux 内核中，进程和线程在调度层面上没有本质区别，都由 `struct task_struct` 表示。我们统称为 **Task**。

#### 1.2 优先级 (Priority)
Linux 采用两套优先级范围（0-139）：
*   **实时优先级 (Real-time, 0-99)**：数字越大，优先级越高。对应 `SCHED_FIFO` 和 `SCHED_RR`。
*   **普通优先级 (Normal, 100-139)**：对应用户空间的 **Nice 值** (-20 到 +19)。
    *   Nice -20 对应优先级 100（最高普通优先级）。
    *   Nice +19 对应优先级 139（最低）。
    *   **默认 Nice 0 对应 120。**

各种视角下的优先级可以参考下图：
![](../../assets/linux%E4%BC%98%E5%85%88%E7%BA%A7.drawio.png)
#### 1.3 抢占 (Preemption)
Linux 是抢占式内核。
*   **用户抢占**：当系统调用返回用户空间或中断处理返回用户空间时，如果当前进程时间片用完或有更高优先级进程唤醒，会发生抢占。
*   **内核抢占**：即使进程在内核态执行（如执行系统调用），只要没有持有自旋锁或关闭中断，也可以被高优先级进程抢占。

---

### 2. 调度器架构：调度类 (Scheduling Classes)

Linux 内核并没有用一个单一的算法管理所有任务，而是引入了**调度类**框架。调度器会按照优先级顺序，依次询问每个调度类：“你有没有要运行的任务？”

**优先级从高到低排列如下：**

1.  **Stop Class** (`stop_sched_class`)：
    *   **优先级最高**。用于内核自身的紧急任务，如 CPU 热插拔、迁移任务。用户不可用。
2.  **Deadline Class** (`dl_sched_class`)：
    *   策略：`SCHED_DEADLINE`。
    *   基于最后期限（Deadline）的调度，用于对时间要求极严苛的场景（如视频编码）。
3.  **Real-Time Class** (`rt_sched_class`)：
    *   策略：`SCHED_FIFO` (先进先出), `SCHED_RR` (时间片轮转)。
    *   **特点**：只要有 RT 任务就绪，普通任务永远得不到 CPU，直到 RT 任务结束或主动让出。
4.  **Fair Class** (`fair_sched_class`) —— **核心主角**：
    *   策略：`SCHED_NORMAL` (也叫 `SCHED_OTHER`), `SCHED_BATCH`。
    *   **实现**：**CFS (完全公平调度器)**。
    *   这是 99% 的用户进程（Shell, Web Server, App）所在的类。
5.  **Idle Class** (`idle_sched_class`)：
    *   策略：`SCHED_IDLE`。
    *   当 CPU 无事可做时运行的 0号进程（swapper），负责让 CPU 进入省电模式。

---

### 3. 核心原理：CFS (完全公平调度器)

CFS 是 Linux 调度器的精髓，取代了老旧的 O(1) 调度器。

#### 3.1 设计哲学
CFS 不再使用传统的“时间片（Time Slice）”概念，而是基于 **虚拟运行时 (Virtual Runtime, `vruntime`)**。
*   **理想模型**：如果你有 N 个任务，CFS 试图模拟一个“理想的多任务 CPU”，让每个任务在物理时间周期内获得 `1/N` 的 CPU 处理能力。
*   **现实实现**：由于 CPU 只能一个一个跑，CFS 记录每个任务已经运行了多久（`vruntime`），每次都挑选 **`vruntime` 最小** 的任务来运行。

#### 3.2 虚拟运行时 (`vruntime`)
`vruntime` 并不等于物理时间，而是经过**权重（Weight）**修正的时间。
$$ vruntime += 物理运行时间 \times \frac{NICE\_0\_WEIGHT}{任务权重} $$

*   **高优先级任务**（Nice < 0）：权重高，分母大，`vruntime` 增加得慢。这意味着它能运行更久的物理时间，`vruntime` 才能追上别人。
*   **低优先级任务**（Nice > 0）：权重低，分母小，`vruntime` 增加得快。跑一点点时间就被踢下去了。

#### 3.3 数据结构：红黑树 (Red-Black Tree)
CFS 不使用链表或数组管理运行队列（Runqueue），而是使用**红黑树**（`rbtree`）。
*   **节点 Key**：任务的 `vruntime`。
*   **左侧**：`vruntime` 小的任务（饥饿，需要 CPU）。
*   **右侧**：`vruntime` 大的任务（刚跑完，或者不急）。
*   **调度逻辑**：调度器总是直接取 **树的最左下角节点 (leftmost)** 运行。时间复杂度为 O(1)（因为内核会缓存最左节点），插入/删除为 O(log N)。

---

### 4. 调度工作流程

#### 4.1 周期性调度 (The Tick)
系统时钟中断（Tick，通常 1ms - 10ms 一次）触发 `scheduler_tick()`：
1.  更新当前进程的 `vruntime`。
2.  检查是否超出了它的理想运行时间。
3.  如果超出了，或者红黑树里有 `vruntime` 更小的任务，设置 `TIF_NEED_RESCHED` 标志，请求调度。

#### 4.2 主调度器 (`schedule()`)
当内核准备切换任务时（主动调用 `schedule()` 或从中断/系统调用返回时）：
1.  **关闭抢占**。
2.  调用 `pick_next_task()`：按照调度类顺序（Stop -> DL -> RT -> Fair -> Idle）查找任务。
3.  在 Fair Class 中，取出红黑树最左节点。
4.  **上下文切换 (`context_switch`)**：
    *   切换内存空间 (`switch_mm`)。
    *   切换寄存器和堆栈 (`switch_to`)。

#### 4.3 负载均衡 (Load Balancing)
在多核（SMP）系统中，每个 CPU 都有自己的运行队列（Runqueue）。
*   **问题**：可能出现 CPU0 很忙，CPU1 很闲的情况。
*   **机制**：内核会周期性地检查各 CPU 的负载。如果发现不平衡，会执行 **Pull**（空闲 CPU 拉任务）或 **Push**（忙碌 CPU 推任务）操作，将任务在 CPU 之间迁移。

---

### 5. 适用场景与策略选择

根据应用类型选择合适的调度策略：

| 场景 | 推荐策略 | 特点 |
| :--- | :--- | :--- |
| **通用服务器/桌面** | `SCHED_NORMAL` (默认) | **CFS**。兼顾响应和吞吐，适合 Web Server、数据库、UI 交互。 |
| **后台批处理** | `SCHED_BATCH` | 类似 Normal，但惩罚频繁唤醒，减少上下文切换，适合编译、视频渲染。 |
| **工业控制/音频处理** | `SCHED_FIFO` / `SCHED_RR` | **实时**。必须确保在微秒级响应。音视频防卡顿、机械臂控制。 |
| **极低优先级** | `SCHED_IDLE` | 仅在系统全空闲时运行，如 SETI@home 计算。 |

---

### 6. 常见 API 与工具

#### 查看与监控
*   `top` / `htop`: 查看 `PR` (优先级) 和 `NI` (Nice值)。
*   `vmstat`: 查看 `r` (运行队列长度) 和 `cs` (上下文切换次数/秒)。
*   `/proc/sched_debug`: 查看内核调度器详细内部状态。

#### 控制与调整
*   **Nice 值**：`nice -n -5 ./cmd` (启动时设置), `renice` (运行时调整)。
*   **实时优先级**：`chrt` 命令。
    *   `chrt -f 99 ./cmd` (以 FIFO 策略、优先级 99 运行)。
*   **CPU 亲和性 (Affinity)**：`taskset`。
    *   `taskset -c 0,1 ./cmd` (强制在 CPU 0 和 1 上运行，减少缓存失效)。

---

### 7. 注意事项与常见坑

1.  **不要滥用实时优先级 (`SCHED_FIFO`)**：
    *   如果你写了一个死循环程序并设为 `SCHED_FIFO`，且优先级高于 shell，你的系统会直接**死机**（Freeze）。因为 CPU 永远不会切换回 Shell 让你输入命令。
    *   *对策*：内核默认保留了 5% 的 CPU 时间给非 RT 任务（`kernel.sched_rt_runtime_us`）以防死锁，但仍需谨慎。

2.  **频繁的上下文切换 (Context Switch)**：
    *   如果在 `vmstat` 看到 `cs` 极高（例如每秒数十万次），通常意味着程序中有大量的锁竞争、线程频繁唤醒/睡眠。这会严重消耗 CPU 在内核态的时间，降低业务吞吐量。

3.  **NUMA 架构影响**：
    *   在大型服务器上，跨 NUMA 节点迁移任务会导致内存访问变慢。内核调度器会自动尝试让任务留在同一个 NUMA 节点，但有时手动用 `numactl` 绑定效果更好。

4.  **Nice 值的误区**：
    *   Nice 值只影响 CFS 中的**权重**（CPU 时间分配比例），**不影响延迟**。如果系统负载极高（Load Average >> CPU核数），即使 Nice -20 的进程也会变慢。

### 总结
Linux 内核调度模块是一个分层的、模块化的系统。
*   底层是 **Context Switch** 和 **Runqueue**。
*   中间层是 **CFS 算法**（红黑树 + vruntime）解决公平性。
*   顶层是 **调度类**（Stop/DL/RT/Fair）解决不同业务需求的优先级隔离。

对于驱动开发者和系统管理员，理解 **"vruntime"** 和 **"抢占时机"** 是排查性能问题的关键。

## APIs

以下详细汇总的 Linux 内核调度器（Scheduler）相关的主要 API。这些接口定义在 `&lt;linux/sched.h&gt;`, `&lt;linux/wait.h&gt;`, `&lt;linux/preempt.h&gt;` 等头文件中。

为了方便查阅，我将 API 分为 **核心调度与切换**、**进程状态与睡眠**、**等待队列机制**、**抢占控制** 以及 **优先级与亲和性** 五大类。

### 1. 核心调度与切换 (Core Scheduling)

这是调度器最底层的操作，决定了 CPU 控制权的移交。

| API 函数接口                        | 功能分类     | 详细功能描述                                                     | 核心注意事项                                                               |
| :------------------------------ | :------- | :--------------------------------------------------------- | :------------------------------------------------------------------- |
| **`schedule()`**                | **主调度器** | 触发调度器运行。它会从就绪队列中选择下一个任务，并进行上下文切换。                          | **不可在中断上下文中调用**。调用前通常需将状态设为非 `TASK_RUNNING`，否则会立即被再次调度回来。            |
| **`cond_resched()`**            | **条件调度** | 检查是否有高优先级任务需要运行（TIF_NEED_RESCHED 标志）。如果有，则主动让出 CPU；否则继续执行。 | **驱动开发神器**。在内核耗时长的循环（如大文件读写、内存初始化）中必须调用，以降低系统延迟（Latency）。            |
| **`yield()`**                   | 让出 CPU   | 放弃当前的 CPU 时间片，将自己移动到运行队列的末尾。                               | **不推荐使用**。在 CFS 调度器下行为不可预测，容易导致性能问题。请优先考虑 `cond_resched()`。          |
| **`schedule_timeout(timeout)`** | 延时调度     | 让当前进程睡眠指定的 jiffies 时间，时间到后自动唤醒。                            | 必须先设置状态（如 `TASK_INTERRUPTIBLE`）。如果是 `TASK_UNINTERRUPTIBLE`，则无法被信号打断。 |
| **`current`**                   | 宏/全局变量   | 获取当前正在 CPU 上运行的进程的 `task_struct` 指针。                       | 实际上通常通过寄存器（如 x86 的 `gs` 段）快速访问，访问频率极高。                               |

---

### 2. 进程状态与睡眠 (State & Sleeping)

在调用 `schedule()` 之前，必须先设置进程状态，否则调度器认为你还在运行，会立刻把你选回来。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`set_current_state(state)`** | 设置状态 | 设置当前进程的状态（如 `TASK_INTERRUPTIBLE`）。包含内存屏障（Memory Barrier）。 | **推荐**。确保在检查睡眠条件前，状态更新对其他 CPU 可见。 |
| **`__set_current_state(state)`**| 设置状态 | 同上，但**不包含**内存屏障。 | 仅在极少数确定无并发竞争的场景下使用以优化性能。 |
| **`wake_up_process(task)`** | 唤醒 | 唤醒指定的进程（将状态改为 `TASK_RUNNING` 并加入运行队列）。 | 如果目标进程已经在运行，则无操作，但可能触发抢占检查。 |
| **`usleep_range(min, max)`** | 睡眠 | 睡眠微秒级时间（高精度定时器）。推荐用于 10us - 20ms 的延时。 | 这是一个封装良好的 API，比忙等待（udelay）省电，比 `msleep` 精度高。 |
| **`msleep(msecs)`** | 睡眠 | 睡眠毫秒级时间。不可中断。 | 底层调用 `schedule_timeout_uninterruptible`。 |
| **`msleep_interruptible(ms)`**| 睡眠 | 睡眠毫秒级时间。可被信号中断。 | 如果被信号打断，返回剩余未睡的毫秒数。 |

---

### 3. 等待队列机制 (Wait Queues)

这是驱动开发中最常用的同步机制。当硬件没准备好时“睡在队列上”，硬件中断来了“唤醒队列”。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`init_waitqueue_head(q)`** | 初始化 | 初始化一个等待队列头 `wait_queue_head_t`。 | 也可以用宏 `DECLARE_WAIT_QUEUE_HEAD(name)` 静态定义。 |
| **`wait_event(wq, condition)`** | **睡眠等待** | 在队列 `wq` 上睡眠，直到 `condition` 为真。**不可中断**（死等）。 | 如果条件不满足，进程进入 `TASK_UNINTERRUPTIBLE` 状态，无法被 kill。 |
| **`wait_event_interruptible(wq, cond)`** | **睡眠等待** | **(最常用)** 同上，但可以被信号（Signal）打断。 | 返回 0 表示条件满足；返回 `-ERESTARTSYS` 表示被信号打断。 |
| **`wait_event_timeout(wq, cond, timeout)`**| 超时等待 | 带超时的等待。 | 返回剩余时间（jiffies），0 表示超时。 |
| **`wake_up(x)`** | **唤醒** | 唤醒等待队列 `x` 上**所有**非互斥的进程。 | 同时唤醒 `INTERRUPTIBLE` 和 `UNINTERRUPTIBLE` 的进程。 |
| **`wake_up_interruptible(x)`** | **唤醒** | 只唤醒等待队列 `x` 上状态为 `TASK_INTERRUPTIBLE` 的进程。 | 配合 `wait_event_interruptible` 使用。 |

---

### 4. 抢占控制 (Preemption Control)

用于保护临界区数据，防止当前进程被其他进程抢占（但通常不屏蔽中断）。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`preempt_disable()`** | 禁止抢占 | 增加抢占计数。在此区间内，即使时间片用完或高优先级任务唤醒，也不会发生**内核抢占**。 | 必须与 `preempt_enable()` 成对出现。代码执行必须快，不能睡眠。 |
| **`preempt_enable()`** | 开启抢占 | 减少抢占计数。如果计数归零且有调度请求，会**立即触发调度**。 | |
| **`preempt_enabled()`** | 状态查询 | 检查当前是否允许抢占。 | 宏定义，通常用于调试断言。 |
| **`preempt_count()`** | 状态查询 | 获取当前的抢占嵌套深度。 | |

---

### 5. 优先级、策略与 CPU 亲和性

用于调整进程的调度策略和运行位置。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`sched_setscheduler(task, policy, param)`** | 设置策略 | 设置指定进程的调度策略（如 `SCHED_FIFO`, `SCHED_NORMAL`）和优先级。 | 需要 root 权限才能设置为实时策略（FIFO/RR）。 |
| **`set_user_nice(task, nice)`** | 设置 Nice | 设置普通进程的 Nice 值（-20 到 19）。 | 影响 CFS 调度器中的权重。 |
| **`set_cpus_allowed_ptr(task, mask)`** | **亲和性** | 强制将进程绑定到指定的 CPU 掩码（cpumask）上运行。 | 常用于性能优化，减少 Cache Miss。会触发任务迁移。 |
| **`yield_to(task, preempt)`** | 让予特定 | 尝试放弃 CPU 并让给指定的 `task` 运行。 | 主要用于自旋锁优化等特殊场景。 |

---

### 📝 驱动开发常用组合 (Best Practices)

1.  **阻塞式 IO 读取：**
    ```c
    // 进程侧
    wait_event_interruptible(my_queue, data_ready == 1);
    
    // 中断/硬件侧
    data_ready = 1;
    wake_up_interruptible(&my_queue);
    ```

2.  **内核长循环防卡顿：**
    ```c
    while (!done) {
        do_heavy_work();
        // 告诉调度器：如果别人急着用 CPU，我先歇会儿
        cond_resched(); 
    }
    ```

3.  **原子上下文保护（不使用锁）：**
    ```c
    preempt_disable();
    // 修改 Per-CPU 变量或关键数据
    // 绝对不能在这里调用 schedule() 或 kmalloc(..., GFP_KERNEL)
    preempt_enable();
    ```