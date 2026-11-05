---
title: 调试方法
---

# input 子系统调试手册

**基线**：Linux 6.1.158（`f6e38ae624cf`），arm64。树上打了 PREEMPT_RT 回移补丁，但 `drivers/input/`、`include/linux/input*` 未被触及（验证命令见 `99-evidence.md` §1）。

**读这一篇的前提**：知道 `input_dev` / `input_handler` / `input_handle` 三者的关系（`02-datastructures.md` §1.0）和事件从 `input_event()` 走到 `read()` 的路径（`03-internals.md` Part A）。DT 属性配错了怎么查在 `06-devicetree.md` §8，本篇不重复。

## §0 先分诊：这个子系统的调试面长什么样

★★ **input 子系统的调试设施分布极不均匀**：核心几乎什么都没有，能力全在**用户态接口**和**个别驱动**里。先把这个形状认清，才不会去找不存在的东西。

| 你想看什么 | 用哪个入口 | 在哪一节 | 需要什么开关 |
| --- | --- | --- | --- |
| 系统里有哪些 input 设备、谁绑了谁 | `/proc/bus/input/devices` | §4.1 | `CONFIG_PROC_FS` |
| 一个设备声明了哪些事件类型/轴/键 | `/sys/class/input/inputN/capabilities/*` | §3.2 | 无 |
| 一条 `ABS_*` 的 min/max/fuzz/flat/resolution | `EVIOCGABS`（`evtest`） | §9.2 | `CONFIG_INPUT_EVDEV` |
| 实际收到了哪些事件 | `evtest` / `evemu-record` | §9 | `CONFIG_INPUT_EVDEV` |
| **内核侧**看到了哪些事件（不经 evdev） | `evbug` 模块 | §1.2 | `CONFIG_INPUT_EVBUG` |
| 键值映射表当前是什么 | `EVIOCGKEYCODE_V2` | §9.2 | `CONFIG_INPUT_EVDEV` |
| 某个驱动内部状态 | 该驱动自己的 sysfs / debugfs | §3.3、§6 | 逐驱动不同 |
| 触摸屏原始电容图 | RMI4 F54 的 `/dev/videoN` | §6.2 | `CONFIG_RMI4_F54` |
| rmi4 内部流程 | `rmi_core` 的 `debug_flags` 模块参数 | §2.3 | `CONFIG_RMI4_CORE` |
| 某个错误码从哪儿来的 | §8 的错误码分诊表 | §8 | 无 |

### §0.1 ★ 三件"以为有、其实没有"的东西

这三条是本篇最重要的结论，因为它们决定了**不要往哪个方向找**。

| 设施 | 核心有没有 | 全树情况 | 依据 |
| --- | --- | --- | --- |
| **tracepoint** | **零个** | 整个 `drivers/input/` 只有一组，在 `drivers/input/keyboard/applespi_trace.h`，属于 Apple SPI 键盘驱动 | §5 |
| **debugfs** | **零个** | 只有 2 个驱动用：`drivers/input/touchscreen/edt-ft5x06.c`、`drivers/input/keyboard/applespi.c` | §6 |
| **故障注入** | **零个** | 全树 `drivers/input/` 里 `fault_inject` / `should_fail` / `DECLARE_FAULT_ATTR` 命中数为 0 | §7 |

重跑这三条：

```bash
grep -rln 'TRACE_EVENT\|DEFINE_EVENT' drivers/input/          # → 1 个文件
grep -rln 'debugfs_create' drivers/input/                     # → 2 个文件
grep -rni 'fault_inject\|should_fail\|DECLARE_FAULT' drivers/input/ | wc -l   # → 0
```

★ **而且那唯一一组 tracepoint 在 arm64 上编不出来。** `CONFIG_KEYBOARD_APPLESPI` 的依赖是 `depends on ACPI && EFI` + `depends on SPI` + `depends on X86 || COMPILE_TEST` + `depends on LEDS_CLASS`（`drivers/input/keyboard/Kconfig:75-78`）。arm64 上只有 `COMPILE_TEST` 能让它编过，而 `COMPILE_TEST` 的构建产物不上板。所以对 arm64 而言，**input 子系统的 tracepoint 覆盖率是 0**。

这不是"文档没写全"，是这个子系统的实际形状：事件流的观察点被设计在**用户态**（`/dev/input/eventN` 就是那个观察点），内核侧只留了一个全量 printk 的 `evbug`。

### §0.2 按任务找章节

| 任务 | 去 |
| --- | --- |
| 设备没出现在 `/dev/input/` | §4.1 → §8.2 |
| 设备出现了但收不到事件 | §1.2（evbug 分段）→ §3.4（`inhibited`）→ §9.1 |
| 事件有但值不对 | §9.2（`EVIOCGABS`）→ `06-devicetree.md` §8.5 C 档 |
| 按键卡住不松开 | §3.4、§9.3 |
| 想知道某个 `-EINVAL` 是谁返回的 | §8.1 |
| 想看日志但什么都没打印 | §2.1（可见性等级表） |
| 触摸屏坐标飘 / 想看原始数据 | §6.1（edt-ft5x06）、§6.2（RMI4 F54） |
| 想给这个子系统加观测点 | §5.2、§7.2 |

## §1 编译期开关

### §1.1 全清单

`drivers/input/Kconfig` 一共 14 个 config（`grep -n '^config \|^menuconfig ' drivers/input/Kconfig` → 14 行），其中**只有一个是为调试存在的**：

| 开关 | 作用 | 调试价值 | 位置 |
| --- | --- | --- | --- |
| `CONFIG_INPUT` | 整个子系统；同时无条件带上 `touchscreen.o` | 关掉就什么都没有 | `drivers/input/Kconfig:8` |
| `CONFIG_INPUT_EVDEV` | `/dev/input/eventN` | **调试必开**：没有它 `evtest` 无从下手 | `:146` |
| `CONFIG_INPUT_EVBUG` | 把所有事件打到 syslog | 唯一的内核侧事件观察器，见 §1.2 | `:155` |
| `CONFIG_INPUT_MOUSEDEV` | `/dev/input/mouseN` 老接口 | 3 个可写 module_param，见 §1.3 | `:89` |
| `CONFIG_INPUT_JOYDEV` | `/dev/input/jsN` | — | `:133` |
| `CONFIG_INPUT_LEDS` | 把 `EV_LED` 接到 LED 子系统 | 键盘灯不亮时先看这个开关 | `:28` |
| `CONFIG_INPUT_FF_MEMLESS` | 无内存力反馈的软件模拟 | `pr_debug` 11 处全在这里，见 §2.2 | `:41` |
| `CONFIG_INPUT_MATRIXKMAP` | `matrix-keymap.o` | in-tree 驱动自动 select | `:67` |
| `CONFIG_INPUT_SPARSEKMAP` / `_VIVALDIFMAP` | 另两种 keymap 库 | — | `:54` / `:80` |
| `CONFIG_INPUT_MOUSEDEV_PSAUX` | `/dev/psaux` | — | `:103` |
| `CONFIG_INPUT_MOUSEDEV_SCREEN_X` / `_Y` | mousedev 的缺省屏幕尺寸 | — | `:113` / `:123` |
| `CONFIG_INPUT_APMPOWER` | 按电源键走 APM | arm64 不涉及 | `:169` |

跨目录的三个与调试直接相关的：

| 开关 | 作用 | 位置 |
| --- | --- | --- |
| `CONFIG_RMI4_F54` | RMI4 原始电容图，走 V4L2 | `drivers/input/rmi4/Kconfig:111` |
| `CONFIG_RMI4_F34` | 固件升级 + `bootloader_id` 等 sysfs | `drivers/input/rmi4/Kconfig:93` |
| `CONFIG_DEBUG_FS` | 门住 `edt-ft5x06` 的 debugfs 节点（`drivers/input/touchscreen/edt-ft5x06.c:625`） | 内核通用 |

### §1.2 `CONFIG_INPUT_EVBUG`：唯一的内核侧事件观察器

`drivers/input/evbug.c`（80 行）是一个**完整的 `input_handler`**，它的 `id_table` 是：

```c
/* drivers/input/evbug.c:74-77 —— 原文 */
static const struct input_device_id evbug_ids[] = {
	{ .driver_info = 1 },	/* Matches all devices */
	{ },			/* Terminating zero entry */
};
```

`.driver_info = 1` 而 `flags` 为 0，于是 `input_match_device()` 对任何设备都返回匹配（匹配规则见 `03-internals.md` §A5）。所以 `modprobe evbug` 之后**系统里每一个 input 设备都会多一个 handle**，每一条事件都会走一次 `evbug_event()`：

```c
/* drivers/input/evbug.c:22-27 —— 原文 */
static void evbug_event(struct input_handle *handle, unsigned int type, unsigned int code, int value)
{
	printk(KERN_DEBUG pr_fmt("Event. Dev: %s, Type: %d, Code: %d, Value: %d\n"),
	       dev_name(&handle->dev->dev), type, code, value);
}
```

四件用之前必须知道的事：

★ **它用的是 `printk(KERN_DEBUG …)`，不是 `dev_dbg`。** 所以它**不受 `CONFIG_DYNAMIC_DEBUG` 控制**——编进去就一定打印，唯一的门是 console loglevel。`dmesg` 里始终能看到，控制台上要 `dmesg -n 8`。这与 §2.1 表里其他 debug 级消息不是一回事。

