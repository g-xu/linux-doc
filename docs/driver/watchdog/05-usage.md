---
title: 使用指南
---

# watchdog 05：各角色代码骨架

## §0 篇首声明（必读）

⚠️ **本篇所有代码骨架都未经编译、未上机运行。** 它们的用途是**说明调用次序和参数关系**，
不是可直接粘贴的驱动或守护进程。每个骨架里出现的符号都已在 `linux-6.1.158` 树上逐个 `grep` 确认存在
（清单见 99 §3），但**错误路径的完整性、Kconfig 依赖、厂商寄存器名（`FOO_*` 是假名）都做了删减**。

⚠️ **一个例外**：**§4.1 的用户态最小骨架直接引自 in-tree 的
`samples/watchdog/watchdog-simple.c`（25 行）** ——**那一段是真实的、能编译的代码。**

### §0.1 本子系统有哪些角色

**watchdog 与其他框架型子系统最大的不同：它有真正的用户态角色**（01 §0.1）。

| # | 角色 | 它要做的事 | 树内数量级 | 本篇 |
|---|---|---|---|---|
| 1 | **最简看门狗驱动** | **填 `watchdog_device` + `watchdog_ops`（只实现 `start`）** | **约 170 个驱动** | §1 |
| 2 | **完整看门狗驱动** | **加 `stop`/`ping`/`set_timeout`/`get_timeleft`/`restart`** | 常见 | §2 |
| 3 | **停不下来的看门狗驱动** | **不实现 `stop`，必须设 `max_hw_heartbeat_ms`** | **相当常见**（SoC 内置看门狗） | §3 |
| 4 | **用户态守护进程** | **打开 `/dev/watchdog`，定期喂，退出前写 `'V'`** | **每个系统一个** | §4 |
| 5 | **用户态诊断工具** | **用 ioctl 查能力/状态/上次复位原因** | — | §5 |
| 6 | **pretimeout governor 作者** | **填 `watchdog_governor`** | **树里只有 2 个** | §6 |

### §0.2 按任务找章节

| 我要… | 去 |
|---|---|
| 写一个最简看门狗驱动 | §1 |
| 支持改超时、查剩余时间 | §2 |
| 我的看门狗停不下来 | §3 |
| 写守护进程 | §4 |
| 查"上次是不是看门狗复位的" | §5.2 |
| 写一个 governor | §6 |
| **两个名字很像的东西该用哪个** | **§7（独立一节）** |
| 常见错误现象 | §8 |

---

## §1 角色 1：最简看门狗驱动

### §1.1 骨架

```c
#include <linux/watchdog.h>
#include <linux/platform_device.h>

#define FOO_WDT_DEFAULT_TIMEOUT	60

struct foo_wdt {
	struct watchdog_device	wdd;		/* 内嵌，不是指针 */
	void __iomem		*base;
};

/* ── 唯一必需的回调 ─────────────────────────────── */
static int foo_wdt_start(struct watchdog_device *wdd)
{
	struct foo_wdt *fw = watchdog_get_drvdata(wdd);

	writel(wdd->timeout * FOO_TICKS_PER_SEC, fw->base + FOO_WDT_LOAD);
	writel(FOO_WDT_ENABLE, fw->base + FOO_WDT_CTRL);
	return 0;
}

static const struct watchdog_info foo_wdt_info = {
	.options	= WDIOF_SETTIMEOUT | WDIOF_KEEPALIVEPING | WDIOF_MAGICCLOSE,
	.identity	= "Foo Watchdog",	/* 32 字节定长，见 §7.6 */
};

static const struct watchdog_ops foo_wdt_ops = {
	.owner		= THIS_MODULE,		/* 别漏，见 §7.1 */
	.start		= foo_wdt_start,
};

static int foo_wdt_probe(struct platform_device *pdev)
{
	struct foo_wdt *fw;

	fw = devm_kzalloc(&pdev->dev, sizeof(*fw), GFP_KERNEL);
	if (!fw)
		return -ENOMEM;

	fw->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(fw->base))
		return PTR_ERR(fw->base);

	/* ① 填静态描述 */
	fw->wdd.info		= &foo_wdt_info;
	fw->wdd.ops		= &foo_wdt_ops;
	fw->wdd.parent		= &pdev->dev;
	fw->wdd.min_timeout	= 1;
	fw->wdd.max_timeout	= 0xFFFF / FOO_TICKS_PER_SEC;
	fw->wdd.timeout		= FOO_WDT_DEFAULT_TIMEOUT;

	/* ② 私有数据 */
	watchdog_set_drvdata(&fw->wdd, fw);

	/* ③ 用模块参数或 DT 的 timeout-sec 覆盖默认超时 */
	watchdog_init_timeout(&fw->wdd, 0, &pdev->dev);

	/* ④ 行为开关（按需） */
	watchdog_set_nowayout(&fw->wdd, WATCHDOG_NOWAYOUT);
	watchdog_stop_on_reboot(&fw->wdd);
	watchdog_stop_on_unregister(&fw->wdd);

	/* ⑤ 注册 */
	return devm_watchdog_register_device(&pdev->dev, &fw->wdd);
}
```

