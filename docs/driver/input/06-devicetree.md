---
title: 设备树
---

# input 子系统：设备树怎么写

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 前置阅读：`01-api.md` §0（代码地图）、`05-usage.md` §1–§4（对应角色的驱动骨架）。

## §0 先分诊：input 设备根本不止一条发现路径

**不要一上来就问「input 有没有 DT 绑定」。** input 是框架型子系统，`struct input_dev` 本身**不是** `struct device` 层面的可发现对象——它没有自己的 `bus_type`，也没有任何一行代码去匹配 `compatible`。真正被 DT 发现的是**承载它的那个设备**（platform / i2c / spi 设备），input 设备是那个驱动 probe 时**顺带造出来的**。

所以「DT 怎么写」这个问题在本子系统里要拆成两半：**父总线那一半**（`compatible` / `reg` / `interrupts` / `*-supply`，由 of / i2c / spi 核心消费，不是本篇内容）和 **input 相关的那一半**（`linux,code` / `touchscreen-*` / `keypad,*`，本篇内容）。

### §0.1 发现路径分诊表

| 发现路径 | 谁扫描 | 走 DT 吗 | arm64 用不用 | 本篇覆盖到哪 |
| --- | --- | --- | --- | --- |
| DT 节点 → platform 设备 → 驱动 probe | `of_platform_populate()`（of 核心） | 是 | ★ **主路径** | 全覆盖 |
| DT 子节点 → i2c 设备 → 驱动 probe | i2c 核心遍历总线节点的子节点 | 是 | ★ **主路径**（触摸屏几乎全是 i2c） | 全覆盖 |
| DT 子节点 → spi 设备 → 驱动 probe | spi 核心 | 是 | 用（少） | 全覆盖 |
| ACPI 表 → i2c/platform 设备 | ACPI 核心 | 否（走 `acpi_device_id`） | ✗ **不涉及**（x86 平板/笔记本路径） | 不覆盖；全树 `grep -rl 'acpi_device_id' drivers/input/` → 17 个文件，裁剪理由记在 `99-evidence.md` §2.7 |
| 板级 `platform_data`（无 DT 的老板子） | 板文件里的 `platform_device_register()` | 否 | ✗ arm64 没有板文件 | §3.4 讲它和 DT 的**互斥**关系 |
| serio 总线自动探测（i8042 → AT 键盘 / PS2 鼠标） | `i8042` 探测 + `serio` 总线匹配 | 否 | ✗ **编译不出来**（见 §0.2） | 不覆盖，见 `04-extensions.md` §2 |
| USB / 蓝牙 HID | `drivers/hid/`，不在本子系统 | 否 | 用（外接键鼠） | 不覆盖 |
| uinput（用户态造设备） | 用户态 ioctl，见 `05-usage.md` §10 | 否 | 用 | 不覆盖（和 DT 完全无关） |
| handler 侧（evdev / joydev / mousedev / input-leds …） | `input_register_handler()`，纯软件 | 否 | 用 | **永远不涉及 DT**，见 §0.3 |

### §0.2 ★ arm64 上 serio / i8042 不是「一般不用」，而是编译不出来

```bash
grep -rn 'ARCH_MIGHT_HAVE_PC_SERIO' arch/arm64/ arch/arm/
```

输出只有一行 `arch/arm/mach-footbridge/Kconfig:60`。`CONFIG_SERIO_I8042` 的 `depends on ARCH_MIGHT_HAVE_PC_SERIO`（`drivers/input/serio/Kconfig:32`）在 arm64 上永远不满足，所以 AT 键盘 / PS2 鼠标那一整条路径在本目标架构上不存在。

`drivers/input/serio/` 里有 DT 绑定的是另外 7 个 SoC 内置 PS/2 控制器：

```bash
grep -rln 'of_match_table' drivers/input/serio/*.c
```

→ `altera_ps2.c` `apbps2.c` `arc_ps2.c` `olpc_apsp.c` `ps2-gpio.c` `sun4i-ps2.c` `xilinx_ps2.c`（7 个）。arm64 DTS 里一个都没有：

```bash
grep -rln 'i8042\|serio' arch/arm64/boot/dts/     # 空
```

### §0.3 ★ handler 侧永远没有 DT

`evdev` / `joydev` / `mousedev` / `input-leds` / `evbug` / `apm-power` 这些 handler（`04-extensions.md` §1、§9、§13）都是 `module_init` 里一句 `input_register_handler()` 就完事，**没有 `compatible`、没有节点、没有属性**。想控制哪个 handler 加载，只有内核配置和模块黑名单两条路，DT 里加什么都没用。

### §0.4 按任务找章节

| 我要…… | 去 |
| --- | --- |
| 弄清 input **核心**到底读哪几个属性 | §1.1（一共 14 个，一张表） |
| 写一个 GPIO 按键节点 | §2 |
| 写一个触摸屏节点 / 屏方向不对 | §3 |
| 写一个矩阵键盘的 `linux,keymap` | §4 |
| 弄清 `touchscreen-size-x` 是最大值还是分辨率 | §3.2（★ 是分辨率，核心会减 1） |
| 弄清 `KEY()` / `MATRIX_KEY()` 的位怎么排 | §4.2 |
| 写 RMI4（Synaptics）触摸板节点 | §5 |
| 查一个属性到底有没有人读 | §6（含一个确认无人读的死字） |
| 属性写了但没生效、也没有任何报错 | §7（静默失效决策树） |
| 拿到一条内核报错想反查原因 | §8.4（错误消息 → 原因表） |

---

## §1 谁消费什么

### §1.1 ★ input 核心真正会读的属性只有 14 个

```bash
grep -nE 'device_property|of_property|fwnode_property' drivers/input/*.c
```

这一条命令的全部输出落在两个文件里：`drivers/input/touchscreen.c` 和 `drivers/input/matrix-keymap.c`。**`drivers/input/input.c`（2770 行）一个属性都不读**——它连 `linux/of.h` 都不需要。

| 属性 | 写在哪个节点 | 谁消费（定位到行） | 类型 |
| --- | --- | --- | --- |
| `touchscreen-min-x` | 触摸屏设备节点自身 | `drivers/input/touchscreen.c:81-83` | u32 |
| `touchscreen-size-x` | 同上 | `drivers/input/touchscreen.c:84-87` | u32 |
| `touchscreen-fuzz-x` | 同上 | `drivers/input/touchscreen.c:88-90` | u32 |
| `touchscreen-min-y` | 同上 | `drivers/input/touchscreen.c:94-96` | u32 |
| `touchscreen-size-y` | 同上 | `drivers/input/touchscreen.c:97-100` | u32 |
| `touchscreen-fuzz-y` | 同上 | `drivers/input/touchscreen.c:101-103` | u32 |
| `touchscreen-max-pressure` | 同上 | `drivers/input/touchscreen.c:108-111` | u32 |
| `touchscreen-fuzz-pressure` | 同上 | `drivers/input/touchscreen.c:112-115` | u32 |
| `touchscreen-inverted-x` | 同上 | `drivers/input/touchscreen.c:125-126` | bool |
| `touchscreen-inverted-y` | 同上 | `drivers/input/touchscreen.c:133-134` | bool |
| `touchscreen-swapped-x-y` | 同上 | `drivers/input/touchscreen.c:141-142` | bool |
| `keypad,num-rows` | 矩阵键盘控制器节点 | `drivers/input/matrix-keymap.c:57` | u32 |
| `keypad,num-columns` | 同上 | `drivers/input/matrix-keymap.c:58` | u32 |
| `linux,keymap` | 同上 | `drivers/input/matrix-keymap.c:84`（计数）+ `:101`（读数组） | u32 数组 |

★ **这 14 个属性核心也不是主动读的。** 三个入口函数都得由具体驱动**显式调用**：`touchscreen_parse_properties()`（36 个调用点 / 35 个文件）、`matrix_keypad_parse_properties()`（13 个调用点）、`matrix_keypad_build_keymap()`（28 个调用点 / 27 个文件）。驱动不调，属性写了也白写——这是本子系统最大的一类静默失效，见 §7.1。

```bash
# ★ 排除定义所在文件时 `.` 必须转义、并带上后面的 `:`：写成 '^drivers/input/touchscreen.c'
# 会把 drivers/input/touchscreen/c*.c 四个驱动一起吞掉（`.` 匹配了 `/`，于是 `touchscreen/c…`
# 也被当成定义文件排除）。末尾再排掉 drivers/input/touchscreen/ads7846.c:1176 那一行注释
# （kerneldoc 引用，不是调用点）。两处偏差方向相反：-4 real +1 noise，净差 3。
grep -rn 'touchscreen_parse_properties(' --include=*.c drivers/ \
  | grep -v '^drivers/input/touchscreen\.c:' | grep -vE ':[0-9]+:[[:space:]]*\*' | wc -l   # → 36
grep -rn 'matrix_keypad_parse_properties(' --include=*.c drivers/ | grep -v 'matrix-keymap.c' | wc -l   # → 13
grep -rn 'matrix_keypad_build_keymap('    --include=*.c drivers/ | grep -v 'matrix-keymap.c' | wc -l   # → 28
```

### §1.2 三方分工

| 消费方 | 属性 | 出错时的表现 |
| --- | --- | --- |
| of / i2c / spi 核心 + 驱动模型 | `compatible` `reg` `interrupts` `interrupt-parent` `*-gpios` `*-supply` `pinctrl-*` `status` | 驱动根本不 probe，`/sys/bus/*/devices/` 里没有这个设备 |
| **input 核心** | §1.1 那 14 个 | 设备在、`/dev/input/eventN` 在，**只是参数不对**（量程 0、键码全 0、方向反） |
| 具体驱动 | `linux,code` `linux,input-type` `autorepeat` `poll-interval` `debounce-interval` `wakeup-source` `syna,*` 等 | 一半报错一半静默，逐条见 §2/§5 |

★ **中间这一层是本篇的重点。** 前一层出错很响（没设备），后一层出错各驱动脾气不同；只有 input 核心这一层的失败模式最一致：**设备注册成功，参数是错的**。

### §1.3 通用绑定文档在哪

| 文档 | 说了什么 | 本篇对应节 |
| --- | --- | --- |
| `Documentation/devicetree/bindings/input/input.yaml` | 7 个通用属性（`autorepeat` / `linux,keycodes` / `linux,code` / `linux,input-type` / `poll-interval` / `power-off-time-sec` / `reset-time-sec`） | §2、§6 |
| `Documentation/devicetree/bindings/input/touchscreen/touchscreen.yaml` | 17 个 `touchscreen-*` 属性（核心只读其中 11 个） | §3、§6 |
| `Documentation/devicetree/bindings/input/matrix-keymap.yaml` | 3 个矩阵属性 + 位打包说明 | §4 |
| `Documentation/devicetree/bindings/input/gpio-keys.yaml` | `gpio-keys` / `gpio-keys-polled` 的父节点 + 子节点属性 | §2 |

