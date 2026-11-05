## radix-tree

Linux 内核中的 `radix-tree`（基数树）是一个非常基础且关键的数据结构。虽然在最新的内核版本（Linux 4.20+ 及以后）中，它的 API 逐渐被更高级的 **XArray** 所取代，且部分实现被 **Maple Tree** 优化，但 `radix-tree` 的核心设计思想依然支撑着内核的内存管理、文件缓存等核心子系统。

以下是对 Linux 内核 `radix-tree` 的系统性介绍。

---

### 1. 什么是 Radix Tree？

**核心定义**：
Linux 的 `radix-tree` 是一种**将长整型（Long Integer）键值映射到指针（Pointer）**的高效机制。

**本质**：
它是一种**压缩的字典树（Trie）**。
*   它不像红黑树（Red-Black Tree）那样通过比较键值的大小来平衡节点。
*   它是通过**键值（Key/Index）的二进制位**来导航的。

**生活类比**：
想象你要去图书馆找一本书，书的编号是 `123-456`。
1.  你先去第 `1` 层楼。
2.  找到第 `2` 号房间。
3.  走到第 `3` 排架子...
这种不需要“比较书名大小”，而是根据“编号的每一位”直接定位位置的方法，就是 Radix Tree 的逻辑。

---

### 2. 核心应用场景

`radix-tree` 在内核中主要解决**“稀疏数组”**的问题。最著名的应用是 **Page Cache（页缓存）**。

*   **场景**：一个文件可能很大（几 TB），包含数亿个 4KB 的页。但此时此刻，内存里可能只缓存了该文件的第 0 页、第 10000 页和第 500000 页。
*   **需求**：
    1.  如果用普通数组：需要开一个巨大的数组，浪费内存。
    2.  如果用链表：查找第 500000 页需要遍历太久。
    3.  **Radix Tree**：只为存在的页分配节点，查找速度极快（几乎是 O(1)），且支持快速找到“脏页”。

---

### 3. 内部数据结构与设计

#### 3.1 基本结构
Radix Tree 由 **Root（根）**、**Node（节点）** 和 **Slot（槽位）** 组成。

*   **Radix（基数）/ Width**：
    Linux 内核通常设定每个节点有 **64 个槽位**（`2^6`）。
    这意味着，树的每一层可以处理 Key 的 **6 个比特位**。

*   **Struct 定义**（简化版）：
    ```c
    struct radix_tree_node {
        unsigned char   shift;      /* 当前节点的偏移量（层高相关） */
        unsigned char   offset;     /* 在父节点中的槽位索引 */
        unsigned int    count;      /* 已使用的槽位数量 */
        void           *slots[64];  /* 64个指针，指向下一层节点或最终的数据 */
        unsigned long   tags[3][2]; /* 标签位图（核心特性，下文详解） */
        struct rcu_head rcu_head;   /* 用于 RCU 释放 */
    };
    ```

#### 3.2 树的高度与动态增长
*   **高度 0**：不需要节点，Root 直接指向数据（如果是极小的 Index）。
*   **高度 1**：1 个节点，能存 $64$ 个数据 (Index 0~63)。
*   **高度 2**：根节点指向 64 个子节点，能存 $64 \times 64 = 4096$ 个数据。
*   **动态调整**：当插入一个很大的 Index（比如 Index 1,000,000）时，树会自动增加高度（加盖楼层），直到能覆盖这个 Index。

---

### 4. 杀手级特性：Tags（多色标记）

这是 Linux `radix-tree` 与普通计算机科学教科书里的 Radix Tree 最大的区别，也是它在 OS 中不可替代的原因。

**问题**：
文件系统中，操作系统需要定期把**“脏页（Dirty Pages）”**写回磁盘。如果文件缓存里有 100万个页，但只有 3 个是脏的，怎么快速找到这 3 个页？遍历 100万次吗？太慢了。

**解决方案（Tags）**：
Radix Tree 在每个中间节点（Node）维护了**Tag 位图**（通常支持 3 种 tag：`DIRTY`, `WRITEBACK`, `TOWRITE`）。

*   **逻辑**：
    *   如果**叶子节点**（实际的页）被标记为“脏”。
    *   它的**父节点**对应的槽位也会被标记为“脏”。
    *   它的**爷爷节点**也会被标记... 直到**根节点**。

*   **效果**：
    查找脏页时，从根节点出发。如果根节点的第 5 个槽位没标记“脏”，那整个第 5 分支下的几万个页都不用看了。
    这使得**查找稀疏的特定状态数据**极其高效。

---

### 5. 核心操作逻辑

#### 5.1 查找 (Lookup)
假设要找 Index `0x1234` (二进制 `... 010010 001101 000100`)。
1.  **Level 1**: 取高 6 位，作为数组下标找到下一层节点。
2.  **Level 2**: 取中间 6 位，下标找到再下一层。
3.  **Level 3**: 取低 6 位，找到最终的数据指针。

