---
title: 使用指南
---

# cdev 05：各角色的代码骨架

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ **篇首免责声明**：本篇所有代码骨架**都没有编译过、没有运行过**。
> 它们是从本树真实驱动里提炼的结构，**不是可以直接 `make` 的代码**。
> 抄之前请对照 §0.2 点名的那几个真实驱动。
>
> ⚠️ 本篇出现的每个内核符号都已在 6.1.158 树上 grep 确认存在，
> 但**参数个数和类型请以头文件为准**（`include/linux/cdev.h` / `fs.h` / `device.h`）。

---

## §0 本子系统有哪些角色

### §0.1 角色清单

| § | 角色 | 你要写的东西 |
|---|---|---|
| §1 | **现代标准写法**（一个设备、自己的 class） | `alloc_chrdev_region` + `cdev_init` + `cdev_device_add` |
| §2 | **misc 捷径**（只要一个节点） | 一个 `struct miscdevice` |
| §3 | **多 minor**（一个驱动 N 个节点） | §1 的写法 × N |
| §4 | **老式写法**（维护老代码时读得懂） | `register_chrdev` |
| §5 | **用户态使用者** | `open` / `ioctl` / `mmap` 的注意点 |

**没有"字符设备总线驱动"这个角色** —— 字符设备层不是总线（02 §2.2）。

### §0.2 每个骨架的真实原型（抄之前先读这几个文件）

| 骨架 | 参考的真实驱动 | 行数 | 为什么选它 |
|---|---|---|---|
| §1 现代标准 | **`drivers/i2c/i2c-dev.c`** | 782 | **完整示范了内嵌 cdev + `device.release` + `put_device` 的全套** |
| §3 多 minor | `drivers/counter/counter-core.c` | 282 | 短，`alloc_chrdev_region(…, COUNTER_DEV_MAX, …)` 一次占一片 |
| §2 misc | `drivers/char/misc.c` 自己 | 293 | 看它怎么用，比看使用者更直接 |
| §4 老式 | `drivers/char/misc.c:280` | —— | 它是老式 API 唯一还合理的用法（04 §3.1） |

### §0.3 ⚠️ 没有 devres 版本

```bash
grep -rn 'devm_.*cdev\|cdev.*devm_' include/linux/cdev.h include/linux/device.h   # → 无输出
```

**`cdev` 没有任何 `devm_*` 包装**。理由（08 §5 会用类比讲）：

| 理由 | 说明 |
|---|---|
| devres 在 **driver detach 时**释放 | 但 `cdev` 必须活到**最后一个 fd 关闭**（03 §A8.1） |
| ⇒ 用 devres 会**过早释放** | 用户态还持着 fd，你的私有数据已经 `kfree` 了 |

⇒ **正确的生命周期锚点是 `struct device` 的 `release` 回调**，不是 devres（§1.3）。

---

## §1 现代标准写法

### §1.1 骨架

