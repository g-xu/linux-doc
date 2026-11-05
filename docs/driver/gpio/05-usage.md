---
title: 使用指南
---

# 05 · gpio 子系统使用举例

## §0 使用本篇前必读

### §0.1 免责声明：所有骨架均未编译

> ⚠️ **本篇所有代码骨架都没有编译过，也没有在硬件上运行过。**
>
> 它们的用途是**说明调用次序、参数含义和易错点**，不是可直接粘贴使用的驱动。
> 具体做过什么、没做什么，见 `99-evidence.md` §5。

在此之上，本篇做了一件可机械验证的事：**正文出现的每一个内核函数名、宏名、标志名、结构体名、字段名，都在 linux-6.1.y 这棵树上 grep 过，确认本版本存在。** 凡是有近邻同名符号（例如 `gpiod_direction_output` vs `gpiod_direction_output_raw`）的地方，两个都写进来并点明区别——因为「名字很像」是这一篇最高危的信号。

骨架里省略的部分统一用 `/* ... */` 标出，不用省略号，避免被误当成合法 C 语法。

### §0.2 gpio 子系统有哪些角色

`gpio` 是框架型子系统，没有自己的 `bus_type`（判定过程见 `00-overview.md` §1.1）。因此角色不是「控制器 / 设备」两分，而是下面九种：

| # | 角色 | 你在写什么 | 关键入口 | 本篇章节 |
|---|------|-----------|---------|----------|
| 1 | **消费者驱动** | 一个用了一两根 GPIO 的设备驱动（复位脚、使能脚、中断脚） | `devm_gpiod_get*()` | §1 |
| 2 | **板级注册者** | 没有 DT 描述时，用 C 代码补上 GPIO 映射 | `gpiod_add_lookup_table()` | §2 |
| 3 | **提供方（最简）** | 一个 GPIO 控制器驱动，自己写全部回调 | `devm_gpiochip_add_data()` | §3 |
| 4 | **提供方（bgpio）** | 寄存器布局规整的 MMIO 控制器，让库填回调 | `bgpio_init()` + 同上 | §4 |
| 5 | **提供方（级联中断）** | 控制器的中断汇总到一根父 IRQ 线上 | `gc->irq.parent_handler` | §5 |
| 6 | **提供方（层级中断）** | 每根 GPIO 在父控制器里有独立的 hwirq | `gc->irq.parent_domain` | §6 |
| 7 | **pinctrl + gpio 双身份** | 同一块硬件既是 pinmux 又是 GPIO 控制器 | `gpiochip_add_pin_range()` | §7 |
| 8 | **用户态消费者** | 从应用里控 GPIO / 等边沿 | `/dev/gpiochipN` + ioctl | §8 |
| 9 | **（反例）整数 API 使用者** | 老代码在用，新代码不要写 | `gpio_request_one()` | §9 |

角色 3–7 不是互斥的：一个真实的 SoC GPIO 驱动通常同时是 4、5 或 6、以及 7。本篇按能力逐个叠加地讲，最后 §10 给一张「你的控制器该选哪几节」的路线图。

### §0.3 头文件怎么选

选错头文件是最常见的第一个编译错误，规则很简单：

| 你是 | include | 不要 include |
|---|---|---|
| 消费者驱动 | `<linux/gpio/consumer.h>` | `<linux/gpio.h>`（整数 API，见 §9） |
| 提供方驱动 | `<linux/gpio/driver.h>` | — |
| 板级代码 | `<linux/gpio/machine.h>` | — |
| 用户态 | `<linux/gpio.h>`（**uapi 的那一个**） | 内核的 `include/linux/gpio.h` |

最后一行是一个真陷阱：树里有**两个** `linux/gpio.h`——`include/linux/gpio.h`（内核内部的整数 legacy API）和 `include/uapi/linux/gpio.h`（用户态 ABI）。用户态程序写 `#include <linux/gpio.h>` 拿到的是后者。文档里凡是提到这个文件名，本篇一律写全路径。

---

## §1 角色 1：消费者驱动

这是 99% 的人要写的东西。

### §1.1 最小骨架：一根复位脚

```c
#include <linux/gpio/consumer.h>

struct my_dev {
	struct gpio_desc *reset;
	/* ... */
};

static int my_probe(struct platform_device *pdev)
{
	struct my_dev *md;
	/* ... 分配 md ... */

	/* con_id "reset" → DT 里找 "reset-gpios" 或 "reset-gpio" */
	md->reset = devm_gpiod_get(&pdev->dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(md->reset))
		return dev_err_probe(&pdev->dev, PTR_ERR(md->reset),
				     "failed to get reset GPIO\n");

	/* 拉出复位 */
	gpiod_set_value_cansleep(md->reset, 0);

	return 0;
}
```

四个要点：

1. **`con_id` 不带 `-gpios` 后缀。** 核心会自己拼：`of_find_gpio()` 循环 `gpio_suffixes[]`，先试 `"reset-gpios"` 再试 `"reset-gpio"`（`drivers/gpio/gpiolib-of.c:504-538`，逐行剖析见 `03-internals.md` §A4.1）。你传 `"reset-gpios"` 会去找 `reset-gpios-gpios`。
2. **返回值必须用 `IS_ERR()` 判，不能判 NULL。** `devm_gpiod_get()` 失败时返回 `ERR_PTR`，从不返回 NULL。
3. **`GPIOD_OUT_HIGH` 里的 "HIGH" 是逻辑值。** 它展开成 `GPIOD_FLAGS_BIT_DIR_SET | GPIOD_FLAGS_BIT_DIR_OUT | GPIOD_FLAGS_BIT_DIR_VAL`（`include/linux/gpio/consumer.h:46-72`），最终走 `gpiod_direction_output()`（**非** `_raw`），所以 DT 里写了 `GPIO_ACTIVE_LOW` 时电平会被反相。这是与 §9 整数 API 的根本差别。
4. **不用在 `remove()` 里释放。** `devm_` 前缀就是这个意思（`04-extensions.md` §4）。

### §1.2 `enum gpiod_flags` 的六个值

`include/linux/gpio/consumer.h:46-56`，kerneldoc 明确写了 **These values cannot be OR'd**：

| 值 | 展开 | 效果 |
|---|---|---|
| `GPIOD_ASIS` | 0 | 什么都不改（连方向都不设） |
| `GPIOD_IN` | `DIR_SET` | 设为输入 |
| `GPIOD_OUT_LOW` | `DIR_SET \| DIR_OUT` | 输出，逻辑低 |
| `GPIOD_OUT_HIGH` | `DIR_SET \| DIR_OUT \| DIR_VAL` | 输出，逻辑高 |
| `GPIOD_OUT_LOW_OPEN_DRAIN` | `GPIOD_OUT_LOW \| OPEN_DRAIN` | 开漏输出，逻辑低 |
| `GPIOD_OUT_HIGH_OPEN_DRAIN` | `GPIOD_OUT_HIGH \| OPEN_DRAIN` | 开漏输出，逻辑高 |

底层的五个位定义在 `:28-32`：`GPIOD_FLAGS_BIT_DIR_SET` BIT(0)、`DIR_OUT` BIT(1)、`DIR_VAL` BIT(2)、`OPEN_DRAIN` BIT(3)、`NONEXCLUSIVE` BIT(4)。

**`GPIOD_FLAGS_BIT_NONEXCLUSIVE` 没有对应的 `GPIOD_*` 枚举值**——它不在这六个里，要用只能自己 `GPIOD_IN | GPIOD_FLAGS_BIT_NONEXCLUSIVE` 这样拼，而这恰恰违反了「cannot be OR'd」这句话。这个矛盾不是笔误，是 `03-internals.md` §C4 讲的那个 hack 的表面症状：`NONEXCLUSIVE` 是为固定 regulator 后加的，没有被纳入枚举设计。

**`GPIOD_ASIS` 不会读方向。** 它只是不设置方向；`desc->flags` 里的 `FLAG_IS_OUT` 来自注册时的探测（`03-internals.md` §A2.11），可能是「猜」出来的。要确切知道方向，用 `gpiod_get_direction()`。

### §1.3 可选 GPIO：`_optional` 变体

复位脚常常是可选的（有的板子接了，有的没接）。这时候用 `_optional`：

```c
	md->reset = devm_gpiod_get_optional(&pdev->dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(md->reset))
		return dev_err_probe(&pdev->dev, PTR_ERR(md->reset),
				     "failed to get reset GPIO\n");
	if (!md->reset)
		dev_dbg(&pdev->dev, "no reset GPIO, skipping reset sequence\n");
```

差别只有一处但很关键：**「没找到」时 `_optional` 返回 NULL 而不是 `ERR_PTR(-ENOENT)`。** 所以判空必须写成「先 `IS_ERR` 再 `!ptr`」两步，顺序不能颠倒（NULL 不是 `IS_ERR`，但 `ERR_PTR` 也不是 NULL）。

第二个好处：**所有 `gpiod_*()` 操作函数都容忍 NULL 描述符**。这是 `validate_desc()`（`drivers/gpio/gpiolib.c:1982-2011`）第一行 `if (!desc) return 0;` 的直接后果。所以拿到 NULL 之后你**不需要**在每个 `gpiod_set_value()` 前面加判空——照常调用，什么都不会发生。这是 `_optional` 真正省下的代码量。

