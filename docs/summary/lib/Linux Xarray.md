## Xarray
Linux 内核中的 **XArray (eXtensible Array)** 是一个相对较新（在 Linux 4.20 引入，2018年）且极其重要的数据结构。

它由资深内核开发者 Matthew Wilcox 设计，**旨在彻底取代旧的 `Radix Tree`（基数树）和 `IDR`（ID分配器）**。

一句话概括：**XArray 是一个可以自动调整大小、支持稀疏索引、内存高效、并发友好的“数组”，它将 `unsigned long` 索引映射到 `void *` 指针。**

---

### 1. 为什么需要 XArray？ (背景与痛点)

在 XArray 出现之前，内核主要使用 `Radix Tree` 来管理页缓存（Page Cache）等映射关系。但 Radix Tree 有几个明显的痛点：

1.  **API 难用且易错**：Radix Tree 的 API 需要用户自己处理复杂的预加载（Preloading）逻辑来防止内存分配死锁，且锁定机制不由数据结构本身管理。
2.  **锁定机制混乱**：使用者必须清楚何时加锁、加什么锁。
3.  **不支持存储数值**：Radix Tree 主要存指针，想存整数（如 PID）得用 IDR，而 IDR 又是基于 Radix Tree 封装的，导致代码重复和性能开销。

**XArray 的改进：**
*   **内置锁机制**：XArray 默认处理了自旋锁（Spinlock）和 RCU（读复制更新），普通用户无需操心。
*   **内存分配自动处理**：彻底消除了 `radix_tree_preload` 的噩梦，内部自动处理 GFP 标志和重试逻辑。
*   **指针与数值通用**：既可以存 `struct page *`，也可以直接存 `int` 值（异常条目）。

---

### 2. 核心特性

1.  **稀疏数组 (Sparse Array)**：
    *   你可以索引 `0`，然后直接索引 `1000000`。中间的空位不占用内存。
    *   底层依然是多叉树（基数树），但在 API 层面表现得像一个巨大的数组。

2.  **索引范围**：
    *   索引是 `unsigned long`，在 64 位系统上范围是 $0$ 到 $2^{64}-1$。

3.  **并发安全**：
    *   **读操作**：使用 `rcu_read_lock`，是无锁的（Lock-less）。
    *   **写操作**：XArray 内部持有一把自旋锁（`xa_lock`），保证原子性。

4.  **标记功能 (Marks)**：
    *   类似于 Radix Tree 的 Tag。可以给某个索引打上标签（如 `XA_MARK_0`）。
    *   典型应用：Page Cache 中标记某个页是“脏页（Dirty）”或“正在回写（Writeback）”。

---

### 3. 内部原理 (简述)

虽然 API 看起来像数组，但 XArray **底层本质上还是一个 Radix Tree**。

*   **分层结构**：它由多层节点组成（Chunk），每层通常有 64 个槽位（Slots，即 $2^6$）。
*   **指针压缩**：XArray 利用指针的最后几位（Alignment bits）来存储状态。
    *   如果是正常的内核指针，最后两通常位是 00。
    *   如果最后一位是 1，表示这是一个**内部节点**或**数值**（Value Entry），而不是指向数据的指针。
*   **锁的优化**：XArray 并没有在每个节点里放一把锁，而是巧妙地利用了根节点指针的最低位作为“锁位”，极其节省内存。

---

### 4. 常用 API 详解

XArray 的 API 分为两类：**Normal API**（简单，自动加锁）和 **Advanced API**（复杂，需手动加锁）。

这里主要介绍最常用的 **Normal API**。

#### A. 定义与初始化

```c
#include <linux/xarray.h>

// 1. 静态定义 (最常用)
DEFINE_XARRAY(my_array);

// 2. 动态定义
struct xarray my_array;
xa_init(&my_array);

// 3. 带标志的初始化 (例如：分配 ID 时使用)
// XA_FLAGS_ALLOC 表示这就当作 IDR 用，自动寻找空闲位
DEFINE_XARRAY_FLAGS(my_requests, XA_FLAGS_ALLOC);
```

#### B. 存储 (Store)

将指针存入指定索引。

```c
struct my_struct *ptr = ...;
unsigned long index = 100;

// 返回值：如果该位置原来有值，返回旧值；否则返回 NULL。
// 错误处理：如果内存不足，返回 ERR_PTR(-ENOMEM)。
void *old = xa_store(&my_array, index, ptr, GFP_KERNEL);

if (xa_is_err(old)) {
    printk("Store failed\n");
}
```

#### C. 读取 (Load)

```c
// 简单读取
struct my_struct *p = xa_load(&my_array, 100);
if (p) {
    // 找到了
}
```

