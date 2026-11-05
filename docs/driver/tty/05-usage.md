---
title: 使用指南
---

# tty 核心层：各角色代码骨架

## §0 篇首声明（必读）

⚠️⚠️ **本篇所有骨架代码都没有编译过。** 它们是从本树（`linux-6.1.158`）的真实
in-tree 代码提炼出来的结构，行号和符号名逐个核对过存在性，但**没有过编译器**。
照抄进项目时请把它当"结构正确的伪代码"，而不是"可以 `make` 的补丁"。

**本篇只覆盖 tty 核心层。** 串口驱动（`drivers/tty/serial/`，120 个 `.c`）
在本树里被 printk/console 序列化补丁改过，显式排除在文档集范围之外（理由记在 `99-evidence.md` §1.1）。
所以下面**没有** `uart_driver` / `uart_ops` / `uart_add_one_port` 这一层——
那一层是串口专用的另一套壳子，套在本篇讲的 `tty_driver` + `tty_port` 之上。

### §0.1 本子系统有哪些角色

**tty 有两层可插拔**（这是它和 pinctrl/phy/watchdog 这类"单层框架"最大的不同）：

```
           用户态进程                       ← 角色 5 / 角色 6
        ─────────────────
  层 2   行规程 ldisc（n_tty / n_gsm / …）   ← 角色 3      可插拔
        ─────────────────
         tty 核心（tty_io.c + tty_port.c）    ← 不用你写
        ─────────────────
  层 1   tty 驱动（tty_operations）           ← 角色 1 / 角色 2  可插拔
        ─────────────────
              硬件 / 伪设备
```

| # | 角色 | 你要填的东西 | 本篇章节 |
|---|---|---|---|
| 1 | **最简 tty 驱动** | `tty_operations` 5 个回调 + 一个 `tty_port` | §1 |
| 2 | **完整 tty 驱动** | 加 `tty_port_operations`、收数据走 flip buffer | §2 |
| 3 | **行规程（ldisc）作者** | `tty_ldisc_ops` + `tty_register_ldisc()` | §3 |
| 4 | **内核内部使用者** | `tty_kopen_exclusive()` / `tty_kclose()` | §4 |
| 5 | **用户态：termios 配置** | `TCGETS` / `TCSETS`，切 raw 模式 | §5 |
| 6 | **用户态：pty + 作业控制** | `TIOCGPTN` / `TIOCSPTLCK` / `TIOCSCTTY` | §6 |

⚠️ **角色 1 和角色 2 不是"两个驱动"，是同一个驱动的两个成熟度**。
新写驱动直接从 §2 起步；§1 存在的意义是先把最小闭环看清楚。

### §0.2 按任务找章节

| 我要做的事 | 去哪一节 |
|---|---|
| 注册一个能 `open`/`write` 的 `/dev/ttyXXX` | §1 |
| 硬件上电/下电该放哪个回调 | §2.2（`activate` / `shutdown`） |
| 从中断里把收到的字节交给上层 | §2.3（flip buffer） |
| 让 `tty->port` 的引用计数不泄漏 | §2.4 + §7.6 |
| 写一个协议层（把串口变成 CAN/蓝牙/GSM 通道） | §3 |
| 内核里想直接读写一个 tty | §4 |
| 关掉回显、一次读一个字节 | §5.2 |
| 起一个 pty 跑子进程（写 terminal 模拟器） | §6.1 |
| 名字很像的两个函数到底选哪个 | **§7（独立一节，必读）** |
| 出了错，从现象倒推原因 | §8 |

---

## §1 角色 1：最简 tty 驱动

### §1.1 in-tree 真实参照：`ttynull`（109 行，全文可读）

⚠️ **这不是我编的骨架，是本树里真实存在的最小 tty 驱动**：
`drivers/tty/ttynull.c`（全文 109 行）。它做的事就是"接受一切写入、丢掉"，
所以剩下的每一行都是**注册一个 tty 驱动的最小必要动作**。先照着它读一遍，
比读任何教程都准。

```c
/* 提炼自 drivers/tty/ttynull.c:13-49、62-104 —— 结构与原文一致，注释是本文加的 */

static const struct tty_port_operations mydrv_port_ops;   /* 全 NULL 也合法，见 §2.2 */
static struct tty_driver *mydrv_driver;
static struct tty_port   mydrv_port;

/* ── tty_operations 的五个回调（ttynull.c:43-49 只填了这五个）── */

static int mydrv_open(struct tty_struct *tty, struct file *filp)
{
	return tty_port_open(&mydrv_port, tty, filp);      /* 转给 port 层 */
}

static void mydrv_close(struct tty_struct *tty, struct file *filp)
{
	tty_port_close(&mydrv_port, tty, filp);
}

static void mydrv_hangup(struct tty_struct *tty)
{
	tty_port_hangup(&mydrv_port);
}

static int mydrv_write(struct tty_struct *tty,
		       const unsigned char *buf, int count)
{
	/* 真驱动在这里把 buf 塞进硬件 FIFO；返回"实际收下了几个字节" */
	return count;
}

static unsigned int mydrv_write_room(struct tty_struct *tty)
{
	/* 还能收多少字节。返回 0 会让上层阻塞等 tty_wakeup() */
	return 65536;
}

static const struct tty_operations mydrv_ops = {
	.open       = mydrv_open,
	.close      = mydrv_close,
	.hangup     = mydrv_hangup,
	.write      = mydrv_write,
	.write_room = mydrv_write_room,
};

/* ── 注册（对应 ttynull.c:62-96）── */

static int __init mydrv_init(void)
{
	struct tty_driver *driver;
	int ret;

	/* 1. 分配。第一个参数是"几条线"（几个次设备号） */
	driver = tty_alloc_driver(1,
			TTY_DRIVER_RESET_TERMIOS |
			TTY_DRIVER_REAL_RAW);
	if (IS_ERR(driver))                     /* 返回 ERR_PTR，不是 NULL */
		return PTR_ERR(driver);

	/* 2. 初始化 port，并把 ops 挂上 */
	tty_port_init(&mydrv_port);
	mydrv_port.ops = &mydrv_port_ops;

	/* 3. 填 tty_driver 的必填字段 */
	driver->driver_name  = "mydrv";         /* 内部名，出现在 /proc/tty/drivers */
	driver->name         = "ttyMY";         /* 设备节点名前缀 → /dev/ttyMY0 */
	driver->type         = TTY_DRIVER_TYPE_SERIAL;
	driver->init_termios = tty_std_termios; /* 必须先拷这个默认值 */
	driver->init_termios.c_oflag = OPOST | OCRNL | ONOCR | ONLRET;  /* 再改差异位 */
	tty_set_operations(driver, &mydrv_ops);

	/* 4. 把 port 和 driver 的第 0 条线绑起来 */
	tty_port_link_device(&mydrv_port, driver, 0);

	/* 5. 注册 */
	ret = tty_register_driver(driver);
	if (ret < 0) {
		tty_driver_kref_put(driver);        /* 失败路径是两步，见 §1.3 */
		tty_port_destroy(&mydrv_port);
		return ret;
	}

	mydrv_driver = driver;
	return 0;
}

/* ── 注销（对应 ttynull.c:98-104）三步，顺序不能换 ── */

static void __exit mydrv_exit(void)
{
	tty_unregister_driver(mydrv_driver);
	tty_driver_kref_put(mydrv_driver);
	tty_port_destroy(&mydrv_port);
}

module_init(mydrv_init);
module_exit(mydrv_exit);
MODULE_LICENSE("GPL v2");
```

### §1.2 ⚠️ 六个必看点

| # | 点 | 出处 | 说明 |
|---|---|---|---|
| 1 | **`tty_alloc_driver` 是宏，不是函数** | `include/linux/tty_driver.h:484-485` | 它展开成 `__tty_alloc_driver(lines, THIS_MODULE, flags)`（函数在 `drivers/tty/tty_io.c:3314`）。**直接调函数版就要自己传 `THIS_MODULE`**，传错模块引用计数就错 → 见 §7.1 |
| 2 | **返回 `ERR_PTR` 而不是 `NULL`** | 同上 | 用 `IS_ERR()` 判断、`PTR_ERR()` 取码。写 `if (!driver)` 会漏掉全部错误 |
| 3 | **`init_termios` 必须先拷 `tty_std_termios`** | `drivers/tty/tty_io.c:123-134`，声明在 `include/linux/tty.h:386` | 它是导出的全局变量（`EXPORT_SYMBOL`，`tty_io.c:134`）。不拷就是一堆 0 —— 波特率 0、无字符大小，`open` 行为未定义 |
| 4 | **`tty_port_link_device()` 不能省** | `drivers/tty/tty_port.c:119` | 它把 `port` 填进 `driver->ports[index]`。省掉的话核心在 `open` 时拿不到 port，`tty->port` 为 `NULL` → 后面每个 `tty_port_*` 调用都是空指针 |
| 5 | **注册失败要清两样东西** | `ttynull.c:86-90` | `tty_driver_kref_put()` **和** `tty_port_destroy()`，顺序如原文。只 put driver 会漏 port 里的资源 |
| 6 | **注销是三步** | `ttynull.c:98-104` | `tty_unregister_driver()`（`tty_io.c:3481`）→ `tty_driver_kref_put()`（`:3404`）→ `tty_port_destroy()`（`tty_port.c:279`）。**unregister 只是"从链表摘掉"，不释放**；释放靠 kref 归零 |

