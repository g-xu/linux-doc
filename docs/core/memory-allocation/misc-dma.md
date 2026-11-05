# CMA、dma-pool 与 dma-buf — DMA 内存管理

> CMA 在启动时预留大块连续物理内存，按需迁移页面来腾出空间；dma-pool 是原子 DMA 分配的预分配池（基于 gen_pool）；dma-buf 是驱动间共享 DMA 缓冲区的框架，通过 fd 传递和 fence 同步。三者分别解决 DMA 内存的"预留"、"原子分配"和"跨驱动共享"问题。

---

## CMA — 连续内存分配器

### 定位

**功能**：某些设备（摄像头、视频解码器、GPU）需要大块物理连续内存。普通页分配器在系统运行一段时间后无法保证大块连续性。CMA 在启动时预留区域，运行时通过迁移可移动页面来腾出连续空间。

**实现方式**：核心结构体 `struct cma`（`mm/cma.h:13-34`）包含区域起始 PFN（`base_pfn`）、总页数（`count`）、分配位图（`bitmap`）和每 bit 代表的页 order（`order_per_bit`）。全局状态 `cma_areas[]` 和 `cma_area_count`（`mm/cma.c:38-40`）管理所有区域。`dma_contiguous_reserve()` 在启动时通过 memblock carve 物理内存，arm64 上优先放在 4GB 以上。分配时 `cma_alloc()`（`mm/cma.c:420`）扫描位图找连续零位，标记后调用 `alloc_contig_range()` 迁移不可移动页面，失败则在不同偏移重试。释放时 `cma_release()`（`mm/cma.c:551`）归还页面给 buddy 并清除位图。

**应用场景**：不仅被设备 DMA 通过 `dma_alloc_coherent()` → `dma_alloc_contiguous()` 路径使用，还被 hugetlb 大页分配（`mm/hugetlb.c`）、dma-buf CMA heap（`drivers/dma-buf/heaps/cma_heap.c`，为用户空间提供连续内存堆）直接调用。`cma_alloc` 16 处调用、`dma_alloc_coherent` 1,821 处调用（780 个文件）。

### 实现方式

核心结构体（`mm/cma.h:13-34`）：

```c
struct cma {
    unsigned long base_pfn;          // 区域起始 PFN
    unsigned long count;             // 区域总页数
    unsigned long *bitmap;           // 分配位图
    unsigned int order_per_bit;      // 每个位代表的页 order
    spinlock_t lock;                 // 保护位图
    char name[CMA_MAX_NAME];         // 区域名（最长 64 字节）
    // CONFIG_CMA_SYSFS:
    atomic64_t nr_pages_succeeded;   // 成功分配页数
    atomic64_t nr_pages_failed;      // 失败页数
};
```

全局状态（`mm/cma.c:38-40`）：
- `cma_areas[MAX_CMA_AREAS]`：所有 CMA 区域数组
- `cma_area_count`：活跃区域数
- `MAX_CMA_AREAS = 1 + CONFIG_CMA_AREAS`

**启动预留**：`dma_contiguous_reserve()` 在 arch 初始化时调用，读取 `cma=` 内核命令行或 `CONFIG_CMA_SIZE_MBYTES`，调用 `cma_declare_contiguous_nid()` 用 memblock  carve 物理内存。arm64 上优先放在 4GB 以上（`mm/cma.c:341-346`）。

**激活**（core_initcall `cma_init_reserved_areas()`）：
1. 分配位图
2. 验证所有 PFN 在同一 zone 内（关键约束）
3. 对每个 pageblock 调用 `init_cma_reserved_pageblock()`，设置 migratetype 为 `MIGRATE_CMA`
4. buddy 分配器随后可以将这些 pageblock 用于可移动分配

**分配流程**（`cma_alloc`，`mm/cma.c:420`）：
1. 扫描位图找连续零位（匹配请求大小 + 对齐）
2. 标记位为已用（spinlock 下）
3. 调用 `alloc_contig_range(pfn, pfn+count, MIGRATE_CMA, GFP_KERNEL)` 迁移不可移动页面
4. 如果 `-EBUSY`（迁移失败）：清除位图，在不同偏移重试
5. 成功返回 `struct page *`

**释放流程**（`cma_release`，`mm/cma.c:551`）：
1. 验证页面属于 CMA 区域
2. `free_contig_range()` 归还页面给 buddy
3. 清除位图位

### 主要 API

头文件：`include/linux/cma.h`；实现：`mm/cma.c`

