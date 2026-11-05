---
title: 总览
---

# timer 00：总览（复习入口）

**这一篇的定位是"复习入口"**：隔几个月回来，**只读这一篇**就能恢复全貌。
它是对 01–08 的浓缩，不是引言。

⚠️ **先记住一件事**：这棵树的 `kernel/time/` **不是纯上游**——
有 3 处 PREEMPT_RT 相关改动，而且 `arch/arm64/Kconfig` 本地加了
`select ARCH_SUPPORTS_RT`（§5.7、01 §0.1）。**这影响你写驱动时的一个必选项。**

---

## §1 定位：timer 解决什么问题

**一句话**：让内核能回答两个问题——**"现在是什么时候"**和**"到点了叫我"**。

**它服务的对象几乎是整个内核**：调度器要时间片、网络栈要重传超时、
块层要 I/O 超时、驱动要延时、printk 要时间戳、用户态要 `clock_gettime()`。
⇒ **它是仅有的几个"所有子系统都依赖"的基础设施之一。**

**规模**：`kernel/time/` 共 **35 个文件、22995 行**，
`include/linux/` 下相关头文件 **2786 行**（99 §1.2 有逐文件表）。

**四个抽象层**（这是全篇最要紧的一张表）：

| 抽象 | 一句话 | 方向 | 谁实现 | 驱动写它还是用它 |
|---|---|---|---|---|
| **clocksource** | "现在是什么时候" | **读** | 硬件计数器驱动 | 平台驱动**实现** |
| **clockevent** | "到点了叫我" | **写** | 能产中断的硬件定时器 | 平台驱动**实现** |
| **timekeeper** | 把原始计数换成人类时间 | 读 | **核心，无驱动侧** | 只**用**（`ktime_get*`） |
| **timer / hrtimer** | "N 纳秒后调我的回调" | 写 | **核心，架在 clockevent 上** | 只**用** |

⇒ **绝大多数驱动作者只碰后两行。** 前两行只有平台/SoC 时钟驱动作者才写。

**子系统类型：框架型**（99 §1.3）。
⚠️ **注意一个陷阱**：`kernel/time/` 里确实有两个 `struct bus_type`
（`clocksource_subsys`、`clockevents_subsys`），但它们是
**纯 sysfs 容器，没有任何 driver 绑上去**（02 §1.0.E）——
所以"有 `bus_type` 就是总线型"这条判据在这里会误判。

---

## §2 背景：为什么会有这么多层

**这一节是理解整个子系统的钥匙。** 现在的四层结构不是设计出来的，
是**四次分别解决不同问题**累积出来的。按时间顺序看就非常清楚。

### §2.1 起点：只有 `jiffies`

最早的内核里，时间就是一个数：**`jiffies`**，即"开机以来硬件定时器中断了多少次"。
中断频率由 `CONFIG_HZ` 决定（100/250/300/1000，`kernel/Kconfig.hz:51`）。

这套机制极其简单：
- 硬件定时器配成**周期中断**
- 每次中断把 `jiffies++`
- "定时"就是"记下目标 jiffies，每次中断检查有没有到"

**它够用了很多年**，因为早期内核的需求就是"进程时间片"和"I/O 超时"，
两者都不需要比 10ms 更细的精度。

### §2.2 问题一：`jiffies` 会绕回

`jiffies` 是 `unsigned long`。32 位系统上 `HZ=1000` 时，
**约 49.7 天就绕一圈**（`2^32 / 1000 / 86400 ≈ 49.7`）。

**后果**：`if (jiffies > target)` 在绕回点附近会给出**相反的答案**
（08 §3 用汽车里程表讲了这件事）。

**解法**：`time_after()` / `time_before()` 宏——做减法然后按有符号数判符号。
⇒ **这不是一层抽象，是一条纪律**：内核里几乎看不到裸的 `jiffies >` 比较。

⚠️ **64 位上 `jiffies` 实际不会绕回**（要几亿年），
但规范仍然要求用 `time_after()`——因为代码会被移植。
⇒ **这类"在你的平台上不会出错但仍必须遵守"的规则最容易被省略。**

### §2.3 问题二：`jiffies` 的精度是死的

`HZ=100` 时一个 jiffy 是 **10ms**。这意味着：
- `msecs_to_jiffies(1)` 得到 1 ⇒ **你要睡 1ms，实际可能睡 10ms**
- 想测一个耗时 200µs 的操作 ⇒ **完全测不出来**