### §1.3 为什么注销要分 unregister 和 kref_put 两步

因为**摘牌和释放是两件事**：`tty_unregister_driver()` 把 driver 从
全局 `tty_drivers` 链表摘掉、销毁 cdev，此后没人能再 `open` 它；
但**已经打开的 `tty_struct` 还持着这个 driver 的 kref**。
所以真正的 `kfree` 发生在最后一个引用 `put` 之后，而不是 unregister 那一刻。

⚠️ 这也解释了 `struct tty_driver` 的第一个字段为什么是 `struct kref kref`
（`include/linux/tty_driver.h:442-473` 的 21 个字段里，kref 排第一）。

### §1.4 `TTY_DRIVER_*` 标志速查（8 个）

全部在 `include/linux/tty_driver.h:547-554`：

| 标志 | 值 | 什么时候用 |
|---|---|---|
| `TTY_DRIVER_INSTALLED` | `0x0001` | **核心自己置的，驱动别写** |
| `TTY_DRIVER_RESET_TERMIOS` | `0x0002` | 每次 `open` 都把 termios 重置回 `init_termios`（pty 从设备用它） |
| `TTY_DRIVER_REAL_RAW` | `0x0004` | 声明"我上报的字节永不带错误标志" ⇒ ldisc 可以走快路径 |
| `TTY_DRIVER_DYNAMIC_DEV` | `0x0008` | **设备节点不在 register 时批量建，由你逐个 `tty_register_device()`**（USB 串口这类热插拔用它）→ §7.4 |
| `TTY_DRIVER_DEVPTS_MEM` | `0x0010` | 节点建在 devpts 里（只有 pty 从端用） |
| `TTY_DRIVER_HARDWARE_BREAK` | `0x0020` | `break_ctl` 的参数含义变成"毫秒数"而不是"开/关"（kerneldoc 在 `:228`） |
| `TTY_DRIVER_DYNAMIC_ALLOC` | `0x0040` | 不预分配 `ttys`/`termios` 数组 |
| `TTY_DRIVER_UNNUMBERED_NODE` | `0x0080` | 节点名不带序号（`/dev/ttynull` 而不是 `/dev/ttynull0`） |

`driver->type` 的 6 个取值在 `:557-562`（`SYSTEM` / `CONSOLE` / `SERIAL` / `PTY` / `SCC` / `SYSCONS`），
`subtype` 按 type 分组在 `:565-575`。**填错只影响 `/proc/tty/drivers` 的显示和少数特例判断**，不影响主流程。

---

## §2 角色 2：完整 tty 驱动

§1 的骨架缺三样真驱动必需的东西：**硬件上下电的时机**、
**收数据的通路**、**引用计数的正确用法**。这一节补上。

### §2.1 完整版填了哪些回调

`struct tty_operations` 一共 37 个回调（`include/linux/tty_driver.h:357-403`，
其中 3 个在 `#ifdef CONFIG_CONSOLE_POLL` 里 ⇒ 无条件的 34 个）。
**绝大多数是可选的**。一个"完整但不花哨"的驱动通常填这 11 个：

| 回调 | 必填？ | 干什么 |
|---|---|---|
| `install` | 否 | 第一次 `open` 该 index 时调，可在这里做 per-tty 分配 |
| `open` / `close` | **是** | 转给 `tty_port_open()` / `tty_port_close()` |
| `cleanup` | 否 | 最后一个引用释放时调（和 `install` 配对） |
| `hangup` | 强烈建议 | 转给 `tty_port_hangup()` |
| `write` | **是** | 往硬件塞字节，返回实际收下的个数 |
| `write_room` | **是** | 还能收多少 |
| `chars_in_buffer` | 建议 | 还有多少没发出去（和 `write_room` 方向相反 → §7.7） |
| `flush_buffer` | 建议 | 丢掉待发数据 |
| `set_termios` | 有硬件参数就必填 | 波特率/校验位变了 |
| `throttle` / `unthrottle` | 建议 | 上层读不过来了，让硬件流控 |
| `tiocmget` / `tiocmset` | 有调制解调器线才填 | 读写 RTS/CTS/DTR/DSR |

### §2.2 ⚠️ 硬件上下电放哪里：`activate` / `shutdown`，不是 `open` / `close`

**这是新写 tty 驱动最容易放错的一件事。**

`struct tty_port_operations` 只有 5 个成员（`include/linux/tty_port.h:33-39`）：

```c
struct tty_port_operations {
	int  (*carrier_raised)(struct tty_port *port);
	void (*dtr_rts)(struct tty_port *port, int raise);
	void (*shutdown)(struct tty_port *port);
	int  (*activate)(struct tty_port *port, struct tty_struct *tty);
	void (*destruct)(struct tty_port *port);
};
```

**`activate` 与 `open` 的区别是"每设备一次"还是"每次打开都来"**：

```c
static int mydrv_activate(struct tty_port *port, struct tty_struct *tty)
{
	struct mydrv *d = container_of(port, struct mydrv, port);

	/* 这里才是：开时钟、申请中断、上电、使能 FIFO */
	clk_prepare_enable(d->clk);
	return request_irq(d->irq, mydrv_isr, 0, "mydrv", d);
}

static void mydrv_shutdown(struct tty_port *port)
{
	struct mydrv *d = container_of(port, struct mydrv, port);

	/* 和 activate 严格配对：free_irq、下电、关时钟 */
	free_irq(d->irq, d);
	clk_disable_unprepare(d->clk);
}

static const struct tty_port_operations mydrv_port_ops = {
	.activate       = mydrv_activate,
	.shutdown       = mydrv_shutdown,
	.carrier_raised = mydrv_carrier_raised,   /* 可选，见下 */
	.dtr_rts        = mydrv_dtr_rts,          /* 可选 */
};
```

⚠️ **核心保证 `activate` 只在"硬件还没初始化"时调一次**。
证据在 `drivers/tty/tty_port.c:765` 的 `tty_port_open()` 里：它先
`tty_port_initialized(port)` 判断，只有为假才调 `port->ops->activate`，
调成功后 `tty_port_set_initialized(port, 1)`；整段用 `port->mutex` 串起来。

**所以：同一个 `/dev/ttyMY0` 被 3 个进程打开，`open` 回调走 3 次，
`activate` 只走 1 次。** 把 `request_irq` 写在 `open` 里 ⇒ 第二个进程打开就
`-EBUSY`；把 `free_irq` 写在 `close` 里 ⇒ 第一个进程关闭就把还在用的中断拆了。

| 回调 | 谁调 | 频率 | 放什么 |
|---|---|---|---|
| `tty_operations.open` | tty 核心 | **每次 `open(2)`** | 只转给 `tty_port_open()` |
| `tty_port_operations.activate` | `tty_port_open()`（`tty_port.c:765`） | **每设备一次** | **中断、时钟、上电** |
| `tty_operations.close` | tty 核心 | 每次 `close(2)` | 只转给 `tty_port_close()` |
| `tty_port_operations.shutdown` | `tty_port_close_end()`（`tty_port.c:681`） | **最后一个关闭时一次** | **和 `activate` 对称拆除** |
| `destruct` | `tty_port_put()`（`tty_port.c:308`） | port 引用归零时一次 | 释放 port 自身（`port` 是动态分配的才需要） |

另两个可选回调管的是**载波检测**：`carrier_raised()` 返回"DCD 线是否有效"，
`dtr_rts(port, raise)` 拉高/拉低 DTR+RTS。核心在
`tty_port_block_til_ready()`（`tty_port.c:510`）里用它们实现
"阻塞等对端接上"——只在 termios 里**没有** `CLOCAL` 时才等。
没有调制解调器线的设备两个都不填，行为等价于"载波永远有效"。

### §2.3 收数据：flip buffer 三步

**驱动收到字节以后不能直接调 ldisc**，要先塞进 flip buffer，
再"push"一次。对外 API 全在 `include/linux/tty_flip.h`（50 行，全文可读）。

```c
/* 中断里调（这几个 API 可以在 atomic 上下文用） */
static void mydrv_rx_chars(struct mydrv *d)
{
	while (rx_fifo_not_empty(d)) {
		u8 ch   = readb(d->base + RXDATA);
		u8 stat = readb(d->base + RXSTAT);
		char flag = TTY_NORMAL;

		/* 1. 把硬件错误翻译成 flip buffer 的 flag */
		if (stat & RXSTAT_PARITY)
			flag = TTY_PARITY;
		else if (stat & RXSTAT_FRAME)
			flag = TTY_FRAME;
		else if (stat & RXSTAT_OVERRUN)
			flag = TTY_OVERRUN;
		else if (stat & RXSTAT_BREAK)
			flag = TTY_BREAK;

		/* 2. 逐字节插入。返回 1 = 成功，0 = buffer 满（丢字节） */
		tty_insert_flip_char(&d->port, ch, flag);
	}

	/* 3. 一次中断只在末尾 push 一次，不要每字节 push */
	tty_flip_buffer_push(&d->port);
}
```

