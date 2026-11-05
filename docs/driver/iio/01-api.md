---
title: API 参考
---

# IIO 代码地图与对外 API

基线 `f6e38ae624cf`（Linux 6.1.158），arm64-only。本篇是「找文件」和「找 API」这两类查询的落点：想知道某个功能的代码住在哪个文件、某个函数叫什么名字、返回什么、在哪个上下文里能调，都在这里。

**本篇的格式约定：**

- API 表格固定三列 `API / 说明 / 备注`。「说明」一句话讲它做什么，其余（返回值陷阱、上下文限制、是宏还是 inline、参数语义）全部进「备注」
- 签名只写「函数名 + 形参名」，不带类型。类型信息进「备注」
- **位置只精确到文件，不带行号。** 每节开头一行交代「头文件 / 实现文件」。需要行号的因果链在 `03-internals.md`，需要复现命令的统计在 `99-evidence.md`
- 「可用」的判据是 `EXPORT_SYMBOL*` **且**头文件里有声明。有 kerneldoc 但 `static` 的内部函数不算——本篇 §10 专门列这类容易误认的符号

## §0 代码地图

### §0.1 本树基线与 IIO 的补丁状况

本树在 6.1.158 之上带了一批 PREEMPT_RT 回合并补丁（涉及 arm/arm64/powerpc/x86 的 `thread_info`、i915、以及 `drivers/net/ethernet/` 下 47 个文件的 `u64_stats` 改名）。

⚠️ **IIO 子系统本身一行未改。** 复核命令：

```bash
git status --porcelain -- drivers/iio include/linux/iio include/uapi/linux/iio \
    Documentation/ABI/testing/sysfs-bus-iio Documentation/driver-api/iio
```

输出为空。所以本篇所有 API 都是**上游 6.1.158 的原样**，不存在「本树独有」的签名或行为差异——这一点和 `output/网卡驱动/01-api.md` §0.6 那种「本树删了 `_irq` 变体」的情况相反，移植上游 IIO 驱动进本树不会因为核心 API 改名而编译失败。

唯一与 PREEMPT_RT 沾边的 IIO 代码是 `drivers/iio/trigger/iio-trig-hrtimer.c` 里的 `HRTIMER_MODE_REL_HARD`，那是上游就有的写法，不是本树补丁（详见 `04-extensions.md` 触发器提供方一节）。

### §0.2 drivers/iio/ 目录地图

全目录 **597 个文件 / 283154 行**。按「读全」与「只读契约」分成三层：

**第一层：核心（`drivers/iio/*.c`，11 个文件，全部逐行读过）**

| 文件 | 职责 | 备注 |
|---|---|---|
| `industrialio-core.c` | 设备注册/注销、sysfs 属性自动合成、值格式化、`iio_bus_type`、字符设备与 ioctl 分发 | 2153 行，最大的单文件 |
| `industrialio-buffer.c` | 缓冲区通用层：`iio_buffer` 生命周期、scan mask、demux、`buffer%d` sysfs 组、读写与 poll | 1999 行 |
| `inkern.c` | 内核内消费者层：`iio_channel` 查找、DT/board-file 映射、raw↔processed 转换 | 1037 行，导出 28 个符号 |
| `industrialio-trigger.c` | 触发器注册、虚拟 irq_chip、pollfunc 分配、`current_trigger` sysfs | 750 行 |
| `industrialio-event.c` | 事件 kfifo、事件 fd、`events/` sysfs 组 | 614 行 |
| `industrialio-sw-trigger.c` | configfs 软件触发器类型注册与实例化 | 183 行 |
| `industrialio-sw-device.c` | configfs 软件设备类型注册与实例化 | 179 行，与上一个近乎孪生 |
| `iio_core.h` | 核心内部头，不对外 | 111 行 |
| `industrialio-triggered-event.c` | 触发式事件的 pollfunc 辅助 | 64 行 |
| `iio_core_trigger.h` | 触发器相关的核心内部头 | 59 行 |
| `industrialio-configfs.c` | `/config/iio/` 这个 configfs 子系统的根 | 48 行 |

**第二层：子框架（`drivers/iio/buffer/`、`drivers/iio/trigger/`，逐行读过）**

| 文件 | 职责 | 行数 |
|---|---|---|
| `buffer/kfifo_buf.c` | 默认的软件 kfifo 缓冲实现 | 290 |
| `buffer/industrialio-buffer-dma.c` | DMA 缓冲通用层 | 682 |
| `buffer/industrialio-buffer-dmaengine.c` | 把上者接到 dmaengine 上 | 297 |
| `buffer/industrialio-hw-consumer.c` | 硬件直连消费者（数据不经 CPU） | 213 |
| `buffer/industrialio-buffer-cb.c` | 回调缓冲：把样本推给内核内消费者 | 156 |
| `buffer/industrialio-triggered-buffer.c` | 「触发器 + kfifo」一键装配辅助 | 139 |
| `trigger/stm32-timer-trigger.c` | STM32 定时器触发器 | 918 |
| `trigger/iio-trig-sysfs.c` | 用户态手动触发（`trigger_now`） | 225 |
| `trigger/iio-trig-hrtimer.c` | 高精度定时器触发器 | 200 |
| `trigger/iio-trig-loop.c` | 内核线程紧循环触发器 | 141 |
| `trigger/stm32-lptimer-trigger.c` | STM32 低功耗定时器触发器 | 118 |
| `trigger/iio-trig-interrupt.c` | 外部中断触发器（board-file only） | 111 |

**第三层：设备类目录（只讲契约，不逐行读）**

`accel/ adc/ addac/ afe/ amplifiers/ cdc/ chemical/ common/ dac/ dummy/ filter/ frequency/ gyro/ health/ humidity/ imu/ light/ magnetometer/ multiplexer/ orientation/ position/ potentiometer/ potentiostat/ pressure/ proximity/ resolver/ temperature/ test/`——27 个目录，占了 597 个文件里的绝大多数。选了哪几个当代表驱动、按什么标准选，登记在 `99-evidence.md` §2.2。

两个目录是例外，它们不是「设备驱动」而是「通道变换框架」，在 `04-extensions.md` 里单独成节：`multiplexer/iio-mux.c`（461 行）、`afe/iio-rescale.c`（611 行）。

### §0.3 头文件地图

`include/linux/iio/` 共 43 个头文件。按「谁该读它」分三组：

**核心契约（写任何 IIO 驱动都要读）**

| 头文件 | 内容 | 行数 |
|---|---|---|
| `iio.h` | `iio_dev`、`iio_chan_spec`、`iio_info`、六种模式、设备生命周期 API | 792 |
| `types.h` | `iio_chan_type`、`iio_modifier` 等枚举的内核侧别名 | 71 |
| `sysfs.h` | `IIO_DEVICE_ATTR` 一族宏、`iio_dev_attr`、`iio_enum` | 152 |
| `iio-opaque.h` | `iio_dev_opaque`——公开结构的私有外壳，**核心内部用** | 80 |

**子框架契约（用到对应功能才读）**

| 头文件 | 内容 | 行数 |
|---|---|---|
| `consumer.h` | 内核内消费者全部 API（`iio_channel_get` 一族） | 427 |
| `trigger.h` | `iio_trigger`、`iio_trigger_ops`、触发器提供方 API | 187 |
| `buffer_impl.h` | `iio_buffer`、`iio_buffer_access_funcs`——**写缓冲实现**才需要 | 168 |
| `buffer-dma.h` | DMA 缓冲层契约 | 146 |
| `sw_trigger.h` / `sw_device.h` | configfs 软件对象的两套孪生契约 | 67 / 67 |
| `buffer.h` | 驱动侧推数据的三个入口 | 58 |
| `events.h` | 事件码打包宏 | 56 |
| `trigger_consumer.h` | `iio_poll_func`、pollfunc 装配 | 53 |
| `driver.h` | `iio_map` 数组注册（board file 路径） | 43 |
| `triggered_buffer.h` | 触发式缓冲一键装配 | 38 |
| `machine.h` | `iio_map` 结构本体 | 35 |
| `afe/rescale.h` | rescale 框架契约 | 36 |
| `kfifo_buf.h` | kfifo 缓冲入口 | 21 |
| `hw-consumer.h` | 硬件直连消费者入口 | 20 |
| `buffer-dmaengine.h` | dmaengine 缓冲入口 | 17 |
| `configfs.h` | `iio_configfs_subsys` 的唯一声明 | 12 |
| `triggered_event.h` | 触发式事件入口 | 12 |

