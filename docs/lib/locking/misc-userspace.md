# 用户空间锁杂项 — futex

> 快速用户空间互斥锁。用户态在无竞争时通过原子操作直接获取/释放锁（零系统调用开销），仅在竞争时陷入内核睡眠/唤醒。是 glibc pthread_mutex 的底层实现。

---

## futex

### 定位

**功能**：为用户态互斥锁提供内核支持——无竞争时用户态通过 `cmpxchg` 原子操作直接获取/释放锁（零系统调用开销），仅在竞争时陷入内核由内核负责睡眠和唤醒。是 glibc `pthread_mutex_t`、`pthread_cond_t`、`sem_t` 的底层实现。

**实现方式**：用户态对 `u32` 字做 `cmpxchg`（快速路径）；竞争时调用 `sys_futex(FUTEX_WAIT)` 陷入内核。内核通过 `struct futex_hash_bucket`（哈希桶 + 自旋锁 + 按优先级排序的 plist 等待队列）管理等待者，`union futex_key` 将用户地址映射为内核标识。唤醒者调用 `sys_futex(FUTEX_WAKE)` 从哈希桶中取出等待者并 `wake_up_process`。PI futex 使用 `rt_mutex` 实现优先级继承。实现在 `kernel/futex/` 目录（4,672 行）。

**应用场景**：glibc 的 `pthread_mutex_t` / `pthread_cond_t` / `sem_t` 的底层——所有使用 POSIX 线程的用户态程序都间接依赖 futex。内核内 `kernel/fork.c` 在进程创建时初始化 robust futex 列表。`futex_waitv`（Linux 5.14+）支持同时等待多个 futex，用于 Wine/游戏场景。PI futex 用于需要优先级继承的用户态锁。

### 核心结构

`kernel/futex/futex.h`：

```c
struct futex_hash_bucket {            // line 45
    atomic_t waiters;                 // 等待者计数
    spinlock_t lock;                  // 保护 chain
    struct plist_head chain;          // 等待队列（按优先级排序）
};

struct futex_q {                      // line 96
    struct plist_node list;           // 链入 hash_bucket.chain
    struct task_struct *task;         // 等待的任务
    spinlock_t *lock_ptr;             // 关联的 hash bucket 锁
    union futex_key key;              // 哈希键（用户地址 → 内核标识）
    struct futex_pi_state *pi_state;  // PI 状态（PI futex 用）
    u32 bitset;                       // 位集合（用于 FUTEX_WAIT_BITSET）
};
```

### 系统调用

`kernel/futex/syscalls.c`：

| 系统调用 | 说明 | 备注 |
|---------|------|------|
| `futex(uaddr, op, val, ...)` | 主 futex 系统调用 | `op` 决定操作类型 |
| `futex_waitv(waiters, nr_waiters, ...)` | 多 futex 等待 | Linux 5.14+ |
| `set_robust_list(head, len)` | 设置健壮 futex 列表 | 线程退出时内核清理 |
| `get_robust_list(pid, head, len)` | 获取健壮 futex 列表 | |

### 核心操作

| 操作 | 说明 | 备注 |
|------|------|------|
| `FUTEX_WAIT` | 如果 `*uaddr == val` 则睡眠 | 最基本的等待 |
| `FUTEX_WAKE` | 唤醒最多 `val` 个等待者 | |
| `FUTEX_WAIT_BITSET` | 带位集合的等待 | 选择性唤醒 |
| `FUTEX_WAKE_BITSET` | 按位集合唤醒 | |
| `FUTEX_REQUEUE` | 将等待者从一个 futex 移到另一个 | 用于 `pthread_cond_t` |
| `FUTEX_LOCK_PI` | PI 感知加锁 | 使用 rt_mutex |
| `FUTEX_UNLOCK_PI` | PI 感知解锁 | |
| `FUTEX_TRYLOCK_PI` | PI 感知 trylock | |

实现：`kernel/futex/` 目录（4,672 行）：
- `core.c`（1,159 行）：核心基础设施
- `waitwake.c`（708 行）：wait/wake 实现
- `pi.c`（1,233 行）：优先级继承
- `requeue.c`（899 行）：requeue 操作

### 工作机制

```
用户态 pthread_mutex_lock():
    1. cmpxchg(&mutex->owner, 0, TID)  // 快速路径：无竞争
       成功 → 返回（零内核开销）
       失败 → 
    2. sys_futex(&mutex->owner, FUTEX_WAIT, expected_val)
       内核：
         a. 再次检查 *uaddr == expected_val（防止竞争）
         b. 计算 hash key → 找到 hash_bucket
         c. 将当前任务加入 bucket.chain
         d. schedule() 睡眠

用户态 pthread_mutex_unlock():
    1. mutex->owner = 0              // 清除所有者
       smp_mb()
    2. if (有等待者)
         sys_futex(&mutex->owner, FUTEX_WAKE, 1)
         内核：
           a. 找到 hash_bucket
           b. 从 chain 取出第一个等待者
           c. wake_up_process(task)
```

### 使用注意

- 内核开发者通常不直接调用 futex 系统调用——glibc 封装在 pthread 中
- PI futex 需要 `CONFIG_FUTEX_PI`，使用 `rt_mutex` 实现优先级继承
- 健壮 futex（`FUTEX_WAIT` + `set_robust_list`）允许内核在线程退出时清理持有的锁
- `futex_waitv` 允许同时等待多个 futex——用于 Wine/游戏场景
