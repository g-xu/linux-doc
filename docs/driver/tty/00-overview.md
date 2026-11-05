---
title: 总览
---

# tty 核心层总览

> **基线**：`linux-6.1.158`（`f6e38ae624cf`），**arm64-only**。
> 本篇是全套 10 篇的入口与复习页：**读这一篇建立全局，改代码去对应的分篇。**
> 范围只覆盖 **tty 核心层**（`drivers/tty/tty_*.c` + `n_tty.c` + `pty.c` + 6 个公共头 + uapi，
> 共 **12004 行**）；串口驱动层 `drivers/tty/serial/`（120 个 `.c`）**显式裁掉**，
> 理由与取证见 99 篇。

---

## §1 一句话定位

**tty 核心层是「字节流 ↔ 终端语义」的转换层与调度层。**
它给你的不是"能读写串口"——那件事驱动自己就能做——而是下面这些**你不想自己写第二遍**的东西：

| 它帮你省掉了什么 | 不用它你得自己做 |
|---|---|
| **字符设备的全部管理** | 申请主次设备号、建 `cdev`、建 `/sys/class/tty/` 节点、维护 `/proc/tty/drivers` |
| **打开/关闭的引用计数** | 五层引用计数（driver / port / tty / ldisc / fd）手工对齐，还要处理拔设备与 fd 未关的竞态 |
| **终端语义** | 退格删字符、`\n` → `\r\n`、Ctrl-C 变 `SIGINT`、行编辑缓冲——这些全在行规程里，共 2549 行 |
| **作业控制** | 控制终端归属、前台进程组、`SIGHUP`/`SIGTTIN`/`SIGTTOU` 的投递规则 |
| **termios 的整套 ABI** | 几十个 ioctl、三个生效时机变体、波特率编码与任意波特率 |
| **中断上下文的解耦** | 中断里不能做的事（唤醒、加睡眠锁、跑行规程）由 flip buffer + workqueue 接手 |
| **流控** | 四条独立的流控路径（软件 XON/XOFF、硬件 RTS/CTS、ldisc 节流、驱动 stop/start） |
| **可插拔的处理策略** | 同一条线在运行时从"终端"切成"PPP 链路"或"GSM 多路复用" |

⚠️ 反过来说，**tty 核心层不做的事**同样要记清：它不碰任何寄存器、不解析任何设备树属性、
不提供 tracepoint、不建 debugfs。它是**纯软件的中间层**。

**一句话判据**：如果一个字节流需要"被人当终端用"，就走 tty；
如果只是搬数据，用 serdev 或裸字符设备更省事。

---

## §2 问题背景与核心抽象

> tty 是**框架型**子系统（无 `bus_type`）**且带很大的用户态 ABI**。
> 所以本节分两半讲：前半是"这个框架要解决的问题"，后半是"它与用户态的契约边界"。

### §2.1 问题域：一个字节流上有四组互相冲突的要求

一条串口线上过来的字节，在四种视角下含义完全不同：

```
     硬件视角              内核中间层视角          终端语义视角        网络协议视角
   ┌───────────┐         ┌──────────────┐       ┌────────────┐     ┌───────────┐
   │ 一个字节   │         │  一段缓冲     │       │ 一行文本    │     │ 一个帧     │
   │ 来自 FIFO │  ──→    │  可能乱序到   │  ──→  │ 可退格编辑  │ 或  │ 有 CRC     │
   │ 在中断里   │         │  可能溢出     │       │ 可触发信号  │     │ 有转义     │
   └───────────┘         └──────────────┘       └────────────┘     └───────────┘
     不能睡眠              可以睡眠                要 echo 回去        要交给协议栈
     不能加锁太久          要能背压                要判断特殊字符      不做任何解释
```

四组要求两两冲突：

1. **中断上下文 vs 需要睡眠的处理**。收到一个字节要做的事里包含"唤醒读进程"、
   "回显到发送方向"、"可能要加互斥锁"——这些都不能在中断里做。
   ⇒ 必须有一个**能在中断里安全调用、把活推给别人**的接口。
   这就是 flip buffer（`drivers/tty/tty_buffer.c`，698 行）存在的唯一理由。

2. **同一条线要支持互斥的解释规则**。既要能当终端（Ctrl-C 变信号），
   又要能当 PPP 链路（Ctrl-C 是数据的一部分，绝不能变信号）。
   ⇒ 解释规则必须**可整体替换**，而且要能在运行时换。这就是行规程（ldisc）。