★ **Kconfig 自己写了这是安全问题**：`drivers/input/Kconfig:161-162` 原文 "it's also a security threat - your keypresses include your passwords, of course"。生产环境不要留。

★ **它会拖慢事件路径**：`evbug_event` 挂在 `input_handler.event` 上（`:82`），由 `input_pass_values()` 在持 `dev->event_lock` 的临界区里同步调用（`03-internals.md` §A8.5）。每条事件一次 printk，触摸屏满帧上报时足以改变时序——**它是观察器，也是干扰源**。

★ **它连 `connect`/`disconnect` 也打印**（`:50-52`、`:66-67`），所以还能当"handler 绑定过程"的观察器：热插拔一个 USB 键盘，日志里会出现 `evbug: Connected device: input5 (Xxx at usb-…)`。这是 `/proc/bus/input/devices` 的 `H:` 行的动态版本。

用法：

```bash
modprobe evbug
dmesg -w | grep evbug        # 另一个终端
# 用完立刻卸掉
rmmod evbug
```

### §1.3 可写的 module_param

整个 `drivers/input/` 顶层只有 3 个 module_param，全在 mousedev，全是 0644（运行时可写）：

| 参数 | 默认 | 位置 | 运行时路径 |
| --- | --- | --- | --- |
| `xres` | `CONFIG_INPUT_MOUSEDEV_SCREEN_X` | `drivers/input/mousedev.c:40` | `/sys/module/mousedev/parameters/xres` |
| `yres` | `CONFIG_INPUT_MOUSEDEV_SCREEN_Y` | `drivers/input/mousedev.c:44` | 同上 `…/yres` |
| `tap_time` | 200 | `drivers/input/mousedev.c:48` | 同上 `…/tap_time` |

rmi4 另有一个，见 §2.3。重跑：`grep -rn 'module_param' drivers/input/*.c drivers/input/rmi4/*.c`。

## §2 日志：可见性等级

### §2.1 等级表

★★ **"这条消息一定会打印吗"有五种不同答案**，而它们在源码里长得几乎一样。查日志之前先定位自己要找的那条属于哪一档，否则会得出"内核什么都没说"的错误结论。

| 档 | 形式 | 默认可见性 | 怎么打开 | 核心 16 个文件里的数量 |
| --- | --- | --- | --- | --- |
| 1 | `dev_err` / `pr_err` | **总是** 进 dmesg | — | `dev_err` 13、`pr_err` 13 |
| 2 | `dev_warn` / `pr_warn` | **总是** 进 dmesg | — | `dev_warn` 2、`pr_warn` 1 |
| 3 | `pr_info` | **总是** 进 dmesg | — | 3 |
| 4 | `dev_dbg` / `pr_debug` | **默认不打印** | `CONFIG_DYNAMIC_DEBUG` + 运行时开；或该文件 `-DDEBUG` | `dev_dbg` 7、`pr_debug` 11 |
| 5 | `printk(KERN_DEBUG …)` | **总是**进 dmesg，控制台要 loglevel 8 | `dmesg -n 8` | 3（全在 `evbug.c`） |

统计范围是 `ls drivers/input/*.c` 的 16 个文件：`apm-power.c`、`evbug.c`、`evdev.c`、`ff-core.c`、`ff-memless.c`、`input-compat.c`、`input-leds.c`、`input-mt.c`、`input-poller.c`、`input.c`、`joydev.c`、`matrix-keymap.c`、`mousedev.c`、`sparse-keymap.c`、`touchscreen.c`、`vivaldi-fmap.c`。重跑：

```bash
for m in dev_err dev_warn dev_dbg pr_err pr_warn pr_info pr_debug printk; do
  echo "$m -> $(grep -how "\b$m\b" drivers/input/*.c | wc -l)"
done
```

★★ **核心里一个 `_once` / `_ratelimited` 变体都没有。** 全树 `drivers/input/` 的 `dev_warn_once` 只有 3 处、`dev_err_ratelimited` 11 处、`dev_warn_ratelimited` 10 处、`dev_err_once` **0** 处，全部在具体驱动里，共 **11 个文件**：`drivers/input/touchscreen/` 下 6 个（`ad7879.c`、`ads7846.c`、`edt-ft5x06.c`、`goodix_fwupload.c`、`hycon-hy46xx.c`、`zet6223.c`）、`drivers/input/keyboard/adp5588-keys.c`、`drivers/input/keyboard/applespi.c`、`drivers/input/misc/cm109.c`、`drivers/input/mouse/elan_i2c_core.c`、`drivers/input/tablet/pegasus_notetaker.c`。重跑：`grep -rlw -e dev_warn_once -e dev_err_ratelimited -e dev_warn_ratelimited drivers/input/ --include=*.c`。含义很实际：**核心的错误消息不会被去重也不会被限速**，一个每帧都失败的路径会把 dmesg 刷满；而个别触摸屏驱动的 I2C 读失败是限速的，看到"只有几条"不代表只失败了几次。

重跑：

```bash
for m in dev_err_once dev_warn_once dev_err_ratelimited dev_warn_ratelimited; do
  echo "$m -> $(grep -rhow "$m" drivers/input/ --include=*.c | wc -l)"
done
```

### §2.2 `dev_dbg` / `pr_debug` 到底在哪 7+11 处

数量少到可以全列，这比"用 dynamic_debug 全局打开"有用得多——你会发现**核心的关键路径上根本没有 debug 消息**。

| 位置 | 消息 | 什么时候有用 |
| --- | --- | --- |
| `drivers/input/input.c:2052-2053` | `%s: dropping reference to %s\n` | 追 `input_dev` 引用计数 |
| `drivers/input/input.c:2314-2315` | `%s: unregistering device %s\n` | 拔设备/卸驱动时的时序 |
| `drivers/input/input.c:2477-2478` | `%s: registering %s with devres.\n` | 确认走的是 devres 托管路径 |
| `drivers/input/ff-core.c:104` | `invalid or not supported effect type in upload\n` | 力反馈 `EVIOCSFF` 返回 `-EINVAL` 时的唯一线索 |
| `drivers/input/ff-core.c:112` | `invalid or not supported wave form in upload\n` | 同上 |
| `drivers/input/ff-core.c:240` | `flushing now\n` | 力反馈设备关闭 |
| `drivers/input/joydev.c:811-812` | （joydev 绑定相关） | 老 joystick 接口 |
| `drivers/input/ff-memless.c` 11 处 | `calculating next timer` / `no actions` / `timer set` / `difference = %d` … | 软件力反馈的定时器计算，`:118`、`:138`、`:141`、`:162`、`:180`、`:181` 等 |

★ **`ff-core.c:104` 和 `:112` 这两条特别值得记住**：`input_ff_upload()` 在这两个分支返回 `-EINVAL`（`:105`、`:113`），而**除了这两条 `dev_dbg` 之外没有任何输出**。用户态拿到 `EVIOCSFF` 失败却看不到原因，就是这个原因。打开办法：

```bash
echo 'file ff-core.c +p' > /sys/kernel/debug/dynamic_debug/control
# 确认生效
grep ff-core /sys/kernel/debug/dynamic_debug/control
```

### §2.3 rmi4 的第六档：`debug_flags`

rmi4 自带一套独立的调试输出机制，**不属于上表任何一档**：

```c
/* drivers/input/rmi4/rmi_bus.c:20-22、24-40 —— 摘录 */
static int debug_flags;
module_param(debug_flags, int, 0644);
MODULE_PARM_DESC(debug_flags, "control debugging information");

void rmi_dbg(int flags, struct device *dev, const char *fmt, ...)
{
	…
	if (flags & debug_flags) {
		…
		dev_printk(KERN_DEBUG, dev, "%pV", &vaf);
	}
}
```

四个标志位（`drivers/input/rmi4/rmi_bus.h:193-196`）：

| 标志 | 值 | 覆盖什么 |
| --- | --- | --- |
| `RMI_DEBUG_CORE` | `BIT(0)` = 1 | 总线/驱动核心 |
| `RMI_DEBUG_XPORT` | `BIT(1)` = 2 | I2C / SPI / SMB 传输层 |
| `RMI_DEBUG_FN` | `BIT(2)` = 4 | function 驱动（F03/F11/F12…） |
| `RMI_DEBUG_2D_SENSOR` | `BIT(3)` = 8 | 二维传感器坐标处理 |

```bash
# 全开（1|2|4|8 = 15）
echo 15 > /sys/module/rmi_core/parameters/debug_flags
dmesg -n 8
```

模块名是 `rmi_core`（`drivers/input/rmi4/Makefile:2`：`obj-$(CONFIG_RMI4_CORE) += rmi_core.o`），**不是** `rmi4_core`。

★ **`rmi_dbg` 用 `dev_printk(KERN_DEBUG, …)` 而不是 `dev_dbg`**（`rmi_bus.c:36`），所以 `dynamic_debug` 对它无效——只有 `debug_flags` 这一道门。反过来说，编进内核就一定能开，不需要 `CONFIG_DYNAMIC_DEBUG`。

