---
title: 扩展机制
---

# phy 04：子框架与边界

## §0 组织轴

⚠️ **本篇的组织轴与其他子系统不同**：**phy 框架几乎没有子框架。**
**`drivers/phy/` 里除了 `phy-core.c`，只有一个 164 行的辅助文件。**
⇒ **所以本篇一半在讲"唯一那个子框架"，另一半在讲****边界**：
**哪些看起来像 phy 的东西其实不属于这个框架。**

**一节一个独立单元，互不调用。**

### §0.1 分诊表：哪些真会碰到

| # | 单元 | 是什么 | 规模 | arm64 是否相关 | 本篇 |
|---|---|---|---|---|---|
| 1 | **MIPI D-PHY 辅助** | **唯一真正的子框架**（在 `drivers/phy/` 内） | **164 行 / 2 个导出符号** | **相关**（MIPI DSI/CSI 显示与摄像头） | §1 |
| 2 | **三个参数族** | **`union phy_configure_opts` 的三个成员** | **头文件 284 + 95 + 32 行** | **相关** | §2 |
| 3 | **ULPI 桥** | **把 ULPI 设备包装成通用 PHY 的 inline 辅助** | **`include/linux/phy/ulpi_phy.h` 32 行，0 个导出符号** | **弱相关**（ULPI 主要在老 SoC 上） | §3 |
| 4 | ⚠️ **`drivers/usb/phy/`（老 USB PHY 框架）** | **完全独立的另一个框架** | **20 个 `.c` / 10895 行** | **弱相关**（arm64 上新平台都走通用框架） | §4.1 |
| 5 | ⚠️ **`drivers/net/phy/`（PHYLIB）** | **完全无关的同名子系统** | — | **相关但不在本文档** | §4.2 |
| 6 | **三个死字宏** | **树里 0 个使用者的公开宏** | — | — | §5 |

### §0.2 各单元怎么接入核心

| 单元 | 挂在 03 时间线的哪一点 |
|---|---|
| **MIPI D-PHY 辅助** | **不挂在时间线上** ——**它是纯计算函数，由驱动在 `ops->configure`/`ops->validate` 内部调用**（03 §A6） |
| **三个参数族** | **`phy_configure()` / `phy_validate()` 的参数**（03 §A6 第 ③ 类） |
| **ULPI 桥** | **替代 03 §A2 的 `phy_create()`** ——**它内部就是 `phy_create` + `phy_create_lookup`** |
| **老 USB PHY 框架** | ⚠️ **完全不挂** ——**反而在 03 §A4.2 被显式排除**（`usb-nop-xceiv` 特例） |

---

## §1 MIPI D-PHY 辅助：164 行，2 个导出符号

### §1.1 它解决什么问题

**MIPI D-PHY 的时序参数有十几个**（`T_HS_PREPARE`、`T_CLK_ZERO`、`T_LPX`…），
**规范给出的是"每个参数的合法范围随时钟频率变化"这样的约束，不是具体值。**

⇒ **每个 MIPI PHY 驱动都要做同样两件事**：
**① 从像素时钟推出一组符合规范的默认值；② 校验调用者传进来的值是否在合法范围内。**
⇒ **这个文件就是把这两件事提取出来。**

### §1.2 两个导出符号

| 符号 | 位置 | 做什么 |
|---|---|---|
| `phy_mipi_dphy_get_default_config(pixel_clock, …)` | **`drivers/phy/phy-core-mipi-dphy.c:20`**（导出于 `:78`）；**声明在 `include/linux/phy/phy-mipi-dphy.h:278`** | **按像素时钟算出一整套默认时序** |
| `phy_mipi_dphy_config_validate(cfg)` | **`drivers/phy/phy-core-mipi-dphy.c:84`**（导出于 `:164`）；**声明在 `include/linux/phy/phy-mipi-dphy.h:282`** | **逐项校验，越界返回 `-EINVAL`** |

