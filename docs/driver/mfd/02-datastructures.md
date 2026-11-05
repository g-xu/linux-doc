---
title: 数据结构
---

# mfd 02：数据结构

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇口径：**字段数按花括号深度数，不数注释行**；本子系统的结构**全部以 `};` 结尾**，
没有 `__randomize_layout`（tty 那个坑在这里不存在，99 §6）。

---

## §1 结构全景：一共只有 5 个

⚠️ `include/linux/mfd/` 下有 **231 个头文件、72605 行**，但其中 **229 个是单芯片寄存器定义**
（`wm8994/registers.h`、`madera/registers.h` 之类）。
**属于框架的只有 `core.h` 和 `syscon.h` 两个。**

| 结构 | 位置 | 字段 | 谁填 | 谁读 |
|---|---|---|---|---|
| `struct mfd_cell` | `include/linux/mfd/core.h:66-124` | **20** | 父芯片驱动 | `mfd_add_device()` |
| `struct mfd_cell_acpi_match` | `include/linux/mfd/core.h:56-59` | **2** | 父芯片驱动 | ⚠️ 只有 ACPI 路径（99 §2.7） |
| `struct mfd_of_node_entry` | `drivers/mfd/mfd-core.c:26-30` | **3** | 核心 | ⚠️ 核心私有，头文件里没有 |
| `struct syscon` | `drivers/mfd/syscon.c:33-37` | **3** | syscon | syscon 私有 |
| ⚠️ **`struct platform_device` 的一个字段** | `include/linux/platform_device.h:41` | —— | 核心 | 子驱动（`mfd_get_cell()`） |

⚠️⚠️ **注意最后一行**：mfd **没有自己的设备结构**。
它不定义 `struct mfd_device`，不定义 `struct mfd_chip`，
子设备就是**原样的 `struct platform_device`**，只是多了一个指针（§5）。

⇒ **这是理解整个子系统的关键**：`struct mfd_cell` 不是一个"设备对象"，
**它是一张"怎么造设备"的图纸**，用完就被复制成 platform data 然后不再被查询
（除了 `mfd_get_cell()` 这一条回读路径）。

---

## §2 `struct mfd_cell` —— 20 个字段

⚠️ 头文件里的注释（`include/linux/mfd/core.h:61-65`）自己说明了它的性质：

> After registration the copy of this structure will become the platform data
> of the resulting platform_device

⇒ ⚠️ **cell 被 `kmemdup` 复制一份**（`drivers/mfd/mfd-core.c:193`），
**副本挂在子设备的 `pdev->mfd_cell` 上**。父驱动可以把 cell 数组声明成
`static const`，注册完就不用留着。

### §2.1 身份三字段

| 字段 | 位置 | 类型 | 说明 |
|---|---|---|---|
| `name` | `:67` | `const char *` | ⚠️ **子设备的 platform_device 名，也是匹配子驱动的依据**（`drivers/mfd/mfd-core.c:189`） |
| `id` | `:68` | `int` | 加到 `mfd_add_devices()` 的 `id` 上（`:187`）；⚠️ **`id = PLATFORM_DEVID_AUTO` 时它被完全忽略**（`:184-185`） |
| `level` | `:69` | `int` | 拆解阶段；只有 `MFD_DEP_LEVEL_NORMAL`(0) 和 `_HIGH`(1) 两个值（`:49-50`） |

⚠️⚠️ **`name` 有两条完全不同的匹配后果**：
- 有 `of_compatible` ⇒ 子设备拿到 DT 节点，子驱动靠 `of_match_table` 匹配；
- 没有 ⇒ **只能靠 `name` 字符串和 `platform_driver.driver.name` 相等来匹配**。
⇒ 写错一个字符的症状是"子设备在 `/sys/bus/platform/devices/` 里有，但没驱动绑定"（07 §2）。

⚠️ **`level` 的实际使用面极窄**：全树只有 `drivers/mfd/madera-core.c:52` 设过
`MFD_DEP_LEVEL_HIGH`。也就是说这 20 个字段里有一个是**为一个驱动加的**（01 §2.3）。

### §2.2 四个回调，其中两个是死字段

