---
title: 使用指南
---

# phy 05：各角色代码骨架

## §0 篇首声明（必读）

⚠️ **本篇所有代码骨架都未经编译、未上机运行。** 它们的用途是**说明调用次序和参数关系**，
不是可直接粘贴的驱动。每个骨架里出现的符号都已在 `linux-6.1.158` 树上逐个 `grep` 确认存在
（清单见 99 §3），但**错误路径的资源回收、Kconfig 依赖、厂商寄存器细节都做了删减**。

### §0.1 本子系统有哪些角色

**phy 是框架型 + 类（class）型子系统**（99 §1），**角色按"谁造 PHY、谁用 PHY"切开**：

| # | 角色 | 它要做的事 | 树内数量级 | 本篇 |
|---|---|---|---|---|
| 1 | **PHY 驱动（provider）** | **填 `phy_ops`、`devm_phy_create`、注册 provider** | **约 150 个驱动 / 167 个 `phy_ops` 实例** | §1 |
| 2 | **多实例 provider** | **自己写 `of_xlate`** | **少数**（Tegra XUSB 等） | §2 |
| 3 | **消费者（控制器驱动）** | **`devm_phy_get` + 生命周期四件套** | **最多**（USB/PCIe/SATA/UFS/DSI 控制器） | §3 |
| 4 | **可选 PHY 的消费者** | **`devm_phy_optional_get`，允许没有** | **很常见** | §4 |
| 5 | **参数可配 PHY 的两侧** | **`phy_configure` / `ops->configure` + `union` 约定** | **MIPI / DP / LVDS** | §5 |
| 6 | **非 DT 场景**（lookup 表） | **`phy_create_lookup`** | **7 处调用**（04 §6.2） | §6 |

**角色 1 和角色 3 是"一造一用"的两端，绝大多数代码只属于其中一端。**

### §0.2 按任务找章节

| 我要… | 去 |
|---|---|
| 写一个新的 PHY 驱动 | §1 |
| 一个 PHY 驱动带多条链路 | §2 |
| 在控制器驱动里用 PHY | §3 |
| PHY 是可选的（有的板子没有） | §4 |
| 传参数给 PHY（MIPI/DP/LVDS） | §5 |
| 没有设备树 | §6 |
| **两个名字很像的函数该用哪个** | **§7（独立一节）** |
| 常见错误现象 | §8 |

---

## §1 角色 1：PHY 驱动（provider）

### §1.1 最小骨架（单实例）

```c
#include <linux/phy/phy.h>
#include <linux/platform_device.h>

struct foo_phy {
	void __iomem	*base;
	struct clk	*clk;
	struct phy	*phy;
};

/* ── 第 1 步：实现 phy_ops ────────────────────────────── */
static int foo_phy_init(struct phy *phy)
{
	struct foo_phy *fp = phy_get_drvdata(phy);	/* 取回私有数据 */

	return clk_prepare_enable(fp->clk);
}

static int foo_phy_exit(struct phy *phy)
{
	struct foo_phy *fp = phy_get_drvdata(phy);

	clk_disable_unprepare(fp->clk);
	return 0;
}

static int foo_phy_power_on(struct phy *phy)
{
	struct foo_phy *fp = phy_get_drvdata(phy);

	writel(FOO_PHY_ENABLE, fp->base + FOO_PHY_CTRL);
	return 0;
}

static int foo_phy_power_off(struct phy *phy)
{
	struct foo_phy *fp = phy_get_drvdata(phy);

	writel(0, fp->base + FOO_PHY_CTRL);
	return 0;
}

static const struct phy_ops foo_phy_ops = {
	.init		= foo_phy_init,
	.exit		= foo_phy_exit,
	.power_on	= foo_phy_power_on,
	.power_off	= foo_phy_power_off,
	.owner		= THIS_MODULE,		/* 别漏，见 §7.5 */
};

/* ── 第 2 步：probe 里造 phy + 注册 provider ──────────── */
static int foo_phy_probe(struct platform_device *pdev)
{
	struct foo_phy *fp;
	struct phy_provider *provider;

	fp = devm_kzalloc(&pdev->dev, sizeof(*fp), GFP_KERNEL);
	if (!fp)
		return -ENOMEM;

	fp->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(fp->base))
		return PTR_ERR(fp->base);

	fp->clk = devm_clk_get(&pdev->dev, NULL);
	if (IS_ERR(fp->clk))
		return PTR_ERR(fp->clk);

	/* 顺序：先 create，再 register provider（03 §0.2） */
	fp->phy = devm_phy_create(&pdev->dev, NULL, &foo_phy_ops);
	if (IS_ERR(fp->phy))
		return PTR_ERR(fp->phy);

	phy_set_drvdata(fp->phy, fp);		/* 必须在 create 之后 */

	provider = devm_of_phy_provider_register(&pdev->dev,
						 of_phy_simple_xlate);
	return PTR_ERR_OR_ZERO(provider);
}
```

