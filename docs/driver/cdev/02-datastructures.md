---
title: 数据结构
---

# cdev 02：核心数据结构与它们的关系

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §1.0 先分清五组关系

> 这个子系统的核心只有 961 行（01 §0.1），
> 它的**全部难度都在"哪几件事是独立的"**上。本节先否掉五个错误理解。

### §1.0.A 「注册设备号」、「`cdev_add()`」、「建 `/dev` 节点」是三件独立的事

**错误理解**：「`alloc_chrdev_region()` 之后 `/dev` 下就会出现节点」。

**三件事的完整对照**：

| # | 动作 | API | 效果能在哪看到 | 少了它会怎样 |
|---|---|---|---|---|
| ① | **占住设备号区间** | `alloc_chrdev_region()` | `/proc/devices` | 别人可能占用同一段设备号 |
| ② | **让设备号"活"起来** | `cdev_add()` | **哪里都看不到**（只在内核内部的 `cdev_map` 里） | `open()` 返回 **`ENXIO`** |
| ③ | **在 `/dev` 建节点** | `class_create()` + `device_create()`（或 `misc_register()`） | `/dev/` 和 `/sys/class/` | **没有节点可以 open** |

⚠️ **三件事各自的失败模式完全不同，而且都容易误诊**：

| 症状 | 缺了哪件 | 判据 |
|---|---|---|
| `/dev` 下没有节点 | 缺 ③ | `ls /sys/class/<你的 class>/` 也是空的 |
| 节点在，`open()` 返回 `ENXIO` | 缺 ② | `cat /proc/devices` 里**有**你的主设备号（说明 ① 做了） |
| `alloc_chrdev_region()` 返回 `-EBUSY` | ① 冲突 | `cat /proc/devices` 看是谁 |

**② 的"哪里都看不到"是本子系统最大的调试困难**：
`cdev_map`（`fs/char_dev.c:28`）**没有任何 procfs / sysfs / debugfs 出口**（07 §4.3）。
⇒ 只能靠 `open()` 是否返回 `ENXIO` 反推。

### §1.0.B `struct cdev` 和 `struct device` 不是父子，也不是同一个东西

**错误理解**：「`cdev` 是 `device` 的一种」或「`cdev` 里有个 `struct device`」。

| 断言 | 对不对 | 依据 |
|---|---|---|
| `struct cdev` 里有 `struct device` | **错** —— 它只有 `struct kobject`（6 个字段一共） | `include/linux/cdev.h:14-21` |
| `struct cdev` 挂在某条总线上 | **错** —— 它连 `bus_type` 都不碰 | 同上 |
| 两者有 kobject 层面的关系 | **有，但要你自己建** | `cdev_set_parent()`（`fs/char_dev.c:508`）把 `cdev->kobj.parent` 设成 `dev->kobj` |
| 两者是"同一个逻辑设备的两个面" | **对** | `cdev_device_add()`（`fs/char_dev.c:537`）就是把这两面缝在一起的函数 |

**正确的说法**：

```
   struct cdev    ── 回答"这段设备号由谁的 fops 负责" ── 服务对象：VFS 的 open()
   struct device  ── 回答"这是什么设备、在 sysfs 哪里、节点叫什么" ── 服务对象：用户态和 udev
```

⇒ 两者**唯一的连接点**就是 `cdev_set_parent()` 那一行赋值
（`fs/char_dev.c:510` 的 `p->kobj.parent = kobj`），
目的只有一个：**保证"还有 fd 打开"时 `struct device` 不被释放**（01 §3.3）。

**"为什么不会发生"的代码依据**：`struct cdev` 只有六个字段
（`include/linux/cdev.h:15-20`）：`kobj` / `owner` / `ops` / `list` / `dev` / `count`。
⇒ **它连 `name` 都没有** —— `/dev` 下的名字来自 `struct device`，不来自 `cdev`。

### §1.0.C `inode->i_cdev` 是一个缓存，而且住在一个 union 里

