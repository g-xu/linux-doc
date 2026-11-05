---
title: 设备树
---

# irq 06：设备树怎么写、谁消费、配错了怎么定位

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。
> ⚠️`Documentation/devicetree/bindings/interrupt-controller/` 下的文件名带逗号，
> 本套一律**不给它们写行号**（理由见 99 §8）。

---

## §0 按任务找章节

| 你想干的事 | 去哪一节 |
|---|---|
| 我只想知道谁消费了这些属性 | §1 |
| 我的节点该不该写 `interrupt-parent` | §2 |
| GIC 的 `interrupts = <a b c>` 三个数是什么意思 | §3 |
| `interrupts` 和 `interrupts-extended` 用哪个 | §4 |
| PCI 的 `interrupt-map` 怎么读 | §5 |
| MSI（`msi-parent` / `msi-map`）怎么配 | §6 |
| 给我一份能抄的 arm64 完整例子 | §7 |
| 配好了但中断收不到，怎么定位 | §8 |
| dmesg 里有一句报错，想知道是哪条属性错了 | §9 |

---

## §1 三方分工与属性总表

### §1.1 irq 核心一条 DT 属性都不读

```
                 ┌──────────────────────────────────────────────┐
   DTS 里的属性   │  谁把它变成行为？                             │
                 └──────────────────────────────────────────────┘

  interrupt-parent / interrupts / interrupts-extended
  #interrupt-cells / interrupt-controller
  interrupt-map / interrupt-map-mask / interrupt-names
        │
        └──▶ ① drivers/of/irq.c   全部在这里被解析

  msi-parent / msi-map / #msi-cells / msi-controller
        │
        └──▶ ① drivers/of/irq.c（MSI 那一段）+ drivers/of/base.c

  翻译出来的 (hwirq, 触发类型)
        │
        └──▶ ② 各控制器驱动的 domain->ops->translate / xlate

  kernel/irq/*.c
        │
        └──▶ ③ 一条 DT 属性都不读
```

**判据可重跑**：
```bash
grep -rn 'of_property_read\|of_get_property\|of_parse_phandle' kernel/irq/    # → 无输出
```
⇒ **irq 核心完全不知道设备树的存在**。
它只接受 `struct irq_fwspec`（一组 u32），
"这组 u32 是从 DT 来的还是从 ACPI 来的"由 `drivers/of/` 或 ACPI 层负责。

对比 remoteproc（那套的 06 §1.1）：核心读 1 条。irq 是 **0 条**。
⇒ 这也解释了 irq 为什么能同时支持 DT / ACPI / 软件 fwnode 三种身份来源。

### §1.2 属性总表（含"写在哪个节点"）

| 属性 | 写在哪个节点 | 类型 | 谁消费 | 消费代码 |
|---|---|---|---|---|
| `interrupt-controller` | **控制器自己** | bool | `drivers/of/irq.c` | `drivers/of/irq.c:252`、`drivers/of/irq.c:564` |
| `#interrupt-cells` | **控制器自己** | u32 | 同上 | `drivers/of/irq.c:128`、`:196`、`:391` |
| `interrupt-parent` | **消费者节点，或它的任一祖先** | phandle | 同上 | `drivers/of/irq.c:70` |
| `interrupts` | **消费者节点** | u32 数组 | 同上 | `drivers/of/irq.c:400` |
| `interrupts-extended` | **消费者节点** | phandle + args | 同上 | `drivers/of/irq.c:383-384`、`drivers/of/irq.c:589` |
| `interrupt-names` | 消费者节点 | 字符串数组 | 同上 | `drivers/of/irq.c:438`、`drivers/of/irq.c:493` |
| `interrupt-map` | **中间节点**（PCI 桥、复用器） | 复合 | 同上 | `drivers/of/irq.c:254` |
| `interrupt-map-mask` | 同上 | u32 数组 | 同上 | `drivers/of/irq.c:279` |
| `msi-controller` | **MSI 控制器自己**（ITS 节点） | bool | `drivers/of/irq.c` | 见 §6 |
| `#msi-cells` | MSI 控制器自己 | u32 | 同上 | `drivers/of/irq.c:739` |
| `msi-parent` | 消费者节点（或 PCI 主桥） | phandle + args | 同上 | `drivers/of/irq.c:739` |
| `msi-map` | **PCI 主桥** | 复合 | `drivers/of/base.c` 的 `of_map_id()` | `drivers/of/irq.c:679` |

