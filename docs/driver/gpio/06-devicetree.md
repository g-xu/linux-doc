---
title: 设备树
---

# 06 · GPIO 子系统的设备树配置

> 目标架构 **arm64**。所有 DTS 举例都取自本树 `arch/arm64/boot/dts/`，读者可以直接 `git show` diff。
> ACPI 路径（`drivers/gpio/gpiolib-acpi.c`）按项目约定整体裁掉，裁剪范围记在 `99-evidence.md` §2.7。
> 本篇所有属性名都做过 `grep -n '"属性名"' drivers/gpio/*.c drivers/of/*.c`，**找不到消费者的属性会被显式标成「死字」**。

---

## §0 按任务找章节

| 我现在要做的事 | 去哪一节 |
|---|---|
| 我在写一个 **GPIO 控制器**的 DTS 节点，不知道要填哪些属性 | §2（提供方属性总表 → 逐条展开） |
| 我在写一个 **用 GPIO 的外设**节点，不知道属性该叫什么名字 | §3.1 后缀规则 + §3.2 名字对照 |
| DTS 里那第三个数字（flags cell）到底能填什么 | §4 完全展开表 |
| 我明明写了 `GPIO_ACTIVE_HIGH`，驱动读出来却是反的 | §5 五个 quirk（尤其 §5.1 regulator 强制反相） |
| 我想让某根线在开机时就被拉起来、不给任何驱动用 | §6 hog |
| 我照绑定文档写了某个属性，但完全没生效 | §7 死字属性清单 |
| 加了 `gpio-ranges` 但 pinctrl 没接上，也没有任何报错 | §8.3 + §9.4（**四条错误路径全部吞错误**） |
| 系统起来了，但 `/sys/kernel/debug/gpio` 里根本没有我的控制器 | §8 三层定位法 |
| 我拿到一条 dmesg，想反查是哪个属性写错了 | §10 错误消息 → 原因映射表 |
| 我想找一个能直接抄的完整例子 | §11 arm64 实例汇总 |

---

## §1 三方分工：谁读了什么

DTS 里跟 GPIO 有关的字符串，实际上分散在**三个层次**去解析。搞不清这一点，就会去 `drivers/gpio/` 里找一个永远找不到的属性名。

```
                        DTS 文本
                           │
  ┌────────────────────────┼────────────────────────┐
  │                        │                        │
第 1 层                  第 2 层                  第 3 层
OF 核心                  gpio 核心                具体控制器/消费者驱动
drivers/of/base.c        drivers/gpio/            drivers/pinctrl/…
                         gpiolib-of.c
                         gpiolib.c

#gpio-cells              gpio-ranges              gpio-controller
gpio-map                 gpio-ranges-group-names    （只有 12 个驱动自己读）
gpio-map-mask            gpio-reserved-ranges     snps,reset-active-low
gpio-map-pass-thru       gpio-line-names            （stmmac 的 quirk）
（phandle 解析本身）      ngpios                   spi-cs-high
                         gpio-hog / input /         （spi 的 quirk）
                         output-low / output-high / enable-active-high
                         line-name                  （regulator 的 quirk）
                         <con-id>-gpios / gpios
```

**第 1 层的关键事实**：`#gpio-cells` 不是 gpio 核心读的。`of_get_named_gpiod_flags()`（`drivers/gpio/gpiolib-of.c:239`）只调了一次 `of_parse_phandle_with_args_map(np, propname, "gpio", index)`（`:247`），把 `"gpio"` 作为 *stem name* 交给 OF 核心；OF 核心在 `drivers/of/base.c:1540` 用 `kasprintf("#%s-cells", stem_name)` **拼出**属性名，然后在 `:1353` / `:1564` / `:1608` 去读它。

**这就是为什么 `grep '"#gpio-cells"' drivers/gpio/` 只能在 hog 路径（`gpiolib-of.c:576`）里找到一处。**正常查找路径里这个字符串根本不以字面量存在——它是运行期拼出来的。同理 `gpio-map` / `gpio-map-mask` / `gpio-map-pass-thru` 三个属性名分别在 `drivers/of/base.c:1545`、`:1549`、`:1553` 拼出。

---

## §2 提供方（GPIO 控制器）节点

### §2.1 总表

| 属性 | 写在哪个节点 | 必填？ | 消费代码 | 备注 |
|---|---|---|---|---|
| `compatible` | 控制器节点 | 是 | 驱动的 `of_match_table` | 决定哪个驱动来 probe |
| `reg` | 控制器节点 | 看驱动 | 驱动自己（`devm_platform_ioremap_resource`） | gpio 核心不读 |
| `gpio-controller` | 控制器节点 | **约定上是，代码上不是** | 仅 `include/linux/gpio/driver.h:810` 的 `for_each_gpiochip_node` 宏，外加 12 个驱动 | 见 §2.2 |
| `#gpio-cells` | 控制器节点 | 是 | OF 核心（`drivers/of/base.c:1564`）；hog 路径另读一次（`gpiolib-of.c:576`） | 默认语义是 2，见 §2.3 |
| `ngpios` | 控制器节点 | 仅当驱动没填 `gc->ngpio` | `drivers/gpio/gpiolib.c:658` | 见 §2.4 |
| `gpio-line-names` | 控制器节点 | 否 | `drivers/gpio/gpiolib.c:375` / `:397` | 走 fwnode 通用属性，不是 `of_*`，见 §2.5 |
| `gpio-reserved-ranges` | 控制器节点 | 否 | `gpiolib-of.c:127`、`:876`、`:881`、`:883` | 越界对儿静默跳过，见 §2.6 |
| `gpio-ranges` | 控制器节点 | 仅 pinctrl 双身份控制器 | `gpiolib-of.c:906`、`:914` | 四条错误路径全吞，见 §2.7 |
| `gpio-ranges-group-names` | 控制器节点 | 仅 npins==0 的组形式 | `gpiolib-of.c:900`（属性名常量）、`:927`、`:957` | 见 §2.8 |
| `interrupt-controller` | 控制器节点 | 仅提供中断的控制器 | irq 子系统，非 gpio 核心 | gpio 核心只在 `gpiochip_add_irqchip()` 里建 domain |
| `#interrupt-cells` | 控制器节点 | 同上 | irq 子系统 | GPIO irqchip 惯例是 2：`<offset, trigger>` |
| `gpio-hog` + `input`/`output-low`/`output-high` + `line-name` + `gpios` | **控制器的子节点** | 否 | `gpiolib-of.c:661`、`:596-600`、`:608`、`:584` | 见 §6 |
| `gpio-map` / `gpio-map-mask` / `gpio-map-pass-thru` | nexus 节点 | 否 | `drivers/of/base.c:1545/1549/1553` | **arm64 DTS 零用户**，见 §7.1 |

**处理次序**（都在 `gpiochip_add_data_with_key()` 里，`drivers/gpio/gpiolib.c`）：

```
:658  ngpios                     ← 驱动没填 ngpio 时才读
:750  devprop_gpiochip_set_names()   ← gpio-line-names
:758  of_gpiochip_add()              ← 依次：gpio-reserved-ranges → gpio-ranges → gpio-hog
:778  gpiochip_add_pin_ranges()      ← add_pin_ranges 回调（DT 之后）
:782  acpi_gpiochip_add()            ← 本项目裁掉
:784  machine_gpiochip_add()         ← 板级查找表（见 05 §2）
:794  gpiochip_add_irqchip()         ← irqchip / irq_domain
```

`of_gpiochip_add()` 内部（`gpiolib-of.c:983-1010`）：

```
:986-987   chip->of_node 为空 → return 0（整个 DT 路径跳过）
:990-992   of_xlate 没填 → of_gpio_n_cells = 2; of_xlate = of_gpio_simple_xlate
:994-995   of_gpio_n_cells > MAX_PHANDLE_ARGS → -EINVAL
:998       of_gpiochip_init_valid_mask()   ← gpio-reserved-ranges
:1000      of_gpiochip_add_pin_range()     ← gpio-ranges
:1006      of_gpiochip_scan_gpios()        ← gpio-hog
```

**hog 在 `:758` 就被 request 了，而 irqchip 直到 `:794` 才建。**所以 hog 子节点里不可能拿到中断——`gpio-hog` 只能做输入/输出电平，这不是绑定文档的限制，是次序造成的物理限制。

### §2.2 `gpio-controller`：约定必填，代码不查

这是本篇第一个反直觉结论。所有绑定文档都要求控制器节点写 `gpio-controller;`，但 **gpio 核心从来不校验它**：