⚠️ **这两个是全 phy 子系统唯一用裸 `EXPORT_SYMBOL` 的符号**
（**不是 `EXPORT_SYMBOL_GPL`**）——
**`phy-core.c` 的 38 个全部是 `_GPL`。**
重跑：`grep -n 'EXPORT_SYMBOL' drivers/phy/phy-core-mipi-dphy.c` ⇒ 两行都是 `EXPORT_SYMBOL(`。

### §1.3 `struct phy_configure_opts_mipi_dphy`：25 个字段

**定义在 `include/linux/phy/phy-mipi-dphy.h`（284 行的绝大部分是它和它的 kerneldoc）。**
**字段数按分号计 = 25**（重跑：
`awk '/^struct phy_configure_opts_mipi_dphy \{/{f=1} f&&/^\};/{exit} f' include/linux/phy/phy-mipi-dphy.h | grep -c ';'`）。

⇒ **它是三个参数族里最大的一个**（§2）——**也是"为什么需要一个辅助函数"的直接原因。**

⚠️ **本文档没有逐项核对这 25 个字段与 MIPI 规范的对应关系**
**也没有核对 `get_default_config()` 里的公式**（99 §2.2 / §5.2 已登记）——
**那需要 MIPI D-PHY 规范文本，属于树外资料。**

### §1.4 Kconfig 独立开关

**`GENERIC_PHY_MIPI_DPHY`（`drivers/phy/Kconfig`）是 `bool` 且无 prompt**
⇒ **只能被需要它的驱动 `select`**（01 §0.4）。
**Kconfig 的 help 原文有一个拼写错误**：*"Provides a number of helpers a core functions for MIPI D-PHY drivers to us."*
（逐字引用，`a core` 应为 `and core`、`to us` 应为 `to use`）——
**无功能影响，但可以作为"这块代码维护不勤"的旁证。**

---

## §2 三个参数族：`union phy_configure_opts` 的成员

### §2.1 三选一的表

| 成员 | 类型 | 头文件 | 头文件行数 | **字段数** | 用在哪 |
|---|---|---|---|---|---|
| `mipi_dphy` | `struct phy_configure_opts_mipi_dphy` | `include/linux/phy/phy-mipi-dphy.h` | **284** | **25** | **MIPI DSI（显示）/ CSI（摄像头）** |
| `dp` | `struct phy_configure_opts_dp` | `include/linux/phy/phy-dp.h` | 95 | **8** | **DisplayPort** |
| `lvds` | `struct phy_configure_opts_lvds` | `include/linux/phy/phy-lvds.h` | 32 | **4** | **LVDS 显示接口** |

重跑（每个换掉结构名和文件名）：
`awk '/^struct phy_configure_opts_dp \{/{f=1} f&&/^\};/{exit} f' include/linux/phy/phy-dp.h | grep -c ';'`

### §2.2 ⚠️ union 没有 tag，靠约定

**`union phy_configure_opts`（`include/linux/phy/phy.h:64-68`）只有这三个成员，
没有任何 `type` / `which` 字段。**

⇒ **"当前是哪一种"完全靠**：
**① 消费者知道自己在配什么 PHY；② 驱动知道自己是什么 PHY。**
⚠️ **框架不检查，也没有办法检查** ——**`phy_configure()` 只是把指针转发给 `ops->configure`**
（`drivers/phy/phy-core.c:515`）。

⇒ **填错成员的后果是静默的类型混淆**：**驱动会把 `dp` 的字段当 `mipi_dphy` 的字段读，
拿到的是内存里的随机重叠内容。**
**这是本子系统最不安全的一个接口**（05 §7 / 08 §5）。

**一个缓解手段是 `phy_validate()`**（01 §6.2）：
**驱动的 `validate` 回调至少能看出"这些值完全不合理"**，
**但它同样不知道 union 现在是哪一种。**

### §2.3 `pcie.h`：一个不在 union 里的参数头

