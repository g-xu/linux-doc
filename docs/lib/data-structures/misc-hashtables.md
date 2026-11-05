# 哈希表类数据结构（合并文档）

> hashtable / assoc_array 两个模块的简明参考。每个模块 20-50 行，覆盖定位、核心 API、使用注意。

---

## 1. hashtable — 静态大小通用哈希表

**头文件**：`include/linux/hashtable.h`（209 行）；**实现**：纯头文件，无 .c
**使用量**：约 1,393 处 API 调用，389 个文件。主要消费者：`drivers/net/ethernet/`、`block/elevator.c`、`kernel/workqueue.c`

### 定位

**功能**：提供编译期定长的通用哈希表，支持元素的添加、删除、查找和遍历，以及 RCU 安全的读写并发变体。

**实现方式**：整个 API 是 `struct hlist_head` 数组上的宏和 inline 函数——没有 `.c` 文件，零运行时开销。`DEFINE_HASHTABLE(name, bits)` 在编译期分配 `2^bits` 个桶（每个桶是一个 `hlist_head`）。元素通过内嵌的 `struct hlist_node` 链接到桶中，`hash_add` 对 key 调用 `hash_min`/`hash_32` 取模后前插到对应桶。桶内是 hlist（单向链表），冲突时线性扫描。提供 `hash_add_rcu`/`hash_del_rcu`/`hash_for_each_possible_rcu` 等 RCU 变体，允许无锁读取。

**应用场景**：内核中最广泛使用的哈希表原语——389 个文件、约 1,393 处 API 调用。典型场景：网络驱动的流表（`drivers/net/ethernet/`）、块层 IO 调度器（`block/elevator.c`）、工作队列的 CPU 绑定表（`kernel/workqueue.c`）。适合桶内元素少（hash 函数好 + 桶数够多）且大小在编译期可确定的场景。需要动态大小时应改用 `rhashtable`。

### 核心结构

没有专用结构体——直接操作 `struct hlist_head` 数组：

```c
// 静态定义：
DEFINE_HASHTABLE(name, bits)
    // 展开为 struct hlist_head name[1 << bits] = { ... }

// 嵌入结构体：
DECLARE_HASHTABLE(name, bits)

// 元素通过 struct hlist_node 链接
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_HASHTABLE(name, bits)` | 静态定义并初始化 | 2^bits 个桶 |
| `DECLARE_HASHTABLE(name, bits)` | 声明（用于嵌入 struct） | |
| `hash_init(hashtable)` | 动态初始化 | |
| `hash_add(hashtable, node, key)` | 添加 | 对 key 取 hash 后前插到桶 |
| `hash_add_rcu(hashtable, node, key)` | RCU 安全添加 | |
| `hash_del(node)` | 删除 | 从任意 hashtable 中删除 |
| `hash_del_rcu(node)` | RCU 安全删除 | |
| `hash_for_each(name, bkt, obj, member)` | 遍历所有桶的所有元素 | |
| `hash_for_each_possible(name, obj, member, key)` | 遍历 key 对应桶的元素 | 最常用 |
| `hash_for_each_safe(name, bkt, tmp, obj, member)` | 遍历时可安全删除 | |
| `hash_empty(hashtable)` | 检查是否为空 | |
| `hash_hashed(node)` | 检查节点是否在表中 | |

### 使用注意

- **大小不可变**：`bits` 在编译期确定，运行时不能增长或缩小。需要动态大小用 `rhashtable`
- **不处理冲突策略**：桶内是 hlist，线性扫描。适合桶内元素少的场景（hash 函数好 + 桶数够多）
- **hash 函数**：通常用 `hash_min(val, bits)` 或 `hash_32(val, bits)`
- 典型模式：元素 struct 内嵌 `struct hlist_node`，通过 `container_of` 取回
- 389 个文件使用，是内核中最广泛使用的哈希表原语

---

## 2. assoc_array — 关联数组（16 路 trie）

