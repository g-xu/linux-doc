---
title: API 参考
---

# tty 01：代码地图与对外 API

> **基线**：`linux-6.1.158`（`f6e38ae624cf`），**arm64-only**。
> ⚠️⚠️ **本树对 `drivers/tty` 打过补丁，但全部在串口驱动层，tty 核心层完全干净** ——
> **详见 §0.1 和 99 §1.1。**

## §0 代码地图

### §0.1 ⚠️⚠️ 先说范围：本文档只覆盖「tty 核心层」

**`drivers/tty/` 有 198 个 `.c`/`.h`、182949 行** ——**不可能一套文档讲完。**
**本文档集的范围是"tty 核心层"，也就是"驱动与用户态之间那一层"。**

| 层 | 目录 / 文件 | 规模 | 本文档 | ⚠️ 本树补丁 |
|---|---|---|---|---|
| **tty 核心** | **`drivers/tty/tty_*.c`（12 个）+ `n_tty.c` + `pty.c`** | **12004 行** | **覆盖** | **干净（0 个脏文件）** |
| **tty 头文件** | **`include/linux/tty*.h`（6 个）+ uapi + 私有头 `drivers/tty/tty.h`** | **1748 + 46 + 119 行** | **覆盖** | **干净** |
| ⚠️ **串口层** | **`drivers/tty/serial/`（120 个 `.c`）** | **约 15 万行** | **裁掉** | ⚠️ **被改过**（见下） |
| hvc / vt / serdev / ipwireless | `drivers/tty/hvc/`(13) · `vt/`(7) · `serdev/`(2) · `ipwireless/`(4) | — | **裁掉** | 干净 |
| 独立设备驱动 | `synclink_gt.c`(5054) · `n_gsm.c`(3889) · `moxa.c`(2398) · `mxser.c`(1938) … | — | **裁掉** | 干净 |

⚠️⚠️ **本树改过的 14 个文件，全部在串口层**：

| 文件 | 改动量 |
|---|---|
| `drivers/tty/serial/8250/8250_port.c` | **+159 行**（最大） |
| `drivers/tty/serial/8250/8250.h` | +41 |
| `drivers/tty/serial/8250/8250_mtk.c` | +32 |
| `drivers/tty/serial/8250/8250_core.c` | +24 |
| `drivers/tty/serial/8250/8250_bcm7271.c` | +21 |
| `drivers/tty/serial/8250/8250_omap.c` · `serial/omap-serial.c` · `serial/amba-pl011.c` | 18 / 12 / 10 |
| 其余 6 个 | 各 1–4 |
| **合计** | **269 插入 / 63 删除** |

**补丁的性质（关键词统计）**：
```
printk_cpu_sync_get_irqsave       15
printk_cpu_sync_put_irqrestore    15
printk_prefer_direct_enter         1
printk_prefer_direct_exit          1
```
重跑：`git diff -- drivers/tty | grep '^+' | grep -oE 'printk_[a-z_]+' | sort | uniq -c | sort -rn`

⇒ **这是 printk / console 序列化方向的 RT 类 backport** ——
**改的是"串口作为 console 时怎么与 printk 抢占协调"，与 tty 核心层的语义无关。**

⇒ **结论**：**本文档描述的 tty 核心层是原生未改的 6.1.158**；
⚠️ **但如果你要读串口驱动，必须自己 `git diff` 一遍**（99 §1.1）。

### §0.2 核心文件在哪

| 文件 | 行数 | 职责 |
|---|---|---|
| **`drivers/tty/tty_io.c`** | **3608** | **字符设备入口 + 驱动注册 + open/release/read/write/ioctl 主体 + hangup** |
| **`drivers/tty/n_tty.c`** | **2549** | **默认行规程**：回显 / 特殊字符 / 规范模式 / 信号 |
| **`drivers/tty/tty_ioctl.c`** | **1004** | **termios 与波特率 ioctl** |
| **`drivers/tty/pty.c`** | **947** | **伪终端（PTY / devpts）** |
| **`drivers/tty/tty_ldisc.c`** | **861** | **行规程的注册 / 切换 / 引用计数** |
| **`drivers/tty/tty_port.c`** | **796** | **`tty_port` 的生命周期与 open/close 辅助** |
| **`drivers/tty/tty_buffer.c`** | **698** | **flip buffer：驱动 → 行规程的数据通路** |
| **`drivers/tty/tty_jobctrl.c`** | **593** | **作业控制：控制终端 / 前台进程组 / `SIGHUP`** |
| **`drivers/tty/tty_ldsem.c`** | **430** | **专用的 ldisc 读写信号量实现**（0 个导出符号） |
| `drivers/tty/tty_audit.c` | 248 | 审计 |
| `drivers/tty/tty_baudrate.c` | 215 | 波特率编解码 |
| `drivers/tty/tty_mutex.c` | 55 | **`tty_lock` / `tty_unlock`** |

**加上 6 个公共头（1748 行）+ uapi（46 行）+ 私有头 `drivers/tty/tty.h`（119 行）**
⇒ **本文档的取证范围约 13900 行。**

### §0.3 八个头文件的分工（6 公共 + 1 uapi + 1 私有）

| 头文件 | 行数 | 装什么 |
|---|---|---|
| **`include/linux/tty_driver.h`** | **594** | **`struct tty_operations`（37 字段）+ `struct tty_driver`（21 字段）+ 8 个 `TTY_DRIVER_*` 标志 + 6 个类型** |
| **`include/linux/tty.h`** | **518** | **`struct tty_struct`（46 字段）+ 12 个 `TTY_*` 状态位 + 大部分 API 声明** |
| **`include/linux/tty_ldisc.h`** | **270** | **`struct tty_ldisc_ops`（18 字段）+ `struct tty_ldisc`（只有 2 字段）** |
| **`include/linux/tty_port.h`** | **256** | **`struct tty_port`（22）+ `tty_port_operations`（5）+ `tty_port_client_operations`（3）** |
| **`include/linux/tty_buffer.h`** | **60** | **`struct tty_buffer`（9）+ `struct tty_bufhead`（5）** |
| **`include/linux/tty_flip.h`** | **50** | **全部是 `static inline` 的 flip 辅助** |
| **`include/uapi/linux/tty.h`** | **46** | **31 个行规程编号 `N_*` + `NR_LDISCS`** |
| ⚠️ **`drivers/tty/tty.h`（私有，不在 `include/` 下）** | **119** | **核心内部接口**：**`tty_msg`/`tty_debug`/`tty_warn`/`tty_err`/`tty_info_ratelimited` 五个打印宏 · `TTY_THROTTLE_SAFE`/`TTY_UNTHROTTLE_SAFE` · `tty_set_flow_change` · `tty_ldisc_lock/unlock` · `__stop_tty`/`__start_tty` · `tty_write_lock/unlock` · buffer 内部函数 · `tty_ldisc_hangup`/`tty_ldisc_reinit` 等 ~30 个声明** |

