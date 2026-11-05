---
title: 取证附录
---

# mfd 99：取证与限制

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇登记**前九篇每个数字的口径、每条推导的边界、以及所有已知不可靠之处**。
⚠️⚠️ **看前九篇里任何一个数字觉得可疑，都到这里核对口径。**

---

## §1 取证边界

### §1.1 ⚠️⚠️ 核心文件不是原生 6.1，接了 8 个 stable backport

**工作区状态**：`git status --porcelain -- drivers/mfd include/linux/mfd` ⇒ **0 行，全部干净**
（无未提交改动，同 pwm / dma-buf，与 tty 不同）。

⚠️ **但"干净"不等于"原生"。** 本树 **没有任何 git tag**
（`git tag | wc -l` ⇒ 0）⇒ **无法用 `v6.1..HEAD` 判断哪些提交是 backport**
⇒ ⚠️ **改用作者日期口径：v6.1 发布日 2022-12-11 之后的提交必然是 backport。**

| 文件 | 总提交 | ⚠️ 2022-12-11 之后 |
|---|---|---|
| `drivers/mfd/mfd-core.c` | 73 | **1** |
| `include/linux/mfd/core.h` | 49 | **1** |
| `drivers/mfd/syscon.c` | 50 | ⚠️ **5** |
| `include/linux/mfd/syscon.h` | 15 | 2 |

**去重后共 8 个 backport**：

| 提交 | 日期 | 影响本套哪一篇 |
|---|---|---|
| ⚠️⚠️ `ee6b91411d3f` mfd: core: Ensure disabled devices are skipped without aborting | 2023-08-18 | **03 §2.4 / 07 §4.1 的"静默成功"行为** |
| `f1ed6c4e59f9` mfd: core: Un-constify mfd_cell.of_reg | 2023-08-28 | 02 §2.4 的 `of_reg` 字段类型 |
| ⚠️ `166fd2aa73ec` mfd: syscon: Add of_syscon_register_regmap() API | 2024-06-21 | **04 §5 的"零调用者"** |
| `dd955e4215f1` mfd: syscon: Remove extern from function prototypes | 2024-02-20 | 01 §4 的原型写法 |
| `3eb47e41c205` mfd: syscon: Call of_node_put() only when of_parse_phandle() takes a ref | 2024-02-20 | 03 §6.1 的引用计数 |
| `527e8c5f3d00` mfd: syscon: Fix null pointer dereference in of_syscon_register() | 2023-12-04 | 03 §6.1 |
| `5481df0e059e` mfd: syscon: Use scoped variables with memory allocators | 2024-07-07 | ⚠️ **`syscon.c:57` 的 `__free(kfree)`** |
| `fa78395d955a` mfd: syscon: Fix race in device_node_get_regmap() | 2024-12-17 | 03 §6.1 的并发 |

⚠️⚠️ **三条必须记住的后果**：

1. **`ee6b91411d3f` 是一次行为变更，不是修 bug**：
   ⇒ ⚠️ **原生 6.1 遇到 disabled 子节点是"中止整批注册"，本树是"跳过并当成功"**
   ⇒ **03 §2.4 / 07 §4.1 描述的静默行为只对本树（及打了这个 backport 的 6.1.y）成立**，
   照搬到别的 6.1.x 之前必须先确认这个提交在不在。
2. ⚠️⚠️ **`of_syscon_register_regmap()` 零调用者的原因由此解释**：
   API 是 2024-06 backport 进来的，**而它在 mainline 里的使用者没有一起 backport**
   ⇒ **这不是"一个没人要的接口"，是"backport 只搬了一半"**（§6 第 7 条）。
3. `5481df0e059e` 把 `__free(kfree)` 这种 6.11+ 的 scoped cleanup 语法带进了 6.1
   ⇒ ⚠️ **读 `syscon.c` 时会看到与周围 6.1 代码风格不一致的写法**，那是 backport 痕迹，不是笔误。

⚠️ **本套所有行号均以本树 HEAD（`f6e38ae624cf` Linux 6.1.158）为准。**

### §1.2 类型判定：门 1 命中了，但判定仍为框架型

| 门 | 命令 | 结果 |
|---|---|---|
| 门 1（有没有自己的 bus） | `grep -rn 'struct bus_type' drivers/mfd/` | ⚠️ **命中 1 处** |
| 门 2（有没有 uapi） | `ls include/uapi/linux/mfd*` | ⚠️ **不存在** |

⚠️⚠️ **门 1 那处命中必须解释清楚**：

```c
/* drivers/mfd/mcp-core.c:44 */
static struct bus_type mcp_bus_type = {
```

⚠️ MCP = Multimedia Communication Port，是 2001 年 StrongARM 时代的一条**独立总线**，
只服务 `ucb1x00-*` 与 `mcp-sa11x0` 两组驱动。
⇒ ⚠️⚠️ **它不是 mfd 核心的一部分**：`mfd-core.c` 里没有任何 `bus_type`，
子设备走的是 platform bus。
⇒ **判定：框架型（借 platform bus），不是总线型。** 08 §2 用它作为"路一"的实物证据。

⚠️ 门 2 附带结论：`include/uapi/linux/` 下唯一形近的是 `memfd.h`（**与 mfd 无关**）
⇒ **无用户态 ABI、无字符设备、无 ioctl、无 sysfs 属性、无 debugfs**（07 §1）。

### §1.3 范围界定：为什么只算 1092 行

