---
title: 调试方法
---

# tty 核心层调试与故障排查

## §0 先认清 tty 的可观测面长什么样

tty 核心层的调试手段分布很不均匀，和多数子系统的直觉相反：

| 手段 | tty 核心层有没有 | 说明 |
|---|---|---|
| tracepoint | **没有** | 核心 14 个文件里零个 `TRACE_EVENT`，`include/trace/events/` 下也没有 tty 相关头文件 → §2 |
| debugfs | **没有** | 核心 14 个文件里 `debugfs` 零命中 → §4 |
| sysfs | **只有一个属性** | `/sys/class/tty/console/active` → §3 |
| **procfs** | **这才是主战场** | `/proc/tty/ldiscs`、`/proc/tty/drivers`、`/proc/tty/driver/<名>` → §4 |
| sysctl | 一个 | `dev.tty.ldisc_autoload` → §1.4 |
| 内核日志 | 有，但**大量是 `pr_debug`** | → §1.3 |
| 用户态 ioctl | **最主要的查询手段** | `stty` 等工具全靠它 → §5 |

⚠️ 一句话概括：**tty 是一个"靠 ioctl 自查、靠 procfs 看全局、几乎不打日志"的子系统。**
习惯了 `echo 1 > /sys/kernel/debug/.../dump` 那套路子的人在这里会无处下手。

### §0.1 按任务找章节

| 我想… | 去哪节 |
|---|---|
| 打开 tty 的调试日志 | §1.2、§1.3 |
| 看系统里有哪些行规程 / tty 驱动 | §4.1 |
| 看当前 console 是谁 | §3.1 |
| 跟踪某个函数有没有被调到 | §2.2 |
| 查一个 ioctl 返回码是谁给的 | §6 |
| 排查设备打不开 / 数据不通 / 卡住 | §7 |
| 一页速查 | §8 |

---

## §1 编译期开关与日志可见性

### §1.1 Kconfig 选项

| 选项 | 位置 | 默认 | 对调试的影响 |
|---|---|---|---|
| `CONFIG_TTY` | `drivers/tty/Kconfig:2-3` | y | 关掉整个 tty 层就不存在了（`bool "Enable TTY" if EXPERT`） |
| `CONFIG_UNIX98_PTYS` | `drivers/tty/Kconfig:99-101` | y | 决定 `/dev/ptmx` 和 `/dev/pts/` 是否存在 |
| `CONFIG_LEGACY_PTYS` | `drivers/tty/Kconfig:122-124` | y | 决定老式 `/dev/ptyXX` + `/dev/ttyXX` 对是否存在 |
| `CONFIG_LEGACY_PTY_COUNT` | `drivers/tty/Kconfig:139-143` | 256 | 老式 pty 的对数 |
| `CONFIG_LDISC_AUTOLOAD` | `drivers/tty/Kconfig:152-154` | y | 只是 sysctl 的**初值**，见 §1.4 |
| `CONFIG_CONSOLE_POLL` | 由 kgdb 等选中 | n | 多编出 3 个 `tty_operations` 回调（`include/linux/tty_driver.h:398-400`） |
| `CONFIG_DYNAMIC_DEBUG` | 通用 | 视配置 | **决定 §1.3 里那批 `pr_debug` 能不能打开** |

⚠️ tty 核心层**没有专属的 `CONFIG_*_DEBUG` 选项**。想看内部日志，唯一的门是
`CONFIG_DYNAMIC_DEBUG`——不像很多子系统那样有个 `CONFIG_XXX_DEBUG` 开关。

### §1.2 日志宏定义在内部头里

tty 自己的日志宏在 `drivers/tty/tty.h`（头文件保护宏 `_TTY_INTERNAL_H`，`:7`）：

```c
#define tty_msg(fn, tty, f, ...)	/* :9 */
#define tty_debug(tty, f, ...)	tty_msg(pr_debug, tty, f, ##__VA_ARGS__)   /* :12 */
#define tty_notice(tty, f, ...)	tty_msg(pr_notice, ...)                    /* :13 */
#define tty_warn(tty, f, ...)	tty_msg(pr_warn, ...)                      /* :14 */
#define tty_err(tty, f, ...)	tty_msg(pr_err, ...)                       /* :15 */
#define tty_info_ratelimited(tty, f, ...)	tty_msg(pr_info_ratelimited, ...)  /* :17-18 */
```

三个要点：

1. ⚠️ **这是内部头，不导出**。文件名叫 `tty.h` 但和 `include/linux/tty.h` 是两个
   完全不同的文件。外部驱动用不了 `tty_debug()` 这套宏，只能自己用 `dev_dbg()`。
