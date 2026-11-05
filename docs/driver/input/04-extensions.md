---
title: 扩展机制
---

# input 子系统 · 子框架与扩展

- 内核版本：Linux 6.1.158（`f6e38ae624cf`），目标架构 **arm64**
- **本篇的组织轴是「一节一个独立框架、互不调用」**。十三节之间没有调用关系，可以按需跳读，不必从头看到尾。唯一的例外在 §3（`ff-memless` 建在 `ff-core` 上）和 §11（三个 keymap 库彼此独立但放在同一节对照），两处都在节内说明
- 每一节的开头都交代**它挂在 `03-internals.md` Part A 时间线的哪一点上**——这是把「一个独立框架」接回主流程的唯一钩子
- 想知道核心自己怎么运转，去 `03-internals.md`；想知道怎么写代码用这些框架，去 `05-usage.md`

---

## §0 分诊表：哪几个在 arm64 上真会碰到

Kconfig 行号默认指 `drivers/input/Kconfig`；其余写全路径。「时间线挂点」一栏的 `A1`…`A13` 是 `03-internals.md` Part A 的章节号。

| # | 框架 | 编译产物 / Kconfig | 是不是 handler | 时间线挂点 | arm64 是否相关 |
|---|---|---|---|---|---|
| §1 | evdev | `evdev.o`（`INPUT_EVDEV`，`:146`） | 是 | A5→A6→A7 | 相关，实际上是必开项 |
| §1 | mousedev | `mousedev.o`（`INPUT_MOUSEDEV`，`:89`） | 是 | A5→A6→A7 | 相关，但是纯遗产接口 |
| §1 | joydev | `joydev.o`（`INPUT_JOYDEV`，`:133`） | 是 | A5→A6→A7 | 相关 |
| §2 | serio | `drivers/input/serio/`（`SERIO`，`drivers/input/serio/Kconfig:5`） | 否，**自带 `bus_type`** | 在 A2 之前：它产出的驱动才去造 `input_dev` | 部分相关：核心可用，`SERIO_I8042` 不可用 |
| §3 | ff-core | 编进 `input-core.o`，无独立 Kconfig | 否 | A3 之后、A4 之前 | 相关 |
| §3 | ff-memless | `ff-memless.o`（`INPUT_FF_MEMLESS`，`:41`） | 否 | A3 之后、A4 之前 | 相关 |
| §4 | input-compat | 编进 `input-core.o`，随 `CONFIG_COMPAT` 分叉 | 否 | A8 之后：用户态边界 | 相关，且 arm64 上 `CONFIG_COMPAT` 通常是开的 |
| §5 | rmi4 | `drivers/input/rmi4/`（`RMI4_CORE`，`drivers/input/rmi4/Kconfig:5`） | 否，**自带 `bus_type`** | **它自己走完 A2→A3→A4**（`rmi_driver.c:1190` / `:1217`） | 相关：I2C/SPI 触摸屏走这条路 |
| §6 | uinput | `drivers/input/misc/uinput.c`（`INPUT_UINPUT`，`drivers/input/misc/Kconfig:513`） | 否，是 `miscdevice` | 它**自己**走完 A2→A3→A4 | 相关 |
| §7 | input-mt | 编进 `input-core.o`，无独立 Kconfig | 否 | A3（声明能力）+ A8.4（事件路径） | 相关 |
| §8 | input-poller | 编进 `input-core.o`，无独立 Kconfig | 否 | A3 之后、A4 之前 | 相关 |
| §9 | input-leds | `input-leds.o`（`INPUT_LEDS`，`:28`） | 是 | A5→A6→A7 | 相关 |
| §10 | touchscreen.c | 编进 `input-core.o`，无独立 Kconfig | 否 | A3（用 DT 属性填 `absinfo`） | 相关，DT 平台的主力 |
| §11 | sparse-keymap | `sparse-keymap.o`（`INPUT_SPARSEKMAP`，`:54`） | 否 | A3 | 可编，但消费者几乎全是 x86 平台驱动 |
| §11 | matrix-keymap | `matrix-keymap.o`（`INPUT_MATRIXKMAP`，`:67`） | 否 | A3 | 相关 |
| §11 | vivaldi-fmap | `vivaldi-fmap.o`（`INPUT_VIVALDIFMAP`，`:80`） | 否 | A4 之后（sysfs 属性） | 相关 |
| §12 | gameport | `drivers/input/gameport/`（`GAMEPORT`，`drivers/input/gameport/Kconfig:5`） | 否，**自带 `bus_type`** | 在 A2 之前 | 五项 Kconfig 全都能选中，**但实际拿不到设备**（§12.5） |
| §13 | evbug | `evbug.o`（`INPUT_EVBUG`，`:155`） | 是 | A5→A6→A7 | 相关，调试用 |
| §13 | apm-power | `apm-power.o`（`INPUT_APMPOWER`，`:169`） | 是 | A5→A6→A7 | **不相关**：依赖 `APM_EMULATION` |
| §13 | DualSense jack | `sound/usb/mixer_quirks.c:531-788`（`SND_USB_AUDIO` + `IS_REACHABLE(CONFIG_INPUT)`） | 是（**唯一动态注册的**） | A5→A6→A7 | 相关 |

三条从这张表读出来的结论：

1. **有六个框架没有自己的 Kconfig 开关**（ff-core、input-mt、input-poller、touchscreen.c、input-compat，以及 `input.c` 自己）——它们无条件编进 `input-core.o`。命令：`grep -n 'input-core-y' drivers/input/Makefile`。这意味着「我没开那个选项所以不会碰到」这句话对它们不成立
2. **三个框架自带 `bus_type`**（serio、gameport、rmi4），也就是说 input 目录下其实住着三条独立的总线。★ 但它们和 input 核心的关系**不一样**：serio 和 gameport 纯粹是**生产者的上游**（端口上挂的 `atkbd` 驱动才是那个造 `input_dev` 的东西），而 **rmi4 的核心自己就造 `input_dev`**（`drivers/input/rmi4/rmi_driver.c:1190` 分配、`:1217` 注册）。所以「自带总线 ⇒ 不碰 `input_dev`」这个推论只对前两个成立
3. **arm64 上真正会被裁掉的只有两项**：`apm-power`（§13）和 `SERIO_I8042`（§2）。gameport（§12）是第三种情况——**Kconfig 层面一项都不排斥 arm64**（`GAMEPORT` 只 `depends on !UML`，两个 PCI 端口驱动只 `depends on PCI`），能编、能加载，只是四个端口驱动探的都是 x86 端口 I/O 空间里的老硬件，arm64 上永远探不到东西（§12.5）

### §0.1 哪些内容不在本篇

| 你想查的 | 去哪 |
|---|---|
| `input_event()` / `input_handle_event()` 的分发逻辑 | `03-internals.md` A8 |
| 四把锁的分工与加锁顺序 | `03-internals.md` B1 |
| 这些框架的对外函数签名一览 | `01-api.md` |
| `struct input_dev` / `input_handler` / `input_handle` 的字段 | `02-datastructures.md` §2 |
| 怎么写一个用这些框架的驱动 | `05-usage.md` |
| `touchscreen-*` 这些 DT 属性该写在哪个节点 | `06-devicetree.md` |
| evbug 的输出长什么样、怎么打开 | `07-debug.md` |
| 每一节的证据等级、读了哪些行 | `99-evidence.md` §2 |

---

## §1 三个字符设备 handler：evdev / mousedev / joydev

**挂点**：三者都是标准 handler，走 A5（匹配）→ A6（`input_register_handle()`）→ A7（`input_open_device()`）。三者的 `connect` 回调里各自 `cdev_device_add()` 出一个字符设备。

这一节把三个放在一起，因为**它们是同一个模式的三份实现**，逐项对照比分开讲省一半篇幅。

★ 三个里只有 `joydev` 有一份完整的官方 uapi 文档：`Documentation/input/joydev/joystick-api.rst`（348 行，`/dev/input/jsN` 的 `struct js_event` 与全部 ioctl）和 `Documentation/input/joydev/joystick.rst`（586 行，硬件与历史）。本节只覆盖 joydev 作为 handler 的那一面（`id_table`、`match`、次设备号、ioctl 个数），**没有逐条复述它的 uapi 语义**——要写 `/dev/input/jsN` 的用户态程序请直接读那两篇；`evdev` 侧的 uapi 在 `05-usage.md` §9。另外 `Documentation/input/gamepad.rst`（197 行）规定的是**手柄按键的语义约定**（`BTN_SOUTH`/`BTN_EAST`… 该对应手柄上哪个物理键），那是 uapi 码点的命名规范，不是本子系统的机制，本文档集不覆盖。

### §1.1 三者逐项对照

| 项 | evdev | mousedev | joydev |
|---|---|---|---|
| 文件 / 行数 | `drivers/input/evdev.c` / 1446 | `drivers/input/mousedev.c` / 1125 | `drivers/input/joydev.c` / 1098 |
| 设备节点名 | `/dev/input/eventN` | `/dev/input/mouseN` + `/dev/input/mice` | `/dev/input/jsN` |
| 传统号段基址 / 个数 | 64 / 32（`evdev.c:10-11`） | 32 / 31（`mousedev.c:11-12`） | 0 / 16（`joydev.c:31-32`） |
| `id_table` 条目数 | 1（空条目，匹配一切） | 5（`mousedev.c:1002-1049`） | 7（`joydev.c:1028-1072`） |
| 有 `match` 回调吗 | 无 | 无 | **有**（`joydev_match()`，`:898-909`） |
| 事件回调 | `event` + **`events`** | `event` | `event` |
| 每 client 缓冲 | 变长环形，`roundup_pow_of_two()` 算出来 | 定长 16 个 `mousedev_motion` | 定长 64 个 `struct js_event` |
| 缓冲溢出怎么办 | 丢整队 + 补 `SYN_DROPPED` | 覆盖最旧的 | 丢整队并置 `startup` 重放 |
| 转换损耗 | **无**，原样透传 `input_event` | 有损：压成 PS/2 3~4 字节包 | 有损：按 `corr[]` 线性归一到 `__s16` |
| ioctl 数量 | 25 个 case（`evdev.c:1046-1179`） | 无 ioctl，靠 `write()` 传 PS/2 命令 | 14 个 case（`joydev.c:521-582`） |
| 遍历 client 用什么 | **RCU**（`evdev.c:298`） | `spin_lock(&mousedev->client_lock)` | `spin_lock(&joydev->client_lock)` |

★ **只有 evdev 是无损的**。mousedev 和 joydev 都在内核里做了一次不可逆的降维——`mousedev` 把绝对坐标积分成相对位移（`mousedev_abs_event()`，`mousedev.c:167-202`），`joydev` 把 `input_absinfo` 的范围硬压到 `-32767..32767`（`joydev_correct()`，`joydev.c:68-86`）。**所以「用户态读到的值和驱动报的值不一样」在这两个接口上是设计，不是 bug**；要原始值只能走 evdev。

### §1.2 次设备号段表

号段边界写死在 `drivers/input/input.c:34-35`：`INPUT_MAX_CHAR_DEVICES` 是 1024，`INPUT_FIRST_DYNAMIC_DEV` 是 256。主设备号是 `INPUT_MAJOR`（13）。

| 号段 | 归谁 | 常量 | `allow_dynamic` | 溢出之后 |
|---|---|---|---|---|
| 0–15 | joydev | `JOYDEV_MINOR_BASE 0` / `JOYDEV_MINORS 16` | `true` | 落到 256+ 动态段，节点名照旧 `jsN`，但 N 不再等于次设备号 |
| 32–62 | mousedev | `MOUSEDEV_MINOR_BASE 32` / `MOUSEDEV_MINORS 31` | `true` | 同上 |
| 63 | mousedev 的混合设备 `/dev/input/mice` | `MOUSEDEV_MIX 63` | **`false`** | 拿不到就直接失败——这个号不能浮动 |
| 64–95 | evdev | `EVDEV_MINOR_BASE 64` / `EVDEV_MINORS 32` | `true` | 同上 |
| 96–255 | 没人用 | — | — | 传统段的空洞 |
| 256–1023 | 动态分配池 | `INPUT_FIRST_DYNAMIC_DEV` | — | `input_get_new_minor()` 返回 `-ENOSPC`（见 `03-internals.md` B4） |

★ **号段之间的空洞是真的空洞**：16–31 和 96–255 没有任何 handler 声明，`input_get_new_minor()` 也不会去用它们（它只在 `[base, base+count)` 和 `[256, 1024)` 两个区间里找）。所以一台机器最多只能有 32 个 `eventN` 落在传统段里，第 33 个开始走 256+。

★ **`/dev/input/mice` 是全树唯一一个 `allow_dynamic = false` 的调用点。** 重跑：

```bash
grep -rn --include=*.c 'input_get_new_minor(' . | grep -v 'drivers/input/input.c'
```

四个调用点里三个传 `true`（`evdev.c:1345`、`mousedev.c:833`、`joydev.c:918`），只有 `mousedev.c:829` 传 `false`——它和 `:833` 同在 `mousedev_reserve_minor(bool mixdev)`（`:824-840`）里，由那个 `mixdev` 形参二选一。两条分支的失败日志还不一样：`"failed to reserve mixdev minor: %d\n"`（`:830`）对 `"failed to reserve new minor: %d\n"`（`:836`）。

### §1.3 evdev —— 唯一的正路

`drivers/input/evdev.c`，1446 行。两个结构：`struct evdev`（`:28-38`，一个设备一份）和 `struct evdev_client`（`:40-54`，一个 fd 一份）。

**缓冲区大小是算出来的，不是配置的**（`evdev_compute_buffer_size()`，`:461-468`）：

```c
/* drivers/input/evdev.c:461-468 —— 原文语义，非逐字抄写 */
n_events = dev->hint_events_per_packet * EVDEV_BUF_PACKETS;   /* ×8 */
if (n_events < EVDEV_MIN_BUFFER_SIZE)
        n_events = EVDEV_MIN_BUFFER_SIZE;                     /* 下限 64 */
return roundup_pow_of_two(n_events);
```

所以驱动调 `input_set_events_per_packet()` 报的那个数字**直接乘 8 变成 evdev 每个 fd 的内存占用**。一个 10 点触控屏 `hint_events_per_packet` 会到 60~70 量级，`roundup_pow_of_two(60*8=480)` = 512 个 `input_event`；`struct evdev_client` 用 `kvzalloc(struct_size(client, buffer, bufsize))` 一次分配（`:479`），是柔性数组不是二次分配。

**溢出的处理是「丢整队、只留最新的一个」**（`__pass_event()`，`:214-242`）：

| 情况 | 动作 |
|---|---|
| 队列没满 | 事件入队；若是 `SYN_REPORT` 则更新 `packet_head` 并 `kill_fasync()` |
| 队列满 | 把 `tail` 拨到 `head - 2`，在那一格写入 `SYN_DROPPED`，紧接着是刚到的这个事件 |

★ 满队时**留下的不是"最旧的 N 个"也不是"最新的 N 个"，而是恰好两个**：一个 `SYN_DROPPED` 加一个刚到的事件。用户态看到 `SYN_DROPPED` 的正确反应是「我的状态机不可信了，用 `EVIOCGKEY`/`EVIOCGABS`/`EVIOCGMTSLOTS` 重新拉一次全量状态」——这也是 `Documentation/input/event-codes.rst` 里说的做法。

**事件掩码（`EVIOCSMASK`）是两级的**（`__evdev_is_filtered()`，`:75-98`）：

```
evmasks[0]      → 按 type 过滤（哪些 EV_* 想要）
evmasks[type]   → 按 code 过滤（该 type 下哪些 code 想要）
```

两条例外写在代码里：`EV_SYN` 永不过滤；`type` 超出 `EV_CNT` 的也永不过滤。**没设过的掩码读回来是全 0xff**（`evdev_get_mask()`，`:991-1027`），即「什么都要」；而 `evdev_get_mask_cnt()`（`:56-72`）里 `[EV_SYN] = EV_CNT` 而不是 `SYN_CNT`——这不是笔误，是让 `evmasks[0]` 这一格的位数正好等于 type 的个数。

`ioctl` 的分发有三层（`evdev_do_ioctl()`，`:1029-1254`）：

| 层 | 位置 | 覆盖 |
|---|---|---|
| 固定命令 | `:1046-1141` | `EVIOCGVERSION`、`EVIOCGID`、`EVIOCGREP`/`SREP`、`EVIOCGRAB`、`EVIOCREVOKE`、`EVIOCGMASK`/`SMASK`、`EVIOCSCLOCKID`、四个 keycode 命令等 |
| 变长命令（大小编在 cmd 里） | `:1144-1191` | `EVIOCGPROP`、`EVIOCGMTSLOTS`、`EVIOCGKEY`、`EVIOCGLED`、`EVIOCGSND`、`EVIOCGSW`、`EVIOCGNAME`、`EVIOCGPHYS`、`EVIOCGUNIQ`、`EVIOCSFF` —— 都写成 `case EVIOCGXXX(0):`，靠 `_IOC_SIZE(cmd)` 取长度 |
| 通用位图读写 | `:1194-1246` | `EVIOCGBIT(ev, len)` 的九张能力位图和四张状态位图。**先用 `_IOC_TYPE(cmd) != 'E'` 挡掉不属于自己的 ioctl**（`:1194`） |

★ **`EVIOC_MASK_SIZE(nr)` 这个宏（`:1144`）只在 evdev 里存在**，作用是把 ioctl 号里的 size 字段抹掉好做 `switch`。重跑：`grep -rn 'EVIOC_MASK_SIZE' .` 只命中 `drivers/input/evdev.c`（宏定义 1 行 + 使用 3 行）。

三个必须知道的边界行为：

| 调用 | 边界 | 位置 |
|---|---|---|
| `read()` 且 `count == 0` | **不是** EOF，而是一条「探测错误」的通路：设备已死就返回 `-ENODEV`，否则返回 0 | `evdev_read()`，`:558-607` |
| `write()` 且 `count == 0` | 合法，直接返回 0；`0 < count < input_event_size()` 才是 `-EINVAL` | `evdev_write()`，`:502-538` |
| `poll()` | **只要设备活着就永远带 `EPOLLOUT|EPOLLWRNORM`**——写方向从不阻塞。设备死了或被 revoke 才有 `EPOLLHUP|EPOLLERR` | `evdev_poll()`，`:610-627` |

★ `evdev_write()` 里那个 `cond_resched()`（在 `input_inject_event()` 的循环里）说明**一次 `write()` 可以注入任意多个事件而不会独占 CPU**——这是 uinput 之外第二条从用户态往内核灌事件的路，前提是有写权限。

### §1.4 mousedev —— 一台 PS/2 鼠标模拟器

`drivers/input/mousedev.c`，1125 行。它做的事比名字暗示的多：**它是一个协议翻译器**，把 input 事件翻成三种 PS/2 变体的字节流。

三种模式（`enum mousedev_emul`，`:82-86`）和它们的握手序列：

| 模式 | 包长 | 附带信息 | 进入方式 |
|---|---|---|---|
| `MOUSEDEV_EMUL_PS2` | 3 字节 | 只有 3 个键、无滚轮 | 默认；`0xff`（Reset）也回到这里 |
| `MOUSEDEV_EMUL_IMPS` | 4 字节 | 第 4 字节是 `dz`（±127），第 1 字节多两个键 | 用户态按 `mousedev_imps_seq` = `{0xf3, 200, 0xf3, 100, 0xf3, 80}` 写进来（`:113`） |
| `MOUSEDEV_EMUL_EXPS` | 4 字节 | 第 4 字节低 4 位是 `dz`（±7），高位放第 4/5 键 | 序列是 `mousedev_imex_seq` = `{0xf3, 200, 0xf3, 200, 0xf3, 80}`（`:114`） |

★ **两个序列只差第 4 个字节**（100 对 200）。它们是真实 PS/2 鼠标的 "Set Sample Rate" 命令三连，用户态的 X 驱动靠这套魔法序列探测滚轮能力——mousedev 忠实地把这个协议约定实现了一遍。

`mousedev_generate_response()`（`:628-672`）响应五个 PS/2 命令，其余一律只回一个 `0xfa`（ACK）：

| 命令 | 回什么 |
|---|---|
| `0xeb` Poll | ACK + 一个完整数据包（`bufsiz` 要额外 `++` 补 ACK 那一字节） |
| `0xf2` Get ID | ACK + `0`（PS2）/ `3`（IMPS）/ `4`（EXPS） |
| `0xe9` Get info | ACK + `0x60, 3, 200` —— 三个写死的常量 |
| `0xff` Reset | ACK + `0xaa, 0x00`，并把 `impsseq`/`imexseq` 清零、模式退回 PS2 |
| 其它 | 只有 ACK（`bufsiz = 1`） |

**位移的截断是有记忆的**（`mousedev_packet()`，`:571-626`）：

```c
/* drivers/input/mousedev.c:576-580 —— 原文 */
dx = clamp_val(p->dx, -127, 127);
p->dx -= dx;
```

★ 减法这一步是关键：**超出一个包能表达的位移会留在队列里，下一个包接着发**。所以快速划过屏幕不会丢距离，只会拆成多个包——代价是 `mousedev_packet()` 只在 `!p->dx && !p->dy && !p->dz` 时才推进 `tail`（`:617-624`），一个大位移会把队列头卡住直到发完。

**混合设备 `/dev/input/mice` 的引用计数是套了两层的**：

| 层 | 函数 | 干什么 |
|---|---|---|
| 外层 | `mixdev_open_devices()` / `mixdev_close_devices()`（`:454-500`） | 拿 `mixdev->mutex`，在 `open` 计数 0↔1 翻转时，遍历 `mousedev_mix_list` 把**每一个**真实 mousedev 打开/关闭 |
| 内层 | `mousedev_open_device()` / `mousedev_close_device()`（`:419-447`） | 每个真实设备自己的 `open` 计数，最终落到 `input_open_device()` |

标记位是每设备的 `opened_by_mixdev`（`:58-80` 结构体里），用来区分「这个设备是被 `/dev/input/mice` 打开的」和「被自己的 `/dev/input/mouseN` 打开的」。★ 外层遍历时 `if (mousedev_open_device(mousedev)) continue;`（`:466-467`）——**单个设备打开失败被静默跳过**，`/dev/input/mice` 照样成功打开，只是少一个设备的事件。日志里什么都不会有。

`mousedev_ids[]`（`:1002-1049`）五个条目在挑什么：

