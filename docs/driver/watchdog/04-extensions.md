---
title: 扩展机制
---

# watchdog 04：子框架

## §0 组织轴

**本篇一节一个独立子框架，互不调用。**
**watchdog 只有两个真正的子框架，而且它们服务同一件事：pretimeout（提前通知）。**

### §0.1 分诊表：哪些真会碰到

| # | 子框架 | 是什么 | 规模 | Kconfig | arm64 是否相关 | 本篇 |
|---|---|---|---|---|---|---|
| 1 | **pretimeout governor 框架** | **"快超时了"这个事件的处理策略，可插拔** | **`watchdog_pretimeout.c` 216 行 + `watchdog_pretimeout.h` 61 行** | `WATCHDOG_PRETIMEOUT_GOV` | **相关**（服务器/工控场景） | §1 |
| 2 | **两个内置 governor** | **`noop`（打日志）· `panic`（直接 panic）** | **各 42 行** | `..._GOV_NOOP` / `..._GOV_PANIC`（都是 `tristate`） | **相关** | §2 |
| 3 | **hrtimer 软 pretimeout** | **给硬件不支持 pretimeout 的看门狗补一个软的** | **`watchdog_hrtimer_pretimeout.c` 44 行** | `WATCHDOG_HRTIMER_PRETIMEOUT` | **弱相关**（硬件支持的话不需要） | §3 |
| 4 | **两套字符设备** | **`/dev/watchdog`（misc）与 `/dev/watchdogN`（cdev）** | — | 无独立开关 | **总是** | **不在本篇** ——**它是核心的一部分**（02 §1.0.C / 03 §A4） |

⚠️ **第 4 行要显式排除**：**两套字符设备不是"子框架"，是核心自身的两个入口**
⇒ **不要在本篇找它。**

### §0.2 三个子框架挂在 03 时间线的哪一点

| 子框架 | 挂载点 |
|---|---|
| **governor 框架** | **注册期**：`watchdog_register_pretimeout(wdd)`（`drivers/watchdog/watchdog_pretimeout.c:177`）在 `watchdog_dev_register()` 里被调 |
| **内置 governor** | **自己的 `module_init`** ——**与看门狗设备的注册完全解耦**（§2.3） |
| **软 pretimeout** | **`watchdog_start()` 里 arm、`watchdog_stop()` 里 cancel**（`drivers/watchdog/watchdog_dev.c:314` 的 `watchdog_hrtimer_pretimeout_stop`，03 §A8） |

---

## §1 pretimeout governor 框架（216 行）

### §1.1 它解决什么问题

**有些看门狗硬件在"真正复位"之前会先发一个中断**：
**"再 N 秒你还不喂我就复位了"。**

**这个中断能做的事很多，而且取决于系统定位**：
**① 什么都不做，只记一条日志（保留现场，等复位）；
② 立刻 panic（这样能拿到内核栈和 crash dump）；
③ 触发用户态的告警；④ 尝试保存关键数据。**

⇒ **"做什么"是策略，"什么时候通知"是机制** ⇒ **所以框架把策略做成可插拔的 governor。**

### §1.2 两个链表 + 两个包装结构

```c
/* drivers/watchdog/watchdog_pretimeout.c:24-38 —— 两个纯内部的包装结构 */
struct watchdog_pretimeout {
	struct watchdog_device		*wdd;
	struct list_head		entry;
};
…
struct governor_priv {
	struct watchdog_governor	*gov;
	struct list_head		entry;
};
```

| 链表 | 装什么 | 保护它的锁 | 位置 |
|---|---|---|---|
| `pretimeout_list` | **所有支持 pretimeout 的看门狗设备**（包在 `watchdog_pretimeout` 里） | **`pretimeout_lock`（spinlock）** | `drivers/watchdog/watchdog_pretimeout.c:22` |
| `governor_list` | **所有已注册的 governor**（包在 `governor_priv` 里） | **`governor_lock`（mutex）** | `drivers/watchdog/watchdog_pretimeout.c:33` |

⚠️ **为什么要包一层**：**`struct watchdog_device` 和 `struct watchdog_governor` 都没有
`list_head` 字段** ⇒ **框架不能直接把它们挂链表，只能包一层。**
⇒ **代价是每个设备/每个 governor 多一次 `kzalloc`**（`:120` / `:184`）。

### §1.3 两把锁的分工（源码注释直接说明）

```c
/* drivers/watchdog/watchdog_pretimeout.c:18 —— 注释逐字 */
/* The spinlock protects default_gov, wdd->gov and pretimeout_list */
```

