---
title: 使用指南
---

# timer 05：各角色的代码骨架

⚠️ **本篇所有代码骨架都没有编译过、没有运行过**（99 §5.1 有完整声明）。
它们是"形状正确"的示意：省略了部分头文件、`MODULE_*` 宏、完整的错误回滚标签。
**照抄前请对着 §0.2 点名的真实驱动 diff 一遍。**

**正文里出现的每个内核符号都做过全树存在性核对**（99 §7 有清单）。

---

## §0 本子系统有哪些角色

**七个角色，规模差别极大**：

| # | 角色 | 你要做什么 | 人数 | 去哪一节 |
|---|---|---|---|---|
| 1 | **消费者** | 读时间 / 定时 / 延时 | **绝大多数内核代码** | §1 |
| 2 | **clocksource 提供者** | 让系统能读你的硬件计数器 | 88 个文件（04 §12.2） | §2 |
| 3 | **clockevent 提供者** | 让系统能编程你的硬件定时器 | 87 个文件 | §3 |
| 4 | **sched_clock 提供者** | 给调度器提供快时钟 | 76 个文件 | §4 |
| 5 | **timecounter 使用者** | 把设备自己的计数器换成纳秒 | 26 个文件（多为网卡） | §5 |
| 6 | **alarmtimer 使用者** | 要能穿过 suspend 的定时器 | **只有 2 个**（04 §2.2） | §6 |
| 7 | **调试者** | 定时器不到点 / 时间跳变 | — | §7（+ 07 篇） |

**角色 2/3/4 在 arm64 上通常是同一个驱动**（02 §1.0.F）——
`drivers/clocksource/arm_arch_timer.c` 三个都注册了。

### §0.2 本篇参照的真实驱动

| 用途 | 文件 | 行数 | 为什么选它 |
|---|---|---|---|
| **最简实现** | `drivers/clocksource/timer-ti-32k.c` | 165 | **只注册 clocksource + sched_clock**，没有 clockevent ⇒ 骨架最干净 |
| **全功能实现** | `drivers/clocksource/arm_arch_timer.c` | 1791 | **arm64 实际在用的那个**；clocksource + clockevent + sched_clock 全有 |

---

## §1 消费者驱动

### §1.1 读时间

```c
#include <linux/ktime.h>
#include <linux/timekeeping.h>

static void my_measure(struct my_device *my)
{
	ktime_t start, end;
	s64 elapsed_ns;

	/* 测间隔一律用 mono —— 不受 settimeofday/NTP 跳变影响（01 §6.1） */
	start = ktime_get();

	my_do_hardware_thing(my);

	end = ktime_get();
	elapsed_ns = ktime_to_ns(ktime_sub(end, start));

	/* ktime_t 不会绕回，可以直接减、直接比 */
	if (elapsed_ns > 1000 * 1000)
		dev_warn(my->dev, "took %lld us\n", elapsed_ns / 1000);
}

static void my_stamp_for_user(struct my_device *my)
{
	struct timespec64 ts;

	/* 要给用户看的墙上时间才用 real */
	ktime_get_real_ts64(&ts);
	my->last_event_sec = ts.tv_sec;
}

static void my_hot_path_stamp(struct my_device *my)
{
	/* 高频路径只要秒精度 ⇒ 用最便宜的那个（01 §6.2） */
	my->last_seen = ktime_get_seconds();
}
```

⚠️ **三个高频错误**：

| 错误写法 | 为什么错 | 正确写法 |
|---|---|---|
| 用 `ktime_get_real()` 算超时 | **NTP 或改时间会让差值变成负数** | `ktime_get()` |
| 热路径里 `ktime_get_real_ts64()` | 每次都读硬件 + 做除法 | `ktime_get_coarse_real_ts64()` 或 `ktime_get_real_seconds()` |
| 为了"快"用 `ktime_get_mono_fast_ns()` | **它是给 NMI/tracing 用的**，牺牲了一致性 | 普通路径用 `ktime_get()` |

### §1.2 低精度定时器（超时场景）

