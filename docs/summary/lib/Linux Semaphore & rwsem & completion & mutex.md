
## semaphore

### 1. 什么是 Semaphore？

**Semaphore（信号量）** 本质上是一个**带等待队列的整数计数器**。
它用于管理对资源的并发访问。与互斥锁（Mutex）不同，信号量不仅能实现“互斥”（一次只允许一个），还能实现“计数”（一次允许 N 个）和“同步”（线程 A 通知线程 B）。

**核心特征：**
*   **睡眠锁 (Sleeping Lock)**：如果获取不到信号量，进程会主动放弃 CPU，进入睡眠状态（发生上下文切换）。
*   **适用环境**：仅用于**进程上下文**，禁止在中断上下文或自旋锁临界区中使用。
*   **无所有权 (No Ownership)**：这是它与 Mutex 的最大区别。**信号量没有“谁加锁就必须谁解锁”的限制**。线程 A 可以执行 `down`（加锁），而线程 B 执行 `up`（解锁）。

---

### 2. 主要功能 API

在使用前需包含头文件：`#include &lt;linux/semaphore.h&gt;`

以下是主要 API 的功能列表：

| API 函数 | 功能描述 | 典型返回值/备注 |
| :--- | :--- | :--- |
| **初始化** | | |
| `sema_init(struct semaphore *sem, int val)` | 动态初始化信号量，`val` 为初始计数值。 | `val=1` 类似于互斥锁；`val>1` 为计数信号量。 |
| `DEFINE_SEMAPHORE(name)` | 静态定义并初始化一个名为 `name` 的信号量，初始值为 1。 | 常用于全局变量。 |
| **获取 (P操作/Down)** | | |
| `down(struct semaphore *sem)` | 减少计数。如果计数为0，进入**不可中断睡眠** (`TASK_UNINTERRUPTIBLE`)。 | **不推荐**。如果死锁，进程无法被 kill，只能重启系统。 |
| `down_interruptible(struct semaphore *sem)` | 减少计数。如果计数为0，进入**可中断睡眠**。允许被信号（如 Ctrl+C）唤醒。 | **最常用**。成功返回 0；若被信号打断返回 `-EINTR`。 |
| `down_killable(struct semaphore *sem)` | 类似于 interruptible，但只响应致命信号。 | 成功返回 0；若被信号打断返回 `-EINTR`。 |
| `down_trylock(struct semaphore *sem)` | **非阻塞**尝试。如果计数>0，减1并返回0；否则立即返回1。 | **不会睡眠**，可用于原子上下文（需谨慎）。 |
| `down_timeout(struct semaphore *sem, long jiffies)` | 带超时时间的获取操作。 | 超时返回错误码。 |
| **释放 (V操作/Up)** | | |
| `up(struct semaphore *sem)` | 增加计数。如果等待队列中有进程，唤醒其中一个。 | 任何持有该信号量指针的上下文都可以调用（不一定是加锁者）。 |

---

### 3. 应用场景

在现代 Linux 内核开发中，`semaphore` 的使用场景比以前缩小了很多（因为有了更高效的 `mutex` 和 `completion`），但它在以下场景仍是不可替代的：

#### 3.1 限制并发数量 (Counting Semaphore) - **核心场景**
这是信号量最独特的用途。
*   **场景**：假设你的驱动程序控制一个硬件，该硬件最多支持同时处理 5 个请求。
*   **用法**：初始化 `sema_init(&sem, 5)`。
*   **效果**：前 5 个线程调用 `down` 都能成功进入，第 6 个线程会被阻塞，直到有人 `up`。

#### 3.2 复杂的生产者-消费者同步
利用信号量“无所有权”的特性。
*   **场景**：线程 A 生产数据，线程 B 处理数据。
*   **用法**：初始化 `val = 0`。
    *   线程 B 调用 `down`（因为 val=0，B 睡眠等待）。
    *   线程 A 生产完数据，调用 `up`。
    *   线程 B 被唤醒，处理数据。
*   *注意：对于这种简单的 1对1 同步，内核现在更推荐使用 `struct completion`，但在复杂的 1对N 或 N对M 场景下，信号量依然有用。*

#### 3.3 读写锁的替代 (在极老旧代码中)
*   **场景**：有些代码会初始化为 1 当作互斥锁用。
*   **现状**：**强烈不推荐**。如果只是为了互斥，请使用 `struct mutex`，因为 Mutex 有优先级继承（解决优先级翻转问题）和更好的自旋优化，性能远高于信号量。

---

### 4. 实现原理

Linux 信号量的实现高度优化，尽量避免自旋锁的开销。

#### 4.1 数据结构
```c
struct semaphore {
    raw_spinlock_t      lock;     // 用于保护结构体本身的自旋锁
    unsigned int        count;    // 计数器 (原子性由 lock 保护)
    struct list_head    wait_list;// 等待队列链表
};
```

