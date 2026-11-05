# rw_semaphore — 读写信号量

> 可睡眠的读写锁。读共享、写独占，适用于临界区较长或可能阻塞的场景。arm64 上使用原子 count 字段编码读者计数 + 写者状态 + 等待标志，快速路径只需一次原子操作。

---

## §1 核心设计洞察

### 1.1 count 字段的位域编码

`struct rw_semaphore` 的核心是 `atomic_long_t count`（`include/linux/rwsem.h:48-67`），单个原子字编码所有状态（`kernel/locking/rwsem.c:82-128`）：

```
 63    62                                    8 7  2  1  0
┌──────┬──────────────────────────────────────┬───┬──┬──┬──┐
│READ  │       读者计数 (55 bit)               │ H │ W │ F │WR│
│FAIL  │   每个读者加 RWSEM_READER_BIAS(256)   │ A │ A │ L │I │
│      │                                      │ N │ I │ A │T │
│      │                                      │ D │ T │ G │E │
│      │                                      │ O │ E │ E │R │
│      │                                      │ F │ R │ O │  │
│      │                                      │ F │ S │ F │  │
└──────┴──────────────────────────────────────┴───┴──┴──┴──┘
```

64 位系统（`kernel/locking/rwsem.c:117-128`）：

| 位 | 名称 | 值 | 含义 |
|----|------|----|------|
| 0 | `RWSEM_WRITER_LOCKED` | `1UL << 0` | 写者持有 |
| 1 | `RWSEM_FLAG_WAITERS` | `1UL << 1` | 等待队列中有等待者 |
| 2 | `RWSEM_FLAG_HANDOFF` | `1UL << 2` | 锁正在移交中 |
| 8-62 | 读者计数 | `RWSEM_READER_BIAS = 256` | 每个读者加 256 |
| 63 | `RWSEM_FLAG_READFAIL` | `1UL << 63` | 溢出保护位 |

32 位系统布局相同，读者计数只有 23 位（bits 8-30）。

**关键推论**：
- **未锁**：count == 0
- **纯读者**：count > 0 且 bit 0 == 0（读者计数在高位）
- **写者持有**：bit 0 == 1
- 读者获取 = 一次 `atomic_add(256)`，释放 = 一次 `atomic_sub(256)`——不需要碰任何 spinlock

### 1.2 struct rw_semaphore 完整布局

```c
struct rw_semaphore {                               // include/linux/rwsem.h:48-67
    atomic_long_t count;          // 读者计数 + 写者位 + 标志位
    atomic_long_t owner;          // 写者指针 + READER_OWNED/NONSPINNABLE 标志
    struct optimistic_spin_queue osq;  // 写者乐观自旋 MCS 队列
    raw_spinlock_t wait_lock;     // 保护 wait_list
    struct list_head wait_list;   // FIFO 等待队列
    // CONFIG_DEBUG_RWSEMS: void *magic
    // CONFIG_DEBUG_LOCK_ALLOC: struct lockdep_map dep_map
};
```

owner 字段的低 2 位（`kernel/locking/rwsem.c:63-65`）：
- `RWSEM_READER_OWNED (0x01)`：锁由读者持有
- `RWSEM_NONSPINNABLE (0x02)`：禁止乐观自旋

### 1.3 快速路径

**读者获取**（`kernel/locking/rwsem.c:1254-1270`）：

```c
// rwsem_read_trylock() — rwsem.c:241-254
static inline bool rwsem_read_trylock(struct rw_semaphore *sem)
{
    atomic_long_t tmp = atomic_long_add_return_acquire(RWSEM_READER_BIAS, &sem->count);
    // 如果结果中没有任何失败位（写者锁/等待者/移交/溢出），成功
    if (likely(!(tmp & RWSEM_READ_FAILED_MASK)))
        return true;
    // 失败：回退减法
    atomic_long_add(-RWSEM_READER_BIAS, &sem->count);
    return false;
}
```

一次原子加法。无竞争时，读者获取锁的全部代价就是一条原子指令。

**写者获取**（`kernel/locking/rwsem.c:1311-1319`）：

```c
// rwsem_write_trylock() — rwsem.c:256-269
static inline bool rwsem_write_trylock(struct rw_semaphore *sem)
{
    unsigned long val = 0;
    if (!atomic_long_try_cmpxchg_acquire(&sem->count, &val, RWSEM_WRITER_LOCKED))
        return false;
    return true;
}
```

一次 `cmpxchg`，从 0 变为 `RWSEM_WRITER_LOCKED`。

**读者释放**（`kernel/locking/rwsem.c:1340-1357`）：

