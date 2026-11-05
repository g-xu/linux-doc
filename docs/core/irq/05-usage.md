---
title: 使用指南
---

# irq 05：各角色的代码骨架

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ **篇首免责声明**：本篇所有代码骨架**都没有编译过、没有运行过**。
> 它们是从本树真实驱动里提炼的结构，**不是可以直接 `make` 的代码**。
> 抄之前请对照 §0.2 点名的那几个真实驱动。
>
> ⚠️ 本篇出现的每个内核符号都已在 6.1.158 树上 grep 确认存在，
> 但**参数个数和类型请以头文件为准**（`include/linux/interrupt.h` / `irq.h` / `irqdomain.h`）。
> ⚠️ 本树的 `include/linux/interrupt.h` 有一处 PREEMPT_RT 本地补丁（01 篇篇首、99 §1.1）。

---

## §0 本子系统有哪些角色

### §0.1 角色清单

| § | 角色 | 你要写的东西 | 主要头文件 |
|---|---|---|---|
| §1 | **中断消费者**（写设备驱动的人） | `request_irq()` + 一个 handler | `include/linux/interrupt.h` |
| §2 | **一级中断控制器** | `IRQCHIP_DECLARE` + `irq_chip` + 层级 `irq_domain_ops` | `include/linux/irq.h` + `irqchip.h` + `irqdomain.h` |
| §3 | **二级 / 级联控制器** | 上面那些 + `irq_set_chained_handler_and_data()` | 同上 + `include/linux/irqchip/chained_irq.h` |
| §4 | **慢总线上的控制器**（I2C/SPI 扩展器） | 上面那些 + `irq_bus_lock` / `irq_bus_sync_unlock` | 同上 |
| §5 | **层级域的中间层**（MSI / 中断复用器） | `irq_domain_create_hierarchy()` + `.alloc`/`.free`/`.activate` | `include/linux/irqdomain.h` |
| §6 | **用户态运维 / 调试者** | shell 命令 | —— |

注意 **没有"中断设备驱动"这个角色** —— 中断不是一条总线上的设备。
消费者（§1）只是"申请一个号码然后挂个函数"。

### §0.2 每个骨架的真实原型（抄之前先读这几个文件）

| 骨架 | 参考的真实驱动 | 行数 | 为什么选它 |
|---|---|---|---|
| §1 消费者 | 任意 platform 驱动 | —— | 太常见，不点名 |
| §2 一级控制器 | `drivers/irqchip/irq-gic-v3.c` | 2584 | arm64 主力，但很大；层级域部分看 `gic_irq_domain_ops` |
| §2 最小完整范例 | `kernel/irq/irq_sim.c` | 250 | **无硬件依赖的最小 `irq_chip` + `irq_domain`**（04 §7） |
| §3 级联控制器 | `drivers/irqchip/irq-al-fic.c` | 287 | **arm64；generic-chip + 线性域 + 级联，三样齐全** |
| §4 慢总线 | `drivers/gpio/gpio-pca953x.c` | —— | I2C GPIO 扩展器，`irq_bus_lock` 的教科书用法 |
| §5 层级域 | `drivers/irqchip/irq-gic-v3-its.c` | 5608 | 太大；本套只讲契约（99 §2.2） |

### §0.3 需要打开的 Kconfig

irq 的 **30** 个 Kconfig 项**全部由架构或驱动 `select`，用户不直接选**（01 §0.4）。
你写驱动时要在自己的 Kconfig 里 `select`：

| 你在写 | 要 `select` |
|---|---|
| 用 generic-chip 的控制器 | `GENERIC_IRQ_CHIP` |
| 层级域 | `IRQ_DOMAIN_HIERARCHY`（它自动 `select IRQ_DOMAIN`） |
| 普通域 | `IRQ_DOMAIN` |
| MSI 域 | `GENERIC_MSI_IRQ_DOMAIN` |
| 需要软件重发 / 故障注入 | `HARDIRQS_SW_RESEND`、`GENERIC_IRQ_INJECTION` |

调试期建议额外打开：`GENERIC_IRQ_DEBUGFS`（04 §6.1，**默认不开**）。

---

## §1 中断消费者：最常见的那 20 行

### §1.1 骨架（platform 驱动）

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/platform_device.h>

struct my_dev {
	struct device	*dev;
	void __iomem	*base;
	int		irq;
	spinlock_t	lock;
};

/* ---- 硬中断 handler：不能睡，越短越好 ---- */
static irqreturn_t my_isr(int irq, void *dev_id)
{
	struct my_dev *priv = dev_id;		/* 就是 request_irq 的最后一个参数 */
	u32 status;

	status = readl(priv->base + STATUS_REG);
	if (!status)
		return IRQ_NONE;		/* 共享中断时必须正确返回它 */

	writel(status, priv->base + STATUS_REG);	/* 清中断源 */

	/* 活多就交给线程 */
	return IRQ_WAKE_THREAD;
}

/* ---- 线程 handler：可以睡 ---- */
static irqreturn_t my_isr_thread(int irq, void *dev_id)
{
	struct my_dev *priv = dev_id;

	/* … 慢活：I2C 传输、mutex、kmalloc(GFP_KERNEL) 都可以 … */
	return IRQ_HANDLED;
}

