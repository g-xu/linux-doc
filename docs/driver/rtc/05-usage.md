---
title: 使用指南
---

# RTC 子系统 · 使用模式

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇回答「谁在用 RTC，怎么用」——驱动侧的 probe 模板、内核态消费者的完整调用链、用户态 ioctl 序列、常见陷阱与故障模式。
> 行号不在本篇维护——需要精确到行的地方去 `03-internals.md`，取证命令去 `99-evidence.md`。

## §1 驱动侧 probe 模板

全树 **168 个驱动**，按总线类型分三大类。以下从每类提取典型模式。

### §1.1 I2C 类：以 `rtc-pcf8563` 为代表

```c
static int pcf8563_probe(struct i2c_client *client, ...)
{
    struct pcf8563 *pcf8563;
    struct rtc_device *rtc;
    int err;

    pcf8563 = devm_kzalloc(&client->dev, sizeof(*pcf8563), GFP_KERNEL);
    pcf8563->client = client;
    i2c_set_clientdata(client, pcf8563);

    rtc = devm_rtc_allocate_device(&client->dev);        // ① 分配
    if (IS_ERR(rtc))
        return PTR_ERR(rtc);

    rtc->ops = &pcf8563_rtc_ops;                         // ② 挂回调
    rtc->range_min = RTC_TIMESTAMP_BEGIN_2000;           // ③ 范围
    rtc->range_max = RTC_TIMESTAMP_END_2099;

    if (client->irq > 0) {                               // ④ 中断
        err = devm_request_threaded_irq(&client->dev, client->irq,
                                         NULL, pcf8563_irq,
                                         IRQF_ONESHOT | IRQF_TRIGGER_LOW,
                                         dev_name(&client->dev), pcf8563);
        if (err) {
            dev_err(&client->dev, "unable to request IRQ\n");
            return err;
        }
        device_set_wakeup_capable(&client->dev, true);   // ⑤ 唤醒能力
    }

    return devm_rtc_register_device(rtc);                // ⑥ 注册
}
```

I2C 类的特点：
- 用 `i2c_driver` + `of_match_table` 匹配 DT 节点
- 中断用 `devm_request_threaded_irq()`（`NULL` 硬中断 + 线程函数），因为 I2C 读寄存器需要睡眠
- `IRQF_ONESHOT` 是强制的——线程化 IRQ 必须在线程处理完之前保持中断线屏蔽
- `device_set_wakeup_capable()` 必须在 `devm_rtc_register_device()` 之前调用，否则 `wakealarm` sysfs 属性不会出现

### §1.2 Platform 类：以 `rtc-armada38x` 为代表

```c
static int armada38x_rtc_probe(struct platform_device *pdev)
{
    struct armada38x_rtc *rtc;
    struct resource *res;
    int err;

    rtc = devm_kzalloc(&pdev->dev, sizeof(*rtc), GFP_KERNEL);
    rtc->dev = &pdev->dev;

    rtc->rtc = devm_rtc_allocate_device(&pdev->dev);
    if (IS_ERR(rtc->rtc))
        return PTR_ERR(rtc->rtc);

    rtc->rtc->ops = &armada38x_rtc_ops;
    rtc->rtc->range_max = U32_MAX;      // 32-bit 秒计数器

    /* 内存映射寄存器 */
    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    rtc->regs_soc = devm_ioremap_resource(&pdev->dev, res);

    /* 中断可选——没有 IRQ 就降级为无闹钟模式 */
    rtc->irq = platform_get_irq(pdev, 0);
    if (rtc->irq >= 0) {
        err = devm_request_irq(&pdev->dev, rtc->irq, armada38x_rtc_interrupt,
                               0, dev_name(&pdev->dev), rtc);
        if (err) {
            rtc->irq = -1;              // 降级：不报错，继续注册
            rtc->rtc->ops = &armada38x_rtc_ops_no_alarm;  // 切换 ops
        }
    }

    /* 硬件 errata  workaround：初始化时做 dummy write 确保后续写操作可靠 */
    armada38x_rtc_delayed_write(rtc, RTC_STATUS, 0);
    udelay(5);

    return devm_rtc_register_device(rtc->rtc);
}
```

