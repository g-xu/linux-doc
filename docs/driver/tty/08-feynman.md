---
title: 费曼学习法
---

# tty 核心层：八个概念的费曼式讲解

> 每个概念四段：**问题 → 先举个例子 → 回到代码 → 这个类比什么时候会误导你**。
> 第四段是重点：类比只负责把你带到门口，越过那道门就要靠源码。

---

## §1 为什么 tty 要有四个对象，而不是一个设备加一个驱动？

### 先举个例子

想象一个老式剧院。里面有四样彼此独立的东西：

- **剧院的营业执照和场地规格**——这家剧院能容纳多少个厅、几号厅到几号厅、
  归哪家公司经营。剧院一年不开门，这些也照样存在。
- **每个厅的物理设施**——墙、线路、投影机、音响。哪怕今晚不演出，
  设施照样在那儿，需要有人维护。
- **今晚这一场演出**——观众进场才有，散场就没了。它临时占用某个厅。
- **翻译耳机的频道**——同一场演出，有人听原声，有人听同传。
  换频道不影响演出本身。

tty 的四个对象一一对应：

| 剧院里的东西 | tty 里的对象 | 存在期 |
|---|---|---|
| 营业执照 + 场地规格 | `struct tty_driver` | 模块加载到卸载 |
| 每个厅的物理设施 | `struct tty_port` | 设备存在期间（比"打开"长） |
| 今晚这场演出 | `struct tty_struct` | 第一次 `open()` 到最后一次 `close()` |
| 翻译耳机频道 | `struct tty_ldisc` | 可运行时切换 |

### 回到代码

| 对象 | 定义位置 | 字段数 | 关键证据 |
|---|---|---|---|
| `struct tty_driver` | `include/linux/tty_driver.h:442-473` | 21 | 有 `major`/`minor_start`/`num`——这是"场地规格" |
| `struct tty_port` | `include/linux/tty_port.h:100-123` | 22 | 有 `kref`（`drivers/tty/tty_port.c:104` 里 `kref_init`）——独立引用计数 |
| `struct tty_struct` | `include/linux/tty.h:193-251` | 46 | 最大的那个，装的是"这次打开"的全部会话状态 |
| `struct tty_ldisc` | `include/linux/tty_ldisc.h:250-253` | **2** | 只有 `ops` 和 `tty`两个字段 |

⚠️ 最反直觉的一条：`struct tty_ldisc` 只有 2 个字段。它不是"一个对象"，
而是"一个操作表指针 + 一个回指"。行规程的全部逻辑在
`struct tty_ldisc_ops`（`include/linux/tty_ldisc.h:211-248`，18 个字段）里，
而每条 tty 的私有行规程数据挂在 `tty->disc_data` 上，**不在 `tty_ldisc` 里**。

### 这个类比什么时候会误导你？

顺着"剧院/演出"往下推，第一个推错的结论是：
**"一个厅同时只能演一场，所以一个 `tty_port` 只能对应一个 `tty_struct`。"**

这句话在串口上成立，在 pty 上不成立——而且核心代码里根本不靠这个假设。
`tty_struct` 里的 `port` 指针（`include/linux/tty.h` 的 `tty_struct` 定义内）
是**多对一允许**的方向：pty 的一对 master/slave 各有自己的 `tty_struct`，
互指靠 `tty->link`（`drivers/tty/pty.c:113`）。

更要紧的第二个推错：**"演出散场，设施也就没用了"**。恰恰相反，
`tty_port` 的引用计数（`tty_port_put()`，`drivers/tty/tty_port.c:308`）
存在的全部理由就是让它**活得比 `tty_struct` 久**。详见 §2。

⚠️ 千万不要把这四个对象往设备模型的 `device` ↔ `driver` 上套。
`tty_driver` 不是 `struct device_driver`：它不参与总线匹配、没有 `probe`。
tty 核心层没有自己的 `bus_type`，四个对象的关系是
"注册表 + 硬件侧持久体 + 会话体 + 可换的处理策略"，
和 `struct file_operations` 的味道更接近。