#### 4.2 Down 操作 (P操作) 逻辑
1.  **快速路径 (Fast Path)**：
    *   调用 `down` 时，内核会尝试原子地检查 `count`。
    *   如果 `count > 0`，直接 `count--`，操作结束，无需加锁，性能很高。
2.  **慢速路径 (Slow Path)**：
    *   如果 `count == 0`，获取 `lock` 自旋锁。
    *   将当前进程（`current`）加入 `wait_list` 尾部。
    *   设置进程状态（如 `TASK_INTERRUPTIBLE`）。
    *   释放 `lock` 自旋锁。
    *   调用调度器 `schedule()`，当前进程睡眠。

#### 4.3 Up 操作 (V操作) 逻辑
1.  **快速路径**：
    *   如果 `wait_list` 为空（即没有人在等待），直接 `count++`。
2.  **慢速路径**：
    *   如果 `wait_list` 不为空，获取 `lock` 自旋锁。
    *   从 `wait_list` 中取出一个等待的进程。
    *   唤醒该进程 (`wake_up_process`)。
    *   **注意**：在 Linux 实现中，`up` 通常不会增加 count 而是直接把锁“交接”给被唤醒的进程，或者被唤醒的进程重新去抢 count。

---

### 5. 信号量 vs 互斥锁 vs 完成量

这是内核编程中最容易混淆的地方，总结如下：

| 特性 | **Semaphore (信号量)** | **Mutex (互斥锁)** | **Completion (完成量)** |
| :--- | :--- | :--- | :--- |
| **计数值** | 任意非负整数 (N) | 只能是 0 或 1 | 任意 |
| **所有权** | **无** (A锁B解) | **有** (谁锁谁解) | 无 |
| **优先级翻转** | **不处理** (可能导致高优先级任务被低优先级饿死) | **支持优先级继承** (Priority Inheritance) | 不涉及 |
| **性能** | 一般 | **高** (有乐观自旋优化) | 高 (针对同步优化) |
| **主要用途** | **控制并发数** (资源计数) | **保护临界区** (互斥) | **线程间通知** (同步) |

### 6. 总结

1.  **Semaphore** 是 Linux 内核处理**多值并发控制**（Count > 1）的首选工具。
2.  它是一种**睡眠锁**，获取失败会发生上下文切换，因此**只能在进程上下文使用**。
3.  如果你的需求仅仅是“一次允许一个线程访问”，请**不要使用 Semaphore**，应该使用 **Mutex**，因为 Mutex 性能更好且更安全。
4.  如果你的需求是“线程 A 叫醒线程 B”，请优先考虑 **Completion**，其次才是 Semaphore。


## rwsem
Linux内核中的 **rwsem** (Read-Write Semaphore，读写信号量) 是一种特殊的锁机制，主要用于**读多写少**的并发场景。

它是 **信号量 (Semaphore)** 的一种变体。与互斥锁（Mutex）不同，它允许多个读者同时进入临界区，但写者必须独占。与自旋锁（Spinlock）不同，rwsem 是一种**睡眠锁**（Sleeping Lock）。

---
### 1. 核心特性

- **读共享，写独占：** 
    - **读模式 (Read Mode)：** 允许多个任务同时持有读锁。
    - **写模式 (Write Mode)：** 同一时间只允许一个任务持有写锁。当写锁被持有时，任何新的读者或写者都必须等待。
- **睡眠锁 (Sleeping Lock)：** 
    - 当一个任务请求锁失败（例如请求写锁但已有读者，或请求读锁但已有写者）时，该任务会**进入睡眠状态**（阻塞），并在等待队列中排队，触发上下文切换。
    - **注意：** 因为会睡眠，所以 **rwsem 不能用于中断上下文 (Interrupt Context)**，只能用于进程上下文。
- **支持写降级 (Write Downgrade)：**     
    - 持有写锁的任务可以将锁“降级”为读锁，而不需要释放锁后再重新获取。这在修改完数据后需要立即读取检查的场景下非常有用，保证了原子性。

---
### 2. 与其他锁的对比

|   |   |   |   |
|---|---|---|---|
|特性|rwsem (读写信号量)|rwlock_t (读写自旋锁)|Mutex (互斥锁)|
|**等待机制**|**睡眠** (Context Switch)|**自旋** (Busy Loop)|睡眠|
|**适用上下文**|仅进程上下文|进程或中断上下文|仅进程上下文|
|**并发度**|多读单写|多读单写|1个持有者|
|**开销**|较高 (涉及调度)|低 (如果不自旋太久)|中等|
|**临界区耗时**|适合**长**时间持有|必须**短**时间持有|适合长时间持有|
|**典型场景**|内存管理 (mmap_lock)|极短的读多写少操作|常规互斥|

**选择建议：**

- 如果你处于中断中，必须用 rwlock_t。
- 如果临界区非常短且不包含睡眠代码，rwlock_t 效率更高。
- 如果临界区较长，或者临界区内可能引起睡眠（如内存分配、IO操作），必须用 rwsem。
- 如果读写频率差不多，或者没有并发读的需求，直接用 Mutex 更简单高效。

