---
title: 数据结构
---

# RTC 子系统 · 数据结构

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇逐个拆解 RTC 框架涉及的结构体与常量：不只是列字段，而是讲清楚**每个字段的生命周期、谁在什么时候写它、谁在什么时候读它、写错了会怎样**。

---

## §1 struct rtc_class_ops —— 驱动回调表

定义：`include/linux/rtc.h:59-71`。驱动填充这张表，框架在 `interface.c` / `dev.c` 里逐字段检查是否为 `NULL` 来决定能力。

### §1.1 回调全表

| # | 回调 | 签名 | 调用者 | 持锁 | 可睡眠 |
|---|------|------|--------|------|--------|
| 1 | `read_time` | `(dev, struct rtc_time *)` | `__rtc_read_time()` | `ops_lock` | **是**（I2C/SPI） |
| 2 | `set_time` | `(dev, struct rtc_time *)` | `rtc_set_time()` | `ops_lock` | **是** |
| 3 | `read_alarm` | `(dev, struct rtc_wkalrm *)` | `rtc_read_alarm_internal()` | `ops_lock` | **是** |
| 4 | `set_alarm` | `(dev, struct rtc_wkalrm *)` | `__rtc_set_alarm()` | `ops_lock` | **是** |
| 5 | `alarm_irq_enable` | `(dev, unsigned int)` | `rtc_alarm_irq_enable()` | `ops_lock` | **是** |
| 6 | `read_offset` | `(dev, long *)` | `rtc_read_offset()` | `ops_lock` | **是** |
| 7 | `set_offset` | `(dev, long)` | `rtc_set_offset()` | `ops_lock` | **是** |
| 8 | `param_get` | `(dev, struct rtc_param *)` | `rtc_dev_ioctl()` | `ops_lock` | **是** |
| 9 | `param_set` | `(dev, struct rtc_param *)` | `rtc_dev_ioctl()` | `ops_lock` | **是** |
| 10 | `ioctl` | `(dev, cmd, arg)` | `rtc_dev_ioctl()` 兜底 | `ops_lock` | **是** |
| 11 | `proc` | `(dev, struct seq_file *)` | `rtc_proc_show()` | **无锁** | **是** |

### §1.2 回调的调用契约

**框架在调用前做了什么：**

| 回调 | 框架预填充 | 驱动必须做什么 |
|------|-----------|---------------|
| `read_time` | `memset(tm, 0, sizeof(*tm))` 在 `__rtc_read_time():93` | 填全部 9 个字段；不需要校验（框架随后调 `rtc_valid_tm()`） |
| `set_time` | 无——调用者传入的 `tm` 已经过 `rtc_valid_tm()` + `rtc_valid_range()` + `rtc_subtract_offset()` | 把值写进硬件；**注意 `tm` 可能已被 `rtc_subtract_offset()` 就地修改** |
| `read_alarm` | `alarm->enabled = 0; alarm->pending = 0;` 所有 `time` 字段设 `-1`（`interface.c:192-202`） | 读硬件闹钟寄存器；缺失字段保留 `-1`（框架的 `__rtc_read_alarm()` 会补全） |
| `set_alarm` | 无——`alarm->time` 已经过 `rtc_valid_tm()` 校验 | 把值写进硬件闹钟寄存器；根据 `alarm->enabled` 决定是否开启中断 |
| `alarm_irq_enable` | 无 | 仅开/关硬件闹钟中断使能位，不改时间 |
| `ioctl` | 无 | 处理框架不认识的 ioctl；不认识返回 `-ENOIOCTLCMD`（框架会转成 `-ENOTTY`） |
| `proc` | 无 | 往 `seq_file` 追加驱动特有信息（如校准寄存器原始值） |

**错误返回约定：**

- 所有回调返回 `int`，0 = 成功，负 errno = 失败
- `read_time` / `read_alarm` 失败时框架**不会**使用输出参数的内容
- `ioctl` 返回 `-ENOIOCTLCMD` 表示"我不认识这个命令"（框架转 `-ENOTTY`）；返回其他负值表示"我认识但执行失败"

### §1.3 回调与 feature 标志的自动联动

`__devm_rtc_register_device()`（`drivers/rtc/class.c:389`）在注册时自动检查：

```
if (!rtc->ops->set_alarm)
    clear_bit(RTC_FEATURE_ALARM, rtc->features);     // class.c:400

if (rtc->ops->set_offset)
    set_bit(RTC_FEATURE_CORRECTION, rtc->features);  // class.c:403
```