static int my_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct my_dev *priv;
	int ret;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	priv->dev = dev;
	spin_lock_init(&priv->lock);

	priv->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(priv->base))
		return PTR_ERR(priv->base);

	/* 首选 platform_get_irq：失败时它已经打过日志了 */
	priv->irq = platform_get_irq(pdev, 0);
	if (priv->irq < 0)
		return priv->irq;		/* 可能是 -EPROBE_DEFER，直接透传 */

	/* 在申请中断之前，硬件必须已经处于"不会乱发中断"的状态 */
	writel(0, priv->base + MASK_REG);
	writel(~0, priv->base + STATUS_REG);

	ret = devm_request_threaded_irq(dev, priv->irq,
					my_isr, my_isr_thread,
					IRQF_ONESHOT,	/* 见 §1.3 */
					dev_name(dev), priv);
	if (ret)
		return dev_err_probe(dev, ret, "failed to request irq %d\n",
				     priv->irq);

	/* 到这里中断已经使能了，硬件可以开始工作 */
	writel(ENABLE_ALL, priv->base + MASK_REG);

	return 0;
}
```

### §1.2 三条顺序纪律

| 纪律 | 为什么 |
|---|---|
| **先让硬件安静，再 `request_irq()`** | `request_irq()` 返回时中断**已经使能**了；如果硬件此刻有未清的中断源，handler 会在你 probe 还没做完时被调用 |
| **`request_irq()` 之后才使能硬件的中断输出** | 反序会丢中断（或者更糟，中断来了但 handler 还没注册 ⇒ `nobody cared`，04 §4） |
| **`free_irq()` 之前先关掉硬件的中断输出** | 否则拆除过程中来一次中断，handler 已经摘掉了 |

用 `devm_request_threaded_irq()` 时第三条由 devres 顺序保证 ——
但**前提是 `devm_ioremap` 在 `devm_request_irq` 之前**（释放是逆序）。

### §1.3 `IRQF_ONESHOT` 什么时候必须设

| 情况 | 必须设吗 |
|---|---|
| 只有 `handler`，没有 `thread_fn` | 不需要 |
| 有 `thread_fn` 且 `handler` 传 **NULL** | **必须** —— 否则 `request_threaded_irq()` 返回 `-EINVAL` |
| 有 `thread_fn` 且 `handler` 自己写了 | **强烈建议设** —— 电平触发的硬件不设会中断风暴（03 §B6） |
| 边沿触发 + 自己在 handler 里清了中断源 | 可以不设 |

判据：**你的 handler 返回 `IRQ_WAKE_THREAD` 之后，硬件的中断线还会不会继续拉着？**
会 ⇒ 必须 `IRQF_ONESHOT`。

### §1.4 handler 里能做什么、不能做什么

| 能 | 不能 |
|---|---|
| `readl` / `writel` | 任何会睡的操作（`mutex_lock` / `kmalloc(GFP_KERNEL)` / `msleep`） |
| `spin_lock()`（注意：**不要用 `spin_lock_irqsave`**，中断已经关了） | `local_irq_enable()`（核心会 `WARN_ONCE` 并替你关回去，03 §A6） |
| `wake_up()` / `complete()` | `disable_irq()`（会睡；用 `disable_irq_nosync()`） |
| `schedule_work()` / `queue_work()` | `free_irq()`（会 `WARN`，03 §A8） |
| `IRQ_WAKE_THREAD` 转交 | `synchronize_irq()` |

⚠️ **`WARN_ONCE(!irqs_disabled(), "irq %u handler %pS enabled interrupts")`
（`kernel/irq/handle.c:161`）是最常见的 handler bug 告警** ——
一般是配错了锁：在 handler 里用 `spin_unlock_irq()`（它会**无条件开中断**）
而不是 `spin_unlock()`。

### §1.5 多中断设备

```c
	/* 首选按名字取，比索引稳 —— DT 里写 interrupt-names */
	irq_tx = platform_get_irq_byname(pdev, "tx");
	irq_rx = platform_get_irq_byname(pdev, "rx");
	if (irq_tx < 0 || irq_rx < 0)
		return -EINVAL;

	ret = devm_request_irq(dev, irq_tx, my_tx_isr, 0, "myctl-tx", priv);
	if (ret)
		return ret;
	ret = devm_request_irq(dev, irq_rx, my_rx_isr, 0, "myctl-rx", priv);
```

**`devname` 会出现在 `/proc/interrupts` 的最后一列**（04 §5.1）——
多中断设备一定要给不同的名字，否则排查时分不清。

### §1.6 唤醒源

```c
	device_init_wakeup(dev, true);
	/* … */
