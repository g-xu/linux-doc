---
title: 内部实现
---

# IIO 核心内部实现

> 对应内核版本：Linux 6.1.158（`f6e38ae624cf`），目标架构 arm64。
> 前置阅读：`01-api.md`（对外 API 与代码地图）、`02-datastructures.md`（结构体与关系）。

## §0 导航

### §0.1 本篇只承诺一条主轴：时间

正文分三个区，章节号自带区标记，引用时一眼能看出「在哪条轴上」：

| 区 | 组织方式 | 内容 |
| --- | --- | --- |
| **Part A** | 严格按时间先后 | 从 `module_init` 到 `module_exit`，一个 IIO 设备一生中核心代码走过的每一段 |
| **Part B** | 按机制横切 | 锁、引用计数、模式状态机、错误回滚——它们贯穿 Part A 的多个阶段，不属于任何单点 |
| **Part C** | 评价 | 这套实现哪里设计得好、源码自己承认了哪些债、哪些地方是实际缺陷 |

Part A 的次序不是凭直觉排的，判据是**「谁调用谁」**：每一节标题后带函数的完整行号区间，节内用「调用者 → 被调用者」的实际调用行把次序钉住。凡是次序无法用调用关系确定的（比如驱动 probe 里 `iio_triggered_buffer_setup()` 和 `iio_device_register()` 谁先谁后），文中会显式说明它由驱动决定而不是核心决定。

### §0.2 分层图

```
 用户态
   │  read()/write()/poll()/ioctl()      sysfs read/write
   ▼                                            ▼
┌──────────────────────────────────────────────────────────┐
│ 第 1 层 · 接口层                                          │
│  industrialio-buffer.c   字符设备读写/poll 实现 + buffer/ 属性 │
│  industrialio-event.c    事件 fd + events/ 属性            │
│  industrialio-core.c     fileops 装配 + 通道属性合成 + ioctl 分发 │
└──────────────────────────────────────────────────────────┘
   │ 调 info->read_raw / access->read / setup_ops->*
   ▼
┌──────────────────────────────────────────────────────────┐
│ 第 2 层 · 核心状态机                                      │
│  模式（DIRECT / BUFFER_*）· scan_mask 协商 · demux 表     │
│  industrialio-buffer.c: iio_verify_update / *_buffers     │
└──────────────────────────────────────────────────────────┘
   │                              │
   │ 采样触发                      │ 数据落地
   ▼                              ▼
┌───────────────────────┐  ┌───────────────────────────────┐
│ 第 3a 层 · 触发器      │  │ 第 3b 层 · 缓冲区后端          │
│ industrialio-trigger.c│  │ buffer/kfifo_buf.c 等          │
│ 虚拟 irq_chip + subirq│  │ access_funcs vtable            │
└───────────────────────┘  └───────────────────────────────┘
   │                              │
   ▼                              ▼
┌──────────────────────────────────────────────────────────┐
│ 第 4 层 · 设备驱动                                        │
│  iio_info 回调 + trigger_handler（在中断线程里跑）         │
└──────────────────────────────────────────────────────────┘
```

四层里只有第 4 层由驱动作者写，第 1~3 层全在 `drivers/iio/` 核心。第 3a 和 3b 是**并列**关系而不是上下关系：触发器决定「什么时候采」，缓冲区后端决定「采到的往哪放」，二者在第 2 层汇合。

### §0.3 生命周期时间线图

竖轴是时间，方框里是本篇对应的章节号。虚线框表示「可选」——不用缓冲区的驱动整条右侧分支都不走。

```
时间 ↓
  ┌─ A1  iio_init()                     模块加载，全进程一次
  │
  │   ── 以下每个设备一遍 ──
  ├─ A2  iio_device_alloc()             驱动 probe 里
  │      ┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐
  │      ╎ A4  iio_triggered_buffer_setup_ext()   ╎ 可选
  │      └╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘
  ├─ A3  iio_device_register()          sysfs / chrdev 出现
  │
  │      ── 以下由用户态或另一个驱动驱动 ──
  │      ┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐
  │      ╎ A5  iio_trigger_register()  触发器提供方 ╎
  │      ╎ A6  echo name > current_trigger         ╎
  │      ╎ A7  echo 1 > buffer0/enable             ╎
  │      ╎ A8  运行期：一轮采样                     ╎
  │      ╎ A9  echo 0 > buffer0/enable             ╎
  │      ╎ A10 echo "" > current_trigger           ╎
  │      └╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘
  ├─ A11 iio_device_unregister()        驱动 remove 里
  │      A12 iio_triggered_buffer_cleanup()
  ├─ A13 iio_device_free()（devm 时自动）
  │
  └─ A14 iio_exit()                     模块卸载
```

A2 和 A4 的先后由**驱动**决定而不是核心：`iio_triggered_buffer_setup_ext()` 只要求 `indio_dev` 已经 alloc 过（它要写 `indio_dev->buffer`、`->pollfunc`、`->modes`），必须在 `iio_device_register()` 之前——因为注册时要按 `attached_buffers_cnt` 决定装哪套 fileops（`drivers/iio/industrialio-core.c:1983-1986`）。这条约束在 A3 里给出行号依据。

### §0.4 哪些内容不在本篇

| 你想找的 | 去哪篇 |
| --- | --- |
| 某个 API 的签名、参数、返回值语义 | `01-api.md` |
| `iio_dev` / `iio_chan_spec` 逐字段含义、结构体关系图 | `02-datastructures.md` |
| scan buffer 的字节布局怎么算出来的 | `02-datastructures.md` §3.1 |
| kfifo / DMA / cb / hw-consumer 四个后端各自怎么实现 | `04-extensions.md` |
| 内核内消费者（`iio_read_channel_raw` 那一套）的内部实现 | `04-extensions.md` |
| 我要写驱动，代码骨架长什么样 | `05-usage.md` |
| DTS 属性由哪一行代码消费 | `06-devicetree.md` |
| 出错了怎么定位、有哪些 tracepoint | `07-debug.md` |
| 为什么要这样设计（大白话） | `08-feynman.md` |
| 某个数字、某条断言的取证过程 | `99-evidence.md` |

---

# Part A · 时间线

## A1 模块初始化 `drivers/iio/industrialio-core.c:345-370`

整个 IIO 子系统的入口只做三件事：

```c
345:static int __init iio_init(void)
...
350:	ret  = bus_register(&iio_bus_type);
...
356:	ret = alloc_chrdev_region(&iio_devt, 0, IIO_DEV_MAX, "iio");
...
362:	iio_debugfs_dentry = debugfs_create_dir("iio", NULL);
```

三件事对应三个全局资源，在 `02-datastructures.md` §6.2 的全局变量表里都有登记：

| 行号 | 动作 | 失败处理 |
| --- | --- | --- |
| `:350` | 注册 `iio_bus_type` | `pr_err` + 直接返回，整个子系统不可用 |
| `:356` | 申请一整块字符设备主设备号区间，宽度 `IIO_DEV_MAX` | goto `error_unregister_bus_type` 回滚总线注册 |
| `:362` | 建 `/sys/kernel/debug/iio` 目录 | **不检查返回值**，debugfs 失败不影响子系统可用 |

`alloc_chrdev_region` 一次申请 `IIO_DEV_MAX` 个次设备号，意味着**同时存在的 IIO 设备数上限在模块初始化时就定死了**，而不是按需扩张。设备的次设备号来自 `iio_dev_opaque->id`（`drivers/iio/industrialio-core.c:1989`），也就是 ida 分配出来的编号——ida 本身不知道 `IIO_DEV_MAX` 这个上限，两者的一致性没有代码检查（见 Part C）。

卸载路径 `drivers/iio/industrialio-core.c:372-378` 是对称的，唯一值得注意的是 `:374` 的 `if (iio_devt)` ——`iio_devt` 为 0 表示 `alloc_chrdev_region` 那步就失败了，但那种情况下 `iio_init` 已经返回错误、`iio_exit` 根本不会被调用；这个判断是防御性的冗余。

## A2 设备构造 `drivers/iio/industrialio-core.c:1661-1712`

`iio_device_alloc()` 是驱动 probe 的第一步。它一次 `kzalloc` 分配出两样东西：核心结构和驱动私有区。

```c
1667:	alloc_size = sizeof(struct iio_dev_opaque);
1668:	if (sizeof_priv) {
1669:		alloc_size = ALIGN(alloc_size, IIO_DMA_MINALIGN);
1670:		alloc_size += sizeof_priv;
1671:	}
```

注意分配的是 `struct iio_dev_opaque` 而不是 `struct iio_dev`——公开结构是私有结构的第一个成员，`:1677` 的 `indio_dev = &iio_dev_opaque->indio_dev;` 只是取地址。为什么必须是这个方向、驱动自己 `kzalloc(sizeof(struct iio_dev))` 会怎样，在 `02-datastructures.md` §1.0 关系一里展开过。

按行号顺序，这个函数依次建立了七组状态：

| 行号 | 动作 | 说明 |
| --- | --- | --- |
| `:1673` | `kzalloc(alloc_size)` | 一次分配，全零 |
| `:1679-1681` | 算出 `indio_dev->priv` | 对齐到 `IIO_DMA_MINALIGN`，理由见 `02-datastructures.md` §2.1 |
| `:1683-1686` | 填 `dev.parent` / `dev.type` / `dev.bus`，`device_initialize` | 这里是 IIO 设备挂上 `iio_bus_type` 的那一行（`:1685`） |
| `:1687-1688` | `mutex_init` 两把锁 | `mlock` 与 `info_exist_lock`，分工见 Part B1 |
| `:1691-1697` | `ida_alloc(&iio_ida)` | 失败时 `pr_err`，注释 `:1693` 解释为什么不能用 `dev_err`——名字还没有 |
| `:1699-1703` | `dev_set_name(..., "iio:device%d", id)` | 失败时回滚 ida 再 `kfree` |
| `:1705-1709` | 初始化两个链表头 + 注册 lockdep key | `mlock_key` 让每个设备的 `mlock` 有独立的 lockdep 类 |

`:1689` 的 `INIT_LIST_HEAD(&iio_dev_opaque->channel_attr_list)` 和 `:1705-1706` 的两个 `INIT_LIST_HEAD` 被 ida 分配隔开了，这是纯粹的历史排列，没有语义。