#### D. 遍历 (Iteration) —— XArray 的杀手锏

XArray 的遍历非常简单且高效，使用 `xa_for_each` 宏。

```c
unsigned long index;
struct my_struct *entry;

// 遍历整个数组
xa_for_each(&my_array, index, entry) {
    printk("Index: %lu, Data: %p\n", index, entry);
}
```

#### E. 分配 ID (替代 IDR)

如果你不关心索引是多少，只想要一个空闲的 ID：

```c
u32 id;
// 自动在 range 范围内找一个空闲位置，将 ptr 存进去，把 ID 写回 &id
int ret = xa_alloc(&my_array, &id, ptr, xa_limit_32b, GFP_KERNEL);
```

#### F. 销毁

```c
// 销毁数组结构，释放 XArray 内部生成的节点内存
// 注意：它不会 kfree 你存进去的 ptr，你需要自己先遍历释放 ptr
xa_destroy(&my_array);
```

---

### 5. 存储数值 (Value Entries)

有时候你不想存指针，只想存一个整数（比如状态码）。XArray 允许直接存整数，但范围有限制（通常是 `LONG_MAX >> 1`）。

```c
// 存整数
xa_store(&my_array, 5, xa_mk_value(123), GFP_KERNEL);

// 取整数
void *entry = xa_load(&my_array, 5);
if (xa_is_value(entry)) {
    int val = xa_to_value(entry); // val == 123
}
```

---

### 6. 实战代码示例

下面是一个完整的内核模块代码，演示了 XArray 的增删查改和遍历。

```c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/slab.h>
#include <linux/xarray.h>

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("XArray Test Module");

/* 定义一个全局 XArray */
static DEFINE_XARRAY(test_array);

struct my_data {
    int id;
    char name[32];
};

static int __init xarray_test_init(void)
{
    struct my_data *d1, *d2, *entry;
    unsigned long index;
    void *ret;

    pr_info("=== XArray Test Start ===\n");

    /* 1. 准备数据 */
    d1 = kmalloc(sizeof(*d1), GFP_KERNEL);
    d1->id = 100;
    snprintf(d1->name, 32, "Device-100");

    d2 = kmalloc(sizeof(*d2), GFP_KERNEL);
    d2->id = 20000; // 稀疏索引
    snprintf(d2->name, 32, "Device-20000");

    /* 2. 存储数据 (xa_store) */
    // 存入索引 100
    ret = xa_store(&test_array, 100, d1, GFP_KERNEL);
    if (xa_is_err(ret)) {
        pr_err("Failed to store d1\n");
        kfree(d1);
        kfree(d2); // 清理
        return PTR_ERR(ret);
    }

    // 存入索引 20000
    ret = xa_store(&test_array, 20000, d2, GFP_KERNEL);
    if (xa_is_err(ret)) {
        pr_err("Failed to store d2\n");
        // d1 已经在 array 里了，最后 destroy 会处理内部节点，但我们需要释放 d1 的数据吗？
        // 通常出错处理比较麻烦，这里简单处理
        return PTR_ERR(ret);
    }

    pr_info("Stored items at index 100 and 20000\n");

    /* 3. 单个查询 (xa_load) */
    entry = xa_load(&test_array, 100);
    if (entry)
        pr_info("Found index 100: %s\n", entry->name);
    else
        pr_err("Index 100 not found!\n");

    /* 4. 覆盖测试 */
    // xa_store 返回旧指针
    ret = xa_store(&test_array, 100, NULL, GFP_KERNEL); // 存 NULL 等于删除
    if (ret == d1) {
        pr_info("Successfully removed index 100 by storing NULL. Old ptr: %p\n", ret);
        // 这里 ret 就是 d1，我们手动重新存回去，为了后面的遍历测试
        xa_store(&test_array, 100, d1, GFP_KERNEL);
    }

    /* 5. 遍历 (xa_for_each) */
    pr_info("Iterating XArray:\n");
    xa_for_each(&test_array, index, entry) {
        pr_info("  Index %lu -> Name: %s\n", index, entry->name);
    }

    /* 6. 高级功能：标记 (Marks) */
    // 给索引 100 打上标记 0 (XA_MARK_0)
    xa_set_mark(&test_array, 100, XA_MARK_0);
    if (xa_get_mark(&test_array, 100, XA_MARK_0))
        pr_info("Index 100 is marked with XA_MARK_0\n");

    /* 7. 只遍历打了标记的条目 */
    pr_info("Iterating marked items:\n");
    xa_for_each_marked(&test_array, index, entry, XA_MARK_0) {
        pr_info("  Marked Index %lu -> %s\n", index, entry->name);
    }

    return 0;
}

static void __exit xarray_test_exit(void)
{
    struct my_data *entry;
    unsigned long index;

    /* 
     * 清理资源：
     * xa_destroy 只释放 XArray 自己的树节点内存。
     * 它不会 kfree 我们的 struct my_data。
     * 所以必须先遍历释放数据，再 destroy。
     */
    xa_for_each(&test_array, index, entry) {
        pr_info("Freeing node at %lu\n", index);
        kfree(entry);
    }

    xa_destroy(&test_array);
    pr_info("=== XArray Test End ===\n");
}

module_init(xarray_test_init);
module_exit(xarray_test_exit);
```

