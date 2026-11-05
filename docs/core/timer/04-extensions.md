---
title: 扩展机制
---

# timer 04：子框架

## §0 组织轴

**本篇的组织轴是"一节一个独立子框架，互不调用"。**
它们的共同点只有一个：都住在 `kernel/time/` 下，都架在 01 §1 那四个核心抽象之上。
彼此之间**基本没有调用关系**——所以本篇的章节可以任意跳读。

⚠️ **本篇不讲核心机制**（那在 03 篇）。如果你在找
"hrtimer 怎么到期"、"tick 怎么停"，去 03 篇。

### §0.1 分诊表：哪几个在 arm64 上真会碰到

| # | 子框架 | 文件（行数） | arm64 上会碰到吗 | 谁会碰 |
|---|---|---|---|---|
| §1 | **tick 广播** | `tick-broadcast.c`(1235) + `tick-broadcast-hrtimer.c`(111) | **会** | cpuidle / PSCI 深度休眠 |
| §2 | **alarmtimer** | `alarmtimer.c`(964) | **会，但用户很少** | `timerfd` + 2 个驱动 |
| §3 | **sched_clock** | `sched_clock.c`(296) | **会** | 所有平台定时器驱动 |
| §4 | **timecounter** | `timecounter.c`(99) | **会** | **网卡 / PTP** |
| §5 | **clocksource 看门狗** | 在 `clocksource.c` 内 | **会** | 不用你写，但日志你要认得 |
| §6 | **POSIX 定时器** | `posix-timers.c`(1459) + `posix-cpu-timers.c`(1702) + `itimer.c`(403) + `posix-stubs.c`(254) | **会**（用户态） | 内核代码不直接调 |
| §7 | **posix-clock / PTP** | `posix-clock.c`(320) | 有 PTP 硬件才会 | `drivers/ptp/` |
| §8 | **NTP** | `ntp.c`(1095) | **会**（`adjtimex`） | 不用你写 |
| §9 | **time namespace** | `namespace.c`(467) | 容器场景 | 不用你写 |
| §10 | **`/proc/timer_list`** | `timer_list.c`(360) | **会** | **调试你自己的定时器** |
| §11 | **自测框架** | `clocksource-wdtest.c`(202) + `time_test.c`(99) + `test_udelay.c`(159) | 只在测试内核 | — |
| — | **PROM tree / legacy tick** | `tick-legacy.c`(37) | **不会** | ⇒ §12 说明为什么裁掉 |

⇒ **真正要读懂的只有 §1 / §3 / §4 / §5 / §10**，其余认得出名字就够。

### §0.2 各框架挂在 03 时间线的哪一点

| 子框架 | 挂载点 | 代码 |
|---|---|---|
| jiffies 时钟源 | **`core_initcall`** —— 比其它都早 | `kernel/time/jiffies.c:69` |
| tick 广播（hrtimer 版） | **03 §A5 的 `time_init()` 里**（`tick_setup_hrtimer_broadcast()`） | `arch/arm64/kernel/time.c:62` |
| POSIX 定时器 | `__initcall` | `kernel/time/posix-timers.c:286` |
| `/proc/timer_list` | `__initcall` | `kernel/time/timer_list.c:359` |
| alarmtimer | `device_initcall` | `kernel/time/alarmtimer.c:964` |
| sched_clock 的 syscore（suspend/resume） | `device_initcall` | `kernel/time/sched_clock.c:296` |
| clocksource sysfs | `device_initcall` | `kernel/time/clocksource.c:1480` |
| clockevents sysfs | `device_initcall` | `kernel/time/clockevents.c:777` |
| timekeeping 的 syscore | `device_initcall` | `kernel/time/timekeeping.c:1919` |
| timer sysctl | `device_initcall` | `kernel/time/timer.c:271` |
| 睡眠时间调试 | `late_initcall` | `kernel/time/timekeeping_debug.c:44` |

