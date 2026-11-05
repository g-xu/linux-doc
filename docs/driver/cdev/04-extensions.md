---
title: 扩展机制
---

# cdev 04：独立框架与展示层

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 组织轴

### §0.1 一节一个独立框架，节与节之间互不调用

本篇的组织方式和 03 篇**相反**：03 是一条时间线（必须顺读），
本篇是**六个互不依赖的模块**（可以任意跳读）。⚠️ **跳读是设计意图**。

**本篇的六节全部在字符设备层之外** ——
`fs/char_dev.c` 的 685 行里**没有一行**属于本篇。
⇒ 这正是 01 §0.2 那句"它的边界比核心大得多"的具体展开。

### §0.2 分诊表：哪几个在 arm64 上真会碰到

| § | 框架 | 文件 / 行数 | Kconfig | arm64 相关度 |
|---|---|---|---|---|
| §1 | **devtmpfs** | `drivers/base/devtmpfs.c` **491** | `DEVTMPFS`（不默认开） | **`/dev` 节点从哪来的答案之一** |
| §2 | **uevent → udev** | `drivers/base/core.c` 的 `dev_uevent()` + `lib/kobject_uevent.c` | 总是编译 | **另一个答案** |
| §3 | **misc 设备** | `drivers/char/misc.c` **293** + `include/linux/miscdevice.h` **111** | `CONFIG_MISC`（几乎总开） | **只要一个节点时的标准做法** |
| §4 | **`/proc/devices`** | `fs/char_dev.c:51` + `fs/proc/devices.c` | `PROC_FS` | **唯一能看到设备号占用的地方** |
| §5 | **sysfs 三个出口** | `drivers/base/core.c` | `SYSFS` | 反查"哪个设备号对应哪个设备" |
| §6 | **模块自动加载** | `fs/char_dev.c:659` + `kmod` | `MODULES` | 现代系统上多数驱动是内建的，用得少 |

**§1 和 §2 是同一个问题的两个答案**（`/dev` 节点从哪来），
但它们**互不知道对方的存在** —— 都由 `device_add()` 分别触发。

### §0.3 各框架挂在 03 时间线的哪一点

```
   §A1 chrdev_init                （六节都不参与）
       │
   §A2 alloc_chrdev_region ──▶ §4  /proc/devices 里多一行
       │
   §A4 cdev_device_add
       ├─ cdev_add               （六节都不参与 —— 所以 cdev_map 没有出口）
       └─ device_add
            ├──▶ §5  /sys/dev/char/<M>:<m> · /sys/class/… · /sys/devices/…
            ├──▶ §2  uevent(KOBJ_ADD) ⇒ udev ⇒ mknod
            └──▶ §1  devtmpfs_create_node ⇒ 内核自己 mknod
       │
   §A5 open() 走到兜底项 ──▶ §6  request_module("char-major-N-M")
       │
   §3 misc_register 是 §A2+§A3+§A4 的一个打包（挂在整条时间线上）
```

| 挂载点 | 位置 |
|---|---|
| §1 `devtmpfs_create_node()` | `drivers/base/devtmpfs.c:121` |
| §2 `dev_uevent()` | `drivers/base/core.c:2566` |
| §4 `chrdev_show()` | `fs/char_dev.c:51` |
| §5 `device_create_sys_dev_entry()` | `drivers/base/core.c:3514` |
| §6 `base_probe()` | `fs/char_dev.c:659` |
| §3 `misc_register()` | `drivers/char/misc.c:175` |

---

## §1 devtmpfs：内核自己维护 `/dev`

### §1.1 它是一个内核线程 + 请求队列

这是本框架最反直觉的地方：`devtmpfs_create_node()` **不直接建节点**，
它把请求塞进一个链表然后**等一个内核线程去做**。