三处失败路径的回滚粒度不一样，值得对照：`:1674` 分配失败直接返回 NULL；`:1692` ida 失败 `kfree` 已分配的内存；`:1699` 命名失败先 `ida_free` 再 `kfree`。**都不走 `put_device`**——因为 `device_initialize()` 已经在 `:1686` 调过了，严格说这三条路径都跳过了 `device` 的引用计数机制，直接 `kfree` 了一个已 initialize 的 `struct device`。这在 6.1 里是安全的（`device_initialize` 不分配额外资源，只填字段和初始化 kobject），但它和 `iio_device_free()` 走 `put_device` 的做法（`:1719-1723`）不一致。

释放侧只有两行：

```c
1719:void iio_device_free(struct iio_dev *dev)
1720:{
1721:	if (dev)
1722:		put_device(&dev->dev);
1723:}
```

真正的释放动作在 `iio_dev_release()` 里（由 `iio_device_type.release` 指向），也就是说 `iio_device_free()` 只是**减一次引用**。如果还有人持有引用（用户态打开着字符设备、内核内消费者拿着 `iio_channel`），内存不会在这一行被释放。这是 A13 的内容。

## A3 设备注册 `drivers/iio/industrialio-core.c:1926-2011`

这是核心里最长的一条顺序流程，也是 sysfs 目录树成型的地方。整段共 86 行，可以切成五段看。

### A3.1 前置校验与 fwnode 继承 `:1932-1953`

```c
1932:	if (!indio_dev->info)
1933:		return -EINVAL;
```

**这是核心唯一一次检查 `info` 的非空。** `info` 里的十六个回调，核心一个都不检查（`02-datastructures.md` §2.4）——所以「回调没实现」的后果是运行期 NULL 解引用，而「`info` 整个没填」的后果是注册期干净失败。

`:1935` 记下 `driver_module`，它后面用于模块引用计数（Part B2）。

```c
1938:	if (dev_fwnode(&indio_dev->dev))
1939:		fwnode = dev_fwnode(&indio_dev->dev);
1940:	/* The default dummy IIO device has no parent */
1941:	else if (indio_dev->dev.parent)
1942:		fwnode = dev_fwnode(indio_dev->dev.parent);
1943:	device_set_node(&indio_dev->dev, fwnode);
```

IIO 设备自己没有 DT 节点——它的 fwnode 是从父设备（通常是 platform 或 i2c 设备）**继承**来的。`:1945` 紧接着从这个 fwnode 读 `label` 属性。这两点是 `06-devicetree.md` 的入口：IIO 设备能读到 DT 属性，靠的是这三行的继承，不是自己被 of_platform 扫出来。

两个校验函数：`iio_check_unique_scan_index()`（`drivers/iio/industrialio-core.c:1883-1904`）确保 `scan_index` 不重复——这是 scan buffer 布局算法的前提（`02-datastructures.md` §3.1）；`iio_check_extended_name()`（`drivers/iio/industrialio-core.c:1906-1922`，调用点 `:1951`）检查的是**`read_label` 回调与 `extend_name` 字段互斥**：驱动实现了 `read_label`（`:1910`）就不许再有任何通道带 `extend_name`（`:1913-1918`），否则 `dev_err` 报 `"Cannot use labels and extend_name at the same time"` 并返回 `-EINVAL`。两者都会影响属性名的合成，同时用会撞车。

### A3.2 缓冲区 sysfs 先于设备 sysfs `:1957-1969`

```c
1957:	ret = iio_buffers_alloc_sysfs_and_mask(indio_dev);
...
1964:	ret = iio_device_register_sysfs(indio_dev);
```

次序容易看反：**缓冲区属性组先建，通道属性组后建**。原因在 `iio_device_register_sysfs()` 里——它要把所有属性收进一个数组，而 `iio_dev_opaque->groups[]` 是共享的收集点，缓冲区那边（`drivers/iio/industrialio-buffer.c:1739-1785`）已经先往里塞了 `buffer0/`、`scan_elements/` 等组。

### A3.3 通道属性合成 `drivers/iio/industrialio-core.c:1534-1620`

这是「一个 `iio_chan_spec` 数组变成一堆 sysfs 文件」的地方。算法是**两遍**：先数个数，再分配数组填指针。

```
第一遍：数
  :1542-1546   数 info->attrs 里已有几个（attrcount_orig）
  :1552-1564   逐通道调 iio_device_add_channel_sysfs()，返回值累加进 attrcount
               ├─ 副作用：属性节点挂进 iio_dev_opaque->channel_attr_list
               └─ :1557-1558 如果有 IIO_TIMESTAMP 通道，记下要加 clk 属性
  :1569-1574   name / label / clk 各占一个名额
第二遍：填
  :1576-1579   kcalloc(attrcount + 1)      ← +1 是数组末尾的 NULL
  :1585-1594   memcpy 驱动原有的 attrs，并转移 is_visible 和 bin_attrs
  :1597-1598   遍历 channel_attr_list 依次填入
  :1599-1604   name / label / clk 补在最后
  :1606-1607   iio_device_register_sysfs_group() 把这个组登记进 groups[]
```

两遍之间数量必须严格一致，否则要么数组越界要么末尾少一个 NULL。这个一致性**没有断言保护**——`attrn` 在 `:1595` 从 `attrcount_orig` 开始递增，最终值应当等于 `attrcount`，代码里没有比较。

单个通道展开成几个属性由 `iio_device_add_channel_sysfs()`（`drivers/iio/industrialio-core.c:1299-1396`）决定，它对四档 info 掩码各调一次 `iio_device_add_info_mask_type()`（`:1231-1259`），后者按位遍历掩码、每个置位的位生成一个属性。属性名的合成规则在 `01-api.md` 里讲过，这里只钉住数量关系：**一个通道产生的属性数 = 四档掩码里置位的总数（× 有 `_available` 版的再加一份）**，返回值就是这个数。

失败回滚两级：`error_free_chan_attrs`（`:1613`）释放刚 kcalloc 的数组并**置 NULL**（`:1615`，避免二次释放），`error_clear_attrs`（`:1616`）释放整条 `channel_attr_list`。

### A3.4 事件集 `drivers/iio/industrialio-event.c:515-583`

结构和 A3.3 几乎一模一样——两遍、`attrcount_orig`、`memcpy`、遍历链表填指针——但有三处关键差异：

| 差异点 | 通道属性组（core.c） | 事件组（event.c） |
| --- | --- | --- |
| 有没有可能整个跳过 | 不会 | 会：`:523-525` 既没有 `event_attrs` 又没有动态事件时直接返回 0 |
| 组名 | NULL（属性直接落在设备目录下） | `:549` `iio_event_group_name`，即 `events/` 子目录 |
| 额外注册 | 无 | `:570-572` 注册 ioctl handler（`IIO_GET_EVENT_FD_IOCTL`） |

`:531` 是全树**唯一**给 `iio_dev_opaque->event_interface` 赋非空值的地方（另外三处 `:581`、`:613` 都是置 NULL，`:596` 是读）。这一点在 Part C3 里会推出一个后果。

### A3.5 字符设备与 `device_add` `:1976-1998`

最后一段决定这个设备在用户态长什么样：

```c
1976:	if (indio_dev->modes & INDIO_ALL_TRIGGERED_MODES)
1977:		iio_device_register_trigger_consumer(indio_dev);
1978:
1979:	if ((indio_dev->modes & INDIO_ALL_BUFFER_MODES) &&
1980:		indio_dev->setup_ops == NULL)
1981:		indio_dev->setup_ops = &noop_ring_setup_ops;
1982:
1983:	if (iio_dev_opaque->attached_buffers_cnt)
1984:		cdev_init(&iio_dev_opaque->chrdev, &iio_buffer_fileops);
1985:	else if (iio_dev_opaque->event_interface)
1986:		cdev_init(&iio_dev_opaque->chrdev, &iio_event_fileops);
```

四件事，每一件都有陷阱：

1. **`:1976-1977`** 只有声明了触发模式的设备才会长出 `current_trigger` 属性。驱动如果忘了 `indio_dev->modes |= INDIO_BUFFER_TRIGGERED`，症状是 sysfs 里没有 `current_trigger`，而不是报错。
2. **`:1979-1981`** 核心替驱动兜底填了 `noop_ring_setup_ops`（定义在 `drivers/iio/industrialio-core.c:1924`）。所以 A7 里 `indio_dev->setup_ops->preenable` 这类解引用是安全的——**前提是设备声明了缓冲模式**。声明了缓冲模式但 `attached_buffers_cnt` 为 0，或者反过来，都会打破这个前提。
3. **`:1983-1986`** 一个 IIO 设备只有**一个**字符设备节点，fileops 二选一，缓冲区优先。这意味着**同时有缓冲区和事件的设备，它的 `/dev/iio:deviceN` 走的是 buffer fileops**；事件 fd 必须通过 ioctl 另外取（`drivers/iio/industrialio-event.c:192-224`），不能直接 `read()` 设备节点拿事件。`02-datastructures.md` §1.0 关系四的对比表讲的就是这两条通路的区别。
4. **`:1994`** `indio_dev->dev.groups = iio_dev_opaque->groups;` 必须在 `cdev_device_add` 之前——注释 `:1993` 写着 "assign device groups now; they should be all registered now"。这一行是前面 A3.2/A3.3/A3.4 三次 `iio_device_register_sysfs_group()` 收集结果的交付点。

`:1996` 的 `cdev_device_add()` 是**可见性的分界线**：这一行之前 sysfs 里什么都没有，这一行返回之后用户态可以立刻 `open()` 和读属性。所以属性组必须全部准备好才能走到这里。

失败回滚是标准的倒序 goto 链，四个标签一一对应前面四步：

```
error_unreg_eventset   :2002  ← cdev_device_add 失败
error_free_sysfs       :2004  ← register_eventset 失败
error_buffer_free_sysfs :2006 ← register_sysfs 失败
error_unreg_debugfs    :2008  ← buffers_alloc_sysfs 失败
```

注意 `:1976-1994` 这一段（trigger consumer 注册、setup_ops 兜底、cdev_init）**没有对应的回滚标签**——它们都是纯赋值或者由 `iio_device_unregister_sysfs` 间接清理的，没有独立分配。

## A4 缓冲区装配 `drivers/iio/buffer/industrialio-triggered-buffer.c:39-98`

这一节是可选分支的起点。绝大多数带缓冲区的驱动不直接碰 `iio_device_attach_buffer()`，而是调这个便利函数一次性把「kfifo 后端 + poll func + 模式标志」三样装好。

```c
56:	if (indio_dev->buffer)
57:		return -EADDRINUSE;
```

