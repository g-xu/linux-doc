---
title: API 参考
---

# mfd 01：对外接口

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ **本子系统 = `drivers/mfd/`（Multi-Function Device）。** 它做的事只有一件：
**把一颗"里面塞了七八个不相干功能块"的芯片，拆成若干个 `platform_device`，
交给各自的子系统驱动去认领。**

⚠️⚠️ **开篇必须先说一件事**：`Documentation/driver-api/` 下**没有 mfd 这一篇**，
`Documentation/mfd/` 目录也不存在。全树关于这个框架的官方文字只有
**`Documentation/devicetree/bindings/mfd/mfd.txt` 51 行**。
⇒ **本篇所有语义都直接来自代码，没有官方 API 文档可对照**（取证限制见 99 §1.4）。

---

## §1 接口全景

`drivers/mfd/` 里导出符号的只有两个文件，**而且用的宏不是一种**：

| 组 | 文件 | 导出数 | 宏 | 干什么 |
|---|---|---|---|---|
| **mfd 核心** | `drivers/mfd/mfd-core.c` | **6** | ⚠️ 全部裸 `EXPORT_SYMBOL` | 把 cell 数组变成子设备 |
| **syscon** | `drivers/mfd/syscon.c` | **7** | 全部 `EXPORT_SYMBOL_GPL` | 按 DT 节点拿到一个 regmap |

⚠️ **两个宏口径在同一个目录里并存**：核心 6 个是**非 GPL 限定**的（历史遗留，
2006 年的代码），syscon 7 个是 GPL-only。**写非 GPL 模块时这个差别是实际存在的。**

**核心六个**（`drivers/mfd/mfd-core.c`）：

| 符号 | 位置 | 一句话 |
|---|---|---|
| `mfd_add_devices()` | `:349`（导出在 `:372`） | 主入口：注册 n 个子设备 |
| `devm_mfd_add_devices()` | `:444`（导出在 `:468`） | 上面那个的 devm 版；⚠️ **有一个坑，见 §2.2** |
| `mfd_remove_devices()` | `:414`（导出在 `:420`） | 删 `level <= NORMAL` 的子设备 |
| `mfd_remove_devices_late()` | `:406`（导出在 `:412`） | 删 `level <= HIGH` 的子设备 |
| `mfd_cell_enable()` | `:36`（导出在 `:47`） | ⚠️ **近乎死 API**，全树只有 1 个真实调用者 |
| `mfd_cell_disable()` | `:49`（导出在 `:60`） | 同上 |

**还有一个不导出但天天用的**：

| 符号 | 位置 | 说明 |
|---|---|---|
| `mfd_get_cell()` | `include/linux/mfd/core.h:139-142` | ⚠️ **`static inline`，直接读 `pdev->mfd_cell`** ⇒ 不需要导出，子驱动包含头文件即可用（全树 9 个文件） |

**syscon 七个**（`drivers/mfd/syscon.c`，原型全在 `include/linux/mfd/syscon.h:20-32`）：

| 符号 | 位置 | 一句话 |
|---|---|---|
| `syscon_regmap_lookup_by_phandle()` | `:254`（导出 `:275`） | ⚠️ **最常用的一个**：按 phandle 属性拿 regmap |
| `syscon_regmap_lookup_by_phandle_optional()` | `:311`（导出 `:322`） | 同上，但"属性不存在"返回 `NULL` 而不是错误 |
| `syscon_regmap_lookup_by_phandle_args()` | `:277`（导出 `:304`） | 同上，外加取出 phandle 后面的固定个数参数 |
| `syscon_regmap_lookup_by_compatible()` | `:238`（导出 `:252`） | ⚠️ 全局按 compatible 找第一个匹配节点 |
| `syscon_node_to_regmap()` | `:229`（导出 `:236`） | 要求节点带 `compatible = "syscon"` |
| `device_node_to_regmap()` | `:223`（导出 `:227`） | ⚠️ **不检查 compatible**，任意节点都行 |
| `of_syscon_register_regmap()` | `:186`（导出 `:221`） | 把外部建好的 regmap 塞进 syscon 表 |

---

## §2 mfd 核心六个接口

### §2.1 `mfd_add_devices()` —— 唯一的主入口

