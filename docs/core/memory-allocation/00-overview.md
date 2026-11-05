---
title: 总览
---

# 内存管理工具 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **slab/kmalloc** | 小对象分配器（8B~8KB），物理连续 | SLUB 快速路径无锁、零内部碎片、GFP_ATOMIC 可在中断中使用 | [slab.md](slab.md) |
| **kmem_cache** | 定制大小的 slab 缓存 | 构造函数、RCU 安全释放（SLAB_TYPESAFE_BY_RCU）、usercopy 白名单 | [misc-slab.md](misc-slab.md) |
| **page allocator** | 物理页管理（buddy system） | 唯一管理所有物理页的底层分配器；支持高阶分配（最大 4MB） | [page-allocator.md](page-allocator.md) |
| **vmalloc** | 虚拟连续、物理不连续分配 | 突破 buddy MAX_ORDER 限制；有 guard page 防越界 | [vmalloc.md](vmalloc.md) |
| **percpu** | per-CPU 数据与分配 | arm64 通过 `tpidr_el1` 单指令访问；`this_cpu_*` 无需关抢占 | [percpu.md](percpu.md) |
| **folio** | 连续页统一抽象 | 消除 compound page 的 head/tail 混淆；与 `struct page` 零成本转换 | [misc-pages.md](misc-pages.md) |
| **kvmalloc** | kmalloc/vmalloc 自动回退 | 大小不确定时自动选择最佳分配路径；`kvfree` 自动判断释放方式 | [misc-alloc.md](misc-alloc.md) |
| **mempool** | 死锁安全的预分配池 | 进程上下文永不失败；专用于"不能失败"的 I/O 路径 | [misc-pool.md](misc-pool.md) |
| **gen_pool** | 位图驱动的通用内存池 | 无锁 cmpxchg（NMI 安全）；管理 slab 不覆盖的特殊内存（SRAM/TCM） | [misc-pool.md](misc-pool.md) |
| **CMA** | 连续内存分配器 | 启动预留 + 运行时迁移可移动页面；解决大块 DMA 的碎片问题 | [misc-dma.md](misc-dma.md) |
| **dma-pool** | 原子 DMA 分配池 | 三个全局 gen_pool（DMA/DMA32/KERNEL）；后台扩展；基于 CMA/buddy | [misc-dma.md](misc-dma.md) |
| **dma-buf** | DMA 缓冲区共享框架 | 基于 fd 传递 + fence 同步；不分配内存，只管理跨驱动共享 | [misc-dma.md](misc-dma.md) |
| **page_frag** | 网络碎片分配器 | 从 compound page 切分；引用偏置避免原子操作；主要服务网络栈 | [misc-pages.md](misc-pages.md) |

> **注**：`objpool`（per-CPU 无锁对象池）在 Linux 6.4 才引入，6.1.y 中不存在。

---

## §2 选型决策

```
需要分配内存？
│
├─ 大小 ≤ 8KB？
│  │
│  ├─ 是 → 高频分配同构对象（> 100 次/秒）？
│  │       │
│  │       ├─ 是 → kmem_cache
│  │       │        （零碎片、构造函数、RCU 安全）
│  │       │
│  │       └─ 否 → kmalloc / kzalloc
│  │                （通用小对象分配）
│  │
│  └─ 否 → 大小不确定（范围宽）？
│          │
│          ├─ 是 → kvmalloc
│          │        （自动 kmalloc→vmalloc 回退）
│          │
│          └─ 否 → 需要物理连续？
│                  │
│                  ├─ 是 → 大小 > 4MB？
│                  │       │
│                  │       ├─ 是 → CMA
│                  │       │        （预留区域 + 页面迁移）
│                  │       │
│                  │       └─ 否 → alloc_pages（buddy system）
│                  │                （order ≤ 10）
│                  │
│                  └─ 否（虚拟连续即可）→ vmalloc
│
├─ 需要 per-CPU 数据？
│  │
│  └─ percpu
│       （this_cpu_* 无锁访问、alloc_percpu 动态分配）
│
├─ 分配"不能失败"？
│  │
│  └─ mempool
│       （预分配保证、进程上下文永不失败）
│
├─ 管理特殊内存（SRAM/TCM/DMA 池）？
│  │
│  └─ gen_pool
│       （位图管理、NMI 安全、自定义算法）
│
├─ 原子上下文需要 DMA 内存？
│  │
│  └─ dma-pool
│       （预分配 gen_pool、后台扩展）
│
├─ 跨驱动共享 DMA 缓冲区？
│  │
│  └─ dma-buf
│       （fd 传递、fence 同步、导出/导入模型）
│
└─ 网络栈碎片分配？
       │
       └─ page_frag
            （compound page 切分、引用偏置）
```

