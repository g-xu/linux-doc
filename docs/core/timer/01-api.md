---
title: API 参考
---

# timer 01：代码地图与对外 API

本篇回答两件事：**代码在哪里**（§0）和**我该调哪个函数**（§1 之后）。
两者是同一次查询，所以放在同一篇。

格式约定：
- 表格固定三列 `API / 说明 / 备注`；**签名只写函数名 + 形参名**，类型信息进「备注」
- **位置只精确到文件**，不逐 API 给行号（行号在 03 和 99 里维护）
- 标记重要度，⚠️ 标记陷阱

---

## §0 代码地图

### §0.1 ⚠️ 先看这个：本树相对上游有 3 处改动

**`kernel/time/` 在这棵树里不是纯上游**。基线核对（99 §1.1 有完整命令）结果：

```bash
git status --porcelain -- kernel/time
# →  M kernel/time/hrtimer.c
#    M kernel/time/tick-sched.c
#    M kernel/time/timer.c
```

三处改动**全部是同一件事**：把 softirq 的唤起换成 PREEMPT_RT 专用的包装函数。

| 文件 | 改动 | 上游写法 |
|---|---|---|
| `hrtimer.c` | 两处 `raise_hrtimer_softirq()` | `raise_softirq_irqoff(HRTIMER_SOFTIRQ)` |
| `timer.c` | 一处 `raise_timer_softirq()` | `raise_softirq(TIMER_SOFTIRQ)` |
| `tick-sched.c` | 一处 `local_pending_timers()` | `local_softirq_pending()` |

**关键结论：在不开 `CONFIG_PREEMPT_RT` 的配置下，语义和上游完全一致。**
因为这三个包装函数在 `include/linux/interrupt.h:624-638` 的 `#else` 分支里
就是原来那三个调用的 `static inline` 直译。

⚠️ **但这棵树是可以开 RT 的**：`arch/arm64/Kconfig:96` 的
`select ARCH_SUPPORTS_RT` 是**本地新增的一行**（上游 6.1 的 arm64 没有它），
而 `CONFIG_PREEMPT_RT` 的门是 `depends on EXPERT && ARCH_SUPPORTS_RT`
（`kernel/Kconfig.preempt:78`）⇒ **arm64 上开得起来**。
开了之后 timer/hrtimer 的 softirq **不再走普通 softirq，而是走一个专用内核线程 `timersd`**
（`kernel/softirq.c` 里的 `timer_threads`）⇒ 03 篇有一节专讲这条路。

⇒ **这套文档同时覆盖两条路**：非 RT（默认）和 RT。每处分叉都会显式标出来。

### §0.2 文件与职责

`kernel/time/` 共 **35 个文件、22995 行**（逐文件行数见 99 §1.2）。按职责分七组：

| 组 | 文件 | 职责 |
|---|---|---|
| **低精度定时器** | `timer.c` | timer wheel、`timer_list` 全套、`schedule_timeout`、`msleep` |
| **高精度定时器** | `hrtimer.c` | 红黑树、`hrtimer` 全套、`schedule_hrtimeout` |
| **读时间** | `timekeeping.c` · `timekeeping_debug.c` · `vsyscall.c` | `ktime_get*` 全家族、`settimeofday`、vDSO 数据更新 |
| **时钟源** | `clocksource.c` · `jiffies.c` · `sched_clock.c` · `timecounter.c` · `clocksource-wdtest.c` | 注册/选优/看门狗；`jiffies.c` 是兜底时钟源 |
| **时钟事件 + tick** | `clockevents.c` · `tick-common.c` · `tick-sched.c` · `tick-broadcast.c` · `tick-broadcast-hrtimer.c` · `tick-oneshot.c` · `tick-legacy.c` | 事件设备注册、周期/单次 tick、NO_HZ、广播 |
| **POSIX 定时器** | `posix-timers.c` · `posix-cpu-timers.c` · `posix-clock.c` · `posix-stubs.c` · `itimer.c` · `alarmtimer.c` | 用户态可见的定时器接口 |
| **其它** | `ntp.c` · `time.c` · `timeconv.c` · `namespace.c` · `timer_list.c` · `test_udelay.c` · `time_test.c` | NTP 校准、系统调用层、时间换算、time namespace、debugfs 输出、测试 |

四个"主体文件"（每个 2.3k 行以上）：`timekeeping.c`(2503) ·
`hrtimer.c`(2426) · `timer.c`(2367) · `posix-cpu-timers.c`(1702)。

### §0.3 头文件

| 头文件 | 行数 | 给谁用 |
|---|---|---|
| `include/linux/hrtimer.h` | 543 | 要用高精度定时器的驱动 |
| `include/linux/jiffies.h` | 443 | `HZ`、`jiffies`、`time_after/before`、各种 `*_to_jiffies` 换算 |
| `include/linux/tick.h` | 324 | 主要给核心和 idle 路径；驱动很少直接用 |
| `include/linux/timekeeping.h` | 312 | **`ktime_get*` 全家族的声明**——"我要读时间"就来这里 |
| `include/linux/clocksource.h` | 309 | 写时钟源驱动 |
| `include/linux/timer.h` | 238 | 低精度定时器 |
| `include/linux/ktime.h` | 234 | `ktime_t` 类型与算术 |
| `include/linux/clockchips.h` | 227 | 写时钟事件设备驱动 |
| `include/linux/timekeeper_internal.h` | 156 | ⚠️ 名字带 internal，但被 vDSO 和架构代码包含 |
| `include/linux/delay.h` | — | **`udelay` / `msleep` / `usleep_range` / `fsleep` 在这里**，不在 `timer.h` |

⚠️ **最常见的找不到**：想 `msleep()` 却去翻 `timer.h`。
`msleep()` 的**实现**在 `kernel/time/timer.c`，但**声明**在 `include/linux/delay.h`。

### §0.4 Kconfig：五个真正影响行为的开关

