---
title: 内部实现
---

# timer 03：内部机制逐段剖析

## §0 导航

**本篇只承诺一条主轴：Part A 是启动到运行的时间线。**
不按时间线组织的内容全部划到 Part B（横切机制）和 Part C（设计评价），
章节号带 `A`/`B`/`C` 前缀，引用时自带"在哪条轴上"的信息。

### §0.1 分层图

```
  ┌─ 消费者 ──────────────────────────────────────────────┐
  │  ktime_get()   hrtimer_start()   mod_timer()  msleep() │
  └───────────────────────────────────────────────────────┘
                            ▲
  ┌─ 核心 ────────────────────┴───────────────────────────┐
  │  timekeeper ── hrtimer ── timer wheel                 │
  │       │           │            ▲                      │
  │       │           ▼            │ 靠 tick 推            │
  │       │      ┌─ tick 层 ───────┘                      │
  │       │      │  周期 / 单次 / NO_HZ                    │
  │       │      │  tick_sched.sched_timer 本身是 hrtimer │
  │       ▼      ▼                                        │
  │  clocksource   clock_event_device                     │
  └───────────────────────────────────────────────────────┘
                            ▲
  ┌─ 驱动 ────────────────────┴───────────────────────────┐
  │  drivers/clocksource/arm_arch_timer.c（arm64）         │
  └───────────────────────────────────────────────────────┘
```

### §0.2 生命周期时间线（Part A 的骨架）

```
start_kernel()                                        init/main.c
  │
  ├─ tick_init()               :1057   只建 sysfs/广播骨架，还没有设备
  ├─ init_timers()             :1059   每 CPU 时间轮 + 注册 TIMER_SOFTIRQ
  ├─ hrtimers_init()           :1061   每 CPU 8 个 clock_base + HRTIMER_SOFTIRQ
  ├─ softirq_init()            :1062
  ├─ timekeeping_init()        :1063   用 jiffies 时钟源（rating=1）先跑起来
  ├─ time_init()               :1064   架构相关：arm64 在这里 probe 真硬件
  │    │                                    arch/arm64/kernel/time.c:55-72
  │    ├─ of_clk_init(NULL)
  │    ├─ timer_probe()   ─────────────► clocksource_register_hz()
  │    │                                  clockevents_config_and_register()
  │    │                                       │
  │    │                                       ├─► clocksource_select()  换掉 jiffies
  │    │                                       └─► tick_check_new_device() 分配职责
  │    ├─ tick_setup_hrtimer_broadcast()
  │    └─ lpj_fine = arch_timer_rate / HZ   udelay 不用标定循环了
  │
  ├─ late_time_init()          :1114-1115
  └─ sched_clock_init()        :1116
       │
       ▼  （此时仍是低精度周期 tick）
  每个 tick: update_process_times() → run_local_timers()
                                        └─ hrtimer_run_queues()
                                             └─ tick_check_oneshot_change()?
                                                  └─ hrtimer_switch_to_hres()  懒切换
       │
       ▼
  高精度 + NO_HZ 模式（稳态）
       │
       ├─ idle: tick_nohz_idle_enter() → tick_nohz_stop_tick()
       └─ 唤醒: tick_nohz_idle_exit()
```

### §0.3 哪些内容不在本篇

| 你想找 | 去哪 |
|---|---|
| 某个 API 怎么调 | 01 篇 |
| 结构体有哪些字段 | 02 §5 |
| 四个抽象的区别 | 02 §1.0 |
| 子框架（广播 / alarmtimer / POSIX） | 04 篇 |
| 驱动代码骨架 | 05 篇 |
| DT 怎么写 | 06 篇 |
| 怎么观测这些机制 | 07 篇 |
| 大白话解释 | 08 篇 |
| 哪些结论没验证过 | 99 §5 |

---

# Part A：启动到运行的时间线

## §A1 `tick_init()`（`kernel/time/tick-common.c:560`）

**它几乎什么都不做**——只有两件事：
建 clockevents 的 sysfs 骨架，和初始化广播机制的数据结构。

⚠️ **此时系统里一个 `clock_event_device` 都还没有**。
tick 层是"等设备来注册"的被动方，这一点决定了后面 §A5 那条注册触发链的形状。

## §A2 `init_timers()`（`kernel/time/timer.c:2306`）