这意味着：
- **不实现 `set_alarm`** → 闹钟功能整体关闭（ioctl 返回 `-EINVAL`，alarmtimer 不会选这个 RTC）
- **实现了 `set_offset`** → 自动开启校准能力（sysfs `offset` 属性出现，`RTC_PARAM_CORRECTION` 可用）
- **实现了 `set_alarm` 但硬件没有中断线** → 驱动必须自己 `clear_bit(RTC_FEATURE_ALARM)`，否则上层会尝试用不存在的中断

### §1.4 最少实现

最小可用驱动只需 `read_time` + `set_time`。没有 `set_alarm` 不报错——只是闹钟功能被静默关闭。

实际驱动通常实现 5-7 个回调。以两个代表性驱动为例：

| 回调 | pcf8563（I2C） | armada38x（platform） |
|------|:-:|:-:|
| `read_time` | ✓ | ✓ |
| `set_time` | ✓ | ✓ |
| `read_alarm` | ✓ | ✓ |
| `set_alarm` | ✓ | ✓ |
| `alarm_irq_enable` | ✓ | ✓ |
| `read_offset` | — | ✓ |
| `set_offset` | — | ✓ |
| `ioctl` | ✓（VL_READ） | — |
| `proc` | — | — |
| `param_get/set` | — | — |

---

## §2 struct rtc_device —— 框架核心对象

定义：`include/linux/rtc.h:87-163`。每个 RTC 硬件对应一个实例。

### §2.1 生命周期

```
devm_rtc_allocate_device(parent)
  │
  ├─ kzalloc(sizeof(*rtc), GFP_KERNEL)          // 全部字段零初始化
  ├─ device_initialize(&rtc->dev)               // 引用计数 = 1
  ├─ rtc->set_offset_nsec = NSEC_PER_SEC + 5ms  // 默认传输延迟
  ├─ rtc->irq_freq = 1
  ├─ rtc->max_user_freq = 64
  ├─ rtc->dev.class = rtc_class
  ├─ rtc->dev.groups = rtc_attr_groups
  ├─ rtc->dev.release = rtc_device_release
  ├─ mutex_init(&rtc->ops_lock)
  ├─ spin_lock_init(&rtc->irq_lock)
  ├─ init_waitqueue_head(&rtc->irq_queue)
  ├─ timerqueue_init_head(&rtc->timerqueue)
  ├─ INIT_WORK(&rtc->irqwork, rtc_timer_do_work)
  ├─ rtc_timer_init(&rtc->aie_timer, rtc_aie_update_irq, rtc)
  ├─ rtc_timer_init(&rtc->uie_rtctimer, rtc_uie_update_irq, rtc)
  ├─ hrtimer_init(&rtc->pie_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL)
  ├─ rtc->pie_timer.function = rtc_pie_update_irq
  ├─ set_bit(RTC_FEATURE_ALARM, rtc->features)
  └─ set_bit(RTC_FEATURE_UPDATE_INTERRUPT, rtc->features)
  
  ↓ 驱动填 ops / range / features
  
__devm_rtc_register_device(owner, rtc)
  │
  ├─ rtc->owner = THIS_MODULE
  ├─ rtc_device_get_offset(rtc)               // 计算 offset_secs
  ├─ __rtc_read_alarm(rtc, &alrm)             // 读硬件已有闹钟
  ├─ rtc_initialize_alarm(rtc, &alrm)         // 接管进 aie_timer
  ├─ rtc_dev_prepare(rtc)                     // 初始化 cdev
  ├─ cdev_device_add(&rtc->char_dev, &rtc->dev)  // 同时注册 cdev + device
  ├─ rtc_proc_add_device(rtc)                 // /proc/driver/rtc
  └─ rtc_hctosys(rtc)                         // 如果是 hctosys 设备

  ↓ 设备运行中...
  
devm_rtc_unregister_device(data)              // devm 回调
  │
  ├─ mutex_lock(&rtc->ops_lock)
  ├─ rtc_proc_del_device(rtc)
  ├─ cdev_device_del(...)                     // 如果 !RTC_NO_CDEV
  ├─ rtc->ops = NULL                          // ★ 后续 API 调用返回 -ENODEV
  ├─ mutex_unlock(&rtc->ops_lock)
  
  ↓ 引用计数归零
  
rtc_device_release(dev)
  │
  ├─ mutex_lock(&rtc->ops_lock)
  ├─ 清空 timerqueue（逐个 timerqueue_del）
  ├─ mutex_unlock(&rtc->ops_lock)
  ├─ cancel_work_sync(&rtc->irqwork)
  ├─ ida_free(&rtc_ida, rtc->id)
  ├─ mutex_destroy(&rtc->ops_lock)
  └─ kfree(rtc)
```