3. **设备可能先于使用者消失**。USB 转串口被拔掉时，硬件侧立刻没了，
   但用户态进程手里的 fd 还在，下一次 `read()` 照样进内核。
   ⇒ "硬件侧的状态"和"打开会话的状态"必须是**两个生命周期独立的对象**。
   这就是 `tty_port` 与 `tty_struct` 的分离。

4. **终端是"一群进程共享的资源"，不是"一个进程的资源"**。
   Ctrl-C 要发给前台进程组的所有进程；终端挂断要通知整个会话。
   ⇒ 归属关系必须挂在**会话/进程组**上，不能挂在 fd 上。这就是作业控制那一套。

⚠️ tty 核心层里几乎每个"看起来过度设计"的地方，都能追到这四条之一。
读代码时先问"这是哪条约束的后果"，比逐行读快得多。

### §2.2 核心抽象：四个对象 + 两层可插拔

tty 的抽象骨架只有六个要素。**先记住这张图，后面所有代码都能挂上去：**

```
                 ┌──────────────────── 用户态 ────────────────────┐
                 │  read/write/ioctl on /dev/ttyXXX 或 /dev/pts/N │
                 └───────────────────────┬────────────────────────┘
                                         │  VFS
  ┌──────────────────────────────────────▼──────────────────────────────────────┐
  │  tty 核心层                                                                  │
  │                                                                              │
  │   struct tty_driver ─────── 注册表：主/次设备号、有几条线、类型              │
  │        │  ttys[]                        （模块加载~卸载）                     │
  │        ▼                                                                     │
  │   struct tty_struct ─────── 会话体：这次打开的全部状态（46 字段，9 把锁）      │
  │        │  ├── ->port ──→ struct tty_port ── 硬件侧持久体（22 字段，独立 kref）│
  │        │  └── ->link ──→ 另一个 tty_struct（只有 pty 用）                     │
  │        ▼                                                                     │
  │   ══ 可插拔层 1：行规程 ══   struct tty_ldisc（只有 2 字段）                  │
  │        │                     逻辑在 tty_ldisc_ops（18 字段）                  │
  │        │                     默认 n_tty（2549 行），可运行时换                │
  │        ▼                                                                     │
  │   ══ 可插拔层 2：驱动 ══     struct tty_operations（37 个回调）               │
  │                                                                              │
  │   flip buffer：驱动 → 行规程的唯一上行通路（异步，workqueue）                 │
  └──────────────────────────────────────┬──────────────────────────────────────┘
                                         │
                                   具体驱动（8250 / pl011 / pty / hvc …）
```

四个对象的生命周期长短是**严格分层**的，这是 tty 最重要的一条结构性事实：

| 对象 | 存在期 | 谁创建 | 定义位置 | 字段数 |
|---|---|---|---|---|
| `tty_driver` | 模块加载 → 卸载 | 驱动 | `include/linux/tty_driver.h:442-473` | 21 |
| `tty_port` | 设备发现 → 设备移除 | 驱动 | `include/linux/tty_port.h:100-123` | 22 |
| `tty_struct` | 首次 `open` → 末次 `close` | 核心 | `include/linux/tty.h:193-251` | 46 |
| `tty_ldisc` | 挂上 → 换掉 | 核心 | `include/linux/tty_ldisc.h:250-253` | **2** |

⚠️ 第一号易错点：**`tty_port` 比 `tty_struct` 活得长**。
`tty_port` 有自己的 `kref`（`kref_init(&port->kref)`，`drivers/tty/tty_port.c:104`），
`tty_port_put()` 在 `drivers/tty/tty_port.c:308`。
把硬件上下电写在 `tty_operations.open`/`close` 里是错的——
应该写在 `tty_port_operations.activate`/`shutdown` 里。

⚠️ 第二号易错点：**`struct tty_ldisc` 只有 2 个字段**（`ops` 和 `tty`）。
它不是"行规程对象"，而是一个绑定记录。行规程的逻辑在
`struct tty_ldisc_ops`（`include/linux/tty_ldisc.h:211-248`，18 个字段），
每条 tty 的私有数据在 `tty->disc_data`。

### §2.3 为什么不能绕过这个框架直接写字符设备

假设你有一个 UART，想跳过 tty 直接注册一个字符设备。省下的是学习成本，
补上的是下面这张清单——**每一条都在核心层有现成实现**：

