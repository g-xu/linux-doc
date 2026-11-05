---
title: 调试方法
---

# RTC 子系统 · 调试与诊断

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇覆盖 RTC 的调试手段：sysfs 属性、procfs 输出、tracepoint、Kconfig 调试选项、KUnit 测试、常见故障的诊断流程。
> 行号不在本篇维护——需要精确到行的地方去 `03-internals.md`，取证命令去 `99-evidence.md`。

## §1 sysfs 属性

`/sys/class/rtc/rtcN/` 下的属性由 `drivers/rtc/sysfs.c` 定义。

### §1.1 属性列表

| 属性 | 权限 | 内容 | 可见性条件 |
|---|---|---|---|
| `name` | RO | 设备名（如 `rtc0`） | 始终可见 |
| `date` | RO | 当前日期（`YYYY-MM-DD`） | 始终可见 |
| `time` | RO | 当前时间（`HH:MM:SS`） | 始终可见 |
| `since_epoch` | RO | 自 epoch 以来的秒数 | 始终可见 |
| `max_user_freq` | RW | 非特权用户可设的最大中断频率（默认 64） | 始终可见 |
| `hctosys` | RO | 这个 RTC 是否是 `CONFIG_RTC_HCTOSYS_DEVICE`（0/1） | `CONFIG_RTC_HCTOSYS` |
| `wakealarm` | RW | 唤醒闹钟时间（秒数或 `+偏移`） | `device_may_wakeup()` |
| `offset` | RW | 时钟校正值（ppb） | `RTC_FEATURE_CORRECTION` |
| `range` | RO | 硬件范围（`[min, max]`） | `range_min != range_max` |

### §1.2 可见性控制

`rtc_attr_is_visible()`（`drivers/rtc/sysfs.c:279`）根据设备能力动态隐藏属性：

| 属性 | 隐藏条件 | 含义 |
|---|---|---|
| `hctosys` | `!CONFIG_RTC_HCTOSYS` 或不是指定设备 | 只有系统时钟 RTC 才显示 |
| `wakealarm` | `!device_can_wakeup(parent)` | 父设备必须支持唤醒 |
| `offset` | `!RTC_FEATURE_CORRECTION` | 驱动必须实现 `set_offset` 回调 |
| `range` | `range_min == range_max` | 驱动必须声明范围 |

隐藏通过返回 `0`（不是 `-ENOENT`）实现——文件仍然存在但 `ls` 看不到。这意味着如果你 `cat` 一个被隐藏的属性，可能得到空输出或错误。

### §1.3 `wakealarm` 的写入格式

```bash
# 设绝对时间（秒数，从 epoch）
echo 1700000000 > /sys/class/rtc/rtc0/wakealarm

# 设相对时间（从现在起 N 秒后）
echo +60 > /sys/class/rtc/rtc0/wakealarm

# 清除闹钟
echo 0 > /sys/class/rtc/rtc0/wakealarm

# 读当前闹钟（返回 epoch 秒数）
cat /sys/class/rtc/rtc0/wakealarm
```

`wakealarm` 的 store 函数（`drivers/rtc/sysfs.c:174-220`）解析输入后调用 `rtc_set_alarm()`。如果闹钟时间在过去，返回 `-ETIME`。

### §1.4 驱动追加属性

驱动可以通过 `rtc_add_group()` / `rtc_add_groups()`（`drivers/rtc/sysfs.c:346/354`）追加自定义属性组。这两个函数是**裸 `EXPORT_SYMBOL`**（非 GPL），任何模块都能用。

使用模式：

```c
static DEVICE_ATTR(my_custom_attr, 0444, my_show, NULL);

static struct attribute *my_rtc_attrs[] = {
    &dev_attr_my_custom_attr.attr,
    NULL,
};

static const struct attribute_group my_rtc_group = {
    .attrs = my_rtc_attrs,
};

static int my_rtc_probe(...)
{
    ...
    rtc_add_group(rtc, &my_rtc_group);
    ...
}
```

---

## §2 procfs 输出

`/proc/driver/rtc` 只对 `CONFIG_RTC_HCTOSYS_DEVICE` 指定的那个 RTC 显示（默认 `rtc0`）。

输出格式（`drivers/rtc/proc.c:49-73`）：

```
rtc_time        : 14:30:00
rtc_date        : 2024-08-27
alrm_time       : 07:00:00
alrm_date       : 2024-08-28
alarm_IRQ       : yes
alrm_pending    : no
update IRQ enabled    : no
periodic IRQ enabled  : no
periodic IRQ frequency: 1
max user IRQ frequency: 64
24hr            : yes
```

