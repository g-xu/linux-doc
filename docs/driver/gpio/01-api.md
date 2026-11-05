---
title: API 参考
---

# GPIO 子系统：代码地图与对外 API

> 目标架构 arm64；ACPI 不在范围内（`gpiolib-acpi.c` 及其 API 一律不列）。
> 本篇定位：**「找文件」和「找 API」的唯一入口**。
> 表格统一三列：**API / 说明 / 备注**。签名只写函数名 + 形参名，不带类型；返回值语义、上下文限制、废弃状态一律进「备注」。
> 每节开头给出头文件与实现文件，**不逐 API 标行号**——行号集中维护在 `03-internals.md` 和 `99-evidence.md`。

---

## §0 代码地图

### §0.1 核心实现文件（`drivers/gpio/`）

| 文件 | 职责 | 对应 Kconfig |
|---|---|---|
| `gpiolib.c` | 核心：`gpio_chip` 注册/注销、描述符分配、request/free、方向、读写值、数组路径、irqchip 集成、pinctrl 桥接、查找表、hog、debugfs | `GPIOLIB` |
| `gpiolib.h` | **内部**头文件：`struct gpio_device`、`struct gpio_desc`、`struct gpio_array`、`FLAG_*` 位、`gpio_lock`、`gpio_devices` | — |
| `gpiolib-of.c` | 设备树查找：`of_find_gpio()`、`of_xlate`、`gpio-ranges` / `gpio-reserved-ranges` / `gpio-hog` / `gpio-line-names` 解析、极性 quirk | `OF_GPIO` |
| `gpiolib-of.h` | of 部分对核心的内部接口 | — |
| `gpiolib-cdev.c` | `/dev/gpiochipN` 字符设备，v1 + v2 两代 UAPI、边沿事件、软件去抖、lineinfo 监视 | `GPIO_CDEV`（v1 另受 `GPIO_CDEV_V1`） |
| `gpiolib-cdev.h` | cdev 对核心的注册/注销接口 | — |
| `gpiolib-sysfs.c` | `/sys/class/gpio/`（**已废弃**）：export/unexport、direction/value/edge/active_low 属性 | `GPIO_SYSFS` |
| `gpiolib-sysfs.h` | sysfs 对核心的注册接口 | — |
| `gpiolib-devres.c` | 所有 `devm_*` 包装（文件头注释自陈「based on kernel/irq/devres.c」） | `GPIOLIB`（无独立开关） |
| `gpiolib-legacy.c` | **已废弃**的整数 API：`gpio_request()` / `gpio_request_one()` / `gpio_request_array()` / `gpio_free*()` | `GPIOLIB` |
| `gpiolib-acpi.c` / `.h` | **不在本文档集范围**（arm64-only 决定，见 `99-evidence.md` §2.7） | `GPIO_ACPI` |

**辅助库**（不是核心，但被大量驱动复用，属于「写驱动时要知道」的一层）：

| 文件 | 职责 | Kconfig |
|---|---|---|
| `gpio-mmio.c` | 「bgpio」库：一组标准 MMIO 寄存器布局（8/16/32/64 位、大小端）的通用访问器，`bgpio_init()` 一次填满 `gpio_chip` 的读写回调 | `GPIO_GENERIC` |
| `gpio-regmap.c` | 基于 regmap 的通用 GPIO 控制器（I2C/SPI 扩展器常用），也是全树唯一调用 `gpiochip_irqchip_add_domain()` 的地方 | `GPIO_REGMAP` |

**控制器驱动**：`drivers/gpio/gpio-*.c` 共 171 个文件。本文档集只把三个当作代表深入（选法与理由见 `99-evidence.md` §2.2）：

| 驱动 | 为什么选它 |
|---|---|
| `gpio-pl061.c` | 最简全功能实现：MMIO + 级联 irqchip + `IRQCHIP_IMMUTABLE` 新写法；本树 arm64 DTS 实际在用（`arch/arm64/boot/dts/hisilicon/hi6220.dtsi`） |
| `gpio-tegra186.c` | 层级（hierarchical）irqdomain 的代表 |
| `gpio-mmio.c` | 不是驱动而是被 35 个驱动调用的库，代表「大多数 MMIO 控制器根本不自己写访问器」 |

⚠️ **重要：大量 GPIO 提供方不住在 `drivers/gpio/` 下。** Qualcomm（`drivers/pinctrl/qcom/pinctrl-msm.c`）、Renesas（`drivers/pinctrl/renesas/pinctrl-rzg2l.c`）等把 pinctrl 和 gpio 做在一个驱动里。做任何「全树唯一」的判断都必须 grep 整棵树，不能只 grep `drivers/gpio/`。

### §0.2 头文件

**消费者要包含的**：

| 头文件 | 内容 | 什么时候包含 |
|---|---|---|
| `include/linux/gpio/consumer.h` | 全部 `gpiod_*` / `devm_gpiod_*` API、`enum gpiod_flags`、`struct gpio_descs`、`GPIOD_FLAGS_BIT_*` | 写消费者驱动，**只需要这一个** |

**提供方要包含的**：

| 头文件 | 内容 |
|---|---|
| `include/linux/gpio/driver.h` | `struct gpio_chip`、`struct gpio_irq_chip`、`gpiochip_add_data()` 系列、irqchip 辅助、pinctrl 桥接、`for_each_gpiochip_node` |

**板级代码（无 DT 的平台）要包含的**：

| 头文件 | 内容 |
|---|---|
| `include/linux/gpio/machine.h` | `struct gpiod_lookup`、`struct gpiod_lookup_table`、`struct gpiod_hog`、`GPIO_LOOKUP*` 宏、9 个 `GPIO_*` lookup 标志 |

**其余**：

| 头文件 | 内容 | 备注 |
|---|---|---|
| `include/uapi/linux/gpio.h` | 用户态 ABI：v1/v2 结构体、13 个 `GPIO_V2_LINE_FLAG_*`、全部 ioctl 号 | 唯一给用户态看的头文件 |
| `include/linux/of_gpio.h` | `of_get_named_gpio*()`、`enum of_gpio_flags`、`struct of_mm_gpio_chip` | 过渡期接口；文件里自带一条 `#include <linux/gpio.h> /* FIXME: Shouldn't be here */` |
| `include/linux/gpio.h` | **已废弃**的整数 API 门面 | 新代码不要包含 |
| `include/asm-generic/gpio.h` | 整数 API 到 `gpiod_*_raw_*` 的映射实现 | 由 `linux/gpio.h` 间接包含 |
| `include/trace/events/gpio.h` | 两个 tracepoint：`gpio_direction`、`gpio_value` | 全部 tracepoint 就这两个，无 `TP_CONDITION` |
| `include/linux/gpio/regmap.h` | `struct gpio_regmap_config` + `gpio_regmap_register()` | 用 regmap 写控制器时用 |
| `include/linux/gpio/gpio-reg.h`、`include/linux/gpio/aspeed.h` | 单驱动私有接口 | 不是通用 API |
| `drivers/gpio/gpiolib.h` | **内部**，消费者和提供方都不该包含 | `struct gpio_desc` 定义在这里，所以描述符对外是不透明的 |

### §0.3 Kconfig

