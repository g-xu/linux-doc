
> 基于 Linux 6.1.y LTS，作者：Daniel Bristot de Oliveira（Red Hat）

---

## 一、设计哲学与背景

### 为什么需要 RV？

传统形式化验证方法（Model Checking、Theorem Proving）面临一个根本性困境：**状态空间爆炸**。对于 Linux 内核这种规模的系统，完整建模几乎不可能。

RV 的核心思想是一种务实的折中：

```
不对整个系统建模 → 只对"关键行为属性"建模
不做穷举验证   → 只在运行时验证实际执行路径
```

关键论文（代码注释中直接引用）：
- **De Oliveira, 2019**（SEFM）：证明了 in-kernel 同步在线监控的开销**低于**将事件写入 trace buffer 的开销
- **De Oliveira, 2020**（PhD Thesis）：基于确定性自动机（DA）对 PREEMPT_RT 内核的形式化验证

这个结论非常反直觉，但它直接决定了 RV 选择**同步在线监控**而非离线分析的架构方向。

---

## 二、整体架构

```
用户空间
    │
    │  /sys/kernel/tracing/rv/
    │  ├── available_monitors
    │  ├── enabled_monitors
    │  ├── monitoring_on
    │  ├── available_reactors
    │  ├── reacting_on
    │  └── monitors/
    │       ├── wip/
    │       │   ├── enable
    │       │   ├── desc
    │       │   └── reactors
    │       └── wwnr/
    │           ├── enable
    │           ├── desc
    │           └── reactors
    │
════════════════════════════════════════════════════════
内核空间
    │
    ├── [rv.c]  RV 核心框架
    │     ├── rv_monitors_list      ← 全局 monitor 链表
    │     ├── monitoring_on         ← 全局开关
    │     ├── rv_register_monitor()
    │     └── rv_unregister_monitor()
    │
    ├── [rv_reactors.c]  Reactor 框架
    │     ├── rv_reactors_list      ← 全局 reactor 链表
    │     ├── reacting_on           ← 全局 reactor 开关
    │     └── rv_register_reactor()
    │
    └── [monitors/]  具体 Monitor 实现
          ├── wip/   ← per-CPU 自动机
          └── wwnr/  ← per-Task 自动机
```

---

## 三、核心数据结构剖析

### 3.1 `struct rv_monitor`（`include/linux/rv.h`）

这是每个 monitor 必须实现的接口契约：

```c
struct rv_monitor {
    const char  *name;         // monitor 名称，最长 32 字节
    const char  *description;  // 描述字符串
    bool         enabled;      // 当前是否启用（框架维护）
    int        (*enable)(void);  // 钩子挂载：attach tracepoints
    void       (*disable)(void); // 钩子卸载：detach tracepoints
    void       (*reset)(void);   // 状态机复位（monitoring_on 重开时调用）
    void       (*react)(char *); // 违规时的反应回调（由 reactor 填充）
};
```

**设计亮点**：`react` 函数指针在运行时由 reactor 框架动态替换（`monitor_swap_reactors()`），实现了 monitor 逻辑与反应策略的**解耦**。

### 3.2 `struct rv_monitor_def`（`rv.h`）

框架内部的 monitor 管理结构，是对 `rv_monitor` 的封装：

```c
struct rv_monitor_def {
    struct list_head    list;          // 挂入 rv_monitors_list
    struct rv_monitor  *monitor;       // 指向 monitor 实现
    struct dentry      *root_d;        // tracefs 目录节点
    struct rv_reactor_def *rdef;       // 当前绑定的 reactor
    bool                reacting;      // 是否处于 reacting 状态
    bool                task_monitor;  // 是否为 per-task 类型
};
```

### 3.3 自动机定义（以 `wip.h` 为例）

这是整个 RV 系统最精妙的部分——**用 C 结构体直接编码确定性有限自动机（DFA）**：

