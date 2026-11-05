---
title: 设备树
---

# I2C 子系统（七）设备树配置

> **arm64 上 DT 是 I²C 唯一的枚举主线。** 适配器注册时核心只调用 `of_i2c_register_devices()`（`i2c-core-base.c:1588`）这一个枚举后端；ACPI 路径（`i2c-core-acpi.c`）是 x86 平台机制，本文档集不描述。
>
> 本篇回答四个问题：**属性该写哪些**、**每个属性由哪一行代码消费**、**mux/gate 这类拓扑怎么表达**、**配错了怎么定位**。
> 所有 DTS 片段里的行号引用都指向本树（6.1.158）的真实位置；标了 `arch/arm64/boot/dts/...` 的片段是从树里原样摘的。

**按任务找章节**（不必从头读）：

| 我要做的事 | 看哪里 |
|---|---|
| 在板子上启用一条 I²C 总线 | §2.1 两层写法 → §2.2 必备属性 → §2.3 时序 |
| 挂一颗芯片上去 | §3.1 骨架 → §3.3 中断 → §3.5 地址的坑 |
| 加 mux / gate / GPIO 切换 | §4.0 选型表 → 对应的 §4.2–§4.5 |
| 让本机当从设备 | §5.2 |
| 配了没生效、设备没出来 | §7.1 三层验证 → §7.2 错误消息表 → §7.3 症状清单 |
| 只想确认某个属性到底有没有人读 | §1 |

---

## 1. 先分清：谁消费什么

**同一个 i2c 节点里的属性有三个不同的消费者。** 搞混了就会出现最典型的那类问题：属性写了，`dtc` 也不报错，但没有任何人读它 —— **静默无效**。

| 消费者 | 典型属性 | 判断特征 |
|---|---|---|
| **DT / 驱动模型通用代码** | `compatible`、`reg`、`status`、`#address-cells`、`#size-cells`、`interrupts`、`clocks`、`resets`、`pinctrl-*`、`*-supply` | 与 i2c 无关，所有子系统都一样 |
| **i2c 核心** | 见下面那张完整表 | 全平台一致，**写了就一定生效** |
| **具体控制器驱动** | `multi-master`、`single-master`、`smbus`、`i2c-digital-filter`、`i2c-analog-filter`、`i2c-gpio,*`、各家私有属性 | **核心完全不看**。换个 SoC 就可能失效 |

第三类的实测规模（`rg` 于本树）：`multi-master` 只有 3 个驱动读（`i2c-tegra.c:1682`、`i2c-aspeed.c:940`、`i2c-rcar.c:1123`），`single-master` 只有 1 个（`i2c-xiic.c:821`），`i2c-digital-filter` 只有 2 个（`i2c-at91-master.c:905`、`i2c-stm32f7.c:679`）。**在别的 SoC 上写这些属性没有任何效果，也没有任何提示。**

> **所以判断一个属性有没有用，唯一可靠的办法是 `rg '"属性名"' drivers/i2c/`。** 通用 binding 文档（`Documentation/devicetree/bindings/i2c/i2c.txt`）里列的属性只是**约定**，不是保证 —— 它开头就写着 "These properties **may not be supported by all drivers**"。

### 1.1 i2c 核心真正会读的属性（全平台一致）

这张表是全篇的骨架，后面每一节都是对其中一行的展开。**"写在哪"一列尤其容易搞错**——同名属性放错节点就完全不生效。

| 属性 | 写在哪个节点 | 效果 | 消费代码 | 详见 |
|---|---|---|---|---|
| `reg \| I2C_TEN_BIT_ADDRESS` | 从设备 | 置 `I2C_CLIENT_TEN`，按 10 位地址寻址 | `i2c-core-of.c:41-44` | §3.1 |
| `reg \| I2C_OWN_SLAVE_ADDRESS` | 从设备 | 置 `I2C_CLIENT_SLAVE`，本机监听该地址 | `:46-49` | §5.2 |
| `host-notify` | 从设备 | 置 `I2C_CLIENT_HOST_NOTIFY`，probe 时分配虚拟中断 | `:55-56` | §3.3 |
| `wakeup-source` | 从设备 | 置 `I2C_CLIENT_WAKE`，注册唤醒中断 | `:58-59` | §3.3 |
| `interrupt-names = "irq"` | 从设备 | 指定哪一根是主中断（多中断时必须） | `i2c-core-base.c:507-511` | §3.3 |
| `interrupt-names = "wakeup"` | 从设备 | 指定专用唤醒中断线 | `:542-560` | §3.3 |
| `interrupt-names` 含 `"smbus_alert"` | **控制器** | 自动在 0x0c 建 ARA 设备 | `i2c-core-smbus.c:706-722` | §2.5 |
| `i2c-bus` 子节点 | **控制器** | 只把这个子节点下的节点当 i2c 设备 | `i2c-core-of.c:96-98` | §2.6 |
| `scl-gpios` / `sda-gpios` | **控制器** | 启用通用 GPIO 总线恢复 | `i2c-core-base.c:374`/`:394` | §2.4 |
| pinctrl 的 `"gpio"` state | **控制器** | 恢复时把引脚借道切成 GPIO | `:327-336` | §2.4 |
| `status` | 两者都可 | 非 `okay` 的节点直接跳过 | `i2c-core-of.c:100` | §2.7 |
| `clock-frequency` + 7 个 `i2c-*` 时序属性 | **控制器** | 填 `struct i2c_timings` | `i2c-core-base.c:1952-1976` | §2.3 |

**最后一行有个前提，务必注意**：时序属性由 `i2c_parse_fw_timings()` 解析，而**这个函数不是核心自动调的，是控制器驱动主动调的**。驱动不调，这 8 个属性全部无效（详见 §2.3）。表里其余各行都是核心无条件执行的。

---

## 2. 控制器节点

### 2.1 两层写法：SoC dtsi 定义 + 板级 dts 启用

arm64 的惯例是 SoC 的 `.dtsi` 里把所有 I²C 控制器都定义好并 `status = "disabled"`，板级 `.dts` 只负责"打开 + 加子设备"。

**SoC 层**（`arch/arm64/boot/dts/freescale/imx8mp.dtsi:845-853`，原样）：

```dts
i2c1: i2c@30a20000 {
	compatible = "fsl,imx8mp-i2c", "fsl,imx21-i2c";
	#address-cells = <1>;
	#size-cells = <0>;
	reg = <0x30a20000 0x10000>;
	interrupts = <GIC_SPI 35 IRQ_TYPE_LEVEL_HIGH>;
	clocks = <&clk IMX8MP_CLK_I2C1_ROOT>;
	status = "disabled";
};
```

**板级层**（`arch/arm64/boot/dts/freescale/imx8mp-evk.dts:234-247`，节选）：