⚠️ **`include/linux/phy/pcie.h`（12 行）不是 `union phy_configure_opts` 的成员。**
**它定义的是 PCIe PHY 的模式常量，供 `phy_set_mode_ext()` 的 `submode` 参数用。**
⇒ **别把它当成第四个参数族。**

---

## §3 ULPI 桥：32 行 inline，0 个导出符号

**`include/linux/phy/ulpi_phy.h` 全文只有两个 `static inline` 函数**：

```c
/* include/linux/phy/ulpi_phy.h —— 两个函数的全部内容 */
static inline struct phy
*ulpi_phy_create(struct ulpi *ulpi, const struct phy_ops *ops)
{
	struct phy *phy;
	int ret;

	phy = phy_create(&ulpi->dev, NULL, ops);
	if (IS_ERR(phy))
		return phy;

	ret = phy_create_lookup(phy, "usb2-phy", dev_name(ulpi->dev.parent));
	if (ret) {
		phy_destroy(phy);
		return ERR_PTR(ret);
	}

	return phy;
}

static inline void ulpi_phy_destroy(struct ulpi *ulpi, struct phy *phy)
{
	phy_remove_lookup(phy, "usb2-phy", dev_name(ulpi->dev.parent));
	phy_destroy(phy);
}
```

| 观察 | 说明 |
|---|---|
| **它是 03 §A2+§A3 的一个替代路径** | **`phy_create()` + `phy_create_lookup()`，完全不注册 provider** |
| ⚠️ **连接名硬编码为 `"usb2-phy"`** | **两个函数里各写一次**（必须一致，否则 remove 找不到） |
| ⚠️ **消费者硬编码为 `ulpi->dev.parent`** | **注释原话："binding it and it's controller, which is always the parent"** |
| **走的是非 DT 的 lookup 路径**（01 §8） | ⇒ **它是"lookup 表"这条路在树里的主要真实用例** |
| **相关文件** | **`drivers/usb/common/ulpi.c`（380 行）提供 ULPI 总线本身** ——**不属于本文档** |
| ⚠️ **全树唯一的使用者** | **`drivers/phy/ti/phy-tusb1210.c`**（重跑 `grep -rln 'phy/ulpi_phy.h' --include=*.c .`）⇒ **这个"子框架"只服务一个驱动** |

**`phy_create_lookup` 的全树真实调用点共 7 处，分布在 4 个驱动文件**（§6.2）。
⚠️ **注意这 7 处里没有一处来自本节这个 inline** ——**`ulpi_phy_create()` 是 `static inline`，
它的调用点在包含 `ulpi_phy.h` 的驱动里，而 grep `.c` 文件时看到的是那些驱动对 `ulpi_phy_create()` 的调用，
不是对 `phy_create_lookup()` 的调用。**

---

## §4 ⚠️ 边界：三个"看起来像但不是"

### §4.1 ⚠️`drivers/usb/phy/`：老的 USB PHY 框架

| 对比 | **通用 PHY**（本文档） | **`drivers/usb/phy/`** |
|---|---|---|
| 规模 | **`phy-core.c` 1244 行** | **20 个 `.c` / 10895 行** |
| 核心结构 | `struct phy` | **`struct usb_phy`** |
| 是否新代码该用 | **是** | ⚠️ **不是，属于历史包袱** |
| **两者的交界处** | **`drivers/phy/phy-core.c:581-584` 显式拒绝 `compatible = "usb-nop-xceiv"`** ——**把那类节点让给老框架**（03 §A4.2） | — |

⚠️ **这是全 `phy-core.c` 唯一一处按 compatible 字符串判断的代码**
（重跑 `grep -c 'of_device_is_compatible' drivers/phy/phy-core.c` ⇒ **1**）。
⇒ **看到一个 USB PHY 节点"框架不认"，先查它的 compatible 是不是 `usb-nop-xceiv`。**

### §4.2 ⚠️`drivers/net/phy/`：完全无关的同名子系统

**完整对照表在 01 §0.1 和 02 §1.0.A，本节不重复。**
**一句话判据：看 `#include` 里有没有 `phy/` 这层目录。**

