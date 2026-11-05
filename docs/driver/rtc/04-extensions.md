---
title: 扩展机制
---

# RTC 子系统 · 扩展点与驱动契约

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇覆盖驱动侧契约、两个完整驱动代码流分析（I2C 型 + platform 型）、alarmtimer 深度集成、nvmem、PM。

---

## §1 驱动契约

### §1.1 最小实现

```c
static const struct rtc_class_ops my_rtc_ops = {
    .read_time  = my_rtc_read_time,
    .set_time   = my_rtc_set_time,
};

static int my_rtc_probe(struct i2c_client *client, ...)
{
    struct rtc_device *rtc;

    rtc = devm_rtc_allocate_device(&client->dev);
    if (IS_ERR(rtc))
        return PTR_ERR(rtc);

    rtc->ops = &my_rtc_ops;
    rtc->range_min = RTC_TIMESTAMP_BEGIN_2000;
    rtc->range_max = RTC_TIMESTAMP_END_2099;

    return devm_rtc_register_device(rtc);
}
```

### §1.2 回调矩阵

| 回调 | 必须？ | 不设的后果 | 框架自动处理 |
|---|---|---|---|
| `read_time` | **必须** | 所有读时间 API 失败 | 无 |
| `set_time` | **必须** | `rtc_set_time()` 返回 `-EINVAL`，`systohc` 失败 | 无 |
| `set_alarm` | 可选 | `RTC_FEATURE_ALARM` 被清除 | 框架自动清 `FEATURE_ALARM` |
| `read_alarm` | 可选 | `rtc_read_alarm()` 返回 `-EINVAL` | 无 |
| `alarm_irq_enable` | 可选 | `rtc_alarm_irq_enable()` 返回 `-EINVAL` | 无 |
| `read_offset` / `set_offset` | 可选 | 校准功能不可用 | 框架自动设 `FEATURE_CORRECTION` |
| `ioctl` | 可选 | 未知 ioctl 返回 `-ENOTTY` | 无 |
| `proc` | 可选 | `/proc/driver/rtc` 无驱动自定义信息 | 无 |
| `param_get` / `param_set` | 可选 | `RTC_PARAM_GET/SET` 返回 `-EINVAL` | 无 |

### §1.3 范围声明

驱动**应该**设置 `range_min` 和 `range_max`。不设（两者均为 0）意味着框架跳过范围校验——任何时间值都能通过。

| 硬件特征 | `range_min` | `range_max` | 典型驱动 |
|---|---|---|---|
| 8-bit 年份，2000–2099 | `RTC_TIMESTAMP_BEGIN_2000` | `RTC_TIMESTAMP_END_2099` | pcf8563 |
| 7-bit 年份，2000–2079 | `RTC_TIMESTAMP_BEGIN_2000` | `RTC_TIMESTAMP_END_2079` | — |
| 全 64-bit 秒计数器 | `0` | `U32_MAX` 或更大 | armada38x |

### §1.4 Feature 标志的手动设置

框架默认置位 `RTC_FEATURE_ALARM` 和 `RTC_FEATURE_UPDATE_INTERRUPT`。驱动如果需要声明额外能力或关闭不支持的能力：

```c
// 闹钟精度只到分钟（pcf8563）
set_bit(RTC_FEATURE_ALARM_RES_MINUTE, rtc->features);

// 硬件不支持更新中断
clear_bit(RTC_FEATURE_UPDATE_INTERRUPT, rtc->features);

// 硬件没有闹钟中断线（实现了 set_alarm 但不能产生中断）
clear_bit(RTC_FEATURE_ALARM, rtc->features);
```

⚠️ 这些设置必须在 `devm_rtc_register_device()` 之前完成。

---

## §2 完整驱动分析：pcf8563（I2C 型）

`drivers/rtc/rtc-pcf8563.c`（617 行）。NXP PCF8563 / Epson RTC8564 / Micro Crystal RV8564 / NXP PCA8565。

### §2.1 硬件概述

PCF8563 是一颗 I2C RTC 芯片，寄存器布局：

| 寄存器 | 地址 | 内容 |
|--------|------|------|
| ST1 | 0x00 | 状态 1（测试位） |
| ST2 | 0x01 | 状态 2（AIE / AF / 中断使能） |
| SC | 0x02 | 秒（BCD，bit7 = LV 低电压标记） |
| MN | 0x03 | 分（BCD） |
| HR | 0x04 | 时（BCD，0-23） |
| DM | 0x05 | 日（BCD） |
| DW | 0x06 | 星期（0-6） |
| MO | 0x07 | 月（BCD，1-12，bit7 = 世纪位 C） |
| YR | 0x08 | 年（BCD，00-99） |
| AMN | 0x09-0x0C | 闹钟（分/时/日/星期） |
| CLKO | 0x0D | 时钟输出控制 |
| TMRC | 0x0E | 定时器控制 |
| TMR | 0x0F | 定时器计数 |

### §2.2 私有数据结构

