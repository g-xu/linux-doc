# 原子位操作与 local_t

> 两种基于原子操作的特殊化模块：atomic_bitops 提供对位图的原子位操作；local_t 提供单 CPU 原子性的轻量计数器。

---

## atomic_bitops — 原子位操作

### 定位

**功能**：对 `unsigned long` 位图中的单个位进行 SMP 安全的设置、清除、翻转和测试。广泛用于标志位管理——设备状态位、页标志（PG_locked、PG_dirty 等）、能力位、锁标志等需要跨 CPU 原子修改的场景。

**实现方式**：三层架构。公共 API 层（`include/asm-generic/bitops/instrumented-atomic.h`）先做 KCSAN 仪器化检查再委托给 `arch_*`。通用原子层（`include/asm-generic/bitops/atomic.h`）用 `atomic_long_*` 实现：`set_bit` → `atomic_long_or(BIT_MASK(nr), ...)`、`test_and_set_bit` → `atomic_long_fetch_or(mask, ...)`。arm64 双路径（`arch/arm64/include/asm/lse.h:24-29`）：运行时根据 CPU 能力选择 LSE 原子指令（`stset`/`ldset` 等）或 LL/SC 循环（`ldxr`/`stxr`）。关键内存序差异：无返回值操作（`set_bit`、`clear_bit`）是 relaxed 无屏障；有返回值操作（`test_and_set_bit` 等）提供全序语义。

**应用场景**：全内核最广泛使用的原子操作之一。`set_bit` 7,086 处、`clear_bit` 5,092 处、`test_and_set_bit` 1,594 处、`test_and_clear_bit` 1,441 处调用。主要用户涵盖内存管理（`mm/page_alloc.c` 页标志）、网络（`net/core/dev.c` 设备状态）、调度器（`kernel/sched/`）、锁子系统（`kernel/locking/lockdep.c`）和定时器（`kernel/time/`）。

### 实现方式

三层架构：

1. **公共 API 层**（`include/asm-generic/bitops/instrumented-atomic.h`）：每个操作先做 KCSAN 仪器化检查，再委托给 `arch_*`
2. **通用原子层**（`include/asm-generic/bitops/atomic.h`）：用 `atomic_long_*` 位操作实现
   - `set_bit` → `atomic_long_or(BIT_MASK(nr), ...)`
   - `clear_bit` → `atomic_long_andnot(BIT_MASK(nr), ...)`
   - `test_and_set_bit` → `atomic_long_fetch_or(mask, ...)`
3. **arm64 双路径**（`arch/arm64/include/asm/lse.h:24-29`）：运行时选择 LSE 或 LL/SC
   - LSE：`stset`/`stclr`/`steor`（无返回值）或 `ldset`/`ldclr`/`ldeor`（有返回值）
   - LL/SC：`ldxr`/`stxr` 循环

### 两类操作的内存序差异

| 类别 | 操作 | 返回值 | 内存序 |
|------|------|--------|--------|
| 无返回 | `set_bit`、`clear_bit`、`change_bit` | void | **relaxed**（无屏障） |
| 有返回 | `test_and_set_bit`、`test_and_clear_bit`、`test_and_change_bit` | 旧位值 | **全序** |

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `set_bit(nr, addr)` | 设置位 | relaxed，无返回值 |
| `clear_bit(nr, addr)` | 清除位 | relaxed |
| `change_bit(nr, addr)` | 翻转位 | relaxed |
| `test_and_set_bit(nr, addr)` | 设置位，返回旧值 | 全序 |
| `test_and_clear_bit(nr, addr)` | 清除位，返回旧值 | 全序 |
| `test_and_change_bit(nr, addr)` | 翻转位，返回旧值 | 全序 |
| `test_bit(nr, addr)` | 测试位 | 只读 |
| `test_bit_acquire(nr, addr)` | 测试位 + acquire | 后续读不重排到此之前 |
| `__set_bit`/`__clear_bit` | 非原子版本 | 仅在关抢占时使用 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `set_bit` | 7,086 |
| `clear_bit` | 5,092 |
| `test_and_set_bit` | 1,594 |
| `test_and_clear_bit` | 1,441 |

---

## local_t — 单 CPU 原子计数器

### 定位

**功能**：提供单 CPU 原子性的轻量计数器操作。只保证在关抢占或关中断时的单 CPU 原子性，不提供跨 CPU 同步。语义上表达"这个变量只在本 CPU 上被修改，但可能被中断打断"的场景。在 arm64 上退化为 `atomic_long_t` 的包装——因为 arm64 没有专用的 per-CPU 原子指令。

**实现方式**：`include/asm-generic/local.h:22-25` 定义 `typedef struct { atomic_long_t a; } local_t;`。所有操作直接委托给 `atomic_long_*`。源码注释（`:13`）承认这在通用实现下"rather pointless"，但在有专用 per-CPU 原子指令的架构（如 x86 的 `inc`/`dec` 不需要锁定总线）上可以覆盖为更高效的实现。与 `atomic_t` 的核心区别：`atomic_t` 提供跨 CPU 原子性，`local_t` 仅单 CPU 原子性，使用前提是关抢占或关中断。

**应用场景**：使用量较小（322 处 API 调用、130 处类型引用），主要在 per-CPU 统计计数和调度器中。典型用户：块 I/O 层（`block/blk-mq.c`、`block/blk-core.c`、`block/blk-iocost.c`）、perf 事件（`kernel/events/core.c`、`kernel/events/ring_buffer.c`）、trace ring buffer（`kernel/trace/ring_buffer.c`）、BPF 内存分配器（`kernel/bpf/memalloc.c`）和 Coresight 硬件追踪（`drivers/hwtracing/coresight/`）。在 arm64 上性能与 `atomic_t` 相同，使用 `local_t` 纯粹为了语义清晰和跨架构可移植性。

### 实现方式

`include/asm-generic/local.h:22-25`：

```c
typedef struct {
    atomic_long_t a;
} local_t;
```

所有操作直接委托给 `atomic_long_*`。注释（`:13`）承认这在通用实现下"rather pointless"，但某些架构（如 x86）有专用的 `inc`/`dec` 指令可以覆盖。

### 与 atomic_t 的区别

| 特性 | atomic_t | local_t |
|------|----------|---------|
| 跨 CPU 原子性 | 是 | 否 |
| 使用前提 | 无 | 关抢占或关中断 |
| 典型用途 | 共享数据 | per-CPU 计数器 |
| arm64 性能 | 相同 | 相同（退化为 atomic_long） |

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `LOCAL_INIT(i)` | 静态初始化 | |
| `local_read(l)` | 读取 | |
| `local_set(l, i)` | 设置 | |
| `local_inc(l)` / `local_dec(l)` | 加 1 / 减 1 | |
| `local_add(i, l)` / `local_sub(i, l)` | 加 / 减 | |
| `local_add_return(i, l)` | 加并返回 | |
| `local_dec_and_test(l)` | 减 1，返回是否到 0 | |
| `local_cmpxchg(l, old, new)` | 比较并交换 | |

非原子快速路径（关抢占时使用）：`__local_inc`、`__local_dec`、`__local_add`、`__local_sub`。

### 使用规模

322 处 API 调用，130 处类型引用。使用量较小，主要在 per-CPU 统计和调度器中。
