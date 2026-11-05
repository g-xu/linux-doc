---
title: 扩展机制
---

# mfd 04：驱动侧的实际形态

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇口径：**核心只有 1092 行，而目录里有 235 个单芯片驱动**（03 §1）。
本篇不逐个讲，只挑**四个能覆盖全部机制的代表**，每个都读到具体行号。

---

## §1 235 个驱动的形态分布

**按父设备挂在什么总线上分**（`drivers/mfd/*.c` 里定义了哪种 driver 结构）：

| 父总线 | 文件数 | 说明 |
|---|---|---|
| **I2C** | **95** | ⚠️ **主流形态**：PMIC、codec、touch controller |
| **platform** | 48 | SoC 内部的寄存器块聚合（syscon 类） |
| SPI | 14 | 高速 codec、部分 PMIC |
| PCI | 12 | ⚠️ 几乎全是 x86（`intel-lpss-pci.c` 之类） |
| USB | 2 | `dln2.c` · `viperboard.c`（USB 转 I2C/SPI/GPIO 桥） |

⚠️ **总数超过 237 是正常的**：一个 `.c` 可以同时注册 I2C 和 SPI 两种驱动
（Cirrus 的 codec 就是这样，同一颗芯片两种接线）。

**按用什么访问寄存器分**：

| 手段 | 文件数 | 说明 |
|---|---|---|
| `devm_regmap_init_i2c()` | **72** | ⚠️ **mfd 父驱动的标准写法** |
| `regmap_add_irq_chip()` | **52** | ⚠️ **一半以上的 mfd 父驱动自带一个 irqchip** |
| `devm_mfd_add_devices()` | 66 | 剩下的用非 devm 版（有拆解顺序要求，§3） |

⇒ ⚠️⚠️ **一个典型的 mfd 父驱动其实是三件东西的组合**：

```
   总线驱动（i2c_driver）
        + regmap（把 I2C 读写包成寄存器访问）
        + regmap-irq（把芯片的一根 INT 脚展开成一个 irq_domain）
        + mfd_add_devices（把功能块分出去）
```

⚠️ **前三样都不属于 mfd 子系统** —— mfd 只负责最后一步。
**这就是为什么核心只有 471 行**：它复用了 regmap、regmap-irq、platform bus 三个现成子系统（08 §4）。

---

## §2 代表一：`axp20x` —— 标准 PMIC 形态

**`drivers/mfd/axp20x.c` 1037 行**，X-Powers AXP 系列 PMIC 的公共核心
（`axp20x-i2c.c` / `axp20x-rsb.c` 是两个总线外壳）。
⚠️ **arm64 上最常见的 mfd 父驱动**（Allwinner 板子几乎都有一颗）。

### §2.1 cell 数组长什么样

```c
/* drivers/mfd/axp20x.c:591-618 */
static const struct mfd_cell axp20x_cells[] = {
        {
                .name           = "axp20x-gpio",
                .of_compatible  = "x-powers,axp209-gpio",
        }, {
                .name           = "axp20x-pek",
                .num_resources  = ARRAY_SIZE(axp20x_pek_resources),
                .resources      = axp20x_pek_resources,
        }, {
                .name           = "axp20x-regulator",
        }, {
                .name           = "axp20x-adc",
                .of_compatible  = "x-powers,axp209-adc",
        }, {
        ...
};
```

⚠️ **三种 cell 混在同一个数组里**，这是最值得注意的一点：

| cell 形态 | 例子 | 子驱动靠什么找到自己的信息 |
|---|---|---|
| **只有 `name`** | `axp20x-regulator`（`:600-601`） | ⚠️ 靠父设备的 `of_node`（`dev->parent->of_node`）自己去翻 |
| **`name` + `of_compatible`** | `axp20x-gpio`（`:593-594`） | 有自己的 DT 子节点 ⇒ 标准 `of_property_read_*` |
| **`name` + `resources`** | `axp20x-pek`（`:596-598`） | `platform_get_irq_byname()` |