### §1.2 ⚠️ 六个必看点

| # | 点 | 依据 |
|---|---|---|
| 1 | **`.owner = THIS_MODULE` 不能漏** | **官方文档原话：*"This to avoid a system crash when you unload the module and /dev/watchdog is still open"***；**`open()` 里 `try_module_get(wdd->ops->owner)`（`drivers/watchdog/watchdog_dev.c:867`）** |
| 2 | **`info` / `ops` / `start` 三者缺一注册就失败** | `drivers/watchdog/watchdog_core.c:244-249` 两处 `-EINVAL` |
| 3 | **`watchdog_set_drvdata()` 要在注册前调** | **注册后核心可能立刻开始喂狗（`WDOG_HW_RUNNING`），回调里要能取到私有数据** |
| 4 | **`watchdog_init_timeout()` 的第二参数是模块参数值** | **传 0 表示"没有模块参数，直接看 DT"**（06 篇） |
| 5 | ⚠️ **`WATCHDOG_NOWAYOUT` 是宏不是变量** | **`include/linux/watchdog.h:124` 的 `IS_BUILTIN(CONFIG_WATCHDOG_NOWAYOUT)`** ⇒ **编译期常量** |
| 6 | **`options` 里那三个标志的分工** | **`WDIOF_SETTIMEOUT` 要自己填；⚠️`WDIOF_MAGICCLOSE` 和 `WDIOF_KEEPALIVEPING` 官方说"由核心报，驱动不用填"** ——**但填了也无害，树里很多驱动都填**（§7.6） |

### §1.3 另一种写法：`.status` 静态初始化 nowayout

```c
static struct watchdog_device foo_wdd = {
	.info	= &foo_wdt_info,
	.ops	= &foo_wdt_ops,
	.status	= WATCHDOG_NOWAYOUT_INIT_STATUS,	/* include/linux/watchdog.h:125 */
};
```
**官方文档给了这两种等价写法**（`watchdog-kernel-api.rst`）：
**静态 `WATCHDOG_NOWAYOUT_INIT_STATUS` 或运行期 `watchdog_set_nowayout()`。**

---

## §2 角色 2：完整看门狗驱动

### §2.1 加上四个常用回调

```c
static int foo_wdt_stop(struct watchdog_device *wdd)
{
	struct foo_wdt *fw = watchdog_get_drvdata(wdd);

	writel(0, fw->base + FOO_WDT_CTRL);
	return 0;
}

static int foo_wdt_ping(struct watchdog_device *wdd)
{
	struct foo_wdt *fw = watchdog_get_drvdata(wdd);

	writel(FOO_WDT_KICK_MAGIC, fw->base + FOO_WDT_KICK);
	return 0;
}

static int foo_wdt_set_timeout(struct watchdog_device *wdd, unsigned int t)
{
	struct foo_wdt *fw = watchdog_get_drvdata(wdd);

	/* 官方文档规定的返回值：0 / -EINVAL（越界）/ -EIO（写不进去） */
	if (watchdog_timeout_invalid(wdd, t))		/* 注意语义是反的，§7.4 */
		return -EINVAL;

	writel(t * FOO_TICKS_PER_SEC, fw->base + FOO_WDT_LOAD);

	/* 必须把"实际达到的值"写回去 */
	wdd->timeout = t;
	return 0;
}

static unsigned int foo_wdt_get_timeleft(struct watchdog_device *wdd)
{
	struct foo_wdt *fw = watchdog_get_drvdata(wdd);

	/* 返回类型是 unsigned int，没有错误码可返回 */
	return readl(fw->base + FOO_WDT_COUNT) / FOO_TICKS_PER_SEC;
}

static const struct watchdog_ops foo_wdt_ops = {
	.owner		= THIS_MODULE,
	.start		= foo_wdt_start,
	.stop		= foo_wdt_stop,
	.ping		= foo_wdt_ping,
	.set_timeout	= foo_wdt_set_timeout,
	.get_timeleft	= foo_wdt_get_timeleft,
};
```

### §2.2 ⚠️`set_timeout` 的三条硬要求（官方文档明文）