| 你必须自己实现 | 核心层里的对应实现 |
|---|---|
| 主次设备号申请与 `cdev` 注册 | `alloc_chrdev_region()`（`drivers/tty/tty_io.c:3424`）/ `register_chrdev_region()`（`:3432`）+ `cdev_add()`（`:3158`） |
| sysfs 类与设备节点 | `class_create(THIS_MODULE, "tty")`（`drivers/tty/tty_io.c:3514`） |
| 全局可见的注册信息 | `/proc/tty/drivers`（`fs/proc/proc_tty.c:176`） |
| 打开时的引用计数与竞态 | `tty_open()` 里两处 `goto retry_open` 的重试逻辑 |
| 5 层引用计数对齐 | 见 03 篇 §B3 |
| 拔设备时 fd 还在的处理 | hangup 路径会**换掉 `f_op`**，见 03 篇 §B4 |
| 终端语义（退格/回显/信号） | `n_tty.c`，**2549 行** |
| 作业控制 | `tty_jobctrl.c`，593 行 |
| termios 全套 ioctl | `tty_ioctl.c`，1004 行，13 个导出符号 |
| 中断上下文解耦 | `tty_buffer.c`，698 行 |
| 一把支持超时的 ldisc 读写锁 | `tty_ldsem.c`，430 行 |

**总计 12004 行。** 这就是"为什么不自己写"的量化答案。
换个角度：核心层导出了 **97 个符号**（`tty_io.c` 30 · `tty_port.c` 26 ·
`tty_ioctl.c` 13 · `tty_buffer.c` 11 · `tty_ldisc.c` 7 · 其余 10 个），
一个最简 tty 驱动只需要用到其中不到 10 个。

⚠️ 真正需要绕过 tty 的场景是存在的：如果这条 UART 上挂的是一个**芯片**
（蓝牙、NFC、GNSS 模块）而不是"终端"，那么正确答案是 **serdev**——
它把 UART 变成一条总线，`/dev/ttyXXX` 反而会**故意不出现**。
判据与代码路径见 06 篇。

### §2.4 用户态 ABI 边界：本项目做过的子系统里最大的一个

tty 与用户态的契约由四个 uapi 头构成：

| 头文件 | 内容 |
|---|---|
| `include/uapi/linux/tty.h` | 46 行，**31 个 `N_*` 行规程编号**（`:10-41`） |
| `include/uapi/linux/tty_flags.h` | `ASYNC_*` 旧标志（多数已废弃） |
| `include/uapi/asm-generic/termbits.h` | `struct termios` / `struct termios2` / 各标志位 |
| `include/uapi/asm-generic/termbits-common.h` | 波特率常量等公共部分 |

这些是**改一个字节就破 ABI** 的文件。三条必须知道的边界事实：

1. **已废弃的标志内核会静默忽略**，并且只打一条限速日志：
   `"%s: '%s' is using deprecated serial flags (with no effect): %.8x"`
   ——`drivers/tty/tty_io.c:2604`，`pr_warn_ratelimited`。
   ⇒ 老程序在新内核上"设置成功但没效果"的根源。

2. **`-ENOIOCTLCMD`（515）是内核内部约定，永远不出现在用户态**。
   约定说明在 `include/linux/tty_driver.h:134`；核心在
   `drivers/tty/tty_io.c:2787` 把它翻译掉。
   ⚠️ 但翻译目标不总是 `ENOTTY`：`drivers/tty/tty_ioctl.c` 之外，
   `drivers/tty/tty_io.c:2781` 先把返回值预置为 `-EINVAL`，
   **当前行规程没有 `ioctl` 回调时，未知 ioctl 得到的是 `EINVAL`**。
   同一个请求换个行规程 errno 就变——这是 ABI 层面一个真实的粗糙处。

3. **`termios` 的三个设置变体差别在"生效时机"，不在参数**：
   `TCSETS` / `TCSETSW` / `TCSETSF` 在 `drivers/tty/tty_ioctl.c:817-822`
   映射成两个内部标志（`TERMIOS_FLUSH` / `TERMIOS_WAIT`，
   定义在 `drivers/tty/tty_ioctl.c:41-46`）的三种组合。
   ⚠️ 且驱动的 `set_termios` 回调是 `void`——**它无法报错**
   （`drivers/tty/tty_ioctl.c:362-363`）。硬件不支持某波特率时通常静默回退，
   ioctl 依然返回 0。**改完必须回读**。

### §2.5 与邻近机制的对比：什么时候不该用 tty

