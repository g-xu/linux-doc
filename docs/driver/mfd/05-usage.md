---
title: 使用指南
---

# mfd 05：怎么用

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇分两侧：**父驱动侧**（你有一颗多功能芯片要分家，§2）和
**子驱动侧**（你写的驱动被 mfd 创建出来，§3）。**两侧的写法完全不同，别混。**

---

## §1 先确认你需要的是哪条路

⚠️⚠️ **一半以上的情况根本不用写 mfd 父驱动。** 按顺序问自己：

```
   这颗"多功能设备"是 SoC 内部的一段 MMIO 吗？
        │
        ├─ 是 ──▶ 每个功能块在 DT 里有自己的 reg 吗？
        │            ├─ 是 ──▶ ⚠️ DT 写 compatible = "...", "simple-mfd"
        │            │           ⇒ 一行 C 代码都不用写（04 §6）
        │            └─ 否，只是一堆散寄存器
        │                     ──▶ ⚠️ DT 加 "syscon"，别人用
        │                           syscon_regmap_lookup_by_phandle() 来读写（§4）
        │
        └─ 否，是挂在 I2C/SPI/USB 上的外部芯片
                 │
                 ├─ 功能块之间无供电/时序依赖 ──▶ devm_mfd_add_devices()（§2）
                 ├─ 有依赖（某个子设备给别人供电）──▶ ⚠️ mfd_add_devices() +
                 │                                   level = HIGH + 手动两次 remove（§5.3）
                 └─ 可热插拔多颗 ──▶ mfd_add_hotplug_devices()（§5.4）
```

⚠️ **判据的本质是"子设备的寄存器能不能被 CPU 直接寻址"**：
能 ⇒ DT 描述得清，用 `simple-mfd`；
不能（要经过 I2C/SPI 传输）⇒ DT 描述不出来，必须由父驱动在代码里分家。

---

## §2 一个完整的最小父驱动：`rt4831`

**`drivers/mfd/rt4831.c` 全文只有 118 行**，是整个目录里最干净的完整例子
（Richtek RT4831，一颗背光 + 显示电源芯片）。⚠️ **建议把它当模板抄。**

### §2.1 cell 数组 —— 这个驱动用了宏

```c
/* drivers/mfd/rt4831.c:24-27 */
static const struct mfd_cell rt4831_subdevs[] = {
        MFD_CELL_OF("rt4831-backlight", NULL, NULL, 0, 0, "richtek,rt4831-backlight"),
        MFD_CELL_NAME("rt4831-regulator")
};
```

⚠️ 展开后是两个 cell：
- `rt4831-backlight`：有 `of_compatible`（`"richtek,rt4831-backlight"`）
  ⇒ **子设备会拿到 DT 里那个子节点**（03 §2.4 出口 A）；
- `rt4831-regulator`：⚠️ **只有名字**，其余全零
  ⇒ **子设备没有 `of_node`**，而且核心会打一条 `pr_warn`（03 §2.4 出口 C）。

⚠️ **这两种混用是 PMIC 类芯片的常态**（04 §2.1）：regulator 的 DT 绑定习惯
把输出通道放在父节点下的 `regulators { }` 容器里，不给每个通道一个 compatible。

⚠️ `MFD_CELL_OF` 的六个位置参数依次是
`name, res, pdata, pdsize, id, compat`（`include/linux/mfd/core.h:34`）
⇒ ⚠️ **位置参数容易数错**。**新代码建议直接写指定初始化器**（01 §3）：

```c
static const struct mfd_cell my_subdevs[] = {
        {
                .name          = "myechip-backlight",
                .of_compatible = "vendor,mychip-backlight",
        }, {
                .name          = "mychip-regulator",
        },
};
```

⚠️ **必须 `static const`** —— 核心只做浅拷贝，`resources` 指针指回你这个数组（03 §2.1）。

### §2.2 probe 的五步