```c
// 状态枚举
enum states_wip {
    preemptive_wip = 0,   // 初始态：可抢占
    non_preemptive_wip,   // 关闭抢占态
    state_max_wip         // 哨兵值，也用作 INVALID_STATE
};

// 事件枚举
enum events_wip {
    preempt_disable_wip = 0,
    preempt_enable_wip,
    sched_waking_wip,
    event_max_wip
};

// 转移函数：function[当前状态][事件] = 下一状态
static struct automaton_wip automaton_wip = {
    .function = {
        //              preempt_disable        preempt_enable       sched_waking
        /* preemptive */    { non_preemptive_wip, INVALID_STATE,    INVALID_STATE },
        /* non_preemptive */{ INVALID_STATE,      preemptive_wip,   non_preemptive_wip },
    },
    .initial_state = preemptive_wip,
    .final_states  = { 1, 0 },  // preemptive 是合法终态，non_preemptive 不是
};
```

**这个自动机描述的语义**：
- 系统初始处于 `preemptive`（可抢占）状态
- `preempt_disable` → 进入 `non_preemptive`
- `preempt_enable` → 回到 `preemptive`
- **违规**：在 `preemptive` 状态下发生 `sched_waking`（即在可抢占上下文中唤醒任务）→ `INVALID_STATE` → 触发 reactor

---

## 四、关键机制深度解析

### 4.1 Monitor 的生命周期

```
rv_register_monitor()
    │
    ├── 检查名称唯一性（遍历 rv_monitors_list）
    ├── kzalloc(rv_monitor_def)
    ├── create_monitor_dir()        ← 在 tracefs 创建目录和文件
    │     ├── rv_create_dir(name)
    │     ├── rv_create_file("enable")
    │     ├── rv_create_file("desc")
    │     └── reactor_populate_monitor()  ← 创建 reactors 文件，绑定 nop reactor
    └── list_add_tail(&rv_monitors_list)
```

### 4.2 Enable/Disable 的并发安全设计

```c
// enable 路径
int rv_enable_monitor(struct rv_monitor_def *mdef)
{
    lockdep_assert_held(&rv_interface_lock);  // 必须持锁
    if (mdef->monitor->enabled)
        return 0;
    retval = mdef->monitor->enable();         // 调用 monitor 的 enable 钩子
    if (!retval)
        mdef->monitor->enabled = 1;
    return retval;
}

// disable 路径（关键！）
static int __rv_disable_monitor(struct rv_monitor_def *mdef, bool sync)
{
    if (mdef->monitor->enabled) {
        mdef->monitor->enabled = 0;
        mdef->monitor->disable();
        if (sync)
            tracepoint_synchronize_unregister();  // ← 关键同步点
        return 1;
    }
    return 0;
}
```

**`tracepoint_synchronize_unregister()` 的作用**：这是一个 `synchronize_rcu()` 的封装，确保所有正在执行中的 tracepoint 回调都完成后才返回。这解决了一个经典的 **use-after-free** 竞态：

```
CPU0: disable_wip() → 卸载 tracepoint
CPU1: 正在执行 handle_preempt_disable() 回调中...
                                          ← 如果没有 synchronize，CPU0 可能已经
                                             释放了 monitor 数据，CPU1 访问野指针
```

### 4.3 `monitoring_on` 的语义与 Reset 机制

这里有一个非常精妙的设计：

```c
static void turn_monitoring_on_with_reset(void)
{
    lockdep_assert_held(&rv_interface_lock);
    if (rv_monitoring_on())
        return;

    // 关键：重新开启监控前必须 reset 所有自动机状态！
    reset_all_monitors();
    turn_monitoring_on();
}
```

**为什么 reset 是必须的？**

当 `monitoring_on = 0` 期间，内核事件仍在发生，但 monitor 没有处理这些事件。这意味着自动机的状态与系统真实状态已经**失去同步**（out-of-sync）。如果不 reset，自动机可能处于一个错误的中间状态，导致后续产生大量误报（false positive）。

`monitoring_on` 与 `enabled` 的区别：
| 维度 | `monitoring_on=0` | `enabled=0` |
|---|---|---|
| tracepoint 钩子 | **仍然挂载** | 已卸载 |
| 事件处理 | 跳过（fast path check） | 不会触发 |
| 开销 | 极低（一次 READ_ONCE + smp_rmb） | 零 |
| 恢复时 | 需要 reset 自动机 | 需要重新 enable |

