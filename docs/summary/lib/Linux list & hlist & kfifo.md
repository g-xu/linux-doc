## list

在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/list.h>
```

### Linux 内核 List API 汇总表

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- | :--- |
| **定义与初始化** | `LIST_HEAD(name)` | **静态**定义并初始化一个链表头。 | 通常用于全局变量。展开后即为一个初始化好的 `struct list_head`。 |
| | `INIT_LIST_HEAD(ptr)` | **动态**初始化链表头。 | 将 `next` 和 `prev` 指针都指向自己。常用于结构体内的成员初始化。 |
| **插入节点** | `list_add(new, head)` | **头插法**。将新节点 `new` 插入到 `head` 之后。 | 常用于实现**栈**（LIFO）。 |
| | `list_add_tail(new, head)` | **尾插法**。将新节点 `new` 插入到 `head` 之前（即链表尾部）。 | 常用于实现**队列**（FIFO）。 |
| **删除节点** | `list_del(entry)` | 将节点 `entry` 从链表中**移除**。 | 移除后 `entry` 的 `next` 和 `prev` 指向不可知区域（LIST_POISON），**不可**直接再次使用。 |
| | `list_del_init(entry)` | 将节点移除，并立即重新初始化它。 | 移除后，`entry` 指向自己，可以安全地再次被添加到链表中。 |
| **替换与移动** | `list_replace(old, new)` | 用新节点 `new` 替换旧节点 `old`。 | |
| | `list_move(list, head)` | 将节点从原链表删除，并**头插**到 `head` 链表中。 | 等同于 `list_del` + `list_add`。 |
| | `list_move_tail(list, head)` | 将节点从原链表删除，并**尾插**到 `head` 链表中。 | 等同于 `list_del` + `list_add_tail`。 |
| **遍历链表**<br>(重点) | `list_for_each_entry(pos, head, member)` | **正向遍历**。自动将 `list_head` 转换为宿主结构体指针。 | `pos`: 宿主结构体指针 (循环变量)。<br>`head`: 链表头。<br>`member`: `list_head` 在宿主结构体中的成员名。 |
| | `list_for_each_entry_reverse(...)` | **反向遍历**。 | 参数同上，从尾部开始向前遍历。 |
| | `list_for_each_entry_safe(pos, n, head, member)` | **安全遍历**。允许在遍历过程中**删除**当前节点 `pos`。 | `n`: 临时指针 (用于缓存下一个节点)。<br>**如果循环内涉及删除操作，必须用此宏。** |
| **底层转换** | `list_entry(ptr, type, member)` | 根据 `list_head` 指针获取宿主结构体的指针。 | 底层封装了 `container_of`。<br>`ptr`: 指向 list_head 的指针。<br>`type`: 宿主结构体类型。<br>`member`: 成员名。 |
| **状态查询** | `list_empty(head)` | 判断链表是否为空。 | 如果 `head->next == head`，返回 1 (true)。 |
| | `list_is_singular(head)` | 判断链表是否只有一个节点。 | |
| **链表合并** | `list_splice(list, head)` | 将链表 `list` 的所有节点合并到 `head` 链表的头部。 | 合并后 `list` 处于未定义状态，不可再用。 |
| | `list_splice_init(list, head)` | 合并链表，并将原链表 `list` 重新初始化为空。 | 推荐使用，合并后 `list` 仍可安全使用。 |

---

### 关键宏参数说明

遍历宏的参数最容易混淆，以下是图解说明：

假设有如下结构：
```c
struct student {
    int id;
    struct list_head list; // 成员名为 "list"
};

struct list_head class_head; // 链表头
```

在使用 `list_for_each_entry(pos, head, member)` 时：

*   **`pos`**:  `struct student *stu` (这是你在循环里用的指针)
*   **`head`**: `&class_head` (链表的头)
*   **`member`**: `list` (结构体中定义的那个成员的名字，**不是字符串，直接写名字**)

**代码示例：**

```c
struct student *stu; // pos
struct student *tmp; // n (用于 safe 版本)

// 1. 普通遍历
list_for_each_entry(stu, &class_head, list) {
    printk("ID: %d\n", stu->id);
}

