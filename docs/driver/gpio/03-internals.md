---
title: 内部实现
---

# 03 · GPIO 子系统实现剖析

> 本篇是 gpiolib 的**实现层**文档。00 讲"是什么"，01 讲"怎么调"，02 讲"数据长什么样"，
> 本篇讲"**代码在什么时刻按什么次序做了什么**"。

---

## §0 导航

### §0.1 本篇的组织轴（只承诺一条）

本篇的**主轴是时间线**（Part A）。凡是"跨越多个时刻、无法排进时间线"的内容，
单独分区到 Part B（横切机制）；凡是"对实现的评价而非描述"的内容，分区到 Part C。

章节号带 A/B/C 前缀，所以任何一处交叉引用（如"见 03 §A2.6"）自带"在哪条轴上"的信息。

| 分区 | 组织方式 | 回答什么问题 |
|---|---|---|
| **Part A** 时间线 | 按内核执行的时间先后 | "从上电到我的 `gpiod_get()` 返回，中间发生了什么？" |
| **Part B** 横切机制 | 按机制归类 | "锁是怎么分层的？极性反转在哪一层做的？" |
| **Part C** 实现评价 | 按问题归类 | "这份代码哪里是欠债？哪里我不该照抄？" |

### §0.2 分层图

```
 ┌────────────────────────────────────────────────────────────────────┐
 │ 消费者                                                              │
 │   驱动:   gpiod_get() / gpiod_set_value() / gpiod_to_irq()          │
 │   用户态: /dev/gpiochipN ioctl        /sys/class/gpio (deprecated)  │
 └───────────────┬──────────────────────┬─────────────────────────────┘
                 │                      │
 ┌───────────────▼──────────────────────▼─────────────────────────────┐
 │ gpiolib 核心（drivers/gpio/gpiolib.c + gpiolib-cdev.c）              │
 │                                                                    │
 │  ┌─── 查找层 ────────────────────────────────────────────────┐      │
 │  │ of_find_gpio() / acpi_find_gpio() / gpiod_find()          │      │
 │  │   → gpio_desc*                                            │      │
 │  └───────────────────────────────────────────────────────────┘      │
 │  ┌─── 语义层 ────────────────────────────────────────────────┐      │
 │  │ FLAG_ACTIVE_LOW 反转 / OPEN_DRAIN / OPEN_SOURCE 改写方向   │      │
 │  │ FLAG_REQUESTED 独占 / FLAG_USED_AS_IRQ 互斥                │      │
 │  └───────────────────────────────────────────────────────────┘      │
 │  ┌─── 编排层 ────────────────────────────────────────────────┐      │
 │  │ 单线 ↔ 位图批量；同芯片合并；快速路径 vs bitmap_alloc()     │      │
 │  └───────────────────────────────────────────────────────────┘      │
 └───────────────┬────────────────────────────────────────────────────┘
                 │  gc->get / gc->set / gc->direction_input / ...
 ┌───────────────▼────────────────────────────────────────────────────┐
 │ 提供方回调（drivers/gpio/gpio-*.c，或 drivers/pinctrl/pinctrl-*.c） │
 │   MMIO 型: 直接 readl/writel（可在 atomic 上下文）                   │
 │   慢总线型: i2c/spi 传输（can_sleep = true）                         │
 └───────────────┬────────────────────────────────────────────────────┘
                 │
 ┌───────────────▼────────────────────────────────────────────────────┐
 │ 硬件：GPIO 控制器寄存器 / 扩展芯片                                   │
 └────────────────────────────────────────────────────────────────────┘
```

**注意这张图和 pinctrl 的交叉**：右侧"提供方回调"里的一大批实现其实住在
`drivers/pinctrl/` 而不是 `drivers/gpio/`——SoC 厂商通常把 pinmux 和 GPIO 做在同一
个 IP 里，于是一个驱动同时注册 `pinctrl_dev` 和 `gpio_chip`。
两者之间还有一条横向的桥（Part B §B6）。

### §0.3 生命周期时间线图

```
 时刻                     动作                                     本篇章节
 ──────────────────────────────────────────────────────────────────────────
 core_initcall       gpiolib_dev_init()                             §A1
                       bus_register(&gpio_bus_type)
                       driver_register(&gpio_stub_drv)
                       alloc_chrdev_region(&gpio_devt, ...)
                       gpiolib_initialized = true
                       gpiochip_setup_devs()  ← 补做早期注册的芯片
                        │
 postcore_initcall   gpiolib_sysfs_init()                           §A1
                       class_register(&gpio_class)
                       补做早期芯片的 sysfs
                        │
 subsys_initcall     gpiolib_debugfs_init()                         §A1
                        │
 驱动 probe          gpiochip_add_data()                            §A2
                       ├─ 分配 gpio_device / gpio_desc[]
                       ├─ 分配全局整数 base 并入 gpio_devices 链
                       ├─ of_gpiochip_add()  ← DT 解析 + hog        §A2.9
                       ├─ 方向初值探测                               §A2.10
                       ├─ pin ranges / machine hogs                 §A2.11
                       ├─ gpiochip_add_irqchip()                    §A2.12
                       └─ gpiochip_setup_dev()  ← chardev + sysfs   §A2.13
                        │
 消费者 probe        gpiod_get(dev, "reset", GPIOD_OUT_LOW)         §A4
                       ├─ of_find_gpio() → gpio_desc*
                       ├─ gpiod_request() → FLAG_REQUESTED + gc->request
                       └─ gpiod_configure_flags() → 极性/偏置/方向
                        │
 运行期             gpiod_set_value() / gpiod_get_value()           §A5
                     request_irq(gpiod_to_irq(desc), ...)           §A6
                        │
 驱动 remove         gpiod_put() → gpiod_free_commit()              §A4.5
                     gpiochip_remove()                              §A7
```

### §0.4 哪些内容不在本篇

| 你想知道的 | 去哪篇 | 为什么不在这里 |
|---|---|---|
| 某个 API 的签名、参数、返回值 | **01** | 本篇只讲实现，不做 API 速查 |
| 有哪些文件、哪些 Kconfig | **01 §0** | "找文件"和"找 API"是同一次查询 |
| `gpio_chip` 有哪些字段、`gpio_desc` 的 flags 位表 | **02 §4** | 本篇假设你已经知道结构长什么样 |
| 锁的层级图、全局变量清单 | **02 §5** | 本篇 §B1 只讲"每条路径实际拿了哪把锁"，不重画层级 |
| 字符设备 ABI（v1/v2 ioctl 细节）、sysfs 属性文件 | **04** | 它们是挂在核心之上的独立子框架 |
| 写一个 GPIO 驱动的代码骨架 | **05** | 本篇讲核心怎么调你，不讲你怎么写 |
| DTS 属性怎么写、谁消费 | **06** | 本篇只说"核心在哪一行读它" |
| tracepoint、debugfs 输出格式、错误码分诊 | **07** | 本篇 §B 会提到机制，格式和用法在 07 |
| "为什么要分 `gpio_chip` 和 `gpio_device`"这类概念疑问 | **08** | 本篇给行号，08 给类比 |

---

# Part A · 时间线

## §A1 三段 initcall

gpiolib 自己的初始化被拆成三个 initcall，**次序由 initcall 级别决定，不是由调用关系决定**：

| 级别 | 函数 | 位置 | 干什么 |
|---|---|---|---|
| `core_initcall` | `gpiolib_dev_init()` | `drivers/gpio/gpiolib.c:4449`，注册于 `:4484` | 注册 `gpio_bus_type`、`gpio_stub_drv`、字符设备号区间 |
| `postcore_initcall` | `gpiolib_sysfs_init()` | `drivers/gpio/gpiolib-sysfs.c:782`，注册于 `:820` | 注册 `gpio_class`（老 sysfs 接口的 class） |
| `subsys_initcall` | `gpiolib_debugfs_init()` | `drivers/gpio/gpiolib.c:4603`，注册于 `:4609` | 建 `/sys/kernel/debug/gpio` |

### §A1.1 `gpiolib_dev_init()` 的四步与回滚

```c
static int __init gpiolib_dev_init(void)                    /* gpiolib.c:4449 */
{
	/* Register GPIO sysfs bus */
	ret = bus_register(&gpio_bus_type);
	if (ret < 0) { pr_err("gpiolib: could not register GPIO bus type\n"); return ret; }

	ret = driver_register(&gpio_stub_drv);
	if (ret < 0) { pr_err("gpiolib: could not register GPIO stub driver\n");
	               bus_unregister(&gpio_bus_type); return ret; }

	ret = alloc_chrdev_region(&gpio_devt, 0, GPIO_DEV_MAX, GPIOCHIP_NAME);
	if (ret < 0) { pr_err("gpiolib: failed to allocate char dev region\n");
	               driver_unregister(&gpio_stub_drv);
	               bus_unregister(&gpio_bus_type); return ret; }

	gpiolib_initialized = true;
	gpiochip_setup_devs();                                  /* :4476 */

#if IS_ENABLED(CONFIG_OF_DYNAMIC) && IS_ENABLED(CONFIG_OF_GPIO)
	WARN_ON(of_reconfig_notifier_register(&gpio_of_notifier));
#endif
	return ret;
}
```

三个要点：

1. **`gpio_stub_drv` 在这里被注册。** 它的 `.probe` 直接 `return 0`（`gpiolib.c:4443-4447`）。
   注册它的唯一目的是让 `fw_devlink=on` 下的依赖链能"完结"——见 02 §1.4。
2. **`GPIO_DEV_MAX` 是 256**（`gpiolib.c:58`）。这是一次性分配的次设备号区间，
   也就是**全系统 gpiochip 数量的硬上限**。
3. **`gpiolib_initialized` 这个布尔量是整段初始化的分水岭。**
   在它被置位之前注册的芯片，`gpiochip_add_data_with_key()` 会跳过
   `gpiochip_setup_dev()`（即不建 chardev、不建 sysfs），只把 `gpio_device`
   挂进 `gpio_devices` 链；置位之后由 `gpiochip_setup_devs()`（`:582`）遍历链表补做。

### §A1.2 `gpiolib_sysfs_init()` 为什么也要"补做一遍"

```c
	/* Scan and register the gpio_chips which registered very
	 * early (e.g. before the class_register above was called).
	 *
	 * We run before arch_initcall() so chip->dev nodes can have
	 * registered, and so arch_initcall() can always gpio_export().
	 */
```
（`gpiolib-sysfs.c:792-797`）

同样的"先入链、后补做"模式在 sysfs 侧再来一次，因为 `gpio_class` 的注册比
`gpio_bus_type` 更晚。遍历时跳过 `gdev->mockdev`（`:800-801`），
即 gpio-mockup 测试设备不进老 sysfs。

这里藏着一条设计债，注释自己写明了（`gpiolib-sysfs.c:804-812`）：