### §1.4 一次拿一组线

```c
	struct gpio_descs *data_lines;

	data_lines = devm_gpiod_get_array(&pdev->dev, "data", GPIOD_OUT_LOW);
	if (IS_ERR(data_lines))
		return PTR_ERR(data_lines);

	dev_info(&pdev->dev, "got %u data lines\n", data_lines->ndescs);

	/* 一次性写全部：每 bit 对应 desc[] 里的一个下标 */
	{
		DECLARE_BITMAP(values, 8) = { 0 };

		bitmap_set(values, 0, 3);     /* bit0..2 = 1 */
		gpiod_set_array_value_cansleep(data_lines->ndescs,
					       data_lines->desc,
					       data_lines->info,
					       values);
	}
```

`struct gpio_descs`（`include/linux/gpio/consumer.h:22-26`）只有三个字段：`info`（不透明的 `struct gpio_array *`）、`ndescs`、`desc[]`。

**第三个参数 `data_lines->info` 必须传，不能传 NULL。** 它是快速路径的钥匙：核心在 `gpiod_get_array_value_complex()` 里用三个条件校验它是否可用（`drivers/gpio/gpiolib.c:2692-2694`，剖析见 `03-internals.md` §A5.3），其中一条是 `(void *)array_info == desc_array + array_info->size`——也就是**要求 `info` 和 `desc[]` 在同一块内存里且位置固定**。传 NULL 会退化成逐线循环，功能对但慢，且丢掉了「一次寄存器写操作」的原子性。

**`values` 位图的 bit 编号是 `desc[]` 的下标，不是 GPIO 的硬件 offset。** 这两个数在 DT 里通常不连续，混淆的后果是控错线。

### §1.5 把一根 GPIO 当中断源用

```c
	int irq;

	md->irq_line = devm_gpiod_get(&pdev->dev, "int", GPIOD_IN);
	if (IS_ERR(md->irq_line))
		return PTR_ERR(md->irq_line);

	irq = gpiod_to_irq(md->irq_line);
	if (irq < 0)
		return dev_err_probe(&pdev->dev, irq, "no IRQ for this line\n");

	ret = devm_request_threaded_irq(&pdev->dev, irq, NULL, my_irq_thread,
					IRQF_ONESHOT | IRQF_TRIGGER_FALLING,
					dev_name(&pdev->dev), md);
```

**`gpiod_to_irq()` 的错误约定和别的 `gpiod_*` 不一样。** 它的源码里有一段专门的注释解释为什么不能用 `VALIDATE_DESC()`：

```c
	/*
	 * Cannot VALIDATE_DESC() here as gpiod_to_irq() consumer semantics
	 * requires this function to not return zero on an invalid descriptor
	 * but rather a negative error number.
	 */
	if (!desc || IS_ERR(desc) || !desc->gdev || !desc->gdev->chip)
		return -EINVAL;
	— drivers/gpio/gpiolib.c:3237-3260
```

也就是说：**`gpiod_to_irq()` 是唯一一个不容忍 NULL 描述符的 `gpiod_*` 函数**（对比 §1.3）。用 `_optional` 拿到 NULL 之后直接调它，会得到 `-EINVAL` 而不是「静默无操作」。

同一段代码里还有第二个约定：`gc->to_irq` 返回 0 时核心翻译成 `-ENXIO`，注释是 `/* Zero means NO_IRQ */`。所以判据必须是 `irq < 0`，写成 `if (!irq)` 或 `if (irq <= 0)` 都会漏掉一部分情况。

更推荐的写法是**别用 `gpiod_to_irq()`**：如果 DT 里那个节点有 `interrupts` 属性，用 `platform_get_irq()` 直接拿。`gpiod_to_irq()` 适用的是「DT 只给了 GPIO、没给 interrupt」的场合。

### §1.6 raw 与逻辑：两组 API 必须成对记

这是消费者侧最容易写错的地方。每个值操作都有 raw 和非 raw 两个版本：

| 逻辑版（走 `FLAG_ACTIVE_LOW` 反相） | raw 版（不反相） |
|---|---|
| `gpiod_get_value()` / `gpiod_get_value_cansleep()` | `gpiod_get_raw_value()` / `gpiod_get_raw_value_cansleep()` |
| `gpiod_set_value()` / `gpiod_set_value_cansleep()` | `gpiod_set_raw_value()` / `gpiod_set_raw_value_cansleep()` |
| `gpiod_get_array_value*()` | `gpiod_get_raw_array_value*()` |
| `gpiod_set_array_value*()` | `gpiod_set_raw_array_value*()` |
| `gpiod_direction_output()` | `gpiod_direction_output_raw()` |

反相只发生在三个地方（`03-internals.md` §B2 有完整分层图）：`gpiod_get_value()`（`drivers/gpio/gpiolib.c:2832-2833`）、`gpiod_set_value_nocheck()`（`:3116-3117`）和两处批量 `bitmap_xor`。**注意 `gpiod_direction_input()` 没有 raw 版本**——输入方向本身不涉及值，所以不需要。

**规则：写驱动一律用逻辑版。** raw 版只在两种场合有意义：(1) 你在实现一个更底层的库；(2) 你要绕过 DT 里的 `GPIO_ACTIVE_LOW` 做诊断。把 raw 版当「更直接所以更好」用是错的——它会让同一个驱动在不同板子上行为不一致。

### §1.7 `_cansleep` 后缀：选哪个

| 你在哪 | 用哪个 |
|---|---|
| probe / 工作队列 / 线程化中断处理 / 任何可睡眠上下文 | **`_cansleep` 版** |
| 硬中断处理 / 持 spinlock / 原子上下文 | 不带后缀的版本 |

判据不是「这根线在不在慢总线上」，而是「**我现在能不能睡**」。原因：不带后缀的版本内部有 `WARN_ON(gc->can_sleep)` 类的检查，在一个 I2C GPIO 扩展器上调用它会打 WARN；而 `_cansleep` 版在原子上下文里调用会真的睡过去，后果更严重。**probe 里一律用 `_cansleep`**，没有例外。

---

## §2 角色 2：板级注册（没有设备树时）

arm64 上设备树是常态，所以这一节多数人不需要。但两个场合会用到：**用 `gpio-sim` 造测试环境**，以及**给一个没有 DT 节点的虚拟设备补 GPIO 映射**。

### §2.1 lookup table 的形状

```c
#include <linux/gpio/machine.h>

static struct gpiod_lookup_table my_gpio_table = {
	.dev_id = "my-platform-dev",              /* 目标设备的 dev_name() */
	.table = {
		GPIO_LOOKUP("gpiochip0", 5, "reset", GPIO_ACTIVE_LOW),
		GPIO_LOOKUP_IDX("gpiochip0", 6, "data", 0, GPIO_ACTIVE_HIGH),
		GPIO_LOOKUP_IDX("gpiochip0", 7, "data", 1, GPIO_ACTIVE_HIGH),
		{ },                                  /* 结束哨兵，必须有 */
	},
};

static int __init my_board_init(void)
{
	gpiod_add_lookup_table(&my_gpio_table);
	/* ... 注册 platform device ... */
	return 0;
}
```

三个宏（都在 `include/linux/gpio/machine.h`）：

| 宏 | 位置 | 用途 |
|---|---|---|
| `GPIO_LOOKUP_SINGLE(name, dev_id, key, hwnum, con_id, flags)` | `:70-78` | 只有一根线时，连表带定义一起生成 |
| `GPIO_LOOKUP(key, hwnum, con_id, flags)` | `:83-84` | 一个表项，`idx` 固定为 0 |
| `GPIO_LOOKUP_IDX(key, hwnum, con_id, idx, flags)` | `:90-97` | 一个表项，指定 `idx` |

`GPIO_LOOKUP()` 就是 `GPIO_LOOKUP_IDX(..., 0, ...)`，源码一行可见（`:84`）。`GPIO_LOOKUP_IDX` 的注释说明了 `idx` 的用途：`Use this macro if you need to have several GPIOs under the same con_id. Each GPIO needs to use a different index and can be accessed using gpiod_get_index()`（`:86-89`）。

### §2.2 `key` 字段的双重含义

`struct gpiod_lookup`（`:36-42`）的 kerneldoc 讲了一件容易忽略的事：

> @key: either the name of the chip the GPIO belongs to, or the GPIO line name
> Note that GPIO line names are not guaranteed to be globally unique, so this will use the first match found!
> @chip_hwnum: hardware number (i.e. relative to the chip) of the GPIO, or U16_MAX to indicate that @key is a GPIO line name
> — `include/linux/gpio/machine.h:23-28`

所以 `key` 有两种解释，由 `chip_hwnum` 是否等于 `U16_MAX` 决定：

| `chip_hwnum` | `key` 的含义 | 风险 |
|---|---|---|
| 普通数值 | chip 的 label | chip label 通常唯一 |
| `U16_MAX` | **line name** | **line name 不保证全局唯一，取第一个匹配** |

