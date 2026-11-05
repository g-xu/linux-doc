---
title: 调试方法
---

# timer 07：调试与可观测性

## §0 按症状找章节

| 症状 | 先看 | 再看 |
|---|---|---|
| 定时器回调不来 / 来晚了 | §3（tracepoint） | §4（`/proc/timer_list`） |
| 系统时间跑偏 / 跳变 | §2.2（看门狗日志） | §5（sysfs 换时钟源） |
| 时间按固定比例偏 | §6.1 | 06 §4（`clock-frequency`） |
| 没进高精度模式 | §4.2 | 05 §3.2 |
| NO_HZ 没生效 | §6.2 | §3.3（`tick_stop` 事件） |
| 深度 idle 后系统很慢/不醒 | §6.3 | 06 §5.2（`always-on`） |
| 系统已经卡住，什么都读不了 | **§4.3（SysRq-q）** | — |
| 开 PREEMPT_RT 后延迟变差 | §6.4 | 03 §B2 |
| 想知道某个错误码什么意思 | §7 | 01 §14 |

---

## §1 编译期开关

### §1.1 影响行为的（不是调试开关，但决定你能看到什么）

| 选项 | 位置 | 不开的后果 |
|---|---|---|
| `CONFIG_HIGH_RES_TIMERS` | `kernel/time/Kconfig:191` | **永远低精度**，`hrtimer_resolution` 停在 `1/HZ`（03 §A6） |
| `CONFIG_NO_HZ_IDLE` | `kernel/time/Kconfig:108` | tick 永不停 |
| `CONFIG_CLOCKSOURCE_WATCHDOG` | `kernel/time/Kconfig:9` | **§2.2 那批日志一条都不会出现** |
| `CONFIG_SYSFS` | — | ⚠️ **§5 的整套 sysfs 都不存在**（02 §1.0.E：两个伪总线都在 `#ifdef CONFIG_SYSFS` 里） |
| `CONFIG_PROC_FS` | — | `/proc/timer_list` 不存在 |

### §1.2 纯调试开关

| 选项 | 位置 | 给什么 |
|---|---|---|
| `CONFIG_DEBUG_TIMEKEEPING` | — | **换一个带校验的 `timekeeping_get_delta()`**（`kernel/time/timekeeping.c:240` 而不是 `:284`，02 §3.1）⇒ 读数越界会报 |
| `CONFIG_DEBUG_OBJECTS_TIMERS` | — | **timer 的对象生命周期校验**：用未初始化的 timer、重复释放都会被抓 |
| `CONFIG_DYNAMIC_DEBUG` | — | **§2.1 的 `pr_debug` 要靠它才能打开** |
| `CONFIG_TIME_KUNIT_TEST` | `kernel/time/Kconfig:67` | 时间换算的 KUnit 测试（04 §11） |
| `CONFIG_CLOCKSOURCE_WATCHDOG_MAX_SKEW_US` | `kernel/time/Kconfig:199` | 调看门狗容差 |

⚠️ **`CONFIG_DEBUG_OBJECTS_TIMERS` 是本子系统最有用的调试开关**：
`timer_list` 的典型 bug（用了 `kfree` 掉的对象、忘了 `timer_setup()`）
在没有它的时候表现为随机崩溃，有它的时候直接指出哪一行。

### §1.3 三道门：`pr_debug` 要全开才看得见

想看到 `kernel/time/` 里的 `pr_debug` 输出，**三个条件都要满足**：

```bash
# 门 1：编译进去了
zcat /proc/config.gz | grep CONFIG_DYNAMIC_DEBUG      # 期望 =y

# 门 2：运行期打开
echo 'file kernel/time/*.c +p' > /sys/kernel/debug/dynamic_debug/control

# 门 3：console loglevel 够低
echo 8 > /proc/sys/kernel/printk
```

⚠️ **只做门 2 是最常见的失败**——日志进了 dmesg 但 console 看不到。

---

## §2 日志

### §2.1 可见性等级（细分到变体）

**`kernel/time/` 的日志按"你能不能看到"分四档**，不是简单的"有/没有"：