| 字段 | 位置 | 核心里被调的地方 |
|---|---|---|
| `enable` | `:71` | `mfd_cell_enable()`，`drivers/mfd/mfd-core.c:40` `:45` |
| `disable` | `:72` | `mfd_cell_disable()`，`:53` `:58` |
| ⚠️⚠️ `suspend` | `:74` | **核心里零引用** |
| ⚠️⚠️ `resume` | `:75` | **核心里零引用** |

⚠️⚠️ **`suspend`/`resume` 是两个死字段。**
把 `mfd-core.c` 全文里所有 `cell->` 的引用列出来（471 行，共 40 处），
**里面没有 `cell->suspend`，也没有 `cell->resume`**
⇒ **填了也永远不会被调用**。
⇒ 子设备的电源管理必须走**标准的 `platform_driver.driver.pm`**，
`mfd_cell` 里这两个字段是 2006 年设计的残留（99 §9）。

⚠️ `enable`/`disable` 也几乎是死的：`include/linux/mfd/core.h:126-131` 的注释承诺
"Refcounting happens automatically"，**但实现里没有任何引用计数**（01 §2.4），
而且全树只有 `drivers/leds/leds-asic3.c` 一个调用者。

### §2.3 平台数据与软件节点

| 字段 | 位置 | 说明 |
|---|---|---|
| `platform_data` | `:78` | ⚠️ **不是直接挂上去，而是 `platform_device_add_data()` 复制一份**（`drivers/mfd/mfd-core.c:249-250`） |
| `pdata_size` | `:79` | ⚠️ **为 0 时整个 `platform_data` 被跳过**（`:248`）——填了指针忘填 size 是静默失败 |
| `swnode` | `:82` | `const struct software_node *`；`device_add_software_node()`（`:255-256`），移除在 `:316` 和 `:390` |

⚠️⚠️ **`pdata_size = 0` 这个坑值得单独记**：

```c
/* drivers/mfd/mfd-core.c:248-253 */
if (cell->pdata_size) {
        ret = platform_device_add_data(pdev,
                        cell->platform_data, cell->pdata_size);
        ...
}
```

⇒ 只判断 `pdata_size`，**完全不判断 `platform_data` 是否非空**。
填了 `.platform_data = &my_data` 却忘了 `.pdata_size = sizeof(my_data)`
⇒ **子驱动的 `dev_get_platdata()` 返回 `NULL`，注册却是成功的**。

⚠️ `swnode` 的使用面：全树只有 3 个文件（`drivers/mfd/intel-lpss-pci.c` ·
`intel-lpss-acpi.c` · `axp20x.c`），**前两个是 x86**
⇒ arm64 上真正用到 `swnode` 的只有 `axp20x.c`（04 §3）。

### §2.4 DT 匹配三字段

| 字段 | 位置 | 说明 |
|---|---|---|
| `of_compatible` | `:88` | ⚠️ **在父节点的直接子节点里找 compatible 匹配的那个**（`drivers/mfd/mfd-core.c:214-216`） |
| `of_reg` | `:95` | `u64`；⚠️ 当**两个子节点 compatible 相同**时用地址区分（`:151`） |
| `use_of_reg` | `:98` | `bool`；⚠️ **必须显式置 `true` 才会看 `of_reg`**（`:139`）——因为 `of_reg = 0` 是合法地址 |

⚠️⚠️ **`use_of_reg` 存在的唯一理由就是"0 是合法的寄存器地址"**：

```c
/* drivers/mfd/mfd-core.c:137-152 —— 节选 */
if (!cell->use_of_reg)
        /* No of_reg defined - allocate first free compatible match */
        goto allocate_of_node;
...
if (cell->of_reg != of_node_addr)
        return -EAGAIN;
```

⇒ 不能用"`of_reg != 0` 就表示要匹配地址"来代替这个 bool。
⚠️ 使用面：全树**只有 `drivers/mfd/ocelot-core.c` 一个文件**用了 `of_reg`/`use_of_reg`
⇒ 这是又一个"为一个驱动加的字段"。

⚠️ `of_compatible` 的匹配是**在父节点的直接子节点里线性搜索第一个未被占用的**
（`:214-227`）。有多个同 compatible 子节点、又没写 `use_of_reg` 时，
**分配顺序就是 cell 数组的顺序对 DT 子节点顺序** —— 这一点在 03 §2.5 展开。