⚠️ 如果系统没有 `CONFIG_RTC_HCTOSYS`，或者指定的 RTC 注册失败，`/proc/driver/rtc` **不存在**。这不是 bug——是设计意图，procfs 只给"系统时钟 RTC"一个特殊入口。

`proc.c` 的实现很直接：`rtc_proc_read()` 调用 `rtc_read_time()` 和 `rtc_read_alarm()` 获取数据，用 `seq_printf()` 格式化输出。没有锁的复杂性——`rtc_read_time()` 和 `rtc_read_alarm()` 自己持锁。

---

## §3 Tracepoint

定义在 `include/trace/events/rtc.h`，共 **3 个 event class / 12 个 event**。

### §3.1 使用方法

```bash
# 启用所有 RTC tracepoint
echo 1 > /sys/kernel/tracing/events/rtc/enable

# 只看时间读写
echo 1 > /sys/kernel/tracing/events/rtc/rtc_read_time/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_set_time/enable

# 看定时器队列操作
echo 1 > /sys/kernel/tracing/events/rtc/rtc_timer_enqueue/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_timer_fired/enable

# 读取 trace
cat /sys/kernel/tracing/trace
```

### §3.2 Event 分类与输出格式

**时间/闹钟操作**（`rtc_time_alarm_class`）——输出格式 `UTC (<secs>) (<err>)`：

| Event | 触发时机 | 典型输出 |
|---|---|---|
| `rtc_read_time` | `rtc_read_time()` 返回时 | `rtc_read_time: UTC (1700000000) (0)` |
| `rtc_set_time` | `rtc_set_time()` 返回时 | `rtc_set_time: UTC (1700000000) (0)` |
| `rtc_read_alarm` | `rtc_read_alarm()` 返回时 | `rtc_read_alarm: UTC (1700003600) (0)` |
| `rtc_set_alarm` | `rtc_set_alarm()` 返回时 | `rtc_set_alarm: UTC (1700003600) (0)` |

⚠️ `err` 字段是返回值——0 表示成功，负数表示失败。如果看到 `(-22)` 就是 `-EINVAL`，`(-19)` 是 `-ENODEV`。

**IRQ 控制**（独立 event）：

| Event | 触发时机 | 输出格式 |
|---|---|---|
| `rtc_irq_set_state` | `rtc_irq_set_state()` 返回时 | `enable/disable RTC 2^N Hz periodic IRQs (<err>)` |
| `rtc_irq_set_freq` | `rtc_irq_set_freq()` 返回时 | `set RTC periodic IRQ frequency:<freq> (<err>)` |
| `rtc_alarm_irq_enable` | `rtc_alarm_irq_enable()` 返回时 | `enable/disable RTC alarm IRQ (<err>)` |

**偏移**（`rtc_offset_class`）——输出格式 `RTC offset: <offset> (<err>)`：

| Event | 触发时机 |
|---|---|
| `rtc_read_offset` | `rtc_read_offset()` 返回时 |
| `rtc_set_offset` | `rtc_set_offset()` 返回时 |

**定时器队列**（`rtc_timer_class`）——输出 timer 指针：

| Event | 触发时机 |
|---|---|
| `rtc_timer_enqueue` | timer 加入 timerqueue |
| `rtc_timer_dequeue` | timer 从 timerqueue 移除 |
| `rtc_timer_fired` | timer 到期触发回调 |

⚠️ `rtc_class_open()` / `rtc_class_close()` **没有 tracepoint**。如果需要跟踪设备打开/关闭，只能用 `ftrace` 的 function tracer 或 kprobe。

### §3.3 实用 tracepoint 组合

**诊断闹钟不触发**：

```bash
# 同时启用闹钟设置、入队、触发
echo 1 > /sys/kernel/tracing/events/rtc/rtc_set_alarm/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_timer_enqueue/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_timer_fired/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_alarm_irq_enable/enable

# 设一个闹钟
echo +60 > /sys/class/rtc/rtc0/wakealarm

# 等 60 秒后查看 trace
cat /sys/kernel/tracing/trace | grep rtc
```

预期看到：`rtc_set_alarm` → `rtc_timer_enqueue` → （60 秒后）`rtc_timer_fired`。如果 `rtc_timer_fired` 没出现，说明硬件闹钟没触发——检查 IRQ 配置。

**诊断时间读写失败**：

