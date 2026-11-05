# maple_tree — 范围自适应 B 树

## §1 定位

maple_tree 是内核中管理**不重叠区间**的自适应 B 树。它的核心使命是替代 rbtree 管理进程的虚拟内存区域（VMA）——这是内核中最大的 hot path 之一。

**与 rbtree 的根本区别：** rbtree 每个节点存一个 key-value 对，搜索需要用户提供比较函数。maple_tree 每个节点存多个区间（64 位系统上最多 16 个），用 **pivot**（枢轴值）划分区间边界，不需要外部比较函数——树自己知道怎么分。

**核心设计决策：**

1. **256 字节节点 + 256 字节对齐**——每个节点恰好占一个 cache line 组（4 × 64 字节），底部 8 位可用于类型编码。节点类型（16 位/32 位/64 位/根节点）编码在父指针的低位中，零额外开销
2. **Pivot 而非 key**——传统 B 树的 key 唯一标识一个位置；maple_tree 的 pivot 表示区间的右端点。slot[i] 的范围是 `pivot[i-1]+1` 到 `pivot[i]`。这使得一个节点可以表达多个连续区间
3. **两种模式共存**——普通树（`maple_range_64`，16 个 slot）追求最大存储密度；分配树（`maple_arange_64`，10 个 slot + gap 数组）牺牲密度换取 O(log n) 的区间分配能力（找一块足够大的空闲区域）
4. **RCU 安全**——读侧无锁（`mtree_load` / `mt_find` 只取 RCU 读锁），写侧用内部 spinlock 或外部锁。被删除的节点通过 `ma_topiary` 链表延迟释放，等 grace period 过后统一回收

**为什么 VMA 需要它：** 一个典型进程的地址空间有几十个到几千个 VMA。rbtree 方案中每次 `mmap` / `munmap` 都要旋转、着色，cache miss 频繁。maple_tree 的宽节点（16 个 slot）意味着树高通常只有 2-3 层，一次 cache 访问就能遍历一个节点的大部分 slot。

**定义位置：**

- 头文件：`include/linux/maple_tree.h`（692 行）
- 实现：`lib/maple_tree.c`（7254 行）
- 文档：`Documentation/core-api/maple_tree.rst`（217 行）

**使用规模：** `struct maple_tree` 全树 153 处引用；`MA_STATE` 112 处；`mas_reset` 94 处；`mas_set` 77 处；`mas_store_gfp` 51 处；`mtree_destroy` 156 处；`mas_walk` 58 处。

## §2 核心 API

头文件：`include/linux/maple_tree.h`；实现：`lib/maple_tree.c`。

