---
title: 费曼学习法
---

# cdev 08：用大白话讲清最难理解的几组关系

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇的写法：每个主题四段 —— **问题** / **先举个例子** / **回到代码** /
> **这个类比什么时候会误导你？**
> 最后那一段是重点：类比只是脚手架，知道它在哪里塌才是真正的理解。
>
> ⚠️ 所有 `文件:行号` 都写完整路径。

---

## §1 设备号、cdev、`/dev` 节点为什么是三件独立的事？

> **这是本子系统最容易被类比错的一组关系**，先讲它。

### 先举个例子

开一家门店，要办三件事：

| # | 事 | 谁办 | 办完能看到什么 | 少了它 |
|---|---|---|---|---|
| ① | **去工商局把"门牌号 88 号"登记到自己名下** | 工商局 | 登记簿上有一行 | 别人可能也在 88 号开店 |
| ② | **在门口挂上"营业中"的牌子，并告诉总台"88 号的电话转给我"** | 你 | **什么都看不到** —— 只有总台的内部转接表里多了一条 | 客人打 88 号的电话，总台说"查无此号" |
| ③ | **在街上装一块指向你家的路牌** | 市政 | 街上有块牌子 | 客人**找不到路** |

三件事**互不知道对方**：
- 只办 ①：登记簿上有你，但电话打不通、街上没牌子；
- 只办 ①②：电话能转接了，但**没人知道路在哪**；
- 只办 ①③：路牌指过来了，客人到了门口，**总台说查无此号**。

### 回到代码

| # | 动作 | API | 效果在哪看 | 少了它 |
|---|---|---|---|---|
| ① | 占设备号 | `alloc_chrdev_region()`（`fs/char_dev.c:236`） | `/proc/devices`（`fs/char_dev.c:51`） | 设备号冲突 |
| ② | 让设备号"活" | **`cdev_add()`**（`fs/char_dev.c:479`） | **哪里都看不到** | `open()` 返回 **`ENXIO`**（`fs/char_dev.c:388`） |
| ③ | 建 `/dev` 节点 | `class_create()` + `device_create()`（或 `misc_register()`） | `/dev/` + `/sys/class/` | **没有节点可以 open** |

| "登记簿" vs "总台转接表" | 位置 |
|---|---|
| 登记簿 = `chrdevs[255]` | `fs/char_dev.c:41` |
| 总台转接表 = `cdev_map` | `fs/char_dev.c:28` |
| 两张表**共用一把锁但不共用数据** | `fs/char_dev.c:669`（把 `&chrdevs_lock` 传给 `kobj_map_init()`） |

**`open()` 走的是"总台转接表"**（`fs/char_dev.c:386` 的 `kobj_lookup(cdev_map, …)`），
**不是登记簿** ⇒ 这就是"`/proc/devices` 里有你的主设备号但 `open()` 还是 `ENXIO`"的全部原因。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"登记簿是公开的，转接表也该能查"—— 转接表查不了**。
   `cdev_map` **没有任何 procfs / sysfs / debugfs 出口**（03 §C5、07 §4.3）。
   ⇒ 类比里你可以去总台问一句，代码里**只能靠打电话试**（`open()` 是否 `ENXIO`）。

2. ⚠️ **"路牌是市政装的，跟你无关"—— 节点名和默认权限是你定的**。
   `device_get_devnode()`（`drivers/base/core.c:3972`）会问你的
   `class->devnode` 回调（`drivers/base/core.c:3987-3988`）——
   ⇒ 名字、权限、甚至子目录都在你的控制下（06 §5）。

3. ⚠️ **"三件事可以任意顺序办"—— ② 必须在 ③ 之前**。
   `cdev_device_add()`（`fs/char_dev.c:537`）的实现顺序就是
   **先 `cdev_add()`（`fs/char_dev.c:544`），后 `device_add()`（`fs/char_dev.c:549`）** ——
   否则节点一出现就可能被 open，而转接表还没建好。
   拆除时顺序相反（`cdev_device_del()`，03 §A8.2）。

