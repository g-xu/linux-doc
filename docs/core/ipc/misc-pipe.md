# 管道（pipe）

> B 级文档 · `fs/pipe.c`（1,515 行）· 头文件 `include/linux/pipe_fs_i.h`（274 行）

## 1 定位

**功能**：管道是内核最基本的单向字节流 IPC 通道。shell 的 `cmd1 | cmd2` 语法、进程间数据传输都依赖管道。管道支持标准 read/write 语义，也支持 splice 零拷贝数据传输。

**实现方式**：核心数据结构 `struct pipe_inode_info` 管理一个环形页面缓冲区（`struct pipe_buffer` 数组），用 `head`/`tail` 索引配合 `ring_size`（2 的幂）实现无锁环形队列判断。写入端分配页面并从用户 `iov_iter` 拷贝数据，读取端从 `tail` 向 `head` 遍历拷贝到用户空间。splice 零拷贝通过 `pipe_buf_operations` 的 `try_steal()` 直接转移页面所有权，避免数据拷贝。读写分别阻塞在 `wr_wait` 和 `rd_wait` 等待队列上。

**应用场景**：`fs/pipe.c` 中的 `create_pipe_files()` 被 `fs/coredump.c`（core dump 写入管道）和 `kernel/usermode_driver.c`（用户态驱动通信）调用。`pipe_buf_operations` 被 `fs/splice.c`（splice 系统调用）、`kernel/trace/trace.c`（ftrace 跟踪环形缓冲区）、`net/smc/smc_rx.c`（SMC 网络接收）和 `kernel/watch_queue.c`（watch_queue 通知）等模块实现和消费。全树共 6 个 C 文件直接引用 `pipe_buf_operations`。

## 2 核心数据结构

头文件：`include/linux/pipe_fs_i.h`

```c
struct pipe_buffer {                  /* :26 */
    struct page *page;                /* 数据所在页面 */
    unsigned int offset, len;         /* 页内偏移和长度 */
    const struct pipe_buf_operations *ops;
    unsigned int flags;               /* PIPE_BUF_FLAG_* */
    unsigned long private;
};

struct pipe_inode_info {              /* :58 */
    struct mutex mutex;
    wait_queue_head_t rd_wait, wr_wait;  /* 读/写等待队列 */
    unsigned int head;                   /* 生产点 */
    unsigned int tail;                   /* 消费点 */
    unsigned int max_usage;              /* 可用槽位数 */
    unsigned int ring_size;              /* 总槽位数（2 的幂） */
    unsigned int readers, writers, files; /* 引用计数 */
    struct pipe_buffer *bufs;            /* 环形缓冲区数组 */
    struct user_struct *user;
};
```

`struct pipe_buf_operations`（`:95`）：`confirm()`、`release()`、`try_steal()`、`get()` —— 控制缓冲区页面生命周期。

## 3 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `create_pipe_files(res, flags)` | 创建管道文件对 | `fs/pipe.c:916` |
| `alloc_pipe_info()` | 分配 pipe_inode_info | |
| `free_pipe_info(pipe)` | 释放管道 | |
| `pipe_resize_ring(pipe, nr_slots)` | 调整环形缓冲区大小 | |
| `get_pipe_info(file, for_splice)` | 获取管道的 pipe_inode_info | splice 路径使用 |

## 4 读写流程

- **`pipe_read`**（`fs/pipe.c:231`）：加锁，从 `tail` 向 `head` 遍历 `pipe_buffer`，拷贝数据到用户 `iov_iter`。管道空时阻塞在 `rd_wait`（除非 O_NONBLOCK）
- **`pipe_write`**（`fs/pipe.c:416`）：加锁，分配页面，从用户 `iov_iter` 拷贝数据到管道页面。管道满时阻塞在 `wr_wait`

## 5 设计要点

- **环形缓冲区**：`head`/`tail` 索引配合 `ring_size`（2 的幂），用 `pipe_empty()`/`pipe_full()` 判断状态
- **页面窃取**：splice 通过 `try_steal()` 直接转移页面所有权，零拷贝
- **引用计数**：`readers`/`writers`/`files` 三个计数器控制生命周期——最后一方关闭时清理
- **watch_queue**：`pipe_inode_info` 可关联 `watch_queue`（`CONFIG_WATCH_QUEUE`），此时拒绝 splice 访问

共 38 个文件引用管道子系统。
