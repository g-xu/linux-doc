# timer 与 hrtimer — 内核定时器

> timer（传统定时器）基于分层时间轮，以 jiffies 为分辨率，在 TIMER_SOFTIRQ 中到期执行；hrtimer（高分辨率定时器）基于红黑树，以纳秒为分辨率，可在硬中断或软中断中到期。两者互补：timer 适合粗粒度超时，hrtimer 适合精确定时和 POSIX timer。

---

## timer — 分层时间轮

### 定位

内核中最广泛使用的定时器机制。以 jiffies 为单位，分辨率取决于 `CONFIG_HZ`（arm64 默认 250 Hz = 4ms）。用于超时检测、看门狗、延迟操作等不需要纳秒精度的场景。

### 实现方式

核心结构体（`include/linux/timer.h:11-24`）：

```c
struct timer_list {
    struct hlist_node entry;           // 哈希链表节点（在轮桶中）
    unsigned long expires;             // 到期时间（jiffies）
    void (*function)(struct timer_list *);  // 回调
    u32 flags;                         // TIMER_DEFERRABLE / PINNED / IRQSAFE
};
```

per-CPU 定时器基（`kernel/time/timer.c:199-214`）：

```c
struct timer_base {
    raw_spinlock_t lock;
    unsigned long clk;                 // 当前轮的"jiffies 时钟"
    unsigned long next_expiry;         // 下一个有待到期定时器的桶
    DECLARE_BITMAP(pending_map, WHEEL_SIZE);  // 非空桶位图
    struct hlist_head vectors[WHEEL_SIZE];    // 桶数组
};
```

**分层时间轮设计**（9 层，HZ=1000 时）：

| 层 | 粒度 | 范围 |
|----|------|------|
| 0 | 1 ms | 0-63 ms |
| 1 | 8 ms | 64-511 ms |
| 2 | 64 ms | 512 ms - ~4 s |
| 3 | 512 ms | ~4 s - ~32 s |
| 4 | ~4 s | ~32 s - ~4 min |
| 5 | ~32 s | ~4 min - ~34 min |
| 6 | ~4 min | ~34 min - ~4 h |
| 7 | ~34 min | ~4 h - ~1 day |
| 8 | ~4 h | ~1 day - ~12 days |

每层 64 个桶，总共 576 个桶。O(1) 插入（计算桶号 + hlist_add_head），到期时跨层收集。

**执行流程**：
1. 定时器中断 → `run_local_timers()` → 检查是否有到期定时器 → 触发 `TIMER_SOFTIRQ`
2. `run_timer_softirq()` → `__run_timers()`：循环 `jiffies >= base->clk`
3. `collect_expired_timers()`：跨层收集到期桶
4. `expire_timers()`：遍历桶中 hlist，调用每个 `timer->function`

### 主要 API

头文件：`include/linux/timer.h`；实现：`kernel/time/timer.c`

| API | 说明 | 备注 |
|-----|------|------|
| `timer_setup(timer, callback, flags)` | 初始化定时器 | 当前 API |
| `from_timer(var, timer_ptr, field)` | container_of 恢复容器结构 | |
| `DEFINE_TIMER(name, fn)` | 静态初始化 | |
| `add_timer(timer)` | 启动定时器（不能已在队） | |
| `add_timer_on(timer, cpu)` | 在指定 CPU 启动 | |
| `mod_timer(timer, expires)` | 修改到期时间（激活或重设） | 最常用 |
| `mod_timer_pending(timer, expires)` | 仅在已排队时修改 | |
| `timer_reduce(timer, expires)` | 只允许缩短 | |
| `timer_pending(timer)` | 检查是否在队 | 无锁 |
| `timer_delete(timer)` | 停用定时器 | 返回 1 表示之前在队 |
| `timer_delete_sync(timer)` | 停用 + 等待回调完成 | |
| `timer_shutdown(timer)` | 停用并阻止重新激活 | 6.x 新增 |