第二种模式配合 DT 的 `gpio-line-names` 使用时很方便，但那句 `will use the first match found!` 是一条实打实的静默错误来源——两块 chip 上有同名线时，你拿到的是先注册的那一根，而注册顺序不受你控制。

### §2.3 九个 `enum gpio_lookup_flags`

`include/linux/gpio/machine.h:8-21`：

| 标志 | 值 | 说明 |
|---|---|---|
| `GPIO_ACTIVE_HIGH` | `(0 << 0)` | 默认，等于「不设」 |
| `GPIO_ACTIVE_LOW` | `(1 << 0)` | 逻辑反相 |
| `GPIO_OPEN_DRAIN` | `(1 << 1)` | 开漏 |
| `GPIO_OPEN_SOURCE` | `(1 << 2)` | 开源 |
| `GPIO_PERSISTENT` | `(0 << 3)` | 默认，等于「不设」 |
| `GPIO_TRANSITORY` | `(1 << 3)` | 睡眠时不保持 |
| `GPIO_PULL_UP` | `(1 << 4)` | 上拉 |
| `GPIO_PULL_DOWN` | `(1 << 5)` | 下拉 |
| `GPIO_PULL_DISABLE` | `(1 << 6)` | 禁用偏置 |
| `GPIO_LOOKUP_FLAGS_DEFAULT` | `GPIO_ACTIVE_HIGH \| GPIO_PERSISTENT` | 两个 0 的或，即 0 |

**`GPIO_ACTIVE_HIGH` 和 `GPIO_PERSISTENT` 的值都是 0。** 它们存在的意义是让代码可读，而不是让你「显式打开」某个行为。写 `GPIO_ACTIVE_HIGH | GPIO_PULL_UP` 和写 `GPIO_PULL_UP` 完全等价。

三个 pull 标志会在 `gpiod_configure_flags()` 里被查互斥（`03-internals.md` §A4.4）：同时设两个会得到 `-EINVAL`，并打印 `multiple pull-up, pull-down or pull-disable enabled, invalid configuration`。

### §2.4 hog：不让任何驱动拿走这根线

```c
static struct gpiod_hog my_hogs[] = {
	GPIO_HOG("gpiochip0", 12, "board-id-strap", GPIO_ACTIVE_HIGH, GPIOD_IN),
	{ },
};

	gpiod_add_hogs(my_hogs);
```

`GPIO_HOG(chip_label, chip_hwnum, line_name, lflags, dflags)` 在 `include/linux/gpio/machine.h:102-110`，`struct gpiod_hog` 在 `:66-72`。

**hog 的语义是「核心自己请求并持有这根线」**，目的是让它对所有驱动和用户态都不可用（防止误操作，例如一根接了 strap 电阻的线）。注意 `dflags` 那一列传的是 `enum gpiod_flags`（§1.2 的六个值之一），而 `lflags` 是 §2.3 的 `enum gpio_lookup_flags`——**两组标志名字风格相近但完全不同的命名空间**，传错编译期不报错（两者都是整型）。

DT 侧的等价写法是 `gpio-hog` 属性，扫描逻辑在 `of_gpiochip_add()` 里（`03-internals.md` §A2.10），一个 arm64 实例见 `06-devicetree.md`。

四个 API（`include/linux/gpio/machine.h:113-117`）：`gpiod_add_lookup_table`、`gpiod_add_lookup_tables`、`gpiod_remove_lookup_table`、`gpiod_add_hogs`、`gpiod_remove_hogs`。

**`gpiod_add_lookup_tables()` 是一个陷阱**：它在头文件里有声明（`:114`）、在 `drivers/gpio/gpiolib.c:3603` 有定义，但**没有 `EXPORT_SYMBOL`**。也就是说模块里用不了它，只有编进内核的板级代码能用。头文件不会告诉你这件事。

---

## §3 角色 3：最简 GPIO 控制器（自己写全部回调）

本节的参照实现是 **`drivers/gpio/gpio-pl061.c`（440 行）**——选它的三个理由：够短能全读、是 ARM PrimeCell 的标准件、在本树 arm64 上真的有人用（`arch/arm64/boot/dts/hisilicon/hi6220.dtsi` 有 20 个 `arm,pl061` 节点（`grep -c 'arm,pl061' arch/arm64/boot/dts/hisilicon/hi6220.dtsi`），见 `06-devicetree.md`）。本节所有引用都指向它，读者能 diff。

### §3.1 `struct gpio_chip` 要填哪些字段

`struct gpio_chip` 定义在 `include/linux/gpio/driver.h:415`，字段很多，但**必填的只有五个**：

| 字段 | 行 | 必填？ | 说明 |
|---|---|---|---|
| `label` | `:416` | **是** | 出现在 `/sys/class/gpio` 和 debugfs 里，习惯用 `dev_name(dev)` |
| `parent` | `:418` | **是** | 指回你的 `struct device`，DT 解析全靠它 |
| `owner` | `:420` | **是** | `THIS_MODULE`，防止模块被卸载 |
| `ngpio` | `:464` | **是** | 这块 chip 有几根线 |
| `base` | `:463` | **是** | **填 `-1`** 让核心动态分配，见下面的 |
| `names` | `:466` | 否 | 静态线名数组，DT 的 `gpio-line-names` 会覆盖它 |
| `can_sleep` | `:467` | 否 | MMIO 控制器填 false（即不填） |
| `offset` | `:465` | 否 | 多 bank 时表示本 chip 在整个设备里的偏移 |

**`base` 必须显式写 `-1`。** `devm_kzalloc()` 出来的结构体这个字段是 0，而 0 是一个**合法的静态 base 值**——核心会当成「我要占用全局号 0 开始的区间」，于是第二块 chip 注册时和它撞号，得到 `-EBUSY`。pl061 在 `drivers/gpio/gpio-pl061.c:325` 一行就是干这个的。这是新写 GPIO 驱动最常见的一个 bug，而且它在只有一块 chip 的板子上不会暴露。

### §3.2 九个操作回调

都在 `include/linux/gpio/driver.h`：

| 回调 | 行 | 什么时候必须实现 |
|---|---|---|
| `request(gc, offset)` | `:422` | 需要在申请时做 pinmux 或上电时（见 §7） |
| `free(gc, offset)` | `:424` | 与 `request` 配对 |
| `get_direction(gc, offset)` | `:426` | **强烈建议**，否则核心只能靠猜（见 §3.4） |
| `direction_input(gc, offset)` | `:428` | 支持输入就要有 |
| `direction_output(gc, offset, value)` | `:430` | 支持输出就要有 |
| `get(gc, offset)` | `:432` | 支持读就要有 |
| `get_multiple(gc, mask, bits)` | `:434` | 可选，批量读的加速路径 |
| `set(gc, offset, value)` | `:437` | 支持写就要有 |
| `set_multiple(gc, mask, bits)` | `:439` | 可选，批量写的加速路径 |
| `set_config(gc, offset, config)` | `:442` | 支持偏置/开漏/去抖配置时 |
| `to_irq(gc, offset)` | `:445` | **别自己填**，见下面的 |
| `dbg_show(s, gc)` | `:448` | 可选，往 debugfs 里加自定义输出（`07-debug.md` §4） |
| `init_valid_mask(gc, mask, ngpios)` | `:451` | 有物理上不存在的线时 |
| `add_pin_ranges(gc)` | `:455` | pinctrl 桥接的回调形式（见 §7.3） |

**`to_irq` 不要自己填。** 只要你设置了 `gc->irq.chip`，`gpiochip_add_irqchip()` 会无条件覆盖它（`drivers/gpio/gpiolib.c:1574`），并且在覆盖前先警告：

```c
	if (gc->to_irq)
		chip_warn(gc, "to_irq is redefined in %s and you shouldn't rely on it\n", __func__);

	gc->to_irq = gpiochip_to_irq;
	— drivers/gpio/gpiolib.c:1571-1574
```

注意这句话打印的是 `__func__`，所以 dmesg 里你会看到 `to_irq is redefined in gpiochip_add_irqchip and you shouldn't rely on it`——搜索这句话时不要带函数名去搜源码。

**`get_multiple` / `set_multiple` 的 `mask` 和 `bits` 都是本 chip 内的 offset 位图**，和消费者侧 §1.4 那个「`desc[]` 下标位图」不是同一个编号空间——核心在 `gpiod_set_array_value_complex()` 里做了转换。回调里当成硬件 offset 用就对了。

### §3.3 pl061 的骨架

