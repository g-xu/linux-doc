---
title: 使用指南
---

# dma-mapping 使用骨架

⚠️ **本篇所有代码骨架都没有编译过、没有运行过。** 它们是从本树真实驱动里提炼的结构，用来说明调用次序和配对关系，不是可以直接粘进产品的代码。抄用之前请自己编译，并核对 01 里对应 API 的返回值语义。

## §0 本子系统有哪些角色

| 角色 | 是谁 | 本篇 | 数量级 |
|---|---|---|---|
| 掩码协商者 | ⚠️ **每一个做 DMA 的驱动**，无例外 | §1 §2 | 全树最大 |
| 流式映射消费者 | 网卡、块设备、大多数控制器 | §3 §4 | 很大 |
| 一致性内存消费者 | 需要描述符环 / 邮箱的驱动 | §5 §6 | 很大 |
| noncoherent / noncontiguous 消费者 | 音频、V4L2、少数图形 | §7 | 少 |
| mmap 到用户态的驱动 | V4L2、DRM、部分 misc | §8 | 少 |
| `dma_map_ops` 实现者 | ⚠️ arm64 上只有 `drivers/iommu/dma-iommu.c` 和两个 vdpa 模拟器 | §9 | 极少 |
| reserved-memory 提供者 | 写 dts 的人 + 调 `of_reserved_mem_device_init()` 的驱动 | 见 06 | 31 个文件 |

⚠️ **前三个角色覆盖了 99% 的驱动作者。** §9 那一节写给极少数人，读它之前先确认你真的不能用 direct 路径——03 §B1 解释了为什么 arm64 通常不需要自己的 ops。

---

## §1 掩码协商：每个驱动的第一步

### §1.1 最常见的写法

```c
static int foo_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	int ret;

	/* 一次设置 dma_mask 和 coherent_dma_mask，绝大多数驱动只需要这一句 */
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64));
	if (ret)
		return dev_err_probe(dev, ret, "could not set DMA mask\n");

	/* ... 后续 ioremap / 申请中断 / 建描述符环 ... */
	return 0;
}
```

真实对照：`drivers/spi/spi-geni-qcom.c:902-904`（arm64 平台，同样的三行）。

| 符号 | 位置 | 备注 |
|---|---|---|
| `dma_set_mask_and_coherent()` | `include/linux/dma-mapping.h:448`（`static inline`） | 内部先调 `dma_set_mask()` 再调 `dma_set_coherent_mask()` |
| `DMA_BIT_MASK(n)` | `include/linux/dma-mapping.h:77` | ⚠️ `n == 64` 时特判成 `~0ULL`，因为 `1ULL << 64` 是未定义行为 |
| `dev_err_probe()` | `include/linux/device.h`（驱动模型，不属本子系统） | 会把 `-EPROBE_DEFER` 降级成 debug 级日志 |

⚠️ **`dma_set_mask_and_coherent()` 失败必须让 probe 失败，不能忽略返回值。** 03 §A4 说明了 `dma_set_mask()` 在 `kernel/dma/mapping.c:757` 用 `dma_supported()` 挡掉不支持的掩码——失败意味着这个平台真的寻址不到那么宽，继续下去后面每次映射都会走 bounce 或者直接失败。

### §1.2 什么时候需要分开设两个掩码

只有一种情况：**流式映射和一致性内存的寻址能力不同**。这在真实硬件上极罕见。分开写法：

```c
	ret = dma_set_mask(dev, DMA_BIT_MASK(64));        /* 流式：64 位 */
	if (ret)
		return ret;
	ret = dma_set_coherent_mask(dev, DMA_BIT_MASK(32)); /* 描述符环必须在 4G 以下 */
	if (ret)
		return ret;
```

⚠️ **次序不能颠倒**：`dma_set_coherent_mask()` 只改 `dev->coherent_dma_mask`（`kernel/dma/mapping.c:770-783`），而 `dma_set_mask_and_coherent()` 会把两个都覆盖掉。先窄后宽会被后一句抹掉。

---

## §2 多掩码回退骨架

**这是 01 承诺的"见 05 §2"。** 硬件"可能支持 64 位、也可能只支持 32 位"时，用**试探 + 降级**而不是硬编码：