#### 5.2 插入 (Insert)
1.  检查树的高度是否足够覆盖 Index。如果不够，**增加树高**（Prepend new root）。
2.  按照路径向下走，如果中间节点不存在，则 `malloc`（从 slab 分配）新节点。
3.  将数据指针放入叶子节点的槽位。

#### 5.3 删除 (Delete)
1.  找到槽位，清空指针。
2.  `node->count` 减 1。
3.  如果 `count` 变为 0，说明该节点空了，**释放该节点**。
4.  如果根节点只剩下一个子节点（且在位置0），**降低树高**（Shrink）。

---

### 6. 并发控制：RCU 与 Lockless Lookup

在操作系统中，读（Lookup）操作远多于写（Insert/Delete）。为了极致性能，Radix Tree 支持 **RCU (Read-Copy-Update)**。

*   **无锁读取**：
    `radix_tree_lookup` 可以在持有 `rcu_read_lock()` 的情况下运行，**不需要任何互斥锁（Mutex/Spinlock）**。
    这意味着在多核 CPU 上，多个进程可以同时查找 Page Cache，互不干扰，性能极高。

*   **延迟释放**：
    当节点被删除时，它不会立即被 `free`，而是通过 `call_rcu` 等待所有当前的读操作结束后才释放内存。

---

### 7. 现代演进：XArray 与 Maple Tree

如果你在看 2025 年的内核代码，你会发现直接调用 `radix_tree_insert` 的代码变少了。这是因为内核引入了更高级的封装。

#### 7.1 XArray (eXtensible Array)
*   **背景**：`radix-tree` 的 API 比较难用（容易加锁错误，API 命名混乱）。Matthew Wilcox 引入了 XArray。
*   **关系**：XArray 底层最初就是 Radix Tree，但提供了一套**更加类型安全、更加易用的 API**（如 `xa_store`, `xa_load`）。
*   **现状**：现在内核开发推荐优先使用 XArray，而不是直接操作 Radix Tree。

#### 7.2 Maple Tree (红枫树)
*   **背景**：针对 **VMA (虚拟内存区域)** 的管理，Radix Tree 在处理“范围（Range）”问题上效率不够高（Radix Tree 擅长处理单个点）。
*   **现状**：在 Linux 6.1+ 中，VMA 管理从红黑树/Radix Tree 迁移到了 **Maple Tree**。Maple Tree 对范围查询进行了极致优化，且对 CPU 缓存更友好。

---

### 总结

**Linux Radix Tree 是：**
1.  一个**将 Index 映射到 指针**的稀疏数组。
2.  利用**Key 的比特位**进行分层索引（Trie）。
3.  利用**Tag 向上冒泡机制**实现 O(1) 复杂度的脏页/状态查找。
4.  利用**RCU** 实现无锁并发读取。

它是 Linux 内存管理（Page Cache）之所以能高效吞吐 GB 级数据的基石之一。虽然 API 外壳在变（XArray），但其核心的“分层索引”和“Tag 标记”思想依然是内核设计的精髓。

## APIs
以下是 Linux 内核 `radix-tree` 模块主要 API 的详细汇总。

**前置说明**：
*   头文件：`#include &lt;linux/radix-tree.h&gt;`
*   **现状提示**：虽然这些 API 仍然存在且大量使用，但在 Linux 4.20 以后，内核社区强烈建议新代码使用更现代的 **XArray** (`&lt;linux/xarray.h&gt;`) 接口，它是对 Radix Tree 的更高级封装。

---

### 1. 初始化与预分配 (Initialization & Preloading)

在使用 Radix Tree 之前，必须初始化树根（root），且在原子上下文（Atomic Context）操作前通常需要预分配内存。

| API 名称 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| `RADIX_TREE(name, mask)` | **静态定义并初始化**一个 radix tree 实例。 | `name`: 变量名<br>`mask`: `GFP_KERNEL` 等分配掩码。 |
| `INIT_RADIX_TREE(root, mask)` | **动态初始化**一个已存在的 `struct radix_tree_root` 结构体。 | 通常在模块的 `init` 函数中使用。 |
| `radix_tree_preload(gfp_mask)` | **预分配节点内存**。在持有自旋锁（spinlock）之前调用，以防止在锁内发生内存分配睡眠。 | 会将节点缓存到 Per-CPU 变量中。<br>**必须**与 `radix_tree_preload_end()` 配对使用。 |
| `radix_tree_preload_end()` | 结束预分配阶段，允许抢占。 | 必须在插入操作完成且释放锁之后调用。 |

---

### 2. 基础增删改查 (CRUD Operations)

这是最常用的操作集合。注意：**写操作必须加锁，读操作通常配合 RCU。**