2. **没有非限速的 `tty_info`**——只有 `tty_info_ratelimited`（`:17-18`）。
   想按 info 级别打日志的，要么用限速版，要么自己调 `pr_info`。
3. `tty_msg` 的作用是给消息加上"哪个 tty"的前缀，所以这批宏的第一个参数是
   `struct tty_struct *`，不是 `struct device *`。

### §1.3 日志等级全表（决定"默认能不能看见"）

| 宏 | 底层 | 默认可见 | 怎么打开 |
|---|---|---|---|
| `tty_err` | `pr_err` | ✔ 可见 | — |
| `tty_warn` | `pr_warn` | ✔ 可见 | — |
| `tty_notice` | `pr_notice` | ✔ 可见 | — |
| `tty_info_ratelimited` | `pr_info_ratelimited` | ✔ 可见，但**会被限速丢弃** | 无法关掉限速 |
| **`tty_debug`** | **`pr_debug`** | **✘ 默认看不见** | 需 `CONFIG_DYNAMIC_DEBUG` + 运行时开关 |

`tty_debug` 的调用点分布（这是"打开动态调试后能多看到多少"的量化）：

| 文件 | 调用点数 |
|---|---|
| `drivers/tty/tty_io.c` | 15 |
| `drivers/tty/pty.c` | 3 |
| `drivers/tty/tty_ioctl.c` | 3 |
| `drivers/tty/tty_ldisc.c` | 1 |
| `drivers/tty/tty_jobctrl.c` | 1 |

打开方式（按文件粒度）：

```sh
echo 'file drivers/tty/tty_io.c +p' > /sys/kernel/debug/dynamic_debug/control
# 整个核心层一次开
for f in tty_io tty_ldisc tty_ioctl tty_jobctrl pty; do
	echo "file drivers/tty/$f.c +p" > /sys/kernel/debug/dynamic_debug/control
done
```

### §1.4 唯一的 sysctl：`dev.tty.ldisc_autoload`

| 项 | 值 | 出处 |
|---|---|---|
| 路径 | `/proc/sys/dev/tty/ldisc_autoload` | `drivers/tty/tty_ldisc.c:827-838` 的 `tty_table` |
| 权限 | `0644`（可写） | `drivers/tty/tty_ldisc.c:832` |
| 取值 | 只能 0 或 1 | `drivers/tty/tty_ldisc.c:834-835`（`SYSCTL_ZERO` / `SYSCTL_ONE`） |
| 初值 | `IS_BUILTIN(CONFIG_LDISC_AUTOLOAD)` | `drivers/tty/tty_ldisc.c:120` |
| 消费点 | `drivers/tty/tty_ldisc.c:154` | `if (!capable(CAP_SYS_MODULE) && !tty_ldisc_autoload)` |

语义（从 `:154` 那行直接读出来，不靠猜）：置 0 后，**只有带
`CAP_SYS_MODULE` 的进程**才能通过 `TIOCSETD` 触发行规程模块的按需加载；
普通进程请求一个未加载的行规程会失败。Kconfig 帮助文本
（`drivers/tty/Kconfig:156-174`）把这条设计成一个攻击面收敛开关。

⚠️ 排查"`TIOCSETD` 返回失败但这个行规程明明有模块"时，先看这个 sysctl，
不要先怀疑 ldisc 本身。

---

## §2 tracepoint：核心层没有

### §2.1 结论与取证

核心 14 个文件里 `TRACE_EVENT` / `DEFINE_EVENT` 零命中；`include/trace/events/`
下没有 tty/console 相关头文件。**tty 核心层不提供任何 tracepoint。**

唯一沾"trace"字样的是 n_tty 里一个手工开关
（`drivers/tty/n_tty.c:83-88`）：

```c
#undef N_TTY_TRACE
#ifdef N_TTY_TRACE
# define n_tty_trace(f, args...)	trace_printk(f, ##args)
#else
# define n_tty_trace(f, args...)	no_printk(f, ##args)
#endif
```

⚠️ 注意第一行是 `#undef`（`:83`）——**它被硬关闭了，改 Kconfig 或 cmdline
都打不开，只能改源码把这行删掉再重编**。而且它用的是 `trace_printk()`：
输出进 ftrace 环形缓冲区（`/sys/kernel/tracing/trace`），**不进 dmesg**。
本文档集的源码树只读，所以这条路在这里只作为知识登记，不作为可执行手段。

### §2.2 替代观测手段