| 要求 | 原文依据 |
|---|---|
| **成功时必须把实际达到的超时写进 `wdd->timeout`** | **原话：*"On success this routine should set the timeout value of the watchdog_device to the achieved timeout value (which may be different from the requested one because the watchdog does not necessarily have a 1 second resolution)."*** |
| **越界返回 `-EINVAL`，写硬件失败返回 `-EIO`** | **原话逐字规定了这两个码** |
| ⚠️ **用了 pretimeout 的话，`set_timeout` 还要自己重新校验 pretimeout** | **原话：*"then set_timeout must also take care of checking if pretimeout is still valid and set up the timer accordingly. This can't be done in the core without races, so it is the duty of the driver."*** |

⇒ **第三条是"核心明确把责任推给驱动"的少见例子**，**理由是"核心做不到无竞争"。**

### §2.3 用看门狗重启机器：`restart`

```c
static int foo_wdt_restart(struct watchdog_device *wdd, unsigned long action,
			   void *data)
{
	struct foo_wdt *fw = watchdog_get_drvdata(wdd);

	/* 设成最短超时并立刻启动 ⇒ 马上复位 */
	writel(1, fw->base + FOO_WDT_LOAD);
	writel(FOO_WDT_ENABLE, fw->base + FOO_WDT_CTRL);

	mdelay(100);		/* 等它真的复位 */
	return 0;
}

/* probe 里可以改优先级 */
	watchdog_set_restart_priority(&fw->wdd, 128);
```

⚠️ **签名是三个参数** ——**官方文档写的是一个参数，是过时的**（01 §2 / 03 §C2 第 1 条）。
**优先级取值指南（官方文档原文）**：
**`0` = 最后手段 · `128` = 默认 · `255` = 最高，抢占其他所有 restart handler。**

⚠️ **实现了 `ops->restart` 就会自动注册 restart notifier**（`drivers/watchdog/watchdog_core.c:318-321`）
⇒ **不需要驱动自己调什么"启用"函数。**
⚠️ **注册失败只 `pr_warn`，probe 仍然成功**（03 §A3.4）。

---

## §3 角色 3：停不下来的看门狗

### §3.1 骨架的两处不同

```c
static const struct watchdog_ops foo_wdt_ops = {
	.owner		= THIS_MODULE,
	.start		= foo_wdt_start,
	/* 没有 .stop —— 硬件一旦启动就停不下来 */
	.ping		= foo_wdt_ping,
};

static int foo_wdt_probe(struct platform_device *pdev)
{
	…
	fw->wdd.info		= &foo_wdt_info;
	fw->wdd.ops		= &foo_wdt_ops;
	fw->wdd.timeout		= 60;		/* 用户态视角：60 秒 */

	/* 没有 stop 就必须设这个，否则注册返回 -EINVAL */
	fw->wdd.max_hw_heartbeat_ms = 2000;	/* 硬件只能撑 2 秒 */

	/* 如果启动时看门狗已经在跑（bootloader 开的） */
	if (readl(fw->base + FOO_WDT_CTRL) & FOO_WDT_ENABLE)
		set_bit(WDOG_HW_RUNNING, &fw->wdd.status);

	…
	return devm_watchdog_register_device(&pdev->dev, &fw->wdd);
}
```

### §3.2 ⚠️ 两条规则都由代码强制执行

```c
/* drivers/watchdog/watchdog_core.c:248-249 —— 注册时的能力校验 */
	if (!wdd->ops->start || (!wdd->ops->stop && !wdd->max_hw_heartbeat_ms))
		return -EINVAL;
```

⇒ **"没有 `stop` 就必须设 `max_hw_heartbeat_ms`"不是建议，是硬性检查**（03 §A3.1）。

**设了之后核心做什么**（02 §1.0.D / 03 §B2）：
**① `timeout(60s) > max_hw_heartbeat_ms(2s)` ⇒ 内核起工作队列，每 <1 秒喂一次；
② 用户态关闭设备 ⇒ `watchdog_stop()` 走 `else` 分支置 `WDOG_HW_RUNNING`（`drivers/watchdog/watchdog_dev.c:307-308`）
⇒ 内核继续喂，直到 `open_deadline`（若配了 `WATCHDOG_OPEN_TIMEOUT`）。**

### §3.3 启动时已在跑：两个额外动作

| 动作 | 什么时候需要 | API |
|---|---|---|
| **置 `WDOG_HW_RUNNING`** | **probe 时读寄存器发现看门狗已启用** | **`set_bit(WDOG_HW_RUNNING, &wdd->status)`**（注册**前**） |
| **告诉核心上次硬件喂狗是多久以前** | **只在"硬件要求 `min_hw_heartbeat_ms` 间隔"时需要** | **`watchdog_set_last_hw_keepalive(wdd, last_ping_ms)`** |