---
### 3. 内部实现原理 (简述)

Linux 内核对 rwsem 的实现经过了多次优化（如 Optimistic Spinning）。

1. **计数器 (Count)：**  
    核心是一个 atomic_long_t 类型的计数器。
    - 通常高位记录计数，低位记录状态（是否有等待者）。
    - 具体的位掩码设计非常复杂，用于快速判断能否获取锁。
2. **等待队列 (Wait Queue)：**  
    所有无法获取锁的任务会被加入到一个链表中，状态被设置为 TASK_UNINTERRUPTIBLE。
3. **乐观自旋 (Optimistic Spinning)：**  
    在较新的内核（开启了 CONFIG_RWSEM_SPIN_ON_OWNER）中，如果当前持有锁的拥有者（Owner）正在另一个 CPU 上运行（Running），那么请求锁的任务不会立刻睡眠，而是会**短暂自旋**。
    - **理由：** 如果拥有者很快就会释放锁，自旋的开销远小于上下文切换（睡眠/唤醒）的开销。
    - 一旦拥有者停止运行（比如它也睡了），或者自旋超时，请求者才会真正去睡眠。
4. **公平性与写者饥饿：**  
    为了防止“写者饥饿”（即读者源源不断，写者永远拿不到锁），Linux 的 rwsem 实现通常倾向于在有写者等待时，禁止新的读者进入（除非该读者是递归嵌套，但这在 rwsem 中通常被禁止）。具体的调度策略非常复杂，旨在在吞吐量和公平性之间取得平衡。
---

### 4. 使用注意事项 
1. **禁止递归写锁：** 同一个线程不能多次请求 down_write，否则会死锁。
2. **谨慎递归读锁：** 虽然理论上同一个线程可以多次 down_read，但如果中间夹杂了一个正在等待的写者，可能会导致死锁（因为为了防饥饿，写者会阻塞后续的读者）。**最佳实践是尽量避免递归上锁。**
3. **不可在中断中使用：** 切记，down 操作可能会睡眠。
4. **性能陷阱：** 如果你的读写锁竞争非常激烈，且写操作并未少到数量级差异，rwsem 的原子计数器操作导致的 Cache Line 颠簸（Cache bouncing）可能会很严重，此时可能需要考虑 Per-CPU 的读写信号量 (percpu-rwsem)。

### 5. 经典案例

Linux 内核中最著名的 rwsem 使用者是 **mm_struct 中的 mmap_lock** (在旧版本内核中称为 mmap_sem)。
- 它保护进程的内存地址空间布局。
- 当发生缺页异常（Page Fault）时，内核通常只需读取内存布局（down_read），允许多个线程并发缺页。
- 当调用 mmap 或 brk 修改内存映射时，内核需要获取写锁（down_write），独占访问。

### 总结

rwsem 是 Linux 内核处理**读多写少、临界区较长**场景的标准同步原语。理解它的核心在于记住它是**睡眠锁**，并且通过**写降级**和**乐观自旋**提供了高性能的并发控制。

### APIs

