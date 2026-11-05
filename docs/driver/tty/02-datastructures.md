---
title: 数据结构
---

# tty 02：数据结构

## §1.0 先分清七组关系

**图画得再全，也补不上一个没被显式否掉的错误类比。**
**tty 是本项目做过的子系统里对象最多、关系最绕的一个** ——**本节七组，前三组必读。**

### §1.0.A ⚠️⚠️ 组 1：`tty_port` ≠ `tty_struct`（本子系统第一号易错点）

**先否掉这个类比**：
> ❌ *"`tty_port` 和 `tty_struct` 应该是一对一的，无非一个是"静态描述"一个是"运行实例"。"*

**一对一大体成立，但**同寿命**完全不成立** ——**这是最要紧的差别。**

| | **`struct tty_port`** | **`struct tty_struct`** |
|---|---|---|
| 位置 | `include/linux/tty_port.h:100-123` | `include/linux/tty.h:193-251` |
| 字段数 | **22** | **46** |
| **谁分配** | **驱动**（通常内嵌在驱动私有结构里） | **tty 核心** |
| **生命周期** | **设备 probe → remove**（一直在） | **第一次 open → 最后一次 close**（没人用时不存在） |
| **数量关系** | **每个 tty 设备一个** | **同一时刻最多一个** |
| **互指** | **`port->tty`（`:102`）· `port->itty`（`:103`）** | **`tty->port`（`include/linux/tty.h:250`）** |

**官方 `index.rst` 原话（逐字）**：
> *"Every TTY device in a system has a corresponding struct tty_port. … Then, upon open, a struct
> tty_struct is allocated and lives until the final close."*

⚠️ **"为什么不会出问题"也要有依据**：
**`tty_port->tty` 可能是 NULL**（没人打开时）
⇒ **所以驱动必须用 `tty_port_tty_get()`（`drivers/tty/tty_port.c:332`）拿，而不是直接读 `port->tty`**
——**那个函数会加锁并 `tty_kref_get()`**（05 §7.1）。

⚠️ **`port->tty` 和 `port->itty` 是两个字段**：
**前者是"当前打开的 tty"，后者是"internal tty"** ——**04 篇讲 PTY 时会用到。**

### §1.0.B ⚠️⚠️ 组 2：两个都叫"ldisc 引用计数"的东西（官方文档专门警告）

**先否掉这个类比**：
> ❌ *"ldisc 有引用计数，就像 kref 那样，一个数就够了。"*

**官方 `tty_ldisc.rst` 用 "Heed this warning" 开头专门讲这件事**（逐字）：
> *"the reference count field of the registered copies of the tty_ldisc structure in the ldisc table
> counts the number of lines using this discipline. The reference count of the tty_ldisc structure
> within a tty counts the number of active users of the ldisc at this instant."*

| | **ldisc 表里那份** | **tty 内那份** |
|---|---|---|
| 数的是 | **有多少条线（tty）在用这个 ldisc** | **此刻有多少执行线程在 ldisc 方法里** |
| 变化频率 | **低**（只在 `tty_set_ldisc` 时变） | **极高**（每次 read/write 都变） |
| 谁维护 | **`drivers/tty/tty_ldisc.c` 的注册表** | **`tty_ldisc_ref()` / `tty_ldisc_deref()`** |
| 作用 | **防止 ldisc 模块被卸载** | **防止 ldisc 在有人用的时候被切换掉** |

⇒ **第二个才是 `ldisc_sem` 这把专用读写信号量存在的理由**（§6.2 / 03 篇）。

### §1.0.C ⚠️ 组 3：`tty_ldisc` 只有 2 个字段，别把它当 ops

**先否掉这个类比**：
> ❌ *"`tty_ldisc` 应该就是行规程的全部，里面装着回调。"*

```c
/* include/linux/tty_ldisc.h:250-253 —— 全文 */
struct tty_ldisc {
	struct tty_ldisc_ops *ops;
	struct tty_struct *tty;
};
```

| | **`struct tty_ldisc`** | **`struct tty_ldisc_ops`** |
|---|---|---|
| 字段数 | **2** | **18** |
| **是什么** | **"这个 tty 与那个 ldisc 的绑定关系"** | **行规程本身（回调集）** |
| **谁分配** | **核心，每个 tty 一个** | **ldisc 模块，全局唯一** |
| **谁注册** | **不注册** | **`tty_register_ldisc()`** |

⇒ **`tty->ldisc` 是一个"绑定对象"，不是"行规程"** ——
**真正的行规程是 `tty->ldisc->ops`。**