开头这个检查配着 `:49-55` 的五行注释，讲的是一个真实约束：`iio_triggered_buffer_cleanup()`（`:105-109`）无脑释放 `indio_dev->buffer`，所以本函数必须是 `iio_device_attach_buffer()` 的**第一个**调用者，否则 cleanup 会去释放别人分配的缓冲区。用 `-EADDRINUSE` 表达「这个位置已被占用」——这个错误码在 IIO 里只此一处（`01-api.md` 的错误码表里有登记）。

装配的四样东西：

| 行号 | 装什么 | 备注 |
| --- | --- | --- |
| `:59` | `iio_kfifo_allocate()` | 后端写死是 kfifo，想用 DMA 后端就不能用这个便利函数 |
| `:65-71` | `iio_alloc_pollfunc()`，flag 写死 `IRQF_ONESHOT` | 名字格式 `"%s_consumer%d"`，即 `<设备名>_consumer<id>` |
| `:78` | `indio_dev->setup_ops = setup_ops` | 可以传 NULL，A3.5 的 `:1979-1981` 会兜底 |
| `:81` | `indio_dev->modes \|= INDIO_BUFFER_TRIGGERED` | **这一行是 A3.5 里 `current_trigger` 属性出现的前提** |

`iio_alloc_pollfunc()`（`drivers/iio/industrialio-trigger.c:357-384`）用的是 `kmalloc` 而不是 `kzalloc`（`:368`），只显式填了 `name`/`h`/`thread`/`type`/`indio_dev` 五个字段（`:372`、`:378-381`）。**`pf->irq` 和 `pf->timestamp` 出生时是垃圾值**，前者由 A6 的 attach 填，后者由 A8 的顶半部填。这个细节在 Part C3 里和另一个缺陷合起来会有后果。

`:83-84` 把 `direction` 和 `attrs` 填进 buffer，然后 `:86` 才 `iio_device_attach_buffer()`——顺序是「先配好再挂上」，因为 attach 之后这个 buffer 就在核心的 `attached_buffers[]` 数组里了。

## A5 触发器注册 `drivers/iio/industrialio-trigger.c:67-100`

触发器是**独立注册**的对象，和任何 IIO 设备都没有绑定关系。注册四步：

```c
71:	trig_info->id = ida_alloc(&iio_trigger_ida, GFP_KERNEL);
...
76:	dev_set_name(&trig_info->dev, "trigger%d", trig_info->id);
...
78:	ret = device_add(&trig_info->dev);
...
83:	mutex_lock(&iio_trigger_list_lock);
84:	if (__iio_trigger_find_by_name(trig_info->name)) {
85:		pr_err("Duplicate trigger name '%s'\n", trig_info->name);
86:		ret = -EEXIST;
87:		goto error_device_del;
88:	}
89:	list_add_tail(&trig_info->list, &iio_trigger_list);
```

值得注意的是**重名检查排在 `device_add` 之后**（`:78` vs `:84`）。这意味着一个重名的触发器会先在 sysfs 里出现、再被删掉（`:96` 的 `device_del`）。这是一个短暂但真实存在的可见窗口。为什么不能反过来：`__iio_trigger_find_by_name()` 比对的是 `trig_info->name`，而这个字段在 `iio_trigger_alloc()` 时就填好了，理论上可以先查——排成这样纯粹是历史。

`trigger%d` 是 `dev_set_name` 给的 **sysfs 目录名**，而 `trig_info->name` 是**用户态写进 `current_trigger` 的名字**，两者不同。前者形如 `trigger0`，后者形如 `sysfstrig1` 或 `<设备名>-dev0`。混淆这两个名字是 IIO 最常见的使用错误之一。

注销路径 `:103-112` 是反的，但少一步——**它不 `set_trigger_state(false)`，也不检查有没有消费者还挂着**。`:110` 有一行注释 `/* Possible issue in here */`，是源码作者自己留下的记号（Part C2 会汇总这类记号）。

## A6 绑定触发器 `drivers/iio/industrialio-trigger.c:431-490`

用户态 `echo <trigger name> > /sys/bus/iio/devices/iio:deviceN/trigger/current_trigger` 落到 `current_trigger_store()`。

### A6.1 三道门

```c
441:	mutex_lock(&indio_dev->mlock);
442:	if (iio_dev_opaque->currentmode == INDIO_BUFFER_TRIGGERED) {
443:		mutex_unlock(&indio_dev->mlock);
444:		return -EBUSY;
445:	}
446:	if (iio_dev_opaque->trig_readonly) {
447:		mutex_unlock(&indio_dev->mlock);
448:		return -EPERM;
449:	}
450:	mutex_unlock(&indio_dev->mlock);
```

`-EBUSY` = 缓冲区正在跑，不许换触发器；`-EPERM` = 驱动调过 `iio_trigger_set_immutable()` 把触发器钉死了。

**注意 `:450` 在真正干活之前就把 `mlock` 放掉了。** 后面 `:470` 的 `indio_dev->trig = trig;` 是在**无锁**状态下写的。这不是笔误——`iio_trigger_attach_poll_func()` 里会 `request_threaded_irq()`，那是不能持 `mlock` 干的事。代价是「检查 currentmode」和「改 trig」之间有窗口。这个窗口的实际影响在 Part B1 里分析。

### A6.2 两级验证

```c
458:	if (trig && indio_dev->info->validate_trigger) {
459:		ret = indio_dev->info->validate_trigger(indio_dev, trig);
...
464:	if (trig && trig->ops && trig->ops->validate_device) {
465:		ret = trig->ops->validate_device(trig, indio_dev);
```

设备一侧和触发器一侧**各有一次否决权**，两个方向都是可选回调。这是 IIO 里唯一的双向协商点：比如某些设备只接受自己的数据就绪中断当触发器，就在 `validate_trigger` 里拒绝别的；某些触发器只服务同一个父设备，就在 `validate_device` 里比对 `dev.parent`。

`:452` 的 `iio_trigger_acquire_by_name()` 允许返回 NULL（写空字符串就是解绑），所以后面每处都带 `if (trig && ...)`。`:453-456` 处理「写的就是当前那个」——直接 put 掉新拿的引用返回成功，幂等。

### A6.3 换与挂

```c
470:	indio_dev->trig = trig;
471:
472:	if (oldtrig) {
473:		if (indio_dev->modes & INDIO_EVENT_TRIGGERED)
474:			iio_trigger_detach_poll_func(oldtrig,
475:						     indio_dev->pollfunc_event);
476:		iio_trigger_put(oldtrig);
477:	}
478:	if (indio_dev->trig) {
479:		if (indio_dev->modes & INDIO_EVENT_TRIGGERED)
480:			iio_trigger_attach_poll_func(indio_dev->trig,
481:						     indio_dev->pollfunc_event);
482:	}
```

这里**只处理 `pollfunc_event`，不处理 `pollfunc`**。数据采样用的 `pollfunc` 在 A7 使能缓冲区时才挂（`drivers/iio/industrialio-buffer.c:1107-1112`）。两个 poll func 的挂载时机不同：

| poll func | 什么时候挂 | 条件 |
| --- | --- | --- |
| `pollfunc_event` | 绑定触发器时（本节 `:480`） | `modes & INDIO_EVENT_TRIGGERED` |
| `pollfunc` | 使能缓冲区时（A7） | `currentmode == INDIO_BUFFER_TRIGGERED` |

所以「绑了触发器但没使能缓冲区」的设备，触发器已经在跑了（如果它有 event 模式），只是没人往缓冲区推数据。

`:480` 的 attach **返回值被丢弃**。结合 Part C3 里 attach 本身「失败也可能返回 0」的问题，这一条通路上的错误是彻底静默的。

### A6.4 subirq 分配 `drivers/iio/industrialio-trigger.c:274-324`

`iio_trigger_attach_poll_func()` 是「虚拟 irq_chip」这个设计的落地点。五步：

```
:283  __module_get(driver_module)        提前锁住消费者驱动的模块
:286  pf->irq = iio_trigger_get_irq()   从 trig->pool 位图里抢一个 subirq 号
:294  request_threaded_irq(pf->irq, pf->h, pf->thread, pf->type, pf->name, pf)
:301  若这是第一个消费者（notinuse），调 trig->ops->set_trigger_state(trig, true)
:312  若 indio_dev->dev.parent == trig->dev.parent，标记 attached_own_device
```

`notinuse` 在 `:278-279` 就算好了（`bitmap_empty` 于 `iio_trigger_get_irq()` **之前**求值），所以它表达的是「进函数时池子是空的」，也就是「我是第一个」。这个次序是必须的：`get_irq` 之后位图就不空了。

`iio_trigger_get_irq()`（`:245-258`）从位图里找一个空位，加上 `subirq_base` 得到真正的 irq 号；`iio_trigger_put_irq()`（`:260-265`）反向清位。池子大小是 `CONFIG_IIO_CONSUMERS_PER_TRIGGER`（默认 2），所以**一个触发器默认只能带两个消费者**，第三个会在 `:287-291` 拿到 `pr_err` 并失败——错误消息里直接写了这个 Kconfig 的名字，是 `07-debug.md` 错误消息表里的一条。

五个标签的倒序 unwind 链：`out_free_irq:317` → `out_put_irq:319` → `out_put_module:321`。三个标签是**穿透**的（没有 return 隔开），所以从 `out_free_irq` 进来会依次执行三步。

解绑侧 `:326-346` 镜像对称，唯一的不对称是判据：attach 用 `bitmap_empty`（我是第一个），detach 用 `:330-331` 的 `bitmap_weight(...) == 1`（我是最后一个）。两者都在动位图之前求值。

## A7 使能缓冲区 `drivers/iio/industrialio-buffer.c:1296-1325`

`echo 1 > buffer0/enable` 是整个 IIO 里最复杂的一条路径，四层嵌套（框内行号全部属于 `drivers/iio/industrialio-buffer.c`）：

```
enable_store()                     :1296-1325   持 mlock
  └─ __iio_update_buffers()        :1193-1245
       ├─ iio_verify_update()      :822-927     只算不改
       ├─ iio_buffer_request_update()
       ├─ iio_disable_buffers()    :1145-1191   先全停
       ├─ iio_buffer_activate()    :731-738     改链表
       └─ iio_enable_buffers()     :1059-1143   再全开
```

### A7.1 外层：幂等与加锁 `:1296-1325`