```c
		/*
		 * TODO we yield gpio_lock here because
		 * gpiochip_sysfs_register() acquires a mutex. This is unsafe
		 * and needs to be fixed.
		 * ...
		 */
		spin_unlock_irqrestore(&gpio_lock, flags);
		status = gpiochip_sysfs_register(gdev);
		spin_lock_irqsave(&gpio_lock, flags);
```

在遍历 `gpio_devices` 的过程中放开保护链表的自旋锁，链表可能被并发改动。
详见 Part C §C1。

---

## §A2 `gpiochip_add_data_with_key()` 逐段

**这是全子系统最长、最关键的一个函数**（`drivers/gpio/gpiolib.c:595-848`，约 254 行）。
`gpiochip_add_data()` 是它的宏包装（`include/linux/gpio/driver.h:599`、`:612`），
包装的目的只是把 lockdep 的两个 key 变成 per-call-site 的静态变量。

下表是它的**十三段**，次序用"谁调用谁"核对过，即代码里的物理先后：

| # | 段 | 关键行 | 做什么 |
|---|---|---|---|
| A2.1 | fwnode 选取 | `:605-611` | `gc->fwnode` 优先，否则 `dev_fwnode(gc->parent)` |
| A2.2 | 分配 `gpio_device` 并互指 | `:615-622` | `kzalloc` → `dev.bus` / `dev.parent` / `gdev->chip` / `gc->gpiodev` |
| A2.3 | 固件绑定 | `:624-629` | `of_gpio_dev_init()` / `acpi_gpio_dev_init()` |
| A2.4 | 命名与 device_initialize | `:631-643` | `ida_alloc` → `dev_set_name("gpiochip%d")` → `device_initialize()` |
| A2.5 | owner 归属 | `:645-650` | 父设备的 driver.owner → `gc->owner` → `THIS_MODULE` |
| A2.6 | ngpio 校验与属性回退 | `:652-681` | `ngpio == 0` 时读 DT/ACPI 的 `ngpios` 属性 |
| A2.7 | descs / label | `:683-693` | `kcalloc(ngpio)`、`kstrdup_const(label ?: "unknown")` |
| A2.8 | 分配整数 base 并入链 | `:695-730` | `gpio_lock` 下 `gpiochip_find_base()` + `gpiodev_add_to_list()` |
| A2.9 | 同步原语与线名 | `:732-756` | notifier / rwsem / pin_ranges 链头 / `gpio-line-names` |
| A2.10 | valid_mask + DT 扫描 | `:758-766` | `gpiochip_alloc_valid_mask()` → `of_gpiochip_add()` → `gpiochip_init_valid_mask()` |
| A2.11 | 方向初值探测 | `:768-779` | 逐线 `gc->get_direction()` 或"看有没有 `direction_input`" |
| A2.12 | pin ranges + hogs | `:781-788` | `gpiochip_add_pin_ranges()` / `acpi_gpiochip_add()` / `machine_gpiochip_add()` |
| A2.13 | irqchip + 上台 | `:790-812` | `gpiochip_irqchip_init_*()` → `gpiochip_add_irqchip()` → `gpiochip_setup_dev()` |

下面只展开有"坑"的那几段。

### §A2.2 两个结构体在这里被缝在一起

```c
	gdev = kzalloc(sizeof(*gdev), GFP_KERNEL);              /* :615 */
	if (!gdev) return -ENOMEM;

	gdev->dev.bus = &gpio_bus_type;                         /* :619 */
	gdev->dev.parent = gc->parent;                          /* :620 */
	gdev->chip = gc;                                        /* :621 */
	gc->gpiodev = gdev;                                     /* :622 */
```

`:621` 和 `:622` 这一对赋值就是 02 §1.1 讲的"互指"。
**记住这两行的行号**——因为它们的"解开"在 `gpiochip_remove()` 里只解了一半（§A7）。

`:620` 是**本子系统和父总线唯一的连接点**：GPIO 控制器自己（比如 pl061）是一个
amba/platform 设备，`gdev->dev` 是它的子设备，挂在 `gpio_bus_type` 上。
02 §3 那张图讲的就是这一行。

### §A2.4 一个次序上的反直觉：`dev_set_name()` 在 `device_initialize()` 之前

```c
	gdev->id = ida_alloc(&gpio_ida, GFP_KERNEL);            /* :631 */
	if (gdev->id < 0) { ret = gdev->id; goto err_free_gdev; }

	ret = dev_set_name(&gdev->dev, GPIOCHIP_NAME "%d", gdev->id);   /* :639 */
	if (ret) goto err_free_ida;

	device_initialize(&gdev->dev);                          /* :643 */
```

正常的驱动模型用法是先 `device_initialize()` 再 `dev_set_name()`。这里反过来，
后果是**错误路径必须手动释放名字**：

```c
err_free_dev_name:
	kfree(dev_name(&gdev->dev));
```

也就是说 `:639` 分配的字符串在这段窗口里没有任何 `struct device` 的生命周期管理兜着。

### §A2.6 `ngpio == 0` 时的属性回退，以及那个"故意置零"

```c
	if (gc->ngpio == 0) {
		ret = device_property_read_u32(&gdev->dev, "ngpios", &ngpios);
		if (ret == -ENODATA)
			/*
			 * -ENODATA means that there is no property found and
			 * we want to issue the error message to the user.
			 * Also we want to return different error code for
			 * different cases.
			 */
			ngpios = 0;
		else if (ret)
			goto err_free_descs;

		gc->ngpio = ngpios;
	}

	if (gc->ngpio == 0) {
		chip_err(gc, "tried to insert a GPIO chip with zero lines\n");
		ret = -EINVAL;
		goto err_free_descs;
	}

	if (gc->ngpio > FASTPATH_NGPIO)
		chip_warn(gc, "line cnt %u is greater than fast path cnt %u\n",
			  gc->ngpio, FASTPATH_NGPIO);
```

三个可观察行为：

1. 驱动可以完全不填 `ngpio`，让核心从 DT 的 `ngpios` 属性读（06 会讲这个属性）。
2. **`-ENODATA` 被特意"降级"成 `ngpios = 0`**，为的是让下面那句
   `"tried to insert a GPIO chip with zero lines"` 打出来，而不是静默返回 `-ENODATA`。
   注释里明确说了这是为了"给用户报错"。
3. `ngpio > FASTPATH_NGPIO` **只是 warn，不是错误**。这条 warn 的含义见 §B3。

### §A2.8 全局整数编号空间：三条 TODO 挤在二十行里

```c
	spin_lock_irqsave(&gpio_lock, flags);

	/*
	 * TODO: this allocates a Linux GPIO number base in the global
	 * GPIO numberspace for this chip. In the long run we want to
	 * get *rid* of this numberspace and use only descriptors, but
	 * it may be a pipe dream. It will not happen before we get rid
	 * of the sysfs interface anyways.
	 */                                                     /* :697-703 */
	base = gc->base;
	if (base < 0) {
		base = gpiochip_find_base(gc->ngpio);               /* :709 */
		if (base < 0) { ret = base; spin_unlock_irqrestore(...); goto err_free_label; }

		/*
		 * TODO: it should not be necessary to reflect the assigned
		 * base outside of the GPIO subsystem. Go over drivers and
		 * see if anyone makes use of this, else drop this and assign
		 * a poison instead.
		 */                                                 /* :713-718 */
		gc->base = base;
	}
	gdev->base = base;

	ret = gpiodev_add_to_list(gdev);
	if (ret) {
		chip_err(gc, "GPIO integer space overlap, cannot add chip\n");
		goto err_free_label;   /* 锁内 goto，出口有 unlock */
	}

	for (i = 0; i < gc->ngpio; i++)
		gdev->descs[i].gdev = gdev;

	spin_unlock_irqrestore(&gpio_lock, flags);
```

要点：

- `gc->base < 0`（驱动填 `-1`）是**推荐做法**，核心自己找一段空闲编号。
  `gpiochip_find_base()` 在 `gpiolib.c:184`。
- 驱动如果自己填了正的 `base`，就是**在全局整数空间里占位**，重叠时报
  `"GPIO integer space overlap, cannot add chip"` 并整个注册失败。
- **核心会把分配到的 base 写回 `gc->base`**（`:718`），TODO 说这本来不该发生。
- `gdev->descs[i].gdev = gdev` 这个反向指针在锁内一次性填好——所以任何时刻
  只要你手里有 `gpio_desc*`，`desc->gdev` 一定有效（但 `desc->gdev->chip`
  可能已经是 NULL，见 §B5）。

### §A2.10 `of_gpiochip_add()`：DT 侧的全部工作

`drivers/gpio/gpiolib-of.c:983-1010`：

```c
int of_gpiochip_add(struct gpio_chip *chip)
{
	if (!chip->of_node) return 0;

	if (!chip->of_xlate) {
		chip->of_gpio_n_cells = 2;
		chip->of_xlate = of_gpio_simple_xlate;              /* :991-992 */
	}
	if (chip->of_gpio_n_cells > MAX_PHANDLE_ARGS) return -EINVAL;

	of_gpiochip_init_valid_mask(chip);
	ret = of_gpiochip_add_pin_range(chip);
	if (ret) return ret;

	of_node_get(chip->of_node);
	ret = of_gpiochip_scan_gpios(chip);
	if (ret) of_node_put(chip->of_node);
	return ret;
}
```

**`:991-992` 是一个静默默认值**：驱动不填 `of_gpio_n_cells` / `of_xlate`，
核心就当它是标准的 2-cell（`<&chip offset flags>`）。绝大多数驱动依赖这个默认，
所以你在驱动里搜不到这两个字段的赋值是正常的。反过来，**如果某个控制器的 DT
绑定用了 3 个 cell 而驱动忘了改 `of_gpio_n_cells`，解析出的 offset 会是错的而不会报错**。

`of_gpiochip_scan_gpios()`（`gpiolib-of.c:655-674`）扫的是**芯片节点自己的子节点**：

```c
	for_each_available_child_of_node(chip->of_node, np) {
		if (!of_property_read_bool(np, "gpio-hog"))
			continue;
		ret = of_gpiochip_add_hog(chip, np);
		if (ret < 0) { of_node_put(np); return ret; }
		of_node_set_flag(np, OF_POPULATED);
	}
```

也就是 DT hog（"上电就锁定成某个状态的线"）在**芯片注册的这一刻**就被占用了，
早于任何消费者驱动 probe。arm64 上的真实例子见 06。

hog 节点的解析在 `of_parse_own_gpio()`（`gpiolib-of.c:555-611`），有一个跨节点的细节：

- `#gpio-cells` 读自**芯片节点**（`chip_np = np->parent`）
- `gpios` 的值读自**hog 子节点**，按 `idx * tmp + i` 取
- 三种状态属性 `input` / `output-low` / `output-high` **必须恰好有一个**，否则：
  ```c
	pr_warn("GPIO line %d (%pOFn): no hogging state specified, bailing out\n",
		desc_to_gpio(desc), np);
	return ERR_PTR(-EINVAL);
  ```