```
$ grep -rn '"gpio-controller"' --include=*.c --include=*.h .
include/linux/gpio/driver.h:810      ← for_each_gpiochip_node 宏
drivers/pinctrl/pinctrl-st.c:1042
drivers/pinctrl/pinctrl-st.c:1651
drivers/pinctrl/pinctrl-pistachio.c:1367
drivers/pinctrl/pinctrl-da9062.c:270
drivers/pinctrl/nuvoton/pinctrl-wpcm450.c:1037
drivers/power/supply/sbs-manager.c:274
drivers/input/touchscreen/ad7879.c:463
drivers/net/dsa/mt7530.c:2538
drivers/net/can/spi/mcp251x.c:586
drivers/gpu/drm/bridge/ti-sn65dsi86.c:1836
arch/powerpc/platforms/52xx/mpc52xx_gpt.c:325
sound/soc/ti/davinci-mcasp.c:1879
```

`drivers/gpio/` 一个都没有。唯一的通用消费点是那个宏：

```c
/* include/linux/gpio/driver.h:808-810 */
#define for_each_gpiochip_node(dev, child)					\
	device_for_each_child_node(dev, child)					\
		if (!fwnode_property_present(child, "gpio-controller")) {} else
```

它用于"一个 device 下挂多个 gpiochip 子节点"的形态（多 bank 控制器），拿它当子节点的筛选条件。

那些非 gpio 目录里的驱动读它是另一个用途：**"这个设备要不要顺便注册成 GPIO 控制器"的开关**。例如 `drivers/net/can/spi/mcp251x.c:586` —— MCP251x 是 CAN 控制器，只有 DTS 写了 `gpio-controller` 才额外注册 gpiochip。

**实践含义**：漏写 `gpio-controller` 时，`arm,pl061` 这类专职控制器照样能工作（因为没人检查），但 `mcp251x` 这类兼职控制器会静默不注册 gpiochip。**症状完全一样（consumer 拿不到 GPIO），根因完全不同。**判据是看 provider 驱动自己有没有 `grep gpio-controller`。

### §2.3 `#gpio-cells`：默认 2，但不是"必须 2"

默认值在 `of_gpiochip_add()` 里给：

```c
/* drivers/gpio/gpiolib-of.c:990-992 */
	if (!chip->of_xlate) {
		chip->of_gpio_n_cells = 2;
		chip->of_xlate = of_gpio_simple_xlate;
	}
```

也就是说 **`of_gpio_n_cells` 和 `of_xlate` 是一对，必须一起改**。默认的 `of_gpio_simple_xlate()`（`gpiolib-of.c:767`）语义是：

- cell[0] = 控制器内的 line offset
- cell[1] = flags（见 §4）

它自己也带两道检查：

```c
/* drivers/gpio/gpiolib-of.c:777-783（节选） */
	if (gc->of_gpio_n_cells < 2) {
		WARN_ON(1);
		return -EINVAL;
	}

	if (WARN_ON(gpiospec->args_count < gc->of_gpio_n_cells))
		return -EINVAL;
```

上界检查在 `of_gpiochip_add()`：`of_gpio_n_cells > MAX_PHANDLE_ARGS` 直接 `-EINVAL`（`:994-995`）。

还有一道更早的检查在 `of_xlate_and_get_gpiod_flags()`（`gpiolib-of.c:99`，cell 数校验在 `:105`）。

**DTS 里 `#gpio-cells` 的值和驱动里 `of_gpio_n_cells` 必须一致，两边都不会替对方兜底。**DTS 写 3 而驱动是默认的 2：`__of_parse_phandle_with_args` 按 3 切分 phandle 列表，`of_gpio_simple_xlate` 只看前 2 个——**不报错，但 offset 会错位**。这是本篇最难查的一类故障，因为 dmesg 是干净的。

### §2.4 `ngpios`：只在驱动没填时才读

```c
/* drivers/gpio/gpiolib.c:656-677（节选，中间 6 行注释已省略） */
	ngpios = gc->ngpio;
	if (ngpios == 0) {
		ret = device_property_read_u32(&gdev->dev, "ngpios", &ngpios);
		if (ret == -ENODATA)
			ngpios = 0;
		else if (ret)
			goto err_free_dev_name;

		gc->ngpio = ngpios;
	}

	if (gc->ngpio == 0) {
		chip_err(gc, "tried to insert a GPIO chip with zero lines\n");
		ret = -EINVAL;
		goto err_free_dev_name;
	}
```

三个要点：

1. **驱动里写了 `gc->ngpio = N` 的话，DTS 的 `ngpios` 完全不生效**——`if (ngpios == 0)` 直接短路。`gpio-pl061.c:331` 就是硬编码的，所以给 pl061 节点加 `ngpios` 是死字。
2. 用的是 `device_property_read_u32()`（fwnode 通用），不是 `of_property_read_u32()`。所以属性得挂在 `gdev->dev` 的 fwnode 上，也就是控制器节点本身。
3. 两边都没给 → `chip_err` + `-EINVAL`，probe 失败。这是**唯一**跟 GPIO 行数有关的硬失败。

紧接着还有一道软警告：

```c
/* drivers/gpio/gpiolib.c:679-681 */
	if (gc->ngpio > FASTPATH_NGPIO)
		chip_warn(gc, "line cnt %u is greater than fast path cnt %u\n",
			  gc->ngpio, FASTPATH_NGPIO);
```

`FASTPATH_NGPIO` 就是 `CONFIG_GPIOLIB_FASTPATH_LIMIT`（`gpiolib.c:69`）。超了只是让 `gpiod_get_array_value_complex()` 走 kmalloc 而不是栈上 bitmap（`gpiolib.c:2717-2722`、`:3011-3016`），功能不受影响。

**`ngpios = <205>` 这类大值会稳定刷一条 warn**，别当成配置错误。实例：`arch/arm64/boot/dts/socionext/uniphier-ld20.dtsi:356`。

### §2.5 `gpio-line-names`：给线起名字

```c
/* drivers/gpio/gpiolib.c:375-377 */
	count = fwnode_property_string_array_count(fwnode, "gpio-line-names");
	if (count < 0)
		return 0;
```

`count < 0` 直接 return 0——**没写这个属性不是错误**，也不打印任何东西。

真正会打印的是"写了但太短"：

```c
/* drivers/gpio/gpiolib.c:387-390 */
	if (count <= chip->offset) {
		dev_warn(&gdev->dev, "gpio-line-names too short (length %d), cannot map names for the gpiochip at offset %u\n",
			 count, chip->offset);
		return 0;
	}
```

`chip->offset`（`include/linux/gpio/driver.h:465`）是"一个 device 下多个 gpiochip"时本 chip 在整体命名空间里的起点。单 chip 设备 `offset == 0`，那这条 warn 只有 `count == 0` 才可能触发。

**命名数组按 `offset` 切片分给各个 chip**，所以多 bank 控制器要按全局线号一次性写完整个数组，不能每个 bank 各写一份。

**不生效但不报错的写法**：属性挂在了 consumer 节点而不是控制器节点上。这里读的是控制器自己的 fwnode。

in-tree 实例（87 项，带行内注释标号，末尾用空字符串占位）：

```dts
/* arch/arm64/boot/dts/amlogic/meson-axg-jethome-jethub-j1xx.dtsi:315-335 */
&gpio {
	gpio-line-names =
		"", "", "", "", "", // 0 - 4
		"", "", "", "", "", // 5 - 9
		"UserButton", "", "", "", "", // 10 - 14
		/* ... 中间同构行省略，每行 5 项 ... */
		"", "LedRed", "LedGreen", "Output3", "Output2", // 25 - 29
		/* ... */
		"1Wire", "ZigBeeRESET", "", "Input4", "Input3", // 40 - 44
		/* ... */
		"", ""; // 85-86
};
```

一共 **87** 项（`sed -n '316,340p' <该文件> | tr ',' '\n' | grep -c '"'` → 87），属性体占 `:317-334`。用不到的线一律写空串占位——**数组不能有洞，只能靠空串按下标跳过**。行内注释 `// 25 - 29` 是人工维护的，代码不读，改数组时很容易忘了同步。

### §2.6 `gpio-reserved-ranges`：屏蔽掉不能用的线

两个消费点，都在 `gpiolib-of.c`：

```c
/* :122-130  探测"要不要建 valid_mask" */
bool of_gpio_need_valid_mask(const struct gpio_chip *gc)
{
	int size;
	const struct device_node *np = gc->of_node;

	size = of_property_count_u32_elems(np,  "gpio-reserved-ranges");
	if (size > 0 && size % 2 == 0)
		return true;
	return false;
}
```

