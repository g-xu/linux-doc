
## RCU
Linux 内核中的 **RCU (Read-Copy Update)** 是一种高性能的同步机制，专门用于解决**读多写少**（Read-mostly）场景下的并发问题。它是 Linux 内核扩展性（Scalability）的基石之一。相比于传统的读写锁（RW-Lock），RCU 最大的优势在于**读侧（Reader）几乎没有任何开销**（不需要获取锁，不需要原子操作指令，甚至不需要内存屏障），且**读写可以并行进行**。

---

### 1. RCU 的核心理念

RCU 的全称是 **Read-Copy Update**，其核心思想包含三个动作：

1.  **Read（读）**：读者可以直接访问数据，不需要加锁。
2.  **Copy（复制）**：写者如果要修改数据，不直接在原数据上修改，而是先分配新内存，将旧数据复制过去，并在新内存上进行修改。
3.  **Update（更新）**：写者修改完新数据后，用原子指令（如指针赋值）将全局指针指向新数据。

**关键点：** 在指针切换之后，旧数据不能立刻释放（Free），因为可能还有旧的读者正在读取它。必须等待所有旧读者离开临界区后，才能释放旧内存。

### 2. 核心概念与术语

理解 RCU 必须掌握以下几个核心术语：

#### 2.1 临界区 (Critical Section)
读者访问受 RCU 保护数据的代码区域。
- 开始：`rcu_read_lock()`
- 结束：`rcu_read_unlock()`
注意：在传统的不可抢占 RCU 中，读侧临界区内**禁止睡眠**或发生上下文切换。

#### 2.2 宽限期 (Grace Period)
这是 RCU 最精妙的概念。
从“写者将指针指向新数据”开始，到“所有在指针切换前就已经存在的读者都离开临界区”为止的这段时间，称为**宽限期**。
只有过了宽限期，旧数据才是安全的（Safe to reclaim）。

#### 2.3 静止状态 (Quiescent State)
CPU 处于一种“绝对不在 RCU 读侧临界区”的状态。
例如：发生上下文切换、运行在用户态、CPU Idle 等。
当所有 CPU 都经历过至少一次静止状态时，宽限期结束。

---

### 3. RCU 的工作流程 (图解逻辑)

假设我们有一个全局指针 `GP` 指向一个结构体 `Old_Data`，现在要更新它的内容。
#### 步骤 1：Reader (读)
读者直接读取 `GP` 指针。
- 不需要加自旋锁。
- 读者进入临界区前调用 `rcu_read_lock()`，读完调用 `rcu_read_unlock()`。
#### 步骤 2：Copy (复制)
写者**不直接修改** `Old_Data`（防止正在读的读者崩溃）。
- 写者分配一块新内存 `New_Data`。
- 将 `Old_Data` 的内容复制到 `New_Data`。
- 在 `New_Data` 上进行修改。
#### 步骤 3：Update (更新/发布)
写者使用原子操作将 `GP` 指针指向 `New_Data`。
- **关键点**：这是一个原子性的指针替换。
- 在此瞬间之后，新来的读者会看到 `New_Data`；而老的读者手中拿的指针依然指向 `Old_Data`。系统处于“新旧数据共存”的状态。
#### 步骤 4：Synchronize (等待宽限期)
写者不能直接 `free(Old_Data)`。它必须等待。
- 写者调用 `synchronize_rcu()`。
- 系统会监测所有 CPU，确保它们都经历过“上下文切换”或退出了读侧临界区。
#### 步骤 5：Reclaim (回收)
宽限期结束，意味着没有读者引用 `Old_Data` 了。
- 写者（或回调函数）调用 `kfree(Old_Data)` 释放内存。

---

### 4. 核心 API 介绍

RCU 的 API 分为读侧、写侧和链表操作三大类。

#### 4.1 读侧 API (Reader)
读者非常轻量级，几乎零开销。

```c
rcu_read_lock();            // 禁止内核抢占（Preempt disable）
p = rcu_dereference(ptr);   // 获取指针，隐含了内存屏障，确保读取顺序
if (p != NULL) {
    do_something(p->data);
}
rcu_read_unlock();          // 开启内核抢占
```

#### 4.2 写侧 API (Writer)
写者负责更新和回收。