---

## §2 `tty_port` 和 `tty_struct` 为什么必须分开活？

### 先举个例子

你正在用一个 USB 转串口小板，`minicom` 开着。这时你把它**直接拔了**。

拔的一瞬间，内核里发生了两件不同的事：

- **硬件不在了**——寄存器读不到，中断不会再来。
- **但有个进程手里还捏着 fd**——`minicom` 还没退出，它下一次
  `read()` 还会进内核。

如果只有一个对象，这一刻就必死无疑：释放它，`minicom` 的 `read()` 就访问
野指针；不释放它，USB 驱动的 `disconnect()` 就没法返回。

分成两个对象就解开了：**硬件侧的东西（`tty_port`）可以立刻标记为"废了"，
会话侧的东西（`tty_struct`）留着，等最后一个 fd 关掉再走。**

### 回到代码

- `tty_port_init()` 里 `kref_init(&port->kref)`——
  `drivers/tty/tty_port.c:104`。port 从这一刻起有自己的生命计数。
- `tty_port_put()`——`drivers/tty/tty_port.c:308`；引用归零才真正
  `tty_port_destroy()`（`drivers/tty/tty_port.c:279`，在 `:294` 被调用）。
- 注释把契约写得很直白：`tty_port_init()` 之后必须销毁，
  "**要么间接通过引用计数 `tty_port_put()`，要么在不用引用计数时直接销毁**"
  ——`drivers/tty/tty_port.c:88-90` 和 `:275-277`。
- 拔掉之后的"废了"标记：`tty_io_error(tty)` 分支在
  `drivers/tty/tty_port.c:520` 让 `tty_port_block_til_ready()` 立刻返回，
  不再去等载波。

### 这个类比什么时候会误导你？

顺着"拔 U 盘"往下推，第一个推错的结论是：
**"所以每个 tty 驱动都得管好 port 的引用计数。"**

不对。注释 `drivers/tty/tty_port.c:275-277` 明确留了另一条路：
**不使用引用计数**，直接在设备移除时 `tty_port_destroy()`。
很多把 port 内嵌在自己结构体里、生命周期与自己完全一致的驱动就是这么做的。
引用计数是**可选机制**，不是强制协议。

⇒ 读驱动代码时，看到只有 `tty_port_init()`/`tty_port_destroy()` 而没有
`tty_port_put()`，不要立刻判定为泄漏——先确认它是不是走的这条路。

---

## §3 行规程为什么能在运行时换掉？

### 先举个例子

同一条电话线，你可以：接一台普通电话机（说话）、接一台传真机（收发文件）、
接一台调制解调器（上网）。线没变，**线两端的"解释规则"变了**。

行规程就是这个"解释规则"。同一个 `/dev/ttyS0`：

- 挂 `N_TTY`：字节流按终端规则解释——退格删字符、回车换行、
  Ctrl-C 变成信号。
- 挂 `N_PPP`：字节流按 PPP 帧解释，变成一个网络接口。
- 挂 `N_GSM0710`：字节流被复用成多条虚拟通道。

### 回到代码

- 换的动作是 `TIOCSETD` ioctl，编号取值来自
  `include/uapi/linux/tty.h` 里的 `N_*` 宏。
- 已注册的可用列表在 `/proc/tty/ldiscs`，格式串
  `"%-10s %2d\n"`——`drivers/tty/tty_ldisc.c:213`。
- 换失败时的兜底：`drivers/tty/tty_ldisc.c:498` 的
  `"Falling back ldisc for %s."`。**新行规程的 `open()` 失败，
  核心会退回 `N_TTY`**，保证这条 tty 不会变成一块砖。
- 按需加载被一个 sysctl 卡着：`drivers/tty/tty_ldisc.c:154`
  的 `if (!capable(CAP_SYS_MODULE) && !tty_ldisc_autoload)`。

### 这个类比什么时候会误导你？

顺着"换电话机"往下推，第一个推错的结论是：
**"既然只是换个解释规则，那换的时候数据不会丢。"**