```c
/* drivers/mfd/mfd-core.c:349-352 */
int mfd_add_devices(struct device *parent, int id,
                    const struct mfd_cell *cells, int n_devs,
                    struct resource *mem_base,
                    int irq_base, struct irq_domain *domain);
```

| 参数 | 含义 | ⚠️ |
|---|---|---|
| `parent` | 父设备（那颗多功能芯片本身的 `struct device`） | 子设备的 `dev.parent`、DMA 掩码、DT 节点都从它来（`:201-205`） |
| `id` | 子设备编号基数 | ⚠️ **传 `PLATFORM_DEVID_AUTO` 时 `cell->id` 被完全忽略**（`:184-187`） |
| `cells` | `struct mfd_cell` 数组（02 §2） | `const`，核心会 `kmemdup` 一份（`:193`） |
| `n_devs` | 数组长度 | 常用 `ARRAY_SIZE()` |
| `mem_base` | ⚠️ **父设备的寄存器窗口**，cell 里的 `IORESOURCE_MEM` 是**相对它的偏移** | `:266-271`；传 `NULL` 则 cell 里的地址被当绝对值用（`:286-288`） |
| `irq_base` | 中断号基数；⚠️ **`domain` 非空时被忽略**（`:272-284`） | 见 §2.1.2 |
| `domain` | 中断域（父芯片自己的 irqchip） | 有它就走 `irq_create_mapping()`（`:277`） |

**返回值**：0 成功；负值失败。⚠️ **失败时会把这一批里已经建好的子设备全部删掉**：

```c
/* drivers/mfd/mfd-core.c:366-370 */
fail:
        if (i)
                mfd_remove_devices(parent);
        return ret;
```

⚠️⚠️ **这个回滚是不完整的**：它调的是 `mfd_remove_devices()`
（`MFD_DEP_LEVEL_NORMAL`），⇒ **`level = MFD_DEP_LEVEL_HIGH` 的 cell 不会被回滚**
（判断在 `:387-388`）。全树只有一个驱动用 `HIGH`（`drivers/mfd/madera-core.c:52`），
所以这个漏洞在实践中没被触发过 —— 但它确实在那里。

#### §2.1.1 ⚠️ 只有两种失败可能

错误码普查（`drivers/mfd/mfd-core.c` 全文）：

| 错误码 | 产生它的行 | 来自哪里 |
|---|---|---|
| `-ENOMEM` | `:180` · `:158` · `:454` | ⚠️ **`:180` 是个初值**（`int ret = -ENOMEM;`）—— `platform_device_alloc`(`:189`)、`kmemdup`(`:193`)、`kcalloc`(`:197`) 三处失败都不单独赋值，直接沿用它；`:158` 是 OF 表项的 `kzalloc`；`:454` 是 devm 的 `devres_alloc` |
| `-EAGAIN` | `:137` · `:147` · `:153` | ⚠️ **全部是"这个 DT 节点不是我要的，继续找下一个"的内部信号**，在 `:224` 被吃掉，**永远不会返回给调用者** |

⚠️⚠️ **`:180` 那个初值写法值得单独记**：三条分配失败路径共用一个预置的
`-ENOMEM`，**中间任何一次成功都不会把它清掉** ——
只有到 `:207` 的 `regulator_bulk_register_supply_alias()` 才第一次给 `ret` 赋新值。
⇒ 读代码时不要在 `:189`–`:199` 附近找"错误码在哪设的"，**它在函数开头**。

⇒ 于是 `mfd_add_devices()` 对外只可能返回两种值：**0，或 `-ENOMEM`**
（外加子调用透传的错误：regulator 别名注册 `:207`、`platform_device_add_data` `:249`、
`device_add_software_node` `:256`、`platform_device_add_resources` `:300`、
`platform_device_add` `:304`）。

⚠️⚠️ **核心一个 `-EINVAL` 都没有，一个 `-ENODEV` 都没有** ⇒
**它不校验任何参数**：`cells` 传 `NULL`、`n_devs` 传负数、`cell->name` 传 `NULL`
都不会被挡住（往下会在 `platform_device_alloc()` 或更深处炸）。
⚠️ 对比 pwm 核心 15 个 `-EINVAL`、dma-buf 47 个 —— **这是本套见过校验最少的核心**。

