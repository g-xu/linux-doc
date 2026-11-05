---
title: API 参考
---

# RTC 子系统 · 代码地图与对外 API

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇回答两类问题：**「这个东西在哪个文件里」** 和 **「我该调哪个函数」**。
> 行号不在本篇维护——需要精确到行的地方去 `03-internals.md`，取证命令去 `99-evidence.md`。

## §0 代码地图

### §0.1 核心与外围的分界

RTC 是**框架型**子系统：它没有自己的 `bus_type`，只有一个 `struct class`（`rtc_class`）。
驱动挂在各自的父总线上（I2C / SPI / platform / AMBA / MFD），通过 API 把自己注册进这个 class。

核心一共 **8 个 `.c` 文件、2987 行**，全部读完是可行的；外围是 **168 个 `rtc-*.c` 驱动**，只讲契约。

| 文件 | 职责 | 编进哪个 Kconfig |
| --- | --- | --- |
| `drivers/rtc/class.c` | `rtc_class` 创建、设备分配与注册、`hctosys`、class 级 PM | `RTC_CLASS`（`rtc-core-y`） |
| `drivers/rtc/interface.c` | 全部内核态 API：时间/闹钟读写、定时器队列、中断上报、偏移 | `RTC_CLASS`（`rtc-core-y`） |
| `drivers/rtc/dev.c` | `/dev/rtcN` 字符设备：`read`/`poll`/`ioctl`/`fasync`、UIE 模拟 | `RTC_INTF_DEV` |
| `drivers/rtc/sysfs.c` | `/sys/class/rtc/rtcN/*` 属性组、驱动追加属性组的入口 | `RTC_INTF_SYSFS` |
| `drivers/rtc/proc.c` | `/proc/driver/rtc`（只给 hctosys 那一个设备开） | `RTC_INTF_PROC` |
| `drivers/rtc/nvmem.c` | 把 RTC 里的备份 RAM 注册成 nvmem 设备 | `RTC_NVMEM` |
| `drivers/rtc/lib.c` | 纯计算：`time64_t` ↔ `struct rtc_time`、闰年、合法性校验 | `RTC_LIB`（独立于 `RTC_CLASS`） |
| `drivers/rtc/lib_test.c` | `lib.c` 的 KUnit 用例（16 万年逐日对账） | `RTC_LIB_KUNIT_TEST` |

`drivers/rtc/rtc-core.h` 是唯一的私有头（43 行），只做一件事：当 `RTC_INTF_DEV` / `RTC_INTF_PROC` / `RTC_INTF_SYSFS` 关掉时，把 `rtc_dev_prepare()` 等钩子替换成空的 `static inline`。所以 `class.c` 里那些调用**永远编得过**，不需要 `#ifdef`。

★ **「把系统时间写回 RTC」的代码不在 `drivers/rtc/` 里**。`CONFIG_RTC_SYSTOHC` 在 `drivers/rtc/Kconfig` 里定义，但实现（`update_rtc()` / `sync_hw_clock()`）住在 `kernel/time/ntp.c`。历史上有一个 `drivers/rtc/systohc.c`，在 5.11 被 `33e62e832384` 删掉了。本树里 `rtc_set_ntp_time` 这个名字**一处都不剩**。方向对照：

| 方向 | 谁触发 | 代码在哪 |
| --- | --- | --- |
| RTC → 系统时间（开机/唤醒一次性） | `__devm_rtc_register_device()` 里的 `rtc_hctosys()` | `drivers/rtc/class.c` |
| 系统时间 → RTC（NTP 同步后约每 11 分钟） | `ntp_notify_cmos_timer()` → `sync_hw_clock()` → `update_rtc()` | `kernel/time/ntp.c` |
| 挂起/恢复补偿睡眠时长 | class 级 `rtc_suspend()` / `rtc_resume()` | `drivers/rtc/class.c` |

### §0.2 头文件

