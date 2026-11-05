---
title: 取证附录
---

# drivers/base 文档集取证附录

这一篇不讲 `drivers/base` 本身，讲**前九篇的每条论断是怎么来的、哪些没被验证过**。读者据此决定哪些结论可以直接用，哪些要自己再确认一遍。

篇内导航：

| 节 | 内容 |
|---|---|
| §1 | 基线、类型判定、官方文档登记 |
| §2 | 阅读范围与证据等级（含 arm64 裁剪清单） |
| §3 | 可重跑的数字 |
| §4 | 交叉验证实例（推翻过初始假设的） |
| §5 | 明确未验证项（四层） |
| §6 | 逐篇取证表 |
| §7 | 批量自检的执行情况 |
| §8 | 下次复核该先看哪里 |

---

## §1 基线与范围

### §1.1 基线

| 项 | 值 | 重跑命令 |
|---|---|---|
| 提交 | `f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe` | `git log -1 --format='%H %s'` |
| 版本 | Linux 6.1.158 | 同上 |
| 子系统范围内的本地改动 | 0 个文件 | `git status --porcelain -- drivers/base include/linux/device.h include/linux/device/ \| wc -l` |

第三行是本套文档能成立的前提：**这棵树整体打了大量补丁，但 `drivers/base` 与 `include/linux/device*` 一个字节都没被改**。所以本套文档的所有结论对上游 6.1.158 同样有效，不需要"本树特有"的免责声明。

⚠️ 这个结论只覆盖上表列出的三个路径。`drivers/base/power/`、`drivers/base/regmap/`、`drivers/base/firmware_loader/` 也在 `drivers/base` 前缀下，被这条命令一起覆盖了；但 `drivers/of/`（本套多处引用）**没有**单独查过是否被改。见 §5.4。

### §1.2 子系统类型判定：两道门的实际输出

| 门 | 命令 | 输出 | 判读 |
|---|---|---|---|
| 门 1：有没有自己的 `bus_type` | `grep -rn 'struct bus_type [a-z_]* = {' drivers/base/*.c \| wc -l` | 8 | 有 |
| 门 2：协议 ABI 在不在 uapi 里 | `ls include/uapi/linux/ \| grep -c '^base'` | 0 | 不在 |

门 1 有、门 2 空 → 按 skill 的判定表本该落到**硬件总线型**。但实际写下来发现两道门都不足以描述它，因为**它的八条总线没有一条有电气层**（清单见 `00-overview.md` §2.6）。于是本套采用第三种定性：**元框架型**——它是"总线"这个概念的**提供者**，同时自己也用这套概念造了八条软件总线。

这个定性影响了三处模板取舍，登记在此以免被当成随意偏离：

- `02-datastructures.md` 的"本子系统总线 ↔ 父总线"关系图改成了 **"核心抽象 ↔ 146 条总线"** 的关系图——因为 `drivers/base` 没有父总线，它就是那一层。
- `06-devicetree.md` 的分诊表里，`drivers/base/*.c` 自身**不消费任何 DT 属性字符串**（`grep -c '"[a-z-]*"' ` 无 DT 属性命中；实际消费方全在 `drivers/of/`）。这一篇因此改成"DT 属性 → 谁替 `drivers/base` 读"。
- `04-extensions.md` 的"子框架"不是 `drivers/base` 的扩展点，而是**住在同一个目录里但互不调用的 12 套独立机制**。这一点在该篇 §0 已声明。

### §1.3 官方文档登记（三等证据）

`check_doc_coverage.sh base output/base .` 报"没找到 `Documentation/base/`、`driver-api/base*` 或 `bindings/base/`"，退出码 1。这是**真阴性**：官方文档确实不叫 `base`，它叫 `driver-model`。手工替代命令：

```bash
find Documentation -ipath "*driver-model*" -type f | sort
```

读过的文件与采纳情况：

| 文件 | 行数 | 读了 | 采纳到 | 时效问题 |
|---|---|---|---|---|
| `Documentation/driver-api/driver-model/overview.rst` | 124 | 全篇 | `00` §2.1、§2.3 | ⚠️ 有，见下 |
| `Documentation/driver-api/driver-model/binding.rst` | 98 | 全篇 | `00` §2.3、`03` §A6 | 无 |
| `Documentation/driver-api/driver-model/bus.rst` | 146 | 全篇 | `01` §2、`03` §A2 | 无 |
| `Documentation/driver-api/driver-model/device.rst` | 120 | 全篇 | `02` §2 | 无 |
| `Documentation/driver-api/driver-model/driver.rst` | 286 | 全篇 | `01` §3、`03` §A7 | ⚠️ 有，见下 |
| `Documentation/driver-api/driver-model/devres.rst` | 448 | 全篇 | `01` §7、`03` §B2 | 无（已核，见 §4.3） |
| `Documentation/driver-api/driver-model/platform.rst` | 246 | 全篇 | `01` §8、`03` §A4 | 无 |
| `Documentation/driver-api/driver-model/design-patterns.rst` | 116 | 全篇 | `08` §3 | 无 |
| `Documentation/driver-api/driver-model/porting.rst` | 448 | 只读小节标题 | 未采纳 | 整篇是 2.4→2.6 迁移指南，与 6.1 无关 |
| `Documentation/driver-api/device_link.rst` | 320 | 全篇 | `03` §B3、`07` §5.3 | 无 |
| `Documentation/driver-api/component.rst` | 19 | 全篇 | `04` §1 | 无（只有一行导语 + kerneldoc 指令） |
| `Documentation/driver-api/infrastructure.rst` | 79 | 全篇 | `01` §0.4 | 无（纯 kerneldoc 汇编指令） |
| `Documentation/devicetree/usage-model.rst` | 420 | 全篇 | `06` §2、§5 | ⚠️ 有，见下 |
| `Documentation/s390/driver-model.rst` | — | 未读 | — | s390 专属，arm64 无关 |