```c
/* drivers/mfd/rt4831.c:45-88 —— 结构，去掉错误分支的 dev_err */
static int rt4831_probe(struct i2c_client *client)
{
        /* ① 打开芯片的 enable 脚 */
        enable_gpio = devm_gpiod_get_optional(&client->dev, "enable", GPIOD_OUT_HIGH);

        /* ② 建 regmap —— 这一步不属于 mfd */
        regmap = devm_regmap_init_i2c(client, &rt4831_regmap_config);

        /* ③ 读芯片 ID 确认真的是这颗芯片 */
        ret = regmap_read(regmap, RT4831_REG_REVISION, &chip_id);
        if ((chip_id & RT4831_VID_MASK) != RICHTEK_VENDOR_ID)
                return -ENODEV;

        /* ④ 芯片级的一次性初始化 */
        ret = regmap_update_bits(regmap, RT4831_REG_I2CPROT, ...);

        /* ⑤ 最后一步才分家 */
        return devm_mfd_add_devices(&client->dev, PLATFORM_DEVID_AUTO, rt4831_subdevs,
                                    ARRAY_SIZE(rt4831_subdevs), NULL, 0, NULL);
}
```

⚠️⚠️ **顺序不是随意的，五步的先后有硬约束**：

| 步 | 为什么必须在 `mfd_add_devices()` 之前 |
|---|---|
| ① enable GPIO | 芯片没上电，③ 的 `regmap_read` 会失败 |
| ② regmap | ⚠️ **子驱动 probe 的第一件事就是 `dev_get_regmap(dev->parent, NULL)`**（§3.1）——晚了就拿到 `NULL` |
| ③ 芯片 ID | ⚠️ **`mfd_add_devices()` 一旦返回，子设备可能已经 probe 完了**（`platform_device_add()` 是同步的，03 §2.8）⇒ 之后再发现"芯片不对"已经来不及 |
| ④ 芯片级初始化 | 同上——子驱动可能已经在读写寄存器了 |

⚠️⚠️ **第 ③ 条是最容易写错的**：把 chip-id 校验放到 `mfd_add_devices()` 之后，
在 `-ENODEV` 返回前那些子设备已经跑起来了，而错误路径又把它们删掉
⇒ **子驱动的 probe/remove 各跑一遍，日志里一堆莫名其妙的报错。**

⚠️ **`mfd_add_devices()` 必须是 probe 的最后一步。** 这是本篇最重要的一条纪律。

### §2.3 remove 里没有 `mfd_remove_devices()`

```c
/* drivers/mfd/rt4831.c:90-99 */
static void rt4831_remove(struct i2c_client *client)
{
        struct regmap *regmap = dev_get_regmap(&client->dev, NULL);
        int ret;

        /* Disable WLED and DSV outputs */
        ret = regmap_update_bits(regmap, RT4831_REG_ENABLE, RT4831_RESET_MASK,
                                 RT4831_RESET_MASK);
        if (ret)
                dev_warn(&client->dev, "Failed to disable outputs (%pe)\n", ERR_PTR(ret));
}
```

⚠️ **用了 `devm_` 版本，所以 remove 里不需要（也不应该）调 `mfd_remove_devices()`**
—— devres 会在 `remove` 返回后自动调（01 §2.2）。

⚠️⚠️ **但这里有一个真实的顺序问题**：`devm` 的释放发生在 `remove()` **之后**
⇒ ⚠️ **`:96` 关输出的时候，子设备还活着**。
对 rt4831 这颗芯片无害（关总输出是幂等的），
但 ⚠️ **如果你的 remove 要做"子设备必须先停"的动作，就不能用 devm 版本**
—— 改成手动 `mfd_add_devices()` + 在 remove 开头 `mfd_remove_devices()`（§5.2）。

⚠️ 顺带注意 `:92` 用 `dev_get_regmap(&client->dev, NULL)` 重新取 regmap
而不是存指针 —— **regmap 挂在设备上是可以随时取回的**，这个技巧在子驱动侧也用（§3.1）。

### §2.4 Kconfig / Makefile / DT 三件套

```
config MFD_RT4831
        tristate "Richtek RT4831 four-channel WLED and Display Bias Voltage"
        depends on I2C
        select MFD_CORE
        select REGMAP_I2C
```

