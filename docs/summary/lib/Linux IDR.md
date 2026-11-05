## IDR
Linux 内核中的 **IDR (Integer ID Management)** 机制是一个用于将**整数 ID** 映射到**指针**的数据结构。

简单来说，它的作用类似于一个**“稀疏数组”**：你可以给它一个对象指针，它会返还给你一个唯一的整数 ID（例如 1, 5, 100）；或者你给它一个整数 ID，它能极其快速地把对应的对象指针找出来。

它广泛用于内核中需要分配句柄（Handle）的场景，比如 POSIX 定时器 ID、I2C 总线号、DRM 驱动中的 GEM 句柄等。

---

### 1. 为什么需要 IDR？

在内核开发中，我们经常遇到这样的需求：
1.  **分配 ID**：需要为某个对象分配一个小的、唯一的整数 ID（比如设备号）。
2.  **查找**：稍后需要通过这个 ID 快速找到对应的对象结构体。

**常见方案的弊端：**
*   **数组**：如果 ID 很大（比如 ID 是 10000），用数组会浪费大量内存（前 9999 个位置都空着）。
*   **链表**：分配 ID 和查找的时间复杂度是 $O(N)$，随着对象增多，性能会急剧下降。

**IDR 的优势：**
*   **高效查找**：时间复杂度接近 $O(1)$ 或 $O(\log N)$。
*   **节省内存**：它是稀疏的，按需分配内存，不会因为 ID 数值大而预分配巨大的空间。
*   **自动扩容**：不需要预先定义最大容量。

---

### 2. 内部实现原理 (演进史)

了解 IDR 的实现有助于理解其性能特性。

*   **早期 (Linux < 4.20)**：IDR 内部基于 **Radix Tree (基数树)** 实现。它是一种多叉树结构，每一层利用 ID 的几位二进制来进行索引。
*   **现代 (Linux >= 4.20)**：IDR 的底层实现已经被 **XArray (eXtensible Arrays)** 取代。
    *   **XArray** 是 Linux 内核中新一代的通用数据结构，旨在统一 IDR 和 Radix Tree 的接口和实现。
    *   现在的 IDR 本质上就是对 XArray 的一层**封装 (Wrapper)**。这意味着 IDR 现在继承了 XArray 的所有优点：更好的缓存局部性、更简洁的代码、原生的 RCU 支持。

---

### 3. 核心 API 详解

IDR 的 API 定义在 `&lt;linux/idr.h&gt;` 中。

#### 3.1 定义与初始化

```c
// 静态定义
DEFINE_IDR(my_idr_name);

// 动态定义与初始化
struct idr my_idr;
idr_init(&my_idr);
```

#### 3.2 分配 ID (插入数据)

以前内核使用的是 `idr_get_new` 等复杂的两步操作（preload + alloc），现在推荐使用 **`idr_alloc`**，一步到位。

```c
int idr_alloc(struct idr *idr, void *ptr, int start, int end, gfp_t gfp_mask);
```
*   **ptr**: 要关联的结构体指针。
*   **start / end**: 希望分配的 ID 范围。
    *   `start` 是起始值（通常是 0 或 1）。
    *   `end` 是结束值（不包含）。如果传 0，表示最大正整数。
*   **gfp_mask**: 内存分配标志（如 `GFP_KERNEL`, `GFP_ATOMIC`）。
*   **返回值**: 成功返回分配到的 ID，失败返回错误码（如 `-ENOSPC`, `-ENOMEM`）。

**变种：循环分配 (`idr_alloc_cyclic`)**
```c
int idr_alloc_cyclic(struct idr *idr, void *ptr, int start, int end, gfp_t gfp_mask);
```
*   区别在于：普通 `alloc` 释放 ID 后，下次分配会优先复用最小的空闲 ID。
*   `cyclic` 会记住上次分配的位置，下次从那里继续往后找。这类似 PID 的分配策略（避免 ID 频繁复用导致的问题）。

