---
title: 总览
---

# 工作延迟与调度 — 总结与对比

---

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **softirq** | 10 个固定优先级的延迟向量——硬中断退出时立即执行 | 唯一在硬中断退出路径上以最高优先级执行的延迟机制；同类型可嵌套；适合网络包处理等高频低延迟场景 | [softirq.md](softirq.md) |
| **tasklet** | per-CPU 序列化的延迟回调——构建在 softirq 之上 | 唯一保证同一 tasklet 不会在多个 CPU 上并行执行的 softirq 封装；**已标记弃用**，推荐 threaded IRQ | [softirq.md](softirq.md) |
| **timer** | 分层时间轮定时器——粗粒度超时（jiffies 分辨率） | 唯一使用 9 层 576 桶时间轮实现 O(1) 插入的定时器；分辨率 = 1/HZ（arm64 默认 4ms）；在 TIMER_SOFTIRQ 中到期 | [timer.md](timer.md) |
| **hrtimer** | 高分辨率定时器——纳秒精度 | 唯一使用红黑树实现纳秒分辨率的定时器；可在 hardirq 或 softirq 中到期；POSIX timer 和精确睡眠的基础 | [timer.md](timer.md) |
| **workqueue** | 进程上下文延迟执行框架——可睡眠的推迟工作 | 唯一在进程上下文执行（可睡眠/持锁/做 I/O）的延迟机制；per-CPU 池并发管理；支持 unbound/ordered/freezable 等多种模式 | [workqueue.md](workqueue.md) |
| **delayed_work** | timer + workqueue 组合——"过一段时间后做某事" | 唯一将 timer（延迟触发）和 workqueue（进程上下文执行）组合的机制；timer 到期后自动将 work 排入 workqueue | [misc-work.md](misc-work.md) |
| **threaded IRQ** | 中断处理线程化——硬中断快速确认 + 线程处理器可睡眠处理 | 唯一将中断处理拆分为原子硬中断处理器和可睡眠线程处理器的机制；`IRQF_ONESHOT` 保持 IRQ 线禁用直到线程完成 | [misc-irq.md](misc-irq.md) |
| **irq_work** | 自 IPI 延迟执行——NMI 安全的延迟回调 | 唯一在 NMI 上下文中安全的延迟执行机制；通过处理器自 IPI 在 `irq_exit()` 时执行；perf 事件、RCU、调度器使用 | [misc-irq.md](misc-irq.md) |
| **kthread** | 内核线程创建 API——持续运行的后台任务 | 唯一创建独立内核线程（无用户空间地址空间）的机制；进程上下文（可睡眠）；需自行实现停止检查循环 | [misc-thread.md](misc-thread.md) |

---

## §2 选型决策

```
需要推迟执行某些工作？
│
├─ 工作可以睡眠（需要持锁/做 I/O/等待）？
│  │
│  ├─ 是 → 是"执行一次"还是"持续运行"？
│  │       │
│  │       ├─ "持续运行"（后台守护）→ kthread
│  │       │        （独立内核线程、自行管理循环）
│  │       │
│  │       └─ "执行一次" → 需要延迟一段时间？
│  │               │
│  │               ├─ 是 → delayed_work
│  │               │        （timer + workqueue，指定延迟后执行）
│  │               │
│  │               └─ 否 → workqueue
│  │                        （立即排队，进程上下文执行）
│  │
│  └─ 是中断处理的一部分？
│          │
│          └─ threaded IRQ
│               （handler 快速确认 + thread_fn 可睡眠处理）
│
├─ 工作不能睡眠（原子上下文）？
│  │
│  ├─ 是 → 延迟容忍度？
│  │       │
│  │       ├─ "立即"（微秒级）→ 是 NMI 上下文？
│  │       │       │
│  │       │       ├─ 是 → irq_work
│  │       │       │        （自 IPI，NMI 安全，irq_exit 时执行）
│  │       │       │
│  │       │       └─ 否 → softirq（如果有对应向量）
│  │       │                或 tasklet（已弃用，考虑 threaded IRQ）
│  │       │
│  │       ├─ "一段时间后" → 需要纳秒精度？
│  │       │       │
│  │       │       ├─ 是 → hrtimer
│  │       │       │        （红黑树，纳秒分辨率）
│  │       │       │
│  │       │       └─ 否 → timer
│  │       │                （时间轮，jiffies 分辨率，O(1) 插入）
│  │       │
│  │       └─ "不急" → timer（TIMER_DEFERRABLE）
│  │                        （不唤醒空闲 CPU，节能）
│  │
│  └─ 是设备中断处理？
│          │
│          └─ 处理需要多少时间？
│                  │
│                  ├─ 快速（< 10μs）→ 传统 hardirq handler
│                  │
│                  └─ 慢速（需读寄存器/I/O）→ threaded IRQ
│
└─ 需要精确的定时触发？
        │
        ├─ 纳秒精度 → hrtimer
        └─ 毫秒精度 → timer
```