```c
static int foo_setup_dma(struct foo_dev *foo)
{
	struct device *dev = foo->dev;

	/* 硬件寄存器说自己支持 64 位地址 */
	if (foo->hw_caps & FOO_CAP_64BIT_DMA) {
		if (!dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64))) {
			foo->desc_addr_bits = 64;
			return 0;
		}
		/* 平台不给：清掉能力位，退回 32 位 */
		dev_warn(dev, "64-bit DMA not available, falling back\n");
		foo->hw_caps &= ~FOO_CAP_64BIT_DMA;
	}

	if (!dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32))) {
		foo->desc_addr_bits = 32;
		return 0;
	}

	return -EIO;	/* 连 32 位都不行，这个平台不能用 */
}
```

真实对照：`drivers/mmc/host/sdhci.c:4104-4112`——先试 64 位，失败就 `pr_warn` 并清掉 `SDHCI_USE_64_BIT_DMA` 标志，然后继续往下走 32 位路径。

⚠️ **回退时必须同时改掉自己的软件状态**（这里是 `desc_addr_bits` / `hw_caps`）。只降掩码不降描述符格式，硬件会按 64 位格式解析一个 32 位地址——这是一类很难查的静默损坏。

⚠️ **不要用 `dma_coerce_mask_and_coherent()` 代替上面的试探。** 它在 `include/linux/dma-mapping.h:460`，作用是**绕过 `dma_supported()` 检查强行写入掩码**。真实用途只有一个：设备本身没有父总线来设掩码（如某些自建的 `struct device`）。⚠️ **用它来"绕过掩码设置失败"是误用**——`dma_supported()` 说不行就是真的不行。

---

## §3 流式映射消费者

### §3.1 单块缓冲：map / 用 / unmap 三段

```c
	dma_addr_t handle;

	/* 1. 映射 */
	handle = dma_map_single(dev, buf, len, DMA_TO_DEVICE);
	if (dma_mapping_error(dev, handle))
		return -ENOMEM;

	/* 2. 交给硬件，硬件读完为止 */
	writel(lower_32_bits(handle), foo->base + FOO_DMA_ADDR);
	writel(len, foo->base + FOO_DMA_LEN);
	wait_for_completion(&foo->done);

	/* 3. 解映射——必须和 map 用同一个 dir 和 len */
	dma_unmap_single(dev, handle, len, DMA_TO_DEVICE);
```

| 检查点 | 为什么 |
|---|---|
| `dma_mapping_error()`（`include/linux/dma-mapping.h:95`） | ⚠️ **返回值不是指针，不能用 `if (!handle)` 判断**——0 是合法 DMA 地址。失败值是 `DMA_MAPPING_ERROR`（`:75`，即 `~(dma_addr_t)0`） |
| unmap 的 `len` / `dir` 与 map 一致 | 07 §4 那张 debug 报告表里最常见的一条就是这个不一致 |
| `buf` 不能是 vmalloc 地址 | ⚠️ `dma_map_single()` 在 `include/linux/dma-mapping.h:332-334` 有 `dev_WARN_ONCE(dev, is_vmalloc_addr(ptr), "rejecting DMA map of vmalloc memory\n")` 并直接返回 `DMA_MAPPING_ERROR`；`:331` 的注释写明 "DMA must never operate on areas that might be remapped" |
| `buf` 不能在栈上 | 04 §6.1 的 `check_for_stack()` 专抓这个；但那要开 `DMA_API_DEBUG` 才会报 |

### §3.2 页内偏移：`dma_map_page()`

缓冲来自 `struct page` 而不是内核线性地址时用它：

```c
	handle = dma_map_page(dev, page, offset, len, DMA_FROM_DEVICE);
	if (dma_mapping_error(dev, handle))
		return -ENOMEM;
	/* ... 硬件写入 ... */
	dma_unmap_page(dev, handle, len, DMA_FROM_DEVICE);
```

⚠️ **`dma_map_single()` 本身就是 `dma_map_page()` 的包装**（`include/linux/dma-mapping.h:328-338`，内部 `virt_to_page` + `offset_in_page`）。所以只要能拿到 page，两者等价，没有性能差别。

### §3.3 反复读写同一块映射：`dma_sync_*`

映射一次、来回读写多次时（典型是环形描述符旁边的数据缓冲），**不要反复 map/unmap，用 sync**：

