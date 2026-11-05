---
title: API 参考
---

# cdev 01：代码地图与对外 API

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇覆盖的是**字符设备层**：`fs/char_dev.c` + `include/linux/cdev.h`
> + 设备号空间（`kdev_t.h`）+ 设备节点的产生（class / devtmpfs）+ misc 设备。
>
> 本篇 API 表统一三列 **API / 说明 / 备注**；签名只写**函数名 + 形参名**，不带类型；
> 位置只到**文件**，每节开头统一交代头文件和实现文件。

---

## §0 代码地图

### §0.1 核心只有一个文件

| 文件 | 行数 | 职责 | 本套的证据等级 |
|---|---|---|---|
| `fs/char_dev.c` | **685** | **全部核心逻辑** —— 设备号注册表、`cdev` 生命周期、`chrdev_open()` | 一等（全文逐行） |
| `include/linux/cdev.h` | **39** | `struct cdev` + 9 个 API 声明 | 一等（全文） |
| `include/linux/kdev_t.h` | 83 | `dev_t` 的 `MAJOR`/`MINOR`/`MKDEV` | 一等 |
| `drivers/base/map.c` | **154** | **`kobj_map`** —— "设备号 → kobject"的查找表 | 一等（全文） |

**685 + 39 + 83 + 154 = 961 行，这就是字符设备层的全部核心。**
⇒ 对比 irq 的 15044 行、remoteproc 的 5068 行 —— 它是本系列里**最小的子系统**。

⚠️ **但它的"边界"比核心大得多** —— 这才是它真正难的地方（§0.2）。

### §0.2 六个必须一起理解的边界

| 边界 | 文件 | 行数 | 它负责什么 |
|---|---|---|---|
| **VFS** | `include/linux/fs.h`（3746 行，只看相关部分） | —— | `struct file_operations` · `struct inode` · `struct file` · `def_chr_fops` |
| **sysfs / class** | `include/linux/device.h` · `include/linux/device/class.h` | —— | `class_create()` / `device_create()` —— **它们才是产生 `/dev` 节点的起点** |
| **devtmpfs** | `drivers/base/devtmpfs.c` | **491** | **内核自己在 `/dev` 里建节点** |
| **uevent / udev** | `lib/kobject_uevent.c` | —— | 通知用户态；udev 在内核树外 |
| **misc 设备** | `drivers/char/misc.c` + `include/linux/miscdevice.h` | **293 + 111** | **"我只要一个设备节点"的捷径** |
| **设备号分配表** | `Documentation/admin-guide/devices.txt` | **3086** | 静态主设备号的官方登记 |

**本套的定位**：核心（§0.1）讲到逐行，六个边界讲到"它在什么时候被调用、
它读写哪个字段"—— 但**不深入它们自己的实现**（99 §2.2）。

### §0.3 导出符号只有 12 个

| 符号 | 行号 |
|---|---|
| `register_chrdev_region` | `fs/char_dev.c:674` |
| `unregister_chrdev_region` | `fs/char_dev.c:675` |
| `alloc_chrdev_region` | `fs/char_dev.c:676` |
| `cdev_init` | `fs/char_dev.c:677` |
| `cdev_alloc` | `fs/char_dev.c:678` |
| `cdev_del` | `fs/char_dev.c:679` |
| `cdev_add` | `fs/char_dev.c:680` |
| `cdev_set_parent` | `fs/char_dev.c:681` |
| `cdev_device_add` | `fs/char_dev.c:682` |
| `cdev_device_del` | `fs/char_dev.c:683` |
| `__register_chrdev` | `fs/char_dev.c:684` |
| `__unregister_chrdev` | `fs/char_dev.c:685` |

```bash
grep -c EXPORT_SYMBOL fs/char_dev.c      # → 12
```

⚠️ **三个"看起来该导出但没导出"的**：

| 符号 | 为什么不导出 |
|---|---|
| `cdev_put()` | 声明在 `include/linux/cdev.h:27`，**实现在 `fs/char_dev.c:361` 但没有 `EXPORT_SYMBOL`** ⇒ **模块用不了** |
| `cd_forget()` | 只给 VFS 用（`fs/inode.c` 销毁 inode 时调） |
| `chrdev_show()` | 只给 `/proc/devices` 用 |