| API | 说明 | 备注 |
|-----|------|------|
| `cma_init_reserved_mem(name, base, size, order_per_bit, res_cma)` | 从已预留内存创建 CMA 区域 | |
| `cma_declare_contiguous_nid(base, size, alignment, limit, fixed, name, res_cma, nid)` | 预留 + 创建 | 用 memblock 分配 |
| `cma_alloc(cma, count, align, no_warn)` | 分配连续页 | 可能迁移页面 |
| `cma_release(cma, pages, count)` | 释放 | |
| `cma_pages_valid(cma, pages, count)` | 检查页面是否属于区域 | |
| `cma_for_each_area(iter, data)` | 遍历所有区域 | |
| `cma_get_base(cma)` | 物理基址 | |
| `cma_get_size(cma)` | 字节大小 | |
| `cma_get_name(cma)` | 区域名 | |

**DMA 桥接层**（`kernel/dma/contiguous.c`）：

| API | 说明 |
|-----|------|
| `dma_contiguous_reserve(limit)` | 启动时预留全局 CMA |
| `dma_alloc_contiguous(dev, size, gfp)` | 高层分配：先 dev->cma_area，再 per-NUMA，再默认 |
| `dma_free_contiguous(dev, page, size)` | 释放：反向查找 + `__free_pages()` 回退 |
| `dev_get_cma_area(dev)` | 获取设备的 CMA 区域 |

### 与 dma_alloc_coherent 的关系

arm64 上无 IOMMU 路径（dma-direct）：
```
dma_alloc_coherent() → dma_direct_alloc() → __dma_direct_alloc_pages()
  → dma_alloc_contiguous() → cma_alloc()
```

CMA 仅在允许睡眠时尝试（`gfpflags_allow_blocking(gfp)` 为真）。

### 使用规模

`cma_alloc` 16 处（8 个文件）、`cma_release` 17 处（9 个文件）、`dma_alloc_coherent` 1,821 处（780 个文件）。主要用户：hugetlb、dma-buf CMA heap、DMA 驱动。

---

## dma-pool — 原子 DMA 分配池

### 定位

**功能**：`dma_alloc_coherent()` 在原子上下文（`GFP_ATOMIC`、不允许睡眠）中无法走 CMA 路径。dma-pool 预分配了三个 gen_pool，保证原子 DMA 分配在任何上下文中都可用。

**实现方式**：三个全局池（`kernel/dma/pool.c:16-21`）——`atomic_pool_dma`（GFP_DMA）、`atomic_pool_dma32`（GFP_DMA32）、`atomic_pool_kernel`（GFP_KERNEL）。postcore_initcall 初始化，默认每 1GB RAM 分配 128KB，可通过 `coherent_pool=` 命令行覆盖。`dma_alloc_from_pool()`（`kernel/dma/pool.c:265`）用 `dma_guess_pool()` 根据 GFP 标志选池，失败按 kernel → dma32 → dma 回退。可用空间低于阈值时调度 workqueue 后台扩展（`atomic_pool_work_fn()`），扩展先尝试 CMA 再回退到 `alloc_pages()`。

**应用场景**：仅被 DMA 映射层调用：`kernel/dma/direct.c`（dma-direct 路径）和 `drivers/iommu/dma-iommu.c`（IOMMU DMA 路径），在 `dma_alloc_coherent()` 的原子分配分支中触发。`dma_alloc_from_pool` 4 处调用、`dma_free_from_pool` 7 处调用。

### 实现方式

三个全局池（`kernel/dma/pool.c:16-21`）：

```c
static struct gen_pool *atomic_pool_dma __ro_after_init;      // GFP_DMA
static struct gen_pool *atomic_pool_dma32 __ro_after_init;    // GFP_DMA32
static struct gen_pool *atomic_pool_kernel __ro_after_init;   // GFP_KERNEL
```

**初始化**（postcore_initcall `dma_atomic_pool_init()`）：
- 默认大小：每 1GB RAM 分配 128KB，限制在 [128KB, MAX_ORDER-1 页]
- 可通过 `coherent_pool=` 命令行覆盖
- 每个池用 `gen_pool_first_fit_order_align` 算法

**池扩展**（`atomic_pool_expand()`）：
1. 先尝试 CMA（`dma_alloc_from_contiguous()`，仅在 CMA 位于正确 zone 时）
2. 回退到 `alloc_pages()`
3. 失败则降低 order 重试
4. 调用 `arch_dma_prep_coherent()` 清理缓存
5. arm64 上 `CONFIG_DMA_DIRECT_REMAP`：用 `pgprot_dmacoherent()` 重映射为非缓存

