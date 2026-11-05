---
title: API 参考
---

# input 子系统 · 代码地图与对外 API

- 内核版本：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**
- 本篇管两件事：**「这段逻辑在哪个文件」** 和 **「我该调哪个函数」**。这两个问题是同一次查询，所以放在同一篇
- 位置一律只精确到文件，不给行号。要行号去 `03-internals.md`（主流程）或 `99-evidence.md`（取证）
- 签名只写「函数名 + 形参名」，类型信息在「备注」列里
- 判断一个符号「读者能不能用」的标准是 **有 `EXPORT_SYMBOL*` 且头文件里有声明**，不是有没有 kerneldoc。本篇每张表的「备注」列会点出不满足这条的符号

---

## §0 代码地图

### §0.1 核心目录与规模

`drivers/input/` 下的 `.c`/`.h` 一共 **215280 行**（410 个 `.c` + 44 个 `.h`）。但**核心只有 9558 行**——就是 `drivers/input/` 根目录下那 19 个文件（16 个 `.c` + 3 个 `.h`）；剩下 20 万行全是设备驱动，住在 9 个子目录里。

| 子目录 | `.c` 个数 | 行数（`.c`+`.h`） | 装什么 |
|---|---|---|---|
| `touchscreen/` | 109 | 59409 | 触摸屏驱动，arm64 板子上最常见的一类 |
| `misc/` | 84 | 36052 | 杂项：`uinput.c`、`gpio-vibra`、`pwm-beeper`、加速度计按键等 |
| `keyboard/` | 68 | 30797 | 键盘与矩阵键盘 |
| `mouse/` | 38 | 32818 | PS/2 触控板系族（`synaptics`、`elantech`、`alps`） |
| `joystick/` | 39 | 15823 | 游戏杆，多数是 x86 ISA/并口老硬件 |
| `serio/` | 29 | 14722 | PS/2 串行 IO 总线（**独立子框架**，见 `04-extensions.md` §2） |
| `rmi4/` | 16 | 10511 | Synaptics RMI4 协议栈（**自带 bus_type**，见 `04-extensions.md` §5） |
| `gameport/` | 5 | 1696 | 游戏端口总线（同上，独立子框架） |
| `tablet/` | 6 | 3894 | 数位板（USB） |

重跑：

```bash
for d in $(ls -d drivers/input/*/); do
  printf '%-28s %4s 个 .c  %7s 行\n' "$d" \
    "$(find $d -name '*.c' | wc -l)" \
    "$(cat $(find $d -name '*.c' -o -name '*.h') 2>/dev/null | wc -l)"
done
```

★ **官方文档里对应的三个入口**（都是纯目录页/聚合页，没有独立叙述内容，所以本文档集只在这里登记一次）：

| 官方文件 | 行数 | 是什么 | 对本篇的意义 |
|---|---|---|---|
| `Documentation/driver-api/input.rst` | 42 | 纯 `kernel-doc:: :export:` 聚合页 | ★ 它拉的文件恰好是一份「官方认定的核心清单」：`include/linux/input.h`、`drivers/input/input.c`、`ff-core.c`、`ff-memless.c`、`include/linux/input/mt.h`、`input-mt.c`、`matrix_keypad.h`、`sparse-keymap.h`、`sparse-keymap.c`——和本篇 §0.2 的核心 16 文件表高度重合，缺的是 `touchscreen.c` / `input-poller.c` / 三个 handler |
| `Documentation/input/input_kapi.rst` | 17 | 内核侧 API 的 toctree | 只列三篇：`input-programming`、`gameport-programming`、`notifier` |
| `Documentation/input/input_uapi.rst` | 22 | 用户态 ABI 的 toctree | 列八篇：`input`、`event-codes`、`multi-touch-protocol`、`gamepad`、`ff`、`joydev/index`、`uinput`、`userio` |

★★ 注意 `driver-api/input.rst` 的 `:export:` 只覆盖 5 个 `.c`（`input.c`、`ff-core.c`、`ff-memless.c`、`input-mt.c`、`sparse-keymap.c`），而本子系统核心 16 个 `.c` 里有 14 处 `EXPORT_SYMBOL_GPL`（口径与清单见 `05-usage.md` §0.1）——**「有 kerneldoc 且被官方聚合页收录」不是判断「读者能不能用」的标准**，判据仍然是 `EXPORT_SYMBOL*` + 公开头文件里有声明。

### §0.2 核心文件逐个交代

按「读的顺序」排，不按字母序。行数取自 `wc -l`。

| 文件 | 行数 | 职责 | 什么时候需要打开它 |
|---|---|---|---|
| `drivers/input/input.c` | 2770 | **输入核心**。事件分发、设备/handler 双向注册与匹配、sysfs 属性、procfs、autorepeat、inhibit、次设备号分配 | 任何「事件到底怎么走的」问题 |
| `drivers/input/evdev.c` | 1446 | `/dev/input/eventN` 的实现，唯一的通用用户态接口 | 用户态读不到事件、ioctl 语义、时间戳来源 |
| `drivers/input/mousedev.c` | 1125 | `/dev/input/mouseN`、`/dev/psaux` 的 PS/2 兼容层 | 只有需要伺候老程序时 |
| `drivers/input/joydev.c` | 1098 | `/dev/input/jsN` 的老游戏杆接口 | 同上 |
| `drivers/input/ff-memless.c` | 553 | 力反馈的「无内存设备」模拟层：把复杂效果算成周期性的 rumble 命令 | 力反馈设备行为不对 |
| `drivers/input/input-mt.c` | 538 | **多点触控槽位库**：slot 分配、in-kernel 轨迹跟踪、指针模拟 | 触摸屏、触控板 |
| `drivers/input/ff-core.c` | 377 | 力反馈核心：effect 上传/擦除/播放、owner 记账 | 力反馈设备 |
| `drivers/input/sparse-keymap.c` | 294 | 稀疏键盘映射库（扫描码不连续时用） | 笔记本热键、平台驱动 |
| `drivers/input/input-poller.c` | 222 | 轮询设备的公共实现（原 `input-polldev` 的替代） | 用 `input_setup_polling()` 的驱动 |
| `drivers/input/input-leds.c` | 220 | 把输入设备的 LED（大小写锁等）接到 LED 子系统 | LED 不亮 |
| `drivers/input/touchscreen.c` | 207 | 触摸屏公共 DT 属性解析与坐标变换 | `touchscreen-*` 属性，见 `06-devicetree.md` |
| `drivers/input/matrix-keymap.c` | 202 | 矩阵键盘的 `linux,keymap` 解析与 keycode 表构建 | 矩阵键盘 |
| `drivers/input/input-compat.c` | 133 | 32 位程序在 64 位内核上读写 `input_event` 的结构体转换 | compat 相关，arm64 上是 `CONFIG_COMPAT` |
| `drivers/input/apm-power.c` | 122 | 把 `KEY_SUSPEND` 转成 APM 事件 | 基本不用（依赖 `CONFIG_APM_EMULATION`） |
| `drivers/input/evbug.c` | 100 | 调试用 handler：把所有事件 `printk` 出来 | 见 `07-debug.md` §2 |
| `drivers/input/vivaldi-fmap.c` | 39 | ChromeOS Vivaldi 键盘功能行映射的 sysfs 展示 | ChromeOS 键盘 |
| `drivers/input/input-compat.h` | 78 | compat 转换的内联部分 + `INPUT_COMPAT_TEST` 宏 | — |
| `drivers/input/input-poller.h` | 18 | poller 内部接口（`input_dev_poller_start` 等） | 核心内部，驱动不该包含 |
| `drivers/input/input-core-private.h` | 16 | 只有 `input_mt_release_slots()` 和 `input_handle_event()` 两个声明 | 核心内部 |

