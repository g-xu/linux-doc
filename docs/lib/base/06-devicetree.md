---
title: 设备树
---

# drivers/base 设备树篇：节点怎么变成 struct device

## §0 本篇的位置

### §0.1 一句话结论：驱动模型核心自己一个 DT 属性都不读

先把这件事讲清楚，否则后面每一节都会让人困惑：`drivers/base/` 目录下**没有任何一处读设备树属性名的代码**。

```
grep -rnoE '"(compatible|status|reg|interrupts|dma-coherent|dma-ranges|iommus|msi-parent)"' drivers/base/*.c
```

这条命令在 6.1.158 上只有两条命中，都在 `drivers/base/component.c:90` 和 `drivers/base/component.c:95`，而且它们是 debugfs 表格的列标题（`seq_printf` 打印 `"aggregate_device name"` / `"status"` 两列表头），跟设备树毫无关系。

真正读 DT 的是 `drivers/of/` 这层胶水。它把节点翻译成三样东西，然后交给 `drivers/base/`：

| 胶水层读出来的 | 交给驱动模型的形态 | 落点 |
| --- | --- | --- |
| `reg` | `struct resource` 数组 | `pdev->resource` / `pdev->num_resources` |
| 节点本身 | `dev->of_node` + `dev->fwnode` | 后续所有 `of_*` / `device_property_*` 查询的起点 |
| phandle 类属性（`clocks`、`power-domains`…） | `struct device_link` | `dev->links.suppliers` / `.consumers` |

所以本篇的组织方式不是"base 支持哪些绑定"，而是**"一个 DT 节点从被扫到、变成 platform_device、绑上驱动，中间每一步读了什么属性、哪一步会静默失败"**。行号大量落在 `drivers/of/`，这是内容本身的形状，不是跑题。

### §0.2 发现路径分诊表

不要先问"这个子系统有没有 DT 绑定"。驱动模型是所有发现路径的汇聚点，DT 只是其中一条。

| 发现路径 | 谁扫描 | 走不走 DT | arm64 用不用 | 本篇覆盖到哪 |
| --- | --- | --- | --- | --- |
| DT 节点 → `platform_device` | `of_platform_default_populate_init()`，`drivers/of/platform.c:517` | 是 | 主路径 | 全篇 |
| DT 节点 → `amba_device` | `of_amba_device_create()`，`drivers/of/platform.c:220` | 是 | 用（`arch/arm64/Kconfig:106` 无条件 `select ARM_AMBA`） | §6 |
| ACPI 表 → `platform_device` | `drivers/acpi/` | 否 | 本文档集按 arm64-only 裁掉 | 不覆盖，见 `99-evidence.md` §2.7 |
| 板级代码手工 `platform_device_register*()` | 驱动/平台代码自己 | 否 | 少量（`bootx-noscreen` 那类只在 PPC 分支） | `05-usage.md` §2 |
| MFD / 父驱动在 probe 里造子设备 | 父驱动 | 部分（子节点可能有 DT 节点） | 用 | §5.3 只讲"父驱动为什么要自己造" |
| `component` 聚合 | `drivers/base/component.c` | 否（靠 match 函数） | 用 | `04-extensions.md` §1 |
| auxiliary bus | `drivers/base/auxiliary.c` | 否 | 用 | `04-extensions.md` §2 |
| software node（代码里造 fwnode） | `drivers/base/swnode.c` | 否，但走同一套 `fwnode` 接口 | 少见 | `04-extensions.md` §3 |
| sysfs 手工 bind/unbind | 用户态 | 否 | 调试用 | `07-debug.md` |

### §0.3 按任务找章节

| 我想干的事 | 去 |
| --- | --- |
| 搞明白我写的节点为什么没变成设备 | §2、§5、§13 |
| 搞明白 `/sys/devices/platform/` 里那些 `1436400.pmu` 名字是怎么来的 | §3.2 |
| 搞明白为什么 `platform_get_resource(pdev, IORESOURCE_IRQ, 0)` 拿不到中断 | §4 |
| 让父节点的子节点也变成 platform_device | §5.1 |
| 搞明白 `-EPROBE_DEFER` 循环和 `fw_devlink` 的关系 | §7 |
| 给一个 DT 节点改名字或塞 `platform_data` | §9 |
| 从零看一遍完整例子 | §10 |
| 查错误消息 | §12 |

## §1 核心真正会读的属性总表

### §1.1 总表

这张表只收"从 DT 节点变成 struct device 这条路上被读的属性"。子系统专属属性（`#clock-cells` 的具体含义、`pinctrl-0` 里 phandle 指向什么）不在本篇范围。

| 属性 | 写在哪个节点 | 谁读 | 读出来变成什么 | 详见 |
| --- | --- | --- | --- | --- |
| `compatible` | 设备节点 | `of_platform_bus_create()`（`drivers/of/platform.c:355-360`）判断是否算设备；`of_device_is_compatible()`（`drivers/of/base.c:547`）做匹配 | 决定"这个节点算不算设备"以及"绑哪个驱动" | §2.1 |
| `status` | 设备节点 | `__of_device_is_available()`，`drivers/of/base.c:612` | `"disabled"` 的节点根本不会被创建 | §2.2 |
| `reg` | 设备节点 | `of_address_to_resource()`，在 `of_device_alloc()` 里循环调用（`drivers/of/platform.c:124-125` 数个数、`:137-141` 填表） | `IORESOURCE_MEM` 资源数组 | §3.1 |
| `reg`（第二用途） | 设备节点 | `of_device_make_bus_id()`，`drivers/of/platform.c:86-87` | 设备名字的前缀（翻译后的物理地址） | §3.2 |
| `mask` | 设备节点 | `of_device_make_bus_id()`，`drivers/of/platform.c:88` | 名字里多一段 `.%x`（`ffs(mask)-1`） | §3.2 |
| `#address-cells` / `#size-cells` / `ranges` | 父节点 | `of_translate_address()` 经由 `of_address_to_resource()` | 决定 `reg` 能不能翻译成 CPU 物理地址 | §3.3 |
| `interrupts` / `interrupts-extended` | 设备节点 | 不在建设备时读。`platform_get_irq_optional()` 里的 `of_irq_get()`（`drivers/base/platform.c:184`）按需解析 | 一个 Linux virq 号，不是 resource | §4 |
| `interrupt-parent` | 设备节点或祖先 | `parse_interrupt_parent()`，登记在 `drivers/of/property.c:1249` | fw_devlink 的一条供方依赖 | §7 |
| `arm,primecell-periphid` | 设备节点 | `of_amba_device_create()`，`drivers/of/platform.c:254-256` | `amba_device.periphid`，覆盖硬件自报的 ID | §6 |
| `dma-coherent` | 设备节点 | `of_dma_is_coherent()`，经 `of_dma_configure_id()`（`drivers/of/device.c:201`） | 决定这个设备用一致性还是非一致性 DMA ops | §8.1 |
| `dma-ranges` | 父节点 | `of_dma_configure_id()`，`drivers/of/device.c:195` 一带 | `dev->dma_range_map` + 总线地址上限 | §8.1 |
| `iommus` / `iommu-map` | 设备节点 / 父节点 | `of_dma_configure_id()` 内部走 `of_iommu_configure()` | 挂上 IOMMU 域；也是 fw_devlink 的可选供方（`drivers/of/property.c:1246`） | §8.2 |
| `msi-parent` | 设备节点 | `of_msi_configure()`，`drivers/of/irq.c:753`，由 `drivers/of/platform.c:187` 调 | `dev->msi.domain` | §8.3 |
| 34 条 phandle 属性 | 设备节点 | `of_supplier_bindings[]`，`drivers/of/property.c:1341` | 每条一个 `struct device_link` | §7 |

