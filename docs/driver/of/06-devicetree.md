---
title: 设备树
---

# of 06：设备树怎么写、谁消费、配错了怎么定位

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ **本篇和其它子系统的 06 篇性质不同**：
> 别的子系统的 06 篇讲"我这个子系统读哪些属性"，
> **本子系统就是"读 DT"本身** ⇒ 本篇讲的是**DTS 的通用语法与约定**。
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 按任务找章节

| 你想干的事 | 去哪一节 |
|---|---|
| 我想知道 DTS 的基本结构 | §1 |
| `#address-cells` / `#size-cells` 到底怎么用 | §2 |
| `reg` 和 `ranges` 的关系 | §3 |
| `status` 有哪些合法值 | §4 |
| phandle 和 `#xxx-cells` | §5 |
| `compatible` 怎么写 | §6 |
| 哪些节点会变成 `platform_device` | §7 |
| 我要写绑定文档 | §8 |
| 给我一份能抄的 arm64 完整实例 | §9 |
| 配好了但不生效，怎么定位 | §10 |
| dmesg 里有一句报错，想知道是哪里配错了 | §11 |

---

## §1 DTS 的基本结构

### §1.1 四种成分

```dts
/dts-v1/;                              /* 版本声明 */

/ {                                    /* ① 根节点 */
	model = "My Board";
	compatible = "myvendor,myboard";
	#address-cells = <2>;              /* ② 属性 */
	#size-cells = <2>;

	soc {                              /* ③ 子节点 */
		compatible = "simple-bus";
		#address-cells = <2>;
		#size-cells = <2>;
		ranges;

		uart0: serial@12340000 {       /* ④ 标签（label）+ 节点名@单元地址 */
			compatible = "myvendor,uart";
			reg = <0x0 0x12340000 0x0 0x1000>;
			interrupts = <GIC_SPI 25 IRQ_TYPE_LEVEL_HIGH>;
			clocks = <&clkctrl 5>;
		};
	};
};
```

| 成分 | 内核里对应什么 |
|---|---|
| 节点 | `struct device_node`（`include/linux/of.h:52`） |
| 属性 | `struct property`（`include/linux/of.h:32`） |
| 节点名 | `np->name`（**不含 `@`**，`include/linux/of.h:53`） |
| 节点名@单元地址 | `np->full_name`（**含 `@`**，`include/linux/of.h:55`） |
| 标签（`uart0:`） | **DTS 源码概念** —— 编译后变成 `phandle` 属性（02 §1.0.B） |

**单元地址（`@12340000`）的约定**：应该等于 `reg` 的第一个地址。
⇒ 它只是**命名约定**，内核**不校验** —— 但 `dtbs_check` 会（§8）。

### §1.2 属性的四种值类型

| 类型 | DTS 写法 | 内核里读 |
|---|---|---|
| **整数（数组）** | `reg = <0x0 0x1000>;` | `of_property_read_u32()` 家族（01 §4.2） |
| **字符串（列表）** | `compatible = "a", "b";` | `of_property_read_string()` / `_index()` / `of_property_match_string()` |
| **布尔（存在即真）** | `dma-coherent;` | `of_property_read_bool()` |
| **phandle（+参数）** | `clocks = <&clk 5>;` | `of_parse_phandle_with_args()`（§5） |
| 字节数组 | `mac = [00 11 22 33 44 55];` | `of_property_read_u8_array()` |

⚠️ **`of_property_read_bool()` 只看"存在不存在"**（01 §4.2）——
```dts
	some-flag = <0>;    /* ⚠️ of_property_read_bool() 仍然返回 true！ */
```
⇒ **布尔属性一律写成无值形式 `some-flag;`**，别写 `= <0>` / `= <1>`。

### §1.3 整数全是大端

DTB 格式规定所有整数是**大端**（01 §4.5）。
⇒ **用 `of_property_read_u32()` 家族，它替你转好了**；
`of_get_property()` 返回的是原始字节，要自己 `be32_to_cpup()`。

---

## §2 `#address-cells` / `#size-cells`