```c
1309:	mutex_lock(&indio_dev->mlock);
1310:
1311:	/* Find out if it is in the list */
1312:	inlist = iio_buffer_is_active(buffer);
1313:	/* Already in desired state */
1314:	if (inlist == requested_state)
1315:		goto done;
```

整条路径**全程持 `mlock`**（`:1309` 到 `:1323`），这是 IIO 里 `mlock` 保护范围最大的一处。`:1314` 的幂等检查让重复 `echo 1` 不报错。`:1324` 的 `return (ret < 0) ? ret : len;` 把成功统一映射成「写入了 len 字节」。

### A7.2 `iio_verify_update()` —— 只算，不改 `:822-927`

这个函数的全部职责是把一个 `struct iio_device_config` 算出来，**一个字节的设备状态都不改**。这个「先算后改」的切分是整条路径能安全回滚的根本原因。

三件事：

**（一）模式协商 `:852-886`。** 从 `indio_dev->modes` 出发，和每一个参与的缓冲区的 `access->modes` 求交集：

```c
852:	modes = indio_dev->modes;
853:
854:	list_for_each_entry(buffer, &iio_dev_opaque->buffer_list, buffer_list) {
855:		if (buffer == remove_buffer)
856:			continue;
857:		modes &= buffer->access->modes;
```

然后按**固定优先级**从交集里挑一个：`INDIO_BUFFER_TRIGGERED`（且有 trig）> `INDIO_BUFFER_HARDWARE` > `INDIO_BUFFER_SOFTWARE`。全落空则 `-EINVAL`，`:883-884` 会给一条 `dev_dbg`「Buffer not started: no trigger」——这是「忘了绑触发器」最常见的症状，但它在 `dev_dbg` 里，默认不可见（`07-debug.md`）。

`:870-878` 的 HARDWARE 分支额外做两件事：拒绝第二个缓冲区（`:875-876`），以及置 `strict_scanmask = true`。

**（二）scan mask 合并 `:889-919`。** 所有参与缓冲区的 `scan_mask` 求并集（`:898`、`:904`）得到 `compound_mask`；`scan_timestamp` 求逻辑或（`:900`、`:906`）——**任何一个缓冲区要时间戳，整个设备就都带时间戳**。

如果驱动声明了 `available_scan_masks`（硬件只支持若干固定组合），`:910-913` 用 `iio_scan_mask_match()` 把并集映射到一个硬件支持的组合上；`strict_scanmask` 决定是否允许「多采一些」。映射不到就 `-EINVAL`（`:915-916`）。

`:914` 的 `bitmap_free(compound_mask)` 只在走 `available_scan_masks` 分支时执行，另一支（`:918`）把 `compound_mask` 直接交给 `config->scan_mask` ——**所有权转移**。这就是为什么 `__iio_update_buffers` 的失败路径要在 `err_free_config`（`:1242`）里 `iio_free_scan_mask()`。

**（三）算字节数 `:921-922`。** `iio_compute_scan_bytes()` 的布局算法在 `02-datastructures.md` §3.1 里逐字节展开过，这里不重复。

`:848-850` 有一条捷径：只删不加、而且删的是唯一那个缓冲区时，直接返回 0 不算任何东西——反正马上全停。

### A7.3 `__iio_update_buffers()` —— 先全停再全开 `:1193-1245`

```c
1212:	ret = iio_disable_buffers(indio_dev);
...
1216:	if (remove_buffer)
1217:		iio_buffer_deactivate(remove_buffer);
1218:	if (insert_buffer)
1219:		iio_buffer_activate(indio_dev, insert_buffer);
...
1225:	ret = iio_enable_buffers(indio_dev, &new_config);
```

**加一个缓冲区要把已经在跑的全部停掉、改完链表再全部重开。** 这是 IIO 多缓冲区支持的核心代价：新开一个缓冲区会打断已有缓冲区的采样。原因是 scan mask 和 demux 表是全设备共享的，改了就得所有人重新对齐。

`:1222-1223` 的 `list_empty` 检查是「关掉最后一个缓冲区」的正常出口：停完、摘完、发现没人了，直接返回 0 不再 enable。

失败路径 `err_deactivate_all`（`:1231`）带一段 9 行注释（`:1232-1239`），态度很直白：配置已经验证过了，走到这一步出错基本是设备 IO 故障，**没有好的恢复策略**，那就把所有缓冲区都摘掉、把设备留在一个 sane 状态，指望它以后能活过来、用户态再试。这是全篇最诚实的一段注释。

### A7.4 `iio_enable_buffers()` —— 七步与七级回滚 `:1059-1143`

```
:1066-1069  把 config 里算好的四个值抄进 indio_dev / opaque（含 currentmode）
:1071       iio_update_demux()                      ← 返回值被丢弃，见 Part C3
:1074-1081  setup_ops->preenable()                  失败 → err_undo_config
:1083-1093  info->update_scan_mode()                失败 → err_run_postdisable
:1095-1097  info->hwfifo_set_watermark()            无返回值检查
:1099-1105  逐 buffer 调 iio_buffer_enable()        失败 → err_disable_buffers（记下 tmp）
:1107-1112  currentmode==TRIGGERED 时挂 pollfunc    失败 → err_disable_buffers
:1114-1121  setup_ops->postenable()                 失败 → err_detach_pollfunc
```

`:1069` 的 `iio_dev_opaque->currentmode = config->mode;` 是**模式状态机真正翻转的那一行**（Part B3）。它在所有可能失败的步骤**之前**，所以每条失败路径都必须经过 `err_undo_config`（`:1138-1140`）把它改回 `INDIO_DIRECT_MODE`——而四个标签是穿透式排列的，从任何一个进来都会走到最后。

`:1130-1134` 的部分回滚值得单独看：

```c
1131:	buffer = list_prepare_entry(tmp, &iio_dev_opaque->buffer_list, buffer_list);
1132:	list_for_each_entry_continue_reverse(buffer, &iio_dev_opaque->buffer_list,
1133:					     buffer_list)
1134:		iio_buffer_disable(buffer, indio_dev);
```

`tmp` 是 `:1102` 记下的**失败的那一个**。`list_prepare_entry` + `continue_reverse` 的组合表示「从 tmp 往前倒着走」，也就是只 disable 那些已经成功 enable 的，不碰失败的那个。`tmp` 初值是 NULL（`:1063`），此时 `list_prepare_entry` 退化成从表头开始——对应「pollfunc 挂载失败」这条路径，此时所有缓冲区都成功了，要全部倒回。

### A7.5 关闭侧 `iio_disable_buffers()` `:1145-1191`

和 enable 的结构对称，但错误处理策略**相反**，`:1156-1161` 的注释说明了原因：

> If things go wrong at some step in disable we still need to continue to perform the other steps, otherwise we leave the device in a inconsistent state. We return the error code for the first error we encountered.

所以每一步都是 `ret2 = ...; if (ret2 && !ret) ret = ret2;` 的形式（`:1165-1166`、`:1176-1177`、`:1182-1183`）——**记住第一个错误，但一步都不跳过**。enable 用 goto 链尽早退出，disable 用「记错误继续走」，这是同一个函数对内的两种态度，理由都写在注释里。

`:1153-1154` 的 `list_empty` 早退让 disable 可以无条件调用，这是 A11 里 `iio_disable_all_buffers()` 能安全兜底的基础。

## A8 运行期：一轮采样 `drivers/iio/industrialio-trigger.c:195-209`

前面七节都是配置，这一节是数据真正流动的地方。一轮采样跨三个上下文。

### A8.1 触发：硬中断上下文 `:195-209`

```c
195:void iio_trigger_poll(struct iio_trigger *trig)
196:{
197:	int i;
198:
199:	if (!atomic_read(&trig->use_count)) {
200:		atomic_set(&trig->use_count, CONFIG_IIO_CONSUMERS_PER_TRIGGER);
201:
202:		for (i = 0; i < CONFIG_IIO_CONSUMERS_PER_TRIGGER; i++) {
203:			if (trig->subirqs[i].enabled)
204:				generic_handle_irq(trig->subirq_base + i);
205:			else
206:				iio_trigger_notify_done_atomic(trig);
207:		}
208:	}
209:}
```

三个细节：

1. **`:199` 的 `use_count` 非零就整轮丢弃。** 上一轮还没全部报完成，这一轮直接不发。所以 IIO 的触发器**会丢采样点而不是排队**。
2. **`use_count` 一次加满 `CONFIG_IIO_CONSUMERS_PER_TRIGGER`（`:200`），不管实际挂了几个消费者。** 空位在 `:205-206` 立刻自减掉。这个写法让「几个消费者」这个信息不必在这里查位图。
3. `generic_handle_irq()` 走的是**硬中断**语义。对应的线程版是 `iio_trigger_poll_chained()`（`:219-233`），用 `handle_nested_irq()`，供已经在线程上下文里的驱动调用。

两个版本唯一的实质差异在**收尾**：

| | `iio_trigger_poll` | `iio_trigger_poll_chained` |
| --- | --- | --- |
| 分发 | `generic_handle_irq`（`:204`） | `handle_nested_irq`（`:228`） |
| 空位收尾 | `iio_trigger_notify_done_atomic`（`:206`） | `iio_trigger_notify_done`（`:230`） |
| `reenable` 怎么调 | `schedule_work(&trig->reenable_work)`（`:192`） | 直接调（`:239-240`） |

`reenable` 回调**可能睡眠**，所以硬中断路径必须甩给 workqueue。`iio_reenable_work_fn()`（`:162-172`）就是那个 work，它的注释 `:167-170` 承认了一个竞态：这个 work 可能在触发器已被关闭之后才跑起来，「in that case the driver should skip reenabling」——**责任推给了驱动**，核心不做任何检查。

### A8.2 顶半部与底半部

`request_threaded_irq()`（A6.4 `:294`）注册的是 `pf->h` / `pf->thread` 一对。绝大多数驱动的 `pf->h` 直接用核心提供的：

```c
348:{
349:	struct iio_poll_func *pf = p;
350:
351:	pf->timestamp = iio_get_time_ns(pf->indio_dev);
352:	return IRQ_WAKE_THREAD;
353:}
```

`iio_pollfunc_store_time()`（`drivers/iio/industrialio-trigger.c:348-354`）只做一件事：**在离触发点最近的地方打时间戳**，然后立刻唤醒线程。`pf->timestamp` 是硬中断半部和线程半部之间唯一的数据通道（`02-datastructures.md` §2.6）。

