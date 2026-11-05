---
title: 总览
---

# GPIO 子系统总览

> 内核版本：linux-6.1.158（`git describe` 见 `99-evidence.md` §1）
> 目标架构：**arm64 only**。ACPI 相关代码与文档**不在本文档集范围内**（裁剪清单见 `99-evidence.md` §2.7）
> 本篇定位：**复习入口**。隔几个月回来只读这一篇就应该能恢复全貌；需要细节再进后面九篇。

---

## §1 这个子系统是干什么的

GPIO = General Purpose Input/Output，通用输入输出。一根 GPIO 线就是芯片上一个「可以被软件读成 0/1，或者被软件驱动成 0/1」的引脚。没有协议、没有时序、没有数据包——**一根线一个比特**，这是它和 i2c/spi 这类总线子系统最根本的区别。

内核里的 gpiolib 要解决的问题不是「怎么收发数据」，而是：

| 问题 | gpiolib 的答案 |
|---|---|
| 板子上有 5 个 GPIO 控制器（SoC 内 3 个 bank + 一个 I2C 扩展器 + 一个 PMIC），消费者驱动怎么统一访问？ | 抽象出 `struct gpio_chip`（提供方契约）和 `struct gpio_desc`（一根线的句柄） |
| LED 驱动要「点亮」，但板子上这根线是低电平点亮的，谁负责取反？ | 描述符里记 `FLAG_ACTIVE_LOW`，`gpiod_set_value()` 自动取反；想要裸值就用 `_raw_` 变体 |
| 消费者驱动怎么知道自己该用哪根线？ | 三条查找路径：DT（`<con_id>-gpios`）、ACPI（本文档集不涉及）、板级查找表（`gpiod_add_lookup_table`） |
| GPIO 线能当中断用，但它不是中断控制器的一条 IRQ line | `gpio_irq_chip` 把 gpiochip 挂成 irqdomain，两种拓扑：级联（cascaded）和层级（hierarchical） |
| 用户态要拨一根线（调试、工业 IO） | `/dev/gpiochipN` 字符设备（v1/v2 两版 ABI）+ 已废弃的 `/sys/class/gpio` |
| 一根线被两个驱动同时抢会怎样？ | 描述符上的 `FLAG_REQUESTED` 位，`test_and_set_bit` 失败就 `-EBUSY` |

### §1.1 子系统类型判定

按 skill 的三分法（硬件总线型 / 框架型 / 协议型），gpio 是**框架型，但带一条退化的总线**：

```
$ grep -n 'struct bus_type' drivers/gpio/gpiolib.c
61:static struct bus_type gpio_bus_type = {
```

有 `bus_type`，但它上面只挂了**一个 driver**，而这个 driver 的 `.probe` 直接 `return 0` 什么都不做：

```c
static int gpio_stub_drv_probe(struct device *dev)
{
	/*
	 * The DT node of some GPIO chips have a "compatible" property, but
	 * never have a struct device added and probed by a driver to register
	 * the GPIO chip with gpiolib. In such cases, fw_devlink=on will cause
	 * the consumers of the GPIO chip to get probe deferred forever because
	 * they will be waiting for a device linked to the GPIO chip's DT node
	 * to probe.
	 * ...
	 */
	return 0;
}
```
（`drivers/gpio/gpiolib.c:4426-4441`）

所以**不要**把它当 i2c/spi 那样的「控制器在父总线、设备在子系统总线」的形状去理解。真正的形状是：

- `gpio_chip`（提供方）由**它自己的驱动**注册，那个驱动挂在**任意**父总线上——platform、amba、i2c、spi、mfd 都有实例（例：PL061 挂在 **amba** 总线上，`drivers/gpio/gpio-pl061.c:428-438`）；
- `gpio_device`（内核内部状态容器）的 `dev.bus = &gpio_bus_type`（`gpiolib.c:619`），但这条总线的唯一作用是给 `fw_devlink=on` 一个「已绑定」的假象；
- **消费者**不是总线上的 device，而是通过 `gpiod_get()` 这类 API 直接拿描述符。

这条「退化总线」的完整故事在 `02-datastructures.md` §1.4，费曼版解释在 `08-feynman.md`。

---

## §2 硬件背景：一根 GPIO 线在硅片上是什么

这一节不看内核代码，只讲硬件。理解了这些，后面很多看起来奇怪的 API（为什么有 open-drain 模拟、为什么 `gpiod_get_direction()` 对某些线返回 0、为什么 bias 和 drive 是两组独立的标志）就都顺了。

### §2.1 最小的 GPIO 单元

一个典型的 SoC GPIO 单元，每根线背后是这么一套东西：

```
                          VDD
                           |
                       ┌───┴───┐
              ┌────────┤ P-MOS │   （上拉驱动管）
              │        └───┬───┘
              │            │
  OUT_DATA ───┤ 驱动逻辑    ├────●────────────►  PAD（芯片外的焊盘/引脚）
              │            │    │
              │        ┌───┴───┐ │
              └────────┤ N-MOS │ │  （下拉驱动管）
                       └───┬───┘ │
                          GND    │
                                 │
                          ┌──────┴──────┐
   IN_DATA  ◄─────────────┤  输入缓冲器  │
                          └──────┬──────┘
                                 │
                    ┌────────────┴────────────┐
                    │  bias 网络（可选）        │
                    │  ├─ 上拉电阻 → VDD        │
                    │  ├─ 下拉电阻 → GND        │
                    │  └─ 全断开（高阻）         │
                    └─────────────────────────┘
```

寄存器层面，最少需要三个：

| 寄存器 | 作用 | 对应内核回调 |
|---|---|---|
| DIR（方向） | 每 bit 决定这根线是输入还是输出 | `.direction_input` / `.direction_output` / `.get_direction` |
| DAT-OUT（输出数据） | 输出模式下，每 bit 是要驱动的电平 | `.set` |
| DAT-IN（输入数据） | 读回 PAD 上的**实际**电平 | `.get` |

