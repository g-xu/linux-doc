---
title: 数据结构
---

# dma-buf 02：数据结构

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

**17 个公开结构 + 4 个核心私有结构**。字段数按花括号深度数过，
本子系统**没有任何结构用 `__randomize_layout`**，全部以裸 `};` 结尾
（与 tty 不同，那边的 `} __randomize_layout;` 会让"找第一个 `};`"的数法冲过头）。

---

## §0 一张全表先建立坐标

| 结构 | 位置 | 字段数 | 一句话 |
|---|---|---|---|
| `dma_buf` | `include/linux/dma-buf.h:302-458` | **17** | 一块可共享的缓冲区 |
| `dma_buf_ops` | `include/linux/dma-buf.h:36-288` | **13** | 导出方实现的回调集；⚠️ 只有 3 个必填 |
| `dma_buf_attachment` | `include/linux/dma-buf.h:517-527` | **9** | "某个 device 挂到某个 dma_buf 上"这件事 |
| `dma_buf_attach_ops` | `include/linux/dma-buf.h:465-493` | **2** | 导入方实现的回调集 |
| `dma_buf_export_info` | `include/linux/dma-buf.h:542-550` | **7** | `dma_buf_export()` 的唯一参数 |
| `dma_fence` | `include/linux/dma-fence.h:65-96` | **8** | 一次性完成信号；⚠️ 含一个三路 union |
| `dma_fence_ops` | `include/linux/dma-fence.h:125-260` | **9** | fence 实现方的回调集 |
| `dma_fence_cb` | `include/linux/dma-fence.h:116-119` | **2** | fence 完成回调节点 |
| `dma_resv` | `include/linux/dma-resv.h:155-179` | **2** | ⚠️ 整个预留对象只有两个字段 |
| `dma_resv_iter` | `include/linux/dma-resv.h:193-217` | **8** | fence 遍历游标 |
| `sync_file` | `include/linux/sync_file.h:35-54` | **7** | fd 化的一个 fence |
| `dma_heap_ops` | `include/linux/dma-heap.h:23-28` | **1** | ⚠️ 只有 `allocate` |
| `dma_heap_export_info` | `include/linux/dma-heap.h:38-42` | **3** | heap 注册参数 |
| `dma_fence_array` | `include/linux/dma-fence-array.h:37-46` | **6** | 等全部（AND）组合子 |
| `dma_fence_array_cb` | `include/linux/dma-fence-array.h:23-26` | **2** | array 的成员回调 |
| `dma_fence_chain` | `include/linux/dma-fence-chain.h:25-50` | **6** | 单调时间线节点 |
| `dma_fence_unwrap` | `include/linux/dma-fence-unwrap.h:18-31` | **3** | 拆套娃游标 |

核心私有（不在头文件里，外部驱动看不到）：

| 结构 | 位置 | 说明 |
|---|---|---|
| `struct dma_resv_list` | `drivers/dma-buf/dma-resv.c:63-67` | resv 真正存 fence 的数组，⚠️ **usage 打包在指针低位** |
| `struct dma_buf_stats_attribute` | `drivers/dma-buf/dma-buf-sysfs-stats.c:53-57` | sysfs 属性包装 |
| `struct dma_buf_poll_cb_t` | `include/linux/dma-buf.h:440-446` | ⚠️ **匿名嵌在 `dma_buf` 里**，算 `dma_buf` 的一个字段 |
| `struct dma_buf_sysfs_entry` | `include/linux/dma-buf.h:453-456` | 同上，`CONFIG_DMABUF_SYSFS_STATS` 下才存在 |

⚠️ **规模上的一个反差**：`dma_buf_ops` 占 253 行（`:36-288`）但只有 13 个字段
——**绝大部分是 kerneldoc**。这个子系统的"规格说明"确实写在结构体注释里，
而不是 `Documentation/` 下（01 §0 已提过 `dma-buf.rst` 主要是 kernel-doc 脚手架）。

---

## §1 `struct dma_buf`：17 个字段

