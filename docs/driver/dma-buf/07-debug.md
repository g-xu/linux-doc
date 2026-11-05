---
title: 调试方法
---

# dma-buf 07：调试

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

---

## §0 手上有哪些工具

按"不需要任何配置就能用"排在最前：

| 工具 | 需要的配置 | 看得到什么 | 章节 |
|---|---|---|---|
| **`/proc/<pid>/fdinfo/<fd>`** | ⚠️ **无需任何配置** | 单个缓冲区的 size / 引用数 / 导出方 / 名字 | §1 |
| **`ls -l /proc/<pid>/fd/`** | ⚠️ **无需任何配置** | 哪些 fd 是 dma-buf、叫什么名字 | §1.2 |
| `/sys/kernel/debug/dma_buf/bufinfo` | `DEBUG_FS` | ⚠️ **全系统所有 dma-buf 的总清单 + fence + attach 的设备** | §2 |
| `dma_fence` tracepoints（7 个） | `FTRACE` | fence 的生命周期时间线 | §3 |
| `sync_timeline` tracepoint（1 个） | `SW_SYNC` | sw_sync 时间线推进 | §3.4 |
| `/sys/kernel/debug/sync/info` | `SW_SYNC` | sync_file 与 timeline 清单 | §4 |
| `/sys/kernel/dmabuf/buffers/` | `DMABUF_SYSFS_STATS`（DEPRECATED） | 每缓冲区两个属性 | §5 |
| `mangle_sg_table()` 主动破坏 | `DMABUF_DEBUG` | 抓"导入方偷看 `struct page`" | §6 |
| `dma_resv_lockdep()` 启动自检 | `PROVE_LOCKING` | 锁序违规 | §7 |
| selftest 模块 | `DMABUF_SELFTESTS` | fence / resv 的单元测试 | §8 |

⚠️⚠️ **最重要的一条**：**`/proc/<pid>/fdinfo` 在任何内核上都可用**，
而 `bufinfo`、tracepoint、sysfs stats 全部要配置。
**排查现场问题时先看 fdinfo。**

---

## §1 `/proc/<pid>/fdinfo`：零配置，最先用

### §1.1 输出格式

```c
/* drivers/dma-buf/dma-buf.c:495-507 */
static void dma_buf_show_fdinfo(struct seq_file *m, struct file *file)
{
        struct dma_buf *dmabuf = file->private_data;

        seq_printf(m, "size:\t%zu\n", dmabuf->size);
        /* Don't count the temporary reference taken inside procfs seq_show */
        seq_printf(m, "count:\t%ld\n", file_count(dmabuf->file) - 1);
        seq_printf(m, "exp_name:\t%s\n", dmabuf->exp_name);
        spin_lock(&dmabuf->name_lock);
        if (dmabuf->name)
                seq_printf(m, "name:\t%s\n", dmabuf->name);
        spin_unlock(&dmabuf->name_lock);
}
```

挂在 `dma_buf_fops.show_fdinfo`（`drivers/dma-buf/dma-buf.c:516`）。实际输出：

```
pos:	0
flags:	02000002
mnt_id:	14
ino:	1234
size:	8294400
count:	3
exp_name:	system
name:	camera-frame-0
```

| 字段 | 含义 | ⚠️ |
|---|---|---|
| `size` | 缓冲区字节数 | 就是 `exp_info.size` |
| `count` | ⚠️ **已经减掉了 procfs 自己那一份临时引用**（`:501` 的 `- 1`）⇒ 这是**真实持有者数量** |
| `exp_name` | 导出方名字（05 §1.2） | heap 导出的话就是 heap 名 |
| `name` | ⚠️ **只有调过 `DMA_BUF_SET_NAME` 才出现这一行**（`:504`） | 没设名字 ⇒ 整行消失，不是空字符串 |

⚠️ **`count` 是找泄漏的核心指标**：一块缓冲区释放不掉 = `count` 降不到 0。
逐个进程 `grep -l . /proc/*/fdinfo/*` 找出还持有它的人（用 `ino` 匹配）。

### §1.2 `ls -l /proc/<pid>/fd/`：一眼看出哪些是 dma-buf

```
lrwx------ 1 root root 64 Aug 24 10:00 7 -> /dmabuf:camera-frame-0
```

这个链接名来自一个自定义的 `d_dname`：