⚠️ **五个必看点**：

| # | 点 | 依据 |
|---|---|---|
| 1 | **`.owner = THIS_MODULE` 不能漏** | **`phy_get()` 里 `try_module_get(phy->ops->owner)` 失败会返回 `-EPROBE_DEFER`**（`drivers/phy/phy-core.c:761-762`）⇒ **消费者会无限 defer** |
| 2 | **顺序必须是 `create` → `register provider`** | **`of_phy_simple_xlate()` 靠遍历 `phy_class` 找实例**（`drivers/phy/phy-core.c:713-721`），实例不存在就 `-ENODEV` |
| 3 | **`phy_set_drvdata()` 必须在 `phy_create()` 之后** | **它就是 `dev_set_drvdata(&phy->dev, data)`**（`include/linux/phy/phy.h:208-211`），phy 不存在时没地方放 |
| 4 | **`devm_phy_create` 的第二参数传 NULL 即可** | **框架会回落到 `dev->of_node`**（`drivers/phy/phy-core.c:969` 的 `node ?: dev->of_node`） |
| 5 | **不要自己开 `phy-supply` 那个 regulator** | **框架在 `phy_create()` 里 `regulator_get_optional(&phy->dev, "phy")`（`:978`），并在 `phy_power_on/off` 里自动开关**（02 §1.0.E） |

### §1.2 加上模式切换

```c
static int foo_phy_set_mode(struct phy *phy, enum phy_mode mode, int submode)
{
	struct foo_phy *fp = phy_get_drvdata(phy);

	switch (mode) {
	case PHY_MODE_USB_HOST:
		writel(FOO_MODE_HOST, fp->base + FOO_PHY_MODE);
		break;
	case PHY_MODE_USB_DEVICE:
		writel(FOO_MODE_DEVICE, fp->base + FOO_PHY_MODE);
		break;
	default:
		return -EINVAL;		/* 不支持的模式要显式拒绝 */
	}
	return 0;
}
```

⚠️ **`default: return -EINVAL` 很重要**：
**如果驱动干脆不实现 `set_mode`，`phy_set_mode_ext()` 会返回 0（成功）
并且把 `attrs.mode` 写成新值**（`drivers/phy/phy-core.c:407-410`）
⇒ **消费者以为切换成功了**（01 §5.1 / 08 §4）。
**实现了但不支持某个模式，就必须返回错误。**

### §1.3 DT 侧对应的最小节点

```dts
/* 见 06 篇完整说明 */
usbphy: phy@ff200000 {
	compatible = "foo,usb-phy";
	reg = <0x0 0xff200000 0x0 0x100>;
	#phy-cells = <0>;		/* 单实例用 0 */
	clocks = <&cru SCLK_USBPHY>;
	phy-supply = <&vcc5v0_usb>;	/* 框架自动处理，见 §1.1 第 5 点 */
};
```

---

## §2 角色 2：多实例 provider（自写 `of_xlate`）

### §2.1 为什么必须自己写