---

## §3 核心对比表

### 3.1 执行上下文频谱

```
紧迫度:  高 ←──────────────────────────────────────→ 低

         softirq    irq_work   tasklet   timer    workqueue  delayed_work  kthread
                    (自IPI)             (hrtimer)
上下文:  原子       原子       原子     原子/软    进程       进程         进程
可睡眠:  否         否         否       否        是         是           是
延迟:    微秒       微秒       微秒     jiffies   调度延迟   调度+延迟    调度延迟
NMI安全: 否*        是         否       否        否         否           否
```

*softirq 不在 NMI 上下文执行，但在 NMI 退出后的 irq_exit 中执行。

### 3.2 定时器对比

| 维度 | timer | hrtimer |
|------|-------|---------|
| 分辨率 | 1/HZ（arm64 默认 4ms） | 纳秒 |
| 数据结构 | 9 层 576 桶时间轮 | 红黑树 |
| 插入复杂度 | O(1) | O(log n) |
| 到期上下文 | TIMER_SOFTIRQ（softirq） | hardirq 或 softirq（可配置） |
| 时钟基 | jiffies | 8 个时钟基（MONOTONIC/REALTIME/BOOTTIME/TAI 等） |
| 节能模式 | `TIMER_DEFERRABLE`（不唤醒空闲 CPU） | `HRTIMER_MODE_SOFT`（softirq 到期） |
| API | `timer_setup` + `mod_timer` | `hrtimer_init` + `hrtimer_start` |

### 3.3 进程上下文延迟机制对比

| 维度 | workqueue | delayed_work | kthread |
|------|-----------|-------------|---------|
| 本质 | 工作排队 → 工作线程执行 | timer + workqueue 组合 | 独立内核线程 |
| 可睡眠 | 是 | 是 | 是 |
| 延迟 | 无（立即排队） | 有（timer 到期后排队） | 无（持续运行） |
| 并发 | per-CPU 池自动管理 | 继承 workqueue 并发管理 | 调用者自行管理 |
| 停止 | `cancel_work_sync` | `cancel_delayed_work_sync` | `kthread_stop` + `kthread_should_stop` |
| 典型用途 | 推迟非紧急处理 | 超时重试、延迟状态变化 | 后台守护、per-CPU 工作线程 |

### 3.4 中断退出路径

所有延迟机制最终都在中断退出路径上被触发：

```
硬中断入口 → handler() / irq_desc 处理
    │
    ├─ raise_softirq() → 设置 per-CPU 待处理位
    ├─ irq_work_queue() → 触发处理器自 IPI
    │
    └─ irq_exit_rcu()
         │
         ├─ __do_softirq()（如果 local_softirq_pending()）
         │    ├─ TIMER_SOFTIRQ → timer 到期
         │    ├─ NET_RX/TX_SOFTIRQ → 网络处理
         │    ├─ TASKLET_SOFTIRQ → tasklet 执行
         │    ├─ HRTIMER_SOFTIRQ → hrtimer 到期
         │    └─ RCU_SOFTIRQ → RCU 回调
         │
         └─ irq_work_run()（raised_list）
              └─ 执行 irq_work 回调
```

### 3.5 per-CPU 设计

几乎所有延迟机制都使用 per-CPU 数据结构：

| 模块 | per-CPU 数据 | 目的 |
|------|-------------|------|
| softirq | `softirq_vec[]`、待处理位图 | 避免跨 CPU 锁 |
| tasklet | `tasklet_vec`、`tasklet_hi_vec` | per-CPU 队列 |
| timer | `timer_base` | per-CPU 时间轮 |
| hrtimer | `hrtimer_clock_base` | per-CPU per-clock 红黑树 |
| workqueue | `worker_pool`、`pool_workqueue` | per-CPU 工作池 |
| irq_work | `raised_list`、`lazy_list` | per-CPU llist |
| kthread | — | 不 per-CPU（独立线程） |