```
   device_add()
     └─ devtmpfs_create_node(dev)
          ├─ !thread ⇒ return 0            线程没起来就静默跳过
          ├─ 填 struct req（name / mode / uid / gid / dev）
          └─ devtmpfs_submit_req(&req, tmp)
               ├─ init_completion(&req->done)
               ├─ spin_lock(&req_lock) → 挂进 requests 链表 → unlock
               ├─ wake_up_process(thread)
               └─ wait_for_completion(&req->done)    同步等
                    │
   ┌────────────────▼──────────────────────────────────────┐
   │  kdevtmpfs 内核线程（devtmpfs_work_loop）               │
   │    while (1) {                                        │
   │      摘下整条 requests 链表（requests = NULL）          │
   │      逐个 handle(name, mode, uid, gid, dev)            │
   │           mode 非 0 ⇒ handle_create()                 │
   │           mode 为 0 ⇒ handle_remove()                 │
   │      complete(&req->done)                             │
   │      没请求了 ⇒ TASK_INTERRUPTIBLE + schedule()        │
   │    }                                                  │
   └───────────────────────────────────────────────────────┘
```

| 元素 | 位置 |
|---|---|
| `struct req`（8 个字段） | `drivers/base/devtmpfs.c:44-53` |
| `mode` 的注释 `/* 0 => delete */` | `drivers/base/devtmpfs.c:49` |
| `requests` 全局链表头 | `drivers/base/devtmpfs.c:53` |
| `req_lock`（spinlock） | `drivers/base/devtmpfs.c:42` |
| `thread` | `drivers/base/devtmpfs.c:38` |
| `devtmpfs_submit_req()` | `drivers/base/devtmpfs.c:104` |
| 挂链 | `drivers/base/devtmpfs.c:108-111` |
| 唤醒 + 等待 | `drivers/base/devtmpfs.c:113-114` |
| `devtmpfs_create_node()` | `drivers/base/devtmpfs.c:121` |
| `!thread ⇒ return 0` | `drivers/base/devtmpfs.c:126-127` |
| `device_get_devnode()` 取名字和权限 | `drivers/base/devtmpfs.c:132` |
| 默认权限 **0600** | `drivers/base/devtmpfs.c:136-137` |
| 块/字符设备的 `S_IFBLK` / `S_IFCHR` | `drivers/base/devtmpfs.c:138-141` |
| `devtmpfs_delete_node()` | `drivers/base/devtmpfs.c:148` |
| `handle()` 按 `mode` 分派 | `drivers/base/devtmpfs.c:387-394` |
| `handle_create()` | `drivers/base/devtmpfs.c:211` |
| `handle_remove()` | `drivers/base/devtmpfs.c:316` |
| `devtmpfs_work_loop()` | `drivers/base/devtmpfs.c:396` |
| 摘下整条链表 | `drivers/base/devtmpfs.c:400-403` |
| 逐个处理 + `complete` | `drivers/base/devtmpfs.c:404-410` |
| 睡下去 | `drivers/base/devtmpfs.c:413-415` |
| `devtmpfsd()`（线程主函数） | `drivers/base/devtmpfs.c:441` |
| `devtmpfs_init()` | `drivers/base/devtmpfs.c:456` |
| `kthread_run(devtmpfsd, &err, "kdevtmpfs")` | `drivers/base/devtmpfs.c:474` |

### §1.2 为什么要用线程？

因为**建节点要走文件系统路径解析**（`kern_path_create()` 之类），
而 `device_add()` 可能在**任何上下文**里被调用，包括：

| 上下文 | 问题 |
|---|---|
| 持着某个子系统的锁 | 文件系统操作可能反过来触发那个子系统 ⇒ 死锁 |
| 启动早期（`/` 还没挂上） | 路径解析会失败 |
| 中断/原子上下文 | 不能睡 |

⇒ **把动作丢给一个专职线程，就把"谁的上下文"这个变量消掉了**。

⚠️ **但 `devtmpfs_submit_req()` 仍然 `wait_for_completion()`**
（`drivers/base/devtmpfs.c:114`）——
⇒ **`device_add()` 的调用者还是会被阻塞**，只是阻塞在一个干净的等待点上。
所以 `device_add()` **不能在原子上下文里调**（这本来也是设备模型的要求）。

### §1.3 默认权限是 0600，节点名来自 `device_get_devnode()`

| 事实 | 位置 |
|---|---|
| `req.mode == 0` ⇒ 用 **0600** | `drivers/base/devtmpfs.c:136-137` |
| 名字/权限/uid/gid 全部来自 `device_get_devnode()` | `drivers/base/devtmpfs.c:132` |
| 文件头注释也说了这一点 | `drivers/base/devtmpfs.c:11-13`（原文："By default, all devices are named after the name of the device, owned by root and have a default mode of 0600. Subsystems can overwrite the default setting if needed."） |