### 2.1 声明与初始化

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_MTREE(name)` | 静态声明并初始化 | 展开为 `struct maple_tree name = MTREE_INIT(name, 0)` |
| `MTREE_INIT(name, flags)` | 静态初始化宏 | 含自旋锁初始化 |
| `MTREE_INIT_EXT(name, flags, lock)` | 使用外部锁的静态初始化 | 仅 `CONFIG_LOCKDEP` 时有区别 |
| `mt_init(mt)` | 动态初始化空树 | flags = 0 |
| `mt_init_flags(mt, flags)` | 带标志的动态初始化 | `MT_FLAGS_ALLOC_RANGE` / `MT_FLAGS_USE_RCU` / `MT_FLAGS_LOCK_*` |

### 2.2 树标志

| 标志 | 说明 | 备注 |
|------|------|------|
| `MT_FLAGS_ALLOC_RANGE` | 启用区间分配（gap 追踪） | 节点类型变为 `maple_arange_64`，slot 数从 16 降为 10 |
| `MT_FLAGS_USE_RCU` | RCU 安全模式 | 写时复制节点而非就地修改 |
| `MT_FLAGS_LOCK_IRQ` | 内部锁以 irq-safe 方式获取 | — |
| `MT_FLAGS_LOCK_BH` | 内部锁以 bh-safe 方式获取 | — |
| `MT_FLAGS_LOCK_EXTERN` | 使用外部锁 | 必须配合 `MTREE_INIT_EXT` |

### 2.3 简单 API（Normal API）

| API | 说明 | 备注 |
|-----|------|------|
| `mtree_load(mt, index)` | 查找 index 处的条目 | RCU 读保护；返回 `void *`，NULL 表示空 |
| `mtree_store(mt, index, entry, gfp)` | 在 index 处存储 entry | 覆盖写；entry=NULL 相当于清除 |
| `mtree_store_range(mt, first, last, entry, gfp)` | 在 [first, last] 区间存储 | 覆盖整个区间 |
| `mtree_insert(mt, index, entry, gfp)` | 在 index 处插入（不允许覆盖） | 已存在返回 `-EEXIST` |
| `mtree_insert_range(mt, first, last, entry, gfp)` | 区间插入（不允许覆盖） | — |
| `mtree_erase(mt, index)` | 删除 index 处条目并返回 | 返回被删条目 |
| `mtree_empty(mt)` | 树是否为空 | inline；检查 `ma_root == NULL` |
| `mtree_destroy(mt)` | 销毁整棵树 | 释放所有节点，不释放条目本身 |

### 2.4 简单查找与遍历

| API | 说明 | 备注 |
|-----|------|------|
| `mt_find(mt, &index, max)` | 从 index 开始查找下一个非 NULL 条目 | RCU 保护；`*index` 自动推进到条目末尾+1 |
| `mt_find_after(mt, &index, max)` | `mt_for_each` 内部使用 | 跳过首次的特殊处理 |
| `mt_next(mt, index, max)` | index 之后的下一个条目 | RCU 保护 |
| `mt_prev(mt, index, min)` | index 之前的上一个条目 | RCU 保护 |
| `mt_for_each(tree, entry, index, max)` | 遍历 [index, max] 内所有非 NULL 条目 | 宏；不返回零条目 |

### 2.5 区间分配 API（需 `MT_FLAGS_ALLOC_RANGE`）

| API | 说明 | 备注 |
|-----|------|------|
| `mtree_alloc_range(mt, &start, entry, size, min, max, gfp)` | 在 [min,max] 中找 size 大小的空闲区间 | 从低地址开始搜索 |
| `mtree_alloc_rrange(mt, &start, entry, size, min, max, gfp)` | 同上但从高地址搜索 | 反向搜索 |

### 2.6 锁与 RCU 控制

| API | 说明 | 备注 |
|-----|------|------|
| `mtree_lock(mt)` | 获取内部 spinlock | 宏 |
| `mtree_unlock(mt)` | 释放内部 spinlock | 宏 |
| `mt_set_in_rcu(mt)` | 切换到 RCU 模式 | 需要持锁（内部或外部） |
| `mt_clear_in_rcu(mt)` | 退出 RCU 模式 | 需要持锁 |
| `mt_in_rcu(mt)` | 查询是否处于 RCU 模式 | inline |
| `mt_height(mt)` | 获取树高度 | inline |
| `mt_external_lock(mt)` | 查询是否使用外部锁 | inline |

### 2.7 高级 API 状态管理

| API | 说明 | 备注 |
|-----|------|------|
| `MA_STATE(name, mt, first, end)` | 声明并初始化 `ma_state` | 宏；`node = MAS_START` |
| `MA_WR_STATE(name, mas, entry)` | 声明写状态 `ma_wr_state` | 宏 |
| `MA_TOPIARY(name, tree)` | 声明 topiary（延迟释放链表） | 宏 |
| `mas_set(mas, index)` | 设置操作位置 | inline；重置 `node = MAS_START` |
| `mas_set_range(mas, start, last)` | 设置操作区间 | inline |
| `mas_reset(mas)` | 重置遍历状态 | inline；下次遍历从根开始 |

### 2.8 高级 API 操作

| API | 说明 | 备注 |
|-----|------|------|
| `mas_walk(mas)` | 走到 `mas->index` 位置 | 返回该位置条目；`mas->index/last` 更新为条目实际范围 |
| `mas_store(mas, entry)` | 在 `mas->index..mas->last` 存储 | 需自行管理锁 |
| `mas_store_gfp(mas, entry, gfp)` | 存储（可分配内存） | — |
| `mas_store_prealloc(mas, entry)` | 使用预分配节点存储 | 不分配内存；配合 `mas_preallocate` |
| `mas_erase(mas)` | 删除 `mas->index` 处的条目 | 返回被删条目 |
| `mas_find(mas, max)` | 从当前位置查找下一个非 NULL | 首次调用从 `mas->index` 开始，后续自动推进 |
| `mas_find_rev(mas, min)` | 反向查找 | 从 `mas->last` 开始反向 |
| `mas_next(mas, max)` | 下一个条目（跳过 NULL） | 返回 `mas->index` 之后的下一个 |
| `mas_prev(mas, min)` | 上一个条目 | — |
| `mas_for_each(mas, entry, max)` | 遍历所有非 NULL 条目 | 宏；基于 `mas_find` |
| `mas_empty_area(mas, min, max, size)` | 找 [min,max] 中 size 大小的空闲区间 | 从低地址搜索；需 `ALLOC_RANGE` |
| `mas_empty_area_rev(mas, min, max, size)` | 反向找空闲区间 | 从高地址搜索 |

### 2.9 高级 API 预分配与生命周期

| API | 说明 | 备注 |
|-----|------|------|
| `mas_preallocate(mas, entry, gfp)` | 预分配后续写入所需的节点 | 保证后续 `mas_store_prealloc` 不分配 |
| `mas_expected_entries(mas, nr)` | 预告将要写入的条目数 | 进入批量插入模式，分配最坏情况节点 |
| `mas_destroy(mas)` | 释放 ma_state 的资源 | 批量插入结束后调用，释放多余节点 |
| `mas_nomem(mas, gfp)` | 处理内存不足 | 返回 true 表示已重试分配，应继续操作 |
| `mas_pause(mas)` | 暂停遍历（需要释放锁时） | 保存状态以便恢复 |

### 2.10 状态查询

| API | 说明 | 备注 |
|-----|------|------|
| `mas_is_err(mas)` | 是否出错 | 检查 `mas->node` 低位 |
| `mas_is_none(mas)` | 是否未找到 | inline；`mas->node == MAS_NONE` |
| `mas_is_paused(mas)` | 是否已暂停 | inline；`mas->node == MAS_PAUSE` |

### 2.11 复制

| API | 说明 | 备注 |
|-----|------|------|
| `mas_dup_tree(oldmas, mas)` | 复制整棵树 | 新树与旧树结构相同 |
| `mas_dup_store(mas, entry)` | 在复制的树中存储 | — |

### 2.12 特殊值

| 符号 | 说明 | 备注 |
|------|------|------|
| `MAS_START` | 未开始遍历 | `(struct maple_enode *)1UL` |
| `MAS_ROOT` | 唯一条目在根中 | `(struct maple_enode *)5UL` |
| `MAS_NONE` | 树中无匹配节点 | `(struct maple_enode *)9UL` |
| `MAS_PAUSE` | 遍历已暂停 | `(struct maple_enode *)17UL` |
| `MA_ERROR(err)` | 编码负错误码 | 低位 = `0b10`，高位 = errno |

### 2.13 调试（`CONFIG_DEBUG_MAPLE_TREE`）

| API | 说明 | 备注 |
|-----|------|------|
| `mt_dump(mt)` | 打印树结构 | 仅调试内核 |
| `mt_validate(mt)` | 验证树的一致性 | — |
| `mt_cache_shrink()` | 收缩节点缓存 | — |
| `MT_BUG_ON(tree, cond)` | 条件断言 | 非调试时退化为 `BUG_ON` |

### 2.14 保留值

maple_tree 保留底部两位为 `10` 且值 < 4096 的指针（即 2, 6, 10, ..., 4094）。如果需要存储这些值，用 `xa_mk_value()` 转换后再存入。

**API 总计：** 约 63 个（47 个函数/inline + 10 个宏 + 6 个调试符号）。

## §3 使用模式

### 模式 1：简单键值存储（替代 xarray / radix tree）

```c
struct maple_tree mt;
mt_init(&mt);

