---
title: 设备树
---

# mfd 06：设备树

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇覆盖三件互不相同的 DT 事务，**别混**：
**① mfd 父节点 + 子节点**（父驱动在代码里分家，DT 只提供属性，§2）·
**② `simple-mfd`**（DT 直接决定有哪些子设备，`mfd-core.c` 不参与，§3）·
**③ `syscon`**（不建子设备，只交出一个 regmap，§4）。

---

## §1 官方绑定文档只有两篇

⚠️⚠️ **`Documentation/driver-api/mfd*` 不存在，`Documentation/mfd/` 目录也不存在**（01 §开篇）。
框架级的官方文字全部在 DT 绑定里，一共两篇：

| 文件 | 行数 | 讲什么 |
|---|---|---|
| `Documentation/devicetree/bindings/mfd/mfd.txt` | **51** | ⚠️ 老式 `.txt`；"什么算 MFD" + `simple-mfd` / `ranges` / `#address-cells` / `#size-cells` 四条属性 |
| `Documentation/devicetree/bindings/mfd/syscon.yaml` | **118** | syscon 的 compatible 白名单 + `reg` / `reg-io-width` / `hwlocks` |

`Documentation/devicetree/bindings/mfd/` 目录下另有 **83 个 `.yaml` / 137 项**，
⚠️ **但那些全是单芯片绑定**（`x-powers,axp20x.yaml` 之类），不是框架说明。

⚠️ `mfd.txt:3-16` 那段定义值得抄一遍，因为它是这个子系统**唯一的官方范围界定**：

> These devices comprise a nexus for heterogeneous hardware blocks containing
> more than one non-unique yet varying hardware functionality.

它列了两类（`:8-16`）：**外部总线上的混合信号 ASIC（通常是 PMIC）**，
以及**一段"杂项系统寄存器"（即 syscon）**。
⇒ ⚠️ **"syscon 算 MFD"这个判断是官方文档给的**，不是本套自己划的（01 §4）。

---

## §2 形态一：mfd 父节点 + 子节点

⚠️ **这是 I2C/SPI 上外部芯片的形态**（04 §2）。特点：
**子设备由父驱动的 cell 数组决定，DT 里的子节点只负责给属性。**

### §2.1 父节点本体

```dts
/* arch/arm64/boot/dts/allwinner/sun50i-a64-bananapi-m64.dts:190-196 —— 节选 */
axp803: pmic@3a3 {
        compatible = "x-powers,axp803";
        reg = <0x3a3>;
        interrupt-parent = <&r_intc>;
        interrupts = <GIC_SPI 32 IRQ_TYPE_LEVEL_LOW>;
        x-powers,drive-vbus-en; /* set N_VBUSEN as output pin */
};
```

⚠️⚠️ **注意这里没有 `simple-mfd`**，也没有 `ranges`、没有 `#address-cells`。
⇒ 因为子设备不是 OF 自动实例化的，**是 `axp20x.c` 调 `mfd_add_devices()` 建的**。

⚠️ `reg = <0x3a3>` 是 I2C 从地址（`0x3a3` 的高位是 RSB 扩展标志）
⇒ **父节点挂在 I2C/RSB 控制器下面，不是内存地址空间里。**

### §2.2 子节点：节点名不重要，`compatible` 才重要

```dts
/* arch/arm64/boot/dts/allwinner/axp803.dtsi:9-38 —— 节选 */
&axp803 {
        interrupt-controller;
        #interrupt-cells = <1>;

        ac_power_supply: ac-power {
                compatible = "x-powers,axp803-ac-power-supply",
                             "x-powers,axp813-ac-power-supply";
                status = "disabled";
        };

        axp_adc: adc {
                compatible = "x-powers,axp803-adc", "x-powers,axp813-adc";
                #io-channel-cells = <1>;
        };

        axp_gpio: gpio {
                compatible = "x-powers,axp803-gpio", "x-powers,axp813-gpio";
                gpio-controller;
                #gpio-cells = <2>;
                ...
        };
```

⚠️⚠️ **匹配靠的是 `compatible`，不是节点名。**
`mfd_match_of_node_to_dev()` 在父节点的直接子节点里找
`of_device_is_compatible(np, cell->of_compatible)`
（`drivers/mfd/mfd-core.c:214-216`，03 §2.5）。
⇒ 节点叫 `adc` 还是 `axp-adc` 都无所谓。

