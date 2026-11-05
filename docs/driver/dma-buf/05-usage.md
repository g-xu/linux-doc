---
title: 使用指南
---

# dma-buf 05：怎么用

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

本篇给四种角色的可编译骨架 + 每种角色的必检清单：
**§1 导出方** · **§2 导入方** · **§3 fence 生产方** · **§4 用户态** · **§5 通用陷阱清单**。

⚠️ 所有骨架都基于 01–04 已核实的行号，不是伪代码；但**每一段都要按你自己的
Kconfig 重新确认**（本篇末尾 §6 是配置检查清单）。

---

## §0 第一件事：`MODULE_IMPORT_NS(DMA_BUF)`

⚠️⚠️ **只要你的模块调了任何 `dma_buf_*` 函数，第一行就要加这个**：

```c
MODULE_IMPORT_NS(DMA_BUF);
```

否则 `modprobe` 时报 **"module uses symbols from namespace DMA_BUF, but does
not import it"** 并拒绝加载 ——**不是链接错误，是加载错误**，编译期完全看不出来。

原因见 01 §1：`dma-buf.c` 的 19 个导出全是 `EXPORT_SYMBOL_NS_GPL(…, DMA_BUF)`。

| 你调的是 | 要不要 `MODULE_IMPORT_NS` |
|---|---|
| 只有 `dma_fence_*` | ⚠️ **不要**（`dma-fence.c` 20 个导出全是普通 `EXPORT_SYMBOL`） |
| 只有 `dma_resv_*` | ⚠️ **不要**（10 个 `EXPORT_SYMBOL` + 7 个 `_GPL`，都不带命名空间） |
| 任何 `dma_buf_*` | **要** |
| `sync_file_*` | 不要（`sync_file.c` 两个导出是普通 `EXPORT_SYMBOL`） |

全树 24 个 `.c` 文件加了它，参考样例：
`drivers/gpu/drm/drm_prime.c:43` · `drivers/infiniband/core/umem_dmabuf.c:13` ·
`drivers/media/common/videobuf2/videobuf2-dma-contig.c:872`。

⚠️ 三个样例的位置不同（文件头 / 文件尾），**位置不影响效果**，
但**必须在同一个 `.ko` 的某个 `.c` 里出现一次**。

---

## §1 写导出方

### §1.1 最小骨架

```c
MODULE_IMPORT_NS(DMA_BUF);

struct my_buffer {
        struct sg_table sg_table;
        size_t len;
        /* 你自己的字段 */
};

/* ---- 三个必填回调（03 §A2 第 1 步只校验这三个 + priv + ops）---- */

static struct sg_table *my_map_dma_buf(struct dma_buf_attachment *attach,
                                       enum dma_data_direction dir)
{
        struct my_buffer *b = attach->dmabuf->priv;
        struct sg_table *table;

        dma_resv_assert_held(attach->dmabuf->resv);   /* 契约：调用方已持锁 */

        table = /* 复制一份 b->sg_table —— 每个 attachment 一份 */;
        if (dma_map_sgtable(attach->dev, table, dir, 0))
                return ERR_PTR(-ENOMEM);
        return table;
}

static void my_unmap_dma_buf(struct dma_buf_attachment *attach,
                             struct sg_table *table,
                             enum dma_data_direction dir)
{
        dma_unmap_sgtable(attach->dev, table, dir, 0);
        sg_free_table(table);
        kfree(table);
}

static void my_release(struct dma_buf *dmabuf)
{
        struct my_buffer *b = dmabuf->priv;
        /* 释放后备内存 */
        kfree(b);
}

static const struct dma_buf_ops my_ops = {
        .map_dma_buf     = my_map_dma_buf,
        .unmap_dma_buf   = my_unmap_dma_buf,
        .release         = my_release,
        /* 下面 10 个可选，但见 §1.3 */
};

/* ---- 导出 ---- */
static int my_export(struct my_buffer *b, unsigned int fd_flags)
{
        DEFINE_DMA_BUF_EXPORT_INFO(exp_info);   /* ⚠️ 用宏，别手写 = {} */
        struct dma_buf *dmabuf;

        exp_info.exp_name = "my-driver";        /* ⚠️ 见 §1.2 */
        exp_info.owner    = THIS_MODULE;
        exp_info.ops      = &my_ops;
        exp_info.size     = b->len;
        exp_info.flags    = fd_flags;           /* 通常 O_RDWR */
        exp_info.priv     = b;
        /* exp_info.resv 留 NULL ⇒ 核心分配一个独立的 dma_resv（03 §A2 第 6 步） */

        dmabuf = dma_buf_export(&exp_info);
        if (IS_ERR(dmabuf))
                return PTR_ERR(dmabuf);

        return dma_buf_fd(dmabuf, fd_flags & O_CLOEXEC);   /* ⚠️ 见 §4.1 */
}
```

