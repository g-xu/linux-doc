---
title: 设备树
---

# dma-mapping 的设备树侧

## §0 发现路径分诊表

⚠️ **别先问"dma-mapping 有没有 DT 绑定"。** 它本身不是一个"被 DT 探测出来的子系统"——**DT 只影响每个设备的 DMA 参数**，而参数的来源有五条路径并存：

| 路径 | 谁扫描 | 走不走 DT | arm64 用不用 | 本篇覆盖 |
|---|---|---|---|---|
| `of_dma_configure_id()` | `drivers/of/device.c:114-223`（driver core probe 前调） | ⚠️ **是，主路径** | ⚠️ **是** | §1 §2 §3 |
| `acpi_dma_configure()` | `drivers/acpi/scan.c` | 否 | ❌ 本文档集裁掉（99 §2.7） | — |
| 父总线代码硬编码 | PCI 层、平台代码 | 否 | 少数 | §7 |
| 驱动自己调 `dma_set_mask_and_coherent()` | 驱动 probe | 否 | ⚠️ **是，几乎全都会** | 05 §1 |
| reserved-memory 节点 → 三种池 | `fdt_init_reserved_mem()` + 三个 `RESERVEDMEM_OF_DECLARE` | ⚠️ **是** | 视板子 | §4 §5 |

⚠️ **前两条互斥，第三/四条叠加在前两条之上，第五条独立。** 一台 arm64 DT 板子上真正生效的是路径 1 + 路径 4（+ 可选的路径 5）。

### §0.1 按任务找章节

| 我想 | 去 |
|---|---|
| 让某个设备的 DMA 地址和 CPU 物理地址不一样 | §2 |
| 声明某个设备是缓存一致的 | §3 |
| 给某个设备划一块专属内存 | §4 |
| 建一个全局 CMA 区 | §5 |
| 把设备关在一块内存里（受限池） | §6 |
| 搞清 `shared-dma-pool` 到底会变成哪种池 | ⚠️ **§4.3 的决策树** |
| dts 写了但没生效，怎么定位 | §8 |
| 看到一条内核报错，想知道是哪个属性错了 | §9 |

---

## §1 核心真正会读的属性总表

⚠️ 这张表只列**会被 `kernel/dma/` 或 `drivers/of/` 消费**的属性。`iommus`、`dma-names` 之类由别的子系统消费的不在此列。

| 属性 | 写在哪个节点 | 谁消费 | 消费位置 | arm64 dts 用量 |
|---|---|---|---|---|
| `dma-ranges` | ⚠️ **设备的父节点 / 总线节点**，不是设备自己 | `of_dma_get_range()` | `drivers/of/address.c:969-1044` | **52 个文件** |
| `dma-coherent` | ⚠️ **设备节点或其任一 DMA 父节点** | `of_dma_is_coherent()` | `drivers/of/address.c:1098-1118` | **45 个文件** |
| `dma-noncoherent` | 同上 | 同上 | `drivers/of/address.c:1110-1112` | ⚠️ **arm64 dts 里 0 个** |
| `memory-region` | ⚠️ **设备节点** | `of_reserved_mem_device_init_by_idx()` + `of_dma_set_restricted_buffer()` | `drivers/of/of_reserved_mem.c:342`、`drivers/of/device.c:65/:74/:78` | **54 个文件** |
| `compatible = "shared-dma-pool"` | `/reserved-memory` 的子节点 | ⚠️ **两个 setup 函数抢** | `kernel/dma/contiguous.c:440`、`kernel/dma/coherent.c:402` | **40 个文件** |
| `reusable` | 同上 | `rmem_cma_setup()` 要它、`rmem_dma_setup()` 拒它 | `kernel/dma/contiguous.c:412`、`kernel/dma/coherent.c:367` | **9 个文件** |
| `no-map` | 同上 | ⚠️ `rmem_cma_setup()` **拒**它、`rmem_swiotlb_setup()` 也拒它、`rmem_dma_setup()` **在 arm64 上根本不看它** | `kernel/dma/contiguous.c:413`、`kernel/dma/swiotlb.c:1102`、`kernel/dma/coherent.c:370-375` | **105 个文件** |
| `linux,cma-default` | 同上 | `rmem_cma_setup()` | `kernel/dma/contiguous.c:402`、`:429-430` | **9 个文件** |
| `linux,dma-default` | 同上 | `rmem_dma_setup()` | `kernel/dma/coherent.c:378-381` | ⚠️ **arm64 dts 里 0 个，而且整段被编译掉** |
| `compatible = "restricted-dma-pool"` | `/reserved-memory` 的子节点 | `rmem_swiotlb_setup()` + `of_dma_set_restricted_buffer()` | `kernel/dma/swiotlb.c:1111`、`drivers/of/device.c:83` | ⚠️ **1 个文件** |
| `size` / `alignment` / `alloc-ranges` / `reg` | `/reserved-memory` 的子节点 | 通用 reserved-memory 层（不属本子系统） | `drivers/of/of_reserved_mem.c:101/:109/:129` | 大量 |