### 关键区分点

| 容易混淆的对 | 区分依据 |
|-------------|---------|
| kmalloc vs vmalloc | kmalloc 物理连续（≤ 8KB）；vmalloc 虚拟连续（可大块） |
| kmalloc vs kmem_cache | kmalloc 通用大小级别；kmem_cache 定制大小零碎片 |
| kvmalloc vs vmalloc | kvmalloc 自动回退（小走 kmalloc、大走 vmalloc）；vmalloc 直接分配 |
| CMA vs dma-pool | CMA 面向用户态大块 DMA（可睡眠）；dma-pool 面向原子上下文 DMA |
| mempool vs gen_pool | mempool 保证不失败（预分配）；gen_pool 管理特殊地址空间（位图） |
| dma-pool vs dma-buf | dma-pool 分配 DMA 内存；dma-buf 共享已分配的 DMA 缓冲区 |

---

## §3 核心对比表

### 3.1 分配特性对比

| 模块 | 分配粒度 | 物理连续 | 最大大小 | 可睡眠 | 中断可用 | 零初始化 |
|------|---------|---------|---------|--------|---------|---------|
| kmalloc | 字节 | 是 | 8KB | GFP_KERNEL 可 | GFP_ATOMIC 可 | kzalloc 可 |
| kmem_cache | 对象 | 是 | 自定义 | GFP_KERNEL 可 | GFP_ATOMIC 可 | 构造函数可 |
| vmalloc | 页 | 否 | 无硬限 | 是 | **否** | vzalloc 可 |
| alloc_pages | 页（2^n） | 是 | 4MB (MAX_ORDER=11) | GFP_KERNEL 可 | GFP_ATOMIC 可 | __GFP_ZERO 可 |
| kvmalloc | 字节~页 | 不确定 | 无硬限 | 是 | **否** | kvzalloc 可 |
| percpu | 字节 | 每 CPU 独立 | 每 chunk 有限 | GFP_KERNEL 可 | GFP_ATOMIC 可 | 是 |
| mempool | 后端决定 | 后端决定 | min_nr 预分配 | 进程上下文永不失败 | 可能失败 | 后端决定 |
| gen_pool | 自定义 | 管理区域决定 | 池大小决定 | 否（无锁） | 是（NMI 安全） | 否 |
| CMA | 页（连续） | 是 | 预留区大小 | 是 | **否** | 否 |
| dma-pool | 页 | 是 | 动态扩展 | 后台扩展 | 是（GFP_ATOMIC） | 否 |
| dma-buf | 不分配 | 导出方决定 | 导出方决定 | 是 | 取决于导出方 | 否 |
| page_frag | 字节碎片 | compound page 内 | 一页碎片 | 否 | 是 | 否 |

### 3.2 GFP 标志体系

所有分配器共享 GFP 标志体系，但解释不同：

| 标志 | slab | vmalloc | page allocator | CMA |
|------|------|---------|---------------|-----|
| `GFP_KERNEL` | 可睡眠 | 可睡眠 | 可睡眠+回收 | 可尝试迁移 |
| `GFP_ATOMIC` | 不可睡眠 | 不可用 | 不回收 | 不尝试 |
| `__GFP_ZERO` | 清零对象 | 清零页 | 清零页 | — |
| `__GFP_DMA` | ZONE_DMA | — | ZONE_DMA | — |
| `__GFP_NOFAIL` | 无限重试 | — | 无限重试 | — |

### 3.3 释放函数匹配

| 分配函数 | 释放函数 | 自动判断 |
|---------|---------|---------|
| `kmalloc` / `kzalloc` | `kfree` | — |
| `vmalloc` / `vzalloc` | `vfree` | — |
| `kvmalloc` / `kvzalloc` | `kvfree` | 自动判断 kfree/vfree |
| `alloc_pages` | `__free_pages` | — |
| `alloc_percpu` | `free_percpu` | — |
| `kmem_cache_alloc` | `kmem_cache_free` | — |