⚠️ **子节点没有 `reg`**（除了 `use_of_reg` 那种情况，§2.5）
—— 寄存器地址在父驱动的 cell 里写死了，DT 不重复。
⚠️ **子节点也没有 `interrupts`** —— 中断号在 cell 的 `resources[]` 里
（`drivers/mfd/axp20x.c:182-185`），DT 不管。

⇒ ⚠️⚠️ **这是本形态与所有其它 DT 形态最大的不同**：
**子节点只提供"这个功能块怎么配置"的属性，不提供"它在哪"的信息。**

### §2.3 ⚠️ `status = "disabled"` 的静默后果

⚠️⚠️ **`ac-power` 那个子节点默认是 `status = "disabled"`**
（`axp803.dtsi:16`），板级 dts 里再打开：

```dts
/* arch/arm64/boot/dts/allwinner/sun50i-a64-bananapi-m64.dts:201-203 */
&ac_power_supply {
        status = "okay";
};
```

⚠️ 核心遇到 disabled 的子节点时（`drivers/mfd/mfd-core.c:234-238`）：
**置 `ret = 0` 然后跳失败路径** ⇒ **子设备根本不注册，而 `mfd_add_devices()` 返回成功**。

⇒ ⚠️⚠️ **"子设备在 `/sys/bus/platform/devices/` 里根本不存在，日志里一句话都没有"
的头号原因就是这个**（05 §6、07 §2）。**先查 DT 的 `status`。**

### §2.4 父节点常常同时是中断控制器

```dts
/* arch/arm64/boot/dts/allwinner/axp803.dtsi:10-11 */
interrupt-controller;
#interrupt-cells = <1>;
```

⚠️ 这两行让 PMIC 自己成为一个中断控制器（由 `regmap_add_irq_chip()` 实现的域）
⇒ 板上别的设备可以写 `interrupt-parent = <&axp803>`。

⚠️⚠️ **但它与 mfd 子设备的中断无关**：axp20x 的子设备中断走的是
cell `resources[]` 里的 hwirq + 子驱动自己 `regmap_irq_get_virq()`
（04 §2.2）—— **完全不经过 DT 的 `interrupts` 属性**。
⇒ **同一颗芯片上两套中断路径并存**：DT 路径给外部设备用，资源路径给自己的子设备用。

### §2.5 ⚠️ 需要 `reg` 区分同类子节点的例外

当两个子节点 `compatible` 相同时，父驱动会用 `use_of_reg = true` + `of_reg`
按地址匹配（04 §4.1）。**这时子节点必须有 `reg`**：

```dts
/* 形如 VSC7512 的两个 MDIO 控制器 */
mdio0: mdio@7107009c {
        compatible = "mscc,ocelot-miim";
        reg = <0x7107009c 0x24>;
};

mdio1: mdio@710700c0 {
        compatible = "mscc,ocelot-miim";
        reg = <0x710700c0 0x24>;
};
```

⚠️ 核心取的是 `of_get_address(np, 0, NULL, NULL)` 的第一个地址
（`drivers/mfd/mfd-core.c:141-152`），⚠️ **原样比较，不做 `ranges` 换算**
⇒ **`of_reg` 里必须写与 DT 中 `reg` 第一个 cell 完全相同的值**（03 §2.5.1）。

---

## §3 形态二：`simple-mfd`

### §3.1 `mfd.txt` 的原文

```
/* Documentation/devicetree/bindings/mfd/mfd.txt:20-26 */
- compatible : "simple-mfd" - this signifies that the operating system should
  consider all subnodes of the MFD device as separate devices akin to how
  "simple-bus" indicates when to see subnodes as children for a simple
  memory-mapped bus. For more complex devices, when the nexus driver has to
  probe registers to figure out what child devices exist etc, this should not
  be used. In the latter case the child devices will be determined by the
  operating system.
```

⚠️⚠️ **最后两句是官方给的选型判据**，值得逐字读：
**"当 nexus 驱动必须去读寄存器才能知道有哪些子设备时，不要用这个"**
⇒ 那种情况下子设备由操作系统（即 `mfd_add_devices()`）决定。