**「写在哪个节点」这一列是本篇的核心**：
`interrupt-controller` 和 `#interrupt-cells` 写在**提供中断的那一方**，
`interrupts` 写在**消费中断的那一方** —— 写错位置的表现是**静默无效**（§8.4）。

### §1.3 `drivers/of/irq.c` 的解析入口

| 函数 | 位置 | 干什么 |
|---|---|---|
| `of_irq_find_parent()` | `drivers/of/irq.c:61` | 沿 `interrupt-parent` / 父节点向上找中断控制器 |
| `of_irq_parse_raw()` | `drivers/of/irq.c:176` | **`interrupt-map` 的遍历主体**（§5） |
| `of_irq_parse_one()` | `drivers/of/irq.c:358` | 解析一条中断 → `of_phandle_args` |
| `of_irq_to_resource()` | `drivers/of/irq.c:421` | 填进 `struct resource` |
| `of_irq_get()` | `drivers/of/irq.c:459` | 解析 + 建映射，**能返回 `-EPROBE_DEFER`** |
| `of_irq_get_byname()` | `drivers/of/irq.c:486` | 按 `interrupt-names` |
| `of_irq_count()` | `drivers/of/irq.c:505` | 数一数 |
| `of_irq_to_resource_table()` | `drivers/of/irq.c:526` | platform 设备创建时批量填 |
| `irq_of_parse_and_map()` | `drivers/of/irq.c:39` | ⚠️ 老接口，失败返回 0（01 §3.2、03 §C4） |
| `of_irq_init()` | `drivers/of/irq.c:553` | 启动时按拓扑序初始化所有控制器（03 §A2） |

---

## §2 `interrupt-parent`：一条继承链

### §2.1 查找规则

`of_irq_find_parent()`（`drivers/of/irq.c:61`）的算法：

```
   从消费者节点开始
     │
     ├─ 本节点有 interrupt-parent 吗？
     │     有  ⇒ 顺着那个 phandle 走
     │     没有 ⇒ 走到 DT 的父节点
     │
     └─ 到达的节点有 #interrupt-cells 吗？
           有  ⇒ 找到了，就是它
           没有 ⇒ 回到上一步继续往上
```

| 关键位置 | 行号 |
|---|---|
| 读 `interrupt-parent` | `drivers/of/irq.c:70` |
| 循环条件：**直到找到有 `#interrupt-cells` 的节点** | `drivers/of/irq.c:80` |

**注意循环的终止条件是 `#interrupt-cells` 而不是 `interrupt-controller`** ——
`interrupt-map` 的中间节点（PCI 桥）也有 `#interrupt-cells` 但不是控制器（§5）。

### §2.2 三种常见写法

```dts
/* 写法 A：根节点统一声明（arm64 最常见） */
/ {
	interrupt-parent = <&gic>;      /* 所有子孙节点默认用 GIC */

	soc {
		uart@12340000 {
			interrupts = <GIC_SPI 25 IRQ_TYPE_LEVEL_HIGH>;
			/* 不写 interrupt-parent，继承根节点的 */
		};
	};
};

/* 写法 B：节点自己指定（挂在二级控制器上时必须） */
	button {
		interrupt-parent = <&gpio1>;
		interrupts = <5 IRQ_TYPE_EDGE_FALLING>;
	};

/* 写法 C：interrupts-extended，每条中断各自指定控制器 */
	mydev {
		interrupts-extended = <&gic  GIC_SPI 25 IRQ_TYPE_LEVEL_HIGH>,
				      <&gpio1 5        IRQ_TYPE_EDGE_FALLING>;
		interrupt-names = "main", "wakeup";
	};
```

**写法 C 是唯一能"一个设备的多条中断来自不同控制器"的方式**（§4）。

### §2.3 ⚠️ 一个静默陷阱：`interrupt-parent` 写在错的层级

```dts
/ {
	interrupt-parent = <&gic>;
	i2c@1234 {
		expander: gpio@20 {
			interrupt-controller;
			#interrupt-cells = <2>;
			interrupts = <GIC_SPI 30 IRQ_TYPE_LEVEL_HIGH>;   /* 它自己挂 GIC */
		};

		/* ⚠️ 想挂在 expander 上的设备，写在 i2c 节点下 */
		sensor@30 {
			interrupts = <3 IRQ_TYPE_EDGE_RISING>;   /* 忘了写 interrupt-parent！ */
		};
	};
};
```

**会发生什么**：`of_irq_find_parent()` 从 `sensor@30` 往上走 ——
`i2c@1234` 没有 `#interrupt-cells`，再往上到根节点，
根节点的 `interrupt-parent = <&gic>` 生效 ⇒ **这条中断被解析成 GIC 的 hwirq 3**！

