---
title: API 参考
---

# dma-mapping 代码地图与对外 API

本篇管两件事：**找文件**和**找 API**。代码基线 Linux 6.1.158，arm64，ACPI 路径一律裁掉（裁剪清单在 99 §2.7）。

⚠️ 先确认你要的是哪个子系统。内核里三个名字都带 dma 的子系统互不相同：

| 子系统 | 目录 | 管什么 | 本项目文档集 |
|---|---|---|---|
| **dma-mapping** | `kernel/dma/` | 给设备算 DMA 地址、做缓存一致性、必要时搬一次数据 | 本套 |
| dmaengine | `drivers/dma/` | DMA 控制器（搬运引擎）的驱动框架，`dmaengine_prep_*` 那一层 | `output/dmaengine/` |
| dma-buf | `drivers/dma-buf/` | 缓冲区跨驱动、跨进程共享，`dma_buf_fd()` 那一层 | `output/dma-buf/` |

三者会在同一个驱动里同时出现：用 dma-mapping 拿到一块 DMA 可达的内存，用 dmaengine 让控制器去搬它，用 dma-buf 把它交给另一个驱动。

本篇的 API 表遵循全套约定：**签名只写函数名 + 形参名，位置只到文件不到行**。行号集中在 03 篇和 99 篇维护。

---

## §0 代码地图

### §0.1 `kernel/dma/` 的文件分工

共 **11 个 `.c` + 2 个 `.h` = 6182 行**（不含 Kconfig 244 行、Makefile 12 行）。

| 文件 | 行数 | 管什么 | 编译条件 |
|---|---|---|---|
| `kernel/dma/mapping.c:1` | 831 | **唯一的对外门面**。34 个导出符号全在这里，负责在"直接路径"和 `dma_map_ops` 之间分流 | 无条件（`CONFIG_HAS_DMA`） |
| `kernel/dma/direct.c:1` | 657 | 没有 IOMMU 时的实现：算偏移、判断可达性、决定要不要 bounce | 无条件 |
| `kernel/dma/swiotlb.c:1` | 1112 | 软件 IOMMU（bounce buffer 池），设备寻址不到时把数据搬到低地址 | `CONFIG_SWIOTLB` |
| `kernel/dma/debug.c:1` | 1608 | DMA-API 用法检查器：泄漏、越界、重复释放、方向不匹配 | `CONFIG_DMA_API_DEBUG` |
| `kernel/dma/contiguous.c:1` | 441 | CMA 区域的创建与分配（大块物理连续内存） | `CONFIG_DMA_CMA` |
| `kernel/dma/coherent.c:1` | 403 | per-device 一致性内存池（dts 里 `shared-dma-pool` 且无 `reusable`） | `CONFIG_DMA_DECLARE_COHERENT` |
| `kernel/dma/map_benchmark.c:1` | 378 | 映射/解映射吞吐的压测模块 | `CONFIG_DMA_MAP_BENCHMARK` |
| `kernel/dma/pool.c:1` | 295 | 原子上下文可用的一致性内存池（预分配好，中断里也能拿） | `CONFIG_DMA_COHERENT_POOL` |
| `kernel/dma/ops_helpers.c:1` | 93 | 给 `dma_map_ops` 实现者复用的三段样板（get_sgtable / mmap / alloc_pages） | `CONFIG_DMA_OPS` |
| `kernel/dma/remap.c:1` | 70 | 把一堆页重新映射成一段虚拟连续、且可改属性的区间 | `CONFIG_DMA_DIRECT_REMAP` |
| `kernel/dma/dummy.c:1` | 38 | 一整套全部返回失败的 ops，用来把设备标成"禁止 DMA" | 无条件 |
| `kernel/dma/direct.h:1` | 126 | direct 路径的内联热路径（`dma_direct_map_page` 等） | 私有头 |
| `kernel/dma/debug.h:1` | 130 | debug 钩子的声明，关闭时全是空 `static inline` | 私有头 |

读法：**只想懂主线就读 `mapping.c` + `direct.c` + `direct.h`（1614 行）**，这三者覆盖 arm64 无 IOMMU 时的全部行为。`swiotlb.c` 和 `debug.c` 是两个自成一体的可选件，读它们之前先读 04 篇的分诊表。

### §0.2 头文件分层

| 头文件 | 行数 | 给谁看 |
|---|---|---|
| `include/linux/dma-mapping.h:1` | 611 | **设备驱动作者**。全部 `DMA_ATTR_*`、`dma_map_single` 一类内联封装、掩码设置函数 |
| `include/linux/dma-map-ops.h:1` | 449 | **`dma_map_ops` 实现者和体系结构代码**。普通驱动不该 include 它 |
| `include/linux/dma-direct.h:1` | 127 | 直接路径的地址翻译（`dma_range_map`、`phys_to_dma` 家族） |
| `include/linux/swiotlb.h:1` | 190 | swiotlb 的对外面，主要给 IOMMU 层和 xen 用 |
| `include/linux/scatterlist.h:1` | 635 | `struct scatterlist` / `sg_table` 及其遍历宏。⚠️ 不属于 `kernel/dma/`，但 sg 类 API 全靠它 |
| `include/linux/dma-direction.h:1` | 18 | 只有 `enum dma_data_direction` 和 `valid_dma_direction()` |

⚠️ **`include/linux/dma-map-ops.h` 是一道分界线。** 一个普通设备驱动如果 include 了它，基本可以断定用错了：它里面的 `set_dma_ops()`、`dma_alloc_from_dev_coherent()`、`arch_sync_dma_for_device()` 是给平台代码和 IOMMU 层用的。判据见 05 §11。

arm64 侧只有一个文件：

| 文件 | 行数 | 内容 |
|---|---|---|
| `arch/arm64/mm/dma-mapping.c:1` | 80 | 四个 arch 钩子：`arch_sync_dma_for_device`、`arch_sync_dma_for_cpu`、`arch_dma_prep_coherent`、`arch_setup_dma_ops`/`arch_teardown_dma_ops` |