统计重跑：

```bash
grep -rl "dma-ranges"           arch/arm64/boot/dts/ | wc -l    # → 52
grep -rl "dma-coherent"         arch/arm64/boot/dts/ | wc -l    # → 45
grep -rl "dma-noncoherent"      arch/arm64/boot/dts/ | wc -l    # → 0
grep -rl "memory-region"        arch/arm64/boot/dts/ | wc -l    # → 54
grep -rl "shared-dma-pool"      arch/arm64/boot/dts/ | wc -l    # → 40
grep -rl "reusable"             arch/arm64/boot/dts/ | wc -l    # → 9
grep -rl "no-map"               arch/arm64/boot/dts/ | wc -l    # → 105
grep -rl "linux,cma-default"    arch/arm64/boot/dts/ | wc -l    # → 9
grep -rl "linux,dma-default"    arch/arm64/boot/dts/ | wc -l    # → 0
grep -rl "restricted-dma-pool"  arch/arm64/boot/dts/ | wc -l    # → 1
```

⚠️ **两个死字属性，但死的方式不同**：

| 属性 | arm64 dts 用量 | 为什么 |
|---|---|---|
| `dma-noncoherent` | 0 | ⚠️ **代码是活的，只是没人需要**：arm64 默认就是非一致（`CONFIG_OF_DMA_DEFAULT_COHERENT` 不选，见 §3.2），写了等于重复默认值 |
| `linux,dma-default` | 0 | ⚠️ **代码在 arm64 上被编译掉**：读它的那段在 `kernel/dma/coherent.c:377-383` 的 `#ifdef CONFIG_DMA_GLOBAL_POOL` 里，而 arm64 不 select `DMA_GLOBAL_POOL`（04 §5.3）——**写了也不会被读** |

```bash
grep -n "^#if\|^#endif" kernel/dma/coherent.c | sed -n '7,9p'   # 看 :377 / :383 这一对
grep -rn "DMA_GLOBAL_POOL" arch/arm64/Kconfig                    # 无输出
```

⚠️ **`no-map` 的 105 个文件远多于 `shared-dma-pool` 的 40 个**——因为 `no-map` 是通用 reserved-memory 属性，绝大多数用途是"把这块内存从内核线性映射里挖掉给固件用"，和 DMA 池无关。**看到 `no-map` 不要假设它是 DMA 池。**

---

## §2 `dma-ranges`：让 DMA 地址不等于物理地址

### §2.1 写在哪、几个格子

⚠️ **写在设备的父节点上，不是设备自己。** `of_dma_get_range()` 从设备节点开始**向上**找（`drivers/of/address.c:981` 在循环里读，`:987` 的注释写明"Once we find 'dma-ranges', then a missing one is an error"）。

真实例子（`arch/arm64/boot/dts/freescale/imx8mq.dtsi:323-328`）：

```dts
	soc: soc@0 {
		compatible = "fsl,imx8mq-soc", "simple-bus";
		#address-cells = <1>;
		#size-cells = <1>;
		ranges = <0x0 0x0 0x0 0x3e000000>;
		dma-ranges = <0x40000000 0x0 0x40000000 0xc0000000>;
		...
	};
```

三段含义：**子（DMA）地址 · 父（CPU 物理）地址 · 长度**。格子数由 `#address-cells` / `#size-cells` 决定——上面这个例子里 `dma-ranges` 的第一个 `0x40000000` 是 DMA 侧起点，`<0x0 0x40000000>` 是 CPU 侧起点（父节点 2 cells），`0xc0000000` 是长度。

⚠️ **`ranges` 和 `dma-ranges` 是两个独立的属性，方向也不同。** `ranges` 管 CPU 看设备寄存器，`dma-ranges` 管设备看内存。同一个节点上两者的数值毫无关系。

### §2.2 它变成了什么

`of_dma_get_range()` 把每条 range 变成一个 `struct bus_dma_region`（02 §4）：

| 字段 | 赋值位置 |
|---|---|
| `cpu_start` | `drivers/of/address.c:1035` |
| `dma_start` | `drivers/of/address.c:1036` |
| `offset` | ⚠️ `drivers/of/address.c:1038`，等于 `cpu_addr - bus_addr` |

数组挂在 `dev->dma_range_map`（`drivers/of/device.c:198`，见 03 §A2 第 7 步）。运行期由 `phys_to_dma()` / `dma_to_phys()` 查表（03 §B2）。

⚠️ **同时它还会窄化掩码。** `of_dma_configure_id()` 在 `drivers/of/device.c:192-194` 用 range 的覆盖范围算出一个 `dma_limit`，然后把 `dev->dma_mask` 收窄到不超过它。**所以 `dma-ranges` 既改地址、又改掩码**——这是最容易被忽略的一半。

### §2.3 三个陷阱