| 选项 | 类型 | 默认 | 作用与注意点 |
|---|---|---|---|
| `GPIOLIB` | menuconfig bool | 由平台 select | 总开关。关掉后 `consumer.h` 里全部 API 退化成返回 `-ENOSYS` 的 `static inline` |
| `GPIOLIB_FASTPATH_LIMIT` | int，范围 32–512 | **512** | 数组快速路径在栈上开位图的上限。帮助文字明写「setting an incorrect value could cause stack corruption」 |
| `OF_GPIO` | def_bool y，依赖 `OF && HAS_IOMEM` | y | 编入 `gpiolib-of.c`。**不是用户可见选项**，有 OF 就有它 |
| `GPIOLIB_IRQCHIP` | bool，`select IRQ_DOMAIN` | 被驱动 select | 编入 irqchip 集成代码。**不是用户可见选项**，由需要它的驱动 select |
| `GPIO_ACPI` | def_bool y | y | 本文档集不涉及 |
| `DEBUG_GPIO` | bool，依赖 `DEBUG_KERNEL` | n | 加 `-DDEBUG` 到整个 `drivers/gpio/`，打开所有 `pr_debug` / `dev_dbg`。帮助文字提醒「can make bitbanged serial protocols slower」 |
| `GPIO_SYSFS` | bool，`if EXPERT`，`select GPIO_CDEV` | — | `/sys/class/gpio`。帮助文字明写「This ABI is deprecated」；那条 select 带注释 `# We need to encourage the new ABI` |
| `GPIO_CDEV` | bool | y | `/dev/gpiochipN` |
| `GPIO_CDEV_V1` | bool，依赖 `GPIO_CDEV` | y | v1 ioctl。帮助文字明写「This ABI version is deprecated」 |
| `GPIO_GENERIC` | bool | 被驱动 select | 编入 `gpio-mmio.c`（Makefile 里映射为 `gpio-generic-$(CONFIG_GPIO_GENERIC) += gpio-mmio.o`，**目标名和文件名不一致**） |
| `GPIO_REGMAP` | bool | 被驱动 select | 编入 `gpio-regmap.c` |
| `ARCH_HAVE_CUSTOM_GPIO_H` | bool | 架构 select | 让架构提供自己的 `asm/gpio.h`。帮助文字：「New uses of this are strongly discouraged」。**arm64 不 select 它** |

### §0.4 官方文档

| 路径 | 内容 | 与代码的偏差 |
|---|---|---|
| `Documentation/driver-api/gpio/index.rst` | 索引 | — |
| `Documentation/driver-api/gpio/intro.rst` | 概念、编号 vs 描述符、约定 | — |
| `Documentation/driver-api/gpio/consumer.rst` | 消费者 API 手册 | 漏了 `gpiod_enable/disable_hw_timestamp_ns`、`devm_gpiod_unhinge`、`gpiod_set_consumer_name`、整个 `fwnode_*` 和 `*_from_of_node` 家族 |
| `Documentation/driver-api/gpio/driver.rst` | 提供方 API 手册、irqchip 三种级联模式 | 两处签名与代码不符（`gpiochip_request_own_desc` 参数个数、`irq_mask_mask_parent()` 这个不存在的函数）——详见 `99-evidence.md` §6 |
| `Documentation/driver-api/gpio/board.rst` | 板级查找表 | 只列了 6 个 lookup 标志，实际有 9 个 + 一个默认值宏 |
| `Documentation/driver-api/gpio/legacy.rst` | 整数 API（**已废弃**） | 多处过时：把 `irq_to_gpio()` 当可用（它全树返回 `-EINVAL`）、丢了 `gpio_request_array()` 的 `const`、把 `gpio_get_value` 说成宏（实际是 `static inline`） |
| `Documentation/driver-api/gpio/using-gpio.rst` | 面向「用 GPIO 的人」的入门 | — |
| `Documentation/driver-api/gpio/drivers-on-gpio.rst` | 建立在 GPIO 之上的那些子系统清单 | — |
| `Documentation/driver-api/gpio/bt8xxgpio.rst` | 单驱动说明 | — |
| `Documentation/admin-guide/gpio/*.rst` | 用户态视角：`sysfs.rst`（废弃 ABI）、`gpio-mockup.rst`、`gpio-aggregator.rst` 等 | `sysfs.rst` 与 `legacy.rst` 的 sysfs 章节重复且函数名不一致 |
| `Documentation/ABI/obsolete/sysfs-gpio` | 明确标记 obsolete 的 sysfs ABI | — |
| `Documentation/devicetree/bindings/gpio/` | 100 个绑定文件 | 见 `06-devicetree.md` |

**三等证据风险提示**：`Documentation/` 里的内容偶尔落后于代码。本文档集凡遇冲突**以代码为准**，冲突点全部登记在 `99-evidence.md` §6（共 8 条）。

---

## §1 消费者 API：获取与释放描述符

> 头文件：`include/linux/gpio/consumer.h`
> 实现：`drivers/gpio/gpiolib.c`（核心）、`drivers/gpio/gpiolib-devres.c`（devm 变体）、`drivers/gpio/gpiolib-of.c`（`*_from_of_node`）

### §1.1 主路径

| API | 说明 | 备注 |
|---|---|---|
| `gpiod_get(dev, con_id, flags)` | 按 con_id 拿这个设备的第 0 根 GPIO | 内部就是 `gpiod_get_index(dev, con_id, 0, flags)`。失败返回 `ERR_PTR`，**必须用 `IS_ERR()` 判**；`__must_check` |
| `gpiod_get_index(dev, con_id, idx, flags)` | 拿第 idx 根 | 同一个 con_id 对应多根线时用（DT 里 `foo-gpios = <&a 1 0>, <&a 2 0>;`）。这是所有获取路径的**汇聚点**：DT → ACPI → 板级查找表三级 fallback 都在这个函数里 |
| `gpiod_get_optional(dev, con_id, flags)` | 同 `gpiod_get()`，但「没配」不算错 | 没找到时返回 **NULL 而不是 `ERR_PTR(-ENOENT)`**。后续所有 `gpiod_*` 对 NULL 描述符是安全的空操作，所以可选线不需要处处判空 |
| `gpiod_get_index_optional(dev, con_id, idx, flags)` | optional + 指定下标 | 同上 |
| `gpiod_get_array(dev, con_id, flags)` | 一次拿这个 con_id 下的**全部**线 | 返回 `struct gpio_descs *`（含 `ndescs`、`desc[]`、`info`）。核心会尝试建立快速路径元信息（见 `04-extensions.md` §4） |
| `gpiod_get_array_optional(dev, con_id, flags)` | 数组版 optional | 没配返回 NULL |
| `gpiod_count(dev, con_id)` | 数一数这个 con_id 有几根线，不请求 | 返回负错误码表示没有。DT 路径下**空 specifier 也计数**（`of_gpio_named_count()` 的 kerneldoc 里有一个 4-GPIO 的例子说明这点） |
| `gpiod_put(desc)` | 释放一根 | 对 NULL 安全 |
| `gpiod_put_array(descs)` | 释放一个数组 | 对 NULL 安全 |

`flags` 取值（`enum gpiod_flags`，`consumer.h:46-54`；`GPIOD_FLAGS_BIT_NONEXCLUSIVE` 不在这个枚举里，它是独立的 `BIT(4)` 宏，`consumer.h:32`，可以和上面任一枚举值按位或）：

| 值 | 含义 |
|---|---|
| `GPIOD_ASIS` | 不碰方向，硬件当前是什么就是什么 |
| `GPIOD_IN` | 立刻设为输入 |
| `GPIOD_OUT_LOW` / `GPIOD_OUT_HIGH` | 立刻设为输出，初值 0 / 1（**逻辑值**，会按 active-low 翻译） |
| `GPIOD_OUT_LOW_OPEN_DRAIN` / `GPIOD_OUT_HIGH_OPEN_DRAIN` | 输出 + 强制 open-drain 语义 |
| `GPIOD_FLAGS_BIT_NONEXCLUSIVE` | 允许和别人共享这根线（下面单独讲） |

**`GPIOD_ASIS` 是个陷阱**：拿到的描述符方向未知，`gpiod_set_value()` 之前必须自己调 `gpiod_direction_output()`，否则等于往一根输入线写值。