⚠️ **`select MFD_CORE` 是必须的** —— `include/linux/mfd/core.h` 里没有
`CONFIG_MFD_CORE=n` 的桩函数，漏了就是链接错误（01 §6）。
⚠️ 有中断分发就再加 `select REGMAP_IRQ`（04 §7 的 axp20x 例子）。

⚠️ DT 侧父节点只需要 `compatible` + `reg` + 若干子节点，**不需要写 `simple-mfd`**
—— 子设备是父驱动建的，不是 OF 自动实例化的（06 §2 有完整例子）。

---

## §3 子驱动侧：被 mfd 创建出来的驱动怎么写

⚠️ **子驱动就是普通的 `platform_driver`**，没有任何 mfd 专用注册接口（02 §5）。
差别只在**四样东西从哪里拿**。

### §3.1 拿 regmap —— `dev_get_regmap(pdev->dev.parent, NULL)`

```c
/* drivers/pwm/pwm-sl28cpld.c:217 */
priv->regmap = dev_get_regmap(pdev->dev.parent, NULL);
```

⚠️⚠️ **这是 arm64 上 mfd 子驱动最常见的第一行有效代码。** 同型写法遍布全树：

| 例子 | 位置 |
|---|---|
| `drivers/thermal/max77620_thermal.c` | `:105` |
| `drivers/thermal/qcom/qcom-spmi-temp-alarm.c` | `:394` |
| `drivers/reset/reset-tn48m.c` | `:93` |
| `drivers/video/backlight/qcom-wled.c` | `:1635` |
| `drivers/pinctrl/qcom/pinctrl-spmi-gpio.c` | `:1079`（用 `dev->parent`） |
| `drivers/mux/mmio.c` | `:50`（⚠️ `?: ERR_PTR(-ENODEV)` 把 `NULL` 转成错误） |

⚠️ **必须判空** —— `dev_get_regmap()` 找不到返回 `NULL`（不是 `ERR_PTR`）。
`drivers/mux/mmio.c:50` 那个 `?:` 写法是最紧凑的处理：

```c
regmap = dev_get_regmap(dev->parent, NULL) ?: ERR_PTR(-ENODEV);
```

⚠️⚠️ **第二个参数是名字**。父驱动只建一个 regmap ⇒ 传 `NULL`；
父驱动按资源名建了多个（04 §4.3 的 ocelot）⇒ **必须传那个名字**：

```c
/* 形如 ocelot 的父设备 */
regmap = dev_get_regmap(dev->parent, "gcb_miim0");
```

⚠️ **这条链路完全不经过 mfd 核心** —— regmap 是父驱动挂在父设备上的，
子驱动按 `dev->parent` 去取。**mfd 只保证了 `dev->parent` 指对**（03 §2.2）。

### §3.2 拿资源 —— 用 `platform_get_resource()`，不要读 cell

```c
res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
base = devm_ioremap_resource(&pdev->dev, res);
```

⚠️⚠️ **绝对不要用 `mfd_get_cell(pdev)->resources`** ——
那是**换算前的相对偏移**，头文件注释明说了
（`include/linux/mfd/core.h:103-106`，02 §2.6）。
`platform_get_resource()` 拿到的是核心换算过的值（03 §2.7）。

⚠️ 按名字取更稳（cell 里用了 `DEFINE_RES_*_NAMED`）：

```c
res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "gcb_gpio");
```

### §3.3 拿中断 —— ⚠️ 两种，取决于父驱动怎么传的

**情况 A：父驱动传了 `domain`** ⇒ 资源里已经是 virq：

```c
irq = platform_get_irq_byname(pdev, "PEK_DBR");
if (irq < 0)
        return irq;
ret = devm_request_threaded_irq(dev, irq, NULL, my_handler, IRQF_ONESHOT, ...);
```

**情况 B：父驱动没传 `domain`（`domain = NULL`）** ⇒ 资源里是芯片内部 hwirq，
⚠️ **必须自己翻译**：