```c
struct pcf8563 {                                    // rtc-pcf8563.c:62
    struct rtc_device *rtc;
    int c_polarity;     // 世纪位极性（启发式判断）
    struct i2c_client *client;
#ifdef CONFIG_COMMON_CLK
    struct clk_hw clkout_hw;  // 时钟输出（32768/1024/32/1 Hz）
#endif
};
```

★ `c_polarity` 的处理：PCF8563 的世纪位（MO 寄存器 bit7）含义不统一——有的芯片 1=19xx，有的 1=20xx。驱动用启发式方法：假设当前在 1970-2069 年范围内，如果 `tm_year >= 100`（2000+）且 MO_C=1，则 MO_C=1 表示 20xx。

### §2.3 I2C 通信层

```c
// 块读：先写寄存器地址，再读 N 字节
static int pcf8563_read_block_data(client, reg, length, buf)  // :86
{
    struct i2c_msg msgs[] = {
        { .addr = client->addr, .len = 1, .buf = &reg },      // 写寄存器地址
        { .addr = client->addr, .flags = I2C_M_RD,
          .len = length, .buf = buf },                         // 读数据
    };
    return (i2c_transfer(client->adapter, msgs, 2) != 2) ? -EIO : 0;
}

// 块写：逐字节写（不用块写是因为某些 PCF8563 修订版不支持 auto-increment）
static int pcf8563_write_block_data(client, reg, length, buf)  // :111
{
    for (i = 0; i < length; i++) {
        unsigned char data[2] = { reg + i, buf[i] };
        err = i2c_master_send(client, data, 2);
        if (err != 2) return -EIO;
    }
    return 0;
}
```

★★ **为什么逐字节写？** 注释没有解释，但这是 PCF8563 的已知 errata——某些批次在块写时地址自增不可靠。驱动选择安全路径：每次 `i2c_master_send` 只写 1 个寄存器。

### §2.4 probe 流程

```
pcf8563_probe(client)                              // :512
  ├─ i2c_check_functionality(adapter, I2C_FUNC_I2C)  // :520
  │   └─ 不支持 → -ENODEV
  │
  ├─ pcf8563 = devm_kzalloc(&client->dev, sizeof(*pcf8563), GFP_KERNEL)  // :523
  ├─ i2c_set_clientdata(client, pcf8563)
  ├─ pcf8563->client = client
  ├─ device_set_wakeup_capable(&client->dev, 1)    // :530 → 声明可做唤醒源
  │
  │  // 初始化定时器为最低频率（省电）
  ├─ buf = PCF8563_TMRC_1_60                      // :533
  ├─ pcf8563_write_block_data(client, REG_TMRC, 1, &buf)  // :534
  │
  │  // 清标志、关中断
  ├─ buf = 0
  ├─ pcf8563_write_block_data(client, REG_ST2, 1, &buf)  // :542
  │
  │  // 分配 RTC 设备
  ├─ pcf8563->rtc = devm_rtc_allocate_device(&client->dev)  // :548
  │
  │  // 设置 ops 和特性
  ├─ pcf8563->rtc->ops = &pcf8563_rtc_ops         // :552
  ├─ set_bit(RTC_FEATURE_ALARM_RES_MINUTE, ...)    // :554 → 闹钟只到分钟
  ├─ clear_bit(RTC_FEATURE_UPDATE_INTERRUPT, ...)  // :555 → 不支持 UIE
  ├─ pcf8563->rtc->range_min = RTC_TIMESTAMP_BEGIN_2000  // :556
  ├─ pcf8563->rtc->range_max = RTC_TIMESTAMP_END_2099    // :557
  ├─ pcf8563->rtc->set_start_time = true           // :558
  │
  │  // 注册中断（如果有）
  ├─ if (client->irq > 0) {                        // :560
  │   └─ devm_request_threaded_irq(                  // :561
  │        client->irq, NULL, pcf8563_irq,
  │        IRQF_SHARED | IRQF_ONESHOT | IRQF_TRIGGER_LOW,
  │        "rtc-pcf8563", client)
  │ }
  │
  ├─ devm_rtc_register_device(pcf8563->rtc)        // :572
  │
  │  // 注册时钟输出（如果 CONFIG_COMMON_CLK）
  └─ pcf8563_clkout_register_clk(pcf8563)          // :578
```

★★ **关键设计决策**：
1. **`set_start_time = true`**：告诉框架"我的 range_min 就是硬件的绝对起点"。配合 `range_min = BEGIN_2000`，框架知道这个 RTC 只能表示 2000-2099 年。
2. **`RTC_FEATURE_ALARM_RES_MINUTE`**：PCF8563 的闹钟寄存器没有秒字段（只有分/时/日/星期），所以闹钟精度只到分钟。
3. **`clear_bit(RTC_FEATURE_UPDATE_INTERRUPT)`**：PCF8563 没有硬件秒更新中断功能。不清除的话，`rtc_update_irq_enable()` 会先尝试硬件路径再 fallback 到软件模拟——多一次无谓的 I2C 传输。
4. **`IRQF_TRIGGER_LOW`**：PCF8563 的 INT# 引脚是低电平有效。
5. **`devm_request_threaded_irq` 第一个函数参数是 NULL**：表示没有硬中断处理函数，所有处理在 threaded handler（`pcf8563_irq`）里完成。这对 I2C 设备是必须的——硬中断里不能做 I2C 传输。