**标志**：
- `TIMER_DEFERRABLE`：不唤醒空闲 CPU
- `TIMER_PINNED`：始终在排队 CPU 到期
- `TIMER_IRQSAFE`：回调执行时关中断

### API 迁移

旧 API（`init_timer` / `setup_timer`）在 6.1 中已**完全移除**。新 API 使用 `timer_setup` + `from_timer`，回调签名从 `void (*)(unsigned long data)` 改为 `void (*)(struct timer_list *t)`。

### 使用规模

| API | 使用次数 |
|-----|---------|
| `struct timer_list` 声明 | 2,662 |
| `timer_setup` | 1,057 |
| `mod_timer` | 1,615 |
| `del_timer_sync` / `timer_delete_sync` | 1,198 |
| `del_timer` / `timer_delete` | 734 |
| `add_timer` | 419 |
| `timer_pending` | 345 |

---

## hrtimer — 高分辨率定时器

### 定位

当需要纳秒级精度时使用（POSIX timer、精确睡眠、实时调度）。基于红黑树而非时间轮，提供精确的到期排序。

### 实现方式

核心结构体（`include/linux/hrtimer.h:118-127`）：

```c
struct hrtimer {
    struct timerqueue_node node;       // 红黑树节点 + expires (ktime_t)
    ktime_t _softexpires;             // 最早到期时间（含 slack）
    enum hrtimer_restart (*function)(struct hrtimer *);  // 回调
    struct hrtimer_clock_base *base;   // per-CPU per-clock 基
    u8 state;                          // INACTIVE(0x00) / ENQUEUED(0x01)
    u8 is_rel;                         // 是否用相对模式启动
    u8 is_soft;                        // 在 softirq 中到期
    u8 is_hard;                        // 在 hardirq 中到期
};
```

回调返回 `HRTIMER_NORESTART`（不重启）或 `HRTIMER_RESTART`（重启）。

**时钟基**（每 CPU 8 个）：

| 基 | 时钟 | 上下文 |
|----|------|--------|
| MONOTONIC | CLOCK_MONOTONIC | hardirq |
| REALTIME | CLOCK_REALTIME | hardirq |
| BOOTTIME | CLOCK_BOOTTIME | hardirq |
| TAI | CLOCK_TAI | hardirq |
| MONOTONIC_SOFT | CLOCK_MONOTONIC | softirq |
| REALTIME_SOFT | CLOCK_REALTIME | softirq |
| BOOTTIME_SOFT | CLOCK_BOOTTIME | softirq |
| TAI_SOFT | CLOCK_TAI | softirq |

**红黑树追踪**：每个时钟基用 `timerqueue_head`（基于 `rb_root_cached`），O(log n) 插入，O(1) 取最早到期（左最节点）。

**定时器模式**（`enum hrtimer_mode`）：

| 模式 | 值 | 含义 |
|------|-----|------|
| `HRTIMER_MODE_ABS` | 0x00 | 绝对到期时间 |
| `HRTIMER_MODE_REL` | 0x01 | 相对当前时间 |
| `HRTIMER_MODE_PINNED` | 0x02 | 绑定到当前 CPU |
| `HRTIMER_MODE_SOFT` | 0x04 | 回调在 softirq 执行 |
| `HRTIMER_MODE_HARD` | 0x08 | 回调在 hardirq 执行 |

可组合：`HRTIMER_MODE_ABS_SOFT`、`HRTIMER_MODE_REL_PINNED_HARD` 等。

**执行流程**：
1. 时钟事件中断 → `hrtimer_interrupt()`
2. 读取当前时间 → 遍历活跃基的红黑树
3. 到期定时器：调用 `function`，处理 `HRTIMER_RESTART`
4. soft 定时器：触发 `HRTIMER_SOFTIRQ`，在 softirq 上下文中处理
5. 重新编程时钟事件设备为下一个到期时间

**ktime_t**：arm64 上 `ktime_t` 是 `s64`（纳秒标量），所有算术运算都是直接整数操作。

