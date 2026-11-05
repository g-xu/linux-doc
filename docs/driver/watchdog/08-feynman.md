---
title: 费曼学习法
---

# watchdog 08：大白话解释

**本篇的目标不是完整，是"讲透最容易想错的那几处"。**
每个主题四段式：**问题 → 先举个例子 → 回到代码 → 这个类比什么时候会误导你。**
**类比后面一定跟源码行号** ——只讲故事不给依据的段落在本套文档里不算通过。

**八个主题**，**其中 §2 是本子系统最容易被类比错的那组关系。**

---

## §1 看门狗到底在防什么？为什么不能用普通定时器？

### 先举个例子

一座无人值守的水泵站，规定值班员每小时要去按一次墙上的按钮。
**如果一小时没人按，墙上那个装置就直接切断总电源、然后重新合闸。**

**为什么不让值班员"发现问题时自己去合闸"？**
**因为要防的恰恰是"值班员自己出了问题"** ——睡着了、心梗了、被困在门外了。
⇒ **能被"值班员的判断"关掉的保险，就防不了"值班员失去判断"这种故障。**

**所以那个装置必须满足三条**：
**① 它不是软件，是独立的硬件电路；② 它默认就会动手，除非被主动阻止；
③ 阻止它的动作要足够简单，简单到"还活着"就一定能做到。**

### 回到代码

**内核里对应的三条**：

| 条 | 代码上的体现 |
|---|---|
| **① 独立硬件** | **`struct watchdog_ops` 的 `start`/`ping` 最终都是往硬件寄存器里写**（05 §1.1） |
| **② 默认会动手** | **框架从不"触发"复位，只是"停止喂狗"**（§4） |
| **③ 阻止动作足够简单** | **`write(fd, "\0", 1)` 一个字节就够** ——**in-tree 样例只有 25 行**（`samples/watchdog/watchdog-simple.c`，05 §4.1） |

**官方 `watchdog-api.rst` 的开场就是这个意思**（原话）：
> *"A Watchdog Timer (WDT) is a hardware circuit that can reset the computer system in case of a
> software fault."*

**注意 "hardware circuit"** ——**这是整个子系统存在的前提。**

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"独立硬件"这句话在内核里有一个例外**：**软 pretimeout 是纯软件的**（§6）。
**它靠 hrtimer 实现（`drivers/watchdog/watchdog_hrtimer_pretimeout.c`，44 行）**
⇒ **系统卡到 hrtimer 都跑不了时它也不响** ——**恰恰是最需要它的时候。**

⚠️ **第二个误导更要紧**：**"按按钮的人"不一定是用户态。**
**内核自己有一个工作队列会替用户态按**（§3）——
**这在水泵站的类比里相当于"厂里派了个人，在正式值班员到岗前先替他按"。**
⇒ **所以"看门狗没重启机器"不等于"用户态活着"**（§4）。

---

## §2 ⚠️⚠️ 为什么有两个"在跑"？

### 先举个例子

回到水泵站。**"这个装置在运行吗"这句话，其实是两个不同的问题**：

**问题一：墙上那个装置的计时器在转吗？**
**问题二：现在有值班员在按按钮吗？**

**四种组合都真实存在**：

| 计时器在转 | 有值班员 | 现实场景 |
|---|---|---|
| ✘ | ✘ | **泵站刚建好，装置还没通电** |
| **✔** | **✘** | **装置通电了，但值班员还在路上** ——**厂里派了临时工在按** |
| ✔ | ✔ | **正常值班** |
| ✘ | ✔ | **短暂的交接瞬间** |

⇒ **一个布尔量装不下这四种状态，所以要两个。**

### 回到代码

| | **`WDOG_HW_RUNNING`（位 3）** | **`WDOG_ACTIVE`（位 0）** |
|---|---|---|
| 对应问题 | **计时器在转吗** | **有值班员吗** |
| 位置 | `include/linux/watchdog.h:118` | `include/linux/watchdog.h:115` |
| 谁设 | **驱动**（或核心在"没有 `stop`"时代设） | **核心**（用户态打开设备时） |
| 查询 | `watchdog_hw_running(wdd)`（`:137`） | `watchdog_active(wdd)`（`:128`） |
| **谁在按按钮** | **内核的工作队列** | **用户态** |

