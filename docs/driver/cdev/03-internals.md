---
title: 内部实现
---

# cdev 03：实现剖析

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 导航

### §0.1 本篇只承诺一条主轴，其余显式分区

| 分区 | 主轴 | 章节号 |
|---|---|---|
| **Part A** | **时间线**：从内核启动到一次 `open()`，再到拆除 | §A1 … §A8 |
| **Part B** | **横切机制**：两把锁、两种 kobj_type、引用计数、模块自动加载 | §B1 … §B6 |
| **Part C** | **评价**：源码设计债与整体判断 | §C1 … §C5 |

### §0.2 分层图

```
   ┌── 你的驱动 ───────────────────────────────────────────────┐
   │  alloc_chrdev_region / cdev_init / cdev_device_add         │
   │  struct file_operations                                    │
   └──────────────────────────┬────────────────────────────────┘
                              │
   ┌──────────────────────────▼────────────────────────────────┐
   │  设备号层（fs/char_dev.c 上半）                             │
   │  chrdevs[255] · __register_chrdev_region · find_dynamic_major│
   └──────────────────────────┬────────────────────────────────┘
                              │
   ┌──────────────────────────▼────────────────────────────────┐
   │  cdev 对象层（fs/char_dev.c 下半）                          │
   │  cdev_init/alloc/add/del · 两种 kobj_type · cdev_purge      │
   └──────────────────────────┬────────────────────────────────┘
                              │
   ┌──────────────────────────▼────────────────────────────────┐
   │  查找层（drivers/base/map.c）                               │
   │  kobj_map / kobj_unmap / kobj_lookup（最长匹配 + 兜底项）    │
   └──────────────────────────┬────────────────────────────────┘
                              │
   ┌──────────────────────────▼────────────────────────────────┐
   │  VFS（include/linux/fs.h + fs/*）                          │
   │  inode->i_rdev · def_chr_fops · replace_fops               │
   └───────────────────────────────────────────────────────────┘
```

### §0.3 生命周期时间线

```
   启动
    │
    ├─ §A1  chrdev_init()                    建 cdev_map（含兜底项）
    │
    ├─ §A2  驱动 probe：占设备号             chrdevs[] 里多一条
    │
    ├─ §A3  驱动 probe：cdev_init             kobject 初始化
    │
    ├─ §A4  驱动 probe：cdev_device_add        cdev_map 里多一条 + /dev 节点出现
    │
    ├─ §A5  ──── 用户态 open() ────           两级查找 + replace_fops
    │
    ├─ §A6  ──── read / write / ioctl ────    完全不经过字符设备层
    │
    ├─ §A7  ──── close() ────                 fops->release
    │
    └─ §A8  驱动 remove：cdev_device_del + unregister_chrdev_region
```

### §0.4 哪些内容不在本篇

| 内容 | 去哪 |
|---|---|
| 12 个 API 的签名与错误码 | 01 篇 |
| 结构体字段、五组关系、命名约定 | 02 篇 |
| devtmpfs / udev / misc / `/proc/devices` 这四个独立框架 | 04 篇 |
| 怎么写驱动 | 05 篇 |
| DTS —— **本子系统没有 DT 绑定** | 06 篇（改讲替代配置机制） |
| `/proc/devices`、`ls /dev`、调试手段 | 07 篇 |
| 「为什么是这样」的大白话解释 | 08 篇 |

---

# Part A：时间线

## §A1 启动：`chrdev_init()`

```
   start_kernel() → vfs_caches_init() → chrdev_init()
                                          └─ cdev_map = kobj_map_init(base_probe, &chrdevs_lock)
```

| 元素 | 位置 |
|---|---|
| `chrdev_init()` | `fs/char_dev.c:667` |
| 那一行赋值 | `fs/char_dev.c:669` |
| `kobj_map_init()` | `drivers/base/map.c:135` |

`kobj_map_init()` 做三件事：

| 步骤 | 动作 | 位置 |
|---|---|---|
| ① | `kmalloc(struct kobj_map)` + `kzalloc(struct probe)` | `drivers/base/map.c:137-138` |
| ② | 建一个 `dev = 1, range = ~0` 的**兜底项** | `drivers/base/map.c:147-148` |
| ③ | 把这**同一个** `base` 项放进**全部 255 个桶** | `drivers/base/map.c:150-151` |
| ④ | 记下外部传进来的锁 | `drivers/base/map.c:152` |

**③ 那一句 `p->probes[i] = base` 是同一个指针放 255 次** ——
不是 255 个副本。⇒ 它永远不会被 `kobj_unmap()` 摘掉（那个函数只按 `dev`+`range` 精确匹配，
`drivers/base/map.c:82`），所以兜底项是**永久的**。

兜底项的 `get` 是 `base_probe()`（`fs/char_dev.c:659`），它只做一件事：
```c
	if (request_module("char-major-%d-%d", MAJOR(dev), MINOR(dev)) > 0)
		/* Make old-style 2.4 aliases work */
		request_module("char-major-%d", MAJOR(dev));
	return NULL;
```
⇒ **打开一个没人注册的设备号会触发模块自动加载**（§B5）。

⚠️ 注意它**总是 `return NULL`** —— 所以模块加载成功后
`kobj_lookup()` 会 `goto retry`（`drivers/base/map.c:129`）从头再查一遍，
这时新加载的模块已经 `cdev_add()` 过了。

## §A2 驱动 probe（一）：占住设备号