四个插入函数的分工（声明都在 `tty_flip.h:13-42`）：

| 函数 | 用在 | 特点 |
|---|---|---|
| `tty_insert_flip_char(port, ch, flag)` | **逐字节，最常用** | **`static inline`（`tty_flip.h:22-36`）**，命中快路径时**完全不进函数**；只在 buffer 需要扩张时才落到 `__tty_insert_flip_char()`（`drivers/tty/tty_buffer.c` 里 `EXPORT_SYMBOL` 在 `:408`） |
| `tty_insert_flip_string(port, chars, size)` | 一整段无错误的数据 | `static inline`（`:38-42`），转调 `..._fixed_flag` 并硬传 `TTY_NORMAL` |
| `tty_insert_flip_string_fixed_flag(port, chars, flag, size)` | 一整段"同一种错误"的数据 | `EXPORT_SYMBOL`（`tty_buffer.c:344`） |
| `tty_insert_flip_string_flags(port, chars, flags, size)` | 每字节各自的 flag（DMA 场景） | **`flags` 是和 `chars` 等长的数组**，不是一个值（`tty_buffer.c:382`）→ §7.2 |

⚠️ **`tty_flip_buffer_push()`（`tty_buffer.c:606`）不是同步交付**——它把工作
排给 work queue，真正调 ldisc 的 `receive_buf` 发生在稍后的进程上下文。
**所以在中断里 push 是安全的，但不要期待 push 返回后数据已经到用户态。**

需要先看空间够不够，用 `tty_buffer_request_room(port, size)`
（`tty_buffer.c:305`）或 `tty_buffer_space_avail(port)`（`:100`）。
DMA 场景可以用 `tty_prepare_flip_string()`（`:439`）拿到可直接写的指针，省一次拷贝。

### §2.4 ⚠️`tty->port` 与 `tty_port_tty_get()`：两个方向的引用

`tty_struct` 和 `tty_port` 的寿命不一样（这一点在 `02-datastructures.md` §1.0.A 里详细展开）。
后果是：**从 port 拿 tty 必须加引用，从 tty 拿 port 不用。**

```c
/* 从下往上（中断/工作线程里）：必须 get / put 成对 */
static void mydrv_notify(struct mydrv *d)
{
	struct tty_struct *tty = tty_port_tty_get(&d->port);   /* tty_port.c:322 */

	if (!tty)                       /* 可能为 NULL：设备没被打开 */
		return;
	tty_wakeup(tty);
	tty_kref_put(tty);              /* 忘了这行就是引用泄漏 */
}

/* 从上往下（回调里，核心已经保证 tty 活着）：直接用 */
static int mydrv_write(struct tty_struct *tty, const unsigned char *buf, int count)
{
	struct tty_port *port = tty->port;   /* 不需要引用计数 */
	...
}
```

**更省事的做法：能用 `tty_port_*` 的包装就别自己 get**。
核心已经把常见组合封好了，内部自己做 get/put：

| 你想做的 | 自己写（要 get/put） | **用这个（不用）** |
|---|---|---|
| 唤醒等写的进程 | `tty_port_tty_get` + `tty_wakeup` + put | `tty_port_tty_wakeup(port)`（`tty_port.c:433`） |
| 挂断 | `tty_port_tty_get` + `tty_vhangup` + put | `tty_port_tty_hangup(port, check_clocal)`（`:419`） |

→ 这两组的区别写在 §7.5。

### §2.5 角色 1–2 API 快速参考

| API | 出处 | 一句话 |
|---|---|---|
| `tty_alloc_driver(lines, flags)` | `include/linux/tty_driver.h:484-485`（宏） | 分配，返回 `ERR_PTR` |
| `tty_set_operations(driver, ops)` | `include/linux/tty_driver.h:493`（`static inline`） | 挂 `tty_operations` |
| `tty_register_driver(driver)` | `drivers/tty/tty_io.c:3416` | 注册；无 `DYNAMIC_DEV` 时顺手建全部节点 |
| `tty_unregister_driver(driver)` | `drivers/tty/tty_io.c:3481` | 只摘牌，不释放 |
| `tty_driver_kref_put(driver)` | `drivers/tty/tty_io.c:3404` | 放引用，归零才真释放 |
| `tty_register_device(driver, index, dev)` | `drivers/tty/tty_io.c:3182` | 配 `TTY_DRIVER_DYNAMIC_DEV` 用 |
| `tty_unregister_device(driver, index)` | `drivers/tty/tty_io.c:3294` | 同上的反向 |
| `tty_port_init(port)` | `drivers/tty/tty_port.c:92` | 初始化 port（必须在 link 之前） |
| `tty_port_link_device(port, driver, index)` | `drivers/tty/tty_port.c:119` | 绑定，**不能省** |
| `tty_port_register_device(port, driver, index, dev)` | `drivers/tty/tty_port.c:139` | link + register 二合一 → §7.4 |
| `tty_port_destroy(port)` | `drivers/tty/tty_port.c:279` | 释放 port 内部资源 |
| `tty_port_open/close/hangup(...)` | `drivers/tty/tty_port.c:765` / `:715` / `:394` | 三个回调直接转给它们 |
| `tty_insert_flip_char(port, ch, flag)` | `include/linux/tty_flip.h:22` | 收一个字节 |
| `tty_flip_buffer_push(port)` | `drivers/tty/tty_buffer.c:606` | 交给上层（异步） |
| `tty_port_tty_wakeup(port)` | `drivers/tty/tty_port.c:433` | 写空间有了，叫醒写者 |
| `tty_wait_until_sent(tty, timeout)` | `drivers/tty/tty_ioctl.c:216` | 等待发完 |

---

## §3 角色 3：行规程（ldisc）作者

**行规程是 tty 的第二个可插拔层**：它坐在 tty 核心之上、用户态之下，
决定"字节流怎么变成用户看到的东西"。默认的 `n_tty` 做的是终端语义
（回显、行编辑、`Ctrl-C` 变信号）；换成别的 ldisc，同一个串口就变成
CAN 接口（`N_SLCAN`）、蓝牙 HCI 通道（`N_HCI`）或 GSM 多路复用（`N_GSM0710`）。

### §3.1 in-tree 真实参照：`n_null`（69 行，可全文引）

`drivers/tty/n_null.c` 是本树里**最小的合法 ldisc**，
一共 5 个回调、全部什么都不做。它的存在意义是"错误路径的兜底"
（`04-extensions.md` §3 讲了它何时被换上），但作为骨架参照正好。

```c
/* 提炼自 drivers/tty/n_null.c:13-69 —— 结构与原文一致 */

static int myldisc_open(struct tty_struct *tty)
{
	/* 在这里分配你的协议私有数据，挂到 tty->disc_data */
	struct myproto *p = kzalloc(sizeof(*p), GFP_KERNEL);

	if (!p)
		return -ENOMEM;
	tty->disc_data = p;
	return 0;
}

static void myldisc_close(struct tty_struct *tty)
{
	kfree(tty->disc_data);           /* 和 open 配对 */
	tty->disc_data = NULL;
}

/* ── 从上面来（用户态方向）── */

static ssize_t myldisc_read(struct tty_struct *tty, struct file *file,
			    unsigned char *buf, size_t nr,
			    void **cookie, unsigned long offset)
{
	return -EOPNOTSUPP;             /* n_null 就是这么写的 → §8 */
}

static ssize_t myldisc_write(struct tty_struct *tty, struct file *file,
			     const unsigned char *buf, size_t nr)
{
	/* 真 ldisc 在这里把用户数据编码，再调 tty->ops->write() 下发 */
	return tty->ops->write(tty, buf, nr);
}

/* ── 从下面来（驱动方向）这是 ldisc 的主入口 ── */

static void myldisc_receive_buf(struct tty_struct *tty,
				const unsigned char *cp, const char *fp,
				int count)
{
	int i;

	for (i = 0; i < count; i++) {
		/* fp 可能为 NULL —— 表示"这一批全部无错误" */
		char flag = fp ? fp[i] : TTY_NORMAL;

		if (flag != TTY_NORMAL) {
			myproto_count_error(tty->disc_data, flag);
			continue;
		}
		myproto_feed_byte(tty->disc_data, cp[i]);
	}
}

static struct tty_ldisc_ops my_ldisc = {
	.owner        = THIS_MODULE,     /* 不填 = 模块能在使用中被卸载 */
	.num          = N_DEVELOPMENT,   /* 编号，见 §3.3 */
	.name         = "myproto",       /* 出现在 /proc/tty/ldiscs */
	.open         = myldisc_open,
	.close        = myldisc_close,
	.read         = myldisc_read,
	.write        = myldisc_write,
	.receive_buf  = myldisc_receive_buf,
};

static int __init myldisc_init(void)
{
	return tty_register_ldisc(&my_ldisc);   /* drivers/tty/tty_ldisc.c:58 */
}

static void __exit myldisc_exit(void)
{
	tty_unregister_ldisc(&my_ldisc);        /* drivers/tty/tty_ldisc.c:84 */
}

module_init(myldisc_init);
module_exit(myldisc_exit);
MODULE_LICENSE("GPL");
MODULE_ALIAS_LDISC(N_DEVELOPMENT);          /* 关键，见 §3.4 */
```

