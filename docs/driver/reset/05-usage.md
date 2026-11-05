---
title: 使用指南
---

# reset 05 —— 各角色的代码骨架

## ⚠️ 先读这三条声明

1. **本篇所有代码骨架都没有编译过、没有运行过。** 它们是从 in-tree 驱动提炼的形状，
   用来说明"顺序和配对关系"，不是可以直接粘贴进项目的代码。
   缺少的头文件、错误分支、`goto` 标签请自己补。这条免责声明**不只写在 `99-evidence.md`**，
   因为本篇是最容易被直接抄走的一篇。
2. **每个骨架下面都点名了一个 in-tree 原型**，带文件和行号。
   骨架和原型有出入时**以原型为准**——原型是编译过、跑过的。
3. arm64-only。举例优先取 arm64 平台上真实存在的驱动（Tegra / Amlogic / Qualcomm）。

## §1 这个子系统有哪些角色

| 角色 | 它要做的事 | 本篇章节 | in-tree 原型 |
|---|---|---|---|
| **消费者 · 独占** | 我这个外设有一条自己的复位线 | §2 | `drivers/gpu/drm/tegra/gr3d.c` |
| **消费者 · 共享** | 复位线是我和别人合用的，我不能随便按住 | §3 | `drivers/usb/dwc3/dwc3-meson-g12a.c` |
| **消费者 · 一次拿一组** | 我有好几条复位线，要一起断言/解除 | §4 | `drivers/gpu/drm/tegra/gr3d.c`、`drivers/ata/libahci_platform.c` |
| **消费者 · 轮流独占** | 这条线同一时刻只能一个驱动用，但要在多个驱动间倒手 | §5 | `drivers/gpu/drm/tegra/gr3d.c` |
| **控制器驱动 · MMIO** | 我这块硬件有一段复位寄存器 | §6 | `drivers/reset/reset-simple.c` |
| **控制器驱动 · syscon** | 复位位散在系统控制寄存器里 | §7 | `drivers/reset/reset-ti-syscon.c` |
| **双身份驱动** | 我是时钟驱动，顺手把复位也提供了 | §8 | `drivers/clk/` 下 33 个文件（`04-extensions.md` §6） |
| **非 DT 平台** | 我这平台没有设备树 | §9 | `drivers/reset/core.c` 的 lookup 路径 |

DTS 怎么写不在本篇（在 `06-devicetree.md`）；出问题怎么查不在本篇（在 `07-debug.md`）。

---

## §2 消费者 · 独占（exclusive）—— 最常见的形态

### 2.1 最小骨架

```c
#include <linux/reset.h>

struct my_priv {
	struct reset_control *rstc;
	/* ... */
};

static int my_probe(struct platform_device *pdev)
{
	struct my_priv *priv;
	int ret;

	priv = devm_kzalloc(&pdev->dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->rstc = devm_reset_control_get_exclusive(&pdev->dev, NULL);
	if (IS_ERR(priv->rstc))
		return dev_err_probe(&pdev->dev, PTR_ERR(priv->rstc),
				     "failed to get reset\n");

	/* 上电顺序：先解除复位，再动这块硬件的任何寄存器 */
	ret = reset_control_deassert(priv->rstc);
	if (ret)
		return ret;

	/* ... 初始化硬件 ... */
	return 0;
}
```

三个要点：

- **`NULL` 作为 `id`** 表示"这个设备只有一条复位线，不用名字区分"。
  DTS 里对应的是只写 `resets = <&rst N>;`、不写 `reset-names`。
  有多条线时传字符串，它必须在 `reset-names` 里出现（解析在 `03-internals.md` §A2）。
- **`devm_` 前缀管释放**，不用写 `reset_control_put()`；不带 `devm_` 的版本必须自己 put，
  四条释放路径见 `03-internals.md` §A6。
- **用 `dev_err_probe()` 而不是 `dev_err()`**：它会把 `-EPROBE_DEFER` 降级成 debug 级别，
  不然每次 defer 都刷一条错误日志。这是 2.2 的伏笔。

### 2.2 `-EPROBE_DEFER` 必须往上传 —— 连 `_optional_` 也不能吞

这是消费者侧最容易写错的一处，`01-api.md` §6 和 `03-internals.md` §B3 都点过，这里给出正确写法。