**错误理解**：「每个字符设备 inode 都有一个 `i_cdev` 指针，一直有效」。

| 断言 | 实际 |
|---|---|
| `i_cdev` 一开始就有值 | **错** —— 第一次 `open()` 时才填（`fs/char_dev.c:395`） |
| `i_cdev` 是 `struct inode` 的一个普通字段 | **错** —— 它在一个 **union** 里，和 `i_pipe` / `i_link` / `i_dir_seq` **共享同一块内存** |
| 一个 `cdev` 只被一个 inode 引用 | **错** —— `cdev->list` 是一条**反向链表**，串起所有引用它的 inode |

**union 的代码依据**（`include/linux/fs.h:682-687`）：

| 字段 | 什么类型的 inode 用它 |
|---|---|
| `i_pipe` | 管道 |
| `i_cdev` | **字符设备**（`include/linux/fs.h:684`） |
| `i_link` | 符号链接 |
| `i_dir_seq` | 目录 |

⇒ **一个 inode 只能是这四种之一** —— 这是"为什么不会发生"的机器可证形式：
不可能有一个 inode 同时是字符设备和管道。

**反向链表的代码依据**：

| 元素 | 位置 |
|---|---|
| `cdev->list`（链表头） | `include/linux/cdev.h:18` |
| `inode->i_devices`（链表节点） | `include/linux/fs.h:681` |
| 第一次 open 时挂进去 | `fs/char_dev.c:396` |
| inode 销毁时摘掉 | `fs/char_dev.c:426` 的 `cd_forget()` |
| cdev 销毁时**把所有 inode 的缓存清掉** | `fs/char_dev.c:435` 的 `cdev_purge()` |

**`cdev_purge()`（`fs/char_dev.c:435`）是这条链表存在的全部理由**：
`cdev_del()` 时必须把每个还指着它的 inode 的 `i_cdev` 置 NULL
（`fs/char_dev.c:441-442`），否则下次 `open()` 会用到已释放的 `cdev`。

### §1.0.D `chrdevs[]` 和 `cdev_map` 是两张完全独立的表

**错误理解**：「设备号注册表和 cdev 查找表是一回事」。

| | `chrdevs[]` | `cdev_map` |
|---|---|---|
| 定义 | `fs/char_dev.c:34-41` | `fs/char_dev.c:28` |
| 类型 | 255 个桶的哈希表，元素是 `struct char_device_struct` | `struct kobj_map`（`drivers/base/map.c:19`），也是 255 个桶 |
| 存什么 | **"这段设备号被谁占了"**（名字 + 区间） | **"这段设备号对应哪个 kobject"** |
| 谁往里写 | `alloc_chrdev_region()` / `register_chrdev_region()` | **`cdev_add()`** |
| 谁读 | `/proc/devices`（`chrdev_show()`，`fs/char_dev.c:51`） | **`chrdev_open()`**（`fs/char_dev.c:386`） |
| 保护 | `chrdevs_lock`（mutex，`fs/char_dev.c:30`） | **同一把 `chrdevs_lock`**（`fs/char_dev.c:669` 传进去的） |
| 少了它 | 设备号冲突不会被发现 | **`open()` 返回 `ENXIO`** |

**两张表的独立性就是 §1.0.A 里 ① 和 ② 独立的实现层面原因。**

⚠️ **一个容易误判的点**：`cat /proc/devices` 里看到你的主设备号，
**只说明 ① 做了**，完全不说明 ② 做了 —— 因为 `/proc/devices` 读的是 `chrdevs[]`。

两张表**共用同一把锁**（`fs/char_dev.c:669` 把 `&chrdevs_lock` 传给 `kobj_map_init()`），
但**不共用数据** —— 这个设计让人容易以为它们是一回事。

### §1.0.E `register_chrdev()` 不是 `alloc_chrdev_region()` 的简写