**`of_phy_simple_xlate()` 的匹配条件只有"`args->np == phy->dev.of_node`"**
（`drivers/phy/phy-core.c:716`）
⇒ **同一个 provider 造出的多个实例如果共用同一个 `of_node`，它会永远返回第一个。**

### §2.2 骨架

```c
#define FOO_NUM_PHYS	4

struct foo_ctrl {
	struct phy	*phys[FOO_NUM_PHYS];
};

/* 自己的 xlate：用 args->args[0] 当下标 */
static struct phy *foo_of_xlate(struct device *dev,
				struct of_phandle_args *args)
{
	struct foo_ctrl *fc = dev_get_drvdata(dev);

	if (args->args_count != 1)
		return ERR_PTR(-EINVAL);

	if (args->args[0] >= FOO_NUM_PHYS)
		return ERR_PTR(-EINVAL);

	return fc->phys[args->args[0]];
}

static int foo_probe(struct platform_device *pdev)
{
	struct foo_ctrl *fc;
	struct phy_provider *provider;
	int i;

	fc = devm_kzalloc(&pdev->dev, sizeof(*fc), GFP_KERNEL);
	if (!fc)
		return -ENOMEM;
	platform_set_drvdata(pdev, fc);	/* xlate 里要用 dev_get_drvdata */

	for (i = 0; i < FOO_NUM_PHYS; i++) {
		fc->phys[i] = devm_phy_create(&pdev->dev, NULL, &foo_phy_ops);
		if (IS_ERR(fc->phys[i]))
			return PTR_ERR(fc->phys[i]);
		phy_set_drvdata(fc->phys[i], &fc->lane[i]);
	}

	provider = devm_of_phy_provider_register(&pdev->dev, foo_of_xlate);
	return PTR_ERR_OR_ZERO(provider);
}
```

**对应 DTS 里 `#phy-cells = <1>`，消费者写 `phys = <&fooctrl 2>`。**

### §2.3 变体：每个实例一个 DT 子节点

**另一种常见布局是"provider 节点下每条链路一个子节点"**：

```dts
fooctrl: phy@ff300000 {
	compatible = "foo,multi-phy";
	reg = <0x0 0xff300000 0x0 0x1000>;
	#address-cells = <1>;
	#size-cells = <0>;

	foophy0: phy@0 {
		reg = <0>;
		#phy-cells = <0>;
	};
	foophy1: phy@1 {
		reg = <1>;
		#phy-cells = <0>;
	};
};
```

**这时每个 `devm_phy_create()` 传各自的子节点**：
```c
	for_each_child_of_node(pdev->dev.of_node, child) {
		fc->phys[i] = devm_phy_create(&pdev->dev, child, &foo_phy_ops);
		...
	}
```
⇒ **每个实例的 `of_node` 不同 ⇒ 可以直接用 `of_phy_simple_xlate`**（它按 `of_node` 匹配）。

⚠️ **DT 里子节点被多包一层时，才需要 `of_phy_provider_register_full()`**
（树里唯一的使用者是 `drivers/phy/tegra/xusb.c:228`，04 §5）。

---

## §3 角色 3：消费者（控制器驱动）

### §3.1 四件套骨架

```c
#include <linux/phy/phy.h>

struct foo_ctlr {
	struct phy	*phy;
};

static int foo_ctlr_probe(struct platform_device *pdev)
{
	struct foo_ctlr *fc;
	int ret;

	fc = devm_kzalloc(&pdev->dev, sizeof(*fc), GFP_KERNEL);
	if (!fc)
		return -ENOMEM;

	/* ① 取 —— 名字要和 DTS 的 phy-names 对上 */
	fc->phy = devm_phy_get(&pdev->dev, "usb2-phy");
	if (IS_ERR(fc->phy))
		return dev_err_probe(&pdev->dev, PTR_ERR(fc->phy),
				     "failed to get phy\n");

	/* ② 初始化 */
	ret = phy_init(fc->phy);
	if (ret)
		return ret;

	/* ③ 设模式（可选） */
	ret = phy_set_mode(fc->phy, PHY_MODE_USB_HOST);
	if (ret)
		goto err_exit;

	/* ④ 上电 —— 必须在 init 之后 */
	ret = phy_power_on(fc->phy);
	if (ret)
		goto err_exit;

	platform_set_drvdata(pdev, fc);
	return 0;

err_exit:
	phy_exit(fc->phy);
	return ret;
}

static int foo_ctlr_remove(struct platform_device *pdev)
{
	struct foo_ctlr *fc = platform_get_drvdata(pdev);

	/* 严格倒序，而且必须手动做 —— devm 不管这两步 */
	phy_power_off(fc->phy);
	phy_exit(fc->phy);
	return 0;
}
```