**`size % 2 == 0` 是唯一的格式检查，而且它只决定"要不要分配 mask"，不产生任何诊断。**写了奇数个 u32 → `of_gpio_need_valid_mask()` 返回 false → mask 根本不建 → **所有保留区间全部失效，dmesg 一片安静。**

真正打洞的是 `of_gpiochip_init_valid_mask()`（`:870`），读同一个属性三次（`:876` 数长度、`:881`/`:883` 逐对取值）。它对越界的 `(start, count)` 对儿是 `continue`（`:885-886`），也就是**部分生效**：前面合法的对儿打了洞，越界那一对静默丢弃。

实例：

```dts
/* arch/arm64/boot/dts/qcom/sm8450-hdk.dts:397-399 */
&tlmm {
	gpio-reserved-ranges = <28 4>, <36 4>;
};
```

语义是 `<起始线号, 条数>`，不是 `<起, 止>`。上面屏蔽的是 28~31 和 36~39。

### §2.7 `gpio-ranges`：把 GPIO 线映射到 pinctrl 的 pin

这是 pinctrl 双身份控制器（见 `04-extensions.md` §9 和 `05-usage.md` §7）唯一的 DT 入口。

```c
/* drivers/gpio/gpiolib-of.c:906-908 */
	if (!of_property_read_bool(np, "gpio-ranges") &&
	    chip->of_gpio_ranges_fallback) {
		return chip->of_gpio_ranges_fallback(chip, np);
	}
```

**DT 优先于回调**：属性存在就走 DT，属性不存在才轮到 `gc->of_gpio_ranges_fallback`（字段在 `include/linux/gpio/driver.h:545`）。本树只有 `pinctrl-msm.c` 用这个 fallback。

格式解析：

```c
/* drivers/gpio/gpiolib-of.c:914-915 */
		ret = of_parse_phandle_with_fixed_args(np, "gpio-ranges", 3,
				index, &pinspec);
```

**cell 数是硬编码的 3，不看任何 `#…-cells`**。三个 cell 是 `<gpio_offset, pin_offset, npins>`，phandle 指向 pinctrl 设备。

两种形式：

| 形式 | `npins`(args[2]) | `pin_offset`(args[1]) | 需要 group-names | 落到哪个函数 |
|---|---|---|---|---|
| 线性区间 | > 0 | 起始 pin 号 | 不需要（有也必须是空串） | `gpiochip_add_pin_range()`（`:935`） |
| 组形式 | **0** | **必须 0** | **必须有** | `gpiochip_add_pingroup_range()`（`:969`） |

**四条错误路径全部吞掉错误。**这是本篇最重要的一个坑：

```
:919-921   of_pinctrl_get() 返回 NULL → return -EPROBE_DEFER   ← 唯一往上传的软错误
:930       "Group name of numeric GPIO ranges must be the empty string." → break
:946       "Illegal gpio-range format."                        → break
:952       "GPIO group range requested but no gpio-ranges-group-names property." → break
:964       "Group name of GPIO group range cannot be the empty string." → break
:974       return 0;   ← 所有 break 都落到这里
```

也就是说：**四条 `pr_err` 打完之后函数返回 0，`of_gpiochip_add()` 认为成功，gpiochip 正常注册。**结果是"控制器在、线在、但 pinctrl 桥接部分或全部没建"。

只有两种情况会真正失败：`-EPROBE_DEFER`（`:921`，pinctrl 还没 probe，会重试）和 `gpiochip_add_pin_range()` / `gpiochip_add_pingroup_range()` 自己返回的错误（`:940-941`、`:970-971`，会 `return ret`）。

诊断决策树：

```
gpio-ranges 写了，但 pinctrl 没接上
        │
        ├─ dmesg 里有 "Illegal gpio-range format." ?
        │      └─ 是 → npins==0 但 pin_offset != 0。组形式要求两个都是 0
        │
        ├─ dmesg 里有 "GPIO group range requested but no ... property." ?
        │      └─ 是 → npins 写了 0 却想用线性形式；要么补 group-names，要么把 npins 填对
        │
        ├─ dmesg 里有 "Group name of numeric GPIO ranges must be the empty string." ?
        │      └─ 是 → 线性形式（npins>0）却给了非空 group name。多写了/写串了
        │
        ├─ dmesg 里有 "Group name of GPIO group range cannot be the empty string." ?
        │      └─ 是 → 组形式的 group-names 里对应下标是 ""
        │
        ├─ dmesg 里有 "could not create pin range" ?
        │      └─ 是 → 这条来自 gpiolib.c:1880，pinctrl 侧没找到对应 range，
        │             是「设备名字符串没匹配上」，不是 DT 格式问题（见 05 §7.2）
        │
        └─ 全都没有，dmesg 干净
               └─ 属性名拼错 / 挂错节点 → of_property_read_bool 直接 false，
                  一句话都不会打。用 §8.2 的 /proc/device-tree 核对
```

**注意 break 的位置**：`break` 跳出的是 `for (;; index++)` 循环，所以**第 N 条出错时，前 N-1 条已经生效了**。"一半映射生效"是完全可能的状态。

实例（线性形式，单条）：

```dts
/* arch/arm64/boot/dts/renesas/r8a77960.dtsi:333-346（节选） */
		gpio0: gpio@e6050000 {
			...
			gpio-ranges = <&pfc 0 0 16>;   /* :340 */
			...
		};
```

同文件 `:355`、`:370` 是 gpio1/gpio2 的同类写法。

### §2.8 `gpio-ranges-group-names`：组形式的名字数组

属性名在代码里是个局部常量：

```c
/* drivers/gpio/gpiolib-of.c:900 */
	static const char group_names_propname[] = "gpio-ranges-group-names";
```

它和 `gpio-ranges` **按下标一一对应**（`:927-929` 和 `:957-959` 都用 `of_property_read_string_index(np, group_names_propname, index, &name)`，`index` 就是 `gpio-ranges` 的条目下标）。

实例（三条区间全是组形式，npins 都是 0）：

```dts
/* arch/arm64/boot/dts/socionext/uniphier-ld20.dtsi:350-356 */
		gpio-ranges = <&pinctrl 0 0 0>,
			      <&pinctrl 96 0 0>,
			      <&pinctrl 160 0 0>;
		gpio-ranges-group-names = "gpio_range0",
					  "gpio_range1",
					  "gpio_range2";
		ngpios = <205>;
```

这个例子把 §2.4 / §2.7 / §2.8 三节的规则全用上了：三条 `<phandle 96 0 0>` 里 `npins=0` 且 `pin_offset=0`（满足 `:946` 的格式要求），三个非空 group name（满足 `:964`），`ngpios=205` 补上驱动没填的行数。

---

## §3 消费方（用 GPIO 的外设）节点

### §3.1 属性名怎么拼出来：后缀规则

消费方不写固定属性名，属性名是**运行期拼出来**的：

```c
/* drivers/gpio/gpiolib-of.c:504-526（节选） */
struct gpio_desc *of_find_gpio(struct device *dev, const char *con_id,
			       unsigned int idx, unsigned long *flags)
{
	char prop_name[32]; /* 32 is max size of property name */
	...
	/* Try GPIO property "foo-gpios" and "foo-gpio" */
	for (i = 0; i < ARRAY_SIZE(gpio_suffixes); i++) {
		if (con_id)
			snprintf(prop_name, sizeof(prop_name), "%s-%s", con_id,
				 gpio_suffixes[i]);
		else
			snprintf(prop_name, sizeof(prop_name), "%s",
				 gpio_suffixes[i]);

		desc = of_get_named_gpiod_flags(dev->of_node, prop_name, idx,
						&of_flags);

		if (!gpiod_not_found(desc))
			break;
	}
```

后缀表只有两项：

```c
/* drivers/gpio/gpiolib.h:81 */
static __maybe_unused const char * const gpio_suffixes[] = { "gpios", "gpio" };
```

所以规则是：

| 驱动里的 con_id | 尝试的属性名（按顺序） |
|---|---|
| `"reset"` | `reset-gpios` → `reset-gpio` |
| `"enable"` | `enable-gpios` → `enable-gpio` |
| `NULL` | `gpios` → `gpio` |

**con_id 不带后缀**。`devm_gpiod_get(dev, "reset", ...)` 对应 DTS 的 `reset-gpios`；写成 `devm_gpiod_get(dev, "reset-gpios", ...)` 会去找 `reset-gpios-gpios`，必然找不到。这一条已在 `05-usage.md` §1.1 讲过，此处从 DTS 侧再钉一次。

**`prop_name` 是 32 字节的栈数组**（`gpiolib-of.c:507`，注释原文 `/* 32 is max size of property name */`）。con_id 太长会被 `snprintf` 静默截断——不会溢出，但会去找一个错误的属性名。可用长度是 32 − 1（NUL）− 6（`-gpios`）= **25 字符**。