```c
#include <linux/gpio/driver.h>

struct my_gpio {
	raw_spinlock_t		lock;
	void __iomem		*base;
	struct gpio_chip	gc;
};

static int my_get_direction(struct gpio_chip *gc, unsigned int offset)
{
	struct my_gpio *g = gpiochip_get_data(gc);

	if (readb(g->base + DIRREG) & BIT(offset))
		return GPIO_LINE_DIRECTION_OUT;

	return GPIO_LINE_DIRECTION_IN;
}

static int my_get_value(struct gpio_chip *gc, unsigned int offset)
{
	struct my_gpio *g = gpiochip_get_data(gc);

	return !!(readb(g->base + DATAREG) & BIT(offset));
}

static void my_set_value(struct gpio_chip *gc, unsigned int offset, int value)
{
	struct my_gpio *g = gpiochip_get_data(gc);
	/* ... 读改写，用 g->lock 保护 ... */
}

static int my_probe(struct platform_device *pdev)
{
	struct my_gpio *g;
	int ret;

	g = devm_kzalloc(&pdev->dev, sizeof(*g), GFP_KERNEL);
	if (!g)
		return -ENOMEM;

	g->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(g->base))
		return PTR_ERR(g->base);

	raw_spin_lock_init(&g->lock);

	g->gc.label		= dev_name(&pdev->dev);
	g->gc.parent		= &pdev->dev;
	g->gc.owner		= THIS_MODULE;
	g->gc.base		= -1;              /* 必须 */
	g->gc.ngpio		= 8;
	g->gc.get_direction	= my_get_direction;
	g->gc.direction_input	= my_direction_input;
	g->gc.direction_output	= my_direction_output;
	g->gc.get		= my_get_value;
	g->gc.set		= my_set_value;

	/* 第三个参数就是回调里 gpiochip_get_data() 拿到的东西 */
	return devm_gpiochip_add_data(&pdev->dev, &g->gc, g);
}
```

**`gpiochip_get_data()`（`include/linux/gpio/driver.h:660`）取的就是 `devm_gpiochip_add_data()` 的第三个参数**，不是 `dev_get_drvdata()`。这两套 drvdata 是独立的：pl061 同时用了两套——`devm_gpiochip_add_data(dev, &pl061->gc, pl061)`（`drivers/gpio/gpio-pl061.c:357`）给回调用，`amba_set_drvdata(adev, pl061)` 给 PM 回调用。写驱动时如果只设一套，另一套是 NULL。

**`devm_gpiochip_add_data()` 是宏而不是函数**，而且有两个版本：`CONFIG_LOCKDEP` 打开时它在调用点静态定义两个 `struct lock_class_key`（`include/linux/gpio/driver.h:605-611`），关闭时退化成传两个 NULL（`:613-614`）。后果：**这个宏必须直接写在你的 probe 里，不能包一层自己的 helper 函数**——包一层会让所有实例共享同一对 lockdep key，lockdep 报告会串味。

**`get_direction()` 的返回值不是 0/1，是两个宏**：`GPIO_LINE_DIRECTION_OUT`（0）和 `GPIO_LINE_DIRECTION_IN`（1）。**注意 IN 是 1、OUT 是 0**，和直觉相反。写成 `return !!(dir_reg & bit);` 表示「是输出」的话，语义正好反了。

### §3.4 不实现 `get_direction()` 的后果

核心不会报错，但会「猜」：注册时 `gpiochip_add_data_with_key()` 里对每根线调一次 `gc->get_direction()`，拿不到就默认按输入记（详见 `03-internals.md` §A2.11）。后果是 `desc->flags` 里的 `FLAG_IS_OUT` 可能与硬件实际状态不符，接下来：

- debugfs 里 `/sys/kernel/debug/gpio` 显示的方向是错的；
- 消费者用 `GPIOD_ASIS` 拿线时，`gpiod_get_direction()` 返回的是这个猜出来的值；
- `gpiod_get_value()` 本身不看 `FLAG_IS_OUT`，所以读值还是对的——这让问题更难发现。

**结论：只要硬件有方向寄存器可读，就实现 `get_direction()`。** 它是唯一一个「不实现不报错但会静默说谎」的回调。

### §3.5 变体：I²C/SPI 扩展器（`can_sleep = true` 的提供方要多做三件事）

上面 pl061 的骨架是 MMIO 型：读写寄存器就是一条 `readl`，**不会睡**。挂在 I²C/SPI 上的扩展器完全不同——每次读写都是一次总线事务，会睡。以 `drivers/gpio/gpio-pca953x.c`（arm64 板子上最常见的扩展器）为例，比 pl061 多的三件事：

1. **声明自己会睡**：

   ```c
   gc->can_sleep = true;                    /* gpio-pca953x.c:699 */
   ```

   这一行的后果**不是「慢一点」，是改变哪些消费者 API 合法**——消费者必须用 `_cansleep` 后缀，否则核心 `WARN_ON`（详见 `08-feynman.md` §8）。你少写这一行，核心不会替你发现。

2. **irqchip 必须线程化，不能用级联 handler**：

   ```c
   girq->threaded = true;                                    /* :944 */
   ret = devm_request_threaded_irq(&client->dev, client->irq, /* :947 */
   ```

   对照 §5 的级联骨架：级联走 `irq_set_chained_handler_and_data()`，处理函数在硬中断上下文里跑——扩展器在那里读不了中断状态寄存器（要发 I²C）。所以扩展器只能走线程化 + `threaded = true`（这一位会让核心额外调 `irq_set_nested_thread()`，见 `08-feynman.md` §7）。

3. **`irq_mask`/`irq_unmask` 不能立刻发总线事务，要攒起来**：

   ```c
   static const struct irq_chip pca953x_irq_chip = {   /* :820 */
           .irq_mask               = pca953x_irq_mask,
           .irq_unmask             = pca953x_irq_unmask,
           .irq_bus_lock           = pca953x_irq_bus_lock,        /* :824，实现在 :743 */
           .irq_bus_sync_unlock    = pca953x_irq_bus_sync_unlock, /* :825 */
           ...
           .flags                  = IRQCHIP_IMMUTABLE,           /* :829 */
           GPIOCHIP_IRQ_RESOURCE_HELPERS,                         /* :830 */
   };
   ```

   `irq_mask()` 只改内存里的影子寄存器，真正的 I²C 写在 `irq_bus_sync_unlock()` 里一次刷出去。**这对回调是 irqchip 框架专门为「慢总线上的中断控制器」留的口子**，MMIO 控制器一律不需要。注意 `:829`/`:830` 那两行——扩展器同样要遵守 IMMUTABLE 契约（见 `01-api.md` §6.4）。

### §3.6 收尾：`remove` 和 PM 这两件事必须交代

**remove。** 两条路，选一条，别混：

- 用 `devm_gpiochip_add_data()`（`04-extensions.md` §4.3）：**不要写任何 remove 代码**，devres 会在设备解绑时按逆序注销。
- 用 `gpiochip_add_data()`：必须自己在 `remove` 里 `gpiochip_remove(gc)`。
- 而且要知道 `gpiochip_remove()` 的真实行为：kerneldoc 承诺「还有线被请求时不能移除」，**但代码只打一条 `dev_crit` 就继续拆**（`drivers/gpio/gpiolib.c:906-907`→`:914`，定性见 `99-evidence.md` §6 的 ⑨）。所以"移除时消费者还占着线"是**你自己要避免的场景**，不是核心会帮你拦住的场景。

**PM。** 提供方侧要区分两个完全不同的问题：

| 问题 | 谁负责 | 怎么写 |
|---|---|---|
| 掉电后**寄存器内容**没了 | 驱动自己 | 扩展器的标准解法是 regmap cache：suspend 里 `regcache_cache_only(chip->regmap, true)`（`gpio-pca953x.c:1262`），resume 里 `regcache_cache_only(…, false)` + `regcache_mark_dirty()` + 回灌（`:1246`–`:1248`），挂在 `SIMPLE_DEV_PM_OPS(pca953x_pm_ops, pca953x_suspend, pca953x_resume)`（`:1357`）上 |
| 睡眠后**线的电平**保不住，消费者需要知道 | 核心提供查询口 | 线上的 `FLAG_TRANSITORY`（`drivers/gpio/gpiolib.h:162`，注释就是 "GPIO may lose value in sleep or reset"）由消费者侧的 `gpiod_set_transitory()` 置位（`gpiolib.c:2588`）或从 lookup flag 带进来（`:4050`，对应 `GPIO_TRANSITORY`）；提供方用 `gpiochip_line_is_persistent()` 反查（`gpiolib.c:3432-3439`，实现就是取 `FLAG_TRANSITORY` 的反） |

第二行有个值得记住的事实：`gpiochip_line_is_persistent()` 是导出符号，但**全树只有一个驱动在用**——`drivers/gpio/gpio-arizona.c:31` 和 `:101`。

```bash
grep -rn 'gpiochip_line_is_persistent' --include=*.c --include=*.h .
```

也就是说，这个"睡眠保持性"机制在 6.1 里基本处于**只有接口、几乎没有实现方**的状态。你写新驱动时**不要指望消费者已经正确设过这个标志**。

---

## §4 角色 4：用 `bgpio_init()` 省掉回调

如果你的控制器满足下面这组条件，`gpio-mmio.c` 能替你把 §3.2 那九个回调全部填好：

- 一个数据寄存器（`dat`），可选的 set / clear 寄存器；
- 可选的方向寄存器（`dir_out` 或 `dir_in`）；
- 寄存器宽度是 1/2/4/8 字节（`is_power_of_2(sz)`），且位数 ≤ `BITS_PER_LONG`；
- 一根 GPIO 对应一个 bit，bit n ↔ offset n。