会丢。切换过程要独占 tty、要把旧行规程关掉。核心为此专门造了一把
**带超时**的读写锁（见 §5），并且在等不到时宁可失败也不等下去。
所以切换行规程是个**有破坏性**的操作，不是无损热插拔。

第二个推错：**"`TIOCSETD` 返回 0 就说明新规则已经在工作了。"**
不一定——见上面那条 `Falling back ldisc`：核心层的切换成功了，
新行规程的 `open()` 却可能失败，此时你实际用的还是 `N_TTY`。
真正的确认方式是回读 `TIOCGETD`。

---

## §4 收到的数据为什么先进缓冲区，再由 workqueue 交给行规程？

### 先举个例子

快递驿站。快递员（中断处理程序）到了门口，把包裹往架子上一放就走——
他不能等你下楼签收，因为他后面还有两百个件。**签收（行规程处理）**
是驿站员工闲下来时慢慢做的另一件事。

如果快递员必须等你签收才能走，一次派送就被最慢的那个收件人拖死。
串口中断里如果直接跑行规程逻辑（要处理回显、要唤醒进程、
可能要加锁），下一个字符就来不及收，直接丢数据。

### 回到代码

- 架子：`struct tty_buffer`（`include/linux/tty_buffer.h:10-23`，9 个字段）
  + `struct tty_bufhead`（`:38-48`，5 个字段）。
- 驿站员工：一个 work，绑定的函数就是 `flush_to_ldisc`——
  `drivers/tty/tty_buffer.c:657` 的 `INIT_WORK(&buf->work, flush_to_ldisc)`。
  函数体在 `drivers/tty/tty_buffer.c:532`。
- 快递员"放下就走"的动作：`tty_flip_buffer_push()`——
  `drivers/tty/tty_buffer.c:599`，它做的事只有
  `queue_work(system_unbound_wq, &buf->work)`（`:604`）。
- 同样的 `queue_work` 出现在 `drivers/tty/tty_buffer.c:79`、`:634`、`:687`
  ——**四个入口，一个出口**。

⚠️ 用的是 `system_unbound_wq`，不是 `system_wq`：unbound 队列不绑 CPU，
worker 可以在任意 CPU 上跑。这对"中断在 CPU0、处理在 CPU3"的场景是必要的，
但也意味着**处理顺序与 CPU 亲和性无关**。

### 这个类比什么时候会误导你？

顺着"驿站"往下推，第一个推错的结论是：
**"既然是异步的，那 `tty_flip_buffer_push()` 返回之后数据就已经在行规程里了。"**

完全不是。`tty_flip_buffer_push()` 只是**排了一个 work**（`:604`），
数据还在缓冲区里躺着。想确认数据到了行规程，得看行规程侧的状态，
不能看 push 的返回。这是"写了 push 但读不到数据"这类问题最常见的误判点。

第二个推错：**"缓冲区满了会阻塞发送方。"**
中断上下文不能阻塞。缓冲区满时插入函数会**少插或不插**，
返回实际插入的字节数——调用方（驱动）有责任检查这个返回值。
`tty_buffer_space_avail()` 存在的理由就是让调用方提前问一句
（pty 就靠它实现 `pty_write_room()`，`drivers/tty/pty.c:133`）。

---

## §5 为什么行规程要自己造一把读写锁，不用现成的 `rw_semaphore`？

### 先举个例子

一间会议室，规则是"讨论的人可以很多，装修的人只能一个，而且装修时谁都不许进"。
标准的读写锁就是这个规则。

但 tty 这间会议室多了两条特殊要求：

1. **等不到就走**。有人要装修（换行规程），已经在里面讨论的人（读者）
   迟迟不出来——不能无限期等下去，因为申请装修的可能是个持有 tty 锁的路径，
   死等就变成系统级的卡死。
2. **装修优先**。如果讨论的人源源不断地来，装修的永远排不上队
   （写者饿死）。所以规则改成：一旦有人申请装修，后来的讨论者一律先排队。

标准 `rw_semaphore` 给不了第 1 条。所以 tty 自己写了一把。

