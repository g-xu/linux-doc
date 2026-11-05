# notifier_chain — 通知链框架

> 内核的事件通知分发机制。允许注册回调函数（notifier_block）到通知链上，当事件发生时依次调用。四种变体适应不同上下文：atomic（中断安全）、blocking（可睡眠）、raw（无锁）、SRCU（低开销读侧）。广泛用于 CPU hotplug、设备管理、reboot、die 通知等跨子系统的回调分发。

---

## 定位

内核中许多事件需要通知多个感兴趣的子系统（如 CPU 上线/下线、设备注册/注销、系统重启）。硬编码调用列表不允许模块动态注册。notifier chain 提供了一个通用的"注册-回调"框架：事件源维护一条链表，事件发生时遍历链表依次调用每个注册的回调。

**核心文件**：
- 头文件：`include/linux/notifier.h`
- 实现：`kernel/notifier.c`

## 核心数据结构

### struct notifier_block（`include/linux/notifier.h:54-58`）

```c
struct notifier_block {
    notifier_fn_t notifier_call;      // 回调函数
    struct notifier_block __rcu *next; // 链表下一个节点（RCU 保护）
    int priority;                      // 优先级（高优先级先调用）
};
```

回调签名（`include/linux/notifier.h:51-52`）：

```c
typedef int (*notifier_fn_t)(struct notifier_block *nb,
                             unsigned long action, void *data);
```

### 四种通知链头

| 类型 | 结构体 | 锁机制 | 回调上下文 | 定义位置 |
|------|--------|--------|-----------|---------|
| Atomic | `struct atomic_notifier_head` | `spinlock_t` + RCU | 中断/原子上下文，不可阻塞 | `include/linux/notifier.h:60-63` |
| Blocking | `struct blocking_notifier_head` | `rw_semaphore` | 进程上下文，可阻塞 | `include/linux/notifier.h:65-68` |
| Raw | `struct raw_notifier_head` | 无（调用者提供） | 无限制 | `include/linux/notifier.h:70-72` |
| SRCU | `struct srcu_notifier_head` | `mutex` + SRCU | 进程上下文，可阻塞 | `include/linux/notifier.h:74-78` |

所有四种的 head 指针都用 `__rcu` 标注，遍历通过 `rcu_dereference_raw()` 进行。

## 回调返回值

定义在 `include/linux/notifier.h:183-191`：

| 常量 | 值 | 含义 |
|------|---|------|
| `NOTIFY_DONE` | `0x0000` | 不关心，继续调用下一个 |
| `NOTIFY_OK` | `0x0001` | 处理成功，继续 |
| `NOTIFY_STOP_MASK` | `0x8000` | 停止标志位，设置后不再调用后续回调 |
| `NOTIFY_BAD` | `NOTIFY_STOP_MASK\|0x0002` | 否决操作，停止链并返回错误 |
| `NOTIFY_STOP` | `NOTIFY_OK\|NOTIFY_STOP_MASK` | 成功但停止后续调用 |

**停止语义**：`notifier_call_chain` 检查返回值是否包含 `NOTIFY_STOP_MASK`，如果有则立即停止遍历（`kernel/notifier.c:92-93`）。

**errno 转换**（`include/linux/notifier.h:194-207`）：
- `notifier_from_errno(err)` — 将 errno 编码进返回值
- `notifier_to_errno(ret)` — 从返回值提取 errno

## 核心实现

### 注册（`kernel/notifier.c:22-41`）

```c
static int notifier_chain_register(struct notifier_block **nl,
                                   struct notifier_block *n,
                                   bool unique_priority)
{
    while ((*nl) != NULL) {
        if (unlikely((*nl) == n)) {
            WARN(1, "notifier callback %ps already registered", n->notifier_call);
            return -EEXIST;
        }
        if (n->priority > (*nl)->priority)
            break;
        if (n->priority == (*nl)->priority && unique_priority)
            return -EBUSY;
        nl = &((*nl)->next);
    }
    n->next = *nl;
    rcu_assign_pointer(*nl, n);
    return 0;
}
```