在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/rwsem.h>
```

| 分类 | API 函数原型 | 功能描述 | 备注/返回值 |
| :--- | :--- | :--- | :--- |
| **声明与初始化** | `DECLARE_RWSEM(name)` | **静态**定义并初始化一个名为 `name` 的读写信号量。 | 通常用于全局变量或静态变量。 |
| | `init_rwsem(struct rw_semaphore *sem)` | **动态**初始化一个已经存在的 rwsem 结构体指针。 | 通常用于结构体成员的初始化。 |
| **读锁操作** | `down_read(struct rw_semaphore *sem)` | 获取**读锁**。如果此时有写锁被持有，当前任务会进入**睡眠**（不可中断）直到获得锁。 | 支持多个读者并发持有。 |
| | `down_read_trylock(struct rw_semaphore *sem)` | **尝试**获取读锁（非阻塞）。如果锁不可用，立即返回，不会睡眠。 | **成功返回 1**，失败返回 0。<br>常用于中断上下文或不能睡眠的场景（需极度小心）。 |
| | `down_read_killable(struct rw_semaphore *sem)` | 获取读锁，但允许被致命信号（fatal signal）中断。 | 成功返回 0，如果被信号中断返回 `-EINTR`。防止死锁导致进程无法被 kill。 |
| | `up_read(struct rw_semaphore *sem)` | **释放**读锁。 | 必须与 `down_read` 系列函数配对使用。 |
| **写锁操作** | `down_write(struct rw_semaphore *sem)` | 获取**写锁**。如果锁被（读者或写者）占用，当前任务进入**睡眠**直到获得锁。 | **独占访问**。写锁持有时，禁止任何其他读/写操作。 |
| | `down_write_trylock(struct rw_semaphore *sem)` | **尝试**获取写锁（非阻塞）。 | **成功返回 1**，失败返回 0。 |
| | `down_write_killable(struct rw_semaphore *sem)` | 获取写锁，但允许被致命信号中断。 | 成功返回 0，被信号中断返回 `-EINTR`。 |
| | `up_write(struct rw_semaphore *sem)` | **释放**写锁。 | 必须与 `down_write` 系列函数配对使用。 |
| **特殊操作** | `downgrade_write(struct rw_semaphore *sem)` | **写降级**：将当前持有的**写锁**原子地转换为**读锁**。 | **非常实用的优化**。<br>调用后，任务仍持有锁（变为读锁），**最终需要调用 `up_read()` 来释放**。 |

### 补充说明

1.  **配对规则**：
    *   `down_read` 必须对应 `up_read`。
    *   `down_write` 必须对应 `up_write`。
    *   唯特例：`down_write` -> `downgrade_write` -> `up_read`。

2.  **上下文要求**：
    *   上述 `down_` 系列函数（trylock 除外）都会导致睡眠，因此**严禁在中断上下文 (Interrupt Context) 或 自旋锁 (Spinlock) 临界区内使用**。

3.  **Killable 变体**：
    *   推荐在可能长时间阻塞的场景使用 `_killable` 后缀的函数，这样当系统负载过高或死锁时，用户可以通过 `kill -9` 杀死进程，而不是让进程变成僵尸状态（D 状态）。

## completion

Linux 内核中的 **Completion**（完成量）是一种轻量级的同步机制，专门用于**一个任务等待另一个任务完成某项操作**的场景。

虽然信号量（Semaphore）也能实现类似功能，但在“线程 A 通知 线程 B”这种简单的同步场景下，Completion 更加简单、直观，并且避免了使用信号量时可能出现的一些竞争冒险（Race Conditions）。

以下是关于 Linux 内核 Completion 的详细介绍。

---

### 1. 核心概念与特点

*   **单一目的：** 它的设计初衷就是为了实现 **"同步" (Synchronization)**，即等待事件发生，而不是为了 "互斥" (Mutual Exclusion)。
*   **一次性/可重用：** 默认情况下，Completion 是“一次性”的（一次 complete 唤醒一个 wait）。如果需要重复使用同一个 completion 变量，必须显式重置（`reinit_completion`）。
*   **支持广播：** 可以一次性唤醒所有等待的线程 (`complete_all`)。
*   **睡眠锁：** 等待 Completion 会导致进程睡眠（上下文切换），因此**只能在进程上下文中使用**等待函数。

---

### 2. 主要 API 接口

在使用前需包含头文件：
```c
#include <linux/completion.h>
```

#### 2.1 声明与初始化

| API | 描述 | 备注 |
| :--- | :--- | :--- |
| `struct completion my_comp;` | 定义结构体 | |
| `init_completion(&my_comp);` | **动态**初始化 | 运行时使用 |
| `DECLARE_COMPLETION(my_comp);` | **静态**定义并初始化 | 相当于定义+init |
| `reinit_completion(&my_comp);` | **重置**完成量 | **重要**：如果你想重复使用一个已经完成过的 completion，必须调用此函数将计数清零。 |

#### 2.2 等待操作 (Wait / Consumer)

等待者调用这些函数，如果事件尚未完成，线程将进入睡眠状态。

| API | 功能描述 | 推荐度 |
| :--- | :--- | :--- |
| `wait_for_completion(struct completion *x)` | 等待完成。**不可中断睡眠**。 | 慎用（防死锁） |
| `wait_for_completion_interruptible(struct completion *x)` | 等待完成。**可被信号中断**。 | **推荐** |
| `wait_for_completion_killable(struct completion *x)` | 等待完成。仅可被致命信号中断。 | 推荐 |
| `wait_for_completion_timeout(struct completion *x, timeout)` | 带**超时**的等待。超时返回 0，成功返回剩余时间。 | 常用 |
| `try_wait_for_completion(struct completion *x)` | 非阻塞尝试。如果已完成返回 1 (并减计数)，否则返回 0。 | 不睡眠 |

#### 2.3 唤醒操作 (Signaling / Producer)

执行操作的线程在任务完成后调用这些函数。

| API | 功能描述 | 场景 |
| :--- | :--- | :--- |
| `complete(struct completion *x)` | 唤醒**一个**正在等待的线程。 | 1对1 同步 |
| `complete_all(struct completion *x)` | 唤醒**所有**正在等待的线程。 | 1对多（广播），如模块卸载通知 |
| `complete_and_exit(struct completion *x, long code)` | 唤醒等待者并立即退出当前线程。 | 用于内核线程退出的最后一步 |

---

### 3. 实现原理

Completion 的实现非常简洁，它基于 **自旋锁 (Spinlock)** 和 **等待队列 (Wait Queue)**。

#### 3.1 数据结构
```c
struct completion {
    unsigned int done;      // 计数器：0 表示未完成，>0 表示完成
    wait_queue_head_t wait; // 等待队列头：存放睡眠的等待者
};
```

#### 3.2 工作流程

1.  **Wait (等待)**：
    *   获取自旋锁。
    *   检查 `done` 的值。
    *   如果 `done > 0`：说明事件已发生，将 `done` 减 1，释放锁，函数返回（不睡眠）。
    *   如果 `done == 0`：将当前进程加入 `wait` 队列，设置进程状态（如 `TASK_INTERRUPTIBLE`），释放锁，调用 `schedule()` 进行睡眠。

2.  **Complete (唤醒)**：
    *   获取自旋锁。
    *   `done` 加 1。
    *   唤醒 `wait` 队列中的一个（`complete`）或所有（`complete_all`）进程。
    *   释放锁。

---

### 4. 为什么要用 Completion 而不是 Semaphore？

虽然 Semaphore 初始化为 0 也可以实现“等待-唤醒”，但 Completion 有两个核心优势：

1.  **语义清晰**：Completion 明确表示“等待某事完成”，而 Semaphore 更多暗示“资源计数”。
2.  **避免竞争冒险（Race Condition）—— 尤其是“锁被释放后立即消失”的问题。**
    *   **Semaphore 的问题**：假设线程 A 在栈上分配了一个信号量，然后等待线程 B 唤醒它。线程 B 调用 `up()` 唤醒 A。如果 A 醒来得非常快，在 B 的 `up()` 函数还没完全执行完（例如还没来得及释放自旋锁）时，A 就退出了函数，栈上的信号量被销毁了。此时 B 还在访问这个已经被销毁的信号量，会导致内核崩溃。
    *   **Completion 的解决**：`complete()` 的实现经过特殊设计（自旋锁保护），能够更安全地处理这种“唤醒者还在运行，等待者已经要销毁数据结构”的边缘情况。

---

### 5. 典型应用场景

#### 5.1 线程启动与同步
内核创建一个新的内核线程（kthread）时，父进程可能需要等待子线程完全启动并初始化完毕，才能继续执行。

```c
/* 定义 */
struct completion my_thread_start;

