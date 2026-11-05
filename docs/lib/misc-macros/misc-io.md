# scatterlist 与 iov_iter

> B 级文档 · scatterlist：`include/linux/scatterlist.h`（635 行）+ `lib/scatterlist.c`（1087 行）· iov_iter：`include/linux/uio.h`（357 行）+ `lib/iov_iter.c`（1894 行）

## 1 scatterlist — DMA 散射-聚集列表

### 定位

**功能**：把"一组不连续的内存区间"描述成一个链表，供 DMA 映射层转换成硬件能理解的总线地址列表。解决硬件 DMA 引擎需要连续物理内存、但调用者数据往往分散在多个页面中的矛盾。

**实现方式**：核心结构 `struct scatterlist` 每项描述一段内存（page + offset + length），通过 `page_link` 低两位复用实现链式表（`SG_CHAIN` 指向下一个 sg 数组，`SG_END` 标记末尾）。`struct sg_table` 封装链表头 + 原始条目数/DMA 映射后条目数。DMA 映射前用 `sg_set_page` 填充，映射后用 `sg_dma_address`/`sg_dma_len` 读取总线地址。

**应用场景**：所有需要 DMA 传输大块数据的驱动：块设备（bio → sg 映射）、SCSI 子系统、网络设备（大帧分片）、多媒体驱动（视频帧 buffer）、USB 大容量存储等。`sg_alloc_table`/`sg_free_table` 是标准的分配/释放配对。

### 核心数据结构

头文件：`include/linux/scatterlist.h`

```c
struct scatterlist {                    /* :11 */
    unsigned long   page_link;          /* 页面指针 + 低 2 位标志 */
    unsigned int    offset;             /* 页内偏移 */
    unsigned int    length;             /* 本段长度 */
    dma_addr_t      dma_address;        /* DMA 映射后的总线地址 */
#ifdef CONFIG_NEED_SG_DMA_LENGTH
    unsigned int    dma_length;         /* DMA 映射后的长度 */
#endif
};
```

**page_link 低两位复用**：
- bit 0 = `SG_CHAIN`（0x01）：page_link 指向下一个 scatterlist 数组（链式表）
- bit 1 = `SG_END`（0x02）：本条目是链表最后一个

```c
struct sg_table {                       /* :39 */
    struct scatterlist *sgl;            /* 链表头 */
    unsigned int nents;                 /* DMA 映射后的条目数 */
    unsigned int orig_nents;            /* 原始条目数（映射前） */
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `sg_init_table(sg, nents)` | 初始化 sg 数组，清零并标记末尾 | 使用前必须调用 |
| `sg_set_page(sg, page, len, offset)` | 设置 sg 条目指向指定页面 | 最常用设置函数 |
| `sg_set_buf(sg, buf, buflen)` | 设置 sg 指向虚拟地址 buf | 内部调用 `virt_to_page` |
| `sg_page(sg)` | 取回 sg 条目的 page 指针 | 清除低 2 位标志 |
| `sg_next(sg)` | 返回下一个 sg 条目 | 处理链式跳转；末尾返回 NULL |
| `sg_mark_end(sg)` | 标记 sg 链表结束 | `sg_next` 到此为止返回 NULL |
| `sg_chain(prv, prv_nents, sgl)` | 把两个 sg 链表链接起来 | prv 最后一个条目变成链指针 |
| `for_each_sg(sglist, sg, nr, i)` | 遍历 sg 链表 | 最常用遍历宏 |
| `for_each_sgtable_sg(sgt, sg, i)` | 遍历 sg_table 的原始条目 | 用 `orig_nents` |
| `for_each_sgtable_dma_sg(sgt, sg, i)` | 遍历 DMA 映射后的条目 | 用 `nents` |
| `sg_dma_address(sg)` | 取 DMA 总线地址 | `dma_map_sg` 后使用 |
| `sg_dma_len(sg)` | 取 DMA 映射长度 | 依赖 `CONFIG_NEED_SG_DMA_LENGTH` |
| `sg_alloc_table(table, nents, gfp)` | 分配 sg_table | `lib/scatterlist.c` |
| `sg_free_table(table)` | 释放 sg_table | 与 `sg_alloc_table` 配对 |

### 使用流程

```
调用者填充 sg 数组
    → sg_init_table() → sg_set_page() × N → sg_mark_end()
DMA 映射
    → dma_map_sg(dev, sgl, nents, dir) → 返回实际映射条目数 nents_dma
使用 DMA 地址
    → for_each_sgtable_dma_sg() 遍历，取 sg_dma_address/sg_dma_len
DMA 解映射
    → dma_unmap_sg(dev, sgl, orig_nents, dir)