```c
	/* 建立一次长期映射 */
	handle = dma_map_single(dev, buf, len, DMA_BIDIRECTIONAL);

	for (;;) {
		/* CPU 要读硬件刚写的内容 */
		dma_sync_single_for_cpu(dev, handle, len, DMA_FROM_DEVICE);
		process(buf);

		/* CPU 改完了，交回硬件 */
		fill(buf);
		dma_sync_single_for_device(dev, handle, len, DMA_TO_DEVICE);
		kick_hw(foo);
	}

	dma_unmap_single(dev, handle, len, DMA_BIDIRECTIONAL);
```

⚠️ **两个 sync 的 `dir` 可以和 map 时的 `dir` 不同**，而且**应该**不同：map 时写 `DMA_BIDIRECTIONAL` 表示这块映射两个方向都会用，每次 sync 写实际这一次的方向，让 03 §B3 那两个 arm64 缓存操作能各自省掉一半工作。

⚠️ **arm64 上 `dma_sync_single_for_cpu(..., DMA_TO_DEVICE)` 是空操作**（`arch/arm64/mm/dma-mapping.c:29-30` 直接 return）。写错方向不会立刻报错，只会在某些方向上静默丢数据——08 §4 展开这个不对称。

### §3.4 `dma_map_resource()`：映射的是别人的寄存器

```c
	/* 把另一个设备的 MMIO 物理地址映射成本设备可以 DMA 到的地址 */
	handle = dma_map_resource(dev, phys_mmio_addr, size, DMA_TO_DEVICE, 0);
```

⚠️ **它不做任何缓存操作，因为目标不是内存。** 用途是设备到设备的搬运（P2P）。⚠️ **别拿它映射普通内存**——它跳过了 bounce 和 cache 逻辑。

---

## §4 sgtable 消费者

### §4.1 推荐写法：`dma_map_sgtable()`

```c
	struct sg_table sgt;
	int ret;

	/* 自己或上层准备好 sgt（页数、每段长度） */
	ret = dma_map_sgtable(dev, &sgt, DMA_TO_DEVICE, 0);
	if (ret)
		return ret;

	/* 遍历映射后的段——注意用 nents 和 for_each_sgtable_dma_sg */
	for_each_sgtable_dma_sg(&sgt, sg, i) {
		push_desc(foo, sg_dma_address(sg), sg_dma_len(sg));
	}

	kick_hw(foo);
	wait_for_completion(&foo->done);

	dma_unmap_sgtable(dev, &sgt, DMA_TO_DEVICE, 0);
```

| 符号 | 位置 | 备注 |
|---|---|---|
| `dma_map_sgtable()` | `kernel/dma/mapping.c:266-276`，`EXPORT_SYMBOL_GPL` 在 `:277` | ⚠️ **返回 0 / 负错误码**，不是段数 |
| `dma_unmap_sgtable()` | `include/linux/dma-mapping.h:371`（`static inline`） | — |
| `sg_dma_address()` / `sg_dma_len()` | `include/linux/scatterlist.h` | ⚠️ 必须在 map **之后**读 |
| `for_each_sgtable_dma_sg()` | `include/linux/scatterlist.h` | ⚠️ 用 `sgt->nents`（映射后段数） |

### §4.2 为什么优先用 sgtable 而不是 `dma_map_sg()`

`dma_map_sg_attrs()`（`kernel/dma/mapping.c:227-236`）返回**成功映射的段数，失败返回 0**。⚠️ **0 既可能是"失败"也可能被误当成"映射了 0 段"**，而且它丢掉了具体错误码。03 §A6 提到核心在 `kernel/dma/mapping.c:200-204` 专门校验实现返回的错误码是不是那三个允许值——**这层信息在 `dma_map_sg()` 的接口上被压成 0 了**。

`dma_map_sgtable()` 是同一段代码的薄封装，只是把错误码原样返回。**新代码一律用 sgtable 版本。**

### §4.3 两个 nents 不能混

| 字段 | 含义 | 什么时候有效 |
|---|---|---|
| `sgt->orig_nents` | ⚠️ **映射前**的段数 | 建 sgt 时就有 |
| `sgt->nents` | ⚠️ **映射后**的段数（IOMMU 可能把多段合并成一段） | 只在 map 成功后有效 |