#### 3.3 查找 (ID -> 指针)

```c
void *idr_find(struct idr *idr, int id);
```
*   返回 ID 对应的指针。如果 ID 不存在，返回 NULL。
*   **注意**：在 RCU 读侧临界区调用此函数是安全的，通常不需要加锁（依赖 RCU 机制）。

#### 3.4 删除

```c
void *idr_remove(struct idr *idr, int id);
```
*   从树中移除该 ID，并返回对应的指针。如果不存则返回 NULL。
*   注意：这只是从 IDR 中移除映射，**不会**释放指针指向的内存，你需要自己 `kfree`。

#### 3.5 遍历

```c
struct my_struct *entry;
int id;

// 类似于 list_for_each_entry
idr_for_each_entry(idr, entry, id) {
    // entry 是当前的指针，id 是当前的 ID
    // 可以在这里处理每个对象
}
```

#### 3.6 销毁

```c
void idr_destroy(struct idr *idr);
```
*   释放 IDR 内部占用的所有内存。
*   **注意**：它不会释放你存储的对象的内存！你通常需要在 destroy 之前遍历并释放所有对象。

---

### 4. IDR 与 IDA 的区别

在内核中，你经常会看到 IDR 和 **IDA (ID Allocator)** 并列。

*   **IDR**: **Map (ID -> Pointer)**。既分配 ID，又存储指针。
    *   *场景*：我有 `struct device *dev`，我想给它分配个 ID，以后通过 ID 找回这个 `dev`。
*   **IDA**: **Bitmap (ID only)**。只分配 ID，不存指针。
    *   *场景*：我只需要一个唯一的数字（比如 SCSI tag），但我不需要通过这个数字在 IDA 里找回什么东西（可能我自己有数组索引，或者不需要找回）。IDA 内部是用 Bitmap 实现的，比 IDR 更省内存。

---

### 5. 代码示例

下面是一个典型的内核模块使用 IDR 的代码片段：

```c
#include <linux/module.h>
#include <linux/idr.h>
#include <linux/slab.h>

// 1. 定义 IDR
static DEFINE_IDR(my_object_idr);
static DEFINE_MUTEX(my_idr_lock); // IDR 的写操作通常需要锁保护

struct my_object {
    int id;
    char name[32];
};

// 添加对象
static int add_object(void)
{
    struct my_object *obj;
    int id;

    obj = kzalloc(sizeof(*obj), GFP_KERNEL);
    if (!obj) return -ENOMEM;

    mutex_lock(&my_idr_lock);
    
    // 2. 分配 ID: 范围 0 ~ 0 (无限制)，使用 GFP_KERNEL
    id = idr_alloc(&my_object_idr, obj, 0, 0, GFP_KERNEL);
    
    if (id < 0) {
        mutex_unlock(&my_idr_lock);
        kfree(obj);
        return id;
    }
    
    obj->id = id;
    mutex_unlock(&my_idr_lock);

    pr_info("IDR: Allocated ID %d\n", id);
    return id;
}

// 查找对象
static void find_object(int id)
{
    struct my_object *obj;

    // 3. 查找: 读操作可以使用 RCU，无需互斥锁
    rcu_read_lock();
    obj = idr_find(&my_object_idr, id);
    if (obj) {
        pr_info("IDR: Found object at ID %d\n", id);
    } else {
        pr_info("IDR: Object %d not found\n", id);
    }
    rcu_read_unlock();
}

// 删除对象
static void remove_object(int id)
{
    struct my_object *obj;

    mutex_lock(&my_idr_lock);
    // 4. 移除映射
    obj = idr_remove(&my_object_idr, id);
    mutex_unlock(&my_idr_lock);

    if (obj) {
        kfree(obj); // 释放对象内存
        pr_info("IDR: Removed ID %d\n", id);
    }
}

// 模块卸载时的清理
static void clean_all_objects(void)
{
    struct my_object *obj;
    int id;

    mutex_lock(&my_idr_lock);
    // 5. 遍历并清理
    idr_for_each_entry(&my_object_idr, obj, id) {
        idr_remove(&my_object_idr, id);
        kfree(obj);
    }
    idr_destroy(&my_object_idr); // 6. 销毁 IDR
    mutex_unlock(&my_idr_lock);
}
```