```dts
&i2c1 {
	clock-frequency = <400000>;
	pinctrl-names = "default";
	pinctrl-0 = <&pinctrl_i2c1>;
	status = "okay";

	pmic@25 {
		compatible = "nxp,pca9450c";
		reg = <0x25>;
		interrupt-parent = <&gpio1>;
		interrupts = <3 IRQ_TYPE_LEVEL_LOW>;
		/* 这个 pmic 自己还有 regulators 子节点 —— 它们不是 i2c 设备 */
	};
};
```

另一种风格（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:585-598`）把 pinctrl 也放在 SoC 层，并用 `assigned-clocks` 固定功能时钟频率：

```dts
i2c1: i2c@ff110000 {
	compatible = "rockchip,rk3399-i2c";
	reg = <0x0 0xff110000 0x0 0x1000>;
	assigned-clocks = <&cru SCLK_I2C1>;
	assigned-clock-rates = <200000000>;
	clocks = <&cru SCLK_I2C1>, <&cru PCLK_I2C1>;
	clock-names = "i2c", "pclk";
	interrupts = <GIC_SPI 59 IRQ_TYPE_LEVEL_HIGH 0>;
	pinctrl-names = "default";
	pinctrl-0 = <&i2c1_xfer>;
	#address-cells = <1>;
	#size-cells = <0>;
	status = "disabled";
};
```

### 2.2 必备属性

| 属性 | 必需 | 说明 |
|---|---|---|
| `compatible` | ✔ | 决定绑哪个控制器驱动。多值时从左到右匹配（上面 imx8mp 复用了 `fsl,imx21-i2c` 的驱动） |
| `reg` | ✔ | 寄存器窗口。**注意 `#address-cells`/`#size-cells` 是父节点（soc）的**，rk3399 是 2/2 所以写 4 个数，imx8mp 是 1/1 所以写 2 个数 |
| `#address-cells = <1>` | ✔ | **必须是 1**。这声明的是"我的子节点用 1 个 cell 表示地址"，即 i2c 地址 |
| `#size-cells = <0>` | ✔ | **必须是 0**。i2c 设备没有"大小" |
| `interrupts` | 视驱动 | 控制器自己的中断。少数轮询式驱动不需要 |
| `clocks` / `clock-names` | 视驱动 | 多个时钟时必须给 `clock-names`，名字由驱动定义 |
| `status` | — | SoC dtsi 里 `"disabled"`，板级改 `"okay"`。**忘了这一步是最常见的 DT 错误**，症状是 `/dev/i2c-*` 里根本没有这条总线 |
| `pinctrl-names` / `pinctrl-0` | 视 SoC | 引脚复用。没配好的症状是总线上一片安静、所有地址 `-ENXIO` |
| `resets` / `power-domains` / `*-supply` | 视 SoC | 任一 provider 未就绪 → probe 返回 `-EPROBE_DEFER` |

**`#address-cells`/`#size-cells` 缺失的后果**：`dtc` 会警告，子节点的 `reg` 解析出来的值不可预期。这两个属性写在控制器节点上（或 §2.7 的 `i2c-bus` 子节点上）。

### 2.3 时序属性

**先确认一件事再往下读**：这 8 个属性由 `i2c_parse_fw_timings()`（`i2c-core-base.c:1952-1976`）解析进 `struct i2c_timings`（`include/linux/i2c.h:602-611`），而**这个函数是控制器驱动主动调的，核心不会替它调**。所以在陌生 SoC 上配时序前，先跑一次：

```bash
$ rg 'i2c_parse_fw_timings' drivers/i2c/busses/<你的驱动>.c
```

没有输出 = 这一整节的属性对你的板子全部无效。

| 属性 | 落到哪个字段 | 单位 | 不写时的默认值 |
|---|---|---|---|
| `clock-frequency` | `bus_freq_hz` | Hz | **100000**（`I2C_MAX_STANDARD_MODE_FREQ`），`:1957` |
| `i2c-scl-rising-time-ns` | `scl_rise_ns` | ns | 看频率，见下表，`:1960-1962` |
| `i2c-scl-falling-time-ns` | `scl_fall_ns` | ns | 看频率，见下表，`:1964-1965` |
| `i2c-scl-internal-delay-ns` | `scl_int_delay_ns` | ns | **0**，`:1967` |
| `i2c-sda-falling-time-ns` | `sda_fall_ns` | ns | **跟随 `scl_fall_ns`**（不是 0），`:1969` |
| `i2c-sda-hold-time-ns` | `sda_hold_ns` | ns | **0**，`:1971` |
| `i2c-digital-filter-width-ns` | `digital_filter_width_ns` | ns | **0**，`:1972` |
| `i2c-analog-filter-cutoff-frequency` | `analog_filter_cutoff_freq_hz` | **Hz**（不是 ns） | **0**，`:1974` |

上升/下降时间的默认值按 `bus_freq_hz` 分三档（`:1960-1965`）：

| 总线频率 | `scl_rise_ns` 默认 | `scl_fall_ns` 默认 |
|---|---|---|
| ≤ 100 kHz（标准模式） | 1000 | 300 |
| ≤ 400 kHz（快速模式） | 300 | 300 |
| > 400 kHz（Fast-mode Plus 及以上） | 120 | 120 |

（上面说的"`use_defaults = true`"就是指用这两张表的默认值；`false` 的含义见下面第 2 点。）

```dts
&i2c1 {
	clock-frequency = <400000>;
	i2c-scl-rising-time-ns = <140>;      /* 实测值，见 00 §2.1 的 Rp/Cb 计算 */
	i2c-scl-falling-time-ns = <20>;
	i2c-sda-falling-time-ns = <20>;
	i2c-sda-hold-time-ns = <300>;
	i2c-digital-filter;                  /* 布尔开关，由驱动读，不是核心 */
	i2c-digital-filter-width-ns = <50>;
};
```

三个细节：

1. **上升/下降时间是实测量，不是许愿值。** 它们描述的是你板子上 Rp 和 Cb 决定的**实际**波形（`00-overview.md` §2.1 有计算公式），控制器驱动拿它去反算分频寄存器。写得比实际小 → 实际频率偏高、可能超规格；写得比实际大 → 频率偏低、白损失带宽。
2. **`use_defaults` 参数的含义**（`:1940-1951` 的注释）：传 `true` 时未写的属性用上表的默认值；传 `false` 时保留驱动自己已经填好的值。注释明说 **"New drivers almost always should use the defaults"**，`false` 只是给老驱动避免回归用的。
3. **`i2c-digital-filter` / `i2c-analog-filter` 这两个布尔开关不在 `i2c_parse_fw_timings()` 里**，核心不读；只有 `i2c-at91-master.c:905/908` 和 `i2c-stm32f7.c:679/704` 自己读。而对应的 `*-width-ns` / `*-cutoff-frequency` 数值属性核心是读的 —— 一半在核心一半在驱动，这是个容易踩的不对称。