⚠️ **arm64 没有 `arch/arm64/include/asm/dma-mapping.h`**，也没有在 `arch/arm64/include/asm/Kbuild` 里为它写 `generic-y`。它靠 `include/asm-generic/Kbuild:20` 的 `mandatory-y += dma-mapping.h` 落到 `include/asm-generic/dma-mapping.h`，那里的 `get_arch_dma_ops()` 恒返回 NULL（`include/asm-generic/dma-mapping.h:5-8`）。含义：**arm64 上没有"整个体系结构统一的 dma_ops"，只有 per-device 的**。详见 03 §B1。

### §0.3 Kconfig 速查

全部在 `kernel/dma/Kconfig`。分三类：

**（一）体系结构声明自己有什么**（`arch/arm64/Kconfig` 里 `select`，驱动作者不用管）：

| 选项 | 位置 | arm64 | 含义 |
|---|---|---|---|
| `ARCH_HAS_SYNC_DMA_FOR_DEVICE` | `kernel/dma/Kconfig:63` | 选（`arch/arm64/Kconfig:43`） | 提供"交给设备前"的缓存维护 |
| `ARCH_HAS_SYNC_DMA_FOR_CPU` | `kernel/dma/Kconfig:66` | 选（`arch/arm64/Kconfig:44`） | 提供"收回给 CPU 前"的缓存维护 |
| `ARCH_HAS_DMA_PREP_COHERENT` | `kernel/dma/Kconfig:73` | 选（`arch/arm64/Kconfig:25`） | 提供一致性内存的首次清理 |
| `ARCH_HAS_SETUP_DMA_OPS` | `kernel/dma/Kconfig:57` | 选（`arch/arm64/Kconfig:37`） | 提供 `arch_setup_dma_ops()` |
| `ARCH_HAS_TEARDOWN_DMA_OPS` | `kernel/dma/Kconfig:60` | 选（`arch/arm64/Kconfig:46`，条件 `IOMMU_SUPPORT`） | 提供 `arch_teardown_dma_ops()` |
| `NEED_DMA_MAP_STATE` | `kernel/dma/Kconfig:30` | 选（`arch/arm64/Kconfig:218`） | `DEFINE_DMA_UNMAP_ADDR` 走"真存字段"那套 |
| `NEED_SG_DMA_LENGTH` | `kernel/dma/Kconfig:27` | 选（`arch/arm64/Kconfig:219`） | `struct scatterlist` 带独立的 `dma_length` 字段 |
| `ARCH_HAS_ZONE_DMA_SET` | ⚠️ 在 `mm/Kconfig:973`，不在本子系统 | 选（`arch/arm64/Kconfig:48`，条件 `EXPERT`） | ZONE_DMA / ZONE_DMA32 可配 |

**（二）可选机制，会改变行为**：

| 选项 | 位置 | 依赖/选中关系 | 打开后 |
|---|---|---|---|
| `SWIOTLB` | `kernel/dma/Kconfig:79` | arm64 无条件 select（`arch/arm64/Kconfig:228`） | 有 bounce buffer 兜底 |
| `DMA_RESTRICTED_POOL` | `kernel/dma/Kconfig:83` | `OF && OF_RESERVED_MEM && SWIOTLB` | dts 可声明 per-device 的受限 bounce 池 |
| `DMA_DIRECT_REMAP` | `kernel/dma/Kconfig:113` | arm64 无条件 select（`arch/arm64/Kconfig:120`）；它自己 select 下面两个 | 非一致性设备的一致性分配走 remap |
| `DMA_COHERENT_POOL` | `kernel/dma/Kconfig:105` | 被 `DMA_DIRECT_REMAP` select | 原子上下文也能 `dma_alloc_coherent` |
| `DMA_NONCOHERENT_MMAP` | `kernel/dma/Kconfig:101` | 被 `DMA_DIRECT_REMAP` select | 非一致性内存也允许 mmap 给用户态 |
| `DMA_CMA` | `kernel/dma/Kconfig:118` | 需 `HAVE_DMA_CONTIGUOUS`（`arch/arm64/Kconfig:182`） | 大块连续分配从 CMA 拿 |
| `DMA_PERNUMA_CMA` | `kernel/dma/Kconfig:134` | —— | 每个 NUMA 节点一个 CMA 池 |
| `DMA_DECLARE_COHERENT` | `kernel/dma/Kconfig:54` | 被 `OF_RESERVED_MEM` 之类间接触发 | 支持 per-device 一致性池 |
| `DMA_GLOBAL_POOL` | `kernel/dma/Kconfig:109` | 与 `DMA_DIRECT_REMAP` **互斥**（见 04 §5） | 全局一个一致性池，无 MMU 场景用 |
| `DMA_OPS` | `kernel/dma/Kconfig:11` | 只被 `drivers/iommu`、`drivers/xen`、`drivers/vdpa` select | `dev->dma_ops` 这条间接层才存在 |
| `DMA_OPS_BYPASS` | `kernel/dma/Kconfig:20` | 无 arm64 使用者 | 有 ops 时也允许对特定设备走直接路径 |

⚠️ **`CONFIG_DMA_OPS` 不是 arm64 自己选的。** arm64 内核如果没编 IOMMU、没编 xen、没编 vdpa，`get_dma_ops()` 直接是个返回 NULL 的空壳（`include/linux/dma-map-ops.h` 的 `#else` 分支），整条 ops 间接层被编译掉。**这解释了为什么在 arm64 上"设备没有 dma_ops"是常态而不是异常**。

**（三）调试与压测**（详见 07 篇）：

| 选项 | 位置 | 打开后 |
|---|---|---|
| `DMA_API_DEBUG` | `kernel/dma/Kconfig:206` | 每次映射记一条账，出错时打印栈；有明显性能代价 |
| `DMA_API_DEBUG_SG` | `kernel/dma/Kconfig:220` | 额外校验 sg 每段的长度与边界，默认 y |
| `DMA_MAP_BENCHMARK` | `kernel/dma/Kconfig:237` | 出 `/sys/kernel/debug/dma_map_benchmark` |
| `CMA_SIZE_MBYTES` / `CMA_SIZE_PERCENTAGE` / `CMA_ALIGNMENT` | `kernel/dma/Kconfig:146` / `:157` / `:186` | CMA 默认大小与对齐 |