★ **`input.yaml` 里那 7 个属性，input 核心一个都不读。** 它们全部由具体驱动读，所以「binding 文档里有」不等于「你的驱动支持」。§6 有一张逐条落地表。

★ **绑定目录规模**：`Documentation/devicetree/bindings/input/` 有 78 个条目（含 `touchscreen/` 49 个和 `rmi4/` 4 个两个子目录）。

```bash
ls Documentation/devicetree/bindings/input/ | wc -l
ls Documentation/devicetree/bindings/input/touchscreen/ | wc -l
ls Documentation/devicetree/bindings/input/rmi4/
```

---

## §2 GPIO 按键：`gpio-keys` 和 `gpio-keys-polled`

arm64 上最常见的 input 节点。`grep -rl 'compatible = "gpio-keys' arch/arm64/boot/dts/ | wc -l` → **151** 个文件。

### §2.1 两个 compatible 对应两个不同的驱动

| compatible | 驱动 | 靠什么产生事件 |
| --- | --- | --- |
| `gpio-keys` | `drivers/input/keyboard/gpio_keys.c`（1077 行） | 中断（GPIO 中断或直接给 `interrupts`） |
| `gpio-keys-polled` | `drivers/input/keyboard/gpio_keys_polled.c` | 定时轮询，间隔来自 `poll-interval` |

两个驱动**属性表几乎相同但不完全相同**，差异见 §2.4。

### §2.2 一个真实的 arm64 节点

`arch/arm64/boot/dts/freescale/imx8mq-librem5.dtsi:36-56`：

```dts
gpio-keys {
	compatible = "gpio-keys";
	pinctrl-names = "default";
	pinctrl-0 = <&pinctrl_keys>;

	key-vol-down {
		label = "VOL_DOWN";
		gpios = <&gpio1 17 GPIO_ACTIVE_LOW>;
		linux,code = <KEY_VOLUMEDOWN>;
		debounce-interval = <50>;
		wakeup-source;
	};

	key-vol-up {
		label = "VOL_UP";
		gpios = <&gpio1 16 GPIO_ACTIVE_LOW>;
		linux,code = <KEY_VOLUMEUP>;
		debounce-interval = <50>;
		wakeup-source;
	};
};
```

★ **一个按键一个子节点，父节点只有三个属性。** 子节点名不能随便起：`Documentation/devicetree/bindings/input/gpio-keys.yaml:26` 那条 `patternProperties` 正则要求名字是 `button` / `event` / `key` / `switch` 之一，或者带 `-` 前后缀的形式（`key-vol-down` 命中的是 `(button|event|key|switch)-[a-z0-9-]+`）。**名字不匹配只会让 `dtbs_check` 报警，驱动照样能跑**——`gpio_keys_get_devtree_pdata()` 用的是 `device_for_each_child_node()`（`drivers/input/keyboard/gpio_keys.c:757`），不看名字。

### §2.3 属性逐条

**父节点：**

| 属性 | 谁消费 | 缺了会怎样 |
| --- | --- | --- |
| `autorepeat` | `drivers/input/keyboard/gpio_keys.c:753`（`gpio-keys`）/ `gpio_keys_polled.c:164`（`polled`） | 不开自动重复，长按只报一次 |
| `label` | `gpio_keys.c:755` / `gpio_keys_polled.c:167` | `input->name` 退化成默认字符串，用户态按名字找设备会找不到 |
| `poll-interval` | 本家族里只有 `gpio_keys_polled.c:165`（全树另有 6 处，见 §6.5） | `gpio-keys-polled` 会用驱动默认值；写在 `gpio-keys` 节点上**完全没人读** |

**子节点：**

| 属性 | 谁消费 | 必填 | 默认值 / 缺了会怎样 |
| --- | --- | --- | --- |
| `linux,code` | `gpio_keys.c:762-767` / `gpio_keys_polled.c:170` | ★ **是** | `dev_err(dev, "Button without keycode\n")` + `-EINVAL`，整个设备不注册 |
| `gpios` | `gpio_keys.c:524-525`（`devm_fwnode_gpiod_get`） | 否 | `-ENOENT` 被吞掉当成「纯中断按键」（`:528-533`），此时必须有 `interrupts` |
| `interrupts` | `gpio_keys.c:758-760`（`irq_of_parse_and_map`） | 否 | 和 `gpios` 二者必须有一个，都没有 → `dev_err(dev, "Found button without gpio or irq\n")` + `-EINVAL`（`:624-626`） |
| `label` | `gpio_keys.c:769` | 否 | 只影响 GPIO 申请时的描述串和 sysfs，不影响事件 |
| `linux,input-type` | `gpio_keys.c:771-773` | 否 | 默认 `EV_KEY`（`:773`） |
| `linux,input-value` | **只有** `gpio_keys_polled.c:183` | 否 | `gpio-keys` 驱动**不读它**，见 §2.4 |
| `wakeup-source` | `gpio_keys.c:776` | 否 | 按键不能唤醒系统；老名字 `gpio-key,wakeup` 同样被接受（`:778`） |
| `wakeup-event-action` | `gpio_keys.c:780-781`，实际使用在 `:606-622` | 否 | 默认 `EV_ACT_ANY`（`:615-621` 的 `default` 分支：不改唤醒触发类型） |
| `linux,can-disable` | `gpio_keys.c:783-784` | 否 | sysfs 里的 `disabled_keys` / `disabled_switches` 不能关掉这个按键 |
| `debounce-interval` | `gpio_keys.c:786-788` / `gpio_keys_polled.c:192` | 否 | 默认 **5**（毫秒，`gpio_keys.c:788`） |

★ **`debounce-interval` 在两种按键上是两个意思。**

| 按键形态 | 代码路径 | 语义 |
| --- | --- | --- |
| 有 `gpios` | `gpio_keys.c:566-581` | 先试硬件消抖 `gpiod_set_debounce(…, interval * 1000)`（**微秒**，`:567-568`）；控制器不支持就退化成软件消抖（`:570-572`），并按 `gpiod_cansleep()` 决定用 hrtimer 还是普通 timer（`:579-580`） |
| 只有 `interrupts` | `gpio_keys.c:636` | 变成 **`release_delay`**——中断只能表示「按下」，这个值是「多久之后自动补一次松开事件」 |

**错用会怎样：** 给一个纯中断按键写 `debounce-interval = <50>`，你以为是消抖 50 毫秒，实际是「按下 50 毫秒后自动松开」。想改消抖得去 `interrupts` 那一侧的控制器上做。

★ **`wakeup-event-action` 对纯中断按键无效。** 那个 `switch`（`:606-622`）整段在 `if (bdata->gpiod)` 里（`:563`），纯中断分支（`:623-634`）压根不看它。

★ **纯中断按键只能是 `EV_KEY`。** `:631-633` 显式拒绝：`dev_err(dev, "Only EV_KEY allowed for IRQ buttons.\n")` + `-EINVAL`。所以 `linux,input-type = <EV_SW>` 必须配 `gpios`。

★ **`wakeup-event-action` 的三个常量不在绑定文档说的地方。** `gpio-keys.yaml:75` 写着 `Supported values are defined in linux-event-codes.h`，但 `grep -rn 'EV_ACT_' include/uapi/linux/input-event-codes.h` 是空的——真正的定义在 `include/dt-bindings/input/gpio-keys.h:9-11`。DTS 里要 `#include <dt-bindings/input/gpio-keys.h>`。（文档与代码冲突，已登记在 `99-evidence.md` 的 Documentation 台账里。）

### §2.4 ★ 两个驱动的属性差集

| 属性 | `gpio-keys` | `gpio-keys-polled` |
| --- | --- | --- |
| `poll-interval` | 不读 | 读（`gpio_keys_polled.c:165`） |
| `linux,input-value` | **不读** | 读（`gpio_keys_polled.c:183`） |
| `wakeup-event-action` | 读 | 不读 |
| `linux,can-disable` | 读 | 不读 |
| `autorepeat` 的读法 | `device_property_read_bool()`（`gpio_keys.c:753`） | `device_property_present()`（`gpio_keys_polled.c:164`） |

**错用会怎样：** 把一个用 `linux,input-value` 做 EV_ABS 摇杆的节点从 `gpio-keys-polled` 改成 `gpio-keys`，那个属性静默失效，`value` 全变成 GPIO 电平（0/1），没有任何日志。反过来，`gpio-keys-polled` 节点上写 `linux,can-disable`，sysfs 里那两个文件不会出现。

---

## §3 触摸屏：11 个通用属性

全部由 `drivers/input/touchscreen.c`（205 行）里的 `touchscreen_parse_properties()` 一个函数读完（`:65-146`）。

### §3.1 一个真实的 arm64 节点

`arch/arm64/boot/dts/allwinner/sun50i-a64-pinephone.dtsi:164-175`：

```dts
touchscreen@5d {
	compatible = "goodix,gt917s";
	reg = <0x5d>;
	interrupt-parent = <&pio>;
	interrupts = <7 4 IRQ_TYPE_LEVEL_HIGH>; /* PH4 */
	irq-gpios = <&pio 7 4 GPIO_ACTIVE_HIGH>; /* PH4 */
	reset-gpios = <&pio 7 11 GPIO_ACTIVE_HIGH>; /* PH11 */
	AVDD28-supply = <&reg_ldo_io0>;
	VDDIO-supply = <&reg_ldo_io0>;
	touchscreen-size-x = <720>;
	touchscreen-size-y = <1440>;
};
```

★ **节点里只有两个 `touchscreen-*` 属性，另外 7 个（`reg` / `interrupt-parent` / `interrupts` / `irq-gpios` / `reset-gpios` / 两个 `*-supply`）都不是 input 的事**——它们分别归 i2c 核心、irq 核心、gpiolib、regulator 消费。这是 §1.2 那张三方分工表在一个真实节点上的样子。

### §3.2 属性逐条

| 属性 | 消费位置 | 默认值（读不到时） | 落到哪个 ABS 轴 |
| --- | --- | --- | --- |
| `touchscreen-min-x` | `drivers/input/touchscreen.c:81-83` | `input_abs_get_min(input, axis_x)` | `ABS_X` 或 `ABS_MT_POSITION_X` |
| `touchscreen-size-x` | `:84-87` | `input_abs_get_max(input, axis_x) + 1` | 同上，**写进去的是本值减 1**（`:92`） |
| `touchscreen-fuzz-x` | `:88-90` | `input_abs_get_fuzz(input, axis_x)` | 同上 |
| `touchscreen-min-y` | `:94-96` | `input_abs_get_min(input, axis_y)` | `ABS_Y` 或 `ABS_MT_POSITION_Y` |
| `touchscreen-size-y` | `:97-100` | `input_abs_get_max(input, axis_y) + 1` | 同上，减 1（`:105`） |
| `touchscreen-fuzz-y` | `:101-103` | `input_abs_get_fuzz(input, axis_y)` | 同上 |
| `touchscreen-max-pressure` | `:108-111` | `input_abs_get_max(input, axis)` | `ABS_PRESSURE` 或 `ABS_MT_PRESSURE` |
| `touchscreen-fuzz-pressure` | `:112-115` | `input_abs_get_fuzz(input, axis)` | 同上 |
| `touchscreen-inverted-x` | `:125-131` | false | 只写 `prop->invert_x`，并把 x 轴区间平移到 0 起点 |
| `touchscreen-inverted-y` | `:133-139` | false | 只写 `prop->invert_y`，同上 |
| `touchscreen-swapped-x-y` | `:141-144` | false | 只写 `prop->swap_x_y`，并 `swap()` 整个 absinfo |