两件事：
1. 初始化本 CPU 的 `timer_bases[NR_BASES]`（`kernel/time/timer.c:216`）——
   两个时间轮，`BASE_STD` 和 `BASE_DEF`
2. `open_softirq(TIMER_SOFTIRQ, run_timer_softirq)`

⇒ **`timer_list` 从这一刻就可用了**，而且**不需要任何硬件**——
因为它靠 tick 推进，而 tick 早期由架构的周期中断提供。

## §A3 `hrtimers_init()`（`kernel/time/hrtimer.c:2301`）

1. 初始化本 CPU 的 `hrtimer_cpu_base`，含 **8 个 `clock_base`**（02 §5.4）
2. `open_softirq(HRTIMER_SOFTIRQ, hrtimer_run_softirq)`

⚠️ **此刻 hrtimer 可用，但只有 tick 粒度的精度**——
`hres_active` 还是 0，没有硬件可以编程到任意时刻。
`Documentation/timers/highres.rst` 明确说了这一点：
"Up to the point where hrtimers are initialized, the system works in
the usual low resolution periodic mode."

## §A4 `timekeeping_init()`（`kernel/time/timekeeping.c:1632`）—— 先用最差的时钟源跑起来

**本篇最反直觉的一步**：`timekeeping_init()` 在 `time_init()` **之前**跑
（`init/main.c:1063` vs `:1064`），也就是说**它初始化的时候，真实硬件时钟源还不存在**。

解法是有一个"永远可用的兜底时钟源"：

```bash
sed -n '32,40p' kernel/time/jiffies.c
```

| 字段 | 值 | 含义 |
|---|---|---|
| `.name` | `"jiffies"` | `kernel/time/jiffies.c:33` |
| `.rating` | **1** | `kernel/time/jiffies.c:34`，注释写着 `lowest valid rating` |
| `.mask` | `CLOCKSOURCE_MASK(32)` | `kernel/time/jiffies.c:37` |

**`clocksource_default_clock()` 是 `__weak` 的**
（`kernel/time/jiffies.c:71`）⇒ 架构可以覆盖它，arm64 没有覆盖，所以用 jiffies。

`timekeeping_init()` 在 `kernel/time/timekeeping.c:1661` 调它，
然后 `tk_setup_internals(tk, clock)` 把 `mult`/`shift`/`mask` 填进 `tkr_mono`。

⇒ **所以启动早期 `ktime_get()` 就能用，只是精度只有 `1/HZ`**。
这解释了两件事：
- 为什么早期 printk 时间戳很粗
- 为什么 `rating` 的最低有效值是 1 而不是 0（要留一个"比什么都强"的档位）

**另外还有个 `refined_jiffies`**（`kernel/time/jiffies.c:86-87`），
把 rating 加 1 变成 2，用更准的 `NSEC_PER_JIFFY` 估计值 ⇒ 仍然是兜底档。

## §A5 `time_init()`：arm64 在这里接上真硬件

**这一步是架构相关的**，arm64 的实现只有 18 行
（`arch/arm64/kernel/time.c:55-72`），但每一行都有讲头：

| 行 | 做什么 | 为什么重要 |
|---|---|---|
| `of_clk_init(NULL)` | 初始化 DT 里声明的时钟 | **定时器要先有时钟输入** |
| `timer_probe()` | **遍历 `TIMER_OF_DECLARE()` 注册的定时器驱动** | 这一步真正建立 clocksource + clockevent |
| `tick_setup_hrtimer_broadcast()` | 装一个基于 hrtimer 的广播设备 | 04 篇讲广播 |
| `arch_timer_get_rate()` + `panic()` | 拿不到频率直接 panic | **arm64 上没有架构定时器就起不来** |
| `lpj_fine = arch_timer_rate / HZ` | **直接算出 loops-per-jiffy** | **不做标定循环** |

**`lpj_fine` 那一行解决了 01 §5.1 提的那个坑**：
`udelay()` 靠 `loops_per_jiffy` 估算，而这个值在变频 CPU 上标定不准。
arm64 直接用架构定时器的固定频率算出来 ⇒ **`udelay()` 在 arm64 上比 x86 可靠**，
因为架构定时器的频率不随 CPU 调频变化。

### §A5.1 注册触发的两条链

`timer_probe()` 里驱动调的两个注册函数各自触发一串动作：