调试手段：这 8 个属性每解析一个都有 `dev_dbg(dev, "%s: %u\n", prop_name, *cur_val_p)`（`:1933`）。打开动态调试就能看到核心到底读到了什么：

```bash
$ echo 'file drivers/i2c/i2c-core-base.c function i2c_parse_timing +p' \
    | sudo tee /sys/kernel/debug/dynamic_debug/control
```

### 2.4 总线恢复（bus recovery）的 DT 侧配置

总线恢复是"从设备把 SDA 一直拉着不放，总线卡死"时的自救手段（原理见 `03-internals.md` §A7）。这里只讲 DT 侧：**它有两条路，一条全靠 DT 属性触发，另一条与 DT 无关。**

| | 路径 A：通用 GPIO 恢复 | 路径 B：控制器自带 |
|---|---|---|
| DT 要写什么 | `scl-gpios`（必需）+ `sda-gpios`（可选）+ pinctrl 的 `"gpio"` state | **什么都不用写** |
| 谁提供恢复动作 | 核心的 `i2c_generic_scl_recovery()` | 控制器驱动自己填的 `recover_bus` 回调 |
| 适用场合 | 引脚可以借道当 GPIO 使的 SoC | 硬件带"发 9 个时钟脉冲"之类的自恢复功能 |

**(a) DTS 怎么写**（路径 A）

```dts
&i2c1 {
	pinctrl-names = "default", "gpio";      /* "gpio" 这个名字是核心硬找的 */
	pinctrl-0 = <&pinctrl_i2c1>;            /* 正常工作时的引脚配置 */
	pinctrl-1 = <&pinctrl_i2c1_gpio>;       /* 恢复时切过去的配置 */

	scl-gpios = <&gpio5 14 GPIO_ACTIVE_HIGH>;
	sda-gpios = <&gpio5 15 GPIO_ACTIVE_HIGH>;
};
```

**(b) 核心拿它做什么** —— 入口 `i2c_gpio_init_recovery()`（`i2c-core-base.c:416-419`），两步，**pinctrl 先、GPIO 后**：

```
第 1 步  i2c_gpio_init_pinctrl_recovery()            (:303-346)
         找 "default" state ─── 找不到 ─┐
         找 "gpio" state              │  两个只要缺一个：
           └ 找不到再找 "recovery"     │  → 整套 pinctrl 恢复作废
             （已废弃，新代码别用）     │    清空 bri->pinctrl (:339-345)
                                       │
         两个都有 ──→ dev_info "using pinctrl states for GPIO recovery" (:340)

第 2 步  i2c_gpio_init_generic_recovery()            (:348-413)
         取 scl-gpios (GPIOD_OUT_HIGH_OPEN_DRAIN)     (:374)
           ├ -EPROBE_DEFER → 整个控制器 probe 推迟重试 (:375-378)
           └ 成功 → recover_bus = i2c_generic_scl_recovery
                    dev_info "using generic GPIOs for recovery"  (:380-382)
         取 sda-gpios (GPIOD_IN)，可选                (:394)
           取之前先把 SCL 拉低 + udelay(10)，取完再放开 (:390-398)
           └ 为什么？防止申请 GPIO 的瞬间在总线上打出毛刺
```

`sda-gpios` 虽然可选（`:386` 的注释明说 *"SDA GPIOD line is optional"*），但**只有配了它核心才能检测"SDA 被别人按住"、也才能主动放开 SDA**。能配就配。

**(c) 怎么确认它真的启用了** —— 这里有个陷阱：

| dmesg 里看到 | 含义 |
|---|---|
| `using pinctrl states for GPIO recovery` | 路径 A 的 pinctrl 部分成功（`:340`） |
| `using generic GPIOs for recovery` | 路径 A 的 GPIO 部分成功（`:382`）。**看到这条才算真的能恢复** |
| `Not using recovery: no suitable method provided` | 驱动设了 `bus_recovery_info` 但既没回调也没 GPIO。**这条是 `dev_dbg` 级别，默认看不见**（`:436` 把 `is_error_level` 置 false） |
| `Not using recovery: no {get\|set}_scl() found`<br>`Not using recovery: either get_sda() or set_sda() needed` | 配了但不完整，**这两条是 `dev_err`**，默认可见（分支在 `:463-466`） |
| 什么都没有 | 驱动根本没设 `bus_recovery_info`，`i2c_init_recovery()`（`:422`，调用点 `:1573`）在 `:428-429` 就返回了 |

**所以"没看到报错"不等于"恢复能用"。** 判据只有一条：**dmesg 里有没有那两条 `using ...` 成功日志**。

### 2.5 多主机与 SMBus 声明

```dts
&i2c1 {
	multi-master;        /* 或 single-master，二者不可共存 */
	smbus;               /* 声明按 SMBus 规则收紧（保留地址更多、超时定义不同） */
};
```

**这三个属性核心一个都不读**，只有个别驱动读（见 §1 的清单）。语义由 binding 文档定义（`i2c.txt`）：`multi-master` 让 OS 知道不能为省电停掉仲裁逻辑；`single-master` 让驱动可以更激进地判定"总线卡死"。

**SMBus Alert 是个需要单独说的例外，因为"看起来该用的属性"和"核心真正认的东西"不是一回事：**

| 你可能以为要写 | 实际 |
|---|---|
| `smbus-alert;` 布尔属性 | binding 里确实有，但**核心不看**，全树只有 `i2c-stm32f7.c:2265` 一个驱动读 |
| —— | 核心认的是**控制器节点的 `interrupt-names` 里含 `"smbus_alert"`** |

写法：

```dts
&i2c1 {
	interrupts = <GIC_SPI 35 IRQ_TYPE_LEVEL_HIGH>,
		     <GIC_SPI 36 IRQ_TYPE_LEVEL_HIGH>;
	interrupt-names = "irq", "smbus_alert";
};
```

核心据此自动建一个 ARA（Alert Response Address，0x0c）设备。判定代码是 `i2c_setup_smbus_alert()`（`i2c-core-smbus.c:706-722`，调用点在适配器注册流程里，`i2c-core-base.c:1564`），三步：

1. 查的是 **`adapter->dev.parent`**，也就是**控制器那个 platform device 的节点**，不是 adapter 自己 —— 属性写在 adapter 上是没用的；
2. `device_property_match_string(parent, "interrupt-names", "smbus_alert")` 命中 → `i2c_new_smbus_alert_device()` 在 0x0c 建一个名叫 `smbus_alert` 的 client；
3. 这个 client 的驱动再用 `fwnode_irq_get_byname(..., "smbus_alert")` 把中断取回来（`i2c-smbus.c:181-182`）。

### 2.6 `i2c-bus` 子节点：什么时候需要

默认情况下核心把控制器节点的**每一个可用子节点**都当作 i2c 设备。如果控制器节点下还挂了非 i2c 的子节点（比如某些 SoC 把 DMA 通道或 pinmux 描述成子节点），就需要用 `i2c-bus` 把真正的 i2c 设备圈起来：