```
   alloc_chrdev_region(&devt, 0, N, "mydev")
     └─ __register_chrdev_region(0, 0, N, "mydev")
          ├─ major >= 512 ⇒ -EINVAL（有日志）
          ├─ minorct 越界 ⇒ -EINVAL（有日志）
          ├─ kzalloc(struct char_device_struct) ⇒ 失败 -ENOMEM
          ├─ mutex_lock(&chrdevs_lock)
          ├─ major == 0 ⇒ find_dynamic_major()
          │    ├─ 254 → 234 扫首选区（找空桶）
          │    ├─ 511 → 384 扫扩展区（扫链表）
          │    └─ 都满 ⇒ -EBUSY（有日志）
          ├─ 遍历 chrdevs[major % 255] 找插入点 + 判重叠 ⇒ -EBUSY（无日志）
          ├─ 填 major/baseminor/minorct/name（strlcpy 截断到 64）
          ├─ 按 major 升序插入链表
          └─ mutex_unlock ⇒ 返回 cd
   ⇒ *dev = MKDEV(cd->major, cd->baseminor)
```

| 关键位置 | 行号 |
|---|---|
| `alloc_chrdev_region()` | `fs/char_dev.c:236` |
| `__register_chrdev_region()` | `fs/char_dev.c:97` |
| `major >= CHRDEV_MAJOR_MAX` | `fs/char_dev.c:103-107` |
| minor 区间检查 | `fs/char_dev.c:109-113` |
| `kzalloc` + `-ENOMEM` | `fs/char_dev.c:115-118` |
| `mutex_lock` | `fs/char_dev.c:119` |
| `find_dynamic_major()` 调用 | `fs/char_dev.c:122` |
| 动态区满的日志 | `fs/char_dev.c:125` |
| `ret = -EBUSY` 预设 | `fs/char_dev.c:132` |
| 重叠判断四条件 | `fs/char_dev.c:133-147` |
| `strlcpy(cd->name, …)` | `fs/char_dev.c:153` |
| 插入链表 | `fs/char_dev.c:155-161` |
| `find_dynamic_major()` | `fs/char_dev.c:65` |
| 首选区扫描 | `fs/char_dev.c:70-73` |
| 扩展区扫描 | `fs/char_dev.c:75-83` |
| `return -EBUSY` | `fs/char_dev.c:85` |
| 输出 `dev_t` | `fs/char_dev.c:241` |

### §A2.1 两个区的扫描方式**不一样**

| 区 | 判据 | 位置 |
|---|---|---|
| 首选区（254 → 234） | **`chrdevs[i] == NULL`** —— 整个桶为空 | `fs/char_dev.c:71` |
| 扩展区（511 → 384） | **遍历链表**看有没有这个 major | `fs/char_dev.c:77-79` |

**为什么不一样**：主设备号 234–254 都小于 255，所以 `major % 255 == major`，
**一个桶就对应一个 major** ⇒ 桶空就等于这个 major 没人用。
而 384–511 会和 129–256 撞桶（`384 % 255 = 129`），所以必须遍历链表确认。

⇒ 这是 `CHRDEV_MAJOR_HASH_SIZE = 255`（`fs/char_dev.c:32`）
和 `CHRDEV_MAJOR_MAX = 512`（`include/linux/fs.h:2896`）不一致带来的真实复杂度。

### §A2.2 重叠判断的四个条件

```c
	for (curr = chrdevs[i]; curr; prev = curr, curr = curr->next) {
		if (curr->major < major)                              continue;   /* 还没到 */
		if (curr->major > major)                              break;      /* 过头了，插这儿 */
		if (curr->baseminor + curr->minorct <= baseminor)     continue;   /* 在我前面 */
		if (curr->baseminor >= baseminor + minorct)           break;      /* 在我后面 */
		goto out;                                                        /* 重叠 */
	}
```

| 条件 | 行号 |
|---|---|
| `curr->major < major` | `fs/char_dev.c:134-135` |
| `curr->major > major` | `fs/char_dev.c:137-138` |
| `curr->baseminor + curr->minorct <= baseminor` | `fs/char_dev.c:140-141` |
| `curr->baseminor >= baseminor + minorct` | `fs/char_dev.c:143-144` |
| `goto out`（重叠） | `fs/char_dev.c:146` |

前两条按 major 排序、后两条按 minor 区间判重叠 ——
**四条走完都没 `continue`/`break` 就是重叠** ⇒ 返回 `-EBUSY`。

## §A3 驱动 probe（二）：`cdev_init()` 或 `cdev_alloc()`

| | `cdev_init(cdev, fops)` | `cdev_alloc()` |
|---|---|---|
| 内存 | **你的**（通常内嵌在你的结构里） | 核心 `kzalloc` |
| 位置 | `fs/char_dev.c:651` | `fs/char_dev.c:633` |
| `memset` | **有**（`fs/char_dev.c:653`） | `kzalloc` 已经清零 |
| `INIT_LIST_HEAD(&list)` | 有（`fs/char_dev.c:654`） | 有（`fs/char_dev.c:637`） |
| kobj_type | **`ktype_cdev_default`**（`fs/char_dev.c:655`） | **`ktype_cdev_dynamic`**（`fs/char_dev.c:638`） |
| 设 `ops` | 有（`fs/char_dev.c:656`） | **没有** —— 你自己填 |
| 设 `owner` | **没有** —— 你自己填 `THIS_MODULE` | 也没有 |

**两种 kobj_type 的差别就是"释放时要不要 `kfree`"**（§B2）。