**一眼能看出的规律**：**sysfs / procfs / syscore 出口全在 `device_initcall`
或更晚**，而核心机制在 03 §A 那批 `start_kernel()` 直接调用里。
⇒ **所以启动早期出问题时，`/proc/timer_list` 和 sysfs 都还不存在**——
07 篇讲早期问题只能靠 `printk`。

---

## §1 tick 广播：深度休眠时谁来叫醒

### §1.1 要解决的问题

**很多 SoC 的本地定时器在深度休眠时会停**。
`clock_event_device` 用 `CLOCK_EVT_FEAT_C3STOP` 这个 feature 位声明这件事
（名字来自 x86 的 C3 电源状态，但**arm64 也用它**）。

**全树声明 `C3STOP` 的文件（9 个）**：

```bash
grep -rlw 'CLOCK_EVT_FEAT_C3STOP' --include=*.c .
```

| 文件 | 说明 |
|---|---|
| `drivers/clocksource/arm_arch_timer.c` | **arm64 的主定时器就会声明它** |
| `drivers/clocksource/clps711x-timer.c` · `mips-gic-timer.c` | 其它平台 |
| `arch/arm/kernel/smp_twd.c` · `arch/x86/kernel/apic/apic.c` · `arch/powerpc/kernel/time.c` · `arch/mips/kernel/cevt-r4k.c` | 各架构的本地定时器 |
| `kernel/time/tick-common.c` · `kernel/time/tick-broadcast.c` | 核心侧的消费者 |

⇒ **7 个驱动/架构声明，2 个核心文件消费**。

### §1.2 解法：一个"不会停"的设备替所有人计时

```
CPU 要进深度休眠
  │
  ├─ cpuidle 调 tick_broadcast_enter()      include/linux/tick.h:99
  │    │
  │    ├─ 把本 CPU 的下一个到期时刻登记到广播设备
  │    └─ 关掉本地 clockevent
  │
  ▼
（本地定时器停了，但广播设备还在跑）
  │
  ├─ 广播设备到期 → 给该醒的 CPU 发 IPI/中断
  │
  └─ cpuidle 调 tick_broadcast_exit()       include/linux/tick.h:103
       └─ 恢复本地 clockevent
```

**消费者是 6 个文件**（口径：`tick_broadcast_enter()` 的调用者）：

| 文件 | arm64 相关 |
|---|---|
| `drivers/cpuidle/cpuidle.c` | **是**——通用 cpuidle 框架 |
| `drivers/firmware/psci/psci_checker.c` | **是**——arm64 的 PSCI 自检 |
| `arch/arm/mach-omap2/cpuidle44xx.c` · `arch/x86/kernel/process.c` · `drivers/acpi/acpi_pad.c` | 否 |
| `kernel/time/tick-broadcast.c` | 核心自己 |

⚠️ **`tick_broadcast_control()` 虽然是导出符号（01 §10），但全树只有
`kernel/time/tick-broadcast.c` 自己在用**——外部一律走 `tick_broadcast_enter()` /
`tick_broadcast_exit()` 这两个 `include/linux/tick.h` 里的 inline 包装。
⇒ **这是一个"导出了但没人直接调"的符号**，数使用者时要按包装函数数，
不能按导出符号数（这条计数口径的教训记在 99 §6）。

### §1.3 两种广播设备

| 类型 | 文件 | 什么时候用 |
|---|---|---|
| 真硬件广播设备 | `tick-broadcast.c` | 平台有一个"永不停"的全局定时器 |
| **hrtimer 模拟的广播设备** | `tick-broadcast-hrtimer.c`(111) | **没有那样的硬件时的兜底**——arm64 装的就是这个（`arch/arm64/kernel/time.c:62`） |

**hrtimer 版广播是个巧妙的循环**：它用**另一个 CPU 上还活着的 hrtimer**
来替休眠的 CPU 计时。⇒ 前提是**至少有一个 CPU 不进深度休眠**，
这和 `NO_HZ_FULL` 要"留一个 CPU 做 timekeeping"是同一类约束（01 §10）。