| 选项 | 位置 | 影响 |
|---|---|---|
| `CONFIG_HIGH_RES_TIMERS` | `kernel/time/Kconfig:191` | **不开则 hrtimer 退化到 tick 粒度**（API 还在，精度没了） |
| `CONFIG_NO_HZ_IDLE` | `kernel/time/Kconfig:108` | idle 时停 tick（arm64 默认这个） |
| `CONFIG_NO_HZ_FULL` | `kernel/time/Kconfig:118` | 只剩一个可运行任务时也停 tick；依赖一长串（`SMP`、`RCU_NOCB_CPU`、`CPU_ISOLATION` …） |
| `CONFIG_HZ` | `kernel/Kconfig.hz:51` | **tick 频率**，100/250/300/1000 四选一 ⇒ 决定 `jiffies` 的粒度 |
| `CONFIG_CLOCKSOURCE_WATCHDOG` | `kernel/time/Kconfig:9` | 时钟源互相验证，发现跳变就降级 ⇒ 07 篇的重要日志来源 |

另外三个只被 `select`、不直接选的：`TICK_ONESHOT`（`:91`）、
`NO_HZ_COMMON`（`:94`）、`GENERIC_CLOCKEVENTS`（`:30`，`def_bool !LEGACY_TIMER_TICK`）。

---

## §1 先分清四组抽象（本篇的导航）

**这个子系统最大的入门障碍不是 API 多，是四个东西名字都像"时钟"**：

| 抽象 | 一句话 | 方向 | 谁实现 | 驱动写它还是用它 |
|---|---|---|---|---|
| **clocksource** | "现在是什么时候" | **读** | 硬件计数器驱动 | 平台驱动**实现**它 |
| **clockevent** | "到点了叫我" | **写** | 能产生中断的硬件定时器 | 平台驱动**实现**它 |
| **timekeeper** | 把 clocksource 的原始计数换算成人类时间 | 读 | **核心，没有驱动侧** | 只**用**（`ktime_get*`） |
| **timer / hrtimer** | "N 纳秒后调我的回调" | 写 | **核心，架在 clockevent 上** | 只**用** |

⇒ **绝大多数驱动作者只碰后两行**（`ktime_get*` 读时间、`hrtimer`/`timer_list` 定时）。
前两行只有**平台/SoC 的时钟驱动**作者才写。
四者的关系图和"为什么必须分开"见 02 §1.0 和 08 §1。

**按你要做的事跳转**：

| 我想… | 去 |
|---|---|
| 延时几微秒/毫秒 | §5 |
| N 毫秒后跑一个回调（精度不敏感） | §3 |
| N 微秒后跑一个回调（精度敏感） | §4 |
| 读当前时间 / 算时间差 | §6 |
| 给新 SoC 写时钟驱动 | §7（clocksource）+ §8（clockevent） |
| 让某个 CPU 不被 tick 打扰 | §10 |
| 用户态定时器接口 | §13 |

---

## §2 jiffies 与 HZ

头文件：`include/linux/jiffies.h`；实现：`kernel/time/jiffies.c`、`kernel/time/timer.c`

| API | 说明 | 备注 |
|---|---|---|
| `jiffies` | 开机以来的 tick 数 | **`unsigned long`，32 位上会绕回** ⇒ 永远别直接比大小 |
| `jiffies_64` | 64 位版本 | 32 位上读它要用 `get_jiffies_64()`（非原子） |
| `get_jiffies_64()` | 安全地读 64 位 jiffies | 64 位架构上就是直接读 |
| `time_after(a, b)` / `time_before(a, b)` | **绕回安全的比较** | **必须用它，不能写 `a > b`** |
| `time_after_eq()` / `time_before_eq()` | 带等号的版本 | — |
| `time_after64()` / `time_before64()` | 64 位版本 | — |
| `msecs_to_jiffies(m)` | 毫秒 → jiffies | **向上取整**；`m` 为 `MAX_JIFFY_OFFSET` 相关的大值时会截断 |
| `usecs_to_jiffies(u)` | 微秒 → jiffies | ⚠️ `HZ=100` 时任何 < 10ms 的值都变成 1 |
| `jiffies_to_msecs(j)` / `jiffies_to_usecs(j)` | 反向换算 | 返回 `unsigned int` |
| `nsecs_to_jiffies(n)` | 纳秒 → jiffies | — |
| `timespec64_to_jiffies()` / `jiffies_to_timespec64()` | 与 `timespec64` 互转 | — |
| `HZ` | tick 频率 | `CONFIG_HZ`，100/250/300/1000 |
| `USER_HZ` | 用户态看到的频率 | 固定 100，`/proc` 里的时间用它 |

⚠️ **`jiffies` 的三个坑**（08 §2 详讲）：
1. **绕回**：`HZ=1000` 的 32 位上约 49.7 天绕一圈 ⇒ 比较一律 `time_after()`
2. **不是时间**：它是 tick 计数，NO_HZ 下 idle 期间靠补偿追平，不是连续累加的
3. **粒度**：`HZ=100` 时一个 jiffy = 10ms ⇒ `msecs_to_jiffies(1)` 得到 1，实际可能睡 10ms

---

## §3 `timer_list`：低精度 / 超时定时器

头文件：`include/linux/timer.h`；实现：`kernel/time/timer.c`（2367 行，30 个导出符号）

**定位**：它是"**超时**"用的，不是"**精确计时**"用的。
官方文档 `Documentation/timers/hrtimers.rst` 把这一点讲得很直白——
timer wheel 针对的场景是"绝大多数超时在到期前就被取消了"（I/O 超时、重传定时器），
所以它优化的是**添加/删除便宜**，不是**到期准**。

### §3.1 定义与初始化

| API | 说明 | 备注 |
|---|---|---|
| `struct timer_list` | 定时器对象 | 内嵌进你自己的结构里，别单独 kmalloc |
| `timer_setup(timer, callback, flags)` | **标准初始化宏** | 回调原型是 `void (*)(struct timer_list *)` ⇒ 用 `from_timer()` 取回宿主结构 |
| `from_timer(var, callback_timer, timer_field)` | 从 `timer_list *` 反查宿主结构 | 就是 `container_of` 的包装 |
| `DEFINE_TIMER(name, callback)` | 静态定义 | 静态定时器，回调不带宿主 |
| `timer_setup_on_stack()` | 栈上定时器 | **用完必须 `destroy_timer_on_stack()`** |
| `init_timer_key()` | 底层实现 | 不要直接调，用 `timer_setup()` |

