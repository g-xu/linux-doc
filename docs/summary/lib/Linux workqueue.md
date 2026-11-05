## workqueue

Linux 内核的 **Workqueue（工作队列）** 是内核中处理**延后任务（Deferred Work）**最常用、最灵活的机制。它与 Tasklet 和 Softirq 共同构成了 Linux 的“下半部（Bottom Half）”处理体系，但它有一个本质的区别：

**Workqueue 运行在进程上下文（Process Context），允许睡眠（Sleep）和阻塞。**

这意味着在 Workqueue 的处理函数中，你可以使用 `mutex`、执行磁盘 I/O、分配大量内存（`GFP_KERNEL`）或调用 `msleep`，而这些在软中断或 Tasklet 中都是绝对禁止的。

以下是对 Workqueue 模块的详细介绍。

---

### 1. 核心概念

#### 1.1 工作（Work）
最基本的单位，由 `struct work_struct` 结构体表示。它记录了“要执行什么函数”以及“上下文数据”。
- 当你想执行某个任务时，就是把这个结构体提交给内核。
- 如果需要延时执行（例如 5秒后），则使用 `struct delayed_work`。

#### 1.2 工作队列（Workqueue）
存放“工作”的链表容器，由 `struct workqueue_struct` 表示。
- 就像一个生产线，你把任务（Work）丢上去。
- **System WQ（系统默认队列）**：内核提供了一个全局通用的队列（`system_wq`），90% 的驱动开发只需要把任务丢给它即可，无需自己创建队列。
- **Custom WQ（自定义队列）**：如果你的任务非常繁重，怕堵塞系统队列，或者需要特殊的调度策略（如高优先级），可以创建自己的队列。

#### 1.3 工作者线程（Worker Thread）
真正干活的内核线程（Kernel Thread）。
- 它们通常以 `kworker/uX:Y` 的形式出现在 `top` 命令中。
- 它们从队列中取出 Work，并执行其中定义的回调函数。

---

### 2. 工作原理与 CMWQ (Concurrency Managed Workqueue)

在 Linux 2.6.36 之前，旧的 Workqueue 机制在大规模并发下存在效率问题（要么创建太多线程，要么并发度不够）。现在的内核使用的是 **CMWQ（并发管理工作队列）**。

#### 2.1 CMWQ 架构
CMWQ 的核心设计哲学是：**将“工作队列的创建”与“底层线程池的管理”解耦。**

1.  **统一的线程池**：
    内核不再为每个 Workqueue 创建一组线程，而是维护一个**每 CPU 全局线程池（Per-CPU Worker Pool）**。
    
2.  **动态并发控制**：
    *   如果不阻塞：当一个 Work 被调度到某个 CPU 上时，内核复用现有的 Worker 线程去执行它。
    *   如果阻塞了：如果正在执行 Work 的 Worker 线程进入睡眠（比如等待磁盘 I/O），CMWQ 会感知到，并立即唤醒或创建一个新的 Worker 线程来接管该 CPU，以运行队列中剩余的任务。
    *   **目的**：尽力保持 CPU 不空闲，同时避免创建过多的线程导致调度开销过大。

#### 2.2 Bound vs Unbound
在申请自定义 Workqueue 时，有两种主要模式：
*   **Bound (默认)**：任务倾向于在提交它的那个 CPU 上运行（利用 CPU 缓存热度）。
*   **Unbound (`WQ_UNBOUND`)**：任务不绑定 CPU，可以被调度器移动到任何空闲的 CPU 上执行。适合计算密集型或长时间睡眠的任务。

---

### 3. 数据结构与 API 概览

#### 3.1 核心结构体
```c
struct work_struct {
    atomic_long_t data;
    struct list_head entry;
    work_func_t func; /* 回调函数 */
};
```

#### 3.2 常用 API