`multitouch` 那个形参决定 A 类轴还是 B 类轴（`:78-79`）：传 `true` 用 `ABS_MT_POSITION_X/Y` + `ABS_MT_PRESSURE`，传 `false` 用 `ABS_X/Y` + `ABS_PRESSURE`。**同一份 DTS 在两个驱动上会落到不同的轴**，这不是属性的语义差异，是调用方参数的差异。

### §3.3 ★ `touchscreen-size-x` 是分辨率，不是最大坐标

绑定文档 `touchscreen.yaml:23-25` 写得很清楚：`horizontal resolution of touchscreen (maximum x coordinate reported + 1)`。代码对得上——`touchscreen.c:92` 传给 `touchscreen_set_params()` 的是 `maximum - 1`。

所以 pinephone 那块屏：`touchscreen-size-x = <720>` → `absinfo[ABS_MT_POSITION_X].maximum == 719`。用户态 `evtest` 看到的是 `Max 719`。

**写成 719 会怎样：** 最大坐标变成 718，最右边一列像素永远点不到。这个错误没有任何日志，只能靠 `evtest` 读 `Max` 值对比屏幕分辨率发现。

### §3.4 ★ 必须先声明轴，`touchscreen_parse_properties()` 才写得进去

`touchscreen_set_params()` 第一件事就是查轴位（`touchscreen.c:39-43`）：

```c
if (!test_bit(axis, dev->absbit)) {
	dev_warn(&dev->dev,
		 "Parameters are specified but the axis %lu is not set up\n",
		 axis);
	return;
}
```

它是 `void` 的，调用方拿不到失败信号，**只有一条 `dev_warn`**。所以顺序是死的：

```
input_set_abs_params() / input_set_capability(EV_ABS, ABS_MT_POSITION_X)
        ↓  必须在前
touchscreen_parse_properties()
```

goodix 的做法是正例：`drivers/input/touchscreen/goodix.c:1189-1190` 先 `input_set_capability(EV_ABS, ABS_MT_POSITION_X/Y)`，`:1194` 才 `touchscreen_parse_properties()`。

★ **`ABS_PRESSURE` 是最容易漏的那一个。** 很多驱动只声明了 x/y 就调 helper，DTS 里又写了 `touchscreen-max-pressure`，于是拿到一条 `axis 24 is not set up`（`ABS_PRESSURE` = 24）或 `axis 58 is not set up`（`ABS_MT_PRESSURE` = 58）的 warn，坐标正常、压力全是 0。

### §3.5 三个 boolean 属性的执行次序

`touchscreen.yaml:66-68` 声明 `Swapping is done after inverting the axis`，代码次序一致：先 `invert_x`（`:125-131`）、再 `invert_y`（`:133-139`）、最后 `swap_x_y`（`:141-144`）。

★ **这三个属性 `touchscreen_parse_properties()` 只是记下来，不做任何坐标变换。** 真正的翻转发生在上报时：`touchscreen_report_pos()`（`:195-204`）和 `touchscreen_set_mt_pos()`（`:173-181`）里调 `touchscreen_apply_prop_to_x_y()`（`:148-160`）。**驱动如果自己 `input_report_abs()` 而不走这两个 helper，这三个属性就完全无效**——DTS 写了、`prop` 里也记了、坐标照旧。

**这是 input 子系统最典型的 DT 静默失效**：属性被"读到了"，也不算死字（有消费者），但消费者链条断在驱动那一环。定位办法见 §7 的决策树。

### §3.6 两个次序副作用

★ **`inverted-x/y` 会改 absinfo。** `:129-130` 把 `maximum -= minimum; minimum = 0`。如果 DTS 同时写了 `touchscreen-min-x = <10>` 和 `touchscreen-inverted-x`，最终区间是 `[0, max-10]` 而不是 `[10, max]`。区间宽度不变，起点被挪到 0。

★ **`swapped-x-y` 交换的是整个 `struct input_absinfo`。** `:144` 的 `swap()` 把 min/max/fuzz/flat/resolution 五个字段一起换过去。所以 x/y 分辨率不同的屏（720×1440）加上这个属性之后，x 轴 max 变成 1439、y 轴变成 719——这是对的，但意味着**你不能只写 `swapped-x-y` 而 `size-x/size-y` 还按物理方向填**，两者是叠加的。

★ **压力的 minimum 被强制成 0。** `:117` 那次调用第三个实参是字面量 `0`，不是读来的值。驱动之前 `input_set_abs_params(dev, ABS_PRESSURE, 5, 255, 0, 0)` 设的下限 5，只要 DTS 里出现 `touchscreen-max-pressure` 或 `touchscreen-fuzz-pressure` 任意一个，就会被改回 0。

### §3.7 绑定文档里的 `dependencies` 不是代码检查

`touchscreen.yaml:83-87` 有四条依赖：`size-x` ↔ `size-y`、`x-mm` ↔ `y-mm`。**这只在 `make dtbs_check` 里生效**，运行时代码完全不看——`touchscreen.c` 里三个轴各自独立判断 `data_present`（`:91`/`:104`/`:116`），只写 `size-x` 不写 `size-y` 会正常生效一半。

---

## §4 矩阵键盘：3 个属性 + 一个位打包约定

由 `drivers/input/matrix-keymap.c`（205 行）读完。arm64 上 `keypad,num-rows` 只出现在 2 个 DTS 文件里（`arch/arm64/boot/dts/mediatek/mt8183-pumpkin.dts:132`、`arch/arm64/boot/dts/nvidia/tegra132-norrin.dts:795`），`linux,keymap` 出现在 9 个文件里——差额来自 `google,cros-ec-keyb` 那一族，行数在 include 进来的 `.dtsi` 里。

```bash
grep -rln 'linux,keymap' arch/arm64/boot/dts/ | wc -l        # → 9
grep -rn  'keypad,num-rows' arch/arm64/boot/dts/             # → 2 行
```

### §4.1 一个真实的 arm64 节点

`arch/arm64/boot/dts/mediatek/mt8183-pumpkin.dts:126-136`：

```dts
&keyboard {
	pinctrl-names = "default";
	pinctrl-0 = <&keyboard_pins>;
	status = "okay";
	linux,keymap = <MATRIX_KEY(0x00, 0x00, KEY_VOLUMEDOWN)
			MATRIX_KEY(0x01, 0x00, KEY_VOLUMEUP)>;
	keypad,num-rows = <2>;
	keypad,num-columns = <1>;
	debounce-delay-ms = <32>;
	mediatek,keys-per-group = <2>;
};
```

前三个属性是核心读的，后两个是驱动私有的（`drivers/input/keyboard/mt6779-keypad.c:160` 和 `:171`）。这个节点也顺手示范了 §1.2 的分工：`pinctrl-*` 归 pinctrl 核心，`status` 归 of 核心。

### §4.2 三个属性

| 属性 | 消费位置 | 缺了会怎样 |
| --- | --- | --- |
| `keypad,num-rows` | `drivers/input/matrix-keymap.c:57` | 与 `num-columns` 一起判断，任一为 0 → `dev_err(dev, "number of keypad rows/columns not specified\n")` + `-EINVAL`（`:60-63`） |
| `keypad,num-columns` | `:58` | 同上 |
| `linux,keymap` | `:84`（数元素）、`:101`（读数组） | `device_property_count_u32()` ≤ 0 → `dev_err "missing or malformed property %s: %d\n"` + `-EINVAL`（`:85-89`） |

`matrix_keypad_parse_properties()` 是 `EXPORT_SYMBOL_GPL`（`:67`），`matrix_keypad_build_keymap()` 是 `EXPORT_SYMBOL`（`:200`）——同一个文件里两种导出，non-GPL 模块只能用后者。

### §4.3 位打包：两个宏，掩码不一样

DTS 里写的是 `MATRIX_KEY()`，来自 `include/dt-bindings/input/input.h:15-16`：

```c
#define MATRIX_KEY(row, col, code)	\
	((((row) & 0xFF) << 24) | (((col) & 0xFF) << 16) | ((code) & 0xFFFF))
```

平台代码里写的是 `KEY()`，来自 `include/linux/input/matrix_keypad.h:12-14`：

```c
#define KEY(row, col, val)	((((row) & (MATRIX_MAX_ROWS - 1)) << 24) |\
				 (((col) & (MATRIX_MAX_COLS - 1)) << 16) |\
				 ((val) & 0xffff))
```

取值端只有一套，`matrix_keypad.h:16-18`：

| 宏 | 定义 | 取几位 |
| --- | --- | --- |
| `KEY_ROW(k)` | `((k) >> 24) & 0xff` | 8 位 |
| `KEY_COL(k)` | `((k) >> 16) & 0xff` | 8 位 |
| `KEY_VAL(k)` | `(k) & 0xffff` | 16 位 |

★ **`KEY()` 和 `MATRIX_KEY()` 的行/列掩码不同：一个是 31（`MATRIX_MAX_ROWS - 1`，5 位），一个是 `0xFF`（8 位）。** 后果不对称：

- **DTS 侧**：`MATRIX_KEY(33, 0, KEY_A)` 原样打包成 row=33，进 `matrix_keypad_map_key()` 后被 `row >= rows` 拦下（`matrix-keymap.c:31-36`），报 `matrix_keypad_map_key: invalid keymap entry 0x2100001e (row: 33, col: 0, rows: 8, cols: 13)`（格式串在 `:33-34`，第一个 `%s` 是 `__func__`；`KEY_A` = 30 = `0x1e`，`include/uapi/linux/input-event-codes.h:105`），整个 probe 失败。**吵，但安全。**
- **平台代码侧**：`KEY(33, 0, KEY_A)` 在宏展开时就被 `& 31` 截成 row=1，**校验永远通过，键位悄悄跑到第 1 行**。没有任何日志。

绑定文档 `matrix-keymap.yaml:27-29` 描述的打包格式是 `row << 24 | column << 16 | key-code`，与两个宏都一致——**它没有提掩码宽度**，这是文档给不出的信息。

### §4.4 keymap 数组多大、索引怎么算

三个量在 `matrix-keymap.c` 里被算两遍（`:74-75` 和 `:156-157`），公式相同：

