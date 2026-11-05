---
title: 费曼学习法
---

# mfd 08：为什么它长这样

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇不介绍接口，只回答一个问题：
**为什么一个被 134 个文件依赖的子系统，核心只有 471 行、6 个导出符号、
没有 bus、没有 class、没有 debugfs、没有一行 `FIXME`？**

---

## §1 先把问题说清楚

**硬件事实**：一颗 PMIC 芯片（比如 Allwinner 板上的 AXP803）通过一根 I2C 线连着 CPU，
内部有十几个互不相干的功能块：

```
        ┌──────────── AXP803（一颗芯片，一个 I2C 地址 0x34）──────────┐
        │  DC-DC ×6   LDO ×11   GPIO ×2   ADC   电池充电   电源键     │
        └──────────────────────────────────────────────────────────┘
```

**软件事实**：Linux 里这十几个功能块分别属于**六个完全不同的子系统**：

| 功能块 | 归谁管 | 注册接口 |
|---|---|---|
| DC-DC / LDO | regulator | `devm_regulator_register()` |
| GPIO | gpio | `devm_gpiochip_add_data()` |
| ADC | iio | `devm_iio_device_register()` |
| 电池充电 | power_supply | `devm_power_supply_register()` |
| 电源键 | input | `input_register_device()` |
| 温度报警 | thermal | `devm_thermal_of_zone_register()` |

⚠️⚠️ **矛盾在这里**：Linux 驱动模型的基本单位是"一个 `struct device` 绑一个 driver"，
而每个子系统的注册接口都要一个**自己的** `struct device`。
⇒ **一颗芯片必须变成十几个 device，否则这十几个子系统的代码没地方挂。**

⚠️ **反过来也不行**：写一个"什么都会"的巨型驱动，把 regulator/gpio/iio/input
的注册全塞进去 —— 那就是 `drivers/mfd/` 里 2008 年前的写法，
每颗新芯片都要重复一遍六个子系统的样板代码。

⇒ ⚠️⚠️ **mfd 要解决的就是这一件事：把"一个物理设备"拆成"多个逻辑 device"。**
**名字里的 MFD = Multi-Function Device，中文最贴切的说法是"分家"。**

---

## §2 三条可能的路，为什么选了第三条

### 路一：给 mfd 自己建一条 bus

⚠️ 这是最"正统"的做法 —— 像 i2c、spi、platform 那样定义 `struct bus_type mfd_bus_type`，
子设备挂在 mfd bus 上，子驱动写 `struct mfd_driver`。

⚠️⚠️ **树里有人真这么干过，而且证据还在**：

```c
/* drivers/mfd/mcp-core.c:44 */
static struct bus_type mcp_bus_type = {
```

⚠️ **MCP（Multimedia Communication Port）就是这条路的产物** ——
一条独立的总线，配套 `mcp_driver_register()` 之类的接口。
**它今天只服务两个驱动**（`ucb1x00-*` 和 `mcp-sa11x0`），是 2001 年的 StrongARM 遗产。

⇒ ⚠️⚠️ **为什么没走这条路**：新建一条 bus 意味着
**每个子驱动都要改写成 `mfd_driver`**。而那十几个功能块的驱动
（regulator/gpio/iio/…）**本来就是 platform_driver，改起来是全树级的工程量**。
⇒ **建 bus 的成本落在几百个子驱动身上，而收益只有"类型更干净"。**

⚠️ 这一点对 99 §1.2 的类型判定很重要：`drivers/mfd/` 里**确实**能 grep 到 `bus_type`，
**但那不是 mfd 核心** ⇒ **mfd 核心是框架型，不是总线型。**

### 路二：让子设备直接挂在父设备所在的总线上

⚠️ 即：GPIO 块也做成一个 `i2c_client`。
⇒ **不行**：I2C 从地址只有一个（`0x34`），十几个功能块共享它。
`i2c_new_client_device()` 需要一个独立地址。

### 路三：⚠️ 借 platform bus —— 实际选择

**platform bus 的定义就是"不属于任何真实总线的设备"** ⇒ 天生适合装"芯片内部的一块"。
更关键的是：**那十几个子系统的驱动本来就全是 platform_driver。**

⇒ ⚠️⚠️ **于是 mfd 需要做的事被压缩成一句话**：
**把一个 `mfd_cell[]` 数组逐个变成 `platform_device`。**