- `line-name` 缺省回退到 `np->name`

### §A2.11 方向初值探测：两种猜法

```c
	for (i = 0; i < gc->ngpio; i++) {
		struct gpio_desc *desc = &gdev->descs[i];

		if (gc->get_direction && gpiochip_line_is_valid(gc, i)) {
			assign_bit(FLAG_IS_OUT,
				   &desc->flags, !gc->get_direction(gc, i));
		} else {
			if (!gc->direction_input && !gc->direction_output)
				gc->get_direction = NULL;   /* 见下 */
			assign_bit(FLAG_IS_OUT,
				   &desc->flags, !gc->direction_input);
		}
	}
```
（`gpiolib.c:768-779`，语义按代码原样）

- 有 `get_direction` 的芯片：**逐线读硬件**，这是唯一准确的做法。
- 没有 `get_direction` 的芯片：核心退化成"**你有 `direction_input` 回调吗？
  有就假设所有线都是输入，没有就假设都是输出**"。
  这是一个纯静态的推测，和硬件复位值可能完全不符。
- 注意这一段是 `ngpio` 次循环，对 `can_sleep` 的芯片（i2c/spi 扩展器）
  意味着注册时会有 `ngpio` 次总线传输。

### §A2.13 上台顺序：先 chardev，后 sysfs——而且 kerneldoc 解释了原因

```c
static int gpiochip_setup_dev(struct gpio_device *gdev)     /* gpiolib.c:522 */
{
	ret = gcdev_register(gdev, gpio_devt);
	if (ret) return ret;

	/* From this point, the .release() function cleans up gpio_device */
	gdev->dev.release = gpiodevice_release;                  /* :531 */

	ret = gpiochip_sysfs_register(gdev);
	if (ret) goto err_remove_device;

	dev_dbg(&gdev->dev, "registered GPIOs %d to %d on %s\n", ...);
	return 0;
	...
}
```

`gpiochip_add_data_with_key()` 的 kerneldoc 把理由写清楚了：

> By first adding the chardev, and then adding the device, we get a device node
> entry in sysfs under /sys/bus/gpio/devices/gpiochipN/dev that can be used for
> coldplug of device nodes and other udev business. We can do this only if
> gpiolib has been initialized. Otherwise, defer until later.

**`:531` 是整个函数最重要的一行分界线**：在它执行之前，`gdev` 的清理是手工
`kfree` 链；在它执行之后，`gdev` 的清理由 `put_device()` → `gpiodevice_release()`
接管。这条分界线直接产生了下一节那两套互斥的错误路径。

---

## §A3 注册失败：一个函数里的两套互斥清理

`gpiolib.c:814-847` 的错误路径是个**双出口**结构，不是常见的单链 goto ladder：

```c
	if (gpiolib_initialized) {
		ret = gpiochip_setup_dev(gdev);
		if (ret)
			goto err_remove_irqchip;
	}
	return 0;

err_remove_irqchip:            gpiochip_irqchip_remove(gc);
err_remove_irqchip_mask:       gpiochip_irqchip_free_valid_mask(gc);
err_remove_acpi_chip:          acpi_gpiochip_remove(gc);
err_remove_of_chip:            gpiochip_free_hogs(gc);
                               of_gpiochip_remove(gc);
err_free_gpiochip_mask:        gpiochip_remove_pin_ranges(gc);
                               gpiochip_free_valid_mask(gc);
                               if (gdev->dev.release) {
                                       /* release() has been registered by
                                          gpiochip_setup_dev */
                                       put_device(&gdev->dev);
                                       goto err_print_message;          /* :822-827 */
                               }
err_remove_from_list:          spin_lock; list_del(&gdev->list); spin_unlock;
err_free_label:                kfree_const(gdev->label);
err_free_descs:                kfree(gdev->descs);
err_free_dev_name:             kfree(dev_name(&gdev->dev));
err_free_ida:                  ida_free(&gpio_ida, gdev->id);
err_free_gdev:                 kfree(gdev);
err_print_message:
	/* failures here can mean systems won't boot... */
	if (ret != -EPROBE_DEFER)
		pr_err("%s: GPIOs %d..%d (%s) failed to register, %d\n", __func__,
		       base, base + (int)gc->ngpio - 1,
		       gc->label ? : "generic", ret);
	return ret;
```

两条规则：

1. **`if (gdev->dev.release)` 是"我到底该 `kfree` 还是该 `put_device`"的判据。**
   `.release` 非空 ⇒ `gpiochip_setup_dev()` 已经跑过 ⇒ 所有 `kfree` 都必须**跳过**，
   否则和 `gpiodevice_release()` 里的释放形成 double free。
2. **`-EPROBE_DEFER` 不打错误日志。** 这是有意的：慢总线上的 GPIO 扩展器
   在 i2c 控制器就绪前 probe 会正常地 defer 若干次。

一个容易看漏的点：`err_remove_from_list` 到 `err_free_gdev` 这一段是
**fall-through 的**，没有 `break`/`return`。这是内核错误路径的惯用写法，
但配合上面那个 `goto err_print_message` 的跳跃，让这个函数成为整份 gpiolib
里最难静态审查的地方。

---

## §A4 消费者取线：`gpiod_get_index()` 的三路查找

`gpiod_get()` / `gpiod_get_optional()` / `devm_gpiod_get()` 全部收敛到
`gpiod_get_index()`（`gpiolib.c:4084-4151`）。它只做四件事：

```
   gpiod_get_index(dev, con_id, idx, flags)
     │
     ├─(1) 查找 ────────────────────────────────────────────┐
     │      is_of_node(fwnode)?  → of_find_gpio()           │  三路互斥 + 兜底
     │      is_acpi_node(fwnode)? → acpi_find_gpio()        │  （arm64 只走 DT 和
     │      都没查到 / 都不是?    → gpiod_find()  ← 板级表    │   板级表两路）
     │                                                       │
     ├─(2) gpiod_request(desc, con_id ?: dev_name(dev))     │  独占 + gc->request
     ├─(3) gpiod_configure_flags(desc, con_id, lflags, dflags)│  极性/偏置/方向
     └─(4) blocking_notifier_call_chain(GPIOLINE_CHANGED_REQUESTED)
```

### §A4.1 第一路：DT 查找与那张 quirk 表

`of_find_gpio()`（`gpiolib-of.c:504-538`）先按约定试两个后缀：

```c
	/* Try GPIO property "foo-gpios" and "foo-gpio" */
	for (i = 0; i < ARRAY_SIZE(gpio_suffixes); i++) {
		if (con_id)
			snprintf(prop_name, sizeof(prop_name), "%s-%s", con_id, gpio_suffixes[i]);
		else
			snprintf(prop_name, sizeof(prop_name), "%s", gpio_suffixes[i]);

		desc = of_get_named_gpiod_flags(dev->of_node, prop_name, idx, &of_flags);
		if (!gpiod_not_found(desc))
			break;
	}

	/* Properly named GPIO was not found, try workarounds */
	for (q = of_find_gpio_quirks; gpiod_not_found(desc) && *q; q++)
		desc = (*q)(dev->of_node, con_id, idx, &of_flags);
```

`gpio_suffixes[]` 是 `{ "gpios", "gpio" }`（`drivers/gpio/gpiolib.h:81`）。
所以 `gpiod_get(dev, "reset", ...)` 找的是 `reset-gpios`，然后 `reset-gpio`。
`con_id` 为 NULL 时找的是裸的 `gpios` / `gpio`。

**quirk 表**（`gpiolib-of.c:495-501`）是给"违反 `-gpios` 命名约定的历史绑定"开的后门：

| quirk 函数 | 适用条件 | 实际去读的属性 |
|---|---|---|
| `of_find_spi_gpio` | 节点 compatible 不是 `spi-gpio` | `gpio-sck` / `gpio-mosi` / `gpio-miso` |
| `of_find_spi_cs_gpio` | 节点 compatible 是 `fsl,spi` / `aeroflexgaisler,spictrl` / `ibm,ppc4xx-spi`，且 `con_id == "cs"` | 裸 `gpios` |
| `of_find_regulator_gpio` | `con_id` ∈ `{"wlf,ldoena", "wlf,ldo1ena", "wlf,ldo2ena"}` | 同名属性（无后缀） |
| `of_find_arizona_gpio` | `con_id == "wlf,reset"` | 同名属性 |
| `of_find_usb_gpio` | `con_id == "fcs,int_n"`，且 `CONFIG_TYPEC_FUSB302` | 同名属性 |

表以 NULL 结尾，`for` 循环靠 `*q` 终止。
**这五个 quirk 是封闭集合**——新驱动必须用 `-gpios` 后缀，不会再有第六个。

单条属性的解析走 `of_get_named_gpiod_flags()`（`gpiolib-of.c:239-275`）：

```c
	ret = of_parse_phandle_with_args_map(np, propname, "gpio", index, &gpiospec);
	if (ret) { pr_debug(...); return ERR_PTR(ret); }

	chip = of_find_gpiochip_by_xlate(&gpiospec);
	if (!chip) { desc = ERR_PTR(-EPROBE_DEFER); goto out; }

	desc = of_xlate_and_get_gpiod_flags(chip, &gpiospec, flags);
	if (IS_ERR(desc)) goto out;

	if (flags)
		of_gpio_flags_quirks(np, propname, flags, index);
```

**控制器还没注册 ⇒ `-EPROBE_DEFER`**，这就是 GPIO 消费者驱动经常 probe 好几次的原因。
`of_parse_phandle_with_args_map(..., "gpio", ...)` 说明 gpiolib 支持
`gpio-map` 类型的 nexus 节点（连接器上的 GPIO 重映射），这一点在 06 展开。

### §A4.2 第三路兜底：板级查找表

不是 DT 也不是 ACPI（或前两路没找到）时走 `gpiod_find()`，
在 `gpio_lookup_list` 里按 `dev_name(dev)` + `con_id` + `idx` 匹配
`struct gpiod_lookup`。arm64 上这条路基本只出现在少数用 board file 的老平台和
一些 MFD 内部构造的查找表里。API 见 01 §8。

### §A4.3 第二步：`gpiod_request()` 的独占语义在哪一行

`gpiod_request()`（`:2014`）→ `gpiod_request_commit()`（`:1919`）：