---

## §1 对外 API

分组原则：**按"你想干什么"分，不按文件分**。每组开头给出头文件与实现文件。

一致性设备（arm64 上 dts 带 `dma-coherent`，或走 IOMMU）和非一致性设备用的是**同一套 API**——差别全在实现里，驱动不需要分支。这是这套 API 最核心的设计承诺。

### §1.1 掩码与能力协商（probe 里第一件事）

头文件：`include/linux/dma-mapping.h`；实现：`kernel/dma/mapping.c`。

| API | 说明 | 备注 |
|---|---|---|
| `dma_set_mask(dev, mask)` | 声明设备的流式 DMA 寻址能力 | 导出；失败返回 `-EIO`，此时**不能**继续用 DMA |
| `dma_set_coherent_mask(dev, mask)` | 声明一致性分配的寻址能力 | 导出；同上 |
| `dma_set_mask_and_coherent(dev, mask)` | 两个一起设 | `static inline`，**不在 `Module.symvers`**；只检查前者的返回值，理由见函数上方注释 |
| `dma_coerce_mask_and_coherent(dev, mask)` | 同上，但顺便把 `dev->dma_mask` 指到 `dev->coherent_dma_mask` | `static inline`；⚠️ 只有"设备的 `dma_mask` 指针没人给我设"时才该用，见 §6 |
| `dma_get_mask(dev)` | 读回当前流式掩码 | `static inline`；⚠️ `dma_mask` 为空或为 0 时**返回 32 位掩码**而不是 0 |
| `dma_get_required_mask(dev)` | 问"要覆盖本机全部内存需要多少位" | 导出 `_GPL` |
| `dma_addressing_limited(dev)` | 问"这个设备是否寻址不全" | `static inline`；实现就是 `min_not_zero(掩码, bus_dma_limit) < 需要的掩码` |
| `dma_supported(dev, mask)` | —— | ⚠️ **`static`，不导出**（`kernel/dma/mapping.c` 内部），驱动用不到；别和 `dma_set_mask` 混 |

⚠️ `DMA_BIT_MASK(n)` 是宏（`include/linux/dma-mapping.h:77`），`n == 64` 时特判成 `~0ULL`——因为 `1ULL << 64` 是未定义行为。别自己手写 `(1ULL << n) - 1`。

多档掩码的正确写法（先试大的、逐档降、最后一次调用必须是最终生效的那一档）在 `Documentation/core-api/dma-api-howto.rst:149-274`，骨架见 05 §2。

### §1.2 一致性分配（coherent / consistent）

头文件：`include/linux/dma-mapping.h`；实现：`kernel/dma/mapping.c`。

"一致性"的含义：**分配出来之后，CPU 和设备可以随时各自访问，不需要显式同步**。代价是这块内存的属性被改过（arm64 上通常是 non-cacheable 或走 remap），CPU 访问比普通内存慢。

| API | 说明 | 备注 |
|---|---|---|
| `dma_alloc_coherent(dev, size, dma_handle, gfp)` | 分配一块 CPU/设备都能直接看的内存 | `static inline`，转发到 `dma_alloc_attrs(..., 0)`；**失败返回 NULL**，不是错误指针 |
| `dma_free_coherent(dev, size, cpu_addr, dma_handle)` | 释放 | `static inline`；`size` 必须与分配时**逐字节相同** |
| `dma_alloc_attrs(dev, size, dma_handle, gfp, attrs)` | 带 `DMA_ATTR_*` 的版本 | 导出；上面两个的实体 |
| `dma_free_attrs(dev, size, cpu_addr, dma_handle, attrs)` | 同上的释放 | 导出；⚠️ `attrs` 要与分配时一致，`DMA_ATTR_NO_KERNEL_MAPPING` 尤其 |
| `dma_alloc_wc(dev, size, dma_handle, gfp)` | write-combine 属性的分配 | `static inline`，等价于带 `DMA_ATTR_WRITE_COMBINE` |
| `dma_free_wc` / `dma_mmap_wc` | 对应的释放与 mmap | 三个都是 `static inline` |
| `dma_can_mmap(dev)` | 问"这块设备的一致性内存能不能 mmap 给用户态" | 导出 `_GPL` |

⚠️ **`gfp` 里不要带 `GFP_DMA` / `GFP_DMA32`**。掩码已经告诉了核心该从哪个 zone 拿，`gfp` 里再指定一遍只会互相打架。这条在 `Documentation/core-api/dma-api.rst` 里反复强调。

⚠️ **`dma_alloc_coherent` 能在原子上下文调用，前提是 `CONFIG_DMA_COHERENT_POOL`**（arm64 由 `DMA_DIRECT_REMAP` 间接打开）。但池子是预分配的、有限的，从中断里大量分配会耗尽。判定逻辑在 `kernel/dma/direct.c:166`（`dma_direct_use_pool`），详见 04 §4。

### §1.3 流式映射：单块内存

头文件：`include/linux/dma-mapping.h`；实现：`kernel/dma/mapping.c`。

"流式"的含义：**一块已有的普通内存，临时借给设备用一次**。映射时做一次缓存维护，解映射时再做一次，中间 CPU 不许碰。

| API | 说明 | 备注 |
|---|---|---|
| `dma_map_single(dev, ptr, size, dir)` | 用内核虚拟地址映射一块内存 | 宏，展开到 `dma_map_single_attrs(..., 0)` |
| `dma_map_single_attrs(dev, ptr, size, dir, attrs)` | 带属性版 | `static inline`；⚠️ 里面有 `dev_WARN_ONCE(is_vmalloc_addr(ptr))` 直接拒绝 vmalloc 地址 |
| `dma_unmap_single(dev, addr, size, dir)` | 解映射 | 宏；`size`/`dir` 必须与映射时相同 |
| `dma_map_page(dev, page, offset, size, dir)` | 用 `(page, 偏移)` 映射 | 宏；**HIGHMEM 页只能走这条**，`dma_map_single` 走不了 |
| `dma_map_page_attrs(...)` | 带属性版 | **导出**，是上面所有单块映射的真实实体 |
| `dma_unmap_page` / `dma_unmap_page_attrs` | 解映射 | 后者导出 |
| `dma_map_resource(dev, phys_addr, size, dir, attrs)` | 映射一段 **MMIO 物理地址**（不是内存），用于设备到设备 | 导出；⚠️ 不做任何缓存维护，`kernel/dma/mapping.c:295` |
| `dma_unmap_resource(...)` | 对应解映射 | 导出 |
| `dma_mapping_error(dev, dma_addr)` | **每次映射后必查** | `static inline`；`dma_addr == DMA_MAPPING_ERROR` 时返回 `-ENOMEM`，否则 0 |

