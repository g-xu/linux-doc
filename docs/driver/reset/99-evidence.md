---
title: 取证附录
---

# reset 99 —— 取证、方法与未验证项

这一篇的作用是让读者知道**每条结论的可信度**，以及**哪些地方要自己再确认一遍**。
本篇所有数字都能用给出的命令原地重跑；只要有一个对不上，整套文档就从"取证"降级成"回忆"。

---

## §1 基线

### 1.1 树的状态

```bash
git log -1 --format='%H %s'
# f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158

git status --porcelain -- drivers/reset include/linux/reset* | wc -l
# 0
```

第二条命令是本套文档的**定性前提**：这棵树整体带有大量本地改动
（`git status` 全局有几百个 `M`），但**本子系统的五个文件一处没被改**。
所以文档描述的是**上游 6.1.158 的原样行为**，不是某个 vendor 分支的行为。

### 1.2 子系统类型判定

```bash
grep -n 'bus_type' include/linux/reset.h include/linux/reset-controller.h drivers/reset/core.c
# 无输出
```

⇒ **框架型**（无自己的 `bus_type`，通过函数调用被消费）。
判定结果决定了：02 画"核心抽象 ↔ 消费者"关系图而不是总线图；
03 的主轴是"注册 / 获取 / 动作"；05 的角色枚举是"消费者 / 控制器 / 双身份"
而不是"控制器驱动 / 设备驱动"。

### 1.3 规模（决定"读全"与"读符号表"的分界）

```bash
wc -l drivers/reset/core.c include/linux/reset.h include/linux/reset-controller.h \
      drivers/reset/Kconfig drivers/reset/Makefile
#  1289 drivers/reset/core.c
#   904 include/linux/reset.h
#   114 include/linux/reset-controller.h
#   327 drivers/reset/Kconfig
#    43 drivers/reset/Makefile

ls drivers/reset/*.c | wc -l          # 39
cat drivers/reset/*.c | wc -l         # 8132
find drivers/reset -name '*.c' | wc -l                   # 44（含 hisilicon/ sti/ tegra/ 等子目录）
find drivers/reset -name '*.c' -exec cat {} + | wc -l    # 8922
```

⇒ 核心 **2307 行**（core.c + 两个头文件）**全部逐行读完**；
驱动 8922 行**只读代表实现 + 符号表**（§2.2）。

### 1.4 读过的 Documentation

| 文件 | 行数 | 读的范围 | 纳入哪一篇 |
|---|---:|---|---|
| `Documentation/driver-api/reset.rst` | 221 | **全文** | 00 §2 · 01 §3 · 05 §3 · 08 §2/§4/§5 |
| `Documentation/devicetree/bindings/reset/reset.txt` | 75 | **全文** | 06 §1.1 · §2.1 · §3.3 · §3.4 |
| `Documentation/devicetree/bindings/reset/ti-syscon-reset.txt` | —（只读 `:30-47`） | cell 语义表 | 06 §4.3 |
| `Documentation/devicetree/bindings/reset/` | 47 个文件 | 只统计数量 + 抽查 2 个 | 06 §1.1 |

```bash
wc -l < Documentation/driver-api/reset.rst                              # 221
wc -l < Documentation/devicetree/bindings/reset/reset.txt               # 75
ls Documentation/devicetree/bindings/reset/ | wc -l                     # 47
# 其中 .yaml/.txt 绑定 34 篇（覆盖率脚本的口径），其余是 include 的文本片段
```

**`reset.rst` 里有三条从代码读不出来的结论**，全部被引用了：

1. `:98-104` —— shared 的 assert **不保证**线真的进入断言状态（→ 07 §6、08 §1）；
2. `:115-122` —— `rearm()` 的语义解释（→ 08 §4）；
3. `:161` —— **All functions are optional.**（→ 00 §4.3、08 §5）。

⚠️ 未发现 `reset.rst` 与代码冲突的地方（对比过 assert/deassert/reset/rearm 四段描述）。

---

## §2 阅读范围

### 2.1 一等证据：逐行读完的文件

| 文件 | 说明 |
|---|---|
| `drivers/reset/core.c`（1289 行） | 全文逐行。所有函数边界、锁临界区、返回码位置都是从这里数出来的 |
| `include/linux/reset.h`（904 行） | 全文。69 个 `static inline` 的返回值逐个核对过 |
| `include/linux/reset-controller.h`（114 行） | 全文。三个结构体字段逐个 |
| `drivers/reset/Kconfig`（327 行） | 全文（为统计 `COMPILE_TEST` 和确认 `default y if ARCH_HAS_RESET_CONTROLLER`） |
| `drivers/reset/Makefile`（43 行） | 全文 |