`cdev_put()` 这一条值得记住：**驱动拿不到 `cdev` 的引用计数控制权** ——
生命周期只能通过 `cdev_device_add()` 那套（§3）或 `struct device` 来管。

### §0.4 没有专属的 Kconfig

`fs/char_dev.c` **无条件编译进内核**（`fs/Makefile` 里没有条件）。
相关的可选项都在边界上：

| 选项 | 作用 |
|---|---|
| `CONFIG_DEVTMPFS` | 内核自己建 `/dev` 节点（`drivers/base/Kconfig`） |
| `CONFIG_DEVTMPFS_MOUNT` | 内核在 initramfs 之前自动挂上 devtmpfs |
| `CONFIG_DEVTMPFS_SAFE` | 用 `nosuid,noexec` 挂 devtmpfs |
| `CONFIG_UNIX98_PTYS` 等 | 具体的字符设备驱动 |
| `CONFIG_PROC_FS` | `/proc/devices` 的内容（`fs/char_dev.c:47` 的 `#ifdef`） |

⇒ **"设备节点从哪来"这个问题的答案取决于 `DEVTMPFS` 开没开**（§6）。

---

## §1 设备号：`dev_t` 与它的三个宏

> 头文件：`include/linux/kdev_t.h`

### §1.1 32 位里的 12 + 20

| 宏 | 定义 | 位置 |
|---|---|---|
| `MINORBITS` | **20** | `include/linux/kdev_t.h:7` |
| `MINORMASK` | `(1U << 20) - 1` = 0xFFFFF | `include/linux/kdev_t.h:8` |
| `MAJOR(dev)` | `dev >> 20` | `include/linux/kdev_t.h:10` |
| `MINOR(dev)` | `dev & MINORMASK` | `include/linux/kdev_t.h:11` |
| `MKDEV(ma, mi)` | `(ma << 20) \| mi` | `include/linux/kdev_t.h:12` |
| `print_dev_t(buffer, dev)` | 打成 `"%u:%u\n"` | `include/linux/kdev_t.h:14-15` |
| `format_dev_t(buffer, dev)` | 打成 `"%u:%u"` 并返回 buffer | `include/linux/kdev_t.h:17-21` |

⇒ **主设备号 12 位（0–4095）、次设备号 20 位（0–1048575）**。

### §1.1.1 ⚠️ uapi 头文件里的宏是**旧的 8+8 位**

`include/uapi/linux/kdev_t.h`（全文 14 行）里有另一套同名宏：

```c
#ifndef __KERNEL__
#define MAJOR(dev)	((dev)>>8)
#define MINOR(dev)	((dev) & 0xff)
#define MKDEV(ma,mi)	((ma)<<8 | (mi))
#endif
```

| 头文件 | 位宽 | 给谁 |
|---|---|---|
| `include/linux/kdev_t.h:10-12` | **12 + 20** | 内核内部 |
| ⚠️`include/uapi/linux/kdev_t.h:10-12` | **8 + 8** | **用户态**（`#ifndef __KERNEL__` 保护） |

那个文件的注释解释了原因：
"Some programs want their definitions of MAJOR and MINOR and MKDEV
from the kernel sources. These must be the externally visible ones."
—— 也就是**为了兼容那些历史上就这么用的老程序**。

⚠️ **用户态程序绝对不要用这套宏** —— 主设备号 ≥ 256 时会算错。
⇒ **用 glibc 的 `major()` / `minor()` / `makedev()`**（`<sys/sysmacros.h>`）。

`include/linux/kdev_t.h:5` 会 include 这个 uapi 头，
但内核编译时 `__KERNEL__` 已定义 ⇒ 那三个宏不会生效，随后被 12+20 的版本定义。

### §1.2 ⚠️ 三个"老式"辅助（兼容旧文件系统）

| 函数 | 说明 | 位置 |
|---|---|---|
| `old_valid_dev(dev)` | 判断 major < 256 且 minor < 256 | `include/linux/kdev_t.h:24` |
| `old_encode_dev(dev)` | 编码成 16 位 | `include/linux/kdev_t.h:29` |
| `old_decode_dev(val)` | 反操作 | `include/linux/kdev_t.h` |

它们给 ext2 之类的老式 inode 格式用 —— **驱动不该碰**。

### §1.3 主设备号的三个区间