```
clocksource_register_hz(cs, hz)                include/linux/clocksource.h:244
  └─ __clocksource_register_scale()
       ├─ 算 mult/shift/max_idle_ns
       ├─ 挂进全局 clocksource 链表
       └─ clocksource_select()                 kernel/time/clocksource.c:130
            └─ __clocksource_select()          kernel/time/clocksource.c:990
                 ├─ clocksource_find_best()    kernel/time/clocksource.c:968
                 │    就是按 rating 挑最大的那个
                 └─ timekeeping_notify()
                      通知 timekeeper 换时钟源
                      （arm64 arch timer 的 clocksource rating 是 400，
                      见 drivers/clocksource/arm_arch_timer.c:235 ≫ jiffies 的 1）
```

```
clockevents_config_and_register(dev, ...)      include/linux/clockchips.h:186
  ├─ 算 mult/shift/min_delta_ns/max_delta_ns
  └─ clockevents_register_device()
       └─ tick_check_new_device(newdev)        kernel/time/tick-common.c:326
            决定这个设备干什么活
            └─ tick_setup_device()             kernel/time/tick-common.c:185
                 （从 :352 或 :268 进来）
                 ├─ 第一个设备 ⇒ 承担周期 tick（jiffies 更新）
                 └─ 后续设备 ⇒ 每 CPU 的本地事件源
```

**两条链的对称性**：都是"注册 → 算换算因子 → 挂上去 → 触发一次重新选择"。
差别在于 clocksource 选**一个最好的**，clockevent 是**给每个都分配职责**。

## §A6 切到高精度模式：一次懒切换

⚠️ **切换不发生在注册的时候**，而是发生在**下一次低精度 tick** 里：

```
run_local_timers()                             kernel/time/timer.c:2043
  └─ hrtimer_run_queues()                      kernel/time/hrtimer.c:1938
       ├─ if (hrtimer_hres_active()) return;   已经是高精度就不用管
       │
       ├─ if (tick_check_oneshot_change(!hrtimer_is_hres_enabled()))
       │        kernel/time/tick-sched.c:1608
       │        问 tick 层：现在能不能切到单次模式？
       │    │
       │    └─ hrtimer_switch_to_hres()        kernel/time/hrtimer.c:1955 → :756
       │         ├─ cpu_base->hres_active = 1
       │         ├─ hrtimer_resolution = HIGH_RES_NSEC
       │         └─ tick_init_highres()
       │
       └─ __hrtimer_run_queues(..., HRTIMER_ACTIVE_HARD)   :1968
```

**为什么要懒切换**（`Documentation/timers/highres.rst` 给的理由）：
必须先确认**注册进来的 clocksource 和 clockevent 都够用**
（时钟源连续、事件设备支持单次模式）才能切；
而这两件事只有在都注册完之后才能判断。⇒ 用"下一次 tick 时再问一遍"代替复杂的通知编排。

⚠️ **`CONFIG_HIGH_RES_TIMERS=n` 时 `hrtimer_switch_to_hres()` 是空函数**
（`kernel/time/hrtimer.c:776`）⇒ 永远留在低精度模式，`hrtimer_resolution` 保持 `1/HZ`。

## §A7 稳态运行

三条独立的路：

| 路 | 入口 | 频率 |
|---|---|---|
| 读时间 | `ktime_get()` 一族 | 极高，无锁（§B1） |
| hrtimer 到期 | 硬件中断 → `hrtimer_interrupt()` | 按需 |
| timer wheel 到期 | tick → `run_local_timers()` | 每 tick 检查一次 |

**`hrtimer_interrupt()` 和 `hrtimer_run_queues()` 是两个入口**：
前者是高精度模式下的硬件中断路径（`kernel/time/hrtimer.c:1873` 跑 HARD 队列），
后者是低精度模式下由 tick 驱动的路径（`:1968` 也跑 HARD 队列）。
⇒ **同一套红黑树，两个驱动源**，取决于 `hres_active`。

## §A8 NO_HZ：停 tick 与恢复