**三条时效问题**，都在正文对应位置也标注过：

1. `overview.rst:29-35` 把 ACPI 说成驱动模型的目标形态（"the model dictated by Intel and Microsoft (namely ACPI)"）。这是文档落款 2002 年的语境（`overview.rst:7-8`），对 arm64 完全不适用。已在 `00-overview.md` §2.7 标注。
2. `driver.rst:151-152` 说 `probe()` 被调用时"with the bus's rwsem locked"。**6.1 里 `drivers/base` 整个目录一个 `rwsem` 都没有**——`grep -rn 'rwsem' drivers/base/*.c drivers/base/*.h` 输出为空。`struct subsys_private`（`drivers/base/base.h`）持有的是 `struct mutex mutex`，而 `really_probe()` 路径上的设备侧锁是 `device_lock()`（即 `dev->mutex`）。本套没有把 rwsem 那句话写进任何一篇。⚠️ 但要注意本条只证到"不是 rwsem"，**没有**逐条追完 probe 路径上到底持有哪几把锁——见 §5.3。
3. `Documentation/devicetree/usage-model.rst:418` 说 platform 层下钻的判据是 compatible 为 `"arm,amba-primecell"`，而 6.1 代码里查的是 `"arm,primecell"`（`drivers/of/platform.c:380`）。全树 `arm,amba-primecell` 只有三处命中，且**没有一处是 C 代码**：`arch/arm/boot/dts/nspire.dtsi:164`、`Documentation/devicetree/usage-model.rst:418`、以及它的中文翻译。也就是说文档写的那个字符串在 6.1 里不被任何代码消费。已在 `06-devicetree.md` §5 标注。

   同篇还有一处 arm64 不适用：`Documentation/devicetree/usage-model.rst` 教的 `.init_machine()` 回调是 arm32 的 `MACHINE_START` 机制，arm64 上不存在（arm64 没有 `machine_desc`）。

---

## §2 阅读范围与证据等级

### §2.1 一等证据：逐行读完的文件

判据是**从第一行读到最后一行，每个函数都打开过**。

| 文件 | 行数 |
|---|---|
| `drivers/base/dd.c` | 1352 |
| `drivers/base/devres.c` | 1239 |
| `drivers/base/bus.c` | 1184 |
| `drivers/base/driver.c` | 303 |
| `drivers/base/platform.c` | 1529 |
| `drivers/base/base.h` | 207 |
| `include/linux/device.h` | 1122 |
| `include/linux/device/bus.h` | 292 |
| `include/linux/device/driver.h` | 296 |
| `include/linux/device/class.h` | 280 |
| `include/linux/dev_printk.h` | 278 |
| `drivers/base/trace.h` | 56 |
| `drivers/base/trace.c` | 10 |
| `drivers/base/init.c` | 42 |
| `drivers/base/Makefile` | 36 |
| `drivers/base/Kconfig` | 233 |

行数重跑：`wc -l <上表全部路径>`。

`drivers/base/core.c`（5190 行）**不在这一档**——见 §2.2。

### §2.2 二等证据：读了指定区间的文件

判据是**只打开了下表列出的行区间**，区间外只看过函数名。引用这些文件的行号时，落在区间外的都要当成待复核。

| 文件 | 详读区间 | 覆盖了什么 |
|---|---|---|
| `drivers/base/core.c` | 40-60、370-380、470-560、600-720、1100-1170、1290-1320、1700-1800、2040-2070、2150-2180、2680-2760、3150-3200、3570-3760、3790-3940、4800-4880、4940-5060 | devlink 属性与 class、依赖检查、fw_devlink 三个入口、sysfs 可写入口、`device_initialize`/`add`/`del`/`unregister`、关机、`dev_printk` 与 `dev_err_probe` |
| `drivers/base/component.c` | 70-140、480-540 | debugfs 与两个调用点 |
| `drivers/base/devcoredump.c` | 15-30、110-260、370-395 | 超时、sysfs 属性、两个 `dev_coredump*` 入口 |
| `drivers/base/property.c` | 只读了 `EXPORT_SYMBOL` 清单与前 60 行 | `01` §9 只讲契约，不讲实现 |
| `drivers/base/class.c` | 只读了 `EXPORT_SYMBOL` 清单 | `02` §5.1 的字段说明来自头文件而非本文件 |
| `drivers/base/swnode.c` | 未详读 | `04` 只登记它存在与规模 |
| `drivers/base/cpu.c` / `memory.c` / `node.c` / `container.c` / `soc.c` / `auxiliary.c` / `isa.c` | 只读了各自 `struct bus_type` 定义那几行 | `00` §2.6 的八条总线表 |
| `drivers/of/property.c` | 1330-1390 | `of_supplier_bindings[]` |
| `drivers/of/platform.c` | 370-400 | `arm,primecell` 下钻判据 |
| `include/linux/platform_device.h` | 只读了 `struct platform_device` 与 `platform_driver` 定义 | `02` §7.1 |
| `include/linux/fwnode.h` | 只读了 `struct fwnode_handle` 与 `fwnode_operations` | `02` §6.4 |
| `include/linux/module.h` | 405-420、670-690、805-820 | 异步探测的模块参数 |
| `kernel/module/main.c` | 2440-2510、2680-2695 | 同上的消费点 |
| `Documentation/admin-guide/kernel-parameters.txt` | 970-990、1120-1130、1570-1600、2040-2055、6175-6185 | 五个命令行参数的官方说明 |