⚠️ `drivers/mfd/` **237 个 `.c` / 112426 行**，本套只把 **1092 行**当作"核心"：

| 文件 | 行数 | 纳入理由 |
|---|---|---|
| `drivers/mfd/mfd-core.c` | 471 | 框架本体 |
| `include/linux/mfd/core.h` | 163 | 唯一的框架头 |
| `drivers/mfd/syscon.c` | 378 | ⚠️ 见下 |
| `include/linux/mfd/syscon.h` | 80 | 同上 |

⇒ **核心占全目录 0.56%**，是本套见过最极端的"薄核心 + 巨量驱动"。

⚠️⚠️ **syscon 纳入核心的三条理由**（这是一个判断，不是事实，必须登记）：
① 住在 `drivers/mfd/` 里且被官方 `mfd.txt:3-16` 归为 MFD 的一类；
② 被 **112 个 arm64 dts** 与 **198 个 `.c`** 使用，远超 mfd 核心自己的 134 个；
③ 导出 7 个符号，是跨子系统基础设施，不是单芯片驱动。
⇒ ⚠️ **反对意见也成立**：它 `select` 不到 `MFD_CORE`（`drivers/mfd/Kconfig:1429-1431` 是 `bool`），
**与 mfd 核心毫无编译依赖** ⇒ 有人会主张它该独立成篇。**本套选择纳入，并在 08 §6 说明概念上的对称性。**

⚠️ **其余 235 个 `.c` 全部按"单芯片驱动"处理**，只在 04 篇挑四个代表
（axp20x / madera / ocelot-core / rt4831）。
⚠️ 同理 `include/linux/mfd/` 下 **231 个 `.h` / 72605 行**里
**229 个是单芯片寄存器定义** ⇒ 02 篇没有去数它们。

⚠️ **裁掉的还有**：`Documentation/driver-api/sm501.rst`（单芯片说明）· `mcp-core.c` 那条老总线
（除 §1.2 的判定用途外不展开）· ACPI 路径（§2.7）。

### §1.4 ⚠️⚠️ 官方框架文档：只有两篇 DT 绑定，零篇 driver-api

**这是本子系统最大的取证限制。**

| 想找的 | 结果 |
|---|---|
| `Documentation/driver-api/mfd*` | ⚠️⚠️ **不存在** |
| `Documentation/mfd/` 目录 | ⚠️⚠️ **不存在** |
| `check_doc_coverage.sh mfd` 的【必查】档 | ⚠️⚠️ **共 0 篇** |
| `Documentation/devicetree/bindings/mfd/mfd.txt` | ⚠️ **存在，51 行**（老式 `.txt`） |
| `Documentation/devicetree/bindings/mfd/syscon.yaml` | ⚠️ **存在，118 行** |
| `Documentation/devicetree/bindings/mfd/` 其余 | 83 个 `.yaml` / 137 项，⚠️ **全是单芯片绑定** |

⚠️⚠️ **对 PROGRESS.md 基线的一处修正**：基线写的是"框架级只有 `mfd.txt`"，
**实际是两篇** —— `syscon.yaml`（118 行）也在。06 §1 用的是修正后的版本。

⇒ ⚠️⚠️ **这条限制的实际影响**：**本套关于"为什么这样设计"的每一句话都是从代码推的，
没有官方文字可以引用。** 具体地：

| 结论 | 有没有官方依据 |
|---|---|
| `mfd_add_devices()` 七个参数的语义 | ⚠️ 只有 `mfd-core.c:336-360` 的 kerneldoc |
| `level` / `MFD_DEP_LEVEL_HIGH` 的用途 | ⚠️⚠️ **零文档、零注释** ⇒ 04 §3.2 的"中间插入父驱动动作"**是从 madera 的代码反推的** |
| `simple-mfd` 与 `mfd_add_devices()` 怎么选 | ⚠️ **有** —— `mfd.txt:23-26` 明确写了判据 |
| `syscon` 算不算 MFD | ⚠️ **有** —— `mfd.txt:3-16` |
| 为什么不建自己的 bus | ⚠️⚠️ **零文档** ⇒ 08 §2 全是推论 |
| `DEVTYPE=mfd_device` 可用于识别 | ⚠️⚠️ **零文档** ⇒ 07 §3 从 `mfd-core.c:32-34 :202` + `drivers/base/core.c:2595` 推出 |

⚠️ **`check_doc_coverage.sh` 的输出口径**：它统计【必查】档 0 篇 ⇒ "必查档全部有引用"这句
**是空集为真**，不构成任何质量证明。DT 绑定档里被引用的 3 篇是
`syscon`（355 次）· `mscc,ocelot`（4 次）· `richtek,rt4831`（2 次）。

---

## §2 数字口径

### §2.1 规模数字

| 数字 | 命令口径 |
|---|---|
| 237 个 `.c` / 112426 行 | `find drivers/mfd -name '*.c' \| wc -l` / `cat` 后 `wc -l` 求和 |
| 471 / 163 / 378 / 80 | `wc -l` 单文件 |
| 231 个 `.h` / 72605 行 | `find include/linux/mfd -name '*.h'`（**含 `wm8994/` 等子目录**） |
| Kconfig 2267 行 / 204 个 `config` | `grep -c '^config '` ⇒ ⚠️ **只数行首 `config`**，不含 `menuconfig` |
| 最大的十个驱动 | `wc -l drivers/mfd/*.c \| sort -rn`；⚠️ **前六名有五个是纯寄存器表** |

