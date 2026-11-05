---
title: 内部实现
---

# watchdog 03：内部机制

## §0 导航

**本篇只承诺一条主轴：Part A 是时间线**（从 initcall 到注销）。
**Part B 是横切关注点**（锁 / 心跳工作队列 / 三个 notifier / 引用计数），
**Part C 是评价与设计债。**
**章节号带 A/B/C 前缀。**

### §0.1 分层图

```
   用户态守护进程
        │ open / write / ioctl / close
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │ drivers/watchdog/watchdog_dev.c（1306 行）                    │
  │  对用户态的整个面 + 心跳工作队列 + 12 个 sysfs 属性          │
  │                                                              │
  │  watchdog_fops（:970-977）：write / unlocked_ioctl /          │
  │                             compat_ioctl / open / release     │
  │  watchdog_start/stop/ping/set_timeout/…（:247-450）           │
  │  全部要求"调用者持 wd_data->lock"                           │
  └──────────────────────────┬───────────────────────────────────┘
                             │  watchdog_dev_register/unregister
                             │  watchdog_dev_suspend/resume
  ┌──────────────────────────▼───────────────────────────────────┐
  │ drivers/watchdog/watchdog_core.c（485 行）                    │
  │  注册入口 + 延迟注册 + id 分配 + 三个 notifier              │
  └──────────────────────────┬───────────────────────────────────┘
                             │  层 1：struct watchdog_ops
  ┌──────────────────────────▼───────────────────────────────────┐
  │ 看门狗驱动（约 170 个）                                        │
  └──────────────────────────────────────────────────────────────┘
```

⚠️ **两个核心文件的分工是清晰的**：
**`watchdog_dev.c` 管"对用户态的面"，`watchdog_core.c` 管"注册与系统事件"。**
⇒ **`watchdog_dev.c` 反过来给 `watchdog_core.c` 提供
`watchdog_dev_register/unregister/suspend/resume` 四个内部接口**
（声明在 `include/linux/watchdog.h:220-221` 和 `drivers/watchdog/watchdog_core.h`）。

### §0.2 生命周期时间线

```
 内核启动
   │
   ├─ subsys_initcall_sync: watchdog_init()        §A1  watchdog_core.c:461-479
   │     ├─ watchdog_dev_init()                         watchdog_dev.c:1215
   │     │     ├─ kthread_create_worker(0, "watchdogd")
   │     │     ├─ class_register(&watchdog_class)
   │     │     └─ alloc_chrdev_region(…, MAX_DOGS, "watchdog")  :1224
   │     └─ watchdog_deferred_registration()            :445-459
   │
   ├─ 驱动 probe（可能早于上面的 initcall！）
   │     └─ watchdog_register_device(wdd)           §A2  watchdog_core.c:350
   │           ├─ wtd_deferred_reg_done == false ⇒ 入队，直接返回 0
   │           └─ == true ⇒ __watchdog_register_device(wdd)   §A3  :240
   │                 ├─ 参数与能力校验                        :244-249
   │                 ├─ id 分配（先试 DT alias）             :260-291
   │                 ├─ watchdog_dev_register(wdd)  §A4       :274
   │                 │     └─ 建 watchdog_core_data + cdev + 可能的 miscdev
   │                 └─ 三个 notifier 条件注册                :294-334
   │
   ├─ 用户态 open("/dev/watchdog")                  §A5  watchdog_dev.c:842
   │     ├─ test_and_set_bit(_WDOG_DEV_OPEN) ⇒ -EBUSY  :857-858
   │     ├─ try_module_get(ops->owner)（仅 !hw_running）:867
   │     └─ watchdog_start(wdd)                          :247
   │
   ├─ 用户态 write() / ioctl(WDIOC_KEEPALIVE)       §A6
   │     └─ watchdog_ping → __watchdog_ping → ops->ping ?: ops->start
   │
   ├─ 内核自己喂狗（并行的一条路）                  §B2
   │     hrtimer → kthread_work → watchdog_worker_should_ping → __watchdog_ping
   │
   ├─ 用户态 close()                                §A7  watchdog_dev.c:920
   │     └─ magic close / nowayout 三层判定
   │
   └─ 驱动 remove
         └─ watchdog_unregister_device(wdd)         §A8  watchdog_core.c:406
```

⚠️ **注意第二个方框**：**驱动 probe 可能早于框架自己的 initcall。**
**这就是"延迟注册"存在的理由**（§A2）——**本子系统与其他框架最不同的一点。**

### §0.3 哪些内容不在本篇

| 想找 | 去 |
|---|---|
| API 清单与 11 个 ioctl | `01-api.md` |
| 结构体与六组易错关系 | `02-datastructures.md` |
| pretimeout governor / 软 pretimeout | `04-extensions.md` |
| 驱动骨架 + 用户态骨架 | `05-usage.md` |
| DT 属性 | `06-devicetree.md` |
| 12 个 sysfs 属性 + 4 个 tracepoint | `07-debug.md` |

---

# Part A：时间线

## §A1 框架自身的初始化（`watchdog_core.c:461-480`）

```c
static int __init watchdog_init(void)
{
	int err;

	err = watchdog_dev_init();
	if (err < 0)
		return err;

	watchdog_deferred_registration();
	return 0;
}

static void __exit watchdog_exit(void)
{
	watchdog_dev_exit();
	ida_destroy(&watchdog_ida);
}

subsys_initcall_sync(watchdog_init);
module_exit(watchdog_exit);
```

