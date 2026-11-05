---
title: 扩展机制
---

# tty 04：子框架

## §0 组织轴

**本篇一节一个独立子框架，互不调用。**
**tty 的"子框架"分两类**：**① 行规程（层 2 的实现）；② 特殊设备（PTY）。**

### §0.1 分诊表：哪些真会碰到

| # | 子框架 | 是什么 | 规模 | arm64 是否相关 | 本篇 |
|---|---|---|---|---|---|
| 1 | **`n_tty`（默认行规程）** | **POSIX 终端语义：回显 / 规范模式 / 特殊字符 / 信号** | **2549 行** | **总是**（每个 tty 默认都用它） | §1 |
| 2 | **`pty`（伪终端）** | **两个配对的 tty，一端是"硬件"另一端是"终端"** | **947 行** | **总是**（ssh / 终端模拟器 / `screen` 全靠它） | §2 |
| 3 | **`n_null`（兜底行规程）** | **永不失败、什么都不做** | **69 行** | **相关**（错误路径） | §3 |
| 4 | **flip buffer** | **接收方向的双缓冲机制** | **698 行** | **总是** | §4 |
| 5 | **其余 17 个 ldisc** | **SLIP / PPP / 蓝牙 HCI / GSM 0710 / CAN 等** | — | **按硬件而定** | §5 |
| 6 | **`tty_port_client_operations`** | **"数据不走 ldisc，直接给我"的替换点** | **3 个回调** | 少数 | §6 |

### §0.2 各子框架挂在 03 时间线的哪一点

| 子框架 | 挂载点 |
|---|---|
| **`n_tty`** | **`tty_init_dev()` → `tty_ldisc_setup()`**（03 §A5 第 ⑦ 步）——**默认就是它** |
| **`pty`** | **它是两个 `tty_driver`**（不挂在别的流程上，自己就是一对驱动） |
| **`n_null`** | **`tty_ldisc_setup()` / `tty_set_ldisc()` 的失败兜底**（03 §A9） |
| **flip buffer** | **`port->buf`，在 `tty_port_init()` 时初始化**（02 §5.3） |
| **其余 ldisc** | **用户态 `ioctl(TIOCSETD)` 主动切换** |

---

## §1 `n_tty`：默认行规程（2549 行）

### §1.1 它的 `tty_ldisc_ops` 实例（全文引用）

```c
/* drivers/tty/n_tty.c:2514-2530 —— 逐字 */
static struct tty_ldisc_ops n_tty_ops = {
	.owner		 = THIS_MODULE,
	.num		 = N_TTY,
	.name            = "n_tty",
	.open            = n_tty_open,
	.close           = n_tty_close,
	.flush_buffer    = n_tty_flush_buffer,
	.read            = n_tty_read,
	.write           = n_tty_write,
	.ioctl           = n_tty_ioctl,
	.set_termios     = n_tty_set_termios,
	.poll            = n_tty_poll,
	.receive_buf     = n_tty_receive_buf,
	.write_wakeup    = n_tty_write_wakeup,
	.receive_buf2	 = n_tty_receive_buf2,
	.lookahead_buf	 = n_tty_lookahead_flow_ctrl,
};
```

**实现了 14 个字段（含 3 个元数据）** ——**18 个里没实现的 4 个是**：
**`compat_ioctl` · `hangup` · `dcd_change` · （无第四个，实际是 `18 - 14 = 4`：
`compat_ioctl` / `hangup` / `dcd_change` 三个 + 元数据齐全）。**

⚠️ **精确说**：**18 个字段里 `n_tty` 实现了 15 个**（3 元数据 + 12 回调），
**没实现的 3 个回调是 `compat_ioctl` / `hangup` / `dcd_change`。**

### §1.2 它做的四类事（官方 `n_tty.rst` 只有一句话，细节全在代码）

**官方 `n_tty.rst` 原话（全文只有这一句实质内容）**：
> *"The default (and fallback) TTY line discipline. It tries to handle characters as per POSIX."*

⇒ **"as per POSIX" 四个字背后是 2549 行代码。分四类**：