| # | 条目大意 |
|---|---|
| 1 | 有 `EV_KEY`+`BTN_LEFT` 且有 `EV_REL`+`REL_X` —— 普通鼠标 |
| 2 | 有 `EV_KEY` 且有 `EV_REL`+`REL_WHEEL` —— 独立滚轮设备 |
| 3 | 有 `EV_KEY`+`BTN_TOUCH` 且有 `EV_ABS`+`ABS_X` —— 数位板类 |
| 4 | 有 `EV_KEY`+`BTN_TOOL_FINGER` 且有 `EV_ABS`+`ABS_X` —— 触摸板 |
| 5 | 有 `EV_KEY`+`BTN_LEFT` 且有 `EV_ABS`+`ABS_X` —— 绝对坐标鼠标（虚拟机、BMC） |

### §1.5 joydev —— 两套接口叠在一个 fd 上

`drivers/input/joydev.c`，1098 行。它的特殊之处是**一个文件描述符上同时活着新旧两套读协议**，靠第一次 `read()` 的长度来区分（`joydev_read()`，`:383-431`）：

| `read()` 的 count | 走哪条路 | 返回什么 |
|---|---|---|
| `< sizeof(struct js_event)`（即 < 8） | `joydev_0x_read()`（`:340-373`） | 旧的 `struct JS_DATA_TYPE`：2 个 `__s16` 轴 + 一个 32 位按键位图 |
| `>= sizeof(struct js_event)` | 新接口 | 一个或多个 `struct js_event` |

★ **旧接口这一条路会把读者自己的事件队列清空**（`joydev.c:340-373` 里重置 `client->startup` 和 `head`/`tail`），也就是说**在同一个 fd 上混用两套读法会互相破坏状态**。旧接口还只能表达 2 个轴、32 个键，且轴值被压成 `(abs[0] / 256 + 128) >> glue.JS_CORR.x` 这种 8 位量级。

**新打开的 fd 会先收到一串合成的初始事件**（`joydev_generate_startup_event()`，`:287-317`）：先 `nkey` 个 `JS_EVENT_BUTTON | JS_EVENT_INIT`，再 `nabs` 个 `JS_EVENT_AXIS | JS_EVENT_INIT`，把当前状态一次性交给用户态，之后才转入真实事件流。所以 joydev 的 `open` 语义里**天然包含一次全量状态同步**——evdev 没有这个，evdev 的读者必须自己用 `EVIOCG*` 拉。

**轴值的归一化在 `connect` 时就算好了系数**（`joydev_connect()`，`:975-989`）：

```c
/* drivers/input/joydev.c:983-989 —— 原文 */
t = (input_abs_get_max(dev, j) + input_abs_get_min(dev, j)) / 2;
joydev->corr[i].coef[0] = t - input_abs_get_flat(dev, j);
joydev->corr[i].coef[1] = t + input_abs_get_flat(dev, j);
t = (input_abs_get_max(dev, j) - input_abs_get_min(dev, j)) / 2
        - 2 * input_abs_get_flat(dev, j);
if (t) {
        joydev->corr[i].coef[2] = (1 << 29) / t;
        joydev->corr[i].coef[3] = (1 << 29) / t;
}
```

★ **`input_absinfo.flat` 在 joydev 里是活的**——它变成死区（`coef[0]`/`coef[1]` 这一对边界）。这和 `03-internals.md` B2.3 的结论并不矛盾：**核心自己不用 `flat`**（`input_defuzz_abs_event()` 只看 `fuzz`），但 joydev 这个 handler 用。所以「`flat` 是死字段」这句话只对核心成立，对 joydev 不成立。类型固定是 `JS_CORR_BROKEN`（`:980`），只有 `max == min` 的轴才是 `JS_CORR_NONE`（`:977-979`）。

**joydev 是唯一实现了 `match` 回调的字符设备 handler**，因为它必须挡掉两类误判（`joydev_match()`，`:898-909`）：

| 门 | 函数 | 挡什么 |
|---|---|---|
| 1 | `joydev_dev_is_blacklisted()`（`:805-818`） | `joydev_blacklist[]`（`:777-803`）里点名的设备，命中时打 `dev_dbg(&dev->dev, "joydev: blacklisting '%s'\n", dev->name)` |
| 2 | `joydev_dev_is_absolute_mouse()`（`:820-896`） | 虚拟机/BMC 的绝对坐标鼠标——它们有 `ABS_X` 所以长得像摇杆 |

第二个判据在源码注释里写得很细，四个条件必须**同时**成立（`:846-851`）：事件类型集合恰好是 `{ABS, KEY, SYN}` 或加上 `MSC`、或再加上 `REL` 三者之一；绝对轴恰好是 `{ABS_X, ABS_Y}`；按键恰好是 `{BTN_LEFT, BTN_RIGHT, BTN_MIDDLE}`；且总线不是 `BUS_AMIGA`。

★ **注意这里用的是 `bitmap_equal()` 而不是 `bitmap_subset()`**（`:854`、`:876`、`:885`）——多一位或少一位就不算绝对鼠标。这和 A5.1 的 `id_table` 匹配（子集判定）是**两套完全不同的比较语义**，同一个文件里两种都用了。最后那个 `BUS_AMIGA` 例外的理由也在注释里：Amiga 摇杆历史上就是用 left/middle/right 三个键上报的（`:889-891`）。

---

## §2 serio：一条自带 `bus_type` 的字节流总线

**挂点**：**在 A2 之前**。serio 本身完全不认识 `input_dev`——它管的是「一条能收发字节的低速端口」。挂在端口上的驱动（`atkbd`、`psmouse`……住在 `drivers/input/keyboard/`、`drivers/input/mouse/`）才是那个调用 `input_allocate_device()` 走 A2→A3→A4 的角色。所以 serio 是**生产者的上游**，不是 input 的一部分。

规模：`drivers/input/serio/` 有 29 个 `.c` 文件，14722 行。核心是 `serio.c`（1049 行）；最大的两个文件 `i8042-acpipnpio.h`（1823）和 `i8042.c`（1675）都属于 x86，见 §2.4。

### §2.1 三个角色

| 角色 | 结构 | 谁实现 | 关键回调 |
|---|---|---|---|
| 端口 | `struct serio`（`include/linux/serio.h:20-`） | 端口驱动（`ambakmi.c`、`i8042.c`……） | `write` `:34`、`open` `:36`、`close` `:37`、`start` `:38`、`stop` `:39` |
| 端口驱动 | `struct serio_driver`（`:67-`） | 协议驱动（`atkbd.c`、`psmouse` 等） | `interrupt` `:74`、`connect` `:75`、`reconnect` `:76`、`fast_reconnect` `:77`、`disconnect` `:78`、`cleanup` `:79` |
| 总线 | `struct bus_type serio_bus`（`drivers/input/serio/serio.c:1010-1021`） | serio 核心 | `.match = serio_bus_match`、`.probe`、`.remove`、`.shutdown`、`#ifdef CONFIG_PM .pm` |

字节流的两个方向是**不对称**的：

```
向下（主机 → 设备）：serio_write(serio, byte)        include/linux/serio.h:122，直接调 serio->write
向上（设备 → 主机）：serio_interrupt(serio, byte, flags)  端口驱动在自己的中断里调，
                     serio 核心转手交给 serio->drv->interrupt
```

★ 向上的这一跳**是在中断上下文里同步完成的**，serio 核心不缓冲。`serio_interrupt()` 拿的是 `serio->lock`（一个 `spinlock_t`），配套的 `serio_pause_rx()` / `serio_continue_rx()`（`include/linux/serio.h:154` / `:159`）就是给端口驱动在改自己状态时临时挡住上行流用的。

`serio_interrupt()` 的使用面：**33 个文件、48 处调用**（含 `serio.c` 里的定义与导出）。重跑：

```bash
grep -rlE --include=*.c '\bserio_interrupt\(' .                    # → 33
grep -rnE --include=*.c '\bserio_interrupt\(' . | grep -v EXPORT_SYMBOL | wc -l   # → 48
```

### §2.2 一切热插拔都过一条工作队列

serio 不在调用者的上下文里做绑定/解绑，而是把动作变成事件排到一条 `DECLARE_WORK`（`serio.c:235`）上：

| 事件类型 | 谁排的 | 处理函数 |
|---|---|---|
| `SERIO_REGISTER_PORT` | `__serio_register_port()`（`:695`） | `serio_add_port()`（`:523`） |
| `SERIO_RECONNECT_PORT` | `serio_resume()`（`:952`） | `serio_reconnect_port()`（`:583`） |
| `SERIO_RESCAN_PORT` | `serio_rescan()`（`:678`） | `serio_disconnect_port()` + `serio_find_driver()` |
| `SERIO_RECONNECT_SUBTREE` | `serio_reconnect()`（`:684`） | `serio_reconnect_subtree()`（`:599`） |
| `SERIO_ATTACH_DRIVER` | `__serio_register_driver()`（`:838`） | `serio_attach_driver()`（`:800`） |

枚举在 `serio.c:129-135`，事件锁是 `static DEFINE_SPINLOCK(serio_event_lock)`（`:144`），队列是 `static LIST_HEAD(serio_event_list)`（`:145`）。

★ **两个「reconnect」不是一回事，而且异步/同步也不一样**，这是这一节最容易看错的地方：

| 入口 | 事件 / 调用 | 范围 | 同步性 |
|---|---|---|---|
| `serio_reconnect()`（导出 API，`:682-686`） | 排 `SERIO_RECONNECT_SUBTREE` | **整棵子树**（端口 + 它的所有子端口） | 异步 |
| `serio_resume()`（PM 回调，`:933-956`） | 排 `SERIO_RECONNECT_PORT`（`:952`） | 只这一个端口 | 异步 |
| 往 `drvctl` 写 `"reconnect"`（`:392`） | **直接调** `serio_reconnect_subtree()` | 整棵子树 | **同步**，在 `write()` 的上下文里 |

所以「`serio_reconnect()` 就是 `drvctl` 那条路的程序版本」这句话是错的：范围一样，但一个排队一个当场做。`serio_resume()` 还有一层——它先试 `serio->drv->fast_reconnect`（调用在 `:940`，整个分支 `:939-944`），只有失败（且不是 `-ENOENT`）才退回排队，注释原文是 `/* Driver reconnect can take a while, so better let kseriod deal with it. */`（`:948-951`）。

★ 事件机制里有两个「去重」函数，名字很像但作用不同：

| 函数 | 位置 | 作用 |
|---|---|---|
| `serio_remove_duplicate_events()` | `:170-194` | 删掉队列里**同一个对象、同一种类型**的旧事件——正在处理的这一个已经代表了它们 |
| `serio_remove_pending_events()` | `:292-` | 对象要销毁了，把它的**所有**待处理事件删掉 |

`serio_get_event()`（`:147-`）里有一处容易被读成 bug 的写法：它**只取队首一个**并从链表摘下来，`serio_handle_event()`（`:196-`）是个 `while` 循环反复调它，而不是一次性把链表切走。这样做的原因写在代码里——处理一个事件的过程中可能会**再排进新事件**（比如 `serio_add_port()` 里再注册子端口）。

### §2.3 手动绑定：两级 `manual_bind`

serio 是少数几个提供「不要自动绑驱动」开关的总线之一，而且**端口和驱动各有一份标志**：

| 层 | 字段 | sysfs | 位置 |
|---|---|---|---|
| 端口 | `serio->manual_bind` | 每设备的 `bind_mode`（读写 `"auto"`/`"manual"`） | `serio_show_bind_mode()` `:410`、`serio_set_bind_mode()` `:416`、属性 `:461` |
| 驱动 | `serio_drv->manual_bind` | 每驱动的 `bind_mode`（`drv_groups`） | `bind_mode_show()` `:739`、`bind_mode_store()` `:745`、属性 `:761`、组挂载 `:1012` |

判据是 `serio_bus_match()` 里的一行——**两个标志任意一个置位就不自动匹配**：

```c
/* drivers/input/serio/serio.c:885 —— 原文 */
if (serio->manual_bind || serio_drv->manual_bind)
        return 0;
```

★ `__serio_register_driver()`（`:800-847`）在注册期间**临时把自己标成 `manual_bind = true`**（`:823`），扫完已有端口再按调用者原本的意思恢复（`:836-837`）。这不是笔误，是为了让「注册驱动」这个动作里的端口扫描由 `serio_attach_driver()` 独占，不被并行的 `serio_find_driver()` 抢走。而 `serio_unregister_driver()` 里那句 `drv->manual_bind = true;` 后面直接写了注释 `/* so serio_find_driver ignores it */`（`:855`）。

每端口的 sysfs 属性挂了**两个组**（`serio_device_attr_groups[]`，`:477-481`），但它们在文件系统里不是平级的：

| 组 | 定义 | `.name` | 落到哪 | 内容 |
|---|---|---|---|---|
| `serio_id_attr_group` | `:453-456` | **`"id"`** | 端口目录下的**子目录 `id/`** | `type`、`proto`、`id`、`extra`（`:440-443`，四个 `DEVICE_ATTR_RO`） |
| `serio_device_attr_group` | `:473-475` | 无 | 端口目录本身 | `modalias`、`description`、`drvctl`、`bind_mode`、`firmware_id`（`:458-462`） |

★ 注意 `id` 这个名字用了两次：**子目录叫 `id/`，它里面还有一个属性文件也叫 `id`**，所以完整路径是 `.../serioN/id/id`（读的是 `serio->id.id`）。看到这个不要以为是笔误。

`drvctl` 是只写的（`DEVICE_ATTR_WO`，`:459`），接受四种写入：`"none"`、`"reconnect"`、`"rescan"`，或者一个驱动名（`drvctl_store()` 的分支在 `:389-403`，最后一支落到 `driver_find(buf, &serio_bus)`）。写别的返回 `-EINVAL`（`:402`）。

### §2.4 arm64 上的 serio 到底能用什么

★ **`SERIO_I8042` 在 arm64 上根本编不出来。** `drivers/input/serio/Kconfig:29-32` 写着 `depends on ARCH_MIGHT_HAVE_PC_SERIO`，而这个符号在 arm64 上没有任何地方 `select`。重跑：

```bash
grep -rn 'select ARCH_MIGHT_HAVE_PC_SERIO' arch/ | sed 's/:.*select.*//' | sort -u
```

选它的是 arm（footbridge）、alpha、ia64、loongarch、sparc、x86、powerpc、mips——**没有 arm64**。所以 `i8042.c`（1675 行）和 `i8042-acpipnpio.h`（1823 行）这 3498 行，也就是整个 serio 目录**近四分之一的代码**，在 arm64 上是死的。

arm64 上能落地的端口驱动：

| Kconfig | 行 | 依赖 | 说明 |
|---|---|---|---|
| `SERIO_AMBAKMI` | `:100-102` | `ARM_AMBA` | ARM PrimeCell PL050，**本树 arm64 DTS 真的在用**（见下） |
| `SERIO_SERPORT` | `:43-46` | TTY | 把一个 tty 变成 serio 端口，`inputattach` 走这条 |
| `SERIO_ALTERA_PS2` | `:213-215` | `HAS_IOMEM`（`:215`） | FPGA soft IP |
| `SERIO_ARC_PS2` | `:246-248` | `HAS_IOMEM`（`:248`） | Synopsys ARC |
| `SERIO_APBPS2` | `:256-258` | `OF && HAS_IOMEM`（`:258`） | Gaisler GRLIB |
| `SERIO_SUN4I_PS2` | `:286-288` | `ARCH_SUNXI \|\| COMPILE_TEST`（`:288`） | 全志 |
| `SERIO_GPIO_PS2` | `:296-298` | `GPIOLIB`（`:298`） | 纯 GPIO 打软 PS/2 |
| `USERIO` | `:307-308` | **一条 `depends on` 都没有** | ★ 端口来自**用户态**：见下 |

★ **`USERIO` 是这张表里唯一真的没有任何 `depends on` 的一项**（`drivers/input/serio/Kconfig:307` 之后紧接着就是 `tristate` 和 `help`，中间没有依赖行），所以它在 arm64 上无条件可选。它提供的是**反方向**的东西：`drivers/input/serio/userio.c`（285 行）注册一个 misc 字符设备 `/dev/userio`（`struct miscdevice userio_misc` `:273-277`，`.minor = USERIO_MINOR` `:275` → `include/linux/miscdevice.h:71` 的 240，`.name = USERIO_NAME` `:276` → `:30` 的 `"userio"`，注册靠 `module_driver(userio_misc, misc_register, misc_deregister)` `:278`），让用户态进程去**扮演一个 serio 端口**——内核里现成的 `atkbd` / `psmouse` 协议驱动会像对待真硬件一样绑上来。ABI 在 `include/uapi/linux/userio.h`（45 行）。官方原文见 `Documentation/input/userio.rst`（85 行）。

★ 和 `uinput` 的分工要分清：`uinput` 伪造的是**已经成形的 input 设备**（直接喂 `input_event`，见 §4），`userio` 伪造的是**协议之下的字节流端口**（喂 PS/2 字节，让内核协议驱动去解析）。前者跳过了整个 serio + 协议层，后者恰好把这两层跑一遍——所以 `userio` 是测试 `psmouse`/`atkbd` 协议解析的手段，`uinput` 不是。本文档集只登记 `userio` 的存在与边界，没有详读它的 285 行（登记在 `99-evidence.md` §2.2）。

配套的三个非端口选项：`SERIO_LIBPS2`（`:178-180`，PS/2 命令封装）、`SERIO_RAW`（`:188`，`/dev/serio_rawN` 字符设备）、`SERIO_PS2MULT`（`:237`，TQC 多路复用协议）。

★ **本树的 arm64 DTS 里确实有 serio 节点**，两个平台各两个 PL050：

```
arch/arm64/boot/dts/arm/juno-motherboard.dtsi:252     compatible = "arm,pl050", "arm,primecell";
arch/arm64/boot/dts/arm/juno-motherboard.dtsi:260     （第二个，鼠标口）
arch/arm64/boot/dts/arm/rtsm_ve-motherboard.dtsi:154  同上
arch/arm64/boot/dts/arm/rtsm_ve-motherboard.dtsi:162
```

重跑：`grep -rn 'arm,pl050' arch/arm64/boot/dts/`。所以「serio 在 arm64 上是遗产」这个说法**只对 i8042 成立**——AMBA KMI 是 Juno 开发板和 RTSM 模型的真实配置。注意 `ambakmi.c` 匹配的是 `struct amba_id`（`:185`），走 AMBA 总线的 PrimeCell ID 而不是 DT `compatible` 字符串本身。

### §2.5 使用面统计

| 口径 | 数字 | 命令 |
|---|---|---|
| `struct serio_driver` 静态实例 | 39 个（39 个文件） | `grep -rnE --include=*.c 'struct serio_driver [a-z_0-9]* = \{' .` |
| 注册端口的文件 | 31 个 | `grep -rlE --include=*.c '\b__serio_register_port\(\|\bserio_register_port\(' .` |
| 调 `serio_interrupt()` 的文件 | 33 个 | 见 §2.1 |

★ 39 个 `serio_driver` 里数量最大的一族是 `drivers/input/mouse/` 下的各种触摸板协议和 `drivers/input/keyboard/` 下的 `atkbd`/`sunkbd`/`newtonkbd` 等——**它们才是造 `input_dev` 的那一层**，而 04 篇不展开它们（代表驱动的选法记在 `99-evidence.md` §2.2）。

初始化次序上有一个值得记的点：**`serio_init()` 是 `subsys_initcall`**（`serio.c:1048`），和 `input_init()` 同级。二者谁先跑取决于链接顺序，但这不构成竞态——serio 端口驱动要到 `module_init`/`device_initcall` 级别才注册，那时两条总线都在了。

---

## §3 力反馈：ff-core 与 ff-memless

**挂点**：**A3 之后、A4 之前**。驱动先声明 `EV_FF` 相关的 `ffbit`，然后在 `input_register_device()` **之前**调 `input_ff_create()` 或 `input_ff_create_memless()`——因为这两个函数会往 `dev->evbit` 里补位，注册之后再补就太晚了（能力位图在注册时已经被 handler 拿去匹配了）。

这是本篇唯一两节之间有依赖的地方：**`ff-memless` 是 `ff-core` 的一个使用者**，它自己不是独立总线。两者的分工：

| | ff-core | ff-memless |
|---|---|---|
| 文件 | `drivers/input/ff-core.c`（377 行） | `drivers/input/ff-memless.c`（553 行） |
| 编译 | 编进 `input-core.o`，无独立 Kconfig | `INPUT_FF_MEMLESS`（`drivers/input/Kconfig:41`） |
| 职责 | 效果表的管理：分配 id、查所有权、转发到驱动 | **把「随时间变化的波形」算出来**，定时喂给硬件 |
| 导出符号 | 6 个，全是 `EXPORT_SYMBOL_GPL` | 1 个：`input_ff_create_memless()`（`:553`） |
| 谁调 | 有独立效果内存的设备（能自己播波形的） | 只会「用某个力度震动」的设备 |

### §3.1 ff-core 管的是一张效果表

`struct ff_device`（`include/linux/input.h:546-566`）末尾是**两段**长度为 `max_effects` 的数组：

```
ff->effects[]        kcalloc 单独分配（ff-core.c:328-329）—— 效果内容
ff->effect_owners[]  柔性数组，和 ff_device 一起分配（:320-326）—— 每个 id 属于哪个 file
```

★ **所有权是按 `struct file *` 记的，不是按进程。** `check_effect_access()`（`:23-37`）的判据是 `ff->effect_owners[effect_id] == file`；`input_ff_flush()`（`:235-250`）在 `release()` 时把这个 file 拥有的效果全擦掉。所以 **fd 一关，它上传的震动效果就自动停并释放**——用户态不需要（也不能靠别人）手动清理。

`input_ff_create()`（`:304-352`）里有三处值得单独记：

| 行 | 做什么 | 为什么要知道 |
|---|---|---|
| `:312-315` | `max_effects > FF_MAX_EFFECTS` → `-EINVAL`，日志 `"cannot allocate more than FF_MAX_EFFECTS effects\n"` | 上限是 **96**，见下面的 ★ |
| `:338-340` | `dev->flush = input_ff_flush; dev->event = input_ff_event;` | **这是 input 核心里唯一给 `dev->event` 赋值的地方**（`:340`）。驱动自己也想要 `dev->event` 的话必须在 `input_ff_create()` 之后再赋，否则被覆盖 |
| `:347-349` | `if (test_bit(FF_PERIODIC, ff->ffbit)) __set_bit(FF_RUMBLE, dev->ffbit);` | 只要设备能播周期波形，核心就**免费替它宣称支持 RUMBLE** |

★ **上限 96 这个数字是为了避开一次编码冲突**，`include/uapi/linux/input.h:507-513` 把理由写在注释里：`ff->playback()` 的 `effect_id` 参数和 `FF_GAIN`（`0x60` = 96）、`FF_AUTOCENTER`（`0x61`）走的是**同一个数值空间**——`input_ff_event()`（`:260-290`）的 `switch (code)` 先拦 `FF_GAIN`/`FF_AUTOCENTER`，剩下的才当 `effect_id` 送进 `ff->playback()`。所以 `#define FF_MAX_EFFECTS FF_GAIN`，最大可用 id 是 95。**这不是"96 够用了"，是 97 会撞车。**

