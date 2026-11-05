# DMA 映射（DMA Mapping）

> **定位**：在 CPU 物理地址与设备总线地址之间建立映射，使设备能通过 DMA 直接访问内存。核心问题：设备看到的地址 ≠ CPU 看到的地址（可能有 IOMMU 翻译），且 CPU cache 与设备之间需要显式同步。

---

## 1. 核心概念

### 1.1 两种 DMA 映射

| 类型 | 说明 | 典型 API |
|------|------|---------|
| **一致性映射（coherent）** | CPU 和设备始终看到一致数据，无需手动同步 | `dma_alloc_coherent` |
| **流式映射（streaming）** | 需要在使用前/后手动同步 cache | `dma_map_single` / `dma_sync_*` |

### 1.2 两条路径

```
dma_map_single / dma_alloc_coherent
    │
    ├── 设备有 IOMMU ──→ iommu_dma_ops（建立 IOVA 映射）
    │                      设备看到 IOVA，IOMMU 翻译到物理地址
    │
    └── 设备无 IOMMU ──→ 直接映射（物理地址 = 总线地址）
                           如果设备 DMA 掩码不够 → swiotlb 弹跳缓冲
```

---

## 2. `dma_map_ops` — 底层操作 vtable

`include/linux/dma-map-ops.h:22-80`：

```c
struct dma_map_ops {
    unsigned int flags;

    // 一致性分配/释放
    void *(*alloc)(dev, size, &dma_handle, gfp, attrs);
    void (*free)(dev, size, vaddr, dma_handle, attrs);

    // 页分配
    struct page *(*alloc_pages)(dev, size, &dma_handle, dir, gfp);
    void (*free_pages)(dev, size, page, dma_handle, dir);

    // 流式映射
    dma_addr_t (*map_page)(dev, page, offset, size, dir, attrs);
    void (*unmap_page)(dev, dma_handle, size, dir, attrs);
    int (*map_sg)(dev, sg, nents, dir, attrs);
    void (*unmap_sg)(dev, sg, nents, dir, attrs);

    // Cache 同步
    void (*sync_single_for_cpu)(dev, dma_handle, size, dir);
    void (*sync_single_for_device)(dev, dma_handle, size, dir);
    void (*sync_sg_for_cpu)(dev, sg, nents, dir);
    void (*sync_sg_for_device)(dev, sg, nents, dir);

    // 配置
    int (*dma_supported)(dev, mask);
    int (*mmap)(dev, vma, cpu_addr, dma_addr, size, attrs);
    // ...
};
```

`get_dma_ops(dev)` 返回设备当前的 ops——由 `arch_setup_dma_ops()` 在设备初始化时设置。

### 2.1 IOMMU DMA ops

`drivers/iommu/dma-iommu.c:1558-1581`：

```c
static const struct dma_map_ops iommu_dma_ops = {
    .alloc          = iommu_dma_alloc,
    .free           = iommu_dma_free,
    .alloc_pages    = iommu_dma_alloc_pages,
    .map_page       = iommu_dma_map_page,
    .unmap_page     = iommu_dma_unmap_page,
    .map_sg         = iommu_dma_map_sg,
    .unmap_sg       = iommu_dma_unmap_sg,
    .sync_single_for_cpu    = iommu_dma_sync_single_for_cpu,
    .sync_single_for_device = iommu_dma_sync_single_for_device,
    // ...
};
```

---

## 3. 驱动侧 API

头文件：`include/linux/dma-mapping.h`（611 行）；核心实现：`kernel/dma/mapping.c`（831 行）

### 3.1 一致性分配

| API | 说明 | 备注 |
|-----|------|------|
| `dma_alloc_coherent(dev, size, &dma_handle, gfp)` | 分配一致性 DMA 缓冲区 | 返回 CPU 虚拟地址，`dma_handle` 是设备地址 |
| `dma_free_coherent(dev, size, cpu_addr, dma_handle)` | 释放 | |
| `dmam_alloc_coherent(dev, size, &dma_handle, gfp)` | devres 版本 | detach 自动释放 |
| `dmam_free_coherent(dev, size, vaddr, dma_handle)` | | |

全树使用量：`dma_alloc_coherent` 1,821 处。

### 3.2 流式映射

