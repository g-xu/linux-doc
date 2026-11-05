---
title: 设备树
---

# SPI 设备树配置

> 本篇只讲 arm64 + Device Tree。ACPI 路径不在范围内（见 `99-evidence.md` §2.7）。
> 所有例子取自本树 `arch/arm64/boot/dts/` 的真实节点，可以直接 diff 核对。

---

## §0 按任务找章节

| 你要做的事 | 去哪 |
|----------|------|
| 我要新加一个 SPI 芯片到板子上 | §1.1 总表 → §3 逐条 → §5.1 完整例子 |
| **哪些属性核心真的会读？** | **§1.1**（一张表说完） |
| 片选电平反了 | §4（极性专章） |
| 属性写了但没生效 | §1.2 三方分工 → §6 定位 |
| 控制器节点该写什么 | §2 |
| 用 GPIO 当片选 | §2.3 + §4 |
| QSPI / flash 怎么配 | §5.2 |
| spidev 怎么配 | §5.3 |
| 报了个错不知道什么意思 | §8 错误消息映射表 |
| 什么都没报错但设备不出现 | §7.1 总线级症状 |

---

## §1 谁消费什么

### §1.1 核心真正会读的属性（**只有 9 个**）

**这是本篇最重要的一张表。** 绑定文档（`Documentation/devicetree/bindings/spi/`）
里列的属性远多于 9 个，但**SPI 核心自己只读这 9 个**，其余全部由具体控制器
驱动读，或者根本没人读。

| 属性 | 写在哪个节点 | 类型 | 消费代码 | 效果 |
|-----|-----------|------|---------|------|
| `spi-cpha` | **设备**（子节点） | flag | `drivers/spi/spi.c:2247` | `spi->mode |= SPI_CPHA` |
| `spi-cpol` | **设备** | flag | `:2249` | `spi->mode |= SPI_CPOL` |
| `spi-3wire` | **设备** | flag | `:2251` | `spi->mode |= SPI_3WIRE` |
| `spi-lsb-first` | **设备** | flag | `:2253` | `spi->mode |= SPI_LSB_FIRST` |
| `spi-cs-high` | **设备** | flag | `:2255` | `spi->mode |= SPI_CS_HIGH` |
| `spi-tx-bus-width` | **设备** | u32 | `:2259` | 1/2/4/8 → `SPI_TX_DUAL/QUAD/OCTAL`；**0 → `SPI_NO_TX`** |
| `spi-rx-bus-width` | **设备** | u32 | `:2283` | 同上，`SPI_RX_*` / `SPI_NO_RX` |
| `reg` | **设备** | u32 | `:2317` | 片选号。**缺了就 probe 失败** |
| `spi-max-frequency` | **设备** | u32 | `:2326` | `spi->max_speed_hz` |

**九个里面八个写在子节点上，只有 `reg` 是必需的。**

另外**两个不带引号也由核心消费**的：

| 属性 | 写在哪 | 消费方式 | 说明 |
|-----|-------|---------|------|
| `cs-gpios` | **控制器** | gpiolib 的 `gpiod_count(dev, "cs")` `drivers/spi/spi.c:2993` + `devm_gpiod_get_index_optional(dev, "cs", i, ...)` `:3017` | 字符串 `"cs-gpios"` **在 `spi.c` 里一次都没出现**——是 gpiolib 从 `"cs"` 这个 con_id 拼出来的 |
| `compatible` | 设备 | `of_modalias_node()` → `spi->modalias` | 砍掉 vendor 前缀，见 `03-internals.md` §A4.2 |

### §1.2 三方分工

| 谁 | 读什么 | 特点 |
|---|-------|-----|
| **通用 DT / 驱动模型层** | `compatible` / `reg` / `status` / `#address-cells` / `#size-cells` / `clocks` / `interrupts` / `pinctrl-*` / `dmas` | 和 SPI 无关，platform 层已处理 |
| **SPI 核心** | §1.1 的 9 + 2 个 | **就这些，没有更多** |
| **具体控制器驱动** | `num-cs` / `spi-slave` / `rx-sample-delay-ns` / `fifo-depth` / 各家私有属性 | **每个驱动自己读**，行为不统一 |

两个**最容易误判成核心属性**的：

**`num-cs`——不是核心属性。** 全树 **21 个控制器驱动各自读一遍**
（`grep -rn '"num-cs"' drivers/spi/`，21 个文件 21 处）：

```
spi-armada-3700.c:830    spi-bcm63xx.c:509        spi-bcm63xx-hsspi.c:415
spi-bcm-qspi.c:1538      spi-cadence.c:550        spi-cadence-quadspi.c:1450
spi-davinci.c:832        spi-dw-mmio.c:319        spi-hisi-kunpeng.c:484
spi-imx.c:1746           spi-ingenic.c:423        spi-lantiq-ssc.c:958
spi-microchip-core.c:525 spi-qup.c:1039           spi-rockchip.c:867
spi-rspi.c:1242          spi-s3c64xx.c:1065       spi-sh-msiof.c:1125
spi-ti-qspi.c:782        spi-zynq-qspi.c:703      spi-zynqmp-gqspi.c:1183
```

**读法还不统一**：`spi-hisi-kunpeng.c:484` 用的是
`device_property_read_u16()`（16 位！），`spi-dw-mmio.c:319` 和
`spi-ingenic.c:423` 用 `device_property_read_u32()`（走 fwnode，ACPI 也能读），
其余用 `of_property_read_u32()`（只认 DT）。**有的检查返回值，有的不检查。**

