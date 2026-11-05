---
title: API 参考
---

# pinctrl 代码地图与对外 API

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**。
> 本篇定位：**"这段代码在哪个文件" 和 "这个功能调什么 API" 是同一次查询，所以放在同一篇。**
>
> **格式约定**（全篇统一）：
> - API 表固定三列 `API / 说明 / 备注`。「说明」一句话讲它做什么，其余（上下文约束、返回值陷阱、`inline`/宏、是否导出、arm64 不涉及）全进「备注」
> - 函数签名只写 **函数名 + 形参名**，不带类型。类型信息不是删掉而是搬进「备注」
> - 位置只精确到**文件**，不给行号。行号是噪声，集中在 `03-internals.md` 和 `99-evidence.md` 里维护
> - 每节开头一行「头文件 / 实现」交代出处

---

## §0 代码地图

### §0.1 核心文件

`drivers/pinctrl/` 顶层的 6 个 `.c` + 3 个私有 `.h` 就是全部核心（合计 5,283 行）。其余 371 个 `.c` 全是厂商驱动。

| 文件 | 职责 | 本篇里对应的 API | 实现剖析 |
|---|---|---|---|
| `drivers/pinctrl/core.c` | 子系统骨架：控制器注册/注销、pin 表、句柄与状态机、mapping 表、GPIO range、debugfs 根。**导出 44 个符号，占全子系统 70%** | §2.1、§2.2、§3、§4、§5 | `03-internals.md` §A2/A3/A4/A6/A8/A11、§B1/B2/B4/B5/B6 |
| `drivers/pinctrl/core.h` | 私有头：`pinctrl_dev` / `pinctrl` / `pinctrl_state` / `pinctrl_setting` / `pin_desc` / `group_desc` 全在这里。**消费者和驱动都看不到** | 见 `02-datastructures.md` | — |
| `drivers/pinctrl/pinmux.c` | 复用层：引脚所有权仲裁（`pin_request`/`pin_free`）、setting 的 enable/disable、泛型 function 存储、`pinmux-*` debugfs | §2.3、§6.2 | `03-internals.md` §A9、§B3 |
| `drivers/pinctrl/pinmux.h` | 私有头：复用层内部接口 + `struct function_desc` + `!CONFIG_PINMUX` 的整套 stub | — | — |
| `drivers/pinctrl/pinconf.c` | 电气配置层：map→setting 转换、`pinconf_apply_setting`、`pinconf-*` debugfs | §2.4、§6.3 | `03-internals.md` §A10 |
| `drivers/pinctrl/pinconf.h` | 私有头：配置层内部接口 + stub | — | — |
| `drivers/pinctrl/pinconf-generic.c` | **可选层**：把 27 个标准 DT 属性翻译成 `PIN_CONFIG_*`，提供现成的 `dt_node_to_map` 实现 | §7 | `04-extensions.md` §2 |
| `drivers/pinctrl/devicetree.c` | DT 解析：读 `pinctrl-names` / `pinctrl-N`，逐条 phandle 找控制器、调驱动的 `dt_node_to_map` | 无对外 API（`pinctrl_dt_to_map` 是核心内部） | `03-internals.md` §A5 |
| `drivers/pinctrl/devicetree.h` | 私有头：`pinctrl_dt_to_map` / `pinctrl_dt_free_maps` + `!CONFIG_OF` stub | — | — |
| `drivers/pinctrl/pinctrl-utils.c` | **可选层**：给驱动的 `dt_node_to_map` 实现用的 map 数组增长/填充/释放辅助 | §8 | `04-extensions.md` §4 |
| `drivers/pinctrl/pinctrl-utils.h` | 上面那 5 个 helper 的声明 | §8 | — |
| `drivers/base/pinctrl.c` | **不在 `drivers/pinctrl/` 下**：设备核心侧的消费者胶水，`pinctrl_bind_pins()` 就在这里 | §4.3 | `03-internals.md` §A7 |

**两个容易找错地方的点**：

1. `pinctrl_bind_pins()` 在 `drivers/base/pinctrl.c`，不在 `drivers/pinctrl/`。它是 driver-core 的一部分，由 `drivers/base/dd.c` 调用
2. 有两个 `pinmux.h`（`drivers/pinctrl/pinmux.h` 私有、`include/linux/pinctrl/pinmux.h` 公开）和两个 `pinconf.h`（同理）。**同名但内容完全不同**，见 §9 近邻文件对照

### §0.2 厂商驱动目录

`drivers/pinctrl/` 下有 **28 个厂商子目录**，其余驱动直接以 `pinctrl-*.c` 形式散在顶层。

| 目录 | arm64 上是否常见 | 备注 |
|---|---|---|
| `qcom/` | 是 | 本文档集的全功能代表驱动 `pinctrl-msm.c` 在这里 |
| `mediatek/` | 是 | MT65xx/MT67xx/MT81xx 系列 |
| `meson/` | 是 | Amlogic |
| `renesas/` | 是 | R-Car；`renesas/core.c` 是全树唯一调用 `pinctrl_provide_dummies()` 的地方（见 §4.4） |
| `samsung/` | 是 | Exynos |
| `sunxi/` | 是 | Allwinner |
| `tegra/` | 是 | NVIDIA |
| `freescale/` | 是 | i.MX 系列（`pinctrl-mxs.c` 是 8 个仍在用裸 `pinctrl_register()` 的驱动之一） |
| `uniphier/`、`sprd/`、`visconti/`、`starfive/`、`nuvoton/`、`bcm/`、`mvebu/` | 是 | arm64 上都有在用的 SoC |
| `actions/`、`aspeed/`、`berlin/`、`stm32/`、`sunplus/`、`ti/` | 部分 | 视具体 SoC |
| `cirrus/`、`intel/`、`nomadik/`、`pxa/`、`ralink/`、`spear/`、`vt8500/` | 否 | 主要服务 x86 / arm32 / MIPS |

**顶层的非目录驱动**命名一律是 `pinctrl-<厂商或 SoC>.c`，例如 `pinctrl-single.c`（1998 行，OMAP 系的通用"一 pin 一寄存器"驱动）、`pinctrl-rockchip.c`（4046 行）、`pinctrl-apple-gpio.c`（531 行，本文档集的最简代表驱动）。

### §0.3 头文件映射

`include/linux/pinctrl/` 下 8 个公开头文件，共 1,106 行。**按"你是哪种角色"来选头文件**：

| 头文件 | 面向 | 本篇引用它的小节 |
|---|---|---|
| `include/linux/pinctrl/consumer.h` | **消费者驱动**（i2c/spi/uart…）。除了 6 个 GPIO 桥接函数之外，消费者只需要这一个 | §4.1、§4.2 |
| `include/linux/pinctrl/pinctrl.h` | **pin controller 驱动**：`pinctrl_desc` / `pinctrl_ops` / `pinctrl_pin_desc` / `pingroup` / `pinfunction` / `pinctrl_gpio_range` + 注册 API | §2.1、§2.2、§5、§6.1 |
| `include/linux/pinctrl/pinmux.h` | pin controller 驱动：**只有** `struct pinmux_ops` 一个定义 | §6.2 |
| `include/linux/pinctrl/pinconf.h` | pin controller 驱动：**只有** `struct pinconf_ops` 一个定义 | §6.3 |
| `include/linux/pinctrl/pinconf-generic.h` | 用泛型配置的 pin controller 驱动：`enum pin_config_param`（25 个参数）+ 打包宏 + `pinconf_generic_dt_*` | §7 |
| `include/linux/pinctrl/machine.h` | **板级代码**：`struct pinctrl_map` + `enum pinctrl_map_type` + 12 个 `PIN_MAP_*` 构造宏 + `pinctrl_register_mappings`。arm64 走 DT，基本不用这个头 | §3 |
| `include/linux/pinctrl/pinctrl-state.h` | 所有人：四个标准状态名字符串常量 | §4.2 |
| `include/linux/pinctrl/devinfo.h` | **driver-core**：`struct dev_pin_info` + `pinctrl_bind_pins` / `pinctrl_init_done` | §4.3 |

**`include/uapi/linux/pinctrl*.h` 不存在**（`ls` 返回 0 个文件）。pinctrl **没有任何用户态 ABI**：没有 ioctl、没有字符设备、没有 sysfs 属性。它的对外可观测面只有 debugfs（见 `07-debug.md` §4）。这也是判定它不是"虚拟总线/跨边界 ABI 型"的第二道门（见 `99-evidence.md` §1.3）。

**包含关系**（写驱动时只 include 最上层那个就够）：

```
 pinctrl.h ──┬── 被 pinmux.h include
             ├── 被 pinconf.h include（间接）
             └── 被 pinconf-generic.h include（间接）

 consumer.h ── 独立，不 include pinctrl.h
               ↑ 消费者驱动只需要这一个

 devinfo.h ── 被 include/linux/device.h include
              （所以 struct device 里能有 pins 字段）
```

### §0.4 Kconfig

全部在 `drivers/pinctrl/Kconfig`（545 行）。**5 个开关里有 3 个是隐藏的**——这是本子系统 Kconfig 最容易搞错的地方。