### §1.2 三方分工

同一个节点的属性被三层代码分掉，搞混了就会去错的地方找 bug：

| 层 | 读什么 | 代码在 |
| --- | --- | --- |
| 通用 DT + 驱动模型 | `compatible` / `status` / `reg` / 地址翻译相关 / 34 条 phandle 属性 / DMA 与 MSI 配置 | `drivers/of/`，翻译结果交给 `drivers/base/` |
| 子系统核心 | 属性的语义（`#clock-cells` 有几个 cell、`pinctrl-0` 指向的配置怎么用） | `drivers/clk/`、`drivers/pinctrl/` … |
| 具体控制器驱动 | 厂商私有属性 | 驱动自己 |

判据很简单：**如果一个属性的名字在 `drivers/of/` 里 grep 不到，那它就是子系统或驱动私有的**，驱动模型这一层完全不知道它存在，写错了也不会有任何来自核心的报错。

## §2 compatible 与 status：节点变不变成设备

### §2.1 compatible 决定"算不算设备"

`of_platform_bus_create()` 的第一道门（`drivers/of/platform.c:355-360`）：

```c
	/* Make sure it has a compatible property */
	if (strict && (!of_get_property(bus, "compatible", NULL))) {
		pr_debug("%s() - skipping %pOF, no compatible prop\n",
			 __func__, bus);
		return 0;
	}
```

`strict` 从哪来？`of_platform_populate()` 在 `drivers/of/platform.c:482` 那一层调用时传的是 `true`。所以默认路径下：

- 没有 `compatible` 的节点不会变成设备，只有一条 `pr_debug`——**默认内核配置下你什么都看不到**
- `/chosen`、`/aliases`、`/memory` 这类信息节点正是靠这条规则被排除的

`Documentation/devicetree/usage-model.rst` 的 §2.4 把这条设计意图讲得很直接：内核从树根开始找带 `compatible` 的节点，"一般假定任何带 `compatible` 属性的节点都代表某种设备"。这一段还解释了为什么根节点下的设备一律做成 `platform_device`：除了 platform（和 amba）以外，几乎所有 `bus_type` 都假定自己的设备是某个总线控制器的孩子，只有 platform 设备可以直接挂在 `/sys/devices` 的根上。

### §2.2 status 决定"可用不可用"

判定函数 `__of_device_is_available()` 在 `drivers/of/base.c:612`：

- 读 `"status"`（`drivers/of/base.c:620`）
- 没有这个属性 → 返回可用（`drivers/of/base.c:621-622`）。这是最常被忘掉的一条：**不写 `status` 等于 `okay`**
- 有属性时只认 `"okay"` 和 `"ok"`（`drivers/of/base.c:625`），其余一切值（包括拼错的 `"enabled"`）都算不可用

另有一个 `__of_device_is_fail()`（`drivers/of/base.c:661`），认的是 `"fail"` 和 `"fail-"` 前缀（`drivers/of/base.c:672`）。它和"可用"是两个独立判断，不要以为 `status` 只有可用/不可用两态。

拦截点在 `of_platform_device_create_pdata()`（`drivers/of/platform.c:176-178`）：

```c
	if (!of_device_is_available(np) ||
	    of_node_test_and_set_flag(np, OF_POPULATED))
		return NULL;
```

⚠️ 这个函数返回 `NULL` 时**没有任何日志**。`status = "disabled"` 的节点和"节点不存在"在 dmesg 里完全一样，都是彻底安静。定位办法见 §11.2。

### §2.3 OF_POPULATED 与 OF_POPULATED_BUS

两个 flag 定义在 `include/linux/of.h:147`（`OF_POPULATED`，"device already created"）和 `include/linux/of.h:148`（`OF_POPULATED_BUS`，"platform bus created for children"）。

| flag | 打在谁身上 | 作用 |
| --- | --- | --- |
| `OF_POPULATED` | 已经建过设备的节点 | 二次 populate 时跳过（`drivers/of/platform.c:177`、`:232`） |
| `OF_POPULATED_BUS` | 已经下钻过的容器节点 | 二次下钻时跳过（`drivers/of/platform.c:367-370`，设置在 `:401` 和 `:490`） |

创建失败时要把 `OF_POPULATED` 清掉，否则这个节点永远建不起来了。两条清理路径：`drivers/of/platform.c:197-198`（platform 路线）和 `drivers/of/platform.c:277`（amba 路线）。

## §3 reg：既变成资源，也变成设备名字

### §3.1 变成资源

`of_device_alloc()`（`drivers/of/platform.c:112-153`，导出在 `:155`）分两趟：

1. 先数（`drivers/of/platform.c:124-125`）：`while (of_address_to_resource(np, num_reg, &temp_res) == 0) num_reg++;`
2. 再 `kcalloc` 一个正好这么大的数组（`drivers/of/platform.c:128`），逐个填（`drivers/of/platform.c:137-141`）

填表那个循环里是 `WARN_ON(rc)`——第一趟能数出来的第二趟不该失败，失败就是内部不一致。

然后是三行赋值，这是"DT 世界"和"驱动模型世界"真正缝在一起的地方：

```c
	dev->dev.of_node = of_node_get(np);
	dev->dev.fwnode = &np->fwnode;
	dev->dev.parent = parent ? : &platform_bus;
```

分别在 `drivers/of/platform.c:143`、`:144`、`:145`。⚠️ 注意 `of_node_get()` —— 设备持有节点的一个引用，所以 `dev->of_node` 在设备活着期间一定有效。

⚠️ **`of_device_alloc()` 只填地址资源，不填中断资源**。这是本篇最容易被误解的一点，见 §4。

### §3.2 变成设备名字

`of_device_make_bus_id()`（`drivers/of/platform.c:74-105`，kerneldoc 在 `:67-73`）解释了 `/sys/devices/platform/` 里那些名字的来历。它从本节点往上走，第一个能翻译出地址的层级就定名：

| 条件 | 名字格式 | 代码 |
| --- | --- | --- |
| `reg` 能翻译出地址，且有 `mask` 属性 | `%llx.%x.%pOFn`（地址 . `ffs(mask)-1` . 节点名） | `drivers/of/platform.c:88-90` |
| `reg` 能翻译出地址，没有 `mask` | `%llx.%pOFn`（地址 . 节点名） | `drivers/of/platform.c:93-94` |
| 翻译不出来 | 用节点全名的 basename，然后爬到父节点再试 | `drivers/of/platform.c:101-102` |

所以 `pmu@1436400` 这个节点最后叫 `1436400.pmu`：**前缀是 `of_translate_address()` 翻译后的地址，不是节点名里 `@` 后面那串字符**。两者在正常的 DTS 里应该一致，但如果父节点的 `ranges` 做了地址搬移，`@` 后面的数字和最终设备名就会不一样——这时候按 `@` 后面的数字去 `/sys/devices/platform/` 里找就会找不到。