```c
/* drivers/dma-buf/dma-buf.c:45-58 */
static char *dmabuffs_dname(struct dentry *dentry, char *buffer, int buflen)
{
        …
        return dynamic_dname(buffer, buflen, "/%s:%s",
                             dentry->d_name.name, ret > 0 ? name : "");
}
```

挂在 `dma_buf_dentry_ops.d_dname`（`drivers/dma-buf/dma-buf.c:108`）。

⚠️ 格式是 **`/dmabuf:<名字>`** —— 没设名字时后半截是空的，显示成 `/dmabuf:`。
⚠️⚠️ **这是 dma-buf 自己挂内部 mount 而不是用 `anon_inode_getfile()` 的唯一理由**
（03 §A1）：换成匿名 inode 的话这里只会显示 `anon_inode:dmabuf`，
**看不到名字，也就没法在一堆 fd 里认出是哪块缓冲区**。

⚠️ 对比：`sync_file` 用的是 `anon_inode_getfile("sync_file", …)`
（`drivers/dma-buf/sync_file.c:31`）⇒ **sync_file 的 fd 在 `ls -l` 里看不出任何信息**。

### §1.3 inode 号是唯一的

```c
/* drivers/dma-buf/dma-buf.c:540-545 */
 * The ->i_ino acquired from get_next_ino() is not unique thus
 * … Override ->i_ino with the unique and dmabuffs specific value.
inode->i_ino = atomic64_add_return(1, &dmabuf_inode);
```

⚠️ **`ino` 是 dma-buf 自己维护的单调递增计数，全系统唯一** ⇒
可以用它把 `fdinfo`、`bufinfo`（`:1541` 打印 `i_ino`）、
sysfs 目录名（`:191-192` 用 `i_ino` 当 kobject 名）**三处对应起来**。
**这是跨进程追踪同一块缓冲区的唯一稳定标识。**

---

## §2 `bufinfo`：全系统总清单

### §2.1 怎么打开

```bash
mount -t debugfs none /sys/kernel/debug 2>/dev/null
cat /sys/kernel/debug/dma_buf/bufinfo
```

创建于 `dma_buf_init_debugfs()`（`drivers/dma-buf/dma-buf.c:1577-1598`）：
目录 `dma_buf`（`:1582`）+ 文件 `bufinfo`（`:1588`，`S_IRUGO` = 只读）。
⚠️ 创建失败只打 `pr_debug`（`:1591`）⇒ **默认日志级别下看不到**。

### §2.2 输出逐列

表头（`drivers/dma-buf/dma-buf.c:1525-1526`）与数据行（`:1536-1542`）：

```
Dma-buf Objects:
size    	flags   	mode    	count   	exp_name	ino     	name
08294400	00080002	0000001f	00000003	system	00001234	camera-frame-0
	write fence: my-driver my-ring0 seq 42 signalled
	Attached Devices:
	fe000000.gpu
	fd000000.vpu
Total 2 devices attached

Total 1 objects, 8294400 bytes
```

| 列 | 来源 | ⚠️ |
|---|---|---|
| `size` | `buf_obj->size` | 十进制，**宽度补零到 8 位** |
| `flags` | `file->f_flags` | ⚠️ **十六进制**（`%08x`），和 `size` 的十进制混在一张表里 |
| `mode` | `file->f_mode` | 十六进制 |
| `count` | `file_count()` | ⚠️ **这里没有减 1**（对比 fdinfo 的 `:501`）⇒ 两处的 `count` 口径不同 |
| `exp_name` | 导出方 | ⚠️ `%s` 无对齐 ⇒ 表格会错位 |
| `ino` | inode 号 | 与 §1.3 对应 |
| `name` | `DMA_BUF_SET_NAME` 设的名字 | ⚠️ 没设时打印 `<none>`（`:1542` 的 `?:`），**不像 fdinfo 那样整行消失** |

之后每块缓冲区还打印两组信息：
- **fence 列表**：`dma_resv_describe(buf_obj->resv, s)`（`:1545`）
- **attach 的设备**：遍历 `buf_obj->attachments`，打 `dev_name()`（`:1550-1553`）

fence 那一行的格式来自两层：