**单数后缀是兼容用的，新 DTS 一律写复数**。`gpio_suffixes[]` 全树共 4 个非 ACPI 消费点：`gpiolib-of.c:69`（`of_gpio_spi_cs_get_count()`）、`:514`（`of_find_gpio()`）、`gpiolib.c:3921`（`fwnode_gpiod_get_index()`），以及 `gpiolib-acpi.c:922`/`:1431`（本项目裁掉）。

### §3.2 in-tree arm64 实例

```dts
/* arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts:115-123 */
	wl1835_pwrseq: wl1835-pwrseq {
		compatible = "mmc-pwrseq-simple";
		/* WLAN_EN GPIO */
		reset-gpios = <&gpio0 5 GPIO_ACTIVE_LOW>;
		clocks = <&pmic>;
		clock-names = "ext_clock";
		post-power-on-delay-ms = <10>;
		power-off-delay-us = <10>;
	};
```

同一份 DTS 里其他形态：

| 行 | 写法 | 说明 |
|---|---|---|
| `:110` | `gpio = <&gpio0 7 0>;` | regulator-fixed 的单数无后缀形式（con_id 为 NULL 时的第二次尝试）。见 §5.1 |
| `:118` | `reset-gpios = <&gpio0 5 GPIO_ACTIVE_LOW>;` | 标准消费者写法 |
| `:130`、`:136`、`:142`、`:148`、`:155`、`:162` | `gpios = <&gpio4 0 0>;` | leds 子节点，con_id 为 NULL |
| `:174` | `pmic-gpios = <&gpio1 2 GPIO_ACTIVE_HIGH>;` | con_id = `"pmic"` |
| `:274` | `enable-gpios = <&gpio1 7 GPIO_ACTIVE_HIGH>;` | con_id = `"enable"` |
| `:326` | `cd-gpios = <&gpio1 0 1>;` | con_id = `"cd"`；flags 用裸 `1` 而不是宏，等价于 `GPIO_ACTIVE_LOW` |
| `:509` | `pd-gpios = <&gpio0 4 0>;` | con_id = `"pd"` |

**`cd-gpios = <&gpio1 0 1>` 这种裸数字写法在本树里很常见**，读 DTS 时要能把数字翻回 §4 的位含义。

### §3.3 把 GPIO 当中断用：`interrupts-extended`

这条路径**完全不经过 gpiolib 的 DT 代码**，走的是 irq 子系统的 `#interrupt-cells`：

```dts
/* arch/arm64/boot/dts/freescale/imx8mm-kontron-bl-osm-s.dts:88-94（节选） */
	can@0 {
		compatible = "microchip,mcp251xfd";
		reg = <0>;
		pinctrl-names = "default";
		pinctrl-0 = <&pinctrl_can>;
		clocks = <&osc_can>;
		interrupts-extended = <&gpio4 28 IRQ_TYPE_LEVEL_LOW>;
```

三个必须分清的点：

| | `<con>-gpios` | `interrupts-extended` |
|---|---|---|
| cell 语义 | `<offset, GPIO_* flags>` | `<offset, IRQ_TYPE_* trigger>` |
| cell 数由谁决定 | `#gpio-cells` | `#interrupt-cells` |
| 谁解析 | `gpiolib-of.c` + OF 核心 | `kernel/irq/` + irq_domain |
| 拿到什么 | `struct gpio_desc *` | Linux virq 号 |
| 极性怎么表达 | `GPIO_ACTIVE_LOW` = 逻辑反相 | `IRQ_TYPE_LEVEL_LOW` = 触发沿/电平 |

**`GPIO_ACTIVE_LOW` 和 `IRQ_TYPE_LEVEL_LOW` 是两套独立的东西，互不影响。**同一根线既当 GPIO 又当中断时，两处都要各自写对；`GPIO_ACTIVE_LOW` 不会让中断自动变成低电平触发。

同一根线同时被 `-gpios` 和 `interrupts-extended` 引用是合法的（`gpiod_to_irq()` 就是为此存在，见 `05-usage.md` §1.5），但要注意 `FLAG_USED_AS_IRQ` 带来的方向锁定，见 `03-internals.md` §B4。

---

## §4 flags cell 完全展开

DTS 里那第三个数字（`#gpio-cells = <2>` 时是第二个 cell），经历**三次形态转换**：

```
   DTS 数值                of_gpio_flags            GPIO_LOOKUP_FLAGS
（dt-bindings 宏）      （enum of_gpio_flags）       （gpiolib 内部）
        │                        │                         │
        │  of_xlate 原样搬       │  of_convert_gpio_flags   │
        └───────────────────────>└────────────────────────>┘
                                  gpiolib-of.c:293
```

### §4.1 第一形态：DTS 宏

`include/dt-bindings/gpio/gpio.h` 全文数值（逐字抄录）：

| 宏 | 值 | 位 |
|---|---|---|
| `GPIO_ACTIVE_HIGH` | 0 | — |
| `GPIO_ACTIVE_LOW` | 1 | bit 0 |
| `GPIO_PUSH_PULL` | 0 | — |
| `GPIO_SINGLE_ENDED` | 2 | bit 1 |
| `GPIO_LINE_OPEN_SOURCE` | 0 | — |
| `GPIO_LINE_OPEN_DRAIN` | 4 | bit 2 |
| `GPIO_OPEN_DRAIN` | `GPIO_SINGLE_ENDED \| GPIO_LINE_OPEN_DRAIN` = 6 | bit 1+2 |
| `GPIO_OPEN_SOURCE` | `GPIO_SINGLE_ENDED \| GPIO_LINE_OPEN_SOURCE` = 2 | bit 1 |
| `GPIO_PERSISTENT` | 0 | — |
| `GPIO_TRANSITORY` | 8 | bit 3 |
| `GPIO_PULL_UP` | 16 | bit 4 |
| `GPIO_PULL_DOWN` | 32 | bit 5 |
| `GPIO_PULL_DISABLE` | 64 | bit 6 |

**五个宏的值是 0**（`ACTIVE_HIGH` / `PUSH_PULL` / `LINE_OPEN_SOURCE` / `PERSISTENT`，加上组合出来的 `GPIO_OPEN_SOURCE` 里的那一份）。写它们纯粹是自我说明，跟不写完全等价。所以 `<&gpio0 7 0>` 和 `<&gpio0 7 GPIO_ACTIVE_HIGH>` 是同一份 DTB。

**`GPIO_OPEN_SOURCE` 和 `GPIO_SINGLE_ENDED` 数值相同（都是 2）**，因为 `GPIO_LINE_OPEN_SOURCE` 是 0。名字很像的这一对必须靠语义区分：`GPIO_SINGLE_ENDED` 单独出现时的意思就是 open source。

### §4.2 第二形态：`enum of_gpio_flags`

```c
/* include/linux/of_gpio.h:26-32 */
	OF_GPIO_ACTIVE_LOW = 0x1,     /* :26 */
	OF_GPIO_SINGLE_ENDED = 0x2,   /* :27 */
	OF_GPIO_OPEN_DRAIN = 0x4,     /* :28 */
	OF_GPIO_TRANSITORY = 0x8,     /* :29 */
	OF_GPIO_PULL_UP = 0x10,       /* :30 */
	OF_GPIO_PULL_DOWN = 0x20,     /* :31 */
	OF_GPIO_PULL_DISABLE = 0x40,  /* :32 */
```

**位定义和 §4.1 的 DTS 宏逐位相同**——这是刻意的，`of_gpio_simple_xlate()` 把 cell 值原样赋给 `*flags`，不做任何映射。

### §4.3 第三形态：`of_convert_gpio_flags()`

```
drivers/gpio/gpiolib-of.c:293   of_convert_gpio_flags()
```

它从 `GPIO_LOOKUP_FLAGS_DEFAULT` 起步，逐位翻译成 gpiolib 内部的 `GPIO_*` lookup flags。关键的非平凡映射：

| of_gpio_flags 输入 | 输出 |
|---|---|
| `SINGLE_ENDED` + `OPEN_DRAIN` | `GPIO_OPEN_DRAIN` |
| `SINGLE_ENDED` 单独 | `GPIO_OPEN_SOURCE` |
| `ACTIVE_LOW` | `GPIO_ACTIVE_LOW` |
| `TRANSITORY` | `GPIO_TRANSITORY` |
| `PULL_UP` / `PULL_DOWN` / `PULL_DISABLE` | 各自对应的 `GPIO_PULL_*` |