⚠️ **陷阱 1：写了 `dma-ranges` 但一条都解析不出来 → `of_dma_get_range()` 返回错误。** `drivers/of/address.c:1005-1006` 在 `range.cpu_addr == OF_BAD_ADDR` 时 `pr_err("translation of DMA address(%llx) to CPU address failed node(%pOF)\n", ...)`。**这是 `pr_err`，不是静默失败**——看到它就是 `dma-ranges` 的格子数写错了。

⚠️ **陷阱 2：完全没有 `dma-ranges` 不是错误。** `drivers/of/address.c:998` 只 `pr_debug("no dma-ranges found for node(%pOF)\n", np)`。**默认 dmesg 里看不到**，要开 dynamic debug。这意味着"我以为我写了 `dma-ranges`，实际节点位置不对"这种错误是完全静默的。

⚠️ **陷阱 3：`size & 1` 会被当成"你把 size 写成了掩码"。** `drivers/of/device.c:158-162`：长度是奇数时打印 `dev_warn(dev, "Invalid size 0x%llx for dma-range(s)\n", size)` 然后**擅自把 size 加一**（注释 `:155-156` 说明这是 work around）。⚠️ **这不是给你用的**——它兼容的是一批把 size 写成 `0xffffffff` 这种掩码形式的老 dts。**看到这条 warn 就去把 dts 的 size 改成真实长度**，别指望这个 work around。紧接着 `:164-168` 如果加一之后变成 0，`dev_err(dev, "Adjusted size 0x%llx invalid\n", size)` 并返 `-EINVAL`。

---

## §3 `dma-coherent` / `dma-noncoherent`

### §3.1 一个布尔属性，向上继承

```dts
	soc {
		dma-coherent;		/* 整个 soc 下面的设备默认都一致 */

		gpu@fd000000 {
			compatible = "...";
			/* 继承上面的 dma-coherent */
		};

		crypto@fe000000 {
			compatible = "...";
			dma-noncoherent;	/* 显式否掉继承 */
		};
	};
```

`of_dma_is_coherent()`（`drivers/of/address.c:1098-1118`）从设备节点开始沿 DMA 父链向上走（`:1114` 的 `of_get_next_dma_parent()`），**先命中哪个属性就用哪个**：

| 命中 | 结果 | 行 |
|---|---|---|
| `dma-coherent` | 一致 | `:1106-1108` |
| `dma-noncoherent` | ⚠️ **不一致（显式否掉继承）** | `:1110-1112` |
| 一路到根都没有 | ⚠️ 看 `CONFIG_OF_DMA_DEFAULT_COHERENT` | `:1101` |

结果落到 `dev->dma_coherent`（`drivers/of/device.c:201`，见 03 §A2 第 8 步）。

### §3.2 arm64 上的默认是"不一致"

⚠️ **arm64 不 select `OF_DMA_DEFAULT_COHERENT`**：

```bash
grep -rn "OF_DMA_DEFAULT_COHERENT" arch/arm64/Kconfig    # 无输出
```

所以 `of_dma_is_coherent()` 的 `:1101` 初值是 `false`——**没写 `dma-coherent` 的设备一律被当成非一致设备**，每次 `dma_map_*` / `dma_sync_*` 都会做缓存维护（03 §B3），`dma_alloc_coherent()` 会建 uncached 的 vmap（03 §A5）。

⚠️ **这就是 `dma-noncoherent` 在 arm64 dts 里 0 次出现的原因**：默认已经是非一致，只有想在一个标了 `dma-coherent` 的父节点下面挖个洞才需要它。

### §3.3 写错的代价是不对称的

| 错误 | 后果 |
|---|---|
| 硬件不一致，dts 写了 `dma-coherent` | ⚠️ **静默数据损坏**：缓存维护全被跳过，CPU 和设备看到不同内容 |
| 硬件一致，dts 没写 `dma-coherent` | ⚠️ **只是慢**：多做了无用的 cache clean/invalidate，功能正确 |

⚠️ **所以不确定时一律不写。** 这条不对称性是 08 §2 的核心内容。

---

## §4 per-device 一致性池：`shared-dma-pool` 且没有 `reusable`

⚠️ **本节的标题在很多资料里写成"`shared-dma-pool` + `no-map`"，那在 arm64 上是错的。** `rmem_dma_setup()` 里检查 `no-map` 的那三行被 `#ifdef CONFIG_ARM` 包着（`kernel/dma/coherent.c:370-375`）——**只有 32 位 ARM 强制要求 `no-map`，arm64 根本不看这个属性**。判据：

```bash
sed -n '370,375p' kernel/dma/coherent.c        # 整段在 #ifdef CONFIG_ARM 里
grep -rn "config ARM$" arch/arm64/Kconfig      # 无输出：arm64 不是 CONFIG_ARM
```

**在 arm64 上，`shared-dma-pool` 只要没有 `reusable`，就一定变成 per-device 一致性池**，加不加 `no-map` 都一样能建起来。完整分流见 §4.3。

### §4.1 完整例子

