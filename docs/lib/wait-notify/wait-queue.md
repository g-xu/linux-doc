# wait_queue — 等待队列

> 内核最基础的等待/唤醒原语。所有 `wait_event*()` 宏、`completion`、`futex` 的底层都建立在 wait_queue 之上。它把"等待某个条件成立"这一模式抽象为：把当前任务挂入链表 → 调度出去 → 被条件变化方唤醒。

---

## §1 核心设计洞察

### 1.1 两个结构体

wait_queue 只有两个结构体（`include/linux/wait.h:30-40`）：

```c
struct wait_queue_entry {
    unsigned int        flags;       // WQ_FLAG_* 标志
    void               *private;    // 通常 = current（等待的任务）
    wait_queue_func_t   func;       // 唤醒回调
    struct list_head    entry;       // 链表节点
};

struct wait_queue_head {
    spinlock_t          lock;       // 保护链表
    struct list_head    head;       // 等待者链表
};
```

`wait_queue_head` 是"等待什么"——通常嵌入在需要被等待的数据结构中。`wait_queue_entry` 是"谁在等"——每个等待的任务一个，通常分配在栈上。

### 1.2 标志位语义

`include/linux/wait.h:19-25` 定义了 6 个标志：

| 标志 | 值 | 含义 |
|------|----|------|
| `WQ_FLAG_EXCLUSIVE` | 0x01 | 排他等待者——唤醒时只唤醒 N 个排他者而非全部 |
| `WQ_FLAG_WOKEN` | 0x02 | 已被唤醒，防止重复唤醒 |
| `WQ_FLAG_BOOKMARK` | 0x04 | 书签节点——长链表遍历的中断点 |
| `WQ_FLAG_CUSTOM` | 0x08 | 自定义 wake function 使用 |
| `WQ_FLAG_DONE` | 0x10 | 已完成（用于 `wait_queue_entry` 复用场景） |
| `WQ_FLAG_PRIORITY` | 0x20 | 优先级等待者——插入链表头部 |

### 1.3 普通等待者 vs 排他等待者

这是 wait_queue 最精妙的设计。两类等待者在链表中的位置不同：

```
wait_queue_head.head
    │
    ▼
┌────────────────────────────────────────────────────────────┐
│  普通等待者（头部插入）  │  排他等待者（尾部插入）           │
│  !WQ_FLAG_EXCLUSIVE     │  WQ_FLAG_EXCLUSIVE               │
│  add_wait_queue()       │  add_wait_queue_exclusive()       │
│                         │                                   │
│  唤醒：全部唤醒          │  唤醒：只唤醒 nr_exclusive 个     │
└────────────────────────────────────────────────────────────┘
```

**为什么需要排他等待者？** 考虑一个只有一个生产者的场景：100 个任务在等数据。如果全部唤醒（thundering herd），99 个会白白被调度又睡回去。排他模式下 `wake_up()` 只唤醒 1 个排他者 + 所有普通等待者，避免惊群。

### 1.4 ___wait_event()：所有 wait_event* 的引擎

`include/linux/wait.h:301-322` 的核心宏，所有 `wait_event*()` 变体都展开为它：

```c
#define ___wait_event(wq_head, condition, state, exclusive, ret, cmd)
({
    struct wait_queue_entry __wq_entry;
    init_wait_entry(&__wq_entry, exclusive ? WQ_FLAG_EXCLUSIVE : 0);
    for (;;) {
        long __int = prepare_to_wait_event(&wq_head, &__wq_entry, state);
        if (condition)           // ★ 先检查条件再睡眠
            break;
        if (___wait_is_interruptible(state) && __int) {
            __ret = __int;       // 被信号打断
            goto __out;
        }
        cmd;                     // schedule() 或变体
    }
    finish_wait(&wq_head, &__wq_entry);
__out: __ret;
})
```

**关键时序**：`prepare_to_wait_event` 把任务加入链表并设置状态 → 检查条件 → 不满足则 `schedule()`。`finish_wait` 在条件满足后把任务从链表移除并恢复 `TASK_RUNNING`。

### 1.5 唤醒路径：__wake_up_common()

`kernel/sched/wait.c:80-122` 是唤醒的核心：

```c
static int __wake_up_common(struct wait_queue_head *wq_head, unsigned int mode,
                            int nr_exclusive, int wake_flags, void *key,
                            wait_queue_entry_t *bookmark)
{
    // 遍历链表
    list_for_each_entry_safe_from(curr, next, &wq_head->head, entry) {
        unsigned flags = curr->flags;
        ret = curr->func(curr, mode, wake_flags, key);  // 调用唤醒回调
        if (ret && (flags & WQ_FLAG_EXCLUSIVE) && !--nr_exclusive)
            break;  // 已唤醒足够多的排他者，停止
    }
}
```

