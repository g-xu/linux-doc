# spinlock — 自旋锁

> 内核最基本的互斥原语。不可睡眠的短临界区保护，通过忙等待（自旋）获取锁。arm64 上使用 MCS 队列自旋锁（qspinlock），保证 FIFO 公平性。

---

## §1 核心设计洞察

### 1.1 三层抽象

spinlock 不是一个结构体，而是三层包装：

```
spin_lock() 调用
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  API 层:  spinlock_t                                     │
│           非 RT: 包装 raw_spinlock_t                     │
│           PREEMPT_RT: 包装 rt_mutex_base（可睡眠）        │
├─────────────────────────────────────────────────────────┤
│  抽象层:  raw_spinlock_t                                 │
│           永远是真正的自旋锁，即使 PREEMPT_RT              │
│           内嵌 arch_spinlock_t + 调试字段 + lockdep 映射  │
├─────────────────────────────────────────────────────────┤
│  架构层:  arch_spinlock_t (= qspinlock, arm64)           │
│           32 位原子字：locked(8) + pending(8) + tail(16) │
│           MCS 队列 + pending bit 优化                    │
└─────────────────────────────────────────────────────────┘
```

**为什么需要三层？** PREEMPT_RT 需要把普通 spinlock 变成可睡眠的 rt_mutex，但调度器、中断处理等核心代码不能睡眠——它们使用 `raw_spinlock_t`，保证在所有配置下都是真正的自旋锁。

### 1.2 qspinlock 的 32 位编码

`arch_spinlock_t` 是一个 `atomic_t`（32 位），按字节拆分为（`include/asm-generic/qspinlock_types.h:14-44`）：

```
 31                    16 15    9 8       0
┌──────────────────────┬─────────┬─────────┐
│   tail CPU (+1)      │tail idx │ pending │ locked │
│   (14 或 21 bit)     │ (2 bit) │ (8 bit) │ (8 bit)│
└──────────────────────┴─────────┴─────────┘
```

- **locked**：0 = 未锁，1 = 已锁。快速路径用 `cmpxchg` 设置
- **pending**：允许一个额外的 CPU 在队列形成前"抢占"获取——避免 2 个 CPU 竞争时进入 MCS 慢路径的开销
- **tail**：MCS 队列的尾指针（CPU 号 + 2-bit 槽位索引）。每个 CPU 有 4 个 per-CPU 节点（task/softirq/hardirq/NMI 各一个）

### 1.3 快速路径与慢路径

**快速路径**（无竞争，`include/asm-generic/qspinlock.h:107-115`）：

```c
static __always_inline void queued_spin_lock(struct qspinlock *lock)
{
    int val = 0;
    if (likely(atomic_try_cmpxchg_acquire(&lock->val, &val, _Q_LOCKED_VAL)))
        return;  // 一次 cmpxchg 成功，锁已获取
    queued_spin_lock_slowpath(lock, val);
}
```

**慢路径**（`kernel/locking/qspinlock.c:316-566`）分两阶段：
1. **Pending 阶段**：如果队列中没有等待者，尝试设置 pending bit。成功则自旋等待 locked 清零
2. **MCS 队列阶段**：如果 pending 也被占，将自己的 per-CPU 节点链入 MCS 队列尾部，然后自旋等待前驱节点传递锁（`node->locked = 1`）

**解锁**（`arch_spin_unlock`）：用 `smp_store_release` 清零 locked 字节。如果有 MCS 队列，前驱节点通过写后继节点的 `node->locked` 字段传递锁。

### 1.4 spin_lock 变体的语义差异

| 变体 | 关中断 | 关 softirq | 关抢占 | 适用上下文 |
|------|--------|-----------|--------|-----------|
| `spin_lock` | 否 | 否 | 是 | 进程上下文，不会被中断 handler 访问同一数据 |
| `spin_lock_irq` | 是 | 是 | 是 | 数据也被硬中断 handler 访问 |
| `spin_lock_irqsave` | 是（保存 flags） | 是 | 是 | 同上，但可能在已关中断的上下文中调用 |
| `spin_lock_bh` | 否 | 是（禁止 softirq 执行） | 是 | 数据也被 softirq/tasklet 访问 |