```
row_shift = get_count_order(cols)          // cols 向上取整到 2 的幂的位数
max_keys  = rows << row_shift
索引       = MATRIX_SCAN_CODE(row, col, row_shift) = (row << row_shift) + col
```

`MATRIX_SCAN_CODE` 在 `include/linux/input/matrix_keypad.h:20`；`get_count_order()` 是 `include/linux/bitops.h:211-217` 的 `static inline`，实现就一行 `fls(--count)`——所以 `cols = 13` 得 4，`cols = 16` 也得 4，`cols = 1` 得 0。

以 tegra132-norrin 为例（`rows = 8`、`cols = 13`）：`get_count_order(13) = 4` → `row_shift = 4` → `max_keys = 8 << 4 = 128`。**注意不是 8 × 13 = 104**——每行按 16 个槽对齐，数组里有 24 个洞。`input_dev->keycodemax` 被设成 128（`:177`）。

★ **`linux,keymap` 的元素个数上限是 `max_keys` 而不是 `rows × cols`**（`:91-95`，超了报 `%s size overflow (%d vs max %u)`）。这个上限比直觉宽，所以「写多了一项」不一定报错——只有落在越界的 row/col 上才会被 `:31-36` 抓到。

★ **`matrix_keypad_build_keymap()` 自己不校验 `rows`/`cols`。** 校验只在 `matrix_keypad_parse_properties()` 里（`:60-63`），而这是两个独立导出的函数。驱动如果跳过前者直接调后者并传 `cols = 0`，`get_count_order(0)` 返回 `-1`，`rows << -1` 是未定义行为。本树 28 个调用点都在同一个 probe 里先调 parse 再调 build，所以碰不到，但这是接口层面的一个缺口（登记在 `99-evidence.md` 的设计债一节）。

`MATRIX_MAX_ROWS` 和 `MATRIX_MAX_COLS` 都是 32（`matrix_keypad.h:9-10`），但**这两个常量只被 `KEY()` 宏用来做掩码，核心没有任何一处拿它们校验 `keypad,num-rows`**：

```bash
grep -rn 'MATRIX_MAX_ROWS\|MATRIX_MAX_COLS' drivers/input/ include/linux/input/
```

### §4.5 ★ 有 platform_data 时 DT 被完全跳过

`matrix_keypad_build_keymap()` 的分支在 `:181-194`：

```c
if (keymap_data) {
	for (i = 0; i < keymap_data->keymap_size; i++) { ... }   /* 用 platform_data */
} else {
	error = matrix_keypad_parse_keymap(keymap_name, rows, cols, input_dev);   /* 才读 DT */
}
```

**第一个实参非 NULL 时，`linux,keymap` 一行都不会被读。** 而 `keypad,num-rows` / `num-columns` 是另一个函数读的，仍然生效——于是出现「行列数来自 DT、键位来自板级代码」的混合状态，DTS 里改 `linux,keymap` 毫无反应。

判断某个驱动走哪条路：看它给第一个实参传什么。传 `NULL` 的才读 DT，例如 `drivers/input/keyboard/cros_ec_keyb.c:619`；传 `pdata->keymap_data` 的不读，例如 `drivers/input/keyboard/matrix_keypad.c:518`。

### §4.6 ★ `keymap_name` 这个形参在本树里永远是 NULL

`matrix_keypad_parse_keymap()` 在 `:81-82` 兜底成 `"linux,keymap"`。全树 28 个调用点，第二个实参**无一例外都是 `NULL`**：

```bash
grep -rn -A1 'matrix_keypad_build_keymap(' --include=*.c drivers/ \
  | grep -oE 'matrix_keypad_build_keymap\([^,]+, *[A-Za-z_"][A-Za-z_0-9"., ]*' \
  | sed 's/.*(//' | awk -F', *' '{print $2}' | sort | uniq -c
# → 28 NULL
```

（29 个 grep 命中里有 1 个是 `drivers/input/matrix-keymap.c:150` 的函数定义本身。）

所以 `linux,fn-keymap` 是**死字**：

| 出处 | 内容 |
| --- | --- |
| `Documentation/devicetree/bindings/input/matrix-keymap.yaml:17-20` | 描述里"提议"这个名字 |
| `Documentation/devicetree/bindings/input/nvidia,tegra20-kbc.txt` 第 26 行 | 作为属性正式文档化 |
| 代码 | **0 处**——`grep -rn 'fn-keymap' --include=*.c --include=*.h --include=*.dts --include=*.dtsi .` → 0 |

写进 DTS 不会报错、不会生效、不会有日志。（`tegra-kbc.c:682` 传的是 `kbc->keymap_data`，连主 keymap 都不走 DT。）

### §4.7 驱动私有属性举例

核心只管那 3 个，其余归驱动。arm64 上会碰到的：

| 属性 | 谁读 | 说明 |
| --- | --- | --- |
| `debounce-delay-ms` | `drivers/input/keyboard/mt6779-keypad.c:160` | mediatek 键盘控制器 |
| `mediatek,keys-per-group` | `mt6779-keypad.c:171` | 同上 |
| `wakeup-source` | `mt6779-keypad.c:188` | 同上；和 §2 的 `wakeup-source` 是同名不同消费者 |
| `google,needs-ghost-filter` | `drivers/input/keyboard/cros_ec_keyb.c:616-617` | 鬼键过滤 |
| `function-row-physmap` | `cros_ec_keyb.c:530-531`、`:542-544`；`drivers/input/keyboard/atkbd.c:1240`、`:1242` | 顶排功能键物理映射，**两个驱动各读一遍**，上限 `VIVALDI_MAX_FUNCTION_ROW_KEYS`，超了只 `dev_warn` 后截断（`cros_ec_keyb.c:535-540`） |

---

## §5 RMI4：唯一有子节点层级的 input DT 绑定

Synaptics RMI4 触摸屏（`drivers/input/rmi4/`）。arm64 上有 13 个 DTS 文件用它，全在 `arch/arm64/boot/dts/qcom/` 下：

```bash
grep -rln 'syna,rmi4' arch/arm64/boot/dts/ | wc -l     # → 13
```

### §5.1 两个 compatible，两条传输

| compatible | 定义位置 | 挂在哪 |
| --- | --- | --- |
| `syna,rmi4-i2c` | `drivers/input/rmi4/rmi_i2c.c:180` | i2c 总线 |
| `syna,rmi4-spi` | `drivers/input/rmi4/rmi_spi.c:352` | spi 总线 |

### §5.2 ★ 属性分布在父节点和 `rmi4-fXX` 子节点两层

`rmi_function_of_probe()`（`drivers/input/rmi4/rmi_bus.c:156-164`）按功能号拼子节点名：

```c
char of_name[9];
snprintf(of_name, sizeof(of_name), "rmi4-f%02x", fn->fd.function_number);
fn->dev.of_node = of_get_child_by_name(node, of_name);
```

功能号是**运行时从芯片的 PDT 里读出来的**，不是从 DT 里读的。DT 只负责给已经发现的功能补参数——找不到对应子节点时 `of_get_child_by_name()` 返回 NULL，功能照常工作，只是拿不到 DT 参数。

★ **子节点名是十六进制两位小写，`of_name` 缓冲区只有 9 字节**（`"rmi4-f"` 6 + 2 + `\0`）。写成 `rmi4-f1`（一位）或 `rmi4-F12`（大写）都匹配不上，且**没有任何日志**。

一个真实节点，`arch/arm64/boot/dts/qcom/msm8998-oneplus-common.dtsi:171-201`：

```dts
touchscreen@20 {
	compatible = "syna,rmi4-i2c";
	reg = <0x20>;
	#address-cells = <1>;
	#size-cells = <0>;

	interrupt-parent = <&tlmm>;
	interrupts = <125 IRQ_TYPE_EDGE_FALLING>;

	pinctrl-names = "default";
	pinctrl-0 = <&ts_int_active &ts_reset_active>;

	vdd-supply = <&vreg_l28_3p0>;
	vio-supply = <&vreg_l6a_1p8>;

	syna,reset-delay-ms = <20>;
	syna,startup-delay-ms = <20>;

	rmi4-f01@1 {
		reg = <0x01>;
		syna,nosleep-mode = <1>;
	};

	rmi4_f12: rmi4-f12@12 {
		reg = <0x12>;
		syna,rezero-wait-ms = <20>;
		syna,sensor-type = <1>;
		touchscreen-x-mm = <68>;
		touchscreen-y-mm = <122>;
	};
};
```

### §5.3 属性全表（17 个 `syna,*` + 5 个 `touchscreen-*` + 2 个 spi）

```bash
grep -rhoE '"syna,[a-z0-9,-]+"' drivers/input/rmi4/*.c | sort -u | grep -v 'rmi4-' | wc -l   # → 17
```

**父节点（transport 设备）：**

| 属性 | 消费位置 | 备注 |
| --- | --- | --- |
| `syna,reset-delay-ms` | `drivers/input/rmi4/rmi_driver.c:996-997` | 复位后等多久 |
| `syna,startup-delay-ms` | `drivers/input/rmi4/rmi_i2c.c:250-251` | **只有 i2c 传输读**；`:253` 紧跟一个 `msleep()` |
| `spi-rx-delay-us` | `drivers/input/rmi4/rmi_spi.c:336-338` | **只有 spi 传输读** |
| `spi-tx-delay-us` | `rmi_spi.c:342-344` | 同上 |

**`rmi4-f01` 子节点（`drivers/input/rmi4/rmi_f01.c:347-372`）：**

| 属性 | 行 | 单位换算 |
| --- | --- | --- |
| `syna,nosleep-mode` | `:347-349` | 直接写进 `power_management.nosleep` |
| `syna,wakeup-threshold` | `:353-354` | 原值 |
| `syna,doze-holdoff-ms` | `:360-361` | ★ 存的是 **`val * 100`**（`:365`） |
| `syna,doze-interval-ms` | `:367-368` | ★ 存的是 **`val / 10`**（`:372`） |

★ **两个 doze 属性的名字带 `-ms`，但寄存器单位不是毫秒。** `doze_interval` 的单位是 10 ms，`doze_holdoff` 是 100 µs——所以 `syna,doze-interval-ms = <5>` 会被整数除法算成 0。写小于 10 的值等于写 0，没有任何警告。

**`rmi4-f11` / `rmi4-f12` 子节点（二维传感器，`drivers/input/rmi4/rmi_2d_sensor.c:225-320`）：**