/* 存储 */
mtree_store(&mt, 42, my_pointer, GFP_KERNEL);

/* 查找 */
void *result = mtree_load(&mt, 42);

/* 删除 */
mtree_erase(&mt, 42);

/* 销毁 */
mtree_destroy(&mt);
```

关键点：`mtree_store` 是覆盖写——如果 index 42 处已有条目，直接替换。如果需要"不存在才插入"的语义，用 `mtree_insert`（返回 `-EEXIST`）。

### 模式 2：区间存储（VMA 管理核心模式）

```c
struct maple_tree mt;
mt_init(&mt);

/* 存储一个区间 [0x1000, 0x4fff] → vma_pointer */
mtree_store_range(&mt, 0x1000, 0x4fff, vma_pointer, GFP_KERNEL);

/* 查找区间内的任意地址都能找到 */
void *result = mtree_load(&mt, 0x2000);  /* 返回 vma_pointer */

/* 部分覆盖：清除 [0x3000, 0x4fff] */
mtree_store_range(&mt, 0x3000, 0x4fff, NULL, GFP_KERNEL);
/* 现在 [0x1000, 0x2fff] 仍有 vma_pointer，[0x3000, 0x4fff] 为 NULL */
```

### 模式 3：高级 API 遍历（持锁遍历整个地址空间）

```c
struct ma_state mas;
MA_STATE(mas, &mt, 0, ULONG_MAX);
void *entry;