```
        mfd_cell[] ──── mfd_add_devices() ────▶ platform_device ×N
        （父驱动写的        （471 行的全部        （走标准 platform
          一张表）             工作内容）           bus 匹配）
```

⚠️ **这就是 471 行的来源**：核心不需要匹配算法（platform bus 有）、
不需要 driver 注册接口（platform bus 有）、不需要电源管理（platform bus 有）、
不需要 sysfs（platform bus 有）。**它只需要"造 device"这一件事。**

---

## §3 为此付出的一个代价：往内核核心结构里打了个洞

```c
/* include/linux/platform_device.h:41 —— 节选 */
struct platform_device {
        ...
        struct mfd_cell *mfd_cell;
        ...
};
```

⚠️⚠️ **`struct platform_device` 里有一个 mfd 专属字段**（前置声明在 `:19`）。

⇒ 这是"借别人的 bus"的必然代价：**mfd 需要在子设备上记住"它是从哪个 cell 来的"，
而它没有自己的 device 类型可以扩展** ⇒ 只能往 platform 的结构里加一个字段。

⚠️ **这个洞其实很小**（一个指针），而且**几乎没人用**：
`mfd_get_cell()` 全树 26 处调用、7 个文件，**全部是 2008–2010 年的老平台，
arm64 上一个都没有**（05 §3.4）。

⇒ ⚠️⚠️ **值得记的判断**：这个字段**是历史包袱而不是设计核心**。
现代子驱动取信息的三条路（`dev_get_platdata()` / `platform_get_resource()` /
`dev_get_regmap(dev->parent, ...)`）**都不经过它**。
⇒ 如果今天重新设计 mfd，这个字段大概不会存在。

⚠️ 另一个更轻的标记是 `mfd_dev_type`（`drivers/mfd/mfd-core.c:32-34`）——
它只是一个名字为 `"mfd_device"` 的 `device_type`，
**核心靠它认出"这个 platform_device 是我建的"**（`:381`）。
⇒ ⚠️ **这个标记比 `mfd_cell` 指针有用得多**：它顺带成了唯一一条用户态可见的
mfd 信息（`DEVTYPE=mfd_device`，07 §3）。

---

## §4 为什么核心可以没有状态、没有锁、没有观测面

**这三件事是同一个原因，必须一起讲。**

### §4.1 mfd 核心不持有任何运行期对象

⚠️⚠️ 把 `mfd_add_devices()` 的工作按时间轴排开：

```
   父驱动 probe
        │
        ├─ mfd_add_devices(cells)
        │      │
        │      ├─ platform_device_alloc()        造壳
        │      ├─ 资源换算 → kcalloc(res)         算地址/中断
        │      ├─ of_node 匹配                   找 DT 子节点
        │      ├─ platform_device_add()          ⚠️ 交给 platform bus
        │      └─ kfree(res)                     ⚠️ 自己的临时内存立刻释放
        │
        └─ probe 返回 —— ⚠️⚠️ mfd 核心此后完全不参与任何事
```

⚠️ **注意 `kfree(res)`（`drivers/mfd/mfd-core.c:311`）**：
资源数组是**临时**的，`platform_device_add_resources()` 已经复制走了。
⇒ **函数返回时，核心手里什么都没剩。**

⇒ ⚠️⚠️ **所以"没有 debugfs"不是偷懒 —— 是没有对象可以展示**（07 §1）。
一个 gpio 子系统能展示 `gpiochip` 列表，一个 pwm 子系统能展示 `pwm_chip` 列表，
**mfd 没有等价物**：它创建的东西全部登记在 platform bus 上，
`/sys/bus/platform/devices/` 就是它的"列表"。

### §4.2 唯一的全局状态是一条链表，而它无锁

```c
/* drivers/mfd/mfd-core.c:24 */
static LIST_HEAD(mfd_of_node_list);
```

⚠️ 它的用途只有一个：记住"哪个 DT 子节点已经被哪个子设备占用了"，
好让第二个同 `of_compatible` 的 cell 不要抢同一个节点（03 §2.5）。

⚠️⚠️ **它没有任何锁保护**：`mfd_add_device()` 遍历它（`:135`）、
失败路径删它（`:319-323`）、`mfd_remove_devices_fn()` 也删它（`:393`）
—— **全部裸操作。**