⚠️ **`watchdog_set_last_hw_keepalive()` 的调用时机是硬要求**（官方文档原话）：
> *"This function must be called immediately after watchdog registration."*
⇒ **注册之后立刻调，不能放到别的地方。**

**配合的 Kconfig 是 `WATCHDOG_HANDLE_BOOT_ENABLED`**（默认 y）——
**它决定"内核到底要不要替这个已启动的看门狗喂狗"**（01 §0.7 / 08 §2）。

---

## §4 角色 4：用户态守护进程

### §4.1 最小骨架（in-tree 真实代码，可编译）

```c
/* samples/watchdog/watchdog-simple.c —— 全文 25 行，逐字引用 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>

int main(void)
{
	int fd = open("/dev/watchdog", O_WRONLY);
	int ret = 0;
	if (fd == -1) {
		perror("watchdog");
		exit(EXIT_FAILURE);
	}
	while (1) {
		ret = write(fd, "\0", 1);
		if (ret != 1) {
			ret = -1;
			break;
		}
		sleep(10);
	}
	close(fd);
	return ret;
}
```

⚠️ **这个样例故意不做 magic close** ——**它是"死了就重启"的语义。**
**官方 `watchdog-api.rst` 就是引用它作为最简范例的。**

⚠️ **注意 `write(fd, "\0", 1)` 写的是一个 NUL 字节，不是 `'V'`**
⇒ **正常喂狗**（任意非 `'V'` 内容都是喂狗，03 §A6）。

### §4.2 带 magic close 的正确退出

```c
	/* 优雅退出：写 'V' 再关，让看门狗真的停下来 */
	if (write(fd, "V", 1) != 1)
		perror("magic close");
	close(fd);
```

⚠️⚠️ **四条必须记住的规则**（依据都在 `drivers/watchdog/watchdog_dev.c:676-714`，03 §A6）：

| # | 规则 | 依据 |
|---|---|---|
| **1** | **`'V'` 必须在**最后一次** write 里** ——**每次 write 开头都会清掉这个标记** | `:691` 的无条件 `clear_bit(_WDOG_ALLOW_RELEASE, …)` |
| **2** | **一次 write 里 `'V'` 出现在任何位置都算** | `:694-699` 的循环不 break |
| ⚠️ **3** | **写 `'V'` 本身也是一次喂狗** | `:707` 无条件 `watchdog_ping()` |
| ⚠️ **4** | **`nowayout` 会压制 magic close** ——**写了 `'V'` 也停不下来** | **官方文档原话：*"The nowayout feature will overrule the magic close feature."***；**代码在 `drivers/watchdog/watchdog_dev.c:297-301`** |

### §4.3 ⚠️ 不写 `'V'` 就退出会看到一条 `pr_crit`

**这是设计好的正常行为，但日志看起来像故障**（03 §A7）：
```
watchdog0: watchdog did not stop!
```
**出处：`drivers/watchdog/watchdog_dev.c:946`。**

⇒ **看到这条不要慌**：**它只是说"守护进程没给 `'V'`，所以我不停表，机器稍后会重启"** ——
**正是 magic close 想要的效果。**

### §4.4 更稳的写法：用 ioctl 而不是 write

```c
#include <linux/watchdog.h>
#include <sys/ioctl.h>

	struct watchdog_info info;
	int timeout;

	/* ① 先查能力 */
	if (ioctl(fd, WDIOC_GETSUPPORT, &info) == 0) {
		printf("identity: %.32s\n", info.identity);	/* %.32s，见 §7.6 */
		if (!(info.options & WDIOF_MAGICCLOSE))
			fprintf(stderr, "no magic close, close() will stop it\n");
	}

	/* ② 设超时（同一个变量会被写回实际值） */
	timeout = 30;
	if (ioctl(fd, WDIOC_SETTIMEOUT, &timeout) == 0)
		printf("timeout actually set to %d\n", timeout);

	/* ③ 喂狗 —— 参数被忽略 */
	while (1) {
		ioctl(fd, WDIOC_KEEPALIVE, 0);
		sleep(timeout / 3);
	}
```

⚠️ **`WDIOC_KEEPALIVE` 要求驱动的 `options` 里有 `WDIOF_KEEPALIVEPING`**：
```c
/* drivers/watchdog/watchdog_dev.c:775-781 */
	case WDIOC_KEEPALIVE:
		if (!(wdd->info->options & WDIOF_KEEPALIVEPING)) {
			err = -EOPNOTSUPP;
			break;
		}
		err = watchdog_ping(wdd);
```
⇒ **没有这个标志时 `WDIOC_KEEPALIVE` 返回 `-EOPNOTSUPP`，但 `write()` 照样能喂**
⇒ **`write()` 是最通用的喂狗方式，`ioctl` 不是。**