PL061 是这个模型最干净的实例（`drivers/gpio/gpio-pl061.c:28-37`）：`GPIODIR` 是方向寄存器，数据寄存器用的是 PrimeCell 的地址位选通技巧（`base + BIT(offset+2)`，`:114`、`:121`），所以它连读写掩码都不用。

### §2.2 输入 / 输出 / 高阻：三种状态，不是两种

软件里习惯说「方向是输入或输出」，硬件上其实是三态：

| 状态 | P-MOS | N-MOS | PAD 上发生什么 |
|---|---|---|---|
| 输出高 | 导通 | 断开 | 芯片主动把 PAD 拉到 VDD，能灌电流 |
| 输出低 | 断开 | 导通 | 芯片主动把 PAD 拉到 GND，能吸电流 |
| 输入（高阻 / Hi-Z） | 断开 | 断开 | 芯片**不驱动**，PAD 电平由外部决定 |

「输入」= 两个驱动管都关掉。这一点是理解 open-drain 模拟的钥匙（§2.4）。

### §2.3 bias：输入时谁来决定电平

一根配成输入的线，如果外面什么都没接，PAD 上是**浮空**的——读回来的值随机跳变，还会因为输入缓冲器工作在阈值附近而漏电。所以硬件通常提供 bias（偏置）网络：

- **pull-up**：一个几十 kΩ 的电阻接到 VDD。没人驱动时读回 1；外部只要能把它拉低（比如一个按钮接地）就读回 0。
- **pull-down**：电阻接到 GND，默认读 0。
- **disabled**：断开，真正浮空。
- **bus-hold / keeper**：少见，弱反馈保持上一个电平。内核 GPIO 的 UAPI 只暴露前三种（`include/uapi/linux/gpio.h:81-83`）。

bias 是**输入侧**的属性，drive（§2.4）是**输出侧**的属性——这两组在内核里是独立的标志位，不要混。GPIO 核心通过 pinctrl 去设 bias：`gpio_set_bias()` 把 `FLAG_BIAS_*` 翻译成 `PIN_CONFIG_BIAS_PULL_UP` 之类的 pinconf 参数（细节见 `03-internals.md` Part B）。**如果 SoC 的 pinctrl 驱动不支持这些参数，DT 里写的 bias 标志就是死字**——这是 `06-devicetree.md` 里点名的静默失效陷阱之一。

### §2.4 open-drain / open-source：为什么需要，怎么模拟

**open-drain（集电极开路 / 漏极开路）**：只保留下拉管，上拉管永远断开。

```
       外部上拉电阻（板子上焊的，或者别的芯片提供）
       VDD ──[R]──┐
                  │
    多个器件的 OD 输出都接到这里
                  │
   芯片A ──┤N-MOS├─┤
   芯片B ──┤N-MOS├─┤
   芯片C ──┤N-MOS├─┘
```

- 谁都不拉 → 电阻把线拉高 → 读到 1
- 任何一个拉低 → 整条线被拉低 → 读到 0

这就是**线与（wired-AND）**，也是 i2c 的 SDA/SCL 必须 open-drain 的原因：多个 master/slave 共享一根线而不会互相短路（一个输出高、一个输出低会烧管子）。中断汇总线（多个器件的 IRQ 汇成一根）也用这个。

**关键：很多 SoC 的 GPIO 单元没有 open-drain 硬件支持。** 内核的办法是用「输入 = 高阻」来假装：

```c
static void gpio_set_open_drain_value_commit(struct gpio_desc *desc, bool value)
{
	...
	if (value) {
		ret = gc->direction_input(gc, offset);   /* 高阻，让外部上拉把线拉高 */
	} else {
		ret = gc->direction_output(gc, offset, 0); /* 主动拉低 */
		...
	}
```
（`drivers/gpio/gpiolib.c:2898-2916`）

**open-source（源极开路）**是镜像版：只保留上拉管，靠外部下拉电阻，得到线或（wired-OR）。模拟方式对称（`gpiolib.c:2923-2941`）。

这个模拟机制带来两个直接后果，在后面的文档里会反复出现：

1. `gpiod_get_direction()` 对一条「open-drain 且当前是输出」的线**直接返回 0（输入）**而不去问硬件，否则模拟出的高阻状态会被误判成方向改了：
   ```c
   /* Open drain emulation using input mode may incorrectly report
    * input here, fix that up.
    */
   if (test_bit(FLAG_OPEN_DRAIN, &desc->flags) &&
       test_bit(FLAG_IS_OUT, &desc->flags))
	   return 0;
   ```
   （`gpiolib.c:224-229`）
2. open-drain/open-source 的线**不能走数组快速路径**（`.set_multiple` 一次写一个寄存器），必须逐根设——因为「设 1」实际上是改方向而不是改数据（`gpiolib.c:3057-3060`）。

### §2.5 中断能力：GPIO 是中断源，但不是中断控制器

一根 GPIO 线要能产生中断，硬件上需要在输入路径后面再加一套边沿/电平检测逻辑：

```
  PAD ──► 输入缓冲 ──► 边沿检测（IS/IBE/IEV 配置） ──► 状态位（RIS）
                                                        │
                                              ┌─────────┴─────────┐
                                              │  屏蔽（IE）        │
                                              └─────────┬─────────┘
                                                        │ 或起来
                                                        ▼
                                            一根（或几根）送给 GIC 的中断线
```

PL061 的四个寄存器正好对应（`gpio-pl061.c:29-35`）：
- `GPIOIS`：0 = 边沿检测，1 = 电平检测
- `GPIOIBE`：1 = 双边沿（此时 IEV 被忽略）
- `GPIOIEV`：边沿/电平的极性
- `GPIOIE`：屏蔽位
- `GPIORIS` / `GPIOMIS`：原始 / 屏蔽后的状态
- `GPIOIC`：写 1 清（边沿中断需要 ACK，电平中断不需要——这一点 PL061 的注释写得很清楚，`:262-269`）

硬件上有两种连接方式，直接决定内核用哪套 irqdomain 模型：

