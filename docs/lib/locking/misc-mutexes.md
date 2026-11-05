# 互斥锁杂项 — rt_mutex / ww_mutex / semaphore

> 三种不同形态的互斥/计数锁。rt_mutex 提供优先级继承的互斥锁（PREEMPT_RT 的基础）；ww_mutex 允许任意顺序获取多把锁而不死锁；semaphore 是计数信号量，允许多个并发持有者。

---

## rt_mutex

### 定位

**功能**：带优先级继承（PI）的互斥锁，防止优先级反转问题。当高优先级任务等待低优先级任务持有的锁时，低优先级持有者临时提升到最高等待者的优先级，确保尽快释放锁。

**实现方式**：使用 `struct rb_root_cached waiters`（红黑树）按优先级排序等待队列，`raw_spinlock_t wait_lock` 保护等待队列，`struct task_struct *owner` 记录当前持有者。核心实现在 `kernel/locking/rtmutex.c`（约 3,200 行），支持 PI 提升链（沿锁依赖链传递优先级）。

**应用场景**：PREEMPT_RT 下 `spinlock_t` 和 `mutex` 的底层实现（自动映射，大多数代码不直接使用）。直接使用的场景包括 PI futex 实现（`kernel/futex/pi.c`）、I2C 总线锁（`drivers/i2c/i2c-core-base.c`）、rwsem 的 RT 路径（`kernel/locking/rwsem.c`）等需要显式 PI 保护的特殊路径。

### 核心结构

`include/linux/rtmutex.h:23-27`：

```c
struct rt_mutex_base {
    raw_spinlock_t          wait_lock;
    struct rb_root_cached   waiters;    // 按优先级排序
    struct task_struct      *owner;
};
```

`struct rt_mutex`（`include/linux/rtmutex.h:57-62`）在 `rt_mutex_base` 上加 lockdep 映射。

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_RT_MUTEX(name)` | 静态定义 | |
| `rt_mutex_init(lock)` | 动态初始化 | |
| `rt_mutex_lock(lock)` | 获取（可睡眠） | |
| `rt_mutex_lock_interruptible(lock)` | 可中断获取 | |
| `rt_mutex_trylock(lock)` | 尝试获取 | |
| `rt_mutex_unlock(lock)` | 释放 | |

头文件：`include/linux/rtmutex.h`；实现：`kernel/locking/rtmutex.c`

### 使用注意

- 大多数代码不直接使用 `rt_mutex`——PREEMPT_RT 下 `spinlock_t` 和 `mutex` 自动映射到它
- 直接使用的场景：PI futex 实现、需要显式 PI 保护的特殊路径
- `rt_mutex_base` 是 PREEMPT_RT 下 `spinlock_t` 和 `mutex` 的共同底层

---

## ww_mutex

### 定位

**功能**：Wait-Wound 互斥锁，允许以任意顺序获取一组锁而不死锁。通过为每个获取事务分配全局时间戳，当检测到锁冲突时根据算法（Wait-Die 或 Wound-Wait）决定等待还是返回 `-EDEADLK` 让调用者重试。

**实现方式**：`struct ww_class` 包含全局时间戳计数器 `atomic_long_t stamp` 和算法选择标志 `is_wait_die`。`struct ww_mutex` 在底层锁（非 RT 为 `struct mutex`，RT 为 `struct rt_mutex_base`）上加 `ww_acquire_ctx *ctx` 指针。实现在 `kernel/locking/ww_mutex.h`（570 行）和 `kernel/locking/mutex.c`。收到 `-EDEADLK` 后调用者必须释放所有锁，通过 `ww_mutex_lock_slow()` 重获取失败的锁后从头重试。

**应用场景**：GPU 驱动（DRM buffer object 验证）是主要用户——`drivers/gpu/drm/drm_gem.c`、`drivers/gpu/drm/ttm/ttm_execbuf_util.c`、`drivers/gpu/drm/msm/msm_gem_submit.c` 等需要按任意顺序锁多个 BO。DMA-BUF _reservation（`drivers/dma-buf/dma-resv.c`）和 regulator 子系统（`drivers/regulator/core.c`）也使用。全树 `ww_mutex_lock` 约 50+ 处。

### 核心结构

`include/linux/ww_mutex.h:38-53`：

```c
struct ww_class {
    atomic_long_t stamp;           // 全局时间戳计数器
    struct lock_class_key acquire_key;
    struct lock_class_key mutex_key;
    unsigned int is_wait_die;      // 1=Wait-Die, 0=Wound-Wait
};