### §0.3 头文件

| 头文件 | 行数 | 给谁看 |
|---|---|---|
| `include/linux/input.h` | 580 | **设备驱动作者和 handler 作者的主入口**。`input_dev`、`input_handler`、`input_handle`、`ff_device` 都在这里 |
| `include/uapi/linux/input.h` | 518 | 用户态 ABI：`struct input_event`、`struct input_absinfo`、`EVIOC*` ioctl、`ff_effect` |
| `include/uapi/linux/input-event-codes.h` | 976 | **事件码的唯一权威表**：`EV_*`、`KEY_*`、`ABS_*`、`REL_*`、`SW_*`、`MSC_*`、`LED_*`、`SND_*`、`FF_*`、`INPUT_PROP_*` |
| `include/uapi/linux/uinput.h` | 232 | 从用户态造输入设备的 ABI |
| `include/linux/input/mt.h` | 129 | 多点触控库 |
| `include/linux/input/matrix_keypad.h` | 92 | 矩阵键盘平台数据与解析 |
| `include/linux/input/sparse-keymap.h` | 59 | 稀疏键盘映射 |
| `include/linux/input/touchscreen.h` | 32 | 触摸屏公共属性 |
| `include/linux/input/vivaldi-fmap.h` | 27 | Vivaldi 功能行 |
| `include/linux/input/*.h`（其余 12 个） | 共 904 | 全是**单个芯片的平台数据**（`ad714x.h`、`adxl34x.h`、`adp5589.h`、`cma3000.h`、`kxtj9.h`、`as5011.h`、`lm8333.h`、`navpoint.h`、`samsung-keypad.h`、`sh_keysc.h`、`tps6507x-ts.h`、`elan-i2c-ids.h`），不是框架 API |

`include/linux/input/` 一共 17 个头文件、1243 行；其中真正算框架 API 的只有前 5 个（`mt.h` + `matrix_keypad.h` + `sparse-keymap.h` + `touchscreen.h` + `vivaldi-fmap.h` = 339 行）。也就是说这个目录里 **73% 的行数是芯片平台数据，不是框架**。

### §0.4 Kconfig

`drivers/input/Kconfig` 里 **14 个 `config` 选项**（`grep -cE '^(config|menuconfig) ' drivers/input/Kconfig` → 14），另外 `source` 进来 9 个子目录的 Kconfig。

| 选项 | 缺省 | 决定什么 | 谁 select 它 |
|---|---|---|---|
| `INPUT` | y（`default y`，`tristate` 且 `if EXPERT` 才可见） | 整个子系统。可以是 `m`——那时候 `input.ko` 是模块 | 只有 5 个 Kconfig、共 6 处 `select INPUT`；真正的常态是 `depends on INPUT`（全树 115 处） |
| `INPUT_LEDS` | y（`default y`，依赖 `LEDS_CLASS`） | 编不编 `input-leds.c` | 无人 select |
| `INPUT_FF_MEMLESS` | n | 编不编 `ff-memless.c` | 力反馈驱动 `select INPUT_FF_MEMLESS`（38 处 / 3 个 Kconfig） |
| `INPUT_SPARSEKMAP` | n | 编不编 `sparse-keymap.c` | 平台/热键驱动 `select INPUT_SPARSEKMAP`（22 处 / 6 个 Kconfig） |
| `INPUT_MATRIXKMAP` | n | 编不编 `matrix-keymap.c` | 矩阵键盘驱动 `select INPUT_MATRIXKMAP`（27 处 / 2 个 Kconfig） |
| `INPUT_VIVALDIFMAP` | n | 编不编 `vivaldi-fmap.c` | `select INPUT_VIVALDIFMAP`（4 处 / 2 个 Kconfig） |
| `INPUT_MOUSEDEV` | y（`default y`） | `/dev/input/mouseN` | 无人 select |
| `INPUT_MOUSEDEV_PSAUX` | n | 额外把 `/dev/psaux` 注册成 **misc 设备**（major 10 / minor 1 = `PSMOUSE_MINOR`），复用 mousedev 的 fops | 无人 select |
| `INPUT_MOUSEDEV_SCREEN_X` | 1024 | mousedev 把绝对坐标缩放到的虚拟屏宽 | — |
| `INPUT_MOUSEDEV_SCREEN_Y` | 768 | 同上，屏高 | — |
| `INPUT_JOYDEV` | n | `/dev/input/jsN` | 无人 select |
| `INPUT_EVDEV` | n | **`/dev/input/eventN`**。不开这个，用户态基本拿不到输入 | 无人 select |
| `INPUT_EVBUG` | n | 调试 handler，会把每个事件打到内核日志 | 无人 select |
| `INPUT_APMPOWER` | n | `KEY_SUSPEND` → APM 事件（依赖 `APM_EMULATION`） | 无人 select |

重跑「谁 select 了某个选项」：

```bash
for s in INPUT INPUT_FF_MEMLESS INPUT_SPARSEKMAP INPUT_MATRIXKMAP INPUT_VIVALDIFMAP; do
  printf '%-20s select %4s 行 / %3s 文件\n' "$s" \
    "$(grep -rnE "^[[:space:]]*select[[:space:]]+$s([[:space:]]|$)" --include=Kconfig . | wc -l)" \
    "$(grep -rlE "^[[:space:]]*select[[:space:]]+$s([[:space:]]|$)" --include=Kconfig . | wc -l)"
done
```

★ 这里必须用**行首锚定 + 词尾边界**的正则：写成 `grep -rn "select INPUT"` 会把 `select INPUT_FF_MEMLESS`、`select INPUT_POLLDEV` 之类全部算进 `INPUT` 名下。`INPUT` 这个名字是所有选项的前缀，子串包含在这里是必然踩的坑。