| 档 | 变体 | 默认可见吗 | 说明 |
|---|---|---|---|
| 1 | `pr_err` / `pr_warn` | **可见** | 真正的故障 |
| 2 | `pr_info` | **可见** | 状态通告（换时钟源等） |
| 3 | `pr_warn_once` / `WARN_ONCE` / `WARN_ON_ONCE` | **只出现一次** | ⚠️ **重启后才会再出现** ⇒ 抓到一次就要留住 dmesg |
| 4 | `pr_debug` | **默认不可见** | 要过 §1.3 那三道门 |
| 5 | `printk_deferred` | 可见，但**延后输出** | ⚠️ **顺序可能和别的日志对不上**——它用在持锁/中断路径里（`kernel/time/clockevents.c:206` 和 `:220`） |

⚠️ **`printk_deferred` 这一档值得特别注意**：`clockevents.c` 用它是因为
那两处在关中断持锁的路径里，直接 `printk` 会死锁。
⇒ **所以看到 clockevent 的警告出现在"不该出现的位置"时，不要以为是别的问题**。

### §2.2 时钟源看门狗的日志（本子系统最重要的一批）

**全部逐字来自 `kernel/time/clocksource.c`**，看到就说明时钟源有问题：

| 日志（逐字） | 位置 | 含义 |
|---|---|---|
| `timekeeping watchdog on CPU%d: Marking clocksource '%s' as unstable because the skew is too large:` | `kernel/time/clocksource.c:499` | **偏差超容差，该时钟源被判不稳定** ⇒ 后面紧跟两行给出 `wd_nsec`/`cs_nsec` 细节（`:501`、`:503`） |
| `'%s' is current clocksource.` | `kernel/time/clocksource.c:506` | **坏的正是当前在用的** ⇒ 会触发降级 |
| `'%s' (not '%s') is current clocksource.` | `kernel/time/clocksource.c:508` | 坏的不是当前用的 ⇒ 影响小 |
| `No current clocksource.` | `kernel/time/clocksource.c:510` | — |
| `timekeeping watchdog on CPU%d: %s read-back delay of %lldns, attempt %d, marking unstable` | `kernel/time/clocksource.c:267` | **读时钟源本身太慢**（不是偏差问题） |
| `timekeeping watchdog on CPU%d: %s retried %d times before success` | `kernel/time/clocksource.c:247` | 重试成功了 ⇒ 硬件不太稳但还能用 |
| `Long readout interval, skipping watchdog check: cs_nsec: %lld wd_nsec: %lld` | `kernel/time/clocksource.c:489` | **本次检查被跳过**（系统太忙）⇒ 不是硬件问题 |
| `Checking clocksource %s synchronization from CPU %d to CPUs %*pbl.` | `kernel/time/clocksource.c:360` | 每 CPU 一致性校验开始 |
| `CPUs %*pbl ahead of CPU %d for clocksource %s.` / `... behind ...` | `kernel/time/clocksource.c:385` / `:388` | **各 CPU 读数不一致** |
| `Not enough CPUs to check clocksource '%s'.` | `kernel/time/clocksource.c:356` | 单 CPU 系统，跳过 |
| `Nonstop clocksource %s should not supply suspend/resume interfaces` | `kernel/time/clocksource.c:746` | **驱动写错了**：声明了 `SUSPEND_NONSTOP` 却又提供 suspend 钩子（05 §2.1） |
| `Failed to enable the non-suspend-able clocksource.` | `kernel/time/clocksource.c:806` | ⚠️ **`pr_warn_once`** ⇒ 只出现一次 |
| `Override clocksource %s is unstable and not HRT compatible - cannot switch while in HRT/NOHZ mode` | `kernel/time/clocksource.c:1017` | **你从 sysfs 手工换时钟源被拒了**（§5.1） |
| `Override clocksource %s is not currently HRT compatible - deferring` | `kernel/time/clocksource.c:1025` | 换的动作被延后 |

**怎么用这张表**：`dmesg | grep -i 'timekeeping watchdog'` 是排查
"时间跳变 / 性能计数不对"的**第一条命令**。

### §2.3 `WARN` 类：定时器误用的直接指认