### §1.0.D 组 4：`tty_operations` 的 37 个回调不都由核心调

**先否掉这个类比**：
> ❌ *"`tty_operations` 里的回调都是 tty 核心调用驱动的。"*

⚠️ **`ldisc` 也会直接调驱动的 ops**（官方 `tty_ldisc.rst` 原话）：
> *"Line discipline methods can call the methods of the underlying hardware driver. These are
> documented as a part of struct tty_operations."*

| 调用者 | 典型调的 ops |
|---|---|
| **tty 核心** | **`install` / `open` / `close` / `shutdown` / `cleanup` / `ioctl`** |
| **ldisc**（如 n_tty） | **`write` / `write_room` / `chars_in_buffer` / `flush_buffer` / `throttle` / `unthrottle`** |
| **两者都会** | **`set_termios` / `hangup`** |

⇒ **所以驱动的 `write` 回调的调用者是 ldisc，不是 tty 核心**（§3 数据流）。

### §1.0.E 组 5：`tty_ldisc_ops` 的 18 个字段分"上下两半"（源码注释直接说明）

```c
/* include/linux/tty_ldisc.h:215-217 —— 源码注释，逐字 */
	/*
	 * The following routines are called from above.
	 */
	…
/* include/linux/tty_ldisc.h:235-237 */
	/*
	 * The following routines are called from below.
	 */
```

| 半 | 谁调 | 字段（逐个列全） |
|---|---|---|
| **"from above"（10 个）** | **用户态 / tty 核心** | **`open`(`:218`) · `close`(`:219`) · `flush_buffer`(`:220`) · `read`(`:221`) · `write`(`:224`) · `ioctl`(`:226`) · `compat_ioctl`(`:228`) · `set_termios`(`:230`) · `poll`(`:231`) · `hangup`(`:233`)** |
| **"from below"（5 个）** | **驱动 / flip buffer** | **`receive_buf`(`:238`) · `write_wakeup`(`:240`) · `dcd_change`(`:241`) · `receive_buf2`(`:242`) · `lookahead_buf`(`:244`)** |
| **元数据（3 个）** | — | **`name`(`:212`) · `num`(`:213`) · `owner`(`:247`)** |

⇒ **"上/下"这个划分是理解 ldisc 的钥匙**：**上半是用户态方向，下半是硬件方向。**

⚠️ **`receive_buf` 与 `receive_buf2` 并存**：
**后者返回 `int`（实际吃掉多少字节），前者是 `void`** ⇒ **`receive_buf2` 支持背压**（04 篇）。

### §1.0.F ⚠️ 组 6：`throttle`/`unthrottle` ≠ `stop`/`start`

**先否掉这个类比**：
> ❌ *"这两组都是流控，随便用哪个都行。"*

| | **`throttle` / `unthrottle`** | **`stop` / `start`** |
|---|---|---|
| 方向 | **接收侧** | **发送侧** |
| 含义 | **"我（内核）快满了，让对端别再发"** | **"我暂停往硬件发"** |
| 触发者 | **ldisc 发现 buffer 快满** | **收到 `^S`（`XOFF`）/ `^Q`（`XON`）** |
| 核心侧 API | **`tty_throttle_safe()` / `tty_unthrottle()`**（`drivers/tty/tty_ioctl.c:122`） | **`stop_tty()` / `start_tty()`**（`drivers/tty/tty_io.c:781` / `:812`） |
| 状态位 | **`TTY_THROTTLED`**（`include/linux/tty.h:314`） | **`tty->flow.stopped`**（`include/linux/tty.h:216`） |

⇒ **两个状态存放的位置都不一样**：**一个在 `tty->flags` 的位里，一个在 `tty->flow` 这个内嵌结构里。**

### §1.0.G 组 7：本子系统有几层可插拔 —— **两层 + 两个半层**

| 层 | ops 结构 | 字段数 | 位置 | 实现者 |
|---|---|---|---|---|
| **层 1** | **`tty_operations`** | **37**（3 个条件编译） | `include/linux/tty_driver.h:357-403` | **tty 驱动** |
| **层 2** | **`tty_ldisc_ops`** | **18** | `include/linux/tty_ldisc.h:211-248` | **ldisc 模块** |
| **半层 A** | **`tty_port_operations`** | **5** | `include/linux/tty_port.h:33-39` | **驱动（为了复用 port 辅助）** |
| **半层 B** | **`tty_port_client_operations`** | **3** | `include/linux/tty_port.h:41-46` | **少数特殊使用者**（04 篇） |

