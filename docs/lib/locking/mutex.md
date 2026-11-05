# mutex — 可睡眠互斥锁

> 内核最基本的可睡眠互斥原语。临界区较长或可能阻塞时替代 spinlock。arm64 上使用 MCS 队列自旋锁 + 乐观自旋 + FIFO 等待队列的三层获取策略，并通过 owner 字段的低位标志实现无锁快速路径。

---

## §1 核心设计洞察

### 1.1 owner 字段的双重身份

mutex 的核心不是锁本身，而是 `owner` 字段——一个 `atomic_long_t`，同时编码持有者指针和三个状态位（`kernel/locking/mutex.c:59-72`）：

```
 63                          3 2 1 0
┌─────────────────────────────┬───┬───┬───┐
│  task_struct * (持有者指针)  │ P │ H │ W │
│  (对齐，低 3 位始终为 0)     │ U │ A │ A │
│                             │ C │ N │ I │
│                             │ K │ D │ T │
│                             │ E │ O │ E │
│                             │ R │ F │ R │
│                             │ S │ F │ S │
└─────────────────────────────┴───┴───┴───┘
```

- **W (WAITERS, 0x01)**：等待队列非空
- **H (HANDOFF, 0x02)**：正在将锁所有权移交给队首等待者
- **P (PICKUP, 0x04)**：handoff 完成，等待队首来"拾取"

三个状态位让 mutex 在 **无锁** 时只需一次 `cmpxchg` 就能完成获取——不需要先拿 `wait_lock` 再检查状态。

### 1.2 三种状态

| 状态 | owner 值 | 含义 |
|------|---------|------|
| 未锁 | `0` | 无人持有 |
| 已锁，无等待者 | `task_ptr`（低 3 位 = 0） | 持有者明确，无竞争 |
| 已锁，有等待者 | `task_ptr \| 0x01` | WAITERS 位置位，解锁走慢路径 |

### 1.3 快速路径：单次 cmpxchg

获取锁（`kernel/locking/mutex.c:166-175`）：

```c
static __always_inline bool __mutex_trylock_fast(struct mutex *lock)
{
    unsigned long curr = (unsigned long)current;
    unsigned long zero = 0UL;
    if (atomic_long_try_cmpxchg_acquire(&lock->owner, &zero, curr))
        return true;
    return false;
}
```

释放锁（`kernel/locking/mutex.c:177-182`）：

```c
static __always_inline bool __mutex_unlock_fast(struct mutex *lock)
{
    unsigned long curr = (unsigned long)current;
    return atomic_long_try_cmpxchg_release(&lock->owner, &curr, 0UL);
}
```

**快速路径成功的条件**：owner == 0（无竞争）。一旦有等待者（WAITERS 位被设置），owner 低 3 位非零，`cmpxchg` 必然失败，进入慢路径。

### 1.4 慢路径：三阶段获取

`mutex_lock` 的完整流程（`kernel/locking/mutex.c:281-288`）：

```c
void __sched mutex_lock(struct mutex *lock)
{
    might_sleep();
    if (!__mutex_trylock_fast(lock))
        __mutex_lock_slowpath(lock);
}
```

慢路径 `__mutex_lock_common`（`kernel/locking/mutex.c:568-741`）分三阶段：

```
mutex_lock()
    │
    ├─ 快速路径: cmpxchg(owner, 0, current)
    │   成功 → 返回
    │
    └─ 慢路径 __mutex_lock_common()
        │
        ├─ 阶段 1: 乐观自旋 (OSQ)
        │   在 MCS 队列上自旋等待持有者释放
        │   条件: CONFIG_MUTEX_SPIN_ON_OWNER
        │         持有者正在运行、未阻塞
        │   成功 → 获取锁，返回
        │
        ├─ 阶段 2: 加入等待队列
        │   获取 wait_lock
        │   将 mutex_waiter 加入 wait_list 尾部 (FIFO)
        │   设置 WAITERS 位
        │
        └─ 阶段 3: 睡眠等待循环
            for (;;) {
                设置 TASK_UNINTERRUPTIBLE
                schedule()  ← 让出 CPU
                被唤醒后重试:
                  尝试获取锁
                  如果 HANDOFF 位被设置 → 队首直接接管
                  否则重新自旋 / 重新睡眠
            }
```