| API | 说明 | 备注 |
|-----|------|------|
| `dma_map_single(dev, ptr, size, dir)` | 映射连续内核缓冲区 | 返回 DMA 地址 |
| `dma_unmap_single(dev, addr, size, dir)` | 解除映射 | |
| `dma_map_page(dev, page, offset, size, dir)` | 映射一页 | |
| `dma_unmap_page(dev, addr, size, dir)` | | |
| `dma_map_sg(dev, sg, nents, dir)` | 映射 scatterlist | 返回映射的段数 |
| `dma_unmap_sg(dev, sg, nents, dir)` | | |
| `dma_map_sgtable(dev, sgt, dir, attrs)` | 映射 `sg_table` | 新版推荐接口 |

全树使用量：`dma_map_single` 1,209 处，`dma_map_sg` 389 处。

### 3.3 Cache 同步

| API | 说明 |
|-----|------|
| `dma_sync_single_for_cpu(dev, addr, size, dir)` | 设备写完 → CPU 读之前 |
| `dma_sync_single_for_device(dev, addr, size, dir)` | CPU 写完 → 设备读之前 |
| `dma_sync_sg_for_cpu(dev, sg, nents, dir)` | scatterlist 版本 |
| `dma_sync_sg_for_device(dev, sg, nents, dir)` | |

**方向**（`enum dma_data_direction`）：
- `DMA_TO_DEVICE`：CPU → 设备（只需 clean cache）
- `DMA_FROM_DEVICE`：设备 → CPU（只需 invalidate cache）
- `DMA_BIDIRECTIONAL`：双向（clean + invalidate）

### 3.4 DMA 掩码设置

| API | 说明 |
|-----|------|
| `dma_set_mask(dev, mask)` | 设置流式 DMA 地址范围 |
| `dma_set_coherent_mask(dev, mask)` | 设置一致性 DMA 地址范围 |
| `dma_set_mask_and_coherent(dev, mask)` | 同时设置两者 |
| `dma_coerce_mask_and_coherent(dev, mask)` | 强制设置（含修正 dma_mask 指针） |
| `DMA_BIT_MASK(n)` | 生成 n 位掩码 |

**典型用法**：
```c
// 声明设备支持 64 位 DMA
dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
```

### 3.5 便捷宏

```c
#define dma_map_single(d, a, s, r)      dma_map_single_attrs(d, a, s, r, 0)
#define dma_unmap_single(d, a, s, r)    dma_unmap_single_attrs(d, a, s, r, 0)
#define dma_map_sg(d, s, n, r)          dma_map_sg_attrs(d, s, n, r, 0)
#define dma_unmap_sg(d, s, n, r)        dma_unmap_sg_attrs(d, s, n, r, 0)
```

### 3.6 错误检查

```c
if (dma_mapping_error(dev, dma_addr))
    // 映射失败
```

`DMA_MAPPING_ERROR` = `~(dma_addr_t)0`。

---

## 4. arm64 特殊性

### 4.1 Cache 同步实现

`arch/arm64/mm/dma-mapping.c`（80 行）：

```c
void arch_sync_dma_for_device(phys_addr_t paddr, size_t size,
                              enum dma_data_direction dir)
{
    unsigned long start = (unsigned long)phys_to_virt(paddr);
    dcache_clean_poc(start, start + size);     // clean to Point of Coherency
}

void arch_sync_dma_for_cpu(phys_addr_t paddr, size_t size,
                           enum dma_data_direction dir)
{
    if (dir == DMA_TO_DEVICE)
        return;                                 // 写方向不需要 invalidate
    unsigned long start = (unsigned long)phys_to_virt(paddr);
    dcache_inval_poc(start, start + size);      // invalidate
}
```

arm64 是弱序架构，cache 同步**必须**做：
- `DMA_TO_DEVICE`：clean（把 CPU cache 写回内存）
- `DMA_FROM_DEVICE`：invalidate（丢弃旧 cache 行，下次读从内存取）
- `DMA_BIDIRECTIONAL`：clean + invalidate

### 4.2 DMA 一致性配置

`arch_setup_dma_ops()`（`dma-mapping.c:64-80`）：
- 根据 DT 的 `dma-coherent` 属性设置 `dev->dma_coherent`
- 如果有 IOMMU，调 `iommu_setup_dma_ops()` 建立 IOMMU DMA 域
- 检查 `ARCH_DMA_MINALIGN` 是否大于 CPU cache line 大小

### 4.3 IOMMU 路径