static int my_suspend(struct device *dev)
{
	struct my_dev *priv = dev_get_drvdata(dev);

	if (device_may_wakeup(dev))
		enable_irq_wake(priv->irq);	/* 计数式，要配平 */
	return 0;
}
static int my_resume(struct device *dev)
{
	struct my_dev *priv = dev_get_drvdata(dev);

	if (device_may_wakeup(dev))
		disable_irq_wake(priv->irq);
	return 0;
}
```

⚠️ **`enable_irq_wake()` 返回 `-ENOSYS` 的处理**：
说明这条中断的 chip（或层级里某一层）没实现 `irq_set_wake`（01 §10.2）。
两种可能：① 硬件真的不支持唤醒；② chip 作者该设 `IRQCHIP_SKIP_SET_WAKE`
（02 §6.4）却忘了。⇒ 遇到时先查 `/sys/kernel/debug/irq/irqs/<N>` 看层级（07 篇）。

**不要**同时用 `IRQF_NO_SUSPEND` 和 `enable_irq_wake()`（01 §2.4、04 §10.1）。

---

## §2 一级中断控制器

### §2.1 骨架（层级域版，GIC 风格）

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/irq.h>
#include <linux/irqchip.h>
#include <linux/irqdomain.h>
#include <linux/of.h>
#include <linux/of_address.h>

struct my_ic {
	void __iomem		*base;
	struct irq_domain	*domain;
	raw_spinlock_t		lock;		/* 保护寄存器读改写 */
};

static struct my_ic my_ic_data;

/* ================= irq_chip 回调 ================= */

static void my_ic_mask(struct irq_data *d)
{
	struct my_ic *ic = irq_data_get_irq_chip_data(d);
	u32 reg = MASK_REG + (d->hwirq / 32) * 4;

	raw_spin_lock(&ic->lock);
	writel(readl(ic->base + reg) | BIT(d->hwirq % 32), ic->base + reg);
	raw_spin_unlock(&ic->lock);
}

static void my_ic_unmask(struct irq_data *d)
{
	/* … 对称 … */
}

static void my_ic_eoi(struct irq_data *d)
{
	struct my_ic *ic = irq_data_get_irq_chip_data(d);

	writel(d->hwirq, ic->base + EOI_REG);
}

static int my_ic_set_type(struct irq_data *d, unsigned int type)
{
	if (type != IRQ_TYPE_LEVEL_HIGH && type != IRQ_TYPE_EDGE_RISING)
		return -EINVAL;
	/* … 写触发类型寄存器 … */
	return 0;
}

static const struct irq_chip my_ic_chip = {		/* 6.x 起是 const */
	.name			= "my-ic",
	.irq_mask		= my_ic_mask,
	.irq_unmask		= my_ic_unmask,
	.irq_eoi		= my_ic_eoi,		/* 配 handle_fasteoi_irq */
	.irq_set_type		= my_ic_set_type,
	.flags			= IRQCHIP_SET_TYPE_MASKED |
				  IRQCHIP_SKIP_SET_WAKE |
				  IRQCHIP_MASK_ON_SUSPEND,
};

/* ================= irq_domain_ops（层级版）================= */

static int my_ic_domain_translate(struct irq_domain *d,
				  struct irq_fwspec *fwspec,
				  unsigned long *hwirq, unsigned int *type)
{
	if (!is_of_node(fwspec->fwnode))
		return -EINVAL;
	if (fwspec->param_count != 2)		/* DT: <hwirq flags>，见 06 篇 */
		return -EINVAL;

	*hwirq = fwspec->param[0];
	*type  = fwspec->param[1] & IRQ_TYPE_SENSE_MASK;
	return 0;
}

static int my_ic_domain_alloc(struct irq_domain *domain, unsigned int virq,
			      unsigned int nr_irqs, void *arg)
{
	struct irq_fwspec *fwspec = arg;
	unsigned long hwirq;
	unsigned int type;
	int i, ret;

	ret = my_ic_domain_translate(domain, fwspec, &hwirq, &type);
	if (ret)
		return ret;

	for (i = 0; i < nr_irqs; i++) {
		/* 顶层的域用 set_info：同时设 chip 和流处理器 */
		irq_domain_set_info(domain, virq + i, hwirq + i,
				    &my_ic_chip, domain->host_data,
				    handle_fasteoi_irq, NULL, NULL);
		irqd_set_single_target(irq_get_irq_data(virq + i));
	}
	return 0;
}

static void my_ic_domain_free(struct irq_domain *domain, unsigned int virq,
			      unsigned int nr_irqs)
{
	irq_domain_free_irqs_top(domain, virq, nr_irqs);
}

static const struct irq_domain_ops my_ic_domain_ops = {
	.translate	= my_ic_domain_translate,	/* 不是 .xlate */
	.alloc		= my_ic_domain_alloc,
	.free		= my_ic_domain_free,
};

/* ================= 中断入口 ================= */

/* __exception_irq_entry 定义在 arch/arm64/include/asm/exception.h:17，
 * 需要 #include <asm/exception.h>（arm64 专有）。
 */
static void __exception_irq_entry my_ic_handle_irq(struct pt_regs *regs)
{
	struct my_ic *ic = &my_ic_data;
	u32 hwirq;

	while ((hwirq = readl(ic->base + ACK_REG)) != SPURIOUS) {
		/* 首选：直接用 (域, hwirq)，省一次查表 */
		generic_handle_domain_irq(ic->domain, hwirq);
	}
}

/* ================= 初始化 ================= */

static int __init my_ic_of_init(struct device_node *node,
				struct device_node *parent)
{
	struct my_ic *ic = &my_ic_data;

	/* 这里没有 struct device！不能用 devm_*（03 §A2） */
	ic->base = of_iomap(node, 0);
	if (!ic->base)
		return -ENOMEM;

	raw_spin_lock_init(&ic->lock);

	/* 硬件复位到"全屏蔽"状态 */
	writel(~0, ic->base + MASK_REG);

	ic->domain = irq_domain_create_hierarchy(NULL, 0, NR_MY_IRQS,
						 of_node_to_fwnode(node),
						 &my_ic_domain_ops, ic);
	if (!ic->domain) {
		iounmap(ic->base);
		return -ENOMEM;
	}

	set_handle_irq(my_ic_handle_irq);	/* 需要 GENERIC_IRQ_MULTI_HANDLER */

	return 0;
}
IRQCHIP_DECLARE(my_ic, "myvendor,my-ic", my_ic_of_init);
```

### §2.2 骨架里六个必须理解的点

