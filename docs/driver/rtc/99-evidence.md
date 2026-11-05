---
title: 取证附录
---

# RTC 子系统 · 取证附录

> 本篇记录文档集的取证过程：做了什么、裁了什么、为什么裁、怎么复核。
> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。

## §1 子系统类型判定

### §1.1 门 1：bus_type 扫描

```bash
grep -rn 'struct bus_type.*rtc\|bus_register.*rtc' drivers/rtc/ include/linux/rtc.h
```

**结果：0 处命中。** RTC 不定义自己的 `bus_type`。

### §1.2 门 2：uapi 扫描

```bash
ls include/uapi/linux/rtc*
```

**结果：1 个文件**——`include/uapi/linux/rtc.h`（152 行）。定义了 ioctl 编号、`struct rtc_time`、`struct rtc_wkalrm`、`struct rtc_param`、feature 标志。

### §1.3 类型判定

**框架型**。理由：

- 没有 `bus_type`（门 1 零命中）
- 有用户态 ABI（门 2 命中 uapi 头 + ioctl + cdev + sysfs + procfs）
- 驱动挂在父总线（I2C / SPI / platform）上，通过 API 注册进 `rtc_class`

---

## §2 裁掉的内容

### §2.1 驱动目录

`drivers/rtc/rtc-*.c`（168 个，73955 行）全部按"单芯片驱动"处理，不在核心文档里逐个分析。04 篇挑了 2 个 arm64 代表做完整代码流分析（pcf8563 代表 I2C 类，armada38x 代表 platform 类）。

### §2.2 ACPI 路径

本套文档 arm64-only。RTC 子系统里没有纯 ACPI 的代码路径（不像某些子系统有 `acpi-*.c`），所以不需要显式裁掉 ACPI 文件。但以下行为值得记录：

- `Documentation/admin-guide/rtc.rst` 里关于老式 `/dev/rtc`（PC/AT 兼容）的章节对 arm64 无意义——本套文档不展开
- `drivers/rtc/rtc-cmos.c` 是 PC/AT CMOS RTC 驱动，arm64 不用，04 篇不选为代表

### §2.3 x86 特定代码

- `arch/x86/` 下的 RTC 相关代码不在本套范围
- `drivers/platform/x86/amd/pmc.c` 调用了 `rtc_read_time()` / `rtc_class_open()`，但这是 x86 平台代码，05 篇只列了名字不展开

### §2.4 MC146818 库

`drivers/rtc/rtc-mc146818-lib.c` 是 MC146818 芯片的通用库（4 个 `EXPORT_SYMBOL_GPL`），被多个 MC146818 兼容驱动使用。本套文档不单独分析这个库——它属于驱动级代码，不是框架核心。导出符号清单在 99-evidence §4.2 里列出。

---

## §3 官方文档阅读记录

| 文档 | 行数 | 是否读完 | 备注 |
|---|---|---|---|
| `Documentation/admin-guide/rtc.rst` | 141 | **是** | 唯一的框架级官方文档；面向用户/管理员，不覆盖内部机制 |

⚠️ RTC 框架**没有** `Documentation/driver-api/rtc*` 或 `Documentation/rtc/` 目录。开发者写 RTC 驱动时没有官方 howto 可参考——只能读源码和头文件。这是本套文档 01-04 篇的主要价值所在。

---

## §4 导出符号清单

### §4.1 核心文件导出（24 个）

通过 `grep 'EXPORT_SYMBOL' drivers/rtc/class.c drivers/rtc/interface.c drivers/rtc/dev.c drivers/rtc/sysfs.c drivers/rtc/nvmem.c drivers/rtc/lib.c` 逐文件统计：

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

### §4.2 驱动文件额外导出（7 个）

通过 `grep 'EXPORT_SYMBOL' drivers/rtc/rtc-*.c` 统计：