| 硬件形态 | 内核模型 | 关键字段 |
|---|---|---|
| 整个 bank 的中断**或**成一根线送 GIC，软件必须读状态寄存器才知道是哪根线 | **级联（cascaded）** | `girq->parent_handler`、`girq->parents[]` |
| 每根 GPIO 线在 GIC/上级中断控制器里**各有**一个独立的 SPI 号 | **层级（hierarchical）** | `girq->parent_domain`、`girq->child_to_parent_hwirq` |

PL061 是级联的典型（`gpio-pl061.c:212-230`：`chained_irq_enter` → 读 `GPIOMIS` → `for_each_set_bit` → `generic_handle_domain_irq`）。Tegra186 是层级的典型（`drivers/gpio/gpio-tegra186.c:862-903`：设 `irq->fwnode`、`child_to_parent_hwirq`、`populate_parent_alloc_arg`，最后 `irq->parent_domain = irq_find_host(np)`）。

在本树 arm64 上层级模型的提供方一共这么几个：

```
$ grep -rln 'parent_domain' drivers/gpio/ drivers/pinctrl/ --include=*.c
drivers/pinctrl/renesas/pinctrl-rzg2l.c
drivers/pinctrl/qcom/pinctrl-spmi-gpio.c
drivers/pinctrl/qcom/pinctrl-msm.c
drivers/gpio/gpiolib.c            ← 核心自己
drivers/gpio/gpio-tegra186.c
drivers/gpio/gpio-thunderx.c
drivers/gpio/gpio-visconti.c
drivers/gpio/gpio-tegra.c
drivers/gpio/gpio-msc313.c
```

绝大多数驱动是级联的。两种模型的完整对照见 `04-extensions.md` §2。

### §2.6 GPIO 和 pinctrl 的硬件分工

同一个物理引脚，往往可以被复用（mux）成 UART TX、I2C SDA、PWM 或者 GPIO。谁管这个复用？

- **pinctrl** 管：引脚复用（mux）、电气参数（drive strength、bias、slew rate）
- **gpiolib** 管：这根线被选成 GPIO 功能之后的方向和数值

两者靠 `gpio-ranges` 属性对接：GPIO 控制器的 DT 节点里写一条 `gpio-ranges = <&pinctrl 起始GPIO 起始PIN 数量>`，核心在注册时把它解析成 pin range（`drivers/gpio/gpiolib-of.c:893-977`），之后 `gpiochip_generic_request()` 就会先向 pinctrl 申请这个 pin（`gpio_request_enable`）。PL061 就是这么做的：`pl061->gc.request = gpiochip_generic_request`（`gpio-pl061.c:323`）。

一部分 SoC 干脆把两者做在一个驱动里（Qualcomm 的 `pinctrl-msm.c`、Renesas 的 `pinctrl-rzg2l.c`），此时 `gpio_chip` 是 pinctrl 驱动注册的，不在 `drivers/gpio/` 下。**这是「全树 grep」在 gpio 子系统上特别重要的原因：提供方大量住在 `drivers/pinctrl/` 里。**

### §2.7 板级现实：这些线接的是什么

看几个 in-tree arm64 板子的真实用法，建立「GPIO 到底用来干什么」的直觉：

```dts
/* arch/arm64/boot/dts/renesas/hihope-common.dtsi:150-157
   一根固定输出的复位线：板子一上电就必须拉低，没有任何驱动会去 request 它 */
&gpio6 {
	usb1-reset-hog {
		gpio-hog;
		gpios = <10 GPIO_ACTIVE_LOW>;
		output-low;
		line-name = "usb1-reset";
	};
};
```

```dts
/* arch/arm64/boot/dts/amlogic/meson-axg-jethome-jethub-j1xx.dtsi:315-327
   给线起名字，用户态 gpioinfo 能看到，脚本按名字而不是编号找线 */
&gpio {
	gpio-line-names =
		"", "", "", "", "",              // 0 - 4
		"", "", "", "", "",              // 5 - 9
		"UserButton", "", "", "", "",    // 10 - 14
		...
		"", "LedRed", "LedGreen", "Output3", "Output2", // 25 - 29
```

```dts
/* arch/arm64/boot/dts/hisilicon/hi6220.dtsi:461-471
   一个标准的 PL061 bank：既是 gpio-controller 又是 interrupt-controller */
gpio0: gpio@f8011000 {
	compatible = "arm,pl061", "arm,primecell";
	reg = <0x0 0xf8011000 0x0 0x1000>;
	interrupts = <0 52 0x4>;
	gpio-controller;
	#gpio-cells = <2>;
	interrupt-controller;
	#interrupt-cells = <2>;
	clocks = <&ao_ctrl 2>;
	clock-names = "apb_pclk";
};
```

典型用途归纳：

| 用途 | 方向 | 常见修饰 |
|---|---|---|
| 复位线（PHY reset、USB hub reset、传感器 reset） | 输出 | 多数 active-low；常用 hog 或 `reset-gpios` |
| 使能/电源开关（regulator enable、LDO enable） | 输出 | `enable-gpios`；注意 `enable-active-high` 这个历史包袱（§2.8） |
| LED | 输出 | `gpios`，`gpio-leds` 驱动消费 |
| 按键 / 拨码开关 | 输入 + 中断 | `gpio-keys` 驱动，需要 bias pull-up |
| 卡检测（SD card detect）、耳机插入检测 | 输入 + 双边沿中断 | `cd-gpios` |
| I2C/SPI 的 bit-bang | 双向 | `gpio-sck` / `gpio-mosi` / `gpio-miso`（`gpiolib-of.c:373-395`） |
| 片选（SPI CS） | 输出 | `cs-gpios`，极性有专门的历史 quirk（§2.8） |
| 中断汇总输入（PMIC / 扩展器的 IRQ 引脚） | 输入 + 中断 | 常配 open-drain + 外部上拉 |

### §2.8 历史包袱：DT 极性标志有时会被忽略

这是纯软件问题，但根源是硬件绑定的历史，放在这里一起交代，因为它是 gpio 子系统最容易踩的坑之一。

`of_gpio_flags_quirks()`（`drivers/gpio/gpiolib-of.c:133-226`）里有**四条**针对特定 compatible 的极性覆盖规则。最刺眼的一条：

