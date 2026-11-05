---
title: 扩展机制
---

# dma-mapping 的可选子框架

**本篇的组织轴：一节一个独立子框架，彼此不互相调用。** 每一节都会交代它挂在 03 时间线的哪一点上。

⚠️ 这一点在本子系统里尤其重要：`kernel/dma/` 那 11 个 `.c` 文件里，**只有 3 个（mapping.c / direct.c / dummy.c）是无条件编译的**，其余 8 个各自由一个 Kconfig 门控，而且**互不引用**。所以"读懂 dma-mapping"实际是"读懂 mapping.c + direct.c，再按板子配置挑几个池"。

---

## §0 分诊表：arm64 上哪几个真会碰到

| 子框架 | Kconfig | arm64 是否启用 | 什么时候真的触发 | 本篇 |
|---|---|---|---|---|
| swiotlb bounce 池 | `SWIOTLB`（`kernel/dma/Kconfig:79`） | ⚠️ **无条件 select**（`arch/arm64/Kconfig:228`） | 只有 `max_pfn > arm64_dma_phys_limit` 时才分配池（03 §A1）；池存在且 mask 不够时才 bounce | §1 |
| 受限 DMA 池 | `DMA_RESTRICTED_POOL`（`:83`） | 需要 `OF && OF_RESERVED_MEM && SWIOTLB`（`:85`），默认 n | dts 有 `restricted-dma-pool` 节点**且设备无 IOMMU** | §2 |
| CMA | `DMA_CMA`（`:118`） | 需要 `HAVE_DMA_CONTIGUOUS && CMA`（`:120`）；arm64 提供前者（`arch/arm64/Kconfig:182`） | 大块连续内存分配 | §3 |
| per-NUMA CMA | `DMA_PERNUMA_CMA`（`:134`） | ⚠️ **`default NUMA && ARM64`**——arm64 NUMA 机器上默认开 | `cma_pernuma=` 参数给了大小 | §3.3 |
| 原子池 | `DMA_COHERENT_POOL`（`:105`） | ⚠️ 被 `DMA_DIRECT_REMAP` 间接 select（`:113-116`），而 arm64 select 后者（`arch/arm64/Kconfig:120`）→ **恒开** | 非一致性设备在原子上下文分配一致性内存 | §4 |
| per-device 一致性池 | `DMA_DECLARE_COHERENT`（`:54`） | 由 `DMA_GLOBAL_POOL` select（`:109-111`）或架构直接 select | dts 有 `shared-dma-pool`（非 `reusable`）+ `memory-region` | §5 |
| 全局一致性池 | `DMA_GLOBAL_POOL`（`:109`） | ⚠️ **arm64 不 select**，只有 hexagon 用 | — | §5.3 |
| DMA-API debug | `DMA_API_DEBUG`（`:206`） | 默认 n，需手工开 | 开了就一直在记账 | §6 |
| 映射性能基准 | `DMA_MAP_BENCHMARK`（`:237`） | 默认 n | 手工 bind 驱动 + 跑 ioctl | §7 |
| `dma_dummy_ops` | 无（无条件编译） | ⚠️ 编译进来但**唯一安装点在 ACPI**，arm64 用不到 | — | §8 |
| remap / ops_helpers 辅助 | 无（`remap.c` 由 `DMA_DIRECT_REMAP` 门控，`ops_helpers.c` 由 `DMA_OPS`） | remap 恒开；ops_helpers 只在有 ops 时 | 被上面几个调用 | §9 |

⚠️ **一台典型 arm64 板子上实际活着的是：swiotlb（可能池为空）+ CMA + 原子池 + remap.c。** 受限池、全局池、debug、benchmark 四个默认全关。

---

## §1 swiotlb：bounce buffer 池

**挂载点：** 03 §A1 第 4 步分配池（`arch/arm64/mm/init.c:454`）；运行期在 03 §B4 那三个触发点被调。

### §1.1 三层分配单元

02 §6.2 已给结构。这里只说分配逻辑：

| 层 | 数量 | 作用 |
|---|---|---|
| `io_tlb_mem` | 全局 1 份（`include/linux/swiotlb.h:106`）+ 每个受限池 1 份 | 池的元信息 |
| `io_tlb_area` | `nareas` 个（`kernel/dma/swiotlb.c:128-141` 决定个数） | ⚠️ **只为了拆锁**，不为了分区隔离 |
| `io_tlb_slot` | `nslabs` 个 | 真正的分配单元，每个 `1 << IO_TLB_SHIFT` 字节 |

分配路径：`swiotlb_find_slots()`（`:744-761`）遍历 area，对每个 area 调 `swiotlb_do_find_slots()`（`:647-742`，**96 行，是本文件最长的函数**）。后者从 `area->index` 开始环形扫描（`wrap_area_index()` `:636`），找连续足够的空闲 slot。