| 操作 | 函数 | 说明 |
| :--- | :--- | :--- |
| **声明** | `DECLARE_WORK(name, func)` | 静态定义并初始化 |
| **初始化** | `INIT_WORK(&work, func)` | 动态初始化（常用于结构体内部成员） |
| **调度 (系统队列)** | `schedule_work(&work)` | 提交给系统默认队列，尽快执行 |
| **延时调度** | `schedule_delayed_work(&dwork, jiffies)` | 延时一段时间后再执行 |
| **调度 (自定义)** | `queue_work(wq, &work)` | 提交给自定义的 `wq` |
| **取消** | `cancel_work_sync(&work)` | 取消任务并等待正在运行的任务结束（**必用**） |
| **创建队列** | `alloc_workqueue(fmt, flags, max_active)` | 创建自定义队列 |
| **销毁队列** | `destroy_workqueue(wq)` | 销毁自定义队列 |

---

### 4. 适用场景

请根据以下特征判断是否应该使用 Workqueue：

1.  **需要睡眠**：任务中包含 `msleep`、`mutex_lock`、`copy_from_user`、文件读写等操作。
2.  **耗时较长**：任务逻辑复杂，执行时间超过微秒级，如果在中断里做会影响系统响应。
3.  **内存分配**：需要分配较大的内存，且必须使用 `GFP_KERNEL`（因为 `GFP_ATOMIC` 容易失败且资源宝贵）。
4.  **对实时性要求不高**：不需要像硬件中断那样立刻马上执行，毫秒级的延迟是可以接受的。

**对比总结：**
*   **Hard IRQ**: 极短，硬件操作，严禁睡眠。
*   **Tasklet/Softirq**: 短，原子上下文，严禁睡眠，优先级高。
*   **Workqueue**: 长，进程上下文，**可以睡眠**，由调度器管理。

---

### 5. 使用时的注意事项（避坑指南）

#### 5.1 重入性与并发
*   **同一任务的串行化**：如果一个 `work_struct` 已经被提交且正在运行，再次调用 `schedule_work` 提交它通常是无效的（除非它已经跑完了）。这意味着同一个 `work` 实例在默认情况下不会在多个 CPU 上并发运行自己。
*   **不同任务的并发**：同一个 Workqueue 中的不同 `work` 可以在不同 CPU 上并发运行。如果它们访问共享数据，**必须加锁**。

#### 5.2 上下文参数传递
初学者常问：“回调函数 `void func(struct work_struct *work)` 只有一个参数，我怎么把我的设备指针传进去？”
**标准做法**：使用 `container_of` 宏。
```c
struct my_device {
    int data;
    struct work_struct my_work; // 嵌入到你的结构体中
};

void my_work_handler(struct work_struct *work) {
    // 通过 work 指针反推 struct my_device 的地址
    struct my_device *dev = container_of(work, struct my_device, my_work);
    printk("Data is %d\n", dev->data);
}
```

#### 5.3 模块卸载时的清理
这是最容易导致 **Kernel Panic** 的地方。
在模块的 `__exit` 函数中，必须调用 `cancel_work_sync(&work)`。
*   **原因**：如果模块卸载了，但 Workqueue 里还有挂起的任务，或者任务正在 CPU 上跑，等它跑的时候，代码段（.text）已经被内核卸载了，CPU 执行空地址会导致系统崩溃。
*   **注意**：使用 `_sync` 版本，确保等待正在运行的任务彻底结束。

#### 5.4 不要阻塞 system_wq 太久
虽然 Workqueue 允许睡眠，但如果你长时间占用系统默认队列（`system_wq`）且不释放，可能会影响其他驱动的正常工作。
*   如果你的任务需要长时间（比如几秒钟）死循环或阻塞等待，建议创建自己的 **Unbound Workqueue** (`WQ_UNBOUND`)。

### 6. 实战代码片段

