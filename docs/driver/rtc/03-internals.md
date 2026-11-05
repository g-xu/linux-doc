---
title: 内部实现
---

# RTC 子系统 · 内部机制

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇回答「它是怎么工作的」——注册流程、锁设计、时间读写管线、闹钟补全算法、定时器队列、中断链、ioctl 分发、UIE 模拟、tracepoint、错误码、initcall 顺序。
> 所有行号对应 `drivers/rtc/` 和 `include/linux/rtc.h`，除非另注。

---

## §1 设备注册流程

### §1.1 两步式注册

驱动的典型路径：

```c
// probe() 里：
rtc = devm_rtc_allocate_device(&client->dev);   // class.c:360
if (IS_ERR(rtc))
    return PTR_ERR(rtc);

rtc->ops = &my_rtc_ops;
rtc->range_min = RTC_TIMESTAMP_BEGIN_2000;
rtc->range_max = RTC_TIMESTAMP_END_2099;
// ... 设置 features ...

return devm_rtc_register_device(rtc);            // 宏 → class.c:389
```

**第一步：`devm_rtc_allocate_device(dev)`**（`class.c:360`）

```
devm_rtc_allocate_device(dev)
  ├─ rtc_device_get_id(dev)                    // class.c:249
  │   ├─ of_alias_get_id(dev->of_node, "rtc")  // 尝试 DT alias
  │   └─ ida_alloc(&rtc_ida, GFP_KERNEL)       // 没有 alias 则自增
  ├─ rtc_allocate_device()                     // class.c:203（内部 static）
  │   ├─ kzalloc(sizeof(*rtc), GFP_KERNEL)     // 全零初始化
  │   ├─ device_initialize(&rtc->dev)          // refcount = 1
  │   ├─ 设默认值（§2.1 的完整列表）
  │   └─ return rtc
  ├─ rtc->id = id
  ├─ rtc->dev.parent = dev
  └─ devm_add_action_or_reset(dev, devm_rtc_release_device, rtc)
```

此时 `rtc->ops` 为 NULL，字符设备还没建。驱动在这两步之间填字段。

**第二步：`__devm_rtc_register_device(owner, rtc)`**（`class.c:389`）

```
__devm_rtc_register_device(owner, rtc)
  ├─ if (!rtc->ops) return -EINVAL             // class.c:394
  ├─ if (!rtc->ops->set_alarm)
  │     clear_bit(RTC_FEATURE_ALARM, ...)       // class.c:400
  ├─ if (rtc->ops->set_offset)
  │     set_bit(RTC_FEATURE_CORRECTION, ...)    // class.c:403
  ├─ rtc->owner = owner                        // class.c:405
  ├─ rtc_device_get_offset(rtc)                // class.c:406 → §2.3
  ├─ __rtc_read_alarm(rtc, &alrm)              // class.c:409 → §4
  │   └─ 如果成功且时间合法：
  │      rtc_initialize_alarm(rtc, &alrm)      // class.c:411
  ├─ rtc_dev_prepare(rtc)                      // class.c:413 → dev.c:540
  │   ├─ rtc->dev.devt = MKDEV(MAJOR(rtc_devt), rtc->id)
  │   ├─ INIT_WORK(&rtc->uie_task, ...)        // 如果 UIE_EMUL
  │   ├─ timer_setup(&rtc->uie_timer, ...)     // 如果 UIE_EMUL
  │   └─ cdev_init(&rtc->char_dev, &rtc_dev_fops)
  ├─ cdev_device_add(&rtc->char_dev, &rtc->dev) // class.c:415
  │   ├─ 成功 → dev_dbg("char device (%d:%d)")
  │   └─ 失败 → set_bit(RTC_NO_CDEV) + dev_warn（不阻止注册）
  ├─ rtc_proc_add_device(rtc)                  // class.c:425
  └─ if (dev_name == CONFIG_RTC_HCTOSYS_DEVICE)
        rtc_hctosys(rtc)                       // class.c:432 → 读 RTC 设系统时钟
```

★★ **`cdev_device_add()` 失败不致命**：如果字符设备注册失败（比如主设备号冲突），框架只设 `RTC_NO_CDEV` 标志并打 warning。sysfs 和内核 API 仍然可用——只是没有 `/dev/rtcN`。注销路径会据此跳过 `cdev_device_del()`。

### §1.2 注销路径

`devm_rtc_unregister_device()`（`class.c:337`）在 devm 回调里执行：

```
devm_rtc_unregister_device(data)
  ├─ mutex_lock(&rtc->ops_lock)               // class.c:341
  ├─ rtc_proc_del_device(rtc)                  // class.c:346
  ├─ if (!RTC_NO_CDEV)
  │     cdev_device_del(&rtc->char_dev, &rtc->dev)  // class.c:348
  ├─ rtc->ops = NULL                           // class.c:349 ★
  ├─ mutex_unlock(&rtc->ops_lock)              // class.c:350
  
  ↓ 引用计数归零后
  
rtc_device_release(dev)                        // class.c:26
  ├─ mutex_lock(&rtc->ops_lock)
  ├─ while (timerqueue_getnext) timerqueue_del  // class.c:33-34
  ├─ mutex_unlock
  ├─ cancel_work_sync(&rtc->irqwork)           // class.c:37
  ├─ ida_free(&rtc_ida, rtc->id)               // class.c:39
  ├─ mutex_destroy(&rtc->ops_lock)             // class.c:40
  └─ kfree(rtc)                                // class.c:41
```