| 点 | 说明 | 出处 |
|---|---|---|
| 没有 `struct device` | `of_irq_init()` 只传 `device_node` ⇒ 不能 `devm_*`、不能 `-EPROBE_DEFER` | 03 §A2 |
| `.translate` 而不是 `.xlate` | 层级域二选一 | 01 §5.2 |
| `irq_domain_set_info()` 而不是 `irq_domain_set_hwirq_and_chip()` | 前者**多设一个流处理器**，顶层的域才用它 | `kernel/irq/irqdomain.c:1409` |
| `handle_fasteoi_irq` 配 `irq_eoi` | 硬件自带优先级和 EOI 时用它 | 01 §4.3 |
| `IRQCHIP_SET_TYPE_MASKED` | 让核心在调 `irq_set_type` 前先 mask | 02 §6.4 |
| `IRQCHIP_SKIP_SET_WAKE` | 明确表态"不需要 `irq_set_wake` 但唤醒是支持的"，避免消费者拿到 `-ENOSYS` | 02 §6.4 · §1.6 |

**`irqd_set_single_target()`** 那一句（对应 `IRQD_SINGLE_TARGET`，02 §6.2）：
如果你的硬件一条中断只能路由到一个 CPU，**一定要设它**，
否则 `effective_affinity` 会误导用户（04 §5.2）。

### §2.3 ⚠️ 不用层级域的写法（非层级）

如果你的控制器**下面不会再挂一层**（不做 MSI、不做复用），可以用简单得多的
`.xlate` + `.map`：

```c
static int my_ic_domain_map(struct irq_domain *d, unsigned int virq,
			    irq_hw_number_t hw)
{
	irq_set_chip_and_handler(virq, &my_ic_chip, handle_level_irq);
	irq_set_chip_data(virq, d->host_data);
	return 0;
}

static const struct irq_domain_ops my_ic_domain_ops = {
	.map	= my_ic_domain_map,
	.xlate	= irq_domain_xlate_twocell,	/* 核心现成的：<hwirq flags> */
};

	/* 建域用 linear 而不是 hierarchy */
	ic->domain = irq_domain_add_linear(node, NR_MY_IRQS,
					   &my_ic_domain_ops, ic);
```

核心提供两个现成的 `xlate`（01 §5.2）：
`irq_domain_xlate_onecell`（DT 只写 hwirq）和
`irq_domain_xlate_twocell`（DT 写 `<hwirq flags>`）。
⇒ **绝大多数简单控制器不用自己写 `xlate`**。

⚠️ **非层级域上不能再挂 MSI 域** —— 06 篇提到的 ITS 之类的东西
需要父域是层级域。⇒ 如果不确定将来会不会加，**直接用层级域**。

---

## §3 二级 / 级联控制器

### §3.1 级联的三件事

| 事 | 怎么做 |
|---|---|
| ① 从父控制器拿一个 virq | `irq_of_parse_and_map(node, 0)` 或 `platform_get_irq()` |
| ② 建自己的域 | `irq_domain_add_linear()` |
| ③ 把父 virq 变成"级联入口" | `irq_set_chained_handler_and_data(parent_irq, my_handler, ic)` |

⚠️ 做了 ③ 之后，父 virq **不能再被 `request_irq()`**，
而且**已经被自动使能**（02 §1.0.B）。

### §3.2 骨架（对照 `drivers/irqchip/irq-al-fic.c`）

```c
#include <linux/irq.h>
#include <linux/irqchip.h>
#include <linux/irqchip/chained_irq.h>	/* chained_irq_enter/exit */
#include <linux/irqdomain.h>
#include <linux/of_address.h>
#include <linux/of_irq.h>

#define NR_MY_IRQS	32

struct my_sub {
	void __iomem		*base;
	struct irq_domain	*domain;
	unsigned int		parent_irq;
};

/* ================= 级联流处理器 ================= */
static void my_sub_handler(struct irq_desc *desc)
{
	struct my_sub *ic = irq_desc_get_handler_data(desc);
	struct irq_chip *parent_chip = irq_desc_get_chip(desc);
	unsigned long pending;
	int hwirq;

	/* 必须成对，它替你做父控制器的 ack/eoi 和 IRQD_IRQ_INPROGRESS 记账 */
	chained_irq_enter(parent_chip, desc);

	pending = readl_relaxed(ic->base + CAUSE_REG);
	pending &= ~readl_relaxed(ic->base + MASK_REG);

	for_each_set_bit(hwirq, &pending, NR_MY_IRQS)
		generic_handle_domain_irq(ic->domain, hwirq);

	chained_irq_exit(parent_chip, desc);
}

/* ================= 用 generic-chip 省掉 irq_chip ================= */
static int __init my_sub_of_init(struct device_node *node,
				 struct device_node *parent)
{
	struct my_sub *ic;
	struct irq_chip_generic *gc;
	int ret;

	ic = kzalloc(sizeof(*ic), GFP_KERNEL);	/* 没有 device，用不了 devm */
	if (!ic)
		return -ENOMEM;

	ic->base = of_iomap(node, 0);
	if (!ic->base) {
		ret = -ENOMEM;
		goto err_free;
	}

	/* 先关掉全部中断，再接上级联（顺序纪律，同 §1.2） */
	writel_relaxed(~0, ic->base + MASK_REG);
	writel_relaxed(~0, ic->base + CAUSE_REG);

	ic->parent_irq = irq_of_parse_and_map(node, 0);
	if (!ic->parent_irq) {		/* ⚠️ 返回 0 表示失败（01 §3.2） */
		ret = -EINVAL;
		goto err_unmap;
	}

	/* ops 用核心提供的 irq_generic_chip_ops，自己不用写 map/xlate */
	ic->domain = irq_domain_add_linear(node, NR_MY_IRQS,
					   &irq_generic_chip_ops, ic);
	if (!ic->domain) {
		ret = -ENOMEM;
		goto err_unmap;
	}

	ret = irq_alloc_domain_generic_chips(ic->domain, NR_MY_IRQS, 1,
					     "my-sub", handle_level_irq,
					     0, 0, 0);
	if (ret)
		goto err_domain;

	gc = irq_get_domain_generic_chip(ic->domain, 0);
	gc->reg_base = ic->base;
	gc->private  = ic;
	gc->chip_types->regs.mask	= MASK_REG;
	gc->chip_types->regs.ack	= CAUSE_REG;
	gc->chip_types->chip.irq_mask	= irq_gc_mask_set_bit;
	gc->chip_types->chip.irq_unmask	= irq_gc_mask_clr_bit;
	gc->chip_types->chip.irq_ack	= irq_gc_ack_clr_bit;

	/* 最后一步：接上级联。它会顺带使能父中断 */
	irq_set_chained_handler_and_data(ic->parent_irq, my_sub_handler, ic);

	return 0;

err_domain:
	irq_domain_remove(ic->domain);
err_unmap:
	iounmap(ic->base);
err_free:
	kfree(ic);
	return ret;
}
IRQCHIP_DECLARE(my_sub, "myvendor,my-sub-ic", my_sub_of_init);
```

