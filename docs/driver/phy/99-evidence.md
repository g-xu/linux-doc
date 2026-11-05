---
title: 取证附录
---

# phy 99：证据、方法与未验证项

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
| **本子系统是否被补丁改动** | **没有，输出为 0** | `git status --porcelain -- drivers/phy include/linux/phy \| wc -l` |
| **体系结构范围** | **arm64-only** | — |

⇒ **结论：本套文档描述的是未经本树补丁修改的原生 6.1.158 通用 PHY 框架代码。**

⚠️ **一个要留意的间接影响**：**本树改过 `arch/arm64/include/asm/preempt.h` 和
`include/linux/preempt.h`**（`git status --porcelain -- ...` 两个都是 `M`）
⇒ **phy 自己的文件没被改，但它用的 `mutex` 语义可能受影响**（§5.4）。

### §1.2 子系统类型判定（两道门 + 一个补充判据）

| 门 | 命令 | 实际输出 |
|---|---|---|
| **门 1：有没有 `bus_type`** | `grep -rn 'struct bus_type' drivers/phy/ include/linux/phy/` | **无输出** |
| **门 2：ABI 在不在 uapi** | `ls include/uapi/linux/phy* \| wc -l` | **0** |
| ⚠️ **补充：有没有 `class`** | `grep -n 'class_create' drivers/phy/phy-core.c` | **有**：`drivers/phy/phy-core.c:1233` 的 `class_create(THIS_MODULE, "phy")` |

⇒ **门 1 无输出 ⇒ 框架型。**
⚠️ **但它是"class 型框架"，不是纯 API 框架**（对比 regmap：regmap 连 class 都没有）
⇒ **所以它有一个真实的 sysfs 观测面 `/sys/class/phy/`**（07 §2）。

**类型判定对模板的实际影响**：

| 篇 | 因为是"框架型 + class 型"而改成 |
|---|---|
| 00 §2 | **走路径 B（问题背景）** |
| 02 | **画"consumer ↔ 核心 ↔ provider"图**，**并显式说明"是 class 不是 bus"**（§1.0.C） |
| 03 | **主轴是"创建 / 取用 / 上电 / 拆除"** |
| 04 | ⚠️ **几乎没有子框架** ⇒ **改成"唯一那个子框架 + 三条边界"** |
| 06 | **正常写**（phy 是 DT 优先的子系统，159 个绑定文件） |
| 07 | **`/sys/class/phy/` 成为唯一出口**（因为 tracepoint/debugfs 全 0） |
| 99 §5 | **不需要第五层「对端不在本树」** |

### §1.3 官方文档登记（这是本子系统与 regmap 的最大差别）

**`Documentation/driver-api/phy/` 三个文件，共 352 行**：

| 文件 | 行数 | 读了吗 | 被吸收进 |
|---|---|---|---|
| **`phy.rst`** | **197** | **全文逐段读过** | **00 §2.1 / 01 §1.3 / §4.3 / §7.3 / 03 §C2 / 04 §5 / 05 §4.1 / 08 §3** |
| `index.rst` | 18 | **全文读过**（只是 toctree） | — |
| ⚠️ **`samsung-usb2.rst`** | **137** | ⚠️ **只读了各级标题，正文未细读** | **本节显式登记为"刻意未覆盖"**，理由见下 |

⚠️ **`samsung-usb2.rst` 为什么不覆盖**（对应 `check_doc_coverage.sh` 报的 0 引用，处置方式 (a)）：
**它讲的是"如何给 Samsung USB2 PHY 驱动添加一个新 SoC"** ——
**内容包括 `samsung_usb2_phy_config` 数组怎么填、compatible 怎么加、Makefile 怎么改。**
⇒ **这是单个厂商驱动的开发指南，不是框架知识**，
**与 00 §2.7 "163 个 PHY 驱动只讲契约"这条裁剪一致。**
**它对应的驱动是 `drivers/phy/samsung/phy-samsung-usb2.c` 等**（04 §6.2 只统计了实例数）。

**`phy.rst` 抓到的六条设计意图**（代码里读不出来，全部吸收进正文）：

| # | 意图 | 落在 |
|---|---|---|
| 1 | **框架动机是"把散落的 PHY 驱动收进 `drivers/phy`"** | 00 §2.1 |
| 2 | ⚠️ **只服务"外置 PHY"** | 01 §1.3 / 00 §1 |
| 3 | **provider 与实例分离，`of_xlate` 负责翻译** | 01 §1.1 / 08 §7 |
| **4** | **NULL 是合法 phy，所有调用变 NOP** | **01 §4.3 / 05 §4 / 08 §3**（⚠️ **含两个反例**） |
| 5 | **`phy_optional_get` 永不返回 `-ENODEV`** | 01 §4.1 |
| 6 | **PM runtime 父子传导；`power_on/off` 各做一次 get/put** | 03 §B4 / 01 §7.3 |

### §1.4 ⚠️ 官方文档与代码的四处不一致（全部以代码为准）

**完整表在 03 §C2，这里只记"怎么发现的"**：

| # | 不一致 | 发现方式 |
|---|---|---|
| 1 | **`phy_put` 参数个数（文档 1 个，代码 2 个）** | **写 05 §7.4 时 grep 签名**：`grep -n 'void phy_put' include/linux/phy/phy.h drivers/phy/phy-core.c` |
| ⚠️⚠️ **2** | **"所有 NULL 调用都是 NOP"有两个反例** | **逐个函数看 `if (!phy)` 分支时发现 `phy_configure`（`:508-509`）和 `phy_validate`（`:543-544`）返回 `-EINVAL`** |
| 3 | **`__devm_of_phy_provider_register_full` 不存在** | `grep -rn '__devm_of_phy_provider_register_full' --include=*.c --include=*.h .` ⇒ **0** |
| 4 | **`pm_runtime_enable` 是有条件的** | **读 `phy_create()` 全文时看到 `if (pm_runtime_enabled(dev))`（`:991`）** |

**另有两处注释/文档瑕疵**：
1. **`of_phy_simple_xlate()` 的 kerneldoc 写 `@args: … (not used here)`（`drivers/phy/phy-core.c:700`），
   但 `:716` 用了 `args->np`**（03 §A3.2 / 08 §7）。
