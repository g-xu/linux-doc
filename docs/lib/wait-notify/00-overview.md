---
title: 总览
---

# 等待与通知机制 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **wait_queue** | 通用等待/唤醒原语 | 所有等待机制的基础，支持排他/非排他唤醒、自定义回调、中断/不可中断睡眠 | [wait-queue.md](wait-queue.md) |
| **completion** | 一次性事件同步 | 一个线程等待某个操作完成，底层用 swait 实现，done 计数器决定行为 | [completion.md](completion.md) |
| **swait** | 简单等待队列 | PREEMPT_RT 安全的简化版 wait_queue，用 `raw_spinlock_t`，全部排他唤醒 | [misc-wait.md](misc-wait.md) §1 |
| **rcuwait** | RCU 保护的单任务等待 | 最轻量——只支持 1 个等待者，无锁（RCU 保护 task 指针），用于 CPU hotplug / futex PI | [misc-wait.md](misc-wait.md) §2 |
| **wait_bit** | 位等待队列 | 等待某个内存字中的特定位被清除，哈希表映射 `(word,bit)` → 256 个全局队列 | [misc-wait.md](misc-wait.md) §3 |

---

## §2 选型决策

```
需要等待什么？
│
├─ 等待某个条件成立（通用场景）
│   ├─ 需要排他唤醒（避免惊群） ──── wait_event_exclusive()
│   ├─ 需要可中断 ───────────────── wait_event_interruptible()
│   ├─ 需要超时 ─────────────────── wait_event_timeout()
│   ├─ 需要可杀死 ───────────────── wait_event_killable()
│   └─ 通用场景 ─────────────────── wait_event()
│
├─ 等待一次性事件完成
│   └────────────────────────────── completion
│       ├─ 一个等待者 ───────────── complete() / wait_for_completion()
│       └─ 多个等待者 ───────────── complete_all() / wait_for_completion()
│
├─ 等待某个位被清除
│   ├─ 页锁 / 缓冲区锁 ─────────── wait_on_bit()
│   ├─ 需要位锁（等待并设置位） ──── wait_on_bit_lock()
│   └─ 等待任意变量变化 ─────────── wait_var_event()
│
├─ RT 关键路径上的等待
│   ├─ 只需要一个等待者 ─────────── rcuwait
│   └─ 需要多个等待者 ───────────── swait
│
└─ 最轻量级（单等待者 + 无锁）
    └────────────────────────────── rcuwait
```

**关键区分**：

| 问题 | 选择 |
|------|------|
| 需要自定义唤醒回调？ | wait_queue（支持）vs swait/rcuwait（不支持） |
| 需要排他 + 非排他混合？ | wait_queue（支持）vs swait（全部排他） |
| 需要 RT 安全？ | swait（`raw_spinlock_t`）vs wait_queue（`spinlock_t`，RT 下可睡眠） |
| 只有一个等待者？ | rcuwait（无锁，最轻量）vs swait（支持多个） |
| 等待位 vs 等待条件？ | wait_bit（位操作）vs wait_queue（通用条件） |
| 一次性事件 vs 循环条件？ | completion（done 计数器）vs wait_event（循环检查） |

---

## §3 核心对比表

### 3.1 功能维度对比

| 维度 | wait_queue | swait | rcuwait | wait_bit | completion |
|------|-----------|-------|---------|---------|-----------|
| 等待者数量 | 多个 | 多个 | 仅 1 个 | 多个 | 多个 |
| 排他/非排他 | 支持混合 | 全部排他 | N/A（仅 1 个） | 自定义 | N/A |
| 自定义回调 | 支持 | 不支持 | 不支持 | 自定义唤醒函数 | 不支持 |
| 锁类型 | `spinlock_t` | `raw_spinlock_t` | 无锁（RCU） | `spinlock_t`（哈希桶） | 继承 swait |
| RT 安全 | RT 下可睡眠 | 真正自旋（有界） | 天然安全 | RT 下可睡眠 | 继承 swait |
| 中断模式 | 可中断/不可中断/可杀死 | 可中断/不可中断 | 可配置 | 可中断/不可中断 | 可中断/不可中断/可杀死 |
| 超时支持 | `wait_event_timeout()` | 无内置超时 | 无内置超时 | 无内置超时 | `wait_for_completion_timeout()` |