`_optional_` 变体的语义是"DTS 里没写这条复位线时返回 NULL 而不是报错"，
**它不代表"所有错误都可以忽略"**。核心在控制器还没注册时返回 `-EPROBE_DEFER`
（`drivers/reset/core.c:855`），这个错误码 `_optional_` 一样会原样返回。

```c
	priv->rstc = devm_reset_control_get_optional_exclusive(dev, NULL);
	if (IS_ERR(priv->rstc))
		return PTR_ERR(priv->rstc);	/* ← 必须 return，包含 -EPROBE_DEFER */

	/* 到这里 rstc 可能是 NULL：DTS 没写复位线。这是合法状态。 */
	ret = reset_control_deassert(priv->rstc);	/* NULL 传进去直接返回 0 */
	if (ret)
		return ret;
```

**错误写法**（真实项目里见得到）：

```c
	priv->rstc = devm_reset_control_get_optional_exclusive(dev, NULL);
	if (IS_ERR(priv->rstc))
		priv->rstc = NULL;	/* ✗ 把 -EPROBE_DEFER 也吞了 */
```

后果不是编译错误，而是**这块硬件在复位控制器驱动晚于自己 probe 的启动次序下随机不工作**——
因为复位从来没被解除过，而日志里一条错误都没有。

为什么 `NULL` 可以安全地传给动作函数：所有动作函数的入口都有
`if (!rstc) return 0;`（`03-internals.md` §A4 那张矩阵表的第一列），
这是 `_optional_` 能成立的全部机制。

### 2.3 remove / 关电时要不要 assert

**要，而且要和 probe 里的 deassert 严格配对。** 但注意两件事：

```c
static int my_remove(struct platform_device *pdev)
{
	struct my_priv *priv = platform_get_drvdata(pdev);

	/* 先停掉硬件访问，再按下复位 */
	my_hw_disable(priv);
	reset_control_assert(priv->rstc);
	/* devm_ 拿的句柄不用 put */
	return 0;
}
```

- **顺序是"先停访问、后 assert"**，反了就会在硬件被复位的同时还有寄存器读写在飞。
- **`devm_reset_control_get_*()` 不会在设备卸载时自动 assert**。devres 只负责
  `reset_control_put()`（释放句柄），**不改硬件状态**——释放时框架不做任何状态检查，
  这一点 `03-internals.md` §A6 有专门一张表。**所以"忘了 assert"是静默的。**

### 2.4 独占语义的三个硬边界

同一条复位线被第二个消费者 get 时，结果只由三行代码决定
（`drivers/reset/core.c:763-767`，完整函数在 `03-internals.md` §A3）：

| 第二个消费者写的 | 结果 | 依据 |
|---|---|---|
| 也是普通 `get_exclusive`（`acquired = true`） | **`WARN_ON` + `-EBUSY`**，拿不到 | `drivers/reset/core.c:766-767` |
| `get_exclusive_released`（`acquired = false`） | **成功**，拿到一个**独立的第二个句柄**（走 `break` 去新建） | `drivers/reset/core.c:763-764` |
| `get_shared`（对方是 exclusive） | `WARN_ON` + `-EBUSY` | 同 `:766-767` |

第一行是"exclusive 真的能挡住别人"的证据。第二行是唯一的例外，也是 §5 那套
acquire/release 机制的入口：**注意 `:763` 只看新请求的 `acquired`，
不看已有句柄的状态**——所以第二个句柄能创建成功，冲突被推迟到
`reset_control_acquire()` 才暴露（那里返回 `-EBUSY`，见 `03-internals.md` §A5）。

---

## §3 消费者 · 共享（shared）

### 3.1 骨架与原型

多个外设共用一条复位线时（典型：一个 USB PHY 复位位管两个控制器），
每个驱动都用 `shared`，框架用引用计数保证"最后一个撤下才真的断言"：

```c
	priv->rstc = devm_reset_control_get_shared(dev, NULL);
	if (IS_ERR(priv->rstc))
		return PTR_ERR(priv->rstc);

	/* 上电 */
	ret = reset_control_deassert(priv->rstc);	/* deassert_count 0→1 时才真调回调 */
	...
	/* 下电 */
	reset_control_assert(priv->rstc);		/* deassert_count 1→0 时才真调回调 */
```

