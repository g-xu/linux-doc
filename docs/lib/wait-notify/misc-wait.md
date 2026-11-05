# 等待队列变体 — swait / rcuwait / wait_bit

> 三种针对特定场景优化的等待机制，都建立在 `wait_queue` 基础之上或与之互补。

---

## swait — 简单等待队列

### 定位

**功能**：为 PREEMPT_RT 设计的确定性等待队列。用 `raw_spinlock_t` 替代普通 `spinlock_t`，保证在 RT 内核中也能安全使用。去掉了完整 `wait_queue` 的灵活性（自定义唤醒回调、混合中断模式），换取有界的锁持有时间和 IRQ 关闭时间。它是 completion 内部和 kthread 停车等待的底层机制。

**实现方式**：`struct swait_queue_head`（`include/linux/swait.h:43-51`）用 `raw_spinlock_t` 保护 `task_list` 链表。`struct swait_queue` 直接存 `task_struct *` 指针，无自定义唤醒回调——这是能用 `raw_spinlock_t` 的前提（回调可能睡眠，`raw_spinlock` 下不允许）。与完整 `wait_queue` 的关键差异：无普通/排他之分（全部排他）、所有唤醒用 `TASK_NORMAL`、`swake_up_all` 每唤醒一个 waiter 释放/重获锁（`kernel/sched/swait.c:77-78`）保证有界持锁时间。

**应用场景**：使用量约 32 处，仅用于 RT 关键路径。`completion` 内部（`kernel/sched/completion.c`）使用 swait 实现 `wait_for_completion`/`complete`，这是内核中最广泛的同步原语之一。kthread 停车等待（`kthread_park`/`kthread_unpark`）和虚拟机 vCPU halt 也使用 swait。使用范围小但位置关键——completion 的 RT 安全性完全依赖 swait。

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `init_swait_queue_head(head)` | 初始化 | 用 `raw_spin_lock_init` |
| `swake_up_one(head)` | 唤醒一个等待者 | `kernel/sched/swait.c:47` |
| `swake_up_all(head)` | 唤醒所有 | 分段释放锁，`swait.c:61` |
| `prepare_to_swait_exclusive(head, entry, state)` | 加入队列（排他） | `swait.c:91` |
| `finish_swait(head, entry)` | 移除并恢复 | `swait.c:132` |
| `swait_event_exclusive(head, cond)` | 等待宏 | `swait.h:186` |
| `swait_event_interruptible_exclusive(head, cond)` | 可中断等待 | `swait.h:210` |
| `swq_has_sleeper(head)` | 是否有等待者 | `smp_mb()` + 检查，`swait.h:134` |

---

## rcuwait — RCU 保护的单任务等待

### 定位

**功能**：最轻量的等待机制——只支持**一个**等待者。用 RCU 保护 `task_struct` 指针的生命周期，无需任何锁。用于"一个任务等待一个事件"的极简场景，是三种等待变体中开销最低的。

**实现方式**：`struct rcuwait`（`include/linux/rcuwait.h:16-18`）只有一个字段 `struct task_struct __rcu *task`。等待方 `prepare_to_rcuwait()` 用 `rcu_assign_pointer` 设置 `task = current`，然后循环检查条件 + `schedule()`。唤醒方 `rcuwait_wake_up()`（`kernel/exit.c:288-315`）在 `rcu_read_lock()` 保护下读取 task 指针并调用 `wake_up_process()`。RCU 保证即使等待者正在退出，`task_struct` 也不会被释放。内存序协议：等待方 `set_current_state`（屏障 A）和唤醒方 `smp_mb()` + `rcu_dereference`（屏障 B）配对，防止"条件已设置但看不到任务"或"任务已注册但看不到条件"的竞态。

**应用场景**：约 47 处使用，用于内核核心路径。CPU hotplug 等待（`rq->hotplug_wait`）、workqueue worker 停车（`worker->wait`）、futex PI 等待（`kernel/futex/`）、percpu_rwsem writer 等待（`kernel/locking/percpu-rwsem.c`）。这些场景的共同特点是：等待者只有一个，且对锁开销极度敏感——rcuwait 的零锁设计使其成为这些路径的最优选择。

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `rcuwait_init(wait)` | 初始化 | 设 `task = NULL`，`rcuwait.h:23` |
| `prepare_to_rcuwait(wait)` | 注册当前任务 | `rcu_assign_pointer`，`rcuwait.h:45` |
| `finish_rcuwait(wait)` | 注销 | 设 `task = NULL`，`kernel/rcu/update.c:410` |
| `rcuwait_wake_up(wait)` | 唤醒 | RCU 保护 + `wake_up_process`，`kernel/exit.c:288` |
| `rcuwait_wait_event(wait, cond, state)` | 等待宏 | 完整等待循环，`rcuwait.h:52` |
| `rcuwait_active(wait)` | 是否有人等 | `rcu_access_pointer`，`rcuwait.h:32` |

