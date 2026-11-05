---
title: 内部实现
---

# tty 03：内部机制

## §0 导航

**本篇只承诺一条主轴：Part A 是时间线**（从驱动注册到 tty 释放）。
**Part B 是横切关注点**（锁 / 引用计数 / 流控 / hangup），**Part C 是评价与设计债。**
**章节号带 A/B/C 前缀。**

### §0.1 分层图

```
   用户态 open/read/write/ioctl/close
        │
  ┌─────▼────────────────────────────────────────────────────────────┐
  │ drivers/tty/tty_io.c（3608 行）—— 字符设备入口 + 驱动/设备注册     │
  │   tty_fops · tty_open · tty_release · tty_read · tty_write        │
  │   tty_ioctl · tty_hangup · tty_init_dev                          │
  └─────┬───────────────────────┬────────────────────────────────────┘
        │                       │
  ┌─────▼───────────┐   ┌───────▼──────────────────────────────────┐
  │ tty_ioctl.c     │   │ tty_ldisc.c（861）+ tty_ldsem.c（430）    │
  │ （1004）termios │   │   ldisc 注册表 · 切换 · 引用 · 专用信号量  │
  └─────────────────┘   └───────┬──────────────────────────────────┘
                                │ 层 2：tty_ldisc_ops（18）
                        ┌───────▼──────────────────────────────────┐
                        │ n_tty.c（2549）或其他 ldisc               │
                        └───────┬──────────────────────────────────┘
                                │
  ┌─────────────────────────────▼────────────────────────────────────┐
  │ tty_buffer.c（698）—— flip buffer（只在接收方向）                │
  └─────────────────────────────┬────────────────────────────────────┘
                                │ 层 1：tty_operations（37）
  ┌─────────────────────────────▼────────────────────────────────────┐
  │ tty 驱动（通常经 tty_port.c 796 行的辅助）                       │
  └───────────────────────────────────────────────────────────────────┘

  旁路：tty_jobctrl.c（593）—— 控制终端 / 前台进程组 / SIGHUP
```

### §0.2 生命周期时间线

```
 模块 init
   │
   ├─ tty_alloc_driver(lines, flags)                §A1  tty_io.c:3369（__tty_alloc_driver）
   ├─ 填 6 个必需字段 + tty_set_operations()
   └─ tty_register_driver(driver)                   §A2  tty_io.c:3473
        ├─ major==0 ⇒ alloc_chrdev_region()               :3424
        ├─ else      ⇒ register_chrdev_region()           :3432
        ├─ tty_cdev_add()                                 :3438
        └─ 没传 TTY_DRIVER_DYNAMIC_DEV ⇒ 一次注册所有设备

 设备 probe
   ├─ tty_port_init(port)                           §A3  tty_port.c:106
   └─ tty_port_register_device(port, driver, idx, dev)     tty_port.c:145

 用户态第一次 open("/dev/ttyX")
   └─ tty_open()                                    §A4  tty_io.c:2112
        ├─ nonseekable_open()                             :2119
        ├─ tty_alloc_file(filp)                           :2122
        ├─ tty_open_current_tty() ?: tty_open_by_driver() :2126-2128
        │     └─ tty_init_dev(driver, idx)          §A5  tty_io.c:1392
        │          ├─ try_module_get(driver->owner)        :1405
        │          ├─ alloc_tty_struct()                   :1408
        │          ├─ tty_driver_install_tty()             :1415  ops->install
        │          ├─ tty->port = driver->ports[idx]       :1419-1420
        │          ├─ WARN_RATELIMIT(!tty->port, …)      :1422-1424
        │          ├─ tty_ldisc_lock(tty, 5*HZ)            :1429  5 秒超时
        │          ├─ tty->port->itty = tty                :1432
        │          └─ tty_ldisc_setup(tty, tty->link)      :1439  ldisc open
        ├─ tty_add_file(tty, filp)                        :2139
        ├─ tty->ops->open(tty, filp)                      :2144-2145  层 1
        │     └─ 多数驱动转发 tty_port_open()        §A6  tty_port.c:796
        ├─ clear_bit(TTY_HUPPED, &tty->flags)             :2169
        └─ !noctty ⇒ tty_open_proc_set_tty()        §A7  :2171-2177

 read / write / ioctl                              §A8

 ldisc 切换（TIOCSETD）                             §A9  tty_ldisc.c:598

 最后一次 close
   └─ tty_release()                                 §A10 tty_io.c
        ├─ tty->ops->close() ⇒ tty_port_close()
        ├─ tty_save_termios(tty)                          :1470（导出于 :1489）
        └─ release_tty() ⇒ tty_ldisc_release ⇒ free
```

### §0.3 哪些内容不在本篇

