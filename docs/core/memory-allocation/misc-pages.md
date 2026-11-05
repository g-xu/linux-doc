# folio 与 page_frag — 页管理与碎片分配

> folio 是内存管理核心对"一个或多个连续页"的统一抽象，消除了 compound page 的混乱 API；page_frag 是面向网络栈的碎片分配器，从 compound page 中逐块切分，用引用计数偏置避免原子操作开销。

---

## folio — 连续页统一抽象

### 定位

**功能**：folio 是内存管理核心对"一个或多个物理连续的页"的统一抽象。传统的 compound page（多页组合）在代码中用 `struct page` 表示，但 head page 和 tail page 的区分容易出错。folio 把"一组物理连续的页"包装成独立类型 `struct folio`，提供清晰的 API，消除 compound page 的混乱接口。每个 page 都属于某个 folio（单页 folio 就是普通 page）。

**实现方式**：核心结构体 `struct folio`（`include/linux/mm_types.h:281-320`）与 `struct page` 通过 union 共享内存布局，`FOLIO_MATCH` 宏（`mm_types.h:322-348`）在编译期验证偏移一致。`page_folio()`（`include/linux/page-flags.h:275`）用 `_compound_head()` 找到 folio 头，零成本转换。folio 携带 `_folio_order`（分配 order）、`_folio_nr_pages`（页数，仅 64 位）、`_pincount`（pin 计数）等 compound page head 的扩展字段。状态测试/修改 API（`folio_test_locked`、`folio_test_dirty` 等）由宏在 `include/linux/page-flags.h` 中自动生成。

**应用场景**：已渗透到内存管理和文件系统各层：mm/ 约 2,101 处、fs/ 约 833 处、include/ 约 639 处。细分：`page_folio` 228 处、`folio_lock/unlock` 255 处、`folio_put` 165 处、`folio_nr_pages` 145 处、`folio_test_*` 686 处。主要用户包括 page cache（`mm/filemap.c`）、内存迁移（`mm/migrate.c`）、内存策略（`mm/mempolicy.c`）和 OCFS2 集群（`fs/ocfs2/cluster/tcp.c`）。

### 实现方式

核心结构体（`include/linux/mm_types.h:281-320`）：

```c
struct folio {
    union {
        struct {
            unsigned long flags;           // 页标志
            union {
                struct list_head lru;      // LRU 链表
                struct {
                    void *__filler;
                    unsigned int mlock_count;
                };
            };
            struct address_space *mapping; // 文件映射或 anon_vma
            pgoff_t index;                 // 文件内偏移（页单位）
            void *private;
            atomic_t _mapcount;
            atomic_t _refcount;
#ifdef CONFIG_MEMCG
            unsigned long memcg_data;
#endif
        };
        struct page page;                  // 零成本转换
    };
    // 以下字段对应 compound page 的 head page 扩展字段
    unsigned long _flags_1;
    unsigned long __head;
    unsigned char _folio_dtor;             // 析构器类型
    unsigned char _folio_order;            // 分配 order
    atomic_t _total_mapcount;
    atomic_t _pincount;
#ifdef CONFIG_64BIT
    unsigned int _folio_nr_pages;          // 页数
#endif
    unsigned long _private_1;
};
```

**关键设计**：`struct folio` 与 `struct page` 通过 union 共享内存布局。`FOLIO_MATCH` 宏（`mm_types.h:322-348`）在编译期验证 folio 字段与 page 对应字段的偏移一致。

**转换**：
- `page_folio(page)`（`include/linux/page-flags.h:275`）：用 `_compound_head()` 找到 folio 头
- `folio_page(folio, n)`（`include/linux/page-flags.h:288`）：`nth_page(&folio->page, n)`

### 主要 API

头文件：`include/linux/mm.h`、`include/linux/page-flags.h`、`include/linux/pagemap.h`

| API | 说明 | 备注 |
|-----|------|------|
| `folio_alloc(gfp, order)` | 分配 folio | order=0 得单页，order>0 得多页 folio |
| `folio_get(folio)` | 增加引用计数 | |
| `folio_put(folio)` | 减少引用计数 | 到 0 时释放 |
| `folio_put_refs(folio, refs)` | 减少多个引用 | |
| `folio_order(folio)` | 获取 order | 单页返回 0 |
| `folio_nr_pages(folio)` | 获取页数 | `1 << order` |
| `folio_test_large(folio)` | 是否多页 folio | order > 0 |
| `folio_next(folio)` | 物理地址下一个 folio | |
| `page_folio(page)` | page → folio | 零成本 |
| `folio_page(folio, n)` | folio 第 n 页 | |

**状态测试/修改**（由宏生成，`include/linux/page-flags.h`）：