⚠️ **`cdev_init()` 里那句 `memset(cdev, 0, sizeof *cdev)`（`fs/char_dev.c:653`）
意味着它会清掉你在它之前设的任何字段** ——
所以 `cdev.owner = THIS_MODULE` 必须写在 `cdev_init()` **之后**。

## §A4 驱动 probe（三）：`cdev_device_add()`

```
   cdev_device_add(cdev, dev)
     ├─ dev->devt 非 0？
     │    ├─ cdev_set_parent(cdev, &dev->kobj)
     │    │    └─ WARN_ON(!kobj->state_initialized)
     │    │       p->kobj.parent = kobj
     │    └─ cdev_add(cdev, dev->devt, 1)
     │         ├─ p->dev = dev; p->count = count
     │         ├─ WARN_ON(dev == WHITEOUT_DEV) ⇒ -EBUSY
     │         ├─ kobj_map(cdev_map, dev, count, NULL, exact_match, exact_lock, p)
     │         │    ├─ n = 跨几个 major（最多 255）
     │         │    ├─ kmalloc_array(n, sizeof(struct probe))
     │         │    ├─ 填 n 个 probe
     │         │    └─ mutex_lock → 每个桶按 range 升序插入 → unlock
     │         └─ kobject_get(p->kobj.parent)     抓住 device
     └─ device_add(dev)
          ├─ sysfs 目录 + /sys/class/<name>/ 链接
          ├─ kobject_uevent(KOBJ_ADD) ⇒ udev
          └─ devtmpfs_create_node(dev)            /dev 节点在这里出现
     失败 ⇒ cdev_del(cdev)
```

| 关键位置 | 行号 |
|---|---|
| `cdev_device_add()` | `fs/char_dev.c:537` |
| `dev->devt` 判断 | `fs/char_dev.c:541` |
| `cdev_set_parent()` 调用 | `fs/char_dev.c:542` |
| `cdev_add()` 调用 | `fs/char_dev.c:544` |
| `device_add()` 调用 | `fs/char_dev.c:549` |
| 失败回滚 | `fs/char_dev.c:550-551` |
| `cdev_set_parent()` 实现 | `fs/char_dev.c:508`；`WARN_ON` 在 `:510`，赋值在 `:511` |
| `cdev_add()` 实现 | `fs/char_dev.c:479` |
| `WARN_ON(dev == WHITEOUT_DEV)` | `fs/char_dev.c:485-487` |
| `kobj_map()` 调用 | `fs/char_dev.c:489-490` |
| `kobject_get(p->kobj.parent)` | `fs/char_dev.c:494` |
| `kobj_map()` 实现 | `drivers/base/map.c:32` |
| 跨 major 数计算 + 上限 255 | `drivers/base/map.c:35`、`drivers/base/map.c:41-42` |
| `kmalloc_array` | `drivers/base/map.c:44` |
| 按 `range` 升序插入 | `drivers/base/map.c:56-62` |
| `devtmpfs_create_node()` | `drivers/base/devtmpfs.c:121` |

### §A4.1 ⚠️`cdev_add()` 成功之后你的 fops 就活了

`cdev_device_add()` 的函数注释最后一段（`fs/char_dev.c:534-535`）：

> "NOTE: Callers must assume that userspace was able to open the cdev and
> can call cdev fops callbacks at any time, **even if this function fails**."

⇒ **两个实际后果**：

| 后果 | 说明 |
|---|---|
| `cdev_add()` 到 `device_add()` 之间有一个窗口 | 此时 `/dev` 节点还没有，但**已存在的节点**（上次没删干净、或手工 `mknod` 的）能打开 |
| `cdev_device_add()` **返回失败也可能有 fd 已经打开了** | ⇒ 你的清理路径不能直接 `kfree` 私有数据 |

**正确的做法**：把私有数据的生命周期交给 `struct device` 的 release 回调
（05 §1 会给写法），而不是在 probe 的错误路径里 `kfree`。

### §A4.2 `kobj_map()` 的跨 major 处理

`drivers/base/map.c:35` 算出这段设备号跨几个 major：
```c
	unsigned int n = MAJOR(dev + range - 1) - MAJOR(dev) + 1;
```
然后 `if (n > 255) n = 255;`（`drivers/base/map.c:41-42`），
分配 `n` 个 `probe` 并**逐个插进对应的桶**（`drivers/base/map.c:56-62`）。

⚠️ **只 `kfree` 一个**：`kobj_unmap()` 里 `found` 只记第一个
（`drivers/base/map.c:87-88`），最后 `kfree(found)`（`drivers/base/map.c:93`）——
因为那 `n` 个是**一次 `kmalloc_array` 出来的连续内存**，第一个就是数组首地址。

**但这有个前提：`kobj_unmap()` 必须以和 `kobj_map()` 完全相同的 `dev`+`range` 调用**
（`drivers/base/map.c:82` 的精确匹配）。
⇒ `cdev_del()` 用的是 `p->dev` 和 `p->count`（`fs/char_dev.c:596`），正好是 `cdev_add()` 存进去的。

## §A5 用户态 `open()`：两级查找 + 一次替换

