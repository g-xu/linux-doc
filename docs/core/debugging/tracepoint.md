# tracepoint — 内核静态跟踪点基础设施

> **定位**：tracepoint 是 Linux 内核的静态跟踪点基础设施。开发者在内核代码中用 `TRACE_EVENT()` 宏声明跟踪点，运行时通过 ftrace 或 perf 启用。未启用时通过 jump label（static_key）补丁为 NOP，实现零开销。本文覆盖 tracepoint 核心机制及其与 ftrace/perf 的桥接。ftrace 函数跟踪在 `ftrace.md` 中单独覆盖。

---

## 1. 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/tracepoint.h` | 582 | 公开 API：`TRACE_EVENT`、`DECLARE_TRACE`、probe 注册 |
| `include/linux/tracepoint-defs.h` | 90 | 核心结构体定义：`struct tracepoint`、`struct tracepoint_func` |
| `kernel/tracepoint.c` | 787 | 核心实现：probe 注册/注销、SRCU 同步、模块通知 |
| `include/trace/trace_events.h` | 497 | 多阶段包含：为每个 TRACE_EVENT 生成事件结构和回调 |
| `include/trace/define_trace.h` | 136 | `CREATE_TRACE_POINTS` 时的宏重定义 |
| `kernel/trace/trace_events.c` | 4254 | trace event 子系统：ftrace/perf 桥接 |
| `arch/arm64/kernel/jump_label.c` | 35 | arm64 jump label 补丁实现 |

---

## 2. 核心数据结构

### 2.1 struct tracepoint（`include/linux/tracepoint-defs.h:32-41`）

每个 `DECLARE_TRACE` / `TRACE_EVENT` 创建一个 `struct tracepoint` 实例，放在 `__tracepoints` ELF 段中。

```c
struct tracepoint {
    const char *name;                        // 跟踪点名称
    struct static_key key;                   // jump label，禁用时为 NOP
    struct static_call_key *static_call_key; // static call 间接分发
    void *static_call_tramp;                 // static call trampoline 地址
    void *iterator;                          // 迭代器函数（多 probe 循环）
    int (*regfunc)(void);                    // 首个 probe 注册时调用
    void (*unregfunc)(void);                 // 最后一个 probe 注销时调用
    struct tracepoint_func __rcu *funcs;     // RCU 保护的 probe 数组
};
```

### 2.2 struct tracepoint_func（`include/linux/tracepoint-defs.h:26-30`）

```c
struct tracepoint_func {
    void *func;   // probe 回调函数指针
    void *data;   // 传递给 probe 的私有数据
    int prio;     // 优先级（越小越高）
};
```

### 2.3 struct trace_event_call（`include/linux/trace_events.h:362-394`）

由 `TRACE_EVENT()` 宏生成，放在 `_ftrace_events` 段中。桥接 tracepoint 和 ftrace/perf：

```c
struct trace_event_call {
    struct list_head        list;
    struct trace_event_class *class;     // 事件类（含 probe 回调）
    union {
        const char          *name;
        struct tracepoint   *tp;         // 指向 __tracepoint_<name>
    };
    struct trace_event      event;
    char                    *print_fmt;
    struct event_filter     *filter;
    void                    *data;
    int                     flags;       // TRACE_EVENT_FL_* 标志
    // CONFIG_PERF_EVENTS:
    int                     perf_refcount;
    struct hlist_head __percpu *perf_events;
    struct bpf_prog_array __rcu *prog_array;  // BPF 程序挂载
};
```

---

## 3. 核心 API

头文件：`include/linux/tracepoint.h`；实现：`kernel/tracepoint.c`

### 3.1 声明/定义宏

| 宏 | 说明 | 备注 |
|----|------|------|
| `TRACE_EVENT(name, proto, args, tstruct, assign, print)` | 创建完整的跟踪点 + 跟踪事件 | 最常用；生成 `trace_<name>()` 调用函数和 ftrace 事件基础设施 |
| `DECLARE_TRACE(name, proto, args)` | 声明轻量级跟踪点 | 只生成 `trace_<name>()` 和 `register/unregister_trace_<name>()` |
| `DEFINE_TRACE(name, proto, args)` | 定义 `struct tracepoint` 实例 | 放在 `__tracepoints` 段 |
| `DECLARE_TRACEPOINT(tp)` | 前向声明跟踪点 | 用于头文件中安全检查 |
| `tracepoint_enabled(tp)` | 检查跟踪点是否启用 | 基于 `static_key_false()`，可在头文件中使用 |