★ `RMI_DEBUG_*` 定义在**私有头** `drivers/input/rmi4/rmi_bus.h` 里，`include/linux/rmi.h` 里没有——树外 rmi4 function 驱动用不了 `rmi_dbg`（`rmi_dbg` 本身是 `EXPORT_SYMBOL_GPL`，`rmi_bus.c:40`，但没有公开声明）。

### §2.4 `WARN_ON` 与 `BUG_ON`：6 + 1 处

| 位置 | 条件 | 触发说明什么 |
| --- | --- | --- |
| `drivers/input/matrix-keymap.c:161` | `!input_dev->dev.parent` | 驱动没设 parent 就调 `matrix_keypad_build_keymap()`。★ 对照：`touchscreen_parse_properties()` 同样的前置条件**不检查**（`06-devicetree.md` §7.5） |
| `drivers/input/input.c:531-532` | src 没有 `EV_ABS` 或源轴未置位 | `input_copy_abs()` 被误用 |
| `drivers/input/input.c:2297` | `!list_empty(&dev->h_list)` | 所有 handler 的 `disconnect()` 都跑完了，还有 handle 挂着 → 某个 handler 的 `disconnect` 没摘干净 |
| `drivers/input/input.c:2566` | `!list_empty(&handler->h_list)` | 同上，反方向（注销 handler 时） |
| `drivers/input/input.c:2119`、`:2504` | `devres_destroy()` 失败 | devres 托管状态不一致 |
| **`drivers/input/evdev.c:108`** | `type == EV_SYN` | ★ **`BUG_ON`，会 panic**。`__evdev_flush_queue()` 不允许被要求冲刷 `EV_SYN`——这是内部不变量，用户态触发不到 |

★ `drivers/input/evdev.c:108` 是整个 `drivers/input/` 顶层唯一一处 `BUG_ON`（全树 `drivers/input/` 有 35 处，其余全在驱动里）。它守的是"调用者不会传 `EV_SYN`"这个内部约定，不是可配置行为。

## §3 sysfs

### §3.0 三个目录，一张图

```
/sys/class/input/inputN/          ← input_dev 本体（input_dev_attr_groups，drivers/input/input.c:1651-1657）
├── name  phys  uniq  modalias  properties  inhibited     ← input_dev_attr_group（:1539-1551）
├── poll  max  min                                        ← input_poller_attribute_group（条件可见）
├── id/       bustype vendor product version              ← input_dev_id_attr_group（:1568-1579）
├── capabilities/  ev key rel abs msc led snd ff sw       ← input_dev_caps_attr_group（:1633-1649）
└── eventM/ … （evdev 的字符设备，另一个 device）

/sys/class/input/eventM/          ← evdev 侧，只有 dev/uevent 等通用属性

/sys/devices/platform/gpio-keys/  ← 驱动私有，见 §3.3
```

★ 三个 group 里只有第一个没有 `.name`，所以 `name`/`inhibited` 直接落在 `inputN/` 下，`bustype` 落在 `inputN/id/` 下。**`poller` 那一组也没有 `.name`**，所以 `poll`/`max`/`min` 也在 `inputN/` 顶层——见 §3.2。

### §3.1 只读属性全清单

| 文件 | 内容 | 实现 |
| --- | --- | --- |
| `name` | `dev->name`，与 `/proc/bus/input/devices` 的 `N: Name=` 同源 | `drivers/input/input.c:1357-1367` 的 `INPUT_DEV_STRING_ATTR_SHOW` 宏 |
| `phys` | 物理位置串，驱动自己填，可能为空 | 同上宏 |
| `uniq` | 唯一 ID（序列号一类），多数驱动不填 | 同上宏 |
| `modalias` | `input:b0003v046Dp…` 形式，供 udev 匹配 | `drivers/input/input.c:1487` |
| `properties` | `dev->propbit` 的 16 进制打包，`INPUT_PROP_DIRECT` 等 | `drivers/input/input.c:1501` |
| `id/bustype` | `%04x`，`BUS_I2C`=0x18、`BUS_HOST`=0x19 等 | `drivers/input/input.c:1553-1567` |
| `id/vendor` `id/product` `id/version` | 同上 `%04x` | 同上 |
| `capabilities/ev` | 支持哪些事件类型（位图） | `drivers/input/input.c:1610-1621` 的宏 |
| `capabilities/key` `rel` `abs` `msc` `led` `snd` `ff` `sw` | 各类型内支持哪些 code（位图） | 同上宏，共 9 个 |

★★ **`capabilities/*` 和 `properties` 打印的是位图，不是数值。** 格式由 `input_print_bitmap()` 产生：按 `unsigned long` 分组、**高位组在前**、以空格分隔、前导全零组被跳过（`drivers/input/input.c:1581-1608`）。所以 `capabilities/abs` 是 `2650000 3` 这种样子——它回答"有没有 ABS_X"，**不回答"ABS_X 的量程是多少"**。

★★ **量程/fuzz/flat/resolution 在 sysfs 里根本不存在。** 唯一的读取途径是 `EVIOCGABS(code)` ioctl，也就是 `evtest` 的输出（见 §9）。这条区分是 `06-devicetree.md` §7 那五棵决策树的分岔点：轴"有没有"看 sysfs，参数"对不对"只能看 `evtest`。

### §3.2 `poll` / `max` / `min`：条件出现的三个文件

```c
/* drivers/input/input-poller.c:203-210 —— 原文 */
static umode_t input_poller_attrs_visible(struct kobject *kobj,
					  struct attribute *attr, int n)
{
	struct device *dev = kobj_to_dev(kobj);
	struct input_dev *input = to_input_dev(dev);

	return input->poller ? attr->mode : 0;
}
```

★ **`is_visible` 返回 0 时文件不创建**，所以 `inputN/` 下有没有 `poll` 直接告诉你这个设备是不是轮询式的——比翻驱动源码快。判据是 `dev->poller` 非空，也就是驱动调过 `input_setup_polling()`。

全树有 **29 个调用点、跨 29 个文件**（`grep -rn 'input_setup_polling' --include=*.c . | grep -v '^drivers/input/input-poller.c'` → 29；`grep -rln` → 30 个文件，减掉 `input-poller.c` 自己 = 29），其中 `drivers/input/` 下 22 个，另有 `drivers/hwmon/applesmc.c`、`drivers/misc/lis3lv02d/lis3lv02d.c`、`drivers/macintosh/ams/ams-input.c` 和 `drivers/platform/x86/` 下 4 个。

三个文件的语义：

| 文件 | 权限 | 单位 | 语义 |
| --- | --- | --- | --- |
| `poll` | **0644**（`drivers/input/input-poller.c:180`） | ms | 当前轮询间隔，可写 |
| `max` | 0444（`:191`） | ms | 上界 |
| `min` | 0444（`:201`） | ms | 下界 |

写 `poll` 的 setter 语义（`drivers/input/input-poller.c:146-178`），四件事按顺序发生：

1. `kstrtouint(buf, 0, &interval)` 失败 → 返回 `kstrtouint` 的错误码（不是统一的 `-EINVAL`）
2. `interval < poll_interval_min` 或 `> poll_interval_max` → **`-EINVAL`**，`:159` 与 `:162` 两处
3. 拿 `input->mutex`，赋值 `poller->poll_interval = interval`
4. ★ **只有在 `input_device_enabled(input)` 为真时才重启定时器**（`:171-175`）：先 `cancel_delayed_work_sync()`，再 `poll_interval > 0` 时重排。设备没被打开时，值写进去了但下次打开才生效

★ **写 0 是合法的**（只要 `poll_interval_min` 是 0），效果是**停止轮询**——`:174` 的 `if (poller->poll_interval > 0)` 不会重排 work。文件里读回来是 0，设备安静，但看起来一切正常。这是"触摸屏突然没反应"的一个非典型原因。

### §3.3 驱动侧的 sysfs

**gpio-keys**（4 个文件，挂在 platform 设备上，不在 `inputN/` 下）：

| 文件 | 权限 | 语义 | 位置 |
| --- | --- | --- | --- |
| `keys` | 0444 | 本设备声明的所有 `EV_KEY` code，`%*pbl` 列表格式（`:222`） | `drivers/input/keyboard/gpio_keys.c:321` |
| `switches` | 0444 | 同上，`EV_SW` | `:322` |
| `disabled_keys` | **0644** | 当前被屏蔽的按键；写入即屏蔽 | `:350-352` |
| `disabled_switches` | **0644** | 同上，`EV_SW` | `:353-355` |

写 `disabled_keys` 的 setter（`gpio_keys_attr_store_helper()`，`drivers/input/keyboard/gpio_keys.c:241-296`）：

- 输入格式是 **`bitmap_parselist` 的列表语法**，即 `114,115` 或 `114-116`，**不是十六进制位图**。解析失败 → 返回 `bitmap_parselist` 的错误码
- ★ **先校验再生效**：`bitmap_subset(bits, bitmap, n_events)` 不成立就 `-EINVAL`（`:259-262`）——你不能屏蔽一个本设备没声明的键，写 `9999` 会被拒
- 失败路径统一 `goto out` 释放临时位图，不会留下半生效状态

```bash
# 屏蔽 KEY_VOLUMEUP(115) 和 KEY_VOLUMEDOWN(114)
echo 114,115 > /sys/devices/platform/gpio-keys/disabled_keys
cat /sys/devices/platform/gpio-keys/disabled_keys
# 全部恢复：写空列表
echo > /sys/devices/platform/gpio-keys/disabled_keys
```