遍历顺序：从链表头开始。普通等待者在头部（全部唤醒），排他等待者在尾部（唤醒 N 个后停止）。

### 1.6 书签机制

当等待链表非常长（> 64 个节点，`WAITQUEUE_WALK_BREAK_CNT`）时，`__wake_up_common` 会在中断点插入一个书签节点（`WQ_FLAG_BOOKMARK`），然后返回。下次调用时从书签位置继续。这避免了长时间持锁遍历（`kernel/sched/wait.c:113-117`）。

### 1.7 内存序协议

等待/唤醒涉及跨 CPU 的可见性问题。协议是：

- **等待方**：`set_current_state(TASK_INTERRUPTIBLE)` 用 `smp_store_set_bits` 保证状态写入对其他 CPU 可见后，再检查条件（`prepare_to_wait_event` 内部有 `smp_mb()`）
- **唤醒方**：修改条件后调用 `wake_up()`，内部 `try_to_wake_up()` 包含完整的内存屏障，保证看到等待者的状态变更
- **关键不变量**：条件检查必须在 `prepare_to_wait` 之后、`schedule()` 之前——否则存在竞态窗口

### 1.8 TASK_* 状态与等待

| 状态 | 值 | 可被 wake_up 唤醒 | 可被信号唤醒 |
|------|----|-------------------|-------------|
| `TASK_RUNNING` | 0 | — | — |
| `TASK_INTERRUPTIBLE` | 1 | 是 | 是（返回 -ERESTARTSYS） |
| `TASK_UNINTERRUPTIBLE` | 2 | 是 | 否 |
| `TASK_KILLABLE` | (U\|K) | 是 | 仅致命信号 |
| `TASK_NORMAL` | — | 用作 wake_up 的 mode 参数，匹配 I+U |

`wait_event()` 用 `TASK_UNINTERRUPTIBLE`——不可被信号打断。`wait_event_interruptible()` 用 `TASK_INTERRUPTIBLE`——可被信号打断并返回 `-ERESTARTSYS`。

---

## §2 API 速查

### 2.1 初始化

头文件：`include/linux/wait.h`；实现：`kernel/sched/wait.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DECLARE_WAIT_QUEUE_HEAD(name)` | 静态声明并初始化 | 编译期初始化，`wait.h:61` |
| `init_waitqueue_head(wq_head)` | 动态初始化 | 带 lockdep class key，`wait.h:66` |
| `init_waitqueue_entry(entry, p)` | 初始化等待条目 | `p` 通常是 `current` |

### 2.2 队列管理

| API | 说明 | 备注 |
|-----|------|------|
| `add_wait_queue(head, entry)` | 加入普通等待者 | 插入链表**头部**，`wait.c:17` |
| `add_wait_queue_exclusive(head, entry)` | 加入排他等待者 | 插入链表**尾部**，`wait.c:28` |
| `remove_wait_queue(head, entry)` | 从队列移除 | `wait.c:50` |
| `init_wait_entry(entry, flags)` | 初始化 entry | 设 `func = autoremove_wake_function`，`wait.h:287` |
| `prepare_to_wait(head, entry, state)` | 加入队列并设状态 | 非排他，`wait.c:264` |
| `prepare_to_wait_exclusive(head, entry, state)` | 加入队列并设状态 | 排他，`wait.c:279` |
| `prepare_to_wait_event(head, entry, state)` | 通用版本 | `___wait_event` 使用，`wait.c:305` |
| `finish_wait(head, entry)` | 移除并恢复 TASK_RUNNING | `wait.c:391` |

### 2.3 唤醒

| API | 说明 | 备注 |
|-----|------|------|
| `wake_up(x)` | 唤醒 1 个排他 + 所有普通 | mode=TASK_NORMAL，`wait.h:222` |
| `wake_up_nr(x, nr)` | 唤醒 nr 个排他 + 所有普通 | `wait.h:223` |
| `wake_up_all(x)` | 唤醒所有 | nr_exclusive=0 表示全部，`wait.h:224` |
| `wake_up_locked(x)` | 唤醒（调用者已持锁） | `wait.h:225` |
| `wake_up_sync(x)` | 同步唤醒 | 唤醒方让出 CPU 给被唤醒方，`wait.h:227` |
| `wake_up_interruptible(x)` | 唤醒 1 个 INTERRUPTIBLE | `wait.h:229` |
| `wake_up_interruptible_all(x)` | 唤醒所有 INTERRUPTIBLE | `wait.h:231` |
| `wake_up_interruptible_sync(x)` | 同步唤醒 INTERRUPTIBLE | `wait.h:232` |
| `wake_up_poll(x, m)` | 唤醒 poll 等待者 | `wait.h:239` |