★★ **`rtc->ops = NULL` 的屏障效应**：注销路径在 `ops_lock` 保护下把 `ops` 置 NULL。此后任何通过 `rtc_read_time()` / `rtc_set_time()` 等 API 的调用都会在 `interface.c` 里看到 `!rtc->ops` 并返回 `-ENODEV`。但 `cancel_work_sync(&rtc->irqwork)` 在 `release` 里而不是 `unregister` 里——这意味着注销后、引用释放前，可能还有 `irqwork` 在排队。`cancel_work_sync()` 保证在 `kfree` 前等它跑完。

---

## §2 锁设计

### §2.1 两把锁的职责划分

| 锁 | 类型 | 可睡眠 | 保护范围 | 典型持锁时长 |
|---|------|--------|---------|------------|
| `ops_lock` | `struct mutex` | **是** | `ops` 指针、`timerqueue` 增删、所有 `ops` 回调 | 毫秒级（I2C 传输） |
| `irq_lock` | `spinlock_t` | **否** | `irq_data`、`async_queue`、UIE 模拟 7 个位域 | 微秒级（纯内存操作） |

**为什么不用一把锁？** `ops_lock` 的回调涉及 I2C/SPI 传输（可能睡眠几百微秒到几毫秒）。如果用 spinlock，在 `PREEMPT_RT` 内核上会导致长时间关中断。分成两把锁后，中断上下文只需要 `irq_lock`（纯内存操作，纳秒级），不被 I2C 传输阻塞。

### §2.2 锁的获取顺序

**先 `ops_lock` 后 `irq_lock`**。代码中实际同时持两把锁的场景极少：

- `rtc_handle_legacy_irq()`（`interface.c:645`）只取 `irq_lock`
- `rtc_timer_do_work()`（`interface.c:928`）只取 `ops_lock`
- `rtc_dev_ioctl()`（`dev.c:203`）取 `ops_lock`，但在调带锁 API 前释放

### §2.3 ioctl 路径的 unlock/relock 模式

`rtc_dev_ioctl()` 的锁模式值得单独分析。它进入时取 `ops_lock`，但很多 ioctl 分支在调用 API 前先 `mutex_unlock`：

```c
case RTC_RD_TIME:
    mutex_unlock(&rtc->ops_lock);     // dev.c:321
    err = rtc_read_time(rtc, &tm);    // 内部会重新 lock
    ...
    return err;

case RTC_SET_TIME:
    mutex_unlock(&rtc->ops_lock);     // dev.c:332
    ...
    return rtc_set_time(rtc, &tm);    // 内部会重新 lock
```

**为什么？** `rtc_read_time()` / `rtc_set_time()` 内部自己用 `mutex_lock_interruptible(&rtc->ops_lock)` 加锁。如果 ioctl 路径不先释放，就会死锁。

**副作用**：权限检查（`CAP_SYS_TIME` 等，在 `mutex_lock` 之后）和实际操作（`mutex_unlock` 之后）之间存在窗口。在这个窗口里：
- 另一个线程可以 `open()` / `release()` 设备
- 注销路径可以 `rtc->ops = NULL`
- 但 API 层会检查 `!rtc->ops` 返回 `-ENODEV`，不会崩溃

---

## §3 时间读写管线

### §3.1 读时间 —— 完整调用链

```
rtc_read_time(rtc, tm)                         // interface.c:110
  ├─ mutex_lock_interruptible(&rtc->ops_lock)   // interface.c:114
  │   └─ 失败 → return -EINTR
  ├─ __rtc_read_time(rtc, tm)                  // interface.c:84
  │   ├─ if (!rtc->ops) return -ENODEV         // interface.c:88
  │   ├─ if (!rtc->ops->read_time) return -EINVAL  // interface.c:90
  │   ├─ memset(tm, 0, sizeof(*tm))            // interface.c:93 ★ 预清零
  │   ├─ rtc->ops->read_time(rtc->dev.parent, tm)  // interface.c:94
  │   │   └─ 失败 → dev_dbg + return err
  │   ├─ rtc_add_offset(rtc, tm)               // interface.c:101
  │   │   └─ if (offset_secs != 0 && 不在重叠区)
  │   │        rtc_time64_to_tm(secs + offset_secs, tm)
  │   └─ rtc_valid_tm(tm)                      // interface.c:103
  │       └─ 失败 → dev_dbg("rtc_time isn't valid")
  ├─ mutex_unlock(&rtc->ops_lock)              // interface.c:119
  └─ trace_rtc_read_time(rtc_tm_to_time64(tm), err)  // interface.c:121
```

★★ **注意 `memset(tm, 0, ...)` 在 `__rtc_read_time():93`**：框架在调驱动回调前把 `tm` 清零。这意味着如果驱动的 `read_time` 实现漏填了某个字段（比如 `tm_yday`），该字段会是 0 而不是垃圾值。但 `rtc_valid_tm()` 不检查 `tm_yday`，所以这个遗漏不会被发现。

### §3.2 写时间 —— 完整调用链

