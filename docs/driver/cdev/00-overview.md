---
title: 总览
---

# cdev 00：总览（复习入口）

> 版本：Linux 6.1.158 · 架构基准：arm64 · 本篇定位：**复习入口**
>
> 隔几个月回来，只读这一篇就能恢复全貌；要细节再翻后面九篇（§8 有导读表）。
>
> ⚠️ 本篇是**浓缩**，不是新内容。所有论断的取证都在对应的分篇里。
>
> ⚠️ **关于 §2**：字符设备层是**纯软件子系统**，没有对应的硬件。
> 按 skill 的适配规则，§2 改讲**它必须理解的背景**：
> UNIX 设备文件的设计、设备号空间的演进、字符 vs 块设备的差异、`/dev` 的四代方案。
> 这些背景决定了这个子系统为什么长成现在这样（§2.10 给映射表）。

---

## §1 定位：cdev 解决什么问题

### §1.1 一句话

**字符设备层把"一个设备号"路由到"一组 `file_operations`"，
让用户态的 `open("/dev/xxx")` 落到正确的驱动函数上。**

### §1.2 它管什么、不管什么

| | 内容 |
|---|---|
| **管** | 设备号空间的分配（`chrdevs[]`）· 设备号 → `cdev` 的查找（`cdev_map`）· `open()` 时把 `f_op` 换成驱动的（`replace_fops`）· `cdev` 的引用计数 |
| **不管** | **`/dev` 节点的创建** —— 那是 `class`/`device_create` + devtmpfs/udev 的事（04 §1 §2） |
| **不管** | **`open()` 之后的一切** —— read/write/ioctl 全是 VFS 直接调你的 fops（03 §A6） |
| 不管 | 路径解析、inode 的产生（VFS 的事） |
| **不管** | **块设备** —— 虽然共享设备号空间，块设备走 `blkdev` 那一套（99 §2.7） |

### §1.3 四个关键判据

| 判据 | 答案 | 出处 |
|---|---|---|
| 有 `struct bus_type` 吗？ | **没有** —— `struct cdev` 只有 6 个字段，连 `name` 都没有 | 02 §1.0.B |
| 读设备树吗？ | **没有 DT 绑定，一条属性都不读** | 06 §1.1 |
| 有 tracepoint 吗？ | **一个都没有** | 07 §3.1 |
| 有 devres（`devm_*`）版本吗？ | **没有，而且是刻意的** | 05 §0.3 · 08 §5 |

### §1.4 规模

| 项 | 数值 |
|---|---|
| 核心 | `fs/char_dev.c` **685 行** |
| 核心头文件 | `include/linux/cdev.h` **39 行** · `include/linux/kdev_t.h` 83 行 |
| 查找层 | `drivers/base/map.c` **154 行** |
| **核心合计** | **961 行** |
| 导出符号 | **12 个** |
| 错误码点 | **11 个**（4 种码，语义完全不重叠） |
| 日志输出点 | **5 个**（3 个 `pr_err` + 2 个 `WARN_ON`） |
| tracepoint | **0** |
| `TODO`/`FIXME`/`XXX` | **0** |
| 边界（本套一并覆盖） | `drivers/base/devtmpfs.c` **491** · `drivers/char/misc.c` **293** · `include/linux/miscdevice.h` **111** |
| 官方文档 | ⚠️ **没有框架文档** —— 只有 `Documentation/admin-guide/devices.txt`（**3086 行**的设备号分配表） |
| DT 绑定 | **0**（根本没有） |

**对比本系列其它子系统**：

| 子系统 | 核心行数 | 导出符号 | 错误码点 | tracepoint |
|---|---|---|---|---|
| irq | 15044 | 151 | 208 | 5 |
| remoteproc | 5068 | 33 | 94 | 0 |
| **cdev** | **961** | **12** | **11** | 0 |

⇒ **它是本系列里最小的子系统，但它的生命周期语义是最难写对的**（§1.5）。

### §1.5 难点不在代码量，在三条注释

961 行读完只需要一小时，但**三条语义**决定了驱动写得对不对，
而它们**只存在于源码注释里**（`Documentation/` 一句没写，03 §C5）：

| # | 注释 | 位置 | 含义 |
|---|---|---|---|
| ① | "any cdevs already open will remain and their fops will still be callable **even after cdev_del returns**" | `fs/char_dev.c:590-592` | **`cdev_del()` 不是安全点**（03 §A8.1 · 08 §4） |
| ② | "Callers must assume that userspace was able to open the cdev … **even if this function fails**" | `fs/char_dev.c:534-535` | `cdev_device_add()` **失败也可能已被打开**（03 §A4.1） |
| ③ | "This should be called **before** cdev_add" | `fs/char_dev.c:501-507` | `cdev_set_parent()` 的顺序要求（01 §3.3 · 08 §2） |

⇒ **这三条合起来就是"为什么不能用 `devm_kzalloc()` 存私有数据"**（05 §1.3 · 08 §5）。

---

## §2 背景：UNIX 设备文件是怎么来的，为什么长这样

### §2.1 "一切皆文件"这个决定的代价

UNIX 最著名的设计决定是"**把设备也做成文件**"：
用 `open` / `read` / `write` / `close` 操作硬件，而不是发明一套新的系统调用。

好处很明显（shell 重定向、权限模型、路径命名空间全都免费复用）。
但它引出一个必须解决的问题：

> **文件系统里的一个"文件"，怎么表示"这不是数据，是一个设备"？**

答案是**设备文件**（device special file）：
一个特殊的 inode，它**不占数据块**，而是记录一个**设备号**。