**具体驱动/厂商私有（跟核心无关，别误当框架）**

`accel/kxcjk_1013.h`、`adc/ad_sigma_delta.h`、`adc/adi-axi-adc.h`、`adc/qcom-vadc-common.h`、`adc/stm32-dfsdm-adc.h`、`common/cros_ec_sensors_core.h`、`common/ssp_sensors.h`、`common/st_sensors*.h`（3 个）、`dac/*.h`（5 个）、`frequency/*.h`（2 个）、`gyro/itg3200.h`、`imu/adis.h`、`timer/stm32-*.h`（2 个）——共 20 个。⚠️ 其中 `imu/adis.h`（572 行）和 `common/st_sensors.h`（340 行）比很多核心头都大，但它们只服务一个厂商系列。

**uapi（用户态 ABI，改一个字节就是 ABI 破坏）**

| 头文件 | 内容 | 行数 |
|---|---|---|
| `include/uapi/linux/iio/types.h` | 通道类型/修饰符/事件类型/事件方向四组枚举 | 126 |
| `include/uapi/linux/iio/events.h` | `iio_event_data` 与事件码解包宏 | 43 |
| `include/uapi/linux/iio/buffer.h` | `IIO_BUFFER_GET_FD_IOCTL` | 10 |

### §0.4 Kconfig 与编译产物

顶层 `drivers/iio/Kconfig` 定义 9 个符号：

| 符号 | 类型 | 说明 |
|---|---|---|
| `IIO` | tristate（menuconfig） | 整个子系统的总开关 |
| `IIO_BUFFER` | bool | 缓冲支持。关掉则 `industrialio-buffer.o` 不编入 |
| `IIO_CONFIGFS` | tristate | `/config/iio/`，`select CONFIGFS_FS` |
| `IIO_TRIGGER` | bool | 触发器支持 |
| `IIO_CONSUMERS_PER_TRIGGER` | int，默认 2 | ⚠️ **一个触发器能带几个消费者的硬上限**，编译期定死。超了 `iio_trigger_attach_poll_func` 失败 |
| `IIO_SW_DEVICE` | tristate | configfs 软件设备，`select IIO_CONFIGFS` |
| `IIO_SW_TRIGGER` | tristate | configfs 软件触发器，`select IIO_CONFIGFS` |
| `IIO_TRIGGERED_EVENT` | tristate | 触发式事件辅助，`select IIO_TRIGGER` |

⚠️ **`IIO_BUFFER` 和 `IIO_TRIGGER` 是 bool 而不是 tristate**，因为它们编进 `industrialio.ko` 这一个模块里，不能单独当模块。而 `IIO_CONFIGFS` / `IIO_SW_*` / `IIO_TRIGGERED_EVENT` 是独立的 `.ko`。

模块组装关系（`drivers/iio/Makefile`）：

```
industrialio.ko = industrialio-core.o + industrialio-event.o + inkern.o
                  [+ industrialio-buffer.o  if IIO_BUFFER]
                  [+ industrialio-trigger.o if IIO_TRIGGER]

industrialio-configfs.ko         if IIO_CONFIGFS
industrialio-sw-device.ko        if IIO_SW_DEVICE
industrialio-sw-trigger.ko       if IIO_SW_TRIGGER
industrialio-triggered-event.ko  if IIO_TRIGGERED_EVENT
```

⚠️ **`industrialio-event.o` 和 `inkern.o` 是无条件编入的**，没有对应的 Kconfig 开关。所以「事件」和「内核内消费者」这两套功能只要开了 `IIO` 就一定在，不存在裁掉它们的配置。

`drivers/iio/buffer/Kconfig` 6 个符号：`IIO_BUFFER_CB`、`IIO_BUFFER_DMA`、`IIO_BUFFER_DMAENGINE`（`select IIO_BUFFER_DMA`）、`IIO_BUFFER_HW_CONSUMER`、`IIO_KFIFO_BUF`、`IIO_TRIGGERED_BUFFER`（`select IIO_TRIGGER` + `select IIO_KFIFO_BUF`）。

`drivers/iio/trigger/Kconfig` 6 个符号：`IIO_HRTIMER_TRIGGER`、`IIO_INTERRUPT_TRIGGER`、`IIO_STM32_LPTIMER_TRIGGER`、`IIO_STM32_TIMER_TRIGGER`、`IIO_TIGHTLOOP_TRIGGER`、`IIO_SYSFS_TRIGGER`。

⚠️ **`trigger/Kconfig` 整个被 `if IIO_TRIGGER` 包住**（顶层 Kconfig 的 source 处），而 `buffer/Kconfig` 被 `if IIO_BUFFER` 包住。所以关掉 `IIO_TRIGGER` 之后这六个触发器驱动在 menuconfig 里直接消失，不是「显示为不可选」。

### §0.5 用户可见入口

| 入口 | 位置 | 由谁生成 |
|---|---|---|
| `/sys/bus/iio/devices/iio:deviceN/` | sysfs | `industrialio-core.c` 自动按 `iio_chan_spec` 合成属性名 |
| `/sys/bus/iio/devices/triggerN/` | sysfs | `industrialio-trigger.c` |
| `/dev/iio:deviceN` | 字符设备 | `industrialio-core.c` |
| `/config/iio/triggers/<type>/<name>` | configfs | `industrialio-sw-trigger.c` |
| `/config/iio/devices/<type>/<name>` | configfs | `industrialio-sw-device.c` |
| `/sys/kernel/debug/iio/iio:deviceN/` | debugfs | `industrialio-core.c`，需 `CONFIG_DEBUG_FS` |

官方 ABI 文档：`Documentation/ABI/testing/sysfs-bus-iio`（2164 行，**775 个 `What:` 行但只有 149 个描述块**——多个属性名共享一段描述，这两个数不能混用）、`Documentation/ABI/obsolete/sysfs-bus-iio`（186 行，legacy `buffer/` 与 `scan_elements/` 路径）。已发现的文档过时之处集中记在 `99-evidence.md`。

### §0.6 按任务找 API

| 想做什么 | 去本篇哪节 | 关键入口 |
|---|---|---|
| 写一个最简单的传感器驱动 | §1 + §2 | `devm_iio_device_alloc` → `devm_iio_device_register` |
| 给驱动加缓冲采集 | §4 | `devm_iio_triggered_buffer_setup` |
| 写一个触发器提供方 | §3.1 | `iio_trigger_alloc` → `iio_trigger_register` |
| 让驱动能被触发器驱动 | §3.2 | `iio_triggered_buffer_setup` 一次搞定 |
| 从内核里读另一个驱动的 ADC 通道 | §6 | `devm_iio_channel_get` → `iio_read_channel_processed` |
| 上报一个阈值事件 | §5 | `iio_push_event` |
| 加一个 configfs 可创建的软件对象 | §7 | `iio_register_sw_trigger_type` |
| 格式化一个定点数值给 sysfs | §2 | `iio_format_value` |
| 查某个错误码是谁返回的 | §9 | 按语义分组的错误码表 |
| 确认某个符号能不能用 | §10 | 「看着像 API 但不是」清单 |