⚠️ **`swiotlb_do_find_slots()` 要同时满足三个约束**：连续 slot 数够、`dma_capable()` 通得过、对齐符合 `dma_get_min_align_mask(dev)`（`include/linux/dma-mapping.h:531`）。**第三个约束是驱动能影响 swiotlb 分配的唯一手段**——设了 `min_align_mask` 之后 bounce 缓冲的低位地址会被保留，NVMe 之类靠低位编码信息的设备需要它。

### §1.2 bounce 的数据搬运

`swiotlb_bounce()`（`kernel/dma/swiotlb.c:551-619`，69 行）靠 `slot->orig_addr` 找回原地址。两个方向由 `dir` 决定。

⚠️ **`:592` 有一个 `PageHighMem()` 分支**：原缓冲在 highmem 时不能直接 `memcpy`，要逐页 `kmap_atomic`。arm64 64 位内核没有 highmem，这个分支进不去。

⚠️ **`slot->alloc_size` 可能小于实际占用的 slot 总字节数。** bounce 只搬 `alloc_size` 个字节——**slot 里剩下的部分是上一次使用留下的数据**。这是 swiotlb 的信息泄漏面，也是 `restricted-dma-pool` 存在的部分理由。

### §1.3 全局普查：谁在用 swiotlb

`swiotlb_*` 符号在 `kernel/dma/` 之外的 **40 个文件**里出现，其中 arm64 相关只有两个：

```bash
grep -rl "swiotlb_" --include=*.c . | grep -v "^./kernel/dma/" | wc -l    # → 40
grep -rl "swiotlb_" --include=*.c . | grep -v "^./kernel/dma/" | grep -E "arm64|iommu|xen"
```

| 文件 | 用途 |
|---|---|
| `arch/arm64/mm/init.c:454` | 启动期分配池 |
| `drivers/iommu/dma-iommu.c` | ⚠️ **IOMMU 路径也用 swiotlb** |
| `drivers/xen/swiotlb-xen.c` | Xen 前端（不在本篇范围） |

⚠️ **"有 SMMU 就不会 bounce"是错的。** `drivers/iommu/dma-iommu.c` 自己会在需要时调 swiotlb——典型场景是缓冲区不满足 IOMMU 的粒度要求。剩下 37 个文件全部是 x86/powerpc/mips/s390/ia64/riscv/loongarch，arm64 无关。

---

## §2 受限 DMA 池（`restricted-dma-pool`）

**挂载点：** 03 §A2 第 10 步，`of_dma_configure_id()` 的最后一行（`drivers/of/device.c:220`），**只在设备没有 IOMMU 时**。

### §2.1 它和普通 swiotlb 的区别

同一套 `io_tlb_mem` 结构，三个字段不同（`rmem_swiotlb_device_init()`，`kernel/dma/swiotlb.c:1030-1082`）：

| 字段 | 全局池 | 受限池 | 行 |
|---|---|---|---|
| `force_bounce` | 看 `swiotlb=force` 参数 | ⚠️ **恒真**（传 `SWIOTLB_FORCE`） | `:1070` → `:301` |
| `for_alloc` | 假 | ⚠️ **真**：`dma_alloc_*` 也从池里出 | `:1072` |
| `dev->dma_io_tlb_mem` | 指向 `io_tlb_default_mem` | 指向这个池 | — |

⚠️ **`for_alloc = true` 意味着这个设备的 `dma_alloc_coherent()` 完全绕过 CMA / 原子池 / buddy**（03 §A5 的第 1 优先级，`kernel/dma/direct.c:126-127`）。**受限池设备拿不到池外的任何 DMA 内存**——这就是"受限"的含义：**设备只能看见这一块内存**，DMA 访问越界会被这层软件挡住（不是硬件挡）。

⚠️ **`:1039` 拒绝 highmem 页**（`PageHighMem(pfn_to_page(PHYS_PFN(rmem->base)))`），返回 `-EINVAL`。

### §2.2 dts 侧的四道排除

`rmem_swiotlb_setup()`（`kernel/dma/swiotlb.c:1095-1109`）先排除四个属性，命中任一就返 `-EINVAL`：

```c
	if (of_get_flat_dt_prop(node, "reusable", NULL) ||          /* :1099 */
	    of_get_flat_dt_prop(node, "linux,cma-default", NULL) ||  /* :1100 */
	    of_get_flat_dt_prop(node, "linux,dma-default", NULL) ||  /* :1101 */
	    of_get_flat_dt_prop(node, "no-map", NULL))               /* :1102 */
		return -EINVAL;
```

