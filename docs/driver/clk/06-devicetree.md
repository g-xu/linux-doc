---
title: 设备树
---

# 06 设备树：clk 的 DTS 怎么写、每条属性谁在消费、配错了怎么定位

> 本篇只讲 **arm64**。举例一律取自 `arch/arm64/boot/dts/` 下的真实在树节点，读者可以直接
> `git show` 出来 diff。ACPI 路径按项目约定裁掉（理由记在 `99-evidence.md` §2.7）。
>
> ⚠️ **本篇一个"版本时效"警告要放在最前面**：clk 的**通用**绑定文档已经**搬出内核树**了。
> `Documentation/devicetree/bindings/clock/clock-bindings.txt` 在 6.1.158 里只剩 2 行，
> 内容是一条指向树外 `dt-schema` 仓库的 URL。也就是说：**你在本树里找不到一份权威的
> "clk 通用属性清单"**，`Documentation/devicetree/bindings/clock/` 下剩的 316 个文件
> 全是**具体 SoC 的**绑定。本篇 §1.1 那张表就是拿来补这个洞的——它不是抄绑定文档，
> 是**反过来从 `drivers/clk/` 的消费代码里 grep 出来的**，所以每一行都有行级出处。

---

## §0 发现路径分诊表：先别问"有没有 DT 绑定"

一个 clk 提供方是怎么被系统发现的，DT 只是其中一条路。**arm64 上四条路并存**，
而且它们的失败症状完全不同——走错了路去查 DTS，会白查一整天。

| 路径 | 谁扫描 | 走 DT 吗 | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|
| **① DT phandle**（`clocks = <&provider N>`） | clk 核心的 `of_clk_get_hw_from_clkspec` / `of_parse_clkspec` | ✅ 全程 | ✅ **绝对主力** | **全篇**，§2–§6 逐属性展开 |
| **② `CLK_OF_DECLARE` 早期声明** | `of_clk_init()`，由 arm64 的 `time_init()` 调用 | ✅ 只按 `compatible` 匹配节点 | ✅ 但只用于 timer/irqchip 的时钟源 | §2 的属性照样适用，**但没有 `struct device`**，见 `05-usage.md` §4 |
| **③ clkdev 名字查表**（`clk_get_sys`） | `drivers/clk/clkdev.c` 里的全局链表 `clocks` | ❌ **完全不看 DT** | ⚠️ 见到就是 arm32 移植没清干净 | 只在 §0.2 交代它存在，机制在 `04-extensions.md` §10 |
| **④ 固件代理**（SCMI / SCPI） | `scmi_clocks` / `scpi_clocks` 驱动，时钟表由**固件**返回 | ✅ 节点要有，但**属性极少** | ✅ Juno / 部分服务器 SoC | §2.6 单独一节说明"为什么这里几乎不用写属性" |

**这张表最重要的一行是 ④**。SCMI 平台上时钟的名字、个数、频率范围**全部由固件运行期
报上来**，DTS 里那个 `compatible = "arm,scmi-clocks"` 的节点上**没有 `clock-output-names`、
没有 `clock-frequency`**——你在 DTS 里怎么翻都翻不到时钟叫什么名字。这时候要去看
`/sys/kernel/debug/clk/clk_summary`（见 `07-debug.md`），而不是继续读 DTS。

### §0.1 三方分工：一条属性到底是谁在读

DT 属性不是"clk 核心"一家在读。搞清这个分工，才知道一条属性配错了该去哪个代码里找。

| 消费者 | 住在哪 | 它读的属性 | 特征 |
|---|---|---|---|
| **通用 DT / 驱动模型层** | `drivers/of/`、`drivers/base/` | `clocks`、`#clock-cells`（**只用来数 cell 个数、建 fwnode 依赖**） | 它不知道"时钟"是什么，只按 phandle+cells 的通用语法解析 |
| **clk 核心** | `drivers/clk/clk.c`、`clk-conf.c`、`clk-bulk.c` | `clocks`、`clock-names`、`#clock-cells`、`clock-indices`、`assigned-clock*`、`clock-ranges` | 语义层：把 clkspec 翻译成 `struct clk_hw *` |
| **基础 clk 驱动** | `drivers/clk/clk-fixed-rate.c` 等 | `clock-frequency`、`clock-accuracy`、`clock-output-names` | **只有这一层读硬件参数**，核心不读 |
| **厂商 clk 驱动** | `drivers/clk/qcom/` 等 | `protected-clocks` 等私有属性 | 全树只此一家，见 §5.2 |

### §0.2 按任务找章节

| 你现在要干的事 | 去哪一节 |
|---|---|
| 我在写一个**时钟提供方**的节点 | §2 |
| 我在写一个**消费者**（i2c / uart / 网卡）的节点 | §3 |
| 我想在 DTS 里**直接把某个时钟拨到某个频率** | §4 |
| 我想知道某条属性**到底有没有人读** | §1.1 表 + §5 |
| DTS 写了但**一点效果都没有，也不报错** | §7 决策树（全在这一节） |
| 起不来 / 报错了，我要定位 | §8 三层定位 + §9 错误消息映射表 |
| 我在 SCMI/SCPI 平台上，DTS 里啥都没有 | §2.6 |

---

## §1 核心真正会读的属性总表

这张表是本篇的骨架，后面每一节都是它的一行展开。**"写在哪个节点"这一列是最容易搞反
的地方**——`#clock-cells` 写在提供方，`clocks` 写在消费方，而 `assigned-clocks`
两边都能写、且写在哪边决定了它**什么时候生效**（§4.2）。

### §1.1 属性 → 消费者对照表