| 想找 | 去 |
|---|---|
| API 清单 | `01-api.md` |
| 结构体与七组易错关系 | `02-datastructures.md` |
| **PTY / n_tty / flip buffer 细节** | `04-extensions.md` |
| 驱动骨架 | `05-usage.md` |
| DT | `06-devicetree.md` |
| `/proc/tty/` 与观测 | `07-debug.md` |

---

# Part A：时间线

## §A1 驱动分配（`tty_io.c:3369` 处导出）

| 事实 | 说明 |
|---|---|
| **`tty_alloc_driver(lines, flags)` 是宏** | **展开成 `__tty_alloc_driver(lines, THIS_MODULE, flags)`** ⇒ **`owner` 自动设** |
| **`lines` 是"最多几个设备"** | **落在 `driver->num`**；**核心拒绝注册超过这个数的设备**（官方文档明说） |
| **`flags` 是 8 个 `TTY_DRIVER_*` 的或** | 01 §3.3 |
| **返回 `ERR_PTR`** | — |
| **释放用 `tty_driver_kref_put()`** | `drivers/tty/tty_io.c:3408` 处导出 |

**官方 `tty_driver.rst` 原话**：
> *"When the driver is about to be freed, tty_driver_kref_put() is called on that. It will decrements
> the reference count and if it reaches zero, the driver is freed."*

## §A2 驱动注册：字符设备区间的两条路

```c
/* drivers/tty/tty_io.c:3424 与 :3432 —— tty_register_driver() 里 */
		error = alloc_chrdev_region(&dev, driver->minor_start, …);   /* major == 0 */
	…
		error = register_chrdev_region(dev, driver->num, driver->name); /* major != 0 */
	…
	error = tty_cdev_add(driver, dev, 0, driver->num);               /* :3438 */
```

| `driver->major` | 走哪条 | 结果 |
|---|---|---|
| **0** | **`alloc_chrdev_region()`**（`:3424`） | **动态分配主设备号** |
| **非 0** | **`register_chrdev_region()`**（`:3432`） | **用指定的主设备号**（冲突会失败） |

⇒ **每个 tty 驱动有自己的 cdev 区间**（对比 watchdog 只有一个全局区间）。

⚠️ **`TTY_DRIVER_DYNAMIC_DEV` 决定"注册驱动"是否顺带"注册所有设备"**（01 §4.1）——
**官方文档推荐传它，然后在 probe 里逐个注册。**

## §A3 设备注册与 port 绑定

**三种绑定方式的优先级见 01 §4.3。本节只强调核心的一处保护**：

```c
/* drivers/tty/tty_io.c:1419-1427 —— tty_init_dev() 里 */
	if (!tty->port)
		tty->port = driver->ports[idx];

	if (WARN_RATELIMIT(!tty->port,
			"%s: %s driver does not set tty->port. This would crash the kernel. Fix the driver!\n",
			__func__, tty->driver->name)) {
		retval = -EINVAL;
		goto err_release_lock;
	}
```

⇒ **`tty->port` 为 NULL 是致命错误，核心用 `WARN_RATELIMIT` + `-EINVAL` 挡住。**
**消息逐字包含 "This would crash the kernel. Fix the driver!"**（07 §4）
⇒ **这是本子系统最直白的一条"驱动写错了"的诊断。**

**两条赋值路径**：
**① `ops->install` 里自己设（`tty_port_install()`）；② 核心从 `driver->ports[idx]` 取。**
⇒ **所以"用 `tty_port_register_device()` 注册"就等于让路径 ② 生效。**

## §A4 `tty_open()`：本子系统最复杂的一个函数

**位置：`drivers/tty/tty_io.c:2112`。它的 kerneldoc 有 22 行，本身就是一份说明。**

### §A4.1 八步

| 步 | 代码 | 位置 |
|---|---|---|
| ① | `nonseekable_open(inode, filp)` | `:2119` |
| ② | `tty_alloc_file(filp)` ——**失败返回 `-ENOMEM`** | `:2122-2124` |
| ③ | **两条查找路径**：`tty_open_current_tty()` 失败再 `tty_open_by_driver()` | `:2126-2128` |
| ④ | **`-EAGAIN` ⇒ `goto retry_open`** | `:2130-2136` |
| ⑤ | `tty_add_file(tty, filp)` | `:2139` |
| ⑥ | **`tty->ops->open(tty, filp)`；没有这个回调 ⇒ `-ENODEV`** | `:2144-2147` |
| ⑦ | **失败时的重试逻辑**（见 §A4.2） | `:2150-2168` |
| ⑧ | **`clear_bit(TTY_HUPPED)` + 设控制终端** | `:2169-2178` |

### §A4.2 ⚠️ 两处 `goto retry_open`：open 会重试