## §1 设备生命周期 API

头文件：`include/linux/iio/iio.h`；实现：`drivers/iio/industrialio-core.c`。

这是写任何 IIO 驱动都要走的一条线：分配 → 填 `iio_dev` 字段 → 注册 → （用）→ 注销 → 释放。

| API | 说明 | 备注 |
|---|---|---|
| `iio_device_alloc(parent, sizeof_priv)` | 分配一个 `iio_dev`，附带 `sizeof_priv` 字节的驱动私有区 | 返回 `struct iio_dev *`，失败返回 NULL（不是 ERR_PTR）。`parent` 同时被设为 `dev.parent`。私有区保证 cacheline 对齐 |
| `devm_iio_device_alloc(parent, sizeof_priv)` | 同上，devres 托管 | 绝大多数驱动用这个 |
| `iio_device_free(indio_dev)` | 释放 `iio_device_alloc` 的结果 | 只在**未注册**或已注销后调 |
| `iio_priv(indio_dev)` | 取私有区指针 | `static inline`，直接返回 `indio_dev->priv`。⚠️ 不做 NULL 检查 |
| `iio_device_register(indio_dev)` | 注册设备，创建 sysfs 属性、字符设备、debugfs | **是宏**，展开成 `__iio_device_register(indio_dev, THIS_MODULE)`。返回 0 或负错误码 |
| `devm_iio_device_register(dev, indio_dev)` | 同上，devres 托管注销 | **是宏**，展开成 `__devm_iio_device_register(dev, indio_dev, THIS_MODULE)` |
| `iio_device_unregister(indio_dev)` | 注销 | void。会等待并断开所有缓冲区消费者 |
| `iio_device_get(indio_dev)` | 引用计数 +1 | `static inline`，包 `get_device()`。返回传入的指针；传 NULL 返回 NULL |
| `iio_device_put(indio_dev)` | 引用计数 −1 | `static inline`，包 `put_device()`。传 NULL 是空操作 |
| `dev_to_iio_dev(dev)` | 从 `struct device *` 反查 `iio_dev` | `static inline`，`container_of`。⚠️ kerneldoc 明说「设备必须是 IIO 设备，否则结果未定义」——不做类型检查 |
| `iio_device_set_parent(indio_dev, parent)` | 改父设备 | `static inline`。⚠️ **只能在 alloc 之后、register 之前调**，kerneldoc 明确了这个窗口 |
| `iio_device_set_drvdata(indio_dev, data)` | 挂任意指针 | `static inline`，包 `dev_set_drvdata()` |
| `iio_device_get_drvdata(indio_dev)` | 取回上面挂的指针 | `static inline` |
| `iio_device_id(indio_dev)` | 取设备编号 N（即 `iio:deviceN` 的 N） | 返回 int。常用于给 pollfunc 命名 |
| `iio_device_get_current_mode(indio_dev)` | 取当前运行模式 | 返回 int（六种 `INDIO_*` 之一）。⚠️ **`currentmode` 字段在 `iio_dev_opaque` 里，只能通过这个函数读** |
| `iio_buffer_enabled(indio_dev)` | 当前是否处于任一缓冲模式 | 返回 bool |
| `iio_device_claim_direct_mode(indio_dev)` | 抢占「直接模式」，成功后缓冲区不能被启用 | 返回 0 成功 / `-EBUSY`。⚠️ **成功返回时 `mlock` 是持有状态**，必须配对调用 release，中间不能睡眠太久 |
| `iio_device_release_direct_mode(indio_dev)` | 释放上者 | void |
| `iio_device_claim_buffer_mode(indio_dev)` | 反向：抢占「缓冲模式」，成功后不能切回直接模式 | 返回 0 / `-EBUSY`。同样持锁返回 |
| `iio_device_release_buffer_mode(indio_dev)` | 释放上者 | void |
| `iio_device_set_clock(indio_dev, clock_id)` | 设置时间戳时钟源 | 返回 0 或负错误码。对应 sysfs 的 `current_timestamp_clock` |
| `iio_device_get_clock(indio_dev)` | 读时间戳时钟源 | 返回 `clockid_t` |
| `iio_get_time_ns(indio_dev)` | 按该设备配置的时钟源取当前时间戳 | 返回 `s64`。⚠️ **驱动打时间戳一律用这个，不要直接用 `ktime_get_ns()`**，否则用户设的 `current_timestamp_clock` 失效 |
| `iio_get_debugfs_dentry(indio_dev)` | 取本设备的 debugfs 目录 | 无 `CONFIG_DEBUG_FS` 时是返回 NULL 的 `static inline` 桩 |

⚠️ **`iio_device_register` / `devm_iio_device_register` 都是宏而不是函数。** 真正的符号是带 `__` 前缀、多一个 `struct module *` 参数的版本。这一点对两件事有影响：(1) 在 `drivers/iio/` 里 grep `iio_device_register(` 会同时命中宏定义和调用点；(2) 代码索引工具看不到这层展开——`99-evidence.md` 里把它登记为索引盲区实例。

## §2 属性合成与值格式化 API

头文件：`include/linux/iio/iio.h`、`include/linux/iio/sysfs.h`；实现：`drivers/iio/industrialio-core.c`。

IIO 的 sysfs 属性名**不是驱动写死的字符串**，而是核心按 `iio_chan_spec` 里的 `type` / `channel` / `modified` / `differential` / 四层 info mask 自动合成的（合成规则见 `03-internals.md`）。驱动只在需要额外属性时才手工声明。

| API | 说明 | 备注 |
|---|---|---|
| `iio_format_value(buf, type, size, vals)` | 把 `int` 数组按 `IIO_VAL_*` 格式化成 sysfs 字符串 | 返回 `ssize_t`（写入字节数）或负错误码。`type` 是 `IIO_VAL_INT` / `IIO_VAL_INT_PLUS_MICRO` / `IIO_VAL_FRACTIONAL` 等 |
| `iio_str_to_fixpoint(str, fract_mult, integer, fract)` | 反向：解析 `"1.234"` 这类定点字符串 | 返回 0 或负错误码。`fract_mult` 决定小数位权重（如 100000 对应六位） |
| `iio_read_const_attr(dev, attr, buf)` | 直接把 `iio_const_attr` 里的常量字符串吐出去 | 声明在 `sysfs.h`。给 `IIO_CONST_ATTR` 宏用 |
| `iio_enum_read(dev, attr, buf)` | 读一个枚举型属性的当前值（字符串形式） | 与 `iio_enum` 结构配合，走 `IIO_ENUM` 宏 |
| `iio_enum_write(dev, attr, buf, len)` | 写枚举型属性 | 输入不在枚举表里返回 `-EINVAL` |
| `iio_enum_available_read(dev, attr, buf)` | 输出该枚举的全部可选值 | 走 `IIO_ENUM_AVAILABLE` 宏 |
| `iio_show_mount_matrix(dev, attr, buf)` | 输出 3×3 安装矩阵 | 对应 sysfs 的 `mount_matrix` |
| `iio_read_mount_matrix(dev, matrix)` | 从固件（DT `mount-matrix` 属性）读安装矩阵 | 返回 0 或负错误码。属性缺失时填单位矩阵 |
| `iio_channel_has_info(chan, type)` | 判断通道有没有某个 info 项 | `static inline`，返回 bool。同时查四层 mask |
| `iio_channel_has_available(chan, type)` | 判断通道有没有某个 info 项的 `_available` 变体 | `static inline`，返回 bool |

`sysfs.h` 里另有一族**宏**用来声明属性：`IIO_DEVICE_ATTR`、`IIO_DEVICE_ATTR_RO/RW/WO`、`IIO_CONST_ATTR`、`IIO_ENUM`、`IIO_ENUM_AVAILABLE`，以及一批 `IIO_CONST_ATTR_*` 便捷宏。它们展开成 `iio_dev_attr` / `iio_const_attr` 实例，用法骨架在 `05-usage.md`。