⚠️ **四个必看点**：

| # | 点 | 依据 |
|---|---|---|
| 1 | ⚠️ **`devm_phy_get` 只保证 `phy_put`，不保证 `power_off`/`exit`** | **devres 回调是 `devm_phy_release` → `phy_put`**（`drivers/phy/phy-core.c:28-33`）⇒ **忘了 `phy_power_off` 会让 PHY 一直上电**（03 §B3） |
| 2 | **顺序必须 `init` 早于 `power_on`** | **反了会打 `dev_warn "phy_power_on was called before phy_init"`**（`drivers/phy/phy-core.c:258-259`）；**只警告，不失败** |
| 3 | **`remove` 里严格倒序** | **`power_off` → `exit`** |
| 4 | **用 `dev_err_probe()` 处理 `-EPROBE_DEFER`** | **`phy_get` 的 `-EPROBE_DEFER` 是正常情况**（provider 还没上来，03 §A4.3） |

### §3.2 多个 PHY：按下标取

```c
	/* 不用名字，按 DTS 里 phys 属性的顺序取（01 §4.1） */
	for (i = 0; i < num_phys; i++) {
		fc->phys[i] = devm_of_phy_get_by_index(&pdev->dev,
						       pdev->dev.of_node, i);
		if (IS_ERR(fc->phys[i]))
			return PTR_ERR(fc->phys[i]);
	}
```

**官方文档点名这是给 ehci 这类"用多个 phy、名字没意义"的驱动用的**（`phy.rst`）。

### §3.3 电源管理里的配合

```c
static int foo_ctlr_suspend(struct device *dev)
{
	struct foo_ctlr *fc = dev_get_drvdata(dev);

	phy_power_off(fc->phy);		/* 不调 phy_exit，保留初始化状态 */
	return 0;
}

static int foo_ctlr_resume(struct device *dev)
{
	struct foo_ctlr *fc = dev_get_drvdata(dev);

	return phy_power_on(fc->phy);
}
```

⚠️ **`suspend` 里只 `power_off` 不 `exit`** ——**这样 `init_count` 保持 1，
resume 时不必重新走 `ops->init`。**
**两个计数各自独立就是为了支持这种用法**（02 §1.0.D / 08 §2）。

---

## §4 角色 4：可选 PHY 的消费者

### §4.1 骨架

```c
	fc->phy = devm_phy_optional_get(&pdev->dev, "usb2-phy");
	if (IS_ERR(fc->phy))
		return PTR_ERR(fc->phy);	/* 仍然要判 IS_ERR */

	/* fc->phy 可能是 NULL —— 下面这些调用都是安全的 NOP */
	ret = phy_init(fc->phy);		/* NULL ⇒ return 0 */
	if (ret)
		return ret;

	ret = phy_power_on(fc->phy);		/* NULL ⇒ return 0 */
	if (ret)
		goto err_exit;
```

**官方文档明写这个设计意图**（`phy.rst`）：
> *"All phy consumer calls on the NULL phy become NOPs."*

### §4.2 ⚠️⚠️ 但这句话有两个反例（本篇最重要的一条）

⚠️ **`phy_configure()` 和 `phy_validate()` 对 NULL 返回 `-EINVAL`，不是 0**：

```c
/* drivers/phy/phy-core.c:508-509 */
	if (!phy)
		return -EINVAL;
/* drivers/phy/phy-core.c:543-544 —— phy_validate 里一模一样 */
```