kerneldoc 里那句"prepend names from parent nodes until a unique name can be derived"说明了 `while (node->parent)` 这个循环的目的：靠父节点名拼出唯一名。

### §3.3 地址翻译依赖父节点

`of_address_to_resource()` 能不能成功，取决于从本节点到根这一路上的 `#address-cells` / `#size-cells` / `ranges`。三条实践结论：

- 容器节点漏写 `ranges`（哪怕是空的 `ranges;`）会让子节点的地址翻译失败。后果不是报错，而是设备名退化成节点名（§3.2 第三行）**并且资源数组为空**
- `#address-cells` 写错会让 `reg` 里的数字被切错，翻译出一个能用但完全错误的地址。这一类不会有任何来自驱动模型的报错，只会在驱动 `ioremap` 之后访问寄存器时表现为读回全 0 或直接挂
- `usage-model.rst` §2.4 的 Tegra 例子里 `soc` 节点写了 `ranges;`（空值，表示 1:1 映射），这是最常见的写法

## §4 interrupts：为什么不在 resource 表里

这是 DT 路线和板级代码路线最大的行为差异。

板级代码用 `platform_device_add_resources()` 时，`IORESOURCE_IRQ` 和 `IORESOURCE_MEM` 是并列填进同一张表的。DT 路线不是这样：`of_device_alloc()` 只处理地址（§3.1），中断留到**第一次有人问**的时候才解析。

解析点在 `platform_get_irq_optional()`（`drivers/base/platform.c:171`）：

```c
	if (IS_ENABLED(CONFIG_OF_IRQ) && dev->dev.of_node) {
		ret = of_irq_get(dev->dev.of_node, num);
		if (ret > 0 || ret == -EPROBE_DEFER)
			goto out;
	}

	r = platform_get_resource(dev, IORESOURCE_IRQ, num);
```

`of_irq_get()` 在 `drivers/base/platform.c:184`，DT 分支整体是 `:183-187`；只有 DT 分支没给出答案时才回退到 resource 表（`drivers/base/platform.c:189`）。

由此得出四条必须记住的结论：

| 结论 | 依据 |
| --- | --- |
| DT 设备上 `platform_get_resource(pdev, IORESOURCE_IRQ, 0)` 通常返回 `NULL` | 资源表里压根没有 IRQ 条目（`drivers/of/platform.c:112-153` 只填地址） |
| 拿中断必须走 `platform_get_irq()` 或 `platform_get_irq_byname()` | 只有它们会调 `of_irq_get()` |
| 中断控制器还没 probe 时会返回 `-EPROBE_DEFER` | `drivers/base/platform.c:185` 明确把这个值直接放行 |
| 没有中断时返回 `-ENXIO` | `drivers/base/platform.c:233-234` 的 `out_not_found` 分支 |

⚠️ 还有一条边界：`if (WARN(!ret, "0 is an invalid IRQ number\n"))` 在 `drivers/base/platform.c:236`，把 irq 0 当作非法并转成 `-EINVAL`。同一句话在 `drivers/base/platform.c:452` 又出现了一次（`platform_get_irq_byname()` 那条路），所以 dmesg 里看到这行不能直接推断是哪个调用者——两处逐字相同。

`platform_get_irq()` 与 `platform_get_irq_optional()` 的区别只有一条：前者失败时自己调 `dev_err_probe()`（`drivers/base/platform.c:265-266`），后者一声不响。近邻符号辨析见 `05-usage.md` §10。

## §5 递归下钻：simple-bus 家族与被跳过的节点

### §5.1 谁的孩子会被继续扫

`of_platform_populate()`（`drivers/of/platform.c:465-494`）只遍历 `root` 的**直接子节点**。要让孙子节点也变成设备，得靠 `of_platform_bus_create()` 末尾的递归（`drivers/of/platform.c:393-400`），而递归的门槛是：

```c
	dev = of_platform_device_create_pdata(bus, bus_id, platform_data, parent);
	if (!dev || !of_match_node(matches, bus))
		return 0;
```

在 `drivers/of/platform.c:389-391`。`matches` 就是传给 `of_platform_populate()` 的第二个参数。默认路径传的是 `of_default_bus_match_table[]`（`drivers/of/platform.c:24-32`）：

| compatible | 行 | 条件 |
| --- | --- | --- |
| `simple-bus` | `drivers/of/platform.c:25` | 无 |
| `simple-mfd` | `drivers/of/platform.c:26` | 无 |
| `isa` | `drivers/of/platform.c:27` | 无 |
| `arm,amba-bus` | `drivers/of/platform.c:29` | `CONFIG_ARM_AMBA`（`drivers/of/platform.c:28` 的 `#ifdef`） |

所以：**一个容器节点想让自己的孩子也被自动扫成 platform_device，它自己的 `compatible` 里必须带上这四个之一**。写了 `compatible = "vendor,my-bus"` 但没带 `"simple-bus"` 的话，容器本身会变成设备，孩子一个都不会有——而且没有任何报错。

arm64 上这四条全都生效（`arch/arm64/Kconfig:106` 无条件 `select ARM_AMBA`）。

`Documentation/devicetree/usage-model.rst` 在这里给了设计理由：`simple-bus` 在 Devicetree 规范里就是"简单内存映射总线"的意思，代码本可以硬编码这个判断，但仍然做成参数传进来，是为了让板级代码能覆盖默认行为。

### §5.2 被显式跳过的节点

`of_skipped_node_table[]` 在 `drivers/of/platform.c:34-37`，只有一条：`operating-points-v2`（`drivers/of/platform.c:35`）。拦截点在 `drivers/of/platform.c:362-365`。

这条规则的意义：OPP 表是以子节点形式挂在设备下面的，节点带 `compatible = "operating-points-v2"`，如果不特判就会凭 §2.1 的规则被造成一个毫无意义的 platform_device。§10 的实例里正好有一个。

`reserved_mem_matches[]`（`drivers/of/platform.c:506-515`）是反方向的白名单：`/reserved-memory` 下面的节点默认不建设备，只有这 7 个 compatible（`phram`、`qcom,rmtfs-mem`、`qcom,cmd-db`、`qcom,smem`、`ramoops`、`nvmem-rmem`、`google,open-dice`）例外，在 `drivers/of/platform.c:585` 显式创建。

### §5.3 arm64 上的完整启动顺序

`of_platform_default_populate_init()`（`drivers/of/platform.c:517`，注册为 `arch_initcall_sync`，见 `drivers/of/platform.c:604`）在非 PPC 分支上按这个顺序动作：

| 顺序 | 动作 | 行 |
| --- | --- | --- |
| 1 | 暂停 `sync_state()` 派发 | `drivers/of/platform.c:521` |
| 2 | `reserved_mem_matches[]` 白名单里的节点 | `drivers/of/platform.c:585` |
| 3 | `/firmware` 子树整棵 populate | `drivers/of/platform.c:588` |
| 4 | `/chosen` 下的 `simple-framebuffer` | `drivers/of/platform.c:594` |
| 5 | 其余全部（`of_platform_default_populate(NULL, NULL, NULL)`） | `drivers/of/platform.c:599` |