底半部 `pf->thread` 由驱动实现，典型形态是：读硬件 → 填一个本地 buffer → 调 `iio_push_to_buffers_with_timestamp()` → 调 `iio_trigger_notify_done()`。最后那一步是**必须**的，否则 `use_count` 永远不归零，A8.1 的 `:199` 会把后续所有触发全部丢弃——症状是「采到一次就再也不动了」。

### A8.3 落地：demux 与推送 `drivers/iio/industrialio-buffer.c:1857-1870`

```c
1857:int iio_push_to_buffers(struct iio_dev *indio_dev, const void *data)
...
1863:	list_for_each_entry(buf, &iio_dev_opaque->buffer_list, buffer_list) {
1864:		ret = iio_push_to_buffer(buf, data);
1865:		if (ret < 0)
1866:			return ret;
1867:	}
```

驱动交上来的是**全量 scan**（按 A7.2 协商出的 `config->scan_mask`），而每个缓冲区可能只订了其中一部分通道。裁剪在 `iio_demux()` 里（`:1821-1833`）：

```c
1826:	if (list_empty(&buffer->demux_list))
1827:		return datain;
1828:	list_for_each_entry(t, &buffer->demux_list, l)
1829:		memcpy(buffer->demux_bounce + t->to,
1830:		       datain + t->from, t->length);
```

demux 表是 A7.4 `:1071` 的 `iio_update_demux()` 建的，每一项就是一次 `memcpy` 的三元组（from/to/length）。表为空是**优化过的常见情况**——缓冲区订的通道和设备采的完全一致时零拷贝，直接返回原指针。

`iio_push_to_buffer()`（`:1835-1850`）先 demux 再 `access->store_to()`，然后**无条件**唤醒 poll 队列（`:1848`）。`:1844-1847` 的注释解释了为什么不按 watermark 判断：`read()` 可能要的比 watermark 少，按 watermark 唤醒会让这种读者永远等不到。

`:1864-1866` 遇到一个缓冲区失败就直接返回，**剩下的缓冲区这一轮拿不到数据**——多缓冲区场景下这是一个不对称点，但因为返回值最终会被驱动的 trigger_handler 拿到（通常也只是记个日志），实际影响有限。

用户态那一侧的 `iio_buffer_read()`（`:106-165`）在 `02-datastructures.md` §3.1 末尾列过它的四道门，其中 `bytes_per_datum == 0` 时返回 0（即 EOF）这一条最容易踩。

## A9 关闭缓冲区 `drivers/iio/industrialio-buffer.c:1296-1325`

`echo 0 > buffer0/enable` 走的是**同一个** `enable_store()`，只是 `:1317-1320` 的两个实参调了个位置：

```c
1317:	if (requested_state)
1318:		ret = __iio_update_buffers(indio_dev, buffer, NULL);
1319:	else
1320:		ret = __iio_update_buffers(indio_dev, NULL, buffer);
```

所以关闭路径复用 A7.3 的整套流程：`iio_verify_update()` 走 `:848-850` 那条捷径（只删且是最后一个 → 直接返回 0）、`iio_disable_buffers()` 全停、`iio_buffer_deactivate()` 摘链表、`:1222-1223` 发现表空了直接返回。**关闭比打开短，是因为算配置那一步被跳过了，不是因为走了另一条代码路径。**

内核内的对应入口是 `iio_update_buffers()`（`:1247-1288`），它是导出符号，供 hw-consumer 这类内核内消费者用。它和 sysfs 那条路的三处差异必须记住：

| | `enable_store`（sysfs） | `iio_update_buffers`（内核内） |
| --- | --- | --- |
| 拿几把锁 | 只有 `mlock`（`:1309`） | `info_exist_lock` 外、`mlock` 内（`:1261-1262`） |
| 设备已消失怎么办 | 不检查 | `:1275-1278` 查 `info == NULL` 返回 `-ENODEV` |
| 输出方向缓冲区 | 不检查 | `:1257-1259` 直接 `-EINVAL` |

第一行确立了本子系统的**锁序**：`info_exist_lock` 在外，`mlock` 在内。第二行是 `info_exist_lock` 的存在理由——它保护「设备是否还活着」这个判断和随后的动作之间的原子性（Part B1）。第三行是一处真实的能力差异：**通过 sysfs 可以 enable 一个输出方向的缓冲区，通过内核 API 不行。**

## A10 解绑触发器 `drivers/iio/industrialio-trigger.c:326-346`

`echo "" > current_trigger` 让 A6 的 `iio_trigger_acquire_by_name()` 返回 NULL，于是 `:472-477` 走 detach + put 旧触发器、`:478-482` 因为 `indio_dev->trig` 是 NULL 而什么都不做。

`iio_trigger_detach_poll_func()` 的四步和 attach 严格镜像：

```
:334-338  若我是最后一个用户（no_other_users），调 set_trigger_state(trig, false)
          └─ 失败则 :337 直接 return ret，后面三步全不做
:339-340  清 attached_own_device
:341      iio_trigger_put_irq()   归还 subirq 号
:342      free_irq()
:343      module_put()            释放对消费者驱动模块的引用
```

`:336-337` 是一处**不对称的早退**：`set_trigger_state(false)` 失败就带着错误码直接返回，subirq 不归还、irq 不释放、模块引用不放。此时消费者认为自己已经解绑（A6 的 `:474` 不看返回值），而资源全部泄漏。这是 Part C3 的一条。

除此之外，缓冲区关闭路径也会调 detach（`drivers/iio/industrialio-buffer.c:1169-1172`），条件是 `currentmode == INDIO_BUFFER_TRIGGERED`。所以同一个 `pollfunc` 的挂/摘完全由缓冲区的开关驱动，和 `current_trigger` 的写入无关——A6.3 的表格说明了这个分工。

## A11 设备注销 `drivers/iio/industrialio-core.c:2018-2038`

注销是全篇最需要小心读的一段，因为它**不是 A3 的逆序**——A3 做的事有一半留到了 A13。

```c
2022:	cdev_device_del(&iio_dev_opaque->chrdev, &indio_dev->dev);
2023:
2024:	mutex_lock(&iio_dev_opaque->info_exist_lock);
2025:
2026:	iio_device_unregister_debugfs(indio_dev);
2027:
2028:	iio_disable_all_buffers(indio_dev);
2029:
2030:	indio_dev->info = NULL;
2031:
2032:	iio_device_wakeup_eventset(indio_dev);
2033:	iio_buffer_wakeup_poll(indio_dev);
2034:
2035:	mutex_unlock(&iio_dev_opaque->info_exist_lock);
2036:
2037:	iio_buffers_free_sysfs_and_mask(indio_dev);
2038:}
```

按时间读这七行：

1. **`:2022` 先切断新的访问**。`cdev_device_del()` 让 sysfs 目录和 `/dev/iio:deviceN` 消失，此后不会有新的 `open()`。但**已经打开的 fd 仍然有效**，它们持有 `struct device` 的引用。
2. **`:2024` 进 `info_exist_lock`**。接下来的三步必须对已有的访问者原子可见。
3. **`:2028` `iio_disable_all_buffers()`**（`drivers/iio/industrialio-buffer.c:1290-1294`）= `iio_disable_buffers()` + `iio_buffer_deactivate_all()`。它能无条件调用，靠的是 A7.5 里 `:1153-1154` 的 `list_empty` 早退。
4. **`:2030` `indio_dev->info = NULL;` 是设备「死亡」的唯一标记。** 这一行之后，任何检查 `info == NULL` 的地方都会返回 `-ENODEV`。注意它在 `iio_disable_all_buffers()` **之后**——先停干净，再宣布死亡，否则停的过程中就没法调 `setup_ops` 了。
5. **`:2032-2033` 主动唤醒所有等待者**。已经阻塞在 `read()` 或 `poll()` 里的进程需要被踢一脚，让它们重新检查 `info` 并返回 `-ENODEV`。这两个函数（`drivers/iio/industrialio-event.c:592-599` 和 `drivers/iio/industrialio-buffer.c:310-320`）就是干这个的。注意 `iio_buffer_wakeup_poll` 遍历的是 `attached_buffers[]`（`:316-319`）而不是活跃链表——此时缓冲区已经被 `iio_disable_all_buffers()` 摘掉了，只有挂载数组里还找得到它们。
6. **`:2037` 出锁之后**才释放缓冲区的 sysfs 资源。

**A3 里的三件事没有出现在这里**：通道属性组的释放、事件集的释放、触发器消费者的注销。它们在 `iio_dev_release()` 里：

```c
1633:static void iio_dev_release(struct device *device)
...
1638:	if (indio_dev->modes & INDIO_ALL_TRIGGERED_MODES)
1639:		iio_device_unregister_trigger_consumer(indio_dev);
1640:	iio_device_unregister_eventset(indio_dev);
1641:	iio_device_unregister_sysfs(indio_dev);
1642:
1643:	iio_device_detach_buffers(indio_dev);
1644:
1645:	lockdep_unregister_key(&iio_dev_opaque->mlock_key);
1646:
1647:	ida_free(&iio_ida, iio_dev_opaque->id);
1648:	kfree(iio_dev_opaque);
1649:}
```

`iio_dev_release` 由 `iio_device_type.release`（`:1651-1654`）挂上，**只在最后一个引用消失时才跑**。所以拆除是**两段式**的：

| 阶段 | 什么时候 | 做什么 | 判据 |
| --- | --- | --- | --- |
| `iio_device_unregister` | 驱动 remove 里，同步 | 停采样、断可见性、宣布死亡、唤醒等待者 | 立即返回，不等任何人 |
| `iio_dev_release` | 最后一个 `put_device` 之后 | 释放 sysfs / 事件集 / 缓冲区数组 / ida / 内存 | 可能远晚于 remove |

这个切分是「用户态还开着 fd 时驱动能不能卸载」这个问题的答案：**能**——注销立刻完成，内存留到 fd 关闭。中间这段时间里，用户态的每次访问都会撞上 `info == NULL`。

## A12 拆缓冲区 `drivers/iio/buffer/industrialio-triggered-buffer.c:105-109`

```c
105:void iio_triggered_buffer_cleanup(struct iio_dev *indio_dev)
106:{
107:	iio_dealloc_pollfunc(indio_dev->pollfunc);
108:	iio_kfifo_free(indio_dev->buffer);
109:}
```

两行、无返回值、**不做任何检查**。它必须在 `iio_device_unregister()` 之后调（此时缓冲区已经停了、pollfunc 已经摘了），也必须由 A4 的那个 `-EADDRINUSE` 约束保证 `indio_dev->buffer` 确实是 A4 分配的那个。这是本篇里「约束靠约定而不是靠代码」最典型的一处。