| inode 里记什么 | 字段 | 位置 |
|---|---|---|
| 这是字符设备还是块设备 | `i_mode` 的 `S_IFCHR` / `S_IFBLK` | —— |
| **设备号** | `i_rdev` | `include/linux/fs.h:628` |

⇒ **设备号是"文件系统"和"驱动"之间唯一的桥** ——
这就是整个字符设备层存在的理由。

### §2.2 主设备号 / 次设备号：一个两级命名方案

设计者面对的问题：**一台机器上有几百个设备，怎么编号？**

用一个平坦的整数？那每加一个驱动就要去中央注册处申请一批号。
UNIX 的选择是**两级**：

| 级 | 含义 | 谁分配 |
|---|---|---|
| **主设备号**（major） | **"哪一类设备 / 哪个驱动"** | **中央登记**（`Documentation/admin-guide/devices.txt`）或内核动态分配 |
| **次设备号**（minor） | **"这一类里的第几个"** | **驱动自己决定** |

**这个两级方案的价值**：驱动作者只需要申请**一个**主设备号，
minor 怎么用完全自由 —— 一个串口驱动可以自己决定 minor 0 是 ttyS0、1 是 ttyS1。

Linux 的位宽（01 §1.1）：

| 项 | 值 | 位置 |
|---|---|---|
| `dev_t` 总宽 | 32 位 | —— |
| `MINORBITS` | **20** | `include/linux/kdev_t.h:7` |
| ⇒ major 范围 | 0 – 4095（12 位） | —— |
| ⇒ minor 范围 | 0 – 1048575（20 位） | —— |
| `MAJOR` / `MINOR` / `MKDEV` | —— | `include/linux/kdev_t.h:10-12` |

⚠️ **历史包袱**：早期 UNIX 是 8+8 位（major/minor 各 0–255），
所以老式文件系统（ext2 的某些 inode 格式）只能存 16 位设备号。
⇒ 内核为此保留了三个转换辅助（`old_valid_dev()` 等，`include/linux/kdev_t.h:24` 起，01 §1.2）。

### §2.3 Linux 实际的主设备号分区

| 区间 | 谁管 | 判据 |
|---|---|---|
| **1 – 233** | **中央登记**（`Documentation/admin-guide/devices.txt`，3086 行） | 写在源码里的常量 |
| **234 – 254** | 内核动态分配（首选区） | `alloc_chrdev_region()` |
| **384 – 511** | 内核动态分配（扩展区） | 同上 |
| 上界 512 | —— | `CHRDEV_MAJOR_MAX`（`include/linux/fs.h:2896`） |

| 常量 | 值 | 位置 |
|---|---|---|
| `CHRDEV_MAJOR_DYN_END` | 234 | `include/linux/fs.h:2898` |
| `CHRDEV_MAJOR_DYN_EXT_START` | 511 | `include/linux/fs.h:2900` |
| `CHRDEV_MAJOR_DYN_EXT_END` | 384 | `include/linux/fs.h:2901` |

**为什么有两个动态区**：1–233 早就用满了，
234–254 只有 21 个号，也很快不够 ⇒ 后来又在 384–511 开了 128 个。
⇒ 这就是 `find_dynamic_major()`（`fs/char_dev.c:65`）要**扫两遍**的原因（03 §A2.1）。

⚠️ **两个区的扫描方式还不一样**（03 §A2.1）：
首选区（234–254）判"整个哈希桶为空"（`fs/char_dev.c:71`），
扩展区（384–511）要**遍历链表**（`fs/char_dev.c:77-79`）——
因为哈希表只有 255 个桶（`fs/char_dev.c:32`）而 major 上界是 512，384 会和 129 撞桶。

### §2.4 字符设备 vs 块设备：一条真实的硬件差异

这不是随意的分类，它对应一个**硬件事实**：

| | 字符设备 | 块设备 |
|---|---|---|
| 硬件的最小访问单位 | **一个字节**（或一个"事件"） | **一个块**（512B / 4KB） |
| 能不能任意定位 | 通常不能（串口、鼠标） | **能**（磁盘的任意扇区） |
| 能不能缓存 | **不能**（读一个字节就消耗掉了） | **必须缓存**（page cache） |
| 能不能重排请求 | 不能（顺序有语义） | **能**（IO 调度器） |
| 内核里的路径 | VFS → 你的 fops（**直达**） | VFS → page cache → bio → IO 调度 → 驱动 |

⇒ **块设备需要一整套额外的基础设施（page cache、bio、IO 调度、分区表）**，
所以它是**另一个子系统**（`block/`），虽然共享同一个设备号空间。

**代码层面的分界**：
`drivers/base/core.c:3509` 那里按设备类型选 `/sys/dev/char/` 还是 `/sys/dev/block/`；
devtmpfs 建节点时按 `is_blockdev(dev)` 选 `S_IFBLK` 还是 `S_IFCHR`
（`drivers/base/devtmpfs.c:138-141`）。

### §2.5 为什么 `open()` 需要"两步"

一个关键的设计约束：**inode 是文件系统的东西，`cdev` 是驱动的东西，
两者在不同的时间被创建**。

```
   时间线
   ─────────────────────────────────────────────────────────▶
   ① 驱动 probe：cdev_add()          ← 内核内部，建立"设备号 → fops"
   ② 用户态 mknod / devtmpfs：建节点  ← 文件系统里出现一个 inode
   ③ 用户态 open()                    ← 此刻才需要把 ① 和 ② 对上
```

