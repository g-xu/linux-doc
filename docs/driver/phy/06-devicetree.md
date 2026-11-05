---
title: 设备树
---

# phy 06：设备树

## §0 发现路径分诊表

**和 regmap 不同，phy 是一个"DT 优先"的子系统**：
**`Documentation/devicetree/bindings/phy/` 有 159 个绑定文件 + 一个 72 行的通用绑定
（`phy-bindings.txt`）**，**arm64 DTS 里 `phys` 属性出现在 149 个文件、558 行。**

### §0.1 三条发现路径

| # | 路径 | 谁扫描 | 走不走 DT | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|---|
| 1 | **DT phandle**（`phys` + `#phy-cells`） | **`_of_phy_get()`**（`drivers/phy/phy-core.c:568`） | **走** | **绝对主力** | **完整**（§2 / §3） |
| 2 | **lookup 表**（非 DT） | **`phy_find()`**（`drivers/phy/phy-core.c:115`） | **不走** | **少数**（ULPI 等，04 §3） | **只讲触发条件**（§0.2）；代码在 05 §6 |
| 3 | **`phy-supply` 取 regulator** | **`phy_create()`**（`drivers/phy/phy-core.c:978`） | **走**（但走的是 regulator 子系统的 DT 解析） | **常用** | **完整 + 一个大陷阱**（§4） |

### §0.2 ⚠️ 路径 1 与路径 2 的切换点只有一行

```c
/* drivers/phy/phy-core.c:744-757 —— phy_get() 里 */
	if (dev->of_node) {
		…（路径 1：DT）
		phy = _of_phy_get(dev->of_node, index);
	} else {
		if (string == NULL) {
			dev_WARN(dev, "missing string\n");
			return ERR_PTR(-EINVAL);
		}
		phy = phy_find(dev, string);	/* 路径 2：lookup 表 */
	}
```

⇒ **判据是"消费者设备有没有 `of_node`"，不是"系统有没有开 DT"。**
⇒ **arm64 上几乎所有设备都有 `of_node`，所以基本只走路径 1。**

### §0.3 按任务找章节

| 我要… | 去 |
|---|---|
| 写一个 PHY 提供者节点 | §2 |
| 写一个消费者节点 | §3 |
| 给 PHY 加电源 | §4 |
| **DTS 写了但 `devm_phy_get` 返回 `-ENODEV`** | **§5（静默失效决策树）** |
| 找 arm64 真实例子 | §2.3 / §3.3 |
| 配错了怎么定位 | §6 |
| 看到某条报错 | §7 |
| 哪些属性/期待是死字 | §8 |

---

## §1 核心真正会读的属性总表

**全表四条，一条不多。** **"写在哪个节点"这一列是本表的重点。**

| 属性 | 写在哪个节点 | 必需？ | 谁消费（定位到行） | 落到哪 |
|---|---|---|---|---|
| `#phy-cells` | **PHY 节点**（或它的子节点，§2.2） | **必需** | `drivers/phy/phy-core.c:575-576`（`of_parse_phandle_with_args` 的第三参数） | **决定 `phys` 里每个条目带几个 cell** |
| `phys` | **消费者节点** | **必需**（否则拿不到 PHY） | `drivers/phy/phy-core.c:575` | **`args.np`（provider 节点）+ `args.args[]`（cell 值）** |
| `phy-names` | **消费者节点** | ⚠️ **代码上不必需**（§8） | `drivers/phy/phy-core.c:746-747` 和 `:627`（`of_property_match_string`） | **把名字换成 `phys` 里的下标** |
| ⚠️`phy-supply` | **PHY 节点**（就是有 `#phy-cells` 的那个） | 可选 | `drivers/phy/phy-core.c:978` 的 `regulator_get_optional(&phy->dev, "phy")` | **`phy->pwr`，由 `phy_power_on/off` 自动开关** |

⚠️ **三条必须先说清的事**：

1. **通用绑定文档只定义了这四个属性**
   （`Documentation/devicetree/bindings/phy/phy-bindings.txt`：PHY 节点侧 `#phy-cells`（Required）
   + `phy-supply`（Optional）；消费者侧 `phys` + `phy-names`（都列为 Required））。
   **其余一切（`clocks` / `resets` / `reg` / 厂商私有属性）都由各自的 PHY 驱动绑定定义，框架不看。**
