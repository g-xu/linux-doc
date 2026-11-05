# IDR / IDA — 整数 ID 分配器

> 基于 radix tree / xarray 的 ID 分配原语。IDR 映射 ID→指针（已弃用，推荐 xarray），IDA 仅分配 ID（每 ID 1 bit，更省内存）。

---

## §1 核心设计洞察

### 1.1 两个模块，一个分层

| | IDR | IDA |
|---|---|---|
| 底层 | `radix_tree_root` | `struct xarray` |
| 每 slot 存储 | `void *` 指针 | 1 bit（打包进 bitmap） |
| 内存效率 | 每 ID 消耗一个 radix tree slot | 每 ID 仅 1 bit |
| 锁 | 调用者自行提供 | 内部 `xas_lock_irqsave` |
| RCU 查找 | 支持（`idr_find` 可在 `rcu_read_lock` 下调用） | 不支持 |
| 状态 | **已弃用**，推荐 `xa_store`/`xa_erase` | **推荐使用** |

IDR 是 radix tree 的薄包装：`struct idr` 内嵌 `struct radix_tree_root idr_rt`，所有操作直接委托给 `radix_tree_*` 系列函数。IDA 是 xarray 的薄包装：`struct ida` 内嵌 `struct xarray xa`，用 `XA_FLAGS_LOCK_IRQ | XA_FLAGS_ALLOC` 初始化。

### 1.2 IDR 的 idr_base 偏移机制

`idr->idr_base` 让 IDR 的 ID 空间不必从 0 开始。所有内部 radix tree 操作使用 `id - idr_base` 作为索引：

```c
// lib/idr.c:152-155
void *idr_remove(struct idr *idr, unsigned long id)
{
    return radix_tree_delete_item(&idr->idr_rt, id - idr->idr_base, NULL);
}
```

`idr_init_base(idr, base)` 设置起始值。典型用途：某些子系统希望 ID 从 1 开始（避免 0 被当作"无效"）。

### 1.3 IDA 的双层存储优化

IDA 不是简单地为每个 ID 分配一个 bit——它有两层存储（`lib/idr.c:330-363`）：

1. **value entry**（≤ `BITS_PER_XA_VALUE` 个低位 bit）：直接将 bit 图编码为 xarray 值条目，零额外分配。适用于少量 ID 的场景
2. **bitmap entry**（`struct ida_bitmap`，128 字节 = `IDA_BITMAP_LONGS × sizeof(long)` = 1024 bit）：当 bit 数超过 value entry 容量时，升级为完整的 bitmap

分配路径（`ida_alloc_range`，`lib/idr.c:380-476`）：先 `xas_find_marked(XA_FREE_MARK)` 找到有空闲位的 chunk，然后在 bitmap 中 `find_next_zero_bit`。如果整个 bitmap 满了，清除 `XA_FREE_MARK` 标记。

### 1.4 IDR 的 IDR_FREE 标记

IDR 使用 radix tree 的 tag 0（`IDR_FREE`）标记"该子树下还有空闲 slot"。`idr_alloc` 分配时清除标记，`idr_remove` 删除时恢复标记。`idr_is_empty` 同时检查 `radix_tree_empty` 和 `radix_tree_tagged(IDR_FREE)`。

---

## §2 API 速查

### 2.1 IDR 初始化与销毁

头文件：`include/linux/idr.h`；实现：`lib/idr.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_IDR(name)` | 静态定义并初始化 IDR | 编译期初始化，无需额外调用 |
| `idr_init(idr)` | 动态初始化 IDR，base=0 | `idr_init_base(idr, 0)` 的包装 |
| `idr_init_base(idr, base)` | 动态初始化，指定起始 ID | base 影响所有后续操作的偏移 |
| `idr_destroy(idr)` | 释放 IDR 内部内存 | **不释放关联的指针所指对象** |
| `idr_is_empty(idr)` | 是否没有已分配 ID | 同时检查空树和 IDR_FREE 标记 |

### 2.2 IDR 分配与查找

| API | 说明 | 备注 |
|-----|------|------|
| `idr_alloc(idr, ptr, start, end, gfp)` | 分配 [start, end) 范围内的 ID | end≤0 视为 INT_MAX+1；返回分配的 ID 或负错误码 |
| `idr_alloc_u32(idr, ptr, &id, max, gfp)` | 分配 [id, max] 范围内的 ID | max 含入；分配结果写入 `*id`；先写 ID 再插指针（并发安全） |
| `idr_alloc_cyclic(idr, ptr, start, end, gfp)` | 从上次分配位置开始循环分配 | 使用 `idr->idr_next` 跟踪位置；耗尽后回绕到 start |
| `idr_find(idr, id)` | 查找 ID 对应的指针 | 可在 `rcu_read_lock` 下调用；NULL 可能表示未分配或存入了 NULL |
| `idr_remove(idr, id)` | 删除 ID 并返回关联指针 | ID 不存在时返回 NULL |
| `idr_replace(idr, ptr, id)` | 替换 ID 关联的指针 | 返回旧指针；ID 不存在返回 `ERR_PTR(-ENOENT)` |