同一条 UART 上的字节流，内核里有四条不同的出路。选错的代价很高——
选了 tty 却只想搬数据，你要额外应付几十个 ioctl 和一套行编辑；
选了裸字符设备却真的要当终端用，`stty`、`Ctrl-C`、`ps` 全部失效。

| 机制 | 用户态看到什么 | 适合的场景 | 代价 |
|---|---|---|---|
| **tty 核心层** | `/dev/ttyXXX`，支持 termios 与作业控制 | 这条线**要被人当终端用**（登录 console、调试串口、终端仿真） | 12004 行框架的全部语义都在生效，包括你不想要的部分 |
| **serdev** | **什么都没有**——`/dev/ttyXXX` 故意不出现 | UART 上挂的是**芯片**（蓝牙、NFC、GNSS），由内核驱动独占 | 用户态彻底拿不到这条线；DT 写法与发现路径见 06 篇 |
| **行规程改造成网络设备** | `/dev/ttyXXX` 仍在，同时多一个 `netdev` | 串口上跑 PPP / SLIP / CAN-over-serial | 需要一个 ldisc 实现，且切换有破坏性（08 篇 §3） |
| **裸字符设备（自建 `cdev`）** | 自定义节点 | 字节流**完全不需要终端语义**，也不需要走总线 | §2.3 那张清单要自己补齐 |

判据可以压成三个问题，从上往下问，第一个"是"就停：

1. 这条线上挂的是**芯片还是人**？芯片 → serdev。
2. 这条线上的字节需要**被解释成帧**（有 CRC、有转义、要变成网络接口）→ 写 ldisc。
3. 用户态需要 `stty` / `Ctrl-C` / `ps` 认得它吗？需要 → tty；不需要 → 裸字符设备。

⚠️ 一个常见的误判：**"我用了 tty，就还能同时用 serdev"**。不能同时。
serdev 接管一条 UART 之后，那条线的 `/dev` 节点不再创建——这是**设计如此**，
不是故障。排查"设备节点消失"时要先确认 DT 里有没有 UART 子节点（06 篇 §3.5）。

### §2.6 arm64 上的实际形态

tty 核心层本身**完全不参与设备树**（06 篇 §1.1 给了取证：核心 14 个文件里
DT 相关 API 零命中）。arm64 上真正与 DT 打交道的是它下面那层串口驱动：

| 观察项 | arm64 上的实际数字 | 怎么复现 |
|---|---|---|
| 串口驱动文件数 | **75 个**（`drivers/tty/serial/` 顶层 `.c`；连子目录算 120 个） | `ls drivers/tty/serial/*.c \| wc -l` |
| arm64 DTS 里写了 `stdout-path` 的文件 | **396 个** | `grep -rl 'stdout-path' arch/arm64/boot/dts \| wc -l` |
| 常见的四个 arm64 串口驱动 | `amba-pl011.c`（ARM 官方 IP）· `8250/8250_dw.c`（DesignWare）· `qcom_geni_serial.c`（高通）· `samsung_tty.c`（Exynos） | 文件均在树内 |
| 核心层与 DT 的耦合 | **0** | 见 06 篇 §1.1 |

⇒ 这条分工线要记牢：**波特率、引脚、时钟、中断全部由串口驱动从 DT 读；
tty 核心只从驱动手里接过一个"已经能收发字节的对象"。**
所以"DT 写了 `current-speed` 但不生效"这类问题，方向在串口驱动，不在核心层。

⚠️ `stdout-path` 决定的是**早期 console**，与"哪个 `/dev/ttyXXX` 是登录终端"
是两件不同的事。前者的解析点在 OF 层（06 篇 §4.1），
后者取决于 init 系统起了哪个 `getty`。核心层的可观测面只到
`/sys/class/tty/console/active`（07 篇 §3.1）。

---

## §3 概念模型：一句话记住每个角色

前面 §2.2 那张图给的是结构，这里给的是**职责边界**——
读代码时判断"这件事该谁做"，靠的是这张表：

| 角色 | 它是什么 | 它**不管**什么 |
|---|---|---|
| `tty_driver` | 一张注册表：主设备号、行数、类型、`tty_operations` | 不管任何一条具体线路的状态 |
| `tty_port` | 硬件侧的持久体：载波、DTR/RTS、接收缓冲头、`activate`/`shutdown` | 不管"谁打开了我"，也不管终端语义 |
| `tty_struct` | 一次打开会话的全部状态：termios、进程组、标志位、9 把锁 | 不管寄存器，也不解释字节含义 |
| `tty_ldisc_ops` | 字节流的解释规则：行编辑、回显、特殊字符、`read`/`write` 的语义 | 不管硬件，也不管设备号 |
| `tty_operations` | 驱动对核心的承诺：能发多少、发出去、改硬件参数 | 不管字节是什么意思 |
| flip buffer | 从中断上下文到可睡眠上下文的**唯一**通道 | 不解释数据，只搬运 |