```c
/* drivers/tty/tty_io.c:2130-2136 —— 查找失败 */
	if (IS_ERR(tty)) {
		tty_free_file(filp);
		retval = PTR_ERR(tty);
		if (retval != -EAGAIN || signal_pending(current))
			return retval;
		…
		goto retry_open;
	}
/* :2150-2167 —— ops->open 失败 */
	if (retval) {
		…
		tty_unlock(tty);		/* need to call tty_release without BTM */
		tty_release(inode, filp);
		if (retval != -ERESTARTSYS)
			return retval;
		if (signal_pending(current))
			return retval;
		schedule();
		if (tty_hung_up_p(filp))
			filp->f_op = &tty_fops;
		goto retry_open;
	}
```

| 触发重试的码 | 场景 |
|---|---|
| **`-EAGAIN`** | **查找阶段的临时冲突** |
| **`-ERESTARTSYS`** | **`ops->open` 被信号打断**（典型是 `tty_port_block_til_ready()` 在等载波） |

⇒ **`open("/dev/ttyS0")` 可能在内核里循环好几轮** ——
**`signal_pending(current)` 是唯一的出口保证。**

⚠️ **`filp->f_op = &tty_fops` 那一行（`:2166`）** ——
**注释说明是"hangup 可能已经把 f_op 换掉了，重试前要换回来"**
⇒ **这是 tty 里"hangup 会改 `f_op`"这个机制留下的痕迹**（§B4）。

### §A4.3 `noctty` 的四个条件

```c
/* drivers/tty/tty_io.c:2171-2175 */
	noctty = (filp->f_flags & O_NOCTTY) ||
		 (IS_ENABLED(CONFIG_VT) && device == MKDEV(TTY_MAJOR, 0)) ||
		 device == MKDEV(TTYAUX_MAJOR, 1) ||
		 (tty->driver->type == TTY_DRIVER_TYPE_PTY &&
		  tty->driver->subtype == PTY_TYPE_MASTER);
```

| 条件 | 含义 |
|---|---|
| `O_NOCTTY` | **调用者显式要求"别当我的控制终端"** |
| `MKDEV(TTY_MAJOR, 0)` | **`/dev/tty0`（当前 VT）** |
| `MKDEV(TTYAUX_MAJOR, 1)` | **`/dev/console`** |
| **PTY master** | **主端永远不能当控制终端** |

⇒ **只有都不满足才调 `tty_open_proc_set_tty()`** ——**那才是"这个 tty 变成我的控制终端"。**
**作业控制的全部逻辑在 `drivers/tty/tty_jobctrl.c`（593 行）。**

## §A5 `tty_init_dev()`：`tty_struct` 的诞生（`tty_io.c:1392-1462`）

### §A5.1 七步 + 四个错误标签

| 步 | 代码 | 位置 | 失败去哪 |
|---|---|---|---|
| ① | **`try_module_get(driver->owner)`** | `:1405-1406` | `-ENODEV` |
| ② | `alloc_tty_struct(driver, idx)` | `:1408-1412` | `err_module_put` |
| ③ | `tty_lock(tty)` | `:1414` | — |
| ④ | **`tty_driver_install_tty()`** ——**调 `ops->install` 或默认的 `tty_standard_install()`** | `:1415-1417` | `err_free_tty` |
| ⑤ | **`tty->port` 的确定 + `WARN_RATELIMIT` 保护** | `:1419-1427` | `err_release_lock` |
| ⑥ | **`tty_ldisc_lock(tty, 5 * HZ)`** | `:1429-1431` | `err_release_lock` |
| ⑦ | **`tty_ldisc_setup(tty, tty->link)`** ——**调 ldisc 的 `open`** | `:1439-1441` | `err_release_tty` |

**四个错误标签的分工**（`:1446-1461`）：

| 标签 | 做什么 |
|---|---|
| `err_free_tty` | **`tty_unlock` + `free_tty_struct`** |
| `err_module_put` | **`module_put(driver->owner)`** |
| `err_release_tty` | **`tty_ldisc_unlock` + `tty_info_ratelimited("ldisc open failed …")`** ⇒ **落到下一个标签** |
| `err_release_lock` | **`tty_unlock` + `release_tty(tty, idx)`** |

**kerneldoc 的承诺（逐字）**：
> *"This code guarantees that either everything succeeds and the TTY is ready for operation, or else
> the table slots are vacated and the allocated memory released. (Except that the termios may be
> retained.)"*

⇒ **"termios 可能被保留"是有意的** ——**`driver->termios[idx]` 跨 open 继承**（§A10.2）。

### §A5.2 ⚠️`tty_ldisc_lock(tty, 5 * HZ)`：5 秒超时

**两处用同一个超时**：
**`drivers/tty/tty_io.c:1429`（open 路径）· `drivers/tty/tty_ldisc.c:530`（`tty_set_ldisc`）。**