### §4.3 三个"看起来是本框架的头文件"其实不是通用 API

| 头文件 | 行数 | 实际归属 |
|---|---|---|
| `include/linux/phy/omap_control_phy.h` | **89** | **TI OMAP 专属**，不是通用接口 |
| `include/linux/phy/omap_usb.h` | 25 | 同上 |
| `include/linux/phy/phy-sun4i-usb.h` | 18 | **Allwinner 专属** |

⇒ **`include/linux/phy/` 里 9 个头文件，只有 `phy.h` 是框架 API**；
**3 个是参数族（§2）、1 个是 ULPI 桥（§3）、1 个是 PCIe 常量、3 个是厂商专属。**

---

## §5 ⚠️ 三个死字宏（树里 0 个使用者）

**口径先说清**：**下面的计数排掉了注释行和 kerneldoc**
（命令：`grep -rnE "(^|[^_a-z])<宏名>\(" --include=*.c . | grep -vE '^\S+: *\*|/\*|\* '`）。

| 宏 | 真实调用点 | 结论 |
|---|---|---|
| ⚠️ **`of_phy_provider_register`**（非 devm） | **0** | ⚠️ **全树没有一个使用者**；**`phy-core.c` 里的 3 处命中全是 kerneldoc**（`:1177` `:1179` `:1197`） |
| ⚠️ **`devm_of_phy_provider_register_full`** | **0** | ⚠️ **死字**；**而且官方文档还专门介绍了它**（03 §C2 第 3 条） |
| **`of_phy_provider_register_full`** | **1** | **唯一使用者是 `drivers/phy/tegra/xusb.c:228`** ——**勉强不算死字** |

⇒ **"注册 provider"这件事，树里实际上只有一种写法**：
**`devm_of_phy_provider_register()`（144 个使用者）**，**外加 Tegra 一家用 `_full`。**

⚠️ **官方文档花了整整一节介绍四个宏**（`phy.rst` "Registering/Unregistering the PHY provider"），
**其中两个在树里没人用。**
⇒ **文档的篇幅分配与实际用量严重不符**（99 §1.3）。

**`phy_create`（非 devm 版）也接近死字**：**全树只有 1 个真实调用点**
（`drivers/phy/tegra/xusb.c:210`）——**对比 `devm_phy_create` 的 152 个。**

---

## §6 汇总表

### §6.1 单元 × 职责 × 挂载点 × arm64 相关性

| 单元 | 职责 | 挂在核心的哪一点 | 导出符号 | arm64 相关 |
|---|---|---|---|---|
| **MIPI D-PHY 辅助** | **算默认时序 + 校验** | **驱动的 `ops->configure`/`validate` 内部** | **2（裸 `EXPORT_SYMBOL`）** | **是** |
| **三个参数族** | **`configure`/`validate` 的参数格式** | **`phy_configure()` 的第二参数** | **0（纯头文件）** | **是** |
| **ULPI 桥** | **ULPI 设备 → 通用 PHY** | **替代 `phy_create` + `provider_register`** | **0（`static inline`）** | 弱 |
| ⚠️ **老 USB PHY 框架** | — | **被显式排除** | — | 弱 |
| ⚠️ **PHYLIB** | — | **无任何关系** | — | 是（但不在本文档） |

### §6.2 全树使用统计（每条都能重跑）

**口径**：**"真实调用点数"= 排掉注释/kerneldoc 后的行数**，
**命令模板**：`grep -rnE "(^|[^_a-z])<符号>\(" --include=*.c . | grep -vE '^\S+: *\*|/\*|\* ' | wc -l`