### §2.5 read_time 流程

```
pcf8563_rtc_read_time(dev, tm)                     // :198
  ├─ pcf8563_read_block_data(client, REG_ST1, 9, buf)  // :205
  │   └─ 读 ST1~YR 共 9 个寄存器
  │
  ├─ if (buf[REG_SC] & PCF8563_SC_LV)             // :209
  │   │  // 低电压检测：电池可能耗尽，时间不可靠
  │   └─ dev_err("low voltage detected...")
  │   └─ return -EINVAL                            // ★ 拒绝返回不可靠数据
  │
  ├─ tm->tm_sec  = bcd2bin(buf[SC] & 0x7F)        // :224
  ├─ tm->tm_min  = bcd2bin(buf[MN] & 0x7F)        // :225
  ├─ tm->tm_hour = bcd2bin(buf[HR] & 0x3F)        // :226
  ├─ tm->tm_mday = bcd2bin(buf[DM] & 0x3F)        // :227
  ├─ tm->tm_wday = buf[DW] & 0x07                 // :228 → 不经过 BCD
  ├─ tm->tm_mon  = bcd2bin(buf[MO] & 0x1F) - 1    // :229 → 硬件 1-12，rtc_time 0-11
  ├─ tm->tm_year = bcd2bin(buf[YR]) + 100          // :230 → 硬件 00-99，rtc_time 100-199
  │
  │  // 世纪位启发式
  └─ pcf8563->c_polarity = (buf[MO] & MO_C) ?     // :232
       (tm->tm_year >= 100) : (tm->tm_year < 100)
```

★★ **低电压检测**：`PCF8563_SC_LV`（SC 寄存器 bit7）在电池电压低于阈值时被硬件置位。一旦置位，时间数据可能不可靠（电池供电时振荡器可能停过）。驱动返回 `-EINVAL` 拒绝把不可靠数据交给框架。这个标记**不会被自动清除**——需要用户重新设置时间后才会消失。

★★ **BCD 编码**：PCF8563 的所有时间字段都是 BCD 编码（二进制编码的十进制）。`bcd2bin(0x59) = 59`。掩码操作用来屏蔽非数值位：
- 秒/分：`& 0x7F`（屏蔽 bit7，秒的 bit7 是 LV 标记）
- 时：`& 0x3F`（只取 0-23）
- 日：`& 0x3F`（只取 0-31）
- 月：`& 0x1F`（屏蔽 bit7 世纪位和 bit6-5，只取 1-12）

### §2.6 中断处理

```
pcf8563_irq(irq, dev_id)                           // :175
  │  // threaded IRQ handler（可以睡眠，做 I2C 传输）
  │
  ├─ pcf8563 = i2c_get_clientdata(dev_id)
  ├─ pcf8563_get_alarm_mode(client, NULL, &pending)  // :181
  │   └─ 读 ST2 寄存器，检查 AF（闹钟标志）bit
  │
  ├─ if (pending) {                                // :185
  │   ├─ rtc_update_irq(pcf8563->rtc, 1, RTC_IRQF | RTC_AF)  // :186
  │   │   └─ schedule_work(&rtc->irqwork) → rtc_timer_do_work()
  │   ├─ pcf8563_set_alarm_mode(client, 1)        // :187
  │   │   └─ 读 ST2 → 清 AF → 保留 AIE → 写 ST2
  │   └─ return IRQ_HANDLED
  │ }
  └─ return IRQ_NONE                               // 不是我们的中断
```

★★ **闹钟标志清除**：PCF8563 的 AF（Alarm Flag）是**写 0 清除**的。`pcf8563_set_alarm_mode(client, 1)` 在重开闹钟中断的同时清除 AF。如果不清除 AF，INT# 引脚会一直保持低电平，导致中断风暴。

### §2.7 驱动特征总结

| 特征 | pcf8563 |
|------|---------|
| 总线 | I2C（`i2c_driver`） |
| 寄存器访问 | `i2c_transfer` / `i2c_master_send` |
| 中断 | threaded IRQ（`IRQF_TRIGGER_LOW`） |
| 闹钟精度 | 分钟级（`RTC_FEATURE_ALARM_RES_MINUTE`） |
| UIE | 不支持（`clear_bit(RTC_FEATURE_UPDATE_INTERRUPT)`） |
| 范围 | 2000-2099（8-bit 年份） |
| 附加功能 | 时钟输出（CONFIG_COMMON_CLK） |
| 低电压检测 | SC 寄存器 bit7（`ioctl` → `RTC_VL_READ`） |
| ops 实现 | 6/11（ioctl, read_time, set_time, read_alarm, set_alarm, alarm_irq_enable） |

