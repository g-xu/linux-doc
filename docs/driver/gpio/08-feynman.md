---
title: 费曼学习法
---

# 08 · gpio 关键概念的大白话解释

> 本篇专门解释 gpio 子系统里**最容易理解错**和**最容易被类比错**的九个问题。
>
> 每个问题四段式：**问题** → **先举个例子**（生活类比）→ **回到代码**（行号）→ **这个类比什么时候会误导你**。
>
> 第四段是本篇的核心。一个类比如果推不出误导点，说明它太泛，帮不上忙。所以每一节都会明确指出类比和代码之间的真实差异。
>
> 目标架构 arm64；ACPI 相关的概念不在本篇（裁剪范围见 99 §2.7）。
>
> 类比里的每个技术断言都带行号 —— 费曼篇不豁免取证。

---

## §0 九个问题

| # | 问题 | 为什么难 |
|---|---|---|
| §1 | 一根线为什么需要三个结构体（`gpio_chip` / `gpio_device` / `gpio_desc`）？ | 三个名字都像"那个控制器" |
| §2 | gpio 有自己的 `bus_type`，为什么上面永远只有一个 stub driver？ | 和 i2c/spi 的总线长得像，行为完全不同 |
| §3 | `active_low` 到底在哪一层反转？ | 四层值模型，反转点在两个方向上位置不同 |
| §4 | 全局 GPIO 编号为什么被判定为一个"错误的抽象"？ | 它在 sysfs / trace 里还活着 |
| §5 | `gpiod_get(dev, "reset", …)` 里那个 `"reset"` 怎么变成 DTS 里的 `reset-gpios` 的？ | 中间隔着一个字符串拼接 |
| §6 | 一个引脚为什么要 pinctrl 和 gpio 两个子系统管？ | 最容易被类比错的一组关系 |
| §7 | GPIO 控制器为什么要"兼任"中断控制器？ | 两个身份挂在同一个结构体上 |
| §8 | `_cansleep` 后缀为什么不能由内核自动判断？ | 看起来像是可以自动的 |
| §9 | 关掉一个文件描述符为什么线就自动还回去了？ | 和 sysfs 的行为相反 |

---

## §1 一根线为什么需要三个结构体？

### 先举个例子

想象一栋写字楼的门禁系统。这里有三样东西，名字听起来都像"门禁"：

1. **厂商发来的那套设备和操作手册** —— 写着"刷卡口在这里、开门要给这个继电器通电、一共 32 个门"。这是**硬件的能力说明**。厂家换型号，这套手册整个换掉。
2. **物业挂在墙上的那块铭牌** —— "本楼门禁系统，编号 3 号系统，管理 32 个门，责任人物业办"。这块铭牌是**给整栋楼的管理体系看的**：审计要查它、报修单要填它、系统拆掉之前铭牌不能摘。
3. **每个门上贴的那张使用登记条** —— "3 号楼 17 号门，当前由市场部借用，只出不进，刷卡反着算"。这是**每个门自己的状态**。

厂商手册可以被撤走（换厂家），但只要还有人拿着 17 号门的钥匙没还，那块铭牌就得留着，否则报修单上的"3 号系统"就变成了悬空引用。

### 回到代码

三样东西一一对应：

| 概念 | 结构体 | 定义位置 | 谁拥有它 |
|---|---|---|---|
| 厂商手册（硬件能力） | `struct gpio_chip` | `include/linux/gpio/driver.h:415` | **驱动**。驱动 remove 时它就没了 |
| 墙上铭牌（驱动模型身份） | `struct gpio_device` | `drivers/gpio/gpiolib.h:53` | **gpiolib 核心**。内嵌一个 `struct device dev`（`:55`）和一个 `struct cdev chrdev`（`:56`） |
| 每门登记条（单线状态） | `struct gpio_desc` | `drivers/gpio/gpiolib.h:148` | **gpiolib 核心**，随 `gpio_device` 一起分配 |

两者的缝合点只有两行（`drivers/gpio/gpiolib.c:619-620`）：

```c
	gdev->dev.bus = &gpio_bus_type;
	gdev->dev.parent = gc->parent;
```

`gpio_device` 里还存了一份 `struct gpio_chip *chip`（`gpiolib.h:59`）以及 `base` / `ngpio`（`:61-62`）—— 也就是**铭牌上抄了一份手册的关键信息**，这样手册撤走之后铭牌还能自己说话。

### 这个类比什么时候会误导你

**误导点一：铭牌不是"指向手册的指针"，它是独立的生命周期主体。** `gpio_chip` 消失之后 `gpio_device` 可以继续存在（引用计数没到 0 时）。类比里"铭牌留着"听起来像个手续问题，代码里它是**实打实防 use-after-free 的机制**：`gpio_device` 内嵌的是 `struct device`，靠 `get_device`/`put_device` 定生死。

**误导点二：类比暗示"手册撤走后一切照常"，代码里恰恰相反 —— 撤走时会报警但不会拦。** `gpiochip_remove()` 发现还有线被 request 就打一条 `dev_crit`（`drivers/gpio/gpiolib.c:906-907`，全文件唯一一处 `crit`），然后**继续拆**（`gcdev_unregister(gdev)` 在 `:914`）。也就是说物业不会因为有人没还钥匙就拒绝拆系统，只会在日志里喊一声。这是 gpio 的一个已知设计债，kerneldoc 的措辞和代码行为对不上（见 99 §6）。

**误导点三：三个结构体不是三层封装。** 类比容易读成"手册 → 铭牌 → 登记条"的包含关系。实际上 `gpio_desc` 不属于 `gpio_chip`，它属于 `gpio_device`；`gpio_chip` 只是被 `gpio_device` 指着的一块外部数据。结构关系图见 02 §2。