```

---

## 2 iov_iter — 多态 I/O 迭代器

### 定位

**功能**：用单一结构体统一描述"数据从哪来、到哪去"的多态 I/O 迭代器。VFS 层需要一种通用方式处理来自不同来源的数据传输——用户空间 iovec、内核 kvec、块层 bio_vec、pipe 缓冲区、页缓存 xarray 等——`iov_iter` 通过类型分派把这些全部统一起来。

**实现方式**：`struct iov_iter` 以 `iter_type` 枚举区分底层数据源类型，内部用 union 存放不同类型的数据指针（iovec/kvec/bvec/pipe/xarray/ubuf）。所有操作（`copy_to_iter`/`copy_from_iter` 等）根据 `iter_type` 内部走不同路径，调用者不需要知道底层是哪种数据源。`ITER_UBUF` 是单段用户 buffer 的快速路径，避免 iovec 数组分配——这是网络 I/O 最常见的情形。

**应用场景**：VFS 读写路径（`vfs_read`/`vfs_write`）、网络协议栈（`sendmsg`/`recvmsg`）、块层 direct I/O、splice 系统调用、文件系统页缓存操作等。几乎所有涉及内核与用户空间之间数据传输的路径都经过 `iov_iter`。

### 核心数据结构

头文件：`include/linux/uio.h`

```c
enum iter_type {                        /* :21 */
    ITER_IOVEC,     /* 用户空间 iovec 数组 */
    ITER_KVEC,      /* 内核 kvec 数组 */
    ITER_BVEC,      /* 块层 bio_vec 数组 */
    ITER_PIPE,      /* pipe 缓冲区 */
    ITER_XARRAY,    /* 页缓存 xarray */
    ITER_DISCARD,   /* 丢弃（黑洞/零源） */
    ITER_UBUF,      /* 单个用户空间 buffer（快速路径） */
};

struct iov_iter {                       /* :41 */
    u8 iter_type;                       /* 上面枚举之一 */
    bool nofault;                       /* 禁止 page fault */
    bool data_source;                   /* 1=WRITE(从 iter 读), 0=READ(往 iter 写) */
    bool user_backed;                   /* 是否有用户空间 backing */
    union {
        size_t iov_offset;              /* 当前段内偏移 */
        int last_offset;                /* pipe 用 */
    };
    size_t count;                       /* 剩余总字节数 */
    union {
        const struct iovec *iov;        /* ITER_IOVEC / ITER_UBUF */
        const struct kvec *kvec;        /* ITER_KVEC */
        const struct bio_vec *bvec;     /* ITER_BVEC */
        struct xarray *xarray;          /* ITER_XARRAY */
        struct pipe_inode_info *pipe;   /* ITER_PIPE */
        void __user *ubuf;              /* ITER_UBUF */
    };
    union {
        unsigned long nr_segs;          /* 段数 */
        struct { unsigned int head, start_head; };  /* pipe 用 */
        loff_t xarray_start;            /* xarray 起始偏移 */
    };
};
```

### 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `iov_iter_init(i, rw, iov, nr_segs, count)` | 初始化为用户空间 iovec | 文件 I/O 路径 |
| `iov_iter_kvec(i, rw, kvec, nr_segs, count)` | 初始化为内核 kvec | 内核内部数据传输 |
| `iov_iter_bvec(i, rw, bvec, nr_segs, count)` | 初始化为 bio_vec | 块层 / direct I/O |
| `iov_iter_pipe(i, rw, pipe, count)` | 初始化为 pipe | splice 路径 |
| `iov_iter_xarray(i, rw, xarray, start, count)` | 初始化为 xarray | 页缓存路径 |
| `iov_iter_ubuf(i, rw, buf, count)` | 初始化为单个用户 buffer | 快速路径，单段 |
| `copy_to_iter(addr, bytes, i)` | 从内核地址拷贝到 iter | `__must_check`，返回实际拷贝字节数 |
| `copy_from_iter(addr, bytes, i)` | 从 iter 拷贝到内核地址 | 同上 |
| `copy_from_iter_full(addr, bytes, i)` | 全有或全无版本 | 失败时自动 `iov_iter_revert` |
| `copy_page_to_iter(page, offset, bytes, i)` | 拷贝一个页面到 iter | 块层常用 |
| `copy_page_from_iter(page, offset, bytes, i)` | 从 iter 拷贝到一个页面 | 同上 |
| `iov_iter_advance(i, bytes)` | 前进迭代器 | 跳过已处理的数据 |
| `iov_iter_revert(i, bytes)` | 回退迭代器 | 错误恢复用 |
| `iov_iter_truncate(i, count)` | 截断 iter 的 count | 限制处理量 |
| `iov_iter_count(i)` | 取剩余字节数 | `i->count` 的 inline 包装 |
| `iov_iter_get_pages2(i, pages, maxsize, maxpages, start)` | 钉住用户页面 | 返回钉住的字节数或负错误码 |
| `iov_iter_npages(i, maxpages)` | 计算 iter 覆盖的页面数 | 用于预分配 page 数组 |
| `import_iovec(type, uvec, nr_segs, ...)` | 从用户空间导入 iovec 并初始化 iter | readv/writev 系统调用入口 |

### 设计要点

- **多态分派**：所有操作根据 `iter_type` 内部走不同路径，调用者不需要知道底层是 iovec 还是 bvec
- **ITER_UBUF 快速路径**：单段用户 buffer 是网络 I/O 最常见的情形，单独一种类型避免 iovec 数组分配
- **`__must_check`**：`copy_to_iter` / `copy_from_iter` 返回实际拷贝量，调用者必须检查是否等于请求量
- **`nofault` 标志**：设置后不会触发用户空间 page fault（用于中断上下文等不能阻塞的场景）