`include/linux/dma-buf.h:302-458`

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `size_t size` | `:308` | ⚠️ **生命周期内不变**（`dma_buf_export_info` 的 kerneldoc `:534` 用了 "invariant"） |
| 2 | `struct file *file` | `:316` | 这块缓冲区的 fd 后端；引用计数就是它的 |
| 3 | `struct list_head attachments` | `:324` | 所有 `dma_buf_attachment` 的链表 |
| 4 | `const struct dma_buf_ops *ops` | `:327` | 导出方回调，见 §2 |
| 5 | `struct mutex lock` | `:336` | ⚠️ **只保护 vmap 那一组**，见下 |
| 6 | `unsigned vmapping_counter` | `:344` | `dma_buf_vmap()` 的嵌套计数 |
| 7 | `struct iosys_map vmap_ptr` | `:350` | 缓存的内核线性映射 |
| 8 | `const char *exp_name` | `:358` | 导出方名；`DEFINE_DMA_BUF_EXPORT_INFO` 填成 `KBUILD_MODNAME` |
| 9 | `const char *name` | `:366` | 用户态通过 `DMA_BUF_SET_NAME` 设的名字 |
| 10 | `spinlock_t name_lock` | `:369` | ⚠️ **专门保护 `name` 一个字段** |
| 11 | `struct module *owner` | `:377` | 导出方模块，防止 rmmod |
| 12 | `void *priv` | `:383` | 导出方私有指针；`dma_buf_export()` 必填 |
| 13 | `struct list_head list_node` | `:380` | 挂进全局 `db_list`，debugfs 遍历用 |
| 14 | `struct dma_resv *resv` | `:433` | ⚠️ **隐式同步的全部**，见 §5 |
| 15 | `wait_queue_head_t poll` | `:436` | `poll()`/`epoll` 的等待队列 |
| 16 | 匿名 `struct dma_buf_poll_cb_t cb_in, cb_out` | `:440-446` | poll 用的两个 fence 回调槽 |
| 17 | 匿名 `struct dma_buf_sysfs_entry *sysfs_entry` | `:453-456` | ⚠️ 仅 `CONFIG_DMABUF_SYSFS_STATS=y` |

### §1.1 ⚠️ 三把锁保护三块不相干的东西

这是 `dma_buf` 最容易搞错的地方——**它有三套互斥机制，各管一段**：

| 机制 | 保护的字段 | 谁来加 |
|---|---|---|
| `dmabuf->lock`（mutex，`:336`） | `vmapping_counter` + `vmap_ptr` | `dma_buf_vmap()`/`vunmap()` 内部 |
| `dmabuf->name_lock`（spinlock，`:369`） | 只有 `name`（`:366`） | `DMA_BUF_SET_NAME` ioctl 路径 |
| ⚠️ **`dmabuf->resv->lock`**（ww_mutex） | `attachments` 链表 + `resv->fences` + 导出方的 backing store | **调用者**（`dma_resv_lock()`） |

⇒ **最反直觉的一条：保护 attach/map 的锁不在 `dma_buf` 里，在它指向的 `dma_resv` 里。**
这就是 01 §2.2 那对 `_unlocked` API 存在的原因。
⚠️ 名字叫 `lock` 的那把（`:336`）**跟 attach/map 一点关系都没有**，
看到它不要以为"锁住 dma_buf 了"。

### §1.2 ⚠️ `poll` 的两个回调槽与隐式栅栏

DOC 块 `dma-buf.c:179`（标题 "implicit fence polling"）规定了 uapi 语义：

| poll 事件 | 等的是什么 |
|---|---|
| `EPOLLIN` | ⚠️ **最近一次写（exclusive）fence 的状态** |
| `EPOLLOUT` | ⚠️ **所有 fence 的状态** |

⇒ 用户态"等这块缓冲区可以读"就 poll `EPOLLIN`，
"等它可以被我重写"就 poll `EPOLLOUT`。这是 dma-buf **唯一的、不需要 sync_file
的用户态同步手段**。

⚠️⚠️ 同一个 DOC 块的最后一句是个大坑：
**poll 只表示 DMA 完成，不表示 cache 已经刷过。**
⇒ poll 返回可读之后，CPU 想读还是得发 `DMA_BUF_IOCTL_SYNC`。

---

## §2 `struct dma_buf_ops`：13 个字段，只有 3 个必填

`include/linux/dma-buf.h:36-288`（253 行，绝大部分是 kerneldoc）

| # | 成员 | 行 | 必填 | 说明 |
|---|---|---|---|---|
| 1 | `bool cache_sgt_mapping` | `:44` | ❌ | ⚠️ **唯一一个不是函数指针的成员**；置 true 让核心缓存 `sg_table` |
| 2 | `attach` | `:72` | ❌ | 有 device 挂上来 |
| 3 | `detach` | `:83` | ❌ | 拆下去 |
| 4 | `pin` | `:109` | ❌ | 动态导出方：钉住内存 |
| 5 | `unpin` | `:122` | ❌ | 解钉 |
| 6 | `map_dma_buf` | `:179` | ✅ | 产出 `sg_table` |
| 7 | `unmap_dma_buf` | `:189` | ✅ | 释放 `sg_table` |
| 8 | `release` | `:203` | ✅ | 最后一个引用没了 |
| 9 | `begin_cpu_access` | `:230` | ❌ | cache invalidate |
| 10 | `end_cpu_access` | `:247` | ❌ | cache flush |
| 11 | `mmap` | `:284` | ❌ | 映射到用户态 |
| 12 | `vmap` | `:286` | ❌ | 内核线性映射 |
| 13 | `vunmap` | `:287` | ❌ | 解除 |