★★ **`rtc->ops = NULL` 的时机**：注销路径先 `cdev_device_del()`（阻止新 open），再把 `ops` 置 NULL。但已经 open 了 `/dev/rtcN` 的进程仍然可以调 ioctl——此时 `rtc_dev_ioctl()` 里的 `ops->ioctl` 会触发 NULL 指针解引用。实际上不会发生，因为 `rtc_dev_ioctl()` 在调用具体 API 前，API 层会检查 `!rtc->ops` 返回 `-ENODEV`。唯一的风险是 `ops->ioctl` 兜底路径——它直接解引用 `ops`，但此时 `ops_lock` 保护着，且 `cdev_device_del()` 已经阻止了新的 ioctl 进入。

### §2.2 字段分组详解

#### A. 设备模型与身份

| 字段 | 类型 | 写入时机 | 读取时机 | 说明 |
|------|------|---------|---------|------|
| `dev` | `struct device` | `rtc_allocate_device()` | 无处不在 | `dev.class = rtc_class`，`dev.parent = 父设备`（I2C client / platform_device） |
| `owner` | `struct module *` | `__devm_rtc_register_device():405` | `alarmtimer_rtc_add_device()` 的 `try_module_get()` | 防止驱动模块在有消费者时被卸载 |
| `id` | `int` | `devm_rtc_allocate_device():375` | sysfs 取名 `rtc%d`、procfs 判断 hctosys | 来自 DT alias 或 `rtc_ida` 自增 |
| `char_dev` | `struct cdev` | `rtc_dev_prepare()` | VFS 查找 `/dev/rtcN` | 与 `dev` 通过 `cdev_device_add()` 原子注册 |
| `flags` | `unsigned long` | 见 §2.4 | `dev.c` open/release、`class.c` 注销 | 只有 2 位 |

#### B. 操作与锁

| 字段 | 类型 | 写入时机 | 保护 | 说明 |
|------|------|---------|------|------|
| `ops` | `const struct rtc_class_ops *` | 驱动 probe 里赋值；注销时置 NULL | `ops_lock` | ★ 注销后变 NULL 是所有 API 返回 `-ENODEV` 的根因 |
| `ops_lock` | `struct mutex` | `rtc_allocate_device()` init | — | 保护 `ops` 指针、`timerqueue` 增删、所有 `ops` 回调调用 |

#### C. 中断与等待

| 字段 | 类型 | 写入时机 | 保护 | 说明 |
|------|------|---------|------|------|
| `irq_data` | `unsigned long` | `rtc_handle_legacy_irq():651` | `irq_lock` | 低 8 位 = 事件类型（`RTC_IRQF \| RTC_AF` 等），高 24 位 = 中断计数（`num << 8`） |
| `irq_lock` | `spinlock_t` | `rtc_allocate_device()` init | — | 保护 `irq_data`、`async_queue`、UIE 模拟状态 |
| `irq_queue` | `wait_queue_head_t` | `rtc_allocate_device()` init | `irq_lock`（间接） | `rtc_dev_read()` 在此阻塞等待中断 |
| `async_queue` | `struct fasync_struct *` | `rtc_dev_fasync()` | `irq_lock` | SIGIO 异步通知链表，`kill_fasync()` 使用 |

★★ **`irq_data` 的编码**：`(rtc->irq_data + (num << 8)) | (RTC_IRQF | mode)`。这意味着多次中断会**累积计数**——如果用户态 `read()` 间隔内发生了 3 次闹钟中断，`irq_data` 的高字节是 `3 << 8 = 0x300`，低字节是 `RTC_IRQF | RTC_AF = 0xA0`。用户态读到的 `unsigned long` 值是 `0x000003A0`。

#### D. 频率控制

| 字段 | 类型 | 默认值 | 写入时机 | 说明 |
|------|------|--------|---------|------|
| `irq_freq` | `int` | **1** | `rtc_irq_set_freq()`、`rtc_allocate_device()` | 当前 PIE 频率（Hz） |
| `max_user_freq` | `int` | **64** | `rtc_allocate_device()`、sysfs `max_user_freq_store()` | 非特权用户的频率上限；sysfs 可改（范围 1-4095） |