⚠️⚠️ **本节标题说"六个"其实是 8 个头文件（6 个公共 + 1 个 uapi + 1 个私有）** ——
**最容易漏的是 `drivers/tty/tty.h`（119 行）**：
**它在 `drivers/tty/` 下而不是 `include/` 下** ⇒ **树外模块看不到它，
但核心的五个打印宏、`tty_ldisc_lock()` 的超时接口、`__stop_tty`/`__start_tty`
这些"核心内部共享"的东西都在里面。**
⇒ **读 tty 核心时它是必读的**（02 §6.5 / 07 §4.1）。

### §0.4 官方文档：10 个文件 / 886 行

**`Documentation/driver-api/tty/`**：

| 文件 | 行数 | 本文档读了吗 |
|---|---|---|
| `index.rst` | **73** | **全文读过**（它给了整个子系统的概念地图） |
| `tty_driver.rst` | **128** | **全文读过** |
| `tty_ldisc.rst` | **85** | **全文读过**（含那段"demons will eat your computer"的警告） |
| `tty_struct.rst` | **81** | **全文读过** |
| `tty_port.rst` | **70** | **全文读过** |
| `tty_buffer.rst` | **46** | **全文读过** |
| `tty_internals.rst` | 31 | **全文读过** |
| `n_tty.rst` | 22 | **全文读过** |
| `moxa-smartio.rst` | 197 | **未读**（单驱动文档，99 §1.3） |
| `n_gsm.rst` | 153 | **未读**（单 ldisc 文档） |

⚠️ **这批文档的形式很特殊**：**大部分正文是 `.. kernel-doc::` 指令**
⇒ **真正的内容在源码的 kerneldoc 注释里** ——
**所以"读官方文档"在 tty 这里等于"读源码的 kerneldoc"**（99 §1.3）。

**`index.rst` 给出的概念地图（原话，本文档的骨架就来自这段）**：
> *"Every TTY device in a system has a corresponding struct tty_port. These devices are maintained
> by a TTY driver which is struct tty_driver. This structure describes the driver but also contains
> a reference to operations which could be performed on the TTYs. It is struct tty_operations.
> Then, upon open, a struct tty_struct is allocated and lives until the final close."*

### §0.5 97 个核心导出符号

| 文件 | 数 | 主要内容 |
|---|---|---|
| `tty_io.c` | **30** | **驱动注册（5）· 设备注册（3）· hangup（3）· start/stop（2）· kopen（3）· termios（3）· 其他** |
| `tty_port.c` | **26** | **port 生命周期（4）· 设备注册（6）· open/close 辅助（6）· 调制解调器信号（3）· 其他** |
| `tty_ioctl.c` | **13** | **termios 换算 + 流控 + `tty_mode_ioctl`** |
| `tty_buffer.c` | **11** | **flip buffer 的填与推** |
| `tty_ldisc.c` | **7** | **注册（2）+ 引用（3）+ flush/set（2）** |
| `tty_baudrate.c` | 4 | 波特率编解码 |
| `tty_jobctrl.c` | 3 | `tty_check_change` / `get_current_tty` / `tty_get_pgrp` |
| `tty_mutex.c` | 2 | **`tty_lock` / `tty_unlock`** |
| `n_tty.c` | **1** | **只有 `n_tty_inherit_ops`** |
| ⚠️`tty_ldsem.c` / `pty.c` | **各 0** | **纯内部实现** |

重跑：`grep -chE '^EXPORT_SYMBOL' drivers/tty/tty_*.c drivers/tty/n_tty.c drivers/tty/pty.c | paste -sd+ | bc` ⇒ **97**。
⚠️ **口径**：**用 `^EXPORT_SYMBOL` 锚定，避免把注释里提到 `EXPORT_SYMBOL` 的行算进来**
（watchdog 那套就吃过这个亏，99 §6 表 3）。

### §0.6 对外 ABI 面积：本项目做过的子系统里最大的

| 面 | 规模 | 本文档 |
|---|---|---|
| **字符设备** | **`tty_class` + `cdev` + `register_chrdev_region`**（`drivers/tty/tty_io.c:3514` / `:3158` / `:3432`） | §7 |
| **termios** | **`include/uapi/asm-generic/termbits.h` + `termbits-common.h`** | **只讲核心怎么用它**（§6） |
| **ioctl** | **几十个 `TC*` / `TIOC*`** | **只讲分派机制和核心自己处理的那些**（§6.3） |
| **行规程编号** | **31 个 `N_*`（`include/uapi/linux/tty.h:10-41`），`NR_LDISCS = 31`（`:44`）** | §5 |
| **`/proc/tty/`** | **`driver_name` + `proc_show`** | 只提及 |

⚠️ **`N_*` 编号是"永不可复用"的 ABI** ——**官方文档用了很重的措辞**（`tty_ldisc.rst`，原话）：
> *"Do not re-use ldisc numbers as they are part of the userspace ABI and writing over an existing
> ldisc will cause demons to eat your computer."*

---

## §1 先分清：四个核心对象 + 两层可插拔

### §1.1 四个核心对象（这是理解 tty 的第一步）