| 锁 | 类型 | 保护 | 为什么是这个类型 |
|---|---|---|---|
| **`pretimeout_lock`** | **spinlock**（`:19`） | **`default_gov` · `wdd->gov` · `pretimeout_list`** | **`watchdog_notify_pretimeout()` 可以在中断上下文调**（01 §6） |
| **`governor_lock`** | **mutex**（`:30`） | **`governor_list`** | **只在注册/注销/查询 governor 时用，允许睡眠**（里面有 `kzalloc`） |

**两把锁的嵌套顺序是固定的**：**先 `governor_lock`（mutex），再 `pretimeout_lock`（spinlock）**
（见 `:124` → `:137` 和 `:157` → `:167`）
⇒ **反过来会死锁（spinlock 内不能拿 mutex）。**

### §1.4 ⚠️`watchdog_notify_pretimeout()`：在 spinlock 内调用 governor

```c
/* drivers/watchdog/watchdog_pretimeout.c:100-112 —— 全文 */
void watchdog_notify_pretimeout(struct watchdog_device *wdd)
{
	unsigned long flags;

	spin_lock_irqsave(&pretimeout_lock, flags);
	if (!wdd->gov) {
		spin_unlock_irqrestore(&pretimeout_lock, flags);
		return;
	}

	wdd->gov->pretimeout(wdd);		/* 在锁内、关中断的情况下调 */
	spin_unlock_irqrestore(&pretimeout_lock, flags);
}
```

⚠️⚠️ **三个必须点明的约束**：
1. **governor 的 `pretimeout` 回调在 `spin_lock_irqsave` 内执行** ⇒ **它不能睡眠。**
2. **`panic` governor 的 `panic()` 就是在这个上下文里调的** ——
   **持着一把 spinlock、关着中断进入 panic**（§2.2）。
3. **`wdd->gov` 为 NULL 时静默返回**（`:105-108`）⇒ **没有日志。**

⚠️ **`struct watchdog_governor.pretimeout` 是 `void` 返回**（`watchdog_pretimeout.h:11`）
⇒ **governor 无法拒绝、也无法报错。**

### §1.5 默认 governor 的选取：字符串比较

```c
/* drivers/watchdog/watchdog_pretimeout.c:135-144 —— 注册 governor 时 */
	if (!strncmp(gov->name, WATCHDOG_PRETIMEOUT_DEFAULT_GOV,
		     WATCHDOG_GOV_NAME_MAXLEN)) {
		spin_lock_irq(&pretimeout_lock);
		default_gov = gov;

		list_for_each_entry(p, &pretimeout_list, entry)
			if (!p->wdd->gov)
				p->wdd->gov = default_gov;
		spin_unlock_irq(&pretimeout_lock);
	}
```

**`WATCHDOG_PRETIMEOUT_DEFAULT_GOV` 是一个编译期字符串**
（`drivers/watchdog/watchdog_pretimeout.h:27-31`）：

| Kconfig | 宏值 |
|---|---|
| `WATCHDOG_PRETIMEOUT_DEFAULT_GOV_NOOP` | **`"noop"`** |
| `WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC` | **`"panic"`**（Kconfig 的 `choice` 默认选这个） |

⇒ **谁是默认 governor 由编译期决定，运行期靠名字匹配认领。**
⇒ **一个后果：默认 governor 的模块没加载时，设备的 `wdd->gov` 是 NULL
⇒ pretimeout 事件被静默丢弃**（§1.4 第 3 点）。

⚠️ **`#endif` 之后没有 `#else` 分支**（`watchdog_pretimeout.h:31`）
⇒ **两个 `DEFAULT_GOV_*` 都没选时，`WATCHDOG_PRETIMEOUT_DEFAULT_GOV` 根本没定义**
⇒ **`:135` 的 `strncmp` 会编译不过。**
**Kconfig 的 `choice` 保证了至少选一个，所以这条路走不到** ——
**但它是一个"靠 Kconfig 保证的编译期前提"，不是代码里的防御。**

### §1.6 三个 sysfs 出口

**framework 提供三个函数给 `watchdog_dev.c` 的 sysfs 属性用**（`watchdog_pretimeout.h:22-25`）：