---

## §2 alarmtimer：唯一能穿过 suspend 的定时器

### §2.1 它比 hrtimer 多了什么

**hrtimer 在系统 suspend 时不走**（时钟源都停了）。
alarmtimer 在 hrtimer 之上加一件事：**把到期时间也编程进 RTC**
⇒ RTC 的中断能把整机唤醒。

| 层 | 作用 |
|---|---|
| 上层 | 一个普通 `hrtimer`（系统醒着时就靠它） |
| 下层 | **RTC 设备**（`alarmtimer_get_rtcdev()`，01 §12） |

⚠️ **没有 RTC 就没有唤醒能力**——`alarmtimer_get_rtcdev()` 返回 NULL 时
alarmtimer 退化成普通 hrtimer，**suspend 期间不会唤醒**，也不会报错。

### §2.2 全树只有 2 个真消费者

口径：**`#include <linux/alarmtimer.h>` 的 `.c` 文件**（这个口径比按函数名 grep 准，
见下面的 ⚠️）：

```bash
grep -rln 'linux/alarmtimer.h' --include=*.c .
```

| 文件 | 用途 |
|---|---|
| `fs/timerfd.c` | **`timerfd_create(CLOCK_REALTIME_ALARM, ...)`** —— 用户态的主要入口 |
| `net/netfilter/xt_IDLETIMER.c` | iptables 的空闲计时目标 |
| — `kernel/time/alarmtimer.c` | 实现本身 |

⚠️ **按函数名 grep 会虚高到 9 个文件**：
`grep -rlwE 'alarm_init|alarm_start'` 会命中 nouveau 的 5 个文件，
但那是 **nouveau 自己 `struct nvkm_timer_func` 里的 `alarm_init` 函数指针字段**
（`drivers/gpu/drm/nouveau/nvkm/subdev/timer/nv04.c:143` 是赋值点，
`base.c:81` 和 `:132` 是调用点），**和内核 alarmtimer 毫无关系**。
⇒ **同名符号是计数虚高最常见的来源**，这条记进 99 §6。

⇒ **结论：alarmtimer 是一个"存在但极少被内核代码直接用"的框架**，
它的价值几乎全部通过 `timerfd` 暴露给用户态。

---

## §3 sched_clock：76 个驱动都要实现它

### §3.1 定位

**它不是 clocksource 的替代，是 clocksource 的"快速近似版"**
（`Documentation/timers/timekeeping.rst` 有一整节讲区别，01 §9 摘了要点）：

| | clocksource | sched_clock |
|---|---|---|
| 追求 | **准** | **快** |
| 调用频率 | 中 | **极高**（调度器每次都调） |
| 必须 NMI-safe | 否 | **是** |
| 挂起时可以停吗 | 不希望 | **可以**（它只服务调度） |
| 谁读它 | timekeeper | **调度器 + printk 时间戳 + ftrace** |

### §3.2 使用统计：76 个文件

```bash
grep -rlw 'sched_clock_register' --include=*.c . | wc -l    # → 76
```

**对比一下三个"驱动侧注册函数"的普及度**：

| 注册函数 | 全树 `.c` 文件数 |
|---|---|
| `clocksource_register_hz()` | **88** |
| `clockevents_config_and_register()` | **87** |
| `sched_clock_register()` | **76** |

⇒ **三者数量接近，说明绝大多数平台定时器驱动会把三个都注册**——
和 02 §1.0.F 的结论一致（arm64 上就是 `arm_arch_timer.c` 一个文件全干了）。

### §3.3 它自己也要处理绕回

`sched_clock.c` 用一个 `struct clock_read_data`（`sched_clock_read_begin()`，
`include/linux/sched_clock.h:36`）+ 序列号协议做无锁读，
并且**用一个周期性 hrtimer 定期"翻页"**，防止窄计数器绕回丢时间。
⇒ 又一处"框架自己用了框架"的循环（03 §C3 讲的那类）。