★ **`INPUT_EVDEV` 缺省是 n 且没有任何东西 select 它**——它只由 defconfig 打开。所以「板子起来了但用户态没有 `/dev/input/event*`」这个现象的第一嫌疑就是它，不是驱动。arm64 的 `arch/arm64/configs/defconfig` 里有 `CONFIG_INPUT_EVDEV=y`。

---

## §1 设备驱动侧 API

这是绝大多数读者要用的一组：你写了一个键盘/触摸屏/按钮驱动，要把事件送进内核。

### §1.1 分配、注册、释放

头文件：`include/linux/input.h`；实现：`drivers/input/input.c`

| API | 说明 | 备注 |
|---|---|---|
| `input_allocate_device()` | 分配并初始化一个 `input_dev` | 无参数。`__must_check`。失败返回 `NULL`（不是 `ERR_PTR`）。内部会 `dev_set_name()` 成 `input0`、`input1`…，并对 `input.ko` 加一次模块引用 |
| `devm_input_allocate_device(dev)` | 同上，但挂在 `dev` 的 devres 上 | ★ 推荐用法。它顺手把 `input->dev.parent = dev` 设好了，**驱动不要再改 parent**。同样返回 `NULL` 表示失败 |
| `input_free_device(dev)` | 释放**尚未注册**（或注册失败）的设备 | 注册成功之后不能调它，要调 `input_unregister_device()`。传 `NULL` 安全 |
| `input_register_device(dev)` | 注册到核心 | `__must_check`。返回 0 或负错误码。**调用后所有能力位、absinfo、keymap 都必须已经设好** |
| `input_unregister_device(dev)` | 注销 | 无返回值。对 devres 托管的设备也能调，见 `03-internals.md` Part A |
| `input_get_device(dev)` / `input_put_device(dev)` | 引用计数加减 | `static inline`，包装 `get_device`/`put_device`。传 `NULL` 安全 |
| `input_set_drvdata(dev, data)` / `input_get_drvdata(dev)` | 私有指针存取 | `static inline`，存在 `dev->dev` 的 driver_data 上——**和 `platform_set_drvdata()` 用的是同一个槽位的不同设备对象**，别混 |
| `input_reset_device(dev)` | 把设备状态复位：所有按键报释放、LED/蜂鸣/重复率重刷一遍 | 主要给 resume 和「抢占权释放」用。会拿 `dev->mutex` 和 `dev->event_lock` |

### §1.2 声明能力

注册前必须做的事。核心靠这些位图决定：哪些事件放行、哪些 handler 能匹配上、sysfs 里 `capabilities/` 显示什么。

| API | 说明 | 备注 |
|---|---|---|
| `input_set_capability(dev, type, code)` | 声明「我会发/收 type 类型的 code 事件」 | 同时置 `evbit` 和对应的分类位图，**这是首选写法**。越界会 `pr_err` + `dump_stack()` 然后**静默返回**，不报错给调用者 |
| `input_set_abs_params(dev, axis, min, max, fuzz, flat)` | 声明一根绝对轴并给出范围 | 内部会 `input_alloc_absinfo()`。分配失败时**直接返回，什么都不做**——错误在 `input_register_device()` 里才被发现 |
| `input_copy_abs(dst, dst_axis, src, src_axis)` | 从另一个设备的某根轴整份复制 absinfo | 给「触摸屏 + 手写笔共用坐标系」这类场合用。源设备没有 `EV_ABS` 或没设该轴时 `WARN_ON` 并返回 |
| `input_alloc_absinfo(dev)` | 手工分配 `absinfo` 数组 | 一般不用直接调。分配失败只 `dev_err` 不返回错误 |
| `input_abs_get_val/min/max/fuzz/flat/res(dev, axis)` | 读单个 absinfo 字段 | **宏 `INPUT_GENERATE_ABS_ACCESSORS` 生成的 12 个 inline**，`absinfo` 为空时一律返回 0（读不出「没设过」和「设成 0」的区别） |
| `input_abs_set_val/min/max/fuzz/flat/res(dev, axis, val)` | 写单个 absinfo 字段 | 同上。写之前会尝试分配，分配失败则静默丢弃 |
| `input_set_events_per_packet(dev, n_events)` | 告诉核心「我一帧大约发多少个事件」 | `static inline`，就是给 `hint_events_per_packet` 赋值。核心自己也会估一个，取两者较大值 |
| `input_enable_softrepeat(dev, delay, period)` | 打开软件自动重复 | 单位毫秒。**注册时若 `rep[]` 两项都为 0，核心会自动按 250/33 调它** |

★ 声明能力有两种写法，**它们不等价**：直接 `__set_bit(KEY_A, dev->keybit)` 只置了分类位，忘记置 `evbit` 里的 `EV_KEY` 就完全不通；`input_set_capability(dev, EV_KEY, KEY_A)` 两个位一起置。老驱动里大量 `__set_bit` 用法是因为它们在别处单独置了 `evbit`。近邻符号的区分见 `05-usage.md` §11。

### §1.3 上报事件

| API | 说明 | 备注 |
|---|---|---|
| `input_event(dev, type, code, value)` | **唯一的真实入口**，下面所有的都是它的包装 | 内部拿 `dev->event_lock`（`spin_lock_irqsave`），所以**任何上下文都能调**。事件类型不在 `evbit` 里就直接丢弃、不报错 |
| `input_report_key(dev, code, value)` | 报按键 | inline；`value` 被 `!!` 归一成 0/1 |
| `input_report_rel(dev, code, value)` | 报相对位移 | inline。**`value == 0` 会被核心丢掉**（相对轴上「没动」没有意义） |
| `input_report_abs(dev, code, value)` | 报绝对坐标 | inline。会走 fuzz 去抖动，值没变则丢弃 |
| `input_report_switch(dev, code, value)` | 报开关 | inline；`!!` 归一 |
| `input_report_ff_status(dev, code, value)` | 报力反馈效果状态 | inline |
| `input_sync(dev)` | 发 `EV_SYN`/`SYN_REPORT`，**表示一帧结束** | inline。★ 不调它，用户态一个事件都读不到——见 `08-feynman.md` §2 |
| `input_mt_sync(dev)` | 发 `EV_SYN`/`SYN_MT_REPORT` | inline。这是**老的 MT-A 协议**用的分隔符，新代码用 slot 协议，见 §3 |
| `input_set_timestamp(dev, timestamp)` | 给这一帧指定一个精确时间戳 | 传入的必须是 `CLOCK_MONOTONIC`；核心自己换算出 REAL 和 BOOT 三份 |
| `input_get_timestamp(dev)` | 取时间戳数组；没设过就地取一个 | 返回 `ktime_t *`，指向 `dev->timestamp[3]`。给 handler 用的，驱动一般不调 |
| `input_inject_event(handle, type, code, value)` | 从 **handler 侧**注入事件 | 需要 `input_handle`，不是 `input_dev`；设备被 grab 且不是自己 grab 的时候会被忽略。见 §2 |