arm64 上大多数 SoC 外设通过 SMMU（System MMU）访问内存：
1. `bus->dma_configure()` → `of_dma_configure()` → `iommu_setup_dma_ops()`
2. 设备的 `dma_ops` 被设为 `iommu_dma_ops`
3. `dma_map_single` → `iommu_dma_map_page` → 分配 IOVA + 建立 SMMU 映射

### 4.4 SWIOTLB 弹跳缓冲

当设备 DMA 掩码不够大（无法寻址全部物理内存）时：
- `kernel/dma/swiotlb.c` 管理弹跳缓冲区
- 默认 64MB（`io_tlb_default_mem`）
- 数据先拷贝到弹跳区（在设备可寻址范围内），再让设备 DMA 弹跳区

---

## 5. DMA 属性

`include/linux/dma-mapping.h:19-63`：

| 属性 | 说明 |
|------|------|
| `DMA_ATTR_WEAK_ORDERING` | 允许弱序访问 |
| `DMA_ATTR_WRITE_COMBINE` | 写合并（WC）映射 |
| `DMA_ATTR_NO_KERNEL_MAPPING` | 不创建内核虚拟映射 |
| `DMA_ATTR_SKIP_CPU_SYNC` | 跳过 cache 同步（调用者保证已同步） |
| `DMA_ATTR_FORCE_CONTIGUOUS` | 强制物理连续分配 |
| `DMA_ATTR_NO_WARN` | 抑制分配失败警告 |

---

## 6. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `dma_alloc_coherent` | 1,821 | 一致性分配 |
| `dma_map_single` | 1,209 | 流式映射 |
| `dma_map_sg` | 389 | scatterlist 映射 |

---

## §4 常见陷阱

### 陷阱 1：对 vmalloc 地址调用 dma_map_single

**错误写法**：
```c
void *buf = vmalloc(SZ_64K);
dma_addr_t dma = dma_map_single(dev, buf, SZ_64K, DMA_TO_DEVICE);
/* dev_WARN_ONCE 触发，返回 DMA_MAPPING_ERROR */
```

**正确写法**：
```c
/* 方案 A：使用 kmalloc（物理连续） */
void *buf = kmalloc(SZ_64K, GFP_KERNEL);
dma_addr_t dma = dma_map_single(dev, buf, SZ_64K, DMA_TO_DEVICE);

/* 方案 B：使用 dma_map_sgtable 配合 vmalloc_to_page */
/* 方案 C：使用 dma_alloc_coherent 分配 DMA 缓冲区 */
```

**原因**：`dma_map_single_attrs()` 显式检查 `is_vmalloc_addr(ptr)`，如果为真则触发 `dev_WARN_ONCE` 并返回 `DMA_MAPPING_ERROR`（`include/linux/dma-mapping.h:332-334`）。vmalloc 内存不是物理连续的，`dma_map_single` 内部调用 `virt_to_page()` + `offset_in_page()` 只能处理直接映射的线性地址。对 vmalloc 地址使用 `virt_to_page()` 会得到错误的 struct page。

**源码依据**：`include/linux/dma-mapping.h:332-334` — `dev_WARN_ONCE(dev, is_vmalloc_addr(ptr), "rejecting DMA map of vmalloc memory\n")`。

### 陷阱 2：dma_map_sg 返回值被当作原始 nents 使用

**错误写法**：
```c
int ret = dma_map_sg(dev, sg, nents, DMA_TO_DEVICE);
/* 假设 ret == nents */
for (i = 0; i < nents; i++)
    hw_desc[i].addr = sg_dma_address(&sg[i]);
/* 错误！IOMMU 可能合并了 entry，ret < nents */
```

**正确写法**：
```c
int mapped = dma_map_sg(dev, sg, nents, DMA_TO_DEVICE);
if (mapped <= 0)
    return -EIO;
/* 用 mapped（不是 nents）遍历 DMA 映射后的 entry */
for (i = 0; i < mapped; i++)
    hw_desc[i].addr = sg_dma_address(&sg[i]);

/* 提交给硬件... */

/* unmap 时必须传原始 nents，不是 mapped */
dma_unmap_sg(dev, sg, nents, DMA_TO_DEVICE);
```