**A. 同步等待 (Synchronous)**
写者会阻塞，直到宽限期结束。
```c
struct foo *new_p = kmalloc(...);
// ... 初始化 new_p ...
old_p = rcu_dereference_protected(global_ptr, lock_is_held(&mylock));
rcu_assign_pointer(global_ptr, new_p); // 发布新数据，带有写屏障

synchronize_rcu(); // 【阻塞】等待所有现有读者离开
kfree(old_p);      // 释放旧数据
```

**B. 异步调用 (Asynchronous)**
写者不阻塞，注册一个回调函数，宽限期结束后由内核线程执行回调。
```c
void my_callback(struct rcu_head *head) {
    struct foo *p = container_of(head, struct foo, rcu);
    kfree(p);
}

// ... 更新指针逻辑同上 ...
call_rcu(&old_p->rcu, my_callback); // 【非阻塞】立即返回
```

#### 4.3 链表与哈希表操作
RCU 最常用于链表。内核封装了专用宏：

*   `list_add_rcu` / `list_del_rcu` / `list_replace_rcu`
*   `list_for_each_entry_rcu` (遍历专用)

---

### 5. RCU 的变种与内部实现

Linux 内核非常复杂，为了适应不同场景，RCU 有多种实现：
#### 5.1 Classic RCU (Preemptible RCU / Tree RCU)
*   **Tree RCU**：这是现代服务器（SMP/NUMA）默认使用的实现。它使用树形结构层级管理 CPU 的状态，极大减少了锁竞争，支持成千上万个 CPU 核心。
*   **Preemptible RCU**：允许在 `rcu_read_lock` 临界区内发生内核抢占（但仍不能主动睡眠）。这是为了提高实时性（Real-time latency）。

#### 5.2 Tiny RCU
*   专为单 CPU（UP）或嵌入式微控制器设计。
*   因为没有其他 CPU 竞争，RCU 变成了简单的封装，几乎没有运行时开销，代码体积非常小。

#### 5.3 SRCU (Sleepable RCU)
*   **痛点**：普通 RCU 临界区内不能睡眠（不能调用可能导致睡眠的函数，如 `mutex_lock` 或用户态内存访问）。
*   **解决**：SRCU 允许在临界区内睡眠。它拥有独立的子系统，使用 `srcu_read_lock` / `srcu_read_unlock`。
*   **代价**：SRCU 的开销比普通 RCU 大。

#### 5.4 Tasks RCU
*   专门用于保护内核中的 "Trampolines"（如 ftrace, kprobes）。它通过监控进程调度来确定宽限期。

---

### 6. RCU 与其他锁的对比

| 特性 | RCU | 读写锁 (RW-Lock) | 自旋锁 (Spinlock) |
| :--- | :--- | :--- | :--- |
| **读者开销** | **极低 (接近 0)** | 低 (需原子操作，有 Cache 抖动) | 高 (需获取锁) |
| **写者开销** | 高 (需分配内存、复制、等待) | 中 | 低 |
| **读写并行** | **支持** (读写不互斥) | 不支持 (读写互斥) | 不支持 |
| **写写并行** | 不支持 (需额外锁保护写者) | 不支持 | 不支持 |
| **死锁风险** | 读者无死锁 | 有 | 有 |
| **内存开销** | 较高 (旧数据延迟释放) | 低 | 低 |

### 7. 什么时候使用 RCU？

必须同时满足以下条件，才推荐使用 RCU：

1.  **读多写少**：读操作占据绝大多数（例如 90% 以上）。
2.  **数据延迟敏感度低**：读者读取到的数据可能是“几毫秒前”的旧数据（Stale data），逻辑上必须能接受这一点。
3.  **数据结构通过指针访问**：RCU 保护的是指针指向的内存块，非常适合链表、哈希表、树。

**典型应用场景：**
*   **网络路由表**：频繁查询路由，偶尔更新。
*   **VFS (虚拟文件系统)**：目录项缓存（dcache）查找。
*   **设备驱动配置**：设备状态读取频繁，配置修改少。
*   **内核模块卸载**：确保没有代码在运行该模块时才卸载。

### 8. 总结

RCU 是 Linux 内核开发者 Paul E. McKenney 的杰作。它打破了传统锁“互斥”的思维定式，通过**空间换时间**（保留旧数据副本）和**延迟释放**（宽限期）的策略，实现了读操作的极致性能。

对于内核开发者而言，理解 RCU 是从“编写正确代码”进阶到“编写高性能并发代码”的必经之路。
## APIs