| 手段 | 命令 | 能看到什么 |
|---|---|---|
| ftrace function tracer | `echo 'tty_*' > /sys/kernel/tracing/set_ftrace_filter; echo function > .../current_tracer` | 哪些 tty 核心函数被调用了、调用序列 |
| function_graph | `echo function_graph > .../current_tracer` 配合 `set_graph_function` | 调用层级和耗时，适合看 `tty_write` → ldisc → driver 这条链 |
| kprobe | `perf probe` 或 `/sys/kernel/tracing/kprobe_events` | 抓具体参数，如 `tty_ioctl` 的 `cmd` |
| 动态调试 | §1.3 | 内核自己埋的 20+ 处 `tty_debug` |
| procfs | §4 | 全局静态状态（有哪些驱动、哪些行规程） |

因为核心层导出符号多达 97 个（见 `01-api.md` 的导出清单），
`set_ftrace_filter` 里用 `tty_*` 通配就能覆盖绝大部分入口，这是 tty 缺
tracepoint 时最实用的补偿。

---

## §3 sysfs：只有一个属性

### §3.1 `/sys/class/tty/console/active`

整个 tty 核心层只创建了一个 sysfs 属性：

| 项 | 内容 | 出处 |
|---|---|---|
| 属性名 | `active` | `drivers/tty/tty_io.c:3563` 的 `DEVICE_ATTR(active, S_IRUGO, show_cons_active, NULL)` |
| 权限 | `S_IRUGO`（0444，只读） | 同上 |
| 属性组 | `cons_dev_groups` | `drivers/tty/tty_io.c:3565-3570` |
| 读函数 | `show_cons_active()` | `drivers/tty/tty_io.c:3526-3562` |
| 类 | `class_create(THIS_MODULE, "tty")` | `drivers/tty/tty_io.c:3514` |

内容语义（逐行读 `show_cons_active()` 得出，不靠猜）：

1. 遍历所有 console，**三个过滤条件**都要满足才计入
   （`drivers/tty/tty_io.c:3536-3541`）：有 `device` 回调、有 `write` 回调、
   带 `CON_ENABLED` 标志。
2. 最多只报 16 个（`cs[16]`，`:3528`；`:3543-3544` 到上限就 break）。
3. 输出顺序是**注册顺序的逆序**（`:3546` 的 `while (i--)`）——
   最后注册的 console 排在最前。
4. ⚠️ 特例：`tty0` 不做名字解析（`:3549` 的注释
   `/* don't resolve tty0 as some programs depend on it */`），
   条件在 `:3550`。所以看到的可能是 `tty0` 而不是它背后的真实设备。
5. 名字之间用空格分隔，末尾换行（`:3557`）。

### §3.2 变化通知

`console_sysfs_notify()`（`drivers/tty/tty_io.c:3574`）内部调
`sysfs_notify(&consdev->kobj, NULL, "active")`（`:3577`）。
⇒ **用户态可以 `poll()` 这个文件等 console 切换**，不必轮询。

⚠️ 除此之外，tty 核心**不为每个 tty 设备创建任何自定义 sysfs 属性**。
`/sys/class/tty/ttyS0/` 下看到的东西来自 driver core 通用逻辑或串口层，
不是 tty 核心加的。

---

## §4 procfs：tty 真正的观测面

### §4.1 目录树

`/proc/tty` 由 `proc_tty_init()` 建立（`fs/proc/proc_tty.c:163-177`）：

```
/proc/tty/
├── ldiscs          已注册的行规程列表      proc_tty.c:175
├── drivers         已注册的 tty 驱动列表   proc_tty.c:176
├── ldisc/          空目录，仅为兼容保留    proc_tty.c:167
└── driver/         模式 0700（见下）        proc_tty.c:174
    └── <driver_name>   仅当驱动实现了 proc_show
```

⚠️ `/proc/tty/driver/` 的权限是 `S_IRUSR|S_IXUSR`（0700，`proc_tty.c:174`
用的是 `proc_mkdir_mode`），这是**故意的安全措施**。内核注释
（`fs/proc/proc_tty.c:168-173`）写明原因：`/proc/tty/driver/serial` 暴露
精确的字符计数，容易被用来推断**密码长度和击键间隔**。
排查时用 root 读它，不要试图放宽权限。

`/proc/tty/driver/<名>` 的出现条件有三个（`fs/proc/proc_tty.c:137-139`），
必须同时满足：驱动有 `driver_name`、还没建过 `proc_entry`、
**且 `driver->ops->proc_show` 非空**。
⇒ 大多数 tty 驱动这里没有条目，属正常。