它还注册了 `syscore_ops` 处理 suspend/resume（`kernel/time/sched_clock.c:296`）。

---

## §4 timecounter / cyclecounter：给设备自己的计数器

### §4.1 它和 clocksource 完全无关

⚠️ **最容易搞错的一点**：`timecounter` 看起来像 clocksource 的兄弟，
其实**它不参与系统计时**。它是给"**设备内部有个自己的硬件计数器，
我要把它的读数换成系统纳秒**"这个场景用的。

| | clocksource | timecounter |
|---|---|---|
| 服务谁 | **系统时间** | **单个设备** |
| 注册到核心吗 | **要**（全局链表 + 选优） | **不要**（驱动自己持有一个实例） |
| 谁推进它 | timekeeper 在 tick 里 | **驱动自己周期性调 `timecounter_read()`** |

⚠️ **`timecounter_read()` 必须被周期性调用**，否则设备计数器绕一圈就丢时间——
这个责任在驱动身上，框架不管。

### §4.2 使用者分布：网卡占绝对多数

```bash
grep -rlw 'timecounter_init' --include=*.c . | xargs -n1 dirname | sort | uniq -c | sort -rn
```

**26 个文件**，按目录：

| 目录 | 文件数 | 说明 |
|---|---|---|
| `drivers/net/ethernet/**` | **大多数** | **PTP 硬件时间戳**（e1000e ×2、xgbe ×2、ti、qede、ionic、mlxsw、mlx5 …） |
| `drivers/ptp/` | 1 | PTP 核心 |
| `sound/hda` | 1 | 音频也要精确时间戳 |
| `kernel/time/` | 1 | 实现本身 |

⇒ **这是 timer 子系统和网卡驱动的接口点**——
网卡给数据包打的硬件时间戳是设备计数器原始值，靠 `timecounter_cyc2time()`
换成系统纳秒。**网卡驱动那套文档会再遇到这三个函数。**

---

## §5 clocksource 看门狗：怎么发现时钟源坏了

### §5.1 机制

**两个时钟源互相验证**：拿一个"公认可靠但可能较慢"的时钟源当看门狗，
定期和当前使用的时钟源对读，差值超过容差就把当前的判为不稳定。

| 组件 | 位置 |
|---|---|
| 看门狗时钟源 | `watchdog`（`kernel/time/clocksource.c:133`） |
| 驱动它的定时器 | `watchdog_timer`（`kernel/time/clocksource.c:134`）——**是个 `struct timer_list`** |
| 失败后的处理 | `clocksource_watchdog_work()`（`kernel/time/clocksource.c:160`）走 workqueue |
| 容差 | `uncertainty_margin`（02 §5.6）+ `CONFIG_CLOCKSOURCE_WATCHDOG_MAX_SKEW_US`（`kernel/time/Kconfig:199`） |
| 每 CPU 一致性校验 | `clocksource_verify_percpu()`（`include/linux/clocksource.h:307`） |

**看门狗自己用低精度 `timer_list`** 这件事值得注意：它不需要高精度，
而且必须在"高精度可能已经坏了"的情况下还能工作 ⇒ **刻意用更底层的机制**。

### §5.2 判定失败后会做什么

1. 把该时钟源标为 `CLOCK_SOURCE_UNSTABLE`
2. **降低它的 `rating` 并触发重新选优**（`clocksource_select_watchdog()`，
   `kernel/time/clocksource.c:613`）
3. **打日志** ⇒ 07 篇会给出这些日志的原文和含义

⇒ **这是本子系统最重要的"自动降级"机制**：
时钟源出问题时系统不会崩，而是退回到一个更差但可信的时钟源。
代价是**时间精度突然变差**，而且如果不看日志你不会知道。

### §5.3 它有专门的自测模块

