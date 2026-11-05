---
title: 扩展机制
---

# regmap 04：子框架

## §0 组织轴

**本篇的组织轴是"一节一个独立子框架，互不调用"。**
**regmap 的子框架有一个共同点**：它们都是**核心之上的可选层**，
**通过 `regmap_config` 的某个字段或一个独立的注册函数接入。**

### §0.1 分诊表：哪些真会碰到

| # | 子框架 | 文件（行数） | 怎么开启 | 会碰到吗 |
|---|---|---|---|---|
| §1 | **regmap-irq** | `regmap-irq.c`(1319) | `devm_regmap_add_irq_chip()` | **MFD/PMIC 驱动一定会** |
| §2 | **三个缓存后端** | `regcache.c`(827) + `regcache-rbtree.c`(554) + `regcache-lzo.c`(368) + `regcache-flat.c`(83) | `config->cache_type` | **要省总线流量或支持挂起就会** |
| §3 | **分页（ranges）** | 在 `regmap.c` 里 | `config->ranges` | 寄存器空间大于窗口时 |
| §4 | **debugfs** | `regmap-debugfs.c`(692) | **自动**（`CONFIG_DEBUG_FS`） | **调试时一定会** |
| §5 | **tracepoint** | `trace.h`(284) | **自动** | **调试时一定会** |
| §6 | **12 个总线适配** | 13 个文件，22 个实例 | `devm_regmap_init_<bus>()` | **一定会用其中一个** |
| §7 | **SPI-AVMM** | `regmap-spi-avmm.c`(713) | `devm_regmap_init_spi_avmm()` | Intel FPGA 才会 |

⇒ **要读懂的是 §1、§2、§4**；§3 知道存在就够；§6 用哪个查 01 §3.1。

### §0.2 各子框架怎么接入核心

| 子框架 | 接入点 | 说明 |
|---|---|---|
| regmap-irq | **独立注册函数** | **不通过 `regmap_config`** ——它是"在 regmap 之上再建一层" |
| 缓存 | `config->cache_type` + `reg_defaults` | 03 §A4 |
| 分页 | `config->ranges` / `num_ranges` | 03 §B4 |
| debugfs | **`__regmap_init` 里自动建** | ⚠️ **`disable_locking` 会关掉它**（03 §A2.1） |
| tracepoint | **编译进核心的读写路径** | 无开关 |
| 总线适配 | **选哪个 `regmap_init_<bus>()`** | 01 §3.1 |

⚠️ **regmap-irq 的接入方式和其它都不一样** ——
**它是唯一一个"有自己的注册函数和自己的数据结构"的子框架**，
⇒ **所以它其实是"用 regmap 实现的另一个子系统"**，而不是 regmap 的一个特性。

---

## §1 regmap-irq：1319 行，11 个导出符号

### §1.1 它解决什么问题

**很多 PMIC/MFD 芯片的中断长这样**：
一根中断线接到 SoC，芯片内部有一组"状态寄存器"，
**哪个位置了就表示哪个子中断来了**；还有"掩码寄存器"控制哪些子中断使能。

**没有 regmap-irq 的话**，每个这类驱动都要自己：
① 注册一个 `irq_chip`；② 建一个 `irq_domain`；
③ 在中断处理里读状态寄存器、逐位分发；④ 实现 mask/unmask。

**regmap-irq 把这四件事全包了** ⇒ **驱动只要描述"寄存器在哪、位怎么排"。**

### §1.2 `struct regmap_irq_chip`：40 个字段

`include/linux/regmap.h:1563` 起。**按用途分六组**：

#### 组 1：主状态寄存器（分层中断）

| 字段 | 说明 |
|---|---|
| `name` | 名字 |
| `main_status` | **"总状态"寄存器** ——先读它知道哪一组有中断 |
| `num_main_status_bits` / `num_main_regs` | 主状态的规模 |
| `sub_reg_offsets` | `struct regmap_irq_sub_irq_map *` ——**主状态位 → 子状态寄存器**的映射 |

**这一组是给"两级中断"的芯片用的**：先读主状态定位到组，再读该组的子状态。
简单芯片不用填。

#### 组 2：七个寄存器基址