配对的恢复动作在另一个 initcall 里：`of_platform_sync_state_init()`（`drivers/of/platform.c:606-610`）注册为 `late_initcall_sync`（`drivers/of/platform.c:611`），调 `device_links_supplier_sync_state_resume()`（`drivers/of/platform.c:608`）。

⚠️ 这一对 pause/resume 跨了整个 initcall 阶段，是 `sync_state()` 回调"为什么在 `late_initcall_sync` 之后才集中触发"的原因。`sync_state()` 的语义见 `03-internals.md` 的 Part A 与 `05-usage.md` §7.4。

`of_platform_populate()` 自己内部也有一对更短的 pause/resume（`drivers/of/platform.c:480` 和 `:488`），保证一次 populate 期间不会有半成品拓扑触发 `sync_state()`。

⚠️ `usage-model.rst` 通篇用 ARM 的 `.init_machine()` 讲设备创建，**arm64 上不存在这个钩子**（`grep -rn 'init_machine' arch/arm64/` 为空）。arm64 完全依赖上面这个 `arch_initcall_sync`。这是三等证据过时的一例，登记在 `99-evidence.md`。

### §5.4 驱动自己在 probe 里下钻

父驱动想给自己的子节点建设备时，用 `of_platform_populate()` 或 `devm_of_platform_populate()`。全树用量：

```
grep -rlw 'of_platform_populate'      --include=*.c . | wc -l      # 92
grep -rlw 'devm_of_platform_populate' --include=*.c . | wc -l      # 51
```

`devm_` 版本（导出在 `drivers/of/platform.c:692`，配对的 `devm_of_platform_depopulate()` 在 `:724`）会在设备移除时自动 depopulate，是新代码该用的形态。手工版本要自己配 `of_platform_depopulate()`（`drivers/of/platform.c:646`，导出 `:653`）。

⚠️ 一个常见误用：容器节点已经带了 `"simple-bus"`，驱动又在 probe 里调一次 `of_platform_populate()`。这不会造出两份设备——`OF_POPULATED_BUS` 会让第二次直接返回（`drivers/of/platform.c:367-370`），只有一条 `pr_debug`。但它意味着代码里有一半是死的，读代码的人会被误导。

## §6 arm,primecell：走 amba_device 而不是 platform_device

`of_platform_bus_create()` 在建 platform_device 之前有一道分叉（`drivers/of/platform.c:380`）：

```c
	if (of_device_is_compatible(bus, "arm,primecell")) {
		/*
		 * Don't return an error here to keep compatibility with older
		 * device tree files.
		 */
		of_amba_device_create(bus, bus_id, platform_data, parent);
		return 0;
	}
```

注意注释和 `return 0`：**amba 设备创建失败会被静默吞掉**，为的是兼容老 DT 文件。

`of_amba_device_create()`（`drivers/of/platform.c:220`）与 platform 路线的差异：

| 步骤 | amba 路线 | platform 路线 |
| --- | --- | --- |
| 可用性 + 去重 | `drivers/of/platform.c:231-232`，同样两道 | `drivers/of/platform.c:176-178` |
| 分配 | `amba_device_alloc(NULL, 0, 0)`，`drivers/of/platform.c:235` | `platform_device_alloc("", PLATFORM_DEVID_NONE)` |
| 额外属性 | 读 `arm,primecell-periphid` 填 `dev->periphid`，`drivers/of/platform.c:254-256` | 无 |
| 资源 | 只取 `reg` 的第 0 项，`drivers/of/platform.c:258` | 全部 `reg` 项 |
| 注册 | `amba_device_add(dev, &iomem_resource)`，`drivers/of/platform.c:265` | `of_device_add()` |
| 失败清 flag | `drivers/of/platform.c:277` | `drivers/of/platform.c:197-198` |

两处 `pr_err`（`drivers/of/platform.c:260` 地址解析失败、`:267` 注册失败）是这条路线上唯一会说话的地方，见 §12。

⚠️ **`Documentation/devicetree/usage-model.rst` 的附录 A 在这里是错的**。它写的是"If a node is compatible with `arm,amba-primecell`, then `of_platform_populate()` will register it as an amba_device"，但代码判断的字符串是 `"arm,primecell"`（`drivers/of/platform.c:380`）。全树 grep `arm,amba-primecell` 只有一处命中，在 `arch/arm/boot/dts/nspire.dtsi:164`——一个 arm32 的 dtsi，而且按代码逻辑这个 compatible 根本走不进 amba 分支。按文档写会静默得到一个 platform_device。这条登记在 `99-evidence.md`。

`CONFIG_ARM_AMBA` 关掉时 `of_amba_device_create()` 是个空桩（`drivers/of/platform.c:281`）。arm64 上它总是开的。

## §7 fw_devlink：34 条 phandle 属性变成 device_link

### §7.1 机制

驱动模型不需要知道 `clocks` 是什么意思，只需要知道"这个属性里有一个 phandle，指向的节点是我的供方"。这就是 `fw_devlink` 的全部内容：**在任何设备 probe 之前，先把 DT 里的依赖图翻译成 `struct device_link`**，让 probe 顺序自动正确，不再依赖 `-EPROBE_DEFER` 反复重试。

入口是 `fw_devlink_parse_fwtree()`（`drivers/base/core.c:1774`），逐节点调 `fw_devlink_parse_fwnode()`（`drivers/base/core.c:1765`）；真正建链在 `fw_devlink_create_devlink()`（`drivers/base/core.c:2051`），两个方向的收集在 `__fw_devlink_link_to_consumers()`（`drivers/base/core.c:2162`）和 `__fw_devlink_link_to_suppliers()`（`drivers/base/core.c:2235`）。

翻译表在 `drivers/of/property.c:1341`，叫 `of_supplier_bindings[]`，共 34 条（`sed -n '1341,1376p' drivers/of/property.c | grep -c 'parse_prop ='` → 34）。其中：

| 来源 | 条数 | 位置 |
| --- | --- | --- |
| `DEFINE_SIMPLE_PROP()` 宏展开 | 27 | `drivers/of/property.c:1244-1270`，宏本体 `:1156` |
| `DEFINE_SUFFIX_PROP()` 宏展开 | 2 | `drivers/of/property.c:1271-1272`，宏本体 `:1210` |
| 手写的特例解析函数 | 5 | `drivers/of/property.c:1274`、`:1284`、`:1293`、`:1315`、`:1330` |

重跑口径：`grep -c '^DEFINE_SIMPLE_PROP' drivers/of/property.c` → 27；`grep -c '^DEFINE_SUFFIX_PROP' drivers/of/property.c` → 2。

### §7.2 34 条属性清单