### 2.3 IDR 遍历

| API | 说明 | 备注 |
|-----|------|------|
| `idr_for_each(idr, fn, data)` | 回调式遍历 | fn 返回非零值停止遍历并返回该值；RCU 安全 |
| `idr_for_each_entry(idr, entry, id)` | 迭代器式遍历 | entry 是类型指针；循环结束后 entry=NULL |
| `idr_for_each_entry_ul(idr, entry, tmp, id)` | unsigned long 版遍历 | 用 tmp 防止 id 溢出 |
| `idr_for_each_entry_continue(idr, entry, id)` | 从当前 id 继续遍历 | 用于分段处理 |
| `idr_get_next(idr, &id)` | 找下一个已用 ID | id 更新为找到的 ID；调用者需手动递增 |
| `idr_get_next_ul(idr, &id)` | unsigned long 版 | 同上 |

### 2.4 IDR 预加载与锁

| API | 说明 | 备注 |
|-----|------|------|
| `idr_preload(idr, gfp)` | 预分配内部节点 | 在持锁前调用，避免 GFP_KERNEL 死锁 |
| `idr_preload_end()` | 结束预加载段 | 必须与 `idr_preload` 配对 |
| `idr_get_cursor(idr)` | 读取循环分配器位置 | `READ_ONCE`，无锁 |
| `idr_set_cursor(idr, val)` | 设置循环分配器位置 | `WRITE_ONCE`，无锁 |

锁宏（均委托给 `xa_lock*` 系列）：

| 宏 | 对应 |
|----|------|
| `idr_lock(idr)` | `xa_lock` |
| `idr_unlock(idr)` | `xa_unlock` |
| `idr_lock_bh(idr)` | `xa_lock_bh` |
| `idr_unlock_bh(idr)` | `xa_unlock_bh` |
| `idr_lock_irq(idr)` | `xa_lock_irq` |
| `idr_unlock_irq(idr)` | `xa_unlock_irq` |
| `idr_lock_irqsave(idr, flags)` | `xa_lock_irqsave` |
| `idr_unlock_irqrestore(idr, flags)` | `xa_unlock_irqrestore` |

### 2.5 IDA API

头文件：`include/linux/idr.h`；实现：`lib/idr.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_IDA(name)` | 静态定义并初始化 IDA | |
| `ida_init(ida)` | 动态初始化 | |
| `ida_alloc(ida, gfp)` | 分配 [0, INT_MAX] 范围 ID | 内部加锁，调用者无需同步 |
| `ida_alloc_min(ida, min, gfp)` | 分配 [min, INT_MAX] 范围 | |
| `ida_alloc_max(ida, max, gfp)` | 分配 [0, max] 范围 | |
| `ida_alloc_range(ida, min, max, gfp)` | 分配 [min, max] 范围 | 底层实现，其余三个是其包装 |
| `ida_free(ida, id)` | 释放 ID | 释放未分配的 ID 会触发 `WARN` |
| `ida_destroy(ida)` | 释放所有 ID 和资源 | 不需要逐个 `ida_free` |
| `ida_is_empty(ida)` | 是否没有已分配 ID | |

已废弃别名：
- `ida_simple_get(ida, start, end, gfp)` → `ida_alloc_range(ida, start, end-1, gfp)`（注意 end 语义变化：旧版 exclusive，新版 inclusive）
- `ida_simple_remove(ida, id)` → `ida_free(ida, id)`

---

## §3 使用模式

### 3.1 IDR 基本用法（ID→指针映射）

```c
struct idr my_idr;
idr_init(&my_idr);

// 分配
int id = idr_alloc(&my_idr, ptr, 1, 0, GFP_KERNEL);  // [1, INT_MAX+1)
if (id < 0) { /* 错误处理 */ }

// 查找（可 RCU）
void *p = idr_find(&my_idr, id);

// 删除
void *old = idr_remove(&my_idr, id);

// 清理
idr_destroy(&my_idr);
```

### 3.2 IDR 预加载模式（持 spinlock 时分配）

```c
idr_preload(GFP_KERNEL);        // 可睡眠，在锁外
spin_lock(&lock);
id = idr_alloc(&idr, ptr, 0, 0, GFP_ATOMIC);  // 不会睡眠
spin_unlock(&lock);
idr_preload_end();
```

### 3.3 IDA 基本用法（纯 ID 分配）

```c
struct ida my_ids;
ida_init(&my_ids);

int id = ida_alloc(&my_ids, GFP_KERNEL);  // 内部加锁
if (id < 0) { /* -ENOMEM 或 -ENOSPC */ }

// 使用 id ...

ida_free(&my_ids, id);

// 清理（不需要逐个 free）
ida_destroy(&my_ids);
```

### 3.4 IDR 遍历与批量清理

```c
int id;
void *entry;

idr_for_each_entry(idr, entry, id) {
    // 处理 entry
    kfree(entry);  // 释放对象
}
idr_destroy(idr);  // 释放树结构（对象已被上面释放）
```

---

## §4 使用注意

### 4.1 IDR 已弃用，新代码应使用 xarray

