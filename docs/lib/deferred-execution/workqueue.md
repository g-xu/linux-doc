# workqueue — 内核延迟执行框架

> workqueue 是内核中将"需要推迟执行的工作"提交给内核线程执行的框架。工作项（work_struct）被排队到工作队列（workqueue_struct），由 per-CPU 或 unbound 的工作线程（worker）取出并执行。核心设计目标是：在保持简单 API 的同时，提供灵活的并发管理、NUMA 感知和内存回收死锁防护。

---

## 定位

内核中大量操作不适合在中断上下文执行（需要睡眠、持锁、做 I/O），但又不想在每次中断时都创建线程。workqueue 提供"提交工作 → 工作线程执行"的标准模式，覆盖从网络包处理到设备驱动初始化的各种场景。

## 核心数据结构

### work_struct（`include/linux/workqueue.h:98-105`）

```c
struct work_struct {
    atomic_long_t data;          // 打包：workqueue 指针/池 ID + 标志位
    struct list_head entry;      // 链入 pool->worklist 或 pwq->inactive_works
    work_func_t func;            // 回调函数
};
```

`data` 字段的位编码（arm64 上 64 位）：
- Bit 0: `WORK_STRUCT_PENDING`（已排队）
- Bit 1: `WORK_STRUCT_INACTIVE`（被 max_active 阻塞）
- Bit 2: `WORK_STRUCT_PWQ`（data 指向 pwq vs 池 ID）
- Bit 3: `WORK_STRUCT_LINKED`（有链接工作）
- Bits 4-7: flush color（4 位）
- 高位: 池 ID（离队时）或 pwq 指针（在队时）

### workqueue_struct（`kernel/workqueue.c:257-299`）

```c
struct workqueue_struct {
    struct list_head pwqs;              // 所有 pool_workqueue 链表
    struct mutex mutex;                 // 保护本 wq
    int work_color;                     // 当前工作颜色
    int flush_color;                    // 当前 flush 颜色
    struct list_head maydays;           // 请求救援的 pwq
    struct worker *rescuer;             // 救援线程（WQ_MEM_RECLAIM）
    unsigned int flags;                 // WQ_* 标志
    struct pool_workqueue __percpu *cpu_pwqs;     // per-CPU pwq
    struct pool_workqueue __rcu *numa_pwq_tbl[];  // unbound 的 NUMA pwq 表
    char name[WQ_NAME_LEN];             // 名称（最长 24 字符）
};
```

### worker_pool（`kernel/workqueue.c:148-193`）

```c
struct worker_pool {
    raw_spinlock_t lock;
    int cpu;                            // 关联 CPU（unbound 为 -1）
    int nr_running;                     // 并发管理计数器
    struct list_head worklist;          // 待处理工作列表
    int nr_idle;                        // 空闲工作线程数
    struct list_head idle_list;
    struct timer_list idle_timer;       // 空闲超时（5 分钟）
    DECLARE_HASHTABLE(busy_hash, 6);    // 64 项忙碌工作线程哈希
    struct worker *manager;
};
```

每个 CPU 有 **2 个标准池**（`NR_STD_WORKER_POOLS = 2`）：普通（nice 0）和高优先级（nice -20）。

### pool_workqueue — 桥梁

连接 `workqueue_struct` 和 `worker_pool`。每个 workqueue 对每个 CPU（per-CPU wq）或每个 NUMA 节点（unbound wq）有一个 pwq。

### worker（`kernel/workqueue_internal.h:24-61`）

```c
struct worker {
    struct work_struct *current_work;   // 当前执行的工作
    work_func_t current_func;           // 当前回调
    struct task_struct *task;           // 内核线程
    struct worker_pool *pool;
    unsigned long last_active;
    unsigned int flags;                 // WORKER_DIE, WORKER_IDLE 等
};
```

## 工作队列类型

| 类型 | 特征 | 创建方式 |
|------|------|----------|
| **per-CPU** | 每个 CPU 独立执行，并发管理（最多 1 个 worker 运行） | `alloc_workqueue(name, 0, max_active)` |
| **ordered** | 全局串行（max_active=1） | `alloc_ordered_workqueue(name, flags)` |
| **unbound** | 不绑定 CPU，无并发管理，NUMA 感知 | `alloc_workqueue(name, WQ_UNBOUND, max_active)` |
| **freezable** | 系统挂起时冻结 | `WQ_FREEZABLE` 标志 |

## 系统工作队列