2. **`phy.rst` 里有一行裸编号标题 `4. Getting a reference to the PHY`**（不是 rst 标题语法）
   **+ `drivers/phy/Kconfig:24` 的 help 有拼写错误 `helpers a core functions … to us.`**（04 §1.4）
   ⇒ **两者都支持"这块文档维护不勤，以代码为准"的判断。**

---

## §2 代码阅读范围与证据等级

### §2.1 一等证据：逐行读过

**判据：从函数开头读到结尾，引用时把源码原样搬进文档。**

| 文件 | 行数 | 详读范围 |
|---|---|---|
| **`drivers/phy/phy-core.c`** | **1244** | **全文**（38 个导出符号 + 5 个静态辅助 + 3 个 devres 回调 + `phy_release` + `phy_core_init` 全部逐行读过） |
| **`include/linux/phy/phy.h`** | **538** | **全文**（4 个结构体、`enum phy_mode`/`phy_media`、`union`、全部宏与 inline、`CONFIG_GENERIC_PHY=n` 的 43 个空桩） |
| `drivers/phy/Kconfig` | — | **全文**（16 个符号里的 7 个顶层 + 25 个 `source`） |
| `include/linux/phy/ulpi_phy.h` | **32** | **全文**（04 §3 整段引用） |
| `Documentation/driver-api/phy/phy.rst` | **197** | **全文**（§1.3） |
| `Documentation/devicetree/bindings/phy/phy-bindings.txt` | **72** | **全文**（06 §1 / §8） |
| `Documentation/ABI/testing/sysfs-platform-phy-rcar-gen3-usb2` | — | **全文**（07 §2.4） |

### §2.2 二等证据：只读了符号表 / 局部

| 文件 | 行数 | 读到什么程度 |
|---|---|---|
| `drivers/phy/phy-core-mipi-dphy.c` | **164** | **只读了两个函数的签名、导出方式（裸 `EXPORT_SYMBOL`）和职责**；⚠️ **时序公式没读**（04 §1.3） |
| `include/linux/phy/phy-mipi-dphy.h` | **284** | **只数了字段数（25）和两个函数声明的行号**；**逐字段语义没读** |
| `include/linux/phy/phy-dp.h` / `phy-lvds.h` / `pcie.h` | 95 / 32 / 12 | **只数了字段数（8 / 4）和判断了 `pcie.h` 不属于 union** |
| `drivers/phy/tegra/xusb.c` | — | **只读了 `:210` 和 `:228` 两处**（`phy_create` 与 `_full` 的唯一使用者，04 §5） |
| `drivers/phy/microchip/sparx5_serdes.c` · `drivers/phy/freescale/phy-fsl-lynx-28g.c` | — | **只读了 `PHY_MODE_ETHERNET` 的判断行**（08 §1） |
| `drivers/phy/renesas/phy-rcar-gen3-usb2.c` · `ti/phy-twl4030-usb.c` · `broadcom/phy-brcm-usb.c` | — | **只读了 `DEVICE_ATTR` 那几行**（07 §2.4） |
| `drivers/mfd/…` / `drivers/net/ethernet/stmicro/stmmac/…` 等 8 个 `phy-supply` 消费者 | — | **只读了 `regulator_get*(dev, "phy")` 那一行**（06 §4.2） |
| **其余 ~160 个 PHY 驱动** | **约 100000 行** | **只做了机械统计**（`phy_ops` 实例数、各宏的调用点数），**没有读任何实现** |

### §2.3 三等证据：官方文档

**读了哪些、哪些没读，见 §1.3 的表。**
⚠️ **四处文档与代码冲突，均以代码为准并在 03 §C2 显式指出。**
⚠️ **`samsung-usb2.rst`（137 行）刻意未覆盖**，理由见 §1.3。

### §2.4 代表驱动的选法

**`drivers/phy/` 有 205 个文件、103073 行，不可能全读。选法**：

| 挑的 | 为什么 |
|---|---|
| **`drivers/phy/tegra/xusb.c`** | **全树唯一用 `phy_create`（非 devm）和 `of_phy_provider_register_full` 的驱动**（04 §5） |
| **`drivers/phy/samsung/`（14 个）· `qualcomm/`（18 个）· `rockchip/`（12 个）** | **只用来统计分布**（01 §0.3）；**arm64 上最常见的三家** |
| **`drivers/phy/microchip/sparx5_serdes.c` · `freescale/phy-fsl-lynx-28g.c`** | **证明"通用 PHY 也服务网口"这一条**（08 §1） |
| **三个带 `DEVICE_ATTR` 的驱动** | **证明"框架 0 个 sysfs 属性，但驱动可以自己加"**（07 §2.4） |
| **arm64 DTS**：`rockchip/rk3399.dtsi` · `rockchip/rk3399-puma-haikou.dts` · `rockchip/rk3328-rock-pi-e.dts` · `ti/k3-am65-main.dtsi` · `xilinx/zynqmp.dtsi` | **分别覆盖"子节点布局 / 真实 `phy-supply` / ⚠️ 网络侧的 `phy-supply` 反例 / `#phy-cells=<2>` / `#phy-cells=<4>`"** |

**其余驱动只讲契约，不讲实现。**

### §2.5 裁掉的范围登记（arm64-only 与 ACPI）

⚠️ **这些不是遗漏，是显式裁掉的**（00 §2.7 是面向读者的版本，本节是取证版）：

| 裁掉的 | 判据 | 代码上是否有独立逻辑 |
|---|---|---|
| **ACPI 路径** | **arm64-only 约定** | **`phy-core.c` 里 0 处 ACPI 相关代码**（重跑 `grep -ic 'acpi' drivers/phy/phy-core.c` ⇒ 0）⇒ **框架完全不支持 ACPI 枚举 PHY**；**非 DT 场景走的是 lookup 表（01 §8），不是 ACPI** |
| **x86 / MIPS / powerpc 专有驱动** | 同上 | **`phy-pistachio-usb.c`（MIPS）· `USB_LGM_PHY`（x86）· `phy-lpc18xx-usb-otg.c`（ARM32）等被裁掉**；**框架核心本身与架构无关**（`grep -ic 'CONFIG_X86\|CONFIG_ARM64' drivers/phy/phy-core.c` ⇒ 0） |
| **PREEMPT_RT 下的 mutex 语义** | **本树改过抢占相关头文件（§1.1），但未在 RT 配置下运行** | **phy 全框架 0 个 spinlock、只用 mutex**（`grep -c 'spin_lock\|irqsave' drivers/phy/phy-core.c` ⇒ 0）⇒ **RT 上 mutex 行为变化会影响 `ops` 回调的执行上下文**（§5.4） |

