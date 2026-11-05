# softirq 与 tasklet — 中断下半部

> softirq 是内核中优先级最高的延迟中断处理机制，10 个固定向量在硬中断退出时执行；tasklet 构建在 softirq 之上，提供 per-CPU 序列化的延迟回调，但整个机制已被标记为弃用（推荐 threaded IRQ 替代）。

---

## softirq — 10 个固定优先级的延迟向量

### 定位

硬中断处理需要尽可能快地完成（关中断时间最小化），但某些后续工作（网络包处理、定时器到期、块 I/O 完成）仍然紧迫。softirq 在硬中断退出时、中断开启的窗口中执行，允许被同类型 softirq 抢占（不同 CPU 上可并行）。

### 10 个向量

定义于 `include/linux/interrupt.h:548-562`：

| 编号 | 名称 | 用途 |
|------|------|------|
| 0 | HI_SOFTIRQ | 高优先级 tasklet |
| 1 | TIMER_SOFTIRQ | 时间轮到期处理 |
| 2 | NET_TX_SOFTIRQ | 网络发送完成 |
| 3 | NET_RX_SOFTIRQ | 网络接收处理 |
| 4 | BLOCK_SOFTIRQ | 块 I/O 完成 |
| 5 | IRQ_POLL_SOFTIRQ | IRQ 轮询 |
| 6 | TASKLET_SOFTIRQ | 普通 tasklet |
| 7 | SCHED_SOFTIRQ | 调度器负载均衡 |
| 8 | HRTIMER_SOFTIRQ | 高分辨率定时器到期 |
| 9 | RCU_SOFTIRQ | RCU 回调 |

热插拔安全掩码（`include/linux/interrupt.h:575-576`）：`TIMER`、`IRQ_POLL`、`HRTIMER`、`RCU` 在 CPU 热插拔时可安全迁移。

### 实现方式

向量表（`kernel/softirq.c:59`）：

```c
static struct softirq_action softirq_vec[NR_SOFTIRQS] __cacheline_aligned_in_smp;
```

每个条目只有一个回调指针（`include/linux/interrupt.h:588-591`）：

```c
struct softirq_action {
    void (*action)(struct softirq_action *);
};
```

**待处理位图**：per-CPU `irq_stat.__softirq_pending`。`raise_softirq()` 设置对应位，`local_softirq_pending()` 读取。

**执行流程**（非 PREEMPT_RT）：

1. **从 irq_exit 触发**（`kernel/softirq.c:669-686`）：`__irq_exit_rcu()` 检查 `!in_interrupt() && local_softirq_pending()`，调用 `invoke_softirq()`
2. **invoke_softirq**：arm64 上 `CONFIG_HAVE_IRQ_EXIT_ON_IRQ_STACK` 已定义，直接调用 `__do_softirq()`
3. **handle_softirqs**（`kernel/softirq.c:535-608`）：核心循环
   - 保存并清除待处理掩码
   - 开启本地中断（允许嵌套）
   - 通过 `ffs()` 遍历待处理位，调用每个 `action`
   - 重新关中断，检查新待处理
   - 最多循环 `MAX_SOFTIRQ_RESTART`（10）次或 2ms（`MAX_SOFTIRQ_TIME`）
   - 超限后唤醒 `ksoftirqd`
4. **ksoftirqd**：per-CPU 内核线程（`"ksoftirqd/%u"`），在超出限制或 `force_irqthreads()` 时运行

### 主要 API

头文件：`include/linux/interrupt.h`；实现：`kernel/softirq.c`

| API | 说明 | 备注 |
|-----|------|------|
| `open_softirq(nr, action)` | 注册向量处理器 | 设置 `softirq_vec[nr].action` |
| `raise_softirq(nr)` | 触发 softirq | 保存/恢复 IRQ 标志 |
| `raise_softirq_irqoff(nr)` | IRQ 已关闭时触发 | 可能唤醒 ksoftirqd |
| `__raise_softirq_irqoff(nr)` | 底层：设置待处理位 | `or_softirq_pending(1UL << nr)` |
| `do_softirq()` | 从进程上下文执行 softirq | 检查 `in_interrupt()` |
| `local_bh_disable()` | 禁止下半部 | preempt count 加 `SOFTIRQ_DISABLE_OFFSET` |
| `local_bh_enable()` | 恢复下半部 | 如有待处理则执行 softirq |

### 抢占计数编码

- `SOFTIRQ_OFFSET`：标记"正在执行 softirq"
- `SOFTIRQ_DISABLE_OFFSET` = `2 * SOFTIRQ_OFFSET`：`local_bh_disable()` 使用
- 两者区分允许判断"正在执行 softirq"vs"BH 被 local_bh_disable 禁用"

### arm64 上的 irq_exit 路径