⚠️ **`DEFINE_DMA_BUF_EXPORT_INFO(exp_info)`（`include/linux/dma-buf.h:552-555`）
会把 `exp_name` 预填成 `KBUILD_MODNAME`、`owner` 预填成 `THIS_MODULE`**
⇒ 用了宏就可以省掉上面那两行。**两个 heap 都没用这个宏
（`system_heap.c:427` 是裸声明），所以它们必须手填全部字段** ——
新增字段时那种写法会漏，别学。

### §1.2 `exp_name` 不能空

⚠️⚠️ `dma_buf_stats_setup()`（`dma-buf-sysfs-stats.c:171`）在 `exp_name` 为空时
打 `pr_err("exporter name must not be empty if stats needed\n")`（`:177`）**并失败**
⇒ **`CONFIG_DMABUF_SYSFS_STATS=y` 时，`exp_name` 为空会让整个
`dma_buf_export()` 失败**（03 §A2 第 8 步）。

⚠️ `exp_name` 还会出现在三个地方：
`/proc/<pid>/fd/` 的符号链接名（03 §A1）· debugfs 的 `bufinfo` ·
sysfs 的 `exporter_name`
⇒ **给它一个能一眼认出来源的名字**（两个 heap 用的是
`dma_heap_get_name(heap)`，`system_heap.c:394` / `cma_heap.c:344`
⇒ heap 名直接成为 exp_name）。

### §1.3 那 10 个"可选"回调，哪些其实是必填

02 §2 已给出完整的"少了谁 → 什么症状"表。这里只列**实践上必须实现**的：

| 回调 | 不实现的后果 | 什么时候真能省 |
|---|---|---|
| `begin_cpu_access` / `end_cpu_access` | ⚠️⚠️ **`DMA_BUF_IOCTL_SYNC` 静默返回 0**（03 §A5.1）⇒ arm64 上间歇性数据损坏 | 只在内核内共享、用户态永远拿不到 fd |
| `mmap` | `dma_buf_mmap()` → `-EINVAL`（`dma-buf.c:1413`） | 用户态不需要 CPU 访问 |
| `vmap` / `vunmap` | `dma_buf_vmap()` 失败 | 没有内核态 CPU 访问需求 |
| `attach` / `detach` | 无（核心跳过） | ⚠️ 多数情况真的可以省 |
| `pin` / `unpin` | 动态 attach 时导入方无法 pin | 静态导出方不需要 |

⚠️⚠️ **arm64 上 `begin/end_cpu_access` 是"必须"而不是"可选"**：
arm64 的 CPU cache 和设备 DMA 不自动一致（除非缓冲区是 coherent 的），
不刷 cache 就是数据损坏。两个 heap 都实现了
（`system_heap.c:154`/`:175`，`cma_heap.c:121`/`:142`）。

### §1.4 抄哪个模板

| 你的场景 | 抄 | 为什么 |
|---|---|---|
| 从系统内存分配，多段 sg | `drivers/dma-buf/heaps/system_heap.c` | 完整实现全部 10 个回调，440 行 |
| 从连续物理内存（CMA）分配 | `drivers/dma-buf/heaps/cma_heap.c` | 同上，且 `mmap` 走 `vm_fault` |
| 包装已有的页数组 | `drivers/dma-buf/udmabuf.c` | 页级 pin + `vm_fault` 样例 |
| GEM/DRM 对象 | `drivers/gpu/drm/drm_prime.c:881`（`drm_gem_prime_export`） | ⚠️ 它把 `exp_info.resv` 指向 GEM 对象的 resv（03 §A2） |

⚠️ 分配路径的两个共同细节，两个 heap 都有：

```c
/* system_heap.c:367-370 —— 每一轮分配前检查致命信号 */
if (fatal_signal_pending(current)) { ret = -EINTR; goto free_buffer; }

/* cma_heap.c:321-322 —— 清零大缓冲区时也检查 */
if (fatal_signal_pending(current)) goto free_cma;
```

⚠️⚠️ **大缓冲区分配/清零必须能被 SIGKILL 打断** ——
一个 1 GB 的 CMA 分配要清零 1 GB 内存，不检查信号就是不可杀的进程。
`cma_heap.c:317-320` 的注释原文就写着 "Avoid wasting time zeroing memory if
the process has been killed by by SIGKILL"（`by by` 是原文的笔误）。

⚠️ **CMA heap 一定会清零**（`cma_heap.c:307-328`）：highmem 走
`kmap_atomic` 逐页（`:313-316`），否则一次 `memset`（`:327`）。
**system heap 靠 `__GFP_ZERO`**（`:43` 的 `LOW_ORDER_GFP`）。
⇒ **两个 heap 分出来的内存都是清零的**，用户态可以依赖这一点（防信息泄露）。

