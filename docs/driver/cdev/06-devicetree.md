---
title: 设备树
---

# cdev 06：配置机制（本子系统没有设备树绑定）

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ **本篇不讲设备树** —— 字符设备层**没有任何 DT 绑定**（§1.1 给判据）。
> 按 skill 的适配规则，本篇改讲它真正的配置机制：
> **Kconfig（编译期）· 命令行参数 · 静态设备号分配表 · class 回调 · udev 规则**。
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 按任务找章节

| 你想干的事 | 去哪一节 |
|---|---|
| 我想确认它真的没有 DT 绑定 | §1.1 |
| 我想知道有哪些配置点、谁消费 | §1.2 |
| 我要选编译选项 | §2 |
| 我要改内核命令行 | §3 |
| 我要申请一个**固定**主设备号 | §4 |
| 我要改节点名 / 权限 / 建子目录 | §5 |
| 我要写 udev 规则 | §6 |
| 给我一份完整例子 | §7 |
| 配好了但节点不对，怎么定位 | §8 |
| dmesg 里有一句报错，想知道是哪里配错了 | §9 |

---

## §1 三方分工与配置点总表

### §1.1 判据：它读的不是设备树

```bash
grep -rn 'of_property_read\|of_get_property\|of_parse_phandle\|device_node' \
     fs/char_dev.c include/linux/cdev.h drivers/base/map.c        # → 无输出
ls Documentation/devicetree/bindings/ | grep -i 'chrdev\|char_dev\|cdev'  # → 无输出
```

**两条命令都是空** ⇒ **字符设备层完全不知道设备树的存在**。

**为什么**：设备号是**软件资源**（一个 32 位整数空间），不是硬件属性。
它由**内核内部的分配器**（`chrdevs[]`，02 §1.0.D）管，和硬件描述无关。

⇒ 对比本系列其它子系统：

| 子系统 | 核心读几条 DT 属性 |
|---|---|
| remoteproc | 1 条（`firmware-name`） |
| irq | **0** 条（但**有** 113 个 DT 绑定，靠 `drivers/of/irq.c` 解析） |
| cdev | **0** 条，而且**根本没有 DT 绑定** |

### §1.2 五类配置点

```
   ┌── 编译期 ────────────────────────────────────────────────┐
   │  §2  Kconfig                                             │
   │      DEVTMPFS / DEVTMPFS_MOUNT / DEVTMPFS_SAFE / PROC_FS │
   └──────────────────────────────────────────────────────────┘

   ┌── 启动参数 ──────────────────────────────────────────────┐
   │  §3  devtmpfs.mount=0|1     （唯一的一个）                │
   └──────────────────────────────────────────────────────────┘

   ┌── 源码里写死 ────────────────────────────────────────────┐
   │  §4  主设备号（静态申请）+ Documentation/admin-guide/     │
   │      devices.txt 的登记                                   │
   │  §5  class->devnode / device_type->devnode 回调           │
   │      （决定节点名、权限、子目录）                          │
   └──────────────────────────────────────────────────────────┘

   ┌── 运行期（用户态）──────────────────────────────────────┐
   │  §6  udev 规则（改名 / 改权限 / 建符号链接）              │
   └──────────────────────────────────────────────────────────┘
```

| 配置点 | 消费者 | 消费代码 |
|---|---|---|
| `CONFIG_DEVTMPFS` | devtmpfs | 整个 `drivers/base/devtmpfs.c` |
| `CONFIG_DEVTMPFS_MOUNT` | 同上 | `drivers/base/devtmpfs.c:40` 的 `mount_dev` 初值 |
| `CONFIG_DEVTMPFS_SAFE` | 同上 | `drivers/base/devtmpfs.c:33` / `:35` 的 `DEVTMPFS_MFLAGS` |
| `CONFIG_PROC_FS` | `/proc/devices` | `fs/char_dev.c:47` 的 `#ifdef` |
| **`devtmpfs.mount=`** | 同上 | `drivers/base/devtmpfs.c:55-60` |
| **主设备号**（源码常量） | `register_chrdev_region()` | `fs/char_dev.c:200` |
| **`class->devnode`** | `device_get_devnode()` | `drivers/base/core.c:3987-3988` |
| **`device_type->devnode`** | 同上（**优先级更高**） | `drivers/base/core.c:3981-3982` |
| udev 规则 | udevd（内核树外） | —— |

