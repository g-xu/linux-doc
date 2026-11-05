---
title: 使用指南
---

# 05 使用举例：五种角色的代码骨架

> ⚠️ **本篇所有代码骨架都没有编译过、没有在硬件上跑过。**
> 它们是"结构正确"的示意，不是可直接复制的补丁。
> 每个符号名都经过全树 grep 确认在 v6.1.158 存在，但**参数顺序、
> 错误处理的完整性、头文件是否齐全都需要你自己再核一遍**。
> 这一免责声明同时记在 99 §5.1。
>
> arm64-only。

## §0 本子系统有哪些角色

| # | 角色 | 你在写什么 | 主要头文件 | 本篇章节 |
|---|---|---|---|---|
| 1 | **消费者** | 用电的设备驱动（MMC / 摄像头 / 网卡 / 传感器…） | `include/linux/regulator/consumer.h` | §1 |
| 2 | **提供者（PMIC 驱动）** | 一颗电源管理芯片的驱动 | `include/linux/regulator/driver.h` | §2 |
| 3 | **中断错误上报者** | PMIC 驱动的一部分：过压/过温中断 | `include/linux/regulator/driver.h` | §3 |
| 4 | **coupler 作者** | SoC 级的联动调压策略 | `include/linux/regulator/coupler.h` | §4 |
| 5 | **板级配置者** | DTS | — | 完整内容在 **06 篇** |

**99% 的人只需要 §1**。写 §2 的人是 SoC/PMIC 厂商，
§3 §4 更少。

还有一个"角色"不在这张表里：**平台数据（`regulator_init_data`）作者**。
arm64 上不存在 —— 约束全部从 DTS 来（06 篇 §1）。
`include/linux/regulator/machine.h` 那套 API 在 arm64 上只被
`of_regulator.c` 内部使用。

---

## §1 消费者：用电的设备驱动

### §1.1 最常见的形态：只要它一直开着

**6.1 里的推荐写法只有一行**：

```c
#include <linux/regulator/consumer.h>

static int my_probe(struct platform_device *pdev)
{
	int ret;

	/* DTS 里的 vdd-supply = <&some_reg>; */
	ret = devm_regulator_get_enable(&pdev->dev, "vdd");
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to enable vdd\n");

	/* ... 你的硬件初始化 ... */
	return 0;
}
```

`devm_regulator_get_enable`（`drivers/regulator/devres.c:130`）
返回 `int` 而不是指针 —— get + enable 合一，
拆除时 driver core 自动 disable + put。

**代价：你拿不到 `struct regulator *`**，
所以中途想调压、想关掉、想查电压的**都不能用它**。
这种情况用 §1.2。

`dev_err_probe` 会自动把 `-EPROBE_DEFER` 降级成 dbg，
不刷屏 —— regulator 的 `-EPROBE_DEFER` 极常见（03 篇 §A2），
所以这个包装几乎是必须的。

### §1.2 需要中途操作的形态

```c
struct my_priv {
	struct regulator *vdd;
	/* ... */
};

static int my_probe(struct platform_device *pdev)
{
	struct my_priv *priv;
	int ret;

	priv = devm_kzalloc(&pdev->dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->vdd = devm_regulator_get(&pdev->dev, "vdd");
	if (IS_ERR(priv->vdd))
		return dev_err_probe(&pdev->dev, PTR_ERR(priv->vdd),
				     "failed to get vdd\n");

	ret = regulator_enable(priv->vdd);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to enable vdd\n");

	/* enable 是手动的，所以 disable 也要手动 —— 注册一个 action */
	ret = devm_add_action_or_reset(&pdev->dev,
				       my_disable_vdd, priv->vdd);
	if (ret)
		return ret;

	platform_set_drvdata(pdev, priv);
	return 0;
}
```

（`my_disable_vdd` 要写在 `my_probe` 之前：）

```c
static void my_disable_vdd(void *data)
{
	regulator_disable(data);
}
```