```c
static inline void __up_read(struct rw_semaphore *sem)
{
    atomic_long_t tmp = atomic_long_add_return_release(-RWSEM_READER_BIAS, &sem->count);
    // 如果有等待者且没有其他读者/写者 → 唤醒
    if (unlikely(tmp & RWSEM_WAKE_MASK))
        rwsem_wake(sem, tmp);
}
```

**写者释放**（`kernel/locking/rwsem.c:1362-1380`）：

```c
static inline void __up_write(struct rw_semaphore *sem)
{
    atomic_long_t tmp = atomic_long_fetch_add_release(-RWSEM_WRITER_LOCKED, &sem->count);
    if (unlikely(tmp & RWSEM_FLAG_WAITERS))
        rwsem_wake(sem, tmp);
}
```

### 1.4 慢路径与公平性

**读者慢路径**（`kernel/locking/rwsem.c:1003-1109`）：
1. 获取 `wait_lock`，加入 `wait_list`
2. 尝试"锁偷取"——如果没有写者且没有 HANDOFF，可以直接获取
3. 否则进入睡眠循环

**写者慢路径**（`kernel/locking/rwsem.c:1114-1208`）：
1. 先尝试 OSQ 乐观自旋（`rwsem_optimistic_spin()`，`kernel/locking/rwsem.c:824-943`）
2. 自旋失败：获取 `wait_lock`，加入 `wait_list`，设置 WAITERS 标志
3. 进入睡眠循环

**写者饥饿防护**（`kernel/locking/rwsem.c:108-116`）：

当 `RWSEM_FLAG_WAITERS` 被设置后，新的 `down_read()` 快速路径会失败（`RWSEM_READ_FAILED_MASK` 包含 WAITERS 位），新读者被强制进入慢路径排队——排在写者后面。这防止了写者被无限期饿死。

**移交协议**：等待超过 `RWSEM_WAIT_TIMEOUT`（约 4ms，`kernel/locking/rwsem.c:362`）的等待者设置 `RWSEM_FLAG_HANDOFF`。一旦设置，只有该等待者能获取锁——其他任务（包括新到达者）被阻止。RT 任务可以立即设置移交位（`kernel/locking/rwsem.c:637-638`）。

### 1.5 乐观自旋 (OSQ)

写者在进入等待队列前，先在 OSQ 上自旋（`kernel/locking/rwsem.c:824-943`）：

- 使用 per-rwsem 的 MCS 队列，保证只有一个写者自旋
- 自旋条件：owner 在 CPU 上运行、不需要调度
- **读者持有时的自旋**：写者对读者持有也做有限自旋，时间上限 = `(10 + nr_readers/2) us`，最大 25μs（`kernel/locking/rwsem.c:811-822`）

### 1.6 PREEMPT_RT 下的行为变化

在 `CONFIG_PREEMPT_RT` 下（`include/linux/rwsem.h:129-134`），rw_semaphore 的结构完全不同：

```c
struct rw_semaphore {
    struct rwbase_rt rwbase;    // 包含 atomic_t readers + rt_mutex_base
    struct lockdep_map dep_map;
};
```

`struct rwbase_rt`（`include/linux/rwbase_rt.h:11-14`）：

```c
struct rwbase_rt {
    atomic_t            readers;   // 基于 READER_BIAS 的计数器
    struct rt_mutex_base rtmutex;  // 底层 RT 互斥锁
};
```

| 特性 | 非 RT | PREEMPT_RT |
|------|-------|------------|
| 写者等待 | OSQ 自旋 + FIFO 队列 | 直接睡眠在 rt_mutex 上 |
| 读者快速路径 | 原子加法 | 原子加法（不变） |
| 读者慢路径 | 等待队列睡眠 | 阻塞在 rt_mutex 上 |
| 优先级继承 | 无 | **有**——通过 rt_mutex |
| 写者公平性 | 有（WAITERS 位 + HANDOFF） | **无**——受 rt_mutex 优先级/截止期继承约束 |

### 1.7 rw_semaphore vs rwlock

| 维度 | rw_semaphore | rwlock |
|------|-------------|--------|
| 睡眠 | **可睡眠**——任务阻塞在等待队列 | 不可睡眠——自旋等待 |
| 上下文 | 仅进程上下文（`might_sleep()`） | 任何上下文（包括中断） |
| 临界区长度 | 适合较长临界区 | 适合极短临界区 |
| 乐观自旋 | OSQ（非 RT） | 纯自旋 |
| 典型用户 | `mmap_lock`、`sb->s_umount` | 中断 handler 中的短数据保护 |