```
rtc_set_time(rtc, tm)                          // interface.c:126
  ├─ rtc_valid_tm(tm)                          // interface.c:130 → 先校验
  │   └─ 失败 → return err
  ├─ rtc_valid_range(rtc, tm)                  // interface.c:134
  │   ├─ if (range_min == range_max) skip      // 驱动没设范围 → 跳过
  │   ├─ range_min = set_start_time ? start_secs : range_min
  │   ├─ range_max = set_start_time ? (start_secs + range_max - range_min) : range_max
  │   └─ if (time < range_min || time > range_max) return -ERANGE
  ├─ rtc_subtract_offset(rtc, tm)              // interface.c:138 ★ 就地修改 tm
  │   └─ if (offset_secs != 0 && secs 不在 [range_min, range_max])
  │        rtc_time64_to_tm(secs - offset_secs, tm)  // ★ 改写调用者的 tm
  ├─ if (uie) rtc_update_irq_enable(rtc, 0)    // interface.c:146 → 先关 UIE
  ├─ mutex_lock_interruptible(&rtc->ops_lock)   // interface.c:151
  ├─ if (!rtc->ops) err = -ENODEV
  │  else if (rtc->ops->set_time)
  │    err = rtc->ops->set_time(dev, tm)       // interface.c:158
  │  else err = -EINVAL
  ├─ pm_stay_awake(rtc->dev.parent)            // interface.c:162 → 阻止休眠
  ├─ mutex_unlock(&rtc->ops_lock)              // interface.c:163
  ├─ schedule_work(&rtc->irqwork)              // interface.c:165 → "可能有 timer 到期"
  ├─ if (uie) rtc_update_irq_enable(rtc, 1)    // interface.c:168 → 恢复 UIE
  └─ trace_rtc_set_time(rtc_tm_to_time64(tm), err)  // interface.c:173
```

★★ **`rtc_set_time()` 的两个副作用**：
1. **就地修改 `tm`**：`rtc_subtract_offset()` 可能改写调用者传入的 `struct rtc_time`。要保留原值须先拷贝。
2. **`schedule_work(&rtc->irqwork)`**：写完时间后触发 `rtc_timer_do_work()`。原因：设了新的时间后，队列里的 timer 可能已经"到期"了（因为时钟被往前拨了），需要立即检查。

★★ **`pm_stay_awake()` 的作用**：每次写 RTC 时间时阻止系统休眠。这确保 NTP 同步（`systohc`）不会因为系统进入休眠而中断。对应的 `pm_relax()` 在 `rtc_timer_do_work()` 结束时调用。

### §3.3 `rtc_valid_range()` 的边界条件

`rtc_valid_range()`（`interface.c:67`）是 `static` 函数，只有 `rtc_set_time()` 调用。

```c
static int rtc_valid_range(struct rtc_device *rtc, struct rtc_time *tm)
{
    if (rtc->range_min != rtc->range_max) {     // 驱动没设范围 → 整体跳过
        time64_t time = rtc_tm_to_time64(tm);
        time64_t range_min = rtc->set_start_time ? rtc->start_secs : rtc->range_min;
        timeu64_t range_max = rtc->set_start_time ?
            (rtc->start_secs + rtc->range_max - rtc->range_min) : rtc->range_max;
        if (time < range_min || time > range_max)
            return -ERANGE;
    }
    return 0;
}
```

★ 如果启用了 `start-year`，有效范围是 `[start_secs, start_secs + range_max - range_min]`——硬件范围被平移到以 `start_secs` 为起点。

---

## §4 闹钟读取的补全算法

`__rtc_read_alarm()`（`interface.c:212`）是框架里**最复杂的单个函数**（约 170 行）。

### §4.1 问题定义

许多 RTC 硬件的闹钟寄存器只存了时、分（可能还有日）——没有月、年。驱动读回来的 `alarm->time` 里 `tm_mon`/`tm_year` 是 -1。框架需要用当前时间补全这些字段，构造一个完整的、可比较的闹钟时间。

### §4.2 竞态问题

补全依赖"当前时间"，但读闹钟和读当前时间不是原子操作。如果在两次读取之间 RTC 的分钟翻转了，补全出来的时间可能不一致。

### §4.3 完整算法（带行号）

