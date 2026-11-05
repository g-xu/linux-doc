---
title: 总览
---

# input 子系统总览

> 基线：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**。
> 树上带有一个 PREEMPT_RT 相关的回合式补丁集，但 `drivers/input/` 与 `include/linux/input*` **未被改动**（取证见 `99-evidence.md` §1）。
> 本篇的定位是**复习入口**：隔几个月回来只读这一篇，就能恢复整个子系统的形状；每一节末尾都指向承载细节的那一篇。

---

## §1 一句话定位

**input 子系统把"一个按键/一次触摸/一次摇杆位移"这件事，从"某颗芯片的某个寄存器位"翻译成"一条全内核统一、且被 uapi 冻结的三元组事件"，并负责把同一条事件流同时送给任意多个互不知道彼此存在的消费者。**

它不是一条总线，也不是一个设备类库。**它是一个翻译层加一个分发层。**

### 1.1 它替驱动省掉了什么

一个键盘/触摸屏驱动如果没有这个框架，为了让用户态能读到按键，至少要自己实现下面这一整列。有了框架之后，这一整列全部由核心承担，驱动只剩最左边那一格：

| 驱动自己要做的 | 框架替它做的 | 承载代码 |
| --- | --- | --- |
| 把寄存器值译成 `KEY_POWER` | —— 只有这一格留给驱动 | 驱动自己 |
| 申请字符设备号、分配次设备号 | `INPUT_MAJOR` 13 + 位图分配次设备号 | `drivers/input/evdev.c:1337-1400` |
| 实现 `read`/`poll`/`fasync`/环形缓冲 | 每个打开的 fd 一个独立环形缓冲 + 溢出通告 | `drivers/input/evdev.c:558-607` |
| 给每个事件打时间戳 | 一帧一次取时间，整帧共用 | `drivers/input/input.c:2157-2165` |
| 32 位程序在 64 位内核上的结构体转换 | `input-compat.c` 统一处理 | `drivers/input/input-compat.c` |
| 重复上报同一个值的去抖/去重 | 状态位图比对，重复值直接丢 | `drivers/input/input.c:273-277` |
| 按键自动重复（autorepeat） | 核心一个 per-device 定时器搞定 | `drivers/input/input.c:84-98`、`:2324-2360` |
| 同一个设备被多个程序同时读 | 每个 fd 一份缓冲，互不影响 | `drivers/input/evdev.c:244-286` |
| 向用户态自描述"我有哪些键、哪些轴" | 九个 `capabilities/*` + 一组 ioctl | `drivers/input/input.c:1610-1631` |
| 力反馈的排队、幅度换算、超时 | `ff-core.c` + `ff-memless.c` | `drivers/input/ff-core.c` |

★ **这张表就是"价值主张"本身**：不要把 input 核心理解成"提供了一批 API"，要理解成"它把上面九行工作从每个驱动里搬走了一次，然后只写了一遍"。`drivers/input/*.c` 一共 9446 行（`wc -l drivers/input/*.c`），换来的是 `drivers/input/` 九个子目录下 **389 个驱动文件**（各目录 `.c` 计数见 §2.8）不必各写一遍这九行。

### 1.2 一句话记住它的形状

```
       驱动侧                核心                 消费者侧
  ┌──────────────┐    ┌─────────────────┐    ┌────────────────────┐
  │ input_dev    │───▶│ 事件码翻译 + 去重 │───▶│ input_handler × N  │
  │ (我有哪些能力)│    │ 成帧 + 分发       │    │ (evdev/kbd/mousedev)│
  └──────────────┘    └─────────────────┘    └────────────────────┘
        一对多  ←── input_handle 是这条连线本身 ──→  一对多
```

**三个对象、两条一对多关系、连线本身也是个对象**——记住这一句，`02-datastructures.md` 的全部内容都能重建出来。

---

## §2 背景：为什么需要这么一层（问题背景）

> input 是**框架型**子系统：全树没有 `struct bus_type input_bus_type`（判定过程和两道门的输出见 `99-evidence.md` §1）。所以本节不讲硬件电气背景，讲**问题域**——它到底在解决哪个问题，以及为什么这个问题不能在驱动里各自解决。

### 2.0 本节读法

| 小节 | 回答的问题 |
| --- | --- |
| §2.1 | 输入设备这个问题域，特殊在哪三点 |
| §2.2 | 没有框架时，每个驱动重复写的到底是什么 |
| §2.3 | 核心抽象是哪五个对象，它们怎么拼 |
| §2.4 | 为什么"事件码空间"本身就是这个子系统最硬的 ABI |
| §2.5 | 为什么必须有"帧"这个概念（`SYN_REPORT`） |
| §2.6 | 为什么驱动不能自己开一个字符设备了事 |
| §2.7 | 和相近框架（HID / IIO / serio / evdev-only）的分工 |
| §2.8 | arm64 上的实际形态：量化统计 + in-tree DTS 实例 |

### 2.1 问题域的三个特殊点

**第一点：设备种类极多，但语义必须完全统一。**

键盘、鼠标、触摸屏、摇杆、旋钮、加速度按键、红外遥控、平板笔、电源键——硬件形态毫无共性，但用户态想问的问题只有一个：**"用户刚才做了什么？"** 一个桌面环境不可能为每一款触摸屏写一段代码。

于是这个子系统做了一个在内核里相当少见的决定：**把"用户做了什么"的全部可能取值，一次性枚举进一个 uapi 头文件，然后冻结。**

```
include/uapi/linux/input-event-codes.h
  ├── EV_SYN  0x00  ── 帧边界（本身不是"用户做了什么"，是标点符号）
  ├── EV_KEY  0x01  ── 516 个 KEY_* / BTN_*：有/无两态的动作
  ├── EV_REL  0x02  ──  15 个 REL_*：相对位移（鼠标、滚轮）
  ├── EV_ABS  0x03  ──  45 个 ABS_*：绝对坐标（触摸屏、摇杆）
  ├── EV_MSC  0x04  ── 杂项（扫描码原样透出等）
  ├── EV_SW   0x05  ── 开关（翻盖、耳机插入）
  ├── EV_LED  0x11  ── 输出方向：点灯
  ├── EV_SND  0x12  ── 输出方向：蜂鸣
  ├── EV_REP  0x14  ── 输出方向：自动重复参数
  ├── EV_FF   0x15  ── 输出方向：力反馈
  ├── EV_PWR  0x16  ── 电源
  └── EV_FF_STATUS 0x17 ── 力反馈状态回读
```

十二个类型定义在 `include/uapi/linux/input-event-codes.h:38-49`，上界 `EV_MAX 0x1f` / `EV_CNT` 在 `:50-51`。三个计数的复现命令留在 `99-evidence.md` §3。

★ **注意这里面有四个是"输出"方向**（`EV_LED` / `EV_SND` / `EV_REP` / `EV_FF`）。"input 子系统只管输入"是一个很常见的误解——`/dev/input/eventN` 是可写的，写进去的事件走 `input_inject_event()`（`drivers/input/input.c:448-466`）反向进入设备。细节见 `03-internals.md` §A8.1。

**第二点：一个物理设备天然有多个消费者，而它们互不知道彼此存在。**

按下电源键这一个动作，同时有意义的消费者至少有：

- 图形界面（通过 `/dev/input/eventN`）
- 内核控制台（虚拟终端要处理 Ctrl-Alt-Del、切换 tty）
- SysRq（魔法组合键，必须在系统半死时仍然可用——所以只能在内核里，不能靠用户态）
- kgdb（`kgdboc` 要能靠按键进调试器）
- LED 同步（Caps Lock 灯）
- 电源管理（`apm-power`）

它们分布在四个不同的目录里，谁都不该知道别人存在。全树 `input_register_handler()` 的调用点正好把这份名单列了出来——**12 个**：