**`devm_regulator_get` 只自动 `put`，不自动 `disable`。**
忘了配对的 `regulator_disable` ⇒ 这一路永远开着，
而且 `regulator_unregister` 时会 `WARN(enable_count == 0, ...)`（03 篇 §A5.3）。

用 `devm_add_action_or_reset` 而不是在 `remove` 里写 disable ——
错误路径也会被覆盖到。

### §1.3 `regulator_get` 在 arm64 上几乎不会失败

**这是消费者侧最重要的一条**：
arm64 上 `have_full_constraints()` 恒真（03 篇 §A7.1），
所以 DTS 里**漏写** `vdd-supply` 的后果是：

```
<你的设备>: supply vdd not found, using dummy regulator
```

一条 `dev_warn`（`core.c:2230`）——
然后 `regulator_get` **返回一个有效指针**，
后续 `regulator_enable` **返回 0**，
你的驱动**probe 成功、跑得好好的、但那一路电根本没被打开**。

三种 get 的区别，按"漏写 supply 时的行为"排：

| API | 漏写 supply 时 | 什么时候用 |
|---|---|---|
| `regulator_get` / `devm_regulator_get` | 静默给 dummy + warn | 默认。**这一路是必需的** |
| `regulator_get_optional` / `devm_regulator_get_optional` | 返回 `-ENODEV` | **这一路真的可选**（比如某些板子上外挂供电，某些板子内置） |
| `regulator_get_exclusive` | 不给 dummy，`-ENODEV` | 极少用：要求"只有我能操作这一路" |

**如果你的设备没电就不能工作，用 `devm_regulator_get`
并在 probe 里加一句自检 —— 核心不会替你把关。**
自检方法见 07 篇 §2.1（`dmesg | grep dummy`）。

反过来一条常见误用：把"这一路在某些板子上不存在"写成
`devm_regulator_get` 然后忽略错误 —— 这在 arm64 上**永远不会报错**，
所以判断逻辑永远走不到。这种情况必须用 `_optional`。

### §1.4 多路一起要

```c
static struct regulator_bulk_data my_supplies[] = {
	{ .supply = "vdd" },
	{ .supply = "vdda" },
	{ .supply = "vddio" },
};

	ret = devm_regulator_bulk_get(&pdev->dev,
				      ARRAY_SIZE(my_supplies), my_supplies);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to get supplies\n");

	ret = regulator_bulk_enable(ARRAY_SIZE(my_supplies), my_supplies);
```

`regulator_bulk_data`（`include/linux/regulator/consumer.h:186`）
的字段：

```c
struct regulator_bulk_data {
	const char *supply;
	int init_load_uA;
	struct regulator *consumer;   /* 出参 */
	/* private: Internal use */
	int ret;
};
```

**`consumer` 是出参**，所以数组不能是 `const`。
想放在 `static const` 里就用 `devm_regulator_bulk_get_const`
（`devres.c:254`），它内部拷一份。

更省的写法：`devm_regulator_bulk_get_enable`（`devres.c:359`）——
get + enable + **失败自动回滚**，一个调用搞定。
签名是 `(dev, num, const char * const *id)`，直接传名字数组，
连 `regulator_bulk_data` 都不用声明。

### §1.5 调压：返回 0 不代表电压是你要的值

```c
	/* 请求一个区间，不是一个值 */
	ret = regulator_set_voltage(priv->vdd, 1100000, 1300000);
	if (ret)
		return ret;

	/* 想知道实际是多少必须回读 */
	uV = regulator_get_voltage(priv->vdd);
```

三条必须知道的（详见 03 篇 §A6 和 §B3）：

1. **参数是区间不是值。** 写 `set_voltage(reg, 1200000, 1200000)`
   是合法的，但硬件档位对不上就 `-EINVAL`。
   **给一个能接受的区间，别写死一个值。**
2. **返回 0 可能什么都没做。** `valid_ops_mask` 没有
   `CHANGE_VOLTAGE`、但你的区间落在约束区间内 ⇒
   核心只记账然后返回 0（`core.c:3782-3789`）。
