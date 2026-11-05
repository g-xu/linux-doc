---
title: API 参考
---

# phy 01：代码地图与对外 API

> **基线**：`linux-6.1.158`（`f6e38ae624cf`），**arm64-only**，
> **`drivers/phy/` 与 `include/linux/phy/` 未被本树补丁修改**（99 §1）。

## §0 代码地图

### §0.1 ⚠️ 先确认你找的是哪个 "phy"（本篇第一件事）

**内核里有两个完全不同的 "phy" 子系统，头文件名只差一层目录**：

| | **本文档讲的**（通用 PHY 框架） | **不是本文档**（网络 PHY / MDIO） |
|---|---|---|
| 目录 | **`drivers/phy/`** | **`drivers/net/phy/`** |
| 头文件 | **`include/linux/phy/phy.h`**（带 `phy/` 目录层） | **`include/linux/phy.h`**（不带） |
| 核心结构 | **`struct phy`**（8 个字段） | **`struct phy_device`** |
| 核心 ops | **`struct phy_ops`** | **`struct phy_driver`** |
| Kconfig | **`GENERIC_PHY`** | **`PHYLIB`** |
| 官方文档 | **`Documentation/driver-api/phy/phy.rst`** | **`Documentation/networking/phy.rst`** |
| 管什么 | **USB / PCIe / SATA / MIPI / UFS 等各种串行链路的物理层**（初始化、上电、模式切换） | **以太网 PHY 芯片**（链路协商、速率、MDIO 寄存器） |

⚠️ **两者没有代码关系**，**只是名字撞了**（详细的区分见 02 §1.0 组 1、08 §1）。
**本篇之后所有"phy"都指前者。**

### §0.2 文件在哪

| 角色 | 文件 | 职责 |
|---|---|---|
| **核心** | **`drivers/phy/phy-core.c`** | **整个框架的全部逻辑**（1244 行，38 个导出符号） |
| **MIPI D-PHY 辅助** | `drivers/phy/phy-core-mipi-dphy.c` | **只做 D-PHY 参数的默认值计算与校验**（164 行，2 个导出符号） |
| **主头文件** | **`include/linux/phy/phy.h`**（538 行） | **结构体 + 全部 API + `CONFIG_GENERIC_PHY=n` 的空桩** |
| **各类 PHY 的参数头** | `include/linux/phy/phy-mipi-dphy.h`(284) · `phy-dp.h`(95) · `phy-lvds.h`(32) · `pcie.h`(12) | **`union phy_configure_opts` 的各成员定义**（§6） |
| 遗留/专用头 | `omap_control_phy.h`(89) · `ulpi_phy.h`(32) · `omap_usb.h`(25) · `phy-sun4i-usb.h`(18) | 不属于通用 API |
| **驱动** | **`drivers/phy/<厂商>/`（25 个子目录）+ 5 个顶层驱动** | **本篇只讲契约**（99 §2.4 登记了读了哪几个） |

⚠️ **规模的形状是"核心极小、驱动极大"**：
**`drivers/phy/` 共 205 个 `.c`/`.h`、103073 行，但核心只占 1408 行（1.4%）。**
⇒ **读 phy 框架本身，只需要读 `phy-core.c` 和 `phy.h` 两个文件，共 1782 行。**

### §0.3 厂商驱动分布（决定"举例用谁"）

| 目录 | `.c` 数 | 目录 | `.c` 数 |
|---|---|---|---|
| `qualcomm/` | **18** | `tegra/` `st/` `socionext/` `intel/` `freescale/` `cadence/` | 各 5 |
| `samsung/` `marvell/` | 各 14 | `renesas/` `allwinner/` | 各 4 |
| `broadcom/` | 13 | `ralink/` `motorola/` `microchip/` | 各 2 |
| `rockchip/` | 12 | 其余（`ingenic/` `lantiq/` `mscc/` `sunplus/` `ti/`(10) `xilinx/` `amlogic/`(9) `hisilicon/`(7) `mediatek/`(11)） | — |

重跑：`for d in drivers/phy/*/; do echo -n "$d "; find $d -name '*.c' | wc -l; done | sort -k2 -rn`

### §0.4 Kconfig：和 regmap 相反，这里是可见选项

```
config GENERIC_PHY
	bool "PHY Core"          ← 有 prompt 字符串，menuconfig 里搜得到
```