### §4.2 `/proc/tty/ldiscs` 的字段

格式来自 `tty_ldiscs_seq_show()`（`drivers/tty/tty_ldisc.c:203-214`）：

```c
seq_printf(m, "%-10s %2d\n", ldops->name ? ldops->name : "???", i);
```

| 列 | 含义 |
|---|---|
| 1 | 行规程名（`tty_ldisc_ops.name`）；为空则显示 `???` |
| 2 | 行规程编号，即 `TIOCSETD` 要传的那个 `N_*` 值 |

⚠️ 这个文件**只列出已经注册的**行规程。模块化的行规程没加载就不在里面，
但仍可能通过 autoload 被 `TIOCSETD` 拉起来（§1.4）。
所以"文件里没有"≠"不能用"。

### §4.3 `/proc/tty/drivers` 的字段

格式来自 `show_tty_range()`（`fs/proc/proc_tty.c:26-64`）与
`show_tty_driver()`（`:67`）：

| 列 | 来源 | 含义 |
|---|---|---|
| 1 | `%-20s` `driver_name`，空则 `unknown`（`proc_tty.c:29`） | 驱动内部名 |
| 2 | `/dev/%-8s` `p->name`（`:30`） | 设备节点名前缀 |
| 3 | 主设备号（`:32` / `:35`） | — |
| 4 | 次设备号或次设备号区间 `N-M`（`:32-35`） | 单个设备时是一个数 |
| 5 | 类型串（`:37-63`） | `system` / `console` / `serial` / `pty:master` / `pty:slave` / `pty`，都不匹配时退化为 `type:<主>.<次>`（`:62`） |

前三行是硬编码的特殊条目（`fs/proc/proc_tty.c:75-83`）：
`/dev/tty`、`/dev/console`、`/dev/ptmx`——它们不对应普通 driver，
所以单独打印。

⚠️ 这是"我的驱动到底注册上了没有"最快的判据，比翻 dmesg 可靠：
`tty_register_driver()` 成功路径里会调 `proc_tty_register_driver()`
（`drivers/tty/tty_io.c:3456`），失败就根本不会出现在这个文件里。

### §4.4 fdinfo：查一个 fd 背后是哪条 tty

`/proc/<pid>/fdinfo/<fd>` 会带上 tty 的私有信息，链路是
`tty_show_fdinfo()`（`drivers/tty/tty_io.c:457-463`）转调
`tty->ops->show_fdinfo`。

⚠️ 但这个回调**几乎没人实现**：核心层里唯一的实现是
`pty_show_fdinfo()`（`drivers/tty/pty.c:740-743`），只输出一行：

```
tty-index:	<index>
```

⇒ 对 pty 来说这是从 master fd 直接问出 `/dev/pts/N` 里那个 N 的办法
（比 `TIOCGPTN` ioctl 更省事，因为不用写代码）。
对串口设备则读不到任何 tty 专属字段。

### §4.5 debugfs：核心层零命中

核心 14 个文件里 `debugfs` 零命中——**tty 核心不建任何 debugfs 文件**。
`/sys/kernel/debug/` 下与 tty 相关的东西全部来自具体驱动，不属本文档集范围。

### §4.6 没有故障注入

核心层没有 `fault-inject.h` 引用、没有 `should_fail()` 调用，
也没有 `CONFIG_FAIL_*` 开关。想模拟"缓冲区分配失败"这类路径，
只能靠通用手段（`CONFIG_FAILSLAB` + `/sys/kernel/debug/failslab/`）
间接命中 `tty_buffer_alloc()` 里的 `kmalloc`。

---

## §5 用户态工具

tty 的用户态 ABI 面很大（termios + 几十个 ioctl），所以**大部分排查其实在用户态做**。

### §5.1 工具清单

| 工具 | 来自 | 用途 | 背后的 ioctl |
|---|---|---|---|
| `stty -a -F /dev/ttyXXX` | coreutils | 打印全部 termios 设置（波特率、行规程标志、控制字符） | `TCGETS` |
| `stty -F /dev/ttyXXX 115200 raw -echo` | coreutils | 改配置 | `TCSETS` 系列 |
| `ldattach <N> /dev/ttyXXX` | util-linux | 给串口挂上指定行规程并驻留 | `TIOCSETD` |
| `setserial -g /dev/ttyS*` | setserial | 查/改串口的硬件参数（老式接口） | `TIOCGSERIAL` / `TIOCSSERIAL` |
| `fuser -v /dev/ttyXXX` | psmisc | 谁占着这个设备（"Device or resource busy" 必用） | 无，走 procfs |
| `lsof /dev/ttyXXX` | lsof | 同上，信息更全 | 无 |
| `ps -o pid,tty,stat,cmd -e` | procps | 每个进程挂在哪条 tty、是不是会话首进程（`s`）、有没有控制终端（`+`） | 无 |
| `tty` | coreutils | 当前 shell 的终端名 | `ttyname()` → procfs |
| `ss`/`socat`/`picocom` | 各自 | 收发数据做端到端验证 | 常规读写 |