```c
/* 1. 定义结构体 */
struct my_data {
    struct work_struct work;
    int value;
};
struct my_data *ctx;

/* 2. 工作处理函数 */
void my_worker_func(struct work_struct *work) {
    // 获取上下文
    struct my_data *my_data = container_of(work, struct my_data, work);
    
    // 可以睡眠
    msleep(100); 
    pr_info("Work executed! Value: %d\n", my_data->value);
}

/* 3. 初始化 (Module Init) */
ctx = kmalloc(sizeof(*ctx), GFP_KERNEL);
ctx->value = 100;
INIT_WORK(&ctx->work, my_worker_func);

/* 4. 提交任务 */
schedule_work(&ctx->work);

/* 5. 清理 (Module Exit) */
cancel_work_sync(&ctx->work);
kfree(ctx);
```

## 使用对比

以下是 **Workqueue**、**Tasklet**、**Softirq** 和 **Threaded IRQ** 的详细特性对比表。

这四种机制主要分为两大类：
1.  **原子上下文（不能睡眠）**：Softirq, Tasklet
2.  **进程上下文（可以睡眠）**：Workqueue, Threaded IRQ

### Linux 内核下半部机制对比表

| 特性 | Softirq (软中断) | Tasklet (小任务) | Workqueue (工作队列) | Threaded IRQ (中断线程化) |
| :--- | :--- | :--- | :--- | :--- |
| **运行上下文** | **中断上下文** (Atomic) | **中断上下文** (Atomic) | **进程上下文** (Process) | **进程上下文** (Process) |
| **能否睡眠/阻塞** | **绝对禁止** | **绝对禁止** | **可以** (Mutex/Sleep/I/O) | **可以** (Mutex/Sleep/I/O) |
| **并发性 (同类型)** | **极高**<br>同一类型的 Softirq 可在不同 CPU 上同时运行。需极高的重入安全性。 | **中等**<br>同一**个** Tasklet 实例不能并发运行；但**不同**类型的 Tasklet 可并行。 | **高**<br>默认不同 CPU 可并发运行；可配置为单线程或未绑定(Unbound)。 | **串行**<br>同一中断源的处理函数是串行的（除非再次触发）。 |
| **调度优先级** | **极高** (仅次于硬件中断) | **极高** (基于 Softirq 实现) | **低** (由 CFS 调度器管理，普通进程优先级) | **可配置** (通常默认为实时优先级 `SCHED_FIFO`) |
| **响应延迟** | 极低 | 低 | 中/高 (取决于系统负载) | 低/中 (取决于优先级配置) |
| **内存分配限制** | 只能用 `GFP_ATOMIC` | 只能用 `GFP_ATOMIC` | 可用 `GFP_KERNEL` (内存不足可等待) | 可用 `GFP_KERNEL` |
| **锁的使用** | 只能用自旋锁 (`spinlock`) | 只能用自旋锁 (`spinlock`) | 可用互斥锁 (`mutex`) 或自旋锁 | 可用互斥锁 (`mutex`) 或自旋锁 |
| **定义方式** | **静态编译时定义**<br>(数量有限，约10个) | 动态或静态定义 | 动态或静态定义 | 通过 `request_threaded_irq` 注册 |
| **主要应用场景** | **核心子系统**<br>(网络收发、块设备、高精度定时器) | **(已不推荐)**<br>旧式驱动程序中不需要并发执行的原子任务。 | **通用延后任务**<br>绝大多数需要延后执行且不紧急的驱动任务。 | **现代驱动中断处理**<br>替代 Tasklet，用于处理硬件中断的耗时逻辑。 |
| **现代内核地位** | **核心基石** (地位稳固，但不建议驱动直接使用) | **逐步废弃** (Deprecated)<br>建议用 Threaded IRQ 或 Workqueue 替代。 | **推荐标准**<br>最通用的机制。 | **推荐标准**<br>RT (实时) 内核补丁的首选机制。 |

---

### 选型决策总结

1.  **我应该用 Softirq 吗？**
    *   **绝大多数情况下：不。**
    *   除非你在编写 Linux 内核的核心子系统（如 TCP/IP 协议栈、SCSI 子系统），否则驱动开发者不应直接使用 Softirq。它的并发处理非常复杂，极易导致死锁。