struct ww_mutex {
    struct WW_MUTEX_BASE base;     // 非 RT: struct mutex; RT: struct rt_mutex_base
    struct ww_acquire_ctx *ctx;
};
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_WD_CLASS(name)` | 定义 Wait-Die 类 | 新事务被杀死 |
| `DEFINE_WW_CLASS(name)` | 定义 Wound-Wait 类 | 新事务被伤害 |
| `ww_mutex_init(lock, class)` | 初始化 | |
| `ww_acquire_init(ctx, class)` | 初始化获取上下文 | 分配时间戳 |
| `ww_mutex_lock(lock, ctx)` | 获取 | 可能返回 -EDEADLK |
| `ww_mutex_lock_slow(lock, ctx)` | 慢路径重获取 | 收到 -EDEADLK 后调用 |
| `ww_mutex_unlock(lock)` | 释放 | |
| `ww_acquire_fini(ctx)` | 结束上下文 | |

头文件：`include/linux/ww_mutex.h`

### 使用注意

- 收到 `-EDEADLK` 后必须释放所有已获取的锁，调用 `ww_mutex_lock_slow` 重新获取失败的锁，然后从头重试
- Wait-Die：新事务等旧事务 → 新事务被杀（-EDEADLK）
- Wound-Wait：旧事务等新事务 → 新事务被伤害，让出资源
- 详见 `output/locking/mutex.md` §1.9

---

## semaphore

### 定位

**功能**：计数信号量——允许多个并发持有者（count > 1 时），或用作二值信号量（count = 1 时）。与 mutex 不同，semaphore 没有所有权概念（任何上下文都可以 `up()`），且支持计数限流。

**实现方式**：`struct semaphore` 包含 `raw_spinlock_t lock`（保护内部状态）、`unsigned int count`（可用资源数）和 `struct list_head wait_list`（FIFO 等待队列）。`down()` 原子减 count，count < 0 时将调用者加入等待队列并睡眠；`up()` 增 count 并唤醒队列头的等待者。实现在 `kernel/locking/semaphore.c`（284 行）。

**应用场景**：限流场景（如限制并发打开的设备数）、传统 Unix 信号量语义、跨上下文释放锁的场景。全树 `down()` 约 174 处、`up()` 约 398 处，分布于网络子系统（`net/rxrpc/call_object.c`）、MMC 主机驱动（`drivers/mmc/host/cavium-octeon.c`）、音频编解码器（`sound/soc/codecs/`）等。选择原则：需要互斥用 mutex，需要计数或跨上下文释放用 semaphore。

### 核心结构

`include/linux/semaphore.h:15-19`：

```c
struct semaphore {
    raw_spinlock_t    lock;       // 保护 count 和 wait_list
    unsigned int      count;      // 可用资源数
    struct list_head  wait_list;  // FIFO 等待队列
};
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_SEMAPHORE(name)` | 静态定义（count=1） | 二值信号量 |
| `sema_init(sem, val)` | 动态初始化 | 指定 count |
| `down(sem)` | 获取（不可中断） | **已不推荐**——用 interruptible 变体 |
| `down_interruptible(sem)` | 可中断获取 | 返回 0 或 -EINTR |
| `down_killable(sem)` | 可被致命信号中断 | |
| `down_trylock(sem)` | 尝试获取 | 返回 0=成功，1=失败（注意：与 mutex_trylock 相反） |
| `down_timeout(sem, jiffies)` | 超时获取 | 返回 0 或 -ETIME |
| `up(sem)` | 释放 | 可由任何上下文调用 |

头文件：`include/linux/semaphore.h`；实现：`kernel/locking/semaphore.c`（284 行）

### 与 mutex 的区别

| 维度 | mutex | semaphore |
|------|-------|-----------|
| 所有权 | 有——必须由获取者释放 | 无——任何上下文可 `up()` |
| 计数 | 固定为 1 | 可配置（N 个并发） |
| 递归释放 | 不允许 | 允许 |
| lockdep | 完整支持 | 有限支持 |
| 性能 | 更快（快速路径 cmpxchg） | 较慢（始终需要 spinlock） |

**选择原则**：需要互斥（1 个持有者）→ 用 mutex。需要计数（N 个并发）或需要跨上下文释放 → 用 semaphore。
