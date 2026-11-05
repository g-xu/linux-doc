# 树类数据结构（合并文档）

> btree / radix_tree / generic_radix_tree 三个模块的简明参考。每个模块 20-50 行，覆盖定位、核心 API、使用注意。

---

## 1. btree — B+ 树

**头文件**：`include/linux/btree.h`（244 行）；**实现**：`lib/btree.c`（796 行）
**补充头文件**：`include/linux/btree-type.h`（类型化包装）、`include/linux/btree-128.h`（128-bit key）
**使用量**：约 305 处引用，45 个文件。主要消费者：`drivers/md/bcache/`、`drivers/md/persistent-data/`、`fs/xfs/`、`net/netfilter/`

### 定位

**功能**：提供通用 B+ 树实现，支持 32/64/128-bit 三种 key 宽度的 key→value 映射，支持查找、插入、更新、删除、前驱查询和两棵树合并。

**实现方式**：节点大小对齐到 `max(L1_CACHE_BYTES, 128)` 字节（通常 128 字节），每个节点内 key 左对齐、以 NUL 哨兵终止——节点内操作退化为线性扫描，对 cache-line 友好（一次 cache miss 内完成整个节点的搜索）。节点分配使用 `mempool`，保证在内存压力下仍能执行删除操作（`GFP_NOIO` 场景不会死锁）。提供三种预定义几何（`btree_geo32`/`btree_geo64`/`btree_geo128`），并通过 `btree-type.h` 宏生成类型化包装（`btree_insert32`/`btree_insert64` 等）。

**应用场景**：块设备缓存（`drivers/md/bcache/`）的 key-value 索引、设备映射持久数据（`drivers/md/persistent-data/`）、XFS 文件系统的 B+ 树操作、netfilter 的集合匹配（`net/netfilter/`）。主要面向存储和文件系统子系统，这些场景需要固定 key 宽度的有序映射且不能容忍内存分配失败。

### 核心结构

```c
struct btree_head {
    unsigned long *node;
    mempool_t     *mempool;
    int            height;
};
// 三种预定义几何：btree_geo32, btree_geo64, btree_geo128
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `btree_init(head)` | 初始化，内部创建 mempool | 返回 0 或 -ENOMEM |
| `btree_init_mempool(head, mempool)` | 用调用者提供的 mempool 初始化 | 无需 destroy |
| `btree_destroy(head)` | 销毁内部 mempool | |
| `btree_lookup(head, geo, key)` | 查找 | 返回 value 指针或 NULL |
| `btree_insert(head, geo, key, val, gfp)` | 插入（key 不能已存在） | |
| `btree_update(head, geo, key, val)` | 更新已有 key 的值 | 不存在返回 -ENOENT |
| `btree_remove(head, geo, key)` | 删除 | 返回被删值或 NULL |
| `btree_last(head, geo, key)` | 获取最大 key 的条目 | 输出 key |
| `btree_get_prev(head, geo, key)` | 获取 `*key` 之前的条目 | 更新 `*key` |
| `btree_merge(target, victim, geo, gfp)` | 将 victim 全部条目合并到 target | |

### 使用注意

- `btree_insert` 要求 key 不存在，否则返回 `-EEXIST`；需要 upsert 语义时用 `btree_update`
- 有类型化包装：`btree_insert32`/`btree_insert64`/`btree_insertl` 等
- mempool 保证在内存压力下仍能删除（`GFP_NOIO` 场景）

---

## 2. radix_tree — 基数树

**头文件**：`include/linux/radix-tree.h`（472 行）；**实现**：`lib/radix-tree.c`（1606 行）
**使用量**：约 523 处引用，99 个文件。核心消费者：`mm/filemap.c`（页缓存）、inode 管理、网络设备驱动

### 定位

**功能**：提供 unsigned long 索引到 `void *` 指针的映射，支持 RCU 安全的并发查找，以及独有的 **tag bit** 功能（2 个 tag）——可以对已存储的条目打标记，然后高效遍历所有带特定标记的条目。

**实现方式**：自 5.x 起，radix tree 是 xarray 的薄兼容包装（`#define radix_tree_root xarray`），底层共享同一个 64 路分支的多级 trie 结构。相对 xarray 的独有功能是 tag bit：每个 slot 有 2 个标记位，`tag_set`/`tag_clear` 修改单个条目的标记，`radix_tree_for_each_tagged` 只遍历带指定标记的条目（利用内部节点的 tag summary 跳过无标记子树）。slot 编码使用底 2 位区分数据指针（00）、内部节点指针（10）和值条目（x1）。`radix_tree_preload` 机制允许在持 spinlock 前预分配内部节点，避免 GFP_KERNEL 睡眠。

