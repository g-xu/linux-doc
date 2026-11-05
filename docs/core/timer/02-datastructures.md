---
title: 数据结构
---

# timer 02：数据结构与关系

本篇先破歧义（§1.0），再画关系（§2、§3、§7），最后逐个看结构体（§5）。

**为什么顺序是这样**：这个子系统里画得再全的图也补不上一个没被显式否掉的
错误类比。所以先用六组对比把"名字像但不是一回事"的东西逐行摊开。

---

## §1.0 先分清六组关系

### §1.0.A clocksource vs clockevent —— 一个读，一个写

**最常见的错误类比**：以为它们是"同一个硬件的两个接口"，或者以为
clockevent 是 clocksource 的"中断版"。

**它们是方向相反的两个抽象**，官方文档 `Documentation/timers/timekeeping.rst`
把 clockevent 称为 clocksource 的 "conceptual reverse"：

| 对比项 | clocksource | clock_event_device |
|---|---|---|
| **方向** | **读**：告诉你"现在在时间轴的哪一点" | **写**：让你说"到这一点时给我一个中断" |
| **必须能产生中断吗** | **不需要** | **必须** |
| 核心字段 | `read()` + `mask` | `set_next_event()` + `event_handler` |
| 有 `mask` 吗 | **有**（要处理绕回） | **没有**——系统不会安排超出它时间视野的事件 |
| 每 CPU 还是全局 | 通常**全局一个**（选优选出最好的那个） | 通常**每 CPU 一个** |
| 选优机制 | 按 `rating` 比大小，`clocksource_find_best()`（`kernel/time/clocksource.c:968`） | 也按 `rating`，但**按职责分配**（周期 tick / 单次 / 广播） |
| 结构体定义 | `include/linux/clocksource.h:96-130` | `include/linux/clockchips.h:100-132` |

**为什么"同一个硬件"的印象不算全错**：arm64 的 `arm_arch_timer` 确实
**同时注册**了 clocksource（读 `CNTVCT_EL0`）和 clockevent（写 `CNTV_CVAL_EL0` + 中断）。
但那是**一个驱动实现了两个抽象**，不是一个抽象有两个面。
⇒ **判据**：`struct clocksource` 里**没有 `irq` 字段**，
`struct clock_event_device` 里有（`include/linux/clockchips.h:127`）。

### §1.0.B clocksource vs timekeeper —— 原始计数 vs 人类时间

**错误类比**：以为 `ktime_get()` 就是读 clocksource。

| 对比项 | clocksource | timekeeper |
|---|---|---|
| 返回什么 | **硬件计数器的原始值**（cycles） | **纳秒**（已经换算、已经加过偏移） |
| 谁实现 | 驱动 | **核心，没有驱动侧** |
| 知道"现在几点"吗 | **不知道**——它只有一个从 0 开始的计数 | 知道：它维护 real/mono/boottime/tai 之间的偏移 |
| 受 NTP 影响吗 | **不受** | **受**（`mult` 会被微调） |
| 结构体 | `struct clocksource` | `struct timekeeper`（`include/linux/timekeeper_internal.h`），内嵌 `struct tk_read_base`（`:34-43`） |

**timekeeper 干的活就一句话**：把 `(cycles_now - cycle_last) * mult >> shift`
累加到一个纳秒计数上，再按你要的时基加偏移。
`tk_read_base`（`include/linux/timekeeper_internal.h:34-43`）里那 8 个字段
（`clock` / `mask` / `cycle_last` / `mult` / `shift` / `xtime_nsec` / `base` / `base_real`）
就是这条换算式的全部输入。

⇒ **所以"读时间"是两层**：clocksource 给 cycles，timekeeper 给纳秒。
`ktime_get()` 读的是**后者**。03 §B 有逐层展开。

### §1.0.C `timer_list` vs `hrtimer` —— 时间轮 vs 红黑树

**错误类比**：以为 hrtimer 是"精度更高的 timer_list"，可以无脑替换。

**它们的数据结构和优化目标都是反的**，这是
`Documentation/timers/hrtimers.rst` 整篇文档在论证的事：

| 对比项 | `timer_list`（timer wheel） | `hrtimer` |
|---|---|---|
| **数据结构** | **时间轮 + 级联**（`vectors[WHEEL_SIZE]` 哈希桶，`kernel/time/timer.c:213`） | **红黑树 + 时间序**（`timerqueue_node`，`include/linux/hrtimer.h:119`） |
| **优化什么** | **添加/删除便宜**（O(1) 入桶） | **到期准**（插入时就排好序） |
| 时间单位 | **jiffies**（`expires` 是 `unsigned long`） | **纳秒**（`ktime_t`，64 位） |
| 精度 | `1/HZ`（10ms ~ 1ms） | 硬件精度（`hrtimer_resolution`） |
| 到期误差 | **只保证"不早于"**，级联会带来 O(N) 抖动 | 尽力准时 |
| 回调返回值 | **`void`**——不能自动重启 | `enum hrtimer_restart`——**能自动重启** |
| 回调能在硬中断跑吗 | 走 softirq（RT 上走 `timersd`） | **能**（`HRTIMER_MODE_HARD`） |
| 典型用途 | **超时**（会在到期前被取消） | **真的要等到那个时刻** |
| 每 CPU 结构 | `struct timer_base`（`kernel/time/timer.c:199-214`），每 CPU **2 个**（`NR_BASES`） | `struct hrtimer_cpu_base`（`include/linux/hrtimer.h:215-241`），每 CPU **1 个**，内含 **8 个** clock_base |

