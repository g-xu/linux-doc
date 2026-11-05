## 内存管理
Linux 内核的 **内存管理 (Memory Management, MM)** 是内核最复杂、最核心的子系统之一。它的主要职责不仅仅是“分配”和“释放”内存，更重要的是实现**虚拟内存机制**，让每个进程都认为自己拥有独立的、连续的地址空间，同时高效地利用有限的物理内存硬件。

为了让你系统地理解，我们可以把 Linux 内存管理划分为**三个层次**和**两个核心视角**：

*   **物理内存管理**：内核怎么管理那一排排真实的 RAM 芯片。
*   **虚拟内存管理**：内核怎么给进程画大饼（虚拟地址空间）。
*   **页面回收与交换**：内存不够用了怎么办。

---

### 1. 物理内存管理 (Physical Memory)

内核并不直接操作每一字节的物理内存，而是将其抽象为特定的层级结构，以适应不同的硬件架构（尤其是 NUMA 架构）。

#### 1.1 物理内存的层级结构

1.  **节点 (Node)**：
    *   对于 NUMA（非一致性内存访问）系统，每个 CPU 都有自己本地的内存，称为一个节点 (`pglist_data`)。访问本地节点快，访问远程节点慢。
    *   UMA（如个人电脑）系统通常只有一个节点。
2.  **管理区 (Zone)**：
    *   每个节点下的内存被划分为不同的区域 (`struct zone`)，以满足不同用途：
    *   `ZONE_DMA`：低地址（如 <16MB），供旧式硬件 DMA 使用。
    *   `ZONE_NORMAL`：内核可以直接映射的内存（最常用）。
    *   `ZONE_HIGHMEM`：高端内存（32位系统中 >896MB 的部分），内核无法直接映射，需要动态映射。64位系统通常没有这个烦恼。
    *   `ZONE_MOVABLE`：可热插拔或可迁移的内存区。
3.  **页帧 (Page Frame)**：
    *   物理内存管理的**最小单位**。通常是 **4KB**。
    *   内核用 `struct page` 结构体来描述每一个物理页。

#### 1.2 物理内存分配器

内核通过两层分配器来管理物理内存：

*   **底层：伙伴系统 (Buddy System)**
    *   **职责**：管理**物理连续**的页面，解决**外部碎片**问题。
    *   **原理**：它维护不同大小的空闲链表（$2^0, 2^1, \dots, 2^{10}$ 个页）。申请内存时，如果需要 4 页，它会找 4 页的块；如果没有，就把 8 页的块切两半，一半分配，一半留着。释放时，如果相邻的“伙伴”也是空闲的，就合并成大块。
    *   **接口**：`alloc_pages`。

*   **上层：Slab/Slub 分配器**
    *   **职责**：管理**小块内存**（对象），解决**内部碎片**问题。
    *   **原理**：基于 Buddy System 申请的大页，将其切割成无数个小块（如 `task_struct`, `inode`）。它像一个对象缓存池，用完回收，不还给 Buddy，而是留给下次接着用。
    *   **现状**：Linux 现在默认使用的是 **Slub**（Slab 的优化版），还有一种用于嵌入式的极简版 **Slob**。
    *   **接口**：`kmalloc`, `kmem_cache_alloc`。

---

### 2. 虚拟内存管理 (Virtual Memory)

这是用户空间进程和内核看到的“逻辑视图”。

#### 2.1 地址空间划分

以 64 位 Linux 为例：
*   **用户空间 (User Space)**：低地址部分。每个进程独立，互不干扰。
*   **内核空间 (Kernel Space)**：高地址部分。所有进程共享同一份内核映射（但在进入内核态前无法访问）。

#### 2.2 核心数据结构

1.  **`mm_struct`**：
    *   每个进程 (`task_struct`) 都有一个指针指向 `mm_struct`，它描述了该进程**整个**虚拟内存空间。
    *   包含页全局目录 (PGD) 的指针。

2.  **`vm_area_struct` (VMA)**：
    *   描述一段连续的、权限相同的虚拟内存区域。
    *   例如：代码段是一个 VMA（只读、可执行），数据段是一个 VMA（可读写），堆栈、mmap 映射区也都是 VMA。
    *   进程的所有 VMA 挂在红黑树和链表上。

#### 2.3 页表与映射 (Mapping)

虚拟地址如何变成物理地址？通过**页表 (Page Table)**。
*   Linux 采用多级页表（通常是 4级 或 5级：PGD -> P4D -> PUD -> PMD -> PTE）。
*   **MMU (内存管理单元)** 硬件负责查表。
*   **TLB (转换后备缓冲器)** 负责缓存查表结果，加速转换。

