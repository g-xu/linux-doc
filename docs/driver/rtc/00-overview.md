---
title: 总览
---

# RTC 子系统 · 总览与阅读指南

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 本篇是入口：回答「RTC 子系统是什么、包含什么、怎么读这套文档」。

## §1 一句话

RTC 子系统是**框架型**子系统——它没有自己的 `bus_type`，只提供一套统一的 API 和字符设备接口，让各种 RTC 芯片驱动（I2C / SPI / platform / MFD）以一致的方式暴露给用户态和内核态。

## §2 为什么 RTC 子系统值得单独理解

大多数内核子系统要么是**总线型**（如 I2C、SPI——定义 `bus_type`，驱动挂在总线上），要么是**协议型**（如 net——定义 socket API）。RTC 两者都不是：

- **没有 `bus_type`**：RTC 驱动挂在各自的父总线（I2C / SPI / platform）上，不通过 RTC 总线枚举
- **没有自己的设备发现机制**：设备由父总线发现后，驱动通过 `devm_rtc_allocate_device()` 主动注册
- **核心是一个 `struct class`**（`rtc_class`）：所有 RTC 设备挂在这个 class 下，共享统一的字符设备接口

这意味着理解 RTC 子系统需要同时理解三件事：
1. **框架层**（`drivers/rtc/class.c` + `interface.c`）：怎么注册、怎么分发、怎么锁
2. **驱动层**（168 个 `rtc-*.c`）：怎么跟各种芯片通信
3. **消费者层**（alarmtimer、ntp、用户态）：怎么用框架提供的能力

## §3 规模

| 维度 | 数据 |
|---|---|
| 核心代码 | **8 个 `.c` + 1 个私有头 = 2987 行** |
| 公共头 | `include/linux/rtc.h`（263 行）+ `include/uapi/linux/rtc.h`（152 行） |
| 导出符号 | **核心 24 个**（16 GPL + 8 裸）；全目录 31 个（含驱动专属导出） |
| 驱动 | **168 个 `rtc-*.c`，73955 行** |
| Kconfig | 1999 行，193 个 `config` |
| DT 绑定 | 82 个文件 |
| arm64 DTS | 217 个文件提到 rtc |
| 官方文档 | `Documentation/admin-guide/rtc.rst`（141 行）——唯一的框架级文档 |

⚠️ 官方文档只面向用户/管理员，不覆盖内部机制（注册流程、锁设计、定时器队列、alarmtimer 集成）。本套文档的 02-04 篇填补了这个空白。

## §4 核心文件地图

| 文件 | 行数 | 职责 | 详见 |
|---|---|---|---|
| `drivers/rtc/class.c` | 487 | `rtc_class` 创建、设备分配/注册/注销、hctosys、class 级 PM | 01 §1, 03 §1 |
| `drivers/rtc/interface.c` | 1119 | 全部内核态 API：时间/闹钟读写、定时器队列、中断上报、偏移 | 01 §2-5, 03 §3-6 |
| `drivers/rtc/dev.c` | 568 | `/dev/rtcN` 字符设备：`read`/`poll`/`ioctl`/`fasync`、UIE 模拟 | 01 §10, 03 §7 |
| `drivers/rtc/sysfs.c` | 355 | sysfs 属性（9 个标准属性 + 驱动追加入口） | 01 §6, 07 §1 |
| `drivers/rtc/proc.c` | 97 | `/proc/driver/rtc`（只给 hctosys 设备开） | 07 §2 |
| `drivers/rtc/nvmem.c` | 30 | 把 RTC 的备份 RAM 注册成 nvmem 设备 | 04 §3 |
| `drivers/rtc/lib.c` | 217 | 纯计算：`time64_t` ↔ `struct rtc_time`、闰年、合法性校验 | 01 §7 |
| `drivers/rtc/rtc-core.h` | 43 | 私有头：Kconfig 关掉时把接口函数替换成空 inline | 04 §7 |

## §5 文档集导航

| 篇 | 回答的问题 | 适合谁 |
|---|---|---|
| **01** · 代码地图与对外 API | 「东西在哪个文件？该调哪个函数？导出状态如何？」 | 第一次接触 RTC 的开发者 |
| **02** · 数据结构 | 「这些结构体的字段都是什么意思？谁在什么时候读写？」 | 需要理解 `rtc_device` / `rtc_class_ops` 的人 |
| **03** · 内部机制 | 「注册流程怎么走？锁怎么设计的？闹钟怎么补全的？竞态在哪？」 | 需要修改框架代码的人 |
| **04** · 扩展点与驱动契约 | 「写一个 RTC 驱动需要实现什么？alarmtimer 怎么集成的？驱动代码怎么走的？」 | 写 RTC 驱动的人 |
| **05** · 使用模式 | 「谁在用 RTC API？用户态怎么操作？内核态谁在调？」 | 在别的子系统里调用 RTC 的人 |
| **06** · 设备树 | 「DT 里怎么描述 RTC？`start-year` 怎么用？arm64 平台实例？」 | 写 DTS 的人 |
| **07** · 调试与诊断 | 「RTC 不工作怎么排查？tracepoint 怎么用？常见故障模式？」 | 遇到问题需要调试的人 |
| **08** · 费曼版 | 「用大白话讲一遍」 | 任何人 |
| **99** · 取证附录 | 「这些数据怎么来的？裁掉了什么？怎么复核？」 | 审核文档准确性的人 |