### §2.1 它们决定"父节点的孩子的 `reg` 怎么切"

```dts
	parent {
		#address-cells = <2>;      /* 孩子的 reg 里地址占 2 个 u32 */
		#size-cells = <1>;         /* 大小占 1 个 u32 */

		child@0 {
			reg = <0x0 0x12340000 0x1000>;
			/*      ▲──── 地址（2 cells）───▲  ▲ 大小（1 cell） */
		};
	};
```

**三条铁律**：

| # | 铁律 | 依据 |
|---|---|---|
| ① | **写在父节点上，管孩子** | `of_n_addr_cells()`（`drivers/of/base.c:108`）**沿父链往上找** |
| ② | **每一层可以不同** | 见 §9.2 的真实例子（三层里第三层从 2 变成 1） |
| ③ | **有 `reg` 的节点，它的父节点必须声明这两个** | 否则地址解析失败 |

| 消费代码 | 位置 |
|---|---|
| `of_n_addr_cells()` | `drivers/of/base.c:108` |
| `of_n_size_cells()` | `drivers/of/base.c:129` |
| 早期扫描时记下根节点的这两个值 | `drivers/of/fdt.c:1060` 的 `early_init_dt_scan_root()` |
| 地址翻译时每层重新取 | `drivers/of/address.c:537`（本层）· `:579`（父层） |

### §2.2 ⚠️ arm64 上的惯例：都是 2

arm64 是 64 位地址空间 ⇒ 需要 2 个 u32 才能表示一个地址。
⇒ **根节点和大多数总线节点都写 `#address-cells = <2>; #size-cells = <2>;`**。

⚠️ **例外**：地址空间小于 4GB 的子总线可以用 1
（见 §9.2 的 `cbass_wakeup`）。

### §2.3 ⚠️ 写错的三种表现

| 错法 | 表现 |
|---|---|
| 父节点**少写**了这两个属性 | 地址解析失败 ⇒ `devm_platform_ioremap_resource()` 返回错误 |
| 值**写小了**（该 2 写了 1） | **解析出一个完全不相关的地址**（高 32 位被当成地址、低 32 位被当成大小） |
| 值**写大了** | 同上，或 `pr_err("Bad cell count for %pOF")`（`drivers/of/address.c:581`） |

⇒ **第二种最危险**：不报错，`ioremap` 一个错地址，
表现是"寄存器读出来全是 0 或 0xffffffff"（§10.3）。

---

## §3 `reg` 与 `ranges`

### §3.1 `reg` 是"相对父总线的地址"

这是 DT 里最反直觉的一条（02 §1.0.D）：
`reg = <0x12340000 0x1000>` **不一定是 CPU 物理地址**。

```
   CPU 看到的物理地址
        ▲
        │ 根节点（没有 ranges，到这里就是物理地址）
   ┌────┴────────────────────────────────────────┐
   │  soc { ranges = <0x0 0x40000000 0x10000>; }  │
   │       ▲── 子地址   ▲── 父地址    ▲── 大小     │
   └────┬────────────────────────────────────────┘
        │ 子节点的 reg = <0x1000 0x100>
        ▼
   ⇒ 实际物理地址 = 0x40000000 + 0x1000 = 0x40001000
```

### §3.2 `ranges` 的三种形态

| 形态 | 语义 | 依据 |
|---|---|---|
| `ranges;`（**空**） | **一一映射**（子地址 = 父地址） | `pr_debug("empty ranges; 1:1 translation")`（`drivers/of/address.c:471`） |
| `ranges = <子地址 父地址 大小>, …;` | 一个或多个"窗口" | `pr_debug("walking ranges...")`（`drivers/of/address.c:475`） |
| **没有 `ranges` 属性** | **不可翻译** ⇒ 子节点的 `reg` 无法转成物理地址 | `pr_debug("no ranges; cannot translate")`（`drivers/of/address.c:464`） |

**每一项的长度** = 子的 `#address-cells` + **父的** `#address-cells` + 子的 `#size-cells`。