### 6. 使用注意事项

1.  **写侧加锁**：`idr_alloc` 和 `idr_remove` 会修改内部树结构。虽然 XArray 底层有一定的原子性，但为了业务逻辑的正确性（比如防止两个线程同时分配），**通常需要由调用者持有 Spinlock 或 Mutex**。
2.  **读侧 RCU**：`idr_find` 支持 RCU 安全查找。这意味着如果你只读，只需要 `rcu_read_lock()`，这使得查找速度非常快且无阻塞。
3.  **内存分配上下文**：`idr_alloc` 的 `gfp_mask` 参数很重要。如果你在持有 Spinlock 的情况下调用它，必须使用 `GFP_ATOMIC`；如果在进程上下文（持有 Mutex），则可以用 `GFP_KERNEL`。
4.  **替换**：IDR 还有一个 `idr_replace(idr, ptr, id)` 函数，用于替换某个 ID 对应的指针。这在需要原子更新对象的场景很有用。

### 总结

Linux 内核 IDR 模块是一个**基于 XArray 的整数到指针的映射机制**。它通过稀疏树结构解决了大范围 ID 分配和快速查找的问题，是内核中管理 Resource Handle（资源句柄）的标准工具。它的核心优点是**查找极快（支持 RCU）**且**内存占用随数据量动态调整**。

## APIs

以下是 Linux 内核 **IDR (Integer ID Management)** 模块主要 API 的详细汇总。

为了便于理解，我将 API 分为 **定义与初始化**、**分配与修改**、**查找与遍历**、**销毁** 四个类别。

### 1. 定义与初始化 (Definition & Initialization)

| API / 宏 | 功能描述 | 适用场景与备注 |
| :--- | :--- | :--- |
| **`DEFINE_IDR(name)`** | **静态定义并初始化**。<br>声明一个名为 `name` 的全局 `struct idr` 变量，并初始化它。 | 适用于全局变量。相当于 `struct idr name; idr_init(&name);`。 |
| **`idr_init(idr)`** | **动态初始化**。<br>初始化一个已存在的 `struct idr` 结构体。 | 适用于嵌入在其他结构体中或动态分配的 IDR。 |
| **`idr_is_empty(idr)`** | **判空**。<br>检查 IDR 中是否包含任何映射。 | 返回 `true` 表示空，`false` 表示非空。 |

---

### 2. 分配与修改 (Allocation & Modification)

**注意**：所有写操作（Alloc, Remove, Replace）通常都需要调用者持有适当的锁（如 Mutex 或 Spinlock）来保证并发安全。

| API / 宏 | 功能描述 | 参数详解 |
| :--- | :--- | :--- |
| **`idr_alloc(idr, ptr, start, end, gfp)`** | **分配整数 ID**。<br>在 `[start, end)` 范围内分配一个未使用的 ID，并将其映射到指针 `ptr`。 | **ptr**: 关联的对象指针。<br>**start**: 起始 ID。<br>**end**: 结束 ID（0 表示最大值 `INT_MAX`）。<br>**gfp**: 内存分配标志（如 `GFP_KERNEL`）。<br>**返回**: 成功返回 ID，失败返回错误码（如 `-ENOSPC`）。 |
| **`idr_alloc_cyclic(...)`** | **循环分配 ID**。<br>功能同上，但下次分配会从“上次分配 ID + 1”开始查找。 | 适用于需要 ID 循环使用的场景（如 PID 分配），防止 ID 短时间内重复使用。 |
| **`idr_alloc_u32(...)`** | **指定 ID 分配**。<br>尝试分配指定的 32 位 ID。如果该 ID 已被占用，则返回错误。 | 适用于用户指定 ID 的场景（如恢复某些状态时）。 |
| **`idr_remove(idr, id)`** | **删除 ID**。<br>移除 ID 与指针的映射。 | **返回**: 被移除的指针 `ptr`。如果 ID 不存在则返回 NULL。<br>**注意**: 不会释放 `ptr` 指向的内存。 |
| **`idr_replace(idr, ptr, id)`** | **替换指针**。<br>将现有的 `id` 重新映射到新的指针 `ptr`。 | **返回**: 旧的指针 `old_ptr`。如果 ID 不存在则返回错误指针（`ERR_PTR`）。 |