⚠️⚠️ **`axp20x-regulator` 这种"只有名字"的 cell 是最容易误解的一类**：
它没有 `of_compatible`，所以子设备**没有 `of_node`**（03 §2.4 出口 C，
而且会打一条 `pr_warn`）。
⇒ 它的子驱动（`drivers/regulator/axp20x-regulator.c`）必须走
`pdev->dev.parent->of_node` 去找 DT 里的 `regulators { }` 子节点。
⚠️ **这是 PMIC 上非常普遍的做法** —— regulator 的 DT 绑定习惯把所有
输出通道放在父节点下的一个 `regulators` 容器里，与 mfd 的"一个功能块一个子节点"模型不合。

⚠️ 注意 `axp20x_cells` 是 `static const`（`:591`）
⇒ 满足 03 §2.1 那条"cell 数组必须活过子设备生命周期"的约束。
**全树几乎所有驱动都这么写**，所以那条约束在实践中从不被违反。

### §2.2 中断怎么传下去 —— 不用 `domain` 参数

```c
/* drivers/mfd/axp20x.c:182-185 */
static const struct resource axp20x_pek_resources[] = {
        DEFINE_RES_IRQ_NAMED(AXP20X_IRQ_PEK_RIS_EDGE, "PEK_DBR"),
        DEFINE_RES_IRQ_NAMED(AXP20X_IRQ_PEK_FAL_EDGE, "PEK_DBF"),
};
```

```c
/* drivers/mfd/axp20x.c:991-1004 —— 节选 */
/* Only if there is an interrupt line connected towards the CPU. */
if (axp20x->irq > 0) {
        ret = regmap_add_irq_chip(axp20x->regmap, axp20x->irq,
                        IRQF_ONESHOT | IRQF_SHARED | axp20x->irq_flags,
                        -1, axp20x->regmap_irq_chip,
                        &axp20x->regmap_irqc);
        ...
}

ret = mfd_add_devices(axp20x->dev, -1, axp20x->cells,
                      axp20x->nr_cells, NULL, 0, NULL);
```

⚠️⚠️ **注意最后一行：`mem_base = NULL`、`irq_base = 0`、`domain = NULL`。**
芯片明明有一个 regmap-irq 域，**却没有把它传给 mfd**。

⇒ 于是 03 §2.7.2 的换算变成 `res[r].start = 0 + AXP20X_IRQ_PEK_RIS_EDGE`
⇒ ⚠️ **子设备的 IRQ 资源里装的是芯片内部的中断编号（hwirq），不是 Linux virq。**

**子驱动必须自己翻译**：

```c
/* drivers/input/misc/axp20x-pek.c:233-243 —— 节选 */
axp20x_pek->irq_dbr = platform_get_irq_byname(pdev, "PEK_DBR");
...
axp20x_pek->irq_dbr = regmap_irq_get_virq(axp20x->regmap_irqc,
                                          axp20x_pek->irq_dbr);
```

⚠️⚠️ **这是本子系统最重要的一个"实践与接口设计不一致"**：

| | mfd 接口设计的意图 | axp20x 实际做法 |
|---|---|---|
| 传 `domain` | 核心调 `irq_create_mapping()`，子设备拿到 **virq** | ⚠️ 不传 |
| 子驱动 | 直接 `platform_get_irq()` 就能用 | ⚠️ 拿到 hwirq，**必须再调 `regmap_irq_get_virq()`** |

⚠️ **为什么这么做**：`regmap_add_irq_chip()` 建的域在
`mfd_add_devices()` 之前就已存在，本可以传进去。
但 `regmap_irq_get_virq()` 这条路让子驱动能**按名字**取 hwirq
（`DEFINE_RES_IRQ_NAMED`），且不依赖核心版本
—— 这是 regmap-irq 出现之前就形成的习惯，之后没人改。
⇒ ⚠️⚠️ **读一个 mfd 子驱动的中断代码时，第一件事是确认父驱动有没有传 `domain`**：
传了就直接用 `platform_get_irq()`，没传就必须自己翻译。**两种写法在树里都大量存在**（07 §3）。

### §2.3 probe / remove 骨架