/* 线程函数 */
int my_thread_func(void *data) {
    /* 做一些初始化工作 ... */
    
    /* 通知父进程：我准备好了 */
    complete(&my_thread_start);
    
    /* 进入主循环 */
    while (!kthread_should_stop()) { ... }
    return 0;
}

/* 启动代码 */
void start_work(void) {
    init_completion(&my_thread_start);
    kthread_run(my_thread_func, NULL, "my_worker");
    
    /* 等待线程初始化完成 */
    wait_for_completion(&my_thread_start);
    
    printk("Worker thread started and ready!\n");
}
```

#### 5.2 硬件驱动的 DMA/IRQ 等待
驱动程序发起一个硬件操作（如 DMA 传输），然后必须等待硬件产生中断信号表示传输完成。
1.  驱动发起 DMA，调用 `reinit_completion()` 和 `wait_for_completion_timeout()`。
2.  硬件完成工作，触发中断。
3.  中断处理函数（ISR）中调用 `complete()`。
4.  驱动程序被唤醒，继续后续处理。

#### 5.3 vfork 实现
在 Linux 中，`vfork` 系统调用会让父进程阻塞，直到子进程调用 `exec` 或 `exit`。内核内部就是利用 completion 来实现父进程的等待逻辑。

#### 5.4 模块卸载或停止
当一个模块准备卸载时，可能需要通知所有的工作线程退出，并等待它们真正退出后才能释放内存。
这里通常配合 `complete_all` 使用。

---

### 6. 使用注意事项 (坑点)

1.  **重复使用需重置 (`reinit_completion`)**：
    这是新手最容易犯的错误。Completion 不会自动清零。如果你调用了 `wait_for_completion` 成功返回了，下一次你想再次复用这个变量进行等待，必须先调用 `reinit_completion(&comp)`。如果不调，`wait` 函数发现 `done` 还是 1，会直接返回，根本不会等待。

2.  **栈上变量的生命周期**：
    如果 `struct completion` 定义在函数 A 的栈上，函数 A 调用 `wait`。函数 B 调用 `complete`。这是安全的。
    但是，如果 `complete` 还没调，函数 A 就因为超时或其他原因退出了（栈内存失效），而函数 B 随后试图去 `complete` 这个野指针，就会崩溃。
    **解决：** 确保等待者在 `completion` 对象生命周期结束前，不会让唤醒者有机会访问它。或者使用 `wait_for_completion_timeout` 时要非常小心处理超时逻辑，确保后续不会有迟到的 `complete` 调用。

### 总结

*   **Completion** 是 Linux 内核中用于 **“任务 A 等待 任务 B”** 的标准机制。
*   它比 Semaphore 更简单、更语义化，且在处理线程退出同步时更安全。
*   **记住两个关键点**：用完要 `reinit`，多线程通知用 `complete_all`。

## Mutex
Linux 内核中的 **Mutex** (Mutual Exclusion，互斥锁) 是现代 Linux 驱动开发中最常用、最重要的同步原语之一。
它的设计目标非常明确：**保护临界区，确保同一时间只有一个线程（任务）能访问共享资源。**
与信号量（Semaphore）相比，Mutex 的语义更严格，性能通常也更高（因为有针对性的优化）。 

---
### 主要API
以下是 Linux 内核模块中 **Mutex (互斥锁)** 主要 API 的功能描述表。

在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/mutex.h>
```

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 / 返回值 |
| :--- | :--- | :--- | :--- |
| **声明与初始化** | `DEFINE_MUTEX(mutexname)` | **静态**定义并初始化一个名为 `mutexname` 的互斥锁。 | 宏定义。常用于全局变量。 |
| | `mutex_init(struct mutex *lock)` | **动态**初始化一个互斥锁结构体。 | 常用于结构体内部成员的初始化。 |
| **加锁操作**<br>(获取锁) | `mutex_lock(struct mutex *lock)` | 获取锁。如果锁不可用，进入**不可中断睡眠** (`TASK_UNINTERRUPTIBLE`)。 | **慎用**。无法被信号打断。若发生死锁，进程将变为僵尸（D状态），无法被 kill。 |
| | `mutex_lock_interruptible(struct mutex *lock)` | 获取锁。如果锁不可用，进入**可中断睡眠** (`TASK_INTERRUPTIBLE`)。 | **推荐使用**。<br>**返回 0**：成功获取锁。<br>**返回 -ERESTARTSYS**：被信号（如 Ctrl+C）中断，需处理退出。 |
| | `mutex_lock_killable(struct mutex *lock)` | 获取锁。仅在收到**致命信号**（如 kill -9）时会被中断。 | 介于前两者之间。防止产生无法杀死的进程，比 `mutex_lock` 更安全。 |
| | `mutex_trylock(struct mutex *lock)` | **非阻塞**尝试获取锁。 | **不会睡眠**。<br>**返回 1**：成功获取锁。<br>**返回 0**：获取失败（锁被占用）。 |
| **解锁操作**<br>(释放锁) | `mutex_unlock(struct mutex *lock)` | 释放互斥锁。 | **必须由持有锁的同一个线程调用**。严禁在线程 A 加锁，在线程 B 解锁。 |
| **状态查询** | `mutex_is_locked(struct mutex *lock)` | 检查锁当前是否被占用。 | 返回 1 表示被锁定，0 表示未锁定。<br>通常仅用于调试或断言，不应用于并发控制逻辑（因为查询完状态可能瞬间改变）。 |
| **调试/特殊** | `mutex_lock_nested(lock, subclass)` | 用于死锁检测器 (Lockdep) 的特殊加锁函数。 | 仅在复杂的嵌套加锁场景下（如遍历目录树）为了安抚 Lockdep 使用，普通驱动开发极少用到。 |
#### 关键注意事项