```dts
&i2c1 {
	some-non-i2c-child { /* 不会被当成 i2c 设备 */ };

	i2c-bus {
		#address-cells = <1>;      /* 必须在这里重新声明 */
		#size-cells = <0>;

		eeprom@50 {
			compatible = "atmel,24c02";
			reg = <0x50>;
		};
	};
};
```

代码只有三行（`i2c-core-of.c:96-98`）：先找名为 `i2c-bus` 的子节点，找不到就退回用控制器节点自身。**有 `i2c-bus` 时，直接挂在控制器节点下的 i2c 设备会被忽略** —— 这是个静默失效，症状是设备凭空消失。

### 2.7 遍历规则与 `status`

`of_i2c_register_devices()`（`i2c-core-of.c:85-114`）的三条规则：

1. **`adap->dev.of_node` 为空就直接返回**（`:91-92`）—— 非 DT 来源的适配器（比如 i2c-stub、mux 通道在某些情况下）不走这条路；
2. 用 `for_each_available_child_of_node()`（`:100`）遍历 —— **`status` 不是 `"okay"`/`"ok"` 的子节点被跳过**，这就是"板级 dts 里 `status = "disabled"` 能关掉单个设备"的实现；
3. `OF_POPULATED` 标志防重复（`:101-102`）：已经建过设备的节点不会再建一次，失败时会把标志清掉（`:109`）以便重试。

---

## 3. 从设备（client）节点

### 3.1 骨架与逐属性对照

```dts
&i2c1 {
	status = "okay";
	clock-frequency = <400000>;

	foo@2a {
		compatible = "vendor,foo-chip";
		reg = <0x2a>;
		interrupt-parent = <&gpio3>;
		interrupts = <7 IRQ_TYPE_LEVEL_LOW>;
		vdd-supply = <&reg_3v3>;
		reset-gpios = <&gpio3 8 GPIO_ACTIVE_LOW>;
	};
};
```

解析全过程在 `of_i2c_get_board_info()`（`i2c-core-of.c:22-62`），只有 41 行，值得对照读：

| DT 写法 | 变成什么 | 代码 |
|---|---|---|
| `compatible = "vendor,foo-chip"` | `info.type = "foo-chip"`（**去掉厂商前缀**） | `:30`，`of_modalias_node()` → `drivers/of/base.c:1231-1232` 找第一个逗号取后半 |
| `reg = <0x2a>` | `info.addr = 0x2a` | `:35-39`、`:51` |
| `reg = <(0x3ff \| I2C_TEN_BIT_ADDRESS)>` | `info.flags \|= I2C_CLIENT_TEN` | `:41-44` |
| `reg = <(0x64 \| I2C_OWN_SLAVE_ADDRESS)>` | `info.flags \|= I2C_CLIENT_SLAVE` | `:46-49` |
| `host-notify;` | `info.flags \|= I2C_CLIENT_HOST_NOTIFY` | `:55-56` |
| `wakeup-source;` | `info.flags \|= I2C_CLIENT_WAKE` | `:58-59` |
| 节点本身 | `info.of_node` / `info.fwnode` | `:52-53` |
| `interrupts` | **不在这里解析**，延后到 probe 时 | `i2c-core-base.c:508-511` |

两个标志位的定义在 `include/dt-bindings/i2c/i2c.h`（整个头文件只有这两行）：

```c
#define I2C_TEN_BIT_ADDRESS	(1 << 31)
#define I2C_OWN_SLAVE_ADDRESS	(1 << 30)
```

用它们必须 `#include <dt-bindings/i2c/i2c.h>`，并且**要给整个表达式加括号**：`reg = <(0x50 | I2C_TEN_BIT_ADDRESS)>;`。

### 3.2 `compatible` 的三个副作用

`of_modalias_node()` 只取**第一个** compatible 字符串、去掉 `,` 之前的厂商前缀，结果同时决定了三件事：

1. **`i2c_client::name`**，也就是 `/sys/bus/i2c/devices/1-002a/name` 的内容；
2. **驱动匹配的最后一条退路**，即 `i2c_of_match_device_sysfs()`（`i2c-core-of.c:116-143`）：
   - 它拿 `client->name` 去比驱动 `of_match_table` 里的 compatible，**整串比一次、去掉厂商前缀再比一次**；
   - 正常 DT 设备根本用不到它 —— 走 `of_match_device()` 就匹配上了（`:154`）；
   - 它是给 sysfs `new_device` 手工创建的设备用的：那种 client 没有 `of_node`，`of_match_device()` 必然失败；
3. **modalias 冷插拔**：`modprobe $(cat /sys/bus/i2c/devices/1-002a/modalias)`。

**一个坑**：假如你写 `compatible = "vendor,foo-chip", "vendor,foo-chip-legacy";`，`info.type` 是 `"foo-chip"`（第一个），但驱动匹配走的是完整的 of_match_table，两者用的规则不同。名字看起来对不上不代表匹配失败。

### 3.3 中断的三种来源

`i2c_device_probe()`（`i2c-core-base.c:499-534`）按**优先级从高到低**试三种来源，第一个成功的胜出。**DT 场景走的是第三条**（前两条在 arm64 的 DT 板子上一般都不成立）：

| 优先级 | 条件 | 中断从哪来 | 代码 |
|---|---|---|---|
| 1 | `client->init_irq` 已经填了 | 代码里传进来的（板级 `i2c_board_info` 或 `i2c_new_client_device()` 的参数），DT 无关 | `:497`、`:499` |
| 2 | 带 `I2C_CLIENT_HOST_NOTIFY`（即 DT 写了 `host-notify`） | `i2c_smbus_host_notify_to_irq()` 分配的**虚拟中断**，没有物理中断线 | `:502-506` |
| 3 | 有 `of_node`（普通 DT 设备） | 先 `of_irq_get_byname(node, "irq")`；返回 `-EINVAL`/`-ENODATA` 就退回 `of_irq_get(node, 0)` 取第一个 | `:507-510` |

第 2 条有个容易忽略的副作用：走 host-notify 时核心会顺手 `pm_runtime_get_sync(adapter)`，**让适配器一直保持活跃**（因为随时可能有从设备主动上报），代价是这条总线不再进入 runtime suspend。

第 3 条的"先按名字、失败再按序号"意味着下面两种写法等价：

```dts
	/* 写法一：只有一个中断，不用命名 */
	foo@2a {
		interrupts = <7 IRQ_TYPE_LEVEL_LOW>;
	};

	/* 写法二：多个中断，必须命名，主中断叫 "irq" */
	foo@2a {
		interrupts = <7 IRQ_TYPE_LEVEL_LOW>, <8 IRQ_TYPE_EDGE_RISING>;
		interrupt-names = "irq", "wakeup";
	};
```