2.  **我应该用 Tasklet 吗？**
    *   **尽量别用。**
    *   虽然它很简单，但它在现代内核中处于“不推荐”状态。它的功能完全可以被 Workqueue（如果不太急）或 Threaded IRQ（如果很急）替代。

3.  **我应该用 Workqueue 吗？**
    *   **是，默认选它。**
    *   只要你的任务不是微妙级的紧急，或者你的任务需要分配大量内存、写文件、等待信号量，就必须用 Workqueue。它是最稳健的选择。

4.  **我应该用 Threaded IRQ 吗？**
    *   **是，写设备驱动时推荐。**
    *   当你调用 `request_irq` 时，现在推荐直接使用 `request_threaded_irq`。它允许你在中断触发后直接唤醒一个专用线程来处理数据，这样你可以用简单的 `mutex` 来保护数据，而不需要复杂的 `spinlock` 技巧，同时也对实时系统（Real-Time Linux）更友好。

## APIs

以下是 Linux 内核 Workqueue 模块主要 API 的详细汇总。为了方便查阅，分为 **定义与初始化**、**调度（提交任务）**、**取消与清理**、**队列管理** 四个类别。

所有的 API 头文件均为：`&lt;linux/workqueue.h&gt;`

### 1. 工作项（Work Item）的定义与初始化

这些宏和函数用于准备 `work_struct` 或 `delayed_work` 结构体。

| API / 宏 | 类型 | 描述 | 适用场景 |
| :--- | :--- | :--- | :--- |
| **`DECLARE_WORK(name, func)`** | 静态宏 | 定义并初始化一个名为 `name` 的 `work_struct`，绑定回调函数 `func`。 | 全局变量或文件作用域变量。 |
| **`INIT_WORK(work, func)`** | 动态初始化 | 初始化一个已经分配内存的 `work_struct` 指针，绑定回调函数 `func`。 | 在结构体内部成员初始化时（如在 `probe` 函数中）。 |
| **`DECLARE_DELAYED_WORK(name, func)`** | 静态宏 | 定义并初始化一个名为 `name` 的 `delayed_work`（带定时器）。 | 全局变量或文件作用域的延时任务。 |
| **`INIT_DELAYED_WORK(dwork, func)`** | 动态初始化 | 初始化一个 `delayed_work` 结构体指针。 | 结构体内部的延时任务成员初始化。 |

---

### 2. 调度任务（提交到队列）

这些函数用于将任务放入队列等待执行。

#### A. 提交到系统默认队列 (`system_wq`)
*大多数驱动程序推荐使用此方式，无需自己创建队列。*

| API 函数 | 返回值 | 描述 | 注意事项 |
| :--- | :--- | :--- | :--- |
| **`schedule_work(work)`** | `bool` | 将任务提交到系统默认队列。如果任务已在队列中等待，则直接返回 `false`（不会重复排队）。 | 立即请求调度。 |
| **`schedule_delayed_work(dwork, delay)`** | `bool` | 将任务提交到系统默认队列，但延迟 `delay` 个 jiffies 后才开始排队。 | `delay` 参数通常配合 `msecs_to_jiffies(ms)` 使用。 |

#### B. 提交到自定义队列
*适用于需要隔离、高优先级或特定并发控制的场景。*

| API 函数 | 返回值 | 描述 | 注意事项 |
| :--- | :--- | :--- | :--- |
| **`queue_work(wq, work)`** | `bool` | 将任务提交到指定的工作队列 `wq`。 | 需要先通过 `alloc_workqueue` 创建 `wq`。 |
| **`queue_delayed_work(wq, dwork, delay)`** | `bool` | 延时提交任务到指定的 `wq`。 | 同上。 |

---

### 3. 取消与同步（清理）

**这是最关键的部分**，用于模块卸载或停止设备时，防止系统崩溃。