成功时打印 `"Reserved memory: created restricted DMA pool at %pa, size %ld MiB\n"`（`:1106`），注册点在 `:1111`。

⚠️ **这四道排除是三个 `RESERVEDMEM_OF_DECLARE` 之间划分地盘的方式**，不是校验。`restricted-dma-pool` 用的是独立 compatible，理论上不会撞上 `shared-dma-pool`，但这四道门保证了写错属性组合时不会静默变成另一种池。详见 06 §4。

### §2.3 设备侧的挑选逻辑

`of_dma_set_restricted_buffer()`（`drivers/of/device.c:56-97`）：

| 行 | 动作 |
|---|---|
| `:62-63` | 没开 `DMA_RESTRICTED_POOL` 直接返回 |
| `:65-66` | 数 `dev->of_node` 的 `memory-region` 个数 |
| `:71-75` | ⚠️ 为 0 时**换成 `np`（有 DMA 配置的那个节点）再数一次** |
| `:77-89` | 遍历，找第一个 compatible 为 `restricted-dma-pool` 且 available 的 |
| `:95-96` | 找到就 `of_reserved_mem_device_init_by_idx()`，失败打印 `"failed to initialise \"restricted-dma-pool\" memory node\n"` |

⚠️ **注释 `:80-81` 明确说"只允许一个 restricted-dma-pool 区域"**，代码在 `:86` 遇到第一个就 `break`。多写几个不会报错，第二个及以后被静默忽略。

⚠️ **`:95` 的条件 `i < count` 依赖 `count` 可能是负错误码**（注释 `:93` 专门提了）。`count` 为负时循环一次都不进，`i == 0 > count` 为假，跳过——所以负值是安全的，但这个安全性来自巧合而非显式检查。

### §2.4 全树使用统计

| 口径 | 数字 | 重跑命令 |
|---|---|---|
| arm64 dts 里的 `restricted-dma-pool` 节点 | **1**（`arch/arm64/boot/dts/mediatek/mt8192-asurada.dtsi:115`） | `grep -rl "restricted-dma-pool" arch/arm64/boot/dts/ \| wc -l` |
| 内核里消费它的代码点 | 2（`kernel/dma/swiotlb.c:1111` 注册、`drivers/of/device.c:83` 匹配） | `grep -rn "restricted-dma-pool" --include=*.c .` |

⚠️ **全树只有一个板子在用。** 这个框架在 arm64 上基本是给 Chromebook 那一条线准备的，通用板子碰不到。

---

## §3 CMA（连续内存分配器）

**挂载点：** 03 §A1 第 3 步预留（`arch/arm64/mm/init.c:435`）；运行期在 `__dma_direct_alloc_pages()` 的第 2 优先级（`kernel/dma/direct.c:131`）。

### §3.1 三个区，优先级从高到低

| 区 | 变量 | 谁设 |
|---|---|---|
| per-device | `dev->cma_area`（`include/linux/device.h:610`） | dts 的 `memory-region` → `shared-dma-pool` + `reusable` |
| per-NUMA | `dma_contiguous_pernuma_area[nid]`（`kernel/dma/contiguous.c:100`） | `cma_pernuma=` 参数 |
| 全局默认 | `dma_contiguous_default_area`（`kernel/dma/contiguous.c:60`） | `cma=` 参数或编译期默认，或 dts 的 `linux,cma-default` |

`dma_alloc_contiguous()`（`kernel/dma/contiguous.c:303-333`）按这个顺序试，`:329-332` 是最后的全局默认。

### §3.2 全局区大小的三个来源，先到者赢

`dma_contiguous_reserve()`（`kernel/dma/contiguous.c:166-202`）：

| 优先 | 来源 | 行 |
|---|---|---|
| 1 | 命令行 `cma=`（`early_param` 在 `:96`，解析器 `early_cma` 在 `:77`） | `:176` |
| 2 | `CMA_SIZE_MBYTES`（`kernel/dma/Kconfig:146`）经 `size_bytes` 宏（`:72`） | `:183`-`:189` |
| 3 | `CMA_SIZE_PERCENTAGE`（`kernel/dma/Kconfig:157`） | 同上 |

⚠️ **`:193` 的条件是 `if (selected_size && !dma_contiguous_default_area)`**，即"dts 已经建了默认区就不再建"。**但命令行和 dts 同时给出时，赢的是命令行**——仲裁不在这里，在 `rmem_cma_setup()` 的开头：`:406-410` 发现 `size_cmdline != -1 && default_cma` 时打印 `"Reserved memory: bypass %s node, using cmdline CMA params instead\n"`（`:407`）并返回 `-EBUSY`，**dts 节点主动让位**，于是 `dma_contiguous_default_area` 保持 NULL，`:193` 的条件成立。完整场景表见 06 §5.2.1。