⇒ **下面这段代码在"板子上没有 PHY"时会失败**：

```c
	fc->phy = devm_phy_optional_get(dev, "dphy");	/* 可能是 NULL */
	...
	ret = phy_configure(fc->phy, &opts);		/* ⚠️ NULL ⇒ -EINVAL */
	if (ret)
		return ret;				/* ⚠️ probe 失败 */
```

**正确写法：显式判 NULL**：

```c
	if (fc->phy) {
		ret = phy_configure(fc->phy, &opts);
		if (ret)
			return ret;
	}
```

**`phy_reset` / `phy_calibrate` / `phy_set_media` / `phy_set_speed` 都是安全的**
（各自 `if (!phy || !phy->ops->xxx) return 0`，01 §4.3 有完整的九个 API 清单）。

### §4.3 什么时候会拿到 NULL（四条路，01 §4.1 / 02 §3）

| 情形 | 机理 |
|---|---|
| **DTS 里没写 `phys` 属性** | `of_parse_phandle_with_args` 失败 ⇒ `-ENODEV`（`drivers/phy/phy-core.c:577-578`） |
| **`phy-names` 里没有你要的名字** | `of_property_match_string` 返回负数 ⇒ 当下标用 ⇒ 同样 `-ENODEV` |
| **PHY 节点 `status = "disabled"`** | `dev_warn "Requested PHY is disabled"` + `-ENODEV`（`:593-596`） |
| ⚠️ **PHY 节点是 `compatible = "usb-nop-xceiv"`** | **框架显式拒绝** ⇒ `-ENODEV`（`:581-584`，03 §A4.2） |

⚠️ **四条路都收敛到 `-ENODEV`** ⇒ **`devm_phy_optional_get` 全部转成 NULL**
⇒ **"DTS 写错"和"故意没写"在代码里无法区分**（06 §4 / 07 §5）。

---

## §5 角色 5：参数可配的 PHY（`configure`）

### §5.1 消费者侧（以 MIPI D-PHY 为例）

```c
#include <linux/phy/phy.h>
#include <linux/phy/phy-mipi-dphy.h>

	union phy_configure_opts opts = { 0 };
	int ret;

	/* ① 用辅助函数算一套默认时序（04 §1.2） */
	ret = phy_mipi_dphy_get_default_config(pixel_clock, bpp, lanes,
					       &opts.mipi_dphy);
	if (ret)
		return ret;

	/* ② 先问能不能（可选但推荐） */
	ret = phy_validate(dphy, PHY_MODE_MIPI_DPHY, 0, &opts);
	if (ret == -EOPNOTSUPP)
		; /* 驱动没实现 validate，跳过这一步 */
	else if (ret)
		return ret;

	/* ③ 真配 —— kerneldoc 要求 phy_init() 必须先调过 */
	ret = phy_init(dphy);
	if (ret)
		return ret;

	ret = phy_configure(dphy, &opts);
	if (ret)
		goto err_exit;

	ret = phy_power_on(dphy);
```

⚠️ **三个陷阱**：

| # | 陷阱 | 说明 |
|---|---|---|
| 1 | ⚠️ **填错 `union` 成员是静默错误** | **`union phy_configure_opts` 没有 tag 字段**（04 §2.2）⇒ **给 DP PHY 传 `opts.mipi_dphy` 不会报错，驱动会读到重叠的垃圾** |
| 2 | **`-EOPNOTSUPP` 要单独处理** | **它表示"驱动没实现这个 ops"，不是"参数错"**（01 §6.2） |
| 3 | ⚠️ **`phy_configure` 不会自动唤醒 PHY** | **它不做 `phy_pm_runtime_get_sync`**（03 §A6 第 ③ 类）⇒ **时序由消费者保证** |

### §5.2 驱动侧