| 符号 | 类型 | 说明 |
|---|---|---|
| `GENERIC_PHY` | **`bool "PHY Core"`** | **有 prompt，用户可直接开**；**也被每个 PHY 驱动 `select`** |
| `GENERIC_PHY_MIPI_DPHY` | **`bool`（无 prompt）** | **只能被 `select`**，对应 `phy-core-mipi-dphy.c` |
| **5 个顶层驱动符号** | `tristate` | `PHY_LPC18XX_USB_OTG` · `PHY_PISTACHIO_USB` · **`PHY_XGENE`（`depends on ARM64`）** · `USB_LGM_PHY` · `PHY_CAN_TRANSCEIVER` |
| **25 个 `source` 行** | — | 每个厂商目录一个 Kconfig |

⚠️ **`CONFIG_GENERIC_PHY=n` 时头文件提供整套空桩**（`include/linux/phy/phy.h:286-537`）——
**和 regmap 不同，这些空桩绝大多数是静默返回，不 `WARN`**（§9.3）。

### §0.5 40 个导出符号，按用途分五组

| 组 | 数量 | 符号 | 在本篇 |
|---|---|---|---|
| **消费者：取用与释放** | **10** | `phy_get` `phy_optional_get` `devm_phy_get` `devm_phy_optional_get` `devm_of_phy_get` `devm_of_phy_get_by_index` `of_phy_get` `phy_put` `devm_phy_put` `of_phy_put` | §4 |
| **消费者：操作 PHY** | **11** | `phy_init` `phy_exit` `phy_power_on` `phy_power_off` `phy_set_mode_ext` `phy_set_media` `phy_set_speed` `phy_reset` `phy_calibrate` `phy_configure` `phy_validate` | §5 / §6 |
| **提供者：创建与销毁** | **4** | `phy_create` `devm_phy_create` `phy_destroy` `devm_phy_destroy` | §3 |
| **提供者：注册 provider** | **5** | `__of_phy_provider_register` `__devm_of_phy_provider_register` `of_phy_provider_unregister` `devm_of_phy_provider_unregister` `of_phy_simple_xlate` | §3 |
| **PM runtime** | **6** | `phy_pm_runtime_get` `phy_pm_runtime_get_sync` `phy_pm_runtime_put` `phy_pm_runtime_put_sync` `phy_pm_runtime_allow` `phy_pm_runtime_forbid` | §7 |
| **非 DT 查找表** | **2** | `phy_create_lookup` `phy_remove_lookup` | §8 |
| **MIPI D-PHY 辅助** | **2** | **在 `phy-core-mipi-dphy.c` 里**（§6.3） | §6 |

重跑：`grep -c 'EXPORT_SYMBOL' drivers/phy/phy-core.c` ⇒ **38**；
`grep -c 'EXPORT_SYMBOL' drivers/phy/phy-core-mipi-dphy.c` ⇒ **2**。

### §0.6 ⚠️ 核心没有的东西（对比 regmap，反差极大）

| 设施 | phy | 参照 |
|---|---|---|
| **tracepoint** | **0** | regmap 有 17 个 |
| **debugfs** | **0** | regmap 有 692 行 |
| **自定义 sysfs 属性** | **0**（`DEVICE_ATTR` / `ATTRIBUTE_GROUPS` 都没有） | — |
| **源码自陈设计债** | **0 条**（`FIXME`/`TODO`/`REVISIT`/`HACK`/`XXX` 全无） | regmap 有 1 条 |
| ⚠️ **`struct class`** | **有！**`phy_class = class_create(THIS_MODULE, "phy")`（`drivers/phy/phy-core.c:1233`） | **这是唯一的观测面**（07 篇） |

重跑：`grep -rn 'trace_\|debugfs\|DEVICE_ATTR\|ATTRIBUTE_GROUPS' drivers/phy/phy-core.c` 无输出。

---

## §1 先分清：三方角色 + 一层可插拔

### §1.1 三方角色

| 角色 | 是什么 | 代码上体现为 |
|---|---|---|
| **PHY provider（提供者）** | **实现了一个或多个 PHY 实例的实体**（官方术语） | **一个 `struct phy_provider`，由 `of_phy_provider_register()` 登记** |
| **PHY 实例** | **一条具体的物理链路** | **一个 `struct phy`，由 `phy_create()` 产生**；**它是一个 `struct device`** |
| **consumer（消费者）** | **需要用这条链路的控制器**（USB / PCIe / SATA 控制器驱动） | **调 `devm_phy_get()` 拿到 `struct phy *`** |

⚠️ **一个 provider 可以有多个 phy 实例**（官方文档明说）——
**这就是 `of_xlate` 存在的全部理由**（§3.3）：
**DT 里 `phys = <&provider 2>` 的那个 `2`，要由 provider 自己翻译成"我的第几个实例"。**

### §1.2 只有一层可插拔：`struct phy_ops`