⚠️ 命令行 `cma=0` 是显式关掉默认区的方式（`kernel/dma/Kconfig:126-127` 的帮助文本提到）。

### §3.3 per-NUMA CMA

⚠️ **`DMA_PERNUMA_CMA` 在 arm64 上 `default NUMA && ARM64`（`kernel/dma/Kconfig:136`），即 NUMA 机器默认开。** 帮助文本（`:138-142`）说明动机是"让 ARM64 SMMU 之类的设备通过 DMA 一致性 API 拿到本地内存"。**但只有给了 `cma_pernuma=` 参数才真的预留**（`early_param` 在 `:108`）。所以"配置开着"不等于"区存在"。

### §3.4 全树使用统计

| 口径 | 数字 | 重跑命令 |
|---|---|---|
| arm64 dts 里带 `reusable` 的节点文件 | **9** | `grep -rl "reusable" arch/arm64/boot/dts/ \| wc -l` |
| arm64 dts 里 `linux,cma-default` 的文件 | **9** | `grep -rl "linux,cma-default" arch/arm64/boot/dts/ \| wc -l` |
| 调 `of_reserved_mem_device_init()` 的驱动文件 | **31** | `grep -rl "of_reserved_mem_device_init" --include=*.c . \| wc -l` |

⚠️ **那 31 个文件不都是 CMA 用户**——`of_reserved_mem_device_init()` 同时服务 `shared-dma-pool` 的两种形态（有 `reusable` = CMA、无 `reusable` = per-device 一致性池）。**光看这个函数的调用者分不出来是哪种**，要看对应的 dts 节点。

---

## §4 原子池（`DMA_COHERENT_POOL`）

**挂载点：** 03 §A5 的出口 4（`kernel/dma/direct.c:250-252`）和出口 5（`:264-265`）——**只在"不能睡"时**。

### §4.1 为什么需要它

非一致性设备的 `dma_alloc_coherent()` 要建一份 uncached 的 vmap 映射（03 §A5 的 `:292`），而 `vmap` 会睡。所以 `GFP_ATOMIC` 的请求走不了正常路径，只能从**预先建好 uncached 映射的池**里切。

### §4.2 三个池、三档寻址

`dma_guess_pool()`（`kernel/dma/pool.c:224-238`）的选择顺序：

```
第一次（prev == NULL）:
   ZONE_DMA32 存在 && gfp & GFP_DMA32  → atomic_pool_dma32
   atomic_pool_dma 存在 && gfp & GFP_DMA → atomic_pool_dma
   否则                                 → atomic_pool_kernel
后续（回退链）:
   atomic_pool_kernel → atomic_pool_dma32（没有则 atomic_pool_dma）
   atomic_pool_dma32  → atomic_pool_dma
   atomic_pool_dma    → NULL（结束）
```

`dma_alloc_from_pool()`（`:265-281`）用 `while` 沿这条链一路试，全失败时 `:279` 打印 `"Failed to get suitable pool for %s\n"`（**是 `WARN(1, ...)`，带栈回溯**）。

⚠️ **回退链方向是"宽 → 窄"**：先试寻址能力要求低的（kernel），失败了才去要求高的（DMA32、DMA）。这和直觉相反——因为窄池更稀缺，留给真的需要它的设备。

### §4.3 后台扩容

`__dma_alloc_from_pool()` 在 `:257-258` 发现 `gen_pool_avail(pool) < atomic_pool_size` 时 `schedule_work(&atomic_pool_work)`。work 函数走 `atomic_pool_resize()`（`kernel/dma/pool.c:145`）对三个池各判一次（`:154` / `:157` / `:159`），需要时调 `atomic_pool_expand()`（`:79-143`）。

`atomic_pool_expand()` 的五步：

| 行 | 动作 |
|---|---|
| `:88` | `order = min(get_order(pool_size), MAX_ORDER-1)` ⚠️ **单次扩容上限是 `MAX_ORDER-1`** |
| `:101` | `arch_dma_prep_coherent(page, pool_size)` 清缓存别名 |
| `:104` | `dma_common_contiguous_remap()` 建 uncached 映射 |
| `:116` | `set_memory_decrypted()`（arm64 空操作） |
| `:120` | `gen_pool_add_virt()` 把新块加进池 |
| `:125` | `dma_atomic_pool_size_add()` 更新 debugfs 里的计数 |

⚠️ **扩容是异步的，所以"池快空了"到"池变大了"之间有窗口。** 这段时间内的原子分配会沿回退链找别的池，全失败就 `WARN`。**短时间内密集的 `GFP_ATOMIC` 一致性分配会失败**，这是设计取舍不是 bug。