⚠️ `stty` 默认作用于**标准输入**。带 `-F` 时它会先 `open()` 目标设备——
对串口而言这可能触发 DTR 抬升等副作用；对已被别人独占的设备会直接失败。

### §5.2 termios 相关的三个易错点

1. `stty -a` 显示的是**当前生效值**，不是"驱动支持的值"。设一个硬件不支持的波特率，
   驱动通常会**静默回退到最近的可用值**并且 `TCSETS` 返回成功
   （`tty_ioctl.c` 的 `set_termios` 路径不校验硬件能力）。
   ⇒ 改完波特率要**再读一次**确认，不能只看返回码。
2. `raw` 与 `-echo` 是两件独立的事：前者关行规程的行编辑，后者只关回显。
   排查"输入被吞/被重复"时要分别验证。
3. 终端尺寸用的 `TIOCGWINSZ`/`TIOCSWINSZ` 由核心直接处理，并会**给前台进程组发
   `SIGWINCH`**，与驱动无关。⇒ 终端尺寸不对不要去查串口驱动。

### §5.3 一个不用写代码的技巧

`/proc/<pid>/fdinfo/<fd>` 加上 `ls -l /proc/<pid>/fd/` 组合起来，
不写一行 C 就能回答"这个进程手里的 master fd 对应哪个 `/dev/pts/N`"（§4.4）。

---

## §6 错误码分诊表

### §6.1 核心层错误码普查

对 14 个核心文件统计 `return -E*` / `= -E*`（同一码多处返回，按语义归类）：

| 错误码 | 出现次数 | 主要来源文件 | 典型语义 |
|---|---|---|---|
| `EFAULT` | 30 | `tty_ioctl.c` 20 · `tty_io.c` 12 · `pty.c` 2 · `tty_jobctrl.c` 1 | `copy_to/from_user` 失败，用户态传了坏指针 |
| `EIO` | 20 | `tty_io.c` 13 · `pty.c` 6 · `tty_jobctrl.c` 3 · `tty_ldisc.c` 2 · `n_tty.c` 2 | 设备已消失 / 挂断（`TTY_IO_ERROR`）/ 对端关闭 |
| `ENOTTY` | 17 | `tty_io.c` 12 · `tty_jobctrl.c` 6 · `tty_ioctl.c` 1 | 这个 fd 不是终端，或该 ioctl 对它无意义 |
| `EINVAL` | 15 | `tty_io.c` 10 · `tty_ldisc.c` 3 · `tty_ioctl.c` 3 · `pty.c` 3 · `tty_buffer.c` 1 | 参数越界 / 行规程编号非法 |
| `ERESTARTSYS` | 12 | `tty_io.c` 5 · `n_tty.c` 3 · `tty_port.c` 2 · `tty_ioctl.c` 1 | 等待被信号打断，**用户态看到的是 `EINTR`** |
| `ENOMEM` | 11 | 分散 | 分配 `tty_struct` / 缓冲区 / ldisc 失败 |
| `EPERM` | 10 | `tty_jobctrl.c` 4 · `tty_io.c` 3 · `tty_ioctl.c` 2 · `tty_ldisc.c` 1 | 权限/能力不足（会话、`TIOCSTI`、ldisc autoload） |
| `ENOIOCTLCMD` | 10 | 见 §6.2 | **内核内部约定，不会到用户态** |
| `EAGAIN` | 8 | 分散 | `O_NONBLOCK` 下暂时无数据/无空间 |
| `ENODEV` | 4 | 分散 | 请求的 tty 行号没有对应设备 |
| `EINTR` | 4 | 分散 | 直接返回的打断 |
| `EBUSY` | 4 | 分散 | 设备已被独占 / 正在关闭 |
| `ESRCH` `EOVERFLOW` `EOPNOTSUPP` `EBADF` | 各 1 | — | 会话不存在 / 值放不下 / 不支持 / fd 非法 |

### §6.2 `-ENOIOCTLCMD` = 515：最容易误判的那一个