⚠️ `cma_heap.c:300-301` 把对齐夹到 `CONFIG_CMA_ALIGNMENT`：

```c
if (align > CONFIG_CMA_ALIGNMENT)  align = CONFIG_CMA_ALIGNMENT;
```

⇒ **超大分配拿不到"自然对齐"** ——需要严格对齐的硬件要自己检查返回的地址。

---

## §2 写导入方

### §2.1 静态导入（绝大多数驱动）

```c
MODULE_IMPORT_NS(DMA_BUF);

struct my_import {
        struct dma_buf            *dmabuf;
        struct dma_buf_attachment *attach;
        struct sg_table           *sgt;
};

static int my_import_fd(struct my_import *m, struct device *dev, int fd)
{
        m->dmabuf = dma_buf_get(fd);              /* -EINVAL/-EBADF */
        if (IS_ERR(m->dmabuf))
                return PTR_ERR(m->dmabuf);

        m->attach = dma_buf_attach(m->dmabuf, dev);
        if (IS_ERR(m->attach))
                goto err_put;

        /* ⚠️ 不持锁 ⇒ 用 _unlocked 版（01 §2.2） */
        m->sgt = dma_buf_map_attachment_unlocked(m->attach, DMA_BIDIRECTIONAL);
        if (IS_ERR(m->sgt))
                goto err_detach;

        return 0;

err_detach:
        dma_buf_detach(m->dmabuf, m->attach);
err_put:
        dma_buf_put(m->dmabuf);
        return PTR_ERR(m->attach);
}

static void my_release_import(struct my_import *m)
{
        dma_buf_unmap_attachment_unlocked(m->attach, m->sgt, DMA_BIDIRECTIONAL);
        dma_buf_detach(m->dmabuf, m->attach);
        dma_buf_put(m->dmabuf);
}
```

⚠️⚠️ **拆的顺序必须严格倒过来**：unmap → detach → put。
跳过 unmap 直接 detach 会在 `dma_buf_detach()` 里被抓（它会 WARN
未 unmap 的 attachment），但**跳过 detach 直接 put 会泄漏 attachment**。

⚠️ 参考实现：`drivers/media/common/videobuf2/videobuf2-dma-contig.c`
——`dma_buf_attach()` 在 `:795`，`dma_buf_map_attachment()` 在 `:714`，
`dma_buf_detach()` 在 `:771`。⚠️ 注意它用的是**持锁版**
`dma_buf_map_attachment()`（6.1 里两个名字都存在），
**新代码应该用 `_unlocked` 版**除非你确实持锁。

### §2.2 ⚠️ 用 sg 表的铁律：只碰 DMA 地址

```c
struct scatterlist *sg;
int i;

for_each_sgtable_dma_sg(m->sgt, sg, i) {
        dma_addr_t addr = sg_dma_address(sg);   /* ✔ */
        unsigned int len = sg_dma_len(sg);      /* ✔ */
        /* struct page *p = sg_page(sg);        ✘ 绝对不要 */
}
```

⚠️⚠️ `CONFIG_DMABUF_DEBUG=y` 时 `mangle_sg_table()`（`dma-buf.c:763`）
**会故意把 `page` 指针打乱**（03 §A3.2）⇒ 偷看 `sg_page()` 立刻崩。
**关着开关时同一段代码"能跑"** ——这是一类只在调试内核上暴露的 bug。

⚠️ 遍历宏也要选对：**`for_each_sgtable_dma_sg`**（按 `nents`，DMA 侧）
而不是 `for_each_sgtable_sg`（按 `orig_nents`，CPU 侧）。
IOMMU 会把多段合并成一段，两个计数不同。

### §2.3 动态导入（需要 `move_notify`）

只有真正需要让导出方迁移内存时才用。样例：
`drivers/infiniband/core/umem_dmabuf.c`。

```c
static void my_move_notify(struct dma_buf_attachment *attach)
{
        /* ⚠️ 调用时已持 attach->dmabuf->resv 锁（02 §3） */
        dma_resv_assert_held(attach->dmabuf->resv);
        /* 把已有映射作废；不要在这里重新 map */
}

static const struct dma_buf_attach_ops my_attach_ops = {
        .allow_peer2peer = false,
        .move_notify     = my_move_notify,   /* ⚠️ 必填，见下 */
};

attach = dma_buf_dynamic_attach(dmabuf, dev, &my_attach_ops, m);
```

⚠️⚠️ **`move_notify` 是动态 attach 的必填项**：
`umem_dmabuf.c:126` 有 `if (unlikely(!ops || !ops->move_notify))` 的检查，
核心侧也依赖它存在来决定走动态路径（03 §A3.1）。