| | `register_chrdev(major, name, fops)` | `alloc_chrdev_region()` + `cdev_add()` |
|---|---|---|
| 占多少 minor | **写死 256 个**（`include/linux/fs.h:2916`） | 你说几个就几个 |
| 建 cdev 吗 | 建（`cdev_alloc()`，`fs/char_dev.c:280`） | 你自己建 |
| 你能拿到那个 cdev 吗 | **拿不到** —— 存在 `char_device_struct.cdev` 里（`fs/char_dev.c:40`），那个字段的注释是 `/* will die */` | 是你的 |
| 返回值 | `major` 为 0 时返回**分配到的主设备号（正数）** | 0 或负错误码 |
| 建 `/dev` 节点吗 | **不建** | 不建 |

**`/* will die */`（`fs/char_dev.c:40`）是上游对这套 API 的态度**（01 §4.1）。

---

## §2 核心抽象与消费者的关系

### §2.1 一张图看清四方分工

```
   ┌── 用户态 ────────────────────────────────────────────────────┐
   │  open("/dev/mydev")                                          │
   └───────────────────────────┬──────────────────────────────────┘
                               │
   ┌───────────────────────────▼──────────────────────────────────┐
   │  VFS                                                          │
   │  · 按路径找到 inode                                            │
   │  · 发现 S_ISCHR ⇒ f_op = def_chr_fops                         │
   │  · 调 def_chr_fops.open = chrdev_open                         │
   └───────────────────────────┬──────────────────────────────────┘
                               │
   ┌───────────────────────────▼──────────────────────────────────┐
   │  字符设备层（fs/char_dev.c）                                   │
   │                                                               │
   │   chrdev_open()                                               │
   │     ├─ inode->i_cdev 缓存命中？                                │
   │     └─ 未命中 ⇒ kobj_lookup(cdev_map, inode->i_rdev)          │
   │            └─ 命中 ⇒ container_of ⇒ struct cdev               │
   │     └─ replace_fops(filp, cdev->ops)   换成你的 fops       │
   │     └─ 调你的 fops->open()                                     │
   └───────────────────────────┬──────────────────────────────────┘
                               │
   ┌───────────────────────────▼──────────────────────────────────┐
   │  你的驱动                                                      │
   │  · struct cdev（cdev_add 登记进 cdev_map）                     │
   │  · struct file_operations                                     │
   │  · struct device（device_create ⇒ /dev 节点 + /sys 目录）      │
   └───────────────────────────────────────────────────────────────┘
```

| 关键位置 | 行号 |
|---|---|
| `def_chr_fops` | `fs/char_dev.c:452-455` |
| `chrdev_open()` | `fs/char_dev.c:373` |
| 缓存读 | `fs/char_dev.c:381` |
| `kobj_lookup()` 调用 | `fs/char_dev.c:386` |
| `container_of` | `fs/char_dev.c:389` |
| `replace_fops()` | `fs/char_dev.c:412` |
| 调你的 `open` | `fs/char_dev.c:414` |
| `kobj_lookup()` 实现 | `drivers/base/map.c:95` |

**`replace_fops()`（`fs/char_dev.c:412`）是整个子系统的枢纽**：
它之前 `filp->f_op` 是 `def_chr_fops`，它之后是你的 fops。
⇒ **一次性替换，之后所有 read/write/ioctl 都直接走你的表，不再经过字符设备层。**

⇒ **这就是为什么字符设备层只有 961 行**：它只管 `open` 这一次，之后就退出了。

### §2.2 ⚠️ 它不在设备模型里，也不在总线上

| 判据 | 结果 |
|---|---|
| 有 `struct bus_type` 吗 | **没有** |
| `struct cdev` 是 `struct device` 吗 | **不是**（§1.0.B） |
| 它挂在 `sysfs` 的哪里 | **哪里都不挂** —— `cdev->kobj` 没有 `kset`，除非你 `cdev_set_parent()` |