⚠️ **`sleep(timeout / 3)` 而不是 `sleep(timeout)`** ——**留余量。**
**注意实际复位时刻可能比 `timeout` 晚最多一个 `max_hw_heartbeat_ms`**（03 §B2.3）。

---

## §5 角色 5：用户态诊断

### §5.1 查能力（`WDIOC_GETSUPPORT`）

```c
	struct watchdog_info info;

	if (ioctl(fd, WDIOC_GETSUPPORT, &info) < 0) {
		perror("WDIOC_GETSUPPORT");
		return -1;
	}
	printf("identity  : %.32s\n", info.identity);
	printf("fw version: %u\n", info.firmware_version);
	printf("options   : 0x%08x\n", info.options);
	printf("  SETTIMEOUT   : %s\n", info.options & WDIOF_SETTIMEOUT   ? "y":"n");
	printf("  MAGICCLOSE   : %s\n", info.options & WDIOF_MAGICCLOSE   ? "y":"n");
	printf("  PRETIMEOUT   : %s\n", info.options & WDIOF_PRETIMEOUT   ? "y":"n");
	printf("  KEEPALIVEPING: %s\n", info.options & WDIOF_KEEPALIVEPING? "y":"n");
```

**这是唯一能问出"硬件到底支不支持什么"的接口** ——
**它把驱动填的 `wdd->info` 原样返回**（`drivers/watchdog/watchdog_dev.c:752-753`）。

### §5.2 查"上次是不是看门狗复位的"（`WDIOC_GETBOOTSTATUS`）

```c
	int boot;

	if (ioctl(fd, WDIOC_GETBOOTSTATUS, &boot) == 0) {
		if (boot & WDIOF_CARDRESET)
			printf("last reboot was caused by the watchdog\n");
		if (boot & WDIOF_OVERHEAT)
			printf("last reboot was caused by overheating\n");
		if (boot & WDIOF_POWERUNDER)
			printf("last reboot: power fault\n");
	}
```

⇒ **这是排查"机器为什么半夜自己重启了"的第一手证据**（07 §5）。
**实现是一行 `put_user(wdd->bootstatus, p)`**（`drivers/watchdog/watchdog_dev.c:760`）
⇒ **值完全由驱动在 probe 时填**（从硬件的复位原因寄存器读）。

⚠️ **驱动没填 `bootstatus` ⇒ 恒为 0 ⇒ 拿不到任何信息，而且看不出"是没填还是真不是"。**

### §5.3 ⚠️`WDIOC_GETSTATUS` 有三个坑

```c
/* drivers/watchdog/watchdog_dev.c:329-355 —— watchdog_get_status() 全文核心 */
	if (wdd->ops->status)
		status = wdd->ops->status(wdd);
	else
		status = wdd->bootstatus & (WDIOF_CARDRESET | WDIOF_OVERHEAT |
					    WDIOF_FANFAULT | WDIOF_EXTERN1 |
					    WDIOF_EXTERN2 | WDIOF_POWERUNDER |
					    WDIOF_POWEROVER);

	if (test_bit(_WDOG_ALLOW_RELEASE, &wd_data->status))
		status |= WDIOF_MAGICCLOSE;

	if (test_and_clear_bit(_WDOG_KEEPALIVE, &wd_data->status))
		status |= WDIOF_KEEPALIVEPING;

	if (IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT))
		status |= WDIOF_PRETIMEOUT;
```

| # | 坑 | 说明 |
|---|---|---|
| ⚠️⚠️ **1** | **`WDIOF_MAGICCLOSE` 在 GETSTATUS 里的含义是"已经收到过 `'V'`"，不是"支持 magic close"** | **同一个位在 GETSUPPORT 里是"支持"，在 GETSTATUS 里是"已收到"** ——`:345-346` |
| ⚠️⚠️ **2** | **`WDIOF_KEEPALIVEPING` 是 `test_and_clear`，读一次就被消耗掉** | **连读两次 GETSTATUS，第二次一定没有这个位** ——`:348-349` |
| ⚠️ **3** | **开了 `CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT` 时无条件加上 `WDIOF_PRETIMEOUT`** | **与 GETSUPPORT 不一致**（04 §3.4） |

⇒ **判据：想问"支不支持"用 GETSUPPORT，想问"发生了什么"用 GETSTATUS。两者的位含义不同。**