| 属性 | 行 | 类型 |
| --- | --- | --- |
| `touchscreen-swapped-x-y` | `:231-232` | bool → `axis_align.swap_axes` |
| `touchscreen-inverted-x` | `:234-235` | bool → `axis_align.flip_x` |
| `touchscreen-inverted-y` | `:237-238` | bool → `axis_align.flip_y` |
| `syna,clip-x-low` | `:240` | u32 |
| `syna,clip-y-low` | `:246` | u32 |
| `syna,clip-x-high` | `:252` | u32 |
| `syna,clip-y-high` | `:258` | u32 |
| `syna,offset-x` | `:264` | u32 |
| `syna,offset-y` | `:270` | u32 |
| `syna,delta-x-threshold` | `:276-277` | u32 |
| `syna,delta-y-threshold` | `:283-284` | u32 |
| `syna,sensor-type` | `:290-291` | u32，1 = 触摸屏、2 = 触摸板（`Documentation/devicetree/bindings/input/rmi4/rmi_2d_sensor.txt`） |
| `touchscreen-x-mm` | `:295` | u32 |
| `touchscreen-y-mm` | `:301` | u32 |
| `syna,disable-report-mask` | `:307-308` | u32 |
| `syna,rezero-wait-ms` | `:314-315` | u32 |

★ **rmi4 读那三个 `touchscreen-*` 布尔属性走的是自己的路，不是 §3 的 helper。** `rmi_2d_sensor.c:231/:234/:237` 直接 `of_property_read_bool(dev->of_node, …)`，所以：

- **只认 DT，不认 ACPI / swnode**（helper 用的 `device_property_*` 三者通吃）
- **写在 `rmi4-f11`/`rmi4-f12` 子节点上，不是父节点上**——和所有其他触摸屏驱动位置相反
- 另外两个同族属性 `touchscreen-x-mm` / `touchscreen-y-mm` 是 u32，走 `rmi_of_property_read_u32()`（`:295`/`:301`），和 elantech 那边读的是同一对名字但代码路径完全不同

### §5.4 ★ `rmi_of_property_read_u32()` 在本树里永远返回 0

这个 helper 是 rmi4 私有的（声明在 `drivers/input/rmi4/rmi_bus.h:190-191`——**私有头，不在 `include/` 下**，树外驱动用不了，尽管它是 `EXPORT_SYMBOL_GPL`，`rmi_bus.c:430`）。实现在 `rmi_bus.c:414-429`：

```c
retval = of_property_read_u32(dev->of_node, prop, &val);
if (retval && (!optional && retval == -EINVAL)) {
	dev_err(dev, "Failed to get %s value: %d\n", prop, retval);
	return retval;
}
*result = val;
return 0;
```

全树 **20 个调用点，`optional` 实参全是 `1`**：

```bash
perl -0777 -ne 'while(/rmi_of_property_read_u32\((.*?)\);/gs){ $a=$1; $a=~s/\s+/ /g; print "$a\n" }' \
  drivers/input/rmi4/*.c | grep -c ', 1$'
# → 20
```

`optional = 1` 让 `!optional && …` 恒假，函数因此**永不返回非 0**。两个后果：

- 每个调用点后面那句 `if (retval) return retval;` 是死代码
- `dev_err(dev, "Failed to get %s value: %d\n", …)`（`rmi_bus.c:422-423`）这条日志**在本树的任何配置下都打不出来**——`grep` 到它、以为它是可用的诊断信号，是会踩空的

★ **更实际的后果：`val` 初始化为 0（`:418`），属性不存在时 `*result = 0` 照样执行。** 所以「DT 里不写这个属性」不等于「保留驱动默认值」，而是「显式写 0」。对 `syna,clip-x-high` 这类属性，0 本该是「上界为 0」，但 clip 逻辑对 0 做了旁路——`rmi_2d_sensor.c:146` 和 `:151` 都是 `if (clip_?_high)` 才收窄，`:55` 和 `:58` 同样带这个门，所以不写就等于不裁剪；对 `syna,sensor-type` 而言 0 是「未指定」的合法值。

★ 顺带一个容易看成 bug 的地方：`:55-59` 那两句 clip 用的是 `sensor->max_x` / `max_y`，**不是属性值本身**。它之所以正确，是因为 `:146-153` 在 configure 阶段已经把 `clip_?_high` 折进 `sensor->max_?` 了（`min()`）。两处一起读才成立，单看 `:55` 会以为属性没生效。

★ `clip_?_low` 没有这个门：`:52-53` 是无条件的 `max(clip_?_low, obj->?)`，`:145` / `:150` 无条件写 `sensor->min_?`。不写该属性 → 下界 0，正好是默认值，所以同样无害。这里的教训不是「有 bug」，而是**这套 DT 解析没有"未指定"和"指定为 0"的区分能力**。

★ **`optional` 那个条件式本身还漏了一类错误。** `of_property_read_u32()` 除了 `-EINVAL`（属性不存在）还会返回 `-ENODATA`（属性存在但没值）和 `-EOVERFLOW`（属性存在但不足 4 字节）。条件里写死了 `retval == -EINVAL`，所以即使某天有调用点传 `optional = 0`，`syna,clip-x-low;`（空属性）这种写法仍然静默通过、值取 0。登记在 `99-evidence.md` 的设计债一节。

---

## §6 死字、孤儿和同名不同义

绑定文档列出属性，不代表有代码读它。这一节把差额算清楚。

### §6.1 `touchscreen.yaml` 的 17 个属性，核心只读 11 个

```bash
awk '/^properties:/{f=1;next} /^[a-z]/{f=0} f && /^  [a-z]/{c++} END{print c}' \
  Documentation/devicetree/bindings/input/touchscreen/touchscreen.yaml
# → 17
```

剩下 6 个不经 `drivers/input/touchscreen.c`，逐条落地：

| 属性 | 消费者 | 状态 |
| --- | --- | --- |
| `touchscreen-min-pressure` | `drivers/input/touchscreen/resistive-adc-touch.c:231-233`、`drivers/input/touchscreen/ads7846.c:1179-1180` | 2 个驱动私有 |
| `touchscreen-average-samples` | `ads7846.c:1184-1185`、`drivers/input/touchscreen/imx6ul_tsc.c:480-481` | 2 个驱动私有，**语义不同**（见 §6.3） |
| `touchscreen-x-mm` | `drivers/input/mouse/elan_i2c_core.c:431-432`、`drivers/input/rmi4/rmi_2d_sensor.c:295` | 2 个驱动私有 |
| `touchscreen-y-mm` | `elan_i2c_core.c:433-434`、`rmi_2d_sensor.c:301` | 同上 |
| `touchscreen-x-plate-ohms` | `resistive-adc-touch.c:188-190` | 1 个驱动私有，且在那里是**必填** |
| `touchscreen-y-plate-ohms` | **无** | ★ **死字** |

### §6.2 ★ 两个确凿的死字

**`touchscreen-y-plate-ohms`**

| 位置 | 内容 |
| --- | --- |
| `Documentation/devicetree/bindings/input/touchscreen/touchscreen.yaml:80-81` | `Resistance of the Y-plate in Ohms` |
| DTS | 4 个文件在用，全是 arm32：`arch/arm/boot/dts/imx6qdl-skov-cpu-revc.dtsi`、`imx6dl-victgo.dts`、`imx6dl-prtvt7.dts`、`imx6dl-plym2m.dts` |
| 代码 | **0 处** |

```bash
grep -rn '"touchscreen-y-plate-ohms"' --include=*.c --include=*.h .   # → 空
```

配对的 `touchscreen-x-plate-ohms` 有一个消费者（`resistive-adc-touch.c:189`），y 那一半没有——**这是一对里只死了一半**，肉眼看 DTS 会以为两个都生效。

**`linux,fn-keymap`** — 见 §4.6，同样 0 消费者，但它连 DTS 都没人写。

★ **两个死字的性质不同：** `y-plate-ohms` 是「有人写、没人读」，`fn-keymap` 是「有人文档化、没人写也没人读」。前者会让人误以为电阻式压力计算用了两个方向的电阻值（实际只用 x），后者只是文档冗余。

### §6.3 ★ `touchscreen-average-samples` 在两个驱动里是两回事

| 驱动 | 落到哪 | 合法取值 | 越界怎样 |
| --- | --- | --- | --- |
| `drivers/input/touchscreen/imx6ul_tsc.c:480-501` | `average_enable` + `average_select`（`:493-494`，`ilog2(n) - 2`） | 1 / 4 / 8 / 16 / 32 | `dev_err "touchscreen-average-samples (%u) must be 1, 4, 8, 16 or 32\n"` + `-EINVAL`（`:496-500`），probe 失败 |
| `drivers/input/touchscreen/ads7846.c:1184-1185` | **`pdata->debounce_max`** | 任意 u32 | 不校验 |

后者的字段名叫 `debounce_max`——**属性名说"平均采样数"，实际落进"去抖上限"**。ads7846 自己也有 `ti,debounce-max`，`touchscreen-average-samples` 是它的通用别名（`:1183` 先读 `ti,debounce-max`，`:1184` 再让通用名覆盖）。

**跨驱动搬 DTS 片段时这类属性最危险**：名字对、schema 过、语义换了。

### §6.4 ★ `touchscreen-*` 名字也被用作驱动之间的内部 ABI

`drivers/input/mouse/elantech.c:1939-1952` 不是**读**这些属性，而是**造**它们：

```c
i2c_props[idx++] = PROPERTY_ENTRY_U32("touchscreen-size-x", info->x_max + 1);
...
if (info->x_res)
	i2c_props[idx++] = PROPERTY_ENTRY_U32("touchscreen-x-mm",
					      (info->x_max + 1) / info->x_res);
```

它把 PS/2 探测到的参数打包成 software node 属性，挂到一个合成出来的 i2c 设备上，让 `elan_i2c` 驱动去读（`drivers/input/mouse/elan_i2c_core.c:431-434`）。所以：

- **`grep` 到 `"touchscreen-size-x"` 的命中里混着生产者和消费者**，数「有几个驱动支持这个属性」必须逐个打开看方向
- 这也是 §1.1 那张表为什么用 `device_property_*` 而不是 `of_property_*` 描述核心 helper：**属性可以不来自 DT**

### §6.5 `input.yaml` 的 7 个属性：核心不读，但都不是死字

| 属性 | 消费者数（`.c` 文件） | 举例 |
| --- | --- | --- |
| `autorepeat` | 10 | `drivers/input/keyboard/gpio_keys.c:753`、`drivers/input/keyboard/adc-keys.c:166` |
| `linux,keycodes` | 11 | `drivers/input/keyboard/cap11xx.c:408`、`drivers/input/misc/iqs626a.c:829` |
| `linux,code` | 13 | `gpio_keys.c:762`、`drivers/input/misc/pm8941-pwrkey.c:322` |
| `linux,input-type` | 4 | `gpio_keys.c:771`、`drivers/input/misc/iqs7222.c:1787` |
| `poll-interval` | 7 | `drivers/input/keyboard/gpio_keys_polled.c:165`、`drivers/input/joystick/adc-joystick.c:211`；另有 `drivers/power/supply/bq24735-charger.c:473` 和 `tools/power/x86/intel-speed-select/isst-config.c:2861`（后者是同名命令行选项，不是 DT 属性） |
| `power-off-time-sec` | 2 | `drivers/input/keyboard/mtk-pmic-keys.c:139`、`drivers/input/misc/stpmic1_onkey.c:71` |
| `reset-time-sec` | **1** | `drivers/input/misc/atc260x-onkey.c:204` |