```c
/* drivers/input/misc/axp20x-pek.c:233-243 —— 节选 */
axp20x_pek->irq_dbr = platform_get_irq_byname(pdev, "PEK_DBR");
...
axp20x_pek->irq_dbr = regmap_irq_get_virq(axp20x->regmap_irqc,
                                          axp20x_pek->irq_dbr);
```

⚠️⚠️ **两种写法在树里都大量存在，而 mfd 核心不提供任何方式让子驱动判断自己属于哪种。**
⇒ **写子驱动前必须先读父驱动的 `mfd_add_devices()` 那一行**，看第七个参数是不是 `NULL`（04 §2.2）。
⚠️ 弄错的症状是 `request_irq()` 返回 `-EINVAL`，或者更糟：**申请到了别的设备的中断**。

### §3.4 ⚠️ `mfd_get_cell()` 基本不要用

全树 26 处调用，**分布在 7 个文件**：

| 文件 | 说明 |
|---|---|
| `drivers/mfd/asic3.c` | `:808` `:818` `:828` —— arm32 掌上设备 |
| `drivers/video/fbdev/tmiofb.c` | 5 处，Toshiba TMIO |
| `drivers/mmc/host/tmio_mmc.c` | 4 处，同上 |
| `drivers/usb/host/ohci-tmio.c` | 4 处，同上 |
| `drivers/mtd/nand/raw/tmio_nand.c` | 4 处，同上 |
| `drivers/leds/leds-asic3.c` | 4 处，⚠️ **唯一用 `mfd_cell_enable()` 的驱动**（01 §2.4） |
| `drivers/regulator/da9052-regulator.c` | `:395` |

⚠️⚠️ **全部是 2008–2010 年的老平台，arm64 上一个都没有。**
它们用它是为了取 `cell->enable`/`disable` 或 `cell->platform_data`
⇒ ⚠️ **现代写法用 `dev_get_platdata()` 取平台数据、用 `dev->parent` 的 regmap 访问硬件，
两条路都不需要 cell。**

⚠️ 真要用，记得判空（`pdev` 可能不是 mfd 建的，01 §2.5）。

---

## §4 syscon 消费者怎么写

⚠️ **这一节与 `mfd_cell` 无关。** 场景是：你的驱动需要读写**别人那块**杂项寄存器
（时钟旁路位、PHY 模式选择、boot 地址寄存器之类）。

### §4.1 标准三步

```c
/* drivers/remoteproc/imx_rproc.c:726 —— 取 */
regmap = syscon_regmap_lookup_by_phandle(dev->of_node, "syscon");
if (IS_ERR(regmap)) {
        dev_err(dev, "failed to find syscon\n");
        return PTR_ERR(regmap);
}
```

对应 DT：

```dts
my-device {
        compatible = "vendor,my-device";
        syscon = <&sysctrl>;      /* 属性名由你的绑定文档定义 */
};
```

之后就是普通 regmap 读写：`regmap_read()` / `regmap_update_bits()`。

⚠️⚠️ **必须在 probe 里取好存起来，不要在需要时才 lookup**：
`syscon_regmap_lookup_by_phandle()` 内部拿 mutex、可能现场 `of_iomap()` +
`regmap_init_mmio()` + `clk_get()`（03 §6.1）
⇒ ⚠️ **它必须在可睡眠上下文里调，绝不能在中断处理或 spinlock 里调**。

### §4.2 属性名没有统一约定

| 属性名 | 使用者 |
|---|---|
| `"syscon"` | `drivers/remoteproc/imx_rproc.c:726` |
| `"samsung,sysreg"` | `drivers/soc/samsung/exynos-usi.c:189` |
| `"st,syscfg"` | `drivers/remoteproc/st_remoteproc.c:321` |
| `"ti,bootreg"` | `drivers/remoteproc/omap_remoteproc.c:1150` |
| `"fsl,dsp-ctrl"` | `drivers/remoteproc/imx_dsp_rproc.c:1035` |
| `"ti,syscon-dev"` | `drivers/remoteproc/keystone_remoteproc.c:346` |