| 函数 | 位置 | 对应 sysfs 属性 |
|---|---|---|
| `watchdog_pretimeout_available_governors_get(buf)` | `drivers/watchdog/watchdog_pretimeout.c:51` | **`pretimeout_available_governors`**（只读，07 篇） |
| `watchdog_pretimeout_governor_get(wdd, buf)` | `:66` | **`pretimeout_governor`（读）** |
| `watchdog_pretimeout_governor_set(wdd, buf)` | `:78` | **`pretimeout_governor`（写）** |

**写入时用 `sysfs_streq()` 比较**（`:45` 的 `find_governor_by_name`）
⇒ **`echo panic > pretimeout_governor` 里的换行符会被容忍。**
**找不到名字返回 `-EINVAL`**（`:88`）。

⚠️ **`CONFIG_WATCHDOG_PRETIMEOUT_GOV=n` 时这三个函数是空桩，全部返回 `-EINVAL`**
（`watchdog_pretimeout.h:43-58`）
⇒ **而 `watchdog_register_pretimeout()` 的空桩返回 0**（`:34-37`）——**不影响注册。**

### §1.7 ⚠️ 一处本文档发现的可疑之处（未验证）

⚠️ **`watchdog_unregister_governor()` 不清 `default_gov`**：

```c
/* drivers/watchdog/watchdog_pretimeout.c:152-175 —— 节选 */
void watchdog_unregister_governor(struct watchdog_governor *gov)
{
	…
	list_for_each_entry_safe(priv, t, &governor_list, entry) {
		if (priv->gov == gov) {
			list_del(&priv->entry);
			kfree(priv);
			break;
		}
	}

	spin_lock_irq(&pretimeout_lock);
	list_for_each_entry(p, &pretimeout_list, entry)
		if (p->wdd->gov == gov)
			p->wdd->gov = default_gov;	/* default_gov 可能就是 gov 自己 */
	spin_unlock_irq(&pretimeout_lock);
	…
}
```

**`default_gov` 在全文只有四处出现**（重跑 `grep -n 'default_gov' drivers/watchdog/watchdog_pretimeout.c`
⇒ `:16` 定义 · `:138` 赋值 · `:142` `:170` `:191` 读取）——
**没有任何一处把它清成 NULL。**

⇒ ⚠️ **推论**：**如果默认 governor（默认配置下是 `panic`）编成模块并被 `rmmod`，
`default_gov` 仍指向那个模块里的 `static struct watchdog_governor watchdog_gov_panic`
（`drivers/watchdog/pretimeout_panic.c:23`）** ⇒ **`:170` 会把设备的 `gov` 设成一个悬垂指针**
⇒ **下一次 pretimeout 事件在 `:110` 解引用它。**

⚠️⚠️ **这是本文档的推断，不是观察到的现象**（99 §5.3）：
**本文档没有编译、没有运行、没有查上游是否已修，也没有确认
`WATCHDOG_PRETIMEOUT_GOV_SEL` 那个 `tristate default m` 的实际取值组合能否触发。**
⇒ **写在这里的目的是"提醒读者这条路径值得自己核一遍"**，
**不是断言这是一个 bug。**

---

## §2 两个内置 governor（各 42 行）

### §2.1 `noop`：只打一条日志

```c
/* drivers/watchdog/pretimeout_noop.c:18-25 —— 核心就这几行 */
static void pretimeout_noop(struct watchdog_device *wdd)
{
	pr_alert("watchdog%d: pretimeout event\n", wdd->id);
}

static struct watchdog_governor watchdog_gov_noop = {
	.name		= "noop",
	.pretimeout	= pretimeout_noop,
};
```

**日志级别是 `pr_alert`**（比 `pr_crit` 高一级）——
**消息逐字是 `watchdog%d: pretimeout event`。**
⇒ **选 `noop` 的意义是"保留现场，让硬件按原计划复位"**（07 §4）。

### §2.2 `panic`：直接 panic

```c
/* drivers/watchdog/pretimeout_panic.c:18-26 —— 全文核心 */
static void pretimeout_panic(struct watchdog_device *wdd)
{
	panic("watchdog pretimeout event\n");
}

static struct watchdog_governor watchdog_gov_panic = {
	.name		= "panic",
	.pretimeout	= pretimeout_panic,
};
```

⚠️ **它是默认 governor**（`WATCHDOG_PRETIMEOUT_DEFAULT_GOV_PANIC` 是 Kconfig 的 `choice` 默认）。
⇒ **选它的意义是"用 panic 换一份内核栈"**：
**panic 会打印栈、可以配合 kdump 拿到 vmcore** ⇒ **比"硬复位后什么都没有"强得多。**