| 区间 | 宏 | 值 | 用途 |
|---|---|---|---|
| **1 – 233** | —— | —— | **静态分配**（登记在 `Documentation/admin-guide/devices.txt`） |
| **234 – 254** | `CHRDEV_MAJOR_DYN_END` = 234 | `include/linux/fs.h:2898` | **动态分配的首选区**（从 254 往下找） |
| **384 – 511** | `CHRDEV_MAJOR_DYN_EXT_START` = 511 · `_END` = 384 | `include/linux/fs.h:2900-2901` | **动态分配的扩展区**（首选区满了才用） |
| 上界 | `CHRDEV_MAJOR_MAX` = **512** | `include/linux/fs.h:2896` | 超了直接 `-EINVAL` |

分配算法在 `find_dynamic_major()`（`fs/char_dev.c:65`）：
先从 254 往下扫首选区（`fs/char_dev.c:70-73`），
满了再扫 511→384 的扩展区（`fs/char_dev.c:75-83`），
都满了返回 `-EBUSY`（`fs/char_dev.c:85`）。

⚠️ **注意 `CHRDEV_MAJOR_HASH_SIZE` 是 255**（`fs/char_dev.c:32`），
而主设备号上界是 512 ⇒ **注册表是一个 255 桶的哈希表**，
`major_to_index()` 就是 `major % 255`（`fs/char_dev.c:44`）。
⇒ 主设备号 300 和 45 会落在同一个桶里，所以每个桶是一条链表（§2 的 02 篇展开）。

---

## §2 设备号区间的注册与释放

> 头文件：`include/linux/fs.h:2904-2921` · 实现：`fs/char_dev.c`

| API | 说明 | 备注 |
|---|---|---|
| `alloc_chrdev_region(dev, baseminor, count, name)` | **动态分配一个主设备号**，通过 `dev` 输出第一个 `dev_t` | **首选**。`fs/char_dev.c:236`；内部就是 `__register_chrdev_region(0, …)` |
| `register_chrdev_region(from, count, name)` | 申请一段**指定**的设备号 | `from` 必须含主设备号；**能跨多个主设备号**（内部按主设备号切段循环，`fs/char_dev.c:206-215`） |
| `unregister_chrdev_region(from, count)` | 释放 | 同样按主设备号切段循环（`fs/char_dev.c:317-322`） |
| `chrdev_show(f, offset)` | `/proc/devices` 的一行 | 不导出（§0.3） |

**`register_chrdev_region()` 的跨主设备号能力**：
它把 `[from, from+count)` 按主设备号边界切开，
每段调一次 `__register_chrdev_region()`；任一段失败就**回滚已成功的那些**
（`fs/char_dev.c:216-223` 的 `fail:` 分支）。

⚠️ **`name` 有 64 字节上限**（`char name[64]`，`fs/char_dev.c:34-41` 的结构体），
用 `strlcpy` 截断（`fs/char_dev.c:153`）。超长**静默截断**，
表现是 `/proc/devices` 里的名字被切掉。

### §2.1 两条 `-EINVAL` 的边界检查

| 检查 | 位置 | 配套日志 |
|---|---|---|
| `major >= CHRDEV_MAJOR_MAX`（512） | `fs/char_dev.c:103-107` | 有：`CHRDEV "%s" major requested (%u) is greater than the maximum (%u)` |
| `minorct > MINORMASK + 1 - baseminor` | `fs/char_dev.c:109-113` | 有：`CHRDEV "%s" minor range requested (%u-%u) is out of range …` |

动态分配区满了也有日志：
`CHRDEV "%s" dynamic allocation region is full`（`fs/char_dev.c:125`）。

⇒ **这三条是本子系统唯一的三条 `pr_err`**（07 §2 会给全量普查）。

### §2.2 ⚠️ `-EBUSY`：区间冲突

`__register_chrdev_region()` 遍历同一个哈希桶的链表，
用四个条件判断"要申请的区间和已有的有没有重叠"（`fs/char_dev.c:133-147`），
重叠就 `goto out` 返回 `-EBUSY`（`fs/char_dev.c:132` 设值、`fs/char_dev.c:147` 跳转）。

⚠️ **这条 `-EBUSY` 没有日志**。
⇒ `alloc_chrdev_region()` 返回 `-EBUSY` 且 dmesg 干净 ⇒ 你要的区间被别人占了；
用 `cat /proc/devices` 看是谁（07 §4）。