上面那个「免费宣称 RUMBLE」在上传时是由 `compat_effect()`（`:50-83`）兑现的：设备不真支持 `FF_RUMBLE` 时，把效果**原地改写**成一个 `FF_PERIODIC` 正弦波：

```c
/* drivers/input/ff-core.c:63-64 —— 原文 */
magnitude = effect->u.rumble.strong_magnitude / 3 +
            effect->u.rumble.weak_magnitude / 6;
```

周期写死 50 ms、波形 `FF_SINE`、offset/phase/envelope 全部清零（`:68-76`）。★ **注意这两个系数是 1/3 和 1/6，不是注释说的 "2/3 强 + 1/3 弱"**——注释（`:60-62`）写的是相对权重（强:弱 = 2:1），代码里两个数都又除了 2，因为正弦波的幅值对应峰值而 rumble 的 magnitude 对应平均能量。这是文档式注释和实现容易读出偏差的一处，但结论上二者不矛盾。

`input_ff_upload()`（`:91-164`）的校验有四道，顺序很关键：

1. `!test_bit(EV_FF, dev->evbit)` → **`-ENOSYS`**（不是 `-EINVAL`）
2. type 越界或 `!test_bit(effect->type, dev->ffbit)` → `-EINVAL`，日志 `"invalid or not supported effect type in upload\n"`
3. `FF_PERIODIC` 的 waveform 越界或不支持 → `-EINVAL`，日志 `"invalid or not supported wave form in upload\n"`
4. `!test_bit(effect->type, ff->ffbit)` → 走 `compat_effect()` 转换

★ 第 2 步查的是 `dev->ffbit`，第 4 步查的是 `ff->ffbit`——**两张位图不是同一张**。`ff->ffbit` 是注册时从 `dev->ffbit` 拷过来的"真实能力"（`:344-345`），而 `dev->ffbit` 后来被加上了模拟出来的 `FF_RUMBLE`。所以「type 在 `dev->ffbit` 里但不在 `ff->ffbit` 里」正是需要转换的那种情况，这两步的顺序不能换。

`effect->id == -1` 表示"给我分一个新 id"（`:123-135`），线性扫 `effect_owners[]` 找空位，满了返回 `-ENOSPC`。写回效果表的那三行拿的是 `dev->event_lock`（`:155-158`）而不是 `ff->mutex`——因为 `input_ff_event()` 是在事件路径上被调的，那条路径已经持有 `event_lock`。

### §3.2 ff-memless 是一个软件波形发生器

它的存在理由：绝大多数游戏手柄的硬件接口只有"以强度 X 震动"，不认识 envelope、不认识周期。ff-memless 用一个 `timer_list` 把 8 种效果类型在软件里算成一串强度值。

| 机制 | 位置 | 说明 |
|---|---|---|
| 定时器 | `ml_effect_timer()`（`:400-414`）、`ml_schedule_timer()`（`:109-147`） | 下一次唤醒时刻由 `calculate_next_time()`（`:74-107`）算 |
| 包络 | `apply_envelope()`（`:149-193`）、`get_envelope()`（`:55-72`） | attack/fade 段的线性插值 |
| 效果叠加 | `ml_combine_effects()`（`:240-321`）、`ml_get_combo_effect()`（`:323-385`） | 多个同时播放的效果合成一组 `FF_RUMBLE`+`FF_CONSTANT` 值 |
| 定点数学 | `fixp_new16()`（`:226-229`）、`fixp_mult()`（`:231-238`） | 内核里没有浮点，方向合成用 16 位定点 |
| 方向 | `ml_calculate_direction()`（`:213-224`） | 按力度加权平均两个效果的方向角 |

★ **ff-memless 自己实现的是 `ff_device` 的回调，而不是绕过 ff-core。** `input_ff_create_memless()`（`:507-552`）先调 `input_ff_create()`，再把 `ff->upload = ml_ff_upload`（`:462`）、`ff->playback = ml_ff_playback`（`:432`）、`ff->set_gain = ml_ff_set_gain`（`:416`）、`ff->destroy = ml_ff_destroy`（`:485`）填上。**驱动传进去的 `play_effect` 回调只被要求做一件事：把算好的强度写进硬件。**

`get_compatible_type()`（`:195-211`）是 ff-memless 自己的一层转换：设备只支持 `FF_RUMBLE` 却收到 `FF_PERIODIC`（或反之）时在这里对齐。★ 于是**一个效果可能被转换两次**：ff-core 的 `compat_effect()` 一次、ff-memless 的 `get_compatible_type()` 一次。查"用户态发的效果为什么变了形"时两处都要看。

### §3.3 arm64 上的力反馈

ff-core 无条件编进核心，所以**它一定在**。`INPUT_FF_MEMLESS` 是 tristate，主要由 USB/蓝牙手柄驱动 `select`。使用面：

| 口径 | 数字 | 命令 |
|---|---|---|
| 调 `input_ff_create()` 的文件 | 10 个（11 处，含定义） | `grep -rlE --include=*.c '\binput_ff_create\(' .` |
| 调 `input_ff_create_memless()` 的文件 | 39 个（43 处，含定义） | `grep -rlE --include=*.c '\binput_ff_create_memless\(' .` |

★ **memless 的使用者比 core 的直接使用者多近四倍**——这说明"设备自己有效果内存"是少数情况，绝大多数手柄走软件波形。这两个数字之间没有重叠关系上的矛盾：`input_ff_create_memless()` 内部会调 `input_ff_create()`，但那一处调用**记在 `ff-memless.c` 名下**，所以 10 个文件里包含 `ff-memless.c` 自己。

---

## §4 input-compat：32 位用户态的边界

**挂点**：**A8 之后**，在字符设备和用户态之间那一层。核心的事件路径完全不知道它存在——只有 `read()`/`write()`/`ioctl()` 在拷贝的那一刻需要它。

两个文件，都很小：`drivers/input/input-compat.c`（133 行）、`drivers/input/input-compat.h`（78 行）。前者无条件编进 `input-core.o`。

### §4.1 需要转换的只有两样东西

| 结构 | 32/64 位差在哪 | compat 版本 |
|---|---|---|
| `struct input_event` | 头两个字段是 `__kernel_ulong_t`（`include/uapi/linux/input.h:34-40`），32 位下是 4 字节、64 位下 8 字节 | `struct input_event_compat`（`input-compat.h:17-23`），用 `compat_ulong_t` |
| `struct ff_effect` | `u.periodic.custom_data` 是一个用户态指针（`include/uapi/linux/input.h:415`） | `struct ff_effect_compat`（`:38-52`），里面 `ff_periodic_effect_compat`（`:25-36`）把它换成 `compat_uptr_t` |

**其余所有 uapi 结构在两种 ABI 下布局相同**，所以不需要 compat 版本——`input_absinfo`、`input_id`、`input_keymap_entry`、`input_mask` 全是定宽类型。

### §4.2 整个文件是两份互斥实现

```
input-compat.c:12   #ifdef CONFIG_COMPAT      → 真正做转换的三个函数（:14 / :38 / :62）
input-compat.c:97   #else                     → 三个直通版本（:99 / :108 / :117）
input-compat.c:129  #endif
input-compat.c:131-133                        → 三个 EXPORT_SYMBOL_GPL，在 #endif 之外
```

★ **三个 `EXPORT_SYMBOL_GPL` 故意写在 `#endif` 外面**（`:131-133`），所以**符号名和签名在两种配置下完全一致**。调用方（evdev、uinput）里没有一个 `#ifdef CONFIG_COMPAT`——这是"用两份实现换掉调用点的条件编译"的标准做法。`input_event_size()` 同样是这个套路，只不过它是 `static inline`，两份实现在头文件里（`input-compat.h:54-57` 与 `:62-65`）。

### §4.3 判据是 `in_compat_syscall() && !COMPAT_USE_64BIT_TIME`

★ **这个第二个条件在 arm64 上恒为真，所以 arm64 上的判据实际就是 `in_compat_syscall()`。** 依据：`COMPAT_USE_64BIT_TIME` 在 `include/linux/compat.h:37-39` 里被 `#ifndef` 兜底成 `0`，而全树**只有 x86 覆盖它**：

```bash
grep -rn 'define COMPAT_USE_64BIT_TIME' --include=*.h .
# → include/linux/compat.h:38  （兜底 0）
# → arch/x86/include/asm/compat.h:79  （唯一的覆盖）
```

x86 那个覆盖是给 x32 ABI 用的（指针 32 位但时间 64 位）。arm64 的 compat 只有 aarch32 一种，时间字段就是 32 位，所以恒走转换分支。**看到这个双重条件不要以为 arm64 上有第三种情况。**

这三个函数用了两条不同的判据，不是笔误：

| 函数 | 判据 | 为什么 |
|---|---|---|
| `input_event_from_user()`（`:14-35`） | `in_compat_syscall() && !COMPAT_USE_64BIT_TIME` | 差异只在时间字段上 |
| `input_event_to_user()`（`:38-59`） | 同上 | 同上 |
| `input_ff_effect_from_user()`（`:62-94`） | **只有 `in_compat_syscall()`** | 差异在**指针宽度**上，和时间无关 |

### §4.4 `ff_effect` 那次原地转换

`input_ff_effect_from_user()` 里有一个值得看清的技巧（`:69-85`）：它**把输出缓冲区强转成 compat 类型，先整块拷进来，再只修那个指针**。

```c
/* drivers/input/input-compat.c:71-79 —— 原文，含注释 */
/*
 * It so happens that the pointer which needs to be changed
 * is the last field in the structure, so we can retrieve the
 * whole thing and replace just the pointer.
 */
compat_effect = (struct ff_effect_compat *)effect;

if (copy_from_user(compat_effect, buffer,
                   sizeof(struct ff_effect_compat)))
        return -EFAULT;
```

★ 这段代码成立依赖三个**未被断言**的前提：

1. `sizeof(struct ff_effect_compat) <= sizeof(struct ff_effect)`——否则 `copy_from_user` 越界写。成立的原因是 compat 版把 8 字节指针换成 4 字节 `compat_uptr_t`（`include/uapi/linux/input.h:415` 对 `input-compat.h:35`），其余字段一字节不差
2. `custom_data` 是整个 union 里**偏移最大**的字段（`include/uapi/linux/input.h:405-416` 的 `struct ff_periodic_effect` 末尾），所以两个布局在它之前完全重合
3. 之后的 `effect->u.periodic.custom_data = compat_ptr(...)`（`:82-84`）写的是 **64 位布局的位置**，而读的是 32 位布局的位置——**读写不同偏移的同一块内存**

没有 `BUILD_BUG_ON` 守着这三条。改动 `struct ff_effect` 或 `struct ff_periodic_effect` 的字段顺序会**静默**破坏它。这一条记进 `99-evidence.md` 的源码设计债。

另外注意 size 校验的两支都是硬相等：compat 路径要求 `size == sizeof(struct ff_effect_compat)`（`:68`），原生路径要求 `size == sizeof(struct ff_effect)`（`:87`），否则 `-EINVAL`。这个 `size` 来自 `EVIOCSFF` 的 `_IOC_SIZE(cmd)`（`drivers/input/evdev.c:1180` 传进来），所以**用户态用错了 ioctl 号的宽度会得到 `-EINVAL` 而不是被静默截断**。

### §4.5 使用面：三个导出符号，两个消费者

| 符号 | 导出 | 调用点 |
|---|---|---|
| `input_event_size()` | `static inline`，不导出 | `evdev.c:510`、`:522`、`:528`、`:567`、`:585`、`:591`；`uinput.c:592`、`:595`、`:606`、`:659`、`:665`、`:677` |
| `input_event_from_user()` | `EXPORT_SYMBOL_GPL`（`:131`） | `evdev.c:524`、`uinput.c:602` |
| `input_event_to_user()` | `EXPORT_SYMBOL_GPL`（`:132`） | `evdev.c:588`、`uinput.c:662` |
| `input_ff_effect_from_user()` | `EXPORT_SYMBOL_GPL`（`:133`） | `evdev.c:1180` |

重跑：

```bash
grep -rn --include=*.c 'input_event_size()\|input_event_from_user(\|input_event_to_user(\|input_ff_effect_from_user(' . \
  | grep -v 'input-compat'
```

★ **全树只有两个消费者文件**（`drivers/input/evdev.c` 和 `drivers/input/misc/uinput.c`），两个都在 `drivers/input/` 下。所以这三个符号虽然 `EXPORT_SYMBOL_GPL` 了，实际上**没有任何树外/树内模块在用它们**——导出是历史遗留（`input-compat.h` 是私有头文件，不在 `include/` 下，树外模块拿不到声明）。这正是规则「符号存在 ≠ 读者能用」的一个反面例子：**导出了，但头文件不公开，等于用不了**。

mousedev 和 joydev 都不在这张表里——mousedev 传的是裸字节流（PS/2 包没有时间字段），joydev 的 `struct js_event` 用的是 `__u32 time`（定宽），两者都不需要 compat 转换。

---

## §5 rmi4：一条总线上跑两种设备

**挂点**：**它自己走完 A2→A3→A4**。`rmi_driver_probe()` 在 `drivers/input/rmi4/rmi_driver.c:1190` 调 `devm_input_allocate_device()`、在 `:1217` 调 `input_register_device()`。这一点和 serio / gameport 不同（那两个从不碰 `input_dev`），也和 §0 那张表里 §2/§12 两行的挂点写法不同——**不要因为"自带 `bus_type`"就以为它在 input 之外**。

规模：`drivers/input/rmi4/` 有 16 个 `.c` + 3 个 `.h`，10511 行。核心两个文件：`rmi_bus.c`（478 行，总线）和 `rmi_driver.c`（1283 行，那个真正的 physical driver）。

RMI4 是 Synaptics 的寄存器映射协议。它的价值在于**一颗触摸屏芯片的能力是运行时从设备里读出来的**，不是编译期写死的——所以驱动结构必须能"按芯片报的功能表动态组装"。

### §5.1 两种设备住在同一条总线上

`rmi_bus_type`（`rmi_bus.c:347-350`）**只有 `.match` 和 `.name` 两个字段**，没有 `.probe`/`.remove`/`.uevent`/`.pm`——这和 §2 的 `serio_bus`（七个字段）形成鲜明对比。原因是它的 probe 挂在驱动上而不是总线上：`rmi_physical_driver.driver.probe = rmi_driver_probe`（`rmi_driver.c:1252`）。

这条总线上有**两类**设备，`.match` 先做的事就是把它们分开：

```c
/* drivers/input/rmi4/rmi_bus.c:336-345 —— 原文 */
static int rmi_bus_match(struct device *dev, struct device_driver *drv)
{
        bool physical = rmi_is_physical_device(dev);

        /* First see if types are not compatible */
        if (physical != rmi_is_physical_driver(drv))
                return 0;

        return physical || rmi_function_match(dev, drv);
}
```

| 类 | 设备结构 | 驱动结构 | 注册函数 | 匹配判据 |
|---|---|---|---|---|
| **transport / physical** | `struct rmi_device` | `struct rmi_driver` | `rmi_register_transport_device()`（`:74-111`） | 只看类型对不对——**全树只有一个 physical driver**，见下 |
| **function** | `struct rmi_function` | `struct rmi_function_handler` | `rmi_register_function()`（`:239-`） | `fn->fd.function_number == handler->func`（`rmi_function_match()`，`:147-153`） |

类型判定靠 `dev->type`：`rmi_is_physical_device()`（`:62-65`）就是 `dev->type == &rmi_device_type`（`rmi_device_type` 在 `:57`，`rmi_function_type` 在 `:137`）。

★ **`rmi_bus_match()` 对 physical 设备返回的是「类型对了就算匹配」**，没有任何 ID 比较。这行得通是因为**全树只有一个 `struct rmi_driver`**：`rmi_physical_driver`（`rmi_driver.c:1247-1259`）。重跑：

```bash
grep -rnE --include=*.c 'struct rmi_driver [a-z_0-9]+ = \{' .
```

→ 只有 `drivers/input/rmi4/rmi_driver.c:1247` 一处。所以 transport 侧的"匹配"实际上是"接上唯一那个"。

### §5.2 三层：transport → physical driver → function handler

```
rmi_i2c.c / rmi_spi.c / rmi_smbus.c        ← 只管收发字节（i2c_driver / spi_driver）
        │  rmi_register_transport_device()
        ▼
rmi_driver.c   rmi_physical_driver          ← 读 PDT、建 function 设备、造 input_dev、管中断
        │  rmi_register_function()
        ▼
rmi_f01 / f03 / f11 / f12 / f30 / f34 / f3a / f54 / f55   ← 每个 RMI 功能号一个驱动
```

**中间那层做的事**（`rmi_driver_probe()`，`rmi_driver.c:1102-`）按顺序是：

| 步骤 | 函数 | 说明 |
|---|---|---|
| 探 bootloader | `rmi_check_bootloader_mode()`（`:744-`） | 固件挂了的芯片只能刷机，不能当触摸屏用 |
| 复位 | `rmi_initial_reset()`（`:791-`） | 通过 `rmi_scan_pdt()` 回调形式做 |
| 扫功能表 | `rmi_scan_pdt()`（`:542-`）+ `rmi_scan_pdt_page()`（`:499-`）+ `rmi_read_pdt_entry()`（`:459-`） | PDT = Page Descriptor Table，**芯片自报家门的那张表** |
| 数中断位 | `rmi_count_irqs()`（`:776-`）、`rmi_probe_interrupts()`（`:1011-`） | 中断位数量也是运行时算的 |
| 造 input_dev | `:1180-1204` | ★ 见下面的复用分支 |
| 建 function 设备 | `rmi_init_functions()`（`:1065-`）→ `rmi_create_function()`（`:828-`） | 每个 PDT 条目一个 `rmi_function` 设备 |
| 注册 input_dev | `:1217` | **在 function 驱动都 probe 完之后**——因为能力位是它们填的 |
| 装中断 | `rmi_irq_init()`（`:217-`） | 线程化中断 `rmi_irq_fn()`（`:188-215`） |

★ **`input_dev` 的注册被故意放在 `rmi_init_functions()` 之后**（`:1206` 对 `:1217`），这正是 A3 必须在 A4 之前的那条规则在真实驱动里的体现：F11/F12 这些 function 驱动在自己的 probe 里调 `input_set_abs_params()` / `input_mt_init_slots()` 声明能力，注册一旦发生就晚了。

★ **有一条「复用上游 input_dev」的分支**（`:1180-1188`），注释写得很清楚：

```c
/* drivers/input/rmi4/rmi_driver.c:1181-1187 —— 原文注释 */
/*
 * The transport driver already has an input device.
 * In some cases it is preferable to reuse the transport
 * devices input device instead of creating a new one here.
 * One example is some HID touchpads report "pass-through"
 * button events are not reported by rmi registers.
 */
```

走这条分支时 rmi4 **既不 allocate 也不 register**（`:1216` 那个 `if (!rmi_dev->xport->input)` 守着），只往别人的 `input_dev` 里报事件。**所以「有 rmi4 就有一个 `Synaptics …` 的 input 节点」这句话不成立**——HID-RMI 那条路上节点是 HID 建的。

设备名字也是两段式的：先 `rmi_driver_set_input_params()`（`:342-349`）写死 `SYNAPTICS_INPUT_DEVICE_NAME` / `SYNAPTICS_VENDOR_ID` / `bustype = BUS_RMI`，等 F01 读出产品 ID 后再由 `rmi_driver_set_input_name()`（`:351-364`）覆盖成 `"Synaptics %s"`。★ 覆盖发生在 `:1215`，**在 `input_register_device()` 之前**，所以用户态看不到那个临时名字。

### §5.3 function handler 的注册是编译期数组 + 运行期匹配

`fn_handlers[]`（`rmi_bus.c:352-378`）是一个**被 `#ifdef` 打得七零八落**的数组：`&rmi_f01_handler` 无条件在，其余八个各由自己的 `CONFIG_RMI4_F*` 守着。`rmi_register_function_handlers()`（`:392-411`）循环注册，失败时 `pr_err("%s: error registering the RMI F%02x handler: %d\n", ...)` 并回滚。

★ **`rmi_f01_handler` 是唯一没有 Kconfig 开关的 function handler**——F01 是 RMI4 的设备控制功能块，没有它就读不出产品 ID、管不了电源状态。对应地 `drivers/input/rmi4/Kconfig` 里也**找不到 `RMI4_F01`**。重跑：`grep -n 'RMI4_F' drivers/input/rmi4/Kconfig` 列出的是 F03 / F03_SERIO / F11 / F12 / F30 / F34 / F3A / F54 / F55 共九项，没有 F01。

回滚函数有一处容易看错的地方：

```c
/* drivers/input/rmi4/rmi_bus.c:380-386 —— 原文 */
static void __rmi_unregister_function_handlers(int start_idx)
{
        int i;

        for (i = start_idx; i >= 0; i--)
                rmi_unregister_function_handler(fn_handlers[i]);
}
```

正常卸载走 `rmi_unregister_function_handlers()`（`:388-391`）传 `ARRAY_SIZE(fn_handlers) - 1`；注册失败时传的是 `i - 1`（`:409`）——**第 `i` 个是失败的那个，它没注册上，所以从 `i-1` 开始回滚**。传 `i` 会去注销一个从未注册的 handler。

### §5.4 arm64 相关性与 Kconfig

| 选项 | 行 | 依赖 / select | arm64 |
|---|---|---|---|
| `RMI4_CORE` | `:5` | `select IRQ_DOMAIN` | 相关 |
| `RMI4_I2C` | `:16-18` | `depends on I2C` | **主力路径** |
| `RMI4_SPI` | `:25-27` | `depends on SPI` | 相关 |
| `RMI4_SMB` | `:34-36` | `depends on I2C` | 相关但少见（PS/2 passthrough 场景） |
| `RMI4_F03` | `:46-48` | —— | 相关 |
| `RMI4_F03_SERIO` | `:55-60` | **`select SERIO`** | ★ 这里和 §2 交叉：F03 是 RMI 上的 PS/2 透传通道，它把自己注册成一个 serio 端口 |
| `RMI4_2D_SENSOR` | `:62` | —— | 被 F11/F12 select |
| `RMI4_F11` / `F12` | `:65-67` / `:75-77` | 都 `select RMI4_2D_SENSOR` | **触摸屏主力** |
| `RMI4_F30` / `F3A` | `:85` / `:103` | —— | 按键 / LED |
| `RMI4_F34` | `:93-95` | `select FW_LOADER` | 固件升级 |
| `RMI4_F54` / `F55` | `:111-115` / `:122` | F54 依赖 `VIDEO_V4L2` 一族 | 诊断用 |