### §2.3 三等证据：官方文档

见 §1.3 的表。三等证据的用法限制：**只用来说明"设计意图"和"为什么"，不用来断言 6.1 的行为**。凡是文档与代码冲突的地方，正文一律以代码为准并显式指出（三处冲突见 §1.3）。

### §2.4 只查过符号存在性，没读实现

以下符号在正文里出现过，但只做到了"确认它在 6.1 存在、可被外部驱动使用"这一步（判据：`EXPORT_SYMBOL*` 且 `include/linux/` 下有声明），没有读它的实现：

`device_property_read_u8/u16/u32/u64/string`、`fwnode_property_*` 全族、`device_get_child_node_count`、`software_node_register*`、`platform_msi_domain_alloc_irqs` 一族、`devm_*` 里除 `devm_kzalloc`/`devm_kfree`/`devm_add_action` 之外的全部（`devres.rst` 提到 135 个，见 §4.3）。

### §2.5 索引工具的使用边界

cbm 索引只用于**找候选和看调用关系**，没有一条论断的最终依据来自它。所有行号与计数都用 `grep -n` / `sed -n` / `wc -l` 在真实树上复核过。原因是 `drivers/base` 恰好踩在索引的两大盲区上：

- **宏生成的符号**：`define_dev_printk_level`（`drivers/base/core.c:4979`）一行展开出 7 个导出函数，索引里看不到 `_dev_err` 这类名字的定义位置。
- **函数指针间接调用**：整个 `bus_type` / `class` / `fwnode_operations` 机制全是间接调用，索引给不出 `bus->probe()` 到 `platform_probe()` 的边。

### §2.6 没有运行过任何东西

本套文档**没有编译过内核、没有启动过系统、没有跑过任何命令行参数或 debugfs 操作**。所有"输出长什么样"的描述都来自源码里的格式字符串，不是实际捕获。这条限制的具体后果见 §5.1。

### §2.7 arm64-only 裁掉了什么

| 裁掉的 | 依据 | 后果 |
|---|---|---|
| `drivers/base/physical_location.c` / `.h` | `obj-$(CONFIG_ACPI)`（`drivers/base/Makefile:28`） | 该文件在 arm64 常见配置下不编译，本套完全不提它的实现 |
| 整条 ACPI 设备发现路径 | `platform_match()` 里的 `acpi_driver_match_device()`（`drivers/base/platform.c:1345-1346`）在无 ACPI 时是空实现 | `06-devicetree.md` §0 的分诊表只剩 DT 与代码注册两行 |
| `container` 总线 | ACPI 容器热插拔专用（`drivers/base/container.c:27`） | `00` §2.6 表里标注，不展开 |
| `isa` 总线 | `CONFIG_ISA_BUS_API`（`drivers/base/Makefile:13`），arm64 无此配置 | 同上 |
| `drivers/base/arch_numa.c` | arm64 上编译，但 NUMA 不在本套范围 | 只在 `07` §11.4 的命令行参数表里出现一次（`numa=`） |
| `drivers/base/arch_topology.c` | 同上 | 未覆盖 |
| x86 / powerpc / arm32 的架构特化 | —— | 本套不做多架构对照 |
| `drivers/base/power/`、`regmap/`、`firmware_loader/` | 范围决定，不是架构决定 | 只在 `00` §7.2 说明分工，实现完全未覆盖 |

⚠️ 一处容易被当成遗漏的：`drivers/base/Makefile:7` 里 `attribute_container.o transport_class.o` 是**无条件 `obj-y`**，不带任何 `CONFIG_`。也就是说这两个文件在 arm64 上照样编译进内核，尽管全树只有 SCSI 在用。`04-extensions.md` §8 讲了它们，但标注为"arm64 上不会碰到"——这里的"不会碰到"指的是**不会有人调用**，不是不编译。

---

## §3 可重跑的数字

全部数字连命令列在下面。定稿前逐格跑过一遍，输出与表内一致。命令都以 `cd /workspace/safety/linux-6.1.y` 为前提。