用 `devm_iio_triggered_buffer_setup_ext()`（`:117-134`）时这一步由 devm 自动做，次序也由 devm 保证。

## A13 释放 `drivers/iio/industrialio-core.c:1633-1649`

上一节已经贴了全文，这里只补两点：

- **`:1643` 的 `iio_device_detach_buffers()`** 释放 `attached_buffers[]` 数组本身以及每个成员的引用。它和 A12 的 `iio_kfifo_free()` 不重复——前者管数组和引用计数，后者管 kfifo 后端自己的内存。`02-datastructures.md` §2.2 讲过 `buffer_list`（活跃的）和 `attached_buffers[]`（挂载的）是两套东西。
- **`:1645` 的 `lockdep_unregister_key`** 和 A2 `:1708` 的 `lockdep_register_key` 配对。这一对是 IIO 里唯一的动态 lockdep key，理由见 Part B1。

`:1647-1648` 的 `ida_free` + `kfree` 是最后两行。注意 ida 编号在这里才归还——所以一个 fd 一直不关，`iio:deviceN` 这个编号就一直被占着，新插入的设备会拿到 N+1。

## A14 模块卸载 `drivers/iio/industrialio-core.c:372-378`

已在 A1 末尾对照过。这里只补一个跨模块的次序问题：**IIO 核心模块能不能在还有设备的情况下卸载？**不能——每个 IIO 驱动模块都依赖核心的导出符号，核心的模块引用计数不为零。反过来，**触发器模块能不能在被别人用着的时候卸载？**A6.4 的 `:283` `__module_get(driver_module)` 保护的是**消费者**驱动的模块，不是触发器提供方的模块；触发器一侧靠 `iio_trigger_get()` 的 `get_device()` 保护 `struct device`，但那不阻止 `rmmod`。这是 A5 末尾那句 `/* Possible issue in here */` 注释所在的位置。

---

## A15 主流程一览

一张表把 Part A 的十四节收拢，**注册与拆除成对列出**——右边一列是左边那步的逆动作，空格表示这一步没有对称的逆动作（或者它的逆动作被推迟到了 `iio_dev_release`）。

| # | 构造 / 使能 | 位置 | 逆动作 | 位置 |
| --- | --- | --- | --- | --- |
| 1 | `iio_init` | `industrialio-core.c:345-370` | `iio_exit` | `industrialio-core.c:372-378` |
| 2 | `iio_device_alloc` | `industrialio-core.c:1661-1712` | `iio_device_free` → `iio_dev_release` | `:1719-1723` → `:1633-1649` |
| 3 | `iio_triggered_buffer_setup_ext` | `buffer/industrialio-triggered-buffer.c:39-98` | `iio_triggered_buffer_cleanup` | 同文件 `:105-109` |
| 4 | `iio_buffers_alloc_sysfs_and_mask` | `industrialio-buffer.c:1739-1785` | `iio_buffers_free_sysfs_and_mask` | `industrialio-buffer.c:1787-1803` |
| 5 | `iio_device_register_sysfs` | `industrialio-core.c:1534-1620` | `iio_device_unregister_sysfs` | `industrialio-core.c:1622-1631`（在 release 里调） |
| 6 | `iio_device_register_eventset` | `industrialio-event.c:515-583` | `iio_device_unregister_eventset` | `industrialio-event.c:601-614`（在 release 里调） |
| 7 | `iio_device_register_trigger_consumer` | `industrialio-trigger.c:739-743` | `iio_device_unregister_trigger_consumer` | `industrialio-trigger.c:745-750`（在 release 里调） |
| 8 | `cdev_device_add` | `industrialio-core.c:1996` | `cdev_device_del` | `industrialio-core.c:2022` |
| 9 | `iio_trigger_register` | `industrialio-trigger.c:67-100` | `iio_trigger_unregister` | `industrialio-trigger.c:103-112` |
| 10 | `current_trigger_store`（写名字） | `industrialio-trigger.c:431-490` | 同函数（写空串） | 同上 |
| 11 | `iio_trigger_attach_poll_func` | `industrialio-trigger.c:274-324` | `iio_trigger_detach_poll_func` | `industrialio-trigger.c:326-346` |
| 12 | `iio_enable_buffers` | `industrialio-buffer.c:1059-1143` | `iio_disable_buffers` | `industrialio-buffer.c:1145-1191` |
| 13 | `iio_buffer_activate` | `industrialio-buffer.c:731-738` | `iio_buffer_deactivate` | `industrialio-buffer.c:740-745` |
| 14 | `iio_push_to_buffers`（运行期） | `industrialio-buffer.c:1857-1870` | 无（数据单向） | — |

三条关于这张表的读法：

- **第 5、6、7 行的逆动作不在 `iio_device_unregister()` 里**，而在 `iio_dev_release()`（`industrialio-core.c:1638-1641`）。A11 的两段式对照表解释了为什么。
- **第 4 行是唯一在 `iio_device_unregister()` 里同步做完的释放**（`:2037`），和它对称的注册（`:1957`）也是 A3 里第一个做的——先建的最后拆，这一对是完整对称的。
- **第 10、11 行不是一对**：写 `current_trigger` 只挂 `pollfunc_event`，`pollfunc` 的挂摘由第 12 行的缓冲区开关驱动（A6.3 的表格）。

---

# Part B · 横切机制

## B1 两把锁的分工

IIO 每个设备有两把 mutex，`02-datastructures.md` §6.1 给了字段位置和加锁行数统计，这里讲它们**为什么是两把而不是一把**。

| | `indio_dev->mlock` | `iio_dev_opaque->info_exist_lock` |
| --- | --- | --- |
| 保护什么 | 模式的互斥性：DIRECT 与 BUFFER_* 不能同时 | `indio_dev->info` 指针的生死 |
| 谁持有最久 | `enable_store` 全程（`drivers/iio/industrialio-buffer.c:1309-1323`） | `iio_device_unregister` 的中段（`drivers/iio/industrialio-core.c:2024-2035`） |
| 典型用法 | `iio_device_claim_direct_mode()` | `if (info == NULL) return -ENODEV;` |
| 锁序 | 内层 | 外层（`drivers/iio/industrialio-buffer.c:1261-1262`） |

**为什么一把锁不够**：`mlock` 的持有者是「正在做业务」的路径，`info_exist_lock` 的持有者是「正在拆设备」的路径。如果合成一把，那么一个阻塞在 `read()` 里的读者会挡住设备注销——而注销恰恰要靠 `:2032-2033` 的唤醒把这个读者踢出来，形成死锁。分成两把之后，注销路径拿的是读者不持有的那把。

`iio_device_claim_direct_mode()`（`drivers/iio/industrialio-core.c:2070-2079`）是 `mlock` 最反直觉的用法：

```c
2070:int iio_device_claim_direct_mode(struct iio_dev *indio_dev)
2071:{
2072:	mutex_lock(&indio_dev->mlock);
2073:
2074:	if (iio_buffer_enabled(indio_dev)) {
2075:		mutex_unlock(&indio_dev->mlock);
2076:		return -EBUSY;
2077:	}
2078:	return 0;
2079:}
```

**成功时带着锁返回。** 配对的 `iio_device_release_direct_mode()`（`:2091-2095`）只有一行 `mutex_unlock`。这是内核里少见的「锁跨函数边界」写法，语义是「我要独占直接模式，直到我说完」。驱动在 `read_raw` 回调里读硬件时用它防止缓冲区采样同时进行。忘了配对调用 release 的后果是这个设备的 `mlock` 永久锁死——sysfs 读写全部挂住，而且 lockdep 不会报（它不是 AB-BA）。

`mlock_key`（A2 `:1708-1709`）的作用：如果所有设备的 `mlock` 共用一个 lockdep 类，那么「设备 A 的 mlock → 设备 B 的 mlock」这种合法嵌套（比如 iio-mux 这类一个 IIO 设备消费另一个 IIO 设备的场景）会被 lockdep 误报成递归死锁。每个设备一个 key 让 lockdep 能区分它们。

## B2 三套引用计数

IIO 里同时跑着三套互不相干的计数，混淆它们是排查「设备/模块拆不掉」的最大障碍。

| 计数 | 载体 | 加/减的行 | 保护什么 |
| --- | --- | --- | --- |
| `struct device` 的 kref | `indio_dev->dev` | A2 `:1686` initialize；A13 `iio_dev_release` 是归零回调 | `iio_dev_opaque` 这块内存 |
| 模块引用 | 消费者驱动的 `struct module` | A6.4 `:283` `__module_get`；A10 `:343` `module_put` | 挂在触发器上时不许 `rmmod` 消费者驱动 |
| `use_count` | `trig->use_count`（atomic） | A8.1 `:200` 一次加满；每个消费者报完成时自减 | 一轮采样的完成状态，不是生命周期 |

第三个是最容易误读的：`use_count` 名字像引用计数，实际是**一轮采样的未完成消费者数**，每一轮都重置。它和对象生命周期毫无关系。

`iio_trigger_get()` / `iio_trigger_put()` 走的是第一套（`get_device`/`put_device`），A6 `:452` 的 acquire 和 `:476`/`:488` 的 put 就是它。

## B3 模式状态机

`iio_dev_opaque->currentmode` 只有四个可能的值，转换点在整棵树里只有五处：

```
                    A7.4 drivers/iio/industrialio-buffer.c:1069
    INDIO_DIRECT_MODE ──────────────────▶ config->mode
            ▲                              （TRIGGERED / HARDWARE / SOFTWARE
            │                                之一，由 A7.2 协商出）
            │
            ├── A7.4 失败回滚 drivers/iio/industrialio-buffer.c:1139
            └── A7.5 正常关闭 drivers/iio/industrialio-buffer.c:1188
```

| 行 | 从 | 到 | 场合 |
| --- | --- | --- | --- |
| `industrialio-buffer.c:1069` | DIRECT | config->mode | 使能缓冲区 |
| `industrialio-buffer.c:1139` | config->mode | DIRECT | 使能失败回滚 |
| `industrialio-buffer.c:1188` | config->mode | DIRECT | 正常关闭 |

`indio_dev->modes`（复数、无 current 前缀）是**能力位图**，由驱动填、核心只读；`currentmode`（单数）是**当前状态**，由核心写、驱动只能通过 `iio_device_get_current_mode()`（`drivers/iio/industrialio-core.c:2140-2145`）读。名字只差一个前缀，含义是「能做什么」对「正在做什么」。