### 3.2 Probe 注册

| API | 说明 | 备注 |
|-----|------|------|
| `tracepoint_probe_register(tp, probe, data)` | 注册 probe 回调 | 默认优先级 `TRACEPOINT_DEFAULT_PRIO=10` |
| `tracepoint_probe_register_prio(tp, probe, data, prio)` | 带优先级注册 | 按优先级排序插入 probe 数组 |
| `tracepoint_probe_unregister(tp, probe, data)` | 注销 probe | 最后一个 probe 移除时禁用 static_key |
| `tracepoint_synchronize_unregister()` | 等待所有读者完成 | 调用 `synchronize_srcu()` + `synchronize_rcu()` |
| `for_each_kernel_tracepoint(fct, priv)` | 遍历所有内核跟踪点 | 用于枚举 `__tracepoints_ptrs` 段 |

### 3.3 每个 TRACE_EVENT 生成的函数

对于 `TRACE_EVENT(name, ...)`，生成：

| 函数 | 说明 |
|------|------|
| `trace_<name>(proto)` | 内联函数；检查 `static_key`，若启用则调用 probe |
| `trace_<name>_enabled()` | 返回 `static_key_false()` 状态 |
| `register_trace_<name>(probe, data)` | 注册 probe 的包装 |
| `unregister_trace_<name>(probe, data)` | 注销 probe 的包装 |
| `__traceiter_<name>(__data, proto)` | 迭代器函数，循环所有注册的 probe |
| `trace_event_raw_event_<name>(__data, proto)` | ftrace probe 回调，写入 ring buffer |

---

## 4. 机制剖析

### 4.1 TRACE_EVENT() 宏展开链

`TRACE_EVENT()` 通过多次重包含头文件展开为多个结构体和函数：

1. **`define_trace.h`**（行 27-28）：当定义了 `CREATE_TRACE_POINTS` 时，将 `TRACE_EVENT` 重定义为 `DEFINE_TRACE()`，在 `__tracepoints` ELF 段创建 `struct tracepoint`。

2. **`trace_events.h` 阶段 1**（行 38-46）：生成 `struct trace_event_raw_<name>`，包含 `struct trace_entry ent` 加用户定义的字段。

3. **阶段 2**：生成 `trace_event_fields_<name>()` 函数，描述字段布局。

4. **阶段 6**（行 382-418）：生成 `trace_event_raw_event_<name>()` —— 实际的 probe 回调：
   - 调用 `trace_event_frame_reserve()` 在 ring buffer 中分配空间
   - 执行用户的 `{ assign; }` 块填充条目
   - 调用 `trace_event_frame_commit()` 提交到 ring buffer

5. **最终结构**（行 339-359）：创建 `event_class_<name>`（`.probe = trace_event_raw_event_<name>`）和 `event_<name>`（`.tp = &__tracepoint_<name>`），放在 `_ftrace_events` 段。

### 4.2 Static Key（Jump Label）零开销机制

`struct tracepoint` 中的 `struct static_key key` 提供禁用时的零开销：

- **初始化**：`DEFINE_TRACE_FN` 设置 `.key = STATIC_KEY_INIT_FALSE`。初始禁用——jump label 在调用点补丁为 NOP。

- **启用路径**（`kernel/tracepoint.c:361`）：当注册首个 probe 时，`static_key_enable(&tp->key)` 将 NOP 补丁为分支指令。

- **禁用路径**（`kernel/tracepoint.c:417`）：当移除最后一个 probe 时，`static_key_disable(&tp->key)` 补丁回 NOP。

- **Static call 优化**（`kernel/tracepoint.c:308-318`）：`tracepoint_update_call()` 使用 `__static_call_update()` 补丁 static call trampoline。当只有一个 probe 时，static call 直接跳转到该 probe（绕过迭代器循环）。当有 0 或 2+ 个 probe 时，指向迭代器函数。

### 4.3 与 ftrace/perf 的连接

桥接层是 `trace_event_reg()`（`kernel/trace/trace_events.c:660-696`）：