#### 2.4 缺页异常 (Page Fault)

Linux 的内存分配是**惰性 (Lazy)** 的。
*   当你调用 `malloc` 时，内核只在 VMA 里记录了一下“这块地归你了”，并**没有**分配物理内存。
*   当你真正去**读写**这块内存时，CPU 触发**缺页异常**。
*   内核捕获异常，分配物理页，修改页表，建立映射。
*   **Copy-on-Write (CoW)**：`fork()` 时，父子进程共享物理内存（只读）。只有当其中一方尝试写入时，内核才触发异常，复制一份物理页给写者。

---

### 3. 内存回收 (Reclaim)

物理内存是有限的，用完了怎么办？内核有以下策略：

#### 3.1 页面回收 (Page Reclaim)
内核通过 **LRU (Least Recently Used)** 链表跟踪页面的活跃程度（活跃链表/不活跃链表）。

*   **文件页 (File-backed pages)**：对应磁盘文件的页（如程序代码、打开的文件）。
    *   回收策略：如果是脏页（Dirty），先回写磁盘，再释放；如果是干净页，直接丢弃。
*   **匿名页 (Anonymous pages)**：堆、栈等没有对应文件的页。
    *   回收策略：必须写入 **Swap 分区**（交换空间）才能释放。

#### 3.2 回收机制
1.  **`kswapd` 线程**：后台默默运行。当内存水位低于“低水位线”时，开始回收不活跃的页。
2.  **直接回收 (Direct Reclaim)**：前台进程申请内存时，发现内存极度紧缺，被迫自己去执行回收逻辑（会导致进程卡顿）。
3.  **OOM Killer (Out of Memory)**：如果回收了半天还是不够，为了保住系统，内核会选择一个“最胖、最不重要”的进程直接杀掉，释放其内存。

---

### 4. 内存管理大图总结

我们可以把 Linux 内存管理想象成一个工厂：

| 组件 | 类比 | 技术术语 |
| :--- | :--- | :--- |
| **物理内存** | 原材料仓库 | RAM, NUMA Nodes, Zones |
| **Buddy System** | 仓库批发员 (整箱出货) | `alloc_pages`, `struct page` |
| **Slub** | 零售加工区 (拆箱做成零件) | `kmalloc`, `kmem_cache` |
| **虚拟内存** | 产品的设计图纸 | Virtual Address, `vmalloc` |
| **mm_struct / VMA** | 客户的订单和合同 | Process Address Space |
| **页表 (Page Table)** | 货物定位系统 | PGD/PTE, MMU |
| **缺页异常** | 按需生产 (客户来了才开工) | Page Fault, Demand Paging |
| **kswapd / Swap** | 清洁工 / 临时仓库 | Page Reclaim, LRU, Swap |

### 5. 常见 API 及其所属层级

*   **用户空间**：`malloc` (libc), `mmap` (syscall), `brk/sbrk`.
*   **内核空间 - 物理/连续**：`kmalloc` (小内存), `alloc_pages` (整页), `__get_free_pages`.
*   **内核空间 - 虚拟连续**：`vmalloc` (大内存，物理不连续).
*   **内核空间 - 专用**：`kmem_cache_alloc` (专用结构体).
*   **硬件交互**：`dma_alloc_coherent`.

理解 Linux 内存管理，关键在于理解 **“虚拟”与“物理”的解耦**，以及 **“惰性分配”** 的设计哲学。

以下是 Linux 内核内存管理模块主要 API 的汇总表。
#### Linux 内核内存管理核心API 