```c
	spin_lock_irqsave(&gpio_lock, flags);

	if (test_and_set_bit(FLAG_REQUESTED, &desc->flags) == 0) {
		desc_set_label(desc, label ? : "?");
	} else {
		ret = -EBUSY;
		goto out_free_unlock;
	}

	if (gc->request) {
		/* gc->request may sleep */
		spin_unlock_irqrestore(&gpio_lock, flags);
		offset = gpio_chip_hwgpio(desc);
		if (gpiochip_line_is_valid(gc, offset))
			ret = gc->request(gc, offset);
		else
			ret = -EINVAL;
		spin_lock_irqsave(&gpio_lock, flags);

		if (ret) {
			desc_set_label(desc, NULL);
			clear_bit(FLAG_REQUESTED, &desc->flags);
			goto out_free_unlock;
		}
	}
	if (gc->get_direction) {
		/* gc->get_direction may sleep */
		spin_unlock_irqrestore(&gpio_lock, flags);
		gpiod_get_direction(desc);
		spin_lock_irqsave(&gpio_lock, flags);
	}
	spin_unlock_irqrestore(&gpio_lock, flags);
```

四个要点：

1. **`test_and_set_bit(FLAG_REQUESTED)` 就是整个"GPIO 是独占资源"的全部实现。**
   拿不到就是 `-EBUSY`。没有等待、没有排队。
2. **`gc->request` 和 `gc->get_direction` 都在放开 `gpio_lock` 之后调用**，
   因为它们可能睡眠。也就是说这个函数会**两次放锁再重拿**。
3. 一个开机期的注释值得留意：
   ```c
	/* NOTE:  gpio_request() can be called in early boot,
	 * before IRQs are enabled, for non-sleeping (SOC) GPIOs.
	 */
   ```
4. `valid_mask` 里被屏蔽掉的线，`gc->request` 存在时返回 `-EINVAL`；
   `gc->request` 不存在时**这个检查根本不会做**——即被屏蔽的线仍然能被 request 成功。

### §A4.4 第三步：`gpiod_configure_flags()` 把 flags 落成 desc 位

`gpiolib.c:4010-4068`。次序是固定的：极性 → 开漏/开源 → 偏置互斥校验 → 偏置 →
transitory → 方向。

```c
	if (lflags & GPIO_ACTIVE_LOW)  set_bit(FLAG_ACTIVE_LOW, &desc->flags);

	if (lflags & GPIO_OPEN_DRAIN)  set_bit(FLAG_OPEN_DRAIN, &desc->flags);
	else if (dflags & GPIOD_FLAGS_BIT_OPEN_DRAIN) {
		/*
		 * This enforces open drain mode from the consumer side.
		 * This is necessary for some busses like I2C, but the lookup
		 * should *REALLY* have specified them as open drain in the
		 * first place, so print a little warning here.
		 */
		set_bit(FLAG_OPEN_DRAIN, &desc->flags);
		gpiod_warn(desc,
			   "enforced open drain please flag it properly in DT/ACPI DSDT/board file\n");
	}

	if (lflags & GPIO_OPEN_SOURCE) set_bit(FLAG_OPEN_SOURCE, &desc->flags);

	if (((lflags & GPIO_PULL_UP)   && (lflags & GPIO_PULL_DOWN)) ||
	    ((lflags & GPIO_PULL_UP)   && (lflags & GPIO_PULL_DISABLE)) ||
	    ((lflags & GPIO_PULL_DOWN) && (lflags & GPIO_PULL_DISABLE))) {
		gpiod_err(desc,
			  "multiple pull-up, pull-down or pull-disable enabled, invalid configuration\n");
		return -EINVAL;
	}
	...
	/* No particular flag request, return here... */
	if (!(dflags & GPIOD_FLAGS_BIT_DIR_SET)) {
		gpiod_dbg(desc, "no flags found for %s\n", con_id);
		return 0;
	}

	/* Process flags */
	if (dflags & GPIOD_FLAGS_BIT_DIR_OUT)
		ret = gpiod_direction_output(desc, !!(dflags & GPIOD_FLAGS_BIT_DIR_VAL));
	else
		ret = gpiod_direction_input(desc);
```

三个可观察行为：

1. **`lflags`（来自 DT/板级表）和 `dflags`（来自消费者的 `GPIOD_*` 枚举）不对等。**
   开漏可以从两边任一边来，但从消费者侧来会打一句 warn——因为"这本来该写在 DT 里"。
2. **三种偏置互斥，同时给两个就是 `-EINVAL`**，整个 `gpiod_get()` 失败。
3. **`GPIOD_ASIS` 走的是那个 early return**，只落极性/偏置，不碰方向。
   这一点对"接管 bootloader 已经配好的引脚"很关键。

### §A4.5 释放：`gpiod_put()` → `gpiod_free_commit()` 清掉哪些位

`gpiolib.c:2036-2082`。**这个函数是"哪些状态属于一次 request"的权威清单**：

```c
		kfree_const(desc->label);
		desc_set_label(desc, NULL);
		clear_bit(FLAG_ACTIVE_LOW,   &desc->flags);
		clear_bit(FLAG_REQUESTED,    &desc->flags);
		clear_bit(FLAG_OPEN_DRAIN,   &desc->flags);
		clear_bit(FLAG_OPEN_SOURCE,  &desc->flags);
		clear_bit(FLAG_PULL_UP,      &desc->flags);
		clear_bit(FLAG_PULL_DOWN,    &desc->flags);
		clear_bit(FLAG_BIAS_DISABLE, &desc->flags);
		clear_bit(FLAG_EDGE_RISING,  &desc->flags);
		clear_bit(FLAG_EDGE_FALLING, &desc->flags);
		clear_bit(FLAG_IS_HOGGED,    &desc->flags);
#ifdef CONFIG_OF_DYNAMIC
		desc->hog = NULL;
#endif
#ifdef CONFIG_GPIO_CDEV
		WRITE_ONCE(desc->debounce_period_us, 0);
#endif
```

**`FLAG_IS_OUT` 不在清单里**。释放一根线不会把方向复位，**硬件保持原状**。
这既是特性（交接给下一个消费者时电平不抖）也是坑（下一个消费者如果用
`GPIOD_ASIS` 就会继承上一任的方向）。
`FLAG_USED_AS_IRQ` 也不在清单里——它由 `gpiochip_unlock_as_irq()` 单独管（§A6）。

`gpiod_free()`（`:2084`）在 commit 成功后才 `module_put(desc->gdev->owner)` +
`put_device(&gdev->dev)`；失败时 `WARN_ON(extra_checks)`——即只有开了
`CONFIG_DEBUG_GPIO` 才会喊。

---

## §A5 一次读、一次写

### §A5.1 单线读：三层薄包装 + 一处反转

```
 gpiod_get_value(desc)                       gpiolib.c:2821
   ├─ VALIDATE_DESC(desc)
   ├─ WARN_ON(desc->gdev->chip->can_sleep)   ← 慢总线芯片在这里被抓
   ├─ value = gpiod_get_raw_value_commit(desc)          :2648
   │            └─ gc->get(gc, offset)  → 归一化成 0/1
   ├─ if (value < 0) return value
   └─ if (test_bit(FLAG_ACTIVE_LOW, &desc->flags))
          value = !value;                    ← 唯一的反转点 :2832-2833
```

`gpiod_get_raw_value()`（`:2802`）走同一条路但**跳过最后一步**。
`*_cansleep()` 变体（`:3450` / `:3467`）区别只是把 `WARN_ON(can_sleep)`
换成 `might_sleep()`。

### §A5.2 单线写：开漏/开源在这里改写"方向"

```
 gpiod_set_value(desc, value)                gpiolib.c:3137
   ├─ VALIDATE_DESC_VOID(desc)
   ├─ WARN_ON(desc->gdev->chip->can_sleep)
   └─ gpiod_set_value_nocheck(desc, value)              :3114
        ├─ if (test_bit(FLAG_ACTIVE_LOW, ...))  value = !value;   :3116-3117
        ├─ if (test_bit(FLAG_OPEN_DRAIN, ...))
        │     gpio_set_open_drain_value_commit(desc, value)        :2898
        │       └─ value ? gc->direction_input(gc, offset)
        │               : gc->direction_output(gc, offset, 0)
        ├─ else if (test_bit(FLAG_OPEN_SOURCE, ...))
        │     gpio_set_open_source_value_commit(desc, value)       :2923
        │       └─ value ? gc->direction_output(gc, offset, 1)
        │               : gc->direction_input(gc, offset)
        └─ else gpiod_set_raw_value_commit(desc, value)
                  └─ gc->set(gc, offset, value)
```

**这是 gpiolib 里最"不像 set 的 set"**：开漏线写 1 会被翻译成
"把引脚切成输入（让外部上拉把它拉高）"，根本没碰输出寄存器。
所以一根声明为 open-drain 的线，它的 `FLAG_IS_OUT` 会随着你写的值来回变。

反转发生在**开漏判断之前**，所以 `active-low` + `open-drain` 是可以叠加的，
语义是"逻辑 1 → 物理 0 → 拉低"。

### §A5.3 批量：快速路径与"同芯片合并"

`gpiod_get_array_value_complex()`（`:2679-2789`）是四个数组 API 的公共实现，
它做的两件事一件都不显然：

**(1) `array_info` 有效性校验是三个条件的合取**：

```c
	if (array_info && array_info->desc == desc_array &&
	    array_size <= array_info->size &&
	    (void *)array_info == desc_array + array_info->size) {
```

注释解释了第三个条件：`array_info` 必须**紧跟在 `desc_array` 之后**，
即两者来自同一次 `gpiod_get_array()` 分配。任何一个条件不满足就
`array_info = NULL`，直接退回逐线循环——**静默降级，没有任何日志**。

**(2) 校验通过后走的是"一次 `get_multiple` + 一次 `bitmap_xor`"**：

```c
		ret = gpio_chip_get_multiple(array_info->chip,
					     array_info->get_mask, value_bitmap);
		if (ret) return ret;

		if (!raw && !bitmap_empty(array_info->invert_mask, array_size))
			bitmap_xor(value_bitmap, value_bitmap,
				   array_info->invert_mask, array_size);

		i = find_first_zero_bit(array_info->get_mask, array_size);
		if (i == array_size) return 0;
```

极性反转在这条路上是**整个位图一次 XOR**，而不是逐线 `if`。
`get_mask` 里为 0 的位（不属于同一芯片、或不连续的线）落到后面的通用循环里补做。

**(3) 通用循环的栈上位图与它的上限**：

```c
		DECLARE_BITMAP(fastpath_mask, FASTPATH_NGPIO);
		DECLARE_BITMAP(fastpath_bits, FASTPATH_NGPIO);

		if (likely(gc->ngpio <= FASTPATH_NGPIO)) {
			mask = fastpath_mask;
			bits = fastpath_bits;
		} else {
			gfp_t flags = can_sleep ? GFP_KERNEL : GFP_ATOMIC;
			mask = bitmap_alloc(gc->ngpio, flags);
			if (!mask) return -ENOMEM;
			bits = bitmap_alloc(gc->ngpio, flags);
			if (!bits) { bitmap_free(mask); return -ENOMEM; }
		}
```