**"绝大多数超时在到期前就被取消"这个前提**决定了 timer wheel 的全部设计：
如果 90% 的定时器根本不会到期，那么"到期时要做点昂贵的级联"是可以接受的，
而"添加时必须便宜"是不能让的。⇒ 08 §5 用取号机的类比讲这件事。

⚠️ **不能无脑替换的具体后果**：把一个 I/O 超时从 `timer_list` 换成 `hrtimer`，
你会得到一堆**本来不会发生的定时器中断**（因为 hrtimer 会真的去编程硬件到那个时刻）。

### §1.0.D `jiffies` vs `ktime_t` —— 计数 vs 时间

| 对比项 | `jiffies` | `ktime_t` |
|---|---|---|
| 类型 | **`unsigned long`**（32 位上 32 位宽） | **`s64`**（纳秒标量） |
| 会绕回吗 | **会**（`HZ=1000` 的 32 位上 ~49.7 天） | **不会**（~292 年） |
| 怎么比大小 | **必须 `time_after()`** | **可以直接 `<`/`>`** |
| 单调吗 | 是，但 NO_HZ 下是"批量补偿"上去的 | 取决于时基（mono 是，real 不是） |
| 谁推进它 | tick 处理函数 | **没人推进**——它是每次读硬件算出来的 |

⚠️ **最容易错的一条**：`jiffies` 在 NO_HZ idle 期间**不是连续累加的**。
CPU 睡了 3 秒再醒，`jiffies` 会一次跳 3000（`HZ=1000`）。
⇒ 依赖"每个 jiffy 都被执行到"的逻辑在 tickless 上会坏。

### §1.0.E ⚠️ 那两个 `bus_type` 是**假总线**

`kernel/time/` 里确实有两个 `struct bus_type`，但**它们都不是驱动模型意义上的总线**：

| 名字 | 位置 | 真相 |
|---|---|---|
| `clocksource_subsys` | `kernel/time/clocksource.c:1459` | **纯 sysfs 容器** |
| `clockevents_subsys` | `kernel/time/clockevents.c:662` | **纯 sysfs 容器** |

**三条判据**（每条都能重跑）：

1. **都在 `#ifdef CONFIG_SYSFS` 里面** —— 不开 sysfs 它们根本不存在
2. **用 `subsys_system_register()` 注册**，落点是 `/sys/devices/system/clocksource/`
   而不是 `/sys/bus/`；`clocksource.c:1464` 紧接着 `device_register()` 了**一个**静态 device
3. **全树没有任何 `device_driver` 绑到它们上**：
   ```bash
   grep -rn 'clocksource_subsys\|clockevents_subsys' --include=*.c . \
     | grep -v 'kernel/time/clocksource.c\|kernel/time/clockevents.c'
   # → 无输出
   ```
   ⇒ **没有 `.match`、没有 probe、没有绑定**。它存在的唯一目的是挂三个 sysfs 属性
   （`current_clocksource` / `available_clocksource` / `unbind_clocksource`，
   `kernel/time/clocksource.c:1451-1456`）。

⇒ **所以 timer 是框架型，不是总线型**。这一点的方法论意义记在 99 §1.3：
"有没有 `bus_type`"这道门在这里会**误判**，必须追问"有没有驱动绑上去"。

### §1.0.F 本子系统有几层可插拔

| 层 | ops / 回调集合 | 实现者住在哪 | arm64 上的实现 |
|---|---|---|---|
| **1. clocksource** | `read` / `enable` / `disable` / `suspend` / `resume` / `mark_unstable` / `tick_stable`（`include/linux/clocksource.h:97`、`:115-120`） | `drivers/clocksource/` + `arch/` | `arm_arch_timer.c`（1791 行） |
| **2. clockevent** | `set_next_event` / `set_next_ktime` / `set_state_*` ×4 / `tick_resume` / `broadcast`（`include/linux/clockchips.h:102-119`） | 同上 | 同一个 `arm_arch_timer.c` |
| **3. sched_clock** | 一个 `read()` 函数指针（`sched_clock_register()`） | 同上 | 同一个文件 |
| **4. posix_clock** | `struct posix_clock_operations` | `drivers/ptp/` | PTP 硬件时钟 |

⇒ **四层，但 arm64 上前三层是同一个驱动文件实现的**。
这是本子系统一个反直觉的地方：抽象分得很细，实现却高度集中。

---

## §2 核心抽象与三方的关系

本子系统**没有自己的总线**（§1.0.E），所以这里画的不是"总线 ↔ 父总线"，
而是「**硬件驱动 ↔ 核心抽象 ↔ 消费者**」三方：