| # | 数字 | 值 | 命令 |
|---|---|---|---|
| 1 | `drivers/base/*.c` 文件数 | 35 | `ls drivers/base/*.c \| wc -l` |
| 2 | `drivers/base/*.c` 总行数 | 23713 | `wc -l drivers/base/*.c \| tail -1` |
| 3 | 行首 `EXPORT_SYMBOL` 语句数 | 312 | `grep -h '^EXPORT_SYMBOL' drivers/base/*.c \| wc -l` |
| 4 | 实际导出符号数 | 318 | 312 − 1（宏体内那条）+ 7（宏的展开数） |
| 5 | 本目录 `bus_type` 实例数 | 8 | `grep -rn 'struct bus_type [a-z_]* = {' drivers/base/*.c \| wc -l` |
| 6 | 全树 `bus_type` 实例数 | 146 | `grep -rn 'struct bus_type [a-z_]* = {' --include=*.c . \| wc -l` |
| 7 | 用 platform 驱动接口的 .c 文件数 | 4019 | `grep -rlw 'platform_driver_register\|module_platform_driver' --include=*.c . \| wc -l` |
| 8 | `of_supplier_bindings[]` 条目数 | 34 | `awk 'NR>=1341 && NR<=1376' drivers/of/property.c \| grep -c 'parse_prop = '` |
| 9 | 本目录命令行/模块参数钩子数 | 8 | `grep -rn '^__setup(\|^early_param(\|^module_param' drivers/base/*.c \| wc -l` |
| 10 | 本目录 tracepoint 数 | 1 | `grep -c 'TRACE_EVENT\|DEFINE_EVENT' drivers/base/trace.h` |
| 11 | `include/uapi/linux/base*` 头文件数 | 0 | `ls include/uapi/linux/ \| grep -c '^base'` |
| 12 | `drivers/base` 里的 `rwsem` 出现次数 | 0 | `grep -rn 'rwsem' drivers/base/*.c drivers/base/*.h \| wc -l` |
| 13 | `CONFIG_COMPONENT` 出现次数 | 0 | `grep -rn 'CONFIG_COMPONENT\b' --include=Kconfig --include=Makefile . \| wc -l` |
| 14 | `drivers/base/map.c` 的导出数 | 0 | `grep -c 'EXPORT_SYMBOL' drivers/base/map.c` |
| 15 | `kobj_lookup()` 的调用文件数 | 2 | `grep -rlw 'kobj_lookup' --include=*.c .` → `drivers/base/map.c`、`fs/char_dev.c` |
| 16 | `devres.rst` 提到的 `devm_*` 符号数 | 135 | 见 §4.3 的脚本 |
| 17 | `devres.rst` 提到但 6.1 树里不存在的符号数 | 0 | 同上 |
| 18 | 逐文件 `dev_dbg(` 计数（八个核心文件） | 见 `07-debug.md` §0.1 | `grep -c 'dev_dbg(' <逐个文件>` |

三个数字必须连口径一起记，否则会被读者用不同的 grep 推翻：

- **#3 用的是 `^EXPORT_SYMBOL` 而不是 `EXPORT_SYMBOL`**。不加锚点得到 313，多出来的那一条是 `drivers/base/auxiliary.c:48` 的**注释**（"Consider EXPORT_SYMBOL_NS() to convey…"）。这是本套遇到的第三个"锚点/子串"陷阱，另两个见 §4.1 和 §4.2。
- **#4 是推算不是 grep 得来的**。312 条语句里有一条（`drivers/base/core.c:4994`）住在 `define_dev_printk_level` 宏体内，它本身不导出任何东西，而宏在 `:4996-5002` 被展开 7 次。所以 312 − 1 + 7 = 318。
- **#7 数的是文件数不是驱动数**。一个 .c 文件里可以有多个 `platform_driver`，所以真实的 platform 驱动数比 4019 大。这个口径是刻意选的：文件数能一条命令重跑，驱动数不能。

---

## §4 交叉验证实例

这一节只收**推翻过初始假设**的例子。它们是判断本套可信度的最好材料——没有它们，读者无法区分"验证过"和"写得像验证过"。

### §4.1 `default_attrs` 里含着 `fault_attr`

起初写 `07-debug.md` §8「故障注入」时，`grep -rn 'fault_attr' drivers/base/` 有 6 条命中，看起来 `drivers/base` 有故障注入设施。逐条打开才发现**全部 6 条都是 `default_attrs` 这个词里的子串**——`default_attrs` 尾部恰好是 `fault_attr` 加一个 `s` 的错位匹配。

正确的判据是加词界：`grep -rnw 'fault_attr\|should_fail\|DECLARE_FAULT_ATTR' drivers/base/` → 空。结论改成"`drivers/base` 完全没有故障注入设施"，并在 §8 列了三个替代手段。

**这条错误 `check_line_refs.py` 和 `check_xrefs.py` 都抓不到**——行号是真的，文件是真的，只有语义是假的。

### §4.2 `numa_parse_early_param` 里含着 `early_param(`

写 `07-debug.md` §11.3 的"本目录有几个命令行参数钩子"那一格时，`grep -c 'early_param('` 逐文件求和得到 9，而手工数出来的钩子只有 8 个。差额来自 `drivers/base/arch_numa.c:27`——那是一个**函数定义**：

```c
static __init int numa_parse_early_param(char *opt)
```

函数名尾部逐字包含 `early_param(`。加行首锚点（`^early_param(`）之后回到 8。这促成了 `07-debug.md` §11.4 那张"全部八个钩子"的表——**表格能暴露差额，流水的正文不能**。

### §4.3 `devres.rst` 的托管接口清单没有过期

`devres.rst`（448 行）列了一大批 `devm_*` 接口，三等证据有过期风险，所以逐个查了一遍：

```bash
for s in $(grep -oE '\bdevm_[a-z0-9_]+' Documentation/driver-api/driver-model/devres.rst | sort -u); do
  grep -rqw "$s" --include=*.h --include=*.c . || echo "MISSING: $s"
done
```

135 个符号，**一个都没缺**。这是本套唯一一处"预期文档过期、结果没过期"的检查——记下来是因为它同样有信息量：`devres.rst` 是 `driver-model/` 下维护得最好的一篇，而同目录的 `porting.rst`（448 行）整篇讲 2.4→2.6 迁移，已经完全无用。**同一个目录下文档的新鲜度可以差二十年**，不能按目录整体判断。