## §3 触发器 API

`IIO_TRIGGER=n` 时 `trigger.h` 只留两个前向声明，整组 API 消失（不是留桩）。所以用到触发器的驱动必须在 Kconfig 里 `select IIO_TRIGGER` 或 `select IIO_TRIGGERED_BUFFER`。

### §3.1 触发器提供方 API

头文件：`include/linux/iio/trigger.h`；实现：`drivers/iio/industrialio-trigger.c`。

| API | 说明 | 备注 |
|---|---|---|
| `iio_trigger_alloc(parent, fmt, ...)` | 分配一个触发器，名字用 printf 格式拼 | **是宏**，展开成 `__iio_trigger_alloc(parent, THIS_MODULE, fmt, ...)`。返回 `struct iio_trigger *`，失败 NULL。`parent` 可以为 NULL（hrtimer/loop/interrupt 三个软件触发器就传 NULL） |
| `devm_iio_trigger_alloc(parent, fmt, ...)` | 同上，devres 托管 | **是宏**，声明在 `iio.h` 而不是 `trigger.h`（⚠️ 唯一一个位置不对称的触发器 API） |
| `iio_trigger_free(trig)` | 释放 | void。只在未注册状态下调 |
| `iio_trigger_register(trig_info)` | 注册触发器：分配 irq 描述符池、建 sysfs 节点 | 返回 0 或负错误码。**注册之后 `/sys/bus/iio/devices/triggerN/` 出现** |
| `devm_iio_trigger_register(dev, trig_info)` | 同上，devres 托管 | 注意参数多一个 `dev` |
| `iio_trigger_unregister(trig_info)` | 注销 | void |
| `iio_trigger_poll(trig)` | 触发一次：给池子里所有已挂载的消费者发虚拟中断 | void。**原子上下文**（走 `generic_handle_irq`），可在硬中断里调 |
| `iio_trigger_poll_chained(trig)` | 同上，但走线程化路径 | void（走 `handle_nested_irq`）。**必须在可睡眠上下文**。`iio-trig-loop.c` 用的是这个 |
| `iio_trigger_generic_data_rdy_poll(irq, private)` | 现成的中断处理函数：收到硬中断就 `iio_trigger_poll` | 返回 `irqreturn_t`。传给 `request_irq` 用，省掉自己写一个 |
| `iio_trigger_set_immutable(indio_dev, trig)` | 把某个触发器钉死在某设备上，用户态不能改 `current_trigger` | 返回 0 或负错误码。用于「传感器自带 data-ready 引脚」这类硬绑定 |
| `iio_trigger_set_drvdata(trig, data)` / `iio_trigger_get_drvdata(trig)` | 挂/取触发器私有指针 | 两个都是 `static inline` |

### §3.2 触发器消费方 API

头文件：`include/linux/iio/trigger_consumer.h`、`include/linux/iio/triggered_buffer.h`；实现：`drivers/iio/industrialio-trigger.c`、`drivers/iio/buffer/industrialio-triggered-buffer.c`。

绝大多数驱动**不直接用这一节的底层 API**，而是用 §4.2 的一键装配函数。这里列出来是为了看懂那个装配函数做了什么。

| API | 说明 | 备注 |
|---|---|---|
| `iio_alloc_pollfunc(h, thread, type, indio_dev, fmt, ...)` | 分配一对「顶半部 + 线程半部」处理函数 | 返回 `struct iio_poll_func *`。`type` 传 `IRQF_ONESHOT`。名字用 printf 拼，会出现在 `/proc/interrupts` |
| `iio_dealloc_pollfunc(pf)` | 释放 | void |
| `iio_pollfunc_store_time(irq, p)` | 现成的顶半部：只做一件事——记时间戳到 `pf->timestamp` | 返回 `irqreturn_t`。⚠️ **99% 的驱动直接把它当 `h` 参数传进去**，不用自己写顶半部 |
| `iio_trigger_notify_done(trig)` | 线程半部干完活必须调它，告诉触发器「这个消费者完事了」 | void。⚠️ **漏调会让触发器永远认为上一次采集没结束，后续触发被吞掉，而且不打任何日志** |
| `iio_trigger_using_own(indio_dev)` | 当前设备用的是不是它自己提供的触发器 | 返回 bool。⚠️ 内部对 `trig` 指针不做 NULL 检查，未挂触发器时调会出问题 |
| `iio_trigger_validate_own_device(trig, indio_dev)` | `validate_device` 回调的现成实现：只接受自己这个设备 | 返回 0 或 `-EINVAL`。填进 `iio_trigger_ops.validate_device` |

## §4 缓冲区 API

### §4.1 驱动侧推数据

头文件：`include/linux/iio/buffer.h`；实现：`drivers/iio/industrialio-buffer.c`。

| API | 说明 | 备注 |
|---|---|---|
| `iio_push_to_buffers(indio_dev, data)` | 把一个完整 scan 推给该设备所有已启用的缓冲区 | 返回 0 或负错误码。`data` 必须已按 scan 布局排好，长度 `scan_bytes` |
| `iio_push_to_buffers_with_timestamp(indio_dev, data, timestamp)` | 同上，但先把时间戳写进 `data` 的最后 8 字节 | **`static inline`，不是导出符号**。⚠️ `data` 缓冲区必须至少 `scan_bytes` 大，且时间戳位置按 `scan_bytes/8-1` 算——**缓冲区开小了是越界写** |
| `iio_push_to_buffers_with_ts_unaligned(indio_dev, data, data_sz, timestamp)` | 数据长度不等于 `scan_bytes` 时用的版本，核心负责补齐 | 返回 0 或负错误码。比上者慢，只在无法预先对齐时用 |
| `iio_pop_from_buffer(buffer, data)` | 从输出方向的缓冲区取一个 scan（DAC 场景） | 返回 0 或负错误码 |
| `iio_device_attach_buffer(indio_dev, buffer)` | 把一个缓冲区挂到设备上 | 返回 0 或负错误码。可多次调用挂多个缓冲区，第一个成为 index 0 |
| `iio_validate_scan_mask_onehot(indio_dev, mask)` | 现成的 `validate_scan_mask` 回调：只允许同时开一个通道 | 返回 bool |

### §4.2 缓冲区一键装配

头文件：`include/linux/iio/triggered_buffer.h`、`include/linux/iio/kfifo_buf.h`。

这是驱动实际会用的入口，两条路线二选一：

| API | 说明 | 备注 |
|---|---|---|
| `iio_triggered_buffer_setup(indio_dev, h, thread, setup_ops)` | 一次装好「kfifo 缓冲 + pollfunc + `INDIO_BUFFER_TRIGGERED` 模式」 | **是宏**，展开成 `_ext` 版本并传 `IIO_BUFFER_DIRECTION_IN` + `NULL` 属性表。返回 0 或负错误码。⚠️ 设备上已有缓冲区时返回 `-EADDRINUSE` |
| `devm_iio_triggered_buffer_setup(dev, indio_dev, h, thread, setup_ops)` | 同上，devres 托管 | **是宏**。**全树 77 个文件用这个**，是最主流的入口 |
| `iio_triggered_buffer_setup_ext(indio_dev, h, thread, direction, setup_ops, buffer_attrs)` | 上者的完整版：能指定方向和额外 sysfs 属性 | 输出方向（DAC）或需要私有属性时用 |
| `devm_iio_triggered_buffer_setup_ext(dev, indio_dev, h, thread, direction, ops, buffer_attrs)` | 完整版 + devres | — |
| `iio_triggered_buffer_cleanup(indio_dev)` | 手工路线的拆除 | void。⚠️ 直接 `iio_kfifo_free(indio_dev->buffer)`，正确性依赖 setup 时那道 `-EADDRINUSE` 检查 |
| `devm_iio_kfifo_buffer_setup(dev, indio_dev, setup_ops)` | 只装 kfifo 缓冲，**不要触发器**（`INDIO_BUFFER_SOFTWARE`） | **是宏**。**全树 17 个文件用**。用于「设备自己有 FIFO / 自己会主动推数据」的场合 |
| `devm_iio_kfifo_buffer_setup_ext(dev, indio_dev, setup_ops, buffer_attrs)` | 上者的完整版 | ⚠️ **这条路径不设置 `buffer->direction`，永远是 IN**；需要输出方向就得走 triggered-buffer 的 `_ext` |
| `iio_kfifo_allocate()` | 裸分配一个 kfifo 缓冲区 | 返回 `struct iio_buffer *`。全树只有 2 个文件用——基本被上面的装配函数取代 |
| `iio_kfifo_free(r)` | 释放 | void，实际是 `iio_buffer_put` |