```bash
echo 1 > /sys/kernel/tracing/events/rtc/rtc_read_time/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_set_time/enable

# 读时间
cat /sys/class/rtc/rtc0/time

# 查看 trace——err 字段非 0 就是失败
cat /sys/kernel/tracing/trace | grep rtc_read_time
```

---

## §4 Kconfig 调试选项

| 选项 | 类型 | 效果 |
|---|---|---|
| `RTC_DEBUG` | `bool` | 给 `drivers/rtc/` 整个目录加 `-DDEBUG`，让 `dev_dbg()` 无条件输出 |
| `RTC_INTF_DEV_UIE_EMUL` | `bool` | 当硬件不支持更新中断时，用软件模拟 `RTC_UIE` |
| `RTC_LIB_KUNIT_TEST` | `tristate` | 启用 `lib.c` 的 KUnit 测试（16 万年逐日对账） |
| `RTC_DRV_TEST` | `tristate` | 软件 RTC 测试驱动，用于验证框架 API |

### §4.1 `RTC_DEBUG` 的作用

打开 `RTC_DEBUG` 后，框架和驱动里的 `dev_dbg()` / `pr_debug()` 语句生效。这些日志在 `dmesg` 里可以看到，覆盖：

- 设备注册/注销
- 时间读写的成功/失败
- 闹钟设置
- 中断处理

⚠️ `RTC_DEBUG` 是 `ccflags-$(CONFIG_RTC_DEBUG) := -DDEBUG`，**作用范围是 `drivers/rtc/` 整个目录**，不是单个文件——打开它以后 168 个驱动里的 `dev_dbg()` 会一起开口。输出量可能很大。

也可以用 `dynamic_debug` 精确控制：

```bash
# 只开核心框架的调试
echo 'file drivers/rtc/class.c +p' > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/rtc/interface.c +p' > /sys/kernel/debug/dynamic_debug/control

# 只开特定驱动的调试
echo 'file drivers/rtc/rtc-pcf8563.c +p' > /sys/kernel/debug/dynamic_debug/control

# 关掉
echo 'file drivers/rtc/class.c -p' > /sys/kernel/debug/dynamic_debug/control
```

### §4.2 `RTC_INTF_DEV_UIE_EMUL`

某些 RTC 硬件不支持秒更新中断（`RTC_UIE`）。打开这个选项后，`dev.c` 里的 `rtc_uie_task()` / `rtc_uie_timer()` 会用两阶段轮询模拟 1Hz 的更新中断：

1. **第一阶段**（workqueue 轮询）：每 50ms 读一次 RTC 的秒字段，检测变化
2. **第二阶段**（timer_list）：第一次检测到秒变化后，切换到精确的 1 秒定时器

代价：第一阶段每秒 20 次 I2C/SPI 传输。只在确实需要 UIE 但硬件不支持时才开。

### §4.3 `RTC_DRV_TEST`

注册一个纯软件的 RTC 设备（`rtc_test`），可以用来：

- 验证框架 API 的正确性
- 测试用户态程序
- 在没有硬件 RTC 的环境（如 QEMU 早期启动）里提供 RTC 功能

---

## §5 KUnit 测试

`drivers/rtc/lib_test.c` 是 `lib.c` 的 KUnit 测试，由 `RTC_LIB_KUNIT_TEST` 控制。

| 测试 | 内容 | 迭代次数 |
|---|---|---|
| `rtc_time64_to_tm` | 从 -62167219200（0000-01-01）到 +253402300799（9999-12-31）逐日对账 | ~365 万次 |
| `rtc_tm_to_time64` | 反向转换的正确性 | 同上 |
| `rtc_valid_tm` | 各种合法/非法时间的校验 | 几十个 case |

⚠️ 这个测试跑的是**逐日**对账，在 CI 里可能需要几秒钟。如果 CI 超时，可以只跑 `rtc_valid_tm` 的 case。

---

## §6 常见调试场景

### §6.1 RTC 不工作（设备不存在）

```bash
# 1. 检查设备是否存在
ls /sys/class/rtc/
# 如果为空 → 驱动没 probe 成功

# 2. 检查 dmesg 里的错误
dmesg | grep -i rtc
# 常见错误：
#   "probe failed: -19"  → -ENODEV，硬件没响应（I2C 地址错？）
#   "probe failed: -2"   → -ENOENT，DT 节点找不到
#   "probe failed: -16"  → -EBUSY，资源冲突

# 3. 检查 DT 匹配
cat /proc/device-tree/.../compatible  # 确认 compatible 字符串
ls /sys/bus/i2c/devices/               # 确认 I2C 设备枚举

# 4. 如果开了 RTC_DEBUG，看详细日志
echo 'module rtc_core +p' > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/rtc/rtc-pcf8563.c +p' > /sys/kernel/debug/dynamic_debug/control
```