| 头文件 | 内容 | 谁该 include |
| --- | --- | --- |
| `include/linux/rtc.h` | `struct rtc_device`、`struct rtc_class_ops`、`struct rtc_timer`、全部内核态 API、`RTC_TIMESTAMP_*` 常量 | 驱动、内核态消费者 |
| `include/uapi/linux/rtc.h` | `struct rtc_time` / `rtc_wkalrm` / `rtc_pll_info` / `rtc_param`、全部 ioctl 号、`RTC_FEATURE_*`、`RTC_PARAM_*`、`RTC_VL_*`、中断标志 | 驱动 + 用户态（`linux/rtc.h` 会自动带上） |
| `include/trace/events/rtc.h` | 12 个 tracepoint 定义（207 行；3 个 `DECLARE_EVENT_CLASS` + 9 个 `DEFINE_EVENT` + 3 个独立 `TRACE_EVENT`） | 只有 `interface.c` 定义它（`CREATE_TRACE_POINTS`） |
| `include/linux/rtc/*.h` | 5 个**芯片专属**头（`ds1286.h` / `ds1307.h` / `ds1685.h` / `m48t59.h` / `rtc-omap.h`） | 只有对应驱动和少数板文件 |

★ `include/linux/rtc.h` 的结构特点：它把 `#include <linux/device.h>` 等重头文件放在**第 37 行**，也就是 `rtc_tm_sub()` 之后。前 36 行只依赖 `linux/types.h` 和 uapi，是给纯计算场景留的轻量区。改这个头的时候不要把声明随手挪到前面去。

### §0.3 Kconfig 与 Makefile

`drivers/rtc/Kconfig` 一共 1999 行，`drivers/rtc/Makefile` 一个；整个 `drivers/rtc/` 下**没有子目录**（176 个 `.c` + 3 个 `.h`，共 77033 行，其中 168 个 `rtc-*.c` 驱动占 73955 行）。

真正影响核心行为的开关：

| 开关 | 作用 | 默认 |
| --- | --- | --- |
| `RTC_LIB` | 只编 `lib.c`（换算函数）。**不依赖 `RTC_CLASS`**，很多 arch 代码只要这个 | 被 `RTC_CLASS` / `RTC_MC146818_LIB` `select` |
| `RTC_CLASS` | 整个 class 框架（`menuconfig`，下面 1900 行都在 `if RTC_CLASS` 里） | — |
| `RTC_HCTOSYS` | 启动时用 RTC 设置系统时间 | `y` |
| `RTC_HCTOSYS_DEVICE` | 用哪个设备名，字符串，默认 `"rtc0"` | `"rtc0"` |
| `RTC_SYSTOHC` | NTP 同步后写回 RTC（实现在 `kernel/time/ntp.c`） | `y` |
| `RTC_SYSTOHC_DEVICE` | 写回哪个设备，默认跟随 `RTC_HCTOSYS_DEVICE` | 同上 |
| `RTC_INTF_SYSFS` / `RTC_INTF_PROC` / `RTC_INTF_DEV` | 三个用户态接口，各自可单独关 | `RTC_CLASS` |
| `RTC_INTF_DEV_UIE_EMUL` | 用轮询模拟 1Hz 更新中断（给硬件不支持的 RTC） | `n` |
| `RTC_NVMEM` | 编 `nvmem.c` | `y` |
| `RTC_DEBUG` | 给整个目录加 `-DDEBUG`，让 `dev_dbg()` 无条件输出 | `n` |
| `RTC_LIB_KUNIT_TEST` | 编 `lib_test.c` | `n` |

★ `RTC_DEBUG` 是 `ccflags-$(CONFIG_RTC_DEBUG) := -DDEBUG`，**作用范围是 `drivers/rtc/` 整个目录**，不是单个文件——打开它以后 168 个驱动里的 `dev_dbg()` 会一起开口。详见 `07-debug.md` §1。

## §1 设备分配与注册

头文件：`include/linux/rtc.h`；实现：`drivers/rtc/class.c`。

驱动侧只有一条推荐路径：**`devm_rtc_allocate_device()` → 填字段 → `devm_rtc_register_device()`**。中间那一段「填字段」是整个子系统里最容易漏的部分（见 §1.2）。