### 2.2 二等证据：详读了部分行的驱动

**选法**：一个最简 MMIO 实现 + 一个 syscon 实现 + 一个固件实现 + arm64 DTS 实际在用的，
其余只查符号和 ops 表。

| 文件 | 详读范围 | 用在哪 |
|---|---|---|
| `drivers/reset/reset-simple.c` | `:60-105`（四个回调 + ops 表 + 导出）、`:170-200`（probe） | 04 §1 · 05 §6 · 08 §6 |
| `drivers/reset/reset-ti-syscon.c` | `:60-120`（回调 + flags 判断）、`:170-200`（probe 与那行除法） | 04 §2 · 05 §7 · 06 §4.3 |
| `drivers/reset/hisilicon/reset-hi3660.c` | `:60-110`（id 打包 + ops + of_xlate 设置） | 04 §2.3 · 05 §7 · 06 §4.2 |
| `drivers/reset/reset-scmi.c` | `:40-90`（三个回调 + ops 表） | 04 §3 · 07 §5.2 |
| `drivers/reset/tegra/reset-bpmp.c` | `:40-70`（ops 表） | 07 §5.2 |
| `drivers/reset/reset-raspberrypi.c` | `:50-80`（只有 `.reset` 的 ops 表） | 07 §5.2 · 08 §5 |
| `drivers/reset/reset-zynqmp.c` | `:130-145`（initcall） | 07 §4.2 |
| `drivers/reset/reset-intel-gw.c` | `:250-265`（initcall + 注释） | 07 §4.2 |
| `drivers/reset/reset-microchip-sparx5.c` | `:170-180`（initcall + 注释） | 07 §4.2 |
| `drivers/clk/bcm/clk-bcm2711-dvp.c` | `:40-55`（复用 `reset_simple_ops` 的那几行） | 08 §6 |
| `drivers/clk/davinci/psc-da850.c` | `:65-80`（唯一的 `RESET_LOOKUP` 使用者） | 05 §9 |
| `drivers/ata/libahci_platform.c` | `:155-190`、`:530-540`（两对 API 的并列写法 + shared get） | 05 §3.3 · 08 §2 |
| `drivers/gpu/drm/tegra/gr3d.c` | `:345-370`（probe 期 acquire/release 自检） | 05 §5.2 · 08 §3 |

### 2.3 三等证据：只查了符号 / 只看了 ops 表

- `drivers/reset/` 其余约 30 个驱动：只 `grep` 了 ops 表字段和 `initcall` 形式，
  **没读实现**。凡是文档里说"某类控制器缺哪个回调"，依据都是 ops 表的字段缺失，
  不是读过它的实现。
- `drivers/clk/` 下 33 个注册复位控制器的文件：**只统计数量**，只读了 bcm2711-dvp 一个。

### 2.7 显式裁掉的部分：ACPI

按用户要求，arm64-only 且不描述 ACPI。全框架的 ACPI 代码只有**一处**：

```bash
grep -n 'acpi\|ACPI' drivers/reset/core.c
# 15:#include <linux/acpi.h>
# 1104: * lines. _RST firmware method will be called for devices with ACPI.
# 1111:#ifdef CONFIG_ACPI
# 1112:	acpi_handle handle = ACPI_HANDLE(dev);
# 1115:		if (!acpi_has_method(handle, "_RST"))
# 1117:		if (ACPI_FAILURE(acpi_evaluate_object(handle, "_RST", NULL,
```

裁掉的内容：`__device_reset()`（`drivers/reset/core.c:1106`）在 DT 路径拿不到句柄时，
会退回到调用 ACPI 的 `_RST` 方法；没有 `_RST` 方法时返回 `-ENOENT`
（`:1116`），`_RST` 调用失败返回 `-EIO`（`:1119`）。

这也意味着 **`-EIO` 这个错误码在 arm64 DT 系统上永远不会出现**
——07 §5.3 的错误码表因此没有 `-EIO` 这一行，这是有意的，不是漏了。

---

## §3 一等证据：可机械复现的统计

每条都是一行命令，每次 review 全部重跑。**本篇最后一次重跑时间 = 文档定稿时**。