| 字段 | 作用 |
|---|---|
| `status_base` | **状态寄存器** ——必填 |
| `mask_base` | **掩码寄存器** |
| `unmask_base` | **单独的"解掩码"寄存器**（有些芯片 set/clear 分开） |
| `ack_base` | **应答寄存器** |
| `wake_base` | 唤醒使能 |
| `type_base` | 触发类型（边沿/电平） |
| `virt_reg_base` / `config_base` | 虚拟寄存器 / 配置寄存器（较新的机制） |
| `irq_reg_stride` | **同一组内寄存器的步长** |

⚠️ **`mask_base` 和 `unmask_base` 同时存在**是因为
**有些芯片"写 1 掩码"和"写 1 解掩码"是两个不同寄存器**（set/clear 寄存器对）
⇒ **这类硬件不能用"读-改-写"，只能往对应寄存器写 1。**

#### ⚠️ 组 3：13 个 1-bit 标志（本节最能说明问题的一组）

| 标志 | 含义 |
|---|---|
| `mask_invert` | **掩码位的极性反了**（1 = 使能而不是屏蔽） |
| `ack_invert` | **应答位的极性反了** |
| `wake_invert` | 唤醒位极性反 |
| `type_invert` | 类型位极性反 |
| `status_invert` | **状态位极性反** |
| `use_ack` | **这颗芯片需要显式应答** |
| `clear_ack` | **应答方式是"写 0 清"而不是"写 1 清"** |
| `init_ack_masked` | **初始化时先应答一遍**（清掉上电时的假中断） |
| `clear_on_unmask` | **解掩码时要先清状态** |
| `mask_unmask_non_inverted` | 新语义：mask/unmask 寄存器不反转 |
| `type_in_mask` | **触发类型编码在掩码寄存器里**（没有独立的 type 寄存器） |
| `not_fixed_stride` | 寄存器步长不固定（要用 `get_irq_reg` 回调） |
| `runtime_pm` | 中断处理前后做 runtime PM |

⇒ ⚠️ **5 个 `*_invert` 标志**（`mask` / `ack` / `wake` / `type` / `status`）
**是整个 regmap 里最能说明"硬件多样性"的地方** ——
**同一个概念（掩码位），不同芯片的极性是相反的。**

**评价**：⚠️ **13 个布尔标志的组合空间是 2^13**，
**实际上只有少数组合被真实硬件用到** ⇒
**这是"用标志位穷举硬件差异"的典型代价**（§C 见 03 §C2 的同类评价）。

#### 组 4：六个计数 + 中断表

| 字段 | 说明 |
|---|---|
| `irqs` / `num_irqs` | **`struct regmap_irq` 数组** ——每个子中断的 `{reg_offset, mask}` |
| `num_regs` | 状态/掩码寄存器的个数 |
| `num_type_reg` / `num_virt_regs` / `num_config_bases` / `num_config_regs` | 各类寄存器的个数 |

#### 组 5：五个回调

| 回调 | 作用 |
|---|---|
| `handle_pre_irq` / `handle_post_irq` | **中断处理前后的钩子**（如芯片需要先解锁寄存器） |
| `set_type_virt` | 用虚拟寄存器设触发类型 |
| `set_type_config` | **用配置寄存器设触发类型** ——**有默认实现**（§1.3） |
| `get_irq_reg` | **算"第 index 个寄存器的地址"** ——**有默认实现**（§1.3） |

#### 组 6：`irq_drv_data`

驱动的私有指针，传给两个 `handle_*_irq` 回调。

### §1.3 两个"默认实现"是导出符号

⚠️ **regmap-irq 的 11 个导出符号里有 2 个不是 API，是"可以直接用的默认回调"**：

| 符号 | 位置 | 给哪个回调用 |
|---|---|---|
| `regmap_irq_get_irq_reg_linear` | `drivers/base/regmap/regmap-irq.c:645` | `get_irq_reg` ——**寄存器地址线性排列的常规情况** |
| `regmap_irq_set_type_config_simple` | `drivers/base/regmap/regmap-irq.c:700` | `set_type_config` ——**简单的类型配置** |

⇒ **驱动可以直接 `.get_irq_reg = regmap_irq_get_irq_reg_linear`** ——
**这是一种很好的 API 设计**：不是"不填就用默认"，而是**把默认实现显式导出**，
**让驱动作者看见"常规情况长什么样"。**