#### E. 定时器队列

| 字段 | 类型 | 写入时机 | 保护 | 说明 |
|------|------|---------|------|------|
| `timerqueue` | `struct timerqueue_head` | `rtc_timer_enqueue()`/`rtc_timer_remove()` | `ops_lock` | 红黑树，按 `node.expires` 排序 |
| `aie_timer` | `struct rtc_timer` | `rtc_set_alarm()`、`rtc_alarm_irq_enable()` | `ops_lock` | 闹钟中断的软件侧——用户设的闹钟最终装进这里 |
| `uie_rtctimer` | `struct rtc_timer` | `rtc_update_irq_enable()` | `ops_lock` | 1Hz 更新中断的软件侧——每秒重排一次 |
| `pie_timer` | `struct hrtimer` | `rtc_irq_set_state()` | 独立（hrtimer 自保护） | 亚秒级周期中断，用 `CLOCK_MONOTONIC` |
| `pie_enabled` | `int` | **0** | `rtc_irq_set_state()` | `pie_timer` 是否激活 |
| `irqwork` | `struct work_struct` | `rtc_update_irq()` schedule | workqueue 序列化 | 回调 `rtc_timer_do_work()`，在 workqueue 里处理到期 timer |

★★ **`aie_timer` vs 硬件闹钟的关系**：框架把所有闹钟（用户设的、alarmtimer 设的）排进 `timerqueue`，然后只把**队列头部**的到期时间写进硬件寄存器。硬件闹钟永远只装最近的一个。当它触发时，`rtc_timer_do_work()` 处理到期的 timer，然后重编程硬件为下一个。这意味着：
- 多个闹钟不会互相覆盖
- 硬件寄存器里的值可能和任何一个用户设的值不同（如果队列里有更早的 alarmtimer）
- `rtc_read_alarm()` 返回的是 `aie_timer` 的值（软件视角），不是硬件寄存器的值

#### F. 时间校准

| 字段 | 类型 | 默认值 | 写入时机 | 说明 |
|------|------|--------|---------|------|
| `set_offset_nsec` | `unsigned long` | **NSEC_PER_SEC + 5ms** | 驱动可在 probe 里覆盖 | NTP 写回 RTC 时的传输延迟补偿。含义：「写完时间后，硬件在多少 ns 后秒进位」 |

`set_offset_nsec` 的默认值 `1005000000`（1秒+5ms）意味着：框架假设硬件在写入操作后恰好 1 秒的整数倍再加 5ms 传输延迟时秒进位。对于 I2C RTC 这基本正确（I2C 传输 < 5ms）。对于直接映射的 SoC RTC（如 armada38x），实际延迟是纳秒级，但驱动没有覆盖这个值——影响可忽略。

#### G. 范围映射

| 字段 | 类型 | 写入时机 | 说明 |
|------|------|---------|------|
| `range_min` | `time64_t` | 驱动 probe | 硬件能表示的最早时间（Unix 秒） |
| `range_max` | `timeu64_t` | 驱动 probe | 硬件能表示的最晚时间 |
| `start_secs` | `time64_t` | `rtc_device_get_offset():287` | DT `start-year` 映射到的秒数 |
| `offset_secs` | `time64_t` | `rtc_device_get_offset():270-335` | 框架计算的偏移量 |
| `set_start_time` | `bool` | `rtc_device_get_offset():289` | 是否从 DT 读到了 `start-year` |

范围映射的完整逻辑见 §2.3。

#### H. Feature 位图

| 字段 | 类型 | 说明 |
|------|------|------|
| `features` | `unsigned long[BITS_TO_LONGS(8)]` | 8 个 feature 标志，见 §5 |

#### I. 条件编译字段（`CONFIG_RTC_INTF_DEV_UIE_EMUL`）

以下 7 个字段**全部由 `irq_lock` 保护**，只在 `CONFIG_RTC_INTF_DEV_UIE_EMUL` 开启时存在：

| 字段 | 类型 | 说明 |
|------|------|------|
| `uie_task` | `struct work_struct` | UIE 模拟的 workqueue 路径 |
| `uie_timer` | `struct timer_list` | UIE 模拟的 timer 路径（减少轮询） |
| `oldsecs` | `unsigned int` | 上次读到的秒数，用于检测变化 |
| `uie_irq_active` | `:1` | UIE 模拟是否启用 |
| `stop_uie_polling` | `:1` | 通知轮询循环停止 |
| `uie_task_active` | `:1` | work 是否正在执行 |
| `uie_timer_active` | `:1` | timer 是否正在执行 |