Platform 类的特点：
- 用 `platform_driver` + `of_match_table` 匹配
- 寄存器通过 `devm_ioremap_resource()` 映射，直接内存访问，不需要 regmap
- 中断**可选**——没有 IRQ 就切换 ops 表，去掉闹钟相关回调
- 某些 SoC 的 RTC 有硬件 errata，需要 workaround（dummy write + majority voting read）
- `range_max = U32_MAX` 意味着硬件是纯秒计数器，范围到 2106 年

### §1.3 SPI / 多总线类：以 `rtc-rv8803` 为代表

```c
static int rv8803_probe(struct device *regmap_dev, ...)
{
    rv8803->regmap = devm_regmap_init(...);
    rv8803->rtc = devm_rtc_allocate_device(regmap_dev);
    rv8803->rtc->ops = &rv8803_rtc_ops;
    rv8803->rtc->range_min = RTC_TIMESTAMP_BEGIN_2000;
    rv8803->rtc->range_max = RTC_TIMESTAMP_END_2099;
    set_bit(RTC_FEATURE_ALARM_RES_MINUTE, rv8803->rtc->features);
    return devm_rtc_register_device(rv8803->rtc);
}

/* 同一个驱动同时注册 I2C 和 SPI */
static struct i2c_driver rv8803_i2c_driver = { ... };
static struct spi_driver rv8803_spi_driver = { ... };
```

多总线类的特点：
- 用 `regmap` 抽象 I2C/SPI 寄存器访问
- 同一个 ops 表被多个总线驱动共享
- probe 函数接收 `struct device *` 而非特定总线类型

### §1.4 六步清单

| 步骤 | 函数/动作 | 必须？ | 常见遗漏 |
|---|---|---|---|
| ① 分配 | `devm_rtc_allocate_device()` | 是 | — |
| ② 挂回调 | `rtc->ops = &xxx_ops` | 是（至少 `read_time` + `set_time`） | — |
| ③ 声明能力 | `set_bit` / `clear_bit` features、`range_min/max` | 强烈建议 | 不设 range → 范围校验跳过 |
| ④ 注册中断 | `devm_request_threaded_irq()` | 有闹钟功能时必须 | 忘了 `IRQF_ONESHOT` → 注册失败 |
| ⑤ 声明唤醒 | `device_set_wakeup_capable()` | 有唤醒功能时必须 | 不设 → `wakealarm` 属性不出现 |
| ⑥ 注册 | `devm_rtc_register_device()` | 是 | — |

⚠️ 步骤 ③⑤ 必须在 ⑥ 之前完成。`devm_rtc_register_device()` 内部会读 `features` 和 `range_*` 做初始化，之后修改不安全。

### §1.5 中断处理函数的标准写法

```c
static irqreturn_t my_rtc_irq(int irq, void *dev_id)
{
    struct my_rtc_data *data = dev_id;
    u8 status;

    /* 读硬件状态寄存器 */
    status = regmap_read(data->regmap, STATUS_REG, &status);
    if (status & ALARM_FLAG) {
        /* 清硬件中断 */
        regmap_write(data->regmap, STATUS_REG, ALARM_FLAG);
        /* 上报框架 */
        rtc_update_irq(data->rtc, 1, RTC_IRQF | RTC_AF);
    }

    return IRQ_HANDLED;
}
```

`rtc_update_irq()` 的第三个参数是事件类型位掩码：

| 组合 | 含义 |
|---|---|
| `RTC_IRQF \| RTC_AF` | 闹钟中断 |
| `RTC_IRQF \| RTC_PF` | 周期中断 |
| `RTC_IRQF \| RTC_UF` | 更新中断（1Hz） |

框架收到后 `schedule_work()` 处理 timerqueue，并唤醒 `irq_queue` 上阻塞的用户态 `read()`。

---

## §2 内核态消费者

