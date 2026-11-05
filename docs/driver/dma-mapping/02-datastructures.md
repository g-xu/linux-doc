---
title: 数据结构
---

# dma-mapping 核心数据结构

代码基线 Linux 6.1.158，arm64。本篇讲"东西存在哪"，流程在 03 篇。

---

## §1.0 先分清六组关系

这个子系统里没有一个复杂的数据结构，难点全在**同名不同物**和**看起来该是一回事其实分了好几路**。下面六组关系不先摊开，后面每一张图都会被读错。

### §1.0.1 三种地址：CPU 虚拟 / CPU 物理 / DMA 地址

**错误类比**：`dma_addr_t` 是"物理地址的另一个类型名"。

**实际**：三者是三个独立的地址空间，任意两个之间都可能有平移。

| 地址 | C 类型 | 谁用它寻址 | 从别的地址怎么来 |
|---|---|---|---|
| CPU 虚拟地址 | `void *` | CPU 的 load/store | `page_address()` / `kmap` / `ioremap` |
| CPU 物理地址 | `phys_addr_t` | 页表的另一端；`memblock` 记的就是它 | `virt_to_phys()` / `page_to_phys()` |
| DMA 地址 | `dma_addr_t` | **设备**发出的总线事务 | `phys_to_dma()`（`include/linux/dma-direct.h:70`） |

官方那张 ASCII 图（`Documentation/core-api/dma-api-howto.rst:44-64`）把这三者和五个访问点 A/B/C/X/Y/Z 画在一起，值得一看。

**为什么必须分三个**：设备看到的地址由**总线布线**决定，可以和 CPU 看到的物理地址不同。arm64 上这个平移由 dts 的 `dma-ranges` 声明，落进 `dev->dma_range_map`（§4）。

⚠️ **arm64 上没有全局的 `dma_pfn_offset`，只有 per-device 的 `dma_range_map`。** 而且 arm64 **不定义** `CONFIG_ARCH_HAS_PHYS_TO_DMA`（`arch/arm64/Kconfig` 里没有这个 select），所以走的是 `include/linux/dma-direct.h:57-84` 的通用实现：`dev->dma_range_map` 为空就恒等，非空就查表。**含义：dts 里没写 `dma-ranges` 的设备，它的 DMA 地址等于物理地址**——这是 arm64 上的绝大多数情况。

⚠️ **`dma_addr_t` 上不能做算术假设**。`translate_phys_to_dma()`（`include/linux/dma-direct.h:26-37`）是**逐区间线性查表**，不同区间的 `offset` 可以不同。所以"把 DMA 地址加 4096 就得到下一页的 DMA 地址"在跨区间时是错的——这也是为什么每一页都要单独 `dma_map_page()`。

⚠️ 查表失败时 `translate_phys_to_dma()` 返回 `DMA_MAPPING_ERROR`，注释里明说这么做的目的是"让 `dma_capable` 随后失败"（`include/linux/dma-direct.h:35`）。**它不是一个正常的返回路径，是一个刻意制造后续失败的技巧。**

### §1.0.2 一致性分配 / 流式映射 / 页分配：三条获取缓冲区的路

**错误类比**：`dma_alloc_coherent` 是"带 DMA 地址的 kmalloc"。

**实际**：三条路的取舍点完全不同，选错的代价是性能或正确性。

| | `dma_alloc_coherent` | `dma_map_single` 系列 | `dma_alloc_pages` 系列 |
|---|---|---|---|
| 内存从哪来 | 子系统自己分配 | **你已有的**普通内存 | 子系统分配整页 |
| CPU 侧缓存 | arm64 上通常关掉或改属性 | 保留 | **保留** |
| 要不要显式同步 | **不要** | 要（`dma_sync_*`） | 要 |
| 生命周期 | 长（probe 到 remove） | 短（一次传输） | 长 |
| CPU 访问速度 | 慢 | 正常 | **正常** |
| 典型用途 | 描述符环、邮箱 | 数据包、块 I/O 缓冲 | 大缓冲区且 CPU 也要频繁读写 |
| 页属性由谁定 | `dma_pgprot()`（`kernel/dma/mapping.c:415`） | 不改 | 不改 |

**判据一句话**：**"CPU 和设备是否需要同时看到最新数据"**——需要就用一致性；不需要、只是交替访问，就用流式或页分配。

⚠️ 第三条路（`dma_alloc_pages` / `dma_alloc_noncoherent`）是这几年才补上的，很多老驱动为了图省事把大缓冲区也用 `dma_alloc_coherent` 分配，结果 CPU 访问全走 non-cacheable，性能塌掉。这是**新代码应该主动避免的老习惯**。

### §1.0.3 掩码不是一个，是四个约束的最小值

**错误类比**：`dev->dma_mask` 就是"这个设备能寻址多少内存"。

**实际**：能不能寻址一个地址，由四个东西共同决定，任何一个不满足就要 bounce 或失败。

| 约束 | 存在哪 | 谁设的 | 语义 |
|---|---|---|---|
| `dev->dma_mask`（**指针**） | `include/linux/device.h:592` | 驱动调 `dma_set_mask()`；总线代码负责让这个指针非空 | 流式映射的寻址能力 |
| `dev->coherent_dma_mask` | `include/linux/device.h:593` | `dma_set_coherent_mask()` | 一致性分配的寻址能力 |
| `dev->bus_dma_limit` | `include/linux/device.h:598` | **平台/dts**，来自 `dma-ranges` 的上界 | 上游总线或桥的硬限制，驱动改不了 |
| `dev->dma_range_map` | `include/linux/device.h:599` | 平台/dts | 地址平移表；查不到的地址根本不可达 |

四者怎么合起来用，看 `dma_capable()`（`include/linux/dma-direct.h:99-111`）：

```c
static inline bool dma_capable(struct device *dev, dma_addr_t addr, size_t size,
		bool is_ram)
{
	dma_addr_t end = addr + size - 1;

	if (addr == DMA_MAPPING_ERROR)          /* :103 dma_range_map 查表失败 */
		return false;
	if (is_ram && !IS_ENABLED(CONFIG_ARCH_DMA_ADDR_T_64BIT) &&
	    min(addr, end) < phys_to_dma(dev, PFN_PHYS(min_low_pfn)))
		return false;                    /* :105-107 32 位平台的下界检查 */

	return end <= min_not_zero(*dev->dma_mask, dev->bus_dma_limit);  /* :110 */
}
```