★ 这是排查"按键抖动/误触"时把单个键摘出去的正规手段，比 `inhibited`（§3.4，整个设备一起关）粒度细。

**RMI4**（9 个属性，全在 `rmi4-XX` 子设备目录下）：

| 来源 | 文件 | 权限 |
| --- | --- | --- |
| `drivers/input/rmi4/rmi_f01.c:274`、`:286`、`:298`、`:310`、`:325` | `manufacturer_id`、`date_of_manufacture`、`product_id`、`firmware_id`、`package_id` | 0444 |
| `drivers/input/rmi4/rmi_f34.c:328`、`:350` | `bootloader_id`、`configuration_id` | 0444 |
| `drivers/input/rmi4/rmi_f34.c:489` | `update_fw` | **0200（只写）** |
| `drivers/input/rmi4/rmi_f34.c:512` | `update_fw_status` | 0444 |

★ `update_fw` 是**固件升级触发器**，不是调试文件。调试时只读前 7 个（读出来就是芯片自报的型号/固件版本，用于确认"驱动认到的是不是我以为的那颗片子"）。

**serio**（`drivers/input/serio/serio.c:440-471`）：`id/{type,proto,id,extra}` 0444，`modalias`/`description`/`firmware_id` 0444，`drvctl` **0200**（写驱动名强制绑定），`bind_mode` **0644**（`auto`/`manual`）。★ arm64 上 serio 通常没有设备——`CONFIG_SERIO_I8042` 依赖 `ARCH_MIGHT_HAVE_PC_SERIO`，详见 `06-devicetree.md` §0.2。

### §3.4 `inhibited`：核心里第二个可写文件

`drivers/input/input.c:1515-1537`，`DEVICE_ATTR_RW`。setter 的语义比名字暗示的重：

| 写入 | 走哪个函数 | 副作用 |
| --- | --- | --- |
| `1` / `y` / `on` | `input_inhibit_device()`，`drivers/input/input.c:1847-1873` | 释放所有 MT 槽位、释放所有按下的键、**补发一个 `SYN_REPORT`**、`input_dev_toggle(dev, false)`（关 LED / 停自动重复）、`dev->inhibited = true`；**总是返回 0** |
| `0` / `n` / `off` | `input_uninhibit_device()`，`:1875-1902` | 重开设备；**可能失败**——它会传播 `dev->open()` 的返回值 |
| 其他 | `strtobool()` 失败 → `-EINVAL`（`:1524-1525`） | — |

抑制生效后，事件在 `drivers/input/input.c:243-244` 被丢弃——**在 handler 之前**，所以 `evtest` 什么也看不到、`evbug` 也不打印。

★★ 三条容易踩的：

- **"我的键卡住了，写了 inhibited 就好了"** —— 因为 `input_inhibit_device()` 主动释放了所有按下的键。这是症状被抹掉，不是原因被修好
- **"莫名多了一个 `SYN_REPORT`"** —— 就是抑制路径补发的那个（用来让用户态看到"所有键都松了"这个一致状态）
- **写 `0` 可能失败**：设备侧 `open()` 出错时 `echo 0 > inhibited` 会返回错误，此时 `inhibited` 读回来仍是 `1`。写 sysfs 一定要看返回值

```bash
D=/sys/class/input/input3
echo 1 > $D/inhibited && cat $D/inhibited   # 1
echo 0 > $D/inhibited || echo "解除失败，看 dmesg"
```

## §4 procfs：`/proc/bus/input/`

`CONFIG_PROC_FS=y` 时创建（`drivers/input/input.c:1319-1341`），两个文件，都是 0 权限位——`proc_create("devices", 0, …)`（`:1327`），procfs 见 `mode & S_IALLUGO` 为 0 就补 `S_IRUGO`（`fs/proc/generic.c:570-571`），最终是 `0444`。

### §4.1 `devices` 逐字段

`input_devices_seq_show()`，`drivers/input/input.c:1192-1234`。每个设备一段，字段固定：

| 前缀 | 来源行 | 内容 | 调试用法 |
| --- | --- | --- | --- |
| `I:` | `:1198-1199` | `Bus=%04x Vendor=… Product=… Version=…` | `Bus=0018` 是 I2C、`0019` 是 HOST；与 `id/*` 同源 |
| `N:` | `:1201` | `Name="…"`，NULL 时打空串 | ★ 名字为空说明驱动没设 `input->name` |
| `P:` | `:1202` | `Phys=…` | 多数 DT 驱动不填 |
| `S:` | `:1203` | `Sysfs=` `kobject_get_path()` 的结果 | ★ **从这里跳到 sysfs 的正确路径**，比自己拼 `input3` 靠得住 |
| `U:` | `:1204` | `Uniq=…` | 少数驱动填 |
| `H:` | `:1205-1209` | `Handlers=` 遍历 `dev->h_list` 打 `handle->name` | ★★ **最有用的一行**：`event3` 在这里出现才说明 evdev 绑上了 |
| `B: PROP=` | `:1211` | `dev->propbit` 位图 | `INPUT_PROP_DIRECT` 等 |
| `B: EV=` | `:1213` | 支持的事件类型位图 | 有没有 `EV_ABS` |
| `B: KEY/REL/ABS/MSC/LED/SND/FF/SW=` | `:1214-1229` | **仅当对应 `EV_*` 位置位时才打印这一行** | 缺行 = 该类型没声明，不是"打印漏了" |

★ 位图格式由 `input_seq_print_bitmap()` 产生（`:1166-1190`）：**高位 `unsigned long` 在前、空格分隔、前导全零组跳过、全零时打单个 `0`**。与 `capabilities/*` 同一套编码，所以 §3.1 那条"位图不是数值"在这里同样成立。

★ `H: Handlers=` 后面每个名字**带一个尾随空格**（`:1207` 的 `"%s "`），写脚本解析时注意。

### §4.2 `handlers`

`input_handlers_seq_show()`，`drivers/input/input.c:1285-1298`：

```
N: Number=0 Name=evdev
N: Number=1 Name=evbug
```

两个可选后缀，都是判断性信息：

- `(filter)` —— `handler->filter` 非空（`:1292-1293`）。这个 handler 能**吞掉**事件让后面的 handler 看不到
- `Minor=%d` —— `handler->legacy_minors` 为真时打印（`:1294-1295`），`mousedev`/`joydev` 这类老接口才有

★ **`evbug` 出现在这个列表里，就说明它已经挂到每个设备上了**（§1.2）。生产系统上看到它 = 有人忘了 `rmmod`。

### §4.3 `devices` 支持 poll

`input_proc_devices_poll()`，`drivers/input/input.c:1115-1124`：用 `input_devices_state` 计数器 + `file->f_version` 比较，设备增删时 `input_wakeup_procfs_readers()`（`:1109-1113`）唤醒。所以**可以 `poll()` 这个文件来等设备热插拔**，不必轮询 `cat`。

```bash
# 观察热插拔（udevadm 更常用，但这条不依赖 udev）
while :; do cat /proc/bus/input/devices | grep -c '^I:'; sleep 1; done
```

## §5 tracepoint：核心一个都没有

### §5.1 普查结论

```bash
# 核心 + 所有子目录，一个 TRACE_EVENT 都不在 input 核心里
grep -rn 'TRACE_EVENT\|DEFINE_EVENT\|DECLARE_EVENT_CLASS' drivers/input/
# 只有一个文件命中：drivers/input/keyboard/applespi_trace.h
grep -rn 'trace_' --include=*.c drivers/input/ | grep -v applespi
# → 空
```

| 范围 | tracepoint 数 |
| --- | --- |
| `drivers/input/*.c`（核心 16 个文件） | **0** |
| `drivers/input/touchscreen/`、`keyboard/`（除 applespi）、`misc/`、`mouse/`、`joystick/`、`serio/`、`rmi4/`、`tablet/` | **0** |
| `drivers/input/keyboard/applespi_trace.h` | 8（7 个 `DEFINE_DUMP_MESSAGE_EVENT` + 1 个 `TRACE_EVENT`） |

也就是说：**`input_event()` / `input_pass_values()` / `input_handle_event()` 这条主路径上没有任何 tracepoint**。想用 `trace-cmd` 看事件流是做不到的——`03-internals.md` §A8 那条时间线在 ftrace 里完全不可见，只能用 `function_graph` 追函数（见 §5.3）。

### §5.2 唯一的那一组：applespi，arm64 上编不出来

`drivers/input/keyboard/applespi_trace.h`，`TRACE_SYSTEM applespi`（`:10`）：

| tracepoint | 类型 | 字段 |
| --- | --- | --- |
| `applespi_tp_ini_cmd`、`applespi_backlight_cmd`、`applespi_caps_lock_cmd`、`applespi_keyboard_data`、`applespi_touchpad_data`、`applespi_unknown_data`、`applespi_bad_crc` | 7 个共享 `dump_message_template`（`:20-49`） | `evt_type`、`pkt_type`、`len`、`__dynamic_array(u8, buf, len)`——**整包原始字节** |
| `applespi_irq_received` | 独立 `TRACE_EVENT`（`:68`） | `evt_type`、`pkt_type` |