**后果：控制器驱动没读它，写了也无效，而且没有任何警告。**
核心用的是 `ctlr->num_chipselect`（驱动在 probe 里硬编码或从 `num-cs` 读），
`spi_get_gpio_descs()` 会把它拉高到 `max(cs-gpios 个数, num_chipselect)`
（`drivers/spi/spi.c:3001`）。

**`spi-slave`——也不是核心属性。** 8 个驱动自己读
（`spi-rockchip.c:760`、`spi-imx.c:1711`、`spi-fsl-dspi.c:1340`、
`spi-fsl-lpspi.c:828`、`spi-omap2-mcspi.c:1432`、`spi-sh-msiof.c:1120`、
`spi-pxa2xx.c:1517`、`spi-sunplus-sp7021.c:411`）。核心只看
`spi_controller_is_slave(ctlr)`，那是驱动用 `spi_alloc_slave()` 决定的。

### §1.3 找不到消费者的属性（**死字**）

绑定文档列了、但在本树里**没有 SPI 层消费者**的属性：

| 属性 | 绑定文档位置 | 实际消费者 | 结论 |
|-----|-----------|----------|------|
| `spi-rx-delay-us` | `spi-peripheral-props.yaml:55` | **只有 `drivers/input/rmi4/rmi_spi.c:338`** | **对 SPI 核心是死字**。只有 RMI4 触摸屏驱动自己读，而且是它自己的私有语义 |
| `spi-tx-delay-us` | `spi-peripheral-props.yaml:72` | **只有 `drivers/input/rmi4/rmi_spi.c:344`** | 同上 |
| `rx-sample-delay-ns` | `spi-peripheral-props.yaml:59` | `spi-rockchip.c:825`、`spi-dw-core.c:798`/`:944` | 不是死字，但**只有这两家控制器**读 |
| `stacked-memories` / `parallel-memories` | `spi-peripheral-props.yaml:76`/`:88` | `drivers/mtd/spi-nor/` 侧 | 属于 flash 层，不是 SPI 层 |

**`spi-rx-delay-us` / `spi-tx-delay-us` 是本篇最值得记住的陷阱**：
它们出现在通用的 `spi-peripheral-props.yaml` 里，看起来像是通用属性，
`dtbs_check` 也不会报错，但**除了 RMI4 触摸屏没有任何驱动读它们**。
在别的设备节点上写这两个属性 = 完全没有效果、零警告。

**验证命令**（`99-evidence.md` §3.6 登记）：

```bash
grep -rn '"spi-rx-delay-us"\|"spi-tx-delay-us"' --include=*.c .
# 只有 drivers/input/rmi4/rmi_spi.c 两行
```

还有三个**在 6.1 里完全不存在**的属性——`spi_delay` 的三段片选时序
（`cs_setup` / `cs_hold` / `cs_inactive`，`02-datastructures.md` §2.1）
**没有 DT 绑定**：

```bash
grep -rn "spi-cs-setup-delay-ns\|spi-cs-hold-delay-ns\|spi-cs-inactive-delay-ns" .
# 全树零结果（含 Documentation 和所有 .c）
```

**所以这三段时序在 6.1 里只能由控制器驱动代码设置，DT 里配不了。**
网上能搜到这些属性名是因为它们在更新的内核里才加入。

---

## §2 控制器节点

### §2.1 一个真实例子

`arch/arm64/boot/dts/rockchip/rk3399.dtsi:742-755`：

```dts
spi1: spi@ff1d0000 {
        compatible = "rockchip,rk3399-spi", "rockchip,rk3066-spi";
        reg = <0x0 0xff1d0000 0x0 0x1000>;
        clocks = <&cru SCLK_SPI1>, <&cru PCLK_SPI1>;
        clock-names = "spiclk", "apb_pclk";
        dmas = <&dmac_peri 12>, <&dmac_peri 13>;
        dma-names = "tx", "rx";
        interrupts = <GIC_SPI 50 IRQ_TYPE_LEVEL_HIGH 0>;
        pinctrl-names = "default";
        pinctrl-0 = <&spi1_clk &spi1_cs0 &spi1_rx &spi1_tx>;
        #address-cells = <1>;
        #size-cells = <0>;
        status = "disabled";
};
```

### §2.2 必需和可选

| 属性 | 必需 | 说明 |
|-----|-----|------|
| `compatible` | ✓ | 决定哪个控制器驱动来 probe |
| `reg` | ✓ | 寄存器地址 |
| `#address-cells = <1>` | ✓ | **必须是 1**——子节点的 `reg` 是片选号 |
| `#size-cells = <0>` | ✓ | **必须是 0**——片选号没有 size |
| `clocks` / `clock-names` | 几乎总是 | 具体名字由驱动定 |
| `interrupts` | 通常 | PIO 中断驱动的控制器需要 |
| `dmas` / `dma-names` | 可选 | 名字通常是 `"tx"` / `"rx"` |
| `pinctrl-*` | 通常 | 引脚复用 |
| `num-cs` | **看驱动** | 见 §1.2——驱动不读就无效 |
| `cs-gpios` | 可选 | 见 §2.3 |
| `status` | — | `.dtsi` 里通常 `"disabled"`，板级 `.dts` 里改 `"okay"` |

**节点名必须匹配 `^spi(@.*|-[0-9a-f])*$`**
（`Documentation/devicetree/bindings/spi/spi-controller.yaml:20`）。
叫 `qspi@...` 或 `spictrl@...` 会 `dtbs_check` 报错。

**`#address-cells` 写成 2 是一个隐蔽错误**：子节点的 `reg` 会被解析成两个
cell，`of_property_read_u32(nc, "reg", &value)`（`drivers/spi/spi.c:2317`）
读到的是第一个 cell——**碰巧能工作，但语义是错的**，`dtbs_check` 会报。