1.  **上下文限制**：
    *   Mutex 获取失败会**睡眠**，因此**严禁在中断上下文**（Hardware IRQ, Softirq, Tasklet）中使用。
    *   中断中请使用 `spinlock`。

2.  **严格的所有权 (Strict Ownership)**：
    *   Mutex 具有严格的拥有者概念。**“解铃还须系铃人”**。
    *   Linux 内核开启 `CONFIG_DEBUG_MUTEX` 后，如果非拥有者尝试解锁，会触发 Kernel Oops。

3.  **不可递归**：
    *   同一个线程不能对同一个 Mutex 重复加锁（Recursive Locking），否则会直接导致**死锁**（自己等自己释放）。

4.  **返回值检查**：
    *   在使用 `mutex_lock_interruptible` 时，**务必检查返回值**。
    ```c
    if (mutex_lock_interruptible(&my_lock)) {
        return -ERESTARTSYS; // 不要继续执行临界区代码！
    }
    /* 临界区 */
    mutex_unlock(&my_lock);
    ```
### 1. 核心特性

1.  **睡眠锁 (Sleeping Lock)**：
    * 如果一个线程试图获取已被占用的 Mutex，该线程**不会**像自旋锁（Spinlock）那样忙等待（占用 CPU 空转）。相反，它会**挂起（Sleep）**，让出 CPU 给其他任务，直到锁被释放。
    * **结论：** Mutex **只能在进程上下文 (Process Context) 中使用**，严禁在中断上下文 (Interrupt Context) 中使用。

2.  **严格的所有权 (Strict Ownership)**：
    *   **谁加锁，谁解锁**。
    *   线程 A 获取了 Mutex，必须由线程 A 来释放。不能由线程 B 释放。
    *   这与信号量（Semaphore）不同，信号量允许 A 等待，B 唤醒。
    *   *内核开启 DEBUG_MUTEX 选项后，如果违反此规则会直接报错。*