| 事实 | 说明 |
|---|---|
| **initcall 级别是 `subsys_initcall_sync`** | **比 `subsys_initcall` 更晚一点**（等所有 `subsys_initcall` 跑完）；⚠️ **仍然可能晚于某些 platform 驱动的 probe** ⇒ **所以需要延迟注册**（§A2） |
| **有 `module_exit`** | **`WATCHDOG_CORE` 是 `tristate`** ⇒ **框架本身可以是模块**（与 phy 的 `bool` 不同） |
| **两步：先 `watchdog_dev_init()`，再处理延迟队列** | **顺序不能反** ——**延迟注册要用到字符设备区间和 class** |

**`watchdog_dev_init()`（`drivers/watchdog/watchdog_dev.c:1215` 起）做三件事**：
**① 建全局 `kthread_worker`；② `class_register(&watchdog_class)`；
③ `alloc_chrdev_region(&watchdog_devt, 0, MAX_DOGS, "watchdog")`（`:1224`）。**

## §A2 延迟注册：本子系统最特殊的机制

```c
/* drivers/watchdog/watchdog_core.c:350-370 */
int watchdog_register_device(struct watchdog_device *wdd)
{
	const char *dev_str;
	int ret = 0;

	mutex_lock(&wtd_deferred_reg_mutex);
	if (wtd_deferred_reg_done)
		ret = __watchdog_register_device(wdd);
	else
		watchdog_deferred_registration_add(wdd);
	mutex_unlock(&wtd_deferred_reg_mutex);

	if (ret) {
		dev_str = wdd->parent ? dev_name(wdd->parent) :
			  (const char *)wdd->info->identity;
		pr_err("%s: failed to register watchdog device (err = %d)\n",
			dev_str, ret);
	}

	return ret;
}
```

**`wtd_deferred_reg_done` 这个布尔量把时间轴切成两半**（`watchdog_core.c:65`）：
**框架 initcall 之前注册 ⇒ 入队；之后注册 ⇒ 立即执行。**

⚠️⚠️ **一个必须点明的后果**：

```c
/* drivers/watchdog/watchdog_core.c:449-456 —— 队列被消费时 */
	while (!list_empty(&wtd_deferred_reg_list)) {
		…
		list_del(&wdd->deferred);
		__watchdog_register_device(wdd);	/* 返回值被丢掉了 */
	}
```

⇒ **走延迟路径时，`watchdog_register_device()` 一定返回 0，
但真正的注册可能在几百毫秒后失败，而且没人知道。**
⇒ **驱动的 probe 会"成功"，看门狗却不存在。**
**唯一的线索是 `__watchdog_register_device()` 内部的 `pr_err`/`pr_warn`**（§A3）——
⚠️ **而它自己不打"注册失败"的总日志**（那条 `pr_err` 在 `watchdog_register_device()` 里，
**延迟路径根本走不到**）。

⇒ **这是本子系统第一号静默失效**（Part C §C3）。

⚠️ **`dev_str` 的回退逻辑也值得注意**（`:363-364`）：
**没有 `parent` 时用 `wdd->info->identity`** ——**那是一个 uapi 里的 32 字节定长数组**
（01 §7.2）⇒ **不保证以 NUL 结尾，这里当 C 字符串用了。**

## §A3 `__watchdog_register_device()`：七步（`watchdog_core.c:240-337`）

| 步 | 代码 | 位置 | 失败怎么办 |
|---|---|---|---|
| ① | **三个指针非空检查**：`wdd`/`wdd->info`/`wdd->ops` | `:244-245` | `-EINVAL` |
| ② | **能力校验**：`if (!wdd->ops->start \|\| (!wdd->ops->stop && !wdd->max_hw_heartbeat_ms))` | `:248-249` | **`-EINVAL`** |
| ③ | `watchdog_check_min_max_timeout(wdd)` | `:251` | 只修正不失败 |
| ④ | **id 分配：先试 DT alias，再自动分配** | `:260-271` | 返回 IDA 的错误码 |
| ⑤ | `watchdog_dev_register(wdd)` | `:274` | **有一条特殊的重试路径**（见 §A3.2） |
| ⑥ | **`stop_on_reboot` 模块参数覆盖 + 注册 reboot notifier** | `:294-316` | **失败是致命的** |
| ⑦ | **条件注册 restart / pm notifier** | `:318-334` | ⚠️ **失败只 `pr_warn`** |

### §A3.1 第 ② 步：官方文档的规则在这里被强制执行

```c
/* drivers/watchdog/watchdog_core.c:248-249 */
	if (!wdd->ops->start || (!wdd->ops->stop && !wdd->max_hw_heartbeat_ms))
		return -EINVAL;
```

⇒ **两条规则合成一行**：
**① `start` 必需（01 §2）；② 没有 `stop` 就必须设 `max_hw_heartbeat_ms`。**
**官方文档 `watchdog-kernel-api.rst` 的原话"must set max_hw_heartbeat_ms"在这里落地了。**

⇒ **这是少见的"文档规则真的有代码强制"的例子**（对比 phy 的一堆"没实现就返回成功"）。

### §A3.2 ⚠️ 第 ⑤ 步：`id == 0` 的重试路径（很容易漏读）

```c
/* drivers/watchdog/watchdog_core.c:274-291 */
	ret = watchdog_dev_register(wdd);
	if (ret) {
		ida_simple_remove(&watchdog_ida, id);
		if (!(id == 0 && ret == -EBUSY))
			return ret;

		/* 只有"id 0 且 -EBUSY"才重试 */
		id = ida_simple_get(&watchdog_ida, 1, MAX_DOGS, GFP_KERNEL);
		if (id < 0)
			return id;
		wdd->id = id;

		ret = watchdog_dev_register(wdd);
		if (ret) {
			ida_simple_remove(&watchdog_ida, id);
			return ret;
		}
	}
```

