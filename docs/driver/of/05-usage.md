---
title: 使用指南
---

# of 05：各角色的代码骨架

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ **篇首免责声明**：本篇所有代码骨架**都没有编译过、没有运行过**。
> 它们是从本树真实驱动里提炼的结构，**不是可以直接 `make` 的代码**。
>
> ⚠️ 本篇出现的每个内核符号都已在 6.1.158 树上 grep 确认存在，
> 但**参数个数和类型请以头文件为准**（`include/linux/of.h` / `of_address.h` / `of_platform.h`）。

---

## §0 本子系统有哪些角色

### §0.1 角色清单

| § | 角色 | 你要写的东西 |
|---|---|---|
| §1 | **消费者驱动**（99% 的驱动） | `of_match_table` + probe 里读属性 |
| §2 | **复合设备**（一个节点带一堆子节点） | `devm_of_platform_populate()` |
| §3 | **提供者**（导出资源给别人 phandle 引用） | `#xxx-cells` + 一个 `xlate` 回调 |
| §4 | **DTS 作者** | **主体在 06 篇** —— 本节只给"驱动 ↔ DTS 的契约" |
| §5 | **调试者** | 主体在 07 篇 |

**没有"设备树驱动"这个角色** —— of 不是一条总线（02 §2.2）。

### §0.2 三条铁律（先记住再看骨架）

| # | 铁律 | 依据 |
|---|---|---|
| ① | **`of_find_*` / `of_get_*` / `of_parse_phandle` 返回的节点要 `of_node_put()`** | 01 §1.3 |
| ② | **`of_property_read_*` 而不是 `of_get_property` + 手工解析** | 后者要自己处理大端（01 §4.5） |
| ③ | **`-EPROBE_DEFER` 原样返回，不要打错误日志** | 01 §14.3 |

---

## §1 消费者驱动

### §1.1 骨架

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>

/* 每个型号的差异放进 .data，用 of_device_get_match_data() 取 */
struct mydev_variant {
	unsigned int	fifo_depth;
	bool		has_dma;
};

static const struct mydev_variant v1_data = { .fifo_depth = 16, .has_dma = false };
static const struct mydev_variant v2_data = { .fifo_depth = 64, .has_dma = true  };

struct mydev {
	struct device			*dev;
	void __iomem			*base;
	const struct mydev_variant	*var;
	u32				clk_freq;
	u32				gpios[4];
	int				irq;
	struct device_node		*phy_np;	/* 持有引用 */
};