★ **`RMI4_CORE` 走的是 `module_init(rmi_bus_init)`（`rmi_bus.c:460`），不是 `subsys_initcall`**——和 serio（`subsys_initcall`，`serio.c:1048`）、gameport（`subsys_initcall`，`gameport.c:854`）都不同。这不构成问题，因为 rmi4 的 transport 驱动（`rmi_i2c.c` 等）本身也是 `module_init` 级别，而且它们要等 I2C/SPI 总线先起来。**三条总线三个初始化级别，这是本篇少数几个"看起来该一致但确实不一致"的地方。**

★ **一对对偶函数的导出宏不一样**：`rmi_register_transport_device()` 是 `EXPORT_SYMBOL_GPL`（`:111`），而 `rmi_unregister_transport_device()` 是**不带 `_GPL` 的 `EXPORT_SYMBOL`**（`:125`）。同文件另外四个导出全是 `_GPL`（`:40` `rmi_dbg`、`:319` `__rmi_register_function_handler`、`:332` `rmi_unregister_function_handler`、`:430` `rmi_of_property_read_u32`）。重跑 `grep -n EXPORT_SYMBOL drivers/input/rmi4/rmi_bus.c` 得六行、五个 `_GPL` 一个不是。**注册要 GPL、注销不要**，这个组合在实践上没有意义（非 GPL 模块拿不到 register 就用不上 unregister），基本可以判定是漏写；记进 `99-evidence.md` 的设计债。

---

## §6 uinput：把 input_dev 交给用户态去造

**挂点**：**它自己走完 A2→A3→A4**，和 §5 的 rmi4 一样，但走法完全不同——rmi4 是内核驱动照常走，uinput 是**把每一步都拆成一个 ioctl 交给用户态按顺序调**。

文件：`drivers/input/misc/uinput.c`，1117 行；uapi：`include/uapi/linux/uinput.h`，232 行。Kconfig `INPUT_UINPUT`（`drivers/input/misc/Kconfig:513`），字符设备号 10:223（`UINPUT_MINOR`），`module_misc_device(uinput_misc)`（`:1110`）。

uinput 不是 handler，是 `struct miscdevice`（`:1105-1109`）。它和 evdev 恰好互为镜像：evdev 把内核里的 `input_dev` 暴露给用户态**读**，uinput 让用户态**建**一个 `input_dev` 出来让别人读。

### §6.1 三态状态机

`enum uinput_state { UIST_NEW_DEVICE, UIST_SETUP_COMPLETE, UIST_CREATED };`（`:37`）——一行三个值，但它是理解整个文件的钥匙。

| 状态 | 怎么进来 | 这个状态下能做什么 | 不能做什么 |
|---|---|---|---|
| `UIST_NEW_DEVICE` | `open()`（`:389`）或 `UI_DEV_DESTROY`（`:291`） | `UI_SET_*BIT`、`UI_DEV_SETUP`、`UI_ABS_SETUP`、legacy `write()` | `UI_DEV_CREATE`（`:313-316` 报 `-EINVAL` + `printk(KERN_DEBUG "%s: write device info first\n", …)`）、注入事件 |
| `UIST_SETUP_COMPLETE` | `UI_DEV_SETUP` 成功（`:484`）或 legacy `write()` 成功（`:578`） | `UI_DEV_CREATE` | —— |
| `UIST_CREATED` | `UI_DEV_CREATE` 成功（`:368`，紧跟在 `input_register_device()` 后面） | 注入事件（`write()`）、读 FF 请求（`read()`）、`UI_GET_SYSNAME` | 再改能力位——`uinput_set_bit` 宏在 `:821` 拦住 |

★ **`UIST_NEW_DEVICE` 是初始态也是回退态。** `uinput_destroy_device()`（`:285-306`）先把状态推回 `UIST_NEW_DEVICE`（`:291`），然后**按旧状态决定走 unregister 还是 free**（`:296-301`）：`UIST_CREATED` 走 `uinput_flush_requests()` + `input_unregister_device()`，其余两态走 `input_free_device()`。这正是 `03-internals.md` A11 讲的那条规则——**注册过的必须 unregister，没注册过的必须 free，两者不能互换**——在 uinput 里由这个状态字段来记账。

★ **`UI_DEV_DESTROY` 之后 fd 还活着**，状态回到 `UIST_NEW_DEVICE`，可以在同一个 fd 上再造一个设备。所以「一个 uinput fd ↔ 一个 input 节点」不成立，是「一个 uinput fd ↔ 同一时刻最多一个 input 节点」。

### §6.2 `struct input_dev` 是在第一次 ioctl 时才分配的

```c
/* drivers/input/misc/uinput.c:870-876 —— 原文 */
	if (!udev->dev) {
		udev->dev = input_allocate_device();
		if (!udev->dev) {
			retval = -ENOMEM;
			goto out;
		}
	}
```

这段在 `uinput_ioctl_handler()`（`:854-1052`）的开头、`switch (cmd)` 之前。★ **任何一个 ioctl 都会触发分配**，包括纯读的 `UI_GET_VERSION`。所以 `open()`（`:377-395`）里只 `kzalloc` 了 `struct uinput_device`，`input_dev` 要等第一次 ioctl；而如果用户态走的是 legacy 的 `write()` 路径、一个 ioctl 都不发，分配则发生在 `uinput_setup_device_legacy()` 里。**A2（分配）在 uinput 里没有单一的触发点**，这是它和普通驱动最不像的地方之一。

### §6.3 同一个 `write()`，两种完全不同的含义

```c
/* drivers/input/misc/uinput.c:626-628 —— 原文 */
	retval = udev->state == UIST_CREATED ?
			uinput_inject_events(udev, buffer, count) :
			uinput_setup_device_legacy(udev, buffer, count);
```

| 状态 | `write()` 的语义 | 期望的 buffer 内容 | 实现 |
|---|---|---|---|
| `UIST_CREATED` | **注入事件** | 一串 `struct input_event` | `uinput_inject_events()`（`:586-611`） |
| 其余两态 | **声明设备** | 一个 `struct uinput_user_dev` | `uinput_setup_device_legacy()`（`:523-584`） |

★ 这和 §1.5 里 joydev 的双读协议是同一类设计（一个 fd 上两套语义），但**判据不同**：joydev 看 `count` 的大小，uinput 看**自己的状态**。两者的共同后果是一样的——**没有任何一个 API 版本号能让用户态提前知道自己在跟哪套协议说话**，只能靠约定。

两套设备声明方式的分工：

| | legacy | 现代 |
|---|---|---|
| 入口 | `write(fd, &uidev, sizeof(uidev))` | `ioctl(fd, UI_DEV_SETUP, &setup)` + 每轴一次 `UI_ABS_SETUP` |
| uapi 结构 | `struct uinput_user_dev`（`include/uapi/linux/uinput.h:223-231`） | `struct uinput_setup`（`:67-71`）+ `struct uinput_abs_setup`（`:102-106`） |
| ABS 参数 | 四个定长数组 `absmax[]`/`absmin[]`/`absfuzz[]`/`absflat[]`，长度写死 `ABS_CNT` | 逐轴一个完整的 `struct input_absinfo` |
| `resolution` 能不能设 | **不能**——老结构里没有这个字段 | 能 |
| 状态落点 | `:578` | `:484` |

★ **legacy 路径设不了 `resolution`**，这是它唯一无法弥补的功能缺失（`fuzz`/`flat` 老结构里都有）。所以「触摸屏用 uinput 模拟」必须走现代路径，否则用户态算不出物理尺寸。

### §6.4 反方向：内核的回调要跑到用户态去执行

普通驱动的 `dev->ff->upload()` 就在内核里同步执行完。uinput 的设备是用户态造的，力反馈效果也只有用户态知道怎么放——于是 uinput 必须把**内核发起的回调**变成**用户态能读到的请求**，再等它回话。这是全子系统唯一一处这样的倒转。

```
 内核侧（evdev 的 EVIOCSFF ioctl 落到这里）
   input_ff_upload()  →  ff->upload = uinput_dev_upload_effect()   :234-257
                              │ 填 struct uinput_request
                              ▼
                         uinput_request_submit()                   :172-192
                              │ 排进 udev->requests[]、唤醒 waitq
                              │ wait_for_completion_timeout(&request->done, 30 * HZ)
                              ▼   ←──────────────────────────┐
 用户态侧                                                     │
   read(fd)  →  收到一个 (EV_UINPUT, UI_FF_UPLOAD, id) 事件     │
   ioctl(UI_BEGIN_FF_UPLOAD)  →  取出 effect                  │
   ……自己实现效果……                                           │
   ioctl(UI_END_FF_UPLOAD)    →  写回 retval，complete() ──────┘
```

| 环节 | 函数 | 关键细节 |
|---|---|---|
| 分配 request id | `uinput_request_alloc_id()`（`:95-114`） | 在 `requests_lock` 下线性扫 16 个槽 |
| 找回 request | `uinput_request_find()`（`:116-124`） | 用户态给的 `request_id` 要校验 |
| 占槽 / 让槽 | `uinput_request_reserve_slot()`（`:126-132`）/ `uinput_request_release_slot()`（`:134-142`） | 槽满时 `wait_event_interruptible()` 等 |
| 送出并等 | `uinput_request_send()`（`:145-169`）+ `uinput_request_submit()`（`:172-192`） | ★ **超时 `30 * HZ` 返回 `-ETIMEDOUT`** |
| 拆除时清账 | `uinput_flush_requests()`（`:201-215`） | 把所有未完成 request 直接 `complete()` 掉，否则 `UI_DEV_DESTROY` 会卡住 |

★ **`UINPUT_NUM_REQUESTS` 是 16（`:35`），和 `UINPUT_BUFFER_SIZE` 的 16（`:34`）是两个无关的 16**——前者是并发未完成的 FF 请求上限，后者是内核→用户态的事件环大小。**别把这两个数字合并成一句话说。**

★ **`uinput_request_send()` 检查 `udev->state != UIST_CREATED` 就返回 `-ENODEV`（`:154-157`）**，所以一个 FF 请求在设备被 destroy 的瞬间发起会失败而不是挂住。

★ **通知用户态用的是一个假事件类型 `EV_UINPUT`**：`#define EV_UINPUT 0x0101`（`include/uapi/linux/uinput.h:219`）。0x0101 = 257，远大于 `EV_MAX`（0x1f），所以它**不可能是一个真的 input 事件**——`uinput_request_send()` 在 `:165` 直接调 `uinput_dev_event()` 把它塞进自己那 16 格环里，**绕过了 `input_event()` 全套分发**（`03-internals.md` A8）。三个字段的含义被重定义为 `(EV_UINPUT, request->code, request->id)`，其中 `code` 只有 `UI_FF_UPLOAD`（1）和 `UI_FF_ERASE`（2）两个值（`:220-221`）。**所以 uinput 的 `read()` 返回的字节流里混着两类东西，用户态必须按 `type == EV_UINPUT` 分流**：

| 类别 | 从哪来 | 举例 |
|---|---|---|
| 真的下行事件 | `dev->event = uinput_dev_event`（`:360`），由核心的 `input_event()` 在**别的**进程写 evdev 时调进来 | `EV_LED`/`LED_CAPSL`（别人按了大写锁定灯）、`EV_SND`、`EV_FF` 的 `FF_GAIN` |
| FF 请求通知 | `uinput_request_send()` 直接调 `uinput_dev_event()`（`:165`） | `(EV_UINPUT, UI_FF_UPLOAD, id)` |

★ **`read()` 里绝对不会出现自己 `write()` 注入的事件**——注入走 `input_event()` 往**下游 handler**（evdev 等）去，不会绕回 `dev->event`。这是初学者最容易在 uinput 上犯的误解。

`uinput_dev_upload_effect()` 有一处硬拒绝（`:247-249`）：`FF_PERIODIC` 且 `waveform == FF_CUSTOM` 时直接 `-EINVAL`。上面那段注释（`:241-246`）把理由和出路都写明了——`custom_data` 是个用户态采样缓冲的指针，没法跨进程传，「如果哪天真有这种设备，我们需要再定义一个 `UI_UPLOAD_SAMPLES` ioctl」。**所以这个 `-EINVAL` 是能力缺口而不是参数错误**，用户态无法通过换参数绕开。★ **这和 §4 的 `input_ff_effect_from_user()` 是同一个指针问题的两种处置**——input-compat 选择"翻译指针宽度"，uinput 选择"根本不支持"。

### §6.5 与 input-compat 的交叉：两套 `uinput_ff_upload_to_user()`

`uinput.c` 里 `uinput_ff_upload_to_user()` / `uinput_ff_upload_from_user()` **各有两份实现**：

| 实现 | 行 | 条件 | 做什么 |
|---|---|---|---|
| 第一份 | `:738-768` / `:770-794` | `#ifdef CONFIG_COMPAT`（`:730`） | 逐字段搬进 `struct uinput_ff_upload_compat`（`:731-736`），`compat_uptr_t` 换宽度 |
| 第二份 | `:798-805` / `:807-814` | `#else`（`:796`） | 一次 `copy_to_user`/`copy_from_user` 了事 |

这和 §4 讲的 `input-compat.c` 的 `#ifdef`/`#else` 结构是同一个手法，但 **uinput 这一处没有把导出符号提到 `#endif` 外面**——因为这两个函数是 `static`，不导出，不存在"两种配置下签名必须一致"的约束。★ **同一个技巧在两处出现，一处需要额外的收尾动作、另一处不需要，判据是"符号有没有跨编译单元的契约"。**

三个会随位宽变号的 ioctl 在 `#ifdef CONFIG_COMPAT` 里另立了 `_COMPAT` 编号（`:1065-1070`，理由写在 `:1061-1064` 的注释里），`uinput_compat_ioctl()`（`:1072-1088`）把它们翻译回正常号再交给同一个 handler。★ **`UI_SET_PHYS` 之所以在列是因为它的第三个参数是 `char *`**——`include/uapi/linux/uinput.h:145` 写的是 `_IOW(UINPUT_IOCTL_BASE, 108, char*)`，一个裸指针类型进了 `_IOW`，`sizeof` 因此随位宽变。同一批 `UI_SET_*BIT`（`:136-147`）其余九个的第三参数都是 `int`，不受影响。**这一处 uapi 设计失误的代价是内核里多了一个编号、一个 `#define`、一条 switch 分支。**

### §6.6 变长 ioctl 走第二个 switch

`uinput_ioctl_handler()` 里有**两个** `switch`：第一个（`:878-1028`）匹配完整的 `cmd`，落不到任何 case 时不返回错误，而是往下走到第二个（`:1033-1046`），只有两个都没命中才在 `:1048` 落到 `-EINVAL`：

```c
/* drivers/input/misc/uinput.c:1030-1048 —— 原文骨架，省掉了两个 case 的函数体 */
	size = _IOC_SIZE(cmd);

	/* Now check variable-length commands */
	switch (cmd & ~IOCSIZE_MASK) {
	case UI_GET_SYSNAME(0):        /* :161 定义，用 _IOC() 而不是 _IOR() */
	case UI_ABS_SETUP & ~IOCSIZE_MASK:
	}

	retval = -EINVAL;
```

两个 case 的写法不一样：`UI_GET_SYSNAME(0)`（`include/uapi/linux/uinput.h:161`）本身是个带长度参数的宏，传 0 得到"长度字段为零"的那个编号，正好等于 `cmd & ~IOCSIZE_MASK`；`UI_ABS_SETUP`（`:135`）是定长宏，所以要手动 `& ~IOCSIZE_MASK`。**两种写法等价，混用只是历史原因**，但读代码时容易以为前者有什么特别含义。

★ `UI_ABS_SETUP` 在第一个 switch 里留了一行注释而不是 case：`/* UI_ABS_SETUP is handled in the variable size ioctls */`（`:896`）。这是本文件里少见的"作者替读者想过一遍"的地方。

### §6.7 参数校验与 arm64

`uinput_validate_absinfo()`（`:397-433`）是三条拒绝，**按源码里的先后顺序**：

| # | 行 | 条件（原文） | 消息（逐字） |
|---|---|---|---|
| 1 | `:405-410` | `(min != 0 \|\| max != 0) && max < min` | `"%s: invalid abs[%02x] min:%d max:%d\n"` |
| 2 | `:412-417` | `!check_sub_overflow(max, min, &range) && abs->flat > range` | `"%s: abs_flat #%02x out of range: %d (min:%d/max:%d)\n"` |
| 3 | `:426-431` | `code == ABS_MT_SLOT && max > 99` | `"%s: unreasonably large number of slots requested: %d\n"` |

三处细节容易记错：

- 第 1 条的例外是**两者同时为 0**——`min == max == 0` 是"这根轴我没设范围"的合法表达，不能因为 `max < min` 不成立就以为它没被特殊照顾（`max == min == 0` 时 `max < min` 本来就是假，这个 `(min != 0 || max != 0)` 前置条件其实是冗余的，但它把意图写出来了）
- 第 2 条比的是 `flat > range`，**`range` 就是 `max - min`，没有除以 2**。`check_sub_overflow()` 先兜住溢出，溢出时这条检查直接跳过——也就是说 `min = INT_MIN, max = INT_MAX` 这种极端值能带着任意 `flat` 通过
- 第 3 条的 **99 是一个字面量，树里没有 `MT_MAX_SLOTS` 这种符号**。重跑 `grep -rnE 'define[[:space:]]+MT_MAX_SLOTS' .` → 无输出。理由写在 `:419-424` 的注释里：100 个触点让 `struct input_mt` 保持在两页以内，而且"我们没用内核内建的 slot 分配，所以不需要给 red 表分配内存"

★ **三条全部用 `printk(KERN_DEBUG …)` 而不是 `dev_err()`，而这个函数的第一个形参 `struct input_dev *dev` 从头到尾没被用过。** 形参在、消息里却只有 `UINPUT_NAME` 这个字符串常量，所以 dmesg 里看不出是哪个进程哪个设备触发的——`07-debug.md` 里排查 uinput 失败要记住这一点。

★ **第 2 条校验的是 `flat`**，而 `03-internals.md` B2.3 说过核心自己只用 `fuzz`、不看 `flat`。加上本篇 joydev 那一小节（1.5）里 joydev 确实用 `flat` 当死区——**`flat` 的完整图景是：核心不用、joydev 用、uinput 校验**。三处结论必须放在一起看才不矛盾。

`uinput_validate_absbits()`（`:436-458`）遍历所有置位的 ABS 轴逐个调上面那个函数；`uinput_abs_setup()`（`:488-520`）是单轴版本，先 `input_alloc_absinfo()`、校验通过再写进 `absinfo[]`。

**arm64 相关性**：完全相关，且是**容器和自动化测试的刚需**（虚拟键盘、libinput 的测试套件、远程桌面的输入注入）。它不依赖任何体系结构特性，唯一的外部依赖是 `CONFIG_INPUT_FF_MEMLESS` 不需要——uinput 自己实现 `ff->upload`，不经过 §3 的 ff-memless。

---

## §7 input-mt：多点触控的槽位记账

**挂点**：**两处**。A3（`input_mt_init_slots()` 声明能力、分配槽位数组）和 A8.4（`input_mt_report_slot_state()` / `input_mt_sync_frame()` 在每帧事件路径上跑）。这是本篇唯一一个挂在两个不同阶段的框架。

文件：`drivers/input/input-mt.c` 538 行，`include/linux/input/mt.h` 129 行。**无独立 Kconfig**，无条件编进 `input-core.o`。

它解决的问题是：MT-B 协议（`Documentation/input/multi-touch-protocol.rst`）要求驱动为每个接触点维护一个"槽"，并在每一帧里只报变化的槽。这份记账如果让每个触摸屏驱动自己做，就会有 60 份各不相同的实现。

### §7.1 一个槽有两个正交的布尔状态

这是 input-mt 里最容易看错的地方，**必须先分清**：

| 状态 | 判据 | 含义 | 谁改它 |
|---|---|---|---|
| **active（活着）** | `input_mt_is_active()`：`ABS_MT_TRACKING_ID >= 0`（`include/linux/input/mt.h:65-68`） | 这个接触点还在屏幕上 | `input_mt_report_slot_state()` 通过报 `ABS_MT_TRACKING_ID` 改 |
| **used（本帧提到过）** | `input_mt_is_used()`：`slot->frame == mt->frame`（`:70-74`） | 驱动在**当前这一帧**里碰过这个槽 | `input_mt_report_slot_state()` 在 `:149` 写 `slot->frame = mt->frame` |

★ **两者可以四种组合任意出现**，其中「active 但本帧没提到」正是 `INPUT_MT_DROP_UNUSED` 要清理的那一类——手指抬起来了、驱动却懒得为它单独报一次 `TRACKING_ID = -1`。

★ **"used" 是靠一个单调递增的帧号实现的，不是靠 bitmap 逐帧清零。** `mt->frame` 在 `input_mt_sync_frame()` 每帧末尾 `++`（`:354`），于是**上一帧的所有 `slot->frame` 自动全部变成"不等于当前帧"**，一次加法顶掉了 N 次清零。初值也很讲究：`input_mt_init_slots()` 在 `:97` 写 `mt->frame = 1`，注释就是 `/* Mark slots as 'unused' */`——因为 `kzalloc` 出来的槽 `frame` 都是 0，1 ≠ 0 就是"没人用过"。

### §7.2 `input_mt_init_slots()` 一个函数干了六件事

`:39-104`。它是 A3 阶段的能力声明大户，flags 决定它替驱动多设多少位：

| flags 位 | 值 | 效果（行） |
|---|---|---|
| 无条件 | | `input_set_abs_params(ABS_MT_SLOT, 0, num_slots-1)`（`:59`）+ `ABS_MT_TRACKING_ID, 0, TRKID_MAX`（`:60`） |
| `INPUT_MT_POINTER` 或 `INPUT_MT_DIRECT` | 0x0001 / 0x0002 | `EV_KEY` + `BTN_TOUCH`（`:63-64`）；把 `ABS_MT_POSITION_X/Y`、`ABS_MT_PRESSURE` 的参数**复制**到 `ABS_X/Y`、`ABS_PRESSURE`（`copy_abs()`，`:15-37`；调用 `:66-68`） |
| `INPUT_MT_POINTER` | 0x0001 | `BTN_TOOL_FINGER`、`BTN_TOOL_DOUBLETAP`，再按 `num_slots >= 3/4/5` 逐级加 `TRIPLETAP`/`QUADTAP`/`QUINTTAP`（`:71-78`）+ `INPUT_PROP_POINTER`（`:79`） |
| `INPUT_MT_DIRECT` | 0x0002 | `INPUT_PROP_DIRECT`（`:81-82`） |
| `INPUT_MT_SEMI_MT` | 0x0010 | `INPUT_PROP_SEMI_MT`（`:83-84`） |
| `INPUT_MT_TRACK` | 0x0008 | ★ **额外 `kcalloc(num_slots², sizeof(int))`** 给 `mt->red`（`:85-90`） |
| `INPUT_MT_DROP_UNUSED` | 0x0004 | 在 `init` 阶段**不做任何事**，只被记进 `mt->flags`，到 `input_mt_sync_frame()` 才起作用 |