**半层 A 的 5 个字段**（逐字）：
**`carrier_raised` · `dtr_rts` · `shutdown` · `activate` · `destruct`。**
⇒ **实现了 `activate` + `shutdown` 就能用 `tty_port_open/close/hangup`**（01 §4.5）。

**半层 B 的 3 个字段**：
**`receive_buf` · `lookahead_buf` · `write_wakeup`** ——
⇒ **它是"数据不走 ldisc，直接给我"的替换点**（默认实现是 `tty_port_default_client_ops`，
`drivers/tty/tty_port.c:81` 处导出）。

---

## §2 四个对象的关系图

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ struct tty_driver（21 字段）—— 模块 init 到 exit                  │
  │   driver_name · name · type · subtype · init_termios · ops        │
  │   major/minor_start/num · flags（8 个 TTY_DRIVER_*）              │
  │   cdevs[] ─────► 字符设备                                        │
  │   ttys[]  ─────► struct tty_struct *（可能全是 NULL）           │
  │   ports[] ─────► struct tty_port *                                │
  │   termios[] ───► 每个 index 上次 close 时的 termios（会被继承）  │
  │   ops ─────────► struct tty_operations（37）──────► 驱动实现      │
  │   other ───────► 只有 PTY 用（配对的另一个驱动）                │
  └────────────────────────────┬─────────────────────────────────────┘
                               │ 一个驱动 N 个设备
  ┌────────────────────────────▼─────────────────────────────────────┐
  │ struct tty_port（22 字段）—— 设备 probe 到 remove（一直在）      │
  │   buf ─────────► struct tty_bufhead（5）──► flip buffer 链         │
  │   tty ─────────► struct tty_struct *（可能是 NULL！）            │
  │   itty ────────► internal tty                                     │
  │   ops ─────────► tty_port_operations（5）  ──► 驱动实现            │
  │   client_ops ──► tty_port_client_operations（3）                   │
  │   lock（spinlock）· mutex · buf_mutex ── 三把锁                  │
  │   count · blocked_open · open_wait · delta_msr_wait                │
  │   xmit_buf / xmit_fifo · close_delay · closing_wait · drain_delay  │
  │   kref ── 最后一个 put 释放 port 和驱动私有结构                  │
  └────────────────────────────┬─────────────────────────────────────┘
                               │ 有人 open 时才存在
  ┌────────────────────────────▼─────────────────────────────────────┐
  │ struct tty_struct（46 字段）—— 首次 open 到最后 close             │
  │   kref · dev · driver · ops · index                               │
  │   ldisc_sem（专用读写信号量）· ldisc ──► struct tty_ldisc（2）   │
  │   六把显式锁：atomic_write_lock · legacy_mutex · throttle_mutex  │
  │                termios_rwsem · winsize_mutex （+ ldisc_sem）      │
  │   termios / termios_locked · name[64] · flags（12 个 TTY_*）       │
  │   count · winsize                                                 │
  │   flow{ lock, stopped, tco_stopped }   ── 内嵌结构 + 自己的锁    │
  │   ctrl{ lock, pgrp, session, pktstatus, packet } ── 同上         │
  │   link ────────► 配对的 tty（只有 PTY 用）                       │
  │   disc_data ───► ldisc 私有                                       │
  │   driver_data ─► 驱动私有                                         │
  │   files_lock + tty_files ── 所有打开这个 tty 的 file            │
  │   port ────────► 回指 tty_port                                    │
  └───────────────────────────────────────────────────────────────────┘
                               │ tty->ldisc->ops
  ┌────────────────────────────▼─────────────────────────────────────┐
  │ struct tty_ldisc_ops（18 字段）—— 全局唯一，注册在 tty_ldiscs[31] │
  │   "from above" 10 个：open close flush_buffer read write        │
  │                        ioctl compat_ioctl set_termios poll hangup │
  │   "from below"  5 个：receive_buf write_wakeup dcd_change       │
  │                        receive_buf2 lookahead_buf                 │
  │   name · num · owner                                              │
  └───────────────────────────────────────────────────────────────────┘