### §4.4 初始默认大小

`dma_atomic_pool_init()`（`kernel/dma/pool.c:187-221`，`postcore_initcall` 在 `:222`）：注释 `:193` 说明默认按"每 1GB 内存 128KB，最小 128KB，最大 `MAX_ORDER-1`"，`:197` 用 `min_t(unsigned long, pages, MAX_ORDER_NR_PAGES)` 卡上限。命令行 `coherent_pool=`（`early_param` 解析器 `early_coherent_pool` 在 `:29`；文档 `Documentation/admin-guide/kernel-parameters.txt:683`）覆盖它。

---

## §5 per-device / 全局一致性池（`DMA_DECLARE_COHERENT` / `DMA_GLOBAL_POOL`）

**挂载点：** ⚠️ **比 03 §A5 那整棵决策树都优先**——`dma_alloc_attrs()` 的第一句就是 `kernel/dma/mapping.c:501` 的 `dma_alloc_from_dev_coherent()`。释放侧对称，在 `:524`。

### §5.1 per-device 池

结构见 02 §6.1。三条建立路径：

| 路径 | 入口 | 谁用 |
|---|---|---|
| dts `shared-dma-pool` 且无 `reusable` | `rmem_dma_setup()`（`kernel/dma/coherent.c:363-389`）+ `rmem_dma_device_init()`（`:336-349`） | 通用 |
| 代码显式声明 | `dma_declare_coherent_memory()`（`kernel/dma/coherent.c:117-131`） | ⚠️ 见下 |
| 全局池（`DMA_GLOBAL_POOL`） | `dma_init_global_coherent()`（`:311-321`） | 只有 hexagon |

⚠️ **`dma_declare_coherent_memory()` 全树只有 8 个文件调，其中 7 个是 `arch/sh/`，第 8 个是 `drivers/remoteproc/remoteproc_virtio.c:410`。**

```bash
grep -rl "dma_declare_coherent_memory" --include=*.c .   # → 8 个文件
```

**arm64 上唯一可能碰到的是 remoteproc 那一处**——它给 virtio 设备划一块和远端固件共享的内存。这解释了为什么这个看起来很边缘的框架不能删。

### §5.2 dts 侧的三道判断

`rmem_dma_setup()`（`kernel/dma/coherent.c:363-389`）：

| 行 | 判断 | arm64 上是否生效 |
|---|---|---|
| `:367` | 有 `reusable` → 返 `-EINVAL` | ⚠️ **生效**：让给 CMA（`rmem_cma_setup()`） |
| `:370-375` | 没有 `no-map` → `pr_err("Reserved memory: regions without no-map are not yet supported\n")`（`:372`）并返 `-EINVAL` | ⚠️ **不生效**：整段在 `#ifdef CONFIG_ARM` 里，arm64 编译不进去 |
| `:377-383` | 有 `linux,dma-default` → 记成全局默认（`WARN` 在 `:379` 防重复） | ⚠️ **不生效**：整段在 `#ifdef CONFIG_DMA_GLOBAL_POOL` 里 |
| `:386` | 成功，打印 `"Reserved memory: created DMA memory pool at %pa, size %ld MiB\n"` | 生效 |

⚠️ **所以 arm64 上 `rmem_dma_setup()` 只有一道门：不能有 `reusable`。** 一个只写了 `shared-dma-pool`、既无 `reusable` 也无 `no-map` 的节点在 arm64 上**会正常建成 per-device 一致性池**——`no-map` 是 32 位 ARM 的要求，不是 arm64 的。判据：

```bash
sed -n '370,375p' kernel/dma/coherent.c        # 被 #ifdef CONFIG_ARM 包着
grep -rn "DMA_GLOBAL_POOL" arch/arm64/Kconfig  # 无输出
```

⚠️ **arm64 上真正会让节点变成死字的组合是 `reusable` 和 `no-map` 同时写**：`rmem_cma_setup()` 在 `kernel/dma/contiguous.c:413` 拒绝带 `no-map` 的区，`rmem_dma_setup()` 在 `:367` 拒绝带 `reusable` 的区，两边都不收。完整决策树见 06 §4.3。

### §5.3 全局池：arm64 用不到，但它揭示一个 Kconfig 关系

`DMA_GLOBAL_POOL`（`kernel/dma/Kconfig:109-111`）`select DMA_DECLARE_COHERENT`。**arm64 不 select 它**，全树唯一的使用者是 `arch/hexagon/kernel/dma.c:41`。