⚠️ **能被 DMA 的内存有硬约束**：`kmalloc`/`__get_free_pages`/slab 出来的可以；**vmalloc 区不行、`kmap` 出来的不行、内核栈上的不行、内核映像和模块里的静态变量不行**。原文 `Documentation/core-api/dma-api-howto.rst:111-147`。`dma_map_single_attrs` 只挡住了 vmalloc 一种，其余错误用法要靠 `CONFIG_DMA_API_DEBUG` 的 `check_for_stack()` 抓（07 §2.3）。

⚠️ **`dma_map_*` 失败不返回负数**。它返回一个 `dma_addr_t`，错误值是 `DMA_MAPPING_ERROR`（全 1）。不查 `dma_mapping_error()` 而直接把这个地址写进硬件寄存器，后果是"设备去写物理地址 0xffffffff…"——从静默数据损坏到 panic 都可能。原文警告在 `Documentation/core-api/dma-api-howto.rst:522-527`。

### §1.4 流式映射：scatterlist 与 sg_table

头文件：`include/linux/dma-mapping.h`（sg 结构本身在 `include/linux/scatterlist.h`）；实现：`kernel/dma/mapping.c`。

| API | 说明 | 备注 |
|---|---|---|
| `dma_map_sg(dev, sglist, nents, dir)` | 映射一组分散内存 | 宏；**返回实际映射出的段数，失败返回 0**——不是负数 |
| `dma_map_sg_attrs(...)` | 带属性版 | 导出；返回 `unsigned int` |
| `dma_unmap_sg(dev, sglist, nents, dir)` | 解映射 | 宏；⚠️ `nents` 必须是**映射时传进去的**，不是映射返回的那个数 |
| `dma_map_sgtable(dev, sgt, dir, attrs)` | `sg_table` 版本 | 导出 `_GPL`；**返回 0 或负错误码**，与 `dma_map_sg` 相反 |
| `dma_unmap_sgtable(dev, sgt, dir, attrs)` | 对应解映射 | `static inline`；内部用 `sgt->orig_nents` |
| `dma_get_max_seg_size(dev)` | 单段最大长度 | `static inline`；⚠️ 没设过时**默认 `SZ_64K`**，不是无限 |
| `dma_set_max_seg_size(dev, size)` | 设置它 | `static inline`；⚠️ `dev->dma_parms` 为空时返回 `-EIO` |
| `dma_get_seg_boundary(dev)` / `dma_set_seg_boundary(dev, mask)` | 段不许跨越的边界 | `static inline`；默认 `ULONG_MAX`（无限制） |
| `dma_get_seg_boundary_nr_pages(dev, page_shift)` | 上面那个换算成页数 | `static inline` |
| `dma_get_merge_boundary(dev)` | 问 IOMMU 的合并边界 | 导出 `_GPL`；无 ops 时返回 0 |

⚠️ **`dma_map_sg` 有两条独立的坑，都会静默出错**：

1. **返回值是新的段数，可能小于 `nents`**（IOMMU 或 direct 路径会把物理相邻的段合并）。遍历时必须用返回值当 count，并且用 `sg_dma_address()` / `sg_dma_len()` 取值，不能用 `sg->page_link`/`sg->length`。原文 `Documentation/core-api/dma-api-howto.rst:578-588`。
2. **解映射和同步都要用原来的 `nents`**，不是返回值。原文分别在 `Documentation/core-api/dma-api-howto.rst:596-601` 和 `:635-640`。

**`dma_map_sgtable` 的存在就是为了消掉这两个坑**：`sg_table` 里 `orig_nents` 和 `nents` 分开存，谁该用哪个由 API 自己决定，返回值也统一成"0 或负错误码"。**新代码优先用它**。

⚠️ 另一条：**已经映射过的 sglist 不能再映射一次**，而且"映射这个动作允许破坏 sglist 里的信息"。原文 `Documentation/core-api/dma-api.rst:384-385`。这不是建议，是 API 契约。

### §1.5 同步：在同一块映射上交替访问

头文件：`include/linux/dma-mapping.h`；实现：`kernel/dma/mapping.c`。

只有**流式**映射需要同步。一致性映射不需要（这就是它叫"一致性"的原因）。

| API | 说明 | 备注 |
|---|---|---|
| `dma_sync_single_for_cpu(dev, addr, size, dir)` | 把缓冲区所有权交回 CPU | 导出 |
| `dma_sync_single_for_device(dev, addr, size, dir)` | 交回设备 | 导出 |
| `dma_sync_single_range_for_cpu(dev, addr, offset, size, dir)` | 只同步一段 | `static inline`，就是 `addr + offset` 后转发 |
| `dma_sync_single_range_for_device(...)` | 同上 | `static inline` |
| `dma_sync_sg_for_cpu(dev, sglist, nents, dir)` | sg 版 | 导出；⚠️ `nents` 用映射时的原值 |
| `dma_sync_sg_for_device(...)` | 同上 | 导出 |
| `dma_sync_sgtable_for_cpu(dev, sgt, dir)` | `sg_table` 版 | `static inline`，内部用 `orig_nents` |
| `dma_sync_sgtable_for_device(dev, sgt, dir)` | 同上 | `static inline` |
| `dma_need_sync(dev, dma_addr)` | 问"这个地址上的同步是不是空操作" | 导出 `_GPL`；用于跳过热路径上的无用调用 |