```c
/* drivers/dma-buf/dma-resv.c:727-738 —— 前缀是 usage 名字 */
static const char *usage[] = { "kernel", "write", "read", "bookkeep" };   /* :729 */
seq_printf(seq, "\t%s fence:", usage[dma_resv_iter_usage(&cursor)]);      /* :734-735 */

/* drivers/dma-buf/dma-fence.c:925-928 —— 后半截 */
seq_printf(seq, "%s %s seq %llu %ssignalled\n",
           fence->ops->get_driver_name(fence),
           fence->ops->get_timeline_name(fence), fence->seqno,
           dma_fence_is_signaled(fence) ? "" : "un");
```

⚠️⚠️ **`unsignalled` 和 `signalled` 只差一个前缀 `un`**（`:928`）
⇒ **`grep signalled` 会同时命中两种状态**。要找未完成的 fence 得 `grep unsignalled`。

⚠️ `dma_resv_describe()` 用 `DMA_RESV_USAGE_READ` 遍历（`drivers/dma-buf/dma-resv.c:733`）
⇒ ⚠️ **`BOOKKEEP` 级别的 fence 不会被打印出来**（02 §4 的 usage 序）
⇒ **`bufinfo` 里看不到 fence，不等于 resv 上没有 fence。**

### §2.3 两个陷阱

⚠️⚠️ **`bufinfo` 会拿两把锁，而且都是可中断版**：
`mutex_lock_interruptible(&db_list.lock)`（`:1519`）
+ `dma_resv_lock_interruptible(buf_obj->resv, NULL)`（`:1530`）
⇒ **`cat` 可能返回 `-ERESTARTSYS` 而输出为空或截断**。
在有大量活跃 GPU 工作负载的系统上这是常态，**读到一半没了要重试，不是内核 bug**。

⚠️ 遍历 `db_list` 期间持 `db_list.lock`，逐个缓冲区还要拿它的 `resv`
⇒ **在锁竞争激烈时 `cat bufinfo` 自己会成为一个卡顿源**。生产环境不要循环 `cat`。

---

## §3 tracepoints

### §3.1 八个事件，一个类

`include/trace/events/dma_fence.h`（89 行）：一个 `DECLARE_EVENT_CLASS(dma_fence)`
（`:12`）+ **7 个 `DEFINE_EVENT`**，字段和打印格式全部共用（`:32-34`）：

```
driver=%s timeline=%s context=%u seqno=%u
```

| 事件 | 定义 | 核心触发点 |
|---|---|---|
| `dma_fence_init` | `:44` | `drivers/dma-buf/dma-fence.c:963` |
| `dma_fence_enable_signal` | `:58` | `drivers/dma-buf/dma-fence.c:592` |
| `dma_fence_signaled` | `:65` | `drivers/dma-buf/dma-fence.c:384` |
| `dma_fence_wait_start` | `:72` | `drivers/dma-buf/dma-fence.c:514` |
| `dma_fence_wait_end` | `:79` | `drivers/dma-buf/dma-fence.c:519` |
| `dma_fence_destroy` | `:51` | `drivers/dma-buf/dma-fence.c:536` |
| **`dma_fence_emit`** | `:37` | ⚠️⚠️ **核心一次都不触发** |

⚠️⚠️ **`dma_fence_emit` 全树只有两个调用点，都在 DRM 驱动里**：
`drivers/gpu/drm/qxl/qxl_release.c:427` 和 `drivers/gpu/drm/virtio/virtgpu_fence.c:114`
⇒ **arm64 真实硬件上开了这个事件也是一条不出**。
它的语义是"fence 已提交给硬件"，**得由驱动自己埋点**，多数驱动没埋。

⚠️ `context` 和 `seqno` 在 tracepoint 里都是 `unsigned int`（`:21-22`），
**而 `struct dma_fence` 里 `seqno` 是 `u64`** ⇒ ⚠️ **超过 32 位会被截断**。
长时间运行的 GPU 时间线上 seqno 会绕回，看 trace 时别把绕回当成"倒退"。

### §3.2 怎么用

```bash
cd /sys/kernel/debug/tracing
echo 1 > events/dma_fence/enable                 # 全开
# 或只开等待相关的两个
echo 1 > events/dma_fence/dma_fence_wait_start/enable
echo 1 > events/dma_fence/dma_fence_wait_end/enable
cat trace_pipe
```

⚠️⚠️ **`wait_start` / `wait_end` 配对缺失就是"卡住的 fence"** ——
这是查"进程 D 状态卡在等 GPU"最直接的手段：