### §2.2 导出符号

⚠️ 口径：`grep -cE '^EXPORT_SYMBOL'`（**行首**，不含被 `#if` 包住的判断）。

| 组 | 数量 | 宏 |
|---|---|---|
| `mfd-core.c` | **6** | ⚠️⚠️ **全部是裸 `EXPORT_SYMBOL`，一个 `_GPL` 都没有** |
| `syscon.c` | **7** | ⚠️ 全部 `EXPORT_SYMBOL_GPL` |

⚠️ 位置：核心 `drivers/mfd/mfd-core.c:47 :60 :372 :412 :420 :468`；
syscon `drivers/mfd/syscon.c:221 :227 :236 :252 :275 :304 :322`。

⚠️⚠️ **同一个目录里三种导出口径并存**：裸 `EXPORT_SYMBOL`（核心）·
`_GPL`（syscon）· `EXPORT_SYMBOL_NS(..., MFD_OCELOT)`（`drivers/mfd/ocelot-core.c:80 :156`）
⇒ 04 §4.3 用它说明这个目录的历史分层。

### §2.3 结构体字段数

⚠️ 口径：**数花括号深度为 1 的成员声明**，不是 `grep -c ';'`
（tty 那次踩过的坑：`} __randomize_layout;` 结尾会让"找第一个 `};`"冲过头）。
⚠️ **mfd 的三个结构都以 `};` 正常结尾，无 `__randomize_layout`。**

| 结构 | 位置 | 字段数 |
|---|---|---|
| `struct mfd_cell` | `include/linux/mfd/core.h:66-124` | **20** |
| `struct mfd_cell_acpi_match` | `include/linux/mfd/core.h:56-59` | 2 |
| `struct mfd_of_node_entry` | `drivers/mfd/mfd-core.c:26-30` | 3（⚠️ 核心私有，不在头文件里） |

⚠️ `core.h` 里的宏 8 个：`MFD_RES_SIZE`(`:15`) · `MFD_CELL_ALL`(`:17-29`) ·
`MFD_CELL_OF_REG`(`:31`) · `MFD_CELL_OF`(`:34`) · `MFD_CELL_ACPI`(`:37`) ·
`MFD_CELL_BASIC`(`:40`) · `MFD_CELL_RES`(`:43`) · `MFD_CELL_NAME`(`:46`)
⇒ ⚠️ **后七个全是 `MFD_CELL_ALL` 的偏特化**（01 §3）。

### §2.4 使用面统计

⚠️⚠️ **全部是"文件数"口径（`grep -rl ... | wc -l`），不是调用点数。** 两者差别很大。

| 符号 | 文件数 |
|---|---|
| `mfd_add_devices` | **134** |
| `devm_mfd_add_devices` | **68**（04 §1 那个 66 是只数 `drivers/mfd/` 下的） |
| `#include <linux/mfd/core.h>` | 180 |
| `mfd_get_cell` | **9**（⚠️ 其中 7 个是真调用，26 个调用点，05 §3.4 逐一列了） |
| `mfd_cell_enable` | ⚠️ **2** ⇒ 近乎死 API |
| `MFD_CELL_*` 宏 | ⚠️ **8** ⇒ **绝大多数驱动手写 cell 初始化** |
| `syscon_regmap_lookup_by_phandle` | **198** |
| `syscon_node_to_regmap` | **164** |
| `syscon_regmap_lookup_by_compatible` | 57 |
| `device_node_to_regmap` | 30 |
| `_by_phandle_args` / `_by_phandle_optional` | 各 3 |
| ⚠️⚠️ `of_syscon_register_regmap` | **0**（除自身定义外） |

⚠️ **总线分布**（`drivers/mfd/*.c` 里定义了哪种 driver 结构，**一个 `.c` 可同时注册两种**）：
I2C **95** · platform **48** · SPI **14** · PCI **12** · USB **2**。
⚠️ `devm_regmap_init_i2c` **72** · `regmap_add_irq_chip` **52**（均为 `drivers/mfd/` 内文件数）。

### §2.5 Kconfig / Makefile

| 项 | 取证 |
|---|---|
| `MFD_CORE` | `drivers/mfd/Kconfig:9-12`：`tristate` / `select IRQ_DOMAIN` / `default n`，⚠️ **无 prompt** |
| `MFD_SYSCON` | `:1429-1431`：⚠️ **`bool`** / `select REGMAP_MMIO`，⚠️⚠️ **不 `select MFD_CORE`** |
| `select MFD_CORE` | ⚠️ **160 处 / 17 个 Kconfig 文件**（`drivers/mfd/Kconfig` 占 140） |
| ⚠️ arm64 平台强制项 | **`arch/arm64/Kconfig.platforms:45`（`ARCH_BCM2835`）是 arm64 上唯一一个** |
| Makefile | `drivers/mfd/Makefile:121`（核心）· `:229`（syscon）· `:78-94`（madera 五张表）· `:123-124`（ocelot） |

⚠️ **160 这个数字曾被写成 159**，以 `grep -rn 'select MFD_CORE' \| wc -l` 为准 ⇒ **160**。

### §2.6 DT 普查

| 项 | 数字 | 口径 |
|---|---|---|
| arm64 dts/dtsi 用 `simple-mfd` | **62** | `grep -rl simple-mfd arch/arm64/boot/dts \| wc -l` ⇒ ⚠️ **文件数** |
| arm64 dts/dtsi 用 `"syscon"` | **112** | 同口径 |
| `Documentation/devicetree/bindings/mfd/` | 137 项 / 83 个 `.yaml` | `find` |