**`GPIOD_FLAGS_BIT_NONEXCLUSIVE` 的真实语义**：正常情况下第二个人来 request 同一根线会拿到 `-EBUSY`；带这个标志时，`gpiod_get_index()` 检查到 `-EBUSY` 后**直接把已有的描述符返回给你**，并且**跳过后面所有的 flags 配置**——也就是说第二个调用者传的方向/初值/bias **全部被忽略**。源码里对此的自评是 "It is a bit of a hack"，并注明这是为了支持共享 enable 线的 fixed regulator（`gpiolib.c:4126-4141`）。

### §1.2 devm 变体

> 实现：`drivers/gpio/gpiolib-devres.c`

| API | 说明 | 备注 |
|---|---|---|
| `devm_gpiod_get(dev, con_id, flags)` | `gpiod_get()` + 自动释放 | 挂在 `dev` 的 devres 上，probe 失败或 remove 时自动 put |
| `devm_gpiod_get_index(dev, con_id, idx, flags)` | 带下标 | |
| `devm_gpiod_get_optional(dev, con_id, flags)` | optional | |
| `devm_gpiod_get_index_optional(dev, con_id, idx, flags)` | optional + 下标 | |
| `devm_gpiod_get_array(dev, con_id, flags)` | 数组 | |
| `devm_gpiod_get_array_optional(dev, con_id, flags)` | 数组 optional | |
| `devm_gpiod_put(dev, desc)` | 提前手动释放 | 正常流程不需要调，只在 probe 里提前放手时用 |
| `devm_gpiod_put_array(dev, descs)` | 数组版 | |
| `devm_gpiod_unhinge(dev, desc)` | **只解除 devres 托管，不释放描述符** | 用于把一根线的所有权移交给别人（之后必须有人调 `gpiod_put()`）。对 NONEXCLUSIVE 描述符容忍 `devres_destroy()` 返回 `-ENOENT`，其他情况 `WARN_ON` |

**NONEXCLUSIVE 在 devm 层有额外去重**：`devm_gpiod_get_index()` 和 `devm_gpiod_get_from_of_node()` 在带 NONEXCLUSIVE 标志时会先 `devres_find()` 找同一个 desc 的已有 devres 记录，找到就直接返回，**不再登记第二条 devres**——否则同一根线会被 release 两次。

**一个不对称**：`devm_gpiod_get_index()` / `devm_gpiod_get_from_of_node()` 是「先 get，再 alloc devres」；`devm_fwnode_gpiod_get_index()` / `devm_gpiod_get_array()` 是「先 alloc devres，再 get，出错 `devres_free`」。两种顺序都正确，但读代码时容易以为自己看错了。

### §1.3 fwnode / of_node 直取（不经过 `dev` 的 con_id 查找）

这一组用于「线不属于 `dev` 自己，而属于 `dev` 的某个子节点」的场景——典型是 `gpio-leds`、`gpio-keys` 这类为每个子节点建一个逻辑对象的驱动。

| API | 说明 | 备注 |
|---|---|---|
| `fwnode_gpiod_get_index(fwnode, con_id, index, flags, label)` | 从任意 fwnode 上取第 index 根 | 需要自己传 `label`（它不像 `gpiod_get()` 那样能退化用 `dev_name()`） |
| `devm_fwnode_gpiod_get_index(dev, fwnode, con_id, index, flags, label)` | 上一个的 devm 版 | devres 记在 `dev` 上，而线来自 `fwnode`——这正是子节点场景需要的 |
| `devm_fwnode_gpiod_get(dev, fwnode, con_id, flags, label)` | index 固定为 0 的便捷包装 | `static inline`，定义在 `consumer.h:574-583`，**在 `#ifdef CONFIG_GPIOLIB` 块之外**，所以两种配置下都是同一份代码 |
| `devm_fwnode_get_index_gpiod_from_child(dev, con_id, index, child, flags, label)` | **旧名**，同 `devm_fwnode_gpiod_get_index()` | `static inline` 转发，保留兼容 |
| `devm_fwnode_get_gpiod_from_child(dev, con_id, child, flags, label)` | **旧名**，同 `devm_fwnode_gpiod_get()` | 同上 |
| `gpiod_get_from_of_node(node, propname, index, dflags, label)` | 直接给 DT 属性名取线 | 需要 `CONFIG_OF_GPIO`；`propname` 是**完整属性名**（如 `"enable-gpios"`），不是 con_id |
| `devm_gpiod_get_from_of_node(dev, node, propname, index, dflags, label)` | 上一个的 devm 版 | 同上 |

**con_id 和 propname 的区别是这里最容易搞错的一点**：`gpiod_get(dev, "reset", ...)` 会自动去试 `reset-gpios`、`reset-gpio`；而 `gpiod_get_from_of_node(node, "reset", ...)` 会去找一个字面叫 `reset` 的属性，**找不到就是找不到**，不会加后缀。

### §1.4 编号 ↔ 描述符互转（尽量别用）

| API | 说明 | 备注 |
|---|---|---|
| `gpio_to_desc(gpio)` | 全局 GPIO 号 → 描述符 | 全局编号空间在 `gpiolib.h` 的 kerneldoc 里被明确标为 "DEPRECATED"。只在需要和旧代码/sysfs 对接时用 |
| `desc_to_gpio(desc)` | 描述符 → 全局 GPIO 号 | 同上；`gpiolib-sysfs.c` 用它来给 sysfs 节点命名 |
| `gpiochip_get_desc(gc, hwnum)` | 芯片内偏移 → 描述符 | 这个**不**废弃，是提供方和内核内部的正常用法。范围越界返回 `ERR_PTR(-EINVAL)` |
| `gpiod_to_chip(desc)` | 描述符 → 它所属的 `gpio_chip` | `static inline`（`driver.h:787`）。chip 已被移除时返回 NULL |

---

## §2 消费者 API：方向与配置

> 头文件：`include/linux/gpio/consumer.h`；实现：`drivers/gpio/gpiolib.c`

| API | 说明 | 备注 |
|---|---|---|
| `gpiod_get_direction(desc)` | 查方向，0 = 输入，1 = 输出 | **不总是问硬件**：如果这根线是 open-drain/open-source 且当前标记为输出，直接返回 0（输入），因为 OD 模拟本身就是靠切成输入实现的。没有 `gc->get_direction` 回调时返回缓存的 `FLAG_IS_OUT` |
| `gpiod_direction_input(desc)` | 设为输入 | 会顺带下发 bias 配置。open-source 线上调它是合法的 |
| `gpiod_direction_output(desc, value)` | 设为输出并给初值（**逻辑值**） | `FLAG_ACTIVE_LOW` 时会先取反 value 再落地；OD/OS 线走模拟路径 |
| `gpiod_direction_output_raw(desc, value)` | 设为输出并给初值（**裸值**） | 不做极性翻译。sysfs 的 `direction` 写入和整数 API 的 `gpio_direction_output()` 都走这里 |
| `gpiod_set_config(desc, config)` | 下发一个打包好的 pinconf 值 | `config` 用 `PIN_CONF_PACKED()` 打包（`include/linux/pinctrl/pinconf-generic.h`）。转发到 `gc->set_config`；没有回调返回 `-ENOTSUPP` |
| `gpiod_set_debounce(desc, debounce)` | 请求硬件去抖，单位微秒 | 这是**硬件**去抖。返回 `-ENOTSUPP` 说明控制器不支持——此时字符设备路径会退化成软件去抖，但**内核消费者不会**，得自己处理 |
| `gpiod_set_transitory(desc, transitory)` | 声明这根线在睡眠/复位后会不会保值 | `transitory = true` 表示「会丢值」，转成 `PIN_CONFIG_PERSIST_STATE`。**取值语义是反的**：persist = !transitory |
| `gpiod_is_active_low(desc)` | 查这根线是不是 active-low | 返回 `FLAG_ACTIVE_LOW` 的状态 |
| `gpiod_toggle_active_low(desc)` | 翻转 active-low 标志 | 用于极性只能在运行时确定的场景（如 SPI 的 `cs-gpios` + `spi-cs-high`）。**不动硬件**，只改标志 |
| `gpiod_cansleep(desc)` | 查访问这根线是否可能睡眠 | 返回 `gc->can_sleep`。I2C/SPI 上的扩展器为 true |
| `gpiod_set_consumer_name(desc, name)` | 改 `desc->label`（debugfs / lineinfo 里显示的消费者名） | 传 NULL 会把 label 设成 NULL。`consumer.rst` 里没写这个 API |
| `gpiod_enable_hw_timestamp_ns(desc, flags)` | 打开硬件时间戳引擎（HTE） | 需要 `CONFIG_HTE` + `gc->en_hw_timestamp`；本树只有 Tegra 实现。`consumer.rst` 里没写 |
| `gpiod_disable_hw_timestamp_ns(desc, flags)` | 关闭 HTE | 同上 |