- `__ARCH_IRQ_EXIT_IRQS_DISABLED` 已定义（`arch/arm64/include/asm/hardirq.h:19`）：`__irq_exit_rcu()` 走 `lockdep_assert_irqs_disabled()` 路径
- 异常入口（`arch/arm64/kernel/entry-common.c`）：`__el1_irq` → `irq_enter_rcu()` → `do_interrupt_handler()` → `irq_exit_rcu()`
- `do_interrupt_handler` 通过 `call_on_irq_stack()` 切换到 per-CPU IRQ 栈
- `do_softirq_own_stack`（`arch/arm64/kernel/irq.c:83-86`）：在 IRQ 栈上调用 `__do_softirq()`

### 使用规模

| API | 调用点数 |
|-----|---------|
| `local_bh_enable` | 353 |
| `local_bh_disable` | 340 |
| `raise_softirq_irqoff` | 14 |
| `do_softirq` | 6 |
| `__raise_softirq_irqoff` | 12 |
| `open_softirq` | 13 |

---

## tasklet — 构建在 softirq 上的序列化回调

### 定位

tasklet 在 `TASKLET_SOFTIRQ`（或 `HI_SOFTIRQ`）上下文中执行，提供 per-CPU 序列化保证（同一 tasklet 不会在多个 CPU 上并行）。适合短小、原子、不需要睡眠的延迟工作。

> **弃用警告**：`include/linux/interrupt.h:648-650` 注释："This API is deprecated. Please consider using threaded IRQs instead."

### 实现方式

核心结构体（`include/linux/interrupt.h:671-682`）：

```c
struct tasklet_struct {
    struct tasklet_struct *next;       // per-CPU 队列链表指针
    unsigned long state;               // SCHED + RUN 位掩码
    atomic_t count;                    // 禁用计数：0=启用，>0=禁用
    bool use_callback;                 // true=新 API，false=旧 API
    union {
        void (*func)(unsigned long data);              // 旧 API
        void (*callback)(struct tasklet_struct *t);    // 新 API
    };
    unsigned long data;                // 旧 API 数据参数
};
```

**状态机**：
- `TASKLET_STATE_SCHED`（Bit 0）：已排队等待执行
- `TASKLET_STATE_RUN`（Bit 1）：正在执行（SMP 序列化用）

**per-CPU 队列头**（`kernel/softirq.c:757-763`）：

```c
static DEFINE_PER_CPU(struct tasklet_head, tasklet_vec);     // TASKLET_SOFTIRQ
static DEFINE_PER_CPU(struct tasklet_head, tasklet_hi_vec);  // HI_SOFTIRQ
```

**执行流程**（`tasklet_action_common`）：
1. 原子地取走整个 per-CPU 链表（关中断/开中断）
2. 遍历链表：
   - `tasklet_trylock(t)`：设置 RUN 位；已运行则重新入队
   - 检查 `count`：非零（禁用）则重新入队
   - 清除 SCHED 位
   - 调用回调：`t->callback(t)` 或 `t->func(t->data)`
   - 清除 RUN 位

### 主要 API

头文件：`include/linux/interrupt.h`；实现：`kernel/softirq.c`

| API | 说明 | 备注 |
|-----|------|------|
| `tasklet_setup(t, callback)` | 新 API 初始化 | `use_callback = true` |
| `tasklet_init(t, func, data)` | 旧 API 初始化 | `use_callback = false` |
| `from_tasklet(var, t, field)` | container_of 恢复容器 | 新 API 用 |
| `tasklet_schedule(t)` | 排队到 TASKLET_SOFTIRQ | |
| `tasklet_hi_schedule(t)` | 排队到 HI_SOFTIRQ | 更高优先级 |
| `tasklet_disable_nosync(t)` | 增加禁用计数 | 不等待运行完成 |
| `tasklet_disable(t)` | 禁用 + 等待完成 | |
| `tasklet_enable(t)` | 减少禁用计数 | |
| `tasklet_kill(t)` | 等待完成并移出队列 | 不能在中断上下文调用 |

**静态声明宏**：

| 宏 | API 版本 |
|----|----------|
| `DECLARE_TASKLET(name, callback)` | 新 API，启用 |
| `DECLARE_TASKLET_DISABLED(name, callback)` | 新 API，禁用 |
| `DECLARE_TASKLET_OLD(name, func)` | 旧 API，启用 |
| `DECLARE_TASKLET_DISABLED_OLD(name, func)` | 旧 API，禁用 |

### API 迁移

| 方面 | 旧 API | 新 API |
|------|--------|--------|
| 回调签名 | `void (*)(unsigned long data)` | `void (*)(struct tasklet_struct *t)` |
| 初始化 | `tasklet_init(t, func, data)` | `tasklet_setup(t, callback)` |
| 恢复容器 | 手动强制转换 data | `from_tasklet(var, t, field)` |