**能不能把 `HZ` 提到 100000？** 不能——那意味着每秒 10 万次中断，
**CPU 全部时间都在处理定时器中断**。

⇒ **这是第一个真正的架构性矛盾**：精度和开销直接冲突，
因为二者都由同一个参数（`HZ`）决定。

### §2.4 第一层分离：把"读时间"从架构代码里抽出来（GTOD）

**2.6.18 引入 Generic Time Of Day 框架。**
在此之前，"当前时间是多少"的换算逻辑**散在每个架构自己的代码里**——
每个架构都要自己写一遍"读硬件计数器、换算成纳秒、处理绕回"。

**GTOD 的做法**：定义一个 `struct clocksource`，
把架构相关的部分压缩到最小：

| 架构要提供 | 核心负责 |
|---|---|
| `read()`：读硬件计数器，返回**原始 cycles** | 换算成纳秒 |
| `mask`：多少位有效 | 绕回补偿 |
| `rating`：这个时钟源有多好 | 在多个候选里选最好的 |

**换算怎么做**：不用除法，用**乘 + 右移**
（`ns ≈ (cycles × mult) >> shift`，`Documentation/timers/timekeeping.rst` 的原文公式）。
**因为除法太慢，而这个换算每次读时间都要做一遍**（08 §4 用超市定价讲了这个）。

⇒ **这一层的产物是 clocksource + timekeeper 两个抽象。**
**为什么是两个而不是一个**：clocksource 给的是**裸数字**，
timekeeper 才知道"这个数字对应几点"（还要维护 real/mono/boottime/tai 之间的偏移）。
02 §1.0.B 用对比表逐行摊开了这组区别。

### §2.5 第二层分离：把"到点通知"也抽出来（clockevents）

**GTOD 只解决了"读"，"写"还是一团乱。**
`Documentation/timers/highres.rst` 描述当时的状况：

> The setup and selection of the event device for various event driven
> functionalities is hardwired into the architecture dependent code.
> This results in duplicated code across all architectures and makes it
> extremely difficult to change the configuration of the system.

**也就是说：每个架构自己决定"哪个硬件定时器负责周期 tick"、
"中断处理函数里做什么"，代码重复且改不动。**

**clockevents 的做法**：定义 `struct clock_event_device`，
把"编程硬件"和"决定这个中断干什么活"分开：

| 驱动提供 | 核心提供 |
|---|---|
| `set_next_event()`：编程到这个 cycles 值 | `event_handler`：**中断来了要做什么** |
| `set_state_*()`：状态机 | 决定这个设备承担什么职责 |
| `features` / `rating` / `cpumask`：能力声明 | 周期 tick / 单次 / 广播 的分配 |

⚠️ **`event_handler` 是这一层最容易搞反的字段**：
它**不是驱动实现的**，是核心填给驱动的。
驱动的中断处理函数只负责清中断然后 `dev->event_handler(dev)`（01 §8、05 §3.1）。

**这个反转是整个设计的关键**：正是因为"中断来了做什么"由核心决定，
核心才能在运行时改变一个定时器的用途——**而这直接使能了后面两件事**。

**成本记录**：文档说这一层"adds about 700 lines of code which results
in a 2KB increase of the kernel binary size"，
而"The conversion of i386 removes about 100 lines of code"。
⇒ **作者当时是拿"二进制大 2KB"换"每个架构少 100 行重复代码 + 使能新功能"。**

### §2.6 第三层：高精度定时器（hrtimers）

**有了 clockevents 之后，"精度"这个矛盾（§2.3）就有解了**：
不再让硬件周期性中断，而是**每次都编程到"下一个真正需要的时刻"**。

**但旧的定时器数据结构（时间轮）用不了。**
`Documentation/timers/hrtimers.rst` 记录了他们**先试过合并，失败了**：

> After a lot of back and forth trying to integrate high-resolution and
> high-precision features into the existing timer framework, and after
> testing various such high-resolution timer implementations in practice,
> we came to the conclusion that the timer wheel code is fundamentally
> not suitable for such an approach.

**为什么不行**，文档给了三条理由，最核心的是**两者优化目标相反**：

| | 时间轮（`timer_list`） | 需要的（hrtimer） |
|---|---|---|
| 优化 | **添加/删除便宜** | **到期准** |
| 结构 | 哈希桶 + 级联 | 需要时间序 |
| 单位 | jiffies | 需要纳秒 |

**时间轮为什么要优化"添加便宜"**——这是全篇最重要的一个洞见：