三个"谁调谁"的方向标（详表在 02 篇 §3.3），是读 tty 代码不迷路的关键：

```
  from above :  用户态 / 核心  ──→  ldisc  ──→  driver
  from below :  driver  ──→  flip buffer  ──→  ldisc  ──→  用户态
  谁在调 driver 的 ops ?  ⚠️ 绝大多数是 ldisc，不是 tty 核心
```

⚠️ 最后一行是新手最容易搞反的一条。看到 `tty_operations` 里某个回调，
不要默认"核心会调它"——先在 `n_tty.c` 里搜一遍。这条事实决定了
"换掉行规程，驱动的某些回调就再也不会被调到"。

---

## §4 数据结构摘要

完整字段清单与七组易错关系在 02 篇。这里只留**复习时最该记住的三件事**。

### §4.1 命名约定：看前缀就知道该找谁

| 前缀 | 含义 | 例子 |
|---|---|---|
| `tty_*` | 核心 API，最大的一族 | `tty_register_driver` `tty_hangup` |
| `tty_port_*` | port 层 API——**驱动应优先用这一族** | `tty_port_open` `tty_port_tty_get` |
| `tty_ldisc_*` | ldisc 层 API | `tty_ldisc_ref` `tty_ldisc_flush` |
| `tty_insert_flip_*` / `tty_flip_*` | flip buffer 的插入与推送 | `tty_flip_buffer_push` |
| `tty_termios_*` | termios 换算与比较 | `tty_termios_baud_rate` |
| `__tty_*` | 底层实体或不加锁版本 | `__tty_alloc_driver` |
| `TTY_*`（宏） | `tty->flags` 的**位号**，12 个 | `TTY_THROTTLED` `TTY_IO_ERROR` |
| `TTY_DRIVER_*`（宏） | `driver->flags` 的**掩码**，8 个 | `TTY_DRIVER_DYNAMIC_DEV` |
| `N_*`（宏） | 行规程编号，31 个，属 uapi | `N_TTY` `N_NULL` |

⚠️ `TTY_*` 是**位号**（配 `test_bit`），`TTY_DRIVER_*` 是**掩码**（配 `&`）。
两族名字长得像，用法完全不同——这是 02 篇列出的五个名字陷阱里最要紧的一个。

### §4.2 字段数与"哪个结构装什么"

| 结构 | 位置 | 字段数 | 一句话 |
|---|---|---|---|
| `struct tty_operations` | `include/linux/tty_driver.h:357-403` | 37（其中 3 个在 `CONFIG_CONSOLE_POLL` 下，`:398-400`） | 驱动填的表 |
| `struct tty_driver` | `include/linux/tty_driver.h:442-473` | 21 | 注册信息 |
| `struct tty_struct` | `include/linux/tty.h:193-251` | 46 | 会话状态，最大的那个 |
| `struct tty_ldisc_ops` | `include/linux/tty_ldisc.h:211-248` | 18 | 行规程逻辑 |
| `struct tty_ldisc` | `include/linux/tty_ldisc.h:250-253` | **2** | 只是一条绑定记录 |
| `struct tty_port` | `include/linux/tty_port.h:100-123` | 22 | 硬件侧持久体 |
| `struct tty_port_operations` | `include/linux/tty_port.h:33-39` | 5 | `activate`/`shutdown` 在这里 |
| `struct tty_port_client_operations` | `include/linux/tty_port.h:41-46` | 3 | serdev 走的就是这三个 |
| `struct tty_buffer` | `include/linux/tty_buffer.h:10-23` | 9 | 一块接收缓冲 |
| `struct tty_bufhead` | `include/linux/tty_buffer.h:38-48` | 5 | 缓冲链表头 + work |

⚠️ 数这些结构的字段时有个坑：它们以 `} __randomize_layout;` 结尾，不是 `};`。
用"找第一个以 `};` 开头的行"定位结构末尾会冲过头，把后面的注释和下一个结构
一起数进来（第一次数 `tty_operations` 得到 76，实际 37）。取证方式记在 99 篇 §6。

### §4.3 锁：14 把，先记住 4 把

全景表在 02 篇 §6.2，用法表在 03 篇 §B1。复习只需要这四把：