### §3.3 真实原型的对照表

| 骨架里的元素 | `drivers/irqchip/irq-al-fic.c` 的位置 |
|---|---|
| 级联流处理器 | `drivers/irqchip/irq-al-fic.c:107` |
| `chained_irq_enter()` | `drivers/irqchip/irq-al-fic.c:116` |
| `generic_handle_domain_irq()` | `drivers/irqchip/irq-al-fic.c:122` |
| `chained_irq_exit()` | `drivers/irqchip/irq-al-fic.c:124` |
| `irq_domain_add_linear(…, &irq_generic_chip_ops, …)` | `drivers/irqchip/irq-al-fic.c:143-145` |
| `irq_alloc_domain_generic_chips()` | `drivers/irqchip/irq-al-fic.c:152-155` |
| 三个 `irq_gc_*` 回调 | `drivers/irqchip/irq-al-fic.c:166-168` |
| `irq_set_chained_handler_and_data()` | `drivers/irqchip/irq-al-fic.c:174` |
| `IRQCHIP_DECLARE` | `drivers/irqchip/irq-al-fic.c:287` |
| 运行期切换电平/边沿（`irq_setup_alt_chip` 的替代做法） | `drivers/irqchip/irq-al-fic.c:45` 的 `al_fic_set_trigger()` |
| `irq_gc_lock()` / `irq_gc_unlock()` | `drivers/irqchip/irq-al-fic.c:71` / `:102` |
| `irq_retrigger` 实现 | `drivers/irqchip/irq-al-fic.c:127` |

**`al_fic_set_trigger()`（`drivers/irqchip/irq-al-fic.c:45`）示范了一个技巧**：
它在 `irq_set_type` 里**直接改 `gc->chip_types->handler`**
（`drivers/irqchip/irq-al-fic.c:53` 那一带在 LEVEL 时选 `handle_level_irq`、
否则选 `handle_edge_irq`）。
⇒ 这是"一个控制器上电平和边沿共用同一组寄存器、只需换流处理器"时的做法，
比 `irq_setup_alt_chip()`（04 §1.4）更轻。

### §3.4 `chained_irq_enter/exit` 到底做了什么

`include/linux/irqchip/chained_irq.h:16` / `:32` 两个 inline 函数：

| 函数 | 动作 |
|---|---|
| `chained_irq_enter(chip, desc)` | 如果父 chip 有 `irq_eoi` 就先 EOI；否则 `irq_mask_ack` 或 `irq_mask`+`irq_ack` |
| `chained_irq_exit(chip, desc)` | 对称地 unmask（如果 enter 时 mask 了） |

⚠️ **不调它们的后果**：
- 父控制器是 fasteoi 型（GIC）⇒ **不 EOI，这条中断线永远不再触发**；
- 父控制器是 level 型 ⇒ **不 mask，中断风暴**。

它们是 inline 的，**没有独立的 `.c` 实现**，所以 grep 函数定义找不到 —— 只能看头文件。

### §3.5 ⚠️ 级联 vs 线程化：什么时候不该用级联

| 情况 | 用什么 |
|---|---|
| 子控制器的寄存器在 MMIO 上（读写不会睡） | **级联**（本节） |
| 子控制器在 I2C / SPI 上（读写会睡） | **不能用级联** —— 流处理器在硬中断上下文，不能睡。用 §4 的线程化方案 |

---

## §4 慢总线上的控制器（I2C / SPI 扩展器）

### §4.1 两个关键差异

| | MMIO 控制器（§3） | 慢总线控制器 |
|---|---|---|
| 父中断怎么处理 | `irq_set_chained_handler_and_data()`（硬中断上下文） | **`request_threaded_irq()`**（线程上下文） |
| `irq_chip` 的 mask/unmask | 直接 `writel` | **只改内存里的缓存**，真正的 I2C 传输推迟到 `irq_bus_sync_unlock` |
| 需要哪两个回调 | 不需要 | **`irq_bus_lock` + `irq_bus_sync_unlock`** |
| 流处理器 | `handle_level_irq` / `handle_edge_irq` | `handle_simple_irq`（硬件确认由线程做完了） |

### §4.2 骨架（对照 `drivers/gpio/gpio-pca953x.c`）