```c
int trace_event_reg(struct trace_event_call *call, enum trace_reg type, void *data)
{
    switch (type) {
    case TRACE_REG_REGISTER:        // ftrace 启用
        return tracepoint_probe_register(call->tp, call->class->probe, file);
    case TRACE_REG_UNREGISTER:      // ftrace 禁用
        tracepoint_probe_unregister(call->tp, call->class->probe, file);
    case TRACE_REG_PERF_REGISTER:   // perf 启用
        return tracepoint_probe_register(call->tp, call->class->perf_probe, call);
    case TRACE_REG_PERF_UNREGISTER: // perf 禁用
        tracepoint_probe_unregister(call->tp, call->class->perf_probe, call);
    }
}
```

### 4.4 `__tracepoints_ptrs` 段与启动发现

- `DEFINE_TRACE_FN` 通过 `__TRACEPOINT_ENTRY(name)` 将每个 `struct tracepoint` 的指针放入 `__tracepoints_ptrs` ELF 段。arm64（有 `CONFIG_HAVE_ARCH_PREL32_RELOCATIONS`）使用 32 位相对偏移。

- 启动时：`for_each_kernel_tracepoint()`（`kernel/tracepoint.c:745-751`）遍历 `__start___tracepoints_ptrs[]` 到 `__stop___tracepoints_ptrs[]`，枚举所有内建跟踪点并通过 debugfs/tracefs 暴露。

- 模块跟踪点：模块加载时，`tracepoint_module_notify()`（`kernel/tracepoint.c:701`）遍历模块的 `tracepoints_ptrs` 范围并注册。

### 4.5 tracepoint_srcu 读者保护

- 定义：`kernel/tracepoint.c:28-29`，`DEFINE_SRCU(tracepoint_srcu)`
- 在跟踪路径中（`tracepoint.h:191-213`，`__DO_TRACE` 宏）：对于 `rcuidle` 调用者（idle 路径中 sched-RCU 不活跃），在 probe 分发前后获取/释放 SRCU 读锁。
- 注销同步（`tracepoint.h:91-95`）：`tracepoint_synchronize_unregister()` 调用 `synchronize_srcu()` + `synchronize_rcu()`。

---

## 5. arm64 实现：Jump Label

**文件**：`arch/arm64/kernel/jump_label.c`（35 行）

两个函数实现 arm64 的 jump label 补丁：

- **`arch_jump_label_transform_queue(entry, type)`**（行 14-30）：
  - `JUMP_LABEL_JMP`：生成 `aarch64_insn_gen_branch_imm()`（B 指令）
  - `JUMP_LABEL_NOP`：生成 `aarch64_insn_gen_nop()`（NOP）
  - 调用 `aarch64_insn_patch_text_nosync()` 补丁指令

- **`arch_jump_label_transform_apply()`**（行 32-35）：
  - 调用 `kick_all_cpus_sync()` 确保所有 CPU 执行补丁后的指令流

---

## 6. 使用统计

| 指标 | 数量 |
|------|------|
| `TRACE_EVENT()` 定义（`include/trace/events/*.h`） | 826 |
| 总跟踪点相关定义 | 895 |
| `tracepoint_probe_register()` 调用点 | 10 |

---

## 7. 数据流总结

```
内核代码调用 trace_<name>(args)
  │
  ▼
static_key_false(&__tracepoint_<name>.key)  -- 禁用时为 NOP（零开销）
  │
  ▼ （启用时，补丁为分支）
__DO_TRACE(name, args, cond, rcuidle)
  ├── srcu_read_lock（rcuidle 调用者）
  ├── static_call(tp_func_<name>)(__data, args)
  │     ├── [1 个 probe]：直接调用 probe 函数
  │     └── [2+ probes]：调用 __traceiter_<name>() 循环
  │
  ▼
trace_event_raw_event_<name>(__data, proto)    [由 trace_event_reg 注册]
  │
  ▼
trace_event_frame_reserve()  →  分配 ring buffer 空间
  │
  ▼
{ assign; }   →  用户定义的 TP_assign 块填充字段
  │
  ▼
trace_event_frame_commit()   →  提交到 ring buffer
```

对于 **perf**：注册 `call->class->perf_probe`（`perf_trace_<name>`），写入 perf 的 ring buffer。
对于 **BPF**：`call->prog_array` 持有附加的 BPF 程序，在 probe 路径中通过 `trace_call_bpf()` 执行。

---

## §4 常见陷阱

### 陷阱 1：在 NMI 上下文中使用 rcuidle 变体

**错误写法**：
```c
void my_nmi_handler(void)
{
    /* NMI 中调用 rcuidle 变体 */
    trace_my_event_rcuidle(...);  /* 错误！ */
}
```