```c
/* drivers/mfd/axp20x.c:1003-1032 —— 节选 */
ret = mfd_add_devices(axp20x->dev, -1, axp20x->cells,
                      axp20x->nr_cells, NULL, 0, NULL);
if (ret) {
        dev_err(axp20x->dev, "failed to add MFD devices: %d\n", ret);
        regmap_del_irq_chip(axp20x->irq, axp20x->regmap_irqc);
        return ret;
}
...
void axp20x_device_remove(struct axp20x_dev *axp20x)
{
        ...
        mfd_remove_devices(axp20x->dev);
        regmap_del_irq_chip(axp20x->irq, axp20x->regmap_irqc);
}
```

⚠️ **拆解顺序是"先删子设备，再删 irqchip"**（`:1030-1031`）——
反过来会让还没退出的子驱动去操作一个已销毁的 irq 域。
⇒ ⚠️ **这个顺序约束不由 mfd 核心保证，靠父驱动自己写对。**

⚠️ 注意 `id` 传的是 **`-1`（`PLATFORM_DEVID_NONE`）而不是 `PLATFORM_DEVID_AUTO`**（`:1003`）
⇒ 子设备名字是干净的 `axp20x-gpio`（无后缀编号）
⇒ ⚠️ **系统里只能有一颗 AXP20X** —— 两颗的话第二颗注册子设备时名字冲突。
实践上 PMIC 确实只有一颗（`axp806` 的主从模式在 `:978-988` 用寄存器区分，不靠 platform id）。

---

## §3 代表二：`madera` —— `level` 存在的唯一理由

**`drivers/mfd/madera-core.c` 799 行**，Cirrus Logic CS47Lxx 音频 codec。
⚠️ **全树唯一使用 `MFD_DEP_LEVEL_HIGH` 的驱动**（01 §2.3、02 §2.1）。

### §3.1 分两批注册

```c
/* drivers/mfd/madera-core.c:49-54 */
static const struct mfd_cell madera_ldo1_devs[] = {
        {
                .name = "madera-ldo1",
                .level = MFD_DEP_LEVEL_HIGH,
        },
};
```

```c
/* drivers/mfd/madera-core.c:522-532 —— 第一批，只在两款芯片上 */
case CS47L85:
case WM1840:
        ret = mfd_add_devices(madera->dev, PLATFORM_DEVID_NONE,
                              madera_ldo1_devs,
                              ARRAY_SIZE(madera_ldo1_devs),
                              NULL, 0, NULL);
```

```c
/* drivers/mfd/madera-core.c:738-741 —— 第二批，所有芯片 */
/* No devm_ because we need to control shutdown order of children */
ret = mfd_add_devices(madera->dev, PLATFORM_DEVID_NONE,
                      mfd_devs, n_devs,
                      NULL, 0, NULL);
```

⚠️⚠️ **`:738` 那条注释是整个 `level` 机制的原始说明，值得逐字读**：
"No devm_ because we need to control shutdown order of children"
⇒ **这就是 01 §2.2 那个"用 HIGH level 就不能用 devm"约束的现场证据**
—— 作者知道 devm 版本不行，所以显式不用。

### §3.2 拆解时的三段夹心

```c
/* drivers/mfd/madera-core.c:768-782 —— 节选 */
int madera_dev_exit(struct madera *madera)
{
        /* Prevent any IRQs being serviced while we clean up */
        disable_irq(madera->irq);
        pm_runtime_get_sync(madera->dev);

        mfd_remove_devices(madera->dev);          /* ① 普通子设备 */

        pm_runtime_disable(madera->dev);

        regulator_disable(madera->dcvdd);         /* ② 父设备自己的清理 */
        regulator_put(madera->dcvdd);

        mfd_remove_devices_late(madera->dev);     /* ③ LDO1 子设备 */
        ...
```

⚠️⚠️ **`madera-ldo1` 是一个 regulator 子设备，它提供的电压给整颗芯片用。**

```
注册顺序：  LDO1(HIGH)  →  其它子设备(NORMAL)
                ↓               ↓
运行期：    LDO1 供电  ←  codec/gpio/pinctrl 都在用它
                ↑               ↑
拆解顺序：  ③ 最后删     ①  先删
```