必填名单来自 `dma_buf_export()` 的那个 `WARN_ON`（`dma-buf.c:608-620`，01 §2.1）。

⚠️⚠️ **10 个可选回调 = 10 个静默失败点**：

| 少了谁 | 症状 |
|---|---|
| `mmap` | 用户态 `mmap(fd,…)` 返回 `-EINVAL`（`dma-buf.c:1413`）；⚠️ **不会有任何日志** |
| `vmap` | `dma_buf_vmap()` 返回 `-EINVAL`；导入方拿不到内核映射 |
| `begin_cpu_access` / `end_cpu_access` | ⚠️⚠️ **`DMA_BUF_IOCTL_SYNC` 静默成功什么都不做** ⇒ arm64 上偶发花屏，换板子才暴露 |
| `attach` / `detach` | 无影响（核心只是不调） |
| `pin` / `unpin` | `dma_buf_pin()` 返回错误；动态导入方退化为必须 pin |
| `cache_sgt_mapping = false` | 每次 map 都真的重建 `sg_table`，只是慢 |

⇒ **写导出方的检查清单在 05 §1**。这里先记住一条：
**"编译过了 + `dma_buf_export()` 没返回错误"完全不代表实现是完整的。**

### §2.1 `map_dma_buf` 的三条隐含契约

kerneldoc 在 `:179` 一带说明（这里只提结论，实现细节在 03）：

1. ⚠️ **调用时 `dma_resv_lock()` 已被持有** ⇒ 里面不能再取任何会包住 fence wait 的锁；
2. ⚠️ **可以失败并返回 `ERR_PTR`**，但**不能返回 NULL**；
3. ⚠️ **必须能被同一个 attachment 反复调用**（每次 map/unmap 配对）。

`include/linux/dma-buf.h:193` 那条**全核心唯一的 TODO**
（`TODO: Add try_map_dma_buf version, to return immed with -EBUSY`）就在这个成员的注释里
⇒ **`map_dma_buf` 一定可能睡，没有非阻塞版本**。

---

## §3 `dma_buf_attachment` 与 `dma_buf_attach_ops`

### §3.1 `struct dma_buf_attachment`：9 个字段

`include/linux/dma-buf.h:517-527` —— 全部是裸字段，**没有一行 kerneldoc 在结构体内部**：

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `struct dma_buf *dmabuf` | `:518` | 挂到哪块缓冲区 |
| 2 | `struct device *dev` | `:519` | 哪个设备挂的；⚠️ **决定 DMA 地址空间和 IOMMU 域** |
| 3 | `struct list_head node` | `:520` | 挂进 `dmabuf->attachments` |
| 4 | `struct sg_table *sgt` | `:521` | ⚠️ 仅 `cache_sgt_mapping = true` 时有效 |
| 5 | `enum dma_data_direction dir` | `:522` | 同上 |
| 6 | `bool peer2peer` | `:523` | P2P DMA（不经过 struct page） |
| 7 | `const struct dma_buf_attach_ops *importer_ops` | `:524` | ⚠️ **非 NULL 才叫"动态 attach"** |
| 8 | `void *importer_priv` | `:525` | 导入方私有 |
| 9 | `void *priv` | `:526` | ⚠️ **导出方**私有（给 `.attach` 回调用） |

⚠️ **字段 8 和 9 是两方各自的私有指针，名字只差一个前缀**：
`importer_priv` 归导入方，`priv` 归导出方。写 `.attach` 回调时用错就是踩别人的数据。

⚠️ **`dev`（字段 2）不是装饰**：`map_dma_buf` 要用它做 `dma_map_sgtable()`，
所以**同一块缓冲区被两个设备 attach 会得到两个不同的 `sg_table`，DMA 地址不同**。
arm64 上如果两个设备在不同的 IOMMU 域里，这两组地址完全无关
——**不要把一个设备拿到的 DMA 地址喂给另一个设备**。

### §3.2 `struct dma_buf_attach_ops`：2 个字段决定 pin 与否

`include/linux/dma-buf.h:465-493`

| # | 成员 | 行 | 说明 |
|---|---|---|---|
| 1 | `bool allow_peer2peer` | `:472` | 置 true 表示"我能处理没有 `struct page` 的资源" |
| 2 | `void (*move_notify)(struct dma_buf_attachment *)` | `:492` | ⚠️ **可选**；有它核心就不必 pin backing store |

