# Relay — 高速 per-CPU 日志通道

> B 级文档 · `kernel/relay.c`（1,247 行）· 头文件 `include/linux/relay.h`（287 行）

## 1 定位

**功能**：Relay 提供高速 per-CPU 环形缓冲区通道，用于将内核数据（主要是跟踪和调试数据）高效传输到用户空间。写入端是中断安全的无锁操作，读取端通过 debugfs 文件暴露给用户空间，支持 read/mmap/splice 三种读取方式。

**实现方式**：核心结构 `struct rchan` 为每个在线 CPU 创建独立的 `struct rchan_buf`，每个缓冲区由多个子缓冲区（sub-buffer）组成。写入时通过 `local_irq_save()` 保护当前 CPU 的缓冲区，当 `offset + length > subbuf_size` 时自动切换到下一个子缓冲区。读取通过 `irq_work` 将唤醒从原子上下文延迟到进程上下文，避免在中断处理中睡眠。客户端通过 `struct rchan_callbacks` 回调参与缓冲区生命周期管理，其中 `create_buf_file()` 和 `remove_buf_file()` 为必须实现的回调。

**应用场景**：主要消费者是跟踪和调试子系统。无线驱动中 `drivers/net/wireless/ath/ath9k/common-spectral.c`、`ath10k/spectral.c`、`ath11k/spectral.c` 和 `drivers/net/wireless/mediatek/mt76/mt7915/debugfs.c` 用 relay 暴露频谱调试数据。`kernel/trace/blktrace.c` 用 relay 传输块 I/O 跟踪数据。`drivers/gpu/drm/i915/gt/uc/intel_guc_log.c` 用 relay 暴露 GuC 固件日志。`drivers/net/wwan/iosm/iosm_ipc_trace.c` 用 relay 传输 IPC 跟踪数据。全树共 9 个 C 文件调用 `relay_open()`。

## 2 核心数据结构

头文件：`include/linux/relay.h`

```c
struct rchan {                        /* :59 */
    u32 version;                      /* RELAYFS_CHANNEL_VERSION = 7 */
    size_t subbuf_size;               /* 子缓冲区大小 */
    size_t n_subbufs;                 /* 每 CPU 的子缓冲区数 */
    const struct rchan_callbacks *cb; /* 客户端回调 */
    struct rchan_buf * __percpu *buf; /* per-CPU 缓冲区 */
    int is_global;
    char base_filename[NAME_MAX];
};

struct rchan_buf {                    /* :34 */
    void *start;                      /* 缓冲区起始 */
    void *data;                       /* 当前子缓冲区起始 */
    size_t offset;                    /* 当前偏移 */
    size_t subbufs_produced;          /* 已生产子缓冲区计数 */
    size_t subbufs_consumed;          /* 已消费子缓冲区计数 */
    wait_queue_head_t read_wait;      /* 读者等待队列 */
    struct dentry *dentry;            /* debugfs 文件 */
    unsigned int cpu;                 /* 此缓冲区对应的 CPU */
} ____cacheline_aligned;
```

`struct rchan_callbacks`（`:80`）：
- `subbuf_start()`：子缓冲区切换时调用
- `create_buf_file()`：**必须实现**，创建 relay 文件的 dentry
- `remove_buf_file()`：**必须实现**，删除文件

## 3 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `relay_open(base_filename, parent, subbuf_size, n_subbufs, cb, private)` | 打开 relay 通道 | `kernel/relay.c:474`，为每个在线 CPU 创建缓冲区 |
| `relay_close(chan)` | 关闭通道 | `:752` |
| `relay_flush(chan)` | 刷新所有缓冲区 | `:785` |
| `relay_reset(chan)` | 重置通道 | `:319` |
| `relay_subbufs_consumed(chan, cpu, consumed)` | 通知已消费 | `:726` |
| `relay_write(chan, data, length)` | 写入数据 | inline，`relay.h:189`，中断安全（`local_irq_save`） |
| `__relay_write(chan, data, length)` | 写入（仅禁止抢占） | `relay.h:217` |
| `relay_reserve(chan, length)` | 预留空间（完全无锁） | `relay.h:242`，调用者自行同步 |

## 4 无锁 per-CPU 设计

1. **per-CPU 分配**：`relay_open()` 为每个 CPU 创建独立的 `rchan_buf`
2. **中断安全写入**：`relay_write()` 用 `local_irq_save()` 保护当前 CPU 的缓冲区
3. **子缓冲区切换**：当 `offset + length > subbuf_size` 时切换到下一个子缓冲区
4. **用户空间暴露**：每个 per-CPU 缓冲区对应一个 debugfs 文件（如 `cpu0`、`cpu1`），用户空间通过 read/mmap/splice 读取
5. **读者唤醒**：使用 `irq_work` 将唤醒从原子上下文延迟到进程上下文

## 5 消费者

主要用户是跟踪/调试子系统：`ath9k`、`ath11k`、`ath10k`、`mt76` 无线驱动（频谱调试）。共 15 个文件使用 relay。
