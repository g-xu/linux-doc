---
title: 取证附录
---

# watchdog 99：证据、方法与未验证项

> **这一篇决定了前九篇里哪些结论可以直接用、哪些要自己再确认一遍。**
> **本篇所有数字都能用给出的命令重跑**（§6），**每轮修改后全部重跑过**（§7）。

---

## §1 基线与类型判定

### §1.1 基线（全部可重跑）

| 项 | 值 | 命令 |
|---|---|---|
| **内核版本** | **Linux 6.1.158** | `git log -1 --format='%H %s'` |
| **commit** | `f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe` | 同上 |
| ⚠️ **树是否打过补丁** | **打过**（含 arm64 / i915 / 网卡 / 抢占相关头文件） | `git status --porcelain \| wc -l` |
| **本子系统是否被补丁改动** | **没有，输出为 0** | `git status --porcelain -- drivers/watchdog include/linux/watchdog.h include/uapi/linux/watchdog.h \| wc -l` |
| **体系结构范围** | **arm64-only** | — |

⇒ **结论：本套文档描述的是未经本树补丁修改的原生 6.1.158 watchdog 代码。**

⚠️ **一个要留意的间接影响**：**本树改过 `arch/arm64/include/asm/preempt.h` 与
`include/linux/preempt.h`** ⇒ **watchdog 自己的文件没被改，但它用的两个 hrtimer 的
上下文语义可能受影响**（§5.4）。

### §1.2 子系统类型判定（两道门都有输出）

| 门 | 命令 | 实际输出 |
|---|---|---|
| **门 1：有没有 `bus_type`** | `grep -rn 'struct bus_type' drivers/watchdog/ include/linux/watchdog.h` | **无输出** |
| **门 2：ABI 在不在 uapi** | `ls include/uapi/linux/watchdog* \| wc -l` | **1** |

⇒ **门 1 无输出 ⇒ 不是总线型。**
⇒ **门 2 有输出 ⇒ 但它也不是 skill 定义的"虚拟总线 / 跨边界 ABI 型"**（那类要求门 1 也有输出）。
⇒ **本文档把它定为"框架型 + 带用户态 ABI"**（一个混合形态）。

**补充判据（三个都跑了）**：

| 判据 | 结果 |
|---|---|
| **有 `class` 吗** | **有**：`static struct class watchdog_class`（`drivers/watchdog/watchdog_dev.c:985`） |
| **有字符设备吗** | **两套**：`misc_register`（`:1040`）+ `alloc_chrdev_region`（`:1224`） |
| **对端在不在本树** | **在**（用户态守护进程虽在树外，但 in-tree 有样例 `samples/watchdog/watchdog-simple.c`） |

**类型判定对模板的实际影响**：

| 篇 | 因为"带用户态 ABI"而改成 |
|---|---|
| 01 | **§0.1 先声明"对外 API 有两套"；§7 单独讲用户态 ABI（11 ioctl + 13 `WDIOF_*`）** |
| 02 | **§1.0.B 讲公私两套状态位；§1.0.C 讲两套字符设备** |
| 05 | **角色 4/5 是用户态角色**（其他框架型子系统没有这两个） |
| 07 | **必须有用户态工具节（§7）+ 篇末速查（§8）** ——**skill 对"有字符设备的子系统"的硬要求** |
| 08 | **magic close（§5）和两套设备号（§8）两个主题只在这类子系统才有** |

### §1.3 官方文档登记（本项目最丰富的一套）

**`Documentation/watchdog/` 共 10 个文件 / 1904 行**（重跑 `cat Documentation/watchdog/* | wc -l`）：

| 文件 | 行数 | 读到什么程度 | 被吸收进 |
|---|---|---|---|
| **`watchdog-kernel-api.rst`** | **350** | **全文逐段读过** | **01 §2/§3/§4 · 02 §1.0.A · 03 §A3.1/§C2 · 05 §2.2/§3.2** |
| **`watchdog-api.rst`** | **271** | **前 110 行逐字读过（用户态 ABI 语义），其余按标题跳读** | **01 §7.4/§7.5 · 05 §4 · 08 §1/§5** |
| ⚠️ **`watchdog-parameters.rst`** | **740** | ⚠️ **只查不读** | **只在 01 §0.3 / 00 §2.7 登记为裁掉** |
| `convert_drivers_to_kernel_api.rst` | 219 | **只读了标题结构** | 同上 |
| `watchdog-pm.rst` | **22** | **全文读过** | **03 §B3**（resume 必须重置定时器那条规则） |
| `hpwdt.rst` / `pcwd-watchdog.rst` / `mlx-wdt.rst` / `wdt.rst` | 77 / 71 / 66 / 63 | **未读** | **登记为刻意不覆盖**（单驱动文档） |
| `index.rst` | 25 | 读过（toctree） | — |

**`check_doc_coverage.sh` 报"9 篇必查、0 篇 0 引用"**（§7）——
**本套文档对每一篇都至少提到了一次（含"刻意不覆盖"的登记）。**

**`watchdog-kernel-api.rst` 抓到的八条设计意图**（代码里读不出来）：

| # | 意图 | 落在 |
|---|---|---|
| 1 | **框架的定位：把所有对用户态的接口做一遍** | 00 §2.2 |
| 2 | **`owner` 的作用是防止"模块卸载时 `/dev/watchdog` 还开着"** | 01 §2 / 05 §1.2 |
| **3** | **`id == 0` 特殊：同时有 `/dev/watchdog0` 和老的 `/dev/watchdog`** | **01 §0.4 / 02 §1.0.C / 08 §8** |
| **4** | **没有 `stop` 就必须设 `max_hw_heartbeat_ms`** | **01 §2 / 03 §A3.1 / 05 §3.2** |
| **5** | **`max_hw_heartbeat_ms` 让核心当节拍转换器** | **01 §3.3 / 02 §1.0.D / 08 §3** |
| 6 | **`set_timeout` 要把实际值写回、pretimeout 的校验由驱动负责（核心做不到无竞争）** | 05 §2.2 |
| 7 | **`nowayout` 压制 magic close** | 01 §7.5 / 08 §7 |
| 8 | **`watchdog_set_last_hw_keepalive()` 必须紧跟注册之后调** | 01 §4.4 / 05 §3.3 |

### §1.4 官方文档与代码的三处不一致（全部以代码为准）

**完整表在 03 §C2，这里记"怎么发现的"**：