```
     消费者（几乎所有内核代码）
     ┌──────────────────────────────────────────────────────────┐
     │  ktime_get()          hrtimer_start()      mod_timer()    │
     │  ktime_get_real_ts64()  schedule_hrtimeout()  msleep()    │
     └───────┬──────────────────────┬────────────────────┬───────┘
             │ 读时间               │ 高精度定时         │ 低精度定时
             ▼                      ▼                    ▼
     ┌───────────────┐      ┌────────────────┐   ┌──────────────┐
     │  timekeeper   │      │    hrtimer     │   │  timer wheel │
     │ (timekeeping.c)│      │  (hrtimer.c)   │   │  (timer.c)   │
     │ cycles→纳秒    │      │  红黑树         │   │  时间轮      │
     └───────┬───────┘      └────────┬───────┘   └──────┬───────┘
             │                       │  要"到点中断"      │ 靠 tick 推进
             │ 要"当前 cycles"        ▼                   │
             │              ┌──────────────────┐          │
             │              │   tick 层         │◄─────────┘
             │              │ (tick-*.c)        │
             │              │ 周期/单次/NO_HZ    │
             │              └────────┬─────────┘
             │                       │ 分配职责
             ▼                       ▼
     ┌───────────────┐      ┌────────────────────┐
     │  clocksource  │      │ clock_event_device │   ← 核心抽象层
     └───────┬───────┘      └─────────┬──────────┘
             │                        │
             └────────┬───────────────┘
                      ▼
        ┌──────────────────────────────────┐
        │ drivers/clocksource/             │   ← 驱动层
        │   arm_arch_timer.c (arm64)       │
        │   同时实现 clocksource +          │
        │   clockevent + sched_clock       │
        └──────────────────┬───────────────┘
                           ▼
                  ARMv8 通用定时器硬件
                  CNTVCT_EL0（读）
                  CNTV_CVAL_EL0 + PPI（写+中断）
```

**图里最要紧的三条边**：

| 边 | 含义 | 代码落点 |
|---|---|---|
| timekeeper → clocksource | **只有这一条**：timekeeper 认一个"当前时钟源" | `curr_clocksource`（`kernel/time/clocksource.c:100`） |
| tick 层 → clockevent | tick 层决定每个 clockevent 干什么活 | `tick_cpu_device`（`kernel/time/tick-common.c:28`） |
| timer wheel ← tick | **时间轮不直接碰硬件**，它靠 tick 每次推进一格 | `run_local_timers()`（`kernel/time/timer.c:2054`，本树改动点之一） |

⚠️ **hrtimer 和 timer wheel 的层级不对称**：hrtimer **能**要求 tick 层
重新编程硬件到任意时刻；timer wheel **不能**，它只能等 tick 来推。
这是"高精度"和"低精度"在架构上的真实差别，不只是精度数字的差别。

---

## §3 核心操作数据流

### §3.1 「读一次时间」：`ktime_get()` 到硬件

```
ktime_get()                                    kernel/time/timekeeping.c
  │
  ├─ seqcount 读侧循环开始（tk_core.seq）
  │    无锁重试：写侧改的时候读侧重来
  │
  ├─ tk = &tk_core.timekeeper                  kernel/time/timekeeping.c:50
  │
  ├─ base = tk->tkr_mono.base                  ← 上次更新时的纳秒基准
  │
  ├─ nsecs = timekeeping_get_ns(&tk->tkr_mono)      timekeeping.c:384-390
  │    │
  │    ├─ delta = timekeeping_get_delta(tkr)        timekeeping.c:240 / :284
  │    │    │      两个同名实现：:284 是普通版，:240 是
  │    │    │        CONFIG_DEBUG_TIMEKEEPING 下带校验的版本
  │    │    │
  │    │    ├─ now = tk_clock_read(tkr)             timekeeping.c:191
  │    │    │        └─ tkr->clock->read(tkr->clock)   这一步才真的读硬件
  │    │    │             └─ arm64: arch_counter_read()
  │    │    │                （drivers/clocksource/arm_arch_timer.c:222）→ CNTVCT_EL0
  │    │    │
  │    │    └─ return (now - tkr->cycle_last) & tkr->mask   mask 处理绕回
  │    │
  │    └─ timekeeping_delta_to_ns(tkr, delta)      timekeeping.c:374
  │         └─ return (delta * tkr->mult) >> tkr->shift     换算成纳秒
  │
  └─ return ktime_add_ns(base, nsecs)          ← 相加得到最终 ktime_t
```

上面这条链是从 `ktime_get()` 的函数体（`kernel/time/timekeeping.c:854` 一带）
逐层展开的，不是概述。注意 `timekeeping_get_delta()` **有两个实现**
（`:240` 和 `:284`），按 `CONFIG_DEBUG_TIMEKEEPING` 二选一 ⇒ 打开调试选项时
这条路上会多一层"读数是否越界"的校验，07 篇会用到。

**三个要点**：
1. **`base` 是上次 tick 更新时算好的**，读的时候只补 `base` 之后的增量
   ⇒ 这就是为什么 `*_coarse*` 变体（只读 `base`，不读硬件）那么便宜
2. **`mask` 在这里生效**：`(now - cycle_last) & mask` 让 32 位计数器的绕回
   自动变成正确的差值
3. **`mult`/`shift` 只做乘和右移**，不做除法——官方文档
   `Documentation/timers/timekeeping.rst` 明确说这是为了避免热路径上的除法

### §3.2 「一个 hrtimer 到期」：硬件中断到回调