⚠️ **syscon 只认"给我一个属性名"**，属性名本身由你的 DT 绑定文档定义
⇒ **写新绑定时用 `vendor,xxx-syscon` 这种带厂商前缀的形式**（06 §5）。

### §4.3 可选的 syscon 用 `_optional`

```c
/* drivers/pci/controller/cadence/pci-j721e.c:317 —— 节选 */
syscon = syscon_regmap_lookup_by_phandle_optional(node, "ti,syscon-acspcie-proxy-ctrl");
```

⚠️⚠️ **三态返回必须写全三个分支**（01 §4.1）：

```c
regmap = syscon_regmap_lookup_by_phandle_optional(np, "vendor,foo-syscon");
if (IS_ERR(regmap))
        return PTR_ERR(regmap);        /* 属性有，但解析失败 —— 真错误 */
if (!regmap)
        goto skip_this_feature;        /* 属性没写 —— 允许 */
/* 拿到了 */
```

⚠️ 漏掉 `!regmap` 那支 ⇒ 后面 `regmap_read(NULL, ...)` 直接空指针。
⚠️⚠️ **而且 `CONFIG_MFD_SYSCON=n` 时的桩也返回 `NULL`**
（`include/linux/mfd/syscon.h:69`）⇒ **"属性不存在"和"syscon 没编进内核"无法区分**。
⇒ **依赖 syscon 的驱动应该在 Kconfig 里 `depends on MFD_SYSCON` 或 `select MFD_SYSCON`。**

### §4.4 ⚠️ 不要用 `device_node_to_regmap()` 代替 `syscon_node_to_regmap()`

⚠️⚠️ **两者的区别不是"少一次 compatible 检查"，而是"少挂时钟"**
（01 §4.2、03 §6.1）：`syscon_node_to_regmap()` 传 `check_clk = true`，
`device_node_to_regmap()` 传 `false`。

⇒ 在"寄存器块需要时钟才能访问"的 SoC 上，用后者拿到的 regmap
**读回全 0 或直接总线挂死**，而函数名完全没有暗示这件事。
⚠️ **`device_node_to_regmap()` 只在你确知那块寄存器不需要时钟时才用。**

⚠️ 还有一个更隐蔽的后果：**regmap 是首次 lookup 时才建的，建好就进表永不重建**
（03 §6.1）⇒ ⚠️⚠️ **谁第一个 lookup，谁的 `check_clk` 决定了这块 syscon 一辈子有没有时钟。**
两个驱动分别用两个接口取同一个节点，**结果取决于 probe 顺序**（07 §5）。

---

## §5 移除路径的四种正确写法

### §5.1 devm 版（⚠️ 默认选它）

```c
/* probe 最后一行 */
return devm_mfd_add_devices(dev, PLATFORM_DEVID_AUTO, cells, ARRAY_SIZE(cells),
                            NULL, 0, NULL);
/* remove 里什么都不用写 */
```

⚠️ 适用条件：**所有 cell 的 `level` 都是默认的 `MFD_DEP_LEVEL_NORMAL`**（01 §2.2）。
⚠️ 注意子设备的销毁在 `remove()` 返回**之后**才发生（§2.3）。

### §5.2 手动版（remove 里要在子设备之前动手）

```c
static void my_remove(struct i2c_client *client)
{
        mfd_remove_devices(&client->dev);   /* 先让子设备全退出 */
        /* 现在可以安全地关芯片总电源、删 irqchip 等 */
        regmap_del_irq_chip(my->irq, my->regmap_irqc);
}
```

⚠️ **`regmap_del_irq_chip()` 必须在 `mfd_remove_devices()` 之后**
—— 否则还没退出的子驱动会去操作已销毁的 irq 域（04 §2.3）。

### §5.3 带 `level` 的三段式（⚠️ 只有一个驱动这么做）