| API | 说明 | 备注 |
| --- | --- | --- |
| `devm_rtc_allocate_device(dev)` | 分配一个 `struct rtc_device`，分配 id，取名 `rtcN`，挂 devm 释放动作 | 返回 `ERR_PTR`；此时**还没有** `ops`，字符设备也还没建。`dev` 是父设备（I2C client / platform device） |
| `devm_rtc_register_device(rtc)` | 完成注册：查已有闹钟、建 `/dev/rtcN`、建 `/proc`、必要时跑 hctosys | 是宏，展开成 `__devm_rtc_register_device(THIS_MODULE, rtc)`，所以**必须在驱动自己的编译单元里调用** |
| `__devm_rtc_register_device(owner, rtc)` | 上面那个宏的真身 | 直接调它就得自己传 `THIS_MODULE`；没有理由这么做 |
| `devm_rtc_device_register(dev, name, ops, owner)` | 一步到位的老接口 | **已废弃**，kerneldoc 标了「use devm_rtc_allocate_device and rtc_register_device instead」。`name` 参数**完全不用**。本树仍有 47 个文件在用 |

★ 没有非 devm 的注册接口。`rtc_device_register()` 这个名字在本树里只剩 **4 处注释**，函数本身早就没了。

### §1.1 分配时已经替你做好的事

`rtc_allocate_device()`（`class.c` 内部静态函数）在返回之前就设好了这些默认值，驱动**不需要**重复设置：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `set_offset_nsec` | `NSEC_PER_SEC + 5 * NSEC_PER_MSEC` | 假设「写完 1 秒后秒进位」，外加 5ms 传输时间 |
| `irq_freq` | `1` | PIE 周期中断频率 |
| `max_user_freq` | `64` | 非特权用户能要求的最高 PIE 频率 |
| `dev.class` | `rtc_class` | — |
| `dev.groups` | `rtc_get_dev_attribute_groups()` | 9 个标准 sysfs 属性 |
| `features` | `RTC_FEATURE_ALARM` + `RTC_FEATURE_UPDATE_INTERRUPT` 两位置 1 | **默认认为你支持闹钟和 UIE** |
| `aie_timer` / `uie_rtctimer` / `pie_timer` | 已 init，回调已挂 | 三种中断源的软件侧 |

★★ `features` 的默认值是「乐观」的：`RTC_FEATURE_ALARM` 一开始就是 1。注册时只有一条自动纠正——`__devm_rtc_register_device()` 发现 `!rtc->ops->set_alarm` 就把它清掉。**如果你实现了 `set_alarm` 但硬件其实没有中断线，就必须自己 `clear_bit()`**，否则上层会一直以为闹钟可用。`RTC_FEATURE_UPDATE_INTERRUPT` 没有任何自动纠正，全靠驱动自觉。

### §1.2 注册前该填的字段

| 字段 | 什么时候必须填 | 不填的后果 |
| --- | --- | --- |
| `ops` | 总是 | `devm_rtc_register_device()` 返回 `-EINVAL` |
| `range_min` / `range_max` | 硬件位宽有限（几乎所有芯片） | `rtc_valid_range()` 直接放行，写入超范围时间后读回是垃圾；`range` sysfs 属性也不显示 |
| `set_offset_nsec` | 硬件不是「写后 1 秒进位」（如 MC146818 是 500ms） | NTP 写回会偏半秒到一秒 |
| `features` 的清除 | 硬件不支持闹钟 / 只支持分钟级 / 只支持 2 秒级 / 只在唤醒时报闹钟 | 上层按不存在的能力调用 |
| `uie_unsupported`（已不存在） | — | ★ 6.1 里**没有这个字段**，早期资料里的 `rtc->uie_unsupported = 1` 要改成 `clear_bit(RTC_FEATURE_UPDATE_INTERRUPT, rtc->features)` |
| `dev.parent` 的 wakeup 能力 | 想让 `wakealarm` sysfs 属性出现 | `rtc_attr_is_visible()` 会把 `wakealarm` 隐藏（判据是 `device_can_wakeup(parent)`） |

## §2 时间读写

头文件：`include/linux/rtc.h`；实现：`drivers/rtc/interface.c`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `rtc_read_time(rtc, tm)` | 读硬件当前时间到 `tm` | 内部先 `memset(tm, 0, …)` 再调 `ops->read_time`，然后加范围偏移、跑 `rtc_valid_tm()`。**用 `mutex_lock_interruptible` 拿锁**，所以除了 `-ENODEV` / `-EINVAL` 还可能返回 `-EINTR` |
| `rtc_set_time(rtc, tm)` | 把 `tm` 写进硬件 | ★ **会就地修改调用者的 `tm`**（见下）。顺序：校验 → 范围校验 → 减偏移 → 关 UIE → 写 → `pm_stay_awake(parent)` → `schedule_work(&rtc->irqwork)` → 恢复 UIE |
| `rtc_valid_tm(tm)` | 判断一个 `rtc_time` 是否自洽 | 在 `lib.c`，纯计算。**不看任何设备**，所以不管 `range_min/max`。拒绝 `tm_year < 70`（即 1970 之前）和 `tm_year > INT_MAX - 1900` |
| `rtc_read_offset(rtc, offset)` | 读走时校准值（ppb） | `ops->read_offset` 没实现返回 `-EINVAL`；`rtc->ops` 为 NULL 返回 `-ENODEV`。★ **未导出**，模块用不了 |
| `rtc_set_offset(rtc, offset)` | 写走时校准值（ppb） | 同上，同样**未导出** |