⚠️⚠️ **`move_notify` 是整个动态 dma-buf 的关键，它的锁契约写在 `:480-483`**：
调用时 **`dma_resv` 锁已被持有**，而且导入方**重新 map 时也必须在这把锁下做**
——这样才能保证"迁移进行中不会有新映射产生"。

`:485-490` 补了两条容易读漏的：

- **已有映射在回调返回后仍然"有效"**（不会被核心拆掉），但**内容所在的物理位置已经变了**
  ⇒ **导入方必须尽快自己销毁并重建所有映射**；
- **回调返回后就可以建新映射**，指向新位置。

⇒ 这就是 `CONFIG_DMABUF_MOVE_NOTIFY` 的意义。它的 Kconfig help
（`drivers/dma-buf/Kconfig:42-52`）自陈这修掉了一个**安全问题**：
**"userspace is able to pin unrestricted amounts of memory through DMA-buf"**。
⚠️ 但它 `default n` 且标着 "(EXPERIMENTAL)" ⇒ **默认内核上这个安全问题仍然存在**，
04 §6 和 07 会再提。

---

## §4 `struct dma_fence`：8 个字段，其中一个是三路 union

`include/linux/dma-fence.h:65-96`

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `spinlock_t *lock` | `:66` | ⚠️ **是指针**，多个 fence 可以共用一把锁（通常是所属 timeline 的锁） |
| 2 | `const struct dma_fence_ops *ops` | `:67` | 见 §4.2；⚠️ **同时充当类型标签**（`dma_fence_is_array()` 靠比它） |
| 3 | ⚠️ **union { `cb_list` / `timestamp` / `rcu` }** | `:84-90` | 见 §4.1 |
| 4 | `u64 context` | `:91` | 时间线 ID，`dma_fence_context_alloc()` 分配 |
| 5 | `u64 seqno` | `:92` | 同 context 内的序号 |
| 6 | `unsigned long flags` | `:93` | `DMA_FENCE_FLAG_*`，见 §4.3 |
| 7 | `struct kref refcount` | `:94` | |
| 8 | `int error` | `:95` | 负错误码；`dma_fence_set_error()` 在 signal 前写 |

### §4.1 ⚠️ 三路 union：同一块内存在生命周期的三个阶段被复用

结构体自己的注释（`:68-83`）解释了这个设计。三个阶段互不重叠：

| 阶段 | union 里用的成员 | 什么时候 |
|---|---|---|
| 未完成 | `struct list_head cb_list` | 从 `dma_fence_init()` 到 signal 之前，挂着所有 `dma_fence_cb` |
| 已完成 | `ktime_t timestamp` | signal 的那一刻起，记完成时间 |
| 待释放 | `struct rcu_head rcu` | refcount 归零后，`dma_fence_free()` 走 RCU 延迟释放 |

⇒ **这是 dma-buf 里最容易写出 UAF 的一处**。两条硬规矩：

1. ⚠️⚠️ **signal 之后绝对不能再碰 `cb_list`** ——那块内存现在是 `timestamp`。
   所以 `dma_fence_add_callback()` 对已 signal 的 fence 返回 `-ENOENT`
   （`dma-fence.c:654`）而不是"加进去"；
2. ⚠️ **读 `timestamp` 前必须先确认 `DMA_FENCE_FLAG_TIMESTAMP_BIT`**。
   辅助函数 `dma_fence_timestamp()`（`include/linux/dma-fence.h:574`）做了这个检查
   ——**不要直接读 `fence->timestamp`**。

⇒ 这也解释了为什么 `struct dma_fence` 只有 8 个字段却这么难用对：
**它把"未完成对象"和"已完成记录"塞进了同一个 40 多字节的结构里。**

### §4.2 `struct dma_fence_ops`：9 个成员

`include/linux/dma-fence.h:125-260`

| # | 成员 | 行 | 必填 | 说明 |
|---|---|---|---|---|
| 1 | `bool use_64bit_seqno` | `:132` | ❌ | ⚠️ **唯一的非函数成员**；置 false 时 seqno 按 32 位环绕比较 |
| 2 | `get_driver_name` | `:143` | ✅ | trace / debugfs 显示用 |
| 3 | `get_timeline_name` | `:155` | ✅ | 同上 |
| 4 | `enable_signaling` | `:192` | ❌ | 有人开始等了，打开中断/软件通知 |
| 5 | `signaled` | `:209` | ❌ | 快速查询（不阻塞） |
| 6 | `wait` | `:227` | ❌ | 不填就用 `dma_fence_default_wait()`（`dma-fence.c:808`） |
| 7 | `release` | `:238` | ❌ | 不填就用 `dma_fence_free()`（`dma-fence.c:577`） |
| 8 | `fence_value_str` | `:248` | ❌ | debugfs 打印用 |
| 9 | `timeline_value_str` | `:258` | ❌ | 同上 |