⚠️ **遍历 DMA 地址用 `nents`，遍历 CPU 页用 `orig_nents`。** 混用在无 IOMMU 的平台上"能工作"（两者相等），一上 SMMU 就越界或漏段——这是 sg 类 bug 最集中的来源。02 §5 有完整对照。

---

## §5 一致性内存消费者

### §5.1 标准三段

```c
	void *desc;
	dma_addr_t desc_dma;

	desc = dma_alloc_coherent(dev, ring_bytes, &desc_dma, GFP_KERNEL);
	if (!desc)
		return -ENOMEM;

	writel(lower_32_bits(desc_dma), foo->base + FOO_RING_BASE);
	writel(upper_32_bits(desc_dma), foo->base + FOO_RING_BASE_HI);

	/* ... 整个设备生命周期内一直用 ... */

	dma_free_coherent(dev, ring_bytes, desc, desc_dma);
```

| 检查点 | 为什么 |
|---|---|
| ⚠️ 判 `!desc`（返回的是**指针**） | 和 §3.1 相反：`dma_alloc_coherent()` 失败返回 `NULL`，**不要**用 `dma_mapping_error()` |
| ⚠️ `free` 时的 `size` 必须和 `alloc` 一致 | `dma_free_attrs()` 用它算释放多少页 |
| ⚠️ **不能在原子上下文里 free** | `kernel/dma/mapping.c:533` 有 `WARN_ON(irqs_disabled())`，`:526-532` 的注释解释了原因（见 03 §A9） |
| 不需要任何 `dma_sync_*` | 这是"一致性"的定义。08 §2 解释代价 |

⚠️ **`dma_alloc_coherent()` 可以睡（`GFP_KERNEL`），也可以不睡（`GFP_ATOMIC`），但后者走的是完全不同的代码路径**（04 §4 的原子池），而且**池空时会失败并 `WARN`**。**probe 阶段一律用 `GFP_KERNEL`。**

### §5.2 write-combine 变体

```c
	buf = dma_alloc_wc(dev, size, &handle, GFP_KERNEL);
	...
	dma_free_wc(dev, size, buf, handle);
```

`include/linux/dma-mapping.h:564` / `:575`，本质是 `dma_alloc_attrs(..., DMA_ATTR_WRITE_COMBINE)`（属性定义在 `:29`）。

⚠️ **arm64 不 select `ARCH_HAS_DMA_WRITE_COMBINE`**（`kernel/dma/Kconfig:45`），所以 `dma_pgprot()`（`kernel/dma/mapping.c:415-424`）在 arm64 上对这个属性的处理和普通 uncached 相同。**在 arm64 上 `dma_alloc_wc()` 不比 `dma_alloc_coherent()` 快**——这个 API 是给 x86 等有独立 WC 内存类型的架构用的。

### §5.3 `DMA_ATTR_NO_KERNEL_MAPPING`：只给设备看

```c
	/* 只要 dma_handle，内核不需要访问这块内存 */
	cookie = dma_alloc_attrs(dev, size, &handle, GFP_KERNEL,
				 DMA_ATTR_NO_KERNEL_MAPPING);
```

⚠️ **返回值不是可以解引用的地址，是一个不透明的 cookie**（`kernel/dma/direct.c:203` 的注释明确写了这一点，见 03 §A5）。**解引用它会崩。** 唯一用途是原样传回 `dma_free_attrs()`。属性定义在 `include/linux/dma-mapping.h:34`。

---

## §6 devres 变体：`dmam_*`

probe 里分配、remove 里释放的一致性内存，可以让设备资源管理自动收尾：

```c
static int foo_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;

	foo->desc = dmam_alloc_coherent(dev, ring_bytes, &foo->desc_dma,
					GFP_KERNEL);
	if (!foo->desc)
		return -ENOMEM;
	/* 不需要写 remove 里的 free */
	return 0;
}
```

| 符号 | 位置 | 备注 |
|---|---|---|
| `dmam_alloc_coherent()` | `include/linux/dma-mapping.h:557`（`static inline`） | 包装 `dmam_alloc_attrs()` |
| `dmam_alloc_attrs()` | `kernel/dma/mapping.c:85-110`，`EXPORT_SYMBOL` 在 `:110` | — |
| `dmam_free_coherent()` | `kernel/dma/mapping.c:61-69` | ⚠️ **提前手工释放时必须用这个，不能用 `dma_free_coherent()`**——后者不会摘掉 devres 记录，remove 时会二次释放 |