⚠️ **驱动没实现 `ops->status` 时，GETSTATUS 只报 `bootstatus` 里那 7 个位**（`:337-343`）
⇒ **看起来像"当前状态"，实际是"启动时的状态"。**

### §5.4 停表与开表（`WDIOC_SETOPTIONS`）

```c
	int opts = WDIOS_DISABLECARD;
	ioctl(fd, WDIOC_SETOPTIONS, &opts);	/* 停 */

	opts = WDIOS_ENABLECARD;
	ioctl(fd, WDIOC_SETOPTIONS, &opts);	/* 开 */
```

⚠️ **两个位可以同时给，代码是"先停再开"**：
```c
/* drivers/watchdog/watchdog_dev.c:767-774 */
		if (val & WDIOS_DISABLECARD) {
			err = watchdog_stop(wdd);
			if (err < 0)
				break;		/* 停失败就不继续开 */
		}
		if (val & WDIOS_ENABLECARD)
			err = watchdog_start(wdd);
```
⇒ **`WDIOS_DISABLECARD | WDIOS_ENABLECARD` 的效果是"重启计时"。**
⚠️ **`nowayout` 下停表返回 `-EBUSY`** ⇒ **`break` ⇒ 开表也不做**（03 §A7）。

---

## §6 角色 6：pretimeout governor 作者

```c
#include <linux/module.h>
#include <linux/watchdog.h>

#include "watchdog_pretimeout.h"	/* 注意：不在 include/ 下，见 §7.7 */

static void pretimeout_foo(struct watchdog_device *wdd)
{
	/* ⚠️ 这里是 spin_lock_irqsave 内、关中断的上下文，不能睡眠！ */
	pr_alert("watchdog%d: pretimeout, saving state\n", wdd->id);
}

static struct watchdog_governor watchdog_gov_foo = {
	.name		= "foo",		/* 最长 20 字节，含结尾 */
	.pretimeout	= pretimeout_foo,
};

static int __init watchdog_gov_foo_register(void)
{
	return watchdog_register_governor(&watchdog_gov_foo);
}

static void __exit watchdog_gov_foo_unregister(void)
{
	watchdog_unregister_governor(&watchdog_gov_foo);
}
module_init(watchdog_gov_foo_register);
module_exit(watchdog_gov_foo_unregister);
MODULE_LICENSE("GPL");
```

⚠️ **三个约束**（04 §1.4 / §1.2）：
1. **回调在 `spin_lock_irqsave(&pretimeout_lock, flags)` 内被调**
   （`drivers/watchdog/watchdog_pretimeout.c:104-111`）⇒ **不能睡眠、不能调可能阻塞的东西。**
2. **`name` 是 `const char name[WATCHDOG_GOV_NAME_MAXLEN]`（20 字节的定长数组）**
   ⇒ **不是指针，赋值时字符串被复制进结构。**
3. **重名会返回 `-EBUSY`**（`drivers/watchdog/watchdog_pretimeout.c:126-130`）。

⚠️ **树外模块写不了 governor** ——**`watchdog_pretimeout.h` 在 `drivers/watchdog/` 下，
不在 `include/` 下**（02 §1.0.F）⇒ **只能在内核树内编译。**

---

## §7 近邻符号区分（独立一节，必读）

### §7.1 ⚠️ 组 1：`WDOG_ACTIVE` vs `WDOG_HW_RUNNING`

**完整对照表在 02 §1.0.A，本节只说"驱动该怎么用"**：

| | `WDOG_ACTIVE` | `WDOG_HW_RUNNING` |
|---|---|---|
| **驱动能设吗** | ⚠️ **不该设** ——**核心自己维护** | **该设** ——**probe 时发现硬件已在跑就 `set_bit`** |
| 查询 | `watchdog_active(wdd)` | `watchdog_hw_running(wdd)` |

**错用会怎样**：
**驱动自己 `set_bit(WDOG_ACTIVE)`** ⇒ **核心以为用户态已接手 ⇒
`watchdog_worker_should_ping()` 走 `watchdog_active(wdd)` 那一支（`drivers/watchdog/watchdog_dev.c:210-211`）
⇒ 内核会一直喂狗，`WATCHDOG_OPEN_TIMEOUT` 完全失效** ⇒ **看门狗形同废掉。**
**忘了设 `WDOG_HW_RUNNING`** ⇒ **bootloader 开的表没人喂 ⇒ 启动过程中意外重启。**

### §7.2 ⚠️ 组 2：`timeout` vs `max_hw_heartbeat_ms`（单位都不一样）

| | `timeout` | `max_hw_heartbeat_ms` |
|---|---|---|
| 单位 | **秒** | ⚠️ **毫秒** |
| 谁看 | **用户态** | **只有核心** |
| 不填 | **注册能过，但用户态拿不到有意义的值** | ⚠️ **没有 `stop` 时注册直接 `-EINVAL`** |