★★ `rtc_set_time()` 的就地修改：`rtc_subtract_offset(rtc, tm)` 会在必要时执行 `rtc_time64_to_tm(secs - rtc->offset_secs, tm)`，直接改写调用者传进来的结构。触发条件有两个，**同时满足才改**：设备启用了范围扩展（`offset_secs != 0`，来自 DT 的 `start-year` 属性），并且待写时间**落在硬件原生范围之外**。在没有 `start-year` 的设备上永远看不到这个副作用。要保留原值就自己先拷一份。

★ `rtc_valid_range()` 是 `static` 的，只有 `rtc_set_time()` 调。它的门槛是 `range_min != range_max`——**驱动没填范围的话这个检查整体跳过**。

### §2.1 `rtc_read_time()` 的调用者

`rtc_read_time()` 在 RTC 框架**外部**被以下文件调用：

| 调用者 | 用途 | arm64 相关？ |
|---|---|---|
| `kernel/time/alarmtimer.c:292` | `alarmtimer_suspend()` 读 RTC 时间计算休眠 delta | **是** |
| `kernel/time/ntp.c` | `update_rtc()` 读 RTC 验证写回 | **是** |
| `kernel/power/suspend_test.c:79` | 休眠测试 | **是** |
| `drivers/platform/x86/amd/pmc.c:723` | AMD PMC 电源管理 | 否（x86 专用） |
| `arch/alpha/kernel/rtc.c` | Alpha 架构 | 否 |

arm64 上真正用到的是前 3 个。框架**内部**调用 `rtc_read_time()` 的地方更多：`class.c`（hctosys + PM）、`dev.c`（ioctl + proc）、`sysfs.c`（date/time/since_epoch 属性）、`interface.c`（闹钟补全 + timerqueue 到期处理）。

## §3 闹钟

头文件：`include/linux/rtc.h`；实现：`drivers/rtc/interface.c`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `rtc_read_alarm(rtc, alrm)` | 读当前生效的闹钟时间与使能状态 | ★ **不一定读硬件**：核心自己维护 `rtc->aie_timer`，这个函数优先返回软件侧的值 |
| `rtc_set_alarm(rtc, alrm)` | 设一次性闹钟 | `!rtc->ops` → `-ENODEV`；`!ops->set_alarm` → `-EINVAL`；时间在过去 → `-ETIME`。会先 `rtc_valid_tm()` 再 `rtc_valid_range()` |
| `rtc_alarm_irq_enable(rtc, enabled)` | 单独开/关闹钟中断，不改时间 | 已经是目标状态时直接返回 0（不会重复下发） |
| `rtc_initialize_alarm(rtc, alrm)` | 注册期用：把硬件里已存在的闹钟接管进 `aie_timer` | 由 `__devm_rtc_register_device()` 调用。★ **已导出但全树 0 个外部调用者** |
| `__rtc_read_alarm(rtc, alrm)` | 从硬件"半个闹钟"反推完整时间 | ★ **未导出**，只有 `class.c` 注册路径在用。最绕的代码，逐段解释在 `03-internals.md` §4 |
| `rtc_update_irq_enable(rtc, enabled)` | 开/关 1Hz 更新中断（UIE） | 用的是 `uie_rtctimer`，即"每秒重排一次的闹钟"，不是硬件 UIE。★ **已导出但全树 0 个外部调用者** |

★ 核心把「闹钟」实现成了**软件队列上的一个条目**，而不是直接透传给硬件。`rtc->aie_timer` 是一个 `struct rtc_timer`，挂在 `rtc->timerqueue` 上；硬件寄存器里装的永远是**队列里最早那个到期时间**。因此：