```c
/*
 * Some GPIO fixed regulator quirks.
 * Note that active low is the default.
 */
if (IS_ENABLED(CONFIG_REGULATOR) &&
    (of_device_is_compatible(np, "regulator-fixed") ||
     of_device_is_compatible(np, "reg-fixed-voltage") ||
     (!(strcmp(propname, "enable-gpio") &&
        strcmp(propname, "enable-gpios")) &&
      of_device_is_compatible(np, "regulator-gpio")))) {
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
```

也就是说：给一个 `regulator-fixed` 节点的 `gpio` 写 `GPIO_ACTIVE_LOW`，**这个标志会被丢掉**，极性完全由有没有 `enable-active-high` 决定，内核只留一条 `pr_warn`。另外三条针对 `reg-fixed-voltage` + `gpio-open-drain`、SPI 的 `cs-gpios`（`spi-cs-high` 优先，否则强制 active-low）、以及 stmmac 的 `snps,reset-gpio`。四条的完整展开在 `06-devicetree.md`。

---

## §3 概念模型：四个角色，两个方向

### §3.1 角色表

gpiolib 里的参与者只有四种，把它们分清楚，整个子系统的形状就出来了：

| 角色 | 谁扮演 | 它提供什么 | 它拿到什么 |
|---|---|---|---|
| **提供方（provider）** | GPIO 控制器驱动（`drivers/gpio/gpio-*.c`，或住在 `drivers/pinctrl/` 里的合并驱动） | 填一个 `struct gpio_chip` 并调 `devm_gpiochip_add_data()` | 核心帮它建 `gpio_device`、分配描述符、建 irqdomain、建字符设备 |
| **消费方（consumer）** | 任何驱动（LED、regulator、MMC、PHY、input…） | 一个 con_id 字符串 | 一个 `struct gpio_desc *`（或 `gpio_descs *` 数组） |
| **核心（core）** | `drivers/gpio/gpiolib*.c` | 查找、抢占检查、极性翻译、irqdomain、UAPI | — |
| **用户态** | `libgpiod` / `gpioset` / 自己写的程序 | `/dev/gpiochipN` 上的 ioctl | 线句柄 fd + 事件流 |

三个「反直觉」的点：

1. **消费方拿到的不是引脚号，是不透明句柄。** `struct gpio_desc` 的定义在 `drivers/gpio/gpiolib.h:148`，**不在任何 `include/` 头文件里**——消费者只能持有指针，不能解引用。这是有意的：`gpiolib.h:145-146` 的 kerneldoc 承诺「a pointer to a &struct gpio_desc is guaranteed to be valid until the GPIO is released」。
2. **提供方填的 `gpio_chip` 是驱动自己的内存**，核心不拷贝它，只存指针（`gdev->chip = gc`，`gpiolib.c:621`）。所以 `gpio_chip` 结构体必须活到 `gpiochip_remove()` 之后——这是 `devm_gpiochip_add_data()` 存在的原因。
3. **`gpio_device` 会比 `gpio_chip` 活得更久。** 热拔（比如 USB 上的 GPIO 扩展器被拔掉）时 `gpiochip_remove()` 把 `gdev->chip` 置 NULL，但只要用户态还持着 fd，`gpio_device` 就不能释放。这个「容器比内容活得久」的设计是 `gdev->sem` 读写信号量存在的全部理由（`gpiolib.h` 里 `struct gpio_device` 的 kerneldoc 明说了这一点）。

### §3.2 两个方向的数据流

```
   ┌───────────────────────── 方向 A：内核消费者 ──────────────────────────┐

   LED 驱动                gpiolib 核心                    控制器驱动
   ────────                ───────────                    ──────────
   gpiod_get(dev,"led",     ①查找（DT/查找表）
             GPIOD_OUT_LOW) ②gpiod_request  ─── gc->request ──► pinctrl 申请 pin
                            ③gpiod_configure_flags
                              └─ 记 ACTIVE_LOW / OD / bias
                            ④按 dflags 定方向 ─ gc->direction_output ─► 写 DIR 寄存器
        │
        ▼  拿到 desc *
   gpiod_set_value(desc,1)   ⑤极性翻译（ACTIVE_LOW → 写 0）
                             ⑥OD/OS 模拟判定
                                          ─── gc->set ─────────► 写 DAT 寄存器

   └───────────────────────────────────────────────────────────────────────┘

   ┌───────────────────────── 方向 B：中断上行 ────────────────────────────┐

   PAD 电平变化 ──► GPIO 硬件边沿检测 ──► GIC ──► arm64 中断入口
                                                      │
                              ┌───────────────────────┘
                              ▼
   级联：gc->irq.parent_handler（如 pl061_irq_handler）
              chained_irq_enter → 读状态寄存器 → for_each_set_bit
                    → generic_handle_domain_irq(gc->irq.domain, offset)
                              │
   层级：GIC 直接把 hwirq 派发到 gpiochip 的 irqdomain
                              │
                              ▼
                    gpiochip 的 irq_desc → handle_edge_irq / handle_level_irq
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
          内核消费者的 handler      cdev 的 edge_irq_handler
          （gpio-keys 等）          （硬中断里打时间戳，
                                     IRQ_WAKE_THREAD 到线程里投 kfifo）
                                            │
                                            ▼
                                    用户态 read(fd) 拿到
                                    struct gpio_v2_line_event

   └───────────────────────────────────────────────────────────────────────┘
```

### §3.3 三套「值」的语义，务必分清

同一根线的「值」，在四个地方含义不同，这是 gpio 最常见的困惑来源：

| 层次 | 名字 | 1 表示什么 |
|---|---|---|
| 硅片 | PAD 电平 | 物理高电平 |
| 寄存器 | DAT bit | 硬件手册定义（几乎总是等于 PAD 电平） |
| gpiolib raw | `gpiod_get_raw_value()` | 等于寄存器值 |
| gpiolib logical | `gpiod_get_value()` | 「逻辑有效」——`FLAG_ACTIVE_LOW` 置位时是寄存器值的**反** |