⚠️ **只有两个 name 回调是真必填的**——`dma_fence_stub_ops`
（`dma-fence.c:119-122`）就只填了这两个，因为桩 fence 出生即完成。

⚠️⚠️ **`use_64bit_seqno` 填错是一类沉默 bug**：
`__dma_fence_is_later()`（`include/linux/dma-fence.h:451`）在 `:458` 分支，
false 时做 32 位环绕比较。`dma_fence_chain` 用的是用户态给的任意 64 位 seqno
⇒ **chain 类 fence 必须置 true**，否则 seqno 超过 2^31 后比较结果翻转，
表现为"已经完成的 fence 被判定为更早"⇒ 同步顺序错乱。

### §4.3 `enum dma_fence_flag_bits`：4 个位

`include/linux/dma-fence.h:98-103`

| 位 | 含义 |
|---|---|
| `DMA_FENCE_FLAG_SIGNALED_BIT` | 已完成 |
| `DMA_FENCE_FLAG_TIMESTAMP_BIT` | `timestamp` 有效（§4.1 第 2 条） |
| `DMA_FENCE_FLAG_ENABLE_SIGNAL_BIT` | `enable_signaling` 已调过 |
| `DMA_FENCE_FLAG_USER_BITS` | ⚠️ **必须永远是最后一个**；驱动自定义位从这里往上用 |

⇒ 驱动要加自己的标志就写 `MY_FLAG = DMA_FENCE_FLAG_USER_BITS`、
`MY_FLAG2 = DMA_FENCE_FLAG_USER_BITS + 1`。**不要写死数字**。

### §4.4 `struct dma_fence_cb`：2 个字段

`include/linux/dma-fence.h:116-119` —— 一个 `struct list_head node` 和一个函数指针。
⚠️ **它挂在 §4.1 那个 union 的 `cb_list` 上** ⇒ fence signal 之后这个节点
已经不在任何链表里了，**`dma_fence_remove_callback()` 返回 false 就说明
回调可能正在别的 CPU 上执行**（01 §3.3）。

---

## §5 `struct dma_resv`：两个字段撑起整个隐式同步

`include/linux/dma-resv.h:155-179`

```c
struct dma_resv {
        struct ww_mutex lock;                    /* :166 */
        struct dma_resv_list __rcu *fences;      /* :178 */
};
```

⚠️ **就这两个。** 25 行里 23 行是 kerneldoc。两段注释各说了一件关键的事：

- `:159-160`：**"Don't use directly, instead use the wrapper functions like
  `dma_resv_lock()` and `dma_resv_unlock()`"** ⇒ 不要直接 `ww_mutex_lock(&resv->lock, …)`。
- `:162-164`：**这把锁在动态内存管理的驱动里同时保护 buffer object 的
  placement、分配策略、整条命令提交路径** ⇒ ⚠️ **它是一把范围极大的锁，
  不是"只锁 fence 列表"**。这就是 §1.1 那张表最后一行的由来。
- `:173-176`：**`dma_resv_add_fence()` 常常发生在命令提交的"不可回退点"之后，
  所以它不能失败**，因此**必须先 `dma_resv_reserve_fences()` 预留槽位**
  （01 §4.4 那条硬顺序，理由就在这里）。

### §5.1 私有的 `struct dma_resv_list`：usage 打包在指针低位

`drivers/dma-buf/dma-resv.c:63-67`

```c
struct dma_resv_list {
        struct rcu_head rcu;
        u32 num_fences, max_fences;
        struct dma_fence __rcu *table[];
};
```

⚠️⚠️ **`table[]` 里存的不是干净的指针**：`dma-resv.c:61` 定义了

```c
#define DMA_RESV_LIST_MASK      0x3
```

⇒ **`enum dma_resv_usage` 的 4 个值（0–3）被塞进了 fence 指针的低 2 位**。
这就是为什么 `dma_resv_iter` 的 kerneldoc 在 `:209` 写
**"the shared fences; private, *MUST* not dereference"**
——**直接解引用 `iter->fences` 或 `list->table[i]` 会拿到一个错位的指针**。

⇒ 两条实践后果：

1. **必须用 `dma_resv_iter_*` 迭代器**，不能自己遍历 `table[]`；
2. **fence 结构体的对齐必须 ≥ 4 字节**（`kmalloc` 天然满足），
   自己用奇怪的分配器给 fence 分配内存会破坏这个假设。

`num_fences` 是当前实际数，`max_fences` 是已预留的容量
——`dma_resv_reserve_fences()`(`dma-resv.c:240`) 抬 `max_fences`，
`dma_resv_reset_max_fences()`(`:261`) 把它压回 `num_fences`。

### §5.2 `struct dma_resv_iter`：8 个字段

