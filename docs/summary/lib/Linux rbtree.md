## rbtree
Linux 内核中的 `rbtree` (Red-Black Tree，红黑树) 是一个极其重要的数据结构模块。它实现了一种**自平衡二叉搜索树**。

与链表（Linked List）的 $O(n)$ 查找复杂度不同，红黑树保证了 **$O(\log n)$** 的查找、插入和删除操作性能。这使得它非常适合用于**频繁查找、排序敏感**的场景。

以下是关于 Linux 内核 `rbtree` 的详细介绍：

---

### 1. 核心概念与特点

*   **定义**：红黑树是一种二叉搜索树，它通过在节点上增加一个存储位表示节点的颜色（红或黑），并通过红黑规则（如根是黑的、红节点子节点必须是黑的等）来保持树的**大致平衡**。
*   **头文件**：`include/linux/rbtree.h`
*   **实现位置**：`lib/rbtree.c`
*   **核心优势**：
    1.  **速度快**：查找、插入、删除的时间复杂度都是对数级。即便有数万个节点，查找也只需要比较十几次。
    2.  **有序性**：树中的节点是天然排序的（中序遍历即为有序序列）。
    3.  **确定性**：最坏情况下的性能也非常稳定，不会像哈希表那样遇到冲突导致性能退化。

---

### 2. 数据结构设计

Linux 内核的 `rbtree` 采用了**侵入式（Intrusive）设计**，这意味着 `rb_node` 结构体是嵌入在你自己的结构体中的，而不是你把数据存到 `rb_node` 里。

#### 核心结构体

```c
// 树的根节点
struct rb_root {
    struct rb_node *rb_node;
};

// 树的每一个节点
struct rb_node {
    unsigned long  __rb_parent_color; // 压缩存储：同时存了父节点指针和自己的颜色
    struct rb_node *rb_right;
    struct rb_node *rb_left;
} __attribute__((aligned(sizeof(long))));
```

*   **`__rb_parent_color` 的黑魔法**：由于内存对齐的原因，指针的最后几位通常是 0。内核利用最低位来存储颜色（0=红, 1=黑），剩下的高位存储父节点的地址。这节省了内存空间。

#### 宿主结构体示例

假设你要写一个驱动来管理一堆学生的分数：

```c
struct student {
    int id;
    int score;
    struct rb_node node; // <--- 嵌入红黑树节点
};
```

---

### 3. 典型应用场景

了解谁在用它，就知道它有多重要：

1.  **CFS 调度器 (Completely Fair Scheduler)**：
    *   这是 `rbtree` 最著名的用户。
    *   内核用红黑树管理所有可运行的进程（task）。
    *   **Key**：进程的虚拟运行时间 (`vruntime`)。
    *   **逻辑**：树的最左侧节点 (`rb_first`) 永远是 `vruntime` 最小的进程，调度器总是选择它来运行。
2.  **VMA (Virtual Memory Area) 管理**：
    *   每个进程的 `mm_struct` 中都有一个红黑树，用于管理该进程所有的虚拟内存区域（如代码段、堆、栈）。
    *   这样发生缺页中断时，内核能通过地址快速找到对应的 VMA。（注：最新内核 v6.1+ 引入了 Maple Tree 逐步替代此场景，但 rbtree 依然是基础）。
3.  **高精度定时器 (Hrtimers)**：
    *   按到期时间排序，最早到期的在最左边。
4.  **文件系统 (ext4, btrfs)**：
    *   用于管理磁盘块分配、目录索引等。

---

### 4. API 与使用方法

Linux 的 `rbtree` API 设计得比较“底层”，它**没有**提供通用的 `insert` 和 `search` 函数。
**原因**：内核不知道你的结构体长什么样，也不知道你要按什么字段（Key）排序（是按 ID 排？还是按名字排？）。

因此，**查找逻辑和插入位置的定位逻辑，必须由用户自己写。**

#### A. 查找 (Search)
你需要自己写个 `while` 循环：

```c
struct student *my_search(struct rb_root *root, int id)
{
    struct rb_node *node = root->rb_node;

    while (node) {
        // 利用 container_of 获取宿主结构体
        struct student *data = container_of(node, struct student, node);

        if (id < data->id)
            node = node->rb_left;
        else if (id > data->id)
            node = node->rb_right;
        else
            return data; // 找到了
    }
    return NULL;
}
```

#### B. 插入 (Insert)
插入分两步：
1.  **手动查找插入位置**（像上面搜索一样）。
2.  **调用内核 API** 进行链接和重新平衡（变色、旋转）。

