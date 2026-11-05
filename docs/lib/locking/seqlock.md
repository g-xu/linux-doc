# seqlock — 序列锁

> 读写一致性机制。写者不阻塞读者，读者通过序列号检测一致性并重试。适用于读极多写极少、读者不能阻塞的场景（如 timekeeping、dcache）。完全在头文件中实现，无 .c 文件。

---

## §1 核心设计洞察

### 1.1 序列号的工作原理

seqlock 的核心是一个 `unsigned sequence` 计数器（`include/linux/seqlock.h:64-69`）：

```
sequence 值:  0    1    2    3    4    5    6
              偶   奇   偶   奇   偶   奇   偶
              稳定 写中 稳定 写中 稳定 写中 稳定
```

- **偶数** = 数据稳定，没有写者正在修改
- **奇数** = 写者正在修改中，数据不一致

**写者**：begin 时 +1（偶→奇），end 时 +1（奇→偶）。两次递增之间插入 `smp_wmb()` 保证写操作的顺序。

**读者**：循环读取，直到 begin 和 end 看到同一个偶数值。如果看到奇数或两次值不同 → 重试。

### 1.2 两种变体

| 变体 | 结构 | 写者保护 | 适用场景 |
|------|------|---------|---------|
| `seqcount_t` | 纯计数器 | **无**——调用者自行保证写者互斥 | 写者已经持有其他锁 |
| `seqlock_t` | 计数器 + spinlock | 内嵌 spinlock 自动互斥 | 需要独立写者保护 |

```c
// seqcount_t — 纯计数器 (include/linux/seqlock.h:64-69)
typedef struct seqcount {
    unsigned sequence;
    // CONFIG_DEBUG_LOCK_ALLOC: struct lockdep_map dep_map
} seqcount_t;

// seqlock_t — 计数器 + spinlock (include/linux/seqlock.h:853-860)
typedef struct {
    seqcount_spinlock_t seqcount;  // 包含 seqcount_t + spinlock_t *lock
    spinlock_t lock;
} seqlock_t;
```

还有带锁关联的 `seqcount_LOCKNAME_t`（`include/linux/seqlock.h:198-202`），用于 lockdep 验证：
- `seqcount_raw_spinlock_t`：关联 `raw_spinlock_t`
- `seqcount_spinlock_t`：关联 `spinlock_t`
- `seqcount_rwlock_t`：关联 `rwlock_t`
- `seqcount_mutex_t`：关联 `struct mutex`

### 1.3 写者操作

```c
// 写者 begin (include/linux/seqlock.h:466-471)
static inline void do_raw_write_seqcount_begin(seqcount_t *s)
{
    kcsan_nestable_atomic_begin();
    s->sequence++;      // 偶 → 奇
    smp_wmb();          // 后续写操作在此之后
}

// 写者 end (include/linux/seqlock.h:487-492)
static inline void do_raw_write_seqcount_end(seqcount_t *s)
{
    smp_wmb();          // 前面的写操作在此之前完成
    s->sequence++;      // 奇 → 偶
    kcsan_nestable_atomic_end();
}
```

`seqlock_t` 的写者操作自动加/解锁（`include/linux/seqlock.h:931-948`）：

```c
static inline void write_seqlock(seqlock_t *sl)
{
    spin_lock(&sl->lock);
    write_seqcount_begin(&sl->seqcount.seqcount);
}

static inline void write_sequnlock(seqlock_t *sl)
{
    write_seqcount_end(&sl->seqcount.seqcount);
    spin_unlock(&sl->lock);
}
```

### 1.4 读者操作

```c
// 读者 begin (include/linux/seqlock.h:325-334)
#define __read_seqcount_begin(s)                    \
    ({                                              \
        unsigned __seq;                             \
        while ((__seq = seqprop_sequence(s)) & 1)   \
            cpu_relax();    /* 奇数 = 写者进行中，自旋等待 */  \
        __seq;                                      \
    })

// 读者 retry 检查 (include/linux/seqlock.h:423-430)
static inline int do___read_seqcount_retry(const seqcount_t *s, unsigned start)
{
    return unlikely(READ_ONCE(s->sequence) != start);
}
```

**典型读者模式**：

```c
unsigned seq;
do {
    seq = read_seqcount_begin(&my_seq);
    // 读取受保护的数据（多字段快照）
    val1 = data->field1;
    val2 = data->field2;
} while (read_seqcount_retry(&my_seq, seq));
```

### 1.5 内存序保证