```
   open("/dev/mydev")
     │
     ├─ VFS：路径解析 ⇒ inode（S_ISCHR，i_rdev = 设备号）
     ├─ VFS：f_op = def_chr_fops
     └─ def_chr_fops.open = chrdev_open
          │
          ├─ spin_lock(&cdev_lock)
          ├─ p = inode->i_cdev
          │
          ├─【缓存未命中】
          │    ├─ spin_unlock(&cdev_lock)          必须先解锁
          │    ├─ kobj = kobj_lookup(cdev_map, inode->i_rdev, &idx)
          │    │    ├─ 遍历 probes[MAJOR % 255]（按 range 升序）
          │    │    ├─ 区间不含 ⇒ continue
          │    │    ├─ range 不更短 ⇒ break
          │    │    ├─ try_module_get(p->owner)     cdev 传的是 NULL
          │    │    ├─ p->lock(dev, data) = exact_lock ⇒ cdev_get()
          │    │    ├─ mutex_unlock
          │    │    ├─ probe(dev, index, data) = exact_match ⇒ &cdev->kobj
          │    │    └─ 返回 NULL ⇒ goto retry
          │    ├─ !kobj ⇒ -ENXIO
          │    ├─ new = container_of(kobj, struct cdev, kobj)
          │    ├─ spin_lock(&cdev_lock)
          │    ├─ 复查 i_cdev（别人可能抢先）
          │    │    ├─ 还是空 ⇒ i_cdev = new；list_add(&inode->i_devices, &p->list)
          │    │    └─ 被抢了 ⇒ cdev_get(p) 失败则 -ENXIO
          │    └─ cdev_put(new)                    抢输了就放掉
          │
          ├─【缓存命中】cdev_get(p) 失败 ⇒ -ENXIO
          ├─ spin_unlock(&cdev_lock)
          │
          ├─ ret = -ENXIO
          ├─ fops = fops_get(p->ops)               = try_module_get(ops->owner)
          ├─ !fops ⇒ goto out_cdev_put
          ├─ replace_fops(filp, fops)              换成你的
          └─ filp->f_op->open ⇒ 调你的 open()
               失败 ⇒ goto out_cdev_put
```

| 关键位置 | 行号 |
|---|---|
| `chrdev_open()` | `fs/char_dev.c:373` |
| `spin_lock` | `fs/char_dev.c:380` |
| 读缓存 | `fs/char_dev.c:381` |
| 先解锁 | `fs/char_dev.c:385` |
| `kobj_lookup()` 调用 | `fs/char_dev.c:386` |
| `-ENXIO`（查不到） | `fs/char_dev.c:387-388` |
| `container_of` | `fs/char_dev.c:389` |
| 重新加锁 | `fs/char_dev.c:390` |
| 复查的注释 | `fs/char_dev.c:391-392` |
| 复查 | `fs/char_dev.c:393` |
| 填缓存 | `fs/char_dev.c:395` |
| 挂反向链表 | `fs/char_dev.c:396` |
| 被抢 ⇒ `-ENXIO` | `fs/char_dev.c:398-399` |
| 缓存命中但拿不到引用 ⇒ `-ENXIO` | `fs/char_dev.c:400-401` |
| 解锁 | `fs/char_dev.c:402` |
| `cdev_put(new)` | `fs/char_dev.c:403` |
| `ret = -ENXIO` 预设 | `fs/char_dev.c:407` |
| `fops_get()` | `fs/char_dev.c:408` |
| `replace_fops()` | `fs/char_dev.c:412` |
| 调你的 `open` | `fs/char_dev.c:413-416` |
| `out_cdev_put:` | `fs/char_dev.c:421-423` |
| `kobj_lookup()` | `drivers/base/map.c:95` |
| `exact_match` / `exact_lock` | `fs/char_dev.c:457` / `:463` |

**三层引用在这条路上被依次拿到**：

| 层 | 谁拿 | 位置 |
|---|---|---|
| ① `cdev->kobj` 的 kref | `exact_lock` → `cdev_get()` → `kobject_get_unless_zero()` | `fs/char_dev.c:355` |
| ② `cdev->owner` 的模块引用 | 同上 → `try_module_get()` | `fs/char_dev.c:353` |
| ③ `ops->owner` 的模块引用 | `fops_get()` | `fs/char_dev.c:408` |

⚠️ **② 和 ③ 是两次不同的 `try_module_get`** ——
一次针对 `cdev.owner`、一次针对 `fops.owner`。
⇒ **两个都要填 `THIS_MODULE`**（02 §5.1）。
只填一个的话保护是残缺的：只填 `fops.owner` ⇒ `cdev` 对象本身没保护。

`kobject_get_unless_zero()`（`fs/char_dev.c:355`）而不是 `kobject_get()` ——
这是"cdev 正在被销毁（kref 已归零）"时的正确处理：**返回 NULL 而不是复活它**。

## §A6 read / write / ioctl：完全不经过字符设备层

`replace_fops()` 之后 `filp->f_op` 就是你的表了。
⇒ **`fs/char_dev.c` 在 `open()` 返回后就彻底退出了这条路** ——
后续所有系统调用都是 `VFS → 你的 fops`，中间没有字符设备层。

| 系统调用 | VFS 入口 | 落到 |
|---|---|---|
| `read()` | `vfs_read()` | `f_op->read` 或 `f_op->read_iter` |
| `write()` | `vfs_write()` | `f_op->write` / `write_iter` |
| `ioctl()` | `do_vfs_ioctl()` → `vfs_ioctl()` | `f_op->unlocked_ioctl` |
| `mmap()` | `vm_mmap_pgoff()` | `f_op->mmap` |
| `poll()` | `do_poll()` | `f_op->poll` |

⇒ **这解释了字符设备层为什么只有 685 行**（01 §0.1）：
它只负责"把 `open()` 路由到正确的 fops"这一件事。

## §A7 `close()`：`release` 不是每次都调

### §A7.1 `__fput()` 里的三步