> The vast majority of those timers never expire and are rarely
> recascaded because the expected correct event arrives in time so they
> can be removed from the timer wheel before any further processing of
> them becomes necessary.

⇒ **绝大多数超时定时器在到期前就被取消了。**
如果 90% 的条目根本不会执行，那么"添加时做精确排序"的工作 90% 是白费的。
⇒ **所以时间轮刻意选了"添加 O(1)、到期时才做昂贵的级联"。**
08 §2 用医院"手术预约 vs 复诊提醒"讲了这个分工。

**hrtimer 的选择**：**红黑树 + 时间序插入**（文档说考虑过基数树和哈希，
选了红黑树因为内核已有成熟实现）。
**时间单位是 64 位纳秒（`ktime_t`），"no compromises"**。

⇒ **结论：两套定时器并存不是历史包袱，是刻意的分工。**

### §2.7 第四层：tickless（NO_HZ）

**这一层是"免费"得到的**，`Documentation/timers/highres.rst` 的原话：

> Dynamic ticks are the logical consequence of the hrtimer based periodic
> tick replacement (sched_tick).

**推理链是这样的**：
1. 有了 hrtimer 之后，"周期 tick"可以用**一个不断自我重排的 hrtimer** 来实现
2. 既然是定时器，那么"下一次什么时候"就是可以选的
3. ⇒ **CPU idle 时，直接把下一次设成"下一个真正有事的时刻"** ⇒ tick 停了

**代码上的证据**：`struct tick_sched` 的**第一个字段就是一个 `struct hrtimer`**
（`kernel/time/tick-sched.h:56` 的 `sched_timer`）。08 §5 用工厂汽笛讲了这件事。

**三种模式**（`Documentation/timers/no_hz.rst` 的三节结构）：

| 模式 | Kconfig | 含义 |
|---|---|---|
| 周期 tick | `CONFIG_HZ_PERIODIC` | 永不省略；**极端实时场景反而选它**（from-idle 延迟最稳） |
| dyntick-idle | `CONFIG_NO_HZ_IDLE`（`kernel/time/Kconfig:108`） | **idle 时停 tick，arm64 默认** |
| adaptive-tick | `CONFIG_NO_HZ_FULL`（`kernel/time/Kconfig:118`） | 只剩一个可运行任务也停 |

⚠️ **`NO_HZ_FULL` 有四个硬约束**（文档明确列出）：
① **不能把所有 CPU 都标成 adaptive**（至少留一个做 timekeeping）；
② 因此至少要 2 个 CPU；③ RCU 回调必须 offload；
④ **POSIX CPU 定时器会阻止 CPU 进 adaptive 模式**。

**停 tick 的代价**：`jiffies` 在 idle 期间**不再逐格前进**，
恢复时一次补齐（`tick_nohz_idle_exit()`，`kernel/time/tick-sched.c:1348`）。
⇒ **依赖"每个 jiffy 都被执行到"的逻辑在 tickless 上会坏**（02 §1.0.D）。

### §2.8 一个副产品：深度休眠需要广播

**tickless 让 CPU 能睡很久，于是暴露了一个硬件问题**：
很多 SoC 的**本地定时器在深度省电态会断电停摆**。

**解法**：让一个"不会停"的设备替所有睡着的 CPU 计时，到点发中断叫醒。
- 声明"我会停"的 feature 位是 `CLOCK_EVT_FEAT_C3STOP`（04 §1.1）
- **arm64 上广播设备是用 hrtimer 模拟的**
  （`tick_setup_hrtimer_broadcast()`，`arch/arm64/kernel/time.c:62`）——
  用**还醒着的 CPU** 替睡着的 CPU 计时

⚠️ **DTS 里的 `always-on` 直接控制这件事**：
`arch_timer_c3stop = !of_property_read_bool(np, "always-on");`
（`drivers/clocksource/arm_arch_timer.c:1401`）。
**写错的后果不对称**：该写没写 = 多一层开销（功能正确）；
不该写却写了 = **CPU 睡过头，系统看起来卡死**（06 §5.2）。
⇒ **不确定就别写。** 08 §7 用宿舍闹钟讲了这件事。

### §2.9 最后一层（本树特有）：实时内核把定时器搬进线程

**这是这棵树相对上游 6.1 的改动**（01 §0.1、03 §B2）。

**要解决的问题**：softirq 是"必须一口气做完、不可被普通任务抢占"的。
定时器回调跑在 softirq 里 ⇒ **一个慢回调会推高整个系统的最坏延迟**。