### §3.2 `tty_ldisc_ops` 的 18 个成员分三组

定义在 `include/linux/tty_ldisc.h:211-248`。**原文用注释把它明确分成
"from above"（用户态方向）和 "from below"（驱动方向）两组**——这个方向标是读
ldisc 代码的关键（`02-datastructures.md` §1.0.E 展开讲了）。

| 组 | 成员 | 备注 |
|---|---|---|
| 身份 | `name`、`num`、`owner` | **三个都必填** |
| **from above**（10 个） | `open`、`close`、`flush_buffer`、`read`、`write`、`ioctl`、`compat_ioctl`、`set_termios`、`poll`、`hangup` | 都在进程上下文、可睡眠 |
| **from below**（5 个） | `receive_buf`、`write_wakeup`、`dcd_change`、`receive_buf2`、`lookahead_buf` | **可能在 atomic 上下文，不能睡** |

`struct tty_ldisc` 本身只有 2 个字段（`include/linux/tty_ldisc.h:250-253`）：
`ops` 和 `tty`。它是"ops + 绑定的 tty"这个二元组的载体，没有别的状态——
你的状态都放在 `tty->disc_data` 里。

### §3.3 ⚠️`num` 怎么选：29 个号已被占用

全部编号在 `include/uapi/linux/tty.h:10-41`，`N_TTY` = 0 到 `N_CAN327` = 30。

⚠️ **`tty_register_ldisc()` 只校验范围，不校验冲突**。看它的实现
（`drivers/tty/tty_ldisc.c:58`）：

```c
if (new_ldisc->num < N_TTY || new_ldisc->num >= NR_LDISCS)
	return -EINVAL;
raw_spin_lock_irqsave(&tty_ldiscs_lock, flags);
tty_ldiscs[new_ldisc->num] = new_ldisc;      /* 直接覆盖！ */
raw_spin_unlock_irqrestore(&tty_ldiscs_lock, flags);
return ret;                                   /* ret 恒为 0 */
```

**用一个已被占用的号注册，不报错，直接把原来那个顶掉。**
⇒ 现象是"某个别的协议突然不工作了"，而不是你的模块加载失败。

**树外测试用 `N_DEVELOPMENT`（29）** —— uapi 头文件里对它的注释就是
`/* Manual out-of-tree testing */`。要正式进主线才去申请新号。

### §3.4 `MODULE_ALIAS_LDISC()` 不能省

宏定义在 `include/linux/tty_ldisc.h`，展开成 `MODULE_ALIAS("tty-ldisc-<num>")`。
**它是"用户态一 `ioctl(fd, TIOCSETD, &n)` 就自动装载你的模块"的唯一依据**。
不写的话，用户必须先手动 `modprobe` 你的模块，否则 `TIOCSETD` 返回 `-EINVAL`。

### §3.5 角色 3 API 快速参考

| API | 出处 | 一句话 |
|---|---|---|
| `tty_register_ldisc(ops)` | `drivers/tty/tty_ldisc.c:58` | 注册；只校验范围 |
| `tty_unregister_ldisc(ops)` | `drivers/tty/tty_ldisc.c:84` | 注销；**不检查是否在用** |
| `tty_set_ldisc(tty, disc)` | 声明在 `include/linux/tty_ldisc.h:268` | 内核里主动换 ldisc |
| `tty_ldisc_ref(tty)` | `drivers/tty/tty_ldisc.c` | 拿引用，**可能返回 `NULL`** → §7.3 |
| `tty_ldisc_ref_wait(tty)` | `drivers/tty/tty_ldisc.c` | 拿引用，等到拿到为止 → §7.3 |
| `tty_ldisc_deref(ld)` | `drivers/tty/tty_ldisc.c` | 放引用 |
| `tty_ldisc_flush(tty)` | 声明在 `include/linux/tty_ldisc.h:264` | 丢掉 ldisc 缓冲 |
| `n_tty_ioctl_helper(...)` | `drivers/tty/tty_ioctl.c:1004` | 处理通用 termios ioctl，自己的 ldisc 也能复用 |

---

## §4 角色 4：内核内部使用者（kopen）

内核代码想直接读写一个 tty（典型场景：把内核日志导到某个串口、
或内核里跑一个 AT 命令会话），走的不是 `filp_open("/dev/ttyS0")`，
而是 tty 专用的 kopen 接口。

```c
/* 声明在 include/linux/tty.h:354-357 */

static int my_grab_tty(const char *name)      /* name 形如 "ttyS0" */
{
	struct tty_struct *tty;
	dev_t dev;
	int ret;

	/* 1. 名字 → dev_t（drivers/tty/tty_io.c:336） */
	ret = tty_dev_name_to_number(name, &dev);
	if (ret)
		return ret;

	/* 2. 独占打开（drivers/tty/tty_io.c:2000） */
	tty = tty_kopen_exclusive(dev);
	if (IS_ERR(tty))                          /* 返回 ERR_PTR */
		return PTR_ERR(tty);

	/* 3. 用。注意这里要自己处理 ldisc 引用 */
	tty->ops->write(tty, "AT\r", 3);

	/* 4. 关（drivers/tty/tty_io.c:1654） */
	tty_kclose(tty);
	return 0;
}
```

⚠️ **`tty_kopen_exclusive()` 和 `tty_kopen_shared()` 的区别就是名字说的那样**，
但后果不对称——kerneldoc 在 `drivers/tty/tty_io.c:2007-2011` 明说
shared 版 "doesn't ensure to be the only user"：

| API | 出处 | 语义 | 用户态还能 `open` 吗 |
|---|---|---|---|
| `tty_kopen_exclusive(dev)` | `drivers/tty/tty_io.c:2000` | **独占**：拿不到就失败 | **不能**，用户态 `open` 会拿到 `-EBUSY` |
| `tty_kopen_shared(dev)` | `drivers/tty/tty_io.c:2015` | 共享：和别人一起用 | 能，两边的数据会互相串 |

两者都用 `tty_kclose()`（`:1654`）关闭。
⚠️ **`tty_kclose()` 不能用来关用户态打开的 tty**，反之亦然——
kopen 走的是独立的计数路径。

三个函数在 `CONFIG_TTY=n` 时都有空实现（`include/linux/tty.h:378-384`），
所以调用方不需要自己包 `#ifdef`。

---

## §5 角色 5：用户态 —— termios 配置

tty 的用户态 ABI 面比本文档集里其他子系统都大。核心就一个结构
（`struct termios`）加一组 ioctl。本节全部常量按 **arm64** 口径给出——
arm64 的 `arch/arm64/include/uapi/asm/` 里**没有** `termbits.h` / `ioctls.h`
（该目录只有 `Kbuild` + 19 个头，`Kbuild` 里只 `generic-y += kvm_para.h`），
所以走 `include/uapi/asm-generic/`。

### §5.1 `struct termios`：六个字段

定义在 `include/uapi/asm-generic/termbits.h:10-17`：

```c
struct termios {
	tcflag_t c_iflag;    /* 输入模式：来的字节怎么预处理 */
	tcflag_t c_oflag;    /* 输出模式：出去的字节怎么后处理 */
	tcflag_t c_cflag;    /* 控制模式：波特率/数据位/校验/停止位 */
	tcflag_t c_lflag;    /* 本地模式：回显、行编辑、信号 —— 最常改的一个 */
	cc_t     c_line;     /* 行规程号（实际不通过这里改，见 §6.4） */
	cc_t     c_cc[NCCS]; /* 控制字符表，NCCS = 19（termbits.h:9） */
};
```

⚠️ **内核内部用的是 `struct ktermios`，不是 `struct termios`**
（`include/uapi/asm-generic/termbits.h:30-39`）：字段前六个一样，**多了
`c_ispeed` / `c_ospeed` 两个 `speed_t`**。这就是为什么 `TCGETS`
拿不到任意波特率，要用 `TCGETS2`（`include/uapi/asm-generic/ioctls.h:61-64`）→ §5.4。

### §5.2 最常见的需求：切进 raw 模式

"关掉回显、`Ctrl-C` 不再变信号、一次读一个字节就返回"——
这三件事分别是 `ECHO`、`ISIG`、`ICANON` 三个 `c_lflag` 位。