⚠️ **同步是"所有权转移"，不是"刷缓存"**。`for_cpu` 之后到 `for_device` 之前这段窗口里，设备不许访问；反过来也一样。arm64 上这两个方向做的事情不对称：`for_device` 无条件 clean（`arch/arm64/mm/dma-mapping.c:16-22`），而 `for_cpu` 在 `dir == DMA_TO_DEVICE` 时**直接返回什么都不做**（`arch/arm64/mm/dma-mapping.c:29-30`）。含义见 08 §4。

⚠️ **`DMA_BIDIRECTIONAL` 要同步两次**：`for_device` 之前一次、`for_cpu` 之后一次，中间 CPU 改了内容还要再来一轮。原文 `Documentation/core-api/dma-api.rst:312-319`。

⚠️ **如果从 `dma_map_*` 到 `dma_unmap_*` 之间 CPU 一次都没碰过数据，`dma_sync_*` 一个都不需要调**。原文 `Documentation/core-api/dma-api-howto.rst:642-645`。多余的同步不会出错，但在大缓冲区上是纯浪费。

### §1.6 页与非连续分配：介于两者之间的第三条路

头文件：`include/linux/dma-mapping.h`；实现：`kernel/dma/mapping.c`。

这一组解决的问题是：**我想长期持有一块缓冲区（像一致性分配那样），但又不想付"整块内存变 non-cacheable"的代价（像流式那样保留缓存）**。代价是要自己调 `dma_sync_*`。

| API | 说明 | 备注 |
|---|---|---|
| `dma_alloc_pages(dev, size, dma_handle, dir, gfp)` | 分配整页、**保持可缓存**、已带 DMA 地址 | 导出 `_GPL`；返回 `struct page *`，失败 NULL |
| `dma_free_pages(dev, size, page, dma_handle, dir)` | 释放 | 导出 `_GPL` |
| `dma_mmap_pages(dev, vma, size, page)` | mmap 给用户态 | 导出 `_GPL` |
| `dma_alloc_noncoherent(dev, size, dma_handle, dir, gfp)` | 上面那个的虚拟地址版 | `static inline`，就是 `page_address()` 一下 |
| `dma_free_noncoherent(...)` | 对应释放 | `static inline` |
| `dma_alloc_noncontiguous(dev, size, dir, gfp, attrs)` | 分配一块**设备侧连续、物理上可以不连续**的缓冲区 | 导出 `_GPL`；返回 `struct sg_table *` |
| `dma_free_noncontiguous(dev, size, sgt, dir)` | 释放 | 导出 `_GPL` |
| `dma_vmap_noncontiguous(dev, size, sgt)` | 给它建一段内核虚拟连续映射 | 导出 `_GPL`；⚠️ 建出来的是可缓存映射，仍需 `dma_sync_*` |
| `dma_vunmap_noncontiguous(dev, vaddr)` | 撤掉 | 导出 `_GPL` |
| `dma_mmap_noncontiguous(dev, vma, size, sgt)` | mmap 给用户态 | 导出 `_GPL` |

⚠️ **`dma_alloc_noncontiguous` 只在有 IOMMU（或 ops 提供了 `alloc_noncontiguous`）时才可能真的返回多段**。没有 IOMMU 时它退化成"分配一整块连续内存再包成一个单段 sgt"——实现就是 `kernel/dma/mapping.c:608` 的 `alloc_single_sgt()`。所以驱动**不能**假设"用了这个 API 就一定省了连续内存"。

⚠️ 这组 API 的 `dir` 参数在**分配**时就要给定，之后所有同步都必须用同一个方向。和流式映射不一样，那里方向是每次映射时给的。

### §1.7 mmap 到用户态与 sgtable 导出

| API | 说明 | 备注 |
|---|---|---|
| `dma_mmap_coherent(dev, vma, cpu_addr, dma_addr, size)` | 把一致性内存映到用户态 | 宏，转发 `dma_mmap_attrs(..., 0)` |
| `dma_mmap_attrs(...)` | 带属性版 | 导出 |
| `dma_mmap_wc(...)` | write-combine 版 | `static inline` |
| `dma_get_sgtable(dev, sgt, cpu_addr, dma_addr, size)` | 把一致性内存转成 `sg_table`（给 dma-buf 导出用） | 宏 |
| `dma_get_sgtable_attrs(...)` | 带属性版 | 导出 |
| `dma_can_mmap(dev)` | 先问能不能 mmap | 导出 `_GPL` |

⚠️ `dma_mmap_*` 用的页属性由 `dma_pgprot()` 决定（`kernel/dma/mapping.c:415`）。**非一致性设备上它会把用户态映射也改成 non-cacheable 或 write-combine**——用户态代码看到的性能特征和普通 `mmap` 不同。

### §1.8 限制与参数查询

| API | 说明 | 备注 |
|---|---|---|
| `dma_max_mapping_size(dev)` | 单次映射的上限（swiotlb 在场时会被它压低） | 导出 `_GPL` |
| `dma_opt_mapping_size(dev)` | "超过这个大小性能会明显掉"的软阈值 | 导出 `_GPL`；⚠️ 是建议不是限制 |
| `dma_get_min_align_mask(dev)` | 设备要求的最小对齐掩码 | `static inline`；`dma_parms` 为空时返回 0 |
| `dma_set_min_align_mask(dev, mask)` | 设置它 | `static inline`；⚠️ `dma_parms` 为空时 `WARN_ON_ONCE` 并返回 `-EIO` |
| `dma_get_cache_alignment()` | 缓存行对齐要求 | `static inline`；**不带 `dev` 参数**，是全体系结构一个值 |
| `dma_pci_p2pdma_supported(dev)` | 问是否支持点对点 DMA | 导出 `_GPL` |

⚠️ **`dma_get_cache_alignment()` 是可被体系结构覆盖的**（`include/linux/dma-mapping.h:547` 的 `#ifndef dma_get_cache_alignment`），通用版本在 `ARCH_HAS_DMA_MINALIGN` 时返回 `ARCH_DMA_MINALIGN`，否则返回 1。

⚠️ **缓冲区不能与别的数据共享缓存行**。这条是 DMA 正确性的硬要求而不是性能建议：`kmalloc` 的返回值已经保证了这一点（靠 `ARCH_DMA_MINALIGN`），但**你在一个结构体里嵌一个 DMA 缓冲区数组就不保证**。原文警告在 `Documentation/core-api/dma-api.rst:287-298`，体系结构侧的要求在 `Documentation/core-api/dma-api-howto.rst:884-897`。

