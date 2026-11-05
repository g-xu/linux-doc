---
title: 内部实现
---

# dma-mapping 内部实现

本篇主轴是 **Part A 时间线**：从内核启动预留内存，一路到设备卸载时拆掉 DMA 配置。**Part B 是横切主题**（地址翻译、缓存维护、bounce 决策），它们在时间线上没有固定位置，每次映射都会走。**Part C 是对这套实现的评价**，含源码里自陈的设计债。

章节号自带轴信息：`§A3` 在时间线上，`§B2` 是横切，`§C1` 是评价。

---

## §0 导航

### §0.1 分层图

```
┌──────────────────────────────────────────────────────────────┐
│ 消费者驱动    dma_alloc_coherent / dma_map_sg / dma_sync_*   │
└──────────────────────────┬───────────────────────────────────┘
                           │ include/linux/dma-mapping.h
┌──────────────────────────▼───────────────────────────────────┐
│ 第 1 层  分派 + 校验 + 埋点     kernel/dma/mapping.c (831)    │
│   · BUG_ON(!valid_dma_direction)  · debug 钩子  · tracepoint  │
│   · dma_map_direct() :137 决定往左还是往右                    │
└───────┬──────────────────────────────────────┬───────────────┘
        │ 左：dev->dma_ops == NULL              │ 右：非 NULL
┌───────▼──────────────────────────┐  ┌────────▼───────────────┐
│ 第 2 层L  direct 路径             │  │ 第 2 层R  ops 路径      │
│   direct.c (657) + direct.h (126)│  │  iommu_dma_ops          │
│   地址翻译 / mask 校验 / 缓存同步 │  │  （drivers/iommu/）     │
└───────┬──────────────────────────┘  └────────────────────────┘
        │ 需要内存 / 需要 bounce
┌───────▼──────────────────────────────────────────────────────┐
│ 第 3 层  内存来源（各自独立，见 04）                          │
│   contiguous.c CMA · pool.c 原子池 · coherent.c per-dev 池    │
│   swiotlb.c bounce 池 + 受限池 · remap.c vmap · ops_helpers.c │
└───────┬──────────────────────────────────────────────────────┘
        │ 缓存维护
┌───────▼──────────────────────────────────────────────────────┐
│ 第 4 层  arch/arm64/mm/dma-mapping.c (80)                    │
│   arch_sync_dma_for_device :16 / _for_cpu :24                │
│   arch_dma_prep_coherent :35 / arch_setup_dma_ops :64        │
└──────────────────────────────────────────────────────────────┘
```

⚠️ **第 1 层不做实际工作，第 4 层只有 80 行。** 这个子系统 6182 行代码的大头在第 3 层（swiotlb 1112 + debug 1608 = 2720 行，占 44%），而这两个在多数板子上要么不启用要么不触发。**真正的热路径代码不到 1000 行**（mapping.c 的分派 + direct.c/direct.h 的翻译与同步）。

### §0.2 生命周期时间线

```
【启动期，没有任何 device 存在】
 setup_arch()  arch/arm64/kernel/setup.c
   ├─ :334 arm64_memblock_init()
   │        └─ arch/arm64/mm/init.c:390 early_init_fdt_scan_reserved_mem()
   │                 ↑ dts 的 reserved-memory 在这里被登记（还没分配）
   ├─ :336 paging_init()
   ├─ :344 unflatten_device_tree()
   └─ :346 bootmem_init()   arch/arm64/mm/init.c:398-446
            ├─ :430 zone_sizes_init()  → :224 arm64_dma_phys_limit = max_zone_phys(zone_dma_bits)
            └─ :435 dma_contiguous_reserve(arm64_dma_phys_limit)   ← CMA 在这里预留
 mem_init()  arch/arm64/mm/init.c:452
   └─ :454 swiotlb_init(max_pfn > PFN_DOWN(arm64_dma_phys_limit), SWIOTLB_VERBOSE)
                                                      ↑ bounce 池在这里分配

【initcall 期】
 core_initcall       kernel/dma/coherent.c:399  dma_init_reserved_memory()
 core_initcall       kernel/dma/debug.c:929     dma_debug_init()
 core_initcall_sync  kernel/dma/debug.c:823     dma_debug_fs_init()
 postcore_initcall   kernel/dma/pool.c:222      dma_atomic_pool_init()
 late_initcall       kernel/dma/swiotlb.c:995   swiotlb_create_default_debugfs()

【每个设备被创建时】
 platform 设备：drivers/base/platform.c:1450 of_dma_configure(dev, np, true)
 amba 设备：   drivers/amba/bus.c:360        同上
   └─ drivers/of/device.c:114-223  of_dma_configure_id()      ← §A2
        └─ :217 arch_setup_dma_ops()  arch/arm64/mm/dma-mapping.c:64-80  ← §A3

【probe 期，驱动主动做的】
 dma_set_mask_and_coherent()  → §A4
 dma_alloc_coherent()         → §A5

【运行期，每笔传输】
 dma_map_* / dma_sync_* / dma_unmap_*  → §A6 / §A7 / §A8

【卸载期】
 dma_free_coherent() → §A9
 arch_teardown_dma_ops()  arch/arm64/mm/dma-mapping.c:58-61  → §A10
```

### §0.3 哪些内容不在本篇

| 想知道 | 去 |
|---|---|
| API 有哪些、返回什么 | 01 |
| 结构体字段逐个含义 | 02 |
| swiotlb / CMA / 受限池 / debug 各自的完整机制 | 04 |
| 驱动侧怎么写 | 05 |
| dts 怎么写、属性谁消费 | 06 |
| 怎么看运行时状态 | 07 |
| 为什么要这么设计 | 08 |

---

# Part A：时间线

## §A1 启动期：三笔内存预留（`arch/arm64/mm/init.c:266-457`）

arm64 上和本子系统相关的启动动作按次序有三笔，**次序不可换**，因为后两笔依赖第一笔算出来的 `arm64_dma_phys_limit`：

