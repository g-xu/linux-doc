# page allocator — 页分配器（Buddy System）

> 内核内存管理的基石。管理所有物理页（`struct page`），通过 buddy system 按 2 的幂次分配和释放连续页。所有其他分配器（slab、vmalloc）最终都从这里获取物理页。

---

## §1 核心设计洞察

### 1.1 Buddy System 基本原理

```
order 0: [页][页][页][页][页][页][页][页]   每块 = 1 页 (4KB)
order 1: [页  页][页  页][页  页][页  页]    每块 = 2 页 (8KB)
order 2: [页    页    页    页    ]          每块 = 4 页 (16KB)
order 3: [页          页          ]          每块 = 8 页 (32KB)
  ...
order 10: [页                              ] 每块 = 1024 页 (4MB)
```

**分配**：请求 order-N 块。如果没有，拆分 order-(N+1) 块为两个 order-N 块，返回一个，保留另一个。

**释放**：释放 order-N 块时，检查它的"buddy"（地址互补的伙伴）是否也空闲。如果是，合并为 order-(N+1) 块。递归向上。

### 1.2 核心数据结构

**`struct zone`**（`include/linux/mmzone.h:525-693`）：内存区域。

```c
struct zone {
    // 空闲链表（按 order 组织）
    struct free_area free_area[MAX_ORDER];

    // 水位线
    unsigned long _watermark[NR_WMARK];  // WMARK_MIN, WMARK_LOW, WMARK_HIGH

    // 统计
    unsigned long nr_free_pages;         // 当前空闲页数
    atomic_long_t vm_stat[NR_VM_ZONE_STAT_ITEMS];

    // 所属节点
    struct pglist_data *zone_pgdat;      // 指向 node

    // 范围
    unsigned long zone_start_pfn;        // 起始页帧号
    unsigned long spanned_pages;         // 总跨度（含洞）
    unsigned long present_pages;         // 实际存在页数
};
```

**`struct free_area`**（`include/linux/mmzone.h:319-327`）：一个 order 的空闲页链表。

```c
struct free_area {
    struct list_head free_list[MIGRATE_TYPES];  // 按迁移类型分链表
    unsigned long nr_free;                       // 空闲块数
};
```

**`struct page`**（`include/linux/mm_types.h:70-204`）：每个物理页的描述符。

```c
struct page {
    unsigned long flags;          // PG_locked, PG_dirty, PG_slab 等
    atomic_t _refcount;           // 引用计数
    atomic_t _mapcount;           // 映射计数（-1 = 未映射）
    union {
        struct {                  // 空闲页（buddy）
            struct list_head lru; // 链接到 free_area 链表
            struct page *buddy;   // （调试用）
        };
        struct {                  // 已分配页
            // ... 各种用途的 union 字段
        };
    };
};
```

### 1.3 Zone 布局（arm64）

| Zone | 范围 | 用途 |
|------|------|------|
| `ZONE_DMA` | 0 ~ 1GB | 32 位 DMA 设备（`__GFP_DMA`） |
| `ZONE_DMA32` | 0 ~ 4GB | 32 位可寻址（`__GFP_DMA32`） |
| `ZONE_NORMAL` | 全部直接映射 | 默认分配区域 |
| `ZONE_MOVABLE` | 可迁移页 | 内存热插拔、CMA |

arm64 没有 `ZONE_HIGHMEM`——所有物理内存都在直接映射区。

### 1.4 分配路径

```
alloc_pages(gfp_mask, order)
    │
    ▼
get_page_from_freelist(gfp_mask, order, ac)
    │
    ├── 快速路径：遍历 zonelist，从 free_area[order] 取页
    │   └── __rmqueue_smallest(zone, order) — buddy split
    │       如果 order-N 空，拆 order-(N+1)，递归
    │
    └── 慢路径（快速失败时）：
        ├── 直接回收（__alloc_pages_slowpath）
        │   ├── wake_all_kswapds() — 唤醒 kswapd
        │   ├── __alloc_pages_direct_reclaim() — 同步回收
        │   └── 重试 get_page_from_freelist
        ├── OOM killer（如果 __GFP_NOFAIL 未设置）
        └── __GFP_NOFAIL：无限重试
```

### 1.5 释放路径

```
__free_pages(page, order)
    │
    ▼
__free_one_page(page, pfn, zone, order, migratetype)
    │
    ├── 检查 buddy 是否空闲
    │   ├── 是：合并为 order+1，递归向上
    │   └── 否：放入 free_area[order] 链表
    │
    └── 更新 zone->nr_free_pages
```

### 1.6 水位线（Watermarks）

每个 zone 维护三个水位线：