---

## §4 使用规模

以下数字均为对内核源码树实时 grep 的精确结果。

### 4.1 workqueue

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `INIT_WORK` | 2,209 | `grep -r "INIT_WORK\b" --include="*.c"` |
| `DECLARE_WORK` | 134 | `grep -r "DECLARE_WORK\b" --include="*.c"` |
| `queue_work` | 2,119 | `grep -r "queue_work\b" --include="*.c"` |
| `schedule_work` | 1,835 | `grep -r "schedule_work\b" --include="*.c"` |
| `cancel_work_sync` | 1,150 | `grep -r "cancel_work_sync" --include="*.c"` |
| `alloc_workqueue` | 387 | `grep -r "alloc_workqueue" --include="*.c"` |
| `alloc_ordered_workqueue` | 150 | `grep -r "alloc_ordered_workqueue" --include="*.c"` |
| `create_singlethread_workqueue` | 214 | `grep -r "create_singlethread_workqueue" --include="*.c"` |
| `create_workqueue` | 43 | `grep -r "create_workqueue\b" --include="*.c"` |
| `flush_workqueue` | 375 | `grep -r "flush_workqueue" --include="*.c"` |
| `system_wq` | 151 | `grep -r "system_wq" --include="*.c"` |

### 4.2 delayed_work

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `INIT_DELAYED_WORK` | 1,091 | `grep -r "INIT_DELAYED_WORK" --include="*.c"` |
| `DECLARE_DELAYED_WORK` | 58 | `grep -r "DECLARE_DELAYED_WORK" --include="*.c"` |
| `schedule_delayed_work` | 1,052 | `grep -r "schedule_delayed_work\b" --include="*.c"` |
| `queue_delayed_work` | 996 | `grep -r "queue_delayed_work\b" --include="*.c"` |
| `cancel_delayed_work_sync` | 1,330 | `grep -r "cancel_delayed_work_sync" --include="*.c"` |

### 4.3 timer

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `timer_setup` | 1,096 | `grep -r "timer_setup" --include="*.c"` |
| `mod_timer` | 1,680 | `grep -r "mod_timer" --include="*.c"` |

### 4.4 hrtimer

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `hrtimer_init` | 199 | `grep -r "hrtimer_init" --include="*.c"` |
| `hrtimer_start` | 225 | `grep -r "hrtimer_start" --include="*.c"` |

### 4.5 softirq / tasklet

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `open_softirq` | 12 | `grep -r "open_softirq" --include="*.c"` |
| `raise_softirq` | 31 | `grep -r "raise_softirq" --include="*.c"` |
| `tasklet_setup` | 223 | `grep -r "tasklet_setup" --include="*.c"` |
| `tasklet_schedule` | 504 | `grep -r "tasklet_schedule" --include="*.c"` |

### 4.6 threaded IRQ / irq_work

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `request_threaded_irq` | 991 | `grep -r "request_threaded_irq" --include="*.c"` |
| `irq_work_queue` | 80 | `grep -r "irq_work_queue" --include="*.c"` |

### 4.7 kthread

| API | 调用次数 | grep 命令 |
|-----|---------|----------|
| `kthread_run` | 300 | `grep -r "kthread_run\b" --include="*.c"` |
| `kthread_create` | 59 | `grep -r "kthread_create\b" --include="*.c"` |
| `kthread_should_stop` | 474 | `grep -r "kthread_should_stop" --include="*.c"` |
| `kthread_stop` | 396 | `grep -r "kthread_stop\b" --include="*.c"` |

---

## §5 设计模式速览

### 5.1 取消/同步模式

| 模块 | 非阻塞取消 | 阻塞取消（等待完成） | 检查是否活跃 |
|------|-----------|-------------------|-------------|
| timer | `timer_delete()` | `timer_delete_sync()` | `timer_pending()` |
| hrtimer | `hrtimer_try_to_cancel()` | `hrtimer_cancel()` | `hrtimer_active()` |
| workqueue | `cancel_work()` (不常用) | `cancel_work_sync()` | `work_pending()` |
| delayed_work | `cancel_delayed_work()` | `cancel_delayed_work_sync()` | — |
| tasklet | — | `tasklet_kill()` | — |
| irq_work | `irq_work_busy()` | `irq_work_sync()` | `irq_work_busy()` |