2. ⚠️ **`phys` 与网络子系统的 `phy` / `phy-handle` 是三个不同的属性。**
   **绑定文档自己就在提醒这件事**（原文：*"not to be confused with the Ethernet specific 'phy' and
   'phy-handle' properties"*）⇒ **详见 §8 和 02 §1.0.A。**
3. ⚠️⚠️ **`phy-supply` 在树里有至少 8 个互不相干的消费者**，
   **通用 PHY 框架只是其中之一** ⇒ **§4.2 是本篇最重要的一节。**

---

## §2 provider 侧：`#phy-cells` 与两种布局

### §2.1 `#phy-cells` 决定引用的写法

| `#phy-cells` | 消费者写法 | provider 需要什么 | arm64 用量 |
|---|---|---|---|
| `<0>` | **`phys = <&myphy>`** | **可以直接用 `of_phy_simple_xlate`** | **330 处（最多）** |
| `<1>` | **`phys = <&myphy 2>`** | **必须自写 `of_xlate`**（除非用子节点布局，§2.2） | **92 处** |
| `<2>` | `phys = <&myphy 1 3>` | 自写 `of_xlate` | **2 处**（`arch/arm64/boot/dts/ti/k3-am65-main.dtsi:61` 和 `:76`） |
| `<4>` | `phys = <&myphy a b c d>` | 自写 `of_xlate` | **1 处**（`arch/arm64/boot/dts/xilinx/zynqmp.dtsi:657`） |

重跑：`grep -rhoE '#phy-cells = <[0-9]+>' arch/arm64/boot/dts/ | sort | uniq -c | sort -rn`

⚠️ **cell 的含义完全由 provider 的绑定定义**，**框架只是把它们原样放进
`args->args[]` 交给 `of_xlate`**（`drivers/phy/phy-core.c:599`）。

### §2.2 两种 DT 布局（本节最要紧的一条）

#### 布局 A：一个节点一个 PHY（`#phy-cells = <0>`）

```dts
usbphy: phy@ff200000 {
	compatible = "foo,usb-phy";
	reg = <0x0 0xff200000 0x0 0x100>;
	#phy-cells = <0>;
};
```
**provider 用 `of_phy_simple_xlate`** ——**它按 `args->np == phy->dev.of_node` 匹配
（`drivers/phy/phy-core.c:716`），一对一，直接命中。**

#### 布局 B：provider 下每条链路一个子节点（arm64 上很常见）

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:1505-1530 —— 真实节点 */
u2phy0: usb2phy@e450 {
	compatible = "rockchip,rk3399-usb2phy";
	reg = <0xe450 0x10>;
	clocks = <&cru SCLK_USB2PHY0_REF>;
	clock-names = "phyclk";
	#clock-cells = <0>;
	clock-output-names = "clk_usbphy0_480m";
	status = "disabled";

	u2phy0_host: host-port {
		#phy-cells = <0>;		/* 在子节点上 */
		interrupts = <GIC_SPI 27 IRQ_TYPE_LEVEL_HIGH 0>;
		interrupt-names = "linestate";
		status = "disabled";
	};

	u2phy0_otg: otg-port {
		#phy-cells = <0>;		/* 每个子节点各有一个 */
		...
	};
};
```

⚠️ **三个关键点**：
1. **`#phy-cells` 在子节点上，provider 自己那个节点没有。**
2. **消费者引用的是子节点的 label**：**`phys = <&u2phy0_host>`**
   （真实例子：`arch/arm64/boot/dts/rockchip/rk3399.dtsi:347`）。
3. **这时 `of_phy_provider_lookup()` 靠 `children` 字段命中**：
   **先比 `provider->dev->of_node == node`（不等），再
   `for_each_child_of_node(provider->children, child)`（`drivers/phy/phy-core.c:140-144`）⇒ 命中。**
   ⇒ **而 `children` 在普通注册版里被替换成了 `dev->of_node`**（`:1116-1118`）
   ⇒ **所以布局 B 用普通的 `devm_of_phy_provider_register()` 就能工作**（02 §5.4 / 05 §2.3）。

⚠️ **子节点还要各自 `devm_phy_create(dev, child, ops)`**（05 §2.3）——
**传 NULL 会让所有实例共用 provider 的 `of_node`，`of_phy_simple_xlate` 就永远返回第一个。**

⚠️ **只有"子节点被多包一层"（比如 `pads { lane@0 { … } }`）才需要 `_full` 版**——
**树里唯一的使用者是 `drivers/phy/tegra/xusb.c:228`**（04 §5）。