```
CPU 进 idle
  │
  ├─ tick_nohz_idle_enter()          kernel/time/tick-sched.c:1168
  │    只做记账（ts->inidle = 1），还没停
  │
  ├─ tick_nohz_idle_stop_tick()      kernel/time/tick-sched.c:1148
  │    │
  │    ├─ tick_nohz_next_event(ts, cpu)     kernel/time/tick-sched.c:806
  │    │    │  算"下一个要做的事在什么时候"
  │    │    ├─ 问 timer wheel: get_next_timer_interrupt()
  │    │    ├─ 问 hrtimer:     hrtimer_get_next_event()
  │    │    └─ 检查 local_timer_softirq_pending()
  │    │            kernel/time/tick-sched.c:800（本树改动点）
  │    │            有未处理的 TIMER_SOFTIRQ 就不能停
  │    │
  │    └─ tick_nohz_stop_tick(ts, cpu)      kernel/time/tick-sched.c:889
  │         ├─ ts->tick_stopped = 1
  │         └─ 把 clockevent 编程到那个"下一个事件"时刻
  │
  ▼
（可能睡几毫秒到几秒，期间没有 tick）
  │
  ├─ 中断/事件唤醒
  │
  └─ tick_nohz_idle_exit()           kernel/time/tick-sched.c:1348
       ├─ 补偿 jiffies（一次跳过去，02 §1.0.D 那个坑的来源）
       └─ 恢复周期 tick

cpuidle 还会问一句"我能睡多久"：
  tick_nohz_get_sleep_length()       kernel/time/tick-sched.c:1240
```

⚠️ **`local_timer_softirq_pending()` 就是本树 4 处改动之一**
（`kernel/time/tick-sched.c:800`）：上游读 `local_softirq_pending()`，
本树读 `local_pending_timers()`。
**为什么必须改**：RT 上 timer softirq 的 pending 位不在普通 softirq 掩码里，
而在 `pending_timer_softirq` 这个独立的 per-CPU 变量里（§B2）⇒
不改的话 RT 内核会认为"没有待处理的定时器 softirq"而错误地停掉 tick。

## §A9 拆除路径

| 场景 | 入口 | 做什么 |
|---|---|---|
| CPU 下线 | `hrtimers_cpu_dying()` / `timers_dead_cpu()` | **把该 CPU 的定时器迁到别的 CPU** |
| 迁移中转 | `migration_cpu_base`（`kernel/time/hrtimer.c:149`） | 迁移过程中 timer 的临时归属，防止 `hrtimer_cancel()` 看到半个状态 |
| clocksource 注销 | `clocksource_unregister()` | 会触发重新选优；正在用的那个不能直接摘 |
| clockevent 解绑 | `clockevents_unbind_device()` | 正在当 tick 源的会返回 `-EBUSY`（01 §14.1 那 14 个 `-EBUSY` 的主要来源） |
| suspend/resume | `timekeeping_suspend()` / `_resume()`（`syscore_ops`，`kernel/time/timekeeping.c:1909`） | 挂起期用 `suspend_clocksource` 计时补偿 |

**`migration_cpu_base` 是个值得注意的设计**：它是一个**假的 `hrtimer_cpu_base`**，
CPU 下线迁移定时器时先把 `timer->base` 指向它。这样并发的 `hrtimer_cancel()`
会发现 base 是 migration base 从而知道"正在迁移，等一下"，
而不需要为迁移单独加一把锁。

---

# Part B：横切机制

## §B1 两套同步策略，各有各的理由

### §B1.1 读时间用 seqcount，不用锁

```
读侧（ktime_get，kernel/time/timekeeping.c:854）
  do {
      seq = read_seqcount_begin(&tk_core.seq);
      base  = tk->tkr_mono.base;
      nsecs = timekeeping_get_ns(&tk->tkr_mono);
  } while (read_seqcount_retry(&tk_core.seq, seq));

写侧（tick 里更新 timekeeper）
  raw_spin_lock_irqsave(&timekeeper_lock, flags);     kernel/time/timekeeping.c:44
  write_seqcount_begin(&tk_core.seq);
      ... 改 shadow_timekeeper ...                    kernel/time/timekeeping.c:57
      memcpy(&tk_core.timekeeper, &shadow_timekeeper, ...)
  write_seqcount_end(&tk_core.seq);
  raw_spin_unlock_irqrestore(&timekeeper_lock, flags);
```

**三个设计决定，每个都有明确理由**：

| 决定 | 理由 |
|---|---|
| 读侧无锁重试 | **读的频率极高**（每次 `ktime_get()`），且**读侧绝对不能阻塞写侧**——否则 tick 更新会被拖住 |
| 用 `shadow_timekeeper` 先改再拷 | **缩短写侧持有 seqcount 的窗口**：复杂的 NTP 调整都在影子副本上做完，最后只做一次 `memcpy` ⇒ 读侧重试概率大幅下降 |
| `timekeeper_lock` 是 `raw_spinlock_t` | suspend/resume 路径在关中断下走，不能用可睡的锁 |

