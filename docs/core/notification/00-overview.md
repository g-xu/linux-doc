---
title: 总览
---

# 通知与事件 — 总结与对比

---

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **notifier_chain** | 内核内部最通用的"注册-回调"事件分发机制——四种变体适应不同上下文 | 唯一提供四种上下文适配（atomic/blocking/raw/SRCU）的回调分发框架；回调返回 `NOTIFY_BAD` 可否决并停止后续调用；`robust` 变体支持"准备-验证-回滚"原子性 | [notifier-chain.md](notifier-chain.md) |
| **eventfd** | 内核到用户态的轻量计数器式通知——一个 fd 代表一个事件源 | 唯一可在原子上下文（中断/hardirq）中调用的内核→用户态通知机制（`eventfd_signal` 无睡眠）；计数器语义简单（递增/清零），无复杂协议 | [misc-events.md](misc-events.md) |
| **eventpoll** | 用户态大规模 fd 多路复用的内核侧支撑——epoll 的实现 | 唯一在内核侧高效管理大量 fd 等待的机制（O(1) 就绪通知）；驱动不直接调用 eventpoll API，只需实现 `poll` 回调 + `wake_up_locked_poll` | [misc-events.md](misc-events.md) |
| **uevent** | 设备模型到用户态的状态广播——设备添加/移除/绑定通知 | 唯一通过 netlink（`NETLINK_KOBJECT_UEVENT`）向所有监听进程广播设备状态变化的机制；8 种标准事件类型（ADD/REMOVE/CHANGE/MOVE/ONLINE/OFFLINE/BIND/UNBIND） | [misc-events.md](misc-events.md) |
| **fsnotify** | 文件系统事件通知的底层框架——inotify 和 fanotify 的基础 | 唯一同时支撑 inotify（单目录监控）和 fanotify（全文件系统监控+权限控制）的统一框架；mark 可附着在 inode/dentry/vfsmount 上 | [misc-events.md](misc-events.md) |

---

## §2 选型决策

```
需要通知"某事发生了"
│
├── 通知对象是谁？
│   │
│   ├── 内核内部的其他子系统
│   │   │
│   │   ├── 回调可以在中断上下文触发？
│   │   │   ├── 是 → atomic_notifier_chain（spinlock + RCU，不可阻塞）
│   │   │   └── 否（仅进程上下文）
│   │   │       ├── 频繁触发、很少注销 → srcu_notifier_chain（SRCU 读侧极低开销）
│   │   │       ├── 回调需要睡眠 → blocking_notifier_chain（rw_semaphore）
│   │   │       └── 有特殊同步需求 → raw_notifier_chain（无锁，调用者提供）
│   │   │
│   │   └── 需要"准备-验证-回滚"原子性？
│   │       └── → blocking_notifier_call_chain_robust 或 raw_notifier_call_chain_robust
│   │
│   └── 用户态进程
│       │
│       ├── 轻量计数器信号（"某事发生了 N 次"）
│       │   └── → eventfd（内核 eventfd_signal，用户态 read）
│       │
│       ├── 设备状态变化（添加/移除/绑定）
│       │   └── → kobject_uevent（自动通过 netlink 广播，udev 监听）
│       │
│       ├── 文件/目录访问或修改事件
│       │   ├── 只需监控单个目录 → inotify（基于 fsnotify）
│       │   └── 需要监控整个文件系统或权限控制 → fanotify（基于 fsnotify）
│       │
│       └── 大规模 fd 多路复用（等待多个 fd 就绪）
│           └── → epoll（驱动侧只需实现 poll 回调 + wake_up）
│
└── 不需要通知——只是等待条件满足
    └── → 不属于本类别，参见 wait-notify 文档
```

---

## §3 核心对比表

### 3.1 通知方向与传输机制

| 模块 | 方向 | 传输机制 | 典型延迟 | 上下文约束 |
|------|------|---------|---------|-----------|
| notifier_chain | 内核 → 内核 | 直接函数调用（链表遍历） | 纳秒级 | 取决于变体（atomic 可在中断上下文） |
| eventfd | 内核 → 用户态 | 计数器递增 + wait queue 唤醒 | 微秒级（需用户态 read） | `eventfd_signal` 可在原子上下文 |
| eventpoll | 用户态等待 ← 内核状态变化 | wait queue 回调 + 就绪链表 | 微秒级（epoll_wait 返回） | 驱动侧 `wake_up_locked_poll` 可在原子上下文 |
| uevent | 内核 → 用户态 | netlink 广播（`NETLINK_KOBJECT_UEVENT`） | 毫秒级（需用户态守护进程） | 进程上下文 |
| fsnotify | 内核 → 用户态 | 事件队列（inotify/fanotify fd 可读） | 毫秒级（需用户态 read） | VFS 层调用，进程上下文 |