⚠️ **`dmam_*` 只有 alloc/free 一对，没有映射侧的对应物。**

```bash
grep -nE "dmam_(map|unmap|sync)" include/linux/dma-mapping.h kernel/dma/mapping.c   # 空
```

**流式映射必须自己配平 map/unmap，没有 devres 帮你。** 这是 dma-mapping 和大多数子系统不同的一点：`devm_` 家族在这里只覆盖了一半。

---

## §7 noncoherent / noncontiguous 消费者

这一组 API 的存在理由是：**一致性内存在 arm64 上是 uncached 的，逐字节访问极慢**（08 §2）。当驱动愿意自己写 sync、换取 cached 访问速度时用它们。

### §7.1 `dma_alloc_pages()` / `dma_alloc_noncoherent()`

```c
	void *buf;
	dma_addr_t handle;

	/* cached 内存 + 已经映射好的 DMA 地址 */
	buf = dma_alloc_noncoherent(dev, size, &handle, DMA_FROM_DEVICE,
				    GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	for (;;) {
		kick_hw(foo);
		wait_for_completion(&foo->done);
		/* ⚠️ 自己负责这一句，忘了就读到旧数据 */
		dma_sync_single_for_cpu(dev, handle, size, DMA_FROM_DEVICE);
		process(buf);
	}

	dma_free_noncoherent(dev, size, buf, handle, DMA_FROM_DEVICE);
```

| 符号 | 位置 | 备注 |
|---|---|---|
| `dma_alloc_noncoherent()` | `include/linux/dma-mapping.h:315-320`（`static inline`） | 包装 `dma_alloc_pages()` + `page_address()` |
| `dma_alloc_pages()` | `kernel/dma/mapping.c:564-573`（`EXPORT_SYMBOL_GPL`） | 返回 `struct page *` |
| `dma_free_pages()` | `kernel/dma/mapping.c:587-593` | — |
| `dma_mmap_pages()` | `kernel/dma/mapping.c:595-606` | 映射到用户态，见 §8 |

⚠️ **`dir` 参数在 alloc 时就要给定，而且 free 时必须一致。** 这和 `dma_alloc_coherent()` 不同——后者没有方向概念。原因是这块内存从分配那一刻起就已经是"被映射的"状态。

⚠️ **拿到的是 cached 内存，所以每次硬件写完都必须 `dma_sync_single_for_cpu()`，每次 CPU 写完都必须 `dma_sync_single_for_device()`。** 少一句就是随机的数据陈旧——而且在一致性平台（`dev->dma_coherent` 为真）上测不出来。

### §7.2 `dma_alloc_noncontiguous()`：大块、不要求物理连续

```c
	struct sg_table *sgt;
	void *vaddr;

	sgt = dma_alloc_noncontiguous(dev, size, DMA_FROM_DEVICE,
				      GFP_KERNEL, 0);
	if (!sgt)
		return -ENOMEM;

	/* 需要 CPU 访问时才建线性映射 */
	vaddr = dma_vmap_noncontiguous(dev, size, sgt);
	if (!vaddr) {
		dma_free_noncontiguous(dev, size, sgt, DMA_FROM_DEVICE);
		return -ENOMEM;
	}

	/* 硬件侧仍然要逐段推描述符 */
	for_each_sgtable_dma_sg(sgt, sg, i)
		push_desc(foo, sg_dma_address(sg), sg_dma_len(sg));

	/* ... */

	dma_vunmap_noncontiguous(dev, vaddr);
	dma_free_noncontiguous(dev, size, sgt, DMA_FROM_DEVICE);
```

| 符号 | 位置 |
|---|---|
| `dma_alloc_noncontiguous()` | `kernel/dma/mapping.c:632-652`（`EXPORT_SYMBOL_GPL` 在 `:652`） |
| `dma_free_noncontiguous()` | `kernel/dma/mapping.c:663-674` |
| `dma_vmap_noncontiguous()` | `kernel/dma/mapping.c:676-686` |
| `dma_vunmap_noncontiguous()` | `kernel/dma/mapping.c:688-695` |
| `dma_mmap_noncontiguous()` | `kernel/dma/mapping.c:697-712` |