```
硬件定时器中断（arm64: PPI）
  │
  ▼
arm_arch_timer 的中断处理函数         drivers/clocksource/arm_arch_timer.c
  │  清中断，然后：
  ▼
dev->event_handler(dev)              核心填的函数指针，不是驱动实现的
  │
  ▼
hrtimer_interrupt(dev)               kernel/time/hrtimer.c
  │
  ├─ 取 cpu_base->lock（raw_spinlock）
  │
  ├─ ① 软到期检查
  │    if (now >= cpu_base->softirq_expires_next)
  │        cpu_base->softirq_activated = 1
  │        raise_hrtimer_softirq()   本树改动点（hrtimer.c:1867）
  │             │
  │             ├─ 非 RT: raise_softirq_irqoff(HRTIMER_SOFTIRQ)
  │             └─ RT:    置位 pending_timer_softirq + 唤醒 timersd 线程
  │
  ├─ ② __hrtimer_run_queues(..., HRTIMER_ACTIVE_HARD)
  │      │  遍历 8 个 clock_base 里 active 的那些
  │      ▼
  │    for each 到期的 hrtimer:
  │      ├─ 从红黑树摘下
  │      ├─ base->running = timer      供 hrtimer_active() 判断
  │      ├─ 放开锁 → timer->function(timer)   回调在这里跑
  │      ├─ 重新加锁
  │      └─ 返回 HRTIMER_RESTART ⇒ 重新入队
  │
  ├─ ③ 算出下一个到期点 expires_next
  │
  └─ ④ 重新编程硬件到 expires_next
         tick_program_event() → dev->set_next_event()
```

⚠️ **`HARD` 和 `SOFT` 在这张图里是两条不同的路**：
`HARD` 的回调在 ② 里跑（**还在硬中断上下文，不能睡**），
`SOFT` 的回调由 ① 唤起的 softirq/`timersd` 稍后跑。
`struct hrtimer` 里 `is_soft` 和 `is_hard` 两个字段
（`include/linux/hrtimer.h:125-126`）记的就是这个选择。

### §3.3 「一个 timer_list 到期」：为什么多一跳

```
tick 到来（周期 tick 或 NO_HZ 下的单次事件）
  │
  ▼
update_process_times(user_tick)                    kernel/time/timer.c:2064
  │  （它还顺手做 account_process_tick / rcu_sched_clock_irq / irq_work_tick）
  ▼
run_local_timers()                                 kernel/time/timer.c:2043-2058
  │
  ├─ ① hrtimer_run_queues()      低精度模式下的 hrtimer 也在这里跑（:2047）
  │
  ├─ ② 检查标准 base 的 next_expiry
  │      if (time_before(jiffies, base->next_expiry)) {
  │          if (!CONFIG_NO_HZ_COMMON) return;      没到就直接走
  │          base++;                                开了 NO_HZ 还要查 deferrable base
  │          if (time_before(jiffies, base->next_expiry)) return;
  │      }
  │
  └─ ③ raise_timer_softirq()     本树改动点（kernel/time/timer.c:2057）
       │
       ├─ 非 RT: raise_softirq(TIMER_SOFTIRQ)
       └─ RT:    置位 + 唤醒 timersd
              │
              ▼
       run_timer_softirq()
         │
         ├─ 取 base->lock
         ├─ 级联：把上层轮的桶摊到下层
         ├─ 收集本格所有到期的 timer 到一个链表
         └─ call_timer_fn(timer, fn, baseclk)
              ├─ base->running_timer = timer   timer_delete_sync() 等的就是它
              ├─ fn(timer)                     回调，返回 void
              └─ base->running_timer = NULL
```

**和 hrtimer 的三个结构性差别**：
1. **必须等 tick**——没有"编程硬件到确切时刻"这一步
2. **有级联**（cascade）：到期时可能要把上层轮的一整桶重新分发到下层
   ⇒ `Documentation/timers/hrtimers.rst` 说的 "unpredictable O(N) overhead" 就是它
3. **回调返回 `void`** ⇒ 想周期性执行必须在回调里自己 `mod_timer()`

⇒ **两条路都要经过 softirq（或 RT 上的 `timersd`）**，
所以"定时器回调什么时候真的跑"在有负载时不确定。07 篇讲怎么观测这个延迟。

---

## §4 命名约定速查

这个子系统的命名相当有规律，认出前缀就知道它属于哪一层：

| 前缀 | 属于 | 例子 |
|---|---|---|
| `ktime_get*` | timekeeper（读时间） | `ktime_get()`、`ktime_get_real_ts64()` |
| `ktime_*`（不带 get） | `ktime_t` 算术 | `ktime_add()`、`ktime_to_ns()` |
| `hrtimer_*` | 高精度定时器 | `hrtimer_start()`、`hrtimer_cancel()` |
| `timer_*` / `*_timer` | **低精度定时器**（`timer_list`） | `timer_setup()`、`mod_timer()`、`timer_delete()` |
| `clocksource_*` | 时钟源框架 | `clocksource_register_hz()` |
| `clockevents_*` | 时钟事件框架 | `clockevents_config_and_register()` |
| `tick_*` | tick 层 | `tick_program_event()`、`tick_nohz_*` |
| `timekeeping_*` | timekeeper 内部 | `timekeeping_get_ns()`（**不导出**） |
| `tk_*` | timekeeper 内部的短前缀 | `tk_clock_read()`、`tk_core`、`tk_fast_mono` |
| `*sleep*` / `*delay*` | 延时/睡眠（`delay.h`） | `msleep()`、`udelay()`、`usleep_range()` |
| `alarm_*` | 能唤醒系统的定时器 | `alarm_start()` |
| `posix_clock_*` | 动态 POSIX 时钟 | `posix_clock_register()` |

⚠️ **三组"名字像但层级不同"的**：