---

## §3 完整驱动分析：armada38x（platform 型）

`drivers/rtc/rtc-armada38x.c`（592 行）。Marvell Armada 38x / Armada 7K/8K SoC 内置 RTC。

### §3.1 硬件概述

与 pcf8563 的 I2C 通信不同，armada38x 的 RTC 寄存器通过 **内存映射** 直接访问（`readl` / `writel`）。有两组寄存器：

| 区域 | 说明 | 访问方式 |
|------|------|---------|
| `regs`（"rtc"） | RTC 宏本体：时间、闹钟、配置 | `readl` / `writel` |
| `regs_soc`（"rtc-soc"） | SoC 级中断控制、MBUS 桥接时序 | `readl` / `writel` |

### §3.2 硬件 errata 处理

★★★ **armada38x 有两个严重的硬件 errata，驱动必须处理**：

**Errata 1：写后延迟**（`rtc_delayed_write()`，`:104`）

```c
static void rtc_delayed_write(val, rtc, offset)
{
    writel(0, rtc->regs + RTC_STATUS);   // dummy write 1
    writel(0, rtc->regs + RTC_STATUS);   // dummy write 2
    writel(val, rtc->regs + offset);     // 实际写入
    udelay(5);                           // 等 5μs
}
```

根据 errata RES-3124064，直接写 RTC 寄存器可能失败。workaround：写入前先对 STATUS 寄存器做两次 dummy 写。写完后等 5μs 让更新完成。

**Errata 2：读不可靠**（`read_rtc_register_38x_wa()`，`:147`）

```c
static u32 read_rtc_register_38x_wa(rtc, rtc_reg)
{
    // 读 100 次，取出现次数最多的值（多数表决）
    for (i = 0; i < SAMPLE_NR; i++) {
        rtc->val_to_freq[i].value = readl(rtc->regs + rtc_reg);
        rtc->val_to_freq[i].freq = 0;
    }
    // ... 频率统计 ...
    return rtc->val_to_freq[index_max].value;
}
```

Armada 38x 的 RTC 寄存器读可能返回错误值。workaround：连续读 100 次，取**多数表决**结果。如果某个值出现超过 50 次就提前返回（优化）。

★★ 这两个 workaround 只用于 Armada 38x 变体。Armada 7K/8K 用普通的 `read_rtc_register()`（直接 `readl`），因为它没有这些 errata。驱动通过 `armada38x_rtc_data` 结构里的函数指针区分：

```c
static const struct armada38x_rtc_data armada38x_data = {
    .read_rtc_reg = read_rtc_register_38x_wa,   // 多数表决
    .clear_isr = armada38x_clear_isr,
    .unmask_interrupt = armada38x_unmask_interrupt,
    .alarm = ALARM1,                             // 用 ALARM1
};

static const struct armada38x_rtc_data armada8k_data = {
    .read_rtc_reg = read_rtc_register,           // 直接读
    .clear_isr = armada8k_clear_isr,
    .unmask_interrupt = armada8k_unmask_interrupt,
    .alarm = ALARM2,                             // 用 ALARM2（不同寄存器）
};
```

### §3.3 probe 流程

```
armada38x_rtc_probe(pdev)                          // :492
  ├─ rtc = devm_kzalloc(&pdev->dev, sizeof(*rtc), GFP_KERNEL)
  ├─ rtc->data = of_device_get_match_data(&pdev->dev)  // 区分 38x / 8k
  │
  ├─ rtc->val_to_freq = devm_kcalloc(SAMPLE_NR, ...)  // :504 → 多数表决缓冲区
  ├─ spin_lock_init(&rtc->lock)
  │
  │  // 映射两组寄存器
  ├─ res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "rtc")
  ├─ rtc->regs = devm_ioremap_resource(&pdev->dev, res)
  ├─ res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "rtc-soc")
  ├─ rtc->regs_soc = devm_ioremap_resource(&pdev->dev, res)
  │
  ├─ rtc->irq = platform_get_irq(pdev, 0)
  │
  │  // 分配 RTC 设备
  ├─ rtc->rtc_dev = devm_rtc_allocate_device(&pdev->dev)  // :524
  │
  │  // 注册中断（可选——中断不可用时降级为无闹钟）
  ├─ if (devm_request_irq(pdev->dev, rtc->irq, armada38x_rtc_alarm_irq, ...) < 0)
  │   └─ rtc->irq = -1                           // :531 → 标记为不可用
  │
  ├─ if (rtc->irq != -1)
  │   └─ device_init_wakeup(&pdev->dev, 1)       // :536 → 可做唤醒源
  │  else
  │   └─ clear_bit(RTC_FEATURE_ALARM, ...)       // :538 → 无中断 = 无闹钟
  │
  │  // 设置 MBUS 桥接时序
  ├─ rtc->data->update_mbus_timing(rtc)          // :541
  │
  │  // 设置 ops 和范围
  ├─ rtc->rtc_dev->ops = &armada38x_rtc_ops      // :543
  ├─ rtc->rtc_dev->range_max = U32_MAX           // :544 → 32-bit 秒计数器
  │
  └─ return devm_rtc_register_device(rtc->rtc_dev)  // :546
```