```
__rtc_read_alarm(rtc, alarm)                   // interface.c:212
  │
  │  // 第一阶段：获取一致的"当前时间"快照
  │
  ├─ err = rtc_read_time(rtc, &before)         // interface.c:263
  │   └─ 失败 → return err
  │
  ├─ do {                                      // interface.c:266
  │   │
  │   │  // 如果不是第一次循环，把 now 复制到 before
  │   ├─ if (!first_time)
  │   │     memcpy(&before, &now, sizeof(struct rtc_time))  // interface.c:268
  │   ├─ first_time = 0
  │   │
  │   │  // 读硬件闹钟
  │   ├─ err = rtc_read_alarm_internal(rtc, alarm)  // interface.c:272
  │   │   └─ 持 ops_lock，预填 enabled=0, pending=0, 所有 time=-1
  │   │   └─ 调 ops->read_alarm(dev, alarm)
  │   ├─ if (err) return err
  │   │
  │   │  // 检查闹钟是否已经完整
  │   ├─ err = rtc_valid_tm(&alarm->time)      // interface.c:277
  │   ├─ if (!err) goto done                   // 完整 → 不需要补全
  │   │
  │   │  // 读第二次当前时间
  │   ├─ err = rtc_read_time(rtc, &now)        // interface.c:282
  │   ├─ if (err < 0) return err
  │   │
  │   │  // 一致性校验：分、时、月、年都没变？
  │   └─ } while (before.tm_min  != now.tm_min ||  // interface.c:287
  │               before.tm_hour != now.tm_hour ||
  │               before.tm_mon  != now.tm_mon ||
  │               before.tm_year != now.tm_year);
  │
  │  // 注意：tm_sec 故意不比较——秒翻转不影响补全
  │
  │  // 第二阶段：用 now（= before，因为一致性校验通过）补全缺失字段
  │
  ├─ if (alarm->time.tm_sec == -1)
  │     alarm->time.tm_sec = now.tm_sec        // interface.c:296
  ├─ if (alarm->time.tm_min == -1)
  │     alarm->time.tm_min = now.tm_min        // interface.c:298
  ├─ if (alarm->time.tm_hour == -1)
  │     alarm->time.tm_hour = now.tm_hour      // interface.c:300
  │
  ├─ if (alarm->time.tm_mday < 1 || > 31)      // interface.c:303
  │   { alarm->time.tm_mday = now.tm_mday; missing = day; }
  ├─ if (alarm->time.tm_mon >= 12)              // interface.c:307
  │   { alarm->time.tm_mon = now.tm_mon; if (missing==none) missing=month; }
  ├─ if (alarm->time.tm_year == -1)             // interface.c:312
  │   { alarm->time.tm_year = now.tm_year; if (missing==none) missing=year; }
  │
  ├─ err = rtc_valid_tm(&alarm->time)           // interface.c:321
  ├─ if (err) goto done                         // 补全后仍无效 → 放弃
  │
  │  // 第三阶段：处理时间翻转
  │
  ├─ t_now = rtc_tm_to_time64(&now)            // interface.c:326
  ├─ t_alm = rtc_tm_to_time64(&alarm->time)    // interface.c:327
  ├─ if (t_now < t_alm) goto done              // 闹钟在未来 → 不需要翻转
  │
  ├─ switch (missing) {                        // interface.c:331
  │   case day:                                // interface.c:337
  │   │  // 24 小时翻转：闹钟时间已过 → 加 1 天
  │   │  t_alm += 24 * 60 * 60;
  │   │  rtc_time64_to_tm(t_alm, &alarm->time);
  │   │  break;
  │   │
  │   case month:                              // interface.c:348
  │   │  // 月翻转：逐月推进直到日的合法
  │   │  do {
  │   │    if (tm_mon < 11) tm_mon++;
  │   │    else { tm_mon = 0; tm_year++; }
  │   │    days = rtc_month_days(tm_mon, tm_year);
  │   │  } while (days < tm_mday);
  │   │  break;
  │   │
  │   case year:                               // interface.c:363
  │   │  // 年翻转：逐年推进直到合法（处理闰年）
  │   │  do {
  │   │    tm_year++;
  │   │  } while (!is_leap_year(tm_year + 1900) &&
  │   │           rtc_valid_tm(&alarm->time) != 0);
  │   │  break;
  │   }
  │
  ├─ err = rtc_valid_tm(&alarm->time)           // interface.c:375
  └─ done:
      if (err) dev_warn("invalid alarm value")  // interface.c:379
      else rtc_add_offset(rtc, &alarm->time)    // interface.c:382
      return err
```

★★ **循环终止保证**：`do...while` 循环在分钟/小时/月/年不再变化时退出。最坏情况是跨越分钟边界——循环 2 次。跨越小时边界也是 2 次（小时翻转意味着分钟也翻转了）。月/年边界同理。不会无限循环。

★★ **`missing` 的语义**：它记录的是**第一个**被补全的"大尺度"缺失字段。如果 `tm_mday` 缺失，`missing = day`，即使 `tm_mon` 和 `tm_year` 也缺失（它们会被当前值填充但不更新 `missing`）。翻转逻辑只根据 `missing` 决定粒度——日翻转加 1 天，月翻转逐月推进，年翻转逐年推进。

---

## §5 定时器队列机制

框架用 `timerqueue_head`（红黑树，按 `expires` 排序）管理多个软件定时器。硬件闹钟寄存器永远只装队列头部的到期时间。

### §5.1 入队 `rtc_timer_enqueue()`（`interface.c:830`）

```
rtc_timer_enqueue(rtc, timer)                  // interface.c:830
  │  // 前提：已持 ops_lock
  │
  ├─ timer->enabled = 1                        // interface.c:835
  ├─ timerqueue_add(&rtc->timerqueue, &timer->node)  // interface.c:836
  │
  ├─ if (timerqueue_getnext() != timer)        // interface.c:838
  │   └─ return 0                              // 不是队列头 → 不改硬件
  │
  │  // 新 timer 是队列头 → 必须重编程硬件闹钟
  │
  ├─ pm_stay_awake(rtc->dev.parent)            // interface.c:842
  │
  ├─ if (!rtc->ops->set_alarm)                 // interface.c:845
  │   ├─ 没有硬件闹钟 → 直接调回调
  │   │  timer->func(rtc)
  │   └─ return 0
  │
  ├─ err = __rtc_set_alarm(rtc, timer)         // interface.c:853
  │   ├─ 成功 → return 0
  │   ├─ -ETIME → schedule_work(&rtc->irqwork)  // interface.c:857
  │   │          立即检查队列（可能已过期）
  │   └─ 其他错误 →
  │       timerqueue_del(...)                   // interface.c:862
  │       timer->enabled = 0                    // interface.c:863
  │       return err                            // 回滚
```

