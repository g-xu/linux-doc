---
title: 内部实现
---

# dma-buf 03：内部机制

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

本篇是源码级追踪，分四组：
**A 组**缓冲区的一生（`dma-buf.c`）· **B 组** fence 状态机（`dma-fence.c`）·
**C 组** dma-resv 的 ww_mutex + RCU（`dma-resv.c`）· **D 组**设计债与陈旧之处。

---

# A 组：缓冲区的一生

## §A1 dma-buf 自己挂了一个文件系统

⚠️ **这是本子系统最出人意料的一处实现**：dma-buf 不是字符设备、不是匿名 inode，
**它注册了一个内部专用的 `file_system_type` 并 `kern_mount` 了它**。

`dma_buf_init()`（`dma-buf.c:1614-1630`，`subsys_initcall` 在 `:1631`）四步：

```
1. :1618  dma_buf_init_sysfs_statistics()   ← 建 /sys/kernel/dmabuf/buffers
2. :1622  dma_buf_mnt = kern_mount(&dma_buf_fs_type)   ⚠️ 内部 mount
3. :1626  mutex_init(&db_list.lock)         ← 全局缓冲区链表
4. :1628  dma_buf_init_debugfs()            ← 建 /sys/kernel/debug/dma_buf/bufinfo
```

对应的 `dma_buf_deinit()`（`:1633-1638`，`__exitcall` 在 `:1639`）逆序拆。

⚠️ **`kern_mount` 的 mount 点不在任何命名空间里可见** ——
`/proc/mounts` 里看不到它。它的唯一作用是给 `alloc_anon_inode(dma_buf_mnt->mnt_sb)`
（`dma-buf.c:530`，在 `dma_buf_getfile()` 里）提供一个超级块。

**为什么不用通用的 `anon_inode_getfile()`？** 因为要自定义
`d_dname`：`dmabuffs_dname()`（`dma-buf.c:45`）通过
`dma_buf_dentry_ops`（`:107-110`）挂上去，让
`ls -l /proc/<pid>/fd/` **显示出缓冲区的名字和大小**，而不是千篇一律的
`anon_inode:dmabuf`。

⇒ ⚠️ **实践价值**：`ls -l /proc/<pid>/fd/` 是排查 dma-buf 泄漏最直接的手段，
而这一点完全依赖上面那个内部文件系统。对比 `sync_file`——它用的是
普通的 `anon_inode_getfile("sync_file", …)`（`sync_file.c:31`）
⇒ **sync_file 的 fd 在 `/proc/<pid>/fd/` 里看不出任何信息**。

## §A2 `dma_buf_export()` 九步

`dma-buf.c:608-692`（导出在 `:692`）：

```
1. :608-620  校验：priv / ops / map_dma_buf / unmap_dma_buf / release
             ⚠️ 只查这 5 个 → WARN_ON + ERR_PTR(-EINVAL)   （02 §2）
2.           ⚠️ 校验里还有一条：ops->cache_sgt_mapping 与 dynamic 的组合限制
3. :645      kzalloc 失败 → -ENOMEM
4.           填 dmabuf：size / ops / priv / exp_name / owner
5. :527      dma_buf_getfile() → 走 §A1 的内部 mount 建 struct file
6.           exp_info->resv 为 NULL ⇒ 分配一个内建 dma_resv 并 dma_resv_init()
7.           file->private_data = dmabuf；dmabuf->file = file
8.           dma_buf_stats_setup()  ← ⚠️ 这里会检查 exp_name 非空（§A8）
9.           挂进 db_list（持 db_list.lock）
```

⚠️ **第 6 步的分叉决定了后面所有的锁语义**：

| `exp_info->resv` | 结果 |
|---|---|
| NULL | 核心分配一个**独立的** `dma_resv`，只保护这个 dma-buf |
| 指向导出方 bo 里内嵌的 resv | ⚠️ **同一把 ww_mutex 同时管着 dma-buf 和导出方的 buffer object**（02 §9.3） |

GPU 驱动一律走第二种（`drm_prime.c` 里 `exp_info.resv = obj->resv`）
⇒ **`dma_resv_lock(dmabuf->resv)` 会把 GPU 驱动的整个 bo 锁住**。
这就是 01 §3.6 第 4 条（fence 完成路径不能取 `dma_resv` 锁）为什么是硬约束。

## §A3 attach → map 的两条路

### §A3.1 静态 vs 动态

`dma_buf_dynamic_attach()`（`dma-buf.c:904` 导出，实现在它上面）的分叉判据是
**`importer_ops` 是否为 NULL**，`dma_buf_attach()`（`:919`）就是传 NULL 的包装。

| | 静态（`importer_ops == NULL`） | 动态（提供了 `move_notify`） |
|---|---|---|
| backing store | ⚠️ **必须一直 pin 住** | 导出方可以随时迁移 |
| map 时机 | attach 时就可以 map 好并缓存 | 每次用前 map，收到 `move_notify` 就作废 |
| 需要的 Kconfig | 无 | ⚠️ `CONFIG_DMABUF_MOVE_NOTIFY=y` 才真的不 pin |
| 安全含义 | ⚠️ **用户态可以通过 dma-buf pin 住无限内存** | 修掉了这个问题 |

⚠️⚠️ **`CONFIG_DMABUF_MOVE_NOTIFY` 默认 `n`**（`drivers/dma-buf/Kconfig:42-52`），
help 原文把它称作 "(EXPERIMENTAL)" 并明说
**"This fixes a security problem where userspace is able to pin unrestricted
amounts of memory through DMA-buf"**
⇒ **默认配置的内核上，这个安全问题是存在的**。
判断方式：`grep CONFIG_DMABUF_MOVE_NOTIFY /proc/config.gz`。