### §2.3 arm64 真实节点（可直接 diff）

| 例子 | 位置 | 形态 |
|---|---|---|
| **布局 B（子节点）** | `arch/arm64/boot/dts/rockchip/rk3399.dtsi:1505-1530` | **`u2phy0` + 两个 `#phy-cells = <0>` 的子节点** |
| **多 cell** | `arch/arm64/boot/dts/ti/k3-am65-main.dtsi:61` / `:76` | **`#phy-cells = <2>`** |
| **最多 cell** | `arch/arm64/boot/dts/xilinx/zynqmp.dtsi:657` | **`#phy-cells = <4>`**（全 arm64 唯一一处） |

**arm64 DTS 统计（可重跑）**：

| 属性 | 文件数 | 行数 | 重跑命令 |
|---|---|---|---|
| `phys = ` | **149** | **558** | `grep -rl 'phys = ' arch/arm64/boot/dts/ \| wc -l` |
| `phy-names` | **130** | **399** | 同上换字符串 |
| `#phy-cells` | **108** | **425** | 同上 |
| ⚠️`phy-supply` | **149** | **288** | ⚠️ **注意：这个数字里混了别的子系统**（§4.2） |

---

## §3 consumer 侧：`phys` 与 `phy-names`

### §3.1 两个属性怎么配合

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:247-249 —— 真实节点（PCIe，四条 lane） */
	phys = <&pcie_phy 0>, <&pcie_phy 1>,
	       <&pcie_phy 2>, <&pcie_phy 3>;
	phy-names = "pcie-phy-0", "pcie-phy-1",
		    "pcie-phy-2", "pcie-phy-3";
```

**消费者侧的三种取法**（01 §4.1 / 05 §3）：

| 代码 | 用哪个属性 | 机理 |
|---|---|---|
| `devm_phy_get(dev, "pcie-phy-2")` | **`phy-names` → 下标 → `phys`** | `of_property_match_string(of_node, "phy-names", string)`（`drivers/phy/phy-core.c:746-747`） |
| `devm_phy_get(dev, NULL)` | **只用 `phys`，取第 0 个** | `index = 0`（`:748-749`） |
| `devm_of_phy_get_by_index(dev, np, 2)` | **只用 `phys`，按下标** | **完全不看 `phy-names`** |

⚠️ **`of_property_match_string()` 找不到时返回负数，而这个负数被直接当下标用**
（`drivers/phy/phy-core.c:746-750`：**没有检查 `index < 0`**）
⇒ **`of_parse_phandle_with_args()` 拿到负下标必然失败 ⇒ `-ENODEV`**（`:577-578`）。
⇒ **名字拼错的结果与"没有这个 PHY"完全一样**（§5 / §7）。

### §3.2 单 PHY 的最简形态

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:334-335 —— eMMC PHY */
	phys = <&emmc_phy>;
	phy-names = "phy_arasan";
```

**`#phy-cells = <0>` 时 `phys` 里就是一个裸 phandle，不带 cell。**

### §3.3 布局 B 的消费者写法

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:347-348 —— 引用的是子节点 label */
	phys = <&u2phy0_host>;
	phy-names = "usb";
```

**注意 `&u2phy0_host` 是 §2.2 里那个子节点的 label，不是 `&u2phy0`。**
⚠️ **写成 `<&u2phy0>` 会失败**：**provider 节点自己没有 `#phy-cells`
⇒ `of_parse_phandle_with_args()` 找不到 cell 数 ⇒ `-ENODEV`**（§7）。

---

## §4 `phy-supply`

### §4.1 框架怎么用它

```c
/* drivers/phy/phy-core.c:978-985 —— 在 phy_create() 里 */
	phy->pwr = regulator_get_optional(&phy->dev, "phy");
	if (IS_ERR(phy->pwr)) {
		ret = PTR_ERR(phy->pwr);
		if (ret == -EPROBE_DEFER)
			goto put_dev;

		phy->pwr = NULL;
	}
```