**优先级排序**：高优先级在前。相同优先级时按注册顺序排列（除非 `unique_priority` 为 true，此时返回 `-EBUSY`）。

**重复注册检测**：如果同一个 `notifier_block` 已经注册，打印警告并返回 `-EEXIST`。

### 调用（`kernel/notifier.c:68-98`）

```c
static int notifier_call_chain(struct notifier_block **nl,
                               unsigned long val, void *v,
                               int nr_to_call, int *nr_calls)
{
    int ret = NOTIFY_DONE;
    struct notifier_block *nb, *next_nb;

    nb = rcu_dereference_raw(*nl);
    while (nb && nr_to_call) {
        next_nb = rcu_dereference_raw(nb->next);
        ret = nb->notifier_call(nb, val, v);
        if (nr_calls) (*nr_calls)++;
        if (ret & NOTIFY_STOP_MASK)
            break;
        nb = next_nb;
        nr_to_call--;
    }
    return ret;
}
```

**RCU 遍历**：所有四种变体最终都调用这个内部函数，通过 `rcu_dereference_raw()` 读取链表指针。各变体的锁只保护注册/注销操作，调用路径本身是无锁的。

### robust 调用（`kernel/notifier.c:116-127`）

`notifier_call_chain_robust` 先调用 `val_up` 事件，如果某个回调返回 `NOTIFY_STOP`，则反向调用 `val_down` 事件进行回滚（只调用到已经成功执行的回调数量）。用于需要原子性"准备-验证"序列的场景。

## 四种变体的 API

### Atomic notifier chain