| 属性 | 写在哪个节点 | 谁消费（定位到行） | 缺了会怎样 |
|---|---|---|---|
| `#clock-cells` | **提供方** | `drivers/clk/clk.c:4961-4962`（`get_clk_provider_node`）；`drivers/clk/clk-conf.c:21`/`:28`/`:50`/`:93` | phandle 解析失败，消费者永远拿不到时钟 |
| `clocks` | **消费方** | `drivers/clk/clk.c:5085-5086`（`of_parse_clkspec`）；`drivers/clk/clk-bulk.c:27` | `clk_get` 返回 `-ENOENT` |
| `clock-names` | **消费方** | `drivers/clk/clk.c:5085`（作为 `of_parse_phandle_with_args` 的 name 参数一路传下去）；`drivers/clk/clk-bulk.c:27` | 按名字取时钟失败；按 index 取仍然可用 |
| `clock-output-names` | **提供方** | `drivers/clk/clk-fixed-rate.c:173`、`clk-fixed-factor.c:267`、`clk-fixed-mmio.c:34` | 时钟名回落成节点名，`clk_summary` 里认不出来 |
| `clock-indices` | **提供方** | `drivers/clk/clk.c:5254`（`of_clk_get_parent_name` 里的 `of_property_for_each_u32`）；`drivers/clk/clk-scpi.c:228` | 只影响"按名字找父时钟"，见 §2.4 |
| `clock-frequency` | **提供方** | `drivers/clk/clk-fixed-rate.c:168`、`clk-pwm.c:91`、`clk-qoriq.c:1103` | **`fixed-clock` 直接 probe 失败返回 `-EIO`** |
| `clock-accuracy` | **提供方** | **只有** `drivers/clk/clk-fixed-rate.c:171` | 精度按 0 处理，即"宣称零误差"，见 §2.5 |
| `assigned-clocks` | 两边都行 | `drivers/clk/clk-conf.c:49`（父）/`:92`（频率） | 后两条属性全部失效且**不报错** |
| `assigned-clock-parents` | 同上 | `drivers/clk/clk-conf.c:20`/`:27` | 父时钟保持驱动的默认选择 |
| `assigned-clock-rates` | 同上 | `drivers/clk/clk-conf.c:90` | 频率保持复位默认值 |
| `clock-ranges` | 提供方的**父**节点 | `drivers/clk/clk.c:5095`/`:5099` | ⚠️ **arm64 DTS 里 0 处使用**，见 §5.1 |
| `protected-clocks` | 提供方 | **`drivers/clk/*.c` 里没有任何消费者**；唯一消费者是 `drivers/clk/qcom/common.c:207-221` | 高通平台专用，见 §5.2 |

表里两条属性的消费者位置值得单独注意：

- **`clock-frequency` 和 `clock-accuracy` 都不在核心里**。它们由 `clk-fixed-rate.c` 这个
  **具体驱动**读。所以"我在 `drivers/clk/clk.c` 里 grep `clock-frequency` grep 不到"
  是正常的，不代表属性是死字。
- **`clock-output-names` 有三个基础驱动各读一遍**，核心从不读它。核心里时钟名的来源是
  注册时传进来的 `init.name`（见 `03-internals.md` §A2），DTS 只是**让驱动把这个名字
  从 DTS 里取出来再传给核心**，中间隔了一层。

### §1.2 arm64 在树使用量（判断一条属性值不值得学）

| 属性 / compatible | arm64 DTS 里的量 | 重跑命令 |
|---|---|---|
| `fixed-clock` 节点 | **536** | `grep -rn '"fixed-clock"' arch/arm64/boot/dts/ \| wc -l` |
| `fixed-factor-clock` 节点 | 20 | 同上换字符串 |
| `assigned-clocks` | **693 行** | `grep -rn 'assigned-clocks' arch/arm64/boot/dts/ \| wc -l` |
| `assigned-clock-parents` | 467 行 | 同上 |
| `clock-output-names` | 470 行 | 同上 |
| `protected-clocks` | 12 行 | 同上 |
| `clock-ranges` | **0** | 同上 |

读法：**`fixed-clock` 536 个 vs `fixed-factor-clock` 20 个**——如果你只学一个
提供方形态，学 `fixed-clock`。而 `assigned-clocks` 有 693 行，说明 §4 那套机制
**不是边角特性，是 arm64 板级配置的日常手段**，值得完整读一遍。

---

## §2 提供方节点怎么写

### §2.1 最小可用的提供方：`fixed-clock`

真实在树节点，rk3399 的 24MHz 晶振（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:215-219`）：

```dts
	xin24m: xin24m {
		compatible = "fixed-clock";
		clock-frequency = <24000000>;
		clock-output-names = "xin24m";
		#clock-cells = <0>;
	};
```

四条属性，一条都不能少（`clock-output-names` 严格说可省，见 §2.3）：

| 属性 | 作用 | 省掉会怎样 |
|---|---|---|
| `compatible = "fixed-clock"` | 匹配 `drivers/clk/clk-fixed-rate.c` 的 platform 驱动 | 没有驱动 probe，节点变成一个哑节点 |
| `clock-frequency` | 频率，`drivers/clk/clk-fixed-rate.c:168` 读 | **probe 直接失败返回 `-EIO`**，是本篇少数"会明确报错"的属性 |
| `clock-output-names` | 时钟名，`:173` 读 | 回落成节点名（这里就是 `xin24m`，正好同名，所以省掉也看不出区别） |
| `#clock-cells = <0>` | 声明"引用我时不带参数" | 消费者侧解析 phandle 时 cell 数对不上 |

这个节点值得记住的一点：**它不在任何 `soc {}` 之类的父节点里，是挂在根节点下的**。
时钟晶振没有寄存器、没有 `reg`，挂在 `soc` 下反而不合适。arm64 里 536 个 `fixed-clock`
绝大多数是这个形状。

### §2.2 `#clock-cells`：它同时决定两件事

`#clock-cells` 的值决定**消费者引用你时要带几个参数**，进而决定**你要用哪个 get 回调**：