**官方文档对 `WDOG_HW_RUNNING` 的原话，恰好就是"临时工"这件事**：
> *"If set, the watchdog infrastructure will send keepalives to the watchdog hardware while
> WDOG_ACTIVE is not set."*

**代码里"派临时工"的判断是一行**：
```c
/* drivers/watchdog/watchdog_dev.c:94-95 —— watchdog_need_worker() 的第二个条件式 */
	return (hm && watchdog_active(wdd) && t > hm) ||
		(t && !watchdog_active(wdd) && watchdog_hw_running(wdd));
	/*      ↑ 没有值班员，但计时器在转 ⇒ 内核代按 */
```

**"计时器停不下来"这种硬件也走同一条路**：
```c
/* drivers/watchdog/watchdog_dev.c:303-309 —— watchdog_stop() 里 */
	if (wdd->ops->stop) {
		clear_bit(WDOG_HW_RUNNING, &wdd->status);
		err = wdd->ops->stop(wdd);
		…
	} else {
		set_bit(WDOG_HW_RUNNING, &wdd->status);	/* "停"变成了"改由内核代按" */
	}
```

### ⚠️ 这个类比什么时候会误导你？

⚠️⚠️ **"有没有值班员"这个问法会让你以为 sysfs 的 `state` 回答的是第一个问题。**
**它回答的是第二个**：

```c
/* drivers/watchdog/watchdog_dev.c:558-566 —— state_show() 全文 */
	if (watchdog_active(wdd))
		return sysfs_emit(buf, "active\n");

	return sysfs_emit(buf, "inactive\n");
```

⇒ **`state = inactive` 的准确含义是"没有值班员"，不是"装置停了"。**
⇒ **bootloader 开了表、内核在代喂的机器上，`state` 就是 `inactive`** ——
**而硬件明明在转**（07 §2.2）。

⚠️ **第二个误导**：**"临时工"不是无限期的。**
**`CONFIG_WATCHDOG_OPEN_TIMEOUT` 给了它一个下班时间**（§3）——
**默认 0 表示不下班，但配了值就会到点走人。**

---

## §3 为什么内核会替用户态喂狗？

### 先举个例子

还是那个墙上的装置。**它有个物理限制：最长只能撑 2 秒。**
**也就是说，必须每 2 秒内按一次，否则断电。**

**但值班员的作业规程写的是"每分钟巡查一次"** ——**每 2 秒跑去按一次是不现实的。**

**厂里的解法是加一个"中继员"**：
**中继员每 1 秒去按一次墙上的按钮**（满足硬件的 2 秒限制），
**同时自己拿个小本子记"值班员上次露面是什么时候"。**
**一旦值班员超过 60 秒没露面，中继员就停手不按了** ⇒ **2 秒后断电。**

⇒ **两层节拍被解耦了**：**硬件的 2 秒 + 值班员的 60 秒。**

### 回到代码

| 概念 | 代码 | 位置 |
|---|---|---|
| **硬件最长撑多久** | **`wdd->max_hw_heartbeat_ms`（毫秒）** | `include/linux/watchdog.h:107` |
| **用户态想要的超时** | **`wdd->timeout`（秒）** | `include/linux/watchdog.h:102` |
| **中继员** | **hrtimer + `kthread_worker`** | `drivers/watchdog/watchdog_core.h:48-49` |
| **"值班员上次露面"** | **`wd_data->last_keepalive`** | `drivers/watchdog/watchdog_core.h:45` |
| **"上次按按钮"** | **`wd_data->last_hw_keepalive`** | `drivers/watchdog/watchdog_core.h:46` |