### 总结

| 特性 | Radix Tree (旧) | XArray (新) |
| :--- | :--- | :--- |
| **API 易用性** | 困难 (需预加载、手动锁) | **简单** (自动分配、内置锁) |
| **类型安全** | `void *` | `void *` 但有包装器辅助 |
| **锁定** | 外部管理 | **内部管理 (spinlock/RCU)** |
| **ID 分配** | 需要配合 IDR 模块 | **原生支持 (xa_alloc)** |
| **主要应用** | 历史遗留代码 | **新驱动、文件系统、Page Cache** |

如果你在开发新的 Linux 内核驱动，并且需要一个**哈希表**或者**数组**来根据 ID 存取对象，**请毫不犹豫地选择 XArray**。

## APIs
以下为您详细汇总了 Linux 内核 XArray 模块的主要 API。这些 API 定义在 `&lt;linux/xarray.h&gt;` 中。

为了方便查阅，我将 API 分为 **初始化**、**读写操作**、**ID 分配**、**遍历**、**标记** 和 **辅助工具** 六大类。

### 1. 初始化与定义 (Initialization)

在使用 XArray 之前，必须先定义并初始化。

| API / 宏               | 参数简述            | 功能描述                               | 备注                                                                   |
| :-------------------- | :-------------- | :--------------------------------- | :------------------------------------------------------------------- |
| `DEFINE_XARRAY`       | `(name)`        | **静态定义**并初始化一个 XArray。             | 最常用的方式，自带 `XA_FLAGS_LOCK_IRQ` 标志。                                    |
| `DEFINE_XARRAY_FLAGS` | `(name, flags)` | **静态定义**并指定标志（如 `XA_FLAGS_ALLOC`）。 | 用于需要当作 IDR（ID分配器）使用的场景。                                              |
| `xa_init`             | `(xa)`          | **动态初始化**。                         | 用于在结构体中嵌入的 `struct xarray` 成员。                                       |
| `xa_init_flags`       | `(xa, flags)`   | **动态初始化**并指定标志。                    | 常用标志：`XA_FLAGS_ALLOC` (支持 `xa_alloc`)、`XA_FLAGS_LOCK_IRQ` (锁操作禁用中断)。 |
| `xa_destroy`          | `(xa)`          | **销毁** XArray。                     | 释放 XArray 内部管理的节点内存。**注意：** 不会释放用户存储在里面的数据指针，需用户先遍历释放。               |

---

### 2. 基础读写操作 (Normal API)

这是最常用的增删改查接口，函数内部自动处理了自旋锁（Spinlock）和内存分配。

| API 函数 | 参数简述 | 功能描述 | 返回值 / 注意事项 |
| :--- | :--- | :--- | :--- |
| `xa_store` | `(xa, index, entry, gfp)` | **存储/修改**。<br>在指定 `index` 处存储 `entry` 指针。如果该位置已有数据，则覆盖。 | 返回**旧值** (Old Value)。<br>如果内存不足，返回 `ERR_PTR(-ENOMEM)`。 |
| `xa_insert` | `(xa, index, entry, gfp)` | **插入**。<br>仅当该 `index` **为空**时才存储。 | 成功返回 0；<br>如果已存在数据，返回 `-EEXIST`。 |
| `xa_load` | `(xa, index)` | **读取**。<br>获取指定 `index` 处的指针。 | 返回存储的指针；<br>如果为空，返回 `NULL`。内部使用 RCU 读锁。 |
| `xa_erase` | `(xa, index)` | **删除**。<br>移除指定 `index` 处的条目。 | 等价于 `xa_store(..., NULL, ...)`。<br>返回被删除的旧指针。 |
| `xa_cmpxchg` | `(xa, index, old, new, gfp)` | **比较并交换**。<br>仅当当前值为 `old` 时，才将其更新为 `new`。 | 返回**操作前**的值。<br>如果返回值等于 `old`，说明替换成功。 |

---

### 3. ID 分配 (Allocation / IDR Replacement)