**`fs/char_dev.c` 里唯一的"配置"就是调用者传进来的参数** ——
没有 Kconfig、没有命令行、没有 module_param（可重跑：
`grep -n '__setup(\|module_param' fs/char_dev.c` → 无输出）。

---

## §2 编译期：四个 Kconfig

| 选项 | 位置 | 默认 | 作用 | 不开的后果 |
|---|---|---|---|---|
| `DEVTMPFS` | `drivers/base/Kconfig:31` | **n** | 内核维护 `/dev` 节点 | 节点只能靠 udev 或手工 `mknod`（01 §6.3） |
| `DEVTMPFS_MOUNT` | `drivers/base/Kconfig:50` | n（`depends on DEVTMPFS`） | 内核自动把 devtmpfs 挂到 `/dev` | 要 initramfs 或 init 脚本自己挂 |
| `DEVTMPFS_SAFE` | `drivers/base/Kconfig:65` | n | 挂载时加 `MS_NOEXEC \| MS_NOSUID` | 安全性略低 |
| `PROC_FS` | `fs/Kconfig` | y | `/proc/devices` | 看不到设备号占用（04 §4） |

**`fs/char_dev.c` 本身无条件编译进内核**（`fs/Makefile` 里没有条件）——
所以"字符设备层"永远存在，只有它的**周边设施**是可选的。

### §2.1 三种组合的实际后果

| DEVTMPFS | DEVTMPFS_MOUNT | udev | `/dev` 节点从哪来 |
|---|---|---|---|
| y | y | 有 | **最常见**：内核建、udev 改属性（04 §2.2） |
| y | n | 有 | initramfs 挂上 devtmpfs 之后节点才出现 |
| y | y | 无 | 全靠内核；**权限一律 0600**（04 §1.3），普通用户访问不了 |
| n | —— | 有 | 全靠 udev `mknod` |
| n | —— | 无 | **rootfs 里必须预先 `mknod`**（老式 busybox 系统） |

**判断当前系统走哪条**：
```sh
mount | grep ' /dev '          # devtmpfs 挂上了吗
dmesg | grep devtmpfs          # 见 §8.2
pidof udevd systemd-udevd      # udev 在跑吗
```

---

## §3 命令行参数：只有一个

| 参数 | 位置 | 语义 |
|---|---|---|
| `devtmpfs.mount=0` / `=1` | `drivers/base/devtmpfs.c:60` | **覆盖 `CONFIG_DEVTMPFS_MOUNT`** |

| 实现 | 位置 |
|---|---|
| `mount_dev` 的初值来自 Kconfig | `drivers/base/devtmpfs.c:40` |
| 解析函数 `mount_param()` | `drivers/base/devtmpfs.c:55` |
| `__setup()` 注册 | `drivers/base/devtmpfs.c:60` |

Kconfig 的帮助文本明确说了这一点（`drivers/base/Kconfig:56` 那一带：
"The behavior can be overridden…"）。

```bash
grep -rn '__setup(' drivers/base/devtmpfs.c fs/char_dev.c drivers/char/misc.c
# → 只有 drivers/base/devtmpfs.c:60 一条
```

⚠️ **`devtmpfs.mount=1` 但没开 `CONFIG_DEVTMPFS`** ⇒ 参数无效（代码都没编进来）。

---

## §4 主设备号：静态 vs 动态

### §4.1 三个区间（01 §1.3 的配置视角）

| 区间 | 谁管 | 怎么用 |
|---|---|---|
| **1 – 233** | **`Documentation/admin-guide/devices.txt`**（3086 行的官方登记表） | 源码里写常量 + `register_chrdev_region()` |
| **234 – 254** | 内核动态分配（首选区） | `alloc_chrdev_region()` |
| **384 – 511** | 内核动态分配（扩展区） | 同上 |