```c
int my_insert(struct rb_root *root, struct student *new_data)
{
    struct rb_node **link = &root->rb_node;
    struct rb_node *parent = NULL;

    // 1. 找到该放哪
    while (*link) {
        struct student *data;
        parent = *link;
        data = container_of(parent, struct student, node);

        if (new_data->id < data->id)
            link = &(*link)->rb_left;
        else if (new_data->id > data->id)
            link = &(*link)->rb_right;
        else
            return -1; // ID 重复
    }

    // 2. 链接并平衡
    rb_link_node(&new_data->node, parent, link); // 挂上去
    rb_insert_color(&new_data->node, root);      // 调整颜色和旋转
    return 0;
}
```

#### C. 删除 (Delete)
删除比较简单，内核帮你搞定复杂的旋转逻辑：

```c
rb_erase(&data->node, &root);
// 注意：rb_erase 只负责把节点从树上摘下来并保持树平衡。
// 释放 struct student 内存需要你自己 kfree(data)。
```

#### D. 遍历 (Iterate)
红黑树支持按顺序遍历（从小到大）：

```c
struct rb_node *node;
for (node = rb_first(&root); node; node = rb_next(node)) {
    struct student *data = container_of(node, struct student, node);
    printk("Student: %d\n", data->id);
}
```

---

### 5. 进阶特性：`rb_root_cached`

在 Linux 内核中，经常有一种需求：**我不仅要快速查找任意节点，我还特别频繁地需要访问“最小”的那个节点（最左节点）。**

*   **痛点**：标准的 `rb_first()` 虽然很快，但还是需要从 Root 往下走几步才能找到最左节点。如果是调度器这种极高频场景，这点开销也不想要。
*   **解决方案**：`struct rb_root_cached`。

```c
struct rb_root_cached {
    struct rb_root rb_root;
    struct rb_node *rb_leftmost; // 永远指向最小的节点
};
```

*   **API**：
    *   `rb_insert_color_cached`: 插入时顺便更新 `rb_leftmost`。
    *   `rb_erase_cached`: 删除时顺便更新。
    *   `rb_first_cached`: **$O(1)$** 直接获取最小节点。
*   **应用**：CFS 调度器就是用的这个，因为它永远需要获取 `vruntime` 最小的进程。

---

### 6. 进阶特性：Augmented Rbtrees (增强红黑树)

有时候我们需要在树节点里存一些**额外的聚合信息**，比如“以当前节点为根的子树中，最大的数值是多少”。

*   **头文件**：`&lt;linux/rbtree_augmented.h&gt;`
*   **用途**：主要用于**区间重叠查找**（Interval Tree）。
    *   例如：管理内存区间 `[start, end)`。当我们想找“有没有哪个区间包含了地址 0x1000”时，普通红黑树很难做，而增强红黑树可以通过维护“子树最大 end 值”来快速剪枝查找。

### 总结

`rbtree` 是 Linux 内核中处理**海量有序数据**的基石。

| 特性       | 描述                                 |
| :------- | :--------------------------------- |
| **复杂度**  | 增删查均为 $O(\log n)$                  |
| **内存开销** | 每个节点额外需要 24 字节 (64位系统, 3个指针大小)     |
| **排序**   | 自动排序（中序遍历有序）                       |
| **内核地位** | 核心数据结构之一，地位仅次于 List                |
| **使用难点** | 用户必须自己编写 `compare` 逻辑和 `search` 循环 |
| **主要用户** | 进程调度、内存管理、文件系统                     |
## APIs
以下为您详细汇总了 Linux 内核 `rbtree` (红黑树) 模块的主要 API。这些定义主要位于 `include/linux/rbtree.h`。

为了方便阅读，我将 API 分为 **基础结构**、**遍历与查找**、**修改（增删）** 以及 **带缓存的扩展（Cached）** 四个类别。

### 1. 基础结构定义与初始化

| API / 宏 | 描述 | 备注 |
| :--- | :--- | :--- |
| `struct rb_root` | 红黑树的**根节点**结构体。 | 仅包含一个指向 `rb_node` 的指针。 |
| `struct rb_node` | 红黑树的**节点**结构体。 | 必须**嵌入**在用户的宿主结构体中使用（侵入式）。 |
| `RB_ROOT` | **静态初始化**宏。 | `struct rb_root my_tree = RB_ROOT;` |
| `rb_entry(ptr, type, member)` | **获取宿主结构体指针**。 | 本质就是 `container_of`，用于从 `rb_node*` 反推回用户数据的结构体指针。 |

---