// 2. 删除时的安全遍历
list_for_each_entry_safe(stu, tmp, &class_head, list) {
    if (stu->id == 0) {
        list_del(&stu->list);
        kfree(stu);
    }
}
```

## hlist
以下是 Linux 内核模块中 **hlist (Hash List，哈希链表)** 主要 API 的功能描述表。

### 为什么需要 hlist？
在介绍 API 之前，必须先明确 `hlist` 与普通 `list` 的区别：
*   **普通 `list_head`**：包含 `next` 和 `prev` 两个指针（16字节 @ 64位）。
*   **`hlist_head`**：**只包含一个 `first` 指针**（8字节 @ 64位）。
*   **目的**：**为了节省空间**。在哈希表（Hash Table）中，通过数组来存储 bucket head。如果数组很大（例如 100万个槽），使用 `hlist_head` 可以节省一半的内存空间。

---

### hlist 主要 API 汇总表

使用前需包含头文件：
```c
#include <linux/list.h>
```

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- | :--- |
| **定义与初始化** | `HLIST_HEAD(name)` | **静态**定义并初始化一个哈希链表头。 | 展开后是一个结构体变量，其 `first` 指针为 NULL。 |
| | `INIT_HLIST_HEAD(ptr)` | **动态**初始化哈希链表头。 | 将 `ptr->first` 设为 NULL。 |
| | `INIT_HLIST_NODE(ptr)` | 初始化一个 hlist 节点。 | 将节点的 `next` 和 `pprev` 设为 NULL。 |
| **插入节点** | `hlist_add_head(n, h)` | **头插法**。将节点 `n` 插入到链表头 `h` 之后。 | 这是哈希表解决冲突最常用的方式。 |
| | `hlist_add_before(n, next)` | 将节点 `n` 插入到节点 `next` **之前**。 | 注意参数顺序，是把 n 插到 next 前面。 |
| | `hlist_add_behind(n, prev)` | 将节点 `n` 插入到节点 `prev` **之后**。 | 替代了旧版内核的 `hlist_add_after`。 |
| **删除节点** | `hlist_del(n)` | 将节点 `n` 从链表中**移除**。 | 修改前驱节点的 `next` 和后继节点的 `pprev`。删除后节点处于未定义状态。 |
| | `hlist_del_init(n)` | 将节点移除，并立即重新初始化它。 | 移除后，该节点可以安全地再次被添加到其他链表中。 |
| **遍历链表**<br>(重点) | `hlist_for_each(pos, head)` | **基础遍历**。遍历 `hlist_node` 指针。 | `pos`: `struct hlist_node *` 类型循环变量。<br>不直接获取宿主结构体，较少使用。 |
| | `hlist_for_each_entry(tpos, head, member)` | **常用遍历**。自动获取宿主结构体指针。 | `tpos`: 宿主结构体指针。<br>`head`: 链表头。<br>`member`: 成员名。<br>**不可**在遍历中删除节点。 |
| | `hlist_for_each_entry_safe(tpos, n, head, member)` | **安全遍历**。允许在遍历中**删除**当前节点。 | `n`: `struct hlist_node *` 类型的临时指针 (缓存下一个节点)。 |
| **底层转换** | `hlist_entry(ptr, type, member)` | 根据 `hlist_node` 指针获取宿主结构体指针。 | 底层封装了 `container_of`。 |
| **状态查询** | `hlist_empty(head)` | 判断链表是否为空。 | 检查 `head->first` 是否为 NULL。 |
| | `hlist_unhashed(node)` | 判断节点是否**不在**任何链表中。 | 检查 `node->pprev` 是否为 NULL。 |

---

### hlist 的特殊设计：pprev 指针

`hlist` 最大的特点在于其节点结构 `struct hlist_node` 的定义：

```c
struct hlist_head {
    struct hlist_node *first;
};

struct hlist_node {
    struct hlist_node *next;
    struct hlist_node **pprev; // 指向“前一个节点的 next 指针”的地址
};
```

**为什么是 `pprev` (指针的指针) 而不是 `prev`？**

1.  **首节点特殊性**：链表的第一个节点的“前驱”是 `hlist_head`，而 `hlist_head` 只有 `first` 指针，没有 `struct hlist_node` 类型。
2.  **统一操作**：
    *   如果用普通的 `prev` 指针，删除第一个节点时，需要判断“我是不是第一个？”，如果是，要修改 `head->first`；如果不是，修改 `prev->next`。
    *   使用 `pprev` 指向**前驱存放“下一个地址”的地方**（即：对于首节点，它指向 `head->first` 的地址；对于中间节点，它指向前一个节点 `next` 字段的地址）。
    *   这样删除节点时，只需要 `*(node->pprev) = node->next`，一行代码即可兼容首节点和中间节点的情况，无需 `if` 判断。

### 常用代码示例

```c
struct my_data {
    int id;
    struct hlist_node node; // 嵌入的 hlist 节点
};

struct hlist_head my_hash_bucket; // 哈希桶头