### 回到代码

文件头的注释把设计意图列成三条，一字不差
（`drivers/tty/tty_ldsem.c:5-9`）：

```
The ldisc semaphore is semantically a rw_semaphore but which enforces
an alternate policy, namely:
  1) Supports lock wait timeouts
  2) Write waiter has priority
  3) Downgrading is not supported
```

| 条目 | 代码落点 |
|---|---|
| 带超时的加锁 | `ldsem_down_read(sem, timeout)`——`drivers/tty/tty_ldsem.c:337`；`ldsem_down_write(sem, timeout)`——`:363` |
| 不支持降级 | 全文件没有 `downgrade` 函数，对照 `drivers/tty/tty_ldsem.c:337-424` 的全部导出接口 |
| 计数布局与 rwsem 不同 | `LDSEM_ACTIVE_MASK` 等宏——`drivers/tty/tty_ldsem.c:37-40`；注释说明"高半部是等待计数，与 rwsem 归一化到 wait bias 的做法不同"（`:11-13`） |
| 超时的实际用法 | `tty_ldisc_lock(tty, 5 * HZ)`——`drivers/tty/tty_io.c:1354`，reopen 路径上最多等 5 秒 |

⚠️ 注释还坦白了一句：`"2) Lacks overflow checking"`
（`drivers/tty/tty_ldsem.c:14`）——**它不做溢出检查**。
这是一把为特定场景手工优化的锁，不是通用设施，别拿去别处用。

### 这个类比什么时候会误导你？

顺着"会议室"往下推，第一个推错的结论是：
**"超时了就说明系统有 bug。"**

不是。超时是**设计出来的正常返回路径**：`tty_ldisc_lock()` 超时会返回错误，
上层据此让 `open()` 失败（返回给用户态），而不是把整个调用链卡死。
换句话说，这把锁把"可能的死锁"降级成了"一次可以重试的失败"。
看到 reopen 偶尔失败，先想到这条 5 秒超时，别去找死锁。

第二个推错：**"写者优先，那读者会饿死。"**
读者不会——因为写者持锁的时间是有界的（换行规程是个有限步骤的操作），
而且写者拿不到就超时退出。两个机制加在一起，双向都不会无限等。

---

## §6 控制终端为什么属于「会话」，而不属于文件描述符？

### 先举个例子

公司给一个**项目组**配一间会议室，不是给某个员工配。

- 员工可以来来去去，会议室归属不变。
- 新员工加入项目组，自动就能用这间会议室——不需要单独审批。
- 项目组解散，会议室收回，**组里所有人同时失去它**。

控制终端就是"配给会话的那间会议室"。它决定了：Ctrl-C 发给谁、
终端挂断时谁收 `SIGHUP`、`/dev/tty` 打开的是哪个设备。

### 回到代码

归属确实存在会话/进程上，不在 `struct file` 上：

| 事实 | 代码 |
|---|---|
| 归属字段挂在信号结构上 | `current->signal->tty`——`drivers/tty/tty_jobctrl.c:112`、`:118` |
| 反向记录会话 | `tty->ctrl.session = get_pid(task_session(current))`——`drivers/tty/tty_jobctrl.c:110` |
| 只有会话首进程能设 | 注释 `drivers/tty/tty_jobctrl.c:91`：`"Only callable by the session leader and only if it does not already have"` |
| 打开时自动获取的条件 | `drivers/tty/tty_jobctrl.c:137-138`：进程**还没有**控制终端，**且**这条 tty 还没被别的会话占用；满足才 `__proc_set_tty()`（`:154`） |
| 整个会话一起失去 | `session_clear_tty()`——`drivers/tty/tty_jobctrl.c:175`，用 `do_each_pid_task(session, PIDTYPE_SID, p)` 遍历会话里**每个**进程清空（`:179-181`） |
| `/dev/tty` 就是查这个字段 | `get_current_tty()`——`drivers/tty/tty_jobctrl.c:160`，取 `current->signal->tty`（`:166`） |