| 消费者 | 注册点 | 作用 |
| --- | --- | --- |
| evdev | `drivers/input/evdev.c:1433` | 生成 `/dev/input/eventN`，桌面的唯一入口 |
| mousedev | `drivers/input/mousedev.c:1104` | 兼容老的 `/dev/input/mouseN` PS/2 协议 |
| joydev | `drivers/input/joydev.c:1089` | 生成 `/dev/input/jsN` |
| VT 键盘 | `drivers/tty/vt/keyboard.c:1670` | 内核控制台的按键处理 |
| SysRq | `drivers/tty/sysrq.c:1043` | 魔法组合键 |
| kgdboc | `drivers/tty/serial/kgdboc.c:114` | 按键进调试器 |
| input-leds | `drivers/input/input-leds.c:207` | 把 `EV_LED` 接到 LED 子系统 |
| apm-power | `drivers/input/apm-power.c:109` | 任意输入唤醒 |
| evbug | `drivers/input/evbug.c:91` | 把所有事件打到内核日志（调试用） |
| mac_hid | `drivers/macintosh/mac_hid.c:170` | 单键鼠标模拟三键 |
| rfkill | `net/rfkill/input.c:336` | 飞行模式键 |
| USB mixer quirks | `sound/usb/mixer_quirks.c:768` | 某些声卡的旋钮 |

★★ **注意最后三行跨出了 `drivers/input/`**（`drivers/macintosh/`、`net/`、`sound/`）。这是"框架型子系统"的典型特征，也是做全称断言时最容易漏的地方（规则 19：全称断言必须跨出子系统目录 grep）。复现命令：

```
grep -rn 'input_register_handler(' --include=*.c .
```

**第三点：事件在中断上下文产生，消费者在用户态。**

按键中断来了，驱动在 ISR（或 threaded IRQ）里调 `input_report_key()`。而消费者是一个可能正睡在 `read()` 上的进程，也可能根本没在读。这中间必须有：缓冲、唤醒、溢出策略、时间戳。

★ 这一点决定了核心整条事件路径的**锁形态**：`input_event()` 拿的是 `spin_lock_irqsave(&dev->event_lock, flags)`（`drivers/input/input.c:430-432`），也就是**整条分发路径关中断执行**。这个决定的直接后果是——分发路径里不能睡、不能做重活，也不能埋 tracepoint（详见 §6.4 和 `08-feynman.md` §10）。

### 2.2 没有框架时，每个驱动重复写的到底是什么

把 §1.1 那张表展开成"一个假想的、不用框架的触摸屏驱动"要写的代码，就能看清框架的边界在哪：

```
   假想的裸驱动 my_touch.c              有框架之后
   ─────────────────────────           ──────────────────────────
   register_chrdev(...)                × 核心已做（evdev）
   my_read() { 环形缓冲出队 }           × 核心已做
   my_poll() { poll_wait(...) }        × 核心已做
   my_ioctl() { 报告分辨率范围 }        × 核心已做（EVIOCGABS）
   自定义结构体 struct my_event         × 换成 struct input_event（uapi 冻结）
   32/64 位兼容转换                     × 核心已做（input-compat.c）
   "上次的坐标和这次一样，别发"          × 核心已做（去重）
   "坐标抖动 ±2 以内当没动"             × 核心已做（defuzz）
   多进程同时打开怎么办                  × 核心已做（每 fd 一份缓冲）
   ─────────────────────────           ──────────────────────────
   读寄存器、拆包、译成坐标              ✓ 只剩这一格
```

★ **最关键的那一格不是缓冲，是 `struct input_event`。** 只要每个驱动定义自己的事件结构体，用户态就必须为每一款硬件写一段代码——框架的真正价值是**用一个冻结的 ABI 换掉了 N 份私有 ABI**。这个结构体在 `include/uapi/linux/input.h:28-47`：

```c
struct input_event {
#if (__BITS_PER_LONG != 32 || !defined(__USE_TIME_BITS64)) && !defined(__KERNEL__)
	struct timeval time;
#define input_event_sec time.tv_sec
#define input_event_usec time.tv_usec
#else
	__kernel_ulong_t __sec;
#if defined(__sparc__) && defined(__arch64__)
	unsigned int __usec;
	unsigned int __pad;
#else
	__kernel_ulong_t __usec;
#endif
#define input_event_sec  __sec
#define input_event_usec __usec
#endif
	__u16 type;
	__u16 code;
	__s32 value;
};
```

三个语义字段只有 8 个字节：`type`（EV_*）、`code`（KEY_*/ABS_*/…）、`value`。剩下的全是时间戳的 y2038 与 32/64 位兼容包装。协议版本 `EV_VERSION 0x010001` 在同文件 `:53`——**从 2.6 时代到 6.1 没有变过**，这就是"冻结"的具体含义。

### 2.3 核心抽象：五个对象

| 对象 | 定义位置 | 一句话 | 谁分配 |
| --- | --- | --- | --- |
| `struct input_dev` | `include/linux/input.h:137` | 一个能产生事件的设备 + 它的能力位图 | 驱动（`input_allocate_device()`） |
| `struct input_handler` | `include/linux/input.h:310-331` | 一个想消费事件的模块 | 消费者模块（静态变量） |
| `struct input_handle` | `include/linux/input.h:345-357` | 一条 dev↔handler 的连线 | **handler**，不是核心 |
| `struct input_value` | `include/linux/input.h:32-36` | 一个 (type, code, value) 三元组，帧的元素 | 核心（`dev->vals[]`） |
| 事件码空间 | `include/uapi/linux/input-event-codes.h` | 所有对象共享的词汇表 | uapi，谁都不能改 |

关系图：

```
  input_dev A                                     input_handler evdev
  ┌───────────────┐                               ┌────────────────────┐
  │ h_list ●──────┼──▶ handle(A,evdev) ─── h_node ┼──▶ ●  h_list       │
  │        ●──────┼──▶ handle(A,kbd)             │    │                │
  │ node   ●──────┼──▶ input_dev_list            │    ● node ──▶ input_handler_list
  └───────────────┘                               └────────────────────┘
  input_dev B                                     input_handler kbd
  ┌───────────────┐                               ┌────────────────────┐
  │ h_list ●──────┼──▶ handle(B,kbd)  ─── h_node ┼──▶ ●  h_list       │
  └───────────────┘                               └────────────────────┘

  两个全局链表：input_dev_list / input_handler_list（都用 list_add_tail）
  每个 dev 有一条 h_list，每个 handler 也有一条 h_list——同名不同物
  handle 同时挂在两条链表上：d_node 挂 dev->h_list，h_node 挂 handler->h_list
```

★★ **`h_list` 这个字段名在 `input_dev` 和 `input_handler` 里都有，含义却不同**（一个是"接我的 handler 们"，一个是"我接的 dev 们"）。这是本子系统最容易看混的一组关系，`02-datastructures.md` §1.0 用一张四行表把它摊开了，`08-feynman.md` §2 用同声传译的类比讲了一遍。

**为什么 handle 必须是一个独立对象、而不是一个指针数组？** 因为连线上有状态：`handle->open` 是"这条连线上有没有人在用"（`include/linux/input.h:349`，是 `int` 不是 `bool`）。而且这块内存由 handler 分配——evdev 直接把它嵌在自己的 `struct evdev` 里（`drivers/input/evdev.c:30`），核心只负责把它挂进两条链表。

### 2.4 事件码空间就是这个子系统最硬的 ABI

大多数子系统的 uapi 是"几个 ioctl 号 + 几个结构体"。input 的 uapi 是**一整套语义命名空间**，而且它的三个头文件之间有清晰分工：