**"每 1 秒按一次"这个 1 秒是算出来的**：
```c
/* drivers/watchdog/watchdog_dev.c:113-114 */
	hw_heartbeat_ms = min_not_zero(timeout_ms, wdd->max_hw_heartbeat_ms);
	keepalive_interval = ms_to_ktime(hw_heartbeat_ms / 2);
```
⇒ **节拍是硬件极限的一半**（2 秒的硬件 ⇒ 每 1 秒按一次），**留一倍余量。**

**"值班员超时就停手"这件事的实现方式很特别**：
```c
/* drivers/watchdog/watchdog_dev.c:117-124 —— 源码注释直接说明 */
	/*
	 * To ensure that the watchdog times out wdd->timeout seconds
	 * after the most recent ping from userspace, the last
	 * worker ping has to come in hw_heartbeat_ms before this timeout.
	 */
	last_heartbeat = ktime_sub(virt_timeout, ms_to_ktime(hw_heartbeat_ms));
```
⇒ **中继员算好"最后一次能按的时刻"，过了就不再排下一次定时器。**

**官方文档把这条规则写得很直白**（`max_hw_heartbeat_ms` 条目，原话）：
> *"the infrastructure will send heartbeats to the watchdog driver if 'timeout' is larger than
> max_hw_heartbeat_ms, unless WDOG_ACTIVE is set and userspace failed to send a heartbeat for at
> least 'timeout' seconds."*

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"中继员按得比硬件极限快，所以很安全"这个感觉是错的** ——
**它引入了一个新的精度问题。**

**中继员保证的是"值班员超时后我不再按"，而硬件是在最后一次按之后 2 秒才断电**
⇒ **实际断电时刻比"值班员超时 60 秒"最多晚一个 `max_hw_heartbeat_ms`**（03 §B2.3 / §C4 第 4 条）。
⇒ **要求"精确 60 秒复位"的场景不能依赖这个机制。**

⚠️ **第二个误导**：**"中继员"是全系统一个，不是每个装置一个。**
```c
/* drivers/watchdog/watchdog_dev.c:57 */
static struct kthread_worker *watchdog_kworker;
```
⇒ **所有看门狗共用一个内核线程** ⇒ **某个驱动的 `ops->ping` 阻塞会拖累别的看门狗**
（03 §C4 第 1 条）。

⚠️ **第三个误导**：**中继员的"下班时间"默认是没有的。**
**`CONFIG_WATCHDOG_OPEN_TIMEOUT` 默认 0 ⇒ `open_deadline = KTIME_MAX`**
（`drivers/watchdog/watchdog_dev.c:69-73`）
⇒ **默认配置下，用户态永远不来，内核也会永远替它喂** ——**看门狗形同废掉。**
**想让"用户态起不来也重启"，必须显式配这个值。**

---

## §4 为什么框架从不"触发"复位，只是"停止阻止"？

### 先举个例子

想象一块石头搁在斜坡上，下面是悬崖。
**有个人一直用手顶着它。** **他一松手，石头就滚下去。**

**现在问：怎么让石头滚下去？**
**答案不是"去推它"，而是"停止顶着"。**

⇒ **这就是看门狗框架的全部行为逻辑**：**它不做"触发复位"这个动作，
它只是决定"还要不要继续顶着"。**

### 回到代码

**这个语义在代码里的实现方式很干净**：

```c
/* drivers/watchdog/watchdog_dev.c:228-236 —— hrtimer 回调 */
static enum hrtimer_restart watchdog_timer_expired(struct hrtimer *timer)
{
	struct watchdog_core_data *wd_data;

	wd_data = container_of(timer, struct watchdog_core_data, timer);

	kthread_queue_work(watchdog_kworker, &wd_data->work);
	return HRTIMER_NORESTART;		/* 不自动重排 */
}
```

**重排发生在"喂狗成功之后"**：
```c
/* drivers/watchdog/watchdog_dev.c:173 —— __watchdog_ping() 的尾部 */
	watchdog_update_worker(wdd);
```