### §1.4 键盘映射

| API | 说明 | 备注 |
|---|---|---|
| `input_get_keycode(dev, ke)` | 查某个扫描码当前映射到哪个 keycode | 转发给 `dev->getkeycode`，核心保证这个指针非空（注册时填了缺省实现） |
| `input_set_keycode(dev, ke)` | 改映射 | 同上。`ke->keycode > KEY_MAX` 直接 `-EINVAL`。改完如果旧 keycode 已不在键位图里而按键正按下，核心会**补一个释放事件** |
| `input_scancode_to_scalar(ke, scancode)` | 把 `input_keymap_entry` 里的变长扫描码转成 `unsigned int` | `ke->len` 只接受 1/2/4，其余返回 `-EINVAL` |

驱动要提供的是 `dev->keycode`/`keycodesize`/`keycodemax` 三件套（用缺省实现），或者自己实现 `dev->setkeycode`/`getkeycode`。**`setkeycode` 是在 `event_lock` 里被调的，不能睡眠**。

### §1.5 轮询设备

头文件：`include/linux/input.h`；实现：`drivers/input/input-poller.c`

| API | 说明 | 备注 |
|---|---|---|
| `input_setup_polling(dev, poll_fn)` | 把设备改造成轮询模式 | 必须在 `input_register_device()` **之前**调。它会替换 `dev->open`/`dev->close`——所以**驱动自己的 open/close 会被覆盖掉** |
| `input_set_poll_interval(dev, interval)` | 设轮询周期（毫秒） | 只有 `input_setup_polling()` 成功后才有意义（内部有 `WARN_ON(!dev->poller)`） |
| `input_set_min_poll_interval(dev, interval)` | 设用户态可调的下限 | 同上 |
| `input_set_max_poll_interval(dev, interval)` | 设上限 | 同上 |
| `input_get_poll_interval(dev)` | 读当前周期 | 返回毫秒；没有 poller 时返回 `-EINVAL` |

轮询设备会多出一组 sysfs 属性（`poll`、`min`、`max`），由 `input_poller_attribute_group` 挂上——见 `07-debug.md` §3。

### §1.6 状态查询

| API | 说明 | 备注 |
|---|---|---|
| `input_device_enabled(dev)` | 「现在有人在用且没被 inhibit」 | `EXPORT_SYMBOL_GPL`。★ **要求调用者已持有 `dev->mutex`**（内部 `lockdep_assert_held`），随手调会触发 lockdep 报警 |
| `input_match_device_id(dev, id)` | 拿一条 `input_device_id` 去比对设备 | 主要给 handler 和 `input_attach_handler()` 用；`drivers/input/misc/` 和 HID 里也有直接调的 |

`input_dev` 里可以直接读的状态字段（不需要 API）：`key[]`/`led[]`/`snd[]`/`sw[]` 是当前状态位图，`users` 是打开计数，`inhibited`、`going_away` 是标志。**改它们必须持 `event_lock`**，注释在 `include/linux/input.h` 的 kerneldoc 里写明了。

---

## §2 handler 侧 API

这一组是给「消费输入事件的模块」用的：evdev、joydev、mousedev、sysrq、VT 键盘、kgdboc、rfkill 都是 handler。**全树只有 12 个 `struct input_handler` 注册点，其中 6 个不在 `drivers/input/` 下**——名单和分布见 `02-datastructures.md` §3。所以这一组 API 的使用者极少，但每一个都在系统里很关键。

头文件：`include/linux/input.h`；实现：`drivers/input/input.c`

### §2.1 handler 与 handle 的注册

| API | 说明 | 备注 |
|---|---|---|
| `input_register_handler(handler)` | 把一个 handler 注册到核心，并对**当前已存在的**所有设备跑一遍匹配 | ★ **它不校验 `handler` 的任何字段**——`event`/`events`/`connect` 全空也照样注册成功。唯一的失败路径是 `mutex_lock_interruptible(&input_mutex)` 被信号打断，返回 `-EINTR`。逐个设备的匹配失败也只在日志里留一行 `failed to attach handler …`，不影响返回值 |
| `input_unregister_handler(handler)` | 注销，并断开它建立的所有 handle | 无返回值。内部按 `h_list` 逐个 `handler->disconnect()` |
| `input_register_handle(handle)` | **在 `connect()` 里调**，把这条 device↔handler 的连接挂进两张链表 | ★ `handle->handler->filter` 非空时挂到 `dev->h_list` 的**头部**，否则挂**尾部**——这就是过滤器优先级的全部机制，见 `08-feynman.md` §4 |
| `input_unregister_handle(handle)` | **在 `disconnect()` 里调** | 无返回值。用 `synchronize_rcu()` 等在途的事件分发走完 |
| `input_handler_for_each_handle(handler, data, fn)` | 遍历某个 handler 的所有 handle | 用 RCU 遍历，`fn` 返回非 0 就提前停止并把该值原样返回给调用者（全部返回 0 时它返回 0）。**`fn` 在 RCU 临界区里被调，不能睡眠**；反过来说这个函数本身可以在原子上下文里用 |

★ `connect()` 回调里的顺序是固定的：先分配自己的 client 结构 → 填好 `handle->dev`/`handler`/`name` → `input_register_handle()` → 建字符设备。搞反了会出现「字符设备已经能 open、但 handle 还没进链表」的窗口。骨架见 `05-usage.md` §6。

### §2.2 打开、关闭、抢占

| API | 说明 | 备注 |
|---|---|---|
| `input_open_device(handle)` | 打开设备（第一个打开者会触发 `dev->open()`） | 返回 0 或负错误码。★ 设备正在注销（`going_away`）时返回 **`-ENODEV`**。内部拿 `dev->mutex`，**会睡眠** |
| `input_close_device(handle)` | 关闭（最后一个关闭者触发 `dev->close()`） | 无返回值。会顺手把还按着的键报成释放 |
| `input_flush_device(handle, file)` | 转发到 `dev->flush()`；力反馈用它清掉某个 `file` 拥有的所有效果 | 设备没实现 `flush` 就返回 0。**不是**「刷新事件队列」 |
| `input_grab_device(handle)` | 独占设备：之后只有这个 handle 收得到事件 | ★ 已被别人 grab 时返回 **`-EBUSY`**（不是 `-EPERM`、不是 `-EAGAIN`）。这是 `EVIOCGRAB` 的实现 |
| `input_release_device(handle)` | 释放独占 | 无返回值。**不是自己 grab 的就什么都不做**，不报错 |