| `#clock-cells` | 消费者写法 | 提供方注册时用的 get 回调 | 典型场景 |
|---|---|---|---|
| `<0>` | `clocks = <&xin24m>` | `of_clk_hw_simple_get` | 单时钟提供方（晶振、单个 PLL） |
| `<1>` | `clocks = <&cru SCLK_I2C1>` | `of_clk_hw_onecell_get` | **CCU（时钟控制单元），arm64 主流** |
| `<2>` 及以上 | `clocks = <&p 1 2>` | 自己写回调 | 少见，通常是"类型+索引"两级编码 |

**这里有一个 90% 的人踩过的坑**：`#clock-cells = <1>` 时，消费者写的那个
`SCLK_I2C1` 宏**不是内核符号，是 DTS 头文件里的数字宏**，它的值就是提供方
`clk_hw_onecell_data.hws[]` 的**数组下标**。所以：

> **`hws[]` 的下标是一份对外 ABI。** 改了某个下标的含义而不同步改
> `include/dt-bindings/clock/<soc>.h`，老 DTB 会**静默地**拿到另一个时钟——
> 不报错，只是行为诡异。这就是为什么每个 SoC 都要有一份 `dt-bindings/clock/` 头文件。

（提供方侧的 `clk_hw_onecell_data` 怎么填、`of_clk_hw_onecell_get` 怎么挂，
在 `05-usage.md` §3.2 有完整骨架，本篇不重复。）

一条容易忽略的核心行为：`get_clk_provider_node`（`drivers/clk/clk.c:4954-4966`）
在**当前节点没有 `#clock-cells` 时会去看父节点**（`:4961-4962`）。这是给 MFD 场景留的口子——
子节点是 MFD 的一个 cell、`#clock-cells` 写在 MFD 父节点上时仍然能工作。
反过来说：**你把 `#clock-cells` 误写在父节点上，有时候居然能跑**，于是错误被掩盖了。

### §2.3 `clock-output-names`：三个驱动各读一遍，核心不读

```
DTS: clock-output-names = "xin24m";
      │
      ▼  drivers/clk/clk-fixed-rate.c:173  of_property_read_string(node, "clock-output-names", &clk_name)
      │
      ▼  clk_name 传进 clk_hw_register_fixed_rate_with_accuracy(... clk_name ...)
      │
      ▼  核心把它存进 clk_core->name（03-internals.md §A2）
      │
      ▼  /sys/kernel/debug/clk/xin24m/ 这个目录名就是它
```

所以它的真正价值在**调试**：`clk_summary` 和 debugfs 目录名都用这个名字。省掉它
不会坏功能（回落成节点名），但会让你在 `clk_summary` 里对不上号。

另两个读它的驱动：`drivers/clk/clk-fixed-factor.c:267`、`drivers/clk/clk-fixed-mmio.c:34`。
**注意 `clk-gate` / `clk-divider` / `clk-mux` 这三个基础驱动不读它**——它们不是
platform 驱动，没有自己的 DT 绑定（见 `04-extensions.md` §0.2）。

### §2.4 `clock-indices`：它只影响"按名字找父时钟"这一条路

这条属性最容易被误解成"给时钟编号"。看它唯一的核心消费点，
`of_clk_get_parent_name`（`drivers/clk/clk.c:5232-5290`），关键段在 `:5254`：

```c
	index = clkspec.args_count ? clkspec.args[0] : 0;
	count = 0;

	/* if there is an indices property, use it to transfer the index
	 * specified into an array offset for the clock-output-names property.
	 */
	of_property_for_each_u32(clkspec.np, "clock-indices", prop, vp, pv) {
		if (index == pv) {
			index = count;
			break;
		}
		count++;
	}
	/* We went off the end of 'clock-indices' without finding it */
	if (prop && !vp)
		return NULL;
```

逐句读：

1. 先拿到消费者写的那个数字（`clocks = <&provider 7>` 里的 7）。
2. 如果提供方有 `clock-indices`，就在这个列表里找 7 出现在**第几个位置**，
   把 7 换成那个位置。
3. **列表里没有 7 就返回 NULL**——注意这是"找不到父时钟名"，不是"报错"。

结论：`clock-indices` 的作用是**让 ID 空间可以稀疏**。没有它，
`clock-output-names` 的第 N 个名字就对应 ID N，ID 必须从 0 连续排；有了它，
你可以只导出 ID 0/1/2 和 5，`clock-output-names` 只写 4 个名字。

真实例子，Juno 的 SCPI 时钟（`arch/arm64/boot/dts/arm/juno-base.dtsi:715-737`），
`:726` 那行是 `clock-indices = <0>, <1>, <2>;`，`:732` 那行是 `clock-indices = <3>;`——
**两个子节点把 0-3 这段 ID 空间切开了**，各自只声明自己那几个。
arm64 里另外几个用它的大户：`amlogic/meson-gx.dtsi`、`freescale/imx8-ss-*.dtsi`。

它的另一个消费者 `drivers/clk/clk-scpi.c:228` 就是为上面这个 Juno 节点服务的。

### §2.5 `clock-accuracy`：全树只有一个消费者

`drivers/clk/clk-fixed-rate.c:171` 是**唯一**一处读它的代码。

这条属性有一个和 `04-extensions.md` §5.1 咬合的坑：**省掉它 = 声明零误差**。
核心里 accuracy 的默认值是 0，而 0 的语义是"完全准确"，不是"未知"。
所以一个真实晶振（±20ppm）如果 DTS 里不写 `clock-accuracy`，内核就会对下游
宣称它是理想时钟。**这不会导致任何报错**，只会让依赖 accuracy 做判断的消费者
（如某些音频采样率协商）算出偏乐观的结果。

### §2.6 SCMI / SCPI：为什么这里几乎不用写属性

arm64 服务器和 Juno 这类平台上，时钟不在本 CPU 的地址空间里，DTS 节点长这样
（机制见 `04-extensions.md` §15）：

```dts
		clocks {
			compatible = "arm,scmi-clocks";
			#clock-cells = <1>;
		};
```