### §2.3 `cs-gpios`：混用 native 和 GPIO 片选

`Documentation/devicetree/bindings/spi/spi-controller.yaml:28-70` 给了权威说明。
核心机制：

```dts
/* 控制器有 4 条 native CS，但只想用第 1 条 native + 3 条 GPIO */
cs-gpios = <&gpio1 0 0>, <0>, <&gpio1 1 0>, <&gpio1 2 0>;
                            ↑ <0> 表示「这一位用 native」
```

映射结果：

| 子节点 `reg` | 用什么 |
|------------|-------|
| 0 | `&gpio1 0` |
| 1 | **native CS1** |
| 2 | `&gpio1 1` |
| 3 | `&gpio1 2` |

代码依据（`spi_get_gpio_descs()` `drivers/spi/spi.c:2985`）：

| 步骤 | 行 | 说明 |
|-----|---|------|
| 数一共几个 | 2993 | `gpiod_count(dev, "cs")`。返 `-ENOENT` 是正常的（没配 `cs-gpios`） |
| 拉高 `num_chipselect` | 3001 | `max(nb, ctlr->num_chipselect)` |
| 逐个取 | 3017 | `devm_gpiod_get_index_optional(..., GPIOD_OUT_LOW)`。**`<0>` 那位返 NULL** |
| NULL 那位记为 native | 3042 | `native_cs_mask |= BIT(i)` |
| 算出未用的 native | 3044 | `ctlr->unused_native_cs = ffs(~native_cs_mask) - 1` |

**`GPIOD_OUT_LOW` 的含义是「未激活」，不是「输出低电平」**。
`:3009-3014` 的注释说得很明白：

> "Most chipselects are active low, the inverted semantics are handled by
> special quirks in gpiolib, so initializing them GPIOD_OUT_LOW here means
> 'unasserted', in most cases this will drive the physical line high."

**`SPI_MASTER_GPIO_SS` 的额外约束**（`:3046-3050`）：同时用 GPIO 片选和
native 片选拉线时，必须有一条**未被占用的** native CS 可以拿来当「无害的
垃圾桶」。没有就是 `dev_err` + `-EINVAL`：

```
No unused native chip select available
```

### §2.4 一个 GPIO 片选的真实例子

`arch/arm64/boot/dts/microchip/sparx5_pcb125.dts:42-70`（`&spi0` 块的完整范围）：

```dts
&spi0 {
        status = "okay";
        spi@0 {
                compatible = "spi-mux";
                ...
        };
};
```

`arch/arm64/boot/dts/rockchip/rk3399-gru.dtsi:576-589` 是一个更典型的
带 `spi-max-frequency` 的设备节点例子。

---

## §3 设备节点属性逐条

### §3.1 `reg`：唯一的必需属性

```dts
flash@0 {
        compatible = "jedec,spi-nor";
        reg = <0>;                   /* 片选 0 */
        spi-max-frequency = <50000000>;
};
```

**三条硬规则**：

1. **缺了就 probe 失败**，有明确日志（`drivers/spi/spi.c:2319` 一带）：
   ```
   /spi@ff1d0000/flash has no valid 'reg' property (-22)
   ```
2. **必须 < `ctlr->num_chipselect`**，否则 `spi_add_device()` 拒绝
   （`:705-707`）：
   ```
   cs0 >= max 1
   ```
3. **不能和同总线上别的设备重复**，否则 `-EBUSY`
   （`spi_dev_check()` `:620`，由 `__spi_add_device()` 在 `:648` 用
   `bus_for_each_dev()` 扫全总线调用，报错在 `:650`）：
   ```
   chipselect 0 already in use
   ```
   注意扫的是**整条 `spi_bus_type`**（`:648`）而不是单个控制器，
   靠 `spi_dev_check()` 里 `new_spi->controller == spi->controller`
   的判断（`:625` 一带）过滤——所以不同控制器上的相同片选号不冲突。

节点名的 unit-address 应该等于 `reg` 的值（`flash@0` + `reg = <0>`），
但**核心只读 `reg`，不看节点名**。写成 `flash@1` + `reg = <0>` 能工作，
只有 `dtbs_check` 会报——这是一个容易长期潜伏的不一致。

### §3.2 `spi-max-frequency`：静默下夹

```dts
spi-max-frequency = <50000000>;
```

**三条反直觉的行为**：

1. **不写会怎样？** `spi->max_speed_hz` 保持 0，然后 `spi_setup()` 里
   静默改成 `ctlr->max_speed_hz`——**跑控制器的最高速**。
   这通常不是你想要的（外设可能撑不住）。**总是显式写。**
2. **写得太高会怎样？** `spi_setup()` **静默下夹到 `ctlr->max_speed_hz`**，
   **没有任何警告**（`03-internals.md` §A6）。所以写 100MHz 而控制器只能
   50MHz，你得到 50MHz 而不知道。
3. **实际速率通常更低**。控制器只能做时钟分频，50MHz 的父时钟除以 3
   得 16.67MHz。真值在 `xfer->effective_speed_hz`
   （驱动回填的话），或者 tracepoint 里。

### §3.3 `spi-cpol` / `spi-cpha`：模式的两个位

```
mode = (cpol << 1) | cpha

SPI_MODE_0  = 0        不写任何一个
SPI_MODE_1  = CPHA     只写 spi-cpha
SPI_MODE_2  = CPOL     只写 spi-cpol
SPI_MODE_3  = 两个都写
```

**芯片手册通常直接说「SPI Mode 3」，你要自己翻译成两个 flag**。
这是配错模式最常见的原因——手册的 mode 号和 DT 的两个 flag 之间
没有直接对应的属性名。