### 主要 API

头文件：`include/linux/hrtimer.h`；实现：`kernel/time/hrtimer.c`

| API | 说明 | 备注 |
|-----|------|------|
| `hrtimer_init(timer, clock_id, mode)` | 初始化 | 指定时钟和模式 |
| `hrtimer_init_on_stack(timer, clock_id, mode)` | 栈上初始化 | |
| `hrtimer_start(timer, tim, mode)` | 启动 | 包装 `hrtimer_start_range_ns` |
| `hrtimer_start_range_ns(timer, tim, delta_ns, mode)` | 带 slack 启动 | 核心入队函数 |
| `hrtimer_cancel(timer)` | 取消 + 等待 | 可睡眠 |
| `hrtimer_try_to_cancel(timer)` | 尝试取消 | 不睡眠，返回 1/0/-1 |
| `hrtimer_active(timer)` | 检查是否活跃 | 无锁，seqcount 保护 |
| `hrtimer_callback_running(timer)` | 回调是否正在执行 | |
| `hrtimer_forward(timer, now, interval)` | 前移到下次到期 | 仅在回调中调用 |
| `hrtimer_forward_now(timer, interval)` | 前移用当前时间 | |
| `hrtimer_get_remaining(timer)` | 获取剩余时间 | |
| `hrtimer_is_queued(timer)` | 是否在队 | 无锁 |

### ktime 辅助函数

| 函数 | 说明 |
|------|------|
| `ktime_set(secs, nsecs)` | 从秒+纳秒创建 |
| `ktime_add(lhs, rhs)` | 加 |
| `ktime_sub(lhs, rhs)` | 减 |
| `ktime_add_ns(kt, ns)` | 加纳秒标量 |
| `ktime_add_safe(lhs, rhs)` | 溢出安全加 |
| `ktime_to_ns(kt)` | 转纳秒（arm64 上为恒等操作） |
| `ns_to_ktime(ns)` | 纳秒转 ktime_t |

### 使用规模

| API | 使用次数 |
|-----|---------|
| `struct hrtimer` 声明 | 575 |
| `hrtimer_init` | 169 |
| `hrtimer_start` | 182 |
| `hrtimer_cancel` | 230 |
| `hrtimer_try_to_cancel` | 49 |
| `hrtimer_active` | 45 |
| `hrtimer_forward` | 16 |

---

## 对比

| 特性 | timer | hrtimer |
|------|-------|---------|
| 分辨率 | jiffies（1-10ms） | 纳秒（硬件限制） |
| 数据结构 | 分层轮（576 桶） | 红黑树（每时钟基） |
| 插入代价 | O(1) | O(log n) |
| 到期精度 | 近似（桶粒度） | 精确（rbtree 排序） |
| 时钟基 | 1 个（jiffies） | 4 时钟 × 2 上下文 = 8 |
| 执行上下文 | TIMER_SOFTIRQ | hardirq 或 softirq |
| 回调签名 | `void (*)(struct timer_list *)` | `enum hrtimer_restart (*)(struct hrtimer *)` |
| 重启 | 手动重设 | 返回 `HRTIMER_RESTART` |
| 主要用途 | 超时、延迟工作 | 精确睡眠、POSIX timer |

## arm64 注意事项

- `CONFIG_HIGH_RES_TIMERS=y`（arm64 defconfig 默认开启）
- ARM Generic Timer（`drivers/clocksource/arm_arch_timer.c`）提供硬件时钟事件源
- `ktime_t` 是 `s64`，无 union/struct 开销
- `hrtimer_clock_base` 使用 `____cacheline_aligned`（64 位路径）

## §4 常见陷阱

### 陷阱 1：在硬中断上下文中调用 timer_delete_sync

**错误写法**：
```c
static irqreturn_t my_irq_handler(int irq, void *dev_id)
{
    // 在中断处理程序中同步删除定时器 → 可能死锁
    timer_delete_sync(&my_timer);
    return IRQ_HANDLED;
}
```