| 类 | 做什么 | termios 里的开关 |
|---|---|---|
| **① 规范模式（canonical）** | **按行缓冲，支持退格 / 删词 / 重打** | **`ICANON`** |
| **② 回显** | **把收到的字符回显给用户** | **`ECHO` / `ECHOE` / `ECHOK` / `ECHONL` / `ECHOCTL` / `ECHOPRT` / `ECHOKE`** |
| **③ 特殊字符与信号** | **`^C` → `SIGINT` · `^Z` → `SIGTSTP` · `^\` → `SIGQUIT`** | **`ISIG`** |
| **④ 输入/输出转换** | **`\n` ↔ `\r\n` · 大小写 · 奇偶标记** | **`ICRNL` / `ONLCR` / `OPOST` / `PARMRK` 等** |

⚠️ **本文档没有逐条核对 `n_tty` 与 POSIX 的一致性**（99 §5.2）——
**源码自己也有两处 `XXX` 在问这件事**（01 §8.4）：
**`drivers/tty/n_tty.c:1348` 的 `XXX are EOL_CHAR and EOL2_CHAR echoed?!?`。**

### §1.3 `n_tty_inherit_ops()`：唯一的导出符号

```c
/* drivers/tty/n_tty.c:2539-2544 —— 全文 */
void n_tty_inherit_ops(struct tty_ldisc_ops *ops)
{
	*ops = n_tty_ops;
	ops->owner = NULL;
}
EXPORT_SYMBOL_GPL(n_tty_inherit_ops);
```

**kerneldoc 原话**：*"Enables a 'subclass' line discipline to 'inherit' N_TTY methods."*

| 事实 | 说明 |
|---|---|
| **整体结构体赋值** | **一次拷走全部 18 个字段** |
| **`ops->owner = NULL`** | **关键的一行**：**调用者必须自己填 `owner`，否则模块引用计数会记到 `n_tty` 头上** |
| **用法** | **先 `n_tty_inherit_ops(&my_ops)`，再覆盖想改的几个回调 + 填 `owner`/`num`/`name`** |

**树里的使用者**（重跑 `grep -rln 'n_tty_inherit_ops' --include=*.c .`）：
**`drivers/accessibility/speakup/spk_ttyio.c` 等**（§5）。

### §1.4 `receive_buf` 与 `receive_buf2` 都实现了

| 回调 | n_tty 的实现 | 返回 |
|---|---|---|
| `receive_buf` | `n_tty_receive_buf`（`drivers/tty/n_tty.c:2526` 处赋值） | **`void`** |
| `receive_buf2` | `n_tty_receive_buf2`（`:2528`） | **`int` ——实际吃掉的字节数** |

⇒ **核心优先用 `receive_buf2`**（因为它能表达背压）
⇒ **`receive_buf` 保留是为了兼容只实现了它的老 ldisc**（02 §1.0.E）。

**`lookahead_buf` 的实现叫 `n_tty_lookahead_flow_ctrl`**（`:2529`）
⇒ **名字直接说明了用途**：**提前扫一遍 buffer 找 `^S`/`^Q`，
这样流控字符不用等到正常处理时才生效。**

---

## §2 `pty`：伪终端（947 行）

### §2.1 它是"两个驱动、四套 ops"

**`drivers/tty/pty.c` 注册了两对驱动**：

| 对 | 驱动变量 | ops | 位置 | 用途 |
|---|---|---|---|---|
| **BSD 风格（legacy）** | **`pty_driver` + `pty_slave_driver`** | **`master_pty_ops_bsd`（`drivers/tty/pty.c:498`）· `slave_pty_ops_bsd`（`:513`）** | 分配于 `:536` / `:543` | **`/dev/ptyXX` + `/dev/ttyXX`**（老接口） |
| **Unix98（现代）** | **`ptm_driver` + `pts_driver`** | **`ptm_unix98_ops`（`:745`）· `pty_unix98_ops`（`:762`）** | 分配于 `:875` / `:883` | **`/dev/ptmx` + `/dev/pts/N`** |

⇒ **一个 947 行的文件里有 4 个 `tty_operations` 实例。**

### §2.2 `driver->other`：全内核唯一用它的地方

```c
/* drivers/tty/pty.c:563 与 :576 —— BSD 对 */
	pty_driver->other = pty_slave_driver;
	…
	pty_slave_driver->other = pty_driver;