| 次序 | 动作 | 位置 | 依赖 |
|---|---|---|---|
| 1 | 登记 dts 的 reserved-memory 区域 | `arch/arm64/mm/init.c:390` | 需要 `paging_init()` 已完成 |
| 2 | 算出 `arm64_dma_phys_limit`，划 zone | `arch/arm64/mm/init.c:224` / `:430` | 需要 `zone_dma_bits` |
| 3 | 预留 CMA | `arch/arm64/mm/init.c:435` | ⚠️ 注释在 `:433` 明确说"必须在 `arm64_dma_phys_limit` 初始化之后" |
| 4 | 分配 swiotlb 池 | `arch/arm64/mm/init.c:454` | 需要 `memblock` 还能分配（在 `memblock_free_all()` `:457` 之前） |

⚠️ **`arm64_dma_phys_limit` 有两个定义，取决于配置**：`arch/arm64/mm/init.c:88`（有 `ZONE_DMA` 或 `ZONE_DMA32` 时，值由 `:224` 运行期算）和 `:90`（都没有时，直接是 `PHYS_MASK + 1`）。它同时被 crashkernel 预留用（`:96` 的 `CRASH_ADDR_LOW_MAX`），所以改 `zone_dma_bits` 会连带影响 kdump 的内存布局。注释 `:66-79` 专门解释了这个耦合。

⚠️ **`swiotlb_init()` 的第一个参数是"要不要开"**：`max_pfn > PFN_DOWN(arm64_dma_phys_limit)`。含义是**只有系统内存超出 DMA zone 上限时才真的分配 bounce 池**。一台内存全在 4GB 以下且 `zone_dma_bits` 覆盖全部内存的板子上，swiotlb 编译进来了但池是空的。这解释了为什么有些板子 `/sys/kernel/debug/swiotlb/` 里 `io_tlb_nslabs` 是 0。

⚠️ **`dma_contiguous_reserve()`（`kernel/dma/contiguous.c:166-202`）自己不做任何 dts 解析。** 它只看命令行 `cma=`（`:176`）和 `CMA_SIZE_MBYTES`/`CMA_SIZE_PERCENTAGE` 的编译期默认（`:183`-`:189`），预留出的是**全局默认 CMA 区**。dts 里 `shared-dma-pool` + `reusable` 那条路径是另一套，走 `rmem_cma_setup()`（`:399-439`），在 `:430` 才可能把自己设成 `dma_contiguous_default_area`。**两条路都能产出默认 CMA 区，命令行赢**——`rmem_cma_setup()` 在 `:406-410` 发现命令行给了 `cma=` 时主动返回 `-EBUSY` 让位，于是 `:193` 的条件 `if (selected_size && !dma_contiguous_default_area)` 成立。完整场景表见 06 §5.2.1。

## §A2 设备创建期：`of_dma_configure_id()`（`drivers/of/device.c:114-223`）

⚠️ 这个函数**不在 `kernel/dma/` 里**，但它是 dts 到 `struct device` 那 13 个字段之间唯一的桥。只有两个调用者：`drivers/base/platform.c:1450` 和 `drivers/amba/bus.c:360`，**两处都传 `force_dma = true`**。

按执行顺序：

| 步 | 行 | 动作 | 失败时 |
|---|---|---|---|
| 1 | `:130` | `of_dma_get_range(bus_np, &map)` 解析 `dma-ranges` | `-ENODEV` 被吞成成功（`:139`），其余错误上抛 |
| 2 | `:158-167` | 校验每条 range 的 size：`size & 1` 为真则 `dev_warn(dev, "Invalid size 0x%llx for dma-range(s)\n")` 并返 `-EINVAL` | ⚠️ 见下 |
| 3 | `:177-179` | `dev->dma_mask` 为空则 `dev_warn(dev, "DMA mask not set\n")`，然后指向 `dev->coherent_dma_mask` | 只是警告 |
| 4 | `:182` | 没有 `dma-ranges` 时用 `coherent_dma_mask` 反推 size | — |
| 5 | `:192-194` | `mask = DMA_BIT_MASK(ilog2(end) + 1)`，然后**同时收窄** `coherent_dma_mask` 和 `*dev->dma_mask` | ⚠️ 见下 |
| 6 | `:197-198` | 写 `dev->bus_dma_limit = end` 和 `dev->dma_range_map = map` | — |
| 7 | `:201` | `coherent = of_dma_is_coherent(np)` 读 `dma-coherent` 属性 | — |
| 8 | `:205-211` | `of_iommu_configure()`；`-EPROBE_DEFER` 时把 `:209` 的 `dev->dma_range_map = NULL` 回滚后返回 | ⚠️ 见下 |
| 9 | `:217` | `arch_setup_dma_ops(dev, dma_start, size, iommu, coherent)` | 无返回值 |
| 10 | `:220` | **只在没有 iommu 时** `of_dma_set_restricted_buffer(dev, np)` | — |

⚠️ **第 5 步是"设备还没 probe，mask 就已经被 dts 改了"。** 驱动 probe 里调 `dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64))` 时，实际生效的是 dts 算出来的那个上限和驱动请求值的交集——`dma_set_mask()` 会先过 `dma_supported()`（见 §A4）。**所以"我明明设了 64 位 mask 为什么还在 bounce"的答案常常在 dts 的 `dma-ranges` 里**。

⚠️ **第 8 步的回滚只回滚了 `dma_range_map`，没回滚 mask。** `:192-194` 收窄过的 `coherent_dma_mask` 和 `*dev->dma_mask` 在 `-EPROBE_DEFER` 之后仍然是收窄后的值；下一轮重试会在已收窄的基础上再收窄一次。因为 `&=` 是幂等的，实际不会越收越小——**但这依赖"mask 只会被 `&=`"这个不变量**，不是显式的回滚。

⚠️ **第 10 步的条件很关键**：`restricted-dma-pool` 只在**没有 IOMMU** 时生效。有 SMMU 的设备写了 `restricted-dma-pool` 是死字。这条在 06 §4.3 展开。

⚠️ **第 2 步那个 `size & 1` 是一个 workaround**，不是普通校验：合法的 range size 至少是页对齐的，最低位为 1 意味着 dts 写错。它先于所有赋值，所以是唯一能在 dts 写错时明确报错的地方——**其余写错的形态（区间不对称、覆盖不全）不会报任何错**（见 02 §4 的翻译失败返回值）。

## §A3 `arch_setup_dma_ops()`（`arch/arm64/mm/dma-mapping.c:64-80`）