★ grab 之后被排除的不只是别的 evdev 客户端——**内核内部的 handler 一样收不到**。所以 `EVIOCGRAB` 一个键盘会让 VT 层和 SysRq 同时失聪；这个副作用是设计意图，不是 bug。

### §2.3 字符设备次设备号

| API | 说明 | 备注 |
|---|---|---|
| `input_get_new_minor(legacy_base, legacy_num, allow_dynamic)` | 分配一个 input 类的次设备号 | 返回次设备号或负错误码。`legacy_base < 0` 表示不要传统号段。★ 传统号段用完后是否回退到动态号段由 `allow_dynamic` 决定，**回退时不报错、不打日志**，用户态看到的设备名会突然变形 |
| `input_free_minor(minor)` | 归还次设备号 | 无返回值 |

号段边界写死在 `drivers/input/input.c` 里：`INPUT_MAX_CHAR_DEVICES` 为 1024，`INPUT_FIRST_DYNAMIC_DEV` 为 256。也就是**传统号段 0-255、动态号段 256-1023**。三个 handler 各自占的传统号段：joydev base 0 共 16 个、mousedev base 32 共 31 个、evdev base 64 共 32 个，三者都传 `allow_dynamic = true`。唯一传 `false` 的是 mousedev 的混合设备（`/dev/input/mice`），它必须固定拿到那一个号——完整号段表见 `04-extensions.md` §1。

### §2.4 设备类与 compat

| 符号 | 说明 | 备注 |
|---|---|---|
| `input_class`（变量） | input 类，`/sys/class/input/` 就是它 | `EXPORT_SYMBOL_GPL`，`extern` 声明在 `include/linux/input.h`。`.devnode` 回调让设备节点落在 `/dev/input/<name>` 而不是 `/dev/<name>` |
| `input_event_from_user(buffer, event)` | 从用户态读一条 `input_event`，必要时做 32↔64 位转换 | `EXPORT_SYMBOL_GPL`。★ 声明在 **`drivers/input/input-compat.h`**——这是子系统私有头文件，不在 `include/` 下。外部模块「能链接上但没法干净地包含声明」 |
| `input_event_to_user(buffer, event)` | 反向 | 同上 |
| `input_ff_effect_from_user(buffer, size, effect)` | 读一条 `ff_effect`，处理 compat 布局 | 同上 |
| `input_event_size()` | 一条事件在当前进程眼里有多大 | `static inline`，在同一个私有头文件里。`CONFIG_COMPAT` 打开时它依赖 `INPUT_COMPAT_TEST` 宏判断当前进程位宽 |

★ arm64 上 `struct input_event` 的时间戳字段在 64 位和 32 位进程眼里布局不同（`__kernel_ulong_t` vs `compat_ulong_t`），所以这套转换是必需的、不是历史包袱。细节见 `04-extensions.md` §4。

---

## §3 多点触控 API

头文件：`include/linux/input/mt.h`；实现：`drivers/input/input-mt.c`。**9 个导出符号全是普通 `EXPORT_SYMBOL`**（非 GPL）。

### §3.1 槽位的建立与拆除

| API | 说明 | 备注 |
|---|---|---|
| `input_mt_init_slots(dev, num_slots, flags)` | 分配 `dev->mt` 并声明一整套 MT 能力位 | 返回 0 或负错误码。★ 它会**替你调用 `input_set_abs_params()`** 声明 `ABS_MT_SLOT`、`ABS_MT_TRACKING_ID`，并按 flags 再声明一批。三个边界：`num_slots` 为 **0 时返回 0 但什么都不做**（不是报错，最容易被吞掉）；已初始化过且槽位数不同 → `-EINVAL`；`num_slots > 1024` → `-EINVAL` |
| `input_mt_destroy_slots(dev)` | 释放 | 无返回值。`input_unregister_device()` 里会自动调，**驱动一般不用管** |

`flags` 五个可选位（`mt.h`）：

| 标志 | 作用 |
|---|---|
| `INPUT_MT_POINTER` | 这是一块触控板（相对指针语义）：额外声明 `BTN_TOOL_*` 和 `INPUT_PROP_POINTER` |
| `INPUT_MT_DIRECT` | 这是一块触摸屏（绝对语义）：声明 `INPUT_PROP_DIRECT`、`BTN_TOUCH` |
| `INPUT_MT_DROP_UNUSED` | 每帧结束自动释放没被这一帧提到的槽位 |
| `INPUT_MT_TRACK` | 打开**内核内轨迹跟踪**：驱动只报触点坐标，槽位与 tracking id 由 `input_mt_assign_slots()` 分配 |
| `INPUT_MT_SEMI_MT` | 硬件只能报「包围盒」而非真实触点 |

### §3.2 每帧上报

| API | 说明 | 备注 |
|---|---|---|
| `input_mt_slot(dev, slot)` | 切到某个槽位（发 `ABS_MT_SLOT`） | `static inline`；后续的 `input_report_abs()` 都算在这个槽位上 |
| `input_mt_report_slot_state(dev, tool_type, active)` | 报当前槽位是活的还是空的 | `active` 为 false 时把 `ABS_MT_TRACKING_ID` 设成 -1。★ 它**依赖当前槽位是刚 `input_mt_slot()` 选中的那个** |
| `input_mt_report_slot_inactive(dev)` | 上一条的常用简写 | `static inline`，等价于 `报 (dev, 0, false)` |
| `input_mt_report_finger_count(dev, count)` | 根据触点数合成 `BTN_TOOL_FINGER`/`DOUBLETAP`/`TRIPLETAP`/`QUADTAP` | 给触控板用；触摸屏不需要 |
| `input_mt_report_pointer_emulation(dev, use_count)` | 从 MT 槽位合成单点的 `ABS_X`/`ABS_Y`/`BTN_TOUCH` | 让不懂 MT 协议的老用户态还能用。它**不检查任何 flag**，`use_count` 只决定要不要顺带合成 `BTN_TOOL_*` 手指数。★ `input_mt_sync_frame()` 已经替你调了（且自己算好 `use_count` = 有 `INPUT_MT_POINTER` 且没有 `INPUT_MT_SEMI_MT`），**驱动一般不用自己调** |
| `input_mt_sync_frame(dev)` | 一帧结束时的收尾：按 flags 做 drop-unused、指针模拟、翻转 frame 计数 | ★ **它不发 `SYN_REPORT`**，驱动仍然要自己调 `input_sync()`。这是最常见的漏一步 |
| `input_mt_drop_unused(dev)` | 手工释放本帧未提到的槽位 | 用了 `INPUT_MT_DROP_UNUSED` 就不用自己调 |