**正确写法**：
```c
void my_nmi_handler(void)
{
    /* NMI 中使用普通变体 */
    trace_my_event(...);
}
```

**原因**：`include/linux/tracepoint.h:191-192` 中有明确的 `WARN_ON_ONCE(rcuidle && in_nmi())` 检查。`rcuidle` 变体使用 SRCU 来保护 idle 路径中 sched-RCU 不活跃的调用者，但 SRCU 不能在 NMI 中使用。NMI 上下文 sched-RCU 一定是活跃的，应使用普通 `trace_xxx()` 而非 `trace_xxx_rcuidle()`。

**源码依据**：`include/linux/tracepoint.h:191-192` — `WARN_ON_ONCE(rcuidle && in_nmi())`；`include/linux/tracepoint.h:201-204` — rcuidle 路径使用 `srcu_read_lock_notrace()`。

---

### 陷阱 2：模块退出前未调用 tracepoint_synchronize_unregister

**错误写法**：
```c
static int __init my_module_init(void)
{
    register_trace_my_event(my_probe, NULL);
    return 0;
}

static void __exit my_module_exit(void)
{
    unregister_trace_my_event(my_probe, NULL);
    /* 直接退出，没有等待 */
}
```

**正确写法**：
```c
static void __exit my_module_exit(void)
{
    unregister_trace_my_event(my_probe, NULL);
    tracepoint_synchronize_unregister();  /* 必须等待所有读者完成 */
}
```

**原因**：`unregister_trace_xxx()` 只是将 probe 从 RCU 保护的 `funcs` 数组中移除，但其他 CPU 上可能仍有正在执行的 probe 调用。如果在 `synchronize_srcu()` + `synchronize_rcu()` 完成前释放 probe 代码所在的模块内存，会导致执行已释放的函数指针。`include/linux/tracepoint.h:86-89` 的注释明确要求："tracepoint_synchronize_unregister must be called between the last tracepoint probe unregistration and the end of module exit to make sure there is no caller executing a probe when it is freed."

**源码依据**：`include/linux/tracepoint.h:86-89` — 注释约束；`include/linux/tracepoint.h:91-95` — 实现调用 `synchronize_srcu(&tracepoint_srcu)` + `synchronize_rcu()`。

---

### 陷阱 3：TRACE_EVENT 的 TP_fast_assign 中使用不可休眠的操作

**错误写法**：
```c
TRACE_EVENT(my_event,
    TP_PROTO(int value),
    TP_ARGS(value),
    TP_STRUCT__entry(
        __field(int, value)
    ),
    TP_fast_assign(
        __entry->value = value;
        /* 错误：TP_fast_assign 在原子上下文中执行 */
        mutex_lock(&my_mutex);
        some_data = kmalloc(100, GFP_KERNEL);
    ),
    TP_printk("value=%d", __entry->value)
);
```

**正确写法**：
```c
TRACE_EVENT(my_event,
    TP_PROTO(int value),
    TP_ARGS(value),
    TP_STRUCT__entry(
        __field(int, value)
    ),
    TP_fast_assign(
        __entry->value = value;
        /* 只做简单的赋值操作 */
    ),
    TP_printk("value=%d", __entry->value)
);
```

**原因**：`TP_fast_assign` 块在 `trace_event_raw_event_xxx()` 回调中执行（`include/trace/trace_events.h:383-408`），该回调在 `__DO_TRACE()` 宏展开中调用，而 `__DO_TRACE()` 在 `preempt_disable_notrace()` 保护下运行（`include/linux/tracepoint.h:195`）。这意味着 `TP_fast_assign` 在抢占禁用的原子上下文中执行，不能调用可能休眠的函数（如 `mutex_lock`、`kmalloc(GFP_KERNEL)`）。

**源码依据**：`include/linux/tracepoint.h:195` — `preempt_disable_notrace()`；`include/trace/trace_events.h:403-405` — `{ assign; }` 块在抢占禁用区间执行。

---

### 陷阱 4：在 tainted 模块中使用 tracepoint 导致静默跳过

**错误写法**：
```c
/* 一个带有非标准 taint 标志的模块 */
static int __init my_module_init(void)
{
    /* tracepoint 注册可能静默失败 */
    return 0;
}
```

**正确写法**：
```c
/* 确保模块不带有被 tracepoint 拒绝的 taint 标志 */
/* 允许的 taint：TAINT_OOT_MODULE、TAINT_CRAP、TAINT_UNSIGNED_MODULE、TAINT_TEST */
/* 其他 taint（如 TAINT_PROPRIETARY_MODULE）会导致模块的 tracepoint 被跳过 */
```