### 1.5 解锁路径

`mutex_unlock`（`kernel/locking/mutex.c:537-545`）：

```c
void __sched mutex_unlock(struct mutex *lock)
{
#ifndef CONFIG_DEBUG_LOCK_ALLOC
    if (__mutex_unlock_fast(lock))
        return;
#endif
    __mutex_unlock_slowpath(lock, _RET_IP_);
}
```

**快速路径**：owner == current（无等待者），`cmpxchg` 清零即可。

**慢路径**（`kernel/locking/mutex.c:901-952`）：
1. 尝试快速释放；如果 WAITERS 位已设置则失败
2. 获取 `wait_lock`，找到等待队列的第一个 waiter
3. 如果 HANDOFF 位被设置，调用 `__mutex_handoff()` 直接将所有权转给队首
4. 否则清除 owner，将 waiter 加入 `wake_q`
5. 释放 `wait_lock`，调用 `wake_up_q()` 批量唤醒

### 1.6 乐观自旋队列 (OSQ)

`struct optimistic_spin_queue`（`kernel/locking/osq_lock.c`）是一个 per-mutex 的 MCS 队列，用于在持有者释放锁之前短暂自旋等待——避免上下文切换的开销。

**关键约束**：
- 只在持有者正在 CPU 上运行时自旋（`vcpu_is_preempted()` 检查）
- arm64 上 `vcpu_is_preempted()` 始终返回 false（`arch/arm64/include/asm/spinlock.h:22-25`），所以 OSQ 在 arm64 虚拟机中可能做无用自旋
- 自旋超时后加入 FIFO 等待队列，保证公平性

### 1.7 struct mutex 布局

```c
struct mutex {                                    // include/linux/mutex.h:66-79
    atomic_long_t       owner;      // 持有者指针 + 3 个标志位
    raw_spinlock_t      wait_lock;  // 保护 wait_list
    struct optimistic_spin_queue osq;  // 乐观自旋 MCS 队列
    struct list_head    wait_list;  // FIFO 等待队列
    // CONFIG_DEBUG_MUTEXES: void *magic
    // CONFIG_DEBUG_LOCK_ALLOC: struct lockdep_map dep_map
};
```

等待者节点（`kernel/locking/mutex.h:14-21`）：

```c
struct mutex_waiter {
    struct list_head    list;       // 链入 mutex.wait_list
    struct task_struct  *task;      // 等待的任务
    struct ww_acquire_ctx *ww_ctx;  // ww_mutex 上下文（普通 mutex 为 NULL）
};
```

### 1.8 PREEMPT_RT 下的行为变化

在 `CONFIG_PREEMPT_RT` 下（`include/linux/mutex.h:138-143`），mutex 的结构完全不同：

```c
struct mutex {
    struct rt_mutex_base    rtmutex;   // 包装 rt_mutex
    struct lockdep_map      dep_map;
};
```

`struct rt_mutex_base`（`include/linux/rtmutex.h:23-27`）使用 rbtree 等待队列（按优先级排序）和 **优先级继承**（PI）协议：

```c
struct rt_mutex_base {
    raw_spinlock_t          wait_lock;
    struct rb_root_cached   waiters;    // 按优先级排序的红黑树
    struct task_struct      *owner;
};
```

| 特性 | 非 RT | PREEMPT_RT |
|------|-------|------------|
| 等待队列 | FIFO list | 优先级排序 rbtree |
| 优先级继承 | 无 | **有**——低优先级持有者临时提升 |
| 乐观自旋 | OSQ + owner 自旋 | 无（直接睡眠） |
| 快速路径 | 单次 cmpxchg | 无（始终走 rt_mutex 路径） |
| 饥饿保护 | 无（FIFO 保证公平） | PI 防止优先级反转 |

### 1.9 WW-Mutex：任意锁序的死锁避免

`ww_mutex`（wait-wound mutex，`include/linux/ww_mutex.h`）允许以任意顺序获取一组互斥锁而不死锁。核心思想来自数据库事务：

**两种算法**（`kernel/locking/ww_mutex.h:164-174`）：