| API 名称 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| `radix_tree_insert(root, index, item)` | **插入数据**。将指针 `item` 关联到索引 `index`。 | 成功返回 0，失败返回错误码（如 `-ENOMEM`, `-EEXIST`）。 |
| `radix_tree_lookup(root, index)` | **查找数据**。返回索引 `index` 对应的指针。 | 返回 `void *`。<br>如果是在 RCU 上下文，应使用 `rcu_read_lock()` 保护。 |
| `radix_tree_delete(root, index)` | **删除数据**。移除索引 `index` 处的条目。 | 返回被删除的 item 指针。<br>如果有 Tag，对应的 Tag 也会被清除。 |
| `radix_tree_delete_item(root, index, item)` | **安全删除**。仅当索引处的指针确实等于 `item` 时才删除。 | 比普通 delete 更安全，防止并发下的误删。 |
| `radix_tree_replace_slot(root, slot, item)` | **替换指针**。将指定 `slot` 中的旧指针替换为新指针 `item`。 | 原子操作，RCU 安全。<br>常用于无锁更新数据。 |
| `radix_tree_lookup_slot(root, index)` | **查找槽位地址**。返回指向存放数据的**指针的指针** (`void **`)。 | **高级用法**。主要用于需要直接操作 slot（如替换）的场景。<br>需配合 `radix_tree_deref_slot` 使用。 |

---

### 3. 标签操作 (Tag Operations)

这是 Radix Tree 最强大的功能，用于快速查找特定状态（如 Dirty, Writeback）的条目。
*通常支持 3 个 Tag：`PAGECACHE_TAG_DIRTY`, `PAGECACHE_TAG_WRITEBACK`, `PAGECACHE_TAG_TOWRITE`*。

| API 名称 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| `radix_tree_tag_set(root, index, tag)` | **设置标签**。将索引 `index` 处的条目打上 `tag` 标记。 | 该标记会沿着树路径**向上冒泡**直到根节点。 |
| `radix_tree_tag_clear(root, index, tag)` | **清除标签**。清除索引 `index` 处的 `tag` 标记。 | 如果该节点下的所有子节点都没了这个标记，会向上清除父节点的标记。 |
| `radix_tree_tag_get(root, index, tag)` | **查询标签**。检查索引 `index` 处是否设置了 `tag`。 | 返回 0 或 1。 |
| `radix_tree_tagged(root, tag)` | **全局查询**。检查整棵树中是否**至少有一个**条目被设置了 `tag`。 | O(1) 复杂度（直接检查根节点），极快。常用于判断是否需要启动回写线程。 |

---

### 4. 批量操作与迭代 (Gang Lookup / Iterators)

为了减少函数调用开销，通常一次查找多个条目。

| API 名称 | 功能描述 | 关键参数/备注 |
| :--- | :--- | :--- |
| `radix_tree_gang_lookup(root, results, first_index, max_items)` | **批量查找**。从 `first_index` 开始，连续查找最多 `max_items` 个条目填入 `results` 数组。 | 返回实际找到的条目数量。<br>常用于遍历。 |
| `radix_tree_gang_lookup_tag(root, results, first_index, max_items, tag)` | **批量查找带标签条目**。仅返回设置了 `tag` 的条目。 | **核心 API**。<br>Page Cache 的回写逻辑主要依赖此函数快速找到所有脏页。 |
| `radix_tree_for_each_slot(slot, root, iter, start)` | **宏：遍历槽位**。提供一个类似 `for` 循环的接口来遍历树。 | 需要配合 `struct radix_tree_iter` 使用。 |
| `radix_tree_for_each_tagged(slot, root, iter, start, tag)` | **宏：遍历带标签槽位**。高效跳过无标签的区域。 | 比普通的 gang_lookup 更现代化，直接在循环体内处理逻辑。 |

---

### 5. 补充：RCU 与并发注意事项

在使用上述 API 时，必须遵守 Linux 内核的并发规则：

| 操作类型 | 锁的要求 | 说明 |
| :--- | :--- | :--- |
| **读取 (Lookup/Tag Get)** | `rcu_read_lock()` | 不需要互斥锁，支持多核并发读。 |
| **修改 (Insert/Delete/Tag Set)** | `spinlock` (自旋锁) | 必须互斥。同时必须在写端使用 `radix_tree_replace_slot` 等 RCU 安全的原语来保证读端的正确性。 |
| **预分配 (Preload)** | 无 | 必须在获取 `spinlock` **之前**调用 `radix_tree_preload`，并在**之后**调用 `radix_tree_preload_end`。 |

### 示例代码结构

```c
/* 典型的写操作流程 */
rcu_read_lock(); // 如果只是读取
/* ... */
rcu_read_unlock();

/* 典型的写操作流程 */
radix_tree_preload(GFP_KERNEL); // 1. 预分配，防止死锁
spin_lock(&my_lock);            // 2. 加锁
if (radix_tree_insert(&my_root, index, item)) {
    // 处理错误
}
spin_unlock(&my_lock);          // 3. 解锁
radix_tree_preload_end();       // 4. 结束预分配
```