in-tree 原型：`drivers/usb/dwc3/dwc3-meson-g12a.c:745`
（`devm_reset_control_get_shared(dev, NULL)`）。

计数是 `deassert_count`，加减位置在 `03-internals.md` §B2 那张三计数表里。

### 3.2 两条正交的轴：shared/exclusive 和 脉冲/电平

这里是整个 reset 框架**最容易搞混的一处**，而且很多人（包括我在写这一篇的初稿时）
会把两件独立的事混成一件：

| 轴 | 取值 | 由什么决定 | 影响哪些 API |
|---|---|---|---|
| **谁拥有这条线** | exclusive / shared | 你 get 时选的变体 | 是否引用计数、能不能被别人抢 |
| **硬件是什么形状** | 自解除脉冲 / 电平保持 | **硬件本身**，DTS 和绑定文档告诉你 | `reset()`+`rearm()` 一对 vs `deassert()`+`assert()` 一对 |

**两条轴互不决定。** 一条 shared 的电平复位线，正确用法就是
`deassert()` / `assert()`（`Documentation/driver-api/reset.rst:94-96` 明确写
"For shared reset controls, calls to the two functions must be balanced"）；
一条 exclusive 的脉冲复位线，正确用法就是 `reset()`。

`drivers/ata/libahci_platform.c` 把**第二条轴**写成了两段并列代码，
是全树最清楚的示范（注意它的 `if` 条件是硬件形状，不是 shared）：

```c
	/* 上电：ahci_platform_deassert_rsts() */
	if (hpriv->f_rsts & AHCI_PLATFORM_RST_TRIGGER)
		return reset_control_reset(hpriv->rsts);

	return reset_control_deassert(hpriv->rsts);
```

（`drivers/ata/libahci_platform.c:161-164`）

```c
	/* 下电：ahci_platform_assert_rsts() */
	if (hpriv->f_rsts & AHCI_PLATFORM_RST_TRIGGER)
		return reset_control_rearm(hpriv->rsts);

	return reset_control_assert(hpriv->rsts);
```

（`:180-183`）它的句柄是
`devm_reset_control_array_get_optional_shared(dev)`（`:536`）——
**一直是 shared，两条分支都是**。分支切换的是"这条线是脉冲还是电平"。
kernel-doc 也把这一点写在函数名解释里：
`:172-173` 说 "asserts or rearms (for self-deasserting resets)"。

### 3.3 真正的禁忌：同一个 shared 句柄上把两对 API 混着用

核心是在 `reset_control_assert()` 的 shared 分支里显式拦这件事的
（`drivers/reset/core.c:453-458`）：

```c
	if (rstc->shared) {
		if (WARN_ON(atomic_read(&rstc->triggered_count) != 0))
			return -EINVAL;

		if (WARN_ON(atomic_read(&rstc->deassert_count) == 0))
			return -EINVAL;
```

两条拦截各对应一种写错的方式：

| 你写的 | 触发哪一条 | 现象 |
|---|---|---|
| 先 `reset_control_reset()`，后 `reset_control_assert()` | `:454-455` | `WARN_ON` 栈回溯 + `-EINVAL` |
| `assert()` 比 `deassert()` 多一次（不平衡） | `:457-458` | 同上 |

所以规则是：**一个 shared 句柄，要么全程用 `reset()`/`rearm()`，要么全程用
`deassert()`/`assert()`，而且必须配平。** 两者混用会被 `WARN_ON` 抓住——
这也是这个框架少数会主动喊出来的错误之一（21 个 WARN 点的清单在 `03-internals.md` §C3）。

另外两个不对称，写代码时要知道：

- **shared 分支下 `.assert` 回调不存在时返回 0**（`drivers/reset/core.c:467-468`，
  注释写明"doing nothing is a valid option"），而 exclusive 分支返回 `-ENOTSUPP`
  （`:475-476`）。**同一条硬件、同一个控制器，换个 get 变体错误码就变了。**
- **`rearm()` 对 exclusive 句柄不动任何计数**：exclusive 分支只检查 `acquired`
  （`drivers/reset/core.c:417-419`），已 acquire 就直接返回 0，没 acquire 返回 `-EPERM`。
  ⚠️ 所以它不是"纯空操作"——在 `_released` 场景下照样会挡你。
  libahci 那种"一份代码伺候两种硬件"的写法之所以安全，是因为它的句柄一直是 shared
  （`drivers/ata/libahci_platform.c:536`），走的是 shared 分支。