⚠️ **seqcount 在 NMI 里会死循环**：如果 NMI 打在写侧持有 seqcount 的中间，
读侧重试永远看到"正在写"。⇒ **这就是 `ktime_get_mono_fast_ns()` 存在的原因**：
它读 `tk_fast_mono`（`kernel/time/timekeeping.c:105`），那是一个**双缓冲快照**，
永远有一份是完整的。01 §6.3 讲了什么时候该用它。

### §B1.2 定时器队列用每 CPU `raw_spinlock`

| 锁 | 为什么是每 CPU | 为什么是 `raw_` |
|---|---|---|
| `timer_base.lock`（`kernel/time/timer.c:200`） | 定时器天然绑 CPU，跨 CPU 操作是少数 | **softirq/中断上下文要拿它** |
| `hrtimer_cpu_base.lock`（`include/linux/hrtimer.h:216`） | 同上 | **硬中断上下文要拿它**（`hrtimer_interrupt()`） |

**`raw_` 的真正含义在 PREEMPT_RT 上才显出来**：RT 上普通 `spinlock_t`
被换成可睡的 rtmutex，`raw_spinlock_t` 才保持"真自旋、不可抢占"。
⇒ **硬中断路径必须用 raw**，否则 RT 内核会在中断里试图睡觉。

## §B2 ⚠️ PREEMPT_RT 路径：本树的重点

**本树 4 处改动全部服务于这一节**（01 §0.1 列了改动清单）。

### §B2.1 非 RT：两个普通 softirq

```
timer 到期  → raise_timer_softirq()   → raise_softirq(TIMER_SOFTIRQ)
                include/linux/interrupt.h:625-628（#else 分支）
hrtimer 到期 → raise_hrtimer_softirq() → raise_softirq_irqoff(HRTIMER_SOFTIRQ)
                include/linux/interrupt.h:630-633
待处理查询   → local_pending_timers()  → local_softirq_pending()
                include/linux/interrupt.h:635-638
```

⇒ **三个包装函数在非 RT 上就是原调用的直译**，所以本树和上游**行为完全一致**。

### §B2.2 RT：一个专用内核线程 `timersd`

```
#ifdef CONFIG_PREEMPT_RT                     include/linux/interrupt.h:612
  DECLARE_PER_CPU(struct task_struct *, timersd);          :613
  DECLARE_PER_CPU(unsigned long, pending_timer_softirq);   :614

  local_pending_timers()  =  __this_cpu_read(pending_timer_softirq)   :619-622
```

唤起路径（`kernel/softirq.c`）：

```
raise_hrtimer_softirq()                      kernel/softirq.c:1044
  └─ raise_ktimers_thread(HRTIMER_SOFTIRQ)
       ├─ trace_softirq_raise(nr)            trace 事件仍然发，07 篇能看到
       └─ __this_cpu_or(pending_timer_softirq, 1 << nr)
            只置位，不唤醒

raise_timer_softirq()                        kernel/softirq.c:1049
  ├─ local_irq_save(flags)
  ├─ raise_ktimers_thread(TIMER_SOFTIRQ)
  ├─ wake_timersd()                          这个才唤醒线程
  └─ local_irq_restore(flags)
```

⚠️ **两者不对称**：`raise_hrtimer_softirq()` **只置位不唤醒**，
`raise_timer_softirq()` 置位**并且**唤醒。
原因是 hrtimer 那条路调用点在硬中断里（`hrtimer_interrupt()`），
中断返回时自然会检查 `timersd` 该不该跑；timer 那条路则需要显式唤醒。

线程本体：

```
timersd_should_run(cpu)   = local_pending_timers()      kernel/softirq.c:1018
timersd_setup(cpu)        = sched_set_fifo_low(current) kernel/softirq.c:1013
     它是一个 FIFO 低优先级实时线程

run_timersd(cpu)                                        kernel/softirq.c:1022
  ├─ ksoftirqd_run_begin()
  ├─ timer_si = local_pending_timers()
  ├─ __this_cpu_write(pending_timer_softirq, 0)   取走并清零
  ├─ or_softirq_pending(timer_si)                 塞回普通 softirq 掩码
  ├─ __do_softirq()                               复用普通 softirq 执行器
  └─ ksoftirqd_run_end()
```