★★ **`-ETIME` 的特殊处理**：如果 `__rtc_set_alarm()` 返回 `-ETIME`（目标时间已过去），不回滚——而是 `schedule_work(&rtc->irqwork)` 让 `rtc_timer_do_work()` 立即处理。因为这个 timer 确实到期了，只是硬件来不及装。

### §5.2 出队 `rtc_timer_remove()`（`interface.c:893`）

```
rtc_timer_remove(rtc, timer)                   // interface.c:893
  │  // 前提：已持 ops_lock
  │
  ├─ if (!timer->enabled) return               // 不在队列里
  │
  ├─ was_first = (timerqueue_getnext() == timer)  // 是队列头吗？
  ├─ timer->enabled = 0
  ├─ timerqueue_del(&rtc->timerqueue, &timer->node)
  ├─ trace_rtc_timer_dequeue(timer)
  │
  ├─ if (was_first)                            // interface.c:907
  │   │  // 删的是最早的 → 必须重编程硬件
  │   ├─ next = timerqueue_getnext()
  │   ├─ if (next)
  │   │   __rtc_set_alarm(rtc, next)           // 装下一个
  │   └─ else
  │       rtc_alarm_disable(rtc)               // 队列空了 → 关硬件闹钟
```

### §5.3 到期处理 `rtc_timer_do_work()`（`interface.c:928`）

这是整个定时器队列的核心——在 workqueue 上下文执行，持 `ops_lock`：

```
rtc_timer_do_work(work)                        // interface.c:928
  │  // container_of → rtc
  │
  ├─ mutex_lock(&rtc->ops_lock)                // interface.c:934
  │
  ├─ while ((node = timerqueue_getnext(...))) { // interface.c:938
  │   ├─ timer = container_of(node, struct rtc_timer, node)
  │   │
  │   ├─ if (ktime_before(now, node->expires))
  │   │   break                                // 还有未到期的 → 停止
  │   │
  │   ├─ timerqueue_del(&rtc->timerqueue, node) // interface.c:943
  │   ├─ timer->enabled = 0
  │   ├─ trace_rtc_timer_fired(timer)
  │   ├─ timer->func(rtc)                      // interface.c:945 → 回调
  │   │   // 如 rtc_aie_update_irq() → rtc_handle_legacy_irq(1, RTC_AF)
  │   │
  │   ├─ if (timer->period != 0) {             // interface.c:947 → 周期性？
  │   │   ├─ expires = ktime_add(expires, period)
  │   │   ├─ timer->node.expires = expires
  │   │   ├─ timer->enabled = 1
  │   │   ├─ timerqueue_add(&rtc->timerqueue, node)  // 重新入队
  │   │   └─ trace_rtc_timer_enqueue(timer)
  │   │ }
  │   }
  │
  │  // 处理完所有到期的 → 重编程硬件为下一个
  │
  ├─ if (timerqueue_getnext()) {               // interface.c:954
  │   │  // 队列不空 → 设硬件闹钟为下一个到期时间
  │   ├─ next = timerqueue_getnext()
  │   ├─ if (rtc->ops->set_alarm) {
  │   │   ├─ err = __rtc_set_alarm(rtc, next)
  │   │   ├─ if (err == -ETIME) {
  │   │   │   │  // 又过期了 → 重读时间，重试
  │   │   │   └─ goto retry                    // interface.c:959
  │   │   │ }
  │   │   └─ if (err) {
  │   │       │  // 其他错误 → 重试最多 100 次
  │   │       ├─ if (++retry_count > 100) {
  │   │       │   dev_err(rtc->dev.parent, "failed to set alarm")
  │   │       │   // 不删除 timer——下次 work 再试
  │   │       └─ }
  │   │   }
  │   └─ }
  │ } else {
  │   rtc_alarm_disable(rtc)                   // 队列空 → 关硬件闹钟
  │ }
  │
  ├─ mutex_unlock(&rtc->ops_lock)              // interface.c:970
  └─ pm_relax(rtc->dev.parent)                 // interface.c:971
```

★★ **重试上限**：原代码里的重试逻辑不是 3 次，而是 **100 次**（`interface.c:963` 的 `retry_count > 100`）。如果 `__rtc_set_alarm()` 连续返回非 `-ETIME` 的错误（如 I2C 传输失败），框架会重试 100 次后放弃——但不删除 timer，下一次 `irqwork` 触发时会再试。

★★ **`-ETIME` 的 goto retry**：如果 `__rtc_set_alarm()` 返回 `-ETIME`（目标时间已过去），直接跳回循环顶部重读时间、重新处理队列。这是因为在 `schedule_work()` 到 `mutex_lock()` 之间可能又有新的 timer 到期了。

### §5.4 `__rtc_set_alarm()` 的竞态处理

`__rtc_set_alarm()`（`interface.c:410`）有两道时间校验：