| 论断 | 命令 | 结果 |
|---|---|---|
| 核心导出 27 个符号 | `grep -c 'EXPORT_SYMBOL_GPL' drivers/reset/core.c` | **27** |
| 核心有 21 处 WARN | `grep -c 'WARN' drivers/reset/core.c` | **21** |
| 头文件里 69 个 inline | `grep -c 'static inline' include/linux/reset.h` | **69** |
| Kconfig 里 33 处 `COMPILE_TEST` | `grep -c 'COMPILE_TEST' drivers/reset/Kconfig` | **33** |
| 零 debugfs / 零 tracepoint / 零 sysfs | `grep -rn 'debugfs\|TRACE_EVENT\|trace_\|DEVICE_ATTR' drivers/reset/` | **无输出** |
| 零自陈设计债 | `grep -rn 'FIXME\|TODO\|REVISIT\|XXX\|HACK' drivers/reset/ \| wc -l` | **0** |
| `drivers/clk/` 下 33 个文件注册复位控制器 | `grep -rl 'reset_controller_register' drivers/clk/ \| wc -l` | **33** |
| 只有两个驱动用 `dev_err_probe` | `grep -rl 'dev_err_probe' drivers/reset/` | `reset-tps380x.c`、`reset-rzg2l-usbphy-ctrl.c`（共 3 处调用） |
| arm64 DTS 的 `#reset-cells` 分布 | `grep -rhoE '#reset-cells = <[0-9]+>' arch/arm64/boot/dts/ \| sort \| uniq -c` | **`<0>` 127 · `<1>` 212 · `<2>` 14** |
| 本版本没有 `reset-gpio.c` | `ls drivers/reset/ \| grep -i gpio` | **无输出**（详见 §7.3） |

关于"零自陈设计债"这条：它是 03 Part C 的起点——**源码里一条 FIXME 都没有，
所以 03 §C1 列的六条不一致全部是本套文档自己判定的**，属于推断（§5.3），
读者可以不同意。

### 3.1 可观测性与日志：为什么说"一个可以量化的零"

```bash
grep -c 'DEVICE_ATTR\|sysfs'   drivers/reset/core.c     # 0
grep -c 'debugfs'              drivers/reset/core.c     # 0
grep -c 'trace_\|TRACE_EVENT'  drivers/reset/core.c     # 0
grep -rn 'debugfs\|TRACE_EVENT\|trace_\|DEVICE_ATTR' drivers/reset/   # 全目录无输出
```

还有一条更极端的：**整个 `core.c` 只有一条普通日志语句。**

```bash
grep -n 'pr_warn\|pr_err\|pr_info\|dev_err\|dev_warn' drivers/reset/core.c
# 183:			pr_warn("%s(): reset lookup entry badly specified, skipping\n",
```

而这唯一一条落在 **lookup 表**这条 arm64 上基本不走的路上（§3.2）
⇒ **DT 路径上一条普通日志都没有，只有 `WARN` 系列**。
这是 03 §C3 和 07 §3 的共同论据。

### 3.2 lookup 表这条路有多冷

```bash
grep -rn 'reset_controller_add_lookup' --include=*.c --include=*.h .
```

去掉核心自己的定义/导出/声明（`drivers/reset/core.c:172`、`:192`、
`include/linux/reset-controller.h:90` 与 `:108` 的桩）之后，
**全树只剩一个真实调用者**：`drivers/clk/davinci/psc-da850.c:75`。

```bash
grep -rl 'RESET_LOOKUP' --include=*.c .    # 同样只有 drivers/clk/davinci/psc-da850.c
```

⇒ 「非 DT 平台的查表机制在 arm64 上用不到」这个论断的完整依据是：
**唯一使用者是一个 DaVinci（ARMv5）时钟驱动。**

### 3.3 acquire / release 这套机制谁在用

```bash
grep -rl 'reset_control_acquire\|reset_control_bulk_acquire' --include=*.c . \
  | grep -v drivers/reset/core.c
```

**6 个文件，全部是 Tegra**：

| 文件 | 角色 |
|---|---|
| `drivers/gpu/drm/tegra/gr2d.c` | 2D 引擎 |
| `drivers/gpu/drm/tegra/gr3d.c` | 3D 引擎（probe 期自检那段就在这里） |
| `drivers/gpu/drm/tegra/sor.c` | 显示输出 |
| `drivers/gpu/host1x/dev.c` | host1x 总线 |
| `drivers/media/platform/nvidia/tegra-vde/vde.c` | 视频解码 |
| `drivers/soc/tegra/pmc.c` | 电源域控制器 |

`_exclusive_released` 变体的使用者是**同一批 6 个文件**（同命令换模式即得）。