⚠️ **`min_not_zero`（`:110`）意味着 `bus_dma_limit == 0` 表示"没有额外限制"，不是"什么都不能寻址"。** 把它当成"0 就是限制为 0"是一个会让人查半天的误读。

⚠️ **`dev->dma_mask` 是指针，不是值。** 它可能是 NULL。`dma_get_mask()`（`include/linux/dma-mapping.h:435-440`）对空指针和值为 0 两种情况都返回 `DMA_BIT_MASK(32)`——**默默给你一个 32 位默认值**。所以"没设过掩码"的设备行为等于"设了 32 位"，不会报错。这正是 `dma_coerce_mask_and_coherent()` 存在的原因：某些设备（例如被平台代码人工造出来的）连 `dma_mask` 指针都没人给它指，它把指针指向 `coherent_dma_mask` 这个**同一个结构里的真实字段**（`include/linux/dma-mapping.h:462`）。

⚠️ **`coherent_dma_mask` 允许比 `dma_mask` 小，不允许更大。** `dma_set_mask_and_coherent()` 的注释（`include/linux/dma-mapping.h:442-447`）把这条写成 API 保证，所以它不检查 `dma_set_coherent_mask()` 的返回值。

### §1.0.4 五个"池"：名字都带 pool，互不相同

⚠️ 这是本子系统最容易混的一组。五个池解决五个不同的问题：

| 池 | Kconfig | 结构 | 解决的问题 | dts 关键字 |
|---|---|---|---|---|
| **CMA**（连续内存分配器） | `DMA_CMA` | `struct cma`（在 `mm/cma.h`，不属本子系统） | 要**大块物理连续**内存，但不想启动时就独占 | `shared-dma-pool` + `reusable` |
| **per-device 一致性池** | `DMA_DECLARE_COHERENT` | `struct dma_coherent_mem`（`kernel/dma/coherent.c:13`） | 某个设备只能用**特定一段物理内存** | ⚠️ `shared-dma-pool` 且**无** `reusable`（`no-map` 在 arm64 上不是条件，见 06 §4） |
| **atomic pool**（原子池） | `DMA_COHERENT_POOL` | 三个 `struct gen_pool *`（`kernel/dma/pool.c:16/18/20`） | **中断上下文**里也要能 `dma_alloc_coherent` | 无（`coherent_pool=` 内核参数） |
| **swiotlb**（默认 bounce 池） | `SWIOTLB` | `struct io_tlb_mem`（`include/linux/swiotlb.h:91`） | 设备**寻址不到**目标内存时搬一次 | 无（`swiotlb=` 内核参数） |
| **restricted DMA pool**（受限池） | `DMA_RESTRICTED_POOL` | 同上，per-device 的 `io_tlb_mem` | **不信任**这个设备，强制它只能读写一小段内存 | `restricted-dma-pool` |

四条区分要点：

1. **CMA 和 per-device 一致性池在 dts 里用的是同一个 compatible** `shared-dma-pool`，⚠️ **在 arm64 上只靠 `reusable` 一个属性区分**（判定点在 `kernel/dma/coherent.c:367` 和 `kernel/dma/contiguous.c:412-413`）——`kernel/dma/coherent.c:371` 那道 `no-map` 检查被 `#ifdef CONFIG_ARM` 编译掉了。写错 `reusable` 就会落到另一个机制上；`reusable` 和 `no-map` 同时写则两边都不收。详见 06 §4.3。
2. **atomic pool 和 per-device 池都叫"一致性池"，但一个是全局三份、一个是每设备一份**。atomic pool 只在 `gfp` 不允许睡眠时才用（判定在 `kernel/dma/direct.c:166`）。
3. **swiotlb 和受限池共用同一套代码和同一个结构**，区别只在 `dev->dma_io_tlb_mem` 指向全局的 `io_tlb_default_mem`（`include/linux/swiotlb.h:106`）还是指向 dts 声明的那一份，以及 `mem->force_bounce` 是否为真。
4. **`struct dma_pool`（`include/linux/dmapool.h`）不在这五个里面**，它是 `dma_pool_create()` 那套小对象分配器，实现在 `mm/dmapool.c`，**不属于 `kernel/dma/`**。名字最像，关系最远。

### §1.0.5 本子系统有几层可插拔

| 层 | 抽象 | 实现者在哪 | 本树实现数 | arm64 常见吗 |
|---|---|---|---|---|
| 第 1 层：per-device 的映射实现 | `struct dma_map_ops`（`include/linux/dma-map-ops.h:22`） | `drivers/iommu/`、`drivers/xen/`、`drivers/vdpa/`、`kernel/dma/dummy.c` | 全树 **24 个实例**，其中非 `arch/`、非 parisc 的只有 **6 个**（见 §3.2） | ⚠️ **不常见**——多数设备 `dev->dma_ops == NULL` |
| 第 2 层：体系结构缓存维护钩子 | `arch_sync_dma_for_*` / `arch_dma_prep_coherent` | `arch/*/mm/` | arm64 一份（`arch/arm64/mm/dma-mapping.c:1`） | **恒用** |
| 第 3 层：reserved-memory 后端 | `RESERVEDMEM_OF_DECLARE` 的 `device_init`/`device_release` | `kernel/dma/coherent.c:402`、`kernel/dma/contiguous.c:440`、`kernel/dma/swiotlb.c:1111` | **3 个** | 用到才有 |

⚠️ **第 1 层在 arm64 上默认不存在。** `CONFIG_DMA_OPS` 只被 `drivers/iommu`、`drivers/xen`、`drivers/vdpa` select，arm64 自己不选。没选时 `get_dma_ops()` 是个恒返回 NULL 的空壳，整条间接层被编译掉。所以**"这个设备的 dma_ops 是什么"在 arm64 上通常是个没有意义的问题**——答案是 NULL，走 direct 路径。详见 03 §B1。

⚠️ **第 1 层存在时，第 2 层不一定被绕过。** IOMMU 的 `iommu_dma_ops`（`drivers/iommu/dma-iommu.c:1558`）内部照样会调 `arch_sync_dma_for_device()`——它只接管地址翻译，不接管缓存维护。**这两层是正交的，不是二选一。**

### §1.0.6 三个都叫 dma 的子系统

已在 01 篇开头给出对照表。这里只补一条结构层面的区分：