⚠️ **不想支持迁移但又要用动态接口**时的标准做法（`umem_dmabuf.c:181-184`）：
提供一个"不支持"的桩 `ib_umem_dmabuf_unsupported_move_notify`（`:173`），
放进 `ib_umem_dmabuf_attach_pinned_ops`（`:181`），然后配合 `dma_buf_pin()`
把 buffer 钉住 ⇒ **迁移永远不会发生，桩永远不会被调**。

⚠️ 动态路径下每次用之前都要在持锁状态下重新 map：

```c
dma_resv_lock(dmabuf->resv, NULL);              /* umem_dmabuf.c:199/:225 的做法 */
sgt = dma_buf_map_attachment(attach, DMA_BIDIRECTIONAL);   /* 持锁版！:29 */
/* ... 提交工作，把 fence 加到 resv 上 ... */
dma_resv_unlock(dmabuf->resv);
```

⚠️⚠️ **持锁时必须调持锁版，不持锁时必须调 `_unlocked` 版**。
反了的两种后果不对称（01 §2.2）：
**持锁时调 `_unlocked` = 自死锁；不持锁时调持锁版 = lockdep WARN 但能跑**。

---

## §3 写 fence 生产方

### §3.1 最小骨架

```c
struct my_fence {
        struct dma_fence base;
        spinlock_t       lock;      /* ⚠️ 见 §3.2 */
};

static const char *my_get_driver_name(struct dma_fence *f) { return "my-driver"; }
static const char *my_get_timeline_name(struct dma_fence *f) { return "my-ring0"; }

static const struct dma_fence_ops my_fence_ops = {
        .get_driver_name   = my_get_driver_name,     /* 必填 */
        .get_timeline_name = my_get_timeline_name,   /* 必填 */
        /* .enable_signaling 不填 ⇒ 见 §3.3 */
        /* .wait 不填 ⇒ 核心用 dma_fence_default_wait（dma-fence.c:761） */
        /* .release 不填 ⇒ 核心用 dma_fence_free（dma-fence.c:573） */
};

/* 驱动初始化时申请一次 */
static u64 my_context;
my_context = dma_fence_context_alloc(1);     /* dma-fence.c:187 */

/* 每个 fence */
static struct my_fence *my_fence_create(u64 seqno)
{
        struct my_fence *f = kzalloc(sizeof(*f), GFP_KERNEL);

        if (!f) return NULL;
        spin_lock_init(&f->lock);
        dma_fence_init(&f->base, &my_fence_ops, &f->lock, my_context, seqno);
        return f;                            /* 引用计数已是 1（dma-fence.c:948） */
}

/* 硬件完成中断里 */
static void my_irq_handler(struct my_fence *f)
{
        dma_fence_signal(&f->base);          /* dma-fence.c:461 */
        dma_fence_put(&f->base);             /* 交出创建时那一份引用 */
}
```

⚠️ **`get_driver_name` / `get_timeline_name` 两个是硬必填**：`dma_fence_describe()`
（`dma-fence.c:923`）和所有 debugfs 打印都会无条件调它们，返回 NULL 直接空指针。

⚠️ **`dma_fence_context_alloc()` 一个驱动只调一次（每条时间线一次），不要每个
fence 调一次**：核心用 `(context, seqno)` 二元组判断"同一条时间线上谁更新"
（`dma-resv.c:294-305` 的替换优化就靠它）。每个 fence 一个 context ⇒ 优化永远不生效。

⚠️ `dma_fence_context_counter` 从 1 开始（`dma-fence.c:36` 的
`ATOMIC64_INIT(1)`）⇒ **context 0 是保留值**，可以当"未初始化"哨兵用。

### §3.2 seqno 单调 + 锁必须活得比 fence 长

⚠️⚠️ **传给 `dma_fence_init()` 的 `spinlock_t *` 必须在 fence 的整个生命期内有效**。
最常见的 bug：锁放在"命令提交结构"里，提交结构先释放，fence 还活着 ⇒ UAF。
**要么把锁放进 fence 自己（上面的骨架），要么放进驱动级的长生命期对象。**

⚠️ 同一条时间线上 seqno 必须单调递增，且**必须按序 signal**。`dma_fence_signal()`
不检查顺序；乱序 signal 会让上面那个替换优化把"新"的 fence 当成旧的丢掉。

### §3.3 `enable_signaling` 的两种返回值都要看懂

不实现它 ⇒ 核心认为"这个 fence 永远靠硬件中断推进"，正常。实现它时：

| 返回 | 含义（`dma-fence.c:579` 的 `__dma_fence_enable_signaling`） |
|---|---|
| `true` | 已开启信号通路，等着中断来 signal |
| `false` | ⚠️ **意思是"这个 fence 其实已经完成了"** ⇒ 核心立刻替你 signal |

⚠️ `false` **不是"失败"** ——03 §B3 详解。想表达失败的话没有通道，只能返回 `true`
然后靠超时兜底。