### §1.9 devres 版本（`dmam_*`）

| API | 说明 | 备注 |
|---|---|---|
| `dmam_alloc_coherent(dev, size, dma_handle, gfp)` | 设备解绑时自动释放的一致性分配 | `static inline` |
| `dmam_alloc_attrs(dev, size, dma_handle, gfp, attrs)` | 带属性版 | **导出**（`kernel/dma/mapping.c:85`） |
| `dmam_free_coherent(dev, size, vaddr, dma_handle)` | 提前手动释放（会顺便注销 devres 记录） | 导出 |

⚠️ **`dmam_*` 只覆盖一致性分配，没有 `dmam_map_single` 之类**。流式映射天生是短生命周期的，devres 帮不上；probe 里映射、remove 里解映射这种用法本身就是设计错误。

### §1.10 只有 `dma_map_ops` 实现者才用的接口

这些在 `include/linux/dma-map-ops.h`，**普通驱动 include 它就说明用错了**。列在这里只为让你认出来：

| API | 谁用 | 说明 |
|---|---|---|
| `set_dma_ops(dev, ops)` / `get_dma_ops(dev)` | IOMMU 层、xen、vdpa | 挂/取 per-device ops；`CONFIG_DMA_OPS` 关闭时 `get_dma_ops()` 恒返回 NULL |
| `arch_setup_dma_ops(...)` / `arch_teardown_dma_ops(dev)` | driver core / of 层 | 设备 probe 前后由平台代码调用，arm64 实现在 `arch/arm64/mm/dma-mapping.c:64` / `:58` |
| `arch_sync_dma_for_device` / `arch_sync_dma_for_cpu` | direct 路径内部 | 体系结构提供的缓存维护，arm64 在 `arch/arm64/mm/dma-mapping.c:16` / `:24` |
| `arch_dma_prep_coherent(page, size)` | direct 路径的分配 | 一致性内存首次交付前的清理，arm64 在 `arch/arm64/mm/dma-mapping.c:35` |
| `dma_common_get_sgtable` / `dma_common_mmap` / `dma_common_alloc_pages` / `dma_common_free_pages` | ops 实现者 | 可直接复用的样板，实现在 `kernel/dma/ops_helpers.c:1` |
| `dma_alloc_from_dev_coherent` / `dma_release_from_dev_coherent` / `dma_mmap_from_dev_coherent` | 分配路径内部 | per-device 一致性池，实现在 `kernel/dma/coherent.c:1` |
| `dma_alloc_from_global_coherent` 等 | 同上 | `CONFIG_DMA_GLOBAL_POOL` 时的全局池 |
| `dma_debug_add_bus(bus)` | 总线代码 | ⚠️ 这是整个子系统里**唯一**出现 `struct bus_type` 的地方，声明在 `include/linux/dma-map-ops.h:383`/`:386`，实现在 `kernel/dma/debug.c:875` |
| `dma_dummy_ops` | ACPI 扫描（arm64 上被裁） | 全部返回失败的 ops，`kernel/dma/dummy.c:33` |

---

## §2 错误码与返回值语义

这套 API 的返回约定**不统一**，是最容易出错的地方。全表：

| API 类别 | 成功 | 失败 | 检查方法 |
|---|---|---|---|
| `dma_map_single` / `dma_map_page` / `dma_map_resource` | 有效 `dma_addr_t` | `DMA_MAPPING_ERROR`（全 1） | **必须** `dma_mapping_error()` |
| `dma_map_sg` / `dma_map_sg_attrs` | **正数**（实际段数，可能 < `nents`） | **0** | `if (!ret)` |
| `dma_map_sgtable` | **0** | **负错误码** | `if (ret)` |
| `dma_alloc_coherent` / `dma_alloc_attrs` / `dmam_alloc_*` | 非空指针 | **NULL** | `if (!ptr)`；不是 `IS_ERR` |
| `dma_alloc_pages` | 非空 `struct page *` | NULL | `if (!page)` |
| `dma_alloc_noncontiguous` | 非空 `struct sg_table *` | NULL | `if (!sgt)` |
| `dma_set_mask` / `dma_set_coherent_mask` | 0 | `-EIO` | `if (ret)` |
| `dma_set_max_seg_size` / `dma_set_seg_boundary` / `dma_set_min_align_mask` | 0 | `-EIO`（`dma_parms` 为空） | `if (ret)` |
| `dma_mmap_attrs` / `dma_get_sgtable_attrs` | 0 | 负错误码（含 `-ENXIO`） | `if (ret)` |
| `dma_unmap_*` / `dma_sync_*` / `dma_free_*` | `void` | —— | **无法检查**，见下 |

⚠️ **`dma_map_sg` 返回 0 表示失败、`dma_map_sgtable` 返回 0 表示成功**——两个语义完全相反的 0，用在同一个子系统里。这不是笔误，是 `dma_map_sgtable` 为了修掉旧 API 的坑而故意改的。`include/linux/dma-map-ops.h:22` 的 `struct dma_map_ops` 里 `map_sg` 回调上方还有一行注释专门说明"`map_sg` 出错要返回负错误码"——ops 层的约定又是第三种。三层三种约定，写代码时看清自己在哪一层。

⚠️ **所有 `void` 返回的函数（unmap / sync / free）都是"错了也不告诉你"**。`dma_unmap_*` 传错 `size` 或 `dir`，正常内核下什么都不会发生，缓存维护范围错了导致的数据损坏会在几百毫秒后以别的形式爆出来。**这是 `CONFIG_DMA_API_DEBUG` 存在的唯一理由**——它给这些哑函数补上了检查（`check_unmap()` 在 `kernel/dma/debug.c:956`，`check_sync()` 在 `:1091`）。详见 07 §2。

⚠️ **`-EIO` 出现在三个语义完全不同的地方**：`dma_set_mask` 的 `-EIO` 意思是"这个掩码设备不支持"；`dma_set_max_seg_size` 的 `-EIO` 意思是"`dev->dma_parms` 是空指针，你的设备没被正确初始化"。看到 `-EIO` 先分清是哪一类。