---

## §3 `struct cdev` 的生命周期

> 头文件：`include/linux/cdev.h` · 实现：`fs/char_dev.c`

### §3.1 四个基础 API

| API | 说明 | 备注 |
|---|---|---|
| `cdev_init(cdev, fops)` | 初始化一个**你自己分配的** `cdev`（通常内嵌在你的结构里） | `fs/char_dev.c:651`；它会 `memset` 整个结构再 `kobject_init` |
| `cdev_alloc()` | 核心替你 `kzalloc` 一个 `cdev` | `fs/char_dev.c:633`；用它的话释放走 `cdev_del()` + kobject 引用归零 |
| `cdev_add(p, dev, count)` | **让这个 cdev 对 `[dev, dev+count)` 生效** | `fs/char_dev.c:479` |
| `cdev_del(p)` | 反操作 | `fs/char_dev.c:594` |

⚠️ **`cdev_init()` 和 `cdev_alloc()` 二选一，不能都用**：
前者假设内存是你的，后者假设内存是核心的。混用会导致重复初始化 kobject。

### §3.2 `cdev_add()` 做的三件事

| 步骤 | 位置 |
|---|---|
| ① 记下 `p->dev` 和 `p->count` | `fs/char_dev.c:482-483` |
| ② `WARN_ON(dev == WHITEOUT_DEV)` ⇒ `-EBUSY` | `fs/char_dev.c:485-487` |
| ③ `kobj_map(cdev_map, dev, count, NULL, exact_match, exact_lock, p)` | `fs/char_dev.c:489-490` |
| ④ `kobject_get(p->kobj.parent)` | `fs/char_dev.c:494` |

**③ 是关键**：它把"这段设备号 → 这个 `cdev` 的 kobject"登记进全局的
`cdev_map`（`fs/char_dev.c:28`）。`chrdev_open()` 就是查它（§4）。

⚠️ **④ 那句 `kobject_get(p->kobj.parent)` 有一个隐含前提**：
`parent` 必须已经设好且已初始化。
⇒ 这就是 `cdev_set_parent()` 必须在 `cdev_add()` **之前**调的原因（§3.3）。

`WHITEOUT_DEV` 是 overlayfs 用的一个特殊 `dev_t`（0），
所以 ② 实际上是在挡"传了 0 进来"。

### §3.3 `cdev_set_parent()`：把生命周期挂到别的 kobject 上

| API | 说明 | 备注 |
|---|---|---|
| `cdev_set_parent(p, kobj)` | 设 `p->kobj.parent = kobj` | `fs/char_dev.c:508`；带 `WARN_ON(!kobj->state_initialized)`（`fs/char_dev.c:510`） |

**它解决的问题**：用户态可能在你 `cdev_del()` 之后**还持有那个 fd**。
把 parent 设成你的 `struct device` 的 kobject，
就保证"只要还有 fd 打开，`struct device` 就不会被释放"。

⇒ 函数注释原文（`fs/char_dev.c:501-507` 那段）说得很清楚：
"sets a parent kobject which will be referenced appropriately
so the parent is not freed before the cdev. **This should be called before cdev_add.**"

### §3.4 推荐用法：`cdev_device_add()` / `cdev_device_del()`

| API | 说明 | 备注 |
|---|---|---|
| `cdev_device_add(cdev, dev)` | **一次做完 `cdev_set_parent` + `cdev_add` + `device_add`** | `fs/char_dev.c:537` |
| `cdev_device_del(cdev, dev)` | 反操作 | `fs/char_dev.c:571` |

实现只有 15 行（`fs/char_dev.c:537-552`）：

```c
	if (dev->devt) {
		cdev_set_parent(cdev, &dev->kobj);
		rc = cdev_add(cdev, dev->devt, 1);
		if (rc)
			return rc;
	}
	rc = device_add(dev);
	if (rc && dev->devt)
		cdev_del(cdev);
	return rc;
```

**四条设计要点全在函数注释里（`fs/char_dev.c:516-536`）**：

| 要点 | 原文要旨 |
|---|---|
| 用 `dev->devt` 作设备号 | 所以 `struct device` 必须**先初始化好** |
| `devt` 为 0 时**退化成纯 `device_add()`** | ⇒ 不会建字符设备，也不报错 |
| 适用场景 | "whenever the struct cdev and the struct device are **members of the same structure** whose lifetime is managed by the struct device" |
| ⚠️ 最重要的一条 | **"Callers must assume that userspace was able to open the cdev and can call cdev fops callbacks at any time, even if this function fails."** |

