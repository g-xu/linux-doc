# completion — 完成量

> 一次性事件同步原语。一个线程等待某个操作完成，另一个线程在操作完成时发信号。底层使用简单等待队列（swait），通过 `done` 计数器实现快速路径无队列操作。全树约 800 处 `wait_for_completion` 调用。

---

## §1 核心设计洞察

### 1.1 struct completion 的极简设计

```c
struct completion {                              // include/linux/completion.h:26-29
    unsigned int done;                           // 0=未完成, >0=已完成, UINT_MAX=永久完成
    struct swait_queue_head wait;                // 简单等待队列
};
```

只有两个字段。`done` 是核心——它的值决定了所有行为：

| done 值 | 含义 | wait_for_completion 行为 |
|---------|------|------------------------|
| 0 | 未完成 | 加入等待队列，睡眠 |
| 1~UINT_MAX-1 | 已完成 N 次 | 快速路径：递减 done，立即返回 |
| UINT_MAX | `complete_all()` 设置的永久完成 | 不递减，立即返回（允许多个等待者） |

### 1.2 底层使用 swait 而非 wait_queue

completion 不使用完整的 `wait_queue_head_t`，而是使用简单等待队列 `swait_queue_head`（`include/linux/swait.h:43-46`）：

```c
struct swait_queue_head {
    raw_spinlock_t      lock;        // 保护 task_list 和 done
    struct list_head    task_list;   // FIFO 等待列表
};
```

**原因**：swait 更轻量——没有排他等待者、没有自定义唤醒函数、没有自定义键。completion 不需要这些复杂特性。

### 1.3 等待循环的核心

`do_wait_for_common()`（`kernel/sched/completion.c:70-95`）：

```c
static inline long do_wait_for_common(struct completion *x,
                                      long (*action)(long), long timeout, int state)
{
    if (!x->done) {                          // 快速路径：已完成？
        DECLARE_SWAITQUEUE(wait);            // 栈上分配等待者节点
        do {
            if (signal_pending_state(state, current)) {
                timeout = -ERESTARTSYS;      // 被信号中断
                break;
            }
            __prepare_to_swait(&x->wait, &wait);
            __set_current_state(state);
            raw_spin_unlock_irq(&x->wait.lock);
            timeout = action(timeout);       // schedule_timeout / io_schedule_timeout
            raw_spin_lock_irq(&x->wait.lock);
        } while (!x->done && timeout);
        __finish_swait(&x->wait, &wait);
        if (!x->done)
            return timeout;                  // 超时或被中断
    }
    if (x->done != UINT_MAX)
        x->done--;                           // 消费一个完成计数
    return timeout ?: 1;
}
```

关键点：
1. **快速路径**（line 74）：如果 `done > 0`，不进入队列，直接递减并返回
2. **等待循环**：prepare → set state → unlock → schedule → lock → check done
3. **消费计数**（line 93）：`done != UINT_MAX` 时递减——`complete_all()` 设置的永久完成不递减

### 1.4 complete() vs complete_all()

**`complete()`**（`kernel/sched/completion.c:28-38`）：

```c
void complete(struct completion *x)
{
    raw_spin_lock_irqsave(&x->wait.lock, flags);
    if (x->done != UINT_MAX)
        x->done++;                // 递增计数
    swake_up_locked(&x->wait);   // 唤醒一个等待者
    raw_spin_unlock_irqrestore(&x->wait.lock, flags);
}
```

**`complete_all()`**（`kernel/sched/completion.c:57-67`）：

```c
void complete_all(struct completion *x)
{
    raw_spin_lock_irqsave(&x->wait.lock, flags);
    x->done = UINT_MAX;           // 永久完成
    swake_up_all_locked(&x->wait); // 唤醒所有等待者
    raw_spin_unlock_irqrestore(&x->wait.lock, flags);
}
```

| 维度 | complete() | complete_all() |
|------|-----------|---------------|
| done 值 | +1 | = UINT_MAX |
| 唤醒 | 一个等待者 | 所有等待者 |
| 后续等待 | 如果 done > 0 可通过 | 始终可通过 |
| 重用 | 无需 reinit | **必须** reinit_completion |

### 1.5 内存序