| 层 | 抽象 | 谁实现 | 实现数 | 开放程度 |
|---|---|---|---|---|
| **唯一一层** | **`struct phy_ops`**（**12 个字段**，`include/linux/phy/phy.h:84-127`） | **每个 PHY 驱动** | **约 200 个驱动文件** | **完全开放** |

**对比 regmap 的三层**（总线 / 缓存 / 锁）：**phy 只有一层，而且核心不提供任何"默认实现"。**
⇒ **phy 框架做的事非常薄**：**引用计数 + 互斥 + PM runtime + DT 解析，然后就是转发到 `ops`。**

**框架自带的唯一"默认实现"是 `of_phy_simple_xlate()`**
（`drivers/phy/phy-core.c:726` 处导出）——**给"只有一个实例"的 provider 用。**

### §1.3 ⚠️ 框架只服务"外置 PHY"（官方文档的一条硬约束）

**`Documentation/driver-api/phy/phy.rst` 原话**：
> *"This framework will be of use only to devices that use external PHY
> (PHY functionality is not embedded within the controller)."*

⇒ **控制器内部集成的 PHY 不该用这个框架**，
**它们直接在控制器驱动里操作自己的寄存器**（99 §1.3 已登记为三等证据）。

---

## §2 `struct phy_ops`：12 个回调

**位置：`include/linux/phy/phy.h:84-127`。一个不多。**

| 回调 | 签名（形参名） | 必需？ | 说明 | 备注 |
|---|---|---|---|---|
| `init` | `(phy)` | **可选** | **第一次 `phy_init()` 时调** | **由 `init_count` 保护，只在 0→1 时调**（03 篇） |
| `exit` | `(phy)` | 可选 | **最后一次 `phy_exit()` 时调** | **1→0 时调** |
| `power_on` | `(phy)` | **可选** | **上电** | **由 `power_count` 保护**；**框架会先自动开 `phy->pwr` 这个 regulator**（03 篇） |
| `power_off` | `(phy)` | 可选 | 断电 | 同上 |
| `set_mode` | `(phy, mode, submode)` | 可选 | **切换工作模式**（USB host/device、PCIe、SATA…） | **20 个 `enum phy_mode` 取值**（§5.1）；**不实现则 `phy_set_mode_ext()` 返回 0（成功）而不是错误** |
| `set_media` | `(phy, media)` | 可选 | 切换传输介质 | **只有 3 个取值**（§5.2） |
| `set_speed` | `(phy, speed)` | 可选 | 设置速率 | **`speed` 是裸 `int`，语义由驱动定** |
| `configure` | `(phy, opts)` | **可选**（kerneldoc 明写 "Optional"） | **改 PHY 参数** | ⚠️ **kerneldoc 要求"必须先调过 `phy_init()`"**；`opts` 是 `union`（§6） |
| `validate` | `(phy, mode, submode, opts)` | 可选 | **只校验不生效** | **给消费者"先问能不能"用**（§6.2） |
| `reset` | `(phy)` | 可选 | 复位 | **不是"电源复位"，语义由驱动定** |
| `calibrate` | `(phy)` | 可选 | 校准 | |
| ⚠️`release` | `(phy)` | 可选 | ⚠️ **不是"设备销毁时调"，而是"每次消费者释放引用时调"** | **`void` 返回**；⚠️ **唯一调用点是 `of_phy_put()`（`drivers/phy/phy-core.c:654-655`），而 `phy_put()` 会调它（`:673`）** ⇒ **一个 phy 生命周期里可能被调多次**；**真正的设备销毁走 `phy_release()`（`:1220-1229`），那里只做 `regulator_put` + `ida_simple_remove` + `kfree`，不调 `ops->release`** |
| `owner` | **`struct module *`（不是回调）** | **实际上必需** | **模块引用计数** | ⚠️ **`phy_get()` 里 `try_module_get(phy->ops->owner)` 失败会返回 `-EPROBE_DEFER`**（§9.2） |

⚠️ **"可选"的含义要小心**：**11 个回调里绝大多数不实现时，对应的 API 返回 0（成功）而不是 `-ENOTSUPP`**
——**少数返回 `-ENOTSUPP`**。**逐个的差别在 03 篇逐函数列，本篇只在 §9.1 给分类。**

---

## §3 提供者侧 API

**头文件：`include/linux/phy/phy.h`；实现：`drivers/phy/phy-core.c`。**

### §3.1 创建 PHY 实例

| API | 说明 | 备注 |
|---|---|---|
| `phy_create(dev, node, ops)` | **创建一个 PHY 实例并注册进 `phy_class`** | **返回 `ERR_PTR`**；**`node` 可为 NULL（用 `dev->of_node`）**；**内部会 `pm_runtime_enable`** |
| `devm_phy_create(dev, node, ops)` | 同上，自动释放 | **绝大多数驱动用这个** |
| `phy_destroy(phy)` | 销毁 | **内部 `pm_runtime_disable`** |
| `devm_phy_destroy(dev, phy)` | 销毁 + 拆 devres | **用了 devm 版创建就不必手动调** |