| 名称 | 特征 | 用途 |
|------|------|------|
| `system_wq` | per-CPU，通用 | `schedule_work()` 的默认目标 |
| `system_highpri_wq` | per-CPU，高优先级 | 高优先级工作 |
| `system_long_wq` | per-CPU，长任务 | 长时间运行的工作 |
| `system_unbound_wq` | unbound | 不需要 CPU 绑定的工作 |
| `system_freezable_wq` | freezable | 挂起时需要冻结的工作 |
| `system_power_efficient_wq` | 可转为 unbound | 节能型工作 |

## 主要 API

头文件：`include/linux/workqueue.h`；实现：`kernel/workqueue.c`

### 工作队列创建

| API | 说明 | 备注 |
|-----|------|------|
| `alloc_workqueue(fmt, flags, max_active, ...)` | 创建 workqueue | 核心创建 API |
| `alloc_ordered_workqueue(fmt, flags, ...)` | 创建有序 workqueue | max_active=1 |
| `destroy_workqueue(wq)` | 销毁 workqueue | 排空所有工作后释放 |

### 工作调度

| API | 说明 | 备注 |
|-----|------|------|
| `INIT_WORK(work, func)` | 初始化 work_struct | 静态初始化宏 |
| `INIT_DELAYED_WORK(dwork, func)` | 初始化 delayed_work | 含定时器 |
| `schedule_work(work)` | 排到 system_wq | 1,026 个文件使用 |
| `schedule_delayed_work(dwork, delay)` | 延迟排到 system_wq | |
| `queue_work(wq, work)` | 排到指定 workqueue | |
| `queue_delayed_work(wq, dwork, delay)` | 延迟排到指定 wq | |
| `schedule_work_on(cpu, work)` | 排到指定 CPU | |
| `mod_delayed_work(wq, dwork, delay)` | 修改延迟或立即排队 | |

### 工作取消与排空

| API | 说明 | 备注 |
|-----|------|------|
| `cancel_work_sync(work)` | 取消并等待完成 | |
| `cancel_delayed_work(dwork)` | 取消（不等待） | 返回是否成功 |
| `cancel_delayed_work_sync(dwork)` | 取消并等待 | |
| `flush_work(work)` | 等待特定工作完成 | |
| `flush_workqueue(wq)` | 排空整个 workqueue | 在系统 wq 上会警告 |
| `flush_delayed_work(dwork)` | 立即执行延迟工作 | |

## 并发管理

per-CPU 池的核心约束：**最多 1 个 worker 同时运行**。

- `nr_running`：活跃 worker 计数。worker 睡眠时递减，此时如果有待处理工作，唤醒或创建新 worker
- `need_more_worker(pool)`：worklist 非空且 `nr_running == 0`
- `keep_working(pool)`：worklist 非空且 `nr_running <= 1`
- 空闲 worker 超时：5 分钟（`IDLE_WORKER_TIMEOUT`）

**unbound 池**不做并发管理——所有排队的工作在 `max_active` 限制内立即执行。

## 执行流程

1. `schedule_work(work)` → `queue_work(system_wq, work)` → `queue_work_on()`
2. `__queue_work()`：选择目标 pwq，检查 `nr_active < max_active`
   - 未超限：加入 `pool->worklist`
   - 超限：标记 `WORK_STRUCT_INACTIVE`，加入 `pwq->inactive_works`
3. 如果需要 worker：唤醒空闲 worker 或创建新 worker
4. `worker_thread()` 主循环：从 `pool->worklist` 取工作 → `process_one_work()`
5. `process_one_work()`：释放 `pool->lock`，调用 `func(work)`，重新加锁处理完成

## 救援线程（WQ_MEM_RECLAIM）

标记为 `WQ_MEM_RECLAIM` 的 workqueue 拥有专用救援 kthread：
- 目的：防止内存回收路径上的死锁
- 当工作排队超过 10ms 未执行时触发 mayday
- 救援线程以最高优先级运行，直接执行该 workqueue 的所有待处理工作

## WQ 标志

| 标志 | 语义 |
|------|------|
| `WQ_UNBOUND` | 不绑定 CPU |
| `WQ_FREEZABLE` | 系统挂起时冻结 |
| `WQ_MEM_RECLAIM` | 可用于内存回收路径（有救援线程） |
| `WQ_HIGHPRI` | 高优先级 |
| `WQ_CPU_INTENSIVE` | CPU 密集型，不参与并发管理 |
| `WQ_SYSFS` | 在 sysfs 中可见 |
| `WQ_POWER_EFFICIENT` | 可转为 unbound 以节能 |