定义在 `:293`（上一行 `:292` 的注释原文：`/* Converts gpio_lookup_flags into bitmask of GPIO_* values */`，注释把方向写反了——入参类型是 `enum of_gpio_flags` 而不是 lookup flags）。三个调用点：`:356`（`of_get_named_gpiod_flags()` 的返回路径）、`:536`（`of_find_gpio()`）、`:594`（`of_parse_own_gpio()`，hog 路径）。

**`OF_GPIO_OPEN_DRAIN`（0x4）单独设置而不带 `SINGLE_ENDED` 是无意义的**——`of_convert_gpio_flags()` 只在 `SINGLE_ENDED` 成立的分支里才看它。DTS 直接写 `4`（而不是 `GPIO_OPEN_DRAIN` = 6）会静默退化成 push-pull。

**flags 里的 `PULL_*` 是给 provider 的建议，不是保证**。它最终变成 `gpio_desc` 的 lookup flag，由 `gpiod_configure_flags()` 转成 `set_config()` 调用；provider 没实现 `set_config` 就静默丢弃。这条链在 `03-internals.md` §A4 展开。

---

## §5 五个 quirk：DTS 写的不一定是驱动拿到的

gpiolib 的 DT 路径里有两组"历史包袱"代码，会**改写**你在 DTS 里写的东西。不知道它们存在，就会遇到"我明明写了 ACTIVE_HIGH"这类完全无法自证的问题。

### §5.1 flags 改写：`of_gpio_flags_quirks()`

函数在 `drivers/gpio/gpiolib-of.c:133`，从 `of_get_named_gpiod_flags()` 的 `:266` 调用（**在 xlate 之后、`of_convert_gpio_flags()` 之前**）。四段独立的 `if`，可以叠加生效：

| # | 触发条件 | 行为 | 行号 |
|---|---|---|---|
| 1 | `CONFIG_REGULATOR` **且**（compatible 是 `regulator-fixed` 或 `reg-fixed-voltage`，**或者** con_id 是 `enable-gpio`/`enable-gpios` 且 compatible 是 `regulator-gpio`） | **`enable-active-high` 属性单独决定极性，phandle flags 被强制忽略** | `:142-163` |
| 2 | `CONFIG_REGULATOR` 且 compatible 是 `reg-fixed-voltage` 且有 `gpio-open-drain` 属性 | 补上 `SINGLE_ENDED\|OPEN_DRAIN`，打一条 `pr_info` | `:167-173` |
| 3 | `CONFIG_SPI_MASTER` 且 propname 恰好是 `cs-gpios` 且该节点有 `cs-gpios` | 按 `reg` 匹配子节点，用子节点的 `spi-cs-high` 反过来定极性 | `:180-217` |
| 4 | `CONFIG_STMMAC_ETH` 且 propname 恰好是 `snps,reset-gpio` 且有 `snps,reset-active-low` | 强制 `OF_GPIO_ACTIVE_LOW` | `:222-225` |

**quirk 1 是最容易踩的。**代码把意图写在注释里：

```c
/* drivers/gpio/gpiolib-of.c:148-162 */
		bool active_low = !of_property_read_bool(np,
							 "enable-active-high");
		/*
		 * The regulator GPIO handles are specified such that the
		 * presence or absence of "enable-active-high" solely controls
		 * the polarity of the GPIO line. Any phandle flags must
		 * be actively ignored.
		 */
		if ((*flags & OF_GPIO_ACTIVE_LOW) && !active_low) {
			pr_warn("%s GPIO handle specifies active low - ignored\n",
				of_node_full_name(np));
			*flags &= ~OF_GPIO_ACTIVE_LOW;
		}
		if (active_low)
			*flags |= OF_GPIO_ACTIVE_LOW;
```

真值表（`ACTIVE_LOW` 指 phandle 第 2 个 cell 的 bit 0）：

| DTS phandle flags | 有 `enable-active-high`？ | 最终极性 | 有诊断吗 |
|---|---|---|---|
| `GPIO_ACTIVE_HIGH`(0) | 否 | **ACTIVE_LOW**（被强制加上，`:162`） | **无。完全静默** |
| `GPIO_ACTIVE_HIGH`(0) | 是 | ACTIVE_HIGH | 无 |
| `GPIO_ACTIVE_LOW`(1) | 否 | ACTIVE_LOW | 无 |
| `GPIO_ACTIVE_LOW`(1) | 是 | ACTIVE_HIGH（被强制去掉，`:159`） | `pr_warn "GPIO handle specifies active low - ignored"` |

**第一行就是本树的真实例子**：

```dts
/* arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts:104-113 */
	reg_5v_hub: regulator@2 {
		compatible = "regulator-fixed";
		regulator-name = "5V_HUB";
		regulator-min-microvolt = <5000000>;
		regulator-max-microvolt = <5000000>;
		regulator-boot-on;
		gpio = <&gpio0 7 0>;          /* :110  flags = 0 = GPIO_ACTIVE_HIGH */
		regulator-always-on;
		vin-supply = <&reg_sys_5v>;
	};
```

DTS 明写 flags `0`（active high），没有 `enable-active-high`，所以 `active_low = true`，`:162` 把 `OF_GPIO_ACTIVE_LOW` 加上——**驱动拿到的是 active-low，而 dmesg 里一个字都不会打**。这个节点本身是对的（硬件确实是低有效，注释就在 `:140` 的 `Note that active low is the default.`），但如果你想要 active high 而在 phandle 里写 `GPIO_ACTIVE_HIGH`，那是无效的，必须加 `enable-active-high;`。

**quirk 1 的第三个分支只对 `regulator-gpio` 且 con_id 是 `enable-gpio(s)` 生效**（`:145-147`），`regulator-gpio` 的其他 GPIO（如 `gpios` 电压选择线）不受影响。

**quirk 3 和 quirk 4 靠 `strcmp(propname, ...)` 精确匹配属性名**，所以只有那两个特定属性名会触发；换个名字就绕过了 quirk（同时也就丢了那份兼容语义）。

### §5.2 属性名兜底：`of_find_gpio_quirks[]`

当按 §3.1 规则拼出的属性名都找不到时，`of_find_gpio()` 会依次试 5 个 hack（`gpiolib-of.c:530-531`，循环条件是 `gpiod_not_found(desc) && *q`）：

```c
/* drivers/gpio/gpiolib-of.c:495-503 */
static const of_find_gpio_quirk of_find_gpio_quirks[] = {
	of_find_spi_gpio,
	of_find_spi_cs_gpio,
	of_find_regulator_gpio,
	of_find_arizona_gpio,
	of_find_usb_gpio,
	NULL
};
```

| 函数 | 行 | 门槛 Kconfig | 触发条件 | 实际去读的属性 |
|---|---|---|---|---|
| `of_find_spi_gpio` | `:373` | `CONFIG_SPI_MASTER` | compatible 是 `spi-gpio` 且 con_id 非空 | `gpio-<con_id>`，即 `gpio-sck` / `gpio-mosi` / `gpio-miso`（注释 `:391`，`snprintf` `:392`） |
| `of_find_spi_cs_gpio` | `:402` | `CONFIG_SPI_MASTER` | con_id 是 `cs` 且 compatible ∈ {`fsl,spi`, `aeroflexgaisler,spictrl`, `ibm,ppc4xx-spi`} | `gpios`（`:424`） |
| `of_find_regulator_gpio` | `:432` | `CONFIG_REGULATOR` | con_id ∈ {`wlf,ldoena`, `wlf,ldo1ena`, `wlf,ldo2ena`}（白名单在 `:438-442`） | con_id 本身，不加后缀 |
| `of_find_arizona_gpio` | `:458` | `CONFIG_MFD_ARIZONA` | con_id 恰好是 `wlf,reset`（`:466`） | con_id 本身 |
| `of_find_usb_gpio` | `:472` | `CONFIG_TYPEC_FUSB302` | con_id 恰好是 `fcs,int_n`（`:485`） | con_id 本身 |

**注意后三个的形态：属性名 = con_id，一点后缀都不加。**它们存在的唯一原因是"当年的 DTS 写错了、已经 ship 出去了"，代码注释直说了（`gpiolib-of.c:479-480`）：`an undocumented DT GPIO line named "fcs,int_n"` / `without the compulsory "-gpios" suffix`。

**`of_find_spi_gpio` 的属性名是 `gpio-` 前缀而不是 `-gpios` 后缀**（`gpio-sck` 不是 `sck-gpios`）——前后缀位置反了。这是 §3.1 后缀规则的唯一例外，只对 `spi-gpio` 生效。

**arm64 上这 5 个 quirk 全部由 Kconfig 门控，但都不是 arm64 特有的**。写新 DTS 时不要利用它们：它们只是让老 DTB 继续能用，绑定文档不会记载它们。