| uapi 头文件 | 内容 | 谁在读 |
| --- | --- | --- |
| `include/uapi/linux/input-event-codes.h` | `EV_*` / `KEY_*` / `ABS_*` / `REL_*` / `SW_*` / `LED_*` / `SND_*` / `MSC_*` / `BUS_*` 全部数值 | 内核驱动、用户态、**DTS（`linux,code = <KEY_POWER>`）** |
| `include/uapi/linux/input.h` | `struct input_event` / `struct input_id` / `struct input_absinfo` / 全部 `EVIOC*` ioctl | evdev 的实现和它的用户 |
| `include/uapi/linux/uinput.h` | 用户态造设备的那套 ioctl | uinput 及其用户 |

★ **第一个头文件被 DTS 直接 include**——`arch/arm64/boot/dts/` 里写 `linux,code = <KEY_POWER>` 时，那个 `KEY_POWER` 就是从这里来的（经 `include/dt-bindings/input/linux-event-codes.h` 转发）。也就是说这套码值不只是内核-用户态 ABI，还是**内核-设备树 ABI**：改一个 KEY 值会同时打破两侧。

三个规模数字（复现命令见 `99-evidence.md` §3）：

```
grep -cE '^#define KEY_[A-Z0-9_]+[ \t]+' include/uapi/linux/input-event-codes.h  →  516
grep -cE '^#define ABS_[A-Z0-9_]+[ \t]+' include/uapi/linux/input-event-codes.h  →   45
grep -cE '^#define REL_[A-Z0-9_]+[ \t]+' include/uapi/linux/input-event-codes.h  →   15
```

★ 注意 `grep -c` 数的是**含该串的行数**，不是符号数——这三个模式都锚定了行首 `#define` 和一个空白分隔符，所以这里两者相等；但同一个文件里 `BTN_*` 也属于 `EV_KEY` 类型，没有被这三个数字覆盖。别把 516 当成"键盘按键数"。

### 2.5 为什么必须有"帧"（`SYN_REPORT`）

这是 input 最容易被忽略、又最不能忽略的设计。

考虑一次触摸：手指落在 (300, 500)。驱动必须发**两个**事件（`ABS_X=300`、`ABS_Y=500`）。如果用户态一个一个读，它在读到 X 之后、读到 Y 之前，看到的是一个坐标是新的、另一个坐标是旧的**不存在的点**。多点触控更糟：五根手指十个坐标，中间任何一个截断点都对应一个物理上从未发生的手势。

所以核心引入了"帧"：

```
   驱动侧调用序列                      核心 dev->vals[] 缓冲          用户态看到
   input_report_abs(ABS_X, 300)  ──▶  [ (ABS,X,300) ]                （什么都没有）
   input_report_abs(ABS_Y, 500)  ──▶  [ (ABS,X,300), (ABS,Y,500) ]   （什么都没有）
   input_sync(dev)               ──▶  [ …, (SYN,SYN_REPORT,0) ]  ──▶ 三个事件一次性可读
                                       ↑ INPUT_FLUSH，整帧下发
```

实现只有两处需要记住：

- `input_sync()` 就是 `input_event(dev, EV_SYN, SYN_REPORT, 0)`，一个 4 行的 inline（`include/linux/input.h:448-451`）。它不是特殊 API，它是一个普通事件。
- 核心给 `SYN_REPORT` 打上 `INPUT_FLUSH` 标志（`drivers/input/input.c:255-257`），其它事件只有 `INPUT_PASS_TO_HANDLERS`。flush 时才真正调 `input_pass_values()`（`:372-375`）。

★★ 两个必须知道的细节，都在 `08-feynman.md` §4/§5 里展开：

1. **空帧会被丢掉**，而且丢两次——核心在 `drivers/input/input.c:373` 用 `if (dev->num_vals >= 2)` 拦一次，evdev 在 `drivers/input/evdev.c:267-270` 按每个 client 再拦一次（因为 evdev 会按类型过滤，核心眼里非空的帧到了某个 client 可能变空）。
2. **核心会替你劈帧**：`dev->vals[]` 快满时核心自己插一个 `SYN_REPORT` 下发（`drivers/input/input.c:383-386`），而它插的那个 value 是 **1**（`input_value_sync` 在 `:49`），不是 `input_sync()` 的 0。**用户态可以据此分辨"驱动自己结的帧"和"核心替它结的帧"**——这一点 uapi 文档里没写。

### 2.6 为什么驱动不能自己开一个字符设备了事

技术上当然可以。但会失去四样东西，而且是不可逆的：

| 自己开字符设备会失去 | 为什么不可逆 |
| --- | --- |
| 内核内部的消费者 | VT 键盘、SysRq、kgdboc、rfkill 都只认 `input_handler`。自己开字符设备 = 这台机器上按 Ctrl-Alt-Del 和 SysRq 都不响应 |
| libinput / X / Wayland 的自动支持 | 它们扫的是 `/dev/input/event*` 且靠 `EVIOCGBIT` 自描述；私有设备节点没人认 |
| 多消费者并发 | 私有实现通常只支持一个 reader；框架下 `input_pass_values()` 遍历整条 `h_list`（`drivers/input/input.c:157-162`） |
| 事件码语义 | 私有码值意味着"这个键是什么"只有你的驱动知道 |

★ 反过来说，**框架也有它挡不住的东西**：一个 handler 可以通过 `filter` 回调把事件从流里截断（`include/linux/input.h:317`），一个进程可以用 `EVIOCGRAB` 把整个设备独占（`drivers/input/input.c:559-578`）。也就是说"注册进框架"换来的是可发现性和复用，不是"我的事件一定送得到"。两条路径的细节见 `03-internals.md` §A8.5（filter）和 §A9（grab），以及 `08-feynman.md` §8。

### 2.7 和相近框架的分工

内核里有好几层东西名字都跟"输入"沾边，很容易搞混。判据只有一个：**谁有 `struct bus_type`，以及事件最终从哪个 `input_dev` 出去。**

| 框架 | 有自己的 `bus_type` 吗 | 和 input 的关系 | 判定依据 |
| --- | --- | --- | --- |
| **input 核心** | **没有** | 它就是本篇的主角 | 全树无 `input_bus_type`；`drivers/input/` 下唯一的 `bus_type` 是 rmi4 的（`drivers/input/rmi4/rmi_bus.c:347-350`） |
| **HID**（`drivers/hid/`） | 有：`hid_bus_type`（`drivers/hid/hid-core.c:2729`） | **在 input 之上**。HID 把 report descriptor 解析成 input 事件，然后调 input 核心 | `drivers/hid/hid-input.c:1990` 调 `input_allocate_device()`，`:2334` 调 `input_register_device()` |
| **serio**（`drivers/input/serio/`） | 有：`serio_bus`（`drivers/input/serio/serio.c:1010`） | **在 input 之下**。它是"低速串行输入端口"这条物理链路；AT 键盘驱动是 serio driver，同时又注册一个 `input_dev` | 目录在 `drivers/input/` 下但是独立总线，见 `04-extensions.md` §2 |
| **gameport**（`drivers/input/gameport/`） | 有：`gameport_bus`（`drivers/input/gameport/gameport.c:786`） | 同上，游戏端口这条物理链路 | 同上 |
| **rmi4**（`drivers/input/rmi4/`） | 有：`rmi_bus_type`（`drivers/input/rmi4/rmi_bus.c:347-350`） | Synaptics 触摸屏的**功能分解总线**：一颗芯片拆成多个 Fxx 功能设备，最后汇总成一个 `input_dev` | `04-extensions.md` §5 |
| **IIO**（`drivers/iio/`） | 有：`iio_bus_type`（`drivers/iio/industrialio-core.c:42`） | **平行、不重叠**。IIO 管"传感器读数"（有量纲、要标定、可能高频采样），input 管"用户动作"（离散、语义化） | 加速度计通常是 IIO 设备；同一颗芯片的"敲击检测"却可能是一个 input 设备 |
| **uinput** | 没有 | **在 input 之上，但方向相反**：用户态造一个 `input_dev` | `04-extensions.md` §6 |
| **evdev / joydev / mousedev** | 没有 | 它们是 input 的 `input_handler`，不是独立框架 | `drivers/input/evdev.c:1420` 等 |