⇒ **一条链**：**定时器到期 → 排工作项 → 判断该不该喂 → 喂了才重排定时器。**
⇒ **"不该喂"的判断一成立，链条就自然停了** ——**不需要任何"触发"动作。**

**"该不该喂"就一个函数**：
```c
/* drivers/watchdog/watchdog_dev.c:203-214 */
static bool watchdog_worker_should_ping(struct watchdog_core_data *wd_data)
{
	struct watchdog_device *wdd = wd_data->wdd;

	if (!wdd)
		return false;

	if (watchdog_active(wdd))
		return true;

	return watchdog_hw_running(wdd) && !watchdog_past_open_deadline(wd_data);
}
```

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"松手石头就滚"暗示复位是立刻的，其实有延迟。**
**松手之后，硬件还要走完自己那一轮计时（最多 `max_hw_heartbeat_ms`）** ——
**这就是 §3 那个精度问题。**

⚠️ **第二个误导，也是本主题最有实用价值的一条**：
**既然复位是"停止阻止"的结果，那么"看门狗为什么没重启机器"这个问题，
必须从"谁还在顶着"入手，不能从"谁该触发"入手。**

**排查清单**（07 §5）：

| 可能还在顶着的 | 怎么查 |
|---|---|
| **用户态守护进程** | **`lsof /dev/watchdog*`** |
| **内核的工作队列**（最容易忘） | **tracepoint 看有没有周期性的 `watchdog_ping`，栈里有 `watchdog_ping_work` 就是内核在喂**（07 §3.4） |
| **`WATCHDOG_OPEN_TIMEOUT=0`（默认）** | **`zcat /proc/config.gz \| grep OPEN_TIMEOUT`** |

⚠️ **第三个误导**：**有一个地方框架确实"主动动手"了** ——
**`ops->restart`（05 §2.3）。**
**那是"用看门狗当复位手段"，是另一件事**（走 restart notifier，与喂狗机制无关，03 §B3）。

---

## §5 magic close：为什么要写一个 `'V'`？

### 先举个例子

值班员下班时要交接。**有两种下班方式**：

**方式一（正常交接）**：在交接本上签个字，然后关掉墙上的装置，走人。
**方式二（异常离场）**：**人不见了，交接本上什么也没有。**

⚠️ **问题是：从"装置"的角度，这两种情况都表现为"没人按按钮了"。**
**它区分不了"下班了"和"倒下了"。**

⇒ **所以要求一个"签字"动作**：**签了字才允许关装置；没签字就当他倒下了，
装置继续走，稍后断电重启。**

**那个"签字"就是往 `/dev/watchdog` 写一个字符 `'V'`。**

### 回到代码

**签字的记账**：
```c
/* drivers/watchdog/watchdog_dev.c:687-699 —— write() 里 */
	/*
	 * Note: just in case someone wrote the magic character
	 * five months ago...
	 */
	clear_bit(_WDOG_ALLOW_RELEASE, &wd_data->status);	/* 每次先撕掉旧签字 */

	/* scan to see whether or not we got the magic character */
	for (i = 0; i != len; i++) {
		if (get_user(c, data + i))
			return -EFAULT;
		if (c == 'V')
			set_bit(_WDOG_ALLOW_RELEASE, &wd_data->status);
	}
```

**源码注释把设计意图讲透了**（逐字）：*"just in case someone wrote the magic character
five months ago..."*
⇒ **签字只对"这一次交接"有效，五个月前签过的不算。**

**关装置时的判定**：
```c
/* drivers/watchdog/watchdog_dev.c:933-942 —— 源码注释就是规则本身 */
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
```

**三层优先级**（01 §7.5 / 03 §A7）：
**`nowayout`（最强，连签字也不认）> magic close（要签字）> 默认（关就停）。**

### ⚠️ 这个类比什么时候会误导你？

⚠️⚠️ **"签字"这个类比会让你以为"签过就一直有效"。**
**实际上每次 `write()` 开头都会先撕掉**（`:691` 的无条件 `clear_bit`）
⇒ **`'V'` 必须出现在**最后一次**写里**（05 §4.2 规则 1）。