`dma_buf_is_dynamic()`（`include/linux/dma-buf.h:585`）就是查这个 Kconfig 加导出方能力。

### §A3.2 `__map_dma_buf()` 与 `mangle_sg_table()`

真正干活的是静态函数 `__map_dma_buf()`（`dma-buf.c:778`），
被 `dma_buf_map_attachment()`（`:1039` 起，导出在 `:1106`）调用。

⚠️ 旁边有个 `mangle_sg_table()`（`dma-buf.c:763`）——
**它在 `CONFIG_DMABUF_DEBUG=y` 时故意把 `sg_table` 里的 `page` 指针打乱**。

```
config DMABUF_DEBUG
        bool "DMA-BUF debug checks"
        default y if DMA_API_DEBUG
```
（`drivers/dma-buf/Kconfig:54-61`，help 说它 "validates that importers do not
peek at the underlying struct page"）

⇒ ⚠️⚠️ **这是内核里少见的"主动破坏数据以暴露错误用法"的手段**。
导入方偷看 `sg_page(sg)` 在开了这个开关的内核上会拿到垃圾指针 ⇒ 立刻崩。
**关着开关时同一段代码"能跑"** ⇒ 这类 bug 只在开了 `DMA_API_DEBUG` 的
调试内核上暴露。写导入方时的规矩：**只用 `sg_dma_address()` 和 `sg_dma_len()`，
永远不要碰 `sg_page()`**（除非你声明了 `allow_peer2peer = false` 并且真的需要 page）。

### §A3.3 那对 `_unlocked` API 的实现差别

| 函数 | 行 | 内部做了什么 |
|---|---|---|
| `dma_buf_map_attachment()` | `:1039`（导出 `:1106`） | 先 `dma_resv_assert_held()`，再 `__map_dma_buf()` |
| `dma_buf_map_attachment_unlocked()` | `:1134` | `dma_resv_lock()` → 调上面那个 → `dma_resv_unlock()` |
| `dma_buf_unmap_attachment()` | `:1146`（导出 `:1170`） | 断言 + `__unmap_dma_buf()`(`:921`) |
| `dma_buf_unmap_attachment_unlocked()` | `:1182`（导出 `:1195`） | 自己加解锁 |

⚠️ 01 §2.2 已说过用错方向的后果不对称。这里补一条实现层的观察：
**`_unlocked` 版是纯包装，没有任何"轻量路径"** ⇒ **性能上没有理由选持锁版**，
选它的唯一理由是"我正在一段已经持锁的批量提交路径里"。

## §A4 `file_operations`：只有 7 个成员

`dma_buf_fops`（`dma-buf.c:509-517`）：

| 成员 | 实现 | 行 |
|---|---|---|
| `.release` | `dma_buf_file_release` | `:90` |
| `.mmap` | `dma_buf_mmap_internal` | `:131` |
| `.llseek` | `dma_buf_llseek` | `:152` |
| `.poll` | `dma_buf_poll` | `:237` |
| `.unlocked_ioctl` | `dma_buf_ioctl` | `:440` |
| `.compat_ioctl` | `compat_ptr_ioctl` | `:515`（通用助手） |
| `.show_fdinfo` | `dma_buf_show_fdinfo` | `:495` |

⚠️ **没有 `.read` / `.write`** ⇒ `cat`/`dd` 一个 dma-buf fd 一定失败。
唯一的数据通路是 `mmap()`。

⚠️ **`.llseek` 的实现（`:152`）就是 01 §9.1 那条 ABI**：
`dma-buf.rst:59-66` 规定只接受 `offset == 0` 配 `SEEK_END`/`SEEK_SET`，
其余 `-EINVAL`，不支持时 `-ESPIPE`
⇒ **`lseek(fd, 0, SEEK_END)` 是唯一不用 ioctl 就能读到 size 的手段。**

⚠️ **`.show_fdinfo`（`:495`）产出 `/proc/<pid>/fdinfo/<fd>`**。
`dma-buf-sysfs-stats.c:20` 的 DOC 块专门论证了它**不够用**：
只覆盖"这个进程持有 fd 或 mmap 过"的缓冲区，看不到全局
⇒ 于是有了 sysfs 统计层（§A8）。

## §A5 ioctl 分派：5 个号，3 个别名

`dma_buf_ioctl()`（`dma-buf.c:440-493`）的 `switch`：

| case | 行 | 处理 |
|---|---|---|
| `DMA_BUF_IOCTL_SYNC` | `:451` | 见 §A5.1 |
| `DMA_BUF_SET_NAME_A` | `:479` | ⚠️ 两个 case 落到同一个函数 |
| `DMA_BUF_SET_NAME_B` | `:480` | `dma_buf_set_name()`(`:320`) |
| `DMA_BUF_IOCTL_EXPORT_SYNC_FILE` | `:484` | ⚠️ **包在 `#if IS_ENABLED(CONFIG_SYNC_FILE)` 里**（`:483`） |
| `DMA_BUF_IOCTL_IMPORT_SYNC_FILE` | `:486` | 同上 |
| `default` | `:491` | `-ENOTTY` |

⚠️⚠️ **`DMA_BUF_SET_NAME` 有三个名字同一个号**
（`include/uapi/linux/dma-buf.h:176` / `:177` / `:178`）：
`DMA_BUF_SET_NAME` 是逻辑名，`_A`（`u32` 参数）和 `_B`（`u64` 参数）
是为了 32/64 位 compat 而定义的两个宏。**分派里只写了 `_A` 和 `_B`**
⇒ 用户态无论按哪个宏算出来的号都能命中。写用户态代码时**用 `DMA_BUF_SET_NAME` 即可**。