整个函数 17 行，做四件事：

```c
	int cls = cache_line_size_of_cpu();              /* :67 */

	WARN_TAINT(!coherent && cls > ARCH_DMA_MINALIGN,  /* :69-73 */
		   TAINT_CPU_OUT_OF_SPEC,
		   "%s %s: ARCH_DMA_MINALIGN smaller than CTR_EL0.CWG (%d < %d)",
		   dev_driver_string(dev), dev_name(dev),
		   ARCH_DMA_MINALIGN, cls);

	dev->dma_coherent = coherent;                     /* :75 */
	if (iommu)
		iommu_setup_dma_ops(dev, dma_base, dma_base + size - 1);  /* :77 */

	xen_setup_dma_ops(dev);                           /* :79 */
```

四个要点：

⚠️ **`dev->dma_ops` 在这里不会被赋值。** 唯一可能赋值的是 `:77` 的 `iommu_setup_dma_ops()`（挂 `iommu_dma_ops`）和 `:79` 的 `xen_setup_dma_ops()`。**没有 IOMMU 的普通 arm64 设备走完这个函数后 `dev->dma_ops` 仍是 NULL**——这就是 §B1 要解释的"arm64 为什么没有自己的 dma_ops"。

⚠️ **`WARN_TAINT` 会污染内核（`TAINT_CPU_OUT_OF_SPEC`）**，条件是"非一致性设备 + 运行时缓存行大于编译期 `ARCH_DMA_MINALIGN`"。这条 taint 一旦触发，**DMA API 的正确性就没有保证了**：编译期按 `ARCH_DMA_MINALIGN` 对齐的缓冲区在这颗 CPU 上可能与相邻数据共享缓存行。它是警告而不是拒绝，因为拒绝会让机器起不来。

⚠️ **`dev->dma_coherent` 是唯一无条件写入的字段**，值直接来自 dts 的 `dma-coherent`。没有这个属性的设备是非一致性的——**这决定后面每一次映射要不要做缓存维护**。

⚠️ **`arch_teardown_dma_ops()`（`:58-61`）整个函数只有一行 `dev->dma_ops = NULL`，而且包在 `#ifdef CONFIG_IOMMU_DMA`（`:57`）里。** 含义：不开 `IOMMU_DMA` 的 arm64 内核**没有** `arch_teardown_dma_ops` 这个符号，通用层会用 `include/linux/dma-map-ops.h` 里的空 inline。它也**不清 `dma_coherent`、不清 `dma_range_map`、不清 mask**——设备结构体被释放时这些字段跟着消失，靠的不是显式清理。

## §A4 `dma_set_mask()` / `dma_set_coherent_mask()`（`kernel/dma/mapping.c:753-783`）

两个函数结构一样，各 14 行：

| 步 | `dma_set_mask` | `dma_set_coherent_mask` |
|---|---|---|
| 1 | `dev->dma_mask` 为空则返 `-EIO` | 无此检查 |
| 2 | `mask &= dma_get_required_mask(dev)`? | 否 |
| 3 | 调 `arch_dma_set_mask(dev, mask)`（弱符号，`:748` 声明） | 无 |
| 4 | `!dma_supported(dev, mask)` 则返 `-EIO` | 同 |
| 5 | 写 `*dev->dma_mask = mask` | 写 `dev->coherent_dma_mask = mask` |

⚠️ **两个 mask 是分开的，而且必须分开设。** `dma_set_mask_and_coherent()`（`include/linux/dma-mapping.h:448`）先调前者，成功了才调后者——**前者失败会直接返回，后者不会被调**。所以 `dma_set_mask_and_coherent()` 返回错误时，两个 mask 可能只有一个被改了、也可能一个都没改。

⚠️ **`dma_supported()` 在 `kernel/dma/mapping.c:714-727` 是 `static`。** 它先判 `dma_map_direct()`，走 direct 时调 `dma_direct_supported()`（`kernel/dma/direct.c:580-601`），否则调 `ops->dma_supported`。**没有 `ops->dma_supported` 的 ops 一律认为"支持"**——这个"缺回调等于放行"的约定和 §3.1 那张表里其余"缺了就失败"的回调相反。

⚠️ **`dma_coerce_mask_and_coherent()`（`include/linux/dma-mapping.h:460`）绕过全部检查**：它在 `:462` 直接 `dev->dma_mask = &dev->coherent_dma_mask` 然后调 `dma_set_mask_and_coherent()`。用它的前提是"我知道这个设备的 `dma_mask` 指针没被初始化"，**不是"我想强行设一个更宽的 mask"**。

⚠️ **运行期改 mask 没有锁保护**（见 02 §9.2）。这两个函数只该在 probe 里调。

## §A5 `dma_alloc_attrs()` → `dma_direct_alloc()`：一致性内存的分配决策树

`kernel/dma/mapping.c:493-516` 是分派壳，真正的决策在 `kernel/dma/direct.c:208-322`。**这是本子系统里分支最多的一个函数**，115 行里有 8 个出口。按判断顺序：