```c
struct my_exp {
	struct i2c_client	*client;
	struct mutex		irq_lock;	/* 保护下面两个缓存 */
	u32			irq_mask;	/* 当前想要的屏蔽状态 */
	u32			irq_mask_cache;	/* 已经写进硬件的状态 */
	struct irq_domain	*domain;
};

/* ---- mask/unmask 只改内存 ---- */
static void my_exp_irq_mask(struct irq_data *d)
{
	struct my_exp *exp = irq_data_get_irq_chip_data(d);

	exp->irq_mask |= BIT(d->hwirq);		/* 不做 I2C 传输 */
}

static void my_exp_irq_unmask(struct irq_data *d)
{
	struct my_exp *exp = irq_data_get_irq_chip_data(d);

	exp->irq_mask &= ~BIT(d->hwirq);
}

/* ---- 核心在一串 chip 操作之前调这个 ---- */
static void my_exp_irq_bus_lock(struct irq_data *d)
{
	struct my_exp *exp = irq_data_get_irq_chip_data(d);

	mutex_lock(&exp->irq_lock);		/* 允许睡，因为不在硬中断上下文 */
}

/* ---- 之后调这个，一次性刷出去 ---- */
static void my_exp_irq_bus_sync_unlock(struct irq_data *d)
{
	struct my_exp *exp = irq_data_get_irq_chip_data(d);

	if (exp->irq_mask != exp->irq_mask_cache) {
		i2c_smbus_write_word_data(exp->client, MASK_REG, exp->irq_mask);
		exp->irq_mask_cache = exp->irq_mask;
	}
	mutex_unlock(&exp->irq_lock);
}

static const struct irq_chip my_exp_irq_chip = {
	.name			= "my-exp",
	.irq_mask		= my_exp_irq_mask,
	.irq_unmask		= my_exp_irq_unmask,
	.irq_bus_lock		= my_exp_irq_bus_lock,		/* */
	.irq_bus_sync_unlock	= my_exp_irq_bus_sync_unlock,	/* */
	.flags			= IRQCHIP_IMMUTABLE,
	GPIOCHIP_IRQ_RESOURCE_HELPERS,		/* 若是 gpiochip 才需要 */
};

/* ---- 父中断走线程 ---- */
static irqreturn_t my_exp_irq_thread(int irq, void *data)
{
	struct my_exp *exp = data;
	u32 pending;
	int hwirq;

	pending = my_exp_read_pending(exp);	/* 这里做 I2C 传输，可以睡 */
	if (!pending)
		return IRQ_NONE;

	for_each_set_bit(hwirq, (unsigned long *)&pending, NR_EXP_IRQS) {
		/* _safe 版：我们在进程上下文，需要它自己关本地中断 */
		generic_handle_domain_irq_safe(exp->domain, hwirq);
	}
	return IRQ_HANDLED;
}

static int my_exp_probe(struct i2c_client *client)
{
	/* … 建域，每条中断 irq_set_chip_and_handler(virq, chip, handle_simple_irq) … */

	return devm_request_threaded_irq(&client->dev, client->irq,
					 NULL, my_exp_irq_thread,
					 IRQF_ONESHOT | IRQF_SHARED,
					 dev_name(&client->dev), exp);
}
```

### §4.3 真实原型的对照

| 元素 | `drivers/gpio/gpio-pca953x.c` 的位置 |
|---|---|
| `irq_bus_lock` 实现 | `drivers/gpio/gpio-pca953x.c:743` |
| `irq_bus_sync_unlock` 实现 | `drivers/gpio/gpio-pca953x.c:751` |
| `irq_chip` 定义 | `drivers/gpio/gpio-pca953x.c:820` |
| 两个回调挂进去 | `drivers/gpio/gpio-pca953x.c:824-825` |
| `girq->handler = handle_simple_irq` | `drivers/gpio/gpio-pca953x.c:943` |
| `girq->threaded = true` | `drivers/gpio/gpio-pca953x.c:944` |
| ⚠️ 一条 `FIXME`（`girq->first`） | `drivers/gpio/gpio-pca953x.c:945` |

**`girq->threaded = true`（`drivers/gpio/gpio-pca953x.c:944`）是 gpiolib 的开关**：
它让 gpiolib 用 `handle_nested_irq()`（01 §4.3）而不是普通流处理器，
⇒ 子中断的 handler 在**父线程的上下文里**直接跑，所以子 handler 也可以睡。

`GPIOCHIP_IRQ_RESOURCE_HELPERS` 是 gpiolib 提供的宏，
配合 `IRQCHIP_IMMUTABLE`（02 §6.4）——
6.x 起 gpiolib 不再偷改 `irq_chip`，改成让驱动自己声明这些辅助回调。

### §4.4 ⚠️ 三个容易错的点

| 错 | 后果 |
|---|---|
| 在 `irq_mask`/`irq_unmask` 里直接做 I2C 传输 | **死机** —— 那两个回调可能在持 `desc->lock`（raw spinlock）时被调 |
| 忘了 `irq_bus_sync_unlock` 里刷缓存 | mask/unmask 永远不生效，中断行为全错 |
| 用 `generic_handle_domain_irq()` 而不是 `_safe` 版 | 在进程上下文调会触发 lockdep 告警（本地中断没关） |

---

## §5 层级域的中间层

### §5.1 什么时候需要

| 场景 | 例子 |
|---|---|
| MSI | GIC ITS 挂在 GIC v3 之上 |
| 中断复用器（crossbar） | 一颗芯片上 N 条外设中断复用 M 条控制器输入（`drivers/irqchip/irq-crossbar.c`） |
| 需要在中断到达路径上"加一层处理" | 电源域相关的唤醒逻辑 |

⚠️ **仅仅是"两级中断控制器"不需要层级域** —— 那是级联（§3、02 §1.0.B）。
判据：**这一层要不要有自己的 `irq_chip` 回调作用在同一个 virq 上**？
要 ⇒ 层级域；不要 ⇒ 级联。

### §5.2 骨架