static int mydev_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *np = dev->of_node;	/* 就是那个 device_node */
	struct mydev *my;
	int ret, n;

	/* ⚠️ 非 DT 探测出来的设备 of_node 可能为 NULL */
	if (!np)
		return -ENODEV;

	my = devm_kzalloc(dev, sizeof(*my), GFP_KERNEL);
	if (!my)
		return -ENOMEM;
	my->dev = dev;

	/* ① 取型号私有数据（01 §6） */
	my->var = of_device_get_match_data(dev);
	if (!my->var)
		return -EINVAL;

	/* ② 地址：resource 已经翻译好了，不要自己 of_iomap（01 §7.1） */
	my->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(my->base))
		return PTR_ERR(my->base);

	/* ③ 中断：惰性解析，可能 -EPROBE_DEFER（03 §A6） */
	my->irq = platform_get_irq(pdev, 0);
	if (my->irq < 0)
		return my->irq;			/* 原样返回，铁律 ③ */

	/* ④ 必需属性：缺了就是 DTS 错 */
	ret = of_property_read_u32(np, "clock-frequency", &my->clk_freq);
	if (ret)
		return dev_err_probe(dev, ret, "missing clock-frequency\n");

	/* ⑤ 可选属性：判 -EINVAL 用默认值（⚠️ 不是 -ENOENT，01 §14.2.1） */
	ret = of_property_read_u32(np, "vendor,fifo-threshold", &my->fifo_thr);
	if (ret == -EINVAL)
		my->fifo_thr = my->var->fifo_depth / 2;	/* 默认值 */
	else if (ret)
		return dev_err_probe(dev, ret, "bad vendor,fifo-threshold\n");

	/* ⑥ 布尔属性：存在即 true（⚠️ 写 <0> 也是 true，01 §4.2） */
	my->inverted = of_property_read_bool(np, "vendor,signal-inverted");

	/* ⑦ 数组：元素个数不对会 -EOVERFLOW */
	n = of_property_count_elems_of_size(np, "vendor,gpio-map", sizeof(u32));
	if (n > 0) {
		if (n > ARRAY_SIZE(my->gpios))
			return dev_err_probe(dev, -EINVAL,
					     "vendor,gpio-map too long (%d)\n", n);
		ret = of_property_read_u32_array(np, "vendor,gpio-map",
						 my->gpios, n);
		if (ret)
			return dev_err_probe(dev, ret, "bad vendor,gpio-map\n");
	}

	/* ⑧ 字符串列表：match_string 返回索引 */
	ret = of_property_match_string(np, "vendor,modes", "fast");
	if (ret >= 0)
		my->fast_mode_idx = ret;

	/* ⑨ phandle：返回的节点要 put（铁律 ①） */
	my->phy_np = of_parse_phandle(np, "vendor,phy", 0);
	if (!my->phy_np)
		return dev_err_probe(dev, -ENODEV, "missing vendor,phy\n");
	/* 保存它 ⇒ 生命周期由我负责，remove 里 put */

	/* ⑩ 遍历子节点：用 _scoped 版免得漏 put（01 §3.2） */
	for_each_available_child_of_node_scoped(np, child) {
		u32 id;

		if (of_property_read_u32(child, "reg", &id))
			continue;			/* break 也安全 */
		ret = mydev_add_channel(my, child, id);
		if (ret)
			return ret;			/* 自动 put */
	}

	platform_set_drvdata(pdev, my);
	return 0;
}

static int mydev_remove(struct platform_device *pdev)
{
	struct mydev *my = platform_get_drvdata(pdev);

	of_node_put(my->phy_np);		/* 和 ⑨ 配平 */
	return 0;
}

/* of_match_table：最具体的 compatible 写在前面（01 §6） */
static const struct of_device_id mydev_of_match[] = {
	{ .compatible = "myvendor,mydev-v2", .data = &v2_data },
	{ .compatible = "myvendor,mydev-v1", .data = &v1_data },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, mydev_of_match);

static struct platform_driver mydev_driver = {
	.probe	= mydev_probe,
	.remove	= mydev_remove,
	.driver	= {
		.name		= "mydev",
		.of_match_table	= mydev_of_match,	/* 不用 of_match_ptr（01 §6） */
	},
};
module_platform_driver(mydev_driver);
MODULE_LICENSE("GPL v2");
```

### §1.2 十个步骤对应的 API

| 步 | API | 位置 |
|---|---|---|
| ① | `of_device_get_match_data()` | `drivers/of/device.c:249` |
| ② | `devm_platform_ioremap_resource()` | 内部走 `platform_get_resource()` ⇒ 那是 `of_device_alloc()` 填的（`drivers/of/platform.c:129-142`） |
| ③ | `platform_get_irq()` ⇒ `of_irq_get()` | `drivers/of/irq.c:475` |
| ④⑤ | `of_property_read_u32()` ⇒ `of_property_read_variable_u32_array()` | `drivers/of/property.c:339` |
| ⑥ | `of_property_read_bool()` | inline（`include/linux/of.h:1156` 那段 doc） |
| ⑦ | `of_property_count_elems_of_size()` + `of_property_read_u32_array()` | `drivers/of/property.c:89` |
| ⑧ | `of_property_match_string()` | `drivers/of/property.c:490` |
| ⑨ | `of_parse_phandle()` | inline（`include/linux/of.h:912`）⇒ `drivers/of/base.c:1477` |
| ⑩ | `for_each_available_child_of_node_scoped()` | `include/linux/of.h:1387` |

### §1.3 四种"读属性"的错误处理模式

| 属性性质 | 正确的处理 |
|---|---|
| **必需** | `if (ret) return dev_err_probe(dev, ret, "…")` |
| **可选，有默认值** | `if (ret == -EINVAL) use_default(); else if (ret) return …;` ⚠️ **不是 `-ENOENT`**（01 §14.2.1） |
| **布尔** | `of_property_read_bool()` —— **不返回错误码** |
| **长度可变的数组** | 先 `of_property_count_elems_of_size()` 再读 |

⚠️ **最常见的错误写法**：
```c
	if (of_property_read_u32(np, "optional-prop", &v))
		v = DEFAULT;		/* ⚠️ 把 -EOVERFLOW 也当成"没配" */