⚠️ **第三种（没有 `ranges`）不是错误** ——
它的意思是"这个节点下面的地址不在 CPU 地址空间里"（如 I2C 从设备的地址、
SPI 的 chip select）。
⇒ **那些节点的子节点不该有需要 `ioremap` 的 `reg`**。

### §3.3 翻译是逐层的

`__of_translate_address()`（`drivers/of/address.c:513`）从当前节点一路走到根，
**每一层用父节点的 `ranges` 做一次窗口变换**（03 §B4.1）。

| 关键位置 | 行号 |
|---|---|
| 主循环 | `drivers/of/address.c:549` |
| 每层调 `of_translate_one()` | `drivers/of/address.c:589` |
| 到根 ⇒ 直接读出来 | `drivers/of/address.c:558-562` |
| `of_translate_one()` 实现 | `drivers/of/address.c:433` |
| 没有 `ranges` | `drivers/of/address.c:464` |
| 空 `ranges`（1:1） | `drivers/of/address.c:471` |
| 地址不在任何窗口里 | `drivers/of/address.c:486`（`pr_debug("not found !")`） |

**三条 `pr_debug` 就是三种失败原因的判据**（§10.3、03 §B4.2）。

### §3.4 `dma-ranges`：DMA 视角的地址

设备做 DMA 时看到的地址可能和 CPU 不同 ⇒ 用 `dma-ranges`（格式同 `ranges`）。

| API | 位置 |
|---|---|
| `of_translate_dma_address()` | `drivers/of/address.c:652` |
| `of_dma_is_coherent()`（读 `dma-coherent` 属性） | `drivers/of/address.c:1119` |

⚠️`dma-coherent` 缺失时算不算 coherent，取决于
`CONFIG_OF_DMA_DEFAULT_COHERENT`（`drivers/of/Kconfig:93`）——
⇒ **平台相关，不要假设**。

---

## §4 `status`：让节点"不生效"

### §4.1 合法值只有几个

| 值 | `of_device_is_available()` 返回 | 依据 |
|---|---|---|
| **属性不存在** | **true** | `drivers/of/base.c:620-622` |
| `"okay"` / `"ok"` | **true** | `drivers/of/base.c:625` |
| `"disabled"` | **false** | 落到 `drivers/of/base.c:629` 的 `return false` |
| `"fail"` / `"fail-…"` | false（另有 `__of_device_is_fail()` 专门判它） | `drivers/of/base.c:661`（判断在 `:672`） |
| 其它任何值 | **false** | `drivers/of/base.c:629` |

| 元素 | 位置 |
|---|---|
| `__of_device_is_available()` | `drivers/of/base.c:612`（doc 在 `:609`） |
| `of_device_is_available()`（加锁的对外版） | `drivers/of/base.c:640` |
| `__of_device_is_fail()` | `drivers/of/base.c:661`（doc 在 `:654`） |
| CPU 节点对 `"fail"` 的特殊处理 | `drivers/of/base.c:821` 那段注释 |

⚠️ **拼错就是 disabled**：写 `status = "enable";` 或 `status = "OK";`
⇒ **`of_device_is_available()` 返回 false，节点被静默跳过**（§10.2）。
⇒ 只有 `"okay"` 和 `"ok"` 两个字符串（小写）是有效的。

### §4.2 谁会检查它

| 检查点 | 位置 |
|---|---|
| 设备填充 | `drivers/of/platform.c:175`（`of_platform_device_create_pdata()` 的第一道门） |
| 遍历"可用"子节点 | `of_get_next_available_child()`（`drivers/of/base.c:815`）⇒ `for_each_available_child_of_node()` |
| 驱动自己 | `of_device_is_available()`（01 §6） |

⚠️ **`for_each_child_of_node()` 不检查 status！**（01 §3.1）
⇒ 遍历子节点时应该用 `for_each_available_child_of_node()`。

### §4.3 典型用法：SoC dtsi 里默认 disabled，板级 dts 里开

```dts
/* SoC 的 .dtsi */
	main_uart2: serial@2810000 {
		compatible = "ti,am654-uart";
		reg = <0x0 0x02810000 0x0 0x100>;
		status = "disabled";        /* 默认关 */
	};

/* 板级 .dts */
&main_uart2 {
	status = "okay";               /* 这块板子上接了东西，打开 */
	pinctrl-0 = <&main_uart2_pins>;
};
```