```c
// SPDX-License-Identifier: GPL-2.0
#include <linux/cdev.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#define MYDEV_MAX	4		/* 最多支持几个实例 */

static dev_t		mydev_devt;	/* 我们那一段设备号的起点 */
static struct class	*mydev_class;

struct mydev {
	struct cdev	cdev;		/* 内嵌，不是指针 */
	struct device	dev;		/* 内嵌，生命周期的锚 */
	void __iomem	*base;
	struct mutex	lock;
	/* … 你的私有数据 … */
};

/* ================= 生命周期的锚点 ================= */
static void mydev_release(struct device *dev)
{
	struct mydev *my = container_of(dev, struct mydev, dev);

	/* 这里才是唯一安全的释放点：
	 *  所有 fd 都关了、cdev 的 kobject 引用也归零了，
	 *  才会走到这里（03 §A8.1、§B3）。
	 */
	kfree(my);
}

/* ================= file_operations ================= */
static int mydev_open(struct inode *inode, struct file *filp)
{
	/* 从 inode 拿回自己：cdev 是内嵌的，所以能 container_of */
	struct mydev *my = container_of(inode->i_cdev, struct mydev, cdev);

	/* 抓一个 device 引用，保证 release 里的数据活到 close */
	get_device(&my->dev);
	filp->private_data = my;

	return 0;
}

static int mydev_release_file(struct inode *inode, struct file *filp)
{
	struct mydev *my = filp->private_data;

	put_device(&my->dev);
	return 0;
}

static ssize_t mydev_read(struct file *filp, char __user *buf,
			  size_t count, loff_t *ppos)
{
	struct mydev *my = filp->private_data;
	/* … */
	return 0;
}

static long mydev_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
	struct mydev *my = filp->private_data;

	switch (cmd) {
	case MYDEV_IOC_FOO:
		/* … */
		return 0;
	default:
		return -ENOTTY;		/* 不是 -EINVAL！见 §1.4 */
	}
}

static const struct file_operations mydev_fops = {
	.owner		= THIS_MODULE,		/* 必填（§1.4） */
	.open		= mydev_open,
	.release	= mydev_release_file,
	.read		= mydev_read,
	.unlocked_ioctl	= mydev_ioctl,
	.compat_ioctl	= compat_ptr_ioctl,	/* 32 位兼容（§1.4） */
	.llseek		= no_llseek,
};

/* ================= probe ================= */
static int mydev_probe(struct platform_device *pdev)
{
	struct mydev *my;
	int id, ret;

	id = ida_alloc_max(&mydev_ida, MYDEV_MAX - 1, GFP_KERNEL);
	if (id < 0)
		return id;

	my = kzalloc(sizeof(*my), GFP_KERNEL);	/* 不用 devm_kzalloc！§1.3 */
	if (!my) {
		ret = -ENOMEM;
		goto err_ida;
	}
	mutex_init(&my->lock);

	my->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(my->base)) {
		ret = PTR_ERR(my->base);
		goto err_free;
	}

	/* 顺序：cdev_init 先，owner 后（cdev_init 会 memset！03 §A3） */
	cdev_init(&my->cdev, &mydev_fops);
	my->cdev.owner = THIS_MODULE;

	device_initialize(&my->dev);
	my->dev.devt    = MKDEV(MAJOR(mydev_devt), id);
	my->dev.class   = mydev_class;
	my->dev.parent  = &pdev->dev;
	my->dev.release = mydev_release;		/* 关键 */
	dev_set_name(&my->dev, "mydev%d", id);

	/* 一步做完 cdev_set_parent + cdev_add + device_add */
	ret = cdev_device_add(&my->cdev, &my->dev);
	if (ret) {
		/* ⚠️ 即使这里失败，用户态也可能已经打开过 cdev（03 §A4.1）
		 * ⇒ 不能 kfree(my)，只能 put_device 让 release 决定
		 */
		put_device(&my->dev);
		goto err_ida;			/* 注意：不 goto err_free */
	}

	platform_set_drvdata(pdev, my);
	return 0;

err_free:
	kfree(my);
err_ida:
	ida_free(&mydev_ida, id);
	return ret;
}

static int mydev_remove(struct platform_device *pdev)
{
	struct mydev *my = platform_get_drvdata(pdev);
	int id = MINOR(my->dev.devt);

	cdev_device_del(&my->cdev, &my->dev);	/* 先关外层门，再关内层（03 §A8.2） */
	put_device(&my->dev);			/* 放掉 device_initialize 那一次 */
	ida_free(&mydev_ida, id);
	return 0;
}

/* ================= 模块 init / exit ================= */
static int __init mydev_init(void)
{
	int ret;

	ret = alloc_chrdev_region(&mydev_devt, 0, MYDEV_MAX, "mydev");
	if (ret)
		return ret;

	mydev_class = class_create(THIS_MODULE, "mydev");
	if (IS_ERR(mydev_class)) {
		ret = PTR_ERR(mydev_class);
		goto err_region;
	}

	ret = platform_driver_register(&mydev_driver);
	if (ret)
		goto err_class;

	return 0;

err_class:
	class_destroy(mydev_class);
err_region:
	unregister_chrdev_region(mydev_devt, MYDEV_MAX);
	return ret;
}

static void __exit mydev_exit(void)
{
	platform_driver_unregister(&mydev_driver);
	class_destroy(mydev_class);
	unregister_chrdev_region(mydev_devt, MYDEV_MAX);
}
module_init(mydev_init);
module_exit(mydev_exit);
MODULE_LICENSE("GPL v2");
```

### §1.2 真实原型的对照