### §2.1 「方向 vs 值」的三个坑

1. **`GPIOD_ASIS` + `gpiod_set_value()` = 什么都不会发生**（线还是输入）。核心不会替你纠正，也不会警告。
2. **`gpiod_direction_output()` 的 value 是逻辑值，`gpiod_direction_output_raw()` 的是裸值**——两个函数名只差 `_raw`，语义相反，混用的后果是极性反了却没有任何报错。
3. **open-drain 线上「输出 1」不是写数据寄存器，是切成输入**。所以在 OD 线上先 `gpiod_direction_output(desc, 1)` 再 `gpiod_get_direction(desc)`，会看到 0（输入）——这不是 bug，见上表第一行的 。

---

## §3 消费者 API：读写单根线

> 头文件：`include/linux/gpio/consumer.h`；实现：`drivers/gpio/gpiolib.c`

这一组是**四维笛卡尔积**：{读, 写} × {逻辑, 裸} × {原子上下文, 可睡眠}。名字规则记住就不会错：

```
gpiod_ [get|set] _ [raw_] value [_cansleep]
        └读/写      └不做极性翻译   └允许睡眠
```

| API | 说明 | 备注 |
|---|---|---|
| `gpiod_get_value(desc)` | 读逻辑值 | 返回 0 / 1，或负错误码。`gc->can_sleep` 为真时会 `WARN_ON` |
| `gpiod_get_raw_value(desc)` | 读裸值 | 同上，不做极性翻译 |
| `gpiod_set_value(desc, value)` | 写逻辑值 | **返回 void，写失败你不会知道**。按 ACTIVE_LOW 翻译，按 OD/OS 走模拟 |
| `gpiod_set_raw_value(desc, value)` | 写裸值 | 返回 void；不翻译极性，但**仍然走 OD/OS 模拟** |
| `gpiod_get_value_cansleep(desc)` | 读逻辑值，允许睡眠 | 无 `WARN_ON`；I2C/SPI 扩展器上的线**必须**用这个 |
| `gpiod_get_raw_value_cansleep(desc)` | 读裸值，允许睡眠 | |
| `gpiod_set_value_cansleep(desc, value)` | 写逻辑值，允许睡眠 | 返回 void |
| `gpiod_set_raw_value_cansleep(desc, value)` | 写裸值，允许睡眠 | 返回 void |

**「可以睡眠」不等于「必须能睡眠」**：`*_cansleep()` 在一个不会睡眠的 MMIO 控制器上完全正常，只是省掉了 `WARN_ON`。反过来在扩展器上用非 cansleep 版本，会在 dmesg 里刷 `WARN_ON`——**但值还是会被写下去**，所以这个 WARN 很容易被忽略。

**返回 void 的四个 setter 是本子系统一个真实的设计缺陷**：I2C 扩展器上一次写失败（总线 NAK）在 API 层面完全静默。核心内部的 `gpiod_set_raw_value_commit()` 也不检查 `gc->set` 的结果——因为 `.set` 回调本身就是 `void`（`driver.h:437-438`）。要检测这类失败只能靠 tracepoint 或者控制器驱动自己打日志。

---

## §4 消费者 API：数组批量读写

> 头文件：`include/linux/gpio/consumer.h`；实现：`drivers/gpio/gpiolib.c`

值用**位图**传递：第 i 位对应 `desc_array[i]`。

| API | 说明 | 备注 |
|---|---|---|
| `gpiod_get_array_value(array_size, desc_array, array_info, value_bitmap)` | 批量读逻辑值 | 返回 0 或负错误码 |
| `gpiod_get_raw_array_value(array_size, desc_array, array_info, value_bitmap)` | 批量读裸值 | |
| `gpiod_set_array_value(array_size, desc_array, array_info, value_bitmap)` | 批量写逻辑值 | **这四个 setter 返回 int，不是 void**——和单线版本相反 |
| `gpiod_set_raw_array_value(array_size, desc_array, array_info, value_bitmap)` | 批量写裸值 | |
| `gpiod_get_array_value_cansleep(...)` / `gpiod_get_raw_array_value_cansleep(...)` | 可睡眠版读 | |
| `gpiod_set_array_value_cansleep(...)` / `gpiod_set_raw_array_value_cansleep(...)` | 可睡眠版写 | |

三个参数怎么来：

```c
struct gpio_descs *descs = devm_gpiod_get_array(dev, "data", GPIOD_OUT_LOW);
DECLARE_BITMAP(values, MAX_LINES);

/* 三个参数分别是： */
gpiod_set_array_value(descs->ndescs,   /* array_size  */
                      descs->desc,     /* desc_array  */
                      descs->info,     /* array_info  ← 快速路径元信息 */
                      values);         /* value_bitmap*/
```

`array_info` 传 **NULL 也合法**——只是放弃快速路径，退化成逐根循环。想自己拼一个数组（不是从 `gpiod_get_array()` 来的）时就传 NULL。

**快速路径的命中条件很严**（`04-extensions.md` §4 有完整判定）：所有成员必须属于同一个 chip、成员 0 的 hwnum 必须是 0、每个成员在数组里的下标必须等于它的 hwnum。任何一条不满足就整体退化。**open-drain / open-source 的线永远走不了快速*写*路径**（因为「写 1」实际是改方向）。

**栈上位图有上限**：`CONFIG_GPIOLIB_FASTPATH_LIMIT`（默认 512）。超过这个线数时核心走堆分配路径。

---

## §5 提供方 API：注册与注销

> 头文件：`include/linux/gpio/driver.h`；实现：`drivers/gpio/gpiolib.c`

| API | 说明 | 备注 |
|---|---|---|
| `devm_gpiochip_add_data(dev, gc, data)` | **推荐入口**：注册一个 GPIO 控制器，`data` 是驱动私有指针 | 宏，展开到 `devm_gpiochip_add_data_with_key()`。`dev` 死时自动 `gpiochip_remove()`。`gc` 必须活到注销之后（核心只存指针，不拷贝） |
| `gpiochip_add_data(gc, data)` | 手动版，注销要自己调 `gpiochip_remove()` | 宏。开了 `CONFIG_LOCKDEP` 时它会在**调用点**声明两个静态 lock_key（`driver.h:599-604`），这是它必须是宏而不能是函数的原因 |
| `gpiochip_add(gc)` | `gpiochip_add_data(gc, NULL)` | `static inline`，等价于不带私有数据 |
| `gpiochip_add_data_with_key(gc, data, lock_key, request_key)` | 真正的实现函数 | 一般不直接调，除非你自己管 lockdep key |
| `devm_gpiochip_add_data_with_key(dev, gc, data, lock_key, request_key)` | 上一个的 devm 版实现 | 同上 |
| `gpiochip_remove(gc)` | 注销 | **返回 void**。用了 devm 版注册就不要再调它 |
| `gpiochip_get_data(gc)` | 取回注册时传的 `data` | 回调里从 `gc` 拿回驱动私有结构的标准方式（替代 `container_of`） |
| `gpiochip_find(data, match)` | 遍历全局 `gpio_devices` 找一个 chip | `match(gc, data)` 返回真即命中。**会对找到的 chip 的 device 加引用吗？不会**——它只在 `gpio_lock` 下遍历并返回 `gc` 指针 |
| `gpiochip_line_is_valid(gc, offset)` | 这个 offset 是不是有效线 | 查 `gc->valid_mask`；没有 mask 时一律返回 true |