真实例子（`arch/arm64/boot/dts/arm/fvp-base-revc.dts:101-113`）：

```dts
	reserved-memory {
		#address-cells = <2>;
		#size-cells = <2>;
		ranges;

		/* Chipselect 2,00000000 is physically at 0x18000000 */
		vram: vram@18000000 {
			/* 8 MB of designated video RAM */
			compatible = "shared-dma-pool";
			reg = <0x00000000 0x18000000 0 0x00800000>;
			no-map;
		};
	};
```

然后在设备节点里引用它：

```dts
	display@1c000000 {
		compatible = "...";
		memory-region = <&vram>;
	};
```

⚠️ **`reg` 和 `size` 二选一**：`reg` 是"就要这块固定地址"，`size` 是"随便给我一块这么大的"。这类池通常用 `reg`（地址是硬件约束）。

⚠️ **上面这个例子里的 `no-map` 在 arm64 上不是必需的**（§4 开头）。它写在这里是因为 `vram` 确实是一块不该被内核线性映射的外部存储——**语义正确，但不是"让池建起来"的条件**。

### §4.2 走通需要两步都对

| 步 | 谁做 | 失败表现 |
|---|---|---|
| 1. 池被建起来 | `rmem_dma_setup()`（`kernel/dma/coherent.c:363-389`），启动期 | 见 §9 的错误表 |
| 2. 设备被挂到池上 | `of_reserved_mem_device_init_by_idx()`（`drivers/of/of_reserved_mem.c:331-375`），probe 前 | ⚠️ **驱动必须自己调它**，不是自动的 |

⚠️ **第 2 步有一个巨大的陷阱**：`of_reserved_mem_device_init()` **不是 driver core 自动调的**，是驱动自己在 probe 里调的。全树只有 31 个文件调（04 §11）。**写了 `memory-region` 但驱动没调这个函数 → 属性完全无效，一句日志都没有。**

```bash
# 确认你的驱动到底调没调
grep -n "of_reserved_mem_device_init" drivers/foo/foo.c
```

⚠️ **唯一的例外是受限池**：`of_dma_set_restricted_buffer()` 由 `of_dma_configure_id()` 自动调（`drivers/of/device.c:220`），不需要驱动配合。见 §6。

### §4.3 `shared-dma-pool` 到底变成哪种池：决策树

⚠️ **这是本篇最重要的一节。** 同一个 compatible 被**两个** `RESERVEDMEM_OF_DECLARE` 注册，靠属性组合分流。**下面这棵树是 arm64 的形状**，和 32 位 ARM 不同（差别见 §4.4）：

```
reserved-memory 子节点 compatible = "shared-dma-pool"
│
├─ 有 reusable ?
│  │
│  ├─ 是 ─┬─ 同时有 no-map ?
│  │      │  ├─ 是 → ⚠️ rmem_cma_setup() 在 :413 拒绝（-EINVAL）
│  │      │  │        rmem_dma_setup() 在 :367 也拒绝（有 reusable）
│  │      │  │        → ⚠️⚠️ 两边都拒 —— 节点变成死字，见下面「死字分支」
│  │      │  └─ 否 ↓
│  │      │
│  │      └─ 有 linux,cma-default 且命令行给了 cma= ?
│  │         ├─ 是 → ⚠️ rmem_cma_setup() 在 :406-410 主动让位：
│  │         │        pr_info("Reserved memory: bypass %s node,
│  │         │                 using cmdline CMA params instead")
│  │         │        返回 -EBUSY → 这个节点不建区，命令行的参数生效（§5.2）
│  │         └─ 否 → 建 CMA 区（cma_init_reserved_mem() 在 :421）
│  │                 有 linux,cma-default → 同时设为全局默认区（:429-430）
│  │
│  └─ 否 → rmem_dma_setup()  (kernel/dma/coherent.c:363-389)
│          ⚠️ arm64 上这里没有任何属性门 —— :370-375 的 no-map 检查
│             被 #ifdef CONFIG_ARM 包着，arm64 编译不进去
│          → 一定建成 per-device 一致性池
│             成功消息 :386 "Reserved memory: created DMA memory pool at ..."
│          （linux,dma-default 那段 :377-383 也被 #ifdef CONFIG_DMA_GLOBAL_POOL
│            编译掉了，arm64 上写它无效）
│
└─ 「死字分支」：reusable + no-map 同时写
   · rmem_cma_setup()  :413 返回 -EINVAL（不接受带 no-map 的区）
   · rmem_dma_setup()  :367 返回 -EINVAL（不接受带 reusable 的区）
   → 内存被预留了但谁也用不上，唯一线索是
     drivers/of/of_reserved_mem.c:282 的 "node %s compatible matching fail"
```

⚠️ **arm64 上唯一会让节点变成死字的属性组合是 `reusable` + `no-map` 同时写。** 这两个属性语义本来就冲突：`reusable` 说"平时借给别人用，要的时候迁移出来"，`no-map` 说"内核连线性映射都别建"——**页不在内存管理器手里就没法迁移**。内核用两边各拒一次的方式挡住它，代价是**报错信息很不直白**（只有那句 `compatible matching fail`）。