**这是 arm64 上最常见的 DTS 组织方式**：
SoC 有的外设不是每块板子都接线 ⇒ dtsi 里全 disabled，板级按需打开。

---

## §5 phandle 与 `#xxx-cells`

### §5.1 三方约定

```dts
	/* ① 提供者：声明"引用我要带几个参数" */
	clkctrl: clock-controller@10000 {
		#clock-cells = <1>;
	};

	dmac: dma-controller@20000 {
		#dma-cells = <2>;
	};

	/* ② 消费者：按提供者声明的个数写 */
	mydev {
		clocks = <&clkctrl 5>, <&clkctrl 6>;      /* 每项 1 个参数 */
		clock-names = "core", "bus";
		dmas   = <&dmac 3 0>, <&dmac 4 0>;        /* 每项 2 个参数 */
		dma-names = "tx", "rx";
	};
```

| 谁定义参数个数 | 依据 |
|---|---|
| **提供者节点的 `#<name>-cells`** | `of_parse_phandle_with_args(np, "clocks", "#clock-cells", …)` 的第三个参数（01 §5.2） |

**参数的含义由那个子系统定义**（05 §3.3）——
`drivers/of/` 只负责"切出正确个数的 u32"。

### §5.2 `xxx-names` 的约定

几乎所有 phandle 列表都配一个 `xxx-names` 字符串列表 ——
让消费者能按名字取而不是按索引：

| 列表属性 | 名字属性 | 按名字取的 API |
|---|---|---|
| `clocks` | `clock-names` | `devm_clk_get(dev, "core")` |
| `interrupts` / `interrupts-extended` | `interrupt-names` | `platform_get_irq_byname()` |
| `dmas` | `dma-names` | `dma_request_chan(dev, "tx")` |
| `resets` | `reset-names` | `devm_reset_control_get(dev, "main")` |
| `memory-region` | `memory-region-names` | `of_reserved_mem_device_init_by_name()` |

**按名字取比按索引稳** —— DTS 里增删一项不会打乱其它的。

⚠️`memory-region-names` 虽然有 API 支持（`drivers/of/of_reserved_mem.c:396`），
但 **remoteproc 里零使用者**（remoteproc 那套的设备树篇里点明过）。

### §5.3 ⚠️ 参数个数不匹配的表现

| 情况 | 表现 |
|---|---|
| DTS 给的参数**少于** `#xxx-cells` | `of_parse_phandle_with_args()` 返回 `-EINVAL` |
| 给的**多于** | 多余的被当成**下一项**的开头 ⇒ **解析出完全错误的引用** |
| 提供者**忘了写** `#xxx-cells` | 按 0 个参数解析 ⇒ 同上 |

⇒ **第二、三种最危险** —— 不报错但引用错了对象。

---

## §6 `compatible`

### §6.1 格式与顺序

```dts
	compatible = "myvendor,soc2-uart", "myvendor,soc1-uart", "ns16550a";
	/*            ▲ 最具体                                    ▲ 最通用 */
```

| 规则 | 依据 |
|---|---|
| 格式是 `"<vendor>,<device>"` | 约定 |
| **最具体的写在最前面** | `of_device_is_compatible()` 返回"分数"，越靠前分越高（`drivers/of/base.c:558`，01 §6） |
| 后面的项是"我也兼容这些" | ⇒ **老驱动能驱动新硬件** |

vendor 前缀要登记在
`Documentation/devicetree/bindings/vendor-prefixes.yaml` 里（§8）。

### §6.2 根节点的 `compatible` 标识"板子"

```dts
/ {
	model = "Texas Instruments AM654 Base Board";
	compatible = "ti,am654-evm", "ti,am654";
};
```

| API | 位置 |
|---|---|
| `of_machine_is_compatible(compat)` | `drivers/of/base.c:602` |

用途：平台代码里做板级 quirk（"这块板子的某个引脚接反了"）。