```
__rtc_set_alarm(rtc, timer)                    // interface.c:410
  │
  ├─ rtc_valid_tm(&alarm->time)                // interface.c:416
  ├─ scheduled = rtc_tm_to_time64(&alarm->time) // interface.c:420
  │
  ├─ __rtc_read_time(rtc, &tm)                 // interface.c:423 → 第一次读时间
  ├─ now = rtc_tm_to_time64(&tm)
  ├─ if (scheduled <= now) return -ETIME       // interface.c:428 → 第一道
  │
  ├─ rtc_subtract_offset(rtc, &alarm->time)    // interface.c:437
  ├─ err = rtc->ops->set_alarm(dev, alarm)     // interface.c:444 → 写硬件
  │
  │  // 第二道：检查"设置动作本身耗时超过剩余时间"的竞态
  ├─ if ((scheduled - now) <= 1) {             // interface.c:460
  │   │  // 剩余时间 ≤ 1 秒 → 可能在 set_alarm 期间秒翻转了
  │   ├─ __rtc_read_time(rtc, &tm)             // interface.c:461 → 第二次读
  │   ├─ now = rtc_tm_to_time64(&tm)
  │   └─ if (scheduled <= now) return -ETIME   // interface.c:465 → 第二道
  │ }
  │
  └─ return err
```

★★ **为什么是 `<= 1`？** 如果 `scheduled - now == 1`（还剩 1 秒），在 `ops->set_alarm()` 执行期间（I2C 传输可能耗时几毫秒到几十毫秒），秒可能刚好翻转。翻转后闹钟时间就过去了，硬件可能不会产生中断。返回 `-ETIME` 让调用者知道这个闹钟"已经来不及了"。

---

## §6 中断链

### §6.1 完整路径

```
硬件闹钟触发
  └─ 驱动中断处理函数（如 pcf8563_irq()）
       ├─ 读硬件状态寄存器，确认是闹钟中断
       ├─ rtc_update_irq(rtc->rtc_dev, 1, RTC_IRQF | RTC_AF)  // interface.c:711
       │   ├─ pm_stay_awake(rtc->dev.parent)     // interface.c:718
       │   └─ schedule_work(&rtc->irqwork)       // interface.c:725
       │       └─ rtc_timer_do_work()            // interface.c:928 → §5.3
       │           ├─ 到期 timer 的回调
       │           │   ├─ rtc_aie_update_irq(rtc)  // interface.c:664
       │           │   │   └─ rtc_handle_legacy_irq(rtc, 1, RTC_AF)
       │           │   └─ rtc_uie_update_irq(rtc)  // interface.c:675
       │           │       └─ rtc_handle_legacy_irq(rtc, 1, RTC_UF)
       │           └─ rtc_handle_legacy_irq(rtc, num, mode)  // interface.c:645
       │               ├─ spin_lock_irqsave(&rtc->irq_lock, flags)
       │               ├─ rtc->irq_data = (rtc->irq_data + (num << 8)) | (RTC_IRQF | mode)
       │               ├─ spin_unlock_irqrestore(&rtc->irq_lock, flags)
       │               ├─ wake_up_interruptible(&rtc->irq_queue)
       │               └─ kill_fasync(&rtc->async_queue, SIGIO, POLL_IN)
       └─ return IRQ_HANDLED
```

用户态的 `rtc_dev_read()`（`dev.c:141`）在 `irq_queue` 上阻塞：

```
rtc_dev_read(file, buf, count, ppos)           // dev.c:141
  ├─ add_wait_queue(&rtc->irq_queue, &wait)    // dev.c:153
  ├─ do {
  │   ├─ __set_current_state(TASK_INTERRUPTIBLE)
  │   ├─ spin_lock_irq(&rtc->irq_lock)
  │   ├─ data = rtc->irq_data                  // 读走累积的中断数据
  │   ├─ rtc->irq_data = 0                     // 清零
  │   ├─ spin_unlock_irq(&rtc->irq_lock)
  │   ├─ if (data != 0) { ret = 0; break; }   // 有数据 → 退出循环
  │   ├─ if (O_NONBLOCK) { ret = -EAGAIN; break; }
  │   ├─ if (signal_pending) { ret = -ERESTARTSYS; break; }
  │   └─ schedule()                            // 阻塞等待
  │ } while (1)
  ├─ set_current_state(TASK_RUNNING)
  ├─ remove_wait_queue(...)
  └─ put_user(data, buf)                       // 返回给用户态
```

### §6.2 PIE 的特殊路径

PIE（周期中断）不走 timerqueue，用独立的 `hrtimer`：

```
rtc_pie_update_irq(hrtimer)                    // interface.c:688
  ├─ period = NSEC_PER_SEC / rtc->irq_freq     // interface.c:695
  ├─ count = hrtimer_forward_now(timer, period) // interface.c:698
  │   └─ 返回跳过的周期数（CPU 忙时可能 > 1）
  ├─ rtc_handle_legacy_irq(rtc, count, RTC_PF) // interface.c:699
  └─ return HRTIMER_RESTART                    // interface.c:700
```

`hrtimer_forward_now()` 处理了"CPU 忙导致错过几个周期"的情况——`count` 返回实际跳过的周期数，用户态的 `read()` 会看到正确的中断计数。