| 属性名 | cells 属性 | 登记行 |
| --- | --- | --- |
| `clocks` | `#clock-cells` | `drivers/of/property.c:1244` |
| `interconnects` | `#interconnect-cells` | `drivers/of/property.c:1245` |
| `iommus` | `#iommu-cells` | `drivers/of/property.c:1246` |
| `mboxes` | `#mbox-cells` | `drivers/of/property.c:1247` |
| `io-channels` | `#io-channel-cells` | `drivers/of/property.c:1248` |
| `interrupt-parent` | 无 | `drivers/of/property.c:1249` |
| `dmas` | `#dma-cells` | `drivers/of/property.c:1250` |
| `power-domains` | `#power-domain-cells` | `drivers/of/property.c:1251` |
| `hwlocks` | `#hwlock-cells` | `drivers/of/property.c:1252` |
| `extcon` | 无 | `drivers/of/property.c:1253` |
| `nvmem-cells` | 无 | `drivers/of/property.c:1254` |
| `phys` | `#phy-cells` | `drivers/of/property.c:1255` |
| `wakeup-parent` | 无 | `drivers/of/property.c:1256` |
| `pinctrl-0` … `pinctrl-8` | 无 | `drivers/of/property.c:1257-1265`（9 条） |
| `pwms` | `#pwm-cells` | `drivers/of/property.c:1266` |
| `resets` | `#reset-cells` | `drivers/of/property.c:1267` |
| `leds` | 无 | `drivers/of/property.c:1268` |
| `backlight` | 无 | `drivers/of/property.c:1269` |
| `panel` | 无 | `drivers/of/property.c:1270` |
| `*-supply`（后缀匹配） | 无 | `drivers/of/property.c:1271` |
| `*-gpio`（后缀匹配） | `#gpio-cells` | `drivers/of/property.c:1272` |
| `*-gpios`（手写） | — | `drivers/of/property.c:1274` |
| `iommu-map`（手写，可选） | — | `drivers/of/property.c:1284` |
| 老式 `*-gpio` 兼容（手写） | — | `drivers/of/property.c:1293` |
| `interrupts`（手写） | — | `drivers/of/property.c:1315` |
| `remote-endpoint`（手写） | — | `drivers/of/property.c:1330` |

表里 `.optional = true` 的两条是 `parse_iommus`（`drivers/of/property.c:1344`）和 `parse_iommu_maps`——供方缺失不算错误。`parse_remote_endpoint` 带 `.node_not_dev = true`，因为 endpoint 节点本身不是设备，得往上找。

⚠️ 这张表意味着：**只要一个属性名在这 34 条里，写上它就会产生一条设备依赖，哪怕你的驱动完全不去读它**。反过来，厂商私有的 phandle 属性（比如 `vendor,my-companion = <&other>`）不在表里，写了也不会产生任何依赖，probe 顺序不受影响——这是"probe 顺序为什么和我 DTS 里的写法无关"的常见答案。

### §7.3 三种模式与两个内核参数

`fw_devlink=` 是 `early_param`（`drivers/base/core.c:1738`），处理函数 `fw_devlink_setup()` 在 `drivers/base/core.c:1722`：

| 取值 | 标志组合 | 定义位置 |
| --- | --- | --- |
| `off` | `fw_devlink_flags = 0` | `drivers/base/core.c:1727-1728` |
| `permissive` | `DL_FLAG_INFERRED` + `DL_FLAG_SYNC_STATE_ONLY` | `drivers/base/core.c:1714` |
| `on`（默认） | `DL_FLAG_INFERRED` + `DL_FLAG_AUTOPROBE_CONSUMER` | `drivers/base/core.c:1716` |
| `rpm` | 上面那组再加 `DL_FLAG_PM_RUNTIME` | `drivers/base/core.c:1718` |

默认值是 `on`：`static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_ON;`（`drivers/base/core.c:1721`）。

第二个参数 `fw_devlink.strict=`（`early_param` 在 `drivers/base/core.c:1745`，处理函数 `:1741`，查询函数 `fw_devlink_is_strict()` 在 `:1760`）。它管的是"供方没 probe 成功时消费方是不是必须等"。

三个标志的语义见 `05-usage.md` §7.3 和 `02-datastructures.md` 里的 `DL_FLAG_*` 表。⚠️ `DL_FLAG_INFERRED`（`include/linux/device.h:330`）就是"这条链接是从固件描述推断出来的"这个标记——手工 `device_link_add()` 不要传它。

### §7.4 环的处理

DT 里出现依赖环是常态（两个设备互相是对方的供方）。`fw_devlink` 有专门的解环逻辑，痕迹是三条日志：

- `pr_debug("----- cycle: start -----\n")`，`drivers/base/core.c:1958`
- `pr_info("%pfwf: Fixed dependency cycle(s) with %pfwf\n", ...)`，`drivers/base/core.c:2090`
- `dev_dbg(link->consumer, "Relaxing link with %s\n", ...)`，`drivers/base/core.c:1794`

只有中间那条是 `pr_info`，默认可见。看到它不是错误，是核心告诉你它发现并降级处理了一个环——被降级的那条链接会带上 `DL_FLAG_CYCLE`（`include/linux/device.h:331`）和 permissive 标志组合（`fw_devlink_get_flags()`，`drivers/base/core.c:1747`）。

## §8 DMA、IOMMU、MSI 相关属性

### §8.1 dma-coherent 与 dma-ranges

这两个属性的处理点不在建设备时，而在**probe 时**。链路：

```
really_probe()                      drivers/base/dd.c:584
  └─ dev->bus->dma_configure(dev)   drivers/base/dd.c:620-621
       └─ platform_dma_configure()  drivers/base/platform.c:1443
            └─ of_dma_configure(dev, dev->of_node, true)   drivers/base/platform.c:1450
                 └─ of_dma_configure_id()                  drivers/of/device.c:114
```

挂载点是 `platform_bus_type.dma_configure`（`drivers/base/platform.c:1486`），字段声明在 `include/linux/device/bus.h:107`；配对的 `.dma_cleanup` 在 `drivers/base/platform.c:1487`（字段 `include/linux/device/bus.h:108`），拆除路径在 `drivers/base/dd.c:677-678`、`:703-704`、`:1262-1263`。

`of_dma_configure_id()`（导出在 `drivers/of/device.c:224`）里两条关键动作：

- `of_dma_is_coherent(np)`（`drivers/of/device.c:201`）—— 这是 `dma-coherent` 属性的读取点，决定挂一致性还是非一致性的 DMA ops
- `dma-ranges` 只有解析成功才会设总线上限和 range map（`drivers/of/device.c:195` 的注释和 `:207` 的注释都在强调这一点）

`force_dma` 参数（`drivers/of/device.c:138`）的意思是"即使没有 `dma-ranges` 也要配 DMA"。platform 总线传的是 `true`（`drivers/base/platform.c:1450`），所以平台设备一律会被配 DMA。

⚠️ **`dma-coherent` 写在设备节点上，`dma-ranges` 写在父节点上**。搞反了不会报错，只会让缓存一致性行为悄悄变错——表现是偶发的数据损坏，而不是启动失败。这是本篇最难定位的一类问题。

### §8.2 iommus / iommu-map

`of_dma_configure_id()` 内部会走 `of_iommu_configure()`，把设备挂到 IOMMU 域上。这一层的细节属于 iommu 子系统，不在本篇。

驱动模型这边有一条要知道的：`platform_dma_configure()` 在 DMA 配置成功后还有一步（`drivers/base/platform.c:1456-1460`）：

```c
	if (!ret && !drv->driver_managed_dma) {
		ret = iommu_device_use_default_domain(dev);
		if (ret)
			arch_teardown_dma_ops(dev);
	}
```

`driver_managed_dma` 是 `struct platform_driver` 上的标志。没设它的驱动会被自动放进默认 IOMMU 域；要自己管域的驱动（VFIO 那类）必须设。