| # | 不一致 | 发现方式 |
|---|---|---|
| **1** | **`watchdog_ops` 的 `restart` 文档写一个参数、代码是三个** | **把文档里的结构体列表与 `include/linux/watchdog.h:43-56` 逐行对照** |
| **2** | **`watchdog_device` 的字段列表漏了 `pm_nb`** | **同一次逐行对照**（文档 19 项、代码 20 项，§6 表 2） |
| **3** | **文档只讲 3 个 `WDOG_*` 位、代码有 6 个** | **数 `include/linux/watchdog.h:115-120` 的 `#define`** |

**文档的"最后审阅日期"是佐证**：
**`Documentation/watchdog/watchdog-kernel-api.rst:5` 写 `Last reviewed: 12-Feb-2013`；
`Documentation/watchdog/watchdog-api.rst:5` 写 `Last reviewed: 10/05/2007`，而且正文里说
"This document describes the state of the Linux 2.4.18 kernel."**
⇒ **用户态 ABI 文档描述的是 2.4 内核 ——而 ABI 本身没变，这正是它仍然有效的原因。**

⚠️ **另外找到一处代码里的复制粘贴错误**（04 §2.4）：
**`drivers/watchdog/pretimeout_noop.c:41` 的 `MODULE_DESCRIPTION` 写成了
"Panic watchdog pretimeout governor"** ——**与 `pretimeout_panic.c:41` 完全一样。**

---

## §2 代码阅读范围与证据等级

### §2.1 一等证据：逐行读过

| 文件 | 行数 | 详读范围 |
|---|---|---|
| **`include/linux/watchdog.h`** | **228** | **全文**（`watchdog_ops` 10 字段 · `watchdog_device` 20 字段 · 6 个 `WDOG_*` · 12 个 inline · 两个宏） |
| **`include/uapi/linux/watchdog.h`** | **58** | **全文**（11 ioctl + 13 `WDIOF_*` + 4 `WDIOS_*` + `watchdog_info`） |
| **`drivers/watchdog/watchdog_core.h`** | **81** | **全文**（`watchdog_core_data` 11 字段 + 3 个 `_WDOG_*` + `MAX_DOGS` + `watchdog_have_pretimeout`） |
| **`drivers/watchdog/watchdog_pretimeout.h`** | **61** | **全文** |
| **`drivers/watchdog/watchdog_core.c`** | **485** | **`:44-65`（全局）· `:95`（min/max 修正）· `:118-156`（`watchdog_init_timeout`）· `:158-217`（三个 notifier）· `:240-337`（`__watchdog_register_device` 全流程）· `:350-371`（注册入口）· `:406`（注销）· `:443-480`（devm + 延迟注册 + init/exit）** |
| **`drivers/watchdog/watchdog_dev.c`** | **1306** | **`:55-62`（全局）· `:64-141`（open_deadline + need_worker + next_keepalive + update_worker）· `:143-236`（ping/worker/hrtimer）· `:247-450`（start/stop/get_status/set_timeout/set_pretimeout/get_timeleft）· `:442-640`（12 个 sysfs 属性 + is_visible + group）· `:653-829`（ioctl 全 switch）· `:676-714`（write）· `:842-978`（open/release/fops/miscdev/class）· `:1032-1090`（cdev 注册）· `:1198`（set_last_hw_keepalive）· `:1210-1250`（init/exit）** |
| **`drivers/watchdog/watchdog_pretimeout.c`** | **216** | **全文** |
| **`drivers/watchdog/watchdog_hrtimer_pretimeout.c`** | **44** | **全文**（04 §3.2 整段引用） |
| **`drivers/watchdog/pretimeout_noop.c` / `pretimeout_panic.c`** | **各 42** | **全文** |
| **`drivers/watchdog/Kconfig`** | — | **前 130 行**（核心 12 个符号 + governor 部分）；⚠️ **后面 ~170 个驱动符号未读** |
| **`include/trace/events/watchdog.h`** | **66** | **全文**（1 个类 + 4 个事件 + 字段与 `TP_printk`） |
| **`samples/watchdog/watchdog-simple.c`** | **25** | **全文**（05 §4.1 逐字引用） |
| **`Documentation/devicetree/bindings/watchdog/watchdog.yaml`** | — | **全文** |
| **`Documentation/watchdog/watchdog-kernel-api.rst`** | **350** | **全文** |
| **`Documentation/watchdog/watchdog-pm.rst`** | **22** | **全文** |

⇒ **本套文档把 watchdog 的 5 个核心文件 + 4 个头文件 + 2 个 governor 全部读完了**
（合计约 2400 行）——**这是本项目覆盖率最高的一套。**

### §2.2 二等证据：只读了局部

| 文件 | 读到什么程度 |
|---|---|
| `Documentation/watchdog/watchdog-api.rst`（271 行） | **前 110 行逐字**（Introduction / 最简 API / magic close / ioctl 前几节）；**后 160 行按标题跳读** |
| `Documentation/watchdog/convert_drivers_to_kernel_api.rst`（219 行） | **只读了各级标题** |
| `drivers/watchdog/Kconfig` 的驱动部分 | **只统计了符号总数（185）** |
| **arm64 DTS** | **只读了 3 个节点的上下文**（06 §2.1 / §2.2）+ 机械统计 |
| **约 170 个看门狗驱动** | **完全没读** ——**只做了目录级统计** |

### §2.3 三等证据：官方文档

**读了哪些、哪些没读，见 §1.3 的表。**
⚠️ **三处与代码冲突，均以代码为准并在 03 §C2 显式指出。**
⚠️ **`watchdog-parameters.rst`（740 行）与四个厂商文档（277 行）刻意未覆盖**，理由见 §1.3。

### §2.4 代表驱动的选法

⚠️ **本套文档一个驱动都没有详读** ——**这是有意的选择，理由如下**：

| 理由 | 说明 |
|---|---|
| **框架自身已经完整** | **`watchdog_ops` 只有 10 个字段、只有 `start` 必需** ⇒ **契约极窄，不需要靠驱动来理解框架** |
| **官方文档已经给了驱动侧指南** | **`watchdog-kernel-api.rst` 350 行专门讲驱动怎么写** ⇒ **05 篇的骨架直接依据它 + 头文件** |
| **驱动之间差异极大** | **约 170 个，从 8 行的 SMC 调用到几百行的 PCI 卡** ⇒ **挑任何一个都不具代表性** |

⇒ **05 篇的三个骨架（最简 / 完整 / 停不下来）是按 `watchdog_ops` 的字段组合构造的**，
**不是从某个具体驱动抄的**（已在 05 §0 声明"未编译"）。

⚠️ **一个后果**：**本套文档无法回答"某颗芯片的 `bootstatus` 从哪个寄存器读"这类问题**
（§5.2）。

**arm64 DTS 的代表节点（三个都逐字引用了）**：