### 3.4 arm64 特定参数

| 参数 | 值 | 影响 |
|------|-----|------|
| `PAGE_SIZE` | 4 KiB（默认） | kvmalloc 阈值、slab 最大 order |
| `ARCH_DMA_MINALIGN` | 128 字节 | kmem_cache DMA 对齐 |
| `MAX_ORDER` | 11（4MB 最大） | buddy 最大分配 |
| `ZONE_DMA` | 0~16MB（`CONFIG_ZONE_DMA`） | `__GFP_DMA` 分配区域 |
| `ZONE_DMA32` | 0~4GB | `__GFP_DMA32` 分配区域 |

---

## §4 使用规模

以下数据来自 `rg -c PATTERN -g '*.c' -g '*.h'` 对 `/workspace/safety/linux-6.1.y` 全树的精确统计：

| 模块 | 涉及文件数 | 调用/出现总次数 | grep 命令 |
|------|-----------|---------------|----------|
| kmalloc | 2966 | 5861 | `rg -c 'kmalloc\b' -g '*.c' -g '*.h'` |
| vmalloc | 1240 | 2036 | `rg -c '\bvmalloc\b' -g '*.c' -g '*.h'` |
| kmem_cache_alloc | 221 | 361 | `rg -c 'kmem_cache_alloc\b' -g '*.c' -g '*.h'` |
| alloc_pages | 131 | 207 | `rg -c '\balloc_pages\b' -g '*.c' -g '*.h'` |
| alloc_percpu | 180 | 295 | `rg -c 'alloc_percpu\b' -g '*.c' -g '*.h'` |
| kvmalloc | 129 | 236 | `rg -c '\bkvmalloc\b' -g '*.c' -g '*.h'` |
| kvfree | 518 | 1565 | `rg -c '\bkvfree\b' -g '*.c' -g '*.h'` |
| mempool_alloc | 105 | 292 | `rg -c '\bmempool_alloc\b' -g '*.c' -g '*.h'` |
| gen_pool_alloc | 33 | 47 | `rg -c '\bgen_pool_alloc\b' -g '*.c' -g '*.h'` |
| cma_alloc | 8 | 16 | `rg -c '\bcma_alloc\b' -g '*.c' -g '*.h'` |
| dma_buf_export | 18 | 32 | `rg -c 'dma_buf_export\b' -g '*.c' -g '*.h'` |
| page_frag | 26 | 58 | `rg -c '\bpage_frag\b' -g '*.c' -g '*.h'` |
| folio | 219 | 6194 | `rg -c '\bfolio\b' -g '*.c' -g '*.h'` |

**解读**：
- kmalloc 是使用最广泛的分配器（2966 文件、5861 次调用），几乎是所有内核模块的默认选择
- folio 出现 6194 次，说明 6.1.y 中 folio 抽象已广泛渗透（但多数是 `struct folio *` 类型声明）
- vmalloc 使用量第二（1240 文件），主要用于模块加载和大块临时缓冲
- kvmalloc/kvfree 合计 1813 次，说明"大小不确定"的场景相当普遍
- CMA（16 次）和 dma-buf（32 次）使用量低，因为它们只出现在多媒体/GPU 驱动中

---

## §5 设计模式速览

### 5.1 分层依赖关系

```
dma-buf（共享框架）
    ↓ 使用
CMA / dma-pool（DMA 内存）
    ↓ 使用
kvmalloc（自动回退）
    ↓ 回退到
kmalloc ←→ kmem_cache     vmalloc
    ↓                         ↓
    └───── page allocator (buddy system) ──────┘
                ↓
           struct page（所有物理页的描述符）
                ↓
           folio（连续页统一抽象）
```

### 5.2 零拷贝路径模式

所有分配器追求"快速路径无锁"：
- **slab 快速路径**：per-CPU freelist，无锁单指令操作（`this_cpu_*`）
- **buddy 快速路径**：per-zone 空闲链表，order ≤ 3 时 per-CPU pageset
- **vmalloc 快速路径**：vmap_area 红黑树查找，vmap_lock spinlock 保护
- **gen_pool 快速路径**：cmpxchg 循环，完全无锁

### 5.3 预分配模式