/* drivers/tty/pty.c:905 与 :918 —— Unix98 对 */
	ptm_driver->other = pts_driver;
	…
	pts_driver->other = ptm_driver;
```

**`tty_driver.other` 的 kerneldoc 原话**（`include/linux/tty_driver.h:424`）：
> *"@other: driver of the linked tty; only used for the PTY driver"*

⇒ **这个字段的存在就是为了 PTY** ——**四处赋值，两两互指。**
**对应地，`tty_struct.link`（`include/linux/tty.h:234`）也只有 PTY 用**（02 §2）。

### §2.3 PTY 的三个特殊之处

| # | 特殊 | 依据 |
|---|---|---|
| **1** | **master 端永远不能当控制终端** | **`tty_open()` 的 `noctty` 判断里显式排除**（`drivers/tty/tty_io.c:2174-2175`，03 §A4.3） |
| **2** | **termios 在第一次 open 时重置，不跨复用继承** | **`tty_open()` 的 kerneldoc 原话**：*"The termios state of a pty is reset on the first open so that settings don't persist across reuse."*（03 §A10.2） |
| **3** | **`tty_port` 按需分配** | **官方 `tty_driver.rst` 原话**：*"tty_port_install() can be used from the tty_driver.ops.install hook as a last resort. The last one is dedicated mostly for in-memory devices like PTY where tty_ports are allocated on demand."*（01 §4.3） |

⇒ **PTY 是"三种 port 绑定方式"里唯一需要第三种（`tty_port_install()`）的场景。**

### §2.4 另外两个 PTY 相关的状态位

| 位 | 位号 | 含义 |
|---|---|---|
| `TTY_OTHER_CLOSED` | **2**（`include/linux/tty.h:316`） | **配对的一端关了** ⇒ **本端读到 EOF / 写到 `-EIO`** |
| `TTY_PTY_LOCK` | **16**（`include/linux/tty.h:320`） | **`TIOCSPTLCK`**：**slave 端被锁住，还不能打开** |

⇒ **`TIOCSPTLCK` 是 `openpty()` 流程的关键**：
**先开 master、锁住 slave、`grantpt`/`unlockpt` 之后才让 slave 可开。**

### §2.5 `TTY_DRIVER_DEVPTS_MEM`

**`include/linux/tty_driver.h:551` 的 `TTY_DRIVER_DEVPTS_MEM = 0x0010`**
⇒ **专给 Unix98 PTY 用** ——**表示"设备节点由 devpts 文件系统在内存里生成"。**
⚠️ **本文档没有覆盖 `fs/devpts/`**（99 §2.5）。

---

## §3 `n_null`：兜底行规程（69 行，全文可引）

### §3.1 它的全部实现

```c
/* drivers/tty/n_null.c —— ops 实例，逐字 */
static struct tty_ldisc_ops null_ldisc = {
	.owner		=	THIS_MODULE,
	.num		=	N_NULL,
	.name		=	"n_null",
	.open		=	n_null_open,		/* return 0; */
	.close		=	n_null_close,		/* 空函数 */
	.read		=	n_null_read,		/* return -EOPNOTSUPP; */
	.write		=	n_null_write,		/* return -EOPNOTSUPP; */
	.receive_buf	=	n_null_receivebuf	/* 空函数 —— 字符被丢弃 */
};
```

⇒ **18 个字段里只实现了 8 个**（3 元数据 + 5 回调）。

### §3.2 ⚠️ 官方文档的说法要精确化

**官方 `tty_ldisc.rst` 原话**：
> *"If even N_TTY fails, N_NULL takes over. That never fails, but also does not process any
> characters -- it throws them away."*

⚠️ **"throws them away" 只对接收方向准确**：

| 操作 | 实际行为 |
|---|---|
| **`open`** | **`return 0`** ——**确实"never fails"** |
| **接收（`receive_buf`）** | **空函数** ⇒ **字符被丢弃，确实是"throws away"** |
| ⚠️ **`read`** | ⚠️ **返回 `-EOPNOTSUPP`** ——**不是"读到 0 字节"，而是报错** |
| ⚠️ **`write`** | ⚠️ **返回 `-EOPNOTSUPP`** ——**同样是报错** |

⇒ **落到 `n_null` 的 tty 表现是**：**能打开、`read`/`write` 都返回 `-EOPNOTSUPP`、
硬件送上来的字符静默消失。**
⇒ **这是 07 篇一个重要的症状特征**：**`-EOPNOTSUPP` + 设备存在 ⇒ 怀疑 ldisc 落到了 `n_null`。**

### §3.3 ⚠️ 它的 init 里有一个 `BUG_ON`

```c
/* drivers/tty/n_null.c —— n_null_init() 全文 */
static int __init n_null_init(void)
{
	BUG_ON(tty_register_ldisc(&null_ldisc));
	return 0;
}
```

⚠️ **这是 tty 核心层唯一的 `BUG_ON`**（在 12 个核心文件里，07 §4）
⇒ **理由可以理解**：**`n_null` 是整个 ldisc 兜底链的最后一环，
它注册不上意味着"错误路径没有出口"** ⇒ **作者选择直接 panic。**

**`MODULE_ALIAS_LDISC(N_NULL)`** ——**让 `request_module` 能按编号自动加载**（§5.3）。

---

## §4 flip buffer（698 行）

### §4.1 为什么叫 "flip"

**"flip" 来自"翻页"**：**驱动往一块 buffer 里写，ldisc 从另一块读，
写满就交换（翻页）** ——**这样两侧不必互相等待。**

**实现上是一个 buffer 链表 + 两个指针**（`struct tty_bufhead`，`include/linux/tty_buffer.h:38-48`，5 字段）：
**`head`（ldisc 正在读的）· `tail`（驱动正在写的）等。**

⚠️ **本文档没有逐字段核对 `tty_bufhead` 与 `tty_buffer`**（99 §5.2）——
**只登记了字段数（5 和 9）。**

### §4.2 三段异步

```
   驱动（中断上下文）
     ├─ tty_insert_flip_char(port, ch, flag)      include/linux/tty_flip.h（inline）
     │    └─ 空间不够 ⇒ __tty_insert_flip_char()  drivers/tty/tty_buffer.c:408
     └─ tty_flip_buffer_push(port)                drivers/tty/tty_buffer.c:606
          └─ 只排一个 work，立即返回

   ─────────── 异步边界 ───────────

   work（进程上下文）
     └─ flush_to_ldisc()（tty_buffer.c 内部）
          ├─ tty_ldisc_ref(tty)
          ├─ ld->ops->receive_buf2(tty, cp, fp, count)   层 2
          └─ tty_ldisc_deref(ld)