| 节点 | 位置 | 覆盖什么 |
|---|---|---|
| **`watchdog@ff848000`** | `arch/arm64/boot/dts/rockchip/rk3399.dtsi:1630-1635` | **最常见形态：不写 `timeout-sec`** |
| **`&wdt0/&wdt1/&wdt2`** | `arch/arm64/boot/dts/renesas/rzg2lc-smarc-som.dtsi:270-283` | **板级覆盖 + `timeout-sec = <60>`** |
| **`#phy-cells` 式的多 cell**（无对应，watchdog 没有 cell 概念） | — | — |

### §2.5 裁掉的范围登记（arm64-only 与 ACPI）

| 裁掉的 | 判据 | 代码上是否有独立逻辑 |
|---|---|---|
| **ACPI 路径** | **arm64-only 约定** | **核心里 0 处 ACPI 代码**（重跑 `grep -ic 'acpi' drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c` ⇒ 0）⇒ **DT 是核心唯一读的固件来源**（06 §0.1） |
| **非 arm64 的驱动符号** | 同上 | **Kconfig 里有 `WATCHDOG_RTAS`（powerpc）· `WATCHDOG_CP1XXX` / `WATCHDOG_RIO` / `WATCHDOG_SUN4V`（sparc）等**（§6 表 8）——**本文档不涉及** |
| **65 个厂商 DT 绑定** | **通用绑定只有 2 个属性**（06 §0.2） | **核心只读 `timeout-sec` 一个**（06 §1） |
| **PREEMPT_RT 下的 hrtimer 语义** | **本树改过 `preempt.h`（§1.1），但未在 RT 配置下运行** | **两个 hrtimer 模式不同（`_HARD` vs 普通），RT 上差异会放大**（04 §3.3 / §5.4） |

---

## §3 符号存在性核查

**判据两条**：**① 导出符号 / `static inline` / 宏；② 在公共头（`include/linux/watchdog.h`
或 `include/uapi/linux/watchdog.h`）里有声明。**

### §3.1 核查过的符号（05 篇正文里每一个都跑过）

| 类别 | 结论 |
|---|---|
| **9 个导出符号** | **逐个核对过所在文件与导出行**（§6 表 3） |
| **12 个 `static inline`** | **逐个核对过位置**（01 §0.6） |
| **21 个内核侧符号**（05 篇用到的） | **`grep -cw` 全部命中 `include/linux/watchdog.h`** ——**无一处幻觉** |
| **17 个用户态符号**（05 篇用到的 ioctl / `WDIOF_*` / `WDIOS_*`） | **`grep -cw` 全部命中 `include/uapi/linux/watchdog.h`** |
| **两个宏** | **`WATCHDOG_NOWAYOUT`（`include/linux/watchdog.h:124`）· `WATCHDOG_NOWAYOUT_INIT_STATUS`（`:125`）** |
| **内核打印的字符串** | **07 §4.2 的 19 条消息全部逐字 grep 过** |
| **`samples/watchdog/watchdog-simple.c`** | **全文逐字引用，未改一个字** |

### §3.2 ⚠️ 抓到的两个"计数口径"问题（不是幻觉，是数错）

| # | 问题 | 详情 | 处置 |
|---|---|---|---|
| ⚠️ **1** | **导出符号 `grep -c 'EXPORT_SYMBOL'` 给 10，实际 9** | **`drivers/watchdog/watchdog_core.c:27` 是一行 `#include <linux/module.h> /* For EXPORT_SYMBOL/... */` 注释** | **改用 `grep -cE '^EXPORT_SYMBOL'`；01 §0.5 专门写了这条口径**（§9 实例 1） |
| ⚠️ **2** | **`struct watchdog_device` 初稿写 19 个字段，实际 20** | **漏数了 `deferred`（`include/linux/watchdog.h:121`）** ——**它被 6 行 `#define WDOG_*` 隔开，肉眼容易跳过** | **00/01/02 三篇共 6 处已改**（§9 实例 2） |

### §3.3 计数纪律（本套文档遵守的三条）

1. **导出符号用 `grep -cE '^EXPORT_SYMBOL'`，不用 `grep -c 'EXPORT_SYMBOL'`**（§3.2 第 1 条）。
2. **结构体字段数用 `awk 'NR>=首字段行 && NR<=末字段行' | grep -c ';'`** ——
   ⚠️ **上界不能含 `};` 那一行**（phy 那套抓到过同一个问题）。
3. ⚠️ **字段区间里夹了 `#define` 时必须逐个数**（§3.2 第 2 条）——
   **`watchdog_device` 就是这种情况。**

---

## §4 索引工具（cbm）的盲区

**本套文档全程只用 `rg` + `Read` 取证。**

| 盲区 | 在 watchdog 里的具体表现 |
|---|---|
| **函数指针间接调用** | **`ops->start` / `ops->ping` / `gov->pretimeout` 全是间接调用** ⇒ **"谁实现了 `start`"必须靠 grep `struct watchdog_ops` 实例** |
| **两套同名前缀的状态位** | **`WDOG_*`（公共）与 `_WDOG_*`（私有）** ⇒ **搜 `WDOG_ACTIVE` 不会命中 `_WDOG_DEV_OPEN`，但反过来搜 `WDOG_` 会把两套混在一起**（02 §1.0.B） |
| **两个 `status` 字段** | **`watchdog_device.status` 与 `watchdog_core_data.status` 同名不同物** ⇒ **grep `->status` 得区分是哪个结构** |
| **条件编译的字段** | **`watchdog_core_data.pretimeout_timer` 在 `#if IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT)` 里**（`drivers/watchdog/watchdog_core.h:50-52`） |
| **私有头文件** | **`watchdog_core.h` / `watchdog_pretimeout.h` 不在 `include/` 下** ⇒ **只在 `drivers/watchdog/` 内可见，索引工具容易把它们当普通头文件** |
| **ioctl 的 switch** | **11 个 `case` 在一个 178 行的函数里**（`drivers/watchdog/watchdog_dev.c:653-829`）⇒ **调用图上是一个大黑盒** |

---

## §5 ⚠️ 未验证项（四层）

### §5.1 第一层：未编译、未运行

| 项 | 说明 |
|---|---|
| **05 篇的驱动骨架（§1–§3、§6）** | **一行都没编译过**；符号存在性核对过（§3），**但 `FOO_*` 寄存器名是假名、错误路径做了删减** |
| **05 §4.1 是唯一的例外** | **它逐字引自 `samples/watchdog/watchdog-simple.c`，是真实可编译的代码** |
| **05 §4.2/§4.4/§5 的用户态片段** | **未编译** ——**但 ioctl 号、结构体、标志位全部核对过** |
| **07 篇全部命令** | **没有在真机上跑过。**`/sys/class/watchdog/` 下的属性清单来自 `DEVICE_ATTR` 的定义，**不是观察到的** |
| **07 §6 的三条"准注入"** | **未实测** ——**依据是代码路径** |
| **`wdctl` 工具的输出** | **未实测**（07 §7 已标注） |
| **06 篇的 DTS 片段** | **真实节点逐字引自 in-tree 文件并给了行号**；**未编译 DTB、未上板** |