⚠️ **不要照抄"arm64 也需要 `no-map`"这句话。** 它在 32 位 ARM 上成立，在 arm64 上不成立——`kernel/dma/coherent.c:371-373` 那句 `pr_err("Reserved memory: regions without no-map are not yet supported\n")` **在 arm64 内核里根本不存在**（被 `#ifdef CONFIG_ARM` 编译掉）。这是本篇修正的一条流行错误说法。

⚠️ **两个 setup 函数用的是同一个 compatible 字符串、不同的 `RESERVEDMEM_OF_DECLARE` 符号名**：`kernel/dma/contiguous.c:440` 是 `RESERVEDMEM_OF_DECLARE(cma, "shared-dma-pool", rmem_cma_setup)`，`kernel/dma/coherent.c:402` 是 `RESERVEDMEM_OF_DECLARE(dma, "shared-dma-pool", rmem_dma_setup)`。第一个参数只是 C 符号名，**compatible 是同一个字符串**——这就是为什么必须靠属性分流，而不是靠 compatible 区分。

⚠️ **两者的成功消息只差三个字母**，看 dmesg 时要盯准：

| 消息 | 出处 | 说明 |
|---|---|---|
| `Reserved memory: created CMA memory pool at %pa, size %ld MiB` | `kernel/dma/contiguous.c:435` | 走的是 CMA 分支 |
| `Reserved memory: created DMA memory pool at %pa, size %ld MiB` | `kernel/dma/coherent.c:386` | 走的是 per-device 一致性池分支 |
| `Reserved memory: created restricted DMA pool at %pa, size %ld MiB` | `kernel/dma/swiotlb.c:1106` | 受限池（§6） |

### §4.4 和 32 位 ARM 的差别

⚠️ **本文档集只覆盖 arm64**（99 §2.7），但这一条差别必须点出来，否则读者会把网上的 ARM32 结论搬过来：

| | 32 位 ARM（`CONFIG_ARM`） | arm64 |
|---|---|---|
| `shared-dma-pool` 无 `reusable` 无 `no-map` | ⚠️ **被拒**，`kernel/dma/coherent.c:372` 报 `regions without no-map are not yet supported` | ⚠️ **正常建池** |
| `no-map` 的作用 | 建池的**必要条件** | 只影响内核线性映射，**和建池无关** |

### §4.5 受限池和 IOMMU 的排除

见 §6.2——`restricted-dma-pool` 用的是独立 compatible，不参与上面这棵树的分流，但它有自己的一套排除规则和一条 IOMMU 静默失效路径。

---

## §5 全局 CMA 区

两种写法，效果相同：

### §5.1 dts 写法

真实例子（`arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts:62-68`）：

```dts
		/* global autoconfigured region for contiguous allocations */
		linux,cma {
			compatible = "shared-dma-pool";
			reusable;
			size = <0x00000000 0x08000000>;
			linux,cma-default;
		};
```

带 `alignment` 的版本（`arch/arm64/boot/dts/amlogic/meson-gx.dtsi:58-64`）：

```dts
		linux,cma {
			compatible = "shared-dma-pool";
			reusable;
			size = <0x0 0x10000000>;
			alignment = <0x0 0x400000>;
			linux,cma-default;
		};
```

⚠️ **节点名叫 `linux,cma` 只是惯例，起作用的是 `compatible` + `reusable` + `linux,cma-default` 这三样。** 改名不影响功能。

⚠️ **`linux,cma-default` 是把这个区设为全局默认区**（`kernel/dma/contiguous.c:429-430`）。不带它就只是一个匿名 CMA 区——**除了被 `memory-region` 明确引用，没人会用到它**。

### §5.2 命令行写法

```
cma=128M
cma=128M@0x40000000
cma=0            # 显式关掉
```

解析在 `kernel/dma/contiguous.c:77`（`early_cma`），注册在 `:96`。

### §5.2.1 ⚠️ 命令行赢，不是 dts 赢

两条路都想产出**同一个**全局默认区，仲裁写在 `rmem_cma_setup()` 的开头（`kernel/dma/contiguous.c:406-410`）：

```c
	if (size_cmdline != -1 && default_cma) {
		pr_info("Reserved memory: bypass %s node, using cmdline CMA params instead\n",
			rmem->name);
		return -EBUSY;
	}
```

⚠️ **`size_cmdline` 的初值是 `-1`**（`kernel/dma/contiguous.c:73`，`static phys_addr_t size_cmdline __initdata = -1`），所以"不等于 -1"就是"命令行给了 `cma=`"。此时带 `linux,cma-default` 的 dts 节点**主动让位**：返回 `-EBUSY`，不建区，`dma_contiguous_default_area` 保持 NULL；随后 `dma_contiguous_reserve()` 的 `:193` 条件 `if (selected_size && !dma_contiguous_default_area)` 成立，按命令行的大小建区。