### §4.4 `probe` 的错误码是正数

写 `07-debug.md` §7 时的初始假设是"`really_probe()` 返回负的 errno"。读到 `drivers/base/dd.c:650-654` 才发现有一句 `ret = -ret`——**probe 的错误码被取反成正数**。旁证有两条：`dd.c:800-802` 的 kerneldoc 逐字写着 "a positive (inverted) error code"，以及 `dd.c:816` 必须同时测 `-EPROBE_DEFER` 和 `EPROBE_DEFER` 两个值。

这条发现改写了 §7 整节的结构：从"列出错误码"改成"列出错误码被改写的四个位置"，因为**读者在 dmesg 里看到的数字和驱动 `return` 的数字未必同号**。

### §4.5 `devres_add()` 确实是返回值黑洞

`07-debug.md` §6.4 初稿写的是"`devres_add()` 有返回值、不构成黑洞"。核对 `include/linux/device.h:182` 发现它是 `void devres_add(struct device *dev, void *res)`——**黑洞存在**。

但改正之后又发现黑洞是有界的，所以最终版写的是三句话而不是一句：会失败的那一步是 `devres_alloc()`（宏在 `include/linux/device.h:172-173`，真身 `__devres_alloc_node()` 在 `:170-171`），它返回指针，调用方能检查；`devres_add()` 之后不会再失败；而 `devres_release_all()` 虽然返回 `int`（`drivers/base/base.h:157`），它是核心内部函数，返回值在 `drivers/base/dd.c:530` 被直接丢掉。

**"有没有黑洞"这个问题问得太粗**，正确的问法是"失败能在哪一步被观察到"。

### §4.6 `device_set_deferred_probe_reason()` 长得像公开 API

它有完整 kerneldoc（`drivers/base/dd.c:223-226`）、名字规整、非 `static`。按"有 kerneldoc"判断会把它当成驱动可用的接口写进 `01-api.md`。

实际过两道门都不通：**没有 `EXPORT_SYMBOL`**，声明在 `drivers/base/base.h:142`——那是**核心私有头**，不在 `include/linux/` 下。唯一调用点是 `drivers/base/core.c:5050` 里的 `dev_err_probe()`。

所以它没进 `01-api.md`。这正是 skill 规则 20 那条"写满 kerneldoc 却不导出的内部函数比没注释的更危险"的实例。

### §4.7 `devlink` 这个名字在内核里有两个主人

`drivers/base/core.c:609` 起注册了一个 `class`，`.name = "devlink"`（`:610`）——设备链接的 sysfs 视图。而网络子系统也有一个叫 devlink 的东西（`include/trace/events/devlink.h`、`net/devlink/`），是完全无关的网络设备管理接口。

两者都在 sysfs 和 tracepoint 命名空间里出现。`07-debug.md` §5.3 因此显式加了一句区分——**否则读者搜到网络 devlink 的文档会得到完全错误的结论**。

### §4.8 `block/genhd.c` 已经不用 `kobj_map`

`drivers/base/map.c` 提供 `kobj_map()` / `kobj_lookup()`，历史上有两个使用者：字符设备和块设备。6.1 里 `grep -c 'kobj_map\|kobj_lookup' block/genhd.c` → **0**，块层已经改用别的机制。唯一剩下的使用者是 `fs/char_dev.c`。

这解释了 `map.c` 为什么 `EXPORT_SYMBOL` 数是 0：它只服务一个树内使用者，不对外。`04-extensions.md` 把它标成"事实上的单用户机制"。

---

## §5 明确未验证项

四层，按"离可信最远"排序。

### §5.1 第一层：未编译、未运行

**这是最大的一条限制。** 本套没有编译过内核、没有启动过任何系统。具体后果：

| 正文里的什么 | 实际状态 |
|---|---|
| `07-debug.md` 里全部示例命令 | 命令语法照源码写，**输出没有一条是实际捕获的**。该篇 §3.6 已声明"输出不许编"，做法是只给格式字符串不给具体值 |
| `05-usage.md` 全部代码骨架 | **未编译**。该篇篇首已声明。头文件、函数名、参数个数逐个 grep 过，但没有过编译器 |
| `06-devicetree.md` 的 DTS 片段 | 语法未经 `dtc` 校验。属性名和消费代码逐条核过，节点结构没有 |
| 所有 Kconfig 组合的行为 | `07-debug.md` §1.2 那张 `CONFIG_DEBUG_DRIVER` 真值表是**读 Makefile 和 `#ifdef` 推出来的**，不是编译验证的 |
| debugfs / sysfs 文件的实际可读写性 | 只核了创建它们的代码（`debugfs_create_file` 的 mode 参数、`DEVICE_ATTR_RW` 宏），没有实际读写过 |
| dmesg 里日志出现的先后次序 | 由代码顺序推断。异步探测下的真实交错**没有观察过**（这一点 `08-feynman.md` §9 也说明了） |

### §5.2 第二层：只读了符号表，没读实现

清单在 §2.4。这一层的风险形态是：**接口存在、名字对、参数个数对，但语义可能与正文描述有偏差**。最需要留意的两处：