| 事实 | 说明 |
|---|---|
| **属性名是 `phy-supply`** | **regulator 子系统的约定：`regulator_get(dev, "phy")` 对应 DT 属性 `phy-supply`** |
| **取的是 `&phy->dev`，不是 provider 的 dev** | **所以属性要写在 PHY 节点（或子节点）上**，**因为 `phy->dev.of_node = node ?: dev->of_node`（`:969`）** |
| **取不到不算错** | **`-EPROBE_DEFER` 之外的错误一律 `phy->pwr = NULL`**（`:984`） |
| **框架自动开关** | **`phy_power_on()` 里 `regulator_enable`（`:330`）· `phy_power_off()` 里 `regulator_disable`（`:393`）** |
| **释放** | **`phy_release()` 里 `regulator_put`（`:1226`）** |

⇒ **驱动完全不用写 regulator 代码**（05 §1.1 第 5 点 / 08 §6）。

**真实例子（布局 B，属性写在子节点上）**：
```dts
/* arch/arm64/boot/dts/rockchip/rk3399-puma-haikou.dts:239-242 */
&u2phy0_host {
	phy-supply = <&vcc5v0_otg>;
	status = "okay";
};
```

### §4.2 ⚠️⚠️`phy-supply` 不止本框架在用（本篇最重要的一条）

**全树至少 8 个互不相干的消费者在用 `regulator_get*(dev, "phy")`**：

| # | 消费者 | 位置 | 属于哪个子系统 |
|---|---|---|---|
| **1** | **通用 PHY 框架** | `drivers/phy/phy-core.c:978` | **本文档** |
| 2 | `imx-mipi-csis` | `drivers/media/platform/nxp/imx-mipi-csis.c:816` | **media** |
| 3 | `libahci_platform` | `drivers/ata/libahci_platform.c:529` | **ATA** |
| 4 | `mdio-sun4i` | `drivers/net/mdio/mdio-sun4i.c:114` | **网络 MDIO** |
| **5** | **`dwmac-rk`** | `drivers/net/ethernet/stmicro/stmmac/dwmac-rk.c:1637` | **网络（stmmac）** |
| 6 | `dwmac-sunxi` | `drivers/net/ethernet/stmicro/stmmac/dwmac-sunxi.c:135` | 网络 |
| 7 | `dwmac-sun8i` | `drivers/net/ethernet/stmicro/stmmac/dwmac-sun8i.c:1161` | 网络 |
| 8 | `emac_rockchip` | `drivers/net/ethernet/arc/emac_rockchip.c:162` | 网络 |

重跑：`grep -rn '_regulator_get.*, *"phy")' --include=*.c drivers/`

⚠️ **一个真实的 arm64 反例**：

```dts
/* arch/arm64/boot/dts/rockchip/rk3328-rock-pi-e.dts:145-151 */
&gmac2io {
	...
	phy-handle = <&rtl8211e>;	/* 网络 PHY 的属性 */
	phy-mode = "rgmii";
	phy-supply = <&vcc_io>;		/* ⚠️ 这个 phy-supply 归 dwmac-rk，不归本框架 */
	...
};
```

⇒ **`grep -rl 'phy-supply' arch/arm64/boot/dts/` 得到的 149 个文件里，
有相当一部分是以太网节点，与通用 PHY 框架无关。**
⇒ **判据：看这个节点有没有 `#phy-cells`。**
**有 `#phy-cells` ⇒ 是通用 PHY 节点；有 `phy-handle`/`phy-mode` ⇒ 是网络控制器节点。**

⚠️ **本文档没有逐个分类那 149 个文件**（99 §5.3 已登记为未完成的统计）——
**所以 §2.3 那张表里的 `phy-supply` 行标了警告。**

---

## §5 ⚠️ 静默失效决策树（本篇核心）

**`devm_phy_get()` 返回 `-ENODEV` 是 phy 最常见的 DT 问题。**
**而 `devm_phy_optional_get()` 会把它变成 NULL ⇒ 连错误码都看不到。**
**照下面这棵树逐层排除，每层都给了验证方法。**