| 水位 | 含义 | 触发行为 |
|------|------|---------|
| `WMARK_MIN` | 最低水位 | 紧急分配（`__GFP_HIGH`）才允许 |
| `WMARK_LOW` | 低水位 | kswapd 开始异步回收 |
| `WMARK_HIGH` | 高水位 | 直接回收目标——回收到此水位才停 |

分配时检查：`free_pages >= watermark + (1 << order)`。低于 MIN 时只有 `__GFP_HIGH` 分配能通过。

### 1.7 MAX_ORDER

`MAX_ORDER` = 11（默认），意味着最大分配 = 2^10 = 1024 页 = 4MB。

`CONFIG_FORCE_MAX_ZONEORDER` 可以调整。透明大页（THP）使用 order-9（2MB）。

### 1.8 迁移类型

`MIGRATE_TYPES` 决定空闲页按迁移类型分链表：

| 类型 | 含义 |
|------|------|
| `MIGRATE_UNMOVABLE` | 不可迁移（内核映射） |
| `MIGRATE_MOVABLE` | 可迁移（用户页、THP） |
| `MIGRATE_RECLAIMABLE` | 可回收（slab） |
| `MIGRATE_HIGHATOMIC` | 原子分配保留 |
| `MIGRATE_ISOLATE` | 隔离（CMA、offline） |

分离链表的好处：回收不可迁移页时不会打扰可迁移页，提高 compaction 效率。

---

## §2 API 速查

头文件：`include/linux/gfp.h`、`include/linux/mmzone.h`；实现：`mm/page_alloc.c`

### 2.1 分配

| API | 说明 | 备注 |
|-----|------|------|
| `alloc_pages(gfp_mask, order)` | 分配 2^order 个连续页 | 返回 `struct page *` |
| `alloc_page(gfp_mask)` | 分配 1 页 | `alloc_pages(gfp, 0)` |
| `__get_free_page(gfp_mask)` | 分配 1 页，返回虚拟地址 | `unsigned long` 返回值 |
| `__get_free_pages(gfp_mask, order)` | 分配 2^order 页，返回虚拟地址 | |
| `get_zeroed_page(gfp_mask)` | 分配 1 页并清零 | |
| `__get_dma_pages(gfp_mask, order)` | 从 ZONE_DMA 分配 | |
| `alloc_pages_exact(size, gfp_mask)` | 分配精确大小的内存 | 可能拆分多余页 |
| `alloc_pages_node(nid, gfp, order)` | 指定 NUMA 节点分配 | |

### 2.2 释放

| API | 说明 | 备注 |
|-----|------|------|
| `__free_pages(page, order)` | 释放 2^order 页 | |
| `free_page(addr)` | 释放 1 页（虚拟地址） | |
| `free_pages(addr, order)` | 释放 2^order 页（虚拟地址） | |
| `__free_page(page)` | 释放 1 页（struct page *） | |
| `free_pages_exact(addr, size)` | 释放 alloc_pages_exact 的结果 | |

### 2.3 查询

| API | 说明 | 备注 |
|-----|------|------|
| `page_address(page)` | 获取页的虚拟地址 | 仅直接映射区有效 |
| `nr_free_pages()` | 全局空闲页数 | |
| `si_meminfo(info)` | 填充内存信息 | `/proc/meminfo` 用 |
| `page_to_pfn(page)` | page → 页帧号 | |
| `pfn_to_page(pfn)` | 页帧号 → page | |
| `virt_to_page(addr)` | 虚拟地址 → page | 仅直接映射区 |
| `page_to_virt(page)` | page → 虚拟地址 | |

### 2.4 GFP 标志

| 标志 | 说明 | 可睡眠 |
|------|------|--------|
| `GFP_KERNEL` | 正常内核分配 | 是 |
| `GFP_ATOMIC` | 原子上下文（中断/spinlock） | 否 |
| `GFP_NOWAIT` | 不等待不重试 | 否 |
| `GFP_NOIO` | 可睡眠但不做 I/O | 是 |
| `GFP_NOFS` | 可睡眠但不进文件系统 | 是 |
| `__GFP_ZERO` | 返回清零页 | — |
| `__GFP_DMA` | 从 ZONE_DMA | — |
| `__GFP_DMA32` | 从 ZONE_DMA32 | — |
| `__GFP_HIGHMEM` | 允许高端内存 | — |
| `__GFP_NOFAIL` | 必须成功（无限重试） | — |
| `__GFP_RETRY_MAYFAIL` | 尽力重试但可失败 | — |
| `__GFP_COMP` | 复合页 | — |
| `__GFP_MOVABLE` | 可迁移 | — |
| `__GFP_THISNODE` | 仅从指定节点 | — |

---

## §3 使用模式

### 3.1 基本页分配

```c
// 分配 1 页
struct page *page = alloc_page(GFP_KERNEL);
if (!page)
    return -ENOMEM;
void *addr = page_address(page);
// 使用 addr...
__free_page(page);
```