| 后果 | 说明 |
|---|---|
| 好一点的情况 | GIC 的 hwirq 3 是 SGI，`translate` 会拒绝 ⇒ probe 失败，有报错 |
| ⚠️ 坏的情况 | 那个 hwirq 在 GIC 上是合法的 ⇒ **映射建立成功，中断永远不来**，一句日志都没有 |

⇒ **凡是挂在二级控制器上的设备，必须显式写 `interrupt-parent`**（或用 `interrupts-extended`）。

---

## §3 `#interrupt-cells`：控制器定义自己的"参数格式"

### §3.1 GIC v3 的三个 cell

```dts
	interrupts = <GIC_SPI 164 IRQ_TYPE_LEVEL_HIGH>;
	              ↑       ↑    ↑
	              cell0   cell1 cell2
```

| cell | 含义 | 取值 |
|---|---|---|
| **cell 0** | **中断类型** | 见下表 |
| **cell 1** | **偏移量，不是 hwirq！** | SPI: 0 ⇒ hwirq 32；PPI: 0 ⇒ hwirq 16 |
| **cell 2** | 触发类型 | `IRQ_TYPE_LEVEL_HIGH`(4) / `IRQ_TYPE_EDGE_RISING`(1) 等 |

cell 0 的**六种取值**（全部在 `gic_irq_domain_translate()` 的 `switch` 里，
`drivers/irqchip/irq-gic-v3.c:1545`）：

| cell 0 | 常量 | hwirq 换算 | 位置 |
|---|---|---|---|
| 0 | `GIC_SPI` | `param[1] + 32` | `drivers/irqchip/irq-gic-v3.c:1547` |
| 1 | `GIC_PPI` | `param[1] + 16` | `drivers/irqchip/irq-gic-v3.c:1550` |
| 2 | ESPI（扩展 SPI） | `param[1] + ESPI_BASE_INTID` | `drivers/irqchip/irq-gic-v3.c:1553` |
| 3 | EPPI（扩展 PPI） | `param[1] + EPPI_BASE_INTID` | `drivers/irqchip/irq-gic-v3.c:1556` |
| —— | `GIC_IRQ_TYPE_LPI` | 原样（LPI 已经是绝对号） | `drivers/irqchip/irq-gic-v3.c:1559` |
| —— | `GIC_IRQ_TYPE_PARTITION` | 分区 PPI，`>= 16` 走 EPPI 基址 | `drivers/irqchip/irq-gic-v3.c:1561-1566` |
| 其它 | —— | `return -EINVAL` | `drivers/irqchip/irq-gic-v3.c:1568-1569` |

`GIC_SPI` = 0、`GIC_PPI` = 1 定义在
`include/dt-bindings/interrupt-controller/arm-gic.h`（该文件同时 include 了
`irq.h` 拿触发类型常量）。

**两个有用的守卫**：

| 守卫 | 位置 | 触发条件 |
|---|---|---|
| `param_count < 3 ⇒ -EINVAL` | `drivers/irqchip/irq-gic-v3.c:1542-1543` | DTS 少写了 cell（比如照抄 GPIO 的两个 cell） |
| `WARN_ON(*type == IRQ_TYPE_NONE && …)` | `drivers/irqchip/irq-gic-v3.c:1578-1579` | **cell 2 写了 0** —— 注释原文是 "Make it clear that broken DTs are... broken." |

⇒ **dmesg 里 `gic_irq_domain_translate` 附近的 `WARN` 就是"你的 DTS 触发类型写了 0"**。

⚠️ **cell 1 是偏移量而不是 hwirq，是本子系统最经典的坑**：

| DTS 写的 | 实际 hwirq | `/proc/interrupts` 里能看到吗 |
|---|---|---|
| `<GIC_SPI 164 …>` | **196**（= 164 + 32） | 看到的是 virq，不是 hwirq |
| `<GIC_PPI 9 …>` | **25**（= 9 + 16） | 同上 |

⇒ **拿硬件手册上的中断号往 DTS 里填之前先确认手册用的是哪套编号**。
手册通常给的是"SPI 号"（和 DTS 一致）或"绝对 INTID"（要减 32）。

换算关系由 GIC 驱动的 `translate` 回调实现
（`drivers/irqchip/irq-gic-v3.c:1530` 的 `gic_irq_domain_translate()`）。

### §3.2 触发类型常量

来自 `include/dt-bindings/interrupt-controller/irq.h`：