⇒ **如果只有一个 remove 入口，反向遍历会把 LDO1 和其它子设备一起删**
—— 顺序由注册顺序的逆序决定，LDO1 先注册所以最后删，**看起来也没问题**。
⚠️ **但中间那一步 `regulator_disable(dcvdd)`（`:779`）必须夹在两者之间**：
它要在普通子设备都停了之后、LDO1 还活着的时候做。
⇒ ⚠️⚠️ **`level` 真正提供的不是"顺序"，而是"在拆解过程中间插入父驱动自己的动作"的能力。**
这一点在 `include/linux/mfd/core.h` 和任何注释里都没写（99 §1.4）。

### §3.3 ⚠️ madera 自己踩了 01 §2.1 那个坑

```c
/* drivers/mfd/madera-core.c:761-762 */
err_devs:
        mfd_remove_devices(dev);
```

⚠️⚠️ **`madera_dev_init()` 的错误路径只调了 `mfd_remove_devices()`**
⇒ **CS47L85/WM1840 上，如果第一批（LDO1，`level = HIGH`）注册成功、
后面某一步失败，那个 `madera-ldo1` 子设备不会被删掉。**

⇒ 这正是 01 §2.1 预测的漏洞在真实驱动里的实例：
**全树唯一使用 `HIGH` 的驱动，恰好也是唯一能触发这个漏洞的驱动，而它的错误路径写漏了。**
⚠️ 触发条件是"probe 中途失败"（比如 `regulator_get` 或 clk 失败）
⇒ 一般不会遇到，但 `-EPROBE_DEFER` 重试场景下**每次重试都会漏一个子设备**（99 §6）。

---

## §4 代表三：`ocelot-core` —— `use_of_reg` 的唯一使用者

**`drivers/mfd/ocelot-core.c` 161 行**，Microsemi VSC7512 交换芯片的外部控制核心
（SPI 挂载，`ocelot-spi.c` 是传输层）。
⚠️ **全树唯一使用 `of_reg` / `use_of_reg` 的驱动**（02 §2.4）。

### §4.1 两个 cell 的 `of_compatible` 完全一样

```c
/* drivers/mfd/ocelot-core.c:110-124 —— 节选 */
}, {
        .name = "ocelot-miim0",
        .of_compatible = "mscc,ocelot-miim",
        .of_reg = VSC7512_MIIM0_RES_START,
        .use_of_reg = true,
        ...
}, {
        .name = "ocelot-miim1",
        .of_compatible = "mscc,ocelot-miim",
        .of_reg = VSC7512_MIIM1_RES_START,
        .use_of_reg = true,
        ...
```

⚠️⚠️ **这颗芯片有两个 MDIO 控制器，硬件完全相同，DT 里两个子节点的
`compatible` 也完全相同** ⇒ 只靠 `of_compatible` 无法区分谁是谁。
⇒ 于是 `use_of_reg = true` 打开地址匹配，用 `of_reg` 指定"我要 `reg` 起始地址等于这个值的那个子节点"
（匹配逻辑在 `drivers/mfd/mfd-core.c:137-152`，03 §2.5.1）。

⚠️ **不打开 `use_of_reg` 会怎样**：`mfd_match_of_node_to_dev()` 走"分配第一个未占用的匹配节点"分支
⇒ `ocelot-miim0` 拿到 DT 里靠前的那个节点，`ocelot-miim1` 拿到靠后的
⇒ ⚠️ **DT 里节点顺序一变，两个控制器的身份就互换** —— 而资源 `res[]` 是 cell 里写死的
（`:82-89`），于是"节点 A 的 DT 属性 + 节点 B 的寄存器窗口"，症状极难查。
⇒ ⚠️⚠️ **`use_of_reg` 挡的就是这个** —— 它把匹配从"顺序依赖"变成"内容依赖"。

### §4.2 ⚠️ 资源里写的是绝对地址，不是偏移

