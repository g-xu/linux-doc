---
title: 取证附录
---

# 99 取证：这套 clk 文档是怎么写出来的、哪些地方要你自己再确认

> 本篇不讲 clk，讲**这套文档的可信度边界**。
> 前九篇里每一条论断的出处、每一个数字的复现命令、每一处"我没验证"的坦白都在这里。
> 判据只有一条：**如果本篇里的某个数字重跑对不上，整套文档就从"取证"降级成"回忆"**。

| 想知道 | 看哪节 |
|---|---|
| 我读的是哪棵树、clk 有没有被补丁改过 | §1.1 |
| clk 属于哪种子系统类型（决定模板怎么套） | §1.2 |
| 官方文档我读了哪些、哪些被判过时 | §1.3 |
| 哪些文件是逐行读的（一等证据），哪些只读了符号表 | §2.1 / §2.2 |
| 文档里每个数字怎么重跑 | §2.3 |
| arm64-only 裁掉了什么 | §2.7 |
| 每一篇的论断→出处对照 | §3 |
| 哪些初始假设被源码推翻了 | §4 |
| 哪些结论我明确没验证 | §5 |
| 官方文档和代码冲突的地方 | §6 |
| 三个自检脚本的结果 | §7 |

---

## §1 基线

### §1.1 树的状态

```bash
$ git log -1 --oneline
f6e38ae624cf Linux 6.1.158
```

这棵树**不是纯净的上游 6.1.158**——`git status` 显示上百个文件被改动
（`arch/arm/`、`arch/arm64/`、`drivers/net/`、`drivers/gpu/drm/i915/` 等，
形态像是打了一套 PREEMPT_RT 相关的补丁）。所以下面这一步是**定性的**：

```bash
$ git status --porcelain -- drivers/clk include/linux/clk.h \
      include/linux/clk-provider.h include/linux/clk/ \
      include/trace/events/clk.h Documentation/driver-api/clk.rst
（无输出）
```

> **结论：clk 子系统在这棵树上一个字节都没被改。**
> 因此本套文档的所有行号、所有代码引用，都可以直接对照上游 v6.1.158 的
> `drivers/clk/`，不需要考虑本地补丁。这一条是整套文档能被外部读者复核的前提。

反过来说：如果你在**别的** 6.1.y 小版本上重跑，行号可能会差几行——
clk 核心在 6.1 稳定分支里改动很少，但不为零。判据是先跑一次上面那条
`git status`，再抽查本篇 §2.3 里的两三个计数。

### §1.2 子系统类型判定（skill 规则 3b）

```bash
$ /usr/bin/grep -rn 'struct bus_type' drivers/clk/
（无输出）
$ /usr/bin/grep -n 'bus_type' include/linux/clk.h include/linux/clk-provider.h
（无输出）
```

对照物（同一棵树）：

```bash
$ /usr/bin/grep -n 'struct bus_type i2c_bus_type' drivers/i2c/i2c-core-base.c
699:struct bus_type i2c_bus_type = {
```

> **判定：clk 是【框架型】子系统，不是硬件总线型。**

这一条判定不是标签，它改变了四篇文档的骨架：

| 篇目 | 因为"框架型"而改的地方 |
|---|---|
| 02 | 没有"总线 ↔ 父总线"图，改画**核心抽象 ↔ 消费者/提供方**三角（`clk_core` / `clk` / `clk_hw`） |
| 03 | 主轴不是"传输"，是**注册 → 拓扑成形 → 调频 → 批量关闭** |
| 04 | clk 没有 i2c 那种独立子框架（mux/slave/smbus），改成讲**基础时钟类型**（gate/divider/mux/composite/fixed/fractional/multiplier） |
| 08 | §5 专门解释"clk 为什么没有 `bus_type`"——这是最容易被类比错的一点 |

附带后果：clk **没有字符设备、没有 sysfs 类、没有用户态工具**。

```bash
$ /usr/bin/grep -rn 'misc_register\|class_create\|cdev_add' drivers/clk/*.c
（无输出）
```

所以 07 的可观测面只有 **debugfs + tracepoint + dmesg** 三样，
没有 07 模板里那个"用户态工具节"。

### §1.3 官方文档登记（skill 规则 6，阻塞性前置）

| 文件 | 行数 | 读法 | 纳入了什么 |
|---|---|---|---|
| `Documentation/driver-api/clk.rst` | 307 | **全文详读** | 设计意图（两半式接口、`clk_hw` 的桥梁作用）、必选回调矩阵、可重入锁的 rationale（`:298-303`）、`clk_ignore_unused`（`:255-262`）、`.enable` 不许睡眠（`:270-273`） |
| `Documentation/admin-guide/kernel-parameters.txt` | — | 只读 `clk_ignore_unused` 一条（`:594`） | 07 §2.1、08 §8 的那张"诊断 vs 修复"分诊表 |
| `Documentation/devicetree/bindings/clock/` | 317 项（223 `.yaml` + 91 `.txt` + 3 个子目录） | **只做统计 + 抽读** | 06 的素材；抽读的具体文件见 §2.2 |
| `Documentation/devicetree/bindings/clock/clock-bindings.txt` | **2** | 全文（就 2 行） | 见下 |

> **一个必须前置说明的"版本时效"事实**：clk 的**通用** DT 绑定
> **已经搬出内核树**了。`clock-bindings.txt` 在 6.1.158 里只剩 2 行，
> 内容是一条指向树外 `dt-schema` 仓库的 URL。
>
> 后果：**本树里找不到一份权威的"clk 通用属性清单"**，
> `Documentation/devicetree/bindings/clock/` 下剩的 316 个文件全是具体 SoC 的绑定。
> 06 §1.1 那张属性总表因此**不是抄绑定文档**，而是反过来从 `drivers/clk/`
> 的消费代码里 grep 出来的——所以每一行都有行级出处，也所以它可能**漏**
> （漏的判据见 §5.3）。

三等证据的风险在 clk 上**兑现了三次**，逐条列在 §6.1。
最要紧的一条：`clk.rst:248` 还在推荐已废弃的 `clk_register()`。

`Documentation/driver-api/clk.rst` **不覆盖调试**：

```bash
$ /usr/bin/grep -in 'debugfs\|clk_summary\|trace' Documentation/driver-api/clk.rst
（无输出）
```

所以 07 整篇（858 行）**没有任何官方文档背书**，全部来自代码。
这是 07 §9 那句结论的出处：「官方文档能帮你写驱动，帮不了你查故障」。

---

## §2 方法

### §2.1 详读范围（一等证据的边界）

**一等证据 = 逐行读过、并且引用时回到源码逐字核对过**。

| 文件 | 行数 | 详读程度 |
|---|---|---|
| `drivers/clk/clk.c` | 5463 | **全文**。这是整套文档的主要来源（文档集里 284 处引用它） |
| `include/linux/clk-provider.h` | 1598 | **全文**（51 处引用） |
| `include/linux/clk.h` | 1161 | **全文**（28 处引用） |
| `drivers/clk/clkdev.c` | 381 | **全文**——§4.1 那条最重要的发现就在这里 |
| `include/trace/events/clk.h` | 270 | **全文**（07 §4 的 6 个事件类、20 个 `DEFINE_EVENT` 全部逐行核对） |
| `drivers/clk/clk-conf.c` | 152 | **全文**（`assigned-clock*` 的唯一消费者，06 §4 全篇靠它） |
| `drivers/clk/clk-bulk.c` | 248 | 全文 |
| `drivers/clk/clk.h` | 38 | 全文（核心内部头，只有几个跨文件声明） |
| `drivers/clk/clk-gate.c` | 259 | 全文（04 的最简样板） |
| `drivers/clk/clk-divider.c` | 689 | 全文（04 里最复杂的基础类型） |
| `drivers/clk/clk-mux.c` | 285 | 全文 |
| `drivers/clk/clk-fixed-rate.c` | 240 | 全文 |
| `drivers/clk/clk-fixed-factor.c` | 342 | 全文 |
| `drivers/clk/clk-composite.c` | 489 | 全文 |
| `drivers/clk/clk-fractional-divider.c` | 259 | 全文 |
| `drivers/clk/clk-multiplier.c` | 156 | 全文 |
| `drivers/clk/clk-devres.c` | 225 | 全文（`devm_*` 家族的真实实现） |
| `drivers/clk/Kconfig` | — | 详读 `COMMON_CLK` 及两个 KUnit 开关（`:476-486`） |