```
dma_direct_alloc(dev, size, dma_handle, gfp, attrs)          direct.c:208
 │
 ├─ :214 size = PAGE_ALIGN(size)      ← ⚠️ 请求 100 字节实际给一整页
 ├─ :215 attrs & DMA_ATTR_NO_WARN → gfp |= __GFP_NOWARN
 │
 ├─【出口 1】:219-221 NO_KERNEL_MAPPING && !加密 && !受限池
 │      → dma_direct_alloc_no_mapping() :190-206
 │        返回的是 struct page * 伪装成 void *（:203 注释"opaque cookie"）
 │        ⚠️ 这个返回值不能解引用、不能 memcpy
 │
 ├─ :223 if (!dev_is_dma_coherent(dev))  ← 非一致性设备走这一整块
 │   ├─【出口 2】:228-233 四个配置全关 → arch_dma_alloc()
 │   │     ⚠️ arm64 不到这里（arch/arm64/Kconfig:120 select 了 DMA_DIRECT_REMAP）
 │   ├─【出口 3】:239-242 DMA_GLOBAL_POOL → dma_alloc_from_global_coherent()
 │   │     ⚠️ arm64 不到这里（不 select DMA_GLOBAL_POOL）
 │   ├─ :248 remap = IS_ENABLED(CONFIG_DMA_DIRECT_REMAP)   ← arm64 恒真
 │   ├─【出口 4】:250-252 remap && 不能睡（GFP_ATOMIC）
 │   │     → dma_direct_alloc_from_pool() 从原子池取
 │   └─ :254-256 !remap 且没有 SET_UNCACHED → return NULL；否则 set_uncached
 │
 ├─【出口 5】:264-265 内存加密 && 不能睡 → 原子池
 │     ⚠️ arm64 上 force_dma_unencrypted() 恒假
 │
 ├─ :268 page = __dma_direct_alloc_pages(...)   ← 真正拿页，见下
 │     :269 失败 → return NULL
 │
 ├─ :277-280 PageHighMem(page) → 强制 remap = true, set_uncached = false
 │     ⚠️ CMA 可能返回 highmem 页（注释 :272-276）
 │
 ├─ :282 if (remap):     ← arm64 非一致性设备走这条
 │     :283 prot = dma_pgprot(dev, PAGE_KERNEL, attrs)
 │     :289 arch_dma_prep_coherent(page, size)    ← 清缓存别名
 │     :292 dma_common_contiguous_remap(...)      ← remap.c:38 建 uncached 映射
 │   else:                ← 一致性设备走这条
 │     :297 ret = page_address(page)              ← 直接用线性映射
 │
 ├─ :302 memset(ret, 0, size)   ← ⚠️ 手工清零，不用 __GFP_ZERO（:267 注释）
 ├─ :304-309 set_uncached → arch_dma_set_uncached()（arm64 不涉及）
 └─ :311 *dma_handle = phys_to_dma_direct(dev, page_to_phys(page))
```

**arm64 上的实际路径只有两条**：一致性设备走 `:297`（直接返回线性映射地址，零额外开销）；非一致性设备走 `:282-295`（清缓存 + 建一份 uncached 的 vmap 映射）。**后者返回的虚拟地址和 `page_address(page)` 不是同一个地址**，这就是为什么 `dma_alloc_coherent()` 的返回值不能用 `virt_to_phys()` 反算。

`__dma_direct_alloc_pages()`（`:117-160`）的页来源优先级：

| 优先 | 行 | 来源 | 条件 |
|---|---|---|---|
| 1 | `:126-127` | 受限池（`swiotlb_alloc`） | `is_swiotlb_for_alloc(dev)`，直接返回不再往下 |
| 2 | `:131` | `dma_alloc_contiguous()` → CMA 或 buddy | 总会试 |
| 3 | `:141` | `alloc_pages_node()` | 上一步失败或地址不合格 |
| 4 | `:146-150` | 加 `GFP_DMA32` 重试 | `ZONE_DMA32` 存在且还没试过 |
| 5 | `:152-155` | 换成 `GFP_DMA` 重试 | `ZONE_DMA` 存在且还没试过 |

⚠️ **每一步拿到页后都要过 `dma_coherent_ok(dev, page_to_phys(page), size)`（`:68-76`）**，不合格就还回去。所以"分配成功但地址超出 mask"这件事在这一层就被排除了——**`dma_alloc_coherent()` 返回非 NULL 就保证地址在 mask 内**，这是它和 `dma_map_*` 的关键区别（后者要靠 bounce 兜底）。

⚠️ **`gfp |= dma_direct_optimal_gfp_mask(...)`（`:129`）在第一次尝试前就加了 zone 提示**，所以第 4、5 步的重试只在第一次猜错时发生。注释 `:57` 说明 `GFP_DMA32`/`GFP_DMA` 在没有对应 zone 时是空操作。

⚠️ **per-device 一致性池比这一整棵树都优先。** `dma_alloc_attrs()` 的第一件事是 `kernel/dma/mapping.c:501` 的 `dma_alloc_from_dev_coherent()`——命中就直接返回，`dma_direct_alloc()` 一行都不执行。所以给设备配了 `shared-dma-pool`（非 `reusable`）之后，CMA、原子池、zone 提示、`arch_dma_prep_coherent` 全部不参与。

## §A6 `dma_map_page_attrs()`（`kernel/dma/mapping.c:143-164`）→ `dma_direct_map_page()`（`kernel/dma/direct.h:84-112`）

分派壳做四件事：`BUG_ON(!valid_dma_direction(dir))` → `dma_map_direct()` 判路 → 调实现 → `debug_dma_map_page()` + `trace_dma_map_page()`。

direct 实现的判断顺序（`kernel/dma/direct.h:84-112`）：

```c
	phys_addr_t phys = page_to_phys(page) + offset;      /* :88 */
	dma_addr_t dma_addr = phys_to_dma(dev, phys);        /* :89 */

	if (is_swiotlb_force_bounce(dev)) {                  /* :91 受限池：必 bounce */
		if (is_pci_p2pdma_page(page))
			return DMA_MAPPING_ERROR;                    /* :93 */
		return swiotlb_map(dev, phys, size, dir, attrs); /* :94 */
	}

	if (unlikely(!dma_capable(dev, dma_addr, size, true))) {   /* :97 mask 不够 */
		if (is_pci_p2pdma_page(page))
			return DMA_MAPPING_ERROR;                    /* :99 */
		if (is_swiotlb_active(dev))
			return swiotlb_map(dev, phys, size, dir, attrs);  /* :101 */

		dev_WARN_ONCE(dev, 1,
			     "DMA addr %pad+%zu overflow (mask %llx, bus limit %llx).\n",
			     &dma_addr, size, *dev->dma_mask, dev->bus_dma_limit);  /* :103-105 */
		return DMA_MAPPING_ERROR;                        /* :106 */
	}

	if (!dev_is_dma_coherent(dev) && !(attrs & DMA_ATTR_SKIP_CPU_SYNC))
		arch_sync_dma_for_device(phys, size, dir);       /* :110 */
	return dma_addr;                                     /* :111 */
```