**为什么会出现"id 0 且 `-EBUSY`"**：
**`id == 0` 时要额外 `misc_register(&watchdog_miscdev)`（02 §1.0.C）**，
**而 misc 次设备号 `WATCHDOG_MINOR` 可能已被别的东西占用**（老式看门狗驱动）。
⇒ **框架的处理是"退让"：放弃 id 0，改用 1..31，只建 `/dev/watchdogN` 不建 `/dev/watchdog`。**

⇒ **所以 `wdd->id` 不一定等于"第几个注册的"** ——**07 篇排查设备号时要记住这条。**

### §A3.3 第 ④ 步：DT alias 可以钉住 id

```c
/* drivers/watchdog/watchdog_core.c:260-268 */
	if (wdd->parent) {
		ret = of_alias_get_id(wdd->parent->of_node, "watchdog");
		if (ret >= 0)
			id = ida_simple_get(&watchdog_ida, ret,
					    ret + 1, GFP_KERNEL);
	}

	if (id < 0)
		id = ida_simple_get(&watchdog_ida, 0, MAX_DOGS, GFP_KERNEL);
```

⇒ **DTS 的 `aliases { watchdog0 = &wdt1; }` 能决定 `/dev/watchdogN` 里的 N**（06 篇）。
⚠️ **alias 指定的 id 已被占用时会静默回退到自动分配**（`:267` 的 `if (id < 0)`）
⇒ **"我在 DTS 里指定了 watchdog0 但它成了 watchdog2"是可能的，而且没有日志。**

### §A3.4 ⚠️ 第 ⑥⑦ 步的失败处理不对称

| notifier | 注册条件 | 失败时 |
|---|---|---|
| **reboot** | **`WDOG_STOP_ON_REBOOT` 置位**（`:301`） | **致命**：`pr_err` + 回滚 `watchdog_dev_unregister` + 释放 id + `return ret`（`:308-314`） |
| **restart** | **`wdd->ops->restart` 非空**（`:318`） | ⚠️ **只 `pr_warn`，注册继续**（`:322-324`） |
| **pm** | **`WDOG_NO_PING_ON_SUSPEND` 置位**（`:327`） | ⚠️ **只 `pr_warn`，注册继续**（`:331-333`） |

⇒ **意思是"停不下来的重启"被视为不可接受，而"重启不了机器"和"suspend 时还在喂狗"可以接受。**

⚠️ **还有一条只警告的路**：
```c
/* drivers/watchdog/watchdog_core.c:301-303 */
	if (test_bit(WDOG_STOP_ON_REBOOT, &wdd->status)) {
		if (!wdd->ops->stop)
			pr_warn("watchdog%d: stop_on_reboot not supported\n", wdd->id);
```
⇒ **要求"重启时停表"但驱动没有 `stop` ⇒ 只打一条警告，什么也不做。**

## §A4 `watchdog_dev_register()`：建字符设备（`watchdog_dev.c` 的 `watchdog_cdev_register`）

**关键步骤与行号**：

| 步 | 代码 | 位置 |
|---|---|---|
| ① | **分配 `watchdog_core_data` 并 `device_initialize`** | `drivers/watchdog/watchdog_dev.c` 的 `watchdog_cdev_register()` |
| ② | **`if (wdd->id == 0)`：存 `old_wd_data` + `misc_register`** | `:1037-1042` |
| ③ | **`cdev_init(&wd_data->cdev, &watchdog_fops)`** | `:1054` |
| ④ | **`wd_data->cdev.owner = wdd->ops->owner`** | `:1055` |
| ⑤ | **`cdev_device_add(&wd_data->cdev, &wd_data->dev)`** | `:1058` |

**misc 注册失败时的日志**（`:1042`，逐字）：
`%s: cannot register miscdev on minor=%d (err=%d).\n`
⇒ **这就是 §A3.2 那条重试路径的触发点。**

⚠️ **第 ④ 步很重要，它解释了一个看似的不对称**：
**`watchdog_open()` 里 `try_module_get(wdd->ops->owner)`（`:867`），
而 `watchdog_release()` 里 `module_put(wd_data->cdev.owner)`（`:964`）** ——
**两者取的是同一个模块**（因为 `:1055` 把它们赋成了同一个值）。
⇒ **不是 bug，只是写法不一致。**

## §A5 `watchdog_open()`：五步（`watchdog_dev.c:842-898`）

```c
	/* ① 两套字符设备在这里合流 */
	if (imajor(inode) == MISC_MAJOR)
		wd_data = old_wd_data;
	else
		wd_data = container_of(inode->i_cdev, struct watchdog_core_data,
				       cdev);

	/* ② 独占：一个看门狗只能有一个打开者 */
	if (test_and_set_bit(_WDOG_DEV_OPEN, &wd_data->status))
		return -EBUSY;

	wdd = wd_data->wdd;

	/* ③ 只在"硬件本来没在跑"时才拿模块引用 */
	hw_running = watchdog_hw_running(wdd);
	if (!hw_running && !try_module_get(wdd->ops->owner)) {
		err = -EBUSY;
		goto out_clear;
	}

	/* ④ 启动 */
	err = watchdog_start(wdd);
	if (err < 0)
		goto out_mod;

	file->private_data = wd_data;

	/* ⑤ 同样只在"硬件本来没在跑"时才拿设备引用 */
	if (!hw_running)
		get_device(&wd_data->dev);

	wd_data->open_deadline = KTIME_MAX;

	return stream_open(inode, file);
```

⚠️ **第 ③⑤ 步的 `if (!hw_running)` 是本函数最要紧的一处**：
**硬件已经在跑（`WDOG_HW_RUNNING` 置位）意味着模块引用和设备引用在注册时就已经拿过了**
（因为核心那时就开始替它喂狗了）⇒ **open 不能重复拿。**
⇒ **`watchdog_release()` 里的 `if (!running)`（`:963`）与它对称**（§A7）。