UIE 模拟的工作方式：每秒轮询一次 `rtc_read_time()`，比较 `tm_sec` 是否变化。变化时通过 `rtc_handle_legacy_irq()` 发出 `RTC_UF` 事件。一旦检测到秒变化，切换到 `uie_timer` 路径（用 `timer_list` 在 `jiffies + HZ - HZ/10` 后唤醒），减少轮询频率。

### §2.3 范围映射详解

`rtc_device_get_offset()`（`drivers/rtc/class.c:270-335`）计算 `offset_secs`，用于把硬件的有限年份范围平移到系统期望的区间。

**前提条件**：`range_min != range_max`（驱动设了范围）且 `set_start_time == true`（DT 有 `start-year`）。两个条件缺一则 `offset_secs = 0`，映射不生效。

**四种情况**：

| 条件 | `offset_secs` | 效果 |
|------|--------------|------|
| `start_secs > range_max` 或 `start_secs + range_secs - 1 < range_min` | `start_secs - range_min` | 硬件范围整体平移到 `[start_secs, start_secs + range_secs - 1]` |
| `start_secs > range_min`（在硬件范围内但偏大） | `range_max - range_min + 1` | 正向平移一个完整周期 |
| `start_secs < range_min`（在硬件范围内但偏小） | `-(range_max - range_min + 1)` | 反向平移一个完整周期 |
| `start_secs == range_min` | `0` | 无需偏移 |

**读写时的偏移处理**：

- **读**（`rtc_add_offset()`，`interface.c:23`）：`time64_t secs = rtc_tm_to_time64(tm)`，如果 `secs` 不在"重叠区"，则 `rtc_time64_to_tm(secs + offset_secs, tm)`。重叠区指扩展范围与原始范围的交集——这段值本身已经合法，不需要偏移。
- **写**（`rtc_subtract_offset()`，`interface.c:46`）：如果 `secs` 在 `[range_min, range_max]` 内，不减偏移（值已经合法）；否则 `rtc_time64_to_tm(secs - offset_secs, tm)`。

★★ **就地修改副作用**：`rtc_subtract_offset()` 会直接改写调用者传入的 `struct rtc_time *tm`。触发条件：`offset_secs != 0` 且待写时间落在硬件原生范围之外。在没有 `start-year` 的设备上永远看不到这个副作用。

### §2.4 flags 位图

定义：`include/linux/rtc.h:84-85`。只有两位：

| 位 | 名字 | 设置时机 | 清除时机 | 效果 |
|---|------|---------|---------|------|
| 0 | `RTC_DEV_BUSY` | `dev.c:28`，`open()` 时 `test_and_set_bit_lock` | `dev.c:520`，`release()` 时 `clear_bit_unlock` | 保证同一时刻只有一个进程能 open 一个 RTC 字符设备 |
| 1 | `RTC_NO_CDEV` | `class.c:417`，`cdev_device_add()` 失败时 | 不主动清除 | 注销路径跳过 `cdev_device_del()` |

`RTC_DEV_BUSY` 使用了 `test_and_set_bit_lock` / `clear_bit_unlock`（带内存屏障），而不是普通的 `set_bit` / `clear_bit`。这保证了 open 的互斥语义在多核上的正确性。

---

## §3 struct rtc_timer —— 定时器队列元素

定义：`include/linux/rtc.h:75-81`。

```c
struct rtc_timer {
    struct timerqueue_node node;       // 红黑树节点，按 expires 排序
    ktime_t period;                    // 0 = 一次性，非 0 = 周期性
    void (*func)(struct rtc_device *rtc);  // 到期回调
    struct rtc_device *rtc;            // 所属设备（传给回调）
    int enabled;                       // 1 = 在队列中，0 = 不在
};
```

### §3.1 与 hrtimer 的区别

`rtc_timer` 不是 `hrtimer`。它没有自己的定时机制——完全依赖硬件闹钟。框架把 `timerqueue` 里最早到期的 `rtc_timer` 的 `expires` 装进硬件闹钟寄存器，硬件触发中断后由 `rtc_timer_do_work()` 在 workqueue 里处理。

`pie_timer` 是真正的 `hrtimer`（`CLOCK_MONOTONIC`），因为它需要亚秒级精度——硬件闹钟做不到。

### §3.2 两个内嵌实例