### 3.2 代码规模

| 模块 | 核心文件 | 行数 |
|------|---------|------|
| wait_queue | `kernel/sched/wait.c` | 486 |
| swait | `kernel/sched/swait.c` | 144 |
| rcuwait | `kernel/exit.c`（`rcuwait_wake_up`） | ~30 |
| wait_bit | `kernel/sched/wait.c`（内嵌） | ~200 |
| completion | `kernel/sched/completion.c` | 343 |

---

## §4 使用规模

以下数字由 `grep` 在 6.1.y 全树上统计（`--include='*.c'`）：

### 4.1 wait_queue 生态

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `wait_event()` | 480 | `grep -r '\bwait_event(' --include='*.c' \| wc -l` |
| `wait_event_interruptible()` | 412 | `grep -r '\bwait_event_interruptible(' --include='*.c' \| wc -l` |
| `wait_event_timeout()` | 420 | `grep -r '\bwait_event_timeout(' --include='*.c' \| wc -l` |
| `wait_event_killable()` | 22 | `grep -r '\bwait_event_killable(' --include='*.c' \| wc -l` |
| `prepare_to_wait()` | 201 | `grep -r '\bprepare_to_wait\b' --include='*.c' \| wc -l` |
| `wake_up()` | 1620 | `grep -r '\bwake_up(' --include='*.c' \| wc -l` |
| `wake_up_all()` | 360 | `grep -r '\bwake_up_all\b' --include='*.c' \| wc -l` |
| `DECLARE_WAIT_QUEUE_HEAD` | 145 | `grep -r 'DECLARE_WAIT_QUEUE_HEAD\b' --include='*.c' --include='*.h' \| wc -l` |
| `init_waitqueue_head()` | 1397 | `grep -r 'init_waitqueue_head\b' --include='*.c' \| wc -l` |

### 4.2 特化变体

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `swake_up_one()` | 8 | `grep -r '\bswake_up_one\b' --include='*.c' \| wc -l` |
| `swake_up_all()` | 3 | `grep -r '\bswake_up_all\b' --include='*.c' \| wc -l` |
| `init_swait_queue_head()` | 7 | `grep -r 'init_swait_queue_head\b' --include='*.c' \| wc -l` |
| `rcuwait_wait_event()` | 10 | `grep -r '\brcuwait_wait_event\b' --include='*.c' \| wc -l` |
| `rcuwait_wake_up()` | 15 | `grep -r '\brcuwait_wake_up\b' --include='*.c' \| wc -l` |
| `wait_on_bit()` | 52 | `grep -r '\bwait_on_bit\b' --include='*.c' \| wc -l` |
| `wake_up_bit()` | 94 | `grep -r '\bwake_up_bit\b' --include='*.c' \| wc -l` |

### 4.3 completion

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `wait_for_completion()` | 800 | `grep -r '\bwait_for_completion\b' --include='*.c' \| wc -l` |
| `complete()` | 2252 | `grep -r '\bcomplete(' --include='*.c' \| wc -l` |
| `complete_all()` | 162 | `grep -r '\bcomplete_all\b' --include='*.c' \| wc -l` |
| `init_completion()` | 2299 | `grep -r 'init_completion\b' --include='*.c' \| wc -l` |
| `DECLARE_COMPLETION` | 54 | `grep -r 'DECLARE_COMPLETION\b' --include='*.c' --include='*.h' \| wc -l` |

---

## §5 设计模式速览

### 5.1 层次关系

```
wait_queue（基础）
    │
    ├── completion（底层用 swait）
    │
    ├── swait（RT 安全的简化版 wait_queue）
    │
    ├── rcuwait（无锁单等待者，与 wait_queue 互补）
    │
    └── wait_bit（基于 wait_queue 的上层封装）
            │
            └── wait_var（复用 wait_bit 基础设施）
```