`struct gpio_chip` 必填/选填速查（完整字段见 `02-datastructures.md` §2）：

| 字段 | 必填？ | 说明 |
|---|---|---|
| `label` | 是 | 出现在 debugfs 和 `/dev` 的 chip 名里 |
| `parent` | 强烈建议 | 决定 sysfs 位置、of_node 继承、owner 推导。不填的话 `gdev->owner` 会退化成 `THIS_MODULE` |
| `ngpio` | 是 | 线数 |
| `base` | 建议填 **-1** | -1 = 让核心动态分配全局编号。手填是「DEPRECATED」（`gpiolib.c` 的 TODO 注释点名了这一点） |
| `get` / `set` | 至少一个 | 纯输出芯片可以只有 `set` |
| `direction_input` / `direction_output` | 可双向时必填 | |
| `get_direction` | 建议 | 不填的话核心只能返回缓存值，`gpiod_get_direction()` 就不反映真实硬件 |
| `request` / `free` | 有 pinctrl 时必填 | 填 `gpiochip_generic_request` / `gpiochip_generic_free` 即可 |
| `set_config` | 可选 | 不填时 `gpiod_set_debounce()` / bias 配置返回 `-ENOTSUPP` |
| `can_sleep` | 慢总线上必填 true | I2C/SPI 扩展器 |
| `get_multiple` / `set_multiple` | 可选 | 填了才有数组快速路径的收益 |
| `init_valid_mask` | 可选 | 用于「某些 offset 物理上不存在」的芯片 |
| `add_pin_ranges` | 可选 | 在 DT `gpio-ranges` 之外手工加 pin 映射的钩子 |
| `dbg_show` | 可选 | 往 `debugfs/gpio` 里加自定义输出 |
| `en_hw_timestamp` / `dis_hw_timestamp` | 可选 | HTE 支持 |
| `irq`（`struct gpio_irq_chip`） | 有中断能力时填 | 见 §6 |

## §6 提供方 API：irqchip 集成

> 头文件：`include/linux/gpio/driver.h`；实现：`drivers/gpio/gpiolib.c`（`CONFIG_GPIOLIB_IRQCHIP`）

**正常路径下你一个都不用调**——填好 `gc->irq` 里的字段，`gpiochip_add_data()` 会替你建 irqdomain。下面这些是辅助函数和特殊情况。

### §6.1 建 domain

| API | 说明 | 备注 |
|---|---|---|
| `gpio_irq_chip_set_chip(girq, chip)` | 把一个 `const struct irq_chip *` 装进 `girq` | `static inline`（`driver.h:644`）。**新驱动应该用这个**，配合 `IRQCHIP_IMMUTABLE`；老写法直接赋 `girq->chip` 会被核心修改结构体内容 |
| `gpiochip_irqchip_add_domain(gc, domain)` | 把一个**外部已建好**的 irqdomain 挂给 gpiochip | 全树只有一个调用者：`drivers/gpio/gpio-regmap.c:279`。用于 domain 由别人（比如 regmap-irq）建的场景 |

### §6.2 层级模型的父 fwspec 填充器

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_populate_parent_fwspec_twocell(gc, parent_arg, parent_hwirq, parent_type)` | 填一个 2-cell 的父 irq specifier | 给 `girq->populate_parent_alloc_arg` 用。层级模型且父控制器是 2-cell（大多数）时用这个 |
| `gpiochip_populate_parent_fwspec_fourcell(gc, parent_arg, parent_hwirq, parent_type)` | 4-cell 版本 | GIC 的 `#interrupt-cells = <3>` 场景需要自己写，这个是给 4-cell 父控制器用的 |

`driver.h:105-115` 的 kerneldoc 说明了默认值：不填 `populate_parent_alloc_arg` 时核心用 twocell 版本。

### §6.3 domain ops 的默认实现（自己建 domain 时用）

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_irq_map(d, irq, hwirq)` | 默认 `.map` | 只在你自己 `irq_domain_create_*()` 时才需要引用 |
| `gpiochip_irq_unmap(d, irq)` | 默认 `.unmap` | |
| `gpiochip_irq_domain_activate(domain, data, reserve)` | 默认 `.activate` | 内部调 `gpiochip_lock_as_irq()` |
| `gpiochip_irq_domain_deactivate(domain, data)` | 默认 `.deactivate` | |
| `gpiochip_irqchip_irq_valid(gc, offset)` | 这个 offset 能不能当 IRQ | 查 `gc->irq.valid_mask` |

### §6.4 IMMUTABLE 契约必须成对使用的四个

这四个是 `IRQCHIP_IMMUTABLE` 写法的核心，**漏掉任何一个都会导致 `FLAG_USED_AS_IRQ` / `FLAG_IRQ_IS_ENABLED` 状态不一致**：

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_enable_irq(gc, offset)` | 在你的 `.irq_unmask` 里调 | 置 `FLAG_IRQ_IS_ENABLED`，并顺带保证这根线被 lock 成 IRQ |
| `gpiochip_disable_irq(gc, offset)` | 在你的 `.irq_mask` 里调 | 清 `FLAG_IRQ_IS_ENABLED` |
| `gpiochip_irq_reqres(data)` | 填到 `.irq_request_resources` | **不要手写**，用 `GPIOCHIP_IRQ_RESOURCE_HELPERS` 宏一次填好两个 |
| `gpiochip_irq_relres(data)` | 填到 `.irq_release_resources` | 同上 |

```c
/* driver.h:640-642，全树唯一定义处 */
#define GPIOCHIP_IRQ_RESOURCE_HELPERS					\
		.irq_request_resources  = gpiochip_irq_reqres,		\
		.irq_release_resources  = gpiochip_irq_relres
```

标准写法（`gpio-pl061.c:296-305`）：

```c
static const struct irq_chip pl061_irq_chip = {
	.irq_ack	= pl061_irq_ack,
	.irq_mask	= pl061_irq_mask,      /* 里面调 gpiochip_disable_irq */
	.irq_unmask	= pl061_irq_unmask,    /* 里面调 gpiochip_enable_irq  */
	.irq_set_type	= pl061_irq_type,
	.irq_set_wake	= pl061_irq_set_wake,
	.irq_print_chip	= pl061_irq_print_chip,
	.flags		= IRQCHIP_IMMUTABLE,
	GPIOCHIP_IRQ_RESOURCE_HELPERS,
};
```