| 骨架里的元素 | `drivers/i2c/i2c-dev.c` 的位置 |
|---|---|
| `struct cdev cdev;` 内嵌 | `drivers/i2c/i2c-dev.c:46` |
| `fops` 定义 | `drivers/i2c/i2c-dev.c:626` |
| `.owner = THIS_MODULE` | `drivers/i2c/i2c-dev.c:627` |
| `cdev_init(&…->cdev, &…_fops)` | `drivers/i2c/i2c-dev.c:663` |
| **紧接着**设 `cdev.owner` | `drivers/i2c/i2c-dev.c:664` |
| `device_initialize()` | `drivers/i2c/i2c-dev.c:666` |
| `dev.devt = MKDEV(…)` | `drivers/i2c/i2c-dev.c:667` |
| `dev.class` | `drivers/i2c/i2c-dev.c:668` |
| `dev.parent` | `drivers/i2c/i2c-dev.c:669` |
| `dev.release` | `drivers/i2c/i2c-dev.c:670` |
| `dev_set_name()` | `drivers/i2c/i2c-dev.c:672` |
| `cdev_device_add()` | `drivers/i2c/i2c-dev.c:676` |
| 拆除：`cdev_device_del()` + `put_device()` | `drivers/i2c/i2c-dev.c:94-95` |
| `unregister_chrdev_region()` | `drivers/i2c/i2c-dev.c:762` · `:773` |

**`drivers/i2c/i2c-dev.c:663-664` 这两行相邻不是巧合** ——
`cdev_init()` 内部有 `memset(cdev, 0, sizeof *cdev)`（`fs/char_dev.c:653`），
所以 `owner` 必须在它**之后**设（03 §A3）。

### §1.3 为什么不能用 `devm_kzalloc()`

| 事实 | 依据 |
|---|---|
| devres 在 **driver detach** 时释放 | 设备模型的规则 |
| 但你的 fops **在 `cdev_device_del()` 之后还会被调** | `fs/char_dev.c:590-592` 的注释（03 §A8.1） |
| ⇒ devm 分配的私有数据会被**过早释放** | ⇒ use-after-free |

**正确的锚点是 `device.release`**：
只有当 ① 所有 fd 关了（每次 `__fput()` 一次 `cdev_put()`，`fs/file_table.c:323`）、
② `cdev` 的 kobject 归零、③ `cdev` 的 release 里 `kobject_put(parent)`
（`fs/char_dev.c:607`）之后，`device.release` 才会被调用。

⇒ **`cdev_set_parent()` 那一步（`cdev_device_add()` 内部做的，`fs/char_dev.c:542`）
就是把这条链接起来的关键**（01 §3.3）。

⚠️ **例外**：`devm_ioremap_resource()` 之类的**硬件资源**用 devm 是对的 ——
它们在 detach 时确实该释放（那时硬件已经不能碰了）。
⇒ **判据：这块内存会不会被 fops 回调访问？** 会 ⇒ 不能 devm。

### §1.4 `file_operations` 的五条纪律

| 纪律 | 为什么 |
|---|---|
| **`.owner = THIS_MODULE`** | `chrdev_open()` 用 `fops_get()`（`fs/char_dev.c:408`）保护模块；和 `cdev.owner` 是**两个不同的计数**（03 §B3） |
| **ioctl 不认识的 cmd 返回 `-ENOTTY`** | POSIX 语义："不是这个设备的 ioctl"。⚠️ 返回 `-EINVAL` 会让 glibc 之类的探测逻辑误判 |
| **有 `unlocked_ioctl` 就要有 `compat_ioctl`** | 32 位用户态 + 64 位内核时走后者。参数布局相同就填 `compat_ptr_ioctl`（`include/linux/fs.h`） |
| **不支持定位就填 `.llseek = no_llseek`** | **`no_llseek` 就是 `NULL`**（`include/linux/fs.h:3305` 的 `#define no_llseek NULL`）—— 写它只是为了让读代码的人知道你是故意的 |
| **`release` 里 `put_device()` 要和 `open` 里的 `get_device()` 配平** | `release` 只在**最后一个引用**消失时调（03 §A7） |

两个和"定位"有关的辅助（都在 `include/linux/fs.h`）：

| 函数 | 用途 |
|---|---|
| `nonseekable_open()`（`include/linux/fs.h:3316`） | 在 `open` 里调，清掉 `FMODE_LSEEK` 等 |
| `stream_open()`（`include/linux/fs.h:3317`） | 同上 + 允许**并发无锁 read/write**（流式设备用） |

### §1.5 从 `inode` 拿回你的结构

```c
	struct mydev *my = container_of(inode->i_cdev, struct mydev, cdev);
```

**前提：`cdev` 必须是内嵌的（不是指针）** ——
这就是 §1.1 骨架里 `struct cdev cdev;` 而不是 `struct cdev *cdev;` 的原因。