| `WARN` | 位置 | 你干了什么 |
|---|---|---|
| `WARN_ON_ONCE(timer_pending(timer))` | `kernel/time/timer.c:1242` · `:1264` | **对已 pending 的定时器调了 `add_timer()`/`add_timer_on()`** ⇒ 该用 `mod_timer()`（01 §3.2） |
| `WARN_ON(in_irq() && !(timer->flags & TIMER_IRQSAFE))` | `kernel/time/timer.c:1563` | **在中断上下文里 `timer_delete_sync()` 了一个非 irqsafe 的定时器**（01 §3.4） |
| `WARN_ONCE(1, "timer: %pS preempt leak: %08x -> %08x\n", ...)` | `kernel/time/timer.c:1706` | **你的回调泄漏了 preempt_count**（进出不平衡）——`%pS` 会打出**是哪个回调** |
| `WARN_ON_ONCE(flags & ~TIMER_INIT_FLAGS)` | `kernel/time/timer.c:847` | `timer_setup()` 传了非法 flag |
| `WARN_ON(!evt->mult)` / `WARN_ON(!dev->mult)` | `kernel/time/clockevents.c:38` · `:161` | **clockevent 的 `mult` 是 0** ⇒ 注册路径没算对 |
| `WARN_ONCE(!clockevent_state_oneshot(dev), ...)` | `kernel/time/clockevents.c:125` · `:319` | **状态机不一致**：想编程单次事件但设备不在 oneshot 态 |
| `WARN_ON_ONCE(expires < 0)` | `kernel/time/clockevents.c:310` | 要求编程到负时刻 |

**`timer.c:1706` 那条最值得记**：它是唯一会**指名道姓打出你的回调函数**的
警告（`%pS`）。定时器回调里 `preempt_disable()` 没配对时就靠它定位。

---

## §3 tracepoint：17 个，而且没有 TP_CONDITION

**和 of 那套形成鲜明对比**（of 一个 tracepoint 都没有）——
timer 的 tracepoint 覆盖相当完整。

```bash
grep -cE '^(TRACE_EVENT|DEFINE_EVENT)\(' include/trace/events/timer.h       # → 13 个事件
grep -cE '^(TRACE_EVENT|DEFINE_EVENT)\(' include/trace/events/alarmtimer.h  # → 4 个事件
grep -n 'TP_CONDITION' include/trace/events/timer.h                          # → 无输出
```

⇒ **17 个事件，零 `TP_CONDITION`** ⇒ **打开就一定会触发**，
不存在"开了但因为条件不满足所以看不到"的情况。这一点很重要：
如果你打开了却没看到，就是**那条路真的没走**。

### §3.1 事件清单

| 事件 | 声明位置 | 什么时候发 |
|---|---|---|
| `timer:timer_init` | `include/trace/events/timer.h:33` | `timer_setup()` |
| `timer:timer_start` | `include/trace/events/timer.h:53` | `mod_timer()` / `add_timer()`；**带 `expires` 和 `flags`** |
| `timer:timer_expire_entry` | `include/trace/events/timer.h:92` | **回调即将执行** |
| `timer:timer_expire_exit` | `include/trace/events/timer.h:127` | **回调执行完** |
| `timer:timer_cancel` | `include/trace/events/timer.h:138` | `timer_delete*()` |
| `timer:hrtimer_init` | `include/trace/events/timer.h:173` | `hrtimer_init()`；带 `clockid` 和 `mode` |
| `timer:hrtimer_start` | `include/trace/events/timer.h:202` | 带**软/硬到期时间两个值**（02 §5.3） |
| `timer:hrtimer_expire_entry` | `include/trace/events/timer.h:239` | **回调即将执行** |
| `timer:hrtimer_expire_exit` | `include/trace/events/timer.h:286` | 回调执行完 |
| `timer:hrtimer_cancel` | `include/trace/events/timer.h:297` | `hrtimer_cancel()` |
| `timer:itimer_state` | `include/trace/events/timer.h:311` | `setitimer()` |
| `timer:itimer_expire` | `include/trace/events/timer.h:348` | itimer 到期 |
| `timer:tick_stop` | `include/trace/events/timer.h:406` | **NO_HZ 停 tick**（§3.3） |
| `alarmtimer:alarmtimer_suspend` | `include/trace/events/alarmtimer.h:23` | **挂起时编程 RTC** |
| `alarmtimer:alarmtimer_fired` | `include/trace/events/alarmtimer.h:73` | alarm 到期 |
| `alarmtimer:alarmtimer_start` | `include/trace/events/alarmtimer.h:80` | `alarm_start()` |
| `alarmtimer:alarmtimer_cancel` | `include/trace/events/alarmtimer.h:87` | `alarm_cancel()` |