**头文件**：`include/linux/assoc_array.h`（88 行）；**私有头**：`include/linux/assoc_array_priv.h`（178 行）；**实现**：`lib/assoc_array.c`（1,726 行）；**文档**：`Documentation/core-api/assoc_array.rst`（554 行）
**使用量**：仅 2 个外部文件（`security/keys/keyring.c` 和 `include/linux/key.h`），约 21 处调用

### 定位

**功能**：提供任意长度 opaque key 到 `void *` 对象的映射，支持 RCU 安全的无锁查找，以及两阶段编辑协议（prepare → apply/cancel）保证修改操作的原子性。

**实现方式**：16 路 trie（`ASSOC_ARRAY_FAN_OUT = 16`），每个内部节点有 16 个 slot。使用指针标记（底 2 位）区分三种节点类型：叶子（bit 0 = 0）、内部节点（bits = 01）、shortcut（bits = 11）。shortcut 节点跳过连续的单分支层，压缩 trie 深度。key 通过调用者提供的 `assoc_array_ops` 回调分块读取（`get_key_chunk`），支持任意长度的 key。编辑操作分两阶段：`insert`/`delete`/`clear` 不直接修改树，而是构建一个 `assoc_array_edit` 描述符；`apply_edit` 通过 RCU 安全的一次指针交换提交修改，保证读侧临界区内的查找不会看到中间状态。

**应用场景**：几乎只被内核密钥环（`security/keys/keyring.c`）使用——密钥环需要在大量 key 中按类型+描述符查找，key 的查找路径需要 RCU 无锁读（密钥查找在进程上下文中频繁发生），且 key 的类型和描述符长度不固定，适合 assoc_array 的 opaque key 设计。如果不需要任意长度 key + RCU 无锁读，用 `hashtable` 或 `rhashtable` 更简单。

### 核心结构

```c
struct assoc_array {
    struct assoc_array_ptr  *root;
    unsigned long            nr_leaves_on_tree;
};
// 内部节点：16 个 slot
struct assoc_array_node {
    struct assoc_array_ptr  *back_pointer;
    u8                       parent_slot;
    struct assoc_array_ptr  *slots[ASSOC_ARRAY_FAN_OUT]; // 16
    unsigned long            nr_leaves_on_branch;
};
// shortcut：跳过公共前缀
struct assoc_array_shortcut {
    struct assoc_array_ptr  *back_pointer;
    int                      parent_slot;
    int                      skip_to_level;
    struct assoc_array_ptr  *next_node;
    unsigned long            index_key[];
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `assoc_array_init(array)` | 初始化 | inline |
| `assoc_array_find(array, ops, index_key)` | 查找 | 返回 void * 或 NULL |
| `assoc_array_insert(array, ops, index_key, object)` | 准备插入 | 返回 edit handle |
| `assoc_array_delete(array, ops, index_key)` | 准备删除 | 返回 edit handle |
| `assoc_array_clear(array, ops)` | 准备清空 | 返回 edit handle |
| `assoc_array_apply_edit(edit)` | 提交编辑 | RCU 安全交换 |
| `assoc_array_cancel_edit(edit)` | 取消编辑 | 释放临时分配 |
| `assoc_array_destroy(array, ops)` | 销毁整棵树 | 调用 ops->free_object |
| `assoc_array_iterate(array, iterator, data)` | 遍历所有叶子 | |
| `assoc_array_gc(array, ops, iterator, data)` | GC：只保留 iterator 返回 true 的叶子 | |

### 使用注意

- **两阶段编辑**：`insert`/`delete`/`clear` 不直接修改树，而是返回一个 `assoc_array_edit *`。调用 `apply_edit` 才真正提交——这允许在 RCU 读侧临界区外准备修改
- **ops 回调**：调用者必须实现 `assoc_array_ops`（key 分块读取、对象比较、diff、释放）
- **几乎只有密钥环在用**：如果不需要任意长度 opaque key + RCU 无锁读，用 hashtable 或 rhashtable 更简单
- shortcut 压缩：当连续几层只有一个子节点时，用 shortcut 节点跳过，避免深层单分支