`Documentation/core-api/idr.rst:20` 明确写道："The IDR interface is deprecated; please use the XArray instead." 但截至 6.1，IDR 仍有 **234** 处 `idr_alloc` 调用、**343** 处 `idr_remove` 调用——迁移是渐进的。新代码直接用 `xa_store`/`xa_erase`/`xa_alloc`。

### 4.2 idr_alloc 的 end 是 exclusive，idr_alloc_u32 的 max 是 inclusive

这是最常见的混淆源：

```c
idr_alloc(idr, ptr, 0, 10, GFP_KERNEL);    // 分配 [0, 10)，最大 ID=9
idr_alloc_u32(idr, ptr, &id, 10, GFP_KERNEL); // 分配 [id, 10]，最大 ID=10
```

`idr_alloc` 内部将 `end-1` 传给 `idr_alloc_u32`（`lib/idr.c:87`）。

### 4.3 idr_find 返回 NULL 有两种含义

NULL 可能表示"ID 未分配"，也可能表示"ID 已分配但关联的指针是 NULL"。`idr_alloc` 文档提到可以用 NULL 指针预留 ID（`Documentation/core-api/idr.rst:36`），此时 `idr_find` 返回 NULL 但 ID 实际已被占用。区分方法：`idr_for_each` 会遍历到该条目。

### 4.4 IDA 内部自带锁，IDR 不带

IDA 的 `ida_alloc_range` 使用 `xas_lock_irqsave`（`lib/idr.c:395`），调用者无需额外同步。IDR 的所有修改操作要求调用者持锁（通过 `idr_lock*` 宏或自定义锁）。

### 4.5 ida_free 释放未分配的 ID 只 WARN 不返回错误

`lib/idr.c:525`：`WARN(1, "ida_free called for id=%d which is not allocated.\n", id)`。函数返回 void，双重释放不会崩溃但会污染 dmesg。

### 4.6 idr_destroy / ida_destroy 不释放关联对象

IDR 的 `idr_destroy` 只释放 radix tree 内部节点，不 `kfree` 关联的指针所指对象。IDA 的 `ida_destroy` 遍历并 `kfree` 所有 bitmap（`lib/idr.c:548-551`），但 IDA 本身不关联对象指针。

### 4.7 idr_alloc_cyclic 的回绕行为

`lib/idr.c:126-128`：当从 `idr_next` 开始找不到空闲 ID 时，回绕到 `start` 再试一次。两次都失败才返回 `-ENOSPC`。这意味着即使 `idr_next` 已经越过大量空闲 ID，也会先尝试回绕。

### 4.8 IDA 的 value entry 优化对调用者透明

当分配的 ID 数量少（≤ `BITS_PER_XA_VALUE` 个 bit 在一个 chunk 内），IDA 使用 xarray value entry 而非完整 bitmap。这个优化完全内部——调用者只需知道"少量 ID 时内存占用极低"。

---

## §5 代码示例

### 5.1 设备次设备号分配（IDA 典型用法）

```c
static struct ida my_minor_ids;

static int __init my_init(void)
{
    ida_init(&my_minor_ids);
    return 0;
}

static int allocate_minor(void)
{
    int id = ida_alloc_max(&my_minor_ids, 255, GFP_KERNEL);
    if (id < 0)
        return id;
    return id;  // 0-255
}

static void release_minor(int id)
{
    ida_free(&my_minor_ids, id);
}

static void __exit my_exit(void)
{
    ida_destroy(&my_minor_ids);
}
```

### 5.2 IDR 配合 RCU 查找

```c
struct idr conn_idr;
spinlock_t conn_lock;

// 写入路径（持锁）
int register_connection(struct connection *conn)
{
    int id;
    spin_lock(&conn_lock);
    id = idr_alloc(&conn_idr, conn, 1, 0, GFP_ATOMIC);
    spin_unlock(&conn_lock);
    if (id < 0)
        return id;
    conn->id = id;
    return 0;
}

// 读取路径（RCU，无锁）
struct connection *lookup_connection(int id)
{
    struct connection *conn;
    rcu_read_lock();
    conn = idr_find(&conn_idr, id);
    if (conn)
        refcount_inc(&conn->ref);
    rcu_read_unlock();
    return conn;
}
```

---

## §6 证据摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| IDR 内嵌 radix_tree_root | `include/linux/idr.h:19-23` | 一等 |
| IDA 内嵌 xarray | `include/linux/idr.h:246-248` | 一等 |
| IDR 已弃用 | `Documentation/core-api/idr.rst:20` | 一等 |
| idr_alloc end exclusive vs u32 max inclusive | `lib/idr.c:87` | 一等 |
| IDA 内部加锁 | `lib/idr.c:395` (`xas_lock_irqsave`) | 一等 |
| ida_free 对非法 ID 只 WARN | `lib/idr.c:525` | 一等 |
| idr_alloc_cyclic 回绕逻辑 | `lib/idr.c:126-128` | 一等 |
| IDA 双层的存储（value/bitmap） | `lib/idr.c:330-363` 开发者注释 + `:403-427` | 一等 |
| 使用统计 | `rg -c` 全树计数，6.1.158 | 一等 |