```c
static int foo_dphy_configure(struct phy *phy, union phy_configure_opts *opts)
{
	struct foo_phy *fp = phy_get_drvdata(phy);
	struct phy_configure_opts_mipi_dphy *cfg = &opts->mipi_dphy;

	/* 先校验，再写寄存器 */
	if (phy_mipi_dphy_config_validate(cfg))
		return -EINVAL;

	writel(cfg->hs_prepare, fp->base + FOO_T_HS_PREPARE);
	/* … */
	return 0;
}

static int foo_dphy_validate(struct phy *phy, enum phy_mode mode, int submode,
			     union phy_configure_opts *opts)
{
	if (mode != PHY_MODE_MIPI_DPHY)
		return -EINVAL;

	return phy_mipi_dphy_config_validate(&opts->mipi_dphy);
}
```

**`phy_mipi_dphy_config_validate()` 是框架给的**（`drivers/phy/phy-core-mipi-dphy.c:84`，
声明在 `include/linux/phy/phy-mipi-dphy.h:282`）——**需要 `select GENERIC_PHY_MIPI_DPHY`**（04 §1.4）。

---

## §6 角色 6：非 DT 场景（lookup 表）

```c
	/* provider 侧：造完 phy 之后登记一条映射 */
	ret = phy_create_lookup(phy, "usb2-phy", "foo-usb-controller.0");
	/*                            ↑ con_id     ↑ dev_id = 消费者的 dev_name() */
	if (ret)
		goto err;

	/* 拆除时 */
	phy_remove_lookup(phy, "usb2-phy", "foo-usb-controller.0");
```

**消费者侧代码完全不变** ——**`phy_get(dev, "usb2-phy")` 在 `dev->of_node` 为空时
自动走 `phy_find()` 查这张表**（`drivers/phy/phy-core.c:756`）。

⚠️ **两个陷阱**：

| # | 陷阱 | 依据 |
|---|---|---|
| 1 | ⚠️ **两个字符串不被复制** | **`phy_create_lookup` 只存指针**（`drivers/phy/phy-core.c:75-77`）⇒ **不能传栈上的临时缓冲**（02 §5.5） |
| 2 | **`dev_id` 必须精确等于消费者的 `dev_name()`** | **比较用 `strcmp`**（`:122`）⇒ **拼错一个字符就查不到，返回 `-ENODEV`** |

**树里的现成例子**：**`include/linux/phy/ulpi_phy.h` 的 `ulpi_phy_create()`**
（它把 `con_id` 硬编码成 `"usb2-phy"`、`dev_id` 用 `dev_name(ulpi->dev.parent)`，04 §3）。

---

## §7 近邻符号区分（独立一节，必读）

### §7.1 ⚠️ 组 1：`phy_init` vs `phy_power_on`（最基础也最常错的一组）

| | `phy_init(phy)` | `phy_power_on(phy)` |
|---|---|---|
| 位置 | `drivers/phy/phy-core.c:245` | `drivers/phy/phy-core.c:322` |
| 计数 | **`init_count`** | **`power_count`** |
| 回调 | `ops->init` | `ops->power_on` |
| **动 regulator 吗** | **不动** | **动**（`phy->pwr`，`:329-333`） |
| 语义 | **"把 PHY 配好，但可以不供电"** | **"让 PHY 真的开始工作"** |

**错用会怎样**：
**只 `power_on` 不 `init`** ⇒ **`dev_warn "phy_power_on was called before phy_init"`（`:259`），
但流程继续** ⇒ **`ops->power_on` 在一个没初始化的 PHY 上跑，行为取决于驱动，通常是链路起不来。**
**只 `init` 不 `power_on`** ⇒ **PHY 配好了但没供电，同样起不来，而且一条警告都没有。**

### §7.2 ⚠️ 组 2：`phy_get` vs `phy_optional_get`

| | `phy_get` / `devm_phy_get` | `phy_optional_get` / `devm_phy_optional_get` |
|---|---|---|
| 找不到时 | **`ERR_PTR(-ENODEV)`** | **`NULL`** |
| 判断方式 | `IS_ERR(phy)` | **`IS_ERR(phy)` 仍然要判**（其他错误照样是 ERR_PTR） |
| 转换范围 | — | ⚠️ **只转 `-ENODEV`**（`drivers/phy/phy-core.c:789-790` / `:843-844`） |

