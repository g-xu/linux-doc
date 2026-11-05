---
title: 总览
---

# of 00：总览（复习入口）

> 版本：Linux 6.1.158 · 架构基准：arm64 · 本篇定位：**复习入口**
>
> 隔几个月回来，只读这一篇就能恢复全貌；要细节再翻后面九篇（§8 有导读表）。
>
> ⚠️ 本篇是**浓缩**，不是新内容。所有论断的取证都在对应的分篇里。
>
> ⚠️ **关于 §2**：设备树核心是**纯软件子系统**。
> 按 skill 的适配规则，§2 改讲**它必须理解的背景**：
> 为什么需要设备树、Open Firmware 的来历、arm64 的"一个内核跑多块板子"目标、
> DTB 格式与 bootloader 契约。这些决定了这个子系统为什么长成现在这样（§2.10 给映射表）。

---

## §1 定位：of 解决什么问题

### §1.1 一句话

**设备树核心把"bootloader 传来的一段二进制硬件描述"变成"内核里可查询的树 +
一堆 `platform_device`"，让同一个内核镜像能跑在不同的板子上。**

### §1.2 它管什么、不管什么

| | 内容 |
|---|---|
| **管** | 早期扫描（内存/命令行/reserved-mem）· unflatten（扁平 → 树）· 查找/遍历/属性读取 · phandle 解析 · **地址翻译** · **把节点变成 `platform_device`** |
| **不管** | **各属性的语义** —— `clocks` 的第二个参数是什么意思由 clk 子系统定（05 §3.1） |
| **不管** | **中断的完整机制** —— 只提供解析入口，域和映射在 irq 子系统（01 §8） |
| 不管 | DTS 的编译（那是 `scripts/dtc`） |
| **不管** | ACPI —— **设备树和 ACPI 是互斥的两条路**（99 §2.7） |

### §1.3 四个关键判据

| 判据 | 答案 | 出处 |
|---|---|---|
| 有自己的 `struct bus_type` 吗？ | **没有** —— 它是 `platform_bus_type` 的**设备来源** | 02 §2.2 |
| 有 tracepoint 吗？ | **一个都没有** | 07 §3 |
| 有调试 Kconfig 吗？ | **一个都没有**（16 个选项里零个调试项） | 07 §1.1 |
| 它的对外承诺是什么？ | **DT 绑定是稳定 ABI** —— 新内核必须读懂老 DTB | 02 §1.0.E · 08 §7 |

### §1.4 规模

| 项 | 数值 |
|---|---|
| `drivers/of/` 总行数 | **16297**（18 个文件） |
| ⇒ 产品代码 | 约 **12650**（减去 `unittest.c` 的 **3647**） |
| 最大四个产品文件 | `base.c` **2213** · `property.c` **1485** · `fdt.c` **1398** · `overlay.c` **1266** |
| 主头文件 | `include/linux/of.h` **1640** 行（比整个 cdev 子系统还大） |
| 导出符号 | **130**（61 `EXPORT_SYMBOL` + 69 `_GPL`） |
| 错误码点 | **228** 次（`-EINVAL` 占 **106**，46%） |
| 日志 | `pr_debug` **113** > `pr_err` **75** |
| tracepoint | **0** |
| `TODO`/`FIXME`/`XXX` | **1**（只有一条 `XXX`，`drivers/of/base.c:2046`） |
| 官方文档 | `Documentation/devicetree/` **947** 行（7 篇），其中 `usage-model.rst` **420** 行 |
| 测试 | `unittest.c` **3647** 行（占全目录 **22%**） |

**对比本系列**：

| 子系统 | 产品行数 | 导出符号 | `FIXME` 类标记 |
|---|---|---|---|
| irq | 15044 | 151 | 2 |
| **of** | **约 12650** | **130** | **1** |
| remoteproc | 5068 | 33 | 3（TODO） |
| cdev | 961 | 12 | 0 |

⇒ **它是本系列里代码最干净、文档最好的一套**（03 §C6）。

### §1.5 难点不在代码，在不成文的约定

12650 行代码读起来不难（只有一条 `XXX`），
但**四条约定**决定 DTS 写得对不对，而它们**大部分不在代码里**：

| # | 约定 | 写在哪 |
|---|---|---|
| ① | `#address-cells` / `#size-cells` **写在父节点上，每层可以不同** | DT 规范 + `of_n_addr_cells()`（`drivers/of/base.c:108`） |
| ② | phandle 的**参数个数由被引用节点的 `#xxx-cells` 决定** | DT 规范 + `of_parse_phandle_with_args()`（`drivers/of/base.c:1477`） |
| ③ | **只有父节点在四项白名单里，子节点才变成 `platform_device`** | **代码里**（`drivers/of/platform.c:24-32`） |
| ④ | **DT 绑定是稳定 ABI** | **只在社区约定和绑定文档里**（08 §7） |