| 常量 | 值 | 位置 |
|---|---|---|
| `CHRDEV_MAJOR_MAX` | 512 | `include/linux/fs.h:2896` |
| `CHRDEV_MAJOR_DYN_END` | 234 | `include/linux/fs.h:2898` |
| `CHRDEV_MAJOR_DYN_EXT_START` | 511 | `include/linux/fs.h:2900` |
| `CHRDEV_MAJOR_DYN_EXT_END` | 384 | `include/linux/fs.h:2901` |

### §4.2 `devices.txt` 的格式

```
   1 char	Memory devices
		  1 = /dev/mem		Physical memory access
		  3 = /dev/null		Null device
		  5 = /dev/zero		Null byte source
		 11 = /dev/kmsg		…

   2 char	Pseudo-TTY masters
		  0 = /dev/ptyp0	First PTY master
```

| 元素 | 位置 |
|---|---|
| 主设备号 1（内存设备）的登记 | `Documentation/admin-guide/devices.txt:5-18` |
| 主设备号 1（块设备，RAM disk） | `Documentation/admin-guide/devices.txt:20` |
| 主设备号 2（PTY master） | `Documentation/admin-guide/devices.txt:31` 起 |
| 主设备号 0（保留） | `Documentation/admin-guide/devices.txt:1-3` |

**格式是"`<major> char\|block <描述>`"后面缩进列出每个 minor**。
⇒ 同一个数字在 `char` 和 `block` 里是**两套独立的空间**
（`Documentation/admin-guide/devices.txt:5` 和 `:20` 都是主设备号 1）。

### §4.3 ⚠️ 什么时候该用静态主设备号

| 用静态 | 用动态 |
|---|---|
| **必须**：`/dev/null`、`/dev/tty` 这类**用户态写死了设备号**的 | **其余全部** |
| 需要在 rootfs 镜像里预先 `mknod`（没有 devtmpfs/udev） | 有 devtmpfs 或 udev |
| 老式 `MODULE_ALIAS("char-major-N-M")` 自动加载（04 §6） | —— |

⚠️ **新驱动几乎一定该用 `alloc_chrdev_region()`**：
静态号需要向上游申请登记，而且 1–233 区间早就很拥挤了。

**申请流程**（本套未实测，只记文档做法）：给 `Documentation/admin-guide/devices.txt`
提补丁并发到相应的邮件列表。⇒ 99 §5.1 记为"未验证"。

---

## §5 节点名、权限、子目录：`devnode` 回调

### §5.1 两个回调，优先级不同

`device_get_devnode()`（`drivers/base/core.c:3972`）依次问两个回调：

| 顺序 | 回调 | 声明 | 能改什么 |
|---|---|---|---|
| ① | `dev->type->devnode(dev, mode, uid, gid)` | `include/linux/device.h:93` | **名字 + mode + uid + gid** |
| ② | `dev->class->devnode(dev, mode)` | `include/linux/device/class.h:63` | **名字 + mode**（**没有 uid/gid**） |

| 调用点 | 位置 |
|---|---|
| ① | `drivers/base/core.c:3981-3982` |
| ② | `drivers/base/core.c:3987-3988` |

**`device_type->devnode` 优先**（先被调），
而且**只有它能设 uid/gid**（`include/linux/device.h:93` 的四参数签名）。

### §5.2 它是唯一能改节点名和默认权限的内核内机制

```c
/* class 版：改名 + 改权限 */
static char *mydev_devnode(struct device *dev, umode_t *mode)
{
	if (mode)
		*mode = 0666;			/* 覆盖 devtmpfs 的 0600 */

	/* 返回带斜杠的名字 ⇒ devtmpfs 会建子目录 */
	return kasprintf(GFP_KERNEL, "mygroup/%s", dev_name(dev));
}

	mydev_class = class_create(THIS_MODULE, "mydev");
	mydev_class->devnode = mydev_devnode;
```