**① 和 ② 的顺序是任意的**（甚至可以先建节点后加载驱动）
⇒ 所以**必须在 ③ 的时候查表**，不能在建节点时就绑定。

⇒ 这就是 `def_chr_fops`（`fs/char_dev.c:452-455`）存在的理由：
**VFS 对所有字符设备节点都装同一个 `open`**，
由它在运行时查表并 `replace_fops()`（`fs/char_dev.c:412`）。

**代价**：多一次间接（第一次 open 时的 `kobj_lookup()`）。
⇒ 所以有 `inode->i_cdev` 缓存（`include/linux/fs.h:684`，02 §1.0.C · 08 §6）。

### §2.6 `/dev` 的四代方案

"节点从哪来"这个问题，Linux 历史上有四个答案：

| 代 | 方案 | 怎么工作 | 问题 |
|---|---|---|---|
| 一 | **静态 `/dev`** | 在 rootfs 里预先 `mknod` 出几千个节点 | 大部分节点对应的设备根本不存在；加新设备要手工建 |
| 二 | **devfs**（2.4 时代） | 内核里一个虚拟文件系统，驱动注册时自动出现节点 | **已从内核移除** —— 命名策略写在内核里，不灵活 |
| 三 | **udev**（用户态） | 内核发 uevent，用户态守护进程 `mknod` | 启动早期 udev 还没跑，`/dev` 是空的 |
| 四 | **devtmpfs**（当前） | **内核建一个最小可用的 `/dev`**，udev 再来改属性 | 权限只能是简单的默认值（0600） |

**现代系统是三 + 四并用**（04 §2.2）：
devtmpfs 保证启动早期就有节点，udev 负责改权限、建符号链接。

| 元素 | 位置 |
|---|---|
| devtmpfs 的设计说明 | `drivers/base/devtmpfs.c:2-14`（文件头注释） |
| 默认权限 **0600** | `drivers/base/devtmpfs.c:136-137` |
| 节点名/权限来自 `device_get_devnode()` | `drivers/base/devtmpfs.c:132` · `drivers/base/core.c:3972` |
| uevent 里的 `MAJOR=`/`MINOR=`/`DEVNAME=`/`DEVMODE=` | `drivers/base/core.c:2579-2585` |

⇒ **devtmpfs 和 udev 用的是同一个 `device_get_devnode()`** ——
所以改一处两边都生效（06 §5.2）。

### §2.7 为什么 devtmpfs 要用一个内核线程

建节点要走**文件系统路径解析**（`kern_path_create()` 之类），
而 `device_add()` 可能在任何上下文里被调用（04 §1.2）：

| 上下文 | 直接建节点的问题 |
|---|---|
| 持着某个子系统的锁 | 文件系统操作可能反过来触发那个子系统 ⇒ 死锁 |
| 启动极早期 | `/` 还没挂上，路径解析必然失败 |

⇒ **把动作丢给一个专职线程（`kdevtmpfs`），"谁的上下文"这个变量就消掉了**。

| 元素 | 位置 |
|---|---|
| 线程指针 | `drivers/base/devtmpfs.c:38` |
| 请求结构（8 个字段） | `drivers/base/devtmpfs.c:44-53` |
| `mode == 0` 表示删除 | `drivers/base/devtmpfs.c:49` 的注释 `/* 0 => delete */` |
| 提交 + **同步等待** | `drivers/base/devtmpfs.c:104`（`wait_for_completion` 在 `:114`） |
| 工作循环 | `drivers/base/devtmpfs.c:396` |
| 线程创建 | `drivers/base/devtmpfs.c:474` |

⚠️ **它仍然同步等待** ⇒ `device_add()` 的调用者会被阻塞，
只是阻塞在一个干净的等待点上（04 §1.2）。

### §2.8 为什么需要"引用计数"而不是简单的"注册/注销"

核心矛盾：

> **用户态可能在驱动 `remove()` 之后还持着 fd。**

| 如果只有"注册/注销" | 后果 |
|---|---|
| `remove()` 时释放私有数据 | **用户态的 read/write 访问已释放内存** ⇒ use-after-free |
| 等所有 fd 关闭再 `remove()` | **做不到** —— 拔 USB 设备时用户态可能永远不关 |

⇒ **唯一的解法是引用计数**：`remove()` 只"摘牌子"，
最后一个 fd 关闭时才真正释放。

**Linux 的实现是三层引用**（03 §B3）：

| 层 | 保护什么 | 谁拿 / 谁放 |
|---|---|---|
| ① `cdev->kobj` 的 kref | `struct cdev` 对象本身 | `fs/char_dev.c:355` / `fs/file_table.c:323` |
| ② `cdev->owner` 模块引用 | **`cdev` 所在的模块** | `fs/char_dev.c:353` / `fs/char_dev.c:361-368` |
| ③ `ops->owner` 模块引用 | **fops 所在的模块**（可能是另一个模块！） | `fs/char_dev.c:408` / `fs/file_table.c:325` |
| ④ parent kobject | **你的 `struct device`** | `fs/char_dev.c:494` / `fs/char_dev.c:607` · `:617` |

⇒ **② 和 ③ 必须是两个计数**，因为 misc 设备场景下它们属于不同模块（08 §7）。

### §2.9 为什么"查找表"用哈希 + 最长匹配

`cdev_map`（`fs/char_dev.c:28`）要回答的问题是：
**"设备号 N 属于哪个 `cdev`？"**

难点：一次 `cdev_add()` 可以覆盖**一段连续的设备号**
（`cdev_add(p, dev, count)`）⇒ 这是一个**区间查找**问题，不是点查找。

Linux 的方案（`drivers/base/map.c`）：