这就是 §A2.6 那句 warn 的含义：`ngpio > FASTPATH_NGPIO` 的芯片，
**每次批量读写都要在堆上分配两个位图**，在 atomic 上下文里还得用 `GFP_ATOMIC`。
`FASTPATH_NGPIO` = `CONFIG_GPIOLIB_FASTPATH_LIMIT`（`gpiolib.c:68`），默认 512。

**(4) "同芯片合并"的内层 do-while**：

```c
		/* collect all inputs belonging to the same chip */
		first = i;
		do {
			__set_bit(gpio_chip_hwgpio(desc_array[i]), mask);
			i++;
			if (array_info)
				i = find_next_zero_bit(array_info->get_mask, array_size, i);
		} while ((i < array_size) && (desc_array[i]->gdev->chip == gc));
```

判据是 `desc_array[i]->gdev->chip == gc`，**只合并数组里物理相邻的同芯片段**。
数组交错排列（A 芯片、B 芯片、A 芯片）时会退化成多次 `get_multiple`。
所以 `gpiod_get_array()` 返回的顺序对性能有影响——它是 DT 里属性的顺序。

写方向的 `gpiod_set_array_value_complex()`（`:2976`）结构对称，
额外多一层"开漏/开源的线不能进 `set_multiple`，得逐线走 commit"的分流。

---

## §A6 一次中断

GPIO 中断有两条完全独立的路径：**内核消费者**和**用户态消费者**。

### §A6.1 内核消费者：`gpiod_to_irq()` → `request_irq()`

```
 gpiod_to_irq(desc)                          gpiolib.c:3237
   │  （注意：这里不能用 VALIDATE_DESC，注释解释了原因——
   │    "requires this function to not return zero on an invalid
   │     descriptor but rather a negative error number"）
   ├─ !desc || IS_ERR || !gdev || !gdev->chip → -EINVAL
   └─ gc->to_irq(gc, offset)                 ← 被核心强行设成 gpiochip_to_irq
        │
        └─ gpiochip_to_irq(gc, offset)       gpiolib.c:1409
             ├─ !gc->irq.initialized → -EPROBE_DEFER          :1417
             ├─ !gpiochip_irqchip_irq_valid(gc, offset) → -ENXIO
             ├─ 层级 domain: 填 irq_fwspec{param[0]=child_offset_to_irq(offset),
             │                             param[1]=IRQ_TYPE_NONE}
             │               → irq_create_fwspec_mapping()
             └─ 级联 domain: irq_create_mapping(domain, offset)
```

`gpiod_to_irq()` 里还有一句：`gc->to_irq` 返回 0 时转成 `-ENXIO`
（注释：`/* Zero means NO_IRQ */`）。

**`-EPROBE_DEFER` 这条分支是一个显式的竞态防护**，注释写明了：

```c
	/*
	 * Avoid race condition with other code, which tries to lookup
	 * an IRQ before the irqchip has been properly registered,
	 * i.e. while gpiochip is still being brought up.
	 */
	if (!gc->irq.initialized)
		return -EPROBE_DEFER;
```

对应的写侧在 `gpiochip_add_irqchip()` 末尾，用了一个显式 `barrier()`（§B4.3）。

`request_irq()` 之后，irq 核心会调 `irqchip->irq_request_resources`，
对 GPIO irqchip 就是 `gpiochip_irq_reqres()` → `gpiochip_lock_as_irq()`（`:3283`）：

```c
	/* To be valid for IRQ the line needs to be input or open drain */
	if (test_bit(FLAG_IS_OUT, &desc->flags) &&
	    !test_bit(FLAG_OPEN_DRAIN, &desc->flags)) {
		chip_err(gc, "%s: tried to flag a GPIO set as output for IRQ\n", __func__);
		return -EIO;
	}

	set_bit(FLAG_USED_AS_IRQ,     &desc->flags);
	set_bit(FLAG_IRQ_IS_ENABLED,  &desc->flags);

	/*
	 * If the consumer has not set up a label (such as when the
	 * IRQ is referenced from .to_irq()) we set up a label here
	 * so it is clear this is used as an interrupt.
	 */
	if (!desc->label)
		desc_set_label(desc, "interrupt");
```

三个可观察行为：

1. **输出线不能当中断源**，除非它是开漏（开漏线在写 1 时其实是输入）。错误码是 `-EIO`。
2. 前面还有一段"如果是快芯片且有 `get_direction`，先刷新一次方向"
   （`:3292-3303`），因为方向可能被别人在背后改过。
3. **`debugfs` / `sysfs` 里看到 label 是 `interrupt` 的线，就是走这条路占用的**——
   没有任何驱动显式起了这个名字。

### §A6.2 用户态消费者：hardirq 打时间戳，threaded handler 造事件

`drivers/gpio/gpiolib-cdev.c` 的边沿事件路径是**两级的**：

```
 硬件中断
   │
   ├─ edge_irq_handler(irq, p)               gpiolib-cdev.c:943
   │    /* Just store the timestamp in hardirq context so we get it as
   │       close in time as possible to the actual event. */
   │    line->timestamp_ns = line_event_timestamp(line);
   │    if (lr->num_lines != 1)
   │            line->req_seqno = atomic_inc_return(&lr->seqno);
   │    return IRQ_WAKE_THREAD;
   │
   └─ edge_irq_thread(irq, p)                gpiolib-cdev.c:897
        memset(&le, 0, sizeof(le));   /* Do not leak kernel stack to userspace */
        if (line->timestamp_ns) le.timestamp_ns = line->timestamp_ns;
        else {
                /* We may be running from a nested threaded interrupt in
                   which case we didn't get the timestamp from
                   edge_irq_handler(). */
                le.timestamp_ns = line_event_timestamp(line);
                if (lr->num_lines != 1) line->req_seqno = atomic_inc_return(&lr->seqno);
        }
        line->timestamp_ns = 0;
        switch (READ_ONCE(line->edflags) & GPIO_V2_LINE_EDGE_FLAGS) {
        case EDGE_BOTH:    le.id = line_event_id(gpiod_get_value_cansleep(line->desc));
        case EDGE_RISING:  le.id = GPIO_V2_LINE_EVENT_RISING_EDGE;
        case EDGE_FALLING: le.id = GPIO_V2_LINE_EVENT_FALLING_EDGE;
        default:           return IRQ_NONE;
        }
        line->line_seqno++;  ...  linereq_put_event(lr, &le);
```

**拆成两级的唯一目的是时间戳精度**：`edge_irq_handler` 在 hardirq 上下文里
只做一件事——记时间。事件的其余内容（读电平判方向、算序号、入 kfifo）
都在线程上下文里做。

**`if (line->timestamp_ns)` 的 else 分支不是防御性代码**：慢总线上的 GPIO
扩展器只有 threaded IRQ，`edge_irq_handler` 根本不会跑，此时时间戳在线程里补取，
精度自然差一截。注释把这个场景写清楚了。

**`EDGE_BOTH` 需要回读电平才能判断是上升还是下降沿**
（`gpiod_get_value_cansleep(line->desc)`），这意味着窄脉冲可能被判反。
这是 ABI 的固有限制，不是 bug。

软件消抖走另一条：

```
 debounce_irq_handler(irq, p)                gpiolib-cdev.c:980
   └─ mod_delayed_work(system_wq, &line->work,
                       usecs_to_jiffies(READ_ONCE(line->debounce_period_us)));
      return IRQ_HANDLED;                    ← 注意：不唤醒线程

 debounce_work_func(work)                    gpiolib-cdev.c:990
   ├─ level = (HTE ? line->raw_level : gpiod_get_raw_value_cansleep(desc))
   ├─ if (level < 0) { pr_debug_ratelimited("debouncer failed to read line value\n"); return; }
   ├─ if (READ_ONCE(line->level) == level) return;    ← 电平没变就丢弃
   └─ WRITE_ONCE(line->level, level); → 判边沿 → 入 kfifo
```

消抖用的是 **`system_wq` 共享工作队列**，不是专用的。
`mod_delayed_work` 的语义是"每次抖动都把定时器往后推"，即静默期算法。

---

## §A7 `gpiochip_remove()`：拆除次序，以及它没做的事

`gpiolib.c:874-918`，全文只有 45 行，但每一行的先后都有讲究：

```c
void gpiochip_remove(struct gpio_chip *gc)
{
	struct gpio_device *gdev = gc->gpiodev;
	unsigned long	flags;
	unsigned int	i;

	down_write(&gdev->sem);                                  /* :880 */

	/* FIXME: should the legacy sysfs handling be moved to gpio_device? */
	gpiochip_sysfs_unregister(gdev);                         /* :883 */
	gpiochip_free_hogs(gc);
	/* Numb the device, cancelling all outstanding operations */
	gdev->chip = NULL;                                       /* :886 */
	gpiochip_irqchip_remove(gc);
	acpi_gpiochip_remove(gc);
	of_gpiochip_remove(gc);
	gpiochip_remove_pin_ranges(gc);
	gpiochip_free_valid_mask(gc);
	/*
	 * We accept no more calls into the driver from this point, so
	 * NULL the driver data pointer.
	 */
	gdev->data = NULL;                                       /* :896 */

	spin_lock_irqsave(&gpio_lock, flags);
	for (i = 0; i < gdev->ngpio; i++) {
		if (gpiochip_is_requested(gc, i))
			break;
	}
	spin_unlock_irqrestore(&gpio_lock, flags);

	if (i != gdev->ngpio)
		dev_crit(&gdev->dev,
			 "REMOVING GPIOCHIP WITH GPIOS STILL REQUESTED\n");

	/*
	 * The gpiochip side puts its use of the device to rest here:
	 * if there are no userspace clients, the chardev and device will
	 * be removed, else it will be dangling until the last user is
	 * gone.
	 */
	gcdev_unregister(gdev);
	up_write(&gdev->sem);
	put_device(&gdev->dev);
}
```

四个要点：

1. **`down_write(&gdev->sem)` 是和字符设备侧的握手。** cdev 那边每个 poll / read /
   ioctl 都拿 `down_read(&gdev->sem)`，所以 remove 会等到所有在飞的用户态操作退出。
2. **`gdev->chip = NULL`（`:886`）是"麻醉"这个设备的开关**，注释就叫
   "Numb the device"。这一行之后，所有 `VALIDATE_DESC` 的调用者都会走
   "backing chip is gone" 分支（§B5）。
   注意 `gc->gpiodev` **没有**被清成 NULL——互指只解开了一半。
3. **"还有线被占用"只喊不拦。** `dev_crit()` 打完就继续往下走。
   而 `gpiochip_remove()` 的 kerneldoc 写的是：
   > A gpio_chip with any GPIOs still requested may not be removed.

   **这是文档与代码的冲突**，记入 99 §6。实际行为是"照样拆，只留一条 crit 日志"。