⇒ 最后那条的实际含义：**`cdev_add()` 一成功，你的 fops 就可能被调用** ——
即使后面的 `device_add()` 失败了。
⇒ **所有 fops 回调必须能应对"半初始化"状态**（05 §1 会给写法）。

### §3.5 `cdev_put()`：声明了但不导出

| API | 说明 | 备注 |
|---|---|---|
| `cdev_put(p)` | 放一次 kobject 引用 + `module_put` | `fs/char_dev.c:361`；**没有 `EXPORT_SYMBOL`**（§0.3） |

---

## §4 老式一步到位：`register_chrdev()`

> 头文件：`include/linux/fs.h:2913-2922`（inline）· 实现：`fs/char_dev.c:268`

| API | 说明 | 备注 |
|---|---|---|
| `register_chrdev(major, name, fops)` | **一步搞定"申请设备号 + 建 cdev + cdev_add"** | inline 包装：`__register_chrdev(major, 0, 256, name, fops)`（`include/linux/fs.h:2916`） |
| `unregister_chrdev(major, name)` | 反操作 | `__unregister_chrdev(major, 0, 256, name)`（`include/linux/fs.h:2921`） |
| `__register_chrdev(major, baseminor, count, name, fops)` | 带 minor 区间的版本 | `fs/char_dev.c:268` |
| `__unregister_chrdev(major, baseminor, count, name)` | 反操作 | `fs/char_dev.c:335` |

**返回值语义很特别**（`fs/char_dev.c:294`）：
```c
	return major ? 0 : cd->major;
```
| 你传的 `major` | 返回 |
|---|---|
| > 0 | **0**（成功） |
| **0**（动态） | **分配到的主设备号**（正数！） |
| 失败 | 负错误码 |

⚠️ **所以 `if (register_chrdev(...))` 这种写法在动态分配时是错的** ——
成功也会返回非 0。

### §4.1 ⚠️ 三个"别用它"的理由

| 理由 | 说明 |
|---|---|
| **它一次占掉 256 个 minor** | `include/linux/fs.h:2916` 写死了 `256`。你只要 1 个也占 256 个 |
| **它不建设备节点** | `/dev` 下什么都不会出现 —— 还得自己 `class_create` + `device_create`（§6） |
| **它的 `cdev` 你拿不到** | 存在 `char_device_struct.cdev` 里（`fs/char_dev.c:40`），而那个字段的注释就是 **`/* will die */`** |

**`/* will die */` 这条注释（`fs/char_dev.c:40`）是上游对这套老 API 的态度** ——
新代码应该用 `alloc_chrdev_region()` + `cdev_device_add()`（§2、§3.4）。

唯一还合理的用途：**你确实要占一整个主设备号的 256 个 minor**
（如 tty、misc 这类"设备号分配器"—— `drivers/char/misc.c:280` 就是这么用的，§7）。

---

## §5 `file_operations`：你真正要写的东西

> 头文件：`include/linux/fs.h`

### §5.1 字符设备最常用的回调

| 回调 | 说明 | 备注 |
|---|---|---|
| `open(inode, file)` | 打开 | `inode->i_rdev` 是设备号；**它是在 `chrdev_open()` 里被调的**（§5.3） |
| `release(inode, file)` | 关闭**最后一个** fd 时调 | ⚠️ **不是每次 `close()` 都调** —— `dup()` 出来的 fd 共享同一个 `struct file` |
| `read(file, buf, count, ppos)` / `write(...)` | 读写 | 现代驱动多用 `read_iter`/`write_iter` |
| `unlocked_ioctl(file, cmd, arg)` | ioctl | **必须有对应的 `compat_ioctl`**（32 位用户态跑在 64 位内核上时） |
| `compat_ioctl` | 32 位兼容 | 参数布局相同的话直接填 `compat_ptr_ioctl` |
| `poll(file, wait)` | select/poll/epoll | —— |
| `mmap(file, vma)` | 内存映射 | —— |
| `llseek(file, off, whence)` | 不支持定位就填 `no_llseek`；想显式表示"忽略"填 `noop_llseek` | —— |
| `owner` | **`THIS_MODULE`** | 忘了填 ⇒ 模块可以在设备被打开时被卸载 ⇒ **崩溃** |