⚠️ **两个 sync_file 相关的 ioctl 是条件编译的**（`:483-488`）
⇒ `CONFIG_SYNC_FILE=n` 时它们返回 `-ENOTTY`
**和"内核太老不认识这个号"完全无法区分**。
`CONFIG_SYNC_FILE` 是 `default n`（`drivers/dma-buf/Kconfig:4-18`）
⇒ ⚠️ **这是一个必须靠 `/proc/config.gz` 而不能靠 errno 判断的特性。**

### §A5.1 `DMA_BUF_IOCTL_SYNC` 的五级校验

`:451-477`：

```
1. :452-453  copy_from_user 失败 → -EFAULT
2. :455-456  sync.flags 有未知位 → -EINVAL
             掩码 DMA_BUF_SYNC_VALID_FLAGS_MASK
             （include/uapi/linux/dma-buf.h:83）
3. :458-470  flags & DMA_BUF_SYNC_RW 三值映射：
               READ  → DMA_FROM_DEVICE
               WRITE → DMA_TO_DEVICE
               RW    → DMA_BIDIRECTIONAL
               其余（即 0）→ -EINVAL      ⚠️ 不给方向 = 参数错
4. :472-475  flags & DMA_BUF_SYNC_END ? end_cpu_access : begin_cpu_access
5. :477      直接返回被调函数的返回值
```

⚠️⚠️ **第 5 步意味着 `-EAGAIN`/`-EINTR` 会原样穿到用户态**
——这就是 DOC 块 `dma-buf.c:1253` 要求用户态重试的实现依据。
⚠️ 而如果导出方**没实现** `begin_cpu_access`/`end_cpu_access`，
`__dma_buf_begin_cpu_access()`（`:1302`）走空路径 ⇒ **ioctl 返回 0**
⇒ **"SYNC 成功了但什么都没做"**（02 §2 那张表的第 3 行）。

## §A6 `poll` 的实现：两个回调槽 + 隐式栅栏

`dma_buf_poll()`（`dma-buf.c:237`）+ `dma_buf_poll_add_cb()`（`:218`）
+ `dma_buf_poll_cb()`（`:203`）三个函数配合。

机制：

```
poll(fd, EPOLLIN)   → dma_buf_poll_add_cb(resv, write=true,  &dmabuf->cb_in)
poll(fd, EPOLLOUT)  → dma_buf_poll_add_cb(resv, write=false, &dmabuf->cb_out)
        ↓
   dma_resv 上挂一个 dma_fence_cb（用 dmabuf 里那两个预留槽，02 §1）
        ↓
   fence signal → dma_buf_poll_cb() → wake_up_interruptible(&dmabuf->poll)
```

⚠️ **只有两个槽（`cb_in` / `cb_out`）** ⇒ 同一个 dma-buf 上
**同一方向的并发 poll 共用一个槽**，靠 `cb->active` 这个 `__poll_t`
（`include/linux/dma-buf.h:444`）做状态管理。这不是 bug，
但意味着 **poll 的实现是"每 buffer 每方向一个 fence 回调"，不是每 poller 一个**。

⚠️ 语义（DOC 块 `dma-buf.c:179`，02 §1.2）：
`EPOLLIN` 等**最近的写 fence**，`EPOLLOUT` 等**所有 fence**。
⚠️⚠️ **两者都只表示 DMA 完成，不表示 cache 已刷** ⇒ poll 醒了之后
CPU 要读还得发 SYNC ioctl。

## §A7 debugfs 的实现

| 项 | 位置 |
|---|---|
| `dma_buf_debug_show()` | `dma-buf.c:1511` |
| `DEFINE_SHOW_ATTRIBUTE(dma_buf_debug)` | `dma-buf.c:1573` |
| `debugfs_create_dir("dma_buf", NULL)` | `dma-buf.c:1582` |
| `debugfs_create_file("bufinfo", S_IRUGO, …)` | `dma-buf.c:1588` |
| `dma_buf_init_debugfs()` | `dma-buf.c:1577`（`CONFIG_DEBUG_FS=n` 时空桩在 `:1605`） |
| `dma_buf_uninit_debugfs()` | `:1600` / 空桩 `:1609` |

⇒ 唯一的文件是 **`/sys/kernel/debug/dma_buf/bufinfo`**，遍历全局 `db_list`
（`dma-buf.c:43` 的 `static struct dma_buf_list db_list`）。
⚠️ **遍历期间持 `db_list.lock`** ⇒ 和 `dma_buf_export()` 第 9 步、
`dma_buf_release()` 互斥。详细输出格式在 07 §1。

## §A8 sysfs 统计层：kobject 而非 class

`drivers/dma-buf/dma-buf-sysfs-stats.c`（202 行，⚠️ **0 个导出符号**）。

| 步骤 | 位置 | 做什么 |
|---|---|---|
| 建 kset | `:148-150` | `kset_create_and_add("dmabuf", &dmabuf_sysfs_no_uevent_ops, kernel_kobj)` |
| 建子 kset | `:154-156` | `kset_create_and_add("buffers", …, &dma_buf_stats_kset->kobj)` |
| 每 buffer 一个目录 | `:191-192` | `kobject_init_and_add(…, "%lu", file_inode(file)->i_ino)` |
| 两个属性 | `:97` / `:98` | `exporter_name` / `size`，都是 `__ATTR_RO` |
| 属性组 | `:105` | `ATTRIBUTE_GROUPS(dma_buf_stats_default)` |
| ktype | `:115` | `dma_buf_ktype` |
| setup / teardown | `:171` / `:121` | 被 `dma_buf_export()` / release 调用 |
| uevent 过滤 | `:135-142` | ⚠️ 返回 0，**屏蔽掉所有 uevent** |