`iommus` 同时也是 fw_devlink 的一条可选供方（`drivers/of/property.c:1246`），所以它有两个消费者：一个建依赖链接，一个配 DMA 域。

### §8.3 msi-parent

`of_msi_configure()`（`drivers/of/irq.c:753`，导出 `:758`）在建设备时就被调用，位置是 `of_platform_device_create_pdata()` 里的 `drivers/of/platform.c:187`——注意它在 `of_device_add()`（`:189`）之前，所以 `dev->msi.domain` 在设备进入 sysfs 之前就已经填好。

## §9 auxdata：给节点改名或塞 platform_data

### §9.1 机制

`of_platform_populate()` 的第三个参数是一张 `struct of_dev_auxdata` 表（结构体 `include/linux/of_platform.h:37`，kerneldoc `:16`，构造宏 `OF_DEV_AUXDATA` 在 `:45`）。它解决两个历史问题：

- DT 化过程中设备名字变了，但老驱动或 clkdev 表还按老名字找设备
- 有些设备还需要 `platform_data`，DT 里没法表达

查表函数 `of_dev_lookup()`（`drivers/of/platform.c:293`）跑两趟：

| 趟 | 匹配条件 | 行 |
| --- | --- | --- |
| 第一趟 | `compatible` 匹配，且 `reg` 第 0 项的起始地址等于 `auxdata->phys_addr` | `drivers/of/platform.c:304-313` |
| 第二趟 | `compatible` 匹配，且表项的 `phys_addr` 和 `name` 都为空 | `drivers/of/platform.c:319-327` |

⚠️ 第一趟里 `of_address_to_resource()` 失败时会**跳过地址比对直接算命中**（`drivers/of/platform.c:308-310` 那个嵌套 `if` 没有 `else`）。也就是说没有 `reg` 的节点只靠 `compatible` 就能匹配上带 `phys_addr` 的表项。

命中结果用在 `of_platform_bus_create()` 的 `drivers/of/platform.c:373-377`：`bus_id` 覆盖设备名，`platform_data` 塞进 `pdev->dev.platform_data`。

### §9.2 arm64 上用不用

不用。全树只有 15 个文件用 `OF_DEV_AUXDATA`：

```
grep -rlw 'OF_DEV_AUXDATA' --include=*.c . | wc -l                    # 15
grep -rlw 'OF_DEV_AUXDATA' --include=*.c arch/arm64 2>/dev/null | wc -l   # 0
```

分布是 `arch/arm/mach-spear`（4）、`arch/arm/mach-versatile`（3）、`arch/arm/mach-omap2` / `mach-mvebu` / `mach-orion5x` / `mach-lpc32xx` 各 1、`arch/mips/pic32/pic32mzda`（1），以及 `drivers/usb/musb`、`drivers/mfd`、`drivers/bus` 各 1。**`arch/arm64` 下一个都没有**，所以写 arm64 平台代码时不需要考虑这个机制——列在这里只为了在读 `of_platform_populate()` 签名时不被第三个参数困住。

## §10 一个完整实例：sdm845 上的 bwmon

拿 `05-usage.md` §12.1 用的那个参考驱动（`drivers/soc/qcom/icc-bwmon.c`）对应的真实节点走一遍。

### §10.1 DTS

容器节点 `arch/arm64/boot/dts/qcom/sdm845.dtsi:1079`：

```
	soc: soc@0 {
		#address-cells = <2>;
		#size-cells = <2>;
		ranges = <0 0 0 0 0x10 0>;
		dma-ranges = <0 0 0 0 0x10 0>;
		compatible = "simple-bus";
```

设备节点 `arch/arm64/boot/dts/qcom/sdm845.dtsi:2178`：

```
		pmu@1436400 {
			compatible = "qcom,sdm845-bwmon", "qcom,msm8998-bwmon";
			reg = <0 0x01436400 0 0x600>;
			interrupts = <GIC_SPI 581 IRQ_TYPE_LEVEL_HIGH>;
			interconnects = <&gladiator_noc MASTER_APPSS_PROC 3 &mem_noc SLAVE_LLCC 3>;

			operating-points-v2 = <&cpu_bwmon_opp_table>;

			cpu_bwmon_opp_table: opp-table {
				compatible = "operating-points-v2";
```

### §10.2 逐属性走一遍

| 属性 | 行 | 谁处理 | 结果 |
| --- | --- | --- | --- |
| `soc@0` 的 `compatible = "simple-bus"` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:1084` | `of_default_bus_match_table[]`（`drivers/of/platform.c:25`）命中 | 递归下钻，`pmu@1436400` 才会被扫到（`drivers/of/platform.c:393-400`） |
| `soc@0` 的 `#address-cells` / `#size-cells` / `ranges` | `:1080-1082` | `of_translate_address()` | `0x01436400` 能翻译成 CPU 物理地址 |
| `soc@0` 的 `dma-ranges` | `:1083` | `of_dma_configure_id()`（`drivers/of/device.c:114`） | 子设备的总线地址范围 |
| 没有 `status` | — | `__of_device_is_available()`（`drivers/of/base.c:620-622`） | 默认可用 |
| `compatible` 两项 | `:2179` | `of_platform_bus_create()` 第一道门（`drivers/of/platform.c:355-360`）过；后续匹配靠 `platform_match()`（`drivers/base/platform.c:1331`） | 见 §10.3 |
| `reg` | `:2180` | `of_device_alloc()`（`drivers/of/platform.c:124-125`、`:137-141`） | 一条 `IORESOURCE_MEM`，`0x01436400` 起、长 `0x600` |
| `reg`（第二用途） | `:2180` | `of_device_make_bus_id()`（`drivers/of/platform.c:86-90`） | 设备名 `1436400.pmu` |
| `interrupts` | `:2181` | 建设备时不读；`platform_get_irq()` 时经 `of_irq_get()`（`drivers/base/platform.c:184`）解析 | 驱动在 `drivers/soc/qcom/icc-bwmon.c:595` 拿到 virq |
| `interconnects` | `:2182` | `parse_interconnects`（`drivers/of/property.c:1245`） | 两条 `device_link`，供方是 `gladiator_noc` 和 `mem_noc` |
| `operating-points-v2` | `:2184` | 不是 fw_devlink 表里的属性，OPP 核心自己读 | 对驱动模型无影响 |
| 子节点 `opp-table` 的 `compatible = "operating-points-v2"` | `:2187` | `of_skipped_node_table[]`（`drivers/of/platform.c:35`）命中，在 `:362-365` 被拦 | 不会造出多余的 platform_device |

### §10.3 一个容易看错的地方：匹配的是第二个 compatible

节点写的是 `compatible = "qcom,sdm845-bwmon", "qcom,msm8998-bwmon"`，而驱动的匹配表（`drivers/soc/qcom/icc-bwmon.c:675`）里三条是 `"qcom,msm8998-bwmon"`、`"qcom,sdm845-llcc-bwmon"`、`"qcom,sc7280-llcc-bwmon"`——**`"qcom,sdm845-bwmon"` 根本不在表里**。

真正命中的是节点的第二个 compatible `"qcom,msm8998-bwmon"`，走的是"从具体到通用"的回退。这决定了 `of_device_get_match_data()`（`include/linux/of_device.h:33`）返回的是 `msm8998_bwmon_data` 而不是某个 sdm845 专属的数据。