```

**五条边要记住**：
1. **`driver->ports[]` 与 `driver->ttys[]` 是两个数组，寿命不同**（§1.0.A）。
2. **`driver->termios[]` 保存"上次 close 时的 termios"** ⇒ **下次 open 会继承**（03 篇）。
3. **`port->tty` 可能是 NULL** ⇒ **必须用 `tty_port_tty_get()`。**
4. **`tty->link` 和 `driver->other` 只有 PTY 用**（04 篇）。
5. **`tty->disc_data` 与 `tty->driver_data` 是两个不同的私有指针** ——**前者给 ldisc，后者给驱动。**

---

## §3 核心操作数据流

### §3.1 接收方向（硬件 → 用户态）：有 buffer，异步

```
  硬件中断
    │
    ▼
  驱动的中断处理
    ├─ tty_insert_flip_char(port, ch, flag)           include/linux/tty_flip.h（inline）
    │    └─ __tty_insert_flip_char()                 drivers/tty/tty_buffer.c:408
    │  或 tty_insert_flip_string_fixed_flag(...)      drivers/tty/tty_buffer.c:344
    │
    └─ tty_flip_buffer_push(port)                    drivers/tty/tty_buffer.c:606
         └─ 排一个 work（异步！）
              │
              ▼
         flush_to_ldisc()（work 回调，tty_buffer.c 内部）
              ├─ 拿 ldisc 引用
              └─ ld->ops->receive_buf2(tty, cp, fp, count)   层 2，"from below"
                    │  （n_tty：处理特殊字符、回显、规范模式）
                    ▼
                 n_tty 的内部读缓冲（read_buf）
                    │
                    ▼
  用户态 read()
    └─ tty_read()                                    drivers/tty/tty_io.c
         ├─ tty_ldisc_ref_wait(tty)                  drivers/tty/tty_ldisc.c:250
         └─ ld->ops->read(tty, file, buf, nr, &cookie, offset)  层 2，"from above"
```

⚠️ **两个必须记住的点**：
1. **`tty_flip_buffer_push()` 是异步的** ——**它只排 work，不直接调 ldisc**
   ⇒ **驱动可以在中断上下文里调它。**
2. **数据在 tty 层被缓冲了两次**：**flip buffer（`port->buf`）+ ldisc 自己的缓冲（n_tty 的
   `read_buf`）** ⇒ **"数据还没到用户态"可能卡在任一层**（07 篇）。

### §3.2 发送方向（用户态 → 硬件）：无 buffer，同步

```
  用户态 write()
    └─ tty_write()                                   drivers/tty/tty_io.c
         ├─ tty_write_lock(tty, ndelay)              drivers/tty/tty_io.c:942
         │    （拿 tty->atomic_write_lock）
         ├─ tty_ldisc_ref_wait(tty)
         └─ ld->ops->write(tty, file, buf, nr)       层 2，"from above"
              │  （n_tty：O_POST 处理、换行转换…）
              ▼
            tty->ops->write(tty, buf, count)          层 1，由 ldisc 直接调
              │
              ▼
            驱动把数据放进自己的发送队列 / 写硬件寄存器

  硬件发完 ──► 驱动调 tty_port_tty_wakeup(port)      drivers/tty/tty_port.c:437
                 └─ ld->ops->write_wakeup(tty)        层 2，"from below"