以下是 Linux 内核 RCU (Read-Copy Update) 模块主要 API 的详细汇总。为了方便查阅，将其分为**读侧操作**、**写侧发布**、**写侧回收/同步**、**链表/哈希表操作**以及**特殊变种 (SRCU)** 五个类别。

### 1. 基础 RCU API 汇总表

#### 1.1 读侧 API (Reader Side)
读侧的主要任务是标记临界区并安全地获取数据指针。

| API / 宏                       | 功能描述                                                                                                 | 关键点与注意事项                                               |
| :---------------------------- | :--------------------------------------------------------------------------------------------------- | :----------------------------------------------------- |
| **`rcu_read_lock()`**         | **开始读侧临界区**。<br>禁止内核抢占（在 Preemptible RCU 中更为复杂，但逻辑上禁止任务切换）。                                          | 必须与 `rcu_read_unlock()` 配对使用。在此区域内**严禁睡眠**（除非使用 SRCU）。 |
| **`rcu_read_unlock()`**       | **结束读侧临界区**。<br>重新开启内核抢占，标志着读者不再引用受保护的数据。                                                            | 必须在 `rcu_read_lock()` 之后调用。                            |
| **`rcu_dereference(ptr)`**    | **获取 RCU 保护的指针**。<br>用于读取全局指针 `ptr` 的值。包含读内存屏障（`smp_read_barrier_depends` 语义），确保读取到的数据在指针获取之前已初始化完毕。 | **仅在读侧临界区内使用**。返回值的类型与 `ptr` 相同。                       |
| **`rcu_access_pointer(ptr)`** | **访问指针但不解引用**。<br>用于仅测试指针是否为 NULL 或比较指针地址，而不去读取指针指向的数据内容。                                            | 不需要内存屏障，开销比 `rcu_dereference` 小。通常用于统计或状态检查。           |

#### 1.2 写侧 API - 数据发布 (Writer Side - Publishing)
写侧在修改数据时，需要安全地更新指针，确保读者不会看到未初始化的数据。

| API / 宏 | 功能描述 | 关键点与注意事项 |
| :--- | :--- | :--- |
| **`rcu_assign_pointer(ptr, new_p)`** | **发布新指针**。<br>将全局指针 `ptr` 指向新地址 `new_p`。隐含写内存屏障（Write Memory Barrier），确保新数据初始化完成**之前**，指针才会被赋值。 | **写者使用**。通常在持有写锁（如 Spinlock/Mutex）的情况下使用。 |
| **`RCU_INIT_POINTER(ptr, new_p)`** | **初始化指针**。<br>将 `ptr` 赋值为 `new_p`。**不**包含内存屏障。 | 仅在以下情况使用：<br>1. 指针为 NULL。<br>2. 没有任何读者能并发访问该指针（如初始化阶段）。<br>3. 仅仅是更新指针值，不涉及数据内容的发布。 |
| **`rcu_dereference_protected(ptr, condition)`** | **写者读取指针**。<br>写者在更新数据前，有时也需要读取当前指针指向的内容。 | **写者使用**。`condition` 通常是锁断言（如 `lockdep_is_held(&mylock)`），用于 Lockdep 检查以防止无锁并发写。 |

#### 1.3 写侧 API - 回收与同步 (Writer Side - Reclaiming)
更新指针后，写者需要处理旧数据的释放。

| API / 宏 | 功能描述 | 关键点与注意事项 |
| :--- | :--- | :--- |
| **`synchronize_rcu()`** | **同步等待宽限期**。<br>阻塞当前线程，直到所有在调用此函数前存在的读侧临界区都结束。 | **阻塞式（Blocking）**。不能在中断上下文或持有自旋锁时调用。属于“同步”回收。 |
| **`call_rcu(head, func)`** | **注册异步回调**。<br>不阻塞，立即返回。将回调函数 `func` 挂入 RCU 队列，待宽限期结束后由内核线程执行 `func(head)`。 | **非阻塞（Non-blocking）**。适用于中断上下文或高性能路径。`head` 是结构体中嵌入的 `struct rcu_head`。 |
| **`kfree_rcu(ptr, rcu_head_member)`** | **便捷的异步释放**。<br>专门用于 `kfree` 操作的宏，相当于 `call_rcu` 的特化版本，用于释放 `ptr`。 | 现代内核推荐使用。不需要自己写回调函数，直接告诉内核哪个成员是 `rcu_head` 即可。 |
| **`synchronize_rcu_expedited()`** | **加速版同步等待**。<br>强制 CPU 进行上下文切换以加速宽限期结束。 | **慎用**。对系统实时性有严重干扰（发送 IPI 中断所有 CPU），仅在非频繁路径（如卸载模块）使用。 |