- 用户设的闹钟和 `alarmtimer` 设的闹钟共用同一条队列，互不覆盖；
- `rtc_read_alarm()` 报的是软件视角，可能和你直接 dump 寄存器读到的不一样；
- 一次性闹钟触发后核心会自动把它从队列摘掉并重排下一个。

★★ `rtc_set_alarm()` 返回 `-ETIME` 的判据不止一处。`__rtc_set_alarm()` 里有两道：下发之前发现目标时间已过去，返回 `-ETIME`；下发之后**重新读一次时间**，发现 `scheduled - now <= 1` 也返回 `-ETIME`。第二道是为了堵「设置动作本身耗时超过了剩余时间」这个竞态——1 秒的余量是硬编码的。

## §4 中断上报（驱动 → 核心）

头文件：`include/linux/rtc.h`；实现：`drivers/rtc/interface.c`。

这一组是**驱动在中断处理里往上报**用的，方向和前面几节相反。

| API | 说明 | 备注 |
| --- | --- | --- |
| `rtc_update_irq(rtc, num, events)` | 通报「发生了 `num` 次 `events` 类型的中断」 | **可以在硬中断上下文调用**——它只做 `pm_stay_awake()` + `schedule_work()`。全树 112 个文件在用，是驱动侧第二常用的核心 API |
| `rtc_aie_update_irq(rtc)` | 闹钟中断的内部上报路径 | ★ **未导出**，`rtc_timer_do_work()` 内部用 |
| `rtc_uie_update_irq(rtc)` | UIE 的内部上报路径 | ★ **未导出** |
| `rtc_pie_update_irq(timer)` | PIE（周期中断）hrtimer 回调 | ★ **未导出**，签名收的是 `hrtimer *` |
| `rtc_handle_legacy_irq(rtc, num, mode)` | 老式路径：直接改 `irq_data`、唤醒 `irq_queue`、发 `SIGIO` | ★ **未导出**，被上面三个共用 |

★ 驱动只需要认识 `rtc_update_irq()` 一个。其余四个虽然声明在公开头文件里，但一个都没导出，模块里链接不上。

★★ `rtc_update_irq()` **不会**让 `rtc_read_alarm()` 的返回值立即变化，也不会把 `aie_timer` 摘掉。摘队列发生在 `rtc_timer_do_work()` 里——`irqwork` 真正跑起来之后。「驱动报了中断」和「核心认为闹钟已经过去」之间有一个工作队列的延迟。

## §5 定时器队列

头文件：`include/linux/rtc.h`；实现：`drivers/rtc/interface.c`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `rtc_timer_init(timer, f, data)` | 初始化一个 `struct rtc_timer`，挂上到期回调 | ★ **未导出**。`f` 的原型是 `void (*)(void *)` |
| `rtc_timer_start(rtc, timer, expires, period)` | 入队。`period` 非 0 表示周期性 | ★ **未导出**。会触发一次硬件闹钟重排 |
| `rtc_timer_cancel(rtc, timer)` | 出队 | ★ **未导出** |
| `rtc_timer_do_work(work)` | 到期处理：摘队列、发中断、重排下一个 | ★ **未导出**。是 `rtc->irqwork` 的回调 |

★★ 这四个函数全部**没有 `EXPORT_SYMBOL`**，而全树唯一的外部使用者是 `kernel/time/alarmtimer.c`——它是 `obj-y` 编进内核的，所以链接得上。**RTC 的定时器队列 API 事实上是 alarmtimer 的专用后门，模块无权使用。** 想让 `CLOCK_REALTIME_ALARM` 之外的东西用上 RTC 唤醒，路径不是调这几个函数，而是走 `rtc_set_alarm()` 或者用户态的 `RTC_WKALM_SET` ioctl。

`alarmtimer` 获取 `rtc_device` 的方式也不走 §8 的 `rtc_class_open()`——它注册了一个 `struct class_interface`（`.class = rtc_class`），在 `.add_dev` 回调里挑第一个「支持闹钟且父设备 `device_may_wakeup()`」的 RTC 记下来。完整分析在 `04-extensions.md` §2。

## §6 sysfs 属性

头文件：`include/linux/rtc.h`（`rtc_add_group` / `rtc_add_groups`）；实现：`drivers/rtc/sysfs.c`。

