## seqlock
Linux 内核中的 **Seqlock (Sequential Lock)** 是一种用于解决读者-写者（Reader-Writer）问题的同步机制。

它的设计哲学非常独特：**极度偏向写者 (Writer-biased)**。与普通的读写锁（rwlock）不同，在 Seqlock 机制中，写者永远不会被读者阻塞。

以下是对 Seqlock 的详细介绍，包括其原理、数据结构、使用场景以及优缺点。

---

### 1. 核心设计思想

Seqlock 的核心思想依赖于一个**序列号（Sequence Number）**。

1.  **写者优先：** 只要没有其他**写者**持有锁，写者就可以立即进入临界区修改数据，不需要等待读者完成。
2.  **读者乐观：** 读者假设在读取数据的过程中不会发生冲突（乐观锁）。读者在读取结束后，检查序列号是否发生变化。如果发生了变化（说明读的过程中有写者介入），读者必须重试。

### 2. 工作原理

Seqlock 内部由两部分组成：
1.  一个 **自旋锁 (Spinlock)**：用于写者之间的互斥。
2.  一个 **序列计数器 (Sequence Counter)**：用于读者判断数据是否一致。

#### 序列号的规则
*   **初始值**：0。
*   **写者进入**：获取自旋锁，将序列号 **+1**（变成奇数）。
*   **写者离开**：将序列号 **+1**（变成偶数），释放自旋锁。

**结论：**
*   如果序列号是 **奇数**：表示当前有写者正在修改数据。
*   如果序列号是 **偶数**：表示当前数据稳定，没有写者在操作。

#### 写者流程 (Writer)
1.  获取自旋锁（与其他写者互斥）。
2.  序列号 `seq++` (变为奇数)。
3.  写内存屏障 (`smp_wmb`)，保证序列号更新在数据修改之前可见。
4.  **修改数据**。
5.  写内存屏障 (`smp_wmb`)，保证数据修改在序列号再次更新前完成。
6.  序列号 `seq++` (变为偶数)。
7.  释放自旋锁。

#### 读者流程 (Reader)
1.  **开始读取**：获取当前的序列号 `seq_start`。
    *   如果 `seq_start` 是奇数，说明正在写，读者可以选择忙等待直到它变偶数。
2.  读内存屏障 (`smp_rmb`)。
3.  **拷贝/读取数据**。
4.  读内存屏障 (`smp_rmb`)。
5.  **结束读取**：再次获取当前序列号 `seq_end`。
6.  **校验**：
    *   如果 `seq_start != seq_end`，说明读取过程中发生了写操作 -> **重试**。
    *   如果 `seq_start` 是奇数（虽然第一步检查了，但可能在读的过程中被改了），也说明数据可能不一致 -> **重试**。

---

### 3. 数据结构与 API

在 Linux 内核代码中（`include/linux/seqlock.h`），主要结构如下：

```c
typedef struct {
    struct seqcount seqcount; // 序列号计数器
    spinlock_t lock;          // 写者互斥锁
} seqlock_t;
```

#### 常用 API

**1. 初始化**
```c
seqlock_t my_seqlock;
seqlock_init(&my_seqlock);
```

**2. 写者操作**
```c
void write_seqlock(seqlock_t *sl);
void write_sequnlock(seqlock_t *sl);

// 中断安全版本
void write_seqlock_irqsave(seqlock_t *sl, unsigned long flags);
void write_sequnlock_irqrestore(seqlock_t *sl, unsigned long flags);
```

**3. 读者操作**
```c
unsigned int read_seqbegin(const seqlock_t *sl);
int read_seqretry(const seqlock_t *sl, unsigned int start);
```

#### 代码示例

**写者：**
```c
write_seqlock(&my_seqlock);
// 修改共享数据
global_config.a = 10;
global_config.b = 20;
write_sequnlock(&my_seqlock);
```

**读者：**
```c
unsigned int seq;
int a, b;

do {
    seq = read_seqbegin(&my_seqlock);
    // 关键：读取共享数据
    a = global_config.a;
    b = global_config.b;
} while (read_seqretry(&my_seqlock, seq));

// 此时 a 和 b 是有一致性的
```

---

### 4. 适用场景

Seqlock 并不是通用的锁，它适用于非常特定的场景：