| 算法 | 定义宏 | 行为 |
|------|--------|------|
| Wait-Die | `DEFINE_WD_CLASS` | 新事务请求旧事务持有的锁 → **新事务被杀死**（返回 -EDEADLK） |
| Wound-Wait | `DEFINE_WW_CLASS` | 老事务请求新事务持有的锁 → **新事务被伤害**（wounded），老事务等待 |

**使用模式**：
1. 定义 `struct ww_class`（全局，描述锁类别）
2. 每个"事务"创建 `struct ww_acquire_ctx`（带时间戳）
3. 获取锁时传入 ctx——如果检测到死锁，返回 -EDEADLK
4. 调用者释放所有锁，调用 `ww_mutex_lock_slow()` 重新获取失败的锁

典型用户：GPU 驱动（`drivers/gpu/drm/`）的 buffer object 验证——需要按任意顺序锁多个 BO。

---

## §2 API 速查

### 2.1 初始化

头文件：`include/linux/mutex.h`；实现：`kernel/locking/mutex.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_MUTEX(name)` | 静态定义并初始化 mutex | 编译期初始化 |
| `mutex_init(mutex)` | 动态初始化 mutex | 带 lockdep class key；宏，展开调用 `__mutex_init` |
| `__mutex_init(lock, name, key)` | 底层初始化 | 通常不直接调用；`EXPORT_SYMBOL` |

### 2.2 基本锁操作

| API | 说明 | 备注 |
|-----|------|------|
| `mutex_lock(lock)` | 获取锁（可睡眠等待） | 调用 `might_sleep()`；不可被信号中断 |
| `mutex_unlock(lock)` | 释放锁 | 必须由持有者调用 |
| `mutex_trylock(lock)` | 尝试获取锁 | 返回 1=成功，0=失败；不自旋不睡眠 |
| `mutex_lock_interruptible(lock)` | 获取锁（可被信号中断） | 返回 0=成功，-EINTR=被信号中断 |
| `mutex_lock_killable(lock)` | 获取锁（仅可被致命信号中断） | 返回 0=成功，-EINTR=被致命信号中断 |
| `mutex_lock_io(lock)` | 获取锁（标记为 IO 等待） | 内部调用 `io_schedule_prepare/finish`；`EXPORT_SYMBOL_GPL` |
| `mutex_is_locked(lock)` | 检查锁是否被持有 | **不提供内存序保证**；仅调试用 |

### 2.3 嵌套锁（lockdep 注解）

| API | 说明 | 备注 |
|-----|------|------|
| `mutex_lock_nested(lock, subclass)` | 获取锁并标注类别 | 同一把锁在不同上下文获取时用 |
| `_mutex_lock_nest_lock(lock, nest_lock)` | 获取锁并标注外层锁 | 证明持锁顺序安全 |
| `mutex_lock_interruptible_nested(lock, subclass)` | interruptible + nested | |
| `mutex_lock_killable_nested(lock, subclass)` | killable + nested | |
| `mutex_lock_io_nested(lock, subclass)` | IO + nested | |

无 `CONFIG_DEBUG_LOCK_ALLOC` 时，`mutex_lock` 等宏直接展开为不带 subclass 的版本。

### 2.4 WW-Mutex

头文件：`include/linux/ww_mutex.h`

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_WD_CLASS(name)` | 定义 Wait-Die 类 | `is_wait_die = 1` |
| `DEFINE_WW_CLASS(name)` | 定义 Wound-Wait 类 | `is_wait_die = 0` |
| `ww_mutex_init(lock, class)` | 初始化 ww_mutex | inline 函数 |
| `ww_acquire_init(ctx, class)` | 初始化获取上下文 | 分配时间戳 |
| `ww_acquire_done(ctx)` | 标记不再获取新锁 | 调试用 |
| `ww_acquire_fini(ctx)` | 结束获取上下文 | |
| `ww_mutex_lock(lock, ctx)` | 获取 ww 锁 | 可能返回 -EDEADLK |
| `ww_mutex_lock_interruptible(lock, ctx)` | 可中断获取 | 可能返回 -EDEADLK 或 -EINTR |
| `ww_mutex_lock_slow(lock, ctx)` | 慢路径重新获取 | 收到 -EDEADLK 后调用 |
| `ww_mutex_unlock(lock)` | 释放 ww 锁 | |
| `ww_mutex_trylock(lock, ctx)` | 尝试获取 | 返回 1=成功，0=失败 |
| `ww_mutex_destroy(lock)` | 销毁（调试） | |

### 2.5 辅助函数

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_dec_and_mutex_lock(cnt, lock)` | 原子减 1，到 0 时加锁 | `EXPORT_SYMBOL`；用于引用计数清理 |