★ **`INPUT_MT_TRACK` 的内存开销是 O(槽数²)**：`mt->red` 是内核内建 slot 分配算法（§7.4）用的代价矩阵。所以 `input_mt_init_slots()` 硬性拒绝 `num_slots > 1024`（`:49-51`，注释 `/* Arbitrary limit for avoiding too large memory allocation. */`）——1024 槽在开 `INPUT_MT_TRACK` 时是 1024² × 4 B = 4 MB。

★ **这个 1024 和 §6.7 里 uinput 的 99 是两个独立的上限，而且理由不同**：核心怕 `red` 矩阵太大，uinput 怕 `struct input_mt` 本身超过两页——**uinput 明确说了它不走内核内建分配、不会分配 `red`**（`drivers/input/misc/uinput.c:419-424` 的注释），所以它管的是柔性数组那部分。**用户态通过 uinput 最多 100 槽，内核驱动最多 1024 槽。**

★ **重复调用是被允许的**（`:46-48`）：`mt` 已存在时，`num_slots` 相同返回 0、不同返回 `-EINVAL`。所以一个驱动栈里两层（如 HID 通用层和具体 quirk）都调一次是安全的，前提是槽数一致。

### §7.3 报一个接触点，和关一帧

驱动侧的标准写法（详细骨架见 `05-usage.md`）是三层嵌套：`input_mt_slot()` 选槽 → `input_mt_report_slot_state()` 报状态 → 逐轴 `input_report_abs()`；所有槽报完调一次 `input_mt_sync_frame()`。

`input_mt_report_slot_state()`（`:138-164`）里有两个容易被忽略的行为：

```c
/* drivers/input/input-mt.c:151-161 —— 原文 */
	if (!active) {
		input_event(dev, EV_ABS, ABS_MT_TRACKING_ID, -1);
		return false;
	}

	id = input_mt_get_value(slot, ABS_MT_TRACKING_ID);
	if (id < 0)
		id = input_mt_new_trkid(mt);

	input_event(dev, EV_ABS, ABS_MT_TRACKING_ID, id);
	input_event(dev, EV_ABS, ABS_MT_TOOL_TYPE, tool_type);
```

- **`active == false` 时它先写 `slot->frame`（`:149`）再走 `!active` 分支**——所以「报一次抬起」也算"本帧提到过"，不会被同一帧的 `DROP_UNUSED` 重复处理
- **`ABS_MT_TOOL_TYPE` 是无条件报的**（`:161`）。kerneldoc 说"只在对应 absbit 置位时才报"（`:133-134`），这不是这里做的判断，而是核心在 `input_handle_abs_event()` 里过滤掉的（见 `03-internals.md` A8.4）。★ **文档描述的是可观察行为，代码里的实现位置在另一个文件**——找不到那个 `if` 不代表文档错了

★ **`input_mt_new_trkid()` 是 `mt->trkid++ & TRKID_MAX`**（`include/linux/input/mt.h:80-83`），`TRKID_MAX` 是 `0xffff`（`:13`）。所以 tracking id 在 65536 之后回绕到 0，**它不是唯一 id，是"够长时间内不重复"的 id**。

`input_mt_sync_frame()`（`:333-355`）做三件事，顺序固定：

| 步 | 条件 | 动作 |
|---|---|---|
| 1 | `flags & INPUT_MT_DROP_UNUSED` | 取 `event_lock` 调 `__input_mt_drop_unused()`（`:341-347`） |
| 2 | 无条件 | `input_mt_report_pointer_emulation(dev, use_count)`（`:352`），其中 `use_count = POINTER && !SEMI_MT`（`:349-350`） |
| 3 | 无条件 | `mt->frame++`（`:354`） |

### §7.4 指针仿真：挑"最老"的那个接触点

`input_mt_report_pointer_emulation()`（`:199-259`）把 MT 状态压回单点的 `ABS_X`/`ABS_Y`/`ABS_PRESSURE`/`BTN_TOUCH`，供只懂单点协议的用户态使用（mousedev 就靠它，见 §1.4）。

★ **"最老"是用回绕安全的比较挑出来的**：

```c
/* drivers/input/input-mt.c:218-221 —— 原文 */
		if ((id - oldid) & TRKID_SGN) {
			oldest = ps;
			oldid = id;
		}
```

`TRKID_SGN` 是 `((TRKID_MAX + 1) >> 1)` = 0x8000（`:13`）。**这是把 16 位空间当环来比大小**——直接写 `id < oldid` 会在 tracking id 回绕的那一刻挑错接触点。初值 `oldid = mt->trkid`（`:209`），即"下一个要发的 id"，于是所有已存在的 id 相对它都是"过去"。

★ **有一个 hover 特例**（`:228-239`）：`count == 0` 且设备只报整体 `ABS_DISTANCE`（不报 per-contact 的 `ABS_MT_DISTANCE`）且 `ABS_DISTANCE != 0` 时，**强行把 count 当 1**，好让 `BTN_TOOL_FINGER` 报出来。注释里写明了这是给"手指悬空但未触屏"的设备用的。这条路径的存在意味着 **`BTN_TOOL_FINGER` 为真不等于有接触点**。

内核内建的 slot 分配（`INPUT_MT_TRACK` 那条路）由 `input_mt_assign_slots()`（`:483-503`）实现，内部是一个在 `mt->red` 上跑的指派问题近似解：`input_mt_set_matrix()`（`:406-427`）算距离平方、`find_reduced_matrix()`（`:391-404`）+ `adjust_dual()`（`:358-389`）做行列归约、`input_mt_set_slots()`（`:429-481`）落槽。**这四个函数本篇只给位置不展开**——它们是纯算法，不涉及任何 input 子系统机制；证据等级记在 `99-evidence.md` §2。

### §7.5 三个函数的锁语义各不相同

| 函数 | 导出 | `event_lock` | 用 `input_event()` 还是 `input_handle_event()` |
|---|---|---|---|
| `input_mt_report_slot_state()`（`:138`） | `EXPORT_SYMBOL`（`:165`） | **不碰**，交给 `input_event()` 自己加 | `input_event()` |
| `input_mt_drop_unused()`（`:283`） | `EXPORT_SYMBOL`（`:298`） | **自己 `spin_lock_irqsave()`**（`:290`） | 内部 helper 直接报 |
| `input_mt_release_slots()`（`:306`） | ★ **不导出** | ★ **`lockdep_assert_held(&dev->event_lock)`**（`:310`）——要求调用者已持有 | `input_handle_event()`（`:319`） |

★ **`input_mt_release_slots()` 是全树唯一一个只给核心自己用的 input-mt 函数。** 它的声明不在 `include/linux/input/mt.h` 里，而在 `drivers/input/input-core-private.h:12`——那个头文件开头就写着 `should not be used by input drivers or handlers`。重跑：

```bash
grep -rn --include=*.c --include=*.h 'input_mt_release_slots' .
```

→ 四行，其中三行是代码：定义 `drivers/input/input-mt.c:306`、声明 `drivers/input/input-core-private.h:12`、唯一调用点 `drivers/input/input.c:1862`（在 `spin_lock_irq(&dev->event_lock)` 之后紧接着）；第四行是 kerneldoc 的标题行 `drivers/input/input-mt.c:301`。**没有 `EXPORT_SYMBOL`，树外模块拿不到它。**

★ 同一个私有头文件里还声明了 `input_handle_event()`（`:13-14`）——`03-internals.md` A8 详细讲过的那个函数**也是核心私有的**。所以「读了 03 就知道 `input_handle_event()` 怎么用」和「可以在驱动里调它」是两回事。

★ **`input_mt_release_slots()` 把 `mt->frame` 加了两次**（`:314` 和 `:321`）。第一次是手法：`frame++` 让所有槽瞬间变成 "unused"，于是紧跟的 `__input_mt_drop_unused()`（`:316`）会把**每一个** active 槽都抬起来——这是"清空所有接触点"的最短写法。第二次是善后，让后续帧不会误认为这些槽在当前帧被提到过。

**arm64 相关性**：完全相关，而且是**触摸屏驱动的必经之路**——`input_mt_init_slots()` 全树 64 个文件 74 个调用点（口径见篇末统计表）。

---

## §8 input-poller：给没有中断的设备装一个定时器

**文件**：`drivers/input/input-poller.c`（222 行）+ **私有**头文件 `drivers/input/input-poller.h`（18 行）。无独立 Kconfig，编进 `input-core.o`。

**挂点**：§0 分诊表记的「A3 之后、A4 之前」指的是**驱动侧**那一步——`input_setup_polling()` 必须在 `input_register_device()`（A4）之前调完。核心侧还有另外两组挂点，一起列在下表里。

| 时机 | 核心侧调用 | 位置 |
|---|---|---|
| A4 注册末尾 | `input_dev_poller_finalize()` 补默认值 | `drivers/input/input.c:2450-2451` |
| A7 打开 / A10 关闭 | `input_dev_poller_start()` / `_stop()` | `drivers/input/input.c:663-664` / `:705-706` |
| 运行期 inhibit / uninhibit | 同上两个函数 | `drivers/input/input.c:1857-1858` / `:1890-1891` |
| A12 释放 | `kfree(dev->poller)` | `drivers/input/input.c:1665` |
| sysfs 属性组挂进设备目录 | `&input_poller_attribute_group` | `drivers/input/input.c:1655` |

### §8.1 两套 API，两个头文件，读者只能碰其中一套

这个框架的对外面被**刻意切成了两半**，切法就是头文件放在哪里：

| 面向 | 函数 | 声明在 | 导出 |
|---|---|---|---|
| 驱动 | `input_setup_polling()` | `include/linux/input.h` | `EXPORT_SYMBOL`（`:92`） |
| 驱动 | `input_set_poll_interval()` | `include/linux/input.h` | `EXPORT_SYMBOL`（`:110`） |
| 驱动 | `input_set_min_poll_interval()` | `include/linux/input.h` | `EXPORT_SYMBOL`（`:117`） |
| 驱动 | `input_set_max_poll_interval()` | `include/linux/input.h` | `EXPORT_SYMBOL`（`:124`） |
| 驱动 | `input_get_poll_interval()` | `include/linux/input.h` | `EXPORT_SYMBOL`（`:133`） |
| 仅核心 | `input_dev_poller_finalize()` | `drivers/input/input-poller.h` | 不导出 |
| 仅核心 | `input_dev_poller_start()` | `drivers/input/input-poller.h` | 不导出 |
| 仅核心 | `input_dev_poller_stop()` | `drivers/input/input-poller.h` | 不导出 |
| 仅核心 | `input_poller_attribute_group`（变量） | `drivers/input/input-poller.h` | 不导出 |

★ 判据是**头文件的位置**，不是 `static`：后四个符号都不是 `static`（`input.c` 要用），但它们的声明只出现在 `drivers/input/` 里的私有头文件中，树外模块拿不到原型。这和 §7 末尾 `input_mt_release_slots()` 的处境完全一样，只是那个住在 `input-core-private.h`、这个住在 `input-poller.h`。**同一棵树里有两个"核心私有头文件"**，别以为只有一个。

### §8.2 `struct input_dev_poller` 从不出现在驱动的视野里

```c
/* drivers/input/input-poller.c:15-24 */
struct input_dev_poller {
	void (*poll)(struct input_dev *dev);

	unsigned int poll_interval; /* msec */
	unsigned int poll_interval_max; /* msec */
	unsigned int poll_interval_min; /* msec */

	struct input_dev *input;
	struct delayed_work work;
};
```

结构体**定义在 `.c` 里**，`struct input_dev` 只持有一个 `struct input_dev_poller *poller` 指针。所以驱动改不了里面任何字段，只能通过上表那五个函数间接改——这是本子系统里封装做得最彻底的一处。

### §8.3 默认值：500 ms，而且 max 会跟着驱动的取值走

```c
/* drivers/input/input-poller.c:46-51 */
void input_dev_poller_finalize(struct input_dev_poller *poller)
{
	if (!poller->poll_interval)
		poller->poll_interval = 500;
	if (!poller->poll_interval_max)
		poller->poll_interval_max = poller->poll_interval;
}
```

★ **第二条比第一条重要得多**：驱动如果只调了 `input_set_poll_interval(dev, 100)` 而没调 `input_set_max_poll_interval()`，那么 max 就被钉成 100。此后用户态往 `poll` 属性里写任何 **>100** 的值都会拿到 `-EINVAL`（`:162-163`），**只能往下调，不能往上调**。想让用户态放慢轮询以省电，驱动必须显式声明一个更大的 max。

三个 setter（`:105-109` / `:112-116` / `:119-123`）都以 `input_dev_ensure_poller()`（`:94-103`）开路，没 poller 就打一行 `dev_err(… "poller structure has not been set up\n")` 然后**静默返回**——不是返回错误码，函数原型是 `void`。只有 `input_get_poll_interval()`（`:126-132`）会返回 `-EINVAL`。

### §8.4 定时器行为：freezable 工作队列 + 对齐

```c
/* drivers/input/input-poller.c:26-35 */
static void input_dev_poller_queue_work(struct input_dev_poller *poller)
{
	unsigned long delay;

	delay = msecs_to_jiffies(poller->poll_interval);
	if (delay >= HZ)
		delay = round_jiffies_relative(delay);

	queue_delayed_work(system_freezable_wq, &poller->work, delay);
}
```

三个细节各有原因：

| 细节 | 代码 | 为什么 |
|---|---|---|
| `system_freezable_wq` | `:34` | 挂起时轮询必须停下，否则会挡住 suspend |
| `delay >= HZ` 才对齐 | `:32-33` | 只有周期 ≥1 秒的慢轮询才值得凑到整秒边界省唤醒；快轮询对齐会引入可观的抖动 |
| `input_dev_poller_start()` 先轮询一次 | `:54-61` | 打开设备后立刻有一份状态，用户态不必等一个周期。**这一次轮询是同步执行的**，跑在 `input_open_device()` 的上下文里 |

`input_dev_poller_stop()`（`:63-66`）就是一行 `cancel_delayed_work_sync()`——**同步**取消，返回后 `poll` 回调保证不在跑。

### §8.5 sysfs：三个属性，靠 `is_visible` 藏起来

```c
/* drivers/input/input-poller.c:203-210 */
static umode_t input_poller_attrs_visible(struct kobject *kobj,
					  struct attribute *attr, int n)
{
	struct device *dev = kobj_to_dev(kobj);
	struct input_dev *input = to_input_dev(dev);

	return input->poller ? attr->mode : 0;
}
```

| 属性 | 权限 | 读 | 写 |
|---|---|---|---|
| `poll` | 0644（`:180-181`） | 当前周期（`:137-144`） | `kstrtouint` → 界内则改周期并按需重排（`:146-178`） |
| `max` | 0444（`:191`） | `poll_interval_max`（`:183-190`） | 不可写 |
| `min` | 0444（`:201`） | `poll_interval_min`（`:193-200`） | 不可写 |

★ **三个 show 函数都直接解引用 `input->poller`，没有一个做 NULL 检查**。安全性完全来自 `is_visible` 返回 0 时 sysfs 根本不创建这些文件。这是一条"正确但脆弱"的依赖：谁把这三个属性搬到别的属性组里、或者忘了带上 `.is_visible`，就是一个空指针解引用。

★ `input_poller_attribute_group`（`:219-222`）**只有 `.is_visible` 和 `.attrs`，没有 `.name`**——所以三个文件直接躺在设备目录下（`/sys/class/input/inputN/poll`），不像 §2 里 serio 的 `id/` 那样自带子目录。写 `poll` 时会拿 `mutex_lock(&input->mutex)`（`:165`），并且只在 `input_device_enabled()` 为真时才重排工作项（`:169-173`）——设备没打开时改周期只改数字，不动定时器。

**arm64 相关性**：相关。全树 30 个文件 30 个调用点用了 `input_setup_polling()`（口径见篇末统计表），典型是 I2C 上的加速度计、按键扩展芯片这类只能查询、拿不到中断线的器件。

---

## §9 input-leds：把 `EV_LED` 桥到 LED class

**文件**：`drivers/input/input-leds.c`（220 行）。Kconfig `INPUT_LEDS`（`drivers/input/Kconfig:28`），默认 `y`，可编成模块。

**挂点**：A5→A6→A7。它是一个**标准 handler**，和 §1 的 evdev 走同一条路——被 `input_attach_handler()` 匹配上，`input_register_handle()` 连线，`input_open_device()` 打开。区别只在于它不建字符设备，而是建 LED class 设备。

### §9.1 匹配面只有一行

```c
/* drivers/input/input-leds.c:188-194 */
static const struct input_device_id input_leds_ids[] = {
	{
		.flags = INPUT_DEVICE_ID_MATCH_EVBIT,
		.evbit = { BIT_MASK(EV_LED) },
	},
	{ },
};
```

对照 §1 那三个 handler 动辄十几条 id 表项，这里只有一条：**凡是声明了 `EV_LED` 的设备都接管**。判据落在 `input_match_device_id()`（`drivers/input/input.c:1000-1033`）第 `:1024` 行的 `bitmap_subset(id->ledbit, dev->ledbit, LED_MAX)`——★ 更准确地说是 `:1019` 的 `bitmap_subset(id->evbit, dev->evbit, EV_MAX)`，因为这张表只填了 `evbit`。是**子集**判定而不是按位与：表里置了的位设备必须全有，设备多出来的位不影响。

★ 而 `.flags = INPUT_DEVICE_ID_MATCH_EVBIT` 这一行**对运行期匹配没有作用**。看 `input_match_device_id()` 的结构：`:1003-1017` 四个字段（bus / vendor / product / version）各自被 `id->flags &` 门控，而 `:1019-1028` 那十个 `bitmap_subset()` 是**无条件执行的**，一次都没查 `flags`。也就是说位图类的 `INPUT_DEVICE_ID_MATCH_*BIT` 标志在这个函数里全是装饰——不写它，`evbit` 一样会被比；写了它，也不会额外比什么。它真正的用处在 `MODULE_DEVICE_TABLE(input, …)`（`:195`）那条路上，由 `scripts/mod/file2alias.c` 生成 modalias 时读取——`:776` 那行 `if (flags & INPUT_DEVICE_ID_MATCH_EVBIT)` 决定 modalias 里要不要带上 `e` 段。§13.1 的 evbug 从反方向印证了这一点：那张表 `.flags` 是 0、十个位图全空，空集是任何集合的子集，于是匹配一切。

`input_leds_handler`（`:197-203`）填了 `.event` / `.connect` / `.disconnect` / `.name = "leds"` / `.id_table` 五个字段。★ 但是：

```c
/* drivers/input/input-leds.c:66-69 */
static void input_leds_event(struct input_handle *handle, unsigned int type,
			     unsigned int code, int value)
{
}
```

**`.event` 回调是个空函数**——两行大括号中间什么都没有。

全树 `.event` 一共被赋值 9 次、分布在 8 个 handler 上（`grep -rn --include=*.c -A8 'struct input_handler [a-z_0-9]* = {' . | grep -E '\.event'`：`drivers/tty/vt/keyboard.c:1644`、`drivers/input/apm-power.c:100`、`drivers/input/evdev.c:1421`、`drivers/input/input-leds.c:198`、`drivers/input/evbug.c:82`、`drivers/input/mousedev.c:1054`、`drivers/input/joydev.c:1077`、`net/rfkill/input.c:310`——evdev 多出来的那一次是同一行附近的 `.events`，见 `evdev.c:1422`）。**逐个打开这 8 个回调的函数体，只有 `input_leds_event()` 是空的**，其余七个第一句就在干活。

★ **`kbd_handler` 这一个的 `.event` 后面还接了第二级分发链，不在本子系统里。** `drivers/tty/vt/keyboard.c` 在收到 keycode 之后会往一条自己的 `ATOMIC_NOTIFIER_HEAD(keyboard_notifier_list)`（`:161`，注册/注销 `:163-173` 两个 `EXPORT_SYMBOL_GPL`）上再广播一轮，五个事件码 `KBD_KEYCODE` / `KBD_UNBOUND_KEYCODE` / `KBD_UNICODE` / `KBD_KEYSYM` / `KBD_POST_KEYSYM` 定义在 `include/linux/notifier.h:232-236`，参数结构 `struct keyboard_notifier_param` 在 `include/linux/keyboard.h:11-17`，五个 `atomic_notifier_call_chain()` 调用点在 `drivers/tty/vt/keyboard.c:1475`、`:1478`、`:1496`、`:1516`、`:1527`。除最后一个之外回调返回 `NOTIFY_STOP` 就能把按键吃掉（`:1477-1483` 是 `KBD_KEYCODE` 那一路的判断）。全树只有两个消费者：`drivers/accessibility/speakup/main.c` 和 `drivers/accessibility/braille/braille_console.c`。

这条链**不是 input 核心的机制**——它住在 tty/vt 里、只看得到已经翻译成 keycode 的控制台按键，跟 `input_handler` 的 `.event` 是两层不同的东西；本文档集的边界停在 `kbd_handler.event`（`:1644`）这一行，链上的语义不覆盖。官方原文见 `Documentation/input/notifier.rst`（55 行，全篇讲的就是这条链）。

为什么不干脆不填？因为 `input_to_handler()`（`03-internals.md` A8.6）的分发是这样写的：

```c
/* drivers/input/input.c:126-130 */
	if (handler->events)
		handler->events(handle, vals, count);
	else if (handler->event)
		for (v = vals; v != vals + count; v++)
			handler->event(handle, v->type, v->code, v->value);
```

两个字段都为 NULL 时事件在这里静静地丢掉，所以填空函数和不填在功能上等价。所以这个空函数**纯粹是文档性质的**：它宣告"我知道有事件流过，我确实不需要它"。LED 状态是在 `brightness_get` 里现读 `input->led` 位图的（下一小节），不靠事件通知。

### §9.2 两个方向：读位图，写注入

```c
/* drivers/input/input-leds.c:50-64 */
static enum led_brightness input_leds_brightness_get(struct led_classdev *cdev)
{
	struct input_led *led = container_of(cdev, struct input_led, cdev);
	struct input_dev *input = led->handle->dev;

	return test_bit(led->code, input->led) ? cdev->max_brightness : 0;
}

static void input_leds_brightness_set(struct led_classdev *cdev,
				      enum led_brightness brightness)
{
	struct input_led *led = container_of(cdev, struct input_led, cdev);

	input_inject_event(led->handle, EV_LED, led->code, !!brightness);
}
```