注册/注销用 `spin_lock_irqsave` 保护，调用用 RCU 保护（无锁）。可从原子上下文调用。

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_notifier_chain_register(nh, nb)` | 注册 | 可在原子上下文调用 |
| `atomic_notifier_chain_register_unique_prio(nh, nb)` | 注册（唯一优先级） | 同优先级返回 `-EBUSY` |
| `atomic_notifier_chain_unregister(nh, nb)` | 注销 | 内部调 `synchronize_rcu()` |
| `atomic_notifier_call_chain(nh, val, v)` | 触发 | RCU 读侧保护 |
| `atomic_notifier_call_chain_is_empty(nh)` | 检查是否为空 | |

### Blocking notifier chain

注册/注销用 `rw_semaphore` 保护，调用用 `down_read` 保护。必须在进程上下文调用。

| API | 说明 | 备注 |
|-----|------|------|
| `blocking_notifier_chain_register(nh, nb)` | 注册 | 进程上下文 |
| `blocking_notifier_chain_register_unique_prio(nh, nb)` | 注册（唯一优先级） | |
| `blocking_notifier_chain_unregister(nh, nb)` | 注销 | |
| `blocking_notifier_call_chain(nh, val, v)` | 触发 | `down_read` 保护 |
| `blocking_notifier_call_chain_robust(nh, val_up, val_down, v)` | 带回滚的触发 | |

**启动期优化**：`system_state == SYSTEM_BOOTING` 时跳过 `down_write()`（`kernel/notifier.c:262-263`），因为此时还没有任务调度。

### Raw notifier chain

无任何内部锁。调用者必须自行提供同步。用于有特殊同步需求的场景。

| API | 说明 | 备注 |
|-----|------|------|
| `raw_notifier_chain_register(nh, nb)` | 注册 | 调用者提供锁 |
| `raw_notifier_chain_unregister(nh, nb)` | 注销 | |
| `raw_notifier_call_chain(nh, val, v)` | 触发 | |
| `raw_notifier_call_chain_robust(nh, val_up, val_down, v)` | 带回滚的触发 | |

### SRCU notifier chain

用 SRCU 保护调用路径。注册用 mutex，注销后调 `synchronize_srcu()`。适合"频繁调用、很少注销"的场景。

| API | 说明 | 备注 |
|-----|------|------|
| `srcu_init_notifier_head(nh)` | 动态初始化 | 必须在使用前调用 |
| `srcu_notifier_chain_register(nh, nb)` | 注册 | 进程上下文 |
| `srcu_notifier_chain_unregister(nh, nb)` | 注销 | 内部调 `synchronize_srcu()`，较慢 |
| `srcu_notifier_call_chain(nh, val, v)` | 触发 | SRCU 读侧保护，开销极低 |
| `srcu_cleanup_notifier_head(name)` | 清理 | 销毁时释放 SRCU 结构 |

## 初始化宏

静态初始化（`include/linux/notifier.h:80-141`）：

| 宏 | 用途 |
|----|------|
| `ATOMIC_NOTIFIER_HEAD(name)` | 声明并初始化 atomic head |
| `BLOCKING_NOTIFIER_HEAD(name)` | 声明并初始化 blocking head |
| `RAW_NOTIFIER_HEAD(name)` | 声明并初始化 raw head |
| `SRCU_NOTIFIER_HEAD(name)` | 声明并初始化 SRCU head |
| `ATOMIC_INIT_NOTIFIER_HEAD(name)` | 运行时初始化（用于动态分配的结构体） |
| `BLOCKING_INIT_NOTIFIER_HEAD(name)` | 运行时初始化 |
| `RAW_INIT_NOTIFIER_HEAD(name)` | 运行时初始化 |

**注意**：SRCU head 必须用 `srcu_init_notifier_head()` 动态初始化，不能用静态宏（因为 SRCU 需要分配 per-CPU 数据）。

## 主要使用者

内核中定义了大量具体的通知链实例，分布在各子系统中：

| 通知链 | 类型 | 位置 | 用途 |
|--------|------|------|------|
| `die_chain` | Atomic | `kernel/notifier.c:578` | die/oops 通知 |
| `reboot_notifier_list` | Blocking | `kernel/notifier.c:15` | 系统重启前通知 |
| CPU notifiers | Atomic+Blocking | `kernel/cpu.c` | CPU hotplug 事件 |
| Netdevice notifiers | Blocking+Atomic | `net/core/dev.c` | 网络设备状态变化 |
| PM notifiers | Blocking+Atomic | `kernel/power/main.c` | 电源管理事件 |
| Memory notifiers | Blocking | `mm/memory.c` | 内存热插拔 |
| Bus notifiers | Blocking | `drivers/base/bus.c` | 设备总线事件 |

## 选型指南

| 场景 | 推荐变体 | 原因 |
|------|---------|------|
| 回调在中断/NMI 上下文执行 | Atomic | spinlock + RCU，不阻塞 |
| 回调需要睡眠（如获取 mutex） | Blocking | rwsem 保护 |
| 频繁调用、极少注销 | SRCU | 调用侧零开销 |
| 已有外部锁保护 | Raw | 避免双重加锁 |

## 使用规模

| 指标 | 数量 |
|------|------|
| `struct notifier_block` 引用 | 2777 处（1168 个文件） |
| `atomic_notifier_chain_register` | 84 处 |
| `blocking_notifier_chain_register` | 90 处 |
| `raw_notifier_chain_register` | 26 处 |
| `srcu_notifier_chain_register` | 11 处 |
| `atomic_notifier_call_chain` | 77 处 |
| `blocking_notifier_call_chain` | 134 处 |
| `raw_notifier_call_chain` | 37 处 |
| `srcu_notifier_call_chain` | 16 处 |
| `NOTIFY_DONE` 返回 | 1119 处 |
| `NOTIFY_OK` 返回 | 529 处 |

Blocking 变体使用最广泛（注册 90 处、调用 134 处），其次是 Atomic（注册 84 处、调用 77 处）。SRCU 变体使用最少，适合其特定的"高频调用、低频注销"场景。

---

## §4 常见陷阱

### 陷阱 1：在原子上下文中调用 blocking notifier

**错误写法**：
```c
/* 在中断处理函数或 spinlock 保护区内 */
spin_lock(&my_lock);
blocking_notifier_call_chain(&my_chain, EVENT_X, data);  /* BUG! */
spin_unlock(&my_lock);
```

**正确写法**：
```c
/* blocking notifier 必须在进程上下文调用 */
blocking_notifier_call_chain(&my_chain, EVENT_X, data);
/* 如果必须在原子上下文中通知，改用 atomic 变体 */
atomic_notifier_call_chain(&my_atomic_chain, EVENT_X, data);
```

**原因**：`blocking_notifier_call_chain()` 内部调用 `down_read(&nh->rwsem)`（`kernel/notifier.c:381`），rw_semaphore 的 down_read 可能睡眠。在原子上下文（中断处理、spinlock 保护区、rcu_read_lock 区间）中睡眠会触发 `BUG: scheduling while atomic`。

**源码依据**：`kernel/notifier.c:380-382` — `down_read(&nh->rwsem)` 在 `blocking_notifier_call_chain` 中。

### 陷阱 2：NOTIFY_BAD 截断后续回调

**错误写法**：
```c
/* 回调 A：优先级 10 */
int callback_a(struct notifier_block *nb, unsigned long action, void *data)
{
    if (some_condition)
        return NOTIFY_BAD;  /* 链立即停止！ */
    return NOTIFY_DONE;
}