```bash
# 有 start 没有 end 的 (context,seqno) 就是元凶
grep dma_fence_wait trace | awk '{print $NF, $(NF-1)}' | sort | uniq -c
```

⚠️ 注意 `wait_start`/`wait_end` **只包住 `dma_fence_default_wait()`**
（`drivers/dma-buf/dma-fence.c:514`/`:519` 在 `dma_fence_wait_timeout()` 里）
⇒ **驱动自己实现了 `ops->wait` 的话，这两个事件根本不出现**。
`ops->wait` 是 9 个 `dma_fence_ops` 回调之一（02 §5）。

### §3.3 ⚠️ 两个回调会在 tracepoint 里被调用

```c
/* include/trace/events/dma_fence.h:19-20 */
__string(driver, fence->ops->get_driver_name(fence))
__string(timeline, fence->ops->get_timeline_name(fence))
```

⇒ ⚠️⚠️ **开着 tracepoint 时，这两个回调会在 fence 的每个生命周期节点被调一次**。
它们必须是**无锁、无分配、可在中断上下文调用**的纯函数
（05 §3.1 给的骨架就是直接 `return "字符串常量"`）。
**在这两个回调里做任何复杂事情，会在开 trace 后暴露成死锁或性能塌陷。**

### §3.4 sw_sync 自己的那一个

`drivers/dma-buf/sync_trace.h`（33 行）：`TRACE_EVENT(sync_timeline)`（`:12`），
格式 `name=%s value=%d`（`:27`），唯一触发点 `drivers/dma-buf/sw_sync.c:197`。

⚠️ 它跟着 `CONFIG_SW_SYNC` 走 ⇒ **生产内核上不存在**（04 §5）。

---

## §4 `/sys/kernel/debug/sync/`

两个文件，创建于 `sync_debugfs_init()`（`drivers/dma-buf/sync_debug.c:174-189`）：

| 文件 | 权限 | 用途 |
|---|---|---|
| `info` | `0444` | 只读清单：所有 timeline + 所有 sync_file |
| `sw_sync` | `0644` | ⚠️ **打开它就创建一条新 timeline**（04 §5.2），不是查看用的 |

`info` 的结构（`drivers/dma-buf/sync_debug.c:141-170`）：

```
objs:
--------------
<timeline 名>: <当前 value>
  <各个 sync_pt>

fences:
--------------
[<指针>] <名字>: <状态>
```

- timeline 段 `:145-156`，每条走 `sync_print_obj()`（`:107`，格式在 `:111`）
- fence 段 `:158-169`，每条走 `sync_print_sync_file()`（`:121`，格式在 `:127-129`）
- 状态字符串来自 `sync_status_str()`（`:54`）

⚠️ 单个 fence 的打印 `sync_print_fence()`（`:65`）在 fence 已完成时
**会附上完成时间戳**（`:82` 的 `@%lld.%09ld`）
⇒ **这是内核里唯一能直接看到 fence 完成时刻的地方**，
比 tracepoint 好用（不用提前开 trace）。但它只覆盖 sw_sync 的 fence。

⚠️⚠️ 两个文件用的是 **`debugfs_create_file_unsafe()`**（`:183`/`:185`），
理由写在紧上面的注释里（`:178-182`）："The debugfs files won't ever get removed
and thus, there is no need to protect it against removal races."
⇒ **这是有意的、有论证的选择，不是疏漏**。但也意味着这两个文件**永远无法移除**。

⚠️ `late_initcall(sync_debugfs_init)`（`:190`）⇒ 比 dma-buf 核心的
`subsys_initcall`（`drivers/dma-buf/dma-buf.c:1631`）晚很多，
**早期启动阶段的 sync 活动看不到**。

---

## §5 sysfs 统计：已废弃，且文档里的路径是错的

### §5.1 真实路径

```c
/* drivers/dma-buf/dma-buf-sysfs-stats.c:148-156 */
dma_buf_stats_kset = kset_create_and_add("dmabuf", …, kernel_kobj);
dma_buf_per_buffer_stats_kset = kset_create_and_add("buffers", …,
                                        &dma_buf_stats_kset->kobj);
```

⇒ 真实路径是 **`/sys/kernel/dmabuf/buffers/<inode 号>/`**，
目录名就是 §1.3 那个 inode 号（`:191-192` 的 `kobject_init_and_add(…, "%lu", file_inode(file)->i_ino)`）。