| 方向 | 手法 | 结果 |
|---|---|---|
| 读（`/sys/class/leds/*/brightness` cat） | 直接 `test_bit()` 读 `input->led` 位图 | 永远和核心记的状态一致，不会漂 |
| 写（echo 进 `brightness`） | `input_inject_event()` 走完整事件路径 | 最终落到 `input_dev->event()` 回调，由设备驱动去点灯 |

★ 写方向**不直接改 `input->led`**。位图是在 `input_handle_event()` 里由 `input_get_disposition()` 更新的（`03-internals.md` A8.3），所以 echo 完再 cat 能读回新值——但那是**事件路径走通之后**的副作用，不是这里赋的。而且改位图这一步就在判定里：

```c
/* drivers/input/input.c:308-315 */
	case EV_LED:
		if (is_event_supported(code, dev->ledbit, LED_MAX) &&
		    !!test_bit(code, dev->led) != !!value) {

			__change_bit(code, dev->led);
			disposition = INPUT_PASS_TO_ALL;
		}
		break;
```

`__change_bit()`（`:312`）先落，`disposition` 后设，设备驱动的 `event()` 回调更是要等到 `input_pass_values()` 之后才跑。所以驱动点灯失败时位图**照样已经改了**。**位图表示"核心认为灯该亮"，不表示"灯真的亮了"。**

★ `max_brightness = 1`（`:134`）。`EV_LED` 只有开关两态，所以 LED class 那套调光/闪烁能力在这里全部退化——`echo 128 > brightness` 会被 LED class 夹到 1。

### §9.3 名字表：按 LED 码下标，只有 11 个有名字

```c
/* drivers/input/input-leds.c:21-36 */
static const struct {
	const char *name;
	const char *trigger;
} input_led_info[LED_CNT] = {
	[LED_NUML] = { "numlock", VT_TRIGGER("kbd-numlock") },
	…
	[LED_CHARGING] = { "charging" },
};
```

数组长度是 `LED_CNT`（= `LED_MAX + 1` = 0x10 = 16，`include/uapi/linux/input-event-codes.h:954-955`），而**填了内容的只有 11 项**。这 11 项不是"挑出来的"——`grep -nE '^#define LED_' include/uapi/linux/input-event-codes.h` 返回 13 行，其中 11 行是真实 LED 码（`LED_NUML` 0x00 到 `LED_CHARGING` 0x0a），另两行是 `LED_MAX` 和 `LED_CNT`。**也就是说这张表覆盖了本版本定义的全部 LED 码**，末尾 5 个空洞对应的是 0x0b–0x0f 这段还没分配的码。

★ 空洞不是无害的：`input_leds_get_count()`（`:71-80`）和 `input_leds_connect()` 的循环体（`:118-120`）都用 `if (!input_led_info[led_code].name) continue;` 跳过没名字的码。所以驱动要是 `set_bit(0x0c, dev->ledbit)`，这个 LED **会被静默忽略**，不报错、不打日志。想加新 LED 码，改 `input-event-codes.h` 之外还必须回来补这张表——两处都改才生效。

`VT_TRIGGER(_name)` 宏（`:15-19`）在 `CONFIG_VT` 关闭时把 `.trigger` 展开成 `NULL`：

```c
#if IS_ENABLED(CONFIG_VT)
#define VT_TRIGGER(_name)	.trigger = _name
#else
#define VT_TRIGGER(_name)	.trigger = NULL
#endif
```

有 trigger 的四项是 numlock / capslock / scrolllock / kana，都是**键盘锁定状态**——只有 VT 层才知道这些状态，所以 VT 关掉时留着触发器名字没有意义。`compose` 那项虽然也是键盘锁，但它**没有 trigger**（表里只写了名字），因为 VT 层没有对应的 `kbd-compose` 触发器。剩下 sleep / suspend / mute / misc / mail / charging 六项本来就与 VT 无关。

### §9.4 `connect()`：先连线再建 LED，失败按相反顺序退

`input_leds_connect()`（`:83-168`）是本篇最标准的一段错误处理，值得逐段对照：

| 步骤 | 行 | 失败去处 |
|---|---|---|
| 数一数有几个有名字的 LED | `:94-96` | 0 个 → `-ENXIO`（直接返回，什么都没申请） |
| `kzalloc(struct_size(leds, leds, num_leds))` | `:98-100` | `-ENOMEM` |
| 填 `handle`（`.name = "leds"`，`.private = leds`） | `:104-107` | — |
| `input_register_handle()` | `:109-111` | `err_free_mem` |
| `input_open_device()` | `:113-115` | `err_unregister_handle` |
| 逐 LED：`kasprintf()` 拼名字 | `:126-132` | `err_unregister_leds` |
| 逐 LED：`led_classdev_register()` | `:139-145` | 打 `"failed to register LED %s: %d\n"`，`kfree` 名字后 `err_unregister_leds` |

三个标签是严格倒序的（`:152` / `:162` / `:165`），`err_unregister_leds` 用 `while (--led_no >= 0)`（`:153`）从**已经成功的最后一个**往前拆——`led_no` 此时正指向失败的那一个，先自减是对的。

★ 注意 `input_open_device()` 在**建 LED 之前**（`:113`），而 §1 那三个字符设备 handler 是在用户 `open()` 时才打开设备的。原因是 LED class 设备一露面就可能被 cat/echo，所以底层设备必须先处于打开状态。代价是：**只要一个键盘存在，它就一直是"被打开"的**，`03-internals.md` A7 里 `dev->open()` 回调的引用计数从注册那刻起就不为零。

LED 名字的形状是 `kasprintf(GFP_KERNEL, "%s::%s", dev_name(&dev->dev), input_led_info[led_code].name)`（`:126-128`），父设备是 `&dev->dev`（`:139`）。所以一块键盘的大写锁定灯落在 `/sys/class/leds/input3::capslock/`——**前缀是 `inputN` 这个 input 设备名，不是 event 号**。这是排查"哪个 LED 属于哪个键盘"时唯一可靠的线索。

`input_leds_disconnect()`（`:170-186`）是对称的：逐个 `led_classdev_unregister()` + `kfree(name)`（`:175-180`）、`input_close_device()`、`input_unregister_handle()`、`kfree(leds)`。

**arm64 相关性**：相关，默认 `y`。但实际有几个 LED 取决于键盘——USB HID 键盘会声明 `LED_NUML`/`LED_CAPSL`/`LED_SCROLLL`，纯 GPIO 按键（`gpio-keys`）通常一个都不声明，于是 `connect()` 在 `:96` 就返回 `-ENXIO`，什么也不建。

---

## §10 touchscreen.c：把 DT 属性翻译成 `absinfo`

**文件**：`drivers/input/touchscreen.c`（207 行）+ `include/linux/input/touchscreen.h`（32 行）。**没有独立 Kconfig**：`drivers/input/Makefile:9-10` 把它拼进了 `input-core-y`，所以只要 `CONFIG_INPUT` 打开它就在。

★ 尽管如此，文件末尾仍有 `MODULE_LICENSE("GPL v2")` 和 `MODULE_DESCRIPTION(…)`（`:206-207`）——**它永远不会被编成独立模块，这两行是装饰**。同一目录下另外四个成员的口径是一致的：`grep -c '^MODULE_' drivers/input/<文件>` 对 `input-compat.c`（§4）、`input-mt.c`（§7）、`input-poller.c`（§8）、`ff-core.c`（§3）都返回 **0**，只有 `input.c` 返回 **3**——那是 `input-core.o` 真正的模块元信息该待的地方。`touchscreen.c` 返回 **2**，是这一组里唯一的例外。

**挂点**：A3。驱动必须**先**用 `input_set_abs_params()`（单点）或 `input_mt_init_slots()`（多点）把轴建好，**再**调 `touchscreen_parse_properties()`。次序反了会出现下一小节那条警告。

### §10.1 它读的是父设备的属性，不是 input 设备的

```c
/* drivers/input/touchscreen.c:65-76 */
void touchscreen_parse_properties(struct input_dev *input, bool multitouch,
				  struct touchscreen_properties *prop)
{
	struct device *dev = input->dev.parent;
	…
	input_alloc_absinfo(input);
	if (!input->absinfo)
		return;
```

两点都容易踩：

★ **`dev = input->dev.parent`（`:68`）**——属性从**父设备**（通常是 I2C client 或 SPI device）的 fwnode 上读，`input_dev` 自己那个 `struct device` 上没有 DT 节点。所以调用前 `dev.parent` 必须已经赋好；`devm_input_allocate_device()` 会替你赋（`03-internals.md` A2），手工 `input_allocate_device()` 的驱动得自己填。**函数里没有 NULL 检查。**

★ **`input_alloc_absinfo()` 失败就静默返回（`:75-76`）**——不报错、不打日志，调用者也拿不到返回值（函数是 `void`）。表现是"DT 里写的属性全被忽略"。

### §10.2 十一条属性，两级默认值

| DT 属性 | 落到 | 默认值（属性缺失时） | 行 |
|---|---|---|---|
| `touchscreen-min-x` | X 轴 `minimum` | 当前 `minimum` | `:81-83` |
| `touchscreen-size-x` | X 轴 `maximum` | 当前 `maximum + 1` | `:84-87` |
| `touchscreen-fuzz-x` | X 轴 `fuzz` | 当前 `fuzz` | `:88-90` |
| `touchscreen-min-y` | Y 轴 `minimum` | 当前 `minimum` | `:94-96` |
| `touchscreen-size-y` | Y 轴 `maximum` | 当前 `maximum + 1` | `:97-100` |
| `touchscreen-fuzz-y` | Y 轴 `fuzz` | 当前 `fuzz` | `:101-103` |
| `touchscreen-max-pressure` | 压力轴 `maximum` | 当前 `maximum` | `:108-111` |
| `touchscreen-fuzz-pressure` | 压力轴 `fuzz` | 当前 `fuzz` | `:112-115` |
| `touchscreen-inverted-x` | `prop->invert_x` + 平移 X 轴 | false | `:125-131` |
| `touchscreen-inverted-y` | `prop->invert_y` + 平移 Y 轴 | false | `:133-139` |
| `touchscreen-swapped-x-y` | `prop->swap_x_y` + 交换两条 `absinfo` | false | `:141-144` |

`multitouch` 参数决定轴号：真则 `ABS_MT_POSITION_X`/`_Y`/`ABS_MT_PRESSURE`，假则 `ABS_X`/`ABS_Y`/`ABS_PRESSURE`（`:78-79`、`:107`）。**同一份 DT 属性两种解释**，一个驱动可以先 `multitouch=true` 调一次、再 `false` 调一次，把同样的范围同时装到 MT 轴和单点轴上。

★ **`-size-` 不是 `-max-`，差 1**。`touchscreen_get_prop_u32()` 拿到的是"这条轴上有多少个刻度"，`:92` 和 `:105` 写进去的是 `maximum - 1`：

```c
	if (data_present)
		touchscreen_set_params(input, axis_x, minimum, maximum - 1, fuzz);
```

DT 里写 `touchscreen-size-x = <1024>`，`absinfo->maximum` 得到的是 **1023**。压力那一路没有这个减 1（`:117` 直接传 `maximum`），因为属性名是 `-max-pressure` 而不是 `-size-`。**同一个函数里两种口径**，看属性名前缀区分。

★ 三次调用之间用的是 **`data_present |=`**（`:84`、`:88` 等），按位或不是逻辑短路——三条属性里**任意一条**存在，`touchscreen_set_params()` 就会被调，另外两个位置写进去的是"当前值"。所以只写 `touchscreen-fuzz-x` 也是安全的：min/max 会被原值覆盖一遍。

### §10.3 轴没建好时的那条警告

```c
/* drivers/input/touchscreen.c:33-50 */
static void touchscreen_set_params(struct input_dev *dev,
				   unsigned long axis,
				   int min, int max, int fuzz)
{
	struct input_absinfo *absinfo;

	if (!test_bit(axis, dev->absbit)) {
		dev_warn(&dev->dev,
			 "Parameters are specified but the axis %lu is not set up\n",
			 axis);
		return;
	}
	…
```

这是本框架唯一的诊断输出。它意味着：DT 里写了这条轴的属性，但驱动没有 `input_set_abs_params()` / `input_mt_init_slots()` 声明过这条轴。**属性被丢掉，设备照样注册成功**——`07-debug.md` 会把这条串收进错误消息表。轴号是按 `%lu` 打十进制的，所以 `ABS_MT_POSITION_X`（0x35）显示成 `53`，`ABS_X`（0x00）显示成 `0`。

### §10.4 invert 是"平移轴"，不是"报告时取反"

`inverted-x` 这条属性同时做了两件事：

```c
/* drivers/input/touchscreen.c:122-131 */
	prop->max_x = input_abs_get_max(input, axis_x);
	prop->max_y = input_abs_get_max(input, axis_y);

	prop->invert_x =
		device_property_read_bool(dev, "touchscreen-inverted-x");
	if (prop->invert_x) {
		absinfo = &input->absinfo[axis_x];
		absinfo->maximum -= absinfo->minimum;
		absinfo->minimum = 0;
	}
```

- 一是记下 `prop->invert_x`，供报告时用
- 二是**把轴平移到从 0 开始**：`maximum -= minimum; minimum = 0`

★ **`prop->max_x` 是在平移之前读的（`:122`），这不是疏忽，是必须的。** 报告路径 `touchscreen_apply_prop_to_x_y()`（`:148-160`）算的是 `*x = prop->max_x - *x`——传进来的 `x` 是**硬件原始坐标**，量程还是 `[min, max]`；用平移前的 `max` 去减，结果正好落在 `[0, max-min]`，也就是平移后的新量程里。若这里改用平移后的 `maximum`，min 非零的屏会整体偏 `min` 个单位。

`swap_x_y` 走的是 `swap(input->absinfo[axis_x], input->absinfo[axis_y])`（`:144`）——**整个 `struct input_absinfo` 对换**，min/max/fuzz/flat/resolution 全跟着走。同样地 `prop->max_x`/`max_y` 在 `:122-123` 就读完了，不受这次对换影响。

`touchscreen_apply_prop_to_x_y()` 里三步的次序是**先 invert 后 swap**（`:152-159`），而不是反过来。这个次序和 `parse_properties()` 里对 `absinfo` 动手的次序（先平移、后对换）是一致的，两边必须对齐才不会错位。

### §10.5 报告面：两个函数，都只是包装

| 函数 | 位置 | 做什么 | 导出 |
|---|---|---|---|
| `touchscreen_set_mt_pos()` | `:173-180` | 转换后写进 `struct input_mt_pos`，给 §7 的 `input_mt_assign_slots()` 用 | `EXPORT_SYMBOL`（`:181`） |
| `touchscreen_report_pos()` | `:195-203` | 转换后直接 `input_report_abs()` 两次 | `EXPORT_SYMBOL`（`:204`） |

两个都是三行函数，共用 `touchscreen_apply_prop_to_x_y()` 这个 `static` 转换器。★ **三个导出符号全部是不带 `_GPL` 的 `EXPORT_SYMBOL`**（`:146`、`:181`、`:204`），和 §5 rmi4 那种混用不同——这里是齐整的。

**全树使用统计**（口径与篇末表二一致：`grep -rlE --include=*.c '\b<函数名>\(' . | wc -l` 数文件、把 `-rlE` 换成 `-rnE` 数行；含定义行，不含 `EXPORT_SYMBOL(foo)` 那行——那里 `foo` 后面跟的是 `)`——但**含**注释和 kerneldoc 里写成 `foo()` 的行，逐符号的偏差见篇末表二）：

| 函数 | 文件数 | 命中行数 |
|---|---|---|
| `touchscreen_parse_properties` | 36 | 38 |
| `touchscreen_report_pos` | 26 | 30 |
| `touchscreen_set_mt_pos` | 5 | 6 |

**arm64 相关性**：相关，而且是 DT 平台上触摸屏驱动的标准动作——36 个文件用了 `touchscreen_parse_properties()`，是本篇统计的十五个符号里覆盖面第五大的（见篇末表）。`touchscreen_set_mt_pos()` 只有 5 个文件用，因为它只服务于"自己不给 tracking ID、要靠核心算槽位"的那类硬件（§7.2 那张标志位表里的 `INPUT_MT_TRACK`）。

---

## §11 三个 keymap 库：稀疏、矩阵、功能行

三个库放同一节，是因为它们回答的是同一个问题的三种形态——**硬件给的编码怎么变成 `KEY_*`**。三者之间**没有任何调用关系**，Kconfig 也各自独立，可以任意组合。

| 库 | 文件 | 行数 | Kconfig | 核心数据 | 挂点 |
|---|---|---|---|---|---|
| sparse-keymap | `drivers/input/sparse-keymap.c` + `include/linux/input/sparse-keymap.h` | 294 + 59 | `INPUT_SPARSEKMAP`（`drivers/input/Kconfig:54`） | `struct key_entry[]`，`KE_END` 结尾 | A3 |
| matrix-keymap | `drivers/input/matrix-keymap.c` + `include/linux/input/matrix_keypad.h` | 202 + 92 | `INPUT_MATRIXKMAP`（`drivers/input/Kconfig:67`） | `unsigned short keymap[rows << row_shift]` | A3 |
| vivaldi-fmap | `drivers/input/vivaldi-fmap.c` + `include/linux/input/vivaldi-fmap.h` | 39 + 27 | `INPUT_VIVALDIFMAP`（`drivers/input/Kconfig:80`） | `struct vivaldi_data`（消费者内嵌） | A4 之后 |

前两个都接管了 `input_dev->keycode` 这个字段，**所以同一个设备只能选一个**。第三个完全不碰 `input_dev`。

### §11.1 sparse-keymap：编码稀疏，所以查表用线性扫

「稀疏」指的是硬件给的 scancode 不连续——ACPI 热键动辄给 `0x8a`、`0xe045` 这类跳跃值，开个数组当下标会浪费几十 KB。所以这里用的是**一个变长条目数组 + 线性查找**。

```c
/* include/linux/input/sparse-keymap.h:33-43 */
struct key_entry {
	int type;		/* See KE_* above */
	u32 code;
	union {
		u16 keycode;		/* For KE_KEY */
		struct {		/* For KE_SW, KE_VSW */
			u8 code;
			u8 value;	/* For KE_SW, ignored by KE_VSW */
		} sw;
	};
};
```

★ `keycode` 和 `sw` 是 **`union`**——填错了不会有编译错误，只会安静地把 `KEY_*` 码的低 8 位当成 `SW_*` 码。类型标签有五个（`sparse-keymap.h:9-14`）：

| 标签 | 值 | `sparse_keymap_setup()` 里的处理 |
|---|---|---|
| `KE_END` | 0 | 数组终止符，循环边界（`:176`、`:28`、`:45`） |
| `KE_KEY` | 1 | 置 `EV_KEY` + `keybit[keycode]`（`:194-197`） |
| `KE_SW` | 2 | 置 `EV_SW` + `swbit[sw.code]`（`:199-203`），报告时用表里的 `sw.value` |
| `KE_VSW` | 3 | 同上，但报告时用调用者传进来的 `value`（`:252-253`） |
| `KE_IGNORE` | 4 | **`switch` 里没有 case** |

★ `grep -n 'KE_IGNORE' drivers/input/sparse-keymap.c` **无输出**——这个标签本库一个字都没写。它纯粹是给驱动自己用的约定："这个 scancode 我认识，但我故意不上报"。驱动查到 `KE_IGNORE` 条目后自行 `return`，本库对它的唯一贡献是"不会把它错当成按键"。全树 `grep -rnw --include=*.c 'KE_IGNORE' . | wc -l` → **128** 行在用这个约定。

`sparse_keymap_setup()`（`:166-219`）五步：

| 步 | 行 | 做什么 |
|---|---|---|
| 1 | `:170-177` | 先扫一遍数到 `KE_END`，得出 `map_size`（**含**终止符那一项） |
| 2 | `:179-182` | `devm_kmemdup()` **复制一份**到 `&dev->dev` 名下 |
| 3 | `:184-205` | 逐项跑可选的 `setup()` 回调，再按类型置 evbit/keybit/swbit |
| 4 | `:207-211` | 有 `EV_KEY` 就追加 `KEY_UNKNOWN` + `EV_MSC` + `MSC_SCAN` |
| 5 | `:213-216` | 接管 `dev->keycode` / `keycodemax` / `getkeycode` / `setkeycode` |

★ **为什么要复制？** 因为 `sparse_keymap_setkeycode()`（`:132-151`）会**原地改 `key->keycode`**——用户态 `EVIOCSKEYCODE` 允许重映射。驱动传进来的那份 keymap 通常是 `static const`，改不得。复制到 devm 内存里，设备解绑时自动释放。

★ **第 4 步的三个附加位是给"表里没有"的编码兜底的**：`sparse_keymap_report_event()` 查不到时不是什么都不做——

```c
/* drivers/input/sparse-keymap.c:285-291 */
	/* Report an unknown key event as a debugging aid */
	unknown_ke.type = KE_KEY;
	unknown_ke.code = code;
	unknown_ke.keycode = KEY_UNKNOWN;
	sparse_keymap_report_entry(dev, &unknown_ke, value, true);

	return false;
```

它**伪造一条 `KEY_UNKNOWN` 条目并强制 `autorelease = true`** 报出去，然后才返回 `false`。所以「返回 false」的含义是"表里没这个码"，**不是"什么都没上报"**——用户态照样会收到一对 `MSC_SCAN` + `KEY_UNKNOWN` 按下/抬起。这正是排查"新机型上某个热键没反应"时该看的东西：`evtest` 里出现 `KEY_UNKNOWN` 就说明中断到了、只是查表没命中，把 `MSC_SCAN` 的值填进驱动的 keymap 即可。

★ `sparse_keymap_report_entry()` 里 **`KE_KEY` 一路会调一到两次 `input_sync()`**（`:241` 固定一次，`:244` 只在 `value && autorelease` 时追加）——`autorelease` 时按下和抬起各自成帧。这和 §1 evdev 的攒帧逻辑配合得刚好：两帧之间用户态能看到两次 `EV_SYN`。

★ **只设了 `keycodemax`，没设 `keycodesize`**（`:214`，`grep -n 'keycodesize' drivers/input/sparse-keymap.c` 无输出）。`keycodemax` 这里的含义还被改了口径——它是**条目总数（含 `KE_END`）**，不是"最大键码"。之所以不出事，是因为第 5 步同时把 `getkeycode`/`setkeycode` 换成了自己的实现，核心那两个默认实现（会用到 `keycodesize` 的）根本没机会跑。**这是一个"必须成套替换"的耦合**：谁把这两个回调再改回默认的，就会按 `keycodesize == 0` 去解释 keymap。