**所以 `struct cdev` 是内核里少见的"只用 kobject 做引用计数、不进 sysfs"的对象**。
⇒ 这也是它没有任何 sysfs 出口的原因（§1.0.A 的调试困难）。

---

## §3 核心操作数据流

### §3.1 注册路径（驱动 probe 时）

```
   你的 probe
     │
     ├─ alloc_chrdev_region(&devt, 0, N, "mydev")
     │    └─ __register_chrdev_region(0, 0, N, "mydev")
     │         ├─ 检查 major < 512、minor 区间合法
     │         ├─ kzalloc(struct char_device_struct)
     │         ├─ find_dynamic_major()          从 254 往下扫
     │         ├─ 遍历 chrdevs[major % 255] 查区间重叠 ⇒ -EBUSY
     │         └─ 按 major 升序插入链表
     │
     ├─ cdev_init(&my->cdev, &my_fops)
     │
     ├─ my->cdev.owner = THIS_MODULE          必填
     │
     ├─ device_initialize(&my->dev)
     │  my->dev.devt = devt
     │  my->dev.class = my_class
     │
     └─ cdev_device_add(&my->cdev, &my->dev)
          ├─ cdev_set_parent(&my->cdev, &my->dev.kobj)
          ├─ cdev_add(&my->cdev, devt, 1)
          │    └─ kobj_map(cdev_map, devt, 1, NULL, exact_match, exact_lock, cdev)
          └─ device_add(&my->dev)
               ├─ sysfs 目录
               ├─ uevent ⇒ udev
               └─ devtmpfs_create_node(dev)   /dev 节点在这里出现
```

| 关键位置 | 行号 |
|---|---|
| `alloc_chrdev_region()` | `fs/char_dev.c:236` |
| `__register_chrdev_region()` | `fs/char_dev.c:97` |
| `find_dynamic_major()` | `fs/char_dev.c:65` |
| 区间重叠检查 | `fs/char_dev.c:133-147` |
| 按 major 升序插入 | `fs/char_dev.c:155-161` |
| `cdev_init()` | `fs/char_dev.c:651` |
| `cdev_device_add()` | `fs/char_dev.c:537` |
| `cdev_set_parent()` | `fs/char_dev.c:508` |
| `cdev_add()` | `fs/char_dev.c:479` |
| `kobj_map()` 调用 | `fs/char_dev.c:489-490` |
| `kobj_map()` 实现 | `drivers/base/map.c:32` |
| `devtmpfs_create_node()` | `drivers/base/devtmpfs.c:121` |

### §3.2 打开路径（用户态 `open()` 时）

见 §2.1 那张图。**关键在于两级查找 + 一次替换**：

| 级 | 查什么 | 命中率 |
|---|---|---|
| ① `inode->i_cdev` | 缓存 | **第二次及以后的 open 都命中** |
| ② `cdev_map` | 全局表 | 第一次 open |

⇒ 所以 `kobj_lookup()` 的性能只影响"每个 inode 的第一次 open"。

### §3.3 `kobj_lookup()` 的"最长匹配"逻辑

`drivers/base/map.c:95`，它比想象的复杂：

| 步骤 | 动作 | 位置 |
|---|---|---|
| ① | `best = ~0UL`（当前最优的区间长度） | `drivers/base/map.c:99` |
| ② | `retry:` + `mutex_lock` | `drivers/base/map.c:101-102` |
| ③ | 遍历 `probes[MAJOR(dev) % 255]` 链 | `drivers/base/map.c:103` |
| ④ | 设备号不在这一项的区间内 ⇒ 跳过 | `drivers/base/map.c:108-109` |
| ⑤ | **这一项的区间不比已有的最优更短 ⇒ `break`** | `drivers/base/map.c:110-111` |
| ⑥ | `try_module_get(p->owner)` 失败 ⇒ 跳过 | `drivers/base/map.c:112-113` |
| ⑦ | 记下 `best`、算出 `*index = dev - p->dev` | `drivers/base/map.c:117-118` |
| ⑧ | 调 `p->lock(dev, data)`（就是 `exact_lock`）；失败 ⇒ 还回模块引用后跳过 | `drivers/base/map.c:119-122` |
| ⑨ | **解锁后**调 `probe(dev, index, data)`（就是 `exact_match`） | `drivers/base/map.c:123-124` |
| ⑩ | `module_put(owner)`；返回非 NULL 就成功 | `drivers/base/map.c:126-128` |
| ⑪ | 返回 NULL ⇒ **`goto retry` 从头再来** | `drivers/base/map.c:129` |