⇒ **`ld_semaphore` 存在的直接理由**（02 §6.3）：
**标准 `rw_semaphore` 没有"带超时的写等待"。**
⇒ **超时的后果是 open 或 `TIOCSETD` 返回错误，而不是永久阻塞。**

## §A6 `tty_port_open()`：驱动侧 open 的标准实现（`tty_port.c:796`）

**官方 `tty_port.rst` 原话**：
> *"If the drivers implement tty_port.ops.activate() and tty_port.ops.shutdown(), they can use
> tty_port_open(), tty_port_close(), and tty_port_hangup() in respective tty_struct.ops hooks."*

**它做的四件事**（详细行号在 04 篇）：
1. **`port->count++`（在 `port->mutex` 下）**
2. **第一次 open ⇒ 调 `port->ops->activate(port, tty)`**
3. **`tty_port_block_til_ready(port, tty, filp)`** ——**等载波/DTR**（`tty_port.c:594`）
4. **设置 `ASYNCB_INITIALIZED` 之类的 `port->flags`**

⚠️ **第 3 步是 `-ERESTARTSYS` 的来源**（§A4.2）：
**它在 `port->open_wait` 上睡，被信号打断就返回 `-ERESTARTSYS`**
⇒ **然后 `tty_open()` 会重试。**

## §A7 控制终端与作业控制（`tty_jobctrl.c` 593 行）

| 概念 | 代码 |
|---|---|
| **控制终端** | **`current->signal->tty`** ——**`get_current_tty()`（`drivers/tty/tty_jobctrl.c:170` 处导出）** |
| **前台进程组** | **`tty->ctrl.pgrp`**（`include/linux/tty.h:223`）——**`tty_get_pgrp()`（`tty_jobctrl.c:432`）** |
| **会话** | **`tty->ctrl.session`**（`include/linux/tty.h:224`） |
| **后台进程写终端的检查** | **`tty_check_change()`（`tty_jobctrl.c:73` 处导出）** ⇒ **可能发 `SIGTTOU`** |

⚠️ **这三个字段都在 `tty->ctrl` 这个内嵌结构里，由 `ctrl.lock` 这把 spinlock 保护**（02 §5.1）。

⚠️ **`tty_jobctrl.c:341` 有一条 `FIXME: Review locking here. The tty_lock never covered any race`**
⇒ **作业控制的锁语义是 tty 里最没定清的一块**（Part C §C2）。

## §A8 read / write 的骨架

### §A8.1 read

```
tty_read()  （drivers/tty/tty_io.c）
  ├─ tty_paranoia_check()                        :258 起
  ├─ ld = tty_ldisc_ref_wait(tty)                tty_ldisc.c:250
  │     └─ 拿不到 ⇒ 走 hung_up_tty_read()
  ├─ iterate_tty_read(ld, tty, file, iter)       tty_io.c:839
  │     └─ 循环调 ld->ops->read(tty, file, buf, nr, &cookie, offset)
  └─ tty_ldisc_deref(ld)
```

⚠️ **`ops->read` 的签名有 `void **cookie` 和 `unsigned long offset`**
（`include/linux/tty_ldisc.h:221-223`）
⇒ **这是为了支持"一次 read 分多次调 ldisc"** ——**`cookie` 是 ldisc 的续传状态。**

### §A8.2 write

```
tty_write()  （drivers/tty/tty_io.c）
  ├─ tty_write_lock(tty, ndelay)                 tty_io.c:942
  │     └─ 拿 tty->atomic_write_lock（保证一次 write 不被穿插）
  ├─ ld = tty_ldisc_ref_wait(tty)
  ├─ 分块从用户态拷到 tty->write_buf              （include/linux/tty.h:247）
  ├─ ld->ops->write(tty, file, buf, nr)          层 2
  │     └─ n_tty 内部再调 tty->ops->write()      层 1（由 ldisc 调，02 §1.0.D）
  └─ tty_ldisc_deref(ld) + tty_write_unlock()
```

⚠️ **`tty->atomic_write_lock` 保证的是"一次 `write()` 系统调用的数据不会与另一次交错"**
⇒ **这是 POSIX 对 tty 的要求。**
⚠️ **`TTY_NO_WRITE_SPLIT`（`include/linux/tty.h:321`）会影响分块策略。**

### §A8.3 poll

```c
/* drivers/tty/tty_io.c:2194-2210 —— 全文，很短，是"ldisc 转发"的最好例子 */
static __poll_t tty_poll(struct file *filp, poll_table *wait)
{
	struct tty_struct *tty = file_tty(filp);
	struct tty_ldisc *ld;
	__poll_t ret = 0;

	if (tty_paranoia_check(tty, file_inode(filp), "tty_poll"))
		return 0;

	ld = tty_ldisc_ref_wait(tty);
	if (!ld)
		return hung_up_tty_poll(filp, wait);
	if (ld->ops->poll)
		ret = ld->ops->poll(tty, filp, wait);
	tty_ldisc_deref(ld);
	return ret;
}
```

