# slab/kmalloc — 内核通用内存分配器

> 内核最常用的内存分配接口。kmalloc 分配小对象（8B ~ 8KB），底层由 SLUB 分配器实现。SLUB 把内存组织为 per-CPU 空闲链表 + slab 页，快速路径无锁。大对象用 vmalloc 或 `__get_free_pages`。

---

## §1 核心设计洞察

### 1.1 整体架构

```
kmalloc(size, GFP_KERNEL)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  API 层:  kmalloc / kzalloc / krealloc / kfree          │
│           include/linux/slab.h                          │
├─────────────────────────────────────────────────────────┤
│  大小选择:  size_index[] 映射 → kmalloc_caches[type][idx]│
│           6.1 有 3 类: KMALLOC_NORMAL / DMA / DMA32      │
│           大小级别: 8, 16, 32, 64, 96, 128, 192, 256,   │
│                    512, 1k, 2k, 4k, 8k                   │
├─────────────────────────────────────────────────────────┤
│  分配器:  SLUB (mm/slub.c, 6,336 行)                    │
│           per-CPU 部分页 → 节点部分页列表 → 新页          │
│           空闲对象链表嵌入对象自身（freelist pointer）     │
├─────────────────────────────────────────────────────────┤
│  底层:    页分配器 (buddy system)                        │
│           分配/释放 slab 页（1 个或多个连续页）            │
└─────────────────────────────────────────────────────────┘
```

### 1.2 SLUB 的 slab 是什么

在 SLUB 中，一个 **slab** 是一个或多个连续物理页（`struct page` 数组），被划分为固定大小的对象。关键结构体：

- **`struct kmem_cache`**（`mm/slub.c:352-415`）：描述一类对象的分配器。包含对象大小、对齐、per-CPU 部分页、节点列表等
- **`struct slab`**（`include/linux/mm_types.h:349-376`）：一个 slab 的描述符。实际上就是 `struct page` 的重解释（slab 和 page 共用同一内存）
- **freelist**：空闲对象的链表。每个空闲对象的前 8 字节存放下一个空闲对象的指针（`freelist_t`）

### 1.3 kmem_cache 的核心字段

`mm/slub.c:352-415`（关键字段）：

```c
struct kmem_cache {
    // 基本属性
    unsigned int size;          // 对象大小（含对齐填充）
    unsigned int object_size;   // 用户请求的原始大小
    unsigned int align;         // 对齐要求
    unsigned int order;         // 每个 slab 的页数（2^order）

    // per-CPU 快速路径
    struct kmem_cache_cpu __percpu *cpu_slab;

    // 全局信息
    unsigned long min_partial;  // 保留的最小部分页数量
    int size;                   // 含元数据的对象大小

    // 节点级
    struct kmem_cache_node *node[MAX_NUMNODES];

    // 名称
    const char name[128];
};
```

### 1.4 快速路径分配（无锁）

SLUB 的快速路径在 `slub.c:3290-3340`（`slab_alloc_node`）：

1. 获取当前 CPU 的 `kmem_cache_cpu` 结构
2. 从 `c->freelist` 取第一个空闲对象
3. 如果 freelist 非空，直接返回——**无锁、无原子操作**
4. 如果 freelist 为空，进入慢路径

```c
// 快速路径伪代码
object = c->freelist;
if (likely(object)) {
    c->freelist = get_freepointer(s, object);
    c->tid = next_tid(c->tid);  // 乐观并发检测
    return object;
}
// 慢路径...
```

### 1.5 慢路径分配

当 per-CPU freelist 为空时：

1. **从 per-CPU 部分页取**：如果 `c->page` 存在但 freelist 空，重新扫描该页
2. **从节点部分页列表取**：`n->partial` 链表上有其他 CPU 释放的部分满 slab
3. **分配新页**：从 buddy system 获取新页，初始化为 slab

### 1.6 kmalloc 大小级别

`mm/slab_common.c:716-745`（`create_kmalloc_caches`）：

| 大小 | 索引 | 备注 |
|------|------|------|
| 8 | 0 | 最小 kmalloc 大小 |
| 16 | 1 | |
| 32 | 2 | |
| 64 | 3 | |
| 96 | 4 | 1.5× cache line |
| 128 | 5 | 2× cache line |
| 192 | 6 | 3× cache line |
| 256 | 7 | |
| 512 | 8 | |
| 1024 | 9 | |
| 2048 | 10 | |
| 4096 | 11 | 1 页 |
| 8192 | 12 | 最大 kmalloc 大小 |