**两个细节**：
1. **`stream_open()`（`:891`）而不是 `nonseekable_open()`** ⇒ **文件位置无意义，且允许并发读写。**
2. **`open_deadline = KTIME_MAX`（`:888`）** ⇒ **用户态一接手，`WATCHDOG_OPEN_TIMEOUT` 就失效**（§B2）。

## §A6 喂狗：`watchdog_ping` → `__watchdog_ping`

**两层的分工**（`drivers/watchdog/watchdog_dev.c:143` 和 `:190`）：

| 层 | 做什么 |
|---|---|
| **`watchdog_ping(wdd)`（`:190`）** | **先判"该不该喂"**：**既不是 `WDOG_ACTIVE` 也不是 `WDOG_HW_RUNNING` 就直接返回** |
| **`__watchdog_ping(wdd)`（`:143`）** | **真喂**：**算距上次硬件喂狗的间隔 → 没到 `min_hw_heartbeat_ms` 就只记账不喂 → 否则 `ops->ping ?: ops->start`** |

**打点在这里**（07 篇）：
**`trace_watchdog_ping(wdd, err)`（`:164`）· `trace_watchdog_start(wdd, err)`（`:167`）** ——
⚠️ **注意 `__watchdog_ping` 里两个 tracepoint 二选一**：
**走 `ops->ping` 打 `watchdog_ping`，走 `ops->start` 打 `watchdog_start`。**
⇒ **所以 trace 里看到 `watchdog_start` 不一定是"启动"，可能只是一次喂狗**（07 §3）。

**`write()` 路径的三步**（`drivers/watchdog/watchdog_dev.c:676-714`，全函数只有 39 行）：

```c
	if (len == 0)
		return 0;			/* 写 0 字节不算喂狗 */

	/*
	 * Note: just in case someone wrote the magic character
	 * five months ago...
	 */
	clear_bit(_WDOG_ALLOW_RELEASE, &wd_data->status);	/* 每次先清零 */

	/* scan to see whether or not we got the magic character */
	for (i = 0; i != len; i++) {
		if (get_user(c, data + i))
			return -EFAULT;
		if (c == 'V')
			set_bit(_WDOG_ALLOW_RELEASE, &wd_data->status);
	}

	err = -ENODEV;
	mutex_lock(&wd_data->lock);
	wdd = wd_data->wdd;
	if (wdd)
		err = watchdog_ping(wdd);
	mutex_unlock(&wd_data->lock);
```

⚠️ **`_WDOG_ALLOW_RELEASE` 的语义是"最近这一次 write 里有没有 `'V'`"，不是"历史上写过没有"**
（`:691` 每次先无条件清零）——**源码注释把理由说得很清楚**（`:687-690`，逐字）：
> *"Note: just in case someone wrote the magic character five months ago..."*

⇒ **所以守护进程必须在**最后一次**写里带上 `'V'`，
早先写过一次不算**（05 §7）。
⚠️ **一次 write 里 `'V'` 出现在任何位置都算**（循环不 break，`:694-699`）。

⚠️ **`len == 0` 直接返回 0，不喂狗**（`:684-685`）
⇒ **`write(fd, buf, 0)` 是一次无效喂狗** ——**而且返回值 0 不是错误，容易被忽略。**

⚠️ **本函数里没有 `_WDOG_KEEPALIVE` 的置位** ——**它在 `watchdog_ping()` 一侧。**

## §A7 `watchdog_release()`：magic close 与 nowayout 的三层判定

```c
/* drivers/watchdog/watchdog_dev.c:920-968，节选 */
	int err = -EBUSY;			/* 初值就是"没停下来" */
	…
	/*
	 * We only stop the watchdog if we received the magic character
	 * or if WDIOF_MAGICCLOSE is not set. If nowayout was set then
	 * watchdog_stop will fail.
	 */
	if (!watchdog_active(wdd))
		err = 0;
	else if (test_and_clear_bit(_WDOG_ALLOW_RELEASE, &wd_data->status) ||
		 !(wdd->info->options & WDIOF_MAGICCLOSE))
		err = watchdog_stop(wdd);

	/* If the watchdog was not stopped, send a keepalive ping */
	if (err < 0) {
		pr_crit("watchdog%d: watchdog did not stop!\n", wdd->id);
		watchdog_ping(wdd);
	}

	watchdog_update_worker(wdd);
	clear_bit(_WDOG_DEV_OPEN, &wd_data->status);

done:
	running = wdd && watchdog_hw_running(wdd);
	mutex_unlock(&wd_data->lock);

	if (!running) {
		module_put(wd_data->cdev.owner);
		put_device(&wd_data->dev);
	}
```

**判定表（这是 watchdog 全部行为里最需要记牢的一张）**：

| 情形 | `err` | 结果 | 有日志吗 |
|---|---|---|---|
| **没启动过**（`!watchdog_active`） | 0 | **什么都不做** | 无 |
| **收到过 `'V'`** | **`watchdog_stop()` 的返回值** | **正常停表** | **成功时无日志** |
| **驱动不支持 magic close**（`!WDIOF_MAGICCLOSE`） | 同上 | **关闭即停表**（传统行为） | 同上 |
| ⚠️ **支持 magic close 但没收到 `'V'`** | **保持 `-EBUSY`** | **不停表 + 补喂一次 ⇒ 稍后硬件复位** | **`pr_crit "watchdog%d: watchdog did not stop!"`** |
| ⚠️ **`nowayout` 置位** | **`watchdog_stop()` 返回 `-EBUSY`** | **不停表** | **`pr_info "watchdog%d: nowayout prevents watchdog being stopped!"`**（`:298-299`）**+** 上面那条 `pr_crit` |