| 选项 | 类型 | 效果 |
|---|---|---|
| `PINCTRL` | `menuconfig bool` | 总开关。关掉后 `core.c` / `pinmux.c` / `pinconf.c` / `devicetree.c` 全部不编译，`consumer.h` 里整套 API 退化成 stub（`pinctrl_get()` stub **返回 NULL 而不是 ERR_PTR**，见 §4.1 的陷阱）。关掉后 `drivers/base/pinctrl.c` 里的 `pinctrl_bind_pins()` 也退化成 `return 0` 的 inline |
| `PINMUX` | `bool`，**只在 `COMPILE_TEST` 下可见**，正常由驱动 `select` | 编入 `pinmux.c`。关掉后 `pinmux_ops` 里的回调永不被调用，`struct pin_desc` 里 `mux_owner`/`gpio_owner`/`mux_usecount` 等字段被 `#ifdef` 掉，debugfs 少 3 个 `pinmux-*` 文件 |
| `PINCONF` | `bool`，同样只在 `COMPILE_TEST` 下可见 | 编入 `pinconf.c`。关掉后所有 `PIN_MAP_TYPE_CONFIGS_*` 的 map 在 `pinconf_validate_map()` stub 里被拒（返回 `-EINVAL`），debugfs 少 2 个 `pinconf-*` 文件 |
| `GENERIC_PINCONF` | `bool`，`select PINCONF` | 编入 `pinconf-generic.c`，同时让 `pinconf_ops.is_generic` 和 `pinctrl_desc.custom_params` 字段存在（二者都在 `#ifdef CONFIG_GENERIC_PINCONF` 之下） |
| `GENERIC_PINCTRL_GROUPS` | 隐藏 `bool`，由驱动 `select` | 启用核心自带的 group radix tree 存储 + `pinctrl_generic_*` 一族函数 |
| `GENERIC_PINMUX_FUNCTIONS` | 隐藏 `bool`，`select PINMUX` | 启用核心自带的 function radix tree 存储 + `pinmux_generic_*` 一族函数 |
| `DEBUG_PINCTRL` | `bool`，`depends on DEBUG_KERNEL` | help 原文："add some extra checks and diagnostics to PINCTRL calls"。实际效果见 `07-debug.md` §2 |

**容易搞错的三点**：

1. **`PINMUX` 和 `PINCONF` 你在 menuconfig 里通常找不到**——它们带 `if COMPILE_TEST` 才显示。正常路径是厂商驱动 `select PINMUX` / `select GENERIC_PINCONF`。所以"我的 pinctrl 驱动编进去了但复用不生效"的第一个排查点是 Kconfig 有没有 select
2. **`GENERIC_PINCONF` 和 `GENERIC_PINCTRL_GROUPS` / `GENERIC_PINMUX_FUNCTIONS` 是三件互不相干的事**：前者是"DT 属性怎么翻译"，后两者是"group/function 表存在哪"。名字都带 GENERIC，但一个都不互相依赖
3. **`CONFIG_OF` 影响 `devicetree.c` 是否编入**，但它不在 pinctrl 的 Kconfig 里。arm64 上一定开着

---

## §1 API 全景：五种角色，五套 API

pinctrl 的 API 面很宽，但**按你的角色切一刀之后每个人只用一小块**：

| 你的角色 | 你需要的头文件 | 你用的 API | 本篇章节 |
|---|---|---|---|
| **写消费者驱动**（i2c/spi/uart 控制器…） | `consumer.h` + `pinctrl-state.h` | `pinctrl_get` / `lookup_state` / `select_state` 四件套，或干脆什么都不写（设备核心代劳） | §4 |
| **写 pin controller 驱动** | `pinctrl.h`（+ `pinmux.h` / `pinconf.h` / `pinconf-generic.h` 按需） | `devm_pinctrl_register_and_init` + 实现三层 ops | §2、§6 |
| **用泛型 group/function 存储的 pin controller 驱动** | 同上 | 额外用 `pinctrl_generic_add_group` / `pinmux_generic_add_function` | §5 |
| **写 gpiolib 驱动**（要接 pinctrl 后端） | `consumer.h` | 6 个 `pinctrl_gpio_*` 桥接函数 | §4.4 |
| **写板级代码**（非 DT 平台） | `machine.h` | `pinctrl_register_mappings` + `PIN_MAP_*` 宏 | §3 |

**导出符号总数 = 63 个**（全部是 `EXPORT_SYMBOL_GPL`，没有一个非 GPL 导出）：

| 文件 | 导出数 | 说明 |
|---|---|---|
| `drivers/pinctrl/core.c` | 44 | 注册/注销、句柄四件套、GPIO range、泛型 group、PM 选择器 |
| `drivers/pinctrl/pinmux.c` | 6 | 全部是 `pinmux_generic_*` 泛型 function 存储 |
| `drivers/pinctrl/pinconf-generic.c` | 5 | `pinconf_generic_dt_*` + `pinconf_generic_parse_dt_config` |
| `drivers/pinctrl/pinctrl-utils.c` | 5 | map 数组辅助 |
| `drivers/pinctrl/devicetree.c` | 3 | `of_pinctrl_get` 等 |
| `drivers/pinctrl/pinconf.c` | **0** | 整个文件都是核心内部函数，一个都不导出 |

**"符号存在" ≠ "你能用"**。判据是 `EXPORT_SYMBOL*` **加上**头文件里有声明，**不是有没有 kerneldoc**。`pinconf.c` 里 `pinconf_apply_setting()` 写满了注释，但既不导出也只在私有 `pinconf.h` 里声明——它长得像公开 API 而实际上不是。同类陷阱清单见 `05-usage.md` §6。

---

## §2 pin controller 驱动：注册 API

> 头文件：`include/linux/pinctrl/pinctrl.h`；实现：`drivers/pinctrl/core.c`

### §2.1 四个注册函数（选哪个）

| API | 说明 | 备注 |
|---|---|---|
| `devm_pinctrl_register_and_init(dev, pctldesc, driver_data, pctldev)` | **推荐写法**：建控制器 + 校验 ops + 登记 pin 表，但**不使能**；devres 自动注销 | 返回 `int`；`pctldev` 是输出参数（`struct pinctrl_dev **`）。注册完必须再调 `pinctrl_enable()` 才生效。**arm64 主流写法之一（19 个文件在用）** |
| `devm_pinctrl_register(dev, pctldesc, driver_data)` | 建控制器 + 立即使能，一步到位；devres 自动注销 | 返回 `struct pinctrl_dev *`，失败是 `ERR_PTR`。**全树用得最多（74 个文件）**。内部就是 `and_init` + `enable` |
| `pinctrl_register_and_init(pctldesc, dev, driver_data, pctldev)` | 非 devm 版本，需自己配 `pinctrl_unregister()` | 返回 `int`。**全树 0 个调用者**——存在但没人用，新代码不要选它 |
| `pinctrl_register(pctldesc, dev, driver_data)` | 非 devm 的一步到位版本 | 返回 `struct pinctrl_dev *`。**kerneldoc 自陈已废弃**（原文："known to have problems as the pin controller driver functions are called before the driver has a `struct pinctrl_dev` handle … please use the new `pinctrl_register_and_init()` below instead"）。仍有 8 个驱动在用，见 §2.5 |
| `pinctrl_enable(pctldev)` | 认领 hog 映射，然后把控制器挂进全局链表——**在此之前它对消费者不可见** | 返回 `int`。只有走 `*_and_init` 变体才需要显式调 |
| `pinctrl_unregister(pctldev)` | 摘链、删 debugfs、释放泛型 group/function 表 | 返回 `void`。内部有 `/* TODO: check that no pinmuxes are still active? */`——**它不检查是否还有活跃的复用**，见 `03-internals.md` §C |
| `devm_pinctrl_unregister(dev, pctldev)` | 提前触发 devres 注销 | 返回 `void`。正常路径不需要调，devres 会自动做 |

**为什么要分 `register` 和 `enable` 两步**：`pinctrl_register()`（一步到位版）的问题在于，注册过程中核心会回调驱动的 ops（比如枚举 group），但此时驱动还没拿到 `struct pinctrl_dev *`，无法在回调里通过 `pinctrl_dev_get_drvdata()` 取回自己的私有数据。`*_and_init` 变体先把 `pctldev` 交给驱动、驱动完成剩余初始化（比如往泛型表里 add group/function）、再调 `pinctrl_enable()` 才让核心开始回调。

**四种写法的选择决策树**：

```
 你的控制器需要在注册后、使能前做初始化吗？
 （例：往泛型 group/function 表里 add 条目、
   或者在 ops 回调里需要 pinctrl_dev_get_drvdata()）
   │
   ├── 需要 ──→ devm_pinctrl_register_and_init()
   │             ... 驱动自己的初始化 ...
   │             pinctrl_enable()
   │
   └── 不需要（pin 表和 group 表都是静态数组）
                 └──→ devm_pinctrl_register()     ← 最简，74 个文件在用

 两个非 devm 版本：新代码不要用。
   pinctrl_register() 已被 kerneldoc 自陈废弃；
   pinctrl_register_and_init() 全树 0 调用者。
```

### §2.2 注册描述符 `struct pinctrl_desc`

驱动填这一个结构体交给核心。定义在 `include/linux/pinctrl/pinctrl.h`：

```c
struct pinctrl_desc {
        const char *name;                        /* 控制器名，进 debugfs 目录名 */
        const struct pinctrl_pin_desc *pins;     /* pin 表 */
        unsigned int npins;                      /* 通常 = ARRAY_SIZE(pins) */
        const struct pinctrl_ops *pctlops;       /* 枚举层，kerneldoc 说 optional */
        const struct pinmux_ops *pmxops;         /* 复用层，可为 NULL */
        const struct pinconf_ops *confops;       /* 配置层，可为 NULL */
        struct module *owner;                    /* 一律填 THIS_MODULE */
#ifdef CONFIG_GENERIC_PINCONF
        unsigned int num_custom_params;          /* 厂商私有 DT 属性个数 */
        const struct pinconf_generic_params *custom_params;
        const struct pin_config_item *custom_conf_items;   /* 必须和 custom_params 等长 */
#endif
        bool link_consumers;                     /* 建 device link 以定 suspend 顺序 */
};
```