| 位置 | 屏障 | 作用 |
|------|------|------|
| 写者 begin | `smp_wmb()` | 保证序列号递增在数据修改之前可见 |
| 写者 end | `smp_wmb()` | 保证数据修改在序列号递增之前可见 |
| 读者 begin | `smp_rmb()`（`raw_read_seqcount_begin`） | 保证序列号读取在数据读取之前 |
| 读者 retry | `smp_rmb()`（`read_seqcount_retry`） | 保证数据读取在序列号重读之前 |

### 1.6 seqlock 不能保护什么

**指针**（`include/linux/seqlock.h:51-52`）：

> This mechanism can't be used if the protected data contains pointers, as the writer can invalidate a pointer that a reader is following.

如果数据包含指针，写者可能在读者正在跟随指针时使其失效。指针保护需要 RCU。

### 1.7 seqcount_latch：双缓冲

`seqcount_latch_t`（`include/linux/seqlock.h:647-649`）用于 NMI 安全的时间读取。维护两份数据副本，写者通过 latch 切换读者到另一份副本：

```c
struct tk_fast {
    seqcount_latch_t seq;
    struct tk_read_base base[2];   // 两份副本
};
```

写者先更新非活跃副本，然后调用 `raw_write_seqcount_latch()` 切换——读者始终读取稳定副本。用于 `kernel/time/timekeeping.c` 的 NMI-safe 时间读取。

### 1.8 PREEMPT_RT 下的行为变化

在 PREEMPT_RT 下（`include/linux/seqlock.h:215-230`），`seqcount_LOCKNAME_t` 的写者不能禁止抢占（否则会导致高延迟）。

**解决方案**：读者检测到写者进行中时，主动获取并释放关联的锁——这让被抢占的写者得以继续执行：

```c
// include/linux/seqlock.h:215-230 (简化)
if (preemptible && unlikely(seq & 1)) {
    lock_acquire(...);         // 获取关联锁
    lock_release(...);         // 立即释放——让被抢占的写者运行
    seq = READ_ONCE(s->sequence);  // 重新读取
}
```

| seqcount 类型 | PREEMPT_RT 下 preemptible |
|---------------|--------------------------|
| `seqcount_raw_spinlock_t` | `false`（不变） |
| `seqcount_spinlock_t` | `true`（RT 下启用帮助机制） |
| `seqcount_rwlock_t` | `true` |
| `seqcount_mutex_t` | `true`（始终） |

---

## §2 API 速查

### 2.1 初始化

头文件：`include/linux/seqlock.h`（1269 行，纯头文件实现）

| API | 说明 | 备注 |
|-----|------|------|
| `SEQCNT_ZERO(name)` | 静态初始化 seqcount_t | 编译期 |
| `seqcount_init(s)` | 动态初始化 seqcount_t | 宏 |
| `DEFINE_SEQLOCK(sl)` | 静态定义 seqlock_t | 编译期 |
| `seqlock_init(sl)` | 动态初始化 seqlock_t | |
| `seqcount_spinlock_init(s, lock)` | 初始化带 spinlock 关联的 seqcount | |
| `seqcount_raw_spinlock_init(s, lock)` | 初始化带 raw_spinlock 关联的 seqcount | |
| `seqcount_rwlock_init(s, lock)` | 初始化带 rwlock 关联的 seqcount | |
| `seqcount_mutex_init(s, lock)` | 初始化带 mutex 关联的 seqcount | |

### 2.2 读侧 API

| API | 说明 | 备注 |
|-----|------|------|
| `read_seqcount_begin(s)` | 开始读取（带 lockdep） | 奇数时自旋等待 |
| `read_seqcount_retry(s, start)` | 检查是否需要重试（带 `smp_rmb`） | 返回 1=需重试 |
| `__read_seqcount_begin(s)` | 开始读取（无屏障） | 性能关键路径用 |
| `__read_seqcount_retry(s, start)` | 检查重试（无屏障） | |
| `raw_read_seqcount_begin(s)` | 开始读取（有 `smp_rmb`，无 lockdep） | |
| `raw_seqcount_begin(s)` | 开始读取（不稳定化） | 不等奇数变偶数 |
| `raw_read_seqcount(s)` | 直接读序列号 | 不做 LSB 处理 |
| `read_seqbegin(sl)` | seqlock_t 版本的 begin | 包装 read_seqcount_begin |
| `read_seqretry(sl, start)` | seqlock_t 版本的 retry | 包装 read_seqcount_retry |

### 2.3 写侧 API