⚠️ **`phys_to_dma()` 在 `:89` 就算好了，在任何检查之前。** 如果 `dma-ranges` 覆盖不到这个物理地址，`phys_to_dma()` 返回 `DMA_MAPPING_ERROR`（见 02 §4），然后 `:97` 的 `dma_capable()` 一定失败——**所以 dts 写错的最终症状是 `:103` 那条 overflow 警告**，报的却是 mask 和 bus limit，不提 `dma-ranges`。这是 06 §5 那张"错误消息 → 原因"表里最容易误诊的一条。

⚠️ **只有 `dev_WARN_ONCE`，即整个设备生命周期只报一次。** 之后的每一次同样失败都静默返回 `DMA_MAPPING_ERROR`。

⚠️ **`swiotlb_map()` 的两个入口条件不一样**：`:94` 是"必须 bounce"（受限池），`:101` 是"够不着才 bounce 且池是活的"。`is_swiotlb_active(dev)`（`kernel/dma/swiotlb.c:959-964`）为假时（池没分配，见 §A1）直接失败。

⚠️ **`arch_sync_dma_for_device()` 在 `:110` 是最后一步**，也就是**先算地址、后刷缓存**。两者之间没有屏障——顺序由编译器和 `dcache_clean_poc` 内部的 `dsb` 保证。

⚠️ **`DMA_ATTR_SKIP_CPU_SYNC` 只跳过缓存维护，不跳过 bounce**（`:91` 和 `:97` 在它之前）。所以带这个 attr 的映射依然可能落进 swiotlb，而且 `swiotlb_map()` 内部会自己拷数据。

`dma_map_sg` 一侧多两道门（`__dma_map_sg_attrs()`，`kernel/dma/mapping.c:182-208`）：

| 行 | 检查 | 失败时 |
|---|---|---|
| `:190` | `WARN_ON_ONCE(!dev->dma_mask)` | 返回 0 |
| `:193-194` | `dma_map_direct()` 或 `arch_dma_map_sg_direct()` | 二选一 |
| `:200-204` | ⚠️ **校验 ops 返回的错误码只能是 `-EINVAL` / `-ENOMEM` / `-EIO` / `-EREMOTEIO`** | 其他值触发 `WARN_ON_ONCE` 并改写成 `-EIO` |

⚠️ **`:200-204` 那道校验是内核里少见的"检查下层是否遵守约定"**。它意味着一个写错返回码的 `map_sg` 实现会被抓出来，但**代价是真实错误码被 `-EIO` 掩盖**。看到 `-EIO` 又伴随 `WARN_ON_ONCE` 时，真实原因在 ops 实现里。

## §A7 `dma_sync_*`（`kernel/dma/mapping.c:328-381`）

四个入口结构完全一致（`:328` / `:342` / `:356` / `:370`），都是 `BUG_ON` → 判路 → 实现 → debug 钩子。**ops 路径上回调缺失就静默跳过**（`else if (ops->sync_...)`），这和 §A4 提到的"缺回调等于放行"是同一个约定。

direct 实现的两个方向**动作顺序相反，而且不对称**：

`dma_direct_sync_single_for_device()`（`kernel/dma/direct.h:55-65`）：
1. `:57` `paddr = dma_to_phys(dev, addr)`
2. `:59-60` 是 swiotlb 缓冲 → `swiotlb_sync_single_for_device()`（把数据拷进 bounce 区）
3. `:62-63` 非一致性 → `arch_sync_dma_for_device()`（clean）

`dma_direct_sync_single_for_cpu()`（`kernel/dma/direct.h:67-82`）：
1. `:69` `paddr = dma_to_phys(dev, addr)`
2. `:71-74` 非一致性 → `arch_sync_dma_for_cpu()` + `arch_sync_dma_for_cpu_all()`
3. `:76-77` 是 swiotlb 缓冲 → `swiotlb_sync_single_for_cpu()`（把数据拷回原地）
4. `:79-80` `dir == DMA_FROM_DEVICE` → `arch_dma_mark_clean()`

⚠️ **两个方向的"缓存维护"和"bounce 拷贝"次序是相反的**（for_device 先拷后刷，for_cpu 先刷后拷）。这不是笔误：for_device 要让"拷进 bounce 区"这个 CPU 写操作也被 clean 出去；for_cpu 要先把设备写进内存的数据从缓存里失效掉，再从 bounce 区拷回。**次序换了就会丢数据。**

⚠️ **`arch_sync_dma_for_cpu_all()` 和 `arch_dma_mark_clean()` 在 arm64 上都不存在**（arm64 不 select `ARCH_HAS_SYNC_DMA_FOR_CPU_ALL`（`kernel/dma/Kconfig:70`）也不 select `ARCH_HAS_DMA_MARK_CLEAN`（`:51`）），它们是空 inline。

⚠️ **arm64 的 `arch_sync_dma_for_cpu()` 对 `DMA_TO_DEVICE` 直接返回**（`arch/arm64/mm/dma-mapping.c:29-30`），而 `arch_sync_dma_for_device()` 对所有方向都 clean（`:21`）。**所以"只读方向"的映射在 arm64 上有一半的缓存操作是免费的**，而"只写方向"的没有。详见 §B3 和 08 §4。

⚠️ **sg 版本的 `dma_direct_sync_sg_*` 是不是空实现取决于配置**（`kernel/dma/direct.h:25-33` 和 `:35-53` 两组 `#if`）：只有 `ARCH_HAS_SYNC_DMA_FOR_*` 或 `SWIOTLB` 至少一个开着才有真实现。arm64 三个全开，所以都是真的。

## §A8 `dma_unmap_page_attrs()`（`kernel/dma/mapping.c:167-179`）

多一道 §A6 没有的门：`:173-174` 的条件是 `dma_map_direct(dev, ops) || arch_dma_unmap_page_direct(dev, addr + size)`。**第二项是给 `ARCH_HAS_DMA_MAP_DIRECT`（`kernel/dma/Kconfig:24`）的架构留的"部分绕过"钩子**，arm64 不 select 它，于是用 `include/linux/dma-map-ops.h:359-360` 那两个恒为 `(false)` 的宏。⚠️ 这两个符号在开启该配置时是**函数声明**（`:352-353`），关闭时是**宏**——`grep` 找它们要同时看两种形态。