每个目录下**只有两个只读属性**（`:100-103`）：

| 属性 | 实现 | 内容 |
|---|---|---|
| `exporter_name` | `:82-87`（`sysfs_emit "%s\n"`） | `dmabuf->exp_name` |
| `size` | `:89-94`（`sysfs_emit "%zu\n"`） | `dmabuf->size` |

⚠️ **就这两个，没有 attach 列表、没有 fence、没有引用计数** ⇒
**`bufinfo` 的信息量远大于它**。sysfs 的唯一优势是"能单个缓冲区独立读，不用扫全表"。

### §5.2 ⚠️⚠️ 官方文档里的路径拼错了

同一个文件里，四处写对、一处写错：

| 位置 | 写的是 | 对不对 |
|---|---|---|
| `drivers/dma-buf/dma-buf-sysfs-stats.c:36` | ⚠️⚠️ **`/sys/kernel/dma-buf/buffers`**（带连字符） | ❌ **错** |
| `drivers/dma-buf/dma-buf-sysfs-stats.c:41` | `/sys/kernel/dmabuf/buffers/<inode_number>/exporter_name` | ✔ |
| `drivers/dma-buf/dma-buf-sysfs-stats.c:42` | `/sys/kernel/dmabuf/buffers/<inode_number>/size` | ✔ |
| `drivers/dma-buf/Kconfig:82` | `/sys/kernel/dmabuf/buffers` | ✔ |
| `Documentation/ABI/testing/sysfs-kernel-dmabuf-buffers:1` | `/sys/kernel/dmabuf/buffers` | ✔ |

⚠️⚠️ **错的那一处偏偏是被发布出去的那一处**：
`:36` 在一个 `DOC:` 注释块里，被 `Documentation/driver-api/dma-buf.rst:114`
的 `.. kernel-doc:: drivers/dma-buf/dma-buf-sysfs-stats.c` 拉进正式文档
⇒ **kernel.org 上"DMA-BUF statistics"一节（`dma-buf.rst:112`）显示的路径是错的**。
按它去 `ls /sys/kernel/dma-buf/` 只会得到"No such file or directory"。

### §5.3 udev 收不到通知

```c
/* drivers/dma-buf/dma-buf-sysfs-stats.c:135-142 */
static int dmabuf_sysfs_uevent_filter(struct kobject *kobj)
{
        return 0;
}
static const struct kset_uevent_ops dmabuf_sysfs_no_uevent_ops = {
        .filter = dmabuf_sysfs_uevent_filter,
};
```

⚠️⚠️ **filter 恒返回 0 = 所有 uevent 都被过滤掉**（两个 kset 都用它，`:149`/`:155`）
⇒ **缓冲区创建/销毁不会有任何 udev 事件** ⇒
**想"监听 dma-buf 创建"是做不到的，只能轮询目录**。
这在缓冲区高频创建销毁的场景下就是它被标 DEPRECATED 的技术原因之一。

### §5.4 为什么不该开

Kconfig 自己写得很直白（`drivers/dma-buf/Kconfig:88-90`）：

> This option is deprecated and should sooner or later be removed.
> Android is the only user of this and it turned out that this resulted
> in quite some performance problems.

⚠️ 性能问题的机制是：**每次 `dma_buf_export()` 都要建一个 kobject 目录**
（`:181-192`），**每次释放都要 `kobject_del` + `kobject_put`**（`:129-130`）。
高频短命缓冲区场景下这是纯开销。

⚠️⚠️ 而且它有一个**功能性副作用**（05 §1.2）：
`exp_name` 为空时 `dma_buf_stats_setup()` 打 `pr_err` 并返回 `-EINVAL`（`:176-179`）
⇒ **开了这个选项之后，一些原本能工作的导出方会突然导出失败**。
**这是"开了调试选项反而坏了功能"的一个真实例子。**

---

## §6 `CONFIG_DMABUF_DEBUG`：主动破坏来抓错

```
config DMABUF_DEBUG                              drivers/dma-buf/Kconfig:54
	bool "DMA-BUF debug checks"
	default y if DMA_API_DEBUG                                        :57
	help
	  … Specifically it validates that importers do not peek at the
	  underlying struct page when they import a buffer.               :60-61
```