合计一等证据约 **12.6k 行**。

### §2.2 只读符号表 / 只读片段的部分（二等证据）

**二等证据 = 只看了符号、签名、或某几行片段，没有读上下文**。
引用它们时文档里都带了限定语（"某某驱动这么用"而不是"内核这样做"）。

| 范围 | 规模 | 详读了什么 |
|---|---|---|
| `drivers/clk/` 下的厂商驱动 | 870 个 `.c`，49 个子目录 | **只 grep，不读**。文档里点名的那几个（`qcom/common.c`、`rockchip/clk.c`、`imx/clk-composite-7ulp.c`、`mvebu/armada-37xx-tbg.c`、`sunxi/clk-a10-pll2.c`、`sunxi/clk-sun4i-pll3.c`）**只详读了被引用的那个函数** |
| `drivers/clk/clk_test.c` | 2411 | **只读 suite 名和 `kunit_suite` 定义**（12 个），没读任何测试用例的实现 |
| `drivers/clk/clk-gate_test.c` | 464 | 同上（5 个 suite） |
| `drivers/clk/clk-scmi.c` / `clk-scpi.c` | — | 只读到"它们是固件型提供方"这一层，寄存器/协议细节没读 |
| `drivers/clk/clk-gpio.c` / `clk-pwm.c` / `clk-si5341.c` / `clk-fsl-sai.c` / `clk-fixed-mmio.c` | — | 只作为"某类提供方存在"的存在性证据，各读 1 个函数 |
| `arch/arm64/boot/dts/rockchip/rk3399.dtsi` | — | 只读被引用的那几个节点（06 的举例来源） |
| `arch/arm64/boot/dts/freescale/imx8mp.dtsi`、`arch/arm64/boot/dts/arm/juno-base.dtsi` | — | 各读 1 个节点 |
| `drivers/of/platform.c` | — | 只读 `of_default_bus_match_table[]` 一处（回答 02 的"子节点会不会被继续下钻"） |
| `drivers/i2c/i2c-core-base.c` | — | 只读 `:699` 一行（§1.2 的对照物） |
| 消费者侧举例（`drivers/thermal/sun8i_thermal.c`、`drivers/phy/allwinner/phy-sun6i-mipi-dphy.c`、`drivers/clocksource/timer-sun5i.c`、`drivers/i2c/busses/i2c-{rk3x,nomadik,bcm2835}.c`） | — | 各只读被引用的那一个调用点 |

> **二等证据里最要小心的一条**：12 + 5 个 KUnit suite 的**名字和行号**是一等的
> （逐个 grep 确认），但"这些测试测什么"是我**从 suite 名推断**的
> （如"3 个测孤儿、3 个测频率范围"）。见 §5.3。

### §2.3 可复现的统计命令

⚠️ **本 shell 的 `grep` 是 ugrep 7.5.0 的包装（带 `-G`）**：不支持 `\s`，
且默认 honor `.gitignore`。所以下面凡是要 GNU 行为的地方都显式写
`/usr/bin/grep`（GNU 3.7）。你换 shell 重跑时数字才对得上。

**A. 规模（§2.1 那张表）**

```bash
wc -l drivers/clk/clk.c drivers/clk/clkdev.c drivers/clk/clk-bulk.c \
      drivers/clk/clk-divider.c drivers/clk/clk-gate.c drivers/clk/clk-mux.c \
      drivers/clk/clk-fixed-rate.c drivers/clk/clk-fixed-factor.c \
      drivers/clk/clk-composite.c drivers/clk/clk-fractional-divider.c \
      drivers/clk/clk-multiplier.c drivers/clk/clk-conf.c \
      drivers/clk/clk_test.c drivers/clk/clk-gate_test.c \
      include/linux/clk.h include/linux/clk-provider.h \
      include/trace/events/clk.h Documentation/driver-api/clk.rst
```

**B. 树规模（§2.2）**

```bash
ls drivers/clk/*.c   | wc -l      # → 73（核心 + 通用类型 + 独立芯片驱动）
ls -d drivers/clk/*/ | wc -l      # → 49（厂商子目录）
find drivers/clk -name '*.c' | wc -l   # → 870
```

**C. 导出接口数（01 的边界）**

```bash
/usr/bin/grep -c 'EXPORT_SYMBOL' drivers/clk/clk.c   # → 75
/usr/bin/grep -c '^static '      drivers/clk/clk.c   # → 165
```

**D. 错误码普查（07 §6 全篇的底座）**

```bash
for e in EINVAL ENOMEM ENOENT EBUSY EPROBE_DEFER EPERM EEXIST ESHUTDOWN \
         EOPNOTSUPP ENOSYS ERANGE EIO ENODEV; do
  printf '%-14s %s\n' "$e" "$(/usr/bin/grep -c -- "-$e" drivers/clk/clk.c)"
done
```

`drivers/clk/clk.c` 的结果（07 §6.1 那张表）：
EINVAL 30 / ENOMEM 14 / ENOENT 12 / EBUSY 6 / EPROBE_DEFER 3 /
EPERM 1 / EEXIST 1 / ESHUTDOWN 1，
而 **EOPNOTSUPP / ENOSYS / ERANGE / EIO / ENODEV 全部为 0**。
后面这五个是本套文档里最有用的"阴性结论"：**在 clk 上看到它们，
问题一定在提供方驱动或更下层，不在 clk 核心**。

**E. 日志与断言普查（07 §5）**

```bash
/usr/bin/grep -c 'pr_[a-z]*(' drivers/clk/clk.c              # → 40
/usr/bin/grep -c '_once\|_ratelimited' drivers/clk/clk.c      # → 0
/usr/bin/grep -c 'WARN('       drivers/clk/clk.c              # → 9
/usr/bin/grep -c 'WARN_ON('    drivers/clk/clk.c              # → 8
/usr/bin/grep -c 'WARN_ON_ONCE' drivers/clk/clk.c             # → 12
```

**`_once` / `_ratelimited` 为 0** 这条是 07 §5.1 的核心：
clk 的 `pr_*` **从不限流**，所以一条日志重复 1000 次就是真的发生了 1000 次；
而 clk 唯一的"只报一次"机制是 `WARN_ON_ONCE` 那 12 处（8 处是锁持有者断言）。

**F. tracepoint（07 §4）**

```bash
/usr/bin/grep -c 'DEFINE_EVENT'  include/trace/events/clk.h   # → 20
/usr/bin/grep -c 'DECLARE_EVENT_CLASS' include/trace/events/clk.h  # → 6
/usr/bin/grep -c 'TP_CONDITION' include/trace/events/clk.h    # → 0
/usr/bin/grep -c '_rcuidle'     drivers/clk/clk.c             # → 4
```

**`TP_CONDITION` 为 0** → clk 的 tracepoint **没有任何内建过滤**，
全开就是全量，只能靠 ftrace 的 `filter` 文件事后筛。

**G. 设计债（03 Part C）**

```bash
/usr/bin/grep -rn 'FIXME\|TODO\|XXX\|REVISIT' drivers/clk/clk.c \
    drivers/clk/clkdev.c include/linux/clk.h include/linux/clk-provider.h
```

四条，全部逐字读过，见 §6.2。

**H. arm64 DTS 的 clk 使用面（06 的取样底座）**

```bash
/usr/bin/grep -rln 'clocks *=' arch/arm64/boot/dts/ | wc -l   # → 501
```

501 个 arm64 dts/dtsi 文件带 `clocks =`。06 的举例只从其中 3 个取
（`rockchip/rk3399.dtsi`、`freescale/imx8mp.dtsi`、`arm/juno-base.dtsi`），
**取样理由见 §5.3 的第 2 条坦白**。

### §2.4 时间线次序的验证方法（03 Part A 的依据）

判据是**"谁调用谁"**，不是直觉，也不是函数在文件里的先后。
具体做法：对时间线上相邻的两步 X → Y，跑
`/usr/bin/grep -n 'Y(' drivers/clk/clk.c` 确认调用点落在 X 的函数体行号区间内。