⇒ 真实路径是 **`/sys/kernel/dmabuf/buffers/<inode 号>/{exporter_name,size}`**。

⚠️⚠️ **目录名是缓冲区的 inode 号**（`:191-192`），不是索引、不是名字
⇒ **要和 `/proc/<pid>/fd/` 对上，得用 `stat -L` 读那个 fd 的 inode**。

⚠️ **uevent 被显式屏蔽**（`:135-142` 的过滤函数返回 0）
⇒ **udev 规则对 dma-buf 完全无效**，不要指望用 udev 监控缓冲区创建。

⚠️ `dma_buf_stats_setup()`（`:171`）在 `exp_name` 为空时
`pr_err("exporter name must not be empty if stats needed\n")`（`:177`）并失败
⇒ **手写 `struct dma_buf_export_info info = {};` 的代价在这里体现**（01 §2.1）。

⚠️⚠️ **这一整层 Kconfig 自己标着 DEPRECATED**
（`drivers/dma-buf/Kconfig:77-90`，标题 "DMA-BUF sysfs statistics (DEPRECATED)"），
help 原文：**"This option is deprecated and should sooner or later be removed.
Android is the only user of this and it turned out that this resulted in quite
some performance problems."**
⇒ **新代码不要依赖它**；`/proc/<pid>/fdinfo/` 和 debugfs 是替代品。

---

# B 组：fence 状态机

## §B1 三个标志位构成的单向状态机

`dma_fence` 的全部状态就是 `flags` 里三个位（02 §4.3）：

```
                    ┌──────────────────────────────────────┐
   dma_fence_init   │                                      │
   (:948)           │ flags = 0                            │
        ↓           │ union 里是 cb_list（空链表）           │
   ┌────────────────┴──────────────────────────────────────┘
   │  ENABLE_SIGNAL_BIT ←── __dma_fence_enable_signaling() (:579)
   │      置位时机：第一个 add_callback / wait / enable_sw_signaling
   │      ⚠️ 用 test_and_set_bit，只有第一次会调 ops->enable_signaling (:591)
   ↓
   │  SIGNALED_BIT + TIMESTAMP_BIT ←── dma_fence_signal_timestamp_locked() (:367)
   │      ⚠️ 也是 test_and_set_bit (:375)，第二次调返回 -EINVAL (:377)
   ↓
   dma_fence_release() (:531)  ← 引用计数归零
```

⚠️ **三个位没有一个能清零** ⇒ fence 是**一次性对象**。
想"重置一个 fence 再用一次"在这个设计里不存在，必须新建。

## §B2 signal 的六行核心：union 换手

`dma_fence_signal_timestamp_locked()`（`dma-fence.c:367-392`）是整个 fence 层
最关键的一段，因为它**就地把 union 从 `cb_list` 相换成 `timestamp`**：

```c
373:  lockdep_assert_held(fence->lock);
375:  if (unlikely(test_and_set_bit(DMA_FENCE_FLAG_SIGNALED_BIT, &fence->flags)))
377:          return -EINVAL;               ⚠️ 重复 signal 不是致命错，只是失败
380:  list_replace(&fence->cb_list, &cb_list);   ⚠️⚠️ 先把链表搬到栈上
382:  fence->timestamp = timestamp;               ← 同一块内存改写成时间戳
383:  set_bit(DMA_FENCE_FLAG_TIMESTAMP_BIT, &fence->flags);
384:  trace_dma_fence_signaled(fence);
386:  list_for_each_entry_safe(cur, tmp, &cb_list, node) {
387:          INIT_LIST_HEAD(&cur->node);         ← 回调看到自己已摘链
388:          cur->func(fence, cur);
389:  }
```

⚠️⚠️ **`:380` 的 `list_replace` 到栈上是必须的**：如果直接遍历 `fence->cb_list`
再写 `timestamp`，两者是同一块内存，遍历会踩在时间戳上。
这也解释了 02 §4.1 的第一条硬规则——**`SIGNALED_BIT` 置位之后
永远不要碰 `cb_list`**。

⚠️ **`:387` 先 `INIT_LIST_HEAD` 再调回调** ⇒ **回调里可以安全地 `kfree(cb)`**，
也可以重新 `dma_fence_add_callback()` 到另一个 fence 上。

⚠️ 回调是**在持 `fence->lock` 的情况下被调用的**（`:388`）
⇒ **回调函数不能睡、不能取任何可能与 `fence->lock` 反序的锁**
（01 §3.6 第 4 条的实现依据）。

## §B3 `enable_signaling` 的懒惰契约

`__dma_fence_enable_signaling()`（`dma-fence.c:579-601`）：

```c
585:  was_set = test_and_set_bit(DMA_FENCE_FLAG_ENABLE_SIGNAL_BIT, &fence->flags);
588:  if (test_bit(DMA_FENCE_FLAG_SIGNALED_BIT, &fence->flags))
589:          return false;                       ← 已完成，无需做任何事
591:  if (!was_set && fence->ops->enable_signaling) {
592:          trace_dma_fence_enable_signal(fence);
594:          if (!fence->ops->enable_signaling(fence)) {
595:                  dma_fence_signal_locked(fence);   ⚠️ 返回 false = "已经完成了"
596:                  return false;
597:          }
598:  }
```

⚠️⚠️ **`ops->enable_signaling` 返回 `false` 的语义不是"失败"，是"这个 fence
其实已经完成了，你直接 signal 吧"** ——核心会**代替驱动**调
`dma_fence_signal_locked()`（`:595`）。写驱动时返回 `false`
**不要再自己 signal 一遍**，否则第二次得到 `-EINVAL`。