**关键判断**：如果你的数据会被中断 handler 读写，**必须**用 `_irq` 或 `_irqsave` 变体。只用 `spin_lock` 保护的数据如果在中断中被访问，会导致数据竞争。

### 1.5 PREEMPT_RT 下的行为变化

在 `CONFIG_PREEMPT_RT` 下（`include/linux/spinlock_rt.h`）：

| 变体 | 非 RT | PREEMPT_RT |
|------|-------|------------|
| `spin_lock` | 自旋 + 关抢占 | **可睡眠**（rt_mutex）+ 关迁移 |
| `spin_lock_irq` | 自旋 + 关中断 + 关抢占 | 自旋 + **不关中断** + 关抢占 |
| `spin_lock_bh` | 自旋 + 禁 softirq + 关抢占 | 自旋 + 禁 softirq + 关抢占 |
| `raw_spin_lock` | 自旋 + 关抢占 | **不变**——始终自旋 + 关抢占 |

`raw_spinlock_t` 是 PREEMPT_RT 下唯一保证真正自旋的锁类型。

---

## §2 API 速查

### 2.1 初始化

头文件：`include/linux/spinlock.h`；类型定义：`include/linux/spinlock_types.h`、`include/linux/spinlock_types_raw.h`

| API | 说明 | 备注 |
|-----|------|------|
| `DEFINE_SPINLOCK(name)` | 静态定义并初始化 spinlock | 编译期初始化 |
| `spin_lock_init(lock)` | 动态初始化 spinlock | 运行时初始化，带 lockdep class key |
| `DEFINE_RAW_SPINLOCK(name)` | 静态定义 raw_spinlock | |
| `raw_spin_lock_init(lock)` | 动态初始化 raw_spinlock | |

### 2.2 基本锁操作

| API | 说明 | 备注 |
|-----|------|------|
| `spin_lock(lock)` | 获取锁（自旋等待） | 关抢占；不关中断 |
| `spin_unlock(lock)` | 释放锁 | 开抢占 |
| `spin_trylock(lock)` | 尝试获取锁 | 返回 1=成功，0=失败；不自旋 |
| `spin_lock_bh(lock)` | 获取锁 + 禁止 softirq | 数据也被 softirq 访问时用 |
| `spin_unlock_bh(lock)` | 释放锁 + 恢复 softirq | |
| `spin_trylock_bh(lock)` | 尝试获取 + 禁止 softirq | |
| `spin_lock_irq(lock)` | 获取锁 + 关本地中断 | 数据也被硬中断访问时用 |
| `spin_unlock_irq(lock)` | 释放锁 + 开本地中断 | |
| `spin_trylock_irq(lock)` | 尝试获取 + 关中断 | |
| `spin_lock_irqsave(lock, flags)` | 获取锁 + 保存并关中断 | **最安全的变体**；flags 是左值 |
| `spin_unlock_irqrestore(lock, flags)` | 释放锁 + 恢复中断状态 | |
| `spin_trylock_irqsave(lock, flags)` | 尝试获取 + 保存并关中断 | |

### 2.3 嵌套锁（lockdep 注解）

| API | 说明 | 备注 |
|-----|------|------|
| `spin_lock_nested(lock, subclass)` | 获取锁并标注锁类别 | 同一把锁在不同上下文中获取时用 |
| `spin_lock_nest_lock(lock, nest_lock)` | 获取锁并标注外层锁 | 证明持锁顺序不会死锁 |
| `spin_lock_irqsave_nested(lock, flags, subclass)` | irqsave + nested | |

### 2.4 查询