`kernel/time/clocksource-wdtest.c`(202) 是一个 `module_init` 的测试模块
（`kernel/time/clocksource-wdtest.c:201`），故意注册坏时钟源来验证看门狗会抓到。
⇒ 只在测试内核里编。

---

## §6 POSIX 定时器层：4238 行，2 个导出符号

### §6.1 组成

| 文件 | 行数 | 职责 |
|---|---|---|
| `posix-timers.c` | 1459 | `timer_create` / `timer_settime` / `timer_delete` 系统调用 |
| `posix-cpu-timers.c` | 1702 | **CPU 时间定时器**（`CLOCK_*_CPUTIME_ID`） |
| `itimer.c` | 403 | 老的 `setitimer` / `getitimer` |
| `posix-stubs.c` | 254 | `CONFIG_POSIX_TIMERS=n` 时的空实现 |

**导出符号只有 2 个**（都在 `posix-clock.c`，见 §7）
⇒ **这一层整体是系统调用实现，不是内核内部 API**（01 §13 已强调）。

### §6.2 ⚠️ 它是本子系统设计债最集中的地方

03 §C1 统计的 6 条自陈债里，**3 条在 `posix-timers.c`**：

| 位置 | 内容 |
|---|---|
| `kernel/time/posix-timers.c:147` | `FIXME: Replace this by a per signal struct xarray` |
| `kernel/time/posix-timers.c:347` | `FIXME: if ->sigq is queued we can race with …` |
| `kernel/time/posix-timers.c:396` | `FIXME: What we really want, is to stop this …` |

⇒ **50% 的自陈债集中在 12% 的代码里**（`posix-timers.c` 1459 行 / 总 22995 行）。
原因很清楚：它要同时满足 POSIX 规范、信号语义和向后兼容，
自由度比核心那几层小得多。

### §6.3 ⚠️ CPU 时间定时器会阻止 NO_HZ_FULL

`Documentation/timers/no_hz.rst` 明确列为 `NO_HZ_FULL` 的四个约束之一
（01 §10）：**POSIX CPU 定时器需要 tick 来累计 CPU 时间**，
所以有它在的 CPU 进不了 adaptive-tick 模式。

缓解手段是 `CONFIG_POSIX_CPU_TIMERS_TASK_WORK`（`kernel/time/Kconfig:56`）——
把 CPU 定时器的到期处理搬到 task work 里，减少对 tick 的依赖。
⚠️ 它需要架构支持（`HAVE_POSIX_CPU_TIMERS_TASK_WORK`，`kernel/time/Kconfig:53`）。

---

## §7 posix-clock：PTP 硬件时钟的接口层

**它是本子系统唯一一个"给驱动注册用"的用户态时钟框架**：

| API | 位置 |
|---|---|
| `posix_clock_register()` | `kernel/time/posix-clock.c:180` |
| `posix_clock_unregister()` | `kernel/time/posix-clock.c:192` |

**全树只有 1 个消费者**：

```bash
grep -rlw 'posix_clock_register' --include=*.c .
# → drivers/ptp/ptp_clock.c
#   kernel/time/posix-clock.c   （实现本身）
```

⇒ **`drivers/ptp/ptp_clock.c` 是唯一使用者**，它把每个 PTP 硬件时钟
暴露成 `/dev/ptpN`，用户态可以对它 `clock_gettime()` / `clock_adjtime()`。
这就是 §4 的 `timecounter` 和用户态之间的桥。

---

## §8 NTP：1095 行，0 个导出符号

`kernel/time/ntp.c` 实现 `adjtimex()` 背后的时钟驯服算法（PLL/FLL）。

| 特征 | 说明 |
|---|---|
| 导出符号 | **0 个** ⇒ 完全内部 |
| 它改什么 | **timekeeper 的 `mult`**（03 §B4） |
| 安全上界 | `maxadj`（02 §5.6）限制单次调整幅度 |
| 唯一的自陈债 | `kernel/time/ntp.c:882` 的 `TODO: test various filters` |
| 硬件辅助 | `hardpps()`（`kernel/time/timekeeping.c:2502`）—— PPS 信号驯服 |