---

## §3 符号存在性核查

**判据两条，必须同时满足才算"读者能用"**（skill 规则 20）：
**① `EXPORT_SYMBOL*`（或 `static inline` / 宏）；② 公共头 `include/linux/phy/phy.h` 里有声明。**

### §3.1 核查过的符号

| 类别 | 结论 |
|---|---|
| **40 个导出符号** | **逐个核对过所在文件与导出行**（§6 表 3） |
| **`static inline` 与宏** | **`to_phy`(`:194`) · 四个 provider 注册宏(`:196-206`) · `phy_set_drvdata`(`:208`) · `phy_get_drvdata`(`:213`) · `phy_set_mode`(`:230`) · `phy_get_mode`(`:238`) · `phy_get_bus_width`(`:244`) · `phy_set_bus_width`(`:248`)** |
| **枚举值** | **`enum phy_mode` 20 个（`:25-46`）· `enum phy_media` 3 个（`:48-52`）** ——**05 篇用到的 `PHY_MODE_USB_HOST` / `PHY_MODE_MIPI_DPHY` 都核对过** |
| **MIPI 辅助** | **`phy_mipi_dphy_get_default_config`（声明 `include/linux/phy/phy-mipi-dphy.h:278`）· `phy_mipi_dphy_config_validate`（`:282`）**；**05 §5.2 用到的 `hs_prepare` 字段在 `:143`** |
| **05 篇里的非 phy 符号** | **`dev_err_probe`（`include/linux/device.h:1108`）· `PTR_ERR_OR_ZERO`（`include/linux/err.h`）· `devm_platform_ioremap_resource` · `clk_prepare_enable`** ——**全部确认存在** |
| **内核打印的字符串** | **07 §4.2 的 13 条消息全部逐字 grep 过**（含"两条字面完全相同"这个结论） |

### §3.2 ⚠️ 抓到的三个"存在性"问题

| # | 问题 | 详情 | 处置 |
|---|---|---|---|
| **1** | **`__devm_of_phy_provider_register_full` 不存在** | **官方文档写了它，全树 grep 命中 0** | **01 §3.2 / 03 §C2 / 04 §5 三处点明** |
| **2** | **`of_phy_provider_register`（非 devm）在树里 0 个使用者** | **`phy-core.c` 的 3 处命中全是 kerneldoc**（`:1177` `:1179` `:1197`） | **04 §5 登记为死字** |
| **3** | **`devm_of_phy_provider_register_full` 也是 0 使用者** | 同上 | 同上 |

⚠️ **`phy_put` 的签名问题不属于"存在性"，属于"文档过时"**（§1.4 第 1 条）。

### §3.3 计数时排掉注释的做法

**本套文档所有"调用点数"都用这个模板**：
```
grep -rnE "(^|[^_a-z])<符号>\(" --include=*.c . | grep -vE '^\S+: *\*|/\*|\* ' | wc -l
```
⚠️ **不排注释的话，`of_phy_provider_register` 会从 0 变成 1**（04 §6.2 口径警告 1）。
⚠️ **正则太松会把驱动的局部函数算进来**：
**`drivers/phy/intel/phy-intel-lgm-combo.c:535` 的 `intel_cbphy_create()` 会被
`[^_]phy_create\(` 命中**（04 §6.2 口径警告 2）。

---

## §4 索引工具（cbm）的盲区

**本套文档全程只用 `rg` + `Read` 取证，索引工具只用于找候选。**

| 盲区 | 在 phy 里的具体表现 |
|---|---|
| **宏生成/包装的符号** | **四个 provider 注册宏（`include/linux/phy/phy.h:196-206`）在 `.c` 里没有定义** ⇒ **搜 `devm_of_phy_provider_register` 找不到函数体** |
| **`phy_set_mode` 是宏** | **`:230`（和空桩段的 `:360`）** ⇒ **调用图上断开，实际落在 `phy_set_mode_ext` 上** |
| **函数指针间接调用** | **`ops->init` / `ops->power_on` / `of_xlate` 全是间接调用** ⇒ **"谁实现了 `init`"必须靠 grep `struct phy_ops` 实例（167 个）** |
| **`CONFIG_GENERIC_PHY` 的双份声明** | **同一个函数名在头文件里出现两次**（真声明 `:236-284` / 空桩 `:286-535`）⇒ **grep 行号必须区分两段**（01 §9.3） |
| ⚠️ **同名子系统干扰** | **搜 `phy_init` 会同时命中 `drivers/net/phy/` 的东西** ⇒ **本套文档所有 grep 都限定了目录或用了全路径**（02 §1.0.A） |

---

## §5 ⚠️ 未验证项（四层）

### §5.1 第一层：未编译、未运行

| 项 | 说明 |
|---|---|
| **05 篇全部代码骨架** | **一行都没编译过**；符号存在性核对过（§3），**但错误路径的完整性、Kconfig 依赖、厂商寄存器名（`FOO_PHY_CTRL` 等是假名）都没有验证** |
| **07 篇全部命令** | **没有在真机上跑过。**`/sys/class/phy/` 下的文件列表是从 `class`/`device` 的通用行为推的，**不是观察到的** |
| **07 §3.1 的三种 ftrace / kprobe 办法** | **完全未实测** ——**只是通用 ftrace 用法** |
| **07 §4.3 的 `VERBOSE_DEBUG` 说法** | **未实测**；**`dev_vdbg` 需要编译期宏是内核通用规则** |
| **06 篇的 DTS 片段** | **真实节点全部逐字取自 in-tree 文件并给了行号**；**05 §1.3 / §2.2 / §2.3 的示例节点是构造的（用了 `foo,` 前缀）** |
| **07 §6 的"DT 侧注入"** | **未实测** ——**但依据是 `drivers/phy/phy-core.c:593-596` 的真实代码** |

### §5.2 第二层：只读了符号表

**完整清单见 §2.2。最要紧的三处**：