| 元素 | 做法 | 位置 |
|---|---|---|
| 哈希 | 按 `MAJOR(dev) % 255` 分 255 个桶 | `drivers/base/map.c:28` |
| 每桶 | **一条按区间长度（`range`）升序排的链表** | `drivers/base/map.c:58-59` |
| 查找 | **找第一个包含目标的项 —— 也就是最短（最精确）的那个** | `drivers/base/map.c:108-111` |
| 兜底 | 每个桶末尾都有一个 `dev=1, range=~0` 的项 | `drivers/base/map.c:147-151` |

**兜底项的作用是"模块自动加载"**：
它的 `get` 回调是 `base_probe()`（`fs/char_dev.c:659`），
会 `request_module("char-major-%d-%d", …)`（`fs/char_dev.c:661`）——
这就是"打开一个没驱动的节点会自动加载模块"的实现（03 §B5 · 04 §6）。

⚠️ **20 年前就承认这个数据结构该换了**：
`drivers/base/map.c:7-9` 的注释原文 ——
"NOTE: data structure needs to be changed. It works, but for large dev_t
it will be too slow. It is **isolated**, though, so these changes will be local to that file."
⇒ "isolated" 也解释了为什么 `cdev_map` 是 `static`、没有任何出口（03 §C3 · 08 §9）。

### §2.10 背景事实 → 框架设计的映射

**把这张表记住，整个子系统的形状就推得出来了**：

| 背景事实 | 框架里对应的设计 |
|---|---|
| 设备号是"文件系统 ↔ 驱动"唯一的桥（§2.1） | `inode->i_rdev`（`include/linux/fs.h:628`）+ 整个字符设备层 |
| 主/次两级编号（§2.2） | `MAJOR`/`MINOR`/`MKDEV`（`include/linux/kdev_t.h:10-12`） |
| 主设备号是稀缺的中央资源（§2.3） | `chrdevs[]` 登记表 + 两个动态区 + `/proc/devices`（04 §4） |
| inode 和 cdev 在不同时间创建（§2.5） | **`def_chr_fops` + `replace_fops`**（`fs/char_dev.c:412`，08 §3） |
| 第一次查表有代价（§2.5） | `inode->i_cdev` 缓存 + `cdev->list` 反向链表（08 §6） |
| 节点从哪来有四代方案（§2.6） | **字符设备层刻意不管节点** ⇒ class + devtmpfs/udev 分工（04 §1 §2） |
| 建节点要走文件系统（§2.7） | devtmpfs 的内核线程 + 请求队列（04 §1.1） |
| 用户态可能在 remove 之后还持 fd（§2.8） | **三层引用计数** + `cdev_set_parent()` + `device.release`（05 §1.3 · 08 §4 §5） |
| fops 和 cdev 可能属于不同模块（§2.8） | **两个 `owner`**（08 §7） |
| 设备号是区间不是点（§2.9） | `kobj_map` 的最长匹配 + 兜底项（03 §B4） |
| 字符 vs 块是真实的硬件差异（§2.4） | 块设备是另一个子系统（99 §2.7） |

---

## §3 概念模型

### §3.1 三件独立的事（全套文档的主线）

| # | 动作 | API | 效果在哪看 | 少了它 |
|---|---|---|---|---|
| ① | 占设备号 | `alloc_chrdev_region()` | `/proc/devices` | 设备号冲突（`-EBUSY`） |
| ② | 让设备号"活" | **`cdev_add()`** | **哪里都看不到** | `open()` → **`ENXIO`** |
| ③ | 建 `/dev` 节点 | `class_create` + `device_create`（或 misc） | `/dev/` + `/sys/class/` | 没有节点可 open |

**这是本套文档反复回来的那条主线**（02 §1.0.A · 07 §7 · 08 §1）。

### §3.2 四个对象

```
   一个字符设备
   ┌─── struct cdev（6 个字段）──────────────────────────────┐
   │  kobj ──parent──▶ struct device 的 kobj                  │
   │  owner  = THIS_MODULE          和 fops.owner 是两个     │
   │  ops   ──▶ struct file_operations（你写的）               │
   │  dev / count                                             │
   │  list ──┬──▶ inode A 的 i_devices   反向链表            │
   │         └──▶ inode B 的 i_devices                        │
   └──────────────────────────────────────────────────────────┘

   全局两张表（互不相干）
   ┌──────────────────────────────────────────────────────────┐
   │  chrdevs[255] ── "谁占了哪段设备号"   ⇒ /proc/devices     │
   │  cdev_map     ── "哪段设备号 → 哪个 cdev"  ⇒ 无出口   │
   └──────────────────────────────────────────────────────────┘
```

| # | 对象 | 一句话 | 详见 |
|---|---|---|---|
| ① | `struct cdev` | **只有 6 个字段，连 `name` 都没有**；回答"这段设备号由谁的 fops 负责" | 02 §5.1 · 08 §2 |
| ② | `struct char_device_struct` | `chrdevs[]` 的元素；**名字里有 `device` 但和 `struct device` 无关** | 02 §5.2 · 03 §C4 |
| ③ | `struct kobj_map` / `struct probe` | 区间查找表（哈希 + 最长匹配 + 兜底项） | 02 §5.3 · §2.9 |
| ④ | `struct inode` 的四个字段 | `i_rdev`（设备号）· `i_cdev`（缓存，在 union 里）· `i_devices`（反向链表节点）· `i_mapping` | 02 §5.4 · 08 §6 |

### §3.3 五组最容易搞错的关系（02 §1.0 的浓缩）