#### §2.1.2 中断资源的两条路

```c
/* drivers/mfd/mfd-core.c:272-284 */
} else if (cell->resources[r].flags & IORESOURCE_IRQ) {
        if (domain) {
                /* Unable to create mappings for IRQ ranges. */
                WARN_ON(cell->resources[r].start != cell->resources[r].end);
                res[r].start = res[r].end = irq_create_mapping(
                        domain, cell->resources[r].start);
        } else {
                res[r].start = irq_base + cell->resources[r].start;
                res[r].end   = irq_base + cell->resources[r].end;
        }
}
```

⚠️⚠️ **走 `domain` 这条路时，一条资源只能是一个中断，不能是范围**
—— 注释和 `WARN_ON`（`:275-276`）说得很清楚。
⚠️ 但它是 `WARN_ON` 而不是返回错误 ⇒ **范围写错了照样注册成功**，
`res[r].start`/`end` 都被设成 `start` 那一个映射，**`end` 静默丢失**。

⚠️ 走 `irq_base` 这条路（`domain == NULL`）时 cell 里写的是相对偏移，
`irq_base` 是绝对基数。**两条路对 cell 内容的解释完全一致（都是相对值），
区别只在"相对谁"**。

### §2.2 `devm_mfd_add_devices()` —— 带一个真实缺陷

```c
/* drivers/mfd/mfd-core.c:444-447 */
int devm_mfd_add_devices(struct device *dev, int id,
                         const struct mfd_cell *cells, int n_devs,
                         struct resource *mem_base,
                         int irq_base, struct irq_domain *domain);
```

参数与 §2.1 完全相同（第一个参数既是 devres 宿主也是 parent）。
kerneldoc 在 `:427-443`，说的是"unbind 时自动删掉所有子设备"。

⚠️⚠️ **但释放回调只调了 `mfd_remove_devices()`**：

```c
/* drivers/mfd/mfd-core.c:422-425 */
static void devm_mfd_dev_release(struct device *dev, void *res)
{
        mfd_remove_devices(dev);
}
```

⇒ **`level = MFD_DEP_LEVEL_HIGH` 的 cell 用 devm 版本注册后永远删不掉**。
kerneldoc 里写的 "All child-devices of the MFD will automatically be removed"
（`:431-432`）**在有 HIGH level cell 时是不成立的**。
⚠️ **用 `MFD_DEP_LEVEL_HIGH` 就不能用 devm 版本** —— 这条约束在代码和文档里都没写。

### §2.3 `mfd_remove_devices()` / `mfd_remove_devices_late()`

```c
/* drivers/mfd/mfd-core.c:406-419 */
void mfd_remove_devices_late(struct device *parent);   /* level <= HIGH   */
void mfd_remove_devices(struct device *parent);        /* level <= NORMAL */
```

两个都返回 `void`，**都不可能失败**。实现差别只有一个常量：

| 函数 | `level` 变量 | 删掉哪些 |
|---|---|---|
| `mfd_remove_devices()` | `MFD_DEP_LEVEL_NORMAL`（=0，`include/linux/mfd/core.h:49`） | 只删 `cell->level <= 0` |
| `mfd_remove_devices_late()` | `MFD_DEP_LEVEL_HIGH`（=1，`:50`） | 删 `cell->level <= 1`，即全部 |

两者都走同一个 `device_for_each_child_reverse(parent, &level, mfd_remove_devices_fn)`
（`:410`/`:418`），过滤在 `mfd_remove_devices_fn()` 里（`:387-388`）：

```c
if (level && cell->level > *level)
        return 0;
```

⚠️⚠️ **父驱动的 remove 必须按顺序调两次**：先 `mfd_remove_devices()`
（拆普通子设备），做完自己的清理，再 `mfd_remove_devices_late()`（拆 HIGH 的）。
**只调前者 ⇒ HIGH 的子设备泄漏；只调后者 ⇒ 顺序保证失效。**

⚠️ 全树只有 `drivers/mfd/madera-core.c:782` 调了 `_late` 版本
⇒ **这套 level 机制是为一个驱动存在的**（05 §3 会解释它为什么需要）。