**⑤ 那句 `break` 说明链表是按 `range` 升序排的**：
`kobj_map()` 插入时就维持这个顺序（`drivers/base/map.c:58-59` 的 `while (*s && (*s)->range < range)`）。
⇒ **区间越短的排越前** ⇒ 找到第一个匹配的就是最"精确"的那个。

**这就是"注册一个覆盖全部设备号的兜底项"能工作的原因**：
`kobj_map_init()`（`drivers/base/map.c:135`）建了一个
`dev = 1, range = ~0` 的 `base` 项（`drivers/base/map.c:147-148`），
放在**每个桶**里（`drivers/base/map.c:150-151`）。
⇒ 它的 `range` 最大，永远排在最后 ⇒ **只有别人都不匹配时才轮到它**。

那个兜底项的 `get` 是 `base_probe()`（`fs/char_dev.c:659`），
它的作用是**尝试自动加载模块**（`request_module`）——
这就是"打开一个没驱动的设备节点会触发模块自动加载"的实现。

### §3.4 `exact_match` 与 `exact_lock`：两个三行函数

| 函数 | 位置 | 干什么 |
|---|---|---|
| `exact_match(dev, part, data)` | `fs/char_dev.c:457` | 直接 `return &p->kobj` |
| `exact_lock(dev, data)` | `fs/char_dev.c:463` | `return cdev_get(p) ? 0 : -1` |

**`exact_lock` 是引用计数的关键**：
`kobj_lookup()` 在 ⑧ 那一步调它，成功就意味着**已经拿到了 `cdev` 的 kobject 引用
+ 模块引用**（`cdev_get()`，`fs/char_dev.c:348`）。
⇒ 所以 `chrdev_open()` 拿到 kobj 之后**不需要再 get 一次**，
但失败路径要 `cdev_put()`（`fs/char_dev.c:422`）。

---

## §4 命名约定速查

| 名字 | 含义 | ⚠️ 易混点 |
|---|---|---|
| `dev_t` | 32 位设备号（12 位 major + 20 位 minor） | ⚠️ **不是 `struct device *`** |
| `struct cdev` | 字符设备对象 | ⚠️ **不是 `struct device`**（§1.0.B） |
| `cdev_map` | 设备号 → kobject 的全局表 | ⚠️ 和 `chrdevs[]` 不是一回事（§1.0.D） |
| `chrdevs[]` | 设备号占用登记表 | 同上 |
| `char_device_struct` | `chrdevs[]` 的元素类型 | ⚠️ **名字里有 `device` 但和 `struct device` 无关** |
| `chrdev_*` | 设备号层的函数（`chrdev_open` / `chrdev_show`） | —— |
| `cdev_*` | `cdev` 对象层的函数 | —— |
| `register_chrdev*` | **老式一步到位** | ⚠️ 和 `register_chrdev_region()` 差一个词，语义差很远（§1.0.E） |
| `__register_chrdev*` | 带 minor 区间的版本 | —— |
| `i_rdev` | inode 记录的**设备号** | ⚠️ 和 `i_dev`（不存在）、`i_cdev`（指针）都不同 |
| `i_cdev` | inode 的 `cdev` **缓存** | ⚠️ 在一个 union 里（§1.0.C） |
| `i_devices` | inode 挂在 `cdev->list` 上的**节点** | ⚠️ 名字像"设备列表"，其实是"我这个 inode 在某个 cdev 的链上的位置" |
| `fops` | `struct file_operations *` | —— |
| `def_chr_fops` | 所有字符设备的**公共 open 入口** | —— |
| `MISC_DYNAMIC_MINOR` | misc 设备的"帮我分配 minor" | 值是 **255**（`include/linux/miscdevice.h:74`） |