| 项 | 后果 |
|---|---|
| **MIPI D-PHY 的时序公式（`phy-core-mipi-dphy.c` 的 164 行）** | **04 §1 只说"它算默认值、它校验"** ⇒ **调 MIPI 时序问题时本文档帮不上忙** |
| **`phy_configure_opts_*` 三个结构的逐字段语义** | **04 §2.1 只给了字段数** |
| **~160 个 PHY 驱动的实现** | **只有机械统计**；⇒ **"这颗 PHY 的 `init` 到底做了什么"必须自己读驱动**（03 §C5 的结论） |

### §5.3 ⚠️ 第三层：推断而非观察

| 论断 | 为什么是推断 | 风险 |
|---|---|---|
| **"`/sys/class/phy/<name>/` 下有 `device` / `of_node` / `power/` / `consumer:` 等"** | **由 `dev.parent`(`:968`)、`dev.of_node`(`:969`)、`pm_runtime_enable`(`:992`)、`device_link_add`(`:766`) 推出的驱动核心通用行为** ——**没有实机 `ls` 过** | **中**（07 §2.2） |
| **"`DL_FLAG_STATELESS` 只影响 suspend/resume/shutdown 顺序"** | **来自驱动模型的一般知识，没有读 `device_link_add` 的实现** | **中**（03 §B5 已标注） |
| **"`try_module_get(NULL)` 不返回成功 ⇒ 漏填 `.owner` 导致 `-EPROBE_DEFER`"** | ⚠️ **没有读 `try_module_get` 对 NULL 的处理，也没有实测** | ⚠️ **偏高** ——**05 §7.5 / 07 §2.3 都用了这条**；**要用之前建议自己确认一遍** |
| **"`regmap-mmio` 式的 64 位分支到不了"类比在 phy 上不存在** | — | — |
| **"arm64 DTS 里 149 个 `phy-supply` 有相当一部分属于网络子系统"** | **只找到了 1 个确凿反例（`rk3328-rock-pi-e.dts:151`）+ 8 个消费者的代码位置** ——⚠️ **没有逐个分类那 149 个文件** | **中**（06 §4.2 已标注） |
| **"`devm_phy_create` 必须早于 `devm_of_phy_provider_register`"** | **由 `of_phy_simple_xlate()` 的实现推出**；**另外看过树里驱动普遍是这个顺序，但没有穷举** | **低**（03 §0.2） |
| **"PHY 驱动都是 platform driver，所以 `device_initcall` 够用"** | **由"绝大多数在 `drivers/phy/` 下用 `platform_driver`"推的，没有穷举** | **低**（03 §A1） |

### §5.4 第四层：版本时效性

| 项 | 说明 |
|---|---|
| **所有行号只对 `6.1.158` 有效** | **`phy-core.c` 1244 行，任何 backport 都会位移** |
| **`class_create(THIS_MODULE, "phy")` 的两参数形式** | ⚠️ **更高版本内核把 `class_create()` 改成了单参数** ⇒ **`drivers/phy/phy-core.c:1233` 抄到新内核上编不过**（本文档只描述 6.1） |
| **`ida_simple_get` / `ida_simple_remove`** | **更高版本推荐 `ida_alloc`/`ida_free`** ⇒ **6.1 里 phy 还在用老接口**（`:957` / `:1227`） |
| ⚠️ **PREEMPT_RT 相关** | **本树改过 `preempt.h`（§1.1）** ⇒ **phy 只用 mutex（0 个 spinlock），RT 上 mutex 的睡眠语义变化会影响 `ops` 回调的执行环境** ——**本文档未在 RT 配置下验证**（§2.5） |
| **官方文档的四处过时** | **说明这块文档更新滞后于代码**（§1.4）⇒ **看更高版本时要重新核对，不能沿用本文档的结论** |

⚠️ **本子系统不需要"第五层：对端 / 树外实现"** ——
**phy 是框架型，没有跨边界 ABI。**
**唯一的"树外"是 MIPI D-PHY / DP / LVDS 规范**，**而本文档没有引用任何规范条文**（§5.2）。

---

## §6 可重跑的数字

⚠️ **口径警告先行**：**每个数字后面的"口径"一列比数字本身更重要。**

### 表 1 规模

| 项 | 值 | 命令 | 口径 |
|---|---|---|---|
| `drivers/phy/` 文件数 | **205** | `find drivers/phy -name '*.c' -o -name '*.h' \| wc -l` | **含 `.h`，含所有子目录** |
| `drivers/phy/` 总行数 | **103073** | `find drivers/phy -name '*.c' -o -name '*.h' \| xargs cat \| wc -l` | 同上 |
| **核心行数** | **1244 + 164 = 1408** | `wc -l drivers/phy/phy-core.c drivers/phy/phy-core-mipi-dphy.c` | **占目录 1.4%** |
| `include/linux/phy/` | **9 个头 / 1125 行** | `wc -l include/linux/phy/*.h` | **不含 `include/linux/phy/tegra/` 子目录** |
| 主头文件 | **538** | `wc -l include/linux/phy/phy.h` | — |
| **`drivers/usb/phy/`（对照）** | **20 个 `.c` / 10895 行** | `ls drivers/usb/phy/*.c \| wc -l` · `cat drivers/usb/phy/*.c \| wc -l` | 04 §4.1 |

### 表 2 结构体字段数

| 结构 | 值 | 命令 |
|---|---|---|
| `struct phy` | **8** | `awk 'NR>=153 && NR<=160' include/linux/phy/phy.h \| grep -c ';'` |
| `struct phy_ops` | **12** | **逐个数过**（`:85-126`，11 个函数指针 + 1 个 `owner`；中间夹了 kerneldoc 段） |
| `struct phy_attrs` | **3** | `awk 'NR>=136 && NR<=138' include/linux/phy/phy.h \| grep -c ';'` ——⚠️ **上界必须是 `138` 而不是 `139`**：**`:139` 的 `};` 里也有分号，写成 139 会得到 4**（定稿前的逐格对账抓到的） |
| `struct phy_provider` | **5** | **逐个数过**（`:172-177`，`of_xlate` 跨两行） |
| `struct phy_lookup` | **4** | `awk 'NR>=188 && NR<=191' include/linux/phy/phy.h \| grep -c ';'` |
| `phy_configure_opts_mipi_dphy` | **25** | `awk '/^struct phy_configure_opts_mipi_dphy \{/{f=1} f&&/^\};/{exit} f' include/linux/phy/phy-mipi-dphy.h \| grep -c ';'` |
| `phy_configure_opts_dp` | **8** | 同上换文件/结构名 |
| `phy_configure_opts_lvds` | **4** | 同上 |