- **mempool**：启动时分配 `min_nr` 个对象，运行时从池中借/还
- **dma-pool**：后台工作队列扩展 gen_pool，保证 GFP_ATOMIC 有库存
- **per-CPU pageset**：buddy 为每个 CPU 预填 pageset，减少 zone lock 竞争

### 5.4 引用计数与生命周期

- **folio**：`folio_ref_count()` 跟踪页引用；到 0 时释放回 buddy
- **page_frag**：偏置引用计数（`page_ref_add`），避免每碎片一次原子操作
- **dma-buf**：`dma_buf_get/put` 管理 fd 引用；到 0 时释放缓冲区

---

## §6 锁模型对比

| 模块 | 分配路径锁 | 释放路径锁 | 并发策略 | 中断安全 |
|------|-----------|-----------|---------|---------|
| slab (SLUB) | per-CPU freelist 无锁；慢路径 zone->lock | per-CPU 无锁；慢路径 zone->lock | per-CPU 分片减少竞争 | GFP_ATOMIC 安全 |
| kmem_cache | 同 slab | 同 slab | 同 slab | GFP_ATOMIC 安全 |
| page allocator | zone->lock（spinlock） | zone->lock | per-zone + per-CPU pageset | GFP_ATOMIC 安全 |
| vmalloc | vmap_lock（spinlock）+ global vmap_area_lock（spinlock） | 同左 + lazy vfree 延迟 | 红黑树 + 延迟释放减少锁持有时间 | **不可中断使用** |
| percpu | pcpu_lock（spinlock，chunk 分配时） | 无锁（per-CPU 释放） | per-CPU chunk，极少全局锁 | GFP_ATOMIC 安全 |
| kvmalloc | 取决于回退到 kmalloc 还是 vmalloc | kvfree 自动判断 | 继承底层分配器 | 取决于底层 |
| mempool | pool->lock（spinlock） | pool->lock | 预分配池 + 等待队列 | 可能失败 |
| gen_pool | 无锁（cmpxchg 循环） | 无锁（cmpxchg 循环） | 位图 + 原子 CAS | NMI 安全 |
| CMA | cma->lock（mutex） | 同左 | 位图 + 页面迁移 | **不可中断使用** |
| dma-pool | gen_pool 的 cmpxchg | gen_pool 的 cmpxchg | 继承 gen_pool 无锁特性 | GFP_ATOMIC 安全 |
| dma-buf | dmabuf->lock（mutex） | 同左 | fence 同步 + 引用计数 | 取决于导出方 |
| page_frag | 无锁（per-CPU 或原子偏置） | 无锁 | 引用偏置避免原子操作 | 安全 |

---

## §7 组合使用模式

### 7.1 slab + percpu：kmalloc 的 per-CPU 缓存

```
kmalloc → SLUB per-CPU freelist → percpu 分配器管理 freelist 存储
```

- `include/linux/slub_def.h` 中 `struct kmem_cache_cpu` 使用 per-CPU 变量
- 快速路径通过 `this_cpu_ptr` 访问 per-CPU freelist，完全无锁
- 慢路径才涉及全局 `struct kmem_cache_node`

### 7.2 page allocator + percpu：per-CPU pageset

```
alloc_pages → buddy 分配 → pcp (per-CPU pageset) 缓存
```

- `mm/page_alloc.c` 中 `struct per_cpu_pages` 为每个 zone 每 CPU 缓存单页
- 避免每次 alloc_pages(0) 都获取 zone->lock
- 水位线机制：pcp 库存低于 low 时批量从 buddy 补充

### 7.3 mempool + slab：块 I/O 死锁安全

```
bio_alloc → mempool_alloc → kmem_cache（后端）
```

- `block/bio.c` 中 `bio_set` 使用 mempool 包装 kmem_cache
- 预分配 `min_nr` 个 bio，保证进程上下文的 I/O 提交永不失败
- 防止文件系统→块设备→内存回收→文件系统的死锁环

### 7.4 CMA + dma-pool：原子 DMA 分配

```
原子 DMA 请求 → dma-pool (gen_pool) → 后台扩展 ← CMA / buddy
```

- `kernel/dma/pool.c` 中三个全局 gen_pool 管理 DMA/DMA32/KERNEL 区域
- 工作队列 `dma_pool_work_fn` 在后台扩展池
- 原子上下文（中断/spinlock）中的 DMA 分配走此路径

### 7.5 vmalloc + page allocator：模块加载

```
load_module → vmalloc → alloc_pages（底层）
```