⚠️ **`dma_vmap_noncontiguous()` 是可选的第二步，不是必需的。** 只在 CPU 真的需要一段连续虚拟地址时才调——这是这套 API 相比 `dma_alloc_coherent()` 的核心优势：**vmap 的代价可以按需支付**。

⚠️ **无 IOMMU 的 arm64 平台上，`dma_alloc_noncontiguous()` 退化成只能给出一段**（direct 路径没有把散页拼成一个连续 DMA 地址的能力）。所以"不连续"这个优势只有上了 SMMU 才拿得到。

---

## §8 mmap 到用户态

三种分配方式各有自己的 mmap 入口，**不能混用**：

| 分配用的 | mmap 用的 | 位置 |
|---|---|---|
| `dma_alloc_coherent()` / `dma_alloc_attrs()` | `dma_mmap_coherent()` / `dma_mmap_attrs()` | `kernel/dma/mapping.c:457-470` |
| `dma_alloc_pages()` | `dma_mmap_pages()` | `kernel/dma/mapping.c:595-606` |
| `dma_alloc_noncontiguous()` | `dma_mmap_noncontiguous()` | `kernel/dma/mapping.c:697-712` |

```c
static int foo_mmap(struct file *file, struct vm_area_struct *vma)
{
	struct foo_dev *foo = file->private_data;

	/* ⚠️ 先问平台支不支持，别直接 mmap */
	if (!dma_can_mmap(foo->dev))
		return -ENXIO;

	return dma_mmap_coherent(foo->dev, vma, foo->buf, foo->handle,
				 foo->size);
}
```

| 符号 | 位置 | 备注 |
|---|---|---|
| `dma_can_mmap()` | `kernel/dma/mapping.c:434-442`（`EXPORT_SYMBOL_GPL`） | ⚠️ **arm64 上恒真**（`DMA_DIRECT_REMAP` select 了 `DMA_NONCOHERENT_MMAP`，见 04 §0），但写上它让代码在别的架构上也对 |
| `dma_get_sgtable_attrs()` | `kernel/dma/mapping.c:395-408` | 把一致性分配转成 sg_table，给 dma-buf 导出用 |

⚠️ **`DMA_ATTR_NO_KERNEL_MAPPING` 分配的内存不能 mmap**——没有内核映射就没有页可以映射给用户态。

---

## §9 `dma_map_ops` 实现者

⚠️ **读这一节之前先确认你真的需要它。** 03 §B1 列了六步理由说明 arm64 普通设备为什么不需要自己的 ops；04 §11 的统计显示全树 24 个实例里，arm64 上会激活的只有 `drivers/iommu/dma-iommu.c:1558` 的 `iommu_dma_ops`。**新写一份几乎一定是走错路了。**

### §9.1 最小骨架

```c
static const struct dma_map_ops foo_dma_ops = {
	/* 一致性内存：alloc/free 必须成对给，或者都不给 */
	.alloc			= foo_alloc,
	.free			= foo_free,

	/* 流式：单块 */
	.map_page		= foo_map_page,
	.unmap_page		= foo_unmap_page,

	/* 流式：sg —— ⚠️ map_sg 的返回值约定见下 */
	.map_sg			= foo_map_sg,
	.unmap_sg		= foo_unmap_sg,

	/* 非一致性平台必须给这四个 */
	.sync_single_for_cpu	= foo_sync_single_for_cpu,
	.sync_single_for_device	= foo_sync_single_for_device,
	.sync_sg_for_cpu	= foo_sync_sg_for_cpu,
	.sync_sg_for_device	= foo_sync_sg_for_device,

	/* 能力查询 */
	.dma_supported		= foo_dma_supported,

	/* mmap / get_sgtable 可以直接用现成的（04 §9.2） */
	.mmap			= dma_common_mmap,
	.get_sgtable		= dma_common_get_sgtable,
};
```

安装方式：`set_dma_ops(dev, &foo_dma_ops)`（`include/linux/dma-map-ops.h:96`）。⚠️ **在 arm64 上正规的安装点是 `arch_setup_dma_ops()`**（`arch/arm64/mm/dma-mapping.c:64-80`，见 03 §A3），不是在驱动 probe 里随手调。