```
devm_phy_get() 返回 -ENODEV（或 optional 版返回 NULL）
│
├─ ① 消费者节点有 phys 属性吗？
│   └─ 没有 ⇒ of_parse_phandle_with_args 失败 ⇒ -ENODEV
│      （drivers/phy/phy-core.c:575-578）
│      验证：dtc -I fs /proc/device-tree/<路径> | grep phys
│
├─ ② phy-names 里有你要的那个名字吗？
│   └─ 没有 ⇒ of_property_match_string 返回负数
│      ⇒ 负数被直接当下标（:746-750，没有检查）⇒ 同样 -ENODEV
│      验证：确认代码里的字符串与 DTS 里 phy-names 的某一项完全相等
│      这是最常见的一条
│
├─ ③ phys 指向的节点有 #phy-cells 吗？
│   └─ 没有 ⇒ of_parse_phandle_with_args 找不到 cell 数 ⇒ -ENODEV
│      布局 B 里最容易犯：指向了 provider 节点而不是子节点（§3.3）
│
├─ ④ cell 个数对得上 #phy-cells 吗？
│   └─ 不对 ⇒ 同样 -ENODEV
│
├─ ⑤ PHY 节点 status 是 "disabled" 吗？
│   └─ 是 ⇒ 这一条有日志！
│      dev_warn "Requested PHY is disabled"（:593-596）⇒ -ENODEV
│      唯一能从 dmesg 直接看出来的一层
│      注意布局 B 里父子节点都可能是 disabled（rk3399 默认两级都 disabled）
│
├─ ⑥ PHY 节点的 compatible 是 "usb-nop-xceiv" 吗？
│   └─ 是 ⇒ 框架显式拒绝，无日志（:581-584）⇒ -ENODEV
│      那类节点归 drivers/usb/phy/ 老框架（04 §4.1）
│
├─ ⑦ PHY 驱动 probe 成功了吗？
│   └─ 没有 ⇒ provider 没注册 ⇒ -EPROBE_DEFER（不是 -ENODEV，:588-589）
│      所以拿到 -EPROBE_DEFER 说明 ①–⑥ 都通过了，问题在 PHY 驱动侧
│
└─ ⑧ 以上都对，但 phy_ops 漏了 .owner？
    └─ ⇒ try_module_get(NULL) ⇒ -EPROBE_DEFER（:761-762），无日志
       新写 PHY 驱动最难查的一个错（05 §7.5）
```

⚠️ **这棵树里只有第 ⑤ 层有日志。**
⇒ **`-ENODEV` 而 dmesg 里没有 "Requested PHY is disabled" ⇒ 落在 ①②③④⑥ 里，
只能靠读 DTS 和读代码。**
⚠️ **`-EPROBE_DEFER` 与 `-ENODEV` 的区分很有用**：**前者说明 DT 是通的，后者说明 DT 不通。**

---

## §6 怎么定位（三层）

### §6.1 编译期 / 静态层

| 想确认 | 命令 |
|---|---|
| **消费者代码用的名字** | `grep -n 'devm_phy_get\|devm_phy_optional_get\|devm_of_phy_get' <控制器驱动.c>` |
| **DTS 里的 `phy-names`** | `grep -n 'phy-names' -A 2 <板级.dts>` |
| **`#phy-cells` 在哪个节点上** | `grep -n '#phy-cells' -B 5 <SoC.dtsi>` |
| **PHY 驱动填了 `.owner` 吗** | `grep -n 'owner' <phy驱动.c>` ——**排 §5 第 ⑧ 层** |
| **PHY 驱动的 `of_xlate`** | `grep -n 'of_phy_provider_register\|of_xlate' <phy驱动.c>` |

### §6.2 运行期静态层：`/sys/class/phy/` 与 `/proc/device-tree/`

**phy 框架唯一的运行期观测面是 `/sys/class/phy/`**（07 §2）：

```bash
### PHY 实例造出来了吗？名字是 phy-<provider设备名>.<id>
ls /sys/class/phy/

### 这个 phy 的父设备（= provider）是谁
readlink /sys/class/phy/<name>/device

### DT 侧：属性到底进了 DTB 没有
ls /proc/device-tree/<节点路径>/
cat /proc/device-tree/<消费者节点>/phy-names | tr '\0' '\n'
```

⚠️ **`/sys/class/phy/` 里有条目 ≠ 消费者能拿到**：
**它只说明 `phy_create()` 成功了**；**provider 有没有注册、DT 能不能匹配都看不出来。**

⚠️ **`/sys/class/phy/` 里没有条目 ⇒ PHY 驱动没 probe 成功** ⇒ **先查那个驱动，不是查 DT。**

### §6.3 运行期动态层

| 手段 | 做法 | 局限 |
|---|---|---|
| **dmesg** | **`dmesg \| grep -iE 'phy'`** | **框架只有 16 处打印**（07 §4） |
| **`initcall_debug` / deferred probe 列表** | **`cat /sys/kernel/debug/devices_deferred`** | **能看出谁在等 provider**（§5 第 ⑦⑧ 层） |
| ⚠️ **tracepoint** | **没有** | **phy 框架 0 个 tracepoint**（07 §0） |
| ⚠️ **debugfs** | **没有** | 同上 |
| **`function_graph` ftrace** | **`echo phy_get > set_graph_function`** | **能看到走进哪个分支** ——**本文档未实测**（99 §5.1） |