### 2.6 Scope guards

```c
DEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))
DEFINE_FREE(mutex, struct mutex *, if (_T) mutex_unlock(_T))
```

支持 `guard(mutex)(&lock)` 和 `CLASS(mutex, name)(&lock)` 自动解锁语法（`include/linux/mutex.h:250-251`）。

---

## §3 使用模式

### 3.1 基本互斥保护

```c
static DEFINE_MUTEX(state_lock);
static struct my_state global_state;

int update_state(int new_val)
{
    mutex_lock(&state_lock);
    global_state.value = new_val;
    global_state.seq++;
    mutex_unlock(&state_lock);
    return 0;
}
```

与 spinlock 的关键区别：临界区内**可以睡眠**——可以调用 `kmalloc(GFP_KERNEL)`、`mutex_lock` 另一把锁、`wait_event` 等。

### 3.2 可中断等待（用户态 syscall）

```c
long my_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    struct my_device *dev = file->private_data;

    if (mutex_lock_interruptible(&dev->lock))
        return -ERESTARTSYS;   // 被信号中断，返回给用户态

    // 临界区
    ret = do_ioctl(dev, cmd, arg);
    mutex_unlock(&dev->lock);
    return ret;
}
```

`mutex_lock_interruptible` 是用户态 syscall 路径的标准选择——允许 `Ctrl+C` 终止等待。

### 3.3 WW-Mutex 使用模式

```c
// 定义锁类别（全局）
static DEFINE_WW_CLASS(bo_ww_class, "bo_lock");

int validate_and_lock_bo_list(struct buffer_object **bos, int count)
{
    struct ww_acquire_ctx ctx;
    struct buffer_object *contended = NULL;
    int i, ret;

    ww_acquire_init(&ctx, &bo_ww_class);

retry:
    for (i = 0; i < count; i++) {
        if (bos[i] == contended)
            continue;  // 跳过已处理的

        ret = ww_mutex_lock(&bos[i]->lock, &ctx);
        if (ret == -EDEADLK) {
            // 死锁检测：释放所有锁，重新获取失败的锁
            contended = bos[i];
            // 释放之前获取的所有锁...
            ww_mutex_lock_slow(&contended->lock, &ctx);
            goto retry;
        }
    }

    ww_acquire_done(&ctx);
    // 所有锁已获取，执行操作...
    // 解锁...
    ww_acquire_fini(&ctx);
    return 0;
}
```

### 3.4 trylock 避免阻塞

```c
if (!mutex_trylock(&dev->lock)) {
    // 锁被占用，不等待
    schedule_work(&deferred);
    return -EAGAIN;
}
// 成功获取
do_work();
mutex_unlock(&dev->lock);
```

---

## §4 常见陷阱

### 4.1 在中断 handler 中使用 mutex

**错误**：中断 handler 调用 `mutex_lock` → `might_sleep()` 触发 WARN → 如果锁被占用，调度器在中断上下文中尝试调度 → kernel panic。

**正确**：中断 handler 中只用 spinlock。mutex 只能在进程上下文使用。

### 4.2 mutex 递归加锁

**错误**：同一线程对同一 mutex 调用两次 `mutex_lock` → 死锁（自己等自己释放）。

**正确**：mutex 不可递归。需要递归锁时用 `rw_semaphore`（读锁可递归）或重新设计。

### 4.3 PREEMPT_RT 下 mutex 获得优先级继承

在 PREEMPT_RT 下，mutex 使用 rt_mutex 实现，带优先级继承。如果低优先级线程持有 mutex，高优先级线程等待时，低优先级线程临时提升到高优先级——防止优先级反转。

**影响**：持锁期间不要做与锁无关的长时间操作，因为这会阻塞所有等待者（包括高优先级任务）。

### 4.4 mutex_is_locked 不是同步原语

`mutex_is_locked` 只检查 owner 是否非零，不提供任何内存序保证。返回值在返回瞬间就可能过时。只用于调试断言，不用于控制流。

### 4.5 WW-Mutex 的 -EDEADLK 处理