**错用会怎样**：
⚠️ **把秒填进 `max_hw_heartbeat_ms`**（例如硬件 2 秒却填 `2`）⇒
**核心以为硬件只能撑 2 毫秒 ⇒ 每 1 毫秒喂一次 ⇒ 大量无用的寄存器写 + I2C 看门狗直接打爆总线。**
**反过来把毫秒填进 `timeout`**（填 `2000` 想表达 2 秒）⇒
**用户态看到"超时 2000 秒"，形同没有看门狗。**

### §7.3 ⚠️ 组 3：`min_timeout`/`max_timeout` vs `min_hw_heartbeat_ms`/`max_hw_heartbeat_ms`

| | `min_timeout` / `max_timeout` | `min_hw_heartbeat_ms` / `max_hw_heartbeat_ms` |
|---|---|---|
| 语义 | **"用户态能设的范围"** | **"硬件的物理约束"** |
| 被谁用 | **`watchdog_timeout_invalid()`** | **核心的喂狗调度算法**（03 §B2.3） |
| ⚠️ 相互作用 | ⚠️ **`max_hw_heartbeat_ms` 非 0 时 `max_timeout` 不生效** | — |

**`max_timeout` 失效那条是官方文档明写的**（原话）：*"Not used if max_hw_heartbeat_ms is non-zero."*
**错用会怎样**：**同时设了 `max_timeout = 30` 和 `max_hw_heartbeat_ms = 2000`
⇒ 用户可以设 300 秒的超时，`max_timeout` 那个 30 被忽略。**

### §7.4 ⚠️ 组 4：`watchdog_timeout_invalid()` 的返回语义是反的

| API | 返回 `true` 表示 |
|---|---|
| `watchdog_timeout_invalid(wdd, t)` | ⚠️ **不合法** |
| `watchdog_pretimeout_invalid(wdd, t)` | ⚠️ **不合法** |
| `watchdog_active(wdd)` | **在跑**（正向） |
| `watchdog_hw_running(wdd)` | **在跑**（正向） |

**错用会怎样**：**写成 `if (!watchdog_timeout_invalid(wdd, t)) return -EINVAL;`
⇒ 合法值被拒、非法值被接受** ——**正好反了，而且编译器不会提示。**

### §7.5 ⚠️ 组 5：`-ENOIOCTLCMD` vs `-ENOTTY`

| 码 | 谁返回 | 谁看到 |
|---|---|---|
| `-ENOIOCTLCMD` | **驱动的 `ops->ioctl`** | **只有核心** ——**核心收到就继续走自己的 switch**（`drivers/watchdog/watchdog_dev.c:746-748`） |
| `-ENOTTY` | **核心的 `default:` 分支**（`:821-823`） | **用户态** |

**错用会怎样**：**驱动的 `ops->ioctl` 对不认识的命令返回 `-ENOTTY`**
⇒ **核心的 `if (err != -ENOIOCTLCMD) goto out_ioctl;`（`:747-748`）会直接把它透给用户态**
⇒ **核心自己那 11 个标准 ioctl 全部失效** ——**连 `WDIOC_KEEPALIVE` 都用不了。**
**这是写 `ops->ioctl` 时最严重的一个错。**

### §7.6 ⚠️ 组 6：`identity` 是定长数组，`options` 里有三个"不用填"

| 字段 | 类型 | 注意 |
|---|---|---|
| ⚠️`watchdog_info.identity` | **`__u8 identity[32]`** | ⚠️ **不保证以 NUL 结尾** ⇒ **用户态要 `printf("%.32s")`**；**核心也把它当 C 字符串用过**（`drivers/watchdog/watchdog_core.c:364`，03 §A2） |
| `WDIOF_MAGICCLOSE` | **能力位** | **官方说"由核心报，驱动不用填"** ——⚠️ **但在 GETSUPPORT 里核心不加，所以驱动想让用户态知道"我支持 magic close"就得自己填**（§5.3 坑 1） |
| `WDIOF_KEEPALIVEPING` | 能力位 | ⚠️ **不填的话 `WDIOC_KEEPALIVE` 返回 `-EOPNOTSUPP`**（§4.4）⇒ **必须填** |
| `WDIOF_SETTIMEOUT` | 能力位 | **必须自己填** |