| 事实 | 依据 |
|---|---|
| devtmpfs 用 `device_get_devnode()` 的返回值当节点名 | `drivers/base/devtmpfs.c:132` |
| 返回的 `mode` 为 0 时才用 **0600** | `drivers/base/devtmpfs.c:136-137` |
| 名字里带 `/` ⇒ 建子目录 | `drivers/base/devtmpfs.c:211` 的 `handle_create()` 会 `dev_mkdir()` |
| uevent 也用同一个函数取 `DEVNAME=` | `drivers/base/core.c:2583` |
| uevent 的 `DEVMODE=` 也来自它 | `drivers/base/core.c:2585` |

⇒ **改一处，devtmpfs 和 udev 两条路同时生效**（04 §2.2）——
因为两者都问同一个 `device_get_devnode()`。

**`/dev/input/event0` 这类带子目录的节点名就是这么来的**。

### §5.3 misc 设备的捷径：两个专用字段

`struct miscdevice`（`include/linux/miscdevice.h:79-89`）里有**两个**字段专门干这件事：

| 字段 | 行号 | 作用 |
|---|---|---|
| `nodename` | `include/linux/miscdevice.h:87` | **节点名**（可以带 `/` 建子目录）—— 不填就用 `name` |
| `mode` | `include/linux/miscdevice.h:88` | **权限** —— 不填（0）则 devtmpfs 用 0600 |

⇒ **用 misc 时完全不用写 `devnode` 回调** —— 填这两个字段就够（05 §2.1）。

另外三个可选字段：`parent`（`:84`，sysfs 的父设备）、
`this_device`（`:85`，**输出**参数，`misc_register()` 填进来的 `struct device *`）、
`groups`（`:86`，一组 sysfs 属性）。

**`this_device` 值得单独记**：它是 misc 唯一给你的 `struct device *` ——
想加 sysfs 属性或做 `dev_dbg()` 就用它。⚠️ 但它**不是** `cdev` 的 parent（05 §2.2）。

---

## §6 udev 规则（运行期）

udev **在内核树外**，本套只讲"内核提供了什么给它用"。

| 内核给的 | 位置 | udev 规则里怎么用 |
|---|---|---|
| `MAJOR=` / `MINOR=` | `drivers/base/core.c:2579-2580` | `ENV{MAJOR}=="243"` |
| `DEVNAME=` | `drivers/base/core.c:2583` | 决定默认节点名 |
| `DEVMODE=` | `drivers/base/core.c:2585` | udev 可以再覆盖 |
| `SUBSYSTEM=`（= class 名） | `drivers/base/core.c:2555` 的 `dev_uevent_name()` | `SUBSYSTEM=="mydev"` |
| class 自定义变量 | `drivers/base/core.c:2612-2613` | 你自己加的 `ENV{…}` |

典型规则（未实测，99 §5.1）：
```
SUBSYSTEM=="mydev", MODE="0660", GROUP="mygroup"
SUBSYSTEM=="mydev", ATTR{serial}=="ABC123", SYMLINK+="mydev-abc"
```

⚠️ **有 devtmpfs 时 udev 通常只改属性、不改名**（04 §2.2）——
因为节点已经存在了，`NAME=` 会导致重命名的竞态。

---

## §7 完整实例：一个"用动态设备号 + 自定义权限"的配置

### §7.1 内核侧

```c
/* 1. 编译期：确保开了 DEVTMPFS（否则要靠 udev） */

/* 2. 源码：动态申请，不写死主设备号 */
	alloc_chrdev_region(&mydev_devt, 0, MYDEV_MAX, "mydev");

/* 3. 源码：class + devnode 回调改权限和子目录 */
static char *mydev_devnode(struct device *dev, umode_t *mode)
{
	if (mode)
		*mode = 0660;
	return kasprintf(GFP_KERNEL, "mygroup/%s", dev_name(dev));
}

	mydev_class = class_create(THIS_MODULE, "mydev");
	mydev_class->devnode = mydev_devnode;

/* 4. 源码：每个实例 device_create 或 cdev_device_add（05 §1） */
	dev_set_name(&my->dev, "mydev%d", id);
```