⚠️ **`enable_signaling` 是懒惰的**：没人等这个 fence 时驱动不必开中断。
这是 GPU 驱动省中断的关键优化 —— 也意味着
⚠️ **一个从没被 wait / add_callback 过的 fence，驱动那边的完成中断可能压根没开**
⇒ 调试 "fence 永远不完成" 时先确认 `ENABLE_SIGNAL_BIT` 有没有置上。

## §B4 默认等待：一个栈上的回调

`dma_fence_default_wait()`（`dma-fence.c:761-807`，导出 `:808`）的机制：

```c
734:  struct default_wait_cb {          ← 私有结构，只有 2 个字段
735:          struct dma_fence_cb base;
736:          struct task_struct *task;
737:  };
739:  dma_fence_default_wait_cb()  →  wake_up_state(wait->task, TASK_NORMAL);
```

⇒ **等待就是"把当前 task 挂成一个 fence 回调然后睡"**。
返回值三态（kerneldoc `:755-758`）：`-ERESTARTSYS` 被信号打断（`:773`/`:797`）·
`0` 超时 · **正数 = 剩余 jiffies**
⚠️ 这就是 01 §3.3 那个坑的出处：**判断成功必须写 `ret > 0`**。

⚠️ kerneldoc `:756-758` 还有一条：**`timeout == 0` 时如果 fence 已完成，返回 1
而不是 0** ——"for consistency with other functions taking a jiffies timeout"
⇒ 拿 `dma_fence_wait_timeout(f, false, 0)` 当"非阻塞查询"用是可以的，
但返回值是 1/0 不是 true/false 的直觉方向。

## §B5 释放时的 `-EDEADLK`：泄漏 fence 的自救

`dma_fence_release()`（`dma-fence.c:531-563`，导出 `:564`）里有一段
**内核里少见的"发现 bug 就强行圆场"逻辑**：

```
:539-543   WARN("Fence %s:%s:%llx:%llx released with pending signals!")
:546-551   注释：解释为什么还要继续救
:553       spin_lock_irqsave(fence->lock, flags)
:554       fence->error = -EDEADLK;        ⚠️⚠️ 给等待者一个错误码
:555       dma_fence_signal_locked(fence)  ← 强制 signal，唤醒所有等待者
:559-562   ops->release ? ops->release(fence) : dma_fence_free(fence)
```

⚠️⚠️ **看到 `dma_fence_get_status()` 返回 `-EDEADLK` 意味着"有人把一个
还没 signal 的 fence 释放了"** ——**这是驱动 bug，不是死锁**。
（01 §8 已说过 `-EDEADLK` 在本子系统里有两个完全不同的含义：
ww_mutex 的正常回退 vs 这里的驱动 bug 信号。**区分方法：看它从哪来的**。）

⚠️ 不救会怎样？等待者永远睡死。所以核心选择"WARN + 强制唤醒"
——**让系统活着并留下证据**，而不是静默挂起。

## §B6 signalling 注解：一把假锁

`dma_fence_begin_signalling()` / `_end_signalling()`（`dma-fence.c:302` / `:325`）
不做任何实际同步，**它们操作的是一个纯粹为了 lockdep 而存在的假锁**：

```c
284:  static struct lockdep_map dma_fence_lockdep_map = {
285:          .name = "dma_fence_map"
286:  };
```

`begin` 的三个分支（`:304-315`）：

| 条件 | 行 | 行为 |
|---|---|---|
| 已经持有（嵌套） | `:305-306` | 直接返回 `true`（cookie = "别 release"） |
| `in_atomic()` | `:309-310` | 返回 `true`，靠 `might_sleep` 检查兜底 |
| 其余 | `:313` | `lock_acquire(…, 1, 1, …)` ⚠️ **非递归读锁** |

`end`（`:325-331`）：cookie 为 `true` 就什么都不做，否则 `lock_release`。

⚠️⚠️ **为什么用"读锁"？** 因为多个 CPU 可以同时处在 signalling 临界区里
（读锁可并发），但**"在 signalling 临界区里等 fence" 会被 lockdep 判成
读锁与自身的依赖环** —— 这正是要抓的 bug。

`__dma_fence_might_wait()`（`dma-fence.c:334-345`）是配套的"等待侧"注解，
它做了一个反直觉的操作：

```c
338:  tmp = lock_is_held_type(&dma_fence_lockdep_map, 1);
339-340:  if (tmp) lock_release(...)      ⚠️ 先临时放掉
341-342:  lock_map_acquire(); lock_map_release();   ← 只为登记依赖
343-344:  if (tmp) lock_acquire(...)      ← 再拿回来
```

⚠️ 先放再取是因为**假锁是非递归读锁**，不放掉就自己撞自己。
`:341-342` 那对空的 acquire/release **唯一目的是让 lockdep 记下
"这条路径上会等 fence"** 这个事实。

⇒ **实践结论**：写 fence 生产方时，
**把 `dma_fence_begin_signalling()` 到 `_end_signalling()` 包住整条
"从提交到 signal" 的路径**，然后开 `CONFIG_PROVE_LOCKING` 跑一遍
——lockdep 会替你找出所有"完成路径上要拿的锁也会被等待方拿"的死锁。
这套注解**只在 `CONFIG_PROVE_LOCKING=y` 时有代码**（`:346` 的 `#endif`）。

---

# C 组：dma-resv 的 ww_mutex + RCU

## §C1 私有的 `dma_resv_list`：指针低两位藏 usage

`dma-resv.c:63-67`：