```
⇒ **DTS 写错了（元素个数不对）会被静默吞掉**，用默认值继续跑。
正确写法见上面第二种模式。

### §1.4 ⚠️`dev->of_node` 可能为 NULL

| 什么时候为 NULL | 说明 |
|---|---|
| 设备不是从 DT 造出来的 | 如手工 `platform_device_register()` 的、ACPI 造的 |
| 你的驱动同时支持 DT 和非 DT | ⇒ **probe 开头必须判空** |

更通用的写法（同时支持 ACPI）：用 `device_property_read_u32()` 那一套（01 §10）。

### §1.5 `of_node_put()` 的三种场合

| 场合 | 写法 |
|---|---|
| 临时用一下 | 用完立刻 put，或用 `_scoped` 循环宏 |
| 要保存起来（如 §1.1 的 `phy_np`） | **remove 里 put** |
| 用 `__free(device_node)` | `struct device_node *np __free(device_node) = of_parse_phandle(…);`（`include/linux/of.h:132`） |

**`__free(device_node)` 是 6.1.158 里的新工具**（01 §1.4）——
它让"函数中途 return 也自动 put"。

---

## §2 复合设备：填充自己的子节点

### §2.1 为什么需要

`of_platform_default_populate()` **只往"白名单总线"下面钻**
（02 §1.0.A.2，白名单在 `drivers/of/platform.c:24-32`）。
⇒ **你的设备节点下面的子节点默认不会变成 `platform_device`**。

### §2.2 两种做法

```c
	/* 做法 A：让子节点变成独立的 platform_device */
static int myctl_probe(struct platform_device *pdev)
{
	int ret;

	/* … 先把自己初始化好（子设备 probe 时可能要用） … */

	/* 一句话：把我的子节点全填成 platform_device */
	ret = devm_of_platform_populate(&pdev->dev);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to populate children\n");

	return 0;
}
	/* devres 会在 remove 时自动 depopulate */

	/* 做法 B：自己管理子节点，不造 platform_device */