```c
#include <linux/timer.h>

struct my_device {
	struct device		*dev;
	struct timer_list	timeout_timer;	/* 内嵌，不要单独 kmalloc */
	spinlock_t		lock;
	bool			done;
};

/* 回调原型固定：void (*)(struct timer_list *) */
static void my_timeout(struct timer_list *t)
{
	/* 用 from_timer() 拿回宿主结构 —— 就是 container_of 的包装 */
	struct my_device *my = from_timer(my, t, timeout_timer);

	dev_err(my->dev, "operation timed out\n");
	my_abort_hardware(my);
}

static int my_probe(struct platform_device *pdev)
{
	struct my_device *my = devm_kzalloc(&pdev->dev, sizeof(*my), GFP_KERNEL);

	if (!my)
		return -ENOMEM;

	spin_lock_init(&my->lock);
	/* 第三个参数是 flags：0 / TIMER_DEFERRABLE / TIMER_PINNED / TIMER_IRQSAFE */
	timer_setup(&my->timeout_timer, my_timeout, 0);

	return 0;
}

static int my_start_operation(struct my_device *my)
{
	my->done = false;
	my_kick_hardware(my);

	/* mod_timer 不管之前是否 pending 都能用 —— 比 add_timer 省心 */
	mod_timer(&my->timeout_timer, jiffies + msecs_to_jiffies(500));
	return 0;
}

static void my_operation_completed(struct my_device *my)
{
	my->done = true;
	/* 只是取消，之后还要再用 ⇒ timer_delete_sync() */
	timer_delete_sync(&my->timeout_timer);
}

static int my_remove(struct platform_device *pdev)
{
	struct my_device *my = platform_get_drvdata(pdev);

	/*
	 * 拆除路径用 timer_shutdown_sync()，不是 timer_delete_sync()：
	 * 它把 timer->function 置 NULL，之后任何 mod_timer() 都被静默丢弃
	 * ⇒ "回调再也不会跑" 变成代码保证，而不是靠你检查所有重启路径（01 §3.4）
	 */
	timer_shutdown_sync(&my->timeout_timer);
	return 0;
}
```

⚠️ **`timer_delete_sync()` 的锁规则**（01 §3.4，原文在
`kernel/time/timer.c:1584-1624`）：**调用时不能持有任何会在中断上下文被拿到的锁，
即使那把锁和这个定时器无关**。所以上面 `my_operation_completed()` 里
**不能**在 `spin_lock_irqsave(&my->lock)` 里面调它。

### §1.3 高精度定时器

```c
#include <linux/hrtimer.h>

struct my_device {
	struct hrtimer		sample_timer;
	ktime_t			period;
};

/* 回调返回值决定是否自动重启 */
static enum hrtimer_restart my_sample(struct hrtimer *t)
{
	struct my_device *my = container_of(t, struct my_device, sample_timer);

	my_read_one_sample(my);

	if (!my->keep_going)
		return HRTIMER_NORESTART;

	/*
	 * 周期定时器的标准写法：先 forward，再 return RESTART。
	 * ⚠️ 不 forward 就 return RESTART ⇒ expires 没变 ⇒ 立刻再到期 ⇒ 死循环
	 */
	hrtimer_forward_now(t, my->period);
	return HRTIMER_RESTART;
}

static void my_setup_hrtimer(struct my_device *my)
{
	/*
	 * hrtimer_init() 不接收回调参数 —— 回调要自己赋值
	 * CLOCK_MONOTONIC + HRTIMER_MODE_REL 是最常用的组合
	 * 显式选 _SOFT 还是 _HARD（本树 PREEMPT_RT 下这个选择有实质差别，见 §1.4）
	 */
	hrtimer_init(&my->sample_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_SOFT);
	my->sample_timer.function = my_sample;

	my->period = ms_to_ktime(10);
}

static void my_start_hrtimer(struct my_device *my)
{
	my->keep_going = true;

	/*
	 * 优先用 _range_ns 版本：给内核 1ms 松弛量，
	 * 它就能把你的唤醒和别人的合并 ⇒ 少一次中断，省电（01 §4.1）
	 */
	hrtimer_start_range_ns(&my->sample_timer, my->period,
			       NSEC_PER_MSEC, HRTIMER_MODE_REL_SOFT);
}

static void my_stop_hrtimer(struct my_device *my)
{
	my->keep_going = false;

	/* ⚠️ hrtimer_cancel() 绝对不能在自己的回调里调 —— 会死等自己 */
	hrtimer_cancel(&my->sample_timer);
}
```