⚠️ **`DMA_GLOBAL_POOL` 和 `DMA_DIRECT_REMAP` 在 Kconfig 层面并不互斥**（`kernel/dma/Kconfig:109-116` 两段没有 `depends on !` 关系），但**在代码里前者胜**：`dma_direct_alloc()` 的 `:239-242` 那个 `if (IS_ENABLED(CONFIG_DMA_GLOBAL_POOL))` 出现在 `:248` 的 `remap = IS_ENABLED(CONFIG_DMA_DIRECT_REMAP)` 之前，命中就直接返回。注释 `:236-238` 写明"有全局池时，非一致性设备一律从它分配"。**所以两个都开时 `DMA_DIRECT_REMAP` 对非一致性设备失效**——这不是配置错误，是显式的优先级。

`dma_init_global_coherent()`（`:311-321`）成功时打印 `"DMA: default coherent area is set\n"`（`:319`），`dma_coherent_default_memory` 是 `__ro_after_init`（`:280`）。三个全局池入口（`:282` / `:292` / `:301`）全部以 `if (!dma_coherent_default_memory) return` 开头——**池不存在时静默返回"没命中"，不是错误**。

---

## §6 DMA-API debug（`DMA_API_DEBUG`）

**挂载点：** 03 §B5 那 11 个插入点，全部在实现返回之后。

### §6.1 它检查什么

结构见 02 §7。核心检查函数：

| 函数 | 行 | 抓什么 |
|---|---|---|
| `check_unmap()` | `kernel/dma/debug.c:956` | ⚠️ unmap 的地址/大小/方向/nents 和 map 时不一致；从没 map 过就 unmap |
| `check_for_stack()` | `:1056` | ⚠️ **拿栈上的缓冲区做 DMA** |
| `check_sync()` | `:1091` | sync 的范围超出映射范围；方向不对 |
| `check_sg_segment()` | `:1166` | sg 段超过 `max_segment_size` 或跨越 `segment_boundary_mask` |

⚠️ **`check_for_stack()` 抓的是最常见也最难查的一类错误。** 栈内存做 DMA 在一致性平台上"能工作"，在 arm64 非一致性设备上会因为缓存行共享而随机损坏相邻的栈变量——症状是不可复现的崩溃。`Documentation/core-api/dma-api-howto.rst` 专门警告过这一点。

### §6.2 两个门 + 一个一次性开关

| 门 | 位置 | 作用 |
|---|---|---|
| `CONFIG_DMA_API_DEBUG` | `kernel/dma/Kconfig:206` | 编译期 |
| `dma_debug=off` | `Documentation/admin-guide/kernel-parameters.txt:1087` | 启动期关掉 |
| `global_disable` | `kernel/dma/debug.c:99` | ⚠️ **运行期一次性熔断** |

⚠️ **`global_disable` 一旦置真就永久关闭**（`dma_debug_disabled()` `:104-107`）。第一条错误报告之后的所有检查都跳过。**含义：日志里只有一条 DMA API 警告，不代表只有一处误用。**

⚠️ **`dma_debug_initialized`（`:102`）是第二个门**，在 `dma_debug_init()`（`core_initcall`，`:929`）之前置真之前，所有钩子都是空操作。**启动早期的 DMA 映射不被记账**。

### §6.3 三个可调项

| 参数 | 文档行 | 默认 |
|---|---|---|
| `dma_debug=off` | `Documentation/admin-guide/kernel-parameters.txt:1087` | 开 |
| `dma_debug_entries=` | `:1090` | `PREALLOC_DMA_DEBUG_ENTRIES` = `1 << 16`（`kernel/dma/debug.c:33`） |
| `dma_debug_driver=` | `:1097` | 无（只过滤指定驱动） |

命令行解析器在 `kernel/dma/debug.c:931` 和 `:944`。debugfs 建立在 `dma_debug_fs_init()`（`:807`，`core_initcall_sync` 在 `:823`），具体文件清单见 07 §3。

### §6.4 `DMA_API_DEBUG_SG`

`kernel/dma/Kconfig:220`，是 `DMA_API_DEBUG` 之下的子开关，专门开 `check_sg_segment()` 那一类检查。⚠️ **它默认是 y**（在 `DMA_API_DEBUG` 已开的前提下），所以不需要额外操作。

### §6.5 `dma_debug_add_bus()`：唯一提到 `bus_type` 的地方

`kernel/dma/debug.c:875`，声明在 `include/linux/dma-map-ops.h:383`（真实现）/ `:386`（空 inline）。**它接受一个 `struct bus_type *`，给那条总线上的所有设备挂一个 unbind 通知**，在设备解绑时检查有没有未解映射的 DMA。

⚠️ **本树里没有任何调用者。**