**解法**：`CONFIG_PREEMPT_RT` 下把 timer/hrtimer 的 softirq
搬到一个**专用 per-CPU FIFO 低优先级内核线程 `timersd`** 里。

| 非 RT | RT |
|---|---|
| `raise_softirq(TIMER_SOFTIRQ)` | 置位 `pending_timer_softirq` + 唤醒 `timersd` |

**本树的 3 处 `kernel/time/` 改动就是这个**：

| 文件 | 改成 | 上游 |
|---|---|---|
| `hrtimer.c`（2 处） | `raise_hrtimer_softirq()` | `raise_softirq_irqoff(HRTIMER_SOFTIRQ)` |
| `timer.c`（1 处） | `raise_timer_softirq()` | `raise_softirq(TIMER_SOFTIRQ)` |
| `tick-sched.c`（1 处） | `local_pending_timers()` | `local_softirq_pending()` |

**关键结论：非 RT 配置下语义和上游完全一致**——
那三个包装函数在 `include/linux/interrupt.h:624-638` 的 `#else` 分支里
就是原调用的 `static inline` 直译。

⚠️ **但 arm64 上现在开得起 RT**（`arch/arm64/Kconfig:96` 本地新增
`select ARCH_SUPPORTS_RT`）⇒ **写 hrtimer 时必须显式想清楚
`_SOFT` 还是 `_HARD`**（05 §1.4）。08 §8 用急诊室讲了这个权衡。

### §2.10 四层累积的结果

```
问题                          解法                    产物
─────────────────────────────────────────────────────────────
jiffies 绕回              →  time_after() 纪律     →  （无新抽象）
精度 vs 开销的死结        →  把周期中断换成         →  clockevent
                             "编程到下一时刻"
读时间的架构重复          →  抽出 read()+mask       →  clocksource
                                                     +  timekeeper
时间轮不适合高精度        →  另起一套红黑树         →  hrtimer
（试过合并，失败了）
tick 也可以是定时器       →  自我重排的 hrtimer     →  NO_HZ（免费得到）
本地定时器深度休眠会停    →  一个"不停"的设备代劳   →  tick 广播
softirq 推高最坏延迟      →  搬进 FIFO 线程         →  timersd（本树）
```

⇒ **所以"为什么这么多层"的答案是：每一层都在解一个具体的、
上一层解不了的问题。没有一层是为了抽象而抽象。**

---

## §3 概念模型

### §3.1 一张图看全

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
     │  cycles→纳秒   │      │  红黑树         │   │  时间轮+级联  │
     └───────┬───────┘      └────────┬───────┘   └──────┬───────┘
             │                       │ 要"到点中断"       │ 靠 tick 推
             │                       ▼                   │
             │              ┌──────────────────┐         │
             │              │   tick 层         │◄────────┘
             │              │ sched_timer     │
             │              │   本身是 hrtimer  │
             │              └────────┬─────────┘
             ▼                       ▼
     ┌───────────────┐      ┌────────────────────┐
     │  clocksource  │      │ clock_event_device │
     └───────┬───────┘      └─────────┬──────────┘
             └────────┬───────────────┘
                      ▼
        drivers/clocksource/arm_arch_timer.c
        （arm64：一个驱动同时实现 clocksource
          + clockevent + sched_clock）