**一个真实的错误写法**：
```c
	write(fd, "V", 1);		/* 签字 */
	write(fd, "\0", 1);		/* ⚠️ 又喂了一次狗 ⇒ 签字被撕掉！ */
	close(fd);			/* ⇒ 看门狗不会停 */
```

⚠️ **第二个误导**：**"没签字就当他倒下了"这个判断，会在 dmesg 里留下一条
看起来像故障的日志**：
```
watchdog0: watchdog did not stop!
```
**出处 `drivers/watchdog/watchdog_dev.c:946`，级别是 `pr_crit`。**
⇒ **这条消息在**正常的**magic close 流程里必然出现** ——
**它说的不是"我坏了"，是"我按规矩不停"**（07 §4.3）。

⚠️ **第三个误导**：**写 `'V'` 本身也是一次喂狗**（`:707` 无条件 `watchdog_ping()`）
⇒ **"签完字之后还有一个完整的 timeout 的宽限期"** ——**不是签完立刻就停。**

---

## §6 为什么软 pretimeout 是"尽力而为"的？

### 先举个例子

墙上的装置有个升级款：**断电前 10 秒会先响一声铃。**
**听到铃声还有 10 秒可以补救** ——比如赶紧记录现场、通知调度室。

**但便宜的老款装置没有铃。**
**省钱的办法是：在值班室放一个普通的厨房定时器，每次按按钮时把它拧到 50 秒。**
**它响了就相当于"还有 10 秒"。**

⚠️ **问题在于**：**厨房定时器是放在值班室里的。**
**如果值班室整个失火了（这恰恰是最需要报警的时候），那个定时器也烧了。**

### 回到代码

**"厨房定时器"就是 hrtimer**：
```c
/* drivers/watchdog/watchdog_hrtimer_pretimeout.c:30-39 —— 全文核心 */
void watchdog_hrtimer_pretimeout_start(struct watchdog_device *wdd)
{
	if (!(wdd->info->options & WDIOF_PRETIMEOUT) &&
	    !watchdog_pretimeout_invalid(wdd, wdd->pretimeout))
		hrtimer_start(&wdd->wd_data->pretimeout_timer,
			      ktime_set(wdd->timeout - wdd->pretimeout, 0),
			      HRTIMER_MODE_REL);
	else
		hrtimer_cancel(&wdd->wd_data->pretimeout_timer);
}
```

**`timeout - pretimeout` 就是"拧到 50 秒"**（`timeout=60, pretimeout=10`）。

**第一个条件是取反的：`!(wdd->info->options & WDIOF_PRETIMEOUT)`**
⇒ **只在硬件**没有**铃的时候才用厨房定时器** ——**两者互斥，不会重复报警。**

**Kconfig 的 help 自己把局限说清楚了**（原话）：
> *"Be aware that because this pretimeout functionality uses hrtimers, it may not be able to fire
> before the actual watchdog fires in some situations."*

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"厨房定时器烧了"这个画面暗示只有极端情况才失效，其实门槛更低。**

**两个 hrtimer 的模式不一样**（04 §3.3）：

| 定时器 | 模式 | 位置 |
|---|---|---|
| **喂狗节拍** | **`HRTIMER_MODE_REL_HARD`**（硬中断上下文） | `drivers/watchdog/watchdog_dev.c:137` |
| **软 pretimeout** | **`HRTIMER_MODE_REL`**（普通） | `drivers/watchdog/watchdog_hrtimer_pretimeout.c:26` / `:36` |

⇒ **在 PREEMPT_RT 上，普通 hrtimer 会被推迟到软中断线程** ——
**软中断线程被高优先级任务饿死时，铃就不响了**（本文档未在 RT 上验证，99 §5.3）。

⚠️ **第二个误导，而且是可观测的**：
**开了 `CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT` 之后，"有没有铃"这件事在三个出口说法不同**
（04 §3.4）：