**只有 `compatible` 和 `#clock-cells`**。没有 `reg`、没有 `clock-frequency`、
没有 `clock-output-names`——因为时钟表是驱动 probe 时通过 SCMI 协议**向固件问出来的**。

这带来三个和 §2.1 完全相反的定位习惯：

| 问题 | 常规平台 | SCMI 平台 |
|---|---|---|
| 时钟叫什么名字 | 查 DTS 的 `clock-output-names` | **只能查 `clk_summary`**，DTS 里没有 |
| 时钟支持哪些频率 | 查驱动里的频率表 | 固件返回，运行期才知道 |
| 时钟树的层次 | `clk_summary` 有缩进 | **是平的**——SCMI 的两张 ops 表都没有 `set_parent`/`get_parent`（`04-extensions.md` §15.1） |

还有一个必须提前知道的后果：SCMI 平台上时钟提供方是**普通 platform 驱动**、
且依赖 SCMI 传输层先就绪，所以消费者拿到 `-EPROBE_DEFER` 是**常态而不是异常**。
`05-usage.md` §2.1 里"别把 `-EPROBE_DEFER` 当错误吞掉"那条在这类平台上是硬要求。

---

## §3 消费方节点怎么写

### §3.1 一个同时用上两套机制的真实节点

rk3399 的 i2c1（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:585-598`）是本篇最值得
背下来的一个节点——它把 §3 和 §4 两套机制**摆在了一起**：

```dts
	i2c1: i2c@ff110000 {
		compatible = "rockchip,rk3399-i2c";
		reg = <0x0 0xff110000 0x0 0x1000>;
		assigned-clocks = <&cru SCLK_I2C1>;
		assigned-clock-rates = <200000000>;
		clocks = <&cru SCLK_I2C1>, <&cru PCLK_I2C1>;
		clock-names = "i2c", "pclk";
		interrupts = <GIC_SPI 59 IRQ_TYPE_LEVEL_HIGH 0>;
		...
	};
```

**这四行时钟属性分属两套完全独立的机制，别混为一谈**：

| 属性 | 机制 | 谁在什么时候执行 | 驱动看得见吗 |
|---|---|---|---|
| `clocks` + `clock-names` | **取用**：驱动 probe 时 `devm_clk_get(dev, "i2c")` 拿句柄 | 驱动自己，在 probe 里 | ✅ 驱动主动调用 |
| `assigned-clocks` + `assigned-clock-rates` | **预置**：把 `SCLK_I2C1` 拨到 200MHz | **驱动 probe 之前，由驱动核心自动做**（§4.2） | ❌ 驱动完全不知道 |

也就是说：i2c 驱动里**根本没有 `clk_set_rate(200000000)` 这行代码**，那个 200MHz
是 DTS 单方面预置好的。这是 arm64 板级配置最常用的一招——693 行 `assigned-clocks`
就是这么来的。

另一个细节：`clocks` 里两个时钟一个是**功能时钟**（`SCLK_I2C1`，决定 SCL 速率），
一个是**总线时钟**（`PCLK_I2C1`，寄存器访问用）。arm64 SoC 上这种"功能时钟 + 总线时钟"
成对出现极其常见，`clock-names` 就是用来区分它们的。

### §3.2 `clocks` / `clock-names` 的取值规则

```
clocks = <&cru SCLK_I2C1>, <&cru PCLK_I2C1>;
           └─┬─┘ └───┬───┘
             │       └─ #clock-cells 个参数（这里 cru 的 #clock-cells = <1>）
             └─ 提供方的 phandle
clock-names = "i2c", "pclk";
               └─ 和 clocks 逐项一一对应，顺序必须一致
```

| 取法 | 代码写法 | 依赖哪条属性 |
|---|---|---|
| 按名字 | `devm_clk_get(dev, "pclk")` | 必须有 `clock-names` |
| 按下标 | `of_clk_get(np, 1)` | 只需要 `clocks` |
| 全拿（带名字） | `devm_clk_bulk_get(dev, n, clks)` | 需要 `clock-names`（`drivers/clk/clk-bulk.c:27`） |
| 全拿（不管名字） | `devm_clk_bulk_get_all(dev, &clks)` | **只需要 `clocks`**，顺序即 DTS 顺序 |

**`clock-names` 是可选的，这一点是很多静默失效的源头**。少写 `clock-names`，
驱动的 `devm_clk_get(dev, "pclk")` 会失败——但如果驱动用的是
`devm_clk_get_optional`，它拿到的是 **NULL 而不是错误**，然后所有 clk API 对 NULL
都静默成功（`05-usage.md` §2.2）。结果就是：**DTS 少写一行，设备跑起来了但时钟从来没开**。
决策树见 §7.3。

### §3.3 解析全过程：`of_parse_clkspec` 逐句

这是消费者侧唯一需要读的核心函数，`drivers/clk/clk.c:5071-5105`，全文只有 30 行：

```c
static int of_parse_clkspec(const struct device_node *np, int index,
			    const char *name, struct of_phandle_args *out_args)
{
	int ret = -ENOENT;

	/* Walk up the tree of devices looking for a clock property that matches */
	while (np) {
		if (name)
			index = of_property_match_string(np, "clock-names", name);
		ret = of_parse_phandle_with_args(np, "clocks", "#clock-cells",
						 index, out_args);
		if (!ret)
			break;
		if (name && index >= 0)
			break;

		np = np->parent;
		if (np && !of_get_property(np, "clock-ranges", NULL))
			break;
		index = 0;
	}

	return ret;
}
```

四条要点：

1. **名字先转下标**。给了 `name` 就先在 `clock-names` 里查它的位置，再按位置去 `clocks`
   里取。所以 `clock-names` 的作用纯粹是"名字 → 下标"的映射表，没有别的语义。
2. **两种返回码含义不同**：`name` 为 NULL 时找不到返回 `-ENOENT`；`name` 非 NULL
   但不在 `clock-names` 里，`of_property_match_string` 返回负数，
   `of_parse_phandle_with_args` 拿到负 index 后返回 **`-EINVAL`**。
   **但这个 `-EINVAL` 通常到不了驱动手里**：走 `clk_get`/`devm_clk_get` 时，
   `drivers/clk/clkdev.c:107` 会把一切非 `-EPROBE_DEFER` 的错误**丢掉**，
   退回按名字查 clkdev 表，最终统一返回 `-ENOENT`。只有直接调
   `of_clk_get_by_name` / `of_clk_get` 才能看到原始的 `-EINVAL`。
   详见 `07-debug.md` §6.3。
3. **`if (name && index >= 0) break;`**：名字**查到了**但 phandle 解析失败，就直接
   放弃、不再往上走。语义是"你明确点名了，我不替你猜"。
4. **只有父节点带 `clock-ranges` 才会往上走**（`:5095`/`:5099`）。这就是下面 §5.1
   要讲的那条属性——它是 `of_parse_clkspec` 里唯一的向上遍历开关。

---

## §4 `assigned-clocks` 三兄弟：在 DTS 里直接拨频率和选父

### §4.1 三条属性的配合方式

```dts
	/* 摘自 arch/arm64/boot/dts/freescale/imx8mp.dtsi:489-492 */
	assigned-clocks = <&clk IMX8MP_CLK_A53_SRC>,
			  <&clk IMX8MP_CLK_A53_CORE>,
			  <&clk IMX8MP_CLK_NOC>,
			  <&clk IMX8MP_CLK_NOC_IO>;