⇒ **"拿 ldisc 引用 → 转发 → 放引用"是 tty 核心里最常见的三段式**（§B2）。
**kerneldoc 的 Locking 一节明说**：*"locks called line discipline but ldisc poll method may be
re-entered freely by other callers."*

## §A9 ldisc 切换：`tty_set_ldisc()`（`tty_ldisc.c:598` 处导出）

**这是 tty 里最讲究的一个流程**（`drivers/tty/tty_ldisc.c:530-597`）：

| 步 | 代码 | 位置 |
|---|---|---|
| ① | **`tty_ldisc_lock(tty, 5 * HZ)`** ——**超时就 `goto err`** | `:530-532` |
| ② | **`!tty->ldisc` ⇒ `-EIO`** | `:534-537` |
| ③ | **已经是这个 ldisc ⇒ 直接 `goto out`** | `:540-541` |
| ④ | **`TTY_HUPPED` ⇒ `-EIO`** | `:543-547` |
| ⑤ | **`tty->ops->ldisc_ok(tty, disc)`** ——**驱动可以拒绝** | `:549-553` |
| ⑥ | **关旧的：`tty_ldisc_close(tty, old_ldisc)`** | `:558` |
| ⑦ | **换指针 + `tty_set_termios_ldisc(tty, disc)`** | `:561-562` |
| ⑧ | **开新的：`tty_ldisc_open(tty, new_ldisc)`** | `:564` |
| ⑨ | **失败 ⇒ `tty_ldisc_put(new)` + `tty_ldisc_restore(tty, old)`** | `:565-569` |
| ⑩ | **通知驱动：`tty->ops->set_ldisc(tty)`（在 `termios_rwsem` 读锁下）** | `:571-575` |
| ⑪ | **`tty_buffer_restart_work(tty->port)`** | `:592` |
| ⑫ | **`tty_ldisc_put(new_ldisc)` 放掉多余引用** | `:594` |

⚠️⚠️ **第 ⑥⑦⑧⑨ 四步是"先关旧的再开新的"，中间有一个窗口两者都不可用**
⇒ **这就是官方文档警告"`ldisc_ops.open/close` 和 `set_ldisc` 里 `tty_ldisc_ref()` 会失败"的原因**
（01 §5.4）。

**第 ⑫ 步的注释解释了引用计数的账**（`:577-583`，逐字）：
> *"At this point we hold a reference to the new ldisc and a reference to the old ldisc, or we hold
> two references to the old ldisc (if it was restored as part of error cleanup above). In either
> case, releasing a single reference from the old ldisc is correct."*

⇒ **`new_ldisc = old_ldisc;`（`:584`）这一行是为了让第 ⑫ 步统一处理两种情况** ——
**一个很紧凑的写法，也是最容易读错的一处。**

⚠️ **第 ⑩ 步只在"编号真的变了"时才通知驱动**（`:571` 的 `if (tty->ldisc->ops->num != old_ldisc->ops->num && …)`）
⇒ **失败回滚后不会误通知。**

## §A10 关闭与释放

### §A10.1 三层收尾

```
用户态 close()
  └─ tty_release()  （drivers/tty/tty_io.c）
       ├─ tty->ops->close(tty, filp)          层 1（多数驱动 ⇒ tty_port_close()）
       ├─ 最后一个 close？
       │    ├─ tty_save_termios(tty)          tty_io.c:1470（导出于 :1489）
       │    ├─ tty->ops->shutdown(tty)        或 port->ops->shutdown()
       │    └─ release_tty(tty, idx)
       │         ├─ tty_ldisc_release(tty)    关 ldisc
       │         ├─ 清 driver->ttys[idx]
       │         ├─ tty->ops->cleanup(tty)
       │         └─ tty_kref_put(tty)  ⇒ 归零则 free_tty_struct()
       └─ tty_free_file(filp)
```

### §A10.2 termios 会跨 open 继承

```c
/* drivers/tty/tty_io.c:1465-1470 —— kerneldoc 与函数头 */
/*
 * tty_save_termios() - save tty termios data in driver table
 * @tty: tty whose termios data to save
 *
 * Locking: Caller guarantees serialisation with tty_init_termios().
 */
void tty_save_termios(struct tty_struct *tty)
```

⇒ **close 时把 termios 存进 `driver->termios[idx]`，下次 open 由 `tty_init_termios()` 取回**
（`drivers/tty/tty_io.c:1269` 处导出）。
⚠️ **例外：`TTY_DRIVER_RESET_TERMIOS`（01 §3.3）会让 hangup 后重置。**
⚠️ **PTY 也是例外**（`tty_open()` 的 kerneldoc 原话）：
> *"The termios state of a pty is reset on the first open so that settings don't persist across reuse."*