**`flags` 的三个标志位（外加"什么都不给"的 0）**（`include/linux/timer.h:38-52` 有
逐个的 kerneldoc）：

| flag | 含义 |
|---|---|
| `0` | 默认：绑在当前 CPU，可被 NO_HZ 迁移 |
| `TIMER_DEFERRABLE` | **可延后**：CPU idle 时不为它唤醒 ⇒ 省电，但到期时间无保证 |
| `TIMER_PINNED` | **钉在当前 CPU**，不迁移 |
| `TIMER_IRQSAFE` | 回调在关中断下跑；影响 `timer_delete_sync()` 的等待语义 |

### §3.2 启动与修改

| API | 说明 | 备注 |
|---|---|---|
| `add_timer(timer)` | 启动一个尚未启动的定时器 | ⚠️ **对已 pending 的定时器调用它是 BUG**（会 `WARN`） |
| `mod_timer(timer, expires)` | **设置到期时间并启动**（不管之前是否 pending） | **这是最常用的一个**；返回 1 表示之前是 pending 的 |
| `timer_reduce(timer, expires)` | **只在能提前时才改** | 用于"多个来源都想设超时，取最早的那个" |
| `mod_timer_pending()` | 只在已 pending 时才改 | 不会启动一个未启动的定时器 |
| `add_timer_on(timer, cpu)` | 启动在指定 CPU 上 | 隐含 `TIMER_PINNED` 语义 |
| `timer_pending(timer)` | 是否在队列里等待到期 | ⚠️ **返回 0 不代表回调没在跑**——正在执行的定时器不算 pending |
| `round_jiffies(j)` 家族 | 把到期时间对齐到秒边界 | 8 个变体（`__round_jiffies`、`_relative`、`_up`、`_up_relative` 及各自的 `__` 版）⇒ 让多个定时器聚在一起到期，省电 |

### §3.3 ⚠️ 删除：这里正在改名，而且新增了一个语义

**6.1.158 上这组 API 处在改名期**，`include/linux/timer.h:184-214` 一次看全：

| 现在的名字 | 位置 | 状态 |
|---|---|---|
| `timer_delete(timer)` | `include/linux/timer.h:186` | **新名，推荐** |
| `timer_delete_sync(timer)` | `include/linux/timer.h:185` | **新名，推荐** |
| `timer_shutdown(timer)` | `include/linux/timer.h:188` | **全新 API，没有旧名对应** |
| `timer_shutdown_sync(timer)` | `include/linux/timer.h:187` | **全新 API** |
| ⚠️ `del_timer(timer)` | `include/linux/timer.h:211-214` | ⚠️ **旧名**，`static inline` 转调新名，注释写着 `Do not use in new code` |
| ⚠️ `del_timer_sync(timer)` | `include/linux/timer.h:198-201` | ⚠️ 同上 |
| `try_to_del_timer_sync(timer)` | `include/linux/timer.h:184` | 非阻塞尝试；**名字还是旧风格，没跟着改** |

**改名形式归类**（skill 规则 24 的四种形式之一）：**平行 `static inline`**。
⇒ **会分叉**：两个名字是两个独立的函数体（虽然现在一个只是转调另一个），
不像 `#define` 那样天然同步。**写文档时不能只说"是别名"，要说清是哪一种。**

⚠️ **旧名仍然是绝对主流**（全树文件数，可重跑）：

| 名字 | 出现的 `.c` 文件数 |
|---|---|
| `del_timer_sync` | **685** |
| `del_timer` | **319** |
| `timer_delete` | 7 |
| `timer_delete_sync` | 5 |
| `timer_shutdown_sync` | 2 |

复现：`grep -rlw '<名字>' --include=*.c . | wc -l`
⇒ **读老代码会大量遇到旧名**，写新代码用新名。

### §3.4 `timer_delete_sync()` 和 `timer_shutdown_sync()` 的区别（最容易踩的一个）

| API | 做什么 | 之后还能重新启动吗 |
|---|---|---|
| `timer_delete_sync()` | 删除 + **等回调跑完** | **能** |
| `timer_shutdown_sync()` | 删除 + 等回调跑完 + **把 `timer->function` 置 NULL** | **不能**——之后的 `mod_timer()` 会被静默丢弃 |

**机理**：`timer_shutdown_sync()` 走的是 `__timer_delete(timer, shutdown=true)`
（`kernel/time/timer.c:1315`），`shutdown` 为真时在持锁状态下把 `function` 置 NULL
（`kernel/time/timer.c:1337` 一带）；此后 `mod_timer()` 一族看到 `function == NULL`
就**返回 0 并且什么都不做**（`kernel/time/timer.c:1182`、`:1207` 的返回值注释里写明了
"was in shutdown state and the operation was discarded"）。

⇒ **用哪个**：
- **对象要释放了**（`kfree` / 模块卸载 / `remove()`）⇒ `timer_shutdown_sync()`。
  它把"回调再也不会跑"这件事变成**代码保证**，而不是靠你自己检查所有重新启动的路径
- **只是想暂停，之后还要用** ⇒ `timer_delete_sync()`

⚠️ **`timer_delete_sync()` 的死锁条件比直觉宽得多。**
`kernel/time/timer.c:1584-1624` 的 kerneldoc 用一个 CPU0/CPU1 对照图讲了这件事，
结论是一句反直觉的话：

> 对**非 irqsafe** 的定时器，调用者**不能持有任何会在中断上下文里被拿到的锁**——
> **即使这把锁和这个定时器毫无关系**。

**为什么"毫无关系的锁"也不行**（原文的场景）：
CPU1 的 softirq 正在跑 `mytimer` 的回调，`base->running_timer = mytimer`；
CPU0 持着 `somelock` 调 `timer_delete_sync()`，开始自旋等 `running_timer` 清零；
此时 CPU1 上来了个中断，它要拿 `somelock` ⇒ **中断把那个 softirq 挤住了**，
回调永远跑不完 ⇒ CPU0 永远等不到，`somelock` 也永远不放。