| 子系统 | 它的核心结构 | 定义处 |
|---|---|---|
| **dma-mapping**（本套） | `struct dma_map_ops` | `include/linux/dma-map-ops.h:22` |
| dmaengine | `struct dma_device` / `struct dma_chan` | `include/linux/dmaengine.h` |
| dma-buf | `struct dma_buf` / `struct dma_buf_attachment` | `include/linux/dma-buf.h` |

三者在一条链上：dma-buf 的 `map_dma_buf` 回调里会调 dma-mapping 的 `dma_map_sgtable()`；dmaengine 的驱动用 dma-mapping 给自己的描述符环分配内存。**没有任何一个包含另一个。**

---

## §2 `struct device` 里的 DMA 字段

这个子系统**没有自己的核心对象**——它的全部 per-device 状态都寄生在 `struct device` 里。这是它是"框架型"而非"总线型"的直接体现（判定过程在 99 §1）。

`include/linux/device.h`，11 个字段 + 2 个位域：

| 字段 | 行 | 类型 | 谁写 | 干什么 |
|---|---|---|---|---|
| `dma_ops` | `:590` | `const struct dma_map_ops *` | `set_dma_ops()`，即 IOMMU / xen / vdpa | 非 NULL 就走 ops 路径；arm64 上通常 NULL |
| `dma_mask` | `:592` | `u64 *`（**指针**） | 总线代码指向某处；`dma_set_mask()` 改它指向的值 | 流式寻址能力 |
| `coherent_dma_mask` | `:593` | `u64`（**值**） | `dma_set_coherent_mask()` | 一致性分配的寻址能力 |
| `bus_dma_limit` | `:598` | `u64` | dts 的 `dma-ranges`（`drivers/of/device.c` 的 `of_dma_configure_id()`） | 上游硬限制，0 = 无限制 |
| `dma_range_map` | `:599` | `const struct bus_dma_region *` | 同上 | 物理↔DMA 地址平移表，见 §4 |
| `dma_parms` | `:601` | `struct device_dma_parameters *` | 总线或驱动 | sg 分段限制，见 §5.2 |
| `dma_pools` | `:603` | `struct list_head` | `mm/dmapool.c` | ⚠️ **不属于本子系统**，是 `dma_pool_*` 那套 |
| `dma_mem` | `:606` | `struct dma_coherent_mem *` | `kernel/dma/coherent.c` | per-device 一致性池，见 §6.1 |
| `cma_area` | `:610` | `struct cma *` | `kernel/dma/contiguous.c` 的 `rmem_cma_device_init()` | per-device CMA 区，NULL 则用全局的 |
| `dma_io_tlb_mem` | `:614` | `struct io_tlb_mem *` | swiotlb 初始化或 dts 的受限池 | 用哪个 bounce 池；注释里明说 `Not for driver use` |
| `dma_coherent`（位域） | `:650` | `bool:1` | `arch_setup_dma_ops()`（arm64：`arch/arm64/mm/dma-mapping.c:75`） | 这个设备硬件上是否缓存一致 |
| `dma_ops_bypass`（位域） | `:653` | `bool:1` | 总线代码（`CONFIG_DMA_OPS_BYPASS`） | 有 ops 也允许走直接路径；arm64 无使用者 |

⚠️ **`dma_mask` 是指针而 `coherent_dma_mask` 是值**，这个不对称是历史遗留，也是 `dma_coerce_mask_and_coherent()` 能存在的技术前提（它把前者指向后者）。写平台代码时必须记住：**给设备设掩码之前先保证 `dma_mask` 指针有效**，否则 `dma_set_mask()` 里的 `*dev->dma_mask = mask` 会解引用空指针。

⚠️ **`dma_coherent` 位域和 `dma_default_coherent` 全局变量是两件事。** 后者在 `kernel/dma/mapping.c:20`，是"本平台默认是否一致"；前者是 per-device 的实际值。arm64 上 `arch_setup_dma_ops()` 直接把 dts 里 `dma-coherent` 属性的解析结果写进 `dev->dma_coherent`（`arch/arm64/mm/dma-mapping.c:75`）。

⚠️ **`dma_pools` 这个字段名极具误导性**：它和 §1.0.4 那五个池**一个都不沾**，是 `dma_pool_create()` 建的小对象分配器链表，代码在 `mm/dmapool.c`。查代码时看到它不要跑到 `kernel/dma/pool.c` 去。

### §2.1 `struct device_dma_parameters`

`include/linux/device.h:281-289`，三个字段，全是驱动可以调的 sg 限制：

| 字段 | 行 | 访问器 | 缺省 |
|---|---|---|---|
| `max_segment_size` | `:286` | `dma_get_max_seg_size()` / `dma_set_max_seg_size()` | ⚠️ `dma_parms` 为空时**返回 `SZ_64K`** |
| `min_align_mask` | `:287` | `dma_get_min_align_mask()` / `dma_set_min_align_mask()` | 0 |
| `segment_boundary_mask` | `:288` | `dma_get_seg_boundary()` / `dma_set_seg_boundary()` | `ULONG_MAX` |

⚠️ **`dev->dma_parms` 本身可能是 NULL**，而且**分配它不是本子系统的责任**——总线层（platform / PCI / usb 各自）负责。所以 `dma_set_max_seg_size()` 返回 `-EIO` 的真实含义是"你的总线没给这个设备分配 `dma_parms`"，不是"这个值不合法"。`dma_set_min_align_mask()` 更直接，空指针时 `WARN_ON_ONCE`（`include/linux/dma-mapping.h:541`）。

⚠️ **`SZ_64K` 这个默认值咬过很多驱动**：一个不设 `max_segment_size` 的设备，`dma_map_sg` 出来的每段都不会超过 64K，哪怕硬件支持更大。发现"为什么我的 sg 段数比预期多"先查这里。

---

## §3 `struct dma_map_ops`：唯一的可插拔接口

`include/linux/dma-map-ops.h:22-84`，**1 个标志字段 + 24 个回调 = 25 个成员**。

### §3.1 25 个成员按职责分组