⇒ **`Documentation/devicetree/usage-model.rst`（420 行）是唯一系统讲这些的地方** ——
本套 §2 和 03 篇的背景主要来自它（99 §1.4）。

---

## §2 背景：为什么会有设备树

### §2.1 问题：内核怎么知道硬件长什么样？

一个内核启动时必须回答一串问题：

| 问题 | x86 的答案 | 嵌入式 ARM 的答案（DT 之前） |
|---|---|---|
| 有多少内存、在哪 | BIOS/UEFI 的 e820 表 | **写死在内核代码里** |
| 有哪些外设、寄存器在哪 | PCI 枚举 + ACPI | **写死在内核代码里** |
| 中断怎么接的 | ACPI MADT | **写死在内核代码里** |
| 时钟怎么接的 | ACPI | **写死在内核代码里** |

**关键差异**：x86 的外设**大多可枚举**（PCI 能问"你是谁"），
而嵌入式 SoC 的外设**挂在不可枚举的总线上**（一段 MMIO 地址 + 几根中断线）——
⇒ **硬件不会自我介绍，必须有人告诉内核。**

### §2.2 DT 之前：board file 的灾难

2010 年前后 ARM Linux 的做法是每块板子一个 C 文件：

```c
/* arch/arm/mach-xxx/board-myboard.c（已经不存在了，只作说明） */
static struct resource myuart_resources[] = {
	{ .start = 0x12340000, .end = 0x12340fff, .flags = IORESOURCE_MEM },
	{ .start = 25,         .end = 25,         .flags = IORESOURCE_IRQ  },
};
static struct platform_device myuart = {
	.name = "myuart", .id = 0,
	.num_resources = ARRAY_SIZE(myuart_resources),
	.resource = myuart_resources,
};
static void __init myboard_init(void) { platform_device_register(&myuart); /* × 50 个设备 */ }
```

**后果**：

| 后果 | 说明 |
|---|---|
| `arch/arm/` 里堆了**几十万行**板级代码 | 一块板子几百到几千行 × 上千块板子 |
| 每加一块板子就要改内核 | **一个内核镜像只能跑一块板子** |
| 大量重复 | 同一颗 SoC 的十块板子有 90% 相同的代码 |
| 维护者不堪重负 | 2011 年 Linus 在邮件列表上明确表达了不满 |

### §2.3 解法：把硬件描述从代码里搬到数据里

借用 **Open Firmware**（IEEE 1275）的设备树概念 —— 它原本是
Sun SPARC 和 Apple PowerPC 固件里的一棵"设备树"，
Linux 的 PowerPC 移植早就在用它。

| 时间 | 事件 |
|---|---|
| 1994 | IEEE 1275（Open Firmware）标准 |
| 2005 前后 | PowerPC Linux 用它统一了 32/64 位的启动 |
| 2011 前后 | **ARM 引入 DT**，开始清理 board file |
| arm64 | **从第一天起就只支持 DT（和 ACPI），没有 board file** |

**`drivers/of/pdt.c`（213 行）是那段历史的化石** ——
它从 SPARC 的 PROM 直接读树（不用 DTB），
⇒ 这也解释了 `struct device_node` 里那两个 `CONFIG_SPARC` 字段
（`include/linux/of.h:69-70`，02 §5.1）。

### §2.4 目标：一个内核镜像跑多块板子

arm64 的现实：

```
   同一个 Image ────┬──▶ 板子 A：加载 boardA.dtb
                    ├──▶ 板子 B：加载 boardB.dtb
                    └──▶ 板子 C：加载 boardC.dtb
```

**这个目标推导出三个设计约束**：

| 约束 | 依据 |
|---|---|
| ① **硬件描述必须是数据，不是代码** | ⇒ DTB 格式 |
| ② **内核不能假设任何具体地址** | ⇒ **一切都要从树里读**（`of_property_read_*` / `of_address_to_resource`） |
| ③ **DTB 可能比内核老很多** | ⇒ **稳定 ABI**（§2.9 · 08 §7） |

### §2.5 DTB 格式：扁平化的树

内存里的树用指针，但**传给内核的必须是自包含的一块内存** ——
⇒ 用"扁平化"格式（Flattened Device Tree）：

```
   ┌── DTB 文件布局 ────────────────────────────┐
   │  header（magic 0xd00dfeed、版本、各段偏移） │
   │  memory reservation block                   │
   │  structure block                            │
   │    · FDT_BEGIN_NODE + 节点名                │
   │    · FDT_PROP + 名字偏移 + 长度 + 值        │
   │    · FDT_END_NODE                           │
   │  strings block（所有属性名放一起去重）      │
   └────────────────────────────────────────────┘
```

**三个设计要点**：