```

| 属性 | 角色 | 消费点 |
|---|---|---|
| `assigned-clocks` | **被操作的时钟列表**，另两条属性都按下标对齐到它 | `drivers/clk/clk-conf.c:49`（父）/`:92`（频率） |
| `assigned-clock-parents` | 逐项指定父时钟 | `drivers/clk/clk-conf.c:20`/`:27` |
| `assigned-clock-rates` | 逐项指定频率 | `drivers/clk/clk-conf.c:90` |

**执行次序是先选父、再设频率**，不能反：`of_clk_set_defaults` 先调
`__set_clk_parents`（`drivers/clk/clk-conf.c:14`）再调 `__set_clk_rates`（`:81`）。
理由很实在——父换了以后可用的频率范围就变了，先设频率再换父等于白设。

### §4.2 最关键的一点：写在哪个节点决定什么时候生效

`__set_clk_parents` 和 `__set_clk_rates` 都带一个 `bool clk_supplier` 参数，
两个调用点传的值**不一样**：

| 调用点 | `clk_supplier` | 含义 |
|---|---|---|
| `of_clk_add_hw_provider`（`drivers/clk/clk.c:4934`） | **`true`** | 提供方**自己注册时**，处理写在提供方节点上的 `assigned-clocks` |
| 驱动核心的 platform 设备 probe 路径 | **`false`** | 普通消费者设备 probe 前，处理写在消费者节点上的 `assigned-clocks` |

`clk_supplier == false` 时的跳过判据（`drivers/clk/clk-conf.c` 里出现两次，
父和频率各一次）：

```c
		if (clkspec.np == node && !clk_supplier) {
			of_node_put(clkspec.np);
			return 0;
		}