`drivers/usb/dwc3/dwc3-meson-g12a.c` 是"shared + 脉冲"这一格的完整实例：
`devm_reset_control_get_shared()`（`:745`）、`reset_control_reset()`（`:752`、`:916`）、
`reset_control_rearm()`（`:832`、`:864`、`:906`，分别在 suspend / 出错回退 / remove）。
这一格为什么成立，`Documentation/driver-api/reset.rst:115-122` 有官方解释：
共享的脉冲复位线上**只有第一个触发真的发脉冲**，之后要等所有消费者都
`rearm()` 过才会再发一次。

这两条轴是 `08-feynman.md` §2 的主题。

---

## §4 消费者 · 一次拿一组（bulk 和 array）

### 4.0 先分清 bulk 和 array

判据只有一句：**回来几个句柄。** bulk 回来 N 个（你自己一个个用），
array 回来 1 个（对它做的动作会散到 N 条线上）。
结构上的区别在 `02-datastructures.md` §1.4。

| | `bulk` | `array` |
|---|---|---|
| 句柄数 | N | 1 |
| 你要准备的东西 | 一个 `struct reset_control_bulk_data[]`，**每项填好 `id`（名字）** | 什么都不用，函数自己数 DTS |
| 能不能只动其中一条 | 能（`rstcs[i].rstc`） | 不能 |
| 顺序 | assert 正序、**deassert 反序** | 全部正序 |
| 典型 API | `devm_reset_control_bulk_get_optional_exclusive_released()` | `devm_reset_control_array_get_optional_shared()` |

那张"顺序与回滚矩阵"（`03-internals.md` §B4）是选 bulk 还是 array 的唯一判据：
**硬件对上下电顺序敏感 ⇒ 必须用 bulk**，因为只有 bulk 保证 deassert 走反序。

### 4.1 bulk 骨架

```c
enum { RST_A, RST_B, RST_MAX };

struct my_priv {
	struct reset_control_bulk_data resets[RST_MAX];
	int nresets;
};

	/* 名字必须和 DTS 的 reset-names 对应 */
	priv->resets[RST_A].id = "a";
	priv->resets[RST_B].id = "b";
	priv->nresets = RST_MAX;

	ret = devm_reset_control_bulk_get_optional_exclusive_released(
			dev, priv->nresets, priv->resets);
	if (ret)
		return ret;

	/* optional：DTS 没写的那几条，rstc 会是 NULL，必须自己判断 */
	if (WARN_ON(!priv->resets[RST_A].rstc))
		return -ENODEV;
```

in-tree 原型：`drivers/gpu/drm/tegra/gr3d.c` —— 数组声明在 `:47`
（`struct reset_control_bulk_data resets[RST_GR3D_MAX]`），
四个 `id` 依次赋值在 `:473-476`，`nresets` 来自 SoC 数据（`:477`），
get 在 `:479`，**"optional 回来的 NULL 要自己查"的检查在 `:486-487`**（两个 `WARN_ON`）。

三条硬规则：

1. **这个数组不能是栈上的局部变量**，也不能在 probe 返回后失效。
   devres 只存了数组的**指针**（`drivers/reset/core.c:1058-1061` 那个
   `reset_control_bulk_devres`），设备卸载时框架会回来遍历它。
   放进 `priv` 里、或者用 `devm_kcalloc` 分配。这条在 `02-datastructures.md` §2.6 有推导。
2. **`id` 要在 get 之前填好**，它是查 `reset-names` 的键，不是输出。
3. **声明顺序要按"assert 的顺序"写**：bulk 的 deassert 是反序遍历
   （`drivers/reset/core.c:584-594`），所以数组正序 = 断言顺序 = 关电顺序，
   反序 = 解除顺序 = 上电顺序。搞反了不会报错，只会让某些 IP 上电时序错。

### 4.2 array 骨架

```c
	/* 一句话拿走 DTS 里所有 resets 项 */
	priv->rsts = devm_reset_control_array_get_optional_shared(dev);
	if (IS_ERR(priv->rsts))
		return PTR_ERR(priv->rsts);

	ret = reset_control_deassert(priv->rsts);	/* 一次动全部 */
```