⚠️⚠️ **本篇最重要的一条诊断纪律**：
**`pr_crit "watchdog did not stop!"` 不一定是错误。**
**守护进程正常"故意不写 `'V'` 就退出"（让机器重启）时，这条 `pr_crit` 必然出现。**
⇒ **它的字面意思（"没停下来！"）会让人以为是故障，其实是设计好的行为**（07 §4 / 08 §4）。

**`watchdog_stop()` 里 nowayout 的拦截**（`drivers/watchdog/watchdog_dev.c:297-301`）：
```c
	if (test_bit(WDOG_NO_WAY_OUT, &wdd->status)) {
		pr_info("watchdog%d: nowayout prevents watchdog being stopped!\n",
			wdd->id);
		return -EBUSY;
	}
```
⇒ **三层优先级在代码里是这样实现的**（01 §7.5）：
**`nowayout` 在 `watchdog_stop()` 里拦（最内层）· magic close 在 `watchdog_release()` 里判（中层）·
"关闭即停"是默认（最外层）。**

## §A8 `watchdog_stop()` 的另一半：没有 `stop` 回调时

```c
/* drivers/watchdog/watchdog_dev.c:303-315 */
	if (wdd->ops->stop) {
		clear_bit(WDOG_HW_RUNNING, &wdd->status);
		err = wdd->ops->stop(wdd);
		trace_watchdog_stop(wdd, err);
	} else {
		set_bit(WDOG_HW_RUNNING, &wdd->status);	/* 反过来置位！ */
	}

	if (err == 0) {
		clear_bit(WDOG_ACTIVE, &wdd->status);
		watchdog_update_worker(wdd);
		watchdog_hrtimer_pretimeout_stop(wdd);
	}
```

⇒ **"停"一个停不下来的看门狗，效果是"置 `WDOG_HW_RUNNING`、清 `WDOG_ACTIVE`"**
⇒ **从用户态看是停了（`err == 0`），实际上硬件还在转，改由核心的工作队列喂狗。**
⇒ **这就是 02 §1.0.A 里 `(ACTIVE=0, HW_RUNNING=1)` 那种组合的第二个来源。**

**`watchdog_update_worker(wdd)`（`:313`）是关键的一步** ——
**它重新决定"内核要不要接着喂、多久喂一次"**（§B2）。

## §A9 注销（`watchdog_core.c:406`）

**`watchdog_unregister_device()` 的顺序是注册的严格倒序**：
**注销 pm/restart/reboot notifier → `watchdog_dev_unregister()` → 释放 id。**

⚠️ **`WDOG_STOP_ON_UNREGISTER` 的语义**（官方文档原话）：
> *"Note that this will only stop the watchdog if the nowayout flag is not set."*
⇒ **`nowayout` 压制它**（与 §A7 的第五行同一个机理）。

⚠️ **字符设备的寿命长于 `watchdog_device`**：
**注销时如果还有人打开着 `/dev/watchdogN`，`wd_data->dev` 的引用计数不为 0**
⇒ **`watchdog_core_data` 要等 `watchdog_core_data_release()`（`drivers/watchdog/watchdog_dev.c:900-907`）
才 `kfree`。**
⇒ **这时 `wd_data->wdd` 会被置 NULL** ——**`watchdog_release()` 开头的 `if (!wdd) goto done;`（`:930-931`）
就是为这种情况准备的。**

---

# Part B：横切关注点

## §B1 锁：一把 mutex 管一个设备

| 锁 | 类型 | 位置 | 保护 |
|---|---|---|---|
| **`wd_data->lock`** | **mutex** | `drivers/watchdog/watchdog_core.h:44` | **一切对这个看门狗的操作** |
| `wtd_deferred_reg_mutex` | mutex | `watchdog_core.c:63` | 延迟注册链表 |
| `pretimeout_lock` | **spinlock** | `watchdog_pretimeout.c:19` | pretimeout 设备链表（04 篇） |
| `governor_lock` | mutex | `watchdog_pretimeout.c:30` | governor 链表 |

**`wd_data->lock` 的契约写在每个内部函数的 kerneldoc 里**（逐字，如
`drivers/watchdog/watchdog_dev.c:243`）：
> *"The caller must hold wd_data->lock."*

⇒ **`watchdog_start` / `watchdog_stop` / `watchdog_ping` / `watchdog_get_status` /
`watchdog_set_timeout` 等一律要求调用者已持锁。**
**持锁的入口有三类**：**① `watchdog_fops` 的各回调；② sysfs 属性的 show/store；
③ 工作队列的 `watchdog_ping_work()`（`:222` 自己加锁）。**

⚠️ **`ops` 回调在 mutex 内被调用** ⇒ **驱动的回调可以睡眠**（I2C/SPI 看门狗需要这一点）。
⚠️ **但 `watchdog_notify_pretimeout()` 不走这把锁**（它可以在中断上下文调，04 篇）
⇒ **这是全框架唯一不受 `wd_data->lock` 保护的对外入口。**

## §B2 心跳工作队列：本框架的心脏

### §B2.1 三段结构

```
  hrtimer（wd_data->timer）
     │ 到期
     ▼
  watchdog_timer_expired()                drivers/watchdog/watchdog_dev.c:228-236
     └─ kthread_queue_work(watchdog_kworker, &wd_data->work)
        return HRTIMER_NORESTART           不自动重排！
     │
     ▼
  watchdog_ping_work()（在内核线程里）      :216-226
     ├─ mutex_lock(&wd_data->lock)
     ├─ watchdog_worker_should_ping(wd_data) ?  :203-214
     │     └─ __watchdog_ping(wd_data->wdd)     :143
     │           └─ watchdog_update_worker()  在这里重排 hrtimer（:173）
     └─ mutex_unlock
```