```c
/* drivers/mfd/ocelot-core.c:82-89 */
static const struct resource vsc7512_miim0_resources[] = {
        DEFINE_RES_REG_NAMED(VSC7512_MIIM0_RES_START, VSC7512_MIIM_RES_SIZE, "gcb_miim0"),
        DEFINE_RES_REG_NAMED(VSC7512_PHY_RES_START, VSC7512_PHY_RES_SIZE, "gcb_phy"),
};
```

⚠️ 注意用的是 **`DEFINE_RES_REG_NAMED`（`IORESOURCE_REG`）而不是 `DEFINE_RES_MEM_NAMED`**。
⇒ 03 §2.7 的三条分支里，`IORESOURCE_REG` **走的是"原样拷贝"那一支**
—— 核心不做任何地址换算。
⇒ ⚠️ **这是绕开 `mem_base` 换算的正规办法**：这颗芯片挂在 SPI 上，
根本没有 CPU 可见的 MMIO 窗口，"父设备寄存器窗口 + 偏移"这个模型对它不成立。

### §4.3 先建 regmap，再分家

```c
/* drivers/mfd/ocelot-core.c:145-155 */
int ocelot_core_init(struct device *dev)
{
        int i, ndevs;

        ndevs = ARRAY_SIZE(vsc7512_devs);

        for (i = 0; i < ndevs; i++)
                ocelot_core_try_add_regmaps(dev, &vsc7512_devs[i]);

        return devm_mfd_add_devices(dev, PLATFORM_DEVID_AUTO, vsc7512_devs, ndevs, NULL, 0, NULL);
}
```

⚠️⚠️ **注册子设备之前先把每个 cell 的每条资源都变成一个 regmap**
（`ocelot_core_try_add_regmaps()` `:136-143` → `ocelot_core_try_add_regmap()` `:127-134`）。
关键是 `:130` 那句 `if (dev_get_regmap(dev, res->name)) return;`
—— **regmap 按 `res->name` 挂在父设备上**，重复调用是幂等的。

⇒ **子驱动的取用方式因此是"按名字从父设备拿 regmap"，不是 `platform_get_resource()` + `ioremap()`**：
资源里那个 `"gcb_miim0"` 名字（`:83`）**同时是 regmap 的查找键**。
⚠️ **这是 04 篇里第三种"资源怎么落到子驱动"的形态**，和前两个都不同：

| 驱动 | 子驱动怎么拿到寄存器访问能力 |
|---|---|
| `axp20x` | ⚠️ 靠父设备的 `dev_get_regmap(dev->parent, NULL)` —— 整颗芯片一个 regmap |
| `ocelot-core` | ⚠️ 靠 `dev_get_regmap(dev->parent, "gcb_miim0")` —— **按资源名分开的多个 regmap** |
| 传统 MMIO 型 | `platform_get_resource()` + `devm_ioremap_resource()` —— 用换算后的 MEM 资源 |

⚠️ 另外注意 `PLATFORM_DEVID_AUTO`（`:154`）：与 axp20x 的 `-1` 相反。
两个 `ocelot-miim` cell 名字不同（`miim0`/`miim1`）本来不冲突，
用 AUTO 是为了**同一块板上能挂多颗 VSC7512**。

⚠️ 顺带一处：`ocelot_core_init` 用 **`EXPORT_SYMBOL_NS(..., MFD_OCELOT)`**（`:156`）
—— 命名空间导出，使用者必须写 `MODULE_IMPORT_NS(MFD_OCELOT)`。
⚠️ **这与 mfd 核心那 6 个裸 `EXPORT_SYMBOL`（01 §1）形成鲜明对比**：
同一个目录里三种导出口径（裸 / `_GPL` / `_NS`）并存。

---

## §5 syscon 的消费面

⚠️ **syscon 的使用者数量远超 mfd 核心**，这是本篇必须摆出来的一组数字：