```c
#include <sys/ioctl.h>
#include <asm/termbits.h>       /* struct termios / 各标志位 */
#include <unistd.h>
#include <stdio.h>

static struct termios saved;    /* 一定要存原值，退出时恢复 */

static int tty_set_raw(int fd)
{
	struct termios t;

	if (ioctl(fd, TCGETS, &saved) < 0)      /* ioctls.h:20 */
		return -1;
	t = saved;

	/* 本地模式：关掉四件事 */
	t.c_lflag &= ~(ICANON      /* termbits.h:128 —— 行缓冲 */
		     | ECHO        /* :130 —— 回显 */
		     | ECHOE       /* :131 —— 退格擦字 */
		     | ISIG);      /* :127 —— Ctrl-C/Ctrl-Z 变信号 */

	/* 输入模式：不做 CR/NL 转换、不做软件流控 */
	t.c_iflag &= ~(ICRNL       /* termbits-common.h:17 */
		     | INLCR       /* termbits-common.h:15 */
		     | IGNCR       /* termbits-common.h:16 */
		     | IXON        /* termbits.h:62 */
		     | ISTRIP      /* termbits-common.h:14 —— 砍第 8 位 */
		     | BRKINT);    /* termbits-common.h:10 */

	/* 输出模式：关掉全部后处理（NL → CRNL 也在里面） */
	t.c_oflag &= ~OPOST;       /* termbits-common.h:21 */

	/* 控制模式：8 位、无校验、1 停止位 */
	t.c_cflag &= ~(CSIZE | PARENB | CSTOPB);   /* :96 / :103 / :101 */
	t.c_cflag |= CS8 | CREAD | CLOCAL;         /* :100 / :102 / :106 */

	/* 关键的两个：读一个字节就返回，不等 */
	t.c_cc[VMIN]  = 1;         /* termbits.h:48 —— 最少读几个字节 */
	t.c_cc[VTIME] = 0;         /* termbits.h:47 —— 超时，单位 0.1 秒 */

	/* 用 TCSETSF 而不是 TCSETS，见下表 */
	return ioctl(fd, TCSETSF, &t);             /* ioctls.h:23 */
}

static void tty_restore(int fd)
{
	ioctl(fd, TCSETSF, &saved);
}
```

⚠️ **`VMIN` / `VTIME` 只在 `ICANON` 已经关掉时才起作用。** 还开着行缓冲
就设 `VMIN` 是无效动作——这是最常见的"我明明设了却不生效"。

### §5.3 ⚠️ 三个 `TCSETS` 变体：差别在"生效时机"

全在 `include/uapi/asm-generic/ioctls.h:21-23`：

| ioctl | 值 | 语义 | 什么时候用 |
|---|---|---|---|
| `TCSETS` | `0x5402` | **立即生效**，不管发送队列 | 只改不影响线路的软件位（如 `ECHO`） |
| `TCSETSW` | `0x5403` | 等**发送**队列排空再改 | 改波特率——避免最后几个字节按新速率发出去 |
| `TCSETSF` | `0x5404` | **等发送排空 + 丢掉接收队列** | **切模式时用这个**：残留在接收队列里的字节是按旧规则解析的，留着就是脏数据 |

对应的 libc 封装是 `tcsetattr()` 的第二参数 `TCSANOW` / `TCSADRAIN` / `TCSAFLUSH`。
**直接用 ioctl 时没有那层名字，容易顺手写 `TCSETS`** —— 切 raw 模式用
`TCSETS` 的典型症状是"切换瞬间吃到几个乱码字节"。

### §5.4 任意波特率：`TCGETS2` / `TCSETS2` + `BOTHER`

标准波特率是 `c_cflag` 里的一个枚举（`CBAUD` 掩码，`termbits.h:95`；
如 `B115200` = `0x00001002`，`:110`）。**要设枚举里没有的波特率
（比如 921600、或某些工业设备的 250000），必须走 `termios2` 那一路**：

```c
#include <asm/termbits.h>

static int set_custom_baud(int fd, unsigned int baud)
{
	struct termios2 t;                      /* termbits.h:19 */

	if (ioctl(fd, TCGETS2, &t) < 0)         /* ioctls.h:61 */
		return -1;

	t.c_cflag &= ~CBAUD;                    /* 清掉枚举字段 */
	t.c_cflag |= BOTHER;                    /* :108 —— "看 c_ospeed" */
	t.c_ispeed = baud;
	t.c_ospeed = baud;

	return ioctl(fd, TCSETS2, &t);          /* ioctls.h:62 */
}
```

⚠️ **`BOTHER`（`0x00001000`）和 `B115200`（`0x00001002`）在同一个掩码里**，
所以必须先 `&= ~CBAUD` 再 `|= BOTHER`，直接 `|=` 会得到一个既非枚举也非 BOTHER 的值。

### §5.5 `c_cc[]` 控制字符表（19 项）

索引常量在 `include/uapi/asm-generic/termbits.h:42-58`：

| 索引 | 名字 | 默认字符 | 作用（只在 `ICANON`/`ISIG` 开着时有效） |
|---|---|---|---|
| 0 | `VINTR` | `Ctrl-C` | 发 `SIGINT`（需 `ISIG`） |
| 1 | `VQUIT` | `Ctrl-\` | 发 `SIGQUIT`（需 `ISIG`） |
| 2 | `VERASE` | `Ctrl-?` | 删一个字符（需 `ICANON`） |
| 3 | `VKILL` | `Ctrl-U` | 删一整行（需 `ICANON`） |
| 4 | `VEOF` | `Ctrl-D` | 文件结束（需 `ICANON`） |
| 5 | `VTIME` | — | **超时，单位 0.1 秒**（需 **关掉** `ICANON`） |
| 6 | `VMIN` | — | **最少字节数**（需 **关掉** `ICANON`） |
| 8 / 9 | `VSTART` / `VSTOP` | `Ctrl-Q` / `Ctrl-S` | 软件流控（需 `IXON`） |
| 10 | `VSUSP` | `Ctrl-Z` | 发 `SIGTSTP`（需 `ISIG`） |
| 11 / 16 | `VEOL` / `VEOL2` | — | 额外的行结束符 |
| 12–15 | `VREPRINT` / `VDISCARD` / `VWERASE` / `VLNEXT` | | 扩展行编辑（需 `IEXTEN`，`:141`） |

⚠️ **`c_cc[VTIME]` / `c_cc[VMIN]` 和上面那些字符共用同一个数组、
但语义完全不同**（一个是"哪个字符触发动作"，一个是"数值参数"）。
索引 5/6 在 canonical 模式下被解释成 `VEOL`/`VEOF` 相关的旧语义——
所以切模式和设 `VMIN` 的顺序不能颠倒。

### §5.6 角色 5 ioctl 快速参考

| ioctl | 出处 | 一句话 |
|---|---|---|
| `TCGETS` / `TCSETS` | `ioctls.h:20` / `:21` | 读/写 termios（立即） |
| `TCSETSW` / `TCSETSF` | `ioctls.h:22` / `:23` | 写 termios（排空 / 排空+丢弃） |
| `TCGETS2` / `TCSETS2` | `ioctls.h:61` / `:62` | 带 `c_ispeed`/`c_ospeed` 的版本 |
| `TCFLSH` | `ioctls.h:30` | 主动丢弃队列 |
| `TCSBRK` | `ioctls.h:28` | 发 break 信号 |
| `TCXONC` | `ioctls.h:29` | 手工流控（挂起/恢复传输） |
| `FIONREAD` / `TIOCINQ` | `ioctls.h:46-47` | **同一个值** —— 接收队列里有几个字节 |
| `TIOCOUTQ` | `ioctls.h:36` | 发送队列里还剩几个字节 |
| `TIOCGWINSZ` / `TIOCSWINSZ` | `ioctls.h:38` / `:39` | 读/写窗口尺寸（`struct winsize`） |
| `TIOCMGET` / `TIOCMSET` | `ioctls.h:40` / `:43` | 读/写调制解调器线（RTS/DTR/CTS/DSR） |
| `TIOCMBIS` / `TIOCMBIC` | `ioctls.h:41` / `:42` | 只置位 / 只清位（不用先读） |
| `TIOCEXCL` / `TIOCNXCL` | `ioctls.h:31` / `:32` | 设/清独占标志 |

---

## §6 角色 6：用户态 —— pty 与作业控制

### §6.1 起一个 pty 跑子进程（terminal 模拟器的核心）

这是 `tmux`、`ssh` 服务端、`script(1)` 都在做的事：
**开一个 pty 主端，把从端交给子进程当控制终端。**

```c
#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <stdlib.h>