所有 `done` 的修改都在 `raw_spinlock_t` 保护下进行——锁本身提供完整的内存屏障。

仅有两处无锁读取使用 `READ_ONCE()`：
- `try_wait_for_completion()`（line 305）：乐观检查，0 则直接返回
- `completion_done()`（line 331）：乐观检查，0 则直接返回

这些是安全的——看到 0 时直接返回（正确），看到非 0 时加锁重新检查。

---

## §2 API 速查

### 2.1 初始化

头文件：`include/linux/completion.h`；实现：`kernel/sched/completion.c`

| API | 说明 | 备注 |
|-----|------|------|
| `DECLARE_COMPLETION(name)` | 静态声明（全局/静态） | 编译期初始化 |
| `DECLARE_COMPLETION_ONSTACK(name)` | 静态声明（栈上） | lockdep 下运行时初始化 |
| `init_completion(x)` | 动态初始化 | `done = 0` + init swait |
| `reinit_completion(x)` | 重新初始化 | 仅 `done = 0`；不重新初始化队列 |

### 2.2 等待

| API | 说明 | 备注 |
|-----|------|------|
| `wait_for_completion(x)` | 等待完成（不可中断） | `might_sleep()` |
| `wait_for_completion_timeout(x, timeout)` | 等待完成（带超时） | 返回剩余 jiffies，0=超时 |
| `wait_for_completion_interruptible(x)` | 可被信号中断 | 返回 0 或 -EINTR |
| `wait_for_completion_interruptible_timeout(x, timeout)` | 可中断 + 超时 | |
| `wait_for_completion_killable(x)` | 仅致命信号可中断 | 返回 0 或 -EINTR |
| `wait_for_completion_killable_timeout(x, timeout)` | killable + 超时 | |
| `wait_for_completion_io(x)` | IO 等待（调度器特殊记账） | 用 `io_schedule_timeout` |
| `wait_for_completion_io_timeout(x, timeout)` | IO 等待 + 超时 | |

### 2.3 信号发送

| API | 说明 | 备注 |
|-----|------|------|
| `complete(x)` | 发信号（唤醒一个等待者） | `done++` |
| `complete_all(x)` | 发信号（唤醒所有） | `done = UINT_MAX`；PREEMPT_RT 下要求线程上下文 |

### 2.4 查询

| API | 说明 | 备注 |
|-----|------|------|
| `try_wait_for_completion(x)` | 非阻塞尝试消费 | 返回 true=成功（done--），false=未完成 |
| `completion_done(x)` | 检查是否已完成 | 乐观检查 + 加锁确认 |

---

## §3 使用模式

### 3.1 内核线程启动同步

```c
// 创建者
struct task_struct *start_worker(void)
{
    DECLARE_COMPLETION_ON_STACK(done);
    struct worker *w = kzalloc(sizeof(*w), GFP_KERNEL);
    w->started = &done;
    w->task = kthread_create(worker_func, w, "my_worker");
    wake_up_process(w->task);
    wait_for_completion(&done);   // 等待线程报告已启动
    return w->task;
}

// 新线程
int worker_func(void *data)
{
    struct worker *w = data;
    // 初始化...
    complete(w->started);          // 通知创建者：我已就绪
    // 主循环...
}
```

### 3.2 中断驱动的一次性事件

```c
struct my_device {
    struct completion fw_loaded;
    int fw_status;
};

// 中断 handler
irqreturn_t fw_irq_handler(int irq, void *data)
{
    struct my_device *dev = data;
    dev->fw_status = read_status(dev);
    complete(&dev->fw_loaded);     // 通知等待者
    return IRQ_HANDLED;
}

// 等待固件加载
int wait_for_firmware(struct my_device *dev)
{
    wait_for_completion_timeout(&dev->fw_loaded, msecs_to_jiffies(5000));
    return dev->fw_status;
}
```

### 3.3 complete_all 用于屏障

```c
static DECLARE_COMPLETION(boot_done);

// 初始化完成后
void finish_boot(void)
{
    complete_all(&boot_done);   // 所有后续等待者立即通过
}

// 任何后续代码
void some_later_init(void)
{
    wait_for_completion(&boot_done);  // 如果 boot 已完成，立即返回
}
```

---

## §4 常见陷阱