⇒ **两条出路**：把锁放掉再删，或者用 `try_to_del_timer_sync()` 自己处理失败。

同一段 kerneldoc 还写明了 `timer_delete_sync()` 的**返回值什么时候没意义**：
如果存在并发重新启动的可能，函数刚放掉 base 锁之后就可能被重新 arm ⇒
**返回值不可信**，这种拆除场景要用 `timer_shutdown_sync()`（§3.4 的表）。

---

## §4 `hrtimer`：高精度定时器

头文件：`include/linux/hrtimer.h`（543 行）；实现：`kernel/time/hrtimer.c`（2426 行，18 个导出符号）

**和 `timer_list` 的分工**（`Documentation/timers/hrtimers.rst` 的核心论点）：
timer wheel 优化"加了又取消"，hrtimer 优化"到期要准"。
数据结构上一个是**时间轮 + 级联**，一个是**红黑树 + 时间序插入**。
⇒ 08 §5 用"取号机 vs 排好序的队列"讲这件事。

### §4.1 初始化与启动

| API | 说明 | 备注 |
|---|---|---|
| `struct hrtimer` | 定时器对象 | 同样内嵌进自己的结构 |
| `hrtimer_init(timer, clock_id, mode)` | 初始化 | **回调要自己赋值给 `timer->function`**，`hrtimer_init()` 不接收回调参数 |
| `timer->function` | 回调 | 原型 `enum hrtimer_restart (*)(struct hrtimer *)` ⇒ **返回值决定是否自动重启** |
| `hrtimer_start(timer, tim, mode)` | 启动 | 是 `hrtimer_start_range_ns(..., 0, mode)` 的包装 |
| `hrtimer_start_range_ns(timer, tim, range_ns, mode)` | **带松弛量的启动** | `range_ns` 允许内核把多个到期点合并 ⇒ **省电，强烈推荐给不严格的场景** |
| `hrtimer_init_on_stack()` / `destroy_hrtimer_on_stack()` | 栈上定时器 | 成对使用 |
| `hrtimer_forward(timer, now, interval)` | **在回调里推进到下一个周期** | 返回跳过了几个周期 ⇒ **周期定时器的标准写法** |
| `hrtimer_forward_now(timer, interval)` | 用当前时间做基准的版本 | 更常用 |

**`clock_id` 常用四个**：

| clock_id | 含义 | 会被 settimeofday 影响吗 |
|---|---|---|
| `CLOCK_MONOTONIC` | 开机以来单调递增（**不含挂起时间**） | **不会** ⇒ 测间隔、做定时用它 |
| `CLOCK_REALTIME` | 墙上时钟 | **会**（可能跳变，甚至倒退） |
| `CLOCK_BOOTTIME` | 单调 + **含挂起时间** | 不会 |
| `CLOCK_TAI` | 原子时（无闰秒） | 不会 |

**`mode` 是两组标志的组合**：

| mode | 含义 |
|---|---|
| `HRTIMER_MODE_ABS` / `_REL` | 到期时间是绝对值还是相对现在 |
| `HRTIMER_MODE_HARD` | **回调在硬中断上下文直接跑** |
| `HRTIMER_MODE_SOFT` | **回调在 softirq 里跑** |
| `_PINNED` | 不迁移到别的 CPU |

⚠️ **`HARD` / `SOFT` 是本子系统在 PREEMPT_RT 上最要紧的一个选择**：
RT 内核上 `SOFT` 回调跑在 `timersd` 线程里（可被抢占、可睡的锁能用），
`HARD` 回调仍在硬中断里（**不能睡，不能拿普通 spinlock**）。
非 RT 上 `SOFT` 走普通 `HRTIMER_SOFTIRQ`。这条分叉是本树 3 处改动的直接后果（§0.1），
03 篇有一节专讲。

### §4.2 查询与取消

| API | 说明 | 备注 |
|---|---|---|
| `hrtimer_cancel(timer)` | 取消 + **等回调跑完** | ⚠️ **不能在自己的回调里调**（会死等自己）⇒ 回调里想停就 `return HRTIMER_NORESTART` |
| `hrtimer_try_to_cancel(timer)` | 非阻塞尝试取消 | 返回 `-1` = **回调正在跑，没取消掉**；0 = 本来就没启动；1 = 取消成功 |
| `hrtimer_active(timer)` | 是否启动中**或**回调正在跑 | **比 `timer_pending()` 语义宽**——它把"正在跑"也算 active，这正是 `timer_list` 那边缺的 |
| `hrtimer_is_queued(timer)` | 只看在不在红黑树里 | 这个才对应 `timer_pending()` |
| `hrtimer_get_remaining(timer)` | 还剩多久到期 | 底层是 `__hrtimer_get_remaining()` |
| `hrtimer_resolution` | **当前实际精度（纳秒）** | 变量不是函数；高精度模式下是硬件精度，否则是 `1/HZ` ⇒ **07 篇用它判断有没有真的进高精度模式** |

**回调返回值只有两个**：`HRTIMER_NORESTART`（结束）和 `HRTIMER_RESTART`
（按 `timer->expires` 再来一次 ⇒ 要先用 `hrtimer_forward_now()` 推进，否则立刻再到期，**死循环**）。

### §4.3 `hrtimer_sleeper`：给"睡到某个时间点"用

| API | 说明 | 备注 |
|---|---|---|
| `struct hrtimer_sleeper` | hrtimer + 要唤醒的任务 | `nanosleep`、futex 超时都用它 |
| `hrtimer_init_sleeper()` / `hrtimer_init_sleeper_on_stack()` | 初始化 | — |
| `hrtimer_sleeper_start_expires()` | 启动 | — |
| `schedule_hrtimeout(expires, mode)` | **睡到 `expires`** | 驱动里等事件+高精度超时的标准写法 |
| `schedule_hrtimeout_range(expires, delta, mode)` | 带松弛量的版本 | **优先用这个**，`delta` 给内核合并的余地 |
| `schedule_hrtimeout_range_clock()` | 还能指定时钟 | 最底层的那个 |