---

## §2 `struct cdev` 和 `struct device` 是什么关系？

### 先举个例子

同一个人，在两个系统里有两条记录：

| 系统 | 记录里有什么 | 谁查它 |
|---|---|---|
| **电话总台的转接表** | "88 号 → 三楼 302 的这台电话机" | **打电话的人**（`open()`） |
| **公司的人事档案** | 姓名、部门、工位、上级是谁 | **HR 和访客**（用户态 / udev / sysfs） |

两条记录**描述同一个人**，但：
- 内容完全不同（一条是"电话怎么转"，一条是"他是谁"）；
- 谁也不"包含"谁；
- 唯一的联系：**转接表那条记录上写了一句"此人在人事档案第 N 号"** ——
  这样人事档案就不会在电话还能转接的时候被销毁。

### 回到代码

| | `struct cdev` | `struct device` |
|---|---|---|
| 字段数 | **6 个**（`include/linux/cdev.h:14-21`） | 几十个 |
| 有什么 | `kobj` / `owner` / `ops` / `list` / `dev` / `count` | 名字、class、parent、sysfs、uevent…… |
| 有名字吗 | **没有** | 有（`dev_set_name()`） |
| 谁查它 | **`chrdev_open()`** | 用户态 / udev / sysfs |
| 在 sysfs 里吗 | **不在** | 在 |

**唯一的联系那一行**：
```c
	p->kobj.parent = kobj;
```
| 位置 | 说明 |
|---|---|
| `fs/char_dev.c:511`（在 `cdev_set_parent()` 里，`fs/char_dev.c:508`） | 把 `cdev` 的 kobject 的 parent 指向 `device` 的 kobject |
| 配套的 `kobject_get(parent)` | `fs/char_dev.c:494`（在 `cdev_add()` 里） |
| 配套的 `kobject_put(parent)` | `fs/char_dev.c:607` · `fs/char_dev.c:617`（两个 release 各一次） |
| 函数注释原文 | `fs/char_dev.c:501-507`："so the parent is not freed before the cdev. **This should be called before cdev_add.**" |

⇒ **`cdev_device_add()` 就是"同时办两条记录并把它们连上"的函数**
（`fs/char_dev.c:537`，它内部先 `cdev_set_parent()` 再 `cdev_add()` 再 `device_add()`）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"转接表指向人事档案"暗示方向是单向的 —— 确实是单向的，而且方向反直觉**。
   是**小的（cdev）持有大的（device）的引用**，不是反过来。
   ⇒ 这样才能保证"电话还能转接时，人事档案不会被销毁"（03 §B3 的 ④）。

2. ⚠️ **"两条记录必须都办"—— `cdev` 可以没有 `device`**。
   老式 `register_chrdev()`（`fs/char_dev.c:268`）就只建 `cdev` 不建 `device`
   ⇒ 结果是**没有 `/dev` 节点**（01 §4.1）。

3. ⚠️ **"人事档案里应该记着他的电话"—— `struct device` 里确实有 `devt`，但那只是个数字**。
   `device.devt` 只是设备号的一份拷贝（`cdev_device_add()` 用它，`fs/char_dev.c:544`）——
   **`struct device` 完全不知道有没有 `cdev`**。
   ⇒ 这就是 `/sys/dev/char/` 回答不了"`cdev_add()` 做了没有"的原因（04 §5.2、07 §4.3）。

---

## §3 为什么 `open()` 之后字符设备层就"消失"了？

### 先举个例子

打前台电话叫外卖。

第一次打过去，**接的是总台**：
> "您好，请问找哪个部门？" —— 你说"外卖"。
> "好的，**我把这条线直接接到外卖部，以后您打这个号就是他们接**。"

从这以后，你打这个号码，总台**根本不参与** —— 线路已经被物理改接了。
总台干的活只有一次："第一次接线"。

### 回到代码