in-tree 原型：`drivers/ata/libahci_platform.c:536`。

两个限制要记住：

- **`reset_control_status()` 对 array 句柄直接返回错误**——"一组线"没有单一状态可报
  （`03-internals.md` §C1.3）。要查状态只能用 bulk。
- 想知道 DTS 里到底有几条线，用 `reset_control_get_count(dev)`（`include/linux/reset.h:70`）；
  array 路径内部数 DTS 的函数是 static 的（`drivers/reset/core.c:1147`），消费者调不到。

---

## §5 消费者 · 轮流独占（`_released` + acquire/release）

### 5.0 什么时候需要它

场景：**一条复位线在硬件上只能有一个主人，但这个主人会换。**
Tegra 的 2D/3D 引擎就是这样——两个 DRM 子设备在不同时刻各自独占同一条 MC 复位线。

做法是所有参与者都用 `_released` 变体拿句柄（于是允许多个句柄共存，见 §2.4 第二行），
然后**在真正要用的时候 `acquire()`、用完 `release()`**。
`acquire()` 时发现别人还占着就返回 `-EBUSY`。

### 5.1 骨架：和 runtime PM 绑在一起

```c
static int my_runtime_resume(struct device *dev)
{
	struct my_priv *priv = dev_get_drvdata(dev);
	int ret;

	ret = reset_control_bulk_acquire(priv->nresets, priv->resets);
	if (ret)
		return ret;			/* 别人正占着 → -EBUSY */

	ret = clk_bulk_prepare_enable(priv->nclocks, priv->clocks);
	if (ret)
		goto release;

	ret = reset_control_bulk_deassert(priv->nresets, priv->resets);
	if (ret)
		goto disable_clk;

	return 0;

disable_clk:
	clk_bulk_disable_unprepare(priv->nclocks, priv->clocks);
release:
	reset_control_bulk_release(priv->nresets, priv->resets);
	return ret;
}

static int my_runtime_suspend(struct device *dev)
{
	struct my_priv *priv = dev_get_drvdata(dev);
	int ret;

	ret = reset_control_bulk_assert(priv->nresets, priv->resets);
	if (ret)
		return ret;

	clk_bulk_disable_unprepare(priv->nclocks, priv->clocks);
	reset_control_bulk_release(priv->nresets, priv->resets);

	return 0;
}
```

in-tree 原型：`drivers/gpu/drm/tegra/gr3d.c` —— resume 路径
`bulk_acquire`（`:600`）→ `clk_bulk_prepare_enable`（`:606`）→ `bulk_deassert`（`:612`）、
出错回退 `bulk_release`（`:623`）；suspend 路径
`bulk_assert`（`:575`）→ `clk_bulk_disable_unprepare`（`:589`）→ `bulk_release`（`:590`）。

**这个四步顺序是本节的全部内容，记住它**：

```
   上电： acquire → 开时钟 → deassert        （拿到所有权 → 供上时钟 → 放开复位）
   下电： assert  → 关时钟 → release         （按下复位 → 撤时钟 → 交还所有权）
```

两个"为什么"：

- **`acquire` 必须在 `deassert` 之前**：exclusive 分支里
  `if (!rstc->acquired)` 会直接 `WARN(1, ...)` + `-EPERM`
  （`drivers/reset/core.c:478-481`）。没 acquire 就 deassert = 一条栈回溯。
- **`release` 必须在 `assert` 之后**：`release` 不做任何状态检查、也不加锁
  （`02-datastructures.md` §6.4 有两个函数体的对照），
  它**不会**帮你把复位按下去。先 release 再 assert 就变成"对着已经交还的句柄操作"。

### 5.2 一个 probe 时的探测小技巧

`drivers/gpu/drm/tegra/gr3d.c:354-366` 有一段值得抄的写法：用**非 devm** 的
`reset_control_get_exclusive_released()`（`:354`）拿一次，
`reset_control_acquire()`（`:358`）试着占一下，
无论成败都 `reset_control_release()`（`:363`）+ `reset_control_put()`（`:366`）还回去。
这是在 probe 阶段**探测"这条线现在有没有人占"**而不留下任何句柄的办法。

⚠️ 注意它用的是非 devm 版本，所以 `put` 必须自己写——这正好是 §2.1 第二个要点的反例。