static int spawn_on_pty(char *const argv[])
{
	int master, slave, unlock = 0;
	unsigned int ptn;
	char slave_name[32];
	pid_t pid;

	/* 1. 打开 pty 多路器，拿到主端 */
	master = open("/dev/ptmx", O_RDWR | O_NOCTTY);
	if (master < 0)
		return -1;

	/* 2. 解锁从端。不做这一步，第 4 步 open 会失败 */
	if (ioctl(master, TIOCSPTLCK, &unlock) < 0)      /* ioctls.h:70 */
		return -1;

	/* 3. 问从端的编号（drivers/tty/pty.c:467 处理这个命令） */
	if (ioctl(master, TIOCGPTN, &ptn) < 0)           /* ioctls.h:69 */
		return -1;
	snprintf(slave_name, sizeof(slave_name), "/dev/pts/%u", ptn);

	pid = fork();
	if (pid < 0)
		return -1;

	if (pid == 0) {
		/* ── 子进程 ── */
		setsid();                        /* 必须先脱离原会话，见 §6.2 */

		/* 4. 打开从端（不带 O_NOCTTY，这样它自动成为控制终端） */
		slave = open(slave_name, O_RDWR);
		if (slave < 0)
			_exit(127);

		/* 5. 显式认领控制终端（配合 setsid 后更稳妥） */
		ioctl(slave, TIOCSCTTY, 0);      /* ioctls.h:33 */

		dup2(slave, 0);
		dup2(slave, 1);
		dup2(slave, 2);
		if (slave > 2)
			close(slave);
		close(master);

		execvp(argv[0], argv);
		_exit(127);
	}

	/* ── 父进程：读写 master 就是和子进程的终端对话 ── */
	return master;
}
```

⚠️ **`TIOCSPTLCK` 的参数是"锁不锁"，`0` 才是解锁。**
`ioctls.h:70` 的原型是 `_IOW('T', 0x31, int)`，传 `&unlock`（值为 0）= 解锁。
传非 0 是"重新锁上"。**忘了这一步的症状是第 4 步
`open("/dev/pts/N")` 返回 `-EIO`**，而不是任何和"锁"有关的提示。

`TIOCGPTPEER`（`ioctls.h:81`）是更安全的替代：**直接从主端 fd
拿到从端 fd，不经过路径名** ⇒ 免疫"容器里 `/dev/pts` 没挂对"和路径竞态。
代价是较新的内核才有，需要 fallback。

### §6.2 ⚠️ 作业控制：`setsid` → `TIOCSCTTY` → `TIOCSPGRP` 三步

**控制终端不是"打开了就有"**，它由三层身份决定：会话（session）、
进程组（process group）、控制终端（controlling tty）。tty 核心里
`drivers/tty/tty_jobctrl.c`（593 行）就是专门管这套的。

| 步骤 | 动作 | 为什么 |
|---|---|---|
| 1 | `setsid()` | **必须**：只有会话首进程（session leader）能认领控制终端；而且它必须**还没有**控制终端 |
| 2 | `open(slave)` 不带 `O_NOCTTY`，或显式 `ioctl(fd, TIOCSCTTY, 0)` | 认领。`TIOCSCTTY` 在 `ioctls.h:33` |
| 3 | `ioctl(fd, TIOCSPGRP, &pgrp)` | 指定"哪个进程组是前台"——只有前台组能读 tty、能收 `Ctrl-C` |

⚠️ **`setsid()` 失败最常见的原因是"调用者已经是进程组组长"**（返回 `-EPERM`）。
标准解法是 `fork()` 一次再在子进程里 `setsid()`——上面 §6.1 的代码正是这个顺序。

⚠️ **后台进程组读 tty 会收到 `SIGTTIN`、写 tty 会收到 `SIGTTOU`**，
而不是拿到错误码。如果你的程序"莫名其妙停住了"（状态 `T`），
先查 `TIOCGPGRP`（`ioctls.h:34`）看自己是不是前台组。

| ioctl | 出处 | 一句话 |
|---|---|---|
| `TIOCSCTTY` | `ioctls.h:33` | 认领控制终端（需先 `setsid`） |
| `TIOCNOTTY` | `ioctls.h:54` | 主动放弃控制终端 |
| `TIOCGPGRP` / `TIOCSPGRP` | `ioctls.h:34` / `:35` | 读/写前台进程组 |

### §6.3 换行规程：`TIOCSETD`

这是**用户态唯一的换 ldisc 手段**，也是 §3 写的 ldisc 被用上的方式：

```c
#include <linux/tty.h>          /* N_* 编号，uapi/linux/tty.h:10-41 */

static int switch_ldisc(int fd, int disc)
{
	int old;

	if (ioctl(fd, TIOCGETD, &old) < 0)      /* ioctls.h:56 —— 先存旧的 */
		return -1;
	if (ioctl(fd, TIOCSETD, &disc) < 0)     /* ioctls.h:55 */
		return -1;
	return old;
}