⇒ 这条机制**不是通用设施，而是为一个平台的一类硬件加的**。
02 §1.3、03 §A5、08 §3 都以此为据说"先按 Tegra 的用法理解它"。

附一条同类的名字核查（05 §9 用到）：
`reset_control_bulk_optional_get_exclusive_released` 这个名字在
`include/linux/reset.h` 的注释块里出现三次，但**全树没有任何函数叫这个名字**——
真名是 `reset_control_bulk_get_optional_exclusive_released`。
照注释抄名字会编译不过，以声明为准。

### 3.4 谁在实现复位控制器（含"能不能睡"）

```bash
grep -rl 'reset_controller_register' --include=*.c --include=*.h . \
  | awk -F/ '{print $2"/"$3}' | sort | uniq -c | sort -rn
```

| 目录 | 文件数 |
|---|---:|
| `drivers/reset` | **43** |
| `drivers/clk` | **33** |
| `drivers/soc` | 5 |
| `drivers/{watchdog,usb,ufs,power,phy,memory,firmware}` | 各 1，合计 7 |
| `arch/arm`、`arch/mips` | 各 1（**`arch/arm64` 下 0 个**） |
| `include/linux` | 1（声明，不是实现） |

⇒ **33 : 43** 就是 04 §6 那个比例的来源；90 个实现文件里有一半以上不住在
`drivers/reset/`。

「四个动作回调可以睡」这一条的依据是**缺失证明**：

```bash
grep -c 'might_sleep' drivers/reset/core.c    # 0
```

核心既不声明自己可能睡，也不阻止驱动睡。而固件类控制器（SCMI / BPMP）
的回调实际会睡 ⇒ 结论只能反过来推：**复位 API 一律按"可能睡眠"对待，
不要在原子上下文里调用**（01 §7、03 §B4、07 §5.2 都引用这一条）。

⚠️ 这是本套文档里**唯一一条建立在"某个东西不存在"之上的行为约束**，
证据强度低于其他条目，单列在这里提醒。

---

## §4 三等证据：来自硬件常识而非本树代码的内容

**00 §2 整节（硬件背景）里，只有下列内容有本树依据**：

| 00 §2 的小节 | 依据 |
|---|---|
| §2.5 两种寄存器形态 | 有代码依据：`reset()`/`rearm()` 与 `assert()`/`deassert()` 两对 API 的存在本身，加上 `reset.rst:115-122` |
| §2.6 四种控制器实现形态 | 有代码依据：四类驱动的 ops 表和访问方式（§2.2 那张表） |
| §2.2 极性 | 半有：`ti-syscon.h` 的 `ASSERT_SET`/`ASSERT_CLEAR` flags 证明极性可配，但"哪种更常见"是常识 |
| §2.1 复位在做什么 · §2.3 同步/异步 · §2.4 三个层级 · §2.7 时序要求 | ✗ **纯硬件常识，本树无依据** |

⚠️ 所以：**00 §2.1 / §2.3 / §2.4 / §2.7 属于三等证据**。
它们在讲道理，不在讲这棵树。特别是 §2.3 那条"很多 IP 的复位是同步的，
必须先开时钟"——**方向是对的，但"很多"这个量词没有统计支持**，
具体某颗 SoC 要查它自己的手册。

---

## §5 未验证项（四层分层）

这一节是本套文档里**最应该被读者当真**的一节。

### 5.1 第一层：未编译、未运行

| 内容 | 状态 |
|---|---|
| `05-usage.md` 里全部四份骨架（消费者 / 最简控制器 / syscon 控制器 / 双身份驱动） | **一行都没编译过**。符号名、字段名、返回码语义逐个 grep 确认存在于本版本，但**没有过编译器**。抄进真实驱动前至少先过一遍 `make M=` |
| `07-debug.md` §3.2 (b) 那份 kprobe 配方 | **没有在真实硬件或 QEMU 上跑过**。`kprobe` 的事件名、`$retval` 的用法是按 ftrace 文档写的，参数偏移在不同编译器/优化级别下会变 |
| `07-debug.md` 里所有 shell 命令的**输出** | 只有"无输出"这一类被真实执行过（`grep` 那三条，§3 表里）。凡是给了输出样例的地方都是**按代码里的 `printk` 格式串手工构造的**，不是抄的真实 dmesg |
| `06-devicetree.md` 里的 DTS 片段 | 引用 in-tree 节点的部分是原样抄的（可 diff）；**示范用的片段没有过 `dtc`** |
| 任何"这样写会挂死 / 会读到全 0"的论断 | ✗ 没有实测。这是硬件行为，见 §4 |