### §3.2 成对事件都是完整的

**两组 entry/exit 都齐全**：

| 组 | entry | exit |
|---|---|---|
| `timer_list` | `timer_expire_entry` | `timer_expire_exit` |
| `hrtimer` | `hrtimer_expire_entry` | `hrtimer_expire_exit` |

⇒ **所以"回调跑了多久"可以直接测**：`exit` 时间戳减 `entry` 时间戳。
**这是排查"定时器回调拖慢系统"的标准手段**，也是 03 §B6 那个
`nr_hangs` 计数器背后的现象。

### §3.3 `tick_stop`：调用点最多的一个

```bash
grep -c 'trace_tick_stop' kernel/time/tick-sched.c    # → 7 处
```

**7 个调用点**是本子系统 tracepoint 调用点最多的——
因为 `tick_nohz_next_event()`（03 §B5）有多个"不能停"的出口，
**每个出口都发一次事件并带上原因码**。
⇒ **这是排查"NO_HZ 为什么没生效"的唯一有效手段**（§6.2）。

### §3.4 常用命令

```bash
cd /sys/kernel/tracing

# ① 看某个定时器从启动到到期的全过程
echo 1 > events/timer/timer_start/enable
echo 1 > events/timer/timer_expire_entry/enable
echo 1 > events/timer/timer_expire_exit/enable
echo 1 > tracing_on ; sleep 2 ; echo 0 > tracing_on
cat trace | head -50

# ② 只看某个函数的定时器（用 filter）
echo 'function == 0xffffffff81234567' > events/timer/timer_start/filter

# ③ 量回调耗时：entry/exit 配对
echo 1 > events/timer/hrtimer_expire_entry/enable
echo 1 > events/timer/hrtimer_expire_exit/enable

# ④ 查 NO_HZ 为什么不停 tick
echo 1 > events/timer/tick_stop/enable

# ⑤ alarmtimer（另一个子系统目录）
echo 1 > events/alarmtimer/enable
```

⚠️ **上面的输出格式和具体字段没有实测**（99 §5.1 登记）——
字段名是从 `include/trace/events/timer.h` 的 `TP_STRUCT__entry` 读出来的，
但**没有在真机上跑过一次**。

---

## §4 `/proc/timer_list` 与 SysRq

### §4.1 权限和范围

| 属性 | 值 |
|---|---|
| 权限 | **`0400`**（`kernel/time/timer_list.c:353`）⇒ **只有 root 能读** |
| 内容 | 每 CPU 的**全部 hrtimer** + 每 CPU 的 tick 设备状态 |
| ⚠️ **不含什么** | ⚠️ **时间轮里的 `timer_list` 定时器一个都没有** |

**"不含 timer_list"这一点内核自己说得很明白**——SysRq 的 action 消息是：

```
Show clockevent devices & pending hrtimers (no others)
```
—— `drivers/tty/sysrq.c` 里 `sysrq_show_timers_op` 的 `.action_msg`

**`(no others)` 这三个字就是官方确认**。⇒ 想看低精度定时器只能靠 §3 的 tracepoint。

### §4.2 五条最有用的读法

```bash
# ① 有没有真的进高精度模式（最常用的一条）
grep -m2 resolution /proc/timer_list
#   期望：.resolution: 1 nsecs
#   ⚠️ 看到 .resolution: 1000000 nsecs 就是 1ms = 1/HZ ⇒ 还在低精度

# ② tick 设备是谁
grep -A4 'Tick Device' /proc/timer_list
#   arm64 期望：arch_sys_timer

# ③ 编程失败与 hang 计数（03 §B6 的三个指标）
grep -E 'events|retries|hangs|max_hang' /proc/timer_list
#   retries 持续增长 ⇒ min_delta 设小了（05 §3.1）
#   hangs 非 0    ⇒ 有回调跑得太久

# ④ 某个 CPU 上挂了哪些 hrtimer
sed -n '/cpu: 0/,/cpu: 1/p' /proc/timer_list

# ⑤ 有没有广播设备在干活（06 §5.2 那个 always-on 问题的判据）
grep -i broadcast /proc/timer_list
```