## 使用规模

| API | 使用文件数 |
|-----|-----------|
| `schedule_work` | 1,665 |
| `create_singlethread_workqueue` | 215 |
| `cancel_delayed_work` | 456 |
| `destroy_workqueue` | 1,123 |
| `schedule_delayed_work` | 1,061 |
| `queue_work` | 1,507 |
| `flush_work` | 523 |
| `cancel_work_sync` | 1,133 |
| `alloc_workqueue` | 362 |

## arm64 注意事项

- arm64 没有 workqueue 特定代码——完全使用通用实现
- NUMA 初始化时机：`workqueue_init()` 在 CPU-to-node 映射可用后才修复 NUMA 关联（arm64 拓扑信息来得较晚）
- `work_struct->data` 使用 `atomic_long_t`（64 位），位打包方案依赖架构宽度
- `cpu_worker_pools` 使用 `DEFINE_PER_CPU_SHARED_ALIGNED`（缓存行对齐）

## §4 常见陷阱

### 陷阱 1：在 system_wq 上调用 flush_workqueue

**错误写法**：
```c
// 试图排空整个系统工作队列
flush_workqueue(system_wq);
```

**正确写法**：
```c
// 只排空自己创建的工作队列
struct workqueue_struct *my_wq = alloc_workqueue("my_wq", 0, 0);
// ...
flush_workqueue(my_wq);

// 如果只需等待特定工作完成，用 flush_work
flush_work(&my_work);
```

**原因**：`system_wq` 是全局共享的工作队列，其它子系统也在上面排队工作。`flush_workqueue(system_wq)` 会等待所有 CPU 上所有排队的工作全部完成，导致不可预测的长延迟，并可能引发与其他子系统的死锁。内核中 `flush_workqueue` 设计用于排空**自己创建**的工作队列。

**源码依据**：`kernel/workqueue.c:2951-2959` — `drain_workqueue` 文档注释明确说明 draining 只允许链式排队（chained queueing），外部排队会被 `__WQ_DRAINING` 标志拒绝并触发 `WARN_ON_ONCE`（`kernel/workqueue.c:1440-1441`）。

### 陷阱 2：cancel_work_sync 从自身工作回调中调用（死锁）

**错误写法**：
```c
static void my_work_func(struct work_struct *work)
{
    // 在回调中试图同步取消自己 → 死锁
    cancel_work_sync(work);
}
```

**正确写法**：
```c
static void my_work_func(struct work_struct *work)
{
    // 如果只需确保后续不再执行，用 cancel_work（非同步版本）
    cancel_work(work);  // 或直接不取消，让回调自然结束
}
```

**原因**：`cancel_work_sync` 内部调用 `__flush_work`，会等待工作执行完成。如果从工作自身的回调中调用，当前工作正在执行中，等待永远不会结束——死锁。`__cancel_work_timer`（`kernel/workqueue.c:3115-3163`）中有一个 do-while 循环等待 `try_to_grab_pending` 成功，而正在运行的工作无法被 grab。

**源码依据**：`kernel/workqueue.c:3121-3152` — `__cancel_work_timer` 的 do-while 循环在 `ret == -ENOENT`（工作正在被取消）时通过 waitqueue 睡眠等待，如果工作就是自己，则永远不会完成。

### 陷阱 3：内存回收路径使用非 WQ_MEM_RECLAIM 工作队列

**错误写法**：
```c
// 在内存回收路径（如 shrinker）中使用普通工作队列
struct workqueue_struct *wq = alloc_workqueue("my_wq", 0, 0);
// 在 shrinker 回调中：
queue_work(wq, &work);
flush_workqueue(wq);  // 可能死锁
```

**正确写法**：
```c
// 内存回收路径必须使用带 WQ_MEM_RECLAIM 标志的工作队列
struct workqueue_struct *wq = alloc_workqueue("my_wq", WQ_MEM_RECLAIM, 0);
```

**原因**：普通工作队列在创建 worker 时使用 `GFP_KERNEL` 分配，而内存回收路径本身持有内存分配锁。如果此时需要分配内存来创建 worker，而分配又需要等待工作队列排空——形成死锁。`WQ_MEM_RECLAIM` 工作队列拥有专用救援线程（rescuer），不依赖 `GFP_KERNEL` 分配，保证前向进度。内核会检测并警告违规的 flush（`kernel/workqueue.c:2628-2635`）。