⚠️⚠️ **62 vs 112 这个对比是 06 §6 的核心结论**：约一半的 syscon 节点不带 `simple-mfd`
⇒ 只被当寄存器块用，没有子设备。

### §2.7 ⚠️ ACPI 裁剪登记（arm64-only 的代价）

⚠️⚠️ **`mfd-core.c` 里有一条真实的 ACPI 路径，本套按 arm64-only 全部裁掉**：

| 元素 | 位置 |
|---|---|
| `#if IS_ENABLED(CONFIG_ACPI)` | `drivers/mfd/mfd-core.c:62` |
| `match_device_ids()` | `:68`（`acpi_match_device_ids` 在 `:72`） |
| `mfd_acpi_add_device()` | `:80-117`（`acpi_dev_for_each_child` 在 `:109`） |
| 空桩 | `:119` |
| 调用点 | ⚠️ `:246`（**在 `of_node` 匹配之后、`platform_data` 之前**） |
| `acpi_check_resource_conflict()` | ⚠️ `:293`（受 `cell->ignore_resource_conflicts` 控制） |
| `struct mfd_cell_acpi_match` | `include/linux/mfd/core.h:56-59` |
| `mfd_cell.acpi_match` 字段 | `include/linux/mfd/core.h:101` |
| `MFD_CELL_ACPI` 宏 | `include/linux/mfd/core.h:37`（`.acpi_match` 在 `:27`） |

⚠️ **裁掉的后果**：
① **02 篇的 `mfd_cell` 20 个字段里，`acpi_match` 只登记不展开**；
② **03 篇的主流程在 `:246` 处标注"arm64 上是空桩"**；
③ ⚠️⚠️ **`-EBUSY` 的一个来源（`:293` 的资源冲突检查）在 arm64 上不成立**
   ⇒ 07 §4.3 那张错误码表里这一行必须带上这个限定。
⚠️ **同 pwm（核心有一条 ACPI 路径），不同于 dma-buf（ACPI 零命中）** —— 别照抄别的子系统的结论。

---

## §3 哪些结论是"推出来的"

⚠️⚠️ **本子系统官方文档为零（§1.4）⇒ 本套有异常高比例的推论。** 全部登记在这里。

| # | 结论 | 在哪篇 | 推导依据 | ⚠️ 可靠度 |
|---|---|---|---|---|
| 1 | `DEVTYPE=mfd_device` 用户态可见，可用作"是不是 mfd 建的"判据 | 07 §3 | `mfd-core.c:32-34` 的 `.name = "mfd_device"` + `:202` 赋值 + `drivers/base/core.c:2595` 的 `add_uevent_var(env, "DEVTYPE=%s", ...)` | ⚠️ **高** —— 三处代码闭环，无需假设 |
| 2 | syscon 的 debugfs 目录名是 `dummy-<节点名>@<地址>` | 07 §6.1 | `syscon.c:103`（`%pOFn@%pa`）+ `:112`（`dev = NULL`）+ `regmap-debugfs.c:550 :581-582 :584-587 :596 :606` 五步 | ⚠️ **高** —— 逐行走通，但 ⚠️ **未在真机上验证过** |
| 3 | `dev_get_regmap()` 永远拿不到 syscon | 07 §6.2 | `map->dev == NULL` + `regmap.c:663` 按 `dev` 匹配 | ⚠️ **高** |
| 4 | syscon 的 regmap 永不释放 | 07 §6.2 | `:112` 用非 devm 的 `regmap_init_mmio()` 且无宿主设备；`syscon_list` 无移除路径 | ⚠️ **高** |
| 5 | `level` 的语义是"在拆解过程中间插入父驱动动作" | 04 §3.2 | ⚠️⚠️ **纯反推** —— 零注释零文档，只有 madera 一个使用者 | ⚠️⚠️ **中** —— 语义解释是推断，机制行为（谁删谁不删）是代码事实 |
| 6 | `struct platform_device.mfd_cell` 是历史包袱 | 08 §3 | `mfd_get_cell()` 26 处 / 7 文件，⚠️ **arm64 上 0 处**；现代取值三条路都不经过它 | ⚠️ **中** —— "包袱"是价值判断 |
| 7 | 无锁链表"至今没炸"是因为异步 probe 罕见 | 08 §4.2 | `mfd_of_node_list` 无锁（`:24 :135 :319-323 :393`）+ `device_lock` 串行化 | ⚠️⚠️ **中** —— **没有实测过并发场景**，是机制层面的论证 |
| 8 | I2C 95 / platform 48 说明"SoC 内部块被 simple-mfd 吸收了" | 08 §5 | 两个 `grep -rl` 计数 + `of_default_bus_match_table`（`drivers/of/platform.c:24-31`） | ⚠️ **中** —— 相关性强，因果是解释 |
| 9 | "mfd 故障 80% 是静默的" | 07 §2、00 §9 | ⚠️⚠️ **这个 80% 是估计值，不是统计** —— 依据是"九条 goto 失败路径零日志 vs 一条默认可见 `pr_warn`" | ⚠️⚠️ **低（数字本身）/ 高（定性结论）** |
| 10 | `mfd_add_devices()` 必须放在 probe 最后一步 | 00 §7、05 §2.2 | `platform_device_add()` 同步触发子设备 probe（platform bus 语义） | ⚠️ **高** |
| 11 | `syscon_probe()` 在 DT 系统上从不执行 | 07 §6.4 | `:361-364` 只有 `id_table` 无 `of_match_table`；DT 实例化的设备名是 `<地址>.<节点名>` | ⚠️ **高** |