static int myctl_probe(struct platform_device *pdev)
{
	struct device_node *np = pdev->dev.of_node;

	for_each_available_child_of_node_scoped(np, child) {
		u32 idx;

		if (of_property_read_u32(child, "reg", &idx))
			continue;
		myctl_register_channel(my, child, idx);	/* 自己的抽象 */
	}
	return 0;
}
```

| API | 位置 |
|---|---|
| `devm_of_platform_populate(dev)` | `drivers/of/platform.c:692`（内部递归调用在 `:590`） |
| `devm_of_platform_depopulate(dev)` | `drivers/of/platform.c:724` |
| `of_platform_populate(root, matches, lookup, parent)` | `drivers/of/platform.c:465` |
| `of_platform_depopulate(parent)` | `drivers/of/platform.c:653` |

### §2.3 怎么选

| 用做法 A（`devm_of_platform_populate`） | 用做法 B（自己遍历） |
|---|---|
| 子节点有**自己的 compatible 和独立驱动** | 子节点只是**配置数据**（如一个通道的参数） |
| 子节点有自己的 `reg` / `interrupts` | 子节点没有独立的资源 |
| 典型：MFD、syscon 下的子功能 | 典型：pinctrl 的 pin group、regulator 的多路输出 |

⚠️ **做法 A 的一个陷阱**：`devm_of_platform_populate()` 一调用，
子设备就可能**立刻 probe** —— 所以**必须先把自己初始化好**。

### §2.4 另一种选择：在 DTS 里写 `simple-mfd`

如果你的节点 compatible 里加上 `"simple-mfd"`
（那是白名单里的一项，`drivers/of/platform.c:26`），
`of_platform_default_populate()` **就会自动往下钻** ——
⇒ 你的驱动一行代码都不用写。

⚠️ **但这是 DTS 的决定，不是驱动的** ——
而且 `simple-mfd` 意味着"这个节点自己没什么要做的"。
⇒ 如果你的节点自己也需要一个驱动（要初始化时钟、复位等），
就得用做法 A（先 probe 自己，再填子节点）。

---

## §3 提供者：让别人能 phandle 引用你

### §3.1 三件事

| # | 事 | 在哪做 |
|---|---|---|
| ① | 在 DTS 里声明 `#xxx-cells` | **DTS** |
| ② | 向对应的子系统注册自己 | 驱动 probe（如 `of_clk_add_provider()` / `gpiochip_add_data()`） |
| ③ | 提供一个 `xlate` 回调，把 cells 翻译成"第几个资源" | 驱动 |

**注意 ①②③ 都不在 `drivers/of/` 里** ——
`drivers/of/` 只提供 `of_parse_phandle_with_args()` 这个**通用解析器**（01 §5.2），
"cells 怎么翻译"由**各子系统自己定义**。

### §3.2 通用形状（以一个假想的 "widget 提供者" 为例）

```c
/* ================= DTS 侧 ================= */
//	widgetctl: widget-controller@10000000 {
//		compatible = "myvendor,widget-ctl";
//		reg = <0x10000000 0x1000>;
//		#widget-cells = <2>;        /* 关键：声明参数个数 */
//	};
//
//	consumer {
//		widgets = <&widgetctl 3 0>, <&widgetctl 5 1>;
//		widget-names = "main", "aux";
//	};

/* ================= 消费者侧的解析（通常由子系统核心提供） ================= */
static int widget_get_by_index(struct device *dev, int index,
			       struct widget_spec *spec)
{
	struct of_phandle_args args;
	struct widget_provider *prov;
	int ret;

	/* cells_name 是 "#widget-cells" —— 由提供者节点声明 */
	ret = of_parse_phandle_with_args(dev->of_node, "widgets",
					 "#widget-cells", index, &args);
	if (ret)
		return ret;			/* -ENOENT（列表缺失/index 越界/空条目）/ -EINVAL（解析错）/ -EPROBE_DEFER */

	prov = widget_find_provider(args.np);	/* 在自己的注册表里找 */
	if (!prov) {
		ret = -EPROBE_DEFER;		/* 提供者还没 probe */
		goto out;
	}

	/* 把 args.args[] 交给提供者自己的 xlate 回调 */
	ret = prov->xlate(prov, &args, spec);
out:
	of_node_put(args.np);			/* 铁律 ① */
	return ret;
}

/* ================= 提供者侧 ================= */
static int widgetctl_xlate(struct widget_provider *prov,
			   const struct of_phandle_args *args,
			   struct widget_spec *spec)
{
	if (args->args_count != 2)
		return -EINVAL;			/* 和 #widget-cells 对不上 */
	if (args->args[0] >= prov->nr_widgets)
		return -EINVAL;

	spec->id    = args->args[0];
	spec->flags = args->args[1];
	return 0;
}

static int widgetctl_probe(struct platform_device *pdev)
{
	/* … */
	prov->np    = pdev->dev.of_node;
	prov->xlate = widgetctl_xlate;
	return widget_add_provider(prov);	/* 挂进子系统的注册表 */
}
```

### §3.3 本树里的真实例子（每个子系统一套）