`chrdev_open()` 里那次 `cdev_get()` 的引用**是在 VFS 的 `__fput()` 里放掉的**
（不在 `fs/char_dev.c` 里，所以只看字符设备层会以为它泄漏了）：

| 步骤 | 动作 | 位置 |
|---|---|---|
| ① | `file->f_op->release(inode, file)` —— **调你的 release** | `fs/file_table.c:319-320` |
| ② | `S_ISCHR && i_cdev != NULL && !FMODE_PATH` ⇒ **`cdev_put(inode->i_cdev)`** | `fs/file_table.c:321-324` |
| ③ | `fops_put(file->f_op)` —— 放掉 `ops->owner` 的模块引用 | `fs/file_table.c:325` |

**注意顺序**：**你的 `release` 先跑，然后才放 cdev 和模块引用** ——
所以 `release` 里访问私有数据是安全的。

`!(mode & FMODE_PATH)` 那个条件排除了 `O_PATH` 打开的 fd ——
那种 fd 从来没走过 `chrdev_open()`，所以没有引用要放。

### §A7.2 inode 回收时：`cd_forget()`

| 元素 | 位置 |
|---|---|
| `evict()` 里的调用 | `fs/inode.c:710-711`（条件是 `S_ISCHR(i_mode) && i_cdev`） |
| `cd_forget()` 实现 | `fs/char_dev.c:426` |

`cd_forget()` 做三件事（`fs/char_dev.c:428-432`）：
`list_del_init(&inode->i_devices)` · `i_cdev = NULL` · `i_mapping = &i_data`。

⚠️ **它不做 `kobject_put()`** —— 因为 `i_cdev` 里存的那个引用
**已经在 `__fput()` 里按每个 `struct file` 配平了**（§A7.1 步骤 ②）。
⇒ `i_cdev` 只是一个**缓存指针**，不额外持有引用。

**完整的配平关系**：

| `cdev_get()` 的时机 | 对应的 `cdev_put()` |
|---|---|
| 每次 `open()` 成功（`fs/char_dev.c:355` 经 `exact_lock`，或缓存命中时 `fs/char_dev.c:400`） | 每次 `__fput()`（`fs/file_table.c:323`）—— **一对一** |
| `open()` 中途失败 | `fs/char_dev.c:422` 的 `out_cdev_put` |
| 缓存竞争抢输了 | `fs/char_dev.c:403` 的 `cdev_put(new)` |

⇒ **本套一开始误判成"疑似泄漏"，查到 `fs/file_table.c:323` 才确认配平** ——
这条记进了 99 §6 的交叉验证实例。

## §A8 拆除

```
   驱动 remove
     ├─ cdev_device_del(cdev, dev)
     │    ├─ device_del(dev)
     │    │    ├─ devtmpfs_delete_node(dev)   ⇒ /dev 节点消失
     │    │    └─ uevent(KOBJ_REMOVE)
     │    └─ dev->devt 非 0 ⇒ cdev_del(cdev)
     │         ├─ cdev_unmap(p->dev, p->count) ⇒ kobj_unmap(cdev_map, …)
     │         │    ⇒ 此后 open() 一律 -ENXIO
     │         └─ kobject_put(&p->kobj)
     │              └─ kref 归零 ⇒ ktype 的 release
     │                   ├─ ktype_cdev_default  ⇒ cdev_default_release()
     │                   │    · cdev_purge(p)     清所有 inode 的缓存
     │                   │    · kobject_put(parent)
     │                   └─ ktype_cdev_dynamic  ⇒ cdev_dynamic_release()
     │                        · cdev_purge(p) · kfree(p) · kobject_put(parent)
     └─ unregister_chrdev_region(devt, N)
          └─ 按 major 切段 ⇒ __unregister_chrdev_region() ⇒ kfree
```

| 关键位置 | 行号 |
|---|---|
| `cdev_device_del()` | `fs/char_dev.c:571` |
| `cdev_del()` | `fs/char_dev.c:594` |
| `cdev_unmap()` | `fs/char_dev.c:578` |
| `kobject_put(&p->kobj)` | `fs/char_dev.c:597` |
| `cdev_default_release()` | `fs/char_dev.c:601` |
| `cdev_dynamic_release()` | `fs/char_dev.c:610` |
| `ktype_cdev_default` | `fs/char_dev.c:620-622` |
| `ktype_cdev_dynamic` | `fs/char_dev.c:624-626` |
| `cdev_purge()` | `fs/char_dev.c:435` |
| `unregister_chrdev_region()` | `fs/char_dev.c:311` |
| `__unregister_chrdev_region()` | `fs/char_dev.c:172` |
| `kobj_unmap()` | `drivers/base/map.c:68` |

### §A8.1 `cdev_del()` 的注释说清了最关键的语义

`fs/char_dev.c:585-592` 那段注释：

> "NOTE: This guarantees that cdev device will no longer be able to be opened,
> however **any cdevs already open will remain and their fops will still be
> callable even after cdev_del returns**."

⇒ **`cdev_del()` 返回之后，你的 fops 仍然可能被调用。**

| 保证 | 不保证 |
|---|---|
| 新的 `open()` 会失败（`-ENXIO`） | **已打开的 fd 上的 read/write/ioctl/release 还会来** |

⇒ **这是字符设备驱动最重要的一条生命周期纪律**：
`cdev_del()` **不是**"安全点"。私有数据必须活到最后一个 fd 关闭
（靠 `cdev_set_parent()` + `struct device` 的 release，01 §3.3、05 §1）。