```

⚠️ **发送路径没有 tty 层的缓冲**（对比接收方向）：
**数据从 ldisc 直接进驱动的队列。**
⇒ **所以 `ops->write_room` 的返回值直接决定 `write()` 能写多少**（05 §1）。

⚠️ **`tty->write_buf` / `tty->write_cnt`（`include/linux/tty.h:247-248`）不是通用缓冲**
——**它是 `tty_write()` 从用户态拷数据用的中转区。**

### §3.3 三个"谁调谁"的方向标（记住这个就不会读错代码）

| 方向 | 谁调谁 |
|---|---|
| **"from above"** | **用户态/核心 → ldisc → 驱动** |
| **"from below"** | **驱动 → flip buffer → ldisc** |
| **驱动 ops 的调用者** | ⚠️ **绝大多数是 ldisc，不是 tty 核心**（§1.0.D） |

---

## §4 命名约定速查

| 形态 | 含义 | 例子 |
|---|---|---|
| `tty_*` | **核心 API（最大的一族）** | `tty_register_driver` `tty_hangup` |
| `tty_port_*` | **port 层 API** ——**驱动应优先用这一族** | `tty_port_open` `tty_port_tty_get` |
| `tty_ldisc_*` | **ldisc 层 API** | `tty_ldisc_ref` `tty_ldisc_flush` |
| `tty_buffer_*` / `tty_insert_flip_*` / `tty_flip_*` | **flip buffer** | `tty_flip_buffer_push` |
| `tty_termios_*` | **termios 换算与比较** | `tty_termios_baud_rate` `tty_termios_hw_change` |
| `n_tty_*` | **默认 ldisc 的对外辅助** | `n_tty_inherit_ops` `n_tty_ioctl_helper` |
| `__tty_*` / `__*` | **底层实体 / 不加锁版本** | `__tty_alloc_driver` `__tty_insert_flip_char` |
| `TTY_*`（宏） | **`tty->flags` 的**位号**（12 个）** | `TTY_THROTTLED` `TTY_IO_ERROR` |
| `TTY_DRIVER_*`（宏） | **`driver->flags` 的**掩码**（8 个）+ 6 个类型** | `TTY_DRIVER_DYNAMIC_DEV` |
| `N_*`（宏） | **行规程编号（31 个，uapi）** | `N_TTY` `N_NULL` |

⚠️ **五个名字陷阱**：

| 陷阱 | 说明 |
|---|---|
| ⚠️ **`TTY_*` 是位号，`TTY_DRIVER_*` 是掩码** | **`TTY_THROTTLED = 0`（位号，用 `test_bit`）；`TTY_DRIVER_INSTALLED = 0x0001`（掩码，用 `&`）** ⇒ **两族的用法完全不同** |
| ⚠️ **`tty_ldisc` vs `tty_ldisc_ops`** | **前者 2 字段，后者 18 字段**（§1.0.C） |
| ⚠️ **`tty->disc_data` vs `tty->driver_data`** | **ldisc 私有 vs 驱动私有** |
| ⚠️ **`port->tty` vs `port->itty`** | **两个不同的字段** |
| ⚠️ **`tty_hangup` vs `tty_vhangup`** | **前者异步（排 work），后者同步**（01 §7.4） |

---

## §5 结构体逐个看

### §5.1 `struct tty_struct`：46 个字段，其中 9 把锁

**位置：`include/linux/tty.h:193-251`。按用途分七组。**

| 组 | 字段 | 行 |
|---|---|---|
| **身份（5）** | `kref` · `dev` · `driver` · `ops` · `index` | `:194-198` |
| **ldisc（2）** | **`ldisc_sem`（专用读写信号量）· `ldisc`** | `:200-201` |
| **锁（5）** | **`atomic_write_lock` · `legacy_mutex` · `throttle_mutex` · `termios_rwsem` · `winsize_mutex`** | `:203-207` |
| **termios 与状态（5）** | `termios` · `termios_locked` · `name[64]` · `flags` · `count` · `winsize` | `:208-212` |
| **两个内嵌结构（各带自己的 spinlock）** | **`flow{ lock, stopped, tco_stopped }`（`:214-219`）· `ctrl{ lock, pgrp, session, pktstatus, packet }`（`:221-228`）** | `:214-228` |
| **流控与队列（3）** | `hw_stopped` · `receive_room` · `flow_change` | `:230-232` |
| **关联与等待（9）** | `link` · `fasync` · `write_wait` · `read_wait` · `hangup_work` · `disc_data` · `driver_data` · `files_lock` · `tty_files` | `:234-242` |
| **收尾（5）** | `closing` · `write_buf` · `write_cnt` · `SAK_work` · `port` | `:246-250` |

⚠️ **`flow` 和 `ctrl` 是两个匿名内嵌结构，各带一个 `spinlock_t`**
⇒ **这是 6.x 引入的细粒度锁改造**：**把原来靠大锁保护的字段分组、各配一把锁。**
**两者都带 `__aligned(sizeof(unsigned long))` 和 `unsigned long unused[0]`**
（`:218` / `:227`）⇒ **为将来扩展预留。**

⚠️ **`#define N_TTY_BUF_SIZE 4096` 出现在结构体中间**（`include/linux/tty.h:244`）
⇒ **数字段时会被这行打断** ——**它不是字段**（99 §6 的计数口径）。

⚠️ **结构以 `} __randomize_layout;` 结尾**（`:251`）
⇒ **不能靠"找第一个 `};`"定位末尾**（99 §6 的口径条）。

### §5.2 12 个 `TTY_*` 状态位（编号不连续）

**位置：`include/linux/tty.h:314-325`。**