⚠️ **`setup_ops` 传 NULL 是合法的。** `triggered_buffer.h` 的 kerneldoc 说「NULL 时会用默认的 setup 函数」，但装配函数本身是把 NULL 原样赋值下去的——真正的替换发生在 `iio_device_register` 内部，换成一个所有回调都是 NULL 的 `noop_ring_setup_ops`。措辞差异记在 `99-evidence.md` 的文档冲突表里。

### §4.3 缓冲区实现方 API

头文件：`include/linux/iio/buffer_impl.h`；实现：`drivers/iio/industrialio-buffer.c`。**只有「写一个新的缓冲区后端」才需要这一节**，写传感器驱动用不到。

| API | 说明 | 备注 |
|---|---|---|
| `iio_buffer_init(buffer)` | 初始化 `iio_buffer` 的通用字段（kref、等待队列、链表） | void。后端在自己的 allocate 里调 |
| `iio_buffer_get(buffer)` | 引用 +1 | 返回 `struct iio_buffer *`。⚠️ `IIO_BUFFER=n` 时是个返回 void 的空桩——签名都不一样 |
| `iio_buffer_put(buffer)` | 引用 −1，归零则调 `access->release` | void |
| `iio_update_buffers(indio_dev, insert_buffer, remove_buffer)` | 原子地插入/移除缓冲区并重算 scan mask | 返回 0 或负错误码。回调缓冲和 hw-consumer 的 start/stop 就是调它 |

后端要填的是 `struct iio_buffer_access_funcs` 这张 vtable（十二个函数指针，外加 `modes` / `flags` 两个非回调字段）。四个 in-tree 实现分别是 kfifo、DMA、回调缓冲、hw-consumer——它们各自实现了几个回调、为什么能少实现，在 `04-extensions.md` 里逐个展开。

## §5 事件 API

头文件：`include/linux/iio/events.h`（内核侧）、`include/uapi/linux/iio/events.h`（用户态侧）；实现：`drivers/iio/industrialio-event.c`。

事件是「阈值越界 / 手势识别 / 数据就绪」这类**离散通知**，不走缓冲区，走一个 16 深的 kfifo，用户态从 `IIO_GET_EVENT_FD_IOCTL` 拿到的 fd 上读。

| API | 说明 | 备注 |
|---|---|---|
| `iio_push_event(indio_dev, ev_code, timestamp)` | 把一个事件塞进事件 kfifo | ⚠️ **返回值是 int，但实际上恒为 0**——事件被丢弃（无人打开 fd、或 kfifo 满）时**也返回 0**，调用方无法区分。详见 `07-debug.md` |
| `IIO_EVENT_CODE(chan_type, diff, modifier, direction, type, chan, chan1, chan2)` | 把八个字段打包成 64 位事件码 | **是宏**。⚠️ **`chan` 和 `chan1` 都 OR 进低 16 位**——差分事件用 `chan1`/`chan2`，非差分用 `chan`，两者不能同时给非零值 |
| `IIO_UNMOD_EVENT_CODE(chan_type, number, type, direction)` | 无修饰符通道的便捷版 | **是宏**，等价于 `IIO_EVENT_CODE(chan_type, 0, 0, direction, type, number, 0, 0)` |
| `IIO_MOD_EVENT_CODE(chan_type, number, modifier, type, direction)` | 带修饰符（如 `IIO_MOD_X`）的便捷版 | **是宏**，`diff` 固定为 0 |

事件码位布局（`IIO_EVENT_CODE` 展开可读出）：

```
 63    56 55 54  48 47  40 39  32 31      16 15       0
[  type ][d][  dir  ][ modif ][chan_type][  chan2   ][ chan/chan1 ]
             7 bit     8 bit     8 bit      16 bit       16 bit
```

用户态侧的解包宏全部在 `include/uapi/linux/iio/events.h`：`IIO_EVENT_CODE_EXTRACT_TYPE`、`_DIR`、`_CHAN_TYPE`、`_CHAN`、`_CHAN2`、`_MODIFIER`、`_DIFF`（7 个）。⚠️ `_CHAN` 和 `_CHAN2` 转成 `__s16`（**有符号**），其余是无符号右移加掩码。uapi 头里 `:35-36` 那句注释「Event code number extraction depends on which type of event we have. Perhaps review this function in the future」是上游自陈的设计遗留。

驱动侧要暴露事件阈值属性（`events/in_voltage0_thresh_rising_value` 这类），填的是 `iio_chan_spec.event_spec` 数组 + `num_event_specs`，属性名同样由核心合成，不用手写。

## §6 内核内消费者 API

头文件：`include/linux/iio/consumer.h`；实现：`drivers/iio/inkern.c`。另有两个注册侧函数在 `include/linux/iio/driver.h`，`iio_map` 结构本体在 `include/linux/iio/machine.h`。

这一层解决的是「电池驱动想读 ADC」这类需求：消费者不是用户态，是另一个内核驱动。**IIO 唯一一个导出符号全走 `EXPORT_SYMBOL_GPL` 的文件**（28 个，无一例外）。

⚠️ 全树分布上，**最大的外部消费者是 `drivers/power/supply/`（21 个文件）**，远超其它任何目录。完整分布表和复现命令在 `99-evidence.md`。

### §6.1 拿到通道 / 放掉通道

| API | 说明 | 备注 |
|---|---|---|
| `iio_channel_get(dev, consumer_channel)` | 按消费者侧的通道名拿一个通道 | 返回 `struct iio_channel *` 或 **ERR_PTR**。⚠️ 提供方还没 probe 时返回 `-EPROBE_DEFER`——消费者必须原样往上返回，不能当致命错误 |
| `iio_channel_release(chan)` | 放掉 | void |
| `devm_iio_channel_get(dev, consumer_channel)` | devres 版 | 返回 ERR_PTR |
| `iio_channel_get_all(dev)` | 拿该消费者的**全部**通道，以 NULL 结尾的数组 | 返回 ERR_PTR。数组末尾用 `channel == NULL` 标记 |
| `iio_channel_release_all(chan)` | 放掉整个数组 | void |
| `devm_iio_channel_get_all(dev)` | devres 版 | 返回 ERR_PTR |
| `fwnode_iio_channel_get_by_name(fwnode, name)` | 不用 `struct device`，直接从 fwnode 查 | 返回 ERR_PTR。给「消费者没有独立 device」的场景 |
| `devm_fwnode_iio_channel_get_by_name(dev, fwnode, consumer_channel)` | 上者的 devres 版 | 注意它有三个参数 |

### §6.2 读写通道值