---

## §5 延时与睡眠（驱动里用得最多的一组）

头文件：`include/linux/delay.h`（**不是 `timer.h`**）；实现散在
`kernel/time/timer.c`（`msleep` / `usleep_range_state`）和各架构的 `udelay`

**选哪个只看两个问题**（`Documentation/timers/timers-howto.rst` 的原始决策树）：
**① 我在原子上下文吗？② 要睡多久？**

### §5.1 原子上下文：只能忙等

| API | 说明 | 备注 |
|---|---|---|
| `udelay(usecs)` | 忙等微秒 | **原子上下文的首选**；靠 `loops_per_jiffy`（`include/linux/delay.h:25`）估算，**不读硬件** |
| `ndelay(nsecs)` | 忙等纳秒 | ⚠️ **多数非 PC 平台上根本没有纳秒级精度**，实际会向上取整 |
| ⚠️ `mdelay(msecs)` | 忙等毫秒 | ⚠️ **官方明确不推荐**（`include/linux/delay.h:44` 是 `udelay` 的循环包装，为了绕过 `MAX_UDELAY_MS`=5 的溢出限制，`:40`）⇒ **能改成 `msleep` 就改** |

⚠️ **`udelay` 在变频 CPU 上不可靠**：`Documentation/timers/timekeeping.rst`
最后一节专门讲这件事——`loops_per_jiffy` 是开机时按当时频率标定的，降频后
同样的循环次数会**延时翻倍**。所以 `udelay(N)` 的语义是"**至少** N 微秒"，
不是"恰好 N 微秒"。有些架构（ARM、OpenRISC）提供 `struct delay_timer`
把忙等换成读硬件计数器来规避这一点。

### §5.2 非原子上下文：按时长选

| 时长 | 用什么 | 为什么 |
|---|---|---|
| **< ~10µs** | `udelay()` | 建立 hrtimer 的开销可能比睡的时间还长 |
| **10µs – 20ms** | `usleep_range(min, max)` | **底层是 hrtimer，精度高** |
| **> 10ms** | `msleep()` | 底层是 jiffies/timer wheel，够用且便宜 |
| 任意（不可中断） | `fsleep(usecs)` | `include/linux/delay.h:81`，按上面的规则**自动三选一**；⚠️ 全树只有这一处定义、几乎没人用 |

| API | 说明 | 备注 |
|---|---|---|
| `usleep_range(min, max)` | 睡 `[min, max]` 微秒 | `include/linux/delay.h:65`；**给足 range**，内核可以把你的唤醒和别人的合并 ⇒ 少一次中断 |
| `usleep_idle_range()` | 同上但用 idle 语义的等待状态 | `include/linux/delay.h:70` |
| `usleep_range_state(min, max, state)` | 能指定任务状态 | `include/linux/delay.h:62`，上面两个的底层 |
| `msleep(msecs)` | 睡毫秒，**不可中断** | ⚠️ **`msleep(1..20)` 实际可能睡到 ~20ms**（官方文档点名的坑）⇒ 这个区间用 `usleep_range` |
| `msleep_interruptible(msecs)` | 可被信号打断 | 返回**剩余的毫秒数**，被打断时非 0 |
| `ssleep(seconds)` | 睡秒 | `include/linux/delay.h:75`，就是 `msleep(s*1000)` |

### §5.3 `schedule_timeout` 家族：等事件 + 超时

实现：`kernel/time/timer.c`

| API | 说明 | 备注 |
|---|---|---|
| `schedule_timeout(timeout)` | 睡最多 `timeout` 个 **jiffies** | ⚠️ **调用前必须自己 `set_current_state()`**，否则会立刻返回 |
| `schedule_timeout_interruptible(t)` | 帮你设好 `TASK_INTERRUPTIBLE` | **更推荐**，少一个坑 |
| `schedule_timeout_uninterruptible(t)` | `TASK_UNINTERRUPTIBLE` | — |
| `schedule_timeout_killable(t)` | 只响应致命信号 | — |
| `schedule_timeout_idle(t)` | `TASK_IDLE`（不计入 load average） | 内核线程等待时用它比 `UNINTERRUPTIBLE` 好 |

⇒ **要高精度超时就用 §4.3 的 `schedule_hrtimeout_range()`**，
`schedule_timeout` 的粒度是 jiffies。

---

## §6 读时间：`ktime_get*` 家族

头文件：`include/linux/timekeeping.h`；类型与算术：`include/linux/ktime.h`；
实现：`kernel/time/timekeeping.c`（26 个导出符号，本子系统导出最多的文件之一）

**这一族是整个子系统里驱动作者用得第二多的东西**（第一是 §5）。
命名规律一旦看懂就不用记了：

```
ktime_get [_real|_boottime|_clocktai|_raw] [_ts64|_seconds|_ns|_fast_ns] [_coarse]
           └── 哪个时基 ──────────────┘   └── 什么返回类型 ─┘        └ 精度/开销 ┘
```

### §6.1 四个时基，先选对这个

| 时基 | 从哪算起 | 含挂起时间 | 会被改时间影响 | 典型用途 |
|---|---|---|---|---|
| **mono**（默认，无后缀） | 开机 | **不含** | **不会** | **测间隔、超时、定时** |
| **real** | 1970-01-01 | 不含 | **会（可能倒退）** | 打时间戳给用户看 |
| **boottime** | 开机 | **含** | 不会 | 需要覆盖挂起期的统计 |
| **tai** | 1970（无闰秒） | 不含 | 不会 | 精密时间同步（PTP） |
| **raw** | 开机 | 不含 | 不会，**且不受 NTP 调频影响** | 想看硬件原始速率 |

⚠️ **默认就该用 mono**。用 `real` 做超时计算是经典 bug：
NTP 或 `settimeofday()` 一跳，你的超时就可能变成负数或几十年。

### §6.2 常用接口