| 位号 | 名字 | 含义 |
|---|---|---|
| **0** | **`TTY_THROTTLED`** | **接收侧已节流**（§1.0.F） |
| **1** | **`TTY_IO_ERROR`** | **设备不可用** ⇒ **几乎所有操作返回 `-EIO`**（01 §8.3） |
| **2** | `TTY_OTHER_CLOSED` | **配对的一端关了**（PTY 用） |
| **3** | `TTY_EXCLUSIVE` | **`TIOCEXCL` 独占** |
| **5** | `TTY_DO_WRITE_WAKEUP` | **要不要调 `write_wakeup`** |
| **11** | `TTY_LDISC_OPEN` | **ldisc 的 `open` 成功过** |
| **16** | `TTY_PTY_LOCK` | **`TIOCSPTLCK`**（PTY） |
| **17** | `TTY_NO_WRITE_SPLIT` | **写不拆分** |
| **18** | `TTY_HUPPED` | **已挂断** |
| **19** | `TTY_HUPPING` | **正在挂断** |
| **20** | `TTY_LDISC_CHANGING` | **ldisc 正在切换** |
| **22** | `TTY_LDISC_HALTED` | **ldisc 已停** |

⚠️⚠️ **编号有大量空洞**：**4 · 6–10 · 12–15 · 21 都没用。**
⇒ **这些是历史上删掉的位** ——**不能假设"位号连续"或"最大位号 = 位数"。**
**重跑：`grep -nE '^#define TTY_[A-Z_]+' include/linux/tty.h` ⇒ 12 行。**

### §5.3 `struct tty_port`：22 个字段，3 把锁

**位置：`include/linux/tty_port.h:100-123`（§2 已逐字列出）。按用途分五组**：

| 组 | 字段 | 说明 |
|---|---|---|
| **数据通路（1）** | **`buf`（`struct tty_bufhead`）** | **flip buffer 的头** ⇒ **buffer 属于 port 而不是 tty！**（§5.5） |
| **关联（4）** | **`tty` · `itty` · `ops` · `client_ops`** | **`tty` 可能是 NULL** |
| **锁（3）** | **`lock`（spinlock）· `mutex` · `buf_mutex`** | **三把，各管一类**（§6.3） |
| **打开计数与等待（4）** | `blocked_open` · `count` · `open_wait` · `delta_msr_wait` | **`tty_port_block_til_ready()` 用它们** |
| **标志（3）** | `flags` · `iflags` · `console:1` | **`console` 是位域** |
| **发送缓冲（2）** | **`xmit_buf` · `xmit_fifo`（`DECLARE_KFIFO_PTR`）** | **`tty_port_alloc_xmit_buf()` 分配** |
| **延时参数（3）** | `close_delay` · `closing_wait` · `drain_delay` | **`tty_port_close_*` 用** |
| **引用与私有（2）** | **`kref` · `client_data`** | **最后一个 `tty_port_put` 释放整个驱动私有结构**（01 §4.4） |

⚠️ **`buf` 在 port 里而不是在 tty 里，是一个重要的设计决定**：
⇒ **即使没人打开这个 tty，驱动也能往 buffer 里塞数据**（数据不会丢）。
⚠️ **代价是"没人读的数据会一直占着 buffer"** ⇒ **`tty_buffer_set_limit()` 存在的理由**（01 §6.1）。

### §5.4 `struct tty_port_operations`：5 个字段

```c
/* include/linux/tty_port.h:33-39 —— 全文 */
struct tty_port_operations {
	int (*carrier_raised)(struct tty_port *port);
	void (*dtr_rts)(struct tty_port *port, int raise);
	void (*shutdown)(struct tty_port *port);
	int (*activate)(struct tty_port *port, struct tty_struct *tty);
	void (*destruct)(struct tty_port *port);
};
```

| 字段 | 什么时候被调 |
|---|---|
| `activate` | **第一次 open 时**（`tty_port_open()` 内部） |
| `shutdown` | **最后一次 close 时** |
| `carrier_raised` | **`tty_port_block_til_ready()` 查载波** |
| `dtr_rts` | **抬起/放下 DTR+RTS** |
| `destruct` | **`kref` 归零时**（释放驱动私有结构的地方） |

⇒ **`activate` + `shutdown` 是"能用 port 辅助"的门票**（01 §4.5 / 05 §2）。

### §5.5 `struct tty_bufhead`（5）与 `struct tty_buffer`（9）

**位置：`include/linux/tty_buffer.h:38-48` 和 `:10-23`。**

⚠️ **`tty_buffer.h` 只有 60 行，是最小的一个头文件** ——
**但它承载的是接收方向的全部数据结构。**
**具体字段与 flip 机制在 04 篇讲**（本篇只登记字段数）。

### §5.6 `struct tty_driver`：21 个字段