`include/linux/dma-resv.h:193-217`

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `struct dma_resv *obj` | `:195` | 遍历哪个 resv |
| 2 | `enum dma_resv_usage usage` | `:198` | ⚠️ **"这一档及以下"**，不是精确匹配 |
| 3 | `struct dma_fence *fence` | `:201` | 当前 fence |
| 4 | `enum dma_resv_usage fence_usage` | `:204` | 当前 fence 的档；用 `dma_resv_iter_usage()` 读（`:257`） |
| 5 | `unsigned int index` | `:207` | 内部游标 |
| 6 | `struct dma_resv_list *fences` | `:210` | ⚠️ **"private, MUST not dereference"** |
| 7 | `unsigned int num_fences` | `:213` | |
| 8 | `bool is_restarted` | `:216` | ⚠️ 用 `dma_resv_iter_is_restarted()` 读（`:269`） |

⚠️ **字段 2 的"及以下"语义**：`usage = DMA_RESV_USAGE_READ`（值 2）
会同时给出 `KERNEL`(0) 和 `WRITE`(1) 和 `READ`(2) 的 fence，**不包括 `BOOKKEEP`**(3)。
这正是 01 §4.2 那张表"谁必须等它"的实现方式——**档位的数值顺序就是包含关系**。

⚠️⚠️ 无锁迭代器**会重启**（头文件 `:186-191` 的警告，01 §4.5），
字段 8 就是给这件事用的。**累加型的遍历必须每轮检查它并清零。**

---

## §6 `struct sync_file`：7 个字段

`include/linux/sync_file.h:35-54`

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `struct file *file` | `:36` | 匿名 inode 后端（`sync_file.c:31`） |
| 2 | `char user_name[32]` | `:44` | ⚠️ **固定 32 字节**，见下 |
| 3 | `struct list_head sync_file_list` | `:46` | ⚠️ **仅 `CONFIG_DEBUG_FS`**（`:45` 的 `#ifdef`） |
| 4 | `wait_queue_head_t wq` | `:49` | poll 等待队列 |
| 5 | `unsigned long flags` | `:50` | |
| 6 | `struct dma_fence *fence` | `:52` | ⚠️ **只有一个**——多 fence 靠塞一个 `dma_fence_array` |
| 7 | `struct dma_fence_cb cb` | `:53` | 用来接 fence 的完成通知 |

⚠️ **字段 2 的 kerneldoc（`:38-42`）说明了一个反直觉点**：
`user_name` **只有 merge 出来的 sync_file 才有值**；
普通的 sync_file **整个数组是 0**，名字是**运行时通过驱动回调生成的**
（`sync_file_get_name()`，`include/linux/sync_file.h:60`，⚠️ **未导出**）。
⇒ 直接读 `sync_file->user_name` 在多数情况下会读到空串，
`SYNC_IOC_FILE_INFO`（`include/uapi/linux/sync_file.h:96`）返回的名字才是完整的。

⚠️ **字段 3 带 `#ifdef CONFIG_DEBUG_FS`** ⇒ 这个结构的大小随配置变化。
对齐 tty 那次教训：**数字段时 `#if` 里的要单独记**——这里 7 个字段里
**有 1 个是条件编译的，无条件的是 6 个**。

---

## §7 heap 的两个结构：合起来 4 个字段

### §7.1 `struct dma_heap_ops`：1 个成员

`include/linux/dma-heap.h:23-28`

```c
struct dma_heap_ops {
        struct dma_buf *(*allocate)(struct dma_heap *heap,
                                    unsigned long len,
                                    unsigned long fd_flags,
                                    unsigned long heap_flags);
};
```

⚠️ **整个 heap 抽象就这一个回调**，而且 kerneldoc（`:21`）明确
**"returns dmabuf on success, `ERR_PTR(-errno)` on error"**
⇒ 返回值是 `struct dma_buf *` 而不是 fd，**fd 化由核心的 ioctl 层做**。

### §7.2 `struct dma_heap_export_info`：3 个字段

`include/linux/dma-heap.h:38-42`：`name`(`:39`) · `ops`(`:40`) · `priv`(`:41`)。

⚠️ `name`（`:32` 的 kerneldoc："used for debugging/device-node name"）
**直接变成 `/dev/dma_heap/<name>`** ⇒ 这个字符串是**用户可见的 ABI**，
定了就不能改。树内两个值：`"system"`（`heaps/system_heap.c`）
和 CMA 区域名（`heaps/cma_heap.c`，跟着 DT 的 `reserved-memory` 节点名，06 会展开）。