### 5.2 第二层：只读了符号表 / ops 表，没读实现

| 内容 | 影响哪些论断 |
|---|---|
| `drivers/reset/` 约 30 个未详读的驱动 | 「某类控制器缺哪个回调」这类论断依据是 **ops 表字段缺失**。如果某个驱动在 `.reset` 里自己做了 assert+delay+deassert，本文档看不出来 |
| `drivers/clk/` 那 33 个文件 | 只知道**它们调用了注册函数**，不知道各自的 cell 语义、nr_resets 来源、是否复用公共 ops。只读了 bcm2711-dvp 一个 |
| SCMI / BPMP / SCPI 固件侧的协议实现 | ✗ **完全没读**。「固件控制器的回调会睡眠」是从"它们经过 mailbox + 等回复"推出来的，不是读了固件传输层确认的 |
| `Documentation/devicetree/bindings/reset/` 其余 45 个绑定文件 | 只抽查了 2 个（generic 的 `reset.txt` 和 `ti-syscon-reset.txt`）。「绝大多数绑定是 1 个 cell」的依据是 **DTS 端的统计**（§3），不是绑定文件端的统计 |

### 5.3 第三层：推断而非观察

这一层是"代码没这么说，是我这么判断的"。**读者完全可以不同意。**

| 论断 | 出现在 | 为什么是推断 |
|---|---|---|
| `03-internals.md` §C1 列的设计债（六条不一致） | 03 Part C | 源码里**一条 `FIXME`/`TODO` 都没有**（§3）。这六条全是本文档自己的判断，不是维护者的自陈 |
| `04-extensions.md` §5 讲的"为什么 hisilicon / sti / tegra 单独建了子目录" | 04 §5 | 只有 Makefile 和目录结构这一个事实，"因为一家厂商有多个 SoC 变体"是推断。git log 没查 |
| 「reset 的可观测性缺失一半是"确实没什么可看的"、一半是"有的东西也没人做"」 | 07 §3 · 08 §8 | 前半有依据（没有拓扑、没有聚合量）；**后半是价值判断** |
| 「`-EINVAL` 覆盖八种语义是设计债而不是刻意简化」 | 03 §C1 · 07 §5.3 | 行号和语义分组是事实，**"这是债"是判断** |
| 「固件类控制器的回调会睡眠，所以不能在原子上下文调用复位 API」 | 01 §7 · 07 §5.2 | 结论方向可靠（核心持 mutex 就已经排除原子上下文），但"具体哪个驱动会睡多久"没测 |
| 「shared + 电平保持是官方推荐写法而非变通」 | 08 §2 | 依据是 `reset.rst:94-96` 那句配平要求 + libahci 的实际写法。"推荐"这个词是我加的 |

### 5.4 第四层：版本时效

- 所有结论**只对 6.1.158 成立**。`drivers/reset/core.c` 在 6.1 之后有过改动
  （例如 `reset_control_bulk_*` 系列的扩充节奏），本文档**没有做跨版本对照**。
- 特别提醒两处高危时效点：
  1. **`reset-gpio.c` 在更新的内核里已经存在**（详见 §7.3）。如果你在读 6.6+ 的树，
     04 篇的"控制器家族"分类需要多一类。
  2. **cell 数统计（127/212/14）随 DTS 增删而变**，只是一个数量级参照，
     不要当成"某个具体 SoC 用几个 cell"的依据。

---

## §6 逐篇取证表

每篇列：**主要论断 → 出处**，以及**这一篇在自审中改过什么**。