★★ **"事件在 input 核心里都长一样"是这套分工能成立的原因。** 无论上游是 USB HID、是 I2C 触摸屏、是 serio 上的 AT 键盘、还是 uinput 里的一个用户态进程，到了 `input_event()` 这一行之后路径完全相同。这就是为什么 `evtest` 能对所有输入设备一视同仁。

★ **反例值得留着**：IIO 和 input 的边界经常在同一颗芯片上被切开。判据不是"这是不是个传感器"，而是"输出是不是一个 uapi 里已有的语义码"。有 `KEY_*`/`ABS_*` 能表达的就走 input，需要"m/s² 加上标定系数"的就走 IIO。

### 2.8 arm64 上的实际形态

**规模。** 核心与驱动的分界很清楚：

| 范围 | 规模 | 本文档集怎么处理 |
| --- | --- | --- |
| `drivers/input/*.c`（16 个文件，核心 + handler） | 9446 行 | 逐行读。`input.c` 2770 行、`evdev.c` 1446 行是重点 |
| 核心 5 个头文件（见下）| 892 行 | 逐行读 |
| `drivers/input/*/ *.c`（9 个子目录） | 389 个文件 | 只讲契约，选代表驱动详读（选法见 `99-evidence.md` §2.2） |

★ 上表第二行的 892 行**必须显式列文件**，不能写成 `include/linux/input/*.h`——那个通配符底下有 17 个头文件、一共 1823 行，大半是具体驱动的板级数据结构（`adxl34x.h` 357 行、`adp5589.h` 180 行）。892 = 这 5 个：

```bash
wc -l include/linux/input.h include/linux/input/mt.h \
      include/linux/input/matrix_keypad.h include/linux/input/sparse-keymap.h \
      include/linux/input/touchscreen.h        # → 580 + 129 + 92 + 59 + 32 = 892
wc -l include/linux/input.h include/linux/input/*.h | tail -1   # → 1823（别拿这个核对上表）
```

九个子目录的驱动文件数（`for d in drivers/input/*/; do ls $d*.c | wc -l; done`）：

```
  touchscreen/  109  ┃████████████████████████████
  misc/          84  ┃█████████████████████
  keyboard/      68  ┃█████████████████
  mouse/         38  ┃██████████
  joystick/      34  ┃█████████
  serio/         29  ┃███████        ← 独立总线，arm64 上只剩 SoC 内置控制器
  rmi4/          16  ┃████
  tablet/         6  ┃██
  gameport/       5  ┃█             ← arm64 上基本不涉及
```

★ 这里 `joystick/` 是 **34**，而 `01-api.md` §0.1 的同一行是 **39**——两个口径，不是矛盾：上面这条用 `ls $d*.c` 只数子目录本身，01 那条用 `find $d -name '*.c'` 会递归进 `drivers/input/joystick/iforce/`（5 个文件）。`find drivers/input -mindepth 2 -type d` 全树只输出一个 `drivers/input/joystick/iforce`，所以九行里**只有 `joystick/` 这一行**两个口径不同，其余八行两个口径完全一致。三个数对得上账：16（根目录）+ 389（`ls drivers/input/*/*.c`）+ 5（iforce）= 410（`find drivers/input -name '*.c'`）。两个口径都登记在 `99-evidence.md` §3。

**arm64 上真正会碰到的那一层。** 三个可复现的过滤：

```
grep -rl 'of_device_id' drivers/input/ --include=*.c   →  138 个文件（走 DT，arm64 主路径）
grep -rl 'acpi_device_id' drivers/input/ --include=*.c →   17 个文件（x86 平板/笔记本路径，本文档集不覆盖）
grep -rn 'depends on.*\bX86\b' drivers/input/Kconfig drivers/input/*/Kconfig | wc -l → 6 条
```

★★ **一个必须记住的编译期事实：arm64 上 i8042 / AT 键盘 / PS2 鼠标这条路径编译不出来。** `CONFIG_SERIO_I8042` 有 `depends on ARCH_MIGHT_HAVE_PC_SERIO`（`drivers/input/serio/Kconfig:32`），而这个 symbol 由各架构 `select`——

```
grep -rn 'ARCH_MIGHT_HAVE_PC_SERIO' arch/
```

输出里有 x86、alpha、ia64、loongarch、mips、powerpc、sparc、`arch/arm/mach-footbridge`，**没有 `arch/arm64/`**。所以整本文档集里凡是提到 i8042/atkbd 的地方都只作为"这个框架长什么样"的例子，不是 arm64 上的实际路径（裁剪清单见 `99-evidence.md` §2.7）。

**arm64 defconfig 实际打开了什么。** 这是"典型 arm64 系统上 input 到底跑哪几个模块"最直接的答案（`grep -n 'INPUT\|KEYBOARD\|TOUCHSCREEN\|SERIO' arch/arm64/configs/defconfig`）：

| 配置 | 值 | 说明 |
| --- | --- | --- |
| `CONFIG_INPUT_EVDEV` | `y` | `arch/arm64/configs/defconfig:406`。**唯一打开的 handler**——`mousedev`/`joydev`/`evbug` 都没开 |
| `CONFIG_KEYBOARD_GPIO` | `y` | `:408`。GPIO 按键，最常见的 arm64 输入设备 |
| `CONFIG_KEYBOARD_ADC` / `_SNVS_PWRKEY` / `_IMX_SC_KEY` / `_CROS_EC` | `m`/`m`/`m`/`y` | `:407`、`:409`、`:410`、`:411` |
| `CONFIG_TOUCHSCREEN_ATMEL_MXT` / `_GOODIX` / `_EDT_FT5X06` | `m` | `:413`–`:415` |
| `CONFIG_INPUT_PM8941_PWRKEY` / `_HISI_POWERKEY` | `y` | `:417`、`:421`。SoC PMIC 电源键 |
| `CONFIG_INPUT_PM8XXX_VIBRATOR` / `_PWM_BEEPER` / `_PWM_VIBRA` | `m` | `:418`–`:420`。**输出方向**的设备（`EV_FF` / `EV_SND`） |
| `CONFIG_SERIO_SERPORT` | **not set** | `:422` |
| `CONFIG_SERIO_AMBAKMI` | `y` | `:423`。ARM PrimeCell KMI——serio 在 arm64 上仅剩的形态 |

★ 这张表说明两件事：(1) arm64 上 handler 侧几乎只有 evdev，所以 `07-debug.md` 的调试手段绝大部分围绕 `/dev/input/eventN`；(2) 打开的设备驱动里既有输入也有**输出**（振动马达、蜂鸣器），印证了 §2.1 里"四个输出类型"那条。

**一个真实的 in-tree DTS 实例。** GPIO 按键是 arm64 上最典型的 input 设备，`arch/arm64/boot/dts/rockchip/rk3399-rockpro64.dtsi:39-52`：

```
	gpio-keys {
		compatible = "gpio-keys";
		autorepeat;
		pinctrl-names = "default";
		pinctrl-0 = <&pwrbtn>;

		key-power {
			debounce-interval = <100>;
			gpios = <&gpio0 RK_PA5 GPIO_ACTIVE_LOW>;
			label = "GPIO Key Power";
			linux,code = <KEY_POWER>;
			wakeup-source;
		};
	};
```