**唤醒中断**（`:542-560`）：只有写了 `wakeup-source` 才会走这段。核心用 `of_irq_get_byname(node, "wakeup")` 找专用唤醒中断；找到且与主中断不同 → `dev_pm_set_dedicated_wake_irq()`；否则复用主中断 → `dev_pm_set_wake_irq()`。也就是说 `wakeup-source` 单写就有意义（复用主中断），`interrupt-names` 里加 `"wakeup"` 是为了指定另一根线。

**i2c 核心认识的三个中断名**：`irq`（client 节点）、`wakeup`（client 节点）、`smbus_alert`（**控制器节点**，见 §2.5）。其余名字留给具体驱动。

### 3.4 节点名与 unit-address

```dts
	eeprom@50 {          /* 节点名任意，unit-address 应等于 reg 的十六进制无前缀形式 */
		reg = <0x50>;
	};
```

**核心只看 `reg`，完全不解析 unit-address。** 但 `dtc -Wunique_unit_address`（`make W=1`）会检查一致性，而且不一致会让所有看 DTS 的人误判。约定是：小写十六进制、不补前导零（`@50` 而不是 `@0x50`、不是 `@0050`）。

sysfs 里的目录名又是另一套：`i2c-core-base.c:892` 用 `"%d-%04x"` 格式，所以是 `1-0050`（**四位补零**）。三种写法（`@50` / `reg = <0x50>` / `1-0050`）指的是同一个地址。

### 3.5 地址值本身的坑

- **手册上的地址往往是 8 位形式。** 手册写 "device address 0xA0（写）/0xA1（读）"时，DT 里要写 `reg = <0x50>` —— 右移一位。写错的症状是 `-ENXIO`。详见 `00-overview.md` §2.4。
- **保留地址不能用。** `0x00-0x07` 和 `0x78-0x7f` 会被 `i2c_check_7bit_addr_validity_strict()` 拒绝。DT 里写了这些地址，`i2c_new_client_device()` 会失败并打印 `of_i2c: Failure registering ...`（`i2c-core-of.c:80`）。
- **同一条总线上地址不能重复。** 核心用 `i2c_check_addr_busy()` 检查；重复的第二个设备注册失败。真的需要两颗同地址芯片就得上 mux（§4）或者做 GPIO 片选。

---

## 4. mux / gate / arbitrator 的节点结构

### 4.0 先选写法

这一节有五种看起来很像的 DTS 形状，**先用下表定位到自己那一种，再去读对应小节**，不必顺读：

| 你的硬件 | 用哪种写法 | 看哪节 |
|---|---|---|
| mux 芯片本身挂在 I²C 上（pca9548 等），节点下**全是通道** | 通道直接做 mux 芯片的子节点 | §4.2 |
| 同上，但 mux 芯片节点下**还有别的非通道子节点** | 加一层 `i2c-mux` 分组节点（**不带 reg**） | §4.3 |
| 只有"通/断"两态，没有多路可选（给 RF tuner 降噪那种） | `i2c-gate` 节点本身就是那条子总线 | §4.4 |
| 多主机竞争仲裁 | `i2c-arb`，形状同 gate | §4.4 末 |
| mux **不是 I²C 设备**（GPIO / pinctrl / mux-controller 切换） | 节点独立于总线之外，用 `i2c-parent` 指回父总线 | §4.5 |

三种分组节点名对应三种拓扑类型（由驱动调 `i2c_mux_alloc()` 时的类型决定，不是 DT 决定）：

| 拓扑类型 | 分组子节点名 | 语义 |
|---|---|---|
| mux / switch | `i2c-mux` | 多路选择，一次通一路（或多路） |
| gate | `i2c-gate` | 只有开/关，无路可选（见 `i2c-gate.yaml`） |
| arbitrator | `i2c-arb` | 多主机竞争仲裁 |

### 4.1 核心是怎么找到通道节点的

这段逻辑值得看懂，因为 §4.3 那个"很难看出来的错误"就出在这里。代码是 `i2c_mux_add_adapter()`（`i2c-mux.c:355-392`），**对每一个通道各跑一遍**，目标只有一个：给这个通道的 adapter 找到它该用的 `of_node`。

```
              ┌─ 从 mux 设备自己的节点 dev_node 出发 ─┐
              │                                      │
   按拓扑类型找分组子节点（:360-365）
   arbitrator→"i2c-arb"  gate→"i2c-gate"  其余→"i2c-mux"
              │
       找到了吗？
        │        │
       没有      找到了
        │        │
        │    这个分组节点上有 reg 属性吗？（:367-373）
        │        │              │
        │       有              没有
        │        │              │
        │   判为"旧式条目"        │
        │   丢弃，当作没找到 │
        │        │              │
        └────────┴──────┐       │
                        │       │
          mux_node = dev_node   mux_node = 分组节点
          （回退到自己）(:375-376)      │
                        │              │
                        │       是 gate/arb 吗？(:377-378)
                        │        │           │
                        │       是          不是（mux）
                        │        │           │
                        │  这个节点自己       │
                        │  就是唯一的子总线    │
                        │  child = mux_node   │
                        │        │           │
                        └────────┼───────────┘
                                 │
                    child 还没定？→ 在 mux_node 的直接子节点里
                    找 reg == chan_id 的那个（:380-387）
                                 │
                    priv->adap.dev.of_node = child  (:390)
```

就是最坑的那一步：**分组节点一旦带了 `reg`，整个分组就被忽略**，核心退回到"在 mux 芯片节点下直接找通道"。注释原文在 `:368`：*"A `reg` property indicates an old-style DT entry"* —— 早年有人把 `i2c-mux` 当普通 i2c 子设备名用（所以带 `reg`），新写法里它是**纯分组节点、绝不带 reg**。

最后那行赋值是整条链的关键：**通道 adapter 拿到 `of_node` 之后，它自己注册时又会调 `of_i2c_register_devices()`**，于是通道节点里的设备才被建出来。反过来说：

- 通道节点的 `reg` 与驱动的 chan_id 对不上 → `child` 为 NULL → 这个子总线**存在但是空的**（`ls /sys/bus/i2c/devices/` 能看到 adapter，下面没有设备）；
- 两种失败都不打印任何错误。

### 4.2 标准写法：pca9548（8 通道 switch）

```dts
&i2c1 {
	status = "okay";

	i2c-mux@70 {
		compatible = "nxp,pca9548";
		reg = <0x70>;                     /* mux 芯片自己的 i2c 地址 */
		#address-cells = <1>;
		#size-cells = <0>;

		reset-gpios = <&gpio2 5 GPIO_ACTIVE_LOW>;
		i2c-mux-idle-disconnect;          /* 空闲时断开所有通道 */

		i2c@0 {
			#address-cells = <1>;         /* 每个通道都要重新声明 */
			#size-cells = <0>;
			reg = <0>;                    /* 通道号 = chan_id */

			eeprom@50 {                   /* 与下面那个地址相同，靠 mux 隔开 */
				compatible = "atmel,24c02";
				reg = <0x50>;
			};
		};

		i2c@1 {
			#address-cells = <1>;
			#size-cells = <0>;
			reg = <1>;

			eeprom@50 {
				compatible = "atmel,24c02";
				reg = <0x50>;
			};
		};
	};
};
```