### §2.5 ACPI 匹配（arm64 上无效）

| 字段 | 位置 | 说明 |
|---|---|---|
| `acpi_match` | `:101` | `const struct mfd_cell_acpi_match *`；核心里只在 `mfd_acpi_add_device()` 读（`drivers/mfd/mfd-core.c:83`） |

⚠️ `CONFIG_ACPI=n` 时那个函数是空桩（`drivers/mfd/mfd-core.c:119`）
⇒ **arm64 内核上这个字段填了等于没填**。按本套 arm64-only 口径裁掉，登记在 99 §2.7。

### §2.6 资源两字段 + 一个开关

| 字段 | 位置 | 说明 |
|---|---|---|
| `num_resources` | `:107` | ⚠️ **手写 cell 时最容易漏的一项**；宏 `MFD_RES_SIZE` 会自动算（`include/linux/mfd/core.h:15`、`:21`） |
| `resources` | `:108` | `const struct resource *`；⚠️ **注释明说这里的地址是相对父设备的**（`:104-106`） |
| `ignore_resource_conflicts` | `:111` | 置 `true` 则跳过 `acpi_check_resource_conflict()`（`drivers/mfd/mfd-core.c:291-297`） |

⚠️⚠️ **`resources` 里写的不是硬件地址**，头文件注释（`include/linux/mfd/core.h:103-106`）说得很直白：

> These resources can be specified relative to the parent device.
> For accessing hardware you should use resources from the platform dev

⇒ **子驱动必须用 `platform_get_resource()` 拿转换后的值，绝不能去读 `mfd_get_cell()->resources`**
（那是转换前的相对偏移，03 §2.7）。

⚠️ `ignore_resource_conflicts` 的实际使用者 **9 个文件全是 x86 遗留平台**
（`lpc_ich.c` · `lpc_sch.c` · `vx855.c` · `tqmx86.c` · `intel_pmc_bxt.c` ·
`intel-lpss.c` · `intel-lpss-pci.c` · `intel_quark_i2c_gpio.c`）
⇒ ⚠️ **arm64 上这个字段没有任何真实使用者**，因为它挡的检查本身就是 ACPI 的。

### §2.7 runtime PM 开关

| 字段 | 位置 | 说明 |
|---|---|---|
| `pm_runtime_no_callbacks` | `:117` | 置 `true` ⇒ 核心替你调 `pm_runtime_no_callbacks(&pdev->dev)`（`drivers/mfd/mfd-core.c:308-309`） |

⚠️ 语义是"这个子设备没有自己的电源域，runtime PM 直接透传给父设备"。
使用者全树只有 **`drivers/mfd/wm8994-core.c`**（`:34` `:39` `:70` 三个 cell）
⇒ 又是一个单驱动字段。

### §2.8 regulator 供电别名两字段

| 字段 | 位置 | 说明 |
|---|---|---|
| `parent_supplies` | `:122` | `const char * const *`；供电名字数组 |
| `num_parent_supplies` | `:123` | 数量 |

⚠️ 语义（头文件注释 `include/linux/mfd/core.h:119-121`）：
**这些供电名字由子驱动 `regulator_get()` 时，实际解析到父设备上去**。

核心的做法是注册一组 **regulator 供电别名**：

```c
/* drivers/mfd/mfd-core.c:207-212 */
ret = regulator_bulk_register_supply_alias(
                &pdev->dev, cell->parent_supplies,
                parent, cell->parent_supplies,
                cell->num_parent_supplies);
if (ret < 0)
        goto fail_res;
```

⚠️ **注意这一段没有任何 `if` 包着** —— 不判断 `num_parent_supplies` 是否为 0，
也不判断父设备有没有 DT 节点。`num = 0` 时它是空操作返回 0
⇒ **对绝大多数 cell 这是一次零成本调用，而不是被跳过的分支**（03 §2.3）。

注销在两处：失败回滚 `:325-327`，正常移除 `:399-400`。

⚠️⚠️ **这两个字段解决的是一个真实的 DT 建模冲突**：
供电引脚长在**芯片**上（所以 DT 里的 `xxx-supply` 属性写在父节点），
但用电的代码在**子驱动**里（所以它会去自己的 `of_node` 上找）。
⇒ 别名机制让"名字相同、查找目标换成父设备"。
⚠️ 这个机制**与 DT 无关** —— 它走的是 regulator 子系统的别名表，
非 DT 平台（板级 `regulator_init_data`）上同样生效。