⚠️ **`phy_create()` 产生的 `struct phy` 是 provider 设备的子设备**
（官方文档明写："the phy device created by this subsystem will be a child of the device that calls `phy_create`"）
⇒ **PM runtime 会顺着父子关系传导**（§7）。

### §3.2 注册 provider（四个宏 + 两个底层函数）

**宏在 `include/linux/phy/phy.h:196-206`，全部展开成两个 `__` 前缀函数**：

| 宏 | 展开成 | 位置 |
|---|---|---|
| `of_phy_provider_register(dev, xlate)` | `__of_phy_provider_register(dev, NULL, THIS_MODULE, xlate)` | `include/linux/phy/phy.h:196-197` |
| `devm_of_phy_provider_register(dev, xlate)` | `__devm_of_phy_provider_register(dev, NULL, THIS_MODULE, xlate)` | `:199-200` |
| `of_phy_provider_register_full(dev, children, xlate)` | `__of_phy_provider_register(dev, children, THIS_MODULE, xlate)` | `:202-203` |
| `devm_of_phy_provider_register_full(dev, children, xlate)` | **`__devm_of_phy_provider_register(dev, children, THIS_MODULE, xlate)`** | `:205-206` |

⚠️ **只有两个底层实体**：`__of_phy_provider_register`（`drivers/phy/phy-core.c:1135` 处导出）
和 `__devm_of_phy_provider_register`（`:1173` 处导出）。
**`_full` 后缀只是"多传一个 `children` 节点"，不是另一个函数。**

⚠️ **官方文档在这里写错了**：
**`phy.rst` 把 `devm_of_phy_provider_register_full` 写成展开到 `__devm_of_phy_provider_register_full`** ——
**这个符号全树不存在**（重跑 `grep -rn '__devm_of_phy_provider_register_full' --include=*.c --include=*.h .` 命中 0）。
**以代码为准**（99 §1.3）。

**`children` 参数的用途**（官方文档说明的设计意图）：
**DT 里 provider 的子节点可能被多包了一层**，
**这时用 `_full` 版把"真正装着 PHY 子节点的那个节点"传进去。**

### §3.3 `of_xlate`：把 DT 参数翻译成实例

| API | 说明 | 备注 |
|---|---|---|
| `of_phy_simple_xlate(dev, args)` | **框架自带的默认实现** | **只适合"provider 只有一个实例"**；`drivers/phy/phy-core.c:726` 处导出；**`args` 参数不使用**（kerneldoc 明写 "not used here"） |

**多实例 provider 必须自己写 `of_xlate`** ——**签名是 `struct phy *(*)(struct device *, struct of_phandle_args *)`**
（`include/linux/phy/phy.h:176-177`）。

### §3.4 注销

| API | 说明 |
|---|---|
| `of_phy_provider_unregister(phy_provider)` | **`drivers/phy/phy-core.c:1192` 处导出** |
| `devm_of_phy_provider_unregister(dev, phy_provider)` | `:1211` 处导出；**用了 devm 版注册就不必手动调** |

### §3.5 私有数据

| API | 位置 | 说明 |
|---|---|---|
| `phy_set_drvdata(phy, data)` | `include/linux/phy/phy.h:208`（`static inline`，不是导出符号） | **在 `phy_create()` 之后调** |
| `phy_get_drvdata(phy)` | `include/linux/phy/phy.h:213`（`static inline`） | **在 `phy_ops` 回调里取回私有数据** |

**这是官方文档推荐的私有数据模式**（`phy.rst` 的 "Creating the PHY" 段）。

---

## §4 消费者侧 API：取用与释放

### §4.1 六个"取"

| API | 说明 | 备注 |
|---|---|---|
| `phy_get(dev, string)` | **按名字取**（DT 下查 `phy-names`，非 DT 下查 lookup 表） | **失败返回 `ERR_PTR`**；**`drivers/phy/phy-core.c:773` 处导出** |
| `phy_optional_get(dev, string)` | **同上，但"没有"不算错** | ⚠️ **只把 `-ENODEV` 转成 NULL**（`phy-core.c:789-790`），**其他错误照样返回 `ERR_PTR`** |
| `devm_phy_get(dev, string)` | **最常用** | 自动释放 |
| `devm_phy_optional_get(dev, string)` | **可选 PHY 的最常用形态** | 自动释放 |
| `devm_of_phy_get(dev, np, con_id)` | **从指定的 DT 节点取**（不是 `dev->of_node`） | **给"控制器读子节点里的 phy"用** |
| `devm_of_phy_get_by_index(dev, np, index)` | **按下标取，不用名字** | **官方文档点名给 ehci 这类"用多个 phy、名字没意义"的驱动**（`phy.rst`） |
| `of_phy_get(np, con_id)` | **裸 DT 取用**，不建 device link | `phy-core.c:640` 处导出；**主要供框架内部和特殊场景** |