⚠️ **`inode->i_cdev` 在你的 `open` 被调时一定非空** ——
`chrdev_open()` 在 `fs/char_dev.c:395` 填好它，
到 `fs/char_dev.c:414` 才调你的 `open`。

**另一条路**：`container_of(inode->i_cdev, …)` 拿到 `cdev` 之后再
`container_of(cdev, struct mydev, cdev)` —— 但既然 `cdev` 内嵌，一步就够。

**多 minor 时**（§3）还要用 `MINOR(inode->i_rdev)` 区分是哪个通道。

---

## §2 misc 捷径

### §2.1 骨架

```c
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/module.h>

static int mymisc_open(struct inode *inode, struct file *filp)
{
	/* ⚠️ 这里 container_of(inode->i_cdev, …) 拿到的是 misc 自己的 cdev，
	 * 不是你的！要拿自己的数据用 filp->private_data（misc_open 已经设好了）
	 * 或者用全局变量。
	 */
	return 0;
}

static const struct file_operations mymisc_fops = {
	.owner		= THIS_MODULE,
	.open		= mymisc_open,
	.unlocked_ioctl	= mymisc_ioctl,
	.compat_ioctl	= compat_ptr_ioctl,
	.llseek		= no_llseek,
};

static struct miscdevice mymisc = {
	.minor	= MISC_DYNAMIC_MINOR,	/* = 255，让核心分配 */
	.name	= "mymisc",		/* 就是 /dev/mymisc */
	.fops	= &mymisc_fops,
	.mode	= 0666,			/* 不填的话 devtmpfs 给 0600（04 §1.3） */
};

static int __init mymisc_init(void)
{
	return misc_register(&mymisc);	/* 一步搞定四件事 */
}

static void __exit mymisc_exit(void)
{
	misc_deregister(&mymisc);
}
module_init(mymisc_init);
module_exit(mymisc_exit);
MODULE_LICENSE("GPL v2");
MODULE_ALIAS("char-major-10-" /* 你的 minor，动态时用不了这个 */);
```

### §2.2 ⚠️ 四个必须知道的限制

| 限制 | 说明 |
|---|---|
| **`container_of(inode->i_cdev, …)` 拿不到你的数据** | misc 的 `cdev` 属于 `drivers/char/misc.c`，不是你的（04 §3.2 的两次 `replace_fops`） |
| **拿不到 `cdev`，做不了生命周期绑定** | ⇒ 你的私有数据必须是**全局的或模块级的**，不能是"每个实例一份且要活到 fd 关闭" |
| **主设备号固定 10、全系统只有 256 个 minor** | ⚠️ 多个模块都用 misc 时可能耗尽 |
| **动态 minor 时 `MODULE_ALIAS` 用不上** | 自动加载（04 §6）需要固定 minor |

⇒ **misc 只适合"单例、无实例私有数据、不在乎设备号"的设备** ——
一旦你需要"每个硬件实例一个节点"，就该用 §1 或 §3。

### §2.3 `misc_register()` 替你做的四件事

| 件 | 对应 §1 骨架的哪一步 |
|---|---|
| ① 分配 minor（`drivers/char/misc.c:179`） | `ida_alloc_max()` |
| ② 挂进 `misc_list`（供 `misc_open` 查） | `cdev_init()` + `cdev_add()` |
| ③ `device_create(misc_class, …)`（`drivers/char/misc.c:205` 附近） | `device_initialize` + `dev_set_name` + `device_add` |
| ④ 失败时还回 minor（`drivers/char/misc.c:216`） | `goto err_ida` |

它**没有**做的：`class_create`（用现成的 `misc_class`）、`alloc_chrdev_region`
（misc 模块 init 时已经占了主设备号 10，`drivers/char/misc.c:280`）。

---

## §3 多 minor：一个驱动 N 个节点

### §3.1 两种做法

| 做法 | 说明 | 什么时候用 |
|---|---|---|
| **A：一个 `cdev` 覆盖 N 个 minor** | `cdev_add(&cdev, devt, N)`，在 `open` 里用 `MINOR(inode->i_rdev)` 区分 | 所有通道共享同一份状态、同一个 `struct device` |
| **B：N 个 `cdev`，每个 1 个 minor** | 每个实例一套 `cdev` + `device`（= §1 骨架跑 N 次） | **推荐** —— 每个通道独立的生命周期和 sysfs 目录 |