⚠️ 反向遍历（`_reverse`）不是可选的：子设备之间有依赖（比如 regulator 子设备
要比 用它的 codec 子设备后删），**注册顺序的逆序就是安全的拆解顺序**。

### §2.4 `mfd_cell_enable()` / `mfd_cell_disable()`

```c
/* drivers/mfd/mfd-core.c:36-46 */
int mfd_cell_enable(struct platform_device *pdev)
{
        const struct mfd_cell *cell = mfd_get_cell(pdev);

        if (!cell->enable) {
                dev_dbg(&pdev->dev, "No .enable() call-back registered\n");
                return 0;
        }
        return cell->enable(pdev);
}
```

`disable` 是完全对称的（`:49-59`）。**返回值就是回调的返回值；回调不存在时返回 0。**

⚠️⚠️ **这两个是近乎死掉的 API**：全树除了 `mfd-core.c` 自己，
只有 **`drivers/leds/leds-asic3.c`** 一个调用者。

⚠️ `include/linux/mfd/core.h:126-131` 的注释说它们带引用计数
（"Refcounting happens automatically, with the cell's enable/disable callbacks
being called only when a device is first being enabled"）——
⚠️⚠️ **但 6.1 的实现里没有任何引用计数**，每次调用都直接透传给回调。
**注释是陈旧的，以代码为准**（这是本子系统的第一处陈旧注释，99 §9 登记）。

### §2.5 `mfd_get_cell()` —— 子驱动侧唯一需要的接口

```c
/* include/linux/mfd/core.h:139-142 */
static inline const struct mfd_cell *mfd_get_cell(struct platform_device *pdev)
{
        return pdev->mfd_cell;
}
```

⚠️⚠️ **`struct platform_device` 里真有一个 `struct mfd_cell *mfd_cell` 字段**
（`include/linux/platform_device.h:41`，前置声明在 `:19`）
⇒ **mfd 往内核核心结构里打了一个洞**。这是它"不需要自己的 bus_type"的手段：
子设备就是普通 platform_device，只是多挂了一个指针（02 §5、08 §3）。

⚠️ 返回值**可能是 `NULL`** —— 如果这个 platform_device 不是 mfd 建的。
子驱动如果既能被 mfd 创建也能被 DT 直接实例化，**必须判空**。

### §2.6 `mfd_add_hotplug_devices()` —— 头文件里的第二个 inline

```c
/* include/linux/mfd/core.h:149-154 */
static inline int mfd_add_hotplug_devices(struct device *parent,
                const struct mfd_cell *cells, int n_devs)
{
        return mfd_add_devices(parent, PLATFORM_DEVID_AUTO, cells, n_devs,
                        NULL, 0, NULL);
}
```

⚠️ **它不是新功能，只是把四个参数固定住**：`id = PLATFORM_DEVID_AUTO`、
`mem_base = NULL`、`irq_base = 0`、`domain = NULL`。

⇒ **适用形态很明确**：**USB/热插拔类的多功能芯片**
—— 设备可以插拔多个同型号，所以编号必须自动分配；
寄存器不是 MMIO（走 USB/I2C 传输），所以没有 `mem_base` 可切片。

⚠️ 全树 6 个使用者，正好都符合这个形态：
`drivers/mfd/dln2.c` · `drivers/mfd/viperboard.c` · `drivers/mfd/cros_ec_dev.c` ·
`drivers/hid/hid-sensor-hub.c` · `drivers/misc/cardreader/rtsx_usb.c` ·
`drivers/gpu/drm/amd/amdgpu/amdgpu_acp.c`。

⚠️ 用了它就**必须**配 `mfd_remove_devices()`（它没有 devm 版本），
而且 `PLATFORM_DEVID_AUTO` 意味着 `/sys/bus/platform/devices/` 里的名字带随机编号
⇒ **不要在 udev 规则或脚本里硬编码这类子设备名**（07 §1）。

---

## §3 `MFD_CELL_*` 宏族

`include/linux/mfd/core.h` 提供 8 个宏，**全部是 `MFD_CELL_ALL` 的偏特化**：