| 篇 | 行数 | 核心论断的出处 | 自审修正记录 |
|---|---:|---|---|
| `00-overview.md` | 614 | §1 规模数字 → §3 那张表；§3–§6 全部浓缩自 02/03，行号继承；§7 邻居表 → 各邻居子系统的实际调用点 | §2 硬件背景一节改过一轮：初稿把"两种寄存器形态"和"shared/exclusive"混成一条轴，改成 2×2 正交表（同一处错误在 08 §2 也改了） |
| `01-api.md` | 473 | 27 个导出符号逐个对 `EXPORT_SYMBOL_GPL` 行；69 个 inline 桩的返回值逐个读；§7 错误码表 → core.c 里每个 `return -E*` | 两次修正：错误码章的自引用编号写错（指向了本篇另一节）；一处指向调试篇的小节号已不存在，重定向到该篇 §2.1 |
| `02-datastructures.md` | 678 | 三个结构体全部逐字引用 `core.c` / `reset-controller.h`；§1 那四组关系的"为什么不会发生"都带行号 | 三计数表初稿写"`acquired` 是计数"，实际是 `bool`；已改 |
| `03-internals.md` | 877 | Part A 五个时间点全部逐行；Part B 两把锁的临界区边界逐个 `mutex_lock`/`unlock` 配对确认 | 初稿写"核心只有一把锁"，实际有两把（`:23` 和 `:24`）；整节重写 |
| `04-extensions.md` | 536 | 四类控制器形态 → §2.2 那张详读表；`reset_simple_ops` 的复用 → 导出行 + 使用点计数 | 「`nr_resets` 由核心强制校验」被推翻：核心只在默认 xlate 里比对，控制器换了 xlate 就没人管（改写为条件成立的说法） |
| `05-usage.md` | 694 | 四份骨架的每个符号全树 grep 过；§9 `RESET_LOOKUP` 的唯一使用者 → `drivers/clk/davinci/psc-da850.c` | 一处指向实现篇的小节号在重排后失效（该篇改用 A/B/C 前缀编号），已重定向到静态函数那一节 |
| `06-devicetree.md` | 519 | §1.1 那三条属性 → `__of_reset_control_get` 里逐个读取点；四种 cell 语义 → 四个具体驱动 | `#reset-cells` 分布初稿凭印象写"几乎都是 1"，改为实测 127/212/14 |
| `07-debug.md` | 411 | 三个零 → §3 的三条空输出命令；§5.3 错误码分诊表 → 每个返回点；§4.2 initcall 等级 → 三个驱动的实际宏 | 两次修正：(a) 初稿把某个 zynqmp 驱动的 initcall 说成 `postcore_initcall`，实为 `arch_initcall`；(b) **回调缺失时的返回码被张冠李戴**，详见 §7.3 第 7 条 |
| `08-feynman.md` | 602 | 八个题目每个都落到行号；§2 的三条证据（官方文档一句 + libahci 两段并列代码 + 两个 `WARN_ON`） | 同 07 (b) 的那处错误在本篇 §5 也改了：换成源码原文 + 三行对比表 |

合计 **00–08 共 5404 行**，加本篇（472 行）共 **5876 行**。

```bash
wc -l output/reset/*.md
```

---

## §7 方法与工具

### 7.1 取证纪律

1. **凡是"大意是 X"的段落必查**：本套文档的错误 100% 集中在没有逐字引用源码的地方。
   贴了真实代码块的段落，自审中一处没错。
2. **否定性论断也要行号**："为什么不会发生 Y"和"会发生 X"是同等强度的断言。
3. **全称断言必须全树 grep**：「全树唯一」「只有 X 这么用」这类话，
   grep 范围不能局限在子系统自己的目录——本子系统最典型的反例就是
   `drivers/clk/` 下那 33 个文件。
4. **表格里的"—"和"可配"是欠债的记号**，定稿前必须清零。
5. **每篇写完立刻跑行号脚本**，不攒到最后。

### 7.2 三个自检脚本的坑（跨子系统通用，逐套累积）

脚本在 skill 的 `scripts/` 目录下，三条命令见 PROGRESS.md。已知行为：

| # | 坑 | 应对 |
|---|---|---|
| 1 | `check_doc_coverage.sh` 的参数是**相对 `Documentation/` 的路径**，不是子系统昵称 | 官方文档有几个位置就跑几次；只有一个位置时另一次会报"没找到独立的官方文档目录"，**这是正确输出**。本套跑了 `driver-api/reset` 和 `reset` 两次 |
| 2 | 同一脚本报的**引用次数不可信**（按 basename 子串匹配） | 只采信 **0 引用**这一个信号，次数一律不引用 |
| 3 | 行号脚本**只有见过 `完整/路径:行号` 形式的引用，才会登记这个文件的全路径**。表格里裸写一个路径**不算** | 每个会用简写 `xxx.h:NNN` 的文件，全套里至少要有一处完整路径带行号的引用 |
| 4 | 行号脚本在**逗号**处切分文件名 | 文件名里含逗号的绑定文件（如 `arm,pl330.yaml`）**一律不带行号引用** |
| 5 | 同名 basename ≥2 个全路径同时出现在一套里会报 AMBIGUOUS；`--ignore` **只对越界行号有效**，对 AMBIGUOUS 无效 | 全树最容易撞的是顶层 `Makefile`，其次 `Kconfig`——这两个**一律不带行号引用** |
| 6 | 跨篇引用脚本的一个真实假阳性：同一行里先出现另一篇的文件名，60 字符内又出现指向**本篇**的小节号，脚本会把这个小节号算到那另一篇头上 | **靠调整语序修**（把指向本篇的引用写在前面），**绝不登记豁免**。同理：正文里不要写别套文档的 `output/<别的子系统>/…` 路径加小节号 |
| 7 | 跨篇引用脚本用的正则**不匹配「NN 篇 §X」这种带"篇"字的写法** | 需要故意让一处引用不被检查时用这个写法；反之要被检查就必须写成文件名形式 |
| 8 | 本篇（99）自己最容易污染检查结果 | 三个雷：(a) 在自审记录里**原样重抄**一个曾经写错的 `文件:行号` —— 一律改写成散文；(b) 引用脚本自己在 `~/.claude/…` 下的路径并带行号；(c) 为了举例而**写出那个假阳性字符串本身** |