3. **实际电压由所有消费者的区间交集决定**（`core.c:527`）——
   同一路上另一个消费者要求 `[1.2V, 1.2V]`，你要 `[1.1V, 1.3V]`，
   结果是 1.2V。你的 `set_voltage` 返回 0，但你的偏好被忽略了。

交集为空时的日志是
`Restricting voltage, %u-%uuV`（`core.c:550`）+ `-EINVAL`。

### §1.6 通知：只有 `PRE_DISABLE` 能否决

```c
static int my_regulator_event(struct notifier_block *nb,
			      unsigned long event, void *data)
{
	if (event & REGULATOR_EVENT_PRE_DISABLE) {
		if (my_device_is_busy())
			return NOTIFY_BAD;   /* 只有这个事件的返回值被看 */
	}
	if (event & REGULATOR_EVENT_OVER_TEMP)
		dev_warn(my_dev, "regulator over temperature\n");

	return NOTIFY_OK;
}

	priv->nb.notifier_call = my_regulator_event;
	ret = devm_regulator_register_notifier(priv->vdd, &priv->nb);
```

**`PRE_DISABLE` 之外的所有事件，返回值都被丢弃**
（03 篇 §B5.1）—— `OVER_VOLTAGE`、`OVER_TEMP`、`FAIL`
只能用来记日志或触发自己的降级逻辑，**不能阻止任何事**。

另有一条：通知链**不向上游传播**（`core.c:4879` 只有一行）。
上游 PMIC 过温不会通知到你 —— 你只收到自己这一路的事件。

### §1.7 消费者侧检查清单

| 检查 | 为什么 |
|---|---|
| ☐ 用了 `dev_err_probe` 而不是 `dev_err` | `-EPROBE_DEFER` 会刷屏 |
| ☐ `regulator_enable` 有配对的 disable（或用 `devm_regulator_get_enable`） | 不配对 ⇒ 永远开着 + unregister 时 WARN |
| ☐ probe 后确认过 `dmesg` 里没有 `using dummy regulator` | 唯一能发现"DTS 漏了 supply"的方法 |
| ☐ "真的可选"的路用了 `_optional` 变体 | 否则判断分支永远走不到 |
| ☐ `set_voltage` 之后回读了 `get_voltage` | 返回 0 不代表值对 |
| ☐ 没有在通知回调里调 `regulator_*` API | 会重入核心（03 篇 §B1.4） |

---

## §2 提供者：一颗 PMIC 的驱动

### §2.1 最小可用骨架

本树里 `drivers/regulator/88pg86x.c` 只有 114 行，
是"regmap + 线性区间"这种最常见形态的**完整范例**。
下面按它的结构走一遍。

**第 1 步：`ops` 表 —— 全部用现成 helper**

```c
static const struct regulator_ops my_ops = {
	.set_voltage_sel = regulator_set_voltage_sel_regmap,
	.get_voltage_sel = regulator_get_voltage_sel_regmap,
	.list_voltage    = regulator_list_voltage_linear_range,
	/* 有开关位就再加这三个： */
	.enable          = regulator_enable_regmap,
	.disable         = regulator_disable_regmap,
	.is_enabled      = regulator_is_enabled_regmap,
};
```

**一行自己的代码都不用写** —— 全部来自 helpers.c（04 篇 §1）。
真实例子：`88pg86x.c:8-12` 只填了三个。

注意 `88pg86x.c` **没有填 `map_voltage`** —— 因为核心会替你选。
`regulator_map_voltage`（`core.c:3475`）在 `ops->map_voltage` 为 NULL 时
**按你填的 `list_voltage` 是哪一个函数来反推**：

```
if (desc->ops->map_voltage)                                  [:3480] → 用你的
if (list_voltage == regulator_list_voltage_linear)           [:3483] → map_voltage_linear
if (list_voltage == regulator_list_voltage_linear_range)     [:3486] → map_voltage_linear_range
if (list_voltage == regulator_list_voltage_pickable_...)     [:3489] → pickable 版
return regulator_map_voltage_iterate(...)                    [:3494] → 兜底 O(n)
```