```c
/* include/linux/mfd/core.h:17-29 —— 根宏，九个参数 */
#define MFD_CELL_ALL(_name, _res, _pdata, _pdsize, _id, _compat, _of_reg, _use_of_reg, _match)
```

| 宏 | 位置 | 固定掉了什么 |
|---|---|---|
| `MFD_CELL_ALL` | `:17` | —— 九参数全给 |
| `MFD_CELL_OF_REG` | `:31` | `_use_of_reg = true`、`_match = NULL` |
| `MFD_CELL_OF` | `:34` | `_of_reg = 0`、`_use_of_reg = false`、`_match = NULL` |
| `MFD_CELL_ACPI` | `:37` | ⚠️ `_compat = NULL`（ACPI 路径，arm64 无关，99 §2.7） |
| `MFD_CELL_BASIC` | `:40` | 无 OF、无 ACPI |
| `MFD_CELL_RES` | `:43` | 只给名字和资源 |
| `MFD_CELL_NAME` | `:46` | ⚠️ **只给名字**，其余全零 |

配套的 `MFD_RES_SIZE(arr)`（`:15`）= `sizeof(arr) / sizeof(struct resource)`
⇒ ⚠️ **宏会自动算 `num_resources`，手写 cell 时这一项最容易漏。**

⚠️⚠️ **但这套宏几乎没人用**：全树只有 **8 个 `.c`** 出现 `MFD_CELL_`
（`drivers/mfd/` 下 7 个：`at91-usart.c` · `mt6370.c` · `rt5120.c` · `rt4831.c` ·
`db8500-prcmu.c` · `mt6360-core.c` · `ab8500-core.c`），
而 `mfd_add_devices` 的调用者有 **134 个文件**
⇒ **绝大多数驱动手写 `.name = …, .of_compatible = …` 的指定初始化器。**

⚠️ 原因可推：宏的参数是**位置参数且有 9 个**，可读性比指定初始化器差。
**新代码建议直接写指定初始化器，不用这套宏**（05 §2）。

---

## §4 syscon 七个接口

⚠️ **syscon 严格说不是 mfd 机制**：它不建子设备，不用 `mfd_cell`。
它住在 `drivers/mfd/` 里的理由是
`Documentation/devicetree/bindings/mfd/mfd.txt:14-16` 那个定义
——"一段杂项系统寄存器"也算一种多功能设备。
**它实际提供的东西是：给定一个 DT 节点，返回一个 regmap。**

### §4.1 四个查找接口（按使用频率）

```c
/* include/linux/mfd/syscon.h:23-30 */
struct regmap *syscon_regmap_lookup_by_phandle(struct device_node *np,
                                               const char *property);
struct regmap *syscon_regmap_lookup_by_phandle_optional(struct device_node *np,
                                                        const char *property);
struct regmap *syscon_regmap_lookup_by_phandle_args(struct device_node *np,
                                                    const char *property,
                                                    int arg_count,
                                                    unsigned int *out_args);
struct regmap *syscon_regmap_lookup_by_compatible(const char *s);
```

| 接口 | 实现 | 返回 | ⚠️ |
|---|---|---|---|
| `_by_phandle` | `drivers/mfd/syscon.c:254-274` | regmap 或 `ERR_PTR` | ⚠️ **`property` 传 `NULL` 时把 `np` 自己当 syscon 节点**（`:260-263`），这是个隐藏用法 |
| `_by_phandle_optional` | `:311-321` | ⚠️ **regmap / `NULL` / `ERR_PTR` 三态** | 只把 `-ENODEV` 转成 `NULL`（`:317-318`），其它错误照样返回 |
| `_by_phandle_args` | `:277-303` | 同 `_by_phandle`，外加填 `out_args[]` | ⚠️ **`out_args` 只在成功路径填**（`:298-299`），且**不检查 `arg_count` 与数组容量是否匹配** |
| `_by_compatible` | `:238-251` | 同 `_by_phandle` | ⚠️⚠️ **全局搜索，返回第一个匹配** ⇒ 一块板上有两个同 compatible 的 syscon 时结果不确定 |

⚠️⚠️ **`_by_phandle_optional` 的三态返回是最容易写错的一处**：