---

## §5 结构体逐个看

### §5.1 `struct cdev`：只有六个字段

`include/linux/cdev.h:14-21`，带 `__randomize_layout`。

| 字段 | 行号 | 要点 |
|---|---|---|
| `kobj` | `include/linux/cdev.h:15` | **引用计数 + parent 关系的载体**（§1.0.B） |
| `owner` | `include/linux/cdev.h:16` | **`THIS_MODULE`** —— `cdev_get()` 用它 `try_module_get`（`fs/char_dev.c:353`） |
| `ops` | `include/linux/cdev.h:17` | 你的 `file_operations` |
| `list` | `include/linux/cdev.h:18` | **反向链表头**，串起所有引用它的 inode（§1.0.C） |
| `dev` | `include/linux/cdev.h:19` | 第一个设备号 |
| `count` | `include/linux/cdev.h:20` | 覆盖几个 minor |

⚠️ **`owner` 有两个来源**：
`cdev_init()` **不设它**（`fs/char_dev.c:651`）——
你要么自己赋 `THIS_MODULE`，要么让 `__register_chrdev()` 从 `fops->owner` 拷过来
（`fs/char_dev.c:284`）。
⇒ **用 `cdev_init()` 的话，`cdev.owner` 和 `fops.owner` 都要填。**

### §5.2 `struct char_device_struct`：`chrdevs[]` 的元素

`fs/char_dev.c:34-41`，**它是 `static` 的，只在这个文件里可见**。

| 字段 | 行号 | 要点 |
|---|---|---|
| `next` | `fs/char_dev.c:35` | 同一个哈希桶的链 |
| `major` / `baseminor` / `minorct` | `fs/char_dev.c:36-38` | 区间 |
| `name[64]` | `fs/char_dev.c:39`（结构体内） | 64 字节，`strlcpy` 截断（`fs/char_dev.c:153`） |
| ⚠️`cdev` | `fs/char_dev.c:40` | **注释就是 `/* will die */`** —— 只给老式 `register_chrdev()` 用（§1.0.E） |

⚠️ **链表按 `major` 升序排**（`fs/char_dev.c:134-138` 的两个 `continue`/`break`），
同一个 `major` 内按 minor 区间判重叠（`fs/char_dev.c:140-146`）；
插入点由 `prev` 决定（`fs/char_dev.c:155-161`）。

### §5.3 `struct kobj_map` 与 `struct probe`

`drivers/base/map.c:19-30`：

| 字段 | 行号 | 要点 |
|---|---|---|
| `probes[255]` | `drivers/base/map.c:28` | **255 个桶**（和 `chrdevs[]` 一样） |
| `lock` | `drivers/base/map.c:29` | 指向外部的 mutex（cdev 传的是 `chrdevs_lock`） |

`struct probe`（内嵌，`drivers/base/map.c:20-28`）：

| 字段 | 行号 | 要点 |
|---|---|---|
| `next` | `drivers/base/map.c:21` | **按 `range` 升序**的链（§3.3） |
| `dev` / `range` | `drivers/base/map.c:22-23` | 区间 |
| `owner` | `drivers/base/map.c:24` | `cdev_add()` 传的是 **NULL**（`fs/char_dev.c:489`） |
| `get` | `drivers/base/map.c:25` | `exact_match` |
| `lock` | `drivers/base/map.c:26` | `exact_lock` |
| `data` | `drivers/base/map.c:27` | 就是你的 `struct cdev *` |