mtree_lock(&mt);
mas_for_each(&mas, entry, ULONG_MAX) {
    /* mas.index 和 mas.last 是当前条目的区间 */
    process_vma(entry, mas.index, mas.last);
}
mtree_unlock(&mt);
```

关键点：`mas_for_each` 在每次迭代后自动更新 `mas.index` 和 `mas.last` 为当前条目的实际范围。如果需要在遍历中释放锁，先调用 `mas_pause(&mas)`。

### 模式 4：区间分配（需要 `MT_FLAGS_ALLOC_RANGE`）

```c
struct maple_tree mt;
mt_init_flags(&mt, MT_FLAGS_ALLOC_RANGE);

/* 找一块 16KB 的空闲区间 */
struct ma_state mas;
MA_STATE(mas, &mt, 0, ULONG_MAX);

mtree_lock(&mt);
int ret = mas_empty_area(&mas, 0x1000, 0xfffff, 4);  /* 4 页 = 16KB */
if (ret == 0) {
    /* mas.index 和 mas.last 现在是找到的空闲区间 */
    mas_store(&mas, my_vma);
}
mtree_unlock(&mt);
```

### 模式 5：预分配（保证写入不失败）

```c
struct ma_state mas;
MA_STATE(mas, &mt, start, end);

/* 在可能睡眠的上下文中预分配 */
int ret = mas_preallocate(&mas, entry, GFP_KERNEL);
if (ret)
    return ret;

/* 在不能失败的上下文中写入（如中断处理） */
spin_lock(&my_lock);
mas_store_prealloc(&mas, entry);
spin_unlock(&my_lock);

/* 清理未使用的预分配 */
mas_destroy(&mas);
```

### 模式 6：批量插入

```c
struct ma_state mas;
MA_STATE(mas, &mt, 0, ULONG_MAX);

/* 预告将要插入 100 个条目 */
mas_expected_entries(&mas, 100);

for (int i = 0; i < 100; i++) {
    mas_set_range(&mas, ranges[i].start, ranges[i].end);
    mas_store(&mas, entries[i]);
}

/* 释放多余节点 */
mas_destroy(&mas);
```

## §4 陷阱

### 陷阱 1：mtree_store 是覆盖写，mtree_insert 才是"不存在才插入"

```c
/* 错误：以为 mtree_store 只在空位插入 */
mtree_store(&mt, idx, new_ptr, GFP_KERNEL);  /* 会覆盖已有条目！ */

/* 正确：用 mtree_insert 检查冲突 */
int ret = mtree_insert(&mt, idx, new_ptr, GFP_KERNEL);
if (ret == -EEXIST)
    handle_conflict();
```

源码依据：`lib/maple_tree.c:6314-6331`（`mtree_store_range`）vs `lib/maple_tree.c:6367`（`mtree_insert_range` 内部检查 `mas_find` 是否已有条目）。

### 陷阱 2：mas_find 的首次调用行为

`mas_find(mas, max)` 首次调用时从 `mas->index` 开始搜索，后续调用从上一次结果的末尾+1 开始。如果先调用了 `mas_walk`，`mas->index` 已经被更新为 walk 的结果范围——`mas_find` 会从该范围之后开始找。

```c
/* 错误：walk 后 find 会跳过 walk 找到的条目 */
mas_walk(&mas);        /* mas.index 现在指向找到的条目 */
mas_find(&mas, max);   /* 从该条目之后开始找——跳过了！ */