三处检查点靠 `currentmode` 做决策：A6.1 `:442`（不许换触发器）、A7.4 `:1107`（要不要挂 pollfunc）、A7.5 `:1169`（要不要摘 pollfunc）。加上 `iio_buffer_enabled()` 的间接读（A7 那条路径和 `claim_direct_mode`），构成模式互斥的全部实现。

## B4 错误回滚的三种策略

同一个子系统里并存三套不同的错误处理态度，每一套都有它的场合：

| 策略 | 代表 | 做法 | 适用场合 |
| --- | --- | --- | --- |
| 倒序 goto 链 | A3 `drivers/iio/industrialio-core.c:2002-2010`、A6.4 `drivers/iio/industrialio-trigger.c:317-322` | 标签穿透，从哪进来就往下走完 | 构造期，每一步都有独立分配 |
| 记住第一个错误、一步不跳 | A7.5 `drivers/iio/industrialio-buffer.c:1163-1184` | `ret2 = ...; if (ret2 && !ret) ret = ret2;` | 拆除期，跳步会留下不一致状态 |
| 放弃恢复、退到 sane 状态 | A7.3 `drivers/iio/industrialio-buffer.c:1231-1240` | `iio_buffer_deactivate_all()` | 硬件 IO 故障，没有好的恢复策略 |

第三种在注释里明确写了「no good recovery strategy」。这三种策略的分界不是随意的：**构造期失败可以精确倒回，拆除期失败不能跳过，运行期硬件失败只能退到已知安全点。**

一处策略不一致的实例：软件设备与软件触发器的 configfs 创建失败路径。`drivers/iio/industrialio-sw-trigger.c:61-66` 在失败时 `mutex_lock` + `list_del` + `mutex_unlock` 把已经挂上的节点摘掉，而同构的 `drivers/iio/industrialio-sw-device.c:61-62` 只设了 `ret = PTR_ERR(...)`、**没有 `list_del`**。两个文件是平行实现，这是 Part C3 的一条。

## B5 上下文与 PREEMPT_RT

本树打了全树 PREEMPT_RT 回移补丁，但 **iio 目录一行未改**（`git status --porcelain` 对所有 iio 路径为空，见 `99-evidence.md` §1）。所以 iio 在 RT 下的行为完全由通用机制决定。三个和上下文相关的点：

| 位置 | 上下文 | RT 下的差异 |
| --- | --- | --- |
| `iio_trigger_poll()`（`drivers/iio/industrialio-trigger.c:195-209`） | 硬中断 | RT 下多数 irq 被强制线程化，这个"硬中断"实际跑在 irq 线程里 |
| `pf->h` 顶半部（`drivers/iio/industrialio-trigger.c:348-354`） | 硬中断 | 同上；`iio_get_time_ns()` 的取时点因此可能晚于真实触发 |
| `pf->thread` 底半部 | 中断线程 | 本来就是线程，无差异 |
| `iio_reenable_work_fn`（`drivers/iio/industrialio-trigger.c:162-172`） | workqueue | 无差异 |

核心里的锁**全部是 mutex，没有一个 spinlock**（`02-datastructures.md` §6.1 的两张锁表就是全集），意味着**IIO 的对外 API 一律不能在原子上下文调用**——包括 `iio_read_channel_raw()` 这类内核内消费者 API，因为它们最终要走 `iio_device_claim_direct_mode()` 拿 mutex。这一条对写驱动的人比 RT 本身更重要。

唯一的例外在 DMA 缓冲区后端：`include/linux/iio/buffer-dma.h:106` 的 `queue->list_lock` 和 `drivers/iio/buffer/industrialio-buffer-dma.c:129` 的 `iio_dma_buffer_dead_blocks_lock` 是 spinlock，用 `spin_lock_irqsave` 保护——因为 DMA 完成回调可能在硬中断里跑。这两把锁不属于核心，属于后端，`04-extensions.md` 里展开。

重跑：`grep -rn 'spinlock_t\|spin_lock' drivers/iio/*.c drivers/iio/*.h drivers/iio/buffer/ drivers/iio/trigger/ include/linux/iio/` 的全部命中都在 `buffer-dma*`。

---

# Part C · 评价

## C1 这套实现做对了什么

以下八条都能指到具体行，不是印象。

**一、把「算配置」和「改状态」彻底分开。** `iio_verify_update()`（`drivers/iio/industrialio-buffer.c:822-927`）一个字节的设备状态都不改，只填一个 `struct iio_device_config`；所有改动集中在 `iio_enable_buffers()`（`:1059-1143`）。这个切分让「配置非法」和「硬件出错」变成两类完全不同的失败：前者在没动任何东西的时候返回，后者才需要回滚。A7.3 那段「no good recovery strategy」的注释之所以只覆盖后者，就是因为前者被前移了。

**二、死亡标记只有一个点。** `indio_dev->info = NULL`（`drivers/iio/industrialio-core.c:2030`）是全树唯一宣布设备消失的动作，配 `info_exist_lock` 保证原子可见。任何要判断「设备还活着吗」的代码只需要 `if (!indio_dev->info)`，不需要一个额外的状态字段，也就不存在两个字段不同步的可能。

**三、拆除分两段。** `iio_device_unregister()` 同步做完「停止 + 隐身 + 唤醒」，`iio_dev_release()` 等最后一个引用消失才释放内存（A11 的对照表）。这让「用户态还开着 fd」不阻塞驱动卸载。

**四、demux 表把复杂度压成三元组。** 多个缓冲区各订不同通道子集这件事，最终只落成一串 `(from, to, length)` 的 `memcpy`（`drivers/iio/industrialio-buffer.c:1828-1830`），而且常见情况（订的和采的完全一致）走 `:1826-1827` 零拷贝。运行期热路径上没有任何位图运算。

**五、触发器做成虚拟 irq_chip。** 复用 genirq 意味着免费得到：线程化中断（`request_threaded_irq`）、mask/unmask 语义、`/proc/interrupts` 里的可见计数。如果自己实现一套回调链表，这三样都要重写一遍。

**六、sysfs 属性名由核心合成。** 驱动只填 `info_mask_*` 的位，属性名、`_available` 变体、通道索引后缀全由 `iio_device_add_info_mask_type()`（`drivers/iio/industrialio-core.c:1231-1259`）生成。**结果是 ABI 一致性不依赖驱动作者的自觉**——这是 IIO 能有一份统一的 `Documentation/ABI/testing/sysfs-bus-iio` 的根本原因。

**七、`noop_ring_setup_ops` 兜底。** `drivers/iio/industrialio-core.c:1979-1981` 替没填 `setup_ops` 的驱动装上空实现，于是 `buffer.c` 里所有 `indio_dev->setup_ops->preenable` 之类的解引用都不必判空。用「保证非空」代替「处处判空」。

**八、关闭路径「记住第一个错误但一步不跳」。** `drivers/iio/industrialio-buffer.c:1156-1161` 的注释把理由写清楚了：跳步会留下不一致状态。这和构造路径的 goto 链是两种相反的策略，而两种都是对的——区别在于失败时想到达的终态不同。

## C2 源码自己承认的设计债

全树扫过 `drivers/iio/*.c`、`*.h`、`buffer/`、`trigger/`、`include/linux/iio/` 的 FIXME / TODO / REVISIT，加上几条虽然没用这些关键词但明确自陈的注释，共九条：

| 位置 | 原文/大意 | 性质 |
| --- | --- | --- |
| `include/linux/iio/iio.h:14` | `/* IIO TODO LIST */` | 头文件开头的历史遗留 TODO 清单标题 |
| `drivers/iio/inkern.c:893` | `/* FIXME: learn about max for other iio values */` | 内核内消费者的取值范围处理不完整 |
| `drivers/iio/buffer/industrialio-buffer-dma.c:382` | `TODO: Implement support in the IIO core to allow buffers to…` | DMA 后端需要核心的能力还不存在 |
| `drivers/iio/trigger/iio-trig-loop.c:8` | 注释自称这个触发器 "may eat babies" | 作者对自己实现的可靠性不做保证 |
| `drivers/iio/trigger/iio-trig-loop.c:10-14` | Todo 块 | 同上文件的待办清单 |
| `drivers/iio/industrialio-trigger.c:110` | `/* Possible issue in here */`，在 `iio_trigger_unregister` 的 `device_del` 之前 | 注销时不检查是否还有消费者挂着 |
| `drivers/iio/industrialio-trigger.c:167-170` | reenable work 可能在触发器已关闭后才跑，"the driver should skip reenabling" | 竞态责任推给驱动 |
| `drivers/iio/industrialio-buffer.c:871-874` | "Keep things simple for now and only allow a single buffer to be connected in hardware mode" | HARDWARE 模式的多缓冲区限制是有意的临时方案 |
| `drivers/iio/industrialio-buffer.c:1564-1576` | 9 行注释解释为什么故意泄漏一个 fd，"rely on the process exit path" | `iio_device_buffer_getfd()` 在 `copy_to_user` 失败时的处理 |

最后一条值得展开：`iio_device_buffer_getfd()`（`drivers/iio/industrialio-buffer.c:1525-1587`）已经 `anon_inode_getfd()` 拿到 fd 之后，如果 `copy_to_user` 把 fd 号写回用户态失败，代码选择**不回收这个 fd**，注释的理由是「反正也没什么好办法，依赖进程退出路径清理」。这不是疏漏，是一个被写下来的权衡。

重跑命令：

```
rg -n 'FIXME|TODO|REVISIT|XXX' drivers/iio/*.c drivers/iio/*.h \
   drivers/iio/buffer/ drivers/iio/trigger/ include/linux/iio/
```

## C3 实际缺陷

以下每条都在 6.1.158 的源码里，按后果严重程度排。**这些是阅读记录，不是本文档集要求修的东西**——本树只读。

### C3.1 `iio_buffer_write()` 提前返回时不摘等待队列 · `industrialio-buffer.c:195-198`

```c
195:	add_wait_queue(&rb->pollq, &wait);
196:	do {
197:		if (indio_dev->info == NULL)
198:			return -ENODEV;
```

`wait` 是 `DEFINE_WAIT_FUNC` 定义在**栈上**的（`:181`），`:195` 把它挂进了 `rb->pollq`。`:198` 的 `return` 绕过了 `:224` 的 `remove_wait_queue`——函数返回后，缓冲区的等待队列里留着一个指向已销毁栈帧的节点。下一次 `wake_up_interruptible_poll()` 就会去访问它。