| 组 | 成员 | 行 | 缺了会怎样 |
|---|---|---|---|
| 元信息 | `flags` | `:23` | 目前只有一位 `DMA_F_PCI_P2PDMA_SUPPORTED`（`:20`） |
| 一致性分配 | `alloc` / `free` | `:25` / `:28` | `dma_alloc_attrs()` 返回 NULL |
| 页分配 | `alloc_pages` / `free_pages` | `:30` / `:33` | `dma_alloc_pages()` 返回 NULL |
| 非连续分配 | `alloc_noncontiguous` / `free_noncontiguous` | `:35` / `:38` | 退化成单段连续（`kernel/dma/mapping.c:608`） |
| 导出到用户态 | `mmap` / `get_sgtable` | `:40` / `:43` | 返回 `-ENXIO` |
| 单块映射 | `map_page` / `unmap_page` | `:47` / `:50` | ⚠️ **`map_page` 是必需的**，没有它设备不能做流式 DMA |
| sg 映射 | `map_sg` / `unmap_sg` | `:58` / `:60` | 同上必需 |
| MMIO 映射 | `map_resource` / `unmap_resource` | `:62` / `:65` | `dma_map_resource()` 返回错误 |
| 同步 | `sync_single_for_cpu` / `sync_single_for_device` / `sync_sg_for_cpu` / `sync_sg_for_device` | `:68` / `:70` / `:73` / `:75` | 静默跳过（一致性设备的正常情况） |
| 遗留 | `cache_sync` | `:77` | ⚠️ 已无核心调用者，见下 |
| 能力查询 | `dma_supported` / `get_required_mask` / `max_mapping_size` / `opt_mapping_size` / `get_merge_boundary` | `:79` / `:80` / `:81` / `:82` / `:83` | 各自回退到默认值 |

⚠️ **`map_sg` 回调的返回约定和它的调用者相反。** `:53-57` 有一段专门的注释：

```c
	/*
	 * map_sg should return a negative error code on error. See
	 * dma_map_sgtable() for a list of appropriate error codes
	 * and their meanings.
	 */
	int (*map_sg)(struct device *dev, struct scatterlist *sg, int nents,
			enum dma_data_direction dir, unsigned long attrs);
```

所以同一个"sg 映射"动作有三套返回约定：**ops 回调返负错误码 → `dma_map_sg_attrs()` 对外返 0 表示失败 → `dma_map_sgtable()` 对外返 0 表示成功**。转换发生在 `__dma_map_sg_attrs()`（`kernel/dma/mapping.c:182`）里。三层三种，01 §2 已列表。

⚠️ **`opt_mapping_size` 的签名不带 `dev` 参数**（`:82` 是 `size_t (*opt_mapping_size)(void)`），和它旁边的 `max_mapping_size` 不一样。这是这个结构里唯一一个不接受 `struct device *` 的回调。

⚠️ **`cache_sync` 是遗留成员。** 它对应的旧 API `dma_cache_sync()` 已经随 `DMA_ATTR_NON_CONSISTENT`（就是 `DMA_ATTR_*` 里那个空掉的位 3）一起被 `dma_alloc_pages` 那套取代。**新写 ops 不要实现它。**

### §3.2 谁真的实现了它

全树 **24 个** `struct dma_map_ops` 实例定义。排掉 `arch/`（arm32/alpha/ia64/sparc/x86/powerpc/mips/s390）和 parisc 之后，arm64 上可能碰到的只有 **6 个**：

| 实例 | 位置 | 什么时候挂上 |
|---|---|---|
| `iommu_dma_ops` | `drivers/iommu/dma-iommu.c:1558` | 设备挂在 IOMMU 下时，由 `iommu_setup_dma_ops()` 装（arm64 从 `arch/arm64/mm/dma-mapping.c:77` 进） |
| `dma_dummy_ops` | `kernel/dma/dummy.c:33` | 需要把设备标成"禁止 DMA"时；本树唯一的安装点在 `drivers/acpi/scan.c` —— ⚠️ **arm64 无 ACPI，所以实际用不到**（记入 99 §2.7） |
| `xen_swiotlb_dma_ops` | `drivers/xen/swiotlb-xen.c:397` | Xen 客户机 |
| `xen_grant_dma_ops` | `drivers/xen/grant-dma-ops.c:281` | Xen grant 表设备 |
| `vdpasim_dma_ops` | `drivers/vdpa/vdpa_sim/vdpa_sim.c:251` | vDPA 模拟设备 |
| `vduse_dev_dma_ops` | `drivers/vdpa/vdpa_user/vduse_dev.c:827` | 用户态 vDPA |

⚠️ **一台普通 arm64 板子上，只有"设备挂在 SMMU 下"这一种情况会让 `dev->dma_ops` 非空**（`iommu_dma_ops`）。没有 SMMU 或设备被配成 bypass 时，`dev->dma_ops == NULL`，全部走 `kernel/dma/direct.c`。

⚠️ **`dma_dummy_ops` 只有 38 行，全部回调都返回失败**（`kernel/dma/dummy.c:1-38`）。它是"用一套永远失败的实现代替一堆 if 判断"的典型手法：把设备标成不可 DMA，不需要在每个 API 入口加检查。arm64 上用不到，但值得作为模式记住。

---

## §4 地址翻译：`struct bus_dma_region`

`include/linux/dma-direct.h:20-25`，四个字段，是 dts 里一条 `dma-ranges` 的内核形态：

```c
struct bus_dma_region {
	phys_addr_t	cpu_start;      /* :21 CPU 侧起点 */
	dma_addr_t	dma_start;      /* :22 设备侧起点 */
	u64		size;           /* :23 区间长度；为 0 表示数组结束 */
	u64		offset;         /* :24 cpu_start - dma_start */
};
```

`dev->dma_range_map` 指向这样一个数组，**以 `size == 0` 结尾**（`include/linux/dma-direct.h:31` 和 `:44` 的循环条件就是 `m->size`）。

两个方向的查表：

| 函数 | 位置 | 找不到时返回 |
|---|---|---|
| `translate_phys_to_dma(dev, paddr)` | `include/linux/dma-direct.h:26-37` | `DMA_MAPPING_ERROR`（`:36`） |
| `translate_dma_to_phys(dev, dma_addr)` | `include/linux/dma-direct.h:39-49` | `(phys_addr_t)-1`（`:48`） |

外层封装（arm64 走的就是这一层，因为它不定义 `CONFIG_ARCH_HAS_PHYS_TO_DMA`）：

| 函数 | 位置 | `dma_range_map` 为空时 |
|---|---|---|
| `phys_to_dma_unencrypted()` | `include/linux/dma-direct.h:58-64` | 恒等返回 `paddr` |
| `phys_to_dma()` | `include/linux/dma-direct.h:70-73` | 恒等（再过一次 `__sme_set`） |
| `dma_to_phys()` | `include/linux/dma-direct.h:75-84` | 恒等（再过一次 `__sme_clr`） |