### §1.4 ⚠️ 本树特有：`HARD` 还是 `SOFT` 必须显式想清楚

**这棵树可以开 `CONFIG_PREEMPT_RT`**（01 §0.1），
所以"回调在什么上下文跑"从编译期常量变成了配置期变量：

| mode | 非 RT 内核 | RT 内核 |
|---|---|---|
| `HRTIMER_MODE_*_HARD` | **硬中断上下文** | **仍然是硬中断上下文** |
| `HRTIMER_MODE_*_SOFT` | softirq 上下文 | **`timersd` 内核线程**（03 §B2.2） |

**怎么选**：

| 你的回调 | 用 | 理由 |
|---|---|---|
| 只写几个寄存器 / 唤醒一个任务 | `_HARD` | 少两次上下文切换，延迟最低 |
| 要拿 `spinlock_t`（非 raw）、要 `kmalloc`、可能睡 | **必须 `_SOFT`** | ⚠️ RT 上 `spinlock_t` 是可睡的，在硬中断里拿它会炸 |
| 不确定 | **`_SOFT`** | **保守选择**；`_HARD` 的约束严格得多 |

⚠️ **最危险的写法**：用 `_HARD` 而回调里拿了普通 `spinlock_t`。
非 RT 内核上跑得好好的，一开 RT 就死。
⇒ **本树上写 hrtimer 必须显式写出 `_SOFT` 或 `_HARD`**，
不要用不带后缀的 `HRTIMER_MODE_REL`（它等于 `_HARD` 的语义分类，
容易让人误以为"没选"）。

### §1.5 延时与睡眠

```c
#include <linux/delay.h>

static int my_reset_hardware(struct my_device *my)
{
	/* 原子上下文（持锁/中断里）只能忙等 */
	writel(RESET_BIT, my->base + REG_CTRL);
	udelay(10);				/* < ~10us ⇒ udelay（01 §5.2） */
	writel(0, my->base + REG_CTRL);

	return 0;
}

static int my_wait_for_ready(struct my_device *my)
{
	int i;

	/* 非原子 + 10us~20ms ⇒ usleep_range，给足 range 让内核合并唤醒 */
	for (i = 0; i < 100; i++) {
		if (readl(my->base + REG_STATUS) & STATUS_READY)
			return 0;
		usleep_range(100, 200);
	}
	return -ETIMEDOUT;
}

static int my_long_wait(struct my_device *my)
{
	/* > 10ms ⇒ msleep 就够；⚠️ 1~20ms 别用 msleep（实际会睡到 ~20ms） */
	msleep(50);
	return 0;
}
```

⚠️ **arm64 上 `udelay()` 比别的架构可靠**：
`arch/arm64/kernel/time.c:69` 直接用架构定时器频率算 `lpj_fine`，
不做标定循环 ⇒ **不受 CPU 调频影响**（03 §A5）。
但语义仍然是"**至少** N 微秒"，不是"恰好"。

---

## §2 clocksource 提供者

**参照 `drivers/clocksource/timer-ti-32k.c`**（165 行，最干净的实例）。