| 文件 | 符号 | 宏 | 说明 |
|---|---|---|---|
| `rtc-mc146818-lib.c:88` | `mc146818_avoid_UIP` | `EXPORT_SYMBOL_GPL` | MC146818 库 |
| `rtc-mc146818-lib.c:98` | `mc146818_does_rtc_work` | `EXPORT_SYMBOL_GPL` | 同上 |
| `rtc-mc146818-lib.c:198` | `mc146818_get_time` | `EXPORT_SYMBOL_GPL` | 同上 |
| `rtc-mc146818-lib.c:311` | `mc146818_set_time` | `EXPORT_SYMBOL_GPL` | 同上 |
| `rtc-sa1100.c:240` | `sa1100_rtc_init` | `EXPORT_SYMBOL_GPL` | SA1100 平台 |
| `rtc-ds1685.c:1437` | `ds1685_rtc_poweroff` | `EXPORT_SYMBOL_GPL` | DS1685 关机 |
| `rtc-omap.c:466` | `omap_rtc_power_off_program` | `EXPORT_SYMBOL`（裸） | OMAP 关机 |

全目录合计 **31 个**导出符号。

---

## §5 消费者调用验证

### §5.1 `rtc_class_open()` 外部调用者

```bash
grep -rn 'rtc_class_open(' --include='*.c' | grep -v 'drivers/rtc/'
```

| 调用者 | 文件:行 | arm64 相关？ |
|---|---|---|
| ntp systohc | `kernel/time/ntp.c:587` | **是** |
| suspend_test | `kernel/power/suspend_test.c:206` | **是** |
| AMD PMC | `drivers/platform/x86/amd/pmc.c:713` | 否 |
| TI PM | `drivers/soc/ti/pm33xx.c:458` | **是** |

### §5.2 `rtc_read_time()` 外部调用者

```bash
grep -rn 'rtc_read_time(' --include='*.c' | grep -v 'drivers/rtc/' | grep -v 'static int.*_read_time'
```

| 调用者 | 文件:行 | arm64 相关？ |
|---|---|---|
| alarmtimer | `kernel/time/alarmtimer.c:292` | **是** |
| suspend_test | `kernel/power/suspend_test.c:79` | **是** |
| AMD PMC | `drivers/platform/x86/amd/pmc.c:723` | 否 |

### §5.3 `rtc_set_time()` 外部调用者

```bash
grep -rn 'rtc_set_time(' --include='*.c' | grep -v 'drivers/rtc/' | grep -v 'static int.*_set_time'
```

| 调用者 | 文件:行 | arm64 相关？ |
|---|---|---|
| ntp systohc | `kernel/time/ntp.c:597` | **是** |

`rtc_set_time()` 在框架外只有 ntp.c 一个调用者。`dev.c:337` 的 ioctl 路径是框架内部调用。

---

## §6 错误码复核方法

```bash
# 逐文件统计
for f in drivers/rtc/class.c drivers/rtc/interface.c drivers/rtc/dev.c; do
    echo "=== $f ==="
    grep -oE 'return -[A-Z]+|err = -[A-Z]+' $f | sort | uniq -c | sort -rn
done
```

⚠️ 这个方法有假阳性风险：`-ERANGE` 可能出现在注释里，`-ENODEV` 可能出现在字符串里。03 篇 §9 的数字是粗 grep 结果，精确数字需要逐行复核。

---

## §7 initcall 复核

```bash
grep -n 'initcall\|module_init\|subsys_initcall' drivers/rtc/class.c drivers/rtc/dev.c drivers/rtc/proc.c drivers/rtc/sysfs.c
```

唯一命中：`drivers/rtc/class.c:487` 的 `subsys_initcall(rtc_init)`。其余 interface 文件没有独立的 initcall——它们的功能通过 `class.c` 的 initcall 间接初始化。

alarmtimer 的 initcall：`kernel/time/alarmtimer.c:964` 的 `device_initcall(alarmtimer_init)`，在 RTC 的 `subsys_initcall` 之后执行。

---

## §8 深度分析验证

### §8.1 `__rtc_read_alarm()` 复杂度

```bash
awk '/^int __rtc_read_alarm/,/^}/' drivers/rtc/interface.c | wc -l
```