/* 典型用法：把一个串口变成 CAN 接口 */
/*   int disc = N_SLCAN;  switch_ldisc(fd, disc);   ← uapi/linux/tty.h:29 */
```

内核收到 `TIOCSETD` 后会 `request_module("tty-ldisc-<num>")` 自动装载——
这正是 §3.4 说 `MODULE_ALIAS_LDISC()` 不能省的原因。

⚠️ **换 ldisc 会把旧 ldisc 的缓冲丢掉**，而且旧 ldisc 的 `close` 和新的
`open` 之间存在一个窗口。如果新 ldisc 的 `open` 失败，核心会挂上
`n_null`（编号 27）兜底——**此后所有 `read`/`write` 都返回 `-EOPNOTSUPP`**
（`drivers/tty/n_null.c:26` / `:32`）。这个现象的排查在 `07-debug.md` 里有专节。

### §6.4 为什么不能用 `termios.c_line` 换 ldisc

`struct termios` 里明明有一个 `cc_t c_line` 字段
（`include/uapi/asm-generic/termbits.h:15`，注释就写着 `line discipline`），
但**在 Linux 上写它不会换 ldisc**。它是历史遗留（早期 System V 的做法），
Linux 只认 `TIOCSETD`。顺手改 `c_line` 再 `TCSETS` 的结果是：
调用成功、返回 0、ldisc 一点没变。

---

## §7 近邻符号区分（独立一节，必读）

下面每组的四件事：**在哪** → **真实区别** → **怎么选** → **用错会怎样**。

### §7.1 ⚠️ 组 1：`tty_alloc_driver` vs `__tty_alloc_driver`

| | 定位 | 是什么 |
|---|---|---|
| `tty_alloc_driver(lines, flags)` | `include/linux/tty_driver.h:484-485` | **宏** |
| `__tty_alloc_driver(lines, owner, flags)` | 声明 `include/linux/tty_driver.h:477`，定义 `drivers/tty/tty_io.c:3314` | 真函数，多一个 `owner` |

**真实区别**：宏只干一件事——把 `THIS_MODULE` 填进 `owner`。

**怎么选：永远用宏。** 直接调函数版的唯一正当场合是"你在写一个帮别的模块
建 driver 的库函数"，此时 `THIS_MODULE` 展开成的是**你的库**而不是调用者。

⚠️ **用错会怎样**：`owner` 填成错的模块 ⇒ 引用计数记在别人头上 ⇒
**你的模块在有 tty 打开着的时候仍然可以 `rmmod`** ⇒ 之后任何访问都是
已卸载代码的野指针。这类问题不会在测试里出现，只在生产上偶发崩。

### §7.2 ⚠️ 组 2：四个 flip 插入函数，尤其 `_flags` 和 `_fixed_flag`

全部声明在 `include/linux/tty_flip.h:13-42`：

| 函数 | 第 3 个参数 | 含义 |
|---|---|---|
| `tty_insert_flip_string_fixed_flag(port, chars, flag, size)` | **`char flag`** | **一个值** —— 整段共用同一个错误标志 |
| `tty_insert_flip_string_flags(port, chars, flags, size)` | **`const char *flags`** | **一个和 `chars` 等长的数组** —— 每字节各自的标志 |

⚠️ **两个名字只差 `s` 和 `fixed_`，而参数类型从"值"变成"指针"。**
**用错会怎样**：把一个 `TTY_NORMAL`（值为 0）当数组传给 `_flags` 版 ⇒
它会去 `NULL` 地址读 `size` 个字节 ⇒ 中断上下文里的空指针解引用。
反过来把数组传给 `_fixed_flag` 版 ⇒ 指针被截成一个 `char`，
整段数据被打上一个随机错误标志。

另两个是便捷层：`tty_insert_flip_string()`（`:38-42`）就是
`_fixed_flag` + 硬传 `TTY_NORMAL`；`tty_insert_flip_char()`（`:22-36`）
是 `static inline` 的单字节快路径，未命中时才落到 `__tty_insert_flip_char()`。

⚠️ **`__tty_insert_flip_char()` 带两个下划线但是导出的**（`drivers/tty/tty_buffer.c:408`）——
别因为它导出就直接调它，你会绕掉 inline 快路径，白付一次函数调用。

### §7.3 ⚠️ 组 3：`tty_ldisc_ref` vs `tty_ldisc_ref_wait`

| | 定位 | 拿不到时 |
|---|---|---|
| `tty_ldisc_ref(tty)` | `drivers/tty/tty_ldisc.c:260`（EXPORT `:271`） | **立即返回 `NULL`** |
| `tty_ldisc_ref_wait(tty)` | `drivers/tty/tty_ldisc.c:240`（EXPORT `:250`） | **睡着等** |

**怎么选**：看你在什么上下文。

- **中断/atomic/持锁**里 ⇒ 只能用 `tty_ldisc_ref()`，**并且必须判 `NULL`**
- 进程上下文、可睡眠、且逻辑上必须拿到 ⇒ `tty_ldisc_ref_wait()`

⚠️ **用错会怎样**：在中断里调 `_wait` 版 ⇒ 在 atomic 上下文睡眠 ⇒
`BUG: scheduling while atomic`。反过来用了 `tty_ldisc_ref()` 却不判 `NULL` ⇒
换 ldisc 的窗口期（`TIOCSETD` 执行中）刚好命中就是空指针。

**两者都必须配 `tty_ldisc_deref(ld)`**（`:284`）。只有 `ref` 返回
`NULL` 时不需要 deref。

### §7.4 ⚠️ 组 4：三个 register，分工完全不同

| 函数 | 定位 | 注册的是 |
|---|---|---|
| `tty_register_driver(driver)` | `drivers/tty/tty_io.c:3416` | **driver 本身**（一次） |
| `tty_register_device(driver, index, dev)` | `drivers/tty/tty_io.c:3182` | **一个设备节点**（每条线一次） |
| `tty_port_register_device(port, driver, index, dev)` | `drivers/tty/tty_port.c:139`（EXPORT `:145`） | **link + 节点，二合一** |

**真实区别在于 `TTY_DRIVER_DYNAMIC_DEV`（`include/linux/tty_driver.h:550`）**：

- **不带这个标志** ⇒ `tty_register_driver()` 自己把 `driver->num` 条线的
  节点全建好，你**不要**再调 `tty_register_device()`
- **带这个标志** ⇒ register_driver 一个节点都不建，你必须在设备真实出现时
  逐个 `tty_register_device()`（USB 串口、蓝牙这类热插拔设备走这条）

**`tty_port_register_device()` 是 §1 里 `tty_port_link_device()` +
`tty_register_device()` 的合并版**。用了它就不要再单独 link。

⚠️ **用错会怎样**：没带 `DYNAMIC_DEV` 又调 `tty_register_device()` ⇒
同一个节点注册两次，sysfs 报 `duplicate` 且第二次失败；
带了 `DYNAMIC_DEV` 却忘了调 ⇒ **驱动加载成功、`/proc/tty/drivers` 里也有，
但 `/dev/` 下一个节点都没有**。

### §7.5 ⚠️ 组 5：四个 hangup，两个维度

两个维度：**同步还是异步**、**参数是 tty 还是 port**。

| 函数 | 定位 | 参数 | 同步性 |
|---|---|---|---|
| `tty_hangup(tty)` | `drivers/tty/tty_io.c:683`（EXPORT `:688`） | `tty` | **异步** —— 只 `schedule_work()`，立刻返回 |
| `tty_vhangup(tty)` | `drivers/tty/tty_io.c:698`（EXPORT `:703`） | `tty` | **同步** —— 直接调 `__tty_hangup()`，返回时已挂完 |
| `tty_port_hangup(port)` | `drivers/tty/tty_port.c:394`（EXPORT `:412`） | `port` | 处理 port 侧状态 |
| `tty_port_tty_hangup(port, check_clocal)` | `drivers/tty/tty_port.c:419`（EXPORT `:427`） | `port` | **内部自己 get/put tty**，还能按 `CLOCAL` 跳过 |

**同步性的理由写在 kerneldoc 里**（`tty_io.c:698` 上方）：`tty_vhangup`
之所以同步，是因为 **`vhangup(2)` 系统调用返回时必须保证挂断已完成——
"That guarantee is necessary for security reasons"**（防止旧终端上的进程
在新登录会话里还能读到数据）。

**怎么选**：

- 驱动检测到载波丢失（中断里）⇒ `tty_port_tty_hangup(port, true)`。
  `check_clocal=true` 让它在 termios 设了 `CLOCAL` 时不挂断（本地直连不该因载波挂）
- `tty_operations.hangup` 回调里 ⇒ `tty_port_hangup(port)`
- 手上只有 tty、又在中断里 ⇒ `tty_hangup(tty)`（异步版）

⚠️ **用错会怎样**：在中断里调 `tty_vhangup()` ⇒ 它同步走
`__tty_hangup()`，而后者要拿 `termios_rwsem`、遍历 tasklist（kerneldoc
`tty_io.c:566-577` 列了 6 把锁）⇒ 死锁或 atomic 里睡眠。

### §7.6 ⚠️ 组 6：`tty->port` vs `tty_port_tty_get()` —— 方向决定要不要计数

判据一句话：**顺着调用方向走不用计数，逆着走必须计数。**

| 你在哪 | 手上有 | 要什么 | 怎么拿 |
|---|---|---|---|
| `tty_operations` 回调里 | `tty` | `port` | **`tty->port`，直接用** —— 核心保证回调期间 tty 活着 |
| 中断/工作线程里 | `port` | `tty` | **`tty_port_tty_get(port)`（`drivers/tty/tty_port.c:322`）+ 判 `NULL` + `tty_kref_put()`** |

⚠️ **`tty_port_tty_get()` 可能返回 `NULL`** —— 设备存在但当前没被任何进程打开。
**用错会怎样**：不判 `NULL` ⇒ 拔掉线时的中断里空指针；
拿了不 `put` ⇒ `tty_struct` 永远不释放，`rmmod` 卡死在 unregister。

**能用包装就别自己 get**：`tty_port_tty_wakeup(port)`（`:433`）和
`tty_port_tty_hangup(port, ...)`（`:419`）内部已经做了 get/put，
用它们比"自己 get + 调 tty 版 + put"少两行、也少一个泄漏点。

### §7.7 ⚠️ 组 7：`tty_write_room` vs `tty_chars_in_buffer` —— 方向相反

| 函数 | 定位 | 返回 |
|---|---|---|
| `tty_write_room(tty)` | `drivers/tty/tty_ioctl.c:83`（EXPORT） | **还能再收多少字节** |
| `tty_chars_in_buffer(tty)` | `drivers/tty/tty_ioctl.c:64`（EXPORT） | **已经积压了多少字节** |

两个都是"查询发送侧"，但一个查空位、一个查存量。
**用错会怎样**：用 `chars_in_buffer` 的返回值当"可写空间"来决定写多少 ⇒
缓冲空的时候它返回 0 ⇒ 你以为写不进去，**永远不写** ⇒ 表现为"设备完全不出数据"。

助记：**`room` 是空位，`chars_in` 是货**。

### §7.8 ⚠️ 组 8：`tty_unthrottle` vs `tty_unthrottle_safe` —— 而且**没有** `tty_throttle`

⚠️ **这一组最反直觉的地方：核心层导出了 `tty_unthrottle()`，
但根本没有 `tty_throttle()`。** 已核对：`drivers/tty/` 与 `include/linux/`
下 `tty_throttle` 的唯一命中是 `drivers/tty/n_gsm.c:2971` 的一句注释
（`/* If clogged call tty_throttle(tty); */`）——**注释提到的这个函数并不存在。**

| 函数 | 定位 | 有加锁保护？ |
|---|---|---|
| `tty_unthrottle(tty)` | 定义 `drivers/tty/tty_ioctl.c:113`，EXPORT `:122` | **没有** |
| `tty_throttle_safe(tty)` | `drivers/tty/tty_ioctl.c:137`，声明 `include/linux/tty.h:419` | **有** |
| `tty_unthrottle_safe(tty)` | `drivers/tty/tty_ioctl.c:168`，声明 `include/linux/tty.h:420` | **有** |
| ~~`tty_throttle(tty)`~~ | **不存在** | — |

**区别是加锁**，而且 kerneldoc 把它说得很清楚
（`include/linux/tty.h:137-138`）：`throttle_mutex` 保护的是
**"并发的 `tty_throttle_safe()` 和 `tty_unthrottle_safe()`"**，
并明确加了一句 **`(but not tty_unthrottle())`**。
另外 safe 版还会看 `tty->flow_change`（kerneldoc 在 `:169-170`）来决定要不要真动作。

**怎么选**：

- **ldisc 里做流控 ⇒ 一律用 `_safe` 版**。`n_tty` 就是这么用的：
  `drivers/tty/n_tty.c:264` 调 `tty_throttle_safe()`、`:295` 和 `:1736` 调 `tty_unthrottle_safe()`
- `tty_unthrottle()` 是给 tty 核心自己在"确定没有并发"的路径上用的

⚠️ **用错会怎样**：在 ldisc 里混用 `tty_unthrottle()` 和
`tty_throttle_safe()` ⇒ 两者不走同一把锁 ⇒ **流控状态错乱**：
可能停在 throttled 再也不恢复（表现为"读几 KB 之后就卡住不动"），
也可能该 throttle 时没 throttle（表现为高速率下丢字节）。

### §7.9 ⚠️ 组 9：`receive_buf` vs `receive_buf2` —— 后者能做背压

两个都是 `tty_ldisc_ops` 的成员（`include/linux/tty_ldisc.h:211-248`）：

| 成员 | 返回值 | 能做流控？ |
|---|---|---|
| `receive_buf(tty, cp, fp, count)` | **`void`** | **不能** —— 没法告诉核心"我只吃下了一部分" |
| `receive_buf2(tty, cp, fp, count)` | **`int`** = 实际消费的字节数 | **能** |

**优先级是硬编码的**，看 `tty_ldisc_receive_buf()`
（`drivers/tty/tty_buffer.c:453`，EXPORT `:465`）：

```c
if (ld->ops->receive_buf2)
	count = ld->ops->receive_buf2(ld->tty, p, f, count);