**子系统怎么"覆盖默认"**：给 `struct class` 填 `devnode` 回调
（它会被 `device_get_devnode()` 调到）。
⇒ 这就是 `/dev/input/event0` 这种**带子目录**的节点名的来源
（回调返回 `"input/event0"`）。

⚠️ **0600 意味着只有 root 能访问** ——
所以在只有 devtmpfs 没有 udev 的系统上，普通用户访问你的设备会 `EACCES`。
⇒ 要么在 class 里填 `devnode` 回调返回 mode，要么在 `misc` 里填 `mode` 字段（§3）。

### §1.4 三个 Kconfig 的差别

| 选项 | 位置 | 作用 |
|---|---|---|
| `DEVTMPFS` | `drivers/base/Kconfig:31` | 建 devtmpfs 实例并维护节点 |
| `DEVTMPFS_MOUNT` | `drivers/base/Kconfig:50` | **内核自动把它挂到 `/dev`**（在挂完 rootfs 之后） |
| `DEVTMPFS_SAFE` | `drivers/base/Kconfig:65` | 挂载时加 `MS_NOEXEC \| MS_NOSUID` |

| 实现 | 位置 |
|---|---|
| `DEVTMPFS_MFLAGS` 的两个分支 | `drivers/base/devtmpfs.c:33`（SAFE）· `:35`（默认） |
| `mount_dev` 的初值 | `drivers/base/devtmpfs.c:40` |
| 命令行覆盖 `devtmpfs.mount=` | `drivers/base/devtmpfs.c:60` |
| 自动挂载（`devtmpfs_mount()`） | `drivers/base/devtmpfs.c:367`；实际挂载在 `drivers/base/devtmpfs.c:377` |
| 内部实例的挂载（给线程用） | `drivers/base/devtmpfs.c:426` |

**两次 `init_mount()` 的区别**：
`drivers/base/devtmpfs.c:426` 是线程在自己的 namespace 里把 devtmpfs 挂到 `/`（内部用），
`drivers/base/devtmpfs.c:377` 是把它挂到真正的 `/dev`（`DEVTMPFS_MOUNT` 时）。

启动日志：`devtmpfs: initialized`（`drivers/base/devtmpfs.c:487`）和
`devtmpfs: mounted`（`drivers/base/devtmpfs.c:381`）——
⇒ **dmesg 里有没有这两句，就是"devtmpfs 有没有在工作"的判据**（07 §4）。

---

## §2 uevent → udev

### §2.1 内核只发消息，节点是用户态建的

```
   device_add()
     └─ kobject_uevent(&dev->kobj, KOBJ_ADD)
          └─ dev_uevent(kobj, env)              ← drivers/base/core.c:2566
               ├─ dev->devt 非 0 ⇒
               │     add_uevent_var(env, "MAJOR=%u", MAJOR(dev->devt))
               │     add_uevent_var(env, "MINOR=%u", MINOR(dev->devt))
               │     add_uevent_var(env, "DEVNAME=%s", name)
               │     add_uevent_var(env, "DEVMODE=%#o", mode & 0777)
               └─ dev->class->dev_uevent 也可以加自己的变量
          └─ netlink 广播 ⇒ udevd
                              └─ mknod /dev/<DEVNAME> c <MAJOR> <MINOR>
                              └─ 按规则改名 / 改权限 / 建符号链接
```

| 元素 | 位置 |
|---|---|
| `dev_uevent()` | `drivers/base/core.c:2566` |
| `MAJOR=` / `MINOR=` | `drivers/base/core.c:2579-2580` |
| `DEVNAME=` | `drivers/base/core.c:2583` |
| `DEVMODE=` | `drivers/base/core.c:2585` |
| class 自己加变量 | `drivers/base/core.c:2612-2613` |
| `dev_uevent_filter()` | `drivers/base/core.c:2541` |
| `dev_uevent_name()` | `drivers/base/core.c:2555` |
| uevent ops 表 | `drivers/base/core.c:2633` |

### §2.2 和 devtmpfs 的分工