| API | 说明 | 备注 |
|-----|------|------|
| `spin_is_locked(lock)` | 锁是否被持有 | **不提供内存序保证**；仅调试用 |
| `spin_is_contended(lock)` | 是否有等待者 | qspinlock 下检查 tail 是否非零 |
| `assert_spin_locked(lock)` | 断言锁被持有 | `CONFIG_DEBUG_SPINLOCK` 下检查 |

### 2.5 raw_spinlock 等价 API

每个 `spin_*` 函数都有对应的 `raw_spin_*` 版本，语义完全相同但绕过 PREEMPT_RT 的可睡眠替换。常用：`raw_spin_lock`、`raw_spin_unlock`、`raw_spin_lock_irqsave`、`raw_spin_unlock_irqrestore`、`raw_spin_trylock`。

### 2.6 辅助函数

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_dec_and_lock(atomic, lock)` | 原子减 1，到 0 时加锁 | 用于引用计数到 0 时的加锁清理 |
| `atomic_dec_and_lock_irqsave(atomic, lock, &flags)` | 同上 + 关中断 | |

---

## §3 使用模式

### 3.1 基本保护（进程上下文）

```c
static DEFINE_SPINLOCK(my_lock);
static struct my_data shared;

void update_data(void)
{
    spin_lock(&my_lock);
    shared.counter++;
    spin_unlock(&my_lock);
}
```

前提：`shared` 不会被中断 handler 或 softirq 访问。

### 3.2 中断安全保护

```c
static DEFINE_SPINLOCK(irq_lock);
static int irq_counter;

irqreturn_t my_handler(int irq, void *dev)
{
    spin_lock(&irq_lock);
    irq_counter++;
    spin_unlock(&irq_lock);
    return IRQ_HANDLED;
}

void read_counter(void)
{
    unsigned long flags;
    spin_lock_irqsave(&irq_lock, flags);
    // 读取 irq_counter
    spin_unlock_irqrestore(&irq_lock, flags);
}
```

`_irqsave` 是必须的——`read_counter` 可能在进程上下文调用，但 handler 随时可能打断。

### 3.3 trylock 避免死锁

```c
if (!spin_trylock(&lock)) {
    // 锁被其他 CPU 持有，走无锁快速路径或推迟操作
    schedule_work(&deferred_work);
    return;
}
// 成功获取锁
do_work();
spin_unlock(&lock);
```

### 3.4 嵌套锁注解

```c
// 同一把锁在两种上下文中获取——lockdep 需要知道这是安全的
void normal_path(void)
{
    spin_lock_nested(&lock, SINGLE_DEPTH_NESTING);
    // ...
    spin_unlock(&lock);
}

void irq_path(void)
{
    // 不加 nested 注解——中断上下文获取锁是不同类别
    spin_lock(&lock);
    // ...
    spin_unlock(&lock);
}
```

---

## §4 常见陷阱

### 4.1 在中断 handler 中用 spin_lock（而非 _irq）

**错误**：数据同时被进程上下文和中断 handler 访问，但进程上下文只用 `spin_lock`。中断打断时看到锁已持有 → 死锁（单 CPU 自旋等自己释放）或数据损坏。

**正确**：只要数据被中断 handler 访问，进程上下文必须用 `spin_lock_irqsave`。

### 4.2 spin_lock 临界区内睡眠

**错误**：`spin_lock` → `kmalloc(GFP_KERNEL)` → 可能睡眠 → 调度器尝试获取 runqueue 锁 → 死锁。

**正确**：临界区内只用 `GFP_ATOMIC` 分配，不做任何可能睡眠的操作（信号量、mutex、`kmalloc(GFP_KERNEL)`、`wait_event` 等）。

### 4.3 PREEMPT_RT 下 spin_lock 不再关中断

在 PREEMPT_RT 下，`spin_lock` 变成可睡眠锁，**不关中断**。如果代码依赖"持锁期间不会被中断打断"的假设（例如访问 per-CPU 硬件寄存器），在 RT 上会出问题。

**正确**：需要真正关中断时用 `raw_spin_lock_irqsave`，或 `local_irq_save` + `spin_lock`。

### 4.4 spin_is_locked 不是同步原语

`spin_is_locked` 不提供任何内存序保证。返回值在返回的瞬间就可能过时。只用于调试断言（`WARN_ON(!spin_is_locked(&lock))`），不用于控制流。

### 4.5 flags 必须是左值

`spin_lock_irqsave(lock, flags)` 中 `flags` 被赋值（宏展开包含 `flags = _raw_spin_lock_irqsave(lock)`）。传常量或表达式会编译失败。必须声明 `unsigned long flags` 变量。

### 4.6 raw_spinlock_t 的选择

大多数内核代码应该用 `spinlock_t`。只有以下场景需要 `raw_spinlock_t`：
- 调度器内部（`rq->lock`）
- 中断控制器驱动（`irq_desc->lock`）
- timer 子系统
- 其他在 PREEMPT_RT 下不能睡眠的核心路径

滥用 `raw_spinlock_t` 会损害 RT 延迟。

---

## §5 代码示例

### 5.1 设备驱动中的典型 spinlock 用法

```c
struct my_device {
    spinlock_t lock;
    u32 reg_cache;
    struct list_head pending;
};