---

## §6 控制器驱动 · MMIO 形态

### 6.1 骨架

```c
#include <linux/reset-controller.h>

struct my_rc {
	struct reset_controller_dev rcdev;
	void __iomem *base;
	spinlock_t lock;
};

static struct my_rc *to_my_rc(struct reset_controller_dev *rcdev)
{
	return container_of(rcdev, struct my_rc, rcdev);
}

static int my_rc_assert(struct reset_controller_dev *rcdev, unsigned long id)
{
	struct my_rc *rc = to_my_rc(rcdev);
	unsigned long flags;
	u32 val;

	spin_lock_irqsave(&rc->lock, flags);
	val = readl(rc->base + (id / 32) * 4);
	val |= BIT(id % 32);
	writel(val, rc->base + (id / 32) * 4);
	spin_unlock_irqrestore(&rc->lock, flags);

	return 0;
}
/* deassert / status 同形，reset 若硬件不支持就干脆不填 */

static const struct reset_control_ops my_rc_ops = {
	.assert   = my_rc_assert,
	.deassert = my_rc_deassert,
	.status   = my_rc_status,
};

static int my_rc_probe(struct platform_device *pdev)
{
	struct my_rc *rc;
	struct resource *res;

	rc = devm_kzalloc(&pdev->dev, sizeof(*rc), GFP_KERNEL);
	if (!rc)
		return -ENOMEM;

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	rc->base = devm_ioremap_resource(&pdev->dev, res);
	if (IS_ERR(rc->base))
		return PTR_ERR(rc->base);

	spin_lock_init(&rc->lock);
	rc->rcdev.owner = THIS_MODULE;			/* 别漏 */
	rc->rcdev.ops = &my_rc_ops;
	rc->rcdev.of_node = pdev->dev.of_node;		/* 别漏 */
	rc->rcdev.nr_resets = resource_size(res) * BITS_PER_BYTE;

	return devm_reset_controller_register(&pdev->dev, &rc->rcdev);
}
```

in-tree 原型：`drivers/reset/reset-simple.c`（逐步对照见 `04-extensions.md` §1.4）。
**如果你的硬件真的就是这个形状，不要自己写——直接内嵌
`struct reset_simple_data` 并用导出的 `reset_simple_ops`**（`04-extensions.md` §1.3）。

### 6.2 控制器侧五条容易漏的

| 漏了什么 | 后果 | 依据 |
|---|---|---|
| `rcdev.of_node` | DT 查找永远匹配不上，消费者一直 `-EPROBE_DEFER` | 匹配靠指针相等（`03-internals.md` §A2） |
| `rcdev.owner = THIS_MODULE` | 有消费者持句柄时模块仍可被卸载 | `try_module_get` 是唯一防线（`03-internals.md` §A3） |
| `nr_resets` | 越界的 DTS id 不再被拦（前提是你没写 `of_xlate`） | `drivers/reset/core.c:90` |
| 自定义 `of_xlate` 却忘了 `of_reset_n_cells` | 每次 get 都 WARN + `-EINVAL` | 两个字段必须一起填（`02-datastructures.md` §2.3） |
| 在 `of_xlate` 里调用任何 `reset_control_*()` | **死锁**——`of_xlate` 是在 `reset_list_mutex` 里被调用的 | `03-internals.md` §B1.4 |

补一句：**`of_reset_simple_xlate()` 是 `static` 的**（`drivers/reset/core.c:87`），
你在驱动里写不出它的名字。要用默认行为就**别填 `of_xlate`**，核心自己会补上
（`drivers/reset/core.c:102-105`）。

### 6.3 回调可以睡眠，但要想清楚

四个动作回调都是在**放开 `reset_list_mutex` 之后**被调用的
（`03-internals.md` §B1.4 列了四个调用点），所以回调里可以睡眠——
基于 SCMI / I2C / 邮箱的控制器必然要睡。

⚠️ 反过来的约束落在**消费者**头上：**核心里没有 `might_sleep()`，也没有任何检查。**
在中断上下文里调 `reset_control_assert()`，控制器是 MMIO 的就没事，
换个平台变成 SCMI 就是"在原子上下文里睡眠"。**这个约束框架不帮你检查，只能靠人。**

---

## §7 控制器驱动 · syscon / regmap 形态