| 实例 | 回调 | 触发源 | 典型 period |
|------|------|--------|------------|
| `aie_timer` | `rtc_aie_update_irq()` → `rtc_handle_legacy_irq(1, RTC_AF)` | 用户设闹钟 / alarmtimer 设唤醒 | 0（一次性） |
| `uie_rtctimer` | `rtc_uie_update_irq()` → `rtc_handle_legacy_irq(1, RTC_UF)` | 用户开 UIE | `ktime_set(1, 0)`（每秒） |

### §3.3 `enabled` 字段的含义

`enabled` 不是"闹钟是否开启"——它是"这个 timer 是否在 timerqueue 里"。`rtc_timer_enqueue()` 设 1，`rtc_timer_remove()` 设 0。`rtc_read_alarm()` 用 `rtc->aie_timer.enabled` 来报告闹钟是否激活。

---

## §4 两把锁的深入分析

### §4.1 为什么需要两把锁

| 锁 | 类型 | 保护的数据 | 为什么选这个类型 |
|---|------|-----------|----------------|
| `ops_lock` | `struct mutex` | `ops` 指针、`timerqueue`、所有 `ops` 回调调用 | 回调涉及 I2C/SPI 传输（可睡眠），必须用 mutex |
| `irq_lock` | `spinlock_t` | `irq_data`、`async_queue`、UIE 模拟的 7 个位域 | `rtc_update_irq()` 在硬中断上下文被调用，不能睡眠 |

两把锁保护的数据**完全不重叠**——`ops_lock` 管慢路径（设备操作），`irq_lock` 管快路径（中断上报）。

### §4.2 锁的获取顺序

如果同时需要两把锁：**先 `ops_lock` 后 `irq_lock`**。

实际代码中几乎不出现同时持两把锁的情况：
- `rtc_handle_legacy_irq()` 只取 `irq_lock`
- `rtc_timer_do_work()` 只取 `ops_lock`
- `rtc_dev_ioctl()` 取 `ops_lock`，但在调 `rtc_read_alarm()` 等已带锁的 API 前会先释放

### §4.3 ioctl 路径的锁窗口

`rtc_dev_ioctl()`（`dev.c:203`）用 `mutex_lock_interruptible(&rtc->ops_lock)` 进入，但在调用 `rtc_read_time()` / `rtc_set_alarm()` 等**自己加锁的 API** 前会先 `mutex_unlock(&rtc->ops_lock)`。

这意味着：
1. 权限检查（`CAP_SYS_TIME` 等）和实际操作之间存在一个窗口
2. 在这个窗口里，另一个线程可以改变设备状态（比如注销 → `ops = NULL`）
3. 但 API 层会检查 `!rtc->ops` 并返回 `-ENODEV`，所以不会崩溃

### §4.4 竞态场景分析

**场景 1：两个线程同时 `rtc_set_time()`**

```
线程 A: rtc_set_time() → mutex_lock(&rtc->ops_lock) → ops->set_time()
线程 B: rtc_set_time() → mutex_lock(&rtc->ops_lock) → 阻塞
线程 A: mutex_unlock → 完成
线程 B: 获得锁 → ops->set_time() → 覆盖 A 的值
```

结论：`ops_lock` 保证串行化，不会同时写硬件。最终值是最后写入的那个。

**场景 2：`rtc_set_time()` 和 `rtc_update_irq()` 并发**

```
线程 A: rtc_set_time() → mutex_lock(&rtc->ops_lock) → ops->set_time() (I2C 传输中...)
中断:   硬件闹钟触发 → rtc_update_irq() → schedule_work(&rtc->irqwork)
```

结论：不冲突。`rtc_update_irq()` 只取 `irq_lock`（或直接 schedule_work），不需要 `ops_lock`。

**场景 3：注销与 ioctl 并发**

```
线程 A: rtc_dev_ioctl() → mutex_lock(&rtc->ops_lock) → 准备调用 ops->read_time
线程 B: devm_rtc_unregister_device() → mutex_lock(&rtc->ops_lock) → 阻塞
线程 A: ops->read_time() → 完成 → mutex_unlock
线程 B: 获得锁 → ops = NULL → mutex_unlock
线程 A: 下一次 ioctl → ops == NULL → -ENODEV
```

结论：`ops_lock` 保证注销不会在 ioctl 使用 `ops` 的过程中发生。

---

## §5 Feature 标志位

定义：`include/uapi/linux/rtc.h:129-137`。存在 `rtc_device.features` 位图里。