```c
	replace_fops(filp, fops);
```
| 元素 | 位置 |
|---|---|
| 那一句 | `fs/char_dev.c:412` |
| 之前 `filp->f_op` 是什么 | `def_chr_fops`（`fs/char_dev.c:452-455`，只有一个 `.open`） |
| 之后是什么 | **你的 `file_operations`** |
| 之后才调你的 `open` | `fs/char_dev.c:413-416` |

⇒ **`open()` 返回之后，`fs/char_dev.c` 彻底退出这条路** ——
所有 `read` / `write` / `ioctl` / `mmap` / `poll` 都是 **VFS → 你的 fops**，
中间没有字符设备层（03 §A6）。

**这就是"685 行做完一整个子系统"的秘诀**（01 §0.1）：
它只负责一次接线。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"总台完全不参与了"—— 它还留着两样东西**：
   ① `inode->i_cdev` 这个缓存（`fs/char_dev.c:395`）；
   ② `cdev` 的引用计数，要等 `__fput()` 才放（`fs/file_table.c:323`）。
   ⇒ 类比里"接完线就走"，代码里**还欠一笔账要在 close 时结**（03 §A7.1）。

2. ⚠️ **"改接是物理的、不可逆的"—— 每个 fd 各改一次**。
   `replace_fops()` 改的是 `filp->f_op`（**这一个 `struct file`**），
   不是 inode 也不是全局。⇒ 你 open 十次，就有十个 `struct file`，各自改一次。

3. ⚠️ **misc 设备是"接了两次线"**（§8）——
   总台先接到 misc 部，misc 部再接到你。
   ⇒ 所以在 misc 的 fops 里 `container_of(inode->i_cdev, …)` 拿到的是
   **misc 的 cdev**，不是你的（05 §2.2）。

---

## §4 为什么 `cdev_del()` 之后 fops 还会被调用？

### 先举个例子

商场撤柜。

**你做的**：把柜台的牌子摘掉、从商场的导购图上删掉。
⇒ **新客人再也找不到你了。**

**但是**：已经在你柜台前排队的三个客人**还在那儿** ——
他们已经进来了，你不能把他们推出去。
⇒ 你必须**等最后一个客人办完事离开**，才能真正拆掉柜台、搬走货架。

### 回到代码

`cdev_del()` 的函数注释（`fs/char_dev.c:585-592`）说得极其明确：

> "NOTE: This guarantees that cdev device will no longer be able to be opened,
> however **any cdevs already open will remain and their fops will still be
> callable even after cdev_del returns**."

| 保证 | 不保证 |
|---|---|
| 新 `open()` 会失败（`ENXIO`） | **已打开的 fd 上的 read/write/ioctl/release 还会来** |

**`cdev_del()` 只做两件事**（`fs/char_dev.c:594-598`）：

| 步 | 动作 | 位置 |
|---|---|---|
| ① | `cdev_unmap()` ⇒ `kobj_unmap(cdev_map, …)` —— **摘牌子** | `fs/char_dev.c:596` |
| ② | `kobject_put(&p->kobj)` —— **放一次引用**（不一定归零！） | `fs/char_dev.c:597` |

⇒ **如果还有 fd 开着，②那次 put 之后引用计数还是 > 0**
（每个开着的 fd 都持一份，`fs/char_dev.c:355` 拿的、`fs/file_table.c:323` 放的）
⇒ release 回调**不会**被调 ⇒ 你的私有数据还活着。

**真正的"最后一个客人走了"信号**是 `cdev` 的 release
（`fs/char_dev.c:601` 或 `:610`），它会 `kobject_put(parent)`
（`fs/char_dev.c:607` / `:617`）⇒ 最终触发 `device.release`。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"排队的客人"暗示你能数出来有几个 —— 数不出来**。
   `cdev->kobj` 的 kref 值不对外暴露。
   ⇒ **你没法在 `remove()` 里等"最后一个 fd 关闭"**，只能把清理动作**挪到 release 回调里**（05 §1.1）。