clk 上这一步救回了两处：

1. `clk_core_reparent_orphans_nolock()` 是在 `__clk_core_init()` 的**末尾**
   （`drivers/clk/clk.c:3952`）被调的，不是在挂链之前——所以"新时钟先安顿自己，
   再去认领孤儿"这个次序是有行号支撑的。
2. `clk_disable_unused` 的**四个循环有固定顺序**
   （`drivers/clk/clk.c:1481-1491`）：先把根链表和孤儿链表都 `disable` 一遍，
   **再**各 `unprepare` 一遍。不是"每棵树 disable+unprepare 走完再下一棵"。
   这个顺序直接决定 08 §8 那段解释的对错。

### §2.5 全称断言的验证方法（skill 规则 19）

本套文档里的全称断言（"全树唯一"、"没有一个"、"只有 X 这么用"）逐条列出，
每条都给了跑法。**框架的使用者不住在框架自己的目录下**，所以一律全树 grep：

| 断言 | 出现在 | 验证命令 |
|---|---|---|
| clk 没有 `bus_type` | 01/02/08 §5 | `/usr/bin/grep -rn 'struct bus_type' drivers/clk/` → 空 |
| clk 没有字符设备/sysfs 类 | 07 §8 | `/usr/bin/grep -rn 'misc_register\|class_create\|cdev_add' drivers/clk/*.c` → 空 |
| `clk.c` 里没有任何 `_once`/`_ratelimited` 的 `pr_*` | 07 §5.1 | §2.3-E |
| `TP_CONDITION` 一次都没用 | 07 §4.1 | §2.3-F |
| `assigned-clock*` 的唯一消费者是 `clk-conf.c` | 06 §4 | `/usr/bin/grep -rln 'assigned-clock' drivers/ include/` |
| `-EOPNOTSUPP` 等五个错误码在 `clk.c` 里为 0 | 07 §6.1 | §2.3-D |
| `CLOCK_ALLOW_WRITE_DEBUGFS` 没有 Kconfig 项 | 07 §3.3 | `/usr/bin/grep -rn 'CLOCK_ALLOW_WRITE_DEBUGFS' .` → 只有 `drivers/clk/clk.c` 自己 |

### §2.6 索引工具的定位

用 cbm（codebase-memory-mcp）**只做导航**：找候选函数、看调用关系的大致形状。
**所有进文档的行号都用 `rg`/`grep` + `sed` 在真实树上重新确认过。**

在 clk 上，索引工具的两大盲区都兑现了：

- **宏**：`DEFINE_SHOW_ATTRIBUTE(clk_summary)` 这类展开出来的
  `clk_summary_open`/`clk_summary_fops` 在索引里查不到，07 §3 全靠 grep 宏名。
- **函数指针间接调用**：`core->ops->set_rate(...)` 这种调用，索引给不出
  "谁真的实现了它"。04 那些"哪个基础类型实现了哪些回调"的表，
  是逐个打开 `clk-*.c` 里的 `clk_ops` 初始化器数出来的。

### §2.7 arm64-only 裁掉了什么

按项目约定（用户明确要求）裁掉的内容，**显式记下来，避免被当成遗漏**：

| 裁掉的 | 为什么 | 影响哪一篇 |
|---|---|---|
| **ACPI 路径**（用户明确指定不要） | arm64 服务器上 clk 基本走 DT；ACPI 下时钟通常被固件隐藏，Linux 侧看不到 CCF 树 | 06 全篇只讲 DT，没有"ACPI 下怎么配"这一节 |
| x86 / powerpc / arm32 特有的时钟实现 | 目标架构单一 | 04 没有 x86 的 `clk-lpss` 之类；03 的初始化次序只按 arm64 的 `of_clk_init()` 讲 |
| `CONFIG_COMMON_CLK=n` 的桩实现 | arm64 defconfig 恒为 `y` | **部分保留**：01 §4 仍登记了 `clk.h` 里 `#else` 分支的返回值，因为"某个 API 悄悄什么都没做"是常见 bug 源 |
| 非 arm64 的 DTS 举例 | 读者要能 `git show` 出来对照 | 06 的例子全部取自 `arch/arm64/boot/dts/` |
| 时钟硬件的模拟细节（PLL 环路、抖动、相位噪声） | 超出内核代码能取证的范围 | 00 §2 的硬件背景只讲到"为什么 prepare 要能睡眠"这一层 |

> 裁 ACPI 的**代价**要说清楚：如果你手上是一块 ACPI 引导的 arm64 服务器，
> 本套文档的 06 整篇不适用，07 里 debugfs 那部分仍然适用（CCF 只要注册了就有 debugfs）。

---

## §3 逐篇取证表

每篇列**主要论断 → 出处**，以及**自审时改掉了什么**。
记号表示"这一条是本套文档里最容易被别人写错、也最值得你自己再确认的"。

### §3.1 `00-overview.md`（512 行）

| 论断 | 出处 |
|---|---|
| clk 是框架型子系统，控制器在 platform 总线上、时钟本身不在任何总线上 | §1.2 的两条 grep |
| 硬件背景：PLL 要锁相所以 prepare 必须能睡眠；门控是纯寄存器写所以 enable 不能睡眠 | `Documentation/driver-api/clk.rst:270-273` + `drivers/clk/clk.c:27-28` |
| 可重入锁是 clk 的签名设计 | `drivers/clk/clk.c:214-227`，rationale 在 `clk.rst:298-303` |
| 「启动到某一步就挂」的头号嫌疑是 `clk_disable_unused` | `drivers/clk/clk.c:1452-1458`、`:1460-1499`；参数说明 `clk.rst:255-262` |

**自审修正**：00 是**最先写**的一篇（clk 之后的子系统改成最后写，理由见
`output/PROGRESS.md` 的提速规则 5）。因此 00 里的浓缩内容都在后续篇目定稿后
回访过一次；行号在 07 定稿后又核对了一遍（`:1452-1458` 与 `:1460` 的分界）。

### §3.2 `01-api.md`（461 行）

| 论断 | 出处 |
|---|---|
| 75 个 `EXPORT_SYMBOL` 划出 clk 核心的对外边界 | §2.3-C |
| `clk_register()` 已废弃，新代码用 `clk_hw_register()` | `include/linux/clk-provider.h` 的两组声明；官方文档 `clk.rst:248` 还在推荐旧的 |
| `-EINVAL` 来自 `clk_hw_register` 的四条 ops 合法性规则 | `drivers/clk/clk.c:3807-3837` |
| arm64 恒 `CONFIG_COMMON_CLK=y`，但 `#else` 桩的返回值仍登记 | `include/linux/clk.h` 的 `#else` 分支 |

**自审修正**：**写 99 时改掉一处硬错**——原 §0.7 写着
`Documentation/devicetree/bindings/clock/clock-bindings.yaml` 是通用绑定所在，
**这个文件在本树里不存在**。详见 §4.4。

### §3.3 `02-datastructures.md`（447 行）

| 论断 | 出处 |
|---|---|
| `clk_core` / `clk` / `clk_hw` 三层的字段划分 | `drivers/clk/clk.c:60-97`（`clk_core`）、`:102-110`（`clk`）、`include/linux/clk-provider.h:324-328`（`clk_hw`） |
| 父指针不是 `clk_core **` 而是 `struct clk_parent_map *` | `drivers/clk/clk.c:52`（结构体）、`:69`（字段）官方文档这里是过时的，见 §6.1 |
| 两把锁 + 两个 owner + 两个 refcnt | `drivers/clk/clk.c:27-31`、`:214-232`、`:240-272` |
| `core->clks` 挂的是所有消费者 handle | `drivers/clk/clk.c:92`（字段）+ `:102-110` 的 `clks_node` |

**自审修正**：02 §3 一次纠了约 **20 处行号**——初稿凭记忆写字段行号，
逐条 `sed` 打开后发现偏移。这次教训直接产出了台账里的提速规则 1
（**开写前先用一次 Bash 把 `行号:符号` 全 dump 成台账**）。

### §3.4 `03-internals.md`（696 行）