### §1.4 其余 9 个导出符号

| 符号 | 位置 | 作用 |
|---|---|---|
| `regmap_add_irq_chip` | `drivers/base/regmap/regmap-irq.c:1102` | 注册 |
| `regmap_add_irq_chip_fwnode` | `:1078` | **带 fwnode**（DT/ACPI） |
| `devm_regmap_add_irq_chip` | `:1245` | **推荐** |
| `devm_regmap_add_irq_chip_fwnode` | `:1218` | — |
| `regmap_del_irq_chip` | `:1153` | 注销 |
| `devm_regmap_del_irq_chip` | `:1268` | — |
| `regmap_irq_chip_get_base` | `:1282` | **拿 irq base**（老接口） |
| `regmap_irq_get_virq` | `:1300` | **拿某个子中断的 virq** ——**新代码用这个** |
| `regmap_irq_get_domain` | `:1319` | 拿 irq_domain |

⚠️ **`regmap_irq_chip_get_base` 假设 irq 号连续** ——
**`regmap_irq_get_virq(data, N)` 才是正确做法**（01 §7）。

### §1.5 唯一那条设计债在这里

**全子系统仅有的 1 条自陈债**（03 §C1）：

| 位置 | 内容 |
|---|---|
| `drivers/base/regmap/regmap-irq.c:633` | `FIXME: This is for backward compatibility and should be removed` |

⇒ **regmap 核心零债，唯一那条在这个子框架里，而且是向后兼容包袱** ——
**和 §1.2 那 13 个标志位是同一个成因**：**要兼容各式各样的老硬件描述方式。**

---

## §2 三个缓存后端

### §2.1 三选一的表

**`cache_types[]` 是 `static const`**（`drivers/base/regmap/regcache.c:18-25`）：

| `cache_type` | 后端 | 位置 | 行数 | 数据结构 |
|---|---|---|---|---|
| `REGCACHE_RBTREE` | `regcache_rbtree_ops` | `drivers/base/regmap/regcache-rbtree.c:542` | 554 | **红黑树**（每个节点是一段连续寄存器） |
| `REGCACHE_FLAT` | `regcache_flat_ops` | `drivers/base/regmap/regcache-flat.c:76` | **83** | **一个数组** |
| `REGCACHE_COMPRESSED` | `regcache_lzo_ops` | `drivers/base/regmap/regcache-lzo.c:360` | 368 | **LZO 压缩块** |

⚠️ **LZO 那一项在 `#if IS_ENABLED(CONFIG_REGCACHE_COMPRESSED)` 里**
（`drivers/base/regmap/regcache.c:20-22`）⇒
**没开这个 Kconfig 时它根本不在表里** ⇒
**`cache_type = REGCACHE_COMPRESSED` 会让 `regmap_init` 失败**（05 §8）。

### §2.2 怎么选

| 场景 | 选哪个 | 理由 |
|---|---|---|
| **不确定** | **`REGCACHE_RBTREE`** | **默认选择**，稀疏/密集都能接受 |
| **寄存器号密集且 `max_register` 小**（比如 < 256） | `REGCACHE_FLAT` | **O(1) 访问，没有树遍历** |
| **寄存器极多（上万）且内存紧张** | `REGCACHE_COMPRESSED` | 用 CPU 换内存 |
| **寄存器全是易失的** | `REGCACHE_NONE` | 缓存没有意义 |

⚠️ **`REGCACHE_FLAT` 的内存开销是 `max_register × val_bytes`**：

**举例**：`max_register = 0xFFFF`、`val_bits = 32` ⇒
**65536 × 4 = 256 KB** ⇒ **`regmap_init` 返回 `-ENOMEM`**（01 §9.4）。

⇒ **`FLAT` 的 83 行代码就是"一个数组 + 边界检查"** ——
**简单到极致，代价全在内存上。**

### §2.3 缓存的四个状态位

**`struct regmap` 里有四个和缓存相关的布尔**（02 §5.2）：