★★ **与 pcf8563 的关键差异**：
1. **`range_max = U32_MAX`**：armada38x 的 RTC_TIME 寄存器是纯 32-bit 秒计数器，不编码年月日。范围是 0 到 2^32-1 秒（约 136 年）。
2. **中断可选**：如果 `platform_get_irq()` 或 `devm_request_irq()` 失败，驱动不是返回错误，而是降级——清 `RTC_FEATURE_ALARM`，继续注册为纯时间源。pcf8563 则要求中断必须可用。
3. **没有 `range_min`**：只设了 `range_max`，`range_min` 保持默认 0。这意味着范围是 `[0, U32_MAX]`。
4. **`set_offset_nsec` 使用默认值**：虽然内存映射的延迟是纳秒级，但驱动没有覆盖默认值（1秒+5ms）。对于纯秒计数器的 RTC，`set_offset_nsec` 的含义不同——它影响的是 NTP 写回时的秒进位时机。

### §3.4 时钟校准（offset）

armada38x 实现了 `read_offset` / `set_offset`，这是 pcf8563 没有的功能：

```
armada38x_rtc_set_offset(dev, offset)             // :415
  │  // offset 单位是 ppb（parts per billion，十亿分之一）
  │
  ├─ offset = clamp(offset, -484270L, 488558L)    // :427 → 硬件范围限制
  │
  ├─ ppb_cor = armada38x_ppb_convert(offset)     // :429 → offset ↔ ppb 转换
  │
  │  // 选择校准模式
  ├─ off = DIV_ROUND_CLOSEST(ppb_cor, 954)       // :435 → 细模式（954 ppb/步）
  ├─ if (off > 127 || off < -128) {              // :436 → 超出 8-bit 范围
  │   └─ ccr = RTC_CCR_MODE;                     // 切粗模式（3815 ppb/步）
  │   └─ off = DIV_ROUND_CLOSEST(ppb_cor, 3815)
  │ }
  │
  └─ ccr |= (off & 0x3fff) ^ 0x2000              // :445 → 符号位编码
  └─ rtc_delayed_write(ccr, rtc, RTC_CCR)        // :446
```

★★ **两种校准模式**：
- **细模式**（`RTC_CCR_MODE = 0`）：每步 954 ppb，8-bit 有符号范围（-128~127），总范围 ±121.6 μs/s
- **粗模式**（`RTC_CCR_MODE = 1`）：每步 3815 ppb，范围 ±484~488 μs/s

驱动优先用细模式（精度更高），只在超出范围时切粗模式。

### §3.5 PM 支持

armada38x 有自己的 `SIMPLE_DEV_PM_OPS`（`:576`）：

```c
static int armada38x_rtc_suspend(dev)
{
    if (device_may_wakeup(dev))
        return enable_irq_wake(rtc->irq);    // 标记中断为唤醒源
    return 0;
}

static int armada38x_rtc_resume(dev)
{
    if (device_may_wakeup(dev)) {
        rtc->data->update_mbus_timing(rtc);  // ★ 恢复时重设 MBUS 时序
        return disable_irq_wake(rtc->irq);
    }
    return 0;
}
```

★★ **resume 时重设 MBUS 时序**：Armada SoC 在 suspend/resume 过程中，MBUS 桥接的时序参数可能丢失（取决于 SoC 的电源域设计）。驱动在 resume 时重新调用 `update_mbus_timing()` 确保寄存器访问可靠。

### §3.6 驱动特征总结

| 特征 | armada38x |
|------|-----------|
| 总线 | platform（内存映射） |
| 寄存器访问 | `readl` / `writel`（带 errata workaround） |
| 中断 | 标准 `devm_request_irq`（可选） |
| 闹钟精度 | 秒级（32-bit 绝对秒数） |
| UIE | 默认支持（未清除 `RTC_FEATURE_UPDATE_INTERRUPT`） |
| 范围 | `[0, U32_MAX]`（32-bit 秒计数器） |
| 附加功能 | 时钟校准（`read_offset` / `set_offset`） |
| errata | 写后 5μs 延迟 + 多数表决读 |
| PM | `enable_irq_wake` / `disable_irq_wake` + MBUS 时序恢复 |
| ops 实现 | 7/11（read_time, set_time, read_alarm, set_alarm, alarm_irq_enable, read_offset, set_offset） |
| 变体 | armada-380（多数表决读）/ armada-8k（直接读，ALARM2） |

---

## §4 两种驱动类型对比