### 这个类比什么时候会误导你？

顺着"会议室归项目组"往下推，第一个推错的结论是：
**"既然会议室归组，那组里任何人都能把它换成另一间。"**

不能。`TIOCSCTTY` 只有**会话首进程**能调（`drivers/tty/tty_jobctrl.c:91`
的注释，以及 `:39` 的 `current->signal->tty != tty` 检查）。
普通成员进程连"抢"都抢不了。

第二个推错，而且是实际写守护进程时最常踩的：
**"我 `open("/dev/ttyS0")` 了，它就成了我的控制终端。"**
只在满足 `drivers/tty/tty_jobctrl.c:137-138` 那两个条件时才会。
`setsid()` 之后进程是新会话的首进程且没有控制终端——这时打开才会自动获取；
反过来，`setsid()` 之后打开 `/dev/tty` 必然失败，返回 `ENXIO`
（`tty_open_current_tty()`，`drivers/tty/tty_io.c:1885`）。
"我明明有终端"这种直觉在这里是错的：**你有的是 fd，没有的是控制终端。**

---

## §7 pty 为什么是两个 tty 背靠背，而不是一个环回设备？

### 先举个例子

对讲机不是一个设备，是**一对**。你手里那只有自己的开关、自己的音量、
自己的电池。另一只在对方手上，参数可以完全不同。
你说话，声音从对方那只出来——但两只对讲机本身是两个独立实体。

pty 就是这样一对：`/dev/ptmx` 开出来的 master 一只，
`/dev/pts/N` 是 slave 那只。终端仿真器（`xterm`、`sshd`）握 master，
shell 握 slave。

如果做成"一个环回设备"，就没法让两端有各自独立的 termios、
各自独立的进程组、各自独立的挂断状态——而这些恰恰是终端语义的核心。

### 回到代码

互指与配对：

| 事实 | 代码 |
|---|---|
| 两个独立的 `tty_driver` | `ptm_driver` / `pts_driver`——`drivers/tty/pty.c:42-43` |
| 驱动层面互指 | `driver->other`——`drivers/tty/pty.c:375`、`:392` |
| 每端各有 `tty_struct`，用 `link` 互指 | `alloc_tty_struct(driver->other, idx)`——`drivers/tty/pty.c:379` |
| 写就是"塞给对面" | `pty_write()` 取 `struct tty_struct *to = tty->link`——`drivers/tty/pty.c:111-113` |
| 剩余空间问对面 | `pty_write_room()` 返回 `tty_buffer_space_avail(tty->link->port)`——`drivers/tty/pty.c:133` |
| 关一端要通知另一端 | `set_bit(TTY_OTHER_CLOSED, &tty->link->flags)` + 两次唤醒——`drivers/tty/pty.c:66-68`；master 关闭还要 `tty_vhangup(tty->link)`（`:79`） |

注释把设计意图讲得很清楚（`drivers/tty/pty.c:106-108`）：
写 pty 就是"**像一个中断处理程序在替对面收数据那样，把东西丢到链路的另一端**"。
⇒ 这解释了为什么 pty 完全不需要"硬件"：它借用的正是 §4 那套接收缓冲机制。

### 这个类比什么时候会误导你？

顺着"对讲机"往下推，第一个推错的结论是：
**"两只对讲机是对称的，所以 master 和 slave 是对称的。"**

不对称，差别很硬：

- **slave 是个正常终端，master 不是。** 对 master 做 reopen 直接返回 `-EIO`
  ——`drivers/tty/tty_io.c:1340-1342` 明确判 `PTY_TYPE_MASTER`。
- **master 关闭会把 slave 挂断**（`tty_vhangup(tty->link)`，
  `drivers/tty/pty.c:79`）；slave 关闭只是置个标志位。
- **打开顺序有约束**：`drivers/tty/pty.c:229` 判
  `subtype == PTY_TYPE_SLAVE && tty->link->count != 1`——
  slave 只能在 master 恰好被打开一次时打开。
