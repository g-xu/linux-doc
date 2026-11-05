# 锁机制杂项 — raw_spinlock / bit_spinlock / rwlock / local_lock

> 四个与 spinlock 密切相关的锁变体。raw_spinlock 是 PREEMPT_RT 下唯一保证真正自旋的锁；bit_spinlock 用单个位实现自旋锁；rwlock 是读写分离的自旋锁；local_lock 在非 RT 下仅禁止抢占、在 RT 下变为真正的 per-CPU spinlock。

---

## raw_spinlock

### 定位

**功能**：在所有内核配置下（包括 PREEMPT_RT）都保证真正自旋的锁。普通 `spinlock_t` 在 PREEMPT_RT 下会被替换为可睡眠的 rt_mutex 实现，而 `raw_spinlock_t` 不受该替换影响，始终执行忙等待自旋。

**实现方式**：`raw_spinlock_t` 内嵌 `arch_spinlock_t`（arm64 上即 qspinlock，使用 32 位原子操作的排队自旋锁），直接映射到硬件级原子指令，不经过 PREEMPT_RT 的可睡眠替换路径。调试模式下额外包含 magic 字段、owner_cpu 和 lockdep 映射。

**应用场景**：调度器内部（`kernel/sched/core.c` 中的 `rq->lock`）、中断控制器（`irq_desc->lock`）、timer 子系统、KVM 虚拟化（`virt/kvm/kvm_main.c`）、io_uring（`io_uring/io-wq.c`）等 PREEMPT_RT 下不能睡眠的核心路径。

### 核心结构

`include/linux/spinlock_types_raw.h:14-23`：

```c
typedef struct raw_spinlock {
    arch_spinlock_t raw_lock;       // arm64: qspinlock (32-bit atomic)
    // CONFIG_DEBUG_SPINLOCK: unsigned int magic, owner_cpu; void *owner;
    // CONFIG_DEBUG_LOCK_ALLOC: struct lockdep_map dep_map;
} raw_spinlock_t;
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_RAW_SPINLOCK(name)` | 静态定义 | |
| `raw_spin_lock_init(lock)` | 动态初始化 | |
| `raw_spin_lock(lock)` | 获取（自旋） | 关抢占；不关中断 |
| `raw_spin_unlock(lock)` | 释放 | |
| `raw_spin_trylock(lock)` | 尝试获取 | 返回 1=成功，0=失败 |
| `raw_spin_lock_irq(lock)` | 获取 + 关中断 | |
| `raw_spin_unlock_irq(lock)` | 释放 + 开中断 | |
| `raw_spin_lock_irqsave(lock, flags)` | 获取 + 保存/关中断 | |
| `raw_spin_unlock_irqrestore(lock, flags)` | 释放 + 恢复中断 | |

头文件：`include/linux/spinlock.h`；类型：`include/linux/spinlock_types_raw.h`

### 使用注意

- 大多数内核代码应使用 `spinlock_t`，只有核心路径需要 `raw_spinlock_t`
- 滥用 `raw_spinlock_t` 会损害 RT 延迟
- 每个 `spin_*` 函数都有对应的 `raw_spin_*` 版本

---

## bit_spinlock

### 定位

**功能**：用 `unsigned long` 中的单个位实现自旋锁，不需要独立的锁结构体。适用于已有数据结构中可以借用一位的场景，实现零额外内存开销的锁保护。

**实现方式**：加锁通过 `test_and_set_bit_lock()` 原子设置目标位实现，解锁通过 `clear_bit_unlock()` 清除该位实现；SMP 下竞争时调用 `preempt_disable()` + `cpu_relax()` 循环自旋。UP 无 DEBUG 时退化为纯 `preempt_disable/enable`。整个实现是纯头文件（`include/linux/bit_spinlock.h`，101 行）。

**应用场景**：SLUB 分配器（`mm/slub.c` 中借用 page 标志位）、zram 压缩块设备（`drivers/block/zram/zram_drv.c` 中借用表项标志位）等需要极致节省内存的场景。全树约 2 处使用——只在确实需要"零开销借用一位"时才用。

### 核心实现

`include/linux/bit_spinlock.h`（101 行，纯头文件）：

- 加锁：`preempt_disable()` → `test_and_set_bit_lock()` → 失败则 `preempt_enable()` + `cpu_relax()` 循环
- 解锁：`clear_bit_unlock()` → `preempt_enable()`
- UP 无 DEBUG 时退化为纯 `preempt_disable/enable`

### API

| API | 说明 | 备注 |
|-----|------|------|
| `bit_spin_lock(bitnum, addr)` | 获取 | `bitnum` 是位号，`addr` 是所在字 |
| `bit_spin_trylock(bitnum, addr)` | 尝试获取 | 返回 0=成功，1=失败 |
| `bit_spin_unlock(bitnum, addr)` | 释放（原子） | `clear_bit_unlock()` |
| `__bit_spin_unlock(bitnum, addr)` | 释放（非原子） | 当锁本身已保护该字时用 |
| `bit_spin_is_locked(bitnum, addr)` | 查询 | `test_bit()` |

### 使用注意

- `bitnum` 必须在 `0..BITS_PER_LONG-1` 范围内
- 被锁定位所在的 `unsigned long` 的其他位可以安全地用于其他目的
- 全树仅约 10 处使用——只在确实需要"零开销借用一位"时才用