```c
	g->gc.parent = &pdev->dev;        /* bgpio_init 会重设，但先设无害 */

	ret = bgpio_init(&g->gc, &pdev->dev, 4 /* sz 字节 */,
			 dat, set, clr, dirout, dirin, 0 /* flags */);
	if (ret)
		return ret;

	/* 覆盖 bgpio_init 已经写过的字段，必须放在它之后 */
	g->gc.label = "my-gpio";
	g->gc.base  = -1;
	g->gc.ngpio = 24;                 /* 硬件只有 24 根，不是 32 根 */

	return devm_gpiochip_add_data(&pdev->dev, &g->gc, g);
```

### §4.1 四个被覆盖的字段

`bgpio_init()`（`drivers/gpio/gpio-mmio.c:599-662`）在 `:609-619` 无条件写了四个你可能也在写的字段：

| 字段 | 行 | `bgpio_init()` 写成 | 你可能想要 |
|---|---|---|---|
| `gc->label` | `:615` | `dev_name(dev)` | 自定义名字 |
| `gc->base` | `:616` | `-1` | 通常正是你要的 |
| `gc->ngpio` | `:617` | `sz * 8`（寄存器满位宽） | **实际线数，通常更小** |
| `gc->request` | `:618` | `bgpio_request` | **`gpiochip_generic_request`（§7）** |

**第 3 行和第 4 行是真陷阱。** `ngpio = sz * 8` 意味着一个 32 位寄存器上永远报 32 根线，即使硬件只接出 24 根——多出来的 8 根会出现在 `/dev/gpiochipN` 里，用户态能请求，读写行为未定义。

**第 4 行会盖掉 pinctrl 桥接。** 如果你是 §7 的双身份驱动，在 `bgpio_init()` 之后必须重新写一次 `gc->request = gpiochip_generic_request; gc->free = gpiochip_generic_free;`，否则 GPIO 申请时不会去跟 pinctrl 打招呼，pinmux 状态和 GPIO 状态会各说各话。

**规则：所有对 `gc` 的字段赋值都放在 `bgpio_init()` 之后。** 放前面的会被静默覆盖，编译器不会提醒。

### §4.2 七个 `BGPIOF_*` 标志

`include/linux/gpio/driver.h:686-692`：

| 标志 | 管什么 |
|---|---|
| `BGPIOF_BIG_ENDIAN` | **位序**：bit 0 是最高位还是最低位 |
| `BGPIOF_UNREADABLE_REG_SET` | set 寄存器只能写不能读 |
| `BGPIOF_UNREADABLE_REG_DIR` | 方向寄存器只能写不能读 |
| `BGPIOF_BIG_ENDIAN_BYTE_ORDER` | **字节序**：用 `ioread32be` 还是 `ioread32` |
| `BGPIOF_READ_OUTPUT_REG_SET` | 读输出值时读 set 寄存器而不是 dat |
| `BGPIOF_NO_OUTPUT` | 只有输入 |
| `BGPIOF_NO_SET_ON_INPUT` | 输入方向下不允许写值 |

**`BGPIOF_BIG_ENDIAN` 和 `BGPIOF_BIG_ENDIAN_BYTE_ORDER` 是两件完全不同的事**，名字却只差一个后缀。第一个决定 `gc->be_bits`（`drivers/gpio/gpio-mmio.c:619`），影响 offset→bit 的换算；第二个传给 `bgpio_setup_accessors()`（`:625`），决定用哪一对 `read_reg`/`write_reg` 访问器。一个 big-endian 的 SoC 上很可能只需要后者，两个都设会把位序也翻过来。

**`BGPIOF_UNREADABLE_REG_DIR` 会关掉方向探测。** `bgpio_init()` 在 `:641-658` 有一段「Inspect hardware to find initial direction setting」的逻辑，它被这个标志跳过；跳过后 `gc->bgpio_dir` 保持 0，即「全部是输入」。如果硬件复位后其实有些线是输出，这里就产生了 §3.4 说的那种静默说谎。

同一段逻辑还有一处会**主动改硬件**：两个方向寄存器都存在时，它把 `reg_dir_in` 写成 `~bgpio_dir`（`:657`），注释是 `the library can not handle a line being input and output at the same time`。也就是说 `bgpio_init()` 不是纯软件初始化——它会向硬件写一次。

---

## §5 角色 5：级联中断（cascaded irqchip）

「级联」指控制器的所有 GPIO 中断汇总到**一根**（或几根）父 IRQ 线上，父中断到来后你自己读状态寄存器，逐位分发。pl061 是标准形态。

### §5.1 四步

```c
	struct gpio_irq_chip *girq;

	/* 步骤 0：先把硬件中断全关掉 */
	writeb(0, g->base + IEREG);

	irq = platform_get_irq(pdev, 0);
	if (irq < 0)
		return irq;

	girq = &g->gc.irq;
	gpio_irq_chip_set_chip(girq, &my_irq_chip);        /* 步骤 1 */
	girq->parent_handler = my_irq_handler;             /* 步骤 2 */
	girq->num_parents = 1;                             /* 步骤 3 */
	girq->parents = devm_kcalloc(&pdev->dev, 1,
				     sizeof(*girq->parents), GFP_KERNEL);
	if (!girq->parents)
		return -ENOMEM;
	girq->parents[0] = irq;
	girq->default_type = IRQ_TYPE_NONE;                /* 步骤 4 */
	girq->handler = handle_bad_irq;

	return devm_gpiochip_add_data(&pdev->dev, &g->gc, g);
```

对照 `drivers/gpio/gpio-pl061.c:339-357`，逐行一致。

**`girq->parents` 必须是动态分配的（或长生命周期的）内存，不能是栈上数组。** 核心在 `gpiochip_add_irqchip()` 里保存的是指针（`drivers/gpio/gpiolib.c:1609`），probe 返回后还会用到（例如 `gpiochip_irqchip_remove()`）。pl061 用 `devm_kcalloc()` 就是为这个。

**`default_type` 一律填 `IRQ_TYPE_NONE`。** 核心里有一条硬规定：

```c
	/*
	 * Specifying a default trigger is a terrible idea if DT or ACPI is
	 * used to configure the interrupts, as you may end up with
	 * conflicting triggers. Tell the user, and reset to NONE.
	 */
	if (WARN(fwnode && type != IRQ_TYPE_NONE,
		 "%pfw: Ignoring %u default trigger\n", fwnode, type))
		type = IRQ_TYPE_NONE;
	— drivers/gpio/gpiolib.c:1562-1569
```

也就是说：**只要设备有 fwnode（DT 上永远有），填非 NONE 的 `default_type` 就会得到一个 `WARN()` 加栈回溯，而且值照样被改成 NONE。** 不是「不推荐」，是「一定挨打且无效」。

**`girq->handler` 填 `handle_bad_irq` 是刻意的。** 它是「还没设触发方式就来中断了」的兜底：pl061 的 `.irq_set_type` 会按 level/edge 用 `irq_set_handler_locked()` 把真正的 handler 换上去（`drivers/gpio/gpio-pl061.c:189` 换成 `handle_edge_irq`，`:198` 在「无触发」时换回 `handle_bad_irq`）。所以初值填 `handle_bad_irq` 而不是 `handle_simple_irq`，是为了让「忘了设触发」这件事响一声而不是静默丢中断。

### §5.2 父中断处理函数

```c
static void my_irq_handler(struct irq_desc *desc)
{
	struct gpio_chip *gc = irq_desc_get_handler_data(desc);
	struct my_gpio *g = gpiochip_get_data(gc);
	struct irq_chip *irqchip = irq_desc_get_chip(desc);
	unsigned long pending;
	int offset;

	chained_irq_enter(irqchip, desc);

	pending = readb(g->base + MISREG);
	if (pending) {
		for_each_set_bit(offset, &pending, MY_GPIO_NR)
			generic_handle_domain_irq(gc->irq.domain, offset);
	}

	chained_irq_exit(irqchip, desc);
}
```

对照 `drivers/gpio/gpio-pl061.c:212-230`。四个要点：

1. **`irq_desc_get_handler_data(desc)` 拿到的是 `gc`**，因为核心把 `parent_handler_data ?: gc` 传了进去（`drivers/gpio/gpiolib.c:1602`）。你如果设了 `girq->parent_handler_data`，这里拿到的就是你设的东西，`gc` 得另外找。
2. **`chained_irq_enter` / `chained_irq_exit` 必须成对**，来自 `<linux/irqchip/chained_irq.h>`（pl061 在 `:18` include 它）。
3. **分发用 `generic_handle_domain_irq(gc->irq.domain, offset)`**，参数是**本 chip 内的 offset**，不是 Linux irq 号。
4. **`for_each_set_bit` 的第二个参数要取地址**（`&pending`），它是位图 API。

**级联和 `can_sleep` 互斥。** 核心第一道门就是这个：

```c
	if (gc->irq.parent_handler && gc->can_sleep) {
		chip_err(gc, "you cannot have chained interrupts on a chip that may sleep\n");
		return -EINVAL;
	}
	— drivers/gpio/gpiolib.c:1555-1558
```

所以 I2C/SPI GPIO 扩展器不能用本节的做法，必须用 `girq->threaded = true`（`include/linux/gpio/driver.h:228`）走线程化嵌套中断。