⚠️ **`panic()` 是在 `spin_lock_irqsave` 内、关中断的情况下被调的**（§1.4）
⇒ **本文档未验证这在所有平台上都能正常产出 crash dump**（99 §5.3）。

⚠️ **`panic()` 的消息里没有 `wdd->id`** ——**多个看门狗时看不出是哪一个触发的。**
**对比 `noop` 的消息带了 `watchdog%d`。**

### §2.3 两个 governor 都是独立模块

| 事实 | 说明 |
|---|---|
| **各自有 `module_init` / `module_exit`** | `pretimeout_panic.c:37-38` · `pretimeout_noop.c` 同构 |
| **Kconfig 是 `tristate`** | **`obj-$(CONFIG_WATCHDOG_PRETIMEOUT_GOV_NOOP) += pretimeout_noop.o`**（`drivers/watchdog/Makefile:14-15`） |
| **默认值跟随 `WATCHDOG_CORE`** | **`default WATCHDOG_CORE`** ⇒ **核心编成模块时它们也是模块** |
| **与看门狗设备完全解耦** | **注册顺序任意** ——**governor 后注册时会认领已存在的、`gov == NULL` 的设备**（§1.5 的 `:140-142`） |

### §2.4 ⚠️`noop` 的 `MODULE_DESCRIPTION` 是复制粘贴错误

**两个文件的第 41 行完全一样**（重跑
`grep -n 'MODULE_DESCRIPTION' drivers/watchdog/pretimeout_noop.c drivers/watchdog/pretimeout_panic.c`）：

| 文件 | 第 41 行 |
|---|---|
| `drivers/watchdog/pretimeout_panic.c:41` | `MODULE_DESCRIPTION("Panic watchdog pretimeout governor");` ✔ |
| ⚠️`drivers/watchdog/pretimeout_noop.c:41` | ⚠️ **`MODULE_DESCRIPTION("Panic watchdog pretimeout governor");`** ✘ |

⇒ **`modinfo pretimeout_noop` 会说自己是 "Panic … governor"。**
**无功能影响**，**但它是"这块代码维护关注度不高"的一个客观旁证**
（和官方文档三处过时是同一类信号，03 §C2）。

---

## §3 hrtimer 软 pretimeout（44 行）

### §3.1 它解决什么问题

**绝大多数 SoC 内置看门狗只有"超时就复位"，没有"提前中断"。**
⇒ **那就用一个软定时器在"复位前 `pretimeout` 秒"自己发通知。**

**Kconfig 的 help 原话已经把局限说清楚了**：
> *"Be aware that because this pretimeout functionality uses hrtimers, it may not be able to fire
> before the actual watchdog fires in some situations."*

⇒ **软 pretimeout 是"尽力而为"的**：**系统卡死到连 hrtimer 都跑不了时，它也不会响。**
⚠️ **而这恰恰是看门狗最需要工作的场景** ⇒ **这是一个诚实但有限的补偿手段**（08 §5）。

### §3.2 全文只有三个函数，逐个看

```c
/* drivers/watchdog/watchdog_hrtimer_pretimeout.c:12-20 —— 回调 */
static enum hrtimer_restart watchdog_hrtimer_pretimeout(struct hrtimer *timer)
{
	struct watchdog_core_data *wd_data;

	wd_data = container_of(timer, struct watchdog_core_data, pretimeout_timer);

	watchdog_notify_pretimeout(wd_data->wdd);
	return HRTIMER_NORESTART;
}
```

```c
/* :22-28 —— 初始化 */
void watchdog_hrtimer_pretimeout_init(struct watchdog_device *wdd)
{
	struct watchdog_core_data *wd_data = wdd->wd_data;

	hrtimer_init(&wd_data->pretimeout_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	wd_data->pretimeout_timer.function = watchdog_hrtimer_pretimeout;
}
```