2. ⚠️ **"撤柜之后就不会有新客人"—— 但 `cdev_device_add()` 失败时也可能已经有客人了**。
   函数注释（`fs/char_dev.c:534-535`）："Callers must assume that userspace was able
   to open the cdev … **even if this function fails**."
   ⇒ 所以 probe 的错误路径**也不能直接 `kfree`**（05 §1.1 那个 `put_device` 分支）。

3. ⚠️ **"你可以先搬货架再摘牌子"—— 顺序反了会出事**。
   `cdev_device_del()`（`fs/char_dev.c:571`）的实现是
   **先 `device_del()`（撤路牌）、后 `cdev_del()`（摘牌子）** ——
   **外层的门先关**（03 §A8.2）。

---

## §5 为什么不能用 `devm_kzalloc()` 存私有数据？

### 先举个例子

租一间办公室，房东提供"退租时自动清空"的服务（= devres）。
听起来很省事：你走的时候不用自己搬东西。

**问题**：退租的时机是"**你解除租约**"，
而不是"**最后一个还在里面办事的客户离开**"（§4）。

⇒ 你解除租约的那一刻，房东就把桌椅搬走了 ——
可里面还有三个客户在用你的桌子。

### 回到代码

| 时机 | 谁触发 |
|---|---|
| devres 释放 | **driver detach**（`remove()` 之后） |
| 私有数据真正能释放 | **最后一个 fd 关闭 + cdev 引用归零 + device.release** |

**两者之间可以隔任意长的时间**（§4）⇒ devm 分配的数据会被**过早释放** ⇒ use-after-free。

**正确的锚点**：

```c
static void mydev_release(struct device *dev)
{
	struct mydev *my = container_of(dev, struct mydev, dev);
	kfree(my);			/* 这里才安全 */
}
	my->dev.release = mydev_release;
```

| 为什么这里安全 | 依据 |
|---|---|
| `cdev` 的 parent 指向 `device`（`cdev_set_parent()`） | `fs/char_dev.c:511` |
| `cdev_add()` 时 `kobject_get(parent)` | `fs/char_dev.c:494` |
| `cdev` 的 release 里才 `kobject_put(parent)` | `fs/char_dev.c:607` / `:617` |
| ⇒ `device.release` **一定在 `cdev` 释放之后** | 这就是整条链的意义 |

真实原型：`drivers/i2c/i2c-dev.c:670` 设 `release`，
`drivers/i2c/i2c-dev.c:94-95` 拆除时 `cdev_device_del()` + `put_device()`。

**可验证的事实**：`cdev` **没有任何 `devm_*` 包装**
（可重跑：`grep -rn 'devm_.*cdev' include/linux/cdev.h include/linux/device.h` → 无输出）。
⇒ **这不是遗漏，是刻意的** —— 因为 devres 的语义根本不适合它。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"所有东西都不能用 devm"—— 硬件资源该用 devm**。
   `devm_ioremap_resource()` 之类在 detach 时释放是**对的** ——
   那时硬件已经不该被访问了。
   ⇒ **判据：这块内存会不会被 fops 回调访问？** 会 ⇒ 不能 devm（05 §1.3）。

2. ⚠️ **"房东的服务一点用都没有"—— devres 仍然管着 probe 失败时的回滚**。
   probe 中途失败时 devres 会按逆序释放已分配的，省掉一大堆 `goto err_*`。
   ⇒ 只有"要活过 detach 的那部分"需要手工管。

---

## §6 `inode->i_cdev` 为什么是个缓存？

### 先举个例子

你家门口的快递柜有个格口号 `A-12`。
第一次去取件，你得**查小票上的取件码 → 到大厅的查询机上查 → 得知是 A-12**。

之后你**把 "A-12" 记在手机备忘录里** ——
下次同一个包裹再去取，直接走到 A-12，不用再查一遍。

⚠️ 但如果**快递柜整个被拆掉换新的**，你的备忘录就成了错的 ——
所以拆柜的人得挨个通知所有记过号的人"作废"。

### 回到代码