### §5.3 `irq_chip` 的三个现代要求

pl061 的 `irq_chip`（`drivers/gpio/gpio-pl061.c:296-305`）示范了 6.1 里的三条新规矩：

```c
static const struct irq_chip pl061_irq_chip = {
	.irq_ack		= pl061_irq_ack,
	.irq_mask		= pl061_irq_mask,
	.irq_unmask		= pl061_irq_unmask,
	.irq_set_type		= pl061_irq_type,
	.irq_set_wake		= pl061_irq_set_wake,
	.irq_print_chip		= pl061_irq_print_chip,
	.flags			= IRQCHIP_IMMUTABLE,
	GPIOCHIP_IRQ_RESOURCE_HELPERS,
};
```

1. **`static const`**。旧写法是可写的全局变量，核心会往里塞回调；现在改成 const 之后要用 `gpio_irq_chip_set_chip()`（`include/linux/gpio/driver.h:644-649`）赋值，这个 inline 函数的注释很坦白：`/* Yes, dropping const is ugly, but it isn't like we have a choice */`（`:647`）。
2. **`.flags = IRQCHIP_IMMUTABLE`**，声明「核心别改我」。
3. **`GPIOCHIP_IRQ_RESOURCE_HELPERS`**（`include/linux/gpio/driver.h:640-642`）——它展开成两个字段赋值：

```c
#define	GPIOCHIP_IRQ_RESOURCE_HELPERS					\
		.irq_request_resources  = gpiochip_irq_reqres,		\
		.irq_release_resources  = gpiochip_irq_relres
```

**这个宏没有分号，必须当成结构体初始化列表里的一个「项」写**，后面跟逗号。把它写成 `GPIOCHIP_IRQ_RESOURCE_HELPERS;` 会编译失败。它的作用是让 `request_irq()` 时自动 `gpiochip_reqres_irq()`，即把这根线在 gpiolib 里也标记成占用——不加的话，同一根线可以既被当中断用又被别的驱动当 GPIO 请求走。

用了 `IRQCHIP_IMMUTABLE` 之后，**`.irq_mask` / `.irq_unmask` 里必须自己调 `gpiochip_disable_irq()` / `gpiochip_enable_irq()`**。pl061 分别在 `drivers/gpio/gpio-pl061.c:244` 和 `:254` 调。这两个函数声明在 `include/linux/gpio/driver.h:632-633`，定义在 `drivers/gpio/gpiolib.c:3354` 和 `:3364`。忘了调的后果是 `FLAG_USED_AS_IRQ` 不同步——`gpiochip_line_is_irq()`（`driver.h:629`）从此返回错的值，`/sys/kernel/debug/gpio` 里那根线不再显示为中断。

---

## §6 角色 6：层级中断（hierarchical irqchip）

「层级」指每根 GPIO 在父中断控制器里有**自己独立的 hwirq**，不需要你读状态寄存器分发——中断直接从父控制器送到对应的 GPIO irq。arm64 上 GIC + SoC GPIO 常是这个形态。

判据只有一个：**`gc->irq.parent_domain` 非 NULL**。核心用它二选一（`drivers/gpio/gpiolib.c:1580`，`gpiochip_hierarchy_is_hierarchical()` 定义在 `:1246`）。

### §6.1 三个必填回调

参照实现是 `drivers/gpio/gpio-tegra186.c`。

```c
	irq = &g->gc.irq;
	gpio_irq_chip_set_chip(irq, &my_gpio_irq_chip);
	irq->fwnode = of_node_to_fwnode(pdev->dev.of_node);
	irq->child_to_parent_hwirq    = my_child_to_parent_hwirq;
	irq->populate_parent_alloc_arg = my_populate_parent_fwspec;
	irq->child_offset_to_irq      = my_child_offset_to_irq;
	irq->handler                  = handle_simple_irq;
	irq->default_type             = IRQ_TYPE_NONE;

	np = of_find_matching_node(NULL, my_parent_of_match);
	if (np) {
		irq->parent_domain = irq_find_host(np);
		of_node_put(np);
		if (!irq->parent_domain)
			return -EPROBE_DEFER;      /* 关键 */
	}
```

对照 `drivers/gpio/gpio-tegra186.c:859-906`。三个回调的分工：

| 回调 | 字段行 | 职责 |
|---|---|---|
| `child_to_parent_hwirq` | `driver.h:100` | 「我的第 N 根线，在父域里 hwirq 是几、类型是什么」 |
| `populate_parent_alloc_arg` | `driver.h:115` | 「把上面那个 hwirq 打包成父域认识的 fwspec」 |
| `child_offset_to_irq` | `driver.h:128` | offset → hwirq 的纯算术换算 |

tegra186 的 `child_to_parent_hwirq` 短到只有三行，因为它把活推给了第三个回调：

```c
static int tegra186_gpio_child_to_parent_hwirq(struct gpio_chip *chip,
					       unsigned int hwirq,
					       unsigned int type,
					       unsigned int *parent_hwirq,
					       unsigned int *parent_type)
{
	*parent_hwirq = chip->irq.child_offset_to_irq(chip, hwirq);
	*parent_type = type;

	return 0;
}
	— drivers/gpio/gpio-tegra186.c:641-650
```

而 `populate_parent_alloc_arg` 就是照父域的 `#interrupt-cells` 填参数：

```c
	fwspec->fwnode = chip->irq.parent_domain->fwnode;
	fwspec->param_count = 3;
	fwspec->param[0] = gpio->soc->instance;
	fwspec->param[1] = parent_hwirq;
	fwspec->param[2] = parent_type;
	— drivers/gpio/gpio-tegra186.c:631-635
```

**`param_count` 必须等于父控制器 DT 节点的 `#interrupt-cells`**，而 `param[]` 的含义由父控制器的绑定文档定义。这里没有任何编译期或运行期校验——填错的表现是中断注册成功但永远不来，或者注册到了别的中断上。这是整个 gpio 子系统里最难调的一类 bug，唯一的定位手段是对着父控制器的 `of_xlate` 实现逐个参数核对。

**`irq_find_host()` 返回 NULL 时必须返回 `-EPROBE_DEFER`，不能返回错误。** 父中断控制器可能还没 probe。tegra186 在 `:903-904` 就是这么写的。返回 `-ENODEV` 之类的会让你的驱动在启动顺序不巧时永久失败。

### §6.2 层级和级联不是互斥的

这是一个反直觉但代码明确支持的事实：`gpiochip_add_irqchip()` 里选择域类型的 `if/else`（`drivers/gpio/gpiolib.c:1580-1593`）和挂父 handler 的 `if`（`:1595-1613`）是**两个独立的判断**，不是同一个 `if/else` 的两支。所以一个驱动可以同时：

- 设 `parent_domain` → 得到层级域；
- 设 `parent_handler` → 父 IRQ 上还挂一个链式 handler。

tegra186 正是这么做的：`irq->parent_handler = tegra186_gpio_irq`（`drivers/gpio/gpio-tegra186.c:867`）和 `irq->parent_domain = irq_find_host(np)`（`:900`）同时存在，而且后者是有条件的——找不到 PMC 节点时它就退化成纯级联。

所以「这个驱动是级联还是层级」这个问句本身有时是错的。要判断只能看这两个字段各自有没有被赋值。

---

## §7 角色 7：pinctrl + gpio 双身份

同一块硬件既做 pinmux 又做 GPIO 时，两边要互相知情：GPIO 被申请时得让 pinctrl 把引脚切到 GPIO 功能，pinctrl 也得知道哪些引脚当前被 GPIO 占着。

### §7.1 三个现成回调

`include/linux/gpio/driver.h:718-721` 提供了三个可以直接赋给 `gc` 的桥接函数：

| 函数 | 声明行 | 定义行 | 赋给 |
|---|---|---|---|
| `gpiochip_generic_request` | `:718` | `drivers/gpio/gpiolib.c:1743` | `gc->request` |
| `gpiochip_generic_free` | `:719` | `drivers/gpio/gpiolib.c:1759` | `gc->free` |
| `gpiochip_generic_config` | `:720` | `drivers/gpio/gpiolib.c:1776` | `gc->set_config` |

用法就是三行赋值，`drivers/pinctrl/qcom/pinctrl-msm.c:752-761` 的模板里可以看到前两个：

```c
static const struct gpio_chip msm_gpio_template = {
	.direction_input  = msm_gpio_direction_input,
	.direction_output = msm_gpio_direction_output,
	.get_direction    = msm_gpio_get_direction,
	.get              = msm_gpio_get,
	.set              = msm_gpio_set,
	.request          = gpiochip_generic_request,
	.free             = gpiochip_generic_free,
	.dbg_show         = msm_gpio_dbg_show,
};
```

注意这个模板里**没有 `label` / `base` / `ngpio` / `parent` / `owner`**——它们在 `msm_gpio_init()` 里逐个补（`drivers/pinctrl/qcom/pinctrl-msm.c:1340-1345`，其中 `chip->base = -1` 在 `:1341`）。「一个 static const 模板 + 一次结构体拷贝 + 补动态字段」是 pinctrl 驱动的通用写法（`pctrl->chip = msm_gpio_template;` 在 `:1479`）。