| 分类                         | API 函数                           | 功能描述                               | 关键特性 / 备注                                                                            |
| :------------------------- | :------------------------------- | :--------------------------------- | :----------------------------------------------------------------------------------- |
| **小块/通用内存**<br>(SLAB/SLUB) | `kmalloc(size, flags)`           | 分配指定大小的内存块。                        | **物理地址连续**，虚拟地址也连续。<br>最常用的接口。<br>`flags` 常用 `GFP_KERNEL` (可睡) 或 `GFP_ATOMIC` (不可睡)。 |
|                            | `kzalloc(size, flags)`           | 分配内存并**清零**。                       | 相当于 `kmalloc` + `memset(0)`。                                                         |
|                            | `kcalloc(n, size, flags)`        | 分配数组内存并清零。                         | 用于分配 `n` 个 `size` 大小的对象，防溢出检查。                                                       |
|                            | `kfree(ptr)`                     | 释放由 `kmalloc` 系列分配的内存。             | 传入 NULL 是安全的。                                                                        |
| **大块虚拟内存**<br>(Vmalloc)    | `vmalloc(size)`                  | 分配巨大的内存块。                          | **虚拟地址连续**，**物理地址不连续**。<br>性能稍低（涉及页表调整）。<br>**可能睡眠**，禁止在中断上下文使用。                     |
|                            | `vzalloc(size)`                  | 分配并清零的 `vmalloc`。                  |                                                                                      |
|                            | `vfree(addr)`                    | 释放由 `vmalloc` 分配的内存。               |                                                                                      |
| **混合策略**<br>(推荐用于数组)       | `kvmalloc(size, flags)`          | 尝试先用 `kmalloc`，如果失败则回退到 `vmalloc`。 | 兼顾性能与分配成功率。**驱动开发中分配大数组的首选**。                                                        |
|                            | `kvzalloc(size, flags)`          | 带清零的 `kvmalloc`。                   |                                                                                      |
|                            | `kvfree(addr)`                   | 释放由 `kvmalloc` 分配的内存。              | 内部会自动判断是属于 kmalloc 还是 vmalloc 区域。                                                    |
| **页级分配**<br>(伙伴系统)         | `alloc_pages(gfp_mask, order)`   | 分配 $2^{order}$ 个物理页。               | 返回 `struct page *` 结构体指针。<br>是内存分配的底层基石。                                             |
|                            | `__get_free_pages(mask, order)`  | 分配页并返回**虚拟地址**。                    | 直接返回可用的逻辑地址指针。                                                                       |
|                            | `get_zeroed_page(mask)`          | 分配一页并**清零**。                       | 返回虚拟地址，等同于 order=0 的 `__get_free_pages` + 清零。                                        |
|                            | `__free_pages(page, order)`      | 释放页（传入 page 指针）。                   |                                                                                      |
|                            | `free_pages(addr, order)`        | 释放页（传入虚拟地址）。                       |                                                                                      |
| **对象缓存**<br>(Kmem Cache)   | `kmem_cache_create(...)`         | 创建一个专用对象缓存池。                       | 针对频繁分配的固定大小结构体（如 `inode`），减少碎片，提升性能。                                                 |
|                            | `kmem_cache_alloc(cache, flags)` | 从缓存池中获取一个对象。                       | 极快，无碎片。                                                                              |
|                            | `kmem_cache_free(cache, obj)`    | 将对象归还给缓存池。                         |                                                                                      |
|                            | `kmem_cache_destroy(cache)`      | 销毁缓存池。                             | 卸载模块时必须调用。                                                                           |
| **Per-CPU 内存**<br>(多核优化)   | `alloc_percpu(type)`             | 为每个 CPU 核心分配一个变量副本。                | 避免多核锁竞争，显著提升并发性能。                                                                    |
|                            | `free_percpu(ptr)`               | 释放 Per-CPU 变量。                     |                                                                                      |
|                            | `per_cpu_ptr(ptr, cpu)`          | 获取指定 CPU 上的变量指针。                   |                                                                                      |
|                            | `this_cpu_ptr(ptr)`              | 获取当前 CPU 上的变量指针。                   |                                                                                      |
| **DMA 内存**<br>(硬件交互)       | `dma_alloc_coherent(...)`        | 分配 DMA 一致性内存。                      | 返回 CPU 用的虚拟地址和硬件用的 DMA 总线地址。<br>物理连续，无 Cache 一致性问题。                                  |
|                            | `dma_free_coherent(...)`         | 释放 DMA 内存。                         |                                                                                      |

---

#### 如何选择正确的 API？(速查指南)

| 你的需求 | 推荐 API | 理由 |
| :--- | :--- | :--- |
| **一般的结构体、小缓冲区** | `kmalloc` / `kzalloc` | 简单，快，物理连续。 |
| **巨大的缓冲区 (如 > 4MB)** | `vmalloc` | 物理内存碎片化导致大块连续内存很难申请到。 |
| **不确定大小的数组** | `kvmalloc` | 小的时候快，大的时候能成功，自动降级。 |
| **频繁创建/销毁同一个结构体** | `kmem_cache_alloc` | 专用高速缓存，无碎片。 |
| **需要在中断/原子上下文中分配** | `kmalloc(..., GFP_ATOMIC)` | 绝不能睡眠。 |
| **需要给硬件做 DMA 传输** | `dma_alloc_coherent` | 物理地址连续，且解决了 CPU Cache 与内存不一致的问题。 |
| **需要按页管理底层内存** | `alloc_pages` | 绕过 SLAB 层，直接操作伙伴系统。 |

## 更全面的API汇总