### 3.2 notifier_chain 四种变体对比

| 变体 | 锁机制 | 调用上下文 | 注册/注销开销 | 适用场景 |
|------|--------|-----------|-------------|---------|
| **atomic** | spinlock + RCU | 任意（含中断） | 中等（spinlock 竞争） | CPU hotplug、die 通知 |
| **blocking** | rw_semaphore | 仅进程上下文 | 低（`down_read` 并发注册） | 设备管理、reboot 通知 |
| **SRCU** | SRCU | 仅进程上下文 | 读侧极低，注销慢（`synchronize_srcu`） | 高频触发、低频注销 |
| **raw** | 无锁 | 任意（调用者提供同步） | 零 | 有特殊同步需求的场景 |

### 3.3 用户态通知机制对比

| 维度 | eventfd | uevent | fsnotify (inotify) | fsnotify (fanotify) |
|------|---------|--------|-------------------|-------------------|
| 事件来源 | 内核代码显式调用 | 设备模型状态变化 | 文件/目录访问修改 | 文件/目录访问修改 |
| 传输层 | fd read（计数器） | netlink 广播 | fd read（事件结构） | fd read（事件结构） |
| 监控粒度 | 一个事件源一个 fd | 全局广播 | 单个 inode/dentry | 全文件系统 |
| 权限控制 | 无 | 无 | 无 | 有（fanotify 权限回调） |
| 驱动侧 API | `eventfd_signal` | `kobject_uevent` | VFS 自动调用 | VFS 自动调用 |

### 3.4 等待队列是共同基础

eventfd、eventpoll、fsnotify 底层都依赖 wait queue 实现"事件到达时唤醒等待者"：

- **eventfd**：`wait_queue_head_t wqh` 内嵌在 `struct eventfd_ctx` 中，`eventfd_signal` 调 `wake_up_locked_poll`
- **eventpoll**：每个 `epitem` 通过 `eppoll_entry` 挂在目标文件的 wait queue 上，回调函数 `ep_poll_callback` 将就绪项加入 epoll 就绪链表
- **fsnotify**：`fsnotify_group` 包含 wait queue，事件入队后唤醒 `inotify_read` / `fanotify_read`

### 3.5 RCU 在通知链中的角色

notifier_chain 的四种变体中，注册/注销操作修改链表，调用操作遍历链表。为避免遍历时的锁竞争：
- **atomic** 和 **raw**：链表指针用 `__rcu` 标注，遍历用 `rcu_dereference_raw()`，注册用 `rcu_assign_pointer()`
- **SRCU**：调用路径用 SRCU 读侧保护，注销后调 `synchronize_srcu()` 确保所有读者退出

---

## §4 使用规模

以下数字均为对内核源码树实时 grep 的精确结果。

### 4.1 notifier_chain

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `blocking_notifier_chain_register` | 93 | `grep -r "blocking_notifier_chain_register" --include="*.c"` |
| `atomic_notifier_chain_register` | 82 | `grep -r "atomic_notifier_chain_register" --include="*.c"` |
| `raw_notifier_chain_register` | 25 | `grep -r "raw_notifier_chain_register" --include="*.c"` |
| `srcu_notifier_chain_register` | 9 | `grep -r "srcu_notifier_chain_register" --include="*.c"` |
| 所有 `*_notifier_call_chain` 调用 | 276 | `grep -rE "(atomic\|blocking\|srcu\|raw)_notifier_call_chain" --include="*.c"` |
| `*_NOTIFIER_HEAD` 声明 | 123 | `grep -r "NOTIFIER_HEAD" --include="*.c"` |

### 4.2 eventfd

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `eventfd_signal` | 70 | `grep -r "eventfd_signal" --include="*.c"` |
| `eventfd_ctx_put` | 71 | `grep -r "eventfd_ctx_put" --include="*.c"` |
| `eventfd_fget` | 5 | `grep -r "eventfd_fget" --include="*.c"` |
| `eventfd_ctx_remove` | 6 | `grep -r "eventfd_ctx_remove" --include="*.c"` |

### 4.3 uevent

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `kobject_uevent` | 288 | `grep -r "kobject_uevent" --include="*.c"` |
| `add_uevent_var` | 246 | `grep -r "add_uevent_var" --include="*.c"` |
| `kobject_uevent_env` | 83 | `grep -r "kobject_uevent_env" --include="*.c"` |