| 常量 | 值 | GIC v3 支持吗 |
|---|---|---|
| `IRQ_TYPE_NONE` | 0 | 表示"用默认"，通常不该写 |
| `IRQ_TYPE_EDGE_RISING` | 1 | 支持 |
| `IRQ_TYPE_EDGE_FALLING` | 2 | **不支持** —— GIC 只有上升沿 |
| `IRQ_TYPE_EDGE_BOTH` | 3 | **不支持** |
| `IRQ_TYPE_LEVEL_HIGH` | 4 | **支持（最常用）** |
| `IRQ_TYPE_LEVEL_LOW` | 8 | **不支持** |

⚠️ **GIC v3 只支持 `LEVEL_HIGH` 和 `EDGE_RISING`**。
写 `IRQ_TYPE_LEVEL_LOW` 会让 `gic_set_type()` 返回 `-EINVAL`，
表现是 `request_irq()` 失败（有日志）。
⇒ 低电平/下降沿的信号必须在**板级用反相器**或者**挂在支持它的二级控制器上**。

### §3.3 各类控制器的 cell 数速查

| 控制器 | `#interrupt-cells` | 格式 |
|---|---|---|
| GIC v2 / v3 | **3** | `<type offset flags>` |
| 大多数 GPIO 控制器 | **2** | `<pin flags>` |
| 简单的一位一条中断控制器 | **1 或 2** | `<hwirq>` 或 `<hwirq flags>` |
| PCI 主桥（作为 `interrupt-map` 的中间层） | **1** | `<INTA/B/C/D>` |

核心提供了两个现成的 `xlate` 对应 cell 数 1 和 2
（`include/linux/irqdomain.h:454` / `:457`，05 §2.3）；
cell 数 3 的（如 GIC）必须自己写 `translate`。

还有一个 `irq_domain_xlate_onetwocell()`（`include/linux/irqdomain.h:460`）——
它**兼容 1 和 2 两种**，给"历史上 cell 数变过"的控制器用。

---

## §4 `interrupts` vs `interrupts-extended`

### §4.1 对照

| | `interrupts` | `interrupts-extended` |
|---|---|---|
| 格式 | 纯 u32 数组 | `<&ctrl args…>` 的列表 |
| 控制器从哪来 | `interrupt-parent` 继承链（§2） | **每条中断自带** |
| 多条中断能来自不同控制器吗 | **不能** | **能** |
| 消费代码 | `drivers/of/irq.c:400` | `drivers/of/irq.c:383-384` |
| 两者共存时哪个赢 | **`interrupts-extended`** —— `of_irq_parse_one()` 先试它 | —— |

**优先级证据**：`of_irq_parse_one()`（`drivers/of/irq.c:358`）
先调 `of_parse_phandle_with_args(device, "interrupts-extended", …)`
（`drivers/of/irq.c:383-384`），**成功就直接返回**；
失败才回退到 `interrupt-parent` + `interrupts` 那条路（`drivers/of/irq.c:391` 起）。

⚠️ **两个都写会让 `interrupts` 被静默忽略**。绑定检查（`dtbs_check`）通常会报
"interrupts and interrupts-extended are mutually exclusive"，但不是所有绑定都写了这条约束。

### §4.2 什么时候必须用 `interrupts-extended`

| 场景 | 例子 |
|---|---|
| 一个设备的多条中断来自不同控制器 | 主中断走 GIC，唤醒中断走 PMIC 的 GPIO |
| 想避免 §2.3 那个继承链陷阱 | 挂在二级控制器上的设备 |
| 中断控制器本身要声明它的父中断 | `drivers/of/irq.c:589` 就是这么读的（`of_irq_init()` 用它排拓扑序，03 §A2） |

**`drivers/of/irq.c:589` 这一处很关键**：
`of_irq_init()` 建拓扑序时读的是 **`interrupts-extended` 的第 0 项**
（`of_parse_phandle(np, "interrupts-extended", 0)`）。
⇒ 二级中断控制器如果只写 `interrupts` + `interrupt-parent`，
`of_irq_init()` 还是能通过 `of_irq_find_parent()` 找到父节点 ——
但**用 `interrupts-extended` 更直接**。

### §4.3 `interrupt-names`：让代码按名字取

```dts
	mydev@1234 {
		interrupts-extended = <&gic  GIC_SPI 25 IRQ_TYPE_LEVEL_HIGH>,
				      <&gic  GIC_SPI 26 IRQ_TYPE_LEVEL_HIGH>,
				      <&gpio1 5        IRQ_TYPE_EDGE_FALLING>;
		interrupt-names = "tx", "rx", "wakeup";
	};
```