**`drivers/counter/counter-core.c` 用的是 B**：
`alloc_chrdev_region(&counter_devt, 0, COUNTER_DEV_MAX, …)`
（`drivers/counter/counter-core.c:259`）一次占一片，
每个 counter 实例自己 `device_initialize()`（`drivers/counter/counter-core.c:116`）
+ `cdev_device_add()`（`drivers/counter/counter-core.c:167`），
拆除 `cdev_device_del()`（`drivers/counter/counter-core.c:182`）。

### §3.2 做法 A 的骨架片段

```c
	/* init：一次占 N 个 */
	alloc_chrdev_region(&mydev_devt, 0, MYDEV_MAX, "mydev");

	/* 一个 cdev 覆盖全部 N 个 minor */
	cdev_init(&g_cdev, &mydev_fops);
	g_cdev.owner = THIS_MODULE;
	cdev_add(&g_cdev, mydev_devt, MYDEV_MAX);

	/* 每个通道一个 device（只为了节点名和 sysfs） */
	for (i = 0; i < MYDEV_MAX; i++)
		device_create(mydev_class, NULL,
			      MKDEV(MAJOR(mydev_devt), i), NULL, "mydev%d", i);

/* open 里区分 */
static int mydev_open(struct inode *inode, struct file *filp)
{
	unsigned int idx = MINOR(inode->i_rdev) - MINOR(mydev_devt);

	if (idx >= MYDEV_MAX)
		return -ENXIO;			/* 和核心的语义保持一致 */
	filp->private_data = &channels[idx];
	return 0;
}
```

⚠️ **做法 A 的三个代价**：

| 代价 | 说明 |
|---|---|
| 只有**一个** `cdev` 的引用计数 | ⇒ 做不到"某个通道的数据活到它自己的 fd 关闭" |
| `device_create()` 建的 `device` **没有和 cdev 绑定** | ⇒ `cdev_set_parent()` 只能指一个 |
| 通道索引要自己算 | `MINOR(i_rdev) - MINOR(base)` —— ⚠️ 别忘了减基址 |

⇒ **除非通道之间真的共享状态，用做法 B。**

---

## §4 ⚠️ 老式写法：只为读懂老代码

```c
static int major;

static int __init old_init(void)
{
	major = register_chrdev(0, "olddev", &old_fops);
	if (major < 0)			/* 注意：不是 if (major) ！ */
		return major;

	/* ⚠️ 到这里 /dev 下还是什么都没有 —— 还要自己建 class + device */
	old_class = class_create(THIS_MODULE, "olddev");
	device_create(old_class, NULL, MKDEV(major, 0), NULL, "olddev");
	return 0;
}

static void __exit old_exit(void)
{
	device_destroy(old_class, MKDEV(major, 0));
	class_destroy(old_class);
	unregister_chrdev(major, "olddev");
}
```

**四条注意**：

| 注意 | 依据 |
|---|---|
| **`major = 0` 时成功返回的是分配到的主设备号（正数）** | `fs/char_dev.c:294` 的 `return major ? 0 : cd->major`（01 §4） |
| **它一次占掉 256 个 minor** | `include/linux/fs.h:2916` 写死 |
| **它不建 `/dev` 节点** | 01 §4.1 |
| **拿不到那个 `cdev`** | 存在 `char_device_struct.cdev` 里，注释是 `/* will die */`（`fs/char_dev.c:40`，03 §C2） |

**唯一还合理的用途**：你就是要占一整个主设备号当"第二级分配器"——
`drivers/char/misc.c:280` 就是这么用的（04 §3.1）。

---

## §5 用户态使用者

### §5.1 基本用法

```c
	int fd = open("/dev/mydev0", O_RDWR);
	if (fd < 0) {
		/* ENXIO = 设备号没人 cdev_add（01 §8.2）
		 * ENOENT = 节点不存在（缺 class_create/device_create）
		 * EACCES = devtmpfs 默认 0600（04 §1.3）
		 */
		perror("open");
		return 1;
	}

	ioctl(fd, MYDEV_IOC_FOO, &arg);
	read(fd, buf, sizeof(buf));
	close(fd);
```

### §5.2 三个容易踩的用户态坑