⚠️ **`phy_get()` 会建立 device link**（**官方文档完全没提这件事**）：

```c
/* drivers/phy/phy-core.c:761-769 */
	if (!try_module_get(phy->ops->owner))
		return ERR_PTR(-EPROBE_DEFER);	/* 注意不是 -ENODEV */

	get_device(&phy->dev);

	link = device_link_add(dev, &phy->dev, DL_FLAG_STATELESS);
	if (!link)
		dev_dbg(dev, "failed to create device link to %s\n",
			dev_name(phy->dev.parent));
```

**三处 `device_link_add(..., DL_FLAG_STATELESS)`**：
`phy-core.c:766`（`phy_get`）· `:879`（`devm_of_phy_get`）· `:926`（`devm_of_phy_get_by_index`）。
⇒ **device link 保证 suspend/resume 和 shutdown 的顺序**，**建不上只打 `dev_dbg`，不算失败。**

### §4.2 三个"放"

| API | 签名（形参名） | 备注 |
|---|---|---|
| ⚠️`phy_put(dev, phy)` | **两个参数** | ⚠️ **官方文档写成 `void phy_put(struct phy *phy)`（一个参数），是过时的** ——**真实签名在 `include/linux/phy/phy.h:261` 和 `drivers/phy/phy-core.c:670`**；**多出的 `dev` 用于 `device_link_remove(dev, &phy->dev)`（`:672`）** |
| `devm_phy_put(dev, phy)` | 两个参数 | **`!phy` 直接 return**（`phy-core.c:689-690`）；**找不到 devres 会 `dev_WARN_ONCE(dev, r, "couldn't find PHY resource\n")`（`:693`）** |
| `of_phy_put(phy)` | 一个参数 | **`phy_put()` 内部会调它**（`phy-core.c:673`） |

### §4.3 ⚠️`NULL` 是合法的 phy —— 但有两个反例（本篇最重要的一条）

**官方文档 `phy.rst` 原话**：
> *"It should be noted that NULL is a valid phy reference. All phy consumer calls on the NULL phy
> become NOPs. That is the release calls, the phy_init() and phy_exit() calls, and phy_power_on()
> and phy_power_off() calls are all NOP when applied to a NULL phy."*

**代码基本支持这句话，九个 API 确实是 NOP + 返回 0**：

| API | NULL 处理 | 位置 |
|---|---|---|
| `phy_init` | **`return 0`** | `drivers/phy/phy-core.c:249-250` |
| `phy_exit` | `return 0` | `:289-290` |
| `phy_power_on` | **`goto out`（`ret` 初值 0）** | `:326-327` |
| `phy_power_off` | `return 0` | `:376-377` |
| `phy_set_mode_ext` | `return 0` | `:403-404` |
| `phy_set_media` | `return 0` | `:421-422` |
| `phy_set_speed` | `return 0` | `:436-437` |
| `phy_reset` | `return 0` | `:451-452` |
| `phy_calibrate` | `return 0` | `:482-483` |

⚠️⚠️ **但这两个不是 NOP，而是返回错误**：

| API | NULL 处理 | 位置 |
|---|---|---|
| ⚠️ **`phy_configure`** | **`return -EINVAL`** | `drivers/phy/phy-core.c:508-509` |
| ⚠️ **`phy_validate`** | **`return -EINVAL`** | `drivers/phy/phy-core.c:543-544` |

⇒ **"All phy consumer calls on the NULL phy become NOPs" 这句话在本版本上不成立。**
⇒ **用了 `devm_phy_optional_get()` 又调 `phy_configure()` 的驱动，在"没有 phy"的板子上会拿到 `-EINVAL`。**
**这是本套文档最要紧的一条 doc-vs-code 差异**（99 §1.3 / 05 §7 / 08 §3）。

---

## §5 模式、介质、速率

### §5.1 `enum phy_mode`：20 个取值

**位置：`include/linux/phy/phy.h:25-46`。**