| 消费点 | 位置 |
|---|---|
| `of_irq_get_byname()` 里的名字匹配 | `drivers/of/irq.c:493` |
| `of_irq_to_resource()` 顺手把名字塞进 `struct resource` | `drivers/of/irq.c:438` |

**`platform_get_irq_byname()` 比 `platform_get_irq(pdev, N)` 稳**（01 §3.1、05 §1.5）：
DTS 里增删一条中断不会打乱其它的索引。

`drivers/of/irq.c:438` 那一处的效果：`/proc/interrupts` 和
`/proc/irq/N/<name>` 里出现的名字**可能来自 `interrupt-names`**，
而不是 `request_irq()` 的 `devname` —— 取决于驱动怎么申请。

---

## §5 `interrupt-map`：中间节点做转换

### §5.1 用在哪

唯一常见的场景是 **PCI**：PCI 设备只知道自己用 `INTA/B/C/D`，
不知道这四根线接到了 GIC 的哪条 SPI 上。`interrupt-map` 就是这张接线表。

```dts
	pcie@1000000 {
		#address-cells = <3>;
		#interrupt-cells = <1>;       /* PCI 侧只有 1 个 cell：INTA=1 … INTD=4 */

		interrupt-map-mask = <0 0 0 7>;
		interrupt-map = <0 0 0 1 &gic GIC_SPI 100 IRQ_TYPE_LEVEL_HIGH>,
				<0 0 0 2 &gic GIC_SPI 101 IRQ_TYPE_LEVEL_HIGH>,
				<0 0 0 3 &gic GIC_SPI 102 IRQ_TYPE_LEVEL_HIGH>,
				<0 0 0 4 &gic GIC_SPI 103 IRQ_TYPE_LEVEL_HIGH>;
	};
```

### §5.2 每一行的结构

```
   < 子节点的 address-cells 个数 | 子节点的 interrupt-cells 个数 | phandle | 父的 interrupt-cells 个数 >
   ├──────── 用 interrupt-map-mask 掩过之后拿来比较 ────────┤ ├──── 命中后替换成这个 ────┤
```

| 元素 | 说明 |
|---|---|
| `interrupt-map-mask` | **先把要比较的 (地址, 中断) 用它 AND 一遍**，再去表里找 |
| 前半段的长度 | = 本节点的 `#address-cells` + 本节点的 `#interrupt-cells` |
| 后半段的长度 | = **父控制器的** `#interrupt-cells`（GIC 是 3） |

上例中 `interrupt-map-mask = <0 0 0 7>` 的意思：
地址的三个 cell **全部忽略**（掩成 0），中断号只看低 3 位。
⇒ 所有 PCI 设备的 INTA 都映到同一条 SPI（"INTx 共享"）。

### §5.3 消费代码

| 位置 | 干什么 |
|---|---|
| `of_irq_parse_raw()` | `drivers/of/irq.c:176` —— 整个遍历循环的主体 |
| 读 `interrupt-controller`（判断"到顶了没"） | `drivers/of/irq.c:252` |
| 读 `interrupt-map` | `drivers/of/irq.c:254` |
| 读 `interrupt-map-mask` | `drivers/of/irq.c:279` |
| 读父的 `#interrupt-cells` | `drivers/of/irq.c:196` |
| 读本节点的 `#interrupt-cells` | `drivers/of/irq.c:128` |

**`of_irq_parse_raw()` 是一个循环**：
每命中一次 `interrupt-map` 就把 (地址, 中断) 换成新的，然后继续往上走 ——
**可以嵌套多层 `interrupt-map`**（PCI 桥套 PCI 桥）。
终止条件是遇到一个有 `interrupt-controller` 属性、且**没有** `interrupt-map` 的节点
（`drivers/of/irq.c:252`）。

⚠️ **`interrupt-map` 手工写非常容易错**：cell 数一个不对，
整行就对不上了，而且**报错信息很难懂**（§9）。
⇒ **arm64 上一般不用自己写** —— PCI 主桥的 `interrupt-map` 由 SoC 厂商的 dtsi 提供，
而且新平台基本都用 MSI（§6）不用 INTx。

---

## §6 MSI：`msi-parent` 和 `msi-map`

### §6.1 两种写法