**`.resolution` 那一行的格式是逐字确认的**：
`kernel/time/timer_list.c:104` 是 `SEQ_printf(m, "  .resolution: %u nsecs\n", hrtimer_resolution)`。

### §4.3 SysRq-q：系统卡住时的唯一出路

| 项 | 值 |
|---|---|
| **按键** | **`q`** |
| 帮助文字 | `show-all-timers(q)` |
| 动作文字 | `Show clockevent devices & pending hrtimers (no others)` |
| 注册位置 | `drivers/tty/sysrq.c:491`（`/* q */` 注释） |
| 处理函数 | `sysrq_handle_show_timers()` → `sysrq_timer_list_show()`（`kernel/time/timer_list.c:265`） |

```bash
# 串口/控制台上
Alt-SysRq-q
# 或
echo q > /proc/sysrq-trigger
```

**它和 `/proc/timer_list` 输出同一套信息**，但走 `printk` 而不是 seq_file
⇒ **`/proc` 都读不动的时候还能用**。

**另一个触发点**：`kernel/panic.c:221` 也会调 `sysrq_timer_list_show()`
⇒ **某些 panic 会自动把定时器状态打出来**，事后分析 vmcore/日志时要记得找。

---

## §5 sysfs：五个文件，三个可写

**落点不是 `/sys/bus/`**（02 §1.0.E 讲了为什么）：

```
/sys/devices/system/clocksource/clocksource0/
    current_clocksource      RW
    available_clocksource    RO
    unbind_clocksource       WO
/sys/devices/system/clockevents/clockevent0/     （每 CPU 一个）
    current_device           RO
    unbind_device            WO
```

### §5.1 逐个文件的 setter 语义

| 文件 | 模式 | 声明位置 | 写进去会发生什么 |
|---|---|---|---|
| `current_clocksource` | **RW** | `kernel/time/clocksource.c:1378`（`DEVICE_ATTR_RW`） | **写一个名字 ⇒ 强制切到那个时钟源**。⚠️ **可能被拒**：不稳定或不支持高精度的时钟源在 HRT/NOHZ 模式下切不过去，会打 §2.2 那两条 `Override clocksource ...` 日志（`kernel/time/clocksource.c:1017`、`:1025`） |
| `available_clocksource` | **RO** | `kernel/time/clocksource.c:1449`（`DEVICE_ATTR_RO`） | — |
| `unbind_clocksource` | **WO** | `kernel/time/clocksource.c:1413`（`DEVICE_ATTR_WO`） | **写名字 ⇒ 把该时钟源摘掉**（触发重新选优）。**写正在用的那个也允许**——核心会先选一个替代 |
| `current_device` | **RO** | `kernel/time/clockevents.c:684`（`DEVICE_ATTR_RO`） | — |
| `unbind_device` | **WO** | `kernel/time/clockevents.c:717`（`DEVICE_ATTR_WO`） | **写名字 ⇒ 解绑该事件设备**。⚠️ **正在当 tick 源的会返回 `-EBUSY`**（01 §14.1 那 14 个 `-EBUSY` 的主要来源） |

### §5.2 最有用的三条命令

```bash
# ① 确认时钟源选对了（arm64 期望 arch_sys_counter）
cat /sys/devices/system/clocksource/clocksource0/current_clocksource

# ② 看有哪些候选
cat /sys/devices/system/clocksource/clocksource0/available_clocksource

# ③ 二分法：怀疑时钟源有问题时切一个试试
echo arch_sys_counter > /sys/devices/system/clocksource/clocksource0/current_clocksource
dmesg | tail            # 一定要看日志确认切成功了，写入成功 ≠ 切换成功
```

⚠️ **第 ③ 条的关键**：`echo` 返回 0 **不代表切换发生了**。
必须回读 `current_clocksource` 或看 dmesg——被拒的路径只打日志，不报错。