#### 1. 通用小块内存分配 (SLAB/SLUB)
**特点**：物理地址连续，虚拟地址连续，性能最高，基于 Slab/Slub 分配器。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `kmalloc(size, flags)` | 分配指定大小的内存。 | **最常用**。最大通常限制在 4MB-8MB (取决于架构)。<br>`flags`: `GFP_KERNEL`(可睡), `GFP_ATOMIC`(不可睡)。 |
| `kzalloc(size, flags)` | 分配并**清零**内存。 | 等同于 `kmalloc` + `memset(0)`。 |
| `kcalloc(n, size, flags)` | 分配数组内存并清零。 | 包含整数溢出检查 (`n * size`)。 |
| `krealloc(p, new_size, flags)` | 重新调整已分配内存的大小。 | 如果无法原地扩展，会分配新内存并拷贝数据，释放旧内存。 |
| `kfree(ptr)` | 释放 `kmalloc` 系列分配的内存。 | 传入 NULL 是安全的。 |
| `kfree_sensitive(ptr)` | **敏感数据释放**。 | 先将内存清零（覆盖），再释放。用于密钥等敏感数据 (旧内核叫 `kzfree`)。 |
| `ksize(ptr)` | 获取由 `kmalloc` 分配的实际内存大小。 | 实际分配可能比请求的略大（对齐原因）。 |

---

#### 2. 大块虚拟内存分配 (Vmalloc)
**特点**：虚拟地址连续，物理地址**不连续**，性能略低，适合大缓冲区。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `vmalloc(size)` | 分配巨大的内存块。 | **可能睡眠**，禁止在中断上下文使用。页对齐。 |
| `vzalloc(size)` | 分配并**清零**的 `vmalloc`。 | |
| `vcalloc(n, size)` | 分配数组并清零。 | |
| `vfree(addr)` | 释放 `vmalloc` 内存。 | 可以在任何上下文调用（但在中断中是延迟释放）。 |
| `vmap(pages, count, flags, prot)` | 将一组物理页映射到连续的虚拟地址空间。 | 高级用法，用于将分散的 `struct page` 拼接成连续视图。 |
| `vunmap(addr)` | 解除 `vmap` 的映射。 | |

---

#### 3. 混合/弹性分配 (KVmalloc)
**特点**：内核开发推荐的大内存分配方式，自动选择策略。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `kvmalloc(size, flags)` | 尝试先用 `kmalloc`，失败则回退到 `vmalloc`。 | **驱动开发首选**。小内存快，大内存稳。 |
| `kvzalloc(size, flags)` | 带清零的 `kvmalloc`。 | |
| `kvcalloc(n, size, flags)` | 数组形式的 `kvzalloc`。 | |
| `kvfree(addr)` | 释放内存。 | 自动识别地址类型并调用 `kfree` 或 `vfree`。 |

---

#### 4. 页级分配 (Buddy System / 伙伴系统)
**特点**：底层机制，分配单位为“页 (Page)”，物理地址连续。

| API 函数                         | 功能描述                        | 备注 / 注意事项                             |
| :----------------------------- | :-------------------------- | :------------------------------------ |
| `alloc_pages(gfp, order)`      | 分配 $2^{order}$ 个物理页。        | **核心函数**。返回 `struct page *` 指针。       |
| `alloc_page(gfp)`              | 分配单个物理页 (order=0)。          | 返回 `struct page *`。                   |
| `__get_free_pages(gfp, order)` | 分配页并返回**虚拟地址**。             | 相当于 `alloc_pages` 后调用 `page_address`。 |
| `get_zeroed_page(gfp)`         | 分配一页并**清零**，返回虚拟地址。         |                                       |
| `__free_pages(page, order)`    | 释放页（传入 `struct page *`）。    | 必须提供与分配时相同的 `order`。                  |
| `free_pages(addr, order)`      | 释放页（传入虚拟地址）。                |                                       |
| `page_address(page)`           | 获取 `struct page` 对应的内核逻辑地址。 | 仅适用于低端内存 (LowMem)。                    |

---

#### 5. 专用对象缓存 (Slab Cache)
**特点**：用于频繁创建/销毁固定大小的结构体，无碎片，极速。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `kmem_cache_create(...)` | 创建缓存池。 | 参数包括名称、对象大小、对齐方式、构造函数等。 |
| `kmem_cache_destroy(cache)` | 销毁缓存池。 | 必须确保池中所有对象已释放，否则报错。 |
| `kmem_cache_alloc(cache, flags)` | 从池中申请一个对象。 | |
| `kmem_cache_zalloc(cache, flags)` | 从池中申请并清零。 | |
| `kmem_cache_free(cache, obj)` | 将对象归还给池。 | |