⚠️ 本套的脚本结果（定稿时）：**两个 `.ignore` 文件都不存在——零豁免**。

- 行号引用：**250 条唯一 `文件:行号`，0 越界、0 解析不到**（消歧表 25 个基名 / 29 个前缀目录）。
- 跨篇引用：**327 条全部命中**。
- 覆盖率：`driver-api/reset` 被引用 24 次（必查档 0 篇零引用）；
  DT 绑定 34 篇里 5 篇被点名引用 —— 按脚本自己的提醒，
  06 篇的硬要求是"每条属性都有定位到行的消费者"，不是每篇绑定都要被引用。
  另一次以 `driver-api/reset` 为参数的运行报"没找到独立的官方文档目录"，
  是 §7.2 第 1 条描述的正确输出。

### 7.3 自我修正记录（全部十处）

保留这一节的理由：**它标出了这个子系统最容易被误读的地方**——
我踩过的坑，读者大概率会踩同一个。

| # | 起初写的 | 实际 | 怎么发现的 |
|---|---|---|---|
| 1 | `nr_resets` 由核心强制校验，越界一定被拦 | 只有**默认 xlate** 会比对 `nr_resets`；控制器自带 `of_xlate` 时核心不看它 | 读 hi3660 的 xlate 时发现它压根没用 `nr_resets` |
| 2 | shared/exclusive 和"脉冲/电平"是同一条轴 | **两条正交的轴**，四种组合都存在且都合法 | 官方文档明写 shared 的 assert/deassert 必须配平；libahci 的 shared 句柄两对 API 都用 |
| 3 | 同一条线被两个驱动 `get_exclusive` 会在**取句柄时**失败 | `_released` 变体下取句柄**永远成功**，冲突推迟到运行期 `acquire()` | 逐行读那个 `break` 判据，发现它只看新请求的 `acquired` |
| 4 | 某个 zynqmp 复位驱动用 `postcore_initcall` | 实为 `arch_initcall` | 07 §4.2 定稿前逐个 `sed` 打开三个驱动的 initcall 行 |
| 5 | 核心只有一把 `mutex` | **两把**（一把管控制器链表，一把管查找） | 03 Part B 配对 `mutex_lock`/`unlock` 时发现有两个不同的锁变量 |
| 6 | `rearm()` 对所有句柄都只是递减计数 | shared 分支递减 `triggered_count`；**exclusive 分支一个计数都不动**，只检查 `acquired` | 逐行读该函数的 else 分支 |
| 7 | `deassert` 在回调缺失时：shared 返回 0、exclusive 返回 `-ENOTSUPP` | **完全张冠李戴**。那两个返回点在 `assert` 函数体内；`deassert` 缺回调时**无条件返回 0**，源码里还有一段注释专门解释为什么（"假定这条线是自解除的"） | 定稿 08 §5 时去把 `reset_control_deassert()` 的函数边界完整读了一遍，发现引用的行号根本不在这个函数里 |
| 8 | 两处跨篇小节号引用（API 篇 → 调试篇、示例篇 → 实现篇） | 目标小节号在重排后已不存在 | 跨篇引用脚本报出来的 |
| 9 | 「reset 完全没有可观测性，因为没什么可看的」 | 半对。**「谁把 `deassert_count` 撑住了」是一个真问题，数据也是现成的，只是没人写这段代码** | 写 08 §8 那张 clk 对比表时，第三行推不出 ✗，改成 △ |
| 10 | PROGRESS.md 里预判「有 `reset-simple`、`reset-gpio` 之类的通用实现」 | **6.1 里没有 `reset-gpio.c`**。`drivers/reset/Kconfig` 第 11–12 行的帮助文本确实提到"通过 GPIO 或 SoC 内部复位控制器模块"，但 GPIO 那条路在本版本只能自己在驱动里 `gpiod_set_value`，没有通用实现 | `ls drivers/reset/ \| grep -i gpio` 空输出，再 `git grep -l reset_controller_register drivers/gpio/` 也是空 |