★ **一个 `TP_CONDITION` 都没有**——这一组是无条件触发的，开了就全打。而 `dump_message_template` 会 `memcpy` 整个报文进 ring buffer（`:39`），所以打开 `applespi_keyboard_data` 等于把击键内容原样写进 trace——和 `evbug` 同一类安全问题（§1.2）。

★★ **arm64 上这一组不存在。** `CONFIG_KEYBOARD_APPLESPI` 的四条 `depends on`（`drivers/input/keyboard/Kconfig:75-78`）：

```
depends on ACPI && EFI
depends on SPI
depends on X86 || COMPILE_TEST
depends on LEDS_CLASS
```

第三条把它钉在 x86 上（`COMPILE_TEST` 只为编译覆盖率存在，不产出可用设备）。所以对本文档的目标架构而言，**整个 input 子系统的 tracepoint 覆盖率是 0**。这条已登记进 `99-evidence.md` §2.7 的 arm64 裁剪记录。

### §5.3 没有 tracepoint 时用什么

按"想看什么"选：

| 想看 | 手段 | 命令 |
| --- | --- | --- |
| 每一条事件（类型/码/值） | `evbug`（内核侧，§1.2）或 `evtest`（用户态，§9） | `modprobe evbug` |
| 事件到达的时间戳/延迟 | `evtest` 的输出自带 `struct input_event` 里的时间戳 | `evtest /dev/input/event3` |
| 核心函数的调用关系 | ftrace `function_graph`——**不需要 tracepoint** | 见下 |
| 某个函数被调了几次 | ftrace `function` + `funcgraph` 计数 | `cat /sys/kernel/tracing/trace_stat/function0` |

```bash
cd /sys/kernel/tracing
echo function_graph > current_tracer
echo input_event > set_graph_function
echo 1 > tracing_on
# 按一下键
echo 0 > tracing_on
cat trace
```

★ `function_graph` 能看到 `input_event → input_handle_event → input_pass_values → evdev_event` 这条链，代价是它会 trace 所有 CPU 上所有实例，触摸屏满帧时数据量很大。配 `set_ftrace_pid` 或改用 `input_inhibit_device` 先把别的设备静音（§3.4）。

★ **`kprobe` 是这里的现实替代品**：核心函数大多不是 `static inline`，可以直接挂。

```bash
cd /sys/kernel/tracing
# 抓 input_event 的三个参数（type/code/value 是第 2/3/4 个参数）
echo 'p:ievent input_event type=%x1:u32 code=%x2:u32 value=%x3:s32' > kprobe_events
echo 1 > events/kprobes/ievent/enable
cat trace_pipe
```

★ 参数寄存器名（`%x1` 等）是 arm64 的写法；`input_event` 的第一个参数是 `dev`，所以 `type` 落在 `%x1`。这条命令依赖 `CONFIG_KPROBE_EVENTS`。

## §6 debugfs：两个驱动有，核心没有

```bash
grep -rln 'debugfs_create' drivers/input/
# → drivers/input/touchscreen/edt-ft5x06.c
#   drivers/input/keyboard/applespi.c
```

核心（`drivers/input/*.c`）**零个 debugfs 节点**。applespi 那个同样受 `depends on X86` 限制（§5.2），所以 arm64 上**只有 edt-ft5x06 一个驱动有 debugfs**。

### §6.1 `edt-ft5x06`：厂测模式与原始电容

目录名是 `dev_driver_string(&client->dev)`（`drivers/input/touchscreen/edt-ft5x06.c:1338`），也就是 `/sys/kernel/debug/edt_ft5x06/`。四个节点（`:845-853`）：

| 节点 | 权限 | 内容 |
| --- | --- | --- |
| `num_x` | `S_IRUSR`（0400） | 传感器 X 方向通道数，`debugfs_create_u16` |
| `num_y` | 0400 | Y 方向通道数 |
| `mode` | `S_IRUSR \| S_IWUSR`（0600） | 0 = 工作模式，1 = 厂测模式 |
| `raw_data` | 0400 | 原始电容矩阵，`num_x × num_y` 个 `u16` |

`mode` 的 setter 语义（`edt_ft5x06_debugfs_mode_set()`，`:736-754`）：

- `mode > 1` → **`-ERANGE`**（不是 `-EINVAL`），`:741-742`
- 值没变时**什么也不做**（`:746` 的 `if (mode != tsdata->factory_mode)`）——重复写 1 是安全的
- 切换过程持 `tsdata->mutex`，会真的给芯片发命令（`edt_ft5x06_factory_mode()` / `edt_ft5x06_work_mode()`）

★★ **`raw_data` 必须先进厂测模式**，否则读到 `-EIO`：

```c
/* drivers/input/touchscreen/edt-ft5x06.c:776-779 —— 原文 */
	if (!tsdata->factory_mode || !tsdata->raw_buffer) {
		error = -EIO;
		goto out;
	}
```

```bash
D=/sys/kernel/debug/edt_ft5x06
cat $D/num_x $D/num_y
echo 1 > $D/mode              # 进厂测模式；此时触摸不再上报事件
hexdump -C $D/raw_data | head
echo 0 > $D/mode              # 必须切回来，否则触摸屏一直是哑的
```

★ **进了厂测模式触摸屏就不上报事件了**——这是"我一调试触摸屏就彻底没反应"的一个真实成因。忘了写回 0 的后果是设备看起来坏了，`evtest` 一片安静，而 `/proc/bus/input/devices` 里一切正常。

★ **设计债**：`:625` 打开的是 `#ifdef CONFIG_DEBUG_FS`，而 `:878` 的收尾注释写的是 `/* CONFIG_DEBUGFS */`——**内核里没有 `CONFIG_DEBUGFS` 这个符号**（`grep -rn 'CONFIG_DEBUGFS\b' ` 只在这一行命中）。注释误导，代码本身是对的。已登记进 `99-evidence.md` 的设计债清单。

### §6.2 RMI4 F54：原始电容图走 V4L2，不走 debugfs

这是本子系统最不像 input 的调试出口：**F54 把原始电容矩阵当成一路视频流导出**。

```c
/* drivers/input/rmi4/rmi_f54.c:724 —— 原文 */
	ret = video_register_device(&f54->vdev, VFL_TYPE_TOUCH, -1);
```

★ `VFL_TYPE_TOUCH` 对应的设备节点名是 **`v4l-touch`**（`drivers/media/v4l2-core/v4l2-dev.c:926-928`），所以路径是 `/dev/v4l-touch0`，**不是 `/dev/video0`**。

七种报告类型（`enum rmi_f54_report_type`，`drivers/input/rmi4/rmi_f54.c:74-83`，值不连续）：

| 值 | 枚举 | V4L2 input 名（`:85-94`） |
| --- | --- | --- |
| 0 | `F54_REPORT_NONE` | `Unknown` |
| 1 | `F54_8BIT_IMAGE` | `Normalized 8-Bit Image` |
| 2 | `F54_16BIT_IMAGE` | `Normalized 16-Bit Image` |
| 3 | `F54_RAW_16BIT_IMAGE` | `Raw 16-Bit Image` |
| 9 | `F54_TRUE_BASELINE` | `True Baseline` |
| 19 | `F54_FULL_RAW_CAP` | `Full Raw Capacitance` |
| 20 | `F54_FULL_RAW_CAP_RX_OFFSET_REMOVED` | `Full Raw Capacitance RX Offset Removed` |

★ **选哪种报告用 `VIDIOC_S_INPUT`**（`rmi_f54_vidioc_s_input()`，`drivers/input/rmi4/rmi_f54.c:447-450`），即 `v4l2-ctl --set-input=N`。图像格式固定 `V4L2_COLORSPACE_RAW`、每像素 `sizeof(u16)`、宽 = rx 数、高 = tx 数（`:437-442`）。

```bash
v4l2-ctl -d /dev/v4l-touch0 --list-inputs
v4l2-ctl -d /dev/v4l-touch0 --set-input=19
v4l2-ctl -d /dev/v4l-touch0 --stream-mmap --stream-count=1 --stream-to=cap.raw
```

编译门（`drivers/input/rmi4/Kconfig:111-115`）：

```
config RMI4_F54
	bool "RMI4 Function 54 (Analog diagnostics)"
	depends on VIDEO_DEV=y || (RMI4_CORE=m && VIDEO_DEV=m)
	select VIDEOBUF2_VMALLOC
	select RMI4_F55
```

★ 两条容易卡住的：`depends on` 里的 **`VIDEO_DEV=y` 与 `RMI4_CORE=m && VIDEO_DEV=m` 两种组合之外都编不出来**（比如 `RMI4_CORE=y` + `VIDEO_DEV=m` 不行）；`select RMI4_F55` 说明 F54 会连带把 F55 拉进来。

## §7 故障注入：本子系统没有

结论先给：**`drivers/input/` 里没有任何故障注入机制。**

```bash
grep -rn 'should_fail\|fault_attr\|DECLARE_FAULT_ATTR\|setup_fault_attr' drivers/input/
# → 空（命中的 fail_* 全是 goto 标签，不是故障注入框架）
```

没有 `CONFIG_FAIL_*` 开关、没有 `/sys/kernel/debug/fail_*/` 目录、没有 `should_fail()` 调用点。所以想验证"分配失败时的错误路径对不对""I2C 读失败时驱动会不会崩"，只能用下面这些替代手段。