| API | 说明 |
|-----|------|
| `folio_test_locked / folio_lock / folio_unlock` | 页锁 |
| `folio_test_dirty / folio_mark_dirty` | 脏页标记 |
| `folio_test_writeback / folio_start_writeback / folio_end_writeback` | 回写状态 |
| `folio_test_uptodate` | 数据最新 |
| `folio_test_lru / folio_test_active / folio_test_referenced` | LRU 状态 |
| `folio_test_slab` | 是否 slab 页 |

### 使用规模

`folio_` 前缀引用：mm/ 约 2,101 处，fs/ 约 833 处，include/ 约 639 处。细分：`folio_put` 165、`page_folio` 228、`folio_lock/unlock` 255、`folio_nr_pages` 145、`folio_test_*` 686。

---

## page_frag — 网络碎片分配器

### 定位

**功能**：网络栈需要频繁分配小块内存（skb 数据缓冲区、IP 输出缓冲等）。page_frag 从 compound page 中逐块切分小块内存，用 per-CPU 缓存和引用计数偏置实现高效分配，避免每次分配整页造成的内部碎片浪费。

**实现方式**：核心结构体 `struct page_frag_cache`（`include/linux/mm_types.h:403-416`）包含 compound page 虚拟地址（`va`）、分配偏移（`offset`）、剩余大小（`size`）和引用计数偏置（`pagecnt_bias`）。分配时（`mm/page_alloc.c:5761`）从页尾向前切分，用递减 `pagecnt_bias` 代替 `get_page()` 避免每次分配都原子修改 `page->_refcount` 的缓存行。缓存耗尽时 `__page_frag_cache_refill()` 分配新 compound page（`__GFP_COMP | __GFP_NOWARN | __GFP_NORETRY`），失败回退到 order-0。释放时（`mm/page_alloc.c:5837`）用 `put_page_testzero()` 在引用计数到 0 时回收整页。

**应用场景**：主要消费者：网络核心栈的 skb 分配（`net/core/skbuff.c`）、IP 输出路径的 per-task 碎片（`net/ipv4/ip_output.c`、`net/ipv6/ip6_output.c`）、网络驱动（mt76 USB/Wi-Fi `drivers/net/wireless/mediatek/mt76/`）、NVMe-oF TCP（`drivers/nvme/target/tcp.c`、`drivers/nvme/host/tcp.c`）。`page_frag_alloc` 13 处、`page_frag_free` 23 处调用。

### 实现方式

核心结构体（`include/linux/mm_types.h:403-416`）：

```c
struct page_frag_cache {
    void *va;                    // compound page 的虚拟地址
    __u16 offset;                // 当前分配偏移
    __u16 size;                  // 剩余大小
    unsigned int pagecnt_bias;   // 引用计数偏置
    bool pfmemalloc;             // 是否来自内存压力路径
};
```

**分配路径**（`mm/page_alloc.c:5761`）：
1. 缓存为空时调用 `__page_frag_cache_refill()`：分配 compound page（`__GFP_COMP | __GFP_NOWARN | __GFP_NORETRY`），失败则回退到 order-0
2. 给页引用计数加上 `PAGE_FRAG_CACHE_MAX_SIZE + 1` 的偏置（批量引用）
3. 从页尾向前分配：`offset = nc->offset - fragsz`
4. 递减 `pagecnt_bias` 代替 `get_page()`——避免每次分配都原子修改 `page->_refcount`

**释放**（`mm/page_alloc.c:5837`）：
1. 从地址找到 head page
2. `put_page_testzero()`——引用计数到 0 时释放整页

**偏置机制**：不用 `get_page()/put_page()` 而用预加偏置 + 递减计数，避免每次分配都 dirty `_refcount` 所在的缓存行。当偏置用完（所有碎片都已释放）时，整页回收。

### 主要 API

头文件：`include/linux/gfp.h`、`include/linux/mm_types.h`

| API | 说明 | 备注 |
|-----|------|------|
| `page_frag_alloc(nc, fragsz, gfp)` | 分配碎片 | inline 包装 |
| `page_frag_alloc_align(nc, fragsz, gfp, align_mask)` | 对齐分配 | 核心实现 |
| `page_frag_free(addr)` | 释放碎片 | 自动找到所属页 |
| `__page_frag_cache_drain(page, count)` | 排空缓存 | 释放偏置引用 |

**网络专用**（`net/core/sock.c`）：

| API | 说明 |
|-----|------|
| `skb_page_frag_refill(sz, pfrag, gfp)` | 为 skb 补充碎片 |
| `sk_page_frag_refill(sk, pfrag)` | socket 级碎片补充 |

**per-task 碎片**：`current->task_frag` 是每个任务的 `struct page_frag`，用于 IP 输出路径（`net/ipv4/ip_output.c`、`net/ipv6/ip6_output.c`）。

### 使用规模

`page_frag_alloc` 13 处、`page_frag_free` 23 处、`page_frag_alloc_align` 7 处。主要用户：网络核心（skb 分配、IP 输出）、网络驱动（mt76 USB、tun）、NVMe-oF TCP、MPTCP、TLS 卸载。