### 4.1 complete_all 后不 reinit 就重用

**错误**：`complete_all()` → 做一些事 → `wait_for_completion()` 期望阻塞——但 `done == UINT_MAX`，永远不会阻塞。

**正确**：`complete_all()` 后如需重用，必须先 `reinit_completion()`，且确保所有等待者已完成。

### 4.2 栈上 completion 的生命周期

**错误**：函数 A 在栈上声明 completion → 函数 B（另一个线程）调用 `complete()` → 函数 A 已返回 → `complete()` 写入已释放的栈帧 → 内存损坏。

**正确**：确保 `complete()` 调用时栈帧仍然有效。`completion_done()` 可用于安全确认——它获取锁确保 `complete()` 已完成。

### 4.3 栈上 completion 用错宏

**错误**：栈上使用 `DECLARE_COMPLETION`（而非 `DECLARE_COMPLETION_ONSTACK`）→ lockdep 无法跟踪锁类别。

**正确**：栈上的 completion 始终用 `DECLARE_COMPLETION_ONSTACK`。无 lockdep 时两者等价。

### 4.4 wait_for_completion 不可中断

**错误**：在用户态 syscall 路径中使用 `wait_for_completion()` → 无法被 `Ctrl+C` 终止。

**正确**：用户态路径用 `wait_for_completion_interruptible()` 或 `wait_for_completion_killable()`。

### 4.5 reinit_completion 的时序

**错误**：在 `complete()` 和 `wait_for_completion()` 返回之间调用 `reinit_completion()` → 竞争条件。

**正确**：`reinit_completion()` 只能在所有等待者已返回**且**没有新的 `complete()` 竞争时调用。安全模式：

```c
wait_for_completion(&c);     // 等待者消费完成
reinit_completion(&c);       // 安全：我刚消费完，没有竞争
```

---

## §5 代码示例

### 5.1 设备驱动中的 completion 用法

```c
struct my_device {
    struct completion cmd_done;
    int result;
    void __iomem *base;
};

static int my_probe(struct platform_device *pdev)
{
    struct my_device *dev = devm_kzalloc(&pdev->dev, sizeof(*dev), GFP_KERNEL);
    init_completion(&dev->cmd_done);
    // ...
    return 0;
}

// 发送命令并等待硬件完成
int send_command(struct my_device *dev, u32 cmd)
{
    reinit_completion(&dev->cmd_done);

    writel(cmd, dev->base + CMD_REG);

    // 等待中断 handler 调用 complete()
    if (!wait_for_completion_timeout(&dev->cmd_done, msecs_to_jiffies(1000)))
        return -ETIMEDOUT;

    return dev->result;
}

// 中断 handler
static irqreturn_t my_irq(int irq, void *data)
{
    struct my_device *dev = data;
    dev->result = readl(dev->base + STATUS_REG);
    complete(&dev->cmd_done);
    return IRQ_HANDLED;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| struct completion 定义 | `include/linux/completion.h:26-29` | 一等 |
| 使用 swait_queue_head | `include/linux/completion.h:28` | 一等 |
| DECLARE_COMPLETION | `include/linux/completion.h:52-53` | 一等 |
| DECLARE_COMPLETION_ONSTACK | `include/linux/completion.h:67-75` | 一等 |
| init_completion | `include/linux/completion.h:84-88` | 一等 |
| reinit_completion 仅重置 done | `include/linux/completion.h:97-100` | 一等 |
| complete() 实现 | `kernel/sched/completion.c:28-38` | 一等 |
| complete_all() 设置 UINT_MAX | `kernel/sched/completion.c:57-67` | 一等 |
| do_wait_for_common 循环 | `kernel/sched/completion.c:70-95` | 一等 |
| try_wait_for_completion | `kernel/sched/completion.c:294-315` | 一等 |
| completion_done | `kernel/sched/completion.c:327-342` | 一等 |
| 所有同步在 raw_spinlock 下 | `kernel/sched/completion.c` 全文 | 一等 |
| complete_all PREEMPT_RT 断言 | `kernel/sched/completion.c:61` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `wait_for_completion` | ~800 |
| `complete` | ~677 |
| `init_completion` | ~460 |
| `complete_all` | ~162 |
| `reinit_completion` | ~47 |