### 4.4 Reactor 的动态绑定机制

```c
static void monitor_swap_reactors(struct rv_monitor_def *mdef,
                                  struct rv_reactor_def *rdef,
                                  bool reacting)
{
    bool monitor_enabled = mdef->monitor->enabled;

    // 切换 reactor 需要先暂停 monitor，避免竞态
    if (monitor_enabled)
        rv_disable_monitor(mdef);

    // 引用计数管理
    mdef->rdef->counter--;   // 旧 reactor 引用 -1
    rdef->counter++;         // 新 reactor 引用 +1

    // 直接替换函数指针！
    mdef->rdef = rdef;
    mdef->reacting = reacting;
    mdef->monitor->react = rdef->reactor->react;  // ← 核心：替换回调

    if (monitor_enabled)
        rv_enable_monitor(mdef);
}
```

**`counter` 的作用**：防止一个正在被 monitor 使用的 reactor 被卸载（`rv_unregister_reactor` 会检查 `counter != 0`，返回 `-EBUSY`）。

---

## 五、两个内置 Monitor 的对比分析

### 5.1 `wip`（Wakeup In Preemptive）— per-CPU 模型

```c
DECLARE_DA_MON_PER_CPU(wip, unsigned char);
```

- **监控目标**：在可抢占上下文中发生 `sched_waking`（唤醒操作）
- **状态粒度**：per-CPU（每个 CPU 一个自动机实例）
- **挂载的 tracepoint**：
  - `preempt_disable` → `da_handle_event_wip(preempt_disable_wip)`
  - `preempt_enable` → `da_handle_start_event_wip(preempt_enable_wip)` ← 注意是 `start_event`
  - `sched_waking` → `da_handle_event_wip(sched_waking_wip)`

**`da_handle_start_event` vs `da_handle_event` 的区别**：
- `da_handle_event`：正常的状态转移，若到达 `INVALID_STATE` 则触发 reactor
- `da_handle_start_event`：用于"重新同步"——如果当前状态是 `INVALID_STATE`，则先将状态重置为初始态，再处理事件。这是处理 monitor 启动时系统已处于中间状态的优雅方案。

### 5.2 `wwnr`（Wakeup While Not Running）— per-Task 模型

```c
DECLARE_DA_MON_PER_TASK(wwnr, unsigned char);
```

- **监控目标**：一个任务在没有运行（not running）的状态下被唤醒
- **状态粒度**：per-Task（每个 `task_struct` 一个自动机实例，存储在 task 的私有字段中）
- **挂载的 tracepoint**：
  - `sched_switch` → 同时处理 prev（switch_out）和 next（switch_in）两个任务
  - `sched_wakeup` → `da_handle_event_wwnr(p, wakeup_wwnr)`

**特别注意 `handle_switch` 中的细节**：

```c
static void handle_switch(void *data, bool preempt, struct task_struct *p,
                          struct task_struct *n, unsigned int prev_state)
{
    // 只有第一次进入 TASK_INTERRUPTIBLE 才用 start_event（重新同步）
    if (prev_state == TASK_INTERRUPTIBLE)
        da_handle_start_event_wwnr(p, switch_out_wwnr);
    else
        da_handle_event_wwnr(p, switch_out_wwnr);

    da_handle_event_wwnr(n, switch_in_wwnr);
}
```

这里的设计哲学：对于新创建的任务，我们不知道它的初始状态，用 `start_event` 来做"懒惰初始化"，避免误报。

---

## 六、并发安全分析

RV 框架中使用了三层并发控制：

| 层次 | 机制 | 保护对象 |
|---|---|---|
| 控制面 | `rv_interface_lock`（mutex） | monitor/reactor 的注册、启停 |
| 数据面（全局开关） | `READ_ONCE` + `smp_rmb/wmb` | `monitoring_on`、`reacting_on` |
| 数据面（tracepoint 卸载） | `tracepoint_synchronize_unregister()` | 防止 disable 后的 use-after-free |

**`monitoring_on` 的无锁读取设计**：