| 要点 | 为什么 |
|---|---|
| **属性名放在单独的 strings block** | `compatible` / `reg` / `status` 出现上千次 ⇒ 去重能省很多空间 |
| **整数全是大端** | 因为 Open Firmware 起源于大端的 SPARC/PowerPC ⇒ 这就是 `of_property_read_u32()` 要转字节序的原因（01 §4.5） |
| **memory reservation block** | 让固件能标记"这块内存我还在用" |

内核用 `scripts/dtc/libfdt` 的 `fdt_*` 函数读扁平格式（03 §A2）。

### §2.6 bootloader 契约

arm64 的启动约定（`Documentation/arm64/booting.rst`）：

| 项 | 约定 |
|---|---|
| DTB 的地址 | **放在 `x0` 寄存器里** |
| DTB 必须在哪 | 内核可访问的物理内存里，8 字节对齐 |
| 谁可以改 DTB | **bootloader 可以改**（填 MAC 地址、内存大小、`bootargs`） |

⇒ **这就是"sysfs 视图和 `.dtb` 文件可能不一致"的来源**（04 §4.4、07 §5.1）。

| 内核侧的接收 | 位置 |
|---|---|
| 校验并记下地址 | `early_init_dt_verify()`（`drivers/of/fdt.c:1271`） |
| 全局指针 | `initial_boot_params`（`drivers/of/fdt.c:473`，带 `__ro_after_init`） |

### §2.7 为什么"早期扫描"和"unflatten"是两个阶段

**鸡生蛋问题**：
- 要建 `device_node` 树需要 `kmalloc`；
- 但 `kmalloc` 需要先知道**有多少内存**；
- 而"有多少内存"写在 **DTB 的 `/memory` 节点**里。

⇒ **必须分两步**：

| 阶段 | 用什么读 DTB | 能做什么 |
|---|---|---|
| ① 早期扫描 | **直接在扁平 DTB 上遍历**（libfdt） | 扫 `/memory` 建 memblock、扫 `/chosen` 拿命令行、扫 reserved-memory |
| ② unflatten | 有分配器了 | **建整棵 `device_node` 树** |

| 元素 | 位置 |
|---|---|
| ① 入口 | `drivers/of/fdt.c:1307` 的 `early_init_dt_scan()` |
| 扫 `/memory` | `drivers/of/fdt.c:1096` |
| 扫 `/chosen` | `drivers/of/fdt.c:1151` |
| 扫根节点的 cells | `drivers/of/fdt.c:1060` |
| ② 入口 | `drivers/of/fdt.c:1327` 的 `unflatten_device_tree()` |

**unflatten 用"两遍扫描 + 一次大分配"**（03 §A3）：
先 dry-run 数出总大小，一次 alloc，第二遍在那块内存里构造。
⇒ 启动早期分配器脆弱 ⇒ **少调用少出错**。

### §2.8 为什么地址要"翻译"

一颗 SoC 内部是**多级互联**（NoC / bridge）：

```
   CPU ──▶ 主互联 ──┬──▶ 外设总线 A（窗口 0x40000000–0x4fffffff）
                    └──▶ 外设总线 B（窗口 0x50000000–0x5fffffff）
```

**一个外设的手册通常给"总线内偏移"**（"UART 在外设总线 A 的 0x1000"），
而不是 CPU 物理地址。

⇒ **DTS 照抄手册**（`reg = <0x1000 0x100>`）+
**在总线节点上写一次映射**（`ranges = <0x0 0x40000000 0x10000000>`）
⇒ 内核自己算出 `0x40001000`。

| 好处 | 说明 |
|---|---|
| DTS 可读性 | 和手册一致 |
| 可复用 | 同一颗 SoC 的两个变体只有互联窗口不同 ⇒ 只改总线节点的 `ranges` |
| 表达"不在 CPU 地址空间里" | **不写 `ranges`** ⇒ I2C/SPI 从设备的 `reg` 是总线地址（06 §3.2） |

实现：`__of_translate_address()`（`drivers/of/address.c:513`）
一层层往上走（03 §B4.1）。

### §2.9 为什么 DT 是稳定 ABI

DTB 和内核**独立发布**（§2.4 约束 ③）：

| | 谁发布 | 更新频率 |
|---|---|---|
| DTB | **设备厂商 / bootloader** | 可能**永不更新**（固化在 flash 里） |
| 内核 | 你 | 经常 |

⇒ **新内核必须读懂十年前的 DTB** ⇒ 三条硬约束（02 §1.0.E · 08 §7）：
不改已发布属性的语义 · 新属性缺失时有合理默认 · 不要求老 DTB 里没有的属性。

⚠️ **代码强制不了这个** —— `drivers/of/` 里没有一行在检查兼容性。
⇒ **全靠社区评审 + 绑定文档**（06 §8）。