| 容易混的 | 真实归属 |
|---|---|
| `timer_*` vs `hrtimer_*` | **两个完全独立的子系统**，不是一个的变体（§1.0.C） |
| `clocksource_*` vs `clockevents_*` | 一个读一个写（§1.0.A）；注意后者是**复数** `clockevents` |
| `tick_nohz_*` vs `tick_*` | 前者是 NO_HZ 专用，后者是通用 tick 层 |

**单复数不一致是真实存在的**：`clocksource_register_hz()`（单数）
vs `clockevents_config_and_register()`（复数）。**记不住就查 §1.0.F 的表。**

---

## §5 结构体逐个看

### §5.1 `struct timer_list`（`include/linux/timer.h`）

| 字段 | 类型 | 作用 |
|---|---|---|
| `entry` | `struct hlist_node` | 挂在时间轮某个桶的链表里 |
| `expires` | `unsigned long` | **到期时刻，单位 jiffies**（绝对值） |
| `function` | `void (*)(struct timer_list *)` | 回调；**被 `timer_shutdown*()` 置 NULL** 就永久失效（01 §3.4） |
| `flags` | `u32` | **低位存 CPU 号，高位存 `TIMER_*` 标志**——不是纯标志字段 |
| `lockdep_map` | — | 只在 `CONFIG_LOCKDEP` 下存在 |

**只有 4 个字段（外加调试字段）**，这是它"添加/删除便宜"的物质基础。

### §5.2 `struct timer_base`（`kernel/time/timer.c:199-214`）

| 字段 | 作用 |
|---|---|
| `lock` | `raw_spinlock_t`——**每 CPU 一把**，不是全局锁 |
| `running_timer` | **当前正在跑回调的那个 timer** ⇒ `timer_delete_sync()` 自旋等它变 NULL（01 §3.4 的死锁场景就是这个字段） |
| `expiry_lock` / `timer_waiters` | ⚠️ **只在 `CONFIG_PREEMPT_RT` 下存在**（`:202-205`）——RT 上不能自旋等，改成睡着等 |
| `clk` | 时间轮当前推进到的 jiffies |
| `next_expiry` | **最近的到期时刻** ⇒ `run_local_timers()` 靠它决定要不要唤起 softirq |
| `is_idle` / `timers_pending` / `next_expiry_recalc` | 状态标志 |
| `pending_map` | 位图：哪些桶非空 ⇒ 免得遍历空桶 |
| `vectors[WHEEL_SIZE]` | **时间轮本体**——一组 hlist 桶 |

**每 CPU 有 2 个 base**（`timer_bases[NR_BASES]`，`kernel/time/timer.c:216`）：
`BASE_STD`（普通）和 `BASE_DEF`（deferrable）。
⇒ **这就是 `TIMER_DEFERRABLE` 的实现方式**：换一个 base，而不是加一个标志判断。

### §5.3 `struct hrtimer`（`include/linux/hrtimer.h:118-127`）

| 字段 | 作用 |
|---|---|
| `node` | `struct timerqueue_node`——**内含红黑树节点 + 到期时间**（硬到期） |
| `_softexpires` | **软到期时间**（`node.expires` 是硬上界）⇒ 两者的差就是 `range_ns` 松弛量 |
| `function` | `enum hrtimer_restart (*)(struct hrtimer *)`——**返回值决定是否重启** |
| `base` | 指回它所属的 `hrtimer_clock_base`（8 个之一） |
| `state` | `HRTIMER_STATE_INACTIVE` / `_ENQUEUED` |
| `is_rel` | 启动时给的是相对时间 |
| `is_soft` / `is_hard` | **回调跑在 softirq 还是硬中断**（01 §4.1 的 `mode`） |

**`_softexpires` 和 `node.expires` 是一对**：内核可以在 `[_softexpires, node.expires]`
之间任选时刻触发，用来把多个到期点合并成一次中断。
⇒ 这就是 `hrtimer_start_range_ns()` 的 `range_ns` 参数落到哪里。

### §5.4 `struct hrtimer_clock_base`（`include/linux/hrtimer.h:159-168`）与 8 个 base

| 字段 | 作用 |
|---|---|
| `cpu_base` | 指回所属 CPU 的 `hrtimer_cpu_base` |
| `index` / `clockid` | 是 8 个里的哪一个 / 对应的 `CLOCK_*` |
| `seq` | `seqcount_raw_spinlock_t`——供 `hrtimer_active()` 无锁判断 |
| `running` | **正在跑回调的 hrtimer** ⇒ `hrtimer_active()` 把它也算 active |
| `active` | `struct timerqueue_head`——**红黑树本体 + 最左节点缓存** |
| `get_time` | **该时基的取时函数指针**（`ktime_get` / `ktime_get_real` / …） |
| `offset` | 相对 monotonic 的偏移（`clock_was_set()` 时更新） |

**`HRTIMER_MAX_CLOCK_BASES = 8`**（`include/linux/hrtimer.h:170-179`）：

| # | base | # | base |
|---|---|---|---|
| 0 | `HRTIMER_BASE_MONOTONIC` | 4 | `HRTIMER_BASE_MONOTONIC_SOFT` |
| 1 | `HRTIMER_BASE_REALTIME` | 5 | `HRTIMER_BASE_REALTIME_SOFT` |
| 2 | `HRTIMER_BASE_BOOTTIME` | 6 | `HRTIMER_BASE_BOOTTIME_SOFT` |
| 3 | `HRTIMER_BASE_TAI` | 7 | `HRTIMER_BASE_TAI_SOFT` |