**这个设计的巧妙处**：它没有另写一套定时器执行逻辑，而是
"**把 pending 位从私有变量搬回普通掩码，然后调普通的 `__do_softirq()`**"。
⇒ 执行逻辑零重复，只是**执行的上下文**从 softirq 变成了一个可被抢占的 FIFO 线程。

**RT 上这么做的收益**：定时器回调不再霸占 softirq 上下文 ⇒
高优先级实时任务可以抢占定时器处理 ⇒ **最坏延迟可控**。
**代价**：多一次上下文切换，且定时器回调的到期抖动变大。

`kernel/softirq.c:681` 还有一处相关判断：
`IS_ENABLED(CONFIG_PREEMPT_RT) && local_pending_timers() && ...`
—— softirq 返回路径上也要考虑这个私有 pending 位。

### §B2.3 数据结构层面的 RT 痕迹

| 字段 | 位置 | 作用 |
|---|---|---|
| `timer_base.expiry_lock` + `timer_waiters` | `kernel/time/timer.c:202-205` | **把 `timer_delete_sync()` 的自旋等改成睡着等** |
| `hrtimer_cpu_base.softirq_expiry_lock` + `timer_waiters` | `include/linux/hrtimer.h:231-234` | 同上，给 `hrtimer_cancel()` |

⚠️ **这两处字段是上游 6.1 就有的**（不在本树的 4 处改动里）。
⇒ **说明上游 6.1 已经为 RT 做好了大部分准备**，本树补的是最后那几个调用点
加上 `arch/arm64/Kconfig` 的 `select ARCH_SUPPORTS_RT`。

**为什么 RT 上必须改成睡着等**：`timer_delete_sync()` 原本是
`while (base->running_timer == timer) cpu_relax();`。RT 上定时器回调跑在
可被抢占的线程里，如果调用者优先级更高，自旋就会**永久饿死**那个回调
⇒ 必须变成"睡在 `expiry_lock` 上让回调跑完"。

## §B3 `mask` 与绕回补偿

```
delta = (now - tkr->cycle_last) & tkr->mask       kernel/time/timekeeping.c:284 一带
```

**一行代码解决绕回**，前提是 `mask` 必须精确等于硬件的有效位数：

| `mask` 写对了 | `mask` 写错了（比如 32 位硬件写成 64 位 mask） |
|---|---|
| 计数器从 `0xFFFFFFFF` 绕到 `0x00000002`，`(2 - 0xFFFFFFFF) & 0xFFFFFFFF = 3` ✓ | 同样的差值算出 `0xFFFFFFFF00000003` ⇒ **时间瞬间跳几百年** |

**还有一个上界**：`max_cycles` / `max_idle_ns`（02 §5.6）——
核心算出"最多能隔多久不读一次这个时钟源"，超过就会漏掉一整圈绕回。
⇒ **这个值直接限制 NO_HZ 能睡多久**：32 位 100MHz 的时钟源
`max_idle_ns` 只有几十秒，`tick_nohz_next_event()`（`kernel/time/tick-sched.c:806`）
会把睡眠时长夹到这个上限以内。

## §B4 `mult`/`shift` 与 NTP 调频

**换算永远只用乘和右移**（`timekeeping_delta_to_ns()`，
`kernel/time/timekeeping.c:374`），不用除法。
官方文档 `Documentation/timers/timekeeping.rst` 明确说这是刻意的：
`ns ~= (clocksource * mult) >> shift`。

**NTP 调频改的就是 `mult`**：`maxadj`（02 §5.6）限制了单次能调多少，
防止一次错误的调整把时间搞飞。
⇒ **所以 `tkr_raw` 存在的意义很清楚了**：它有自己独立的 `mult`，
不受 NTP 调整，用来回答"硬件真实速率是多少"。

## §B5 停 tick 的决策：四道门

`tick_nohz_next_event()`（`kernel/time/tick-sched.c:806`）要过四道门才停：

| # | 门 | 不过怎么办 |
|---|---|---|
| 1 | 有没有待处理的 timer softirq —— `local_timer_softirq_pending()`（`kernel/time/tick-sched.c:800`） | **不停** |
| 2 | timer wheel 的下一个到期点 —— `get_next_timer_interrupt()` | 取最近的 |
| 3 | hrtimer 的下一个到期点 —— `hrtimer_get_next_event()` | 取最近的 |
| 4 | 时钟源的 `max_idle_ns` 上限（§B3） | 夹到上限 |