| 论断 | 出处 |
|---|---|
| 注册路径 `__clk_core_init` 的三路挂链 | `drivers/clk/clk.c:3871-3880` |
| 孤儿认领发生在 `__clk_core_init` 末尾 | `drivers/clk/clk.c:3952`（调用点）、`:3730`（本体）；次序验证见 §2.4 |
| 调频三阶段（协商 / 通知 / 提交） | `drivers/clk/clk.c:2443-2468` |
| 批量关闭的四个循环及其顺序 | `drivers/clk/clk.c:1481-1491`、`:1499` |
| 设计债（FIXME/TODO/XXX）四条 | §2.3-G，逐条在 §6.2 |

**自审修正**：Part A 的时间线原本把"认领孤儿"排在"挂链"之前，用 §2.4 的
"谁调用谁"判据推翻。Part A 主流程表补上了 `clk_unregister` 拆除路径
（初稿只写了注册，违反 skill 规则的"必须含拆除路径"）。

### §3.5 `04-extensions.md`（1005 行）

| 论断 | 出处 |
|---|---|
| clk 没有 i2c 那种独立子框架，"扩展"指的是**基础时钟类型** | 篇首已声明；`drivers/clk/clk-{gate,divider,mux,composite,fixed-rate,fixed-factor,fractional-divider,multiplier}.c` |
| 每种类型实现了哪些 `clk_ops` 回调 | 逐个打开各文件的 `clk_ops` 初始化器数出来的，**不是索引给的**（§2.6） |
| `clk_composite` 是"把三个 ops 拼起来"而不是新硬件抽象 | `drivers/clk/clk-composite.c` 全文 |

**自审修正**：对比表里的对称符号（如 `clk_divider_ops` 与 `clk_divider_ro_ops`）
初稿只 grep 了一侧，导致一处描述反了。这条教训已回吐进 skill 方法论
（"对比表里的对称符号必须逐个 grep"）。

### §3.6 `05-usage.md`（744 行）

| 论断 | 出处 |
|---|---|
| clk 有**三个**角色：消费者、提供方、平台初始化代码 | 篇首枚举 |
| 消费者骨架的每个符号都在 6.1 存在 | 逐个全树 grep（skill 规则 20） |
| `devm_clk_get_optional` 会吞掉几乎所有 DTS 配错 | `drivers/clk/clkdev.c:100-112` + `include/linux/clk.h:1131-1139`；见 §4.1 |
| 提供方骨架用 `container_of` 拿私有结构 | `include/linux/clk-provider.h:324-328` + `drivers/clk/clk-gate.c` 的真实写法 |

**自审修正**：篇首补了"骨架未编译"声明（初稿只放在 99）。
`clk_bulk_get_optional` 与 `devm_clk_get_optional` 的差别（前者会 `dev_err`，
后者静默）是在 07 §6.3 定稿后回补进来的。

### §3.7 `06-devicetree.md`（713 行）

| 论断 | 出处 |
|---|---|
| 通用绑定已搬出内核树 | `Documentation/devicetree/bindings/clock/clock-bindings.txt`（全文 2 行） |
| §1.1 的属性总表是从消费代码反查的 | 每行都有 `drivers/clk/` 的行级出处 |
| `assigned-clock*` 的唯一消费者是 `clk-conf.c` | §2.5 |
| 父名拼错 → 永久孤儿 → 无任何日志 | `drivers/clk/clk.c:3871-3880`（挂链）+ `:3730`（认领）+ 缺失的日志（grep 无 `pr_*`） |

**自审修正**：**三处被 07 §6.3 推翻并回改**——原文说驱动会看到
`-EINVAL`，实际只会看到 `-ENOENT`。详见 §4.1。

### §3.8 `07-debug.md`（858 行）

| 论断 | 出处 |
|---|---|
| `hardware enable` 列是**三值**，其中一个 `Y` 是编出来的 | `drivers/clk/clk.c:3226-3232` |
| 无消费者的时钟**不换行**，按字段号 awk 不可用 | `drivers/clk/clk.c:3204-3243` |
| 写 debugfs 的能力故意不给 Kconfig | `drivers/clk/clk.c:3352-3358`（`#undef` + 注释原文） |
| 五个错误码在 `clk.c` 里为 0 | §2.3-D |
| `pr_*` 从不限流；只报一次只用在 `WARN_ON_ONCE` | §2.3-E |
| `TP_CONDITION` 为 0；enable/disable 用 `_rcuidle` 变体 | §2.3-F；`drivers/clk/clk.c:1139`/`:1144`/`:1198`/`:1203` |
| KUnit：12 + 5 个 suite | `drivers/clk/clk_test.c` / `clk-gate_test.c` 的 `kunit_suite` 定义；开关 `drivers/clk/Kconfig:476-486` |

**自审修正**：三处，全部在 §4 展开（§4.1 `-ENOENT`、§4.2 三值列、§4.3 换行陷阱）。
另修一处行号（`clk_pm_runtime_get_all` `:3268` → `:3266`）。

### §3.9 `08-feynman.md`（651 行）

| 主题 | 类比后面的代码出处 |
|---|---|
| §1 `clk` vs `clk_core` | `drivers/clk/clk.c:102-110`、`:60` |
| §2 `clk_hw` 的朝向 | `include/linux/clk-provider.h:324-328` |
| §3 prepare/enable 分离 | `drivers/clk/clk.c:27-28`、`:1188-1190`、`:1002`、`:1359-1362` |
| §4 两把锁 | `drivers/clk/clk.c:30-31`、`:214-232` |
| §5 没有 `bus_type` | §1.2 的两条 grep + `drivers/i2c/i2c-core-base.c:699` |
| §6 孤儿 | `drivers/clk/clk.c:36-37`、`:3871-3880`、`:3730`/`:3952` |
| §7 静默返回 0 | `drivers/clk/clk.c:2443-2447` |
| §8 `clk_disable_unused` | `drivers/clk/clk.c:1460-1499` |
| §9 `protect_count` | `drivers/clk/clk.c:901`、`:922-932`、`:967`、`:1072-1080` |

**自审修正**：按 skill 规则，每个主题都必须能推出一个**真实的**"类比误导点"——
§7 那个"地铁没有油门"的类比原本推不出误导点（说明类比太泛），
补上"把它改成报错会破坏一大批正常代码"和"这不是唯一的静默返回 0"两条后才成立。

---

## §4 交叉验证实例：初始假设被推翻的地方

这一节是本篇里最该读的部分。**下面每一条都是我先写错、然后被源码推翻的。**

### §4.1 驱动看不到 `-EINVAL`——`clk_get` 会把 DT 错误**丢掉**

**初始假设**（写在 06 三处）：DTS 里 `clocks` 写错，`of_parse_clkspec` 返回
`-EINVAL`，驱动的 `devm_clk_get` 就能看到 `-EINVAL`。

**推翻它的代码**（`drivers/clk/clkdev.c:100-112`，逐字）：

```c
struct clk *clk_get(struct device *dev, const char *con_id)
{
	const char *dev_id = dev ? dev_name(dev) : NULL;
	struct clk_hw *hw;

	if (dev && dev->of_node) {
		hw = of_clk_get_hw(dev->of_node, 0, con_id);
		if (!IS_ERR(hw) || PTR_ERR(hw) == -EPROBE_DEFER)
			return clk_hw_create_clk(dev, hw, dev_id, con_id);
	}

	return __clk_get_sys(dev, dev_id, con_id);
}
```

读 `:107` 那个 `if`：**只有成功、或者 `-EPROBE_DEFER`，DT 的结果才被采用**。
其余一切错误码——包括 `-EINVAL`——被**默默丢掉**，退回 `__clk_get_sys()`
按名字查 clkdev 表，查不到就返回 `-ENOENT`（`drivers/clk/clkdev.c:75`）。

后果分两路：

| 你调的是 | 看到的错误码 |
|---|---|
| `clk_get` / `devm_clk_get` / `devm_clk_get_optional*` / `clk_bulk_get*` | **只有 `-ENOENT`**（和 `-EPROBE_DEFER`） |
| `of_clk_get_by_name` / `of_clk_get` / `of_clk_get_hw` | 原始错误码（含 `-EINVAL`） |