### §5.2 `def_chr_fops`：所有字符设备的公共入口

```c
const struct file_operations def_chr_fops = {
	.open = chrdev_open,
	.llseek = noop_llseek,
};
```

| 元素 | 位置 |
|---|---|
| 定义 | `fs/char_dev.c:452-455` |
| 声明 | `include/linux/fs.h:2893` |
| 上面那段"Dummy default file-operations"注释 | `fs/char_dev.c:447-451` |

**它只有一个 `open`** —— VFS 打开任何字符设备节点时先落到这里，
`chrdev_open()` 再**把 `filp->f_op` 换成你的 fops**（§5.3）。

### §5.3 `chrdev_open()`：整个子系统的枢纽

`fs/char_dev.c:373`，全文 50 行。

| 步骤 | 动作 | 位置 |
|---|---|---|
| ① | `spin_lock(&cdev_lock)`；读 `inode->i_cdev` | `fs/char_dev.c:380-381` |
| ② | 缓存未命中 ⇒ 解锁，`kobj_lookup(cdev_map, inode->i_rdev, &idx)` | `fs/char_dev.c:385-386` |
| ③ | 查不到 ⇒ **`-ENXIO`** | `fs/char_dev.c:387-388` |
| ④ | `container_of(kobj, struct cdev, kobj)` | `fs/char_dev.c:389` |
| ⑤ | 重新加锁后**再查一次** `i_cdev`（可能被别人抢先填了） | `fs/char_dev.c:391-393` |
| ⑥ | 填缓存：`inode->i_cdev = p`；`list_add(&inode->i_devices, &p->list)` | `fs/char_dev.c:395-396` |
| ⑦ | 拿引用失败 ⇒ `-ENXIO` | `fs/char_dev.c:398-399` |
| ⑧ | 先置 `ret = -ENXIO`（`fs/char_dev.c:407`），再 `fops = fops_get(p->ops)`（`fs/char_dev.c:408`）；失败 ⇒ `goto out_cdev_put` | `fs/char_dev.c:407-410` |
| ⑨ | **`replace_fops(filp, fops)`** —— 把 `f_op` 换成你的 | `fs/char_dev.c:412` |
| ⑩ | 有 `open` 就调 `filp->f_op->open(inode, filp)` | `fs/char_dev.c:413-416` |
| 失败路径 | `out_cdev_put: cdev_put(p)` | `fs/char_dev.c:421-423` |

**三个必须记住的点**：

| 点 | 说明 |
|---|---|
| **`-ENXIO` 是"没有这个设备"的统一答案** | ③⑦⑧ 三处都返回它 ⇒ `open()` 拿到 `ENXIO` 就是"设备号没人认领 / cdev 正在消失 / 模块正在卸载" |
| **`inode->i_cdev` 是一个缓存** | 第一次 open 才查 `cdev_map`，之后直接用（`fs/char_dev.c:381`）。⇒ 这就是 `cd_forget()`（§0.3）存在的原因 |
| **`fops_get()` 就是 `try_module_get`** | ⇒ **`fops->owner` 没填 `THIS_MODULE` 的话这道保护就是空的**（§5.1） |

---

## §6 设备节点从哪来

> 头文件：`include/linux/device.h` · `include/linux/device/class.h`
> 实现：`drivers/base/core.c` · `drivers/base/class.c` · `drivers/base/devtmpfs.c`

### §6.1 `cdev_add()` 不会让 `/dev` 里出现任何东西

这是本子系统**最大的认知落差**：
`alloc_chrdev_region()` + `cdev_add()` 之后，
`/proc/devices` 里有了、`cat /dev/xxx` **没有节点**。

节点的产生要走另一条完全独立的链：

```
   class_create(owner, name)            ← 建一个 /sys/class/<name>/
        │
   device_create(cls, parent, devt, drvdata, "fmt", …)
        │  · 建 struct device，设 dev->devt
        │  · device_add() → sysfs 目录 + uevent
        ▼
   ┌────────────────────┬──────────────────────────┐
   │ devtmpfs（内核内）  │  udev（用户态）            │
   │ 直接在 /dev 建节点  │  收 uevent 后 mknod       │
   └────────────────────┴──────────────────────────┘
```