| 想注入的故障 | 替代手段 | 说明 |
| --- | --- | --- |
| 内存分配失败 | 通用 `CONFIG_FAILSLAB` + `/sys/kernel/debug/failslab/` | 不是 input 专有，但能覆盖 `input_allocate_device()`（`drivers/input/input.c:2010` 起）里的 `kzalloc` |
| 设备突然消失 | `echo <驱动名> > /sys/bus/i2c/drivers/<drv>/unbind` | 走真实的 `remove()` 路径，能查拆除时序（`03-internals.md` §A12） |
| 设备"不上报" | `echo 1 > inhibited`（§3.4） | 在 handler 之前丢事件，用户态看起来像硬件死了 |
| 轮询停摆 | `echo 0 > poll`（§3.2） | 只对有 poller 的 29 个驱动有效 |
| 单个按键失效 | `echo <code> > disabled_keys`（§3.3） | 只对 gpio-keys |
| 上报"不存在的键" | `/dev/uinput` 造一个虚拟设备（§9.4） | 唯一能在**用户态**构造任意事件序列的手段 |
| 触摸屏进哑模式 | `echo 1 > /sys/kernel/debug/edt_ft5x06/mode`（§6.1） | 只对 edt-ft5x06 |

★ **`uinput` 是本子系统事实上的故障注入器**：它让你不碰硬件就构造出"MT 槽位没配对""SYN 缺失""坐标越界"这类畸形序列，用来验证上层（libinput / 应用）的健壮性。它注入的是**事件**而不是**失败**，这是它和 `FAILSLAB` 一类框架的根本区别。

## §8 错误码分诊

### §8.0 统计范围与总表

★ 下面所有数字的统计范围是**核心的 7 个文件**，显式列出：`drivers/input/input.c`、`input-mt.c`、`input-poller.c`、`ff-core.c`、`touchscreen.c`、`matrix-keymap.c`、`input-compat.c`。**不含** `evdev.c` / `mousedev.c` / `joydev.c` / `uinput.c` 这些 handler，也不含任何驱动。重跑：

```bash
F="drivers/input/input.c drivers/input/input-mt.c drivers/input/input-poller.c \
   drivers/input/ff-core.c drivers/input/touchscreen.c drivers/input/matrix-keymap.c \
   drivers/input/input-compat.c"
for e in EINVAL ENOMEM EFAULT ENODEV ENOSYS ENXIO ENOSPC EBUSY EACCES; do
  echo "$e -> $(grep -how -- "-$e" $F | wc -l)"
done
```

| 错误码 | 命中行数 | 真正的返回点 | 语义 |
| --- | --- | --- | --- |
| `-EINVAL` | 33 | **32** | 参数/状态不合法，见 §8.1 |
| `-ENOMEM` | 15 | 15 | 分配失败，见 §8.2 |
| `-EFAULT` | 9 | 9 | 用户态指针拷贝失败，**全在 `input-compat.c`**，见 §8.3 |
| `-ENODEV` | 3 | **2** | 设备已消失 / handler 不匹配，见 §8.4 |
| `-ENOSYS` | 2 | 2 | 力反馈设备没有 `upload`/`erase` 回调 |
| `-ENXIO` | 1 | 1 | MT 槽位没初始化 |
| `-ENOSPC` | 1 | 1 | 力反馈效果槽满 |
| `-EBUSY` | 1 | 1 | 设备已被别人 grab |
| `-EACCES` | 1 | 1 | 力反馈效果不属于你 |

★★ **"命中行数"不等于"返回点数"**，本表里有两处差值，都必须逐行打开才看得出来：

- `-EINVAL` 33 → 32：`drivers/input/input-mt.c:36` 是 `copy_abs()` 上方**注释**里的一句话
- `-ENODEV` 3 → 2：`drivers/input/input.c:1061` 是 `if (error && error != -ENODEV)` 这个**比较**，不是返回

所有"命中行数"都来自 `grep -how`，它数的是**含该串的行**。凡是要拿它当"返回点数"用，都得先排掉注释行和比较表达式。

★ `drivers/input/touchscreen.c` 在这张表里**一个错误码都没有**：`touchscreen_parse_properties()` 返回 `void`，所有失败都是静默的（这是 `06-devicetree.md` §7 整章存在的理由）。

### §8.1 `-EINVAL` 的 32 个返回点，按语义分组

**A 组 · 键码映射（7 处）** —— 用户态 `EVIOCGKEYCODE`/`EVIOCSKEYCODE` 的失败都在这里

| 位置 | 所在函数 | 触发条件 |
| --- | --- | --- |
| `drivers/input/input.c:805` | `input_scancode_to_scalar()` | scancode 长度不是 1/2/4 字节（`switch` 的 `default`） |
| `drivers/input/input.c:839`、`:850` | `input_default_getkeycode()` | `dev->keycodesize` 为 0（驱动没设键码表）/ `index >= dev->keycodemax` |
| `drivers/input/input.c:869`、`:880`、`:884` | `input_default_setkeycode()` | 前两条同上；`:884` 是**新键码放不进驱动键码表的宽度**（`keycodesize < sizeof(ke->keycode)` 且高位非零） |
| `drivers/input/input.c:959` | `input_set_keycode()` | `ke->keycode > KEY_MAX` |

★ 这 7 处是**同一个用户态 ioctl 的 7 条不同失败路径**，而内核**一条日志都不打**。用户态只看到 `EINVAL`，分辨不出是"索引错"还是"键码超范围"。想区分只能挂 kprobe（§5.3）。

**B 组 · 力反馈（8 处）** —— `EVIOCSFF` / `EVIOCRMFF` 的失败

| 位置 | 所在函数 | 触发条件 |
| --- | --- | --- |
| `drivers/input/ff-core.c:26-28` | `check_effect_access()` | `effect_id` 越界，或该槽位没有 owner |
| `drivers/input/ff-core.c:57` | `compat_effect()` | 请求 `FF_RUMBLE` 但设备不支持 `FF_PERIODIC` |
| `drivers/input/ff-core.c:105` | `input_ff_upload()` | 效果类型不支持 —— ★ 唯一线索是 `:104` 的 `dev_dbg`（§2.2） |
| `drivers/input/ff-core.c:113` | `input_ff_upload()` | 波形不支持 —— 同样只有 `:112` 的 `dev_dbg` |
| `drivers/input/ff-core.c:147` | `input_ff_upload()` | 改写已有效果时新旧不兼容（`check_effects_compatible()`） |
| `drivers/input/ff-core.c:312`、`:317`、`:322` | `input_ff_create()` | 驱动侧误用：`max_effects` 为 0 / 超上限 / `ff` 已存在 |

**C 组 · 矩阵键盘（6 处）** —— 全部与 DT 解析相关，逐条的 DTS 症状见 `06-devicetree.md` §4

| 位置 | 所在函数 | 触发条件 |
| --- | --- | --- |
| `drivers/input/matrix-keymap.c:62` | `matrix_keypad_parse_properties()` | `keypad,num-rows` / `num-columns` 缺失或为 0 |
| `drivers/input/matrix-keymap.c:88`、`:94`、`:111` | `matrix_keypad_parse_keymap()` | 属性不存在 / 长度不是 4 的倍数 / 行列号越界 |
| `drivers/input/matrix-keymap.c:162`、`:187` | `matrix_keypad_build_keymap()` | `:162` = `input_dev->dev.parent` 为空（带 `WARN_ON`，§2.4）；`:187` = 平台传入的 `keymap_data` 里某个条目行列号越界（`matrix_keypad_map_key()` 返回 false） |

**D 组 · 多点触摸（3 处）**

| 位置 | 所在函数 | 触发条件 |
| --- | --- | --- |
| `drivers/input/input-mt.c:48` | `input_mt_init_slots()` | 已初始化过，且这次要的槽数与上次不同 —— ★ **重复初始化本身是允许的，改槽数不允许** |
| `drivers/input/input-mt.c:51` | `input_mt_init_slots()` | `num_slots > 1024`（源码注释写明是"避免过大分配"的任意上限） |
| `drivers/input/input-mt.c:494` | `input_mt_assign_slots()` | `num_pos > mt->num_slots` |

**E 组 · 轮询（3 处）**

| 位置 | 所在函数 | 触发条件 |
| --- | --- | --- |
| `drivers/input/input-poller.c:129` | `input_get_poll_interval()` | `dev->poller` 为空 —— 对非轮询设备调这个 API |
| `drivers/input/input-poller.c:160`、`:163` | `input_dev_set_poll_interval()` | 写 sysfs `poll` 时越界，见 §3.2 |

**F 组 · 注册与 sysfs（2 处）**

| 位置 | 所在函数 | 触发条件 |
| --- | --- | --- |
| `drivers/input/input.c:2405` | `input_register_device()` | ★★ **声明了 `EV_ABS` 却没有 `absinfo`**。这一处**有日志**：`Absolute device without dev->absinfo, refusing to register`（`:2403-2404`），是核心里最容易撞上的注册失败 |
| `drivers/input/input.c:1524` | `inhibited_store()` | `strtobool()` 不认这个字符串，见 §3.4 |

**G 组 · compat（3 处，其中至多 2 处会被编译）**