---

## §2 gpio 有自己的 `bus_type`，为什么上面永远只有一个 stub driver？

### 先举个例子

想象一个只为了发身份证而存在的户籍系统。

正常的户籍系统（比如 i2c 总线）是这样运转的：新居民登记进来 → 系统拿着他的信息去匹配"哪个社区负责他" → 匹配上了就把他交给那个社区管理。**登记的目的是为了被接管。**

gpio 的这套"户籍系统"只做前半截：新的 GPIO 控制器登记进来，拿到一个身份证号（`gpiochip3`）、一个可以被查询的档案页（`/sys/bus/gpio/devices/gpiochip3`）、一个可以挂在别人档案下的父子关系。**然后就结束了 —— 没有任何社区会来接管它。**

那为什么还要办这个户籍？因为身份证本身有用：有它才能开设备节点、才能在 sysfs 上被看到、才能被引用计数保护、才能挂在父设备下面被 runtime PM 一起管。

### 回到代码

`drivers/gpio/gpiolib.c` 里的完整证据链：

| 行 | 内容 |
|---|---|
| :61 | `static struct bus_type gpio_bus_type = { .name = "gpio", … };` |
| :619 | `gdev->dev.bus = &gpio_bus_type;` —— 每个控制器都注册在这条总线上 |
| :4443-4447 | `static struct gpio_stub_drv gpio_stub_drv = { … };` —— **全树唯一**的一个 driver |
| :4454 | `ret = bus_register(&gpio_bus_type);` |
| :4460 | `ret = driver_register(&gpio_stub_drv.drv);` |

所以 `/sys/bus/gpio/drivers/` 下永远只有 `gpio_stub_drv` 一个条目，每个 `gpiochipN` 的 `driver` 链接都指向它。**看到这个不是配置错误。**

真正干活的那个 driver 在**另一条总线**上：控制器的 DT 节点通常被 platform 层变成 `platform_device`，由 `platform_driver` probe，probe 里再调 `gpiochip_add_data()`。所以一个 GPIO 控制器**同时是两条总线上的两个 device**：

```
platform 总线                        gpio 总线
┌─────────────────────┐             ┌──────────────────────┐
│ platform_device      │            │ gpio_device          │
│  "e6052000.gpio"     │            │  "gpiochip3"         │
│  ← 被 platform_driver│            │  ← 只被 stub 认领     │
│    probe，真正干活    │            │    （不做任何事）      │
└──────────┬──────────┘             └──────────┬───────────┘
           │                                    │
           └──── gc->parent ────────────────────┘
                 gpiolib.c:620
                 gdev->dev.parent = gc->parent
```

### 这个类比什么时候会误导你

**误导点一：类比说"没人接管"，但 stub driver 不是空的占位符 —— 它的存在是为了让 driver core 的绑定流程不报错。** 一个 `bus_type` 上如果有 device 却没有任何 driver 能匹配，driver core 会不断尝试匹配、留下 uevent。stub 让每个 device 立刻绑定成功、流程闭合。类比里的"办完户籍就结束"掩盖了这个主动动作。

**误导点二：不要由此推论"所有框架型子系统都会建一条退化总线"。** clk、pinctrl、regulator 都没有自己的 `bus_type`。gpio 建了，主要是为了 `/dev/gpiochipN` 这个字符设备需要一个 `struct device` 挂载点（`gpiolib.h:56` 的 `struct cdev chrdev`）。**是字符设备需求驱动了总线的存在，不是"框架都该有总线"。**

**误导点三：`dev.parent` 不是"总线之间的桥"，它只是一条普通的父子指针。** 类比里两条总线像是被"缝在一起"，实际上 driver core 完全不知道这两个 device 有什么特殊关系 —— `:620` 那一行赋值和任何一个设备挂在父设备下没有区别。它带来的实际效果只有三个：sysfs 路径层级、runtime PM 传播、以及 §7 的 debugfs 表头里那个 `parent:` 字段。

---

## §3 `active_low` 到底在哪一层反转？

### 先举个例子

一个说反话的传声筒。

你在楼上的办公室按下"开灯"，指令要经过一个传声筒传到楼下的开关工人。这个传声筒被设定成**说反话**：你说"开"，它喊出去的是"关"。

现在关键问题是：**这个反话是在传声筒的哪一端说的？**

- 如果反话在**靠你这一端**说：你喊"开"，传声筒立刻在你耳边把它变成"关"，然后一路"关"传下去。**通道里跑的是"关"。**
- 反过来，楼下工人报上来"灯是亮的"，传声筒把它变成"灯是暗的"传给你。**通道里跑的是"亮"，你听到的是"暗"。**

所以在同一根通道上：**往下走的指令是"已经反过的"，往上走的报告是"还没反的"**。你如果去通道中间偷听（比如装个窃听器），听到的永远是**没有你这一侧的语义的那个版本**。

### 回到代码

写和读的反转点分别在 `_commit` 函数的**两侧**，这是全篇最需要记牢的一条。

**写路径**（`drivers/gpio/gpiolib.c:3114-3124`，`gpiod_set_value_nocheck()` 全文）：

```c
static void gpiod_set_value_nocheck(struct gpio_desc *desc, int value)
{
	if (test_bit(FLAG_ACTIVE_LOW, &desc->flags))
		value = !value;
	if (test_bit(FLAG_OPEN_DRAIN, &desc->flags))
		gpio_set_open_drain_value_commit(desc, value);
	else if (test_bit(FLAG_OPEN_SOURCE, &desc->flags))
		gpio_set_open_source_value_commit(desc, value);
	else
		gpiod_set_raw_value_commit(desc, value);
}
```

反转在 `:3116-3117`，**在调 `*_commit` 之前**。