| 分组 | 取值 |
|---|---|
| **无效值** | `PHY_MODE_INVALID`（**值为 0，即"没设过"**） |
| **USB 主机（5）** | `PHY_MODE_USB_HOST` · `_LS` · `_FS` · `_HS` · `_SS` |
| **USB 设备（5）** | `PHY_MODE_USB_DEVICE` · `_LS` · `_FS` · `_HS` · `_SS` |
| **USB OTG（1）** | `PHY_MODE_USB_OTG` |
| **UFS（2）** | `PHY_MODE_UFS_HS_A` · `PHY_MODE_UFS_HS_B` |
| **其他链路（6）** | `PHY_MODE_PCIE` · `PHY_MODE_ETHERNET` · `PHY_MODE_MIPI_DPHY` · `PHY_MODE_SATA` · `PHY_MODE_LVDS` · `PHY_MODE_DP` |

**API 两个形态**：

| API | 位置 | 说明 |
|---|---|---|
| `phy_set_mode_ext(phy, mode, submode)` | **`drivers/phy/phy-core.c:415` 处导出** | **真正的实现** |
| `phy_set_mode(phy, mode)` | **宏**，`include/linux/phy/phy.h:230` | **展开成 `phy_set_mode_ext(phy, mode, 0)`** |
| `phy_get_mode(phy)` | `include/linux/phy/phy.h:238`（`static inline`） | **读 `phy->attrs.mode`，不问硬件** |

⚠️ **`phy_set_mode_ext()` 有一个静默陷阱**：

```c
/* drivers/phy/phy-core.c:406-411 */
	mutex_lock(&phy->mutex);
	if (phy->ops->set_mode)
		ret = phy->ops->set_mode(phy, mode, submode);
	if (!ret)
		phy->attrs.mode = mode;		/* 注意这里 */
	mutex_unlock(&phy->mutex);
```

⇒ **驱动没实现 `set_mode` 时，`ret` 保持 0 ⇒ `attrs.mode` 照样被写成了新模式。**
⇒ **`phy_get_mode()` 之后会报告一个硬件从未进入过的模式**，**而且调用者拿到的是"成功"。**

### §5.2 `enum phy_media`：只有 3 个取值

`include/linux/phy/phy.h:48-52`：`PHY_MEDIA_DEFAULT` · `PHY_MEDIA_SR` · `PHY_MEDIA_DAC`。

| API | 位置 | ops 缺失时 |
|---|---|---|
| `phy_set_media(phy, media)` | `drivers/phy/phy-core.c:430` 处导出 | **返回 0（成功）**（`:421-422` 把 `!phy` 和 `!ops->set_media` 合并判断） |

### §5.3 速率

| API | 位置 | 备注 |
|---|---|---|
| `phy_set_speed(phy, speed)` | `drivers/phy/phy-core.c:445` 处导出 | **`speed` 是裸 `int`，语义完全由驱动定**；**ops 缺失返回 0** |
| `phy_get_bus_width(phy)` | `include/linux/phy/phy.h:244`（`static inline`） | **读 `phy->attrs.bus_width`** |
| `phy_set_bus_width(phy, bus_width)` | `include/linux/phy/phy.h:248`（`static inline`） | **纯赋值，不碰硬件** |

⚠️ **`phy_attrs` 三个字段都是"软状态"**（`include/linux/phy/phy.h:135-139`）：
**`bus_width` / `max_link_rate` / `mode`** ——**全部只是记在结构里，没有任何硬件读回。**

---

## §6 `configure` / `validate` 与参数联合体

### §6.1 `union phy_configure_opts`：只有三种

**位置：`include/linux/phy/phy.h:64-68`。**

| 成员 | 类型定义在 | 行数 |
|---|---|---|
| `mipi_dphy` | `include/linux/phy/phy-mipi-dphy.h` | **284 行（最大的一个）** |
| `dp` | `include/linux/phy/phy-dp.h` | 95 |
| `lvds` | `include/linux/phy/phy-lvds.h` | 32 |

⚠️ **是 `union` 不是 `struct`** ⇒ **调用者和驱动必须对"这是哪一种"有一致约定**，
**框架不做任何检查、也不带 tag 字段** ⇒ **填错成员是纯静默的类型混淆**（05 §7 / 08 §5）。

### §6.2 两个 API 的语义差别

| API | 做什么 | ops 缺失时 | NULL phy 时 |
|---|---|---|---|
| `phy_configure(phy, opts)` | **真正改参数** | **`-EOPNOTSUPP`**（`drivers/phy/phy-core.c:511-512`） | ⚠️ **`-EINVAL`**（`:508-509`） |
| `phy_validate(phy, mode, submode, opts)` | **只问"能不能"，不生效** | **`-EOPNOTSUPP`**（`:546-547`） | ⚠️ **`-EINVAL`**（`:543-544`） |

**`validate` 的用途**：**消费者在真正配置前先试探，避免把 PHY 配成半死状态。**
**`validate` 多两个参数（`mode`/`submode`）**，**因为"这套参数能不能用"往往取决于工作模式。**