⚠️ **`HRTIMER_NORESTART`（`:235`）+ 在 `__watchdog_ping()` 里重排（`:173`）是关键设计**：
⇒ **"不再重排"就等于"不再喂狗"，硬件随后超时复位。**
⇒ **框架从不主动触发复位，它只是停止阻止**（02 §3 / 08 §3）。

⚠️ **为什么要绕一层内核线程**：**`ops->ping` 可能睡眠**（§B1）
⇒ **不能在 hrtimer 回调（原子上下文）里直接调。**

⚠️ **`HRTIMER_MODE_REL_HARD`（`:137`）** ——**"hard" 变体。**
**在 PREEMPT_RT 上它仍然在硬中断上下文执行**，**而不是被推迟到软中断线程。**
⇒ **这样 `kthread_queue_work` 的排队动作不会被 RT 的软中断线程化延迟。**
⚠️ **本文档未在 RT 内核上验证这条**（99 §5.3）。

### §B2.2 什么时候需要内核代喂：两个条件式

```c
/* drivers/watchdog/watchdog_dev.c:75-96 —— 源码注释就是最好的说明 */
static inline bool watchdog_need_worker(struct watchdog_device *wdd)
{
	unsigned int hm = wdd->max_hw_heartbeat_ms;
	unsigned int t = wdd->timeout * 1000;

	/*
	 * A worker to generate heartbeat requests is needed if all of the
	 * following conditions are true.
	 * - Userspace activated the watchdog.
	 * - The driver provided a value for the maximum hardware timeout, and
	 *   thus is aware that the framework supports generating heartbeat
	 *   requests.
	 * - Userspace requests a longer timeout than the hardware can handle.
	 *
	 * Alternatively, if userspace has not opened the watchdog
	 * device, we take care of feeding the watchdog if it is
	 * running.
	 */
	return (hm && watchdog_active(wdd) && t > hm) ||
		(t && !watchdog_active(wdd) && watchdog_hw_running(wdd));
}
```

| 条件式 | 场景 | 对应 02 §1.0.A 的组合 |
|---|---|---|
| **`hm && active && t > hm`** | **用户态在用，但它要的超时比硬件能撑的长** ⇒ **核心当"节拍转换器"** | `(1, 1)` |
| **`t && !active && hw_running`** | **用户态还没接手（或已放手），但硬件在转** ⇒ **核心当"临时看护"** | `(0, 1)` |

⇒ **两个条件式恰好对应 02 §1.0.D 和 §1.0.A 讲的两件事**，**它们在同一个函数里。**

### §B2.3 喂多快：`watchdog_next_keepalive()` 的四步算法

```c
/* drivers/watchdog/watchdog_dev.c:98-126 */
	if (watchdog_active(wdd))
		virt_timeout = ktime_add(wd_data->last_keepalive,
					 ms_to_ktime(timeout_ms));
	else
		virt_timeout = wd_data->open_deadline;

	hw_heartbeat_ms = min_not_zero(timeout_ms, wdd->max_hw_heartbeat_ms);
	keepalive_interval = ms_to_ktime(hw_heartbeat_ms / 2);

	/*
	 * To ensure that the watchdog times out wdd->timeout seconds
	 * after the most recent ping from userspace, the last
	 * worker ping has to come in hw_heartbeat_ms before this timeout.
	 */
	last_heartbeat = ktime_sub(virt_timeout, ms_to_ktime(hw_heartbeat_ms));
	latest_heartbeat = ktime_sub(last_heartbeat, ktime_get());
	if (ktime_before(latest_heartbeat, keepalive_interval))
		return latest_heartbeat;
	return keepalive_interval;
```

| 步 | 做什么 | 要点 |
|---|---|---|
| ① | **算"虚拟超时点"** | **用户态在用 ⇒ `last_keepalive + timeout`；不在用 ⇒ `open_deadline`** |
| ② | **`hw_heartbeat_ms = min_not_zero(timeout_ms, max_hw_heartbeat_ms)`** | **取两者里非零的较小值** |
| ③ | **`keepalive_interval = hw_heartbeat_ms / 2`** | **常规节拍是硬件心跳的一半** ——**留一倍余量** |
| ④ | **最后一次喂狗必须比虚拟超时点提前 `hw_heartbeat_ms`** | **源码注释就是这条规则的说明**；**返回两者的较小值** |

⇒ **第 ④ 步是"用户态超时要准"的保证**：
**核心保证在"用户态最后一次喂狗 + timeout"这个时刻之后不再喂**
⇒ **硬件在那之后的 ≤ `hw_heartbeat_ms` 内复位。**
⚠️ **所以实际复位时刻会比 `timeout` 晚最多一个 `hw_heartbeat_ms`**（精度代价，Part C §C4）。

### §B2.4 `open_deadline`：内核代喂的时限

```c
/* drivers/watchdog/watchdog_dev.c:69-73 */
static void watchdog_set_open_deadline(struct watchdog_core_data *data)
{
	data->open_deadline = open_timeout ?
		ktime_get() + ktime_set(open_timeout, 0) : KTIME_MAX;
}
```

| 事实 | 说明 |
|---|---|
| **`open_timeout` 来自 `CONFIG_WATCHDOG_OPEN_TIMEOUT`** | `drivers/watchdog/watchdog_dev.c:62`；**可用启动参数 `watchdog.open_timeout` 覆盖** |
| **0 表示无限** | **`KTIME_MAX`** ⇒ **默认（0）时内核会一直代喂** |
| **`open()` 时被清成 `KTIME_MAX`** | `:888` ⇒ **用户态一接手就不再有时限** |
| **判定函数** | `watchdog_past_open_deadline()`（`:64-67`）——**在 `watchdog_worker_should_ping()`（`:213`）里被用** |