复现：

```bash
for p in autorepeat linux,keycodes linux,code linux,input-type \
         poll-interval power-off-time-sec reset-time-sec; do
  printf '%-22s → %s\n' "$p" "$(grep -rl "\"$p\"" --include=*.c . | wc -l)"
done
```

★ **`reset-time-sec` 只有一个消费者，且不在按键驱动里而在 PMIC onkey 驱动里。** 写进任何 gpio-keys 节点都不会生效。这类「通用绑定 + 单一消费者」的属性是最容易误用的一档——比死字更隐蔽，因为它真的能工作，只是只在一个芯片上能工作。

### §6.6 同名属性在不同节点上归不同代码

| 属性名 | 在 gpio-keys 子节点上 | 在矩阵键盘节点上 | 在 rmi4 子节点上 |
| --- | --- | --- | --- |
| `wakeup-source` | `drivers/input/keyboard/gpio_keys.c:776` | `drivers/input/keyboard/mt6779-keypad.c:188` | 不读 |
| `debounce-interval` | `gpio_keys.c:786-788`（双语义，§2.3） | 不读（用 `debounce-delay-ms`） | 不读 |
| `touchscreen-inverted-x` | 不读 | 不读 | `drivers/input/rmi4/rmi_2d_sensor.c:234`（`of_property_read_bool`，**不是** §3 的 helper） |
| `linux,code` | `gpio_keys.c:762` | 不读（用 `linux,keymap`） | 不读 |

**判断一个属性在你的节点上有没有效，唯一可靠的办法是先确定 compatible → 找到驱动文件 → 在那个文件里 grep 属性名。** §8 给出这条流程的完整步骤。

---

## §7 静默失效：五棵决策树

input 的 DT 属性大多由 `void` 函数或"读不到就用默认值"的 helper 处理，**失败不冒泡**。这一节把五条最常踩的路径画出来。判据全部落到行。

### §7.1 ★ 驱动根本没调 helper

这是排第一位的失效模式，因为核心那 14 个属性**没有一个是自动读的**（§1.1）。

```
DTS 写了 touchscreen-size-x，evtest 里 Max 值不对
  │
  ├─ 驱动源码里 grep touchscreen_parse_properties → 无命中
  │     → 这个驱动不支持任何 touchscreen-* 属性。DTS 白写。
  │       核心不会警告，因为核心根本没被调到。
  │
  ├─ 有命中，但行号大于同一个函数里 input_register_device() 的行号
  │     → 本树 35 个文件里没有这种写法（下面那条命令逐文件比过行号）。
  │       如果是树外驱动写成了这样：absinfo 是指针共享的，注册之后再改
  │       max 值，用户态下一次 EVIOCGABS 仍读到新值；真正会坏的是
  │       驱动自己在注册前已经用旧 max 算过缩放系数的情况。
  │
  └─ 有命中，且在同一函数内位于 input_register_device() 之前
        → 这是本树全部 36 个调用点的写法。走 §7.2
```

判定命令（把 `<driver>.c` 换成 compatible 匹配到的那个文件）：

```bash
grep -n 'touchscreen_parse_properties\|matrix_keypad_parse_properties\|matrix_keypad_build_keymap' \
     drivers/input/touchscreen/<driver>.c
```

核对"在注册之前"用这条（对每个调用者同时打印两个行号）：

```bash
for f in $(grep -rln 'touchscreen_parse_properties(' --include=*.c drivers/input/); do
  echo "$f parse=$(grep -n 'touchscreen_parse_properties(' $f | cut -d: -f1 | tr '\n' ' ')" \
       "reg=$(grep -n 'input_register_device(' $f | cut -d: -f1 | tr '\n' ' ')"
done
```

全树支持情况：**36 个调用点 / 35 个文件**调 `touchscreen_parse_properties`（35 个调用点在 `drivers/input/touchscreen/` 的 34 个文件里，另 1 个在 `drivers/input/misc/iqs626a.c:1489`），而 `drivers/input/touchscreen/` 下有 **109** 个 `.c` 文件（`ls drivers/input/touchscreen/*.c | wc -l`）——**多数触摸屏驱动不支持这组属性**。

> 上面那条 for 循环在本树有两个输出需要人眼判一下，都不是"注册后才 parse"：`drivers/input/touchscreen/goodix.c` 的 `reg` 里除了探测路径的 `:1240` 还有 `:354`，那是运行时按需注册 pen 设备的 `goodix_ts_report_pen_down()`（`:352-355`）；`drivers/input/misc/iqs626a.c` 的 `reg=1498 1708`，`:1498` 与 `parse=1489` 在同一个函数里且在其后。

### §7.2 ★ 轴位没有先声明

```
调了 touchscreen_parse_properties()，某个轴的参数还是默认值
  │
  ├─ dmesg 里有 "Parameters are specified but the axis N is not set up"
  │     → 命中 drivers/input/touchscreen.c:39-43。
  │       驱动在调 helper 之前没有 input_set_abs_params() / 
  │       input_set_capability(EV_ABS, ABS_xxx) 声明这个轴。
  │       N 是轴号：0=ABS_X, 1=ABS_Y, 24=ABS_PRESSURE,
  │       53=ABS_MT_POSITION_X, 54=ABS_MT_POSITION_Y, 58=ABS_MT_PRESSURE
  │       （include/uapi/linux/input-event-codes.h 依次为
  │         :841 / :842 / :860 / :886 / :887 / :891）
  │
  ├─ dmesg 里什么都没有，但 x/y 对、压力不对
  │     → 大概率就是上面这条，只是 dev_warn 被 loglevel 挡住了。
  │       确认：dmesg -n 8 后重新 bind 驱动。
  │
  └─ dmesg 里什么都没有，x/y 也不对
        → 走 §7.3（multitouch 形参选错了轴）
```

★ **这条 warn 是 input 子系统 DT 侧唯一一条会主动告诉你"配错了"的日志。** 其余全是静默。所以查 DT 问题第一件事是 `dmesg | grep -i 'axis\|keymap\|keycode\|button'`。

### §7.3 A 类轴和 B 类轴选错

```
touchscreen_parse_properties(input, multitouch, prop) 的第二个实参
  │
  ├─ true  → 属性落到 ABS_MT_POSITION_X / _Y / ABS_MT_PRESSURE
  │            （drivers/input/touchscreen.c:78-79、:107）
  │
  └─ false → 属性落到 ABS_X / ABS_Y / ABS_PRESSURE
```

**症状**：`evtest` 里 `ABS_MT_POSITION_X` 的 Max 是 DTS 写的值、`ABS_X` 的 Max 是驱动硬编码的（或反过来）。两套轴同时存在时（B 类协议驱动通常两套都报），只有一套被 DTS 影响。

**判定**：`grep -n 'touchscreen_parse_properties' <driver>.c` 看第二个实参。全树 36 个调用点里 **26 个传 `true`、10 个传 `false`**，两种都不罕见：

```bash
# 26
grep -rn 'touchscreen_parse_properties(' --include=*.c drivers/input/ \
  | grep -v '^drivers/input/touchscreen.c:' | grep -cw true
# 10
grep -rn 'touchscreen_parse_properties(' --include=*.c drivers/input/ \
  | grep -v '^drivers/input/touchscreen.c:' | grep -cw false
```

同一个驱动两种都用的只有 `drivers/input/touchscreen/silead.c`：`:135` 给触摸 input 传 `true`、`:175` 给 pen input 传 `false`，两个 `input_dev` 共用同一个 `data->prop`。

### §7.4 ★ `prop == NULL` 时三个 boolean 属性直接被丢掉

```
touchscreen-inverted-x / -inverted-y / -swapped-x-y 写了没反应
  │
  ├─ 驱动调 helper 时第三个实参传了 NULL
  │     → drivers/input/touchscreen.c:119-120 提前 return，
  │       三个 device_property_read_bool() 一次都不执行。
  │       坐标属性（min/size/fuzz）仍然生效——所以"一半生效一半不生效"
  │       是这条的特征症状。
  │
  └─ 传了 &ts->prop，但驱动自己 input_report_abs() 上报
        → prop->invert_x 等被填对了，没人用。
          翻转发生在 touchscreen_report_pos()（:195-204）和
          touchscreen_set_mt_pos()（:173-181）里，二者都调
          touchscreen_apply_prop_to_x_y()（:148-160）。
          驱动绕开这两个 helper，翻转就不发生。
```

判定：

```bash
# 第三个实参是不是 NULL
grep -n -A2 'touchscreen_parse_properties(' drivers/input/touchscreen/<driver>.c
# 有没有用上报 helper
grep -n 'touchscreen_report_pos\|touchscreen_set_mt_pos' drivers/input/touchscreen/<driver>.c
```

全树传 `NULL` 的调用点正好两个：`drivers/input/touchscreen/cyttsp_core.c:700`、`drivers/input/touchscreen/ad7879.c:567`（`grep -rn 'touchscreen_parse_properties(.*NULL)' --include=*.c drivers/input/`）。这两个驱动**不可能**支持那三个 boolean 属性。

上报 helper 的使用面：`touchscreen_report_pos` 29 个调用点 / 25 个驱动文件，`touchscreen_set_mt_pos` 5 个调用点 / 4 个驱动文件（均已排除定义所在的 `drivers/input/touchscreen.c`）。两者并集 29 个文件，而调 `touchscreen_parse_properties` 的是 35 个文件——**差额 6 个驱动两个上报 helper 都不用**，它们的三个 boolean 属性一律无效：

| 驱动 | parse 调用点 | 第三个实参 |
| --- | --- | --- |
| `drivers/input/touchscreen/ad7879.c` | `:567` | `NULL` |
| `drivers/input/touchscreen/cyttsp_core.c` | `:700` | `NULL` |
| `drivers/input/touchscreen/hideep.c` | `:808` | `&ts->prop` |
| `drivers/input/touchscreen/s6sy761.c` | `:444` | `&sdata->prop` |
| `drivers/input/touchscreen/stmfts.c` | `:667` | `&sdata->prop` |
| `drivers/input/touchscreen/zet6223.c` | `:211` | `&ts->prop` |

后四个是最隐蔽的形态：`prop` 传对了、字段填对了、**没有任何代码读它**。重跑：

```bash
for f in $(grep -rln 'touchscreen_parse_properties(' --include=*.c drivers/input/ \
           | grep -v '^drivers/input/touchscreen.c$'); do
  grep -q 'touchscreen_report_pos\|touchscreen_set_mt_pos' $f || echo $f
done
```

### §7.5 `dev.parent` 为空 / absinfo 分配失败