---

#### 6. Per-CPU 内存分配
**特点**：为每个 CPU 核分配独立的副本，无锁并发，访问速度极快。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `alloc_percpu(type)` | 分配某种类型的 Per-CPU 变量。 | 返回一个特殊的偏移量指针（不是真实地址）。 |
| `free_percpu(ptr)` | 释放 Per-CPU 变量。 | |
| `per_cpu_ptr(ptr, cpu)` | 获取指定 CPU 上的变量真实地址。 | 必须通过此宏访问数据。 |
| `this_cpu_ptr(ptr)` | 获取**当前** CPU 上的变量真实地址。 | 需确保禁止抢占，防止中途切到其他 CPU。 |
| `get_cpu_var(var)` | 获取当前 CPU 的静态 Per-CPU 变量并**禁抢占**。 | 针对 `DEFINE_PER_CPU` 定义的静态变量。 |
| `put_cpu_var(var)` | 开启抢占。 | 与 `get_cpu_var` 配对。 |

---

#### 7. DMA (直接内存访问) 内存
**特点**：用于设备驱动与硬件交互，保证 Cache 一致性。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `dma_alloc_coherent(dev, size, &dma_handle, flags)` | 分配一致性 DMA 内存。 | 返回：虚拟地址。<br>`dma_handle`: 返回给硬件用的总线地址。<br>物理连续，无 Cache 问题。 |
| `dma_free_coherent(dev, size, cpu_addr, dma_handle)` | 释放 DMA 内存。 | |
| `dma_pool_create(...)` | 创建 DMA 内存池。 | 类似于 Slab，但产出的是 DMA 内存。 |
| `dma_pool_alloc(...)` | 从池中分配 DMA 内存块。 | |
| `dma_pool_free(...)` | 归还 DMA 内存块。 | |

---

#### 8. 内存池 (Mempool)
**特点**：为了防止在内存极度紧缺时发生死锁（例如文件系统回写数据时需要申请内存），预留一部分内存作为“救命稻草”。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `mempool_create(...)` | 创建内存池。 | 通常基于 `kmalloc` 或 `kmem_cache` 作为底层分配器。 |
| `mempool_destroy(pool)` | 销毁内存池。 | |
| `mempool_alloc(pool, flags)` | 从池中分配。 | 如果系统内存耗尽，会使用池中预留的备用元素，**保证分配成功**（可能会阻塞等待）。 |
| `mempool_free(obj, pool)` | 归还对象。 | |

---

#### 9. 高端内存映射 (Highmem)
**特点**：在 32 位系统中，访问超过 896MB 的物理内存需要临时映射。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `kmap(page)` | 将 page 映射到内核虚拟地址空间。 | **可能睡眠**。资源有限，用完必须解算。 |
| `kunmap(page)` | 解除映射。 | |
| `kmap_atomic(page)` | 临时原子映射。 | **不可睡眠**，禁止抢占。通常用于中断上下文极短时间的访问。 |
| `kunmap_atomic(addr)` | 解除原子映射。 | |
| `kmap_local_page(page)` | (新版内核推荐) 本地映射。 | 替代 `kmap_atomic`，允许迁移但不可睡眠。 |

---

#### 10. 用户空间数据交互
**特点**：在内核空间安全地访问用户空间的虚拟地址。

| API 函数 | 功能描述 | 备注 / 注意事项 |
| :--- | :--- | :--- |
| `copy_from_user(to, from, n)` | 从用户空间拷贝数据到内核。 | **可能睡眠**（缺页异常）。返回未拷贝成功的字节数。 |
| `copy_to_user(to, from, n)` | 从内核拷贝数据到用户空间。 | **可能睡眠**。 |
| `get_user(x, ptr)` | 读取用户空间简单变量 (int, char)。 | |
| `put_user(x, ptr)` | 写入用户空间简单变量。 | |
| `access_ok(ptr, size)` | 检查用户指针是否有效。 | 通常 `copy_` 函数内部会自动检查，但也可见于老代码。 |

---

#### 速查：我该用哪个？

1.  **绝大多数情况** --> `kmalloc`
2.  **需要大内存（>64KB）且不给硬件用** --> `kvmalloc`
3.  **需要极大内存（>4MB）** --> `vmalloc`
4.  **结构体频繁创建销毁** --> `kmem_cache_alloc`
5.  **驱动给硬件传数据** --> `dma_alloc_coherent`
6.  **需要在中断里分配** --> `kmalloc(..., GFP_ATOMIC)`
7.  **需要保证 100% 分配成功（如 swap 回写）** --> `mempool_alloc`