direct 实现（`kernel/dma/direct.h:114-125`）只两步：
1. `:119-120` 除非 `DMA_ATTR_SKIP_CPU_SYNC`，调 `dma_direct_sync_single_for_cpu()`
2. `:122-124` 是 swiotlb 缓冲 → `swiotlb_tbl_unmap_single()`，**并且强行加上 `DMA_ATTR_SKIP_CPU_SYNC`**（`:124`）

⚠️ **第 2 步那个强行加的 attr 是为了避免重复同步**：第 1 步已经做过缓存维护和 bounce 回拷了。**如果调用者本来传了 `DMA_ATTR_SKIP_CPU_SYNC`，第 1 步被跳过，第 2 步依然被跳过**——数据不会从 bounce 区回来。这是 `DMA_ATTR_SKIP_CPU_SYNC` 在 unmap 上最危险的用法。

⚠️ **`dma_unmap_*` 没有任何返回值，也没有"这个地址不是我给你的"检查。** 传错地址的唯一发现途径是 `CONFIG_DMA_API_DEBUG` 的 `check_unmap()`（`kernel/dma/debug.c:956`）。

## §A9 `dma_free_attrs()`（`kernel/dma/mapping.c:519-543`）

按顺序：

| 行 | 动作 |
|---|---|
| `:524-525` | `dma_release_from_dev_coherent()` 命中就直接返回（和 §A5 的 `:501` 对称） |
| `:533` | ⚠️ `WARN_ON(irqs_disabled())` |
| `:535-536` | `cpu_addr` 为 NULL 直接返回（**释放 NULL 是合法的**） |
| `:538` | `debug_dma_free_coherent()` |
| `:539-542` | `dma_direct_free()` 或 `ops->free` |

⚠️ **`:533` 那个 `WARN_ON(irqs_disabled())` 有一段 8 行的注释解释理由（`:526-532`）**：非一致性平台的一致性缓冲是靠 non-cacheable remap 实现的，`ops->free()` 可能调 `vunmap()`——在中断上下文里 a) 可能撞 `BUG_ON` 或试图睡眠，b) 说明驱动本来就在误用一致性 API。**这是本子系统里唯一一处"警告的措辞明确指责调用者"的地方**。

⚠️ **`dma_alloc_attrs()` 没有对应的 `irqs_disabled()` 警告**——分配可以在原子上下文做（走原子池，§A5 出口 4），释放不行。**这个不对称是设计的**：`dma_free_from_pool()`（`kernel/dma/pool.c:283-295`）本身能在原子上下文跑，但 `dma_free_attrs()` 无法在不解析地址来源的情况下预知会不会走到 `vunmap`。

## §A10 拆除

| 层 | 动作 | 位置 | 会不会真的发生 |
|---|---|---|---|
| 驱动 | `dma_free_coherent()` / `dma_unmap_*()` | — | 驱动自己负责；漏了就是泄漏 |
| devres | `dmam_release()` 自动调 `dma_free_coherent()` | `kernel/dma/mapping.c:32-38` | 只对 `dmam_*` 分配的 |
| 驱动模型 | `arch_teardown_dma_ops()` → `dev->dma_ops = NULL` | `arch/arm64/mm/dma-mapping.c:58-61` | ⚠️ 只在 `CONFIG_IOMMU_DMA` 下存在 |
| — | `dev->dma_range_map` / mask / `dma_coherent` 的清理 | **没有** | ⚠️ 随 `struct device` 一起消失 |

⚠️ **没有任何代码释放 `dev->dma_range_map` 指向的数组。** 它由 `of_dma_configure_id()` 在 `drivers/of/device.c:198` 赋值，`-EPROBE_DEFER` 路径上在 `:209` 置 NULL、`:210` 把数组 `kfree` 掉，正常路径上一直挂到设备结构体被释放。**设备反复 bind/unbind 不会泄漏**（`dma_range_map` 只在设备创建时设一次，不随 probe 重设），但**设备热插拔的 `struct device` 释放路径里也没有对应的 kfree**——这块内存的生命周期依赖 `struct device` 的分配者。

⚠️ **`dmam_release()` 是唯一的自动清理路径，而且只覆盖 `dmam_alloc_coherent()` / `dmam_alloc_attrs()`。** 流式映射（`dma_map_*`）没有 devres 版本——**没有 `dmam_map_single()`**。含义：unmap 只能靠驱动自己配对，没有安全网。

---

# Part B：横切主题

## §B1 arm64 为什么没有自己的 `dma_map_ops`

`arch/arm64/` 里**一个 `struct dma_map_ops` 实例都没有**（02 §3.2 那 24 个实例里，arm32 有一个 `iommu_ops`（`arch/arm/mm/dma-mapping.c:1526`），arm64 零个）。链条是这样断的：

| 环节 | 事实 | 位置 |
|---|---|---|
| 1 | arm64 没有 `arch/arm64/include/asm/dma-mapping.h` | — |
| 2 | 也没有在 `Kbuild` 里写 `generic-y += dma-mapping.h` | — |
| 3 | 靠 `mandatory-y += dma-mapping.h` 兜底 | `include/asm-generic/Kbuild:20` |
| 4 | 于是用通用版，`get_arch_dma_ops()` 恒返回 NULL | `include/asm-generic/dma-mapping.h:5-8` |
| 5 | `get_dma_ops()` 先看 `dev->dma_ops`，为空才问 arch | `include/linux/dma-map-ops.h:89` |
| 6 | `arch_setup_dma_ops()` 只在有 iommu 时才设 `dev->dma_ops` | `arch/arm64/mm/dma-mapping.c:76-77` |

结论：**普通 arm64 设备的 `get_dma_ops()` 返回 NULL，`dma_map_direct()`（`kernel/dma/mapping.c:137`）返回真，全部走 `kernel/dma/direct.c`。** arm64 需要的架构特异性全部通过 4 个 `arch_*` 钩子（80 行）表达，而不是一整套 ops。

⚠️ **这是"用最小接口表达架构差异"的成功案例。** 对比 arm32：`arch/arm/mm/dma-mapping.c` 有 1500+ 行、自带一整套 ops。arm64 把同样的功能压到 80 行，代价是**必须接受通用 direct 路径的决策结构**（§A5 那棵树）。