五个属性分别由三方消费：`compatible`/`gpios` 由 DT 与 GPIO 核心，`autorepeat`/`debounce-interval`/`linux,code`/`label`/`wakeup-source` 由 `drivers/input/keyboard/gpio_keys.c` 自己读（`autorepeat` 在 `:753`、父节点 `label` `:755`、`linux,code` `:762`、子节点 `label` `:769`、`wakeup-source` `:776`、`debounce-interval` `:786`）。**注意 `linux,code = <KEY_POWER>` 里的 `KEY_POWER` 就是 §2.4 那个 uapi 头文件里的值**——`include/dt-bindings/input/linux-event-codes.h` 是一个指向 `include/uapi/linux/input-event-codes.h` 的符号链接，DTS 和用户态共用同一份定义。逐属性的消费者表见 `06-devicetree.md` §1.1。

触摸屏侧的实例（`arch/arm64/boot/dts/exynos/exynos5433-tm2.dts:91-94`）更能说明"核心真正会读的属性"有多少：

```
&stmfts {
	touchscreen-size-x = <1439>;
	touchscreen-size-y = <2559>;
};
```

这两条由 `drivers/input/touchscreen.c` 里的 `touchscreen_parse_properties()` 统一消费，全树 36 个调用点——**这是 input 核心侧唯一一批直接读 DT 的属性**（其余属性全由具体驱动自己读）。分工表见 `06-devicetree.md` §1.1，静默失效陷阱见 `06-devicetree.md` §7.3。

---

## §3 概念模型

### 3.1 分层图

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 用户态                                                              │
 │  libinput / X / Wayland        evtest / evemu        自造设备的程序   │
 │        │                            │                     │         │
 │        ▼                            ▼                     ▼         │
 │  /dev/input/eventN           /dev/input/eventN      /dev/uinput      │
 └────────┬─────────────────────────────┬───────────────────┬──────────┘
          │  read/poll/ioctl            │                   │ ioctl+write
 ═════════╪═════════════════════════════╪═══════════════════╪══════════
          │            内核             │                   │
 ┌────────▼─────────────────────────────▼──────┐   ┌────────▼─────────┐
 │ handler 层（input_handler，12 个注册点）      │   │ uinput           │
 │  evdev  mousedev  joydev  input-leds  evbug  │   │ (造 input_dev)   │
 │  VT-kbd  sysrq  kgdboc  rfkill  apm  mac_hid │   └────────┬─────────┘
 └────────────────────┬─────────────────────────┘            │
                      │ handler->event / events / filter      │
 ┌────────────────────▼──────────────────────────────────────▼─────────┐
 │ input 核心（drivers/input/input.c，2770 行）                          │
 │   两个全局链表 + 匹配 + 能力校验 + 去重去抖 + 攒帧 + 分发             │
 │   input_event() → input_handle_event() → input_get_disposition()     │
 │                → input_pass_values() → input_to_handler()            │
 │   附属库：input-mt / ff-core / input-poller / touchscreen / *-keymap  │
 └────────────────────▲──────────────────────────────────────▲─────────┘
                      │ input_report_*() / input_sync()       │
 ┌────────────────────┴──────────┐              ┌─────────────┴────────┐
 │ 设备驱动（389 个文件）          │              │ 上游框架              │
 │  gpio_keys  edt-ft5x06  goodix│              │ HID（hid-input.c）    │
 │  pm8941-pwrkey  pwm-vibra  …  │              │ serio→atkbd（非 arm64）│
 └───────────────────────────────┘              │ rmi4                  │
                                                 └──────────────────────┘
```

### 3.2 三个"想不到的"结构性事实

这三条是理解这个子系统的门槛，任何一条搞错都会推出错误结论：

| 事实 | 为什么反直觉 | 依据 |
| --- | --- | --- |
| **没有总线，匹配由核心自己遍历完成** | 大部分子系统的匹配是 `bus_type.match` 回调 | `input_register_device()` 末尾 `list_for_each_entry(handler, &input_handler_list, node) input_attach_handler(dev, handler);`（`drivers/input/input.c:2469-2470`），**没有 `break`** |
| **匹配是"一对多"而不是"一对一"** | driver-device 模型是一个设备绑一个 driver | 同上——不 break 就意味着一个设备会被所有匹配的 handler 同时接管；返回值在 `:2470` 和 `:2542` 两个调用点都被丢弃 |
| **"不匹配"是常态，不是错误** | 通常 probe 失败要报错 | `input_attach_handler()`（`:1051-1066`）对 `-ENODEV` 故意不打日志：`:1061` 的条件是 `error != -ENODEV` |

★★ 第三条的实际后果：**内核日志里看不到"某个 handler 拒绝了某个设备"**。想知道一个设备到底被谁接管了，只能看 `/proc/bus/input/devices` 的 `H:` 行（`03-internals.md` §B8.2）。

### 3.3 命名约定

| 前缀 | 含义 | 举例 |
| --- | --- | --- |
| `input_report_*()` | 驱动 → 核心，报一个事件，不结帧 | `input_report_key()`（`include/linux/input.h:423-426`） |
| `input_sync()` / `input_mt_sync()` | 驱动 → 核心，结帧 | `include/linux/input.h:448-451` / `:453-456` |
| `input_set_*()` | 驱动侧声明能力/参数（注册前调） | `input_set_capability()`、`input_set_abs_params()` |
| `input_*_device()` | 核心侧对整个设备的动作 | `input_register_device()`、`input_open_device()`、`input_grab_device()` |
| `input_*_handler()` | 核心侧对 handler 的动作 | `input_register_handler()` |
| `input_*_handle()` | 核心侧对一条连线的动作 | `input_register_handle()`、`input_unregister_handle()` |
| `input_mt_*()` | 多点触控槽位记账 | `include/linux/input/mt.h` |
| `EVIOC*` | evdev 的 ioctl | `include/uapi/linux/input.h` |
| `UI_*` | uinput 的 ioctl | `include/uapi/linux/uinput.h` |

★ **`_device` / `_handler` / `_handle` 三个后缀差一个字母，作用对象完全不同**。近邻符号的完整区分表在 `05-usage.md` §11。

---

## §4 核心数据结构速览

完整字段表和内存关系图在 `02-datastructures.md`，这里只留"复习时需要立刻想起来的"。

### 4.1 四个结构体

| 结构体 | 位置 | 关键字段 | 一句话 |
| --- | --- | --- | --- |
| `struct input_dev` | `include/linux/input.h:137` | `evbit/keybit/absbit/…` 能力位图；`key/led/sw` 当前状态位图；`absinfo[]`；内嵌 `struct device dev`（`:198`）；`vals/num_vals/max_vals`（`:203-205`）；`h_list`（`:200`）；`node`（`:201`）；`grab`；`users`；`inhibited` | 设备 + 它的能力 + 它的当前状态 + 它的攒帧缓冲 |
| `struct input_handler` | `include/linux/input.h:310-331` | `event`/`events`/`filter`（`:317`）；`connect`/`disconnect`；`id_table`（`:327`）；`minor`；`h_list`（`:329`）；`node`（`:330`） | 一个想消费事件的模块 |
| `struct input_handle` | `include/linux/input.h:345-357` | `open`（`int`，`:349`）；`dev`（`:352`）；`handler`（`:353`）；`d_node`（`:355`）；`h_node`（`:356`） | 一条连线；内存由 handler 分配 |
| `struct input_value` | `include/linux/input.h:32-36` | `type`/`code`/`value` | 帧的元素；和 uapi 的 `struct input_event` 去掉时间戳后完全同构 |

★ **`struct input_value` 和 uapi 的 `struct input_event` 的关系值得单独记一下**：核心内部只搬三元组，时间戳是 evdev 在投递时才贴上的（`drivers/input/evdev.c:256-258` 用 `input_get_timestamp()` 的结果）。所以"事件时间戳"精确到的是**帧到达 evdev 的时刻**，不是硬件中断的时刻——除非驱动主动调了 `input_set_timestamp()`。

### 4.2 两个全局链表 + 四把锁

```
  input_dev_list      ──▶ dev ──▶ dev ──▶ dev …     （list_add_tail，drivers/input/input.c:2467）
  input_handler_list  ──▶ h   ──▶ h   ──▶ h   …     （list_add_tail，drivers/input/input.c:2539）
  两条链表由同一把 input_mutex 保护