**原因**：`dma_map_sg_attrs()` 返回实际映射的段数（`kernel/dma/mapping.c:227-236`），IOMMU 驱动会将物理连续的相邻 scatterlist entry 合并为一个 DMA 段。返回值可能 < `nents`。但 `dma_unmap_sg_attrs()` 必须使用原始的 `nents`（`kernel/dma/mapping.c:279-293`），因为底层实现需要遍历原始 scatterlist 结构来清理映射。使用 `dma_map_sgtable()` 可以避免此混淆——它将映射后的段数存在 `sgt->nents`，原始数量在 `sgt->orig_nents`。

**源码依据**：`kernel/dma/mapping.c:227-236` — 返回 `ret`（可能 < `nents`）；`kernel/dma/mapping.c:279` — `dma_unmap_sg_attrs` 使用传入的 `nents`。

### 陷阱 3：流式映射后未同步就访问缓冲区

**错误写法**：
```c
dma_addr_t dma = dma_map_single(dev, buf, size, DMA_FROM_DEVICE);
/* 启动 DMA 传输... */
wait_for_dma_complete();
/* 直接读取 buf —— 可能读到 CPU cache 中的旧数据 */
data = buf[0];
dma_unmap_single(dev, dma, size, DMA_FROM_DEVICE);
```

**正确写法**：
```c
dma_addr_t dma = dma_map_single(dev, buf, size, DMA_FROM_DEVICE);
/* 启动 DMA 传输... */
wait_for_dma_complete();
/* 先同步给 CPU */
dma_sync_single_for_cpu(dev, dma, size, DMA_FROM_DEVICE);
/* 现在可以安全读取 buf */
data = buf[0];
/* 使用完毕后 unmap */
dma_unmap_single(dev, dma, size, DMA_FROM_DEVICE);
```

**原因**：arm64 是弱序架构，CPU cache 与 DMA 设备之间不自动一致。`DMA_FROM_DEVICE` 传输完成后，设备写入的数据在内存中，但 CPU cache 可能还保存着旧数据。`dma_sync_single_for_cpu()` 调用 `arch_sync_dma_for_cpu()` 执行 cache invalidate（`arch/arm64/mm/dma-mapping.c`：`dcache_inval_poc`），丢弃旧 cache 行。注意：如果不需要在 unmap 前访问数据，`dma_unmap_single` 本身会处理同步——只有在 unmap 之前需要读数据时才需要显式 sync。

**源码依据**：`kernel/dma/mapping.c:328-339` — `dma_sync_single_for_cpu` 分派到 `ops->sync_single_for_cpu` 或 `dma_direct_sync_single_for_cpu`。

### 陷阱 4：CPU 写完数据后未同步就给设备

**错误写法**：
```c
dma_addr_t dma = dma_map_single(dev, buf, size, DMA_TO_DEVICE);
/* 修改 buf 内容 */
memset(buf, 0xAA, size);
/* 直接让设备读取——设备可能看到旧数据（CPU cache 未刷回内存） */
start_device_read(dma);
```

**正确写法**：
```c
/* 先填好数据 */
memset(buf, 0xAA, size);
/* 然后映射（映射时会自动同步给设备） */
dma_addr_t dma = dma_map_single(dev, buf, size, DMA_TO_DEVICE);
/* 映射已包含 cache clean，设备能看到正确数据 */
start_device_read(dma);

/* 如果映射后还需要修改数据： */
dma_sync_single_for_cpu(dev, dma, size, DMA_TO_DEVICE);
memset(buf + offset, 0xBB, len);
dma_sync_single_for_device(dev, dma, size, DMA_TO_DEVICE);
start_device_read(dma);
```

**原因**：`dma_map_single` 在映射时调用 `arch_sync_dma_for_device()` 执行 cache clean（`arch/arm64/mm/dma-mapping.c`：`dcache_clean_poc`），将 CPU cache 数据写回内存。但如果映射后又修改了缓冲区，新数据只在 CPU cache 中——设备看到的仍是映射时 clean 过的旧数据。必须再次调用 `dma_sync_single_for_device()` 刷 cache。

**源码依据**：`kernel/dma/mapping.c:342-354` — `dma_sync_single_for_device` 分派到 `arch_sync_dma_for_device`。

### 陷阱 5：在 IRQ 上下文中调用 dma_free_coherent

**错误写法**：
```c
static irqreturn_t my_irq_handler(int irq, void *data)
{
    /* 处理完毕，释放 DMA 缓冲区 */
    dma_free_coherent(dev, size, cpu_addr, dma_handle);
    /* WARN_ON(irqs_disabled()) 触发！ */
    return IRQ_HANDLED;
}
```