### 5.2 等待循环模式

所有等待机制共享同一个基本模式：

```c
// 1. 准备等待（注册到等待队列）
prepare_to_wait(&wq_head, &entry, state);

// 2. 检查条件
if (condition)
    break;

// 3. 调度出去
schedule();

// 4. 被唤醒后重复 2-3，直到条件成立

// 5. 结束等待（从队列移除）
finish_wait(&wq_head, &entry);
```

`wait_event*()` 宏、`swait_event*()`、`rcuwait_wait_event()` 都是这个模式的封装。

### 5.3 排他 vs 非排他唤醒

wait_queue 的核心设计：
- **非排他等待者**（头部插入）：唤醒时全部唤醒
- **排他等待者**（尾部插入）：唤醒时只唤醒 N 个

这解决了"惊群"问题——当只有一个生产者时，唤醒 100 个等待者只有 1 个能获取数据，其余 99 个白白被调度又睡回去。

### 5.4 内存序协议

所有等待机制都依赖成对的内存屏障：

```
   WAITER                          WAKER
   [S] 注册到等待队列               [S] 设置条件
       MB (A)                          MB (B)
   [L] 检查条件                     [L] 读取等待者指针
```

屏障 A（`set_current_state` 内部）和屏障 B（`wake_up` 内部）配对，保证不会出现"条件已设置但唤醒方看不到等待者"或"等待者已注册但看不到条件"的竞态。

### 5.5 completion 的 done 计数器

completion 使用 `done` 计数器区分行为：
- `done == 0`：等待者睡眠
- `complete()`：`done++`，唤醒一个等待者
- `complete_all()`：`done = INT_MAX`，唤醒所有等待者
- `wait_for_completion()`：循环等待直到 `done > 0`，然后 `done--`
- `reinit_completion()`：重置 `done = 0`，允许重复使用

---

## §6 锁模型对比

| 模块 | 锁机制 | RT 安全性 | 关键约束 |
|------|--------|----------|---------|
| **wait_queue** | `spinlock_t` 保护队列链表 | PREEMPT_RT 下 `spinlock_t` 变为可睡眠 mutex，锁持有时间无界 | 不能在持有 `spinlock_t` 时调用 `wake_up()`（RT 下可能睡眠） |
| **swait** | `raw_spinlock_t` 保护队列链表 | `raw_spinlock_t` 在 PREEMPT_RT 下仍真正自旋，有界锁持有时间 | `swake_up_all()` 每唤醒一个 waiter 释放/重获锁（`kernel/sched/swait.c:77-78`），保证有界持锁 |
| **rcuwait** | 无锁（RCU 保护 `task` 指针） | 天然 RT 安全 | 只支持 1 个等待者；`rcu_assign_pointer` 设置 task，`rcu_dereference` 读取 |
| **wait_bit** | `spinlock_t` 保护哈希桶 | 与 wait_queue 相同 | 256 个全局哈希桶，`(word, bit)` 哈希映射 |
| **completion** | 继承 swait 的 `raw_spinlock_t` | 继承 swait 的 RT 安全性 | `wait_for_completion()` 内部调用 `swait_event*()` |

**RT 安全适配总结**：
- wait_queue → `spinlock_t` → RT 下可睡眠（不适合 RT 关键路径）
- swait → `raw_spinlock_t` → RT 下真正自旋（适合 RT 关键路径）
- rcuwait → 无锁 → 天然 RT 安全（最轻量）
- completion → 用 swait → 继承 RT 安全性

---

## §7 组合使用模式

### 7.1 completion + swait（RT 安全的一次性同步）

completion 内部使用 swait 实现（`kernel/sched/completion.c` 中 4 处 swait 引用）：
1. `wait_for_completion()` 内部调用 `swait_event_exclusive()`
2. `complete()` 内部调用 `swake_up_one()`
3. `complete_all()` 内部调用 `swake_up_all()`
4. 这使 completion 在 PREEMPT_RT 内核中也是安全的

### 7.2 wait_queue + IPC