⚠️ **`phy_ops` 和 `phy_provider` 不能用 `grep -c ';'`** ——
**前者中间夹了 kerneldoc 注释段（`:93-102` 等），后者的 `of_xlate` 声明跨两行。**
⇒ **这两个是逐个数的，不是脚本数的**（这一条本身就是一条口径纪律）。

### 表 3 导出符号（40）

| 文件 | 数 | 导出方式 |
|---|---|---|
| `drivers/phy/phy-core.c` | **38** | **全部 `EXPORT_SYMBOL_GPL`** |
| `drivers/phy/phy-core-mipi-dphy.c` | **2** | ⚠️ **裸 `EXPORT_SYMBOL`（非 GPL）** |

重跑：`grep -c 'EXPORT_SYMBOL' drivers/phy/phy-core.c` ⇒ **38**；
`grep -n 'EXPORT_SYMBOL' drivers/phy/phy-core-mipi-dphy.c` ⇒ **2 行，都是 `EXPORT_SYMBOL(`**。

### 表 4 `phy_ops` 实例与调用点

| 项 | 值 | 命令 / 口径 |
|---|---|---|
| **`struct phy_ops` 实例（全树）** | **167** | `grep -rhoE 'struct phy_ops [a-z_0-9]+ = \{' --include=*.c . \| wc -l` |
| **（`drivers/phy/` 内）** | **163** | 同上限定目录 |
| ⚠️ **（目录外）** | **4，分布在 3 个文件** | `drivers/pinctrl/tegra/pinctrl-tegra-xusb.c`（**2**）· `drivers/media/platform/sunxi/sun8i-a83t-mipi-csi2/sun8i_a83t_dphy.c`（1）· `drivers/gpu/drm/rockchip/dw-mipi-dsi-rockchip.c`（1） |
| **`devm_phy_create`** | **152** | §3.3 的模板 |
| ⚠️ **`phy_create`（非 devm）** | **1**（`drivers/phy/tegra/xusb.c:210`） | 同上 |
| **`devm_of_phy_provider_register`** | **144** | 同上 |
| **`of_phy_provider_register_full`** | **1**（`drivers/phy/tegra/xusb.c:228`） | 同上 |
| ⚠️ **`of_phy_provider_register` / `devm_of_phy_provider_register_full`** | **各 0** | **死字**（04 §5） |
| **`phy_create_lookup`** | **7 处调用 + 1 处定义** | **调用分布：`marvell/phy-pxa-usb.c` 3 · `ti/phy-da8xx-usb.c` 2 · `mediatek/phy-mtk-dp.c` 1 · `ti/phy-twl4030-usb.c` 1** |
| **`phy/ulpi_phy.h` 的包含者** | **1**（`drivers/phy/ti/phy-tusb1210.c`） | `grep -rln 'phy/ulpi_phy.h' --include=*.c .` |

### 表 5 错误码普查

| 码 | 值 |
|---|---|
| `-EINVAL` | **31** |
| `-ENODEV` | **11** |
| `-ENOTSUPP` | **11** |
| `-ENOMEM` | 8 |
| `-EPROBE_DEFER` | **7** |
| `-EOPNOTSUPP` | **2** |
| `-EBUSY` / `-EIO` / `-ERANGE` / `-EAGAIN` / `-ENOSYS` | **全 0** |

重跑：`for e in EINVAL ENODEV ENOTSUPP ENOMEM EPROBE_DEFER EOPNOTSUPP EBUSY EIO ERANGE EAGAIN ENOSYS; do echo -n "-$e "; grep -rho -- "-$e" drivers/phy/phy-core.c drivers/phy/phy-core-mipi-dphy.c | wc -l; done`

⚠️ **口径三条**：
1. **范围显式限定两个核心 `.c`**，**不含头文件的空桩段** ⇒ **`-ENOSYS` 在这里是 0，在头文件里是 25**（01 §9.3）。
2. **`grep -rho` 数的是出现次数，不是返回点数。**
3. **`-ENOTSUPP` 与 `-EOPNOTSUPP` 是两个不同的串，子串不互相计入。**

### 表 6 空桩段（`CONFIG_GENERIC_PHY=n`）

| 项 | 值 | 命令 |
|---|---|---|
| **范围** | **`include/linux/phy/phy.h:286-535`**（`:285` 是 `#else`，`:536` 是 `#endif`） | `grep -n '#else\|#endif' include/linux/phy/phy.h` |
| `return 0` | **16** | `awk 'NR>=286 && NR<=535' include/linux/phy/phy.h \| grep -oE 'ERR_PTR\(-[A-Z]+\)\|return -[A-Z]+\|return 0' \| sort \| uniq -c \| sort -rn` |
| `return -ENOSYS` | **14** | 同上 |
| `ERR_PTR(-ENOSYS)` | **11** | 同上 |
| `return -ENODEV` | 2 | 同上 |
| **`WARN` 数** | **0** | `awk 'NR>=286 && NR<=535' include/linux/phy/phy.h \| grep -c 'WARN'` |

### 表 7 告警与打印

| 项 | 值 | 口径 |
|---|---|---|
| **打印语句所在行数** | **16** | `grep -c 'pr_err\|dev_err\|dev_warn\|dev_dbg\|dev_vdbg\|dev_WARN' drivers/phy/phy-core.c` |
| **分级** | **1 `pr_err` · 5 `dev_err` · 2 `dev_warn` · 1 `dev_WARN` · 3 `dev_WARN_ONCE` · 3 `dev_dbg` · 1 `dev_vdbg`** | **逐行看过**（07 §4.1） |
| ⚠️ **裸 `WARN_ON(`** | **1**（`drivers/phy/phy-core.c:950`） | ⚠️ **`grep -c 'WARN_ON'` 返回 4，因为 `dev_WARN_ONCE` 里含同一个子串** |
| **`BUG_ON`** | **0** | `grep -c 'BUG_ON' drivers/phy/phy-core.c` |
| **重复的消息** | **`couldn't find PHY resource` 出现 2 次**（`:693` `:1066`）· **`failed to create device link to %s` 出现 3 次**（`:768` `:881` `:928`） | **逐字比对过** |

### 表 8 可观测性（全 0 的那些）