---

# Part B：横切关注点

## §B1 锁：14 把，但只有 4 把要记住

**全景表在 02 §6.2。本节只讲"什么时候用哪把"**：

| 锁 | 什么时候拿 | 谁拿 |
|---|---|---|
| **`tty_mutex`（全局）** | **open 期间的查找与 `tty_init_dev`** | **`tty_open()` 及其子函数** |
| **`tty->legacy_mutex`（= `tty_lock()`）** | **几乎所有对单个 tty 的操作** | **核心到处拿** |
| **`tty->ldisc_sem`** | **用 ldisc 时拿读锁，切 ldisc 时拿写锁（带 5 秒超时）** | **`tty_ldisc_ref*` / `tty_ldisc_lock`** |
| **`tty->atomic_write_lock`** | **整个 `write()` 期间** | **`tty_write_lock()`（`tty_io.c:942`）** |

**`tty_open()` 的 kerneldoc 明确列了锁序**（逐字）：
> *"Locking: %tty_mutex protects tty, tty_lookup_driver() and tty_init_dev(). @tty->count should
> protect the rest. ->siglock protects ->signal/->sighand"*

⚠️ **最后一句还有一条注解**（`:2110`，逐字）：
> *"Note: the tty_unlock/lock cases without a ref are only safe due to %tty_mutex"*
⇒ **这说明 tty 的锁正确性部分依赖"全局大锁还在"这个前提** ——
**也是 Part C 要评价的地方。**

## §B2 "拿 ldisc 引用 → 转发 → 放引用"三段式

**这是 tty 核心里出现频率最高的模式**（§A8.3 的 `tty_poll` 是最短的例子）：

```c
	ld = tty_ldisc_ref_wait(tty);       /* 或 tty_ldisc_ref() */
	if (!ld)
		return <挂断路径>;
	if (ld->ops->xxx)
		ret = ld->ops->xxx(...);
	tty_ldisc_deref(ld);
```

| 变体 | 用在哪 |
|---|---|
| **`tty_ldisc_ref_wait()`**（`tty_ldisc.c:250`） | **read / write / poll ——可以等** |
| **`tty_ldisc_ref()`**（`tty_ldisc.c:271`） | **不能等的路径 ——可能返回 NULL** |

⚠️ **拿不到 ldisc 的两种情况**：
**① 正在切换（`TTY_LDISC_CHANGING`）；② tty 已挂断。**
⇒ **核心的处理是"走 `hung_up_tty_*` 那一族桩函数"**（例如 `hung_up_tty_poll`，§A8.3）。

## §B3 引用计数五层

| 层 | 计数 | 归零时 |
|---|---|---|
| **① 驱动模块** | **`try_module_get(driver->owner)`**（`tty_io.c:1405`） | **`tty_init_dev` 失败或 tty 释放时 `module_put`** |
| **② `tty_driver`** | **`driver->kref`** | **`tty_driver_kref_put()`** ⇒ 释放 driver |
| **③ `tty_port`** | **`port->kref`** | **`tty_port_put()`** ⇒ **调 `port->ops->destruct()`，释放驱动私有结构**（01 §4.4） |
| **④ `tty_struct`** | **`tty->kref`** | **`tty_kref_put()`（`tty_io.c:1567`）** ⇒ `free_tty_struct()` |
| **⑤ ldisc（两个！）** | **注册表那份 + tty 内那份** | 02 §1.0.B |
| **⑥ `tty->count`** | **打开次数** | **最后一个 close 触发 shutdown** |
| **⑦ `port->count`** | **port 的打开次数** | **`tty_port_close_start()` 递减** |

⇒ **一次完整的 open/close 会动到 7 个不同的计数** ——**这就是 tty 难写驱动的根源。**

⚠️ **`tty->count` 与 `port->count` 是两个数**：
**`tty_open()` 的 kerneldoc 专门解释了为什么不能用 inode 计数**（逐字）：
> *"tty_open() and tty_release() keep up the tty count that contains the number of opens done on a
> tty. We cannot use the inode-count, as different inodes might point to the same tty."*

**核心还有一个自检函数 `check_tty_count()`**（`drivers/tty/tty_io.c:272`）
⇒ **它会在计数不一致时打日志**（07 §4）。

## §B4 hangup：会换掉 `f_op`

| API | 位置 | 同步/异步 |
|---|---|---|
| `tty_hangup(tty)` | `drivers/tty/tty_io.c:688` | **异步**（排 `tty->hangup_work`） |
| `tty_vhangup(tty)` | `drivers/tty/tty_io.c:703` | **同步** |
| `tty_hung_up_p(filp)` | `drivers/tty/tty_io.c:748` | **查询** |