---

## §3 `static inline` 与导出符号：这条界线要划清

整个子系统对外导出 **38 个符号**：

| 文件 | `_GPL` | 非 GPL | 小计 |
|---|---|---|---|
| `kernel/dma/mapping.c` | 16 | 18 | 34 |
| `kernel/dma/swiotlb.c` | 2（`swiotlb_max_segment` `:189`、`is_swiotlb_active` `:965`） | 0 | 2 |
| `kernel/dma/debug.c` | 0 | 2（`debug_dma_map_single` `:1206`、`debug_dma_mapping_error` `:1280`） | 2 |
| 其余 8 个 `.c` | 0 | 0 | **0** |

⚠️ **`coherent.c` / `contiguous.c` / `direct.c` / `dummy.c` / `map_benchmark.c` / `ops_helpers.c` / `pool.c` / `remap.c` 一个符号都不导出。** 它们提供的一切只通过两条路到达驱动：要么被 `mapping.c` 内部调用，要么以 `static inline` 的形式住在头文件里。**含义：模块化的树外驱动无法直接调用 CMA 或 swiotlb 的任何函数**——只能通过 `dma_alloc_coherent()` 一类的通用入口间接触发。

⚠️ 反过来的坑更常见：**你天天在用的这些名字里有一大半不是导出符号，而是 `static inline` 或宏**。它们照样可用（编译进你的模块里），但 `grep Module.symvers` 找不到，`nm` 也看不到调用关系：

| 名字 | 真身 | 位置 |
|---|---|---|
| `dma_map_single` / `dma_unmap_single` | 宏 → `dma_map_single_attrs` | `include/linux/dma-mapping.h:412` / `:413` |
| `dma_map_page` / `dma_unmap_page` | 宏 → `dma_map_page_attrs` | `include/linux/dma-mapping.h:416` / `:417` |
| `dma_map_sg` / `dma_unmap_sg` | 宏 → `dma_map_sg_attrs` | `include/linux/dma-mapping.h:414` / `:415` |
| `dma_alloc_coherent` / `dma_free_coherent` | `static inline` → `dma_alloc_attrs` | `include/linux/dma-mapping.h:421` / `:428` |
| `dma_set_mask_and_coherent` | `static inline` → `dma_set_mask` + `dma_set_coherent_mask` | `include/linux/dma-mapping.h:448` |
| `dma_coerce_mask_and_coherent` | `static inline` | `include/linux/dma-mapping.h:460` |
| `dma_mapping_error` | `static inline` | `include/linux/dma-mapping.h:95` |
| `dma_get_mask` / `dma_addressing_limited` | `static inline` | `include/linux/dma-mapping.h:435` / `:474` |
| `dma_alloc_wc` / `dma_free_wc` / `dma_mmap_wc` | `static inline` | `include/linux/dma-mapping.h:564` / `:575` / `:582` |
| `dmam_alloc_coherent` | `static inline` → `dmam_alloc_attrs` | `include/linux/dma-mapping.h:557` |
| `dma_get_max_seg_size` 等 6 个 seg/align 访问器 | `static inline` | `include/linux/dma-mapping.h:480`–`:545` |
| `dma_unmap_addr` 等 6 个 unmap 状态宏 | 宏，两套定义 | `include/linux/dma-mapping.h:592`–`:607` |

⚠️ **`DEFINE_DMA_UNMAP_ADDR` / `dma_unmap_addr_set` 这一组有两套定义**，由 `CONFIG_NEED_DMA_MAP_STATE`（arm64 无条件 select，`arch/arm64/Kconfig:218`）二选一。arm64 走的是"真的存字段"那一套（`include/linux/dma-mapping.h:592-597`），另一套（`:599-607`）把字段整个消掉、`set` 变成空操作。**用意在 `Documentation/core-api/dma-api-howto.rst:815-870`：让驱动不用写 `#ifdef` 就能在两类平台上都省下这几个字段。** 在 arm64 上它省不了任何东西，但用它写出来的驱动能原样在别的架构上编译。

⚠️ **`CONFIG_HAS_DMA` 关闭时，`include/linux/dma-mapping.h:161-303` 那一整段给出了全部 API 的失败版 `static inline`**（映射返回 `DMA_MAPPING_ERROR`、分配返回 NULL、`dma_set_mask` 返回 `-EIO`）。所以"编译过了"不等于"这个平台真有 DMA"。arm64 恒有。

---

## §4 `DMA_ATTR_*` 属性标志

8 个，全在 `include/linux/dma-mapping.h`。语义原文在 `Documentation/core-api/dma-attributes.rst:1`（132 行，逐个标志一节）。

| 标志 | 位 | 位置 | 用在 | arm64 上是否真生效 |
|---|---|---|---|---|
| `DMA_ATTR_WEAK_ORDERING` | 1 | `:24` | map / alloc | 平台可忽略；arm64 direct 路径不看它 |
| `DMA_ATTR_WRITE_COMBINE` | 2 | `:29` | alloc / mmap | **生效**，`dma_pgprot()` 会据此选属性 |
| `DMA_ATTR_NO_KERNEL_MAPPING` | 4 | `:34` | alloc | **生效**；返回值只是个 cookie，**不许解引用** |
| `DMA_ATTR_SKIP_CPU_SYNC` | 5 | `:40` | map / unmap | **生效**；多设备共享同一缓冲区时省掉重复的缓存维护 |
| `DMA_ATTR_FORCE_CONTIGUOUS` | 6 | `:45` | alloc | **生效**；强制物理连续（走 CMA） |
| `DMA_ATTR_ALLOC_SINGLE_PAGES` | 7 | `:51` | alloc | ⚠️ 文档说只在 ARM 实现（`Documentation/core-api/dma-attributes.rst:104-105`） |
| `DMA_ATTR_NO_WARN` | 8 | `:56` | alloc | ⚠️ 文档说只在 PowerPC 实现（`Documentation/core-api/dma-attributes.rst:122`） |
| `DMA_ATTR_PRIVILEGED` | 9 | `:63` | alloc / map | 交给 IOMMU 层处理，direct 路径不看 |