### 2.4 wait_event 家族

| API | 说明 | 备注 |
|-----|------|------|
| `wait_event(wq, condition)` | 不可中断等待 | TASK_UNINTERRUPTIBLE，`wait.h:341` |
| `wait_event_timeout(wq, cond, timeout)` | 带超时 | 返回剩余 jiffies，0=超时，`wait.h:413` |
| `wait_event_interruptible(wq, cond)` | 可被信号中断 | 返回 -ERESTARTSYS，`wait.h:497` |
| `wait_event_interruptible_timeout(wq, cond, timeout)` | 可中断 + 超时 | `wait.h:531` |
| `wait_event_freezable(wq, cond)` | 可冻结 | 系统挂起时自动冻结，`wait.h:380` |
| `wait_event_killable(wq, cond)` | 仅致命信号可中断 | TASK_KILLABLE |
| `wait_event_cmd(wq, cond, cmd1, cmd2)` | 自定义调度前后动作 | cmd1 在 schedule 前，cmd2 在后，`wait.h:471` |
| `wait_event_lock(wq, cond, lock)` | 带锁等待 | 检查/调度时持锁 |
| `wait_event_lock_irq(wq, cond, lock)` | 带锁 + 关中断等待 | |

### 2.5 唤醒回调函数

| API | 说明 | 备注 |
|-----|------|------|
| `autoremove_wake_function(entry, mode, sync, key)` | 唤醒 + 自动移除 | 成功唤醒后从链表删除 entry，`wait.c:417` |
| `default_wake_function(entry, mode, sync, key)` | 默认唤醒 | 调用 `try_to_wake_up()` |

---

## §3 使用模式

### 3.1 基本等待（最常用）

```c
DECLARE_WAIT_QUEUE_HEAD(my_wq);
static bool data_ready;

// 等待方
void consumer(void)
{
    wait_event(my_wq, data_ready == true);
    // data_ready 已为 true，安全地读取数据
}

// 唤醒方
void producer(void)
{
    data_ready = true;
    wake_up(&my_wq);
}
```

`wait_event` 内部循环：检查条件 → 不满足则加入队列并调度 → 被唤醒后重新检查条件。

### 3.2 可中断等待（用户空间 syscall）

```c
long my_read(struct file *file, char __user *buf, size_t count, loff_t *ppos)
{
    struct my_dev *dev = file->private_data;
    int ret;

    ret = wait_event_interruptible(dev->waitq,
                                   dev->data_available || dev->disconnected);
    if (ret)
        return -ERESTARTSYS;  // 被信号打断

    if (dev->disconnected)
        return -ENODEV;

    return copy_data_to_user(dev, buf, count);
}
```

条件里包含 `disconnected`——避免设备断开后永远阻塞。

### 3.3 带超时等待

```c
ret = wait_event_timeout(dev->waitq,
                         dev->response_received,
                         msecs_to_jiffies(5000));
if (ret == 0) {
    // 超时：条件在 5 秒内未成立
    return -ETIMEDOUT;
}
// ret > 0: 剩余 jiffies 数
```

### 3.4 手动管理等待队列（复杂场景）

```c
DEFINE_WAIT(wait);

for (;;) {
    prepare_to_wait(&wq, &wait, TASK_INTERRUPTIBLE);
    if (condition)
        break;
    schedule();
}
finish_wait(&wq, &wait);
```

通常不需要手动管理——`wait_event*()` 宏封装了这一切。只有需要在等待循环中做额外工作（如释放/重获其他锁）时才用这种模式。

### 3.5 排他等待（避免惊群）

```c
DEFINE_WAIT(wait);

prepare_to_wait_exclusive(&wq, &wait, TASK_INTERRUPTIBLE);
// 只有 1 个排他等待者会被 wake_up() 唤醒
// 适合只有一个资源可用的场景
```

---

## §4 常见陷阱

### 4.1 条件检查和 prepare_to_wait 的顺序

**错误**：先 `schedule()` 再检查条件。唤醒方在 `schedule()` 之前设置条件 → 等待者永远不醒。

**正确**：`___wait_event` 保证顺序：`prepare_to_wait_event` → 检查条件 → 不满足才 `schedule()`。使用 `wait_event*()` 宏就不可能犯这个错。

### 4.2 唤醒方缺少内存屏障

**错误**：修改条件后直接检查 `waitqueue_active()` 再 `wake_up()`——`waitqueue_active()` 不提供同步保证。

**正确**：直接调用 `wake_up()`。它内部的 `try_to_wake_up()` 包含必要的内存屏障。`waitqueue_active()` 只用于优化（避免不必要的锁获取），不用于正确性。