---

## wait_bit — 位等待队列

### 定位

**功能**：等待某个内存字中的特定位被清除（或设置）。是完整 `wait_queue` 的上层封装，通过哈希表把 `(word, bit)` 映射到 256 个全局等待队列之一。广泛用于页标志（PageLocked / PageWriteback）、缓冲区锁、文件系统位标志，以及通用变量等待（`wait_var_event` / `wake_up_var`）。

**实现方式**：全局 `bit_wait_table[256]`（`kernel/sched/wait_bit.c:10-17`），每个元素是一个 `wait_queue_head_t`。`bit_waitqueue(word, bit)` 用哈希函数把地址映射到队列。`struct wait_bit_queue_entry`（`include/linux/wait_bit.h:10-19`）嵌入完整 `wait_queue_entry`，附加 `wait_bit_key`（被等待字的地址 + 位号 + 超时）。自定义唤醒函数 `wake_bit_function`（`wait_bit.c:21-32`）检查 key 是否匹配且位已清除。等待循环用 `prepare_to_wait` + `test_bit` + action 函数（`bit_wait`/`bit_wait_io`/`bit_wait_timeout`）调度。`wait_var_event`（`wait_bit.h:273-312`）复用同一套基础设施，`bit_nr = -1`，等待任意指针指向的值发生变化。

**应用场景**：使用量最大（约 332 处），广泛用于内存管理核心路径。页缓存锁（`wait_on_bit(PAGE_WAIT_BIT)` → PageLocked）、缓冲区头锁（BH_Lock）、文件系统标志等待是传统用户。`wait_var_event` / `wake_up_var`（约 98 处调用）是新增的通用变量等待接口，用于驱动中等待硬件状态变化、固件加载完成等场景。`clear_and_wake_up_bit` 提供原子清位 + 唤醒的组合操作。

### 主要 API

| API | 说明 | 备注 |
|-----|------|------|
| `wait_on_bit(word, bit, mode)` | 等待位清除 | 快速路径 `test_bit_acquire`，`wait_bit.h:70` |
| `wait_on_bit_io(word, bit, mode)` | 同上但 `io_schedule` | `wait_bit.h:95` |
| `wait_on_bit_timeout(word, bit, mode, timeout)` | 带超时 | `wait_bit.h:121` |
| `wait_on_bit_lock(word, bit, mode)` | 等待并设置位（位锁） | `wait_bit.h:178` |
| `wake_up_bit(word, bit)` | 唤醒等待该位的任务 | `wait_bit.c:147` |
| `clear_and_wake_up_bit(word, bit)` | 清位 + 唤醒 | `clear_bit_unlock` + `smp_mb` + `wake_up_bit`，`wait_bit.h:330` |
| `wait_var_event(ptr, cond)` | 等待变量变化 | 复用 bit 基础设施，`wait_bit.h:273` |
| `wake_up_var(ptr)` | 唤醒变量等待者 | `wait_bit.c:191` |
| `store_release_wake_up(ptr, val)` | release 存储 + 唤醒 | `wait_bit.h:391` |
| `atomic_dec_and_wake_up(ptr)` | 原子减到 0 + 唤醒 | `wait_bit.h:372` |

### action 函数

| 函数 | 说明 | 备注 |
|------|------|------|
| `bit_wait(key, mode)` | `schedule()` + 信号检查 | `wait_bit.c:197` |
| `bit_wait_io(key, mode)` | `io_schedule()` + 信号检查 | `wait_bit.c:207` |
| `bit_wait_timeout(key, mode)` | `schedule_timeout()` + 超时检查 | `wait_bit.c:217` |
| `bit_wait_io_timeout(key, mode)` | `io_schedule_timeout()` + 超时 | `wait_bit.c:231` |

---

## 三者对比

| 特性 | swait | rcuwait | wait_bit |
|------|-------|---------|----------|
| 最大等待者 | 无限（链表） | 1（单指针） | 无限（哈希队列） |
| 锁类型 | `raw_spinlock_t` | 无锁（RCU） | 普通 `wait_queue` spinlock |
| RT 安全 | 是（设计目标） | 是（无锁） | 否（用普通 wait_queue） |
| 自定义唤醒回调 | 否 | 不适用 | 是（`wake_bit_function`） |
| 全树使用量 | ~32 | ~47 | ~332 |
| 典型场景 | completion 内部、kthread 停车 | CPU hotplug、workqueue | 页锁、缓冲区锁、变量等待 |