4. **`put_device()` 不一定真的释放。** 注释说明了：如果还有用户态客户端持有
   `/dev/gpiochipN` 的 fd，`gpio_device` 会"dangling until the last user is gone"。
   最终释放在 `gpiodevice_release()`（`:495`）。

`gpiochip_remove_pin_ranges()` 在这里和 §A3 的错误路径里各出现一次
（`:820` 和 `:890`），是唯一在两条路上都出现的清理函数。

---

# Part B · 横切机制

Part A 是"某一时刻做了什么"。Part B 是"某个机制在所有时刻怎么表现"——
这些内容无法排进单一时间线，所以单独分区。

## §B1 每条路径实际拿了哪把锁

锁的**层级图和全局变量清单在 02 §5**，本节只回答"这条代码路径实际拿了什么"。

| 路径 | 拿的锁 | 关键细节 |
|---|---|---|
| `gpiochip_add_data_with_key()` 分配 base 并入链 | `gpio_lock`（自旋，`irqsave`） | 只保护 `gpio_devices` 链和 `descs[i].gdev` 的填充（`gpiolib.c:695-730`） |
| `gpiod_request_commit()` | `gpio_lock` | **中途两次放锁**：调 `gc->request` 前放、调 `gc->get_direction` 前放（`:1945-1966`） |
| `gpiod_free_commit()` | `gpio_lock` | 调 `gc->free` 前放锁；`might_sleep()` 在函数开头（`:2042`） |
| `gpiod_get_value()` / `gpiod_set_value()` | **不拿任何锁** | 只有 `test_bit` / `assign_bit` 的原子位操作 |
| `gpiod_*_array_value_complex()` | **不拿任何锁** | 位图在栈上或堆上，per-call |
| `gpiochip_lock_as_irq()` / `unlock_as_irq()` | **不拿任何锁** | 纯 `set_bit`/`clear_bit`（`:3283`、`:3337`） |
| `gpiochip_remove()` | `gdev->sem`（rwsem，写） + `gpio_lock`（查占用时） | rwsem 是和 cdev 的握手，见 §B5 |
| cdev 的 poll / read / ioctl | `down_read(&gdev->sem)` | 每次进入都拿，退出前放 |
| cdev 改配置 | `lr->config_mutex` | 保护 `linereq` 内部的 per-line 配置 |
| sysfs 全部操作 | `sysfs_lock`（mutex，`gpiolib-sysfs.c:37`） | 和 `gpio_lock` 是两套独立的锁 |
| 板级查找表 | `gpio_lookup_lock`（mutex，`gpiolib.c:77`） | 只在 `gpiod_add_lookup_table*` / `gpiod_find` 里 |
| machine hogs | `gpio_machine_hogs_mutex`（`gpiolib.c:81`） | 只在 `gpiod_add_hogs` / `machine_gpiochip_add` 里 |
| pin ranges 链 | **无锁** | `gdev->pin_ranges` 只在注册/注销时改，假设不并发（§C1） |

三条必须记住的规则：

1. **读写单根线的快速路径完全无锁。** 并发保护全靠"一根线只有一个消费者"
   （`FLAG_REQUESTED` 独占）这个前提。两个消费者拿到同一个 desc 的唯一合法途径是
   `GPIOD_FLAGS_BIT_NONEXCLUSIVE`——而那条路径核心自己承认是 hack（§C2）。
2. **`gpio_lock` 是自旋锁，所以任何可能睡眠的回调都必须在放锁之后调。**
   `gpiod_request_commit()` 里那两次放锁-重拿就是这个约束的产物。
3. **`gc->can_sleep` 是"我是慢总线芯片"的自陈标记**，核心用它来：
   - 在非 cansleep 的 API 里 `WARN_ON(gc->can_sleep)`
   - 在 `gpiochip_add_irqchip()` 里拒绝级联中断（§B4.4）
   - 在 `gpiod_free_commit()` 里 `might_sleep_if(gc->can_sleep)`

## §B2 三套值语义在各层的位置

00 §3 立了四层概念（PAD 电平 / 寄存器值 / raw 值 / 逻辑值）。本节回答"转换发生在哪一行"。

```
 消费者看到的:  逻辑值 (logical)
                      ▲
                      │  FLAG_ACTIVE_LOW ⇒ 取反
                      │  读: gpiolib.c:2832-2833  (gpiod_get_value)
                      │  写: gpiolib.c:3116-3117  (gpiod_set_value_nocheck)
                      │  批量读: bitmap_xor with invert_mask (:2705-2707)
                      │  批量写: 见 gpiod_set_array_value_complex
                      ▼
                  raw 值
                      ▲
                      │  FLAG_OPEN_DRAIN / FLAG_OPEN_SOURCE
                      │  ⇒ 把"写值"翻译成"改方向"（不是改寄存器值！）
                      │  gpiolib.c:2898 / :2923
                      ▼
                寄存器值 (gc->set / gc->get)
                      ▲
                      │  驱动自己的 readl/writel 或 i2c/spi 传输
                      ▼
                  PAD 电平
```

**反转只有这两处**（加批量的两处 XOR）。任何其他地方看到"取反"都不是极性反转，
比如 `assign_bit(FLAG_IS_OUT, ..., !gc->get_direction(gc, i))`
里的 `!` 是因为 `get_direction` 返回 1 表示输入。

**`_raw_` 系列 API 跳过第一层但不跳过第二层。**
`gpiod_direction_output_raw()`（`:2397`）绕过极性，
但 `gpiod_direction_output()`（`:2416`）在反转之后还会走开漏/开源分流。

三个坑，01 §2 也列了，这里给行号：
1. 开漏线的 `FLAG_IS_OUT` 会随写入值变化——因为写 1 就是切成输入。
2. `active-low` 是 desc 的属性而不是芯片的属性，所以同一个芯片上不同线可以不同。
3. `gpiod_free_commit()` 清 `FLAG_ACTIVE_LOW` 但不清 `FLAG_IS_OUT`（§A4.5）。

## §B3 快速路径的判定条件

批量 API 的性能特征由**两个独立判定**决定，很容易混淆：

| 判定 | 条件 | 不满足的后果 | 位置 |
|---|---|---|---|
| **能否用 `array_info` 一次搞定** | `array_info` 非空 且 `desc == desc_array` 且 `array_size <= size` 且 `array_info` 紧跟 `desc_array` | 静默退回逐线循环 | `gpiolib.c:2692-2712` |
| **位图能否放栈上** | `gc->ngpio <= FASTPATH_NGPIO` | `bitmap_alloc()` × 2，atomic 上下文用 `GFP_ATOMIC` | `gpiolib.c:2722-2737` |

第一个判定的"紧跟"条件意味着：**你自己拼出来的 `desc_array` 永远走不了这条路**，
只有 `gpiod_get_array()` 一次性分配的那块内存才行（它把 `gpio_array` 放在
`gpio_desc*` 数组尾部）。02 §4 有这块内存的布局。

第二个判定的 `FASTPATH_NGPIO` = `CONFIG_GPIOLIB_FASTPATH_LIMIT`（`gpiolib.c:68`），
默认 512。**这是编译期常量**，因为 `DECLARE_BITMAP` 需要编译期大小。

内层的"同芯片合并"（§A5.3）是第三层优化，不受上面两个判定影响。

## §B4 irqchip：两种模型，一次运行时改写

### §B4.1 分叉点只有一行

```c
	if (gpiochip_hierarchy_is_hierarchical(gc)) {
		ret = gpiochip_hierarchy_add_domain(gc);
		if (ret) return ret;
	} else {
		gc->irq.domain = irq_domain_create_simple(fwnode, gc->ngpio,
			gc->irq.first, gc->irq.domain_ops ?: &gpiochip_domain_ops, gc);
		if (!gc->irq.domain) return -EINVAL;
	}
```
（`gpiolib.c:1580-1591`）

而 `gpiochip_hierarchy_is_hierarchical()` 的全部实现是：

```c
static bool gpiochip_hierarchy_is_hierarchical(struct gpio_chip *gc)
{
	return !!gc->irq.parent_domain;                          /* :1246-1249 */
}
```

**判据就是"驱动有没有填 `parent_domain`"，一个字段决定走哪条路。**
`CONFIG_IRQ_DOMAIN_HIERARCHY` 关掉时，`gpiochip_hierarchy_add_domain()`
的 stub 直接 `return -EINVAL`（`:1287-1290`），而 `is_hierarchical()`
的 stub 返回 false（`:1292`），所以关掉这个配置的内核里层级驱动会走级联路径。

### §B4.2 层级模型的两个静默默认

```c
	if (!gc->irq.child_to_parent_hwirq || !gc->irq.fwnode) {
		chip_err(gc, "missing irqdomain vital data\n");
		return -EINVAL;
	}

	if (!gc->irq.child_offset_to_irq)
		gc->irq.child_offset_to_irq = gpiochip_child_offset_to_irq_noop;

	if (!gc->irq.populate_parent_alloc_arg)
		gc->irq.populate_parent_alloc_arg =
			gpiochip_populate_parent_fwspec_twocell;
```
（`gpiolib.c:1213-1227`）

- **必填只有两个**：`child_to_parent_hwirq` 和 `fwnode`。缺任一 → `-EINVAL` +
  `"missing irqdomain vital data"`。
- `child_offset_to_irq` 缺省是 noop（offset 直接当 irq 号）。
- `populate_parent_alloc_arg` 缺省是 **twocell**——绝大多数 arm64 GIC 都是 2-cell，
  所以驱动通常不用填。四 cell 的变体是 `gpiochip_populate_parent_fwspec_fourcell`
  （`:1267-1282`，param[0] 和 param[2] 恒为 0）。

domain 用 `irq_domain_create_hierarchy(parent_domain, 0, gc->ngpio, fwnode,
&gc->irq.child_irq_domain_ops, gc)` 建（`:1230-1236`），
注意 ops 用的是**内嵌在 `gpio_irq_chip` 里的 `child_irq_domain_ops`**，
由 `gpiochip_hierarchy_setup_domain_ops()` 现场填好（`:1228`）——
所以层级 GPIO irqchip 的 domain ops 是 per-chip 的，不是共享的。

### §B4.3 `initialized` 的写侧与那个显式 `barrier()`

`gpiochip_add_irqchip()` 的末尾（`gpiolib.c:1617-1624`）：

```c
	/*
	 * Using barrier() here to prevent compiler from reordering
	 * gc->irq.initialized before initialization of above
	 * GPIO chip irq members.
	 */
	barrier();

	gc->irq.initialized = true;
```

读侧是 `gpiochip_to_irq()` 的 `-EPROBE_DEFER`（§A6.1）。
这是一对**手写的、不带内存屏障语义的**发布-订阅：`barrier()` 只阻止编译器重排，
不发 CPU 屏障指令。在弱序架构（arm64 就是）上严格来说不够，
但实际上 `initialized` 的读侧发生在另一个驱动的 probe 里，中间隔着
device 模型的锁，所以有足够的 happens-before。