| 元素 | 位置 | 作用 |
|---|---|---|
| 缓存字段 `inode->i_cdev` | `include/linux/fs.h:684` | 记住"这个 inode 对应哪个 cdev" |
| 第一次 open 时填 | `fs/char_dev.c:395` | —— |
| 之后直接读 | `fs/char_dev.c:381` | ⇒ 不用再 `kobj_lookup()` |
| **反向链表**（"谁记过我的号"） | `cdev->list`（`include/linux/cdev.h:18`）↔ `inode->i_devices`（`include/linux/fs.h:681`） | —— |
| 第一次 open 时挂上 | `fs/char_dev.c:396` | —— |
| **拆柜时挨个作废** | `cdev_purge()`（`fs/char_dev.c:435`），清每个 inode 的 `i_cdev`（`fs/char_dev.c:442`） | —— |
| inode 自己被回收时摘链 | `cd_forget()`（`fs/char_dev.c:426`），由 `fs/inode.c:711` 调 | —— |

**`cdev_purge()` 就是"拆柜时挨个通知"** ——
它在 `cdev` 的 release 回调里被调（`fs/char_dev.c:606` / `:615`），
遍历 `cdev->list` 把每个 inode 的 `i_cdev` 置 NULL。
⇒ 没有它，下次 open 会用到已释放的 `cdev`。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"备忘录只是个笔记，不占资源"—— `i_cdev` 住在一个 union 里**。
   它和 `i_pipe` / `i_link` / `i_dir_seq` **共享同一块内存**
   （`include/linux/fs.h:682-687`）。
   ⇒ 这是"为什么一个 inode 不可能同时是字符设备和管道"的机器可证形式（02 §1.0.C）。

2. ⚠️ **"记了号就等于占了柜子"—— `i_cdev` 不额外持引用**。
   引用是**每个 `struct file` 一份**（`fs/char_dev.c:355` 拿、`fs/file_table.c:323` 放），
   `i_cdev` 只是个裸指针。
   ⇒ **本套一开始误判成"疑似泄漏"，查到 `fs/file_table.c:323` 才确认配平**（99 §6）。

3. ⚠️ **"查询机很慢所以要缓存"—— `kobj_lookup()` 其实不算慢**。
   缓存的真正价值是**避免每次 open 都拿 `chrdevs_lock`（mutex）**
   （`drivers/base/map.c:102`）——
   ⇒ 高频 open 的设备（如 `/dev/null`）才看得出差别。

---

## §7 为什么要两个 `owner`？

### 先举个例子

一栋楼里，**电话线路**和**接线员**可能属于两家不同的公司：

| 东西 | 属于谁 | 撤走的后果 |
|---|---|---|
| **转接表上那条记录** | 电信公司 | 记录没了 ⇒ 电话转不过去 |
| **实际接电话的那个人** | 外包客服公司 | 人走了 ⇒ 电话通了但没人接 |

⇒ **要保证"电话能用"，得同时不让这两家撤走** —— 所以要**两份合同**。

### 回到代码

| owner | 属于哪个结构 | 谁拿引用 | 位置 |
|---|---|---|---|
| `cdev.owner` | `struct cdev`（`include/linux/cdev.h:16`） | `cdev_get()` → `try_module_get()` | `fs/char_dev.c:353` |
| `fops.owner` | `struct file_operations` | `fops_get()` | `fs/char_dev.c:408` |

| 放的时机 | 位置 |
|---|---|
| `cdev.owner` | `cdev_put()`（`fs/char_dev.c:361-368`），由 `fs/file_table.c:323` 触发 |
| `fops.owner` | `fops_put()`，`fs/file_table.c:325` |

**通常两者是同一个模块，所以看起来重复。但有一种情况它们不同**：

| 场景 | `cdev.owner` | `fops.owner` |
|---|---|---|
| **misc 设备**（§8） | `misc.ko` 的（`drivers/char/misc.c` 里那个 cdev） | **你的模块**（`miscdevice.fops`） |