- `drivers/base/property.c`（1421 行）只读了导出清单和前 60 行。`01-api.md` §9 讲的 `device_property_*` 一族**只有契约级描述**——"读 u32 属性"这类。它们在 fwnode 层的具体分发路径、数组读取的边界处理、字符串生命周期，本套都没有覆盖。
- `drivers/base/swnode.c`（1177 行）完全未详读。软件节点（software node）在 `04-extensions.md` 里只登记了"它存在、它是第三种 fwnode 实现、arm64 上主要被 USB Type-C 一类驱动用"。任何关于它行为的具体问题，本套答不了。

### §5.3 第三层：推断而非观察

以下结论是**从代码结构推出来的**，没有直接证据（既没有运行观察，也没有一句源码注释明说）：

| 结论 | 在哪一篇 | 推断链 | 缺什么 |
|---|---|---|---|
| 关机逆序正确是因为"父设备必然比子设备先注册" | `00` §2.2 | `device_add()` 要求 `parent` 已存在 → 父先入 `devices_kset` → 逆序遍历先到子 | 没有逐条验证"所有创建路径都保证父先注册"。热插拔路径可能有例外 |
| probe 路径上持有哪几把锁 | `03` §B1 | 从 `device_lock()` / `bus->p->mutex` 的取放位置读出 | 只证到"不是 rwsem"（§1.3 第 2 条）。**没有**逐条追完全部嵌套加锁点，也没有对照 lockdep |
| 延迟探测最坏 O(n²) | `00` §2.5、`08` §4 | 每次驱动注册重试整条链表，链表最长 n | 没有构造实例，也没有查内核里有没有做过复杂度优化 |
| `devm_*` 逆序释放"自动满足依赖"因为申请顺序编码了依赖 | `08` §5 | `list_for_each_entry_safe_reverse`（`drivers/base/devres.c:497`）+ 常识 | 这是一条**设计意图的推断**。源码注释没有明说，`devres.rst` 也没有 |
| `fw_devlink` 是 arm64 启动顺序可预测的"主要原因" | `00` §2.5 | 机制上合理 | "主要"这个量词**没有依据**。没有对比过关掉 `fw_devlink` 前后的 defer 次数 |
| `platform` 是最忙的一条总线 | `00` §2.6、`08` §7 | 4019 个文件 vs 146 条总线 | 文件数不等于设备数。真实运行时的设备数分布**没有观察过** |
| `class` 的 `dev_release()` 存在是因为 class 设备常无容器结构体 | `08` §2 | `device_create()` 的实现形状 | 这是对**历史原因**的推测，没有找到 commit 或注释支持 |

⚠️ 上表第 5 行和第 6 行是本套里两处**带量词的推断**（"主要原因"、"最忙"）。它们在正文里没有加限定词，读者应当把它们当作合理猜测而非结论。

### §5.4 第四层：版本时效与范围外依赖

| 项 | 状态 |
|---|---|
| `drivers/of/` 是否被本树补丁改过 | **未查**。本套在 `06-devicetree.md` 和 `08` §6 多处引用 `drivers/of/property.c`、`drivers/of/platform.c` 的行号。§1.1 的 `git status` 命令**不覆盖** `drivers/of/`。重跑命令：`git status --porcelain -- drivers/of/` |
| `include/linux/module.h`、`kernel/module/main.c` 是否被改过 | **未查**。同上 |
| 三条上游文档时效问题 | 已查，见 §1.3。**其余 11 篇文档没有逐条与代码对照**，只是读的时候没撞见冲突——这不等于没有冲突 |
| 6.1 之后的上游变化 | 完全未跟踪。已知会影响本套的方向：`bus_type` 的 `const` 化、`class_register()` 的签名变更、`platform_driver->remove` 改成 `remove_new`。这些在 6.1 里都**还没有发生**，但读者如果拿本套去看更新的树会立刻撞上 |
| `devcoredump` 的 `DEVCD_TIMEOUT` 默认值 | 读的是 `drivers/base/devcoredump.c:19` 的宏定义。**没有查**它有没有被别的 Kconfig 或命令行覆盖 |

---

## §6 逐篇取证表

每篇列"最容易被质疑的三到五条论断"及其出处，以及本篇自审改掉的错。**"自审修正"一列是这张表的重点**——它是判断某篇可信度的直接材料，改动多的篇不代表更差，代表被查得更细。

### §6.1 `01-api.md`

| 论断 | 出处 |
|---|---|
| 本目录导出 318 个符号 | §3 表 #3、#4 |
| 本目录有 8 条总线 | §3 表 #5，逐条给了定义行号 |
| `device_set_deferred_probe_reason()` 不是公开 API | §4.6 |
| 错误码语义表（§13）逐条点名函数 | 每条都有 `文件:行号` |

自审修正：把 §2.4 的总线清单从 7 条补到 8 条（漏了 `isa`，因为它带 `CONFIG_ISA_BUS_API` 条件、grep 时被过滤掉了）；删掉逐 API 的行号（skill 规则 10），改成每节开头一行"头文件 / 实现"。

### §6.2 `02-datastructures.md`

| 论断 | 出处 |
|---|---|
| `dev->p` / `drv->p` / `bus->p` 是三个不同类型 | §1.0.3，三个 `struct` 定义都在 `drivers/base/base.h` |
| `bus` 和 `class` 共用 `struct subsys_private` | §1.0.2 |
| `struct devres_group` 在链表里占两个节点 | §1.0.6，`drivers/base/devres.c:38-43` 的 `node[2]` |
| `platform_device` 内嵌而非继承 `device` | §1.0.5 |