**错用会怎样**：
**必需的 PHY 用了 `optional` 版** ⇒ **DTS 漏写 `phys` 时 probe 照样成功、PHY 全程是 NULL
⇒ 所有 phy 调用变成 NOP ⇒ 硬件不工作但没有任何错误信息。**
**可选的 PHY 用了非 optional 版** ⇒ **没有 PHY 的板子 probe 直接失败。**

### §7.3 ⚠️ 组 3：`phy_set_mode` vs `phy_set_mode_ext`

| | `phy_set_mode(phy, mode)` | `phy_set_mode_ext(phy, mode, submode)` |
|---|---|---|
| 是什么 | **宏**（`include/linux/phy/phy.h:230`） | **函数**（`drivers/phy/phy-core.c:399`，导出于 `:415`） |
| 展开成 | **`phy_set_mode_ext(phy, mode, 0)`** | — |

**错用会怎样**：**两者可以互换**（前者就是后者 `submode = 0`）——
**这是本节唯一"错用没有后果"的一组。**
⚠️ **但要注意宏在头文件里出现两次**（`:230` 和 `:360`）——
**后者是 `CONFIG_GENERIC_PHY=n` 的空桩段**（01 §9.3）。

### §7.4 ⚠️ 组 4：`phy_put` vs `of_phy_put` vs `devm_phy_put`

| API | 参数个数 | 位置 | 做什么 |
|---|---|---|---|
| `phy_put(dev, phy)` | **2** | `drivers/phy/phy-core.c:670` | **`device_link_remove` + `of_phy_put`** |
| `of_phy_put(phy)` | **1** | `drivers/phy/phy-core.c:648` | **`ops->release` + `module_put` + `put_device`** |
| `devm_phy_put(dev, phy)` | **2** | `drivers/phy/phy-core.c:685` | **`devres_release`（会间接调 `phy_put`）** |

⚠️ **官方文档写的是 `void phy_put(struct phy *phy)`（一个参数），是过时的**（01 §4.2 / 03 §C2）。

**错用会怎样**：
**`devm_phy_get` 拿的 phy 手动调 `phy_put`** ⇒ **device 引用被放两次**（一次手动、
一次 devres）⇒ **use-after-free。**
**`phy_get` 拿的 phy 调 `devm_phy_put`** ⇒ **找不到 devres ⇒
`dev_WARN_ONCE "couldn't find PHY resource"`（`:693`）⇒ 引用泄漏。**

### §7.5 ⚠️ 组 5：`ops->owner` vs `phy_provider->owner`

| | `phy->ops->owner` | `phy_provider->owner` |
|---|---|---|
| 谁填 | **驱动在 `phy_ops` 里写 `.owner = THIS_MODULE`** | **注册宏自动填**（`include/linux/phy/phy.h:197` 的 `THIS_MODULE`） |
| 什么时候被 get | **`phy_get()`（`:761`）· `of_phy_get()`（`:633`）** | **`_of_phy_get()` 查找期间（`:588`）** |
| 什么时候被 put | **`of_phy_put()`（`:658`）** | **`of_xlate` 返回后立刻（`:602`）** |
| 持有时长 | **消费者持引用期间一直** | **只在查找的几微秒** |

**错用会怎样**：**`phy_ops` 里漏了 `.owner`**（NULL）⇒
**`try_module_get(NULL)` 的行为不是"成功"** ⇒ **`phy_get()` 返回 `-EPROBE_DEFER`
⇒ 消费者永远 defer，dmesg 里看不到任何 phy 相关错误。**
⚠️ **这是新写 PHY 驱动最容易犯、最难查的一个错。**

### §7.6 ⚠️ 组 6：`-ENOTSUPP` vs `-EOPNOTSUPP`