| API | 说明 | 备注 |
|-----|------|------|
| `write_seqcount_begin(s)` | 开始写（带 lockdep） | 序列号偶→奇 + `smp_wmb` |
| `write_seqcount_end(s)` | 结束写（带 lockdep） | `smp_wmb` + 序列号奇→偶 |
| `raw_write_seqcount_begin(s)` | 开始写（无 lockdep） | |
| `raw_write_seqcount_end(s)` | 结束写（无 lockdep） | |
| `write_seqlock(sl)` | seqlock_t 写开始 | spin_lock + begin |
| `write_sequnlock(sl)` | seqlock_t 写结束 | end + spin_unlock |
| `write_seqlock_bh(sl)` | _bh 变体 | spin_lock_bh + begin |
| `write_sequnlock_bh(sl)` | _bh 变体 | end + spin_unlock_bh |
| `write_seqlock_irq(sl)` | _irq 变体 | |
| `write_sequnlock_irq(sl)` | _irq 变体 | |
| `write_seqlock_irqsave(sl, flags)` | _irqsave 变体 | |
| `write_sequnlock_irqrestore(sl, flags)` | _irqsave 变体 | |
| `raw_write_seqcount_barrier(s)` | 写屏障（排序保证） | 不修改序列号 |
| `write_seqcount_invalidate(s)` | 使读者失效 | begin + end |

### 2.4 Latch API

| API | 说明 | 备注 |
|-----|------|------|
| `raw_read_seqcount_latch(s)` | 读 latch 计数器 | 用于双缓冲读取 |
| `raw_write_seqcount_latch(s)` | 切换读者到另一份副本 | 写者更新非活跃副本后调用 |
| `write_seqcount_latch_begin(s)` | 开始 latch 写 | |
| `write_seqcount_latch(s)` | 执行切换 | |
| `write_seqcount_latch_end(s)` | 结束 latch 写 | |

### 2.5 混合读者 API

| API | 说明 | 备注 |
|-----|------|------|
| `read_seqbegin_or_lock(lock, seq)` | 混合：首次无锁，后续加锁 | `*seq` 初始为 0 |
| `need_seqretry(lock, seq)` | 检查混合读是否需要重试 | |
| `done_seqretry(lock, seq)` | 结束混合读 | 如果加了锁则解锁 |

---

## §3 使用模式

### 3.1 seqlock_t 基本用法（dcache rename_lock）

```c
// 全局定义 (fs/dcache.c:77)
DEFINE_SEQLOCK(rename_lock);

// 写者：独占修改
void d_rename(struct dentry *old, struct dentry *new)
{
    write_seqlock(&rename_lock);
    // 修改 dentry 树结构
    old->d_parent = new_parent;
    write_sequnlock(&rename_lock);
}

// 读者：无锁读取 + 重试
void lookup_path(const char *name, struct path *result)
{
    unsigned seq;
    do {
        seq = read_seqbegin(&rename_lock);
        // 遍历 dentry 树
        walk_dentry_tree(name, result);
    } while (read_seqretry(&rename_lock, seq));
}
```

### 3.2 seqcount_t + 外部锁（timekeeping）

```c
// kernel/time/timekeeping.c:50-55
static struct {
    seqcount_raw_spinlock_t seq;
    struct timekeeper timekeeper;
} tk_core ____cacheline_aligned = {
    .seq = SEQCNT_RAW_SPINLOCK_ZERO(tk_core.seq, &timekeeper_lock),
};

// 写者：已经持有 timekeeper_lock
void update_timekeeper(void)
{
    raw_spin_lock_irqsave(&timekeeper_lock, flags);
    write_seqcount_begin(&tk_core.seq);
    // 修改 timekeeper 字段
    tk->xtime_sec = new_sec;
    tk->tkr_mono.xtime_nsec = new_nsec;
    write_seqcount_end(&tk_core.seq);
    raw_spin_unlock_irqrestore(&timekeeper_lock, flags);
}

// 读者：无锁快速路径
u64 ktime_get_real_ns(void)
{
    unsigned int seq;
    u64 secs, nsecs;
    do {
        seq = read_seqcount_begin(&tk_core.seq);
        secs = tk->xtime_sec;
        nsecs = tk->tkr_mono.xtime_nsec;
    } while (read_seqcount_retry(&tk_core.seq, seq));
    return secs * NSEC_PER_SEC + nsecs;
}
```

### 3.3 混合读者（减少重试开销）

```c
unsigned seq = 0;
do {
    read_seqbegin_or_lock(&rename_lock, &seq);
    // 第一次：seq=0，无锁读取
    // 如果重试：seq 为奇数，加锁读取（保证成功）
    walk_tree();
} while (need_seqretry(&rename_lock, seq));
done_seqretry(&rename_lock, seq);
```

第一次尝试无锁读取。如果写者频繁导致重试，第二次自动升级为加锁读者——保证不再重试。

---

## §4 常见陷阱