⇒ 结果：`/dev/mygroup/mydev0`，权限 0660。

### §7.2 用户态侧（可选）

```
# /etc/udev/rules.d/99-mydev.rules
SUBSYSTEM=="mydev", GROUP="mygroup"
```

**只改 group** —— mode 已经由 `devnode` 回调给了 0660。

### §7.3 ⚠️ 三段的分工

| 段 | 决定什么 | 改了要重新做什么 |
|---|---|---|
| Kconfig | 节点**由谁**建 | 重新编译内核 |
| 源码（`devnode` 回调 / `dev_set_name`） | 节点**叫什么、默认权限** | 重新编译模块 |
| udev 规则 | **属性微调、符号链接** | `udevadm control --reload` |

⇒ **能在 udev 里解决的就别改内核** —— 但节点名和默认权限只能在内核里定。

---

## §8 定位：三层排查

### §8.1 第一层：编译期

```bash
# 确认三个选项
grep -E 'CONFIG_DEVTMPFS|CONFIG_PROC_FS' .config
zcat /proc/config.gz | grep DEVTMPFS      # 运行中的内核
```

⚠️ **没有 `dtbs_check` 这类工具可用** —— 本子系统没有 DT 绑定（§1.1），
配置错误只能靠 §8.2 和 §8.3 发现。

### §8.2 第二层：运行期静态（节点有没有出现）

```sh
# 1. devtmpfs 在工作吗？（04 §1.4）
dmesg | grep devtmpfs
#   "devtmpfs: initialized"  ⇒ 实例建好了
#   "devtmpfs: mounted"      ⇒ DEVTMPFS_MOUNT 生效了
mount | grep ' /dev '

# 2. 设备号占上了吗？（04 §4）
cat /proc/devices | grep mydev

# 3. class 和 device 建出来了吗？（04 §5）
ls /sys/class/mydev/
ls -l /sys/dev/char/ | grep mydev

# 4. 节点在吗？名字和权限对吗？
ls -l /dev/mydev*  /dev/mygroup/ 2>/dev/null
```

**四步各自对应一个配置点**：

| 步 | 空的话说明 |
|---|---|
| 1 | `CONFIG_DEVTMPFS` 没开，或没挂上 ⇒ 节点得靠 udev（§2.1） |
| 2 | `alloc_chrdev_region()` 没成功 |
| 3 | `class_create()` / `device_create()`（或 `cdev_device_add`）没成功 |
| 4 | 前三步都对但节点没有 ⇒ devtmpfs 和 udev 都没在工作 |

### §8.3 第三层：运行期动态（节点在但打不开）

```sh
# 权限问题？
ls -l /dev/mydev0        # crw------- ⇒ 只有 root（§5.2）
id                       # 你是谁

# open 报什么？
strace -e trace=openat cat /dev/mydev0 2>&1 | tail -3
```

| `open()` 的错误 | 配置层面的原因 |
|---|---|
| `ENXIO` | **不是配置问题** —— 是驱动忘了 `cdev_add()`（02 §1.0.A、05 §6） |
| `EACCES` | **配置问题** —— devtmpfs 默认 0600（§5.2 给改法） |
| `ENOENT` | 节点不存在（§8.2 第 4 步） |
| `ENODEV` | 你的驱动自己返回的 |

### §8.4 ⚠️ 三个静默失效

| 现象 | 原因 | 判据 |
|---|---|---|
| 节点名没有子目录 | `devnode` 回调返回的名字里没有 `/`，或**回调根本没被装上** | `cat /sys/class/mydev/mydev0/uevent \| grep DEVNAME` |
| 权限还是 0600 | `devnode` 回调**没设 `*mode`**，或装在了 `class` 上但 `device_type->devnode` 抢先返回了 | 同上，看 `DEVMODE=` |
| `devtmpfs.mount=1` 无效 | `CONFIG_DEVTMPFS` 没开 ⇒ 参数解析代码都没编进来 | `dmesg \| grep devtmpfs` 完全没有输出 |