`/sys/class/rtc/rtcN/` 下的 9 个标准属性：

| 属性 | 读写 | 来源 | 可见性条件 |
|------|------|------|-----------|
| `name` | 只读 | `dev_name()` | 始终 |
| `date` | 只读 | `rtc_read_time()` | 始终 |
| `time` | 只读 | `rtc_read_time()` | 始终 |
| `since_epoch` | 只读 | `rtc_read_time()` | 始终 |
| `max_user_freq` | 读写 | `rtc->max_user_freq` | 始终 |
| `hctosys` | 只读 | 是否 `RTC_HCTOSYS_DEVICE` | 始终 |
| `wakealarm` | 读写 | `rtc_wkalrm` | `device_can_wakeup(parent)` 为真 |
| `offset` | 读写 | `rtc_read_offset` / `rtc_set_offset` | `ops->read_offset` 或 `ops->set_offset` 存在 |
| `range` | 只读 | `range_min` / `range_max` | `range_min != range_max` |

可见性由 `rtc_attr_is_visible()`（`drivers/rtc/sysfs.c:279`）控制。`wakealarm` 需要父设备支持唤醒——如果 I2C 控制器没声明 wakeup 能力，这个属性不会出现。

驱动追加自定义属性组：`rtc_add_group(rtc, grp)` / `rtc_add_groups(rtc, grps)`（`drivers/rtc/sysfs.c:320-346`）。需 `CONFIG_RTC_INTF_SYSFS`。这两个函数是**裸 `EXPORT_SYMBOL`**（非 GPL），任何模块都能用。

## §7 lib.c 工具函数

实现：`drivers/rtc/lib.c`（214 行）。纯计算，不碰任何设备。`RTC_LIB` 独立于 `RTC_CLASS`——arch 代码可以只用换算函数而不引入整个框架。

| 函数 | 导出 | 说明 |
|------|------|------|
| `rtc_month_days(month, year)` | `EXPORT_SYMBOL` | 某月天数 |
| `rtc_year_days(day, month, year)` | `EXPORT_SYMBOL` | 年内第几天 |
| `rtc_time64_to_tm(secs, tm)` | `EXPORT_SYMBOL` | `time64_t` → `struct rtc_time`（Neri-Schneider 算法） |
| `rtc_valid_tm(tm)` | `EXPORT_SYMBOL` | 校验 `rtc_time` 自洽性（`tm_year >= 70`） |
| `rtc_tm_to_time64(tm)` | `EXPORT_SYMBOL` | `struct rtc_time` → `time64_t`（inline，`include/linux/rtc.h:24`） |
| `rtc_tm_to_ktime(tm)` | `EXPORT_SYMBOL_GPL` | → `ktime_t` |
| `rtc_ktime_to_tm(kt)` | `EXPORT_SYMBOL_GPL` | `ktime_t` → `struct rtc_time` |
| `is_leap_year(year)` | inline | 闰年判断（`include/linux/rtc.h:222`） |
| `rtc_tm_sub(lhs, rhs)` | inline | 两个 `rtc_time` 的秒差（`include/linux/rtc.h:32`） |

★ `rtc_time64_to_tm()` 使用的 Neri-Schneider 算法在 `lib.c:58-152`，有完整的数学推导注释。`lib_test.c` 的 KUnit 测试覆盖了从 0000-01-01 到 9999-12-31 的逐日对账（约 365 万次迭代）。

## §8 声明了但没导出的函数

以下函数在 `include/linux/rtc.h` 里有原型，但**没有 `EXPORT_SYMBOL`**——`obj-m` 模块链接不上。只有编进内核（`obj-y` / `obj-$(CONFIG_...)=y`）的代码能用。