```c
#include <linux/clocksource.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/sched_clock.h>

struct my_cs {
	void __iomem		*counter;
	struct clocksource	cs;
};

static struct my_cs my_cs_dev = {
	.cs = {
		.name	= "my-counter",		/* sysfs 里显示的就是它 */
		.rating	= 250,			/* 选优靠它比大小（§2.2） */
		.read	= my_cs_read,
		.mask	= CLOCKSOURCE_MASK(32),	/* ⚠️ 必须等于硬件真实位数 */
		.flags	= CLOCK_SOURCE_IS_CONTINUOUS,
	},
};

/* read() 的签名固定，返回原始 cycles（不是纳秒） */
static u64 notrace my_cs_read(struct clocksource *cs)
{
	struct my_cs *my = container_of(cs, struct my_cs, cs);

	return readl_relaxed(my->counter);
}

static int __init my_timer_init(struct device_node *np)
{
	int ret;

	my_cs_dev.counter = of_iomap(np, 0);
	if (!my_cs_dev.counter) {
		pr_err("my-timer: can't ioremap\n");
		return -ENXIO;
	}

	/* 用 _hz 版本，让核心代算 mult/shift（01 §7） */
	ret = clocksource_register_hz(&my_cs_dev.cs, 32768);
	if (ret) {
		pr_err("my-timer: can't register clocksource\n");
		return ret;
	}

	return 0;
}

/* 这个宏把驱动登记给 03 §A5 的 timer_probe() */
TIMER_OF_DECLARE(my_timer, "vendor,my-timer", my_timer_init);
```

### §2.1 ⚠️ 四个必须想清楚的字段

| 字段 | 写错的后果 |
|---|---|
| ⚠️`mask` | **32 位硬件写成 64 位 mask ⇒ 绕回时时间跳几百年**（03 §B3） |
| `rating` | 太低选不上，太高会顶掉更好的时钟源（§2.2） |
| `read` 的 `notrace` | **不加 `notrace` 可能造成 ftrace 递归**——`timer-ti-32k.c` 和 `arm_arch_timer.c` 都加了 |
| `flags` | 不设 `CLOCK_SOURCE_IS_CONTINUOUS` ⇒ 核心认为它不连续，**不能用于高精度模式** |

### §2.2 `rating` 该填多少

**参考本树的真实取值**：

| 时钟源 | rating | 位置 |
|---|---|---|
| `jiffies`（兜底） | **1** | `kernel/time/jiffies.c:34`（注释：`lowest valid rating`） |
| `refined-jiffies` | 2 | `kernel/time/jiffies.c:86-87` |
| arm64 arch timer | **400** | `drivers/clocksource/arm_arch_timer.c:235` |

⇒ **一个"能用但不如架构定时器"的外设计数器，填 200~300 比较安全**。
想临时切换调试时不用改代码——sysfs 的 `current_clocksource` 可写（07 篇）。

### §2.3 更省事的路径：`clocksource_mmio_init()`

如果你的时钟源就是**一个 MMIO 寄存器**，不用自己写 `read()`：

```c
	/* 一行搞定，核心提供 4 个现成的 read 函数（01 §7） */
	ret = clocksource_mmio_init(base + REG_COUNTER, "my-counter",
				    32768 /* hz */, 250 /* rating */,
				    32 /* bits */, clocksource_mmio_readl_up);
```

四个现成 read 函数：`clocksource_mmio_readl_up` / `_readl_down` /
`_readw_up` / `_readw_down`（`include/linux/clocksource.h:272-275`），
`_down` 用于**递减**计数的硬件。

---

## §3 clockevent 提供者