**完整分组在 01 §3.1/§3.2。结构上要补的两点**：
1. **`ttys[]` / `ports[]` / `termios[]` 是三个平行数组，都按 `index` 索引。**
2. ⚠️ **`termios[]` 存的是"上次 close 时的 termios"** ⇒ **下次 open 会继承**
   （`tty_save_termios()`，`drivers/tty/tty_io.c:1489` 处导出，03 篇）。

---

## §6 全局变量与锁（tty 的锁是本项目最复杂的）

### §6.1 `tty_io.c` 的四个文件级/全局变量

| 变量 | 位置 | 用途 |
|---|---|---|
| **`tty_std_termios`** | `drivers/tty/tty_io.c:123`（导出于 `:134`） | **默认 termios** ——**注释原话："for the benefit of tty drivers"** |
| **`tty_drivers`（`LIST_HEAD`）** | `drivers/tty/tty_io.c:141` | **所有已注册的 tty 驱动** |
| **`tty_mutex`（`DEFINE_MUTEX`）** | `drivers/tty/tty_io.c:144` | **全局大锁**（§6.2） |
| **`redirect_lock` + `redirect`** | `drivers/tty/tty_io.c:505-506` | **`TIOCCONS` 的控制台重定向** |
| **`tty_class`** | `drivers/tty/tty_io.c:3145`（建于 `:3514`） | **`/sys/class/tty/`** |

**`tty_ldisc.c` 的一个**：
**`tty_ldiscs[NR_LDISCS]`（`drivers/tty/tty_ldisc.c:47`）** ——**31 项的注册表。**

### §6.2 锁的全景（本节是本篇最有价值的一节）

| 锁 | 类型 | 位置 | 保护什么 |
|---|---|---|---|
| **`tty_mutex`（全局）** | **mutex** | `drivers/tty/tty_io.c:144` | **`tty_drivers` 链表 + open 期间的全局串行化** |
| **`tty->ldisc_sem`** | **`struct ld_semaphore`（专用实现，`drivers/tty/tty_ldsem.c` 430 行）** | `include/linux/tty.h:200` | **ldisc 的切换 vs 使用** |
| **`tty->legacy_mutex`** | mutex | `include/linux/tty.h:204` | **就是 `tty_lock()` 拿的那把**（`drivers/tty/tty_mutex.c:20`） |
| **`tty->atomic_write_lock`** | mutex | `include/linux/tty.h:203` | **保证一次 `write()` 的原子性** |
| **`tty->termios_rwsem`** | **rw_semaphore** | `include/linux/tty.h:206` | **termios** |
| **`tty->throttle_mutex`** | mutex | `include/linux/tty.h:205` | **节流状态** |
| **`tty->winsize_mutex`** | mutex | `include/linux/tty.h:207` | **窗口尺寸** |
| **`tty->flow.lock`** | **spinlock** | `include/linux/tty.h:215` | **`stopped` / `tco_stopped`** |
| **`tty->ctrl.lock`** | **spinlock** | `include/linux/tty.h:222` | **`pgrp` / `session` / `packet`** |
| **`tty->files_lock`** | **spinlock** | `include/linux/tty.h:241` | **`tty_files` 链表** |
| **`port->lock`** | **spinlock** | `include/linux/tty_port.h:106` | **`port->tty` 等快速字段** |
| **`port->mutex`** | mutex | `include/linux/tty_port.h:114` | **open/close 串行化** |
| **`port->buf_mutex`** | mutex | `include/linux/tty_port.h:115` | **`xmit_buf` 的分配/释放** |
| **`port->buf.lock`** | **mutex**（在 `tty_bufhead` 里） | `include/linux/tty_buffer.h` | **flip buffer** |

⇒ **单个 tty 会话最多牵涉 14 把锁** ——**这是 tty 最难的地方。**

**锁的使用密度（出现次数，可重跑）**：

| 文件 | 锁相关行数 |
|---|---|
| `drivers/tty/tty_io.c` | **67** |
| `drivers/tty/n_tty.c` | **41** |
| `drivers/tty/tty_ioctl.c` | **35** |
| `drivers/tty/tty_ldisc.c` | **22** |
| `drivers/tty/pty.c` | 7 |
| `drivers/tty/tty_mutex.c` | 4 |
| `drivers/tty/tty_baudrate.c` / `tty_jobctrl.c` | 各 1 |

重跑：`grep -c 'ldisc_sem\|legacy_mutex\|tty_mutex\|termios_rwsem\|throttle_mutex\|atomic_write_lock\|files_lock' <12 个核心 .c>`

### §6.3 ⚠️`ld_semaphore`：为 ldisc 专门写的一把锁