翻译只发生在**一处**，就是 `gpiod_get_value()` / `gpiod_set_value()` 这一层的 `if (test_bit(FLAG_ACTIVE_LOW, ...)) value = !value;`（读侧 `gpiolib.c:2832-2833`，写侧 `:3116-3117`）。凡是名字里带 `_raw_` 的，一律不翻译。

**两条陷阱**（`07-debug.md` 会再展开）：

- 已废弃的整数 API（`gpio_set_value()` 那一套）**全部走 raw 路径**——`include/asm-generic/gpio.h:98` 把 `__gpio_get_value` 映射到 `gpiod_get_raw_value`，`:102` 把 `__gpio_set_value` 映射到 `gpiod_set_raw_value`。用旧 API 写 active-low 的线，极性不会被处理。
- `/sys/class/gpio/gpioN/direction` 写入用的是 `gpiod_direction_output_raw()`（`gpiolib-sysfs.c:87`、`:89`），所以 sysfs 也是 raw 语义；只有 `/dev/gpiochipN` 是逻辑语义。

---

## §4 数据结构速览

完整字段和关系图在 `02-datastructures.md`，这里只留骨架和「谁指向谁」。

```
                        ┌──────────────────────────────┐
                        │  gpio_devices (全局链表)      │  gpiolib.c
                        └───────────┬──────────────────┘
                                    │ list_head
        ┌───────────────────────────▼────────────────────────────┐
        │ struct gpio_device            drivers/gpio/gpiolib.h:53│
        │  ├─ struct device dev         ← .bus = gpio_bus_type   │
        │  ├─ struct cdev chrdev        ← /dev/gpiochipN         │
        │  ├─ struct device *mockdev    ← /sys/class/gpio（废弃） │
        │  ├─ struct gpio_chip *chip ───┐  热拔时置 NULL          │
        │  ├─ struct gpio_desc *descs ──┼─┐                      │
        │  ├─ int base, u16 ngpio       │ │  DEPRECATED 全局编号  │
        │  ├─ struct rw_semaphore sem   │ │  热拔保护             │
        │  └─ blocking_notifier_head    │ │  lineinfo 变更通知    │
        └───────────────────────────────┼─┼──────────────────────┘
                                        │ │
        ┌───────────────────────────────▼─┼──────────────────────┐
        │ struct gpio_chip     include/linux/gpio/driver.h:415   │
        │  由控制器驱动持有，核心只存指针                          │
        │  ├─ 回调：request/free/get_direction/direction_input/   │
        │  │        direction_output/get/get_multiple/set/        │
        │  │        set_multiple/set_config/to_irq/dbg_show/      │
        │  │        init_valid_mask/add_pin_ranges/               │
        │  │        en_hw_timestamp/dis_hw_timestamp   (:421-461) │
        │  ├─ base / ngpio / offset / names / can_sleep           │
        │  ├─ struct gpio_irq_chip irq  ────────────────┐         │
        │  └─ valid_mask / of_node / fwnode             │         │
        └───────────────────────────────────────────────┼─────────┘
                                                        │
        ┌───────────────────────────────────────────────▼─────────┐
        │ struct gpio_irq_chip  include/linux/gpio/driver.h:41    │
        │  两种拓扑共用一个结构，看填了哪组字段：                    │
        │  级联 → parent_handler / parents[] / num_parents        │
        │  层级 → parent_domain / child_to_parent_hwirq /         │
        │          populate_parent_alloc_arg / child_offset_to_irq│
        │  共用 → domain / chip / handler / default_type /        │
        │          valid_mask / threaded / initialized            │
        └─────────────────────────────────────────────────────────┘

        ┌─────────────────────────────────────────────────────────┐
        │ struct gpio_desc      drivers/gpio/gpiolib.h:148        │
        │  一根线一个，descs[ngpio] 数组，消费者只见指针            │
        │  ├─ struct gpio_device *gdev      回指                   │
        │  ├─ unsigned long flags   ← 20 个 FLAG_* 位 (:151-170)  │
        │  ├─ const char *label     消费者名（cat debugfs 看到的） │
        │  ├─ const char *name      DT gpio-line-names 给的名字    │
        │  ├─ struct device_node *hog   （CONFIG_OF_DYNAMIC）      │
        │  └─ unsigned int debounce_period_us                     │
        └─────────────────────────────────────────────────────────┘
```

### §4.1 `flags` 位就是这根线的全部状态

`gpio_desc` 只有六个字段，其中真正承载状态的是 `flags`。20 个位分四组（`drivers/gpio/gpiolib.h:151-170`）：

| 组 | 位 | 含义 |
|---|---|---|
| 占用 | `FLAG_REQUESTED`(0) | 已被 request，第二个人来就 `-EBUSY` |
| 方向/电气 | `FLAG_IS_OUT`(1)、`FLAG_ACTIVE_LOW`(6)、`FLAG_OPEN_DRAIN`(7)、`FLAG_OPEN_SOURCE`(8)、`FLAG_TRANSITORY`(12) | |
| bias | `FLAG_PULL_UP`(13)、`FLAG_PULL_DOWN`(14)、`FLAG_BIAS_DISABLE`(15) | 三者互斥，转成 pinconf 参数下发 |
| 中断 | `FLAG_USED_AS_IRQ`(9)、`FLAG_IRQ_IS_ENABLED`(10) | 前者是「已锁为 IRQ」，后者是「IRQ 当前使能」 |
| 导出/hog | `FLAG_EXPORT`(2)、`FLAG_SYSFS`(3)、`FLAG_IS_HOGGED`(11) | 前两个由 `sysfs_lock` 保护 |
| cdev 私有 | `FLAG_EDGE_RISING`(16)、`FLAG_EDGE_FALLING`(17)、`FLAG_EVENT_CLOCK_REALTIME`(18)、`FLAG_EVENT_CLOCK_HTE`(19) | 只有字符设备路径会设，注释里明写 "GPIO CDEV" |

注意 **4、5 号位是空的**——历史上被删掉的标志留下的洞，不要以为自己数错了。

### §4.2 用户态 ABI 结构

`include/uapi/linux/gpio.h`（531 行）里有**两代**结构，v1 已废弃但仍默认编入：

