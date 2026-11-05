# 杂项：ring_buffer 与 trace 缓冲区

> B 级文档。ring_buffer 是 ftrace / tracepoint 的底层数据通道，不属于调试逻辑本身，但理解它才能理解 trace 数据怎么从产生到被消费。

### 定位

**功能**：提供 ftrace 和 tracepoint 的底层无锁 per-CPU 环形缓冲区，负责将追踪事件从产生端（中断处理、系统调用、函数调用等）高效传递到消费端（tracefs 读取、perf 采集）。是内核追踪子系统的数据传输通道。

**实现方式**：每个 CPU 维护独立的 `struct ring_buffer_per_cpu`（`kernel/trace/ring_buffer.c:497-520`），写者通过 `arch_spinlock_t`（arm64 上是 `ldxr`/`stxr` 票锁）序列化，读者通过 `raw_spinlock_t` 序列化，不同 CPU 之间完全无竞争。事件头 `struct ring_buffer_event` 用 5 位 type_len + 27 位 time_delta 紧凑编码。支持 5 级上下文嵌套写入（NMI → IRQ → SOFTIRQ → NORMAL），通过 `current_context` 字段跟踪，保证高优先级中断打断低优先级写入时不破坏事件。

**应用场景**：`ring_buffer_write`/`ring_buffer_lock_reserve` 在 4 个文件的 12 处被直接调用，几乎所有使用者都是 trace 子系统内部代码：`kernel/trace/trace.c`（核心追踪缓冲区操作）、`kernel/trace/ring_buffer.c`（内部自引用）、`kernel/trace/ring_buffer_benchmark.c`（性能基准测试）。外部子系统通过 `trace_event` 机制间接使用——tracepoint 触发时调用 `trace_event_buffer_lock_reserve()` 内部转调 `ring_buffer_lock_reserve()`。

## 1 ring_buffer — 无锁 per-CPU 环形缓冲区

### 1.1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/trace/ring_buffer.c` | 6234 | 核心实现：分配、写入、读取、resize |
| `include/linux/ring_buffer.h` | 217 | 对外 API |

### 1.2 核心数据结构

**`struct ring_buffer_event`**（`include/linux/ring_buffer.h:15-19`）— 写入缓冲区的最小单元：

```c
struct ring_buffer_event {
    u32    type_len:5, time_delta:27;
    u32    array[];
};
```

- `type_len`：5 位。0–28 表示数据类型（`RINGBUF_TYPE_DATA_TYPE_LEN_MAX = 28`），≥29 表示特殊类型
- `time_delta`：27 位。与上一个事件的时间差；若需更多位则插入 `RINGBUF_TYPE_TIME_EXTEND`（8 字节，59 位时间戳）

**`enum ring_buffer_type`**（`ring_buffer.h:55-60`）：

| 值 | 含义 |
|----|------|
| 0–28 | 数据记录。`type_len=0` 时长度在 `array[0]`；否则 `length = type_len << 2` |
| `RINGBUF_TYPE_PADDING` (29) | 页尾填充或丢弃事件 |
| `RINGBUF_TYPE_TIME_EXTEND` (30) | 扩展时间戳（28–59 位） |
| `RINGBUF_TYPE_TIME_STAMP` (31) | 绝对时间戳 |

**`struct buffer_page`**（`kernel/trace/ring_buffer.c:328-335`）— 管理单个数据页：

```c
struct buffer_page {
    struct list_head list;
    local_t    write;      // 下一个写入位置
    unsigned   read;       // 下一个读取位置
    local_t    entries;    // 本页条目数
    unsigned long real_end;
    struct buffer_data_page *page;
};
```

**`struct ring_buffer_per_cpu`**（`kernel/trace/ring_buffer.c:497-520`）— 每个 CPU 的独立缓冲区：

```c
struct ring_buffer_per_cpu {
    int                cpu;
    atomic_t           record_disabled;
    raw_spinlock_t     reader_lock;    // 序列化读者
    arch_spinlock_t    lock;           // 写者锁
    struct buffer_page *head_page;     // 读端
    struct buffer_page *tail_page;     // 写端
    struct buffer_page *commit_page;   // 已提交
    struct buffer_page *reader_page;
    local_t            entries_bytes;
    local_t            entries;
    local_t            overrun;
    ...
};
```

**关键设计**：写者用 `arch_spinlock_t`（在 arm64 上是 `ldxr`/`stxr` 票锁），读者用 `raw_spinlock_t` 序列化。不同 CPU 之间完全无竞争——每个 CPU 写自己的 `ring_buffer_per_cpu`。

### 1.3 上下文分级

`kernel/trace/ring_buffer.c:462-470` 定义了 5 个上下文级别：

| 级别 | 值 | 含义 |
|------|---|------|
| `RB_CTX_TRANSITION` | 0 | 上下文切换过渡 |
| `RB_CTX_NMI` | 1 | NMI 上下文 |
| `RB_CTX_IRQ` | 2 | 硬中断 |
| `RB_CTX_SOFTIRQ` | 3 | 软中断 |
| `RB_CTX_NORMAL` | 4 | 进程上下文 |
| `RB_CTX_MAX` | 5 | — |

同一 CPU 上不同上下文级别各自嵌套写入（最多 `MAX_NEST=5` 层），通过 `current_context` 字段跟踪。这保证了 NMI 打断 IRQ 时不会破坏正在写入的事件。

### 1.4 核心 API

头文件：`include/linux/ring_buffer.h`；实现：`kernel/trace/ring_buffer.c`

**分配与释放**