⚠️ **`dma_go_direct()`（`kernel/dma/mapping.c:112`）还有第二条走 direct 的路径**：`dev->dma_ops_bypass`（`include/linux/device.h:653`）为真时，即使 `dev->dma_ops` 非空也走 direct。这个位由 `DMA_OPS_BYPASS`（`kernel/dma/Kconfig:20`）门控，**arm64 不 select 它**，所以在 arm64 上恒假。

## §B2 地址翻译：三层，而且只有一层是本子系统的

| 层 | 谁做 | arm64 上 |
|---|---|---|
| 页内偏移 → 物理地址 | `page_to_phys(page) + offset` | 恒等，无配置 |
| 物理地址 → 总线地址 | `phys_to_dma()`（`include/linux/dma-direct.h:70`） | ⚠️ **`dma_range_map` 为空时恒等** |
| 总线地址 → 设备真正看到的地址 | IOMMU（SMMU）或直连 | 走 `iommu_dma_ops` 时由 `drivers/iommu/` 负责 |

⚠️ **绝大多数 arm64 板子上，中间那层是恒等映射。** `dma-ranges` 属性在 arm64 dts 里只有 52 个文件用到（06 §1 有统计），其余全部是"物理地址 == 总线地址"。**含义：一个只在这类板子上测过的驱动，如果混用了 `virt_to_phys()` 和 `dma_map_*()` 的结果，不会暴露问题**——换到带 `dma-ranges` 的板子上立刻坏。

⚠️ **`dma_capable()`（`include/linux/dma-direct.h:99-111`）的判据是 `min_not_zero(*dev->dma_mask, dev->bus_dma_limit)`（`:110`）**，即两个限制取更严的那个，**但 0 被当作"没设"而不是"限制为 0"**。所以 `bus_dma_limit = 0` 表示无限制，不表示不能 DMA。

⚠️ **`dma_capable()` 的第四个参数 `is_ram` 改变判据**：为真时额外要求 `dma_addr + size - 1 <= limit` 之外的一致性检查。`dma_direct_map_page()` 在 `:97` 传 `true`，`dma_map_resource()`（`kernel/dma/mapping.c:295`）走的是另一条路（MMIO 不是 RAM）。

## §B3 缓存维护：arm64 的三个钩子和它们的不对称

| 钩子 | arm64 实现 | 底层操作 | 对 `dir` 敏感吗 |
|---|---|---|---|
| `arch_sync_dma_for_device` | `arch/arm64/mm/dma-mapping.c:16-22` | `dcache_clean_poc` | ⚠️ **不敏感**，所有方向都 clean |
| `arch_sync_dma_for_cpu` | `arch/arm64/mm/dma-mapping.c:24-33` | `dcache_inval_poc` | ⚠️ **敏感**，`DMA_TO_DEVICE` 直接 return（`:29-30`） |
| `arch_dma_prep_coherent` | `arch/arm64/mm/dma-mapping.c:35-55` | `dcache_clean_inval_poc` | 不接受 `dir` 参数 |

⚠️ **`arch_dma_prep_coherent()` 用的是 clean+invalidate，而注释（`:39-53`）明确说"架构上只需要 clean"。** 理由写得很直白：有厂商（注释点名 Qualcomm）拿 DMA API 当"把缓冲区交给安全世界"用，缓冲区交出去之后如果出现非安全访问就重启系统。clean+invalidate 让这个问题"不那么容易发生"，但注释 `:49-52` 承认**真正的修法是修驱动**，因为 CPU 仍可能通过线性映射做投机读。注释里带一条 lore 链接（`:46`）。

⚠️ **这段注释是本子系统里唯一一处明确写着"这是权宜之计、将来要放宽回 clean"的地方**（`:51-52` 的 "Once the drivers are fixed, we can relax this to a clean operation"）。它是 §C1 那张设计债表里最实的一条。

⚠️ **`arch_sync_dma_for_device()` 对 `DMA_FROM_DEVICE` 也做 clean 看起来多余，但不是。** 只读方向的缓冲区如果缓存里有脏行，设备写完之后 CPU 可能把脏行写回、覆盖设备的数据。clean 掉才安全。**反过来 `arch_sync_dma_for_cpu()` 对 `DMA_TO_DEVICE` 跳过 invalidate 是真的没必要**——设备没写内存，缓存不可能过期。

## §B4 bounce 决策：三个入口，两套语义

| 触发点 | 条件 | 位置 | 语义 |
|---|---|---|---|
| 1 | `is_swiotlb_force_bounce(dev)` | `kernel/dma/direct.h:91` | **无条件 bounce**（受限池） |
| 2 | `!dma_capable()` && `is_swiotlb_active(dev)` | `kernel/dma/direct.h:97-101` | **够不着才 bounce** |
| 3 | `is_swiotlb_for_alloc(dev)` | `kernel/dma/direct.c:126` | **分配都从池里出**（受限池） |

⚠️ **`is_swiotlb_buffer(dev, paddr)`（`include/linux/swiotlb.h:108-113`）是解映射和同步侧的判据**，它靠"这个物理地址落在 `dev->dma_io_tlb_mem` 的 `[start, end)` 里"来认。**含义：一个设备只能关联一份 swiotlb 池**——受限池设备用不了全局池，反之亦然。

⚠️ **触发点 2 的 `is_swiotlb_active(dev)` 为假时映射直接失败**（`kernel/dma/direct.h:106`）。§A1 已说明池可能没分配。所以**"mask 不够 + 没有 swiotlb"= 硬失败**，没有第三条出路。

⚠️ **bounce 一旦发生，`sg_dma_address()` 指向的就不是原缓冲区了。** 所有后续的 CPU 访问必须走 `dma_sync_*`，否则读到的是设备还没写、或者已经过期的数据。**这是 `dma_sync_*` 存在的唯一理由在流式映射上的体现**——不是"为了缓存",而是"为了缓存 **或** bounce"。

## §B5 debug 层的插入点

`kernel/dma/debug.h`（130 行）给每个入口定义了一对真实现/空 inline。**每个 `dma_*` 入口都在自己的末尾调一次 debug 钩子**，位置固定在"实现返回之后"：