---

## §7 ioctl 分发

### §7.1 权限检查（`dev.c:222-240`）

| ioctl | 需要的权限 | 失败返回 |
|---|---|---|
| `RTC_SET_TIME` / `RTC_EPOCH_SET` / `RTC_PARAM_SET` | `CAP_SYS_TIME` | `-EACCES` |
| `RTC_IRQP_SET` | `arg > max_user_freq` 时需 `CAP_SYS_RESOURCE` | `-EACCES` |
| `RTC_PIE_ON` | `irq_freq > max_user_freq` 时需 `CAP_SYS_RESOURCE` | `-EACCES` |

### §7.2 操作分发（`dev.c:256-460`）

框架优先自己处理标准 ioctl，只在最后 fallback 到 `ops->ioctl()`：

| ioctl | 框架处理 | 调用的 API | 锁操作 |
|---|---|---|---|
| `RTC_ALM_READ` | 是 | `rtc_read_alarm()` | unlock → 调 API（内部加锁）→ return |
| `RTC_ALM_SET` | 是 | `rtc_set_alarm()` | unlock → 构造 alarm → 调 API → return |
| `RTC_RD_TIME` | 是 | `rtc_read_time()` | unlock → 调 API → return |
| `RTC_SET_TIME` | 是 | `rtc_set_time()` | unlock → 调 API → return |
| `RTC_WKALM_RD` | 是 | `rtc_read_alarm()` | unlock → 调 API → return |
| `RTC_WKALM_SET` | 是 | `rtc_set_alarm()` | unlock → 调 API → return |
| `RTC_PIE_ON/OFF` | 是 | `rtc_irq_set_state()` | 持锁直接调 |
| `RTC_AIE_ON/OFF` | 是 | `rtc_alarm_irq_enable()` | unlock → 调 API → return |
| `RTC_UIE_ON/OFF` | 是 | `rtc_update_irq_enable()` | unlock → 调 API → return |
| `RTC_IRQP_READ` | 是 | `put_user(rtc->irq_freq)` | 持锁直接读 |
| `RTC_IRQP_SET` | 是 | `rtc_irq_set_freq()` | 持锁直接调 |
| `RTC_PARAM_GET` | 是 | `ops->param_get` 或直接读 | 视子命令 unlock/relock |
| `RTC_PARAM_SET` | 是 | `ops->param_set` 或直接写 | 视子命令 unlock/relock |
| `RTC_VL_READ` / `RTC_VL_CLR` | 否 | `ops->ioctl()` 透传 | 持锁调 ops->ioctl |
| 其他 | 否 | `ops->ioctl()` | 持锁调 ops->ioctl |

★★ **`RTC_ALM_SET` 的 24 小时窗口**（`dev.c:280-316`）：

```c
// RTC_ALM_SET 的逻辑：
// 1. 读当前时间 → tm
// 2. 把 alarm.time 的日/月/年设成 tm 的日/月/年
// 3. 如果 alarm.time < now → 加 24 小时（翻到明天）
// 4. 调 rtc_set_alarm()
```

这意味着 `RTC_ALM_SET` 只能设**未来 24 小时内**的闹钟。要设更远的闹钟必须用 `RTC_WKALM_SET`。

---

## §8 UIE 模拟

`CONFIG_RTC_INTF_DEV_UIE_EMUL`（`dev.c:45-137`）为不支持硬件 UIE 的 RTC 提供软件模拟。

### §8.1 工作流程

```
set_uie(rtc)                                   // dev.c:108
  ├─ rtc_read_time(rtc, &tm)                   // 记录当前秒数
  ├─ rtc->uie_irq_active = 1
  ├─ rtc->stop_uie_polling = 0
  ├─ rtc->oldsecs = tm.tm_sec
  ├─ rtc->uie_task_active = 1
  └─ schedule_work(&rtc->uie_task)             // 启动轮询

rtc_uie_task(work)                             // dev.c:45
  ├─ rtc_read_time(rtc, &tm)
  ├─ spin_lock_irq(&rtc->irq_lock)
  ├─ if (stop_uie_polling || err)
  │   └─ uie_task_active = 0                   // 停止
  ├─ else if (oldsecs != tm.tm_sec)
  │   │  // 秒变了！
  │   ├─ num = (tm.tm_sec + 60 - oldsecs) % 60 // 跳过了几秒
  │   ├─ oldsecs = tm.tm_sec
  │   ├─ uie_timer.expires = jiffies + HZ - HZ/10  // 切换到 timer 路径
  │   ├─ uie_timer_active = 1
  │   ├─ uie_task_active = 0
  │   └─ add_timer(&uie_timer)                // 以后用 timer 而不是 work
  ├─ else if (schedule_work(&uie_task) == 0)
  │   └─ uie_task_active = 0                   // 已经在跑了
  └─ spin_unlock_irq
  └─ if (num) rtc_handle_legacy_irq(rtc, num, RTC_UF)  // 上报中断

rtc_uie_timer(t)                               // dev.c:73
  ├─ spin_lock_irqsave(&rtc->irq_lock)
  ├─ uie_timer_active = 0
  ├─ uie_task_active = 1
  ├─ if (schedule_work(&uie_task) == 0)
  │   └─ uie_task_active = 0
  └─ spin_unlock_irqrestore
```