**实现在 OF 子系统里，不在 mfd**：

```c
/* drivers/of/platform.c:24-31 */
const struct of_device_id of_default_bus_match_table[] = {
        { .compatible = "simple-bus", },
        { .compatible = "simple-mfd", },
        { .compatible = "isa", },
        ...
};
```

⇒ ⚠️ **`simple-mfd` 与 `simple-bus` 在代码里是同一条路**，
`of_platform_populate()` 递归实例化子节点。**语义差别纯属文档约定**：
`simple-bus` 表示"这是一条总线"，`simple-mfd` 表示"这是一颗多功能芯片"。

### §3.2 另外三条属性

| 属性 | `mfd.txt` | 作用 |
|---|---|---|
| `ranges` | `:28-30` | ⚠️ **子节点的 `reg` 是相对地址，靠它换算成物理地址** |
| `#address-cells` | `:32-33` | ⚠️ **用了 `ranges` 就必须有** |
| `#size-cells` | `:35-36` | 同上 |

⚠️ `mfd.txt:29-30` 说 `ranges` 的三段依次是
"子地址基址（应为 0）、父地址空间里的物理地址、映射长度"。

### §3.3 arm64 上的实例

**arm64 dts/dtsi 里用 `simple-mfd` 的有 62 个文件。** 最常见的写法是三个 compatible 连写：

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:1022-1035 —— 节选 */
pmu: power-management@ff310000 {
        compatible = "rockchip,rk3399-pmu", "syscon", "simple-mfd";
        reg = <0x0 0xff310000 0x0 0x1000>;

        power: power-controller {
                compatible = "rockchip,rk3399-power-controller";
                #power-domain-cells = <1>;
                ...
        };
};
```

⚠️⚠️ **三个 compatible 各管一件事，缺一不可**：

| compatible | 谁用它 |
|---|---|
| `"rockchip,rk3399-pmu"` | 如果有专用驱动，它来认（这里没有） |
| `"syscon"` | ⚠️ 让别的驱动能 `syscon_regmap_lookup_by_phandle()` 拿 regmap |
| `"simple-mfd"` | ⚠️ 让 `power-controller` 子节点被自动实例化成 platform_device |

⇒ **这一个节点同时满足了两种消费方式**：想读寄存器的走 syscon，
想当独立设备的做子节点。⚠️ **这是 arm64 SoC dtsi 里最高频的 mfd 用法**。

**带 `ranges` 的完整例子**：

```dts
/* arch/arm64/boot/dts/realtek/rtd139x.dtsi:64-71 */
crt: syscon@0 {
        compatible = "syscon", "simple-mfd";
        reg = <0x0 0x1000>;
        reg-io-width = <4>;
        #address-cells = <1>;
        #size-cells = <1>;
        ranges = <0x0 0x0 0x1000>;
};
```

⚠️ `ranges = <0x0 0x0 0x1000>` ⇒ 子节点写 `reg = <0x30 0x4>` 表示
"这块 syscon 里偏移 0x30 处的 4 字节"，OF 会加上父设备的物理基址。
⚠️ **`imx8mp.dtsi:451` 那个 `"fsl,sec-v4.0-mon","syscon", "simple-mfd"` 是同一个模式。**

### §3.4 ⚠️ 什么时候不该用 `simple-mfd`

| 情况 | 为什么不行 |
|---|---|
| 芯片挂在 I2C/SPI/USB 上 | ⚠️ **子节点没有可 `ioremap` 的地址**，实例化出来的 platform_device 拿不到寄存器 |
| 子设备清单要读寄存器才知道 | `mfd.txt:23-26` 明说不要用 |
| 子设备之间有供电/拆解顺序依赖 | ⚠️ `of_platform_populate()` 不提供 `level` 机制（04 §3） |
| 访问寄存器前要先解锁/上电 | ⚠️ **没有父驱动 probe 这一步可以做这件事** |

⚠️⚠️ **反过来说**：SoC 内部一段 MMIO、子块各有 `reg`、无前置动作
⇒ **`simple-mfd` 一行 C 代码都不用写**（05 §1）。
这就是为什么 `drivers/mfd/` 里 platform_driver 只有 48 个而 I2C 有 95 个（04 §1）。

---

## §4 形态三：`syscon` 的 DT 属性

### §4.1 `syscon.yaml` 管了什么

`Documentation/devicetree/bindings/mfd/syscon.yaml` 118 行，⚠️ **只定义了三条属性**：

| 属性 | 位置 | 约束 |
|---|---|---|
| `reg` | `:80-81` | `maxItems: 1` ⇒ ⚠️ **syscon 节点只能有一个寄存器窗口** |
| `reg-io-width` | `:83-87` | `enum: [1, 2, 4, 8]` 字节 |
| `hwlocks` | `:89-92` | `maxItems: 1`，指向一个 hwspinlock 提供者 |

⚠️ `compatible` 的规则有两支（`:34-78`）：
要么"白名单里的某个值 + `const: syscon`"（`:35-73`），
要么"含有 `syscon`，共 2–5 个"（`:75-78`）。
⚠️⚠️ **`:37-71` 那份白名单有 36 项**（allwinner / rockchip / samsung / hisilicon / mediatek …）
⇒ **新加一个纯 `"vendor,xxx", "syscon"` 的组合要往这个白名单里加一条**，否则 dtbs_check 报错。

⚠️ `:98-108` 那条 `allOf/if` 规则：**带 `simple-mfd` 时 compatible 必须有 3–5 个**
⇒ 官方在绑定里承认了 §3.3 那种三连写法。

### §4.2 `reg` 决定 regmap 的边界

```c
/* drivers/mfd/syscon.c:110 */
syscon_config.max_register = resource_size(&res) - reg_io_width;
```

⚠️⚠️ **`max_register` 完全由 DT 的 `reg` 长度算出来**，代码里没有默认值。
⇒ **DT 里 `reg` 长度写短了，超出部分的 `regmap_read()` 返回 `-EIO`**
（症状与"寄存器不存在"完全一样，07 §5）。
⚠️ 减的是 `reg_io_width` 而不是 4 ⇒ `reg-io-width = <8>` 时边界少 8 字节。

### §4.3 `reg-io-width` 一改动三处

```c
/* drivers/mfd/syscon.c:81-83 · :108-110 */
ret = of_property_read_u32(np, "reg-io-width", &reg_io_width);
if (ret)
        reg_io_width = 4;