```c
/* :30-39 —— 最关键的一段：arm 的条件 */
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

⚠️⚠️ **arm 条件的第一项是"取反"的**：`!(wdd->info->options & WDIOF_PRETIMEOUT)`
⇒ **只在硬件**不**支持 pretimeout 时才启用软定时器。**
⇒ **硬件支持就用硬件的，两者互斥、不会重复通知。**

**第二项 `!watchdog_pretimeout_invalid(wdd, wdd->pretimeout)`** ——
⚠️ **注意 `*_invalid()` 的语义是反的**（01 §5）⇒ **这里是"pretimeout 值合法"。**

**定时长度 = `timeout - pretimeout` 秒**（`:35`）
⇒ **例：`timeout=60, pretimeout=10` ⇒ 喂狗后 50 秒发通知。**

### §3.3 ⚠️ 两个 hrtimer 的模式不同（容易混）

| hrtimer | 字段 | 模式 | 位置 |
|---|---|---|---|
| **喂狗节拍** | `wd_data->timer` | **`HRTIMER_MODE_REL_HARD`** | `drivers/watchdog/watchdog_dev.c:137` |
| **软 pretimeout** | `wd_data->pretimeout_timer` | **`HRTIMER_MODE_REL`（不带 `_HARD`）** | `drivers/watchdog/watchdog_hrtimer_pretimeout.c:26` / `:36` |

⇒ **喂狗那个是 hard 定时器**（在 PREEMPT_RT 上仍走硬中断上下文，03 §B2.1）；
**pretimeout 这个是普通的** ⇒ **RT 上会被推迟到软中断线程。**
⚠️ **这个差异会放大 §3.1 那个"可能来不及响"的局限**（本文档未在 RT 上验证，99 §5.3）。

⚠️ **`pretimeout_timer` 字段本身是条件编译的**
（`drivers/watchdog/watchdog_core.h:50-52` 的 `#if IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT)`）
⇒ **关掉这个 Kconfig 连字段都不存在**（02 §5.2）。

### §3.4 它改变了 `watchdog_have_pretimeout()` 的语义

```c
/* drivers/watchdog/watchdog_core.h:67-71 */
static inline bool watchdog_have_pretimeout(struct watchdog_device *wdd)
{
	return wdd->info->options & WDIOF_PRETIMEOUT ||
	       IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT);
}
```

⚠️⚠️ **第二个条件是编译期常量，与具体设备无关。**
⇒ **开了 `CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT` 之后，**所有**看门狗都被认为"有 pretimeout"。**

**这个 inline 有五个使用点**（重跑 `grep -rn 'watchdog_have_pretimeout' drivers/watchdog/`）：

| 使用点 | 影响 |
|---|---|
| `drivers/watchdog/watchdog_pretimeout.c:181` | **决定要不要挂进 `pretimeout_list`** |
| `drivers/watchdog/watchdog_pretimeout.c:201` | 注销时对称判断 |
| `drivers/watchdog/watchdog_dev.c:404` | **`watchdog_set_pretimeout()` 的准入** |
| `drivers/watchdog/watchdog_dev.c:609` | **决定 `pretimeout` 这个 sysfs 属性是否可见** |
| `drivers/watchdog/watchdog_dev.c:613` | **决定 `pretimeout_governor` 属性是否可见**（还要 `CONFIG_WATCHDOG_PRETIMEOUT_GOV`） |

⇒ **一个直接的可观测后果**：**开了 `HRTIMER_PRETIMEOUT` 之后，
连硬件完全不支持 pretimeout 的看门狗，`/sys/class/watchdog/watchdogN/pretimeout` 也会出现**（07 §2）。

⚠️⚠️ **而且三个出口的说法不一致（逐个查过代码，不是推断）**：

| 出口 | 会不会报 `WDIOF_PRETIMEOUT` | 依据 |
|---|---|---|
| ⚠️ **`WDIOC_GETSUPPORT`（能力）** | **不会** | **它把 `wdd->info` 原样 `copy_to_user`**（`drivers/watchdog/watchdog_dev.c:752-753`）——**核心从不改 `wdd->info`** |
| **`WDIOC_GETSTATUS`（状态）** | **会** | **`if (IS_ENABLED(CONFIG_WATCHDOG_HRTIMER_PRETIMEOUT)) status \|= WDIOF_PRETIMEOUT;`** ——`drivers/watchdog/watchdog_dev.c:351-352` |
| **sysfs 的 `pretimeout` 属性** | **会（属性存在）** | **`watchdog_have_pretimeout()` 的第二个条件**（`:609`） |

⇒ **同一个标志位，"能力"说没有、"状态"说有** ——**这是本子系统一处真实的不一致**（07 §5 / 08 §5）。
**判据：想知道"硬件到底支不支持"，只能看 `WDIOC_GETSUPPORT`。**

---

## §4 汇总表

### §4.1 子框架 × 职责 × 挂载点 × 相关性