| 对象 | 一句话 | 生命周期 | 位置 |
|---|---|---|---|
| **`struct tty_driver`** | **"我这个驱动能提供 N 个 tty"** | **模块 init → module exit** | `include/linux/tty_driver.h:442-473`（21 字段） |
| **`struct tty_port`** | **"一个物理端点"** ——**每个 tty 设备都必须有一个** | **设备 probe → remove** | `include/linux/tty_port.h:100-123`（22 字段） |
| **`struct tty_struct`** | **"一次打开会话"** | **第一次 open → 最后一次 close** | `include/linux/tty.h:193-251`（46 字段） |
| **`struct tty_ldisc`** | **"字符怎么被加工"** | **随 tty 存在，可运行期切换** | `include/linux/tty_ldisc.h:250-253`（**只有 2 字段**） |

**官方 `index.rst` 的原话就是这四者的关系**（§0.4 已引用）。

⚠️⚠️ **最容易想错的一条**：**`tty_port` 和 `tty_struct` 不是一对一，也不是同寿命的。**
**`tty_port` 在设备存在期间一直在；`tty_struct` 只在"有人打开着"的时候存在。**
⇒ **详见 02 §1.0.A。**

### §1.2 两层可插拔

| 层 | ops 结构 | 字段数 | 谁实现 | 实现数 |
|---|---|---|---|---|
| **层 1：驱动操作** | **`struct tty_operations`** | **37**（3 个在 `#ifdef CONFIG_CONSOLE_POLL` 里） | **tty 驱动** | **树里几十个** |
| **层 2：行规程** | **`struct tty_ldisc_ops`** | **18** | **ldisc 模块** | **31 个编号已分配** |

⚠️ **还有一个"半层"**：**`struct tty_port_operations`（5 字段）**
⇒ **它不是独立的一层，是驱动为了复用 `tty_port_open/close/hangup` 辅助而实现的回调**（§4.3）。
**加上 `tty_port_client_operations`（3 字段）** ——**那是"数据往哪送"的可替换点**（04 篇）。

**对照**：**regmap 三层 · tty 两层（+两个半层）· watchdog 两层 · phy 一层。**

### §1.3 数据流的三段（记住这个就不会走错方向）

```
   硬件 ──→ 驱动 ──→ ① flip buffer ──→ ② 行规程 ──→ ③ 用户态 read()
                     (tty_buffer.c)    (n_tty.c 等)   (tty_io.c)

   用户态 write() ──→ ③ tty_io.c ──→ ② 行规程 ──→ ① 驱动的 ops->write ──→ 硬件
```

| 段 | 谁负责 | 关键 API |
|---|---|---|
| **① 接收缓冲** | **`drivers/tty/tty_buffer.c`** | **`tty_insert_flip_*` + `tty_flip_buffer_push`** |
| **② 行规程加工** | **`drivers/tty/n_tty.c` 或其他 ldisc** | **`ops->receive_buf2` / `ops->read` / `ops->write`** |
| **③ 用户态接口** | **`drivers/tty/tty_io.c`** | **`tty_fops` 的 read_iter / write_iter / ioctl** |

⚠️ **注意方向的不对称**：**接收方向有 buffer（异步），发送方向没有** ——
**`write()` 是同步地穿过 ldisc 直达驱动**（03 篇）。

---

## §2 `struct tty_operations`：37 个回调

**位置：`include/linux/tty_driver.h:357-403`。按用途分七组。**

### §2.1 组 1：生命周期（7 个）

| 回调 | 签名（形参名） | 说明 | 备注 |
|---|---|---|---|
| `lookup` | `(driver, filp, idx)` | **按下标找 `tty_struct`** | **返回 `struct tty_struct *`** ——**PTY 这类动态设备用** |
| `install` | `(driver, tty)` | **把 `tty_struct` 装进驱动的表** | **`tty_port` 必须在这一步之前或之中就绑好**（官方文档明说，§4.3） |
| `remove` | `(driver, tty)` | 反操作 | |
| `open` | `(tty, filp)` | **每次 open 都调**（不只第一次） | **多数驱动直接转发给 `tty_port_open()`** |
| `close` | `(tty, filp)` | 每次 close 都调 | **转发给 `tty_port_close()`** |
| `shutdown` | `(tty)` | **最后一次 close 之后** | |
| `cleanup` | `(tty)` | **`tty_struct` 释放前的最后清理** | |

### §2.2 组 2：发送（6 个）

| 回调 | 签名 | 说明 | 备注 |
|---|---|---|---|
| `write` | `(tty, buf, count)` | **核心的发送出口** | **返回实际写入的字节数** |
| `put_char` | `(tty, ch)` | **单字节** | **不实现时核心用 `write` 代替**（`drivers/tty/tty_io.c:3143` 的 `tty_put_char`） |
| `flush_chars` | `(tty)` | **把攒着的字符推出去** | |
| `write_room` | `(tty)` | **还能写多少字节** | **返回 `unsigned int`**（6.1 里已改成无符号） |
| `chars_in_buffer` | `(tty)` | 队列里还有多少 | 同上 |
| `flush_buffer` | `(tty)` | 丢弃待发数据 | |

### §2.3 组 3：流控与状态（6 个）

| 回调 | 说明 |
|---|---|
| `throttle` / `unthrottle` | **接收侧流控**：**"我快满了，别再送"/"可以送了"** |
| `stop` / `start` | **发送侧流控**（对应 `^S` / `^Q`） |
| `send_xchar` | **插队发一个流控字符** |
| `hangup` | **载波丢失 / 强制挂断** |

⚠️ **`throttle`/`unthrottle` 与 `stop`/`start` 是两组不同的东西**（05 §7）：
**前者管"我不想再收"，后者管"我暂停发"。**

### §2.4 组 4：termios 与线路参数（4 个）

| 回调 | 说明 |
|---|---|
| `set_termios` | **termios 变了**（第二参数是 `const struct ktermios *old`） |
| `break_ctl` | **发 break 信号** |
| `tiocmget` / `tiocmset` | **读/写调制解调器控制线**（DTR/RTS/CTS…） |

### §2.5 组 5：ioctl 与信息（7 个）