---

## §7 哪些节点会变成 `platform_device`

### §7.1 三个条件（02 §1.0.A.2 的 DTS 视角）

| # | 条件 | 依据 |
|---|---|---|
| ① | 节点 `status` 可用（§4） | `drivers/of/platform.c:175` |
| ② | 还没被填充过 | `drivers/of/platform.c:176` |
| ③ | **它的父节点的 compatible 在白名单里** | `drivers/of/platform.c:24-32` |

**白名单只有四项**：

| compatible | 行号 |
|---|---|
| `"simple-bus"` | `drivers/of/platform.c:25` |
| `"simple-mfd"` | `drivers/of/platform.c:26` |
| `"isa"` | `drivers/of/platform.c:27` |
| `"arm,amba-bus"` | `drivers/of/platform.c:29` |

另外**根节点的直接子节点总会被填充**（`of_platform_populate()`
从 root 开始遍历，`drivers/of/platform.c:465`）。

### §7.2 三种"让子节点也变成设备"的办法

| 办法 | 怎么做 | 什么时候用 |
|---|---|---|
| A | 父节点 compatible 加 `"simple-bus"` | 父节点**自己不需要驱动**（纯地址容器） |
| B | 父节点 compatible 加 `"simple-mfd"` | 父节点有寄存器但不需要驱动做什么 |
| C | **父节点的驱动调 `devm_of_platform_populate()`** | **父节点自己也要 probe**（05 §2） |

⚠️ **A/B 和 C 的区别**：
A/B 让子节点在**启动填充阶段**就变成设备（父节点可能还没 probe）；
C 让子节点在**父节点 probe 之后**才变成设备。
⇒ **子设备依赖父设备先初始化时必须用 C**。

### §7.3 ⚠️ 三类"不该变成 platform_device"的子节点

| 类 | 例子 | 为什么 |
|---|---|---|
| 配置数据 | pinctrl 的 pin group、regulator 的 `regulators { }` 子节点 | 它们没有独立的资源，只是参数 |
| 别的总线上的设备 | I2C 从设备、SPI 从设备 | 它们由 **I2C/SPI 核心**枚举，不是 platform |
| graph 的 `ports`/`port`/`endpoint` | 媒体连接描述（04 §3） | 只是拓扑描述 |

⇒ **这三类的父节点都不该有 `"simple-bus"` / `"simple-mfd"`**。

---

## §8 绑定文档

### §8.1 没有绑定文档的属性等于不存在

上游的规则：**任何新的 compatible 和属性必须有一份 YAML 绑定文档**，
放在 `Documentation/devicetree/bindings/<subsystem>/<vendor>,<device>.yaml`。

| 作用 | 说明 |
|---|---|
| ① 它是**规范** | 别人（包括 U-Boot、其它 OS）按它写 DTS |
| ② 它是**可执行的校验** | `make dtbs_check` 用它检查所有 DTB |
| ③ 它是**稳定 ABI 的载体** | 02 §1.0.E |

### §8.2 三个常用命令

```bash
# 校验所有 arm64 DTB
make ARCH=arm64 dtbs_check

# 只校验某一个绑定
make ARCH=arm64 dtbs_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/serial/myvendor,uart.yaml

# 校验绑定文档本身的语法
make dt_binding_check DT_SCHEMA_FILES=…
```

⚠️ 需要装 `dtschema`（`pip install dtschema`）—— **本套未实测**（99 §5.1）。

### §8.3 `additionalProperties: false` 的意义

绑定 YAML 里写了这一行，`dtbs_check` 就会**报出所有未声明的属性** ——
⇒ **这是抓"属性名拼错"的唯一自动化手段**（§10.1）。

⚠️ 不是所有老绑定都写了它 ⇒ 老平台上拼错的属性可能查不出来。

---

## §9 完整的 arm64 实例（本树真实节点，可以 diff）

### §9.1 根节点