---

### 3. 查找与遍历 (Lookup & Iteration)

**注意**：IDR 的查找操作支持 RCU。如果在 RCU 读侧临界区内调用（即 `rcu_read_lock()` 包围），则不需要额外的互斥锁。

| API / 宏 | 功能描述 | 关键点 |
| :--- | :--- | :--- |
| **`idr_find(idr, id)`** | **查找指针**。<br>根据 ID 返回对应的指针。 | 如果 ID 未分配或不存在，返回 NULL。<br>**RCU 安全**。 |
| **`idr_for_each_entry(idr, entry, id)`** | **遍历所有条目**。<br>类似于 `list_for_each_entry` 的宏。 | **entry**: 每次循环时指向的对象。<br>**id**: 每次循环时的 ID 变量。<br>**注意**: 如果在遍历中删除节点，需小心并发问题。 |
| **`idr_for_each_entry_continue(...)`** | **断点续遍历**。<br>从当前 `id` 的下一个位置继续遍历。 | 适用于分批次处理或遍历过程被打断的场景。 |
| **`idr_get_next(idr, &nextid)`** | **查找下一个已用 ID**。<br>查找 `>= nextid` 的最小已分配 ID，并更新 `nextid`。 | 底层遍历函数，通常用于实现迭代器。返回找到的指针，更新 `nextid` 为该 ID。 |
| **`idr_for_each(idr, fn, data)`** | **回调遍历**。<br>对每个条目调用回调函数 `fn(id, ptr, data)`。 | 较老式的接口，现在更推荐使用 `idr_for_each_entry` 宏。 |

---

### 4. 销毁 (Destruction)

| API / 宏 | 功能描述 | 注意事项 |
| :--- | :--- | :--- |
| **`idr_destroy(idr)`** | **销毁 IDR**。<br>释放 IDR 内部维护树结构所占用的所有内存。 | **极其重要**：此函数**只释放树结构**（节点内存），**不会**释放你存储在里面的对象（`ptr`）。<br>通常必须先遍历 IDR，手动 `kfree` 所有对象，最后再调用 `idr_destroy`。 |

---

### 5. 快速使用示例

为了方便理解，这里提供一个最常用的 API 组合模式：

| 动作 | 代码模式 | 锁的要求 |
| :--- | :--- | :--- |
| **创建** | `DEFINE_IDR(my_idr);` | 无 |
| **添加** | `id = idr_alloc(&my_idr, ptr, 0, 0, GFP_KERNEL);` | **需要** (Mutex/Spinlock) |
| **查找** | `rcu_read_lock();`<br>`p = idr_find(&my_idr, id);`<br>`rcu_read_unlock();` | **RCU** (无需互斥锁) |
| **遍历** | `idr_for_each_entry(&my_idr, ptr, id) { ... }` | 视情况而定 (通常需锁) |
| **删除** | `ptr = idr_remove(&my_idr, id);` | **需要** (Mutex/Spinlock) |
| **销毁** | `idr_for_each_entry(...) { kfree(ptr); }`<br>`idr_destroy(&my_idr);` | **需要** (Mutex/Spinlock) |