| 出口 | 说法 | 为什么 |
|---|---|---|
| ⚠️ **`WDIOC_GETSUPPORT`** | **说没有** | **它把 `wdd->info` 原样返回，核心不改**（`drivers/watchdog/watchdog_dev.c:752-753`） |
| **`WDIOC_GETSTATUS`** | **说有** | **`if (IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT)) status \|= WDIOF_PRETIMEOUT;`**（`:351-352`） |
| **sysfs 的 `pretimeout`** | **属性存在** | **`watchdog_have_pretimeout()` 的第二个条件是编译期常量**（`drivers/watchdog/watchdog_core.h:69-70`） |

⇒ **判据：想问"硬件到底有没有铃"，只能看 `WDIOC_GETSUPPORT`。**

---

## §7 为什么 `nowayout` 是一道单向门？

### 先举个例子

有些场合的规定是："这个保险一旦启用，任何人都不能再关掉它。"
**典型的例子是电梯的超载保护、化工反应釜的紧急泄压。**

**为什么要单向？** **因为"能关掉"这件事本身就是一个攻击面 / 误操作面。**
**只要存在关闭的途径，就一定会有人在错误的时刻用它。**

⇒ **单向门的价值不在于"更安全一点"，在于"消除了一整类失误"。**

### 回到代码

**三处一致地实现了这个单向性**：

| 路径 | 代码 | 位置 |
|---|---|---|
| **sysfs 写 0** | **`if (test_bit(WDOG_NO_WAY_OUT, &wdd->status) && !value) return -EPERM;`** ——**源码注释：*"nowayout cannot be disabled once set"*** | `drivers/watchdog/watchdog_dev.c:463-465` |
| **停表** | **`if (test_bit(WDOG_NO_WAY_OUT, &wdd->status)) { pr_info(…); return -EBUSY; }`** | `drivers/watchdog/watchdog_dev.c:297-301` |
| **magic close** | **官方文档原话：*"The nowayout feature will overrule the magic close feature."*** | **实现在上一行那个 `-EBUSY`**（03 §A7） |

**注销时也一样**（官方文档 `watchdog_stop_on_unregister` 条，原话）：
> *"Note that this will only stop the watchdog if the nowayout flag is not set."*

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"任何人都不能关掉"有一个例外：reboot notifier。**

```c
/* drivers/watchdog/watchdog_core.c:164-172 —— reboot notifier 里 */
	if (code == SYS_DOWN || code == SYS_HALT) {
		if (watchdog_active(wdd) || watchdog_hw_running(wdd)) {
			int ret;

			ret = wdd->ops->stop(wdd);	/* 直接调 ops，不走 watchdog_stop() */
			…
		}
	}
```

⇒ **它绕过了 `watchdog_stop()` 那层包装，所以也绕过了 `nowayout` 检查**（03 §B3）。
**这是合理的**（系统正在正常关机，不该被看门狗打断），
**但"nowayout 意味着绝对停不下来"这句话严格说是不成立的。**

⚠️ **第二个误导**：**`nowayout` 有三个来源，而不是一个。**

| 来源 | 时机 |
|---|---|
| **`CONFIG_WATCHDOG_NOWAYOUT`** | **编译期** ——**通过 `WATCHDOG_NOWAYOUT` 宏（`include/linux/watchdog.h:124`）传给驱动** |
| **驱动的 `nowayout` 模块参数** | **加载期** |
| **sysfs 的 `nowayout` 属性** | **运行期（只能设，不能清）** |

⇒ **`cat /sys/class/watchdog/watchdogN/nowayout` 读到 1，无法区分是哪个来源设的。**

---

## §8 为什么 `/dev/watchdog` 和 `/dev/watchdog0` 是同一个东西？

### 先举个例子

一家公司搬了新办公楼。**新楼给每个部门都编了号：`1 号会议室`、`2 号会议室`……**
**但老员工二十年来一直管一楼那间叫"大会议室"。**

**公司的做法不是强行改名，而是在门口挂**两块**牌子**：
**"大会议室"和"1 号会议室"，指的是同一个房间。**