### §5.3 arm64 上时钟源名字的对照

| 名字 | 是什么 | 位置 |
|---|---|---|
| `arch_sys_counter` | **架构定时器（正常应该是这个）** | `drivers/clocksource/arm_arch_timer.c:233` |
| `arch_mem_timer` | MMIO 变体 | `drivers/clocksource/arm_arch_timer.c:873` |
| ⚠️`jiffies` | **兜底档，出现它就是失败信号** | `kernel/time/jiffies.c:33`（rating=1，03 §A4） |
| ⚠️`refined-jiffies` | 略好的兜底 | `kernel/time/jiffies.c:86` |

**clockevent 名字**：`arch_sys_timer`（`drivers/clocksource/arm_arch_timer.c:848`）。

---

## §6 六类问题的定位

### §6.1 时间按固定比例偏（`sleep 10` 实际 20 秒）

| 步 | 命令 | 判据 |
|---|---|---|
| 1 | `cat /sys/devices/system/clocksource/clocksource0/current_clocksource` | 是 `jiffies` ⇒ 跳到 §6.5 |
| 2 | `dmesg \| grep -i 'clock-frequency\|arch_timer'` | 看有没有频率相关信息 |
| 3 | `dtc -I fs /sys/firmware/devicetree/base \| grep -A3 armv8-timer` | **DTS 里有没有 `clock-frequency`** |
| 4 | 对比 `CNTFRQ_EL0` 与 DTS 的值 | **不一致就是根因**（06 §4） |

**结论几乎总是 `clock-frequency` 写错了**——因为它会**覆盖硬件寄存器**，
而且不产生任何日志。

### §6.2 NO_HZ 没生效

```bash
# ① 配置层
zcat /proc/config.gz | grep -E 'NO_HZ|HZ_PERIODIC'

# ② NO_HZ_FULL 有没有真的在跑
cat /sys/devices/system/cpu/nohz_full 2>/dev/null
#    ⚠️ 没有这个文件 ⇒ 内核没开 NO_HZ_FULL

# ③ 最有效的一条：看 tick_stop 事件（§3.3）
cd /sys/kernel/tracing
echo 1 > events/timer/tick_stop/enable
echo 1 > tracing_on ; sleep 3 ; echo 0 > tracing_on
cat trace | grep tick_stop | head -20
#    事件里带"为什么不能停"的原因码
```

**四个常见原因**（03 §B5 的四道门 + 01 §10 的四个约束）：
① 有未处理的 timer softirq；② 有 hrtimer 快到期；
③ **时钟源的 `max_idle_ns` 上限太小**（32 位窄计数器）；
④ **POSIX CPU 定时器在用**（`NO_HZ_FULL` 专属，04 §6.3）。

### §6.3 深度 idle 后系统很慢或不醒

**最可能是 `always-on` 写错了**（06 §5.2）。

```bash
# ① 有没有广播设备在干活
grep -i -A5 broadcast /proc/timer_list

# ② DTS 里写了 always-on 吗
ls /sys/firmware/devicetree/base/timer*/ | grep always-on
#    有这个文件 ⇒ 声明了 always-on ⇒ 不会安排广播
#    ⇒ 如果硬件其实会停，就是这里错了
```

**判据**：**声明了 `always-on` 但 `/proc/timer_list` 里看不到广播设备，
同时深度 idle 有问题** ⇒ 把 `always-on` 删掉重试。

### §6.4 开 PREEMPT_RT 后延迟变差

**先确认自己的 hrtimer 用的是 `_SOFT` 还是 `_HARD`**（05 §1.4）：

```bash
# hrtimer_init 事件带 mode 字段
cd /sys/kernel/tracing
echo 1 > events/timer/hrtimer_init/enable

# 看 timersd 线程的优先级和 CPU 占用（RT 内核才有）
ps -eLo pid,tid,cls,rtprio,comm | grep timersd
```

**RT 上定时器回调跑在 `timersd`（FIFO 低优先级，03 §B2.2）**
⇒ 如果你的实时任务优先级更高，它会抢占定时器 ⇒
**定时器抖动变大是设计预期，不是 bug**。
⚠️ **要低抖动就必须用 `_HARD`**，代价是回调里不能睡。