⚠️ **两个方向的"找不到"返回值不一样**，而且都不是 0。`translate_dma_to_phys()` 的 `(phys_addr_t)-1` **没有任何调用者去检查**——所以 dts 的 `dma-ranges` 写得不对称（正向有覆盖、反向没有）时，反向翻译会安静地返回一个巨大的假物理地址。这是 06 §3 那张决策树的技术根源。

⚠️ **`offset` 字段是冗余的**（等于 `cpu_start - dma_start`），存下来只为了让热路径上少一次减法。改 `dma_range_map` 的代码必须自己维持这个不变量——`drivers/of/device.c` 的 `of_dma_get_range()` 负责这件事。

⚠️ **`__sme_set` / `__sme_clr` 是内存加密（AMD SME）的钩子**，arm64 上是空操作。它们的存在解释了为什么会有 `phys_to_dma` 和 `phys_to_dma_unencrypted` 两个函数：swiotlb 的 bounce buffer 必须是未加密的（注释在 `include/linux/dma-direct.h:66-69`）。**arm64 上这对函数完全等价**。

---

## §5 `scatterlist` 与 `sg_table`

⚠️ 这两个结构**不属于 `kernel/dma/`**，定义在 `include/linux/scatterlist.h`。放在本篇是因为 sg 类 API 的全部坑都源于它们的字段设计。

### §5.1 `struct scatterlist`

`include/linux/scatterlist.h:11-22`，最多 6 个字段，其中 2 个条件编译：

| 字段 | 行 | 谁写 | 含义 |
|---|---|---|---|
| `page_link` | `:12` | 调用者（`sg_set_page()`） | 页指针 + 两个标志位（chain / end）打包在一起 |
| `offset` | `:13` | 调用者 | 页内偏移 |
| `length` | `:14` | 调用者 | **CPU 侧**长度 |
| `dma_address` | `:15` | **`dma_map_sg` 写** | 设备侧地址 |
| `dma_length` | `:17` | **`dma_map_sg` 写** | 设备侧长度；只在 `CONFIG_NEED_SG_DMA_LENGTH` 时存在 |
| `dma_flags` | `:20` | p2pdma | 只在 `CONFIG_PCI_P2PDMA` 时存在 |

访问必须走宏，不能直接摸字段：

| 宏 | 行 | `NEED_SG_DMA_LENGTH` 关闭时 |
|---|---|---|
| `sg_dma_address(sg)` | `:31` | 恒为 `sg->dma_address` |
| `sg_dma_len(sg)` | `:34` / `:36` | ⚠️ **退化成 `sg->length`** |

⚠️ **arm64 无条件 select `NEED_SG_DMA_LENGTH`**（`arch/arm64/Kconfig:219`），所以 `dma_length` 字段存在、和 `length` 分开。**在不 select 它的架构上 `sg_dma_len()` 直接就是 `length`**——含义：那些架构上"映射后长度被改写"这件事是原地发生的，而 arm64 上是写到另一个字段。**这就是为什么绝对不能直接读 `sg->length` 当设备侧长度**：在 arm64 上你会读到映射前的值，在别的架构上你会读到映射后的值，同一份代码两种行为。

⚠️ **`page_link` 里打包了标志位。** 直接对它做指针运算是错的，必须用 `sg_page()` / `sg_next()` / `for_each_sg()`。

⚠️ 头文件里 `:24-29` 那段注释给了一条备用的终止条件：**"要么只处理 `dma_map_sg` 返回的段数，要么在第一个 `sg_dma_len(sg) == 0` 处停下"**。第二条在有些实现里不成立，**以第一条为准**。

### §5.2 `struct sg_table`

`include/linux/scatterlist.h:38-42`，三个字段，存在的唯一理由就是把 §1.0.3 那两个 nents 分开：

| 字段 | 行 | 含义 | 哪些 API 用它 |
|---|---|---|---|
| `sgl` | `:39` | 链表头 | 全部 |
| `nents` | `:40` | **映射后**的段数 | 遍历硬件描述符时用 |
| `orig_nents` | `:41` | **映射前**的段数 | `unmap` / `sync` 时用 |

⚠️ **`dma_map_sg` 那套 API 要求调用者自己记住这两个数并在正确的地方用正确的那个**；`dma_map_sgtable` 那套把这个责任收进了 API 内部（`dma_unmap_sgtable()` 用 `orig_nents`，见 `include/linux/dma-mapping.h:371-374`；`dma_sync_sgtable_for_cpu()` 同理，`:389-392`）。**这就是"新代码优先用 sgtable"的全部理由**，不是风格问题。

---

## §6 五个池的结构

### §6.1 `struct dma_coherent_mem`（per-device 一致性池）

`kernel/dma/coherent.c:13-21`，7 个字段：

| 字段 | 行 | 含义 |
|---|---|---|
| `virt_base` | `:14` | 池的内核虚拟起点 |
| `device_base` | `:15` | 池的设备侧起点 |
| `pfn_base` | `:16` | 池的起始页帧号 |
| `size` | `:17` | ⚠️ 类型是 `int`，单位是**页数**不是字节 |
| `bitmap` | `:18` | 位图分配器 |
| `spinlock` | `:19` | 保护 `bitmap`；**这是本子系统里 per-池 的那把锁** |
| `use_dev_dma_pfn_offset` | `:20` | 是否把 `dev->dma_range_map` 的平移也算进去 |

⚠️ **`size` 是 `int` 且是页数。** 一个 `struct dma_coherent_mem` 池最大 `INT_MAX` 页。这个类型选择在 6.1 里还在，用位图分配也意味着**它不适合频繁分配释放的场景**。

### §6.2 `struct io_tlb_mem` / `io_tlb_area` / `io_tlb_slot`（swiotlb 与受限池）

三层结构，一份池 → 多个 area（为了分锁）→ 多个 slot（真正的分配单元）。

`struct io_tlb_mem`，`include/linux/swiotlb.h:91-105`，13 个字段：