```c
regmap = syscon_regmap_lookup_by_phandle_optional(np, "foo-syscon");
if (IS_ERR(regmap))
        return PTR_ERR(regmap);   /* 真错误 */
if (!regmap)
        /* 属性不存在，这是允许的 —— 不要在这里 return */ ;
```

⚠️ 漏掉 `!regmap` 这一支 ⇒ 后面拿 `NULL` 去 `regmap_read()` 直接空指针。
⚠️ 而 `CONFIG_MFD_SYSCON=n` 时这个桩函数**也返回 `NULL`**
（`include/linux/mfd/syscon.h:65-70`）⇒ **两种"合法的 NULL"混在一起**。

### §4.2 两个节点转换接口

```c
struct regmap *syscon_node_to_regmap(struct device_node *np);   /* :229 */
struct regmap *device_node_to_regmap(struct device_node *np);   /* :223 */
```

⚠️ **唯一区别是要不要检查 compatible**：

```c
/* drivers/mfd/syscon.c:229-235 */
struct regmap *syscon_node_to_regmap(struct device_node *np)
{
        if (!of_device_is_compatible(np, "syscon"))
                return ERR_PTR(-EINVAL);
        return device_node_get_regmap(np, true);
}
```

而 `device_node_to_regmap()` 直接 `return device_node_get_regmap(np, false)`（`:225`）。

⚠️⚠️ **第二个参数 `check_clk` 也跟着变了**：`syscon_node_to_regmap` 传 `true`，
`device_node_to_regmap` 传 `false`
⇒ **只有走 `syscon_node_to_regmap()` 这条路才会去取并挂上节点的时钟**
（`drivers/mfd/syscon.c:120-132`）。
⚠️ **这不是一个"少一次检查"的区别，而是"少一个功能"的区别** ——
用 `device_node_to_regmap()` 拿到的 regmap 在寄存器块需要时钟才能访问的
SoC 上会挂死或读回全 0。这一点两个函数的名字完全没有暗示。

### §4.3 `of_syscon_register_regmap()`

```c
/* drivers/mfd/syscon.c:186 —— kerneldoc 在 :175-184 */
int of_syscon_register_regmap(struct device_node *np, struct regmap *regmap);
```

把一个**外部建好的** regmap 登记到 syscon 表里，之后别人用
`syscon_regmap_lookup_by_phandle()` 就能拿到它。

| 返回 | 条件 |
|---|---|
| 0 | 成功（`:214`） |
| `-EINVAL` | ⚠️ `np` 或 `regmap` 为 `NULL`（`:191-192`）——**syscon 里唯一的参数校验** |
| `-ENOMEM` | 表项分配失败（`:194-196`） |
| `-EEXIST` | ⚠️ **该节点已经登记过**（`:201-205`） |

⚠️ 用途：某些寄存器块需要特殊的 regmap 配置（非 MMIO、带 hwspinlock、
特殊 endian），**syscon 自己的通用 probe 路径做不出来**，
就由那个驱动自己建 regmap 再注册进来。

---

## §5 返回值总表

| 接口 | 成功 | 失败形态 |
|---|---|---|
| `mfd_add_devices` | 0 | ⚠️ 只有 `-ENOMEM`；**且"DT 子节点 disabled"也返回 0**（§2.1、03 §2.4） |
| `devm_mfd_add_devices` | 0 | `-ENOMEM` 或透传上面的 |
| `mfd_remove_devices` / `_late` | `void` | ⚠️ **不可能失败** |
| `mfd_cell_enable` / `_disable` | 回调返回值；⚠️ **无回调时返回 0** | 回调自己定 |
| `mfd_get_cell` | cell 指针 | ⚠️ **可能 `NULL`**（不是 `ERR_PTR`） |
| syscon 全部 `lookup_*` | regmap 指针 | ⚠️ **`ERR_PTR`，必须 `IS_ERR()`**；`_optional` 另有 `NULL` 一态 |
| `of_syscon_register_regmap` | 0 | `-EINVAL` / `-ENOMEM` / `-EEXIST` |

⚠️⚠️ **两套返回值约定并存**：mfd 核心用 `int` + `NULL`，syscon 用 `ERR_PTR`。
**同一个目录、同一个头文件目录下的两套接口，判错方式不一样。**

---

## §6 `CONFIG=n` 时的桩函数