### §2.10 背景事实 → 框架设计的映射

**把这张表记住，整个子系统的形状就推得出来了**：

| 背景事实 | 框架里对应的设计 |
|---|---|
| 嵌入式外设不可枚举（§2.1） | **整个设备树的存在理由** |
| board file 的灾难（§2.2） | `of_platform_populate()` 把描述变成设备（03 §A5） |
| 一个内核跑多块板子（§2.4） | **一切从树里读**：`of_property_read_*` / `of_address_to_resource` |
| DTB 是自包含的一块内存（§2.5） | 扁平格式 + `unflatten_device_tree()`（`drivers/of/fdt.c:1327`） |
| 起源于大端架构（§2.5） | **DT 里整数全是大端** ⇒ 类型化读取接口（01 §4.5） |
| 属性名重复上千次（§2.5） | 独立的 strings block |
| bootloader 可以改 DTB（§2.6） | sysfs 视图 ≠ `.dtb` 文件（04 §4.4） |
| 鸡生蛋（§2.7） | **早期扫描 + unflatten 两阶段**（03 §A2 §A3） |
| 启动早期分配器脆弱（§2.7） | 两遍扫描 + 一次大分配（03 §A3） |
| SoC 是多级互联（§2.8） | `ranges` + `__of_translate_address()`（03 §B4） |
| 手册给总线内偏移（§2.8） | `reg` 是相对地址（02 §1.0.D · 08 §3） |
| DTB 可能比内核老（§2.9） | **稳定 ABI** + 属性缺失返回 `-EINVAL`（01 §14.2） |
| 一个节点可能被引用（时钟/中断/DMA） | phandle + `#xxx-cells`（02 §1.0.B · 08 §2） |
| phandle 解析次数很多 | 哈希缓存（03 §B3） |
| 中断上下文也要读 DT | `devtree_lock` 是 **raw spinlock**（03 §B1.2） |
| 三类子节点不该变成设备（06 §7.3） | **填充白名单**（`drivers/of/platform.c:24-32` · 08 §5） |

---

## §3 概念模型

### §3.1 四方分工

```
   ┌── bootloader ─────────────────────────────────────────────┐
   │  DTB 地址放进 x0（arm64 启动约定）                          │
   └──────────────────────────┬───────────────────────────────┘
   ┌──────────────────────────▼───────────────────────────────┐
   │  早期扫描  drivers/of/fdt.c（直接读扁平 DTB）               │
   │  · /memory ⇒ memblock   · /chosen ⇒ 命令行                 │
   │  · unflatten ⇒ device_node 树                              │
   └──────────────────────────┬───────────────────────────────┘
   ┌──────────────────────────▼───────────────────────────────┐
   │  树访问 + 资源翻译  base.c · property.c · address.c        │
   └───────┬───────────────────────────────┬──────────────────┘
   ┌───────▼──────────────────┐  ┌─────────▼──────────────────┐
   │  设备填充 platform.c      │  │  驱动直接读                  │
   │  ⇒ platform_device        │  │  of_property_read_u32(…)     │
   │  ⇒ platform_bus_type      │  │  of_parse_phandle(…)         │
   └───────┬──────────────────┘  └────────────────────────────┘
   ┌───────▼──────────────────────────────────────────────────┐
   │  设备模型：按 of_match_table 匹配驱动 ⇒ probe               │
   └───────────────────────────────────────────────────────────┘
```

**两条独立的路**（02 §2.1）：
① **填充路**（DT → `platform_device` → probe）；
② **直读路**（probe 里自己读属性）。
⇒ 一个典型驱动**两条都走**。

### §3.2 三个对象

| 对象 | 一句话 | 详见 |
|---|---|---|
| `struct device_node` | **DT 里的一个节点** —— name / phandle / full_name / fwnode / 属性链 / 父子兄弟 / flags | 02 §5.1 |
| `struct property` | 一个属性 —— **`length` 是字节数、`value` 是大端原始字节** | 02 §5.2 |
| `struct of_phandle_args` | phandle 解析的结果 —— `np`（**带引用，要 put**）+ `args[16]` | 02 §5.4 |

### §3.3 五组最容易搞错的关系（02 §1.0 的浓缩）

| # | 错误理解 | 正确的 |
|---|---|---|
| A | `device_node` 就是设备 | **它是"图纸"，`platform_device` 是"立项档案"**；连接只有 `dev->of_node` 一个**单向**指针（08 §1） |
| B | `<&foo>` 是取地址 | **phandle 是 u32 标签**，dtc 编译时替换（08 §2） |
| C | 忘了 `of_node_put()` 会泄漏 | **`!OF_DYNAMIC` 时是空操作** —— 但仍然要配平（08 §6） |
| D | `reg` 是 CPU 物理地址 | **相对父总线**，要沿 `ranges` 逐层翻译（08 §3） |
| E | DT 是驱动的配置文件 | **它是硬件描述 + 稳定 ABI**（08 §7） |