| | devtmpfs（§1） | udev |
|---|---|---|
| 谁建节点 | 内核线程 | 用户态守护进程 |
| 什么时候 | `device_add()` **同步等它做完** | 异步（uevent 发出后不知道多久） |
| 名字 | `device_get_devnode()` 的结果 | 同一个（从 `DEVNAME=`），**但可以被规则改** |
| 权限 | 默认 **0600** | 规则可以改成 0666、改 group |
| 符号链接（`/dev/disk/by-uuid/…`） | **不做** | 做 |
| 需要用户态配合 | 不需要 | 需要 |

**两者可以共存**：现代发行版就是 devtmpfs 先建好节点（保证早期可用），
udev 再去改权限/加链接。
⇒ 这也是为什么 udev 规则里常见 `MODE=`、`GROUP=` 而不是 `NAME=` ——
节点已经存在了，改属性就够。

⚠️ **嵌入式系统上如果两者都没有**，`/dev` 就必须在 rootfs 镜像里**预先 `mknod`** ——
这就是老式 busybox 系统里 `/dev` 是静态目录的原因（01 §6.3）。

---

## §3 misc 设备：只要一个节点时的捷径

### §3.1 它是"字符设备层之上的第二级 minor 分配器"

```
   misc 模块 init
     └─ register_chrdev(MISC_MAJOR=10, "misc", &misc_fops)
          ⇒ 占掉主设备号 10 的全部 256 个 minor
          ⇒ misc_fops 只有一个 .open = misc_open

   你的驱动
     └─ misc_register(&my_misc)
          ├─ minor == MISC_DYNAMIC_MINOR(255) ⇒ 从位图里分一个
          ├─ 挂进 misc_list
          └─ device_create(misc_class, parent, MKDEV(10, minor), …, "%s", name)
               ⇒ /dev/<name> 出现（走 §1 或 §2）

   用户态 open("/dev/<name>")
     └─ chrdev_open() ⇒ 找到 misc 的 cdev ⇒ replace_fops(misc_fops)
          └─ misc_open(inode, file)
               ├─ 按 MINOR(inode->i_rdev) 在 misc_list 里找你的 miscdevice
               ├─ 找不到 ⇒ request_module("char-major-10-<minor>")
               └─ replace_fops(file, my_misc.fops)   第二次替换！
```

| 元素 | 位置 |
|---|---|
| `MISC_MAJOR` = 10 | `include/linux/miscdevice.h` |
| `MISC_DYNAMIC_MINOR` = **255** | `include/linux/miscdevice.h:74` |
| `struct miscdevice` | `include/linux/miscdevice.h:79` |
| `MODULE_ALIAS` 辅助宏 | `include/linux/miscdevice.h:109` |
| `misc_open()` | `drivers/char/misc.c:100` |
| 自动加载 | `drivers/char/misc.c:119` |
| `misc_fops`（只有 open） | `drivers/char/misc.c:151` |
| `misc_register()` | `drivers/char/misc.c:175`（导出于 `:231`） |
| 动态 minor 判断 | `drivers/char/misc.c:179` |
| 算出 `dev_t` | `drivers/char/misc.c:205` |
| 失败时还回 minor | `drivers/char/misc.c:216` |
| `misc_deregister()` | `drivers/char/misc.c:241`（导出于 `:255`） |
| `device_destroy()` | `drivers/char/misc.c:250` |
| 占主设备号 10 | `drivers/char/misc.c:280` |
| 失败日志 | `drivers/char/misc.c:286` |

### §3.2 **两次 `replace_fops`** 是这个框架的本质

| 次 | 谁做 | 从什么换成什么 |
|---|---|---|
| ① | `chrdev_open()`（`fs/char_dev.c:412`） | `def_chr_fops` → `misc_fops` |
| ② | `misc_open()`（`drivers/char/misc.c:100` 里） | `misc_fops` → **你的 fops** |

⇒ **同一个套路套了两层** ——
理解了 03 §A5 的 `replace_fops`，misc 就没有任何新东西了。

### §3.3 用 misc 的代价