**正确写法**：
```c
static irqreturn_t my_irq_handler(int irq, void *dev_id)
{
    // 在中断上下文中只能用非同步版本
    timer_delete(&my_timer);
    return IRQ_HANDLED;
}
```

**原因**：`timer_delete_sync` 需要等待定时器回调执行完成。如果定时器回调正在另一个 CPU 上执行，该函数会自旋等待。在硬中断上下文中，这可能导致死锁——因为中断上下文无法让出 CPU。源码中有明确的 `WARN_ON` 检测这种误用。唯一的例外是标记了 `TIMER_IRQSAFE` 的定时器，其回调在中断关闭时执行，可以安全地从 IRQ 中同步删除。

**源码依据**：`kernel/time/timer.c:1559-1563` — 注释 "don't use it in hardirq context, because it could lead to deadlock" 及 `WARN_ON(in_irq() && !(timer->flags & TIMER_IRQSAFE))`。

### 陷阱 2：对已激活的定时器调用 add_timer

**错误写法**：
```c
timer_setup(&my_timer, my_callback, 0);
my_timer.expires = jiffies + HZ;
add_timer(&my_timer);

// 稍后，未检查是否仍在队
my_timer.expires = jiffies + 2 * HZ;
add_timer(&my_timer);  // WARN_ON! 定时器已在队
```

**正确写法**：
```c
// 修改已激活定时器的到期时间，用 mod_timer
mod_timer(&my_timer, jiffies + 2 * HZ);

// 或者先删再加
timer_delete(&my_timer);
add_timer(&my_timer);
```

**原因**：`add_timer` 设计用于首次激活定时器。如果定时器已在时间轮中（pending），再次调用 `add_timer` 会触发 `WARN_ON_ONCE` 并直接返回，不会修改到期时间。`mod_timer` 才是修改已激活定时器到期时间的正确 API——它同时处理"未激活→激活"和"已激活→修改"两种情况。

**源码依据**：`kernel/time/timer.c:1242` — `if (WARN_ON_ONCE(timer_pending(timer))) return;`

### 陷阱 3：hrtimer_forward 在回调之外调用

**错误写法**：
```c
// 在定时器启动函数中调用 hrtimer_forward
hrtimer_start(&my_timer, ktime_set(1, 0), HRTIMER_MODE_REL);
// 错误：此时不在回调中
hrtimer_forward(&my_timer, ktime_get(), ktime_set(1, 0));
```

**正确写法**：
```c
static enum hrtimer_restart my_hrtimer_cb(struct hrtimer *timer)
{
    // hrtimer_forward 只能在回调中调用
    hrtimer_forward(timer, ktime_get(), ktime_set(1, 0));
    return HRTIMER_RESTART;
    // 或者更简单：直接返回 HRTIMER_RESTART，框架自动重启
}
```

**原因**：`hrtimer_forward` 用于在回调中前移到期时间到下一个周期。它检查定时器是否处于 `HRTIMER_STATE_ENQUEUED` 状态——在回调执行期间，定时器已被从红黑树中取出（`__remove_hrtimer`），所以 `state & HRTIMER_STATE_ENQUEUED` 为假。如果在回调外调用，定时器可能已经在红黑树中，触发 `WARN_ON`。

**源码依据**：`kernel/time/hrtimer.c:1068` — `if (WARN_ON(timer->state & HRTIMER_STATE_ENQUEUED)) return 0;`

### 陷阱 4：hrtimer 回调中返回 HRTIMER_RESTART 但已手动重新入队

**错误写法**：
```c
static enum hrtimer_restart my_cb(struct hrtimer *timer)
{
    // 在回调中手动重新启动定时器
    hrtimer_start(timer, ktime_set(2, 0), HRTIMER_MODE_REL);
    // 又返回 HRTIMER_RESTART → 定时器被入队两次！
    return HRTIMER_RESTART;
}
```