| # | 错误理解 | 正确的 |
|---|---|---|
| A | `alloc_chrdev_region()` 之后 `/dev` 就有节点 | **三件独立的事**（§3.1） |
| B | `cdev` 是 `device` 的一种，或里面有个 `device` | **两条平行记录**，唯一联系是 `kobj.parent`（08 §2） |
| C | `irq_desc` 式的 1:1 | `inode->i_cdev` 是**缓存**，而且在一个 **union** 里（`include/linux/fs.h:684`） |
| D | `chrdevs[]` 和 `cdev_map` 是一回事 | **两张独立的表**（§3.2），`/proc/devices` 只反映前者 |
| E | `register_chrdev()` 是 `alloc_chrdev_region()` 的简写 | 它一次占 **256** 个 minor、不建节点、**拿不到 cdev**（01 §4） |

### §3.4 术语速查

| 名字 | 含义 | ⚠️ 易混点 |
|---|---|---|
| `dev_t` | 32 位设备号 | ⚠️ **不是 `struct device *`** |
| `struct cdev` | 字符设备对象 | ⚠️ **不是 `struct device`** |
| `char_device_struct` | `chrdevs[]` 的元素 | ⚠️ 名字里的 `device` 是误导 |
| `i_rdev` | inode 记的**设备号** | ⚠️ 和 `i_cdev`（指针）不同 |
| `i_devices` | inode 挂在 `cdev->list` 上的**节点** | ⚠️ 不是"设备列表" |
| `chrdev_*` | 设备号层的函数 | —— |
| `cdev_*` | 对象层的函数 | —— |
| `register_chrdev` vs `register_chrdev_region` | 差一个词，语义差很远 | 01 §4 |
| `def_chr_fops` | 所有字符设备的公共 open 入口 | —— |

---

## §4 数据结构速览

### §4.1 `struct cdev`（`include/linux/cdev.h:14-21`）—— 六个字段

| 字段 | 行号 | 要点 |
|---|---|---|
| `kobj` | `include/linux/cdev.h:15` | 引用计数 + parent 关系的载体 |
| `owner` | `include/linux/cdev.h:16` | 必填 `THIS_MODULE`；⚠️ 和 `fops.owner` **是两个** |
| `ops` | `include/linux/cdev.h:17` | 你的 `file_operations` |
| `list` | `include/linux/cdev.h:18` | **反向链表头**（串起引用它的 inode） |
| `dev` / `count` | `include/linux/cdev.h:19-20` | 覆盖的设备号区间 |

⚠️ **`cdev_init()` 里有 `memset`**（`fs/char_dev.c:653`）
⇒ **`owner` 必须设在它之后**（05 §1.2 · 08 §7）。

### §4.2 `struct char_device_struct`（`fs/char_dev.c:34-41`）

| 字段 | 行号 | 要点 |
|---|---|---|
| `next` / `major` / `baseminor` / `minorct` | `fs/char_dev.c:35-38` | 桶内链表 + 区间 |
| `name[64]` | `fs/char_dev.c:39` | 64 字节，`strlcpy` **静默截断**（`fs/char_dev.c:153`） |
| ⚠️`cdev` | `fs/char_dev.c:40` | 注释就是 **`/* will die */`**（03 §C2） |

### §4.3 `struct kobj_map` / `struct probe`（`drivers/base/map.c:19-30`）

| 字段 | 行号 | 要点 |
|---|---|---|
| `probes[255]` | `drivers/base/map.c:28` | 255 个桶 |
| `next` | `drivers/base/map.c:21` | **按 `range` 升序** ⇒ 最短的排最前（最长匹配） |
| `dev` / `range` | `drivers/base/map.c:22-23` | 区间 |
| `owner` | `drivers/base/map.c:24` | ⚠️`cdev_add()` 传的是 **NULL**（`fs/char_dev.c:489`） |
| `get` / `lock` / `data` | `drivers/base/map.c:25-27` | `exact_match` / `exact_lock` / **你的 `cdev *`** |

### §4.4 `struct inode` 的四个字段

| 字段 | 行号 | 要点 |
|---|---|---|
| `i_rdev` | `include/linux/fs.h:628` | 设备号 —— `chrdev_open()` 查表用它 |
| `i_cdev` | `include/linux/fs.h:684` | **在 union 里**（和 `i_pipe`/`i_link`/`i_dir_seq` 共享） |
| `i_devices` | `include/linux/fs.h:681` | 挂在 `cdev->list` 上的节点 |
| `i_mapping` | `include/linux/fs.h:609` | `cd_forget()` 会复位它（`fs/char_dev.c:431`） |

### §4.5 全局状态与锁

| 项 | 位置 | 保护 |
|---|---|---|
| `chrdevs[255]` | `fs/char_dev.c:41` | `chrdevs_lock` |
| `cdev_map` | `fs/char_dev.c:28` | **同一把 `chrdevs_lock`**（`fs/char_dev.c:669`） |
| `def_chr_fops` | `fs/char_dev.c:452` | 常量 |
| `chrdevs_lock`（**mutex**） | `fs/char_dev.c:30` | 两张表 |
| `cdev_lock`（**spinlock**） | `fs/char_dev.c:346` | `inode->i_cdev` + `cdev->list` |

**唯一的锁序约束**：`cdev_lock`（spinlock）**不能**嵌 `chrdevs_lock`（mutex）
⇒ `chrdev_open()` 必须"解锁 → 查表 → 重新加锁 → 复查"
（`fs/char_dev.c:385` → `:386` → `:390` → `:393`，注释在 `fs/char_dev.c:391-392`）。

---