```

### §3.2 三条要记住的边

| 边 | 含义 | 代码落点 |
|---|---|---|
| timekeeper → clocksource | **只有一条**：认一个"当前时钟源" | `curr_clocksource`（`kernel/time/clocksource.c:100`） |
| tick 层 → clockevent | tick 层决定每个 clockevent 干什么活 | `tick_cpu_device`（`kernel/time/tick-common.c:28`） |
| tick 层 ← hrtimer（**双向**） | **`sched_timer` 是个 hrtimer**，而 hrtimer 又靠 tick 层碰硬件 | `kernel/time/tick-sched.h:56` |

⚠️ **第三条那个双向依赖是初始化顺序特别绕的根本原因**（03 §C3、§5.5）。

### §3.3 每 CPU vs 全局

| 每 CPU | 全局 |
|---|---|
| `timer_base` ×2（`BASE_STD` / `BASE_DEF`） | `clocksource`（选一个最好的） |
| `hrtimer_cpu_base` ×1（内含 8 个 clock_base） | `timekeeper`（`tk_core`） |
| `tick_device` / `tick_sched` | 全局 clocksource 链表 |
| `clock_event_device`（通常） | — |

⇒ **"读时间"是全局单份，"定时"是每 CPU** ——
这个不对称直接决定了锁策略（§5.6）。

---

## §4 数据结构速览

**七个结构体撑起整个子系统**（详见 02 §5）：

| 结构体 | 位置 | 一句话 | 关键字段 |
|---|---|---|---|
| `struct timer_list` | `include/linux/timer.h` | **低精度定时器，只有 4 个字段** | `expires`（jiffies）· `function`（`void` 返回）· `flags`（**低位存 CPU 号**） |
| `struct timer_base` | `kernel/time/timer.c:199-214` | 每 CPU 时间轮 | `vectors[WHEEL_SIZE]`（轮本体）· `running_timer`（`timer_delete_sync()` 等的就是它）· `next_expiry` |
| `struct hrtimer` | `include/linux/hrtimer.h:118-127` | 高精度定时器 | `node`（红黑树+硬到期）· `_softexpires`（**软到期**）· `function`（**返回值决定重启**）· `is_soft`/`is_hard` |
| `struct hrtimer_clock_base` | `include/linux/hrtimer.h:159-168` | **一棵红黑树 + 一个时基** | `active`（树）· `get_time`（该时基的取时函数）· `running` |
| `struct hrtimer_cpu_base` | `include/linux/hrtimer.h:215-241` | 每 CPU，**内含 8 个 clock_base** | `active_bases`（位图）· `expires_next`（**编程硬件用**）· `hres_active` |
| `struct clocksource` | `include/linux/clocksource.h:96-130` | "现在几点"的硬件抽象 | `read` · `mask` · `rating`；**没有 `irq`** |
| `struct clock_event_device` | `include/linux/clockchips.h:100-132` | "到点叫我"的硬件抽象 | `set_next_event`（驱动填）· `event_handler`（**核心填**）· `features` · `irq` |
| `struct tk_read_base` | `include/linux/timekeeper_internal.h:34-43` | **换算式的全部输入**（8 个字段） | `clock` · `mask` · `cycle_last` · `mult` · `shift` · `xtime_nsec` · `base` |
| `struct tick_sched` | `kernel/time/tick-sched.h:55` | NO_HZ 状态机 | `sched_timer`（**是个 hrtimer**）· `nohz_mode` · `tick_stopped` |
| `struct tick_device` | `kernel/time/tick-sched.h:12-15` | **只有 2 个字段**：CPU↔clockevent 的绑定 | `evtdev` · `mode` |

### §4.1 两个"编码进数据结构"的设计决定

**本子系统有两处把"运行时判断"变成"挂到哪个结构上"**，值得记：

| 决定 | 怎么实现的 | 而不是 |
|---|---|---|
| hrtimer 的回调跑在硬中断还是 softirq | **8 个 clock_base = 4 时基 × {HARD, SOFT}**（`include/linux/hrtimer.h:170-179`） | 8 处 `if (is_soft)` |
| `TIMER_DEFERRABLE` | **每 CPU 第二个 `timer_base`**（`BASE_DEF`，`kernel/time/timer.c:216`） | 每次检查一个标志位 |

⇒ **这是本子系统代码质量的一个正面标志**（03 §C6）。

### §4.2 ⚠️ 两处 PREEMPT_RT 专属字段

| 字段 | 位置 | 作用 |
|---|---|---|
| `timer_base.expiry_lock` + `timer_waiters` | `kernel/time/timer.c:202-205` | **把"自旋等回调跑完"改成"睡着等"** |
| `hrtimer_cpu_base.softirq_expiry_lock` + `timer_waiters` | `include/linux/hrtimer.h:231-234` | 同上 |

⚠️ **这两处是上游 6.1 就有的**，不在本树的 3 处改动里
⇒ **说明上游早就为 RT 铺好了路，本树补的只是最后那几个调用点。**

---

## §5 机制速览

### §5.1 读一次时间（`ktime_get()`）

```
seqcount 读侧循环（tk_core.seq）
  ├─ base = tk->tkr_mono.base              ← 上次 tick 算好的纳秒基准
  ├─ delta = (读硬件 - cycle_last) & mask   ← mask 处理绕回
  └─ 返回 base + (delta × mult) >> shift    ← 只有乘和右移，没有除法