| 字段 | 行 | 含义 |
|---|---|---|
| `start` / `end` | `:92` / `:93` | 池的物理地址区间 |
| `vaddr` | `:94` | 内核虚拟起点 |
| `nslabs` | `:95` | 总 slot 数 |
| `used` | `:96` | 已用 slot 数（debugfs 读它） |
| `debugfs` | `:97` | 这个池的 debugfs 目录 |
| `late_alloc` | `:98` | 是否是启动后才分配的 |
| `force_bounce` | `:99` | **恒定 bounce**，受限池置真 |
| `for_alloc` | `:100` | 这个池是否也用于 `dma_alloc_*` |
| `nareas` / `area_nslabs` | `:101` / `:102` | 分了几个 area、每个多少 slot |
| `areas` | `:103` | `struct io_tlb_area` 数组 |
| `slots` | `:104` | `struct io_tlb_slot` 数组 |

`struct io_tlb_area`，`kernel/dma/swiotlb.c:91-95`，3 个字段（`used` / `index` / `lock`），字段注释在 `:85-90`。**分 area 的唯一目的是把那把 spinlock 分开，降低多核竞争**。

`struct io_tlb_slot`，`kernel/dma/swiotlb.c:65-69`，3 个字段：

| 字段 | 行 | 含义 |
|---|---|---|
| `orig_addr` | `:66` | **原始**物理地址——bounce 之后靠它把数据搬回去 |
| `alloc_size` | `:67` | 请求的字节数（可能小于 slot 对齐后的大小） |
| `list` | `:68` | 从这里开始还有几个连续空闲 slot |

⚠️ **`orig_addr` 是整个 bounce 机制的关键**：`swiotlb_bounce()`（`kernel/dma/swiotlb.c:551`）靠它在解映射和同步时把数据从 bounce 缓冲搬回原地。**这也解释了为什么 `dma_unmap_*` 的 `dir` 参数不能传错**——搬的方向由它决定。

⚠️ **全局默认池是 `io_tlb_default_mem`（`include/linux/swiotlb.h:106`），受限池是另一份**。判定用哪个只看 `dev->dma_io_tlb_mem` 这一个指针（`is_swiotlb_buffer()`，`include/linux/swiotlb.h:108-113`）。

⚠️ **`mem->force_bounce` 让"寻址能力够"这个前提失效**：受限池设备的每一次映射都要 bounce，哪怕地址本来就可达。判定入口是 `is_swiotlb_force_bounce()`（`include/linux/swiotlb.h:115-120`）。

### §6.3 atomic pool（原子上下文的一致性池）

没有自己的结构体——直接用 `mm/` 的 `struct gen_pool`。三个全局指针（`kernel/dma/pool.c:16` / `:18` / `:20`）：

| 变量 | 行 | 从哪个 zone 取 |
|---|---|---|
| `atomic_pool_dma` | `:16` | `GFP_DMA` |
| `atomic_pool_dma32` | `:18` | `GFP_DMA32` |
| `atomic_pool_kernel` | `:20` | 普通内核内存 |

配套：`pool_size_dma` / `pool_size_dma32` / `pool_size_kernel`（`:17` / `:19` / `:21`，debugfs 读它们）、`atomic_pool_size`（`:24`，由 `coherent_pool=` 参数设）、`atomic_pool_work`（`:27`，快用满时后台扩容的 workqueue）。

⚠️ **三个池对应三档寻址能力**，选哪个由请求时的 `gfp` 决定。⚠️ **后台扩容是异步的**：短时间内大量原子分配会先失败、之后才扩容成功。这不是 bug，是设计取舍。

### §6.4 CMA

用 `mm/` 的 `struct cma`，本子系统只持有指针：

| 变量 | 位置 | 含义 |
|---|---|---|
| `dma_contiguous_default_area` | `kernel/dma/contiguous.c:60` | 全局默认 CMA 区（**非 static**，别处也用） |
| `dma_contiguous_pernuma_area[MAX_NUMNODES]` | `kernel/dma/contiguous.c:100` | 每 NUMA 节点一个，`CONFIG_DMA_PERNUMA_CMA` |
| `dev->cma_area` | `include/linux/device.h:610` | per-device 的；NULL 则回落到全局 |

大小的决定顺序在 `dma_contiguous_reserve()`（`kernel/dma/contiguous.c:168-200`）：**命令行 `cma=`（`:176`）优先，否则按 `CMA_SIZE_SEL_*` 在 `size_bytes`（宏在 `:72`）和百分比之间选**（`:183`–`:189`）。

---

## §7 DMA-API debug 的账本结构

只在 `CONFIG_DMA_API_DEBUG` 下存在。`kernel/dma/debug.c`：

| 结构/变量 | 行 | 含义 |
|---|---|---|
| `struct dma_debug_entry` | `:66-82` | 一次映射的完整记录；`____cacheline_aligned_in_smp` |
| `struct hash_bucket` | `:86-89` | 一条哈希链 + 一把 spinlock |
| `dma_entry_hash[HASH_SIZE]` | `:92` | **16384** 条链（`HASH_SIZE` 在 `:29`） |
| `free_entries` + `free_entries_lock` | `:94` / `:96` | 预分配 entry 的空闲链和它的锁 |
| `global_disable` | `:99` | 出过一次错就整个关掉 |
| `dma_debug_initialized` | `:102` | 早期启动阶段的门 |
| `num_free_entries` / `nr_total_entries` / `nr_prealloc_entries` | `:117` / `:119` / `:122` | debugfs 里那几个计数 |

`struct dma_debug_entry` 的字段（`:67`–`:81`）：`list` / `dev` / `dev_addr` / `size` / `type` / `direction` / `sg_call_ents` / `sg_mapped_ents` / `pfn` / `offset` / `map_err_type`，再加 `CONFIG_STACKTRACE` 下的 `stack_len` + `stack_entries[]`。

⚠️ **`sg_call_ents` 和 `sg_mapped_ents` 是两个字段** —— 就是 §5.2 那两个 nents 的账本副本。debug 层能抓出"unmap 时 nents 传错"正是因为它把两个都记下来了（`check_unmap()` 在 `:956`）。

⚠️ **预分配 65536 条 entry**（`PREALLOC_DMA_DEBUG_ENTRIES` 在 `:33`，即 `1 << 16`），不够时可用 `dma_debug_entries=` 参数调（`Documentation/admin-guide/kernel-parameters.txt:1090`）。

⚠️ **`global_disable` 是一次性的**：`dma_debug_disabled()`（`:104-107`）一旦返回真，后续所有检查全部跳过。**含义：第一条错误报告之后的日志不能当"没有别的错"来读**。详见 07 §2.1。

---

## §8 关系图

### §8.1 核心抽象与消费者

本子系统**没有自己的 `bus_type`**，所以这张图画的不是总线层级，而是"谁持有状态、谁提供实现、谁在消费"。