⚠️ 使用者：只有 Cirrus 的两个 codec 家族
（`drivers/mfd/madera-core.c` 和 `drivers/mfd/arizona-core.c`）。

---

## §3 `struct mfd_cell_acpi_match` —— 2 个字段

```c
/* include/linux/mfd/core.h:55-59 */
/* Matches ACPI PNP id, either _HID or _CID, or ACPI _ADR */
struct mfd_cell_acpi_match {
        const char                      *pnpid;
        const unsigned long long        adr;
};
```

⚠️ **arm64 上这个结构完全不参与运行**（§2.5）。本篇列出它只为字段清单完整。
它的唯一读取点是 `match_device_ids()`（`drivers/mfd/mfd-core.c:68-78`），
整段包在 `#if IS_ENABLED(CONFIG_ACPI)` 里（`:62`）。

⚠️ 注意 `adr` 是 `const unsigned long long` —— **成员上的 `const`**，
意味着这个结构一旦初始化就不能整体赋值覆盖。

---

## §4 `struct mfd_of_node_entry` 与那条无锁链表

```c
/* drivers/mfd/mfd-core.c:24-30 */
static LIST_HEAD(mfd_of_node_list);

struct mfd_of_node_entry {
        struct list_head list;
        struct device *dev;
        struct device_node *np;
};
```

**它记的是"哪个 DT 子节点已经被哪个子设备占用了"** —— 只有这一个用途。

| 操作 | 位置 | 说明 |
|---|---|---|
| 遍历（查这个 np 是否已被占） | `:135` | 在 `mfd_match_of_node_to_dev()` 里 |
| 分配并插入 | `:156-162`（`kzalloc` + `list_add_tail`） | 匹配成功后登记 |
| 删除（失败回滚） | `:319-322`（`list_del` + `kfree`） | `fail_of_entry` 标签（`:318`） |
| 删除（正常移除） | `:393-396`（同上） | `mfd_remove_devices_fn()` 里 |

⚠️ 两处删除的判据都是 `of_entry->dev == &pdev->dev`（`:320`/`:394`）
⇒ **按子设备找它占的 DT 节点**，一个子设备最多占一个节点。

⚠️⚠️ **这条全局链表没有任何锁保护。**
`mfd-core.c` 全文没有 `mutex`、没有 `spinlock`、没有 RCU。
⇒ 它依赖的是一个**隐含假设**：`mfd_add_devices()` 只在 probe 上下文里调，
而设备核心对同一个总线的 probe 是串行的。
⚠️ 但**不同总线上的两个 mfd 父设备可以并行 probe**
（`CONFIG_..._ASYNC_PROBE` 或不同 bus 的 probe 线程）
⇒ **这是一个真实存在的并发缺口**，只是至今没人报过。03 §C 详细分析。

⚠️ 另一个后果：**这张表是全局的，不是 per-parent 的**。
`mfd_match_of_node_to_dev()` 判"这个 np 被占了吗"是**全内核范围**判断
⇒ 理论上不同父设备的 cell 会互相看见对方占用的节点。
实践中不冲突，因为一个 DT 节点只有一个父节点。

### §4.1 设备类型标记

```c
/* drivers/mfd/mfd-core.c:32-34 */
static struct device_type mfd_dev_type = {
        .name   = "mfd_device",
};
```

- 打标记：`pdev->dev.type = &mfd_dev_type`（`:202`）
- 认标记：`if (dev->type != &mfd_dev_type) return 0;`（`:381`）

⚠️⚠️ **这是 mfd "认出自己的孩子" 的全部机制。**
`mfd_remove_devices()` 遍历父设备的**所有**子设备，靠这一个指针比较过滤出自己建的那些
⇒ **父驱动可以放心地在同一个父设备下混合挂 mfd 子设备和自己手工建的设备**，
`mfd_remove_devices()` 不会误删后者。

⚠️ 它也出现在 sysfs 里：`/sys/devices/.../<子设备>/` 的 `uevent` 会带
`DEVTYPE=mfd_device` —— 这是 07 §1 能用来筛 mfd 子设备的唯一标记。