| API 函数 | 阻塞性 | 描述 | 关键用法 |
| :--- | :--- | :--- | :--- |
| **`cancel_work_sync(work)`** | **是** (阻塞) | 取消任务。如果任务正在运行，会**等待**其执行完毕。 | **模块卸载 (`__exit`) 时必须调用**，确保内存释放前没有代码在跑。 |
| **`cancel_delayed_work(dwork)`** | 否 (非阻塞) | 尝试取消延时任务的定时器。 | 只能取消还在倒计时的任务；如果任务已经开始运行，它无能为力。 |
| **`cancel_delayed_work_sync(dwork)`** | **是** (阻塞) | 取消延时任务。如果定时器在跑则取消；如果任务正在执行则等待执行完毕。 | **模块卸载时处理延时任务的推荐 API**。 |
| **`flush_workqueue(wq)`** | **是** (阻塞) | 等待指定队列中**所有**已提交的任务执行完毕。 | 用于清空整个自定义队列，确保所有积压工作完成。 |
| **`flush_work(work)`** | **是** (阻塞) | 等待某个特定的任务执行完毕（如果它在队列中）。 | 仅同步状态，不取消任务。 |

---

### 4. 队列管理（创建与销毁）

用于管理自定义的工作队列（`workqueue_struct`）。

| API 函数 | 描述 | 参数说明 |
| :--- | :--- | :--- |
| **`alloc_workqueue(fmt, flags, max_active)`** | 创建一个新的工作队列。取代了旧的 `create_workqueue`。 | `fmt`: 队列名称格式 (printf风格)。<br>`flags`: 见下文详解。<br>`max_active`: 并发度（通常填 `0` 让内核自动决定）。 |
| **`destroy_workqueue(wq)`** | 销毁工作队列。 | **必须确保队列是空的**。通常先调 `cancel_...` 停止所有任务，再调此函数。 |

#### 关于 `alloc_workqueue` 的 Flags 详解
| Flag | 含义 | 场景 |
| :--- | :--- | :--- |
| **`WQ_UNBOUND`** | **解绑 CPU**。Worker 线程不绑定在特定 CPU 上，可被调度到任意空闲 CPU。 | 适合**CPU 密集型**或**长时间睡眠**的任务，避免阻塞特定 CPU 的其他任务。 |
| **`WQ_HIGHPRI`** | **高优先级**。Worker 线程以高优先级（nice 值更低）运行。 | 仅用于对延迟非常敏感的任务。 |
| **`WQ_MEM_RECLAIM`** | **内存回收保障**。预留紧急救援线程 (Rescuer)。 | 仅用于涉及内存回收路径（如块设备、文件系统释放内存）的任务，防止 OOM 死锁。 |
| **`0`** | 默认值（绑定 CPU）。 | 适合快速、轻量级的任务，利用 CPU 缓存亲和性。 |

---

### 5. 辅助宏与函数

| API / 宏 | 描述 |
| :--- | :--- |
| **`work_pending(work)`** | 检查任务是否处于“挂起”状态（即已提交但尚未开始执行）。 |
| **`delayed_work_pending(dwork)`** | 检查延时任务是否处于挂起状态。 |
| **`to_delayed_work(work_ptr)`** | 将 `struct work_struct *` 转换为 `struct delayed_work *`。常用于回调函数中。 |
| **`container_of(ptr, type, member)`** | **通用核心宏**。在 Workqueue 回调中，用于从 `work` 指针反推回自定义设备结构体的指针。 |

### 6. 速查示例：标准生命周期

```c
/* 1. 定义 */
struct my_dev {
    struct work_struct work;
};

void my_func(struct work_struct *work) {
    // 处理业务...
}

/* 2. 初始化 */
INIT_WORK(&my_dev->work, my_func);

/* 3. 调度 */
schedule_work(&my_dev->work);

/* 4. 卸载/退出 (至关重要) */
cancel_work_sync(&my_dev->work);
// 只有在这里之后，释放 my_dev 才是安全的
kfree(my_dev);
```