⚠️ 反例：`sw_sync.c:155` 的 `timeline_fence_enable_signaling()` **永远返回 `true`**，
因为它的推进完全靠用户态写 debugfs。

### §3.4 ⚠️⚠️ 有界时间：fence 必须在可预期的时间内完成

这是整个框架最硬的契约（08 篇是它的第一性原理推导）。实践上三条：

1. **不要等用户态**。等用户态 = 不设上限 = 死锁全系统。`CONFIG_SW_SYNC` 的
   help 文本自己承认这一点（`drivers/dma-buf/Kconfig:20` 那一项）。
2. **signal 路径上不能分配内存**。`dma_resv_lockdep()`（`dma-resv.c:742-780`）
   把这条锁序在启动时排练一遍：`mmap_lock` → `dma_resv` → `fs_reclaim` → 等 fence
   ⇒ **在等 fence 的路径上再申请内存就是回边**（03 §C5）。
3. **一定要有超时兜底**。硬件挂了没人 signal 时，驱动的 timeout 处理要
   `dma_fence_set_error(f, -ETIME)` 然后 `dma_fence_signal(f)` ——
   **设置错误码必须在 signal 之前**（`dma_fence_set_error` 里有
   `WARN_ON` 检查 SIGNALED 位）。

⚠️ 想给 signal 临界区加 lockdep 保护，用这一对（03 §B6）：

```c
bool cookie = dma_fence_begin_signalling();   /* dma-fence.c:302 */
/* ... 这里不允许做任何可能等 fence 或申请内存的事 ... */
dma_fence_end_signalling(cookie);             /* dma-fence.c:325 */
```

### §3.5 等 fence 的正确写法

```c
long ret = dma_fence_wait_timeout(f, true /* intr */, timeout);

if (ret < 0)         return ret;      /* -ERESTARTSYS 等，要往上传 */
if (ret == 0)        return -ETIME;   /* 超时 */
/* ret > 0 ⇒ 完成，剩余 jiffies 在 ret 里 */
if (f->error)        return f->error; /* ⚠️ 完成 ≠ 成功 */
```

⚠️⚠️ **三个独立的坑**：
（a）返回值 `> 0` 是成功，不是 `== 0`（`dma-fence.c:501`）；
（b）**fence 完成了也可能是失败完成的**，必须再查 `dma_fence_get_status()`
（`dma-fence.c:685`）或 `f->error`；
（c）`intr=true` 时 `-ERESTARTSYS` 要一路传到系统调用层，
**不能吞掉当成"再等一次"**（03 §A5.1）。

### §3.6 把 fence 挂到缓冲区上（dma-resv）

```c
/* 第 1 步：先预留槽位 —— 这一步会分配内存，所以必须在拿锁的早期做 */
dma_resv_lock(dmabuf->resv, NULL);
ret = dma_resv_reserve_fences(dmabuf->resv, 1);   /* dma-resv.c:177 */
if (ret) goto unlock;

/* 第 2 步：提交硬件工作，拿到 fence */

/* 第 3 步：挂上去 —— 这一步不会失败 */
dma_resv_add_fence(dmabuf->resv, fence, DMA_RESV_USAGE_WRITE);  /* dma-resv.c:275 */
dma_resv_unlock(dmabuf->resv);
```

⚠️⚠️ **漏掉第 1 步 = 内核 panic，不是返回错误**：`dma_resv_add_fence()` 里是
`BUG_ON(fobj->num_fences >= fobj->max_fences)`（`dma-resv.c:307`）。
详见 03 §C2。

⚠️ **`usage` 参数选错是最隐蔽的一类 bug**（02 §4 有完整语义表）：

| 你在做什么 | 用 |
|---|---|
| 往缓冲区里写 | `DMA_RESV_USAGE_WRITE` |
| 只读缓冲区 | `DMA_RESV_USAGE_READ` |
| 只是 bookkeeping（不影响数据） | `DMA_RESV_USAGE_BOOKKEEP` |
| 内核态自己的搬移 | `DMA_RESV_USAGE_KERNEL` |

⚠️ 写成 `READ` 而实际在写 ⇒ **别人的读操作不会等你，直接读到半成品**。

⚠️ 等所有人用完再回收内存：

```c
long r = dma_resv_wait_timeout(dmabuf->resv, DMA_RESV_USAGE_READ,
                               true, MAX_SCHEDULE_TIMEOUT);   /* dma-resv.c:666 */
```

⚠️ `DMA_RESV_USAGE_READ` 在这里表示"等 READ 及更强的全部"——
**usage 是有序的，等级越低等的越多**（02 §4）。

---

## §4 用户态怎么用

### §4.1 拿到一个 dma-buf fd

三条来路，都要 `O_CLOEXEC`：