**读路径**（`gpiod_get_value()`，`:2821-2836`）：

```c
	value = gpiod_get_raw_value_commit(desc);
	if (value < 0)
		return value;

	if (test_bit(FLAG_ACTIVE_LOW, &desc->flags))
		value = !value;
```

反转在 `:2833-2834`，**在 `*_commit` 返回之后**。

于是"raw 层"有了精确定义：**`*_commit` / `*_complex` 这一层，就是极性反转的另一侧。** 而 §6 的 tracepoint 九个调用点全部落在这一层（`:2948` 的 `trace_gpio_value` 在 `gpiod_set_raw_value_commit()` 里，等等，完整表见 07 §6.2）。所以：

```
你调 gpiod_set_value(desc, 1)，线是 GPIO_ACTIVE_LOW
  → trace 里看到的是  gpio_value: <n> set 0
```

**这不是 bug，这是"窃听器装在通道中间"的必然结果。**

四层值模型完整对应表：

| 层 | 这一层的 1 是什么意思 | 在哪能观察到 |
|---|---|---|
| 逻辑值（logical） | "我要的那个状态"，比如"复位生效" | `gpiod_get/set_value()` 的参数、sysfs 的 `value`（07 §5.2） |
| raw | 寄存器该写什么 | tracepoint（07 §6.3）、`gpiod_get/set_raw_value()` |
| 寄存器值 | 芯片手册里那个 bit | 控制器驱动的 `dbg_show`（07 §4.5） |
| PAD 电平 | 万用表量到的电压 | 示波器；gpio-sim 的 `pull` 可注入（07 §9.1） |

### 这个类比什么时候会误导你

**误导点一：传声筒暗示"反转是一次性的、对称的"，代码里它是不对称的两处独立代码。** `:3116` 和 `:2833` 是两个不同函数里的两处 `test_bit`，没有任何共享逻辑。这意味着**有可能只有一侧被正确处理** —— 事实上这正是 07 §5.2.1 那个坑的成因：sysfs 的 `direction_store()` 走 `gpiod_direction_output_raw()`（`drivers/gpio/gpiolib-sysfs.c:87`、`:89`），**完全绕过了写路径的反转**，而同目录下的 `value_store()` 走 `gpiod_set_value_cansleep()`（`:135`）不绕。同一个目录里两个文件语义层不同。

**误导点二：类比里"反话"只有一种，代码里 `active_low` 之外还有 open-drain/open-source 在同一个函数里做完全不同的变换。** `:3118-3123` 三个分支里，open-drain 那条会把"写 1"变成"切换成输入方向"—— 那已经不是"反话"，而是"改说话方式"。所以 open-drain 线上 `gpio:gpio_value` 一条事件都不会有，全变成 `gpio_direction`（07 §6.4）。

**误导点三：不要以为"raw 就是硬件真实电平"。** raw 和 PAD 之间还隔着寄存器语义和引脚的电气配置（上下拉、驱动能力、复用）。raw 写 1 而 PAD 是低的情况完全可能 —— 那通常是 pinctrl 那一侧的问题，见 §6。

---

## §4 全局 GPIO 编号为什么被判定为一个"错误的抽象"？

### 先举个例子

一个城市决定给全市所有的门牌统一编一套流水号：不叫"3 号楼 17 门"，而叫"全市第 4823 号门"。

刚开始很方便 —— 一个数字就能定位一扇门，写在便条上很短。但麻烦很快来了：

1. **号段是按楼房登记的先后切出来的。** 3 号楼先登记，拿到 4800–4831；后来 3 号楼加盖了两层要多 8 个号，**已经切给别人了，加不进去**。
2. **号段总量有上限。** 全市号池是固定的，用完就没了 —— 哪怕实际的门只有一半。
3. **两栋楼如果都坚持要 4800 开头，直接冲突**，第二栋登记失败。
4. **换个装修顺序，同一扇门的号就变了。** 便条上写的 4823 明年可能指向另一扇门。

于是城市改用"楼号 + 门号"两段式。但已经印出去的便条、贴在墙上的老告示、监控系统里的日志，**还都在用流水号**。新旧两套坐标系同时在跑。

### 回到代码

四条麻烦全部对应到 `drivers/gpio/gpiolib.c` 的具体行：

| 麻烦 | 代码 |
|---|---|
| 号段从高往低倒着切 | `:187` `int base = ARCH_NR_GPIOS - ngpio;`（在 `gpiochip_find_base()` 里，从顶端往下找空隙） |
| **号池有上限，用完就 `-ENOSPC`** | `:202`。**这是全 `drivers/gpio/*.c` 里唯一一处 `-ENOSPC`** |
| 上限是编译期定死的 | `include/asm-generic/gpio.h:27-33`：`ARCH_NR_GPIOS` 取 `CONFIG_ARCH_NR_GPIO`，没配就是 **512** |
| 两个控制器号段重叠 → `-EBUSY` | `:292`，在 `gpiodev_add_to_list()` 里 |

重跑确认唯一性：

```bash
grep -n -- '-ENOSPC' drivers/gpio/*.c     # → 只有 gpiolib.c:202
```

**`-ENOSPC` 是一个"纯粹由 legacy 编号方案引入"的失败模式。** cdev 路径（`/dev/gpiochipN` + offset）上根本不存在"编号耗尽"这个概念 —— 因为它不需要一个全局号池。

而"老便条还在用流水号"对应三个仍然活着的面（07 §11.2 那张表）：

| 面 | 用什么标识一根线 |
|---|---|
| cdev / `lsgpio` / gpio-sim / DTS 消费 | **`(设备节点, offset)`** ← 新坐标 |
| `/sys/class/gpio/export` | **全局编号** ← 老坐标 |
| tracepoint 的 `gpio` 字段 | **全局编号** ← 老坐标（`desc_to_gpio()`，`drivers/gpio/gpiolib.h:211-213`） |
| `/sys/kernel/debug/gpio` 每一行的 `gpio-%d` | **全局编号**，但表头给的是区间 |