```
touchscreen_parse_properties() 什么都没做，也没有日志
  │
  ├─ input->dev.parent == NULL
  │     → :68 取 dev = input->dev.parent 之后没有 NULL 检查。
  │       后面所有 device_property_*(dev, …) 拿到 NULL dev，全部返回错误，
  │       于是每个属性都走"用默认值"分支，data_present 全 false，
  │       连 touchscreen_set_params() 都不会被调 → 零日志。
  │       成因：用了 input_allocate_device() 却忘了设 dev.parent。
  │       （devm_input_allocate_device() 不会有这个问题，
  │         drivers/input/input.c:2091 帮你设好了。）
  │
  └─ input_alloc_absinfo() 失败
        → :74-76，OOM 时静默 return。生产环境基本碰不到，
          但它是这个函数第二个无日志退出点。
```

对比：`matrix_keypad_build_keymap()` 在同一件事上是**有**检查的——`drivers/input/matrix-keymap.c:161-162` 的 `WARN_ON(!input_dev->dev.parent)` 会打栈回溯并返回 `-EINVAL`。**同一个子系统里两个 helper 对同一个前置条件一个 WARN 一个静默**，这是 §1.1 那张表下面那句"中间层的失败模式最一致地表现为设备注册成功、参数是错的"的根源。

### §7.6 五棵树汇总

| 失效模式 | 有日志吗 | 特征症状 | 判据行 |
| --- | --- | --- | --- |
| 驱动没调 helper | 无 | 所有 `touchscreen-*` 全无效 | 驱动源码 grep 无命中 |
| 轴位没先声明 | **有**（`dev_warn`） | 某一个轴无效 | `drivers/input/touchscreen.c:39-43` |
| A/B 类轴选错 | 无 | 两套轴里只有一套被影响 | `touchscreen.c:78-79`、`:107` |
| `prop == NULL` | 无 | 坐标生效、三个 boolean 无效 | `touchscreen.c:119-120` |
| 驱动绕开上报 helper | 无 | 三个 boolean 无效，坐标也生效 | `touchscreen.c:148-160` 无调用者 |
| `dev.parent` 为空 | 无 | 所有属性无效，且**无 warn** | `touchscreen.c:68` |
| gpio-keys：`debounce-interval` 语义反转 | 无 | 按键会自动松开 / 消抖没生效 | `gpio_keys.c:566-581` vs `:636` |
| 矩阵：platform_data 抢占 DT | 无 | `linux,keymap` 改了没反应 | `matrix-keymap.c:181-194` |
| rmi4：子节点名拼错 | 无 | 该 function 的 `syna,*` 全无效 | `rmi_bus.c:161-163` |

## §8 定位：三层

DT 侧的问题按"能在哪一层看见"分三层。**顺序不能颠倒**——第一层不过关时后两层的观察全是误导（代码没编进去，DTB 写得再对也不会有人读）。

| 层 | 看什么 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| §8.1 编译期 | `.config` / `Makefile` | 消费属性的代码在不在内核里 | 属性有没有被读到 |
| §8.2 运行期静态 | `/sys/firmware/devicetree/base` | DTB 里到底有哪些节点和属性、值是多少 | 有没有人去读 |
| §8.3 运行期动态 | `/proc/bus/input/devices`、`/sys/class/input/*/capabilities/*`、`evtest` | 属性有没有落到 `input_dev` | 落错了是哪一条路径（回 §7） |

### §8.1 编译期：三个入口的编译门不一样

| 入口 | 编进哪个模块 | Kconfig 门 |
| --- | --- | --- |
| `touchscreen_parse_properties()` 等 3 个 | `input-core.o` | **无独立开关**：`drivers/input/Makefile:8-10` 把 `touchscreen.o` 无条件加进 `input-core-y`，只要 `CONFIG_INPUT` 打开就有 |
| `matrix_keypad_parse_properties()` / `matrix_keypad_build_keymap()` | `matrix-keymap.o` | `CONFIG_INPUT_MATRIXKMAP`（`drivers/input/Makefile:14`） |
| rmi4 的 `syna,*` 解析 | `rmi4/` 整个目录 | `CONFIG_RMI4_CORE`（`drivers/input/Makefile:32`） |

★ **`CONFIG_INPUT_MATRIXKMAP` 不需要手动打开。** `drivers/input/Kconfig:67-73` 的 help 明说"This option is only useful for out-of-tree drivers since in-tree drivers select it automatically"，例如 `CONFIG_KEYBOARD_MATRIX` 在 `drivers/input/keyboard/Kconfig:302` 就是 `select INPUT_MATRIXKMAP`。所以 in-tree 矩阵驱动打开了、`linux,keymap` 还不生效，方向不是这个开关。

真正要查的是**驱动自己的开关**，以及它上面那层依赖：

```bash
# 以 gpio-keys 为例
grep -E 'CONFIG_KEYBOARD_GPIO|CONFIG_INPUT_KEYBOARD|CONFIG_GPIOLIB' /path/to/.config
# 以 goodix 为例
grep -E 'CONFIG_TOUCHSCREEN_GOODIX|CONFIG_INPUT_TOUCHSCREEN|CONFIG_I2C' /path/to/.config
```

两层门都要过：`drivers/input/Makefile:23` / `:27` 用 `CONFIG_INPUT_KEYBOARD` / `CONFIG_INPUT_TOUCHSCREEN` 决定**整个子目录**是否进构建，子目录里再看单个驱动的 `CONFIG_KEYBOARD_*` / `CONFIG_TOUCHSCREEN_*`。`CONFIG_INPUT_TOUCHSCREEN=n` 时 109 个触摸屏驱动一个都不编，而 `touchscreen.o` 照样在——**核心 helper 存在不代表有人调它**（这正是 §7.1）。

★ 还有一条 arm64 上直接编不出来的路径，见 §0.2：`CONFIG_SERIO_I8042` 依赖 `ARCH_MIGHT_HAVE_PC_SERIO`，arm64 无人 select。

### §8.2 运行期静态：DTB 里到底有什么

DTS 是源，DTB 才是内核看到的东西。**中间会丢东西**：`/delete-property/`、被覆盖的 `&label {}`、没有被 include 进来的 `.dtsi`、`status = "disabled"`。所以第一步不是回头读 DTS，而是读运行中的 DTB。

```bash
# 属性在不在、值是多少（十六进制大端，u32 是 4 字节一组）
DT=/sys/firmware/devicetree/base
find $DT -name 'touchscreen-size-*' | head
hexdump -C $DT/soc/i2c@*/touchscreen@*/touchscreen-size-x

# 有 dtc 的话直接反编译回 DTS 形态，最省事
dtc -I fs -O dts $DT 2>/dev/null | less
```

`/proc/device-tree` 是同一份内容的兼容路径（指向 `/sys/firmware/devicetree/base`），两个都能用。

三件在这一层就能定死的事：

| 检查 | 命令 | 判据 |
| --- | --- | --- |
| 节点有没有被 `status` 关掉 | `cat $DT/<node>/status` | 有这个文件且内容不是 `okay`/`ok` → 设备根本不会被 probe |
| compatible 是否是驱动认的那个 | `cat $DT/<node>/compatible \| tr '\0' '\n'` | 拿到的字符串去驱动的 `of_device_id[]` 里 grep |
| 属性名有没有拼错 | `ls $DT/<node>/` | ★ 拼错的属性在 DTB 里是**存在**的，只是没人读——这一层看不出对错，只能看出"有" |

★ **rmi4 的子节点名必须在这一层核**：`ls $DT/<rmi4-node>/` 出来的名字要精确等于 `rmi4-f%02x` 形态（`drivers/input/rmi4/rmi_bus.c:156-164`，`%02x` 是小写十六进制两位），写成 `rmi4-f1`（少了前导 0）或 `rmi4-F11`（大写）都会让 `of_get_child_by_name()` 返回 `NULL`，该 function 的全部 `syna,*` 属性静默失效，见 §5.2。

### §8.3 运行期动态：属性有没有落到 `input_dev`

这一层要区分**两种观察工具，能看到的东西不一样**——这是本节最容易走错的地方。

| 观察点 | 内容 | 来源 |
| --- | --- | --- |
| `/proc/bus/input/devices` | 每个设备一段 `I:/N:/P:/S:/U:/H:` + `PROP=`/`EV=`/`KEY=`/`ABS=`… **位图** | `drivers/input/input.c:1327-1328` 注册，`input_devices_seq_show()` 生成 |
| `/sys/class/input/inputN/capabilities/{ev,key,abs,…}` | 同样的位图，一个文件一类 | `INPUT_DEV_CAP_ATTR` 宏（`drivers/input/input.c:1610-1621`）+ `input_dev_caps_attr_group`（`:1646-1649`，`.name = "capabilities"`） |
| `evtest /dev/input/eventN` | 每条 `ABS_*` 的 **min / max / fuzz / flat / resolution** | 走 `EVIOCGABS` ioctl，读的是 `input_dev->absinfo[]` |

★★ **位图只回答"这个轴存不存在"，不回答"参数对不对"。** `touchscreen-size-x` 落没落进 `absinfo` 只有 `evtest`（或任何发 `EVIOCGABS` 的程序）能看见。反过来，`ABS=` 位图恰好是 §7.2 那条 warn 的静态版本——轴位没声明时对应的 bit 就是 0：

```bash
# 这个设备声明了哪些 ABS 轴（位图，从 bit0 起，每 64 bit 一组、组间空格、高位在前）
cat /sys/class/input/input3/capabilities/abs
# 这个设备每条轴的实际 min/max（要看参数就必须用这个）
evtest /dev/input/event3
```

判读位图不必手算：`ABS_X`=0、`ABS_Y`=1、`ABS_PRESSURE`=24（`0x18`）、`ABS_MT_POSITION_X`=53（`0x35`）、`ABS_MT_POSITION_Y`=54（`0x36`）、`ABS_MT_PRESSURE`=58（`0x3a`），对应 `include/uapi/linux/input-event-codes.h:841 / :842 / :860 / :886 / :887 / :891`。只报 A 类轴还是只报 B 类轴，直接对上 §7.3。

另外两个这一层专有的观察点：

- **`gpio-keys` 有自己的 sysfs**：`keys` / `switches` / `disabled_keys` / `disabled_switches`（`drivers/input/keyboard/gpio_keys.c:321`、`:322`、`:350`、`:353`）。`keys` 列出实际注册成功的 keycode——**DTS 里写了 5 个子节点、这里只有 4 个**，说明有一个在 `gpio_keys_setup_key()` 里失败了，回 §8.4 查日志。
- **`H: Handlers=` 那一行**：空的说明没有任何 handler 绑上去，此时 `/dev/input/event*` 不存在，`evtest` 无从下手。这不是 DT 问题（DT 侧永远看不到 handler，见 §0.3），是 `CONFIG_INPUT_EVDEV` 没打开。

### §8.4 错误消息 → 原因