## §5 机制速览

### §5.1 八条一句话

| # | 机制 | 一句话 | 详见 |
|---|---|---|---|
| 1 | **设备号分配** | 255 桶哈希 + 桶内按 major 升序链表 + 四条件判重叠 | 03 §A2 |
| 2 | **两个动态区** | 254→234 判"桶空"；511→384 要**遍历链表**（撞桶） | 03 §A2.1 |
| 3 | **区间查找** | `kobj_map` 哈希 + 按 `range` 升序 + **最长匹配** + 兜底项 | 03 §B4 · §2.9 |
| 4 | **open 路由** | 两级查找（`i_cdev` 缓存 → `cdev_map`）+ **一次 `replace_fops`** | 03 §A5 · 08 §3 |
| 5 | **模块自动加载** | 兜底项的 `base_probe()` ⇒ `request_module("char-major-N-M")` ⇒ `goto retry` | 03 §B5 · 04 §6 |
| 6 | **三层引用计数** | `cdev` kref · `cdev.owner` · `fops.owner` · parent kobject | 03 §B3 · 08 §7 |
| 7 | **两种 kobj_type** | `cdev_init` ⇒ default（**不 kfree**）；`cdev_alloc` ⇒ dynamic（**kfree**） | 03 §B2 |
| 8 | **反向链表 + purge** | `cdev` 销毁时清掉所有 inode 的缓存 | 03 §A8 · 08 §6 |

### §5.2 三条"文档没写但决定对错"的语义

见 §1.5 那张表。这里给**它们合起来的结论**：

```
   probe:  cdev_device_add() 成功或失败，用户态都可能已经打开了
   remove: cdev_device_del() 之后，你的 fops 还会被调用
   ⇒ 私有数据不能用 devm_kzalloc()，必须活到 device.release
```

| 正确的写法 | 位置 |
|---|---|
| 设 `dev.release` | `drivers/i2c/i2c-dev.c:670` |
| 用 `cdev_device_add()`（内部会 `cdev_set_parent()`） | `drivers/i2c/i2c-dev.c:676` |
| 拆除：`cdev_device_del()` + `put_device()` | `drivers/i2c/i2c-dev.c:94-95` |

### §5.3 三个"没有日志"的坑

| 现象 | 原因 | 判据 |
|---|---|---|
| 节点在、`open()` → `ENXIO` | **忘了 `cdev_add()`** | `open()` 就是判据（07 §4.3） |
| `open()` → `EACCES` | devtmpfs 默认 **0600** | `ls -l /dev/…`（06 §5.2 给改法） |
| 一个节点都不出现、dmesg 只报过一次 | kdevtmpfs 线程起不来 ⇒ 之后 `devtmpfs_create_node()` 在 `!thread` 处**返回 0（成功）** | `dmesg \| grep devtmpfs`（07 §2.3） |

### §5.4 三个"必须成对"的东西

| 成对 | 不成对的后果 |
|---|---|
| `cdev_init()` ↔ **紧跟着**设 `cdev.owner` | `memset` 会清掉（08 §7） |
| `cdev.owner` ↔ `fops.owner`（**两个都填**） | 保护残缺（08 §7） |
| `cdev_device_add()` ↔ `cdev_device_del()` + `put_device()` | 引用泄漏（05 §1.1） |

---

## §6 端到端调用路径

### §6.1 路径一：启动

```
   start_kernel() → vfs_caches_init() → chrdev_init()      fs/char_dev.c:667
     └─ cdev_map = kobj_map_init(base_probe, &chrdevs_lock) fs/char_dev.c:669
          ├─ kmalloc(kobj_map) + kzalloc(probe)             drivers/base/map.c:137-138
          ├─ base: dev=1, range=~0, get=base_probe          drivers/base/map.c:147-149
          └─ 同一个 base 放进全部 255 个桶               drivers/base/map.c:150-151
```

### §6.2 路径二：驱动 probe

```
   alloc_chrdev_region(&devt, 0, N, "mydev")               fs/char_dev.c:236
     └─ __register_chrdev_region(0, 0, N, "mydev")          fs/char_dev.c:97
          ├─ major >= 512 ⇒ -EINVAL（有日志）               fs/char_dev.c:103-107
          ├─ minor 越界 ⇒ -EINVAL（有日志）                 fs/char_dev.c:109-113
          ├─ kzalloc ⇒ -ENOMEM                              fs/char_dev.c:115-118
          ├─ mutex_lock(&chrdevs_lock)                       fs/char_dev.c:119
          ├─ find_dynamic_major()                            fs/char_dev.c:65
          │    ├─ 254→234 判桶空                             fs/char_dev.c:70-73
          │    ├─ 511→384 遍历链表                           fs/char_dev.c:75-83
          │    └─ 都满 ⇒ -EBUSY（有日志 :125）               fs/char_dev.c:85
          ├─ 四条件判重叠 ⇒ -EBUSY（无日志）              fs/char_dev.c:133-147
          ├─ strlcpy(name, …, 64)                            fs/char_dev.c:153
          └─ 按 major 升序插入                               fs/char_dev.c:155-161

   cdev_init(&my->cdev, &my_fops)                            fs/char_dev.c:651
     ├─ memset                                          fs/char_dev.c:653
     └─ kobject_init(…, &ktype_cdev_default)                 fs/char_dev.c:655
   my->cdev.owner = THIS_MODULE                              必须在这之后

   cdev_device_add(&my->cdev, &my->dev)                      fs/char_dev.c:537
     ├─ cdev_set_parent(cdev, &dev->kobj)                    fs/char_dev.c:508
     │    ├─ WARN_ON(!kobj->state_initialized)               fs/char_dev.c:510
     │    └─ p->kobj.parent = kobj                           fs/char_dev.c:511
     ├─ cdev_add(cdev, dev->devt, 1)                         fs/char_dev.c:479
     │    ├─ WARN_ON(dev == WHITEOUT_DEV) ⇒ -EBUSY           fs/char_dev.c:485-487
     │    ├─ kobj_map(cdev_map, …, exact_match, exact_lock, p) fs/char_dev.c:489-490
     │    └─ kobject_get(p->kobj.parent)                 fs/char_dev.c:494
     └─ device_add(dev)                                      fs/char_dev.c:549
          ├─ sysfs + /sys/dev/char/<M>:<m>                   drivers/base/core.c:3664
          ├─ uevent(KOBJ_ADD) ⇒ udev                          drivers/base/core.c:2566
          └─ devtmpfs_create_node(dev) ⇒ /dev 节点            drivers/base/devtmpfs.c:121
```