⇒ **它靠的是一个隐含假设**：**同一时刻只有一个 CPU 在给同一个父设备分家。**
这个假设在实践中成立（同一个父驱动的 probe 是串行的），
但 ⚠️⚠️ **它是全局链表而不是 per-parent 链表** ⇒
**两个不同的父设备并行 probe 时，两条 `list_add`/`list_del` 会撞在同一条链上。**

⚠️ **为什么至今没炸**：异步 probe（`PROBE_PREFER_ASYNCHRONOUS`）在
mfd 父驱动里几乎没人用，而同步 probe 由 `device_lock` + 驱动核心的串行化挡住了。
⇒ ⚠️ **这是本子系统最大的一处技术债，而它在代码里连一句注释都没有**（99 §6）。

### §4.3 为什么只有两种错误码

⚠️ **核心通篇只产生 `-EAGAIN`（4 处，全是 OF 匹配的内部信号）和 `-ENOMEM`（3 处）。
没有 `-EINVAL`，没有 `-ENODEV`。**

对比同套已完成的子系统：pwm 核心 15 个 `-EINVAL`，dma-buf 核心 47 个。

⇒ ⚠️⚠️ **含义是"mfd 核心几乎不做参数校验"**：
`num_resources` 填错了不检查（01 §2.1.1）、`platform_data` 与 `pdata_size`
不配对不检查（02 §8）、cell 数组是否 `static` 不检查（03 §2.1）。

⚠️ **这不是懒，是分工判断**：cell 数组是**编译期常量**，由驱动作者写死在 `.c` 文件里
⇒ **写错了在开发阶段就会暴露，不是运行期的外部输入。**
⇒ 与 pwm 那种"接受用户态 sysfs 输入"的子系统性质完全不同。

⚠️⚠️ **但代价真实存在**：填错 `num_resources` 的症状是子驱动
`platform_get_resource()` 返回 `NULL`，**报错点离出错点很远**（07 §4.2）。
⇒ **用 `MFD_RES_SIZE()` 或 `ARRAY_SIZE()` 而不是手写数字**，是唯一的防线。

---

## §5 为什么 `simple-mfd` 不在 `drivers/mfd/` 里

```c
/* drivers/of/platform.c:24-31 —— 节选 */
const struct of_device_id of_default_bus_match_table[] = {
        { .compatible = "simple-bus", },
        { .compatible = "simple-mfd", },
```

⚠️⚠️ **一个叫 `simple-mfd` 的东西，实现在 OF 子系统里，`mfd-core.c` 一行都不参与。**
初看像分层错误，其实是**一条很清晰的判断**。

**关键问题：子设备的寄存器在哪里？**

| 父设备形态 | 子设备的寄存器地址 | DT 能不能描述 |
|---|---|---|
| SoC 内部一段 MMIO | ⚠️ **在 CPU 的物理地址空间里** | ⚠️ **能** —— 子节点写 `reg`，`ranges` 换算 |
| I2C/SPI 上的外部芯片 | ⚠️ **在芯片内部的寄存器空间里** | ⚠️⚠️ **不能** —— 那不是 CPU 可寻址的地址 |

⇒ ⚠️⚠️ **第一行的情况根本不需要 mfd**：DT 已经把子设备描述完整了，
`of_platform_populate()` 照着实例化就行 —— **这是 OF 子系统的本职工作，不是 mfd 的。**
⇒ **`simple-mfd` 和 `simple-bus` 在代码里是同一条路**（`:25` 和 `:26` 相邻两行），
差别纯粹是语义标注："这是一条总线" vs "这是一颗多功能芯片"。

⚠️ **只有第二行才需要 mfd**：DT 描述不出来的部分（"这个功能块在芯片内部偏移 0x30"），
**只能写在驱动的 C 代码里** ⇒ 这就是 `mfd_cell[]` 存在的全部理由。

⚠️⚠️ **这条判断在数字上留下了痕迹**（04 §1）：
`drivers/mfd/` 里 **I2C 驱动 95 个，platform 驱动只有 48 个**。
⇒ **SoC 内部的多功能块大部分被 `simple-mfd` 吸收了，根本没进这个目录。**

⇒ ⚠️ **所以 `simple-mfd` 不在 mfd 里不是分层错误，而是一条正确的边界**：
**mfd 只处理"DT 描述不了"的那一半。**

---

## §6 为什么 `syscon` 住在这个目录里