| 代价 | 说明 |
|---|---|
| 主设备号固定是 **10** | 不能自选 |
| minor 只有 **256 个**（全系统共享） | ⚠️ 多个模块都用 misc 时可能耗尽 |
| 没有自己的 `/sys/class/<name>/` | 全都在 `/sys/class/misc/` 下 |
| 拿不到 `cdev`，做不了 `cdev_set_parent()` 生命周期绑定 | ⚠️ 和老式 `register_chrdev()` 同一个问题（03 §C2） |
| 一个 `miscdevice` 只对应一个 minor | 要多个节点得注册多次 |

**换来的好处**：少写约 30 行（设备号 + cdev + class + device_create 四步全省）。

⇒ **判据**（01 §7.2）：只要一个节点、不在乎主设备号、不需要自己的 class ⇒ 用 misc。

---

## §4 `/proc/devices`：唯一能看到设备号占用的地方

### §4.1 输出格式

```
Character devices:
  1 mem
  4 tty
  5 /dev/tty
 10 misc
 13 input
189 usb_device
243 mydev            ← 你 alloc_chrdev_region 得到的
...

Block devices:
...
```

| 元素 | 位置 |
|---|---|
| `chrdev_show()` | `fs/char_dev.c:51` |
| `#ifdef CONFIG_PROC_FS` | `fs/char_dev.c:47` |
| 加锁 | `fs/char_dev.c:54` |
| 遍历桶链 | `fs/char_dev.c:55` |
| 过滤 `major == offset` | `fs/char_dev.c:56` |
| 格式 `"%3d %s\n"` | `fs/char_dev.c:57` |

### §4.2 ⚠️ 它读的是 `chrdevs[]`，不是 `cdev_map`

**这是本子系统最容易误诊的一点**（02 §1.0.D）：

| `/proc/devices` 里有你的主设备号 | 说明什么 | **不说明**什么 |
|---|---|---|
| 是 | `alloc_chrdev_region()` 成功了 | **不说明 `cdev_add()` 做了** |

⇒ **"节点在、`/proc/devices` 里有、但 `open()` 返回 `ENXIO`"**
= 你忘了 `cdev_add()`（或 `cdev_device_add()`）。

### §4.3 ⚠️ 两个次要的坑

| 坑 | 说明 |
|---|---|
| 同一个主设备号可能出现**多行** | 每个 `char_device_struct` 一行；你在同一个 major 上 `alloc_chrdev_region()` 两次就有两行（03 §B6） |
| **名字可能被截断到 63 字符** | `char name[64]` + `strlcpy`（`fs/char_dev.c:39`、`fs/char_dev.c:153`） |
| 它**不显示 minor 区间** | 格式里只有 `major` 和 `name`（`fs/char_dev.c:57`）⇒ 看不出谁占了哪些 minor |

最后一条是真实的可观测性缺口：**内核知道 minor 区间**
（`char_device_struct.baseminor` / `minorct`）**但不暴露** ——
所以"我的 minor 区间和谁冲突了"这个问题**没有直接答案**（07 §4.3）。

---

## §5 sysfs 的三个出口

### §5.1 三条路径

| 路径 | 内容 | 谁建 |
|---|---|---|
| `/sys/dev/char/<major>:<minor>` | **符号链接**，指向 `/sys/devices/…` | `device_create_sys_dev_entry()` |
| `/sys/class/<class>/<name>` | 符号链接，指向 `/sys/devices/…` | `device_add()` 的 class 部分 |
| `/sys/devices/.../<name>/dev` | **一个文件，内容是 `<major>:<minor>`** | `device_add()` 的 `dev_attr_dev` |

| 元素 | 位置 |
|---|---|
| `sysfs_dev_char_kobj` | `drivers/base/core.c:2283` |
| 它的创建（`kobject_create_and_add("char", dev_kobj)`） | `drivers/base/core.c:4158` |
| 选桶（字符 vs 块） | `drivers/base/core.c:3509` |
| `device_create_sys_dev_entry()` | `drivers/base/core.c:3514` |
| 用 `format_dev_t()` 生成 `<M>:<m>` 名字 | `drivers/base/core.c:3521` |
| `device_remove_sys_dev_entry()` | `drivers/base/core.c:3528` |
| `device_add()` 里的调用 | `drivers/base/core.c:3664` |
| `device_del()` 里的调用 | `drivers/base/core.c:3867` |
| 一致性的注释 | `drivers/base/core.c:3498-3499` |