```c
/* (a) 从 dma-heap 分配 */
int heap = open("/dev/dma_heap/system", O_RDONLY | O_CLOEXEC);
struct dma_heap_allocation_data d = {
        .len       = size,
        .fd        = 0,          /* ⚠️ 必须传 0，见下 */
        .fd_flags  = O_RDWR | O_CLOEXEC,
        .heap_flags = 0,
};
ioctl(heap, DMA_HEAP_IOCTL_ALLOC, &d);   /* include/uapi/linux/dma-heap.h:50 */
int dmabuf_fd = d.fd;

/* (b) 从 memfd 包装（udmabuf） */
/* (c) 驱动自己的 ioctl（DRM PRIME / V4L2 EXPBUF …） */
```

⚠️⚠️ **`.fd` 必须是 0**：`dma_heap_ioctl_allocate()` 在 `:102-103` 检查
`if (heap_allocation->fd) return -EINVAL`
⇒ **同一个结构体复用第二次会失败**，因为第一次内核把 fd 写回去了（`:117`）。
每次调用前重新清零。

⚠️ `fd_flags` 和 `heap_flags` 各有掩码校验（`:105-109`），传未定义位 ⇒ `-EINVAL`。
**这是有意的前向兼容检查：老内核会拒绝新标志，而不是忽略。**

⚠️ heap 名字就是 uapi：`/dev/dma_heap/system` 恒存在（`CONFIG_DMABUF_HEAPS_SYSTEM`），
`/dev/dma_heap/<CMA 区名>` 名字取自设备树（04 §2.2、06 篇）
⇒ **不要在用户态硬编码 CMA heap 的名字，要遍历目录**。

### §4.2 CPU 访问：mmap 前后必须 SYNC

```c
void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, dmabuf_fd, 0);

struct dma_buf_sync s = { .flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_RW };
ioctl(dmabuf_fd, DMA_BUF_IOCTL_SYNC, &s);   /* include/uapi/linux/dma-buf.h:171 */

/* ... 读写 p ... */

s.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_RW;
ioctl(dmabuf_fd, DMA_BUF_IOCTL_SYNC, &s);
```

⚠️⚠️ **arm64 上不做 SYNC 就是数据损坏**，而且是间歇性的、跟 cache 行为相关的、
在 x86 上复现不出来的那种。

⚠️ **`MAP_SHARED` 是强制的**：`dma_buf_mmap_internal()` 会检查
（udmabuf 的 `mmap_udmabuf()` 在 `:52-53` 也独立检查一遍
`VM_SHARED|VM_MAYSHARE`，否则 `-EINVAL`）。`MAP_PRIVATE` 一律失败。

⚠️ **`offset` 必须是 0**：核心的 `mmap` 路径要求整体映射（04 §2.3 里两个 heap
的 `mmap` 实现都只处理从 0 开始的整块）。

⚠️⚠️ **导出方没实现 `begin/end_cpu_access` 时，`DMA_BUF_IOCTL_SYNC` 返回 0
而什么都不做**（03 §A5.1）⇒ **用户态无法区分"同步成功"和"这个导出方不支持同步"**。
唯一的防御是只用已知实现了它的导出方（两个 heap 都实现了）。

### §4.3 给缓冲区起名字，便于排查

```c
ioctl(dmabuf_fd, DMA_BUF_SET_NAME, "camera-frame-0");
```

⚠️ 这个号有**三个别名**（`include/uapi/linux/dma-buf.h:176-178`）：
`DMA_BUF_SET_NAME` / `_A`（`char *`）/ `_B`（`u64`）——**同一个 ioctl 号**，
只是为了兼容 32/64 位下 `const char *` 的宽度差异。**用 `_A` 最稳**。

设了名字之后，三个地方能看到它：
`/proc/<pid>/fd/N` 的链接名（03 §A1）· debugfs 的 `bufinfo` · sysfs（若开启）。

### §4.4 跨进程传 fd

dma-buf fd 是普通 fd ⇒ **`SCM_RIGHTS` 传，或者 fork 继承**。
⚠️ **`O_CLOEXEC` 一定要带**：忘了带的话，任何 `exec` 的子进程都白拿一份缓冲区引用，
缓冲区永远不释放。内核侧的对应约定是 `dma_buf_fd(dmabuf, flags & O_CLOEXEC)`
（`udmabuf.c:283-286` 是标准写法）。

### §4.5 udmabuf：把 memfd 变成 dma-buf

```c
int memfd = memfd_create("buf", MFD_CLOEXEC | MFD_ALLOW_SEALING);
ftruncate(memfd, size);
fcntl(memfd, F_ADD_SEALS, F_SEAL_SHRINK);     /* ⚠️ 必须 */

int udma = open("/dev/udmabuf", O_RDWR | O_CLOEXEC);
struct udmabuf_create c = { .memfd = memfd, .flags = UDMABUF_FLAGS_CLOEXEC,
                            .offset = 0, .size = size };
int dmabuf_fd = ioctl(udma, UDMABUF_CREATE, &c);  /* include/uapi/linux/udmabuf.h:30 */
```