```c
63:  struct dma_resv_list {
64:          struct rcu_head rcu;
65:          u32 num_fences, max_fences;
66:          struct dma_fence __rcu *table[];      ← 柔性数组
67:  };
```

`:60-61` 的注释和宏是全篇最关键的一行：

```c
60:  /* Mask for the lower fence pointer bits */
61:  #define DMA_RESV_LIST_MASK	0x3
```

存取的两个静态助手：

```c
84:  dma_resv_list_set():   long tmp = ((long)fence) | usage;    (:89)
70:  dma_resv_list_entry(): *fence = (struct dma_fence *)(tmp & ~DMA_RESV_LIST_MASK);  (:78)
79-80:                     if (usage) *usage = tmp & DMA_RESV_LIST_MASK;
```

⚠️⚠️ **`enum dma_resv_usage` 的四个取值（0..3）被塞进了 fence 指针的低两位**
⇒ 三个后果：
1. **`struct dma_fence` 必须至少 4 字节对齐** —— 这是隐式的、没有
   `BUILD_BUG_ON` 保护的假设。用 `kmalloc` 分配的 fence 一定满足，
   但**打包在 `__packed` 结构里的 fence 会静默损坏**。
2. **`table[]` 里的裸指针不能直接 deref** ⇒ 这就是 `dma_resv_iter.fences`
   的 kerneldoc 写"private, *MUST* not dereference"（02 §5.2）的原因。
3. 遍历必须走 `dma_resv_list_entry()`，不能自己写循环。

⚠️ `dma_resv_list_alloc()`（`:98-110`）有个细节：**`max_fences` 不是传进来的值，
而是 `ksize()` 反算出来的**（`:106-107`）——利用 slab 实际给的
比请求更大的块，白拿几个槽位。

## §C2 `reserve → add` 为什么必须成对

`dma_resv_add_fence()`（`dma-resv.c:275-314`）**不分配内存**，
第 `:307` 行是：

```c
307:  BUG_ON(fobj->num_fences >= fobj->max_fences);
```

⚠️⚠️ **`BUG_ON`，不是 `WARN_ON`** ⇒ **忘了先 `dma_resv_reserve_fences()`
= 内核 panic**，不是返回错误。这是 01 §4.4 那条"顺序是硬要求"
在实现层最硬的证据。

为什么这么设计？因为 `add_fence` **要能在不能分配内存的上下文里调**
（提交路径、中断上下文），所以分配必须提前到 `reserve` 里做——
`dma_resv_reserve_fences()`（`:177-239`）用的是 `GFP_KERNEL`（`:102`），会睡。

⚠️ `reserve` 的扩容策略（`:185-191`）：

| 情况 | 行 | 新容量 |
|---|---|---|
| 已有槽且够用 | `:186-187` | 直接返回 0，**不分配** |
| 已有槽但不够 | `:188` | `max(旧数+请求数, 旧容量*2)` ⇒ **倍增** |
| 首次 | `:190` | `max(4, roundup_pow_of_two(请求数))` ⇒ **起步就是 4** |

⚠️⚠️ **kerneldoc `:170-172` 的一条易漏约束**：
**"the preallocated slots need to be re-reserved if @obj is unlocked at any time
before calling dma_resv_add_fence()"**，而且
**"This is validated when CONFIG_DEBUG_MUTEXES is enabled"**
⇒ `reserve` → **unlock** → `lock` → `add` 这条路径是 bug，
**但只在 `CONFIG_DEBUG_MUTEXES=y` 时会被抓到**
（机制是 `dma_resv_reset_max_fences()`，`:251`，导出 `:261`，
整个函数包在 `#ifdef CONFIG_DEBUG_MUTEXES` 里，`:242`）。
⚠️ 生产内核上这个 bug 表现成 §C2 开头那个 `BUG_ON` panic。

## §C3 `add_fence` 的替换优化

`:294-305` 的循环在追加之前先找能覆盖的旧 fence：

```c
298:  if ((old->context == fence->context && old_usage >= usage &&
299:       dma_fence_is_later_or_same(fence, old)) ||
300:      dma_fence_is_signaled(old)) {
301:          dma_resv_list_set(fobj, i, fence, usage);   ← 原地替换
302:          dma_fence_put(old);
303:          return;                                     ← 不占新槽
304:  }
```

两个替换条件：**同 context 且更新** 或 **旧的已完成**。

⚠️ 实践含义：**同一个 context（同一条引擎队列）反复 `add_fence`
不会让 fence 表无限增长** ——这是 GPU 驱动能一直往同一个 bo 上加 fence
而不爆 `max_fences` 的原因。
⚠️ 但**跨 context 的 fence 会各占一槽** ⇒ 多引擎并发访问同一 buffer 时
`reserve` 的数量要按引擎数算。

⚠️ `:289` 有个 `WARN_ON(dma_fence_is_container(fence))`，注释 `:286-288`：
**"Drivers should not add containers here, instead add each fence individually"**
⇒ **不要把 `dma_fence_array`/`chain` 塞进 `dma_resv`**，要先用
`dma_fence_unwrap_for_each` 拆开（01 §5）。

⚠️ `:311-313` 的 `smp_wmb()` 顺序：**先写好槽位内容，屏障，再增 `num_fences`**
⇒ 无锁读者（`rcu_read_lock` 下）看到的 `num_fences` 范围内的槽一定是有效的。

## §C4 无锁迭代器：restart 的真实原因

`dma_resv_iter_walk_unlocked()`（`dma-resv.c:366-394`）：