| | v2（当前） | v1（废弃） |
|---|---|---|
| 线请求 | `gpio_v2_line_request` (`:196`) | `gpiohandle_request` (`:412`) / `gpioevent_request` (`:475`) |
| 读值 | `gpio_v2_line_values` (`:96`) 位图 + 掩码 | `gpiohandle_data` (`:449`) 每线一字节 |
| 线信息 | `gpio_v2_line_info` (`:224`)，13 个标志 | `gpioline_info` (`:335`)，8 个标志 |
| 事件 | `gpio_v2_line_event` (`:293`)，带 line_seqno/seqno | `gpioevent_data` (`:497`)，只有时间戳和 id |
| 一次最多几根线 | `GPIO_V2_LINES_MAX` = 64 (`:45`) | `GPIOHANDLES_MAX` = 64 (`:343`) |
| 每线属性 | `gpio_v2_line_attribute` (`:130`)：FLAGS / OUTPUT_VALUES / DEBOUNCE | 无 |
| 是否支持事件+多线 | 支持（一次请求可同时要值和边沿事件） | 不支持（handle 和 event 是两套 ioctl） |

v1 靠 `CONFIG_GPIO_CDEV_V1` 控制（`drivers/gpio/Kconfig`，`default y`，帮助文字里明写 "This ABI version is deprecated"）。**64 这个上限不是随便定的**：`:45` 的注释说明它受制于用 64 位位图传值。

---

## §5 机制速览

八个机制，每个一段。深入见括号里的文档。

**① 描述符查找的三条路径**（`03-internals.md` Part A §A4、`06-devicetree.md`）
`gpiod_get_index()`（`gpiolib.c:4084`）按 fwnode 类型分派：`is_of_node` → `of_find_gpio()`；`is_acpi_node` → `acpi_find_gpio()`（本文档集不涉及）；两者都没结果就落到 `gpiod_find()` 查板级查找表（`gpiolib.c:4098-4114`）。DT 路径会依次尝试 `<con_id>-gpios`、`<con_id>-gpio`（后缀表 `gpio_suffixes[]` 在 `gpiolib.h:81`），再试一批遗留名字。

**② 抢占检查**（`03-internals.md` Part A §A5）
`gpiod_request_commit()`（`gpiolib.c:1919`）里 `test_and_set_bit(FLAG_REQUESTED, &desc->flags)` 一把定生死，失败返回 `-EBUSY`。唯一的例外是 `GPIOD_FLAGS_BIT_NONEXCLUSIVE`——`gpiod_get_index()` 在 `-EBUSY` 时会检查这个标志并直接返回已存在的描述符，代码里自称 "It is a bit of a hack"（`gpiolib.c:4126-4136`）。

**③ 极性与电气配置的落地**（`03-internals.md` Part B）
`gpiod_configure_flags()`（`gpiolib.c:4010`）是唯一把 lookup flags 翻译成 `FLAG_*` 位的地方，紧接着调 `gpio_set_bias()`（`:2243`）把 bias 下发到 pinctrl。**注意顺序：先记标志，再设方向**——否则 `GPIOD_OUT_HIGH` 在 active-low 线上会输出错电平。

**④ 数组快速路径**（`04-extensions.md` §4）
`gpiod_get_array()` 会尝试把一批描述符压缩成一个 `struct gpio_array`（`gpiolib.h:97`），带 get/set/invert 三张掩码。命中条件很严：所有成员同一个 chip、成员 0 的 hwnum 必须是 0、每个成员的数组下标必须等于它的 hwnum。命中后 `gpiod_get/set_array_value_complex()` 直接走 `gc->get_multiple` / `gc->set_multiple` 一次寄存器读写。栈上位图大小由 `CONFIG_GPIOLIB_FASTPATH_LIMIT` 决定（默认 512，Kconfig 里警告 "setting an incorrect value could cause stack corruption"）。

**⑤ irqchip：级联 vs 层级**（`04-extensions.md` §2）
`gpiochip_add_irqchip()`（`gpiolib.c:1543`）在注册时被调用，先设 `gc->to_irq = gpiochip_to_irq`（`:1574`），然后按 `girq->parent_domain` 是否为空走 `gpiochip_hierarchy_add_domain()`（`:1213`）或建普通 irqdomain。级联又细分三种：chained（硬中断链，RT 上**不会**被强制线程化）、generic-chained、nested-threaded。

**⑥ `IRQCHIP_IMMUTABLE` 契约**（`04-extensions.md` §2.4）
历史上 gpiolib 会**修改**驱动给的 `struct irq_chip`（往里塞自己的 mask/unmask 包装）。新写法是驱动把 irq_chip 声明成 `const` + `IRQCHIP_IMMUTABLE`，并自己在 mask/unmask 里调 `gpiochip_disable_irq()` / `gpiochip_enable_irq()`，再加 `GPIOCHIP_IRQ_RESOURCE_HELPERS`（`include/linux/gpio/driver.h:640`，全树只此一处定义）。PL061 是标准范例（`gpio-pl061.c:296-305`）。

**⑦ 字符设备的两级时间戳**（`04-extensions.md` §3）
边沿事件走两段：`edge_irq_handler()` 在**硬中断上下文**里只做一件事——记 `line->timestamp_ns`（注释：as close in time as possible to the actual event），然后 `return IRQ_WAKE_THREAD`；`edge_irq_thread()` 在线程里 memset 事件结构（注释 "Do not leak kernel stack to userspace"）、判定边沿类型、投进 kfifo。软件去抖是第三条路：`debounce_irq_handler()` 用 `mod_delayed_work(system_wq, ...)` 推迟采样。

**⑧ 热拔安全**（`03-internals.md` Part B §B5）
`/dev/gpiochipN` 的每个 fops 入口都被 `down_read(&gdev->sem)` 包住，靠三个 wrapper：`call_poll_locked` / `call_ioctl_locked` / `call_read_locked`（`gpiolib-cdev.c:67-104`）。`gpio_chrdev_open()` 里如果发现 `!gdev->chip` 就直接 `-ENODEV`。**内核侧没有这层保护**：`VALIDATE_DESC` 宏走的 `validate_desc()` 在「backing chip is gone」时返回 0 而不是错误码，所以 `gpiochip_remove()` 之后内核消费者的 `gpiod_get_value()` 会**静默返回 0**。