| 子系统 | `#xxx-cells` | 提供者注册 | xlate 回调 |
|---|---|---|---|
| clk | `#clock-cells` | `of_clk_add_provider()` | `struct clk_hw *(*get)(struct of_phandle_args *, void *)` |
| gpio | `#gpio-cells` | `gpiochip_add_data()` | `gc->of_xlate` |
| irq | `#interrupt-cells` | `irq_domain_add_*()` | `ops->xlate` / `ops->translate`（irq 那套 01 §5.2） |
| dma | `#dma-cells` | `of_dma_controller_register()` | `of_dma_xlate` |
| reset | `#reset-cells` | `reset_controller_register()` | `rcdev->of_xlate` |
| pinctrl | 无（用 `pinctrl-0` + 子节点） | —— | —— |

**五个子系统五套 xlate，但解析的入口是同一个**：
`of_parse_phandle_with_args()`（`drivers/of/base.c:1477`）。
⇒ 这就是"`drivers/of/` 只管解析、不管语义"的具体体现（§3.1）。

### §3.4 ⚠️`#xxx-cells` 的三条纪律

| 纪律 | 说明 |
|---|---|
| ① **一旦发布就不能改** | 它是稳定 ABI（02 §1.0.E）—— 老 DTB 里的 `<&prov 3>` 必须继续能解析 |
| ② **参数个数不足或过多都要报错** | 见 §3.2 的 `args_count != 2` 检查 |
| ③ **`0` 是合法值** | `#xxx-cells = <0>;` 表示"引用时不带参数"（如只有一路输出的 regulator） |

---

## §4 驱动 ↔ DTS 的契约

> DTS 的语法和常见属性**主体在 06 篇**。本节只讲**驱动作者要承担的义务**。

### §4.1 五条义务

| # | 义务 | 为什么 |
|---|---|---|
| ① **写绑定文档** | `Documentation/devicetree/bindings/…/myvendor,mydev.yaml` | 没有绑定文档的属性等于不存在（06 篇） |
| ② **属性名带 vendor 前缀** | `vendor,my-quirk` | 避免和将来的通用属性撞名 |
| ③ **新属性缺失时有合理默认** | §1.3 第二种模式 | **老 DTB 必须继续能工作**（02 §1.0.E） |
| ④ **不改已发布属性的语义** | —— | 同上 |
| ⑤ **不把驱动策略放进 DT** | 超时时间、缓冲区大小这类属于驱动 | 02 §1.0.E |

### §4.2 compatible 的写法

```dts
	/* 最具体的写在前面 */
	compatible = "myvendor,soc2-mydev", "myvendor,soc1-mydev";
```

**驱动只匹配 `"myvendor,soc1-mydev"` 时，soc2 的 DTB 也能工作** ——
因为 `of_device_is_compatible()` 会在整个列表里找（`drivers/of/base.c:558`）。
⇒ **这就是"向前兼容"的机制**：新 SoC 声明"我也兼容老的"。

⚠️ 但 `of_device_get_match_data()` 返回的是**匹配到的那一项**的 data ——
⇒ 匹配到老的那项就拿到老的 data。这通常正是你想要的。

### §4.3 ⚠️ 三个"DTS 写了但驱动没读"的坑

| 坑 | 判据 |
|---|---|
| 属性名拼错 | **驱动读到 `-EINVAL`，走了默认路径** ⇒ 07 篇教怎么查 |
| 属性写在了错误的节点上（父/子搞反） | 同上 |
| 属性名对但**没有绑定文档** ⇒ `dtbs_check` 报未知属性 | 06 §8 |

---

## §5 调试者

> 主体在 07 篇。这里只给**三条最常用的**。

```sh
# ① 看内核实际解析出来的树（04 §4）
ls /sys/firmware/devicetree/base/soc/
cat /sys/firmware/devicetree/base/soc/uart@12340000/compatible | tr '\0' '\n'
hexdump -C /sys/firmware/devicetree/base/soc/uart@12340000/reg

# ② 反编译 DTB 看展开后的值（宏都成了裸数字）
dtc -I dtb -O dts -o /tmp/x.dts /boot/mydev.dtb

# ③ 地址翻译失败时打开 pr_debug（03 §B4.2）
echo 'file address.c +p' > /sys/kernel/debug/dynamic_debug/control
```