**分配路径**（`dma_alloc_from_pool()`，`kernel/dma/pool.c:265`）：
1. `dma_guess_pool()` 根据 GFP 标志选择池
2. 失败则按 kernel → dma32 → dma 顺序回退
3. 分配成功后清零、检查物理地址合法性
4. 可用空间低于阈值时调度后台扩展

**后台扩展**（`atomic_pool_work_fn()`）：可用空间低于 `atomic_pool_size` 时触发，作为 workqueue 任务运行。

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `dma_alloc_from_pool(caller, size, prot, ret_addr, phys)` | 从原子池分配 | 自动选择池 |
| `dma_free_from_pool(caller, addr, size)` | 释放回池 | 自动匹配所属池 |

### arm64 特性

- arm64 有 `CONFIG_ZONE_DMA`（zone_dma_bits=24，16MB）和 `CONFIG_ZONE_DMA32`
- `CONFIG_DMA_DIRECT_REMAP`：原子池分配被重映射为非缓存映射
- `arch_dma_prep_coherent()` 调用 `__flush_icache_all()` + `set_memory_valid()`

### 使用规模

`dma_alloc_from_pool` 4 处（4 个文件）、`dma_free_from_pool` 7 处（4 个文件）。调用者：`kernel/dma/direct.c`、`drivers/iommu/dma-iommu.c`。

---

## dma-buf — DMA 缓冲区共享框架

### 定位

**功能**：不同驱动（GPU、摄像头、显示器）需要共享同一块 DMA 缓冲区。dma-buf 提供基于 fd 的缓冲区共享协议：导出方分配内存，导入方通过 fd 获取映射。fence 机制保证跨驱动同步。

**实现方式**：核心结构体 `struct dma_buf`（`include/linux/dma-buf.h:302`）包含 anon inode 文件（用于引用计数和共享）、设备附加链表（`attachments`）、导出方回调（`ops`）和 dma_resv 预留对象（fence 同步）。导出方-导入方模型：导出方调 `dma_buf_export()` + `dma_buf_fd()` 创建缓冲区并获取 fd；导入方通过 `dma_buf_get(fd)` → `dma_buf_attach()` → `dma_buf_map_attachment()` 获取 sg_table 执行 DMA。支持静态映射（attach 时缓存）和动态映射（导入方处理 `move_notify` 缓冲区移动通知）。内部创建私有内核挂载 `dmabuf`，支持 poll/ioctl/mmap。

**应用场景**：DRM/KMS GPU 显示驱动（`drivers/gpu/drm/drm_prime.c`、`drivers/gpu/drm/virtio/`、`drivers/gpu/drm/vmwgfx/`）、videobuf2 媒体框架（`drivers/media/common/videobuf2/`）、fastrpc 远程处理器通信（`drivers/misc/fastrpc.c`）、dma_heap 用户空间内存分配（`drivers/dma-buf/heaps/`）、Xen gntdev DMA buf 共享（`drivers/xen/gntdev-dmabuf.c`）。`dma_buf_export` 27 处、`dma_buf_put` 54 处调用。

### 实现方式

核心结构体（`include/linux/dma-buf.h:302`）：

```c
struct dma_buf {
    size_t size;                    // 缓冲区大小
    struct file *file;              // 用于共享/引用计数的文件
    struct list_head attachments;   // 已附加设备链表
    const struct dma_buf_ops *ops;  // 导出方回调
    struct mutex lock;              // 序列化链表操作和 vmap
    unsigned vmapping_counter;      // vmap 引用计数
    struct iosys_map vmap_ptr;      // 当前 vmap 指针
    const char *exp_name;           // 导出方名称
    struct module *owner;
    struct list_head list_node;     // 全局 dma_buf 链表
    void *priv;                     // 导出方私有数据
    struct dma_resv *resv;          // 预留对象（fence）
    wait_queue_head_t poll;         // poll() 支持
};
```

**导出方/导入方模型**：

导出方（缓冲区拥有者）：
1. 分配后备存储
2. `dma_buf_export()` 创建 dma_buf
3. `dma_buf_fd()` 获取 fd
4. 传递 fd 给用户空间

导入方（缓冲区消费者）：
1. 从用户空间接收 fd
2. `dma_buf_get(fd)` 获取 `struct dma_buf`
3. `dma_buf_attach()` 注册设备
4. `dma_buf_map_attachment()` 获取设备映射的 sg_table
5. 用 sg_table 执行 DMA
6. `dma_buf_unmap_attachment()` → `dma_buf_detach()` → `dma_buf_put()`