| 场景 | 谁建了全局默认区 |
|---|---|
| 只有 dts `linux,cma-default` | dts（`kernel/dma/contiguous.c:429-430`） |
| 只有命令行 `cma=` | 命令行（`kernel/dma/contiguous.c:193-201`） |
| ⚠️ **两个都有** | ⚠️ **命令行**，dts 节点被 bypass 并打印 `bypass %s node, using cmdline CMA params instead` |
| 都没有 | 编译期默认（`CMA_SIZE_MBYTES` / `CMA_SIZE_PERCENTAGE`），见 04 §3.2 |

⚠️ **调试时看到 `bypass ... using cmdline CMA params instead` 不是错误**，是仲裁生效的证据——**但它同时意味着 dts 里写的 `size` / `alignment` / `reg` 全部被丢掉了**。想让 dts 生效就必须从命令行里去掉 `cma=`。

⚠️ **这条仲裁只对带 `linux,cma-default` 的节点生效。** 不带这个属性的 `shared-dma-pool` + `reusable` 节点（被 `memory-region` 明确引用的那种）永远不受命令行影响。

### §5.3 per-NUMA CMA

只有命令行一条路：

```
cma_pernuma=64M
```

解析在 `kernel/dma/contiguous.c:103`（`early_cma_pernuma`），注册在 `:108`。⚠️ **没有对应的 dts 属性。** `DMA_PERNUMA_CMA` 在 arm64 NUMA 机器上默认开（`kernel/dma/Kconfig:136`），但不给参数就不预留任何东西（04 §3.3）。

---

## §6 受限 DMA 池

```dts
	reserved-memory {
		restricted_dma_reserved: restricted_dma_reserved {
			compatible = "restricted-dma-pool";
			size = <0x0 0x4000000>;
		};
	};

	/* 设备节点 */
	mmc@11230000 {
		compatible = "...";
		memory-region = <&restricted_dma_reserved>;
	};
```

真实例子：`arch/arm64/boot/dts/mediatek/mt8192-asurada.dtsi:115`（**全树唯一**）。

### §6.1 四个不能写的属性

`rmem_swiotlb_setup()`（`kernel/dma/swiotlb.c:1095-1109`）看到下面任一个就返 `-EINVAL`（04 §2.2）：`reusable`（`:1099`）· `linux,cma-default`（`:1100`）· `linux,dma-default`（`:1101`）· `no-map`（`:1102`）。

⚠️ **`no-map` 被明确拒绝**，和 §4 的 per-device 池正好相反。原因是受限池需要内核能访问这块内存来做 bounce 搬运。

### §6.2 和 IOMMU 互斥

⚠️ **`of_dma_configure_id()` 只在设备没有 IOMMU 时才调 `of_dma_set_restricted_buffer()`**（`drivers/of/device.c:220`，见 03 §A2 第 10 步）。所以：

| 设备节点有 `iommus` | `memory-region` 指向受限池 | 结果 |
|---|---|---|
| 否 | 是 | ⚠️ 受限池生效 |
| ⚠️ **是** | 是 | ⚠️ **受限池被静默忽略**，走 IOMMU 路径 |

**这是完全静默的**——没有任何日志说"你的受限池被 IOMMU 顶掉了"。这条是 §8 定位章里最难查的一类。

### §6.3 只允许一个

`of_dma_set_restricted_buffer()` 在 `drivers/of/device.c:86` 遇到第一个匹配的就 `break`（注释 `:80-81` 明确说"只允许一个"）。⚠️ **写多个 `restricted-dma-pool` 不报错，第二个及以后被静默忽略。**

---

## §7 不走 DT 的两条路

### §7.1 PCI 设备

PCI 设备的 DMA 参数来自 PCI 层，不来自自己的 DT 节点（PCI 设备通常没有 DT 节点）。⚠️ **但 PCI 控制器的 DT 节点上的 `dma-ranges` 会影响挂在它下面的所有 PCI 设备**——这条继承是 PCI 层实现的，不属本子系统。

### §7.2 驱动硬编码

驱动在 probe 里调 `dma_set_mask_and_coherent()`（05 §1）是**在 DT 之后叠加**的：`of_dma_configure_id()` 先按 `dma-ranges` 定一个上限（`drivers/of/device.c:192-194`），驱动再往里收。⚠️ **驱动设不宽于 DT 给的上限**——`dma_set_mask()` 里的 `dma_supported()` 会挡住（03 §A4）。

---

## §8 配错了怎么定位

### §8.1 编译期

| 检查 | 命令 |
|---|---|
| dtb 里属性真的在不在 | `dtc -I dtb -O dts arch/arm64/boot/dts/vendor/board.dtb \| grep -n "dma-coherent\|dma-ranges\|memory-region"` |
| `shared-dma-pool` 属性组合对不对 | 照 §4.3 的决策树逐行走一遍 |
| 相关 Kconfig 开没开 | `grep -E "DMA_CMA\|DMA_RESTRICTED_POOL\|DMA_DECLARE_COHERENT\|DMA_DIRECT_REMAP" .config` |