| API | 说明 | 位置 |
|---|---|---|
| `class_create(owner, name)` | 宏，展开成 `__class_create(owner, name, &__key)` | `include/linux/device/class.h:273-277` |
| `class_destroy(cls)` | —— | `include/linux/device/class.h:255` |
| `device_create(cls, parent, devt, drvdata, fmt, …)` | **建 `struct device` 并设 `devt`** | `include/linux/device.h:1026` |
| `device_create_with_groups(...)` | 同上 + 一组 sysfs 属性 | `include/linux/device.h:1029` |
| `device_destroy(cls, devt)` | 按设备号销毁 | `include/linux/device.h:1032` |

### §6.2 devtmpfs：内核自己建节点

| 函数 | 位置 | 什么时候调 |
|---|---|---|
| `devtmpfs_create_node(dev)` | `drivers/base/devtmpfs.c:121` | `device_add()` 里（`dev->devt` 非 0 时） |
| `devtmpfs_delete_node(dev)` | `drivers/base/devtmpfs.c:148` | `device_del()` 里 |
| `devtmpfs_mount()` | `drivers/base/devtmpfs.c:367`（声明在 `include/linux/device.h:1087`） | `CONFIG_DEVTMPFS_MOUNT` 时由 init 代码调 |
| `devtmpfs_init()` | `drivers/base/devtmpfs.c:456` | 启动早期 |
| 实际建节点的 worker | `drivers/base/devtmpfs.c:211` 的 `handle_create()` | —— |
| 实际删节点的 | `drivers/base/devtmpfs.c:316` 的 `handle_remove()` | —— |
| 二者的分发入口 | `drivers/base/devtmpfs.c:387` 的 `handle()` | —— |

**`CONFIG_DEVTMPFS` 没开的话，`/dev` 节点只能靠 udev 或手工 `mknod`**（§0.4）。

⚠️`include/linux/device.h:1089` 有一个空实现（`static inline int devtmpfs_mount(void) { return 0; }`）
⇒ 不开这个选项时调用点不用条件编译。

### §6.3 三条路的对照

| 路 | 谁建节点 | 需要什么 | 节点名从哪来 |
|---|---|---|---|
| **devtmpfs** | 内核 | `CONFIG_DEVTMPFS` | `dev_name(dev)`（`device_create` 的 `fmt`） |
| **udev** | 用户态 | udev 在跑 + 收到 uevent | udev 规则可以改名 |
| **手工 `mknod`** | 你 | 知道主/次设备号 | 你起 |

⇒ **嵌入式系统上如果既没开 devtmpfs 也没有 udev，就必须在 rootfs 里预先 `mknod`** ——
这就是老式 busybox 系统里 `/dev` 是静态目录的原因。

---

## §7 misc 设备：只要一个节点时的捷径

> 头文件：`include/linux/miscdevice.h`（111 行）· 实现：`drivers/char/misc.c`（293 行）

| API | 说明 | 备注 |
|---|---|---|
| `misc_register(misc)` | **一次搞定设备号 + cdev + class + 节点** | `drivers/char/misc.c:175`（导出于 `:231`） |
| `misc_deregister(misc)` | 反操作 | `drivers/char/misc.c:241`（导出于 `:255`） |

`struct miscdevice`（`include/linux/miscdevice.h:79`）你要填的：

| 字段 | 说明 |
|---|---|
| `minor` | 填 `MISC_DYNAMIC_MINOR`（= **255**，`include/linux/miscdevice.h:74`）让核心分配 |
| `name` | **`/dev` 下的节点名** |
| `fops` | 你的 `file_operations` |
| `mode`（可选） | 节点权限 |
| `parent`（可选） | sysfs 的父设备 |

### §7.1 它是怎么做到的

| 事实 | 位置 |
|---|---|
| misc 自己在 init 时占了一整个主设备号 | `drivers/char/misc.c:280` 的 `register_chrdev(MISC_MAJOR, "misc", &misc_fops)` |
| `MISC_MAJOR` = **10** | `include/linux/miscdevice.h` |
| 它的 fops 只有一个 `open` | `drivers/char/misc.c:151` 的 `misc_fops` |
| `misc_open()` 按 minor 找到你的 `miscdevice` 并 `replace_fops` | `drivers/char/misc.c:100` |
| 找不到就 `request_module("char-major-%d-%d", …)` 试着自动加载 | `drivers/char/misc.c:119` |
| `misc_register()` 里建节点 | `drivers/char/misc.c:205` 算出 `MKDEV(MISC_MAJOR, minor)`，然后 `device_create` |
| 失败时把 minor 还回去 | `drivers/char/misc.c:216` |
| `misc_deregister()` 销毁节点 | `drivers/char/misc.c:250` |