这组 API 用于替代旧的 `IDR` 机制，自动寻找空闲的索引（ID）。**前提：** 初始化时必须设置 `XA_FLAGS_ALLOC` 标志。

| API 函数 | 参数简述 | 功能描述 | 返回值 |
| :--- | :--- | :--- | :--- |
| `xa_alloc` | `(xa, &id, entry, limit, gfp)` | **分配 ID**。<br>在 `limit` 指定的范围内找一个空闲位置存入 `entry`，并将位置写入 `id`。 | 成功返回 0；<br>无空闲位返回 `-ENOSPC`；<br>内存不足返回 `-ENOMEM`。 |
| `xa_alloc_cyclic` | `(xa, &id, entry, limit, *next, gfp)` | **循环分配 ID**。<br>从 `*next` 记录的位置开始查找空闲位，找到末尾后回绕到开头。 | 适用于 ID 复用的场景（如 PID 分配）。 |
| `xa_limit_32b` | (变量) | **预定义限制**。<br>表示范围 `0` 到 `UINT_MAX`。 | 常用作 `xa_alloc` 的 `limit` 参数。 |

---

### 4. 遍历与搜索 (Iteration)

XArray 的遍历非常高效，底层利用了基数树的跳跃能力。

| API / 宏 | 参数简述 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| `xa_for_each` | `(xa, index, entry)` | **遍历整个数组**。<br>这是个宏，类似于 `for` 循环。 | 自动处理稀疏空洞，只遍历有值的条目。<br>`index` 会自动更新。 |
| `xa_for_each_marked` | `(xa, index, entry, mark)` | **遍历标记项**。<br>只遍历被打上特定标记（Mark）的条目。 | 极其高效，跳过未标记的区域。常用于 Page Cache 查找脏页。 |
| `xa_find` | `(xa, &index, max, mark)` | **查找**。<br>寻找 `>= index` 且 `<= max` 的下一个条目。 | `xa_for_each` 底层调用的就是它。 |
| `xa_find_after` | `(xa, &index, max, mark)` | **查找下一个**。<br>寻找 `> index` 的下一个条目。 | 用于手动控制遍历逻辑。 |

---

### 5. 标记功能 (Marks)

类似于文件系统中标记“脏页”。XArray 支持给每个条目打上 3 种标记：`XA_MARK_0`, `XA_MARK_1`, `XA_MARK_2`。

| API 函数 | 参数简述 | 功能描述 | 场景举例 |
| :--- | :--- | :--- | :--- |
| `xa_set_mark` | `(xa, index, mark)` | **设置标记**。 | 标记某索引为 "Dirty"。 |
| `xa_clear_mark` | `(xa, index, mark)` | **清除标记**。 | 写入磁盘后清除 "Dirty" 标记。 |
| `xa_get_mark` | `(xa, index, mark)` | **查询标记**。<br>返回 `true` 或 `false`。 | 检查某索引是否处于 "Writeback" 状态。 |

---

### 6. 特殊值与错误处理 (Advanced)

XArray 允许直接存储整数（称为 Value Entry），也有一套处理返回值的机制。

| API / 宏 | 描述 | 示例 / 用途 |
| :--- | :--- | :--- |
| `xa_mk_value(val)` | 将整数转换为可存储的 XArray 条目。 | `xa_store(&xa, 0, xa_mk_value(123), GFP_KERNEL);` |
| `xa_to_value(entry)` | 将取出的条目还原为整数。 | `int val = xa_to_value(entry);` |
| `xa_is_value(entry)` | 判断取出的条目是否是整数。 | 防止把整数当指针解引用。 |
| `xa_is_err(entry)` | 判断操作是否出错。 | `if (xa_is_err(ret)) return PTR_ERR(ret);` |
| `ERR_PTR(-ENOMEM)` | 典型的错误返回。 | 当 `xa_store` 无法分配内存节点时返回。 |

### 快速参考：你应该用哪个？

1.  **我要存一个指针到数组里，索引我是知道的：**
    *   用 `xa_store(xa, index, ptr, GFP_KERNEL)`.
2.  **我要存一个指针，索引是多少无所谓，给我找个空位就行：**
    *   初始化用 `DEFINE_XARRAY_FLAGS(name, XA_FLAGS_ALLOC)`.
    *   分配用 `xa_alloc(xa, &id, ptr, xa_limit_32b, GFP_KERNEL)`.
3.  **我要从数组里根据索引拿数据：**
    *   用 `xa_load(xa, index)`.
4.  **我要遍历所有数据：**
    *   用 `xa_for_each(xa, index, entry) { ... }`.
5.  **我想存个状态码（整数），不想分配内存存指针：**
    *   用 `xa_store(xa, index, xa_mk_value(status), GFP_KERNEL)`.