```c
381-383:  dma_resv_list_entry(cursor->fences, cursor->index++, …)
384:      cursor->fence = dma_fence_get_rcu(cursor->fence);
385:      if (!cursor->fence) {
386:              dma_resv_iter_restart_unlocked(cursor);   ⚠️⚠️ 从头再来
387:              continue;
388:      }
390-392:  if (!dma_fence_is_signaled(cursor->fence) &&
              cursor->usage >= cursor->fence_usage)
              break;                                       ← 找到了
```

⚠️⚠️ **`:384` 的 `dma_fence_get_rcu()` 失败（返回 NULL）就是 restart 的触发点**
——它失败意味着**这个 fence 的引用计数已经归零，正在被释放**
⇒ 整个 `fences` 数组可能都已经换了一版，**只能从头重扫**。

`dma_resv_iter_restart_unlocked()`（`:355-363`）重取 list 指针（`:359`）
并置 `is_restarted = true`（`:362`）。

⇒ **这就是 02 §5.2 那条"累加统计必须查 `dma_resv_iter_is_restarted()`"的
全部实现依据**：restart 之后前面数过的 fence 会被再数一遍。
kerneldoc `:402-404` 原文：**"Code which accumulates statistics or similar needs
to check for this with dma_resv_iter_is_restarted(). For this reason prefer the
locked dma_resv_iter_first() whenver possible."**（拼写错误是原文的）

⚠️ `:390-391` 的过滤条件说明**无锁迭代器会跳过已 signal 的 fence 和
usage 档位不够的 fence** ⇒ 它给的**不是"表里所有 fence"，而是
"还没完成且满足 usage 档位的 fence"**。想看全部要用带锁版
（`dma_resv_iter_first()`，`:459`，`EXPORT_SYMBOL_GPL` 在 `:472`）。

## §C5 `dma_resv_lockdep()`：启动时故意演一遍死锁

`dma-resv.c:742-780`，`subsys_initcall(dma_resv_lockdep)` 在 `:781`。

⚠️⚠️ **这是一个 `__init` 函数，唯一目的是在启动时把整套锁序"表演"给
lockdep 看**，好让后续任何真实驱动违反锁序时立刻被抓。它演的顺序是：

```
744  mm_alloc()                          ← 造一个假的 mm_struct
756  mmap_read_lock(mm)                  ← ① mmap_lock
757  ww_acquire_init(&ctx, &reservation_ww_class)
758  dma_resv_lock(&obj, &ctx)           ← ② dma_resv（ww_mutex）
759-760  -EDEADLK → dma_resv_lock_slow() ← ⚠️ 连回退路径也演一遍
761  fs_reclaim_acquire(GFP_KERNEL)      ← ③ 内存回收
763-764  i_mmap_lock_write/unlock_write  ← ④ 注释 :762 说明是给 shrinker 里的
                                             unmap_mapping_range 用的
766  lock_map_acquire(&__mmu_notifier_invalidate_range_start_map)
767  __dma_fence_might_wait()            ← ⑤ 在这里"等 fence"
768  lock_map_release(...)
```

⇒ ⚠️⚠️ **这条链宣告了内核里最强的一条 dma-buf 约束**：

> **`mmap_lock` → `dma_resv` → `fs_reclaim` → `mmu_notifier` → 等 fence**
> 这个顺序是合法的；**任何反方向的依赖都是死锁**。

具体地说，**"在 fence 完成路径上分配内存"就违反了它**
（`fs_reclaim` 在"等 fence"之前）——这正是 01 §3.6 第 5 条
（signalling 临界区内的 GFP 限制）的来源。

⚠️ `#ifdef CONFIG_MMU_NOTIFIER`（`:765`/`:769`）说明
**关掉 MMU notifier 时这条链短一截** ⇒ 某些死锁只在开了
`CONFIG_MMU_NOTIFIER` 的内核上被 lockdep 抓到。

⚠️ `reservation_ww_class` 由 `DEFINE_WD_CLASS`（`:57`）定义、`EXPORT_SYMBOL`（`:58`）
⇒ **全内核所有 `dma_resv` 共享同一个 ww class**
⇒ **不同驱动的 buffer object 之间也能互相排序** ——这是跨驱动
多 buffer 原子提交能工作的前提。

---

# D 组：三层的全局状态与设计债

## §D1 全局状态一览

本子系统的全局可变状态只有 5 处，都很小：

| 状态 | 位置 | 保护 | 作用 |
|---|---|---|---|
| `db_list`（head + mutex） | `dma-buf.c:43` | 自带 `mutex` | debugfs 遍历全部缓冲区 |
| `dma_buf_mnt` | `dma-buf.c:112` | 只在 init/deinit 写 | 内部 mount（§A1） |
| `dma_fence_context_counter` | `dma-fence.c:36` | `atomic64_t` | `dma_fence_context_alloc()`(`:187`) 发 context 号 |
| `dma_fence_stub` + 锁 | `dma-fence.c:27-28` | `dma_fence_stub_lock` | 全局唯一的已完成 fence |
| `reservation_ww_class` | `dma-resv.c:57` | 只读 | 全内核共享的 ww class（§C5） |

⚠️ **`dma_fence_context_counter` 从 1 起（`ATOMIC64_INIT(1)`，`:36`）** ⇒
**context 号 0 是保留的/无效值**。自己造 fence 时不要硬编码 context = 0。

⚠️⚠️ **`dma_fence_get_stub()`（`dma-fence.c:131`，导出 `:149`）返回的是
一个全局静态 fence**，用的 ops 是 `dma_fence_stub_ops`（`:119`），
其 `get_driver_name`/`get_timeline_name` 都返回固定串（`:114`）。
它的 **timestamp 是第一次被调用时的时间，也就是通常的启动早期**
⇒ **拿 stub fence 的 timestamp 做时间计算会得到荒谬的值**。
需要正确时间戳的场合用 `dma_fence_allocate_private_stub(ktime_t)`（`:157`，导出 `:177`）
——它每次分配一个新的、由调用者指定时间戳的已完成 fence。