⚠️ `struct dma_heap` 本身是**不完整类型**（`include/linux/dma-heap.h:15` 只有前向声明）
⇒ heap 驱动**只能通过 `dma_heap_get_drvdata()`(`:51`) 和 `dma_heap_get_name()`(`:60`)
访问它**，看不到内部字段。这是本子系统里唯一一处做了完整封装的地方。

---

## §8 三个组合子结构

### §8.1 `struct dma_fence_array`：6 个字段

`include/linux/dma-fence-array.h:37-46`

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `struct dma_fence base` | `:38` | ⚠️ **内嵌而非指针** ⇒ array 本身就是一个 fence |
| 2 | `spinlock_t lock` | `:40` | ⚠️ `base.lock` 指向它 |
| 3 | `unsigned num_fences` | `:41` | |
| 4 | `atomic_t num_pending` | `:42` | 还没完成的个数，归零时 signal |
| 5 | `struct dma_fence **fences` | `:43` | ⚠️ **所有权归 array**（01 §5.1） |
| 6 | `struct irq_work work` | `:45` | 从中断上下文安全地 signal |

配套的 `struct dma_fence_array_cb`（`:23-26`，2 个字段）是**每个成员一个**的回调节点。

### §8.2 `struct dma_fence_chain`：6 个字段，也含一个 union

`include/linux/dma-fence-chain.h:25-50`

| # | 字段 | 行 | 说明 |
|---|---|---|---|
| 1 | `struct dma_fence base` | `:26` | 内嵌 |
| 2 | `struct dma_fence __rcu *prev` | `:27` | ⚠️ 指向链上更早的节点，**RCU 保护** |
| 3 | `u64 prev_seqno` | `:28` | 前驱的 seqno |
| 4 | `struct dma_fence *fence` | `:29` | 这个节点真正包着的 fence |
| 5 | ⚠️ **union { `cb` / `work` }** | `:30-48` | 见下 |
| 6 | `spinlock_t lock` | `:49` | |

⚠️ **第二个 union**（继 `dma_fence` 之后）：`struct dma_fence_cb cb`(`:38`) 和
`struct irq_work work`(`:47`) 复用同一块内存，两段注释都写着
**"Never used at the same time as the …"**（`:35-36` / `:44-45`）。
`:43-44` 说明了为什么要有 irq_work 这条路：
**"to allow us to add the callback without running into lock inversion"**
⇒ 加回调这件事本身可能造成锁反转，所以要甩到 irq_work 里去做。

⚠️ **`prev` 是 RCU 指针 ⇒ `dma_fence_chain_walk()`(`dma-fence-chain.c:77`) 会顺路做 GC**：
已经 signal 的中间节点被摘掉，链会变短。**不要缓存链上任何中间节点的指针。**

### §8.3 `struct dma_fence_unwrap`：3 个字段

`include/linux/dma-fence-unwrap.h:18-31`：
`chain`(`:22`) · `array`(`:26`) · `index`(`:30`)。

⚠️ 两个字段的 kerneldoc 都写着 **"potential … but can be other fence as well"**
（`:20` / `:24`）⇒ **这两个字段的类型是"可能是也可能不是"**，
判断靠 `dma_fence_is_chain()`/`is_array()`（01 §5.3）。
⇒ **只能通过 `dma_fence_unwrap_for_each()` 宏使用，不要手动读这三个字段。**

---

## §9 五组关系图

### §9.1 一块缓冲区、多个导入方

```
                    ┌─────────────────────────────────────┐
                    │ struct dma_buf                      │
   fd (用户态) ────▶│  file ──────────▶ struct file       │
                    │  ops  ──────────▶ dma_buf_ops (13)  │──▶ 导出方驱动
                    │  priv ──────────▶ 导出方的 bo       │
                    │  resv ──────────▶ struct dma_resv ──┼──▶ ww_mutex + fence 列表
                    │  attachments ─┐                     │
                    └───────────────┼─────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
     dma_buf_attachment    dma_buf_attachment    dma_buf_attachment
       dev = GPU             dev = 显示控制器      dev = V4L2 设备
       sgt = <地址组 A>      sgt = <地址组 B>      sgt = <地址组 C>
```

⚠️ **三组 `sg_table` 里的 DMA 地址互不相同**（§3.1）
——同一块物理内存，三个设备三套地址。

### §9.2 三种"名字"

| 名字 | 字段 | 谁设 | 在哪能看到 |
|---|---|---|---|
| `exp_name` | `dma_buf.exp_name`(`:358`) | 导出方（`DEFINE_DMA_BUF_EXPORT_INFO` 填 `KBUILD_MODNAME`） | debugfs `bufinfo` · sysfs `exporter_name` |
| `name` | `dma_buf.name`(`:366`) | ⚠️ **用户态**通过 `DMA_BUF_SET_NAME` ioctl | debugfs `bufinfo` |
| heap 的 `name` | `dma_heap_export_info.name`(`:39`) | heap 驱动 | ⚠️ **变成 `/dev/dma_heap/<name>`，是 ABI** |