⇒ **`WATCHDOG_OPEN_TIMEOUT` 的语义是"内核最多替用户态兜多久"**：
**设成 60 ⇒ 启动 60 秒内用户态没打开 `/dev/watchdog` ⇒ 内核放手 ⇒ 机器重启。**
**这是一个"防止用户态永远起不来"的保险**（08 §2）。

## §B3 三个 notifier

| notifier | 回调 | 位置 | 做什么 |
|---|---|---|---|
| **reboot** | `watchdog_reboot_notifier` | `watchdog_core.c:158-176` | **`SYS_DOWN`/`SYS_HALT` 时，若 active 或 hw_running 就调 `ops->stop`**；**失败返回 `NOTIFY_BAD`** |
| **restart** | `watchdog_restart_notifier` | `watchdog_core.c:178-191` | **直接调 `ops->restart(wdd, action, data)`** ——**三个参数**（01 §2） |
| **pm** | `watchdog_pm_notifier` | `watchdog_core.c:193-217` | **六个 PM 事件分两组**：`*_PREPARE` ⇒ `watchdog_dev_suspend()`；`POST_*` ⇒ `watchdog_dev_resume()` |

⚠️ **reboot notifier 里直接 `wdd->ops->stop(wdd)`（`:168`），不走 `watchdog_stop()` 包装**
⇒ **绕过了 nowayout 检查、不清 `WDOG_ACTIVE`、不更新 worker。**
**合理**（系统正在关机，状态维护无意义），**但它意味着 `nowayout` 拦不住 reboot 时的停表。**
**它仍然打 `trace_watchdog_stop(wdd, ret)`（`:169`）** ⇒ **trace 里能看到这次停表。**

⚠️ **pm notifier 只在 `WDOG_NO_PING_ON_SUSPEND` 置位时才注册**（§A3.4）
⇒ **默认情况下 suspend 期间内核工作队列照常喂狗。**
**官方 `watchdog-pm.rst` 的唯一一条规则是 resume 侧的**（原话）：
> *"On resume, a watchdog timer shall be reset to its selected value to give userspace enough time
> to resume."*

## §B4 引用计数三层

| 层 | 计数 | 何时 get | 何时 put |
|---|---|---|---|
| **① 模块引用** | **`wdd->ops->owner`**（与 `wd_data->cdev.owner` 同值，§A4） | **`open()` 且 `!hw_running`**（`:867`） | **`release()` 且 `!running`**（`:964`） |
| **② 设备引用** | **`wd_data->dev`** | **`open()` 且 `!hw_running`**（`:879`） | **`release()` 且 `!running`**（`:965`） |
| **③ id 占用** | **`watchdog_ida`** | **注册时** | **注销时** |

⚠️ **第 ①② 层的 `!hw_running` 条件是本框架最容易读漏的一处**：
**硬件已在跑意味着注册时就已经拿过这两个引用**（因为核心那时就开始代喂了）
⇒ **`open`/`release` 不能重复拿/放。**
⚠️ **注意 `release()` 用的是 `running = wdd && watchdog_hw_running(wdd)`（`:956`），
在**释放锁之后**判断（`:957` 才 unlock）** ——**`running` 的值是在锁内取的，正确。**

## §B5 4 个 tracepoint

**`include/trace/events/watchdog.h`（66 行）**：

| 事件 | 类 | 打点位置 |
|---|---|---|
| `watchdog_start` | `watchdog_template`（`:11`） | **`drivers/watchdog/watchdog_dev.c:167`（作为 ping 的替代）+ `:267`（真正的启动）** |
| `watchdog_ping` | 同上（`:34`） | `drivers/watchdog/watchdog_dev.c:164` |
| `watchdog_stop` | 同上（`:38`） | **`drivers/watchdog/watchdog_dev.c:306` + `watchdog_core.c:169`（reboot notifier）** |
| `watchdog_set_timeout` | **独立 `TRACE_EVENT`**（`:42`） | `drivers/watchdog/watchdog_dev.c:379` |

⚠️ **`watchdog_start` 有两个打点，语义不同**：
**`:167` 是"驱动没有 `ping`，用 `start` 代替喂狗"；`:267` 是"真的在启动"。**
⇒ **trace 里看到 `watchdog_start` 不能直接理解成"启动了"**（07 §3）。

**打点数口径**：**4 个事件 / 6 个打点位置。**

---

# Part C：评价与设计债

## §C1 源码自陈设计债：**0 条**

**重跑 `grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' drivers/watchdog/watchdog_*.c` 无输出。**

⚠️ **但官方文档自己承认了 ABI 层面的债**（`Documentation/watchdog/watchdog-api.rst`，逐字）：
> *"The Linux watchdog API is a rather ad-hoc construction and different drivers implement
> different, and sometimes incompatible, parts of it."*

⇒ **设计债在文档里，不在代码注释里**（01 §8.4）。

## §C2 ⚠️ 官方文档与代码的三处不一致（以代码为准）