| 回调 | 说明 |
|---|---|
| `ioctl` / `compat_ioctl` | **驱动私有 ioctl** ——**不认识的必须返回 `-ENOIOCTLCMD`**（§8.2） |
| `get_icount` | **中断计数**（`struct serial_icounter_struct`） |
| `get_serial` / `set_serial` | **`struct serial_struct`**（老式串口配置） |
| `show_fdinfo` | **`/proc/<pid>/fdinfo/<fd>` 的内容** |
| `proc_show` | **`/proc/tty/driver/<name>` 的内容** |

### §2.6 组 6：行规程与杂项（4 个）

| 回调 | 说明 |
|---|---|
| `ldisc_ok` | **"这个 ldisc 我支持吗"** ——**可以拒绝某些 ldisc** |
| `set_ldisc` | **ldisc 换了** ——⚠️ **官方文档警告：这个回调里 ldisc 不可用，`tty_ldisc_ref()` 会失败**（§5.4） |
| `wait_until_sent` | **等待发送队列排空** |
| `resize` | **窗口尺寸变了**（`struct winsize`） |

### §2.7 ⚠️ 组 7：三个条件编译的（`CONFIG_CONSOLE_POLL`）

**`include/linux/tty_driver.h:397-401`**：
**`poll_init` / `poll_get_char` / `poll_put_char`。**
⇒ **给 KGDB / early console 这类"不能靠中断"的场景用。**
⚠️ **关掉 `CONFIG_CONSOLE_POLL` 时这三个字段根本不存在**
⇒ **驱动里填它们必须自己包 `#ifdef`。**

**配套的查询函数是 `tty_find_polling_driver()`**（`drivers/tty/tty_io.c:421` 处导出）。

### §2.8 哪些是必需的？

⚠️ **官方文档没有像 watchdog 那样标出"mandatory"** ——
**`tty_operations` 里没有 `/* mandatory */` 注释。**

**但从 `tty_driver.rst` 的一句话能推出必需集**（原话）：
> *"The fields required to be set before calling tty_register_driver() include @driver_name, @name,
> @type, @subtype, @init_termios, and @ops."*

⇒ **文档只规定了 `tty_driver` 的必需**字段**，没规定 `tty_operations` 的必需**回调****。
⇒ **实践上 `open` / `close` / `write` / `write_room` 这四个不实现就没法用**（05 §1）——
⚠️ **但这是本文档的推断，不是文档或代码的强制**（99 §5.3）。

---

## §3 `struct tty_driver`：21 个字段

**位置：`include/linux/tty_driver.h:442-473`。**

### §3.1 官方文档明确的六个必需字段

| 字段 | 说明 |
|---|---|
| `driver_name` | **`/proc/tty` 里用的名字** |
| `name` | **构造 `/dev` 节点名用** |
| `type` | **6 个 `TTY_DRIVER_TYPE_*` 之一**（§3.3） |
| `subtype` | **`SYSTEM_TYPE_*` / `PTY_TYPE_*` / `SERIAL_TYPE_*`** |
| `init_termios` | **每个 tty 的初始 termios**（通常是 `tty_std_termios`） |
| `ops` | **层 1**（§2）——**用 `tty_set_operations()` 设** |

**原话见 §2.8。**

### §3.2 其余 15 个字段按用途

| 组 | 字段 |
|---|---|
| **身份与编号** | `major` · `minor_start` · `name_base` · `num` |
| **引用与所有权** | `kref` · `owner`（`tty_alloc_driver()` 自动设） |
| **设备表** | `cdevs` · `ttys` · `ports` · `termios` |
| **标志与状态** | `flags`（8 个 `TTY_DRIVER_*`，§3.3） |
| **proc** | `proc_entry` |
| **PTY 专用** | ⚠️ **`other`** ——**官方 kerneldoc 明说"only used for the PTY driver"** |
| **私有** | `driver_state` |
| **链表** | `tty_drivers` |

### §3.3 8 个 `TTY_DRIVER_*` 标志

**位置：`include/linux/tty_driver.h:547-554`。**

| 标志 | 值 | 含义 |
|---|---|---|
| `TTY_DRIVER_INSTALLED` | `0x0001` | **核心自己用** |
| `TTY_DRIVER_RESET_TERMIOS` | `0x0002` | **每次 hangup 后重置 termios** |
| `TTY_DRIVER_REAL_RAW` | `0x0004` | **驱动保证不需要字符处理** |
| `TTY_DRIVER_DYNAMIC_DEV` | **`0x0008`** | **官方文档推荐传** ——**不传的话 `tty_register_driver()` 会一次注册所有设备**（§4.1） |
| `TTY_DRIVER_DEVPTS_MEM` | `0x0010` | **devpts 专用** |
| `TTY_DRIVER_HARDWARE_BREAK` | `0x0020` | **`break_ctl` 由硬件实现** |
| `TTY_DRIVER_DYNAMIC_ALLOC` | `0x0040` | **不预分配 `ttys`/`termios` 表** |
| `TTY_DRIVER_UNNUMBERED_NODE` | `0x0080` | **`/dev` 节点名不带编号** |

### §3.4 6 个驱动类型

**`include/linux/tty_driver.h:557-562`**：
**`TTY_DRIVER_TYPE_SYSTEM`(1) · `_CONSOLE`(2) · `_SERIAL`(3) · `_PTY`(4) · `_SCC`(5) · `_SYSCONS`(6)。**

---

## §4 注册 API

### §4.1 驱动侧五步（官方 `index.rst` 给的顺序）

**原话**：
> *"1. Allocate and register a TTY driver (module init) 2. Create and register TTY devices as they
> are probed (probe function) 3. Handle TTY operations and events like interrupts 4. Remove devices
> as they are going away 5. Unregister and free the TTY driver (module exit)"*