**= 4 个时基 × {HARD, SOFT}**。⇒ **`HARD`/`SOFT` 不是一个运行时判断，
是"挂到哪棵红黑树上"** ——这是本子系统一个很干净的设计决定：
把"回调在什么上下文跑"编码进数据结构，而不是编码进 if。

### §5.5 `struct hrtimer_cpu_base`（`include/linux/hrtimer.h:215-241`）

| 字段 | 作用 |
|---|---|
| `lock` | `raw_spinlock_t`，每 CPU 一把 |
| `active_bases` | **位图：8 个 base 里哪些非空** ⇒ 到期时只遍历这些 |
| `hres_active` | **是否已进入高精度模式**（07 篇要看这个） |
| `in_hrtirq` / `hang_detected` / `softirq_activated` / `online` | 状态位 |
| `nr_events` / `nr_retries` / `nr_hangs` / `max_hang_time` | ⚠️ 只在 `CONFIG_HIGH_RES_TIMERS` 下存在（`:225-230`）；**`/proc/timer_list` 里能看到它们** |
| `softirq_expiry_lock` / `timer_waiters` | ⚠️ **只在 `CONFIG_PREEMPT_RT` 下存在**（`:231-234`）——和 `timer_base` 同样的手法 |
| `expires_next` / `next_timer` | **下一个硬到期时刻和它的 timer** ⇒ 编程硬件用的就是它 |
| `softirq_expires_next` / `softirq_next_timer` | 软到期那一路的同样两个字段 |
| `clock_base[8]` | 8 个 clock_base 内嵌在这里（不是指针数组） |
| `csd` | `call_single_data_t`——跨 CPU 唤起用 |

⚠️ **`timer_base` 和 `hrtimer_cpu_base` 都有一对 `CONFIG_PREEMPT_RT` 专属字段**，
而且作用一样（把"自旋等回调跑完"换成"睡着等"）。
⇒ **这是本树 RT 特性在数据结构层面的痕迹**，虽然这两处字段本身是上游的
（不在本树的 3 处改动里）。03 篇有一节讲 RT 路径。

### §5.6 `struct clocksource`（`include/linux/clocksource.h:96-130`）

| 字段 | 必填 | 作用 |
|---|---|---|
| `read` | **是** | 读硬件计数器，返回原始 cycles |
| `mask` | **是** | **多少位有效** ⇒ 绕回补偿靠它（01 §7 讲过写错的后果） |
| `mult` / `shift` | 否 | **由 `clocksource_register_hz()` 代算**，别手填 |
| `rating` | **是** | **选优就靠它比大小** |
| `name` | **是** | `/sys/devices/system/clocksource/clocksource0/current_clocksource` 显示的就是它 |
| `max_idle_ns` / `max_cycles` / `maxadj` | 否 | 核心算出来的：**最长能安全 idle 多久**（NO_HZ 要用） |
| `uncertainty_margin` | 否 | 看门狗判定的容差 |
| `flags` | 否 | `CLOCK_SOURCE_IS_CONTINUOUS` 等 |
| `enable` / `disable` / `suspend` / `resume` | 否 | 电源管理钩子（`:115-118`） |
| `mark_unstable` / `tick_stable` | 否 | 看门狗回调（`:119-120`） |
| `list` | — | 挂在全局 clocksource 链表上 |
| `wd_list` / `cs_last` / `wd_last` | — | ⚠️ 只在 `CONFIG_CLOCKSOURCE_WATCHDOG` 下（`:123-128`） |
| `vdso_clock_mode` | 否 | **能不能给 vDSO 用**——arm64 上 `clock_gettime` 走 vDSO 靠这个 |

### §5.7 `struct clock_event_device`（`include/linux/clockchips.h:100-132`）

| 字段 | 谁填 | 作用 |
|---|---|---|
| ⚠️`event_handler` | **核心填**，驱动只调用 | **最容易搞反的字段**（01 §8） |
| `set_next_event` | **驱动填** | 单次模式的"编程到这个 cycles 值" |
| `set_next_ktime` | 驱动填（可选） | 直接收 `ktime_t` 的变体，`CLOCK_EVT_FEAT_KTIME` 才用 |
| `set_state_periodic` / `_oneshot` / `_oneshot_stopped` / `_shutdown` | **驱动填** | 状态机（`:113-116`） |
| `tick_resume` | 驱动填 | 恢复 |
| `min_delta_ns` / `max_delta_ns` | **核心算**（由 `min_delta_ticks`/`max_delta_ticks` 换算） | **能编程的时间范围** |
| `min_delta_ticks` / `max_delta_ticks` | **驱动填**（`:122-123`） | 硬件能力 |
| `mult` / `shift` | 核心算 | 换算因子 |
| `state_use_accessors` | 核心 | ⚠️ **名字就在说"别直接读，用访问器"**（`clockevent_state_*()`） |
| `features` | 驱动填 | `CLOCK_EVT_FEAT_PERIODIC` / `_ONESHOT` / `_C3STOP` / `_KTIME` … |
| `retries` | 核心 | **编程失败重试次数** ⇒ 07 篇的健康指标 |
| `rating` / `name` / `irq` / `cpumask` | 驱动填 | `cpumask` 决定它是"每 CPU"还是"全局" |
| `bound_on` | 核心 | 绑在哪个 CPU 上 |
| `broadcast` | 驱动填（可选） | 广播用 |