```bash
grep -rn "dma_debug_add_bus" --include=*.c --include=*.h .   # 只有定义和声明，没有调用
```

⚠️ **这是整个 dma-mapping 子系统唯一出现 `bus_type` 的地方**（02 §1.0.6 判定"框架型"的依据之一），而且是死代码。**别把它当成"dma-mapping 有总线概念"的证据。**

---

## §7 映射性能基准（`DMA_MAP_BENCHMARK`）

`kernel/dma/map_benchmark.c`（378 行），`kernel/dma/Kconfig:237`，默认 n。

**挂载点：** 不在时间线上——它是一个**独立的 platform + PCI 驱动**，要手工 bind 到一个设备上才跑。

| 组件 | 位置 |
|---|---|
| ioctl 号 `DMA_MAP_BENCHMARK` | `include/linux/map_benchmark.h:9`（`_IOWR('d', 1, struct map_benchmark)`） |
| 参数结构 `struct map_benchmark` | `include/linux/map_benchmark.h:18` |
| debugfs 文件 `dma_map_benchmark` | `kernel/dma/map_benchmark.c:318`（模式 0600） |
| ioctl 处理 | `kernel/dma/map_benchmark.c:195-...`，`DMA_MAP_BENCHMARK` 分支在 `:207` |
| 工作线程 | `kernel/dma/map_benchmark.c:34` |
| 两个驱动 | platform `:332`、PCI `:345` |
| 用户态工具 | `tools/testing/selftests/dma/dma_map_benchmark.c` |

参数校验（`:209`-`:226`）：`threads > DMA_MAP_MAX_THREADS`、`seconds > DMA_MAP_MAX_SECONDS`、`dma_trans_ns > DMA_MAP_MAX_TRANS_DELAY`、`node` 范围。

⚠️ **ioctl 号和参数结构定义在 `include/linux/map_benchmark.h`，不在 `include/uapi/`。**

```bash
ls include/uapi/linux/map_benchmark.h    # → No such file
```

含义：这个 ioctl **不是稳定 ABI**，`tools/testing/selftests/dma/` 里的工具靠复制头文件内容工作。**不要在产品代码里用它。**

---

## §8 `dma_dummy_ops`

`kernel/dma/dummy.c`（38 行，无条件编译），实例在 `:33`，声明在 `include/linux/dma-map-ops.h:394`。

**挂载点：** 不在时间线上。全树唯一安装点：

```bash
grep -rn "dma_dummy_ops" --include=*.c .
# drivers/acpi/scan.c:1625:  set_dma_ops(dev, &dma_dummy_ops);
# kernel/dma/dummy.c:33:     const struct dma_map_ops dma_dummy_ops = {
```

⚠️ **arm64 不走 ACPI（本文档集的范围约定，记入 99 §2.7），所以这个框架在 arm64 上编译进来但永不安装。**

⚠️ **它的价值是模式而不是功能**：所有回调返回失败，用来把设备标成"不能 DMA"，从而免掉在每个 API 入口加检查。38 行换掉几十处 `if`。

---

## §9 两个辅助文件

这两个不是独立框架，是上面几节共用的工具，列出来是为了说明"为什么 `kernel/dma/` 有 11 个文件"。

### §9.1 `remap.c`（70 行，`DMA_DIRECT_REMAP` 门控）

| 函数 | 行 | 谁调 |
|---|---|---|
| `dma_common_find_pages()` | `:9` | `dma_direct_free()` 找回 page 数组 |
| `dma_common_pages_remap()` | `:22` | `dma_alloc_noncontiguous` 路径 |
| `dma_common_contiguous_remap()` | `:38` | ⚠️ **`dma_direct_alloc()` `:292` 和 `atomic_pool_expand()` `:104` —— 两个最热的 uncached 映射点** |
| `dma_common_free_remap()` | `:60` | 释放侧 |

⚠️ **arm64 非一致性设备的每一次 `dma_alloc_coherent()` 都经过 `:38`。** 70 行的文件，是 arm64 一致性内存分配的必经之路。

### §9.2 `ops_helpers.c`（93 行，`DMA_OPS` 门控）

给**实现 `dma_map_ops` 的人**用的通用实现，本身不被 direct 路径调用：

| 函数 | 行 | 用途 |
|---|---|---|
| `dma_common_vaddr_to_page()` | `:8`（static） | 内部辅助 |
| `dma_common_get_sgtable()` | `:18` | `ops->get_sgtable` 的现成实现 |
| `dma_common_mmap()` | `:34` | `ops->mmap` 的现成实现 |
| `dma_common_alloc_pages()` | `:61` | `ops->alloc_pages` 的现成实现 |
| `dma_common_free_pages()` | `:84` | 配对 |