---

## §7 错误消息 → 原因映射表

**phy 框架全部 16 处打印里，与 DT 直接相关的只有三条。**

| 消息（逐字） | 出处 | 原因 |
|---|---|---|
| `Requested PHY is disabled` | `drivers/phy/phy-core.c:594` | **PHY 节点 `status = "disabled"`** ——⚠️ **打在 provider 的 dev 上，不是消费者的 dev** |
| `missing string` | `drivers/phy/phy-core.c:753`（`dev_WARN`） | **非 DT 路径下 `phy_get(dev, NULL)`** ⇒ **`-EINVAL`** |
| `failed to create device link to %s` | `drivers/phy/phy-core.c:768` / `:881` / `:928`（三处字面相同，`dev_dbg`） | **device link 建失败** ——**不算错误，且默认不输出** |
| `unable to get id` | `drivers/phy/phy-core.c:959` | **IDA 耗尽**（极罕见） |
| `phy init failed --> %d` / `phy exit failed --> %d` / `phy poweron failed --> %d` / `phy poweroff failed --> %d` | `:264` / `:301` / `:345` / `:383` | **驱动的对应回调返回了错误** ——**与 DT 无关** |
| `phy_power_on was called before phy_init` | `:259` | **消费者调用顺序错** ——**与 DT 无关**（05 §7.1） |
| `couldn't find PHY resource` | `:693` / `:1066`（两处字面相同） | **devm 与非 devm 混用**（05 §7.4） |
| `couldn't find PHY provider device resource` | `:1209` | 同上 |
| `failed to create phy class --> %ld` | `:1235`（唯一的 `pr_err`） | **框架自身初始化失败**（几乎不会发生） |

⚠️ **没有任何一条消息告诉你"`phys` 属性写错了"。**
⇒ **①②③④⑥ 五种 DT 错误全部静默**（§5）。

---

## §8 死字与不成立的期待

| 期待 | 实际 | 依据 |
|---|---|---|
| **"`phy-names` 是必需属性"**（绑定文档这么写的） | ⚠️ **代码上不必需** | **`phy_get(dev, NULL)` 走 `index = 0`（`drivers/phy/phy-core.c:748-749`）；`devm_of_phy_get_by_index()` 完全不看它** |
| **"看到 `phy-supply` 就是通用 PHY 框架在管"** | ⚠️ **错，全树 8 个消费者** | **§4.2**；**判据是有没有 `#phy-cells`** |
| **"`phys` 和以太网的 `phy` / `phy-handle` 是一回事"** | ⚠️ **完全不是** | **绑定文档自己在提醒**（`phy-bindings.txt` 的 `phys` 条）；02 §1.0.A |
| **"`phys` 指向 provider 节点就行"** | ⚠️ **布局 B 里必须指向带 `#phy-cells` 的那个（子）节点** | **§3.3** |
| **"DTS 里 cell 个数写错会报错"** | ⚠️ **只会变成 `-ENODEV`，无日志** | `drivers/phy/phy-core.c:577-578` |
| **"框架会校验 `phy-supply` 指向的 regulator 存在"** | ⚠️ **不会** ——**取不到就当没有** | `:978-985` |
| **"`#phy-cells` 的 cell 有通用含义"** | ⚠️ **没有** ——**含义 100% 由 provider 的绑定定义** | **绑定文档原话：*"The meaning of all those cells is defined by the binding for the phy node"*** |
| **"通用绑定文档定义了更多属性"** | **只有 4 个**（`phy-bindings.txt` 72 行全文） | §1 |

**反例（说明结论的方向）**：**`phy-supply` 在 `rk3399-puma-haikou.dts:239-241`
确实是被通用 PHY 框架消费的**（写在 `&u2phy0_host` 上，而那个节点有 `#phy-cells`）
⇒ **不能反过来说"`phy-supply` 都不归本框架"。**

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 调试出口与速查 | `07-debug.md` |
| 代码骨架 | `05-usage.md` |
| 为什么 `-ENODEV` 被归一 | `08-feynman.md` §3 |
| DT 解析的完整流程 | `03-internals.md` §A4 |