| 步 | API | 位置（导出行） |
|---|---|---|
| ① 分配 | **`tty_alloc_driver(lines, flags)`**（宏）/ **`__tty_alloc_driver(lines, owner, flags)`** | `drivers/tty/tty_io.c:3369`（`__tty_alloc_driver`） |
| ② 填字段 | **§3.1 的六个必需 + `tty_set_operations()`** | — |
| ③ 注册驱动 | **`tty_register_driver(driver)`** | `drivers/tty/tty_io.c:3473` |
| ④ 注册设备 | **`tty_register_device(driver, index, device)`** 或 **`tty_port_register_device()`**（推荐） | `tty_io.c:3187` / `tty_port.c:145` |
| ⑤ 注销 | **`tty_unregister_device` → `tty_unregister_driver` → `tty_driver_kref_put`** | `tty_io.c:3303` / `:3489` / `:3408` |

⚠️ **`TTY_DRIVER_DYNAMIC_DEV` 决定第 ④ 步要不要做**（官方 `tty_driver.rst` 原话）：
> *"It is recommended to pass ``TTY_DRIVER_DYNAMIC_DEV`` in flags of tty_alloc_driver(). If it is
> not passed, *all* devices are also registered during tty_register_driver()."*

### §4.2 设备注册的六个变体

**`drivers/tty/tty_port.c` 提供了带 `tty_port` 绑定的版本，官方推荐用它们**：

| API | 位置 | 特点 |
|---|---|---|
| `tty_register_device(driver, index, dev)` | `drivers/tty/tty_io.c:3187` | **最基础** |
| `tty_register_device_attr(...)` | `drivers/tty/tty_io.c:3282` | **多一个 `attribute_group`** |
| `tty_port_register_device(port, driver, index, dev)` | `drivers/tty/tty_port.c:145` | **注册 + 绑 port，一步到位（推荐）** |
| `tty_port_register_device_attr(...)` | `tty_port.c:169` | 同上 + attr |
| `tty_port_register_device_serdev(...)` | `tty_port.c:220` | **serdev 变体** |
| `tty_port_register_device_attr_serdev(...)` | `tty_port.c:201` | 同上 + attr |
| `tty_port_unregister_device(...)` | `tty_port.c:243` | 注销 |

### §4.3 port 与 index 的三种绑定方式（官方文档给的优先级）

**官方 `tty_driver.rst` 原话（逐字）**：
> *"Ideally, the driver uses tty_port_register_device() or tty_port_register_device_attr() instead
> of tty_register_device() … If that is not possible, the driver still can link the tty_port to a
> specific index *before* the actual registration by tty_port_link_device(). If it still does not
> fit, tty_port_install() can be used from the tty_driver.ops.install hook as a last resort."*

| 优先级 | 方式 | 位置 |
|---|---|---|
| **首选** | **`tty_port_register_device()` 系列** | `drivers/tty/tty_port.c:145` 起 |
| **次选** | **注册前调 `tty_port_link_device(port, driver, index)`** | `drivers/tty/tty_port.c:126` |
| **最后手段** | **在 `ops->install` 里调 `tty_port_install(port, driver, tty)`** | `drivers/tty/tty_port.c:744` |

⚠️ **硬要求**：**"`tty_port` 必须在 `ops->install()` 之前或之中让 tty 层知道"**
（官方文档原话："It must be known to the TTY layer at `tty_driver.ops.install()` at latest"）。

⇒ **`tty_port_install()` 主要给 PTY 这类"port 按需分配"的设备用**（04 篇）。

### §4.4 `tty_port` 的生命周期 API

| API | 位置 | 说明 |
|---|---|---|
| `tty_port_init(port)` | `drivers/tty/tty_port.c:106` | **初始化** |
| `tty_port_destroy(port)` | `tty_port.c:284` | 反操作 |
| `tty_port_get(port)` / `tty_port_put(port)` | **`tty_port_put` 在 `tty_port.c:313`**；`tty_port_get` 是 `include/linux/tty_port.h` 里的 inline | **官方推荐用引用计数**；**最后一个 put 负责释放 port 和它所在的驱动私有结构** |
| `tty_port_alloc_xmit_buf` / `tty_port_free_xmit_buf` | `tty_port.c:259` / `:269` | **发送缓冲的分配辅助** |

**官方 `tty_driver.rst` 原话**：
> *"The driver is also recommended to use tty_port's reference counting by tty_port_get() and
> tty_port_put(). The final put is supposed to free the tty_port including the device's private
> struct."*

### §4.5 port 的 open/close 辅助（驱动最该用的一组）

**官方 `tty_port.rst` 原话**：
> *"If the drivers implement tty_port.ops.activate() and tty_port.ops.shutdown(), they can use
> tty_port_open(), tty_port_close(), and tty_port_hangup() in respective tty_struct.ops hooks."*

| API | 位置 | 对应 `tty_operations` 的哪个钩子 |
|---|---|---|
| `tty_port_open(port, tty, filp)` | `drivers/tty/tty_port.c:796` | **`ops->open`** |
| `tty_port_close(port, tty, filp)` | `drivers/tty/tty_port.c:726` | **`ops->close`** |
| `tty_port_hangup(port)` | `drivers/tty/tty_port.c:412` | **`ops->hangup`** |
| `tty_port_block_til_ready(port, tty, filp)` | `tty_port.c:594` | **等载波/DTR 就绪**（`tty_port_open` 内部会用） |
| `tty_port_close_start` / `tty_port_close_end` | `tty_port.c:668` / `:700` | **想自己拆开 close 流程时用** |
| `tty_port_shutdown` | 未导出（内部） | — |

⇒ **`struct tty_port_operations` 只有 5 个字段**（`include/linux/tty_port.h:33-39`），
**其中 `activate` / `shutdown` 是这套辅助的前提**（02 §5.4 / 05 §2）。

### §4.6 调制解调器信号（3 个）

| API | 位置 |
|---|---|
| `tty_port_carrier_raised(port)` | `drivers/tty/tty_port.c:453` |
| `tty_port_raise_dtr_rts(port)` | `tty_port.c:468` |
| `tty_port_lower_dtr_rts(port)` | `tty_port.c:483` |

⇒ **它们转发到 `tty_port_operations` 的 `carrier_raised` / `dtr_rts`。**

---

## §5 行规程（层 2）