/* 正确：如果要包含 walk 的结果，不要紧接着 find */
```

### 陷阱 3：区间分配需要 MT_FLAGS_ALLOC_RANGE

```c
/* 错误：普通树不支持区间分配 */
struct maple_tree mt;
mt_init(&mt);
mas_empty_area(&mas, 0, ULONG_MAX, 4);  /* 行为未定义！ */

/* 正确：必须用 MT_FLAGS_ALLOC_RANGE 初始化 */
mt_init_flags(&mt, MT_FLAGS_ALLOC_RANGE);
```

分配树使用 `maple_arange_64` 节点（10 个 slot + gap 数组），比普通树（`maple_range_64`，16 个 slot）每节点存储更少的条目。

### 陷阱 4：保留值（底部两位为 10 且 < 4096）

```c
/* 危险：这些值会被误判为错误 */
mtree_store(&mt, 0, (void *)2, GFP_KERNEL);   /* 2 的底部两位是 10 */
void *p = mtree_load(&mt, 0);                  /* 返回 (void *)2 */
/* mas_is_err() 可能误判 */

/* 安全：用 xa_mk_value() 包装整数 */
mtree_store(&mt, 0, xa_mk_value(2), GFP_KERNEL);
unsigned long val = xa_to_value(mtree_load(&mt, 0));  /* val == 2 */
```

源码依据：`include/linux/maple_tree.h:88-94`——保留底部两位 `10` 且值 < 4096 的指针。

### 陷阱 5：mas_nomem 的使用模式

当高级 API 返回 `-ENOMEM` 时，不能直接重试——必须调用 `mas_nomem` 来分配内存并重置状态：

```c
/* 正确模式 */
int ret;
do {
    ret = mas_store_gfp(&mas, entry, GFP_KERNEL);
} while (mas_nomem(&mas, GFP_KERNEL));

if (ret)
    return ret;
```

`mas_nomem` 返回 true 表示已成功分配内存并重置了状态，应继续操作。返回 false 表示没有内存请求需要处理。

### 陷阱 6：mtree_destroy 不释放条目

`mtree_destroy` 只释放树的内部节点，不释放你存入的条目。如果条目是动态分配的指针，必须先遍历释放：

```c
/* 正确：先释放条目，再销毁树 */
void *entry;
unsigned long idx = 0;
mt_for_each(&mt, entry, idx, ULONG_MAX) {
    kfree(entry);
}
mtree_destroy(&mt);
```

### 陷阱 7：mas_pause 的必要性

如果在遍历中需要释放锁（比如需要睡眠），必须先调用 `mas_pause`：

```c
/* 错误：直接释放锁后继续遍历 */
mas_for_each(&mas, entry, max) {
    mtree_unlock(&mt);
    do_something_slow(entry);
    mtree_lock(&mt);
    /* mas 的内部状态可能已失效！ */
}

/* 正确：暂停后再恢复 */
mas_for_each(&mas, entry, max) {
    mas_pause(&mas);
    mtree_unlock(&mt);
    do_something_slow(entry);
    mtree_lock(&mt);
    /* mas_for_each 内部会处理暂停状态 */
}
```

## §5 代码示例

### 示例 1：VMA 管理器（简化版，伪代码）

```c
/* 伪代码：演示 maple_tree 如何管理虚拟内存区域 */
struct vm_area_struct {
    unsigned long vm_start;
    unsigned long vm_end;
    /* ... 其他字段 ... */
};

struct mm_struct {
    struct maple_tree mm_mt;
    /* ... */
};

/* 初始化 */
void mm_init(struct mm_struct *mm)
{
    mt_init_flags(&mm->mm_mt, MT_FLAGS_ALLOC_RANGE);
}

/* 查找地址所在的 VMA */
struct vm_area_struct *find_vma(struct mm_struct *mm, unsigned long addr)
{
    return mtree_load(&mm->mm_mt, addr);
}

/* 插入新 VMA */
int insert_vma(struct mm_struct *mm, struct vm_area_struct *vma)
{
    return mtree_insert_range(&mm->mm_mt, vma->vm_start,
                              vma->vm_end - 1, vma, GFP_KERNEL);
}