---

## rwlock

### 定位

**功能**：读写分离的自旋锁——多个读者可并发持有读锁，写者独占访问。适用于读多写少且临界区极短的数据结构保护，不可睡眠。

**实现方式**：`rwlock_t` 包装 `arch_rwlock_t`（arm64 上是 qrwlock，使用原子计数器 `atomic_t cnts` 记录读者数量加写者锁定位，配合 `arch_spinlock_t wait_lock` 保护写者排队）。与 spinlock 具有相同的变体体系（irq/irqsave/bh）。PREEMPT_RT 下变为可睡眠的 `rwbase_rt` 实现。

**应用场景**：短临界区的读写保护，如 dcache 目录项缓存、进程列表遍历等。全树 `read_lock` 559 处、`write_lock` 544 处，广泛分布于 VFS、网络、设备驱动等子系统。临界区较长时应改用 `rw_semaphore`。

### 核心结构

`include/linux/rwlock_types.h:25-34`：

```c
typedef struct {
    arch_rwlock_t raw_lock;
    // debug + lockdep fields
} rwlock_t;
```

arm64 的 `arch_rwlock_t`（`include/asm-generic/qrwlock_types.h:13-27`）：

```c
typedef struct qrwlock {
    union {
        atomic_t cnts;           // 读者计数 + 写者锁定位
        struct { u8 wlocked; u8 __lstate[3]; };
    };
    arch_spinlock_t wait_lock;   // 保护写者排队
} arch_rwlock_t;
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_RWLOCK(x)` | 静态定义 | |
| `rwlock_init(lock)` | 动态初始化 | |
| `read_lock(lock)` | 获取读锁 | 多读者并发 |
| `read_unlock(lock)` | 释放读锁 | |
| `read_trylock(lock)` | 尝试获取读锁 | |
| `write_lock(lock)` | 获取写锁 | 独占 |
| `write_unlock(lock)` | 释放写锁 | |
| `write_trylock(lock)` | 尝试获取写锁 | |
| `read_lock_irqsave(lock, flags)` | 读锁 + 关中断 | |
| `write_lock_irqsave(lock, flags)` | 写锁 + 关中断 | |
| `read_unlock_irqrestore(lock, flags)` | 释放读锁 + 恢复中断 | |
| `write_unlock_irqrestore(lock, flags)` | 释放写锁 + 恢复中断 | |
| `read_lock_bh(lock)` | 读锁 + 禁 softirq | |
| `write_lock_bh(lock)` | 写锁 + 禁 softirq | |

头文件：`include/linux/rwlock.h`；SMP 实现：`include/linux/rwlock_api_smp.h`

### 使用注意

- 不可睡眠——仅用于短临界区
- 临界区较长时用 `rw_semaphore` 替代
- PREEMPT_RT 下变为可睡眠的 `rwbase_rt` 实现

---

## local_lock

### 定位

**功能**：per-CPU 数据保护锁——非 RT 下仅禁止抢占（保证当前 CPU 对 per-CPU 数据的独占访问），PREEMPT_RT 下变为真正的 per-CPU spinlock，防止因任务迁移导致的并发问题。

**实现方式**：非 RT 下 `local_lock_t` 是空结构（仅包含 lockdep 字段），`local_lock()` 等价于 `preempt_disable()`，零锁开销。PREEMPT_RT 下 `typedef spinlock_t local_lock_t`，`local_lock()` 变为 `migrate_disable()` + `spin_lock(this_cpu_ptr(lock))`，通过真正的自旋锁保护被迁移任务访问的 per-CPU 数据。实现在 `include/linux/local_lock.h`（54 行）和 `include/linux/local_lock_internal.h`（142 行）。

**应用场景**：保护 per-CPU 数据，如 SLUB 分配器的 per-CPU 缓存（`mm/slub.c`）、per-CPU 页面缓存（`mm/swap.c`）、mmap 锁（`mm/mmap_lock.c`）、softirq 处理（`kernel/softirq.c`）、zsmalloc（`mm/zsmalloc.c`）等。全树 89 处使用。设计目的是让同一份代码在 RT 和非 RT 下都能正确工作。

### 核心实现

`include/linux/local_lock.h`（54 行）+ `include/linux/local_lock_internal.h`（142 行）：

- 非 RT：`local_lock_t` 是空结构（仅 lockdep 字段）
- PREEMPT_RT：`typedef spinlock_t local_lock_t`

### API

| API | 说明 | 备注 |
|-----|------|------|
| `local_lock(lock)` | 获取 per-CPU 锁 | 非 RT = preempt_disable |
| `local_unlock(lock)` | 释放 | |
| `local_lock_irq(lock)` | + 关中断 | |
| `local_unlock_irq(lock)` | + 开中断 | |
| `local_lock_irqsave(lock, flags)` | + 保存/关中断 | |
| `local_unlock_irqrestore(lock, flags)` | + 恢复中断 | |

头文件：`include/linux/local_lock.h`

### 使用注意

- 设计目的是让 per-CPU 数据保护代码在 RT 和非 RT 下都能正确工作
- 非 RT 下零开销（仅 preempt_disable）
- RT 下需要真正的锁是因为 per-CPU 数据可能被迁移后的任务访问