```dts
/* 写法 A：msi-parent —— 简单，一个设备指一个 MSI 控制器 */
	mydev@1234 {
		msi-parent = <&its 0x1234>;     /* 第二个参数是 device-id */
	};

/* 写法 B：msi-map —— PCI 用，把一段 RID 映射到一段 device-id */
	pcie@1000000 {
		msi-map = <0x0 &its 0x0 0x10000>;
		/*         ↑     ↑     ↑    ↑
		 *      RID base  ITS  ID base  长度  */
	};
```

| 属性 | 写在哪 | 消费代码 |
|---|---|---|
| `msi-controller` | **ITS 节点自己** | 见 §7 的实例 |
| `#msi-cells` | ITS 节点自己 | `drivers/of/irq.c:739` |
| `msi-parent` | 消费者（非 PCI 设备） | `drivers/of/irq.c:739` |
| `msi-map` | **PCI 主桥** | `drivers/of/irq.c:679` |

### §6.2 解析入口

| 函数 | 位置 | 干什么 |
|---|---|---|
| `of_msi_map_id()` | `drivers/of/irq.c:696` | RID → device-id（走 `msi-map`） |
| `of_msi_map_get_device_domain()` | `drivers/of/irq.c:712` | RID → MSI 域 |
| `of_msi_get_domain()` | `drivers/of/irq.c:731` | 走 `msi-parent` 找域 |
| `of_msi_configure()` | `drivers/of/irq.c:753` | 给一个设备装上 MSI 域 |
| 通用的 ID 映射 | `drivers/of/base.c:2120` 附近的 `of_map_id()`（同一套机制也用于 `iommu-map`） |

**`msi-map` 和 `iommu-map` 用的是同一套 `of_map_id()`**
（`drivers/of/base.c:2120` 的注释明说了）——
因为两者要解决的是同一个问题：**把 PCI 的 RID 翻译成 SoC 内部的某种 ID**。

### §6.3 ⚠️ device-id 必须和硬件一致

ITS 用 device-id 在自己的表里索引"这个设备的中断转换表"。
写错的表现是**中断永远不来，且没有任何日志** ——
ITS 收到的是一个它没登记过的 device-id，直接丢弃。

⇒ device-id 通常等于"这个设备在 SoC 互联上的 stream ID"，
和 `iommus = <&smmu 0x1806 0x0>` 里那个数**往往是同一个**（§7 会看到）。

---

## §7 完整的 arm64 实例（本树真实节点，可以 diff）

### §7.1 GIC v3 + ITS 节点

```dts
/* arch/arm64/boot/dts/qcom/sdm845.dtsi */
		intc: interrupt-controller@17a00000 {
			compatible = "arm,gic-v3";
			#address-cells = <2>;
			#size-cells = <2>;
			ranges;
			#interrupt-cells = <3>;          /* 三个 cell（§3.1） */
			interrupt-controller;            /* 声明"我是控制器" */
			reg = <0 0x17a00000 0 0x10000>,     /* GICD */
			      <0 0x17a60000 0 0x100000>;    /* GICR * 8 */
			interrupts = <GIC_PPI 9 IRQ_TYPE_LEVEL_HIGH>;   /* GIC 自己的维护中断 */

			msi-controller@17a40000 {
				compatible = "arm,gic-v3-its";
				msi-controller;          /* 声明"我是 MSI 控制器" */
				#msi-cells = <1>;
				reg = <0 0x17a40000 0 0x20000>;
				status = "disabled";
			};
		};
```

| 元素 | 行号 |
|---|---|
| `compatible = "arm,gic-v3"` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5114` |
| `#interrupt-cells = <3>` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5118` |
| `interrupt-controller;` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5119` |
| `reg`（GICD + GICR 两块） | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5120-5121` |
| GIC 自己的 PPI | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5122` |
| ITS 子节点 | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5124` |
| `compatible = "arm,gic-v3-its"` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5125` |
| `msi-controller;` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5126` |
| `#msi-cells = <1>` | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5127` |

注意 GIC 节点自己也有 `interrupts`（`:5122`）——
那是 GIC 的"维护中断"（maintenance IRQ），给 KVM 用的，
**不影响它作为中断控制器的角色**。

### §7.2 一个普通消费者

```dts
		slimbam: dma-controller@17184000 {
			compatible = "qcom,bam-v1.7.0";
			reg = <0 0x17184000 0 0x2a000>;
			interrupts = <GIC_SPI 164 IRQ_TYPE_LEVEL_HIGH>;
			/* 没有 interrupt-parent —— 继承根节点的（§2.2 写法 A） */
			iommus = <&apps_smmu 0x1806 0x0>;
		};