⚠️ **这两个是全框架唯一用 `-EOPNOTSUPP` 的地方**（共 2 处，§9.1）。

### §6.3 MIPI D-PHY 的两个辅助函数

**在 `drivers/phy/phy-core-mipi-dphy.c`（164 行，2 个导出符号）** ——
**它不是"另一层可插拔"，是一组给 D-PHY 驱动复用的参数计算函数**：
**按 MIPI 规范从时钟频率推出各个时序参数的默认值，并做范围校验。**
⚠️ **本文档只读了它的导出符号和职责，没有逐行核对规范公式**（99 §2.2）。

---

## §7 PM runtime：六个包装 + 两个隐含调用

### §7.1 六个导出包装

| API | 位置（导出行） | 说明 |
|---|---|---|
| `phy_pm_runtime_get(phy)` | `drivers/phy/phy-core.c:166` | |
| `phy_pm_runtime_get_sync(phy)` | `:184` | **框架内部用得最多的一个** |
| `phy_pm_runtime_put(phy)` | `:196` | |
| `phy_pm_runtime_put_sync(phy)` | `:208` | |
| `phy_pm_runtime_allow(phy)` | `:220` | **`void` 返回** |
| `phy_pm_runtime_forbid(phy)` | `:232` | **`void` 返回** |

### §7.2 ⚠️`-ENOTSUPP` 被刻意吃掉

**框架内部每次调 `phy_pm_runtime_get_sync()` 都跟着同一段处理**：

```c
/* drivers/phy/phy-core.c:252-255（phy_init 里，另外 :292-295 和 :335-339 一模一样） */
	ret = phy_pm_runtime_get_sync(phy);
	if (ret < 0 && ret != -ENOTSUPP)
		return ret;
	ret = 0; /* Override possible ret == -ENOTSUPP */
```

⇒ **`-ENOTSUPP` 表示"这个 phy 没启用 PM runtime"，属于正常情况，被显式改写成 0。**
⇒ **注释就是源码原话**：`/* Override possible ret == -ENOTSUPP */`。
⚠️ **所以 `-ENOTSUPP` 在 phy 框架里几乎不会传到消费者手上**（§9.1）。

### §7.3 官方文档交代的父子传导

**`phy.rst` "PM Runtime" 段的三条**（三等证据，但代码可印证）：
1. **`phy_create` 里 `pm_runtime_enable`、`phy_destroy` 里 `pm_runtime_disable`**
2. **phy 设备是 provider 设备的子设备** ⇒ **`pm_runtime_get_sync(phy)` 会连带唤醒 provider**
3. **`phy_power_on` / `phy_power_off` 各自做一次 `phy_pm_runtime_get_sync` / `phy_pm_runtime_put`**

⚠️ **第 3 条要精确到"哪个变体"**：
**`phy_power_on` 用 `get_sync`（`drivers/phy/phy-core.c:335`），
错误路径用 `put_sync`（`:355`）；而 `phy_power_off` 用的是 `put`（`:390`，不带 `_sync`）。**

---

## §8 非 DT 场景：lookup 表

**官方文档把它类比成 clkdev**（`phy.rst` "PHY Mappings" 段）。

| API | 位置 | 说明 |
|---|---|---|
| `phy_create_lookup(phy, con_id, dev_id)` | `drivers/phy/phy-core.c:85` 处导出 | **把一个已存在的 `struct phy` 绑到 "(设备名, 连接名)" 上** |
| `phy_remove_lookup(phy, con_id, dev_id)` | `:113` 处导出 | **`void` 返回** |

**`struct phy_lookup`（`include/linux/phy/phy.h:187-192`）四个字段**：
`node`（链表）· `dev_id`（消费者设备名）· `con_id`（连接名）· `phy`。

⇒ **`phy_get(dev, string)` 在 `dev->of_node` 为空时走 `phy_find(dev, string)`**
（`drivers/phy/phy-core.c:756`）**去查这张表。**
⚠️ **arm64 上几乎总是走 DT 路径**，**这条路主要给 ACPI / 老平台**（06 §0 分诊表）。

---

## §9 错误码语义

### §9.1 普查（`phy-core.c` + `phy-core-mipi-dphy.c`）