```c
bool rv_monitoring_on(void)
{
    smp_rmb();                    // 读屏障：确保看到最新的写入
    return READ_ONCE(monitoring_on);
}
```

这个函数会在**每个 tracepoint 回调的热路径**中被调用，因此不能用 mutex，只能用内存屏障 + `READ_ONCE` 保证可见性。`smp_rmb()` 与写端的 `smp_wmb()` 配对，构成一个完整的 acquire/release 语义对。

---

## 七、与 ftrace/tracefs 的关系

RV 并非独立子系统，而是**寄生在 tracefs 之上**：

```c
// rv.h 中的宏定义——直接复用 tracefs API
#define rv_create_dir    tracefs_create_dir
#define rv_create_file   tracefs_create_file
#define rv_remove        tracefs_remove
```

用户接口路径 `/sys/kernel/tracing/rv/` 就是 tracefs 挂载点下的子目录。这个设计决策带来的好处：
1. **零基础设施成本**：不需要实现自己的 VFS 接口
2. **一致的用户体验**：与 `tracing_on`、`events/` 等接口风格完全一致
3. **权限模型复用**：tracefs 的访问控制自动适用

---

## 八、如何编写一个新的 RV Monitor（实践指南）

基于以上分析，编写一个新 monitor 的步骤：

**第一步：定义自动机（`.h` 文件）**
```c
// 1. 定义状态枚举
enum states_mymon { state_a = 0, state_b, state_max_mymon };
// 2. 定义事件枚举
enum events_mymon { event_x = 0, event_y, event_max_mymon };
// 3. 填写转移矩阵
static struct automaton_mymon automaton_mymon = {
    .function = {
        { state_b,      INVALID_STATE },  // state_a: x→b, y→invalid
        { INVALID_STATE, state_a      },  // state_b: x→invalid, y→a
    },
    .initial_state = state_a,
    .final_states  = { 1, 0 },
};
```

**第二步：实现 monitor（`.c` 文件）**
```c
// 选择 per-CPU 或 per-Task
DECLARE_DA_MON_PER_CPU(mymon, unsigned char);

// 实现 tracepoint 回调
static void handle_event_x(void *data, ...) {
    da_handle_event_mymon(event_x_mymon);
}

// 实现 enable/disable
static int enable_mymon(void) {
    da_monitor_init_mymon();
    rv_attach_trace_probe("mymon", some_tracepoint, handle_event_x);
    return 0;
}
```

**第三步：注册**
```c
static struct rv_monitor rv_mymon = {
    .name    = "mymon",
    .enable  = enable_mymon,
    .disable = disable_mymon,
    .reset   = da_monitor_reset_all_mymon,
};
module_init(register_mymon);
```

---

## 九、6.1.y 的局限性与未来演进方向

在 6.1.y 中，RV 子系统仍处于**早期阶段**，有几个明显的局限：

1. **Monitor 数量极少**：只有 `wip` 和 `wwnr` 两个，且都是测试性质的 sample monitor
2. **per-Task slot 数量有限**：`RV_PER_TASK_MONITORS` 限制了同时运行的 per-task monitor 数量
3. **自动机只支持 DFA**：不支持更复杂的时序逻辑（LTL/MTL）
4. **无历史记录**：违规发生时只有当前事件，没有导致违规的事件序列

**Mainline 的演进方向**（6.x 后续版本）：
- 引入更多针对 PREEMPT_RT 的 monitor（如 `snroc`、`scpd` 等）
- 探索将 eBPF 与 RV 框架结合，实现用户态可编程的 monitor
- 与 `osnoise`/`timerlat` tracer 协同，用于实时系统的延迟验证

---

## 总结

RV 子系统的核心设计哲学可以用一句话概括：**用确定性自动机在内核热路径上做轻量级在线形式化验证，并通过可插拔的 reactor 机制实现从"观察"到"干预"的全谱响应能力**。它是形式化方法在工业级操作系统内核中落地的一次非常务实的尝试。

如果你对某个具体方面想深入（比如 `da_monitor.h` 的宏展开机制、per-task slot 的 task_struct 嵌入方式、或者如何用 ftrace 追踪 RV 自身的行为），随时告诉我。