```

⇒ **`tty_flip_buffer_push()` 可以在中断上下文调**（这是它存在的全部意义）。

### §4.3 五个"填"的函数怎么选

| API | 位置 | 什么时候用 |
|---|---|---|
| **`tty_insert_flip_char(port, ch, flag)`** | **`include/linux/tty_flip.h`（inline）** | **单字符，最常用** ——**inline 里有快路径** |
| `__tty_insert_flip_char(port, ch, flag)` | `drivers/tty/tty_buffer.c:408` | **inline 的慢路径**（需要新 buffer 时） |
| `tty_insert_flip_string_fixed_flag(port, chars, flag, size)` | `drivers/tty/tty_buffer.c:344` | **一串字符，标志相同**（最高效） |
| `tty_insert_flip_string_flags(port, chars, flags, size)` | `drivers/tty/tty_buffer.c:382` | **每字符一个标志** |
| `tty_prepare_flip_string(port, chars, size)` | `drivers/tty/tty_buffer.c:439` | **拿一块可直接 DMA 写入的空间** |

⚠️ **`include/linux/tty_flip.h` 只有 50 行，全是 inline** ——
**驱动实际写的是 inline 那层，`grep` 找 `tty_insert_flip_char` 的实现会找到宏/inline，不是函数**
（99 §4）。

### §4.4 两个"绕过"的函数

| API | 位置 | 说明 |
|---|---|---|
| `tty_ldisc_receive_buf(ld, p, f, count)` | `drivers/tty/tty_buffer.c:465` | **绕过 buffer，直接调 ldisc** ——**调用者必须已持 ldisc 引用** |
| ⚠️`tty_buffer_lock_exclusive` / `tty_buffer_unlock_exclusive` | `drivers/tty/tty_buffer.c:59` / `:81` | ⚠️ **官方文档原话："These are used only in special circumstances. Avoid them."** |

### §4.5 缓冲上限

| API | 位置 | 说明 |
|---|---|---|
| `tty_buffer_space_avail(port)` | `drivers/tty/tty_buffer.c:100` | **还能塞多少** |
| `tty_buffer_set_limit(port, limit)` | `drivers/tty/tty_buffer.c:677` | **改上限** |

⇒ **上限存在的理由**：**buffer 挂在 `port` 上，没人打开 tty 时数据也会积累**（02 §5.3）
⇒ **必须有一个天花板。**

---

## §5 其余 17 个行规程

### §5.1 全树 20 个 `tty_ldisc_ops` 实例

**重跑 `grep -rn 'struct tty_ldisc_ops [a-z_0-9]* = {' --include=*.c . | wc -l` ⇒ **20**。**

| 目录 | 文件 | 对应 `N_*` |
|---|---|---|
| **`drivers/tty/`（4 个）** | **`n_tty.c` · `n_null.c` · `n_hdlc.c` · `n_gsm.c`** | **`N_TTY`(0) · `N_NULL`(27) · `N_HDLC`(13) · `N_GSM0710`(21)** |
| **网络（8 个）** | **`net/slip/slip.c` · `net/hamradio/6pack.c` · `net/hamradio/mkiss.c` · `net/can/can327.c` · `net/can/slcan/slcan-core.c` · `net/caif/caif_serial.c` · `net/mctp/mctp-serial.c` · `net/ppp/ppp_synctty.c` · `net/ppp/ppp_async.c` | **`N_SLIP`(1) · `N_6PACK`(7) · `N_AX25`(5) · `N_CAN327`(30) · `N_SLCAN`(17) · `N_CAIF`(20) · `N_MCTP`(28) · `N_SYNC_PPP`(14) · `N_PPP`(3)** |
| **蓝牙 / 无线（2 个）** | **`drivers/bluetooth/hci_ldisc.c` · `net/nfc/nci/uart.c`** | **`N_HCI`(15) · `N_NCI`(25)** |
| **其他（5 个）** | **`drivers/misc/ti-st/st_core.c` · `drivers/accessibility/speakup/spk_ttyio.c` · `drivers/input/serio/serport.c` · `sound/soc/ti/ams-delta.c` · `sound/soc/codecs/cx20442.c`** | **`N_TI_WL`(22) · `N_SPEAKUP`(26) · `N_MOUSE`(2) · `N_V253`(19)** |

⚠️ **20 个实例 vs 31 个已分配编号** ⇒ **11 个编号在本树里没有实现**：
**`N_STRIP`(4) · `N_X25`(6) · `N_MASC`(8) · `N_R3964`(9) · `N_PROFIBUS_FDL`(10) ·
`N_IRDA`(11) · `N_SMSBLOCK`(12) · `N_GIGASET_M101`(16) · `N_PPS`(18) ·
`N_TRACESINK`(23) · `N_TRACEROUTER`(24) · `N_DEVELOPMENT`(29)。**

⚠️⚠️ **这 12 个（不是 11 个）里有三类**：
**① 明确标注 "Reserved for …" 的三个（8/9/10，01 §5.2）；
② 曾经有实现但被删掉的（`N_IRDA` / `N_STRIP` / `N_X25`）；
③ 专门留给树外的（`N_DEVELOPMENT`，注释 "Manual out-of-tree testing"）。**

⚠️ **本文档没有逐个核对这 12 个的历史**（99 §5.3）——
**数字 12 = 31 − 20 + 1（`N_TTY` 与 `n_tty.c` 一对一，但有的文件里不止一个实例）** ——
⚠️ **准确的说法是**：**20 个实例覆盖了约 19 个编号，剩下约 12 个编号没有实现**
（精确对账见 §5.4 的口径警告）。

### §5.2 两种写 ldisc 的路子

| 路子 | 做法 | 树里的例子 |
|---|---|---|
| **从零写** | **自己填 `tty_ldisc_ops` 的 18 个字段** | **`n_hdlc.c` · `slip.c` · `hci_ldisc.c` 等绝大多数** |
| **继承 n_tty** | **`n_tty_inherit_ops(&my_ops)` 后改几个** | **`drivers/accessibility/speakup/spk_ttyio.c`** |

⇒ **继承路子只适合"我要的行为和终端语义几乎一样，只想插一手"** ——
**网络类 ldisc（SLIP/PPP）完全不需要 POSIX 终端语义，所以从零写。**

### §5.3 `MODULE_ALIAS_LDISC()`：按编号自动加载

**每个 ldisc 模块都应该有这一行**（`n_null.c` 的例子）：
```c
MODULE_ALIAS_LDISC(N_NULL);
```
⇒ **用户态 `ioctl(fd, TIOCSETD, &N_HDLC)` 时，核心可以 `request_module` 自动加载。**

### §5.4 ⚠️ 一条口径警告

⚠️ **"20 个实例"与"31 个编号"不能直接相减**：
1. **一个文件可能有多个实例**（例如为了兼容而注册两个编号）；
2. **一个编号可能被多个文件竞争**（不同配置下只有一个生效）；
3. **有的实例不在 `drivers/tty/` 下，`grep` 的目录范围会影响结果。**

⇒ **本节的 20 是"全树 `.c` 文件里 `struct tty_ldisc_ops X = {` 的行数"** ——
**这个口径可重跑，但不等于"有 20 个可用的 ldisc"。**
⚠️ **本文档没有做"编号 → 实现"的完整对账**（99 §5.3）。

---

## §6 `tty_port_client_operations`：绕过 ldisc 的替换点

### §6.1 三个回调

```c
/* include/linux/tty_port.h:41-46 —— 全文 */
struct tty_port_client_operations {
	int (*receive_buf)(struct tty_port *port, const unsigned char *, const unsigned char *, size_t);
	void (*lookahead_buf)(struct tty_port *port, const unsigned char *cp,
			      const unsigned char *fp, unsigned int count);
	void (*write_wakeup)(struct tty_port *port);
};
```

**默认实现是 `tty_port_default_client_ops`**（`drivers/tty/tty_port.c:81` 处导出）
⇒ **它把数据转给 ldisc。**

### §6.2 它解决什么问题

**场景**：**内核里某个子系统想直接拿到串口数据，不需要 ldisc 的加工。**
⇒ **替换 `port->client_ops` 就能把数据引到自己这里。**

**典型使用者是 `drivers/tty/serdev/`**（serdev 总线：**把串口当成一条总线，
挂 GPS / 蓝牙 / NFC 这类"串口上的设备"**）
⇒ **`tty_port_register_device_serdev()`（`drivers/tty/tty_port.c:220`）就是给它用的**（01 §4.2）。

⚠️ **本文档没有覆盖 `drivers/tty/serdev/`（2 个 `.c`）**（99 §2.5）——
**只交代它是 `client_ops` 的主要使用者。**

### §6.3 与 ldisc 的关系

| | **ldisc（层 2）** | **client_ops（半层 B）** |
|---|---|---|
| **谁能换** | **用户态（`TIOCSETD`）** | **只有内核代码** |
| **数据走向** | **flip buffer → ldisc → 用户态** | **flip buffer → client_ops → 内核子系统** |
| **数量** | **31 个编号** | **默认 1 个 + serdev** |

⇒ **两者是互斥的两条出口** ——**换了 `client_ops` 就等于把 ldisc 摘掉了。**

---

## §7 汇总表

### §7.1 子框架 × 职责 × 挂载点 × 相关性

| 子框架 | 职责 | 挂载点 | 导出符号 | arm64 相关 |
|---|---|---|---|---|
| **`n_tty`** | **POSIX 终端语义** | **默认 ldisc** | **1**（`n_tty_inherit_ops`） | **总是** |
| **`pty`** | **伪终端（两对驱动）** | **自己是驱动** | **0** | **总是** |
| **`n_null`** | **兜底** | **失败路径** | **0** | **相关** |
| **flip buffer** | **接收通路** | **`port->buf`** | **11** | **总是** |
| **其余 17 个 ldisc** | **各类协议** | **`TIOCSETD`** | **各自** | 按硬件 |
| **client_ops** | **绕过 ldisc** | **`port->client_ops`** | **1**（默认实现） | 少数 |

### §7.2 全树使用统计（每条都能重跑）

| 项 | 数量 | 命令 |
|---|---|---|
| **`struct tty_ldisc_ops` 实例（全树）** | **20** | `grep -rn 'struct tty_ldisc_ops [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| **含实例的文件数** | **20** | `grep -rln 'struct tty_ldisc_ops [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| **`drivers/tty/` 内的 ldisc** | **4**（`n_tty` · `n_null` · `n_hdlc` · `n_gsm`） | 同上限定目录 |
| **已分配编号** | **31**（`N_TTY`=0 … `N_CAN327`=30） | `grep -cE '^#define N_[A-Z0-9_]+' include/uapi/linux/tty.h` |
| **`NR_LDISCS`** | **31** | `grep -n 'NR_LDISCS' include/uapi/linux/tty.h` |
| **`pty.c` 里的 `tty_operations` 实例** | **4** | `grep -c 'struct tty_operations [a-z_0-9]* = {' drivers/tty/pty.c` |
| **`driver->other` 的赋值点** | **4，全在 `pty.c`** | `grep -rn '\->other = ' --include=*.c . ` |
| **`n_tty_inherit_ops` 使用者** | **见 §5.2** | `grep -rln 'n_tty_inherit_ops' --include=*.c .` |
| **`n_null.c` 行数** | **69** | `wc -l drivers/tty/n_null.c` |

### §7.3 裁掉了什么

| 裁掉的 | 为什么 |
|---|---|
| **`drivers/tty/serial/`（120 个 `.c`，约 15 万行）** | **另一层框架**；⚠️ **而且被本树改过**（01 §0.1 / 99 §1.1） |
| **`drivers/tty/vt/`（7 个 `.c`）** | **虚拟控制台是独立子系统** |
| **`drivers/tty/hvc/`（13 个 `.c`）** | **hypervisor console** |
| **`drivers/tty/serdev/`（2 个 `.c`）** | **只交代它是 `client_ops` 的使用者**（§6.2） |
| **`fs/devpts/`** | **文件系统，不属 tty 核心**（§2.5） |
| **17 个非默认 ldisc 的实现** | **只统计与分类**（§5.1） |
| **`n_gsm.rst`（153 行）与 `n_gsm.c`（3889 行）** | **单 ldisc 的完整协议实现** |
| **`n_tty` 与 POSIX 的逐条一致性** | **需要 POSIX 规范文本**；**源码自己有两处 `XXX` 在问**（§1.2） |
| **`tty_bufhead` / `tty_buffer` 的逐字段语义** | **只登记字段数**（§4.1） |

## §8 阅读下一篇

| 想知道 | 去 |
|---|---|
| API 清单 | `01-api.md` |
| 结构体与七组易错关系 | `02-datastructures.md` |
| 主流程与设计债 | `03-internals.md` |
| **驱动骨架 + ldisc 骨架** | `05-usage.md` |
| DT | `06-devicetree.md` |
| **`-EOPNOTSUPP` ⇒ 怀疑 `n_null`** | `07-debug.md` |
| 为什么要有 flip buffer | `08-feynman.md` |