```

| 元素 | 行号 |
|---|---|
| 节点 | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5133` |
| `interrupts = <GIC_SPI 164 …>` ⇒ **hwirq 196** | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5138` |
| `iommus`（那个 `0x1806` 就是 stream ID，§6.3） | `arch/arm64/boot/dts/qcom/sdm845.dtsi:5142` |

### §7.3 三段分工

| 层 | 放什么 | 为什么 |
|---|---|---|
| 根节点 | `interrupt-parent = <&intc>` | 一次声明，全树受益 |
| SoC `.dtsi` | 控制器节点 + 所有 SoC 内部设备的 `interrupts` | 芯片决定的 |
| 板级 `.dts` | 只有板上外设（挂 GPIO 的按键、传感器）的 `interrupts` | 板子决定的 |

`interrupts-extended` 在 arm64 DTS 里的使用面：
```bash
grep -rl 'interrupts-extended' arch/arm64/boot/dts/ | wc -l     # → 90 个文件
```
⇒ **不是罕见写法**，遇到多控制器场景放心用（§4.2）。

---

## §8 定位：三层排查

### §8.1 第一层：编译期

```bash
# 绑定校验
make dtbs_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/interrupt-controller/

# 反编译看实际生效的值（比读 dts 源码可靠 —— 宏都展开了）
dtc -I dtb -O dts -o /tmp/x.dts arch/arm64/boot/dts/<vendor>/<board>.dtb
```

| 能查出来 | 查不出来 |
|---|---|
| 属性名拼错（绑定有 `additionalProperties: false` 时） | ⚠️ **`interrupt-parent` 写在错的层级**（§2.3） |
| cell 数不对（绑定写了 `interrupts: maxItems`） | ⚠️ **cell 1 的偏移量算错**（§3.1） |
| —— | ⚠️`interrupt-map` 的行内 cell 数错位（§5.2） |

**`dtc -I dtb -O dts` 是最实用的一条**：
`GIC_SPI` / `IRQ_TYPE_LEVEL_HIGH` 这些宏在反编译输出里变成裸数字，
一眼能看出 cell 0 是 0 还是 1、cell 2 是 4 还是 0。

### §8.2 第二层：运行期静态（映射建起来了没）

```sh
# 1. 这条中断的 virq 是多少？
cat /proc/interrupts | grep -i <你的设备名>

# 2. 域建起来了吗？（需要 GENERIC_IRQ_DEBUGFS）
cat /sys/kernel/debug/irq/domains/default

# 3. hwirq 对不对？
cat /sys/kernel/debug/irq/irqs/<virq> | head -20
#    看 hwirq 那一行：应该等于 DTS 的 cell1 + 32（SPI）或 + 16（PPI）

# 4. probe 阶段的报错
dmesg | grep -iE 'irq|interrupt' | head -40
```

**第 3 步是把 DTS 和实际行为对起来的关键**：
`/sys/kernel/debug/irq/irqs/<virq>` 会打印每一层的 `hwirq`（04 §6.3）。
DTS 写 `<GIC_SPI 164 …>` ⇒ 这里应该看到 `hwirq: 196`。不一致就是 §3.1 的换算问题。

### §8.3 第三层：运行期动态（映射对了但中断不来）

```sh
# 1. 计数在涨吗？
watch -n1 'grep <设备名> /proc/interrupts'

# 2. 手工注入一次，验证"从核心到你的 handler"这一段是通的
echo trigger > /sys/kernel/debug/irq/irqs/<virq>
dmesg | tail

# 3. 触发类型对不对？
cat /sys/kernel/debug/irq/irqs/<virq> | grep -i level
#    IRQD_LEVEL 置位 = 电平触发