**选择原则**：临界区可能超过几微秒、或需要在中断上下文之外使用 → rw_semaphore。临界区只有几条指令、或需要在中断 handler 中使用 → rwlock。

---

## §2 API 速查

### 2.1 初始化

头文件：`include/linux/rwsem.h`；实现：`kernel/locking/rwsem.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DECLARE_RWSEM(name)` | 静态声明并初始化 | 编译期初始化 |
| `init_rwsem(sem)` | 动态初始化 | 宏，展开调用 `__init_rwsem`；带 lockdep key |
| `__init_rwsem(sem, name, key)` | 底层初始化 | 通常不直接调用；`EXPORT_SYMBOL` |

### 2.2 读锁操作

| API | 说明 | 备注 |
|-----|------|------|
| `down_read(sem)` | 获取读锁（可睡眠等待） | 共享——多个读者可同时持有 |
| `up_read(sem)` | 释放读锁 | |
| `down_read_trylock(sem)` | 尝试获取读锁 | 返回 1=成功，0=失败 |
| `down_read_interruptible(sem)` | 获取读锁（可被信号中断） | 返回 0 或 -EINTR |
| `down_read_killable(sem)` | 获取读锁（仅致命信号可中断） | 返回 0 或 -EINTR |

### 2.3 写锁操作

| API | 说明 | 备注 |
|-----|------|------|
| `down_write(sem)` | 获取写锁（可睡眠等待） | 独占——阻塞所有读者和其他写者 |
| `up_write(sem)` | 释放写锁 | |
| `down_write_trylock(sem)` | 尝试获取写锁 | 返回 1=成功，0=失败 |
| `down_write_killable(sem)` | 获取写锁（仅致命信号可中断） | 返回 0 或 -EINTR |
| `downgrade_write(sem)` | 将写锁降级为读锁 | 不释放锁，避免竞争窗口 |

### 2.4 嵌套锁（lockdep 注解）

| API | 说明 | 备注 |
|-----|------|------|
| `down_read_nested(sem, subclass)` | 获取读锁 + 标注类别 | |
| `down_read_killable_nested(sem, subclass)` | killable + nested | |
| `_down_write_nest_lock(sem, nest_lock)` | 获取写锁 + 标注外层锁 | |
| `down_write_nested(sem, subclass)` | 获取写锁 + 标注类别 | |

### 2.5 非持有者操作

| API | 说明 | 备注 |
|-----|------|------|
| `down_read_non_owner(sem)` | 获取读锁但不记录持有者 | 用于特殊场景（如 NFS） |
| `up_read_non_owner(sem)` | 释放非持有者读锁 | |

### 2.6 Scope guards

```c
DEFINE_GUARD(rwsem_read, struct rw_semaphore *, down_read(_T), up_read(_T))
DEFINE_GUARD(rwsem_write, struct rw_semaphore *, down_write(_T), up_write(_T))
DEFINE_FREE(up_read, struct rw_semaphore *, if (_T) up_read(_T))
DEFINE_FREE(up_write, struct rw_semaphore *, if (_T) up_write(_T))
```

支持 `guard(rwsem_read)(&sem)` 和 `guard(rwsem_write)(&sem)` 自动解锁（`include/linux/rwsem.h:205-209`）。

---

## §3 使用模式

### 3.1 基本读写保护

```c
static DECLARE_RWSEM(config_rwsem);
static struct config_data config;

// 读者：多个线程可同时读取
void read_config(struct config_data *out)
{
    down_read(&config_rwsem);
    *out = config;          // 拷贝快照
    up_read(&config_rwsem);
}

// 写者：独占访问
void update_config(int new_val)
{
    down_write(&config_rwsem);
    config.value = new_val;
    config.seq++;
    up_write(&config_rwsem);
}
```

### 3.2 写锁降级

```c
down_write(&sem);
// 修改数据结构
data->new_field = compute();

// 修改完成，后续只需要读取——降级为读锁
downgrade_write(&sem);
// 现在其他读者可以进来了
read_and_report(data);
up_read(&sem);
```

`downgrade_write` 避免了"释放写锁→重新获取读锁"之间的竞争窗口。

### 3.3 可中断读锁（用户态 syscall）

```c
long my_read(struct file *file, char __user *buf, size_t count, loff_t *ppos)
{
    struct my_dev *dev = file->private_data;

    if (down_read_interruptible(&dev->rwsem))
        return -ERESTARTSYS;

    ret = copy_to_user(buf, dev->data, count);
    up_read(&dev->rwsem);
    return ret ? -EFAULT : count;
}
```

---

## §4 常见陷阱