### §6.3 路径三：一次 `open()`

```
   open("/dev/mydev0")
     └─ VFS：inode（S_ISCHR，i_rdev）；f_op = def_chr_fops    fs/char_dev.c:452
          └─ chrdev_open(inode, filp)                          fs/char_dev.c:373
               ├─ spin_lock(&cdev_lock)                        fs/char_dev.c:380
               ├─ p = inode->i_cdev                            fs/char_dev.c:381
               ├─【未命中】
               │    ├─ spin_unlock（因为下面要拿 mutex）    fs/char_dev.c:385
               │    ├─ kobj_lookup(cdev_map, i_rdev, &idx)      fs/char_dev.c:386
               │    │    ├─ 遍历 probes[MAJOR%255]（range 升序） drivers/base/map.c:103
               │    │    ├─ 区间不含 ⇒ continue                 drivers/base/map.c:108-109
               │    │    ├─ range 不更短 ⇒ break                drivers/base/map.c:110-111
               │    │    ├─ exact_lock ⇒ cdev_get()             drivers/base/map.c:119 · fs/char_dev.c:463
               │    │    ├─ 解锁后调 exact_match            drivers/base/map.c:123-124
               │    │    └─ 返回 NULL ⇒ goto retry              drivers/base/map.c:129
               │    ├─ !kobj ⇒ -ENXIO                       fs/char_dev.c:387-388
               │    ├─ container_of                             fs/char_dev.c:389
               │    ├─ spin_lock + 复查                      fs/char_dev.c:390-393
               │    ├─ 填缓存 + 挂反向链表                       fs/char_dev.c:395-396
               │    └─ 抢输了 ⇒ cdev_put(new)                    fs/char_dev.c:403
               ├─【命中】cdev_get 失败 ⇒ -ENXIO                  fs/char_dev.c:400-401
               ├─ ret = -ENXIO                                  fs/char_dev.c:407
               ├─ fops = fops_get(p->ops)                   fs/char_dev.c:408
               ├─ replace_fops(filp, fops)                  fs/char_dev.c:412
               └─ filp->f_op->open(inode, filp)  ← 你的代码      fs/char_dev.c:414
                    失败 ⇒ out_cdev_put                         fs/char_dev.c:421-423
```

**`fs/char_dev.c:412` 之后，字符设备层退出这条路** ——
所有 read/write/ioctl 都是 VFS 直接调你的 fops（03 §A6 · 08 §3）。

### §6.4 路径四：`close()`

```
   close() → 最后一个引用消失 → __fput()                     fs/file_table.c
     ├─ ① file->f_op->release(inode, file)   ← 你的代码       fs/file_table.c:319-320
     ├─ ② cdev_put(inode->i_cdev)                         fs/file_table.c:321-324
     └─ ③ fops_put(file->f_op)                                fs/file_table.c:325
```

**顺序很重要：你的 `release` 先跑，然后才放 cdev 和模块引用**
⇒ `release` 里访问私有数据是安全的（03 §A7.1）。

inode 被回收时：`fs/inode.c:710-711` 调 `cd_forget()`（`fs/char_dev.c:426`），
它只清指针和摘链，**不 `kobject_put()`** —— 因为引用已按 `struct file` 配平了。

### §6.5 路径五：拆除

```
   cdev_device_del(cdev, dev)                                fs/char_dev.c:571
     ├─ device_del(dev)   先关外层门                        （04 §1 §2 §5 都在这里反操作）
     └─ cdev_del(cdev)                                        fs/char_dev.c:594
          ├─ cdev_unmap ⇒ kobj_unmap                          fs/char_dev.c:596 · drivers/base/map.c:68
          │    ⇒ 此后 open() 一律 -ENXIO
          └─ kobject_put(&p->kobj)                            fs/char_dev.c:597
               └─ 归零才走 release（有 fd 开着就不归零）
                    ├─ cdev_default_release  ⇒ purge + put(parent)        fs/char_dev.c:601
                    └─ cdev_dynamic_release ⇒ purge + kfree + put(parent) fs/char_dev.c:610
   unregister_chrdev_region(devt, N)                          fs/char_dev.c:311
```

---

## §7 子系统关系

### §7.1 依赖谁

```
                  ┌──────────────────┐
                  │  字符设备层        │
                  │  fs/char_dev.c    │
                  └────────┬─────────┘
        ┌──────────┬───────┼────────┬──────────┐
        ▼          ▼       ▼        ▼          ▼
      VFS       kobject  kmod     procfs   drivers/base/map.c
    （必需）    （必需） （可选）  （可选）    （必需）
```