第 10 条顺便说明一件事：**PROGRESS.md 里的"已知特点（待核实）"就是待核实的**，
三条里两条对（shared/exclusive 确实成了 08 篇的主题；inline 桩确实要单列一节），
一条错。预判不能当结论用。

---

## §8 工具与环境

所有取证都在同一台机器、同一棵树上完成。版本记下来，是为了让"重跑对不上"时
能先排除工具差异。

| 工具 | 版本 | 备注 |
|---|---|---|
| Python | 3.10.12 | 三个自检脚本纯 stdlib，无第三方依赖 |
| GNU grep | 3.7（`/usr/bin/grep`） | 本篇 §1/§3 里所有 `grep` 命令都指这一个 |
| ugrep | 7.5.0 | ⚠️ 本机 shell 里裸写 `grep` 落到的是这个：**不支持 `\s`**，而且**默认遵守 `.gitignore`**。取证一律显式写 `/usr/bin/grep` 或改用 POSIX `[[:space:]]` |
| ripgrep | 13.0.0 | 用于全树 grep（第 19/20 条纪律） |
| git | 2.34.1 | 只用只读子命令（`log` / `status` / `grep`） |
| cbm（codebase-memory MCP） | — | **只用于导航找候选，一条论断都没直接采信**。宏和函数指针间接调用是它的两大盲区，恰好是本子系统的 `of_xlate` 回调所在 |

⚠️ 一条踩过的环境坑：**对 `arch/arm64/boot/dts/<厂商>/` 递归 grep 会撞 2 分钟命令超时**。
本篇 §3 那条 `#reset-cells` 统计之所以用 `grep -rhoE` 加短模式，就是为了让它跑得完；
需要在 DTS 里找长模式时，把范围收窄到单个文件或单个小目录。

另外两条与本套文档无关但会影响重跑的坑（记在这里避免下次重犯）：
写 Python 补丁脚本时，替换串前面**不能**跟 `b`（那是 bytes 字面量，会静默失败或语法错），
且**替换串里含 ASCII 双引号时必须用三引号字符串**。

---

## §9 与外部文档集的对照

**无对照套。**

前六套（pinctrl / gpio / clk / dma / regulator / mailbox 中的前四套）在 `output/qoder/`
下有另一个工具生成的同主题文档可做交叉比对（规则 29），
**reset 没有**——`output/qoder/reset/` 不存在：

```bash
ls output/qoder/
```

所以本套文档的所有结论**只经过了一遍自审，没有经过第二来源的对照**。
这对读者的实际含义是：**§5.3 那一层（推断而非观察）比前几套更值得怀疑**，
因为前几套里有几处推断正是被对照套的不同说法逼出来重新取证的。

如果你手上有别的来源写的 reset 文档，最值得先对的三处：

| 优先级 | 对哪一条 | 为什么它最容易分歧 |
|---|---|---|
| 1 | 回调缺失时三个动作各返回什么（00 §4.3 那张四行表） | 这是我自己唯一栽过的**事实性**错误（§7.3 第 7 条），三个动作的行为真的不一致，凭直觉写必错 |
| 2 | shared/exclusive 与脉冲/电平是两条轴（08 §2） | 网上流传的解释里把两者混为一谈的很多 |
| 3 | 「`_optional_` 变体拿不到句柄时返回 NULL 而不是错误」的后果链（08 §5） | 结论本身不难，但后果链（返回 0 → 以为解除了 → 读寄存器全 1）容易被写漏 |

---

## §10 去哪一篇

| 你想要的 | 去处 |
|---|---|
| 全貌 / 复习入口 | `00-overview.md` |
| 查 API 和文件位置 | `01-api.md` |
| 结构体与三个计数器 | `02-datastructures.md` |
| 逐行实现剖析 | `03-internals.md` |
| 各控制器家族 | `04-extensions.md` |
| 可抄的骨架 | `05-usage.md` |
| DTS 写法与配错定位 | `06-devicetree.md` |
| 出问题怎么查 | `07-debug.md` |
| 大白话解释难点 | `08-feynman.md` |