### §5.1 注册与注销

| API | 位置 | 说明 |
|---|---|---|
| `tty_register_ldisc(new_ldisc)` | `drivers/tty/tty_ldisc.c:72` | **注册**；**编号来自 `new_ldisc->num`** |
| `tty_unregister_ldisc(ldisc)` | `drivers/tty/tty_ldisc.c:92` | — |

⚠️⚠️ **官方文档的三条硬规则**（`tty_ldisc.rst`，逐字）：
1. *"At the point of registration the discipline must be ready to use and it is possible it
   will get used before the call returns success."*
2. *"Do not re-use ldisc numbers as they are part of the userspace ABI…"*
3. *"You must not re-register over the top of the line discipline even with the same data…"*

**编号范围检查在代码里**：
```c
/* drivers/tty/tty_ldisc.c:63 */
	if (new_ldisc->num < N_TTY || new_ldisc->num >= NR_LDISCS)
```
⇒ **`NR_LDISCS = 31`**（`include/uapi/linux/tty.h:44`）⇒ **合法编号 0..30。**

### §5.2 31 个已分配编号（`include/uapi/linux/tty.h:10-41`）

| 编号 | 名字 | 编号 | 名字 |
|---|---|---|---|
| **0** | **`N_TTY`（默认）** | 16 | `N_GIGASET_M101` |
| 1 | `N_SLIP` | 17 | `N_SLCAN` |
| 2 | `N_MOUSE` | 18 | `N_PPS` |
| 3 | `N_PPP` | 19 | `N_V253` |
| 4 | `N_STRIP` | 20 | `N_CAIF` |
| 5 | `N_AX25` | **21** | **`N_GSM0710`**（`drivers/tty/n_gsm.c`） |
| 6 | `N_X25` | 22 | `N_TI_WL` |
| 7 | `N_6PACK` | 23 | `N_TRACESINK` |
| 8 | `N_MASC`（保留） | 24 | `N_TRACEROUTER` |
| 9 | `N_R3964`（保留） | 25 | `N_NCI` |
| 10 | `N_PROFIBUS_FDL`（保留） | 26 | `N_SPEAKUP` |
| 11 | `N_IRDA` | **27** | **`N_NULL`（错误兜底）** |
| 12 | `N_SMSBLOCK` | 28 | `N_MCTP` |
| **13** | **`N_HDLC`**（`drivers/tty/n_hdlc.c`） | **29** | **`N_DEVELOPMENT`**（注释："Manual out-of-tree testing"） |
| 14 | `N_SYNC_PPP` | 30 | `N_CAN327` |
| 15 | `N_HCI` | — | — |

⚠️ **三个"保留但没实现"的编号**：**`N_MASC`(8) · `N_R3964`(9) · `N_PROFIBUS_FDL`(10)**
——**注释里写着 "Reserved for …"** ⇒ **编号占了位但树里没有实现**（04 §5）。

### §5.3 ldisc 的三级兜底（官方文档明说）

**`tty_ldisc.rst` 原话**：
> *"The default line discipline is N_TTY. It is also a fallback if establishing any other
> discipline for a tty fails. If even N_TTY fails, N_NULL takes over. That never fails, but also
> does not process any characters -- it throws them away."*

⇒ **请求的 ldisc → `N_TTY` → `N_NULL`。**
⚠️ **落到 `N_NULL` 意味着"这个 tty 能打开但吞掉所有字符"** ——
**表现是"设备存在但完全不响应"**（07 篇）。

### §5.4 三个引用函数 + 一个警告

| API | 位置 | 说明 |
|---|---|---|
| `tty_ldisc_ref_wait(tty)` | `drivers/tty/tty_ldisc.c:250` | **拿引用，会等** |
| `tty_ldisc_ref(tty)` | `drivers/tty/tty_ldisc.c:271` | **拿引用，不等（可能返回 NULL）** |
| `tty_ldisc_deref(ld)` | `drivers/tty/tty_ldisc.c:284` | **放引用** |

⚠️⚠️ **官方文档的警告（逐字）**：
> *"A caution: The tty_ldisc_ops.open(), tty_ldisc_ops.close() and tty_driver.set_ldisc()
> functions are called with the ldisc unavailable. Thus tty_ldisc_ref() will fail in this situation
> if used within these functions."*

⇒ **在这三个回调里调 `tty_ldisc_ref()` 一定拿不到** ——**必须知道这一条。**

### §5.5 ⚠️ 两个不同的 ldisc 引用计数（官方文档专门警告）

**`tty_ldisc.rst` 原话（逐字）**：
> *"Heed this warning: the reference count field of the registered copies of the tty_ldisc structure
> in the ldisc table counts the number of lines using this discipline. The reference count of the
> tty_ldisc structure within a tty counts the number of active users of the ldisc at this instant."*

| 计数 | 数的是什么 |
|---|---|
| **ldisc 表里那份** | **有多少条线在用这个 ldisc** |
| **tty 内那份** | **此刻有多少执行线程在 ldisc 方法里** |

⇒ **两个数完全不同的东西，名字都叫"引用计数"** ——**02 §1.0 会单独破歧义。**

### §5.6 其他两个导出

| API | 位置 | 说明 |
|---|---|---|
| `tty_set_ldisc(tty, disc)` | `drivers/tty/tty_ldisc.c:598` | **切换 ldisc**（`TIOCSETD` 的实现） |
| `tty_ldisc_flush(tty)` | `drivers/tty/tty_ldisc.c:393` | **清空 ldisc 的缓冲** |

**`n_tty.c` 唯一的导出符号是 `n_tty_inherit_ops`**（`drivers/tty/n_tty.c:2544`）
⇒ **给"想在 N_TTY 基础上改几个回调"的 ldisc 用**（04 篇）。

---

## §6 flip buffer 与 termios

### §6.1 flip buffer：驱动往上送数据的唯一通路

**官方 `tty_buffer.rst` 原话**：
> *"Drivers are supposed to fill the buffer by one of those functions below and then flip the
> buffer, so that the data are passed to line discipline for further processing."*