## §D2 唯一的一处代码级设计债

全核心 `grep -nE '(FIXME|TODO|XXX)'` **只有 1 处**：

```
include/linux/dma-buf.h:193  TODO: Add try_map_dma_buf version, to return
                             immed with -EBUSY
```

⚠️ 它就在 `dma_buf_ops.map_dma_buf` 的 kerneldoc 里。含义：
**现在没有"非阻塞 map"** ——`dma_buf_map_attachment()` 一定会等
（等锁、等迁移完成）。
⇒ **在不能睡的上下文里没有任何办法 map 一个 dma-buf**，
必须提前在能睡的路径上 map 好并缓存（这也是静态 attach 存在的理由，§A3.1）。
01 §11 已核实：**`dma_buf_try_map_attachment` 这个符号不存在。**

## §D3 陈旧与不一致（写文档/调试时会被绊到）

### §D3.1 sysfs 路径在官方文档里是错的

| 出处 | 写的路径 |
|---|---|
| `dma-buf-sysfs-stats.c:36`（DOC 块内） | ⚠️ **`/sys/kernel/dma-buf/buffers`** |
| `dma-buf-sysfs-stats.c:41-42`（同一个 DOC 块） | `/sys/kernel/dmabuf/buffers` |
| `dma-buf-sysfs-stats.c:148`（真实代码） | `kset_create_and_add("dmabuf", …)` |
| `drivers/dma-buf/Kconfig:82` | `/sys/kernel/dmabuf/buffers` |
| `Documentation/ABI/testing/sysfs-kernel-dmabuf-buffers:1` | `/sys/kernel/dmabuf/buffers` |

⚠️⚠️ **`dma-buf.rst:114-115` 会把那个 DOC 块渲染进官方文档**
⇒ **kernel.org 上发布的文档里带着 `/sys/kernel/dma-buf/buffers` 这个错路径**，
一比五。**以代码为准：`/sys/kernel/dmabuf/buffers`。**

### §D3.2 `dma-buf.rst` 几乎是个 kernel-doc 骨架

348 行里手写内容不到 100 行，其余全是 `kernel-doc::` 指令
⇒ ⚠️ **想读"设计意图"，读 rst 的收益远低于直接读 8 个 DOC 块**（01 §12 已列）。
rst 里真正独有、代码注释里找不到的只有四段：

| 段 | 行 | 内容 |
|---|---|---|
| llseek ABI | `:59-66` | `lseek` 的确切语义（§A4） |
| O_CLOEXEC 论证 | `:68-83` | 为什么**必须**在 `dma_buf_fd()` 时就给 `O_CLOEXEC`，事后 `fcntl` 有竞态 |
| Indefinite DMA Fences | `:203-272` | 为什么"无限期 fence"被禁（08 篇主题） |
| Recoverable Page Faults | `:274-348` | 可恢复缺页与 fence 的根本冲突（08 篇主题） |

### §D3.3 `sync_debug.c` 用了 `_unsafe` 变体

`sync_debug.c:181` 有一行注释专门为
`debugfs_create_file_unsafe()`（`:183`/`:185`）辩护。
⚠️ `_unsafe` 意味着**不做"文件被删时还在用"的引用计数保护**
⇒ 这是 `CONFIG_SW_SYNC`（`default n`，"test and debug only"）才有的路径，
生产内核不该开。详见 07 §2。

### §D3.4 `dma_buf_debug_show` 与 `dma_resv_describe`

debugfs 的输出由两层拼成：
`dma_buf_debug_show()`（`dma-buf.c:1511`）遍历 `db_list`，
每个 buffer 调 `dma_resv_describe()`（`dma-resv.c:727`，导出 `:739`）
打 fence 列表，后者再对每个 fence 调
`dma_fence_describe()`（`dma-fence.c:923`，导出 `:930`）。

⚠️ **这三个函数是全内核唯一的"人可读 fence 状态"来源**
⇒ 驱动自己的 debugfs 想打 fence 就该调 `dma_fence_describe()`，不要自己格式化。

## §D4 四条只有读了实现才知道的结论

1. **`dma_buf_export()` 的校验只覆盖 5 个字段**（§A2 第 1 步）
   ⇒ 少写 13 个 `dma_buf_ops` 回调里的任何一个（除那 3 个必填）
   **都能成功注册**，故障推迟到运行时（02 §2 的症状表）。
2. **`add_fence` 越界是 `BUG_ON` 而不是 `WARN_ON`**（§C2）
   ⇒ `reserve/add` 配对是唯一一条"写错就 panic"的规则。
3. **fence 的完成回调在持 `fence->lock` 时被调**（§B2）
   ⇒ 回调里的任何一行代码都在原子上下文。
4. **`CONFIG_DMABUF_MOVE_NOTIFY` 默认关**（§A3.1）
   ⇒ 默认内核上用户态确实能通过 dma-buf pin 住无限内存，
   这是 Kconfig help 自陈的安全问题，不是推测。

---

## 下一步读什么

| 想知道 | 去哪 |
|---|---|
| dma-heap / udmabuf / sw_sync / sync_file 各自怎么实现 | **04** |
| 我要写导出方/导入方/fence 生产方，代码骨架 | **05** |
| CMA heap 的内存从哪来、`reserved-memory` 怎么配 | **06** |
| debugfs / tracepoint / lockdep 实操 | **07** |
| 为什么 fence 必须有界完成、无限期 fence 为什么被禁 | **08** |
| 本篇每条结论的取证命令 | **99** |