```

| 锁 | 保护什么 | 上下文 |
| --- | --- | --- |
| `input_mutex`（全局） | 两条链表、连线的建立与拆除 | 可睡；注册/注销路径 |
| `dev->mutex`（每设备） | 打开/关闭、grab、keymap 修改、inhibit | 可睡 |
| `dev->event_lock`（每设备 spinlock） | **整条事件路径** | **关中断**（`drivers/input/input.c:430-432`） |
| `client->buffer_lock`（每 evdev fd） | 那个 fd 的环形缓冲 | evdev 内部；投递侧已在关中断上下文 |

★★ **`list_add_tail` 这个细节有实际后果**：**注册顺序就是通知顺序**。一个带 `filter` 回调的 handler 能不能截住另一个 handler 的事件，取决于 modprobe 顺序。四把锁的加锁顺序和每个回调的上下文约束见 `03-internals.md` §B1。

### 4.3 一个设备的两个"打开计数"

这是最容易看错的一处，单独列出来：

| 计数 | 谁加 | 语义 |
| --- | --- | --- |
| `handle->open` | `input_open_device()`（`drivers/input/input.c:639`） | **这条连线**上有没有人在用；只有 0/1 两个有效值 |
| `dev->users` | 同一函数（`:641`，写在 `if (dev->users++ \|\| dev->inhibited)` 里） | **这个设备**一共有多少个使用者；决定要不要调驱动的 `dev->open()` 上电 |

只有 `handle->open` 非零的连线才会收到事件（`input_pass_values()` 的 `:157-162`）。展开见 `03-internals.md` §A7 和 `08-feynman.md` §2。

---

## §5 核心机制速览

每条后面标的是承载它的章节号，复习时按需展开。

### 5.1 生命周期（时间轴）

| 机制 | 一句话 | 章节 |
| --- | --- | --- |
| 子系统自举 | 注册 `input_class`、`INPUT_MAJOR` 字符设备、procfs 入口 | `03-internals.md` §A1 |
| 分配 | `input_allocate_device()` 只填 `dev.type` 和 `dev.class`，**不填 `dev.bus`** | `03-internals.md` §A2 |
| 声明能力 | `input_set_capability()` / `input_set_abs_params()`，必须在注册前 | `03-internals.md` §A3 |
| 注册（分水岭） | 校验 → 算 `max_vals` → `device_add()` → 遍历所有 handler 尝试匹配 | `03-internals.md` §A4 |
| 匹配 | 三层：`input_match_device_id()` → `input_match_device()` → `input_attach_handler()`；`-ENODEV` 不打日志 | `03-internals.md` §A5 |
| 连线 | `input_register_handle()` 把 handle 挂进两条 `h_list` | `03-internals.md` §A6 |
| 打开 | `handle->open` 与 `dev->users` 两个计数；`dev->open()` 只在第一个使用者到来时调 | `03-internals.md` §A7 |
| 抢占 | `input_grab_device()` 设 `dev->grab`；释放时用 `handler->start` 回放状态 | `03-internals.md` §A9 |
| 关闭 | `input_close_device()` 递减两个计数，隐式解除 grab | `03-internals.md` §A10 |
| 拆连线 / 注销 | `input_unregister_handle()`；两个 `WARN_ON(!list_empty(...))` 兜底 | `03-internals.md` §A11、§A12、§A13 |

### 5.2 事件路径（横切）

| 机制 | 一句话 | 章节 |
| --- | --- | --- |
| 唯一决策点 | `input_get_disposition()` 一个函数决定每个事件是丢、是暂存、是转发、是同时改状态 | `03-internals.md` §A8.3 |
| 能力校验 | `is_event_supported()`：设备没声明的 type/code 静默丢弃 | `03-internals.md` §B2.1 |
| 状态去重 | `!!test_bit(code, dev->key) != !!value`，重复值直接丢（`drivers/input/input.c:273-277`） | `03-internals.md` §B2.2 |
| 去抖 | `input_defuzz_abs_event()`：`absinfo` 里的 `fuzz` 决定多小的变化算没变 | `03-internals.md` §B2.3 |
| MT slot 暂存 | `ABS_MT_SLOT` 不直接下发，等到有真实 MT 轴值才补发 | `03-internals.md` §B2.4 |
| 攒帧 / 劈帧 | `dev->vals[]` 攒到 `SYN_REPORT` 才下发；快满时核心自己插一个 value=1 的 `SYN_REPORT` | `03-internals.md` §A8.4 |
| 分发 | 遍历 `dev->h_list`，只发给 `handle->open` 非零的连线；`filter` 返回真则截断 | `03-internals.md` §A8.5、§A8.6 |
| 抑制 | `dev->inhibited` 在 `input_get_disposition()` 的**最前面**拦掉一切（`drivers/input/input.c:243-245`） | `03-internals.md` §B5 |
| 自动重复 | 每设备一个 timer；参数可由 `EV_REP` 事件从用户态改 | `03-internals.md` §B3 |
| 注入（反向） | `input_inject_event()` 从 handler 侧灌事件，用于点灯、力反馈、uinput | `03-internals.md` §A8.1 |

### 5.3 对外表面

| 机制 | 一句话 | 章节 |
| --- | --- | --- |
| 次设备号 | 每个 handler 声明 `minor` 基址，核心用位图分配 | `03-internals.md` §B4 |
| sysfs | 三个 `attribute_group`；九个 `capabilities/*` 由 `INPUT_DEV_CAP_ATTR` 宏生成 | `03-internals.md` §B8.1 |
| procfs | `/proc/bus/input/devices`——查"设备被谁接管了"的唯一入口 | `03-internals.md` §B8.2 |
| modalias / uevent | 让 udev 能自动加载 handler 模块 | `03-internals.md` §B8.3、§B9 |
| 键码映射 | `EVIOCGKEYCODE`/`SETKEYCODE` 的三种 scancode 宽度 | `03-internals.md` §B7 |
| 电源管理 | 挂起时释放所有按下的键，避免唤醒后残留按下状态 | `03-internals.md` §B6 |

### 5.4 十三个子框架

`04-extensions.md` 一节一个，篇首有"哪几个在 arm64 上真会碰到"的分诊表。速记：

```
  字符设备三件套 ─ evdev(§1) / mousedev(§1) / joydev(§1)   ← arm64 只开 evdev
  独立总线两条 ── serio(§2) / gameport(§12)                ← arm64 只剩 AMBA KMI
  功能分解总线 ── rmi4(§5)
  能力扩展 ────── ff-core+ff-memless(§3) / input-mt(§7) / input-poller(§8)
  边界与桥 ────── input-compat(§4) / uinput(§6) / input-leds(§9)
  DT 与 keymap ─ touchscreen.c(§10) / sparse+matrix+vivaldi keymap(§11)
  只消费不建节点 ─ evbug(§13.1) / apm-power(§13.2) / DualSense 耳机插孔(§13.3)
```

---

## §6 端到端调用路径

一次按键从中断到用户态 `read()` 返回的完整路径。**每一层都标出了锁在哪加、重试在哪做、tracepoint 埋在哪**。

### 6.1 主路径

```
 ① 硬件中断 / threaded IRQ
    gpio_keys_gpio_isr()  [drivers/input/keyboard/gpio_keys.c]
        │  锁：驱动自己的（gpio_keys 用 delayed work 做 debounce）
        ▼
 ② input_report_key(dev, KEY_POWER, 1)      include/linux/input.h:423-426
        │  只是 input_event(dev, EV_KEY, code, !!value) 的 inline 包装
        ▼
 ③ input_event()                            drivers/input/input.c:423-434
        │  ★ 锁在这里加：spin_lock_irqsave(&dev->event_lock)  (:430-432)
        │  ★ 从这一行往下全程关中断，直到 ⑨ 返回
        │  先过 is_event_supported()，设备没声明的能力直接静默返回
        ▼
 ④ input_handle_event()                     drivers/input/input.c:390-404
        │  lockdep_assert_held(&dev->event_lock)  (:146 在下游断言)
        │  ★ 顺手给内核熵池喂一勺：add_input_randomness()  (:400)
        ▼
 ⑤ input_get_disposition()                  drivers/input/input.c:237-345
        │  唯一决策点，返回 INPUT_IGNORE_EVENT / PASS_TO_HANDLERS /
        │  PASS_TO_DEVICE / PASS_TO_ALL / FLUSH 的位组合
        │  ★ dev->inhibited 在最前面拦掉一切  (:243-245)
        │  ★ 去重、去抖、MT slot 暂存都在这里
        ▼
 ⑥ input_event_dispose()                    drivers/input/input.c:347-388
        │  攒帧：dev->vals[dev->num_vals++] = v   (:366-369)
        │  ★ 只有 INPUT_FLUSH（即 SYN_REPORT）才继续往下，且要求 num_vals >= 2 (:373)
        │  ★ 快满时自己插一个 value=1 的 SYN_REPORT 并强制下发  (:383-386)
        ▼
 ⑦ input_pass_values()                      drivers/input/input.c:140-178
        │  dev->grab 非空 → 只发给它，否则遍历 dev->h_list  (:153-162)
        │  ★ 只发给 handle->open 非零的连线
        │  ★ 顺手做 autorepeat 启动 (:172)
        ▼
 ⑧ input_to_handler()                       drivers/input/input.c:105-133
        │  ★ filter 回调在这里：返回真的事件被从流里剔除
        │  然后调 handler->events()（批量）或 handler->event()（逐个）
        ▼
 ⑨ evdev_events()                            drivers/input/evdev.c:291-309
        │  取一次时间戳（整帧共用）：input_get_timestamp() (:296)
        │  遍历 evdev->client_list，每个 client 一份
        ▼
 ⑩ evdev_pass_values()                       drivers/input/evdev.c:244-286
        │  锁：spin_lock(&client->buffer_lock)
        │  注释明说 /* Interrupts are disabled, just acquire the lock. */ (:260)
        │  ★ 每个 client 再拦一次空帧  (:267-270)
        │  ★ 按类型过滤（__evdev_is_filtered，:264）
        ▼
 ⑪ __pass_event()                             drivers/input/evdev.c:214-242
        │  入队；缓冲满则丢最旧的一帧并补一条 SYN_DROPPED (:231)
        │  ★ 这是整条路径上唯一会告诉用户态"你丢事件了"的地方
        ▼
 ⑫ wake_up_interruptible_poll(&evdev->wait, ...)
        │  —— 关中断段在这里结束（回到 ③ 的 spin_unlock_irqrestore）
 ═══════ 上下文切换 ═══════
 ⑬ evdev_read()                               drivers/input/evdev.c:558-607
        │  出队 + copy_to_user，按 struct input_event 的布局
        ▼
 ⑭ 用户态拿到一串 struct input_event，以 SYN_REPORT 结尾