| API | 说明 | 备注 |
|---|---|---|
| `iio_read_channel_raw(chan, val)` | 读原始值（未换算） | 返回 0 或负错误码，值写进 `*val` |
| `iio_read_channel_average_raw(chan, val)` | 读硬件平均后的原始值 | 提供方未实现 `INFO_AVERAGE_RAW` 时返回 `-EINVAL` |
| `iio_read_channel_processed(chan, val)` | 读换算成物理量的整数值 | 核心会自动 raw × scale + offset。⚠️ 结果是**整数**，精度损失取决于 scale |
| `iio_read_channel_processed_scale(chan, val, scale)` | 同上，额外乘一个倍数（用来保精度） | 例如传 1000 得到毫伏而不是伏 |
| `iio_write_channel_raw(chan, val)` | 写原始值（DAC 场景） | 返回 0 或负错误码 |
| `iio_read_max_channel_raw(chan, val)` | 读该通道原始值的上限 | 从 `_available` 表里取最大值 |
| `iio_read_avail_channel_raw(chan, vals, length)` | 读原始值的可选集合 | `*vals` 指向核心的数组，`*length` 是元素个数 |
| `iio_read_channel_attribute(chan, val, val2, attribute)` | 读任意 `IIO_CHAN_INFO_*` 项 | `val`/`val2` 是定点数的整数/小数部分，返回值是 `IIO_VAL_*` 格式标签 |
| `iio_write_channel_attribute(chan, val, val2, attribute)` | 写任意 `IIO_CHAN_INFO_*` 项 | — |
| `iio_read_avail_channel_attribute(chan, vals, type, length, attribute)` | 读任意 info 项的可选集合 | — |
| `iio_read_channel_offset(chan, val, val2)` | 读 offset | 返回 `IIO_VAL_*` 标签 |
| `iio_read_channel_scale(chan, val, val2)` | 读 scale | 同上 |
| `iio_convert_raw_to_processed(chan, raw, processed, scale)` | 手工把 raw 换算成 processed | ⚠️ 源码里带 `/* FIXME: learn about max for other iio values */` 自陈注释，只处理了部分 `IIO_VAL_*` 形式 |
| `iio_get_channel_type(channel, type)` | 查这个通道是什么物理量 | `*type` 是 `enum iio_chan_type` |
| `iio_get_channel_ext_info_count(chan)` | 该通道有几个扩展 info 属性 | 返回 unsigned int |
| `iio_read_channel_ext_info(chan, attr, buf)` | 按名字读扩展属性（字符串接口） | 返回 `ssize_t` |
| `iio_write_channel_ext_info(chan, attr, buf, len)` | 按名字写扩展属性 | 返回 `ssize_t` |

### §6.3 提供方侧：注册映射表

头文件：`include/linux/iio/driver.h` + `include/linux/iio/machine.h`。

DT 路径下**不需要这一节**——核心直接从 `io-channels` 属性解析。只有 board file（非 DT）路径才用：

| API | 说明 | 备注 |
|---|---|---|
| `iio_map_array_register(indio_dev, map)` | 注册一张「提供方通道 ↔ 消费者名字」映射表 | 返回 0 或负错误码。`map` 数组以全 NULL 项结尾 |
| `iio_map_array_unregister(indio_dev)` | 撤销该设备的所有映射 | 返回 int |
| `devm_iio_map_array_register(dev, indio_dev, maps)` | devres 版 | 注意有三个参数 |
| `IIO_MAP(_provider_channel, _consumer_dev_name, _consumer_channel)` | 填 `iio_map` 的便捷宏 | **是宏**，只填三个字段，`consumer_data` 留 NULL |

`iio_map.adc_channel_label` 是拿去和提供方 `iio_chan_spec.datasheet_name` 比对的——⚠️ **提供方没填 `datasheet_name` 的通道永远匹配不上**，而这不会报错。

### §6.4 回调缓冲：把连续数据流推给内核消费者

头文件：`include/linux/iio/consumer.h`；实现：`drivers/iio/buffer/industrialio-buffer-cb.c`。需要 `CONFIG_IIO_BUFFER_CB`。

§6.2 是「点读」，这一组是「订阅数据流」：

| API | 说明 | 备注 |
|---|---|---|
| `iio_channel_get_all_cb(dev, cb, private)` | 拿到该消费者的全部通道并建一个回调缓冲 | 返回 `struct iio_cb_buffer *` 或 ERR_PTR。⚠️ `cb` 不能为 NULL，否则打印 `Invalid arguments: A callback must be provided!` 并返回错误。⚠️ **所有通道必须来自同一个提供方**，否则 `-EINVAL` |
| `iio_channel_cb_set_buffer_watermark(cb_buffer, watermark)` | 设置攒够多少样本再回调 | 返回 0 或 `-EINVAL`（传 0 时） |
| `iio_channel_start_all_cb(cb_buff)` | 启动数据流 | 返回 0 或负错误码。内部是 `iio_update_buffers` |
| `iio_channel_stop_all_cb(cb_buff)` | 停止 | void |
| `iio_channel_release_all_cb(cb_buffer)` | 释放 | void。⚠️ 必须先 stop |
| `iio_channel_cb_get_channels(cb_buffer)` | 取回通道数组 | 返回 `struct iio_channel *` |
| `iio_channel_cb_get_iio_dev(cb_buffer)` | 取回提供方的 `iio_dev` | 返回 `struct iio_dev *` |

全树只有 4 个调用点用 `iio_channel_get_all_cb`：`drivers/input/touchscreen/resistive-adc-touch.c`、`drivers/input/joystick/adc-joystick.c`、`drivers/iio/potentiostat/lmp91000.c`、`sound/soc/stm/stm32_adfsdm.c`。

### §6.5 硬件直连消费者

头文件：`include/linux/iio/hw-consumer.h`；实现：`drivers/iio/buffer/industrialio-hw-consumer.c`。需要 `CONFIG_IIO_BUFFER_HW_CONSUMER`。

| API | 说明 | 备注 |
|---|---|---|
| `iio_hw_consumer_alloc(dev)` | 建立一个「数据不经 CPU」的消费关系 | 返回 `struct iio_hw_consumer *` 或 ERR_PTR |
| `iio_hw_consumer_free(hwc)` | 释放 | void |
| `devm_iio_hw_consumer_alloc(dev)` | devres 版 | ⚠️ **kerneldoc 写「NULL on failure」是错的，实际返回 ERR_PTR** |
| `iio_hw_consumer_enable(hwc)` | 启用 | 返回 0 或负错误码，失败时按逆序回滚已启用的部分 |
| `iio_hw_consumer_disable(hwc)` | 停用 | void |

⚠️ **这个框架的 `iio_buffer_access_funcs` 里没有 `.store_to`**——数据根本不进 CPU，只有 `INDIO_BUFFER_HARDWARE` 模式。全树唯一使用者是 `drivers/iio/adc/stm32-dfsdm-adc.c`（1 个文件 / 6 个调用点），**arm32 STM32 平台专用，arm64 上碰不到**。

## §7 软件对象与 configfs API

### §7.1 软件触发器类型

头文件：`include/linux/iio/sw_trigger.h`；实现：`drivers/iio/industrialio-sw-trigger.c`。需要 `CONFIG_IIO_SW_TRIGGER`。

| API | 说明 | 备注 |
|---|---|---|
| `iio_register_sw_trigger_type(t)` | 注册一种「可以用 `mkdir` 创建实例」的触发器类型 | 返回 0；重名返回 `-EBUSY`。同时在 `/config/iio/triggers/` 下建一个以 `t->name` 命名的默认组 |
| `iio_unregister_sw_trigger_type(t)` | 注销 | void |
| `iio_sw_trigger_create(type, name)` | 从内核里直接创建一个实例（不经 configfs） | 返回 `struct iio_sw_trigger *` 或 ERR_PTR。类型不存在时打印 `Invalid trigger type: %s` |
| `iio_sw_trigger_destroy(t)` | 销毁 | void |
| `module_iio_sw_trigger_driver(__iio_sw_trigger_type)` | 生成 `module_init`/`module_exit` 的样板宏 | **是宏**。hrtimer 和 loop 两个触发器都用它 |