| API | 说明 | 备注 |
|-----|------|------|
| `ring_buffer_alloc(size, flags)` | 分配 per-CPU 环形缓冲区 | 宏，自动创建 `lock_class_key`；`size` 是每个 CPU 的字节数 |
| `ring_buffer_free(buffer)` | 释放缓冲区 | — |
| `ring_buffer_resize(buffer, size, cpu)` | 运行时调整大小 | `cpu = RING_BUFFER_ALL_CPUS` 调整全部 |

**写入路径**

| API | 说明 | 备注 |
|-----|------|------|
| `ring_buffer_lock_reserve(buffer, length)` | 预留空间并返回事件指针 | 返回 `struct ring_buffer_event *`；写入后必须 commit |
| `ring_buffer_unlock_commit(buffer, event)` | 提交事件 | 与 `lock_reserve` 配对 |
| `ring_buffer_write(buffer, length, data)` | 一步写入 | 内部调用 `lock_reserve` + `memcpy` + `unlock_commit` |
| `ring_buffer_discard_commit(buffer, event)` | 丢弃已预留但未提交的事件 | 调用后不能再 `unlock_commit` |
| `ring_buffer_nest_start/end(buffer)` | 标记嵌套写入开始/结束 | 用于同一上下文级别内的嵌套事件 |

**读取路径**

| API | 说明 | 备注 |
|-----|------|------|
| `ring_buffer_peek(buffer, cpu, ts, lost_events)` | 偷看下一个事件（不消费） | — |
| `ring_buffer_consume(buffer, cpu, ts, lost_events)` | 消费下一个事件 | 移动读指针 |
| `ring_buffer_read_prepare/read_start/read_finish` | 准备迭代器批量读取 | 三步：prepare → start → 循环 peek/advance → finish |
| `ring_buffer_iter_peek(iter, ts)` | 迭代器偷看 | — |
| `ring_buffer_iter_advance(iter)` | 迭代器前进 | — |
| `ring_buffer_alloc_read_page(buffer, cpu)` | 分配读页面 | 用于 `mmap` 式高效读取 |
| `ring_buffer_read_page(buffer, data_page, len, cpu, full)` | 读整页到用户缓冲区 | — |

**查询与控制**

| API | 说明 | 备注 |
|-----|------|------|
| `ring_buffer_size(buffer, cpu)` | 当前缓冲区大小（字节） | — |
| `ring_buffer_nr_pages(buffer, cpu)` | 页数 | — |
| `ring_buffer_entries(buffer)` | 总事件数 | — |
| `ring_buffer_overruns(buffer)` | 溢出次数 | — |
| `ring_buffer_empty(buffer)` | 是否为空 | — |
| `ring_buffer_record_disable/enable(buffer)` | 全局禁止/启用记录 | 不释放缓冲区，只停止写入 |
| `ring_buffer_reset(buffer)` | 重置所有 CPU | — |
| `ring_buffer_swap_cpu(a, b, cpu)` | 交换两个缓冲区的某 CPU 数据 | 需 `CONFIG_RING_BUFFER_ALLOW_SWAP` |
| `ring_buffer_wait(buffer, cpu, full)` | 等待数据可用 | 阻塞 |
| `ring_buffer_poll_wait(buffer, cpu, filp, poll_table, full)` | poll 等待 | 用于 `/sys/kernel/tracing/` 的 `poll()` |

### 1.5 写入流程

```
ring_buffer_write() / lock_reserve() + unlock_commit()
  │
  ├─ rb_reserve_event()
  │    ├─ rb_time_read() → 当前时间戳
  │    ├─ rb_calc_event_size() → 计算事件长度（含对齐到 4 字节）
  │    ├─ 检查 tail_page 剩余空间
  │    │    ├─ 够 → 直接在 tail_page 写入
  │    │    └─ 不够 → rb_allocate_cpu_buffer_page() 分配新页
  │    │         └─ 旧页加入链表，tail_page 指向新页
  │    ├─ 写入 ring_buffer_event 头部（type_len + time_delta）
  │    └─ 返回事件数据指针
  │
  └─ unlock_commit()
       └─ local_add() 更新 commit 计数器
           （写者和读者通过 commit_page vs head_page 判断哪些页可读）
```

### 1.6 与 ftrace / tracepoint 的关系

- **tracepoint** 触发时调用 `trace_event_buffer_lock_reserve()` → 内部调用 `ring_buffer_lock_reserve()`
- **ftrace** 的 `trace_event_buffer_commit()` → 内部调用 `ring_buffer_unlock_commit()`
- tracefs 的 `trace` 文件读取 → `ring_buffer_read_page()` 或迭代器接口
- `trace_buffer` 是对 `ring_buffer` 的薄封装（`kernel/trace/trace.h`），增加了 `trace_array` 级别的统计

### 1.7 arm64 特殊性

- `local_t` 在 arm64 上是 `long`（64 位），`local_add` / `local_read` 使用 `ldxr`/`stxr` 实现原子操作
- `arch_spinlock_t` 是 ARM64 票锁（`arch/arm64/include/asm/spinlock.h`）
- 时间戳来源：`arch_timer_read_counter()` → `arch/arm64/include/asm/arch_timer.h`
- `ring_buffer_time_stamp()` 调用 `sched_clock()` → arm64 走 `arch_timer`

### 1.8 使用统计

- `ring_buffer_lock_reserve` 在树内约 15 处直接调用（主要在 `kernel/trace/`）
- `ring_buffer_write` 约 8 处
- `ring_buffer_alloc` 约 12 处
- 几乎所有使用者都是 trace 子系统内部代码；外部子系统通过 `trace_event` 间接使用