⚠️⚠️ **第 9 条要特别说明**：文档里的 "80%" 是**修辞而非度量**。
⇒ **可核实的事实版本是**：核心 471 行里 4 条日志语句、9 条失败路径 0 条日志、
1 条默认可见消息（`:242`）。**引用请用这组数字，不要引用 80%。**

---

## §4 没做的事（本套的空白）

⚠️⚠️ **以下全部未做，引用本套结论时必须知道**：

| # | 没做 | 后果 |
|---|---|---|
| 1 | ⚠️⚠️ **没有在真机或 QEMU 上跑过任何一条命令** | 07 §8 的所有 shell 命令是**按代码推导写的**，未经执行验证；`dummy-` 目录名、`DEVTYPE` 输出格式都属此列 |
| 2 | ⚠️ 没有编译过内核 | 无法确认 `MFD_CORE=n` 时哪些驱动会掉；`core.h` 在 `CONFIG_MFD_CORE=n` 下的行为未验证 |
| 3 | ⚠️⚠️ **235 个单芯片驱动只读了 4 个**（axp20x / madera / ocelot-core / rt4831） | 04 篇的"四种形态"**不能保证穷尽** —— 可能存在第五种用法 |
| 4 | ⚠️ `include/linux/mfd/` 那 229 个寄存器头一个没读 | 02 篇不涉及任何具体芯片的寄存器语义 |
| 5 | ⚠️ 没有跨版本对比（mainline / 6.6 / 6.12） | 只知道本树 HEAD 的样子；§1.1 的 backport 分析是**唯一的版本维度信息** |
| 6 | ⚠️ 没做性能测量 | "分家 N 个 cell 的开销"没有任何数据 |
| 7 | ⚠️⚠️ **`mfd_get_cell()` 的 26 个调用点只确认了"7 个文件、arm64 上 0 个"**，未逐个读 | 08 §3 的"历史包袱"判断建立在文件名与年代上，不是逐个读完的结论 |
| 8 | ⚠️ 无锁链表的并发缺口**没有构造复现** | 08 §4.2 / §6 是静态分析结论，⚠️ **不是已观测到的 bug** |
| 9 | ⚠️ `drivers/mfd/Kconfig` 2267 行未逐项读 | 只统计了行数、config 数与三个框架项，204 个驱动项的依赖关系没查 |

⚠️ **一条方法论限制**：**本套的行号全部来自 `grep -n` / `sed -n`，没有用编译期信息。**
⇒ 被 `#if` 关掉的代码与生效代码在行号上无区别 ⇒ ACPI 段（§2.7）就是靠**人工判断**排除的，
不是靠预处理输出。

---

## §5 与已完成子系统的对比口径

⚠️ 本套多处拿 mfd 与别的子系统对比，**口径必须一致才有意义**：

| 对比项 | mfd | pwm | dma-buf | ⚠️ 口径是否一致 |
|---|---|---|---|---|
| "核心行数" | 1092（4 个文件） | 1734（`core.c`+`sysfs.c`） | 4000+ | ⚠️ **一致**：都是"框架文件的 `wc -l`"，不含驱动 |
| "错误码种类" | 2 | 9 | — | ⚠️ **一致**：都是 `grep -oE '\-E[A-Z]+' 核心文件 \| sort \| uniq -c` |
| `-EINVAL` 计数 | **0** | 15 | 47 | ⚠️ **一致** |
| "官方文档" | ⚠️ **0 篇 driver-api** | 1 篇 179 行 | 1 篇 + 146 行设计债 | ⚠️ **一致** |
| "观测设施" | 全 0 | class+debugfs+2 tracepoint | debugfs+sysfs | ⚠️ **一致** |
| "使用面" | 134 文件 | 44 处 `pwmchip_add` | — | ⚠️⚠️ **不一致！** mfd 数的是**文件数**，pwm 数的是**调用点数** ⇒ **这两个数字不可直接比大小** |

⚠️⚠️ **最后一行是本套已知的一处口径不齐**，00 §3 与 08 §6 里
"198 个文件 > 134 个文件"这类比较是**内部一致的**（都是文件数），
**但不要把它们与 pwm 那套数字放在一起排名。**

---

## §6 代码问题清单

⚠️⚠️ **共 12 条。每条给位置、判断、以及"是缺陷还是设计选择"。**
⚠️ **树里没有任何一条被 `FIXME`/`TODO` 标注**（`mfd-core.c` + `core.h` 里 0 处）。

### §6.1 核心侧（7 条）

**1. ⚠️⚠️ `mfd_of_node_list` 全局链表无任何锁保护 —— 缺陷**

```c
/* drivers/mfd/mfd-core.c:24 */
static LIST_HEAD(mfd_of_node_list);
```
遍历 `:135`（`mfd_match_of_node_to_dev()`）· 失败路径删 `:319-323` ·
`mfd_remove_devices_fn()` 删 `:393`。
⚠️ **它是全局的而不是 per-parent 的** ⇒ 两个不同父设备并行 probe 时会撞同一条链。
⚠️ **靠"probe 串行"这个隐含假设活着，代码里零注释。**（03 §3、08 §4.2）

**2. ⚠️ `if (level && cell->level > *level)` 里的 `level &&` 是死判断 —— 无害但误导**