| 子框架 | 职责 | 挂载点 | 导出符号 | arm64 相关 |
|---|---|---|---|---|
| **governor 框架** | **pretimeout 事件的策略分发** | **注册期挂 `pretimeout_list`；事件期 `watchdog_notify_pretimeout()`** | **3**（`watchdog_notify_pretimeout` `_GPL` + 两个注册函数裸 `EXPORT_SYMBOL`） | **是** |
| **`noop` governor** | **`pr_alert` 一条日志** | **自己的 `module_init`** | **0** | **是** |
| **`panic` governor** | **`panic()`** | 同上 | **0** | **是**（默认） |
| **软 pretimeout** | **硬件没有 pretimeout 时用 hrtimer 补** | **`watchdog_start`/`stop` 里 arm/cancel** | **0**（三个函数只在 `drivers/watchdog/` 内部可见） | **弱** |

### §4.2 全树使用统计（每条都能重跑）

**口径**：**"实现数"= `struct watchdog_governor … = {` 的实例数；
"调用点"= 排掉注释行后的行数。**

| 项 | 数量 | 命令 |
|---|---|---|
| **`struct watchdog_governor` 实例（全树）** | **2** | `grep -rn 'struct watchdog_governor [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| **实例分布** | **`drivers/watchdog/pretimeout_noop.c:23` · `drivers/watchdog/pretimeout_panic.c:23`** | 同上 |
| **`watchdog_notify_pretimeout()` 的驱动调用者** | **见下方口径警告** | `grep -rln 'watchdog_notify_pretimeout' drivers/watchdog/ \| grep -v watchdog_pretimeout` |
| **`watchdog_have_pretimeout()` 使用点** | **5 处使用 + 1 处定义 = grep 命中 6** | `grep -rn 'watchdog_have_pretimeout' drivers/watchdog/ \| wc -l` ⇒ **6**（定义在 `watchdog_core.h:67`，5 处使用见 §3.4 的表） |
| **`WATCHDOG_GOV_NAME_MAXLEN`** | **20** | `grep -n 'WATCHDOG_GOV_NAME_MAXLEN' drivers/watchdog/watchdog_pretimeout.h` |
| **`MAX_DOGS`** | **32** | `grep -n 'MAX_DOGS' drivers/watchdog/watchdog_core.h` |
| **Makefile 里 pretimeout 相关的 4 行** | **`watchdog_pretimeout.o`(11) · `watchdog_hrtimer_pretimeout.o`(12) · `pretimeout_noop.o`(14) · `pretimeout_panic.o`(15)** | `grep -n 'pretimeout' drivers/watchdog/Makefile` |

⚠️ **一条口径警告**：**`watchdog_notify_pretimeout` 的"驱动调用者"数没有写进本表。**
**原因**：**要排掉框架自身的三处（定义、`EXPORT_SYMBOL`、hrtimer 回调）以及头文件声明，
而 `drivers/watchdog/` 有 180 个文件** ⇒ **本文档没有做这个统计**（99 §5.3 已登记为未完成项）。
**不写一个可能错的数字，比写一个没核对过的数字好。**

### §4.3 裁掉了什么

| 裁掉的 | 为什么 |
|---|---|
| **约 170 个看门狗驱动的实现** | **只讲契约**；代表驱动的选法见 99 §2.4 |
| **`watchdog-parameters.rst`（740 行）逐驱动的模块参数** | **它是字典而非机制说明** ——**登记为"只查不读"**（99 §1.3） |
| **四个厂商专属文档**（`hpwdt` / `pcwd-watchdog` / `mlx-wdt` / `wdt`，共 277 行） | **单驱动文档** |
| **`convert_drivers_to_kernel_api.rst`（219 行）** | **老驱动迁移指南**，**只读了标题结构**（99 §2.2） |
| **`panic()` 在关中断持锁上下文里能否产出 crash dump** | **需要实机验证**（§2.2 / 99 §5.3） |
| **§1.7 那处可疑之处的实际可达性** | **需要编译 + 特定 Kconfig 组合 + `rmmod`**（已显式标为未验证推断） |

## §5 阅读下一篇

| 想知道 | 去 |
|---|---|
| API 清单 | `01-api.md` |
| 结构体与六组易错关系 | `02-datastructures.md` |
| 主流程 / 心跳工作队列 / 设计债 | `03-internals.md` |
| **驱动骨架 + 用户态骨架** | `05-usage.md` |
| DT 属性 | `06-devicetree.md` |
| **12 个 sysfs 属性 + 4 个 tracepoint** | `07-debug.md` |
| 为什么软 pretimeout 是"尽力而为" | `08-feynman.md` §5 |