### §8.2 运行期静态（一次性，启动后就定了）

| 想确认 | 看哪里 |
|---|---|
| 池建起来了没有 | `dmesg \| grep -i "reserved memory"` —— 见 §9 的消息表 |
| CMA 区多大 | `dmesg \| grep -i cma`；`/proc/meminfo` 的 `CmaTotal` / `CmaFree` |
| swiotlb 池分配了没有 | `dmesg \| grep -i swiotlb` |
| 某个设备的 DMA 参数 | `/sys/kernel/debug/devices_deferred`（probe 失败时）；具体字段见 07 §3 |

### §8.3 运行期动态

| 想确认 | 怎么做 | 详见 |
|---|---|---|
| 有没有在 bounce | swiotlb 的 debugfs 计数 | 07 §3 |
| 映射有没有配平 | 开 `DMA_API_DEBUG` | 07 §2 |
| 具体每次映射的地址 | tracepoint | 07 §5 |

### §8.4 症状清单，按坏掉的范围分组

**范围：整个板子**

| 症状 | 最可能的原因 |
|---|---|
| 启动时大量 `dma_alloc_coherent` 失败 | CMA 区没建起来 / 太小 → §5 |
| dmesg 里 swiotlb 池是 0 | ⚠️ **正常**：`max_pfn <= arm64_dma_phys_limit` 时不分配（03 §A1） |

**范围：单个设备**

| 症状 | 最可能的原因 |
|---|---|
| 设备 DMA 数据错乱、随机 | ⚠️ 错标了 `dma-coherent` → §3.3 |
| 设备只能 DMA 到低地址 | `dma-ranges` 窄化了掩码 → §2.2 |
| `memory-region` 写了但设备没用那块内存 | ⚠️ **驱动没调 `of_reserved_mem_device_init()`** → §4.2 |
| 受限池没生效 | ⚠️ 设备节点有 `iommus` → §6.2 |

**范围：单个属性**

| 症状 | 最可能的原因 |
|---|---|
| `dma-ranges` 好像没生效，dmesg 无提示 | ⚠️ 写在了设备自己节点上而不是父节点 → §2.1；`pr_debug` 默认不显示 → §2.3 陷阱 2 |
| reserved-memory 节点建了但没池 | ⚠️ `reusable` 和 `no-map` 同时写了 → §4.3 死字分支 |
| dts 里的 CMA 大小/地址被忽略 | ⚠️ 命令行有 `cma=` 顶掉了 → §5.2.1 |

**范围：拓扑**

| 症状 | 最可能的原因 |
|---|---|
| 父节点写了 `dma-coherent`，子设备还是走缓存维护 | 中间某层写了 `dma-noncoherent` → §3.1 |
| 两个 reserved-memory 区重叠 | `drivers/of/of_reserved_mem.c:244` 的 `OVERLAP DETECTED!` |

---

## §9 错误消息 → 原因映射表