⚠️ **hangup 的核心动作是"把每个 `file` 的 `f_op` 换成 `hung_up_tty_fops`"**：
⇒ **之后所有系统调用都落到 `hung_up_tty_*` 那一族桩函数上** ——
**`tty_io.c:452` 的 `hung_up_tty_fasync` 是其中一个。**
⇒ **这解释了 `tty_open()` 里那行 `filp->f_op = &tty_fops;`（`:2166`）** ——
**重试前必须换回来**（§A4.2）。

**`tty->tty_files` 链表（`include/linux/tty.h:242`，由 `files_lock` 保护）就是为此存在的**
⇒ **hangup 要遍历所有打开这个 tty 的 `file`。**

**三个相关状态位**：**`TTY_HUPPED`（18）· `TTY_HUPPING`（19）· `TTY_IO_ERROR`（1）**（02 §5.2）。

## §B5 流控：四条独立的路

| 路 | 方向 | 状态放哪 | 触发者 |
|---|---|---|---|
| **① `throttle`/`unthrottle`** | **接收** | **`TTY_THROTTLED` 位** | **ldisc 发现 buffer 快满** |
| **② `stop`/`start`** | **发送** | **`tty->flow.stopped`** | **`^S`/`^Q`** |
| **③ `hw_stopped`** | **发送** | **`tty->hw_stopped`**（`include/linux/tty.h:230`） | **硬件流控（CTS 掉了）** |
| **④ `tco_stopped`** | **发送** | **`tty->flow.tco_stopped`** | **`TCOOFF`/`TCOON` ioctl** |

⇒ **发送方向有三个独立的"暂停"开关**（②③④），**任一个置位都会停止发送。**
⚠️ **这是"为什么明明没有 `^S` 却发不出去"的常见原因**（07 篇）。

**`tty->flow_change`（`include/linux/tty.h:232`）是一个防抖标记** ——
**`tty_set_flow_change()`（`drivers/tty/tty.h:52`）用它避免节流状态在切换过程中被误改。**

**配套的两个值与两个函数**：
**`TTY_THROTTLE_SAFE = 1` / `TTY_UNTHROTTLE_SAFE = 2`（`drivers/tty/tty.h:44-45`）**；
**`tty_throttle_safe()`（`drivers/tty/tty_ioctl.c:137`）· `tty_unthrottle_safe()`（`:168`）** ——
**两者都声明在 `include/linux/tty.h:419` 附近，是公开 API。**

**它们的语义**（`drivers/tty/tty_ioctl.c:129` 的注释，逐字）：
> *"tty_throttle_safe will only attempt throttle if tty->flow_change is …"*
⇒ **只有在"我刚刚宣布过要节流"的窗口内才真的节流** ——
**防止 ldisc 与核心互相覆盖对方的决定**（`drivers/tty/tty_ioctl.c:143` / `:174` 的两处比较）。

## §B6 可观测性

| 出口 | 有没有 | 说明 |
|---|---|---|
| **`/proc/tty/drivers`** | **有** | **所有已注册驱动**（来自 `tty_drivers` 链表） |
| **`/proc/tty/driver/<name>`** | **驱动实现了 `ops->proc_show` 才有** | 01 §2.5 |
| **`/sys/class/tty/`** | **有** | **`tty_class`（`drivers/tty/tty_io.c:3145` / `:3514`）** |
| **`tty_debug_hangup()`** | **有条件编译的调试打印** | **`tty_open` 里用了两处**（`:2142` / `:2151`） |
| **tracepoint** | **0** | **tty 核心没有 tracepoint** |
| **debugfs** | **0** | — |

**详见 07 篇。**

---

# Part C：评价与设计债

## §C1 源码自陈设计债：10 处（本项目最多）

**完整清单在 01 §8.4。按性质分三类**：

| 类 | 数量 | 位置 |
|---|---|---|
| **锁/顺序语义没定清** | **3** | `tty_jobctrl.c:341` · `tty_ioctl.c:353` · `tty_ioctl.c:540` |
| **行为不确定（需要查 POSIX）** | **3** | `n_tty.c:1348`（`XXX are EOL_CHAR and EOL2_CHAR echoed?!?`）· `n_tty.c:1358` · `tty_ioctl.c:239` |
| **实现待完善** | **4** | `tty_io.c:1026` · `:2267`（`FIXME: does not honour flow control ??`）· `:2813` · `:2995` |

⚠️⚠️ **`tty_io.c:2267` 的 `FIXME: does not honour flow control ??` 值得单独指出** ——
**它出现在 tty 的核心路径附近，而且是一个带问号的疑问**
⇒ **说明这块行为连维护者都不确定。**

## §C2 ⚠️ 三处"锁语义未定"的具体内容