### §5.2 第二层：只读了符号表 / 完全没读

| 项 | 后果 |
|---|---|
| ⚠️ **约 170 个看门狗驱动** | **完全没读**（§2.4）⇒ **无法回答"某颗芯片的 `bootstatus` 从哪个寄存器读"、"某个驱动的 `max_hw_heartbeat_ms` 为什么是这个值"** |
| **`watchdog-parameters.rst`（740 行）** | **只查不读** ⇒ **无法回答"某个驱动有哪些模块参数"** |
| **`convert_drivers_to_kernel_api.rst`（219 行）** | **只读标题** ⇒ **老驱动迁移的具体步骤不在本文档** |
| **Kconfig 的 ~170 个驱动符号** | **只统计了总数** |

### §5.3 ⚠️ 第三层：推断而非观察

| 论断 | 为什么是推断 | 风险 |
|---|---|---|
| ⚠️ **04 §1.7 的"`default_gov` 可能变悬垂指针"** | **纯代码推理**：`watchdog_unregister_governor()` 不清 `default_gov`（`grep -n 'default_gov'` 四处出现，无一处清零）⇒ **默认 governor 模块被 `rmmod` 后 `:170` 会把设备的 `gov` 设成悬垂指针**；⚠️ **没有编译、没有实测、没有查上游是否已修、没有确认 Kconfig 组合可达** | ⚠️ **高** ——**04 §1.7 已显式标为"提醒读者自己核一遍"，不是断言** |
| **"`/sys/class/watchdog/watchdogN/` 下有 `device` / `of_node` / `dev` 等通用属性"** | **由 `watchdog_class` + `watchdog_core_data.dev` 推出的驱动核心通用行为** ——**没有实机 `ls` 过** | **中**（07 §2.8） |
| **"`panic()` 在关中断持锁上下文能正常产出 crash dump"** | ⚠️ **没有验证** ——**只指出了这个上下文的存在** | **中**（04 §2.2 已标注） |
| **"实际复位时刻比 `timeout` 晚最多一个 `max_hw_heartbeat_ms`"** | **由 `watchdog_next_keepalive()` 的算法推出**（`drivers/watchdog/watchdog_dev.c:117-124` 的注释支持这个方向）——**未实测** | **低**（03 §B2.3） |
| **"所有看门狗共用一个 kworker 会互相拖累"** | **由 `watchdog_kworker` 是全局变量推出** ——**未构造场景验证** | **低**（03 §C4 第 1 条） |
| **"`identity` 填满 32 字节时 `%s` 会读出界"** | **由 uapi 定义 + `sysfs_emit("%s")` 推出** ——**树里没有填满的驱动，未实测** | **低**（07 §2.6） |
| **"约 170 个驱动"** | ⚠️ **是 180 减去核心 5 个、两个 governor、几个头文件后的估算** ——**没有逐个分类** | **低**（本文档一律写"约"） |

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| **所有行号只对 `6.1.158` 有效** | **`watchdog_dev.c` 1306 行，任何 backport 都会位移** |
| **`ida_simple_get` / `ida_simple_remove`** | **更高版本推荐 `ida_alloc`/`ida_free`** ⇒ **6.1 里还在用老接口**（`drivers/watchdog/watchdog_core.c:263` / `:268` / `:276`） |
| **`class_register(&watchdog_class)` 用静态 `struct class`** | **更高版本内核推动"class 不再由调用者持有静态实例"** ⇒ **这段代码在新内核上可能形式不同** |
| ⚠️ **两个 hrtimer 的模式差异在 RT 上的表现** | **本树改过 `preempt.h`（§1.1）** ⇒ **`HRTIMER_MODE_REL_HARD`（喂狗）与 `HRTIMER_MODE_REL`（软 pretimeout）在 RT 上的实际行为未验证**（04 §3.3 / 08 §6） |
| **官方文档三处过时**（§1.4） | **说明这块文档更新滞后** ⇒ **看更高版本时要重新核对** |
| ⚠️ **04 §1.7 那处可疑之处** | **没有查上游是否已修** ⇒ **在新内核上可能已经不存在** |

⚠️ **本子系统需要一个"第五层"的变体：对端在树外。**
**watchdog 的主要消费者是用户态守护进程（`watchdog` / `systemd`），它们不在本树里。**
⇒ **本文档对"守护进程实际怎么用"的描述，依据是**：
**① in-tree 样例（25 行，一等证据）；② 官方 `watchdog-api.rst`（三等证据）；
③ uapi 头文件的 ABI 定义（一等证据）。**
⚠️ **没有读过任何一个真实守护进程的源码**（`systemd` / `watchdog` 都不在本树）。

---

## §6 可重跑的数字

⚠️ **口径警告先行**：**每个数字后面的"口径"一列比数字本身更重要。**

### 表 1 规模

| 项 | 值 | 命令 | 口径 |
|---|---|---|---|
| 文件数 | **180** | `ls drivers/watchdog/*.c drivers/watchdog/*.h \| wc -l` | **含 `.h`；目录没有子目录** |
| 总行数 | **59266** | `cat drivers/watchdog/*.c drivers/watchdog/*.h \| wc -l` | 同上 |
| **核心 5 文件** | **2132** | `cat drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c drivers/watchdog/watchdog_pretimeout.c drivers/watchdog/watchdog_hrtimer_pretimeout.c drivers/watchdog/watchdog_core.h \| wc -l` | **占 3.6%** |
| 逐文件 | **`watchdog_dev.c` 1306 · `watchdog_core.c` 485 · `watchdog_pretimeout.c` 216 · `watchdog_core.h` 81 · `watchdog_hrtimer_pretimeout.c` 44** | `wc -l drivers/watchdog/watchdog_*.c drivers/watchdog/watchdog_core.h` | — |
| 两个 governor | **各 42** | `wc -l drivers/watchdog/pretimeout_*.c` | — |
| **两个头文件** | **228 + 58** | `wc -l include/linux/watchdog.h include/uapi/linux/watchdog.h` | — |
| **私有头** | **81 + 61** | `wc -l drivers/watchdog/watchdog_core.h drivers/watchdog/watchdog_pretimeout.h` | — |
| **tracepoint 头** | **66** | `wc -l include/trace/events/watchdog.h` | — |
| **官方文档** | **10 文件 / 1904 行** | `ls Documentation/watchdog/ \| wc -l` · `cat Documentation/watchdog/* \| wc -l` | — |