---

## §6 端到端调用路径

三条路径，把 §5 的机制串起来。完整逐段剖析在 `03-internals.md`。

### §6.1 提供方注册（以 PL061 为例）

```
amba_probe
 └─ pl061_probe                                      gpio-pl061.c:307
     ├─ devm_ioremap_resource
     ├─ 填 pl061->gc：request/free = gpiochip_generic_*, base = -1
     ├─ 填 girq：gpio_irq_chip_set_chip(&pl061_irq_chip)
     │           parent_handler = pl061_irq_handler, num_parents = 1
     └─ devm_gpiochip_add_data(dev, &pl061->gc, pl061)
         └─ gpiochip_add_data_with_key                  gpiolib.c:595
             ├─ kzalloc(gpio_device)、dev.bus = gpio_bus_type    :616-619
             ├─ of_gpio_dev_init（继承 parent 的 of_node）
             ├─ ida_alloc → dev_set_name("gpiochip%d") → device_initialize  :631-643
             ├─ gpiochip_set_desc_names / devprop_gpiochip_set_names
             ├─ 分配 descs[ngpio]、加入 gpio_devices 全局链表
             ├─ gpiochip_add_pin_ranges → of_gpiochip_add（解析 gpio-ranges、
             │                             gpio-reserved-ranges、扫 gpio-hog）
             ├─ gpiochip_irqchip_init_valid_mask                :786
             ├─ gpiochip_add_irqchip（建 irqdomain）              :794
             └─ gpiochip_setup_dev                               :807
                 ├─ gpiolib_cdev_register → cdev_device_add（/dev/gpiochipN）
                 └─ gpiochip_sysfs_register（/sys/class/gpio/gpiochipN，若类已就绪）
```

关键顺序：**descs 先分配、pin range 再建、irqdomain 再建、字符设备最后**。字符设备最后建是必须的——它一露头用户态就能进来。

### §6.2 消费方拿线并写值（以一根 active-low 的复位线为例）

```
某驱动 probe
 └─ devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW)        gpiolib-devres.c:93
     └─ gpiod_get_index(dev, "reset", 0, GPIOD_OUT_LOW)    gpiolib.c:4084
         ├─ is_of_node(fwnode) → of_find_gpio(dev,"reset",0,&lookupflags)
         │    └─ 试 "reset-gpios" → of_get_named_gpiod_flags
         │        └─ of_xlate（默认 of_gpio_simple_xlate）→ 取 args[1] 当 flags
         │        └─ of_gpio_flags_quirks ← 四条 compatible 特例可能改极性
         ├─ gpiod_request(desc, "reset")                       :4123
         │   └─ gpiod_request_commit → test_and_set_bit(FLAG_REQUESTED)  :1919
         │       └─ gc->request → gpiochip_generic_request → pinctrl_gpio_request
         └─ gpiod_configure_flags(desc,"reset",lookupflags,dflags)   :4010
             ├─ 置 FLAG_ACTIVE_LOW（因为 DT 写了 GPIO_ACTIVE_LOW）
             ├─ gpio_set_bias                                       :2243
             └─ dflags 含 OUT → gpiod_direction_output(desc, 0)      :2416
                 ├─ FLAG_ACTIVE_LOW → value = !value = 1
                 ├─ OD/OS 判定
                 └─ gpiod_direction_output_raw_commit                :2343
                     └─ gc->direction_output → 写 DIR + DAT 寄存器
                        （PL061 这里写两次值，见 gpio-pl061.c:95,104）

后续：gpiod_set_value(desc, 1)                             gpiolib.c:3137
       └─ gpiod_set_value_nocheck                                  :3114
           ├─ FLAG_ACTIVE_LOW → 取反
           ├─ FLAG_OPEN_DRAIN → gpio_set_open_drain_value_commit
           ├─ FLAG_OPEN_SOURCE → gpio_set_open_source_value_commit
           └─ 否则 gpiod_set_raw_value_commit                       :2943
               └─ trace_gpio_value + gc->set
```

### §6.3 一次用户态边沿事件

```
用户态                          内核
──────                          ────
open("/dev/gpiochip0")   →  gpio_chrdev_open                gpiolib-cdev.c:2796
                              down_read(&gdev->sem); -ENODEV if !gdev->chip
                              bitmap_zalloc(ngpio) 建 watched_lines
                              注册 lineinfo_changed 通知块

ioctl(GPIO_V2_GET_LINE) →  call_ioctl_locked → gpio_ioctl_unlocked   :2587
                            └─ linereq_create
                                ├─ 逐线 gpiod_request_user
                                ├─ gpio_v2_line_config → 设 edflags
                                ├─ kfifo_alloc(事件缓冲，默认 num_lines*16)
                                └─ request_threaded_irq(edge_irq_handler,
                                                        edge_irq_thread)
                                   或 debounce_irq_handler（配了去抖）

  ↓ 返回一个新的 fd

（硬件产生边沿）
                            GIC → pl061_irq_handler（级联）
                              → generic_handle_domain_irq
                                → handle_edge_irq
                                  → edge_irq_handler   硬中断  :943
                                      line->timestamp_ns = ktime_get_ns()
                                      return IRQ_WAKE_THREAD
                                  → edge_irq_thread    线程    :897
                                      memset(&le, 0, sizeof(le))
                                      switch (READ_ONCE(line->edflags) &
                                              GPIO_V2_LINE_EDGE_FLAGS)
                                      linereq_put_event → kfifo_in + wake_up

poll(fd, POLLIN)         →  call_poll_locked → linereq_poll
read(fd, &event, 16*n)   →  call_read_locked → linereq_read
                              kfifo_out → copy_to_user
```

---

## §7 和其他子系统的关系