| 位 | 含义 | 谁设 |
|---|---|---|
| `cache_only` | **只读写缓存** | `regcache_cache_only()` |
| `cache_bypass` | **绕过缓存** | `regcache_cache_bypass()` |
| `cache_dirty` | **缓存里有未写回的值** | **核心自动设**（`cache_only` 期间的写）+ `regcache_mark_dirty()` |
| `cache_free` | 缓存已释放 | 核心 |

**电源管理就是这四个位的编排**（03 §A6、05 §3）。

### §2.4 ⚠️`reg_defaults` 不填的后果

**缓存需要知道"没被写过的寄存器是什么值"** ——
**这就是 `config->reg_defaults` 的作用。**

⚠️ **不填会怎样**：
**缓存会在第一次读某个寄存器时从硬件读一次并记住**（延迟填充）
⇒ **功能正确，但 `regcache_sync()` 只会写回"被读过或写过"的寄存器。**

**所以两种用法都合理**：
① **填 `reg_defaults`** ——从一开始就知道全貌，`sync` 能写回全部；
② **不填** ——延迟填充，`sync` 只写回碰过的。

⚠️ **但如果芯片上电默认值和你的 `reg_defaults` 不符**，
**缓存会认为"硬件已经是这个值"而跳过写** ⇒ **配置静默丢失**（05 §8）。

---

## §3 分页（ranges）：驱动完全看不见

### §3.1 `struct regmap_range_cfg` 的八个字段

`include/linux/regmap.h:462`（02 §5.6 已列）。**机制**：

```
  驱动访问虚拟地址 X（落在 range_min..range_max）
    │
    ├─ 核心算出：页号 = (X - range_min) / window_len
    │            页内偏移 = (X - range_min) % window_len
    │
    ├─ 先写 selector_reg：
    │     val = (页号 << selector_shift) & selector_mask
    │
    └─ 再访问 window_start + 页内偏移
```

⇒ **驱动像访问一个平坦的大地址空间一样用它。**

### §3.2 为什么需要它

**典型硬件**：一颗芯片有 4096 个寄存器，
**但总线上只能看到 256 个地址** ——多出来的靠"页选择器"切换。

⇒ **没有 regmap 的话，驱动每次访问都要自己算页并写选择器**，
**而且要自己记住"当前在哪一页"以避免重复写。**

**核心会缓存当前页** ⇒ **同一页内的连续访问不会重复写选择器。**

⚠️ **代价是跨页访问多一次总线传输** ——
**所以寄存器布局应该让常用的寄存器挤在同一页。**

---

## §4 debugfs：692 行，本子系统最有用的调试出口

### §4.1 落点与文件

**`/sys/kernel/debug/regmap/<name>/`**，
其中 `<name>` 来自 `config->name`（没填就用 device 名）。

**常见的文件**（07 篇会给完整清单和实测注意事项）：

| 文件 | 内容 |
|---|---|
| `registers` | **dump 全部可读寄存器** ——`地址: 值` 逐行 |
| `access` | **每个寄存器的可读/可写/易失/precious 属性** |
| `range` | 分页信息 |
| `name` | regmap 的名字 |

### §4.2 ⚠️ 两个盲区（都是刻意的）

| 盲区 | 原因 |
|---|---|
| ⚠️ **`disable_locking` 的 regmap 完全看不到** | `__regmap_init` 里 `regmap_debugfs_disable(map)`（03 §A2.1）——**因为 dump 需要加锁** |
| ⚠️ **`precious` 寄存器不被 dump** | **读它们有副作用**（02 §1.0.C）——**`cat registers` 会清掉中断状态** |

⇒ **第二条是 `precious` 这个字段存在的唯一理由** ——
**它只影响 debugfs，不影响缓存**（那是 `volatile` 的事）。

### §4.3 0 个导出符号

**`regmap-debugfs.c`(692 行) 一个导出符号都没有**（01 §0.5）
⇒ **纯内部** ——驱动不需要也不能主动调它。

---

## §5 tracepoint：17 个事件

**`drivers/base/regmap/trace.h`(284 行)**，
**22 个声明里 5 个是 `DECLARE_EVENT_CLASS`** ⇒ **17 个真实事件**（07 §3）。

**按类分组**：