**misc 本质上是"字符设备层之上的第二级 minor 分配器"** ——
和 §5.3 的 `chrdev_open()` + `replace_fops` 是同一个套路，套了两层。

### §7.2 什么时候用 misc、什么时候不用

| 用 misc | 自己 `alloc_chrdev_region()` |
|---|---|
| 只要 **1 个**节点 | 要多个 minor（如每个通道一个节点） |
| 不在乎主设备号是 10 | 需要固定的主设备号 |
| 不需要自己的 class | 需要自己的 `/sys/class/<name>/` |
| 想少写 30 行代码 | 需要 `cdev_device_add()` 那种生命周期绑定（§3.4） |

`MODULE_ALIAS("char-major-10-<minor>")` 宏（`include/linux/miscdevice.h:109`）
配合 `drivers/char/misc.c:119` 的 `request_module()` 实现**按需自动加载**。

---

## §8 错误码语义

### §8.1 全量普查

`fs/char_dev.c` 里 `-E*` 一共出现 **11** 次：

| 错误码 | 次数 | 语义 | 位置 |
|---|---|---|---|
| `-ENXIO` | **4** | **"没有这个设备"** | **全部在 `chrdev_open()` 里**：`fs/char_dev.c:388`（`kobj_lookup` 查不到）· `:399` 和 `:401`（`cdev_get()` 失败）· `:407`（`fops_get()` 之前的预设值），见 §5.3 |
| `-EBUSY` | **3** | 动态区满 / 设备号区间冲突 / `WHITEOUT_DEV` | `fs/char_dev.c:85` · `:132` · `:487` |
| `-EINVAL` | 2 | 主设备号或 minor 区间越界 | `fs/char_dev.c:107` · `:113` |
| `-ENOMEM` | 2 | `kzalloc` 失败 / `__register_chrdev()` 的初值 | `fs/char_dev.c:118` · `:274` |

```bash
grep -on '\-E[A-Z_]*' fs/char_dev.c
```

**只有 11 个错误码点，而且语义完全不重叠** ——
这是本系列里错误码信息量最高的子系统（对比 irq 的 `-EINVAL` 占 58%）。
四个 `-ENXIO` 全部集中在同一个函数里，所以 `open()` 拿到 `ENXIO`
**不用猜是哪一层返回的** —— 一定是 `chrdev_open()`。

### §8.2 高频错误的第一嫌疑

| 拿到 | 第一嫌疑 | 怎么确认 |
|---|---|---|
| `open()` → `ENXIO` | **设备号没人 `cdev_add()`**，或 cdev 正在消失，或模块正在卸载 | `cat /proc/devices` 看主设备号在不在；注意"在 `/proc/devices` 里"只说明**设备号注册了**，不代表 `cdev_add()` 过（§6.1、07 §4） |
| `alloc_chrdev_region()` → `-EBUSY` | ① 动态区（234-254 + 384-511）满了 —— **有日志**；② 你要的区间被占 —— **无日志** | `cat /proc/devices` |
| `register_chrdev_region()` → `-EBUSY` | 指定的区间被占 | 同上 |
| `-EINVAL` | 主设备号 ≥ 512，或 minor 区间越界 | **两条都有 `pr_err`** |
| `cdev_add()` → `-EBUSY` | 传了 `dev == 0`（`WHITEOUT_DEV`）—— 带 `WARN_ON` | 看栈回溯 |
| `open()` → `ENODEV` | **不是本子系统返回的** —— 是你的 `fops->open` 自己返回的 | —— |
| `/dev` 下没有节点 | 忘了 `class_create` + `device_create`（或 misc） | §6.1 |
| 模块能在设备被打开时卸载 | `fops.owner` 没填 `THIS_MODULE` | §5.1 |

---

**下一篇**：02 篇讲 `struct cdev` / `char_device_struct` / `kobj_map` / `inode` 之间的关系。