/* 分配地址空间并插入 VMA */
int alloc_vma(struct mm_struct *mm, unsigned long size)
{
    MA_STATE(mas, &mm->mm_mt, 0, ULONG_MAX);
    int ret;

    mtree_lock(&mm->mm_mt);
    ret = mas_empty_area_rev(&mas, TASK_SIZE, 0, size);
    if (ret == 0) {
        /* mas.index .. mas.last 是找到的空闲区间 */
        struct vm_area_struct *vma = create_vma(mas.index, mas.index + size);
        mas_store(&mas, vma);
    }
    mtree_unlock(&mm->mm_mt);
    return ret;
}

/* 遍历所有 VMA */
void dump_vmas(struct mm_struct *mm)
{
    struct vm_area_struct *vma;
    unsigned long idx = 0;

    mt_for_each(&mm->mm_mt, vma, idx, ULONG_MAX) {
        printk("VMA: %lx - %lx\n", vma->vm_start, vma->vm_end);
    }
}
```

### 示例 2：高级 API 的预分配模式（真实代码模式）

```c
/* 基于 mm/mmap.c 中的真实模式（伪代码） */
int store_and_commit(struct maple_tree *mt, unsigned long start,
                     unsigned long end, void *entry)
{
    MA_STATE(mas, mt, start, end);
    int ret;

    /* 步骤 1：在可睡眠上下文中预分配 */
    ret = mas_preallocate(&mas, entry, GFP_KERNEL);
    if (ret)
        return ret;

    /* 步骤 2：在持锁上下文中写入（不分配内存） */
    mas_lock(&mas);
    mas_store_prealloc(&mas, entry);
    mas_unlock(&mas);

    /* 步骤 3：清理 */
    mas_destroy(&mas);
    return 0;
}
```

## §6 取证

### 已读文件

| 文件 | 行数 | 读法 |
|------|------|------|
| `include/linux/maple_tree.h` | 692 | 全读 |
| `lib/maple_tree.c` | 7254 | 头部注释 + EXPORT_SYMBOL 表 + 关键函数 |
| `Documentation/core-api/maple_tree.rst` | 217 | 全读 |

### 关键论断出处

| 论断 | 出处 | 验证命令 |
|------|------|----------|
| 256 字节节点 | `include/linux/maple_tree.h:30-42` | `MAPLE_NODE_SLOTS = 31`（64 位），每个 slot 8 字节 + parent 8 字节 = 256 字节 |
| 16 个 slot（range64） | `include/linux/maple_tree.h:31` | `MAPLE_RANGE64_SLOTS = 16` |
| 10 个 slot（arange64） | `include/linux/maple_tree.h:32` | `MAPLE_ARANGE64_SLOTS = 10` |
| 保留值 < 4096 | `include/linux/maple_tree.h:88-94` | 底部两位 `10` 且值 < `MAPLE_RESERVED_RANGE(4096)` |
| struct maple_tree 153 处 | grep 统计 | `grep -rw 'struct maple_tree\b' --include='*.c' --include='*.h' . \| wc -l` → 153 |
| MA_STATE 112 处 | grep 统计 | `grep -rw 'MA_STATE\b' --include='*.c' --include='*.h' . \| wc -l` → 112 |
| mtree_destroy 156 处 | grep 统计 | `grep -rw 'mtree_destroy\b' --include='*.c' --include='*.h' . \| wc -l` → 156 |
| mas_walk 58 处 | grep 统计 | `grep -rw 'mas_walk\b' --include='*.c' --include='*.h' . \| wc -l` → 58 |
| mas_store_gfp 51 处 | grep 统计 | `grep -rw 'mas_store_gfp\b' --include='*.c' --include='*.h' . \| wc -l` → 51 |
| mas_reset 94 处 | grep 统计 | `grep -rw 'mas_reset\b' --include='*.c' --include='*.h' . \| wc -l` → 94 |

### 未验证项

- `lib/maple_tree.c` 的 7254 行实现只读了头部注释（设计说明）和 EXPORT_SYMBOL 列表，未逐行核实核心算法（B 树分裂/合并/rebalance）
- `mas_dup_tree` 和 `mas_dup_store` 全树各只有 1 处调用，未深入分析使用场景
- 32 位系统的节点配置（`MAPLE_NODE_SLOTS = 63`）未验证
- `maple_dense` 节点类型的具体使用条件未从源码确认