| 依赖 | 强度 | 说明 |
|---|---|---|
| VFS | 必需 | `def_chr_fops` / `inode` / `replace_fops` / `__fput` |
| kobject | 必需 | `cdev` 的引用计数就是 kobject |
| `drivers/base/map.c` | 必需 | `cdev_map` 的实现 |
| kmod（`request_module`） | 可选 | 兜底项的模块自动加载 |
| procfs | 可选 | `/proc/devices` |

### §7.2 谁依赖它

**几乎所有暴露 `/dev` 节点的驱动**。四类使用者：

| 使用者 | 怎么用 |
|---|---|
| 直接用（现代写法） | `alloc_chrdev_region` + `cdev_device_add`（如 `drivers/i2c/i2c-dev.c`） |
| 通过 **misc** | `misc_register()` —— 04 §3 |
| 通过 **tty 层** | tty 自己是一个二级分配器（和 misc 同构） |
| 老式 | `register_chrdev()` |

### §7.3 和 `struct device` / class / devtmpfs 的分工

| 层 | 负责 |
|---|---|
| 字符设备层 | **设备号 → fops 的路由** |
| `struct device` + class | **身份、sysfs、uevent、节点名** |
| devtmpfs / udev | **在 `/dev` 里真的建出节点** |

⇒ **三层各自独立，`cdev_device_add()` 是唯一把前两层缝在一起的函数**（08 §2）。

### §7.4 ⚠️ 和块设备的关系：只共享设备号空间

| | 字符设备 | 块设备 |
|---|---|---|
| 设备号空间 | **共享 `devices.txt` 的编号习惯**，但是**两套独立的 major 空间** | 同 |
| 查找表 | `cdev_map` | **另一套**（`bdev` 那边） |
| sysfs 出口 | `/sys/dev/char/` | `/sys/dev/block/` |
| 本套覆盖 | 覆盖 | **不覆盖**（99 §2.7） |

判据：`Documentation/admin-guide/devices.txt:5` 和 `:20` 都是主设备号 1，
但一个是 `char`（内存设备）一个是 `block`（RAM disk）——
⇒ **同一个数字在两个空间里是两个不同的设备**（§2.4）。

### §7.5 对比：它在本系列里的位置

| | i2c/spi（总线型） | remoteproc / irq（框架型） | cdev |
|---|---|---|---|
| `bus_type` | 有 | 没有 | 没有 |
| 它是什么 | 设备的组织方式 | 硬件抽象层 | **VFS 和驱动之间的一层路由** |
| 核心行数 | 千行级 | 5k–15k | **961** |
| 难点 | 拓扑与协议 | 状态机与硬件语义 | **引用计数与生命周期** |

---

## §8 导读：我该看哪一篇

### §8.1 按问题找篇目

| 我想知道 | 去哪篇 |
|---|---|
| 12 个 API 的签名、11 个错误码点、`file_operations` 契约 | **01** |
| 结构体字段、五组关系、两张表、两把锁 | **02** |
| 注册/打开/关闭/拆除的完整时间线、三层引用、设计债 | **03** |
| devtmpfs / udev / misc / `/proc/devices` / sysfs / 自动加载 | **04** |
| 我要动手写驱动（现代 / misc / 多 minor / 老式） | **05** |
| 配置机制（**没有设备树**）：Kconfig / 命令行 / 设备号申请 / `devnode` 回调 / udev | **06** |
| **怎么确认 `cdev_add()` 做了没有** | **07** §4.3 |
| 「我理解不了 X」「为什么是这样」 | **08** |
| 这些结论怎么来的、哪些没验证 | **99** |

### §8.2 按角色推荐阅读顺序

| 角色 | 顺序 |
|---|---|
| **第一次接触** | 00 §2（背景）→ 08 §1 §3 → 02 §1.0 → 03 §A |
| **只写一个字符设备驱动** | 00 §3.1 → 05 §1 → 01 §5 → 07 §7 |
| **只要一个节点（用 misc）** | 08 §8 → 05 §2 → 04 §3 |
| **在排查"节点/打开"问题** | 07 §7 速查 → 07 §4.3 → 06 §8 |
| **在排查 use-after-free** | 08 §4 §5 → 03 §A8.1 · §B3 → 07 §1.3 |
| **要评审代码** | 03 Part C → 05 §6 那张错误速查表 |
| **要复核结论** | 99 |

### §8.3 只记六条的话

1. **设备号 / `cdev_add()` / `/dev` 节点是三件独立的事**（§3.1）。
   ⇒ 节点在但 `open()` 返回 `ENXIO` = 忘了 `cdev_add()`。
2. **`cdev_add()` 的效果哪里都看不到** ——
   `cdev_map` 没有任何出口，只能靠 `open()` 反推（07 §4.3）。
3. **`open()` 之后字符设备层就退出了** ——
   `replace_fops()`（`fs/char_dev.c:412`）一次替换，之后 VFS 直接调你的 fops（08 §3）。
4. **`cdev_del()` 不是安全点** ——
   之后你的 fops 还会被调用（`fs/char_dev.c:590-592` 的注释，08 §4）。
5. **私有数据不能用 `devm_kzalloc()`** ——
   必须活到 `device.release`；`cdev` 也**故意没有** `devm_*` 版本（05 §1.3 · 08 §5）。
6. **`cdev.owner` 和 `fops.owner` 是两个不同的计数，两个都要填**；
   而且 `cdev.owner` 必须设在 `cdev_init()` **之后**（它会 `memset`，08 §7）。

---

**取证与未验证项**：见 99 篇。