`setkeycode` 里还有一处 sparse 独有的清理：

```c
/* drivers/input/sparse-keymap.c:141-145 */
			*old_keycode = key->keycode;
			key->keycode = ke->keycode;
			set_bit(ke->keycode, dev->keybit);
			if (!sparse_keymap_entry_from_keycode(dev, *old_keycode))
				clear_bit(*old_keycode, dev->keybit);
```

改完映射后**回头查一遍旧键码还有没有别的条目在用**，没有就把 `keybit` 里那一位清掉。matrix-keymap 没有这个动作——它的 `keybit` 只会越来越满。

**arm64 相关性：能编，但树里一个消费者都落不到 arm64 上。** 24 个文件命中 `sparse_keymap_setup(`，去掉定义文件是 23 个消费者：

| 位置 | 文件数 | 架构 |
|---|---|---|
| `drivers/platform/x86/`（含 `dell/`、`intel/`、`hp/` 子目录） | 20 | x86 |
| `drivers/platform/loongarch/` | 1 | LoongArch |
| `drivers/input/misc/wistron_btns.c` | 1 | `depends on X86_32 && !UML`（`drivers/input/misc/Kconfig:333`） |
| `drivers/input/misc/dm355evm_keys.c` | 1 | `depends on MFD_DM355EVM_MSP`（`drivers/input/misc/Kconfig:667`），TI DaVinci 是 arm32 |

重跑：`grep -rlE --include=*.c '\bsparse_keymap_setup\(' . | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn`

### §11.2 matrix-keymap：位域打包，而且打包和解包不对称

矩阵键盘的编码是 (行, 列) 二元组。平台代码用 `KEY()` 宏把三个数塞进一个 `u32`：

```c
/* include/linux/input/matrix_keypad.h:9-20 */
#define MATRIX_MAX_ROWS		32
#define MATRIX_MAX_COLS		32

#define KEY(row, col, val)	((((row) & (MATRIX_MAX_ROWS - 1)) << 24) |\
				 (((col) & (MATRIX_MAX_COLS - 1)) << 16) |\
				 ((val) & 0xffff))

#define KEY_ROW(k)		(((k) >> 24) & 0xff)
#define KEY_COL(k)		(((k) >> 16) & 0xff)
#define KEY_VAL(k)		((k) & 0xffff)

#define MATRIX_SCAN_CODE(row, col, row_shift)	(((row) << (row_shift)) + (col))
```

★ **打包时掩 5 位（`& 31`），解包时取 8 位（`& 0xff`）。** 两边不对称。后果是 `KEY(32, 0, KEY_A)` 会被**静默截断**成 `KEY(0, 0, KEY_A)`——没有警告，只是那个键跑到了第 0 行。而 `matrix_keypad_map_key()` 的越界检查（`:31-36`）拿到的已经是截断后的行号，检查不出来。DT 那条路走的是 `linux,keymap` 里的原始 `u32`，**没有经过 `KEY()` 宏，所以能被检查到**——两条来源的容错程度不一样。

★ `MATRIX_SCAN_CODE(row, col, row_shift)` 用的是 **`+ col` 而不是 `| col`**。只有当 `row_shift = get_count_order(cols)`（`:156`、`:74`）保证 `col < (1 << row_shift)` 时两者才等价。这个前提由本库自己维护，驱动如果自己算 scancode 传别的 `row_shift`，加法会把行列串到一起。

`matrix_keypad_build_keymap()`（`:150-199`）的七步：

| 步 | 行 | 说明 |
|---|---|---|
| 1 | `:156-157` | `row_shift = get_count_order(cols)`，`max_keys = rows << row_shift` |
| 2 | `:161-162` | `WARN_ON(!input_dev->dev.parent)` → `-EINVAL`。kerneldoc `:147-148` 明写了这条前提 |
| 3 | `:164-173` | `keymap == NULL` 就 `devm_kcalloc()` 自动分配 |
| 4 | `:175-177` | 接管 `keycode` / `keycodesize` / `keycodemax` |
| 5 | `:179` | 置 `EV_KEY` |
| 6 | `:181-194` | 两条来源二选一：`keymap_data`（平台代码）或 `matrix_keypad_parse_keymap()`（DT） |
| 7 | `:196` | `__clear_bit(KEY_RESERVED, input_dev->keybit)` |

★ **第 3 步的 devm 挂在 `input_dev->dev.parent` 上，不是 `input_dev` 自己**（`:165`）。所以这块 keymap 的寿命跟着**父设备解绑**走，而不是跟着 `input_dev` 的引用计数走。第 2 步那个 `WARN_ON` 就是为它把关的——没有父设备，devm 无处可挂。

★ **第 7 步是为第 3 步善后的**。自动分配的 keymap 全是 0，而 `KEY_RESERVED` 恰好就是 0（`include/uapi/linux/input-event-codes.h:75`）。第 6 步逐个 `__set_bit(code, keybit)` 时，那些没被填过的空槽虽然不会置位，但**被填过的表项里也可能有驱动故意写的 `KEY_RESERVED`**——所以统一在最后清一次。对比 §11.1 的 sparse-keymap：它没有"空槽"概念，也就不需要这一步。

`matrix_keypad_parse_keymap()`（`:69-121`）是 DT 那条路，三条错误消息各对一种毛病：

| 消息 | 行 | 触发 |
|---|---|---|
| `"missing or malformed property %s: %d\n"` | `:86-87` | `device_property_count_u32()` ≤ 0，即属性不存在或长度非法；`size < 0` 时原样返回该错误码，`size == 0` 时返回 `-EINVAL`（`:88`） |
| `"%s size overflow (%d vs max %u)\n"` | `:92-93` | 条目数超过 `rows << row_shift` |
| `"failed to read %s property: %d\n"` | `:103-104` | 计数成功但读取失败 |

属性名默认 `"linux,keymap"`（`:81-82`），调用者可以传别的名字覆盖。★ 这个函数用 `kmalloc_array()` 而不是 devm（`:97`），因为它只是个临时缓冲，用完 `kfree()`（`:119`）——真正的 keymap 是第 3 步那块。

`matrix_keypad_parse_properties()`（`:52-66`）读另外两条属性 `keypad,num-rows` / `keypad,num-columns`，任一缺失就 `dev_err(dev, "number of keypad rows/columns not specified\n")` + `-EINVAL`。★ **注意 `*rows = *cols = 0;`（`:55`）在前，两次 `device_property_read_u32()` 的返回值都被丢掉**——判据完全落在"读完之后还是不是 0"上。所以 DT 里写 `keypad,num-rows = <0>` 和不写这条属性，报的是同一个错。

★ **同一个文件里两种导出宏**：`matrix_keypad_parse_properties()` 是 `EXPORT_SYMBOL_GPL`（`:67`），`matrix_keypad_build_keymap()` 是不带 `_GPL` 的 `EXPORT_SYMBOL`（`:200`）。和 §5 rmi4 那处一样是分两代加进来的：带 `_GPL` 的那个随 2016-11-11 的提交 `Input: matrix-keypad - switch to using generic device properties` 一起引入，`build_keymap()` 比它老。重跑：`git log --oneline -1 --format='%ad %s' --date=short -S'EXPORT_SYMBOL_GPL(matrix_keypad_parse_properties)' -- drivers/input/matrix-keymap.c`。

**arm64 相关性**：相关，是 DT 平台矩阵键盘的标准路径。28 个文件命中 `matrix_keypad_build_keymap(`，其中 26 个在 `drivers/input/keyboard/`、1 个在 `drivers/auxdisplay/`、1 个是定义文件本身。三条 DT 属性的写法交给 `06-devicetree.md`。

### §11.3 vivaldi-fmap：全库只有一个函数，而且不碰 input

```c
/* drivers/input/vivaldi-fmap.c:19-36 —— 全库唯一的函数 */
ssize_t vivaldi_function_row_physmap_show(const struct vivaldi_data *data,
					  char *buf)
{
	ssize_t size = 0;
	int i;
	const u32 *physmap = data->function_row_physmap;

	if (!data->num_function_row_keys)
		return 0;

	for (i = 0; i < data->num_function_row_keys; i++)
		size += scnprintf(buf + size, PAGE_SIZE - size,
				  "%s%02X", size ? " " : "", physmap[i]);
	if (size)
		size += scnprintf(buf + size, PAGE_SIZE - size, "\n");

	return size;
}
```

ChromeOS 的 Vivaldi 键盘把顶排功能键做成可配置的：厂商决定第 1 到第 N 个位置各是什么功能，用户态（ChromeOS 的输入法/快捷键层）需要知道这个次序才能画对提示。内核这边只负责把驱动存下来的 `u32` 数组打成 `"3A 3B 3C\n"` 这样的字符串。

| 事实 | 依据 |
|---|---|
| 上限 24 个键 | `VIVALDI_MAX_FUNCTION_ROW_KEYS`（`include/linux/input/vivaldi-fmap.h:7`），`function_row_physmap` 是定长数组（`:20`） |
| 空表返回 0 字节 | `:26-27`，sysfs 读出来是空文件而不是报错 |
| 用 `scnprintf` 而非 `snprintf` | `:30`、`:33`，返回值是**实际写入量**，累加 `size` 才不会越界 |
| 只在 `size` 非零时补换行 | `:32-33` |

★ **这个库不做任何翻译，也不注册任何东西**——它没有 `input_dev` 参数，`struct vivaldi_data` 由消费者自己内嵌、自己填。之所以住在 `drivers/input/` 而不是某个驱动里，是因为四个消费者**横跨两个子系统**：

| 消费者 | 子系统 | 调用点 |
|---|---|---|
| `drivers/hid/hid-vivaldi-common.c` | HID | `:110` |
| `drivers/input/keyboard/atkbd.c` | input | `:309` |
| `drivers/input/keyboard/cros_ec_keyb.c` | input | `:650` |
| （定义） | — | `drivers/input/vivaldi-fmap.c:19` |

`struct vivaldi_data` 的使用面更宽一点——`grep -rlE --include=*.c 'struct vivaldi_data' .` 命中 6 个文件，多出来的两个是 `drivers/hid/hid-vivaldi.c` 和 `drivers/hid/hid-google-hammer.c`，它们内嵌了这个结构、但把打印工作交给 `hid-vivaldi-common.c` 那一处。

**挂点**：A4 之后。它是一个 sysfs `show` 函数的实现体，只在用户态读属性时才跑，和设备注册流程无关。

**arm64 相关性**：相关。`cros_ec_keyb` 是 arm64 Chromebook 的键盘驱动，`hid-google-hammer` 是它们的可拆卸键盘底座。`atkbd` 那一路是 x86。

---

## §12 gameport：一条只剩历史的总线

**挂点**：**在 A2 之前**。和 §2 的 serio 一样，gameport 层完全不碰 `input_dev`——它交付的是「一个能读的 15 针游戏端口」。拿到端口的摇杆驱动（`drivers/input/joystick/`）才自己走 A2→A3→A4 去建 `input_dev`。所以本节里不会出现任何 `input_*` 函数。

★ 官方原文见 `Documentation/input/gameport-programming.rst`（233 行）——它是**写 gameport 端口驱动的教程**，讲 `gameport_register_port()` 前该填哪些字段、`cooked`/`raw` 两种读法、以及速度测量的用途。本节的组织方式不同（先摆五个文件、再讲总线与队列），两边可以互补；下面凡是与那篇有出入的地方以代码为准。

### §12.1 五个文件：一个核心 + 四个端口

```
drivers/input/gameport/
├── gameport.c      855 行   核心：bus_type、事件队列、sysfs、速度测量
├── lightning.c     322 行   PDPI Lightning 4 游戏卡（固定 I/O 口 0x201）
├── ns558.c         267 行   标准 ISA / PnP 游戏端口
├── fm801-gp.c      144 行   ForteMedia FM801 声卡上的游戏端口（PCI）
└── emu10k1-gp.c    108 行   Creative SB Live! 系列声卡上的游戏端口（PCI）
                    ────
                   1696 行
```

```bash
wc -l drivers/input/gameport/*.c
```

Kconfig 五项（`drivers/input/gameport/Kconfig`），后四项包在 `if GAMEPORT`（`:24`）… `endif`（`:65`）里，即 `GAMEPORT` 不选中时四个端口驱动整段不可见：

| 符号 | 行 | 依赖 | arm64 |
| --- | --- | --- | --- |
| `GAMEPORT` | `:5-7` | `depends on !UML` | 能编 |
| `GAMEPORT_NS558` | `:26` | 无 | 能编 |
| `GAMEPORT_L4` | `:36` | 无 | 能编 |
| `GAMEPORT_EMU10K1` | `:44` | `depends on PCI` | 能编 |
| `GAMEPORT_FM801` | `:54` | `depends on PCI` | 能编 |

一个 `depends on ARCH_*` 都没有——**arm64 上这五项全都可以选中编译**。这不等于能用，理由见 §12.5。

驱动侧的消费者是 12 个摇杆驱动，全在同一个目录下：

```bash
grep -rn --include=*.c 'struct gameport_driver [a-z_0-9]* = {' .
```

12 个命中，`drivers/input/joystick/` 下 12 个文件各一个（`a3d.c` `adi.c` `analog.c` `cobra.c` `gf2k.c` `grip.c` `grip_mp.c` `guillemot.c` `interact.c` `joydump.c` `sidewinder.c` `tmdc.c`）。对应地，`drivers/input/joystick/Kconfig` 里有 12 处 `select GAMEPORT`（`:21` `:38` `:59` `:70` `:79` `:89` `:99` `:109` `:119` `:129` `:139` `:285`）——即「打开任一个 gameport 摇杆驱动就自动拉进 gameport 核心」。

### §12.2 ★ 这条总线的 `match` 恒为真

`drivers/input/gameport/gameport.c:786-793` 是一个完整的 `struct bus_type`：

```c
static struct bus_type gameport_bus = {
	.name		= "gameport",
	.dev_groups	= gameport_device_groups,
	.drv_groups	= gameport_driver_groups,
	.match		= gameport_bus_match,
	.probe		= gameport_driver_probe,
	.remove		= gameport_driver_remove,
};
```

它是 `static` 的，`grep -rn 'gameport_bus' .` 只在本文件内命中——**外部拿不到这条总线的句柄**。注册发生在 `gameport_init()` `:829-841`，用的是 **`subsys_initcall(gameport_init)`** `:854`（对比 §5 rmi4 的 `module_init`）。

`match` 回调只有三行（`:779-784`）：

```c
static int gameport_bus_match(struct device *dev, struct device_driver *drv)
{
	struct gameport_driver *gameport_drv = to_gameport_driver(drv);

	return !gameport_drv->ignore;
}
```

★ 没有 id 表，没有任何设备与驱动的比对。只要驱动没设 `ignore`，它就匹配**每一个**已注册的端口。真正的筛选被推迟到 probe（`:691-698`）：

```c
static int gameport_driver_probe(struct device *dev)
{
	struct gameport *gameport = to_gameport_port(dev);
	struct gameport_driver *drv = to_gameport_driver(dev->driver);

	drv->connect(gameport, drv);
	return gameport->drv ? 0 : -ENODEV;
}
```

★ 两点都反直觉：

1. **`drv->connect()` 的返回值被丢掉了**（`:696` 没有赋给任何变量）。
2. 成功判据是 `gameport->drv` 有没有被设上。而这个字段唯一的写入者是 `gameport_set_drv()` `:795-800`，它只被 `gameport_open()` `:813` 和 `gameport_close()` `:823` 调用。**所以「驱动接管了这个端口」的真实信号是「它在 connect 里成功 open 过、并且没有再 close」**——不是它返回了 0。

这套设计的直接后果：探测一根 15 针端口上挂的到底是什么摇杆，是靠 12 个驱动**依次全试一遍**，谁的 `connect()` 认出了信号谁留下。gameport 时代的手柄本来就没有身份寄存器，只能靠时序特征猜——总线层于是干脆不做匹配。

### §12.3 ★ `ignore` 一个字段两种用途

`ignore` 是 `struct gameport_driver` 的最后一个字段（`include/linux/gameport.h:60`），配合上面那个恒真的 `match`，它被当成**一个全局使能位**在用，而且用在两个毫不相干的场景里：

| 场景 | 位置 | 置位含义 |
| --- | --- | --- |
| 注册期间临时屏蔽 | `:731` 置 true → `driver_register()` `:733` → `:743` 清 false | 让 `driver_register()` 内部的自动绑定扫描一无所获，把探测挪到工作队列 |
| 注销期间永久屏蔽 | `:760` 置 true，注释写着 `/* so gameport_find_driver ignores it */` | 防止拆除过程中又被重新绑上 |

第一种用法的理由写在源码注释里（`:727-730`）：

```c
	/*
	 * Temporarily disable automatic binding because probing
	 * takes long time and we are better off doing it in kgameportd
	 */
	drv->ignore = true;
```

清掉 `ignore` 之后（`:743`）立刻排一个 `GAMEPORT_ATTACH_DRIVER` 事件（`:744`），由工作队列去做实际绑定；排队失败就把驱动 `driver_unregister()` 回滚（`:745-748`）。事件类型只有两种（`:254-257`）：

```c
enum gameport_event_type {
	GAMEPORT_REGISTER_PORT,
	GAMEPORT_ATTACH_DRIVER,
};
```

队列的其余部件：`gameport_event_lock` `:266` / `gameport_event_list` `:267` / `gameport_get_event()` `:269` / `gameport_free_event()` `:286`（里面 `module_put()` `:288`）/ `gameport_remove_duplicate_events()` `:292` / `gameport_handle_events()` `:318` / `DECLARE_WORK(gameport_event_work, …)` `:350` / `gameport_queue_event()` `:352`（里面 `try_module_get()` `:383`）/ `gameport_remove_pending_events()` `:407` / `gameport_get_pending_child()` `:432`。形状和 §2.3 的 serio 事件队列同源，只是事件种类少得多——serio 那边有热插拔子树重连（`SERIO_RECONNECT_SUBTREE`），gameport 没有子树可言。

### §12.4 ★ 一个函数四份实现，arm64 走的是忙等

端口注册时要测一次 I/O 速度，`gameport_add_port()` `:555-557`：

```c
	gameport->speed = use_ktime ?
		gameport_measure_speed(gameport) :
		old_gameport_measure_speed(gameport);
```

`use_ktime` 是模块参数（`:30` 声明并初始化为 `true`，`:31` 是 `module_param(use_ktime, bool, 0400)`，`:32` 是 `MODULE_PARM_DESC`）。两个被调函数一共给出四条代码路径：

| 实现 | 位置 | 生效条件 | 计时源 |
| --- | --- | --- | --- |
| `gameport_measure_speed()` | `:78-108` | `use_ktime` 为真（默认） | `ktime_get_ns()`，体系结构无关 |
| `old_…` i386 分支 | `:112-134` | `use_ktime=0` 且 `#if defined(__i386__)` | 8253 PIT，`get_time_pit()` `:56-68` |
| `old_…` x86_64 分支 | `:136-158` | `use_ktime=0` 且 `#elif defined (__x86_64__)` | `rdtsc()` |
| `old_…` 通用分支 | `:160-171` | `use_ktime=0` 且 `#else` ← **arm64 落这条** | `jiffies` 忙等 |

arm64 上编出来的 `old_gameport_measure_speed()` 只剩这段（`:162-171`）：

```c
	unsigned int j, t = 0;

	if (gameport_open(gameport, NULL, GAMEPORT_MODE_RAW))
		return 0;

	j = jiffies; while (j == jiffies);
	j = jiffies; while (j == jiffies) { t++; gameport_read(gameport); }

	gameport_close(gameport);
	return t * HZ / 1000;
```

★ 三点值得记：

- 先空转到一个 tick 边界，再在下一个 tick 里数 `gameport_read()` 做了多少次。**这条分支没有 `local_irq_save()`**——默认路径 `:90`/`:96` 有，PIT 分支和 rdtsc 分支也有，只有通用分支裸着忙等，中断打进来就把测量结果压低。既然默认 `use_ktime=1`，这段实际上是死代码，除非有人手动 `gameport.use_ktime=0`。
- `get_time_pit()` 和它引用的 `i8253_lock` 整段被 `#if defined(__i386__)` `:49` 包住，连 x86_64 都编不进去。这是 §99 里 arm64 裁剪清单的一条。
- 这两个测速函数都用 `gameport_open(gameport, NULL, GAMEPORT_MODE_RAW)` 打开端口——**`drv` 传 `NULL` 是合法的**，见 `gameport_open()` `:802-815`：没有 `->open` 回调时只要 mode 是 RAW 就成功（`:808-811`），然后无条件 `gameport_set_drv(gameport, drv)`，此处即写入 `NULL`。★ 而且这次 open 发生在 `device_add()` `:568` **之前**——端口还没上总线，speed 就已经测完并打进了 `dev_info()` 那行日志（`:562-566`）。

★ `gameport_open()` 的失败返回值是**裸 `-1`**（`:806`、`:810`），不是错误码。调用者只能判真假，拿不到原因。`__gameport_register_port()` / `gameport_unregister_port()`（`:654`/`:664`）和 `gameport_open` / `gameport_close` 一样都是 `EXPORT_SYMBOL`（`:659`/`:671`/`:816`/`:827`），没有一个是 `_GPL`。

### §12.5 arm64 分诊：能编，但拿不到设备

前面说了五个 Kconfig 项在 arm64 上都能选中。真正的障碍在读写指令本身。`include/linux/gameport.h` 里三个内联访问器把端口 I/O 写死成了 x86 风格的端口指令：

```c
static inline unsigned char gameport_read(struct gameport *gameport)
{
	if (gameport->read)
		return gameport->read(gameport);
	else
		return inb(gameport->io);
}
```

（`:174-180`；`gameport_trigger()` `:166-172` 同形，回退到 `outb(0xff, gameport->io)` `:171`。回调存在时走回调，所以这层本身不是硬阻塞。）

四个端口驱动分两类：

| 驱动 | 端口来源 | arm64 现实 |
| --- | --- | --- |
| `ns558.c` | 写死的 ISA 口表 `ns558_isa_portlist[]`（`:25-26`，15 个口：0x201 0x200 0x202 0x203 0x204 0x205 0x207 0x209 0x20b 0x20c 0x20e 0x20f 0x211 0x219 0x101，以 0 结尾）+ PnP 枚举 | arm64 上没有 ISA 总线，PnP 也不存在，探测必然一无所获 |
| `lightning.c` | 单个写死的口 `#define L4_PORT 0x201`（`:19`），直接 `inb`/`outb` 猜卡 | 同上 |
| `emu10k1-gp.c` | PCI BAR（Creative 声卡） | 能 probe，但要通过 `inb`/`outb` 读 I/O BAR |
| `fm801-gp.c` | PCI BAR（ForteMedia 声卡） | 同上 |