消费点：`drivers/spi/spi.c:2247`（cpha）/ `:2249`（cpol）。

症状：**模式配错通常表现为「读到的数据整体错位一个 bit」或「全 0 / 全 1」**，
而不是完全没反应。用示波器看时钟第一个边沿最快，没示波器就四种都试一遍。

### §3.4 `spi-tx-bus-width` / `spi-rx-bus-width`

```dts
spi-tx-bus-width = <4>;      /* QUAD 写 */
spi-rx-bus-width = <4>;      /* QUAD 读 */
```

合法值和效果（`drivers/spi/spi.c:2259` / `:2283`）：

| 值 | tx 效果 | rx 效果 |
|---|--------|--------|
| **0** | `SPI_NO_TX`——**这个方向不接线** | `SPI_NO_RX` |
| 1 | 单线（默认，不设任何位） | 同 |
| 2 | `SPI_TX_DUAL` | `SPI_RX_DUAL` |
| 4 | `SPI_TX_QUAD` | `SPI_RX_QUAD` |
| 8 | `SPI_TX_OCTAL` | `SPI_RX_OCTAL` |
| **其它** | **只 `dev_warn` 然后继续**，当成单线 | 同 |

**两个陷阱**：

1. **非法值只警告不失败**（`:2277-2280` / `:2301-2304`）：
   ```
   spi-tx-bus-width 3 not supported
   ```
   是 `dev_warn`——**设备照样会 probe 成功，只是跑单线**。
   写了 `<3>` 想要三线的人会以为生效了。
2. **`<0>` 是有意义的值，不是「没配」**。它设 `SPI_NO_TX` / `SPI_NO_RX`
   （`include/linux/spi/spi.h:184` / `:185`），表示物理上这个方向的线
   根本没接。只写不读的显示屏可以写 `spi-rx-bus-width = <0>`。

arm64 上这两个属性很常用：`grep -rl "spi-rx-bus-width" arch/arm64/boot/dts/`
返回 **77 个文件**——基本都是 QSPI flash 节点。

### §3.5 `spi-3wire`

```dts
spi-3wire;
```

MOSI 和 MISO 共用一根线，分时收发。`drivers/spi/spi.c:2251`。

**两个连带效果**：

1. **哑缓冲不会被创建**（`spi_map_msg()` 里 `!(msg->spi->mode & SPI_3WIRE)`
   的条件，`03-internals.md` §B3.1）。合理——三线本来就不同时收发。
2. **控制器必须在 `mode_bits` 里声明 `SPI_3WIRE`**，否则 `spi_setup()`
   返 `-EINVAL`，设备 probe 失败。全树支持 `SPI_3WIRE` 的控制器不多。

### §3.6 `spi-lsb-first`

```dts
spi-lsb-first;
```

`drivers/spi/spi.c:2253`。**同样需要控制器声明 `SPI_LSB_FIRST`**。
很多控制器不支持——这时只能在驱动里手工翻转每个字节（`bitrev8()`）。

### §3.7 `spi-cs-high`

见 §4 专章。

---

## §4 片选极性专章（本篇最容易踩的地方）

### §4.1 结论先说

**在 DT 系统里，用 GPIO 当片选时 `spi-cs-high` 完全没有作用。**
极性 100% 由 `cs-gpios` 里的第二个 flag（`GPIO_ACTIVE_LOW` / `GPIO_ACTIVE_HIGH`）
决定。

| 你用什么片选 | 谁决定极性 | `spi-cs-high` 有用吗 |
|-----------|----------|-------------------|
| **native CS**（控制器自己的引脚） | `spi-cs-high` | ✓ 有用，唯一手段 |
| **GPIO CS（DT）** | **`cs-gpios` 的 ACTIVE_LOW/HIGH** | ✗ **无效，被忽略** |
| GPIO CS（ACPI） | `spi-cs-high` | ✓ 有用（不在本篇范围） |
| GPIO CS + `SPI_MASTER_GPIO_SS` 的那条 native 线 | `spi-cs-high` | ✓ 只影响 native 那条 |

### §4.2 代码怎么走的

`spi_set_cs()` `drivers/spi/spi.c:974`。关键是**它有两个变量**：

```c
static void spi_set_cs(struct spi_device *spi, bool enable, bool force)
{
        bool activate = enable;                    /* :976 —— 原始意图，之后永不修改 */
        ...
        if (spi->mode & SPI_CS_HIGH)               /* :996 */
                enable = !enable;                  /* :997 —— 只翻转 enable */
```

然后三条出口用的变量**不一样**：

| 出口 | 行 | 用哪个变量 | 结果 |
|-----|---|----------|------|
| GPIO CS，**非 ACPI**（= DT） | **1015** | **`activate`** | **`SPI_CS_HIGH` 被绕过** |
| GPIO CS，ACPI | 1012 | `!enable` | `SPI_CS_HIGH` 生效 |
| `SPI_MASTER_GPIO_SS` 的额外 native 线 | 1020 | `!enable` | 生效 |
| 纯 native CS | 1022 | `!enable` | 生效 |

DT 那一行自带注释，把意图说得很清楚（`:1014-1015`）：

```c
                        else
                                /* Polarity handled by GPIO library */
                                gpiod_set_value_cansleep(spi_get_csgpiod(spi, 0), activate);
```

**`activate` 是「我要选中这个片」这个逻辑意图，不是电平。**
gpiolib 拿到 `activate=1` 之后，按该 GPIO 描述符登记的 `ACTIVE_LOW`/`ACTIVE_HIGH`
自己决定往引脚上写 1 还是 0。所以极性只有一层，不是两层相乘。