再往下一层更难受：`clk_get_optional`（`include/linux/clk.h:1131-1139`）
**只吞 `-ENOENT`**——而上面刚证明了 DTS 配错最终**就是** `-ENOENT`。
所以 `devm_clk_get_optional` 会把几乎所有 DTS 配错静默吞掉，
驱动一路 probe 成功，然后在跑起来之后才崩。

**做了什么**：回改 `06-devicetree.md` **三处**（§3.3 第 2 点、§5.2 第 2 点、
§7.3 决策树），每处都加了指向 `07-debug.md` §6.3 的前向引用。

> 这条的方法论意义：它是在写 07（调试篇）时才浮出来的，
> 而它推翻的是一篇**已经"写完"的** 06。所以 skill 规则 17
> （跨文档一致性）不能只在收尾跑一次——**每发现一个新机制就要跑一次**。

### §4.2 `clk_summary` 的 `hardware enable` 列是**三值**，不是两值

**初始假设**：那一列是 `Y`/`N`，读寄存器得来。初稿写了一张四行判断表。

**推翻它的代码**（`drivers/clk/clk.c:3226-3232`，逐字）：

```c
	if (c->ops->is_enabled)
		seq_printf(s, " %5c ", clk_core_is_enabled(c) ? 'Y' : 'N');
	else if (!c->ops->enable)
		seq_printf(s, " %5c ", 'Y');
	else
		seq_printf(s, " %5c ", '?');
```

三条分支 = 三种可信度：

| 显示 | 含义 | 可信吗 |
|---|---|---|
| `Y` / `N`（有 `.is_enabled`） | 真读了硬件 | 可信 |
| `Y`（无 `.is_enabled` 且无 `.enable`） | **编出来的**——核心假设"没法关的时钟一定是开着的" | 不可信 |
| `?` | 有 `.enable` 但没法回读 | 未知 |

**做了什么**：07 §3.2 换成逐字引用 + 一张三行可信度表 + 一张六行
（`enable count` × `hardware enable`）判断表。
08 §3 也补了一句：这个"编出来的 `Y`"的判据就是
`clk_is_enabled_when_prepared`（`drivers/clk/clk.c:1359-1362`）。

### §4.3 `clk_summary` 对**没有消费者的时钟不换行**——我写的 awk 命令是错的

**初始假设**：`clk_summary` 是规整的表格，可以
`awk 'NR>3 && $2==0 && $9=="Y"'` 找出"没人用但硬件开着"的时钟。

**推翻它的代码**：`clk_summary_show_one`（`drivers/clk/clk.c:3204-3243`）里，
`\n` **只出现在消费者循环内**：

```c
	hlist_for_each_entry(clk_user, &c->clks, clks_node) {
		seq_printf(s, "%*s%-*s  %-25s\n", ...);
		multi_node = 1;
	}
```

于是一个**没有任何消费者**（没人 `clk_get` 过）的时钟，
打印完自己那一行后**不换行**，下一个时钟直接接在同一文本行上。

两个后果：(1) 按字段号 awk 完全不可用——而"没人用"恰好就是这条命令要找的目标；
(2) `clk_orphan_summary` 里孤儿大多没有消费者，整份文件会塌成一行。

**做了什么**：把那条 awk 换成基于固定字符列的 `grep -o`，
并老实写上「实践中更可靠的做法是直接翻这份文件，人眼找"count 列是 0、Y 在最后"的行」。
新增 07 §3.2.1 专讲这个陷阱，并说明 `clk_dump` **不能**替代
（它没有 `hardware enable` 字段，`drivers/clk/clk.c:3283`）。
按 skill 方法论 §D，**没有编任何示例输出**。

### §4.4 `clock-bindings.yaml` 不存在——一条在写 99 时才抓到的硬错

**初始假设**（写在 `01-api.md` §0.7）：通用绑定在
`Documentation/devicetree/bindings/clock/clock-bindings.yaml`。

**推翻它的命令**：

```bash
$ ls Documentation/devicetree/bindings/clock/clock-bindings.*
Documentation/devicetree/bindings/clock/clock-bindings.txt
```

**只有 `.txt`，而且只有 2 行**，内容是指向树外 `dt-schema` 仓库的 URL。

讽刺的是：`06-devicetree.md` 的篇首**已经把这件事写对了**（甚至当成
"版本时效警告"放在最前面），而 01 §0.7 那一格还留着错的文件名。

> 这条的方法论意义：**跨篇一致性最容易漏的不是结论，是"证据文件的名字"**。
> 01 §0.7 是一张"文件清单"，它不承载结论，所以逐篇自审时注意力没落在它上面。
> 教训：**清单类表格要单独验一遍每一行的文件真的存在**（`ls` 一遍即可）。

**做了什么**：改掉 01 §0.7 那一格，并把"本树里没有通用属性清单"这个事实
和 06 篇首的警告对齐。

### §4.5 `CLOCK_ALLOW_WRITE_DEBUGFS` 不是"没打开的 Kconfig"，是**故意不给 Kconfig**

**初始假设**：这是个 `CONFIG_` 开关，`.config` 里打开就能写 debugfs 改频率。

**推翻它的代码**（`drivers/clk/clk.c:3352-3358`）：

```c
#undef CLOCK_ALLOW_WRITE_DEBUGFS
#ifdef CLOCK_ALLOW_WRITE_DEBUGFS
/*
 * This can be dangerous, therefore don't provide any real compile time
 * configuration option for this feature.
 * People who want to use this will need to modify the source code directly.
 */
```

第一行是 `#undef`——**紧跟在它后面的 `#ifdef` 永远为假**。
注释把意图写得毫不含糊：**故意不提供编译期配置项，要用就自己改源码**。

**做了什么**：07 §3.3 不再写"打开某个 CONFIG"，改成引用这段注释原文，
并明确"这四段写接口（`:3518`/`:3616`/`:3684` 及 `:3353`）在任何 defconfig 下都是死代码"。
这也是 skill 方法论 §D「调试开关的多道门」那一条检查动作真正兑现的一次。

### §4.6 官方文档的 `.init` 回调返回类型是**过时的** `void`

**初始假设**：照抄 `clk.rst` 里的 `struct clk_ops` 原型没风险。

**推翻它的对照**：`clk.rst:98`（文档）写

```c
		void		(*init)(struct clk_hw *hw);
```

而 6.1 的真实定义（`include/linux/clk-provider.h:264`）是

```c
	int		(*init)(struct clk_hw *hw);
```

这是**返回类型**变了，是最危险的一类过时：一个照文档写 `void .init`
的提供方驱动，编译期就会报类型不匹配（还算走运），
而一个照文档**理解**的人会以为 `.init` 没有失败路径。

**做了什么**：01 §4 和 04 的回调表一律按 `include/linux/clk-provider.h:232-266`
的真实原型写；这条以及另外两条 `clk.rst` 过时项列在 §6.1。

---

## §5 明确未验证项（四层分层）

这一节的价值不低于前面九篇——**它决定你该在哪里加倍小心**。

### §5.1 第一层：未编译、未运行

> **这套文档全程没有编译过一行代码，没有在任何硬件或模拟器上运行过。**

具体到条目：

| 内容 | 状态 |
|---|---|
| `05-usage.md` 里的三份代码骨架（消费者 / 提供方 / 平台初始化） | **从未编译**。符号存在性逐个 grep 过，但组合起来能不能编过、能不能跑，未知 |
| 07 §2、§3、§4、§7 里的所有 shell 命令 | **从未在真机上跑过**。命令的**语义**依据是源码（哪个文件存在、字段是什么），但**输出格式**没有实测样本 |
| **所有示例输出** | 按 skill 方法论 §D「示例命令输出不许编」——**本套文档没有编任何一份示例输出**。凡是需要展示格式的地方，一律改成引用 `seq_printf` 的格式串（如 `drivers/clk/clk.c:3204-3209` 的 `%-7d %-8d %-8d %-11lu %-10lu`），让你自己按字段宽度对照 |
| 17 个 KUnit suite | **没编译、没运行**。07 §7.2 的 `kunit.py run --kunitconfig=drivers/clk` 只验证了 `drivers/clk/.kunitconfig` **文件存在**，没验证它能跑通 |
| `clk_ignore_unused` 的实际效果 | 只读了代码路径（`drivers/clk/clk.c:1465-1468`）和参数文档，没有在板子上加过这个参数 |