实现方要填 `struct iio_sw_trigger_type`（`name` / `owner` / `ops` / 内部 `list` / 内部 `group`），`ops` 里两个回调：`probe(const char *name)` 返回 `struct iio_sw_trigger *`、`remove(sw_trig)`。

全树只有 **2 个** `iio_sw_trigger_type` 实现：`trigger/iio-trig-hrtimer.c`（`.name = "hrtimer"`）和 `trigger/iio-trig-loop.c`（`.name = "loop"`）。

### §7.2 软件设备类型

头文件：`include/linux/iio/sw_device.h`；实现：`drivers/iio/industrialio-sw-device.c`。需要 `CONFIG_IIO_SW_DEVICE`。

结构与 §7.1 **逐字对应**，只是把 `trigger` 换成 `device`、`triggers` 组换成 `devices` 组：

| API | 说明 | 备注 |
|---|---|---|
| `iio_register_sw_device_type(t)` | 注册一种软件设备类型 | 返回 0 / `-EBUSY`。⚠️ **与触发器版有一处真实差异**：configfs 建组失败时它不回滚 `list_del`，触发器版会回滚。见 `07-debug.md` |
| `iio_unregister_sw_device_type(t)` | 注销 | void |
| `iio_sw_device_create(type, name)` | 内核侧直接创建实例 | 返回 ERR_PTR。类型不存在时打印 `Invalid device type: %s` |
| `iio_sw_device_destroy(d)` | 销毁 | void |
| `module_iio_sw_device_driver(__iio_sw_device_type)` | 样板宏 | **是宏** |

全树只有 **1 个** `iio_sw_device_type` 实现：`drivers/iio/dummy/iio_simple_dummy.c`。

### §7.3 configfs 根

头文件：`include/linux/iio/configfs.h`；实现：`drivers/iio/industrialio-configfs.c`。

这个头文件只声明一个符号：`extern struct configfs_subsystem iio_configfs_subsys;`（`.ci_namebuf = "iio"`，即挂载点下的 `iio/` 目录）。它被 §7.1/§7.2 两个模块引用来挂自己的默认组，普通驱动不碰它。

### §7.4 触发式事件辅助

头文件：`include/linux/iio/triggered_event.h`；实现：`drivers/iio/industrialio-triggered-event.c`。需要 `CONFIG_IIO_TRIGGERED_EVENT`。

| API | 说明 | 备注 |
|---|---|---|
| `iio_triggered_event_setup(indio_dev, h, thread)` | 装一对 pollfunc 到 `indio_dev->pollfunc_event`，并置上 `INDIO_EVENT_TRIGGERED` 模式 | 返回 0 或负错误码。用于「事件检测也要靠触发器驱动」的设备 |
| `iio_triggered_event_cleanup(indio_dev)` | 拆除，清掉模式位 | void |

⚠️ **它给 pollfunc 起的名字格式和 `iio_triggered_buffer_setup_ext` 完全一样**（`"<设备名>_consumerN"`）。同时用了触发式缓冲和触发式事件的设备，会在 `/proc/interrupts` 里出现两个同名条目——排查时无法区分谁是谁。全树只有 2 个文件用这个 API。

## §8 宏包装与 header-only 辅助

### §8.1 藏了 `THIS_MODULE` 的宏包装

IIO 有一组 API **表面上是函数，实际上是宏**——宏把 `THIS_MODULE` 悄悄塞进去，真正的符号带 `__` 前缀且多一个参数。写代码时用宏，但排查符号、看 `nm` 输出、用代码索引工具时要知道真名：

| 你写的（宏） | 真正的符号 | 宏定义在 |
|---|---|---|
| `iio_device_register(indio_dev)` | `__iio_device_register(indio_dev, this_mod)` | `iio.h` |
| `devm_iio_device_register(dev, indio_dev)` | `__devm_iio_device_register(dev, indio_dev, this_mod)` | `iio.h` |
| `iio_trigger_alloc(parent, fmt, ...)` | `__iio_trigger_alloc(parent, this_mod, fmt, ...)` | `trigger.h` |
| `devm_iio_trigger_alloc(parent, fmt, ...)` | `__devm_iio_trigger_alloc(parent, this_mod, fmt, ...)` | `iio.h` |

另有三个宏只是「填默认参数」，不涉及 `THIS_MODULE`：

| 你写的（宏） | 展开成 | 补的默认值 |
|---|---|---|
| `iio_triggered_buffer_setup(indio_dev, h, thread, setup_ops)` | `iio_triggered_buffer_setup_ext(...)` | `IIO_BUFFER_DIRECTION_IN` + `buffer_attrs = NULL` |
| `devm_iio_triggered_buffer_setup(dev, indio_dev, h, thread, setup_ops)` | `devm_iio_triggered_buffer_setup_ext(...)` | 同上 |
| `devm_iio_kfifo_buffer_setup(dev, indio_dev, setup_ops)` | `devm_iio_kfifo_buffer_setup_ext(...)` | `buffer_attrs = NULL` |

⚠️ **这七个宏是代码索引工具的盲区**：索引里查 `iio_device_register` 的调用者会得到零结果或只有宏定义那一行，因为展开后的符号名不一样。本文档集所有涉及这些 API 的计数都是用 `rg`/`grep` 在真实树上跑的，命令记在 `99-evidence.md`。

### §8.2 只在头文件里的辅助（无独立符号）

这些是 `static inline`，不出现在 `EXPORT_SYMBOL` 列表里，但**读者可以正常使用**——它们随头文件展开进调用方：

| 位置 | 符号 |
|---|---|
| `iio.h` | `iio_device_put`、`dev_to_iio_dev`、`iio_device_get`、`iio_device_set_parent`、`iio_device_set_drvdata`、`iio_device_get_drvdata`、`iio_priv`、`iio_channel_has_info`、`iio_channel_has_available`、`iio_get_debugfs_dentry`（仅 `CONFIG_DEBUG_FS=n` 时的桩） |
| `buffer.h` | `iio_push_to_buffers_with_timestamp` |
| `trigger.h` | `iio_trigger_get_drvdata`、`iio_trigger_set_drvdata` |

⚠️ **`iio_push_to_buffers_with_timestamp` 是全 IIO 最常被调用的函数之一，但它没有符号。** 想在它上面下 kprobe 是不行的——`07-debug.md` 里给了替代办法。

### §8.3 一个真实的签名分叉

`iio_buffer_get` 在 `buffer_impl.h` 里有两个版本，由 `CONFIG_IIO_BUFFER` 切换：开启时是**返回 `struct iio_buffer *` 的函数**，关闭时是**返回 void 的空 `static inline`**。⚠️ 写 `buf = iio_buffer_get(buf);` 在 `IIO_BUFFER=n` 的配置下编译不过，而这类配置很少被测到。

## §9 错误码语义

核心 7 个 `.c` 文件（core / buffer / event / trigger / inkern / sw-trigger / sw-device）加 `buffer/` 目录，按语义分组如下。

⚠️ **表里的数字是「含该字符串的行数」，不是「返回点数」**——注释里提到错误码的行也被数进去了（例如 `-ENOENT` 的 4 行里有 3 行在注释里）。每个数字的复现命令在 `99-evidence.md`。