---

## §5 mfd 在 `struct platform_device` 里打的那个洞

```c
/* include/linux/platform_device.h:19 */
struct mfd_cell;
...
/* include/linux/platform_device.h:23-45 —— 节选 */
struct platform_device {
        ...
        /* MFD cell pointer */
        struct mfd_cell *mfd_cell;      /* :41 */
        ...
};
```

⚠️⚠️ **这一个字段是整个子系统的架构决定。** 它的含义是：

**mfd 放弃了"自己做一个总线"这条路，改成在 platform bus 上寄生。**

| 如果自己做 bus | mfd 实际的做法 |
|---|---|
| 定义 `struct bus_type mfd_bus_type` | 无 |
| 定义 `struct mfd_device`（含 `struct device`） | 无，直接用 `platform_device` |
| 定义 `struct mfd_driver` + `mfd_driver_register()` | 无，子驱动就是普通 `platform_driver` |
| 实现 `.match` / `.probe` / `.remove` | 无，全部复用 platform bus 的 |
| 子设备只能被 mfd 驱动认领 | ⚠️ **子设备可被任何 platform_driver 认领** |

⇒ **代价就是这个字段**：核心结构里多一个只服务一个子系统的指针。
**收益是 6.1 里 471 行核心 + 0 个新概念** —— 为什么这笔交易是对的，08 §3 完整推导。

⚠️ **这个字段的三条真实约束**：
1. 它是 `struct mfd_cell *`（**非 const**），指向 `kmemdup` 出来的副本
   （`drivers/mfd/mfd-core.c:193`）⇒ **可写**，`drivers/mfd/intel-lpss.c:404`
   就在 probe 里改过它；
2. `mfd_get_cell()` 返回 `const struct mfd_cell *`（`include/linux/mfd/core.h:139`）
   ⇒ **通过标准接口拿到的是只读视图**；
3. ⚠️ **它对非 mfd 建的 platform_device 是 `NULL`** ⇒ 必须判空（01 §2.5）。

⚠️ 顺带说明：**它不是 `platform_device.platform_data`**。
cell 的副本挂在 `mfd_cell` 上，而 `cell->platform_data` 的内容被另外复制到
`platform_data` 上（`:249-250`）⇒ **两条路，互不干扰**。
子驱动用 `dev_get_platdata()` 拿的是后者。

---

## §6 syscon 的三个结构

⚠️ 全部是 `drivers/mfd/syscon.c` 私有的，**头文件里一个都没有**
⇒ syscon 对外只暴露函数，不暴露结构（01 §4）。

### §6.1 `struct syscon` —— 3 个字段的表项

```c
/* drivers/mfd/syscon.c:33-37 */
struct syscon {
        struct device_node *np;
        struct regmap *regmap;
        struct list_head list;
};
```

**它就是一条 `DT 节点 → regmap` 的映射记录。** 全部 7 个对外接口最终都归到
"在链表里按 `np` 找一条，找不到就现场建一条"。

| 全局状态 | 位置 | ⚠️ |
|---|---|---|
| `syscon_list` | `drivers/mfd/syscon.c:31` | 所有已建立的映射 |
| `syscon_list_lock` | `drivers/mfd/syscon.c:30` | ⚠️⚠️ **有锁！是 `DEFINE_MUTEX`** |

⚠️⚠️ **这是本篇最值得对比的一处**：
**同一个目录下，syscon 的全局链表有 mutex 保护，mfd 核心的 `mfd_of_node_list` 没有任何锁**（§4）。
⇒ 两段代码对"probe 是否串行"这个假设的态度完全相反。
**syscon 的做法是对的**（它可以被 `probe` 之外的路径调到，比如别的驱动的 `resume`）。

⚠️ syscon 还把这个假设**断言化**了：

```c
/* drivers/mfd/syscon.c:55 —— of_syscon_register() 的第一行可执行语句 */
WARN_ON(!mutex_is_locked(&syscon_list_lock));
```

⇒ **内部函数自己检查"调用者必须已经持锁"**。
⚠️ 这类断言在 mfd 核心里一处都没有 —— 差异不只是"有没有锁"，
还包括"有没有把锁约定写成可执行的检查"（08 §6）。