| 位 | 名字 | 含义 | 默认 | 谁设/清 |
|---|------|------|------|---------|
| 0 | `RTC_FEATURE_ALARM` | 硬件支持闹钟 | **置位** | 分配时默认置位（`class.c:243`）；`!ops->set_alarm` 时自动清除（`class.c:400`）；驱动可在 probe 里手动清除（如硬件无中断线） |
| 1 | `RTC_FEATURE_ALARM_RES_MINUTE` | 闹钟精度只到分钟 | 0 | 驱动手动设置（如 pcf8563） |
| 2 | `RTC_FEATURE_NEED_WEEK_DAY` | 硬件需要 `tm_wday` | 0 | 驱动手动设置 |
| 3 | `RTC_FEATURE_ALARM_RES_2S` | 闹钟精度为 2 秒 | 0 | 驱动手动设置 |
| 4 | `RTC_FEATURE_UPDATE_INTERRUPT` | 硬件支持秒更新中断 | **置位** | 分配时默认置位（`class.c:244`）；**无自动纠正**——驱动必须手动清除 |
| 5 | `RTC_FEATURE_CORRECTION` | 硬件支持走时校准 | 0 | `ops->set_offset != NULL` 时自动置位（`class.c:403`） |
| 6 | `RTC_FEATURE_BACKUP_SWITCH_MODE` | 支持备份电源切换模式 | 0 | 驱动手动设置 |
| 7 | `RTC_FEATURE_ALARM_WAKEUP_ONLY` | 闹钟仅用于唤醒，不产生用户态中断 | 0 | 驱动手动设置 |

★★ **位 0 和位 4 的"乐观默认"问题**：

框架默认假设硬件支持闹钟和更新中断。如果驱动不声明支持但硬件实际不支持：
- **位 0**（`RTC_FEATURE_ALARM`）：如果没实现 `set_alarm`，框架会自动清除。但如果实现了 `set_alarm` 而硬件没有中断线，框架不会知道——驱动必须自己 `clear_bit`。
- **位 4**（`RTC_FEATURE_UPDATE_INTERRUPT`）：**完全没有自动纠正**。如果硬件不支持秒更新中断，驱动必须在 probe 里 `clear_bit(RTC_FEATURE_UPDATE_INTERRUPT, rtc->features)`。否则 `rtc_update_irq_enable()` 会尝试用硬件 UIE，失败后 fallback 到软件模拟——多一次无谓的硬件访问。

---

## §6 用户态 ABI 结构

### §6.1 struct rtc_time（`uapi/linux/rtc.h:25-35`）

| 字段 | 类型 | 范围 | 说明 |
|------|------|------|------|
| `tm_sec` | `int` | 0–59 | 秒 |
| `tm_min` | `int` | 0–59 | 分 |
| `tm_hour` | `int` | 0–23 | 时 |
| `tm_mday` | `int` | 1–31 | 日 |
| `tm_mon` | `int` | **0–11** | 月（0 = 一月，与 `struct tm` 一致，**不是** 1–12） |
| `tm_year` | `int` | ≥70 | 年 - 1900（2024 年 = 124） |
| `tm_wday` | `int` | 0–6 | 星期（0 = 周日） |
| `tm_yday` | `int` | 0–365 | 年内第几天 |
| `tm_isdst` | `int` | — | RTC 不使用，框架始终设 0 |

`rtc_valid_tm()` 的校验规则（`lib.c:161`）：
- `tm_year < 70`（1970 之前）→ `-EINVAL`
- `tm_year > INT_MAX - 1900` → `-EINVAL`
- `tm_mon >= 12` → `-EINVAL`
- `tm_mday < 1` 或 `> rtc_month_days(tm_mon, tm_year + 1900)` → `-EINVAL`
- `tm_hour >= 24` / `tm_min >= 60` / `tm_sec >= 60` → `-EINVAL`

### §6.2 struct rtc_wkalrm（`uapi/linux/rtc.h:41-45`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | `unsigned char` | 0 = 关闭，1 = 开启 |
| `pending` | `unsigned char` | 0 = 未触发，1 = 已触发待处理 |
| `time` | `struct rtc_time` | 闹钟目标时间 |

### §6.3 struct rtc_param（`uapi/linux/rtc.h:70-79`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `param` | `__u64` | 参数编号 |
| `uvalue` / `svalue` / `ptr` | 联合体 | 参数值 |
| `index` | `__u32` | 参数数组下标 |
| `__pad` | `__u32` | 对齐填充 |