**`drivers/tty/tty_ldsem.c` 430 行，0 个导出符号。**

| 为什么不用标准 `rw_semaphore` | 说明 |
|---|---|
| **需要"带超时的写等待"** | **ldisc 切换不能无限等**（否则一个卡住的 read 会永久阻塞 `TIOCSETD`） |
| **需要"读者可以在中断上下文之外递归"** | **ldisc 方法可能嵌套调用** |

⇒ **它是 tty 为了解决"ldisc 切换与使用的竞争"而付出的代价**（03 篇 / 08 §3）。
⚠️ **本文档没有逐行读 `tty_ldsem.c` 的实现**（99 §5.2）——**只讲它的用途和接口。**

### §6.5 ⚠️ 第 8 个头文件：`drivers/tty/tty.h`（119 行，私有）

⚠️ **它不在 `include/` 下** ⇒ **树外模块看不到，但它是核心几个文件之间的共享接口。**

| 类别 | 内容 |
|---|---|
| **五个打印宏** | **`tty_msg`（`drivers/tty/tty.h:9`）· `tty_debug`(`:12`) · `tty_notice`(`:13`) · `tty_warn`(`:14`) · `tty_err`(`:15`) · `tty_info_ratelimited`(`:17`)** ——**全部带 tty 名字前缀**（07 §4.1） |
| **流控防抖** | **`TTY_THROTTLE_SAFE = 1`（`:44`）· `TTY_UNTHROTTLE_SAFE = 2`（`:45`）· `__tty_set_flow_change`（`:47`）· `tty_set_flow_change`（`:52`）** |
| **ldisc 锁** | **`tty_ldisc_lock(tty, timeout)`（`:58`）· `tty_ldisc_unlock`（`:59`）** ——**带超时的那个接口**（03 §A5.2） |
| **不加锁的 start/stop** | **`__stop_tty`（`:63`）· `__start_tty`（`:64`）** |
| **写锁** | **`tty_write_lock(tty, ndelay)`（`:66`）· `tty_write_unlock`（`:65`）** |
| **作业控制内部** | **`__tty_check_change`(`:61`) · `tty_check_change`(`:62`) · `tty_vhangup_session`(`:67`) · `tty_open_proc_set_tty`(`:68`) · `tty_signal_session_leader`(`:69`) · `session_clear_tty`(`:70`)** |
| **buffer 内部** | **`tty_buffer_free_all`(`:71`) · `tty_buffer_flush`(`:72`) · `tty_buffer_init`(`:73`) · `tty_buffer_set_lock_subclass`(`:74`) · `tty_buffer_flush_work`(`:77`)** |
| **ldisc 内部** | **`tty_ldisc_hangup`(`:79`) · `tty_ldisc_reinit`(`:80`)** |

⇒ **约 30 个声明** ——**它们不是导出符号，所以 01 §0.5 那 97 个里没有它们。**
⚠️ **两个容易混的名字**：
**`tty_write_lock()` 拿的是 `tty->atomic_write_lock`**（不是"写数据的锁"）；
**`tty_ldisc_lock()` 拿的是 `tty->ldisc_sem` 的写锁并带超时**（不是 `tty_ldisc_ref()`）。

### §6.4 ⚠️ 三处源码自陈"锁语义没定清"

| 位置 | 内容（逐字节选） |
|---|---|
| `drivers/tty/tty_jobctrl.c:341` | `FIXME: Review locking here. The tty_lock never covered any race` … |
| `drivers/tty/tty_ioctl.c:353` | `FIXME: we need to decide on some locking/ordering semantics` … |
| `drivers/tty/tty_ioctl.c:540` | `FIXME: Arguably if tmp_termios == tty->termios AND the` … |

⇒ **tty 的锁复杂度不只是"多"，而是"连维护者都在注释里说没想清"**（03 Part C / 08 §3）。

---

## §7 阅读下一篇

| 想知道 | 去 |
|---|---|
| API 清单（97 个导出符号） | `01-api.md` |
| open / read / write / ldisc 切换的逐段剖析 | `03-internals.md` |
| PTY · n_tty · 其他 ldisc · flip buffer 细节 | `04-extensions.md` |
| **驱动骨架 + ldisc 骨架 + 近邻符号** | `05-usage.md` |
| DT 参与度 | `06-devicetree.md` |
| `/proc/tty/` 与观测手段 | `07-debug.md` |
| **为什么 port 和 struct 要分开、为什么两个引用计数** | `08-feynman.md` |