### 表 2 结构体字段数

| 结构 | 值 | 命令 / 口径 |
|---|---|---|
| ⚠️`struct watchdog_device` | **20** | `awk 'NR>=95 && NR<=121' include/linux/watchdog.h \| grep -c ';'` ——⚠️ **区间里夹了 6 行 `#define WDOG_*`（`:114-120`），肉眼数极易漏掉最后的 `deferred`（`:121`）**（§3.2 第 2 条） |
| `struct watchdog_ops` | **10** | `awk 'NR>=44 && NR<=55' include/linux/watchdog.h \| grep -c ';'` |
| `struct watchdog_core_data` | **11** | `awk 'NR>=41 && NR<=53' drivers/watchdog/watchdog_core.h \| grep -c ';'` ——**其中 `pretimeout_timer` 在 `#if` 里** |
| `struct watchdog_governor` | **2** | **逐个数**（`drivers/watchdog/watchdog_pretimeout.h:10-11`） |
| `struct watchdog_info`（uapi） | **3** | **逐个数** |

### 表 3 导出符号（9）

| 文件 | 数 | 符号 |
|---|---|---|
| `watchdog_core.c` | **5** | `watchdog_init_timeout`(`:156`) · `watchdog_set_restart_priority`(`:238`) · `watchdog_register_device`(`:371`) · `watchdog_unregister_device`(`:406`) · `devm_watchdog_register_device`(`:443`) |
| `watchdog_pretimeout.c` | **3** | `watchdog_notify_pretimeout`(`:113`，`_GPL`) · ⚠️`watchdog_register_governor`(`:150`，**裸**) · ⚠️`watchdog_unregister_governor`(`:175`，**裸**) |
| `watchdog_dev.c` | **1** | `watchdog_set_last_hw_keepalive`(`:1198`) |

⚠️⚠️ **口径（本篇第一号数字纪律）**：
**`grep -cE '^EXPORT_SYMBOL' …` ⇒ 9**；
**`grep -c 'EXPORT_SYMBOL' …` ⇒ 10（多算了 `watchdog_core.c:27` 那行注释）。**

### 表 4 用户态 ABI

| 项 | 值 | 命令 | 口径 |
|---|---|---|---|
| **ioctl 数** | **11** | `grep -c '^#define\s*WDIOC_' include/uapi/linux/watchdog.h` | — |
| **`WDIOF_*` 数** | **13** | `grep -c '^#define\s*WDIOF_' include/uapi/linux/watchdog.h` | ⚠️ **含 `WDIOF_UNKNOWN`（值 `-1`，是错误标记不是能力位）** ⇒ **真正的能力/状态位是 12 个** |
| **`WDIOS_*` 数** | **4** | `grep -c '^#define\s*WDIOS_' include/uapi/linux/watchdog.h` | ⚠️ **含 `WDIOS_UNKNOWN`（值 `-1`）** ⇒ **真正能传给 `WDIOC_SETOPTIONS` 的是 3 个** |

### 表 5 sysfs 与 tracepoint

| 项 | 值 | 命令 |
|---|---|---|
| **sysfs 属性数** | **12** | `grep -c 'DEVICE_ATTR' drivers/watchdog/watchdog_dev.c` |
| **其中可写** | **2**（`nowayout` · `pretimeout_governor`） | `grep -n 'DEVICE_ATTR_RW' drivers/watchdog/watchdog_dev.c` |
| **条件可见的** | **4** | `drivers/watchdog/watchdog_dev.c:600-617` 的 `wdt_is_visible()` |
| **tracepoint 事件** | **4** | `grep -cE '^(TRACE_EVENT\|DEFINE_EVENT)\(' include/trace/events/watchdog.h` |
| **`DECLARE_EVENT_CLASS`** | **1** | **不算事件**（与 regmap 那套同一条口径） |
| **tracepoint 打点位置** | **6** | `grep -rn 'trace_watchdog' drivers/watchdog/*.c \| wc -l` |

### 表 6 错误码普查（4 个核心 `.c`）

| 码 | 值 |
|---|---|
| `-EINVAL` | **10** |
| `-ENODEV` | **7** |
| `-EBUSY` | **7** |
| `-EFAULT` | **5** |
| `-EOPNOTSUPP` | **5** |
| `-ENOMEM` | 4 |
| `-ENOIOCTLCMD` | **2** |
| `-ENOTTY` / `-EPERM` | 各 1 |
| `-EIO` / `-EAGAIN` / `-ERANGE` | **全 0** |

重跑：
`C="drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c drivers/watchdog/watchdog_pretimeout.c drivers/watchdog/watchdog_hrtimer_pretimeout.c"; for e in EINVAL ENODEV EBUSY EFAULT EOPNOTSUPP ENOMEM ENOIOCTLCMD ENOTTY EPERM EIO EAGAIN ERANGE; do echo -n "-$e "; grep -rho -- "-$e" $C | wc -l; done`

⚠️ **口径两条**：
1. **范围显式限定 4 个核心 `.c`**，**不含两个 governor 和头文件。**
2. **`-EIO` 为 0，但官方文档要求驱动的 `set_timeout` 在写硬件失败时返回 `-EIO`**
   ⇒ **那是驱动侧的约定，核心自己不用**（01 §8.1）。

### 表 7 打印与锁

| 项 | 值 | 命令 |
|---|---|---|
| **打印总数** | **19** | `grep -n 'pr_crit\|pr_err\|pr_warn\|pr_info\|pr_alert' drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c drivers/watchdog/pretimeout_noop.c \| wc -l` |
| **分布** | **`watchdog_core.c` 9 · `watchdog_dev.c` 9 · `pretimeout_noop.c` 1** | 同上分文件 |
| **`dev_dbg`/`pr_debug`** | **0** | `grep -c 'pr_debug\|dev_dbg' drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c` ⇒ **没有需要 dyndbg 的隐藏日志** |
| **`spin_lock` 出现的文件** | **只有 `watchdog_pretimeout.c`（7 处）** | `grep -c 'spin_lock' drivers/watchdog/watchdog_dev.c drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_pretimeout.c` ⇒ **0 / 0 / 7** |
| **锁总数** | **4 把 + 1 个 IDA** | 02 §6.4 |
| **设计债标记** | **0** | `grep -nE '(FIXME\|TODO\|REVISIT\|HACK\|\bXXX\b)' drivers/watchdog/watchdog_*.c` 无输出 |
| **`MAX_DOGS`** | **32** | `grep -oE 'MAX_DOGS\s+[0-9]+' drivers/watchdog/watchdog_core.h` |
| **`WATCHDOG_GOV_NAME_MAXLEN`** | **20** | `grep -n 'WATCHDOG_GOV_NAME_MAXLEN' drivers/watchdog/watchdog_pretimeout.h` |