```c
/* ---- 本层的 irq_chip：大部分动作转发给父层 ---- */
static const struct irq_chip my_mid_chip = {
	.name			= "my-mid",
	.irq_mask		= irq_chip_mask_parent,	   /* 直接用核心的 */
	.irq_unmask		= irq_chip_unmask_parent,
	.irq_eoi		= irq_chip_eoi_parent,
	.irq_set_affinity	= irq_chip_set_affinity_parent,
	.irq_set_type		= my_mid_set_type,	   /* 只有这个自己做 */
};

/* ---- 本层的 alloc：先做自己的事，再向父域申请 ---- */
static int my_mid_alloc(struct irq_domain *domain, unsigned int virq,
			unsigned int nr_irqs, void *arg)
{
	struct irq_fwspec *fwspec = arg, parent_fwspec;
	unsigned long hwirq;
	unsigned int type;
	int i, ret;

	ret = my_mid_translate(domain, fwspec, &hwirq, &type);
	if (ret)
		return ret;

	/* ① 本层：只设 hwirq 和 chip，不设流处理器 */
	for (i = 0; i < nr_irqs; i++) {
		ret = irq_domain_set_hwirq_and_chip(domain, virq + i,
						    hwirq + i, &my_mid_chip,
						    domain->host_data);
		if (ret)
			return ret;
	}

	/* ② 构造给父域的 fwspec（父域的参数格式和本层不同！） */
	parent_fwspec.fwnode      = domain->parent->fwnode;
	parent_fwspec.param_count = 3;
	parent_fwspec.param[0]    = 0;			/* GIC: SPI */
	parent_fwspec.param[1]    = my_map_to_parent(hwirq);
	parent_fwspec.param[2]    = type;

	/* ③ 向父域申请 —— 递归就在这里 */
	return irq_domain_alloc_irqs_parent(domain, virq, nr_irqs,
					    &parent_fwspec);
}

static void my_mid_free(struct irq_domain *domain, unsigned int virq,
			unsigned int nr_irqs)
{
	irq_domain_free_irqs_common(domain, virq, nr_irqs);	/* 会往上递归 */
}

static const struct irq_domain_ops my_mid_domain_ops = {
	.translate	= my_mid_translate,
	.alloc		= my_mid_alloc,
	.free		= my_mid_free,
	/* 需要"真正接线"这一步（如写 MSI 消息）才填 .activate/.deactivate */
};

	/* ---- 建域：必须给出 parent ---- */
	parent_domain = irq_find_host(parent_node);
	if (!parent_domain)
		return -ENXIO;		/* 父控制器还没就绪 */

	ic->domain = irq_domain_create_hierarchy(parent_domain, 0, NR_MID_IRQS,
						 of_node_to_fwnode(node),
						 &my_mid_domain_ops, ic);
```

### §5.3 四条纪律

| 纪律 | 依据 |
|---|---|
| **中间层用 `irq_domain_set_hwirq_and_chip()`，顶层用 `irq_domain_set_info()`** | 后者多设一个流处理器，一个 virq 只该有一个流处理器（02 §1.0.E） |
| **必须自己构造给父域的 `fwspec`** | 父域的参数格式（`param_count` 和各 param 的含义）和你这一层**不一样** |
| **`.free` 用 `irq_domain_free_irqs_common()`** | 它会往父层递归（`kernel/irq/irqdomain.c:1426`） |
| **分配自顶向下，激活自底向上** | `__irq_domain_activate_irq()` 先递归父层（`kernel/irq/irqdomain.c:1821`、03 §B4） |

⚠️ **第二条是层级域最容易写错的地方**：
往 GIC v3 申请一条 SPI 要 3 个参数 `<0 hwirq flags>`，
而你自己这一层的 DT 可能只写 2 个。
⇒ **不能把收到的 `fwspec` 原样传给父域**。

### §5.4 `.activate` 什么时候要填

| 你的这一层 | 要 `.activate` 吗 |
|---|---|
| 只是转发（复用器 / crossbar） | 不要 |
| MSI 类（要把"往哪写"告诉设备） | **要** —— 04 §2.2 |
| 需要"分配了但先不接线"（预留模式） | 要，配合 `IRQD_CAN_RESERVE`（02 §6.2） |

---

## §6 用户态运维

### §6.1 看现状

```sh
# 所有中断的计数（每 CPU 一列）
cat /proc/interrupts

# 某条中断的亲和性三件套（04 §5.2）
cat /proc/irq/42/smp_affinity            # 你要求的
cat /proc/irq/42/smp_affinity_list       # 同上，CPU 列表形式
cat /proc/irq/42/effective_affinity      # 硬件实际在往哪送
cat /proc/irq/42/affinity_hint           # 驱动给 irqbalance 的建议

# 乱中断统计
cat /proc/irq/42/spurious

# 全局默认亲和性
cat /proc/irq/default_smp_affinity
```

### §6.2 改亲和性

```sh
echo 4 > /proc/irq/42/smp_affinity          # 位掩码：只允许 CPU2
echo 0-3 > /proc/irq/42/smp_affinity_list   # CPU 列表形式

# 写完一定回读 effective_affinity 确认
cat /proc/irq/42/effective_affinity
```

⚠️ **三种写失败**：

| 现象 | 原因 |
|---|---|
| `-EPERM` | 托管中断（`IRQD_AFFINITY_MANAGED`，多队列设备）或 `IRQF_NOBALANCING`（01 §10.4） |
| `-EINVAL` | 掩码和这条中断允许的 CPU 集合不交叠 |
| 写成功但 `effective_affinity` 只有一位 | **正常** —— `IRQD_SINGLE_TARGET`（GIC SPI 只能选一个 CPU，04 §5.2） |