换算：`全局编号 = base + offset`，`base` 从 `/sys/class/gpio/gpiochipN/base`（`gpiolib-sysfs.c:388`）或 debugfs 表头（`gpiolib.c:4573-4575`）拿。

### 这个类比什么时候会误导你

**误导点一：类比暗示"老编号是历史包袱，早该删掉"，但代码里它是被有意保留的。** `gpiochip_find_base()` 每次注册都在跑，全局编号**始终在分配**，哪怕系统里没有一个用户使用 sysfs。原因是 `gpio_desc` 的全局数组和 `gpio_to_desc()` 这套查找机制还在被内部代码用，而且 tracepoint 的 ABI 已经固化成全局号了（`include/trace/events/gpio.h:12`、`:34` 的 `TP_PROTO(unsigned gpio, …)`），改它会破坏用户态 trace 解析器。

**误导点二："号段倒着切"不是随意的实现细节，它有可观察的后果。** 因为 `:187` 从 `ARCH_NR_GPIOS` 往下找，**最早注册的控制器拿到最大的号**。所以在同一块板子上，片上 GPIO（早注册）的全局号通常比 I²C 扩展芯片（晚注册）的**大**。凭"号小的先注册"去猜是哪个控制器会猜反。

**误导点三：类比里"两段式坐标"听起来是纯粹的改进，但它引入了一个新麻烦 —— `gpiochipN` 里那个 N 本身也不稳定。** 探测顺序变了，`gpiochip3` 可能变成 `gpiochip5`。所以 cdev 的正确用法不是硬编码 `/dev/gpiochip3`，而是先用 `GPIO_GET_CHIPINFO_IOCTL`（`include/uapi/linux/gpio.h:505`）遍历、按 label 匹配 —— `tools/gpio/lsgpio.c` 就是这么做的（无参数时遍历 `/dev/gpiochip*`，`:109`）。**两段式解决了号池耗尽，没解决命名稳定性。** 真正稳定的标识是行名（`gc->names[]`，DTS 的 `gpio-line-names`，见 06 §2.5）。

---

## §5 `gpiod_get(dev, "reset", …)` 里那个 `"reset"` 怎么变成 DTS 里的 `reset-gpios` 的？

### 先举个例子

一个只会按固定格式念名字的档案员。

你走到窗口说"我要查**人事**的档案"。档案员不会去理解"人事"是什么意思，他只会做一件事：**把你说的词拼上后缀，然后去架子上找那个标签**。

```
你说：人事
他找：「人事-卷宗」  →  架子上没有
他找：「人事-卷」    →  架子上没有
```

两个后缀都试完了还没有，他就翻出抽屉里那张手写的**"特殊情况对照表"**：上面记着五条特例，比如"如果来访者是三号楼的，'主控'这个词其实存档为'卷宗-主控'"。他挨条比对，命中就按特例找。

全部落空，才回你一句"没有"。

关键：**他从头到尾不知道"人事"是什么意思。** 整个过程就是字符串拼接 + 查表。

### 回到代码

后缀表只有两项，定义在 `drivers/gpio/gpiolib.h:81`：

```c
/* gpio suffixes used for ACPI and device tree lookup */
static __maybe_unused const char * const gpio_suffixes[] = { "gpios", "gpio" };
```

拼接就发生在 `of_find_gpio()` 里（`drivers/gpio/gpiolib-of.c:504`，缓冲区在 `:507` 声明为 `char prop_name[32]`）：

```c
	/* Try GPIO property "foo-gpios" and "foo-gpio" */
	for (i = 0; i < ARRAY_SIZE(gpio_suffixes); i++) {
		if (con_id)
			snprintf(prop_name, sizeof(prop_name), "%s-%s", con_id,
				 gpio_suffixes[i]);
		else
			snprintf(prop_name, sizeof(prop_name), "%s",
				 gpio_suffixes[i]);
```

（`:513-520`。）所以 `gpiod_get(dev, "reset", …)` 依次去找 DTS 属性 **`reset-gpios`**、然后 **`reset-gpio`**。`con_id` 传 `NULL` 时（`:519-520` 那个分支）找的是裸的 `gpios` / `gpio`。

两个后缀都落空之后走"特殊情况对照表"（`:529-531`）：

```c
	/* Properly named GPIO was not found, try workarounds */
	for (q = of_find_gpio_quirks; gpiod_not_found(desc) && *q; q++)
		desc = (*q)(dev->of_node, con_id, idx, &of_flags);
```

表本身在 `:495-502`，**恰好五条 + NULL 哨兵**：

| 条目 | 针对的历史遗留写法 |
|---|---|
| `of_find_spi_gpio` | `compatible = "spi-gpio"` 的节点用 `gpio-sck` / `gpio-mosi` / `gpio-miso`（前缀而非后缀！） |
| `of_find_spi_cs_gpio` | SPI 片选的老写法 |
| `of_find_regulator_gpio` | regulator 的老写法 |
| `of_find_arizona_gpio` | Wolfson Arizona 编解码器 |
| `of_find_usb_gpio` | USB 相关老写法 |

`of_find_spi_gpio` 的全文很短，值得逐字看它怎么把后缀变成前缀（`gpiolib-of.c:387-394`）：

```c
	/* Allow this specifically for "spi-gpio" devices */
	if (!of_device_is_compatible(np, "spi-gpio") || !con_id)
		return ERR_PTR(-ENOENT);

	/* Will be "gpio-sck", "gpio-mosi" or "gpio-miso" */
	snprintf(prop_name, sizeof(prop_name), "%s-%s", "gpio", con_id);
```