自审修正：`struct device` 的字段行号整体偏移过一次（初稿按 `include/linux/device.h` 的旧记忆写，实测 `struct device` 起于 `:557`）；§1.0 从 4 组关系补到 6 组（补了"两种依赖"和"三个 `p`"）。

### §6.3 `03-internals.md`

| 论断 | 出处 |
|---|---|
| Part A 的时间线次序 | 每个章节标题带完整行号区间，次序用"谁调用谁"验证 |
| `really_probe()` 的 12 个阶段 | `drivers/base/dd.c:584-710` 逐段 |
| `__device_release_driver()` 的放锁重查循环 | `drivers/base/dd.c:1234-1249` |
| §C1 的设计债清单 | 逐条引 `REVISIT` / `FIXME` / `TODO` 注释原文 |

自审修正：`device_add()` 的行号区间从 `:3487` 改成 `:3578`（差 91 行，初稿凭记忆）；把 `bus_probe_device()` 的调用点从 `:3708` 改成 `:3697`。

### §6.4 `04-extensions.md`

| 论断 | 出处 |
|---|---|
| 12 套机制互不调用 | §0.1，逐对查过交叉调用 |
| `CONFIG_COMPONENT` 不存在 | §3 表 #13 |
| `attribute_container` / `transport_class` 是无条件 `obj-y` | §2.7 的 ⚠️ 段 |
| `map.c` 零导出、单用户 | §3 表 #14、#15、§4.8 |

自审修正：`kobj_map` 的使用者从"字符设备和块设备"改成"只有字符设备"（§4.8）。

### §6.5 `05-usage.md`

| 论断 | 出处 |
|---|---|
| 全部符号在 6.1 存在且可用 | §11 的符号存在性核对表，两道门（`EXPORT_SYMBOL*` + `include/linux/` 声明） |
| §10 的近邻符号区分 | 每组带"错用会怎样" |
| 骨架未编译 | 篇首声明 + §5.1 |

自审修正：删掉了两个只有 kerneldoc、实际不导出的函数（其中一个是 §4.6 那条）。

### §6.6 `06-devicetree.md`

| 论断 | 出处 |
|---|---|
| `drivers/base/*.c` 自己不读任何 DT 属性名 | §1.2 的 ⚠️ 段，§1.2 的重跑命令 |
| 34 条 phandle 属性规则 | §3 表 #8 |
| `arm,primecell` 而非 `arm,amba-primecell` | 本篇 §1.3 第 3 条 |
| `.init_machine()` 在 arm64 不存在 | 本篇 §1.3 第 3 条末尾 |

自审修正：把"官方文档说的下钻判据"从直接采信改成显式标注为文档错误。

### §6.7 `07-debug.md`

| 论断 | 出处 |
|---|---|
| 八个命令行/模块参数钩子 | §3 表 #9、§4.2 |
| 只有一个 tracepoint | §3 表 #10 |
| probe 错误码被改写四次 | §4.4 |
| 没有故障注入 | §4.1 |
| `devres_add()` 是有界的返回值黑洞 | §4.5 |

自审修正：四处。`devres_add()` 那段（§4.5）；`§0.1` 的表从 7 行补到 8 行（漏了 `property.c`，实测 `dev_dbg`/`pr_debug` 都是 0）；`§11.3` 的 `__setup` 计数从 9 改成 8 并新增 §11.4（§4.2）；`§5.3` 代码块里四条裸文件名引用改成不带文件名的形式（脚本报了歧义，见 §7）。

### §6.8 `08-feynman.md`

| 论断 | 出处 |
|---|---|
| `bus` 和 `class` 的三条差别 | §2，两个结构体定义逐字段对比 |
| `class` 也是单指针（社团类比在此断裂） | §2 的 ⚠️ 段 |
| `__device_release_driver()` 52 行里 16 行是放锁重查 | §8，`drivers/base/dd.c:1226-1277` / `:1234-1249` |
| `remove()` 不能失败（两处 `void`） | §8，`drivers/base/platform.c:1414`、`include/linux/device/bus.h:96` |
| `of_supplier_bindings[]` 里九条是 pinctrl | §6，`drivers/of/property.c` 区间内逐条 |

自审修正：四处行号（`device_shutdown` → `:4808`；`device_add` → `:3578`；`bus_probe_device()` 调用点 → `:3697`；关机三级次序 → `:4850-4863`）。加上一处半角逗号改全角。

### §6.9 `00-overview.md`

| 论断 | 出处 |
|---|---|
| 元框架型判定 | §1.2 |
| 八条总线连定义行号 | §3 表 #5 |
| §6 的端到端链条 | 每一步带行号，与 `03` Part A 一致 |
| §2 的三段上游文档引文 | 逐字引用 + 行号 |

自审修正：五处数字（`devres.c` 1239 而非 866、`property.c` 1421 而非 1700+、Kconfig 符号是 `CONFIG_ISA_BUS_API`、八条总线表补齐真实变量名与行号、`device_unbind_cleanup()` 的行号后移一行）；一处交叉引用指错篇（总线清单在 `01-api.md` §2.4，不在 `04-extensions.md` §8）。

---

## §7 批量自检的执行情况