| 符号 / 结构 | 数量 | 口径 |
|---|---|---|
| **`struct phy_ops` 实例（全树）** | **167** | `grep -rhoE 'struct phy_ops [a-z_0-9]+ = \{' --include=*.c . \| wc -l` |
| **`struct phy_ops` 实例（`drivers/phy/` 内）** | **163** | 同上限定目录 |
| ⚠️ **`drivers/phy/` 之外的实例** | **4，分布在 3 个文件** | `drivers/pinctrl/tegra/pinctrl-tegra-xusb.c`（**2**）· `drivers/media/platform/sunxi/sun8i-a83t-mipi-csi2/sun8i_a83t_dphy.c`（1）· `drivers/gpu/drm/rockchip/dw-mipi-dsi-rockchip.c`（1） |
| **`devm_phy_create` 调用点** | **152** | **框架的主力入口** |
| ⚠️ **`phy_create` 调用点** | **1**（`drivers/phy/tegra/xusb.c:210`） | §5 |
| **`devm_of_phy_provider_register` 调用点** | **144** | — |
| **`of_phy_provider_register_full` 调用点** | **1**（`drivers/phy/tegra/xusb.c:228`） | §5 |
| ⚠️ **`of_phy_provider_register` / `devm_of_phy_provider_register_full`** | **各 0** | §5（死字） |
| **`phy_create_lookup` 调用点** | **7 处调用 + 1 处定义 = grep 命中 8** | **调用分布：`drivers/phy/marvell/phy-pxa-usb.c` **3** · `ti/phy-da8xx-usb.c` **2** · `mediatek/phy-mtk-dp.c` **1** · `ti/phy-twl4030-usb.c` **1**；第 8 处是 `phy-core.c:64` 的函数定义本身** |
| **`phy-core.c` 导出符号** | **38**（全 `_GPL`） | `grep -c 'EXPORT_SYMBOL' drivers/phy/phy-core.c` |
| **`phy-core-mipi-dphy.c` 导出符号** | **2**（非 GPL） | `grep -c 'EXPORT_SYMBOL' drivers/phy/phy-core-mipi-dphy.c` |
| **`drivers/usb/phy/` 规模** | **20 个 `.c` / 10895 行** | `ls drivers/usb/phy/*.c \| wc -l` · `cat drivers/usb/phy/*.c \| wc -l` |

⚠️ **两条口径警告**：
1. **`grep -rl` 会把 kerneldoc 里的 `函数名()` 算成使用者** ——
   **本节所有数字都排掉了注释行**；**不排的话 `of_phy_provider_register` 会从 0 变成 1**（`phy-core.c`）。
2. **驱动里可能有同名的局部函数** ——**`drivers/phy/intel/phy-intel-lgm-combo.c:535` 有个
   `intel_cbphy_create()`**，**宽松的正则会把它算成 `phy_create` 的调用**（本节已排除）。

### §6.3 裁掉了什么

| 裁掉的 | 为什么 |
|---|---|
| **MIPI D-PHY 的 25 个字段与规范的对应、`get_default_config()` 的公式** | **需要 MIPI 规范文本（树外资料）**；**只讲"这两个函数各自做什么"**（§1.3） |
| **`drivers/usb/phy/` 的 10895 行** | **另一个独立框架**；**只交代边界在哪一行**（§4.1） |
| **`drivers/net/phy/` PHYLIB** | **完全无关的子系统**（§4.2） |
| **`drivers/usb/common/ulpi.c`（380 行）** | **ULPI 总线本身属于 USB 子系统**；**只讲桥接那 32 行**（§3） |
| **163 个 PHY 驱动的实现** | **只讲契约**；**代表驱动的选法见 99 §2.4** |
| **3 个厂商专属头文件的内容** | **不属于通用 API**（§4.3） |
| **`phy_configure_opts_dp` / `_lvds` 的逐字段语义** | **需要 DP / LVDS 规范** |

## §7 阅读下一篇

| 想知道 | 去 |
|---|---|
| API 清单 | `01-api.md` |
| 结构体关系 | `02-datastructures.md` |
| 主流程与设计债 | `03-internals.md` |
| **代码骨架** | `05-usage.md` |
| DT 属性 | `06-devicetree.md` |
| 调试 | `07-debug.md` |