### §A8.2 顺序：先 `device_del()` 还是先 `cdev_del()`？

`cdev_device_del()` 的实现顺序是 **先 `device_del()`，后 `cdev_del()`**
（和 `cdev_device_add()` 正好相反）。

**理由**：先让 `/dev` 节点消失（阻止新的 `open()` 找到路径），
再让设备号失效（阻止已有节点被 open）。
⇒ 两道门按"外层先关"的顺序关。

---

# Part B：横切机制

## §B1 两把锁

### §B1.1 清单（02 §6.2 的机制展开）

| 锁 | 位置 | 类型 | 保护 | 最长临界区 |
|---|---|---|---|---|
| `chrdevs_lock` | `fs/char_dev.c:30` | **mutex** | `chrdevs[]` + `cdev_map` | `kobj_lookup()` 里包含 `p->lock()` 回调 |
| `cdev_lock` | `fs/char_dev.c:346` | **spinlock** | `inode->i_cdev` + `cdev->list` | 几行 |

### §B1.2 唯一的锁序约束

```
   ❌ 不能：spin_lock(&cdev_lock) → mutex_lock(&chrdevs_lock)
   ✅ 只能：先放掉 cdev_lock，再去拿 chrdevs_lock
```

`chrdev_open()` 就是这么做的（`fs/char_dev.c:385` 解锁 → `:386` 查表 → `:390` 重新加锁），
配套的复查在 `fs/char_dev.c:393`，注释在 `fs/char_dev.c:391-392`。

**这是本子系统唯一的并发难点**，也是唯一一处"解锁-重查"模式（02 §6.3）。

### §B1.3 ⚠️ `kobj_lookup()` 在锁内外各调一个回调

| 回调 | 在锁内还是锁外 | 位置 |
|---|---|---|
| `p->lock()`（= `exact_lock`） | **锁内**（持 `chrdevs_lock`） | `drivers/base/map.c:119` |
| `p->get()`（= `exact_match`） | **锁外**（`drivers/base/map.c:123` 先解锁） | `drivers/base/map.c:124` |

⇒ **`exact_lock` 里绝对不能再碰 `chrdevs_lock`**（会死锁）。
cdev 的实现只有一行 `cdev_get()`（`fs/char_dev.c:463-467`），安全。

⚠️ 但 `base_probe()`（兜底项的 `get`）**在锁外**被调 ——
它要 `request_module()`，那会睡很久。
⇒ **这个"锁外调 probe"的设计就是为了让 `base_probe` 能睡**（§B5）。

## §B2 两种 `kobj_type`：谁负责 `kfree`

| kobj_type | 位置 | release 回调 | 做什么 |
|---|---|---|---|
| `ktype_cdev_default` | `fs/char_dev.c:620-622` | `cdev_default_release()`（`fs/char_dev.c:601`） | `cdev_purge()` + `kobject_put(parent)`；**不 `kfree`** |
| `ktype_cdev_dynamic` | `fs/char_dev.c:624-626` | `cdev_dynamic_release()`（`fs/char_dev.c:610`） | `cdev_purge()` + **`kfree(p)`** + `kobject_put(parent)` |

| 谁装上哪个 | 位置 |
|---|---|
| `cdev_init()` ⇒ `default`（内存是你的） | `fs/char_dev.c:655` |
| `cdev_alloc()` ⇒ `dynamic`（内存是核心的） | `fs/char_dev.c:638` |

**这就是 01 §3.1 那条"`cdev_init()` 和 `cdev_alloc()` 二选一"的实现原因**：
装错了 kobj_type 就意味着 `kfree` 一块不该 free 的内存（或者反之内存泄漏）。

两个 release 都做 `kobject_put(parent)`（`fs/char_dev.c:607` / `:617`）——
和 `cdev_add()` 里那句 `kobject_get(p->kobj.parent)`（`fs/char_dev.c:494`）配对。

## §B3 三层引用计数

```
   ┌── ① struct cdev 的 kobj（kref）─────────────────────────┐
   │   +1：cdev_init/alloc（初值 1）                          │
   │   +1：每次 open 成功（exact_lock → cdev_get）             │
   │   -1：cdev_del（kobject_put）                            │
   │   -1：？  见 §C2                                     │
   │   归零 ⇒ ktype 的 release ⇒ cdev_purge (+ kfree)         │
   └─────────────────────────────────────────────────────────┘

   ┌── ② cdev->owner 模块引用 ───────────────────────────────┐
   │   +1：每次 open（cdev_get → try_module_get）              │
   │   -1：cdev_put（module_put）                             │
   └─────────────────────────────────────────────────────────┘

   ┌── ③ cdev->ops->owner 模块引用 ──────────────────────────┐
   │   +1：fops_get（chrdev_open）                            │
   │   -1：fops_put（VFS 的 __fput）                          │
   └─────────────────────────────────────────────────────────┘

   ┌── ④ parent kobject（通常是 struct device）─────────────┐
   │   +1：cdev_add                                          │
   │   -1：cdev 的 release                                   │
   └─────────────────────────────────────────────────────────┘
```

| 关键位置 | 行号 |
|---|---|
| ① 初值：`kobject_init` | `fs/char_dev.c:655`（default）· `fs/char_dev.c:638`（dynamic） |
| ① `kobject_get_unless_zero` | `fs/char_dev.c:355` |
| ① `cdev_del` 的 `kobject_put` | `fs/char_dev.c:597` |
| ② `try_module_get` | `fs/char_dev.c:353` |
| ② `module_put`（在 `cdev_put` 里） | `fs/char_dev.c:361-368` |
| ③ `fops_get` | `fs/char_dev.c:408` |
| ④ `kobject_get(parent)` | `fs/char_dev.c:494` |
| ④ `kobject_put(parent)` | `fs/char_dev.c:607` · `fs/char_dev.c:617` |