超过 8192 字节的请求走 `kmalloc_large` → 直接从 buddy system 分配连续页。

### 1.7 GFP 标志

| 标志 | 含义 | 典型用途 |
|------|------|---------|
| `GFP_KERNEL` | 可睡眠，正常分配 | 进程上下文 |
| `GFP_ATOMIC` | 不可睡眠 | 中断 handler、spinlock 内 |
| `GFP_NOWAIT` | 不可睡眠 + 不重试 | 紧急路径 |
| `GFP_NOIO` | 可睡眠但不做 I/O | 块设备路径 |
| `GFP_NOFS` | 可睡眠但不做文件系统操作 | 文件系统路径 |
| `__GFP_ZERO` | 清零返回的内存 | 等价于 kzalloc |
| `__GFP_DMA` | 从 ZONE_DMA 分配 | DMA 缓冲区 |
| `__GFP_HIGHMEM` | 允许高端内存 | 32 位系统用 |

---

## §2 API 速查

头文件：`include/linux/slab.h`

### 2.1 kmalloc 家族

| API | 说明 | 备注 |
|-----|------|------|
| `kmalloc(size, flags)` | 分配内存 | 不初始化 |
| `kzalloc(size, flags)` | 分配并清零 | 等价 `kmalloc + memset` |
| `kmalloc_node(size, flags, node)` | 指定 NUMA 节点分配 | |
| `kmalloc_array(n, size, flags)` | 分配 n×size 数组 | 溢出检查 |
| `kcalloc(n, size, flags)` | 分配并清零数组 | `kmalloc_array + __GFP_ZERO` |
| `krealloc(ptr, new_size, flags)` | 重新分配 | 可能移动；ptr=NULL 等价 kmalloc |
| `kfree(ptr)` | 释放 | ptr=NULL 安全 |
| `kfree_sensitive(ptr)` | 释放前清零 | 敏感数据 |
| `kvfree(ptr)` | 释放 kmalloc 或 vmalloc 的内存 | 自动判断 |
| `ksize(ptr)` | 返回实际分配大小 | ≥ 请求大小 |

### 2.2 kmem_cache 操作

| API | 说明 | 备注 |
|-----|------|------|
| `kmem_cache_create(name, size, align, flags, ctor)` | 创建 slab cache | `ctor` 可为 NULL |
| `kmem_cache_create_usercopy(name, size, align, flags, useroffset, usersize, ctor)` | 创建允许用户空间拷贝的 cache | `copy_to/from_user` 用 |
| `kmem_cache_destroy(cache)` | 销毁 cache | 必须无活跃对象 |
| `kmem_cache_alloc(cache, flags)` | 从 cache 分配 | |
| `kmem_cache_free(cache, obj)` | 释放回 cache | |
| `kmem_cache_zalloc(cache, flags)` | 分配并清零 | |
| `kmem_cache_shrink(cache)` | 回收空闲 slab | 内存压力时调用 |
| `kmem_cache_reap_active()` | 是否有收缩进行中 | |

### 2.3 查询与调试

| API | 说明 | 备注 |
|-----|------|------|
| `ksize(ptr)` | 实际分配大小 | 可能大于请求大小 |
| `kmem_valid_obj(ptr)` | 指针是否指向 slab 对象 | 调试用 |
| `kmem_dump_obj(ptr)` | 打印对象信息 | 调试用 |
| `kmem_cache_name(cache)` | cache 名称 | |

---

## §3 使用模式

### 3.1 基本 kmalloc

```c
struct my_data *data;

data = kmalloc(sizeof(*data), GFP_KERNEL);
if (!data)
    return -ENOMEM;
// 使用 data...
kfree(data);
```

### 3.2 自定义 slab cache（高频分配对象）

```c
static struct kmem_cache *my_obj_cache;

struct my_object {
    int field1;
    char buffer[256];
};

static int __init my_init(void)
{
    my_obj_cache = kmem_cache_create("my_obj",
                                     sizeof(struct my_object),
                                     0,
                                     SLAB_HWCACHE_ALIGN,
                                     NULL);
    return my_obj_cache ? 0 : -ENOMEM;
}

struct my_object *alloc_my_obj(void)
{
    return kmem_cache_zalloc(my_obj_cache, GFP_KERNEL);
}

void free_my_obj(struct my_object *obj)
{
    kmem_cache_free(my_obj_cache, obj);
}
```

### 3.3 GFP_ATOMIC vs GFP_KERNEL 选择