```c
#include <linux/clockchips.h>
#include <linux/interrupt.h>

struct my_ce {
	void __iomem			*base;
	struct clock_event_device	ced;
};

static int my_set_next_event(unsigned long cycles,
			     struct clock_event_device *ced)
{
	struct my_ce *my = container_of(ced, struct my_ce, ced);

	writel_relaxed(cycles, my->base + REG_LOAD);
	writel_relaxed(CTRL_ENABLE | CTRL_ONESHOT, my->base + REG_CTRL);

	/* 返回 0 成功；返回 -ETIME 表示"这个时刻已经过去了" */
	return 0;
}

static int my_set_state_shutdown(struct clock_event_device *ced)
{
	struct my_ce *my = container_of(ced, struct my_ce, ced);

	writel_relaxed(0, my->base + REG_CTRL);
	return 0;
}

static int my_set_state_oneshot(struct clock_event_device *ced)
{
	/* 只切模式，不启动 —— 启动是 set_next_event 的事 */
	return my_set_state_shutdown(ced);
}

/* 你的中断处理函数：清中断，然后调核心填给你的 event_handler */
static irqreturn_t my_timer_irq(int irq, void *dev_id)
{
	struct clock_event_device *ced = dev_id;
	struct my_ce *my = container_of(ced, struct my_ce, ced);

	writel_relaxed(IRQ_ACK, my->base + REG_IRQ);

	/*
	 * ⚠️ event_handler 不是你实现的 —— 是核心根据这个设备被分配到
	 * 什么职责（周期 tick / 单次 / 广播）填进来的函数指针（01 §8）
	 */
	ced->event_handler(ced);

	return IRQ_HANDLED;
}

static int __init my_ce_init(struct device_node *np)
{
	struct my_ce *my = &my_ce_dev;
	int irq, ret;

	my->base = of_iomap(np, 0);
	if (!my->base)
		return -ENXIO;

	irq = irq_of_parse_and_map(np, 0);
	if (!irq)
		return -EINVAL;

	my->ced.name			= "my-timer";
	my->ced.features		= CLOCK_EVT_FEAT_ONESHOT;
	my->ced.rating			= 300;
	my->ced.irq			= irq;
	my->ced.cpumask			= cpu_possible_mask;	/* 全局设备 */
	my->ced.set_next_event		= my_set_next_event;
	my->ced.set_state_shutdown	= my_set_state_shutdown;
	my->ced.set_state_oneshot	= my_set_state_oneshot;

	ret = request_irq(irq, my_timer_irq, IRQF_TIMER, "my-timer", &my->ced);
	if (ret)
		return ret;

	/*
	 * ⚠️ 这个函数返回 void —— 注册失败你拿不到任何反馈（03 §C2）
	 * 参数：设备、频率、最小 cycles、最大 cycles
	 */
	clockevents_config_and_register(&my->ced, 32768, 0xf, 0xffffffff);

	return 0;
}

TIMER_OF_DECLARE(my_ce, "vendor,my-timer-ce", my_ce_init);
```

### §3.1 ⚠️ 五个坑

| 坑 | 后果 |
|---|---|
| ⚠️ **以为要自己实现 `event_handler`** | **最常见的误解**（01 §8）——你只调用它 |
| ⚠️ **`clockevents_config_and_register()` 返回 `void`** | 注册失败静默（03 §C2）⇒ 只能靠 `/proc/timer_list` 确认设备出现了 |
| ⚠️ **`min_delta`（第 3 个参数）填太小** | `set_next_event()` 会频繁"编程了但已过期" ⇒ `nr_retries` 暴涨（03 §B6） |
| ⚠️ **`cpumask` 填错** | 填 `cpu_possible_mask` = 全局设备（承担周期 tick）；填 `cpumask_of(cpu)` = 每 CPU 设备。**填错会导致职责分配不对** |
| ⚠️ **忘了 `IRQF_TIMER`** | 定时器中断需要这个标志（影响 suspend 时的处理和 RT 上的线程化） |

### §3.2 `features` 位怎么选

| feature | 什么时候设 |
|---|---|
| `CLOCK_EVT_FEAT_ONESHOT` | **硬件能编程到任意时刻** ⇒ 高精度/NO_HZ 的前提 |
| `CLOCK_EVT_FEAT_PERIODIC` | 硬件能自动周期触发 |
| ⚠️`CLOCK_EVT_FEAT_C3STOP` | **深度休眠时本设备会停** ⇒ 核心会安排广播（04 §1） |
| `CLOCK_EVT_FEAT_KTIME` | 你实现的是 `set_next_ktime()` 而不是 `set_next_event()` |

⚠️ **不设 `ONESHOT` 的后果**：整个系统进不了高精度模式，也用不了 NO_HZ
（03 §A6 的 `tick_check_oneshot_change()` 会一直返回假）。

---

## §4 sched_clock 提供者