---

## §6 hog：让线在启动时就被占住

`gpio-hog` 是**唯一写在控制器子节点里**的 GPIO 配置形态。作用是：gpiolib 在注册控制器时立刻 request 这根线并置好电平，不给任何驱动机会。用于板级的复位/使能线，且没有对应驱动。

### §6.1 语法

```dts
/* arch/arm64/boot/dts/renesas/hihope-common.dtsi:150-156 */
&gpio6 {
	usb1-reset-hog {
		gpio-hog;
		gpios = <10 GPIO_ACTIVE_LOW>;
		output-low;
		line-name = "usb1-reset";
	};
};
```

| 属性 | 必填 | 消费点 | 说明 |
|---|---|---|---|
| `gpio-hog` | **是** | `gpiolib-of.c:661`（注册路径）、`:717`（overlay 路径） | bool。没有它整个子节点被忽略 |
| `gpios` | **是** | `gpiolib-of.c:584` | **不带 phandle**，见下 |
| `input` / `output-low` / `output-high` | **是，三选一** | `:596` / `:598` / `:600` | 一个都没有 → `-EINVAL`，见 §6.3 |
| `line-name` | 否 | `:608` | 缺省用子节点名（`:608-609` 的 `*name = np->name`） |

### §6.2 hog 的 `gpios` 语法和消费者的完全不同

```c
/* drivers/gpio/gpiolib-of.c:576-587 */
	ret = of_property_read_u32(chip_np, "#gpio-cells", &tmp);
	if (ret)
		return ERR_PTR(ret);

	gpiospec.np = chip_np;
	gpiospec.args_count = tmp;

	for (i = 0; i < tmp; i++) {
		ret = of_property_read_u32_index(np, "gpios", idx * tmp + i,
						 &gpiospec.args[i]);
		if (ret)
			return ERR_PTR(ret);
	}
```

对比：

| | 消费者节点的 `<con>-gpios` | hog 子节点的 `gpios` |
|---|---|---|
| 写法 | `<&gpio0 5 GPIO_ACTIVE_LOW>` | `<10 GPIO_ACTIVE_LOW>` |
| 有 phandle 吗 | **有** | **没有**——父节点就是控制器，隐含 |
| 谁解析 | OF 核心的 `of_parse_phandle_with_args_map`（`:247`） | 手工循环 `of_property_read_u32_index`（`:584`） |
| `#gpio-cells` 从哪读 | OF 核心自己拼名字去读 | `of_parse_own_gpio()` 显式读父节点的（`:576`） |
| 多个条目怎么排 | phandle 列表 | 扁平 u32 数组，第 idx 条从 `idx * #gpio-cells` 开始（`:584`） |

**这是本篇最容易写错的一条。**在 hog 子节点里写 `gpios = <&gpio6 10 GPIO_ACTIVE_LOW>` 会把 phandle 值当成 line offset —— phandle 是个大整数，`of_gpio_simple_xlate()` 里 offset 超界返回 `-EINVAL`，hog 添加失败。

### §6.3 状态必填，且失败方式分两种

```c
/* drivers/gpio/gpiolib-of.c:595-605 */
	if (of_property_read_bool(np, "input"))
		*dflags |= GPIOD_IN;
	else if (of_property_read_bool(np, "output-low"))
		*dflags |= GPIOD_OUT_LOW;
	else if (of_property_read_bool(np, "output-high"))
		*dflags |= GPIOD_OUT_HIGH;
	else {
		pr_warn("GPIO line %d (%pOFn): no hogging state specified, bailing out\n",
			desc_to_gpio(desc), np);
		return ERR_PTR(-EINVAL);
	}
```

**`if / else if / else if` 是互斥的**：同时写 `output-low` 和 `output-high`，第一个胜出（low），不报冲突。

**`*dflags` 初值是 `GPIOD_ASIS`（`:574`），三个状态是 `|=` 上去的**。`GPIOD_ASIS` 是 0，所以实际等价于赋值，但 `input` 分支得到的是 `GPIOD_IN`——参见 `05-usage.md` §1.2 关于 `GPIOD_ASIS` 不读方向的说明。

失败传播分两条路：

| 触发时机 | 调用链 | 失败后果 |
|---|---|---|
| 控制器注册时 | `of_gpiochip_add()` `:1006` → `of_gpiochip_scan_gpios()` `:655` → `of_gpiochip_add_hog()` `:621` | **返回值往上传**，`gpiochip_add_data_with_key()` 整个失败，控制器注册不上 |
| DT overlay 动态加载时 | `of_gpio_notify()` `:702` → `:727` `of_gpiochip_add_hog()` | 打 `pr_err("%s: failed to add hogs for %pOF\n", ...)`（`:729`），overlay 加载失败 |

**hog 写错会让整个 GPIO 控制器 probe 失败**，症状是"整条总线上的所有 GPIO 都没了"，很容易误判成控制器驱动的问题。定位方法见 §9.1。

**hog 拿不到中断**：`of_gpiochip_scan_gpios()` 在 `:1006`，而 `gpiochip_add_irqchip()` 在 `gpiolib.c:794`，hog 跑的时候 irq_domain 还不存在。

---

## §7 死字属性清单

绑定文档、网上的例子、别的 SoC 的 DTS 里会出现一些在本树 gpio 路径上**没有任何消费者**的属性。逐条做过 `grep`：

### §7.1 `gpio-map` / `gpio-map-mask` / `gpio-map-pass-thru`

nexus 节点（GPIO 转接板）的重映射机制。属性名由 `drivers/of/base.c:1545` / `:1549` / `:1553` 用 `kasprintf` 拼出，`of_parse_phandle_with_args_map()` 会去读——所以**机制是活的**。

但：

```
$ grep -rln 'gpio-map' arch/arm64/boot/dts/
（无输出）
```

**arm64 DTS 零用户。**机制可用，但本树在 arm64 上没有任何范例可抄，也就没有任何回归测试覆盖。用它属于开荒。

### §7.2 逐条判定

| 属性 | 判定 | 依据 |
|---|---|---|
| `gpio-controller` | **核心不校验**（半死字） | §2.2：`drivers/gpio/` 里零消费点 |
| `ngpios`（当驱动硬编码了 `gc->ngpio`） | **死字** | `gpiolib.c:657` 的 `if (ngpios == 0)` 短路 |
| `gpio-line-names`（挂在 consumer 节点上） | **死字** | `gpiolib.c:370` 读的是 `dev_fwnode(&gdev->dev)`，即控制器自己 |
| `gpio-ranges-group-names`（线性形式，npins>0） | **必须是空串，否则整条链断** | `gpiolib-of.c:927-932` |
| `#gpio-cells` 写 1 | **provider 无法工作** | `of_gpio_simple_xlate()` `:777` 的 `WARN_ON(1)` + `-EINVAL` |
| `gpio-open-drain`（compatible 不是 `reg-fixed-voltage`） | **死字** | `gpiolib-of.c:168` 的 compatible 精确匹配 |
| `enable-active-high`（compatible 不是那三个 regulator） | **死字** | `gpiolib-of.c:142-147` 的条件 |
| `spi-cs-high`（父节点属性不叫 `cs-gpios`） | **在 gpio 侧是死字** | `gpiolib-of.c:180` 的 `strcmp(propname, "cs-gpios")`；spi 核心可能另有读法 |
| `of_mm_gpiochip_add_data()` 相关的 memory-mapped 形态 | **arm64 不涉及** | 4 个 in-tree 用户全在 powerpc/MIPS/nios2：`drivers/soc/fsl/qe/gpio.c`、`drivers/gpio/gpio-mpc5200.c`、`gpio-mm-lantiq.c`、`gpio-altera.c` |

**`gpio-hog` 写在消费者节点里是死字**（不报错、不生效）：`of_gpiochip_scan_gpios()` 只遍历**控制器节点的直接子节点**（`:660` 的 `for_each_available_child_of_node(chip->of_node, np)`）。

---

## §8 三层定位法

DTS 出问题时，按"排除范围最大的先做"的顺序走三层。

### §8.1 第一层：编译期（DTB 里到底有什么）

DTS 有 include、有 `&label {}` 覆盖、有 `/delete-property/`，你写的和编出来的经常不一样。**先证明 DTB 里的内容是你以为的那样**，再谈内核行为。

```bash
# 反编译目标 dtb，看最终形态
dtc -I dtb -O dts -o /tmp/final.dts arch/arm64/boot/dts/<vendor>/<board>.dtb

# 只看某个控制器节点
dtc -I dtb -O dts arch/arm64/boot/dts/<vendor>/<board>.dtb | sed -n '/gpio@f8011000/,/};/p'
```