### §6.5 `current_clocksource` 是 `jiffies`

**说明架构定时器根本没注册上**。

```bash
# ① DT 节点在不在
ls /sys/firmware/devicetree/base/ | grep -i timer

# ② 节点属性对不对
ls /sys/firmware/devicetree/base/timer/

# ③ 中断解析成功了吗
cat /proc/interrupts | grep -i arch_timer

# ④ 有没有 panic 前的线索（arm64 拿不到频率会直接 panic）
dmesg | grep -i 'architected timer'
```

⚠️ **arm64 上这个状态其实很难出现**——因为
`arch/arm64/kernel/time.c:66` 拿不到频率会直接 `panic("Unable to initialise architected timer.\n")`。
⇒ **如果你真看到 `jiffies`，说明频率拿到了但注册失败了**，
或者你在一个非常特殊的配置上。

### §6.6 定时器回调不来 / 来晚了

| 步 | 做什么 |
|---|---|
| 1 | **开 `timer_start` + `timer_expire_entry`**（§3.4）——**看 start 有没有发生** |
| 2 | start 有、entry 没有 ⇒ 到期路径的问题 ⇒ 查 §4.2 的 `retries`/`hangs` |
| 3 | start 都没有 ⇒ **你的代码没调 `mod_timer()`，或之前调过 `timer_shutdown*()`**（01 §3.4） |
| 4 | entry 有但很晚 ⇒ **量 `expire_exit - expire_entry`** 看是不是别的回调拖的（§3.2） |
| 5 | 开 `CONFIG_DEBUG_OBJECTS_TIMERS`（§1.2）排除对象生命周期问题 |

**第 3 步是最常见的**：`timer_shutdown_sync()` 之后 `mod_timer()`
被**静默丢弃**（返回 0，不报错）⇒ `timer_start` 事件也不会发。
⇒ **"`timer_start` 事件完全没有"是这个 bug 的确定性判据。**

---

## §7 错误码分诊

### §7.1 全量普查（01 §14.1 的原始数据）

统计范围：`kernel/time/` 下 18 个非测试 `.c`（显式列文件，命令见 99 §3.3）。

| 错误码 | 次数 | 含义 |
|---|---|---|
| `-EINVAL` | **61** | 参数不合法 |
| `-EFAULT` | **56** | **`copy_to/from_user()` 失败** ⇒ 几乎全在 POSIX 那一层（04 §6） |
| `-ENODEV` | **21** | 时钟源/事件设备不存在或不可用 |
| `-EBUSY` | **14** | **设备正在当 tick 源，不能解绑**（§5.1 的 `unbind_device`） |
| `-EOPNOTSUPP` | 10 | 该时钟不支持这个操作 |
| `-EPERM` | 8 | 改系统时间要 `CAP_SYS_TIME` |
| `-EAGAIN` | 7 | 稍后重试 |
| `-ENOSYS` / `-EINTR` | 4 / 4 | 功能未编译 / 被信号打断 |
| `-EACCES` | 3 | — |
| `-ENOMEM` / `-EOVERFLOW` | 1 / 1 | — |
| `-ENOENT` / `-ETIMEDOUT` / `-ERESTARTSYS` / `-ENOTSUPP` | **0** | **本子系统不返回这四个** |

### §7.2 拿到某个码先查什么

| 拿到 | 先查 |
|---|---|
| `-EBUSY`（解绑设备） | `cat .../current_device` —— 它就是当前 tick 源 |
| `-EINVAL`（`hrtimer_*`） | **`clock_id` 或 `mode` 写错**（01 §4.1） |
| `-EPERM`（改时间） | `CAP_SYS_TIME` |
| `-EOPNOTSUPP`（对时钟操作） | **该 POSIX 时钟是只读的**（04 §7） |
| `-1`（不是错误码） | **`hrtimer_try_to_cancel()` 的"回调正在跑"**（05 §7.2） |
| `0`（不是错误码） | `mod_timer()` 的"之前未 pending"；⚠️ 也可能是 **shutdown 后被丢弃**（01 §3.4） |