**判据是函数指针相等**，不是"看 desc 里填了什么"。
所以你**自己实现了 `list_voltage`** 就一定落到 `:3494` 那个
O(n) 兜底 —— 想要 O(1) 就必须自己也填 `map_voltage`。
但如果你填了 `regulator_map_voltage_linear_range`
**却忘了填 `linear_ranges`，是 `BUG_ON` 而不是错误返回**（04 篇 §1.2）。

**第 2 步：电压区间表**

```c
static const struct linear_range my_buck1_ranges[] = {
	REGULATOR_LINEAR_RANGE(      0,  0, 10,     0),   /* 前 11 档是"关" */
	REGULATOR_LINEAR_RANGE(1000000, 11, 34, 25000),
	REGULATOR_LINEAR_RANGE(1600000, 35, 47, 50000),
};
```

宏定义在 `include/linux/regulator/driver.h:53`，
四个参数是 `(_min_uV, _min_sel, _max_sel, _step_uV)`。

**`_step_uV` 为 0 的区间表示"这些选择子都对应同一个电压"** ——
上面第一行的 11 个选择子（0~10）全是 0V，也就是"输出关闭"。
真实例子：`88pg86x.c:14-18`。

**第 3 步：`regulator_desc` 数组**

```c
static const struct regulator_desc my_regulators[] = {
	{
		.id              = 0,
		.type            = REGULATOR_VOLTAGE,
		.name            = "buck1",
		.of_match        = of_match_ptr("buck1"),      /* 关键 */
		.n_voltages      = 11 + 24 + 13,               /* 必须和区间表对上 */
		.linear_ranges   = my_buck1_ranges,
		.n_linear_ranges = 3,
		.vsel_reg        = 0x24,
		.vsel_mask       = 0xff,
		.ops             = &my_ops,
		.owner           = THIS_MODULE,
	},
	/* buck2 ... */
};
```

对照 `88pg86x.c:26-53`。三个字段最容易写错：

| 字段 | 陷阱 |
|---|---|
| `of_match` | **这是核心从 DTS 找约束的唯一钩子**。写错了 ⇒ 该路的所有 DTS 约束被静默忽略（06 篇 §3） |
| `n_voltages` | 必须等于所有 `linear_ranges` 覆盖的选择子总数。写小了高档位取不到，写大了 `list_voltage` 会越界 |
| `owner` | 必须是 `THIS_MODULE` —— 核心用它 `try_module_get` 防 rmmod（03 篇 §A8） |

多路共享一个 DTS 子节点容器时还要填 `regulators_node`
（`driver.h:366`），比如 `.regulators_node = "regulators"`。
不填时核心从 PMIC 节点本身找 `of_match` 命名的子节点。

**第 4 步：probe 里循环注册**

```c
static int my_probe(struct i2c_client *i2c)
{
	struct regulator_config config = { .dev = &i2c->dev };
	struct regmap *regmap;
	int id, ret;

	regmap = devm_regmap_init_i2c(i2c, &my_regmap_config);
	if (IS_ERR(regmap))
		return dev_err_probe(&i2c->dev, PTR_ERR(regmap),
				     "regmap init failed\n");

	for (id = 0; id < ARRAY_SIZE(my_regulators); id++) {
		struct regulator_dev *rdev;

		rdev = devm_regulator_register(&i2c->dev,
					       &my_regulators[id], &config);
		if (IS_ERR(rdev))
			return dev_err_probe(&i2c->dev, PTR_ERR(rdev),
					     "failed to register %s\n",
					     my_regulators[id].name);
	}
	return 0;
}
```

对照 `88pg86x.c:60-85`。

**`config.regmap` 没有显式填。**
核心在 `regulator_register` 里有三级回退（`core.c:5613-5618`）：
`config->regmap` → `dev_get_regmap(config->dev, NULL)` →
`dev_get_regmap(config->dev->parent, NULL)`。
i2c 设备上 `devm_regmap_init_i2c` 已经把 regmap 挂在 `&i2c->dev` 上，
所以第二级就命中。**但如果你的 regmap 挂在别处，必须显式填
`config.regmap`**，否则那些 `_regmap` helper 全都拿到 NULL。