机制是 `mangle_sg_table()`（`drivers/dma-buf/dma-buf.c:763`，03 §A3.2）
**故意把返回给导入方的 sg 表里的 `page` 指针打乱**。

| 场景 | 关着（默认） | 开着 |
|---|---|---|
| 导入方只用 `sg_dma_address()` | 正常 | 正常 |
| 导入方偷看 `sg_page()` | ⚠️ **"能跑"** | **立刻崩，栈上就是罪魁** |

⚠️⚠️ **这就是 05 §5 表里 #7 那条"只在调试内核上炸"的来源**：
不开这个选项，误用 `sg_page()` 的代码可能在整个开发周期里都表现正常，
直到换了一台开了 `DMA_API_DEBUG` 的机器。

⚠️ **CI 必须开着它跑一遍** —— 它的开销只在 map/unmap 路径上，可接受。
⚠️ 它 `depends on DMA_SHARED_BUFFER`（`:56`），而 `default y if DMA_API_DEBUG`（`:57`）
⇒ **开了 `DMA_API_DEBUG` 就顺带开了它**，不用单独配。

---

## §7 lockdep：唯一能提前发现锁序错误的手段

### §7.1 启动时的锁序排练

`dma_resv_lockdep()`（`drivers/dma-buf/dma-resv.c:742-780`，
`subsys_initcall` 在 `:781`，`#if IS_ENABLED(CONFIG_LOCKDEP)` 在 `:741`）
在启动时**假装**走一遍完整的锁序，把这条链登记进 lockdep：

> **`mmap_lock` → `dma_resv` → `fs_reclaim` → `mmu_notifier` → 等 fence**

⚠️⚠️ **登记之后，任何违反这条顺序的真实代码路径都会在第一次执行时被 lockdep 抓住**，
不用等到真的死锁。这是本子系统最有价值的调试设施（03 §C5、08 篇）。

⚠️ 实践含义（05 §3.4 第 2 条）：
**在等 fence 或 signal fence 的路径上申请内存 ⇒ `fs_reclaim` 出现在 fence 之后 ⇒
回边 ⇒ lockdep splat。** 报告里会明确出现 `fs_reclaim` 和
`dma_fence_map`（那把假锁，`drivers/dma-buf/dma-fence.c:284` 的 `dma_fence_lockdep_map`）。

⚠️ **必须开 `CONFIG_PROVE_LOCKING`**，光有 `CONFIG_LOCKDEP` 不够
（`dma_resv_lockdep()` 的 `mm_alloc()` 在 `:744`，整个函数只在 lockdep 编入时存在）。

### §7.2 给自己的 signal 临界区加保护

```c
bool cookie = dma_fence_begin_signalling();   /* drivers/dma-buf/dma-fence.c:302 */
…
dma_fence_end_signalling(cookie);             /* drivers/dma-buf/dma-fence.c:325 */
```

⚠️ 这一对拿的是**假锁**（`drivers/dma-buf/dma-fence.c:284`），
只为让 lockdep 建立关系，没有任何真实互斥效果（03 §B6）。
⇒ **加上它零运行时成本（非 lockdep 内核编译成空），但能让整类 bug 提前暴露。**
**新写的 fence 生产方应该无条件加。**

⚠️ 配套的 `__dma_fence_might_wait()`（`drivers/dma-buf/dma-fence.c:334`）
是给"我这里会等 fence"的路径用的声明，`dma_resv_lockdep()` 内部就调它。

---

## §8 selftest

`CONFIG_DMABUF_SELFTESTS`（`drivers/dma-buf/Kconfig:63-66`，**tristate**）
产出一个 `dmabuf_selftests.ko`（`drivers/dma-buf/Makefile:11-16`）：

| 测试文件 | 行数 | 覆盖 |
|---|---|---|
| `st-dma-fence-chain.c` | 710 | fence chain |
| `st-dma-fence.c` | 593 | fence 状态机、回调、等待 |
| `st-dma-fence-unwrap.c` | 382 | fence 容器展开 |
| `st-dma-resv.c` | 316 | dma-resv 的增删与迭代 |
| `selftest.c` + `selftests.h` | 167 + 16 | 框架 |

⚠️⚠️ **没有任何一个 selftest 覆盖缓冲区共享层**（`dma_buf_export` / `attach` /
`map_attachment`）⇒ **`dma-buf.c` 那 1639 行改动之后，in-tree 测试一条都不会失败**。
验证只能靠 out-of-tree 的 IGT（`igt-gpu-tools`）或自己写模块。