...
syscon_config.reg_stride  = reg_io_width;
syscon_config.val_bits    = reg_io_width * 8;
syscon_config.max_register = resource_size(&res) - reg_io_width;
```

⚠️⚠️ **一个属性同时改了三个 regmap 配置项**（03 §6.2.1）：
步进、值宽度、上界。**不写就是 4 字节**（`:83`）。

⚠️ `:76-80` 那段注释说明了为什么不校验取值：
"regmap_init_mmio will return an error if values are invalid so there is no need
to check them here" ⇒ ⚠️ **非法值（比如 3）的报错来自 regmap，不是 syscon**
—— 日志里看不到 "syscon" 字样。

⚠️ arm64 上真正写这个属性的例子：
`arch/arm64/boot/dts/realtek/rtd139x.dtsi:67` `:76` `:85` `:94` `:103` `:166`
（Realtek 平台每个 syscon 都显式写 `<4>`，其实与默认值相同）。

### §4.4 ⚠️ endian 三兄弟：代码认，但 `syscon.yaml` 里没有

```c
/* drivers/mfd/syscon.c:68-74 */
/* Parse the device's DT node for an endianness specification */
if (of_property_read_bool(np, "big-endian"))
        syscon_config.val_format_endian = REGMAP_ENDIAN_BIG;
else if (of_property_read_bool(np, "little-endian"))
        syscon_config.val_format_endian = REGMAP_ENDIAN_LITTLE;
else if (of_property_read_bool(np, "native-endian"))
        syscon_config.val_format_endian = REGMAP_ENDIAN_NATIVE;