**静态 vs 动态映射**：
- **静态**（默认）：`dma_buf_attach()` 传 NULL importer_ops。框架在 attach 时缓存映射
- **动态**：`dma_buf_dynamic_attach()` 传 importer_ops（含 `move_notify`）。导入方处理缓冲区移动通知

**内部文件系统**：dma-buf 创建私有内核挂载（`dma_buf_fs_type`，名 "dmabuf"）。每个 dma_buf 获得一个 anon inode。file_operations 包含 poll、ioctl（SYNC、SET_NAME、EXPORT/IMPORT_SYNC_FILE）、mmap。

### 主要 API

头文件：`include/linux/dma-buf.h`；实现：`drivers/dma-buf/dma-buf.c`

| API | 说明 | 备注 |
|-----|------|------|
| `dma_buf_export(exp_info)` | 创建 dma_buf | 含 ops、size、priv |
| `dma_buf_fd(dmabuf, flags)` | 获取 fd | |
| `dma_buf_get(fd)` | 从 fd 查找 dma_buf | fget + 验证 |
| `dma_buf_put(dmabuf)` | 释放引用 | 最后一个引用触发 release |
| `get_dma_buf(dmabuf)` | 增加引用 | inline：`get_file()` |
| `dma_buf_attach(dmabuf, dev)` | 附加设备（静态） | |
| `dma_buf_dynamic_attach(dmabuf, dev, ops, priv)` | 附加设备（动态） | |
| `dma_buf_detach(dmabuf, attach)` | 移除附加 | |
| `dma_buf_map_attachment(attach, dir)` | 获取 sg_table | 动态导出方需持 resv 锁 |
| `dma_buf_unmap_attachment(attach, sgt, dir)` | 取消映射 | |
| `dma_buf_pin(attach)` | 锁定缓冲区 | 仅动态导入方 |
| `dma_buf_unpin(attach)` | 解锁 | |
| `dma_buf_begin_cpu_access(dmabuf, dir)` | 开始 CPU 访问 | 等待 fence |
| `dma_buf_end_cpu_access(dmabuf, dir)` | 结束 CPU 访问 | |
| `dma_buf_mmap(dmabuf, vma)` | 用户空间 mmap | |
| `dma_buf_vmap(dmabuf, map)` | 内核虚拟映射 | 引用计数 |
| `dma_buf_vunmap(dmabuf, map)` | 取消 vmap | 引用计数 |
| `dma_buf_move_notify(dmabuf)` | 通知动态附加方缓冲区移动 | |

### Fence 同步集成

- **dma_resv**（预留对象）：每个 dma_buf 内嵌一个，包含 `ww_mutex` + RCU 保护的 `dma_fence` 数组
- **隐式同步**（`dma_buf_poll`）：用户空间 poll fd——`EPOLLIN` 等待写 fence 信号（可读）、`EPOLLOUT` 等待所有 fence 信号（可写）
- **显式同步**（`CONFIG_SYNC_FILE`）：`DMA_BUF_IOCTL_EXPORT_SYNC_FILE` / `IMPORT_SYNC_FILE`
- **CPU 访问同步**：`dma_buf_begin_cpu_access()` 通过 `dma_resv_wait_timeout()` 等待相关 fence

### 使用规模

`dma_buf_export` 27 处（16 个文件）、`dma_buf_put` 54 处（21 个文件）、`dma_buf_attach` 21 处（13 个文件）、`dma_buf_map_attachment` 28 处（15 个文件）、`dma_buf_vmap` 23 处（11 个文件）。主要用户：DRM/KMS（GPU 显示）、videobuf2（视频采集）、fastrpc（远程处理器通信）。

---

## 三者关系

```
dma_alloc_coherent()
  ├─ [可睡眠] dma_alloc_contiguous() → cma_alloc()     ← CMA
  └─ [原子]   dma_alloc_from_pool()                     ← dma-pool
                └─ [扩展] dma_alloc_from_contiguous() → cma_alloc()

dma-buf 在更高层运作：共享已分配的缓冲区（可能来自 CMA 或系统内存）
  └─ dma_heap (drivers/dma-buf/heaps/) 可直接用 cma_alloc() 分配
```

- **CMA** 提供物理连续内存
- **dma-pool** 提供原子上下文下的预分配 CMA/buddy 内存访问
- **dma-buf** 在更高层运作：通过 fd 在驱动间共享已分配的缓冲区，用 fence 做同步