| 锁 | 保护什么 | 谁拿 |
|---|---|---|
| `tty_mutex`（全局） | open 期间的查找与 `tty_init_dev()` | `tty_open()` 及其子函数 |
| `tty->legacy_mutex`（即 `tty_lock()`） | 几乎所有对单个 tty 的操作 | 核心到处拿 |
| `tty->ldisc_sem` | 用 ldisc 时读锁、切 ldisc 时写锁（**带 5 秒超时**） | `tty_ldisc_ref*` / `tty_ldisc_lock` |
| `tty->atomic_write_lock` | 整个 `write()` 期间 | `tty_write_lock()` |

⚠️ `ldisc_sem` 不是标准 `rw_semaphore`，是 `tty_ldsem.c`（430 行）里手写的一把，
支持超时、写者优先、不支持降级、**不做溢出检查**
（设计意图逐字写在 `drivers/tty/tty_ldsem.c:5-9`，坦白那句在 `:14`）。
超时是**设计出来的正常返回路径**，不是 bug——看到 reopen 偶发失败先想它（08 篇 §5）。

---

## §5 机制摘要

按 03 篇的编号顺序过一遍，每条只留"结论 + 去哪看"。
`§Ax` 是流程，`§Bx` 是横切机制，`§Cx` 是设计评价。

| 编号 | 机制 | 一句话结论 |
|---|---|---|
| §A1 | 驱动分配 | `tty_alloc_driver()` 在 `drivers/tty/tty_io.c:3369` 处导出；分配即带 kref |
| §A2 | 驱动注册 | 字符设备区间有**两条路**：动态 `alloc_chrdev_region()`（`:3424`）或固定 `register_chrdev_region()`（`:3432`） |
| §A3 | 设备注册与 port 绑定 | port 与 index 有三种绑定方式，优先级由官方文档定（01 篇 §4.3） |
| §A4 | `tty_open()` | 核心层最复杂的函数；**有两处 `goto retry_open`**，即 open 会重试 |
| §A5 | `tty_init_dev()` | `tty_struct` 的诞生，七步 + 四个错误标签；这里拿 `tty_ldisc_lock(tty, 5 * HZ)`（`:1354`） |
| §A6 | `tty_port_open()` | 驱动侧 open 的标准实现（`drivers/tty/tty_port.c:796`），里面就是 `activate` + `block_til_ready` |
| §A7 | 控制终端与作业控制 | 归属挂在 `current->signal->tty`，不在 fd 上（`drivers/tty/tty_jobctrl.c:112`） |
| §A8 | read / write / poll | 三者都是"拿 ldisc 引用 → 转发 → 放引用"；写方向**没有核心级缓冲** |
| §A9 | ldisc 切换 | `tty_set_ldisc()` 在 `drivers/tty/tty_ldisc.c:598` 处导出；失败会回退 `N_TTY`（`:498` 的日志） |
| §A10 | 关闭与释放 | 三层收尾；**termios 会跨 open 继承**——这是"上次的设置还在"的原因 |
| §B1 | 锁 | 14 把，只需记住 4 把（§4.3） |
| §B2 | 三段式 | 出现频率最高的代码模式：`tty_ldisc_ref` → 转发 → `tty_ldisc_deref` |
| §B3 | 引用计数 | **五层**：driver / port / tty / ldisc / fd，各有各的规则 |
| §B4 | hangup | ⚠️ 它会**换掉 `f_op`**——这是设备消失后老 fd 仍能安全 `read()` 的机制 |
| §B5 | 流控 | **四条互相独立的路**：软件 XON/XOFF、硬件 RTS/CTS、ldisc 节流、驱动 stop/start |
| §B6 | 可观测性 | 核心层几乎不打日志，观测面在 `/proc/tty/`（07 篇 §4） |
| §C1 | 设计债 | 源码自陈 10 处（`FIXME`/`XXX`），是本项目做过的子系统里最多的 |
| §C2 | 锁语义未定 | 三处源码自己承认"锁语义没定清" |
| §C3 | 需警惕的行为 | 四类，含 `void set_termios` 无法报错 |
| §C4/§C5 | 设计评价 | 五个可以合理质疑的选择 + 整体评价 |

⚠️ 这份摘要里最值得单独记住的两条：**§B4 换 `f_op`** 和 **§A10 termios 继承**。
它们解释了两类"看起来不可能"的现象——设备已经不在了 `read()` 却不崩，
以及新打开的串口带着上一个进程留下的波特率。