| 函数 | 实现位置 | 实际使用者 |
|------|----------|-----------|
| `__rtc_read_alarm()` | `drivers/rtc/interface.c:212` | `class.c` 注册路径 |
| `rtc_update_irq_enable()` | `drivers/rtc/interface.c:580` | 全树 0 个外部调用者 |
| `rtc_initialize_alarm()` | `drivers/rtc/interface.c:517` | 全树 0 个外部调用者 |
| `rtc_read_offset()` | `drivers/rtc/interface.c:1068` | 仅 sysfs.c |
| `rtc_set_offset()` | `drivers/rtc/interface.c:1103` | 仅 sysfs.c |
| `rtc_timer_init()` | `drivers/rtc/interface.c:1008` | alarmtimer（`obj-y`） |
| `rtc_timer_start()` | `drivers/rtc/interface.c:1025` | 同上 |
| `rtc_timer_cancel()` | `drivers/rtc/interface.c:1049` | 同上 |
| `rtc_timer_do_work()` | `drivers/rtc/interface.c:928` | 仅 `irqwork` 回调 |
| `rtc_handle_legacy_irq()` | `drivers/rtc/interface.c:645` | 仅 interface.c 内部 |
| `rtc_aie_update_irq()` | `drivers/rtc/interface.c:664` | 同上 |
| `rtc_uie_update_irq()` | `drivers/rtc/interface.c:675` | 同上 |
| `rtc_pie_update_irq()` | `drivers/rtc/interface.c:711` | 同上（hrtimer 回调） |

共 **13 个**声明了但未导出的函数。这是本子系统「声明了不等于能用」的典型。

## §9 导出符号完整清单

### §9.1 核心文件导出（24 个）

| 文件 | 符号 | 宏 |
|---|---|---|
| `class.c:387` | `devm_rtc_allocate_device` | `EXPORT_SYMBOL_GPL` |
| `class.c:438` | `__devm_rtc_register_device` | `EXPORT_SYMBOL_GPL` |
| `class.c:474` | `devm_rtc_device_register` | `EXPORT_SYMBOL_GPL` |
| `interface.c:124` | `rtc_read_time` | `EXPORT_SYMBOL_GPL` |
| `interface.c:176` | `rtc_set_time` | `EXPORT_SYMBOL_GPL` |
| `interface.c:408` | `rtc_read_alarm` | `EXPORT_SYMBOL_GPL` |
| `interface.c:514` | `rtc_set_alarm` | `EXPORT_SYMBOL_GPL` |
| `interface.c:547` | `rtc_initialize_alarm` | `EXPORT_SYMBOL_GPL` |
| `interface.c:578` | `rtc_alarm_irq_enable` | `EXPORT_SYMBOL_GPL` |
| `interface.c:633` | `rtc_update_irq_enable` | `EXPORT_SYMBOL_GPL` |
| `interface.c:720` | `rtc_update_irq` | `EXPORT_SYMBOL_GPL` |
| `interface.c:740` | `rtc_class_open` | `EXPORT_SYMBOL_GPL` |
| `interface.c:747` | `rtc_class_close` | `EXPORT_SYMBOL_GPL` |
| `dev.c:137` | `rtc_dev_update_irq_enable_emul` | `EXPORT_SYMBOL`（裸） |
| `sysfs.c:346` | `rtc_add_groups` | `EXPORT_SYMBOL`（裸） |
| `sysfs.c:354` | `rtc_add_group` | `EXPORT_SYMBOL`（裸） |
| `nvmem.c:30` | `devm_rtc_nvmem_register` | `EXPORT_SYMBOL_GPL` |
| `lib.c:34` | `rtc_month_days` | `EXPORT_SYMBOL`（裸） |
| `lib.c:43` | `rtc_year_days` | `EXPORT_SYMBOL`（裸） |
| `lib.c:156` | `rtc_time64_to_tm` | `EXPORT_SYMBOL`（裸） |
| `lib.c:176` | `rtc_valid_tm` | `EXPORT_SYMBOL`（裸） |
| `lib.c:187` | `rtc_tm_to_time64` | `EXPORT_SYMBOL`（裸） |
| `lib.c:196` | `rtc_tm_to_ktime` | `EXPORT_SYMBOL_GPL` |
| `lib.c:213` | `rtc_ktime_to_tm` | `EXPORT_SYMBOL_GPL` |

合计 **24 个**：16 个 `EXPORT_SYMBOL_GPL`，8 个裸 `EXPORT_SYMBOL`。

### §9.2 驱动文件额外导出（7 个）