```
                   ┌──────────────────────────────────────┐
                   │  消费者（几百个驱动）                  │
                   │  leds-gpio / gpio-keys / regulator-  │
                   │  fixed / mmc / phy / spi-gpio / i2c- │
                   │  gpio / w1-gpio / gpio-fan / ...     │
                   └──────────────┬───────────────────────┘
                                  │ gpiod_get / gpiod_set_value
                                  │ gpiod_to_irq → request_irq
   ┌──────────────────────────────▼───────────────────────────────┐
   │                        gpiolib 核心                           │
   │  gpiolib.c  gpiolib-of.c  gpiolib-cdev.c                     │
   │  gpiolib-sysfs.c  gpiolib-devres.c  gpiolib-legacy.c         │
   └──┬────────┬─────────┬──────────┬──────────┬─────────┬────────┘
      │        │         │          │          │         │
      ▼        ▼         ▼          ▼          ▼         ▼
  ┌───────┐┌───────┐┌────────┐┌─────────┐┌────────┐┌──────────┐
  │pinctrl││irq    ││driver  ││ OF /    ││ cdev / ││ pm /     │
  │       ││domain ││ core   ││ fwnode  ││ sysfs  ││ suspend  │
  └───────┘└───────┘└────────┘└─────────┘└────────┘└──────────┘
```

逐条说明谁依赖谁、连接点在哪：

| 对方子系统 | 方向 | 连接点 | 说明 |
|---|---|---|---|
| **pinctrl** | gpio → pinctrl | `gpiochip_generic_request()` → `pinctrl_gpio_request()`；`gpio_set_bias()` → `pinconf` | 靠 `gpio-ranges` 建立 pin 映射（`gpiolib-of.c:893`）。**反向也存在**：很多 pinctrl 驱动自己注册 `gpio_chip` |
| **irqdomain / irqchip** | gpio → irq | `gpiochip_add_irqchip()` 建 domain；`gpiod_to_irq()` → `gc->to_irq` | 级联时 gpio 是 irq **消费者**（占用一根父 IRQ）；层级时 gpio 是中间层 domain |
| **驱动模型 / fw_devlink** | 双向 | `drivers/of/property.c:1272-1310` 的 `parse_gpio` / `parse_gpios` / `parse_gpio_compat` | fw_devlink 从 DT 属性名推导供需关系；`gpio_stub_drv` 是为它准备的兜底 |
| **OF / fwnode** | gpio → of | `gpiolib-of.c` 整个文件 | `of_xlate` 回调是提供方的扩展点 |
| **regulator** | regulator → gpio | `regulator-fixed` 的 `gpio` / `enable-gpios` | 有极性 quirk（§2.8）+ NONEXCLUSIVE 共享需求（`gpiolib.c:4126-4136` 的注释点名 "fixed regulators"） |
| **spi** | spi → gpio | `cs-gpios` | 有 `spi-cs-high` 极性 quirk |
| **i2c** | 双向 | i2c-gpio（用 GPIO 做 bit-bang I2C）；gpio-pca953x 等（I2C 上的 GPIO 扩展器） | 扩展器的 `gc->can_sleep = true`，所以只能用 `*_cansleep()` 变体访问 |
| **HTE（硬件时间戳引擎）** | gpio → hte | `gpiod_enable_hw_timestamp_ns()` → `gc->en_hw_timestamp` | `CONFIG_HTE`，本树只有 Tegra 支持 |
| **PM / suspend** | gpio → pm | `FLAG_TRANSITORY` + `gpiod_set_transitory()` | 「这根线在睡眠/复位时会丢值」的声明，转成 `PIN_CONFIG_PERSIST_STATE` |

**关于 platform 总线的定位**（对应 skill 第 12 条）：gpio 没有「控制器在 platform、设备在自己总线上」这个形状。真实形状是——

```
   ┌────────────────────────────────────────────────────────────┐
   │  控制器驱动住在它自己的父总线上（不固定！）                    │
   │                                                            │
   │  platform_bus ──── gpio-mmio / gpio-pca953x(via mfd) / ... │
   │  amba_bustype ──── gpio-pl061          ← arm64 上很常见     │
   │  i2c_bus_type ──── gpio-pca953x / gpio-pcf857x             │
   │  spi_bus_type ──── gpio-max3191x / gpio-mc33880            │
   └───────────────────────────┬────────────────────────────────┘
                               │ devm_gpiochip_add_data(dev, gc, data)
                               │ gc->parent = 上面那个 device
                               ▼
   ┌────────────────────────────────────────────────────────────┐
   │  gpio_bus_type（退化）                                       │
   │   上面只有 gpio_device（dev.bus = &gpio_bus_type，:619）      │
   │   和唯一一个空 driver gpio_stub_drv（:4443-4447）             │
   │   消费者不在这条总线上，它们只是 API 调用者                   │
   └────────────────────────────────────────────────────────────┘
```

`gc->parent` 就是那根缝合线（`gpiolib.c:620`：`gdev->dev.parent = gc->parent`）。

---

## §8 导读：按你的问题挑文档

| 我想知道… | 去哪篇 |
|---|---|
| 有哪些文件、哪个头文件、有哪些对外 API、错误码什么意思 | `01-api.md` |
| `gpio_chip` / `gpio_desc` / `gpio_irq_chip` 各字段什么用，它们怎么互指 | `02-datastructures.md` |
| 注册流程逐段发生了什么、锁怎么用、卸载路径、设计债 | `03-internals.md` |
| 字符设备 ABI、irqchip 两种拓扑、数组快速路径、sysfs、legacy 整数 API | `04-extensions.md` |
| 我要写一个 GPIO 控制器驱动 / 消费者驱动 / 用户态程序，给我骨架 | `05-usage.md` |
| DTS 怎么写、每个属性谁读、写错了什么症状 | `06-devicetree.md` |
| 打开哪个 debugfs、哪个 tracepoint、错误码怎么反查、用户态工具怎么用 | `07-debug.md` |
| 我就是没理解 `gpio_chip` 和 `gpio_device` 为什么要分开 / OD 模拟到底在干嘛 | `08-feynman.md` |
| 这些结论怎么来的、哪些没验证、行号能不能信 | `99-evidence.md` |

**推荐顺序**：写驱动 → 00 → 02 → 05 → 06 → 03；调 bug → 00 §3.3 → 07 → 06；纯理解 → 00 → 08 → 03。