要点：

- **节点名受 schema 约束**：`i2c-mux.yaml` 里 `$nodename: pattern: '^(i2c-?)?mux'`，所以 `i2c-mux@70` / `imux@70` / `mux@70` 合法，`switch@70` 不合法（`make dtbs_check` 会报）。
- 通道节点名必须匹配 `^i2c@[0-9a-f]+$`（`i2c-mux.yaml` 的 `patternProperties`）。
- **`i2c-mux-idle-disconnect`** 是 pca954x 驱动读的（`i2c-mux-pca954x.yaml`），语义是"空闲时断开所有子通道"。**多个 mux 且子总线上有相同地址时必须加** —— 否则两个 mux 可能同时通着，地址撞车。`idle-state` 属性（若存在）会覆盖它。
- 这里没有 `i2c-mux` 分组子节点，因为 pca9548 节点下面全是通道，不需要圈。要圈的场合见 §4.3。

### 4.3 需要 `i2c-mux` 分组节点的场合

当 mux 芯片节点下面还有非通道的子节点时：

```dts
	i2c-mux@70 {
		compatible = "nxp,pca9548";
		reg = <0x70>;

		some-other-child { /* 不是通道 */ };

		i2c-mux {                    /* 分组节点：不带 reg！ */
			#address-cells = <1>;
			#size-cells = <0>;

			i2c@0 {
				#address-cells = <1>;
				#size-cells = <0>;
				reg = <0>;
				/* ... */
			};
		};
	};
```

**分组节点一旦带了 `reg` 就会被当作旧式条目整个丢弃**（§4.1 决策树里标 的那一步），后果是核心退回到"在 mux 芯片节点下直接找通道"，而通道都在分组节点里面 —— 于是一个通道都找不到，且**不报任何错**。症状是所有子总线都空着。这是本篇最难看出来的错误，写分组节点时唯一要记住的就是：**它不带 `reg`。**

### 4.4 gate 的写法

gate 只有一条子总线，所以 `i2c-gate` 节点本身就是那条总线（`i2c-mux.c:378-379`），**不需要通道子节点、不需要 `reg`**：

```dts
	tuner@60 {
		compatible = "vendor,some-tuner";
		reg = <0x60>;

		i2c-gate {
			#address-cells = <1>;
			#size-cells = <0>;

			magnetometer@c {
				compatible = "asahi-kasei,ak8975";
				reg = <0x0c>;
			};
		};
	};
```

`i2c-arb` 同理（`i2c-arb.txt` / `i2c-arb-gpio-challenge.txt`）。

### 4.5 不是 i2c 设备的 mux：`i2c-parent`

GPIO 控制的 mux、pinctrl 切换的 mux、通用 mux-controller 驱动的 mux（`i2c-mux-gpio` / `i2c-mux-pinctrl` / `i2c-mux-gpmux`）**本身不挂在 i2c 总线上** —— 它们是独立的平台设备，所以节点写在 SoC 之外，用 `i2c-parent` 指回父总线：

```dts
i2cmux {
	compatible = "i2c-mux-gpio";
	#address-cells = <1>;
	#size-cells = <0>;

	mux-gpios = <&gpio1 22 0>, <&gpio1 23 0>;   /* 两根 GPIO → 4 个通道 */
	i2c-parent = <&i2c1>;                        /* 关键 */

	i2c@1 {
		#address-cells = <1>;
		#size-cells = <0>;
		reg = <1>;
		oled@3c { compatible = "solomon,ssd1307fb-i2c"; reg = <0x3c>; };
	};

	i2c@3 {
		#address-cells = <1>;
		#size-cells = <0>;
		reg = <3>;
		gpio@20 { compatible = "nxp,pca9555"; gpio-controller; #gpio-cells = <2>; reg = <0x20>; };
	};
};
```

（结构取自 `Documentation/devicetree/bindings/i2c/i2c-mux-gpio.yaml` 的 example。）

注意 `reg` 在这里是**通道号而不是地址**，且不必连续 —— 上例只用了 1 和 3 两个通道。

### 4.6 锁风格不在 DT 里

**mux-locked 还是 parent-locked 由驱动代码决定**（`i2c_mux_alloc()` 的 `I2C_MUX_LOCKED` 标志），DT 上没有任何属性能表达或改变它。这一点很重要，因为拓扑安全性完全取决于它 —— 详见 `04-extensions.md` §1.4/§1.5 的限制清单和 `07-debug.md` §8.5 的排查流程。

---

## 5. 两个特殊控制器的 DT

### 5.1 `i2c-gpio`：把两根 GPIO 当 I²C 用

没有硬件控制器、或硬件控制器坏了要临时绕过时用它。它跑的是 algo-bit（`04-extensions.md` §2.1），软件逐位摇引脚。

真实例子（`arch/arm64/boot/dts/exynos/exynos5433-tm2-common.dtsi:88-104`，原样）：

```dts
i2c_max98504: i2c-gpio-0 {
	compatible = "i2c-gpio";
	sda-gpios = <&gpd0 1 GPIO_ACTIVE_HIGH>;
	scl-gpios = <&gpd0 0 GPIO_ACTIVE_HIGH>;
	i2c-gpio,delay-us = <2>;              /* 半周期延时 → ~250 kHz */
	#address-cells = <1>;
	#size-cells = <0>;

	max98504: amplifier@31 {
		compatible = "maxim,max98504";
		reg = <0x31>;
	};
};
```

| 属性 | 说明 |
|---|---|
| `sda-gpios` / `scl-gpios` | 必需。binding 建议加 `GPIO_OPEN_DRAIN` 标志 |
| `i2c-gpio,delay-us` | 半周期延时。**这是唯一的调速手段**（没有 `clock-frequency`）。`delay-us = <2>` ≈ 250 kHz，`<5>` ≈ 100 kHz |
| `i2c-gpio,timeout-ms` | 超时，映射到 `adap->timeout` |
| `i2c-gpio,scl-output-only` | SCL 只能输出（读不回来）→ **不支持时钟拉伸检测**，核心会打上 `I2C_AQ_NO_CLK_STRETCH`（`algos/i2c-algo-bit.c:661-662`） |
| `gpios = <...>` | **已废弃**，用 `{sda,scl}-gpios` |
| `i2c-gpio,sda-open-drain` / `-scl-open-drain` | **已废弃，且语义与字面相反** |

**关于开漏，这里有三个层次，混在一起最容易搞错**：