**§4 的 `bgpio_init()` 会覆盖 `gc->request`。** 如果你同时是 §4 和 §7，`gpiochip_generic_request` 的赋值必须写在 `bgpio_init()` 之后。

### §7.2 pin range：优先用 DT

GPIO offset ↔ pinctrl pin number 的映射有两种建立方式，**优先用 DT 的 `gpio-ranges` 属性**。`gpiochip_add_pin_range()` 的 kerneldoc 说得很直接：

```
 * Calling this function directly from a DeviceTree-supported
 * pinctrl driver is DEPRECATED. Please see Section 2.1 of
 * Documentation/devicetree/bindings/gpio/gpio.txt on how to
 * bind pinctrl and gpio drivers via the "gpio-ranges" property.
	— drivers/gpio/gpiolib.c:1849-1853
```

pinctrl-msm 的做法是标准答案：先看 DT 有没有写，没写才退回手工调用。

```c
	/*
	 * For DeviceTree-supported systems, the gpio core checks the
	 * pinctrl's device node for the "gpio-ranges" property.
	 * If it is present, it takes care of adding the pin ranges
	 * for the driver. In this case the driver can skip ahead.
	 *
	 * In order to remain compatible with older, existing DeviceTree
	 * files which don't set the "gpio-ranges" property or systems that
	 * utilize ACPI the driver has to call gpiochip_add_pin_range().
	 */
	if (!of_property_read_bool(pctrl->dev->of_node, "gpio-ranges")) {
		ret = gpiochip_add_pin_range(&pctrl->chip,
			dev_name(pctrl->dev), 0, 0, chip->ngpio);
		— drivers/pinctrl/qcom/pinctrl-msm.c:1388-1401
```

`gpio-ranges` 的 DTS 写法和消费者见 `06-devicetree.md`；核心侧的解析入口是 `of_gpiochip_add_pin_range()`，它由 `of_gpiochip_add()` 调用（剖析见 `03-internals.md` §A2.10）。

**`gpiochip_add_pin_range()` 的第二个参数是 pin controller 的 `dev_name()`，用字符串匹配。** 匹配失败时 `pinctrl_find_and_add_gpio_range()` 返回 `ERR_PTR`，核心打 `could not create pin range`（`drivers/gpio/gpiolib.c:1880`）。这是字符串比较，拼错不会有编译警告。

**`!CONFIG_PINCTRL` 时 `gpiochip_add_pin_range()` 是返回 0 的空 stub**（`include/linux/gpio/driver.h:747-753`）。也就是说：在没开 pinctrl 的配置上，你的桥接代码「成功」了但什么都没做。这个 stub 是为了让驱动不用写 `#ifdef`，代价是配置错了没有任何提示。

### §7.3 `add_pin_ranges` 回调形式

`gc->add_pin_ranges`（`include/linux/gpio/driver.h:455`）是同一件事的回调版本：核心在注册流程里调它（`drivers/gpio/gpiolib.c:778` → `gpiochip_add_pin_ranges()` 在 `:477-483`）。

用它的好处是**时机正确**——注册流程内部调用，此时 `gdev->base` 已经确定；手工调用则必须自己保证在 `gpiochip_add_data()` 之后。

本树有 **10 个**驱动用回调形式：

```
$ grep -rnE 'add_pin_ranges[[:space:]]*=' --include=*.c drivers/ | grep -v gpiolib.c
drivers/pinctrl/pinctrl-keembay.c:1487
drivers/pinctrl/pinctrl-cy8c95x0.c:864
drivers/pinctrl/starfive/pinctrl-starfive-jh7100.c:1317
drivers/pinctrl/intel/pinctrl-baytrail.c:1637
drivers/pinctrl/intel/pinctrl-cherryview.c:1631
drivers/pinctrl/intel/pinctrl-intel.c:1347
drivers/pinctrl/nuvoton/pinctrl-wpcm450.c:1069
drivers/pinctrl/intel/pinctrl-lynxpoint.c:884
drivers/gpio/gpio-tegra186.c:821
drivers/gpio/gpio-merrifield.c:484
```

这个 grep 必须写成 `[[:space:]]*=` 而不是 ` = `：`pinctrl-keembay.c:1487` 用的是 Tab 对齐（`gc->add_pin_ranges	= ...`），用空格版会漏掉它，得到 9 而不是 10。这类「对齐方式导致计数少一」是本文档集里反复出现的统计陷阱。

按架构分：arm64 相关的是 `pinctrl-keembay.c`（Intel Keem Bay）和 `gpio-tegra186.c`；`drivers/pinctrl/intel/` 那四个加 `gpio-merrifield.c` 是 x86，`pinctrl-starfive-jh7100.c` 是 RISC-V，`pinctrl-wpcm450.c` 是 arm32；`pinctrl-cy8c95x0.c` 是 I2C 扩展器，与架构无关。

---

## §8 角色 8：用户态消费者（cdev v2）

字符设备 ABI 的机制说明见 `04-extensions.md` §1，本节只给能跑的调用序列。头文件是 **`include/uapi/linux/gpio.h`**（不是内核的 `include/linux/gpio.h`，见 §0.3）。

### §8.1 请求一组线并写值

```c
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/gpio.h>

	int chipfd, linefd;
	struct gpio_v2_line_request req = { 0 };   /* 必须清零：有 padding */
	struct gpio_v2_line_values vals = { 0 };

	chipfd = open("/dev/gpiochip0", O_RDONLY);
	if (chipfd < 0)
		return -1;

	req.offsets[0] = 5;
	req.offsets[1] = 6;
	req.num_lines  = 2;
	strcpy(req.consumer, "my-app");            /* ≤ 31 字符 + NUL */
	req.config.flags = GPIO_V2_LINE_FLAG_OUTPUT;

	if (ioctl(chipfd, GPIO_V2_GET_LINE_IOCTL, &req) < 0)
		return -1;

	linefd = req.fd;                            /* 内核回填在这里 */
	close(chipfd);                              /* chipfd 可以关了 */

	vals.mask = 0x3;                            /* 操作 bit0、bit1 */
	vals.bits = 0x1;                            /* offset5=1, offset6=0 */
	ioctl(linefd, GPIO_V2_LINE_SET_VALUES_IOCTL, &vals);
```

结构体位置：`gpio_v2_line_request` 在 `include/uapi/linux/gpio.h:196-205`，`gpio_v2_line_values` 在 `:96-99`，13 个 `GPIO_V2_LINE_FLAG_*` 在 `:73-85`，ioctl 号在 `:505-516`。

五个要点：

1. **结构体必须整体清零。** `gpio_v2_line_request` 有 `padding[5]`（`:203`），`gpio_v2_line_config` 有 `padding[5]`（`:170`）。内核会校验 padding 为 0，非零直接 `-EINVAL`。
2. **`req.fd` 是输出参数。** ioctl 成功后新的 line fd 在 `req.fd`（`:204`）里，之后所有值操作都用它，`chipfd` 可以关掉。
3. **`vals.mask` 的 bit 编号是 `req.offsets[]` 的下标**，kerneldoc 写得很明确：`with each bit number corresponding to the index into &struct gpio_v2_line_request.offsets`（`:92-94`）。**不是硬件 offset。** 这一条和内核侧 §1.4 的规则一致。
4. **`GPIO_V2_LINES_MAX` 是 64，且是硬上限**（`:45`）——因为 `bits` / `mask` 是 `__aligned_u64`（`:97-98`），一根线一个 bit，没有扩展空间。
5. **`consumer` 最长 `GPIO_MAX_NAME_SIZE`（32）字节含 NUL**（`:23`、`:198`）。

### §8.2 逐线覆盖配置：`attrs[]`

`config.flags` 是**所有线的默认值**。要给单根线不同配置，用 `config.attrs[]`：

```c
	struct gpio_v2_line_config_attribute *a;

	req.config.flags = GPIO_V2_LINE_FLAG_INPUT;      /* 默认：全部输入 */
	req.config.num_attrs = 1;

	a = &req.config.attrs[0];
	a->mask = 0x2;                                    /* 只作用于 offsets[1] */
	a->attr.id = GPIO_V2_LINE_ATTR_ID_FLAGS;
	a->attr.flags = GPIO_V2_LINE_FLAG_INPUT |
			GPIO_V2_LINE_FLAG_BIAS_PULL_UP;
```

`gpio_v2_line_config_attribute` 在 `include/uapi/linux/gpio.h:148-151`（只有 `attr` 和 `mask` 两个字段），`gpio_v2_line_attribute` 在 `:130-139`（`id` + 一个三选一的 union：`flags` / `values` / `debounce_period_us`），`gpio_v2_line_config` 在 `:167-173`。

**`attr.flags` 是完全覆盖，不是叠加。** kerneldoc：`overrides the default flags contained in the &struct gpio_v2_line_config`（`:156-158`）。上面例子里必须把 `GPIO_V2_LINE_FLAG_INPUT` 再写一遍——只写 `BIAS_PULL_UP` 的话，这根线的方向就没了。