**推荐阅读顺序**：

- **快速了解**：08 → 00（本篇）→ 01 §0
- **写驱动**：01 → 04 → 06
- **改框架**：01 → 02 → 03
- **排查问题**：07 → 03 §9（错误码）
- **全面理解**：01 → 02 → 03 → 04 → 05 → 06 → 07

## §6 关键概念速查

| 概念 | 一句话 | 详见 |
|---|---|---|
| `rtc_class` | 全局 `struct class`，所有 RTC 设备挂在上面 | 01 §1 |
| `rtc_class_ops` | 驱动回调表（11 个回调，所有回调在 `ops_lock` 保护下执行，除了 `proc`） | 02 §1 |
| `rtc_device` | 框架核心对象，每个 RTC 一个；含两把锁、timerqueue、features 位图 | 02 §2 |
| `ops_lock` / `irq_lock` | mutex + spinlock，各管各的；几乎不同时持有 | 03 §2 |
| timerqueue | 红黑树排序的软件定时器队列；alarmtimer 的事实专用 API | 03 §5 |
| `__rtc_read_alarm()` | 框架最复杂的函数（~170 行）：三次读取 + 一致性校验 + 字段补全 | 03 §4 |
| `alarmtimer` | 通过 `class_interface` 选中第一个满足条件的 RTC 做休眠唤醒 | 04 §2 |
| `start-year` | DT 属性，框架用来计算范围偏移，把 2-bit 年份映射到任意世纪 | 06 §4 |
| `RTC_FEATURE_*` | 8 个 feature 标志位；2 个默认置位，其余需驱动手动设 | 02 §5 |
| `set_offset_nsec` | 传输延迟补偿：默认 `NSEC_PER_SEC + 5ms`，假设"写完 1 秒后秒进位" | 02 §2.1 |

## §7 与其他子系统的关系

| 子系统 | 关系 | 详见 |
|---|---|---|
| **alarmtimer**（`kernel/time/`） | 最大消费者；用 `rtc_timer_start()` 设硬件闹钟做休眠唤醒 | 04 §2 |
| **ntp**（`kernel/time/ntp.c`） | `rtc_set_time()` 的唯一内核态调用者（`CONFIG_RTC_SYSTOHC`） | 05 §2 |
| **nvmem** | RTC 的备份 RAM 注册成 nvmem 设备 | 04 §3 |
| **PM / suspend** | class 级 PM ops 计算休眠时间；alarmtimer 的 suspend/resume | 04 §4 |
| **I2C / SPI / platform** | RTC 驱动挂在这些总线上 | 04 §5 |
| **MFD** | 某些 RTC 藏在 MFD 芯片里（如 `axp20x`、`rk808`） | 04 §5 |
| **suspend_test** | 用 `rtc_class_open()` 获取 RTC 做休眠唤醒测试 | 05 §2.3 |

## §8 数据流全景

```
                    ┌─────────────────────────────────────┐
                    │          用户态                      │
                    │  ioctl(RTC_RD_TIME) / read() / poll()│
                    └──────────────┬──────────────────────┘
                                   │ /dev/rtcN
                    ┌──────────────▼──────────────────────┐
                    │    dev.c（字符设备层）                │
                    │  ioctl 分发 → interface.c API        │
                    │  read/poll → irq_queue 阻塞          │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │    interface.c（API 层）             │
                    │  rtc_read_time / rtc_set_time        │
                    │  rtc_read_alarm / rtc_set_alarm      │
                    │  rtc_timer_do_work（workqueue）      │
                    │  rtc_update_irq（驱动→框架上报）     │
                    └──────────────┬──────────────────────┘
                                   │ ops->xxx()
                    ┌──────────────▼──────────────────────┐
                    │    驱动层（rtc-*.c）                  │
                    │  I2C regmap / SPI / 内存映射         │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │    硬件（RTC 芯片）                   │
                    │  时间寄存器 / 闹钟寄存器 / 中断线    │
                    └─────────────────────────────────────┘

    内核态消费者：
    ┌────────────────────────────────────────────────────┐
    │  alarmtimer.c  → rtc_timer_start() → 硬件闹钟     │
    │  ntp.c         → rtc_set_time()  → 驱动 set_time  │
    │  suspend_test  → rtc_class_open() + rtc_set_alarm()│
    └────────────────────────────────────────────────────┘
```

## §9 官方文档

RTC 框架的官方文档只有一篇：`Documentation/admin-guide/rtc.rst`（141 行）。它面向用户和系统管理员，介绍：

- 老式 `/dev/rtc`（PC/AT 兼容）与新式 `/dev/rtcN`（RTC Class）的区别
- ioctl 接口的基本用法
- 中断频率限制（64Hz 默认上限）

⚠️ 框架内部机制（注册流程、锁设计、定时器队列、alarmtimer 集成）在官方文档里**完全没有覆盖**。没有 `Documentation/driver-api/rtc*` 或 `Documentation/rtc/` 目录——驱动开发者只能读源码和头文件。