`drivers/input/input-compat.c` 的 `#ifdef CONFIG_COMPAT` 在 `:12`，`#else` 在 `:97`，`#endif` 在 `:129`。所以：

| 位置 | 编译条件 | 触发条件 |
| --- | --- | --- |
| `:69`、`:88` | `CONFIG_COMPAT=y` | `input_ff_effect_from_user()`：size 不对 |
| `:121` | `CONFIG_COMPAT=n` | 同上，非 compat 版本 |

★ **同一个函数在两个分支里各写了一遍**，所以"3 个 `-EINVAL`"里永远只有 2 个在你的内核里。arm64 上跑 32 位用户态才需要 `CONFIG_COMPAT=y`。

### §8.2 `-ENOMEM` 的 15 处

| 文件 | 行 | 所在函数 | 备注 |
| --- | --- | --- | --- |
| `drivers/input/input.c` | `:1325`、`:1341` | `input_proc_init()` | `proc_mkdir` / `proc_create` 失败，`__init` 期，失败即整个子系统初始化失败 |
| `drivers/input/input.c` | `:1683`、`:1689`、`:1699` | `input_add_uevent_bm_var()` | uevent 缓冲不够 —— ★ 会导致 udev 收不到完整的能力位图 |
| `drivers/input/input.c` | `:1718`、`:1726` | `input_add_uevent_modalias_var()` | 同上，`MODALIAS=` 那一行 |
| `drivers/input/input.c` | `:2412`、`:2433` | `input_register_device()` | devres 与 `dev_name` 分配 |
| `drivers/input/input-mt.c` | `:103` | `input_mt_init_slots()` | 槽位数组 |
| `drivers/input/input-poller.c` | `:82` | `input_setup_polling()` | poller 结构 |
| `drivers/input/ff-core.c` | `:326`、`:332` | `input_ff_create()` | `ff_device` 与 `effect_owners` |
| `drivers/input/matrix-keymap.c` | `:99`、`:171` | `matrix_keypad_parse_keymap()` / `build_keymap()` | 临时数组与 keycode 表 |

★ 15 处里 5 处（`:1683`—`:1726`）是 **uevent 构造**失败。它不会让设备注册失败，但会让 **udev 规则匹配不上**——症状是"设备在 `/proc/bus/input/devices` 里有，但 `/dev/input/by-path/` 里没有软链接"。

### §8.3 `-EFAULT` 的 9 处：全在同一个文件

9 处全在 `drivers/input/input-compat.c`，分布在 3 个函数 × 2 个编译分支：

| 函数 | `CONFIG_COMPAT=y` 分支 | `=n` 分支 |
| --- | --- | --- |
| `input_event_from_user()` | `:22`、`:32` | `:103` |
| `input_event_to_user()` | `:52`、`:56` | `:112` |
| `input_ff_effect_from_user()` | `:80`、`:91` | `:124` |

也就是说：**任何 `-EFAULT` 都只能来自"用户态给的 buffer 指针不可访问"**，与设备、驱动、DT 全都无关。`read()`/`write()` `/dev/input/eventN` 拿到 `EFAULT` = 你的用户态代码有 bug，不是内核的问题。

★ 相关的一条已登记设计债：`evdev_write()` 和 `uinput_inject_events()` 在中途遇到 `-EFAULT` 时会**丢弃已成功处理的事件计数**，直接返回错误而不是返回部分字节数——用户态无法知道"前 3 条已经生效了"。

### §8.4 其余 8 处，一条一行

| 错误码 | 位置 | 所在函数 | 含义 |
| --- | --- | --- | --- |
| `-ENODEV` | `drivers/input/input.c:635` | `input_open_device()` | `dev->going_away` 已置位——打开一个正在注销的设备 |
| `-ENODEV` | `drivers/input/input.c:1058` | `input_attach_handler()` | `input_match_device()` 返回 NULL——handler 的 `id_table` 不匹配这个设备 |
| `-ENOSYS` | `drivers/input/ff-core.c:100` | `input_ff_upload()` | `ff->upload` 为空 |
| `-ENOSYS` | `drivers/input/ff-core.c:216` | `input_ff_erase()` | `ff->erase` 为空 |
| `-ENXIO` | `drivers/input/input-mt.c:492` | `input_mt_assign_slots()` | `!mt \|\| !mt->red`——没调过 `input_mt_init_slots()`，或调时没要 `INPUT_MT_TRACK` |
| `-ENOSPC` | `drivers/input/ff-core.c:130` | `input_ff_upload()` | 效果槽全满（`max_effects` 用尽） |
| `-EBUSY` | `drivers/input/input.c:569` | `input_grab_device()` | 已有别的 handle grab 了这个设备 |
| `-EACCES` | `drivers/input/ff-core.c:31` | `check_effect_access()` | 这个效果 ID 的 owner 不是当前 `struct file` |

★ `drivers/input/input.c:1061` 不在这张表里：那一行是 `if (error && error != -ENODEV)`，用来判断"connect 失败但不是'不匹配'"要不要打 `pr_err`（`:1062-1063`）。它读的是别人的返回值，自己不返回。

★★ **`-EBUSY` 是排查"我的程序读不到事件"的第一嫌疑**：某个进程（常见的是 X/Wayland 的 evdev 输入后端，或另一个 `evtest`）用 `EVIOCGRAB` 独占了设备。判据不在日志里——`input_grab_device()` 静默返回。查法：

```bash
# 谁打开了这个设备
fuser -v /dev/input/event3
# 或
ls -l /proc/*/fd 2>/dev/null | grep event3
```

★ **`-ENXIO` 与 `-EINVAL` 在 `input_mt_assign_slots()` 里语义不同**（`drivers/input/input-mt.c:490-495`）：`-ENXIO` = "你根本没初始化 MT / 没开 `INPUT_MT_TRACK`"，`-EINVAL` = "初始化了，但这次给的点数超过槽数"。前者是驱动写错了，后者是运行期数据异常。

## §9 用户态工具

input 子系统的观察点**主要在用户态**——核心既没有 tracepoint 也没有 debugfs（§0.1、§5、§6），真正的数据出口是 `/dev/input/eventN` 这个字符设备。所以这一节不是"附加内容"，而是本篇的主线之一。

### §9.1 `evtest`：唯一能看到轴参数的工具

`evtest` 打开设备后先跑一遍 `EVIOCG*` ioctl，把设备的完整能力打出来，然后进入事件循环。它是**唯一能显示 `min/max/fuzz/flat/resolution` 的常用工具**——因为这四个值只由 `EVIOCGABS` 提供，sysfs 和 procfs 都没有（§3.1）。

```bash
evtest                       # 交互式列出所有设备让你选
evtest /dev/input/event3     # 直接打开
evtest --grab /dev/input/event3   # 用 EVIOCGRAB 独占（会让桌面收不到输入）
```

★ 它先打印能力再打印事件，所以**"能力段"对应 `06-devicetree.md` §7 的参数验证，"事件段"对应事件流验证**。两段要分开看：能力对了但没事件 = 硬件/中断问题；能力错了 = DT 或驱动问题。

★ **`--grab` 会让整个桌面失去这个设备的输入**。在有图形界面的机器上对键盘用 `--grab` 之前先确认有另一条登录途径（串口/ssh）。

### §9.2 `EVIOC*` ioctl 全清单

全部定义在 `include/uapi/linux/input.h:132-243`。按用途分组：

| ioctl | 方向 | 作用 |
| --- | --- | --- |
| `EVIOCGVERSION` | R | evdev 协议版本 |
| `EVIOCGID` | R | `struct input_id`（bustype/vendor/product/version），与 `id/*` 同源 |
| `EVIOCGREP` / `EVIOCSREP` | R / W | 自动重复的 delay 与 period，**可写** |
| `EVIOCGKEYCODE` / `EVIOCSKEYCODE` | R / W | 老版键码映射，`unsigned int[2]` |
| `EVIOCGKEYCODE_V2` / `EVIOCSKEYCODE_V2` | R / W | 新版，`struct input_keymap_entry`，支持按 index 或按 scancode |
| `EVIOCGNAME(len)` / `EVIOCGPHYS(len)` / `EVIOCGUNIQ(len)` | R | 三个字符串，与 sysfs 的 `name`/`phys`/`uniq` 同源 |
| `EVIOCGPROP(len)` | R | `propbit`，与 sysfs `properties` 同源 |
| `EVIOCGMTSLOTS(len)` | R | ★ **当前每个 MT 槽位的某个 ABS 值**——sysfs 里完全没有对应物 |
| `EVIOCGKEY(len)` / `EVIOCGLED(len)` / `EVIOCGSND(len)` / `EVIOCGSW(len)` | R | 四类的**当前状态**（不是能力） |
| `EVIOCGBIT(ev, len)` | R | 能力位图，与 `capabilities/*` 同源 |
| `EVIOCGABS(abs)` / `EVIOCSABS(abs)` | R / W | ★★ `struct input_absinfo`：`value/minimum/maximum/fuzz/flat/resolution`。**唯一的量程读写入口** |
| `EVIOCSFF` / `EVIOCRMFF` / `EVIOCGEFFECTS` | W / W / R | 力反馈，失败码见 §8.1 B 组 |
| `EVIOCGRAB` | W | 独占设备，失败是 `-EBUSY`（§8.4） |
| `EVIOCREVOKE` | W | 单向吊销自己的访问权（不可撤销） |
| `EVIOCGMASK` / `EVIOCSMASK` | R / W | 按事件类型设置接收掩码，减少无关事件唤醒 |
| `EVIOCSCLOCKID` | W | 切换时间戳时钟源（`CLOCK_REALTIME` ↔ `CLOCK_MONOTONIC`） |