⇒ **停 tick 不是"idle 就停"，而是"算出下一个必须醒的时刻，然后把硬件编程到那里"**。
⚠️ **`tick_stopped = 1` 之后 `jiffies` 就不再逐格前进**，
恢复时在 `tick_nohz_idle_exit()`（`kernel/time/tick-sched.c:1348`）里一次补齐。

## §B6 clockevent 编程失败与 hang 检测

**"编程到某个时刻"是会失败的**：如果算出来的时刻已经过去了（或者太近，
小于 `min_delta_ns`），硬件会直接不产生中断。

`hrtimer_interrupt()` 里有一套三级保护（`kernel/time/hrtimer.c`）：

| 级别 | 代码 | 做什么 |
|---|---|---|
| ① 重试 | `:1886` 的 `if (!tick_program_event(expires_next, 0))` 成功就返回 | 正常路径 |
| ② 记账 | `cpu_base->nr_retries++`（`:1906`） | **编程失败计数** |
| ③ hang 检测 | `cpu_base->nr_hangs++` + `hang_detected = 1`（`:1915-1916`）+ 更新 `max_hang_time`（`:1920-1921`） | **放弃精确编程，退回到"下一个 tick 再说"** |

**`hang_detected` 的后果**：`:701` 处
`if (!hrtimer_hres_active(cpu_base) || cpu_base->hang_detected)` 会**跳过重新编程**
⇒ 系统临时退化到低精度，等下一次中断再恢复（`:1887` 和 `:2226` 处清零）。

⇒ **这三个计数器是 07 篇最有价值的健康指标**：
`nr_retries` 涨得快说明 `min_delta_ns` 设小了；`nr_hangs` 非 0 说明
**有回调跑得太久**，把下一个到期点都错过了。

---

# Part C：设计评价

## §C1 源码自陈的设计债：6 条

```bash
grep -rnE '(FIXME|TODO|XXX|REVISIT|HACK)' <kernel/time 下 20 个非测试 .c>
```

| 位置 | 内容 | 性质 |
|---|---|---|
| `kernel/time/posix-timers.c:147` | `FIXME: Replace this by a per signal struct xarray once there is one` | **数据结构欠债**：定时器 ID 查找结构待换 |
| `kernel/time/posix-timers.c:347` | `FIXME: if ->sigq is queued we can race with ...` | **已知竞态** |
| `kernel/time/posix-timers.c:396` | `FIXME: What we really want, is to stop this ...` | **语义不理想** |
| `kernel/time/timer.c:578` | `TODO: This wants some optimizing similar to the code below` | 性能欠债 |
| `kernel/time/ntp.c:882` | `TODO: test various filters` | 测试欠债 |
| `kernel/time/timekeeping.c:1582` | `XXX - Do be sure to remove it once all arches implement it` | **跨架构过渡期的临时代码** |

**分布很说明问题：3 条里有 3 条集中在 `posix-timers.c`**。
⇒ **核心（timer/hrtimer/timekeeping/clocksource/clockevents）几乎没有自陈债，
债都在用户态接口那一层**。这和 of 那套的结论（全目录只有 1 条）是同一类现象：
**越靠近硬件抽象的代码越干净，越靠近 uapi 的代码越多历史包袱**。

## §C2 一大类 API 完全不返回错误

| 类别 | 例子 | 参数错了会怎样 |
|---|---|---|
| 定时器启动 | `hrtimer_start()` · `add_timer()` · `mod_timer()` | **`void` 或返回"之前是否 pending"** ⇒ 参数错只能 `WARN` |
| 读时间 | `ktime_get()` 全家族 | **没有失败概念**；`ktime_get()` 只 `WARN_ON(timekeeping_suspended)` |
| 初始化 | `timer_setup()` · `hrtimer_init()` | `void` |
| clockevent 注册 | `clockevents_config_and_register()` | **`void`**——注册失败你不知道 |

⚠️ **`clockevents_config_and_register()` 返回 `void` 是最值得说的一条**：
一个驱动注册时钟事件设备**拿不到任何反馈**。
对比 `clocksource_register_hz()` 是返回 `int` 的。
⇒ **同一个子系统里两个对称的注册函数，一个有返回值一个没有**，
这是明显的不一致（05 §3 会提醒驱动作者注意）。

## §C3 tick 与 hrtimer 的双向依赖