```

### 6.2 三个必须记住的路径事实

**(a) 锁只有一把，而且关中断。** ③ 到 ⑫ 整段在 `dev->event_lock` 里、关中断执行。所以：驱动的 `dev->event()` 回调不能睡（要点灯只能排 work）；handler 的 `event`/`events`/`filter` 回调也不能睡。上下文约束的完整表在 `03-internals.md` §B1.3。

**(b) 没有重试，只有丢弃。** 整条路径上没有任何一层会重试。丢弃点有五个，只有最后一个会通知用户态：

| 丢弃点 | 位置 | 用户态能知道吗 |
| --- | --- | --- |
| 设备没声明这个能力 | `is_event_supported()`（`drivers/input/input.c:62-66`） | **不能** |
| 状态没变（去重） | `drivers/input/input.c:273-277` | **不能** |
| 变化小于 `fuzz`（去抖） | `input_defuzz_abs_event()`（`drivers/input/input.c:68-82`） | **不能** |
| `dev->inhibited` | `drivers/input/input.c:243-245` | **不能** |
| evdev 缓冲满 | `drivers/input/evdev.c:231` | **能**，收到 `SYN_DROPPED` |

★★ 这张表是 input 调试最有用的一张表：**"我的事件没到用户态"有五种可能，其中四种完全静默**。这四种的观察手段见 `07-debug.md` §5.3，按症状找章节见 `07-debug.md` §0.2。

**(c) tracepoint 一个都没有——在 arm64 上是 0 个。** 这不是遗漏，是 (a) 的直接后果：整条路径关中断，而 `/dev/input/eventN` 本身已经是一条带时间戳的完整 trace。验证：

```
grep -rln 'TRACE_EVENT\|DECLARE_EVENT_CLASS' drivers/input/
```

只有 `drivers/input/keyboard/applespi_trace.h` 一个文件，而 `CONFIG_KEYBOARD_APPLESPI` 有 `depends on X86 || COMPILE_TEST`（`drivers/input/keyboard/Kconfig:77`）——**arm64 上编译不出来**。代价是上面那四个静默丢弃点在 ftrace 里也看不见，只能用 `function_graph` 或 kprobe 补。完整讨论见 `07-debug.md` §5.1 和 `08-feynman.md` §10。

### 6.3 反向路径（点灯 / 力反馈 / uinput）

```
 用户态 write(/dev/input/eventN)  或  echo 1 > /sys/class/leds/…/brightness
        │                                    │
        ▼                                    ▼
 evdev_write()                        input_leds_brightness_set()
        │                                    │  drivers/input/input-leds.c
        └────────────┬───────────────────────┘
                     ▼
        input_inject_event(handle, EV_LED, code, value)   drivers/input/input.c:448-466
                     │  ★ 第一个参数是 handle 不是 dev——它带上了注入者的身份，
                     │    从而能被 grab 检查正确处理
                     ▼
        走 ③–⑧ 完全同一条路（含 event_lock、含能力校验）
                     │
                     ▼
        dev->event() 回调 —— 由设备驱动去写硬件寄存器
```

★ **反向路径和正向路径共用同一条代码路径和同一把锁**，只是决策点给 `EV_LED`/`EV_SND`/`EV_REP`/`EV_FF` 打的是 `INPUT_PASS_TO_ALL`（同时给 handler 和设备）而不是只给 handler。这就是为什么"点灯"也会被 `evtest` 看到。

---

## §7 与内核其它子系统的关系

input 核心自己几乎不碰硬件，所以它的"邻居"分两类：**上游把事件喂给它的**，和**下游被它调用的**。

### 7.1 关系图

```
                    ┌──────────────┐   ┌──────────┐   ┌────────────┐
   上游（喂事件）→   │ HID          │   │ serio    │   │ rmi4       │
                    │ drivers/hid/ │   │ (非arm64)│   │ (I2C/SPI)  │
                    └──────┬───────┘   └────┬─────┘   └─────┬──────┘
                           └────────────────┴───────────────┘
                                            │ input_report_*()
   ┌─────────────┐                          ▼
   │ GPIO / IRQ  │──探测&中断──▶ ┏━━━━━━━━━━━━━━━━━━━━━━━┓
   │ I2C / SPI   │              ┃    input 核心          ┃
   │ regulator   │              ┃  drivers/input/input.c ┃
   │ pinctrl     │              ┗━━━━┳━━━━━━━━━┳━━━━━━━━━┛
   │ pwm         │                   │         │
   └─────────────┘                   │         │
                        ┌────────────┘         └───────────┐
                        ▼                                  ▼
   下游（被调用）→  ┌──────────────┐              ┌───────────────────┐
                   │ LED class    │              │ char dev / VFS    │
                   │ random（熵池）│              │ tty/vt、sysrq、kgdb│
                   │ PM（wakeup） │              │ rfkill、ALSA、V4L2 │
                   └──────────────┘              └───────────────────┘