### 5.2 workqueue 典型使用模式

```c
/* 1. 定义 work */
static void my_work_handler(struct work_struct *work);
static DECLARE_WORK(my_work, my_work_handler);

/* 2. 排队执行 */
schedule_work(&my_work);  /* 排入 system_wq */
/* 或 */
queue_work(my_wq, &my_work);  /* 排入自定义 workqueue */

/* 3. 取消（等待完成） */
cancel_work_sync(&my_work);
```

### 5.3 delayed_work 典型使用模式

```c
/* 1. 初始化 */
static void my_timeout(struct work_struct *work);
INIT_DELAYED_WORK(&priv->dwork, my_timeout);

/* 2. 延迟排队 */
schedule_delayed_work(&priv->dwork, msecs_to_jiffies(100));

/* 3. 取消 */
cancel_delayed_work_sync(&priv->dwork);
```

### 5.4 threaded IRQ 典型使用模式

```c
/* 硬中断处理器：快速确认 */
static irqreturn_t my_hardirq_handler(int irq, void *dev_id)
{
    /* 清中断标志，不做耗时操作 */
    return IRQ_WAKE_THREAD;
}

/* 线程处理器：可睡眠处理 */
static irqreturn_t my_thread_handler(int irq, void *dev_id)
{
    /* 读寄存器、做 I/O、可睡眠 */
    return IRQ_HANDLED;
}

/* 注册 */
request_threaded_irq(irq, my_hardirq_handler, my_thread_handler,
                     IRQF_ONESHOT, "my_dev", priv);
```

### 5.5 kthread 典型使用模式

```c
static int my_kthread(void *data)
{
    while (!kthread_should_stop()) {
        /* 做工作 */
        set_current_state(TASK_INTERRUPTIBLE);
        schedule_timeout(HZ);  /* 或 wait_event */
    }
    return 0;
}

/* 创建并启动 */
struct task_struct *t = kthread_run(my_kthread, data, "my_kthread");

/* 停止 */
kthread_stop(t);  /* 设置停止标志 + 等待退出 */
```

### 5.6 弃用与迁移

| 旧机制 | 状态 | 推荐替代 |
|--------|------|---------|
| tasklet | **已弃用**（`interrupt.h:648`） | threaded IRQ |
| `init_timer`/`setup_timer` | **已移除**（6.1） | `timer_setup` + `from_timer` |
| `create_singlethread_workqueue` | 仍可用但建议审查 | `alloc_ordered_workqueue` |

### 5.7 arm64 特定参数

| 参数 | 值 | 影响 |
|------|-----|------|
| `CONFIG_HZ` | 250（默认） | timer 分辨率 = 4ms |
| `CONFIG_HIGH_RES_TIMERS` | y（默认） | hrtimer 可用 |
| `MAX_SOFTIRQ_RESTART` | 10 | softirq 最大循环次数 |
| `MAX_SOFTIRQ_TIME` | 2ms | softirq 最大执行时间 |
| `IDLE_WORKER_TIMEOUT` | 5 分钟 | workqueue 空闲 worker 超时 |
| ARM Generic Timer | `arch/arm64/kernel/arch_timer.c` | hrtimer 硬件时钟事件源 |

---

## §6 锁模型对比

本类别模块的并发保护策略差异显著，是选型的核心维度之一：