### 表 8 ⚠️ Kconfig（口径最容易搞错的一张）

| 项 | 值 | 口径 |
|---|---|---|
| ⚠️ **`config` 总数** | **185** | `grep -c '^config ' drivers/watchdog/Kconfig` ——⚠️ **绝大多数是驱动符号，不是核心行为开关** |
| **`WATCHDOG` 前缀的符号** | **16** | `grep -c '^config WATCHDOG' drivers/watchdog/Kconfig` |
| ⚠️ **其中真正的核心行为开关** | **12** | **`WATCHDOG_CORE` · `_NOWAYOUT` · `_HANDLE_BOOT_ENABLED` · `_OPEN_TIMEOUT` · `_SYSFS` · `_HRTIMER_PRETIMEOUT` · `_PRETIMEOUT_GOV` · `_PRETIMEOUT_GOV_SEL` · `_PRETIMEOUT_GOV_NOOP` · `_PRETIMEOUT_GOV_PANIC` · `_PRETIMEOUT_DEFAULT_GOV_NOOP` · `_PRETIMEOUT_DEFAULT_GOV_PANIC`** |
| ⚠️ **剩下 4 个是驱动符号** | **`WATCHDOG_RTAS`（powerpc）· `WATCHDOG_CP1XXX` · `WATCHDOG_RIO` · `WATCHDOG_SUN4V`（sparc）** | **名字带 `WATCHDOG_` 前缀但不是核心开关** ⇒ **只按前缀数会多算 4 个**（§2.5） |

**加上 `menuconfig WATCHDOG` 本身**（`grep '^config '` 数不到它，因为它是 `menuconfig`）
⇒ **01 §0.7 那张表列的是"核心行为开关 + `WATCHDOG` 本身"。**

### 表 9 arm64 DTS

| 属性 | 文件数 | 行数 | 备注 |
|---|---|---|---|
| `watchdog@` | **91** | **125** | — |
| `timeout-sec` | **29** | **34** | **只有 1/3 的节点写它** |
| `wdt@` | **3** | 17 | ⚠️ **违反通用绑定的 `$nodename` 模式**（06 §7） |
| ⚠️ **`watchdogN = <&…>` alias** | **0** | **0** | **整个 `arch/` 下 0 处** ⇒ **`of_alias_get_id` 那条路是死字**（06 §3.2） |
| **`timeout-sec` 取值分布** | — | **`<60>` 25 · `<30>` 3 · `<10>` 3 · `<40>`/`<20>`/`<12>` 各 1** | `grep -rhoE 'timeout-sec = <[0-9]+>' arch/arm64/boot/dts/ \| sort \| uniq -c \| sort -rn` |
| **DT 绑定文件数** | **66**（含通用绑定 1 个） | — | `ls Documentation/devicetree/bindings/watchdog/ \| wc -l` |

### 表 10 其他