**同一个文件里的读路径是对的**：`iio_buffer_read()` 的同位置检查（`:142-145`）用的是 `break`，正常走到 `:162` 的 `remove_wait_queue`。两个函数是明显的复制粘贴关系，写路径改错了控制流关键字。

触发条件：写方向缓冲区（`IIO_BUFFER_DIRECTION_OUT`，即 DAC 类设备）+ 在 `write()` 阻塞期间设备被拔掉/驱动被卸载。

### C3.2 `iio_trigger_attach_poll_func()` 在拿不到 subirq 时返回 0 · `industrialio-trigger.c:280-291`

```c
280:	int ret = 0;
...
286:	pf->irq = iio_trigger_get_irq(trig);
287:	if (pf->irq < 0) {
288:		pr_err("Could not find an available irq for trigger %s, ...");
289:		...
290:		goto out_put_module;
291:	}
```

`ret` 初值 0，这条分支**没有给 `ret` 赋错误码**就 goto 到 `out_put_module`（`:321`），然后 `:323 return ret;` ——返回 0，调用者认为成功。

后果链：`iio_enable_buffers()` 的 `:1108-1111` 检查了返回值，但拿到的是 0，于是继续往下走，缓冲区被标记为已使能。而 `pf->irq` 此时是一个负数（`iio_trigger_get_irq()` 失败时的返回值），A4 提过 `iio_alloc_pollfunc()` 用的是 `kmalloc` 所以这个字段本来也没初始化过。等到 A9/A10 走 detach，`:341-342` 会拿这个负数去 `iio_trigger_put_irq()`（对位图做 `clear_bit(负数 - base)`）和 `free_irq(负数)`。

唯一的现场信号是 `:288` 那条 `pr_err`。触发条件：同一个触发器上挂第 `CONFIG_IIO_CONSUMERS_PER_TRIGGER + 1` 个消费者（默认即第 3 个）。

### C3.3 `iio_trigger_detach_poll_func()` 早退时泄漏三样资源 · `industrialio-trigger.c:334-338`

```c
334:	if (trig->ops && trig->ops->set_trigger_state && no_other_users) {
335:		ret = trig->ops->set_trigger_state(trig, false);
336:		if (ret)
337:			return ret;
338:	}
```

`set_trigger_state(false)` 失败就直接返回，`:341` 的 `iio_trigger_put_irq()`、`:342` 的 `free_irq()`、`:343` 的 `module_put()` 三步全不执行。subirq 位不归还（这个触发器从此少一个可用槽位）、irq 不释放、消费者驱动的模块引用不放（从此 `rmmod` 不掉）。

而两个调用点都不看返回值：`current_trigger_store` 的 `:474-475`，以及 `iio_disable_buffers` 的 `:1169-1172`。所以这个失败对上层完全不可见。

### C3.4 `industrialio-core.c:1566-1567` 是死代码

```c
1566:	if (iio_dev_opaque->event_interface)
1567:		clk = &dev_attr_current_timestamp_clock.attr;
```

这两行在 `iio_device_register_sysfs()` 里，调用点是 `__iio_device_register()` 的 `:1964`。而 `event_interface` 全树**唯一**的赋非空点是 `industrialio-event.c:531`，在 `iio_device_register_eventset()` 里，调用点是 `:1970` ——晚六行。

（重跑：`grep -rn 'event_interface[[:space:]]*=' drivers/iio/ include/linux/iio/` 只有四处，`:531` 赋值、`:581` 与 `:613` 置 NULL、`:596` 是读。）

所以注册路径上 `:1566` 的条件永远为假。后果：**有事件、但没有 `IIO_TIMESTAMP` 通道的设备，拿不到 `current_timestamp_clock` 属性**——而 `:1557-1558` 那条通道判断是它唯一还生效的来源。`current_timestamp_clock` 控制 `iio_get_time_ns()` 用哪个时钟源，事件时间戳同样受它影响，所以这两行的意图（「有事件也该给这个属性」）是对的，只是被次序打败了。

### C3.5 `iio_scan_mask_query()` 的上界差一 · `industrialio-buffer.c:495`

```c
495:	if (bit > indio_dev->masklength)
496:		return -EINVAL;
```

`masklength` 是位图的位数，合法下标是 `0 .. masklength-1`，所以这里应当是 `>=`。`bit == masklength` 会被放过去，落到 `:502` 的 `test_bit(masklength, buffer->scan_mask)` ——读位图外的一个 bit。

实际可达性受调用点限制：`iio_scan_el_store()`（`:505-542`）传进来的 `bit` 来自属性自己的 `address` 字段，由核心在建属性时填，不来自用户输入。所以这是一个防御性检查写错了，而不是一条可从用户态触达的越界读。

同一组函数里 `iio_scan_mask_clear()`（`:486-490`）**完全没有**上界检查——它直接 `clear_bit(bit, buffer->scan_mask)`。两个函数一个查错了、一个不查，说明这层检查本来就不是被认真对待的边界。

### C3.6 `iio_trigger_using_own()` 无判空 · `industrialio-trigger.c:712-715`

```c
712:bool iio_trigger_using_own(struct iio_dev *indio_dev)
713:{
714:	return indio_dev->trig->attached_own_device;
715:}
716:EXPORT_SYMBOL(iio_trigger_using_own);
```

导出符号、无 kerneldoc、直接解引用 `indio_dev->trig`。没绑触发器的设备 `trig` 是 NULL。调用者必须自己先确认已绑定，而函数名（"using own"）读起来像是「可以随便问」的查询。

### C3.7 `iio_update_demux()` 的返回值被丢弃 · `industrialio-buffer.c:1071`

```c
1071:	iio_update_demux(indio_dev);
```

`iio_update_demux()`（`:1039-1057`）返回 int，内部的 `iio_buffer_update_demux()`（`:974-1037`）会做多次 `kmalloc`，失败时返回 `-ENOMEM`。这一行不接返回值，于是 demux 表建不起来的时候，`iio_enable_buffers()` 照常往下走。运行期的后果是 `iio_demux()`（`:1821-1833`）看到一个不完整的表，按残缺的三元组做 `memcpy` ——数据错位，而不是报错。

### C3.8 configfs 创建失败时软件设备不摘链表 · `industrialio-sw-device.c:61-62`

```
industrialio-sw-trigger.c:61-66   失败时 mutex_lock + list_del + mutex_unlock + ret = PTR_ERR
industrialio-sw-device.c:61-62    失败时只有 ret = PTR_ERR，没有 list_del
```

两个文件是平行实现（软件触发器 / 软件设备），同一位置的错误处理不一致。设备侧留在链表上的节点随后会被释放，链表里剩一个悬空指针。

### C3.9 三条低危项

| 位置 | 问题 | 后果 |
| --- | --- | --- |
| `drivers/iio/buffer/kfifo_buf.c:158` | `if (kfifo_size(&kf->kf) < 1)` 用的是**容量**而不是占用量，应当是 `kfifo_len` | 这个检查恒为假（容量至少 2，见 `:81-82`），等于没有；真正拦住空缓冲区的是 `:162-163` 的 `kfifo_out` 返回值检查。属于无效代码而非错误行为 |
| `drivers/iio/buffer/kfifo_buf.c:81-82` | `if (length < 2) length = 2;` 静默把用户设的长度改大 | 用户 `echo 1 > buffer0/length` 之后读回来是 2，没有任何提示。`06/07` 篇的「设了没生效」类症状 |
| `drivers/iio/iio_core_trigger.h:59` | `#endif /* CONFIG_TRIGGER_CONSUMER */` 与 `:8` 的 `#ifdef CONFIG_IIO_TRIGGER` 不匹配 | 纯注释错误。`CONFIG_TRIGGER_CONSUMER` 在全树只出现在这一行注释里（`grep -rn CONFIG_TRIGGER_CONSUMER .` 只有这一处），说明它从来不存在 |

### C3.10 两处过时的 `MODULE_ALIAS`

`drivers/iio/trigger/iio-trig-loop.c:141` 和 `drivers/iio/trigger/iio-trig-sysfs.c:225` 都有 `MODULE_ALIAS("platform:…")`，但这两个文件里 `platform_driver` 的数量是 **0** ——它们是 configfs 驱动的软件触发器，不走 platform 总线。别名指向一个不存在的绑定方式，`modprobe` 按 platform 别名永远匹配不上。

对照：`drivers/iio/trigger/stm32-timer-trigger.c:916` 也有 `MODULE_ALIAS`，但那个文件里有 2 个 `platform_driver`，是合法的。

### C3.11 `IIO_DEV_MAX` 与 ida 之间没有约束

A1 提过：`alloc_chrdev_region(&iio_devt, 0, IIO_DEV_MAX, "iio")`（`drivers/iio/industrialio-core.c:356`）划定了次设备号区间宽度，而设备的次设备号来自 `ida_alloc(&iio_ida)`（`drivers/iio/industrialio-core.c:1691`）分配的编号，在 `:1989` 直接 `MKDEV(MAJOR(iio_devt), iio_dev_opaque->id)`。**ida 不知道 `IIO_DEV_MAX` 这个上限，注册路径也不检查 `id < IIO_DEV_MAX`。** 系统里同时存在超过 `IIO_DEV_MAX` 个 IIO 设备时，第 `IIO_DEV_MAX+1` 个会拿到一个不在申请区间内的设备号。这需要相当多的设备才会碰到，但代码里确实没有这道门。

## C4 kerneldoc 与代码不符的地方

这几条留给 `99-evidence.md` 的规则 6 冲突表登记，这里只列与本篇 Part A 直接相关的：

| 位置 | kerneldoc 说 | 代码实际 |
| --- | --- | --- |
| `drivers/iio/buffer/industrialio-triggered-buffer.c:24-25` | 描述了 `setup_ops` 传 NULL 时的默认行为 | 默认值由 `drivers/iio/industrialio-core.c:1979-1981` 的 `noop_ring_setup_ops` 提供，且**只在设备声明了缓冲模式时**才装 |
| `drivers/iio/industrialio-core.c:2068` | `iio_device_claim_direct_mode` 的 "Returns: 0 on success, -EBUSY on failure" | 准确，但**没有说成功时会带着 `mlock` 返回**——这是使用它最需要知道的一件事 |
| `drivers/iio/industrialio-trigger.c:712-715` | 无 kerneldoc | 导出符号却无文档，且有 C3.6 的判空问题 |