```

**三个要点**：
1. **`base` 是上次更新时算好的** ⇒ 这就是 `*_coarse*` 变体（只读 base，
   不读硬件）为什么那么便宜
2. **无锁重试**（seqcount）⇒ 读侧不阻塞写侧
3. ⚠️ **seqcount 在 NMI 里会死循环** ⇒ 所以另有一套 `*_fast_ns`
   读双缓冲快照（`tk_fast_mono`，`kernel/time/timekeeping.c:105`）

### §5.2 一个 hrtimer 到期

```
硬件中断 → 驱动清中断 → dev->event_handler(dev)     核心填的指针
                            ▼
                      hrtimer_interrupt()
                        ├─ ① 软到期检查 → raise_hrtimer_softirq()  本树改动点
                        ├─ ② 跑 HARD 队列的回调（还在硬中断里）
                        ├─ ③ 算下一个 expires_next
                        └─ ④ tick_program_event() → dev->set_next_event()
```

⚠️ **`HARD` 在 ② 里跑（不能睡），`SOFT` 由 ① 唤起的 softirq/`timersd` 稍后跑。**

### §5.3 一个 `timer_list` 到期（多一跳）

```
tick → update_process_times()（kernel/time/timer.c:2064）
         └─ run_local_timers()（:2043-2058）
              ├─ ① hrtimer_run_queues()    低精度模式下的 hrtimer 也在这里跑
              ├─ ② 检查 next_expiry，没到就 return
              └─ ③ raise_timer_softirq()   本树改动点（:2057）
                    └─ run_timer_softirq()
                         ├─ 级联（把上层轮的桶摊到下层）
                         └─ call_timer_fn() → 你的回调（返回 void）
```

**和 hrtimer 的三个结构性差别**：
① **必须等 tick**（不能编程硬件到确切时刻）；
② **有级联**（官方文档说的 "unpredictable O(N) overhead"）；
③ **回调返回 `void`** ⇒ 想周期执行必须自己 `mod_timer()`。

### §5.4 启动顺序（最反直觉的一段）

```
start_kernel()                                     init/main.c
  ├─ tick_init()          :1057   几乎什么都不做（还没有设备）
  ├─ init_timers()        :1059   时间轮 + TIMER_SOFTIRQ
  ├─ hrtimers_init()      :1061   8 个 clock_base + HRTIMER_SOFTIRQ
  ├─ timekeeping_init()   :1063   用 jiffies（rating=1）先跑起来
  ├─ time_init()          :1064   arm64 在这里 probe 真硬件
  └─ sched_clock_init()   :1116
```

**`timekeeping_init()` 在 `time_init()` 之前** ⇒
**timekeeper 初始化时真硬件还不存在** ⇒ 靠一个 rating=1 的兜底时钟源
（`kernel/time/jiffies.c:34`，注释就写着 `lowest valid rating`）。
真硬件（arm64 rating=400，`drivers/clocksource/arm_arch_timer.c:235`）
注册时触发重新选优，自动换掉。08 §6 用"挂钟还在快递路上"讲了这件事。

**arm64 的 `time_init()` 只有 18 行**（`arch/arm64/kernel/time.c:55-72`），
但有一行特别值得记：**`lpj_fine = arch_timer_rate / HZ`**（`:69`）——
**直接算 loops-per-jiffy，不做标定循环** ⇒ `udelay()` 在 arm64 上不受 CPU 调频影响。

### §5.5 切到高精度是一次"懒切换"

⚠️ **不在注册时切，在下一次低精度 tick 里切**：

```
hrtimer_run_queues()（kernel/time/hrtimer.c:1938）
  └─ tick_check_oneshot_change()?（kernel/time/tick-sched.c:1608）
       └─ hrtimer_switch_to_hres()（kernel/time/hrtimer.c:756）
```

**为什么懒切换**：必须先确认 clocksource 连续、clockevent 支持单次，
而这只有在都注册完之后才能判断 ⇒ **用"下次 tick 再问一遍"代替复杂的通知编排**。

### §5.6 两套同步策略

| 场景 | 用什么 | 为什么 |
|---|---|---|
| 读时间 | **seqcount（无锁重试）** | 频率极高，且读侧不能阻塞写侧 |
| 定时器队列 | **每 CPU `raw_spinlock_t`** | **中断上下文要拿它**；RT 上普通 `spinlock_t` 可睡，raw 才保证不睡 |

**写侧还有个技巧**：先改 `shadow_timekeeper`（`kernel/time/timekeeping.c:57`），
最后一次 `memcpy` 拷回 ⇒ **缩短读侧被打断的窗口**。

### §5.7 ⚠️ 本树特有：PREEMPT_RT 的 `timersd`

```
#ifdef CONFIG_PREEMPT_RT              include/linux/interrupt.h:612
  timersd                  ← per-CPU FIFO 低优先级线程
  pending_timer_softirq    ← 独立的 pending 位图（不在普通 softirq 掩码里）