⚠️ **`cdev_add()` 传 `module = NULL`**（`fs/char_dev.c:489`）——
所以 `kobj_lookup()` 里的 `try_module_get(p->owner)`（`drivers/base/map.c:112`）
对 NULL 是直接成功。
⇒ **模块引用实际是由 `exact_lock` → `cdev_get()` → `try_module_get(p->owner)`
（`fs/char_dev.c:353`，用的是 `cdev->owner`）拿的**，
不是 `probe->owner`。这两个 `owner` 是不同的东西。

### §5.4 `struct inode` 的四个相关字段

| 字段 | 行号 | 要点 |
|---|---|---|
| `i_rdev` | `include/linux/fs.h:628` | **设备号** —— `chrdev_open()` 查表用的就是它（`fs/char_dev.c:386`） |
| `i_mapping` | `include/linux/fs.h:609` | `cd_forget()` 会把它复位成 `&i_data`（`fs/char_dev.c:431`） |
| `i_data` | `include/linux/fs.h:680` | 内嵌的 address_space |
| `i_devices` | `include/linux/fs.h:681` | 挂在 `cdev->list` 上的节点（§1.0.C） |
| `i_cdev` | `include/linux/fs.h:684` | **union 里**，缓存（§1.0.C） |

### §5.5 `struct miscdevice`

`include/linux/miscdevice.h:79`。只讲你要填的字段（01 §7）。

---

## §6 全局变量与锁

### §6.1 全部只有三个全局状态

| 变量 | 位置 | 类型 | 保护 |
|---|---|---|---|
| `chrdevs[255]` | `fs/char_dev.c:41` | `struct char_device_struct *` 数组 | `chrdevs_lock` |
| `cdev_map` | `fs/char_dev.c:28` | `struct kobj_map *` | **同一把 `chrdevs_lock`**（`fs/char_dev.c:669`） |
| `def_chr_fops` | `fs/char_dev.c:452` | 常量 | 无需保护 |

```bash
grep -n 'static struct kobj_map \*cdev_map\|} \*chrdevs\[\|^const struct file_operations def_chr_fops' fs/char_dev.c
```

### §6.2 两把锁，粒度差别很大

| 锁 | 位置 | 类型 | 保护什么 | 能睡吗 |
|---|---|---|---|---|
| `chrdevs_lock` | `fs/char_dev.c:30` | **mutex** | `chrdevs[]` + `cdev_map` | 能 |
| `cdev_lock` | `fs/char_dev.c:346` | **spinlock** | `inode->i_cdev` 缓存 + `cdev->list` 链表 | **不能** |

**两把锁保护的是两个完全不同的东西**：

| | `chrdevs_lock` | `cdev_lock` |
|---|---|---|
| 什么时候持 | 注册/注销设备号、`cdev_add`/`cdev_del`、`kobj_lookup` | **只在 `chrdev_open` / `cd_forget` / `cdev_purge` 里** |
| 持有时长 | 可能很长（`kobj_lookup` 里还要调回调） | 极短 |
| 为什么类型不同 | 注册路径都在进程上下文 | `cd_forget()` 可能在 inode 回收路径上被调，那里不能睡 |

⚠️ **`kobj_lookup()` 会在持 `chrdevs_lock` 的情况下调 `p->lock()`（`exact_lock`）**
（`drivers/base/map.c:119`），
然后**解锁再调 `probe()`（`exact_match`）**（`drivers/base/map.c:123-124`）。
⇒ 所以 `exact_lock` 里**不能睡**（它在 mutex 保护下，但更重要的是它不能重入 `chrdevs_lock`），
而 `exact_match` 是在锁外调的。
本子系统的这两个实现都只有三行，都不睡（§3.4）。

### §6.3 `chrdev_open()` 里的"解锁 - 查表 - 加锁 - 复查"