| 接口 | 引用它的 `.c` 文件数 | 形态 |
|---|---|---|
| `syscon_regmap_lookup_by_phandle()` | **198** | ⚠️ **绝对主流**：`xxx-syscon = <&phandle>` |
| `syscon_node_to_regmap()` | **164** | 已经有 `device_node *` 在手 |
| `syscon_regmap_lookup_by_compatible()` | 57 | ⚠️ 全局搜 compatible（01 §4.1 的不确定性） |
| `device_node_to_regmap()` | 30 | ⚠️ 不检查 compatible，**也不挂时钟** |
| `syscon_regmap_lookup_by_phandle_args()` | 3 | phandle + 固定参数 |
| `syscon_regmap_lookup_by_phandle_optional()` | 3 | `drivers/pci/controller/cadence/pci-j721e.c:317` · `drivers/soc/mediatek/mtk-pm-domains.c:355` |
| ⚠️⚠️ `of_syscon_register_regmap()` | **0** | **全树没有任何调用者** |

⚠️⚠️ **最后一行是本篇最值得记的一条**：`of_syscon_register_regmap()`
（`drivers/mfd/syscon.c:186`，带 12 行 kerneldoc `:175-184`，`EXPORT_SYMBOL_GPL` 在 `:221`）
**在 6.1.158 整棵树里除了自己的定义之外零引用**
⇒ **一个有完整文档、正式导出、却没有使用者的接口**（99 §6 登记）。
⚠️ 它是为"regmap 需要特殊配置、syscon 通用路径造不出来"的场景预留的，
但真需要这么做的驱动都选择了"自己保存 regmap 指针"而不是登记进 syscon 表。

**典型消费点**（都在 probe 里，一次取好存起来）：

```c
/* drivers/remoteproc/imx_rproc.c:726 */
regmap = syscon_regmap_lookup_by_phandle(dev->of_node, "syscon");
```

```c
/* drivers/soc/samsung/exynos-usi.c:189 */
usi->sysreg = syscon_regmap_lookup_by_phandle(np, "samsung,sysreg");
```

⚠️ **属性名没有统一约定**：`"syscon"` · `"samsung,sysreg"` · `"st,syscfg"`
（`drivers/remoteproc/st_remoteproc.c:321`）· `"ti,bootreg"`
（`drivers/remoteproc/omap_remoteproc.c:1150`）· `"fsl,dsp-ctrl"`
（`drivers/remoteproc/imx_dsp_rproc.c:1035`）
⇒ **属性名由各自的 DT 绑定定义，syscon 只负责"给我一个 phandle 属性名，我还你 regmap"**（06 §5）。

---

## §6 `simple-mfd` —— 完全不经过 mfd 核心的另一条路

```c
/* drivers/of/platform.c:24-31 */
const struct of_device_id of_default_bus_match_table[] = {
        { .compatible = "simple-bus", },
        { .compatible = "simple-mfd", },
        { .compatible = "isa", },
        ...
};
```

⚠️⚠️ **`simple-mfd` 不在 `drivers/mfd/` 里，它在 OF 子系统里。**
含义是"请把我的子节点也当作 platform_device 逐个实例化"
—— 由 `of_platform_populate()` 递归完成，**`mfd-core.c` 一行代码都不参与**。

**arm64 上用它的 dts/dtsi 有 62 个**，最常见的写法是与 `syscon` 连写：

```dts
/* arch/arm64/boot/dts/rockchip/rk3399.dtsi:1023 */
compatible = "rockchip,rk3399-pmu", "syscon", "simple-mfd";
```

```dts
/* arch/arm64/boot/dts/freescale/imx8mp.dtsi:451 */
compatible = "fsl,sec-v4.0-mon","syscon", "simple-mfd";
```

⚠️ **三个 compatible 各管一件事**：
`"rockchip,rk3399-pmu"` 给专用驱动认；`"syscon"` 让别人能
`syscon_regmap_lookup_by_phandle()` 拿到 regmap；`"simple-mfd"` 让子节点被自动实例化。

⚠️⚠️ **`simple-mfd` 与 mfd 核心的分工必须说清**：