/* 回调 B：优先级 5 —— 永远不会被调用 */
int callback_b(struct notifier_block *nb, unsigned long action, void *data)
{
    do_important_cleanup();  /* 被跳过！ */
    return NOTIFY_DONE;
}
```

**正确写法**：
```c
/* 除非确实要否决整个操作，否则返回 NOTIFY_DONE */
int callback_a(struct notifier_block *nb, unsigned long action, void *data)
{
    if (some_condition)
        pr_warn("condition met, but continuing chain\n");
    return NOTIFY_DONE;  /* 让后续回调继续执行 */
}
```

**原因**：`notifier_call_chain()` 在每次回调返回后检查 `ret & NOTIFY_STOP_MASK`（`kernel/notifier.c:92-93`）。`NOTIFY_BAD = NOTIFY_STOP_MASK | 0x0002`，设置了 STOP_MASK 位，导致遍历立即终止。后续所有回调（包括优先级更低的）都不会被执行。如果回调 B 做的是关键的清理工作，它将被跳过。

**源码依据**：`kernel/notifier.c:92-93` — `if (ret & NOTIFY_STOP_MASK) break;`。

### 陷阱 3：SRCU notifier 注销的延迟开销

**错误写法**：
```c
/* 频繁注销/注册 SRCU notifier */
while (handling_events()) {
    srcu_notifier_chain_register(&nh, &nb);
    /* ... 处理一段时间 ... */
    srcu_notifier_chain_unregister(&nh, &nb);  /* 每次都等 synchronize_srcu！ */
}
```

**正确写法**：
```c
/* 长期注册，通过内部状态控制是否响应 */
srcu_notifier_chain_register(&nh, &nb);  /* 注册一次 */
while (handling_events()) {
    /* 在回调内部检查是否应该处理 */
    /* ... */
}
srcu_notifier_chain_unregister(&nh, &nb);  /* 模块卸载时注销一次 */
```

**原因**：`srcu_notifier_chain_unregister()` 在注销后调用 `synchronize_srcu(&nh->srcu)`（`kernel/notifier.c:521`），等待所有进行中的 SRCU 读侧临界区结束。这个等待可能持续数十毫秒。SRCU 变体的设计初衷就是"调用侧零开销，注销侧高开销"（`include/linux/notifier.h:43-46`），频繁注销完全违背了这个设计。

**源码依据**：`kernel/notifier.c:521` — `synchronize_srcu(&nh->srcu)`。

### 陷阱 4：从通知链回调内部注销自身

**错误写法**：
```c
int my_callback(struct notifier_block *nb, unsigned long action, void *data)
{
    if (action == LAST_EVENT) {
        /* 在回调内部注销自己 —— 死锁或内存损坏！ */
        blocking_notifier_chain_unregister(&my_chain, nb);
    }
    return NOTIFY_DONE;
}
```

**正确写法**：
```c
int my_callback(struct notifier_block *nb, unsigned long action, void *data)
{
    if (action == LAST_EVENT) {
        schedule_work(&my_cleanup_work);  /* 推迟到进程上下文 */
    }
    return NOTIFY_DONE;
}
/* 在 work handler 中安全注销 */
static void cleanup_work_fn(struct work_struct *work)
{
    blocking_notifier_chain_unregister(&my_chain, &my_nb);
}
```

**原因**：`include/linux/notifier.h:37-38` 明确声明 `*_unregister()` 不得从通知链回调内部调用。blocking 变体的 `unregister` 需要获取 `down_write(&nh->rwsem)`，而 `call_chain` 持有 `down_read`，同一线程尝试获取写锁会导致死锁。atomic 变体的 `unregister` 调用 `synchronize_rcu()`，在 RCU 读侧临界区内（`rcu_read_lock` 在 `atomic_notifier_call_chain` 中持有）等待会导致死锁。

**源码依据**：`include/linux/notifier.h:37-38` — 注释明确禁止；`kernel/notifier.c:197` — `synchronize_rcu()` 在 unregister 中。

### 陷阱 5：SRCU notifier head 未做动态初始化

**错误写法**：
```c
/* 静态声明后直接使用 */
static struct srcu_notifier_head my_head;
/* 忘记调用 srcu_init_notifier_head() */