---

## §6 端到端调用路径

### §6.1 写方向：`write()` 到线上（同步，无核心缓冲）

```
用户态 write(fd, buf, n)
  │
  ▼  VFS → tty_fops.write
tty_write()                       拿 tty->atomic_write_lock（整个 write 期间）
  │                               拿 ldisc 读引用（tty_ldisc_ref_wait）
  ▼
ld->ops->write()  = n_tty_write()  在这里做 OPOST 输出转换（\n → \r\n 等）
  │                                空间不够就睡在 tty->write_wait 上
  ▼
tty->ops->write()  = 驱动          ⚠️ 返回值是"实际接受了多少"，可能小于 n
  │                                驱动自己决定是塞 FIFO 还是排自己的环形缓冲
  ▼
硬件寄存器 / 对端 tty（pty 走 tty->link，drivers/tty/pty.c:111-113）
```

三个必须记住的点：

1. **写方向没有核心级缓冲**。`tty_buffer` 只服务接收方向。
   写不进去时的等待发生在 ldisc 里，不是在一个核心队列里。
2. 驱动 `write()` 的返回值是**短写语义**。ldisc 负责循环，驱动不负责。
3. 剩余空间要问 `write_room`——⚠️ 缺这个回调会打 `"missing write_room method"`
   （`drivers/tty/tty_io.c:1082`，`tty_err` 级别，默认可见）。

### §6.2 读方向：线上到 `read()`（异步，跨上下文）

```
中断 / 驱动轮询                    ← 中断上下文，不能睡眠
  │
  ▼  tty_insert_flip_char() 等四个插入函数
struct tty_buffer  链表             ⚠️ 满了就少插或不插，返回实际插入量
  │
  ▼  tty_flip_buffer_push()        drivers/tty/tty_buffer.c:599
queue_work(system_unbound_wq)      :604  ⚠️ 到这里只是"排了个 work"
  │ ══════════ 上下文切换：中断 → 可睡眠的 worker ══════════
  ▼
flush_to_ldisc()                   drivers/tty/tty_buffer.c:532（INIT_WORK 在 :657）
  │                                拿 buf->lock 与 ldisc 读引用
  ▼
ld->ops->receive_buf2()            n_tty：行编辑、回显、特殊字符 → 信号
  │                                节流点在这里（返回值 < count 就是背压）
  ▼
n_tty 的读缓冲 + 唤醒 tty->read_wait
  │
  ▼
用户态 read(fd, buf, n)
```

可观测性落点标在路径上（07 篇给了完整命令）：

| 位置 | 能看到什么 | 手段 |
|---|---|---|
| 插入点 | 有没有丢字节 | 驱动自己的统计；核心不计数 |
| `flush_to_ldisc` | work 有没有被调度 | ftrace function tracer |
| ldisc 层 | 收到了什么 | 打开 `tty_debug` 动态调试 |
| 全程 | 谁占着这条 tty | `fuser -v` / `lsof` |

⚠️ 这张图上最关键的一条竖线是那道"上下文切换"。**它是 tty 全部异步性的来源**，
也是"数据 push 了但读不到"这类问题的分界线：分界线以上是驱动的问题，
以下是 ldisc 或用户态的问题。

### §6.3 打开路径：五个可能失败的点

```
open("/dev/ttyXXX")
 ├─ tty_open()          查 driver + index      → ENODEV
 ├─ 独占/重开检查        tty_io.c:1340-1348     → EIO / EAGAIN / EBUSY
 ├─ tty_init_dev()      分配 + 挂 ldisc         → ENOMEM，或 5 秒超时失败（:1354）
 ├─ tty->ops->open()    驱动侧                  → 驱动自定义 errno
 └─ tty_port_block_til_ready()  等载波          → 阻塞（不是错误！tty_port.c:510）
```

⚠️ 最后一格是排查里最常被误判的：**它阻塞是规定动作**。
四个短路条件（IO 错误 `:520`、`O_NONBLOCK` `:524`、`CLOCAL` `:532`、等待 `:570`）
逐条列在 07 篇 §7.2。

---

## §7 与其他子系统的关系