⚠️ **本子系统一大类 API 根本不返回错误码**（03 §C2）——
`hrtimer_start()` / `add_timer()` / `ktime_get*()` / `clockevents_config_and_register()`
全是 `void` 或非错误返回值。⇒ **这些路径上的问题只能靠 §2.3 的 `WARN` 和
§4 的 `/proc/timer_list` 发现，没有错误码可查。**

---

## §8 故障注入：本子系统**没有**

```bash
grep -rn 'fault_inject\|should_fail\|DECLARE_FAULT_ATTR\|CONFIG_FAIL' kernel/time/*.c
# → 无输出
```

**`kernel/time/` 下没有任何故障注入框架的接入点。**

**替代手段有三个**：

| 手段 | 怎么做 | 能模拟什么 |
|---|---|---|
| **手工切坏时钟源** | `echo jiffies > .../current_clocksource`（§5.2） | **时钟精度突然变差** |
| **看门狗自测模块** | `insmod clocksource-wdtest.ko`（04 §5.3，`kernel/time/clocksource-wdtest.c:201`） | **故意注册一个坏时钟源**，验证看门狗会抓到 |
| **`test_udelay.ko`** | `insmod test_udelay.ko`（`kernel/time/test_udelay.c:147`） | 验证 `udelay()` 的准度（03 §A5 的 `lpj_fine`） |

⇒ **`clocksource-wdtest.c` 实际上就是本子系统的故障注入**，
只是它不用通用的 fault-injection 框架，而是自己写了一个坏时钟源。

---

## §9 一页速查

```bash
# ═══ 状态确认（先跑这四条）═══
cat /sys/devices/system/clocksource/clocksource0/current_clocksource   # 期望 arch_sys_counter
grep -m2 resolution /proc/timer_list                                   # 期望 1 nsecs
grep -E 'retries|hangs' /proc/timer_list                               # 期望都很小 / 0
dmesg | grep -i 'timekeeping watchdog'                                 # 期望空

# ═══ 定时器不到点 ═══
cd /sys/kernel/tracing
echo 1 > events/timer/timer_start/enable
echo 1 > events/timer/timer_expire_entry/enable
echo 1 > events/timer/timer_expire_exit/enable

# ═══ NO_HZ 不生效 ═══
echo 1 > /sys/kernel/tracing/events/timer/tick_stop/enable

# ═══ 系统卡死 ═══
echo q > /proc/sysrq-trigger        # SysRq-q
```

| 拿到 | 第一嫌疑 | 去 |
|---|---|---|
| `current_clocksource` = `jiffies` | 架构定时器没注册上 | §6.5 |
| `.resolution: 1000000 nsecs` | 没进高精度：clockevent 缺 `ONESHOT` 或 `CONFIG_HIGH_RES_TIMERS=n` | §4.2 · 05 §3.2 |
| `retries` 持续涨 | `min_delta` 设小了 | 05 §3.1 · 03 §B6 |
| `hangs` 非 0 | **有回调跑得太久** | §3.2 量 entry/exit |
| `timekeeping watchdog ... unstable` | 时钟源偏差过大 / 需要勘误属性 | §2.2 · 06 §8 |
| 完全没有 `timer_start` 事件 | **调过 `timer_shutdown*()` 之后又 `mod_timer()`** | §6.6 |
| 时间按比例偏 | `clock-frequency` 写错 | §6.1 · 06 §4 |
| 深度 idle 后不醒 | `always-on` 写错 | §6.3 · 06 §5.2 |
| `preempt leak` 警告 | 回调里 `preempt_disable()` 没配对（`%pS` 会点名） | §2.3 |
| 开 RT 后抖动大 | 用了 `_SOFT`，回调跑在 `timersd` 里 | §6.4 · 03 §B2 |

⚠️ **本篇所有命令的输出都没有实测**（99 §5.1）——
命令本身是从源码里的 `proc_create` / `DEVICE_ATTR` / `TRACE_EVENT` 反推的，
路径和文件名可信，**但输出的具体格式和字段没有在真机上核对过**。
唯一逐字确认的是 `.resolution: %u nsecs`（`kernel/time/timer_list.c:104`）
和 SysRq 的两句消息文字。