| 模块 | 主要锁/保护机制 | 执行上下文 | 可抢占 | 可睡眠 | NMI 安全 |
|------|---------------|-----------|--------|--------|---------|
| **softirq** | BH-disable（`local_bh_disable`） | softirq 上下文（硬中断退出） | 否 | 否 | 否 |
| **tasklet** | BH-disable + per-CPU 序列化 | softirq 上下文（TASKLET_SOFTIRQ） | 否 | 否 | 否 |
| **timer** | `base->lock`（spinlock） | TIMER_SOFTIRQ | 否 | 否 | 否 |
| **hrtimer** | `cpu_base->lock`（raw_spinlock） | hardirq 或 softirq | 否 | 否 | 否 |
| **workqueue** | `pool->lock`（spinlock）+ worker mutex | 工作线程（进程上下文） | 是 | 是 | 否 |
| **delayed_work** | timer lock + workqueue pool lock | timer 到期 → workqueue 线程 | 是（workqueue 阶段） | 是（workqueue 阶段） | 否 |
| **threaded IRQ** | `desc->lock`（raw_spinlock）+ 线程调度 | hardirq → 线程（进程上下文） | 是（线程阶段） | 是（线程阶段） | 否 |
| **irq_work** | per-CPU llist（lock-free） | 自 IPI → irq_exit | 否 | 否 | **是** |
| **kthread** | 调用者自行管理 | 进程上下文 | 是 | 是 | 否 |

**关键差异**：
- **irq_work** 是唯一 NMI 安全的延迟机制——使用 lock-free per-CPU llist，可在 NMI 中 `irq_work_queue`
- **softirq/tasklet/timer/hrtimer** 都在 BH-disable 保护下执行——不可睡眠、不可被同 CPU 的进程抢占
- **workqueue** 在进程上下文执行——可睡眠、可被抢占，但受 `pool->lock` 保护排队操作
- **threaded IRQ** 的硬中断部分在 raw_spinlock 保护下快速执行，线程部分在进程上下文可睡眠执行

### 6.1 BH-disable 保护范围

```
local_bh_disable()
    │
    ├─ softirq 执行（__do_softirq）
    │    ├─ TIMER_SOFTIRQ → timer 到期回调
    │    ├─ TASKLET_SOFTIRQ → tasklet 回调
    │    ├─ HRTIMER_SOFTIRQ → hrtimer 到期回调
    │    └─ NET_RX/TX_SOFTIRQ → 网络处理
    │
    └─ 期间不可睡眠、不可被同 CPU 进程抢占
```

### 6.2 选择锁模型的关键问题

```
你的回调需要睡眠？
├─ 是 → workqueue / delayed_work / threaded IRQ / kthread
└─ 否 → 需要多快？
    ├─ 微秒级 → softirq（有对应向量）/ tasklet（已弃用）/ irq_work（NMI 安全）
    └─ 定时触发 → timer（jiffies 精度）/ hrtimer（纳秒精度）
```

---

## §7 组合使用模式

### 7.1 delayed_work = timer + workqueue 组合

最常见的组合：timer 到期后将 work 排入 workqueue，在进程上下文执行。

```c
INIT_DELAYED_WORK(&priv->dwork, my_handler);
schedule_delayed_work(&priv->dwork, msecs_to_jiffies(100));
/* 100ms 后：timer 到期 → TIMER_SOFTIRQ → delayed_work_timer_fn
   → queue_work → workqueue 线程执行 my_handler */
```

全树 1,091 处 `INIT_DELAYED_WORK`，1,052 处 `schedule_delayed_work`。

实例：`drivers/bluetooth/hci_vhci.c` 使用 `INIT_DELAYED_WORK` 实现打开超时。

### 7.2 workqueue + firmware loader 异步组合

驱动在 probe 中异步加载固件，完成后在 workqueue 中初始化设备：

```c
request_firmware_nowait(THIS_MODULE, FW_ACTION_UEVENT,
                        "fw.bin", dev, GFP_KERNEL, priv,
                        my_fw_loaded);
/* my_fw_loaded 回调在 workqueue 进程上下文中执行 */
```

实例：`drivers/bluetooth/btusb.c` 同时使用 `request_firmware` + `schedule_work`。

### 7.3 threaded IRQ + workqueue 组合

中断处理拆分：硬中断快速确认 + workqueue 处理后续逻辑。

```c
/* 硬中断处理器 */
static irqreturn_t my_irq(int irq, void *data)
{
    /* 清中断标志 */
    schedule_work(&priv->work);  /* 推迟到 workqueue */
    return IRQ_HANDLED;
}
```

实例：`drivers/bluetooth/hci_intel.c` 同时使用 `request_threaded_irq` + `schedule_work`。

### 7.4 timer + notifier 组合

CPU hotplug 通知 + 定时器重配置：当 CPU 上线时，notifier 回调中重新配置 per-CPU 定时器。