### §5.2 第二层：只读了符号表 / 只读了片段

见 §2.2 的完整表。这里只强调**三条最容易被误当成一等证据**的：

1. **870 个 `.c` 里的 864 个我没读**。凡是关于"厂商驱动一般怎么做"的话，
   都是从被点名的那几个文件外推的。04 §0.2 那条"arm64 大厂根本不用基本类型的 ops 表"
   是**统计**（grep 计数）支撑的，不是通读支撑的——统计能证明"没调这个函数"，
   不能证明"它们改用了什么"。
2. **KUnit suite 测什么是推断**。suite 名（如 `clk-orphan-transparent-multiple-parent-mux-test`）
   和行号是 grep 出来的一等证据；"这 3 个在测孤儿"是我从名字读出来的。
   如果你要靠它验证某个行为，**打开测试用例自己看**。
3. `drivers/clk/clk-scmi.c` / `clk-scpi.c` 我只读到"固件型提供方 + 两张 ops 表"这一层。
   SCMI 协议本身、传输层、原子模式的实际时序**都没读**。

### §5.3 第三层：推断而非观察

这一层最危险，因为它读起来和一等证据一样自信。逐条坦白：

| 推断 | 出现在 | 为什么只能算推断 |
|---|---|---|
| 06 §1.1 的属性总表「完整」 | 06 | 它是从消费代码 grep 出来的，**grep 只能找到我想到要搜的属性名**。本树里没有权威清单可以对照（§1.3），所以**这张表可能漏**。判据：如果你在 DTS 里见到一个本表没有的 clk 相关属性，先 `grep -rn '"属性名"' drivers/clk/` 自己确认 |
| 「父名拼错 → 永久孤儿 → 无任何日志」 | 06 §7.2、08 §6 | 代码路径是确定的（挂链 + 认领 + 没有 `pr_*`），但"永久"这个词是推断——理论上一个后来注册的模块可能刚好提供了那个名字 |
| 「`clk_disable_unused` 是 arm64 启动挂死的头号嫌疑」 | 00、07 §2.1、08 §8 | 这是**经验性判断**，不是从本树统计出来的。代码机制确定，"头号"这个排序没有数据支撑 |
| 「PLL 锁相需要几十微秒到毫秒级」 | 00 §2、08 §3 | 硬件常识，本树代码里没有这个数字。它解释了"prepare 为什么要能睡眠"的动机，但具体量级你要查芯片手册 |
| 一等证据合计「约 12.6k 行」 | §2.1 | `wc -l` 的和是精确的，但"逐行读过"这件事本身无法被第三方验证 |
| 07 §8 那张 13 行症状速查表的"第一动作" | 07 | 每一行的**机制**都有行号，但"遇到这个症状应该先看那个文件"是**排序建议**，不是代码事实 |

### §5.4 第四层：版本时效

| 事项 | 时效风险 |
|---|---|
| 所有行号 | 绑定 **6.1.158**。6.1.y 内部的小版本可能偏移几行；跨大版本（6.6/6.12）clk 核心变动明显（如 `clk_rate_request` 的演进），行号一律失效 |
| 通用 DT 绑定已出树 | 见 §1.3。**未来版本里这个文件可能连那 2 行都不剩** |
| `Documentation/driver-api/clk.rst` 的三处过时 | §6.1。这些是**文档落后于代码**，未来可能被修好，届时 §6.1 就该删 |
| `drivers/clk/` 下的 49 个厂商目录 | 增长很快，§2.3-B 的三个计数（73 / 49 / 870）每个版本都会变 |
| `clk-conf.c` 只有 152 行 | `assigned-clock*` 的能力在后续版本有扩展（如 `assigned-clock-rates-u64`），**本篇的属性表按 6.1 写死** |

### §5.5 不适用项（本套文档没有的东西，不是遗漏）

- **没有性能数据**：没测过 `clk_set_rate` 的耗时、没测过 `clk_summary` 在大树上的开销。
- **没有 ACPI 内容**：见 §2.7，用户明确要求裁掉。
- **没有非 arm64 架构对照**：见 §2.7。
- **没有 `sunxi-ng` / `qcom` / `rockchip` 这些大型 CCU 抽象的展开**：
  它们各自是一套框架（`drivers/clk/sunxi-ng/` 单独有 30+ 文件），
  展开任何一个都够写另一套 10 篇。04 §0.2 只是**指出它们存在并且是主流**。

---

## §6 已发现的问题清单

### §6.1 官方文档 vs 代码（三条，全部以代码为准）

`Documentation/driver-api/clk.rst`（307 行）里三处已落后于 6.1 代码：

| # | 位置 | 文档写的 | 6.1 代码的事实 | 危害等级 |
|---|---|---|---|---|
| ① | `Documentation/driver-api/clk.rst:248` | 示例用 `clk_register(...)` | `clk_register()` 已废弃，应用 `clk_hw_register()`（`include/linux/clk-provider.h` 的两组声明） | 高——照抄会写出返回 `struct clk *` 的过时提供方 |
| ② | `Documentation/driver-api/clk.rst:47-58` 的 `struct clk_core` 摘要 | `const char **parent_names;` + `struct clk_core **parents;` | 6.1 的 `clk_core` **没有 `parent_names` 字段**，`parents` 的类型是 `struct clk_parent_map *`（`drivers/clk/clk.c:69`，结构体定义在 `:52`） | 中——影响你理解"父关系是线索不是指针"（03 §A2.1 讲这个） |
| ③ | `Documentation/driver-api/clk.rst:98` 的 `clk_ops` 摘要 | `void (*init)(struct clk_hw *hw);`，且整张表**缺** `.save_context`/`.restore_context`/`.get_duty_cycle`/`.set_duty_cycle`/`.terminate` | 6.1 是 `int (*init)`（`include/linux/clk-provider.h:264`），缺的五个分别在 `:241`/`:242`/`:260`/`:262`/`:265` | 高——**返回类型变了**。照文档理解会以为 `.init` 没有失败路径 |

> 文档自己声明是"modified for brevity"（`clk.rst:45`），所以字段缺失可以理解；
> 但 ① 的过时 API 和 ③ 的**返回类型**不属于"为简洁而删"，是真的旧。

另外一条不算"冲突"但值得记：`clk.rst` **完全不覆盖调试**（§1.3 的 grep），
所以 07 整篇没有官方背书。

### §6.2 代码层面的设计债（四条，源码自陈）

全部来自 §2.3-G 的 grep，逐字读过：

| # | 位置 | 内容 | 我在文档里怎么处理 |
|---|---|---|---|
| ④ | `drivers/clk/clk.c:2622-2629` | `FIXME:` 承认 `clk_set_rate_range` 的 `clamp` 之后那次 `clk_core_set_rate_nolock` **可能因为 `round_rate()` 落到边界错的一侧、或 `determine_rate()` 没检查这个角落而失败** | 03 Part C 逐字引用；06 的范围属性一节据此提醒"设了范围不等于生效" |
| ⑤ | `drivers/clk/clk.c:2720` | `/* TODO: Create a per-user clk and change callers to call clk_put */` —— `clk_get_parent()` 返回的是 `parent->hw->clk`，**不是给调用者的独立 handle** | 05 明确写了"`clk_get_parent()` 的返回值不要 `clk_put`"，出处就是这条 TODO |
| ⑥ | `drivers/clk/clk.c:4729` | `/* XXX the notifier code should handle this better */` —— 在 `clk_notifier_unregister` 附近 | 03 Part C 记录；未展开（属于 §5.2 的只读片段） |
| ⑦ | `include/linux/clk-provider.h:1351` | `/* FIXME clock api without lock protection */` 挂在 `clk_hw_round_rate()` 上 | 04 和 05 都明确标注：`clk_hw_round_rate()` **不带锁**，提供方在回调里调它要自己确认锁状态 |

### §6.3 保留结论（不要在任何篇目里推翻）

- **写 debugfs 的能力不是"没开的 Kconfig"，是故意不给 Kconfig**
  （`drivers/clk/clk.c:3352-3358` 的 `#undef` + 注释）。
  任何时候看到"打开 `CLOCK_ALLOW_WRITE_DEBUGFS` 就能写"的说法都是错的——
  它必须改源码重编。