1.  **读多写少**：读操作非常频繁，写操作很少。
2.  **写者极其重要**：不能让读者阻塞写者（例如更新系统时间 `jiffies` 或路由表）。
3.  **临界区很小**：被保护的数据结构简单且小（通常是几个整型变量）。如果临界区太大，读者读取时间过长，容易频繁发生冲突导致无限重试。
4.  **无副作用**：读者的临界区代码不能有副作用（side effects）。因为读者可能会被迫多次执行循环体。

**典型应用案例：**
*   Linux 系统时间管理（`xtime`）：写者是由时钟中断触发的，优先级极高；读者是 `gettimeofday`，需要频繁读取。
*   某些网络统计数据。

---

### 5. Seqlock 的局限性与禁忌

Seqlock 虽然高效，但使用不当会导致严重 Bug：

1.  **不能保护指针引用的对象（内存释放问题）**：
    *   如果写者要把一个指针 `ptr` 修改为 `NULL` 并释放原内存，而读者正在解引用旧的 `ptr`。
    *   虽然 Seqlock 会检测到重试，但在检测之前，读者可能已经访问了被释放的内存，导致 **Kernel Panic**。
    *   *解决方案*：对于指针保护，通常使用 **RCU (Read-Copy-Update)** 机制，而不是 Seqlock。

2.  **读者饥饿 (Reader Starvation)**：
    *   如果写者非常频繁，读者可能一直处于 `do { ... } while(...)` 的循环中无法退出。

3.  **数据类型限制**：
    *   被保护的数据最好不包含指针（除非你非常确信该指针指向的内存不会被释放，只会修改指针的值指向另一个静态存在的对象）。

---

### 6. 与其他锁的对比

| 特性 | Spinlock (自旋锁) | Rwlock (读写锁) | Seqlock (顺序锁) | RCU |
| :--- | :--- | :--- | :--- | :--- |
| **写者阻塞** | 被其他持有者阻塞 | 被读者或写者阻塞 | **仅被写者阻塞** | 不阻塞（使用副本） |
| **读者阻塞** | 被其他持有者阻塞 | 被写者阻塞 | **不阻塞** (但可能重试) | 不阻塞 |
| **主要开销** | 锁竞争 | 锁竞争 | 读者重试循环 | 内存空间(副本) |
| **指针保护** | 安全 | 安全 | **不安全** (可能访问野指针) | 非常适合 |
| **适用场景** | 短小临界区，读写均等 | 读多写少，写者可等待 | 读极多写极少，写者不可等待 | 读多写少，链表/指针 |

### 总结

Linux 内核的 Seqlock 是一种**以空间换时间（重试循环）**且**牺牲读者公平性以保证写者实时性**的同步原语。

当你需要保护一组较小的变量（如坐标、时间戳、统计计数），且写操作极其重要不能被读操作拖慢时，Seqlock 是最佳选择。但如果涉及复杂的链表操作或内存分配释放，请务必转向 RCU 或其他锁机制。

## APIs

Linux 内核中的 `seqlock_t`（顺序锁）API 主要定义在 `&lt;include/linux/seqlock.h&gt;` 头文件中。

以下是 Seqlock 模块主要 API 的详细汇总，分为 **初始化**、**读者操作** 和 **写者操作** 三大类。

### 1. 初始化 (Initialization)

| API 名称 | 参数 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `seqlock_init(sl)` | `seqlock_t *sl` | **动态初始化**一个 seqlock。 | 适用于通过 `kmalloc` 分配或嵌入在结构体中的锁。 |
| `DEFINE_SEQLOCK(name)` | `name` (变量名) | **静态定义并初始化**一个 seqlock。 | 用于定义全局变量或静态变量。 |

---

### 2. 读者操作 (Reader Side)

读者操作的核心是“乐观读取”与“重试机制”。**注意：读者不需要获取锁，因此不会阻塞写者。**

| API 名称 | 参数 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `read_seqbegin(sl)` | `const seqlock_t *sl` | **开始读取**。返回当前的序列号（sequence number）。 | 读者必须保存该返回值，供后续校验使用。如果当前是奇数（正在写），它会自旋等待直到变为偶数。 |
| `read_seqretry(sl, start)` | `const seqlock_t *sl`<br>`unsigned int start` | **校验读取结果**。检查当前的序列号是否与 `start` 一致，且不为奇数。 | 返回 `0` 表示读取有效；<br>返回 `1` 表示读取期间发生了写操作，需要重试。 |
| `read_seqbegin_irqsave(sl, flags)` | `sl`, `flags` | **中断安全版本的开始读取**。保存中断状态并禁止本地中断，返回序列号。 | 仅在读者需要禁止中断以防止死锁（例如读者在中断上下文且可能被写者抢占）时使用。 |
| `read_seqretry_irqrestore(sl, start, flags)`| `sl`, `start`, `flags` | **中断安全版本的校验**。校验序列号并恢复中断状态。 | 必须与 `read_seqbegin_irqsave` 成对使用。 |