- master 还有一个 slave 完全没有的"包模式"（`ctrl.packet`，
  `drivers/tty/pty.c:246`、`:326`、`:339`）。

第二个推错：**"既然是软件设备，就没有缓冲区限制。"**
有——`pty_write_room()`（`:129-133`）问的就是对面缓冲区的真实剩余空间。
pty 一样会写满、一样要处理短写。

---

## §8 `TCSETS` / `TCSETSW` / `TCSETSF` 差在哪，为什么差别在核心而不在硬件？

### 先举个例子

你要改一条流水线的规格。三种改法：

- **立刻改**（`TCSETS`）：马上换模具，正在传送带上的半成品照旧往下走。
- **等做完再改**（`TCSETSW`）：等传送带上的东西全部出完，再换模具。
- **扔掉再改**（`TCSETSF`）：等已发出的做完，**并且把还没进线的原料倒掉**，
  然后换模具。

第三种听起来粗暴，但很有必要：把波特率从 9600 改成 115200 时，
缓冲区里那些按 9600 收进来的字节已经没有意义了，留着只会串成乱码。

### 回到代码

三种语义在核心里是**两个内部标志位的组合**，不是三段不同的代码：

```c
/* drivers/tty/tty_ioctl.c:41-46 */
#define TERMIOS_FLUSH	1
#define TERMIOS_WAIT	2
#define TERMIOS_TERMIO	4
#define TERMIOS_OLD	8
```

映射关系一目了然（`drivers/tty/tty_ioctl.c:817-822`）：

| ioctl | 传给 `set_termios()` 的 opt | 语义 |
|---|---|---|
| `TCSETS` | `TERMIOS_OLD` | 立刻生效 |
| `TCSETSW` | `TERMIOS_WAIT` + `TERMIOS_OLD` | 等发送队列排空 |
| `TCSETSF` | `TERMIOS_FLUSH` + `TERMIOS_WAIT` + `TERMIOS_OLD` | 排空 + 丢弃已收数据 |

两个标志的执行点都在 `set_termios()`（`drivers/tty/tty_ioctl.c:465`）里，
集中在 `:503-527`：

- `TERMIOS_FLUSH` → 调行规程的 `ld->ops->flush_buffer`（`:520`）
- `TERMIOS_WAIT` → 调驱动的 `tty->ops->wait_until_sent`（`:525`）

⇒ 都是**核心在调别人**。硬件驱动只被告知"参数变了"，
它对"变之前要不要等、要不要丢"一无所知。

最后落到 `tty_set_termios()`（`drivers/tty/tty_ioctl.c:341`），
它做的是**两次通知**：

```c
if (tty->ops->set_termios)		/* :362 */
	tty->ops->set_termios(tty, &old_termios);	/* :363 */
...
if (ld->ops->set_termios)		/* :369 */
	ld->ops->set_termios(tty, &old_termios);	/* :370 */
```

驱动和行规程各收到一次，参数都是**旧值**——因为新值已经写进
`tty->termios` 了，回调要做的是"对比新旧、决定改什么硬件寄存器"。

### 这个类比什么时候会误导你？

顺着"换模具"往下推，第一个推错的结论是：
**"`TCSETS` 返回成功，说明硬件已经按新参数工作了。"**

不成立，两个理由：

1. `tty_set_termios()` 先无条件更新 `tty->termios`，**之后**才通知驱动
   （`:362-363`）。驱动的 `set_termios` 回调是 `void`——**它没有办法报错**。
   硬件不支持这个波特率，回调通常静默回退到最近的可用值，
   而 ioctl 依然返回 0。
2. 注释 `drivers/tty/tty_ioctl.c:256` 提到驱动"总应该提供 `set_termios` 方法"
   ——用"应该"，说明**存在不提供的驱动**。那种驱动上，参数只改了核心里的记录，
   硬件那边什么都没发生。

⇒ 正确做法：改完**回读一次**（`TCGETS` 或 `stty -a`），拿实际值做判断。
只看返回码等于什么都没验证。