### 4.4 fsnotify

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `fanotify` 相关代码 | 395 | `grep -r "fanotify" --include="*.c" fs/` |
| `inotify` 相关代码 | 178 | `grep -r "inotify" --include="*.c" fs/` |
| `fsnotify()` 直接调用 | 7 | `grep -r "fsnotify(" --include="*.c"` |
| `fsnotify_add_mark` | 9 | `grep -r "fsnotify_add_mark" --include="*.c"` |
| `inotify_init` | 17 | `grep -r "inotify_init" --include="*.c"` |

### 4.5 eventpoll (epoll)

| 指标 | 数量 | grep 命令 |
|------|------|----------|
| epoll 相关代码（fs/ 目录） | 126 | `grep -r "epoll" --include="*.c" fs/` |

---

## §5 设计模式速览

### 5.1 返回值语义

| 模块 | 返回值含义 |
|------|-----------|
| notifier_chain | `NOTIFY_DONE`(0) = 不关心；`NOTIFY_OK`(1) = 成功；`NOTIFY_BAD` = 否决并停止；`NOTIFY_STOP` = 成功但停止 |
| eventfd | `eventfd_signal` 返回实际递增量（溢出时 < 请求量） |
| fsnotify | `fsnotify` 返回 0 或错误；fanotify 权限回调可返回允许/拒绝 |

### 5.2 notifier_chain 典型使用模式

```c
/* 1. 定义 notifier head */
static ATOMIC_NOTIFIER_HEAD(my_notifier_list);

/* 2. 注册回调 */
static struct notifier_block my_nb = {
    .notifier_call = my_callback,
};
atomic_notifier_chain_register(&my_notifier_list, &my_nb);

/* 3. 触发通知 */
atomic_notifier_call_chain(&my_notifier_list, event_type, data);

/* 4. 注销 */
atomic_notifier_chain_unregister(&my_notifier_list, &my_nb);
```

### 5.3 eventfd 典型使用模式

```c
/* 内核侧：创建并信号通知 */
struct eventfd_ctx *ctx = eventfd_ctx_fdget(fd);
eventfd_signal(ctx, 1);  /* 可在原子上下文调用 */
eventfd_ctx_put(ctx);

/* 用户态：等待事件 */
uint64_t val;
read(fd, &val, sizeof(val));  /* 阻塞直到 eventfd_signal 被调用 */
```

### 5.4 epoll 驱动侧集成模式

驱动不直接调用 eventpoll API，只需：

```c
/* 1. 实现 poll 回调 */
static __poll_t my_poll(struct file *filp, poll_table *wait)
{
    poll_wait(filp, &my_wait_queue, wait);
    return data_available ? EPOLLIN : 0;
}

/* 2. 数据到达时唤醒 */
wake_up_locked_poll(&my_wait_queue, EPOLLIN);
```

---

## §6 锁模型对比

| 模块 | 主要锁机制 | 保护对象 | 调用上下文约束 |
|------|-----------|---------|--------------|
| **atomic_notifier** | spinlock（注册/注销）+ RCU（调用遍历） | 链表结构 | 调用可在任意上下文（含中断） |
| **blocking_notifier** | rw_semaphore | 链表结构 | 调用在进程上下文（`down_read` 可睡眠） |
| **SRCU_notifier** | SRCU（调用路径）+ mutex（注册/注销） | 链表结构 | 调用在进程上下文；注销需 `synchronize_srcu` |
| **raw_notifier** | 无锁 | — | 调用者自行提供同步 |
| **eventfd** | spinlock（`wqh.lock`） | 计数器 + wait queue | `eventfd_signal` 可在原子上下文 |
| **eventpoll** | spinlock（`ep->lock`）+ mutex（`ep->mtx`） | 就绪链表 + epitem | `wake_up_locked_poll` 可在原子上下文 |
| **uevent** | `uevent_sock_mutex`（mutex） | netlink socket 发送序列化 | 进程上下文 |
| **fsnotify** | `group->notification_lock`（spinlock 或 mutex） | 事件队列 | 事件入队可在原子上下文（VFS 路径） |

**关键差异**：
- **atomic_notifier** 是唯一保证调用路径完全无锁（RCU 读侧）的变体，适合中断上下文
- **eventfd** 的 `eventfd_signal` 使用 `spin_lock_irqsave`，可在硬中断中安全调用
- **blocking_notifier** 的调用路径持 `rw_semaphore` 读锁，不能在中断上下文使用