`RTC_PARAM_FEATURES`（index 必须为 0）返回 `features[0]` 位图。`RTC_PARAM_CORRECTION`（index 必须为 0）读/写校准值（ppb）。

---

## §7 时间戳常量

定义：`include/linux/rtc.h:170-177`。

| 常量 | 秒数 | 日期 | 对应硬件 |
|------|------|------|---------|
| `RTC_TIMESTAMP_BEGIN_0000` | -62167219200 | 0000-01-01 | — |
| `RTC_TIMESTAMP_BEGIN_1900` | -2208988800 | 1900-01-01 | — |
| `RTC_TIMESTAMP_BEGIN_2000` | 946684800 | 2000-01-01 | 世纪位 = 20xx 的 RTC 的 range_min |
| `RTC_TIMESTAMP_END_2063` | 2966371199 | 2063-12-31 | 6-bit 年份（0-63） |
| `RTC_TIMESTAMP_END_2079` | 3471292799 | 2079-12-31 | 7-bit 年份（0-79） |
| `RTC_TIMESTAMP_END_2099` | 4102444799 | 2099-12-31 | 8-bit 年份（0-99），最常见 |
| `RTC_TIMESTAMP_END_2199` | 7258118399 | 2199-12-31 | — |
| `RTC_TIMESTAMP_END_9999` | 253402300799 | 9999-12-31 | 全精度 RTC（如纯秒计数器） |

驱动典型用法：
```c
rtc->range_min = RTC_TIMESTAMP_BEGIN_2000;   // 946684800
rtc->range_max = RTC_TIMESTAMP_END_2099;     // 4102444799
```

---

## §8 中断标志位

定义：`include/uapi/linux/rtc.h:123-126`。

| 值 | 名字 | 含义 |
|---|------|------|
| `0x80` | `RTC_IRQF` | 中断有效标记（必须与其他位组合） |
| `0x40` | `RTC_PF` | 周期中断（PIE） |
| `0x20` | `RTC_AF` | 闹钟中断（AIE） |
| `0x10` | `RTC_UF` | 更新中断（UIE，1Hz） |

用户态 `read()` 返回的 `unsigned long`：
- 低 8 位：事件类型（`RTC_IRQF | RTC_AF | RTC_PF | RTC_UF` 的组合）
- 高 24 位：自上次 `read()` 以来的中断次数（`num << 8`）

---

## §9 结构关系总图

```
                     ┌──────────────────────────┐
                     │  rtc_class (全局 class)   │
                     │  class.c:478 创建         │
                     └────────────┬─────────────┘
                                  │ dev.class
                                  ▼
                 ┌────────────────────────────────────┐
                 │        struct rtc_device            │
                 │                                     │
  /sys/class/ ──│  dev (struct device)                 │──→ cdev → /dev/rtcN
  rtc/rtcN      │  ops (rtc_class_ops *) ─────────────│──→ 驱动回调表 (§1)
                │  ops_lock (mutex) ──────────────────│──→ 保护 ops/timerqueue
                │  irq_lock (spinlock) ───────────────│──→ 保护 irq_data/UIE
                │  irq_data / irq_queue ──────────────│──→ read()/poll() 阻塞
                │  timerqueue (红黑树)                 │
                │    ├─ aie_timer ────────────────────│──→ 闹钟（软件侧）
                │    └─ uie_rtctimer ─────────────────│──→ UIE（软件侧）
                │  pie_timer (hrtimer) ───────────────│──→ PIE（亚秒级）
                │  irqwork → rtc_timer_do_work() ─────│──→ 到期处理
                │  features[8 bits] ──────────────────│──→ §5
                │  range_min/max/offset_secs ─────────│──→ §2.3
                └────────────────────────────────────┘
                                  │
                                  │ alarmtimer 通过 class_interface 选中
                                  ▼
                 ┌────────────────────────────────────┐
                 │  kernel/time/alarmtimer.c           │
                 │  rtcdev = 第一个匹配的 rtc_device   │
                 │  rtctimer = rtc_timer（休眠唤醒用）  │
                 └────────────────────────────────────┘

    用户态 ioctl 结构（uapi/linux/rtc.h）：
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ struct rtc_time│  │struct rtc_wkalrm│ │struct rtc_param│
    │  9 个 int     │  │ enabled/pending│ │ param/value  │
    │  秒分时日月年 │  │ + rtc_time    │  │ + index      │
    └──────────────┘  └──────────────┘  └──────────────┘
```