⚠️⚠️ 三条硬门槛（04 §3）：
（a）**必须加 `F_SEAL_SHRINK`**，否则 `-EINVAL`（`udmabuf.c:222-223`）；
（b）**不能有 `F_SEAL_WRITE` / `F_SEAL_FUTURE_WRITE`**，否则同样 `-EINVAL`；
（c）**`offset` 和 `size` 必须页对齐**（`udmabuf.c:189-197`）。

⚠️ **默认上限只有 64 MB**（`udmabuf.c:20` 的 `size_limit_mb = 64`），
`_LIST` 版的段数上限 1024（`:16` 的 `list_limit`）。两个都是 0644 的 module param
⇒ 可以在 `/sys/module/udmabuf/parameters/` 里改，**但改大要先想清楚**：
udmabuf 为每一页存一个 `struct page *`，64 MB / 4K = 16384 个指针 = 128 KB 内核内存。

⚠️⚠️ **udmabuf 无条件宣称自己能寻址 64 位**
（`udmabuf.c:381-382` 的 `dma_coerce_mask_and_coherent(…, DMA_BIT_MASK(64))`）
⇒ **在 DMA 地址受限的 arm64 SoC 上，把 udmabuf 交给这类设备会静默损坏数据**。

### §4.6 fence 的用户态一面：sync_file

```c
/* 从 dma-buf 上的 fence 导出一个 sync_file fd */
struct dma_buf_export_sync_file e = { .flags = DMA_BUF_SYNC_READ };
ioctl(dmabuf_fd, DMA_BUF_IOCTL_EXPORT_SYNC_FILE, &e);
int fence_fd = e.fd;

/* 等它：sync_file 支持 poll */
struct pollfd pfd = { .fd = fence_fd, .events = POLLIN };
poll(&pfd, 1, timeout_ms);

/* 合并两个 */
struct sync_merge_data m = { .name = "merged", .fd2 = fence_fd2 };
ioctl(fence_fd, SYNC_IOC_MERGE, &m);   /* include/uapi/linux/sync_file.h:86 */
```

⚠️⚠️ **`EXPORT_SYNC_FILE` / `IMPORT_SYNC_FILE` 两个 ioctl 被
`#if IS_ENABLED(CONFIG_SYNC_FILE)` 包着**（`dma-buf.c:483-488`）
⇒ **`CONFIG_SYNC_FILE=n` 时它们返回 `-ENOTTY`**，而 `CONFIG_SYNC_FILE`
的默认值是 `n`（04 §4）。**用户态必须处理 `-ENOTTY` 而不是当致命错误。**

⚠️ `SYNC_IOC_FILE_INFO`（`include/uapi/linux/sync_file.h:96`）是**两趟协议**：
第一趟 `num_fences = 0` 问个数，分配好数组，第二趟才填（04 §4）。

⚠️ ⚠️ **`/dev/sw_sync` 不存在**：sw_sync 的入口是 debugfs
文件 `/sys/kernel/debug/sync/sw_sync`（`sync_debug.c:176-186`），
而且 **`CONFIG_SW_SYNC` 只该在测试内核上开**（04 §5.4：它把 fence
的完成权交给了用户态，直接违反 §3.4 的有界时间契约）。
**生产代码不要依赖它。**

---

## §5 通用陷阱清单

按"踩到的代价"从高到低排：