| # | 文档说 | 代码是 | 后果 |
|---|---|---|---|
| **1** | **`watchdog-kernel-api.rst` 的 `watchdog_ops` 列表写 `int (*restart)(struct watchdog_device *);`（一个参数）** | **`int (*restart)(struct watchdog_device *, unsigned long, void *);`（三个参数）** ——`include/linux/watchdog.h:54` | **照文档写编译不过**（显式失败） |
| **2** | **同一份文档的 `watchdog_device` 字段列表里没有 `pm_nb`** | **`struct notifier_block pm_nb;`** ——`include/linux/watchdog.h:110` | **读文档会以为只有两个 notifier**（§B3 有三个） |
| **3** | **文档只讲了 3 个 `WDOG_*` 状态位**（`WDOG_ACTIVE` / `WDOG_NO_WAY_OUT` / `WDOG_HW_RUNNING`） | **代码有 6 个** ——`include/linux/watchdog.h:115-120` | **`WDOG_STOP_ON_REBOOT` / `WDOG_STOP_ON_UNREGISTER` / `WDOG_NO_PING_ON_SUSPEND` 三个只能从代码和 inline 名字倒推** |

**文档的"最后审阅日期"本身就是线索**：
**`watchdog-kernel-api.rst` 写着 `Last reviewed: 12-Feb-2013`，
`watchdog-api.rst` 写着 `Last reviewed: 10/05/2007`，后者还写着
"This document describes the state of the Linux 2.4.18 kernel."**
⇒ **用户态 ABI 文档描述的是 2.4 内核，但 ABI 本身没变（这正是它的价值）。**

## §C3 ⚠️ 四类静默失效

| # | 现象 | 机理 | 位置 |
|---|---|---|---|
| ⚠️ **1** | **驱动 probe 成功，但看门狗根本没注册上** | **走延迟注册路径时 `watchdog_register_device()` 一定返回 0，而 `watchdog_deferred_registration()` 丢掉了 `__watchdog_register_device()` 的返回值** | `drivers/watchdog/watchdog_core.c:355-359` + `:455` |
| ⚠️ **2** | **DTS 里指定了 `watchdog0` 别名，实际成了 `watchdog2`** | **alias 指定的 id 被占用时静默回退到自动分配** | `watchdog_core.c:260-268` |
| ⚠️ **3** | **`id` 变了，`/dev/watchdog` 没建出来** | **id 0 的 misc 注册撞了 `-EBUSY` ⇒ 框架退让到 1..31** | `watchdog_core.c:277-291`（§A3.2） |
| ⚠️ **4** | **设了 `WDIOF_SETTIMEOUT` 但硬件超时没变** | **驱动没实现 `set_timeout`，核心只改软字段**（01 §4.3） | **官方文档明确规定的行为** |

⚠️ **第 1 类是本子系统最严重的一个** ——**它让"probe 返回 0"这个最基本的成功信号失效。**
**可用的线索只有 `__watchdog_register_device()` 内部那几条 `pr_warn`/`pr_err`**
（而"注册失败"的总日志在外层，延迟路径走不到）。

## §C4 四个可以合理质疑的设计选择

| # | 设计 | 质疑点 | 位置 |
|---|---|---|---|
| **1** | **所有看门狗共用一个 `kthread_worker`** | **某个驱动的 `ops->ping` 阻塞会拖累别的看门狗** ——而看门狗恰恰是"最不能被拖"的设备 | `drivers/watchdog/watchdog_dev.c:57` |
| **2** | **延迟注册丢弃返回值** | §C3 第 1 类 | `watchdog_core.c:455` |
| **3** | ⚠️ **`pr_crit "watchdog did not stop!"` 在正常的 magic-close 流程里也会出现** | **`pr_crit` 是最高级别之一，用在一个"设计好的正常路径"上会误导运维** | `watchdog_dev.c:945-946`（§A7） |
| **4** | **实际复位时刻比 `timeout` 晚最多一个 `hw_heartbeat_ms`** | **精度损失是内核代喂机制的固有代价**；源码注释承认了这个取舍 | `watchdog_dev.c:117-121`（§B2.3） |

**一处值得称赞的地方**：
**`__watchdog_register_device()` 第 ② 步（`watchdog_core.c:248-249`）
把官方文档的两条规则用一行代码强制执行了** ——
**这在本项目做过的子系统里很少见**（对比 phy 的一堆"没实现就返回成功"）。

## §C5 整体评价

| 维度 | 评价 |
|---|---|
| **代码量** | **核心 2132 行**（占目录 3.6%）——**一到两天能读完** |
| **抽象层数** | **两层**（`watchdog_ops` + `watchdog_governor`） |
| **可读性** | **好** ——**每个内部函数都有 kerneldoc 写明锁契约；关键算法（`watchdog_need_worker` / `watchdog_next_keepalive`）都有大段注释解释意图** |
| **文档** | **1904 行，本项目最丰富**；⚠️ **但有三处过时**（§C2） |
| **可观测性** | **好**：**12 个 sysfs 属性 + 4 个 tracepoint + `WDIOC_GETBOOTSTATUS` 这个复位取证手段**（07 篇） |
| ⚠️ **错误处理的严格度** | **不均匀**：**注册期的能力校验很严（§A3.1），但延迟注册路径把返回值丢了（§C3 第 1 类）** |

**本子系统最特殊的一点**：
**框架会替用户态干活，而且有明确的交接协议。**
**`WDOG_ACTIVE` / `WDOG_HW_RUNNING` 两个状态位 + `last_keepalive` / `last_hw_keepalive`
两个时间戳 + `open_deadline` 一个截止时刻**，**五样东西共同描述"现在谁在负责喂狗"。**
⇒ **读 watchdog 的正确姿势是"先搞清这五样东西的含义"**（02 §1.0 / 08 §2），
**而不是先读 ioctl 的 switch。**

⚠️ **另一条**：**框架从不"触发"复位，它只是"停止阻止"** ——
**`HRTIMER_NORESTART` + 在喂狗成功后才重排（§B2.1）就是这个语义的实现。**
⇒ **所有"看门狗为什么没重启机器"的问题，都要从"谁还在喂"入手，而不是从"谁该触发"入手。**