★★ **`EVIOCSABS` 可以在运行时改量程**。这是不动 DTS、不重编内核就验证"是不是 `touchscreen-size-x` 写错了"的最快手段——改完 `evtest` 立刻能看到新的映射。它是运行期覆盖，重新插拔/重新加载驱动就复原。

★ **`EVIOCSCLOCKID` 决定事件时间戳的含义**。默认 `CLOCK_REALTIME`，会被 NTP 跳变影响；测延迟一定要先切到 `CLOCK_MONOTONIC`，否则算出的延迟可能是负数。

### §9.3 `evemu` 与 `libinput`

| 工具 | 用途 | 备注 |
| --- | --- | --- |
| `evemu-describe` | 把设备能力导成文本 | 输出可直接用于 bug 报告 |
| `evemu-record` | 把事件流录成可回放的文本 | ★ 录下来的文件是**纯文本**，能手工编辑再回放 |
| `evemu-play` | 通过 `/dev/uinput` 回放录制 | 见 §9.4 |
| `libinput record` | 录制 + 附带设备描述，YAML 格式 | libinput 上游 bug 报告的标准格式 |
| `libinput debug-events` | 看 libinput **解释后**的事件 | ★ 这一层已经做了手势/加速/去抖，与 `evtest` 看到的原始事件不同 |

★★ **`evtest` 与 `libinput debug-events` 不一致时，问题在 libinput 而不在内核。** 这条分界线非常有用：`evtest` 看到的是 `03-internals.md` §A8 那条链的终点，`libinput` 是它之后的用户态处理。触摸板"手势不灵"绝大多数是后者。

### §9.4 `/dev/uinput`：构造虚拟设备

`drivers/input/misc/uinput.c`，`misc` 设备，`UINPUT_NAME` = `"uinput"`（`:33`）。它是本子系统事实上的故障注入器（§7）。

关键 ioctl（`include/uapi/linux/uinput.h`）：

| ioctl | 作用 |
| --- | --- |
| `UI_DEV_SETUP`（`:100`） | 设置 name/id，**新式接口**，取代旧的写 `struct uinput_user_dev` |
| `UI_ABS_SETUP`（`:135`） | ★ 逐轴设置 `input_absinfo`——构造触摸屏时必需 |
| `UI_SET_EVBIT` / `KEYBIT` / `RELBIT` / `ABSBIT` / `MSCBIT` / `LEDBIT` / `SNDBIT` / `FFBIT` / `SWBIT` / `PROPBIT`（`:137-147`） | 逐位声明能力，10 个 |
| `UI_DEV_CREATE`（`:64`） | ★ **真正调用 `input_register_device()` 的那一刻**——之前的所有设置在此固化 |
| `UI_DEV_DESTROY`（`:65`） | 注销 |
| `UI_GET_SYSNAME(len)`（`:161`） | 拿到 `inputN` 这个名字，用来找对应的 `/dev/input/eventM` |
| `UI_BEGIN_FF_UPLOAD` / `UI_END_FF_UPLOAD` / `UI_BEGIN_FF_ERASE` / `UI_END_FF_ERASE`（`:149-152`） | 把力反馈请求转发到用户态处理 |

★ **顺序是硬性的**：所有 `UI_SET_*BIT` 和 `UI_ABS_SETUP` 必须在 `UI_DEV_CREATE` **之前**——之后再设就没有效果了（因为设备已注册）。这是写 uinput 程序最常见的错误。

★ **`UI_GET_SYSNAME` 是把 uinput fd 和 `/dev/input/eventN` 对应起来的正规办法**。靠"猜最新的那个 eventN"在有并发热插拔时会错。

★ **uinput 有一个已登记的设计债**：它的事件环只有 16 项（`UINPUT_BUFFER_SIZE`，`drivers/input/misc/uinput.c:34`；`head` 直接 `% 16` 绕回，`:87`），溢出时**不发 `SYN_DROPPED`**（整个 `uinput.c` 里 grep 不到这个符号）——写得太快会静默丢事件。对比 evdev 侧：`evdev.c` 的缓冲区是 `max(hint_events_per_packet * 8, 64)`（`drivers/input/evdev.c:464-465`，常量在 `:12-13`），溢出时**会**补 `SYN_DROPPED`（`:152`、`:231`）。两边行为不对称。

### §9.5 没有工具时的最小办法

```bash
# 1. 有哪些设备、谁绑了 handler
cat /proc/bus/input/devices

# 2. 事件流：每 24 字节（arm64 上 struct input_event = 16+8）一条
#    输出是二进制，只能确认"有没有数据"，解析不了
hexdump -C /dev/input/event3

# 3. 内核侧全量事件（会打到 dmesg）
modprobe evbug && dmesg -w
```

★ `hexdump` 这条只能回答"有没有事件"这个是/否问题。**不要试图靠肉眼解析**——`struct input_event`（`include/uapi/linux/input.h:28-47`）在 arm64 内核侧是 `__kernel_ulong_t __sec`(8) + `__usec`(8) + `__u16 type` + `__u16 code` + `__s32 value` = 24 字节，且 compat 环境下布局不同（`drivers/input/input-compat.c`）。要解析就装 `evtest`。

## §10 一页速查

```
┌─ 设备在不在 ────────────────────────────────────────────────┐
│ cat /proc/bus/input/devices          有没有这个设备（§4.1） │
│   H: Handlers=  行里有 eventN ?      evdev 绑上了吗          │
│   B: EV=/ABS=   缺行 = 没声明该类型                          │
│ ls /sys/class/input/                 sysfs 侧                │
└─────────────────────────────────────────────────────────────┘
┌─ 参数对不对 ────────────────────────────────────────────────┐
│ evtest /dev/input/eventN             ★ 唯一能看 min/max     │
│   能力段错 → DT/驱动问题（06 §7）                            │
│   能力段对、无事件 → 中断/硬件                               │
│ cat /sys/class/input/inputN/capabilities/abs   只有位图      │
└─────────────────────────────────────────────────────────────┘
┌─ 事件流 ────────────────────────────────────────────────────┐
│ modprobe evbug; dmesg -w             内核侧全量（§1.2）      │
│ evtest                               用户态原始事件          │
│ libinput debug-events                解释后的事件（§9.3）    │
│   evtest 有、libinput 无 → 问题在 libinput                   │
└─────────────────────────────────────────────────────────────┘
┌─ 读不到事件 ────────────────────────────────────────────────┐
│ fuser -v /dev/input/eventN           谁 EVIOCGRAB 了（-EBUSY）│
│ cat /sys/class/input/inputN/inhibited  是不是被抑制了（§3.4）│
│ cat /sys/class/input/inputN/poll     轮询设备写成 0 了？（§3.2）│
│ cat .../disabled_keys                gpio-keys 屏蔽了？（§3.3）│
└─────────────────────────────────────────────────────────────┘
┌─ 深挖 ──────────────────────────────────────────────────────┐
│ echo 'file ff-core.c +p' > /sys/kernel/debug/dynamic_debug/control  │
│ echo 15 > /sys/module/rmi_core/parameters/debug_flags   rmi4（§2.3）│
│ ls /sys/kernel/debug/edt_ft5x06/     原始电容（§6.1）        │
│ v4l2-ctl -d /dev/v4l-touch0 --list-inputs   RMI4 F54（§6.2） │
│ /sys/kernel/tracing + function_graph / kprobe（§5.3）        │
└─────────────────────────────────────────────────────────────┘
```

**三件本子系统没有的东西**（§0.1）：核心 tracepoint、核心 debugfs、故障注入框架。别去找。

**arm64 额外没有的**：applespi 的 8 个 tracepoint（`depends on X86`）、applespi 的 debugfs、serio/i8042 相关的一切。

## 本篇引用的其他文档

| 想知道 | 去哪篇 |
| --- | --- |
| 有哪些对外 API、代码住在哪 | `01-api.md` |
| `input_dev` / `input_handler` / `input_handle` 三者关系 | `02-datastructures.md` §1.0 |
| 事件从驱动到 `/dev/input/eventN` 的完整调用链 | `03-internals.md` §A8 |
| 注册与注销的时间线（`going_away` 在哪置位） | `03-internals.md` §A4 与 §A12.2 |
| evdev / mousedev / joydev / uinput 各自的定位 | `04-extensions.md` |
| 各角色的代码骨架、近邻符号区分 | `05-usage.md` |
| DTS 怎么写、属性由谁消费 | `06-devicetree.md` §1 |
| DT 配错了的五棵决策树 | `06-devicetree.md` §7 |
| 编译期/运行期静态/运行期动态三层定位 + 错误消息映射表 | `06-devicetree.md` §8 |
| 为什么核心没有 tracepoint（设计上的取舍） | `08-feynman.md` |
| 本篇每个数字的重跑命令与证据等级 | `99-evidence.md` |