```

翻译成人话：**"这个 assigned-clocks 指向的时钟就住在我自己这个节点里，而现在这一轮
不是提供方注册轮，那就不是我该管的，跳过。"**

对写 DTS 的人来说，结论是一句话：

> **`assigned-clocks` 写在消费者节点上 → 该设备 probe 前生效（时机晚、但精确）；
> 写在提供方（CCU）节点上 → 提供方注册时就生效（时机早，用于设 CPU/NOC 这类
> 没有具体消费者设备的系统级时钟）。**

imx8mp 那一大段写在 CCU 节点上，正是因为 `IMX8MP_CLK_A53_CORE`、`IMX8MP_CLK_NOC`
这些时钟没有一个"消费者设备节点"可挂——它们服务的是整个 SoC。
rk3399 i2c1 那条写在消费者节点上，因为它只关心自己的 `SCLK_I2C1`。

### §4.3 两个静默跳过的行为

**(1) `assigned-clock-rates` 里的 0 是"跳过"，不是"设成 0Hz"。**
`__set_clk_rates` 的循环体第一行就是 `if (rate) {`（`drivers/clk/clk-conf.c:90-91`）。
所以下面这种写法是**合法且常见**的占位：

```dts
	assigned-clocks = <&clk A>, <&clk B>, <&clk C>;
	assigned-clock-rates = <100000000>, <0>, <200000000>;   /* B 只换父不设频率 */
```

**(2) 空 phandle（`<0>`）也是跳过。** 两个函数里都有：

```c
			if (rc == -ENOENT)
				continue;
```

这两条合起来意味着：**`assigned-clock-*` 的列表允许有"洞"**，而洞不产生任何日志。
如果你发现某一项没生效，先数一遍下标对齐——很可能是前面某项被静默跳过导致后面全错位。

---

## §5 死字与准死字属性

规则 14 要求：找不到消费者的属性必须显式标出来。clk 这边有两条，性质不同。

### §5.1 `clock-ranges`：核心有消费者，但 arm64 DTS 里 0 处使用

| 维度 | 结论 |
|---|---|
| 核心消费者 | **有**：`drivers/clk/clk.c:5095`/`:5099`，`of_parse_clkspec` 的向上遍历开关（§3.3 要点 4） |
| arm64 DTS 使用量 | **0**（`grep -rn 'clock-ranges' arch/arm64/boot/dts/ \| wc -l`） |
| 全 DTS 树使用量 | 8 个文件，全在非 arm64 架构下 |
| 绑定文档 | `Documentation/devicetree/bindings/clock/` 下**没有**；只出现在两个内存控制器绑定里（`Documentation/devicetree/bindings/memory-controllers/ti-aemif.txt:50`、以及同目录下的 `arm,pl172.txt`（这个文件名里带逗号，写成 `文件名:行号` 会被本项目的行号检查脚本误切，所以此处只给文件不给行号）） |

所以它在 arm64 上是**准死字**：机制活着，但没人用。
两条实际影响：

1. **不要指望"子节点自动继承父节点的 clocks"**。很多人以为 DT 里 clocks 有继承语义，
   其实没有——它需要父节点显式写 `clock-ranges` 才成立，而 arm64 上没人写。
2. 你在 arm64 上看到"消费者节点没有 `clocks` 属性但时钟居然能拿到"，**不可能是
   `clock-ranges` 的功劳**，去查别的路径（§0 的路径 ③ clkdev 或路径 ④ 固件）。

### §5.2 `protected-clocks`：厂商私有，核心完全不知道它

| 维度 | 结论 |
|---|---|
| `drivers/clk/*.c` 里的消费者 | **一个都没有**——核心根本不认识这条属性 |
| 唯一消费者 | `qcom_cc_drop_protected`，`drivers/clk/qcom/common.c:207-221` |
| arm64 DTS 使用量 | 12 行，全在高通平台 |
| 语义 | 列出的时钟 ID **从 clk 提供方表里摘掉**，于是 Linux 完全看不到它们 |

它的用途是"这些时钟归安全世界/别的处理器管，Linux 别碰"。
两条要点：

1. **在非高通平台上写它 = 完全无效且无日志**。因为没有任何代码读。
2. 它的效果是**时钟消失**，不是"时钟被锁住"。所以症状是消费者
   `clk_get` 拿到 **`-ENOENT`**（`clk_summary` 里也找不到），
   而不是 `clk_set_rate` 返回 `-EBUSY`。这个区别在 §9 表里要用到。

---

## §6 一条 clkspec 从 DTS 走到 `clk_hw` 的完整链路

这一节把前面的属性串成一条线，并交代它挂在 `03-internals.md` 时间线的哪一点上。

```
消费者 DTS                                    提供方 DTS
clocks = <&cru SCLK_I2C1>                     cru: clock-controller@ff760000 {
clock-names = "i2c"                                #clock-cells = <1>;
     │                                             ...
     │  ① 驱动 probe: devm_clk_get(dev, "i2c")   };
     ▼
 clk_get / of_clk_get_by_name
     │
     │  ② of_parse_clkspec(np, -1, "i2c", &spec)      drivers/clk/clk.c:5071-5105
     │       "i2c" ──在 clock-names 里找到下标 0──▶ index=0
     │       of_parse_phandle_with_args(np,"clocks","#clock-cells",0,&spec)
     │       这一步要读提供方的 #clock-cells 才知道吃几个 cell
     ▼
 spec = { np = cru 节点, args_count = 1, args[0] = SCLK_I2C1 }
     │
     │  ③ of_clk_get_hw_from_clkspec → __of_clk_get_hw_from_provider
     │       在全局 of_clk_providers 链表里按 spec.np 找注册记录
     ▼
 provider->get_hw(&spec, provider->data)
     │       = of_clk_hw_onecell_get(spec, hw_data)     drivers/clk/clk.c:4844-4855
     │       return hw_data->hws[ spec.args[0] ]        这里就是那份"下标 ABI"
     ▼
 struct clk_hw *
     │
     │  ④ clk_hw_create_clk：为这个消费者造一个独立的 struct clk 句柄
     ▼
 struct clk *   ← devm_clk_get 返回给驱动的东西
```

挂载点：整条链路发生在 `03-internals.md` **Part A 的消费者取用阶段**，也就是
提供方注册（§A4，`of_clk_add_hw_provider` 把记录挂进 `of_clk_providers`）**之后**。
所以：

**顺序倒了就是 `-EPROBE_DEFER`**。第 ③ 步在链表里找不到 `spec.np` 对应的记录，
返回 `-EPROBE_DEFER`，驱动核心把消费者放回队列稍后重试。这不是错误，是正常的
依赖排序机制——`05-usage.md` §2.1 那条"用 `dev_err_probe` 而不是 `dev_err`"就是为它准备的。

第 ④ 步值得单独记一句：**每个消费者拿到的 `struct clk *` 是独立的**，
同一个 `clk_hw` 被两个驱动 get 会产生两个句柄。这就是为什么频率范围约束
（`clk_set_rate_range`）是**per 句柄**的（`05-usage.md` §7）。

---

## §7 静默失效决策树

本节全部是"配错了但不报错"的情形。clk 的 DT 层有一个整体特征：
**报错的地方很少，静默降级的地方很多**。下面四棵树覆盖了绝大部分实际踩坑。

### §7.1 时钟拿到了，但频率不对

```
clk_get_rate() 的值和 DTS 里 assigned-clock-rates 不一致
 │
 ├─ dmesg 里有 "clk: couldn't set ... clk rate to ..." ?
 │   ├─ 有 ──▶ 不是静默失效，去 §9 查那条消息（通常是 .set_rate 拒绝或父不支持）
 │   └─ 没有 ──▼
 │
 ├─ assigned-clocks 和 assigned-clock-rates 的**项数与下标**对齐吗？
 │   └─ 中间有 <0> 占位项吗？0 会被静默跳过（§4.3），后面全部错位
 │
 ├─ assigned-clocks 写在**哪个节点**上？
 │   ├─ 写在提供方节点，但你以为它在消费者 probe 前生效
 │   │   ──▶ 它其实在提供方注册时就跑完了，之后被别人改回去了（§4.2）
 │   └─ 写在消费者节点，但那个时钟其实由消费者自己提供
 │       ──▶ clk_supplier == false，整项被静默 return 0 跳过（§4.2）
 │
 └─ 都对 ──▶ 目标时钟没有 .set_rate 且没有 CLK_SET_RATE_PARENT
             ──▶ clk_set_rate **静默返回 0**（`drivers/clk/clk.c:2445-2447`）
             ──▶ 这是 clk 子系统最坑的一条语义，见 `01-api.md` 的错误码节
```

### §7.2 时钟出现在 `clk_summary` 里，但父是 orphan / 没有层次

```
clk_summary 里看不到预期的父子层次
 │
 ├─ 平台是 SCMI 吗？
 │   └─ 是 ──▶ 正常。SCMI 的两张 ops 表都没有 set_parent/get_parent，
 │              clk_summary 必然是平的（§2.6、`04-extensions.md` §15.1）
 │
 ├─ 看 /sys/kernel/debug/clk/clk_orphan_summary
 │   └─ 你的时钟在里面 ──▶ 父时钟名字写错了
 │       注意：父名字写错**不产生任何错误**，只是变成孤儿
 │       （提供方侧三个互斥的 parent 字段见 `05-usage.md` §3.3）
 │
 └─ 提供方注册顺序问题 ──▶ 孤儿会在父时钟后续注册时自动被收养，
     所以"启动早期是孤儿"不一定是 bug；以 late_initcall 之后的快照为准
```

### §7.3 设备起来了，但时钟从来没开

这是本篇危险度最高的一条，因为**设备表面正常工作了一段时间**（时钟可能被别的
消费者或 bootloader 开着），直到某次改动后突然挂掉。

```
怀疑某个时钟没被打开
 │
 ├─ 驱动用的是 devm_clk_get_optional* 吗？
 │   └─ 是 ──▼ 高危组合
 │       │
 │       ├─ DTS 的 clock-names 里有驱动要的那个名字吗？
 │       │   └─ 没有 ──▶ 返回 **NULL 而不是错误**
 │       │       ──▶ 后续 clk_prepare_enable(NULL) **静默成功返回 0**
 │       │       ──▶ 时钟从未被打开，且**一条日志都没有**
 │       │
 │       └─ 诊断动作：clk_get_rate(clk) == 0 基本可断定拿到的是 NULL
 │
 ├─ 驱动用的是 clk_bulk_get_all 吗？
 │   └─ 它返回的是**时钟个数**不是 0/负数，写 `if (ret)` 判错就永远走错误分支
 │       （`05-usage.md` §2.3）
 │
 └─ 平台是高通吗？
     └─ 检查 protected-clocks 有没有把这个 ID 摘掉（§5.2）
        ──▶ 症状是 clk_get 拿到 -ENOENT 且 clk_summary 里根本没这个时钟
```

### §7.4 提供方节点写好了，消费者却一直 `-EPROBE_DEFER`

```
消费者反复 EPROBE_DEFER，提供方驱动看起来 probe 成功了
 │
 ├─ 提供方调了 of_clk_add_hw_provider / devm_of_clk_add_hw_provider 吗？
 │   └─ 没有 ──▶ 这是最常见的一条：**注册时钟**和**让别人找得到**是两步，
 │       只做第一步，clk_summary 里有你的时钟，但 DT 查不到（`05-usage.md` §3.1）
 │
 ├─ #clock-cells 的值和 get 回调匹配吗？（§2.2 的表）
 │   └─ 不匹配时不一定报错：#clock-cells = <0> 配 of_clk_hw_onecell_get，
 │       args_count 是 0，`clkspec->args[0]` 读到的是**未初始化/残留值**
 │       ──▶ 通常撞上 `idx >= hw_data->num` 那道检查，
 │           打印 "of_clk_hw_onecell_get: invalid index N"（`drivers/clk/clk.c:4849`）
 │
 └─ 提供方是 builtin_platform_driver 吗？
     └─ clk-gpio 是（`04-extensions.md` §14.1），**它不能编成模块**；
        编成模块的话驱动根本不会加载
```

---

## §8 三层定位

### §8.1 编译期

| 检查项 | 命令 / 动作 | 抓什么 |
|---|---|---|
| DTS 语法 + phandle | `make dtbs` | cell 数不匹配、phandle 悬空 |
| 绑定 schema 校验 | `make dt_binding_check` / `make CHECK_DTBS=y dtbs_check` | ⚠️ **clk 通用属性查不出来**——通用 schema 已搬出本树（见篇首警告） |
| 提供方驱动在不在内核里 | `grep -n 'CONFIG_COMMON_CLK' .config`；具体驱动看 `drivers/clk/Kconfig` | `compatible` 匹配不上的根因往往是驱动没编进去 |
| 提供方能不能编成模块 | 看驱动结尾是 `module_platform_driver` 还是 `builtin_platform_driver` | clk-gpio 是 builtin，clk-pwm 是 module（`04-extensions.md` §14） |

### §8.2 运行期静态（不改任何状态，只读）

| 想知道 | 看哪里 |
|---|---|
| DTB 里这条属性到底进去了没有 | `/sys/firmware/devicetree/base/<路径>/<属性>`，用 `xxd` 看；或 `dtc -I fs -O dts /sys/firmware/devicetree/base` |
| 提供方注册成功了没有 | `/sys/kernel/debug/clk/clk_summary` 里有没有它导出的时钟名 |
| 时钟树的形状 | 同上，看缩进层次 |
| 有没有孤儿 | `/sys/kernel/debug/clk/clk_orphan_summary` |
| 某个时钟的详细参数 | `/sys/kernel/debug/clk/<clk名>/` 下的 `clk_rate`、`clk_flags`、`clk_prepare_count`、`clk_enable_count` |
| 消费者到底 get 到了几个时钟 | `/sys/kernel/debug/clk/clk_summary` 最右边的引用计数列 |

一个高价值的对照动作：**把 DTS 里 `clock-output-names` 的字符串和
`clk_summary` 里的时钟名逐个对**。对不上就说明提供方驱动没读 DTS 里那个名字
（回看 §2.3——只有三个基础驱动读它）。

### §8.3 运行期动态（会改状态，只用于调试）

| 手段 | 怎么用 | 风险 |
|---|---|---|
| `clk_ignore_unused` | 加到内核 cmdline | **arm64 启动挂死第一诊断手段**：挂死消失就说明是 `clk_disable_unused` 关掉了某个没人声明的时钟。dmesg 会出现 `clk: Not disabling unused clocks`（`drivers/clk/clk.c:1466`） |
| 写 debugfs 改频率/父 | **没有 Kconfig 开关**，只能改源码：`drivers/clk/clk.c:3352` 那行 `#undef CLOCK_ALLOW_WRITE_DEBUGFS` 必须自己删掉再重编 | 内核会打一大段 `WRITEABLE clk DebugFS SUPPORT HAS BEEN ENABLED` 的安全警告（`drivers/clk/clk.c:3686-3699`），生产内核不要开。详见 `07-debug.md` |
| tracepoint | `echo 1 > /sys/kernel/tracing/events/clk/enable` | 见 `07-debug.md` |

（三层定位的完整清单在 `07-debug.md`，本节只列**和 DTS 配置直接相关**的那部分。）

---

## §9 错误消息 → 原因映射表

全部逐字回到源码 grep 过。`%pOF` 会被内核打印成出问题的那个**节点路径**——
这是定位 DTS 问题最直接的线索。

| 内核打印（逐字） | 出处 | 真实原因 | 先查什么 |
|---|---|---|---|
| `clk: invalid value of clock-parents property at <节点>` | `drivers/clk/clk-conf.c:23` | `assigned-clock-parents` 的 cell 数凑不成整组 | **消息里写的是 `clock-parents`，属性名其实是 `assigned-clock-parents`**——按消息里的字串去 grep DTS 会 grep 不到 |
| `clk: couldn't get parent clock N for <节点>` | `drivers/clk/clk-conf.c:44` | `assigned-clock-parents` 第 N 项的 phandle 解析不到提供方 | 这条**不包括** `-EPROBE_DEFER`（源码显式排掉了），所以看到它就是真错 |
| `clk: couldn't get assigned clock N for <节点>` | `drivers/clk/clk-conf.c:62` | `assigned-clocks` 第 N 项解析不到 | 同上，`-EPROBE_DEFER` 已排除 |
| `clk: failed to reparent <子> to <父>: <码>` | `drivers/clk/clk-conf.c:70` | `clk_set_parent` 被拒 | 码是 `-EPERM` → 该时钟没有 `.set_parent`；码是 `-EBUSY` → `CLK_SET_PARENT_GATE` 且 `prepare_count` 非 0（`01-api.md`） |
| `clk: couldn't get clock N for <节点>` | `drivers/clk/clk-conf.c:110` | 设频率阶段解析 `assigned-clocks` 第 N 项失败 | 和上面那条同因不同阶段（父/频率各一份） |
| `clk: couldn't set <名> clk rate to <值> (<码>), current rate: <当前>` | `drivers/clk/clk-conf.c:117` | `assigned-clock-rates` 设不上 | 这条**带当前频率**，最有用：当前值就是硬件实际能给的 |
| `of_clk_hw_onecell_get: invalid index N` | `drivers/clk/clk.c:4849` | 消费者写的 ID 超出提供方 `hws[]` 长度 | `#clock-cells` 和 get 回调不匹配也会撞这里（§7.4） |
| `of_clk_src_onecell_get: invalid clock index N` | `drivers/clk/clk.c:4835` | 同上，但走的是老的 `struct clk *` 版接口 | 见到它说明提供方用的是**已废弃**的 `clk_onecell_data` |
| `clk: Not disabling unused clocks` | `drivers/clk/clk.c:1466` | 你加了 `clk_ignore_unused` | 这是**你自己要求的**，不是故障；但它意味着系统里有时钟没人声明 |
| `<函数>: <名> must implement .get_parent & .set_parent` 等四条 | `drivers/clk/clk.c:3811`/`:3818`/`:3825`/`:3833` | 提供方 `clk_ops` 填得不全，**注册就被拒** | 这是提供方代码问题不是 DTS 问题，见 `05-usage.md` §5 |
| `<函数>: critical clk '<名>' failed to prepare` / `to enable` | `drivers/clk/clk.c:3938`/`:3945` | 带 `CLK_IS_CRITICAL` 的时钟起不来 | 只是 `pr_warn`，**注册仍然成功**——所以这条很容易被刷过去 |

表里最实用的一条是**倒数第三条**：`clk: Not disabling unused clocks` 是判断
"启动挂死是不是 clk 引起的"的**确认信号**。流程是：挂死 → 加 `clk_ignore_unused`
→ 起来了且 dmesg 有这条 → 用 `clk_summary` 对比"加参数前后哪些时钟状态不同"，
差集就是缺声明的那些（机制在 `03-internals.md` 的 `clk_disable_unused` 一节）。

表里**没有**任何一条对应 §7 的静默失效——这不是遗漏，是本篇的核心结论：

> **clk 的 DT 层，报错的路径都相对好查；真正花时间的全是不报错的那些。**
> 所以拿到一个"DTS 改了没效果"的问题，第一动作不是搜 dmesg，是去 §7 走决策树。

---

## §10 读完这篇之后

| 你接下来想干的事 | 去哪篇 |
|---|---|
| 我要按角色抄一份代码骨架 | `05-usage.md` |
| 我要查某个 API 的返回值和错误码语义 | `01-api.md` |
| 我要搞清 `clk_hw` / `clk_core` / `clk` 三个类型的区别 | `02-datastructures.md` |
| 我要知道注册和取用在时间线上的先后 | `03-internals.md` Part A |
| 我要用 debugfs / tracepoint 定位 | `07-debug.md` |
| SCMI / clkdev / clk-gpio 这些"周边框架"的机制 | `04-extensions.md` |
| 我想用大白话理解 prepare/enable 为什么要分开 | `08-feynman.md` |
| 本篇每条论断的出处和未验证项 | `99-evidence.md` |