### §6.5 手动 lock/unlock（不用 gpiolib irqchip 集成时）

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_lock_as_irq(gc, offset)` | 声明「这根线现在归 IRQ 用」 | 置 `FLAG_USED_AS_IRQ`。**线还没被 request 时会先自动 request 它**。已是输出时返回 `-EIO` |
| `gpiochip_unlock_as_irq(gc, offset)` | 解除 | 返回 void |
| `gpiochip_reqres_irq(gc, offset)` | `gpiochip_lock_as_irq()` + 模块引用 | `gpiochip_irq_reqres()` 内部用的就是它 |
| `gpiochip_relres_irq(gc, offset)` | 对应的释放 | |
| `gpiochip_line_is_irq(gc, offset)` | 查 `FLAG_USED_AS_IRQ` | 用于「这根线被占了，别让人当普通 GPIO 用」的检查 |

`gpiolib-sysfs.c:178-186` 里有一段值得注意的代码：sysfs 的 `edge` 属性写入路径**自己**调 `gpiochip_lock_as_irq()`，并带一条 FIXME 说明这本该由 `irq_request_resources` 回调做，「but a few drivers currently fail to do so」。

## §7 提供方 API：pinctrl 桥接与线状态查询

> 头文件：`include/linux/gpio/driver.h`；实现：`drivers/gpio/gpiolib.c`

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_generic_request(gc, offset)` | 通用 `.request`：向 pinctrl 申请这个 pin | 直接赋给 `gc->request` 即可。没有 pin range 时是空操作 |
| `gpiochip_generic_free(gc, offset)` | 通用 `.free` | 配对使用 |
| `gpiochip_generic_config(gc, offset, config)` | 通用 `.set_config`：把 pinconf 转给 pinctrl | 适用于电气参数由 pinctrl 管的控制器 |
| `gpiochip_add_pin_range(gc, pinctl_name, gpio_offset, pin_offset, npins)` | 手工加一段连续的 GPIO→pin 映射 | 一般由 DT `gpio-ranges` 自动做；`add_pin_ranges` 回调里手工加时用 |
| `gpiochip_add_pingroup_range(gc, pctldev, gpio_offset, pin_group)` | 按 pin **group** 名加映射 | 用于映射不连续的场景 |
| `gpiochip_remove_pin_ranges(gc)` | 清掉所有 pin range | `gpiochip_remove()` 里会自动调 |