static int my_probe(struct platform_device *pdev)
{
    struct my_device *dev = devm_kzalloc(&pdev->dev, sizeof(*dev), GFP_KERNEL);
    if (!dev)
        return -ENOMEM;

    spin_lock_init(&dev->lock);
    INIT_LIST_HEAD(&dev->pending);
    // ...
    return 0;
}

// 进程上下文：安全地修改设备状态
int my_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    struct my_device *dev = file->private_data;
    unsigned long flags;

    spin_lock_irqsave(&dev->lock, flags);
    dev->reg_cache = compute_new_value();
    writel(dev->reg_cache, dev->base + REG_OFFSET);
    spin_unlock_irqrestore(&dev->lock, flags);

    return 0;
}

// 中断 handler：也用 irqsave（可能嵌套）
static irqreturn_t my_irq_handler(int irq, void *data)
{
    struct my_device *dev = data;
    unsigned long flags;

    spin_lock_irqsave(&dev->lock, flags);
    // 处理中断，可能操作 pending 列表
    spin_unlock_irqrestore(&dev->lock, flags);

    return IRQ_HANDLED;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| 三层包装关系（spinlock_t → raw_spinlock_t → arch_spinlock_t） | `include/linux/spinlock_types.h:17-29`（非 RT）、`:50-55`（RT） | 一等 |
| qspinlock 32 位编码 | `include/asm-generic/qspinlock_types.h:14-44` | 一等 |
| 快速路径 cmpxchg | `include/asm-generic/qspinlock.h:107-115` | 一等 |
| spin_lock 是 raw_spin_lock 的薄包装 | `include/linux/spinlock.h:349-352` | 一等 |
| PREEMPT_RT 下 spinlock_t 变为 rt_mutex | `include/linux/spinlock_types.h:50-55` | 一等 |
| arm64 使用 qspinlock | `arch/arm64/include/asm/spinlock.h:8` (`#include <asm/qspinlock.h>`) | 一等 |
| arm64 `smp_mb__after_spinlock` = `smp_mb()` | `arch/arm64/include/asm/spinlock.h:12` | 一等 |
| spin_lock_irqsave 内部序列（关中断→关抢占→lockdep→获取锁） | `include/linux/spinlock_api_smp.h:104-113` | 一等 |
| 使用统计 | `grep -rc` 全树计数，6.1.158 | 一等 |
| PREEMPT_RT 语义变化 | `Documentation/locking/locktypes.rst:224-293` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `spin_lock_irqsave` | 16,319 |
| `spin_lock` | 11,045 |
| `spin_lock_bh` | 4,596 |
| `spin_lock_irq` | 3,976 |
| `DEFINE_SPINLOCK` | 1,256 |
| `spin_trylock` | 119 |
| `raw_spin_lock` | 395 |