⇒ **这就是必须是两个计数的原因** ——
misc 场景下"接线员"（你的 fops）和"转接表"（misc 的 cdev）**确实属于两家**。

⚠️ **只填一个的后果**：

| 只填 | 后果 |
|---|---|
| 只填 `fops.owner` | `cdev` 对象本身没保护 ⇒ 用 `cdev_init()` 自建 cdev 时，模块可以在有 fd 时被卸载 |
| 只填 `cdev.owner` | fops 表没保护 |

真实原型 `drivers/i2c/i2c-dev.c` **两个都填**：
`fops.owner`（`drivers/i2c/i2c-dev.c:627`）和
`cdev.owner`（`drivers/i2c/i2c-dev.c:664`）。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"两份合同是并列的"—— 它们由两个不同的层管**。
   `cdev.owner` 由**字符设备层**（`fs/char_dev.c`）管，
   `fops.owner` 由 **VFS**（`fs/file_table.c`）管。
   ⇒ 所以在 `fs/char_dev.c` 里只看得到一半的配平（§6 那条误判）。

2. ⚠️ **`cdev_init()` 不会替你填 `cdev.owner`**（`fs/char_dev.c:651-657`）——
   而且它开头有 `memset`（`fs/char_dev.c:653`）会清掉你之前设的任何东西。
   ⇒ **必须写在 `cdev_init()` 之后**（`drivers/i2c/i2c-dev.c:663-664` 那两行相邻不是巧合，05 §1.2）。

---

## §8 为什么 misc 设备"套了两层"？

### 先举个例子

一栋写字楼只申请到了**一个总机号码**（比如 `10`），但楼里有 200 家小公司。

解决办法：**装一个自动语音总机**。
> 打 10 号 → "请输入分机号" → 输入 235 → 转到某家公司。

于是接线变成两步：
① 电信的总台把 10 号接到**楼里的语音总机**；
② 语音总机再按分机号接到**具体那家公司**。

### 回到代码

**两次 `replace_fops`**：

| 次 | 谁做 | 从 → 到 | 位置 |
|---|---|---|---|
| ① | `chrdev_open()` | `def_chr_fops` → **`misc_fops`** | `fs/char_dev.c:412` |
| ② | `misc_open()` | `misc_fops` → **你的 fops** | `drivers/char/misc.c:100` 里 |

| 支撑事实 | 位置 |
|---|---|
| misc 模块 init 时占掉整个主设备号 10 | `drivers/char/misc.c:280` 的 `register_chrdev(MISC_MAJOR, "misc", &misc_fops)` |
| `misc_fops` **只有一个 `.open`** | `drivers/char/misc.c:151` |
| 按 minor 在 `misc_list` 里找你 | `drivers/char/misc.c:100` |
| 找不到就试着自动加载 | `drivers/char/misc.c:119` |
| `MISC_DYNAMIC_MINOR` = **255** | `include/linux/miscdevice.h:74` |

⇒ **misc 本质上就是"字符设备层之上的第二级 minor 分配器"**（04 §3.1）——
它和 `chrdev_open()` 用的是**同一个套路**，套了两层。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"语音总机很智能"—— 它只有 256 个分机号，全楼共享**。
   `MISC_MAJOR` 固定是 10，minor 只有 0–255。
   ⇒ 多个模块都用 misc 时**可能耗尽**（05 §2.2）。

2. ⚠️ **"分机后面的公司能查到自己的记录"—— 查不到**。
   在你的 fops 里 `container_of(inode->i_cdev, …)` 拿到的是
   **misc 自己的 cdev**（第 ① 次接线留下的），不是你的。
   ⇒ 所以 misc 驱动的私有数据只能是**全局的**，
   而且**做不了 `cdev_set_parent()` 生命周期绑定**（05 §2.2）。

3. ⚠️ **"两层是白加的开销"—— 换来的是不用申请主设备号**。
   `misc_register()` 一句话替你做完四件事（04 §3.1）——
   ⇒ 只要一个节点、不在乎设备号，misc 是对的选择（01 §7.2）。