线状态查询（给 pinctrl / cdev / 其他子系统看 GPIO 内部状态）：

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_is_requested(gc, offset)` | 返回消费者的 label 字符串，未被占用返回 NULL | **不是返回 bool**，名字有误导性 |
| `gpiochip_line_is_open_drain(gc, offset)` | 查 `FLAG_OPEN_DRAIN` | |
| `gpiochip_line_is_open_source(gc, offset)` | 查 `FLAG_OPEN_SOURCE` | |
| `gpiochip_line_is_persistent(gc, offset)` | 查 `FLAG_TRANSITORY` 的**反** | 名字和标志是反的，别看错 |

提供方自用描述符（控制器驱动自己想占一根自己的线，比如做复位）：

| API | 说明 | 备注 |
|---|---|---|
| `gpiochip_request_own_desc(gc, hwnum, label, lflags, dflags)` | 芯片自己申请自己的一根线 | **五个参数**。`Documentation/driver-api/gpio/driver.rst:766-769` 写的是四个参数，那是过时的（见 `99-evidence.md` §6 第 3 条） |
| `gpiochip_free_own_desc(desc)` | 配对释放 | |

## §8 板级 API：无 DT 平台的查找表与 hog

> 头文件：`include/linux/gpio/machine.h`；实现：`drivers/gpio/gpiolib.c`

arm64 上几乎总是有 DT，所以这一组主要出现在**测试模块**（`gpio-mockup`、`gpio-aggregator`）和少数 MFD 驱动里。

| API | 说明 | 备注 |
|---|---|---|
| `gpiod_add_lookup_table(table)` | 注册一张查找表 | 必须在消费者 probe **之前**注册。内部就是 `gpiod_add_lookup_tables(&table, 1)` |
| `gpiod_add_lookup_tables(tables, n)` | 一次注册 n 张 | **声明在 `machine.h:113` 但没有 `EXPORT_SYMBOL`**——只有编进内核（非模块）的代码能用它 |
| `gpiod_remove_lookup_table(table)` | 注销 | |
| `gpiod_add_hogs(hogs)` | 注册一批 hog（开机就占住并设成固定状态的线） | 数组以全零项结尾 |
| `gpiod_remove_hogs(hogs)` | 注销 | `board.rst` 里没提这个 |

构造宏：

| 宏 | 说明 | 备注 |
|---|---|---|
| `GPIO_LOOKUP(_key, _chip_hwnum, _con_id, _flags)` | 一条查找项 | 内部就是 `GPIO_LOOKUP_IDX(..., 0, ...)` |
| `GPIO_LOOKUP_IDX(_key, _chip_hwnum, _con_id, _idx, _flags)` | 带下标的查找项 | 同一个 con_id 多根线时每根用不同 idx |
| `GPIO_LOOKUP_SINGLE(_name, _dev_id, _key, _chip_hwnum, _con_id, _flags)` | **一次性定义整张只有一项的表** | `machine.h:69-77`；它会 `static struct gpiod_lookup_table _name = {...}`，所以只能在文件作用域用。`board.rst` 里没提这个 |
| `GPIO_HOG(_chip_label, _chip_hwnum, _line_name, _lflags, _dflags)` | 一条 hog 项 | |

`_key` 有两种含义（`machine.h:24-29` 的 kerneldoc）：

- 一般情况：**chip 的 label**，配合 `chip_hwnum` 指定第几根线；
- `chip_hwnum == U16_MAX` 时：`_key` 变成**线名**（`gpio-line-names` 给的名字）。kerneldoc 明确警告「GPIO line names are not guaranteed to be globally unique, so this will use the first match found!」

`_flags` 取值（`enum gpio_lookup_flags`，`machine.h:8-21`，**9 个 + 1 个默认组合**）：

| 值 | 位 | 含义 |
|---|---|---|
| `GPIO_ACTIVE_HIGH` | (0 << 0) | 高有效（默认） |
| `GPIO_ACTIVE_LOW` | (1 << 0) | 低有效 |
| `GPIO_OPEN_DRAIN` | (1 << 1) | open-drain |
| `GPIO_OPEN_SOURCE` | (1 << 2) | open-source |
| `GPIO_PERSISTENT` | (0 << 3) | 睡眠/复位后保值（默认） |
| `GPIO_TRANSITORY` | (1 << 3) | 会丢值 |
| `GPIO_PULL_UP` | (1 << 4) | bias 上拉 |
| `GPIO_PULL_DOWN` | (1 << 5) | bias 下拉 |
| `GPIO_PULL_DISABLE` | (1 << 6) | bias 断开 |
| `GPIO_LOOKUP_FLAGS_DEFAULT` | — | `GPIO_ACTIVE_HIGH | GPIO_PERSISTENT` |

⚠️ `board.rst:126-133` 只列了前 6 个，缺三个 bias 标志，也没提 `GPIO_LOOKUP_FLAGS_DEFAULT`。**这套标志和 DT 里的 `GPIO_*`（`include/dt-bindings/gpio/gpio.h`）是两套不同的东西，虽然名字长得一样**——DT 那套要经 `of_gpio_flags` 转换才变成这套。

## §9 OF 专用 API

> 头文件：`include/linux/of_gpio.h`；实现：`drivers/gpio/gpiolib-of.c`（`CONFIG_OF_GPIO`）

这一组是「DT 时代之前和描述符时代之间」的过渡产物，**新代码应该用 `gpiod_get_from_of_node()` 或 `fwnode_gpiod_get_index()` 代替**。

| API | 说明 | 备注 |
|---|---|---|
| `of_get_named_gpio_flags(np, propname, index, flags)` | 从 DT 属性拿**整数** GPIO 号 + `enum of_gpio_flags` | 唯一被导出的一个。返回全局编号，还得再 `gpio_to_desc()` |
| `of_get_named_gpio(np, propname, index)` | 上一个的简化版（不要 flags） | `static inline` |
| `of_get_gpio_flags(np, index, flags)` | propname 固定为 `"gpios"` | `static inline` |
| `of_get_gpio(np, index)` | propname 固定为 `"gpios"`，不要 flags | `static inline` |
| `of_gpio_named_count(np, propname)` | 数属性里有几个 specifier | 就是 `of_count_phandle_with_args(np, propname, "#gpio-cells")`。它的 kerneldoc（`of_gpio.h:82-101`）明确说明**空 specifier 也计数**，并给了一个「写了 4 个但只有 2 个真 GPIO」的例子 |
| `of_gpio_count(np)` | propname 固定为 `"gpios"` | `static inline` |
| `of_mm_gpiochip_add_data(np, mm_gc, data)` | 「memory-mapped」便捷注册：一次搞定 `of_iomap` + label 生成 + `gpiochip_add_data` | 用 `kasprintf("%pOF")` 生成 label，`gc->base = -1`。适合极简 MMIO 控制器 |
| `of_mm_gpiochip_remove(mm_gc)` | 配对注销 | |
| `gpiod_get_from_of_node(node, propname, index, dflags, label)` | **推荐**：直接从 DT 属性拿描述符 | 声明在 `consumer.h:605-612`，实现在 `gpiolib-of.c`。见 §1.3 |

`enum of_gpio_flags`（`of_gpio.h:25-33`，7 个值）：`OF_GPIO_ACTIVE_LOW`(0x1)、`OF_GPIO_SINGLE_ENDED`(0x2)、`OF_GPIO_OPEN_DRAIN`(0x4)、`OF_GPIO_TRANSITORY`(0x8)、`OF_GPIO_PULL_UP`(0x10)、`OF_GPIO_PULL_DOWN`(0x20)、`OF_GPIO_PULL_DISABLE`(0x40)。**注意 open-drain 需要 `SINGLE_ENDED | OPEN_DRAIN` 两位一起**——单独一个 `OPEN_DRAIN` 位没有意义，这是 DT 绑定层的编码约定。

## §10 已废弃 API

### §10.1 整数 GPIO API

> 头文件：`include/linux/gpio.h`（+ `include/asm-generic/gpio.h`）；实现：`drivers/gpio/gpiolib-legacy.c`

**这一整组最重要的事实：它们全部走 raw 路径，完全不处理 active-low。**

| API | 说明 | 备注 |
|---|---|---|
| `gpio_is_valid(number)` | 编号是否落在合法范围 | `static inline` |
| `gpio_request(gpio, label)` | 申请一根线 | 不是 `gpio_request_one(gpio, 0, ...)` 的包装，是独立实现（直接调 `gpiod_request()`），但带同一个 `-EPROBE_DEFER` 兼容判断 |
| `gpio_request_one(gpio, flags, label)` | 申请 + 按 `GPIOF_*` 配置 | **描述符不存在但编号合法时返回 `-EPROBE_DEFER`**，源码注释：「Compatibility: assume unavailable "valid" GPIOs will appear later」 |
| `gpio_request_array(array, num)` | 批量申请 | `array` 是 `const struct gpio *`。`legacy.rst:311` 丢了 `const` |
| `gpio_free(gpio)` | 释放 | |
| `gpio_free_array(array, num)` | 批量释放 | 同样是 `const struct gpio *`；`legacy.rst:315` 也丢了 `const` |
| `gpio_direction_input(gpio)` | 设为输入 | → `gpiod_direction_input()`（`include/asm-generic/gpio.h:68-71`） |
| `gpio_direction_output(gpio, value)` | 设为输出 | → **`gpiod_direction_output_raw()`**（`include/asm-generic/gpio.h:72-75`）。**不翻译极性** |
| `gpio_get_value(gpio)` | 读值 | `static inline` → `__gpio_get_value()` → **`gpiod_get_raw_value()`**（`include/asm-generic/gpio.h:96-99`）。`legacy.rst:572-574` 说它是宏，实际是 `static inline`（`include/linux/gpio.h:64-67`） |
| `gpio_set_value(gpio, value)` | 写值 | → `__gpio_set_value()` → **`gpiod_set_raw_value()`**（`include/asm-generic/gpio.h:100-103`） |
| `gpio_get_value_cansleep(gpio)` | 可睡眠读 | → **`gpiod_get_raw_value_cansleep()`**（`include/asm-generic/gpio.h:82-85`） |
| `gpio_set_value_cansleep(gpio, value)` | 可睡眠写 | → **`gpiod_set_raw_value_cansleep()`**（`include/asm-generic/gpio.h:86-89`） |
| `gpio_cansleep(gpio)` | 是否可能睡眠 | |
| `gpio_set_debounce(gpio, debounce)` | 硬件去抖 | → `gpiod_set_debounce()`，这个不涉及极性 |
| `gpio_to_irq(gpio)` | 编号 → IRQ 号 | |
| `irq_to_gpio(irq)` | IRQ 号 → 编号 | **全树两处定义都直接 `return -EINVAL`**（`include/linux/gpio.h:84-87` 和 `:221-226`）。这个函数是**死的**，但 `legacy.rst:390-391` 还把它当可用的介绍 |
| `gpio_export(gpio, direction_may_change)` | 导出到 sysfs | 见 §10.2 |
| `gpio_export_link(dev, name, gpio)` | 建 sysfs 符号链接 | |
| `gpio_unexport(gpio)` | 取消导出 | |
| `devm_gpio_request(dev, gpio, label)` | devm 版申请 | 实现在 `gpiolib-devres.c` |
| `devm_gpio_request_one(dev, gpio, flags, label)` | devm 版申请 + 配置 | |

`GPIOF_*` 标志（`include/linux/gpio.h:20-42`）：`GPIOF_DIR_OUT`/`GPIOF_DIR_IN`、`GPIOF_INIT_LOW`/`GPIOF_INIT_HIGH`、组合别名 `GPIOF_IN`/`GPIOF_OUT_INIT_LOW`/`GPIOF_OUT_INIT_HIGH`、`GPIOF_ACTIVE_LOW`、`GPIOF_OPEN_DRAIN`、`GPIOF_OPEN_SOURCE`、`GPIOF_EXPORT`、`GPIOF_EXPORT_CHANGEABLE`、别名 `GPIOF_EXPORT_DIR_FIXED`/`GPIOF_EXPORT_DIR_CHANGEABLE`。

**`GPIOF_ACTIVE_LOW` 是个陷阱**：`gpio_request_one()` 会把它记到描述符的 `FLAG_ACTIVE_LOW` 上，但后续的 `gpio_get_value()` / `gpio_set_value()` 走 raw 路径**不看这个标志**。也就是说：设了，但没人用（除非你后来改用 `gpiod_*` API 访问同一根线）。

### §10.2 sysfs API（`/sys/class/gpio`）

> 头文件：`include/linux/gpio/consumer.h:716-741`（需要 `CONFIG_GPIO_SYSFS`）；实现：`drivers/gpio/gpiolib-sysfs.c`

| API | 说明 | 备注 |
|---|---|---|
| `gpiod_export(desc, direction_may_change)` | 在 `/sys/class/gpio/` 下建一个 `gpioN` 目录 | 类还没注册好时返回 `-ENOENT`（错误信息字面是 "called too early!"）；线未被 request 或已导出返回 `-EPERM` |
| `gpiod_export_link(dev, name, desc)` | 在 `dev` 的 sysfs 目录下建到 `gpioN` 的符号链接 | |
| `gpiod_unexport(desc)` | 撤销导出 | 先 `device_unregister` 再释放 irq，源码注释：「Release irq after deregistration to prevent race with edge_store.」 |

**这套 ABI 被 `Documentation/ABI/obsolete/sysfs-gpio` 明确标为 obsolete**，`Kconfig` 帮助文字也说 "This ABI is deprecated"。三个 raw 语义的坑：

1. 写 `direction` 用 `gpiod_direction_output_raw()`；
2. 对非输出线写 `value` 返回 **`-EPERM`**（不是 `-EIO` 也不是 `-EINVAL`）；
3. `edge` 属性配的边沿极性在 `FLAG_ACTIVE_LOW` 时**会被反过来**——和 `direction`/`value` 的 raw 语义不一致。

`legacy.rst:738` 把这些函数写成 `gpio_export()`（整数版），而 `admin-guide/gpio/sysfs.rst:146` 写成 `gpiod_export()`（描述符版），两处文档不一致——两个名字都存在，前者是后者的整数包装。

## §11 bgpio 库（MMIO 控制器的捷径）

> 头文件：`include/linux/gpio/driver.h:681-684`（需要 `CONFIG_GPIO_GENERIC`）；实现：`drivers/gpio/gpio-mmio.c`

| API | 说明 | 备注 |
|---|---|---|
| `bgpio_init(gc, dev, sz, dat, set, clr, dirout, dirin, flags)` | 一次填满 `gc` 的 get/set/direction 回调 | `sz` 是寄存器宽度（字节，支持 1/2/4/8）；`dat`/`set`/`clr`/`dirout`/`dirin` 是五个可选的寄存器地址；`flags` 是 `BGPIOF_*` 组合。全树 35 个文件调用它 |

`BGPIOF_*` 标志：`BGPIOF_BIG_ENDIAN`、`BGPIOF_UNREADABLE_REG_SET`、`BGPIOF_UNREADABLE_REG_DIR`、`BGPIOF_BIG_ENDIAN_BYTE_ORDER`、`BGPIOF_READ_OUTPUT_REG_SET`、`BGPIOF_NO_OUTPUT`、`BGPIOF_NO_SET_ON_INPUT`。

**为什么要知道这个**：读 GPIO 驱动代码时，如果一个驱动没有 `.get` / `.set` 回调却工作正常，八成是它调了 `bgpio_init()`。这也是索引工具容易误判的地方——回调是运行时填的，不是静态赋值。

## §12 错误码语义

`gpiod_*` API 返回的错误码分布得比较集中，按「看到这个码该往哪查」组织：

| 错误码 | 最常见的产生点 | 通常意味着什么 |
|---|---|---|
| `-ENOENT` | `gpiod_get()` 找不到 con_id 对应的属性 / 查找表项 | **DTS 写错了或忘写了**。用 `gpiod_get_optional()` 可以让它变成 NULL |
| `-EPROBE_DEFER` | ① `of_gpiochip_add_pin_range()` 里 `of_pinctrl_get()` 失败；② `gpio_request_one()` 里编号合法但描述符不存在 | GPIO 控制器（或它依赖的 pinctrl）还没 probe 完，正常的重试信号，**不要当错误报** |
| `-EBUSY` | `gpiod_request_commit()` 的 `test_and_set_bit(FLAG_REQUESTED)` 失败 | 这根线被别人占了。查 `debugfs/gpio` 看 label 是谁 |
| `-EINVAL` | ① offset 越界（`gpiochip_get_desc`）；② `of_gpio_simple_xlate()` 的 `args[0] >= gc->ngpio`；③ hog 节点没写 `input`/`output-low`/`output-high`；④ cdev 的 padding 非零、未知 ioctl | 参数或 DTS 内容不合法 |
| `-ENOTSUPP` | `gpio_set_config()` 系列：`gc->set_config` 为 NULL 或不认这个参数 | 控制器不支持这个电气配置。**不一定是 bug**，但 DT 里写的 bias / debounce 就是没生效 |
| `-EIO` | ① `gpiochip_lock_as_irq()` 发现线已是输出；② sysfs `edge` 写入时 `gpiod_to_irq() < 0` | 「这根线不能当中断用」 |
| `-EPERM` | ① sysfs 对非输出线写 `value`；② `gpiod_export()` 线未 request 或已导出；③ cdev 的 ABI 版本已被 pin 住又想用另一版 | 状态不对，不是权限问题——名字有误导 |
| `-ENODEV` | ① cdev 打开/ioctl 时 `gdev->chip` 为 NULL；② sysfs `edge` 找不到 `value` dirent | **芯片已经被移除**（热拔），或 sysfs 结构不完整 |
| `-ENOSYS` | `!CONFIG_GPIOLIB` 时全部 `static inline` 桩；`!CONFIG_OF_GPIO` 时 `of_get_named_gpio_flags()` 的桩 | **编译配置问题**，不是运行时问题。`of_gpio.h:70-78` 的注释解释了为什么用桩而不是编译错误：「Drivers may not strictly depend on the GPIO support, so let them link」 |
| `-ENOMEM` | 描述符数组、位图、kfifo 分配 | |
| `-EOPNOTSUPP` | cdev 的 v1 ioctl 在 `!CONFIG_GPIO_CDEV_V1` 时 | 内核编译时关掉了 v1 ABI |

**一个必须知道的「不返回错误码」的失败**：`gpiochip_remove()` 之后，内核消费者继续调 `gpiod_get_value()` 会**静默返回 0**，而不是错误码。原因是 `VALIDATE_DESC` 宏依赖的 `validate_desc()` 在「backing chip is gone」这条路上 `return 0`（表示「校验通过，什么都别做」）。用户态路径没有这个问题（cdev 有 `gdev->sem` + `-ENODEV`）。详见 `03-internals.md` Part B §B5。

## §13 上下文与可睡眠性速查

| 我在哪 | 能用什么 | 不能用什么 |
|---|---|---|
| probe / 进程上下文 | 全部 | — |
| 硬中断 handler | `gpiod_get_value()` / `gpiod_set_value()`（仅 `can_sleep == false` 的控制器）、`gpiod_get/set_array_value()` | 任何 `*_cansleep()` **不是不能用**，而是没必要；任何 `gpiod_get*()` 获取描述符的操作（会分配内存） |
| 软中断 / tasklet | 同硬中断 | 同上 |
| 线程化 IRQ handler / 工作队列 | 全部读写（含 `*_cansleep()`） | 获取/释放描述符最好也别在这里做 |
| 原子上下文 + I2C/SPI 扩展器上的线 | **什么都不能做** | 这类控制器 `can_sleep = true`，非 cansleep 版本会 `WARN_ON` 但仍执行——底层 I2C 传输会在原子上下文里睡眠，后果不可预测 |

判断一根线属于哪类：`gpiod_cansleep(desc)`，或者看 `debugfs/gpio` 里它属于哪个 chip。

---

## §14 命名约定一页速查

```
gpiod_*            描述符 API（推荐）
gpio_*             整数 API（已废弃）
gpiochip_*         提供方 API
gpiod_add_*        板级注册（查找表 / hog）
of_*gpio*          DT 过渡期 API
devm_*             上面各族的资源托管版本
bgpio_*            MMIO 通用库

后缀含义：
  _raw_            不做 active-low 翻译
  _cansleep        允许睡眠（无 WARN_ON）
  _optional        没配返回 NULL 而不是 ERR_PTR
  _array_          批量位图版本
  _commit          （内部）真正落到硬件的那一层
  _own_desc        提供方申请自己的线
  _from_of_node    直接给 DT 属性名，不加 -gpios 后缀
  _index / _idx    同一个 con_id 下的第几根

容易混的近邻对：
  gpiod_direction_output      vs  gpiod_direction_output_raw     ← 逻辑值 / 裸值
  gpiod_set_value（void）      vs  gpiod_set_array_value（int）   ← 返回类型不同
  gpiochip_line_is_persistent vs  FLAG_TRANSITORY                ← 语义相反
  gpiochip_is_requested       vs  gpiochip_line_is_valid         ← 前者返回字符串
  gpiod_get(dev,"reset",…)     vs  gpiod_get_from_of_node(np,"reset",…)
                                                                  ← 前者加后缀，后者不加
  GPIO_ACTIVE_LOW（machine.h） vs  GPIO_ACTIVE_LOW（dt-bindings）  ← 同名不同套
```