### §2.1 `kernel/time/ntp.c` —— 系统时间 → RTC

ntp.c 是 `rtc_set_time()` 在 RTC 框架**外部**的唯一内核态调用者（不算 arch 代码）。它实现 `CONFIG_RTC_SYSTOHC`：当 NTP 同步了系统时间后，定期把系统时间写回 RTC 硬件。

完整路径：

```
ntp_notify_cmos_timer()                    // ntp.c, 每 11 分钟由 ntp 触发
  └─ schedule_delayed_work(&sync_cmos_work, 0)
       └─ sync_hw_clock(work)              // ntp.c:574
            ├─ rtc = rtc_class_open(CONFIG_RTC_SYSTOHC_DEVICE)   // :587
            ├─ rtc_read_time(rtc, &tm)      // :591, 读 RTC 当前时间
            ├─ rtc_set_time(rtc, &tm)       // :597, 写回系统时间
            └─ rtc_class_close(rtc)         // :604
```

⚠️ `rtc_class_open()` 通过名字（`CONFIG_RTC_SYSTOHC_DEVICE`，默认 `"rtc0"`）查找 RTC 设备。如果系统里 `rtc0` 不是期望的那个 RTC（比如 DT alias 编号不同），systohc 会写到错误的设备上。

### §2.2 `kernel/time/alarmtimer.c` —— 休眠唤醒

alarmtimer 通过 `class_interface` 机制获取 RTC（不走 `rtc_class_open()`），用 `rtc_timer_start()` 设置硬件闹钟。完整路径见 `04-extensions.md` §2。

关键区别：alarmtimer 用的是**未导出的** `rtc_timer_start()` API，而不是公开的 `rtc_set_alarm()`。这是因为 alarmtimer 需要把闹钟挂进 timerqueue 红黑树，和用户的闹钟共存。

### §2.3 `kernel/power/suspend_test.c` —— 休眠测试

```c
static int __init suspend_test_init(void)
{
    rtc = rtc_class_open(dev_name(dev));     // :206
    ...
}

static void suspend_test_finish(void)
{
    rtc_read_time(rtc, &alm.time);           // :79
    ...
    rtc_set_alarm(rtc, &alm);                // 设短时间后的闹钟
    ...
}
```

测试代码用 `rtc_class_open()` 获取 RTC，设一个短时间的闹钟，然后触发休眠，验证系统能否被闹钟唤醒。

### §2.4 `drivers/soc/ti/pm33xx.c` —— TI SoC 电源管理

```c
omap_rtc = rtc_class_open("rtc0");           // pm33xx.c:458
```

TI AM33xx/AM43xx 的电源管理驱动用 `rtc_class_open()` 获取 RTC，用于低功耗模式下的时间保持。这是 arm64 上除 alarmtimer 和 suspend_test 之外的另一个 `rtc_class_open()` 使用者。

### §2.5 `rtc_read_time()` 外部消费者汇总

| 调用者 | 文件 | arm64？ | 获取方式 |
|---|---|---|---|
| alarmtimer | `kernel/time/alarmtimer.c:292` | **是** | `class_interface` 自动选中 |
| ntp systohc | `kernel/time/ntp.c:591` | **是** | `rtc_class_open(CONFIG_RTC_SYSTOHC_DEVICE)` |
| suspend_test | `kernel/power/suspend_test.c:79` | **是** | `rtc_class_open(dev_name(dev))` |
| TI PM | `drivers/soc/ti/pm33xx.c` | **是** | `rtc_class_open("rtc0")` |
| AMD PMC | `drivers/platform/x86/amd/pmc.c:723` | 否 | `rtc_class_open("rtc0")` |

### §2.6 `rtc_class_open()` 的使用模式

`rtc_class_open()` 通过名字查找 RTC 设备并增加模块引用计数。使用者**必须**在不再需要时调用 `rtc_class_close()` 释放引用。

```c
struct rtc_device *rtc;

rtc = rtc_class_open("rtc0");
if (IS_ERR(rtc))
    return PTR_ERR(rtc);

/* 使用 rtc_read_time / rtc_set_time / rtc_set_alarm 等 */
err = rtc_read_time(rtc, &tm);
...

rtc_class_close(rtc);
```