`struct regulator_config` 是**栈上的临时结构**，
`regulator_register` 返回后就可以销毁 —— 核心把需要的字段拷走了
（02 篇 §4.2）。唯一例外是 `ena_gpiod`：**所有权被移交给核心**
（`core.c:5602`），你不要再 `gpiod_put`。

### §2.2 「我实现了回调但调不动」

这是提供者侧最常见的困惑，答案在 03 篇 §B2：

**核心的授权判据只有 `constraints->valid_ops_mask`，
完全不看你的 `ops` 表。**

```
regulator_ops_is_valid()  [core.c:117]
  只看 rdev->constraints->valid_ops_mask
```

而 `valid_ops_mask` 来自 **DTS**：

| DTS 属性 | 置的位 |
|---|---|
| `regulator-min-microvolt` + `regulator-max-microvolt`（两者**不等**） | `REGULATOR_CHANGE_VOLTAGE`（`of_regulator.c:106-107`） |
| `regulator-min-microamp` + `regulator-max-microamp`（两者不等） | `REGULATOR_CHANGE_CURRENT`（`:125-126`） |
| `regulator-allowed-modes` + 驱动填了 `of_map_mode` | `REGULATOR_CHANGE_MODE`（`:217-219`） |
| `regulator-allow-bypass` | `REGULATOR_CHANGE_BYPASS`（`:135-136`） |
| `regulator-allow-set-load` | `REGULATOR_CHANGE_DRMS`（`:138-139`） |
| **没写** `regulator-always-on` | `REGULATOR_CHANGE_STATUS`（`:130-131`） |

**最后一行是反直觉的**：`CHANGE_STATUS` 不是"有开关硬件就给"，
而是"**没声明 always-on 就给**"。所以
`regulator-min-microvolt = <1800000>; regulator-max-microvolt = <1800000>;`
这种写死一个值的节点，`CHANGE_VOLTAGE` 拿不到（min == max），
但 `CHANGE_STATUS` 会拿到。

完整映射在 06 篇 §1.1。
**调试时先看 `/sys/class/regulator/regulator.N/` 下的属性**
（07 篇 §3）——`min_microvolts` / `max_microvolts` 是否等于你 DTS 写的。

### §2.3 提供者侧检查清单

| 检查 | 为什么 |
|---|---|
| ☐ 用 `devm_regulator_register` 而不是 `regulator_register` | 免手写拆除路径（03 篇 §A8 那条 `while (use_count--)` 不好写对） |
| ☐ `of_match` 字符串和 DTS 子节点名一致 | 写错 ⇒ 约束静默丢失 |
| ☐ `n_voltages` 和 `linear_ranges` 覆盖范围对上 | 越界或取不到高档位 |
| ☐ `.owner = THIS_MODULE` | 防 rmmod |
| ☐ 填了 `map_voltage_linear_range` 就必须填 `linear_ranges` | 否则 `BUG_ON` |
| ☐ 填了 `map_voltage_*` 和 `list_voltage_*` 且两者自洽 | `helpers.c:430-436` 会反算验证 |
| ☐ regmap 挂在 `config.dev` 或它的 parent 上，否则显式填 `config.regmap` | 三级回退可能全落空 |
| ☐ `ops` 回调里**没有**调 `regulator_*` 消费者 API、没有长睡眠 | `rdev->mutex` 被持有（03 篇 §B1.4） |
| ☐ 没有把 `regulator_config` 存起来长期用 | 它是临时结构 |
| ☐ `ena_gpiod` 交出去之后不再自己管 | 所有权已移交 |

---

## §3 中断错误上报：`regulator_irq_helper`

只有带过压/过温/欠压中断引脚的 PMIC 需要这一节。
机制见 04 篇 §3。