### 3.2 多页分配（order）

```c
// 分配 4 个连续页（order-2）
struct page *pages = alloc_pages(GFP_KERNEL, 2);
if (!pages)
    return -ENOMEM;
// pages 指向第一页，4 页物理连续
// ...
__free_pages(pages, 2);
```

### 3.3 虚拟地址接口

```c
// 分配 1 页，返回虚拟地址
unsigned long addr = __get_free_page(GFP_KERNEL);
if (!addr)
    return -ENOMEM;
// 直接使用 addr 作为指针
void *ptr = (void *)addr;
// ...
free_page(addr);
```

### 3.4 精确大小分配

```c
// 需要 10KB（不是 2 的幂）
void *buf = alloc_pages_exact(10240, GFP_KERNEL);
if (!buf)
    return -ENOMEM;
// buf 物理连续（可能分配了 3 页 = 12KB，多余的 2 页已释放）
// ...
free_pages_exact(buf, 10240);
```

---

## §4 常见陷阱

### 4.1 order 超过 MAX_ORDER

**错误**：`alloc_pages(GFP_KERNEL, 11)` — MAX_ORDER = 11，最大 order = 10。

**后果**：返回 NULL 或 `WARN_ON`。

**正确**：大块内存用 vmalloc（不要求物理连续）。

### 4.2 在中断上下文用 GFP_KERNEL

**错误**：中断 handler 中 `alloc_pages(GFP_KERNEL, 0)` — GFP_KERNEL 可能睡眠。

**正确**：原子上下文用 `GFP_ATOMIC`。

### 4.3 忘记释放页

**错误**：分配 `struct page *` 后只释放了虚拟地址，没释放页本身。

**正确**：`alloc_pages` 用 `__free_pages` 释放；`__get_free_page` 用 `free_page` 释放。

### 4.4 page_address 对高端内存无效

**错误**：对 HIGHMEM 页调用 `page_address()`。

**事实**：`page_address` 只对直接映射区的页有效。高端内存需要 `kmap` 获取临时映射。arm64 没有 HIGHMEM，所以这个问题在 arm64 上不存在。

### 4.5 假设空闲页的内容

**错误**：假设 `alloc_pages` 返回的页内容是零。

**正确**：需要零页用 `get_zeroed_page` 或手动清零。空闲页可能包含之前的数据。

### 4.6 内存碎片导致分配失败

**事实**：长时间运行的系统可能出现高阶分配失败（内存碎片化）。

**缓解**：使用 `__GFP_RETRY_MAYFAIL` 允许失败；或降低 order；或用 `__GFP_COMP` 请求复合页。

---

## §5 代码示例

### 5.1 设备驱动中的 DMA 缓冲区

```c
// 分配 DMA 安全的缓冲区（ZONE_DMA，物理连续）
struct dma_buffer {
    struct page *page;
    void *virt;
    dma_addr_t dma;
    size_t size;
};

int alloc_dma_buffer(struct device *dev, struct dma_buffer *buf, size_t size)
{
    unsigned int order = get_order(size);

    buf->page = alloc_pages(GFP_KERNEL | GFP_DMA, order);
    if (!buf->page)
        return -ENOMEM;

    buf->virt = page_address(buf->page);
    buf->size = PAGE_SIZE << order;
    buf->dma = dma_map_single(dev, buf->virt, size, DMA_BIDIRECTIONAL);
    if (dma_mapping_error(dev, buf->dma)) {
        __free_pages(buf->page, order);
        return -EIO;
    }
    return 0;
}

void free_dma_buffer(struct device *dev, struct dma_buffer *buf, size_t size)
{
    unsigned int order = get_order(size);
    dma_unmap_single(dev, buf->dma, size, DMA_BIDIRECTIONAL);
    __free_pages(buf->page, order);
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| page_alloc.c 实现 | `mm/page_alloc.c`（9,794 行） | 一等 |
| gfp.h GFP 标志 | `include/linux/gfp_types.h`（350 行） | 一等 |
| mmzone.h zone 定义 | `include/linux/mmzone.h`（1,915 行） | 一等 |
| struct page 定义 | `include/linux/mm_types.h:70-204` | 一等 |
| free_area 定义 | `include/linux/mmzone.h:319-327` | 一等 |
| MAX_ORDER = 11 | `include/linux/mmzone.h:28` | 一等 |
| 水位线定义 | `mm/page_alloc.c` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `alloc_pages` | 207 |
| `alloc_page` | 357 |
| `__get_free_page` | 219 |
| `__get_free_pages` | 223 |
| `free_pages` | 500 |
| `__free_pages` | 171 |
| `GFP_KERNEL` | 39,846 |
| `GFP_ATOMIC` | 5,067 |