| 项 | 内容 |
|---|---|
| 数值 | **515**（`include/linux/errno.h:17`） |
| 约定说明 | `include/linux/tty_driver.h:134` |
| 含义 | "**我这一层不认识这个 ioctl，请上层继续试**"——不是错误 |
| 核心的返回点 | `tty_jobctrl.c:592` · `tty_ioctl.c:907` · `pty.c:470` · `pty.c:660` |
| 核心的拦截点 | `tty_io.c:2770` `:2775` `:2786`（非 compat 路径）· `:2864` `:2956` `:2965`（compat 路径） |

分诊链条（逐行读 `tty_io.c:2768-2789`）：

```
tty_ioctl()
 ├─ 核心自己认识的 cmd            → 直接处理，返回
 ├─ default: tty_jobctrl_ioctl()   :2769  → 不是 -ENOIOCTLCMD 就返回  :2770
 ├─ tty->ops->ioctl（驱动）        :2773  → 不是 -ENOIOCTLCMD 就返回  :2775
 ├─ retval = -EINVAL               :2781  ← 注意这行
 └─ ld->ops->ioctl（行规程）       :2783  → 若返回 -ENOIOCTLCMD
                                            则改成 -ENOTTY           :2787
```

⚠️ 三个后果，第三个最容易踩：

1. **用户态永远看不到 515**。日志里出现 `515`，说明某个驱动把它当真错误往上传了——
   那是驱动的 bug。
2. 用户态拿到 `ENOTTY` 时，**无法区分"核心不支持"还是"驱动+行规程都不支持"**。
   要定位是哪一层拒绝的，靠 §2.2 的 kprobe 抓 `tty_ioctl` 的返回值，
   或打开 §1.3 的动态调试。
3. ⚠️ `:2781` 那行把 `retval` 预置成 `-EINVAL`，而 `-ENOTTY` 的赋值只发生在
   `if (ld->ops->ioctl)` 成立的分支里（`:2783-2788`）。
   ⇒ **当前行规程根本没有 `ioctl` 回调时，一个不认识的 ioctl 返回的是
   `EINVAL` 而不是 `ENOTTY`**。同一个未知 ioctl，换个行规程 errno 就变了，
   排查时不要把 `EINVAL` 当成"参数写错了"。

### §6.3 `ERESTARTSYS` 与 `EINTR` 的对应

核心里 12 处返回 `-ERESTARTSYS`，但用户态**从不会**看到这个值：
它是给 syscall 返回层的指令（"信号来了，按 `SA_RESTART` 决定重启还是报错"）。

⇒ 用户态观察到的现象是：装了 `SA_RESTART` 的 handler ⇒ 系统调用自动重启，看不到错误；
没装 ⇒ 收到 `EINTR`。**排查"读串口偶尔返回 EINTR"要去查信号处理，不是查 tty。**

### §6.4 内核内部一致性检查（`WARN`）

| 文件 | `WARN*` 处数 |
|---|---|
| `drivers/tty/tty_ldisc.c` | 5 |
| `drivers/tty/tty_buffer.c` | 2 |
| `drivers/tty/tty_port.c` | 2 |
| `drivers/tty/tty_io.c` | 2 |
| `drivers/tty/pty.c` | 1 |
| `drivers/tty/n_tty.c` | 0 |

这 12 处 `WARN` 触发即意味着**内核内部状态不自洽**（引用计数、缓冲区、ldisc 生命周期），
带完整栈回溯。看到它们不要只看消息，要看栈——问题几乎总在调用者一侧。

---

## §7 四类故障的排查路径

tty 是框架型子系统，故障按"哪一环断了"分四类。

### §7.1 注册失败：设备节点根本没出现

```
/dev/ttyXXX 不存在
├─ /proc/tty/drivers 里有这个驱动吗？（§4.3）
│   ├─ 没有 → tty_register_driver() 就没成功
│   │        查 dmesg：tty_io.c:3226 "Attempt to register invalid tty
│   │        line number (%d)"（pr_err，默认可见，判据在 :3225）
│   │        → 通常是 driver->num / minor 起始值算错
│   └─ 有   → 驱动注册成功，但设备没注册
│            → 缺 tty_register_device()/tty_port_register_device() 调用
│            → 检查 /sys/class/tty/ 下有没有对应目录
└─ 用了 serdev？→ 那 /dev 节点会被故意跳过，不是故障（见 06 文档 §3.5）
```

### §7.2 打开失败：`open()` 直接报错