| 类 | 事件 | 位置 |
|---|---|---|
| `regmap_reg`（class `:16`） | `regmap_reg_write`(`:38`) · `regmap_reg_read`(`:46`) · `regmap_reg_read_cache`(`:54`) | 3 个 |
| `regmap_bulk`（class `:62`） | `regmap_bulk_write`(`:87`) · `regmap_bulk_read`(`:95`) | 2 个 |
| `regmap_block`（class `:103`） | `regmap_hw_read_start`(`:124`) · `_read_done`(`:131`) · `_write_start`(`:138`) · `_write_done`(`:145`) | 4 个 |
| 缓存 | `regcache_sync`(`:152`，**独立 TRACE_EVENT**) · `regcache_drop_region`(`:253`) | 2 个 |
| `regmap_bool`（class `:175`） | `regmap_cache_only`(`:194`) · `regmap_cache_bypass`(`:201`) | 2 个 |
| `regmap_async`（class `:208`） | `regmap_async_write_start`(`:225`) · `regmap_async_io_complete`(`:232`) · `regmap_async_complete_start`(`:239`) · `_done`(`:246`) | 4 个 |

⇒ **3+2+4+2+2+4 = 17**。

⚠️ **计数陷阱**（99 §6）：**`DECLARE_EVENT_CLASS` 不产生事件**，
**数它会得到 22** ——和 timer / virtio 那两套踩过的是同一个坑。

**最有价值的一条设计**：**`regmap_reg_read` 和 `regmap_reg_read_cache` 是两个不同事件**
⇒ **从 trace 直接能算缓存命中率**（07 §3.2）——
**这是 regmap 可观测性优于大多数子系统的关键。**

**`regmap_hw_*` 那四个是"真的碰硬件"的事件** ⇒
**配合 `regmap_reg_*` 能看出"一次 API 调用产生了几次总线传输"**（分页、批量退化都能看出来）。

---

## §6 12 个总线适配，22 个实例

01 §1.2 已列全实例和变体。**这里只补三条观察**：

### §6.1 每个适配文件都只有 1–4 个导出符号

| 文件 | 导出符号 | 说明 |
|---|---|---|
| `regmap-mmio.c` · `regmap-spmi.c` | 各 4 | **有多个变体入口** |
| `regmap-ac97.c` | 3 | 含一个 `regmap_ac97_default_volatile()` 辅助 |
| 其余 9 个 | **各 2** | **就是 `regmap_init_<bus>` + `devm_regmap_init_<bus>`** |
| `regmap-i3c.c` | **1** | **只有 devm 版**（01 §3.1 的不对称） |

⇒ **"各 2 个"的规律非常整齐** ——**一个适配 = 一个 `regmap_bus` + 两个入口。**

### §6.2 适配层的代码量差异极大

| 文件 | 行数 | 为什么 |
|---|---|---|
| `regmap-i3c.c` | **60** | **最薄** ——I3C 本身就有寄存器语义 |
| `regmap-slimbus.c` | 71 | 薄 |
| `regmap-mmio.c` | **636** | **要处理 8/16/32/64 位 × 3 种字节序 × relaxed 变体** |
| `regmap-spi-avmm.c` | **713** | **最厚**（§7） |

⇒ **适配层的厚度 = "这个总线离寄存器语义有多远"。**

### §6.3 `regmap-mmio.c` 为什么这么厚

**MMIO 是唯一"寄存器式"的适配**（02 §5.3 的第二组回调）——
**它实现 `reg_read`/`reg_write` 而不是 `read`/`write`。**

**636 行主要在处理组合爆炸**：
**值宽度（8/16/32/64）× 字节序（BE/LE/NATIVE）× `use_relaxed_mmio`**
⇒ **每个组合一个 `readl`/`readl_relaxed`/`ioread32be` 之类的调用。**

⚠️ **DT 的三个字节序属性只对这个适配生效**（06 §1）。

---

## §7 SPI-AVMM：713 行，最特殊的一个适配

**`drivers/base/regmap/regmap-spi-avmm.c`(713 行) 是最大的总线适配文件**，
比核心的 `regcache.c`(827) 都快接近了。

**AVMM = Avalon Memory-Mapped**，Intel FPGA 内部的总线。
**它通过 SPI 隧道传输 AVMM 事务** ⇒
**适配层要自己实现一层协议封装（帧头、转义、CRC 之类）。**