```c
/* 注册：分两批 */
mfd_add_devices(dev, PLATFORM_DEVID_NONE, ldo_cells, n_ldo, NULL, 0, NULL);  /* level=HIGH */
...
mfd_add_devices(dev, PLATFORM_DEVID_NONE, main_cells, n_main, NULL, 0, NULL);

/* 拆解：三段夹心 */
mfd_remove_devices(dev);          /* ① 普通子设备 */
regulator_disable(my->dcvdd);     /* ② 父驱动自己的清理 —— 必须夹在中间 */
mfd_remove_devices_late(dev);     /* ③ level = HIGH 的 */
```

⚠️⚠️ **三条铁律**（04 §3）：
1. ⚠️ **不能用 devm 版本** —— 它的释放回调只调 `mfd_remove_devices()`，
   `HIGH` 的 cell 永远删不掉（01 §2.2）。
2. ⚠️ **probe 的错误路径必须也调 `_late()`** —— 否则 `-EPROBE_DEFER` 重试时
   每次漏一个子设备。**`drivers/mfd/madera-core.c:761-762` 就漏了这一步**（04 §3.3）。
3. ⚠️ **`mfd_add_devices()` 自己的部分失败回滚也只调 `mfd_remove_devices()`**
   （`drivers/mfd/mfd-core.c:366-368`）⇒ **同一批里既有 `HIGH` 又有 `NORMAL` 时，
   回滚不完整** ⇒ **把 `HIGH` 的 cell 单独一批注册。**

### §5.4 热插拔版

```c
ret = mfd_add_hotplug_devices(dev, cells, ARRAY_SIZE(cells));
...
mfd_remove_devices(dev);     /* 它没有 devm 版本，必须手动 */
```

⚠️ `id` 固定 `PLATFORM_DEVID_AUTO` ⇒ ⚠️ **子设备名带自动编号**
（`/sys/bus/platform/devices/mychip-gpio.3`）
⇒ **不要在 udev 规则或脚本里硬编码这类名字**（01 §2.6、07 §1）。

---

## §6 常见错误：症状 → 原因

| 症状 | 最可能的原因 | 去看 |
|---|---|---|
| ⚠️ 子设备在 `/sys/bus/platform/devices/` 里**根本没有** | DT 子节点写了 `status = "disabled"` ⇒ **核心静默跳过并返回 0** | 03 §2.4 |
| 子设备**有**，但没有驱动绑定 | `cell->name` 与子驱动的 `driver.name` 不一致（无 `of_compatible` 时靠名字匹配） | 02 §2.1 |
| 子驱动 probe 里 `dev_get_platdata()` 返回 `NULL` | ⚠️ 填了 `platform_data` 忘填 `pdata_size` | 02 §2.3 |
| 子驱动 `platform_get_resource()` 返回 `NULL` | ⚠️ 忘填 `num_resources`（用 `MFD_RES_SIZE` 或 `ARRAY_SIZE`） | 01 §3 |
| 子驱动读寄存器读到别的设备 | ⚠️ `mem_base` 传了 `NULL`，cell 里的 MEM 地址被当**绝对值** | 03 §2.7 |
| `request_irq()` 返回 `-EINVAL` | ⚠️ 父驱动没传 `domain`，资源里是 hwirq，忘了 `regmap_irq_get_virq()` | §3.3 |
| 内核日志 "Failed to locate of_node" | cell 有 `of_compatible` 但 DT 里没有对应子节点 ⇒ ⚠️ **子设备仍会注册，只是没有 `of_node`** | 03 §2.4 |
| 两个同 compatible 的子节点身份互换 | ⚠️ 没设 `use_of_reg = true` + `of_reg` | 04 §4.1 |
| 子驱动填了 `cell->suspend`，休眠时不被调 | ⚠️⚠️ **`suspend`/`resume` 是死字段**，用 `platform_driver.driver.pm` | 02 §2.2 |
| `mfd_cell_enable()` 调两次，回调也跑两次 | ⚠️ 注释承诺的引用计数**不存在** | 01 §2.4 |
| unbind 父驱动后某个子设备还在 | ⚠️ 它的 `level = HIGH` 而你用了 devm 版本 | §5.3 |
| syscon 读回全 0 | ⚠️ 用了 `device_node_to_regmap()`，时钟没挂上 | §4.4 |
| syscon `regmap_read()` 返回 `-EIO` | ⚠️ DT 里 `reg` 长度写短了 ⇒ `max_register` 卡住 | 03 §6.2 |
| 换了 probe 顺序 syscon 行为就变 | ⚠️⚠️ **regmap 惰性创建，首个 lookup 者的 `check_clk` 定终身** | §4.4 |