| API | 位置 | 说明 |
|---|---|---|
| `tty_buffer_request_room(port, size)` | `drivers/tty/tty_buffer.c:305` | **先要空间** |
| `tty_insert_flip_string_fixed_flag(port, chars, flag, size)` | `tty_buffer.c:344` | **一串字符，共用一个标志** |
| `tty_insert_flip_string_flags(port, chars, flags, size)` | `tty_buffer.c:382` | **每个字符一个标志** |
| `__tty_insert_flip_char(port, ch, flag)` | `tty_buffer.c:408` | **单字符（慢路径）** |
| `tty_prepare_flip_string(port, chars, size)` | `tty_buffer.c:439` | **拿一块可直接写的空间** |
| `tty_flip_buffer_push(port)` | `tty_buffer.c:606` | **推给 ldisc** |
| `tty_ldisc_receive_buf(ld, p, f, count)` | `tty_buffer.c:465` | **绕过 buffer 直接送 ldisc** |
| `tty_buffer_space_avail(port)` | `tty_buffer.c:100` | 剩余空间 |
| `tty_buffer_set_limit(port, limit)` | `tty_buffer.c:677` | **改缓冲上限** |
| `tty_buffer_lock_exclusive` / `tty_buffer_unlock_exclusive` | `tty_buffer.c:59` / `:81` | ⚠️ **官方文档原话："These are used only in special circumstances. Avoid them."** |

⚠️ **`include/linux/tty_flip.h`（50 行）全是 inline 包装** ——
**例如 `tty_insert_flip_char()` 是宏/inline，最终落到 `__tty_insert_flip_char()`。**
⇒ **驱动实际写的是 inline 那层**（05 §1.3）。

### §6.2 termios 相关的 13 个导出（`tty_ioctl.c`）

| 组 | API |
|---|---|
| **换算** | **`tty_termios_baud_rate` · `tty_termios_input_baud_rate` · `tty_termios_encode_baud_rate` · `tty_encode_baud_rate`**（这 4 个在 `tty_baudrate.c`） |
| **比较与复制** | **`tty_termios_copy_hw`（`tty_ioctl.c:262`）· `tty_termios_hw_change`（`:281`）** |
| **字符/帧长度** | **`tty_get_char_size`（`:304`）· `tty_get_frame_size`（`:328`）** |
| **设置** | **`tty_set_termios`（`:376`）** |
| **ioctl 分派** | **`tty_mode_ioctl`（`:910`）· `tty_perform_flush`（`:953`）· `n_tty_ioctl_helper`（`:1004`）** |
| **流控与查询** | **`tty_chars_in_buffer`（`:64`）· `tty_write_room`（`:83`）· `tty_driver_flush_buffer`（`:98`）· `tty_unthrottle`（`:122`）· `tty_wait_until_sent`（`:216`）** |

⚠️ **`tty_std_termios` 是一个导出的全局变量**（`drivers/tty/tty_io.c:134`）
⇒ **驱动的 `init_termios` 通常直接赋值它。**

### §6.3 ioctl 的三级分派

```
用户态 ioctl()
  │
  ├─ ① tty_io.c 的 tty_ioctl()：核心自己认识的（TIOCGWINSZ / TIOCSETD / TIOCSTI …）
  │
  ├─ ② 驱动的 ops->ioctl()：返回 -ENOIOCTLCMD 表示"不认识"
  │
  └─ ③ ldisc 的 ops->ioctl()：n_tty 用 n_tty_ioctl_helper()
```

⚠️ **`-ENOIOCTLCMD` 是三级之间的"我不认识"信号**（§8.2）——
**核心 `.c` 里共 10 处**（§8.1）。

---

## §7 字符设备与 kopen

### §7.1 字符设备的建立

| 动作 | 位置 |
|---|---|
| **`tty_class = class_create(THIS_MODULE, "tty")`** | `drivers/tty/tty_io.c:3514` |
| **`cdev_add(driver->cdevs[index], dev, count)`** | `drivers/tty/tty_io.c:3158`（在 `tty_cdev_add()` 里） |
| **`alloc_chrdev_region(&dev, driver->minor_start, ...)`** | `drivers/tty/tty_io.c:3424`（`major == 0` 时自动分配） |
| **`register_chrdev_region(dev, driver->num, driver->name)`** | `drivers/tty/tty_io.c:3432`（指定了 major 时） |

⇒ **每个 tty 驱动有自己的 cdev 区间** ——**不像 watchdog 那样共用一个。**

### §7.2 从内核态打开 tty：三个 kopen API

**官方 `tty_internals.rst` 专门讲这一组**：

| API | 位置 | 说明 |
|---|---|---|
| `tty_kopen_exclusive(device)` | `drivers/tty/tty_io.c:2004` | **独占打开** |
| `tty_kopen_shared(device)` | `drivers/tty/tty_io.c:2019` | **共享打开** |
| `tty_kclose(tty)` | `drivers/tty/tty_io.c:1674` | **关闭** |

⇒ **典型使用者是 console / n_gsm / 蓝牙 HCI 这类"内核自己要用一条 tty"的场景。**

### §7.3 其他内核侧工具

| API | 位置 | 说明 |
|---|---|---|
| `tty_dev_name_to_number(name, number)` | `drivers/tty/tty_io.c:371` | **`"ttyS0"` → `dev_t`** |
| `tty_release_struct(tty, idx)` | `drivers/tty/tty_io.c:1703` | **释放 `tty_struct`** |
| `tty_get_icount(tty, icount)` | `drivers/tty/tty_io.c:2580` | **拿中断计数** |
| `tty_devnum(tty)` | `drivers/tty/tty_io.c:3495` | — |
| `do_SAK(tty)` | `drivers/tty/tty_io.c:3065` | **Secure Attention Key**：**杀掉所有用这个 tty 的进程** |

### §7.4 hangup 与 start/stop