```c
/* drivers/mfd/mfd-core.c:387-388 */
if (level && cell->level > *level)
        return 0;
```
⚠️ 两个调用者（`mfd_remove_devices_late()` `:412` / `mfd_remove_devices()` `:420`）
**都传 `&level`，永远非 `NULL`** ⇒ 前半个条件恒真。
⇒ ⚠️ **读代码时会以为"`level == NULL` 表示删全部"，实际没有这条路。**

**3. ⚠️ `pm_runtime_no_callbacks()` 调在 `platform_device_add()` 之后 —— 可疑时序**

```
:304   ret = platform_device_add(pdev);        ⚠️ 此处子设备已开始 probe
...
:308   if (cell->pm_runtime_no_callbacks)
:309           pm_runtime_no_callbacks(&pdev->dev);
```
⚠️⚠️ **`platform_device_add()` 可能同步走完子驱动 probe** ⇒
**子驱动 probe 里若使用 runtime PM，看到的是"还没设置 no_callbacks"的状态。**
⚠️ **未构造复现**（§4 第 8 条同类），但时序上确实倒置。

**4. ⚠️⚠️ 子设备的 `of_node` 引用计数从未增加 —— 疑似缺陷**

⚠️ **`of_node_get()` 在 `mfd-core.c` 里零命中**，全文只有一个 `of_node_put()`（`:226`）。
⇒ 子设备拿到 `pdev->dev.of_node` 时**没有取引用**。
⚠️ **现实中不出问题的原因**：DT 节点在非 overlay 场景下永不释放。
⇒ ⚠️ **overlay 场景下这是一个真实的悬垂风险**，但本套未验证 overlay 路径。

**5. ⚠️ `mfd_cell.suspend` / `resume` 是死字段 —— 应删未删**

```c
/* include/linux/mfd/core.h:74-75 */
int (*suspend)(struct platform_device *dev);
int (*resume)(struct platform_device *dev);
```
⚠️⚠️ **`mfd-core.c` 里从不调用它们**（`grep 'cell->suspend'` 零命中）
⇒ **填了不报错、不生效。** 正确做法是子驱动的 `driver.pm`。（02 §2.3）

**6. ⚠️ `mfd_cell_enable()` 上方的引用计数注释与实现不符 —— 陈旧注释**

```c
/* include/linux/mfd/core.h:126-131 —— 注释原文大意 */
Refcounting happens automatically, with the cell's enable/disable
callbacks being called only when a device is first being enabled or
no other clients are making use of it.
```
⚠️⚠️ **`mfd_cell_enable()`（`mfd-core.c:36-48`）里没有任何计数器** ——
它只是"有 `enable` 就调，没有就 `dev_dbg`"。
⇒ ⚠️ **按注释写代码会重复触发硬件回调。** 只有 2 个文件用这个 API（近乎死 API）。

**7. ⚠️ `devm_mfd_add_devices()` 的 kerneldoc 承诺了它做不到的事 —— 错误文档**

```c
/* drivers/mfd/mfd-core.c:430-432 —— 大意 */
All child-devices of the MFD will automatically be removed when it
gets unbinded.
```
⚠️⚠️ **假的**：devres 回收函数只调 `mfd_remove_devices()`（`:422-425`）
⇒ **`level = MFD_DEP_LEVEL_HIGH` 的 cell 永远删不掉**（`:387-388`）。
⇒ ⚠️ **这是"文档说得比代码多"的一处**，01 §2.2 / 04 §3 已按代码为准写。

### §6.2 syscon 侧（4 条）

**8. ⚠️⚠️ `of_syscon_register_regmap()` 全树零调用者 —— backport 只搬了一半**

定义 `drivers/mfd/syscon.c:186`，kerneldoc `:175-184`，`EXPORT_SYMBOL_GPL` `:221`。
⚠️ 全树除自身定义外 **0 个调用点**。
⇒ ⚠️⚠️ **原因已在 §1.1 查明**：`166fd2aa73ec`（2024-06-21）把 API backport 进 6.1，
**它在 mainline 里的使用者没跟着进来** ⇒ **不是"没人要的接口"，是半截 backport。**
⇒ **6.1.y 上写新代码不要指望它，用 `syscon_regmap_lookup_by_*()`。**

**9. ⚠️⚠️ `syscon_probe()` 那条 platform 路径在 DT 系统上永不执行 —— 死代码**

`:325-359` 的函数体 · `:361-364` 只有 `id_table = { "syscon", }` 无 `of_match_table` ·
`:366-372` 的 `platform_driver` · `:374-378` 的 `postcore_initcall`。
⚠️ 而且**这条死路的行为比活路更弱**（07 §6.4 四行对照）：
⚠️⚠️ **`max_register` 硬编码 `resource_size(res) - 4`（`:345`），不看 `reg-io-width`**；
endian / `hwlocks` 全不支持。

**10. ⚠️ `syscon.c` 的两条 `pr_err` 都不带节点名 —— 可诊断性缺口**

`:96` `"Failed to retrieve valid hwlock: %d"` · `:115` `"regmap init failed"`。
⚠️⚠️ 一块板上十几个 syscon 时**看不出是哪个节点**。
⚠️ 而 `of_syscon_register()` 手里明明有 `np` ⇒ **加个 `%pOF` 是一行的事，没加。**（07 §6.3）

**11. ⚠️ endian 三属性在 `syscon.yaml` 里没有登记 —— 文档缺口**