**读者标准代码范式：**
```c
unsigned int seq;
do {
    seq = read_seqbegin(&sl);
    // ... 读取数据 ...
} while (read_seqretry(&sl, seq));
```

---

### 3. 写者操作 (Writer Side)

写者操作本质上是 **Spinlock (自旋锁) + 序列号递增**。根据上下文不同（是否在中断、软中断中运行），需要选择不同的 API。

#### A. 基础写者 (进程上下文)

| API 名称 | 参数 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `write_seqlock(sl)` | `seqlock_t *sl` | **加锁**。获取自旋锁，将序列号 +1（变奇数），加入写内存屏障。 | 仅用于进程上下文，且确认不会被中断处理程序打断。 |
| `write_sequnlock(sl)` | `seqlock_t *sl` | **解锁**。加入写内存屏障，将序列号 +1（变偶数），释放自旋锁。 | 必须与 `write_seqlock` 成对使用。 |
| `write_tryseqlock(sl)` | `seqlock_t *sl` | **尝试加锁**。尝试获取内部的自旋锁。 | 成功返回 1，失败返回 0。不会自旋等待。 |

#### B. 中断安全 (IRQ Safe) - 推荐常用

当写者可能在 **硬中断 (Hardware Interrupt)** 上下文中运行时，或者写者会被硬中断抢占时使用。

| API 名称 | 参数 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `write_seqlock_irqsave(sl, flags)` | `sl`, `flags` | **保存中断状态并加锁**。关中断，保存 `flags`，获取锁，更新序列号。 | **最常用的写者 API**，安全性最高，防止死锁。 |
| `write_sequnlock_irqrestore(sl, flags)` | `sl`, `flags` | **解锁并恢复中断状态**。更新序列号，释放锁，恢复 `flags`。 | 必须与 `_irqsave` 版本成对使用。 |
| `write_seqlock_irq(sl)` | `sl` | **关中断并加锁**。直接禁止本地中断（不保存状态）。 | 仅在确信调用前中断是开启的情况下使用（较少用，建议用 `irqsave`）。 |
| `write_sequnlock_irq(sl)` | `sl` | **解锁并开中断**。 | 必须与 `_irq` 版本成对使用。 |

#### C. 软中断安全 (Bottom Half / Softirq Safe)

当写者可能在 **软中断 (Softirq/Tasklet)** 上下文中运行时使用。

| API 名称 | 参数 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `write_seqlock_bh(sl)` | `seqlock_t *sl` | **关软中断并加锁**。禁止本地软中断（Bottom Half），获取锁。 | 防止软中断抢占导致死锁。 |
| `write_sequnlock_bh(sl)` | `seqlock_t *sl` | **解锁并开软中断**。 | 必须与 `_bh` 版本成对使用。 |

---

### 4. 辅助/高级 API (Seqcount)

有些场景下，开发者想用自己的锁（如 Mutex）而不是 Seqlock 自带的 Spinlock 来保护写临界区，此时可以直接操作 `seqcount`。

| API 名称 | 参数 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `write_seqcount_begin(s)` | `seqcount_t *s` | 仅递增序列号（标记写开始）。不包含任何锁操作。 | 调用者必须自己持有外部锁（如 Mutex）。 |
| `write_seqcount_end(s)` | `seqcount_t *s` | 仅递增序列号（标记写结束）。 | 必须在释放外部锁之前调用。 |

### 总结选择指南

1.  **你是读者**：
    *   绝大多数情况：`read_seqbegin` + `read_seqretry`。
2.  **你是写者**：
    *   **常规且安全**（不知道上下文）：`write_seqlock_irqsave` / `write_sequnlock_irqrestore`。
    *   **只在进程上下文**（无中断干扰）：`write_seqlock` / `write_sequnlock`。
    *   **涉及软中断**（网络/定时器）：`write_seqlock_bh` / `write_sequnlock_bh`。