### 4.1 在中断 handler 中使用 rw_semaphore

**错误**：中断 handler 调用 `down_read` → `might_sleep()` 触发 WARN → 如果锁被占用 → kernel panic。

**正确**：中断 handler 中用 `read_lock` / `write_lock`（rwlock 变体）。rw_semaphore 只能在进程上下文使用。

### 4.2 读锁持有期间修改数据

**错误**：`down_read` → 修改共享数据 → 其他读者也读到不一致的状态。

**正确**：读锁只用于读取。修改数据必须用 `down_write`。

### 4.3 递归获取写锁

**错误**：同一线程 `down_write` 两次 → 死锁。

**正确**：rw_semaphore 不可递归。需要递归读锁是安全的（多个读者可同时持有），但递归写锁不行。

### 4.4 忘记处理 down_read_interruptible 的返回值

**错误**：`down_read_interruptible` 返回 -EINTR 时继续访问数据——此时没有持有锁。

**正确**：始终检查返回值，非零时走错误路径。

### 4.5 PREEMPT_RT 下 rw_semaphore 不再保证写者公平

非 RT 下有 WAITERS 位 + HANDOFF 协议防止写者饥饿。PREEMPT_RT 下这些机制被绕过，写者可能因 rt_mutex 的优先级继承而被延迟。依赖"写者最终一定能获取"的代码在 RT 上可能表现不同。

---

## §5 代码示例

### 5.1 设备驱动中的 rw_semaphore

```c
struct my_device {
    struct rw_semaphore rwsem;
    u32 *data_buffer;
    size_t buffer_size;
    bool reconfiguring;
};

static int my_probe(struct platform_device *pdev)
{
    struct my_device *dev = devm_kzalloc(&pdev->dev, sizeof(*dev), GFP_KERNEL);
    if (!dev)
        return -ENOMEM;

    init_rwsem(&dev->rwsem);
    // ...
    return 0;
}

// 多读者并发读取设备数据
ssize_t my_read(struct file *file, char __user *buf, size_t count, loff_t *ppos)
{
    struct my_device *dev = file->private_data;

    if (down_read_killable(&dev->rwsem))
        return -ERESTARTSYS;

    ret = copy_to_user(buf, dev->data_buffer, min(count, dev->buffer_size));
    up_read(&dev->rwsem);
    return ret ? -EFAULT : count;
}

// 独占写：重新配置设备
int my_reconfigure(struct my_device *dev, u32 new_config)
{
    down_write(&dev->rwsem);
    dev->reconfiguring = true;
    // 可能睡眠的操作（如等待硬件确认）
    ret = wait_for_hw_ready(dev);
    if (ret == 0)
        apply_config(dev, new_config);
    dev->reconfiguring = false;
    up_write(&dev->rwsem);
    return ret;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| count 字段位域编码 | `kernel/locking/rwsem.c:82-128` | 一等 |
| struct rw_semaphore 布局（非 RT） | `include/linux/rwsem.h:48-67` | 一等 |
| 读者快速路径 = 一次 atomic_add | `kernel/locking/rwsem.c:241-254` | 一等 |
| 写者快速路径 = 一次 cmpxchg | `kernel/locking/rwsem.c:256-269` | 一等 |
| WAITERS 位阻止新读者快速路径 | `kernel/locking/rwsem.c:127-128`（`RWSEM_READ_FAILED_MASK`） | 一等 |
| HANDOFF 超时 ~4ms | `kernel/locking/rwsem.c:362`（`DIV_ROUND_UP(HZ, 250)`） | 一等 |
| OSQ 读者持有自旋上限 25μs | `kernel/locking/rwsem.c:811-822` | 一等 |
| PREEMPT_RT 下 struct rw_semaphore 包装 rwbase_rt | `include/linux/rwsem.h:129-134` | 一等 |
| struct rwbase_rt 布局 | `include/linux/rwbase_rt.h:11-14` | 一等 |
| PREEMPT_RT 不保证写者公平 | `kernel/locking/rwsem.c:28-42` 注释 | 一等 |
| downgrade_write 存在 | `kernel/locking/rwsem.c:1633-1637` | 一等 |
| upgrade_read 不存在 | 全树 grep 确认 | 一等 |
| scope guard 定义 | `include/linux/rwsem.h:205-209` | 一等 |
| arm64 无架构特定 rwsem 代码 | `arch/arm64/` 下无 rwsem 相关文件 | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `down_read(` | 1,037 |
| `down_write(` | 1,222 |
| `up_read(` | 1,417 |
| `up_write(` | 1,575 |
| `down_read_trylock(` | 103 |