三个脚本，定稿时全部通过。

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/base
python3 $S/check_xrefs.py     $D
python3 $S/check_line_refs.py $D .
bash    $S/check_doc_coverage.sh base $D .
```

### §7.1 最终结果

| 脚本 | 结果 |
|---|---|
| `check_xrefs.py` | 10 篇，跨篇引用命中 159 条，失效 0 条 |
| `check_line_refs.py` | 806 条唯一引用，无法解析 0 个，越界 0 条，歧义 0 条 |
| `check_doc_coverage.sh` | 退出码 1，但是**真阴性**——见 §1.3 |

### §7.2 途中出现过的三类问题及处置

**一、跨篇引用指向尚不存在的章节。** 写完 `08-feynman.md` 时脚本报了 10 条失效，全部指向 `99-evidence.md`——那时这一篇还没写。**这不是错误，是产出顺序（`01→…→08→00→99`）的必然中间态**。99 写完后自动归零，没有做任何修改。

登记这一点是为了下次别误判：**在 99 完成之前，指向 99 的失效条数应当等于前九篇引用它的次数**，如果对不上才要查。

**二、同名头文件造成的歧义警告。** `07-debug.md` 有一节的代码块里，四条引用只写了裸文件名加行号。脚本报了歧义——它是**猜**着校验的，因为内核树里同名的 `core.c` 有多个。处置是把那四条改成只保留行号、不带文件名的形式：该代码块的上文已经点明了完整路径，块内不需要重复。

这里有一条脚本行为值得记住：**只写行号（不带文件名和扩展名）的引用，脚本完全看不见，不会被校验**。所以这种写法只适合"上文刚给过全路径、块内去重"的场合，不能拿来偷懒回避校验。

**三、裸基名解析不到。** 本篇 §1.3 讨论上游文档时效时，一处引用只写了 `usage-model.rst` 加行号，而这个基名在整套文档里从没写过一次全路径。处置是改成全路径。

**这一条是本篇自己制造的**，机理值得点出：**99 的任务之一就是"引用别的文件谈它们的问题"，而这个动作天然会产生脚本要校验的引用**。所以写 99 的时候，凡是提到某个文件，都要用全路径，即使那句话的主题是"这个文件写错了"。

### §7.3 三个脚本都抓不到的两类缺陷

| 缺陷类型 | 为什么脚本抓不到 | 本套的处置 |
|---|---|---|
| 行号在范围内但指向无关代码 | 脚本只校验"文件存在、行号不越界" | 逐个 `sed` 打开核对，不抽样。§6 每篇的"自审修正"列的都是这一类 |
| 章节存在但不讲那件事 | 脚本只校验章节号存在 | 对账轮。本套抓到一处（`00` §2.6 指向 `04-extensions.md` §8，那节实际讲 attribute container） |

第二类的抓法是**从被引方反查**：把 `01`–`08` 全部 `## §` 标题一次性 dump 出来对照，而不是顺着引用方的思路重读。`00` 那处错就是这样掉出来的——顺着读感觉毫无问题，因为 `04-extensions.md` §8 确实存在。

### §7.4 数字类错误脚本一个都抓不到

本篇 §3 那 18 个数字，脚本全都不看。它们的保证方式是**定稿前逐格重跑一遍**。§4.1 和 §4.2 那两个陷阱就是逐格重跑时掉出来的，两个都是"grep 命中了子串或漏了锚点"，两个的行号都是真的。

结论：**数字是本套最脆弱的一类论断**。§3 给出命令的目的不是好看，是让读者能一条命令推翻它。

---

## §8 下次复核该先看哪里

按"最可能已经不对"排序。

| 优先级 | 复核什么 | 怎么做 |
|---|---|---|
| 1 | §3 的 18 个数字 | 逐格重跑。任何一格对不上，本套从"取证"降级成"回忆" |
| 2 | §1.1 的基线 | `git log -1` + 那条 `git status`。换了基线就要整套重核 |
| 3 | `drivers/of/` 有没有被本树补丁改过 | §5.4 第 1 行。**这是本套已知的最大取证缺口** |
| 4 | §2.2 里 `drivers/base/core.c` 的详读区间 | 凡是引用了区间外行号的地方，都要补读那一段 |
| 5 | §5.3 的七条推断 | 尤其第 5、6 行那两个带量词的（"主要原因"、"最忙"） |
| 6 | `07-debug.md` 的示例命令 | 一旦有机会实际运行，先跑这一篇。它是全套里"未运行"这条限制影响最大的 |
| 7 | §1.3 里没逐条对照的 11 篇上游文档 | 已知三条冲突之外，剩下的只是"没撞见"，不是"没有" |

### §8.1 如果要把本套移植到更新的内核

按 §5.4 最后一行，6.1 之后有三处会直接把本套打穿：

- `struct bus_type` 的 `const` 化 → `01-api.md` §2、`02-datastructures.md` §3、`05-usage.md` §4 的骨架全部要改。
- `class_register()` 签名变更（`class_create()` 少一个参数）→ `05-usage.md` 的 class 骨架失效。
- `platform_driver->remove` 改成 `remove_new`（返回 `void`）→ `08-feynman.md` §8 的"remove 不能失败"结论**方向不变但依据要换**（新签名把 `void` 写得更明确了）。

三条都是 6.2–6.8 之间发生的，在 6.1.158 上**一条都还没发生**。

### §8.2 一句话

本套最可信的部分是 `03-internals.md` Part A 和 `07-debug.md` §1–§6——它们建立在逐行读完的四个文件（`dd.c` / `devres.c` / `bus.c` / `platform.c`）之上。最不可信的部分是任何涉及**运行时行为、时序交错、性能量级**的描述，因为一次都没运行过。