### §3.4 术语速查

| 名字 | 含义 | ⚠️ 易混点 |
|---|---|---|
| `device_node` | DT 节点 | ⚠️ **不是 `struct device`** |
| `phandle` | u32 标签 | ⚠️ **不是指针** |
| `name` / `full_name` | 节点名（不含/含 `@unit-address`） | `include/linux/of.h:53` / `:55` |
| `of_find_*` | 全树查找，**会 get 引用** | ⚠️ 用完要 put |
| `of_get_*` | 从已知节点出发，**多数会 get** | ⚠️ `of_get_property()` **例外** |
| `of_property_read_*` | 类型化读取（**已转字节序**） | 首选 |
| `of_n_addr_cells` | **沿父链**找 `#address-cells` | ⚠️ 不是读本节点的 |
| `of_device_*` | 从 `struct device` 出发 | ⚠️ 和 `of_device_is_*`（从 `device_node` 出发）名字很像 |
| `%pOF` | **printk 格式符：打印节点全路径** | 调试时非常有用 |
| 两套 "bus" | `of_busses[]`（地址格式）vs 填充白名单（要不要建设备） | **毫无关系**（03 §C5 · 08 §8） |

---

## §4 数据结构速览

### §4.1 `struct device_node`（`include/linux/of.h:52-72`）

| 字段 | 行号 | 要点 |
|---|---|---|
| `name` / `full_name` | `include/linux/of.h:53` / `:55` | 不含 / 含 `@unit-address` |
| `phandle` | `include/linux/of.h:54` | **u32 标签** |
| `fwnode` | `include/linux/of.h:56` | **内嵌**的通用视图（`of_fwnode_handle()` 取它的地址，`:172`） |
| `properties` | `include/linux/of.h:58` | 属性单链表 |
| `deadprops` | `include/linux/of.h:59` | 被删的属性（overlay 撤销要还原） |
| `parent`/`child`/`sibling` | `include/linux/of.h:60-62` | **树形就是这三个指针** |
| `kobj` | `include/linux/of.h:64` | 引用计数 + `/sys/firmware/devicetree/`（`OF_KOBJ`） |
| `_flags` | `include/linux/of.h:66` | `OF_POPULATED` 等六个位（§4.3） |
| SPARC 专用两个 | `include/linux/of.h:69-70` | arm64 上不存在 |

### §4.2 `struct property`（`include/linux/of.h:32-46`）

| 字段 | 行号 | 要点 |
|---|---|---|
| `name` | `include/linux/of.h:33` | —— |
| `length` | `include/linux/of.h:34` | **字节数**（不是元素个数）⇒ `-EOVERFLOW` 的来源 |
| `value` | `include/linux/of.h:35` | **大端原始字节** |
| `next` | `include/linux/of.h:36` | 单链表 |
| `attr` | `include/linux/of.h:44` | sysfs 里那个文件（`OF_KOBJ`） |

### §4.3 `_flags` 的六个位（`include/linux/of.h:145-150`）

| 位 | 行号 | 含义 |
|---|---|---|
| `OF_DYNAMIC` | `include/linux/of.h:145` | 节点是 `kmalloc` 出来的 |
| `OF_DETACHED` | `include/linux/of.h:146` | 已摘下 |
| `OF_POPULATED` | `include/linux/of.h:147` | **已为它建过设备** |
| `OF_POPULATED_BUS` | `include/linux/of.h:148` | **已为它的孩子建过设备** |
| `OF_OVERLAY` / `OF_OVERLAY_FREE_CSET` | `:149` / `:150` | overlay 相关 |

访问器：`of_node_check_flag()`（`include/linux/of.h:190`）·
`_set_flag()`（`:201`）· `_clear_flag()`（`:206`）。

### §4.4 全局状态与锁

| 项 | 位置 | 说明 |
|---|---|---|
| `of_root` | `drivers/of/base.c:35` | 根节点（导出于 `:36`） |
| `of_chosen` | `drivers/of/base.c:37` | `/chosen`（导出于 `:38`） |
| `of_aliases` / `of_stdout` | `drivers/of/base.c:39` / `:40` | —— |
| `aliases_lookup` | `drivers/of/of_private.h:41` | `/aliases` 解析结果 |
| `devtree_lock` | `drivers/of/base.c:56` | **`raw_spinlock_t`** —— 树的读写 |
| `of_mutex` | `drivers/of/base.c:51` | changeset / overlay 串行化 |
| `phandle_cache[]` | `drivers/of/base.c:141` | 单槽直接映射缓存 |
| `initial_boot_params` | `drivers/of/fdt.c:473` | DTB 的地址（`__ro_after_init`） |