⚠️ 顺带一处值得注意的代码风格：`:57` 用了
`struct syscon *syscon __free(kfree) = kzalloc(...)`
—— **基于作用域的自动释放**（`linux/cleanup.h` 的 `__free`）。
这个写法在 6.1 主线里不存在，是**本树 6.1.158 从更新内核回合进来的**
⇒ ⚠️ 读 6.1 其它子系统的代码时不要期待这个风格，它在 syscon 是个例外。

⚠️ 用 mutex 而非 spinlock 的后果：**syscon 的查找接口不能在原子上下文里调**
—— 而且它内部还会 `of_iomap()`、`regmap_init_mmio()`、`clk_get()`，
本来就必须是可睡眠上下文。⇒ **不要在中断处理里第一次去 lookup syscon**，
要在 probe 里拿好 regmap 存起来（05 §4）。

### §6.2 `syscon_regmap_config` —— 默认的 regmap 参数

```c
/* drivers/mfd/syscon.c:39-43 */
static const struct regmap_config syscon_regmap_config = {
        .reg_bits = 32,
        .val_bits = 32,
        .reg_stride = 4,
};
```

⚠️ **这三个默认值就是"32 位寄存器、4 字节步进"**，覆盖绝大多数 SoC 的杂项寄存器块。

⚠️⚠️ **但它是一份模板，不是最终配置。** `of_syscon_register()`
（`drivers/mfd/syscon.c:45-149`）会**复制它然后按 DT 属性改写**：

| DT 属性 | 改写哪一项 | 位置 |
|---|---|---|
| `reg-io-width` | `reg_stride` 与访问宽度（默认 4） | `:81-83` |
| `big-endian` / `little-endian` / `native-endian` | `val_format_endian` | `:69-74` |
| `hwlocks` | 挂上 hwspinlock | `:85-101` |

⇒ **同一个模板能产出好几种不同的 regmap** —— 06 §4 逐个属性讲。

⚠️ 一个直接后果：`.max_register` **不在模板里**，是运行时按 `resource_size()` 算的
⇒ **syscon 的 regmap 边界来自 DT 的 `reg` 长度**，DT 写短了就读不到后面的寄存器，
而症状是 `regmap_read()` 返回 `-EIO`（07 §5）。

### §6.3 ⚠️ syscon 没有的结构

**没有 `struct syscon_device`，没有引用计数，没有注销接口。**

⚠️⚠️ **一条表项一旦成功进入 `syscon_list` 就永不移除**：
`drivers/mfd/syscon.c` 全文**没有任何 `list_del`**。
（文件里确实有 `kfree`，但只出现在**尚未入链的失败路径**上：
`:113` 释放临时的 regmap 名字串，`:218` 释放 `-EEXIST` 时那条没用上的表项，
`:57` 的 `__free(kfree)` 也只在 `of_syscon_register()` 提前返回时生效
—— **成功路径上 `:139` 的 `return_ptr()` 会解除自动释放**。）

⇒ 这是有意的：syscon 描述的是**SoC 上永久存在的寄存器块**，不是可插拔设备。
⚠️ 也因此 `MFD_SYSCON` 是 `bool` 而不是 `tristate`（`drivers/mfd/Kconfig:1429`）
⇒ **编进内核就永远在，根本不存在卸载路径**，"永不移除"于是自洽（04 §5）。

⚠️ 附带的两处内存所有权细节，读 `of_syscon_register()`（`:45-149`）时要注意：
- `base = of_iomap(np, 0)`（`:64`）由 **`err_regmap` 标签统一 `iounmap`**（`:147`）
  ⇒ 失败路径不漏 io 映射；
- 但**成功路径不保存 `base`** —— 它只活在 regmap 内部
  ⇒ ⚠️ **没有任何代码能把这块映射还回去**，与"表项永不移除"是同一个决定。

---

## §7 `mfd_cell` 到 `platform_device` 的字段映射

⚠️ 这张表是 03 篇的浓缩，放在这里是因为**它才是 `struct mfd_cell` 的真正语义**：
每个字段最终变成了子设备上的什么。