⚠️ 前两个都在 `dma_buf` 里，**差一个前缀，含义完全不同**：
`exp_name` 是"谁造的"，`name` 是"用户态给它起的绰号"。
排查时**只有 `exp_name` 可信**（用户态可以把 `name` 设成任意 31 字符内的字符串，
`DMA_BUF_NAME_LEN` = 32，`include/uapi/linux/dma-buf.h:86`）。

### §9.3 三个层次的"锁"

```
dma_resv->lock (ww_mutex)   ← 最外层，保护 attach/map/fence 列表/backing store
   └─ dma_buf->lock (mutex)     ← 只保护 vmap 计数
   └─ dma_buf->name_lock (spin) ← 只保护 name
   └─ dma_fence->lock (spin*)   ← 保护单个 fence 的 cb_list / flags
                                  ⚠️ 是指针，多个 fence 常共用 timeline 的锁
```

⚠️⚠️ **最外层那把不在 `dma_buf` 里** —— 它在 `dmabuf->resv` 指向的对象里，
而那个对象**可能是导出方自己的 bo 里内嵌的**（`dma_buf_export_info.resv` 非 NULL 时）
⇒ **同一把 ww_mutex 同时锁着 dma-buf 和导出方的 buffer object**，
这就是 §5 里 `:162-164` 那段注释的实际含义。

### §9.4 fence 的三种容器（可以套娃）

```
dma_fence（base 内嵌）
   ├── dma_fence_array   ── fences[] ──▶ dma_fence × N        （AND 语义）
   ├── dma_fence_chain   ── prev ──▶ dma_fence_chain ──▶ …    （时间线）
   │                     ── fence ──▶ dma_fence               （节点内容）
   └── 普通驱动 fence
```

⚠️ **array 的成员可以是 chain，chain 的内容可以是 array** ⇒ 任意深度嵌套。
`dma_fence_unwrap_for_each()`（`include/linux/dma-fence-unwrap.h:47-49`）
就是为了把这棵树拍平成一串叶子 fence。

⚠️ 判断类型靠**比 `ops` 指针**，所以 `dma_fence_array_ops`(`dma-fence-array.c:159`)
和 `dma_fence_chain_ops`(`dma-fence-chain.c:217`) **必须导出**（01 §5.1/§5.2）。

### §9.5 生命周期与引用计数

| 对象 | 引用计数在哪 | 加/减 |
|---|---|---|
| `dma_buf` | ⚠️ **`dmabuf->file` 的 `f_count`**，不在 `dma_buf` 里 | `get_dma_buf()`（`include/linux/dma-buf.h:572-575`，实现就是 `get_file()`）/ `dma_buf_put()` |
| `dma_fence` | `fence->refcount`（kref，`include/linux/dma-fence.h:94`） | `dma_fence_get()`(`:285`) / `dma_fence_put()`(`:273`) |
| `dma_buf_attachment` | ⚠️ **没有引用计数** | `dma_buf_attach()` / `dma_buf_detach()` 严格配对 |
| `dma_resv` | ⚠️ **没有自己的引用计数**，跟着宿主 | 由 `dma_buf` 或导出方 bo 管 |
| `sync_file` | `sync_file->file` 的 `f_count` | `fget`/`fput` |

⚠️⚠️ **`dma_buf` 的引用计数在 `struct file` 里，这是它能当 fd 传的根本原因**，
也带来一个陷阱：**`dma_buf_fd()` 成功之后，fd 表持有一份引用；
失败时必须自己 `dma_buf_put()`**，否则泄漏。

⚠️ **RCU 三处**：`dma_resv->fences`(`:178`) · `dma_fence_chain->prev`(`:27`) ·
`dma_fence` union 里的 `rcu`(`include/linux/dma-fence.h:84-90`)。
⇒ **这三处的读取都不需要锁，但读到的对象随时可能已经在等 grace period**
⇒ 拿到 fence 指针后要立刻 `dma_fence_get_rcu()`（`include/linux/dma-fence.h:299`）
提引用，否则用着就没了。这是 03 §C4 的主题。

---

## §10 接着读

- 每个字段是被哪条路径写进去的 → [03-internals.md](03-internals.md)
- 这些结构在 heap / udmabuf / sw_sync 里怎么被复用 → [04-extensions.md](04-extensions.md)
- 填这些结构的可编译骨架 → [05-usage.md](05-usage.md)
- debugfs / sysfs 里这些字段怎么显示出来 → [07-debug.md](07-debug.md)
- 为什么 `dma_fence` 要用 union 省内存 → [08-feynman.md](08-feynman.md)