### 4.3 在中断上下文中使用不可中断等待

**错误**：硬中断 handler 中调用 `wait_event()`——中断上下文不能调度。

**正确**：中断 handler 只负责设置条件并 `wake_up()`。等待必须在进程上下文。

### 4.4 wake_up 与 wake_up_interruptible 混用

**错误**：等待方用 `wait_event_interruptible()`（TASK_INTERRUPTIBLE），唤醒方用 `wake_up()`（mode=TASK_NORMAL=TASK_INTERRUPTIBLE|TASK_UNINTERRUPTIBLE）。这**实际上是正确的**——`TASK_NORMAL` 包含 `TASK_INTERRUPTIBLE`。

**真正的问题**反过来：等待方用 `wait_event()`（TASK_UNINTERRUPTIBLE），唤醒方用 `wake_up_interruptible()`（只唤醒 TASK_INTERRUPTIBLE）→ 等待者**永远不会被唤醒**。

**规则**：`wake_up*` 的 mode 必须包含等待方使用的 state。安全做法是 `wait_event` 配 `wake_up`，`wait_event_interruptible` 配 `wake_up_interruptible` 或 `wake_up`。

### 4.5 等待条件中的竞态

**错误**：条件检查在 `prepare_to_wait` 之前，中间没有原子性保证。

```c
// 错误模式
if (condition) return;       // 检查
prepare_to_wait(&wq, &w, TASK_UNINTERRUPTIBLE);
schedule();                  // 竞态窗口：条件在这两行之间变化
```

**正确**：使用 `wait_event()` 宏，它保证 `prepare_to_wait` → 条件检查 → `schedule()` 之间不会被唤醒方打断（状态已设为非 RUNNING，唤醒方的 `try_to_wake_up` 会看到）。

### 4.6 wait_queue_entry 的生命周期

`wait_queue_entry` 通常分配在栈上（`DEFINE_WAIT` / `___wait_event` 内部）。**绝不能**在 `finish_wait()` 返回后还持有对它的引用。如果唤醒回调（`func`）异步执行，entry 可能已被销毁。

---

## §5 代码示例

### 5.1 字符设备驱动中的等待模式

```c
struct my_device {
    wait_queue_head_t read_wq;
    wait_queue_head_t write_wq;
    u8 *buffer;
    size_t buf_size;
    size_t read_pos;
    size_t write_pos;
    bool disconnected;
};

static ssize_t my_read(struct file *filp, char __user *buf,
                       size_t count, loff_t *ppos)
{
    struct my_device *dev = filp->private_data;
    int ret;

    // 快路径：数据已就绪
    if (dev->read_pos < dev->write_pos)
        goto copy;

    // 慢路径：等待数据
    ret = wait_event_interruptible(dev->read_wq,
        (dev->read_pos < dev->write_pos) || dev->disconnected);
    if (ret)
        return -ERESTARTSYS;
    if (dev->disconnected)
        return -ENODEV;

copy:
    // 复制数据到用户空间（在锁外）
    return copy_to_user_remaining(dev, buf, count);
}

// 中断 handler：数据到达
static irqreturn_t my_irq(int irq, void *data)
{
    struct my_device *dev = data;
    // ... 填充 buffer ...
    dev->write_pos = new_pos;
    wake_up_interruptible(&dev->read_wq);
    return IRQ_HANDLED;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| wait_queue_entry 结构体定义 | `include/linux/wait.h:30-35` | 一等 |
| wait_queue_head 结构体定义 | `include/linux/wait.h:37-40` | 一等 |
| WQ_FLAG_* 标志定义 | `include/linux/wait.h:19-25` | 一等 |
| ___wait_event 宏展开 | `include/linux/wait.h:301-322` | 一等 |
| __wake_up_common 遍历逻辑 | `kernel/sched/wait.c:80-122` | 一等 |
| 书签机制 WAITQUEUE_WALK_BREAK_CNT=64 | `kernel/sched/wait.c:65`、`:113-117` | 一等 |
| 普通等待者头部插入、排他尾部插入 | `kernel/sched/wait.c:17-37` | 一等 |
| autoremove_wake_function 实现 | `kernel/sched/wait.c:417-426` | 一等 |
| wake_up 宏展开 | `include/linux/wait.h:222-247` | 一等 |
| prepare_to_wait / finish_wait | `kernel/sched/wait.c:264-415` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `wake_up` | ~1,965 |
| `wait_event` | ~681 |
| `wake_up_interruptible` | ~783 |
| `wait_event_interruptible` | ~436 |
| `wait_event_timeout` | ~432 |
| `DEFINE_WAIT` | ~187 |