**① 和 ② 的区别**（04 §4.4）：
① 是**内核内存里的树**（overlay 之后会变），② 是**bootloader 传的 DTB**（不变）。

---

## §6 常见错误速查

| 症状 | 最可能的原因 | 去哪一节 |
|---|---|---|
| probe 没被调用 | ① `compatible` 不匹配；② 节点 `status = "disabled"`；③ **父节点不在填充白名单里** | 02 §1.0.A.2 · 07 篇 |
| `dev->of_node` 是 NULL | 设备不是从 DT 造的 | §1.4 |
| `of_property_read_u32()` → `-EINVAL` | DTS 里没这个属性（或拼错、或写在别的节点上）；⚠️ 也可能是 `np` 为 NULL | §4.3 |
| → `-EOVERFLOW` | **元素个数不对** | 01 §14.2 |
| → `-ENODATA` | DTS 写成了 `prop;`（无值）却当整数读 | 同上 |
| 可选属性配了但没生效 | **代码用了 `if (ret)` 而不是 `if (ret == -EINVAL)`** ⇒ `-EOVERFLOW` 被吞了；⚠️ **判成 `-ENOENT` 更糟：那个分支永远不进** | §1.3 · 01 §14.2.1 |
| `platform_get_irq()` → `-EPROBE_DEFER` 一直不好 | 中断控制器驱动没编进内核，或 compatible 不匹配 | irq 那套 06 §9 |
| `devm_platform_ioremap_resource()` 失败 | ① 节点没 `reg`；② **父节点缺 `ranges`**；③ `#address-cells` 不对 | 03 §B4.2 · 06 篇 |
| 地址翻译出一个奇怪的值 | `#address-cells` / `#size-cells` 写错 | 03 §B4.1 |
| 子节点没变成 `platform_device` | 忘了 `devm_of_platform_populate()`，或没在 DTS 加 `simple-mfd` | §2 |
| phandle 解析失败（返回 NULL） | 分不清原因 —— 换 `of_parse_phandle_with_args()` 看错误码 | 01 §14.4 |
| `of_parse_phandle_with_args()` → `-EINVAL` | **提供者节点的 `#xxx-cells` 和 DTS 里给的参数个数不一致** | §3.4 |
| 内存泄漏（`OF_DYNAMIC` 开着时） | 忘了 `of_node_put()` | §1.5 · 01 §1.3 |
| `dtbs_check` 报未知属性 | 没写绑定文档，或属性名没带 vendor 前缀 | §4.1 |
| `/sys/firmware/devicetree/` 里出现 `foo#1` | **DTS 里有两个同名同 unit-address 的节点** | 04 §4.2 |

---

## §7 本篇覆盖了哪些角色、漏了哪些

| 角色 | 本篇给了什么 | 没给什么 |
|---|---|---|
| 消费者驱动 | 完整骨架（十步）+ 四种错误处理模式 + 三条铁律 | 具体子系统的 API（clk/gpio/dma 的取法各不相同） |
| 复合设备 | 两种做法 + 怎么选 | —— |
| 提供者 | 通用形状 + 五个子系统的对照 | **各子系统的注册 API 细节**（属于那些子系统） |
| DTS 作者 | 五条义务 + compatible 的写法 | **主体在 06 篇** |
| 调试者 | 三条最常用的命令 | **主体在 07 篇** |
| overlay / changeset 使用者 | **本篇不给骨架** —— arm64 上极少用（04 §1，99 §2.7） | —— |

⚠️ 再强调一次篇首的免责声明：**本篇代码全部未编译未运行**。

---

**下一篇**：06 篇讲 DTS 怎么写 —— 单元格约定、`reg`/`ranges`、`status`、
phandle、绑定文档，以及配错了怎么定位。