⚠️ syscon（378 行）**几乎与 `mfd_cell` 毫无关系**：它不创建任何子设备，
只做一件事 —— 把一个 DT 节点变成一个 regmap 交出去。
⚠️ 它的 Kconfig 甚至**不 `select MFD_CORE`**，是个 `bool`（`drivers/mfd/Kconfig:1429-1431`）
⇒ **两者在编译上毫无依赖，只是住在同一个目录。**

**那为什么在这里？官方文档给了答案**：

```
/* Documentation/devicetree/bindings/mfd/mfd.txt:3-16 —— 归纳 */
一类是外部总线上的混合信号 ASIC（通常是 PMIC），
另一类是"一段杂项系统寄存器"。
```

⇒ ⚠️⚠️ **`mfd.txt` 明确把"一段杂项系统寄存器"也算作 MFD** ——
"多功能"指的是**寄存器的用途五花八门**，而不是"能分出多个 device"。

⚠️ **这类寄存器块的共同特征**：里面有十几个位分属十几个不同的功能
（一个时钟旁路位、一个 PHY 模式选择位、一个复位位、一个启动地址寄存器…），
**而这些功能的驱动散落在 remoteproc / phy / clk / pci 各处。**

⇒ ⚠️⚠️ **它与 PMIC 分家在概念上是对称的**：

| | PMIC（`mfd_add_devices`） | 杂项寄存器块（`syscon`） |
|---|---|---|
| 一个物理实体 | 一颗 I2C 芯片 | 一段 MMIO |
| 多个逻辑用户 | 十几个功能块 | 十几个别处的驱动 |
| mfd 交出去的东西 | ⚠️ **多个 `platform_device`** | ⚠️ **一个共享的 `regmap`** |
| 谁来"分" | 父驱动的 cell 数组 | ⚠️ **不分** —— 大家按位共享同一个 regmap |

⇒ ⚠️ **一句话**：**`mfd_add_devices()` 是"按功能块切开"，
`syscon` 是"不切，大家共享"。** 两者面对的是同一类硬件现实（一个实体多个用途），
选了相反的两种应对。

⚠️⚠️ **使用面上 syscon 反而是赢家**：`syscon_regmap_lookup_by_phandle()`
被 **198 个 `.c` 文件**引用，`syscon_node_to_regmap()` **164 个**
—— **都超过 `mfd_add_devices()` 的 134 个**（04 §5）。
⇒ **这个目录里最常被用的接口，恰好是与"分家"无关的那一组。**

---

## §7 471 行的代价：五处硬伤

⚠️ 前面讲的都是"为什么这样是对的"。这一节讲**账单**。

⚠️⚠️ `mfd-core.c` + `core.h` 里 `FIXME` / `TODO` / `XXX` / `HACK` / `REVISIT`
**共 0 处**，而框架级官方文档也是 **0 篇**
⇒ ⚠️ **这个子系统的技术债形态是"根本没写下来"** ——
以下五条全部是读代码推出来的，没有任何一处在树里被标注（99 §6）。

| # | 硬伤 | 后果 | 位置 |
|---|---|---|---|
| 1 | ⚠️⚠️ `mfd_of_node_list` 全局链表无锁 | 并行 probe 下链表可能被并发改写 | §4.2 |
| 2 | ⚠️⚠️ `level` 机制是半成品 | `devm` 版本永远删不掉 `HIGH` cell；部分失败回滚也不删 | 01 §2.2、04 §3.3 |
| 3 | ⚠️ disabled 子节点走失败路径却返回成功 | **静默不注册**，是"子设备不见了"的头号原因 | `mfd-core.c:234-238` |
| 4 | ⚠️ `mfd_cell.suspend` / `resume` 是死字段 | 填了不报错、不生效 | `core.h:74-75` |
| 5 | ⚠️ `mfd_cell_enable()` 的引用计数注释与实现不符 | 按注释写代码会重复触发回调 | `core.h:126-131` |

### §7.1 第 2 条为什么特别值得看

⚠️ `MFD_DEP_LEVEL_HIGH`（`include/linux/mfd/core.h:50`）**全树只有一个使用者**：
`drivers/mfd/madera-core.c`。

⚠️⚠️ **而这个唯一的使用者，自己的错误路径就写漏了**：

```c
/* drivers/mfd/madera-core.c:761-762 */
err_devs:
        mfd_remove_devices(dev);
```

⇒ probe 中途失败时，`level = HIGH` 的 `madera-ldo1` 不会被删（04 §3.3）。