三个高频问题：

1. **`&label {}` 覆盖没生效**：label 拼错时 dtc 会报 `Reference to non-existent node or label`，编译直接失败——这个不会静默。
2. **属性被两处赋值**：后出现的胜出。`.dtsi` 里的值被 `.dts` 覆盖是常态。
3. **`#gpio-cells` 和 phandle 里的 cell 数不匹配**：dtc **不校验**（它不知道 `#gpio-cells` 的语义）。这一层查不出来，要靠 §8.2。

### §8.2 第二层：运行期静态（内核看到的 DT）

DTB 加载完之后，`/proc/device-tree/` 是最权威的现场。

```bash
# 控制器节点的属性都在不在
ls /proc/device-tree/soc/gpio@f8011000/

# #gpio-cells 的实际值（大端 u32）
hexdump -C /proc/device-tree/soc/gpio@f8011000/\#gpio-cells

# consumer 侧的 phandle 列表原始字节
hexdump -C /proc/device-tree/wl1835-pwrseq/reset-gpios
```

**`#gpio-cells` vs phandle cell 数的错位，只能在这一层查出来。**`reset-gpios` 的字节数应该是 `4 * (1 + #gpio-cells)`。上面那个例子应该是 12 字节（phandle + offset + flags）；如果是 16 字节而 `#gpio-cells` 是 2，就是 DTS 多写了一个 cell。

属性名拼错的判据也在这一层：`ls /proc/device-tree/<consumer>/` 里根本没有 `reset-gpios` 这一项，而 §8.1 里你以为写了。

### §8.3 第三层：运行期动态（gpiolib 的内部状态）

到这一层才需要看 gpiolib 自己。**判据是"控制器注册上了没有"**：

```bash
# 控制器在不在（每个 gpiochip 一行）
ls /sys/bus/gpio/devices/
cat /sys/kernel/debug/gpio            # 需要 CONFIG_DEBUG_FS

# 线名字有没有生效（gpio-line-names 是否被读到）
gpioinfo                              # libgpiod
```

对照表：

| 现象 | 结论 | 下一步 |
|---|---|---|
| `/sys/bus/gpio/devices/` 里没有该控制器 | provider 侧问题：probe 失败或没匹配 | 查 dmesg 里驱动名；查 §9.1 |
| 有控制器，但 `ngpio` 数字不对 | `ngpios` 属性或驱动硬编码的问题 | §2.4 |
| 有控制器，线名全是空的 | `gpio-line-names` 没被读到 | §2.5 的两个静默失效点 |
| 有控制器、线正常，consumer 拿不到 | consumer 侧属性名 / cell 数问题 | §8.2 + §3.1 |
| 都正常，但电平方向是反的 | 极性问题 | §4 + §5.1 的 quirk 真值表 |
| pinctrl 复用配不上、`debugfs/pinctrl/*/gpio-ranges` 为空 | `gpio-ranges` 问题 | §2.7 的决策树 |

**`/sys/kernel/debug/gpio` 和 `gpioinfo` 都不显示 DT 层的东西**，它们只反映 gpiolib 已经建好的内部状态。DT 属性没被读到时，这两个工具只能告诉你"结果不对"，不能告诉你"哪个属性错了"——那要回到 §8.2。调试接口的完整清单见 `07-debug.md`。

---

## §9 症状清单（按坏掉的范围分组）

### §9.1 范围：整个控制器不见了

| 症状 | 可能原因 | 判据 |
|---|---|---|
| `/sys/bus/gpio/devices/` 里没有，dmesg 有 `tried to insert a GPIO chip with zero lines` | 驱动没填 `gc->ngpio` 且 DTS 没写 `ngpios` | §2.4；`gpiolib.c:674` |
| 没有，dmesg 有 `no hogging state specified, bailing out` | hog 子节点缺 `input`/`output-low`/`output-high` | §6.3；`gpiolib-of.c:603` |
| 没有，dmesg 有 hog 相关的 `-EINVAL`/`-ENOENT` 但没有上面那条 | hog 的 `gpios` 写错（最常见：多写了 phandle） | §6.2 |
| 没有，dmesg 里驱动一条日志都没有 | compatible 不匹配 / 驱动没编进内核 / `status = "disabled"` | 跟 gpio 无关，回到 §8.1 |
| 没有，且 provider 是"兼职"设备（CAN/PMIC/桥片） | 漏写 `gpio-controller` | §2.2 的 12 个驱动清单 |
| 反复 probe defer，最后超时 | `gpio-ranges` 指向的 pinctrl 一直没上来 | §2.7 的 `:921` |

### §9.2 范围：单个 consumer 拿不到 GPIO

| 症状 | 可能原因 | 判据 |
|---|---|---|
| `devm_gpiod_get()` 返回 `-ENOENT` | 属性名拼错 / con_id 带了 `-gpios` 后缀 / 属性挂错节点 | §3.1；`/proc/device-tree` 核对 |
| 返回 `-EPROBE_DEFER` 且不收敛 | 控制器节点在但控制器没注册上 | 先解 §9.1 |
| 返回 `-EBUSY` | 线已被别人占（hog、别的 consumer、pinctrl 复用） | `cat /sys/kernel/debug/gpio` 看 label |
| 返回 `-EINVAL` | offset 超出 `ngpio`，通常是 `#gpio-cells` 错位导致的 | §8.2 的字节数校验 |
| 返回成功但操作时 `-EIO`/`-ENOTSUPP` | 线在 `gpio-reserved-ranges` 里被屏蔽了 | §2.6 |

**`-ENOENT` 是 gpiolib 的"没找到"约定值**，判据是 `gpiod_not_found()`（`of_find_gpio()` `:530`、`:535` 都用它做循环条件）。不要和 `-ENODEV` 混。

### §9.3 范围：单根线的电平/方向不对

| 症状 | 可能原因 | 判据 |
|---|---|---|
| 电平整体反相 | phandle flags 的 `GPIO_ACTIVE_LOW` 写错/漏写 | §4.1 |
| 电平反相，且是 regulator/spi-cs/stmmac 相关 | **quirk 改写了极性** | §5.1，尤其那张真值表的第一行 |
| dmesg 有 `GPIO handle specifies active low - ignored` | DTS 里的极性被 quirk 主动丢弃 | `gpiolib-of.c:157` 或 `:205`，看哪一条 |
| dmesg 有 `enforce active low on chipselect handle` | spi cs 的极性被强制加上 | `gpiolib-of.c:211` |
| 期望 open-drain 但实测是推挽 | flags 只写了 bit 2（值 4）没带 `SINGLE_ENDED` | §4.3 的 |
| 上下拉不生效 | provider 没实现 `set_config()`，静默丢弃 | `03-internals.md` §A4 |
| dmesg 有 `uses legacy open drain flag - update the DTS if you can` | 用了老的 `gpio-open-drain` 属性 | `gpiolib-of.c:171`；改成 phandle flags 里的 `GPIO_OPEN_DRAIN` |

### §9.4 范围：拓扑/映射（pinctrl 桥接、保留区间、命名）

| 症状 | 可能原因 | 判据 |
|---|---|---|
| `gpio-ranges` 写了，dmesg 有四条 `pr_err` 之一 | 格式错。**注意错误被吞，控制器仍然注册成功** | §2.7 决策树 |
| `gpio-ranges` 写了，dmesg 完全干净，但映射没建 | 属性名拼错 / 挂错节点 → `of_property_read_bool` 直接 false | §2.7 决策树最后一支 |
| 只有一部分 range 生效 | 某一条出错 `break`，前面的已经生效 | §2.7 的 break 语义 |
| dmesg 有 `could not create pin range` | pinctrl 侧设备名字符串没匹配上，不是 DT 格式问题 | `gpiolib.c:1880`；`05-usage.md` §7.2 |
| `gpio-reserved-ranges` 完全不生效，无日志 | u32 个数是奇数 → `of_gpio_need_valid_mask()` 返回 false | §2.6；`gpiolib-of.c:128` |
| `gpio-reserved-ranges` 部分不生效，无日志 | 某一对越界，被 `continue` 静默跳过 | `gpiolib-of.c:885-886` |
| 线名字为空，dmesg 有 `gpio-line-names too short` | 数组长度 ≤ `chip->offset`，多 bank 控制器常见 | §2.5；`gpiolib.c:387-389` |
| 线名字为空，dmesg 有 `failed to read GPIO line names` | 属性存在但读取失败（类型不对，如写成了 u32 数组） | `gpiolib.c:399-400` |
| 线名字为空，dmesg 干净 | 属性名拼错 / 挂在 consumer 节点上 | §7.2 |
| overlay 加载失败，dmesg 有 `failed to add hogs for` | overlay 里的 hog 子节点写错 | `gpiolib-of.c:729` |