`syscon.c:68-74` 解析 `big-endian` / `little-endian` / `native-endian`，
⚠️⚠️ **但 `Documentation/devicetree/bindings/mfd/syscon.yaml` 完全没提这三个**。
⇒ 它们只在老式 `Documentation/devicetree/bindings/common-properties.txt:12-23` 里。
⚠️ **能通过 dtbs_check 的原因是 `syscon.yaml:110` 写了 `additionalProperties: true`**
⇒ ⚠️ **这份 yaml 实质上不校验属性名。**（06 §4.4）

### §6.3 使用者侧（1 条）

**12. ⚠️⚠️ `level` 机制的唯一使用者自己就漏了清理 —— 真实泄漏**

```c
/* drivers/mfd/madera-core.c:761-762 */
err_devs:
        mfd_remove_devices(dev);
```
⚠️ madera 用 `MFD_DEP_LEVEL_HIGH` 标了 `madera-ldo1`，
而错误路径只调 `mfd_remove_devices()`（只删 `level <= NORMAL`）
⇒ ⚠️⚠️ **probe 中途失败时 `madera-ldo1` 这个 platform_device 留在系统里**
⇒ `-EPROBE_DEFER` 反复重试会**越攒越多**（07 §7 第 12 行）。
⇒ ⚠️ **正确写法是 `mfd_remove_devices()` 之后再调 `mfd_remove_devices_late()`**（04 §3.3）。

### §6.4 ⚠️ 一处查过之后确认"不是 bug"的代码

```c
/* drivers/mfd/syscon.c:112-113 */
regmap = regmap_init_mmio(NULL, base, &syscon_config);
kfree(syscon_config.name);
```
⚠️ 看起来像"把还在用的名字释放了"，⚠️⚠️ **实际安全** ——
`drivers/base/regmap/regmap.c:615` 用 `kstrdup_const()` 复制了 `config->name`，
regmap 不持有原指针。
⇒ **登记在此，免得后来的人重复怀疑。**（07 §6.2 末段）

---

## §7 复现命令

⚠️ **在 `/workspace/safety/linux-6.1.y` 下执行，全部只读。**

```sh
# —— §1 树的状态与 backport 判定 ——
git status --porcelain -- drivers/mfd include/linux/mfd     # ⇒ 空
git tag | wc -l                                            # ⚠️ ⇒ 0，所以不能用 v6.1..HEAD
for f in drivers/mfd/mfd-core.c include/linux/mfd/core.h \
         drivers/mfd/syscon.c include/linux/mfd/syscon.h; do
  echo "$f  total=$(git log --oneline -- $f | wc -l)  after=$(git log --oneline --since=2022-12-11 -- $f | wc -l)"
done
git log --since=2022-12-11 --format='%h %ad %s' --date=short \
    -- drivers/mfd/mfd-core.c include/linux/mfd/core.h \
       drivers/mfd/syscon.c include/linux/mfd/syscon.h

# —— §1.2 类型判定两道门 ——
grep -rn 'struct bus_type' drivers/mfd/                     # ⚠️ 只有 mcp-core.c:44
ls include/uapi/linux/ | grep -i mfd                        # ⇒ 空

# —— §1.4 官方文档 ——
ls Documentation/driver-api/ | grep -i mfd                   # ⇒ 空
wc -l Documentation/devicetree/bindings/mfd/mfd.txt \
      Documentation/devicetree/bindings/mfd/syscon.yaml

# —— §2 规模与导出 ——
find drivers/mfd -name '*.c' | wc -l
wc -l drivers/mfd/mfd-core.c include/linux/mfd/core.h \
      drivers/mfd/syscon.c include/linux/mfd/syscon.h
grep -nE '^EXPORT_SYMBOL' drivers/mfd/mfd-core.c drivers/mfd/syscon.c

# —— §2 错误码与设计债 ——
grep -oE '\-E[A-Z]+' drivers/mfd/mfd-core.c include/linux/mfd/core.h | sort | uniq -c
grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' drivers/mfd/mfd-core.c include/linux/mfd/core.h

# —— §2.4 使用面（文件数口径）——
for s in mfd_add_devices devm_mfd_add_devices mfd_get_cell mfd_cell_enable \
         syscon_regmap_lookup_by_phandle syscon_node_to_regmap \
         device_node_to_regmap of_syscon_register_regmap; do
  echo "$s: $(grep -rl "$s" --include='*.c' . | wc -l)"
done

# —— §2.6 DT 普查（arm64 only）——
grep -rl simple-mfd arch/arm64/boot/dts | wc -l              # ⇒ 62
grep -rl '"syscon"' arch/arm64/boot/dts | wc -l              # ⇒ 112

# —— §6 各条问题定位 ——
grep -n 'of_node_get\|of_node_put' drivers/mfd/mfd-core.c    # ⚠️ 只有一个 put
grep -n 'cell->suspend\|cell->resume' drivers/mfd/           # ⇒ 空（死字段）
grep -n 'debugfs\|DEVICE_ATTR\|ATTRIBUTE_GROUPS' \
        drivers/mfd/mfd-core.c drivers/mfd/syscon.c          # ⇒ 空
grep -nE 'dev_dbg|dev_err|dev_warn|pr_err|pr_warn|WARN' \
        drivers/mfd/mfd-core.c                               # ⚠️ ⇒ 只有 4 行
sed -n '55,60p;100,120p' drivers/mfd/syscon.c                # kfree/dev=NULL 那段
sed -n '610,620p;660,668p' drivers/base/regmap/regmap.c      # kstrdup_const / dev_get_regmap
sed -n '545,630p' drivers/base/regmap/regmap-debugfs.c       # dummy- 名字的五步
sed -n '2590,2600p' drivers/base/core.c                      # DEVTYPE= 那一行
```