- `kernel/module/main.c` 中模块代码/数据段通过 vmalloc 分配
- vmalloc 内部调用 `__vmalloc_area_node` → `alloc_pages` 获取物理页
- 模块卸载时 vfree 触发 lazy 释放

### 7.6 dma-buf + CMA：GPU 缓冲区共享

```
GPU 驱动 → CMA 分配 → dma_buf_export → 其他驱动通过 fd 导入
```

- 导出方（如 DRM 驱动）用 CMA 分配连续物理内存
- 通过 `dma_buf_export` 创建 dma-buf fd
- 导入方（如 V4L2/显示驱动）通过 fd 获取 DMA 地址
- fence 机制保证跨驱动访问的同步

---

## §8 阅读顺序建议

### 路线 A：通用内核开发（从最常用到最少用）

1. [slab.md](slab.md) — kmalloc 是每个驱动的基础
2. [percpu.md](percpu.md) — per-CPU 数据模式无处不在
3. [vmalloc.md](vmalloc.md) — 大块分配的备选方案
4. [misc-alloc.md](misc-alloc.md) — kvmalloc 自动回退
5. [page-allocator.md](page-allocator.md) — 理解底层页管理
6. [misc-pages.md](misc-pages.md) — folio 抽象
7. [misc-slab.md](misc-slab.md) — kmem_cache 定制缓存
8. [misc-pool.md](misc-pool.md) — mempool 和 gen_pool
9. [misc-dma.md](misc-dma.md) — CMA、dma-pool、dma-buf

### 路线 B：驱动开发（DMA 优先）

1. [misc-dma.md](misc-dma.md) — CMA + dma-pool + dma-buf 全览
2. [slab.md](slab.md) — kmalloc 基础
3. [page-allocator.md](page-allocator.md) — alloc_pages
4. [misc-pool.md](misc-pool.md) — gen_pool 管理特殊内存
5. [percpu.md](percpu.md) — per-CPU 统计
6. [vmalloc.md](vmalloc.md) — 大块缓冲
7. [misc-alloc.md](misc-alloc.md) — kvmalloc
8. [misc-slab.md](misc-slab.md) — kmem_cache
9. [misc-pages.md](misc-pages.md) — folio + page_frag

### 路线 C：内存管理子系统开发（从底层到上层）

1. [page-allocator.md](page-allocator.md) — buddy system 核心
2. [misc-pages.md](misc-pages.md) — folio 抽象
3. [slab.md](slab.md) — SLUB 架构
4. [misc-slab.md](misc-slab.md) — kmem_cache 内部
5. [percpu.md](percpu.md) — per-CPU 内存管理
6. [vmalloc.md](vmalloc.md) — 虚拟内存映射
7. [misc-alloc.md](misc-alloc.md) — kvmalloc 回退逻辑
8. [misc-pool.md](misc-pool.md) — mempool 死锁安全
9. [misc-dma.md](misc-dma.md) — DMA 内存管理

---

## §9 文档索引

| 文档 | 覆盖模块 | 主要内容 |
|------|---------|---------|
| [slab.md](slab.md) | slab/kmalloc | SLUB 架构、快速/慢路径、kmalloc 大小级别、GFP 标志 |
| [percpu.md](percpu.md) | percpu | tpidr_el1 偏移、this_cpu_* vs per_cpu_*、动态分配、chunk 管理 |
| [vmalloc.md](vmalloc.md) | vmalloc | 虚拟连续映射、vmap_area、guard page、lazy vfree |
| [page-allocator.md](page-allocator.md) | page allocator | buddy system、zone 布局、水位线、迁移类型 |
| [misc-pages.md](misc-pages.md) | folio, page_frag | folio 与 page 零成本转换、page_frag 偏置机制 |
| [misc-slab.md](misc-slab.md) | kmem_cache | kmem_cache 结构体、SLAB 标志、usercopy 白名单 |
| [misc-alloc.md](misc-alloc.md) | kvmalloc | kvmalloc_node 回退逻辑、kvfree_rcu |
| [misc-pool.md](misc-pool.md) | mempool, gen_pool | mempool 死锁安全、gen_pool 无锁 cmpxchg |
| [misc-dma.md](misc-dma.md) | CMA, dma-pool, dma-buf | CMA 迁移机制、三池架构、dma-buf fd/fence |