```dts
/* arch/arm64/boot/dts/ti/k3-am65.dtsi */
/ {
	model = "Texas Instruments K3 AM654 SoC";
	compatible = "ti,am654";
	interrupt-parent = <&gic500>;      /* 一次声明，全树受益 */
	#address-cells = <2>;              /* arm64 惯例 */
	#size-cells = <2>;

	aliases {
		serial0 = &wkup_uart0;          /* of_alias_get_id() 读它 */
		serial1 = &mcu_uart0;
		…
	};

	chosen { };
};
```

| 元素 | 行号 |
|---|---|
| `model` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:15` |
| `compatible = "ti,am654"` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:16` |
| `interrupt-parent` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:17` |
| `#address-cells = <2>` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:18` |
| `#size-cells = <2>` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:19` |
| `aliases` 节点 | `arch/arm64/boot/dts/ti/k3-am65.dtsi:21` |
| 第一条 alias | `arch/arm64/boot/dts/ti/k3-am65.dtsi:22` |

### §9.2 三层嵌套的 `ranges`（本篇最有价值的一段）

```dts
	cbass_main: bus@100000 {                       /* 第 1 层 */
		compatible = "simple-bus";
		#address-cells = <2>;
		#size-cells = <2>;
		ranges = <0x00 0x00100000 0x00 0x00100000 0x00 0x00020000>, /* ctrl mmr */
		         …（共 22 个窗口）…;

		cbass_mcu: bus@28380000 {                  /* 第 2 层 */
			compatible = "simple-bus";
			#address-cells = <2>;
			#size-cells = <2>;
			ranges = <0x00 0x28380000 0x00 0x28380000 0x00 0x03880000>,
			         …（共 13 个窗口）…;

			cbass_wakeup: bus@42040000 {           /* 第 3 层 */
				compatible = "simple-bus";
				#address-cells = <1>;   /* 从 2 变成 1！ */
				#size-cells = <1>;
				ranges = <0x42040000 0x00 0x42040000 0x03ac2400>;
				/*         ▲子地址(1)  ▲父地址(2)   ▲大小(1)      */
			};
		};
	};
```

| 元素 | 行号 |
|---|---|
| 第 1 层 `cbass_main` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:66` |
| 它的 cells | `arch/arm64/boot/dts/ti/k3-am65.dtsi:68-69` |
| 它的 `ranges`（22 个窗口） | `arch/arm64/boot/dts/ti/k3-am65.dtsi:70` 起 |
| 第 2 层 `cbass_mcu` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:94` |
| 它的 cells | `arch/arm64/boot/dts/ti/k3-am65.dtsi:96-97` |
| 它的 `ranges` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:98` 起 |
| 第 3 层 `cbass_wakeup` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:112` |
| **`#address-cells = <1>`（从 2 变成 1）** | `arch/arm64/boot/dts/ti/k3-am65.dtsi:114` |
| `#size-cells = <1>` | `arch/arm64/boot/dts/ti/k3-am65.dtsi:115` |
| 它的 `ranges`（1 项） | `arch/arm64/boot/dts/ti/k3-am65.dtsi:117` |

**第 3 层那一行 `ranges = <0x42040000 0x00 0x42040000 0x03ac2400>;`
是理解 `ranges` 格式的最佳例子**：

| 段 | cells 数 | 来自哪 | 值 |
|---|---|---|---|
| 子地址 | **1** | **本节点**的 `#address-cells`（`:114`） | `0x42040000` |
| 父地址 | **2** | **父节点**（`cbass_mcu`）的 `#address-cells`（`:96`） | `0x00 0x42040000` |
| 大小 | **1** | **本节点**的 `#size-cells`（`:115`） | `0x03ac2400` |
| 合计 | **4** 个 u32 | —— | 和 DTS 里那一行正好 4 个数 |

⇒ **这就是 §3.2 那句"每一项的长度 = 子 addr + 父 addr + 子 size"的实证**。

### §9.3 三段分工

| 层 | 文件 | 放什么 |
|---|---|---|
| SoC 公共 | `k3-am65.dtsi` | 根节点、CPU、总线层级和 `ranges`、`aliases` |
| SoC 外设 | `k3-am65-main.dtsi` · `k3-am65-mcu.dtsi` | 各外设节点（默认 `status = "disabled"`） |
| 板级 | `k3-am654-base-board.dts` | **打开需要的外设 + reserved-memory + 板上器件** |