```bash
grep -ln 'inb\|outb' drivers/input/gameport/*.c
```

四个命中：`gameport.c`、`lightning.c`、`ns558.c`、`fm801-gp.c`。

**结论**：整个 gameport 层在 arm64 上「名义上可编、实际拿不到设备」——前两个驱动探的是 x86 遗留的固定 ISA 口，后两个虽然是正常 PCI 设备，但要靠端口 I/O 空间访问上世纪的声卡。**没有任何 DT 绑定**（`grep -rli gameport Documentation/devicetree/bindings/ \| wc -l` → 0），也就没有任何路径能在 arm64 平台上把一根游戏端口描述出来。本节按「读源码时会撞见」的标准写，不按「你会用到」的标准写。

---

## §13 只消费不建设备的三个 handler

**挂点**：**A5→A6→A7**，三个都是标准 handler。它们和 §1 那三个字符设备 handler 走完全相同的路（匹配 → `input_register_handle()` → `input_open_device()`），区别在于**它们不建任何设备节点**：一个往内核日志打字，一个往电源管理框架转发，一个往 ALSA 控件写状态。放在一节里是因为它们的骨架短到可以整段读完——想照抄一个 handler，看这一节。

### §13.1 evbug：匹配一切的调试 handler

100 行，`INPUT_EVBUG` 在 `drivers/input/Kconfig:155`，是 tristate。★ 它的 help 文本自己写了这是安全问题（`:161`）：`a security threat - your keypresses include your passwords`。

匹配面（`drivers/input/evbug.c:74-77`）：

```c
static const struct input_device_id evbug_ids[] = {
	{ .driver_info = 1 },	/* Matches all devices */
	{ },			/* Terminating zero entry */
};
```

★ 两个条目长得几乎一样，靠 `.driver_info = 1` 区分：第一项必须**非全零**才不会被当成终止项。`.flags` 是 0、十个位图全空，按 §9.1 分析过的 `input_match_device_id()` 逻辑（四个字段被 flags 门控所以全跳过，十个 `bitmap_subset()` 拿空集去比所以全通过），结果就是注释说的 `Matches all devices`。

事件回调是全篇最短的一个（`:22-26`）：

```c
static void evbug_event(struct input_handle *handle, unsigned int type, unsigned int code, int value)
{
	printk(KERN_DEBUG pr_fmt("Event. Dev: %s, Type: %d, Code: %d, Value: %d\n"),
	       dev_name(&handle->dev->dev), type, code, value);
}
```

`type` / `code` 用 `%d` 打印而形参是 `unsigned int`——所以看到的是十进制数字，不是 `EV_KEY` 这样的名字。要对照名字得自己查 `include/uapi/linux/input-event-codes.h`。

`evbug_connect()` `:28-62` 是本篇里最干净的 handler 骨架，五步一步不多：

| 步 | 行 | 动作 |
| --- | --- | --- |
| 1 | `:34-36` | `kzalloc(sizeof(struct input_handle))`，失败 `-ENOMEM` |
| 2 | `:38-40` | 填 `handle->dev` / `->handler` / `->name = "evbug"` |
| 3 | `:42-44` | `input_register_handle()`，失败跳 `err_free_handle` |
| 4 | `:46-48` | `input_open_device()`，失败跳 `err_unregister_handle` |
| 5 | `:50-53` | 打一行 `Connected device: %s (%s at %s)`，`dev->name ?: "unknown"` |

两个退出标签 `:57`/`:59` 严格按相反顺序拆。`evbug_disconnect()` `:64-72` 三步：打日志 → `input_close_device()` → `input_unregister_handle()` → `kfree()`。

handler 本体 `:81-87` 只有五个字段，用的是 `.event`（单事件）而不是 `.events`（批量）——每个事件一次 `printk`，这也是它为什么不能常开。

### §13.2 apm-power：arm64 上编不出来的那一个

122 行。`INPUT_APMPOWER` 在 `drivers/input/Kconfig:169-171`：

```
config INPUT_APMPOWER
	tristate "Input Power Event -> APM Bridge" if EXPERT
	depends on INPUT && APM_EMULATION
```

`APM_EMULATION` 在 `kernel/power/Kconfig:277-279`，`depends on SYS_SUPPORTS_APM_EMULATION`。而 `SYS_SUPPORTS_APM_EMULATION` 只有这些架构 select：

```bash
grep -rn 'SYS_SUPPORTS_APM_EMULATION' --include='Kconfig*' arch/
```

命中里 `select` 的只有 `arch/arm/Kconfig:140`（arm32 的 `ARCH_*` 段）和 `arch/mips/Kconfig:202`、`arch/sh/boards/Kconfig:119`；其余是 `config SYS_SUPPORTS_APM_EMULATION` 的定义行（`arch/arm/Kconfig:192`、`arch/powerpc/Kconfig:350`、`arch/mips/Kconfig:1182`、`arch/sh/Kconfig:99`）。**`arch/arm64/` 一条都没有**——这就是 §0 那张分诊表里 §13 一行写「apm-power 在 arm64 上不可选」的依据，也是本篇唯一一个**连 Kconfig 都选不上**的框架（对比 §12 的 gameport：能选、能编、只是拿不到设备）。

匹配面 `:89-95` 是 `EV_PWR`，事件回调 `:31-46`：

```c
static void apmpower_event(struct input_handle *handle, unsigned int type,
			   unsigned int code, int value)
{
	/* only react on key down events */
	if (value != 1)
		return;

	switch (type) {
	case EV_PWR:
		system_power_event(code);
		break;
	...
```

★ `if (value != 1) return;` 把松键（0）和自动重复（2）都挡掉了，只认按下。`system_power_event()` `:19-29` 的 switch 里**只有一个 case**：`KEY_SUSPEND` → `apm_queue_event(APM_USER_SUSPEND)` + `pr_info("Requesting system suspend...\n")`（`:22-25`），`KEY_POWER` 不在其中。

★ `EV_PWR` 这个事件类型本身在核心里有两处特殊处理，都值得记：

| 位置 | 行为 |
| --- | --- |
| `drivers/input/input.c:338-340` | `input_get_disposition()` 里 `case EV_PWR: disposition = INPUT_PASS_TO_ALL;` ——**无条件放行**，不看任何位图、不做任何值检查 |
| `drivers/input/input.c:2221-2223` | `input_set_capability()` 里 `case EV_PWR: /* do nothing */` ——★ **没有 `pwrbit` 这个位图**，声明 `EV_PWR` 能力只会在 `evbit` 上置一位，code 层面没有任何过滤 |

也就是说 `EV_PWR` 是全部事件类型里唯一「有类型位、没有代码位图」的一个。它的 code 复用 `KEY_*` 空间（`system_power_event()` 拿 `code` 直接和 `KEY_SUSPEND` 比），但既然没有位图，`input_event()` 传什么 code 都会照原样送到 handler。

### §13.3 DualSense 耳机插孔：住在 `sound/` 里的两个 handler

`sound/usb/mixer_quirks.c:531-788`，整段包在 `#if IS_REACHABLE(CONFIG_INPUT)` 里（`:531` / `:788` 配对）。开头注释 `:532-538` 交代了为什么要这么绕：PS5 手柄的音频部分是 UAC 1 设备，**协议层没有插孔检测**；但同一个 USB 设备的 HID 部分（`hid-playstation`）会把耳机/麦克风的插入状态当 `EV_SW` 事件报出来。于是 ALSA 侧注册一个 input handler，去偷听那个 input 设备。

★ 这是本篇唯一一个**不用全局变量承载 handler** 的例子（`:543-548`）：

```c
struct dualsense_mixer_elem_info {
	struct usb_mixer_elem_info info;
	struct input_handler ih;
	struct input_device_id id_table[2];
	bool connected;
};
```

`ih` 和 `id_table` 都内嵌在混音器控件的私有结构里，回调里靠 `container_of(handle->handler, struct dualsense_mixer_elem_info, ih)` 反查回来（`:560`、`:663`）。而且 `snd_dualsense_controls_create()` `:778-787` 建**两个**控件——`"Headphone Jack"` `:782` 和 `"Headset Mic Jack"` `:786`——所以一块 PS5 手柄插上来，这一个文件会注册**两个** `input_handler` 实例。★ 这正是 §1 篇末那张「12 个注册点」表两种口径都数不准运行期实例数的原因：那数的是**代码里的注册点**，不是运行时的 handler 个数。

`id_table` 在运行期填（`:749-758`），四个条件：

| 字段 | 行 | 值 |
| --- | --- | --- |
| `flags` | `:750-751` | `MATCH_VENDOR \| MATCH_PRODUCT \| MATCH_EVBIT \| MATCH_SWBIT` |
| `vendor` / `product` | `:752-753` | 从 `mixer->chip->usb_id` 拆出来 |
| `evbit` | `:754` | `EV_SW` |
| `swbit` | `:756` 或 `:758` | 输出控件填 `SW_HEADPHONE_INSERT`，输入控件填 `SW_MICROPHONE_INSERT` |

按 §9.1 的结论，这里 `MATCH_VENDOR` / `MATCH_PRODUCT` 两个标志是**真起作用**的（`input_match_device_id()` `:1007-1013` 确实查它们），`MATCH_EVBIT` / `MATCH_SWBIT` 两个仍然是装饰。

★ 但 vendor/product 相等还不够——同一型号插两个手柄就会互相串台。所以它填了 `.match` 回调（`:761`）。全树填了这个回调的 handler 一共**三个**，而且要两条命令才数全：

```bash
grep -rn --include=*.c -A 14 'struct input_handler [a-z_0-9]* = {' . | grep -E '\.match'
```

两个命中：`drivers/tty/vt/keyboard.c:1645`（`kbd_match`）、`drivers/input/joydev.c:1078`（`joydev_match`，见 §1）。DualSense 那个是运行期赋值，静态初始化块的 grep 抓不到——和 §1 篇末数 handler 注册点时遇到的是同一个问题。核心侧的调用只有一处，`drivers/input/input.c:1043`：

```c
		    (!handler->match || handler->match(handler, dev))) {
```

★ **不填就等于通过**，所以另外九个 handler 没有 `.match` 不是缺陷。

`snd_dualsense_ih_match()` `:571-616` 的做法是拿 `kobject_get_path()` 取出 input 设备（`:583`）和 USB 声卡设备（`:589`）各自的 sysfs 路径，比较前者是不是挂在后者下面——**用设备拓扑代替设备身份**。

`.start` 回调（`:764` → `snd_dualsense_ih_start()` `:657-678`）解决另一个问题：handler 连上时插孔可能**已经**是插着的，而 `EV_SW` 只在状态变化时才报。所以 `start()` 直接 `test_bit(SW_HEADPHONE_INSERT, handle->dev->sw)` 读当前值（`:668`/`:671`），并且先用 `handle->dev->swbit` 确认这个开关真的被声明过（`:667`/`:670`）；`status` 初值 `-1` `:661`，两个条件都不成立就什么都不做。核心侧调用 `.start` 的地方有两处：`drivers/input/input.c:594-595`（`input_open_device()` 之后遍历已打开的 handle）和 `:2652-2653`（`input_register_handler()` 给已存在的设备补挂 handle 时）。★ 全树填了 `.start` 的 handler 共三个：`drivers/tty/vt/keyboard.c:1648`（`kbd_start`）、`net/rfkill/input.c:312`（`rfkill_start`）、以及这里的 `:764`。

★ 最后一个细节是错误处理用了个哨兵（`:768-773`）：

```c
	err = input_register_handler(&mei->ih);
	if (err) {
		dev_warn(&mixer->chip->dev->dev,
			 "Could not register input handler: %d\n", err);
		mei->ih.event = NULL;
	}
```

注册失败时把 `.event` 抹成 NULL，释放路径 `:708-709` 再用 `if (mei->ih.event) input_unregister_handler(&mei->ih);` 判断该不该注销。★ 注册失败**不算致命错误**——`snd_dualsense_jack_create()` 照样 `return 0`（`:775`），控件建好了，只是永远读不到真实的插孔状态。这是「input 是可选增强」这个态度的直接体现，和 `#if IS_REACHABLE(CONFIG_INPUT)` 是同一个思路。

---

## 篇末汇总表

### 表一：框架 × 职责 × 挂载点 × arm64

和 §0 那张分诊表是同一批框架、不同的列：§0 回答「它住在哪个 Kconfig 下、我这台机器上有没有」，这张表回答「它干什么、挂在生命周期哪一步」。挂载点字母对应 `03-internals.md` 的 Part A 时间线。

| 节 | 框架 | 职责 | 挂载点 | arm64 |
| --- | --- | --- | --- | --- |
| §1 | evdev | 把事件原样送到 `/dev/input/eventN`，唯一的通用用户态接口 | A5→A6→A7 | 相关 |
| §1 | mousedev | 把相对/绝对事件翻译成 PS/2 协议字节流，喂 `/dev/input/mouseN` | A5→A6→A7 | 相关（遗留兼容） |
| §1 | joydev | 把摇杆事件翻译成老 joystick ABI，喂 `/dev/input/jsN` | A5→A6→A7 | 相关 |
| §2 | serio | 一条收发字节的低速端口总线（PS/2 及同类） | A2 之前，产出的是端口不是 `input_dev` | 部分：核心可用，`SERIO_I8042` 不可用 |
| §3 | ff-core | 力反馈效果的登记、编号、生命周期与 `EV_FF` 事件路由 | A3 之后、A4 之前 | 相关 |
| §3 | ff-memless | 替不能自己存效果的设备做效果合成与定时重发 | A3 之后、A4 之前 | 相关 |
| §4 | input-compat | 32 位用户态在 64 位内核上读写 `input_event` 时的结构体换算 | A8 之后（用户态边界） | 相关，`CONFIG_COMPAT` 通常开着 |
| §5 | rmi4 | Synaptics RMI4 触摸屏的整套协议栈，自带总线并自己造 `input_dev` | 自己走完 A2→A3→A4 | 相关 |
| §6 | uinput | 把 A2→A3→A4 每一步拆成 ioctl 交给用户态，用来造虚拟设备 | 自己走完 A2→A3→A4 | 相关 |
| §7 | input-mt | 多点触摸的槽位管理、帧同步与指针分配算法 | A3（建槽）+ A8.4（每帧） | 相关 |
| §8 | input-poller | 给不会中断的设备套一层轮询工作项，附带 sysfs 调周期 | A3 之后、A4 之前 | 相关 |
| §9 | input-leds | 把 `EV_LED` 能力桥成 LED class 设备，让 `/sys/class/leds` 能控键盘灯 | A5→A6→A7 | 相关 |
| §10 | touchscreen.c | 从父设备的 DT/属性里读 8 个 `touchscreen-*` 属性填进 `absinfo` | A3 | 相关，DT 平台主力 |
| §11 | sparse-keymap | 稀疏扫描码 → 键值/开关的查表库，供厂商热键驱动用 | A3 | 可编，消费者几乎全是 x86 平台驱动 |
| §11 | matrix-keymap | 行列矩阵键盘的 keymap 解析与 `keycode` 数组构建 | A3 | 相关 |
| §11 | vivaldi-fmap | 一个 sysfs `show`：把顶行功能键的物理映射打成十六进制串 | A4 之后 | 相关 |
| §12 | gameport | 15 针游戏端口总线，`match` 恒真、靠 `connect()` 试探选驱动 | A2 之前，产出的是端口不是 `input_dev` | Kconfig 全可选，实际拿不到设备 |
| §13 | evbug | 匹配一切设备，把每个事件打到内核日志（含密码，别常开） | A5→A6→A7 | 相关，调试用 |
| §13 | apm-power | 把 `EV_PWR` 的 `KEY_SUSPEND` 转成 `apm_queue_event()` | A5→A6→A7 | **不相关**，`SYS_SUPPORTS_APM_EMULATION` 没有 arm64 |
| §13 | DualSense jack | 偷听手柄 HID 报的 `EV_SW`，替 UAC 1 声卡补出插孔检测 | A5→A6→A7 | 相关 |

### 表二：每个框架的全树使用统计

所有数字用同一条口径，把 `<符号>` 换掉即可重跑：

```bash
grep -rlE --include=*.c '\b<符号>\(' . | wc -l    # 文件数
grep -rnE --include=*.c '\b<符号>\(' . | wc -l    # 命中行数
```

★ 三条必须一起记住的口径细节，否则数字对不上：

1. 只扫 `*.c`，头文件里的声明和 `static inline` 包装不计入
2. **包含定义自身那一行**（所以文件数至少是 1）
3. **`EXPORT_SYMBOL(foo)` 不计入**（那里 `foo` 后面跟的是 `)`），**但注释里写成 `foo()` 的行会计入**（kerneldoc 标题行 `* foo() - …` 正是这个形状）。逐符号查过的偏差有三处，都在「命中行数」那一列里：`input_mt_init_slots` 74 行含 2 行注释（`drivers/input/input-mt.c:25` 的 kerneldoc、`drivers/input/mouse/synaptics.c:1330` 的普通注释）、`touchscreen_parse_properties` 38 行含 1 行（`drivers/input/touchscreen/ads7846.c:1176`）、`input_ff_create` 11 行含 1 行（`drivers/input/ff-core.c:294` 的 kerneldoc）。★ 所以「命中行数」读作**调用点数的上界**，不是精确调用点数；「文件数」不受影响（这三个文件本来都有真实调用）

   ```bash
   grep -rnE --include=*.c '\binput_mt_init_slots\(' . | grep -E ':[0-9]+:[[:space:]]*[*/]'
   ```

| 节 | 框架 | 代表入口符号 | 文件数 | 命中行数 | 备注 |
| --- | --- | --- | --- | --- | --- |
| §1 | 三个字符设备 handler | `input_register_handler` | 13 | 13 | 减去 `drivers/input/input.c` 里的定义行 = **12 个注册点**，即全树 handler 数（§1 篇末已展开两种口径） |
| §2 | serio | `serio_register_port` | 30 | 33 | 宏，展开成 `__serio_register_port(… THIS_MODULE)` |
| §3 | ff-core | `input_ff_create` | 10 | 11 | 10 个消费者里多数是 HID 和摇杆驱动 |
| §3 | ff-memless | `input_ff_create_memless` | 39 | 43 | ★ 比 `input_ff_create` 多近四倍——多数设备存不了效果 |
| §4 | input-compat | `input_event_from_user` | 3 | 4 | 消费者只有 `evdev.c` 和 `uinput.c`（外加定义文件） |
| §5 | rmi4 | `rmi_register_transport_device` | 5 | 5 | 四个 transport 驱动（`rmi_i2c` / `rmi_spi` / `rmi_smbus` / `hid-rmi`）+ 定义 |
| §7 | input-mt | `input_mt_init_slots` | 64 | 74 | **全篇消费者最多的符号**，多点触摸驱动的必经之路 |
| §7 | input-mt | `input_mt_report_slot_state` | 62 | 79 | 行数 > 文件数最多的一个：一个驱动里往往多处调 |
| §7 | input-mt | `input_mt_sync_frame` | 46 | 64 | 用它的都是走 `INPUT_MT_POINTER`/`_TRACK` 的驱动 |
| §7 | input-mt | `input_mt_assign_slots` | 14 | 15 | 只有需要内核替它做指针跟踪的驱动才用 |
| §7 | input-mt（旧 API） | `input_mt_sync` | 3 | 4 | ★ 只剩 3 个消费者——协议 A 已基本绝迹，对比上面四个 |
| §8 | input-poller | `input_setup_polling` | 30 | 30 | 行数 == 文件数：每个驱动只调一次 |
| §10 | touchscreen.c | `touchscreen_parse_properties` | 36 | 38 | DT 属性解析的唯一入口 |
| §10 | touchscreen.c | `touchscreen_report_pos` | 26 | 30 | 单点/多点共用的上报包装 |
| §10 | touchscreen.c | `touchscreen_set_mt_pos` | 5 | 6 | 只有自己管 `input_mt_assign_slots` 的驱动会用 |
| §11 | sparse-keymap | `sparse_keymap_setup` | 24 | 27 | ★ 23 个消费者里 20 个在 `drivers/platform/x86/**`，**零个 arm64 消费者** |
| §11 | sparse-keymap | `sparse_keymap_report_event` | 15 | 23 | 分布同上 |
| §11 | matrix-keymap | `matrix_keypad_build_keymap` | 28 | 29 | 26 个在 `drivers/input/keyboard/`，1 个在 `drivers/auxdisplay/` |
| §11 | matrix-keymap | `matrix_keypad_parse_properties` | 14 | 15 | 只有走 DT/属性的那一半驱动会用 |
| §11 | vivaldi-fmap | `vivaldi_function_row_physmap_show` | 4 | 4 | 3 个消费者：`hid-vivaldi-common.c` / `atkbd.c` / `cros_ec_keyb.c` |
| §12 | gameport | `gameport_register_port` | 20 | 21 | 宏，同 serio；20 里含 4 个 gameport 自己的端口驱动 |

★ 四个框架**没有面向驱动的入口符号**，所以不在表二里，它们自己就是消费者：

| 节 | 框架 | 为什么没有 | 替代口径 |
| --- | --- | --- | --- |
| §6 | uinput | 它的接口是 ioctl，不是 C 函数 | 内核内消费者 0 个；`grep -c '^#define UI_' include/uapi/linux/uinput.h` → 23，逐条含义散在 §6.1 的三态状态机表和 §6.6 的变长 ioctl 一节里 |
| §9 | input-leds | 是 handler，只有一个注册点 | `drivers/input/input-leds.c:207`（`input_register_handler`），已含在表二第一行的 13 里 |
| §13 | evbug | 同上 | `drivers/input/evbug.c:91` |
| §13 | apm-power | 同上 | `drivers/input/apm-power.c:109` |

★ 从表二读出的三件事：

1. **多点触摸是 input 目录之外最大的消费面**。`input_mt_init_slots` 一个符号就有 64 个消费者文件，超过其余所有符号；而 §7 那一节在本篇里只占中等篇幅。**代码量和使用面不成比例**——这也是 §7 值得反复读的理由
2. **旧 API 的消亡是可测的**。协议 A 的 `input_mt_sync` 剩 3 个消费者，协议 B 的四个符号合计上百个。读到老驱动里的 `input_mt_sync()` 就该知道那是没人动过的代码
3. **「能编」和「有人用」是两回事**。`sparse_keymap_setup` 有 23 个消费者，但按 §11.1 逐个查过 Kconfig 依赖之后，**arm64 上一个都到不了**。反过来 `matrix_keypad_build_keymap` 的 28 个里绝大多数是 arm/arm64 SoC 键盘控制器。同样是「INPUT_*KMAP 两个库」，实际相关性完全相反