和 §6 只有两处不同：**寄存器句柄从哪来**，以及**回调用什么函数写寄存器**。

```c
struct my_syscon_rc {
	struct reset_controller_dev rcdev;
	struct regmap *regmap;
};

static int my_syscon_assert(struct reset_controller_dev *rcdev, unsigned long id)
{
	struct my_syscon_rc *rc = to_my_syscon_rc(rcdev);

	if (id >= rcdev->nr_resets)		/* 自己查上界 */
		return -EINVAL;

	/* regmap 自带并发保护，这里不需要 spinlock */
	return regmap_write_bits(rc->regmap, MY_OFFSET(id), MY_MASK(id), MY_MASK(id));
}

static int my_syscon_probe(struct platform_device *pdev)
{
	struct device_node *np = pdev->dev.of_node;
	struct my_syscon_rc *rc;

	/* ... 分配 rc ... */

	rc->regmap = syscon_regmap_lookup_by_phandle(np, "my,rst-syscon");
	if (IS_ERR(rc->regmap))
		return PTR_ERR(rc->regmap);

	rc->rcdev.owner = THIS_MODULE;
	rc->rcdev.ops = &my_syscon_ops;
	rc->rcdev.of_node = np;
	rc->rcdev.nr_resets = MY_NR_RESETS;

	return devm_reset_controller_register(&pdev->dev, &rc->rcdev);
}
```

in-tree 原型两个，形态不同：

- `drivers/reset/reset-ti-syscon.c` —— 寄存器布局**全部写在 DTS 里**
  （一条线 7 个数字，`:183` 那个 `/ 7`），regmap 从 MFD 父设备拿；
  三个回调都自己查 `id >= data->nr_controls`（`:73-74`）。
- `drivers/reset/hisilicon/reset-hi3660.c` —— regmap 靠
  `syscon_regmap_lookup_by_phandle(np, "hisilicon,rst-syscon")`（`:86`），
  自带 `of_xlate` 把 `<offset bit>` 打包成一个 id（`:65-74`）。
  ⚠️ 它是**反面教材**：既没有 `nr_resets` 也没有任何上界检查（`04-extensions.md` §2.3）。

两条要点：

1. **不要自己 `ioremap` 一段已经属于 syscon 的地址**——那块寄存器还被时钟、pinmux 等瓜分，
   两个驱动各自映射会互相踩。这正是 syscon 存在的理由。
2. **用了 regmap 就不要再加自旋锁**：`regmap_write_bits()` 内部已经有锁。

---

## §8 双身份：一个驱动既提供时钟又提供复位

```c
static int my_cru_probe(struct platform_device *pdev)
{
	/* ... 映射寄存器、注册一堆时钟 ... */
	ret = devm_clk_hw_register(dev, &my_clk->hw);
	if (ret)
		return ret;

	/* 同一块寄存器的另一半：复位 */
	rc->rcdev.owner = THIS_MODULE;
	rc->rcdev.ops = &my_cru_reset_ops;
	rc->rcdev.of_node = dev->of_node;	/* 和时钟共用同一个 of_node */
	rc->rcdev.nr_resets = MY_NR_RESETS;

	return devm_reset_controller_register(dev, &rc->rcdev);
}
```

要点只有一条，但很关键：**两次注册共用同一个 `of_node`，两个框架各自建立索引，
互不知道对方存在**（`04-extensions.md` §6 有图）。DTS 里同一个 phandle
既出现在别人的 `clocks` 也出现在别人的 `resets`，靠的就是这一点。

如果复位部分刚好是"一位一条线"，可以直接内嵌 `struct reset_simple_data`
并借用导出的 `reset_simple_ops`——`drivers/clk/bcm/clk-bcm2711-dvp.c` 就是这么做的
（`04-extensions.md` §1.3 那张表的第 5 行）。

---

## §9 非 DT 平台：lookup 表

没有设备树的平台（老的 DaVinci、部分 MIPS）用一张静态表把
"provider 设备名 + index" 映射到 "消费者设备名 + 连接名"：

```c
static struct reset_control_lookup my_reset_lookup[] = {
	RESET_LOOKUP("my-psc0", 15, "my-rproc.0", NULL),
};

	/* 在 provider 注册之后调用 */
	reset_controller_add_lookup(my_reset_lookup, ARRAY_SIZE(my_reset_lookup));
```