### §3.3 内核内轨迹跟踪

| API | 说明 | 备注 |
|---|---|---|
| `input_mt_assign_slots(dev, slots, pos, num_pos, dmax)` | 把这一帧的 N 个坐标匹配到已有槽位上（匈牙利算法的贪心变体） | 返回 0 或负错误码。`dmax` 是「同一个手指两帧之间最多能移动多远」，为 0 表示不限；`slots[]` 是输出 |
| `input_mt_get_slot_by_key(dev, key)` | 按驱动自己的 key（硬件触点 ID）找槽位 | ★ 返回的是 **`int` 槽位号，全满或没初始化过 MT 时返回 `-1`**——不是指针，用 `if (!slot)` 判断会把 0 号槽位当失败。找不到已有槽位时它会占一个空槽并记下 key。**给硬件自带触点 ID 的设备用**，这类设备不需要 `INPUT_MT_TRACK`。它依赖每帧都调 `input_mt_sync_frame()` |

`input_mt_get_value/set_value/is_active/is_used/new_trkid` 是 `mt.h` 里的 inline 辅助，直接操作 `input_mt_slot`；`input_is_mt_value(code)` / `input_is_mt_axis(code)` 判断一个 `ABS_*` 码是不是 MT 轴——**两者不等价**：前者排除 `ABS_MT_SLOT`，后者包含它。用错会让 slot 切换事件被当成坐标处理。

---

## §4 力反馈 API

实现：`drivers/input/ff-core.c`（6 个导出）+ `drivers/input/ff-memless.c`（1 个）。**这 7 个全是 `EXPORT_SYMBOL_GPL`**，声明都在 `include/linux/input.h` 末尾。

| API | 说明 | 备注 |
|---|---|---|
| `input_ff_create(dev, max_effects)` | 给设备装上 `ff_device`，容量 `max_effects` | 返回 0 或负错误码。三条 `-EINVAL`：`max_effects` 为 0、大于 `FF_MAX_EFFECTS`、`struct_size()` 溢出。★ **`FF_MAX_EFFECTS` 定义成 `FF_GAIN` 也就是 0x60 = 96**，不是某个圆整的大数——它借用的是效果码空间的上界。★ 调用**之前**要先把支持的效果类型置进 `dev->ffbit`（函数会把它复制进 `ff->ffbit`）；`EV_FF` 这个 evbit **它自己会置**，不用驱动动手 |
| `input_ff_create_memless(dev, data, play_effect)` | 上一条的封装：适用于「硬件只会持续震动、不会自己跑波形」的设备 | 返回 0 或负错误码。内部固定申请 16 个效果槽位，并注册一个定时器把复杂效果算成一串 `play_effect()` 调用 |
| `input_ff_destroy(dev)` | 拆掉 | 无返回值。`input_unregister_device()` 会自动调 |
| `input_ff_event(dev, type, code, value)` | 把 `EV_FF`/`EV_FF_STATUS` 事件转成 `playback`/`set_gain`/`set_autocenter` | 返回 0 或负错误码。★ **它是给 `dev->event` 回调用的**，而 `input_ff_create()` 里已经写了 `dev->event = input_ff_event`、`dev->flush = input_ff_flush`——所以驱动**不要再自己赋值**，赋成自己的函数会把力反馈整条路断掉 |
| `input_ff_upload(dev, effect, file)` | 上传/替换一个效果，并记住是哪个 `file` 拥有它 | 返回 0 或负错误码。`effect->id` 为 -1 时分配新槽位并回填 id，**槽位全满返回 `-ENOSPC`**；设备没有 `EV_FF` → `-ENOSYS`；效果类型不在 `ffbit` 里 → `-EINVAL`。这是 `EVIOCSFF` 的实现 |
| `input_ff_erase(dev, effect_id, file)` | 擦除一个效果 | ★ 只有**当初上传它的那个 `file`** 能擦，别人来擦返回 **`-EACCES`**；id 越界或那个槽位本来没主人 → `-EINVAL`；设备没有 `EV_FF` → `-ENOSYS`。这是 `EVIOCRMFF` 的实现 |
| `input_ff_flush(dev, file)` | 擦掉某个 `file` 拥有的全部效果 | **恒返回 0**，逐个槽位的失败被吞掉。核心通过 `dev->flush` 在 `close()` 时自动调它，**驱动不用自己接**。★ `file` 传 `NULL` 的语义是「擦掉所有效果，不看所有权」——所有权检查里那一步是 `if (file && …)` |

★ 力反馈是 input 子系统里**唯一有「所有权」概念的资源**：`ff_device` 里的 `effect_owners[]` 记的是 `struct file *`。整个子系统的其他部分（除了 grab）都没有 per-file 状态。这个不对称的来由见 `08-feynman.md` §6。

`input_ff_upload` / `input_ff_erase` / `input_ff_flush` 在树里的调用者只有 `drivers/input/evdev.c`、`drivers/input/misc/uinput.c` 和 `drivers/hid/hid-input.c` 三个文件——重跑：

```bash
grep -rn --include=*.c -w -e input_ff_upload -e input_ff_erase \
     -e input_ff_flush -e input_ff_event . | grep -v drivers/input/ff-core.c
```

---

## §5 辅助库

这五个库互不依赖、各自由一个 Kconfig 控制，**用不到的直接跳过**。共 16 个导出符号。

### §5.1 稀疏键盘映射

头文件：`include/linux/input/sparse-keymap.h`；实现：`drivers/input/sparse-keymap.c`；开关 `CONFIG_INPUT_SPARSEKMAP`。5 个导出全是普通 `EXPORT_SYMBOL`。

适用场景：硬件给的扫描码**又稀疏又不规则**（笔记本热键：0x81、0x93、0xf0…），做成 256 项的数组太浪费。

| API | 说明 | 备注 |
|---|---|---|
| `sparse_keymap_setup(dev, keymap, setup)` | 把一张 `key_entry` 表装到设备上，并声明对应能力位 | 返回 0 或负错误码。内部会**复制**这张表，所以调用者的 `keymap` 可以是 `__initdata`。`setup` 是每项的回调，可以为 `NULL` |
| `sparse_keymap_entry_from_scancode(dev, code)` | 按扫描码查表 | 找不到返回 `NULL` |
| `sparse_keymap_entry_from_keycode(dev, code)` | 按 keycode 反查 | 同上 |
| `sparse_keymap_report_entry(dev, ke, value, autorelease)` | 上报一个已查到的表项 | `autorelease` 为 true 时立刻补一个释放事件（硬件只给「按下」脉冲时用） |
| `sparse_keymap_report_event(dev, code, value, autorelease)` | 查表 + 上报，一步到位 | 返回 bool：**false 表示这个扫描码不在表里**。★ 但它**在返回 false 之前还是会上报一个 `KEY_UNKNOWN`**（带 `autorelease`，作为调试线索）。所以「返回 false = 什么都没发生」是错的：用户态会看到 `KEY_UNKNOWN` 事件，`evtest` 里显示成 `KEY_UNKNOWN (240)` |