```
   spin_lock(&cdev_lock)
     p = inode->i_cdev
     if (!p) {
         spin_unlock(&cdev_lock)        先解锁，因为 kobj_lookup 要拿 mutex
         kobj = kobj_lookup(...)
         spin_lock(&cdev_lock)
         p = inode->i_cdev              复查：别人可能抢先填了
         ...
     }
   spin_unlock(&cdev_lock)
```

| 元素 | 位置 |
|---|---|
| 先解锁 | `fs/char_dev.c:385` |
| `kobj_lookup()` | `fs/char_dev.c:386` |
| 重新加锁 | `fs/char_dev.c:390` |
| 复查的注释 | `fs/char_dev.c:391-392`（原文："Check i_cdev again in case somebody beat us to it while we dropped the lock."） |
| 复查 | `fs/char_dev.c:393` |
| 抢输了就把自己查到的那个放掉 | `fs/char_dev.c:403` 的 `cdev_put(new)` |

**锁序**：`cdev_lock`（spinlock）**不能**嵌在 `chrdevs_lock`（mutex）里，
所以必须先放掉 spinlock 才能去调 `kobj_lookup()`。
⇒ **这是本子系统唯一一处"解锁 - 重查"模式**，也是它唯一的并发难点。

---

## §7 结构关系总图

```
   全局（fs/char_dev.c）
   ┌──────────────────────────────────────────────────────────────┐
   │  chrdevs[255] ──▶ char_device_struct ──next──▶ …             │
   │      （谁占了哪段设备号；/proc/devices 读它）                   │
   │                                                              │
   │  cdev_map ──▶ struct kobj_map                                │
   │                 probes[255] ──▶ probe ──next──▶ probe        │
   │                                  · dev / range               │
   │                                  · get  = exact_match        │
   │                                  · lock = exact_lock         │
   │                                  · data ──┐                  │
   │              （每桶末尾都有一个 range=~0 的兜底项 base_probe） │
   └───────────────────────────────────────────┼──────────────────┘
                                               │
   一个字符设备                                  ▼
   ┌─── struct cdev ─────────────────────────────────────────────┐
   │  kobj ──parent──▶ struct device 的 kobj   cdev_set_parent   │
   │  owner  = THIS_MODULE                                        │
   │  ops   ──▶ struct file_operations（你写的）                    │
   │  dev / count                                                 │
   │  list ──┬──▶ inode A 的 i_devices                            │
   │         └──▶ inode B 的 i_devices        反向链表           │
   └──────────────────────────────────────────────────────────────┘
              ▲                    ▲
              │ i_cdev（缓存）      │ i_cdev
   ┌──────────┴─────────┐  ┌───────┴──────────┐
   │ struct inode A     │  │ struct inode B    │
   │  i_rdev = 设备号    │  │  i_rdev = 设备号   │
   │  i_cdev（union 里） │  │                   │
   └────────────────────┘  └───────────────────┘

   同一个逻辑设备的另一面
   ┌─── struct device ───────────────────────────────────────────┐
   │  devt  = 同一个设备号     cdev_device_add 用它            │
   │  class ──▶ /sys/class/<name>/                               │
   │  kobj  ──▶ /sys/devices/…                                   │
   │  ⇒ device_add() ⇒ uevent + devtmpfs_create_node ⇒ /dev 节点  │
   └──────────────────────────────────────────────────────────────┘
```

**图里最该盯住的四条边**：
1. `probe->data` 指向 `struct cdev` —— 这是 `cdev_map` 到 `cdev` 的唯一路径（§5.3）。
2. `cdev->list` ↔ `inode->i_devices` 是**反向**链表 —— 为了 `cdev_purge()`（§1.0.C）。
3. `cdev->kobj.parent` → `device->kobj` —— **唯一**连接 cdev 和 device 的边（§1.0.B）。
4. `device->devt` 和 `cdev->dev` 是**同一个设备号的两份拷贝** —— `cdev_device_add()` 保证它们一致。

---

**下一篇**：03 篇按时间线剖析注册、打开、读写、拆除，以及两把锁和并发。