```c
#include <linux/sched_clock.h>

/* 必须 notrace，且必须任意上下文可调（含 NMI） */
static u64 notrace my_read_sched_clock(void)
{
	return readl_relaxed(my_base + REG_COUNTER);
}

static int __init my_timer_init(struct device_node *np)
{
	/* ... 前面已经 clocksource_register_hz() ... */

	/* 参数：read 函数、计数器有效位数、频率 */
	sched_clock_register(my_read_sched_clock, 32, 32768);

	return 0;
}
```

**`timer-ti-32k.c` 就是这个形状**：先 `clocksource_register_hz(&cs, 32768)`
再 `sched_clock_register(omap_32k_read_sched_clock, 32, 32768)`。

⚠️ **三条约束**：
1. **必须 NMI-safe**——不能加锁，不能等
2. **`bits` 参数要准**：框架靠它算多久"翻页"一次防绕回（04 §3.3）
3. **全系统只能有一个**——后注册的会覆盖先注册的

---

## §5 timecounter 使用者（网卡 / PTP）

```c
#include <linux/timecounter.h>

struct my_nic {
	struct cyclecounter	cc;
	struct timecounter	tc;
	spinlock_t		tc_lock;	/* timecounter 不自带锁 */
};

static u64 my_nic_read_cc(const struct cyclecounter *cc)
{
	struct my_nic *nic = container_of(cc, struct my_nic, cc);

	return readq(nic->base + REG_HW_CLOCK);
}

static void my_nic_init_timecounter(struct my_nic *nic)
{
	nic->cc.read	= my_nic_read_cc;
	nic->cc.mask	= CYCLECOUNTER_MASK(48);	/* 硬件真实位数 */
	nic->cc.mult	= my_calc_mult(nic);
	nic->cc.shift	= my_calc_shift(nic);

	spin_lock_init(&nic->tc_lock);
	timecounter_init(&nic->tc, &nic->cc, ktime_get_real_ns());
}

/* ⚠️ 必须周期性调用，否则计数器绕一圈就丢时间（04 §4.1） */
static void my_nic_overflow_work(struct work_struct *work)
{
	struct my_nic *nic = container_of(work, struct my_nic, overflow_work.work);
	unsigned long flags;

	spin_lock_irqsave(&nic->tc_lock, flags);
	timecounter_read(&nic->tc);		/* 读一次就是推进一次 */
	spin_unlock_irqrestore(&nic->tc_lock, flags);

	schedule_delayed_work(&nic->overflow_work, MY_OVERFLOW_PERIOD);
}

/* 把一个历史的硬件时间戳换成系统纳秒 */
static u64 my_nic_stamp_to_ns(struct my_nic *nic, u64 hw_cycles)
{
	unsigned long flags;
	u64 ns;

	spin_lock_irqsave(&nic->tc_lock, flags);
	ns = timecounter_cyc2time(&nic->tc, hw_cycles);
	spin_unlock_irqrestore(&nic->tc_lock, flags);

	return ns;
}
```

⚠️ **三条**：
1. **`timecounter` 不自带锁**——并发保护是你的事
2. **周期性 `timecounter_read()` 是硬要求**，周期要短于"计数器绕一圈的时间的一半"
3. **和 clocksource 完全无关**，不注册到任何全局链表（04 §4.1）

---

## §6 alarmtimer 使用者

```c
#include <linux/alarmtimer.h>

struct my_wakeup {
	struct alarm	alarm;
};

static enum alarmtimer_restart my_alarm_fn(struct alarm *alarm, ktime_t now)
{
	/* 返回值语义和 hrtimer 平行 */
	return ALARMTIMER_NORESTART;
}

static void my_arm_wakeup(struct my_wakeup *w, ktime_t when)
{
	/* type 二选一：ALARM_REALTIME / ALARM_BOOTTIME */
	alarm_init(&w->alarm, ALARM_BOOTTIME, my_alarm_fn);
	alarm_start(&w->alarm, when);
}
```