⚠️ `rtc_class_open()` 返回的是 `struct rtc_device *`，**不是**文件描述符。它直接操作内核对象，不需要 open/close 字符设备。但同一时刻只有一个进程能 `open()` 字符设备（`RTC_DEV_BUSY` 标志）——`rtc_class_open()` 不受这个限制，多个内核消费者可以同时持有 `rtc_device` 引用。

---

## §3 用户态 ioctl 序列

### §3.1 读时间

```c
int fd = open("/dev/rtc0", O_RDONLY);
if (fd < 0) { perror("open"); return 1; }

struct rtc_time tm;
if (ioctl(fd, RTC_RD_TIME, &tm) < 0) {
    perror("ioctl RTC_RD_TIME");
    close(fd);
    return 1;
}

printf("%04d-%02d-%02d %02d:%02d:%02d\n",
       tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
       tm.tm_hour, tm.tm_min, tm.tm_sec);
close(fd);
```

### §3.2 设闹钟并等待

```c
int fd = open("/dev/rtc0", O_RDONLY);

struct rtc_wkalrm alarm = {
    .enabled = 1,
    .time = { .tm_hour = 7, .tm_min = 0, .tm_sec = 0,
              .tm_mday = 25, .tm_mon = 7, .tm_year = 126 },
};
if (ioctl(fd, RTC_WKALM_SET, &alarm) < 0) {
    perror("RTC_WKALM_SET");
    close(fd);
    return 1;
}

/* 等待闹钟触发 */
unsigned long data;
if (read(fd, &data, sizeof(data)) < 0) {
    perror("read");
    close(fd);
    return 1;
}
/* data 的低字节包含事件类型：RTC_IRQF | RTC_AF */
/* data >> 8 是自上次 read 以来的中断次数 */

close(fd);
```

### §3.3 周期中断

```c
int fd = open("/dev/rtc0", O_RDONLY);

unsigned long freq = 2;
if (ioctl(fd, RTC_IRQP_SET, freq) < 0) {
    perror("RTC_IRQP_SET");    // >64Hz 需要 CAP_SYS_RESOURCE
}

ioctl(fd, RTC_PIE_ON, 0);

for (int i = 0; i < 10; i++) {
    unsigned long data;
    read(fd, &data, sizeof(data));
    /* data & RTC_PF → 周期中断 */
    /* data >> 8 → 跳过的中断次数（如果 CPU 忙） */
}

ioctl(fd, RTC_PIE_OFF, 0);
close(fd);
```

### §3.4 poll() 非阻塞等待

```c
int fd = open("/dev/rtc0", O_RDONLY);

ioctl(fd, RTC_AIE_ON, 0);     // 开启闹钟中断

struct pollfd pfd = { .fd = fd, .events = POLLIN };
int ret = poll(&pfd, 1, 5000);  // 5 秒超时

if (ret > 0 && (pfd.revents & POLLIN)) {
    unsigned long data;
    read(fd, &data, sizeof(data));
    /* 闹钟触发了 */
}

ioctl(fd, RTC_AIE_OFF, 0);
close(fd);
```

### §3.5 fasync 信号通知

```c
int fd = open("/dev/rtc0", O_RDONLY);

/* 注册 SIGIO 信号处理 */
signal(SIGIO, my_handler);

/* 启用 fasync */
fcntl(fd, F_SETFL, O_ASYNC);
fcntl(fd, F_SETOWN, getpid());

ioctl(fd, RTC_AIE_ON, 0);

/* 闹钟触发时收到 SIGIO，在 handler 里 read(fd, ...) */
```

---

## §4 常见陷阱

### §4.1 `tm_mon` 是 0-based

`struct rtc_time` 的 `tm_mon` 字段范围是 0–11，不是 1–12。`tm_year` 需要加 1900。用户态程序经常在这里犯错。

### §4.2 `RTC_ALM_SET` 的 24 小时限制