---

## §7 可以直接抄的父驱动模板

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/i2c.h>
#include <linux/mfd/core.h>
#include <linux/module.h>
#include <linux/regmap.h>

#define MYCHIP_REG_ID   0x00
#define MYCHIP_ID_VAL   0x5a

/* ① cell 数组：static const，混用两种形态 */
static const struct mfd_cell mychip_subdevs[] = {
        {
                .name          = "mychip-gpio",
                .of_compatible = "vendor,mychip-gpio",
        }, {
                .name          = "mychip-regulator",   /* 无 of_node，靠父节点 */
        },
};

static const struct regmap_config mychip_regmap_config = {
        .reg_bits     = 8,
        .val_bits     = 8,
        .max_register = 0x7f,
};

static int mychip_probe(struct i2c_client *client)
{
        struct device *dev = &client->dev;
        struct regmap *regmap;
        unsigned int id;
        int ret;

        /* ② regmap 必须先建 —— 子驱动第一件事就是来取它 */
        regmap = devm_regmap_init_i2c(client, &mychip_regmap_config);
        if (IS_ERR(regmap))
                return dev_err_probe(dev, PTR_ERR(regmap), "regmap init failed\n");

        /* ③ 确认芯片身份 —— 必须在分家之前 */
        ret = regmap_read(regmap, MYCHIP_REG_ID, &id);
        if (ret)
                return dev_err_probe(dev, ret, "failed to read ID\n");
        if (id != MYCHIP_ID_VAL)
                return dev_err_probe(dev, -ENODEV, "bad ID 0x%02x\n", id);

        /* ④ 芯片级一次性初始化 */
        /* regmap_update_bits(regmap, ...); */

        /* ⑤ 最后一步才分家 */
        return devm_mfd_add_devices(dev, PLATFORM_DEVID_AUTO, mychip_subdevs,
                                   ARRAY_SIZE(mychip_subdevs), NULL, 0, NULL);
}

static const struct of_device_id mychip_of_match[] = {
        { .compatible = "vendor,mychip", },
        { }
};
MODULE_DEVICE_TABLE(of, mychip_of_match);

static struct i2c_driver mychip_driver = {
        .driver = {
                .name           = "mychip",
                .of_match_table = mychip_of_match,
        },
        .probe_new = mychip_probe,
};
module_i2c_driver(mychip_driver);
MODULE_LICENSE("GPL");
```

**配套 Kconfig**：

```
config MFD_MYCHIP
        tristate "Vendor MyChip multi-function device"
        depends on I2C
        select MFD_CORE
        select REGMAP_I2C
```

⚠️ **要分发中断就再加 `select REGMAP_IRQ`**，probe 里在 ⑤ 之前
`regmap_add_irq_chip()`，并决定是把 `domain` 传给 `mfd_add_devices()`
还是让子驱动自己 `regmap_irq_get_virq()`（§3.3）。

⚠️ **模板刻意没有 `remove`** —— devm 版本不需要（§5.1）。
要在子设备之前做清理就改成 §5.2 的手动版。

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 接口签名和返回值的细节 | 01-api.md |
| `mfd_cell` 每个字段能填什么 | 02-datastructures.md |
| 核心在 ⑤ 那一步到底做了什么 | 03-internals.md |
| 更复杂的真实驱动（多变体、中断、level） | 04-extensions.md |
| DT 该怎么写 | 06-devicetree.md |
| 上面这些症状怎么现场定位 | 07-debug.md |
| 为什么模板长这样 | 08-feynman.md |
| 本篇引用的取证口径 | 99-evidence.md |