| 坑 | 说明 |
|---|---|
| **`close()` 不保证立刻调到驱动的 `release`** | `dup()` 出来的 fd、`fork()` 继承的 fd 共享同一个 `struct file` ⇒ **最后一个关闭时才调**（03 §A7） |
| **`ioctl` 返回 `ENOTTY` 是"这个 cmd 不认识"** | 不是"不是终端"—— 这个错误码的名字是历史遗留（§1.4） |
| **`ENXIO` 和 `ENODEV` 要分清** | `ENXIO` 一定来自 `chrdev_open()`（01 §8.1）；`ENODEV` 是你的驱动自己返回的 |

### §5.3 查"这个节点对应哪个设备"

```sh
# 节点的主/次设备号
ls -l /dev/mydev0
# → crw------- 1 root root 243, 0 …
#      ▲                    ▲    ▲
#      c = 字符设备        major minor

# 反查到 sysfs（04 §5.2）
ls -l /sys/dev/char/243:0

# 主设备号被谁占了（04 §4）
grep 243 /proc/devices
```

⚠️ **没有办法从用户态确认 `cdev_add()` 做了没有** ——
`cdev_map` 没有出口（04 §7）。唯一的判据就是 `open()` 会不会返回 `ENXIO`。

---

## §6 常见错误速查

| 症状 | 最可能的原因 | 去哪一节 |
|---|---|---|
| `/dev` 下没有节点 | 忘了 `class_create()` + `device_create()`（或用 misc） | §1.1 · 01 §6.1 |
| 节点在，`open()` → `ENXIO` | **忘了 `cdev_add()` / `cdev_device_add()`** | §1.1 · 02 §1.0.A |
| `open()` → `EACCES`（普通用户） | devtmpfs 默认 **0600** | 04 §1.3 · §2.1（misc 的 `.mode`） |
| `alloc_chrdev_region()` → `-EBUSY` | ① 动态区满（**有日志**）② 你指定的区间被占（**无日志**） | 01 §8.2 |
| 模块卸载后崩溃 | `fops.owner` 或 `cdev.owner` 没填 `THIS_MODULE`（**两个都要填**） | §1.4 · 03 §B3 |
| 拔设备/remove 后崩溃（use-after-free） | 私有数据用了 `devm_kzalloc()`，或没设 `device.release` | §1.3 · 03 §A8.1 |
| `cdev.owner` 设了但被清掉 | 设在 `cdev_init()` **之前** —— 它会 `memset` | §1.2 · 03 §A3 |
| 32 位程序 ioctl 失败 | 缺 `.compat_ioctl` | §1.4 |
| 用户态探测逻辑异常 | ioctl 不认识的 cmd 返回了 `-EINVAL` 而不是 `-ENOTTY` | §1.4 |
| misc 设备里 `container_of(inode->i_cdev, …)` 拿到垃圾 | 那是 misc 自己的 cdev | §2.2 |
| 多 minor 时通道索引错 | 忘了减基址 `MINOR(mydev_devt)` | §3.2 |
| `register_chrdev()` 成功却被当成失败 | `major = 0` 时成功返回**正数** | §4 · 01 §4 |
| `/proc/devices` 里同一个 major 出现多次 | 正常 —— 每个 `char_device_struct` 一行 | 04 §4.3 |
| `/proc/devices` 里名字被截断 | `char name[64]` + `strlcpy` | 04 §4.3 |

---

## §7 本篇覆盖了哪些角色、漏了哪些

| 角色 | 本篇给了什么 | 没给什么 |
|---|---|---|
| 现代标准写法 | 完整骨架 + 五条 fops 纪律 + 生命周期的三层配平 | 具体的 ioctl 参数设计（属于 uapi 设计） |
| misc 捷径 | 骨架 + 四个限制 | —— |
| 多 minor | 两种做法的对照 + 做法 A 的三个代价 | 做法 B 的完整骨架（= §1 跑 N 次，不重复） |
| 老式写法 | 骨架 + 四条注意 | 不鼓励用，只为读懂老代码 |
| 用户态 | 三个坑 + 三条查询命令 | ioctl 的具体封装（各设备不同） |
| **块设备** | **完全不覆盖** —— 块设备走 `blkdev` 那一套，虽然设备号空间共享 | 99 §2.7 |

⚠️ 再强调一次篇首的免责声明：**本篇代码全部未编译未运行**。
每个骨架都点名了它提炼自哪个真实驱动（§0.2），落地时请以那个驱动为准。

---

**下一篇**：06 篇讲这个子系统的配置机制 —— **它没有 DT 绑定**，
配置全靠 Kconfig、命令行参数、静态设备号分配表和 udev 规则。