| API | 说明 | 备注 |
|---|---|---|
| `ktime_get()` | 单调时间，`ktime_t` | **最常用的一个** |
| `ktime_get_ns()` | 单调时间，`u64` 纳秒 | 就是 `ktime_to_ns(ktime_get())` |
| `ktime_get_real_ts64(ts)` | 墙上时钟，`struct timespec64` | 要"年月日"就先拿这个 |
| `ktime_get_ts64(ts)` | 单调，`timespec64` | — |
| `ktime_get_boottime()` / `_ns()` / `_ts64()` | boottime 时基 | — |
| `ktime_get_clocktai()` | TAI 时基 | — |
| `ktime_get_raw()` / `ktime_get_raw_ts64()` | 原始时基 | — |
| `ktime_get_seconds()` | 单调**秒**数 | **很便宜**，只要秒精度就用它 |
| `ktime_get_real_seconds()` | 墙上时钟**秒**数 | 同上 |
| `getboottime64(ts)` | 开机时刻的墙上时间 | — |

### §6.3 两个"性能变体"：coarse 和 fast

| 变体 | 语义 | 什么时候用 |
|---|---|---|
| `*_coarse*`（如 `ktime_get_coarse_real_ts64()`） | **只读上次 tick 更新的缓存值，不读硬件** | 极便宜，误差最多一个 tick ⇒ **高频打日志/统计用它** |
| `*_fast_ns`（如 `ktime_get_mono_fast_ns()`） | **NMI-safe**：不加 seqlock，用无锁快照 | **只有在 NMI / tracing / printk 这种不能等锁的上下文才用** |

五个 fast 变体全在 `kernel/time/timekeeping.c`：
`ktime_get_mono_fast_ns()` · `ktime_get_raw_fast_ns()` · `ktime_get_boot_fast_ns()` ·
`ktime_get_tai_fast_ns()` · `ktime_get_real_fast_ns()`。

⚠️ **别因为名字里有 fast 就默认用它**——它牺牲的是一致性（可能读到
正在被更新的时基），普通代码用 `ktime_get()` 就好。

### §6.4 `ktime_t` 的算术

头文件：`include/linux/ktime.h`

| API | 说明 | 备注 |
|---|---|---|
| `ktime_t` | 64 位纳秒标量 | 本质是 `s64`，**可以直接加减比较** |
| `ktime_add(a, b)` / `ktime_sub(a, b)` | 加减 | 也可以直接写 `a + b` |
| `ktime_add_ns()` / `ktime_sub_ns()` | 和纳秒数加减 | — |
| `ktime_to_ns()` / `ns_to_ktime()` | 与 `u64` 纳秒互转 | — |
| `ktime_to_us()` / `ktime_to_ms()` | 转微秒/毫秒 | 会做除法，热路径注意 |
| `ktime_compare(a, b)` / `ktime_after()` / `ktime_before()` | 比较 | `ktime_t` 不会绕回，不像 `jiffies` |
| `ktime_add_safe(a, b)` | 加法但饱和到 `KTIME_MAX` | 实现在 `kernel/time/hrtimer.c` |
| `ktime_divns(kt, div)` | 除法 | 32 位上底层是 `__ktime_divns()`（`kernel/time/hrtimer.c`） |
| `ktime_get_resolution_ns()` | 当前时基的分辨率 | 和 §4.2 的 `hrtimer_resolution` 不是同一个东西 |

---

## §7 clocksource：给平台驱动用（"现在是什么时候"）

头文件：`include/linux/clocksource.h`；实现：`kernel/time/clocksource.c`（6 个导出符号）

**只有写 SoC 时钟驱动的人才碰这一节。** arm64 上实际用的是
`drivers/clocksource/arm_arch_timer.c`（1791 行），DT 绑定 `arm,armv8-timer`。

| API | 说明 | 备注 |
|---|---|---|
| `clocksource_register_hz(cs, hz)` | **按 Hz 注册**（`include/linux/clocksource.h:244`） | **自动算 `mult`/`shift`**，这是推荐入口 |
| `clocksource_register_khz(cs, khz)` | 按 kHz 注册（`:249`） | 同上 |
| `__clocksource_register_scale()` | 底层（`:231`） | ⚠️ 头文件注释明确写着 `Don't call ... directly`（`:227-228`） |
| `clocksource_mmio_init(base, name, hz, rating, bits, read)` | **单个 MMIO 寄存器的时钟源一行注册**（`:277`） | 最省事的路径，配 4 个现成 read 函数（`clocksource_mmio_readl_up/down`、`readw_up/down`，`:272-275`） |
| `clocksource_unregister(cs)` | 注销（`:210`） | — |
| `clocksource_change_rating(cs, rating)` | 改评分 ⇒ 触发重新选优（`:212`） | **选优就是靠 `rating` 比大小** |
| `clocks_calc_mult_shift()` | 手工算换算因子（`:224`） | — |
| `clocksource_khz2mult()` / `clocksource_hz2mult()` | 固定 shift 下算 mult（`:172` / `:186`） | — |
| `clocksource_verify_percpu(cs)` | 校验各 CPU 读数一致（`:307`） | 看门狗相关 |

**`struct clocksource` 的三个必填字段**（02 §5 展开）：
`read()`（读计数器）、`mask`（**多少位有效**，决定绕回补偿）、`rating`（选优用）。
`mult`/`shift` 由注册函数代算，别手填。

⚠️ **`mask` 写错是本子系统最隐蔽的坑**：32 位计数器写成 64 位的 mask，
系统跑到绕回点（100MHz 下约 43 秒）时间就跳一次。官方文档
`Documentation/timers/timekeeping.rst` 的 clocksource 一节专门解释了 mask 的作用。

---

## §8 clockevent：给平台驱动用（"到点了叫我"）

头文件：`include/linux/clockchips.h`；实现：`kernel/time/clockevents.c`（4 个导出符号）

| API | 说明 | 备注 |
|---|---|---|
| `clockevents_config_and_register(dev, freq, min_delta, max_delta)` | **配置 + 注册一步到位**（`include/linux/clockchips.h:186`） | **驱动的标准入口** |
| `clockevents_register_device(dev)` | 只注册（`:183`） | 需要自己填好 `mult`/`shift` |
| `clockevents_update_freq(ce, freq)` | 频率变了之后更新（`:190`） | — |
| `clockevents_unbind_device(ced, cpu)` | 解绑（`:184`） | — |
| `clockevent_delta2ns(latch, evt)` | 硬件计数 → 纳秒（`:182`） | 算 `min_delta_ns`/`max_delta_ns` 用 |
| `clockevents_suspend()` / `clockevents_resume()` | 电源管理（`:198` / `:199`） | 核心调，驱动不调 |