| # | 陷阱 | 后果 | 出处 |
|---|---|---|---|
| 1 | `dma_resv_add_fence()` 前忘了 `dma_resv_reserve_fences()` | ⚠️⚠️ **内核 panic**（`BUG_ON`） | `dma-resv.c:307` |
| 2 | 持锁时调了 `_unlocked` 版接口 | ⚠️⚠️ 自死锁 | 01 §2.2 |
| 3 | 传给 `dma_fence_init()` 的锁生命期比 fence 短 | ⚠️⚠️ UAF | §3.2 |
| 4 | 在等 fence / signal fence 的路径上分配内存 | ⚠️⚠️ 死锁（lockdep 会在启动时就警告） | `dma-resv.c:742-780` |
| 5 | 忘了 `MODULE_IMPORT_NS(DMA_BUF)` | 模块拒绝加载 | §0 |
| 6 | 导出方没实现 `begin/end_cpu_access` | arm64 上间歇数据损坏，且用户态无从察觉 | 03 §A5.1 |
| 7 | 从 sg 表里读 `sg_page()` | 只在 `CONFIG_DMABUF_DEBUG=y` 时崩 | `dma-buf.c:763` |
| 8 | `dma_fence_wait_timeout()` 按 `== 0` 判成功 | 逻辑反了 | `dma-fence.c:501` |
| 9 | fence 完成后不查 `error` | 把失败当成功 | `dma-fence.c:685` |
| 10 | 吞掉 `-ERESTARTSYS`/`-EINTR` | 进程杀不掉 / 信号丢失 | 03 §A5.1 |
| 11 | `usage` 写成 `READ` 而实际在写 | 别人读到半成品 | 02 §4 |
| 12 | 每个 fence 单独 `dma_fence_context_alloc()` | resv 替换优化永久失效，fence 数量线性膨胀 | `dma-resv.c:294-305` |
| 13 | 复用 `dma_heap_allocation_data` 不清零 `.fd` | 第二次 `-EINVAL` | `dma-heap.c:102-103` |
| 14 | `dma_buf_fd()` 不传 `O_CLOEXEC` | 子进程白持引用，缓冲区不释放 | `udmabuf.c:283-286` |
| 15 | 大缓冲区分配循环里不查 `fatal_signal_pending()` | 不可杀进程 | `system_heap.c:367-370` |
| 16 | `exp_info.exp_name` 留空 | `CONFIG_DMABUF_SYSFS_STATS=y` 时导出直接失败 | §1.2 |
| 17 | 动态 attach 不提供 `move_notify` | 被拒 | `umem_dmabuf.c:126` |
| 18 | 依赖 `EXPORT_SYNC_FILE` 存在 | `CONFIG_SYNC_FILE=n` 时 `-ENOTTY` | `dma-buf.c:483-488` |

⚠️ 其中 **#7 / #18 / #6 是"配置相关"** ——同一份代码在两台机器上表现不同。
这类问题在 CI 上必须开着 `CONFIG_DMABUF_DEBUG` 跑一遍（07 篇）。

---

## §6 配置检查清单

写完代码之后，按这张表逐项确认你依赖的东西真的在（04 §7 有"该不该开"的判断）：

| 你依赖 | 需要的 Kconfig | 6.1 默认 | 不开的后果 |
|---|---|---|---|
| 任何 `dma_buf_*` | ⚠️ **无需任何选项** | 恒编入 | ——（六个核心 `.c` 是 `obj-y`，04 §0） |
| `/dev/dma_heap/system` | `DMABUF_HEAPS` + `DMABUF_HEAPS_SYSTEM` | n | 目录不存在 |
| `/dev/dma_heap/<cma>` | `DMABUF_HEAPS_CMA` + `CMA` + DT 里有 CMA 区 | n | 同上（06 篇） |
| `/dev/udmabuf` | `UDMABUF` | n | 节点不存在 |
| `EXPORT/IMPORT_SYNC_FILE`、`SYNC_IOC_*` | `SYNC_FILE` | n | `-ENOTTY` |
| `move_notify` 真的被调 | `DMABUF_MOVE_NOTIFY` | n（EXPERIMENTAL） | 动态 attach 退化成静态 pin |
| sg 表误用能被抓 | `DMABUF_DEBUG` | n | bug 潜伏到调试内核上才炸 |
| `/sys/kernel/dmabuf/buffers/` | `DMABUF_SYSFS_STATS` | n（DEPRECATED） | 目录不存在 |
| `/sys/kernel/debug/sync/` | `SW_SYNC`（连带 `SYNC_FILE`+`DEBUG_FS`） | n | ⚠️ 生产内核本来就不该开 |
| 锁序被校验 | `PROVE_LOCKING` | n | `dma_resv_lockdep()` 不跑 |

⚠️⚠️ **`DMABUF_HEAPS_*` 只有 bool，没有 tristate** ⇒ **heap 只能编进内核，不能做模块**
（04 §1）。想在模块里加自己的 heap 也不行：`dma-heap.c` 一个符号都没导出
（`dma_heap_add()` 不是 `EXPORT_SYMBOL`）⇒ **out-of-tree heap 在 6.1 里做不到**。

⚠️ **`CONFIG_DMABUF_MOVE_NOTIFY=n` 是有安全含义的默认值**：Kconfig 的 help 自陈
不 pin 才能修掉"用户态可以 pin 住无限内存"的问题（`drivers/dma-buf/Kconfig:42` 那一项）
⇒ **默认配置下，任何能拿到 dma-buf fd 的进程都能钉住内存**。这一条要写进威胁模型。

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 这些函数的完整签名和返回值 | 01-api.md |
| `usage` 的完整语义、结构体每个字段 | 02-datastructures.md |
| 为什么必须这样写（锁序、状态机） | 03-internals.md |
| 两个 heap / udmabuf / sw_sync 的实现细节 | 04-extensions.md |
| CMA heap 的内存从设备树哪来 | 06-devicetree.md |
| 出问题了怎么查（debugfs / tracepoint） | 07-debug.md |
| "有界时间"到底是怎么推出来的 | 08-feynman.md |