```
        ┌─────────────────────────────────────────────────┐
        │  消费者：任何要做 DMA 的驱动                     │
        │  （网卡 / 存储 / 显示 / 音频 / crypto …）        │
        └────────────────────┬────────────────────────────┘
                             │ dma_alloc_coherent / dma_map_* / dma_sync_*
                             │ include/linux/dma-mapping.h（唯一合法入口）
                             ▼
        ┌─────────────────────────────────────────────────┐
        │  kernel/dma/mapping.c   ——  分派层              │
        │  dma_map_direct() :137 / dma_alloc_direct() :131 │
        │  唯一职责：判断走 direct 还是走 dev->dma_ops     │
        └───────┬───────────────────────────┬─────────────┘
      ops==NULL │                           │ ops!=NULL
   （arm64 常态）│                           │（挂在 SMMU 下 / Xen / vDPA）
                ▼                           ▼
   ┌────────────────────────┐   ┌──────────────────────────┐
   │ kernel/dma/direct.c    │   │ struct dma_map_ops       │
   │ + swiotlb.c            │   │ dma-map-ops.h:22         │
   │ + contiguous.c/pool.c  │   │ 24 个实例，arm64 上实际   │
   │ + coherent.c           │   │ 只有 iommu_dma_ops       │
   └───────┬────────────────┘   └───────────┬──────────────┘
           │                                │
           │  arch_sync_dma_for_{cpu,device}│（direct 路径才调）
           ▼                                ▼
   ┌─────────────────────────────────────────────────────┐
   │  arch/arm64/mm/dma-mapping.c（80 行）               │
   │  :16 clean / :24 inval / :35 prep_coherent          │
   │  :64 arch_setup_dma_ops → :77 iommu_setup_dma_ops   │
   └─────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────┐
   │  状态寄居处：struct device（device.h:590-653）      │
   │  dma_ops / dma_mask / coherent_dma_mask /           │
   │  bus_dma_limit / dma_range_map / dma_parms /        │
   │  dma_mem / cma_area / dma_io_tlb_mem /              │
   │  dma_coherent:1 / dma_ops_bypass:1                  │
   └─────────────────────────────────────────────────────┘
```

三个要点：

⚠️ **`struct device` 是这个子系统的"实例"。** 没有 `struct dma_device`、没有 `dma_class`、没有 `/sys/bus/dma/`。要看某个设备的 DMA 配置，只能读 `struct device` 里那 13 个字段。

⚠️ **`arch_*` 钩子只在 direct 路径上被调。** 走 `iommu_dma_ops` 时，缓存维护由 `drivers/iommu/dma-iommu.c` 自己决定何时调 `arch_sync_dma_for_*`。**"设置了 `ARCH_HAS_SYNC_DMA_FOR_DEVICE` 就一定会同步缓存"是错的**——是不是同步由那一层决定。

⚠️ **`kernel/dma/mapping.c` 本身不做任何实际工作。** 831 行里绝大部分是"检查 + 分派 + 调 debug 钩子 + 打 tracepoint"。真正搬数据/算地址的代码在 direct.c 和 swiotlb.c。

### §8.2 数据流：一次 `dma_map_single()` 走到哪里

以 arm64、无 IOMMU、`DMA_TO_DEVICE`、缓冲区在 lowmem 为例：

```
驱动: dma_map_single(dev, ptr, size, DMA_TO_DEVICE)
  └─ include/linux/dma-mapping.h:328  dma_map_single_attrs()
       ├─ dev_WARN_ONCE(is_vmalloc_addr(ptr))  ← ⚠️ vmalloc 内存在这里被拒
       └─ kernel/dma/mapping.c:143  dma_map_page_attrs()
            ├─ BUG_ON(!valid_dma_direction(dir))
            ├─ dma_map_direct(dev, ops)  :137
            │    └─ dma_go_direct()  :112   ← ops==NULL 或 dma_ops_bypass
            ├─【direct 分支】kernel/dma/direct.h:84  dma_direct_map_page()
            │    ├─ phys = page_to_phys(page) + offset
            │    ├─ is_swiotlb_force_bounce(dev)?      ← 受限池设备：必 bounce
            │    │    └─ swiotlb_map()  kernel/dma/swiotlb.c:914
            │    ├─ dma_capable(dev, dma_addr, size, true)?  dma-direct.h:99
            │    │    └─ 否 → swiotlb_map() 或返回 DMA_MAPPING_ERROR
            │    ├─ phys_to_dma(dev, phys)  dma-direct.h:70
            │    │    └─ dev->dma_range_map ? 查表 : 恒等
            │    └─ !dev_is_dma_coherent(dev)?
            │         └─ arch_sync_dma_for_device(phys, size, dir)
            │              arch/arm64/mm/dma-mapping.c:16 → dcache_clean_poc()
            ├─【ops 分支】ops->map_page(dev, page, offset, size, dir, attrs)
            ├─ kernel/dma/debug.c  debug_dma_map_page()   ← DMA_API_DEBUG 才有
            └─ trace_dma_map_page(...)                     ← tracepoint
返回: dma_addr_t，失败时 == DMA_MAPPING_ERROR（不是 0，不是负数）
```

反向（`dma_unmap_single` + `DMA_FROM_DEVICE`）的关键差异只有两处：**缓存操作换成 `dcache_inval_poc()`**（`arch/arm64/mm/dma-mapping.c:32`），而且 **`DMA_TO_DEVICE` 会在 `:29-30` 直接 return**——解映射一个只写方向的缓冲区在 arm64 上不碰缓存。这个不对称在 08 §4 展开。

⚠️ **失败值是 `DMA_MAPPING_ERROR`（`include/linux/dma-mapping.h:75`，即 `~(dma_addr_t)0`），必须用 `dma_mapping_error()` 判**（`:95-102`）。**不能判 0**——0 在很多平台上是合法总线地址。

---

## §9 全局变量与锁

### §9.1 全局变量

跨文件可见（非 static）的只有三个，其余都关在本文件里：

| 变量 | 位置 | 类型 | 谁改 |
|---|---|---|---|
| `zone_dma_bits` | 定义 `kernel/dma/direct.c:23`，声明 `include/linux/dma-direct.h:15` | `unsigned int`，初值 **24** | `arch/` 的早期内存初始化改它 |
| `dma_contiguous_default_area` | `kernel/dma/contiguous.c:60` | `struct cma *` | 启动期 CMA 预留 |
| `io_tlb_default_mem` | `include/linux/swiotlb.h:106` | `struct io_tlb_mem` | swiotlb 初始化 |