### tasklet vs workqueue

| 属性 | tasklet | workqueue |
|------|---------|-----------|
| 上下文 | softirq（原子，不能睡眠） | 进程上下文（可睡眠） |
| CPU 绑定 | 仅在排队 CPU 执行 | 可绑定或 unbound |
| 序列化 | 与自身严格串行 | per-work-item 串行 |
| 延迟 | 较低（softirq 上下文） | 较高（内核线程调度） |
| 适用场景 | 短小、原子、不阻塞 | 可能睡眠或耗时较长 |

### 使用规模

| API | 调用点数 |
|-----|---------|
| `tasklet_schedule` | 483 |
| `tasklet_setup` | 224 |
| `tasklet_kill` | 362 |
| `tasklet_disable` | 107 |
| `tasklet_init` | 103 |
| `tasklet_enable` | 85 |
| `tasklet_hi_schedule` | 84 |

## §4 常见陷阱

### 陷阱 1：在中断上下文中调用 tasklet_kill

**错误写法**：
```c
static irqreturn_t my_irq_handler(int irq, void *dev_id)
{
    // 在中断处理程序中杀死 tasklet → 无法睡眠
    tasklet_kill(&my_tasklet);
    return IRQ_HANDLED;
}
```

**正确写法**：
```c
// tasklet_kill 只能在进程上下文调用
// 如果需要在中断中禁用 tasklet，使用 tasklet_disable_nosync
static irqreturn_t my_irq_handler(int irq, void *dev_id)
{
    tasklet_disable_nosync(&my_tasklet);
    return IRQ_HANDLED;
}
// 在进程上下文中再调用 tasklet_kill
```

**原因**：`tasklet_kill` 内部使用 `wait_var_event` 等待 `TASKLET_STATE_SCHED` 位被清除。这需要睡眠能力，而中断上下文无法睡眠。源码中有 `in_interrupt()` 检查，但只打印 `pr_notice` 而非阻止执行——实际仍会进入等待循环，导致死锁。

**源码依据**：`kernel/softirq.c:909-919` — `tasklet_kill` 中 `if (in_interrupt()) pr_notice("Attempt to kill tasklet from interrupt\n")`，之后仍执行 `while (test_and_set_bit(...))` 等待循环。

### 陷阱 2：softirq 回调中修改 preempt_count

**错误写法**：
```c
static void my_softirq_handler(struct softirq_action *a)
{
    // 手动修改抢占计数 → 触发 WARN
    preempt_disable();
    do_something();
    // 忘记 preempt_enable() → preempt_count 泄漏
}
```

**正确写法**：
```c
static void my_softirq_handler(struct softirq_action *a)
{
    // 不要手动操作 preempt_count
    // 如果需要保护临界区，使用 rcu_read_lock 或 spin_lock
    rcu_read_lock();
    do_something();
    rcu_read_unlock();
}
```

**原因**：`handle_softirqs` 在进入和退出每个 softirq 回调后检查 `preempt_count` 是否一致。如果不一致，打印错误信息并强制恢复原来的值。手动修改 `preempt_count` 会破坏这个检查，并且如果忘记恢复，会导致抢占计数泄漏，进而影响调度器的抢占判断。

**源码依据**：`kernel/softirq.c:580-585` — `if (unlikely(prev_count != preempt_count())) { pr_err("huh, entered softirq %u %s %p with preempt_count %08x, exited with %08x?\n", ...); preempt_count_set(prev_count); }`

### 陷阱 3：在 softirq 回调中执行长时间操作

**错误写法**：
```c
static void my_softirq_handler(struct softirq_action *a)
{
    // 处理大量数据，耗时可能超过 2ms
    for (int i = 0; i < 100000; i++) {
        process_packet(&packets[i]);
    }
}
```

**正确写法**：
```c
static void my_softirq_handler(struct softirq_action *a)
{
    // 限制单次处理量，让出 CPU 给其他 softirq 和进程
    int budget = min(remaining, 64);
    for (int i = 0; i < budget; i++) {
        process_packet(&packets[i]);
    }
    if (remaining > 0)
        raise_softirq(MY_SOFTIRQ);  // 重新触发，下次再处理
}
```

**原因**：softirq 处理有时间和次数限制：最多连续执行 `MAX_SOFTIRQ_TIME`（2ms）或 `MAX_SOFTIRQ_RESTART`（10）次。超过限制后，内核唤醒 `ksoftirqd` 线程接管，但在此期间硬中断被阻塞，导致中断延迟增加。如果 softirq 回调本身执行时间过长，会直接拉长单次 softirq 处理时间，影响系统响应性。网络子系统的 NAPI 轮询就是典型例子——它通过 budget 机制限制单次处理量。