**应用场景**：页缓存（`mm/filemap.c`）是最大消费者——用 radix tree 存储 `page → offset` 映射，用 tag bit 追踪 dirty/towrite/writeback 状态。writeback 路径通过 `radix_tree_for_each_tagged(PAGECACHE_TAG_DIRTY)` 高效扫描所有脏页。inode 管理、网络设备驱动的 ID 映射也使用 radix tree。新代码应使用 xarray，radix tree 保留仅为兼容存量代码。

### 核心结构

```c
// radix_tree_root 实际就是 xarray
struct radix_tree_iter {
    unsigned long index;
    unsigned long next_index;
    unsigned long tags;
    struct radix_tree_node *node;
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `radix_tree_insert(root, index, ptr)` | 在 index 处插入 | |
| `radix_tree_lookup(root, index)` | 查找 | RCU 安全 |
| `radix_tree_lookup_slot(root, index)` | 返回 slot 指针 | 用于后续 replace |
| `radix_tree_delete(root, index)` | 删除 | 返回被删指针 |
| `radix_tree_replace_slot(root, slot, entry)` | 替换 slot 内容 | |
| `radix_tree_gang_lookup(root, results, first, max)` | 批量查找 | 从 first 起最多 max 个 |
| `radix_tree_tag_set/clear/get` | tag 操作 | 2 个 tag bit |
| `radix_tree_preload(gfp)` / `radix_tree_preload_end()` | 预加载 | 持锁插入时使用 |
| `radix_tree_for_each_slot(slot, root, iter, start)` | 遍历所有非空 slot | |
| `radix_tree_for_each_tagged(slot, root, iter, start, tag)` | 遍历指定 tag 的 slot | writeback 核心路径 |

### 使用注意

- **新代码应使用 xarray**：radix tree 保留仅为兼容存量代码
- tag bit 是 radix tree 相对 xarray 的独有功能——页缓存的 `PAGECACHE_TAG_DIRTY`/`PAGECACHE_TAG_TOWRITE`/`PAGECACHE_TAG_WRITEBACK` 依赖它
- slot 编码：底 2 位区分数据指针（00）、内部节点（10）、值条目（x1）
- preload 模式：在持 spinlock 前调用 `radix_tree_preload(GFP_KERNEL)`，插入时用 `GFP_ATOMIC`

---

## 3. generic_radix_tree (genradix) — 通用基数树

**头文件**：`include/linux/generic-radix-tree.h`（239 行）；**实现**：`lib/generic-radix-tree.c`（250 行）
**使用量**：约 79 处引用，仅 4 个文件。消费者：`fs/proc/base.c`、`include/net/sctp/structs.h`

### 定位

**功能**：提供类型安全的稀疏数组——用 `GENRADIX(struct foo)` 声明一个以 `struct foo` 为元素类型的稀疏数组，支持按索引 O(log n) 访问、按需分配页面、遍历所有已分配条目。

**实现方式**：以页为粒度构建 radix tree，每个叶子节点存储 `PAGE_SIZE` 字节的原始数据。`GENRADIX(type)` 宏创建一个包装结构，内部通过 `sizeof(type)` 将元素索引转换为字节偏移，再由 radix tree 定位到对应的页面和页内偏移。分配路径使用 `cmpxchg_release` 实现无锁并发分配（多个线程同时分配不同索引不会冲突）。迭代器 `genradix_iter` 维护字节偏移和逻辑索引，自动跨页面边界。

**应用场景**：使用极少（6.1 中仅 4 个文件）。`fs/proc/base.c` 用 genradix 存储 `/proc` 下每个进程的条目列表（类型化稀疏数组，索引即进程号）。SCTP 网络协议（`include/net/sctp/structs.h`）用 genradix 存储关联的流列表。大多数场景用 xarray 或 radix tree 更合适——genradix 的独特卖点是类型安全和每元素可达 `PAGE_SIZE` 大小。

### 核心结构

```c
struct __genradix { struct genradix_root *root; };
// 用户通过宏使用：
#define GENRADIX(_type)  struct { struct __genradix tree; _type type[0]; }
// 迭代器：
struct genradix_iter { size_t offset; size_t pos; };
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `genradix_init(radix)` | 初始化 | 宏，不会失败 |
| `genradix_free(radix)` | 释放所有内存 | |
| `genradix_ptr(radix, idx)` | 获取元素指针 | 未分配返回 NULL |
| `genradix_ptr_alloc(radix, idx, gfp)` | 获取或分配 | 按需分配页面 |
| `genradix_for_each(radix, iter, p)` | 遍历所有已分配条目 | p 指向每个元素 |
| `genradix_prealloc(radix, nr, gfp)` | 预分配 nr 个条目 | |

### 使用注意

- 每个元素最大 `PAGE_SIZE` 字节
- 使用极少（4 个文件），大多数场景用 xarray 或 radix tree 更合适
- 类型安全是主要卖点：`GENRADIX(struct foo)` 让 `genradix_ptr` 返回 `struct foo *`