**`devtree_lock` 必须是 raw spinlock**：中断上下文也读 DT（03 §B1.2）
⇒ **持它时不能分配内存** ⇒ changeset 要两阶段（03 §B1.3）。

---

## §5 机制速览

### §5.1 十条一句话

| # | 机制 | 一句话 | 详见 |
|---|---|---|---|
| 1 | **早期扫描** | 直接读扁平 DTB（分配器还没就绪）⇒ memblock / 命令行 / reserved-mem | 03 §A2 |
| 2 | **unflatten** | **两遍扫描 + 一次大分配** ⇒ `device_node` 树 | 03 §A3 |
| 3 | **sysfs 视图** | `of_core_init()` 建 `/sys/firmware/devicetree/` + 填 phandle 缓存 | 03 §A4 · 04 §4 |
| 4 | **设备填充** | 递归，**父节点在四项白名单里才继续下钻** | 03 §A5 · 08 §5 |
| 5 | **地址翻译** | 沿父链逐层走 `ranges`，每层重新取 cells 数 | 03 §B4 · 08 §3 |
| 6 | **中断惰性解析** | 地址在填充时翻译（eager），**中断在 probe 时**（lazy，为了能 `-EPROBE_DEFER`） | 03 §A6 |
| 7 | **属性读取三层** | inline 包装 → 长度校验+字节序 → 加锁遍历属性链 | 03 §B5 |
| 8 | **phandle 缓存** | 单槽直接映射 + tag 校验；未命中退化成遍历 | 03 §B3 |
| 9 | **changeset** | 事务：执行和通知分两步（因为通知回调会睡） | 04 §1.2 |
| 10 | **reserved-memory** | **两个阶段**（早期扫描登记 + probe 时绑到 DMA） | 04 §2.1 |

### §5.2 四条"默认值/约定决定行为"的链

| 链 | 后果 |
|---|---|
| `status` 只有 `"okay"`/`"ok"` 算可用 | **拼错就等于 disabled**（06 §4.1） |
| 填充白名单只有四项 | **子节点默认不变成设备**（06 §7.1） |
| `of_property_read_bool()` 只看存在性 | 写 `flag = <0>;` **也是 true**（06 §1.2） |
| `dma-coherent` 缺失时的默认 | 取决于 `CONFIG_OF_DMA_DEFAULT_COHERENT`（06 §3.4） |

### §5.3 五个"没有日志"的坑（06 §10.4）

| 现象 | 原因 | 判据 |
|---|---|---|
| 节点在但没设备 | `status` 拼错 / 父节点不在白名单 | `ls /sys/bus/platform/devices/` + 查父 compatible |
| 寄存器读出全 0 | **`#address-cells` 写错** | `hexdump` sysfs 里的 `reg` + 对比 `/proc/iomem` |
| 可选属性没生效 | 属性名拼错 ⇒ 驱动读到 `-EINVAL` | `ls /sys/firmware/devicetree/base/<路径>/` |
| phandle 引用错对象 | 参数个数和 `#xxx-cells` 不一致 | `dtc -I dtb` 看 phandle 值 |
| 布尔属性关不掉 | 写成了 `flag = <0>;` | **删掉那一行才是关** |

**前三条的统一判据都是 `/sys/firmware/devicetree/base/`**（07 §4）。

### §5.4 三条铁律（05 §0.2）

| # | 铁律 |
|---|---|
| ① | `of_find_*` / `of_get_*` / `of_parse_phandle` 返回的节点要 `of_node_put()` |
| ② | 用 `of_property_read_*` 而不是 `of_get_property` + 手工解析 |
| ③ | `-EPROBE_DEFER` 原样返回，不打错误日志 |

---

## §6 端到端调用路径

### §6.1 启动全景

```
   ① bootloader：DTB 地址 → x0
   ② early_init_dt_verify()                    drivers/of/fdt.c:1271
        · fdt_check_header + initial_boot_params（drivers/of/fdt.c:473）
   ③ early_init_dt_scan()                      drivers/of/fdt.c:1307
        ├─ early_init_dt_scan_chosen()          drivers/of/fdt.c:1151
        ├─ early_init_dt_scan_root()            drivers/of/fdt.c:1060  记 cells
        └─ early_init_dt_scan_memory()          drivers/of/fdt.c:1096  memblock
      early_init_fdt_scan_reserved_mem()        drivers/of/fdt.c:630
      early_init_fdt_reserve_self()             drivers/of/fdt.c:654
   ④ unflatten_device_tree()                    drivers/of/fdt.c:1327
        └─ __unflatten_device_tree()            drivers/of/fdt.c:365
             ├─【第一遍】unflatten_dt_nodes()   drivers/of/fdt.c:284（dryrun 数大小）
             ├─ 一次大 alloc
             └─【第二遍】unflatten_dt_alloc()   drivers/of/fdt.c:96（切）
   ⑤ of_core_init()                             drivers/of/base.c:166
        ├─ /sys/firmware/devicetree/           drivers/of/kobj.c:115
        └─ 填 phandle 缓存                      drivers/of/base.c:181-182
   ⑥ fdt_init_reserved_mem()                    drivers/of/of_reserved_mem.c:254
   ⑦ of_platform_default_populate()             drivers/of/platform.c:504
        └─ of_platform_populate()               drivers/of/platform.c:465
             └─ of_platform_bus_create()（递归） drivers/of/platform.c:343
                  └─ of_platform_device_create_pdata()  drivers/of/platform.c:167
                       └─ of_device_alloc()     drivers/of/platform.c:112
   ⑧ platform_bus_type 匹配 of_match_table ⇒ probe
```