**`struct clock_event_device` 的关键回调**（02 §5 展开）：
`set_next_event()`（单次模式，**必填**）、`set_state_oneshot()` / `set_state_periodic()` /
`set_state_shutdown()`（状态机）、`event_handler`（**核心填给你的**，
驱动的中断处理函数里调它）。

⚠️ **`event_handler` 是这一层最容易搞反的地方**：它**不是你实现的**，
是 clockevent 核心根据这个设备被分配到什么职责（周期 tick / 单次 / 广播）
填进去的函数指针；你的硬件中断处理函数只负责清中断然后 `dev->event_handler(dev)`。
`Documentation/timers/highres.rst` 的 "clock event management" 一节讲的就是这件事。

**广播相关**（`include/linux/clockchips.h:203-212`）：`tick_broadcast()`、
`tick_receive_broadcast()`、`tick_setup_hrtimer_broadcast()`、`tick_check_broadcast_expired()`。
⇒ 用于"深度 idle 时本地定时器停了，靠一个全局定时器把大家叫醒"，04 篇有一节。

---

## §9 `sched_clock`：给调度器和 printk 用的快时钟

头文件：`include/linux/sched_clock.h`；实现：`kernel/time/sched_clock.c`（无导出符号）

| API | 说明 | 备注 |
|---|---|---|
| `sched_clock_register(read, bits, rate)` | **注册一个 sched_clock 源**（`include/linux/sched_clock.h:41`） | arm64 上由 `arm_arch_timer` 调 |
| `sched_clock()` | 读，返回开机以来纳秒 | **必须任意上下文可调（含 NMI）** |
| `sched_clock_read_begin()` / `_retry()` | 无锁读的序列号协议（`:36`） | 一般不直接用 |

**它和 clocksource 的区别**（`Documentation/timers/timekeeping.rst` 有一整节）：
`sched_clock()` 追求**快**，可以为速度牺牲精度；clocksource 追求**准**。
两者常常读同一个硬件计数器，但走不同的换算路径。
⚠️ `sched_clock()` **可能在挂起时停或归零** ⇒ 别拿它当时间戳。

---

## §10 tick 与 NO_HZ

头文件：`include/linux/tick.h`；实现：`kernel/time/tick-*.c`（`tick-sched.c` 有 8 个导出符号）

⚠️ **这一节里几乎没有"驱动该调的 API"**——tick 层是核心和 idle 路径的内部机制。
列出来是为了让你在读代码/看 trace 时认得出它们。

| API / 变量 | 说明 | 备注 |
|---|---|---|
| `tick_nohz_full_mask` | 哪些 CPU 是 adaptive-tick | `nohz_full=` 启动参数的结果 |
| `tick_nohz_full_running` | NO_HZ_FULL 是否真的在跑 | 07 篇用它确认配置有没有生效 |
| `tick_nohz_dep_set_cpu(cpu, bit)` / `_clear_cpu()` | **"我这里有事，别停 tick"** | 子系统用它临时禁止某 CPU 进 tickless；`bit` 是 `TICK_DEP_BIT_*` |
| `tick_nohz_dep_set_task()` / `_clear_task()` | 按任务粒度 | — |
| `get_cpu_idle_time_us(cpu, last_update)` | 该 CPU 的 idle 累计微秒 | `/proc/stat` 的数据来源 |
| `get_cpu_iowait_time_us()` | iowait 累计 | — |
| `tick_broadcast_control()` / `tick_broadcast_oneshot_control()` | 广播开关 | cpuidle 驱动会调 |

**三种 tick 模式**（`Documentation/timers/no_hz.rst` 的三节结构）：

| 模式 | Kconfig | 含义 |
|---|---|---|
| **周期 tick** | `CONFIG_HZ_PERIODIC` | 永不省略 ⇒ 极端实时场景反而选它（from-idle 延迟最稳） |
| **dyntick-idle** | `CONFIG_NO_HZ_IDLE` | **idle 时停 tick**，arm64 的默认 |
| **adaptive-tick** | `CONFIG_NO_HZ_FULL` + `nohz_full=` | 只剩一个可运行任务也停 |

⚠️ **`NO_HZ_FULL` 的四个硬约束**（官方文档明确列出）：
① 不能把所有 CPU 都标成 adaptive（**至少留一个做 timekeeping**）；
② 因此系统至少要有 2 个 CPU；③ RCU 回调必须 offload（`CONFIG_RCU_NOCB_CPU`，
`NO_HZ_FULL` 会自动 `select`）；④ **POSIX CPU 定时器会阻止 CPU 进 adaptive 模式**。

---

## §11 `timecounter` / `cyclecounter`：给设备自己的计数器用

头文件：`include/linux/timecounter.h`；实现：`kernel/time/timecounter.c`（3 个导出符号）

| API | 说明 | 备注 |
|---|---|---|
| `timecounter_init(tc, cc, start_tstamp)` | 初始化 | **和 clocksource 无关**——这是给"设备内部有个自己的计数器"用的 |
| `timecounter_read(tc)` | 读并推进，返回纳秒 | ⚠️ **必须周期性调用**，否则计数器绕回就丢时间 |
| `timecounter_cyc2time(tc, cycles)` | 把一个历史 cycle 值换成纳秒 | 网卡硬件时间戳（PTP）的典型用法 |

**典型使用者是网卡和 PTP 驱动**：硬件给数据包打的时间戳是设备计数器的原始值，
用 `timecounter_cyc2time()` 换成系统纳秒。⇒ 网卡驱动那套文档会再遇到它。

---

## §12 `alarmtimer`：能把系统从挂起中唤醒的定时器

实现：`kernel/time/alarmtimer.c`（964 行，10 个导出符号）