注意 `:392` 里 `"gpio"` 和 `con_id` 的顺序**和 `:516` 是反的** —— 这就是为什么 `spi-gpio` 的 DTS 写 `gpio-sck` 而不是 `sck-gpios`。而且它有一道 `of_device_is_compatible(np, "spi-gpio")` 的门（`:388`），别的节点即使写 `gpio-sck` 也不会被认。

### 这个类比什么时候会误导你

**误导点一：类比里"架子上找不到就报没有"，代码里"找不到"和"出错了"是两个不同的返回值，而且必须分清。** 循环的退出条件是 `!gpiod_not_found(desc)`（`:525`）—— 也就是"不是 not-found 就停"。这意味着**真正的错误（比如 `-EPROBE_DEFER`）会立刻终止搜索**，不会继续试下一个后缀、也不会去走 quirk 表。如果把两者混为一谈，就会以为 `-EPROBE_DEFER` 时"两个后缀都试过了"。

**误导点二：`prop_name[32]` 是硬上限，`snprintf` 会静默截断。** 注释就写在 `:507`：`/* 32 is max size of property name */`。`con_id` 长到 27 字符以上（加上 `-gpios` 就 33 了），拼出来的属性名会被截短，然后**去找一个 DTS 里根本不存在的名字，报"找不到"而不是"名字太长"**。类比里的档案员至少会念完整个词。

**误导点三：类比暗示"特殊情况表是给老板子打的补丁，新代码不用管"，但它会影响你的调试判断。** 五条 quirk 都在 `of_find_gpio()` 的正常路径上，每次查找失败都会跑一遍。所以在 `spi-gpio` 节点上，`gpiod_get(dev, "sck", …)` **既会试 `sck-gpios` 也会试 `gpio-sck`** —— DTS 写成任意一种都能工作。看到有人两种写法混用不要以为其中一种是 bug。反过来，在非 `spi-gpio` 节点上写 `gpio-sck` 一定不工作，而且**没有任何日志**。

---

## §6 一个引脚为什么要 pinctrl 和 gpio 两个子系统管？

> 这是 gpio 里**最容易被类比错**的一组关系。

### 先举个例子

一间会议室和一部会议电话。

**pinctrl 管的是"这间屋子现在改造成什么用途"**：今天拆掉隔断当大会议室、明天装上投影当放映室、后天摆几张桌子当自习室。它管的是**这块物理空间的形态和基础设施**（墙、插座、通风、隔音）。

**gpio 管的是"这间屋子现在谁在用、用来干什么"**：市场部借了两小时开会、门口挂了"使用中"的牌子、用完要还。

现在关键的一点：**"改造成会议室"和"市场部正在里面开会"是两件必须同时成立的事，但它们由两个完全不同的部门批。** 后勤部管改造，行政部管借用。行政部把会议室借给市场部的时候，得先向后勤部确认"这间屋子现在的形态是会议室吗"—— 如果后勤部昨天刚把它改造成了库房，这个借用就不该批。

而两个部门用的**房间编号还不一样**：后勤部按施工图纸编号（"三层西区第 7 号空间"），行政部按门牌编号（"会议室 302"）。两边得有一张对照表。

### 回到代码

一个引脚在两个子系统里各有一个身份：

| 子系统 | 它管什么 | 它眼里这个引脚叫什么 |
|---|---|---|
| pinctrl | 功能复用（这个 pad 当 GPIO 还是当 UART TX）、上下拉、驱动能力、schmitt 等电气属性 | **pin 号** |
| gpio | 方向、电平、谁 request 了、中断 | **GPIO offset / 全局编号** |

**对照表就是 DTS 里的 `gpio-ranges`**（写法见 06，那里有真实节点）。核心侧建表的入口是 `gpiochip_add_pin_range()`（`drivers/gpio/gpiolib.c:1855`，kerneldoc 从 `:1839` 起）。成败两条日志：

| 行 | 日志 |
|---|---|
| :1880 | `chip_err(gc, "could not create pin range\n")` |
| :1884 | `chip_dbg(gc, "created GPIO range %d->%d ==> %s PIN %d->%d\n", …)` |

**成功那条是 `chip_dbg`（默认不可见），失败那条是 `chip_err`（一定可见）。** 所以"对照表建对了"这件事默认无法确认，得先开 dynamic debug（07 §3）。这条 `chip_dbg` 是 30 条里最值得先开的之一。

DT 侧的建表时机在注册流程里：`gpiochip_add_pin_ranges()`（`:477`）由 `gpiochip_add_data_with_key()` 在 `:778` 调用；拆除时在 `:820`（注册失败回滚）和 `:890`（正常 remove）两处调 `gpiochip_remove_pin_ranges()`。

**"行政部批借用前先问后勤部"** 对应 `gpiochip_generic_request()`（`:1743`）：

```c
	return pinctrl_gpio_request(gc->gpiodev->base + offset);
```

（`:1750`；释放侧是 `pinctrl_gpio_free(gc->gpiodev->base + offset)`，`:1766`。）

**注意这一行传出去的是 `gc->gpiodev->base + offset` —— 全局 GPIO 编号。** 也就是说 gpio 和 pinctrl 之间那道桥的接口参数，用的正是 §4 里被判定为"错误的抽象"的那套编号。这两节要连起来读：**全局编号删不掉的原因之一就在这里**，它已经变成了跨子系统的 ABI。

### 这个类比什么时候会误导你

**误导点一：类比暗示"每个 GPIO 控制器都要向 pinctrl 报备"，实际上这道桥是可选的。** `gpiochip_generic_request` 只是一个**现成的 `->request` 实现**，驱动可以填也可以不填。不填的控制器（比如很多 I²C 扩展芯片，它们的引脚不复用）根本不和 pinctrl 打交道。判断某个控制器有没有走这道桥：

