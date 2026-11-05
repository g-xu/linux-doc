# 信号量杂项 — percpu_rw_semaphore

> per-CPU 读写信号量。读侧快速路径仅做 per-CPU 原子递增（无全局竞争），写侧通过 RCU 同步强制所有读者走慢路径后等待读者全部退出。用于 `mmap_lock` 等读极多写极少的场景。

---

## percpu_rw_semaphore

### 定位

**功能**：高可扩展性读写信号量——读侧快速路径仅做 per-CPU 原子递增（无全局原子操作竞争），写侧通过 RCU 同步强制所有读者走慢路径后等待读者全部退出。专为读极多写极少的场景设计，比 `rw_semaphore` 在高 CPU 数下可扩展得多。

**实现方式**：`struct percpu_rw_semaphore` 包含 `struct rcu_sync rss`（RCU 同步状态机）、`unsigned int __percpu *read_count`（per-CPU 读者计数器）、`atomic_t block`（写者阻塞标志）和 `wait_queue_head_t waiters`。读快速路径 = `this_cpu_inc(*read_count)`（无全局原子操作）；写路径通过 `atomic_xchg(&sem->block, 1)` 设置阻塞标志，再经 `rcu_sync_enter()` 等待一个 RCU 宽限期强制读者走慢路径，最后等待所有读者退出。实现在 `kernel/locking/percpu-rwsem.c`（282 行）。

**应用场景**：`mmap_lock`（`mm_struct` 的读写锁，`mm/memory_hotplug.c`）、文件系统超级块写保护（`fs/super.c` 中的 `sb->s_write_lock`）、cgroup 子系统（`kernel/cgroup/cgroup.c`）、CPU 热插拔（`kernel/cpu.c`）、文件锁（`fs/locks.c`）、ext4 迁移（`fs/ext4/migrate.c`）等读极多写极少的场景。全树约 100 处使用。

### 核心结构

`include/linux/percpu-rwsem.h:12-21`：

```c
struct percpu_rw_semaphore {
    struct rcu_sync       rss;          // RCU 同步状态机
    unsigned int __percpu *read_count;  // per-CPU 读者计数
    struct rcuwait        writer;       // 写者等待
    wait_queue_head_t     waiters;      // 读者/写者等待队列
    atomic_t              block;        // 写者阻塞标志
};
```

### API

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_PERCPU_RWSEM(name)` | 静态定义（全局） | |
| `DEFINE_STATIC_PERCPU_RWSEM(name)` | 静态定义（文件静态） | |
| `percpu_init_rwsem(sem)` | 动态初始化 | |
| `percpu_free_rwsem(sem)` | 销毁 | |
| `percpu_down_read(sem)` | 获取读锁 | 快速路径：`this_cpu_inc` |
| `percpu_up_read(sem)` | 释放读锁 | `this_cpu_dec` |
| `percpu_down_read_trylock(sem)` | 尝试获取读锁 | |
| `percpu_down_write(sem)` | 获取写锁 | 阻塞所有读者后等待排空 |
| `percpu_up_write(sem)` | 释放写锁 | 恢复读者快速路径 |
| `percpu_is_write_locked(sem)` | 检查写锁状态 | |
| `percpu_rwsem_assert_held(sem)` | lockdep 断言 | |

头文件：`include/linux/percpu-rwsem.h`；实现：`kernel/locking/percpu-rwsem.c`（282 行）

### 工作机制

```
读快速路径（无写者等待）:
    this_cpu_inc(*read_count)    // 无全局原子操作！
    // rcu_sync 处于 idle 状态，不需要检查 sem->block

读慢路径（有写者等待）:
    // rcu_sync_enter() 已切换状态
    this_cpu_inc(*read_count)
    smp_mb()
    if (atomic_read(&sem->block)) {
        // 写者正在等待，回退并进入等待队列
        this_cpu_dec(*read_count)
        睡眠等待
    }

写获取:
    atomic_xchg(&sem->block, 1)     // 设置阻塞标志
    rcu_sync_enter()                 // 等待一个 GP，强制读者走慢路径
    wait_event(readers_active_check) // 等待所有读者退出
```

### 使用注意

- 读侧快速路径完全无全局竞争——比 `rw_semaphore` 在高 CPU 数下可扩展得多
- 写侧代价高——需要等待一个 RCU 宽限期 + 所有读者排空
- 适合读极多写极少的场景
- 不适合写频繁的场景——每次写获取都要等 RCU 宽限期