⚠️ **没有 RTC 就没有唤醒能力**，而且**不报错**（04 §2.1）——
它静默退化成普通 hrtimer。⇒ 依赖唤醒的逻辑要自己确认
`alarmtimer_get_rtcdev()` 非 NULL。

**全树只有 2 个真消费者**（04 §2.2）⇒ 绝大多数场景你要的其实是
`timerfd_create(CLOCK_BOOTTIME_ALARM, ...)`（用户态）或者普通 hrtimer。

---

## §7 近邻符号区分（本篇最该细读的一节）

**这个子系统的近邻符号特别多**，而且错用的后果往往是"看起来能跑"。
**一组一小节，每组都给"错用会怎样"。**

### §7.1 ⚠️`timer_delete_sync()` vs `timer_shutdown_sync()`

| | `timer_delete_sync()` | `timer_shutdown_sync()` |
|---|---|---|
| 之后能重启吗 | **能** | **不能**（`function` 被置 NULL） |
| 返回值可信吗 | ⚠️ **并发重启时不可信**（`kernel/time/timer.c:1584-1624` 的 kerneldoc 明说） | 可信 |

**错用会怎样**：拆除路径用了 `timer_delete_sync()`，
而另一条路径在你 `kfree()` 之后又 `mod_timer()` 了 ⇒ **use-after-free**。
这正是 `timer_shutdown_sync()` 被加进来的原因。

### §7.2 ⚠️`timer_pending()` vs `hrtimer_active()` vs `hrtimer_is_queued()`

| 函数 | "回调正在跑"算不算活着 |
|---|---|
| `timer_pending()` | **不算**——只看在不在队列里 |
| `hrtimer_active()` | **算**（`base->running` 也检查，02 §5.4） |
| `hrtimer_is_queued()` | **不算**——这个才对应 `timer_pending()` |

**错用会怎样**：拿 `timer_pending() == 0` 当"回调不会再跑"的判据
⇒ **漏掉"正在跑"这个状态** ⇒ 释放对象时回调还在用它。
⇒ **判断"回调不会再跑"的唯一正确方法是 `*_sync()` 系列，不是查询函数。**

### §7.3 ⚠️`ktime_get()` vs `ktime_get_real()` vs `ktime_get_boottime()`

| | 会跳变吗 | 含挂起时间 |
|---|---|---|
| `ktime_get()`（mono） | **不会** | 不含 |
| `ktime_get_real()` | **会**（可倒退） | 不含 |
| `ktime_get_boottime()` | 不会 | **含** |

**错用会怎样**：用 `ktime_get_real()` 算超时，
NTP 往回调 1 秒 ⇒ **你的超时变成负数，逻辑立刻走进"已超时"分支**。
这类 bug 在实验室里几乎不会出现，上线几天后偶发一次。

### §7.4 ⚠️`ktime_get_*_coarse*` vs `ktime_get_*_fast_ns`

| | 精度 | 为什么快 | 什么时候用 |
|---|---|---|---|
| `*_coarse*` | 最多差一个 tick | **不读硬件**，只读上次 tick 的缓存 | **高频路径，只要粗精度** |
| `*_fast_ns` | **精度不降** | **不加 seqlock**，读双缓冲快照 | **NMI / tracing / printk** |

**错用会怎样**：因为名字里有 `fast` 就在普通路径上用 `*_fast_ns`
⇒ 拿到的可能是**正在被更新的时基**，一致性不如 `ktime_get()`，
而且**并不更快**（它只是不阻塞）。

### §7.5 ⚠️`HRTIMER_MODE_*_SOFT` vs `*_HARD`

见 §1.4 的表。**错用会怎样**：`_HARD` + 回调里拿 `spinlock_t`
⇒ **非 RT 上正常，开 RT 就在硬中断里尝试睡眠** ⇒ 死。

### §7.6 ⚠️ 内核 `timer_delete()` vs 用户态 `timer_delete()`

| | 删什么 |
|---|---|
| 内核 `timer_delete()` | 一个 `struct timer_list`（01 §3.3） |
| 用户态 `timer_delete()` | **`timer_create()` 建的 POSIX 定时器**（01 §13） |