⚠️ **`min_delta_ns` 太小会导致"编程了但已经过期"** ⇒ 核心会重试并把
`retries` 累加，极端情况下 `GENERIC_CLOCKEVENTS_MIN_ADJUST`
（`kernel/time/Kconfig:44`）会自动把下界往上调。

### §5.8 `struct tick_device` 与 `struct tick_sched`

**`struct tick_device`（`kernel/time/tick-sched.h:12-15`）只有两个字段**：

| 字段 | 作用 |
|---|---|
| `evtdev` | 这个 CPU 用哪个 `clock_event_device` |
| `mode` | `TICKDEV_MODE_PERIODIC` / `_ONESHOT` |

⇒ **它就是"CPU ↔ clockevent"的绑定关系**，全局变量是
`tick_cpu_device`（`kernel/time/tick-common.c:28`）。

`struct tick_sched`（`kernel/time/tick-sched.h:55` 起）是 NO_HZ 的状态机，关键字段：

| 字段 | 作用 |
|---|---|
| `sched_timer` | **一个 `struct hrtimer`** ——高精度模式下的周期 tick **本身就是个 hrtimer** |
| `nohz_mode` | `NOHZ_MODE_INACTIVE` / `_LOWRES` / `_HIGHRES` |
| `tick_stopped` | **tick 停了没有** ⇒ 07 篇判断 tickless 是否生效 |
| `inidle` / `idle_active` / `got_idle_tick` / `do_timer_last` | 状态位 |
| `last_tick` / `next_tick` | 上次/下次 tick 时刻 |

**`sched_timer` 是一个 hrtimer 这件事很关键**：它说明
"周期 tick"在高精度模式下不是硬件周期中断，而是**一个不断自我重排的 hrtimer**。
`Documentation/timers/highres.rst` 的 "dynamic ticks" 一节讲的就是这个转换。
⇒ 08 §6 用它讲"tick 是被模拟出来的"。

### §5.9 `struct tk_read_base`（`include/linux/timekeeper_internal.h:34-43`）

8 个字段，就是 §3.1 那条换算式的全部输入：

| 字段 | 在换算式里的角色 |
|---|---|
| `clock` | 提供 `read()` |
| `mask` | 绕回补偿 |
| `cycle_last` | 上次采样点 |
| `mult` / `shift` | `(delta * mult) >> shift` |
| `xtime_nsec` | 累积的小数部分 |
| `base` | 纳秒基准 |
| `base_real` | real 时基的基准 |

**`struct timekeeper` 里有两个 `tk_read_base`**：`tkr_mono` 和 `tkr_raw`。
⇒ 这就是为什么 raw 时基不受 NTP 影响——**它有自己独立的一套 mult/shift**。

---

## §6 全局变量与锁

### §6.1 全局变量

| 变量 | 位置 | 作用 |
|---|---|---|
| `tk_core` | `kernel/time/timekeeping.c:50-55` | **匿名 struct，含 `seq` + `timekeeper`** ——读时间的唯一真源 |
| `shadow_timekeeper` | `kernel/time/timekeeping.c:57` | **影子副本**：更新时先改它，再一次性拷回 ⇒ 缩短读侧被打断的窗口 |
| `tk_fast_mono` / `tk_fast_raw` | `kernel/time/timekeeping.c:105` / `:111` | **NMI-safe 快照** ⇒ `ktime_get_mono_fast_ns()` 读的是这个 |
| `dummy_clock` | `kernel/time/timekeeping.c:86` | 挂起期间顶替真时钟源的占位 |
| `timekeeping_suspended` | `kernel/time/timekeeping.c` | `ktime_get()` 会 `WARN_ON` 它 |
| `curr_clocksource` | `kernel/time/clocksource.c:100` | **当前选中的时钟源** |
| `suspend_clocksource` | `kernel/time/clocksource.c:101` | 挂起期计时用 |
| `watchdog` / `watchdog_timer` | `kernel/time/clocksource.c:133` / `:134` | 看门狗时钟源 + **一个 `timer_list`**（看门狗自己用低精度定时器） |
| `timer_bases[NR_BASES]` | `kernel/time/timer.c:216` | **每 CPU 2 个 timer wheel** |
| `hrtimer_bases` | `kernel/time/hrtimer.c:71` | **每 CPU 1 个，内含 8 个 clock_base** |
| `migration_cpu_base` | `kernel/time/hrtimer.c:149` | **CPU 下线时定时器的临时归属** |
| `tick_cpu_device` | `kernel/time/tick-common.c:28` | 每 CPU 的 tick 设备绑定 |
| `tick_nohz_full_mask` / `tick_nohz_full_running` | `kernel/time/tick-sched.c:261` / `:263` | NO_HZ_FULL 状态（01 §10） |

### §6.2 锁