```

⚠️⚠️ **这三个属性 `syscon.yaml` 里一个都没列**，
它靠 `additionalProperties: true`（`:110`）放行。
定义在通用文档里：`Documentation/devicetree/bindings/common-properties.txt:12-23`：

| 属性 | 语义（原文归纳） |
|---|---|
| `big-endian` | 无条件用大端访问（`ioread32be`/`iowrite32be`），`:13-15` |
| `little-endian` | 无条件用小端访问（`readl`/`writel`），`:16-18` |
| `native-endian` | ⚠️ **跟内核二进制的字节序走，永不做字节交换**，`:19-23` |

⚠️ `common-properties.txt:25-30` 要求"支持这些属性的绑定应说明缺省行为"
⇒ ⚠️⚠️ **`syscon.yaml` 既没列这三条属性、也没说缺省行为**
—— 缺省是"三个都不写就不设 `val_format_endian`"，由 regmap 自己定（99 §6）。

⚠️ **arm64 上这三条基本用不到**：arm64 内核都是小端，
`native-endian` 与不写等价。⇒ **它们是为 BE 的 ppc/mips 平台留的**。

### §4.5 `hwlocks`

```c
/* drivers/mfd/syscon.c:85-101 —— 节选 */
ret = of_hwspin_lock_get_id(np, 0);
if (ret > 0 || (IS_ENABLED(CONFIG_HWSPINLOCK) && ret == 0)) {
        syscon_config.use_hwlock  = true;
        syscon_config.hwlock_id   = ret;
        syscon_config.hwlock_mode = HWLOCK_IRQSTATE;
} else if (ret < 0) {
        switch (ret) {
        case -ENOENT:
                /* Ignore missing hwlock, it's optional. */
                break;
        default:
                pr_err("Failed to retrieve valid hwlock: %d\n", ret);
                fallthrough;
        case -EPROBE_DEFER:
                goto err_regmap;
        }
}
```

⚠️ 语义：**这块寄存器要与另一个处理器（协处理器/固件）共享**，
所以每次访问都要先拿硬件自旋锁。`HWLOCK_IRQSTATE`（`:89`）表示加锁时同时关本地中断。

⚠️⚠️ **三条错误处理路径必须分清**（03 §6.2）：

| `of_hwspin_lock_get_id()` 返回 | 行为 |
|---|---|
| `> 0`，或 `CONFIG_HWSPINLOCK=y` 时的 `0` | ⚠️ 挂上 hwlock（`:86-89`） |
| `-ENOENT` | ⚠️ **属性没写 ⇒ 视为可选，正常继续**（`:92-94`） |
| `-EPROBE_DEFER` | ⚠️ **整个 syscon 注册失败并回滚**（`:98-99`） |
| 其它负值 | ⚠️ 打 `pr_err` 然后 `fallthrough` 到上一行 ⇒ **同样失败** |

⚠️⚠️ **`-EPROBE_DEFER` 那一支很容易咬人**：syscon 的 regmap 是
**首次 lookup 时才创建的**（03 §6.1）⇒ 如果第一个 lookup 者在 hwspinlock 驱动之前 probe，
它拿到的是 `-EPROBE_DEFER`，得自己回去重试。**这条链路的 defer 会从 syscon 传导到消费者。**

⚠️ arm64 上 `hwlocks` 的典型用法是高通的 `qcom,smem`：
`arch/arm64/boot/dts/qcom/sc7280.dtsi:647` · `sm6125.dtsi:361` ·
`ipq6018.dtsi:172` · `msm8996.dtsi:538`（都是 `hwlocks = <&tcsr_mutex 3>;`）。

---

## §5 消费者侧：phandle 属性怎么命名

**消费者的 DT 写法**（05 §4）：

```dts
my-device {
        compatible = "vendor,my-device";
        vendor,foo-syscon = <&sysctrl>;
};
```

驱动侧：`syscon_regmap_lookup_by_phandle(np, "vendor,foo-syscon")`。

⚠️⚠️ **属性名完全由你的绑定文档定义，syscon 不做任何约定。** 树里实际的命名：

| 属性名 | 使用者 |
|---|---|
| `"syscon"` | `drivers/remoteproc/imx_rproc.c:726` ⇒ ⚠️ **无厂商前缀，不推荐新代码模仿** |
| `"samsung,sysreg"` | `drivers/soc/samsung/exynos-usi.c:189` |
| `"st,syscfg"` | `drivers/remoteproc/st_remoteproc.c:321` |
| `"ti,bootreg"` | `drivers/remoteproc/omap_remoteproc.c:1150` |
| `"ti,syscon-dev"` | `drivers/remoteproc/keystone_remoteproc.c:346` |
| `"fsl,dsp-ctrl"` | `drivers/remoteproc/imx_dsp_rproc.c:1035` |

⚠️ **新绑定用 `vendor,xxx-syscon` 形式** —— 带厂商前缀是 DT 的通用规范。

⚠️ 需要在 phandle 后面跟参数（比如"这个 syscon 里的第 3 个寄存器"）时用
`syscon_regmap_lookup_by_phandle_args()`：

```dts
vendor,foo-syscon = <&sysctrl 0x30 0x4>;
```

⚠️⚠️ **但它不检查 `arg_count` 与 `out_args` 数组容量是否匹配**
（`drivers/mfd/syscon.c:277-303`，01 §4.1）⇒ **传错个数会写越界。**

---

## §6 arm64 上的普查数字

| 项 | 数字 | 口径 |
|---|---|---|
| 用 `simple-mfd` 的 dts/dtsi | **62** | `grep -rl simple-mfd arch/arm64/boot/dts` |
| 用 `"syscon"` compatible 的 dts/dtsi | **112** | 基线表，`grep -rl` 文件数 |
| `syscon_regmap_lookup_by_phandle()` 的调用文件 | **198** | 全树 `.c`（04 §5） |
| `syscon_node_to_regmap()` 的调用文件 | **164** | 同上 |
| 写了 `reg-io-width` 的 arm64 dts | ⚠️ **极少**，`realtek/rtd139x.dtsi` 等 | 默认值 4 已够用 |
| 写了 `hwlocks` 的 arm64 dts | ⚠️ 基本只有 qcom 平台 | `qcom,smem` 形态 |

⚠️⚠️ **对比值得记**：`simple-mfd` 62 个文件 vs `"syscon"` 112 个文件
⇒ **约一半的 syscon 节点不带 `simple-mfd`**（只被当寄存器块读写，没有子设备）。

---

## §7 DT 陷阱清单

| # | 陷阱 | 症状 | 依据 |
|---|---|---|---|
| 1 | ⚠️⚠️ 子节点忘删 `status = "disabled"` | **子设备完全不出现，日志一句话都没有** | §2.3 |
| 2 | ⚠️ 以为 mfd 子节点要写 `reg` / `interrupts` | 写了也没人读（除 `use_of_reg` 情况） | §2.2 |
| 3 | ⚠️ `of_reg` 写成经 `ranges` 换算后的地址 | 匹配不上 ⇒ `pr_warn "Failed to locate of_node"`，子设备无 `of_node` | §2.5 |
| 4 | ⚠️ I2C/SPI 芯片的父节点上写 `simple-mfd` | 子节点被实例化成拿不到寄存器的空设备 | §3.4 |
| 5 | ⚠️ 用了 `ranges` 忘写 `#address-cells`/`#size-cells` | dtc 报错或地址换算错 | §3.2 |
| 6 | ⚠️⚠️ syscon 的 `reg` 长度写短 | `regmap_read()` 返回 `-EIO`，像"寄存器不存在" | §4.2 |
| 7 | ⚠️ `reg-io-width` 写非法值（如 3） | 报错来自 regmap，日志里没有 "syscon" 字样 | §4.3 |
| 8 | ⚠️ 指望 `syscon.yaml` 校验 `big-endian` | 它没列这三条属性，靠 `additionalProperties: true` 放行 | §4.4 |
| 9 | ⚠️⚠️ syscon 引用了还没 probe 的 hwspinlock | `-EPROBE_DEFER` 从 syscon 传导到消费者 | §4.5 |
| 10 | ⚠️ 新 `"vendor,xxx", "syscon"` 组合没加进白名单 | `dtbs_check` 报错 | §4.1 |
| 11 | ⚠️ `_by_phandle_args` 的参数个数传错 | 写越界，无任何检查 | §5 |

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| DT 匹配的三个 cell 字段 | 02 §2.4 |
| 核心怎么按 `of_compatible` 找子节点 | 03 §2.4、§2.5 |
| `use_of_reg` 的唯一使用者长什么样 | 04 §4 |
| 我要写父驱动 / 消费 syscon | 05-usage.md |
| 子设备没出现，现场怎么查 | 07-debug.md |
| 为什么 `simple-mfd` 不在 mfd 里 | 08-feynman.md |
| 本篇数字与文档缺口的取证 | 99-evidence.md |