**错用会怎样**：读代码时把两者搞混，误以为内核有一套 POSIX 定时器的内部 API。
⇒ **本子系统真实存在的同名冲突**，看 `#include` 判断在哪一层。

### §7.7 ⚠️`del_timer_sync()` vs `timer_delete_sync()`

**同一个函数**——`del_timer_sync()` 是 `include/linux/timer.h:198-201` 的
`static inline` 包装，注释写着 `Do not use in new code`。
**错用会怎样**：不会出错，但新代码用旧名会和周围风格不一致。
⚠️ **注意 `try_to_del_timer_sync()` 没有改名版**（`include/linux/timer.h:184`，03 §C5）。

### §7.8 ⚠️`usleep_range()` vs `msleep()` vs `udelay()`

见 §1.5 和 01 §5.2 的决策表。
**最常见的错用**：`msleep(1)` 想睡 1ms ⇒ **实际可能睡到 ~20ms**
（`Documentation/timers/timers-howto.rst` 点名的坑）。这个区间要用 `usleep_range()`。

---

## §8 常见错误速查

| 症状 | 最可能的原因 | 去哪一节 |
|---|---|---|
| 定时器回调没被调用 | ① 忘了 `mod_timer()`/`hrtimer_start()`；② **之前调过 `timer_shutdown*()`** | §1.2 · 01 §3.4 |
| 回调被调用两次 / 死循环 | **`return HRTIMER_RESTART` 之前没 `hrtimer_forward_now()`** | §1.3 |
| 释放对象后崩溃 | 用了 `timer_delete_sync()` 而非 `timer_shutdown_sync()`；或用 `timer_pending()` 当判据 | §7.1 · §7.2 |
| `timer_delete_sync()` 卡死 | **持有会在中断上下文被拿的锁**（哪把锁都算） | §1.2 · 01 §3.4 |
| 开 PREEMPT_RT 后崩 | `_HARD` 回调里拿了 `spinlock_t` | §1.4 · §7.5 |
| 超时偶发变成立即超时 | **用了 `ktime_get_real()`** | §7.3 |
| 时间跳变几百年 | **clocksource 的 `mask` 位数写错** | §2.1 · 03 §B3 |
| 系统进不了高精度/NO_HZ | clockevent 没设 `CLOCK_EVT_FEAT_ONESHOT` | §3.2 |
| `nr_retries` 暴涨 | `min_delta` 填太小 | §3.1 · 03 §B6 |
| clockevent 注册了但没生效 | **它返回 `void`，只能查 `/proc/timer_list`** | §3.1 · 04 §10 |
| suspend 期间没被唤醒 | 没有 RTC ⇒ alarmtimer 静默退化 | §6 |
| 网卡时间戳越用越偏 | `timecounter_read()` 调用周期太长 | §5 |

---

## §9 本篇覆盖了哪些角色、漏了哪些

| 角色 | 覆盖情况 |
|---|---|
| 消费者 | **完整**（读时间 / 两种定时器 / 延时 / RT 分叉） |
| clocksource 提供者 | **完整**（含 `clocksource_mmio_init()` 简化路径） |
| clockevent 提供者 | **完整**（含中断处理和 features） |
| sched_clock 提供者 | **完整但简短**（它本来就只有一个函数） |
| timecounter 使用者 | **骨架完整**，⚠️ `mult`/`shift` 的计算没展开（各驱动自己算） |
| alarmtimer 使用者 | **骨架完整** |
| 调试者 | ⚠️ **本篇只给了速查表，具体操作全在 07 篇** |

⚠️ **本篇没有覆盖的两个角色**（显式声明，不是遗漏）：

| 没覆盖的 | 为什么 |
|---|---|
| **POSIX 定时器的内核侧实现者** | 那一层是系统调用实现，几乎没有外部扩展点（04 §6.1：4238 行只有 2 个导出符号） |
| **广播设备提供者** | arm64 上用 hrtimer 版兜底（04 §1.3），**没有平台需要自己写** |