**源码依据**：`kernel/workqueue.c:2628-2635` — `check_flush_dependency` 函数中两个 `WARN_ONCE`：一个检测 `PF_MEMALLOC` 任务 flush 非 `WQ_MEM_RECLAIM` 工作队列，另一个检测 `WQ_MEM_RECLAIM` 工作队列的回调 flush 非 `WQ_MEM_RECLAIM` 工作队列。

### 陷阱 4：对已排队的工作重复调用 queue_work

**错误写法**：
```c
// 假设 work 已经在队列中
queue_work(my_wq, &work);  // 静默失败，返回 false
// 开发者以为工作已重新排队，实际上没有
```

**正确写法**：
```c
// 如果需要修改已排队工作的行为，先取消再排队
if (!queue_work(my_wq, &work)) {
    // 工作已在队列中，如果需要更新参数：
    // 方案 A：cancel + requeue
    cancel_work_sync(&work);
    update_work_params(&work);
    queue_work(my_wq, &work);
    // 方案 B：在工作结构体中用原子变量传递新参数
}
```

**原因**：`queue_work` 通过 `test_and_set_bit(WORK_STRUCT_PENDING_BIT, ...)` 检测工作是否已排队。如果 `PENDING` 位已设置，直接返回 `false`，不做任何操作。工作仍然按原来的参数执行，不会重新排队。源码中有明确的 `WARN_ON` 检测试图将已在链表中的工作重复入队（`kernel/workqueue.c:1502`）。

**源码依据**：`kernel/workqueue.c:1502` — `WARN_ON(!list_empty(&work->entry))`；`kernel/workqueue.c:1547-1549` — `test_and_set_bit` 返回非零时直接返回 `false`。

### 陷阱 5：在 work 回调中重新排队自身而不理解链式机制

**错误写法**：
```c
static void my_work_func(struct work_struct *work)
{
    // 在回调中重新排队自己
    // 如果没有设置 linked work，可能导致意外行为
    schedule_work(work);
}
```

**正确写法**：
```c
static void my_work_func(struct work_struct *work)
{
    // 如果需要周期性执行，使用 delayed_work
    // 或者使用独立的 work_struct 来链式调度
    struct my_work *mw = container_of(work, struct my_work, work);
    if (mw->need_reschedule)
        queue_delayed_work(my_wq, &mw->dwork, msecs_to_jiffies(100));
}
```

**原因**：当工作回调中重新排队自身时，`__queue_work` 需要检测该工作是否仍在执行中（`PENDING` 位在 `process_one_work` 完成前已清除，所以可以成功入队）。但这种模式容易出错：如果工作队列正在被 drain 或 destroy，`__WQ_DRAINING` 标志会导致新的排队被拒绝并触发 `WARN_ON_ONCE`（`kernel/workqueue.c:1440-1441`）。此外，这种模式使得 `flush_workqueue` 的语义变得复杂。

**源码依据**：`kernel/workqueue.c:1440-1441` — `__WQ_DRAINING` 检查与 `WARN_ON_ONCE(!is_chained_work(wq))`。

## §6 取证摘要

**读取的文件**：
- `include/linux/workqueue.h` — work_struct 结构体定义、位标志枚举、WQ_* 标志
- `kernel/workqueue.c` — 核心实现：__queue_work、process_one_work、worker_thread、cancel/flush 逻辑、rescuer_thread
- `kernel/workqueue_internal.h` — worker 结构体定义

**关键论断出处**：
- "PENDING 位标记工作已排队" — 来源：`include/linux/workqueue.h:31`（`WORK_STRUCT_PENDING_BIT = 0`）
- "drain 期间只允许链式排队" — 来源：`kernel/workqueue.c:1440-1441`（`__WQ_DRAINING` 检查）
- "cancel_work_sync 的 do-while 等待循环" — 来源：`kernel/workqueue.c:3121-3152`
- "WQ_MEM_RECLAIM 依赖检测" — 来源：`kernel/workqueue.c:2628-2635`（`check_flush_dependency`）
- "空闲 worker 超时 5 分钟" — 来源：`kernel/workqueue.c:94`（`IDLE_WORKER_TIMEOUT = 300 * HZ`）
- "mayday 超时 10ms" — 来源：`kernel/workqueue.c:96-98`（`MAYDAY_INITIAL_TIMEOUT`）

**未验证项**：
- `flush_workqueue` 在 system_wq 上是否有显式 WARN（文档中声称"在系统 wq 上会警告"，但源码中未找到针对 system_wq 的专门检查，警告可能来自 lockdep 或依赖检测路径）