⚠️ **`zone_dma_bits = 24` 这个初值是 ISA 遗产**（16MB），arm64 上由平台按实际情况改写。它决定 `GFP_DMA` zone 的边界，也决定 `dma_direct_optimal_gfp_mask()` 挑哪个 zone。**改这个值的代码不在本子系统里**。

本子系统内的 static 全局（按文件）：

| 文件 | 变量 | 行 |
|---|---|---|
| mapping.c | `dma_default_coherent` | `:20` |
| direct.c | 无（只有上面那个非 static 的） | — |
| pool.c | `atomic_pool_{dma,dma32,kernel}` + 三个 `pool_size_*` + `atomic_pool_size` + `atomic_pool_work` | `:16`–`:27` |
| contiguous.c | `dma_contiguous_pernuma_area[]` `:100`、`size_bytes` 宏 `:72`、`selected_size` `:168` | — |
| swiotlb.c | `swiotlb_force_bounce` `:71`、`swiotlb_force_disable` `:72`、`default_nslabs` `:78`、`default_nareas` `:79` | — |
| debug.c | `dma_entry_hash[]` `:92`、`free_entries` `:94`、`global_disable` `:99`、`dma_debug_initialized` `:102`、三个计数 `:117`/`:119`/`:122` | — |

⚠️ **`dma_default_coherent`（`kernel/dma/mapping.c:20`）决定"没有显式配置时设备算不算一致性"**。它和 per-device 的 `dev->dma_coherent`（`include/linux/device.h:650`）是两层：全局默认 + 逐设备覆盖。dts 里的 `dma-coherent` 属性改的是后者。

### §9.2 锁

本子系统**没有一把全局大锁**，只有四把局部锁，各自保护一份池的分配状态：

| 锁 | 位置 | 保护什么 | 上下文 |
|---|---|---|---|
| `mem->spinlock` | `kernel/dma/coherent.c:19` | 一个 per-device 一致性池的位图 | 可在原子上下文取 |
| `area->lock` | `kernel/dma/swiotlb.c:94` | 一个 swiotlb area 的 slot 分配 | 同上；**分 area 就是为了拆这把锁** |
| `free_entries_lock` | `kernel/dma/debug.c:96` | debug 的预分配 entry 空闲链 | 同上 |
| `bucket->lock` | `kernel/dma/debug.c:88` | 一条 debug 哈希链 | 同上；16384 把 |

⚠️ **没有一把锁保护 `struct device` 里那 13 个 DMA 字段。** 它们的写入全部发生在 probe 之前/期间（`arch_setup_dma_ops()`、`dma_set_mask()`）——**并发安全靠"只在设备还没开始 DMA 时改"这个约定**，不靠锁。含义：**运行期改 mask 是未定义行为**，`dma_set_mask()` 只该在 probe 里调。

⚠️ **CMA 的锁不在本子系统里**（在 `mm/cma.c`），atomic pool 的锁在 `lib/genalloc.c`。所以"本子系统的锁"这个说法只覆盖上面那四把。

---

## §10 命名约定速查

| 前缀 / 形态 | 含义 | 例 |
|---|---|---|
| `dma_*` | 对外 API | `dma_map_single()` |
| `dmam_*` | devres 版本，设备卸载时自动释放 | `dmam_alloc_coherent()` `include/linux/dma-mapping.h:557` |
| `dma_direct_*` | 无 IOMMU 的直接路径实现 | `dma_direct_alloc()` `kernel/dma/direct.c:208` |
| `__dma_*` | 同名函数的内部版本（少一层检查或不加锁） | `__dma_alloc_pages()` `kernel/dma/mapping.c:546` |
| `arch_dma_*` / `arch_sync_dma_*` / `arch_setup_dma_ops` | 架构必须提供的钩子，由 `ARCH_HAS_*` 门控 | `arch/arm64/mm/dma-mapping.c:16` |
| `swiotlb_*` | bounce buffer 层 | `swiotlb_map()` `kernel/dma/swiotlb.c:914` |
| `dma_contiguous_*` | CMA 层 | `kernel/dma/contiguous.c` |
| `debug_dma_*` | DMA-API debug 的钩子；关掉配置时是空 inline | `kernel/dma/debug.h` |
| `dma_alloc_wc` / `dma_mmap_wc` | write-combine 的便捷包装，等价于带 `DMA_ATTR_WRITE_COMBINE` | `include/linux/dma-mapping.h:564`/`:582` |
| `DMA_ATTR_*` | 传给 `*_attrs` 版本的属性位 | `include/linux/dma-mapping.h:24`–`:63` |
| `ARCH_HAS_*` / `ARCH_DMA_*` | 架构能力声明，架构在自己的 Kconfig 里 select | `arch/arm64/Kconfig:43` |
| `DMA_*`（Kconfig） | 本子系统的功能开关 | `kernel/dma/Kconfig:79` |

⚠️ **三组命名容易混：**

- **`dma_alloc_*` vs `dma_map_*`**：前者是"分配一块适合 DMA 的内存"，后者是"把已有内存交给设备"。混用的典型后果是对 `kmalloc` 的内存调 `dma_free_coherent()`。
- **`dma_sync_single_for_cpu` vs `dma_sync_sg_for_cpu` vs `dma_sync_sgtable_for_cpu`**：第三个才是新代码该用的（用 `orig_nents`，不会传错 nents）。
- **`dma_pool_*`（`mm/dmapool.c`）不属于本子系统。** 它是建在 `dma_alloc_coherent()` 之上的小对象分配器，名字像但层次不同。`dev->dma_pools`（`include/linux/device.h:603`）是它的链表头。

---

## §11 下一篇读什么

| 想知道 | 去 |
|---|---|
| 这些结构在什么时候被填、被谁填 | 03 §A |
| 地址翻译和缓存维护的完整逻辑 | 03 §B |
| swiotlb / CMA / 受限池各自的独立机制 | 04 |
| 怎么在驱动里正确用这些 API | 05 |
| `dma-ranges` / `dma-coherent` / 三个 reserved-memory 怎么写 | 06 |
| 出问题了看哪些 debugfs / tracepoint | 07 |
| 为什么要区分一致性和流式、为什么 arm64 的同步是不对称的 | 08 §2 / 08 §4 |