### 2. 遍历与查找 (Traversal & Lookup)

内核**不提供**通用的 `rb_search` 函数，用户必须自己编写 `while` 循环进行比较查找。但内核提供了遍历树的工具。

| API 函数 | 描述 | 时间复杂度 |
| :--- | :--- | :--- |
| `rb_first(root)` | 获取树中**最小**（最左侧）的节点。 | $O(\log n)$ |
| `rb_last(root)` | 获取树中**最大**（最右侧）的节点。 | $O(\log n)$ |
| `rb_next(node)` | 获取当前节点的**后继**节点（比当前大的下一个节点）。 | 均摊 $O(1)$ |
| `rb_prev(node)` | 获取当前节点的**前驱**节点（比当前小的上一个节点）。 | 均摊 $O(1)$ |
| `rbtree_postorder_for_each_entry_safe(...)` | **后序遍历**宏。通常用于**销毁**整棵树时释放内存（因为后序遍历先访问子节点再访问父节点，删除时是安全的）。 | $O(n)$ |

---

### 3. 修改操作 (Modification)

插入操作分为两步：先由用户代码确定位置并建立链接（Link），再由内核代码进行颜色调整和旋转（Rebalance）。

| API 函数 | 参数简述 | 描述 | 关键点 |
| :--- | :--- | :--- | :--- |
| `rb_link_node` | `(node, parent, rb_link)` | **第一步：物理链接**。<br>将新节点挂载到父节点的指定子节点指针位置。 | **不进行平衡**。仅仅是设置指针，把节点挂上去。 |
| `rb_insert_color` | `(node, root)` | **第二步：平衡调整**。<br>执行红黑树算法（变色、旋转），恢复树的平衡属性。 | **必须**在 `rb_link_node` 之后调用。 |
| `rb_erase` | `(node, root)` | **删除节点**。<br>将节点从树中移除，并重新平衡树。 | 仅从树中移除，**不释放**节点内存（用户需自行 `kfree`）。 |
| `rb_replace_node` | `(victim, new, root)` | **替换节点**。<br>用 `new` 节点替换树中的 `victim` 节点，保持原有的颜色和位置关系。 | 用于快速替换，不需要重新平衡。 |

---

### 4. 带缓存的优化版本 (Cached Rbtree)

**场景**：当频繁需要获取树中**最小节点**（如 CPU 调度器获取 `vruntime` 最小的进程）时使用。它在 `rb_root` 基础上额外维护了一个指向最左节点的指针，使获取最小节点变为 $O(1)$。

| API 函数 | 描述 | 相比普通版的区别 |
| :--- | :--- | :--- |
| `struct rb_root_cached` | 带缓存的根节点结构体。包含 `rb_root` 和 `rb_leftmost` 指针。 | 比 `struct rb_root` 多一个指针。 |
| `rb_first_cached(root)` | **获取最小节点**。 | **$O(1)$** (普通版是 $O(\log n)$)。 |
| `rb_insert_color_cached` | **插入并平衡**。 | 插入时会自动检测新节点是否是新的最小值，并更新缓存。 |
| `rb_erase_cached` | **删除并平衡**。 | 删除时如果删的是最小值，会自动更新缓存指向下一个最小值。 |

---

### 5. 增强版 (Augmented Rbtree) - 简要

用于需要在节点中维护**子树聚合信息**（如子树中最大的区间结束值）的场景，主要用于区间树（Interval Tree）。

| API 函数 | 描述 |
| :--- | :--- |
| `rb_insert_augmented` | 替代 `rb_insert_color`。在旋转过程中回调用户提供的函数来更新聚合信息。 |
| `rb_erase_augmented` | 替代 `rb_erase`。删除时回调用户函数更新聚合信息。 |

### 总结：标准插入流程代码模版

这是你在写驱动时最常写的代码片段：

```c
int my_insert(struct rb_root *root, struct my_data *new)
{
    struct rb_node **link = &root->rb_node;
    struct rb_node *parent = NULL;
    struct my_data *entry;

    // 1. 手动查找插入位置 (Search Loop)
    while (*link) {
        parent = *link;
        entry = rb_entry(parent, struct my_data, node);
        
        if (new->val < entry->val)
            link = &(*link)->rb_left;
        else if (new->val > entry->val)
            link = &(*link)->rb_right;
        else
            return -1; // 存在相同 Key
    }

    // 2. 链接节点 (Link)
    rb_link_node(&new->node, parent, link);

    // 3. 调整平衡 (Rebalance)
    rb_insert_color(&new->node, root);

    return 0;
}
```