- **驱动通过 `clk_get` 家族拿到的 DTS 配错一律是 `-ENOENT`**，不是 `-EINVAL`
  （§4.1）。06 已回改三处，不要改回去。
- **`clk_summary` 的 `hardware enable` 是三值，其中一个 `Y` 是编出来的**（§4.2）。
- **`clock-bindings.yaml` 在本树不存在**（§4.4）。

---

## §7 批量自检脚本的结果

### §7.1 `check_xrefs.py`（跨篇 `0X §Y` 引用）

```bash
python3 ~/.claude/skills/kernel-subsystem-docs/scripts/check_xrefs.py output/clk
```

**首轮结果：198 条命中 / 12 条失效。** 12 条分两类：

| 类别 | 条数 | 处置 |
|---|---|---|
| 指向本篇（99）还没写的 `§5`/`§6`/`§6.1`/`§7` | 6 | 写完本篇后自动消失（`01-api.md:92`/`:236`、`05-usage.md:5`/`:744`、`07-debug.md:794`/`:843`） |
| **真的错了** | 6 | 已逐条改，见下 |

真的错了的 6 条：

| 位置 | 原来写的 | 改成 | 为什么错 |
|---|---|---|---|
| `00-overview.md:328` | `04 §1.3` | `02 §4.2` | ops 组合约束在 02 §4.2「组合约束（注册时会校验）」，04 §1.3 不存在 |
| `00-overview.md:473` | `07 §1.3` | `07 §2.1` | `clk_ignore_unused` 的分诊流程在 07 §2.1（07 §1 只到 §1.2） |
| `02-datastructures.md:293` | `07 §1.3` | `07 §2.1` | 同上 |
| `03-internals.md:480` | `07 §1.3` | `07 §2.1` | 同上 |
| `04-extensions.md:548` | `02-datastructures.md §A2` | `03-internals.md §A2` | **篇名错了**：`init.ops` 是指针拷贝这条规则在 `03-internals.md` §A2（`:127` 那一行表格），02 里根本没有 §A2 |
| `04-extensions.md:853` | 同上 | 同上 | 同上 |

> 最后两条值得单独说：`§A2` 这个编号**在 03 里是存在的**，
> 所以人眼读起来完全不别扭——错的是**篇名**。
> 这正是脚本比手工回访强的地方：手工回访会去 03 里找到 §A2、觉得没问题就过了。

**复跑（改完 + 本篇写完 + 吸收 §9 的三条之后）**：

```
文档 10 篇；跨篇引用命中 235 条，失效 0 条（忽略项 5 条）
干净：所有跨篇引用都指向真实存在的章节。
```

那 5 条忽略项登记在 `output/clk/.xref-ignore` 里，**全部来自上面这张修正记录表本身**——
表里"原来写的"一列逐字保留了改之前的错误引用（`04 §1.3` / `07 §1.3` / `02 §A2`），
脚本无法区分"正在使用的引用"和"被引号括起来展示的历史错误"。
这是这个脚本**结构性的**假阳性：**任何一篇忠实记录了自己修正历史的取证篇都会踩到。**
处理原则按 skill 方法论 §B：不改文档去迁就脚本，登记在 `.xref-ignore` 并写明判据
（这些字符串只出现在 §7.1 这张表里，正文其余位置已全部指向正确目标）。

### §7.2 `check_line_refs.py`（`路径:行号` 是否越界）

```bash
python3 ~/.claude/skills/kernel-subsystem-docs/scripts/check_line_refs.py output/clk .
```

**结果：395 条唯一 `file:line` 引用，无 UNRESOLVED，无越界行号。**

本套文档**没有使用 `.lineref-ignore`**（忽略项 0 条）。

残留 2 条 AMBIGUOUS，两条都**不是错**，但必须在这里登记（脚本只能提醒，判定得人做）：

| 简写基名 | 脚本按哪个校验 | 树里的其他候选 | 人工普查结论 |
|---|---|---|---|
| `clk.c` | `drivers/clk/clk.c` | `drivers/clk/rockchip/clk.c` | 正文里**每一处**裸写 `clk.c:` 的都指 `drivers/clk/clk.c`；引用 rockchip 那个时一律写全路径 |
| `clk.h` | `include/linux/clk.h` | `include/trace/events/clk.h` | 正文里**每一处**裸写 `clk.h:` 的都指 `include/linux/clk.h`；引用 tracepoint 头时一律写全路径（07 §4 全节如此） |

> 关于这个脚本的**两个必须知道的边界**（都在 clk 这一轮踩实了）：
>
> 1. **AMBIGUOUS 只在"文档自己写过 ≥2 个同名全路径"时才触发**，脚本不扫树。
>    所以"干净"≠"没有同名文件风险"，每套文档都得自己做一次同名基名普查——
>    这就是本节存在的理由。
> 2. **`--ignore` 只对越界行号生效**（脚本 `:172`，在
>    `if hi > total or start < 1:` 分支内），**压不掉 UNRESOLVED**。
>    另外它的分词器会把含逗号的路径切错（`arm,pl172.txt` 后面接行号时，基名会被切成逗号右边那半截），
>    所以**含逗号的文件名只能不带行号引用**。本套文档因此没有给
>    `Documentation/devicetree/bindings/memory-controllers/ti-aemif.txt` 之外的
>    逗号路径配行号。

### §7.3 `check_doc_coverage.sh`（官方文档反向覆盖率）

```bash
bash ~/.claude/skills/kernel-subsystem-docs/scripts/check_doc_coverage.sh clk output/clk .
```

**结果：必查档 1 篇（`driver-api/clk`），被引用 17 次，0 引用的篇数为 0。全绿。**

clk 是这套 skill 用过的子系统里**官方文档最少的一个**——只有 1 篇框架级文档。
对比 gpio/pinctrl 动辄 5–8 篇必查档，clk 的 307 行 `clk.rst` 就是全部。
所以本套文档的**三等证据占比极低**，绝大部分论断直接落在代码上。
这既是好事（不依赖可能过时的文档），也是坏事（**没有第二个信源可以交叉验证设计意图**，
凡是"为什么这么设计"的话都只能从代码结构反推）。

### §7.4 工具与复现环境（本节决定你重跑我的命令能不能得到同一个数）

| 工具 | 版本 / 路径 | 陷阱 |
|---|---|---|
| `grep`（shell 里的） | **ugrep 7.5.0 的包装**（带 `-G`） | **不支持 `\s`**；**默认 honor `.gitignore`**。用它跑本篇的统计命令会得到不同的数 |
| `grep`（GNU） | `/usr/bin/grep` 3.7 | 本篇所有统计命令都显式写这个路径 |
| POSIX 字符类 | — | 需要匹配空白时一律写 `[[:space:]]`，不写 `\s` |
| `python3` | 系统自带，脚本纯 stdlib 无外部依赖 | — |
| 三个自检脚本 | `~/.claude/skills/kernel-subsystem-docs/scripts/` | 见 §7.1–§7.3 各自的边界 |

---

## §8 本文档集的读法建议

| 你的处境 | 读法 |
|---|---|
| 第一次接触 clk | `00` → `08`（费曼）→ `02` → `03`。**先建直觉再看结构** |
| 隔几个月回来复习 | **只读 `00`**。它是为这个场景写的 |
| 要写一个消费者驱动 | `01` §1 → `05` §2 → `06` §2。顺手读 §4.1（`-ENOENT` 那条），能省你一天 |
| 要写一个提供方驱动 | `01` §2 → `02` §4 → `04` → `05` §3。必读 §6.1 的三条文档过时项 |
| 板子起不来 / 挂在启动 | **直接 `07` §2.1**，然后 `07` §8 的症状表 |
| `clk_set_rate` 不起作用 | `08` §7 → `07` §3.4 |
| DTS 配了但没生效 | `06` §7.3 的决策树 |
| 想判断某条结论能不能直接用 | **本篇 §2.1（一等证据表）+ §5.3（推断项）** |

---

## §9 与另一套文档集的对照（skill 规则 29）

对照对象：`output/qoder/clk/`，同样 10 篇、同样 6.1.158、同样 arm64-only，
由另一个工具在同一棵树上独立产出。规模对比：