| 维度 | pcf8563（I2C） | armada38x（platform） |
|------|---------------|---------------------|
| 通信延迟 | 毫秒级（I2C 协议开销） | 纳秒级（内存映射） |
| 中断上下文 | **必须** threaded IRQ（I2C 不能硬中断） | 可以硬中断（但驱动也用了 threaded） |
| 电源域 | 独立供电（纽扣电池） | SoC 电源域（suspend 时可能断电） |
| 寄存器可靠性 | 高（I2C 有 ACK 机制） | 低（需要 errata workaround） |
| 时间表示 | BCD 年月日时分秒 | 纯 32-bit 秒计数器 |
| 世纪问题 | 有（世纪位启发式处理） | 无（纯秒数，无歧义） |
| 范围 | 100 年（2000-2099） | 136 年（0 ~ 2^32-1 秒） |
| 校准 | 不支持 | 支持（双模式 ppb） |
| `set_offset_nsec` | 默认值够用（I2C 延迟 ~5ms < 默认 1s+5ms） | 默认值也够用（但含义不同） |

---

## §5 alarmtimer 深度集成

`kernel/time/alarmtimer.c`（965 行）是 RTC 框架的最大消费者。它提供 `CLOCK_REALTIME_ALARM` 和 `CLOCK_BOOTTIME_ALARM` 两个 POSIX 时钟。

### §5.1 RTC 设备选择

alarmtimer 通过 `class_interface` 机制自动选择 RTC：

```
alarmtimer_rtc_add_device(dev, class_intf)       // alarmtimer.c:84
  │
  ├─ if (rtcdev) return -EBUSY                   // :92 → 已经选过了
  │
  ├─ if (!test_bit(RTC_FEATURE_ALARM, rtc->features))  // :95
  │   └─ return -1                               // 不支持闹钟
  │
  ├─ if (!device_may_wakeup(rtc->dev.parent))    // :97
  │   └─ return -1                               // 不能做唤醒源
  │
  │  // 注册 platform_device（用于 PM ops）
  ├─ pdev = platform_device_register_data(dev, "alarmtimer", ...)  // :100
  ├─ if (!IS_ERR(pdev))
  │   └─ device_init_wakeup(&pdev->dev, true)
  │
  ├─ spin_lock_irqsave(&rtcdev_lock, flags)      // :105
  ├─ if (!IS_ERR(pdev) && !rtcdev) {             // :106 → 双重检查
  │   ├─ if (!try_module_get(rtc->owner)) {      // :107 → 防止模块卸载
  │   │   └─ ret = -1; goto unlock
  │   │ }
  │   ├─ rtcdev = rtc                            // :112 → 全局唯一
  │   ├─ get_device(dev)                         // :114 → 持引用
  │   └─ pdev = NULL                             // :115 → 防止下面 unregister
  │ }
  ├─ spin_unlock_irqrestore(&rtcdev_lock, flags)  // :120
  │
  └─ platform_device_unregister(pdev)            // :122 → 如果没被选中则释放
```

★★ **选择条件总结**：
1. 还没有选中过（`rtcdev == NULL`）
2. 支持闹钟（`RTC_FEATURE_ALARM`）
3. 父设备可做唤醒源（`device_may_wakeup()`）
4. 驱动模块可以 hold（`try_module_get()` 成功）

★★ **`try_module_get()` 的作用**：alarmtimer 持有了 `rtcdev` 的引用，如果不 `try_module_get(rtc->owner)`，驱动模块可能被 `rmmod` 卸载，导致 `rtcdev` 指向已释放的内存。`get_device(dev)` 防止设备被释放，`try_module_get()` 防止模块被卸载。

### §5.2 suspend 路径

当系统进入 suspend 时，alarmtimer 的 PM ops 被调用：

```
alarmtimer_suspend(dev)                          // alarmtimer.c:242
  │
  │  // 第一步：收集 freezer 信息
  ├─ spin_lock_irqsave(&freezer_delta_lock, flags)
  ├─ min = freezer_delta; expires = freezer_expires; type = freezer_alarmtype
  ├─ freezer_delta = 0                           // 消费掉
  ├─ spin_unlock_irqrestore
  │
  ├─ rtc = alarmtimer_get_rtcdev()              // :257
  ├─ if (!rtc) return 0                         // 没有 RTC → 不唤醒
  │
  │  // 第二步：找最近的 alarm
  ├─ for (i = 0; i < ALARM_NUMTYPE; i++) {      // :263
  │   ├─ spin_lock_irqsave(&base->lock, flags)
  │   ├─ next = timerqueue_getnext(&base->timerqueue)
  │   ├─ spin_unlock_irqrestore
  │   ├─ if (!next) continue
  │   ├─ delta = ktime_sub(next->expires, base->get_ktime())
  │   └─ if (!min || delta < min) {             // 找最近的
  │       expires = next->expires
  │       min = delta
  │       type = i
  │   }
  │ }
  ├─ if (min == 0) return 0                     // 没有 alarm → 不唤醒
  │
  │  // 第三步：太近了就不睡了
  ├─ if (ktime_to_ns(min) < 2 * NSEC_PER_SEC) { // :283
  │   ├─ pm_wakeup_event(dev, 2 * MSEC_PER_SEC) // 报告唤醒事件
  │   └─ return -EBUSY                          // 拒绝 suspend
  │ }
  │
  │  // 第四步：设 RTC 定时器唤醒
  ├─ rtc_timer_cancel(rtc, &rtctimer)            // :291
  ├─ rtc_read_time(rtc, &tm)                     // :292 → 读 RTC 当前时间
  ├─ now = rtc_tm_to_ktime(tm)                   // :293
  ├─ now = ktime_add(now, min)                   // :294 → 当前 + delta = 唤醒时刻
  │
  ├─ ret = rtc_timer_start(rtc, &rtctimer, now, 0)  // :297
  │   └─ 内部调 __rtc_set_alarm() 写硬件
  │
  ├─ if (ret < 0)                               // :298
  │   └─ pm_wakeup_event(dev, MSEC_PER_SEC)     // 设闹钟失败 → 立即唤醒
  │
  └─ return ret
```