| 项 | 值 | 命令 |
|---|---|---|
| **`struct watchdog_governor` 实例（全树）** | **2** | `grep -rn 'struct watchdog_governor [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| **`watchdog_have_pretimeout` 命中行** | **6**（5 处使用 + 1 处定义） | `grep -rn 'watchdog_have_pretimeout' drivers/watchdog/ \| wc -l` |
| **故障注入设施** | **0** | `grep -rniE 'fault_inject\|should_fail' drivers/watchdog/watchdog_*.c` |
| **debugfs** | **0** | `grep -rl 'debugfs' drivers/watchdog/watchdog_*.c` |
| **核心里的 ACPI 代码** | **0** | `grep -ic 'acpi' drivers/watchdog/watchdog_core.c drivers/watchdog/watchdog_dev.c` |
| **in-tree 用户态样例** | **1 个 / 25 行** | `wc -l samples/watchdog/watchdog-simple.c` |

---

## §7 三个批量自检脚本的结果

| 脚本 | 结果 | 说明 |
|---|---|---|
| `check_line_refs.py` | **干净** | **扫描约 220 条唯一 `文件:行号` 引用，无解析不到的文件、无越界行号、无 AMBIGUOUS、无 UNRESOLVED、忽略项 0 条** |
| `check_xrefs.py` | **干净** | **跨篇 `0X §Y` 引用全部命中，忽略项 0 条** |
| `check_doc_coverage.sh` | **9 篇必查档，0 篇 0 引用** | **本项目第一次遇到"必查档全部有引用"是真通过而不是假通过** |

**忽略文件**：**本文档集不需要 `.xref-ignore` / `.lineref-ignore`。**

⚠️ **`check_doc_coverage.sh` 的两个口径问题**：
1. **它报 `wdt` "引用 57 次"、`watchdog` 绑定"引用 1464 次"** ——
   **这两个数字没有意义**：**脚本按文件名去正文里数，而 `wdt` / `watchdog` 是本文档集
   每一句话都可能出现的词。**
   ⇒ **它只能证明"不是 0"。**
2. **32 篇 DT 绑定被列为"参考、被引用 2 篇"** ——**这是预期的**：
   **06 篇的硬要求是"每条属性都有定位到行的消费者"**（06 §1）。

⚠️ **本节刻意只描述结果、不照抄脚本失败行的格式**（skill 规则 28 的 条）。

---

## §8 逐篇取证表

### 00-overview.md

| 论断 | 出处 |
|---|---|
| 框架型 + 带用户态 ABI | §1.2 两道门 + 三个补充判据 |
| 180 文件 / 59266 行 / 核心 2132 行 / 9 导出 | §6 表 1、表 3 |
| 五样东西描述"谁在喂狗" | 02 §5.2 + 一等证据（`watchdog_core.h` 全文） |
| 三处文档不一致 | §1.4 / 03 §C2 |
| 端到端路径每一步行号 | §2.1 的详读范围 |

**自审修正**：**`watchdog_device` 字段数 19 → 20**（§9 实例 2）。

### 01-api.md

| 论断 | 出处 |
|---|---|
| 两套对外 API | 一等证据（两个头文件全文） |
| **9 个导出符号（不是 10）** | §6 表 3 的口径 |
| `watchdog_ops` 10 字段、只有 `start` 必需 | **源码注释 `/* mandatory operations */`（`include/linux/watchdog.h:45`）** |
| 20 个字段分五组 | §6 表 2 |
| 11 ioctl + 13 `WDIOF_*` + 4 `WDIOS_*` | §6 表 4 |
| `-ENOIOCTLCMD` vs `-ENOTTY` 的分工 | `drivers/watchdog/watchdog_dev.c:746-748` + `:821-823` |

⚠️ **自审修正（两条）**：
**① 导出符号从 10 改成 9，并补了口径说明**（§9 实例 1）；
**② `watchdog_device` 19 → 20 个字段。**

### 02-datastructures.md

| 论断 | 出处 |
|---|---|
| 六组易错关系 | 一等证据（两个头文件 + 两个私有头 + 两个核心 `.c`） |
| **`WDOG_ACTIVE` vs `WDOG_HW_RUNNING` 的四种组合** | **`drivers/watchdog/watchdog_dev.c:94-95` 的两个条件式 + `:307-308`** |
| 公私两套状态位 | `include/linux/watchdog.h:115-120` vs `drivers/watchdog/watchdog_core.h:54-56` |
| 两套字符设备指向同一个 `watchdog_core_data` | `drivers/watchdog/watchdog_dev.c:850-854` + `:1037-1040` |
| 四把锁 + 1 个 IDA | `:22-26` + `watchdog_core.c:44-65` + `watchdog_pretimeout.c:16-33` |

**自审修正**：**`watchdog_device` 字段数 19 → 20（两处）。**

### 03-internals.md

| 论断 | 出处 |
|---|---|
| 七步注册 | `drivers/watchdog/watchdog_core.c:240-337` 逐行 |
| **延迟注册丢弃返回值** | `watchdog_core.c:355-359` + `:455` |
| **心跳算法四步** | `drivers/watchdog/watchdog_dev.c:98-126` 逐行 + 源码注释 |
| magic close 三层判定 | `drivers/watchdog/watchdog_dev.c:933-948` + 源码注释 |
| 没有 `stop` 时"停"变成置 `WDOG_HW_RUNNING` | `:303-315` |
| 三处文档不一致 + 四类静默失效 + 四个可质疑设计 | Part C，每条都有行号 |

⚠️ **自审修正（一条）**：
**§A6 原写"写了别的字符则清掉 `_WDOG_ALLOW_RELEASE`"，
改为"每次 write 开头无条件清零，再看这次 buffer 里有没有 `'V'`"**（依据 `:691`，§9 实例 3）——
**并补上了 `len == 0` 不喂狗、`_WDOG_KEEPALIVE` 不在 write 里置位两条。**

### 04-extensions.md

| 论断 | 出处 |
|---|---|
| 两个链表 + 两个包装结构 + 两把不同类型的锁 | `drivers/watchdog/watchdog_pretimeout.c:16-38` 全文 + 源码注释 `:18` |
| **governor 回调在 spinlock 内、关中断执行** | `watchdog_pretimeout.c:104-111` |
| 两个内置 governor 各 42 行 | 全文读过 |
| **软 pretimeout 只在硬件没有时才 arm** | `drivers/watchdog/watchdog_hrtimer_pretimeout.c:32` 的取反条件 |
| **三个出口对 `WDIOF_PRETIMEOUT` 说法不一致** | `drivers/watchdog/watchdog_dev.c:752-753` vs `:351-352` vs `:609` |
| ⚠️ **`default_gov` 可能悬垂**（未验证） | `grep -n 'default_gov'` 的四处 + `:170` |
| `noop` 的 `MODULE_DESCRIPTION` 复制粘贴错误 | `pretimeout_noop.c:41` vs `pretimeout_panic.c:41` |

⚠️ **自审修正（两条）**：
**① `phy_create_lookup` 式的计数问题：`watchdog_have_pretimeout` 从"5"改成
"5 处使用 + 1 处定义 = grep 命中 6"**；
**② §3.4 原写"GETSUPPORT 说没有、sysfs 说有"，
补读 `watchdog_get_status()` 后发现 **GETSTATUS 也说有** ⇒ 改成三个出口的对照表**（§9 实例 4）。

### 05-usage.md

| 论断 | 出处 |
|---|---|
| 六个角色（含两个用户态角色） | 由"谁造 / 谁用 / 谁治理"切分 |
| 每个骨架里的符号都存在 | §3.1 |
| 七组近邻符号 | 一等证据（两个头文件 + 核心 `.c`） |
| **`set_timeout` 的三条硬要求** | **官方 `watchdog-kernel-api.rst` 逐字引用** |
| **magic close 的四条规则** | `drivers/watchdog/watchdog_dev.c:676-714` 逐行 |
| **`WDIOF_MAGICCLOSE` 在两个 ioctl 里含义不同** | `:345-346`（GETSTATUS）vs `:752-753`（GETSUPPORT） |
| **`WDIOF_KEEPALIVEPING` 是 `test_and_clear`** | `:348-349` |

**自审修正**：**无内容修正**（38 个符号一次核查全部通过）。

### 06-devicetree.md

| 论断 | 出处 |
|---|---|
| 四条配置来源 + 三层优先级 | `drivers/watchdog/watchdog_core.c:118-155` 全文 + kerneldoc 逐字 |
| **模块参数的 0 与 `timeout-sec` 的 0 含义不同** | **kerneldoc 明文 + `:129` 与 `:142` 的条件对比** |
| 通用绑定只有 2 项 | `Documentation/devicetree/bindings/watchdog/watchdog.yaml` 全文 |
| **aliases 那条路全树 0 处使用** | `grep -rn 'watchdog[0-9]* = <&' arch/ --include=*.dts --include=*.dtsi` 无输出 |
| 六层决策树 | 每层都有行号 |
| arm64 统计 | §6 表 9 |

**自审修正**：**无内容修正。**

### 07-debug.md

| 论断 | 出处 |
|---|---|
| 12 个 sysfs 属性逐个实现位置 | `drivers/watchdog/watchdog_dev.c:442-638` 逐行 |
| **`state` 只反映 `WDOG_ACTIVE`** | `:558-566` 全文 |
| **`cat status` 有副作用** | `:348-349` 的 `test_and_clear_bit` |
| **`nowayout` 只能设不能清** | `:463-465` + 源码注释 |
| 4 个 tracepoint / 6 个打点 | §6 表 5 |
| **`watchdog_start` 可能只是一次喂狗** | `:164-167` 的二选一 |
| 19 条打印逐条 | §6 表 7 |

⚠️ **自审修正（一条）**：
**§4.1/§4.2 初稿只列了 13 条消息，重跑 grep 后补全到 19 条**（补了 `a legacy watchdog module
is probably present.`、`running and kernel based pre-userspace handler disabled` 等 6 条，§9 实例 5）。

### 08-feynman.md

| 论断 | 出处 |
|---|---|
| 八个主题的每个技术断言 | **全部带行号，取自 §2.1 的详读范围** |
| 两个"在跑"的四种组合 | 02 §1.0.A（同源） |
| **"框架只是停止阻止"** | `drivers/watchdog/watchdog_dev.c:235` 的 `HRTIMER_NORESTART` + `:173` 的重排点 |
| magic close 的"签字"记账 | `:687-699` + 源码注释逐字 |
| `nowayout` 的例外（reboot notifier 绕过） | `watchdog_core.c:164-172` |
| 两块牌子与"退让" | `watchdog_core.c:277-291` + `watchdog_dev.c:1045` |

**自审修正**：**无内容修正。**

### 99-evidence.md（本篇）

**自审修正**：**§6 的每一格都逐条重跑对账过**（skill 规则 30 的 条），
**抓到三个口径问题**：
**① 导出符号的注释行；② `watchdog_device` 字段区间里夹了 `#define`；
③ `grep -c '^config WATCHDOG'` 会把 4 个驱动符号算成核心开关（表 8）。**