**同一根线被多个 attr 命中时，下标最小的那个生效。** kerneldoc：`If an attribute is associated with a line multiple times then the first occurrence (i.e. lowest index) has precedence`（`:159-161`）。所以 attr 的顺序有语义，不是集合。

**`num_attrs` 上限是 `GPIO_V2_LINE_NUM_ATTRS_MAX`（10）**（`:51`、`:172`）。64 根线只有 10 个 attr 槽位，所以「每根线都不一样」这种需求在一次请求里表达不了——只能分多次请求，或者接受默认值 + 少量例外这个模型。

### §8.3 等边沿事件

```c
	struct gpio_v2_line_event ev;

	req.config.flags = GPIO_V2_LINE_FLAG_INPUT |
			   GPIO_V2_LINE_FLAG_EDGE_RISING |
			   GPIO_V2_LINE_FLAG_EDGE_FALLING;
	req.event_buffer_size = 0;                  /* 0 = 让内核决定 */
	ioctl(chipfd, GPIO_V2_GET_LINE_IOCTL, &req);
	linefd = req.fd;

	for (;;) {
		ssize_t n = read(linefd, &ev, sizeof(ev));

		if (n != sizeof(ev))
			break;
		printf("offset=%u id=%u seqno=%u ts=%llu\n",
		       ev.offset, ev.id, ev.seqno,
		       (unsigned long long)ev.timestamp_ns);
	}
```

`gpio_v2_line_event` 在 `include/uapi/linux/gpio.h:293-300`：`timestamp_ns`、`id`、`offset`、`seqno`、`line_seqno`、`padding[6]`。

**`event_buffer_size` 填 0 是推荐做法。** kerneldoc 说 0 时 `buffer size defaults to a minimum of @num_lines * 16`（`:190-192`）——注意是 **minimum**，实际值可能更大，用户态无法预知。填一个自己算的小值只会更容易丢事件。

**两个序号要分清**：`seqno` 是这个 fd 上所有线的全局序号，`line_seqno` 是这根线自己的序号。检测丢事件要看前者的跳变。

**时间戳的时钟源由 flag 决定**，默认 `CLOCK_MONOTONIC`；设 `GPIO_V2_LINE_FLAG_EVENT_CLOCK_REALTIME`（`:84`）改成 `CLOCK_REALTIME`，设 `GPIO_V2_LINE_FLAG_EVENT_CLOCK_HTE`（`:85`）用硬件时间戳引擎。**后者在 `CONFIG_HTE=n` 时会静默降级**（`04-extensions.md` §1.6），用户态收到的还是软件时间戳，没有任何错误返回——要确认只能对比精度。

### §8.4 不要用 v1

`GPIO_GET_LINEHANDLE_IOCTL`（`:524`）和 `GPIO_GET_LINEEVENT_IOCTL`（`:525`）是 v1 接口，`04-extensions.md` §2 讲了它为什么被拆成两种 fd、以及五条拒绝规则。新代码一律用 v2；理由不是「v1 会被删」（`CONFIG_GPIO_CDEV_V1` 现在还是 `default y`），而是 v1 拿不到偏置配置、去抖和事件时钟源。

用户态更推荐直接用 **libgpiod** 而不是裸 ioctl。本树只有内核侧，libgpiod 是独立项目；`07-debug.md` §6 列了它的命令行工具（`gpiodetect` / `gpioinfo` / `gpioget` / `gpioset` / `gpiomon`）。

---

## §9 反例：整数 legacy API 为什么不要用

```c
	/* ❌ 不要这样写新代码 */
	#include <linux/gpio.h>

	ret = gpio_request_one(37, GPIOF_OUT_INIT_HIGH, "my-reset");
	if (ret)
		return ret;
	gpio_set_value(37, 0);
	gpio_free(37);
```

四条理由，每条都有代码依据（详细剖析见 `04-extensions.md` §5）：

1. **`37` 这个数字是全局的、不稳定的。** 它是 `gdev->base + offset`，而 `base` 在动态分配时取决于**注册顺序**。改一次 DT、换一次内核版本、多一个 GPIO 扩展器，这个数就变了。
2. **整个 legacy 层是 raw 的，不认 `GPIO_ACTIVE_LOW`。** `gpio_request_one()` 用 `gpiod_direction_output_raw()`（`drivers/gpio/gpiolib-legacy.c:48`），`GPIOF_INIT_HIGH` 表示的是**电平高**而不是**逻辑有效**。同一份 DT 下，`gpiod_*` 和 `gpio_*` 会得到相反的结果。
3. **它反过来依赖废弃的 sysfs。** `gpio_request_one()` 在 `:55` 调 `gpiod_export()`——申请一根线的副作用是把它 export 到 `/sys/class/gpio`。这不是可选行为，是写死的。
4. **`gpio_request()` 不是 `gpio_request_one(gpio, 0, label)` 的包装。** 两者走的是不同路径（`drivers/gpio/gpiolib-legacy.c:68-83` vs `:21-66`），名字的相似性会误导人。

顺便记一条已经完全死掉的 API：**`irq_to_gpio()` 在本树上没有任何定义**，但 `Documentation/driver-api/gpio/legacy.rst:390-391` 还在介绍它。查过程见 `99-evidence.md` §6。

**迁移对照表：**

| legacy | 换成 |
|---|---|
| `gpio_request_one(n, GPIOF_OUT_INIT_HIGH, l)` | `devm_gpiod_get(dev, con_id, GPIOD_OUT_HIGH)` |
| `gpio_get_value(n)` / `gpio_set_value(n, v)` | `gpiod_get_value(desc)` / `gpiod_set_value(desc, v)` |
| `gpio_direction_input(n)` | `gpiod_direction_input(desc)` |
| `gpio_to_irq(n)` | `gpiod_to_irq(desc)` |
| `gpio_free(n)` | 什么都不用写（`devm_` 自动） |
| `gpio_is_valid(n)` | 不需要——`IS_ERR()` 已经覆盖 |

---

## §10 路线图与导航

### §10.1 我要写一个 GPIO 控制器驱动，该读哪几节

```
     开始
      │
      ├── 我只是用别人的 GPIO？ ──── 是 ──→ 只读 §1（+ §8 如果在用户态）
      │                                     完
      否
      │
      ▼
   §3 填 struct gpio_chip（所有提供方必读）
      │
      ├── 寄存器布局规整（dat/set/clr/dir，一线一 bit）？
      │        │
      │        是 ──→ §4 用 bgpio_init() 省掉回调
      │        │        之后必须重写 label/base/ngpio/request
      │        否 ──→ 自己写 §3.2 那九个回调
      │
      ├── 这块硬件也做 pinmux？ ── 是 ──→ §7（三个 generic 回调 + gpio-ranges）
      │
      ├── 要支持中断？
      │        │
      │        否 ──→ 完（连 gc->irq 都不用碰）
      │        是
      │        │
      │        ├── 每根线在父控制器里有独立 hwirq？
      │        │        是 ──→ §6 层级（parent_domain + 三个回调）
      │        │        否 ──→ §5 级联（parent_handler + 自己分发）
      │        │
      │        └── 两者可以同时设，见 §6.2
      │
      └── 没有 DT（板级代码）？ ── 是 ──→ §2 lookup table
```

### §10.2 本篇六个最容易踩的坑

| # | 坑 | 症状 | 在哪节 |
|---|---|---|---|
| 1 | `gc.base` 忘了填 `-1` | 第二块 chip 注册 `-EBUSY`；单 chip 板子上不暴露 | §3.1 |
| 2 | `bgpio_init()` 之前设 `ngpio` / `request` | 被静默覆盖；多出的线出现在 `/dev/gpiochipN` | §4.1 |
| 3 | `get_direction()` 没实现 | 方向信息静默说谎，但读值是对的 | §3.4 |
| 4 | `default_type` 填了非 NONE | `WARN()` + 栈回溯，值还是被改成 NONE | §5.1 |
| 5 | `IRQCHIP_IMMUTABLE` 但没调 `gpiochip_{enable,disable}_irq()` | `FLAG_USED_AS_IRQ` 不同步，debugfs 显示错 | §5.3 |
| 6 | 用户态 `vals.mask` 按硬件 offset 编号 | 控错线，且能「正常工作」直到 offsets 不连续 | §8.1 |

### §10.3 下一步读什么

| 想知道 | 去哪 |
|---|---|
| 这些函数的完整签名和错误码 | `01-api.md` |
| `gpio_chip` / `gpio_desc` / `gpio_device` 的字段全貌 | `02-datastructures.md` |
| `devm_gpiochip_add_data()` 里到底发生了什么 | `03-internals.md` Part A |
| raw / 逻辑 / 寄存器 / PAD 四层值怎么换算 | `03-internals.md` §B2 |
| cdev / sysfs / devres / bgpio / regmap 的机制 | `04-extensions.md` |
| DTS 怎么写、`gpio-ranges` / `gpio-hog` 的语法 | `06-devicetree.md` |
| 出问题了怎么查（debugfs / tracepoint / 错误码） | `07-debug.md` |
| 「为什么要这么设计」的大白话解释 | `08-feynman.md` |
| 本篇每条论断的出处、哪些没验证 | `99-evidence.md` §5 |