| 码 | 谁产生 | 会不会传到消费者 |
|---|---|---|
| `-ENOTSUPP` | **PM runtime（`phy_pm_runtime_*`）** | ⚠️ **基本不会** ——**框架三处显式吃掉它**（`drivers/phy/phy-core.c:253-255` / `:293-295` / `:336-339`） |
| `-EOPNOTSUPP` | **`phy_configure` / `phy_validate` 发现驱动没实现 ops** | **会** |

**错用会怎样**：**把 `-ENOTSUPP` 当成"驱动没实现"来处理** ⇒
**实际上你几乎见不到它** ⇒ **写了永远走不到的分支。**
**判据：只有 `phy_configure`/`phy_validate` 会给你 `-EOPNOTSUPP`，其余"没实现"一律返回 0。**

### §7.7 ⚠️ 组 7：通用 PHY 的 `phy_init` vs 网络 PHY 的 `phy_init_hw`

⚠️ **两个子系统的 API 名字接近，而且都叫 "phy_"**（01 §0.1 / 02 §1.0.A）：

| 通用 PHY（本文档） | 网络 PHY（PHYLIB，不是本文档） |
|---|---|
| `phy_init(phy)` | `phy_init_hw(phydev)` |
| `phy_get(dev, name)` | `phy_find_first(bus)` / `of_phy_get_and_connect()` |
| `phy_set_speed(phy, speed)` | `phy_set_max_speed(phydev, max_speed)` |

**错用会怎样**：**类型不同，编译器会拦住**（`struct phy *` vs `struct phy_device *`）——
**这是唯一"编译器会救你"的一组。**
⚠️ **但 `#include` 写错（`<linux/phy.h>` vs `<linux/phy/phy.h>`）会导致一堆莫名的
"未声明的标识符"** ⇒ **报错信息不会提示你 include 错了。**

---

## §8 常见错误现象 → 原因

| 现象 | 最可能的原因 | 去哪查 |
|---|---|---|
| **控制器驱动永远 `-EPROBE_DEFER`** | **① PHY 驱动没 probe（正常，等一等）；⚠️ ② `phy_ops` 漏了 `.owner`** | §7.5 / 03 §A4.3 |
| **`devm_phy_get` 返回 `-ENODEV`** | **DTS 的 `phys` / `phy-names` 没对上，或 PHY 节点 `disabled`** | §4.3 / 06 §4 |
| **probe 成功但硬件完全不工作，dmesg 干净** | ⚠️ **用了 `optional_get` 而 DTS 漏写 `phys` ⇒ phy 是 NULL ⇒ 全是 NOP** | §7.2 |
| **`dev_warn: phy_power_on was called before phy_init`** | **调用顺序反了** | §7.1 |
| **`phy_configure` 返回 `-EINVAL`，但参数明明是对的** | ⚠️ **phy 是 NULL**（可选 PHY） | §4.2 |
| **`phy_configure` 返回 `-EOPNOTSUPP`** | **驱动没实现 `ops->configure`** | 01 §6.2 |
| **`phy_set_mode` 返回成功但模式没变** | ⚠️ **驱动没实现 `ops->set_mode`** | §1.2 / 01 §5.1 |
| **regulator 报 "unbalanced disables"** | ⚠️ **`phy_power_off` 调多了**（计数下溢） | 02 §1.0.D / 03 §C3 |
| **`dev_WARN_ONCE: couldn't find PHY resource`** | **devm 与非 devm 版混用** | §7.4 |
| **一堆"未声明的标识符"** | ⚠️ **`#include <linux/phy.h>` 写错了，应该是 `<linux/phy/phy.h>`** | §7.7 |
| **返回 `-ENOSYS`** | **`CONFIG_GENERIC_PHY=n`** | 01 §9.3 |

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| DTS 怎么写 | `06-devicetree.md` |
| 出问题看什么输出 | `07-debug.md` |
| 为什么两个计数、为什么 NULL 合法 | `08-feynman.md` |
| API 完整语义 | `01-api.md` |
| 内部流程 | `03-internals.md` |