前两层通过 `#include` 拼起来（`k3-am65.dtsi` 末尾）。

---

## §10 定位：三层排查

### §10.1 第一层：编译期

```bash
# ① 绑定校验（抓属性名拼错、cells 数不对）
make ARCH=arm64 dtbs_check

# ② 反编译看展开后的值（宏都变成裸数字了）
dtc -I dtb -O dts -o /tmp/x.dts arch/arm64/boot/dts/<vendor>/<board>.dtb

# ③ 只看某个节点
dtc -I dtb -O dts /boot/board.dtb | sed -n '/uart@12340000/,/};/p'
```

**② 是本篇最有用的一条命令**：
`GIC_SPI` / `IRQ_TYPE_LEVEL_HIGH` / `<&clkctrl 5>` 这些在反编译输出里
全变成裸数字和 `phandle` 值 ⇒ **一眼看出你写的到底是什么**。

| 能查出来 | 查不出来 |
|---|---|
| 属性名拼错（绑定有 `additionalProperties: false` 时，§8.3） | ⚠️ **`status` 值拼错**（`"enable"` 而不是 `"okay"`） |
| cells 数和绑定声明不符 | ⚠️ **属性写在了错误的层级** |
| 缺必需属性 | ⚠️ phandle 指向了错误的节点 |

### §10.2 第二层：运行期静态（节点/设备有没有建出来）

```sh
# ① 内核实际解析出来的树（04 §4）
ls /sys/firmware/devicetree/base/
ls /sys/firmware/devicetree/base/bus@100000/

# ② status 生效了吗
cat /sys/firmware/devicetree/base/.../serial@2810000/status | tr -d '\0'; echo
#   → okay  ⇒ 可用；disabled ⇒ 被跳过；其它任何值也等于跳过（§4.1）

# ③ platform_device 建出来了吗
ls /sys/bus/platform/devices/ | grep 2810000

# ④ 驱动匹配上了吗
ls -l /sys/bus/platform/devices/2810000.serial/driver
```

**四步各自对应一个原因**：

| 步 | 空/不对说明 |
|---|---|
| ① | 节点根本不在树里（DTS 没编进这个 DTB，或 `#include` 漏了） |
| ② | `status` 不是 `"okay"`/`"ok"`（**包括拼错**） |
| ③ | **父节点不在填充白名单里**（§7.1 条件 ③） |
| ④ | compatible 不匹配驱动的 `of_match_table` |

### §10.3 第三层：运行期动态（设备建了但行为不对）

```sh
# ① 地址翻译失败 ⇒ 打开 pr_debug（03 §B4.2）
echo 'file address.c +p' > /sys/kernel/debug/dynamic_debug/control
echo <设备> > /sys/bus/platform/drivers/<驱动>/unbind
echo <设备> > /sys/bus/platform/drivers/<驱动>/bind
dmesg | tail -40
#   "no ranges; cannot translate"     ⇒ 父节点缺 ranges
#   "not found !"                     ⇒ 地址不在任何窗口里
#   "Bad cell count for %pOF"（pr_err）⇒ #address-cells 不合法

# ② 看 resource 翻译出来是什么
cat /sys/bus/platform/devices/2810000.serial/of_node/reg | hexdump -C
grep -i serial /proc/iomem

# ③ 属性读到了吗（驱动侧加 dev_dbg，或看行为）
```

**`/proc/iomem` 是最快的一条**：
它列出所有已 `request_mem_region()` 的区间 ⇒
**如果你的设备在里面，说明地址翻译成功了**。

### §10.4 ⚠️ 五个静默失效