| 字段 | 必填？ | 备注 |
|---|---|---|
| `name` | 是 | 出现在 `/sys/kernel/debug/pinctrl/` 的目录名里。与 `dev_name(dev)` **不同名**时，debugfs 目录名变成 `"<dev_name>-<desc->name>"` 而不是单独一个名字 |
| `pins` / `npins` | 是 | 用 `PINCTRL_PIN(number, name)` 宏构造；不需要名字时用 `PINCTRL_PIN_ANON(number)`。**pin 编号可以稀疏** |
| `pctlops` | 名义可选，实际必填 | kerneldoc 写的是 "this is optional"，但 `pinctrl_check_ops()` 一开头就检查 `pctlops` 为 NULL 直接 `-EINVAL`。这是 kerneldoc 与代码不一致的一处 |
| `pmxops` | 否 | 为 NULL 时核心跳过 `pinmux_check_ops()`，也不创建 `pinmux-*` debugfs 文件。全树 6 个控制器确实没有 |
| `confops` | 否 | 同理。全树 17 个控制器没有 |
| `owner` | 是 | 核心用它做模块引用计数（`try_module_get`） |
| `custom_params` / `custom_conf_items` | 否 | 两者**必须等长**（kerneldoc 明确要求），长度都等于 `num_custom_params`。用 `PCONFDUMP()` 宏构造 `custom_conf_items` |
| `link_consumers` | 否 | 置 true 后核心为每个消费者建 device link。用途是确保 suspend/resume 顺序正确——**消费者应该比 pin controller 先 suspend** |

**`pinctrl_desc` 可以是栈上的临时变量吗？不行也行**——核心在 `pinctrl_init_controller()` 里把整个 `pctldesc` 指针存进 `pctldev->desc`，不做深拷贝。所以 desc 必须活得比控制器长。全树 63 个静态 `struct pinctrl_desc X = {` 是一种写法，另一种是把 desc 内嵌进驱动私有结构里动态填（`pinctrl-msm.c` 就是这么做的）。

### §2.3 pin 表的构造

```c
static const struct pinctrl_pin_desc foo_pins[] = {
        PINCTRL_PIN(0,  "GPIO0"),
        PINCTRL_PIN(1,  "GPIO1"),
        /* 编号可以跳 —— 稀疏是合法的 */
        PINCTRL_PIN(16, "SDCLK"),
        PINCTRL_PIN_ANON(17),          /* 不给名字，核心自动叫 "PIN17" */
};
```

| 宏 | 说明 | 备注 |
|---|---|---|
| `PINCTRL_PIN(a, b)` | 构造一个带名字的 pin 描述符 | 展开成 `{ .number = a, .name = b }`。`b` 必须是常量字符串或长期有效的指针——核心不拷贝 |
| `PINCTRL_PIN_ANON(a)` | 构造匿名 pin | 展开成 `{ .number = a }`。核心随后在 `pinctrl_register_one_pin()` 里用 `kasprintf` 生成 `"PIN%u"` 作为名字 |
| `PINCTRL_PINGROUP(_name, _pins, _npins)` | 构造 `struct pingroup` 字面量 | 用于驱动自己维护 group 表时 |
| `PINCTRL_PINFUNCTION(_name, _groups, _ngroups)` | 构造 `struct pinfunction` 字面量 | 同上，用于 function 表 |

**重复的 pin 编号会被拒绝**：`pinctrl_register_one_pin()` 先查 radix tree，发现该编号已存在就 `dev_err("pin %d already registered")` 并返回 `-EINVAL`。所以 pin 表里不能有重号。