#else
  三个包装函数 = 原调用的直译          include/linux/interrupt.h:624-638
#endif
```

**最巧妙的一点**：`run_timersd()`（`kernel/softirq.c:1022` 一带）
**把 pending 位塞回普通掩码然后调普通的 `__do_softirq()`**
⇒ **执行逻辑零重复，只改变执行上下文。**

⚠️ **对驱动作者的唯一影响**：`_SOFT` 回调在 RT 上跑在可被抢占的线程里
⇒ **能拿 `spinlock_t`、能睡**；`_HARD` 仍在硬中断里 ⇒ **不能**。
**不确定就用 `_SOFT`**（05 §1.4）。

### §5.8 自我保护三件套

| 机制 | 抓什么 | 位置 |
|---|---|---|
| **clocksource 看门狗** | 时钟源偏差过大 ⇒ **自动降级** | 04 §5；日志原文见 07 §2.2 |
| **hrtimer hang 检测** | 回调跑太久错过下一个到期点 ⇒ **退回低精度** | `kernel/time/hrtimer.c:1915-1921`（03 §B6） |
| **`max_idle_ns` 上界** | 睡太久会漏掉一整圈绕回 ⇒ **夹住 NO_HZ 睡眠时长** | 02 §5.6、03 §B3 |

**三个都是"硬件或回调不守规矩时系统还能活"** ⇒ 03 §C6 把它列为正面评价。

---

## §6 端到端调用路径

### §6.1 一个驱动做 `usleep_range(100, 200)`

```
usleep_range(100, 200)                    include/linux/delay.h:65
  └─ usleep_range_state(min, max, TASK_UNINTERRUPTIBLE)
       └─ schedule_hrtimeout_range(...)   kernel/time/hrtimer.c
            ├─ hrtimer_init_sleeper_on_stack()
            ├─ hrtimer_sleeper_start_expires()
            │    └─ hrtimer_start_range_ns()
            │         ├─ 插入红黑树（按 _softexpires 排序）
            │         └─ 若成为最早的 ⇒ tick_program_event()
            │                            └─ dev->set_next_event()
            │                                 └─ arm64: 写 CNTV_CVAL_EL0
            ├─ schedule()                 让出 CPU
            │  ...（硬件中断到来）...
            │    └─ hrtimer_interrupt() → 唤醒本任务
            └─ destroy_hrtimer_on_stack()
```

### §6.2 一次 `clock_gettime(CLOCK_MONOTONIC)`

```
用户态 clock_gettime()
  │
  ├─ arm64 上大概率不进内核 ── 走 vDSO
  │    └─ 读 vDSO 数据页里的 mult/shift/base + 直接读 CNTVCT_EL0
  │         ↑ 这个数据页由 kernel/time/vsyscall.c 在每次 tick 更新
  │
  └─ 走不了 vDSO 时才进系统调用
       └─ ktime_get_ts64() → §5.1 那条链
```

**`clocksource.vdso_clock_mode` 字段决定能不能走 vDSO**（02 §5.6）。
⇒ **这就是为什么 `clock_gettime()` 在 arm64 上极快** ——
它根本不进内核。

### §6.3 CPU 进 idle 并停 tick

```
cpuidle
  ├─ tick_nohz_idle_enter()         kernel/time/tick-sched.c:1168（只记账）
  ├─ tick_nohz_idle_stop_tick()     kernel/time/tick-sched.c:1148
  │    └─ tick_nohz_next_event()    kernel/time/tick-sched.c:806
  │         四道门（03 §B5）：
  │         ① local_timer_softirq_pending()   ← 本树改动点（:800）
  │         ② get_next_timer_interrupt()      ← 问时间轮
  │         ③ hrtimer_get_next_event()        ← 问 hrtimer
  │         ④ 时钟源的 max_idle_ns 上限        ← 夹住
  │    └─ tick_nohz_stop_tick()      kernel/time/tick-sched.c:889
  │
  ├─ （若定时器会断电）tick_broadcast_enter()   include/linux/tick.h:99
  │
  ...（睡）...
  │
  ├─ tick_broadcast_exit()
  └─ tick_nohz_idle_exit()          kernel/time/tick-sched.c:1348
       └─ 一次补齐 jiffies