void test_hlist(void) {
    struct my_data *obj;
    struct hlist_node *n; // 临时游标，用于 safe 遍历

    // 1. 遍历并删除
    hlist_for_each_entry_safe(obj, n, &my_hash_bucket, node) {
        if (obj->id == 100) {
            hlist_del(&obj->node); // 移除
            kfree(obj);            // 释放
        }
    }
    
    // 2. 普通遍历
    hlist_for_each_entry(obj, &my_hash_bucket, node) {
        printk("ID: %d\n", obj->id);
    }
}
```

## kfifo
Linux 内核中的 **kfifo** 是一个**通用的、无锁的（在特定条件下）、基于 2 的幂次大小的环形缓冲区（Ring Buffer）** 实现。
它被设计为内核中最基础的数据结构之一，专门用于在生产者（Producer）和消费者（Consumer）之间高效地传输数据流。

---

### 1. 核心特性

1.  **无锁并发 (Lockless for SPSC)：**
    *   在**单生产者（One Producer）** 和 **单消费者（One Consumer）** 的场景下，kfifo 不需要任何锁（Spinlock 或 Mutex）即可保证数据一致性。
    *   这是通过内存屏障（Memory Barrier）和巧妙的索引设计实现的。
    *   *注意：如果是多生产者或多消费者，仍然需要加锁。*

2.  **高性能 (Performance)：**
    *   **2 的幂次大小**：kfifo 要求缓冲区的大小必须是 $2^n$（如 1024, 4096）。这使得取余操作（%）可以被位运算（&）替代，极大提高了索引计算速度。
    *   **DMA 友好**：支持散聚列表（Scatter-Gather），可以直接配合 DMA 使用。

3.  **严格的 FIFO 顺序：**
    *   先入先出，保证数据流的顺序性。

4.  **类型安全与通用性：**
    *   Linux 内核通过宏（Macros）实现了类似 C++ 模板的功能，使得 kfifo 可以处理任意类型的结构体或字节流。

---

### 2. 内部数据结构

在 `include/linux/kfifo.h` 中定义。虽然具体实现随内核版本微调，但核心字段如下：

```c
struct __kfifo {
    unsigned int    in;       // 入队指针 (Write Index)
    unsigned int    out;      // 出队指针 (Read Index)
    unsigned int    mask;     // 掩码 (Size - 1)
    unsigned int    esize;    // 元素大小 (Element Size)
    void            *data;    // 指向实际数据缓冲区的指针
};
```

**设计精髓：**
*   **`in` 和 `out` 不回绕归零**：这两个指针是 `unsigned int`，它们一直增加，直到溢出（Overflow）回绕。
*   **取数据下标**：实际访问数组的下标是通过 `off = in & mask` 计算的。
*   **数据长度**：`len = in - out`。利用无符号整数溢出的特性，即使 `in` 溢出了，减法运算依然能得到正确的长度。

---

### 3. 无锁实现原理 (The Lockless Magic)

kfifo 能在 SPSC（单生产单消费）下无锁，依靠的是**内存屏障 (Memory Barrier)** 和**操作顺序**。

#### 入队 (Put / Write)
1.  **拷贝数据**：将数据写入到 `data[in & mask]` 位置。
2.  **写屏障 (`smp_wmb`)**：确保数据在内存中真正落地。
3.  **更新索引**：`in++`。

**为什么安全？** 消费者通过检查 `in` 指针来判断是否有新数据。屏障保证了：当消费者看到 `in` 变大时，对应的数据肯定已经写好了。

#### 出队 (Get / Read)
1.  **拷贝数据**：从 `data[out & mask]` 读取数据。
2.  **读屏障 (`smp_rmb`)**：确保数据读取完成。
3.  **更新索引**：`out++`。

**为什么安全？** 生产者通过检查 `out` 指针来判断是否有空闲空间。屏障保证了：当生产者看到 `out` 变大时，消费者肯定已经把旧数据读走了，可以覆盖写入。

---

### 4.  使用场景

1.  **中断上下文与进程上下文通信：**
    *   这是最经典的场景。硬件中断（ISR）产生数据（如 UART 接收字符），迅速调用 `kfifo_in` 存入缓冲区。
    *   用户进程或下半部（Bottom Half）读取 `kfifo_out` 消费数据。
    *   由于无锁，非常适合中断处理这种对延迟敏感的环境。

2.  **音频/网络数据流缓冲：**
    *   音频驱动通常需要环形缓冲区来平滑播放或录音数据。

3.  **日志记录 (Logging/Tracing)：**
    *   在高频日志记录时，先写入内存 kfifo，再由后台线程批量写入磁盘，避免阻塞业务线程。


### 总结

*   **kfifo** 是 Linux 内核处理字节流或对象流的首选数据结构。
*   它的核心优势是 **SPSC 场景下的无锁性能** 和 **位运算索引**。
*   在使用时，务必确保缓冲区大小是 **2 的幂次方**（`kfifo_alloc` 会自动向上取整，但 `DEFINE_KFIFO` 如果不是 2 的幂可能会报错或浪费空间）。

### APIs
在使用这些 API 之前，请确保已包含头文件：
```c
#include <linux/kfifo.h>
```

| API 分类 | API 函数 / 宏 | 功能描述 | 返回值 / 备注 |
| :--- | :--- | :--- | :--- |
| **声明与定义** | `DEFINE_KFIFO(name, type, size)` | **静态**定义并初始化一个 kfifo。 | `name`: 变量名。<br>`type`: 元素类型 (如 `int`, `struct foo`)。<br>`size`: **必须是 2 的幂**。 |
| | `struct kfifo` | kfifo 核心结构体。 | 通常配合动态分配使用。 |
| **初始化与释放** | `kfifo_alloc(fifo, size, gfp_mask)` | **动态**分配内存并初始化 kfifo。 | **返回 0 表示成功**，负数表示失败。<br>`size` 若不是 2 的幂，内核会自动向上取整。 |
| | `kfifo_init(fifo, buffer, size)` | 使用**已有的内存块**初始化 kfifo。 | 用于你自己管理内存的场景。<br>`size` 必须手动保证是 2 的幂。 |
| | `kfifo_free(fifo)` | 释放由 `kfifo_alloc` 分配的内存。 | **静态定义的 kfifo 不可调用此函数**。 |
| **入队操作**<br>(生产者) | `kfifo_put(fifo, val)` | 将**单个**元素 `val` 放入 kfifo。 | 返回实际推入的元素个数（成功为 1，满为 0）。<br>内部处理了类型，直接传值即可。 |
| | `kfifo_in(fifo, buf, n)` | 将 `buf` 中 **n 个元素** 拷贝入 kfifo。 | 返回实际拷贝的元素个数。<br>注意：是元素个数，不是字节数（除非类型是 char）。 |
| | `kfifo_in_spinlocked(fifo, buf, n, lock)` | **带自旋锁**的入队操作。 | 适用于**多生产者**场景。<br>需传入一个初始化好的 `spinlock_t`。 |
| **出队操作**<br>(消费者) | `kfifo_get(fifo, val)` | 从 kfifo 取出**单个**元素存入 `val`。 | 返回实际取出的元素个数（成功为 1，空为 0）。 |
| | `kfifo_out(fifo, buf, n)` | 从 kfifo 取出 **n 个元素** 存入 `buf`。 | 返回实际拷贝的元素个数。 |
| | `kfifo_out_peek(fifo, buf, n)` | **偷看**数据。读取 n 个元素但不删除它们（不移动 out 指针）。 | 返回实际读取的个数。<br>常用于头部数据校验。 |
| | `kfifo_out_spinlocked(fifo, buf, n, lock)` | **带自旋锁**的出队操作。 | 适用于**多消费者**场景。 |
| **状态查询** | `kfifo_len(fifo)` | 获取当前已存储的数据量（元素个数）。 | - |
| | `kfifo_avail(fifo)` | 获取剩余可用空间（元素个数）。 | `size - len` |
| | `kfifo_is_empty(fifo)` | 判断是否为空。 | 返回布尔值。 |
| | `kfifo_is_full(fifo)` | 判断是否已满。 | 返回布尔值。 |
| | `kfifo_size(fifo)` | 获取 kfifo 的总容量。 | - |
| **重置** | `kfifo_reset(fifo)` | 重置 kfifo（清空数据）。 | 将 `in` 和 `out` 指针复位。<br>操作非原子，需自行保证并发安全。 |

#### 关键注意事项

1.  **大小限制 (Power of 2)**：
    *   kfifo 的 `size` **必须是 2 的幂** (2, 4, 8, 16, 1024 ...)。
    *   这是因为 kfifo 使用位运算 `(index & mask)` 替代取模运算 `(index % size)` 来提高效率并处理索引溢出回绕。
    *   使用 `kfifo_alloc` 时，如果传入 100，内核会自动帮你分配 128 字节的空间；但使用 `DEFINE_KFIFO` 或 `kfifo_init` 时，必须手动保证。

2.  **并发安全性 (Lockless vs Locking)**：
    *   **单生产者 + 单消费者 (SPSC)**：**不需要锁**。`kfifo_put` 和 `kfifo_get` 可以并发执行（依靠内存屏障保证一致性）。
    *   **多生产者 (MPSC)**：入队需要加锁（`kfifo_in_spinlocked` 或手动加锁）。
    *   **多消费者 (MCSC)**：出队需要加锁（`kfifo_out_spinlocked` 或手动加锁）。

3.  **数据拷贝**：
    *   `kfifo_in` 和 `kfifo_out` 内部发生了内存拷贝 (`memcpy`)。
    *   如果数据量特别大，或者需要零拷贝，kfifo 可能不是最佳选择（或者你需要使用 Scatter-Gather DMA 相关的 kfifo 高级用法）。