| errno | 最可能的原因 | 验证动作 |
|---|---|---|
| `ENODEV` | 行号超出 `driver->num`，或该 minor 没 `tty_register_device` | 对照 `/proc/tty/drivers` 第 4 列的 minor 区间 |
| `EBUSY` | 设备被 `TIOCEXCL` 独占，且本进程没有 `CAP_SYS_ADMIN`（`drivers/tty/tty_io.c:1347-1348`）；或还在关闭中 | `fuser -v` / `lsof`；`TIOCGEXCL` 查独占位 |
| `EIO` | 已置 `TTY_IO_ERROR`（设备被拔掉 / hangup 过）；或对 pty master 做了 reopen（`drivers/tty/tty_io.c:1340-1342`） | 关掉所有 fd 重开一次 |
| `EAGAIN` | reopen 时 `tty->count` 已归零，正在竞态销毁（`drivers/tty/tty_io.c:1344-1345`） | 重试一次即可 |
| `ENXIO` | 打开 `/dev/tty` 但本进程**没有控制终端**（`tty_open_current_tty()`，`drivers/tty/tty_io.c:1885`） | `ps -o tty= -p <pid>` 看是不是 `?` |
| `EPERM` | 权限或能力不足 | `ls -l` 看属主与 mode |
| `ENOMEM` | 分配 `tty_struct`/缓冲区失败 | 看内存压力，在 dmesg 里找 OOM 记录 |

⚠️ `ENXIO` 这一条在守护进程里特别常见：`setsid()` 之后进程就没有控制终端了，
此时 `open("/dev/tty")` 必然失败。这是**语义正确的行为，不是故障**。

⚠️ 打开成功但**卡住不返回**是另一类问题：`tty_port_block_til_ready()`
（`drivers/tty/tty_port.c:510`）在等载波检测。它的短路条件读得很清楚：

| 条件 | 位置 | 结果 |
|---|---|---|
| 已处于 IO 错误态 | `drivers/tty/tty_port.c:520` | 立刻返回，不等 |
| `filp == NULL` 或带 `O_NONBLOCK` | `drivers/tty/tty_port.c:524` | 立刻返回，抬 DTR/RTS 后置 active |
| termios 设了 `CLOCAL` | `drivers/tty/tty_port.c:532` | `do_clocal = 1`，等待循环里直接放行（`:570`） |
| 以上都不满足 | `drivers/tty/tty_port.c:570` | 阻塞等 `tty_port_carrier_raised()` |

⇒ 阻塞是**规定动作**，不是 bug。加 `O_NONBLOCK` 或 `stty clocal -F /dev/ttyXXX`
就能验证是不是卡在这里。注释 `drivers/tty/tty_port.c:565-567` 还说明：
驱动没实现 `carrier_raised` 时 `tty_port_carrier_raised()` 恒真，所以
"永远等不到"通常是驱动实现了这个回调但返回值不对，不是核心的问题。

### §7.3 消费者获取失败：行规程挂不上

```
TIOCSETD 失败
├─ EINVAL  → 行规程编号非法（对照 /proc/tty/ldiscs §4.2 与 N_* 定义）
├─ EPERM   → dev.tty.ldisc_autoload = 0 且进程没有 CAP_SYS_MODULE（§1.4）
├─ ENOMEM  → 分配 ldisc 失败
└─ 成功但行为不对
    └─ dmesg 有 tty_ldisc.c:498 "Falling back ldisc for %s."（pr_warn，可见）
       ⇒ 新 ldisc 的 open() 失败了，核心退回到 N_TTY 保证终端可用
       ⇒ 真正的原因在那个 ldisc 模块自己的 open()，不在 tty 核心
```

⚠️ `Falling back ldisc` 是**最有价值的一条 tty 日志**：它说明切换在核心层是成功的，
问题在具体行规程。很多人看到"设了 ldisc 但没效果"却不去翻 dmesg，绕远路。

### §7.4 状态异常：引用计数与生命周期

这批消息全部默认可见，都是**内核认为不该发生**的情况：