```bash
grep -n 'gpiochip_generic_request' drivers/gpio/gpio-<你的驱动>.c
```

**误导点二：类比里"两个部门"是对等的，代码里 pinctrl 是被动的一方。** pinctrl 不会主动阻止 gpio 操作 —— 它只在 `pinctrl_gpio_request()` 被调用时才有机会拒绝。如果驱动没填 `->request`，或者 DTS 里 `gpio-ranges` 没写全，**gpio 层会照样把这根线设成输出并驱动电平，而这个 pad 此刻可能还复用给 UART**。这类"两个子系统各自认为自己是对的"的故障没有任何日志，只能靠读 pinctrl 的 debugfs（`/sys/kernel/debug/pinctrl/*/pinmux-pins`）和 gpio 的 debugfs（07 §4）两边对。

**误导点三：不要把 `gpio-ranges` 理解成"gpio 依赖 pinctrl"。** 依赖方向反而更接近相反：`gpio-ranges` 写在 **GPIO 控制器**节点里，由 **gpio 核心**解析，然后**主动去 pinctrl 那边注册一个 range**（`pinctrl_find_and_add_gpio_range()`，`:1876-1877`）。是 gpio 把自己的地盘登记进 pinctrl，不是反过来。匹配用的是 pin controller 的 `dev_name()` **字符串**，拼错不会有编译警告，只会得到 `:1880` 那条 `could not create pin range`（05 §7 也提了这一条）。

---

## §7 GPIO 控制器为什么要"兼任"中断控制器？

### 先举个例子

一栋楼的前台，同时兼着两份差事。

**第一份差事**是"查询窗口"：你问"3 楼 17 号房的灯开着吗"，前台去看一眼告诉你。这是同步的、你问他答。

**第二份差事**是"呼叫转接台"：3 楼 17 号房里装了个紧急按钮，按下之后信号先到前台，前台再判断"这是 17 号房按的"，然后打电话通知登记过要接这个房间警报的人。

两份差事必须由同一个前台做，因为**只有他手上有那张"哪个信号来自哪个房间"的对照表**。物业总控室（真正的中断控制器）只知道"前台那条线响了"，不知道是哪个房间。

而这个前台有两种工作方式：

- **快班**：警报来了他当场就转接出去，几秒钟搞定。要求他自己的工作台反应够快（不需要走到别的楼层查记录）。
- **慢班**：这个前台的对照表存在另一栋楼的档案室里，他得跑一趟才知道是哪个房间。这时警报不能当场处理，必须先记下来、下班后集中处理。

### 回到代码

两份差事挂在同一个结构体上：`struct gpio_chip` 里有一个 `struct gpio_irq_chip irq` 成员（定义在 `include/linux/gpio/driver.h:415` 那个结构体里面）。整套机制由 `CONFIG_GPIOLIB_IRQCHIP` 控制（`drivers/gpio/Kconfig:46`）。

"查询窗口"这一份是 `gc->get` / `gc->set` 那些回调；"转接台"这一份靠 `gc->to_irq`：

```c
	gc->to_irq = gpiochip_to_irq;
```

（`drivers/gpio/gpiolib.c:1574`。）如果驱动自己已经填过 `to_irq`，核心会先吼一声再覆盖（`:1572`）：

```c
	chip_warn(gc, "to_irq is redefined in %s and you shouldn't rely on it\n", …);
```

`gpiochip_to_irq()` 的前两个出口就是 07 §8.4 那对兄弟错误码（`:1413-1424`）：

```c
#ifdef CONFIG_GPIOLIB_IRQCHIP
	/*
	 * Avoid race condition with other code, which tries to lookup
	 * an IRQ before the irqchip has been properly registered,
	 * i.e. while gpiochip is still being brought up.
	 */
	if (!gc->irq.initialized)
		return -EPROBE_DEFER;
#endif

	if (!gpiochip_irqchip_irq_valid(gc, offset))
		return -ENXIO;
```

**`-EPROBE_DEFER` = 前台还没上班（会自愈）；`-ENXIO` = 这个房间根本没装按钮（永远不会有）。** 这是 gpio irqchip 排查里最省时间的一条区分。

"快班 / 慢班"对应 `gc->irq.threaded`（`gpiochip_irq_map()`，`:1324-1328`）：

```c
	irq_set_chip_and_handler(irq, gc->irq.chip, gc->irq.handler);
	/* Chips that use nested thread handlers have them marked */
	if (gc->irq.threaded)
		irq_set_nested_thread(irq, 1);
	irq_set_noprobe(irq);
```

（解绑时在 `:1353` 对称地清掉。）**慢班就是 `threaded = true`** —— 典型是 I²C/SPI 上的 GPIO 扩展芯片：读一次中断状态寄存器要发一次 I²C 事务，会睡眠，不能在硬中断上下文里做。这和 §8 的 `can_sleep` 是同一个物理原因的两个面。

"总控室只知道前台那条线响了"对应级联安装（`:1609-1611`）：

```c
			irq_set_chained_handler_and_data(gc->irq.parents[i],
							 gc->irq.parent_handler,
							 data);
```

上面还有一段注释说清了为什么要用 `handler_data` 而不是 `chip_data`（`:1604-1607`）：`The parent IRQ chip is already using the chip_data for this IRQ chip, so our callbacks simply use the handler_data.`

### 这个类比什么时候会误导你

**误导点一：类比里前台是"一个人干两件事"，代码里两份差事的开关是独立的 —— 一个控制器可以只有查询窗口没有转接台。** 这就是 `-ENXIO` 的常态来源。而且反过来也成立：`gpiochip_irqchip_irq_valid()` 是**按 offset** 判断的，所以**同一个控制器上有些线有中断、有些线没有**是正常配置。不能用"这个 chip 支持中断"推出"这根线支持中断"。