else {
	...
	if (count && ld->ops->receive_buf)
		ld->ops->receive_buf(ld->tty, p, f, count);
}
```

**两个都填时，`receive_buf` 永远不会被调用。** 这不是"回退链"，是二选一。

头文件的 kerneldoc（`include/linux/tty_ldisc.h:187`）明确推荐：
**"If assigned, prefer this function for automatic flow control."**

⚠️ **用错会怎样**：以为"填两个更保险" ⇒ 你精心写的 `receive_buf` 是死代码，
所有数据都走了 `receive_buf2`。反过来只填 `receive_buf` 而数据率很高 ⇒
没有背压手段，flip buffer 满了就直接丢字节，且**不会有任何日志**。

第三个相关成员 `lookahead_buf` 的语义写在 `:193`：它处理
**"没被 `receive_buf()` 或 `receive_buf2()` 吃掉的字符"**，
用于提前发现软件流控字符。

### §7.10 ⚠️ 组 10：`tty_termios_copy_hw` vs `tty_termios_hw_change`

| 函数 | 定位 | 做什么 |
|---|---|---|
| `tty_termios_copy_hw(new, old)` | `drivers/tty/tty_ioctl.c:262`（EXPORT） | **动作**：把硬件相关字段从 old 拷到 new |
| `tty_termios_hw_change(a, b)` | `drivers/tty/tty_ioctl.c:281`（EXPORT） | **判断**：硬件相关字段有没有变，返回布尔 |

典型配合：驱动的 `set_termios` 回调里先
`if (!tty_termios_hw_change(&tty->termios, old))  return;` 提前退出，
避免只改了 `ECHO` 这种软件位也去重新配置一次波特率发生器。

⚠️ **用错会怎样**：名字都以 `tty_termios_` 开头、都带 `hw`，
把判断函数当动作函数调（`tty_termios_hw_change(new, old);` 丢弃返回值）⇒
编译通过、不报警、**什么也没发生** ⇒ 新 termios 里的硬件字段是 0。

### §7.11 ⚠️ 组 11：`tty_get_char_size` vs `tty_get_frame_size` —— 差 2 位

| 函数 | 定位 | 返回 |
|---|---|---|
| `tty_get_char_size(cflag)` | `drivers/tty/tty_ioctl.c:290`（EXPORT `:304`），声明 `include/linux/tty.h:452` | **数据位数**（5/6/7/8） |
| `tty_get_frame_size(cflag)` | `drivers/tty/tty_ioctl.c:315`（EXPORT `:328`），声明 `include/linux/tty.h:453` | **整帧位数** = 起始位 + 数据 + 校验 + 停止位 |

**关系在代码里一眼可见**（`tty_ioctl.c:317`）：
`unsigned char bits = 2 + tty_get_char_size(cflag);` —— 先加起始位和一个停止位，
再按 `PARENB` / `CSTOPB` 加。

**怎么选**：算传输耗时（超时、DMA 长度）用 `frame_size`；
算缓冲区里放几个字节用 `char_size`。

⚠️ **用错会怎样**：用 `char_size` 算超时 ⇒ 算出来比真实值小 20%~30% ⇒
`tty_wait_until_sent()` 之类的等待提前返回，数据还在线上就被当成发完了。

### §7.12 ⚠️ 组 12：有完整 kerneldoc ≠ 你能调用

**模板里点名的高危项：写满 kerneldoc 的内部函数比没注释的更危险**，
因为它读起来最像公开 API。

本子系统最典型的例子是 `__tty_hangup()`（`drivers/tty/tty_io.c:578`）：

- 它有**本文件里最详尽的 kerneldoc**——连 6 把锁的获取顺序都列了
  （`drivers/tty/tty_io.c:566-577`：BTM、redirect lock、file list lock、
  `tty_ldiscs_lock`、`termios_rwsem`、`tasklist_lock`）
- **但它是 `static`，也没有 `EXPORT_SYMBOL`，头文件里没有声明** ⇒ 你调不到

**两条判据别混**（这是本篇最重要的一条方法）：

| 问题 | 判据 |
|---|---|
| 这个符号在本版本**存在**吗 | `grep` 有命中。**注意 tab 陷阱**：`#define` 和宏名之间常是 TAB 或多个空格（如 `CS8` 在 `include/uapi/asm-generic/termbits.h:100` 写作 `#define CS8`），所以要用 `grep -nE 'define[[:space:]]+CS8\b'`，不要用 `grep 'define CS8'` |
| 我**能不能用** | **`EXPORT_SYMBOL*` 且头文件里有声明**。**不是"有没有 kerneldoc"** |

想触发挂断，用它导出的两个包装：`tty_hangup()`（`:683`，异步）
或 `tty_vhangup()`（`:698`，同步）—— 区别见 §7.5。

---

## §8 常见错误现象 → 原因

| 现象 | 最可能的原因 | 去哪一节 |
|---|---|---|
| **`read`/`write` 一律返回 `-EOPNOTSUPP`** | **ldisc 被换成了 `n_null`（27）** —— 你的 ldisc `open` 失败后核心的兜底动作（`drivers/tty/n_null.c:26` / `:32`） | §6.3 |
| 驱动加载成功、`/proc/tty/drivers` 里有，`/dev/` 下没节点 | 带了 `TTY_DRIVER_DYNAMIC_DEV` 却没调 `tty_register_device()` | §7.4 |
| 同一节点注册两次，sysfs 报 duplicate | 没带 `DYNAMIC_DEV` 又手工 register_device | §7.4 |
| 第二个进程 `open` 拿到 `-EBUSY` | `request_irq` 写在 `open` 而不是 `activate` | §2.2 |
| 第一个进程 `close` 后设备就死了 | `free_irq` 写在 `close` 而不是 `shutdown` | §2.2 |
| **`open` 时空指针崩在 `tty_port_*`** | **忘了 `tty_port_link_device()`** ⇒ `tty->port` 为 `NULL` | §1.2 第 4 点 |
| `open` 行为诡异、波特率是 0 | `init_termios` 没先拷 `tty_std_termios` | §1.2 第 3 点 |
| `if (!driver)` 判空后仍然崩 | `tty_alloc_driver()` 返回 `ERR_PTR` 不是 `NULL` | §1.2 第 2 点 |
| `rmmod` 卡死 / 模块永远 `Used by 1` | `tty_port_tty_get()` 拿了没 `tty_kref_put()` | §7.6 |
| 有 tty 打开着却能 `rmmod`，之后随机崩 | `__tty_alloc_driver()` 的 `owner` 传错 | §7.1 |
| 中断里 `BUG: scheduling while atomic` | 用了 `tty_ldisc_ref_wait()` 或 `tty_vhangup()` | §7.3 / §7.5 |
| 换 ldisc 瞬间偶发空指针 | `tty_ldisc_ref()` 的返回值没判 `NULL` | §7.3 |
| **注册新 ldisc 后，别的协议不工作了** | `num` 撞号 —— `tty_register_ldisc()` 直接覆盖且**不报错** | §3.3 |
| `TIOCSETD` 返回 `-EINVAL`，手工 `modprobe` 后就好了 | 漏了 `MODULE_ALIAS_LDISC()` | §3.4 |
| 设了 `VMIN`/`VTIME` 完全不生效 | `ICANON` 还开着 | §5.2 |
| 切 raw 模式瞬间吃到几个乱码字节 | 用了 `TCSETS` 而不是 `TCSETSF` | §5.3 |
| 改 `c_line` 换 ldisc，返回 0 但没换 | Linux 不认 `c_line`，只认 `TIOCSETD` | §6.4 |
| **`open("/dev/pts/N")` 返回 `-EIO`** | **漏了 `TIOCSPTLCK` 解锁** | §6.1 |
| `setsid()` 返回 `-EPERM` | 调用者已经是进程组组长 ⇒ 先 `fork()` | §6.2 |
| 进程莫名停住（状态 `T`） | 后台进程组读写 tty 收到 `SIGTTIN`/`SIGTTOU` | §6.2 |
| 自定义波特率设了不生效 | 没 `&= ~CBAUD` 就 `|= BOTHER` | §5.4 |
| 设备完全不出数据 | 把 `tty_chars_in_buffer()` 当可写空间用 | §7.7 |
| 读几 KB 后卡住不动 / 高速率丢字节 | 混用 `tty_unthrottle()` 和 `_safe` 版 | §7.8 |
| 精心写的 `receive_buf` 从没被调用 | 同时填了 `receive_buf2`，后者优先且互斥 | §7.9 |
| 高速率静默丢字节、无任何日志 | 只填 `receive_buf`，没有背压手段 | §7.9 |
| 中断里空指针，崩在 flip 插入 | 把单个 flag 值传给了 `..._string_flags()`（要数组） | §7.2 |
| `set_termios` 后硬件字段全是 0 | 把 `tty_termios_hw_change()` 当动作函数用 | §7.10 |
| `tty_wait_until_sent()` 提前返回 | 用 `char_size` 而不是 `frame_size` 算超时 | §7.11 |
| 照 kerneldoc 调 `__tty_hangup()` 链接失败 | 它是 `static`，kerneldoc 再全也调不到 | §7.12 |

---

## §9 阅读下一篇

| 你还想知道 | 去哪篇 |
|---|---|
| DT 参与度（结论：极低） | `06-devicetree.md` |
| **`/proc/tty/` 观测、`-EOPNOTSUPP` 的完整排查** | `07-debug.md` |
| 四个对象的寿命差异、"from above / from below" 方向标 | `02-datastructures.md` |
| 三把锁与主流程 | `03-internals.md` |
| `n_tty` / `pty` / `n_null` 各自做了什么 | `04-extensions.md` |
| 为什么要有 flip buffer、为什么 port 和 struct 要分开 | `08-feynman.md` |
| 全部取证记录与计数口径 | `99-evidence.md` |