表项类型（`sparse-keymap.h`）：`KE_END`(0) 结束标记、`KE_KEY`(1) 按键、`KE_SW`(2) 开关、`KE_VSW`(3) 值来自事件而非表项的开关、`KE_IGNORE`(4) 显式忽略。★ `KE_IGNORE` 和「表里没有」不一样：前者返回 true（认领了但不上报），后者返回 false。

### §5.2 矩阵键盘映射

头文件：`include/linux/input/matrix_keypad.h`；实现：`drivers/input/matrix-keymap.c`；开关 `CONFIG_INPUT_MATRIXKMAP`。

| API | 说明 | 备注 |
|---|---|---|
| `matrix_keypad_build_keymap(keymap_data, keymap_name, rows, cols, keymap, input_dev)` | 建 keycode 表：给了 `keymap_data` 就用它，否则从固件读 `keymap_name` 属性 | 普通 `EXPORT_SYMBOL`。返回 0 或负错误码。`keymap` 传 `NULL` 时它用 `devm_kcalloc()` 自己分配（挂在 **`input_dev->dev.parent`** 的 devres 上）并填进 `input_dev->keycode`。它顺手置 `EV_KEY`、清掉 `KEY_RESERVED`。行列超界的表项 → `-EINVAL`。★ **`input_dev->dev.parent` 必须已经设好**，否则 `WARN_ON` + `-EINVAL`——用 `devm_input_allocate_device()` 的驱动天然满足，用 `input_allocate_device()` 的要自己先赋 parent |
| `matrix_keypad_parse_properties(dev, rows, cols)` | 从固件读 `keypad,num-rows` / `keypad,num-columns` | `EXPORT_SYMBOL_GPL`。返回 0 或负错误码；任一为 0 → `-EINVAL` |
| `matrix_keypad_parse_of_params` | **它不是函数**，是 `#define matrix_keypad_parse_of_params matrix_keypad_parse_properties` | 改名期留下的兼容宏。全树的调用点已经全部用新名，这个宏只剩兼容作用——不要在新代码里用 |

★ 每个 keymap 项的编码格式是 `KEY(row, col, keycode)` 宏，打包成 `row << 24 | col << 16 | keycode`。写 DTS 时的十六进制形式和常见错误见 `06-devicetree.md` §3。

### §5.3 触摸屏公共属性

头文件：`include/linux/input/touchscreen.h`；实现：`drivers/input/touchscreen.c`。3 个导出全是普通 `EXPORT_SYMBOL`，**没有独立 Kconfig**（`CONFIG_INPUT_TOUCHSCREEN` 控制的是整个子目录，这个文件跟着 `INPUT` 一起编）。

| API | 说明 | 备注 |
|---|---|---|
| `touchscreen_parse_properties(input, multitouch, prop)` | 读 `touchscreen-size-x/y`、`touchscreen-min-x/y`、`touchscreen-inverted-x/y`、`touchscreen-swapped-x-y`、`touchscreen-fuzz-*`，并**回填到 absinfo** | ★ 必须在 `input_mt_init_slots()` **之后**调（`multitouch` 为 true 时它改的是 `ABS_MT_*`）。`prop` 可以为 `NULL`，那时只改 absinfo、不留变换参数 |
| `touchscreen_set_mt_pos(pos, prop, x, y)` | 按 `prop` 里的翻转/交换参数变换一对坐标，写进 `input_mt_pos` | 普通导出函数（不是 inline）。给用 `input_mt_assign_slots()` 的驱动用：它只算坐标、不上报 |
| `touchscreen_report_pos(input, prop, x, y, multitouch)` | 变换 + 直接上报 | 大多数驱动用这个。`multitouch` 决定报 `ABS_MT_POSITION_*` 还是 `ABS_X/Y` |

★ 这三个函数只在 `prop` 被 `touchscreen_parse_properties()` 填过之后才会真的做变换。**顺序搞反（先 report 后 parse）不会报错，只会让屏幕的 X/Y 一直是反的**——DTS 明明写了 `touchscreen-inverted-x` 却不生效，第一嫌疑就是这个。

### §5.4 ChromeOS Vivaldi 功能行

头文件：`include/linux/input/vivaldi-fmap.h`；实现：`drivers/input/vivaldi-fmap.c`；开关 `CONFIG_INPUT_VIVALDIFMAP`。39 行，只有一个导出符号。

| API | 说明 | 备注 |
|---|---|---|
| `vivaldi_function_row_physmap_show(data, buf)` | 把 `vivaldi_data` 里的功能行物理映射输出成 sysfs 内容 | `EXPORT_SYMBOL_GPL`。驱动自己声明 `DEVICE_ATTR` 并在 show 里转发给它。★ **`data->num_function_row_keys` 为 0 时返回 0 字节**——sysfs 文件存在但读出来是空的 |

`VIVALDI_MAX_FUNCTION_ROW_KEYS` 为 24。全树引用 `vivaldi_data` 或这个函数的有 5 个文件：`drivers/input/keyboard/cros_ec_keyb.c`、`drivers/input/keyboard/atkbd.c`、`drivers/hid/hid-vivaldi.c`、`drivers/hid/hid-vivaldi-common.c`、`drivers/hid/hid-google-hammer.c`。arm64 Chromebook 走的是 `cros_ec_keyb`。

### §5.5 轮询库

见 §1.5。它归在辅助库里是因为实现独立（`drivers/input/input-poller.c`），但 API 是设备驱动侧用的，所以放在了 §1。**没有独立 Kconfig**——它无条件编进 `input.ko`。

---

## §6 错误码语义

input 核心返回的错误码不多，但同一个码在不同函数里含义不同。下表只收**能返回给驱动作者的**那些；用户态看到的 `errno`（主要来自 evdev 的 ioctl）在 `07-debug.md` §5。