### §4.3 代码与官方绑定文档冲突

`Documentation/devicetree/bindings/spi/spi-controller.yaml:54-61` 给了一张权威真值表，
`:63-70` 是它的两条 Notes。把它和代码逐行对一遍：

| 设备节点 | cs-gpio flag | 文档说激活电平 | **代码实际** | 一致？ |
|---------|-------------|-------------|------------|-------|
| `spi-cs-high` | 无（native） | H | H | ✓ |
| 无 | 无（native） | L | L | ✓ |
| `spi-cs-high` | ACTIVE_HIGH | H | H | ✓ |
| 无 | ACTIVE_HIGH | **L**（Note 1） | **H** | ✗ **相反** |
| `spi-cs-high` | ACTIVE_LOW | **H**（Note 2） | **L** | ✗ **相反** |
| 无 | ACTIVE_LOW | L | L | ✓ |

**不一致的两行，恰好就是文档标了 Note 1 / Note 2 的两行**——也就是文档
自己认为"应该打极性反转警告"的两种组合。

而那两条 Note 承诺的警告**在代码里根本不存在**：

```bash
grep -rni "polarity" drivers/spi/*.c | grep -i "warn\|dev_err\|dev_info"
# 空
```

**解释**：6.1 的实现没有去"组合"两个极性来源，而是**在 DT 路径上直接让
gpiolib 独占极性**（`:1015` 用 `activate`）。既然没有组合，也就没有反转
可以警告——文档描述的是一个从未落地的设计。

**实践规则（记住这一条就够）**：

```
用 GPIO 片选  →  只写 cs-gpios 的 ACTIVE_LOW / ACTIVE_HIGH，不要写 spi-cs-high
用 native 片选 →  只写 spi-cs-high，没有别的选择
```

### §4.4 决策树

```
片选电平不对（示波器上 CS 反了 / 设备完全不响应）
  │
  ├─ 控制器节点里有 cs-gpios 吗？
  │    │
  │    ├─ 有 ── 这一位是 <0> 吗？
  │    │         ├─ 是 <0> → 走的是 native，见下面「无 cs-gpios」分支
  │    │         └─ 是真 phandle → 改 cs-gpios 第二个 flag
  │    │                            （GPIO_ACTIVE_LOW ↔ GPIO_ACTIVE_HIGH）
  │    │                            加 / 删 spi-cs-high 不会有任何变化
  │    │
  │    └─ 无 ── 改设备节点的 spi-cs-high（加上或删掉）
  │
  └─ 改完还是不对？
       ├─ pinmux 层还有一次反转 → 查 pinctrl 的 output-low / bias 设置
       ├─ 板子上有反相器 / 电平转换芯片 → 电路图
       └─ 控制器 mode_bits 里没有 SPI_CS_HIGH → spi_setup() 会返 -EINVAL，
          probe 就失败了，看 dmesg（不会是"电平不对"这个症状）
```

注意 `use_gpio_descriptors` 的控制器会**自动获得** `SPI_CS_HIGH` 能力
（`drivers/spi/spi.c:3174`，注释在 `:3170-3173`），所以用 GPIO 片选时
`spi_setup()` 永远不会因为 `SPI_CS_HIGH` 而拒绝——**它接受了这个位，
只是在 DT 路径上不用它**。这就是为什么这个坑没有任何报错。

### §4.5 一个副作用：改 mode 会强制重新设置片选

`spi_set_cs()` 的早退条件（`:983-985`）除了比片选号，**还比
`last_cs_mode_high`**：

```c
	    (spi->controller->last_cs_mode_high == (spi->mode & SPI_CS_HIGH)))
		return;
```

所以在两次传输之间调 `spi_setup()` 改了 `SPI_CS_HIGH`，下一次
`spi_set_cs()` 一定会真的调进驱动，不会被早退吃掉。
`:3691` 一带的注释专门点出这一点：`SPI_CS_HIGH` 是**立即生效**的，
其它 mode 位要等下一次传输。

---

## §5 完整例子（全部 in-tree，可 diff）

### §5.1 最常见形态：native 片选 + 一个 SPI NOR

`arch/arm64/boot/dts/rockchip/rk3399-gru.dtsi:576-589`：

```dts
&spi1 {
	status = "okay";

	pinctrl-names = "default", "sleep";
	pinctrl-1 = <&spi1_sleep>;

	flash@0 {
		compatible = "jedec,spi-nor";
		reg = <0>;

		/* May run faster once verified. */
		spi-max-frequency = <10000000>;
	};
};
```

三点值得学：
1. **板级 `.dts`/`.dtsi` 只写 `status = "okay"` + 子节点**，寄存器/时钟/中断
   都在 SoC 的 `.dtsi` 里（§2.1）。
2. `#address-cells` / `#size-cells` **不用重复写**——SoC `.dtsi` 已经有了。
3. 只写了 `reg` 和 `spi-max-frequency`——`SPI_MODE_0` 是默认，
   不需要写 `spi-cpol`/`spi-cpha`。

### §5.2 QSPI / 四线读

`arch/arm64/boot/dts/rockchip/rk3566-quartz64-b.dts:636-650`：

```dts
&sfc {
	pinctrl-0 = <&fspi_pins>;
	pinctrl-names = "default";
	#address-cells = <1>;
	#size-cells = <0>;
	status = "okay";

	flash@0 {
		compatible = "jedec,spi-nor";
		reg = <0>;
		spi-max-frequency = <24000000>;
		spi-rx-bus-width = <4>;
		spi-tx-bus-width = <1>;
	};
};
```