老式 `RTC_ALM_SET` ioctl 只能设未来 24 小时内的闹钟。超过返回 `-EINVAL`（`drivers/rtc/dev.c:280-293`）。用 `RTC_WKALM_SET` 代替——它没有这个限制。

### §4.3 同一时刻只能一个 open

`RTC_DEV_BUSY` 标志保证同一时刻只有一个进程能 `open()` 一个 RTC 字符设备。第二个 `open()` 返回 `-EBUSY`。这意味着不能用一个 fd 读时间、另一个 fd 设闹钟——必须复用同一个 fd。

⚠️ 这个限制只影响字符设备的 `open()`。`rtc_class_open()` 不受限制——多个内核消费者可以同时持有引用。

### §4.4 `devm_rtc_register_device()` vs `devm_rtc_device_register()`

| 宏/函数 | 步骤 | 推荐？ |
|---|---|---|
| `devm_rtc_register_device(rtc)` | 两步式：先 alloc 再 register | **推荐** |
| `devm_rtc_device_register(dev, name, ops, owner)` | 一步式 | 不推荐（无法在中间设 range/features） |

`devm_rtc_device_register()` 内部也是 alloc + register，但不给驱动插入自定义步骤。新驱动应该用两步式。本树仍有 47 个文件用旧 API。

### §4.5 注销后 ops 为 NULL

`devm_rtc_unregister_device()` 把 `rtc->ops` 置 NULL（`drivers/rtc/class.c:349`）。如果此时还有线程在调 `rtc_read_time()` 等 API，会看到 `ops == NULL` 并返回 `-ENODEV`。这是设计意图——不需要额外的引用计数保护。

### §4.6 `rtc_update_irq()` 可以在中断上下文调用

`rtc_update_irq()` 只做 `pm_stay_awake()` + `schedule_work()`，不睡眠，可以在硬中断处理函数里直接调用。实际的 timerqueue 处理在 workqueue 上下文执行。

### §4.7 `rtc_set_time()` 会修改传入的 `tm`

如果设备有 `start-year` 属性且待写时间落在硬件原生范围之外，`rtc_subtract_offset()` 会就地改写调用者的 `struct rtc_time`。要保留原值就自己先拷一份。

### §4.8 范围不设 = 校验跳过

驱动不设 `range_min` / `range_max`（两者均为 0，相等），`rtc_valid_range()` 直接放行。写入超范围时间后读回是垃圾值，但不报错。

---

## §5 故障模式速查

| 症状 | 可能原因 | 排查步骤 |
|---|---|---|
| `/sys/class/rtc/` 为空 | 驱动没 probe 成功 | `dmesg \| grep rtc`；检查 DT `compatible` |
| `cat /sys/class/rtc/rtc0/time` 返回错误 | `read_time` 回调失败 | 开 tracepoint：`echo 1 > /sys/kernel/tracing/events/rtc/rtc_read_time/enable` |
| `wakealarm` 属性不存在 | 父设备没声明 wakeup 能力 | 检查 DT `wakeup-source`；检查 `device_set_wakeup_capable()` |
| 闹钟设了但不触发 | IRQ 没注册 / `RTC_FEATURE_ALARM` 被清 | 检查 `dmesg` 里 IRQ 注册错误；检查 `cat /proc/driver/rtc` 里 `alarm_IRQ` |
| 时间偏差大 | `set_offset_nsec` 不对 / 没设范围 | 检查驱动是否设了正确的传输延迟补偿 |
| `open()` 返回 `-EBUSY` | 另一个进程已经 open 了 | `lsof /dev/rtc0` |
| `ioctl` 返回 `-ENODEV` | 设备已注销（`ops == NULL`） | 检查驱动是否正在 remove |
| `ioctl` 返回 `-ETIME` | 闹钟时间已过去 | 检查设置的时间是否在未来 |
| NTP 写回偏差 | systohc 写到了错误的 RTC | 检查 `CONFIG_RTC_SYSTOHC_DEVICE` 和 `rtc0` 的 alias |