### §2.4 控制器自身的元信息读取

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_dev_get_name(pctldev)` | 取 `desc->name` | 返回 `const char *`。给别的框架（如 gpiolib）用来打日志 |
| `pinctrl_dev_get_devname(pctldev)` | 取底层 `struct device` 的 `dev_name()` | 返回 `const char *`。与上一个**不是同一个字符串**——前者是 desc 里写的名字，后者是 platform 设备名 |
| `pinctrl_dev_get_drvdata(pctldev)` | 取注册时传入的 `driver_data` | 返回 `void *`。在 ops 回调里拿回私有结构的**唯一**正规途径 |
| `of_pinctrl_get(np)` | 由 DT 节点找到已注册的 `pinctrl_dev` | 返回 `struct pinctrl_dev *`，找不到返回 NULL。`!CONFIG_OF` 时是返回 NULL 的 inline stub |

### §2.5 仍在用裸 `pinctrl_register()` 的 8 个驱动

全树 grep 结果（口径：非 devm、非 `_and_init` 的 `pinctrl_register(` 调用点）：

```
drivers/pinctrl/pinctrl-artpec6.c
drivers/pinctrl/ralink/pinctrl-ralink.c
drivers/pinctrl/sprd/pinctrl-sprd.c
drivers/pinctrl/bcm/pinctrl-ns2-mux.c
drivers/pinctrl/pinctrl-k210.c
drivers/pinctrl/pinctrl-oxnas.c
drivers/pinctrl/aspeed/pinctrl-aspeed.c
drivers/pinctrl/freescale/pinctrl-mxs.c
```

这 8 个不是"写错了"——它们的注册路径确实不需要在 register 和 enable 之间插初始化。但既然 kerneldoc 已经自陈这个入口有设计缺陷，**新驱动不要照抄它们**。重跑命令见 `99-evidence.md` §2.3。

---

## §3 板级 mapping API（arm64 上基本用不到）

> 头文件：`include/linux/pinctrl/machine.h`；实现：`drivers/pinctrl/core.c`

**arm64 走 DT，这一节的 API 只在没有 DT 的平台上用**。放在这里是因为：(1) `struct pinctrl_map` 这个结构体在 DT 路径上照样是核心的中间表示（DT 解析的产物就是它）；(2) 读 `03-internals.md` 时会碰到 `PIN_MAP_TYPE_*` 枚举。

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_register_mappings(map, num_maps)` | 把一个 `pinctrl_map` 数组注册进全局 mapping 表 | 返回 `int`。核心**深拷贝**整个数组（`kmemdup`），所以传 `__initdata` 数组是安全的。逐条校验 type/`ctrl_dev_name`/data，任一条不合法整批失败 |
| `pinctrl_unregister_mappings(map)` | 按数组首地址撤销注册 | 返回 `void`。参数必须是**当初传给 register 的那个指针** |
| `pinctrl_provide_dummies()` | 告诉核心"没找到映射时给一个假状态而不是报错" | 返回 `void`。**全树只有 1 个调用者**：`drivers/pinctrl/renesas/core.c`。用于那些引脚由固件/复位默认值配好、内核不需要动的平台 |

**`struct pinctrl_map` 的五种 type**（`enum pinctrl_map_type`）：

| type | 含义 | `data` union 用哪个成员 | 校验函数 |
|---|---|---|---|
| `PIN_MAP_TYPE_INVALID` | 占位，注册时被拒 | — | `pinctrl_register_mappings()` 的 switch 直接 `-EINVAL` |
| `PIN_MAP_TYPE_DUMMY_STATE` | 只声明"这个状态存在"，不做任何硬件动作 | 不用 | 无需 `ctrl_dev_name` |
| `PIN_MAP_TYPE_MUX_GROUP` | 一次复用：把 function 应用到 group | `data.mux`（`group` + `function`） | `pinmux_validate_map()` |
| `PIN_MAP_TYPE_CONFIGS_PIN` | 对单个 pin 应用一组配置 | `data.configs`（`group_or_pin` + `configs` + `num_configs`） | `pinconf_validate_map()` |
| `PIN_MAP_TYPE_CONFIGS_GROUP` | 对整个 group 应用一组配置 | 同上 | 同上 |

**12 个构造宏**，命名规律是 `PIN_MAP_<动作>[_DEFAULT][_HOG]`：

| 宏族 | 三个变体 | 差别 |
|---|---|---|
| `PIN_MAP_MUX_GROUP(dev, state, pinctrl, grp, func)` | `_DEFAULT`（省掉 state 参数，固定 `"default"`）、`_HOG`（省掉 pinctrl 参数，用 dev 自己）、`_HOG_DEFAULT`（两个都省） | — |
| `PIN_MAP_CONFIGS_PIN(dev, state, pinctrl, pin, cfgs)` | 同上三个变体 | — |
| `PIN_MAP_CONFIGS_GROUP(dev, state, pinctrl, grp, cfgs)` | 同上三个变体 | — |
| `PIN_MAP_DUMMY_STATE(dev, state)` | 无变体 | — |

**`_HOG` 是什么**：hog（"占坑"）指**控制器给自己上的映射**——`dev_name` 和 `ctrl_dev_name` 是同一个字符串。用于"系统起来就要固定配好、没有对应设备驱动"的引脚（比如一根一直要拉高的复位线）。核心在 `pinctrl_enable()` → `pinctrl_claim_hogs()` 里认领它们，且**只认 `"default"` 状态的 hog**。完整机制见 `03-internals.md` §B3。

---

## §4 消费者 API

> 头文件：`include/linux/pinctrl/consumer.h` + `include/linux/pinctrl/pinctrl-state.h`；实现：`drivers/pinctrl/core.c`

**先看这一段再决定要不要写代码**：如果你的设备只需要 `default` 状态，**一行 pinctrl 代码都不用写**。设备核心在 probe 之前已经替你做完了（见 §4.3）。只有下面三种情况才需要自己调 API：

1. 需要运行时在多个状态之间切（比如省电时切 `sleep`）
2. 需要在 probe 的特定时点切，而不是 probe 之前
3. 你在写 gpiolib 驱动（用 §4.4 的桥接函数）

### §4.1 句柄四件套

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_get(dev)` | 为一个设备创建 pinctrl 句柄：解析 DT / 遍历 mapping 表，把所有状态和 setting 建成树 | 返回 `struct pinctrl *`，失败是 `ERR_PTR`。带 `__must_check`。**同一个 dev 重复调返回同一个句柄且引用计数 +1**。必须配 `pinctrl_put()`，**不能**配 `devm_pinctrl_put()` |
| `pinctrl_put(p)` | 释放句柄，引用计数归零时销毁整棵状态树 | 返回 `void`。**不能**用来释放 `devm_pinctrl_get()` 的返回值 |
| `devm_pinctrl_get(dev)` | 同 `pinctrl_get()`，但挂 devres，设备移除时自动释放 | 返回 `struct pinctrl *`，失败是 `ERR_PTR`。**推荐用法** |
| `devm_pinctrl_put(p)` | 提前触发 devres 释放 | 返回 `void`。正常路径不需要调 |
| `pinctrl_lookup_state(p, name)` | 在句柄里按名字找一个状态 | 返回 `struct pinctrl_state *`，找不到是 `ERR_PTR(-ENODEV)`。带 `__must_check`。**只是查表，不碰硬件** |
| `pinctrl_select_state(p, state)` | **真正让状态生效**：拆旧状态 + 装新状态 + 写寄存器 | 返回 `int`。**只能在可睡眠上下文调用**（全 mutex）。当前已是该状态时直接返回 0（快路径） |
| `pinctrl_select_default_state(dev)` | 把设备切到它的 `default` 状态 | 返回 `int`。用 `dev->pins` 里缓存的指针，不重新 lookup。设备没有 `dev->pins` 时返回 0（不是错误） |

**返回值语义速查**：

| 情况 | `pinctrl_get` / `devm_pinctrl_get` | `pinctrl_lookup_state` | `pinctrl_select_state` |
|---|---|---|---|
| 成功 | 有效指针 | 有效指针 | `0` |
| 设备在 DT 里没有 `pinctrl-N` 属性 | **成功**，返回一个没有任何状态的空句柄 | `ERR_PTR(-ENODEV)` | — |
| 引用的控制器还没 probe | `ERR_PTR(-EPROBE_DEFER)` | — | — |
| 状态名不存在 | — | `ERR_PTR(-ENODEV)` | — |
| 引脚被别人占了 | — | — | `-EINVAL`（来自 `pin_request()`，**不是 `-EBUSY`**，见 §11） |
| `CONFIG_PINCTRL=n` | **`NULL`，不是 `ERR_PTR`** | `NULL` | `0` |

**最容易写错的一处**：`!CONFIG_PINCTRL` 时 `pinctrl_get()` 的 stub 返回的是 **`NULL`**，而不是 `ERR_PTR(-ENODEV)`。所以

```c
p = devm_pinctrl_get(dev);
if (IS_ERR(p))           /* CONFIG_PINCTRL=n 时 NULL 过不了这个判断 */
        return PTR_ERR(p);
state = pinctrl_lookup_state(p, "sleep");   /* 传 NULL 进去 —— stub 版本会安全返回 NULL */
```
在 `CONFIG_PINCTRL=n` 的内核上是安全的（因为 `lookup_state` 也是 stub），但**如果你在中间对 `p` 做了任何解引用就会 oops**。稳妥写法见 `05-usage.md` §3.2。

### §4.2 四个标准状态名

`include/linux/pinctrl/pinctrl-state.h` 里只有四个 `#define`：

| 常量 | 字符串 | 什么时候被选上 |
|---|---|---|
| `PINCTRL_STATE_DEFAULT` | `"default"` | probe 前（无 `init` 时）由设备核心选；probe 后由 `pinctrl_init_done()` 从 `init` 切过来；resume 时由驱动自己选 |
| `PINCTRL_STATE_INIT` | `"init"` | probe **之前**。存在的理由（头文件原文）：有些驱动 probe 前就切到 default 会让引脚产生毛刺（glitch），这时用 `init` 表示"probe 前的引脚状态" |
| `PINCTRL_STATE_IDLE` | `"idle"` | 系统半睡（时钟门控但还供电）。典型从 `pm_runtime_suspend()` / `pm_runtime_idle()` 里选 |
| `PINCTRL_STATE_SLEEP` | `"sleep"` | 系统最深睡眠。典型从普通 `.suspend()` 里选 |

**三个 PM 选择器**（都在 `#ifdef CONFIG_PM` 下是真函数、否则是返回 0 的 inline stub）：

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_pm_select_default_state(dev)` | 切到 default | 返回 `int`。`!CONFIG_PM` 时是返回 0 的 inline |
| `pinctrl_pm_select_sleep_state(dev)` | 切到 sleep | 同上 |
| `pinctrl_pm_select_idle_state(dev)` | 切到 idle | 同上 |

这三个和 §4.1 的 `pinctrl_select_default_state()` 的区别：**前三个用 `dev->pins` 里 driver-core 预先 lookup 好的缓存指针**，`dev->pins` 为 NULL 时直接返回 0；后者也用缓存，但不带 `CONFIG_PM` 条件。四个函数全部**不会**自己去 `pinctrl_lookup_state()`。

### §4.3 设备核心的自动路径

> 头文件：`include/linux/pinctrl/devinfo.h`；实现：`drivers/base/pinctrl.c`

这两个函数**不是给驱动调的**，由 `drivers/base/dd.c` 的 `really_probe()` 调用。列在这里是因为读者需要知道"默认路径上发生了什么"。

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_bind_pins(dev)` | probe **之前**：建句柄、lookup 四个状态存进 `dev->pins`、选上 `init`（无则 `default`） | 返回 `int`。`dev->of_node_reused` 为真时直接返回 0（不做任何事）。只有 `-EPROBE_DEFER` 和 `-EINVAL` 会往上抛，`-ENOENT` 一类被吞掉（源码注释："We ignore errors like -ENOENT meaning no pinctrl state"） |
| `pinctrl_init_done(dev)` | probe **之后**：如果当前还停在 `init` 状态，切到 `default` | 返回 `int`。实现在 `drivers/pinctrl/core.c`。`dev->pins` 为 NULL 或没有 `init_state` 时返回 0 |

`struct dev_pin_info`（挂在 `struct device.pins` 上）：

```c
struct dev_pin_info {
        struct pinctrl *p;
        struct pinctrl_state *default_state;
        struct pinctrl_state *init_state;
#ifdef CONFIG_PM
        struct pinctrl_state *sleep_state;
        struct pinctrl_state *idle_state;
#endif
};
```

`sleep_state` / `idle_state` 在 `!CONFIG_PM` 时**字段本身不存在**。所以引用它们的代码必须自己带 `#ifdef CONFIG_PM`（`drivers/base/pinctrl.c` 里就是这么写的）。

### §4.4 GPIO 桥接（只给 gpiolib 驱动用）

官方文档明确规定这几个函数**只许 gpiolib 调用**（`Documentation/driver-api/pin-control.rst` 的 "GPIO mode pitfalls" 一节）。普通消费者驱动想让某根引脚做 GPIO，应该在 DTS 里写 `function = "gpio"`，不是调这些。

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_gpio_can_use_line(gpio)` | 问一句"这根线现在能不能拿去当 GPIO" | 返回 `bool` 而不是错误码。用于 gpiolib 的 `.request` 前置检查 |
| `pinctrl_gpio_request(gpio)` | 把一根 GPIO 编号对应的 pin 请求为 GPIO 用途 | 返回 `int`。参数是**全局 GPIO 号**，核心内部通过 `pinctrl_gpio_range` 换算成控制器局部 pin 号 |
| `pinctrl_gpio_free(gpio)` | 上一个的反向操作 | 返回 `void` |
| `pinctrl_gpio_direction_input(gpio)` | 通知 pin controller"这根线要做输入" | 返回 `int`。控制器没实现 `gpio_set_direction` 时返回 0（不是错误） |
| `pinctrl_gpio_direction_output(gpio)` | 同上，方向为输出 | 返回 `int` |
| `pinctrl_gpio_set_config(gpio, config)` | 对一根 GPIO 对应的 pin 施加一条 `PIN_CONFIG_*` 配置 | 返回 `int`。`config` 是打包好的 `unsigned long`（用 `pinconf_to_config_packed()` 打包） |

**GPIO range 注册**（pin controller 驱动侧）：

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_add_gpio_range(pctldev, range)` | 登记一段 GPIO 号 ↔ pin 号映射 | 返回 `void`。**从 pin controller 驱动里调用已被官方文档标为 DEPRECATED**——DT 里写 `gpio-ranges` 属性时 gpiolib 会自己建 range |
| `pinctrl_add_gpio_ranges(pctldev, ranges, nranges)` | 批量版本 | 返回 `void` |
| `pinctrl_remove_gpio_range(pctldev, range)` | 撤销 | 返回 `void` |
| `pinctrl_find_and_add_gpio_range(devname, range)` | 按控制器 devname 找到控制器再加 range | 返回 `struct pinctrl_dev *`，找不到返回 `ERR_PTR(-EPROBE_DEFER)` |
| `pinctrl_find_gpio_range_from_pin(pctldev, pin)` | 反查：某个 pin 落在哪个 range 里 | 返回 `struct pinctrl_gpio_range *` 或 NULL。内部会拿 `pctldev->mutex` |
| `pinctrl_find_gpio_range_from_pin_nolock(pctldev, pin)` | 同上但不拿锁 | 调用者必须已持 `pctldev->mutex` |
| `pinctrl_get_group_pins(pctldev, pin_group, pins, num_pins)` | 按 group 名取出它的 pin 数组 | 返回 `int`。给 gpiolib 之外的框架用 |

**"DEPRECATED" 的代码侧证据**在 `drivers/pinctrl/qcom/pinctrl-msm.c` 的 `msm_gpio_init()` 里：它显式检查 DT 有没有 `gpio-ranges`，**只在没有的时候**才手工调 `gpiochip_add_pin_range()`，注释写明了原因。所以新驱动的正确做法是在 DTS 里写 `gpio-ranges`（见 `06-devicetree.md` §3）。

`struct pinctrl_gpio_range` 的两种形态：

```c
struct pinctrl_gpio_range {
        struct list_head node;
        const char *name;
        unsigned int id;
        unsigned int base;         /* 起始的全局 GPIO 号 */
        unsigned int pin_base;     /* 起始的控制器局部 pin 号 —— 线性形态用 */
        unsigned int const *pins;  /* 稀疏形态：GPIO 偏移 → pin 号的查找表 */
        unsigned int npins;
        struct gpio_chip *gc;
};
```

**`pins` 非空时 `pin_base` 被完全忽略**。`gpio_to_pin()` 的分支就是看 `range->pins` 是不是 NULL。两种形态混填不会报错，只会静默按 `pins` 走——这是一个静默失效点，见 `03-internals.md` §B4。

---

## §5 泛型 group / function 存储 API

> 头文件：`drivers/pinctrl/core.h`（group）+ `drivers/pinctrl/pinmux.h`（function）——**都是私有头**；实现：`drivers/pinctrl/core.c` / `drivers/pinctrl/pinmux.c`

**这一族函数的定位很特别：`EXPORT_SYMBOL_GPL` 导出了，但只在私有头里声明。** 后果是**只有住在 `drivers/pinctrl/` 下的驱动能用**（因为只有它们能 `#include "core.h"`）。树外模块拿不到声明。这不是 bug，是刻意的边界。

启用条件：`select GENERIC_PINCTRL_GROUPS` / `select GENERIC_PINMUX_FUNCTIONS`。

### §5.1 group 一族（`GENERIC_PINCTRL_GROUPS`）

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_generic_get_group_count(pctldev)` | 返回已注册 group 数 | 返回 `int`。可以**直接赋给** `pinctrl_ops.get_groups_count` |
| `pinctrl_generic_get_group_name(pctldev, selector)` | 按 selector 返回 group 名 | 返回 `const char *`。可直接赋给 `pinctrl_ops.get_group_name` |
| `pinctrl_generic_get_group_pins(pctldev, selector, pins, num_pins)` | 按 selector 返回 pin 数组 | 返回 `int`。可直接赋给 `pinctrl_ops.get_group_pins` |
| `pinctrl_generic_get_group(pctldev, selector)` | 取出整个 `struct group_desc`（含驱动自己塞的 `data` 指针） | 返回 `struct group_desc *` 或 NULL。**给驱动在 `set_mux` 里取回 group 私有数据用** |
| `pinctrl_generic_add_group(pctldev, name, pins, num_pins, data)` | 往 radix tree 里加一个 group | 返回 `int`（新 group 的 selector，≥0）。内部用 `devm_kzalloc`，所以不需要手工释放。**只能在 probe 里调**（注册后、`pinctrl_enable()` 前后都行） |
| `pinctrl_generic_remove_group(pctldev, selector)` | 从 radix tree 删一个 group | 返回 `int`。全树几乎无人用；`pinctrl_unregister()` 会自动清空整棵树 |
| `pinctrl_get_group_selector(pctldev, pin_group)` | 按名字反查 selector | 返回 `int`，找不到返回 `-EINVAL`。**不导出**，只在核心内部用（`pinmux_select` debugfs 写入路径） |

`pinctrl_generic_add_group()` 的返回值是 **selector 而不是 0**。写成 `if (ret) return ret;` 会把第 1 个之后的所有 group 都当成失败。正确写法见 `05-usage.md` §2.4。

`struct group_desc`（`drivers/pinctrl/core.h`）：

```c
struct group_desc {
        const char *name;
        const unsigned int *pins;
        int num_pins;
        void *data;            /* 驱动私有，核心完全不碰 */
};
```

### §5.2 function 一族（`GENERIC_PINMUX_FUNCTIONS`）

| API | 说明 | 备注 |
|---|---|---|
| `pinmux_generic_get_function_count(pctldev)` | 返回已注册 function 数 | 返回 `int`。可直接赋给 `pinmux_ops.get_functions_count` |
| `pinmux_generic_get_function_name(pctldev, selector)` | 按 selector 返回 function 名 | 返回 `const char *`。可直接赋给 `pinmux_ops.get_function_name` |
| `pinmux_generic_get_function_groups(pctldev, selector, groups, num_groups)` | 按 selector 返回它作用的 group 名数组 | 返回 `int`。可直接赋给 `pinmux_ops.get_function_groups`。失败时 `dev_err("%s could not find function%i")` + 返回 `-EINVAL` |
| `pinmux_generic_get_function(pctldev, selector)` | 取出整个 `struct function_desc` | 返回 `struct function_desc *` 或 NULL。给驱动在 `set_mux` 里取回 function 私有数据 |
| `pinmux_generic_add_function(pctldev, name, groups, num_groups, data)` | 往 radix tree 里加一个 function | 返回 `int`（selector，≥0）。**重名时不报错**：直接返回已存在那个的 selector（幂等）。内部 `devm_kzalloc` |
| `pinmux_generic_remove_function(pctldev, selector)` | 删一个 function | 返回 `int`，selector 不存在返回 `-ENOENT` |

`struct function_desc`（`drivers/pinctrl/pinmux.h`）：

```c
struct function_desc {
        const char *name;
        const char **group_names;
        int num_group_names;
        void *data;            /* 驱动私有 */
};
```

**`add_group` 和 `add_function` 的重名行为不一样**：`pinmux_generic_add_function()` 遇到重名直接返回已有 selector；`pinctrl_generic_add_group()` **不查重**，会插入第二个同名 group（后续按名字查会命中第一个）。这条差异在两处 kerneldoc 里都没写，是读代码得出的——见 `99-evidence.md` §4。

---

## §6 三层 ops 回调契约（pin controller 驱动必读）

### §6.1 `struct pinctrl_ops`

> 头文件：`include/linux/pinctrl/pinctrl.h`；校验：`drivers/pinctrl/core.c` 的 `pinctrl_check_ops()`

| 回调 | 必需？ | 说明 |
|---|---|---|
| `get_groups_count(pctldev)` | **必需** | 返回 group 总数。`pinctrl_check_ops()` 检查它为 NULL 就 `-EINVAL` |
| `get_group_name(pctldev, selector)` | **必需** | 返回第 selector 个 group 的名字 |
| `get_group_pins(pctldev, selector, pins, num_pins)` | 可选 | 返回 group 对应的 pin 数组。**核心不检查它是否为 NULL**，但 debugfs 的 `pingroups` 文件和 `pinctrl_get_group_pins()` 都要用它 |
| `pin_dbg_show(pctldev, s, offset)` | 可选 | debugfs `pins` 文件里每行末尾追加驱动自己的信息 |
| `dt_node_to_map(pctldev, np_config, map, num_maps)` | DT 平台**实际必需** | 把一个 DT 状态节点翻译成 `pinctrl_map` 数组。kerneldoc 说 optional（针对不支持 DT 的驱动）。arm64 上不实现它 = DT 里的状态节点全部无效 |
| `dt_free_map(pctldev, map, num_maps)` | 与上一个配对 | 释放 `dt_node_to_map` 分配的一切。绝大多数驱动直接填 `pinctrl_utils_free_map` |

**`pinctrl_check_ops()` 实际检查的三项**（`drivers/pinctrl/core.c`）：`pctlops` 非空、`get_groups_count` 非空、`get_group_name` 非空。任一不满足 → `dev_err` + `-EINVAL`，注册失败。

### §6.2 `struct pinmux_ops`

> 头文件：`include/linux/pinctrl/pinmux.h`；校验：`drivers/pinctrl/pinmux.c` 的 `pinmux_check_ops()`

| 回调 | 必需？ | 说明 |
|---|---|---|
| `get_functions_count(pctldev)` | **必需** | 返回 function 总数 |
| `get_function_name(pctldev, selector)` | **必需** | 返回第 selector 个 function 的名字。`pinmux_check_ops()` **会遍历每一个 selector 挨个调它**，任何一个返回 NULL 就 `dev_err("pinmux ops has no name for function%u")` + `-EINVAL` |
| `get_function_groups(pctldev, selector, groups, num_groups)` | **必需** | 返回该 function 能作用于哪些 group |
| `set_mux(pctldev, func_selector, group_selector)` | **必需** | **真正写 MUX 寄存器**。返回 `int` |
| `request(pctldev, offset)` | 可选 | 核心在占用某个 pin 之前问一句"这根 pin 能拿来复用吗"。驱动可以返回负错误码拒绝 |
| `free(pctldev, offset)` | 可选 | `request` 的反向 |
| `gpio_request_enable(pctldev, range, offset)` | 可选 | 把某根 pin 单独切成 GPIO。kerneldoc 明确：**只有能把每根 pin 单独切成 GPIO 时才实现它** |
| `gpio_disable_free(pctldev, range, offset)` | 可选 | 上一个的反向 |
| `gpio_set_direction(pctldev, range, offset, input)` | 可选 | GPIO 方向变化时通知驱动（有的 SoC 输入/输出要配不同寄存器）。**不实现时 `pinctrl_gpio_direction_*()` 返回 0** |
| `strict`（bool 字段，不是回调） | 默认 false | true = **禁止**同一根 pin 同时被 GPIO 和普通 function 占用。`pin_request()` 会同时严格检查 `gpio_owner` 和 `mux_owner` |

**官方文档的断言与代码一致**：`Documentation/driver-api/pin-control.rst` 明确写 "All the above functions are mandatory to implement for a pinmux driver"，指的就是上面标 的四个。

**没有 `gpio_request_enable` 时的退化行为**：核心会去找一个名字叫 `"gpioN"`（N 是 pin 号）的 **function**，把它当成"GPIO 模式"来 `set_mux`。这条退化路径在 `pin-control.rst` 里有说明，容易被忽略——它意味着你也可以完全不实现 GPIO 相关回调，只要在 function 表里放一堆 `gpio0`/`gpio1`/… 就行。

**`strict` 的选择判据**（取决于你的硬件是哪种形态，图见 `08-feynman.md` §4）：

| 硬件形态 | `strict` 该填 | 理由 |
|---|---|---|
| GPIO 挂在 MUX 之后（GPIO 是 MUX 的一路输入） | `true` | 物理上 GPIO 和外设**互斥**，同时占用是错误 |
| GPIO 从 pad 直连（旁路 MUX） | `false` | 物理上可以"外设在用，同时用 GPIO 读这根线的电平"（调试常用） |

### §6.3 `struct pinconf_ops`

> 头文件：`include/linux/pinctrl/pinconf.h`；校验：`drivers/pinctrl/pinconf.c` 的 `pinconf_check_ops()`

| 回调 | 必需？ | 说明 |
|---|---|---|
| `pin_config_set(pctldev, pin, configs, num_configs)` | **二选一** | 对单个 pin 施加一批配置。注意签名是**数组 + 个数**，不是单个 config |
| `pin_config_group_set(pctldev, selector, configs, num_configs)` | **二选一** | 对整个 group 施加一批配置 |
| `pin_config_get(pctldev, pin, config)` | 可选 | 读回单个 pin 的某项配置。kerneldoc 规定的返回值语义：**不支持该项返回 `-ENOTSUPP`；支持但当前禁用返回 `-EINVAL`** |
| `pin_config_group_get(pctldev, selector, config)` | 可选 | 同上，group 粒度。返回值语义同上 |
| `pin_config_dbg_show(pctldev, s, offset)` | 可选 | debugfs `pinconf-pins` 里追加驱动信息 |
| `pin_config_group_dbg_show(pctldev, s, selector)` | 可选 | debugfs `pinconf-groups` 里追加 |
| `pin_config_config_dbg_show(pctldev, s, config)` | 可选 | 解码并显示一个 config 值 |
| `is_generic`（bool 字段，`#ifdef CONFIG_GENERIC_PINCONF`） | 默认 false | true = 告诉核心"我用的是通用 `PIN_CONFIG_*` 编码"，于是 debugfs 能用核心的解码器打印 |

**`pinconf_check_ops()` 的判据是"`pin_config_set` 和 `pin_config_group_set` 至少有一个"**，两个都没有才 `-EINVAL`。所以存在只支持 group 粒度配置的控制器——`pinctrl-msm.c` 的 `msm_pinconf_ops` 就是这种（只填了 group get/set 两项）。

**这里有一处官方文档滞后于代码**：`pin-control.rst` 里的驱动示例把 `pin_config_set` 写成 `foo_pin_config_set(pctldev, offset, unsigned long config)`——**单个 config**。当前代码的签名是 `unsigned long *configs, unsigned num_configs`。照文档抄会编译不过。以代码为准，登记在 `99-evidence.md` §2.3。

---

## §7 pinconf-generic：标准 DT 属性的翻译层

> 头文件：`include/linux/pinctrl/pinconf-generic.h`；实现：`drivers/pinctrl/pinconf-generic.c`（需 `CONFIG_GENERIC_PINCONF`）

这一层的价值：**驱动一行 DT 解析代码都不用写**，直接把核心提供的 `pinconf_generic_dt_node_to_map_*` 赋给 `pinctrl_ops.dt_node_to_map` 即可。完整机制见 `04-extensions.md` §2。

### §7.1 三个现成的 `dt_node_to_map` 实现

| API | 说明 | 备注 |
|---|---|---|
| `pinconf_generic_dt_node_to_map_group(pctldev, np_config, map, num_maps)` | 生成的 config map 是 **group 粒度**（`PIN_MAP_TYPE_CONFIGS_GROUP`） | `static inline`，包装 `pinconf_generic_dt_node_to_map()`。配 `pinconf_ops.pin_config_group_set` 用 |
| `pinconf_generic_dt_node_to_map_pin(...)` | 生成的 config map 是 **pin 粒度**（`PIN_MAP_TYPE_CONFIGS_PIN`） | `static inline`。配 `pin_config_set` 用 |
| `pinconf_generic_dt_node_to_map_all(...)` | 让核心自己按节点里写的是 `pins` 还是 `groups` 来决定粒度 | `static inline`。最省心，但要求两种 set 回调都实现 |
| `pinconf_generic_dt_node_to_map(pctldev, np_config, map, reserved_maps, num_maps, type)` | 上面三个的底层实现 | 直接调它也行，`type` 手工传。会自动递归处理**嵌套子节点** |
| `pinconf_generic_dt_subnode_to_map(pctldev, np, map, reserved_maps, num_maps, type)` | 只处理一个子节点 | 一般不直接调 |
| `pinconf_generic_dt_free_map(pctldev, map, num_maps)` | 配套的 `dt_free_map` | 内部就是调 `pinctrl_utils_free_map()` |
| `pinconf_generic_parse_dt_config(np, pctldev, configs, nconfigs)` | 只解析配置属性、不生成 map | 给自己写 `dt_node_to_map` 但想复用属性解析的驱动用 |
| `pinconf_generic_dump_config(pctldev, s, config)` | 把一个 config 值解码成人类可读文本 | 给 debugfs 用 |

**`_group` / `_pin` / `_all` 三选一的判据是"你实现了哪个 set 回调"**，不是"你的 DT 里写的是 pins 还是 groups"。选错的后果：DT 里写了配置属性、`dt_node_to_map` 生成了 group 粒度的 map，但驱动只实现了 `pin_config_set` → `pinconf_apply_setting()` 找不到对应回调，`dev_err` 之后配置**静默不生效**。诊断见 `07-debug.md` §5。

### §7.2 `enum pin_config_param`：25 个标准参数

`include/linux/pinctrl/pinconf-generic.h` 里 `enum pin_config_param` 共 **25 个实际参数** + 2 个哨兵：

| 分组 | 参数 |
|---|---|
| **偏置（bias）** 6 个 | `BIAS_BUS_HOLD`、`BIAS_DISABLE`、`BIAS_HIGH_IMPEDANCE`、`BIAS_PULL_DOWN`、`BIAS_PULL_PIN_DEFAULT`、`BIAS_PULL_UP` |
| **驱动方式（drive）** 5 个 | `DRIVE_OPEN_DRAIN`、`DRIVE_OPEN_SOURCE`、`DRIVE_PUSH_PULL`、`DRIVE_STRENGTH`、`DRIVE_STRENGTH_UA` |
| **输入** 4 个 | `INPUT_DEBOUNCE`、`INPUT_ENABLE`、`INPUT_SCHMITT`、`INPUT_SCHMITT_ENABLE` |
| **输出** 4 个 | `OUTPUT`、`OUTPUT_ENABLE`、`OUTPUT_IMPEDANCE_OHMS`、`SLEW_RATE` |
| **模式** 2 个 | `MODE_LOW_POWER`、`MODE_PWM` |
| **杂项** 4 个 | `PERSIST_STATE`、`POWER_SOURCE`、`SKEW_DELAY`、`SLEEP_HARDWARE_STATE` |
| **哨兵** | `PIN_CONFIG_END = 0x7F`（厂商私有参数从 `PIN_CONFIG_END + 1` 开始编号）、`PIN_CONFIG_MAX = 0xFF` |

**`PIN_CONFIG_END = 0x7F` 是厂商扩展的分界线**（`PIN_CONFIG_MAX = 0xFF`）：kerneldoc 原文是"if you need to pass in custom configurations to the pin controller, use `PIN_CONFIG_END+1` as the base offset"。厂商私有参数通过 `pinctrl_desc.custom_params` 注册，见 `04-extensions.md` §3.8。

### §7.3 config 值的打包与解包

一个 config 是 `unsigned long`，**低 8 位是参数编号、高位是参数值**：

| API / 宏 | 说明 | 备注 |
|---|---|---|
| `PIN_CONF_PACKED(p, a)` | 宏：把参数 `p` 和实参 `a` 打包成一个 `unsigned long` | 展开成 `((a << 8) \| ((unsigned long) p & 0xffUL))` |
| `pinconf_to_config_packed(param, argument)` | 上面那个宏的 `static inline` 版本 | 新代码用这个，类型更安全 |
| `pinconf_to_config_param(config)` | 从打包值里取出参数编号 | `static inline`，返回 `enum pin_config_param` |
| `pinconf_to_config_argument(config)` | 从打包值里取出实参 | `static inline`，返回 `u32` |
| `PCONFDUMP(a, b, c, d)` | 宏：构造一个 `struct pin_config_item`（debugfs 显示用） | 四个参数分别是 param、显示名、单位、是否有实参 |

**参数编号只有 8 位**（`& 0xffUL`），这就是 `PIN_CONFIG_MAX = 0xFF` 的来由。实参有 `sizeof(unsigned long) * 8 - 8` 位——arm64 上是 56 位，足够放任何频率/电阻值。

**布尔型参数（如 `BIAS_DISABLE`）的实参约定**：DT 侧写 `bias-disable;`（无值），核心把实参填 1。`drivers/pinctrl/pinconf-generic.c` 的 `dt_params[]` 表里每一项都标了"是否需要读取属性值"。

---

## §8 pinctrl-utils：写 `dt_node_to_map` 的辅助函数

> 头文件：`drivers/pinctrl/pinctrl-utils.h`（**私有头**）；实现：`drivers/pinctrl/pinctrl-utils.c`

和 §5 一样：**导出了但只在私有头里声明**，所以只有 `drivers/pinctrl/` 下的驱动能用。用途是让自己写 `dt_node_to_map` 的驱动不必手工管 map 数组的增长。

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_utils_reserve_map(pctldev, map, reserved_maps, num_maps, reserve)` | 确保 map 数组至少还能再放 `reserve` 条，不够就 `krealloc_array` 扩容 | 返回 `int`。失败时 `dev_err("krealloc(map) failed")` + `-ENOMEM`。**必须先 reserve 再 add**，否则下面两个函数会 `WARN_ON` |
| `pinctrl_utils_add_map_mux(pctldev, map, reserved_maps, num_maps, group, function)` | 追加一条 `PIN_MAP_TYPE_MUX_GROUP` | 返回 `int`。容量不够时 `WARN_ON(*num_maps == *reserved_maps)` + `-ENOSPC` |
| `pinctrl_utils_add_map_configs(pctldev, map, reserved_maps, num_maps, group, configs, num_configs, type)` | 追加一条配置 map | 返回 `int`。**内部 `kmemdup` 复制整个 configs 数组**，所以调用者的临时数组可以立即释放 |
| `pinctrl_utils_add_config(pctldev, configs, num_configs, config)` | 往一个 configs 数组里追加一条 config（数组本身也自动扩容） | 返回 `int` |
| `pinctrl_utils_free_map(pctldev, map, num_maps)` | 释放整个 map 数组 | 返回 `void`。**只释放 `CONFIGS_GROUP` / `CONFIGS_PIN` 两种 type 的 `configs` 子数组**，再释放 map 本身。MUX type 的 map 里没有需要单独释放的东西。**绝大多数驱动直接把它填进 `pinctrl_ops.dt_free_map`** |

**配对纪律**：`pinctrl_utils_add_map_configs()` 做了 `kmemdup`，所以只能用 `pinctrl_utils_free_map()` 释放；反过来，如果你在 `dt_node_to_map` 里自己 `kmalloc` 了 configs 又用 `pinctrl_utils_free_map()` 释放，也是对的（它只 `kfree`）。**混用会漏内存的场景是"自己分配了 map 数组但用别的方式释放"**。

---

## §9 `#pinctrl-cells` 风格驱动的两个辅助函数

> 头文件：`drivers/pinctrl/devicetree.h`（**私有头**）；实现：`drivers/pinctrl/devicetree.c`

| API | 说明 | 备注 |
|---|---|---|
| `pinctrl_count_index_with_args(np, list_name)` | 数一个"索引 + N 个 u32"格式的属性里有几条记录 | 返回 `int`；属性不存在或读不到 `#pinctrl-cells` 时返回 `-ENOENT`。`np` 必须是**父 pin controller 节点** |
| `pinctrl_parse_index_with_args(np, list_name, index, out_args)` | 取出第 `index` 条记录，填进 `struct of_phandle_args` | 返回 `int`；`index` 越界返回 `-EINVAL`。`out_args->args_count` = `#pinctrl-cells` + 1（第一个 cell 永远是控制器内的索引） |

**这两个是给"一 pin 一寄存器 + 自定义 cells"风格的驱动准备的**，全树只有 2 个使用者：

| 驱动 | 用在哪 |
|---|---|
| `drivers/pinctrl/pinctrl-single.c` | 解析 `pinctrl-single,pins` / `pinctrl-single,bits` |
| `drivers/pinctrl/ti/pinctrl-ti-iodelay.c` | 解析 `pinctrl-single,pins` 风格的 iodelay 配置 |

`#pinctrl-cells` 属性本身由 `drivers/pinctrl/devicetree.c` 的 `pinctrl_find_cells_size()` 读取，写在**控制器节点**上。见 `06-devicetree.md` §4。

---

## §10 同名文件与近邻符号

### §10.1 四对同名头文件

pinctrl 是全内核同名头文件最密集的子系统之一。`#include` 写错不会报错，只会让你找不到自己要的定义：

| 名字 | 公开版（`include/linux/pinctrl/`） | 私有版（`drivers/pinctrl/`） |
|---|---|---|
| `pinctrl.h` | 驱动侧 API：`pinctrl_desc`、`pinctrl_ops`、注册函数、GPIO range | 不存在同名私有版；私有版叫 `core.h` |
| `pinmux.h` | **只有** `struct pinmux_ops` 一个定义（85 行） | 复用层内部接口 + `struct function_desc` + `pinmux_generic_*` 声明 |
| `pinconf.h` | **只有** `struct pinconf_ops` 一个定义（67 行） | 配置层内部接口 + `!CONFIG_PINCONF` stub |
| `pinconf-generic.h` | `enum pin_config_param` + 打包宏 + `pinconf_generic_dt_*` | 不存在同名私有版 |

**判据**：写 pin controller 驱动时你只该 `#include <linux/pinctrl/*.h>`；只有住在 `drivers/pinctrl/` 下、需要泛型 group/function 存储或 pinctrl-utils 的驱动，才额外 `#include "core.h"` / `"pinmux.h"` / `"pinctrl-utils.h"`。

### §10.2 近邻符号

完整的"错用会怎样"讨论在 `05-usage.md` §6；这里只列对照，方便查表：

| 一组 | 区别 |
|---|---|
| `struct pin_desc` vs `struct pinctrl_pin_desc` | 前者是**核心内部**运行时状态（`mux_owner`/`gpio_owner`/`mux_usecount`，在私有 `core.h`）；后者是**驱动声明** pin 用的（`number`/`name`/`drv_data`，在公开 `pinctrl.h`）。驱动只填后者 |
| `struct pingroup` vs `struct group_desc` | 前者是公开的 group 字面量类型（`pinctrl.h`）；后者是**泛型存储**的表项（私有 `core.h`，多一个 `data` 字段）。两者互不转换 |
| `struct pinfunction` vs `struct function_desc` | 同上关系：公开字面量 vs 泛型存储表项 |
| `pinctrl_register()` vs `pinctrl_register_and_init()` | 前者一步到位但 kerneldoc 自陈已废弃；后者分两步、要配 `pinctrl_enable()` |
| `pinctrl_get()` vs `devm_pinctrl_get()` | **返回值不能交叉释放**：前者配 `pinctrl_put()`，后者配 `devm_pinctrl_put()`（或什么都不配） |
| `pinctrl_select_default_state()` vs `pinctrl_pm_select_default_state()` | 功能相同；后者在 `!CONFIG_PM` 时是返回 0 的 inline stub。两者都用 `dev->pins` 缓存，都不重新 lookup |
| `pinctrl_dev_get_name()` vs `pinctrl_dev_get_devname()` | 前者取 `desc->name`；后者取底层 platform 设备的 `dev_name()`。**两者通常不同** |
| `pinctrl_find_gpio_range_from_pin()` vs `..._nolock()` | 后者要求调用者已持 `pctldev->mutex`。选哪个取决于**你在哪个回调里**：GPIO 相关回调（`gpio_request_enable` / `gpio_disable_free` / `gpio_set_direction`）、`pin_config_group_get()`、各 `*_dbg_show` 是在持锁状态下被调的，必须用 `_nolock` 版；`set_mux` / `request` / `free` / `pin_config_set` 等状态应用路径上的回调不持锁，用带锁版。完整分组见 §12 |
| `pinconf_generic_dt_node_to_map_group` / `_pin` / `_all` | 决定生成 group 粒度还是 pin 粒度的 config map。**选错 = 配置静默不生效** |
| `PIN_CONFIG_DRIVE_STRENGTH` vs `PIN_CONFIG_DRIVE_STRENGTH_UA` | 前者单位由驱动自定（通常 mA 或档位号）；后者明确是微安。DT 侧分别对应 `drive-strength` 和 `drive-strength-microamp` |
| `PIN_CONFIG_INPUT_SCHMITT` vs `PIN_CONFIG_INPUT_SCHMITT_ENABLE` | 前者带阈值实参；后者只是开关 |
| `PIN_CONFIG_OUTPUT` vs `PIN_CONFIG_OUTPUT_ENABLE` | 前者"配成输出**并且**驱动指定电平"；后者只是"使能输出模式"，不指定电平 |

---

## §11 错误码语义

### §11.1 消费者会看到的错误码

| 错误码 | 含义 | 谁产生 |
|---|---|---|
| `-EPROBE_DEFER` | **不是错误**，是"控制器还没就绪，稍后重试"。设备核心会自动重新 probe | 三个来源：① `add_setting()` 遇到未注册的控制器名（`core.c`）；② `dt_to_map_one_config()` 的 phandle 指向的控制器还没注册（`devicetree.c`）；③ `pinctrl_get_device_gpio_range()` / `pinctrl_find_and_add_gpio_range()` 找不到 GPIO range（`core.c`） |
| `-ENODEV` | 找不到对象。**语义分两种**：① `pinctrl_lookup_state()` 里 = 状态名不存在；② `add_setting()` 里 = 这是一条 hog 映射但控制器还没准备好认领 | `core.c`、`devicetree.c` |
| `-EINVAL` | **本子系统最泛滥的错误码**（核心 6 个文件里共 48 处）。语义见 §11.2 分组 | 全部核心文件 |
| `-ENOMEM` | 分配失败（句柄、setting、map 数组、configs 数组、radix tree 节点）。另有一处特殊：`pinmux_select()` debugfs 写入超过 `PINMUX_SELECT_MAX`（128 字节）也返回 `-ENOMEM`，不是 `-E2BIG` | `core.c` 11 处、`pinmux.c` 4 处、`devicetree.c` 3 处、`pinconf-generic.c` 2 处、`pinctrl-utils.c` 3 处 |
| `-ENOTSUPP` | 该配置项这个控制器不支持。**kerneldoc 规定的区分**：`-ENOTSUPP` = 不支持这一项；`-EINVAL` = 支持但当前处于禁用状态 | `pinconf.c` 3 处（`confops` 为 NULL、`pin_config_get` 未实现、`pin_config_group_get` 未实现） |
| `-ENOENT` | ① `pinmux_generic_remove_function()` selector 不存在；② `pinctrl_generic_remove_group()` selector 不存在；③ `pinctrl_count_index_with_args()` 属性或 `#pinctrl-cells` 缺失 | `pinmux.c`、`core.c`、`devicetree.c` |
| `-ENOSPC` | map 数组容量不够就调了 `add`（忘了先 `reserve`）。**伴随 `WARN_ON`** | `pinctrl-utils.c` 2 处 |

### §11.2 `-EINVAL` 的七组语义

48 处 `-EINVAL` 不是一件事。**按"你在哪一步拿到它"分组**才能定位：

| 组 | 触发时机 | 典型来源 | 排障入手点 |
|---|---|---|---|
| **1. ops 契约不满足** | 控制器注册时 | `pinctrl_check_ops()`：`pctlops`/`get_groups_count`/`get_group_name` 缺一；`pinmux_check_ops()`：四个必需回调缺一，或某个 function 没名字；`pinconf_check_ops()`：两个 set 回调都没有 | 看 `dev_err` 具体说了哪个回调；见 §6 |
| **2. desc 本身不合法** | 控制器注册时 | `pinctrl_init_controller()`：`pctldesc` 为 NULL 或 `pctldesc->name` 为 NULL | 检查 `struct pinctrl_desc` 有没有填 `name` |
| **3. pin 表问题** | 控制器注册时 | `pinctrl_register_one_pin()`：pin 编号重复；`pin_get_name()`/`pinctrl_get_group_pins()`：pin 或 group 不存在 | pin 表有重号 |
| **4. mapping 表不合法** | mapping 注册时 / DT 解析后 | `pinctrl_register_mappings()` 的校验 switch（4 处）、`pinmux_validate_map()`、`pinconf_validate_map()` | map 的 `type` 是 `INVALID`、`ctrl_dev_name` 为空、`data` 字段与 type 不匹配 |
| **5. 引脚被别人占了** | `pinctrl_select_state()` 时 | `pin_request()` 的两道冲突门（`status` 初值即 `-EINVAL`，见 `03-internals.md` §A11.2 的真值表），以及 `try_module_get()` 失败、`ops->request()` 回调失败 | **不是 `-EBUSY`**。看 `dev_err "pin %s already requested by %s; cannot claim for %s"`；同时读 debugfs 的 `pinmux-pins` 看当前 owner |
| **6. 名字查不到 selector** | 状态生效时 / debugfs 写入时 | `pinmux_func_name_to_selector()` 返回 `-EINVAL`（function 名不在控制器的 function 表里）；`pinctrl_get_group_selector()` 同理；`pinmux_map_to_setting()` 里 group 不属于该 function | DTS 里 `function = "xxx"` 或组名写错了。看 `dev_err "invalid function %s in map table"` / `"invalid group \"%s\" for function \"%s\""` |
| **7. `commit_state` 里的 setting type 非法** | 状态切换时 | `pinctrl_commit_state()` 两遍应用循环各有一处 `default: ret = -EINVAL`（拆旧状态那一遍用的是 `if ... continue`，没有 default 分支） | 理论上不该发生（type 在注册时已校验过）；出现说明内存被踩 |

**`pinctrl_commit_state()` 分两遍走**：第一遍只应用 `MUX_GROUP` 类 setting，第二遍才应用 `CONFIGS_*` 类。所以"复用生效了但电气配置没生效"和反过来是两种不同的失败，看 `dev_err` 是从 `pinmux_enable_setting()` 出来的还是 `pinconf_apply_setting()` 出来的即可区分。分诊表见 `07-debug.md` §5。

### §11.3 给驱动作者的三条实操建议

1. **`request` 回调拒绝时返回什么都行，但要记得核心会打印 `"request() failed for pin %d"`**。核心不改写你的错误码，会原样传给 `pinctrl_select_state()` 的调用者
2. **`pin_config_get` 的两个码必须分清**：不支持该项 → `-ENOTSUPP`；支持但当前禁用 → `-EINVAL`。这不是风格问题：`pinconf-generic.c` 的 debugfs 显示代码把这两个码**当成同一种情况**跳过（`if (ret == -EINVAL || ret == -ENOTSUPP) continue;`），但通用配置读取路径会区分
3. **不要在 ops 回调里返回 `-EPROBE_DEFER`**。核心不区分"哪一层返回的 defer"，会一路抛给设备核心，导致消费者驱动被无限重新 probe

---

## §12 上下文速查：在这里能调什么

| 你在哪 | 能用 | 不能用 | 理由 |
|---|---|---|---|
| **消费者驱动 `probe()`** | 全部消费者 API | — | 可睡眠，锁都能拿 |
| **消费者驱动 `remove()`** | 全部消费者 API | — | 同上 |
| **`.suspend()` / `.resume()`** | `pinctrl_pm_select_*_state()`、`pinctrl_select_state()` | — | 系统 PM 回调在进程上下文，可睡眠 |
| **`pm_runtime_suspend()` / `_resume()`** | 同上 | — | runtime PM 回调也可睡眠 |
| **中断处理函数（硬 irq）** | **一个都不能用** | 全部 | 核心全用 `mutex`，没有 spinlock 变体。而且部分 pin controller 的寄存器在 I2C PMIC 后面，`select_state` 可能睡很久 |
| **软中断 / tasklet** | 一个都不能用 | 全部 | 同上 |
| **原子上下文（持 spinlock）** | 一个都不能用 | 全部 | 同上 |
| **ops 回调内（`set_mux` / `request` / `free` / `pin_config_set` / `dt_node_to_map`）** | `pinctrl_dev_get_drvdata()`、`pinctrl_generic_get_group()`、`pinmux_generic_get_function()`、`pinctrl_find_gpio_range_from_pin()`（**带锁版**） | — | **这些回调是在不持 `pctldev->mutex` 的情况下被调的**（状态应用路径全程无锁，见 `03-internals.md` §B1），所以带锁版函数可以安全调用 |
| **ops 回调内（`gpio_request_enable` / `gpio_disable_free` / `gpio_set_direction` / `pin_config_group_get` / 各 `*_dbg_show`）** | `pinctrl_dev_get_drvdata()`、`pinctrl_find_gpio_range_from_pin_nolock()`（**无锁版**） | `pinctrl_find_gpio_range_from_pin()`、`pinctrl_gpio_*()` 等会再拿 `pctldev->mutex` 的函数 | 这一组回调的调用者（`pinctrl_gpio_request()` 等、`pin_config_group_get()`、debugfs show）**已持 `pctldev->mutex`**，再拿一次就死锁 |
| **pin controller 驱动 `probe()`，注册之后** | `pinctrl_generic_add_group()`、`pinmux_generic_add_function()`、`pinctrl_enable()` | — | 走 `*_and_init` 变体时的正常初始化窗口 |
| **ops 回调内（`dt_free_map`）** | `pinctrl_utils_free_map()` 等纯内存释放 | 任何会回到 pinctrl 核心的 API | 不持 `pctldev->mutex`，但经 `pinctrl_free()` 这条路时**持全局 `pinctrl_list_mutex`**（`core.c:1168` → `devicetree.c:41-43`）。见 `03-internals.md` §B1.2 |
| **gpiolib 驱动的 `.request` / `.direction_*`** | 6 个 `pinctrl_gpio_*` 桥接函数 | 消费者四件套 | 桥接函数内部会拿 `pctldev->mutex`，所以 gpiolib 的这几个回调也必须是可睡眠的（gpiolib 保证了这一点） |
| **gpiolib 驱动的 `.set` / `.get`（快路径）** | 一个都不能用 | 全部 | 这两个回调可能在原子上下文被调 |
| **关机路径 `shutdown()`** | `pinctrl_select_state()` | — | 仍是进程上下文 |
| **`late_initcall` / 板级初始化** | `pinctrl_register_mappings()`、`pinctrl_provide_dummies()` | — | `pinctrl_provide_dummies()` 必须在**任何消费者 probe 之前**调，否则来不及生效 |

**一句话记住**：**pinctrl 全子系统只能在可睡眠上下文使用**。官方文档特意提醒过：`pinctrl_select_state()` 名义上是"快路径"，但 pin controller 的寄存器可能挂在慢速总线（如 I2C 上的 PMIC）后面，所以不能假设它很快返回。