| 错误码 | 出现在 | 含义 |
|---|---|---|
| `-EINVAL` | `input_register_device` | 声明了 `EV_ABS` 但 `absinfo` 是空的。内核日志里有 `Absolute device without dev->absinfo, refusing to register` |
| `-EINVAL` | `input_set_keycode` | keycode 超过 `KEY_MAX`，或扫描码长度不是 1/2/4 |
| `-EINVAL` | `input_ff_create` | `max_effects` 为 0、大于 `FF_MAX_EFFECTS`(96)，或结构体尺寸溢出 |
| `-EINVAL` | `matrix_keypad_parse_properties` | 固件里行数或列数为 0 |
| `-EINVAL` | `input_get_poll_interval` | 设备没走 `input_setup_polling()` |
| `-ENODEV` | `input_open_device` | 设备正在注销（`going_away` 已置） |
| `-EBUSY` | `input_grab_device` | 已经被别的 handle 独占 |
| `-EACCES` | `input_ff_erase` / `input_ff_upload` | 想动别人上传的效果（`check_effect_access()` 里唯一的一处） |
| `-ENOMEM` | 各 alloc 路径 | 内存不足。★ `input_set_abs_params()` 和 `input_abs_set_*()` **不返回它**，它们静默失败，错误推迟到注册时 |
| `-ENOSPC` | `input_get_new_minor` | 次设备号用完 |
| `NULL`（不是 `ERR_PTR`） | `input_allocate_device` / `devm_input_allocate_device` | 分配失败。★ 用 `IS_ERR()` 检查会漏掉 |

13 个核心文件里的错误码分布（`grep -c` 口径：含该串的行数，不是返回点数）：

| 错误码 | 行数 | 主要来源 |
|---|---|---|
| `-EFAULT` | 60 | 几乎全在 `evdev.c` / `mousedev.c` / `joydev.c` / `input-compat.c` 的 copy_to/from_user 路径上 |
| `-EINVAL` | 57 | 参数校验，分散 |
| `-ENOMEM` | 27 | 分配失败 |
| `-ENODEV` | 14 | 设备正在消失 |
| `-ENOSYS` | 4 | 两处在 `ff-core.c`（设备没有 `EV_FF` 就想上传/擦除效果），两处在 `evdev.c`（`EVIOCGREP`/`EVIOCSREP` 但设备没有 `EV_REP`）。**不是「没实现这个 ioctl」的意思** |
| `-EAGAIN` | 3 | 非阻塞读且队列空 |
| `-ENXIO` | 2 | `input_mt_assign_slots()` 在没开轨迹跟踪的设备上被调；`input-leds.c` 发现设备一个 LED 都没有 |
| `-EBUSY` | 2 | `input_grab_device()` 里一处；`evdev.c` 自己那层还有一处先行判断（同一个客户端重复 grab） |
| `-ENOSPC` | 1 | 次设备号耗尽 |
| `-ENOENT` | 1 | `evdev.c` 的 `str_to_user()`：`EVIOCGNAME`/`GPHYS`/`GUNIQ` 撞上空指针字段。★ 这就是 `EVIOCGUNIQ` 在多数设备上返回 `ENOENT` 的原因——设备没填 `uniq`，不是 ioctl 不支持 |
| `-EACCES` | 1 | 力反馈所有权检查，`ff-core.c` 里唯一一处 |

重跑（★ 必须显式列文件，用 `drivers/input/*.c` 通配会把驱动目录之外的数字混进来）：

```bash
cd drivers/input
F="input.c evdev.c joydev.c mousedev.c ff-core.c ff-memless.c input-mt.c
   input-poller.c input-leds.c sparse-keymap.c matrix-keymap.c touchscreen.c
   input-compat.c"
for e in EFAULT EINVAL ENOMEM ENODEV ENOSYS EAGAIN ENXIO EBUSY ENOSPC ENOENT EACCES; do
  printf '%-8s %3s\n' "-$e" "$(grep -h -- "-$e" $F | wc -l)"
done
```

---

## §7 长得像 API 但你不能用的符号

判据还是那条：**`EXPORT_SYMBOL*` + 头文件声明**，两个都要有。

| 符号 | 缺哪一条 | 结果 |
|---|---|---|
| `input_handle_event(dev, type, code, value)` | 有全局符号、**没有 `EXPORT_SYMBOL`**；声明在 `drivers/input/input-core-private.h` | 它是 `input_event()` 去掉加锁之后的内核，**要求调用者已持 `dev->event_lock`**。全树只有 `input.c`（11 处）和 `input-mt.c`（3 处）调它，两个文件都编进 `input.ko`。模块里调用会链接失败 |
| `input_mt_release_slots(dev)` | 同上，声明在同一个私有头文件 | 核心在 `input_dev_release_keys()` 里用来把所有槽位报成释放 |
| `input_dev_poller_finalize` / `_start` / `_stop`（3 个） | 声明在 `drivers/input/input-poller.h`，无导出 | poller 的内部实现，`input.c` 在注册和 open/close 里调。驱动要用的是 §1.5 那 5 个。同一个头文件里还 `extern` 了 `input_poller_attribute_group` |
| `input_event_from_user` 一家（3 个） | **有 `EXPORT_SYMBOL_GPL`**，但声明在私有头文件 `drivers/input/input-compat.h` | 树内的 `evdev.c` / `uinput.c` 用得很顺；树外模块链接得上、却拿不到干净的声明。见 §2.4 |
| `input_class` | 两条都满足，**但它是变量不是函数** | 直接 `class_create` 一个自己的 input 类不会有 `/dev/input/` 前缀。要挂进 input 类就用这个变量 |
| `INPUT_GENERATE_ABS_ACCESSORS` | 是宏，展开出 12 个 inline | 想知道 `input_abs_get_res()` 在哪定义时 `grep` 不到函数体——它由宏生成。★ **这是索引工具的典型盲区**，见 `99-evidence.md` §2.5 |

★ 这一节的六行里，前四行是「核心内部实现」，后两行是「你能用但容易用错形式」。真正会让人在编译期栽跟头的只有 `input_handle_event`：它有 kerneldoc、名字最像公开 API、和 `input_event()` 只差一个词。近邻符号的完整区分表在 `05-usage.md` §11。

---

## 本篇引用的其他文档

| 去处 | 看什么 |
|---|---|
| `02-datastructures.md` | `input_dev` / `input_handler` / `input_handle` 的字段、全局链表与三把锁、12 个 handler 注册点的名单 |
| `03-internals.md` | 本篇每个 API 的内部实现与行号区间、注册与注销时间线 |
| `04-extensions.md` | 21 个互不调用的子框架分节详解（三个字符设备 handler、serio、力反馈、input-compat、rmi4、uinput、input-mt、input-poller、input-leds、touchscreen 属性、三个 keymap 库、gameport、三个只消费的 handler），次设备号段表，以及篇末「框架 × 职责 × 挂载点 × arm64」和「全树使用统计」两张汇总表 |
| `05-usage.md` | 各角色的代码骨架、近邻符号区分 |
| `06-devicetree.md` | `touchscreen-*`、`linux,keymap`、`keypad,num-*` 属性怎么写 |
| `07-debug.md` | 错误码分诊、evbug、procfs / sysfs 出口 |
| `08-feynman.md` | 为什么是双向注册而不是总线、`input_sync()` 为什么不能省 |