| API | 说明 | 备注 |
|---|---|---|
| `alarm_init(alarm, type, function)` | 初始化 | `type` 是 `ALARM_REALTIME` / `ALARM_BOOTTIME` |
| `alarm_start(alarm, start)` / `alarm_start_relative()` | 启动 | — |
| `alarm_restart(alarm)` | 用原到期时间重启 | — |
| `alarm_cancel(alarm)` / `alarm_try_to_cancel()` | 取消（阻塞 / 非阻塞） | 语义对应 hrtimer 那两个 |
| `alarm_forward(alarm, now, interval)` / `alarm_forward_now()` | 推进周期 | 对应 `hrtimer_forward()` |
| `alarm_expires_remaining(alarm)` | 剩余时间 | — |
| `alarmtimer_get_rtcdev()` | 拿到用于唤醒的 RTC 设备 | **没有 RTC 就没有唤醒能力**，只退化成普通 hrtimer |

**它是 hrtimer 之上薄薄一层**：普通 hrtimer 在系统挂起时不走；
alarmtimer 额外把到期时间编程进 RTC ⇒ **能把机器从 suspend 里唤醒**。
用户态对应 `CLOCK_REALTIME_ALARM` / `CLOCK_BOOTTIME_ALARM`。

---

## §13 POSIX 定时器：用户态可见的那一层

实现：`posix-timers.c`(1459) · `posix-cpu-timers.c`(1702) · `posix-clock.c`(320) ·
`posix-stubs.c`(254) · `itimer.c`(403)

⚠️ **这四个文件加起来 4238 行，但导出符号只有 2 个**
（`posix_clock_register()` / `posix_clock_unregister()`，`kernel/time/posix-clock.c`）
⇒ **它整体是系统调用的实现，不是给内核内部用的 API**。

| 用户态接口 | 内核实现落点 | 备注 |
|---|---|---|
| `clock_gettime` / `clock_settime` / `clock_getres` | `posix-timers.c` + `time.c` | arm64 上 `clock_gettime` 大概率走 **vDSO 不进内核**（`kernel/time/vsyscall.c` 负责更新 vDSO 数据） |
| `timer_create` / `timer_settime` / `timer_delete` | `posix-timers.c` | ⚠️ 用户态的 `timer_delete` **和 §3.3 内核的 `timer_delete()` 完全是两回事**，只是同名 |
| `nanosleep` / `clock_nanosleep` | `hrtimer.c` 的 sleeper | `Documentation/timers/hrtimers.rst` 说明这两个已统一到 hrtimer |
| `setitimer` / `getitimer` | `itimer.c` | 老接口 |
| CPU 时间定时器（`CLOCK_*_CPUTIME_ID`） | `posix-cpu-timers.c` | ⚠️ **会阻止 NO_HZ_FULL**（§10） |
| 动态 POSIX 时钟（`/dev/ptpN`） | `posix-clock.c` | **这是 PTP 硬件时钟的接口层** |

⚠️ **同名陷阱汇总**（05 篇有独立一节讲近邻符号）：

| 名字 | 内核内部含义 | 用户态含义 |
|---|---|---|
| `timer_delete` | 删除 `struct timer_list`（§3.3） | 销毁 `timer_create()` 建的 POSIX 定时器 |
| `timer_settime` | 内核里没有这个符号 | POSIX 系统调用 |

---

## §14 错误码语义

### §14.1 全量普查

统计范围：`kernel/time/` 下 18 个非测试 `.c` 文件（显式列文件，命令见 99 §3.3）。

| 错误码 | 次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **61** | 参数不合法：非法 `clock_id`、`timespec` 越界、`nohz_full=` 参数错 |
| `-EFAULT` | **56** | **`copy_to/from_user()` 失败** —— 数量这么高是因为 §13 那一层全是系统调用 |
| `-ENODEV` | **21** | 时钟源/事件设备不存在或不可用 |
| `-EBUSY` | 14 | 设备正在被用作 tick 源，不能解绑 |
| `-EOPNOTSUPP` | 10 | 该时钟不支持这个操作（如对只读时钟 `settime`） |
| `-EPERM` | 8 | **权限**：改系统时间要 `CAP_SYS_TIME` |
| `-EAGAIN` | 7 | 稍后重试（`posix-clock` 的非阻塞路径） |
| `-ENOSYS` | 4 | 编译期未开启对应功能 |
| `-EINTR` | 4 | 被信号打断 |
| `-EACCES` | 3 | — |
| `-ENOMEM` / `-EOVERFLOW` | 1 / 1 | — |
| `-ENOENT` / `-ETIMEDOUT` / `-ERESTARTSYS` / `-ENOTSUPP` | **0** | 本子系统不返回这四个 |

**`-EFAULT`(56) 排第二**是本子系统的一个特征：它是"内核基础设施 + 一大层系统调用"
的混合体，普查数字被系统调用层主导。⇒ **看这张表要分清你在哪一层**：
写驱动只会碰到 §14.2 那几个。

### §14.2 驱动作者实际会拿到的错误码

| 场景 | 返回什么 | 你该怎么办 |
|---|---|---|
| `clocksource_register_hz()` 失败 | `-EBUSY`（同名已注册）/ 计算失败 | 检查 `rating` 和频率参数 |
| `hrtimer_try_to_cancel()` | **`-1` 不是错误码** | `-1` = 回调正在跑；要么重试，要么改用 `hrtimer_cancel()` |
| `mod_timer()` / `add_timer()` | **返回 0/1 不是错误码** | 0 = 之前未 pending，1 = 之前 pending；⚠️ shutdown 后的 0 是"被丢弃"（§3.4） |
| `msleep_interruptible()` | **返回剩余毫秒数** | 非 0 = 被信号打断 |
| `clockevents_update_freq()` | `-ETIME` 类语义由具体驱动定 | — |

⚠️ **本子系统一大类 API 根本不返回错误码**：
`hrtimer_init()`、`hrtimer_start()`、`timer_setup()`、`add_timer()`、
`ktime_get*()` 全是 `void` 或直接返回值 ⇒ **没有失败可言，参数错就是 `WARN` 或 UB**。
03 篇的 Part C 把这条当作设计评价的一项。