### §9.2 `.map_sg` 的返回值约定必须逐字遵守

02 §3.1 引了 `include/linux/dma-map-ops.h` 里那段注释的原文。要点：

| 返回 | 含义 |
|---|---|
| 正数 | 成功映射的段数 |
| `-EINVAL` | 参数不对 |
| `-ENOMEM` | 资源不够 |
| `-EIO` | 有段的地址设备寻址不到 |
| `-EREMOTEIO` | P2P 相关 |

⚠️ **核心会校验返回的错误码是不是这几个之一**：`kernel/dma/mapping.c:200-204`（见 03 §A6）。返回别的负值会触发 `WARN_ON_ONCE`。**返回 0 是非法的**——0 段成功没有意义。

### §9.3 三个可以不实现的回调

| 回调 | 不实现的后果 |
|---|---|
| `.alloc` / `.free` | `dma_alloc_attrs()` 返回 `NULL`（`kernel/dma/mapping.c:508-509` 判 `!ops->alloc`） |
| `.mmap` | `dma_can_mmap()` 返回假，用户态映射不了 |
| `.get_required_mask` | `dma_get_required_mask()` 回落到按内存上限算 |

---

## §10 近邻符号区分

这一节独立成节，因为下面每一组的两个符号**名字只差几个字母、编译都能过、行为不同**。

### §10.1 `dma_mapping_error()` vs 判 NULL

| 用在 | 判据 |
|---|---|
| `dma_map_*()` 返回的 `dma_addr_t` | ⚠️ **必须** `dma_mapping_error(dev, handle)` |
| `dma_alloc_*()` 返回的指针 | ⚠️ **必须** `!ptr` |

⚠️ **错用会怎样**：`dma_map_single()` 成功返回 0（合法地址）被 `if (!handle)` 判成失败 → 白白报 `-ENOMEM`；反过来，`dma_alloc_coherent()` 返回 `NULL` 被 `dma_mapping_error()` 检查（`NULL != ~0`）→ 判成成功，随后解引用 `NULL` 崩掉。

### §10.2 `dma_map_sg_attrs()` vs `dma_map_sgtable()`

| API | 成功返回 | 失败返回 |
|---|---|---|
| `dma_map_sg_attrs()` / `dma_map_sg()` | ⚠️ **段数（正数）** | ⚠️ **0** |
| `dma_map_sgtable()` | ⚠️ **0** | 负错误码 |

⚠️ **错用会怎样**：把 `dma_map_sgtable()` 当 `dma_map_sg()` 用，`if (!ret) goto err` → **成功路径被当成失败**。这个错误在编译期完全看不出来（两者都返回 `int`）。

### §10.3 `sgt->nents` vs `sgt->orig_nents`

见 §4.3。⚠️ **错用会怎样**：无 IOMMU 平台上两者相等，测试全过；上了 SMMU 之后 `nents < orig_nents`，用 `orig_nents` 遍历 DMA 地址会读到未初始化的 `sg_dma_address()` → 硬件 DMA 到随机物理地址。

### §10.4 `dma_free_coherent()` vs `dmam_free_coherent()`

⚠️ **错用会怎样**：`dmam_alloc_coherent()` 分配、`dma_free_coherent()` 释放 → devres 记录还在，remove 时再释放一次 → double free。反过来（`dma_alloc_coherent()` + `dmam_free_coherent()`）会在 `kernel/dma/mapping.c:66` 的 `WARN_ON(devres_destroy(...))` 里报出来，因为找不到对应的 devres 项。

### §10.5 `dma_set_mask_and_coherent()` vs `dma_coerce_mask_and_coherent()`

| API | 检查 `dma_supported()`？ |
|---|---|
| `dma_set_mask_and_coherent()`（`include/linux/dma-mapping.h:448`） | ⚠️ **会** |
| `dma_coerce_mask_and_coherent()`（`:460`） | ⚠️ **不会，直接覆写 `dev->coherent_dma_mask` 再调前者** |

⚠️ **错用会怎样**：用 coerce 版本"解决"掩码设置失败 → 掩码设成了平台实际不支持的值 → 后续每次映射拿到设备寻址不到的地址，表现为 DMA 静默写错位置或 IOMMU 报 fault。

### §10.6 `dma_sync_single_for_cpu()` vs `dma_sync_single_for_device()`