**③ 是保护"模块被卸载但 fd 还开着"的主力**（VFS 层管），
② 是保护"`cdev` 对象所在的模块"（字符设备层管）。
⇒ 通常两者是同一个模块，所以看起来重复；
但如果 `cdev` 和 `fops` 来自不同模块（如 misc 设备：`cdev` 属于 misc.ko、
`fops` 属于你的模块），两者就分开了 —— **这才是它们必须是两个计数的原因**。

## §B4 `kobj_lookup()` 的最长匹配

02 §3.3 已展开机制。这里只补一条**为什么需要它**：

| 场景 | 谁匹配 |
|---|---|
| 主设备号 10、minor 5 已被某个 `cdev_add(dev, 1)` 登记 | 那个 `range = 1` 的 probe（**最短**，排最前） |
| 主设备号 10 的其它 minor | 兜底项（`range = ~0`，排最后）⇒ `base_probe` ⇒ 试着加载模块 |

**"区间越短越精确"** ⇒ 链表按 `range` 升序（`drivers/base/map.c:58-59`），
找到第一个匹配的就返回（`drivers/base/map.c:110-111` 的 `break` 保证不会再看更长的）。

## §B5 模块自动加载：`char-major-N-M`

```
   open("/dev/foo")  —— 主设备号 N、次设备号 M，但没人 cdev_add
     └─ kobj_lookup() 走到兜底项
          └─ base_probe(dev, &idx, NULL)
               ├─ request_module("char-major-%d-%d", N, M)
               ├─ 返回 > 0 ⇒ 再试 request_module("char-major-%d", N)
               └─ return NULL
     └─ kobj_lookup: kobj == NULL ⇒ goto retry
          └─ 重新遍历 —— 新模块已经 cdev_add 过了 ⇒ 命中
```

| 元素 | 位置 |
|---|---|
| `base_probe()` | `fs/char_dev.c:659` |
| 第一次 `request_module` | `fs/char_dev.c:661` |
| 老式 2.4 别名的兜底 | `fs/char_dev.c:662-663`（注释原文 "Make old-style 2.4 aliases work"） |
| `return NULL` | `fs/char_dev.c:664` |
| `goto retry` | `drivers/base/map.c:129` |

**模块侧要声明的别名**：`MODULE_ALIAS("char-major-10-235")` 之类。
misc 设备有专门的宏（`include/linux/miscdevice.h:109`，01 §7.2）。

⚠️ **`goto retry` 是无限循环的风险点**：如果 `request_module` 每次都"成功"
（返回 > 0 表示失败，返回 0 表示成功但模块可能没注册设备号），
理论上会一直重试。
⇒ 实际上 `request_module` 对**同一个别名**的第二次调用会因为
"模块已加载"而快速返回，而 `base_probe` 仍返回 NULL ⇒ 再 retry ⇒ 还是走到兜底项……
**这一条本套没有实测**（99 §5.3），只指出代码结构上存在这个路径。

## §B6 `/proc/devices` 的内容

`chrdev_show()`（`fs/char_dev.c:51`）被 `fs/proc/devices.c` 调：

```c
	mutex_lock(&chrdevs_lock);
	for (cd = chrdevs[major_to_index(offset)]; cd; cd = cd->next) {
		if (cd->major == offset)
			seq_printf(f, "%3d %s\n", cd->major, cd->name);
	}
	mutex_unlock(&chrdevs_lock);
```

| 元素 | 位置 |
|---|---|
| 函数 | `fs/char_dev.c:51` |
| `#ifdef CONFIG_PROC_FS` | `fs/char_dev.c:47` |
| 遍历桶链 | `fs/char_dev.c:55` |
| 只打 `major == offset` 的 | `fs/char_dev.c:56` |
| 输出格式 `"%3d %s\n"` | `fs/char_dev.c:57` |

**`fs/char_dev.c:56` 那个 `if` 是撞桶的后果**（§A2.1）：
一个桶里可能有多个不同 major 的条目，所以要过滤。

⚠️ **同一个 major 上多次 `alloc_chrdev_region()` 会打出多行**（每个 `char_device_struct` 一行）。
⇒ `/proc/devices` 里同一个主设备号出现多次是正常的。

---

# Part C：评价

## §C1 源码里的自陈问题

```bash
grep -n 'TODO\|FIXME\|XXX\|REVISIT\|HACK\|will die\|NOTE:' \
     fs/char_dev.c drivers/base/map.c include/linux/cdev.h
```

| 标记 | 个数 | 位置 |
|---|---|---|
| `TODO` / `FIXME` / `XXX` / `REVISIT` / `HACK` | **0** | —— |
| `/* will die */` | **1** | `fs/char_dev.c:40` |
| `NOTE: data structure needs to be changed` | **1** | `drivers/base/map.c:7` |
| `NOTE:`（文档性质，不是债） | 3 | `fs/char_dev.c:534` · `:567` · `:590` |

⇒ **零 `FIXME`，但有两条"换了说法的技术债"** —— 分别是 §C2 和 §C3。

## §C2 `/* will die */`：老式 API 的遗留字段

`fs/char_dev.c:40`：
```c
	struct cdev *cdev;		/* will die */
```