⇒ **这就是"适配层厚度 = 离寄存器语义的距离"最极端的例子**（§6.2）：
**SPI 上跑一个内存映射总线协议，中间隔了两层。**

**它只有 2 个导出符号**（01 §0.5）⇒ 接口和别的适配一样简单。

⇒ **arm64 上除非用 Intel FPGA，否则碰不到。**

---

## §8 汇总表

### §8.1 子框架 × 接入方式 × 规模 × 相关性

| 子框架 | 接入 | 行数 | 导出符号 | 会碰到吗 |
|---|---|---|---|---|
| regmap-irq | **独立注册函数** | 1319 | **11** | **MFD/PMIC 一定会** |
| 缓存（4 文件） | `config->cache_type` | 1832 | **6（全在 `regcache.c`）** | **常会** |
| 分页 | `config->ranges` | 在核心里 | 0 | 大寄存器空间才会 |
| debugfs | **自动** | 692 | **0** | **调试一定会** |
| tracepoint | **自动** | 284 | — | **调试一定会** |
| 总线适配（13 文件） | 选 init 函数 | **约 3000** | **共 24** | **一定用一个** |
| SPI-AVMM | 同上 | 713 | 2 | Intel FPGA |

### §8.2 全树使用统计（每条都能重跑）

| 对象 | 口径 | 值 | 重跑命令 |
|---|---|---|---|
| `regmap_bus` 全树实例 | `struct regmap_bus X = {` | **61** | `grep -rn 'struct regmap_bus [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| 同上，regmap 目录内 | 同上 | **22** | 同上加 `drivers/base/regmap/*.c` |
| 同上，目录内的**文件数** | ⚠️ **另一个口径** | **13** | `... \| xargs -n1 dirname` 后去重 |
| 目录外自定义 | 61 − 22 | **39** | `drivers/mfd` 占 8 个文件最多 |
| `regcache_ops` 实例 | `struct regcache_ops X = {` | **3** | `grep -rn 'struct regcache_ops [a-z_]* = {' drivers/base/regmap/*.c` |
| 锁策略 | `static void regmap_(lock\|unlock)_*` | **7 组** | `grep -nE '^static void regmap_(lock\|unlock)_[a-z_]+\(' drivers/base/regmap/regmap.c` |
| tracepoint 事件 | ⚠️ **`TRACE_EVENT\|DEFINE_EVENT`（**不含** `DECLARE_EVENT_CLASS`）** | **17** | `grep -cE '^(TRACE_EVENT\|DEFINE_EVENT)\(' drivers/base/regmap/trace.h` |
| 设计债 | **排掉 `XXX` 字符串** | **1** | `grep -nE '(FIXME\|TODO\|REVISIT\|HACK\|\bXXX\b)' drivers/base/regmap/*.c \| grep -vE '"[^"]*XXX'` |

⚠️ **三条口径警告**（99 §6 详记）：
1. **`regmap_bus` 有四个口径**（全树/目录内实例/目录内文件数/目录外）——**别混用**
2. **tracepoint 不能数 `DECLARE_EVENT_CLASS`**（22 vs 17）
3. **设计债必须排掉 `"virtio,device..."` 那类占位串** ——本子系统没有这类串，
   **但 grep 模式要统一**（和 virtio 那套保持一致）

### §8.3 裁掉了什么

| 裁掉的 | 为什么 |
|---|---|
| **红黑树/LZO 缓存的内部算法** | **它们是通用数据结构问题**，不是 regmap 特有；**只讲"怎么选"**（§2.2） |
| **regmap-irq 的中断分发主循环** | 1319 行里只读了结构和 11 个导出符号（99 §2.2）；**它本质是一个 irq_chip 实现，属于 irq 子系统的知识** |
| **SPI-AVMM 的协议细节** | **Intel FPGA 专属**，arm64 通用场景碰不到（§7） |
| **12 个适配里 10 个的实现** | **只读了 `regmap-mmio.c` 和 `regmap-i2c.c` 的关键部分**（99 §2.2） |
| AC97 / SLIMbus / SoundWire / W1 / SPMI / MDIO / SCCB / I3C 这些总线本身 | **各属自己的子系统** |
| `regmap_irq_sub_irq_map` 的两级中断细节 | 只交代 `main_status` 那一组字段的用途（§1.2 组 1） |