约 170 行。是框架里最长的单个函数。包含：三次读取循环、一致性校验、字段补全、时间计算。

### §8.2 `rtc_timer_do_work()` 重试上限

```bash
grep -n 'retry\|RETRY\|100' drivers/rtc/interface.c
```

`rtc_timer_do_work()` 在 `__rtc_set_alarm()` 返回 `-ETIME` 时重试，上限 100 次（`drivers/rtc/interface.c:965-971`）。超过 100 次放弃并打印错误。

### §8.3 `__rtc_set_alarm()` 双重 -ETIME 检查

```bash
grep -n 'ETIME' drivers/rtc/interface.c
```

两处 `-ETIME` 返回：
1. `interface.c:497`：下发前检查 `scheduled < now`
2. `interface.c:505-508`：下发后重读时间，检查 `scheduled - now <= 1`

### §8.4 alarmtimer 选中条件

```bash
grep -A 20 'alarmtimer_rtc_add_device' kernel/time/alarmtimer.c | head -30
```

4 个条件：`!rtcdev`、`RTC_FEATURE_ALARM`、`device_may_wakeup()`、`try_module_get()`。

---

## §9 跨篇引用登记

| 源篇 | 引用目标 | 内容 |
|---|---|---|
| 00 §4 | 01-08, 99 | 阅读指南导航 |
| 00 §6 | 02, 03, 04, 06 | 关键概念速查 |
| 00 §7 | 04, 05 | 子系统关系 |
| 01 §0 | 03 | 代码地图引用 internals |
| 01 §2.1 | 05 | rtc_read_time 调用者分析 |
| 01 §9 | 99 | 导出符号交叉引用 |
| 02 §1 | 01 | 回调矩阵引用 API |
| 02 §2 | 03 | 锁设计引用 internals |
| 02 §5 | 01 | feature 标志引用 API |
| 03 §1 | 02 | 注册流程引用数据结构 |
| 03 §4 | 04 | 闹钟补全引用驱动契约 |
| 03 §5 | 02 | timerqueue 引用 rtc_timer |
| 04 §2 | 03, 05 | alarmtimer 引用中断链和使用模式 |
| 04 §5 | 05 | 驱动分析引用 probe 模板 |
| 05 §2 | 04 | 内核消费者引用 alarmtimer |
| 06 §4 | 03 | start-year 引用范围映射 |
| 06 §6 | 04 | wakeup-source 引用 alarmtimer |
| 07 §3 | 03 | tracepoint 引用内部机制 |
| 07 §6 | 04, 06 | 调试场景引用驱动契约和 DT |
| 08 全篇 | 01-07 | 费曼版简化引用各篇 |

---

## §10 已知限制

1. **行号未逐条复核**：本套文档的行号来自基线收集阶段的 grep 结果，没有在每篇写完后逐条验证。如果源码有改动，行号可能偏移。
2. **驱动分析非穷举**：168 个驱动只挑了 2 个代表做完整代码流分析（pcf8563 + armada38x），其余驱动的实现差异未覆盖。
3. **错误码统计为粗 grep**：03 §9 的数字来自 `grep -oE 'return -[A-Z]+'`，可能包含注释和字符串里的假阳性。
4. **tracepoint 数量**：12 个 event 的统计来自 `grep 'DEFINE_EVENT\|TRACE_EVENT'`，如果后续版本新增 event 需要重新统计。
5. **alarmtimer 分析聚焦 RTC 交互**：alarmtimer 是 `kernel/time/` 的子系统，本套只分析了它与 RTC 的交互点（class_interface 注册、suspend/resume 路径），没有深入其内部的 `alarm_base` / `timerqueue` 机制。
6. **消费者分析仅限 arm64 相关**：x86 特定的消费者（如 `drivers/platform/x86/amd/pmc.c`）只列了名字不展开。
7. **UIE 模拟分析基于代码阅读**：没有在实际硬件上验证两阶段切换的行为。
8. **范围映射的数学验证**：§4.2 的映射示例基于 `mktime64()` 的计算结果，没有用 `lib_test.c` 的 KUnit 测试交叉验证。