### §5.2 `/sys/dev/char/` 是"设备号 → 设备"反查的唯一工具

```sh
# 已知设备号，想知道是哪个设备
ls -l /sys/dev/char/243:0
# → ../../devices/platform/soc/.../mydev

# 已知设备，想知道设备号
cat /sys/devices/platform/.../mydev/dev
# → 243:0
```

**它用的名字就是 `format_dev_t()` 的输出**（`drivers/base/core.c:3521`，
宏在 `include/linux/kdev_t.h:17-21`）⇒ 格式是 `"%u:%u"`。

⚠️ **它仍然不告诉你 `cdev_add()` 做了没有**（§4.2 那个问题）——
`/sys/dev/char/` 反映的是 `struct device`，`cdev_map` 反映的是 `struct cdev`，
**两者独立**（02 §1.0.B）。

---

## §6 模块自动加载：`char-major-N-M`

机制已在 03 §B5 展开。本节只讲**使用者要做什么**。

| 角色 | 要做的 |
|---|---|
| 内核侧 | 什么都不用做 —— 兜底项在 `chrdev_init()` 时就装好了（`fs/char_dev.c:669`） |
| 模块侧 | 声明 `MODULE_ALIAS("char-major-<M>-<m>")` |
| misc 设备 | 用现成的宏（`include/linux/miscdevice.h:109`） |
| 用户态 | 需要 `/sbin/modprobe` 可用（`request_module` 走它） |

| 元素 | 位置 |
|---|---|
| `base_probe()` | `fs/char_dev.c:659` |
| 第一次尝试（带 minor） | `fs/char_dev.c:661` |
| 老式 2.4 别名兜底（只带 major） | `fs/char_dev.c:662-663` |
| `return NULL` ⇒ 触发重试 | `fs/char_dev.c:664` |
| `goto retry` | `drivers/base/map.c:129` |

⚠️ **现代 arm64 系统上用得很少**：绝大多数字符设备驱动是
① 内建的，或 ② 由 DT/ACPI 匹配自动 probe 的。
⇒ "打开节点触发模块加载"主要是 x86 桌面时代的机制。

但它仍然是**理解 `kobj_lookup()` 为什么要 `goto retry`** 的唯一理由（03 §B4）。

---

## §7 汇总表

| § | 框架 | 职责 | 挂在 03 时间线的哪一点 | arm64 相关 | 最该记住的一条 |
|---|---|---|---|---|---|
| §1 | devtmpfs | 内核自己在 `/dev` 建节点 | `device_add()` 里 | | 它是**内核线程 + 同步等待**；默认权限 **0600** |
| §2 | uevent → udev | 通知用户态去建节点 | 同上 | | 和 devtmpfs **互不知道对方**；两者常共存（devtmpfs 建、udev 改属性） |
| §3 | misc 设备 | 只要一个节点的捷径 | 打包了 §A2+§A3+§A4 | | 本质是**第二次 `replace_fops`**；代价是拿不到 cdev |
| §4 | `/proc/devices` | 看设备号占用 | `alloc_chrdev_region()` 时 | | 它读 `chrdevs[]` —— **有你的 major 不代表 `cdev_add()` 做了**；不显示 minor 区间 |
| §5 | sysfs 三出口 | 设备号 ↔ 设备的反查 | `device_add()` 里 | | `/sys/dev/char/<M>:<m>` 是唯一的反查工具 |
| §6 | 模块自动加载 | 打开节点时加载驱动 | `open()` 走到兜底项 | | arm64 上少用；但它解释了 `goto retry` 的存在 |

**六节里只有 §1 §2 §3 参与"节点能不能出现"**，
§4 §5 是可观测性，§6 是历史机制。

⚠️ **六节合起来仍然回答不了一个问题**：
**"哪些设备号被 `cdev_add()` 过？"**
⇒ `cdev_map` **没有任何出口**（03 §C5）。这是本子系统最大的可观测性缺口，07 §4.3 会给绕法。

---

**下一篇**：05 篇给出四种角色的代码骨架（现代标准写法 / misc 捷径 / 多 minor / 老式）。