用法：`modprobe dmabuf_selftests`，结果看 `dmesg`。
⚠️ 它是 tristate ⇒ **可以做成模块，跑完就卸掉**（与 heap 不同，04 §1）。

---

## §9 症状 → 查法

| 症状 | 先查 | 再查 |
|---|---|---|
| 缓冲区不释放 / 内存泄漏 | `fdinfo` 的 `count`（§1.1） | `bufinfo` 的 `Attached Devices`（§2.2）看谁没 detach |
| 进程 D 状态卡住 | `dma_fence_wait_start` 无配对 `wait_end`（§3.2） | `bufinfo` 里 `grep unsignalled`（§2.2） |
| 数据损坏（arm64） | 用户态有没有做 `DMA_BUF_IOCTL_SYNC`（05 §4.2） | 导出方有没有实现 `begin/end_cpu_access`（`bufinfo` 看 `exp_name` 定位导出方） |
| 只在某些机器上崩 | ⚠️ `CONFIG_DMABUF_DEBUG` 是否开着（§6） | 是不是在读 `sg_page()` |
| 内核 panic 在 `dma_resv_add_fence` | ⚠️ 漏了 `dma_resv_reserve_fences()`（05 §5 #1） | `BUG_ON` 在 `drivers/dma-buf/dma-resv.c:307` |
| lockdep splat 带 `fs_reclaim` | signal/wait 路径上分配了内存（§7.1） | 05 §3.4 |
| `/dev/dma_heap/` 里节点缺失 | 06 §5 的十步清单 | —— |
| `ioctl` 返回 `-ENOTTY` | `CONFIG_SYNC_FILE`（05 §4.6） | `drivers/dma-buf/dma-buf.c:483-488` 的编译开关 |
| `ioctl` 返回 `-EINVAL` 莫名其妙 | 结构体没清零 / 输出字段没传 0（05 §4.1） | `drivers/dma-buf/dma-heap.c:102-109` |
| `/sys/kernel/dma-buf/` 不存在 | ⚠️ **官方文档路径写错了**（§5.2） | 真实路径 `/sys/kernel/dmabuf/` |

⚠️ **表里没有"用 `printk` 加日志"这一项，因为核心几乎不打日志**：
`dma-buf.c` 全文件的用户可见日志只有 `bufinfo` 创建失败那一条 `pr_debug`（`:1591`）。
⇒ **这个子系统的可观测性完全依赖上面这些结构化设施，不是 dmesg。**

---

## §10 建议的两套配置

| 选项 | 生产内核 | 调试/CI 内核 |
|---|---|---|
| `DEBUG_FS` | y（`bufinfo` 值得留） | y |
| `DMABUF_DEBUG` | n | ⚠️ **y（必须）** |
| `PROVE_LOCKING` | n | ⚠️ **y（必须，`dma_resv_lockdep` 才跑）** |
| `DMABUF_SELFTESTS` | n | m |
| `DMABUF_SYSFS_STATS` | ⚠️ **n**（DEPRECATED，且会让空 `exp_name` 的导出方失败，§5.4） | n |
| `SW_SYNC` | ⚠️ **n**（能被用户态挂死内核，04 §5.4） | y（只在专门测 sync_file 时） |
| `SYNC_FILE` | 按需（用户态要 fence fd 就 y） | y |
| `DMABUF_MOVE_NOTIFY` | n（EXPERIMENTAL；但 n 意味着用户态可 pin 内存，04 §7） | y（想测动态 attach 路径就必须 y） |
| `FTRACE` | y | y |

⚠️ **`FTRACE` 建议两边都开**：tracepoint 不开启时开销近似为零，
而出问题时它是唯一能拿到时间线的手段（§3）。

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 这些接口的签名与返回值 | 01-api.md |
| `bufinfo` 里那些字段对应结构体的哪一项 | 02-datastructures.md |
| `mangle_sg_table` / `dma_resv_lockdep` 的实现 | 03-internals.md |
| 各个 Kconfig 选项到底控制什么 | 04-extensions.md |
| 怎么写出不需要调试的代码 | 05-usage.md |
| CMA heap 相关的排查 | 06-devicetree.md §5 |
| 为什么"等 fence 时不能分配内存"是必然的 | 08-feynman.md |
