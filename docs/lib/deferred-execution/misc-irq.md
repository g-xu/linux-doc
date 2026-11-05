# threaded IRQ 与 irq_work — 中断线程化与自 IPI

> threaded IRQ 将中断处理拆分为硬中断处理器（快速确认）和线程处理器（可睡眠、做重活）；irq_work 通过自 IPI 机制在硬中断退出时执行延迟工作，是 NMI 安全的。

---

## threaded IRQ — 中断处理线程化

### 定位

**功能**：将中断处理拆分为硬中断处理器（快速确认硬件）和线程处理器（可睡眠、做重活），让设备驱动在独立内核线程中完成复杂的中断处理工作，而不需要在硬中断处理器中手动调度 tasklet 或 workqueue。

**实现方式**：核心结构体 `struct irqaction`（`include/linux/interrupt.h:118-132`）包含 `handler`（硬中断处理器）、`thread_fn`（线程处理器）和 `struct task_struct *thread`（内核线程）。硬中断到达时先执行 `handler()`，返回 `IRQ_WAKE_THREAD` 则唤醒线程；`thread_fn()` 在线程上下文中执行（可睡眠、持锁、做 I/O）。`IRQF_ONESHOT`（`0x00002000`）标志使 IRQ 线在线程处理器完成前保持禁用，对电平触发中断至关重要。实现在 `kernel/irq/manage.c`。

**应用场景**：几乎所有需要复杂中断处理的设备驱动——GPIO 扩展器（`drivers/gpio/gpio-pca953x.c`、`gpio-max732x.c`）、LED 触发器（`drivers/leds/trigger/ledtrig-gpio.c`）、邮箱控制器（`drivers/mailbox/rockchip-mailbox.c`）、字符设备（`drivers/gpio/gpiolib-cdev.c`）等。`request_threaded_irq` 839 个文件使用、`devm_request_threaded_irq` 628 个文件使用。

### 核心实现

核心结构体 `irqaction`（`include/linux/interrupt.h:118-132`）：

```c
struct irqaction {
    irq_handler_t handler;           // 硬中断处理器（快速确认）
    irq_handler_t thread_fn;         // 线程处理器（可睡眠）
    struct task_struct *thread;      // 线程处理器的内核线程
    void *dev_id;
    unsigned int irq;
    unsigned int flags;
    unsigned long thread_flags;      // IRQTF_RUNTHREAD 等
    unsigned long thread_mask;       // oneshot 掩码
    struct irqaction *secondary;     // 链接硬中断和线程 action
};
```

**执行流程**：
1. 硬件中断 → `handler()` 在硬中断上下文执行（快速确认硬件）
2. 返回 `IRQ_WAKE_THREAD`：唤醒 `thread` 内核线程
3. `thread_fn()` 在线程上下文执行（可睡眠、持锁、做 I/O）
4. 线程完成后重新进入等待

**IRQF_ONESHOT**（`include/linux/interrupt.h:77`，值 `0x00002000`）：
- 硬中断处理器执行后 IRQ 线保持禁用
- 直到线程处理器完成后才重新启用
- 对电平触发中断至关重要（硬件不会自动清除中断源）
- 如果 `handler` 为 NULL 且指定了 `thread_fn`，自动设置此标志

**线程函数**（`kernel/irq/manage.c:1286-1329`）：

```c
static int irq_thread(void *data)
{
    while (!irq_wait_for_interrupt(action)) {
        irqreturn_t action_ret;
        action_ret = handler_fn(desc, action);  // 调用 thread_fn
        if (action_ret == IRQ_WAKE_THREAD)
            irq_wake_secondary(desc, action);
        wake_threads_waitq(desc);
    }
}
```

### 主要 API

头文件：`include/linux/interrupt.h`；实现：`kernel/irq/manage.c`

| API | 说明 | 备注 |
|-----|------|------|
| `request_threaded_irq(irq, handler, thread_fn, irqflags, name, dev_id)` | 请求线程化 IRQ | handler 可为 NULL |
| `devm_request_threaded_irq(dev, ...)` | 设备管理版本 | 设备移除时自动释放 |
| `request_any_context_irq(irq, handler, flags, name, dev_id)` | 自动选择上下文 | 根据 flags 选硬中断或线程 |