### §B4.4 「可睡眠芯片 + 级联中断」是硬错误

```c
	if (gc->irq.parent_handler && gc->can_sleep) {
		chip_err(gc, "you cannot have chained interrupts on a chip that may sleep\n");
		return -EINVAL;
	}
```
（`gpiolib.c:1555-1558`）

**这就是"为什么 I2C/SPI GPIO 扩展器必须用 nested threaded IRQ"的结构性原因。**
级联 handler（`parent_handler`）跑在父中断的 hardirq 上下文里，
而慢总线芯片读中断状态寄存器需要发一次 i2c 传输，会睡眠。
扩展器驱动的正确写法是 `gc->irq.threaded = true` + 自己 `request_threaded_irq()`。

同一函数里还有两个"礼貌性"检查：

```c
	if (WARN(fwnode && type != IRQ_TYPE_NONE,
		 "%pfw: Ignoring %u default trigger\n", fwnode, type))
		type = IRQ_TYPE_NONE;

	if (gc->to_irq)
		chip_warn(gc, "to_irq is redefined in %s and you shouldn't rely on it\n", __func__);
	gc->to_irq = gpiochip_to_irq;
```

**驱动自己填的 `to_irq` 会被无条件覆盖**，只留一句 warn。

### §B4.5 `gpiochip_set_irq_hooks()`：核心在运行时改写驱动的 `irq_chip`

这是 gpiolib 里**最反直觉的一段代码**（`gpiolib.c:1491-1535`）：

```c
static void gpiochip_set_irq_hooks(struct gpio_chip *gc)
{
	struct irq_chip *irqchip = gc->irq.chip;

	if (irqchip->flags & IRQCHIP_IMMUTABLE)
		return;

	chip_warn(gc, "not an immutable chip, please consider fixing it!\n");

	if (!irqchip->irq_request_resources && !irqchip->irq_release_resources) {
		irqchip->irq_request_resources = gpiochip_irq_reqres;
		irqchip->irq_release_resources = gpiochip_irq_relres;
	}

	if (WARN_ON(gc->irq.irq_enable))
		return;

	/* Check if the irqchip already has this hook... */
	if (irqchip->irq_enable == gpiochip_irq_enable ||
	    irqchip->irq_mask == gpiochip_irq_mask) {
		/*
		 * ...and if so, give a gentle warning that this is bad
		 * practice.
		 */
		chip_info(gc,
			  "detected irqchip that is shared with multiple gpiochips: please fix the driver.\n");
		return;
	}

	if (irqchip->irq_disable) {
		gc->irq.irq_disable = irqchip->irq_disable;      /* 存档 */
		irqchip->irq_disable = gpiochip_irq_disable;     /* 原位替换 */
	} else {
		gc->irq.irq_mask = irqchip->irq_mask;
		irqchip->irq_mask = gpiochip_irq_mask;
	}

	if (irqchip->irq_enable) {
		gc->irq.irq_enable = irqchip->irq_enable;
		irqchip->irq_enable = gpiochip_irq_enable;
	} else {
		gc->irq.irq_unmask = irqchip->irq_unmask;
		irqchip->irq_unmask = gpiochip_irq_unmask;
	}
}
```

**它在改的是驱动那个 `static struct irq_chip` 的字段。** 不是拷贝，是原地修改。
这带来三个连锁后果：

1. **两个 gpiochip 共用一个 `static irq_chip` 就会出错**——第二次进来时
   `irqchip->irq_mask` 已经是 `gpiochip_irq_mask` 了，于是核心检测到并打印
   `"detected irqchip that is shared with multiple gpiochips: please fix the driver."`
   然后**直接 return，第二个芯片的 hook 就没装上**。
2. **`static const struct irq_chip` 会导致写只读内存崩溃**——这正是
   `IRQCHIP_IMMUTABLE` 存在的理由：新写法把 `irq_chip` 声明成 `const`，
   打上 `IRQCHIP_IMMUTABLE`，函数第一行就 return。
3. **所有非 IMMUTABLE 的芯片都会收到一句 warn**：
   `"not an immutable chip, please consider fixing it!"`。
   这是 6.1 里的迁移期状态，dmesg 里刷一堆是正常的。

四个包装函数（`gpiolib.c:1458-1489`）的**次序是不对称的**：

| 包装 | 次序 |
|---|---|
| `gpiochip_irq_mask` | 先调存档的 `irq_mask`，**再** `gpiochip_disable_irq` |
| `gpiochip_irq_unmask` | **先** `gpiochip_enable_irq`，再调存档的 `irq_unmask` |
| `gpiochip_irq_enable` | 先 `gpiochip_enable_irq`，再调存档的 `irq_enable` |
| `gpiochip_irq_disable` | 先调存档的 `irq_disable`，再 `gpiochip_disable_irq` |

规律是"**打开时先标记后动硬件，关闭时先动硬件后清标记**"——
保证 `FLAG_IRQ_IS_ENABLED` 这个标记在任何时刻都不会比硬件状态"更乐观"。

拆除时 `gpiochip_irqchip_remove()`（`:1628-1678`）把这套改写逆转回去，
同样以 `!(irqchip->flags & IRQCHIP_IMMUTABLE)` 为条件，
`irq_request_resources` / `irq_release_resources` 恢复成 NULL，
`irq_enable` / `irq_disable` 从 `gc->irq.*` 的存档还原，最后 `gc->irq.chip = NULL`。
`domain_is_allocated_externally` 为真时跳过 domain 的拆除——
这是 `gpiochip_irqchip_add_domain()` 那条路径（全树只有
`drivers/gpio/gpio-regmap.c:279` 一个调用者）留下的钩子。

### §B4.6 IMMUTABLE 芯片要自己做什么

改写机制被跳过之后，四件事就落回驱动身上，`GPIOCHIP_IRQ_RESOURCE_HELPERS`
（`include/linux/gpio/driver.h:640-642`，**全树唯一的定义**）帮你填两件：

```c
#define GPIOCHIP_IRQ_RESOURCE_HELPERS					\
		.irq_request_resources  = gpiochip_irq_reqres,		\
		.irq_release_resources  = gpiochip_irq_relres
```

另外两件必须手写：在 `.irq_mask` / `.irq_unmask`（或 `.irq_disable` / `.irq_enable`）
里自己调 `gpiochip_disable_irq()` / `gpiochip_enable_irq()`。
pl061 的完整示例在 01 §6.4。

## §B5 热拔：`gdev->chip = NULL` 之后世界变成什么样

`gpiochip_remove()` 的 `:886` 把 `gdev->chip` 置 NULL。之后：

```c
static int validate_desc(const struct gpio_desc *desc, const char *func)
{
	if (!desc)
		return 0;
	if (IS_ERR(desc)) { pr_warn("%s: invalid GPIO (errorpointer)\n", func);
	                    return PTR_ERR(desc); }
	if (!desc->gdev)  { pr_warn("%s: invalid GPIO (no device)\n", func);
	                    return -EINVAL; }
	if (!desc->gdev->chip) {
		dev_warn(&desc->gdev->dev, "%s: backing chip is gone\n", func);
		return 0;                                        /* ← 注意是 0 */
	}
	return 1;
}

#define VALIDATE_DESC(desc) do { \
	int __valid = validate_desc(desc, __func__); \
	if (__valid <= 0) \
		return __valid; \
	} while (0)
```
（`gpiolib.c:1982-2011`）

**"chip 已经消失"返回的是 0，不是错误码。** 于是：

| 调用者 | 芯片消失后的行为 |
|---|---|
| `gpiod_get_value(desc)` | **返回 0**（看起来像"读到低电平"），只在 dmesg 留一句 `"gpiod_get_value: backing chip is gone"` |
| `gpiod_direction_input(desc)` | 返回 0（看起来像"设置成功"） |
| `gpiod_set_value(desc)` | 静默返回（`VALIDATE_DESC_VOID`） |
| `gpiod_to_irq(desc)` | **`-EINVAL`**——它故意不用 `VALIDATE_DESC`，注释解释了原因（§A6.1） |

这个设计对 `desc == NULL`（optional GPIO 没配）是对的——"没有这根线"就当无操作。
但把"芯片被拔了"和"这根线是可选的且不存在"归并到同一个返回值，
意味着**消费者无法区分二者**。记入 Part C。

真正阻止 use-after-free 的是 `gdev->sem`：

```
 cdev 侧                                gpiochip_remove() 侧
   down_read(&gdev->sem)   ────┐
   ... poll / read / ioctl     │
   up_read(&gdev->sem)     ────┘
                                        down_write(&gdev->sem)   ← 等上面全部退出
                                        gdev->chip = NULL
                                        gcdev_unregister(gdev)
                                        up_write(&gdev->sem)
                                        put_device(&gdev->dev)   ← 可能不释放
```

`gdev->sem` 的 kerneldoc 在 `drivers/gpio/gpiolib.h:42-44`。
`put_device()` 之后 `gpio_device` 可能仍然活着（有用户态 fd 时），
最终由 `gpiodevice_release()`（`gpiolib.c:495`）释放。

## §B6 pinctrl 桥接

同一根引脚同时是"pinctrl 的 pin"和"gpiolib 的 line"时，两套编号空间要对齐。

### §B6.1 三个建立映射的入口

| 函数 | 位置 | 用法 |
|---|---|---|
| `gpiochip_add_pin_range` | `gpiolib.c:1855` | 按 offset+npins 建连续映射。**kerneldoc 明确说 DT 驱动直接调这个是 DEPRECATED**，应该用 DTS 的 `gpio-ranges` |
| `gpiochip_add_pingroup_range` | `gpiolib.c:1797` | 按 pinctrl 的 **group 名**建映射，pins 数组从 `pinctrl_get_group_pins()` 取 |
| `gc->add_pin_ranges` 回调 | 由 `gpiochip_add_pin_ranges()`（`:477`）调 | 注册时机内建，驱动在回调里调上面两个 |

两个函数的公共部分是一样的四行：

```c
	pin_range->range.id   = gpio_offset;      /* "Use local offset as range ID" */
	pin_range->range.gc   = gc;
	pin_range->range.name = gc->label;
	pin_range->range.base = gdev->base + gpio_offset;   /* ← 全局整数编号！ */
```

**`range.base` 用的是全局整数编号 `gdev->base + gpio_offset`**，
不是 chip-local offset。这就是 §A2.8 那句 TODO 说"不可能在摆脱 sysfs 之前
摆脱全局编号空间"的一个具体原因——pinctrl 的 gpio range API 就是按全局编号定义的。
`gpiochip_generic_request()`（`:1743`）同理，传给 pinctrl 的是
`gc->gpiodev->base + offset`。

`gpiochip_remove_pin_ranges()`（`:1899`）遍历 `gdev->pin_ranges`，
`list_del` + `pinctrl_remove_gpio_range` + `kfree`，**全程无锁**。