★★ **两阶段策略**：
1. **初始阶段**：用 `schedule_work()` 高频轮询（workqueue 调度延迟约几毫秒）
2. **秒变化后**：切换到 `timer_list`，在 `jiffies + HZ - HZ/10`（约 0.9 秒后）唤醒——减少 CPU 占用

`clear_uie()`（`dev.c:86`）的清理逻辑比较复杂：需要等 timer 和 work 都跑完才能返回。它用 `del_timer_sync()` + `flush_work()` 确保清理完成，中间需要反复释放/重获 `irq_lock` 以避免 ABBA 死锁。

---

## §9 Tracepoint

定义：`include/trace/events/rtc.h`（206 行）。**3 个 event class + 12 个 event**：

| Event class | 实例 | 参数 |
|---|---|---|
| `rtc_time_alarm_class` | `rtc_read_time` · `rtc_set_time` · `rtc_read_alarm` · `rtc_set_alarm` | `(time64_t secs, int err)` |
| `rtc_offset_class` | `rtc_read_offset` · `rtc_set_offset` | `(long offset, int err)` |
| `rtc_timer_class` | `rtc_timer_enqueue` · `rtc_timer_dequeue` · `rtc_timer_fired` | `(struct rtc_timer *)` |

独立 event：

| Event | 参数 | 触发点 |
|---|---|---|
| `rtc_irq_set_state` | `(int enabled, int err)` | `rtc_irq_set_state()` |
| `rtc_irq_set_freq` | `(int freq, int err)` | `rtc_irq_set_freq()` |
| `rtc_alarm_irq_enable` | `(unsigned int enabled, int err)` | `rtc_alarm_irq_enable()` |

tracepoint 只在 `interface.c` 触发（`CREATE_TRACE_POINTS` 在 `interface.c:17`）。**`rtc_class_open/close` 没有 tracepoint**——要跟踪设备打开/关闭需要用 kprobe 或 ftrace 的 function tracer。

---

## §10 错误码分布

核心 3 个文件的错误码统计：

| 错误码 | `interface.c` | `dev.c` | `class.c` | 含义 |
|---|---|---|---|---|
| `-EINVAL` | 7 | 7 | 1 | 参数非法 / ops 未设 / 设备不支持 |
| `-EFAULT` | 0 | 9 | 0 | 用户态指针无效（`copy_from/to_user` 失败） |
| `-EACCES` | 0 | 3 | 0 | 权限不足（`capable()` 检查失败） |
| `-ENODEV` | 3 | 0 | 0 | `rtc->ops == NULL`（设备已注销） |
| `-ETIME` | 3 | 0 | 0 | 闹钟/定时器时间已过 |
| `-ENOTTY` | 0 | 2 | 0 | 未知 ioctl 命令（`-ENOIOCTLCMD` 转换） |
| `-ERANGE` | 1 | 0 | 1 | 超出硬件范围 |
| `-EBUSY` | 0 | 1 | 0 | 设备已被 open（`RTC_DEV_BUSY`） |
| `-EINTR` | 1 | 0 | 0 | `mutex_lock_interruptible` 被信号打断 |
| `-ENOMEM` | 0 | 0 | 0 | ★ 返回 NULL 而非 ERR_PTR |

★★ **`-ENOMEM` 的特殊性**：`rtc_allocate_device()`（`class.c:203`）在 `kzalloc` 失败时返回 **NULL**，不是 `ERR_PTR(-ENOMEM)`。调用者 `devm_rtc_allocate_device()` 用 `if (!rtc)` 检查。但 `devm_rtc_allocate_device()` 自己的失败路径返回 `ERR_PTR(-ENOMEM)`。所以驱动侧的 `IS_ERR()` 检查是对的——只是内部多了一层转换。

---

## §11 initcall 顺序

```
subsys_initcall(rtc_init)         // class.c:487
  ├─ class_create(THIS_MODULE, "rtc")  → rtc_class
  ├─ rtc_class->pm = RTC_CLASS_DEV_PM_OPS  // 挂 PM ops
  └─ rtc_dev_init()                    // dev.c:561
       └─ alloc_chrdev_region(&rtc_devt, 0, RTC_DEV_MAX, "rtc")
           // 分配 16 个次设备号（RTC_DEV_MAX = 16）

device_initcall(alarmtimer_init)  // alarmtimer.c:964
  ├─ alarmtimer_rtc_timer_init()     // 初始化 rtctimer
  ├─ 初始化 alarm_bases[]            // 设 clockid、get_ktime、get_timespec
  ├─ alarmtimer_rtc_interface_setup() // 注册 class_interface → rtc_class
  └─ platform_driver_register(&alarmtimer_driver)  // 注册 PM driver
```

★★ **时序关系**：
- `subsys_initcall`（level 4）在 `device_initcall`（level 6）之前
- RTC class 在大多数总线驱动 probe 之前就已就绪
- I2C/SPI RTC 驱动通常在 `module_init`（= `device_initcall`）里 probe
- `alarmtimer` 的 `class_interface` 注册后，每当新 RTC 注册到 `rtc_class`，`.add_dev` 被调用
- **第一个**满足条件的 RTC 被 alarmtimer 选中——之后注册的 RTC 即使更好也不会被选