| 项 | 值 | 命令 |
|---|---|---|
| **tracepoint** | **0** | `grep -rc 'trace_' drivers/phy/phy-core.c` ⇒ 0；**`ls drivers/phy/*trace*` 无文件** |
| **debugfs（整个目录）** | **0 个文件提到** | `grep -rl 'debugfs' drivers/phy/ \| wc -l` |
| **框架级 sysfs 属性** | **0** | `grep -c 'DEVICE_ATTR\|ATTRIBUTE_GROUPS\|dev_groups\|class_groups' drivers/phy/phy-core.c` |
| **驱动级 `DEVICE_ATTR`** | **3 个文件 / 4 个属性** | `grep -rl 'DEVICE_ATTR' drivers/phy/`（07 §2.4） |
| **故障注入** | **0** | `grep -rniE 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR' drivers/phy/` 无输出 |
| **模块参数** | **1**（厂商驱动的，`drivers/phy/phy-xgene.c:548`） | `grep -rn 'module_param' drivers/phy/` |
| **设计债标记** | **0** | `grep -nE '(FIXME\|TODO\|REVISIT\|HACK\|\bXXX\b)' drivers/phy/phy-core.c drivers/phy/phy-core-mipi-dphy.c` 无输出 |
| **spinlock / irqsave** | **0** | `grep -c 'spin_lock\|irqsave' drivers/phy/phy-core.c` |
| **`of_device_is_compatible`** | **1**（`:581`，`usb-nop-xceiv` 特例） | `grep -c 'of_device_is_compatible' drivers/phy/phy-core.c` |
| **`devres_alloc`** | **5** | `grep -c 'devres_alloc' drivers/phy/phy-core.c` |
| **`device_link_add`** | **3**（`:766` `:879` `:926`） | `grep -c 'device_link_add' drivers/phy/phy-core.c` |
| **Kconfig 里 `debug`** | **0** | `grep -ic 'debug' drivers/phy/Kconfig` |

### 表 9 arm64 DTS

| 属性 | 文件数 | 行数 | 备注 |
|---|---|---|---|
| `phys = ` | **149** | **558** | — |
| `phy-names` | **130** | **399** | — |
| `#phy-cells` | **108** | **425** | — |
| ⚠️`phy-supply` | **149** | **288** | ⚠️ **混了网络子系统的用法，未逐个分类**（§5.3） |
| **`#phy-cells` 取值分布** | — | **`<0>` 330 · `<1>` 92 · `<2>` 2 · `<4>` 1** | `grep -rhoE '#phy-cells = <[0-9]+>' arch/arm64/boot/dts/ \| sort \| uniq -c \| sort -rn` |

### 表 10 其他

| 项 | 值 | 命令 |
|---|---|---|
| **Kconfig 符号数** | **16** | `grep -c '^config ' drivers/phy/Kconfig` |
| **`source` 行数（厂商目录）** | **25** | `grep -c '^source ' drivers/phy/Kconfig` |
| **厂商子目录的 `.c` 分布** | qualcomm 18 · samsung 14 · marvell 14 · broadcom 13 · rockchip 12 · mediatek 11 · ti 10 · amlogic 9 · hisilicon 7 · … | `for d in drivers/phy/*/; do echo -n "$d "; find $d -name '*.c' \| wc -l; done \| sort -k2 -rn` |
| **DT 绑定文件数** | **159** | `ls Documentation/devicetree/bindings/phy/ \| wc -l` |
| **`phy-supply` 的全树消费者** | **8** | `grep -rn '_regulator_get.*, *"phy")' --include=*.c drivers/` |

---

## §7 三个批量自检脚本的结果

| 脚本 | 结果 | 说明 |
|---|---|---|
| `check_line_refs.py` | **干净** | **扫描约 180 条唯一 `文件:行号` 引用，无解析不到的文件、无越界行号、无 AMBIGUOUS、无 UNRESOLVED、忽略项 0 条** |
| `check_xrefs.py` | **干净** | **跨篇 `0X §Y` 引用全部命中，忽略项 0 条** |
| ⚠️`check_doc_coverage.sh` | ⚠️ **报 2 篇必查档，其中 1 篇 0 引用** | **`phy.rst` 有引用（但计数虚高，见下）；`samsung-usb2.rst` 0 引用，已在 §1.3 按处置方式 (a) 显式登记** |

**忽略文件**：**本文档集不需要 `.xref-ignore` / `.lineref-ignore`。**

⚠️ **`check_doc_coverage.sh` 的两个口径问题（本子系统特别明显）**：
1. **它报 `phy.rst` "引用 2683 次"** ——**这个数字没有意义**：
   **脚本按文件名去正文里数，而"phy"是本文档集里每一句话都会出现的词。**
   ⇒ **它只能证明"不是 0"，不能证明"覆盖得好"。**
2. **它把 114 篇 DT 绑定列为"参考、被引用 0 篇"** ——**这是预期的**：
   **06 篇的硬要求是"每条属性都有定位到行的消费者"，不是"每篇绑定都被引用"**（06 §1）。

⚠️ **本节刻意只描述结果、不照抄脚本失败行的格式**（skill 规则 28 的 条）。

---

## §8 逐篇取证表

### 00-overview.md

| 论断 | 出处 |
|---|---|
| 框架型 + class 型 | §1.2 两道门 + 补充判据的实际输出 |
| 205 文件 / 103073 行 / 核心 1408 行 / 40 导出 | §6 表 1、表 3 |
| 一层可插拔 | 01 §1.2（一等证据：头文件全文） |
| 四处文档不一致 | §1.4 / 03 §C2 |
| 端到端路径每一步行号 | §2.1 的详读范围（`phy-core.c` 全文） |

**自审修正**：**无内容修正**（本篇最后写，是对已定稿内容的浓缩）。

### 01-api.md

| 论断 | 出处 |
|---|---|
| 两个 phy 子系统的对照 | 一等证据（两边头文件路径 + Kconfig 名 + 官方文档路径都核对过） |
| 40 个导出符号分五组 | §6 表 3 |
| 12 个 `phy_ops` 回调 | 一等证据（`include/linux/phy/phy.h:84-127` 全文） |
| **NULL 的九个安全 API + 两个反例** | **逐个函数看 `if (!phy)` 分支** |
| 43 个空桩的返回值分布 | §6 表 6 |