```c
static int my_map_event(int irq, struct regulator_irq_data *rid,
			unsigned long *dev_mask)
{
	struct my_priv *priv = rid->data;
	unsigned int val;
	int ret;

	ret = regmap_read(priv->regmap, MY_IRQ_STATUS, &val);
	if (ret)
		return ret;

	*dev_mask = 0;
	if (val & MY_OVP_BIT) {
		*dev_mask |= BIT(0);                      /* 第 0 路出问题了 */
		rid->states[0].notifs  = REGULATOR_EVENT_OVER_VOLTAGE_WARN;
		rid->states[0].errors  = REGULATOR_ERROR_OVER_VOLTAGE_WARN;
	}
	return 0;
}

static const struct regulator_irq_desc my_irq_desc = {
	.name      = "my-pmic-err",
	.map_event = my_map_event,     /* 必填 */
	.fatal_cnt = 0,                /* 见下 */
	.data      = NULL,             /* 会被拷进 rid->data，probe 里改成 priv */
};

	/* probe 里，在所有 rdev 注册完之后 */
	priv->irq_handle = devm_regulator_irq_helper(dev, &my_irq_desc,
			irq,
			IRQF_ONESHOT,                          /* irq_flags */
			REGULATOR_ERROR_OVER_VOLTAGE_WARN,     /* common_errs */
			NULL,                                  /* per_rdev_errs */
			priv->rdevs, priv->num_rdevs);
	if (IS_ERR(priv->irq_handle))
		return PTR_ERR(priv->irq_handle);
```

完整签名在 `include/linux/regulator/driver.h`：
`devm_regulator_irq_helper(dev, d, irq, irq_flags, common_errs,
per_rdev_errs, rdev, rdev_amount)`。
**`irq_flags` 是这个函数的参数，不是 `regulator_irq_desc` 的字段** ——
`struct regulator_irq_desc` 里没有 `irq_flags`，
它只有 `name` / `fatal_cnt` / `reread_ms` / `irq_off_ms` /
`skip_off` / `high_prio` / `data` 加三个回调。

**`fatal_cnt` 非 0 意味着"重试超过这个次数就关机"**
（`irq_helpers.c:65-77` 的 `hw_protection_shutdown`）。
不确定就填 0 —— 0 表示禁用这个机制。

三个可选回调的分工：

| 回调 | 何时调 | 不填的后果 |
|---|---|---|
| `map_event` | 每次中断 | 必填，不填等于没实现 |
| `renable` | 重新开中断前，判断故障是否还在 | 不填 ⇒ 直接重开中断，靠中断再次触发 |
| `die` | 重试超 `fatal_cnt` 时的最后一招 | 不填 ⇒ 直接 `hw_protection_shutdown`（`irq_helpers.c:66-67`） |

硬件简单（一个状态位）的话可以直接用现成的
`regulator_irq_map_event_simple`（`irq_helpers.c:418`）当 `map_event`，
适用条件写在它的 kerneldoc（`:405-416`）。

时序要求：**必须在 rdev 全部注册完之后调**，
因为参数里要传 rdev 数组。

---

## §4 coupler 作者

只有需要"多路电压必须保持一定关系"的 SoC 需要。
机制见 03 篇 §B4 和 04 篇 §4。

```c
static int my_attach(struct regulator_coupler *coupler,
		     struct regulator_dev *rdev)
{
	if (!is_my_soc_regulator(rdev))
		return 1;                 /* 1 = 不是我管的，试下一个 coupler */
	return 0;                         /* 0 = 我接了 */
}

static int my_balance(struct regulator_coupler *coupler,
		      struct regulator_dev *rdev,
		      suspend_state_t state)
{
	/* 在这里实现"CPU 电压不能低于 SoC 电压 - 100mV"这类策略，
	 * 用 regulator_get_voltage_rdev / regulator_set_voltage_rdev
	 * 逐个调整耦合组成员。
	 */
	return 0;
}

static struct regulator_coupler my_coupler = {
	.attach_regulator = my_attach,
	.balance_voltage  = my_balance,     /* 3 路以上必填 */
};

static int __init my_coupler_init(void)
{
	return regulator_coupler_register(&my_coupler);
}
arch_initcall(my_coupler_init);         /* 必须早于 PMIC 驱动 */
```