| | `simple-mfd`（DT 驱动） | `mfd_add_devices()`（代码驱动） |
|---|---|---|
| 谁决定有哪些子设备 | ⚠️ **DT** —— 有几个子节点就有几个 | ⚠️ **驱动的 cell 数组** —— DT 只提供属性 |
| 需要父驱动吗 | ⚠️ **不需要**，`of_platform_populate()` 直接干 | 需要，父驱动必须 probe 起来 |
| 资源怎么来 | 子节点自己的 `reg`（经 `ranges` 换算） | cell 里的相对偏移 + `mem_base` |
| 中断域 | 子节点自己的 `interrupts` | 可由 `domain` 参数注入 |
| 芯片挂在 I2C/SPI 上时 | ⚠️ **不适用**（子节点没有可 `ioremap` 的地址） | ⚠️ **适用** |

⇒ ⚠️⚠️ **判据一句话**：
**父设备是 SoC 内部的一段 MMIO ⇒ 优先 `simple-mfd`，一行 C 代码都不用写。
父设备是挂在 I2C/SPI/USB 上的外部芯片 ⇒ 必须 `mfd_add_devices()`。**

⚠️ 这就解释了 §1 那张总线表：`drivers/mfd/` 里 platform_driver 只有 48 个而 I2C 有 95 个
—— **SoC 内部的多功能块大部分被 `simple-mfd` 吸收了，根本不需要驱动**（08 §5）。

---

## §7 Kconfig / Makefile 接入形态

**`MFD_CORE` 自己的样子**：

```
config MFD_CORE            /* drivers/mfd/Kconfig:9-12 */
        tristate
        select IRQ_DOMAIN
        default n
```

⚠️ **没有 prompt 字符串** ⇒ menuconfig 里看不见、选不了，
**只能被别的 config `select`**。⚠️ 它是 `tristate`，所以 `mfd-core.o` 可以是模块
（`drivers/mfd/Makefile:121`）。
⚠️ 注意它 `select IRQ_DOMAIN` —— 因为 `mfd_add_device()` 会调 `irq_create_mapping()`（03 §2.7.2）。

**`select MFD_CORE` 全树 160 处 / 17 个 Kconfig 文件**：

| Kconfig | 处数 |
|---|---|
| `drivers/mfd/Kconfig` | **140** |
| `drivers/misc/cardreader/Kconfig` | 3 |
| `drivers/gpio/Kconfig` · `arch/mips/lantiq/Kconfig` | 各 2 |
| `drivers/watchdog` · `thermal` · `staging/nvec` · `soc/samsung` · `misc` · `media/radio` · `iio/common/ssp_sensors` · `hid` · `gpu/drm/amd/acp` · `firmware/xilinx` · `platform/x86/intel/int3472` · `arch/arm64/Kconfig.platforms` · `arch/arm/mach-bcm` | 各 1 |

⚠️⚠️ **`arch/arm64/Kconfig.platforms:45` 那一处值得单独指出**：
`ARCH_BCM2835`（树莓派）**在平台 Kconfig 里直接 `select MFD_CORE`**
⇒ **arm64 上唯一一个"整个平台强制打开 mfd 核心"的例子**。

⚠️ **有 20 个 `select MFD_CORE` 不在 `drivers/mfd/` 下** ⇒
**`mfd_add_devices()` 的调用者不限于 `drivers/mfd/`**（01 §1 那 134 个文件里就有它们）。

**父驱动的标准 Kconfig 三段式**（以 axp20x 为例）：

```
config MFD_AXP20X               /* drivers/mfd/Kconfig:182-185 */
        tristate
        select MFD_CORE
        select REGMAP_IRQ

config MFD_AXP20X_I2C           /* :187-191 */
        tristate "X-Powers AXP series PMICs with I2C"
        select MFD_AXP20X
        select REGMAP_I2C
        depends on I2C
```

⚠️ **"无 prompt 的公共核心 + 带 prompt 的每种总线一个"** ——
这个模式在 `drivers/mfd/Kconfig` 里反复出现（madera、arizona、ocelot 都是）。

⚠️ **`MFD_SYSCON` 是例外**：

```
config MFD_SYSCON               /* drivers/mfd/Kconfig:1429-1431 */
        bool "System Controller Register R/W Based on Regmap"
        select REGMAP_MMIO
```