```c
// 进程上下文：可以睡眠，用 GFP_KERNEL
void process_work(struct work_struct *work)
{
    buf = kmalloc(4096, GFP_KERNEL);
}

// 中断 handler：不能睡眠，用 GFP_ATOMIC
irqreturn_t my_handler(int irq, void *dev)
{
    buf = kmalloc(256, GFP_ATOMIC);
    if (!buf)
        return IRQ_HANDLED;  // 分配失败，丢弃
}
```

### 3.4 kvfree（不确定来源时）

```c
// 可能来自 kmalloc 或 vmalloc
void *ptr = allocate_buffer(size);
// ... 使用 ...
kvfree(ptr);  // 自动判断用 kfree 还是 vfree
```

---

## §4 常见陷阱

### 4.1 在中断上下文用 GFP_KERNEL

**错误**：中断 handler 或持 spinlock 时调用 `kmalloc(size, GFP_KERNEL)`。GFP_KERNEL 可能睡眠 → 调度器尝试获取锁 → 死锁。

**正确**：中断/spinlock 内只用 `GFP_ATOMIC` 或 `GFP_NOWAIT`。

### 4.2 kmalloc 返回的内存未初始化

**错误**：`kmalloc` 返回的内存包含垃圾值。

**正确**：需要零初始化用 `kzalloc`，或手动 `memset`。

### 4.3 krealloc(ptr, 0) 不等于 kfree

**错误**：认为 `krealloc(ptr, 0)` 会释放 ptr。

**事实**：`krealloc(ptr, 0, flags)` 等价于 `kfree(ptr)` 并返回 NULL——但语义不清晰。直接用 `kfree`。

### 4.4 ksize 的误用

**错误**：用 `ksize(ptr)` 获取的大小来写入数据，认为"反正分配了这么多"。

**事实**：`ksize` 返回的是 slab 实际分配的大小（含对齐填充），但写入超出请求大小的区域可能在 cache 合并后覆盖其他对象的元数据。只用于调试/信息输出。

### 4.5 slab cache 销毁时还有活跃对象

**错误**：`kmem_cache_destroy` 时 cache 中还有未释放的对象。

**后果**：内核会 `BUG_ON` 或打印警告。必须确保所有对象已释放后才销毁 cache。

### 4.6 超过 8KB 的 kmalloc

kmalloc 最大支持 `KMALLOC_MAX_SIZE`（通常 8KB）。超过此大小的请求会走 `kmalloc_large` → buddy system 分配连续页。对于大块内存（> 页大小），考虑 `vmalloc`（不要求物理连续）或 `__vmalloc`。

---

## §5 代码示例

### 5.1 设备驱动中的内存分配

```c
struct my_device {
    struct kmem_cache *req_cache;
    void __iomem *base;
    int irq;
};

static int my_probe(struct platform_device *pdev)
{
    struct my_device *dev;

    dev = kzalloc(sizeof(*dev), GFP_KERNEL);
    if (!dev)
        return -ENOMEM;

    dev->req_cache = kmem_cache_create("my_req",
                                        sizeof(struct request),
                                        0,
                                        SLAB_HWCACHE_ALIGN | SLAB_PANIC,
                                        NULL);
    // ...
}

static void my_remove(struct platform_device *pdev)
{
    struct my_device *dev = platform_get_drvdata(pdev);
    kmem_cache_destroy(dev->req_cache);
    kfree(dev);
}

// 快速分配请求对象
struct request *alloc_request(struct my_device *dev)
{
    return kmem_cache_zalloc(dev->req_cache, GFP_ATOMIC);
}

void free_request(struct my_device *dev, struct request *req)
{
    kmem_cache_free(dev->req_cache, req);
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| SLUB 实现文件 | `mm/slub.c`（6,336 行） | 一等 |
| slab_common 实现 | `mm/slab_common.c`（1,444 行） | 一等 |
| kmem_cache 结构体 | `mm/slub.c:352-415` | 一等 |
| kmalloc 大小级别 | `mm/slab_common.c:716-745` | 一等 |
| SLUB 快速路径 | `mm/slub.c:3290-3340` | 一等 |
| slab.h API 声明 | `include/linux/slab.h`（775 行） | 一等 |
| slub_def.h | `include/linux/slub_def.h`（192 行） | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `kmalloc` | 5,474 |
| `kzalloc` | 13,663 |
| `kfree` | 38,197 |
| `kmem_cache_alloc` | 334 |
| `kmem_cache_free` | 967 |
| `krealloc` | 201 |
| `kmem_cache_create` | 491 |