⚠️ **NTP 只调 mono/real，不调 raw**（03 §B4）
⇒ 想看"硬件真实速率"用 `ktime_get_raw()`。

---

## §9 time namespace：容器里的时间

`kernel/time/namespace.c`(467) 实现 `CLONE_NEWTIME`。

| 特征 | 说明 |
|---|---|
| 导出符号 | **0 个** |
| 只影响**三个**时基 | **`CLOCK_MONOTONIC` · `CLOCK_BOOTTIME` · `CLOCK_BOOTTIME_ALARM`**（`kernel/time/namespace.c:23-58` 的 `switch` 就这三个 `case`）——**`CLOCK_REALTIME` 不能虚拟化** |
| 实现方式 | 每个 namespace 一组偏移（`struct timens_offsets`），
在 `do_timens_ktime_to_host()`（`kernel/time/namespace.c:23`）里加减 |
| vDSO 交互 | **namespace 里的进程要走特殊的 vDSO 页**，否则会读到宿主的时间 |

⇒ **arm64 上容器场景才会碰到**，普通驱动开发完全无关。

---

## §10 `/proc/timer_list`：调试自己定时器的主要出口

| 特征 | 位置 |
|---|---|
| 创建 | `proc_create_seq_private("timer_list", 0400, ...)`（`kernel/time/timer_list.c:353`） |
| **权限是 `0400`** | ⚠️ **只有 root 能读** |
| 挂载点 | `__initcall`（`kernel/time/timer_list.c:359`） |
| 内容 | 每 CPU 的所有 hrtimer + tick 设备状态 + `nr_events`/`nr_retries`/`nr_hangs`（03 §B6） |
| SysRq 版本 | `sysrq_timer_list_show()`（`kernel/time/timer_list.c:265`）—— **SysRq 也能触发同样的输出** |

**`sysrq_timer_list_show()` 的价值**：系统已经卡住、`/proc` 读不动时，
SysRq 是唯一还能拿到定时器状态的路。⇒ 07 篇给出具体按键和输出解读。

⚠️ **它只列 hrtimer，不列 `timer_list`**——时间轮里的低精度定时器
在这里看不到。这是本子系统可观测性不均衡（03 §C4）的又一处体现。

---

## §11 自测框架

| 文件 | 行数 | 测什么 | 怎么开 |
|---|---|---|---|
| `clocksource-wdtest.c` | 202 | 看门狗能不能抓到坏时钟源 | `module_init`（`:201`） |
| `time_test.c` | 99 | 时间换算的 KUnit 测试 | `CONFIG_TIME_KUNIT_TEST`（`kernel/time/Kconfig:67`） |
| `test_udelay.c` | 159 | `udelay()` 准不准 | `module_init`（`:147`） |

⇒ **三个都是可选模块**，生产内核不编。但 `test_udelay.c` 在移植新平台时
值得跑一次——它能验证 03 §A5 那个 `lpj_fine` 算对了没有。

---

## §12 汇总表

### §12.1 框架 × 职责 × 挂载点 × arm64 相关

| 框架 | 职责 | 挂载点 | arm64 |
|---|---|---|---|
| tick 广播 | 深度休眠时代为计时 | `time_init()` 内（`arch/arm64/kernel/time.c:62`） | **相关** |
| alarmtimer | 穿过 suspend 的定时器 | `device_initcall`（`kernel/time/alarmtimer.c:964`） | 相关（`timerfd`） |
| sched_clock | 调度器/printk 的快时钟 | 驱动在 `time_init()` 里注册 | **相关** |
| timecounter | 设备自有计数器换算 | 无全局挂载点，驱动自持 | 相关（网卡） |
| clocksource 看门狗 | 自动降级坏时钟源 | 随 clocksource 注册启动 | **相关** |
| POSIX 定时器 | 用户态定时器 | `__initcall`（`kernel/time/posix-timers.c:286`） | **相关** |
| posix-clock | PTP 时钟设备 | 由 `drivers/ptp/` 调 | 有 PTP 才相关 |
| NTP | 驯服时钟 | 随 timekeeper | **相关** |
| time namespace | 容器时间虚拟化 | — | 容器才相关 |
| `/proc/timer_list` | 观测 hrtimer | `__initcall`（`kernel/time/timer_list.c:359`） | **相关** |
| 自测框架 | 验证 | `module_init` | 测试内核 |
| `tick-legacy.c` | 老式周期 tick | `CONFIG_LEGACY_TIMER_TICK`（`kernel/time/Kconfig:60`） | **不相关**（arm64 用 `GENERIC_CLOCKEVENTS`） |