| API | 位置 | 说明 |
|---|---|---|
| `tty_hangup(tty)` | `drivers/tty/tty_io.c:688` | **异步挂断**（排一个 work） |
| `tty_vhangup(tty)` | `drivers/tty/tty_io.c:703` | **同步挂断** |
| `tty_hung_up_p(filp)` | `drivers/tty/tty_io.c:748` | **这个 fd 是不是已经挂断了** |
| `stop_tty(tty)` / `start_tty(tty)` | `drivers/tty/tty_io.c:781` / `:812` | **发送侧流控**（对应 `^S`/`^Q`） |
| `tty_wakeup(tty)` | `drivers/tty/tty_io.c:530` | **驱动在"能写了"时叫醒等待者** |
| `tty_port_tty_wakeup(port)` | `drivers/tty/tty_port.c:437` | **port 版本（更常用）** |
| `tty_port_tty_hangup(port, check_clocal)` | `drivers/tty/tty_port.c:427` | 同上 |

⚠️ **驱动应该用 `tty_port_*` 版本** ——**因为它们自己处理"tty 可能已经不存在"的情况**（05 §7）。

---

## §8 错误码语义

### §8.1 普查（12 个核心 `.c`）

| 码 | 次数 | 主要语义 |
|---|---|---|
| `-EFAULT` | **35** | **`copy_to/from_user` 失败** ——**最多的一个，反映 ioctl 面积大** |
| `-EIO` | **26** | **设备已挂断 / `TTY_IO_ERROR` 置位** |
| `-EINVAL` | **21** | 参数非法 |
| `-ENOTTY` | **19** | **"不是 tty"或"这个 ioctl 不支持"** |
| `-ENOMEM` | 13 | 分配失败 |
| `-EPERM` | **12** | **权限**（作业控制相关） |
| `-ERESTARTSYS` | **12** | **被信号打断，需要重启系统调用** |
| `-ENOIOCTLCMD` | **10** | **三级 ioctl 分派的"我不认识"** |
| `-EAGAIN` | 9 | **非阻塞模式下没数据 / 写不进** |
| `-ENODEV` | 7 | — |
| `-EBUSY` | 6 | — |
| `-EINTR` | 4 | — |
| `-ENXIO` / `-EOPNOTSUPP` | 各 1 | — |

重跑：
`C="drivers/tty/tty_io.c drivers/tty/tty_ldisc.c drivers/tty/tty_port.c drivers/tty/tty_buffer.c drivers/tty/tty_ioctl.c drivers/tty/tty_jobctrl.c drivers/tty/tty_ldsem.c drivers/tty/tty_mutex.c drivers/tty/tty_baudrate.c drivers/tty/tty_audit.c drivers/tty/n_tty.c drivers/tty/pty.c"; for e in EFAULT EIO EINVAL ENOTTY ENOMEM EPERM ERESTARTSYS ENOIOCTLCMD EAGAIN ENODEV EBUSY EINTR ENXIO EOPNOTSUPP; do echo -n "-$e "; grep -rho -- "-$e" $C | wc -l; done`

### §8.2 ⚠️`-ENOIOCTLCMD` vs `-ENOTTY`（三级分派的关键）

| 码 | 谁返回 | 谁看到 |
|---|---|---|
| `-ENOIOCTLCMD` | **驱动的 `ops->ioctl` 或 ldisc 的 `ops->ioctl`** | **只有上一级看到** ⇒ **上一级会继续尝试别的处理者** |
| `-ENOTTY` | **最终没人认识时由核心返回** | **用户态** |

⚠️ **驱动返回 `-ENOTTY` 会截断分派链** ——**与 watchdog 那套完全同一个机理**（05 §7）。

### §8.3 `-EIO` 的两个主要来源

| 来源 | 说明 |
|---|---|
| **`TTY_IO_ERROR` 置位** | **设备已挂断或从未成功打开** ⇒ **几乎所有操作返回 `-EIO`** |
| **驱动的 ops 返回失败** | **透传** |

**`TTY_IO_ERROR` 是 12 个 `TTY_*` 状态位之一**（`include/linux/tty.h:315`，02 §5.2）。

### §8.4 设计债：10 处（本项目最多）

**重跑 `grep -nE '(FIXME|TODO|REVISIT|HACK|\bXXX\b)' <12 个核心 .c>` ⇒ 10 处。**

| 位置 | 内容（逐字节选） |
|---|---|
| `drivers/tty/n_tty.c:1348` | `XXX are EOL_CHAR and EOL2_CHAR echoed?!?` |
| `drivers/tty/n_tty.c:1358` | `XXX does PARMRK doubling happen for` … |
| `drivers/tty/tty_jobctrl.c:341` | `FIXME: Review locking here. The tty_lock never covered any race` … |
| `drivers/tty/tty_ioctl.c:239` | `FIXME: What should we do for i/ospeed` |
| `drivers/tty/tty_ioctl.c:353` | `FIXME: we need to decide on some locking/ordering semantics` … |
| `drivers/tty/tty_ioctl.c:540` | `FIXME: Arguably if tmp_termios == tty->termios AND the` … |
| `drivers/tty/tty_io.c:1026` | `FIXME! Have Al check this!` |
| `drivers/tty/tty_io.c:2267` | `FIXME: does not honour flow control ??` |
| `drivers/tty/tty_io.c:2813` | `compat_ulong_t iomap_base FIXME` |
| `drivers/tty/tty_io.c:2995` | `via AF_UNIX socket, close it and later fetch from socket. FIXME.` |

⇒ **有 3 处直接说的是"锁的语义没定清"**（`tty_jobctrl.c:341` · `tty_ioctl.c:353` + 一处间接）
⇒ **这是 tty 最著名的历史包袱**（03 Part C）。

---

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| **四个对象的关系与易错类比** | `02-datastructures.md` §1.0 |
| open / read / write / ldisc 切换的完整流程 | `03-internals.md` |
| PTY / n_tty / 其他 ldisc | `04-extensions.md` |
| **驱动骨架 + ldisc 骨架** | `05-usage.md` |
| DT 参与度（结论：极低） | `06-devicetree.md` |
| **`/proc/tty/` + 观测手段** | `07-debug.md` |
| 为什么有 port 又有 struct、为什么两个引用计数 | `08-feynman.md` |