★★ **`< 2 秒就拒绝 suspend` 的逻辑**：如果最近的 alarm 在 2 秒内就要触发，不如不睡——suspend/resume 本身就需要 1-2 秒。返回 `-EBUSY` 让 PM 核心推迟 suspend。

★★ **`rtc_timer_start()` 走的是未导出 API**：alarmtimer 直接调用 `rtc_timer_start()` / `rtc_timer_cancel()`，这些函数没有 `EXPORT_SYMBOL`。alarmtimer 能用是因为它是 `obj-y`（编进内核），不是模块。

### §5.3 resume 路径

```
alarmtimer_resume(dev)                           // alarmtimer.c:303
  ├─ rtc = alarmtimer_get_rtcdev()
  ├─ if (rtc)
  │   └─ rtc_timer_cancel(rtc, &rtctimer)        // 取消唤醒闹钟
  └─ return 0
```

resume 只做一件事：取消 suspend 时设的 RTC 定时器。系统已经醒了，不需要硬件闹钟再触发。

### §5.4 POSIX 时钟接口

alarmtimer 实现了 `struct k_clock alarm_clock`（`alarmtimer.c:888`），注册为 `CLOCK_REALTIME_ALARM` 和 `CLOCK_BOOTTIME_ALARM` 的后端：

| 操作 | 函数 | 说明 |
|------|------|------|
| `clock_getres` | `alarm_clock_getres()` | 返回 hrtimer 分辨率 |
| `timer_create` | `alarm_timer_create()` | 检查 `alarmtimer_get_rtcdev()` + `CAP_WAKE_ALARM` |
| `timer_set` | `common_timer_set()` | 通用 posix timer 设置 |
| `timer_arm` | `alarm_timer_arm()` | 调 `alarm_start()` |
| `nsleep` | `alarm_timer_nsleep()` | `clock_nanosleep()` 对 _ALARM 时钟 |

★★ **`CAP_WAKE_ALARM` 权限检查**：`alarm_timer_create()` 和 `alarm_timer_nsleep()` 都检查 `capable(CAP_WAKE_ALARM)`。这个 capability 控制谁能设唤醒闹钟——防止普通用户设一个马上触发的闹钟来阻止系统休眠（DoS）。

---

## §6 nvmem 集成

`drivers/rtc/nvmem.c`（30 行）是框架里最小的文件。

```c
int devm_rtc_nvmem_register(struct rtc_device *rtc,
                            struct nvmem_config *nvmem_config)
{
    nvmem_config->dev = rtc->dev.parent;
    nvmem_config->owner = rtc->owner;
    nvmem = devm_nvmem_register(dev, nvmem_config);
    return PTR_ERR_OR_ZERO(nvmem);
}
```

驱动在 probe 里调用这个函数，传入 `nvmem_config`（包含读写回调、大小、word_size 等）。注册后用户态可以通过 `/sys/bus/nvmem/devices/` 访问 RTC 芯片里的非易失存储。

典型用法：RTC 芯片里有几十字节的备份 RAM（如 DS1307 的 56 字节 NVRAM），可以用来存开机计数器、配置参数等。

---

## §7 class 级 PM 支持

`drivers/rtc/class.c` 定义了 `SIMPLE_DEV_PM_OPS(rtc_class_dev_pm_ops, rtc_suspend, rtc_resume)`（`class.c:196`），但**只对 `CONFIG_RTC_HCTOSYS_DEVICE` 指定的那个 RTC 生效**。

### §7.1 suspend