⚠️ 名字里的 `for_XXX` 指的是**接下来谁要访问**，不是"刚才谁访问过"。

| 调用 | 时机 |
|---|---|
| `_for_cpu` | 硬件写完了，**CPU 马上要读** |
| `_for_device` | CPU 写完了，**硬件马上要读** |

⚠️ **错用会怎样**：arm64 上 `_for_device` 做 clean（`arch/arm64/mm/dma-mapping.c:16-22`）、`_for_cpu` 做 invalidate（`:24-33`）。写反了 → CPU 的脏行没被刷出去，硬件读到旧数据；或者 CPU 侧的 invalidate 把还没写回的修改丢掉。08 §4 展开。

### §10.7 `dma-mapping.h` vs `dma-map-ops.h`

见 §11。

---

## §11 该 include 哪个头文件

**这是 01 承诺的"见 05 §11"。**

| 头文件 | 给谁用 | 判据 |
|---|---|---|
| `include/linux/dma-mapping.h` | ⚠️ **消费者（99% 的驱动）** | 你调 `dma_map_*` / `dma_alloc_*` |
| `include/linux/dma-map-ops.h` | ⚠️ **实现者（架构代码、IOMMU 层）** | 你定义 `struct dma_map_ops` 实例，或调 `set_dma_ops()` |
| `include/linux/dma-direct.h` | ⚠️ **只有 `kernel/dma/` 和架构代码** | 你需要 `phys_to_dma()` 这类地址转换 |
| `include/linux/swiotlb.h` | ⚠️ **只有实现 bounce 的层** | 你直接操作 `io_tlb_mem` |

⚠️ **普通驱动 include 了 `dma-map-ops.h` 就是一个信号：要么走错路了（在驱动里 `set_dma_ops()`），要么在用不该用的内部符号。** 判断动作：

```bash
# 你的驱动里如果出现下面任何一个，就说明 include 错了
grep -nE "set_dma_ops|get_dma_ops|struct dma_map_ops|phys_to_dma|dma_to_phys" drivers/foo/
```

⚠️ **`dma-map-ops.h` 里的符号很多也带 `EXPORT_SYMBOL`，所以"能链接"不代表"该用"。** 05 的判据是 04 §11 那张表里的实例数：全树只有 6 个非架构实现者，你大概不是第 7 个。

---

## §12 陷阱清单

| 陷阱 | 症状 | 出处 |
|---|---|---|
| 栈上缓冲做 DMA | 随机崩溃、相邻栈变量被改 | 04 §6.1 |
| vmalloc 缓冲做 DMA | `dev_WARN_ONCE` + 映射失败 | §3.1 |
| 忘了 `dma_mapping_error()` | 用 `~0` 当地址交给硬件 | §10.1 |
| unmap 的 size/dir 和 map 不一致 | 开 debug 才报；否则缓存操作范围不对 | §3.1 |
| 在原子上下文 `dma_free_coherent()` | `WARN_ON(irqs_disabled())` | 03 §A9 |
| `GFP_ATOMIC` 的 `dma_alloc_coherent()` 密集调用 | 池空 → `WARN` + 返回 `NULL` | 04 §4.3 |
| 用 `orig_nents` 遍历 DMA 地址 | 只在有 IOMMU 时出错 | §4.3 |
| 解引用 `DMA_ATTR_NO_KERNEL_MAPPING` 的返回值 | 立即崩 | §5.3 |
| noncoherent 内存忘了 sync | 只在非一致性设备上出错 | §7.1 |
| 掩码降级但没改描述符格式 | 硬件按错误格式解析地址 | §2 |
| 在驱动里 `set_dma_ops()` | 覆盖了 `arch_setup_dma_ops()` 装好的 ops | §11 |

---

## §13 导航

| 想知道 | 去 |
|---|---|
| 每个 API 的完整签名和错误码 | 01 |
| 传进去的 `struct device` 里哪些字段被读 | 02 §2 |
| `dma_alloc_coherent()` 内部走了哪条分支 | 03 §A5 |
| 为什么必须配平 map/unmap | 03 §A8 |
| dts 侧要写什么才能拿到池 | 06 |
| 出错了怎么定位 | 07 |
| 为什么一致性内存慢、为什么 sync 不对称 | 08 §2 / 08 §4 |