| 方向 | 证据 |
|---|---|
| hrtimer → tick | hrtimer 靠 `tick_program_event()` 编程硬件（`kernel/time/hrtimer.c:1886`） |
| tick → hrtimer | **`tick_sched.sched_timer` 本身是一个 `struct hrtimer`**（02 §5.8） |

**这个循环是本子系统初始化顺序绕的根本原因**（§A4–§A6）：
- 不能"先把 tick 建好再建 hrtimer"，因为高精度 tick 就是个 hrtimer
- 也不能反过来，因为 hrtimer 要靠 tick 层去碰硬件
- ⇒ **解法是分两阶段**：先用低精度周期 tick 把 hrtimer 跑起来，
  再由 hrtimer 接管 tick（`hrtimer_switch_to_hres()`）

**评价**：这是一个**必要的循环**，不是设计缺陷——
"周期 tick 只是高精度定时器的一个特例"这个洞见本身就要求这种结构。
但它的代价是**初始化路径很难读**，而且"当前处于哪个模式"成了一个
必须随时查的状态（`hres_active` / `nohz_mode` / `tick_stopped` 三个标志）。

## §C4 可观测性：极度不均衡

| 层 | 可观测性 | 依据 |
|---|---|---|
| hrtimer / timer | **好**：有 tracepoint、有 `/proc/timer_list`、有 hang 计数器 | 07 §2–§4 |
| timekeeping | **差**：几乎没有运行期可见状态 | 只有 `CONFIG_DEBUG_TIMEKEEPING` 下的额外校验（§3.1 提的两个 `timekeeping_get_delta()`） |
| clocksource | **中**：有 sysfs（`current_clocksource` / `available_clocksource`）+ 看门狗日志 | 02 §1.0.E |
| clockevent | **中**：sysfs 只有 `current_device`，但有 `retries` 计数 | §B6 |
| tick / NO_HZ | **中**：`/proc/stat` 的 idle 时间 + `tick_nohz_full_running` | 01 §10 |

⇒ **最要紧的那一层（timekeeping）反而最不可观测**。
这是可以理解的（读时间的热路径不能带记账），但意味着
**时间跳变类问题只能靠 clocksource 看门狗日志间接发现**。

## §C5 改名期：`del_timer` → `timer_delete`

01 §3.3 已经列了形式和统计。这里补设计评价：

**改名的动机是对的**：`del_timer_sync` 和 `timer_delete_sync` 相比，
后者与 `timer_setup` / `timer_pending` / `timer_reduce` 前缀一致。
**但改法留了尾巴**：

| 问题 | 表现 |
|---|---|
| 旧名仍是绝对主流 | `del_timer_sync` 685 个文件 vs `timer_delete_sync` 5 个（01 §3.3） |
| 有一个名字没跟着改 | **`try_to_del_timer_sync()` 还是旧风格**（`include/linux/timer.h:184`） |
| 平行 inline 会分叉 | 两个名字是两个函数体，未来可能不同步（skill 规则 24） |

⇒ **读老代码时旧名是常态**，写新代码用新名，**但别指望能全树统一**。

## §C6 整体评价

**做得好的三点**：

1. **抽象切分干净**：clocksource（读）/ clockevent（写）/ timekeeper（换算）/
   定时器（排程）四层职责不重叠（02 §1.0）。官方文档
   `Documentation/timers/highres.rst` 记录了这个切分的历史动机，值得一读
2. **把运行时判断编码进数据结构**：`HARD`/`SOFT` 变成 8 个 clock_base
   而不是 8 处 if（02 §5.4）；`TIMER_DEFERRABLE` 变成第二个 timer_base 而不是标志判断
3. **自我保护完整**：hang 检测（§B6）、看门狗、`max_idle_ns` 上界，
   都是"硬件或回调不守规矩时系统还能活"的机制

⚠️ **代价和欠债三点**：

1. **初始化路径很难读**（§C3 的双向依赖）
2. **一大类 API 没有错误返回**（§C2），注册失败静默
3. **最关键的一层最不可观测**（§C4）

**本树特有的一点**：4 处 PREEMPT_RT 改动（§B2）**让"回调在什么上下文跑"
从一个编译期常量变成了一个配置期变量**。
⇒ **写驱动时不能再假设"定时器回调一定在 softirq 里"**，
必须按 `HARD`/`SOFT` 显式表达意图（05 §1 有专门一节）。