⚠️ **mfd 核心没有桩函数** —— `include/linux/mfd/core.h` 里没有任何
`#ifdef CONFIG_MFD_CORE` 分支。⇒ **`CONFIG_MFD_CORE=n` 时调 `mfd_add_devices()`
是链接错误，不是运行时错误**。这是有意的：所有需要它的驱动都在 Kconfig 里
`select MFD_CORE`（04 §1）。

**syscon 有完整的桩**（`include/linux/mfd/syscon.h:33-78`）：

| 接口 | `=y` | ⚠️ `=n` 桩返回 |
|---|---|---|
| `device_node_to_regmap` | regmap | `ERR_PTR(-ENOTSUPP)`（`:36`） |
| `syscon_node_to_regmap` | regmap | `ERR_PTR(-ENOTSUPP)`（`:41`） |
| `syscon_regmap_lookup_by_compatible` | regmap | `ERR_PTR(-ENOTSUPP)`（`:46`） |
| `syscon_regmap_lookup_by_phandle` | regmap | `ERR_PTR(-ENOTSUPP)`（`:53`） |
| `syscon_regmap_lookup_by_phandle_args` | regmap | `ERR_PTR(-ENOTSUPP)`（`:62`） |
| `syscon_regmap_lookup_by_phandle_optional` | regmap / `NULL` | ⚠️⚠️ **`NULL`**（`:69`） |
| `of_syscon_register_regmap` | 0 | ⚠️ **`-EOPNOTSUPP`**（`:75`），不是 `-ENOTSUPP` |

⚠️⚠️ **两处不一致**：
1. `_optional` 的桩返回 `NULL` ⇒ **`CONFIG_MFD_SYSCON=n` 时它看起来像"属性不存在"**，
   驱动会以为"这是可选的，跳过就行"而继续跑，**而不是发现 syscon 根本没编进来**。
2. 六个函数用 `-ENOTSUPP`，`of_syscon_register_regmap` 用 `-EOPNOTSUPP`
   —— ⚠️ `-ENOTSUPP` 本身是**不应该返回给用户态的内部错误码**
   （`include/linux/errno.h`），这里只在内核内部传递，尚可；但**同一个头文件里
   两种拼法**说明这是历史累积。

---

## §7 这个子系统没有的东西

⚠️ 明确列出来，省得读者去找：

| 常见设施 | mfd 有没有 |
|---|---|
| uapi 头 / ioctl | ⚠️ **没有**（`include/uapi/linux/` 下无 mfd 相关头） |
| 字符设备 / misc 设备 | **没有** |
| sysfs 属性 / class | ⚠️ **没有**（`mfd-core.c` 里 `sysfs`/`class_create`/`DEVICE_ATTR` 零命中） |
| debugfs | **没有** |
| tracepoint | ⚠️ **没有**（`include/trace/events/` 下无 mfd 头） |
| 自己的 `bus_type` | ⚠️ **没有** —— 子设备走 platform bus（08 §3） |
| 官方 API 文档 | ⚠️⚠️ **没有**（`Documentation/driver-api/mfd*` 不存在） |
| selftest | **没有** |

⇒ **这个子系统的可观测性完全依赖 `/sys/bus/platform/devices/` 里子设备存不存在**
（07 §1）。**它没有任何自己的调试接口。**

⚠️ 唯一的日志出口是 `mfd_add_device()` 里那条 `pr_warn`
（`drivers/mfd/mfd-core.c:242-243`，"Failed to locate of_node"）
和两条 `dev_dbg`（`:41`/`:54`）。**三条，全文件就这三条。**

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| `struct mfd_cell` 那 20 个字段各干什么 | 02-datastructures.md |
| `mfd_add_device()` 那 165 行到底怎么走 | 03-internals.md |
| 挑一个真实芯片驱动看完整写法 | 04-extensions.md |
| 我要写一个 mfd 父驱动 | 05-usage.md |
| DT 里 `simple-mfd` 和 mfd 什么关系 | 06-devicetree.md |
| 子设备没出现，怎么查 | 07-debug.md |
| 为什么它不需要自己的 bus | 08-feynman.md |
| 上面这些结论的证据 | 99-evidence.md |