四条硬要求：

1. **`attach_regulator` 的返回值是三态**：`0` = 我管、
   `1` = 不是我的（继续试下一个）、`< 0` = 出错。
   写成"不是我的就返回 -EINVAL"会让整个注册失败（`core.c:5329-5330`）。
2. **注册时机必须早于被耦合的 rdev**。
   `regulator_find_coupler` 只在 rdev 注册那一刻查一次（`core.c:5463`）。
   `arch_initcall` 或 `subsys_initcall` 是安全的，
   `module_init` 通常太晚。
3. **3 路以上耦合必须实现 `balance_voltage`**，
   否则 `regulator_find_coupler` 在 `core.c:5322-5324` 直接
   `-EPERM` + `Voltage balancing for multiple regulator couples is unimplemented`。
4. **你的 coupler 会先于通用 coupler 被试到**（倒序遍历，
   `core.c:5319`，注释 `:5314-5318`）——所以 `attach` 里的
   "不是我的"判断必须准，否则会抢掉本该走通用路径的 rdev。

`balance_voltage` 里可用的核心 API：
`regulator_get_voltage_rdev`（`core.c:4457`）和
`regulator_set_voltage_rdev`（`core.c:3820`）——
注意是 `_rdev` 后缀的**内部** API，
**不要**用消费者侧的 `regulator_set_voltage`（会重入取锁）。

---

## §5 常见错误速查

| 症状 | 最可能的原因 | 去哪查 |
|---|---|---|
| 消费者 probe 一直 `-EPROBE_DEFER` | 上游 PMIC 还没注册；或 `regulator-coupled-with` 没解析完 | 03 篇 §A2 / §B4.3 |
| 电源"打开成功"但设备没反应 | 拿到的是 dummy | §1.3，`dmesg \| grep dummy` |
| `regulator_set_voltage` 返回 `-EPERM` | DTS 没写 min/max ⇒ `valid_ops_mask` 缺 `CHANGE_VOLTAGE` | §2.2、06 篇 §1.1 |
| `regulator_set_voltage` 返回 0 但电压没变 | 落在约束区间内的"只记账"路径 | 03 篇 §A6 第 2 步 |
| 电压不是我要的值 | 同一路上另一个消费者的区间交集 | §1.5、03 篇 §B3 |
| `regulator_enable` 返回 `-EPERM` | `valid_ops_mask` 缺 `CHANGE_STATUS` | 03 篇 §B2 |
| `regulator_disable` 返回 0 但电源还开着 | 正常：语义是"我不再需要它"，不是"关掉它" | 03 篇 §A5.3 |
| 开机 30 秒后一批 `disabling` 日志 + 设备挂掉 | `regulator_late_cleanup` 关掉了没人认领的路 | 03 篇 §A7 |
| DTS 里的约束好像被忽略了 | `of_match` 或 `regulators_node` 写错 | §2.2、06 篇 §3 |
| 注册时 kernel panic（`BUG_ON`） | 填了 `map_voltage_linear_range` 忘了 `linear_ranges` | 04 篇 §1.2 |
| rmmod PMIC 驱动后系统炸 | `open_count` 非 0 时 `regulator_unregister` 只 WARN 不拦 | 03 篇 §A8 |

## §6 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 本篇用到的每个 API 的完整签名和错误码 | `01-api.md` |
| `regulator_desc` / `regulator_config` 的字段全貌 | `02-datastructures.md` §4.2 |
| 核心内部为什么这么要求 | `03-internals.md` Part A / Part B |
| helpers.c 那 28 个函数的完整清单 | `04-extensions.md` §1 |
| DTS 每条属性怎么写、写错怎么查 | `06-devicetree.md` |
| 上面每条症状怎么用日志/sysfs/tracepoint 定位 | `07-debug.md` |
| 「dummy 为什么骗过了我」之类的直觉纠偏 | `08-feynman.md` |
| 本篇每条论断的出处，以及"未编译"的完整声明 | `99-evidence.md` |