第二个推错：**"`TCSETSF` 会丢掉两个方向的数据。"**
看 `:520` 那行——它调的是**行规程的** `flush_buffer`，
清的是**已接收待处理**的那一侧。发送侧靠的是
`TERMIOS_WAIT`（`:525` 的 `wait_until_sent`），那是**等完**而不是丢掉。
一个丢、一个等，方向和动作都不一样。

---

## §9 阅读下一篇

| 本文讲的 | 想深入看 | 去哪篇 |
|---|---|---|
| 四个对象各是什么 | 全部字段清单、七组关系图 | `02-datastructures.md` |
| port 与 tty 的生命周期 | 引用计数与并发的完整规则 | `03-internals.md` |
| 行规程可插拔 | 行规程作为子框架的注册与实现 | `04-extensions.md` |
| 缓冲区与 workqueue | 数据通路逐跳分析 | `03-internals.md` |
| 那把带超时的锁 | 三把锁的层级与加锁顺序 | `03-internals.md` |
| 控制终端与会话 | 作业控制相关的 ioctl 全集 | `01-api.md` |
| pty 的两端 | 写 pty 类驱动的代码骨架 | `05-usage.md` |
| termios 的三个变体 | 用户态调 ioctl 的完整骨架 | `05-usage.md` |
| 上面任何一条排查不下去 | 日志、procfs、错误码分诊 | `07-debug.md` |

---

## §10 常见误解对照表

| 常见误解 | 正确理解 | 代码依据 |
|---|---|---|
| `tty_driver` 是设备模型里的 driver | 它是注册表，不参与总线匹配，tty 核心没有自己的 `bus_type` | `include/linux/tty_driver.h:442-473` |
| 一个 `tty_port` 对应一个 `tty_struct` | pty 的两端各有 `tty_struct`，靠 `tty->link` 互指 | `drivers/tty/pty.c:113` |
| `struct tty_ldisc` 装着行规程的逻辑 | 它只有 2 个字段；逻辑在 `tty_ldisc_ops`，私有数据在 `tty->disc_data` | `include/linux/tty_ldisc.h:250-253` |
| 驱动必须用 `tty_port_put()` 管引用 | 引用计数是可选的，也允许直接 `tty_port_destroy()` | `drivers/tty/tty_port.c:275-277` |
| `TIOCSETD` 返回 0 就换成功了 | 可能已回退到 `N_TTY`，要看 dmesg 并回读 `TIOCGETD` | `drivers/tty/tty_ldisc.c:498` |
| `tty_flip_buffer_push()` 返回后数据已交给行规程 | 它只排了一个 work，数据还在缓冲区 | `drivers/tty/tty_buffer.c:599-604` |
| 缓冲区满会阻塞插入方 | 中断上下文不能阻塞，插入函数返回实际写入量，调用方必须检查 | `drivers/tty/pty.c:129-133` |
| ldsem 超时说明有 bug | 超时是设计出来的失败路径，把死锁降级成可重试的失败 | `drivers/tty/tty_ldsem.c:5-7`、`drivers/tty/tty_io.c:1354` |
| 打开一个 tty 就获得了控制终端 | 要同时满足"本进程还没有"且"这条 tty 未被别的会话占用" | `drivers/tty/tty_jobctrl.c:137-138` |
| 有 fd 就能 `open("/dev/tty")` | 没有控制终端时返回 `ENXIO`，`setsid()` 之后必然如此 | `drivers/tty/tty_io.c:1885` |
| pty master 和 slave 对称 | master 不能 reopen（`-EIO`）、关闭会挂断对端、独有包模式 | `drivers/tty/tty_io.c:1340-1342`、`drivers/tty/pty.c:79` |
| `TCSETS` 返回 0 就说明硬件按新参数跑了 | 驱动的 `set_termios` 是 `void`，无法报错；必须回读确认 | `drivers/tty/tty_ioctl.c:362-363` |
| `TCSETSF` 丢弃收发两个方向 | 只 flush 接收侧；发送侧是"等完"，靠 `wait_until_sent` | `drivers/tty/tty_ioctl.c:520`、`:525` |