| cell 字段 | 变成子设备的什么 | 转换点 |
|---|---|---|
| `name` | `pdev->name` | `platform_device_alloc()`，`drivers/mfd/mfd-core.c:189` |
| `id` | `pdev->id`（加上基数） | `:184-189` |
| —— | `pdev->dev.parent = parent` | `:201` |
| —— | ⚠️ `pdev->dev.type = &mfd_dev_type` | `:202` |
| —— | `pdev->dev.dma_mask` / `dma_parms` / `coherent_dma_mask` 从父设备继承 | `:203-205` |
| `platform_data` + `pdata_size` | `pdev` 的 platform data（**另一份拷贝**） | `:248-253` |
| `swnode` | 设备的 software node | `:255-259` |
| `resources[]` + `num_resources` | ⚠️ **`pdev->resource[]`，地址已换算** | `:261-302` |
| `of_compatible` + `of_reg` + `use_of_reg` | `pdev->dev.of_node` | `:214-246` |
| `pm_runtime_no_callbacks` | 调 `pm_runtime_no_callbacks()` | `:308-309` |
| `parent_supplies` | regulator 供电别名 | `:207-210` |
| **cell 整体** | ⚠️ **`pdev->mfd_cell`（`kmemdup` 副本）** | `:193` |
| `level` | ⚠️ **不下发**，只在拆解时读副本 | `:387` |
| `enable` / `disable` | ⚠️ **不下发**，`mfd_cell_enable()` 现场读副本 | `:40` `:53` |
| `acpi_match` | ⚠️ arm64 上不生效 | `:83` |
| `ignore_resource_conflicts` | ⚠️ 只影响 ACPI 冲突检查 | `:291` |
| ⚠️ `suspend` / `resume` | ⚠️⚠️ **不下发，也没人读** | —— |

⇒ **20 个字段里，真正在 arm64 上有作用的是 13 个**：
`name` · `id` · `level` · `enable` · `disable` · `platform_data` · `pdata_size` ·
`swnode` · `of_compatible` · `of_reg` · `use_of_reg` · `num_resources` · `resources` ·
加上 `pm_runtime_no_callbacks` 和 `parent_supplies`/`num_parent_supplies`（各有单一使用者）。

**剩下的**：`acpi_match` 与 `ignore_resource_conflicts` 属 ACPI（99 §2.7），
`suspend`/`resume` 是死字段（§2.2）。

---

## §8 结构相关的陷阱清单

| # | 陷阱 | 症状 | 依据 |
|---|---|---|---|
| 1 | ⚠️ 填了 `platform_data` 忘填 `pdata_size` | 子驱动 `dev_get_platdata()` 得 `NULL`，注册却成功 | §2.3 |
| 2 | ⚠️ 手写 cell 忘填 `num_resources` | 子驱动 `platform_get_resource()` 返回 `NULL` | §2.6 |
| 3 | ⚠️⚠️ 以为填 `suspend`/`resume` 就有电源管理 | 回调永远不被调用 | §2.2 |
| 4 | ⚠️ 以为 `enable`/`disable` 带引用计数 | 头文件注释陈旧，实现里没有 | §2.2 |
| 5 | ⚠️ 子驱动去读 `mfd_get_cell()->resources` | 拿到的是**相对偏移**，不是硬件地址 | §2.6 |
| 6 | ⚠️ 用 `of_reg != 0` 代替 `use_of_reg = true` | 地址匹配根本不生效 | §2.4 |
| 7 | ⚠️ 忘了 `mfd_get_cell()` 可能返回 `NULL` | 子驱动被 DT 直接实例化时空指针 | §5 |
| 8 | ⚠️ 在中断上下文里 lookup syscon | mutex 睡眠，还有 `of_iomap` | §6.1 |
| 9 | ⚠️ 以为 `mem_base` 传不传只是"多一次加法" | 传 `NULL` 时 MEM 地址被当**绝对值**，读到别处 | 03 §2.7 |

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 这些字段被怎么处理的（那 165 行） | 03-internals.md |
| 接口签名和返回值 | 01-api.md |
| 真实芯片驱动怎么填这些字段 | 04-extensions.md |
| 我自己要填一份 cell 数组 | 05-usage.md |
| `reg-io-width` / `big-endian` 怎么写 | 06-devicetree.md |
| 为什么它不做自己的 bus | 08-feynman.md |
| 上面结论的证据与口径 | 99-evidence.md |