| 子系统 | 关系 | 落点 |
|---|---|---|
| **VFS / 字符设备** | tty 是个字符设备大户：一次 `alloc_chrdev_region()`/`register_chrdev_region()` + `cdev_add()` | `drivers/tty/tty_io.c:3424` `:3432` `:3158` |
| **driver core** | 只用到 `class` 与 `device`，**没有自己的 `bus_type`** | `class_create(THIS_MODULE, "tty")`，`drivers/tty/tty_io.c:3514` |
| **serdev** | 反向依赖：serdev 是 tty 的**使用者**，靠 `tty_port_client_operations` 接数据 | `include/linux/tty_port.h:41-46`；DT 侧见 06 篇 §3 |
| **信号 / pid** | 作业控制全靠它：`current->signal->tty`、`PIDTYPE_SID` 遍历会话 | `drivers/tty/tty_jobctrl.c:112` `:179-181` |
| **workqueue** | 接收路径的上下文切换用 `system_unbound_wq`（不绑 CPU） | `drivers/tty/tty_buffer.c:604` |
| **procfs** | tty 的主要观测面就建在这里 | `fs/proc/proc_tty.c:163-177` |
| **printk / console** | tty 提供 `/sys/class/tty/console/active` 与切换通知 | `drivers/tty/tty_io.c:3563` `:3574` |
| **audit** | `tty_audit.c`（248 行）把键入内容送进审计子系统 | `drivers/tty/tty_audit.c` |
| **devpts 文件系统** | pty 的 slave 节点由 `fs/devpts/` 提供，**不属 tty 核心** | 见 06 篇 §5.1 |
| **网络栈** | 只通过 ldisc 间接相关（PPP/SLIP/CAN 各自实现一个 ldisc） | 04 篇 §5.1 的 20 个 ldisc 实例 |

⚠️ 一条容易搞错的边界：**`/dev/pts/N` 的创建不在 tty 核心里**，
在 devpts 文件系统里。所以"`/dev/pts` 开不出新的"这类问题要去查 mount 选项
（`max` 参数）和 sysctl，不是查 `pty.c`（06 篇 §6.4）。

另外两组"不相关"也要记住，省得白找：

- **tty 核心不碰时钟、电源、pinctrl、regulator**。这些全在串口驱动那一层。
- **tty 核心不碰 DMA**。收发的 DMA 由具体串口驱动实现，核心只见到字节。

---

## §8 按角色的阅读指引

全套 10 篇，没人需要全读。按你要做的事挑：

| 你要做的事 | 读这几篇，按此顺序 | 大约的重点 |
|---|---|---|
| **写一个新的 tty 驱动** | 01 → 05 角色 1–2 → 03 §A1–§A6 | `tty_operations` 哪些必填、`activate`/`shutdown` 放什么、flip buffer 三步 |
| **写一个行规程** | 04 → 05 角色 3 → 02 §1.0.E | 18 个 `ops` 字段分上下两半、编号怎么选、`MODULE_ALIAS_LDISC` 不能省 |
| **改核心层代码** | 03 全篇 → 02 §6 → 04 | 五层引用计数、四把锁、10 处设计债 |
| **写用户态程序（termios / pty）** | 05 角色 5–6 → 01 §8 → 08 §8 | `TCSETS` 三变体、raw 模式、pty 起子进程、必须回读 |
| **排查线上故障** | 07 → 06 §7 → 05 §8 | `/proc/tty/*`、错误码分诊、四类故障决策树 |
| **只想弄懂它为什么这么设计** | 08 → 00 §2 → 03 §C | 八个概念的费曼式讲解 + 五个可质疑的设计选择 |
| **做代码审计 / 评估风险** | 03 §C → 07 §6 → 99 | 设计债、锁语义未定处、全部取证命令 |

三条对所有角色都成立的忠告：

1. **先读 02 篇 §1.0 的七组关系**。它把本子系统全部易错点集中在一处，
   十分钟能省掉几小时的调试。
2. **官方文档只有 483 行必读**（`Documentation/driver-api/tty/` 里的六篇），
   比本套任何一篇都短，值得先读一遍原文。
3. **任何"设置成功但没生效"的现象，先回读**。tty 的 ABI 里有多处
   "接受但静默忽略"（废弃标志、不支持的波特率、`void set_termios`），
   只看返回码等于没有验证。

---

## §9 本篇的边界

本篇是**纯浓缩**：所有结论都在 01–08 篇里有完整推导与取证，
这里不引入任何新的判断。行号与命令的完整取证清单在 99 篇。

**范围之外**（本套 10 篇都不覆盖）：串口驱动层 `drivers/tty/serial/`、
虚拟控制台 `drivers/tty/vt/`、hvc、`n_gsm` 的协议实现、devpts 文件系统内部。
理由与逐条取证见 99 篇 §1.1。