下表全部是本树逐字 grep 到的格式串（`%s`/`%d` 原样保留）。**dmesg 里看到的是它们展开后的样子**，所以搜索时截取不含 `%` 的片段。

| dmesg 片段 | 出处 | DT 侧原因 | 去哪一节 |
| --- | --- | --- | --- |
| `Parameters are specified but the axis N is not set up` | `drivers/input/touchscreen.c:41-42` | 驱动没在调 helper 之前声明这条轴 | §7.2 |
| `number of keypad rows/columns not specified` | `drivers/input/matrix-keymap.c:61` | 缺 `keypad,num-rows` 或 `keypad,num-columns` | §4.2 |
| `missing or malformed property linux,keymap: -N` | `matrix-keymap.c:86-87` | `linux,keymap` 不存在，或不是 u32 数组 | §4.2 |
| `linux,keymap size overflow (N vs max M)` | `matrix-keymap.c:92-93` | keymap 条目数 > `rows << row_shift` | §4.4 |
| `failed to read linux,keymap property: -N` | `matrix-keymap.c:103-104` | 数组读取本身失败（长度对不上等） | §4.2 |
| `matrix_keypad_map_key: invalid keymap entry 0xNNNN (row: R, col: C, rows: X, cols: Y)` | `matrix-keymap.c:33-34` | 某个 `MATRIX_KEY()` 的 row/col 超出声明的 rows/cols | §4.3 |
| `Unable to allocate memory for keymap` | `matrix-keymap.c:169-170` | OOM，与 DT 无关（注意这条**没有** `\n`） | — |
| `Button without keycode` | `drivers/input/keyboard/gpio_keys.c:764` | 某个 button 子节点缺 `linux,code` | §2.3 |
| `Found button without gpio or irq` | `gpio_keys.c:625` | 子节点既没 `gpios` 也没 `interrupts` | §2.3 |
| `Only EV_KEY allowed for IRQ buttons.` | `gpio_keys.c:632` | 纯 IRQ 按键写了 `linux,input-type = <5>`（`EV_SW`）之类 | §2.3 |
| `missing child device node for entry N` | `gpio_keys.c:869-871` | `gpios` 数组长度 > 子节点个数 | §2.3 |
| `failed to get gpio: -N` | `gpio_keys.c:536-537` | `gpios` 指向的 controller/pin 有问题（`-EPROBE_DEFER` 时**不打印**） | §2.3 |
| `Unable to get irq number for GPIO N, error -M` | `gpio_keys.c:589-591` | GPIO 不支持中断且没写 `poll-interval` → 该用 `gpio-keys-polled` | §2.4 |
| `Unable to claim irq N; error -M` | `gpio_keys.c:675-676` | `interrupts` 与别的设备冲突，或 trigger 类型不兼容 | §2.3 |
| `Unable to register input device, error: -N` | `gpio_keys.c:891-892` | 注册阶段失败，前面必然还有一条更具体的 | — |
| `touchscreen-average-samples (N) must be 1, 4, 8, 16 or 32` | `drivers/input/touchscreen/imx6ul_tsc.c:497-499` | 该属性在这个驱动里只收 5 个值，其余 `-EINVAL` | §6.3 |
| `can't get touchscreen-x-plate-ohms property` | `drivers/input/touchscreen/resistive-adc-touch.c:192` | 这个驱动把该属性当**必选** | §6.2 |
| `can't get touchscreen-min-pressure property.` | `resistive-adc-touch.c:234` | 可选，`dev_dbg` 级——**默认 loglevel 下看不见** | §6.2 |
| `only up to N top row keys is supported (M specified)` | `drivers/input/keyboard/cros_ec_keyb.c:536-538` | `function-row-physmap` 条目过多，**被截断而不是报错** | §4.7 |
| `failed to parse function-row-physmap property: -N` | `cros_ec_keyb.c:546-548` | 同上属性读取失败，`dev_warn` 级，probe 继续 | §4.7 |
| `Absolute device without dev->absinfo, refusing to register` | `drivers/input/input.c:2403-2405` | 声明了 `EV_ABS` 却没有任何 `input_set_abs_params()`。**不是 DT 问题**，但触摸屏调试时常撞上 | §7.2 |

三条读表时容易掉的坑：

★ **`number of keypad rows/columns not specified` 全树有 3 个来源**：`drivers/input/matrix-keymap.c:61`、`drivers/input/keyboard/samsung-keypad.c:264`、`drivers/input/keyboard/matrix_keypad.c:423`。只凭这句话不能确定是哪一层在抱怨，要看 dmesg 里前缀的设备名。同理 `Button without keycode` 除 `gpio_keys.c:764` 外还有 `drivers/input/keyboard/qt1050.c:362`。

★ **`Failed to get %s value: %d`（`drivers/input/rmi4/rmi_bus.c:422-423`）在本树永远不会出现。** 20 个调用点全部传 `optional = 1`，那个 `if` 的条件恒假（§5.4）。看到"rmi4 没报错"不等于"属性读到了"。

★ **`dev_dbg` 的两条（`resistive-adc-touch.c:234` 那类）默认不打印。** 要么 `CONFIG_DYNAMIC_DEBUG` 下开 `echo 'file resistive-adc-touch.c +p' > /sys/kernel/debug/dynamic_debug/control`，要么该文件编译时带 `-DDEBUG`。

### §8.5 症状清单：按坏掉的范围分组

input 没有自己的 `bus_type`（见 `02-datastructures.md` §1.0），所以"总线级"这一档换成**核心/handler 级**——影响所有 input 设备的那一类。

**A. 核心 / handler 级：所有设备都不对**

| 症状 | 最可能原因 | 第一条命令 |
| --- | --- | --- |
| `/dev/input/` 空，但 `/proc/bus/input/devices` 有内容 | `CONFIG_INPUT_EVDEV=n`，与 DT 无关 | `cat /proc/bus/input/devices \| grep Handlers` |
| `/proc/bus/input/devices` 不存在 | `CONFIG_PROC_FS=n` 或 `CONFIG_INPUT=n` | `ls -d /proc/bus/input` |
| 所有 input 设备都没出现 | `CONFIG_INPUT=n`；此时 `touchscreen.o` 也不在（§8.1） | `zcat /proc/config.gz \| grep '^CONFIG_INPUT='` |

**B. 单设备级：这一个设备完全没出来**

| 症状 | 最可能原因 | 第一条命令 |
| --- | --- | --- |
| `/proc/bus/input/devices` 里没有这个设备 | 驱动没 probe：`status = "disabled"`、compatible 不匹配、驱动没编 | `cat $DT/<node>/status; cat $DT/<node>/compatible \| tr '\0' '\n'` |
| dmesg 里有 `-EPROBE_DEFER` 反复重试 | 依赖的 GPIO/regulator/i2c 控制器还没就绪；`gpio_keys.c:535` 明确对 `-EPROBE_DEFER` **不打印** | `grep -c 'probe.*defer' <(dmesg)` |
| 设备出来了但名字是 `gpio-keys` 而不是 `label` 写的 | 缺 `label` 属性，走了驱动默认名 | `ls $DT/<node>/label` |

**C. 参数级：设备出来了，值不对**

★ 这一档是本篇的主体，也是**唯一一档"看起来一切正常"的**——`/proc/bus/input/devices` 有条目、`/dev/input/eventN` 能打开、能收到事件，只是坐标/键值错。

| 症状 | 最可能原因 | 去哪一节 |
| --- | --- | --- |
| 所有 `touchscreen-*` 全无效 | 驱动没调 `touchscreen_parse_properties()`（`drivers/input/touchscreen/` 的 109 个文件里只有 34 个调了） | §7.1 |
| 坐标 min/size 生效，三个 boolean 无效 | 第三实参传 `NULL`，或驱动绕开上报 helper（6 个驱动） | §7.4 |
| 只有一套轴（A 类或 B 类）被影响 | `multitouch` 实参选反 | §7.3 |
| `ABS_PRESSURE` 的 min 永远是 0 | `touchscreen.c:117` 把 `minimum` 写死成字面 0 | §3.2 |
| `size-x` 写 720，`evtest` 的 Max 是 719 | 设计如此：`touchscreen.c:92` 传 `maximum - 1` | §3.3 |
| 某个轴的参数是默认值，dmesg 有 axis warn | 轴位没先声明 | §7.2 |
| `linux,keymap` 改了没反应 | 驱动传了 `keymap_data`（platform_data 抢占 DT） | §4.5 |
| 按键会自己松开 / 消抖没生效 | `debounce-interval` 在两条路径上语义不同 | §2.3 |
| `wakeup-event-action` 无效 | 纯 IRQ 按键：整段在 `if (bdata->gpiod)` 里 | §2.3 |
| `syna,doze-*-ms` 的值对不上 | 名字骗人：一个 `×100` 一个 `÷10` | §5.3 |
| 写了 `touchscreen-y-plate-ohms` / `linux,fn-keymap` | 死字，全树 0 个消费者 | §6.2 |

**D. 拓扑级：设备之间的关系不对**

| 症状 | 最可能原因 | 去哪一节 |
| --- | --- | --- |
| rmi4 某个 function 的 `syna,*` 全无效，其余 function 正常 | 子节点名不是精确的 `rmi4-f%02x` | §5.2、§8.2 |
| 多点触控只报 1 个点 | `syna,sensor-type` / slot 数不是 DT 属性能改的，回驱动 | §5.3 |
| `gpio-keys` 只出来一半按键 | 子节点个数与 `gpios` 数组长度不一致（`gpio_keys.c:869-871`） | §2.3 |
| 同一块板上两个触摸屏，只有一个受 DTS 影响 | 两个 `input_dev` 共用一个 `prop`（如 `silead.c:135`/`:175`） | §7.3 |
| `input_dev` 的 `dev.parent` 为空 → 所有属性无效且零日志 | 驱动用了 `input_allocate_device()` 没设 parent | §7.5 |

## 本篇引用的其他文档

| 你想知道 | 去 |
| --- | --- |
| 子系统定位、概念模型、端到端路径 | `00-overview.md` |
| `touchscreen_parse_properties` 等 helper 的签名和错误码 | `01-api.md` |
| `input_absinfo` / `touchscreen_properties` 每个字段的含义 | `02-datastructures.md` |
| 属性写进 `absinfo` 之后事件怎么走完全程、注册时间线在哪一步读 DT | `03-internals.md` |
| rmi4 作为独立总线的整体结构、evdev 怎么把 `absinfo` 交给用户态 | `04-extensions.md` |
| 驱动侧调用这些 helper 的代码骨架、近邻符号区分 | `05-usage.md` |
| tracepoint、dynamic_debug、sysfs 全清单、错误码分诊 | `07-debug.md` |
| 为什么 DT 属性要由驱动显式去读、槽位协议为什么长这样 | `08-feynman.md` |
| 本篇每条论断的出处与未验证项 | `99-evidence.md` |