1. **默认就是开漏**：驱动申请引脚时用的是 `GPIOD_OUT_HIGH_OPEN_DRAIN`（`i2c-gpio.c:398`/`:406`）。所以 DTS 里**不写** `GPIO_OPEN_DRAIN` 也照样工作 —— 上面那个树内真实例子就没写。
2. **在 `*-gpios` 里写 `GPIO_OPEN_DRAIN`**：binding 建议这么写，属于"把事实写明白"，行为不变。
3. **`i2c-gpio,sda-open-drain` / `-scl-open-drain` 这两个布尔属性：已废弃，而且语义与字面相反。** 它们的意思**不是**"请把引脚设成开漏"，而是"**引脚已经被 gpiolib 之外的东西设成开漏了，别再让 gpiolib 去设**"（`:313-315`）—— 写了它，驱动反而改用普通 `GPIOD_OUT_HIGH`。binding 自己把它称为 "essentially an inconsistency flag"。**不要按字面理解，新 DTS 不要用。**

### 5.2 本机作为从设备（slave 模式）

`I2C_OWN_SLAVE_ADDRESS` 标志告诉核心"这个地址是**我们自己**要监听的，不是一颗外部芯片"：

```dts
&i2c1 {
	status = "okay";

	/* 本机在 0x64 上模拟一颗 24c02 EEPROM */
	eeprom@64 {
		compatible = "slave-24c02";
		reg = <(0x64 | I2C_OWN_SLAVE_ADDRESS)>;
	};
};
```

前提是控制器驱动实现了 `.reg_slave`（6.1 里共 13 个驱动，全部是 arm/arm64 平台，清单见 `00-overview.md` §2.9c）。后端行为见 `04-extensions.md` §3。

---

## 6. 运行时增删：overlay 与 `OF_DYNAMIC`

`CONFIG_OF_DYNAMIC` 打开时，i2c 核心注册一个 DT 变更通知器 `i2c_of_notifier`（`i2c-core-of.c:162-220`，注册点 `i2c-core-base.c:2117-2118`），于是 **加载 overlay 就能动态创建 i2c 设备**：

| DT 事件 | 核心动作 | 代码 |
|---|---|---|
| `OF_RECONFIG_CHANGE_ADD` | 用 `rd->dn->parent` 找对应的 adapter；找不到就当作"不是给我的"直接返回 | `:172-174` |
| | `OF_POPULATED` 已置位 → 跳过（防重复） | `:176-179` |
| | **清 `FWNODE_FLAG_NOT_DEVICE`**，然后注册 client | `:185-186` |
| | 失败 → 打印 `failed to create client for '%pOF'` 并清掉 `OF_POPULATED` | `:188-192` |
| `OF_RECONFIG_CHANGE_REMOVE` | `OF_POPULATED` 未置位 → 没建过，返回 | `:198-199` |
| | `of_find_i2c_device_by_node()` 找到 client → `i2c_unregister_device()` | `:202-210` |

表里"清 `FWNODE_FLAG_NOT_DEVICE`"那一步看着突兀，`:181-184` 的注释解释了原因：

> *Clear the flag before adding the device so that fw_devlink doesn't skip adding consumers to this device.*

拆开说三句话：

1. `FWNODE_FLAG_NOT_DEVICE` 的含义是"这个 DT 节点不会变成 `struct device`"；
2. 带着这个标志，`fw_devlink` 就**不给它建依赖关系**（device link）；
3. 而 i2c 设备是需要依赖关系的 —— 它得等 regulator、gpio 这些 provider 就绪才能 probe。所以必须**先清标志、再注册设备**，否则 overlay 里的设备会在 provider 没就绪时就 probe。

实操（需要 `CONFIG_OF_CONFIGFS` 或平台自己的 overlay 加载路径）：

```bash
# 编译 overlay
$ dtc -@ -I dts -O dtb -o foo.dtbo foo.dtso

# 典型加载路径（取决于平台/发行版，并非所有内核都有）
$ mkdir /sys/kernel/config/device-tree/overlays/foo
$ cat foo.dtbo > /sys/kernel/config/device-tree/overlays/foo/dtbo

# 验证
$ ls /sys/bus/i2c/devices/
$ dmesg | tail
```

> **未验证**：上面的加载路径本机没有跑过（见 `99-evidence.md` §5.1）。`OF_DYNAMIC` 通知器的行为是逐行读代码得出的，overlay 的用户态接口在不同平台差异很大。

不改 DT 也能临时加设备的办法见 `05-usage.md` §3（sysfs `new_device`）—— 那条路不经过 DT，建出来的 client 没有 `of_node`，因此走的是 §3.2 说的 `i2c_of_match_device_sysfs()` 匹配退路。

---

## 7. DT 配错了怎么定位

### 7.1 三层验证

按顺序查，每一层排除掉一类原因，不要跳步：

| 顺序 | 查什么 | 能排除的原因 |
|---|---|---|
| ① 编译期 | `dtc` / `make dtbs_check` | 语法错、schema 不符、unit-address 与 `reg` 不一致、必需属性缺失 |
| ② 运行期（静态） | `/sys/firmware/devicetree/base/` | **内核实际拿到的 DT 跟你改的那份不是一份** |
| ③ 运行期（动态） | `dmesg` + `/sys/bus/i2c/devices/` | 核心到底建出了哪些设备、卡在哪一步 |

**② 是最容易被跳过、又最值得先做的一步。** 板级 dts 被别的文件覆盖、bootloader 加载的还是旧 dtb、overlay 根本没加载上 —— 这三种情况在源码里怎么看都看不出来，但在这里立刻现形。

#### ① 编译期：schema 检查

```bash
$ make dt_binding_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/i2c/
$ make dtbs_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/i2c/
```

需要先 `pip install dtschema`，原因是 6.1 的 binding 目录本身不自洽：

- `Documentation/devicetree/bindings/i2c/` 下有 80 个文件，但通用 binding **还是未 yaml 化的 `i2c.txt`**；
- `i2c-mux.yaml` 引用的 `/schemas/i2c/i2c-controller.yaml` 在本树里**不存在**，它由外部 `dtschema` 包提供。

所以不装 dtschema，`dtbs_check` 对 i2c 节点基本查不出东西。
> **未验证**：本机没有 dtschema，上面两条命令没有实际执行过。

#### ② 运行期静态：内核实际拿到的 DT

```bash
# 逐个属性看
$ ls /sys/firmware/devicetree/base/soc@0/i2c@30a20000/
compatible  reg  status  interrupts  clocks  #address-cells  name  pmic@25
$ cat /sys/firmware/devicetree/base/soc@0/i2c@30a20000/status
okay

# 或者整棵树反编译回可读 DTS —— 最实用的一招
$ dtc -I fs -O dts /sys/firmware/devicetree/base > /tmp/running.dts
$ sed -n '/i2c@30a20000/,/};/p' /tmp/running.dts
```