| 消息（逐字） | 出处 | 原因 |
|---|---|---|
| `translation of DMA address(%llx) to CPU address failed node(%pOF)` | `drivers/of/address.c:1006`（`pr_err`） | `dma-ranges` 的 cells 数或数值写错，无法翻译 |
| `no dma-ranges found for node(%pOF)` | `drivers/of/address.c:998`（⚠️ **`pr_debug`**） | 没有 `dma-ranges`——**通常正常**，但也可能是写错了节点位置 |
| `Invalid size 0x%llx for dma-range(s)` | `drivers/of/device.c:159`（`dev_warn`） | `dma-ranges` 的长度是奇数，内核擅自加一 → §2.3 陷阱 3 |
| `Adjusted size 0x%llx invalid` | `drivers/of/device.c:165`（`dev_err`） | 上一条加一之后变成 0 → 返 `-EINVAL` |
| `DMA mask not set` | `drivers/of/device.c:178`（`dev_warn`） | 创建这个设备的总线代码没初始化 `dev->dma_mask`，核心把它指向 `coherent_dma_mask` 兜底（注释 `:171-176`） |
| `Reserved memory: regions without no-map are not yet supported` | `kernel/dma/coherent.c:372`（`pr_err`） | ⚠️ **arm64 上看不到这条**：整段在 `#ifdef CONFIG_ARM` 里（`:370-375`）→ §4.4 |
| `Reserved memory: created DMA memory pool at %pa, size %ld MiB` | `kernel/dma/coherent.c:386`（`pr_info`） | ⚠️ **成功**：per-device 一致性池建好 |
| `Reserved memory: created CMA memory pool at %pa, size %ld MiB` | `kernel/dma/contiguous.c:435`（`pr_info`） | ⚠️ **成功**：CMA 区建好 |
| `Reserved memory: bypass %s node, using cmdline CMA params instead` | `kernel/dma/contiguous.c:407`（`pr_info`） | ⚠️ dts 的 `linux,cma-default` 节点让位给命令行 `cma=` → §5.2.1 |
| `Reserved memory: incorrect alignment of CMA region` | `kernel/dma/contiguous.c:417`（`pr_err`） | `base` 或 `size` 不满足 `CMA_MIN_ALIGNMENT_BYTES` |
| `Reserved memory: unable to setup CMA region` | `kernel/dma/contiguous.c:423`（`pr_err`） | `cma_init_reserved_mem()` 失败（区数超上限 / 参数非法） |
| `Reserved memory: created restricted DMA pool at %pa, size %ld MiB` | `kernel/dma/swiotlb.c:1106`（`pr_info`） | ⚠️ **成功**消息，不是错误 |
| `failed to initialise "restricted-dma-pool" memory node` | `drivers/of/device.c:96`（`dev_warn`） | 受限池节点找到了但 `device_init` 失败（如 highmem，`kernel/dma/swiotlb.c:1039`） |
| `DMA: default coherent area is set` | `kernel/dma/coherent.c:319`（`pr_info`） | 全局池建好——arm64 上看不到（04 §5.3） |
| `invalid size property in '%s' node.` | `drivers/of/of_reserved_mem.c:101`（`pr_err`） | reserved-memory 子节点的 `size` 格子数不对 |
| `invalid alignment property in '%s' node.` | `drivers/of/of_reserved_mem.c:109`（`pr_err`） | `alignment` 格子数不对 |
| `invalid alloc-ranges property in '%s', skipping node.` | `drivers/of/of_reserved_mem.c:129`（`pr_err`） | `alloc-ranges` 格子数不对 |
| `failed to allocate memory for node '%s': size %lu MiB` | `drivers/of/of_reserved_mem.c:161`（`pr_err`） | 要的太大 / `alloc-ranges` 范围内没有空闲 |
| `not enough space for all defined regions.` | `drivers/of/of_reserved_mem.c:68`（`pr_err`） | reserved-memory 子节点数超过编译期上限 |
| `OVERLAP DETECTED!\n%s (%pa--%pa) overlaps with %s (%pa--%pa)` | `drivers/of/of_reserved_mem.c:244`（`pr_err`） | 两个 reserved-memory 区地址重叠 |
| `initialized node %s, compatible id %s` | `drivers/of/of_reserved_mem.c:193`（`pr_info`） | ⚠️ **成功**消息：某个 `RESERVEDMEM_OF_DECLARE` 收下了这个节点 |
| `node %s compatible matching fail` | `drivers/of/of_reserved_mem.c:282`（`pr_info`） | ⚠️ 没有任何 setup 函数收下这个节点——**§4.3 死字分支的第二条线索** |
| `rejecting DMA map of vmalloc memory` | `include/linux/dma-mapping.h:333`（`dev_WARN_ONCE`） | 驱动问题，不是 dts 问题 → 05 §3.1 |

⚠️ **`initialized node %s, compatible id %s` 和 `node %s compatible matching fail` 这一对是判断 reserved-memory 节点有没有被收下的唯一可靠依据。** 前者出现 = 有 setup 函数认领；后者出现 = 内存预留了但没人用。

---

## §10 相关绑定文档

| 文件 | 内容 |
|---|---|
| `Documentation/devicetree/bindings/reserved-memory/reserved-memory.yaml` | 通用 reserved-memory 骨架 |
| `Documentation/devicetree/bindings/reserved-memory/shared-dma-pool.yaml` | ⚠️ **`shared-dma-pool` 的正式绑定** |
| `Documentation/devicetree/bindings/reserved-memory/memory-region.yaml` | `memory-region` 属性 |
| `Documentation/devicetree/bindings/reserved-memory/reserved-memory.txt` | 老版文本绑定，仍在树里 |

⚠️ **`restricted-dma-pool` 在 `Documentation/devicetree/bindings/reserved-memory/` 下没有独立的 yaml 文件。**

```bash
ls Documentation/devicetree/bindings/reserved-memory/ | grep restricted    # 空
```

**它的唯一权威定义是代码**（`kernel/dma/swiotlb.c:1095-1111`）。这解释了为什么全树只有一个板子用它。

⚠️ **`dma-ranges` / `dma-coherent` 不属于任何一个子系统的绑定文件**，它们定义在 DT 规范本体和 `Documentation/devicetree/bindings/` 的通用部分里——**这也是它们容易被写错的原因：没有 schema 校验兜底。**

---

## §11 导航

| 想知道 | 去 |
|---|---|
| 这些属性在启动期什么时候被读 | 03 §A1 §A2 |
| 池的内部结构 | 02 §6 |
| 每种池的完整行为 | 04 |
| 驱动侧怎么配合（`of_reserved_mem_device_init()` 调在哪） | 05 §0 |
| 属性生效后怎么在运行期确认 | 07 §3 |
| 为什么 `dma-coherent` 写错一个方向致命另一个方向只是慢 | 08 §2 |