```

---

## §7 子系统关系

| 子系统 | 关系 | 落点 |
|---|---|---|
| **irqchip / 中断** | **clockevent 必须有中断** | `clock_event_device.irq`；arm64 是 4 个 PPI（06 §3） |
| **of（设备树）** | **`TIMER_OF_DECLARE()` 是主发现路径** | 72 个驱动用它（06 §0.1） |
| **cpuidle / PSCI** | **进深度休眠要登记广播** | `tick_broadcast_enter()`；6 个消费者（04 §1.2） |
| **调度器** | **`sched_clock()` 是调度器的时间源** | 76 个驱动注册它（04 §3.2） |
| **RCU** | **`NO_HZ_FULL` 要求 RCU 回调 offload** | `select RCU_NOCB_CPU`（01 §10） |
| **softirq / PREEMPT_RT** | **本树 3 处改动全在这个接口上** | `include/linux/interrupt.h:612-639` |
| **网卡 / PTP** | **`timecounter` 换算硬件时间戳** | 26 个文件，多为 `drivers/net/ethernet/`（04 §4.2） |
| **RTC** | **alarmtimer 靠它穿过 suspend** | `alarmtimer_get_rtcdev()`（04 §2.1） |
| **clk** | **定时器要先有时钟输入** | `of_clk_init(NULL)` 在 `time_init()` 里（`arch/arm64/kernel/time.c:59`） |
| **vDSO** | **`clock_gettime()` 靠它不进内核** | `kernel/time/vsyscall.c`（§6.2） |
| **cpufreq** | ⚠️ **arm64 上没有耦合**（`lpj_fine` 用固定频率算，§5.4） | 03 §A5 |

**最紧的三条**：中断（clockevent 的前提）、of（发现路径）、softirq（本树改动点）。

---

## §8 导读：我该看哪一篇

| 你的处境 | 先读 | 再读 |
|---|---|---|
| **完全不懂时间子系统** | **08 篇**（大白话九个话题） | §2（背景）→ 02 §1.0 |
| **要在驱动里定时/延时/读时间** | **05 §1** | 01 §2–§6 |
| **要写平台定时器驱动** | **05 §2 + §3** | 01 §7 §8 → 06 篇 |
| **定时器不到点** | **07 §6.6** | 07 §3（tracepoint） |
| **时间跑偏 / 跳变** | **07 §2.2**（看门狗日志） | 06 §4（`clock-frequency`） |
| **深度 idle 后系统不醒** | **06 §5.2**（`always-on`） | 04 §1（广播） |
| **要开 PREEMPT_RT** | **05 §1.4** | 03 §B2 → 08 §8 |
| **写 DTS** | **06 §9**（可 diff 的真实节点） | 06 §1（属性总表） |
| **要改 timer 核心本身** | 02 → 03（全篇）→ 04 | **99 §5**（先知道哪里没验证过） |
| **隔几个月回来复习** | **只读本篇** | — |

### §8.1 全篇最该记住的六条

1. **四个抽象**：clocksource（读）/ clockevent（写）/ timekeeper（换算）/
   定时器（排程）。**把这四个分清，整个子系统就不乱了**（§1、08 §1）
2. **两套定时器的分工判据是"到期前会不会被取消"，不是"重不重要"**
   （§2.6、08 §2）
3. **`event_handler` 是核心填给驱动的，不是驱动实现的**（§2.5、05 §3.1）
4. **周期 tick 本身是一个 hrtimer** ⇒ tickless 是免费得到的（§2.7、08 §5）
5. ⚠️ **本树可以开 PREEMPT_RT** ⇒ 写 hrtimer 必须显式选 `_SOFT`/`_HARD`
   （§2.9、05 §1.4）
6. ⚠️ **DTS 的 `always-on` 写错会让 CPU 睡过头**，而且默认（不写）是安全的那一侧
   （§2.8、06 §5.2）

### §8.2 ⚠️ 三条容易被忽略的硬约束

| 约束 | 后果 |
|---|---|
| `jiffies` 一律用 `time_after()` 比较 | 32 位上 49.7 天绕回；**64 位不会出错但仍须遵守** |
| `timer_delete_sync()` 不能持"任何会在中断里被拿的锁" | **即使那把锁和定时器无关** ⇒ 死锁（01 §3.4） |
| 拆除路径用 `timer_shutdown_sync()` 而非 `timer_delete_sync()` | 否则并发重启会 use-after-free（05 §7.1、08 §9） |