四点：
1. **读四线、写单线**是 SPI NOR 的常态——`tx` 写 `<1>` 而不是 `<4>`，
   因为大多数 flash 的写命令只支持单线（或者不值得用四线）。
2. **`#address-cells`/`#size-cells` 在这里写了**，因为 `&sfc` 的 SoC
   `.dtsi` 节点没写（不同 SoC 习惯不同，照抄不如自己确认）。
3. **这条路径走的是 spi-mem**（`04-extensions.md` §1），不是普通的
   message pump——`spi-rx-bus-width` 最终变成 `spi->mode` 的
   `SPI_RX_QUAD` 位，由 `spi_mem_default_supports_op()` 检查。
4. **不要在这个节点上加 `cs-gpios`**。`spi_mem_exec_op()` 里有
   `!mem->spi->cs_gpiod` 的守卫（`drivers/spi/spi-mem.c:328`）——
   有 GPIO 片选就**静默退回**到逐段模拟，硬件加速全部失效、零日志。
   这是全篇最贵的一个静默陷阱（`04-extensions.md` §1.5）。

### §5.3 spidev（用户态直接访问）

`arch/arm64/boot/dts/freescale/imx8mm-mx8menlo.dts:72-87`：

```dts
&ecspi2 {
	pinctrl-0 = <&pinctrl_ecspi2 &pinctrl_gpio1>;
	cs-gpios = <&gpio5 13 GPIO_ACTIVE_LOW>, <&gpio3 4 GPIO_ACTIVE_LOW>;
	status = "okay";

	spidev@0 {
		compatible = "menlo,m53cpld";
		reg = <0>;
		spi-max-frequency = <25000000>;
	};

	spidev@1 {
		compatible = "menlo,m53cpld";
		reg = <1>;
		spi-max-frequency = <25000000>;
	};
};
```

**`compatible` 不能写 `"spidev"`**。`spidev` 有一张 11 项白名单
（`drivers/spi/spidev.c:723-736`），只有名单上的 compatible 才能绑上去；
写别的会被 `spidev_of_check()`（`:714`）拒绝，报：

```
spidev listed directly in DT is not supported
```

这里用的 `menlo,m53cpld` 就在名单上（`:729`）。
实践中大家常借用 `rohm,dh2228fv`（`:732`）——它本来是个 DAC 的
compatible，但已经变成事实上的"通用 spidev"标记。理由和白名单机制见
`04-extensions.md` §2.7。

另外注意这个例子用了 GPIO 片选、`GPIO_ACTIVE_LOW`、**没有写
`spi-cs-high`**——正是 §4.3 表里"一致"的那一行，也是应该照抄的写法。

### §5.4 纯 GPIO 模拟的 SPI 总线

`arch/arm64/boot/dts/rockchip/rk3566-anbernic-rg503.dts:20-31`：

```dts
	gpio_spi: spi {
		compatible = "spi-gpio";
		pinctrl-names = "default";
		pinctrl-0 = <&spi_pins>;
		#address-cells = <1>;
		#size-cells = <0>;

		sck-gpios = <&gpio4 RK_PB3 GPIO_ACTIVE_HIGH>;
		mosi-gpios = <&gpio4 RK_PB0 GPIO_ACTIVE_HIGH>;
		cs-gpios = <&gpio4 RK_PA7 GPIO_ACTIVE_HIGH>;
		num-chipselects = <0>;
	};
```

四点：
1. **节点不在任何控制器下面**，是根级节点——`spi-gpio` 是个 platform
   驱动，自己造一个 SPI 控制器出来（走 `spi_bitbang`，
   `04-extensions.md` §3）。
2. **没有 `miso-gpios`**——这是只写设备（屏幕）。
3. `num-chipselects`（**不是 `num-cs`**）是 `spi-gpio` 自己的属性。
4. 用了 `GPIO_ACTIVE_HIGH` 且**没写 `spi-cs-high`**——按官方绑定文档
   的 Note 1 这该报警告并按 L 走，按 §4.3 的代码实际是按 **H** 走。
   **这是本树里一个"照文档理解会理解错"的真实例子。**

### §5.5 `spi-cs-high` 的唯一 arm64 in-tree 用例

整个 `arch/arm64/boot/dts/` 里 **`spi-cs-high` 只出现一次**：
`arch/arm64/boot/dts/ti/k3-am642-evm.dts:432`。

```dts
&main_spi0 {
	pinctrl-names = "default";
	pinctrl-0 = <&main_spi0_pins_default>;
	ti,pindir-d0-out-d1-in;
	eeprom@0 {
		compatible = "microchip,93lc46b";
		reg = <0>;
		spi-max-frequency = <1000000>;
		spi-cs-high;
		data-size = <16>;
	};
};
```

**注意控制器节点没有 `cs-gpios`**——这是 native 片选，
所以 `spi-cs-high` 在这里是真的有效的（§4.1 第一行）。
93LC46B 这颗 EEPROM 的片选确实是高有效，属于罕见硬件。

验证命令：

```bash
grep -rn "spi-cs-high" arch/arm64/boot/dts/ | wc -l     # 1
```

---

## §6 三层定位

### §6.1 编译期（改 DTS 之后第一件事）

| 动作 | 命令 | 能抓到什么 |
|-----|-----|----------|
| 编 DTB | `make ARCH=arm64 dtbs` | 语法错、phandle 找不到、include 缺失 |
| **schema 校验** | `make ARCH=arm64 dtbs_check` | 节点名不合 `^spi(...)`、`reg` 和 unit-address 不一致、属性类型错、**属性名拼错** |
| 反编译看合并结果 | `dtc -I dtb -O dts arch/arm64/boot/dts/<厂商>/<板子>.dtb` | `&spi1 {...}` 覆盖之后**实际**长什么样 |