### §6.2 读时间失败

```bash
# 1. 直接读 sysfs
cat /sys/class/rtc/rtc0/time
# 如果报错 → 看 dmesg

# 2. 开 tracepoint
echo 1 > /sys/kernel/tracing/events/rtc/rtc_read_time/enable
cat /sys/class/rtc/rtc0/time
cat /sys/kernel/tracing/trace | grep rtc_read_time
# 看 err 字段：
#   (-5)  → -EIO，I2C 通信失败（检查总线）
#   (-16) → -EBUSY，设备被占用
#   (-19) → -ENODEV，设备已注销

# 3. 检查 I2C 总线（如果是 I2C RTC）
i2cdetect -y 1  # 确认设备在总线上
```

### §6.3 闹钟不触发

```bash
# 1. 检查 wakealarm 属性是否存在
ls /sys/class/rtc/rtc0/wakealarm
# 如果不存在 → device_may_wakeup() 返回 false
#   → 检查 DT 里有没有 wakeup-source
#   → 检查驱动有没有 device_set_wakeup_capable()

# 2. 检查 feature 是否支持闹钟
cat /proc/driver/rtc | grep alarm
# alarm_IRQ 应该是 "yes"

# 3. 设一个 1 分钟后的闹钟
echo +60 > /sys/class/rtc/rtc0/wakealarm

# 4. 看 tracepoint
echo 1 > /sys/kernel/tracing/events/rtc/rtc_set_alarm/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_timer_enqueue/enable
echo 1 > /sys/kernel/tracing/events/rtc/rtc_timer_fired/enable

# 5. 等 60 秒，看 rtc_timer_fired 是否出现
cat /sys/kernel/tracing/trace | grep rtc_timer

# 6. 如果 fired 没出现 → 硬件闹钟没触发
#   → 检查 IRQ 配置：cat /proc/interrupts | grep rtc
#   → 检查 DT 里 interrupts 属性是否正确
```

### §6.4 时间偏差

```bash
# 1. 读 offset 校正值
cat /sys/class/rtc/rtc0/offset
# 如果属性不存在 → 驱动没实现 set_offset 回调

# 2. 设校正值（ppb，十亿分之一）
# 正 = 钟快，负 = 钟慢
echo 100000 > /sys/class/rtc/rtc0/offset   # 快 0.1ms/s = 8.64s/天

# 3. 看 tracepoint
echo 1 > /sys/kernel/tracing/events/rtc/rtc_set_offset/enable

# 4. 校准方法：
#   a. 记录当前 RTC 时间和 NTP 参考时间的差值
#   b. 等 24 小时
#   c. 再记录差值
#   d. 计算每天偏差的秒数 → 换算成 ppb
#   e. 设 offset
```

### §6.5 alarmtimer 没选中任何 RTC

```bash
# 1. 检查 alarmtimer 状态
dmesg | grep alarmtimer
# 应该看到 "alarmtimer: no device" 或 "alarmtimer: using rtcX"

# 2. 检查哪个 RTC 满足条件
for d in /sys/class/rtc/rtc*; do
    echo -n "$(basename $d): "
    echo -n "wakeup=$(cat $d/device/power/wakeup 2>/dev/null || echo N/A) "
    echo -n "alarm=$(grep alarm_IRQ /proc/driver/rtc 2>/dev/null || echo N/A)"
    echo
done

# 3. 如果所有 RTC 都不满足条件：
#   → 没有 wakeup-source → 加 DT 属性
#   → RTC_FEATURE_ALARM 被清 → 驱动没实现 set_alarm
#   → device_may_wakeup() 返回 false → 检查 device_set_wakeup_capable()
```

### §6.6 NTP systohc 写到了错误的 RTC

```bash
# 1. 检查 CONFIG_RTC_SYSTOHC_DEVICE
zcat /proc/config.gz | grep RTC_SYSTOHC_DEVICE
# 默认是 "rtc0"

# 2. 确认 rtc0 是期望的设备
cat /sys/class/rtc/rtc0/hctosys
# 1 = 是系统时钟 RTC

# 3. 如果 rtc0 不是期望的设备 → 修改 DT alias
# aliases { rtc0 = &expected_rtc; };
```