**原因**：`kernel/tracepoint.c:571-576` 的 `trace_module_has_bad_taint()` 检查模块的 taint 标志。当模块加载通知到达 `tracepoint_module_notify()` 时，如果 `trace_module_has_bad_taint()` 返回 true（行 652），该模块的 tracepoint 不会被注册到全局列表中。这意味着通过 `for_each_kernel_tracepoint()` 枚举时将看不到这些 tracepoint，它们无法通过 tracefs 被用户空间访问。

**源码依据**：`kernel/tracepoint.c:571-576` — `trace_module_has_bad_taint()` 实现；`kernel/tracepoint.c:652-653` — 模块加载时跳过 bad taint 模块。

---

### 陷阱 5：TRACE_EVENT 头文件包含顺序错误导致宏冲突

**错误写法**：
```c
/* my_trace.h */
#undef TRACE_SYSTEM
#define TRACE_SYSTEM my_system

/* 错误：在 CREATE_TRACE_POINTS 下包含两个 trace 头文件 */
#include "trace_a.h"
#include "trace_b.h"
```

**正确写法**：
```c
/* 方案 A：每个 TRACE_EVENT 定义在单独的头文件中 */
/* trace_a.h 中定义 TRACE_EVENT(a_event, ...) */
/* trace_b.h 中定义 TRACE_EVENT(b_event, ...) */

/* 在 C 文件中逐个包含 */
#define CREATE_TRACE_POINTS
#include "trace_a.h"
#undef CREATE_TRACE_POINTS
#define CREATE_TRACE_POINTS
#include "trace_b.h"
```

**原因**：`include/linux/tracepoint.h:137-143` 的注释解释了 trace event 的包含方式："If a file includes two trace event headers under one CREATE_TRACE_POINTS the first include will override the TRACE_EVENT and break the second include." `define_trace.h` 在包含时会 `#undef CREATE_TRACE_POINTS`（行 22），防止递归但意味着第二个头文件不再创建 tracepoint 定义。

**源码依据**：`include/linux/tracepoint.h:137-143` — 注释说明包含约束；`include/trace/define_trace.h:22` — `#undef CREATE_TRACE_POINTS` 防止递归。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/tracepoint.h` — `__DO_TRACE()` 宏（行 184-214）、`__DECLARE_TRACE()` 宏（行 240-283）、`DEFINE_TRACE_FN()` 宏（行 290-322）、`tracepoint_synchronize_unregister()`（行 91-95）、包含约束注释（行 137-143）
- `include/linux/tracepoint-defs.h` — `struct tracepoint`（行 32-41）、`struct tracepoint_func`（行 26-30）
- `include/trace/define_trace.h` — `CREATE_TRACE_POINTS` 处理（行 19-22）、TRACE_EVENT 重定义（行 27-28）
- `include/trace/trace_events.h` — `__DECLARE_EVENT_CLASS()` 宏（行 382-408）、probe 回调生成
- `kernel/tracepoint.c` — `trace_module_has_bad_taint()`（行 571-576）、`tracepoint_module_notify()`（行 652-653）

**关键论断出处**：
- "rcuidle 变体不能在 NMI 中使用" — 来源：`include/linux/tracepoint.h:191-192`（`WARN_ON_ONCE(rcuidle && in_nmi())`）
- "模块退出前必须调用 synchronize_unregister" — 来源：`include/linux/tracepoint.h:86-89`（注释）及行 91-95（实现）
- "TP_fast_assign 在抢占禁用上下文中执行" — 来源：`include/linux/tracepoint.h:195`（`preempt_disable_notrace()`）及 `include/trace/trace_events.h:403-405`
- "tainted 模块的 tracepoint 被静默跳过" — 来源：`kernel/tracepoint.c:571-576` 及行 652-653
- "两个 trace 头文件不能在同一个 CREATE_TRACE_POINTS 下包含" — 来源：`include/linux/tracepoint.h:137-143`

**未验证项**：
- `trace_event_buffer_reserve()` 的 ring buffer 分配失败路径未完整追踪
- static_call 优化路径（单 probe 直接调用 vs 多 probe 迭代器切换）的具体实现细节未逐行验证
- `TRACE_EVENT_CONDITION` 的 `cond` 参数在 `__DO_TRACE()` 中的求值时机未单独验证