```
rtc_suspend(dev)                                   // class.c:101
  ├─ if (timekeeping_rtc_skipsuspend()) return 0
  ├─ if (strcmp(dev_name, CONFIG_RTC_HCTOSYS_DEVICE) != 0) return 0  // ★ 只服务指定设备
  │
  ├─ rtc_read_time(rtc, &tm)                     // :115 → 读 RTC
  ├─ ktime_get_real_ts64(&old_system)            // :121 → 读系统时间
  ├─ old_rtc.tv_sec = rtc_tm_to_time64(&tm)      // :122
  │
  │  // 漂移补偿
  ├─ delta = old_system - old_rtc                // :130
  ├─ delta_delta = delta - old_delta             // :131
  ├─ if (|delta_delta.tv_sec| >= 2)              // :132 → 偏差 > 2 秒
  │   └─ old_delta = delta                       // NTP 可能修正了，重新基准
  │  else
  │   └─ old_system -= delta_delta               // :140 → 微调
  └─ return 0
```

### §7.2 resume

```
rtc_resume(dev)                                  // class.c:146
  ├─ if (timekeeping_rtc_skipresume()) return 0
  ├─ if (strcmp(dev_name, CONFIG_RTC_HCTOSYS_DEVICE) != 0) return 0
  │
  ├─ ktime_get_real_ts64(&new_system)            // :162
  ├─ rtc_read_time(rtc, &tm)                     // :163
  ├─ new_rtc.tv_sec = rtc_tm_to_time64(&tm)      // :169
  │
  ├─ if (new_rtc.tv_sec < old_rtc.tv_sec)        // :172 → 时间倒流？
  │   └─ return 0                                // 放弃
  │
  │  // 计算休眠时间
  ├─ sleep_time = new_rtc - old_rtc              // :178
  ├─ sleep_time -= (new_system - old_system)     // :187 → 减去内核运行时间
  │
  ├─ if (sleep_time.tv_sec >= 0)                 // :190
  │   └─ timekeeping_inject_sleeptime64(&sleep_time)  // :191 → 注入系统时钟
  └─ return 0
```

★★ **休眠时间计算公式**：`sleep_time = (new_rtc - old_rtc) - (new_system - old_system)`

- `new_rtc - old_rtc`：RTC 记录的实际经过时间（包括休眠）
- `new_system - old_system`：系统时钟记录的经过时间（不包括休眠，因为 suspend 期间系统时钟停了）
- 差值 = 休眠期间经过的时间

这个值被注入系统时钟（`timekeeping_inject_sleeptime64`），让系统时间跳前休眠时长。

---

## §8 驱动追加 sysfs 属性

`drivers/rtc/sysfs.c` 提供了 `rtc_add_group()` / `rtc_add_groups()`（`:348` / `:315`），让驱动在标准 9 个属性之外追加自定义属性组。

```c
int rtc_add_group(rtc, grp)
{
    // 分配新的 groups 数组（旧 + 新 + 1）
    // devm_kcalloc → memcpy 旧 → memcpy 新 → 替换 rtc->dev.groups
    // 如果旧的不是默认数组 → devm_kfree 旧的
}
```

典型用法：驱动暴露校准寄存器原始值、温度传感器读数、电池状态等。

---

## §9 Kconfig 与私有头

### §9.1 Kconfig 结构

`drivers/rtc/Kconfig`（1999 行，193 个 `config`）：

| 段 | 内容 |
|---|---|
| `RTC_CLASS` | 框架总开关 |
| `RTC_HCTOSYS` / `RTC_SYSTOHC` | 时间同步方向 |
| `RTC_INTF_DEV` / `SYSFS` / `PROC` | 三个用户态接口 |
| `RTC_INTF_DEV_UIE_EMUL` | UIE 软件模拟 |
| `RTC_NVMEM` | nvmem 支持 |
| `RTC_DEBUG` | 全目录 `-DDEBUG` |
| 其余 ~180 个 | 各芯片驱动 |

### §9.2 私有头 `rtc-core.h`

43 行，只做一件事：Kconfig 关掉时把接口函数替换成空 inline。

| Kconfig 关掉 | 被替换的函数 |
|---|---|
| `!RTC_INTF_DEV` | `rtc_dev_init()` → 空，`rtc_dev_prepare()` → 空 |
| `!RTC_INTF_PROC` | `rtc_proc_add_device()` → 空，`rtc_proc_del_device()` → 空 |
| `!RTC_INTF_SYSFS` | `rtc_get_dev_attribute_groups()` → 返回 NULL |

---

## §10 DT 绑定

`Documentation/devicetree/bindings/rtc/` 下有 **82 个绑定文件**。arm64 DTS 里提到 `rtc` 的有 **217 个文件**。

| 属性 | 必需？ | 含义 |
|---|---|---|
| `compatible` | 是 | 芯片型号 |
| `reg` | 是 | I2C/SPI 地址或内存映射基址 |
| `interrupts` | 否 | 闹钟中断线 |
| `wakeup-source` | 否 | 声明可做唤醒源 |
| `start-year` | 否 | 框架级属性——`rtc_device_get_offset()` 直接读 |
| `clocks` | 否 | 外部时钟输入 |

★★ **`start-year` 是框架级属性**：不需要驱动参与，`rtc_device_get_offset()`（`class.c:284`）直接用 `device_property_read_u32()` 读它。如果 DT 里没写，范围偏移机制不生效。