### 4.1 忽略 read_seqretry 的返回值

**错误**：读取数据后不检查 `read_seqretry()`，直接使用可能不一致的数据。

**正确**：必须用 `do { ... } while (read_seqretry(...))` 循环，直到返回 0。

### 4.2 seqlock_t 写侧睡眠

**错误**：`write_seqlock` → 调用可能睡眠的函数 → spinlock 临界区内睡眠 → 死锁。

**正确**：`seqlock_t` 的写侧临界区内不能睡眠。需要跨睡眠操作的保护时，用 `seqcount_t` + 外部 mutex。

### 4.3 用 seqlock 保护指针

**错误**：受保护数据包含指针，写者在读者跟随指针时使其失效 → 读者访问已释放内存。

**正确**：指针保护用 RCU。seqlock 只保护值类型数据（整数、结构体字段的快照）。

### 4.4 seqcount_t 写者不互斥

**错误**：两个写者同时调用 `write_seqcount_begin` → 序列号混乱，读者永远无法获得一致快照。

**正确**：`seqcount_t` 不提供写者互斥。调用者必须自行保证（如已持有外部锁）。如果无法保证，用 `seqlock_t`。

### 4.5 写者过于频繁导致读者饥饿

**错误**：写者频繁修改数据，读者不断重试，永远无法完成读取。

**正确**：seqlock 适合读极多写极少的场景。如果写者频繁，考虑 rw_semaphore 或重新设计。`read_seqbegin_or_lock` 可以在重试后升级为加锁读者来缓解。

### 4.6 多字段读取缺少原子性

**错误**：读取 field1 和 field2 之间没有保证原子性——编译器可能重排读取顺序，或者 field1 来自旧版本、field2 来自新版本。

**正确**：使用 `READ_ONCE()` 读取每个字段，确保编译器不会缓存或重排。seqlock 的正确使用依赖 `READ_ONCE` 语义。

---

## §5 代码示例

### 5.1 网络子系统中的 seqlock

```c
// 保护 sysctl 参数
struct local_port_range {
    seqlock_t lock;
    int low;
    int high;
};

static struct local_port_range ip_local_ports = {
    .lock = __SEQLOCK_UNLOCKED(ip_local_ports.lock),
    .low = 32768,
    .high = 60999,
};

// 读者：获取端口范围
void get_local_port_range(int *low, int *high)
{
    unsigned seq;
    do {
        seq = read_seqbegin(&ip_local_ports.lock);
        *low = ip_local_ports.low;
        *high = ip_local_ports.high;
    } while (read_seqretry(&ip_local_ports.lock, seq));
}

// 写者：修改端口范围
int set_local_port_range(int low, int high)
{
    if (low >= high)
        return -EINVAL;
    write_seqlock(&ip_local_ports.lock);
    ip_local_ports.low = low;
    ip_local_ports.high = high;
    write_sequnlock(&ip_local_ports.lock);
    return 0;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| seqcount_t 定义 | `include/linux/seqlock.h:64-69` | 一等 |
| seqlock_t 定义 | `include/linux/seqlock.h:853-860` | 一等 |
| seqcount_LOCKNAME_t 宏生成 | `include/linux/seqlock.h:198-202` | 一等 |
| 写者 begin/end 实现 | `include/linux/seqlock.h:466-492` | 一等 |
| 读者 begin 自旋奇数 | `include/linux/seqlock.h:325-334` | 一等 |
| 读者 retry 检查 | `include/linux/seqlock.h:423-430` | 一等 |
| smp_wmb 在写者 begin/end | `include/linux/seqlock.h:470,489` | 一等 |
| 不能保护指针 | `include/linux/seqlock.h:51-52` 注释 | 一等 |
| seqcount_latch_t 定义 | `include/linux/seqlock.h:647-649` | 一等 |
| PREEMPT_RT 读者帮助写者 | `include/linux/seqlock.h:215-230` | 一等 |
| rename_lock 定义 | `fs/dcache.c:77` | 一等 |
| tk_core 使用 seqcount_raw_spinlock_t | `kernel/time/timekeeping.c:50-55` | 一等 |
| try_write_seqlock 不存在 | 全树 grep 确认 | 一等 |
| read_seqcount_or_lock 不存在 | 全树 grep 确认 | 一等 |
| 纯头文件实现，无 .c 文件 | 全树搜索确认 | 一等 |

### 使用规模

| 使用方式 | 涉及文件数 |
|---------|-----------|
| `read_seqbegin` / `read_seqcount_begin` | 140 |
| `write_seqlock` / `write_seqcount_begin` | 140 |
| `read_seqbegin_or_lock` 系列 | 21 |