**误导点二：类比暗示"级联"是唯一的转接方式，实际上还有一种完全不同的机制 —— 层级 irqdomain。** 级联（chained）是"父中断响了，前台在父中断的处理函数里 demux"；层级（hierarchical）是"每根 GPIO 线在父控制器那边有自己独立的中断号，前台只负责翻译，不负责 demux"。arm64 上层级方式很常见（GIC + SPI 直连）。两者的代码路径几乎不重叠：层级走 `gpiochip_hierarchy_irq_domain_translate()` / `_alloc()`（07 §7.2 表里 `:1110`、`:1135` 那两条 WARN 就在这条路径上），级联走 `:1609`。**排查时第一步是先确定自己在哪条路径上**，否则会去读完全无关的代码。

**误导点三：类比里"慢班"只是慢一点，代码里它改变的是"能不能在这个上下文调用"这件事本身。** `threaded = true` 的控制器上，你在硬中断处理函数里调 `gpiod_get_value()` 不是"慢"，是**直接错**（会 WARN 然后可能睡眠，见 §8）。类比里的"慢班"暗示只是延迟，实际后果是 `scheduling while atomic`。

---

## §8 `_cansleep` 后缀为什么不能由内核自动判断？

### 先举个例子

一个"这件事能不能现在办"的问题。

你在开会，中途需要一份文件。有两种拿法：

- **抽屉里就有**：伸手拿出来，会议不中断。
- **要去档案馆调**：得走出会议室、坐车过去、办手续。**会议必须暂停**。

现在关键问题：**决定"能不能暂停会议"的不是文件在哪，是这场会议的性质。** 如果这是一场随时可以中断的碰头会，去档案馆没问题；如果这是一场正在同步直播的发布会，绝对不能离场 —— 哪怕文件真的在档案馆。

所以"文件在哪"和"能不能去拿"是两条独立的信息，分别掌握在两个人手里：**文件在哪，档案系统知道；会议能不能中断，只有开会的人知道。**

### 回到代码

"文件在哪"这一条内核知道 —— 就是 `gc->can_sleep`（提供方注册时自己声明的）。所以核心能做的只是**检查**，不是**决定**：

`gpiod_get_value()`（`drivers/gpio/gpiolib.c:2825-2827`）：

```c
	VALIDATE_DESC(desc);
	/* Should be using gpiod_get_value_cansleep() */
	WARN_ON(desc->gdev->chip->can_sleep);
```

`gpiod_set_value()`（`:3139-3141`）：

```c
	VALIDATE_DESC_VOID(desc);
	/* Should be using gpiod_set_value_cansleep() */
	WARN_ON(desc->gdev->chip->can_sleep);
```

**注意这两处只 `WARN_ON`，然后照样往下执行。** 核心不知道你现在的上下文能不能睡，所以它无权拦。八处同类 WARN 的完整清单在 07 §7.1（`:2696`、`:2742`、`:2806`、`:2827`、`:2993`、`:3036`、`:3100`、`:3141`）。

而"会议能不能中断"这一条只有调用者知道 —— 所以它被编码成**你选哪个函数名**。选了 `_cansleep` 版本，就等于向内核声明"我这里可以睡"。反向的检查借 `might_sleep_if()` 完成，但**只在 `CONFIG_DEBUG_GPIO=y` 时打开**（`:3452`、`:3471`、`:3502`、`:3528`、`:3549`、`:3567`、`:3590`、`:3632`，开关链见 07 §1.2 的 `extra_checks`，`:50-54`）。

于是形成一个完整的双向校验：

```
                  提供方声明的                你选的函数名
                  gc->can_sleep                有没有 _cansleep
                        │                            │
  你选了不带后缀的  ────┼──→ can_sleep=true ? ────→ WARN_ON  (:2827/:3141)
                        │                            │        「文件在档案馆，你却想伸手拿」
  你选了带后缀的   ─────┼──→ 上下文真的能睡? ──────→ might_sleep_if (:3452 等 8 处)
                        │                            │        「你说能离场，那核对一下」
                        ▼                            ▼
                   永远在跑                  只在 DEBUG_GPIO=y 时跑
```

某个控制器是不是 `can_sleep`，一眼可查：`/sys/kernel/debug/gpio` 的表头里有没有 `, can sleep` 字样（格式串在 `:4584`）。

### 这个类比什么时候会误导你

**误导点一：类比暗示"选错了会被拦住"，代码里选错只会得到一条 WARN 然后继续跑。** 后果是**延迟爆炸**：`gpiod_set_value()` 在一个 I²C 扩展芯片上被从原子上下文调用，WARN 打完之后真的进了 I²C 传输、真的睡眠，然后才在别处爆出 `scheduling while atomic` 或者直接卡死。**看到 `WARN_ON(…can_sleep)` 就必须改代码，不能当噪声。** 这也是为什么 07 把它单列一节。

**误导点二：类比里"能不能中断会议"是二值的，代码里 `might_sleep_if(extra_checks)` 那一侧默认根本不检查。** 也就是说**反方向的错误（在原子上下文里调 `_cansleep` 版本）在生产内核上完全静默**。要抓它必须打开 `CONFIG_DEBUG_GPIO`，而打开它又会让 dynamic debug 失效（07 §1.2 的第 3 跳副作用）。这个取舍要提前想好。