**`dtbs_check` 是唯一能抓到"属性名拼错"的手段**。运行期不会有任何提示——
`of_property_read_bool(nc, "spi-cpol")` 拼成 `spi-cpoll` 就只是读不到，
静默按默认值走。schema 校验会报 unevaluated property。

**但 `dtbs_check` 抓不到 §1.3 的死字**：`spi-rx-delay-us` 在
`spi-peripheral-props.yaml:55` 里是合法属性，写了不报错，只是没人读。

反编译一步不能省。多层 `.dtsi` + 板级覆盖之后，`status`、`spi-max-frequency`
的最终值经常不是你以为的那个。

### §6.2 运行期静态（设备起来了没有）

按这个顺序查，每一步都能把范围缩小一半：

```bash
# 1. 控制器出来了吗？
ls /sys/class/spi_master/          # 或 spi_slave/
#    空 → 控制器驱动没 probe，问题在 clocks/pinctrl/compatible，不在 SPI 层

# 2. 设备出来了吗？
ls /sys/bus/spi/devices/           # spi0.0 = 控制器 0 的片选 0
#    控制器在但设备不在 → 见 §7.2

# 3. 设备绑上驱动了吗？
ls -l /sys/bus/spi/devices/spi0.0/driver
#    没有这个符号链接 → 设备在但没驱动，是 modalias/of_match_table 的问题

# 4. 参数对吗？
cat /sys/bus/spi/devices/spi0.0/modalias
cat /sys/class/spi_master/spi0/of_node/../../uevent 2>/dev/null
```

**`/sys/bus/spi/devices/` 里有节点 = DT 解析成功了**。到这一步还没有，
问题在 §7.1/§7.2，跟 SPI 参数无关。

### §6.3 运行期动态（设备在但行为不对）

| 想知道什么 | 手段 | 说明 |
|----------|-----|------|
| 实际速率 / 每段参数 | `trace-cmd record -e spi:spi_transfer_start` | 唯一能看到 `effective_speed_hz` 的地方 |
| 片选实际怎么动 | `-e spi:spi_set_cs` | 参数是 `activate`（逻辑意图），**不是电平**（§4.2） |
| 消息有没有被拆 | 比对 `spi_message_submit` 和 `spi_transfer_start` 的条数 | 拆分发生在提交期（`03-internals.md` §B4） |
| 走了同步快路径还是队列 | `statistics/spi_sync_immediate` 对 `spi_sync` 的比 | `07-debug.md` §4 |
| 时序 | 示波器 | 极性问题最终只能靠这个确认 |

完整的 tracepoint 清单和 sysfs 统计项见 `07-debug.md`。

---

## §7 症状清单（按坏掉的范围分组）

### §7.1 总线级：`/sys/class/spi_master/` 是空的

| 症状 | 最可能的原因 | 怎么确认 |
|-----|------------|---------|
| 没有任何 spi_master | `status` 还是 `"disabled"` | 反编译 DTB 看合并后的 `status` |
| 同上 | `compatible` 没有匹配的驱动 / 驱动没编进去 | `zcat /proc/config.gz \| grep SPI_` |
| dmesg 有 `-EPROBE_DEFER` 反复出现 | `clocks` / `dmas` / pinctrl 的提供者还没起来 | `cat /sys/kernel/debug/devices_deferred` |
| dmesg 有 `Failed to get clk` 之类 | `clock-names` 名字和驱动要的不一致 | 读该控制器驱动的 `devm_clk_get()` 调用 |
| dmesg 有 `No unused native chip select available` | 混用 GPIO CS 和 `SPI_MASTER_GPIO_SS`，native CS 全被占了 | `drivers/spi/spi.c:3046-3050`；给 `cs-gpios` 少留一位或加 `num-cs` |

### §7.2 单设备级：控制器在，`/sys/bus/spi/devices/` 里少了一个

| 症状 | 原因 | 依据 |
|-----|-----|------|
| dmesg: `... has no valid 'reg' property (-22)` | 子节点漏了 `reg` | `drivers/spi/spi.c:2319` 一带 |
| dmesg: `cs0 >= max 1` | `reg` 超过 `ctlr->num_chipselect` | `:706` |
| dmesg: `chipselect 0 already in use` | 两个子节点 `reg` 相同 | `:650` |
| dmesg: `%pOF is not called 'slave'` | slave 模式下子节点名字不叫 `slave` | `:2309` |
| dmesg: `spidev listed directly in DT is not supported` | `compatible` 不在 spidev 白名单里 | `drivers/spi/spidev.c:719` |
| dmesg: `can't setup spi0.0, status -22` | 控制器 `mode_bits` 不支持你要的模式位 | `:671`；查 `SPI_3WIRE`/`SPI_LSB_FIRST` |
| **什么都没报，就是不出现** | 子节点被 `status = "disabled"` 关了，或者写在了错误的父节点下面 | 反编译 DTB |
| 设备出现了但没有 driver 链接 | `compatible` 的 vendor 前缀砍掉后和 `spi_device_id` 不匹配 | `05-usage.md` §2.4 |

### §7.3 参数级：设备在、驱动绑上了、通信不对