| 入口 | debug 钩子 | 行 |
|---|---|---|
| `dma_map_page_attrs` | `debug_dma_map_page` | `kernel/dma/mapping.c:161` |
| `dma_unmap_page_attrs` | `debug_dma_unmap_page` | `kernel/dma/mapping.c:178` |
| `__dma_map_sg_attrs` | `debug_dma_map_sg` | `kernel/dma/mapping.c:201` |
| `dma_unmap_sg_attrs` | `debug_dma_unmap_sg` | `kernel/dma/mapping.c:286` |
| 四个 `dma_sync_*` | `debug_dma_sync_*` | `:338` / `:352` / `:366` / `:380` |
| `dma_alloc_attrs` | `debug_dma_alloc_coherent` | `kernel/dma/mapping.c:514` |
| `dma_free_attrs` | `debug_dma_free_coherent` | `kernel/dma/mapping.c:538` |

⚠️ **钩子在实现之后调，所以映射失败的调用不会被记账**（`debug_dma_map_page` 内部会看 `dma_addr` 是不是 `DMA_MAPPING_ERROR`）。**含义：`/sys/kernel/debug/dma-api/` 的计数只反映成功的映射**。

⚠️ **`debug_dma_map_single()`（`kernel/dma/debug.c:1206`）和 `debug_dma_mapping_error()`（`:1280`）是本文件里唯一两个导出符号，而且是 bare `EXPORT_SYMBOL`。** 它们不在 `kernel/dma/mapping.c` 里被调——`debug_dma_map_single()` 由 `include/linux/dma-mapping.h:328` 的 `dma_map_single_attrs()` 调（因为只有那一层知道 `ptr` 是内核虚拟地址），`debug_dma_mapping_error()` 由 `:95-102` 的 `dma_mapping_error()` 调。**这是 debug 钩子唯一两个不在 mapping.c 里的插入点。**

---

# Part C：对这套实现的评价

## §C1 源码自陈的设计债

全树 grep `kernel/dma/*.c`、`kernel/dma/*.h` 和四个头文件的 `FIXME` / `TODO` / `XXX` / `REVISIT` / `HACK`，**只命中一条**：

| 位置 | 原文 | 含义 |
|---|---|---|
| `kernel/dma/direct.c:225-226` | "Fallback to the arch handler if it exists. This should eventually go away." | `arch_dma_alloc()` 这条后备路径是历史遗留，计划移除。arm64 走不到（§A5 出口 2） |

**一条，6182 行。** 这个数字本身是个评价：这个子系统在 6.1 里已经相当稳定。

但"没有 FIXME"不等于没有债。用另一组判据（注释里明确写了"权宜/将来要改/看起来能缓解"的地方）能找到两条更实的：

| 位置 | 内容 | 为什么是债 |
|---|---|---|
| `arch/arm64/mm/dma-mapping.c:39-53` | `arch_dma_prep_coherent()` 用 clean+invalidate 而非架构要求的 clean，注释承认"appears to make this issue less likely"、"drivers themselves still need fixing"、"Once the drivers are fixed, we can relax this" | ⚠️ **为了兜住误用 API 的厂商驱动，给所有 arm64 设备加了额外的缓存操作**。这是全平台的性能税 |
| `include/linux/dma-mapping.h` 位 3 空缺 | `DMA_ATTR_*` 从 `1UL<<1` 到 `1UL<<9`，**位 3 是洞**（历史上的 `DMA_ATTR_NON_CONSISTENT`），`struct dma_map_ops` 的 `cache_sync` 回调（`include/linux/dma-map-ops.h:77`）也还留着 | 删了 attr 但没删对应的 ops 回调，也没有回收位号。**新读者会以为 `cache_sync` 是活的** |

## §C2 三处让人意外的取舍

⚠️ **一、`dma_map_sg` 和 `dma_map_sgtable` 的返回约定相反，而且都保留着。** 前者返 0 表示失败，后者返 0 表示成功，ops 回调返负错误码——三套并存（01 §2 有表）。这不是没人发现，而是**改前者会破坏几百个驱动**。代价是每个新读者都要踩一次。

⚠️ **二、`kernel/dma/debug.c` 有 1608 行，占整个子系统 26%，而它默认不编译。** `CONFIG_DMA_API_DEBUG`（`kernel/dma/Kconfig:206`）默认关。这个比例说明**这套 API 的误用足够普遍、足够难查，值得为它写四分之一的代码**。反过来说，**它默认关着也意味着大部分产品内核里没有任何 DMA API 误用检测**。

⚠️ **三、没有一把锁保护 `struct device` 的 13 个 DMA 字段（02 §9.2）。** 正确性完全靠"probe 之前设好、之后不改"这个不成文约定。`dma_set_mask()` 没有任何"设备已经在 DMA 了吗"的检查——**运行期调它是静默的未定义行为**。这个取舍换来的是热路径上零锁开销。

## §C3 这套设计好在哪

⚠️ **一、`arch_*` 钩子的粒度选得很准。** arm64 用 4 个钩子 80 行覆盖了 arm32 需要 1500 行 ops 才能做的事（§B1）。判据是"钩子表达的是**架构真正独有**的东西（缓存维护指令、cache line 大小），而不是策略"。

⚠️ **二、`dma_dummy_ops`（`kernel/dma/dummy.c`，38 行全部返回失败）。** 用"一套永远失败的实现"代替在每个 API 入口加 `if (dev_is_broken)`。arm64 上用不到（唯一安装点在 `drivers/acpi/scan.c:1625`），但模式值得记。

⚠️ **三、`dma_map_sgtable()` 把 nents 责任收进 API。** 老 API 要求调用者自己记住 `nents` 和 `orig_nents` 并在正确的地方用正确的那个（02 §5.2）；新 API 把两个都放进 `struct sg_table`，`dma_unmap_sgtable()` 自己取 `orig_nents`。**这是"把易错的约定变成类型"的教科书做法**，而且做到了向后兼容（老 API 一行没改）。

---

## §C4 下一篇读什么

| 想知道 | 去 |
|---|---|
| swiotlb / CMA / 原子池 / 受限池 / debug 各自的完整机制 | 04 |
| 驱动侧的代码骨架 | 05 |
| dts 属性怎么写、哪条路径生效 | 06 |
| debugfs / tracepoint / 错误消息对照 | 07 |
| 为什么要分一致性和流式、为什么 arm64 的同步不对称 | 08 §2 / 08 §4 |