⚠️ **位 3 是个空洞**：从 `1UL << 2` 直接跳到 `1UL << 4`。历史上那一位是 `DMA_ATTR_NON_CONSISTENT`，已经被 `dma_alloc_pages` 那组 API 取代并删除，位号没有回收。**别把它当可用位来自定义扩展**。

⚠️ **`DMA_ATTR_NO_KERNEL_MAPPING` 的返回值不是指针。** `dma_alloc_attrs()` 带上它之后返回的是一个不透明 cookie，只能原样传给 `dma_mmap_attrs()` 和 `dma_free_attrs()`，而且这两次调用**也必须带上同一个标志**。原文 `Documentation/core-api/dma-attributes.rst:31-40`。解引用它 = 访问一个没有内核映射的地址。

⚠️ **`DMA_ATTR_SKIP_CPU_SYNC` 是双向的**：加在 `unmap` 上表示"解映射后让缓冲区留在设备域"。用它的前提是你自己清楚谁在什么时候做了那次缓存维护。原文最后一句就是 `Use this attribute with care!`（`Documentation/core-api/dma-attributes.rst:68`）。

---

## §5 方向枚举

`include/linux/dma-direction.h:5-10`，四个值：

| 值 | 数值 | 含义 | 谁写 | 谁读 |
|---|---|---|---|---|
| `DMA_BIDIRECTIONAL` | 0 | 两个方向都可能 | 都可能 | 都可能 |
| `DMA_TO_DEVICE` | 1 | CPU 准备数据 → 设备取走（发送） | CPU | 设备 |
| `DMA_FROM_DEVICE` | 2 | 设备写入 → CPU 读走（接收） | 设备 | CPU |
| `DMA_NONE` | 3 | **仅用于调试**，不是合法的映射方向 | —— | —— |

`valid_dma_direction()`（`include/linux/dma-direction.h:12-16`）显式把 `DMA_NONE` 排除在合法值之外。

⚠️ **方向不是可选的性能提示，它决定做哪种缓存维护**。arm64 上 `DMA_TO_DEVICE` 走 clean、`DMA_FROM_DEVICE` 走 invalidate、`DMA_BIDIRECTIONAL` 两个都做（`arch/arm64/mm/dma-mapping.c:16-33`）。**写错方向的后果不是慢，是数据损坏**：把一个接收缓冲区标成 `DMA_TO_DEVICE`，`for_cpu` 时的 invalidate 被跳过，CPU 读到的是缓存里的旧数据。

⚠️ **"不知道方向就写 `DMA_BIDIRECTIONAL`"是官方许可的做法**（`Documentation/core-api/dma-api-howto.rst:461-465`），代价是性能。但**已经知道方向却写 `DMA_BIDIRECTIONAL`** 会掩盖 bug，还会让 `CONFIG_DMA_API_DEBUG` 的方向检查失效。

⚠️ **一致性映射没有方向参数，语义上等价于 `DMA_BIDIRECTIONAL`**（`Documentation/core-api/dma-api-howto.rst:480-482`）。

网络驱动的固定用法：发包 `DMA_TO_DEVICE`、收包 `DMA_FROM_DEVICE`（`Documentation/core-api/dma-api-howto.rst:488-491`）。SCSI 用 `sc_data_direction` 字段（`:484-486`）。

---

## §6 不该用、或者用了要写理由的接口

| 接口 | 状态 | 该用什么 |
|---|---|---|
| `dma_map_sg` + 手工管两个 nents | 仍然可用但坑多 | **`dma_map_sgtable`**，把 `orig_nents`/`nents` 的区分交给 API |
| `dma_coerce_mask_and_coherent` | 可用但需理由 | 正常情况用 `dma_set_mask_and_coherent`。前者会**改写 `dev->dma_mask` 指针本身**，只有"平台代码没给这个设备设 `dma_mask`"时才是对的 |
| `dma_alloc_coherent` 用于大缓冲区 + CPU 频繁访问 | 能用但慢 | `dma_alloc_pages` / `dma_alloc_noncontiguous` + 显式 `dma_sync_*` |
| 在 `gfp` 里带 `GFP_DMA` / `GFP_DMA32` | 错 | 只设掩码，zone 的选择交给核心 |
| 直接读写 `dev->dma_mask` / `dev->coherent_dma_mask` | 错（除平台代码） | `dma_get_mask()` / `dma_set_*_mask()` |
| 直接调 `get_dma_ops()` / `set_dma_ops()` | 驱动不该用 | 没有替代——需要它说明你在写平台代码或 IOMMU 层 |
| `swiotlb_max_segment()` | 导出但**不给普通驱动** | `dma_max_mapping_size()` |
| `debug_dma_map_single()` / `debug_dma_mapping_error()` | 导出但只因为被 `static inline` 引用 | 不要直接调，它们是 `dma_map_single_attrs()` / `dma_mapping_error()` 的内部实现 |
| `DMA_ATTR_ALLOC_SINGLE_PAGES` / `DMA_ATTR_NO_WARN` | arm64 上无效果 | 写了不报错也不生效，别指望它 |

⚠️ **`swiotlb_max_segment` 和两个 `debug_dma_*` 是"因为技术原因不得不导出"的典型**：前者给 xen 和 IOMMU 层用，后两者只是因为它们的调用者是头文件里的 `static inline`，模块编译时会真的产生外部引用。**"被导出"在这三个符号上完全不等于"这是公开 API"**。

---

## §7 去哪找什么

| 你想知道 | 去哪 |
|---|---|
| 这些函数内部怎么实现的 | 03 篇 |
| `struct device` 里那几个 DMA 字段的关系 | 02 §2 |
| `dma_map_ops` 的 25 个成员 | 02 §3 |
| swiotlb / CMA / 一致性池 / 受限池怎么选、怎么配 | 04 篇 |
| 一个驱动该按什么顺序调这些 API | 05 篇 |
| dts 里 `dma-ranges` / `dma-coherent` 怎么写 | 06 篇 |
| 出问题了看什么输出 | 07 篇 |
| 为什么要分"一致性"和"流式" | 08 §2 |