⇒ ⚠️⚠️ **这是一条很有代表性的推论**：
**一个只有一个使用者的机制，等于没有人替它测试。**
`level` 的语义（"在拆解过程中间插入父驱动自己的动作"，04 §3.2）
既没写进注释、也没写进文档、还只有一处实现 —— 而那一处有 bug。
⇒ **需要它的时候，把 04 §3 那三段夹心当作规范抄，别指望核心兜住你。**

### §7.2 为什么这些债一直没还

⚠️ **因为它们都不影响 99% 的使用者**：

| 债 | 触发条件 | 现实频率 |
|---|---|---|
| 无锁链表 | 两个父设备并行 probe 且都用 `of_compatible` | ⚠️ 极低（异步 probe 罕见） |
| `level` 的坑 | 用 `MFD_DEP_LEVEL_HIGH` | ⚠️ **全树 1 个驱动** |
| disabled 静默 | DT 写了 `status = "disabled"` | ⚠️ **常见，但一查就知道**（07 §4.1） |
| 死字段 | 填了 `cell->suspend` | ⚠️ 低（0 个驱动填） |
| 注释不符 | 用 `mfd_cell_enable()` | ⚠️ 2 个文件 |

⇒ ⚠️ **典型的"薄核心"权衡**：**471 行换来的是"绝大多数情况下不出错"，
而不是"任何情况下都不出错"。**
⚠️⚠️ 与 dma-buf（核心 4000+ 行、146 行 rst 专门讲设计债）对比就很清楚：
**dma-buf 选择把复杂性收进核心，mfd 选择把它推给驱动作者。**

---

## §8 一句话对照：mfd 在本套子系统里的位置

| 子系统 | 它对外交出的东西 | 核心规模 | 有没有自己的 bus/class |
|---|---|---|---|
| gpio | `gpio_desc` + 一个 chardev | 大 | class + chardev |
| pwm | `pwm_device` | 1734 行 | class + debugfs |
| regmap | ⚠️ 一个寄存器访问抽象 | 大 | ⚠️ 无（纯库） |
| dma-buf | `dma_buf` fd | 大 | ⚠️ 匿名 inode + sysfs |
| ⚠️ **mfd** | ⚠️⚠️ **一批 `platform_device`** | ⚠️ **1092 行（核心 471）** | ⚠️⚠️ **全都没有** |

⇒ ⚠️⚠️ **mfd 是本套见过最"薄"的子系统，而它的薄有一个清晰的来源**：
**它交出的东西（`platform_device`）已经有一整套现成的基础设施照顾了。**

**三句话总结**：

1. ⚠️ **mfd 解决的问题**：一颗芯片、多个功能块、每个功能块归不同子系统管
   ⇒ 必须变成多个 `struct device`。
2. ⚠️ **mfd 的做法**：不建 bus、不建 class，**借 platform bus**，
   只写"把 `mfd_cell[]` 造成 `platform_device`"这一段
   ⇒ 471 行，6 个导出符号，2 种错误码，0 个观测设施。
3. ⚠️⚠️ **mfd 的代价**：**校验和顺序全部推给驱动作者**
   —— `num_resources` 填对、`pdata_size` 配对、cell 写 `static const`、
   `mfd_add_devices()` 放在 probe 最后一步、`level = HIGH` 时手动调两个 remove。
   **这五条没有任何一条由核心保证**（05 §2.2、§5.3）。

⚠️ **最后一个值得记住的反直觉点**：这个目录 **112426 行代码里核心占 0.56%**，
而目录里最常被外部引用的接口（`syscon_regmap_lookup_by_phandle()`，198 个文件）
**与"分家"这件事毫无关系**。
⇒ ⚠️⚠️ **"`drivers/mfd/` 是什么"和"mfd 框架是什么"是两个不同的问题**，
读这个目录时必须随时分清自己在问哪一个。

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 三分钟版的全景 | 00-overview.md |
| 那 6 个接口的签名与返回值 | 01-api.md |
| `mfd_cell` 20 个字段 | 02-datastructures.md |
| 471 行逐段走一遍 | 03-internals.md |
| 真实驱动的四种形态 | 04-extensions.md |
| 我要动手写 | 05-usage.md |
| DT 三种形态 | 06-devicetree.md |
| 出问题了怎么查 | 07-debug.md |
| 本篇每个数字的口径与推导 | 99-evidence.md |