所有 IPC 机制都依赖 wait_queue 实现阻塞/唤醒：
- Pipe：`rd_wait` / `wr_wait` 等待管道空/满
- Unix socket：`sk_sleep` 等待数据到达
- mqueue：`wait_q` 等待消息
- Relay：`read_wait` 等待新数据

### 7.3 wait_bit + 页锁

wait_bit 是页锁（PageLocked）和缓冲区锁的基础：
- `wait_on_bit()`（52 处调用）等待页锁被清除
- `wake_up_bit()`（94 处调用）清除位后唤醒等待者
- `wait_var_event()` / `wake_up_var()` 是 wait_bit 的泛化（`bit_nr = -1`）

### 7.4 rcuwait + CPU hotplug

rcuwait 用于 CPU hotplug 等待（`rq->hotplug_wait`）：
1. 只有 1 个等待者（CPU hotplug 操作是串行的）
2. 对锁开销极度敏感（hotplug 路径不能引入额外锁）
3. rcuwait 的零锁设计使其成为最优选择

### 7.5 wait_queue + fault-injection

fault-injection 框架使用 wait_queue 实现等待注入点：
- `should_fail()` 检查是否应该注入故障
- 注入决策后通过 wake_up 唤醒等待者

---

## §8 阅读顺序建议

### 路径 A：从零开始理解等待机制

1. **wait_queue** → 理解基础：prepare_to_wait / schedule / finish_wait 循环
2. **completion** → 理解一次性事件同步
3. **swait** → 理解 RT 安全适配
4. **rcuwait** → 理解无锁单等待者
5. **wait_bit** → 理解位等待和页锁

### 路径 B：排查并发问题

1. **wait_queue** → 理解内存序协议（屏障 A/B 配对）
2. **swait** → 理解 RT 下的锁持有时间
3. **rcuwait** → 理解 RCU 保护的 task 指针

### 路径 C：驱动开发

1. **wait_queue** → `wait_event_interruptible()` 等待硬件中断
2. **completion** → 等待初始化完成
3. **wait_bit** → 等待硬件寄存器位变化

### 路径 D：RT 内核开发

1. **swait** → `raw_spinlock_t` 保证有界锁持有
2. **rcuwait** → 无锁设计
3. **completion** → 继承 swait 的 RT 安全性

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 | 代码依据 |
|-------------|----------|---------|
| wait_queue vs swait | wait_queue 功能完整（自定义回调、排他/非排他）；swait 简化版（RT 安全、全部排他） | `kernel/sched/wait.c` vs `kernel/sched/swait.c` |
| wait_queue vs rcuwait | wait_queue 支持多等待者；rcuwait 只支持 1 个等待者但无锁 | `include/linux/wait.h` vs `include/linux/rcuwait.h:16-18` |
| completion vs wait_event | completion 是一次性事件同步（done 计数器）；wait_event 是条件等待（循环检查） | `kernel/sched/completion.c` vs `kernel/sched/wait.c` |
| wait_bit vs wait_var | wait_bit 等待特定位被清除；wait_var 等待任意变量变化（`bit_nr = -1`） | `kernel/sched/wait.c` 中 `wait_var_event` 实现 |
| swait vs rcuwait | swait 支持多等待者（用 `raw_spinlock` 保护）；rcuwait 只支持 1 个等待者（无锁） | `kernel/sched/swait.c` vs `kernel/exit.c:288` |
| swake_up_one vs swake_up_all | swake_up_one 只唤醒一个；swake_up_all 唤醒所有（分段释放锁保证有界持锁） | `kernel/sched/swait.c:47` vs `kernel/sched/swait.c:61` |

---

## §10 文档索引

| 文档 | 覆盖模块 | 路径 |
|------|----------|------|
| wait_queue 等待队列 | wait_queue、wait_event*、wake_up* | `output/wait-notify/wait-queue.md` |
| completion 事件同步 | completion、wait_for_completion、complete | `output/wait-notify/completion.md` |
| 等待变体 | swait / rcuwait / wait_bit | `output/wait-notify/misc-wait.md` |