| 症状 | 原因 | 怎么查 |
|-----|-----|-------|
| 完全没有响应，示波器上 CS 一直不动 | 片选极性反了 | §4.4 决策树 |
| 数据整体错位一个 bit / 全 0 / 全 FF | `spi-cpol`/`spi-cpha` 配错 | §3.3；四种模式试一遍 |
| 速率比配的低很多 | 控制器只能整数分频，静默下夹 | tracepoint 看 `effective_speed_hz`（§6.3） |
| 速率比配的**高** | `spi-max-frequency` 漏写，跑了控制器最高速 | §3.2 第 1 条 |
| 慢速能通、快了就错 | 信号完整性 / 缺 `rx-sample-delay-ns` | §1.3；只有 rockchip 和 dw 支持 |
| 字节序反了 | 需要 `spi-lsb-first` 但控制器不支持 | §3.6 |
| 四线配了但速度没变 | `spi-tx/rx-bus-width` 写了非法值，只 `dev_warn` 就当单线 | §3.4 陷阱 1；`dmesg \| grep bus-width` |
| QSPI 慢得像单线 | 控制器节点上有 `cs-gpios`，spi-mem 静默退回 | §5.2 第 4 点 |
| 写了 `spi-rx-delay-us` 没效果 | **核心不读这个属性** | §1.3 |
| 写了 `num-cs` 没效果 | 这个控制器驱动不读它 | §1.2 |

### §7.4 拓扑级：多设备 / 级联

| 症状 | 原因 | 依据 |
|-----|-----|-----|
| 一个设备工作、另一个就乱 | 两个设备 `spi-max-frequency` 差很多，而控制器 `set_cs` 没在每段之间重设 | `03-internals.md` §B2.1 的早退条件 |
| 加了第二个设备后第一个也不行了 | 片选没真正释放（`cs_change` 语义） | `05-usage.md` §2.3 |
| `spi-mux` 下面的设备全不通 | mux 节点自己没配对（`mux-controls` / `mux-control-states`） | `04-extensions.md` §4 |
| 用了 `spi-mux` 但整机卡死 | 极少见——`must_async` 是防这个的 | `drivers/spi/spi-mux.c:165` |

---

## §8 错误消息 → 原因 映射表

全部来自本树 `drivers/spi/`，可以直接 `grep` 原文核对。

| dmesg 原文 | 出处 | 含义 / 怎么改 |
|-----------|------|-------------|
| `%pOF has no valid 'reg' property (%d)` | `spi.c:2319` 一带 | 子节点漏 `reg`。加上片选号 |
| `cs%d >= max %d` | `spi.c:706`（另一处 `:728`） | `reg` 超过控制器片选数。改小 `reg`，或看驱动是否读 `num-cs`（§1.2） |
| `chipselect %d already in use` | `spi.c:650` | 同一控制器下两个子节点 `reg` 相同 |
| `%pOF is not called 'slave'` | `spi.c:2309` | slave 模式控制器的子节点必须**正好**叫 `slave` |
| `spi-tx-bus-width %d not supported` | `spi.c:2277-2280` | 只是 `dev_warn`，**probe 照样成功**，按单线跑。合法值 0/1/2/4/8 |
| `spi-rx-bus-width %d not supported` | `spi.c:2301-2304` | 同上 |
| `Invalid native chip select %d` | `spi.c:3039` 一带 | `cs-gpios` 里 `<0>` 的位置超出了 native CS 的位宽 |
| `No unused native chip select available` | `spi.c:3049` 一带 | `SPI_MASTER_GPIO_SS` 需要一条闲置 native CS，全被占了 |
| `can't setup %s, status %d` | `spi.c:671` | `spi_setup()` 失败。`-EINVAL` 通常是 `mode_bits` 不支持某个模式位 |
| `spidev listed directly in DT is not supported` | `spidev.c:719` | `compatible` 不在 `:723-736` 的白名单里 |
| `SPI transfer timed out` | 控制器驱动各自实现 | 不是 DT 问题（除非片选/时钟根本没通）。见 `07-debug.md` §5 |

**没有对应错误消息的三类问题**（这也是本篇存在的理由）：

| 静默失效 | 后果 | 只能靠 |
|---------|-----|-------|
| 属性名拼错 | 按默认值走 | `dtbs_check`（§6.1） |
| `spi-max-frequency` 写得太高 | 静默下夹 | tracepoint（§6.3） |
| `spi-rx/tx-delay-us` / `num-cs` / `spi-slave` 没人读 | 完全无效 | 本篇 §1.2 / §1.3 |
| QSPI 节点上的 `cs-gpios` | 硬件加速失效 | 本篇 §5.2 第 4 点 |
| GPIO 片选下的 `spi-cs-high` | 被忽略 | 本篇 §4 |

---

## §9 一页速查

```
控制器节点（SoC .dtsi）              设备节点（板级 .dts）
─────────────────────              ─────────────────────
compatible          必需            compatible          必需
reg                 必需            reg                 必需，= 片选号
#address-cells = 1  必需            spi-max-frequency   强烈建议显式写
#size-cells = 0     必需            spi-cpol / spi-cpha  按手册 Mode 号翻译
clocks/clock-names  几乎总是         spi-cs-high         只对 native CS 有效
interrupts          通常            spi-tx/rx-bus-width  0/1/2/4/8，非法值只警告
dmas/dma-names      可选            spi-3wire            需控制器支持
pinctrl-*           通常            spi-lsb-first        需控制器支持
cs-gpios            可选，极性在这
num-cs              看驱动读不读
status              板级改 okay

核心只读上面标出的 9 个 + cs-gpios（经 gpiolib）。其余都是驱动私有或死字。
```

改完 DTS 的固定三步：

```bash
make ARCH=arm64 dtbs_check                      # 1. 抓拼写和 schema
dtc -I dtb -O dts <板子>.dtb | less             # 2. 看合并后的真实结果
ls /sys/bus/spi/devices/ && dmesg | grep -i spi # 3. 起来了没有 + 有没有 warn
```

---

**下一篇**：`07-debug.md`（tracepoint / sysfs / debugfs / 错误码分诊的完整清单）