### 使用规模

`request_threaded_irq` 839 个文件、`devm_request_threaded_irq` 628 个文件、`request_any_context_irq` 26 个文件。

---

## irq_work — 自 IPI 延迟执行

### 定位

**功能**：通过处理器自 IPI 机制在硬中断退出时执行延迟工作，是 NMI 安全的。允许在 NMI 或硬中断上下文中将工作推迟到"安全"时刻（`irq_exit()` 时）执行，延迟仅微秒级。

**实现方式**：`struct irq_work`（`include/linux/irq_work.h:17-21`）包含 `struct __call_single_node node`（llist 节点 + 状态标志）和回调函数指针。排队时原子 claim（设置 PENDING 位）、添加到 per-CPU llist（无锁）、非 lazy 工作调用 `arch_irq_work_raise()` 触发处理器自 IPI。执行时在 `irq_exit()` 中遍历 `raised_list` 调用回调。lazy 工作在非 RT 下也在 `irq_exit()` 执行，PREEMPT_RT 下由专用线程处理。状态机：free → claimed → pending → busy → free。实现在 `kernel/irq_work.c`。

**应用场景**：perf 事件（`kernel/events/core.c`）、RCU 回调、printk 日志输出（`kernel/printk/printk.c`）、KFENCE 内存检测（`mm/kfence/core.c`）、DMA fence 同步（`drivers/dma-buf/dma-fence-array.c`、`dma-fence-chain.c`）、i915 GPU 驱动（`drivers/gpu/drm/i915/`）、InfiniBand 驱动（`drivers/infiniband/hw/hns/`）等。`irq_work_queue` 47 个文件使用、`irq_work_sync` 19 个文件使用。

### 核心实现

核心结构体（`include/linux/irq_work.h:17-21`）：

```c
struct irq_work {
    struct __call_single_node node;    // llist 节点 + 状态标志
    void (*func)(struct irq_work *);   // 回调
    struct rcuwait irqwait;            // irq_work_sync 用
};
```

**per-CPU 链表**（`kernel/irq_work.c:25-27`）：

```c
static DEFINE_PER_CPU(struct llist_head, raised_list);  // 立即执行
static DEFINE_PER_CPU(struct llist_head, lazy_list);    // 延迟到下一 tick
```

**排队**（`irq_work_queue`）：
1. 原子 claim（设置 PENDING 位）
2. 添加到 llist（无锁）
3. 非 lazy：调用 `arch_irq_work_raise()` 触发处理器自 IPI
4. lazy：等到下一个 tick

**执行**（`irq_work_run`，在 `irq_exit()` 中调用）：
1. 遍历 `raised_list`，调用每个 `work->func(work)`
2. 非 PREEMPT_RT：也执行 `lazy_list`
3. PREEMPT_RT：lazy 工作由专用线程 `irq_workd` 处理

**状态机**：
```
free → claimed → pending → busy → free/claimed
```

### 主要 API

头文件：`include/linux/irq_work.h`；实现：`kernel/irq_work.c`

| API | 说明 | 备注 |
|-----|------|------|
| `init_irq_work(work, func)` | 初始化 | |
| `irq_work_queue(work)` | 排队到当前 CPU | NMI 安全 |
| `irq_work_queue_on(work, cpu)` | 排队到指定 CPU | |
| `irq_work_sync(work)` | 等待回调完成 | |
| `irq_work_busy(work)` | 检查是否正在执行 | |

### 使用规模

`irq_work_queue` 47 个文件、`irq_work_sync` 19 个文件。主要用户：perf 事件、RCU、调度器、tick 管理。

---

## 对比

| 特性 | threaded IRQ | irq_work |
|------|-------------|----------|
| 上下文 | 独立内核线程 | hardirq 退出时 |
| 可睡眠 | 是 | 否 |
| 延迟 | 线程调度延迟 | 自 IPI 延迟（微秒级） |
| NMI 安全 | 不适用 | 是 |
| 用途 | 慢速中断处理 | 原子上下文中的延迟工作 |