```

### 7.2 逐条

| 邻居 | 方向 | 关系 | 依据 |
| --- | --- | --- | --- |
| **driver model / platform** | 上游 | `input_dev` 内嵌 `struct device`（`include/linux/input.h:198`），但 `dev.bus` 为空——它靠 `dev.class = &input_class` 出现在 `/sys/class/input/`，靠 `dev.parent` 挂到真正的父总线（i2c / spi / platform） | `drivers/input/input.c:2017-2018` 只填 `type` 和 `class` |
| **字符设备 / VFS** | 下游 | `INPUT_MAJOR` 13；每个 handler 声明 minor 基址，核心用位图分配 | `03-internals.md` §B4 |
| **GPIO / IRQ / I2C / SPI / regulator / pinctrl / pwm** | 上游 | 全部是**设备驱动**的依赖，不是核心的依赖。核心一行都不碰 | arm64 defconfig 里那批驱动（§2.8） |
| **random（熵池）** | 下游 | 每个事件顺手喂一勺 `add_input_randomness()`（`drivers/input/input.c:400`） | ★ 这是 input 唯一一处主动调用无关子系统的地方 |
| **LED class** | 双向 | `input-leds` 是一个 handler：把 `EV_LED` 事件桥成 `led_classdev`；反向 `echo` 亮度走 `input_inject_event()`（`drivers/input/input-leds.c:63`） | `04-extensions.md` §9 |
| **tty / vt** | 下游 | VT 键盘是一个 handler（`drivers/tty/vt/keyboard.c:1670`），处理控制台按键和 tty 切换 | §2.1 那张 12 行表 |
| **SysRq / kgdb** | 下游 | 各是一个 handler（`drivers/tty/sysrq.c:1043`、`drivers/tty/serial/kgdboc.c:114`）。**它们必须在内核里**，因为系统半死时用户态帮不上忙 | 同上 |
| **rfkill** | 下游 | 飞行模式键是一个 handler（`net/rfkill/input.c:336`） | 同上 |
| **ALSA** | 下游 | 某些 USB 声卡的旋钮/耳机插孔是 handler（`sound/usb/mixer_quirks.c:768`） | 同上；`04-extensions.md` §13.3 |
| **PM / wakeup** | 双向 | `wakeup-source` 属性让按键能唤醒系统；挂起时核心主动释放所有按下的键 | `03-internals.md` §B6 |
| **V4L2** | 下游 | rmi4 F54 的原始电容图走 `/dev/v4l-touch0` 而不是 debugfs | `07-debug.md` §6.2 |
| **HID** | 上游 | 在 input 之上，最终调 `input_register_device()` | §2.7 |
| **IIO** | 无关系 | 平行框架，边界见 §2.7 |

★★ **注意第三行**：`GPIO / I2C / regulator / pinctrl` 这些看起来"input 肯定要用"的子系统，**input 核心一行都不碰**。它们是设备驱动的依赖。这个区分在读代码时很重要——`drivers/input/input.c` 里搜不到任何 `gpio_` 或 `i2c_`，所有硬件耦合都在 `drivers/input/*/` 那 389 个文件里。

★ **`net/rfkill/input.c` 这个文件名值得单独记一笔**：本树有两个 `input.c`（`drivers/input/input.c` 和 `net/rfkill/input.c`），所以本文档集里**从不写裸的 `input.c:NNN`**，一律写全路径。

---

## §8 本文档集导读

### 8.1 按角色

**如果你在写一个输入设备驱动**（最常见的场景）：

```
  01-api.md §0（代码地图）→ 01-api.md §1（设备驱动侧 API）+ §5（辅助库）
     → 05-usage.md 里对应你设备形态的那一节：§1 中断按键 / §2 轮询 / §3 多点触摸 / §4 矩阵键盘
     → 06-devicetree.md §1.1（核心真正会读的 14 个属性）+ §2/§3/§4（按设备类型看属性写在哪个节点）
     → 07-debug.md §9.1（用 evtest 验证）
  遇到多点触控 → 04-extensions.md §7 + 08-feynman.md §6
  遇到无中断设备 → 04-extensions.md §8（input-poller）
  遇到需要振动/蜂鸣 → 04-extensions.md §3（力反馈）
```

**如果你在写一个 handler（消费事件的内核模块）**：

```
  02-datastructures.md §1.0（先分清三组关系）
     → 03-internals.md §A5-A7（匹配、连线、打开）
     → 04-extensions.md §13（三个最短的 handler 骨架，可以整段读完）
     → 05-usage.md（handler 角色那一节）
  想截断事件流 → 03-internals.md §A8.5、§A8.6（filter 的位置和语义）
```

**如果你在写用户态程序读 `/dev/input/eventN`**：

```
  02-datastructures.md（struct input_event 的布局）
     → 05-usage.md §9（evdev 用户态骨架）
     → 07-debug.md §9.2（EVIOC* ioctl 全清单）
  要拿轴的范围 → 只能用 EVIOCGABS，sysfs 里没有（08-feynman.md §7）
  要独占设备 → 08-feynman.md §8（EVIOCGRAB 的两层语义）
  收到 SYN_DROPPED 怎么办 → §6.2(b) 那张丢弃点表 + 04-extensions.md §1
```

**如果你在造虚拟输入设备（测试/回放）**：

```
  04-extensions.md §6（uinput）→ 05-usage.md §10（uinput 骨架）
     → 07-debug.md §9.4
```

**如果你在查一个"事件没到"的故障**：

```
  §6.2(b) 五个丢弃点表 → 07-debug.md §0.2（按症状找章节）
     → cat /proc/bus/input/devices 看 H: 行（03-internals.md §B8.2）
     → 07-debug.md §5.3（没有 tracepoint 时用什么）
     → 07-debug.md §8（错误码分诊）
  怀疑是 DT 配错了 → 06-devicetree.md §7（静默失效决策树）
```

**如果你只是想搞懂它为什么这么设计**：

```
  08-feynman.md 十个主题，从 §1（为什么不注册在总线上）读起
```

### 8.2 按篇

| 篇 | 回答什么 | 什么时候读 |
| --- | --- | --- |
| `00-overview.md`（本篇） | 整体形状、背景、端到端路径 | 第一次入门，或隔几个月回来复习 |
| `01-api.md` | 代码在哪、有哪些 API、错误码什么意思 | 手边要写代码时 |
| `02-datastructures.md` | 结构体长什么样、谁指向谁、锁保护什么 | 读核心代码时对照 |
| `03-internals.md` | 每个函数逐段做了什么 | 要改核心、或要精确知道某个行为的边界 |
| `04-extensions.md` | 十三个子框架各是什么 | 碰到 MT / 力反馈 / uinput / serio / rmi4 时 |
| `05-usage.md` | 各角色的代码骨架 + 近邻符号区分 | 要动手写代码时（★ 骨架未编译，见该篇篇首） |
| `06-devicetree.md` | DTS 怎么写、属性谁消费、配错怎么查 | 做板级适配时 |
| `07-debug.md` | 开关、日志、sysfs、procfs、工具、错误码 | 现场排障 |
| `08-feynman.md` | 十个"为什么"，带类比和类比边界 | 概念上卡住时 |
| `99-evidence.md` | 每个论断的出处、方法、未验证项 | 要引用本文档集做决策前 |

★ **本篇里所有数字的复现命令都集中在 `99-evidence.md` §3**；每篇的逐条取证表在 `99-evidence.md` §4；本文档集**没有验证**的部分（未编译、只读符号表、推断非观察、版本时效）在 `99-evidence.md` §5——引用任何结论之前先看那一节。