**`cat /sys/class/<class>/<dev>/uevent` 是这三条的统一判据** ——
它打印的就是 `dev_uevent()` 生成的那组变量（04 §2.1），
里面有 `DEVNAME=` 和 `DEVMODE=`，**正是 devtmpfs 会用的值**。

---

## §9 错误消息 → 原因映射表

| dmesg 里的话 | 出自 | 最可能的配置原因 |
|---|---|---|
| `CHRDEV "%s" major requested (%u) is greater than the maximum (%u)` | `fs/char_dev.c:105` | 静态主设备号 ≥ **512**（§4.1） |
| `CHRDEV "%s" minor range requested (%u-%u) is out of range …` | `fs/char_dev.c:111` | `baseminor + count` 超过 2²⁰ |
| `CHRDEV "%s" dynamic allocation region is full` | `fs/char_dev.c:125` | 动态区（234-254 + 384-511）**全满** —— 系统上注册了 150+ 个字符设备区间 |
| `devtmpfs: initialized` | `drivers/base/devtmpfs.c:489` | 正常（说明实例建好了） |
| `devtmpfs: mounted` | `drivers/base/devtmpfs.c:381` | 正常（`DEVTMPFS_MOUNT` 生效） |
| `devtmpfs: unable to create devtmpfs %ld` | `drivers/base/devtmpfs.c:463-464` | `vfs_kern_mount()` 失败（启动早期内存不足） |
| `devtmpfs: unable to register devtmpfs type %i` | `drivers/base/devtmpfs.c:469-470` | `register_filesystem()` 失败 |
| `devtmpfs: unable to create devtmpfs %i` | `drivers/base/devtmpfs.c:483` | **kdevtmpfs 线程起不来** ⇒ 之后所有 `devtmpfs_create_node()` 都在 `!thread` 那里静默返回 0（04 §1.1） |
| `devtmpfs: error mounting %i` | `drivers/base/devtmpfs.c:379` | `DEVTMPFS_MOUNT` 时挂 `/dev` 失败 |
| `unable to get major %d for misc devices` | `drivers/char/misc.c:286` | 主设备号 10 被别人占了（几乎不可能） |
| **什么都没打，`open()` 返回 `EACCES`** | —— | devtmpfs 的 0600（§5.2） |
| **什么都没打，`open()` 返回 `ENXIO`** | —— | **不是配置问题** —— 忘了 `cdev_add()`（02 §1.0.A） |
| **什么都没打，节点名不对** | —— | `devnode` 回调没装上（§8.4） |

表里**只有三条是本子系统自己打的**（`fs/char_dev.c:105` / `:111` / `:125`）——
其余都来自边界（devtmpfs / misc）或者根本没有日志。

---

## §10 本篇覆盖情况

| 内容 | 状态 |
|---|---|
| 它没有 DT 绑定 | **给了两条可重跑的判据**（§1.1） |
| 五类配置点 | 完整，每条都有定位到行的消费代码（§1.2） |
| Kconfig 四项 | 完整 + 五种组合的后果表（§2.1） |
| 命令行参数 | 完整（只有一个） |
| 静态设备号 | 讲了区间和 `devices.txt` 的格式；⚠️ **申请流程未实测**（99 §5.1） |
| `devnode` 回调 | 完整，含两个回调的优先级和"只有 type 版能设 uid/gid" |
| udev 规则 | 只讲"内核提供了什么"；⚠️ **规则语法未实测**（99 §5.1） |
| ⚠️ **块设备的设备号** | **不覆盖** —— 虽然共享 `devices.txt`，但走 `blkdev` 那一套（99 §2.7） |
| ⚠️ ACPI | 本套 arm64-only；而且**字符设备层和 ACPI 也没关系** |

---

**下一篇**：07 篇讲编译期开关、`/proc/devices`、`/sys/dev/char/`、
**以及 `cdev_map` 没有出口时的绕法**。