⚠️ **arm64 上只有 `iommu_dma_ops` 可能用到它们。** 普通设备走 direct 路径，这个文件的代码一行不执行。

---

## §10 汇总表

| 框架 | 职责 | 挂在 03 时间线的哪一点 | arm64 是否相关 |
|---|---|---|---|
| swiotlb | 寻址不够时搬数据 | §A1 分配池 / §B4 三个触发点 | ⚠️ 编译进来，池可能为空 |
| 受限池 | 把设备关在一块内存里 | §A2 第 10 步（无 IOMMU 时） | 默认关；全树 1 个 dts 用 |
| CMA | 大块连续内存 | §A1 预留 / §A5 第 2 优先级 | 是 |
| per-NUMA CMA | 本地节点连续内存 | 同上 | NUMA 机器默认开，需 `cma_pernuma=` |
| 原子池 | 原子上下文的一致性内存 | §A5 出口 4/5 | ⚠️ 恒开（被 `DMA_DIRECT_REMAP` select） |
| per-device 一致性池 | 设备专属一致性内存 | ⚠️ **在 §A5 之前**（`mapping.c:501`） | remoteproc 会用 |
| 全局一致性池 | 全系统一份 | §A5 出口 3 | ❌ 只有 hexagon |
| DMA-API debug | 误用检测 | §B5 的 11 个点 | 默认关，需手工开 |
| map benchmark | 映射吞吐测量 | 不在时间线上（独立驱动） | 默认关 |
| `dma_dummy_ops` | 把设备标成不可 DMA | 不在时间线上 | ❌ 唯一安装点在 ACPI |
| remap.c | 建 uncached 映射 | 被 §A5 `:292` 和 §4.3 `:104` 调 | ⚠️ 是，且是热路径 |
| ops_helpers.c | 给 ops 实现者的通用件 | 被 ops 实现调 | 只有 `iommu_dma_ops` |

## §11 每个框架的全树使用统计

| 框架 | 口径 | 数字 | 重跑命令 |
|---|---|---|---|
| swiotlb | `kernel/dma/` 外引用 `swiotlb_*` 的 `.c` 文件数 | **40** | `grep -rl "swiotlb_" --include=*.c . \| grep -v "^./kernel/dma/" \| wc -l` |
| 受限池 | arm64 dts 节点文件数 | **1** | `grep -rl "restricted-dma-pool" arch/arm64/boot/dts/ \| wc -l` |
| CMA | arm64 dts 带 `reusable` 的文件数 | **9** | `grep -rl "reusable" arch/arm64/boot/dts/ \| wc -l` |
| CMA | arm64 dts 带 `linux,cma-default` 的文件数 | **9** | `grep -rl "linux,cma-default" arch/arm64/boot/dts/ \| wc -l` |
| reserved-memory 通用 | 调 `of_reserved_mem_device_init()` 的 `.c` 文件数 | **31** | `grep -rl "of_reserved_mem_device_init" --include=*.c . \| wc -l` |
| per-device 一致性池 | 调 `dma_declare_coherent_memory()` 的 `.c` 文件数 | **8**（7 个 `arch/sh/` + `drivers/remoteproc/remoteproc_virtio.c`） | `grep -rl "dma_declare_coherent_memory" --include=*.c . \| wc -l` |
| 全局一致性池 | 调 `dma_init_global_coherent()` 的架构数 | **1**（hexagon） | `grep -rn "dma_init_global_coherent" --include=*.c arch/` |
| `dma_dummy_ops` | 安装点数 | **1**（`drivers/acpi/scan.c:1625`） | `grep -rn "dma_dummy_ops" --include=*.c .` |
| `dma_debug_add_bus` | 调用点数 | **0** | `grep -rn "dma_debug_add_bus" --include=*.c .` |
| `dma_map_ops` 实例 | 全树定义数 | **24**，非 `arch/`、非 parisc 的 **6** | `grep -rnE 'struct dma_map_ops[[:space:]]+[a-z_0-9]+[[:space:]]*=[[:space:]]*\{' --include=*.c --include=*.h . \| wc -l` |

⚠️ **`of_reserved_mem_device_init()` 那 31 个文件是三种池的混合**（CMA / per-device 一致性 / 受限池），光看调用者分不出来。要看的是它们对应的 dts 节点属性组合——见 06 §4。

---

## §12 下一篇读什么

| 想知道 | 去 |
|---|---|
| 驱动侧怎么用这些框架 | 05 |
| dts 里 `shared-dma-pool` 到底会变成哪种池 | 06 §4 |
| 每个框架的 debugfs 长什么样 | 07 §3 |
| 为什么需要 bounce、为什么需要原子池 | 08 §3 / 08 §5 |