⚠️ **自审修正（两条）**：
**① `release` 回调原写成"`struct device` 释放时调"，改为"每次 `of_phy_put()` 时调"**
（唯一调用点 `drivers/phy/phy-core.c:654-655`，§9 实例 1）；
**② §9.3 原写"具体返回值没有逐个核对"，补读后升级为完整的 43 个空桩分布表**（证据等级从推断升到一等）。

### 02-datastructures.md

| 论断 | 出处 |
|---|---|
| 六组易错关系 | 一等证据（头文件 + `phy-core.c` 全文） |
| **两个计数无下溢保护** | `drivers/phy/phy-core.c:305` / `:388` 逐行 |
| 三层电源 | `:329-343` 逐行 |
| 五个全局变量 + 一把 per-phy 锁 | `:22-26` + `include/linux/phy/phy.h:156` |
| ⚠️ 全局链表 `phys` 装的是 lookup | `:25` 定义 + `:80` 插入 |

⚠️ **自审修正（一条）**：
**`phy_provider.children` 原写成"普通版是 NULL"，改为"永远不为 NULL"**
（`drivers/phy/phy-core.c:1116-1118` 把 NULL 替换成 `dev->of_node`，§9 实例 2）——
**并补上了 `_full` 版的后代校验（`:1100-1113`）。**

### 03-internals.md

| 论断 | 出处 |
|---|---|
| `phy_create()` 九步 | `drivers/phy/phy-core.c:943-1005` 逐行 |
| **PM runtime 是有条件启用的** | `:991-994`（§9 实例 3） |
| 四层引用计数 | `:588`/`:602` · `:761`/`:658` · `:764`/`:659` · 两个 count |
| 三个 devres 回调 | `:28-47` 全文 |
| 四处文档不一致 + 三类静默失效 + 三个可质疑设计 | Part C，每条都有行号 |

⚠️ **自审修正（两条）**：
**① `devres_alloc` 模式原写"出现 5 次"却列了 6 个函数名，核对为 5 处，并说明
`devm_phy_optional_get` 是 `devm_phy_get` 的包装**（§9 实例 4）；
**② "全框架唯一的 `WARN_ON`"补上了口径说明**（`grep -c` 返回 4 是因为子串包含）。

### 04-extensions.md

| 论断 | 出处 |
|---|---|
| 唯一子框架 + 三条边界 | 一等/二等混合（§2.1 / §2.2 已分开登记） |
| **两个 MIPI 导出符号是裸 `EXPORT_SYMBOL`** | §6 表 3 |
| **三个死字宏** | §6 表 4（排掉注释的口径） |
| ULPI 桥全文 + 唯一使用者 | `include/linux/phy/ulpi_phy.h` 全文 + `grep -rln` |
| `drivers/usb/phy/` 规模 | §6 表 1 |

⚠️ **自审修正（一条）**：
**`phy_create_lookup` 调用点原写 7 并把 `phy-core.c` 列进分布，核对为
"7 处调用 + 1 处定义 = grep 命中 8"，并改正了分布中各文件的次数**（§9 实例 5）。

### 05-usage.md

| 论断 | 出处 |
|---|---|
| 六个角色 | 由"谁造 PHY / 谁用 PHY"切分 |
| 每个骨架里的符号都存在 | §3.1 |
| 七组近邻符号 | 一等证据（头文件 + `phy-core.c`） |
| **NULL 的两个反例的正确写法** | `drivers/phy/phy-core.c:508-509` / `:543-544` |
| MIPI 骨架的函数签名与字段名 | **`include/linux/phy/phy-mipi-dphy.h:278-281`（签名）· `:143`（`hs_prepare`）** |

**自审修正**：**无内容修正**（符号存在性核查一次通过，含 `dev_err_probe` / `PTR_ERR_OR_ZERO`）。

### 06-devicetree.md

| 论断 | 出处 |
|---|---|
| 三条发现路径 | `drivers/phy/phy-core.c:744-757` 的分支 |
| 四个属性 + 各自消费者行号 | 逐属性 grep 过 |
| **`of_property_match_string` 的负数没被检查** | `:746-750` 逐行（§9 实例 6） |
| 两种 DT 布局 + `children` 的作用 | `:137-144` + `:1116-1118` |
| ⚠️ **`phy-supply` 的 8 个消费者 + 一个网络侧反例** | `grep -rn '_regulator_get.*, *"phy")'` + `arch/arm64/boot/dts/rockchip/rk3328-rock-pi-e.dts:145-151` 上下文 |
| 八层决策树 | 每层都有行号 |
| arm64 统计 | §6 表 9 |

**自审修正**：**无内容修正。**
⚠️ **但留下一个未完成的统计**：**149 个 `phy-supply` 文件未逐个分类**（§5.3）——
**正文已就地标注警告，不当成已完成。**

### 07-debug.md

| 论断 | 出处 |
|---|---|
| 四个"全 0"的出口 | §6 表 8 |
| `/sys/class/phy/` 里能看什么 / 看不到什么 | **`:967-969` `:973` `:957` `:766` 推出**（部分是推断，§5.3） |
| 16 处打印的分级与逐字消息 | §6 表 7 |
| 一个 `WARN_ON` / 零 `BUG_ON` | §6 表 7 |
| **`devices_deferred` 的两步分诊** | **`drivers/base/dd.c:346` 确认文件存在** |
| 错误码普查与三类 `-EINVAL` | §6 表 5 |

**自审修正**：**无内容修正**（四个计数当场核对一致：`BUG_ON` 0 · 打印 16 · Kconfig `debug` 0 · 空桩 `ENOSYS` 25）。

### 08-feynman.md

| 论断 | 出处 |
|---|---|
| 八个主题的每个技术断言 | **全部带行号，取自 §2.1 的详读范围** |
| **两个 phy 撞名 + 一个真实交叉点** | `include/linux/phy/phy.h:41` + `drivers/phy/microchip/sparx5_serdes.c:2167` + `drivers/phy/freescale/phy-fsl-lynx-28g.c:388` |
| NULL 的两个反例 | `:508-509` / `:543-544` |
| `set_mode` 写脏 `attrs.mode` | `:407-410` + `include/linux/phy/phy.h:238-241` |
| `of_phy_simple_xlate` 不看 cell 值 | `:713-721` |