| 现象 | 原因 | 判据 |
|---|---|---|
| 节点在树里但没设备 | ① `status` 拼错；② 父节点不在白名单 | §10.2 步 ② 和 ③ |
| 寄存器读出来全是 0 或 0xffffffff | **`#address-cells` 写错** ⇒ ioremap 了错地址 | 对比 `/proc/iomem` 和 DTS |
| 可选属性配了但驱动没用 | 属性名拼错 ⇒ 驱动读到 `-EINVAL` 走了默认 | `ls /sys/firmware/devicetree/base/.../` 看属性在不在 |
| phandle 引用了错误的对象 | 参数个数和 `#xxx-cells` 不一致（§5.3） | `dtc -I dtb` 看 phandle 值，再对照目标节点的 `phandle` 属性 |
| 布尔属性"关"不掉 | 写成了 `flag = <0>;` ⇒ 仍然是 true（§1.2） | **删掉那一行才是"关"** |

**前四条的统一判据都是 `/sys/firmware/devicetree/base/`**（04 §4）——
它反映的是**内核实际解析出来的树**。

---

## §11 错误消息 → 原因映射表

| dmesg 里的话 | 出自 | 最可能的 DTS 原因 |
|---|---|---|
| `no ranges; cannot translate`（pr_debug） | `drivers/of/address.c:464` | **父节点缺 `ranges`** |
| `not found !`（pr_debug） | `drivers/of/address.c:486` | **地址不在任何 `ranges` 窗口里** |
| `empty ranges; 1:1 translation`（pr_debug） | `drivers/of/address.c:471` | 正常（空 `ranges`） |
| `Bad cell count for %pOF`（**pr_err**） | `drivers/of/address.c:581` | **`#address-cells` / `#size-cells` 不合法** |
| `amba: of_address_to_resource() failed (%d) for %pOF` | `drivers/of/platform.c:260` | amba 设备的 `reg` 解析失败 |
| `amba_device_add() failed (%d) for %pOF` | `drivers/of/platform.c:267` | 地址区间和别人冲突 |
| `ERROR: memory leak, expected refcount 1 instead of %d …` | `drivers/of/dynamic.c:471` | **不是 DTS 问题** —— 驱动忘了 `of_node_put()`（01 §1.2） |
| `ERROR: Bad of_node_put() on %pOF` | `drivers/of/dynamic.c:336` | 同上，put 多了 |
| **什么都没打，设备不存在** | —— | §10.4 那五条 |
| **什么都没打，寄存器读出全 0** | —— | `#address-cells` 写错（§10.4 第 2 条） |

表里**只有一条是 `pr_err`**（`drivers/of/address.c:581`）——
其余要么是 `pr_debug`（要开动态调试），要么根本没有日志。
⇒ **这就是 03 §C4 说的"可观测性极不均衡"的实际后果。**

---

## §12 本篇覆盖情况

| 内容 | 状态 |
|---|---|
| DTS 四种成分 + 四种值类型 | 完整 |
| `#address-cells` / `#size-cells` | 完整 + **三层嵌套的真实实证**（§9.2） |
| `reg` / `ranges` | 完整 + 三种形态各有 `pr_debug` 判据 |
| `status` | 完整（含"拼错就是 disabled"） |
| phandle / `#xxx-cells` / `xxx-names` | 完整 |
| `compatible` | 完整 |
| 填充白名单 | 完整（四项都给了行号） |
| 绑定文档 | 讲了作用和命令；⚠️ **`dtbs_check` 未实测**（99 §5.1） |
| arm64 完整实例 | 三段全部原样摘自本树（给了行号） |
| ⚠️ **DTS 语法的边角**（`/delete-node/`、`/omit-if-no-ref/`、`/bits/`、覆盖语法） | **不覆盖**（99 §2.7） |
| ⚠️ **overlay 的 DTS 写法**（`__overlay__` / `target-path`） | **不覆盖** —— arm64 上极少用（04 §1） |
| ⚠️ **各子系统的具体属性**（`clocks` 的语义、`pinctrl-0` 的格式等） | **不覆盖** —— 那在各子系统自己的 06 篇里 |
| ⚠️ ACPI | 本套 arm64-only；而且**设备树和 ACPI 是互斥的两条路** |

---

**下一篇**：07 篇讲编译期开关、`/sys/firmware/devicetree/`、`dtc` 工具、
**以及"热路径只有 pr_debug"时的调试办法**。