| 码 | 出现次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **31** | **参数非法**；⚠️ **也包含"NULL phy 传给 `configure`/`validate`"这一类**（§4.3） |
| `-ENODEV` | **11** | **找不到 phy** ——**`phy_optional_get` 唯一会转成 NULL 的码** |
| `-ENOTSUPP` | **11** | **几乎全部来自 PM runtime，且被框架吃掉**（§7.2） |
| `-ENOMEM` | 8 | 分配失败 |
| `-EPROBE_DEFER` | **7** | **provider 还没就绪** ——**含 `try_module_get` 失败这一条**（§9.2） |
| `-EOPNOTSUPP` | **2** | **只有 `phy_configure` / `phy_validate` 用**（§6.2） |
| `-EBUSY` / `-EIO` / `-ERANGE` / `-EAGAIN` / `-ENOSYS` | **全 0** | **本框架不用** |

重跑：`for e in EINVAL ENODEV ENOTSUPP ENOMEM EPROBE_DEFER EOPNOTSUPP EBUSY EIO ERANGE EAGAIN ENOSYS; do echo -n "-$e "; grep -rho -- "-$e" drivers/phy/phy-core.c drivers/phy/phy-core-mipi-dphy.c | wc -l; done`

⚠️ **`-ENOTSUPP` 和 `-EOPNOTSUPP` 在本框架里语义完全不同，不是笔误**：
**前者 = "PM runtime 没开"（内部信号）；后者 = "驱动没实现这个 ops"（对外结论）。**

### §9.2 ⚠️ 两个"一码多因"

| 码 | 原因 | 位置 |
|---|---|---|
| **`-EPROBE_DEFER`** | ① provider 尚未注册；**② `try_module_get(phy->ops->owner)` 失败**（**驱动模块正在卸载**） | `drivers/phy/phy-core.c:761-762` |
| **`-EINVAL`** | ① 参数真的非法；**② NULL phy 传给了 `configure`/`validate`**（§4.3）；**③ 非 DT 路径下 `string == NULL`** ——**这一条会先 `dev_WARN(dev, "missing string\n")`** | `:508` `:543` · `:753-754` |

⇒ **拿到 `-EINVAL` 先看有没有那条 `missing string` 的 warn**，**有就是第 ③ 类。**

### §9.3 `CONFIG_GENERIC_PHY=n` 的空桩：和 regmap 不一样

**空桩段是 `include/linux/phy/phy.h:286-535`**（`:285` 是 `#else`，`:536` 是 `#endif`）。

**43 个空桩的返回值分布（逐个数过）**：

| 返回什么 | 个数 | 典型 |
|---|---|---|
| **`return 0`** | **16** | **NULL-phy 分支 + `void` 语义的操作** |
| **`return -ENOSYS`** | **14** | **返回 `int` 的操作类 API** |
| **`ERR_PTR(-ENOSYS)`** | **11** | **返回指针的 `*_get` / `*_create` 类** |
| `return -ENODEV` | 2 | |

重跑：`awk 'NR>=286 && NR<=535' include/linux/phy/phy.h | grep -oE 'ERR_PTR\(-[A-Z]+\)|return -[A-Z]+|return 0' | sort | uniq -c | sort -rn`

**空桩仍然保留了 NULL-phy 的 NOP 语义**，这是一个有意的设计：

```c
/* include/linux/phy/phy.h:286-291 —— 每个操作类空桩都是这个形状 */
static inline int phy_pm_runtime_get(struct phy *phy)
{
	if (!phy)
		return 0;		/* NULL phy 依然静默成功 */
	return -ENOSYS;
}
```

⚠️ **与 regmap 的对比**（regmap 07 §1.1 / 01 §9）：

| | phy | regmap |
|---|---|---|
| **空桩会不会 `WARN`** | **0 处 WARN**（重跑 `awk 'NR>=286 && NR<=535' include/linux/phy/phy.h \| grep -c 'WARN'` ⇒ 0） | **45 处 `WARN_ONCE`** |
| **返回什么** | **`-ENOSYS` 为主，NULL phy 仍返回 0** | **统一 `-EINVAL`** |
| **诊断难度** | ⚠️ **更难** ——**没有栈可看，dmesg 里一个字都没有** | 相对容易 |

⚠️ **口径提醒**：**§9.1 那张表统计的是两个 `.c` 文件，`-ENOSYS` 为 0；
`-ENOSYS` 全部在头文件的空桩段里**（两处不矛盾，是范围不同）。
⇒ **看到 `-ENOSYS` 就说明 `CONFIG_GENERIC_PHY=n`**，**这是本框架最干脆的一条判据。**

---

## §10 阅读下一篇

| 想知道 | 去 |
|---|---|
| 结构体关系与易错类比 | `02-datastructures.md` |
| 引用计数 / 锁 / PM 的完整流程 | `03-internals.md` |
| 代码骨架 | `05-usage.md` |
| DTS 怎么写 | `06-devicetree.md` |
| 出问题看什么 | `07-debug.md` |
| 为什么设计成这样 | `08-feynman.md` |