| 位置 | 逐字内容 | 意味着什么 |
|---|---|---|
| `drivers/tty/tty_jobctrl.c:341` | `FIXME: Review locking here. The tty_lock never covered any race` … | **作业控制（控制终端/进程组）的竞争没有被 `tty_lock` 覆盖** |
| `drivers/tty/tty_ioctl.c:353` | `FIXME: we need to decide on some locking/ordering semantics` … | **termios 设置的顺序语义未定** |
| `drivers/tty/tty_ioctl.c:540` | `FIXME: Arguably if tmp_termios == tty->termios AND the` … | **"termios 没变时是否还要通知驱动"未定** |

⇒ **加上 `tty_open()` kerneldoc 里那句"only safe due to %tty_mutex"（§B1）**
⇒ **tty 的锁正确性有一部分是"靠一把全局大锁兜着"** ——
**这是它历史包袱最重的地方**（08 §3）。

## §C3 ⚠️ 四类需要警惕的行为

| # | 现象 | 机理 | 位置 |
|---|---|---|---|
| **1** | **驱动不设 `tty->port` ⇒ 注册失败并打一条很直白的 WARN** | **核心显式挡住** | `drivers/tty/tty_io.c:1422-1424` |
| **2** | **`open()` 可能在内核里重试多轮** | **两处 `goto retry_open`** | `tty_io.c:2136` / `:2167` |
| **3** | **ldisc 切换有一个"两者都不可用"的窗口** | **先 close 旧的再 open 新的** | `tty_ldisc.c:558-569` |
| **4** | **发送方向有三个独立的暂停开关** | **`flow.stopped` / `hw_stopped` / `flow.tco_stopped`** | §B5 |

⚠️ **第 3 类的直接后果**：**在 `ldisc_ops.open/close` 或 `tty_operations.set_ldisc` 里
调 `tty_ldisc_ref()` 一定失败**（01 §5.4）——**官方文档专门警告过。**

## §C4 五个可以合理质疑的设计选择

| # | 设计 | 质疑点 | 位置 |
|---|---|---|---|
| **1** | **一个 tty 会话牵涉 14 把锁、7 个引用计数** | **复杂度极高，新驱动很难写对** | 02 §6.2 / §B3 |
| **2** | **为 ldisc 专门写了一把 430 行的信号量** | **说明标准锁原语解决不了这个问题**；也说明问题本身可能过度复杂 | `drivers/tty/tty_ldsem.c` |
| **3** | **hangup 靠换 `f_op` 实现** | **一个很"聪明"但很难跟踪的机制** ——**要在 open 重试路径里手工换回来**（`tty_io.c:2166`） | §B4 |
| **4** | **`tty_operations` 有 37 个回调，但没有任何"必需"标注** | **对比 watchdog 的 `/* mandatory operations */`** ⇒ **只能靠读实现推断**（01 §2.8） | `include/linux/tty_driver.h:357-403` |
| **5** | **接收有 buffer、发送没有** | **不对称，但合理**（接收是中断驱动的、发送是系统调用驱动的） | 02 §3 |

**一处值得称赞的地方**：
**`tty_set_ldisc()` 的错误回滚写得非常严谨**（§A9 第 ⑨⑫ 步）——
**它用一行 `new_ldisc = old_ldisc;` 把两种错误状态统一成一个 put，并在注释里解释了引用计数的账。**

## §C5 整体评价

| 维度 | 评价 |
|---|---|
| **代码量** | **核心 12004 行 + 头文件 1794 行** ——**本项目最大的一套**；**不是一两天能读完** |
| **抽象层数** | **两层 + 两个半层**（02 §1.0.G） |
| ⚠️ **可读性** | **两极**：**kerneldoc 极其详尽**（`tty_open` 有 22 行注释）；⚠️ **但锁的交互只能靠读代码，没有一张总图** |
| **文档** | **10 篇 886 行，而且大部分正文是 `kernel-doc` 指令** ⇒ **"读文档"实际等于"读源码注释"** |
| **可观测性** | **中**：**`/proc/tty/` + `/sys/class/tty/` 有，但 0 tracepoint / 0 debugfs** |
| ⚠️ **设计债** | ⚠️ **10 处自陈，其中 3 处直接说"锁语义没定清"** ——**本项目最多** |

**本子系统最特殊的一点**：
**它是"三方各自演进了三十年"的产物**：
**用户态 ABI（termios / ioctl，来自 1970 年代的 UNIX）·
行规程（可插拔的字符加工层）· 驱动（从 UART 到 PTY 到蓝牙）。**
⇒ **tty 的复杂度不是设计出来的，是历史累积的。**

⇒ **读 tty 的正确姿势**：
**① 先记住四个对象的寿命差异（02 §1.0.A）；
② 再记住"from above / from below"这个方向标（02 §1.0.E）；
③ 然后按需读单条路径，不要试图一次理解全部锁。**