⚠️⚠️ **这一组的结论与官方文档有张力**：
**文档说 `WDIOF_MAGICCLOSE` / `WDIOF_KEEPALIVEPING` "由核心报，不必从驱动报"**，
**但那句话说的是 `ops->status` 的返回值（GETSTATUS 路径），不是 `wdd->info->options`（GETSUPPORT 路径）。**
⇒ **两个路径不能混**：**`ops->status` 不用报这两个位；`wdd->info->options` 要报。**
**代码依据：`:345-349` 是核心往 status 里加，而 `:752-753` 把 `info` 原样返回、一个位都不加。**

### §7.7 ⚠️ 组 7：两个头文件、两套状态位

| | **`include/linux/watchdog.h`** | **`drivers/watchdog/watchdog_pretimeout.h`** / `watchdog_core.h` |
|---|---|---|
| 谁能 include | **任何驱动，包括树外** | ⚠️ **只有 `drivers/watchdog/` 内的文件** |
| 里面有 | `watchdog_device` · `watchdog_ops` · 6 个 `WDOG_*` · 12 个 inline | **`watchdog_governor` · `watchdog_core_data` · 3 个 `_WDOG_*`** |

**错用会怎样**：**树外模块想 `#include "watchdog_pretimeout.h"` ⇒ 找不到文件**（编译期失败）。
**想访问 `_WDOG_DEV_OPEN` ⇒ 未定义的标识符** ——**这一组是编译器会救你的。**

---

## §8 常见错误现象 → 原因

| 现象 | 最可能的原因 | 去哪查 |
|---|---|---|
| **`watchdog_register_device()` 返回 `-EINVAL`** | **`info`/`ops`/`start` 缺一，或没有 `stop` 却没设 `max_hw_heartbeat_ms`** | 03 §A3.1 |
| ⚠️ **probe 返回 0，但 `/dev/watchdog*` 不存在** | ⚠️ **走了延迟注册路径，真正的注册在后面失败了而返回值被丢弃** | **03 §A2 / §C3 第 1 类** |
| **`/dev/watchdog` 没有，只有 `/dev/watchdog1`** | **id 0 的 misc 注册撞了 `-EBUSY`，框架退让** | 03 §A3.2 |
| **DTS 里指定了 `watchdog0` 别名却成了别的号** | **alias 指定的 id 被占用，静默回退** | 03 §A3.3 / 06 篇 |
| **`open()` 返回 `-EBUSY`** | **已经有人打开了**（`/dev/watchdog` 与 `/dev/watchdog0` 是同一个设备） | 02 §1.0.C / 03 §A5 |
| **`ioctl(WDIOC_KEEPALIVE)` 返回 `-EOPNOTSUPP`** | **驱动的 `options` 没填 `WDIOF_KEEPALIVEPING`** | §4.4 / §7.6 |
| **`ioctl(WDIOC_GETTIMEOUT)` 返回 `-EOPNOTSUPP`** | **`wdd->timeout == 0`**（`drivers/watchdog/watchdog_dev.c:799-801`） | — |
| **未知 ioctl 返回 `-ENOTTY`** | **正常** ——或者驱动的 `ops->ioctl` 错返回了 `-ENOTTY` | §7.5 |
| **`dmesg: watchdog0: watchdog did not stop!`** | **多数是正常的 magic close 流程**（守护进程没写 `'V'`） | §4.3 / 03 §A7 |
| **`dmesg: watchdog0: nowayout prevents watchdog being stopped!`** | **`nowayout` 生效中，停表被拒** | 03 §A7 |
| **`dmesg: watchdog0: stop_on_reboot not supported`** | **要求重启时停表，但驱动没有 `stop`** | 03 §A3.4 |
| **改了超时但硬件没变** | **驱动没实现 `set_timeout` 却填了 `WDIOF_SETTIMEOUT`** | 01 §4.3 |
| **连读两次 GETSTATUS 结果不同** | **`WDIOF_KEEPALIVEPING` 是 `test_and_clear`** | §5.3 坑 2 |
| **GETSUPPORT 说没有 pretimeout，sysfs 里却有 `pretimeout`** | **开了 `CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT`** | 04 §3.4 |
| **pretimeout 事件没有任何反应** | **`wdd->gov` 是 NULL**（默认 governor 模块没加载）——**静默丢弃，无日志** | 04 §1.4 / §1.5 |
| **喂狗后机器还是重启了，但比 `timeout` 晚一点** | **正常**：**精度损失最多一个 `max_hw_heartbeat_ms`** | 03 §B2.3 |

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| DTS 怎么写（`timeout-sec` / aliases） | `06-devicetree.md` |
| **12 个 sysfs 属性 + 4 个 tracepoint + 用户态工具** | `07-debug.md` |
| 为什么内核会替用户态喂狗 | `08-feynman.md` |
| API 完整语义 | `01-api.md` |
| 内部流程 | `03-internals.md` |