### §6.3 debugfs（需要 `GENERIC_IRQ_DEBUGFS`）

```sh
mount -t debugfs none /sys/kernel/debug 2>/dev/null

# 看层级域结构（唯一的窗口，04 §6.2）
ls /sys/kernel/debug/irq/domains/
cat /sys/kernel/debug/irq/domains/default
cat /sys/kernel/debug/irq/domains/'irqchip@0000000008000000-2'

# 看单条中断的四组状态位 + 每一层的 chip/hwirq
cat /sys/kernel/debug/irq/irqs/42

# 注入一次中断（04 §6.4）
echo trigger > /sys/kernel/debug/irq/irqs/42
```

⚠️ 域的名字带 `@地址`，用 `ls` 看实际名字再 `cat`，别猜。

### §6.4 命令行参数

| 参数 | 作用 | arm64 上建议 |
|---|---|---|
| `threadirqs` | 把所有能线程化的中断都线程化 | 调实时性问题时有用 |
| `irqaffinity=<cpulist>` | 设 `irq_default_affinity` | 隔离核场景常用 |
| `noirqdebug` | 关掉乱中断检测 | 只临时用（04 §4.3） |
| `irqfixup` / `irqpoll` | 尝试修正错误路由 | **arm64 上不该用**（04 §4.3） |

---

## §7 常见错误速查

| 症状 | 最可能的原因 | 去哪一节 |
|---|---|---|
| `platform_get_irq()` 返回 `-EPROBE_DEFER` | 正常，中断控制器还没 probe —— 直接透传返回值 | §1.1 |
| `request_threaded_irq()` → `-EINVAL` | 传了 `thread_fn` 但 handler 为 NULL 且没设 `IRQF_ONESHOT` | §1.3 |
| `request_irq()` → `-EINVAL`，dmesg 干净 | 这条 virq 是级联入口（`IRQ_NOREQUEST`） | §3.1 · 02 §1.0.B |
| `request_irq()` → `-EBUSY` | 共享冲突：别人先占了、没设 `IRQF_SHARED`，或触发类型不同 | 01 §10.4 |
| `request_irq()` → `-ENOSYS` | 这条 virq 的 chip 还是 `no_irq_chip`（手工 `irq_alloc_descs` 后忘了 `irq_set_chip_and_handler`） | 03 §A5 |
| dmesg: `irq N handler … enabled interrupts` | handler 里用了 `spin_unlock_irq()` 之类会开中断的东西 | §1.4 |
| dmesg: `irq N: nobody cared` | 共享中断的 handler 返回值错了 | 04 §4 |
| 中断只来一次就再也不来 | 级联时忘了 `chained_irq_exit()`（父层是 fasteoi 型 ⇒ 没 EOI） | §3.4 |
| 中断风暴 | ① 电平触发漏了 `IRQF_ONESHOT`；② 级联时忘了 `chained_irq_enter()`；③ 流处理器选错（level/edge 反了） | §1.3 · §3.4 · 01 §4.3 |
| 中断行为完全反了（一直来 或 一直不来） | generic-chip 的 `set_bit` / `clr_bit` 选反了 | 04 §1.2 |
| `enable_irq_wake()` → `-ENOSYS` | 层级里某层的 chip 没实现 `irq_set_wake`，或该设 `IRQCHIP_SKIP_SET_WAKE` 没设 | §1.6 · 01 §10.2 |
| `Unbalanced enable for IRQ N` | `enable_irq()` 比 `disable_irq()` 多调了 | 01 §2.1 |
| `disable_irq()` 之后硬件还在发中断 | **lazy disable** —— 需要 `IRQ_DISABLE_UNLAZY` | 03 §B3 |
| I2C 扩展器上的中断导致死机 | 在 `irq_mask`/`irq_unmask` 里直接做了 I2C 传输 | §4.4 |
| `irq_of_parse_and_map()` 返回 0，分不清原因 | 换成 `of_irq_get()` | 01 §3.2 · 03 §C4 |
| `effective_affinity` 和 `smp_affinity` 不一样 | **正常**（`IRQD_SINGLE_TARGET`） | §6.2 |
| 层级域申请失败 | 给父域的 `fwspec` 参数格式不对 | §5.3 |

---

## §8 本篇覆盖了哪些角色、漏了哪些

| 角色 | 本篇给了什么 | 没给什么 |
|---|---|---|
| 中断消费者 | 完整骨架 + 三条顺序纪律 + handler 能做什么 | 具体硬件的中断状态寄存器布局 |
| 一级控制器 | 层级域版 + 非层级版两套骨架 | GIC 那种"多个 chip / SGI-PPI-SPI-LPI 分区"的复杂形态 |
| 级联控制器 | 完整骨架 + `chained_irq_enter/exit` 的机制 | —— |
| 慢总线控制器 | 完整骨架 + 三个易错点 | gpiolib 的 `girq` 那套辅助设施（属于 gpio 子系统） |
| 层级域中间层 | 骨架 + 四条纪律 | **MSI 域的完整实现**（`msi_domain_info` 的填法，属于 04 §2 的契约层） |
| 用户态运维 | procfs / debugfs / 命令行三条路 | `irqbalance` 的配置（用户态工具，不在内核树） |

⚠️ 再强调一次篇首的免责声明：**本篇代码全部未编译未运行**。
每个骨架都点名了它提炼自哪个真实驱动（§0.2），落地时请以那个驱动为准。

---

**下一篇**：06 篇讲 DTS 的 `interrupts` / `interrupt-parent` / `#interrupt-cells` 怎么写、谁消费、配错了怎么定位。