| | 本套 | 对照套 |
|---|---|---|
| 总行数 | **6963** | 4537 |
| 最厚的一篇 | `04-extensions.md` 1005 行 | `08-feynman.md` 560 行 |
| 最薄的一篇 | `02-datastructures.md` 447 行 | `99-evidence.md` 298 行 |

对照方法：先读对方 10 篇的全部章节标题建结构差异表，再对**结论有分歧**
和**只有对方有**的条目逐条回源码验证。**不比篇幅、不比措辞，只比"谁说的是对的"
和"谁少说了一件真事"。**

> 排版约定：本节引用**对照套**的章节时一律写成「对照套 NN 的 §X」（篇号和 `§` 之间加"的"），
> 引用**本套**时才写「NN §X」。原因是 `check_xrefs.py` 会把「NN §X」当成指向本套的引用去校验，
> 而对照套的章节号在本套里并不存在——**加一个"的"就能让脚本正确跳过，不必登记忽略项。**

### §9.1 吸收的 3 条

| # | 从哪来 | 吸收到哪 | 内容 | 为什么值得吸收 |
|---|---|---|---|---|
| 1 | 对照套 07 §4b | 本套新增 **07 §7.3** | **clk 没有任何故障注入机制**（`fault_inject`/`should_fail`/`DECLARE_FAULT_ATTR` 在 `drivers/clk/` 全为 0），以及三条替代手段 | 这是本套的**真实遗漏**：skill 的 07 模板明确要求写"故障注入"，我漏了。而且这个"没有"是有信息量的——它把 07 §7 标题里的"唯一能在本机验证"从修辞变成了字面事实 |
| 2 | 对照套 07 的 §2.3（取其反面） | 本套新增 **07 §4.2.1** + 修正 07 §8 速查表一行 | 「entry 有、`_complete` 没有 ⇒ 中途失败」这个推断**在 6.1 里是错的**，四处提交代码的两个打点之间都没有提前退出；真正有诊断价值的是"连 entry 都没有" | 见 §9.3——对方这条是**硬错**，但正是因为去验它，才发现我自己 07 §8 速查表里也写了同样的错误建议 |
| 3 | 顺着第 2 条挖出来 | 本套新增 **08 §7 第三个误导点** + 并入 07 §4.2.1 | `clk_change_rate` 是 `static void`（`drivers/clk/clk.c:2318`），`.set_rate` 的返回值在 `:2367-2368` **裸调用、不接返回值**，`:2473` 也不检查 → **`clk_set_rate()` 返回 0 只证明协商通过，不证明寄存器写成功** | 这是**两套都没有**的事实，也是本轮对照的最大收获。它给 08 §7"必须回读"这条建议补上了第二个、更硬的理由 |

> 第 3 条说明了对照的真正价值：**它不在于抄对方的正确内容，
> 而在于"为了判定对方那条是对是错，你必须把这段代码重读一遍"。**
> 重读时发现的东西可能两套都没写。

### §9.2 谢绝的 5 条（写明理由）

| # | 对方有什么 | 不吸收的理由 |
|---|---|---|
| 1 | 01 的 §5「导出符号统计」逐文件表，合计写 **`~142`** | 违反本套的硬规则「所有数字都要能一条命令重跑」（§2.3）。一个带 `~` 的合计数是无法复核的；本套 §2.3-C 只写了能重跑的那两个数（`clk.c` 的 `EXPORT_SYMBOL` 75、`^static ` 165），逐文件拆分对读者没有决策价值 |
| 2 | 04 用「一节一个时钟类型」的均匀结构，每节固定四个小标题 | 结构本身是好的，但本套 04 已经做了更细的事：除 8 种基本类型外还覆盖 clkdev / bulk / devres / conf / gpio / pwm / **SCMI / SCPI** / KUnit 共 17 节，并给出 §0.2 那条**推翻直觉的统计**（arm64 大厂根本不用基本类型的 ops 表）。换成均匀结构会丢掉"哪几节在 arm64 上真会碰到"的分诊 |
| 3 | 07 §5 提到"Qualcomm 的 `clock_debug` 脚本" | **树外、不可验证**。本套 07 §8 的判据是"在这棵树上 grep 得到的可观测面"，引入一个无法用命令确认存在的厂商工具会污染这个判据 |
| 4 | 06 的 §3.6 空 phandle、§3.3 `rates` 为 0 | **本套已有且更完整**：`06-devicetree.md` 已写「`assigned-clock-rates` 里的 0 是"跳过"，不是"设成 0Hz"」和「空 phandle（`<0>`）也是跳过，两个函数里都有」，并给出错位后果。不重复 |
| 5 | 02 的结构体标题里直接写 `（clk.c:60-97）` | 与本套的格式约定冲突：行号集中在 03 和 99 里维护（skill 规则 10），标题里带行号会在每次代码变动时产生大量需要同步的点。本套的做法是正文引用带行号、标题不带 |

### §9.3 查出对照套的 1 处硬错

> **对照套 07 的 §2.3 第 4 条：**
> 「没有 error-code 字段：tracepoint 只在操作**入口/出口**触发，失败路径不会触发
> `_complete` 事件。因此若只看到 `clk_set_rate` 而没有 `clk_set_rate_complete`，
> 说明操作中途失败返回。」

**这条是错的。** 逐字读四处提交代码（细节见本套 07 §4.2.1 的表）：

- `drivers/clk/clk.c:2366` → `:2367-2368` → `:2371`：两个打点之间只有一句裸调用，**无分支、无 return**
- `:2348` → `:2349-2356` → `:2359`：同样无条件
- `:2106` → `:2110` → `:2112`：`ret` 确实被接住了，但 **`_complete` 在 `:2112` 先打，错误处理在 `:2114` 之后**
- `:2898` → `:2900-2903` → `:2906`：同样无条件

结论：**entry 触发了 `_complete` 就一定触发**，"配对缺失"这个信号在 6.1 的 clk 上
**不可能出现**。按对方的说法去排查，会得到"每次都配对，所以从来没失败"的假安心——
而真实情况恰恰相反：**提交阶段的失败连返回值通道都没有**（§9.1 第 3 条）。

这条错误的成因值得记下来：它是**从"入口/出口成对"这个正确观察推出来的合理猜测**，
而不是从代码读出来的。这正好落在 skill 规则 19 那条规律上——
**"写了概述、没有逐字引用源码"的段落是错误的集中地**。
判据不是"我理解得对不对"，而是"我有没有把源码原样搬过来"。

### §9.4 回吐给 skill / 方法论的 2 条

| # | 条目 | 依据 |
|---|---|---|
| 1 | **成对 tracepoint 的"配对缺失"不能当失败信号用，除非逐字确认两个打点之间存在提前退出路径。** 检查动作：`sed` 打开 entry 和 `_complete` 之间的全部行，找 `return` / `goto` / `break`；一行都没有就说明这个信号不存在 | 本轮 §9.3。这是个**可迁移**的检查动作——任何有 `X` / `X_complete` 打点的子系统（clk / regulator / cpufreq 都有）都要走一遍 |
| 2 | **07 篇的"故障注入"一节即使结论是"没有"也必须写。** "没有"本身是结论，而且它决定了读者能不能测错误路径；省掉这一节会让读者以为是遗漏 | 本轮 §9.1 第 1 条：本套漏了，对照套有 |

### §9.5 对照结论

两套在**事实层面高度一致**——没有出现任何一条互相矛盾的行号或返回值。
差异集中在两处：

1. **覆盖广度**：本套 04（17 节，含 SCMI/SCPI/KUnit）和 07（错误码分诊 + 日志三档 + `_rcuidle`）
   明显更宽；对照套 04 只覆盖 8 种基本类型，07 缺错误码语义分组。
2. **取证纪律**：本套 99 是 842 行、对照套 298 行，差距不在篇幅而在
   **"数字能不能重跑"**（对照套的 `~142`）和**"未验证项分不分层"**
   （对照套有四层，这一点是对等的）。

**唯一一处对照套做得比本套好的地方就是 §9.1 第 1 条那个"没有故障注入"**——
它提醒了一件事：**模板里每一条都要走一遍，"这个子系统没有"不是跳过的理由。**