| 事实 | 说明 |
|---|---|
| 谁写它 | 只有 `__register_chrdev()`（`fs/char_dev.c:292`） |
| 谁读它 | 只有 `__unregister_chrdev()`（`fs/char_dev.c:341-342`） |
| 为什么"该死" | 因为它让 `char_device_struct`（**设备号登记表**的元素）
知道了 `cdev`（**对象层**的东西）—— **两层职责混在一起了**（02 §1.0.D） |

**实际代价**：`register_chrdev()` 的使用者拿不到那个 `cdev`（01 §4.1），
所以**没法用 `cdev_set_parent()` 做生命周期绑定** ——
这是老式 API 的一个真实功能缺失，不只是风格问题。

严重程度：低（新代码不该用老式 API），但它解释了为什么这个字段还在。

## §C3 `drivers/base/map.c` 的自陈：数据结构该换了

`drivers/base/map.c:7-9` 的文件头注释：

> "NOTE: data structure needs to be changed. It works, but **for large dev_t
> it will be too slow**. It is isolated, though, so these changes will be
> local to that file."

| 事实 | 说明 |
|---|---|
| 数据结构是什么 | **255 桶的哈希 + 每桶一条按 `range` 升序的单链表**（`drivers/base/map.c:19-30`，02 §5.3） |
| 慢在哪 | 主设备号空间是 **512**（`include/linux/fs.h:2896`）而桶只有 255 ⇒ 必然撞桶；而 minor 空间有 **2²⁰**，一个 major 上可以有大量不同区间的 probe ⇒ **链表可能很长** |
| 注释的日期 | 版权行是 "Al Viro 2002,2003"（`drivers/base/map.c:5`）⇒ **这条注释挂了 20 年** |
| 为什么没人改 | 注释自己说了：`kobj_lookup()` 只在**每个 inode 的第一次 open** 时走（§A5），不是热路径 |

**实际影响的判据**：一台机器上"同一个主设备号下有多少个独立的 `cdev_add()` 区间"。

| 场景 | 链表长度 |
|---|---|
| 普通嵌入式系统 | 几个到几十个 |
| 大量 tty / pty | 几百个（但 pty 用的是同一个 `cdev` 的大 range，不是很多个小 range） |
| 极端：几千个独立设备共享一个 major | 链表就是几千长 |

结论：**在 arm64 嵌入式场景下这条注释不构成实际问题**，
但它是理解 `kobj_lookup()` 为什么长这样的关键背景（§B4）。

## §C4 三处"名字容易误导"

| 名字 | 为什么误导 | 出处 |
|---|---|---|
| `char_device_struct` | **名字里有 `device` 但和 `struct device` 毫无关系** —— 它是设备号登记表的元素 | 02 §4 |
| `inode->i_devices` | 名字像"这个 inode 的设备列表"，实际是"我这个 inode **挂在某个 `cdev` 的链上的节点**" | 02 §1.0.C |
| `register_chrdev()` vs `register_chrdev_region()` | **差一个词，语义差很远**（一步到位 vs 只占号） | 01 §4 · 02 §1.0.E |

这三条都不是 bug，但它们是本子系统"读起来困惑"的主要来源（08 篇会用类比讲）。

## §C5 整体评价

| 维度 | 评价 |
|---|---|
| **代码量与复杂度的比** | 961 行（01 §0.1）做完了"把设备号路由到 fops"这件事，而且**零 `FIXME`** |
| **抽象的清晰度** | ☆ 设备号层 / 对象层 / 查找层三层分得很干净；⚠️ 唯一的污点是 `/* will die */` 那个跨层字段（§C2） |
| **文档质量** | ☆☆ ⚠️ **`Documentation/` 下没有任何字符设备层的框架文档** —— 只有 `Documentation/admin-guide/devices.txt`（3086 行的设备号分配表）。三条最重要的语义（`cdev_del` 之后 fops 仍可调、`cdev_device_add` 失败也可能已被打开、`register_chrdev` 的返回值）**全靠源码里的 `NOTE:` 注释**（`fs/char_dev.c:534` · `:567` · `:590`） |
| **可观测性** | ☆☆☆ ⚠️ **`cdev_map` 完全没有出口** —— 没有 procfs / sysfs / debugfs 能看到"哪些设备号被 `cdev_add()` 过"（02 §1.0.A）。⇒ 只能靠 `open()` 是否 `ENXIO` 反推（07 §4.3） |
| **错误码的信息量** | 只有 11 个点、4 种码、语义完全不重叠（01 §8.1） |
| **命名** | ☆☆ ⚠️ 三处误导（§C4） |
| **历史包袱** | ☆ 只有两条：`/* will die */`（§C2）和 20 年前的数据结构注释（§C3）。都不影响正确性 |
| **生命周期语义的难度** | ☆☆☆（**难**）⚠️`cdev_del()` 之后 fops 仍可调（§A8.1）+ `cdev_device_add()` 失败也可能已被打开（§A4.1）+ 三层引用计数跨三个文件配平（§B3、§A7.1）—— **这是本子系统真正的难点，而且文档没写** |

**一句话总结**：这是内核里**代码最短、抽象最干净、但生命周期语义最容易写错**的子系统之一。
它的难度不在读懂 961 行，而在**理解那三条 `NOTE:` 注释的含义** ——
而这三条恰好是唯一的官方说明。

---

**下一篇**：04 篇讲四个互不调用的独立框架（devtmpfs / udev-uevent / misc 设备 / `/proc/devices`）。