---

## §8 各篇的引用面

| 篇 | 主要证据来源 | ⚠️ 本篇特有的风险 |
|---|---|---|
| 01-api | `mfd-core.c` 全文 + `core.h` 全文（**两个文件都通读了**，共 634 行） | 低 |
| 02-datastructures | `core.h:56-163` 逐字段 | 低；⚠️ `acpi_match` 按 §2.7 只登记 |
| 03-internals | `mfd-core.c:125-334` + `:374-404` + `syscon.c:40-230` | 低；⚠️ 并发论述属静态分析（§4 第 8 条） |
| 04-extensions | ⚠️ **只读了 4 个驱动** | ⚠️⚠️ **不保证形态穷尽**（§4 第 3 条） |
| 05-usage | 上述四驱动 + `axp20x-pek.c:233-243` | ⚠️ 建议性内容，⚠️ **未编译验证** |
| 06-devicetree | `mfd.txt` 51 行 + `syscon.yaml` 118 行**全文** + arm64 dts 普查 | 低（这是唯一有官方文字支撑的一篇） |
| 07-debug | ⚠️⚠️ **大量跨子系统推导**（regmap-debugfs / drivers/base/core.c） | ⚠️⚠️ **所有命令未实机执行**（§4 第 1 条） |
| 08-feynman | ⚠️⚠️ **全篇是推论** | ⚠️⚠️ **零官方依据**，见 §3 表 |
| 00-overview | ⚠️ **纯浓缩，不引入新推导** | 低 |

---

## §9 本套自查

**三个检查脚本**（`~/.claude/skills/kernel-subsystem-docs/scripts/`）：

```sh
S=~/.claude/skills/kernel-subsystem-docs/scripts; D=output/mfd
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh mfd $D .
```

| 项 | 结果 |
|---|---|
| `check_line_refs.py`（每条 `文件:行号` 是否落在文件实际行数内） | ⚠️ **每写完一篇都跑过，全部通过** |
| `check_xrefs.py`（篇间交叉引用是否指向存在的章节） | 见文末 |
| `check_doc_coverage.sh`（官方必读文档是否被引用） | ⚠️⚠️ **【必查】共 0 篇** ⇒ 空集为真（§1.4） |
| ⚠️ 实心五角星标记 | **0 处**（本套硬规则） |

⚠️⚠️ **`check_line_refs.py` 能查什么、不能查什么**：
它只验证"行号 ≤ 文件总行数"，⚠️ **不验证那一行的内容是不是文档说的东西**。
⇒ **行号偏移一两行的错误它查不出来。** 本套的对策是"先建 `行号:符号` 台账再写正文"，
但 ⚠️ **不能排除偏移。发现对不上时以源码为准。**

---

## §10 对照与结论

### §10.1 rule-29 对照套

⚠️ 规则 29 要求与 `output/qoder/<子系统>/` 下的既有文档集做对比。

```sh
ls output/qoder/
# clk  dma  gpio  irq  pinctrl  spi
```

⇒ ⚠️⚠️ **`output/qoder/mfd/` 不存在 ⇒ 本子系统无对照套，rule-29 记为「无」。**
（同 regulator / mailbox / reset / cpufreq / remoteproc / irq / cdev / of / timer /
virtio / regmap / phy / watchdog / tty / pwm / dma-buf。）

### §10.2 三条最该记住的取证结论

1. ⚠️⚠️ **本子系统的官方文档是零**（§1.4）⇒
   **本套里"为什么这样设计"的每一句都是推论**，§3 那张表就是它们的可靠度清单。
   ⇒ **引用 00/08 两篇的论述时，请一并引用 §3。**
2. ⚠️⚠️ **核心文件不是原生 6.1**（§1.1）⇒
   **`disabled` 子节点被静默跳过这个最常见的行为，是 `ee6b91411d3f` 带来的 backport**
   ⇒ 换一棵 6.1.x 树之前先确认这个提交在不在。
3. ⚠️⚠️ **本套所有 shell 命令都没有实机跑过**（§4 第 1 条）⇒
   07 §8 的命令按代码推导可信，但 ⚠️ **输出格式（尤其 `dummy-` 目录名）
   第一次用时请自己核对一遍。**

### §10.3 一句话交代

⚠️ **mfd 是本套见过核心占比最小（0.56%）、观测面最空（全 0）、
官方文档最少（0 篇 driver-api）的子系统**，
而它同时被 **134 个文件**（分家侧）与 **198 个文件**（syscon 侧）依赖。
⇒ ⚠️⚠️ **这个反差本身就是本套最值得记住的一条结论**：
**依赖面广不等于文档全，代码少不等于坑少。**

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 三分钟看完全貌 | 00-overview.md |
| 六个接口怎么调 | 01-api.md |
| `mfd_cell` 每个字段 | 02-datastructures.md |
| 471 行逐段 | 03-internals.md |
| 真实驱动的四种形态 | 04-extensions.md |
| 我要写驱动 | 05-usage.md |
| DT 怎么写 | 06-devicetree.md |
| 出问题了怎么查 | 07-debug.md |
| 为什么它长这样 | 08-feynman.md |