**正确写法**：
```c
static irqreturn_t my_irq_handler(int irq, void *data)
{
    /* 标记需要释放，调度 workqueue 处理 */
    schedule_work(&cleanup_work);
    return IRQ_HANDLED;
}

static void cleanup_work_fn(struct work_struct *work)
{
    /* 在进程上下文中安全释放 */
    dma_free_coherent(dev, size, cpu_addr, dma_handle);
}
```

**原因**：`dma_free_attrs()` 包含 `WARN_ON(irqs_disabled())`（`kernel/dma/mapping.c:533`）。在非 cache 一致的 arm64 平台上，`ops->free()` 可能调用 `vunmap()`，这在 IRQ 上下文中会睡眠或触发 `BUG_ON()`。即使 cache 一致平台不触发实际问题，此警告也表明驱动在错误上下文中释放内存。

**源码依据**：`kernel/dma/mapping.c:533` — `WARN_ON(irqs_disabled())`。

### 陷阱 6：dma_set_mask 未检查 dev->dma_mask 指针

**错误写法**：
```c
/* 设备未初始化 dma_mask 指针 */
dma_set_mask(&pdev->dev, DMA_BIT_MASK(64));
/* 如果 dev->dma_mask == NULL，静默失败返回 -EIO */
```

**正确写法**：
```c
/* 先确保 dma_mask 指针有效 */
pdev->dev.dma_mask = &pdev->dev.coherent_dma_mask;
/* 或使用便捷函数 */
dma_coerce_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
```

**原因**：`dma_set_mask()` 检查 `!dev->dma_mask` 时直接返回 `-EIO`（`kernel/dma/mapping.c:761`）。如果设备的 `dma_mask` 指针未初始化（为 NULL），设置掩码会失败。`dma_coerce_mask_and_coherent()`（`include/linux/dma-mapping.h:460-464`）自动将 `dev->dma_mask` 指向 `dev->coherent_dma_mask`，解决此问题。

**源码依据**：`kernel/dma/mapping.c:761` — `if (!dev->dma_mask || !dma_supported(dev, mask)) return -EIO`；`include/linux/dma-mapping.h:460-464` — `dma_coerce_mask_and_coherent` 设置指针。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/dma-mapping.h` — 驱动侧 API 定义、`dma_map_single_attrs` 的 vmalloc 检查、`dma_set_mask_and_coherent`/`dma_coerce_mask_and_coherent` 便捷函数、DMA 属性常量
- `kernel/dma/mapping.c` — 核心实现：`dma_map_page_attrs`/`dma_map_sg_attrs`/`dma_sync_*`/`dma_alloc_attrs`/`dma_free_attrs`/`dma_set_mask`，直接映射 vs IOMMU 分派逻辑
- `arch/arm64/mm/dma-mapping.c`（通过文档引用）— `arch_sync_dma_for_device`（clean）和 `arch_sync_dma_for_cpu`（invalidate）实现

**关键论断出处**：
- "`dma_map_single` 拒绝 vmalloc 地址" — 来源：`include/linux/dma-mapping.h:332-334`
- "`dma_map_sg` 返回值可能 < nents" — 来源：`kernel/dma/mapping.c:227-236`
- "`dma_unmap_sg` 必须使用原始 nents" — 来源：`kernel/dma/mapping.c:279-293`
- "`dma_free_attrs` 在 IRQ 上下文中触发 WARN_ON" — 来源：`kernel/dma/mapping.c:533`
- "`dma_set_mask` 检查 `dev->dma_mask` 非 NULL" — 来源：`kernel/dma/mapping.c:761`
- "arm64 `DMA_TO_DEVICE` 只需 clean，`DMA_FROM_DEVICE` 只需 invalidate" — 来源：`arch/arm64/mm/dma-mapping.c`（文档 §4.1 引用）

**未验证项**：
- `iommu_dma_map_sg()` 的 entry 合并逻辑（`drivers/iommu/dma-iommu.c` 中实现）
- SWIOTLB 弹跳缓冲的完整拷贝路径（`kernel/dma/swiotlb.c`）
- `dma_alloc_attrs` 中 `dma_alloc_from_dev_coherent` 的设备私有内存池逻辑