实例：`drivers/char/random.c` 使用 notifier + timer 组合。

### 7.5 irq_work + perf 事件组合

NMI 中的 perf 事件采样通过 irq_work 推迟到安全上下文处理：

```c
/* NMI 上下文 */
static void perf_event_nmi_handler(...)
{
    irq_work_queue(&event->pending_irq);
}

/* irq_exit 时执行 */
static void perf_pending_irq(struct irq_work *work)
{
    /* 安全上下文处理 perf 事件 */
}
```

### 7.6 kthread + workqueue 组合

workqueue 的工作线程本身就是 kthread——`worker_thread` 函数在 kthread 中循环执行排队的工作。

### 7.7 最容易被类比错的关系

| 容易混淆的对 | 本质区别 |
|-------------|---------|
| timer vs hrtimer | timer 用时间轮（O(1) 插入，jiffies 分辨率）；hrtimer 用红黑树（O(log n) 插入，纳秒分辨率） |
| workqueue vs kthread | workqueue 是"排队-执行"模型（per-CPU 池自动管理并发）；kthread 是"持续运行"模型（调用者自行管理循环） |
| softirq vs tasklet | softirq 可并行执行（同类型可在多 CPU 并行）；tasklet 严格序列化（同一 tasklet 不会并行） |
| delayed_work vs timer | delayed_work 最终在进程上下文执行（可睡眠）；timer 回调在 softirq 执行（不可睡眠） |
| threaded IRQ vs workqueue | threaded IRQ 的线程由中断触发（每次中断唤醒）；workqueue 由代码显式排队 |
| irq_work vs softirq | irq_work 是 NMI 安全的（lock-free llist）；softirq 不是 NMI 安全 |

---

## §8 阅读顺序建议

### 路径 A：写一个设备驱动（需要延迟处理）

1. [workqueue.md](workqueue.md) — 最常用的延迟执行机制
2. [misc-work.md](misc-work.md) — delayed_work（timer + workqueue 组合）
3. [misc-irq.md](misc-irq.md) — threaded IRQ（如果处理中断）
4. [timer.md](timer.md) — 如果需要精确超时

### 路径 B：理解中断处理路径

1. [softirq.md](softirq.md) — softirq 和 tasklet（底层延迟向量）
2. [misc-irq.md](misc-irq.md) — threaded IRQ 和 irq_work
3. [timer.md](timer.md) — timer/hrtimer 在 TIMER_SOFTIRQ/HRTIMER_SOFTIRQ 中到期

### 路径 C：理解定时器子系统

1. [timer.md](timer.md) — timer（时间轮）和 hrtimer（红黑树）
2. [softirq.md](softirq.md) — 理解 TIMER_SOFTIRQ 和 HRTIMER_SOFTIRQ
3. [misc-work.md](misc-work.md) — delayed_work 如何组合 timer + workqueue

### 路径 D：理解后台线程机制

1. [misc-thread.md](misc-thread.md) — kthread API
2. [workqueue.md](workqueue.md) — workqueue 的工作线程模型
3. [misc-irq.md](misc-irq.md) — threaded IRQ 的线程处理器

### 路径 E：NMI 安全的延迟执行

1. [misc-irq.md](misc-irq.md) — irq_work（NMI 安全，自 IPI）
2. [softirq.md](softirq.md) — 对比 softirq（非 NMI 安全）

---

## §9 文档索引

| 文档 | 覆盖模块 |
|------|---------|
| [workqueue.md](workqueue.md) | workqueue：work_struct 位编码、workqueue_struct、worker_pool、并发管理、救援线程 |
| [timer.md](timer.md) | timer + hrtimer：分层时间轮（9 层 576 桶）、红黑树、ktime_t、8 个时钟基 |
| [softirq.md](softirq.md) | softirq + tasklet：10 个向量、执行流程、tasklet 弃用、API 迁移 |
| [misc-work.md](misc-work.md) | delayed_work：timer+workqueue 组合、delayed_work_timer_fn、取消/修改 |
| [misc-irq.md](misc-irq.md) | threaded IRQ + irq_work：handler+thread_fn 拆分、IRQF_ONESHOT、自 IPI 机制、NMI 安全 |
| [misc-thread.md](misc-thread.md) | kthread：kthread_create vs kthread_run、停止协议、暂停机制 |