| 锁 | 位置 | 类型 | 保护什么 |
|---|---|---|---|
| `timekeeper_lock` | `kernel/time/timekeeping.c:44` | `raw_spinlock_t` | **写侧**：更新 timekeeper |
| `tk_core.seq` | `kernel/time/timekeeping.c:51` | `seqcount_raw_spinlock_t` | **读侧**：无锁重试（§3.1 那个 do-while） |
| `timer_base.lock` | `kernel/time/timer.c:200` | `raw_spinlock_t` | **每 CPU** 时间轮 |
| `hrtimer_cpu_base.lock` | `include/linux/hrtimer.h:216` | `raw_spinlock_t` | **每 CPU** 红黑树 |
| `hrtimer_clock_base.seq` | `include/linux/hrtimer.h:163` | `seqcount_raw_spinlock_t` | `hrtimer_active()` 的无锁判断 |
| `clocksource_mutex` | `kernel/time/clocksource.c:103` | `mutex` | 注册/注销/选优 |
| `clockevents_lock` | `kernel/time/clockevents.c` | `raw_spinlock_t` | 事件设备链表与职责分配 |
| `tick_freeze_lock` | `kernel/time/tick-common.c:497` | `raw_spinlock_t` | suspend/resume 时冻结 tick |
| `timer_keys_mutex` | `kernel/time/timer.c:221` | `mutex` | static key 切换（NO_HZ 迁移开关） |
| `timer_base.expiry_lock` | `kernel/time/timer.c:203` | ⚠️`spinlock_t`，**仅 PREEMPT_RT** | RT 上"睡着等回调跑完" |
| `hrtimer_cpu_base.softirq_expiry_lock` | `include/linux/hrtimer.h:232` | ⚠️ 仅 PREEMPT_RT | 同上 |

**两条模式**（03 §B 展开）：
1. **读时间用 seqcount，不用锁**——因为读的频率极高，且读侧不能阻塞写侧
2. **定时器队列用每 CPU raw_spinlock**——`raw_` 是因为**中断上下文要拿它**，
   RT 上普通 `spinlock_t` 会变成可睡的，raw 才保证不睡

⚠️ **`timekeeper_lock` 也是 `raw_`**：改系统时间的路径可能在关中断下走
（suspend/resume），所以不能用可睡的锁。

---

## §7 结构关系总图

```
                     ┌──────────────── 每 CPU ────────────────┐
                     │                                        │
  tick_cpu_device ───┼──► struct tick_device                  │
  (tick-common.c:28) │      ├─ evtdev ──────────┐             │
                     │      └─ mode             │             │
                     │                          ▼             │
                     │            struct clock_event_device   │
                     │              ├─ event_handler  ◄── 核心填
                     │              ├─ set_next_event ◄── 驱动填
                     │              └─ cpumask                │
                     │                                        │
  hrtimer_bases ─────┼──► struct hrtimer_cpu_base             │
  (hrtimer.c:71)     │      ├─ lock (raw_spinlock)            │
                     │      ├─ active_bases (位图)             │
                     │      ├─ expires_next ──► 编程硬件用     │
                     │      └─ clock_base[8]                  │
                     │           ├─[0] MONOTONIC ──┐          │
                     │           ├─[1] REALTIME    │ 每个含   │
                     │           ├─ …              │ 一棵     │
                     │           └─[7] TAI_SOFT ───┤ 红黑树   │
                     │                             ▼          │
                     │                    struct hrtimer ×N   │
                     │                      ├─ node (rb+时间) │
                     │                      ├─ _softexpires   │
                     │                      ├─ function       │
                     │                      └─ is_soft/is_hard│
                     │                                        │
  timer_bases[2] ────┼──► struct timer_base ×2                │
  (timer.c:216)      │      ├─ [BASE_STD]                     │
                     │      ├─ [BASE_DEF]  ← TIMER_DEFERRABLE │
                     │      ├─ lock / running_timer           │
                     │      ├─ next_expiry                    │
                     │      └─ vectors[WHEEL_SIZE] ──┐        │
                     │                               ▼        │
                     │                   struct timer_list ×N │
                     │                     ├─ entry (hlist)   │
                     │                     ├─ expires(jiffies)│
                     │                     └─ function        │
                     │                                        │
  tick_sched ────────┼──► struct tick_sched                   │
                     │      ├─ sched_timer (是个 hrtimer)   │
                     │      ├─ nohz_mode                      │
                     │      └─ tick_stopped                   │
                     └────────────────────────────────────────┘

                     ┌──────────────── 全局 ──────────────────┐
                     │                                        │
  tk_core ───────────┼──► { seq, struct timekeeper }          │
  (timekeeping.c:50) │        ├─ tkr_mono ──► struct tk_read_base
                     │        │                ├─ clock ──┐   │
                     │        │                ├─ mask    │   │
                     │        │                └─ mult/shift  │
                     │        └─ tkr_raw ──► （另一套）    │   │
                     │                                    ▼   │
  curr_clocksource ──┼──────────────────► struct clocksource  │
  (clocksource.c:100)│                       ├─ read()        │
                     │                       ├─ mask          │
                     │                       └─ rating        │
                     │                            ▲           │
                     │                            │ 按 rating 选优
                     │                    全局 clocksource 链表 │
                     └────────────────────────────────────────┘
```

**这张图最该记住的三件事**：

1. **每 CPU 的东西比全局的多**：定时器队列、tick 设备、clockevent
   全是每 CPU；**只有 clocksource 和 timekeeper 是全局单份**
2. **`tick_sched.sched_timer` 是个 hrtimer** ⇒ tick 层反过来依赖 hrtimer
   （不是单向的"hrtimer 架在 tick 上"）——这个**双向依赖**是本子系统
   初始化顺序特别绕的根本原因，03 §A 会展开
3. **8 个 clock_base 内嵌在 `hrtimer_cpu_base` 里**（不是指针），
   所以一个 CPU 的所有 hrtimer 状态是一块连续内存 ⇒ cache 友好，
   也是 `____cacheline_aligned` 的原因