实践结论：**`compatible` 列表的顺序是语义的一部分**，最具体的写在最前面。驱动可以只认通用的那一项，将来需要区分时再加具体项，DTS 不用改。读 DTS 时不能假定第一个 compatible 就是驱动实际匹配上的那个——要拿驱动的 `of_device_id` 表逐条对。

## §11 配错了怎么定位

按"什么时候能查出来"分三层。⚠️ 本篇的定位手段只覆盖"节点 → 设备 → 绑定"这一段；绑定之后的调试（probe 里失败、`sync_state()` 没触发、devres 泄漏）在 `07-debug.md`。

### §11.1 编译期

| 手段 | 能查出什么 | 查不出什么 |
| --- | --- | --- |
| `make dtbs` 的 dtc 警告 | 语法错误、`reg` 与节点名 `@` 后地址不一致、缺 `#address-cells` | 属性名拼错（dtc 不知道哪些名字有意义） |
| `make dt_binding_check` / `CHECK_DTBS=y` | 有 YAML 绑定的属性的取值范围 | 没有绑定文件的厂商私有属性 |
| `grep` 属性名 | 属性到底有没有消费者 | — |

第三条是本篇最推荐的动作，而且是唯一能查出"死字属性"的办法：

```
grep -rn '"my-property"' drivers/ | head
```

空结果 = 这个属性谁都不读，写它等于写注释。⚠️ 绑定文档不会告诉你这件事——绑定文档描述的是"约定"，不是"实现"。

### §11.2 运行期静态（启动完成后一次性检查）

这一层不需要复现问题，随时可查。

| 查什么 | 怎么查 | 依据 |
| --- | --- | --- |
| 内核实际拿到的 DT 长什么样 | `/sys/firmware/devicetree/base/` 目录树，每个属性是一个文件 | kset 建于 `drivers/of/base.c:173`，根节点目录名 `base` 来自 `drivers/of/kobj.c:128` |
| 原始 FDT blob | `/sys/firmware/fdt`（配合 `dtc -I dtb -O dts`） | `drivers/of/fdt.c:1393` |
| 某个节点有没有变成设备 | 看 `/sys/devices/platform/` 下有没有对应名字；名字规则见 §3.2 | `of_device_make_bus_id()`，`drivers/of/platform.c:74` |
| 某个设备对应哪个节点 | 读 `/sys/devices/platform/<name>/of_node` 这个符号链接的目标 | 链接建于 `drivers/base/core.c:3410` |
| 设备绑上驱动了没有 | `/sys/devices/platform/<name>/driver` 这个链接存不存在 | `03-internals.md` Part A |
| 依赖链接建成什么样 | `/sys/devices/platform/<name>/consumer:*` 和 `supplier:*` 这些链接 | `device_link_add()`，`drivers/base/core.c:785` |
| 还在等供方的设备 | `cat /sys/kernel/debug/devices_deferred` | debugfs 文件建于 `drivers/base/dd.c:346` |

⚠️ 第三行和第四行合起来是排查"我的节点为什么没生效"的主力：**`/sys/firmware/devicetree/base/` 里有这个节点，但 `/sys/devices/platform/` 里没有对应设备**，就精确定位到了 §2 或 §5 的某一条门槛，而这些门槛全部是 `pr_debug`，dmesg 里看不到。

### §11.3 运行期动态

| 手段 | 打开方式 | 看什么 |
| --- | --- | --- |
| `of_platform` 的 `pr_debug` | `dyndbg="file drivers/of/platform.c +p"` 或 `CONFIG_DYNAMIC_DEBUG` 下 `echo 'file platform.c +p' > /sys/kernel/debug/dynamic_debug/control` | §2.1 / §5.1 / §5.2 三道门跳过节点时的原因 |
| `fw_devlink` 的解环日志 | 同上，针对 `drivers/base/core.c` | `----- cycle: start -----`（`drivers/base/core.c:1958`）一带的完整环路径 |
| 延迟探测 | `initcall_debug` + `deferred_probe_timeout=<秒>`（`__setup` 在 `drivers/base/dd.c:275`） | 超时后 `dev_warn(dev, "deferred probe timeout, ignoring dependency\n")`，`drivers/base/dd.c:298` |
| 谁在等谁 | `cat /sys/kernel/debug/devices_deferred`；每行由 `dev_info(p->device, "deferred probe pending\n")` 同源的信息构成（`drivers/base/dd.c:318`） | 卡住的设备清单 |

### §11.4 症状清单（按坏掉的范围分组）

**整棵树级**（一个设备都没有 / 极少）

| 症状 | 最可能的原因 | 去 |
| --- | --- | --- |
| `/sys/firmware/devicetree/` 不存在 | 内核没拿到 DT，或 `CONFIG_OF` 没开 | 不在本篇 |
| DT 存在但 `/sys/devices/platform/` 几乎是空的 | 根节点下的节点都没有 `compatible` | §2.1 |

**容器节点级**（某个 SoC 子树整块缺失）

| 症状 | 最可能的原因 | 去 |
| --- | --- | --- |
| 容器节点自己变成了设备，孩子一个都没有 | 容器的 `compatible` 里没有 `simple-bus` / `simple-mfd` | §5.1 |
| 孩子有设备，但资源都是空的 | 容器漏写 `ranges` | §3.3 |
| 驱动里调了 `of_platform_populate()` 但什么都没建 | `OF_POPULATED_BUS` 已被设过（容器已带 `simple-bus`） | §5.4 |

**单设备级**

| 症状 | 最可能的原因 | 去 |
| --- | --- | --- |
| 节点在 DT 里，设备不在 sysfs 里，dmesg 全静默 | `status` 不是 `okay`/`ok`，或没有 `compatible` | §2.1、§2.2 |
| 设备名字和节点名 `@` 后面的数字不一样 | 父节点 `ranges` 做了地址搬移 | §3.2 |
| 设备建了但没有 `driver` 链接 | `compatible` 与驱动的 `of_device_id` 表对不上 | §10.3 |
| 设备卡在 `devices_deferred` 里 | 供方设备没 probe 成功 | §7、`05-usage.md` §8 |
| 期望走 amba 却成了 platform_device | `compatible` 写的是 `arm,amba-primecell` 而不是 `arm,primecell` | §6 |

**属性级**

| 症状 | 最可能的原因 | 去 |
| --- | --- | --- |
| `platform_get_resource(pdev, IORESOURCE_IRQ, 0)` 返回 `NULL` | DT 路线不填 IRQ 资源 | §4 |
| `platform_get_irq()` 返回 `-ENXIO` | 节点没有 `interrupts`，或 `interrupt-parent` 链断了 | §4 |
| `platform_get_irq()` 一直 `-EPROBE_DEFER` | 中断控制器还没 probe | §4、§7 |
| 我写的属性完全没生效，也没报错 | 没有消费者 | §11.1 第三行 |
| probe 顺序和预期不符 | 用的 phandle 属性不在 34 条表里 | §7.2 |
| 偶发数据损坏 | `dma-coherent` / `dma-ranges` 写错层级 | §8.1 |

## §12 错误消息 → 原因

只收和"节点变设备"这条路直接相关的。每条都能用给出的命令在源码里逐字找到。