**自审修正（一条）**：
**§1 的"树里的真实例子"原先只给了文件名，补上了两个文件里 `PHY_MODE_ETHERNET` 判断的行号
和 `phy_ops` 实例数**（证据从"文件级"升到"行级"）。

### 99-evidence.md（本篇）

**自审修正**：**§6 的每一格都逐条重跑对账过**（skill 规则 30 的 条），
**抓到两个口径问题：`phy_ops` / `phy_provider` 的字段数不能用 `grep -c ';'`（kerneldoc 夹在中间、声明跨行）；
`grep -c 'WARN_ON'` 会把 `dev_WARN_ONCE` 算进来。**

---

## §9 交叉验证实例（初始假设被推翻的七条）

| # | 初始假设 | 推翻它的证据 | 落到哪 |
|---|---|---|---|
| **1** | **"`ops->release` 是设备销毁时的清理回调"**（凭名字推的） | `grep -n '\->release' drivers/phy/phy-core.c` ⇒ **唯一调用点是 `of_phy_put()` 的 `:654-655`**；**`phy_release()`（`:1220-1229`）根本不调它** | **01 §2 改写**；**03 §A7.3 单独一节** |
| **2** | **"`phy_provider->children` 在普通注册版里是 NULL"** | **读 `__of_phy_provider_register()` 全文，`:1116-1118` 把 NULL 换成 `dev->of_node`** | **02 §5.4 改写 + 补 `_full` 的后代校验** |
| **3** | **"phy 创建时一定启用 PM runtime"**（官方文档这么说的） | **`:991` 有前提 `if (pm_runtime_enabled(dev))`** | **03 §A2.1 单独一节 + 列为文档不一致第 4 条** |
| **4** | **"六个 devm 函数各有一次 `devres_alloc`"** | `grep -c 'devres_alloc'` ⇒ **5**；**`devm_phy_optional_get`（`:839-847`）是包装，没有自己的 devres** | **03 §A3.3 改数 + 补说明** |
| **5** | **"`phy_create_lookup` 有 7 个调用点，分布在 4 个驱动 + phy-core"** | **按文件计次后发现是"7 处调用（marvell 3 / da8xx 2 / mtk-dp 1 / twl4030 1）+ 1 处定义"** | **04 §6.2 改写口径** |
| **6** | **"`phy-names` 拼错会有明确错误"** | **`:746-750` 把 `of_property_match_string` 的负数直接当下标用，没有 `if (index < 0)`** ⇒ **和"没有这个属性"完全一样，都是 `-ENODEV`** | **06 §3.1 / §5 第 ② 层 / 08 §3** |
| **7** | **"`phy-supply` 是通用 PHY 框架的属性"** | `grep -rn '_regulator_get.*, *"phy")'` ⇒ **8 个消费者，5 个是网络驱动**；**`arch/arm64/boot/dts/rockchip/rk3328-rock-pi-e.dts:151` 是挂在 `&gmac2io` 上的确凿反例** | **06 §4.2 单独一节 + §8 死字表 + 08 §1** |

**三条方法论结论**：
1. **"凭回调名字推它什么时候被调"是高发错误**（实例 1）——**必须 grep 调用点。**
2. **官方文档越像"设计文档"，越要逐条回代码核**（实例 3；四处不一致里三处都是这样发现的）。
3. **"某个字段在某种用法下是 NULL"这类假设必须读到赋值点**（实例 2）——
   **中间可能有一次替换。**

**一条反例（说明结论的方向）**：
**05 篇的符号存在性核查一次通过，40 个导出符号 + 全部宏/inline + 三个非 phy 符号
（`dev_err_probe` / `PTR_ERR_OR_ZERO` / `hs_prepare`）无一处幻觉。**
⇒ **说明"每个符号写进文档前先 grep"这条纪律是有效的，不是运气**（对比 regmap 那轮抓到的 `REGCACHE_MAPLE`）。

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统没有对照套

**`output/qoder/` 下没有 phy 目录**（对照套只覆盖了 spi）
⇒ **本套文档没有做过独立双套 diff，§9 那七条全部来自自审。**

**读者若另有一套 phy 文档，最值得对照的四处**：
1. **`ops->release` 的调用时机**（本套认为是"每次 `of_phy_put`"，§9 实例 1）；
2. **官方文档的四处不一致**（本套全部以代码为准，§1.4）；
3. **`phy-supply` 归谁**（本套认为要按 `#phy-cells` 判断，§9 实例 7）；
4. **`try_module_get(NULL)` 那条推断**（本套标为"偏高风险"，§5.3）。

### §10.2 要回写 skill 的两条

| # | 内容 | 为什么 |
|---|---|---|
| **1** | **在 02 §1.0 的要求里加一条：「子系统名字与别的子系统撞车时，第一组关系必须是这个」** | **phy 的头号混淆不是内部关系，是"内核里有两个 phy"**（01 §0.1 / 02 §1.0.A / 08 §1）——**skill 现在的模板假设易错点都在子系统内部** |
| **2** | **在规则 6（读官方文档）里补一句：「有概念文档的子系统要专门列一张 doc-vs-code 差异表」** | **phy 有 197 行的正式文档，却有四处过时**（§1.4）⇒ **"有文档"反而制造了一类新的错误来源**；**regmap（0 篇文档）没有这个问题** |

### §10.3 明确不吸收的权衡（避免下次又发现一遍）

| 没做 | 理由 |
|---|---|
| **不读 163 个 PHY 驱动** | **10 万行，且逐个厂商相关**；**框架文档的边界就到 `phy_ops` 契约** |
| **不读 MIPI D-PHY 时序公式** | **需要 MIPI 规范（树外）** |
| **不覆盖 `samsung-usb2.rst`** | **单个厂商驱动的开发指南，不是框架知识**（§1.3） |
| **不逐个分类 149 个 `phy-supply`** | **需要逐节点判断有没有 `#phy-cells`，工作量大且对读者价值低**；**已给出判据和一个反例**（06 §4.2） |
| **不实测 ftrace / kprobe** | **未上机运行**（§5.1）；**且那是通用 ftrace 能力，不是 phy 特有** |
| **不做 ACPI 对照** | **arm64-only 约定 + 框架里 0 处 ACPI 代码**（§2.5）——**这条比 regmap 更干净：regmap 的 `fwnode_*` 是共用的，phy 连 `fwnode` 都不用** |