### §6.2 `of_device_alloc()` 的五件事

| 步 | 动作 | 行号 |
|---|---|---|
| ① | `platform_device_alloc("", PLATFORM_DEVID_NONE)` | `drivers/of/platform.c:120` |
| ② | 数 `reg` 个数 | `drivers/of/platform.c:125-126` |
| ③ | 逐个翻译成 `struct resource`（失败只 `WARN_ON`） | `drivers/of/platform.c:129-142`（`WARN_ON` 在 `:140`） |
| ④ | `dev->dev.of_node = of_node_get(np)` | `drivers/of/platform.c:144` |
| ⑤ | `fwnode` / `parent` / 名字 | `drivers/of/platform.c:145-151` |

⚠️ **③ 里 `WARN_ON` 不阻止流程** ⇒ 地址翻译失败也会建出设备（08 §9 误导点 3）。

### §6.3 地址翻译的循环

```
   __of_translate_address(dev, get_parent, in_addr, "ranges", host)
                                              drivers/of/address.c:513
     ├─ of_node_get(dev)                       drivers/of/address.c:527
     ├─ bus = of_match_bus(parent)             drivers/of/address.c:534
     ├─ bus->count_cells(dev, &na, &ns)        drivers/of/address.c:537
     ├─ memcpy(addr, in_addr, na*4)            drivers/of/address.c:542  本地副本
     └─ for (;;) {                             drivers/of/address.c:549
            dev = parent; parent = get_parent(dev)      :553-555
            parent == NULL ⇒ of_read_number(addr, na)   :558-562  到根
            pbus->count_cells(dev, &pna, &pns)          :579
            of_translate_one(dev, bus, pbus, addr, …)   :589      一层翻译
            na = pna; ns = pns; bus = pbus;              :593-595
        }
     └─ bail: of_node_put(parent); of_node_put(dev)      :599-601
```

**`of_translate_one()`（`drivers/of/address.c:433`）的三种结局**：
没 `ranges`（`:464`）· 空 `ranges` 1:1（`:471`）· 窗口里找不到（`:486`）——
⇒ **这三条 `pr_debug` 是调试地址问题的唯一线索**（07 §6.2）。

### §6.4 驱动 probe 里的三件事（03 §A6）

| 事 | API | 时机 |
|---|---|---|
| 读属性 | `of_property_read_u32()` ⇒ `drivers/of/property.c:339` | probe |
| 取地址 | `devm_platform_ioremap_resource()` —— **resource 已经填好了** | 填充时就翻译了 |
| 取中断 | `platform_get_irq()` ⇒ `of_irq_get()`（`drivers/of/irq.c:475`） | **probe 时才解析**（能 `-EPROBE_DEFER`） |

---

## §7 子系统关系

### §7.1 依赖谁

| 依赖 | 强度 | 说明 |
|---|---|---|
| `scripts/dtc/libfdt` | 必需 | 早期扫描直接读扁平 DTB |
| memblock | 必需 | 早期建物理内存表 |
| 设备模型（`platform_bus_type`） | 必需 | 填充的目标 |
| kobject | `OF_KOBJ` 时 | sysfs 视图 + 引用计数 |
| CMA / dma-coherent | `OF_RESERVED_MEM` 时 | reserved-memory 的落地 |

### §7.2 谁依赖它

**arm64 上几乎所有驱动** —— 这是内核里被依赖最广的子系统之一。

| 使用者 | 关系 |
|---|---|
| **所有 platform 驱动** | 它们的 `platform_device` **全是 of 造的** |
| **irq / clk / gpio / dma / reset / pinctrl / regulator** | 各自定义 `#xxx-cells` 语义，用 `of_parse_phandle_with_args()` 解析（05 §3.3） |
| i2c / spi / mdio 核心 | 用 `for_each_available_child_of_node()` 枚举从设备 |
| remoteproc | `of_reserved_mem_device_init_by_idx()`（04 §2） |
| 媒体 / DRM | graph（04 §3） |