---

### 2. 链表与哈希表专用宏 (List & Hash Helpers)

Linux 内核大量使用链表，RCU 为此提供了专属封装，这些宏通常位于 `&lt;linux/rculist.h&gt;`。

| API / 宏 | 功能描述 | 对应标准操作 |
| :--- | :--- | :--- |
| **`list_add_rcu(new, head)`** | **插入节点**。<br>将新节点插入链表头部。包含内存屏障，确保新节点内容对读者可见。 | `list_add` |
| **`list_add_tail_rcu(new, head)`** | **插入节点到尾部**。<br>同上，插入尾部。 | `list_add_tail` |
| **`list_del_rcu(entry)`** | **删除节点**。<br>将节点从链表摘除。**注意**：它只修改前驱节点的 `next` 指针，被删节点的 `next` 指针依然有效（为了保证正在遍历该节点的读者不迷路）。 | `list_del` |
| **`list_replace_rcu(old, new)`** | **替换节点**。<br>用新节点替换旧节点。 | `list_replace` |
| **`list_for_each_entry_rcu(pos, head, member)`** | **遍历链表**。<br>读者专用的遍历宏，内部使用了 `rcu_dereference`。 | `list_for_each_entry` |
| **`hlist_..._rcu`** | **哈希链表操作**。<br>包含 `hlist_add_head_rcu`, `hlist_del_rcu`, `hlist_for_each_entry_rcu` 等，用法类似 list。 | 标准 hlist 操作 |

---

### 3. SRCU (Sleepable RCU) API

当读者需要在临界区内**睡眠**（Sleep/Block）时，必须使用 SRCU。普通的 `rcu_read_lock` 临界区内睡眠是违法的。

| API / 宏 | 功能描述 | 关键点与注意事项 |
| :--- | :--- | :--- |
| **`srcu_read_lock(ssp)`** | **开始 SRCU 读侧临界区**。<br>返回一个索引 `idx`，该索引必须传给 unlock 函数。 | `ssp` 是 `struct srcu_struct` 指针，需要用户定义和初始化。允许在临界区睡眠。 |
| **`srcu_read_unlock(ssp, idx)`** | **结束 SRCU 读侧临界区**。<br>需要传入 lock 返回的 `idx`。 | 必须与 lock 严格配对。 |
| **`synchronize_srcu(ssp)`** | **等待 SRCU 宽限期**。<br>等待所有使用该 `ssp` 的读者离开临界区。 | 写者使用。阻塞式。 |

---

### 4. 快速对照：API 配对使用习惯

为了写出正确的 RCU 代码，请遵循以下配对模式：

| 角色 | 动作 | API 配对模式 |
| :--- | :--- | :--- |
| **Reader** | 普通读取 | `rcu_read_lock()` <br> `... rcu_dereference(...) ...` <br> `rcu_read_unlock()` |
| **Reader** | 链表遍历 | `rcu_read_lock()` <br> `... list_for_each_entry_rcu(...) ...` <br> `rcu_read_unlock()` |
| **Writer** | 阻塞式更新 | `mutex_lock(...)` (保护写者)<br> `... rcu_assign_pointer(...)` <br> `mutex_unlock(...)`<br> `synchronize_rcu()` <br> `kfree(...)` |
| **Writer** | 异步更新 | `mutex_lock(...)` <br> `... rcu_assign_pointer(...)` <br> `mutex_unlock(...)`<br> `call_rcu(...)` 或 `kfree_rcu(...)` |

### 5. 常见错误提示
1.  **在 `rcu_read_lock` 中睡眠**：绝对禁止（会导致 `scheduling while atomic` 错误）。如果必须睡眠，请改用 SRCU。
2.  **直接访问指针**：在读者中直接访问 `p->next` 而不使用 `rcu_dereference` 或 `list_..._rcu` 宏，在某些架构（如 Alpha, ARM）上可能读到乱序的垃圾数据。
3.  **忘记写内存屏障**：写者直接赋值指针 `ptr = new_p` 而不使用 `rcu_assign_pointer`，读者可能在看到指针时，新结构体还没初始化好。