| 消息（格式串） | 位置 | 等级 | 含义 |
|---|---|---|---|
| `"(%d:%d): %s: NULL tty"` | `drivers/tty/tty_io.c:263` | `pr_warn` | 拿到了空 tty 指针，调用者的生命周期管理有问题 |
| `"%s: tty->count(%d) != (#fd's(%d) + #kopen's(%d))"` | `drivers/tty/tty_io.c:290` | `pr_warn` | 引用计数与实际持有者数量不符——**泄漏或多重释放** |
| `"missing write_room method"` | `drivers/tty/tty_io.c:1082` | `tty_err` | 驱动的 `tty_operations` 少填了必需回调 |
| `"read/write wait queue active!"` | `drivers/tty/tty_io.c:1799` | — | 释放 tty 时还有人挂在等待队列上 |
| `"bad slave count (%d)"` | `drivers/tty/tty_io.c:1810` | — | pty 主从计数不匹配 |
| `"bad tty->count (%d)"` | `drivers/tty/tty_io.c:1815` | — | 同 `:290`，在释放路径上发现 |
| `"%s: tty->count = 1 port count = %d"` | `drivers/tty/tty_port.c:635` | `tty_warn` | port 计数与 tty 计数不一致 |
| `"%s: bad port count (%d)"` | `drivers/tty/tty_port.c:640` | `tty_warn` | port 计数本身非法 |
| `"Falling back ldisc for %s."` | `drivers/tty/tty_ldisc.c:498` | `pr_warn` | 见 §7.3 |
| `"%s: '%s' is using deprecated serial flags (with no effect): %.8x"` | `drivers/tty/tty_io.c:2604` | `pr_warn_ratelimited` | 用户态程序在设已废弃的 `ASYNC_*` 标志，**内核忽略之** |
| `"%s: Attempt to register invalid tty line number (%d)"` | `drivers/tty/tty_io.c:3226` | `pr_err` | 见 §7.1 |

⚠️ 计数类消息（`:263` `:290` `:1810` `:1815` `tty_port.c:635` `:640`）的共同点：
**它们在故障现场之后才打出来**，真正的错误（少一次 `tty_kref_put`、
多一次 `tty_port_put`）发生在更早。看到这些要往回追，别在打印点附近找。

⚠️ `deprecated serial flags`（`:2604`）不是错误：内核只是告知这些标志无效。
它被限速，所以**日志里出现的次数少于实际发生的次数**，不要用它算频率。

---

## §8 一页速查

### §8.1 先跑这五条

```sh
cat /proc/tty/drivers            # 驱动注册没有？minor 区间对不对？
cat /proc/tty/ldiscs             # 有哪些行规程可用
cat /sys/class/tty/console/active # 当前 console 是谁
stty -a -F /dev/ttyXXX           # termios 当前值
fuser -v /dev/ttyXXX             # 谁占着
```

### §8.2 打开全部内部日志

```sh
mount -t debugfs none /sys/kernel/debug 2>/dev/null
for f in tty_io tty_ldisc tty_ioctl tty_jobctrl pty; do
	echo "file drivers/tty/$f.c +p" > /sys/kernel/debug/dynamic_debug/control
done
dmesg -w
```

### §8.3 缺 tracepoint 的替代

```sh
cd /sys/kernel/tracing
echo 'tty_*' > set_ftrace_filter
echo function > current_tracer
cat trace_pipe
```

### §8.4 症状 → 先看哪节

| 症状 | 去哪节 |
|---|---|
| `/dev/ttyXXX` 不存在 | §7.1 |
| `open()` 返回错误 | §7.2 |
| `open()` 挂住不返回 | §7.2 末尾的 DCD/CLOCAL 说明 |
| `ioctl` 返回 `ENOTTY` | §6.2 |
| 偶发 `EINTR` | §6.3 |
| `TIOCSETD` 失败或没效果 | §7.3 |
| dmesg 里有 count 不匹配的告警 | §7.4 |
| console 不是预期的那个 | §3.1，以及 06 文档里讲 DT console 选择的那节 |
| 想知道某个 pty master 对应哪个 slave | §4.4 |

### §8.5 三条最容易走错的路

1. 去 `/sys/kernel/debug/` 找 tty 目录——**没有**（§4.5）。观测面在 `/proc/tty/`。
2. 想 `echo 1 > /sys/kernel/tracing/events/tty/enable`——**没有 tty 事件目录**（§2.1）。
3. 以为 `dmesg` 里没消息就等于一切正常——tty 大部分内部日志是 `pr_debug`，
   默认全被丢掉（§1.3）。

---

## §9 延伸阅读

| 想深入 | 看 |
|---|---|
| 97 个导出符号与四大对象的关系 | 本套的 01 篇 |
| 数据通路上锁与并发 | 本套的 03、04 篇 |
| DT 与 console 选择、serdev | 本套的 06 篇 |
| 官方文档 | `Documentation/driver-api/tty/index.rst`、`tty_driver.rst`、`tty_ldisc.rst` |
| 动态调试语法 | `Documentation/admin-guide/dynamic-debug-howto.rst` |
| ftrace 用法 | `Documentation/trace/ftrace.rst` |
| termios 语义 | `man 3 termios`（用户态权威） |