⚠️⚠️ **它是 `bool` 而不是 `tristate`，而且不 `select MFD_CORE`**
⇒ 两条结论：**syscon 编进内核就永远在**（与 02 §6.3 的"表项永不移除"自洽），
以及 **syscon 与 mfd 核心在编译上毫无依赖** —— 它只是住在同一个目录里（01 §4）。

**Makefile 形态**（`drivers/mfd/Makefile`）：

| 行 | 内容 | 说明 |
|---|---|---|
| `:121` | `obj-$(CONFIG_MFD_CORE) += mfd-core.o` | 核心 |
| `:229` | `obj-$(CONFIG_MFD_SYSCON) += syscon.o` | syscon |
| `:147-149` | `axp20x.o` / `axp20x-i2c.o` / `axp20x-rsb.o` | ⚠️ 三个独立 `.o`，对应三个 config |
| `:78-94` | `madera-objs := madera-core.o` + 5 个 `*-tables.o` → `madera.o` | ⚠️ **多文件合并成一个模块** |
| `:123-124` | `ocelot-soc-objs := ocelot-core.o ocelot-spi.o` | 同上 |

⚠️ `madera-objs` 那五个 `*-tables.o`（`:80` `:83` `:86` `:89` `:92`）**各自由独立 config 控制**
⇒ 只编需要的芯片型号的寄存器表。**这是 mfd 目录里 11 万行有一半是寄存器表**（基线表）**却不臃肿的原因。**

---

## §8 写新驱动时怎么选

| 你的情况 | 选什么 | 依据 |
|---|---|---|
| SoC 内部一段 MMIO，子块各有 `reg` | ⚠️ **DT 写 `simple-mfd`，不写驱动** | §6 |
| 别人只想读写你这块杂项寄存器 | ⚠️ **DT 加 `"syscon"`，不写驱动** | §5、06 §3 |
| 挂在 I2C/SPI 上的芯片，功能块各自有 DT 子节点 | `devm_mfd_add_devices()` + 每个 cell 填 `of_compatible` | §2.1 |
| 同一个 `of_compatible` 有多个子节点 | ⚠️ **必须 `use_of_reg = true` + `of_reg`** | §4.1 |
| 芯片有一根 INT 脚要分给多个功能块 | `regmap_add_irq_chip()`；**要么传 `domain`，要么让子驱动 `regmap_irq_get_virq()`** | §2.2 |
| 子设备之间有供电/时序依赖 | ⚠️ **`level = MFD_DEP_LEVEL_HIGH` + 不能用 devm + 手动调两个 remove** | §3 |
| USB 上的桥片，可插多个 | `mfd_add_hotplug_devices()`（01 §2.6） | 01 §2.6 |
| 寄存器访问不是 MMIO（SPI/I2C 上的寄存器空间） | ⚠️ **用 `IORESOURCE_REG` + 父设备上按名字挂 regmap** | §4.2、§4.3 |
| 子驱动要用父芯片的供电轨名字 | `parent_supplies` + `num_parent_supplies`（02 §2.8） | §3 |

⚠️⚠️ **三条硬性提醒**：
1. **`num_resources` 必须自己填对**（或用 `MFD_RES_SIZE`）—— 核心不校验（01 §2.1.1）。
2. **`platform_data` 和 `pdata_size` 必须成对**（02 §8 陷阱 1）。
3. ⚠️ **cell 数组一定写 `static const`** —— 副本是浅拷贝，`resources` 指针指回原数组（03 §2.1）。

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 这些驱动调的那 6 个接口的签名 | 01-api.md |
| `mfd_cell` 每个字段的含义 | 02-datastructures.md |
| 核心是怎么处理这些字段的 | 03-internals.md |
| 我要从零写一个 mfd 父驱动 | 05-usage.md |
| DT 里怎么写 `simple-mfd` / `syscon` | 06-devicetree.md |
| 子设备没出现 / 中断不对 | 07-debug.md |
| 为什么 mfd 能只有 471 行 | 08-feynman.md |
| 本篇数字的取证口径 | 99-evidence.md |