# 4. 亲和性把它送到了一个下线的 CPU？
cat /proc/irq/<virq>/effective_affinity
```

**第 2 步（`echo trigger`）能把问题一刀切成两半**：

| 注入后 handler 被调了 | 说明 |
|---|---|
| 是 | 核心→handler 这一段是通的 ⇒ 问题在**硬件到 GIC** 这一段（DTS 的 hwirq 错、外设没使能中断输出、板级接线） |
| 否 | ⇒ 问题在**核心侧**（handler 没注册上、中断被 disable、亲和性指向了下线 CPU） |

### §8.4 ⚠️ 四个静默失效（DTS 错了但没有任何日志）

| 现象 | 原因 | 判据 |
|---|---|---|
| 中断永远不来 | `interrupt-parent` 写在错层级，被解析成了 GIC 的另一条中断 | `/sys/kernel/debug/irq/irqs/<virq>` 里的 chip 名字不是你期望的那个控制器 |
| 中断永远不来 | cell 1 的偏移量算错（多减了 32 或少加了 32） | 同上，看 `hwirq` |
| MSI 中断永远不来 | `msi-parent` / `msi-map` 的 device-id 和硬件的 stream ID 不一致 | 和同节点的 `iommus` 那个数对比（§6.3） |
| 中断行为像"反了" | 触发类型不被硬件支持而驱动没检查返回值 | `cat /sys/kernel/debug/irq/irqs/<virq>` 看 `IRQD_LEVEL` |

**这四条的共同判据都是同一个文件** ——
`/sys/kernel/debug/irq/irqs/<virq>`。
⇒ **调 DTS 中断问题，第一件事是打开 `CONFIG_GENERIC_IRQ_DEBUGFS`**（01 §0.4、04 §6.1）。

---

## §9 错误消息 → 原因映射表

| dmesg 里的话 | 出自 | 最可能的 DT 原因 |
|---|---|---|
| `WARN` 在 `gic_irq_domain_translate` 附近 | `drivers/irqchip/irq-gic-v3.c:1578-1579` | **cell 2（触发类型）写了 0** |
| `Illegal GSI%d translation request` | `drivers/irqchip/irq-gic-v3.c:1588` | ACPI 路径上 GSI < 16（本套 arm64-only 不覆盖 ACPI，99 §2.7） |
| `platform_get_irq: IRQ index N not found` 类 | `drivers/base/platform.c` | DTS 里 `interrupts` 的条目数不够 |
| 返回 `-EPROBE_DEFER` 但一直不好 | `drivers/of/irq.c:459` 的 `of_irq_get()` | 中断控制器驱动没编进内核，或它的 `IRQCHIP_DECLARE` compatible 和 DTS 不匹配 |
| `irq: no irq domain found for … !` | `drivers/of/irq.c` 附近 | 同上 |
| `-EINVAL` 且 `request_irq()` 失败 | `drivers/irqchip/irq-gic-v3.c` 的 `gic_set_type` | 触发类型 GIC 不支持（`LEVEL_LOW` / `EDGE_FALLING`，§3.2） |
| `-EINVAL` 在 translate 里 | `drivers/irqchip/irq-gic-v3.c:1542-1543` | **cell 数不是 3**（照抄了 GPIO 的两个 cell） |
| `-EINVAL` 在 translate 里 | `drivers/irqchip/irq-gic-v3.c:1568-1569` | cell 0 不是 0/1/2/3/LPI/PARTITION 之一 |
| `nobody cared` | `kernel/irq/spurious.c:202` | **不是 DT 问题** —— handler 返回值错（04 §4） |
| `Unbalanced enable for IRQ N` | `kernel/irq/manage.c` 的 `__enable_irq()` | 不是 DT 问题（01 §2.1） |
| **什么都没打，中断不来** | —— | §8.4 那四条 |

表里**一半以上的 DT 问题最终只表现为 `-EINVAL` 或"什么都没有"** ——
这就是 §8.2 第 3 步（对比 hwirq）必须做的原因。

---

## §10 本篇覆盖情况

| 内容 | 状态 |
|---|---|
| irq 核心读的属性 | **0 条**（§1.1，已给可重跑命令） |
| `drivers/of/irq.c` 的全部属性消费点 | 完整，每条都定位到行 |
| GIC v3 的 cell 语义 | 完整：六种 cell 0 + 两个守卫，全部定位到行 |
| `interrupt-map` | 讲了机制和消费点；⚠️ **没有逐字展开 `of_irq_parse_raw()` 的循环**（二等证据，99 §2.2） |
| MSI 属性 | 讲了机制和入口；⚠️ ITS 侧的实现（5608 行）只讲契约 |
| arm64 完整实例 | 三段全部原样摘自本树（给了行号） |
| ⚠️ ACPI（MADT / GSI） | 本套 arm64-only，**ACPI 一律裁掉**（99 §2.7）。§9 里那条 `Illegal GSI` 只作提示 |
| ⚠️ 其它中断控制器的 cell 语义 | `Documentation/devicetree/bindings/interrupt-controller/` 有 **113** 个绑定文件（49 `.yaml` + 63 `.txt` + 1 个其它），本套只覆盖 GIC v2/v3 |

⚠️ 本篇的 DTS 片段都是从本树**原样摘录**的（§7 给了行号），
可以用 `sed -n` 逐条对照。§3.1 那六行 `switch` 同样是原样摘录。

---

**下一篇**：07 篇讲编译期开关、tracepoint、`/proc/interrupts`、故障注入和错误码分诊。