| 消息 | 出处 | 含义 | 常见原因 |
| --- | --- | --- | --- |
| `amba: of_address_to_resource() failed (%d) for %pOF` | `drivers/of/platform.c:260` | primecell 节点的 `reg` 解析失败 | 缺 `reg`，或父节点 `ranges` 不对 |
| `amba_device_add() failed (%d) for %pOF` | `drivers/of/platform.c:267` | amba 设备注册失败 | 资源与 `iomem_resource` 冲突 |
| `IRQ index %u not found` | `drivers/base/platform.c:265-266`（经 `dev_err_probe()`） | `platform_get_irq()` 拿不到中断 | 见 §11.4 属性级前三行 |
| `0 is an invalid IRQ number` | `drivers/base/platform.c:236` 和 `drivers/base/platform.c:452` | 解析出 irq 0 | ⚠️ 两处逐字相同，不能凭消息判断是哪条调用路径 |
| `Failed to create device link (0x%x) with %s` | `drivers/base/core.c:2121` | fw_devlink 建链接失败 | 供方设备状态异常 |
| `%pfwf: Fixed dependency cycle(s) with %pfwf` | `drivers/base/core.c:2090` | 发现并降级处理了依赖环 | ⚠️ 这是 `pr_info`，不是错误 |
| `Error %d creating of_node link` | `drivers/base/core.c:3412` | sysfs 里的 `of_node` 符号链接建失败 | 极少见；同名冲突 |
| `deferred probe timeout, ignoring dependency` | `drivers/base/dd.c:298` | `deferred_probe_timeout` 到了，强行放行 | 供方驱动没编进内核 |
| `deferred probe pending` | `drivers/base/dd.c:318` | `devices_deferred` 里每个条目的标记 | 正常的等待状态 |
| `%s: probe of %s failed with error %d` | `drivers/base/dd.c:576` | probe 返回了非 defer 的错误 | 驱动自己的问题，不是 DT 配置问题 |

⚠️ 这张表最重要的一条信息是它有多短。**§2 和 §5 那五道门（无 `compatible`、`status` 不可用、被 `of_skipped_node_table` 跳过、`OF_POPULATED` 已置、容器不在 match 表里）全部只有 `pr_debug`，一条 `dev_err` 都没有。** 所以"dmesg 干净"绝不等于"DT 没问题"。

## §13 静默失效决策树

节点写了但设备没出现，按这个顺序查。每一步都能在不重启的情况下确认。

```
/sys/firmware/devicetree/base/<路径> 存在吗？
├─ 否 → DT 根本没包含这个节点（改错了 dtsi / 用了错的 dtb）
└─ 是
   ├─ 节点有 compatible 吗？
   │  └─ 没有 → §2.1 第一道门。⚠️ 只有 pr_debug
   ├─ 节点有 status 且值不是 okay/ok 吗？
   │  └─ 是 → §2.2。⚠️ 完全静默，连 pr_debug 都没有
   ├─ compatible 是 operating-points-v2 吗？
   │  └─ 是 → §5.2 白名单跳过。⚠️ 只有 pr_debug
   ├─ 节点在 /reserved-memory 下面吗？
   │  └─ 是 → 只有 §5.2 那 7 个 compatible 会建设备
   ├─ 所有祖先容器节点都带 simple-bus/simple-mfd/isa/arm,amba-bus 吗？
   │  └─ 有一个没带 → §5.1。⚠️ 只有 pr_debug，而且容器自己是有设备的，
   │                    很容易误判成"populate 成功了"
   └─ 都过了 → 设备应该存在。按 §3.2 的名字规则重算一次名字再找
              （地址前缀是翻译后的地址，不是节点名里的数字）
```

设备存在但没绑驱动，换一条：

```
/sys/devices/platform/<name>/driver 存在吗？
├─ 是 → 绑上了，问题在 probe 里面 → 07-debug.md
└─ 否
   ├─ 设备在 /sys/kernel/debug/devices_deferred 里吗？
   │  └─ 是 → 在等供方。看 supplier:* 链接 → §7
   ├─ 驱动编进内核 / 模块装了吗？
   │  └─ 没有 → 装模块；modalias 由 compatible 生成
   └─ 驱动的 of_device_id 表里有节点 compatible 列表中的任意一项吗？
      └─ 没有 → §10.3。⚠️ 这一步必须逐条对，不能只对第一项
```

## §14 汇总

### §14.1 本篇覆盖到哪

| 阶段 | 本篇 | 别处 |
| --- | --- | --- |
| DT 节点 → `platform_device` / `amba_device` | §2–§6、§9 | — |
| DT 依赖 → `device_link` | §7 | 语义在 `05-usage.md` §7、`02-datastructures.md` |
| DMA / IOMMU / MSI 配置的触发点 | §8 | 配置内容本身在 dma-mapping 与 iommu 文档集 |
| 绑定过程（match → probe） | 只到"绑没绑上" | `03-internals.md` Part A |
| probe 内部失败 | 不覆盖 | `07-debug.md` |
| 非 DT 发现路径 | §0.2 分诊表点名 | `04-extensions.md`、`05-usage.md` §2 |

### §14.2 三句话记住的东西

- `drivers/base/` 自己不读任何 DT 属性；读 DT 的是 `drivers/of/`，它只交出三样东西：资源数组、`dev->of_node`、`device_link`
- DT 路线的中断不进资源表，必须走 `platform_get_irq*()`
- 从节点到设备这条路上有五道门，全部只有 `pr_debug` 或完全静默——排查时先比对 `/sys/firmware/devicetree/base/` 和 `/sys/devices/platform/`，不要指望 dmesg

### §14.3 本篇用到的可重跑数字

| 数字 | 命令 | 值 |
| --- | --- | --- |
| `drivers/base/*.c` 里的 DT 属性名 | `grep -rnoE '"(compatible\|status\|reg\|interrupts\|dma-coherent\|dma-ranges\|iommus\|msi-parent)"' drivers/base/*.c` | 2 条，均为 debugfs 列头 |
| fw_devlink 属性表条数 | `sed -n '1341,1376p' drivers/of/property.c \| grep -c 'parse_prop ='` | 34 |
| 其中宏生成 | `grep -c '^DEFINE_SIMPLE_PROP' drivers/of/property.c` / `grep -c '^DEFINE_SUFFIX_PROP' drivers/of/property.c` | 27 / 2 |
| `of_platform_populate` 使用文件数 | `grep -rlw 'of_platform_populate' --include=*.c . \| wc -l` | 92 |
| `devm_of_platform_populate` 使用文件数 | `grep -rlw 'devm_of_platform_populate' --include=*.c . \| wc -l` | 51 |
| `OF_DEV_AUXDATA` 使用文件数 | `grep -rlw 'OF_DEV_AUXDATA' --include=*.c . \| wc -l` | 15 |
| 其中 arm64 下的 | `grep -rlw 'OF_DEV_AUXDATA' --include=*.c arch/arm64 \| wc -l` | 0 |
| `arm,amba-primecell` 全树命中 | `grep -rn 'arm,amba-primecell' --include=*.c --include=*.dtsi --include=*.dts .` | 1（`arch/arm/boot/dts/nspire.dtsi:164`） |
| `reserved_mem_matches[]` 条数 | `sed -n '506,515p' drivers/of/platform.c \| grep -c compatible` | 7 |