**错误**：收到 `-EDEADLK` 后直接返回错误，不做重试。这会导致操作未完成就放弃。

**正确**：收到 `-EDEADLK` 后必须释放所有已获取的锁，调用 `ww_mutex_lock_slow` 重新获取失败的锁（此时会等待），然后从头重试整个获取序列。

### 4.6 mutex_lock 的返回值

`mutex_lock` 返回 `void`——它**总是**成功获取锁（不可中断）。需要可中断版本时用 `mutex_lock_interruptible` 或 `mutex_lock_killable`，并检查返回值。

---

## §5 代码示例

### 5.1 设备驱动中的 mutex 用法

```c
struct my_device {
    struct mutex lock;
    u32 config_reg;
    struct list_head requests;
    bool suspended;
};

static int my_probe(struct platform_device *pdev)
{
    struct my_device *dev = devm_kzalloc(&pdev->dev, sizeof(*dev), GFP_KERNEL);
    if (!dev)
        return -ENOMEM;

    mutex_init(&dev->lock);
    INIT_LIST_HEAD(&dev->requests);
    // ...
    return 0;
}

// 用户态 read：可中断等待
static ssize_t my_read(struct file *file, char __user *buf,
                       size_t count, loff_t *ppos)
{
    struct my_device *dev = file->private_data;

    if (mutex_lock_interruptible(&dev->lock))
        return -ERESTARTSYS;

    // 临界区内可以安全调用可能睡眠的函数
    ret = copy_to_user(buf, &dev->config_reg, sizeof(u32));

    mutex_unlock(&dev->lock);
    return ret ? -EFAULT : sizeof(u32);
}

// _suspend 路径：用 killable 避免阻止系统关机
int my_suspend(struct device *dev)
{
    struct my_device *mdev = dev_get_drvdata(dev);

    if (mutex_lock_killable(&mdev->lock))
        return -EAGAIN;

    mdev->suspended = true;
    mutex_unlock(&mdev->lock);
    return 0;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| owner 字段编码持有者指针 + 3 标志位 | `kernel/locking/mutex.c:59-72` | 一等 |
| 三种状态（未锁/已锁无等待/已锁有等待） | `kernel/locking/mutex.c:59-67` 注释 | 一等 |
| 快速路径 cmpxchg | `kernel/locking/mutex.c:166-182` | 一等 |
| `mutex_lock` = 快速路径 + 慢路径 | `kernel/locking/mutex.c:281-288` | 一等 |
| `mutex_unlock` = 快速路径 + 慢路径 | `kernel/locking/mutex.c:537-545` | 一等 |
| 慢路径三阶段（OSQ → 加入队列 → 睡眠） | `kernel/locking/mutex.c:568-741` | 一等 |
| MUTEX_FLAG_WAITERS/HANDOFF/PICKUP 定义 | `kernel/locking/mutex.c:68-70` | 一等 |
| struct mutex 布局（非 RT） | `include/linux/mutex.h:66-79` | 一等 |
| PREEMPT_RT 下 struct mutex 包装 rt_mutex_base | `include/linux/mutex.h:138-143` | 一等 |
| struct rt_mutex_base 使用 rbtree + PI | `include/linux/rtmutex.h:23-27` | 一等 |
| ww_mutex Wait-Die vs Wound-Wait | `kernel/locking/ww_mutex.h:164-174` | 一等 |
| arm64 `vcpu_is_preempted` 始终返回 false | `arch/arm64/include/asm/spinlock.h:22-25` | 一等 |
| `mutex_waiter` 结构 | `kernel/locking/mutex.h:14-21` | 一等 |
| `__mutex_init` EXPORT_SYMBOL | `kernel/locking/mutex.c:57` | 一等 |
| `mutex_lock_io` EXPORT_SYMBOL_GPL | `kernel/locking/mutex.c:1030` | 一等 |
| scope guard 定义 | `include/linux/mutex.h:250-251` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `mutex_lock(` | 23,072 |
| `mutex_unlock(` | 29,169 |
| `mutex_lock_interruptible(` | 874 |
| `mutex_trylock(` | 306 |

`mutex_unlock` 调用数高于 `mutex_lock` 是正常的——错误路径和提前退出会导致单个 lock 对应多个 unlock 点。