**代价是：有人按"大会议室"预定、有人按"1 号会议室"预定，会撞车。**

### 回到代码

**两块牌子**：

| 牌子 | 注册方式 | 位置 |
|---|---|---|
| **`/dev/watchdog`（老名字）** | **`misc_register(&watchdog_miscdev)`** | `drivers/watchdog/watchdog_dev.c:1040` |
| **`/dev/watchdogN`（新名字）** | **`cdev_device_add()`** | `drivers/watchdog/watchdog_dev.c:1058` |

**只有 `id == 0` 那个挂两块牌**：
```c
/* drivers/watchdog/watchdog_dev.c:1037-1040 */
	if (wdd->id == 0) {
		old_wd_data = wd_data;
		watchdog_miscdev.parent = wdd->parent;
		err = misc_register(&watchdog_miscdev);
```

**源码注释就是这段设计的说明**（`:995-997`，逐字）：
> *"Register a watchdog character device including handling the legacy /dev/watchdog node.
> /dev/watchdog is actually a miscdevice and thus we set it up like that."*

**两块牌子怎么找到同一个房间**：
```c
/* drivers/watchdog/watchdog_dev.c:850-854 —— open() 的第一步 */
	if (imajor(inode) == MISC_MAJOR)
		wd_data = old_wd_data;			/* 老牌子走全局变量 */
	else
		wd_data = container_of(inode->i_cdev, struct watchdog_core_data,
				       cdev);		/* 新牌子走 cdev */
```

**"撞车"的处理**：
```c
/* drivers/watchdog/watchdog_dev.c:857-858 */
	if (test_and_set_bit(_WDOG_DEV_OPEN, &wd_data->status))
		return -EBUSY;
```
⇒ **两个入口共享同一个 `_WDOG_DEV_OPEN` 位** ⇒ **第二个打开者必然拿到 `-EBUSY`。**

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"公司总能挂上两块牌"是错的** ——**老牌子可能已经被别人占了。**

```c
/* drivers/watchdog/watchdog_core.c:277-284 —— 挂不上老牌子时的退让 */
		if (!(id == 0 && ret == -EBUSY))
			return ret;

		/* 放弃 id 0，改用 1..31 */
		id = ida_simple_get(&watchdog_ida, 1, MAX_DOGS, GFP_KERNEL);
```

**占用者通常是一个老式（未迁移到 core 的）看门狗驱动** ——
**核心还专门给了一条提示日志**（`drivers/watchdog/watchdog_dev.c:1045`，逐字）：
```
%s: a legacy watchdog module is probably present.
```
⇒ **所以"第一个注册的看门狗一定是 `watchdog0`"这句话不成立**（03 §A3.2 / 07 §5.2）。

⚠️ **第二个误导**：**`/dev/watchdog` 不是"所有看门狗的总入口"** ——
**它只是 `id == 0` 那一个。**
**系统有三个看门狗时，`/dev/watchdog` 只能操作其中一个**（02 §1.0.C）。

⚠️ **第三个误导**：**两块牌子的主设备号不同，但这不代表是两个设备。**
**`ls -l /dev/watchdog /dev/watchdog0` 会看到不同的主设备号（10 vs 动态）** ——
**它们指向同一个 `struct watchdog_core_data`**（07 §5.3）。

---

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 复习整个子系统 | `00-overview.md` |
| API 完整清单（含 11 个 ioctl） | `01-api.md` |
| 结构体与六组易错关系 | `02-datastructures.md` |
| 主流程 / 心跳算法 / 设计债 | `03-internals.md` |
| pretimeout governor 与软 pretimeout | `04-extensions.md` |
| **驱动骨架 + 用户态骨架 + 七组近邻符号** | `05-usage.md` |
| DT 属性与失效决策树 | `06-devicetree.md` |
| **12 个 sysfs 属性 + 4 个 tracepoint + 速查** | `07-debug.md` |
| **哪些结论不能直接信** | `99-evidence.md` §5 |