**源码依据**：`kernel/softirq.c:499-500` — `#define MAX_SOFTIRQ_TIME msecs_to_jiffies(2)` 和 `#define MAX_SOFTIRQ_RESTART 10`；`kernel/softirq.c:596-601` — 超限后 `wakeup_softirqd()`。

### 陷阱 4：local_bh_disable 后忘记 local_bh_enable

**错误写法**：
```c
void my_function(void)
{
    local_bh_disable();
    // 访问 per-CPU 数据或执行 BH 临界区
    do_something();
    // 提前返回，忘记 local_bh_enable
    if (error)
        return;  // BH 被永久禁用！
    local_bh_enable();
}
```

**正确写法**：
```c
void my_function(void)
{
    local_bh_disable();
    do_something();
    if (error) {
        local_bh_enable();  // 所有退出路径都必须恢复
        return;
    }
    local_bh_enable();
}
```

**原因**：`local_bh_disable` 将 preempt count 增加 `SOFTIRQ_DISABLE_OFFSET`（= `2 * SOFTIRQ_OFFSET`），阻止 softirq 执行和内核抢占。如果忘记恢复，softirq 将永久无法在该 CPU 上执行，导致网络包堆积、定时器延迟等严重问题。`SOFTIRQ_DISABLE_OFFSET` 是 `SOFTIRQ_OFFSET` 的两倍，这使得 lockdep 能区分"正在执行 softirq"和"BH 被手动禁用"两种状态。

**源码依据**：`kernel/softirq.c:91-100` — 注释说明 `SOFTIRQ_OFFSET` 和 `SOFTIRQ_DISABLE_OFFSET` 的区别；`include/linux/interrupt.h` 中 `local_bh_disable` / `local_bh_enable` 定义。

### 陷阱 5：tasklet 回调中调用可能睡眠的函数

**错误写法**：
```c
static void my_tasklet_cb(struct tasklet_struct *t)
{
    // tasklet 在 softirq 上下文中执行，不能睡眠
    mutex_lock(&my_mutex);      // 可能睡眠！
    kmalloc(1024, GFP_KERNEL);  // 可能睡眠！
}
```

**正确写法**：
```c
static void my_tasklet_cb(struct tasklet_struct *t)
{
    // 使用自旋锁和原子分配
    spin_lock(&my_lock);
    do_something();
    spin_unlock(&my_lock);
    // 如果需要分配，用 GFP_ATOMIC
    void *p = kmalloc(1024, GFP_ATOMIC);
}
```

**原因**：tasklet 在 `TASKLET_SOFTIRQ`（或 `HI_SOFTIRQ`）上下文中执行，这是原子上下文，不允许睡眠。任何可能导致调度的操作（mutex_lock、msleep、GFP_KERNEL 分配等）都会触发 "scheduling while atomic" BUG。如果需要睡眠，应该使用 workqueue 替代 tasklet。

**源码依据**：`include/linux/interrupt.h:648-650` — 注释 "This API is deprecated. Please consider using threaded IRQs instead"；tasklet 在 softirq 上下文执行（`kernel/softirq.c:809-850` 中 `tasklet_action_common` 调用回调）。

## §6 取证摘要

**读取的文件**：
- `include/linux/interrupt.h` — softirq 向量定义、tasklet_struct 结构体、tasklet API 声明、弃用警告
- `kernel/softirq.c` — handle_softirqs、tasklet_action_common、tasklet_kill、ksoftirqd、MAX_SOFTIRQ_TIME/RESTART

**关键论断出处**：
- "tasklet_kill 在中断中只打印 notice" — 来源：`kernel/softirq.c:911-912`
- "softirq 回调 preempt_count 检查" — 来源：`kernel/softirq.c:580-585`
- "MAX_SOFTIRQ_TIME = 2ms, MAX_SOFTIRQ_RESTART = 10" — 来源：`kernel/softirq.c:499-500`
- "超限后唤醒 ksoftirqd" — 来源：`kernel/softirq.c:596-601`
- "tasklet API 弃用警告" — 来源：`include/linux/interrupt.h:648-650`
- "SOFTIRQ_DISABLE_OFFSET = 2 * SOFTIRQ_OFFSET" — 来源：`kernel/softirq.c:91-100`
- "softirq_handle_end 中 WARN_ON_ONCE(in_interrupt())" — 来源：`kernel/softirq.c:425`

**未验证项**：
- PREEMPT_RT 配置下 softirq 行为差异（文档以非 RT 路径为主，RT 路径在 `kernel/softirq.c:651-667` 有独立的 timersd 线程）
- `do_softirq_own_stack` 在 arm64 上的栈切换实现细节（`arch/arm64/kernel/irq.c:83-86` 已引用但未深入展开）