| 错误码 | 语义分组 | 典型出处与判据 |
|---|---|---|
| `-EINVAL` | 参数不合法 / 该功能提供方未实现 | 核心 7 文件 **71 行**、`buffer/` **8 行**，是最泛的一类。三种子语义：①用户写进 sysfs 的值不在合法集合里；②`iio_info` 里对应回调是 NULL（消费者读一个提供方没实现的 info 项）；③通道号/scan_index 越界 |
| `-ENODEV` | 目标不存在或已消失 | 核心 **35 行**，其中 **`inkern.c` 占 23 行**（找不到提供方/通道），`industrialio-buffer.c` 占 7 行（读写过程中设备被注销）。⚠️ **消费者拿不到通道时先分清是 `-ENODEV` 还是 `-EPROBE_DEFER`**，前者是配置错，后者只是顺序问题 |
| `-ENOMEM` | 分配失败 | 核心 **30 行**、`buffer/` **10 行**。无特殊语义 |
| `-EBUSY` | 状态冲突。**IIO 里这个码有四种完全不同的含义**（见下） | 核心 **23 行**、`buffer/` **3 行** |
| `-EPERM` | 方向不对 / 被钉死 | 3 行。①往 IN 方向缓冲区写、从 OUT 方向缓冲区读（`industrialio-buffer.c` 的 read/write 各一处）；②`current_trigger` 被 `iio_trigger_set_immutable` 钉死后仍尝试改写 |
| `-EAGAIN` | 非阻塞模式下暂无数据 | 5 行（event 2 处 + buffer 2 处 + 1 处注释）。`O_NONBLOCK` 打开且 kfifo 空时 |
| `-ERESTARTSYS` | 等待数据时被信号打断 | 4 行（event / buffer / kfifo 各处）。**这是正常路径，用户态要重试** |
| `-EFAULT` | `copy_to_user` / `copy_from_user` 失败 | 5 行（core / buffer ×2 / event / buffer-dma） |
| `-EFBIG` | sysfs 输出超过一页 | 3 行，全在 `industrialio-core.c` 的值格式化路径。⚠️ **通道的 `_available` 列表太长时会撞上这个**，症状是读属性返回错误而不是截断 |
| `-EEXIST` | 触发器重名 | 1 行，`industrialio-trigger.c` 注册路径，同时打印 `Duplicate trigger name '%s'` |
| `-EPROBE_DEFER` | 提供方还没注册 | 2 行，全在 `inkern.c`。⚠️ **消费者必须原样往上返回**，让驱动核心稍后重试 |
| `-EADDRINUSE` | 设备上已经有缓冲区了 | **全树 1 行**，`buffer/industrialio-triggered-buffer.c`。语义是「同一个设备上装了两次触发式缓冲」——这是驱动 bug，不是运行时状态 |

### §9.1 `-EBUSY` 的四种含义

这是 IIO 里最容易误读的错误码，四种含义互不相干：

| 含义 | 出处 | 用户可见表现 |
|---|---|---|
| **模式互斥**：缓冲区已启用时不能进直接模式，或反之 | `iio_device_claim_direct_mode` / `iio_device_claim_buffer_mode`；`iio_device_set_clock`（事件或缓冲已开时不许改时钟源）；`current_trigger` 写入时 `currentmode == INDIO_BUFFER_TRIGGERED` | 读 sysfs 属性或写 `current_trigger` 返回 `EBUSY`。**关掉 `buffer/enable` 就好** |
| **独占打开**：`/dev/iio:deviceN` 或事件 fd 已被打开 | 字符设备 `open`（`IIO_BUSY_BIT_POS` 的 test_and_set）；`IIO_GET_EVENT_FD_IOCTL`；新旧两套 buffer 读写 API 混用时的 wrapper 检查 | 第二个进程打开失败。**IIO 设备节点是独占的** |
| **属性名重复**：sysfs 属性合成时同名冲突 | `industrialio-core.c` 的属性链表查重；事件属性同样一处 | ⚠️ **多数情况下这个 `-EBUSY` 被核心自己吞掉**——`shared_by != IIO_SEPARATE` 时属于预期（多个通道共享同一个属性名，第二次起跳过）。只有 `shared_by == IIO_SEPARATE` 时才打印 `tried to double register : %s` 并真的失败 |
| **kfifo 满 / 空** | `kfifo_buf.c` 的 `store_to`（满）和 `remove_from`（取不出） | 数据被丢弃，且 `iio_push_to_buffers` 的返回值往往没人看 |

⚠️ **第三种是「预期中的 `-EBUSY`」**：源码里有三处 `if ((ret == -EBUSY) && (shared_by != IIO_SEPARATE))` 形态的判断把它当成成功。看到 `-EBUSY` 就断定出错是错的——要先看它是从哪条路径来的。

## §10 看着像 API 但不是的东西

| 符号 | 为什么不能用 | 替代 |
|---|---|---|
| `iio_find_channel_from_si(indio_dev, si)` | ⚠️ **声明在公开头 `iio.h` 里，但全树没有 `EXPORT_SYMBOL`**。核心内部（`industrialio-buffer.c` 等）能用是因为同在 `industrialio.ko` 里；外部模块链接会失败 | 自己遍历 `indio_dev->channels` |
| `devm_iio_kfifo_allocate(dev)` | `static`，只在 `kfifo_buf.c` 内部用，kerneldoc 写得很像公开 API | `devm_iio_kfifo_buffer_setup` |
| `iio_dev_opaque` 的任何字段 | 结构定义在 `include/linux/iio/iio-opaque.h`，但它是**核心私有外壳**。`currentmode`、`clock_id`、`event_interface`、`attached_buffers[]` 等都在这里 | 有 getter 就用 getter（`iio_device_get_current_mode`、`iio_device_get_clock`、`iio_buffer_enabled`） |
| `drivers/iio/iio_core.h` 里的 24 个声明 | 文件在 `drivers/` 下而不是 `include/` 下，就是「不对外」的意思 | 无 |
| `drivers/iio/iio_core_trigger.h` 里的声明 | 同上。⚠️ 该文件末尾的 `#endif` 注释写的是 `CONFIG_TRIGGER_CONSUMER`，而真正的守卫宏是 `CONFIG_IIO_TRIGGER`——**别照着注释去 grep** | 无 |
| `__iio_device_register` / `__devm_iio_device_register` / `__iio_trigger_alloc` / `__devm_iio_trigger_alloc` | 虽然确实导出了，但直接调等于手工传 `THIS_MODULE`，绕过宏没有任何好处 | 用 §8.1 的宏 |
| `iio_bus_type` | 确实导出，但⚠️ **这条总线上零个驱动**：它没有 `.match`、没有 `.probe`，四种设备注册在上面只为拿到统一的 sysfs 命名空间和一个 fwnode 查找索引。**不要试图往上注册 `device_driver`** | 详见 `02-datastructures.md` 与 `99-evidence.md` §1 |
| `iio_configfs_subsys` | 导出了，但只该被 `industrialio-sw-trigger.ko` / `industrialio-sw-device.ko` 引用 | 用 §7.1/§7.2 的类型注册 API |

## 阅读导航

| 下一步想做什么 | 去哪篇 | 具体位置 |
|---|---|---|
| 看 `iio_dev` / `iio_chan_spec` / `iio_buffer` 到底有哪些字段 | `02-datastructures.md` | §1.0 先分清几组关系 |
| 跟一遍注册、采样、缓冲启停、拆除的完整时间线 | `03-internals.md` | Part A |
| 搞清 kfifo / DMA / 回调缓冲 / 六个触发器提供方各是什么 | `04-extensions.md` | 一节一个框架 |
| 照着骨架写一个驱动 | `05-usage.md` | 按角色分节 |
| 写 DTS（`io-channels` 一族）或排查 DTS 配错 | `06-devicetree.md` | §0 发现路径分诊表 |
| 现场排查一个具体故障 | `07-debug.md` | §0.1 按症状找工具 |
| 用大白话理解通道模型、触发器为什么是虚拟中断 | `08-feynman.md` | 一节一个概念 |
| 复习整个子系统的全貌 | `00-overview.md` | §3 概念模型 |
| 核对本篇任何一个数字或统计口径 | `99-evidence.md` | 逐篇取证表 |