`RESET_LOOKUP` 宏在 `include/linux/reset-controller.h:46-52`，
四个字段的含义在同文件 `:30-44` 的 kernel-doc 里。

**全树只有一个使用者**：`drivers/clk/davinci/psc-da850.c:70`
（表项）+ `:75`（`reset_controller_add_lookup` 调用）。它是 arm32 DaVinci，
而且——又一次——**是个时钟驱动**。

结论：**arm64 上你不会用到这条路径**。它在文档里的价值是解释
`03-internals.md` §A2.3 那条分支为什么存在，以及为什么
`rcdev->dev == NULL` 的控制器无法被 lookup 表引用（`02-datastructures.md` §1.2）。

---

## §10 易错清单

### 10.1 名字很像的成对符号（抄代码时最容易选错）

| 你可能想写的 | 实际存在的 | 区别 |
|---|---|---|
| `reset_control_get()` | 存在，但**是过时别名** | 新代码要显式选 `_exclusive` 或 `_shared` |
| `reset_control_array_get_*()` | 存在 | **一个句柄**管一组线 |
| `reset_control_bulk_get_*()` | 存在 | **N 个句柄**，要自己准备数组和 `id` |
| `reset_control_array_assert()` | ✗ **不存在于对外 API** | 那是 core 内部的 static 函数（`03-internals.md` §A4.3 那六个静态函数） |
| `reset_control_bulk_get_optional_exclusive_released()` | ✓ 存在（`include/linux/reset.h:316`） | 长名字里 `optional` 在 `get` 之后 |
| `reset_control_bulk_optional_get_exclusive_released()` | ✗ **不存在** | 见 10.2 |

### 10.2 一个 kernel-doc 里的错名字（本版本实测）

`include/linux/reset.h` 的 kernel-doc 里出现了
`reset_control_bulk_optional_get_exclusive_released()` 这个名字**三次**
（`:646`、`:651`、`:655`，都在 `devm_reset_control_bulk_get_optional_exclusive_released` 的注释块里），
而全树**没有任何函数叫这个名字**——真名是
`reset_control_bulk_get_optional_exclusive_released()`（声明在 `:302`、`:316`）。

按文档纪律这条记进 `99-evidence.md` §3.3：**照注释抄名字会编译不过**，
以声明为准。这也是"名字很像 = 高危信号"的一个现成例子。

### 10.3 十条最容易踩的

| # | 坑 | 正确做法 | 依据 |
|---|---|---|---|
| 1 | 把 `_optional_` 的 `-EPROBE_DEFER` 吞掉 | 一律 `return PTR_ERR()` | §2.2 |
| 2 | 以为 `devm_` 会在卸载时帮你 assert | remove 里自己 assert | §2.3 |
| 3 | 以为 `get_exclusive` 拿两次没事 | 第二次 `-EBUSY`；要轮换用 `_released` | §2.4 |
| 4 | shared 句柄上混用 `reset()` 和 `assert()` | 二选一并配平 | §3.3 |
| 5 | 把 shared/exclusive 和 脉冲/电平 当一回事 | 两条正交轴 | §3.2 |
| 6 | bulk 的数组放在栈上 | 放 `priv` 或 `devm_kcalloc` | §4.1 |
| 7 | bulk 数组顺序按"上电顺序"写 | 按**断言顺序**写（deassert 是反序） | §4.1 |
| 8 | 对 array 句柄调 `status()` | 用 bulk | §4.2 |
| 9 | 先 `deassert()` 后 `acquire()` | `acquire` → 开时钟 → `deassert` | §5.1 |
| 10 | 控制器驱动漏填 `of_node` / `owner` | 五条清单逐个过 | §6.2 |

---

## §11 接下来看哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| DTS 怎么写、`#reset-cells` 填几、`reset-names` 怎么对应 | `06-devicetree.md` |
| 上面这些函数返回错误码了怎么查 | `07-debug.md` §5 |
| 为什么 shared 和 exclusive 是这样设计的 | `08-feynman.md` §2 |
| acquire/release 到底解决什么问题 | `08-feynman.md` §3 |
| 这些 API 的完整清单 | `01-api.md` |
| 核心里逐行怎么走 | `03-internals.md` |