**正确写法**：
```c
static enum hrtimer_restart my_cb(struct hrtimer *timer)
{
    // 方案 A：用 hrtimer_forward + 返回 HRTIMER_RESTART
    hrtimer_forward(timer, ktime_get(), ktime_set(2, 0));
    return HRTIMER_RESTART;

    // 方案 B：手动 hrtimer_start + 返回 HRTIMER_NORESTART
    // hrtimer_start(timer, ktime_set(2, 0), HRTIMER_MODE_REL);
    // return HRTIMER_NORESTART;
}
```

**原因**：回调返回 `HRTIMER_RESTART` 时，框架会检查定时器是否已入队（`timer->state & HRTIMER_STATE_ENQUEUED`）。如果已通过 `hrtimer_start` 手动入队，框架不会再入队一次——但 `hrtimer_start` 已经用新的相对时间重新设置了到期时间，而 `HRTIMER_RESTART` 路径使用旧的 `interval` 值。这导致到期时间混乱。源码中 `__run_hrtimer`（`kernel/time/hrtimer.c:1762-1764`）只在 `restart != HRTIMER_NORESTART && !(timer->state & HRTIMER_STATE_ENQUEUED)` 时才入队。

**源码依据**：`kernel/time/hrtimer.c:1762-1764` — 条件检查 `restart != HRTIMER_NORESTART && !(timer->state & HRTIMER_STATE_ENQUEUED)`。

### 陷阱 5：timer_pending 作为同步手段使用

**错误写法**：
```c
// CPU 0：
mod_timer(&t, jiffies + HZ);
// ...
// 认为 timer_pending 返回 0 就意味着回调不会执行
if (!timer_pending(&t)) {
    // 安全地释放 t 相关的资源
    kfree(resource);
}
```

**正确写法**：
```c
// 必须用 timer_delete_sync 确保回调不在其他 CPU 上执行
timer_delete_sync(&t);
kfree(resource);
```

**原因**：`timer_pending` 只是检查定时器是否在时间轮中（`!hlist_unhashed_lockless(&timer->entry)`）。返回 0 只表示定时器不在队列中——但回调可能正在另一个 CPU 上执行（已从队列取出但尚未完成）。此时释放回调依赖的资源会导致 use-after-free。`timer_pending` 的注释明确要求调用者"ensure serialization wrt. other operations done to this timer"。

**源码依据**：`include/linux/timer.h:160-168` — `timer_pending` 注释 "Callers must ensure serialization wrt. other operations"；实现为 `!hlist_unhashed_lockless(&timer->entry)`。

## §6 取证摘要

**读取的文件**：
- `include/linux/timer.h` — timer_list 结构体、TIMER_* 标志、timer_setup 宏、timer_pending 实现
- `kernel/time/timer.c` — add_timer、timer_delete_sync、__mod_timer、分层时间轮实现
- `include/linux/hrtimer.h` — hrtimer 结构体、hrtimer_clock_base 结构体
- `kernel/time/hrtimer.c` — hrtimer_start_range_ns、__run_hrtimer、hrtimer_forward、hrtimer_try_to_cancel

**关键论断出处**：
- "timer_delete_sync 不能在硬中断中使用" — 来源：`kernel/time/timer.c:1559-1563`
- "add_timer 拒绝已 pending 的定时器" — 来源：`kernel/time/timer.c:1242`
- "hrtimer_forward 检查 ENQUEUED 状态" — 来源：`kernel/time/hrtimer.c:1068`
- "hrtimer 重启条件检查" — 来源：`kernel/time/hrtimer.c:1762-1764`
- "timer_pending 不保证回调不在执行" — 来源：`include/linux/timer.h:160-168`
- "hrtimer_start 检查 function 非空" — 来源：`kernel/time/hrtimer.c:1334`

**未验证项**：
- `timer_shutdown` / `timer_shutdown_sync` 的具体实现细节（6.x 新增 API，源码中存在但未深入分析其与 `timer_delete_sync` 的行为差异）