| 文件 | 符号 | 宏 | 说明 |
|---|---|---|---|
| `rtc-mc146818-lib.c:88` | `mc146818_avoid_UIP` | `EXPORT_SYMBOL_GPL` | MC146818 库函数 |
| `rtc-mc146818-lib.c:98` | `mc146818_does_rtc_work` | `EXPORT_SYMBOL_GPL` | 同上 |
| `rtc-mc146818-lib.c:198` | `mc146818_get_time` | `EXPORT_SYMBOL_GPL` | 同上 |
| `rtc-mc146818-lib.c:311` | `mc146818_set_time` | `EXPORT_SYMBOL_GPL` | 同上 |
| `rtc-sa1100.c:240` | `sa1100_rtc_init` | `EXPORT_SYMBOL_GPL` | SA1100 平台初始化 |
| `rtc-ds1685.c:1437` | `ds1685_rtc_poweroff` | `EXPORT_SYMBOL_GPL` | DS1685 关机控制 |
| `rtc-omap.c:466` | `omap_rtc_power_off_program` | `EXPORT_SYMBOL`（裸） | OMAP RTC 关机编程 |

全目录合计 **31 个**导出符号。

## §10 用户态 ioctl 速查

字符设备 `/dev/rtcN`（class = "rtc"）。完整定义在 `include/uapi/linux/rtc.h:86-120`。

| ioctl | 方向 | 参数 | 权限 | 说明 |
|-------|------|------|------|------|
| `RTC_RD_TIME` | 读 | `rtc_time` | — | 读当前时间 |
| `RTC_SET_TIME` | 写 | `rtc_time` | `CAP_SYS_TIME` | 设置当前时间 |
| `RTC_ALM_READ` | 读 | `rtc_time` | — | 读闹钟（旧） |
| `RTC_ALM_SET` | 写 | `rtc_time` | — | 设闹钟（旧，24h 内） |
| `RTC_WKALM_RD` | 读 | `rtc_wkalrm` | — | 读唤醒闹钟（新） |
| `RTC_WKALM_SET` | 写 | `rtc_wkalrm` | — | 设唤醒闹钟 |
| `RTC_AIE_ON/OFF` | — | 无 | — | 闹钟中断开/关 |
| `RTC_UIE_ON/OFF` | — | 无 | — | 更新中断开/关 |
| `RTC_PIE_ON/OFF` | — | 无 | — | 周期中断开/关 |
| `RTC_IRQP_READ` | 读 | `unsigned long` | — | 读 PIE 频率 |
| `RTC_IRQP_SET` | 写 | `unsigned long` | `CAP_SYS_RESOURCE` | 设 PIE 频率 |
| `RTC_PARAM_GET` | 读写 | `rtc_param` | — | 扩展参数 |
| `RTC_PARAM_SET` | 读写 | `rtc_param` | — | 扩展参数 |
| `RTC_VL_READ` | 读 | `unsigned int` | — | 电压低状态 |
| `RTC_VL_CLR` | — | 无 | — | 清除电压低标记 |

`RTC_PARAM_*` 子命令（`uapi/linux/rtc.h:139-142`）：`RTC_PARAM_FEATURES`（读 features 位图）、`RTC_PARAM_CORRECTION`（读/写校准值）、`RTC_PARAM_BACKUP_SWITCH_MODE`。

## §11 近邻符号区分

| 容易混淆的符号对 | 区别 |
|-----------------|------|
| `devm_rtc_allocate_device` vs `devm_rtc_device_register` | 前者只分配（还需 `devm_rtc_register_device`）；后者一步完成（旧 API，47 个文件仍在用） |
| `devm_rtc_register_device(dev)` vs `__devm_rtc_register_device(owner, rtc)` | 前者是宏（自动传 `THIS_MODULE`）；后者需要显式传 owner |
| `RTC_ALM_SET` vs `RTC_WKALM_SET` | 旧接口只设时间（24h 内）；新接口用 `rtc_wkalrm` 支持任意未来时间 |
| `rtc_update_irq` vs `rtc_update_irq_enable` | 前者是**驱动调的**（通知框架发生了 IRQ）；后者是**消费者调的**（开关 UIE） |
| `rtc_read_time` vs `__rtc_read_time` | 前者是公开 API（带锁+校验+offset）；后者是 static（直接调 ops） |
| `rtc_read_alarm` vs `__rtc_read_alarm` | 前者返回软件视角的闹钟（可能和硬件不同）；后者从硬件"半个闹钟"反推完整时间 |
| `rtc_class_open` vs `class_interface` | 前者是消费者主动获取 RTC 设备；后者是框架自动通知新设备注册（alarmtimer 用这个） |