3.  **不可递归 (Non-recursive)**：
    *   Linux 的 Mutex 默认**不支持递归加锁**。
    *   如果线程 A 已经持有锁，再次调用 `mutex_lock` 会导致死锁（自己等自己）。

4.  **优先级继承 (Priority Inheritance, PI)**：
    *   虽然标准 Mutex 不完全实现 RT (Real-Time) 的 PI，但 Linux 的 `rt_mutex` 机制支持优先级继承，用于解决“优先级翻转”问题。标准 Mutex 在某种程度上也受益于相关优化。

5.  **乐观自旋 (Optimistic Spinning)**：
    *   这是 Mutex 性能优于 Semaphore 的关键。
    *   当申请锁失败时，如果**当前持有锁的拥有者（Owner）正在另一个 CPU 上运行**，请求者不会立即睡眠，而是会短暂自旋等待。因为拥有者可能很快就会释放锁，自旋的开销远小于“睡眠+唤醒”的上下文切换开销。

---

### 2. Mutex vs 其他锁

| 特性 | **Mutex (互斥锁)** | **Spinlock (自旋锁)** | **Semaphore (信号量 init 1)** |
| :--- | :--- | :--- | :--- |
| **等待机制** | **睡眠** (Context Switch) | **自旋** (Busy Wait) | 睡眠 |
| **适用上下文** | **进程上下文** | **任何上下文** (含中断) | 进程上下文 |
| **临界区耗时** | 允许较长，允许睡眠 | 必须极短，严禁睡眠 | 允许较长 |
| **所有权** | **严格** (谁锁谁解) | 无严格限制 (通常谁锁谁解) | **无** (A锁B解) |
| **性能** | 高 (有乐观自旋优化) | 高 (仅在无竞争时) | 一般 (无乐观自旋) |
| **首选场景** | **互斥保护的首选** | 中断处理、极短临界区 | 计数控制、复杂同步 |

---


### 3. 数据结构与原理 (简述)

在 `include/linux/mutex.h` 中：

```c
struct mutex {
    atomic_long_t       owner;      // 记录持有锁的 task_struct 指针
    spinlock_t          wait_lock;  // 保护等待队列的自旋锁
    struct list_head    wait_list;  // 等待队列 (存放睡眠的进程)
    /* ... 调试字段 ... */
};
```

**工作流程：**
1.  **Fast Path (快速路径)**：
    *   调用 `mutex_lock` 时，直接尝试原子操作 (Atomic Compare-Exchange) 修改 `owner` 字段。
    *   如果 `owner` 为 NULL，直接修改为当前进程，**获取成功，无需进入内核深层逻辑，性能极高**。
2.  **Mid Path (乐观自旋)**：
    *   如果锁被占用，但**拥有者正在另一个 CPU 上运行**，当前进程会进行自旋（Spin），等待拥有者释放。
    *   这避免了进程睡眠/唤醒的昂贵开销。
3.  **Slow Path (慢速路径)**：
    *   如果自旋超时或拥有者也在睡眠，当前进程将被放入 `wait_list`，状态设为睡眠，并调用调度器 (`schedule()`) 让出 CPU。

---

### 4. 使用注意事项 (坑点)

1.  **禁止在中断中使用**：
    *   `mutex_lock` 可能会睡眠，在中断上下文（ISR、Softirq、Tasklet）中睡眠会导致内核崩溃（Oops）。
    *   中断中请使用 `spinlock`。

2.  **禁止递归加锁**：
    *   Linux Mutex 不是递归锁。
    *   `mutex_lock(A); ... mutex_lock(A);` -> **死锁**。

3.  **必须在同一上下文解锁**：
    *   不能在线程 A 中 lock，在线程 B 中 unlock。如果需要这种“交接”功能，请使用 `semaphore` 或 `completion`。

4.  **不要持有太久**：
    *   虽然比自旋锁可以持有更久，但长时间持有 Mutex 会降低系统并发度。如果临界区极长（例如涉及用户交互等待），考虑代码设计是否合理。

5.  **与 kmalloc 的配合**：
    *   如果在临界区内进行内存分配 (`kmalloc`)，可以使用 `GFP_KERNEL`（允许睡眠），因为 Mutex 本身就是允许睡眠的。但在 Spinlock 临界区内只能用 `GFP_ATOMIC`。

### 总结

*   **Mutex** 是 Linux 内核中用于**进程级互斥**的标准工具。
*   它比 Semaphore 更高效、更安全（有所有权检查）。
*   **口诀**：
    *   **能用 Mutex 就别用 Semaphore。**
    *   **能用 Mutex 就别用 Spinlock (除非在中断里或临界区极短)。**
    *   **记得处理 `mutex_lock_interruptible` 的返回值。**

## mutex vs rt_mutex

Linux内核中的`mutex`（互斥锁）和`rt_mutex`（实时互斥锁）虽然都是睡眠锁（sleeping locks），但它们的设计目标、内部实现机制以及应用场景有显著的区别。