**误导点三：不要以为"`can_sleep` 的控制器只能用 `_cansleep` API"就意味着两套 API 是等价功能的两个版本。** 它们的返回路径也不同 —— 数组版本里 `can_sleep` 参数会改变 fast-path 的选取（`:2696`、`:2742` 那两处 WARN 就在"要不要走逐 chip 收集"的分支里）。所以在 `can_sleep` 控制器上用数组 API，性能特征和片上 GPIO 完全不是一回事。

---

## §9 关掉一个文件描述符为什么线就自动还回去了？

### 先举个例子

两种借用登记方式。

**第一种（老式纸质登记本）**：你在门口本子上签名"张三借用 17 号门"，然后拿走钥匙。这个登记**和你本人没有任何绑定** —— 你辞职了、你调走了、你忘了，登记还在那儿。要销掉必须有人再回来划掉那一行。

**第二种（刷卡借用）**：你刷卡进入，系统给你开一个"会话"。这个会话**绑在你的门禁卡上**：卡失效、你离场、系统重启你的会话，借用记录**自动**销掉，钥匙自动归还。

第二种明显更安全，因为它把"归还"这件事从**人的自觉**变成了**系统的必然**。代价是你必须一直持有那张卡 —— 卡一丢，借用就断了，不能"借完先放着"。

### 回到代码

**cdev 是刷卡式的**。三套 fd 各有一个 release，全部在关闭时释放持有的线（`drivers/gpio/gpiolib-cdev.c`）：

| fd 类型 | 释放 desc 的地方 | release 函数 | 挂到 `file_operations` |
|---|---|---|---|
| v2 `linereq`（`GPIO_V2_GET_LINE_IOCTL`） | `gpiod_free(line->desc)` `:1722` | `linereq_release()` `:1730` | `.release = linereq_release` `:1754` |
| v1 `linehandle` | `gpiod_free(lh->descs[i])` `:328` | `linehandle_release()` | `.release = linehandle_release` `:341` |
| v1 `lineevent` | `gpiod_free(le->desc)` `:2064` | `lineevent_release()` `:2070` | `.release = lineevent_release` `:2126` |

以 v2 为例，`linereq_free()` 里那个循环（`:1714-1723`）逐根线做三件事：停边沿检测、清 supinfo、`gpiod_free()`。然后 `:1726` 的 `put_device(&lr->gdev->dev)` 放掉对控制器的引用 —— 这一行正是 §1 里"钥匙没还就不能摘铭牌"的实现机制。

**`.release` 是 VFS 在最后一个 fd 关闭时调用的，进程被 kill、崩溃、忘记 close 都会走到。** 所以"进程死了线就还回来"不是 gpio 特意实现的功能，是它**选择把所有权挂在 fd 上**之后自动得到的。

**sysfs 是纸质登记本式的**。`export_store()` 在 `drivers/gpio/gpiolib-sysfs.c:475` 打上标记：

```c
	set_bit(FLAG_SYSFS, &desc->flags);
```

清掉这个标记的地方只有 `unexport_store()`（`:509` 的 `test_and_clear_bit(FLAG_SYSFS, …)`）—— 也就是**必须有人再写一次 `unexport`**。

后果很直接：**用 shell 脚本 `echo N > export` 之后脚本被 Ctrl-C 掉，那根线就永久卡在 requested 状态**，直到有人手工 `unexport` 或者重启。这时 07 §4.2 的 debugfs 清单里会看到它的 consumer 是 `"sysfs"`，而系统里已经没有任何进程和它有关。

### 这个类比什么时候会误导你

**误导点一：类比说"卡一丢借用就断"，听起来是缺点，但在内核里这是唯一正确的设计。** 如果 cdev 允许"借完先放着"，那么任何崩溃的用户态程序都会永久泄漏 GPIO 线 —— 而 GPIO 线不像内存，它有物理副作用（一个卡在输出高的复位脚会让芯片一直处于复位）。所以 cdev **有意**不提供"持久化持有"的能力。需要开机就置位并保持的场景，正确做法是走 DTS 的 `gpio-hog`（06）或者写一个内核驱动，不是让用户态程序常驻。

**误导点二：类比里"会话"是一个整体，代码里一个 fd 可以持有多根线，而释放是逐根做的。** `linereq_free()` 的循环里有 `if (!line->desc) continue;`（`:1716-1717`）—— 意思是**部分申请成功、部分失败的中间状态是存在的**，release 必须能处理。所以不能假设"fd 存在 ⟺ 所有请求的线都拿到了"。

**误导点三：不要以为"关 fd 就万无一失"，`/dev/gpiochipN` 本身那个 fd 是另一回事。** 控制器节点的 `.release` 是 `gpio_chrdev_release`（`:2873`），它释放的是 chip 级别的资源（比如 `LINEINFO_WATCH` 的监听位图），**不释放任何线** —— 线是由从它派生出去的 `linereq` / `linehandle` / `lineevent` fd 各自持有的。所以关掉 `/dev/gpiochip0` 而不关那些派生 fd，线依然被占着。这是"父 fd 关了子 fd 还活着"的标准 Unix 语义，但和"刷卡离场"的类比直觉相反。

---

## §10 阅读下一篇

| 想继续往下 | 去 |
|---|---|
| 这些 API 的完整签名和错误码语义 | 01 |
| §1 那三个结构体的字段全表和关系图 | 02 |
| §3 四层值模型在注册/操作流程里的位置 | 03 §A5 |
| §7 两条 irqchip 路径的逐段剖析 | 03 §B4、04 §5 |
| §9 cdev 的 v1/v2 结构体和事件语义 | 04 §6 |
| 自己写提供方/消费者代码 | 05 |
| §5 那些属性名在 DTS 里怎么写 | 06 |
| §6 `gpio-ranges` 的真实节点例子 | 06 §2.4 |
| 上面每个 怎么在运行的系统上验证 | 07 |
| 本篇每条断言的出处和自审记录 | 99 |