---

## §10 错误消息 → 原因映射表

把 `drivers/gpio/gpiolib-of.c` 和 `gpiolib.c` 里所有跟 DT 有关的打印全部列出。**`pr_debug` 需要 dynamic debug 打开才可见**（见 `07-debug.md` §1）。

| 消息（截取可 grep 的片段） | 位置 | 等级 | 原因 | 后果 |
|---|---|---|---|---|
| `GPIO handle specifies active low - ignored` | `gpiolib-of.c:157` | warn | regulator quirk：写了 `enable-active-high` 但 phandle 又标了 ACTIVE_LOW | 极性按 `enable-active-high` 走，phandle 被丢 |
| `uses legacy open drain flag - update the DTS if you can` | `gpiolib-of.c:171` | info | `reg-fixed-voltage` 用了老的 `gpio-open-drain` 属性 | 自动补上 OPEN_DRAIN，功能正常 |
| `GPIO handle specifies active low - ignored` | `gpiolib-of.c:205` | warn | spi cs quirk：子节点有 `spi-cs-high` 但 phandle 又标了 ACTIVE_LOW | 按 `spi-cs-high` 走 |
| `enforce active low on chipselect handle` | `gpiolib-of.c:211` | info | spi cs quirk：子节点没有 `spi-cs-high`，强制 active low | 极性被改写 |
| `can't parse '%s' property of node '%pOF[%d]'` | `gpiolib-of.c:250` | **debug** | 属性不存在，或 phandle/cell 解析失败 | 返回 `ERR_PTR`；**默认不可见，这是最常见的静默失败** |
| `parsed '%s' property of node '%pOF[%d]' - status (%d)` | `gpiolib-of.c:268` | debug | 成功路径的追踪点 | 无 |
| `GPIO line %d (%pOFn): no hogging state specified, bailing out` | `gpiolib-of.c:603` | warn | hog 子节点缺三选一的状态属性 | `-EINVAL`，**整个控制器注册失败** |
| `%s: failed to add hogs for %pOF` | `gpiolib-of.c:729` | err | overlay 动态加载路径上 hog 添加失败 | overlay 加载失败 |
| `%pOF: GPIO chip registration failed with status %d` | `gpiolib-of.c:848` | err | `of_mm_gpiochip_add_data()` 失败 | arm64 不涉及（4 个用户全在 powerpc/MIPS/nios2） |
| `%pOF: Group name of numeric GPIO ranges must be the empty string.` | `gpiolib-of.c:930` | err | `gpio-ranges` 线性形式（npins>0）却给了非空 group name | **break + return 0**，后续 range 全丢，控制器仍注册成功 |
| `%pOF: Illegal gpio-range format.` | `gpiolib-of.c:946` | err | npins==0 但 pin_offset != 0 | 同上 |
| `%pOF: GPIO group range requested but no gpio-ranges-group-names property.` | `gpiolib-of.c:952` | err | npins==0 但没写 group-names | 同上 |
| `%pOF: Group name of GPIO group range cannot be the empty string.` | `gpiolib-of.c:964` | err | 组形式的 group-names 对应下标是 `""` | 同上 |
| `gpio-line-names too short (length %d), cannot map names for the gpiochip at offset %u` | `gpiolib.c:388-389` | warn | 命名数组长度 ≤ `chip->offset` | 该 chip 的线全部无名，其余正常 |
| `failed to read GPIO line names` | `gpiolib.c:400` | warn | 属性存在但 `fwnode_property_read_string_array` 失败 | 返回错误码，注册失败 |
| `tried to insert a GPIO chip with zero lines` | `gpiolib.c:674` | err(chip_err) | 驱动和 DTS 都没给行数 | `-EINVAL`，注册失败 |
| `line cnt %u is greater than fast path cnt %u` | `gpiolib.c:680-681` | warn(chip_warn) | `ngpio > CONFIG_GPIOLIB_FASTPATH_LIMIT` | 仅性能路径变化，功能正常 |
| `could not create pin range` | `gpiolib.c:1880` | err | `pinctrl_find_and_add_gpio_range()` 没匹配到设备名 | `gpio-ranges` 的 pinctrl 侧失败 |
| `%s unknown fwnode type proceed anyway` | `gpiolib.c:1088` | err(chip_err) | fwnode 既不是 OF 也不是 ACPI | 在**成功路径**上打印，见 `03-internals.md` §A2 |

**`gpiolib-of.c:250` 那条 `pr_debug` 是本篇最重要的一行。**"属性没找到"这件事默认完全静默——`devm_gpiod_get()` 返回 `-ENOENT`，consumer 驱动可能只是 `dev_err_probe` 一句自己的话，你根本不知道 gpiolib 去找的属性名是什么。打开它就能直接看到 gpiolib 拼出来的 `prop_name`：

```bash
echo 'file drivers/gpio/gpiolib-of.c +p' > /sys/kernel/debug/dynamic_debug/control
```

---

## §11 arm64 实例汇总

本篇引用的全部 in-tree 例子，可直接 `sed -n` 打开对照：

| 用途 | 文件 | 行 |
|---|---|---|
| 完整 provider 节点（pl061，20 个同类节点） | `arch/arm64/boot/dts/hisilicon/hi6220.dtsi` | `461-471` |
| consumer 的 `reset-gpios` | `arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts` | `115-123` |
| regulator quirk 强制反相的真实节点 | `arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts` | `104-113`（`gpio` 属性在 `:110`） |
| 各种 con_id 后缀形态（`pmic-`/`enable-`/`cd-`/`pd-`/裸 `gpios`） | `arch/arm64/boot/dts/hisilicon/hi6220-hikey.dts` | `110/118/130/136/142/148/155/162/174/274/326/509` |
| `gpio-keys` 消费者（用 `RK_PA5` 这类宏做 offset） | `arch/arm64/boot/dts/rockchip/rk3399-rockpro64.dtsi` | `39-52`（`gpios` 在 `:47`） |
| hog 子节点 | `arch/arm64/boot/dts/renesas/hihope-common.dtsi` | `150-156` |
| `gpio-line-names`（87 项，带行内标号） | `arch/arm64/boot/dts/amlogic/meson-axg-jethome-jethub-j1xx.dtsi` | `315-335`（属性体 `317-334`） |
| `gpio-ranges` 线性形式 | `arch/arm64/boot/dts/renesas/r8a77960.dtsi` | `340`（节点 `333-346`），另 `355`/`370` |
| `gpio-reserved-ranges` | `arch/arm64/boot/dts/qcom/sm8450-hdk.dts` | `397-399` |
| `gpio-ranges-group-names` 组形式 + `ngpios` | `arch/arm64/boot/dts/socionext/uniphier-ld20.dtsi` | `350-356` |
| GPIO 当中断用（`interrupts-extended`） | `arch/arm64/boot/dts/freescale/imx8mm-kontron-bl-osm-s.dts` | `88-94` |

可重跑的计数命令：

```bash
grep -c 'arm,pl061' arch/arm64/boot/dts/hisilicon/hi6220.dtsi                    # 20
grep -rln 'gpio-map' arch/arm64/boot/dts/                                        # 无输出（零用户）
grep -rn '"gpio-controller"' --include=*.c --include=*.h . | wc -l               # 13（含 driver.h 的宏）
grep -rn '"gpio-controller"' --include=*.c --include=*.h drivers/gpio/ | wc -l   # 0
grep -rln 'of_mm_gpiochip_add' --include=*.c drivers/ arch/arm64/                # 5（含 gpiolib-of.c 自身）
```

---

## §12 读下一篇

| 我还想知道 | 去哪一篇 |
|---|---|
| 这些属性被读进来之后，`gpio_desc` 里的 flag 是怎么变的 | `03-internals.md` §A2 / §A4 |
| DTS 之外还能怎么配（板级查找表 / hog 的代码等价物） | `05-usage.md` §2 |
| provider 驱动里那些 `of_*` 字段（`of_xlate` / `of_gpio_n_cells` / `of_gpio_ranges_fallback`）怎么用 | `02-datastructures.md` §3、`05-usage.md` §3 / §7 |
| 怎么打开 dynamic debug、看 tracepoint、读 debugfs | `07-debug.md` |
| `gpiolib-of.c` 在整个框架里的位置、和 cdev/sysfs/devres 的关系 | `04-extensions.md` §9 |
| 为什么"极性"要在 DT 里表达，而不是让驱动自己反 | `08-feynman.md` |
| 上面每条论断的取证记录 | `99-evidence.md` §3（06 篇逐条） |