---

## §7 组合使用模式

### 7.1 notifier_chain + timer 组合

CPU hotplug 通知 + 定时器重配置：当 CPU 上线时，notifier 回调中重新配置 per-CPU 定时器。

实例：`drivers/char/random.c` 使用 `atomic_notifier_chain_register` 注册 CPU 通知，回调中操作 timer。

### 7.2 notifier_chain + workqueue 组合

设备管理通知 + 延迟处理：notifier 回调中 schedule_work，将耗时操作推迟到 workqueue 进程上下文。

实例：`drivers/acpi/apei/ghes.c` 使用 blocking notifier + workqueue 处理硬件错误。

### 7.3 threaded IRQ + workqueue 组合

中断处理拆分：硬中断快速确认 + workqueue 处理后续逻辑。

实例：`drivers/bluetooth/hci_intel.c` 同时使用 `request_threaded_irq` + `schedule_work`。

### 7.4 eventfd + epoll 组合

用户态事件循环：多个 eventfd 挂到 epoll，统一等待：

```c
/* 用户态 */
epoll_ctl(epfd, EPOLL_CTL_ADD, eventfd1, &ev);
epoll_ctl(epfd, EPOLL_CTL_ADD, eventfd2, &ev);
epoll_wait(epfd, events, MAX, -1);
```

内核侧多个子系统各 `eventfd_signal`，用户态一次 `epoll_wait` 获取所有就绪事件。

### 7.5 uevent + device model 自动组合

设备注册自动触发 uevent：

```
device_register() → kobject_add() → kobject_uevent(KOBJ_ADD)
                   → netlink 广播 → udev 规则匹配 → 创建设备节点
```

`kobject_uevent` 全树 288 处调用，大部分由 device model 自动触发，驱动很少直接调用。

### 7.6 fsnotify + VFS 自动组合

VFS 层在文件操作时自动调用 fsnotify：

```
open() → vfs_open() → fsnotify_open()
read() → vfs_read() → fsnotify_access()
write() → vfs_write() → fsnotify_modify()
```

驱动通常不直接使用 fsnotify——VFS 层自动处理。

### 7.7 最容易被类比错的关系

| 容易混淆的对 | 本质区别 |
|-------------|---------|
| notifier_chain vs eventfd | notifier 是内核到内核的回调分发（同步调用链）；eventfd 是内核到用户态的计数器信号（异步，需 read） |
| eventfd vs eventpoll | eventfd 是"一个事件源一个 fd"（计数器语义）；eventpoll 是"一个 fd 监控多个事件源"（多路复用） |
| uevent vs fsnotify | uevent 广播设备模型事件（ADD/REMOVE/BIND），由 udev 处理；fsnotify 广播文件系统事件（open/read/create），由 inotify/fanotify 用户处理 |
| blocking vs SRCU notifier | blocking 用 rw_semaphore 保护调用路径（每次 call 都 `down_read`）；SRCU 用 SRCU 保护（读侧几乎零开销，但注销慢） |

---

## §8 阅读顺序建议

### 路径 A：理解内核内部事件分发

1. [notifier-chain.md](notifier-chain.md) — 四种变体的区别和选择
2. [misc-events.md](misc-events.md) — eventfd 和 epoll 的内核侧集成

### 路径 B：理解用户态事件通知

1. [misc-events.md](misc-events.md) — eventfd、uevent、fsnotify 的完整覆盖
2. [notifier-chain.md](notifier-chain.md) — 对比内核内部通知机制

### 路径 C：理解设备模型事件流

1. [misc-events.md](misc-events.md) — uevent 机制
2. [notifier-chain.md](notifier-chain.md) — blocking notifier 在设备管理中的使用

### 路径 D：理解文件系统监控

1. [misc-events.md](misc-events.md) — fsnotify 框架、inotify/fanotify 区别

---

## §9 文档索引

| 文档 | 覆盖模块 |
|------|---------|
| [notifier-chain.md](notifier-chain.md) | notifier_chain 框架：四种变体（atomic/blocking/raw/SRCU）、数据结构、注册/调用/注销实现、优先级排序、返回值语义、robust 调用、初始化宏、使用场景 |
| [misc-events.md](misc-events.md) | eventfd（内核到用户态计数器通知）、eventpoll（epoll 内核侧集成）、uevent（设备模型到用户态广播）、fsnotify（文件系统事件框架，inotify/fanotify 底层） |