### §7.3 和 `platform_bus_type` 的关系

of **没有** `bus_type`，但它是 `platform_bus_type` 的**设备来源**
（02 §2.2）：那行赋值在 `drivers/of/platform.c:186`。

**对比 remoteproc**：那套也没有 `bus_type`，但它**创造 virtio 设备**。
⇒ **两者是同一类角色：某条总线的设备来源** ——
区别是数据来源不同（DTB vs 固件资源表，08 §1 会用这个类比）。

### §7.4 ⚠️ 和 ACPI 的关系：互斥

| | 设备树 | ACPI |
|---|---|---|
| 谁提供 | bootloader 的 DTB | 固件的 ACPI 表 |
| 内核侧 | `drivers/of/` | `drivers/acpi/` |
| 统一抽象 | **`fwnode`** —— `of_fwnode_ops`（`drivers/of/property.c:1485`） |
| 一台机器能同时用吗 | **不能**（arm64 上二选一） |
| 本套覆盖 | 覆盖 | **不覆盖**（99 §2.7） |

**`fwnode` 的意义**：让驱动用 `device_property_read_u32()` 一套代码
同时支持两条路（01 §10）。

### §7.5 对比：它在本系列里的位置

| | i2c/spi（总线型） | irq / remoteproc（框架型） | cdev（VFS 层） | of |
|---|---|---|---|---|
| `bus_type` | 有 | 没有 | 没有 | 没有 |
| 它是什么 | 设备的组织方式 | 硬件抽象 | 路由 | **所有驱动的公共输入** |
| 产品行数 | 千行级 | 5k–15k | 961 | **约 12650** |
| 难点 | 拓扑与协议 | 状态机 | 引用计数 | **不成文的约定**（§1.5） |
| 对外承诺 | —— | —— | —— | **稳定 ABI** |

---

## §8 导读：我该看哪一篇

### §8.1 按问题找篇目

| 我想知道 | 去哪篇 |
|---|---|
| 130 个 API 分成哪几组、14 种错误码什么意思 | **01** |
| 结构体字段、五组关系、两把锁、两套 bus 抽象 | **02** |
| 启动/翻译/填充的完整时间线，phandle 缓存，设计债 | **03** |
| overlay / reserved-memory / graph / sysfs / unittest / kexec | **04** |
| 我要写驱动（消费者 / 复合设备 / 提供者） | **05** |
| **我要写 DTS** | **06** |
| 配了不生效怎么定位 | **07** |
| 「我理解不了 X」「为什么是这样」 | **08** |
| 这些结论怎么来的、哪些没验证 | **99** |

### §8.2 按角色推荐阅读顺序

| 角色 | 顺序 |
|---|---|
| **第一次接触** | 00 §2（背景）→ 08 §1 §3 → 02 §1.0 → 03 §A |
| **只写设备驱动** | 00 §3.1 → 05 §1 → 01 §4（属性）→ 07 §8 |
| **只写 DTS** | 06 全篇 → 08 §3 §4 §5 → 07 §4 |
| **写复合设备 / 提供者** | 05 §2 §3 → 08 §5 → 02 §1.0.A |
| **在排查"配了不生效"** | 07 §8 速查 → 07 §4.3 → 06 §10 |
| **在排查地址问题** | 08 §3 §4 → 07 §6.2 → 03 §B4 |
| **要评审 DTS 或绑定** | 06 §8 → 08 §7（稳定 ABI）→ 05 §4 |
| **要复核结论** | 99 |

### §8.3 只记七条的话

1. **`device_node` 是"图纸"、`platform_device` 是"立项档案"** ——
   连接只有 `dev->of_node` 一个**单向**指针（08 §1）。
2. **`reg` 是相对父总线的地址**，要沿 `ranges` 逐层翻译；
   "没有 `ranges`"的意思是"这层下面不在 CPU 地址空间里"（08 §3）。
3. **`#address-cells` 写在父节点上，每层可以不同**；
   写错了**不报错**，表现是寄存器读出全 0（08 §4）。
4. **不是所有节点都会变成设备** —— 需要父节点在**四项白名单**里，
   或父驱动自己调 `devm_of_platform_populate()`（08 §5）。
5. **phandle 是 u32 标签，参数个数由被引用节点的 `#xxx-cells` 决定**（08 §2）。
6. **`status` 只有 `"okay"`/`"ok"` 算可用 —— 拼错就等于 disabled**（06 §4.1）。
7. **调试第一步永远是 `ls /sys/firmware/devicetree/base/<路径>/`** ——
   它反映内核实际看到的树；整数属性用 `hexdump` 不用 `cat`（07 §8.2）。

---

**取证与未验证项**：见 99 篇。