---

## §9 为什么 `cdev_map` 没有出口？

### 先举个例子

回到 §1 那个"总台转接表"。

你会发现：**登记簿是公开的**（谁在 88 号开店，谁都能查），
**转接表却是总台内部的**（没有窗口对外提供查询）。

为什么？因为**当年设计的时候，"打个电话试试"就是最直接的查询方式** ——
没人想到有一天需要"不打电话就知道 88 号能不能转接"。

### 回到代码

| 表 | 有出口吗 | 出口是什么 |
|---|---|---|
| `chrdevs[255]`（`fs/char_dev.c:41`） | **有** | `/proc/devices`（`chrdev_show()`，`fs/char_dev.c:51`） |
| `cdev_map`（`fs/char_dev.c:28`） | **完全没有** | —— |

```bash
grep -rn 'cdev_map' --include=*.c --include=*.h .     # → 只在 fs/char_dev.c 里出现
```

**它是 `static`**（`fs/char_dev.c:28`）⇒ 连别的内核文件都拿不到它。

**历史线索**：`drivers/base/map.c` 的文件头注释（`drivers/base/map.c:7-9`）
写着 "NOTE: data structure needs to be changed… It is isolated, though,
so these changes will be **local to that file**." ——
⇒ **"isolated"（隔离）是设计意图**：它刻意不对外暴露，好让将来能随便换实现。
版权行是 "Al Viro 2002,2003"（`drivers/base/map.c:5`）⇒ 这个注释挂了 20 年（03 §C3）。

**实际后果**（07 §4.3）：确认 `cdev_add()` 做了没有，只能：
① `open()` 看是否 `ENXIO`；② kprobe 看 `kobj_lookup` 的返回值。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"没有出口是疏忽"—— 一半是刻意的**。
   注释里的 "isolated" 说明作者**故意**不让外部依赖它的结构（这样才好换实现）。
   ⚠️ 但"不暴露结构"和"不提供只读视图"是两件事 ——
   ⇒ 后者确实是一个真实的可观测性缺口（03 §C5）。

2. ⚠️ **"打电话试试很麻烦"—— `open()` 其实是最好的判据**。
   `ENXIO` 的四个来源全在 `chrdev_open()` 里（01 §8.1），
   而且**只有第一个**（`fs/char_dev.c:388`）表示"`cdev_add()` 没做"。
   ⇒ 配上一个 kprobe 就能完全区分（07 §3.2）。

---

## §10 阅读下一篇

| 你现在想知道什么 | 去哪 |
|---|---|
| 12 个 API 的签名和 11 个错误码点 | 01 篇 |
| §1 §2 §6 提到的结构体字段和五组关系 | 02 篇 |
| §3 §4 §5 §7 的完整机制剖析 | 03 篇（Part A 时间线 / Part B 横切 / Part C 评价） |
| §8 §9 提到的四个独立框架（devtmpfs / udev / misc / procfs） | 04 篇 |
| 我要动手写代码 | 05 篇（§5 对应 05 §1.3，§8 对应 05 §2） |
| 配置机制（**没有设备树**） | 06 篇 |
| §9 说的那些绕法怎么做 | 07 篇 §3 §4.3 |
| 本套文档的取证方法和未验证项 | 99 篇 |

### §10.1 九个主题里最该记住的三条

1. **设备号 / `cdev_add()` / `/dev` 节点是三件独立的事**（§1）。
   `cdev_add()` **哪里都看不到**，忘了它的表现是 `open()` 返回 `ENXIO`。
2. **`cdev_del()` 之后你的 fops 还会被调用**（§4）。
   ⇒ 私有数据必须活到 `device.release`，**不能用 `devm_kzalloc()`**（§5）。
3. **`cdev.owner` 和 `fops.owner` 是两个不同的计数，两个都要填**（§7）。
   而且 `cdev.owner` 必须设在 `cdev_init()` **之后**（它会 `memset`）。