### 7.2 错误消息 → 原因（第③层）

i2c 的 DT 解析只会打这几条消息，全部来自 `i2c-core-of.c`：

| 消息 | 位置 | 原因 |
|---|---|---|
| `of_i2c: modalias failure on <节点>` | `:31` | 子节点**没有 `compatible`** 属性 |
| `of_i2c: invalid reg on <节点>` | `:37` | 子节点**没有 `reg`**，或 `reg` 不是单个 u32（例如 `#address-cells` 写成了 2） |
| `of_i2c: Failure registering <节点>` | `:80` | 地址非法（保留地址）、地址已被占用、或分配 client 失败 |
| `Failed to create I2C device for <节点>` | `:106-108` | 同上，外层再报一次 |
| `failed to create client for '<节点>'` | `:188` | overlay 动态添加失败 |

**最麻烦的一类是"什么消息都没有，设备也不存在"** —— 核心是**静默跳过**的，三种可能：

1. 节点 `status != "okay"`：被 `for_each_available_child_of_node()` 直接跳过（`i2c-core-of.c:100`）；
2. 总线上有 `i2c-bus` 分组子节点，设备写在了分组外面（§2.6）；
3. 节点挂在了错误的父节点下（例如挂到了 mux 本体而不是通道节点上，§4.1）。

打开这两条 `dev_dbg` 能看到遍历过程：

```bash
$ echo 'file drivers/i2c/i2c-core-of.c +p' | sudo tee /sys/kernel/debug/dynamic_debug/control
# of_i2c: walking child nodes        (i2c-core-of.c:94)
# of_i2c: register <节点>            (:72)  ← 每个被识别的子节点一行
```

**"walking child nodes" 都没打印**，说明 `adap->dev.of_node` 是空的（`:91-92` 提前返回）—— 控制器驱动没把 of_node 传给 adapter，或者这条总线根本不是 DT 来的。

### 7.3 常见错误清单

按"坏掉的范围"分四组 —— 先看整条总线在不在，再看单个设备，最后才怀疑参数。

**A. 整条总线级**

| 症状 | 最可能的 DT 原因 | 怎么确认 |
|---|---|---|
| `/dev/i2c-*` 里没这条总线 | 控制器节点 `status` 没改 `"okay"` | `cat /sys/firmware/devicetree/base/.../status` |
| 控制器一直不 probe | `clocks`/`resets`/`*-supply`/pinctrl 的 provider 未就绪 | `cat /sys/kernel/debug/devices_deferred` |
| 总线在，但一个设备都没有 | 子节点 `status = "disabled"`；或有 `i2c-bus` 分组把设备挡在外面（§2.6） | 开 `i2c-core-of.c` 的 dev_dbg 看有没有 `register` 行 |
| 总线在、设备也在，但所有地址都 `-ENXIO` | pinctrl 没配/配错，引脚还是 GPIO 或别的功能 | 量引脚静态电平（应为高）；`cat /sys/kernel/debug/pinctrl/*/pinmux-pins` |

**B. 单个设备级**

| 症状 | 最可能的 DT 原因 | 怎么确认 |
|---|---|---|
| 只少了某一个设备 | 那个节点缺 `compatible` 或 `reg` | `dmesg \| grep of_i2c`，对照 §7.2 前两条消息 |
| 设备存在但驱动没绑 | `compatible` 与驱动 `of_match_table` 不一致 | `readlink /sys/bus/i2c/devices/N-00XX/driver`；见 `07-debug.md` §8.1 |
| 地址差一倍（0xA0 vs 0x50） | `reg` 写了手册上的 8 位地址 | `00-overview.md` §2.4 |

**C. 参数级（设备在、能通，但行为不对）**

| 症状 | 最可能的 DT 原因 | 怎么确认 |
|---|---|---|
| 频率不对 | 驱动没调 `i2c_parse_fw_timings()`（§2.3 的前提）；或功能时钟频率（`assigned-clock-rates`）与驱动假设不符 | 开 §2.3 的 dev_dbg 看核心读到的值；`cat /sys/kernel/debug/clk/clk_summary` |
| `dtc` 报 unit-address 警告 | `@50` 与 `reg = <0x50>` 不一致 | `make W=1 dtbs` |

**D. mux 级**

| 症状 | 最可能的 DT 原因 | 怎么确认 |
|---|---|---|
| mux 子总线是空的 | 通道节点 `reg` 与 chan_id 不匹配；或分组节点 `i2c-mux` 上误加了 `reg`（§4.3） | `ls /sys/bus/i2c/devices/` 看子 adapter 在不在、下面有没有设备 |
| mux 下同地址设备偶发读错 | 缺 `i2c-mux-idle-disconnect` | `04-extensions.md` §1.5、`07-debug.md` §8.5 |

---

## 8. 一页速查

```
控制器节点必须有：
  compatible / reg / #address-cells=<1> / #size-cells=<0> / status="okay"
  加上 SoC 需要的 interrupts / clocks / pinctrl-* / resets / *-supply

从设备节点必须有：
  compatible（决定 name 和模块自动加载）
  reg = <7位地址>            ← 手册的 0xA0 要写成 0x50

核心真正会读的 i2c 专属属性（其余都是驱动私有；完整表+行号见 §1.1）：
  子设备  reg|I2C_TEN_BIT_ADDRESS   reg|I2C_OWN_SLAVE_ADDRESS
          host-notify   wakeup-source   interrupt-names="irq","wakeup"
  控制器  i2c-bus 子节点   scl-gpios/sda-gpios   pinctrl "gpio" state
          interrupt-names 含 "smbus_alert"（→ 自动建 0x0c 的 ARA）
  两者    status                     ← 非 okay 一律静默跳过
  时序    clock-frequency + 7 个 i2c-*      ← 仅当驱动调 i2c_parse_fw_timings()

拓扑节点名：i2c-mux（不带 reg！）/ i2c-gate / i2c-arb
           通道节点名 i2c@N，reg = 通道号（不是地址）
           非 i2c 设备形态的 mux 用 i2c-parent = <&i2cN>

三条定位命令：
  dtc -I fs -O dts /sys/firmware/devicetree/base   ← 内核实际拿到的 DT
  dmesg | grep of_i2c                              ← 解析失败的原因
  echo 'file drivers/i2c/i2c-core-of.c +p' > /sys/kernel/debug/dynamic_debug/control
                                                   ← 看遍历了哪些节点
```

**关键源文件**：`drivers/i2c/i2c-core-of.c`（220 行，全篇的解析侧）、`i2c-core-base.c:1952`（时序）、`:303-470`（恢复）、`:499-560`（中断）、`i2c-mux.c:355-392`（拓扑）、`include/dt-bindings/i2c/i2c.h`（两个标志位）、`Documentation/devicetree/bindings/i2c/i2c.txt`（通用 binding）。