int init(void)
{
    srcu_notifier_chain_register(&my_head, &nb);  /* BUG: SRCU 结构未初始化 */
    return 0;
}
```

**正确写法**：
```c
static struct srcu_notifier_head my_head;

int init(void)
{
    srcu_init_notifier_head(&my_head);  /* 必须先初始化 */
    srcu_notifier_chain_register(&my_head, &nb);
    return 0;
}
```

**原因**：`srcu_init_notifier_head()` 调用 `init_srcu_struct()` 分配 per-CPU 数据（`kernel/notifier.c:567-573`）。如果跳过这一步，`srcu_read_lock()` 访问未初始化的 per-CPU 数据会导致空指针解引用或内存损坏。其他三种 head 可以用静态初始化宏（`ATOMIC_NOTIFIER_HEAD` 等），但 SRCU 不行——注释明确写道"srcu_notifier_heads must be cleaned up dynamically"（`include/linux/notifier.h:92`）。

**源码依据**：`kernel/notifier.c:567-573` — `srcu_init_notifier_head` 实现；`include/linux/notifier.h:92` — 注释。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/notifier.h` — 四种 notifier_block 结构体定义、返回值常量、静态初始化宏、注释中的上下文约束
- `kernel/notifier.c` — notifier_chain_register/unregister/call_chain 核心实现、四种变体的注册/注销/调用函数、die_chain 实例

**关键论断出处**：
- "NOTIFY_STOP_MASK 设置后停止遍历" — 来源：`kernel/notifier.c:92-93`
- "atomic unregister 调用 synchronize_rcu()" — 来源：`kernel/notifier.c:197`
- "srcu unregister 调用 synchronize_srcu()" — 来源：`kernel/notifier.c:521`
- "blocking call_chain 使用 down_read" — 来源：`kernel/notifier.c:381`
- "SRCU head 必须动态初始化" — 来源：`include/linux/notifier.h:92`
- "重复注册返回 -EEXIST 并打 WARN" — 来源：`kernel/notifier.c:28-30`
- "启动期 blocking register 跳过 down_write" — 来源：`kernel/notifier.c:262-263`
- "禁止从回调内部注销" — 来源：`include/linux/notifier.h:37-38`

**未验证项**：
- `notifier_call_chain_robust` 的回滚计数精确性（`kernel/notifier.c:122-124` 中 `nr-1` 的边界条件）未找到实际使用者进行交叉验证