### §B6.2 三个"generic"回调的短路优化

```c
int gpiochip_generic_request(struct gpio_chip *gc, unsigned int offset)
{
#ifdef CONFIG_PINCTRL
	if (list_empty(&gc->gpiodev->pin_ranges))
		return 0;
#endif
	return pinctrl_gpio_request(gc->gpiodev->base + offset);
}
```
（`gpiolib.c:1743-1751`，`gpiochip_generic_free` 在 `:1759` 结构相同）

**没建 pin range 的芯片，`gpiochip_generic_request` 是个空操作。**
所以驱动无脑填上这三个回调是安全的：没有 pinctrl 关联时它们什么都不做。

**`gpiochip_generic_config`（`:1776`）没有这个短路**——它无条件调
`pinctrl_gpio_set_config()`。所以一个没建 range 的芯片如果把
`.set_config = gpiochip_generic_config`，`gpiod_set_config()` 会一路进 pinctrl 然后失败。

---

# Part C · 实现评价

Part A/B 描述代码。Part C 是判断——"这段实现哪里有问题、我不该照抄什么"。

## §C1 源码自陈的设计债（13 条）

以下全部是源码注释里 gpiolib 维护者自己写下的 FIXME / TODO / REVISIT。
按"读者最可能踩到"排序：

| # | 位置 | 注释原文（节选） | 影响 |
|---|---|---|---|
| 1 | `gpiolib.c:697-703` | `TODO: this allocates a Linux GPIO number base in the global GPIO numberspace ... we want to get *rid* of this numberspace ... It will not happen before we get rid of the sysfs interface anyways.` | 全局整数编号空间无法删除，见 §C3 |
| 2 | `gpiolib.c:713-718` | `TODO: it should not be necessary to reflect the assigned base outside of the GPIO subsystem ... assign a poison instead.` | 核心写回 `gc->base`，驱动可能依赖这个值 |
| 3 | `gpiolib.c:645-650` | `/* TODO: remove chip->owner */` | `gc->owner` 是历史遗留，应由父设备的 driver.owner 提供 |
| 4 | `gpiolib.c:882` | `FIXME: should the legacy sysfs handling be moved to gpio_device?` | 老 sysfs 的生命周期和 `gpio_device` 不同步 |
| 5 | `gpiolib.c:1368-1369` | `TODO: move these activate/deactivate in under the hierarchicial irqchip section` | 层级 irqchip 代码的组织问题（纯组织，无行为影响） |
| 6 | `gpiolib.c:2644-2645` | `REVISIT when debugging, consider adding some instrumentation to ensure ...` | 读路径的调试插桩位置待定 |
| 7 | `gpiolib.c:4133` | `It is a bit of a hack.` （NONEXCLUSIVE 路径） | 见 §C2 |
| 8 | `gpiolib.c:4136` | `FIXME: Make this more sane and safe.` （同上） | 同上 |
| 9 | `gpiolib-sysfs.c:804-812` | `TODO we yield gpio_lock here because gpiochip_sysfs_register() acquires a mutex. This is unsafe and needs to be fixed.` | 遍历 `gpio_devices` 时放开保护链表的锁 |
| 10 | `gpiolib-sysfs.c:179-185` | `This should be done in the irq_request_resources callback when the irq is requested, but a few drivers currently fail to do so. Remove this redundant call ... when those drivers have been fixed.` | sysfs 导出时多余地 `gpiochip_lock_as_irq()` |
| 11 | `gpiolib-of.c:56-57` | `FIXME: get rid of those external users by converting them to GPIO descriptors and let them all use gpiod_count()` | `of_gpio_count()` 的外部用户还没迁到 `gpiod_count()` |
| 12 | `include/linux/of_gpio.h:15` | `#include <linux/gpio.h> /* FIXME: Shouldn't be here */` | 头文件把废弃的整数 API 拖进所有 OF GPIO 用户 |
| 13 | `gpiolib-cdev.c:2397-2398` | `FIXME: find a non-racy way to retrieve this information. Maybe a lock common to both frameworks?` | cdev 与 irq 框架之间读线状态有竞态 |

复现命令：

```bash
grep -rn 'FIXME\|TODO\|REVISIT' drivers/gpio/gpiolib.c drivers/gpio/gpiolib-of.c \
    drivers/gpio/gpiolib-sysfs.c drivers/gpio/gpiolib-cdev.c \
    drivers/gpio/gpiolib.h include/linux/of_gpio.h
```

## §C2 `.set` 返回 void：错误被吞掉

`struct gpio_chip` 的写回调（`include/linux/gpio/driver.h:437`）：

```c
	void			(*set)(struct gpio_chip *gc,
					unsigned int offset, int value);
```

**返回类型是 `void`。** 于是整条写路径没有任何地方能报告失败：

```
 gpiod_set_value(desc, v)            → void
 gpiod_set_raw_value(desc, v)        → void
 gpiod_set_value_cansleep(desc, v)   → void
 gpiod_set_raw_value_cansleep(desc,v)→ void
```

对 MMIO 芯片这没问题（`writel` 不会失败）。
但对 **I2C/SPI GPIO 扩展器**，一次 `gpiod_set_value_cansleep()` 背后是一次总线传输，
传输失败时**消费者驱动完全无法感知**——它会以为电平已经改了。

对比：`gc->get` 返回 `int`，可以返回负错误码，读路径是能报错的。
想要错误码的消费者只能用数组 API：`gpiod_set_array_value*()` 返回 `int`
（因为 `gc->set_multiple` 也返回 void，实际上它只能报告位图分配失败，
不能报告传输失败）。

## §C3 全局整数编号空间：为什么删不掉

| 依赖方 | 依赖点 | 位置 |
|---|---|---|
| 老 sysfs 接口 | `/sys/class/gpio/gpioN` 的 N 就是全局编号 | `gpiolib-sysfs.c` 全篇 |
| 老整数 API | `gpio_request(gpio, ...)` 的第一个参数 | `gpiolib-legacy.c:21`、`:68` |
| pinctrl 桥接 | `pinctrl_gpio_request(base + offset)`、`range.base` | `gpiolib.c:1750`、`:1815`、`:1873` |
| debugfs | 输出里的 `gpio-%-3d` 就是全局编号 | `gpiolib.c:4505`、`:4512` |
| tracepoint | `trace_gpio_value(desc_to_gpio(desc), ...)` | `gpiolib.c` 九处调用 |

也就是说 TODO #1 里那句"It will not happen before we get rid of the sysfs
interface anyways"是准确的：**即使删掉 sysfs，pinctrl 桥接和 tracepoint 还挂着**。
新代码应该只用 `gpio_desc*`，不要碰整数编号。01 §10 列了全部废弃的整数 API。

## §C4 NONEXCLUSIVE：核心自己承认的 hack

`gpiod_get_index()`（`gpiolib.c:4125-4140`）：

```c
	ret = gpiod_request(desc, con_id ?: devname);
	if (ret) {
		if (!(ret == -EBUSY && flags & GPIOD_FLAGS_BIT_NONEXCLUSIVE))
			return ERR_PTR(ret);

		/*
		 * This happens when there are several consumers for
		 * the same GPIO line: we just return here without
		 * further initialization. It is a bit of a hack.
		 * This is necessary to support fixed regulators.
		 *
		 * FIXME: Make this more sane and safe.
		 */
		dev_info(dev, "nonexclusive access to GPIO for %s\n", con_id ?: devname);
		return desc;
	}
```

三件事同时发生：

1. **`gpiod_configure_flags()` 被跳过。** 第二个消费者拿到的 desc 保持
   第一个消费者配置的极性、偏置、方向——它自己传的 `flags` 被完全忽略。
2. **`GPIOLINE_CHANGED_REQUESTED` 通知不发。**
3. **引用计数不增。** 于是第一个消费者 `gpiod_put()` 之后，第二个消费者手里的
   desc 就变成"未 request 状态的 desc"，但它并不知道。

存在的唯一理由是 `regulator-fixed`：多个 regulator 消费者共享一个使能脚。
新代码不应该用这个标志。

## §C5 文档与代码的冲突指向

99 §6 记了 **9 条**内核文档与代码不一致的地方。和本篇直接相关的三条：

| 冲突 | 文档说 | 代码是 |
|---|---|---|
| `gpiochip_remove()` 的前置条件 | kerneldoc：`A gpio_chip with any GPIOs still requested may not be removed.` | 只 `dev_crit("REMOVING GPIOCHIP WITH GPIOS STILL REQUESTED\n")` 然后照拆（`gpiolib.c:905-907`） |
| `gpiochip_request_own_desc()` 的签名 | `Documentation/driver-api/gpio/driver.rst:766-769` 写 4 参数 | 实际 5 参数（`gpiolib.c:2142-2146`） |
| 层级 irqchip 的父 mask 辅助函数名 | `driver.rst:593`、`:609` 写 `irq_mask_mask_parent()` / `irq_mask_unmask_parent()` | 真名是 `irq_chip_mask_parent`（`include/linux/irq.h:669`）/ `irq_chip_unmask_parent`（`:671`），文档里那两个名字全树不存在 |

其余 6 条（`irq_to_gpio()` 已死、整数 API 全是 raw 语义、板级 flags 数量、
consumer.rst 遗漏的 API、`legacy.rst` 丢 `const`、sysfs 章节重复）见 99 §6。

## §C6 一句话总结：这份实现的形状

gpiolib 是**一个被两次历史包袱压住的框架**：

1. **全局整数编号空间**（来自 2007 年的 `gpio_request(N)` 时代）——
   三条 TODO、五个依赖方，删不掉（§C3）。
2. **可变的 `irq_chip`**（来自"gpiochip 就一个、随便改"的时代）——
   `gpiochip_set_irq_hooks()` 原地改写驱动的静态结构体，
   `IRQCHIP_IMMUTABLE` 是 6.1 里进行中的迁移（§B4.5）。

去掉这两层，核心逻辑其实很干净：一个 desc 数组、一组标志位、
两次极性反转、一层批量位图编排。读代码时先把这两层历史识别出来，
剩下的就好懂了。

---

## 下一步读什么

| 你想知道 | 去 |
|---|---|
| 字符设备 ABI 的完整形态（v1/v2 ioctl、事件结构） | **04** |
| 老 sysfs 接口的属性文件和 deprecated 现状 | **04** |
| 写一个 GPIO 提供方驱动的代码骨架 | **05** |
| 写一个 GPIO 消费者驱动的代码骨架 | **05** |
| `gpio-ranges` / `gpio-reserved-ranges` / hog 的 DTS 写法 | **06** |
| tracepoint 的字段、debugfs 输出格式、错误码分诊表 | **07** |
| "为什么要分 `gpio_chip` 和 `gpio_device`"这类概念问题 | **08** |
| 本篇每条论断的出处和自审记录 | **99** |