简而言之：**`mutex`注重通用性能和吞吐量，而`rt_mutex`注重实时性、确定性以及解决优先级反转问题。**

以下是两者的详细区别对比：

### 1. 核心设计目标与优先级继承 (Priority Inheritance)
这是两者最大的区别。

*   **`rt_mutex` (Real-Time Mutex)**:
    *   **原生支持优先级继承 (PI)**。当一个高优先级任务等待由低优先级任务持有的锁时，`rt_mutex`会自动将低优先级持有者的优先级临时提升到高优先级任务的水平。
    *   **目的**：解决**优先级反转 (Priority Inversion)** 问题，防止高优先级任务被中等优先级的任务无限期阻塞。
    *   **复杂性**：为了支持PI，它需要处理复杂的优先级传播链（例如：任务A等锁L1，L1由任务B持有，任务B又在等锁L2...）。

*   **`mutex` (Standard Mutex)**:
    *   **不支持优先级继承**（标准内核配置下）。它更关注在多核系统下的高吞吐量。
    *   **策略**：通常采用“乐观自旋”（Optimistic Spinning）配合MCS锁机制，在锁持有者正在运行时尝试自旋等待，而不是立即睡眠，从而减少上下文切换的开销。

### 2. 内部数据结构与等待队列
由于功能不同，它们的内部实现结构也完全不同：

*   **等待队列结构**:
    *   **`mutex`**: 使用**双向链表 (`struct list_head`)**。等待者通常按照FIFO（先入先出）顺序排队（或者结合乐观自旋机制）。
    *   **`rt_mutex`**: 使用**红黑树 (`rbtree`)**。等待者严格按照**优先级**顺序排队。这意味着如果有新的高优先级任务加入等待，它会插入到队列的前面，而不是尾部。

*   **结构体大小**:
    *   **`mutex`**: 结构体较小（通常为32字节左右，视架构而定），包含`owner`、`wait_lock`、`wait_list`以及用于乐观自旋的`osq`锁。
    *   **`rt_mutex`**: 结构体较大，因为它需要维护红黑树根节点以及为了支持PI所需的额外字段。

### 3. 性能表现 (Performance)
*   **`mutex`**:
    *   **吞吐量高**。针对“锁竞争不激烈”或“持有时间短”的场景进行了极度优化。
    *   **MCS Spin-on-Owner**: 使用OSQ（Optimistic Spinning Queue）机制，让等待者在各自的缓存行上自旋，避免了多CPU同时争抢同一缓存行（Cache-line bouncing），极大提高了多核性能。

*   **`rt_mutex`**:
    *   **开销较大**。由于需要维护红黑树、计算优先级继承逻辑，其加锁和解锁路径（Slowpath）比普通mutex要慢。
    *   **确定性**。它的设计不是为了平均情况下的最快速度，而是为了保证在最坏情况下的延迟是可预期的。

### 4. 使用场景与 PREEMPT_RT
*   **在普通 Linux 内核 (Mainline) 中**:
    *   **`mutex`**: 是驱动开发和内核子系统中默认使用的互斥锁。除非你有明确的优先级反转问题需要解决，否则应始终使用`mutex`。
    *   **`rt_mutex`**: 主要作为内核内部机制使用，最典型的用户是**PI-Futex**（用户空间的pthread_mutex_t如果设置了`PTHREAD_PRIO_INHERIT`属性，底层就是由`rt_mutex`支持的）。

*   **在 PREEMPT_RT (实时补丁/实时内核) 中**:
    *   这是一个巨大的变化点。在开启了 `CONFIG_PREEMPT_RT` 的内核中，普通的 `mutex`、`spinlock_t`、`rwlock_t` 等大部分锁机制，在底层都会被替换为基于 **`rt_mutex`** 的实现。
    *   这意味着在实时内核中，几乎所有的内核锁都变成了“可睡眠的”且“支持优先级继承的”锁，以最大限度地减少不可抢占的时间片，从而保证硬实时性能。

### 总结对比表

| 特性 | Mutex (标准互斥锁) | Rt_mutex (实时互斥锁) |
| :--- | :--- | :--- |
| **主要目标** | 高吞吐量、低开销 | 实时性、确定性 (Determinism) |
| **优先级继承 (PI)** | 不支持 | **原生支持** (核心特性) |
| **等待队列算法** | FIFO (双向链表) | **优先级排序** (红黑树) |
| **自旋优化** | 激进的 MCS 锁 (OSQ) 自旋 | 有限/适应性自旋 |
| **结构体大小** | 较小 (Compact) | 较大 (Heavyweight) |
| **典型用途** | 通用驱动、文件系统、普通内核代码 | PI-Futex 后端、PREEMPT_RT 内核的基础锁 |

**一句话建议**：如果你在编写普通的Linux驱动，请使用 `mutex`；只有当你深入研究实时补丁实现，或者需要手动处理极端复杂的优先级反转场景时，才会直接接触到 `rt_mutex`。