### §12.2 每个框架的全树使用统计（每条都能重跑）

| 框架 | 口径 | 数值 | 重跑命令 |
|---|---|---|---|
| clocksource | 注册函数的调用文件数 | **88** | `grep -rlw 'clocksource_register_hz' --include=*.c . \| wc -l` |
| clockevent | 同上 | **87** | `grep -rlw 'clockevents_config_and_register' --include=*.c . \| wc -l` |
| sched_clock | 同上 | **76** | `grep -rlw 'sched_clock_register' --include=*.c . \| wc -l` |
| timecounter | `timecounter_init` 文件数 | **26** | `grep -rlw 'timecounter_init' --include=*.c . \| wc -l` |
| timecounter（读侧） | `timecounter_read` 文件数 | **23** | `grep -rlw 'timecounter_read' --include=*.c . \| wc -l` |
| C3STOP 声明者 | feature 位的使用文件数 | **9**（含 2 个核心文件） | `grep -rlw 'CLOCK_EVT_FEAT_C3STOP' --include=*.c . \| wc -l` |
| tick 广播 | `tick_broadcast_enter()` 调用文件数 | **6**（含 1 个核心文件） | `grep -rlw 'tick_broadcast_enter' --include=*.c . \| wc -l` |
| alarmtimer | ⚠️ **`#include <linux/alarmtimer.h>` 的文件数** | **3**（含实现本身 ⇒ 真消费者 **2**） | `grep -rln 'linux/alarmtimer.h' --include=*.c . \| wc -l` |
| posix-clock | `posix_clock_register` 文件数 | **2**（含实现 ⇒ 真消费者 **1**） | `grep -rlw 'posix_clock_register' --include=*.c . \| wc -l` |
| `/proc/timer_list` | — | 无"使用者"概念，是个 procfs 出口 | — |

⚠️ **两条口径警告**（详见 99 §6）：
1. **alarmtimer 不能按函数名数**：`grep -rlwE 'alarm_init|alarm_start'` 得到 **9**，
   但其中 5 个是 nouveau 自己 `struct nvkm_timer_func` 的同名字段 ⇒ **虚高 3 倍**
2. **tick 广播不能按导出符号数**：`tick_broadcast_control()` 全树只有核心自己调，
   真消费者走 `include/linux/tick.h:99` 的 inline 包装

### §12.3 裁掉了什么

| 裁掉的 | 为什么 |
|---|---|
| `Documentation/timers/hpet.rst`（30 行） | **HPET 是 x86 专属硬件**，arm64 没有 |
| `tick-legacy.c`（37 行） | `CONFIG_LEGACY_TIMER_TICK` 给还没转 clockevent 的架构用；arm64 走 `GENERIC_CLOCKEVENTS`（`kernel/time/Kconfig:30` 的 `def_bool !LEGACY_TIMER_TICK`） |
| `arch/x86` / `arch/powerpc` 的 C3STOP 实现 | 非目标架构（§1.1 的表里点名但不展开） |
| `CONFIG_OF_PROMTREE` 相关 | SPARC 专属 |
| NTP 算法的数学细节 | **超出本套范围**——本篇只讲它改 `mult` 这个接口面（99 §5.2 登记为二等证据） |