---

## §9 交叉验证实例（初始假设被推翻的六条）

| # | 初始假设 | 推翻它的证据 | 落到哪 |
|---|---|---|---|
| ⚠️ **1** | **"导出符号 10 个"**（`grep -c 'EXPORT_SYMBOL'` 的结果） | **逐行看 grep 输出，发现 `watchdog_core.c:27` 是 `#include <linux/module.h> /* For EXPORT_SYMBOL/... */`** ⇒ **改用 `grep -cE '^EXPORT_SYMBOL'` ⇒ 9** | **01 §0.5 专门写了这条口径；99 §6 表 3** |
| ⚠️ **2** | **"`struct watchdog_device` 19 个字段"**（肉眼数） | **`awk … \| grep -c ';'` 给 20** ⇒ **漏数了 `deferred`（`:121`），因为它被 6 行 `#define WDOG_*` 隔在后面** | **00/01/02 共 6 处已改** |
| **3** | **"写了非 `'V'` 字符会清掉 magic close 标记"** | **读 `write()` 全文发现 `:691` 是**每次开头无条件**清零，然后再扫描本次 buffer** ⇒ **语义是"最近这一次 write 里有没有 `'V'`"** | **03 §A6 改写 + 05 §4.2 规则 1** |
| **4** | **"开了 `HRTIMER_PRETIMEOUT` 后，sysfs 说有 pretimeout 而 ioctl 说没有"** | **补读 `watchdog_get_status()` 发现 `:351-352` 让 **GETSTATUS 也说有**** ⇒ **不一致的准确形态是"GETSUPPORT 说没有、GETSTATUS 和 sysfs 说有"** | **04 §3.4 改成三个出口的对照表；05 §5.3 坑 3；08 §6** |
| **5** | **"核心的打印有 13 条"**（初稿按印象列的） | **`grep -n 'pr_crit\|pr_err\|pr_warn\|pr_info\|pr_alert'` 给 19 条** ⇒ **补了 6 条，其中 `a legacy watchdog module is probably present.` 是排查 `/dev/watchdog` 缺失的关键线索** | **07 §4.1/§4.2** |
| **6** | **"`watchdog_have_pretimeout` 有 5 个使用点"** | **`grep -rn … \| wc -l` 给 6** ⇒ **5 处使用 + 1 处定义** | **04 §4.2 改口径** |

**三条方法论结论**：
1. ⚠️ **`grep -c '<符号>'` 会把注释行算进去** ——**导出符号一律用 `^EXPORT_SYMBOL` 锚定**（实例 1）。
2. ⚠️ **结构体字段区间里夹了 `#define` 时，肉眼数一定会漏** ——
   **必须用 `awk` + `grep -c ';'`，而且上界要排掉 `};`**（实例 2）。
3. **"某个出口说 A、另一个说 B"这类不一致，必须把**每一个**出口的代码都读到**
   （实例 4）——**只读两个就下结论，第三个会推翻你。**

**一条反例（说明结论的方向）**：
**05 篇 38 个符号的存在性核查一次通过，无一处幻觉。**
⇒ **说明"每个符号写进文档前先 grep"这条纪律有效**（对比 regmap 那轮抓到的 `REGCACHE_MAPLE`）。

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统没有对照套

**`output/qoder/` 下没有 watchdog 目录**（对照套只覆盖了 spi）
⇒ **本套文档没有做过独立双套 diff，§9 那六条全部来自自审。**

**读者若另有一套 watchdog 文档，最值得对照的四处**：
1. **导出符号是 9 还是 10**（本套认为 9，§9 实例 1）；
2. **`struct watchdog_device` 是 19 还是 20 个字段**（本套认为 20，§9 实例 2）；
3. **`WDIOF_PRETIMEOUT` 在三个出口的说法**（本套逐个读过代码，§9 实例 4）；
4. ⚠️ **04 §1.7 那处 `default_gov` 的可疑之处**（本套标为"高风险推断"，§5.3）。

### §10.2 要回写 skill 的三条

| # | 内容 | 为什么 |
|---|---|---|
| ⚠️ **1** | **在规则 30 里加一条：「`grep -c '<符号>'` 要用 `^` 锚定，注释行会被算进去」** | **本轮第一个错就是这个**（§9 实例 1）；**regmap 那轮的"子串包含"是同一类问题的另一个变体** |
| ⚠️ **2** | **在规则 30 里加一条：「结构体字段区间里夹了 `#define` / 注释块时，`awk` 区间必须逐行确认」** | **`watchdog_device` 的 `deferred` 被 6 行 `#define` 隔开，肉眼和粗略的 awk 都会漏**（§9 实例 2） |
| **3** | **在 07 篇的检查动作里加一条：「同一个标志位在多个出口出现时，必须把每个出口的代码都读到」** | **`WDIOF_PRETIMEOUT` 有三个出口，读两个就下了错结论**（§9 实例 4） |

### §10.3 明确不吸收的权衡（避免下次又发现一遍）

| 没做 | 理由 |
|---|---|
| ⚠️ **一个看门狗驱动都没详读** | **有意的**（§2.4）：**契约只有 10 个字段、官方文档已有 350 行驱动指南** ⇒ **读驱动的边际收益低于成本** |
| **不读 `watchdog-parameters.rst`（740 行）** | **它是字典而非机制说明** |
| **不覆盖四个厂商文档（277 行）** | **单驱动文档** |
| **不验证 04 §1.7 那处可疑之处** | **需要编译 + 特定 Kconfig + `rmmod`** ⇒ **已显式标为未验证推断，并写清了"为什么不该当成断言"** |
| **不实测 `wdctl` 的输出** | **未上机运行**（§5.1） |
| **不做 ACPI 对照** | **核心里 0 处 ACPI 代码**（§2.5）——**比 phy 那套更干净：phy 至少用了 `fwnode_*`，watchdog 直接用 `of_*`** |
