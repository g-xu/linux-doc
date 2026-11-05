# ftrace — 内核函数跟踪框架

> **定位**：ftrace 是 Linux 内核的函数级动态跟踪框架。它通过编译器在每个函数入口插入的探测点（mcount / patchable-function-entry），实现函数调用跟踪、函数图跟踪、延迟分析、事件过滤等功能。本文覆盖 ftrace 核心引擎（`kernel/trace/ftrace.c`）、函数图跟踪（`kernel/trace/fgraph.c`）、以及 tracefs 用户接口。tracepoint 子系统在 `tracepoint.md` 中单独覆盖。

---

## 1. 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/trace/ftrace.c` | 8488 | 核心引擎：注册、动态补丁、hash 过滤、trampoline、direct call |
| `kernel/trace/trace.c` | 10505 | 顶层追踪基础设施：ring buffer 管理、tracefs 接口、tracer 切换 |
| `kernel/trace/trace.h` | 2038 | 内部头文件：`ftrace_hash`、`ftrace_probe_ops`、`trace_array` |
| `kernel/trace/trace_functions.c` | 968 | function tracer 实现 |
| `kernel/trace/fgraph.c` | 664 | function graph tracer 核心 |
| `kernel/trace/fprobe.c` | 334 | fprobe：基于 ftrace 的探针（BPF 使用） |
| `kernel/trace/rethook.c` | 346 | 返回钩子机制 |
| `kernel/trace/trace_events.c` | 4254 | trace event 子系统（ftrace ↔ tracepoint 桥接） |
| `include/linux/ftrace.h` | 1146 | 公开 API：`ftrace_ops`、`dyn_ftrace`、所有导出函数 |
| `arch/arm64/kernel/entry-ftrace.S` | 335 | arm64 汇编 trampoline |
| `arch/arm64/kernel/ftrace.c` | 329 | arm64 C 层：动态补丁、graph 支持 |
| `arch/arm64/include/asm/ftrace.h` | 127 | arm64 ftrace 定义 |

---

## 2. 核心数据结构

### 2.1 struct ftrace_ops（`include/linux/ftrace.h:277-292`）

ftrace 的核心抽象。每个用户注册一个 `ftrace_ops`，框架通过链表管理所有注册的 ops。

```c
struct ftrace_ops {
    ftrace_func_t             func;           // 回调函数
    struct ftrace_ops __rcu   *next;          // RCU 链表
    unsigned long             flags;          // FTRACE_OPS_FL_* 标志
    void                      *private;       // 用户私有数据
    ftrace_func_t             saved_func;     // RCU 过渡期保存
#ifdef CONFIG_DYNAMIC_FTRACE
    struct ftrace_ops_hash    local_hash;     // filter/notrace 哈希
    struct ftrace_ops_hash    *func_hash;     // 指向 local_hash 或全局
    struct ftrace_ops_hash    old_hash;       // 更新期间的旧哈希
    unsigned long             trampoline;     // 自定义架构 trampoline 地址
    unsigned long             trampoline_size;
    struct list_head          list;
    ftrace_ops_func_t         ops_func;       // 命令回调
#endif
};
```

**回调签名**（`include/linux/ftrace.h:129-130`）：
```c
typedef void (*ftrace_func_t)(unsigned long ip, unsigned long parent_ip,
                              struct ftrace_ops *op, struct ftrace_regs *fregs);
```

- `ip`：被跟踪函数的地址（callsite PC）
- `parent_ip`：调用者地址（LR / 返回地址）
- `op`：本 ftrace_ops 指针
- `fregs`：保存的寄存器上下文

### 2.2 FTRACE_OPS_FL_* 标志（`include/linux/ftrace.h:190-209`）

共 18 个标志（BIT(0) ~ BIT(17)），关键标志：

| 标志 | 含义 |
|------|------|
| `ENABLED` | ops 已注册生效 |
| `SAVE_REGS` | 要求保存完整 pt_regs |
| `SAVE_REGS_IF_SUPPORTED` | 架构支持则保存 regs |
| `RECURSION` | 需要递归保护 |
| `IPMODIFY` | 可修改 IP（需配合 SAVE_REGS） |
| `PID` | 按 PID 过滤 |
| `PERMANENT` | 不可卸载（模块引用计数保护） |
| `DIRECT` | 使用 direct call |
| `ALLOC_TRAMP` | trampoline 由架构代码动态分配 |

### 2.3 struct ftrace_hash（`kernel/trace/trace.h:812-818`）

过滤机制的底层数据结构：

```c
struct ftrace_hash {
    unsigned long       size_bits;     // 桶数量 = 2^size_bits
    struct hlist_head   *buckets;      // 哈希桶数组
    unsigned long       count;         // 条目数
    unsigned long       flags;
    struct rcu_head     rcu;
};
```

### 2.4 struct dyn_ftrace（`include/linux/ftrace.h:563-567`）

每个被跟踪的函数调用点对应一个 `dyn_ftrace` 记录：

```c
struct dyn_ftrace {
    unsigned long           ip;    // mcount 调用点地址
    unsigned long           flags; // 启用/禁用/转换中等状态
    struct dyn_arch_ftrace  arch;  // arm64 上为空
};
```

### 2.5 struct ftrace_probe_ops（`kernel/trace/trace.h:1049-1066`）

函数探针的操作回调，用于 event trigger 机制：

```c
struct ftrace_probe_ops {
    void (*func)(unsigned long ip, unsigned long parent_ip,
                 struct trace_array *tr, struct ftrace_probe_ops *ops, void *data);
    int  (*init)(struct ftrace_probe_ops *ops, struct trace_array *tr,
                 unsigned long ip, void *init_data, void **data);
    void (*free)(struct ftrace_probe_ops *ops, struct trace_array *tr,
                 unsigned long ip, void *data);
    int  (*print)(struct seq_file *m, unsigned long ip,
                  struct ftrace_probe_ops *ops, void *data);
};
```

---

## 3. 核心 API

### 3.1 注册/注销

头文件：`include/linux/ftrace.h`；实现：`kernel/trace/ftrace.c`

| API | 说明 | 备注 |
|-----|------|------|
| `register_ftrace_function(ops)` | 注册 ftrace_ops 回调 | `EXPORT_SYMBOL_GPL`；内部调用 `prepare_direct_functions_for_ipmodify` 后走 `register_ftrace_function_nolock` |
| `unregister_ftrace_function(ops)` | 注销 ftrace_ops | `EXPORT_SYMBOL_GPL`；调用 `ftrace_shutdown` |

### 3.2 过滤器设置

| API | 说明 | 备注 |
|-----|------|------|
| `ftrace_set_filter(ops, buf, len, reset)` | 按 glob 模式设置过滤 | `EXPORT_SYMBOL_GPL`；支持 `*`、`?`、`!` 前缀 |
| `ftrace_set_notrace(ops, buf, len, reset)` | 按 glob 模式设置排除 | `EXPORT_SYMBOL_GPL` |
| `ftrace_set_filter_ip(ops, ip, remove, reset)` | 按精确 IP 设置过滤 | `EXPORT_SYMBOL_GPL` |
| `ftrace_set_filter_ips(ops, ips, cnt, remove, reset)` | 批量 IP 过滤 | `EXPORT_SYMBOL_GPL` |
| `ftrace_set_global_filter(buf, len, reset)` | 设置全局过滤 | `EXPORT_SYMBOL_GPL` |
| `ftrace_set_global_notrace(buf, len, reset)` | 设置全局排除 | `EXPORT_SYMBOL_GPL` |
| `ftrace_ops_set_global_filter(ops)` | 让 ops 使用全局过滤 | `EXPORT_SYMBOL_GPL` |
| `ftrace_free_filter(ops)` | 释放过滤内存 | `EXPORT_SYMBOL_GPL`；仅在 ops 注销后调用 |

### 3.3 Direct Call

| API | 说明 | 备注 |
|-----|------|------|
| `register_ftrace_direct(ip, addr)` | 在 IP 处注册直接调用 | `EXPORT_SYMBOL_GPL`；跳过 ftrace 框架直接跳转 |
| `unregister_ftrace_direct(ip, addr)` | 注销直接调用 | |
| `modify_ftrace_direct(ip, old, new)` | 修改直接调用目标 | |
| `register_ftrace_direct_multi(ops, addr)` | 通过 ops 过滤批量注册 | |
| `unregister_ftrace_direct_multi(ops, addr)` | 注销批量直接调用 | |

### 3.4 函数图跟踪

头文件：`include/linux/ftrace.h:1065-1066`

| API | 说明 | 备注 |
|-----|------|------|
| `register_ftrace_graph(ops)` | 注册函数图跟踪 ops | `struct fgraph_ops` 包含 entry/return 回调 |
| `unregister_ftrace_graph(ops)` | 注销函数图跟踪 | |

---

## 4. 机制剖析

### 4.1 函数调用点的插入方式

ftrace 依赖编译器在函数入口插入探测点：

- **`-pg`（传统方式）**：GCC 在函数入口插入 `mov x0, x30; bl _mcount`。`_mcount` 检查 `ftrace_trace_function != ftrace_stub`，若已注册则调用跟踪回调。
- **`-fpatchable-function-entry=2`（现代方式）**：编译器在函数序言前放置两个 NOP。ftrace 在运行时将它们补丁为：
  ```asm
  MOV X9, LR       // 保存返回地址到 X9
  BL  <entry>       // 跳转到 ftrace_caller 或 ftrace_regs_caller
  ```
  这是 `CONFIG_DYNAMIC_FTRACE_WITH_REGS` 路径，arm64 的默认方式。

### 4.2 动态 ftrace：运行时指令补丁

核心流程：

1. **启动时发现**：`ftrace_process_locs()` 读取 `__mcount_loc` 段（编译器/recordmcount 放置的调用点地址），为每个调用点创建 `dyn_ftrace` 记录。

2. **初始状态**：所有调用点都是 NOP（或 `MOV X9, LR; NOP` 对）。

3. **启用**：`ftrace_make_call()`（`arch/arm64/kernel/ftrace.c:143`）使用 `aarch64_insn_gen_branch_imm()` 将 NOP 补丁为 `BL ftrace_caller`。

4. **禁用**：`ftrace_make_nop()`（`arch/arm64/kernel/ftrace.c:214`）补丁回 NOP。

5. **协调更新**：`ftrace_modify_all_code()`（`kernel/trace/ftrace.c:2769`）协调整个更新过程。arm64 通过 `arch_ftrace_update_code()`（`arch/arm64/kernel/ftrace.c:244`）调用，带 `FTRACE_MAY_SLEEP` 标志。

6. **模块 PLT**：由于 `BL` 指令只有 ±128MB 范围，arm64 对距离内核超过 128MB 的模块使用 PLT trampoline。见 `ftrace_find_callable_addr()`（`arch/arm64/kernel/ftrace.c:89`）。

### 4.3 ftrace_ops 链表与 trampoline 选择

当多个 `ftrace_ops` 注册时：

- **单个 ops**：如果只有一个 ops 且有过滤器，trampoline 可以直接调用 `ops->func`（快速路径）。
- **多个 ops**：trampoline 调用 `ftrace_ops_list_func()`，它遍历 `ftrace_ops_list` RCU 链表，对每个 ops 调用 `ftrace_ops_test()`（`kernel/trace/ftrace.c:1474`）检查过滤哈希。
- **递归/RCU 安全**：如果设置了 `FTRACE_OPS_FL_RECURSION` 或 `FTRACE_OPS_FL_RCU`，`ftrace_ops_get_func()` 返回 `ftrace_ops_assist_func` 而不是 `ops->func`。

### 4.4 过滤/哈希决策逻辑

每个 `ftrace_ops` 携带一个 `ftrace_ops_hash`，包含：
- `filter_hash`：非空时只跟踪此哈希中的 IP
- `notrace_hash`：此哈希中的 IP 被排除

`ftrace_ops_test()`（`kernel/trace/ftrace.c:1474`）的决策逻辑：
```
match = (filter_hash 为空 OR ip 在 filter_hash 中)
     AND (notrace_hash 为空 OR ip 不在 notrace_hash 中)
```

### 4.5 函数图跟踪

函数图跟踪器（`kernel/trace/fgraph.c`）同时钩住函数入口和返回：

1. **入口**：`prepare_ftrace_return()`（`arch/arm64/kernel/ftrace.c:258`）将栈上的返回地址替换为 `return_to_handler`。
2. **返回**：函数返回时跳转到 `return_to_handler`（`entry-ftrace.S:313`），保存返回值寄存器（x0-x7），调用 `ftrace_return_to_handler()` 获取真实返回地址，恢复寄存器后返回真实调用者。
3. **`DYNAMIC_FTRACE_WITH_REGS` 路径**：`ftrace_graph_func()`（`arch/arm64/kernel/ftrace.c:283`）直接修改保存的 `pt_regs` 中的 LR，避免基于栈的方式。

---

## 5. arm64 实现细节

### 5.1 汇编 trampoline（`arch/arm64/kernel/entry-ftrace.S`）

- **`ftrace_regs_caller`**（行 80）：保存所有寄存器（完整 `pt_regs`），然后落入 `ftrace_common`。
- **`ftrace_caller`**（行 86）：只保存 caller-saved 寄存器（x0-x8, x29/FP），然后落入 `ftrace_common`。
- **`ftrace_common`**（行 92）：设置参数：
  - `x0` = `ip`（callsite PC，调整 -4）
  - `x1` = `parent_ip`（原始 LR，来自 X9）
  - `x2` = `function_trace_op`（从全局变量加载）
  - `x3` = `sp`（指向保存的 `pt_regs`）
  - 然后 `bl ftrace_stub`（通过 `ftrace_call` 标签补丁为实际处理函数）
  - 返回后恢复所有寄存器，`ret x9`（使用保存的 PC）。

### 5.2 arm64 特有特性

| 特性 | 位置 | 说明 |
|------|------|------|
| `ARCH_SUPPORTS_FTRACE_OPS` | `asm/ftrace.h:27` | 仅在 `DYNAMIC_FTRACE_WITH_REGS` 下定义，启用 per-ops trampoline |
| 模块 PLT 支持 | `ftrace.c:66-78` | `FTRACE_PLT_IDX` 和 `FTRACE_REGS_PLT_IDX`，模块距内核 >128MB 时使用 |
| `ftrace_init_nop` | `ftrace.c:198` | 将第一个 NOP 补丁为 `MOV X9, LR` |
| `ftrace_call_adjust` | `asm/ftrace.h:66-79` | 调整 `rec->ip` 指向 BL 指令 |
| `HAVE_FUNCTION_GRAPH_RET_ADDR_PTR` | `asm/ftrace.h:24` | 使用帧记录地址作为返回地址的唯一标识 |
| `ARCH_TRACE_IGNORE_COMPAT_SYSCALLS` | `asm/ftrace.h:106` | 忽略 AArch32 兼容系统调用 |
| Shadow Call Stack | `entry-ftrace.S:28` | x18 保存 SCS 指针，在 trampoline 中被保留 |

---

## 6. tracefs 用户接口

tracefs 挂载在 `/sys/kernel/tracing/`（或旧系统的 `/sys/kernel/debug/tracing/`）。

### 6.1 主要文件

| 文件 | 权限 | 用途 |
|------|------|------|
| `available_tracers` | R | 列出所有可用的 tracer 插件 |
| `current_tracer` | W | 选择当前活跃的 tracer |
| `trace` | RW | 读取当前跟踪缓冲区 |
| `trace_pipe` | R | 流式跟踪输出（读后消费） |
| `trace_options` | W | tracer 选项标志 |
| `tracing_on` | W | 全局跟踪开关 |
| `buffer_size_kb` | W | 每 CPU 缓冲区大小 |
| `trace_clock` | W | 时钟源选择 |
| `trace_marker` | W | 用户空间标记写入 |
| `snapshot` | W | 快照缓冲区控制 |
| `set_ftrace_filter` | W | 函数跟踪过滤 |
| `set_ftrace_notrace` | W | 函数跟踪排除 |
| `set_ftrace_pid` | W | 按 PID 过滤 |
| `function_profile_enabled` | W | 函数概要分析 |
| `error_log` | W | 错误日志 |
| `events/` | 目录 | trace event 配置 |
| `instances/` | 目录 | 多跟踪实例 |

### 6.2 典型使用

```bash
# 启用函数跟踪
echo function > /sys/kernel/tracing/current_tracer
echo 1 > /sys/kernel/tracing/tracing_on
cat /sys/kernel/tracing/trace

# 过滤特定函数
echo do_sys_open > /sys/kernel/tracing/set_ftrace_filter

# 函数图跟踪
echo function_graph > /sys/kernel/tracing/current_tracer

# 使用 ftrace 过滤
echo 'sched_*' > /sys/kernel/tracing/set_ftrace_filter
```

---

## 7. 使用统计

| 模式 | 使用文件数 |
|------|-----------|
| `ftrace_ops` | 43 |
| `register_ftrace_function` | 18 |
| `ftrace_set_filter` | 7 |
| `register_ftrace_direct` | 11 |

主要消费者：
- `kernel/trace/trace_functions.c` — function tracer 自身
- `kernel/trace/trace_irqsoff.c` — irqsoff 延迟跟踪器
- `kernel/trace/trace_sched_switch.c` — 调度器跟踪
- `kernel/bpf/trampoline.c` — BPF trampoline 集成
- `samples/ftrace/` — 示例模块

---

## 8. 与 tracepoint 的关系

ftrace 和 tracepoint 是独立但深度互联的子系统：

- **ftrace 函数跟踪**通过编译器插入的调用点钩住函数入口/返回。
- **tracepoint** 是在内核特定位置静态放置的标记，通过 `trace_xxx()` 宏调用。
- **桥接层**：`trace_event_reg()`（`kernel/trace/trace_events.c:660-696`）将两者连接。当通过 ftrace 的 `events/` 接口启用事件时，它调用 `tracepoint_probe_register()` 将 ftrace 的探针处理函数注册到底层内核 tracepoint 上。
- **共享基础设施**：两者共享 ring buffer 和 tracefs 输出。

详见 `tracepoint.md`。

---

## §4 常见陷阱

### 陷阱 1：注册后修改 ftrace_ops 的属性标志

**错误写法**：
```c
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS,
};

register_ftrace_function(&my_ops);
/* 注册后追加 IPMODIFY 标志 */
my_ops.flags |= FTRACE_OPS_FL_IPMODIFY;  /* 错误！ */
```

**正确写法**：
```c
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS | FTRACE_OPS_FL_IPMODIFY,
};
/* 所有属性标志必须在 register 之前一次性设好 */
register_ftrace_function(&my_ops);
```

**原因**：`include/linux/ftrace.h:137-141` 的注释明确说明：`CONTROL, SAVE_REGS, SAVE_REGS_IF_SUPPORTED, RECURSION, STUB and IPMODIFY are a kind of attribute flags which can be set only before registering the ftrace_ops, and can not be modified while registered. Changing those attribute flags after registering ftrace_ops will cause unexpected results.` 注册后框架已根据原始 flags 选择了 trampoline 和调用路径，中途修改会导致 trampoline 与实际回调不匹配。

**源码依据**：`include/linux/ftrace.h:137-141` — 属性标志注册后不可修改的约束声明；`kernel/trace/ftrace.c:299-340` — `__register_ftrace_function()` 在注册时一次性读取 flags 并设置 trampoline。

---

### 陷阱 2：忘记设置 FTRACE_OPS_FL_RECURSION 导致回调被递归调用

**错误写法**：
```c
static void my_callback(unsigned long ip, unsigned long parent_ip,
                        struct ftrace_ops *op, struct ftrace_regs *fregs)
{
    /* 回调内部调用了被跟踪的函数（如 kmalloc） */
    spin_lock(&my_lock);  /* spin_lock 本身也被 ftrace 跟踪 */
    ...
}

static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS,  /* 缺少 RECURSION */
};
```

**正确写法**：
```c
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS | FTRACE_OPS_FL_RECURSION,
};
```

**原因**：如果不设置 `FTRACE_OPS_FL_RECURSION`，ftrace 框架假定回调自行处理递归。当回调内部调用的函数也被 ftrace 跟踪时，回调会被重入调用，导致栈溢出或死锁。设置此标志后，框架在 `ftrace_ops_list_func()` 遍历中通过 `trace_recursion` 位图检测并跳过递归调用。

**源码依据**：`include/linux/ftrace.h:160-163` — `RECURSION` 标志的注释说明；`kernel/trace/ftrace.c:204` — 设置此标志时 `ftrace_ops_get_func()` 返回 `ftrace_ops_assist_func` 而非直接返回 `ops->func`，由 assist 函数提供递归保护。

---

### 陷阱 3：ftrace_free_filter 在 ops 仍注册时调用

**错误写法**：
```c
register_ftrace_function(&my_ops);
/* ... 使用 ... */
ftrace_free_filter(&my_ops);  /* 错误！ops 还在链表中 */
unregister_ftrace_function(&my_ops);
```

**正确写法**：
```c
register_ftrace_function(&my_ops);
/* ... 使用 ... */
unregister_ftrace_function(&my_ops);
ftrace_free_filter(&my_ops);  /* 注销后才能释放 */
```

**原因**：`ftrace_free_filter()` 直接释放 `ops->func_hash` 中的 `filter_hash` 和 `notrace_hash`。如果 ops 仍在注册状态，其他 CPU 上的 ftrace 调用路径仍在通过 RCU 读取这些哈希表，释放会导致 use-after-free。`kernel/trace/ftrace.c:5856` 的注释明确说明："Must only be used after the ops is unregistered."

**源码依据**：`kernel/trace/ftrace.c:1249-1258` — `ftrace_free_filter()` 实现，直接 `free_ftrace_hash()`；`kernel/trace/ftrace.c:5856` — 注释要求注销后使用。

---

### 陷阱 4：IPMODIFY 与 DIRECT 标志互斥但代码未检查

**错误写法**：
```c
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS | FTRACE_OPS_FL_IPMODIFY | FTRACE_OPS_FL_DIRECT,
};
register_ftrace_function(&my_ops);
```

**正确写法**：
```c
/* IPMODIFY 和 DIRECT 不能同时设置，只能二选一 */
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS | FTRACE_OPS_FL_IPMODIFY,
};
```

**原因**：`kernel/trace/ftrace.c:1916` 中有 `WARN_ON_ONCE(is_ipmodify && is_direct)` 检查，两者同时设置会触发警告并被跳过。IPMODIFY 允许回调修改指令指针（用于 livepatch），DIRECT 绕过 ftrace 框架直接跳转——两者的语义互斥。

**源码依据**：`kernel/trace/ftrace.c:1909-1917` — `ftrace_hash_ipmodify_update()` 中检测互斥；`include/linux/ftrace.h:178-181` — IPMODIFY 注释说明需要配合 SAVE_REGS。

---

### 陷阱 5：SAVE_REGS 在不支持 WITH_REGS 的架构上注册失败

**错误写法**：
```c
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS,  /* 硬要求 */
};
register_ftrace_function(&my_ops);  /* 在不支持的架构上返回 -EINVAL */
```

**正确写法**：
```c
static struct ftrace_ops my_ops = {
    .func  = my_callback,
    .flags = FTRACE_OPS_FL_SAVE_REGS_IF_SUPPORTED,  /* 宽容版本 */
};
register_ftrace_function(&my_ops);
/* 回调中检查 fregs 是否为 NULL */
static void my_callback(unsigned long ip, unsigned long parent_ip,
                        struct ftrace_ops *op, struct ftrace_regs *fregs)
{
    struct pt_regs *regs = ftrace_get_regs(fregs);
    if (!regs) {
        /* 架构不支持保存 regs，降级处理 */
        return;
    }
    /* 正常使用 regs */
}
```

**原因**：`kernel/trace/ftrace.c:313-315` 中，如果架构未定义 `CONFIG_DYNAMIC_FTRACE_WITH_REGS` 且只设置了 `SAVE_REGS`（没有同时设置 `SAVE_REGS_IF_SUPPORTED`），注册直接返回 `-EINVAL`。arm64 在 6.1 内核中支持 `DYNAMIC_FTRACE_WITH_REGS`，但编写可移植代码时应使用 `SAVE_REGS_IF_SUPPORTED`。

**源码依据**：`kernel/trace/ftrace.c:307-319` — 注册时的架构能力检查；`include/linux/ftrace.h:147-158` — SAVE_REGS vs SAVE_REGS_IF_SUPPORTED 的注释说明。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/ftrace.h` — ftrace_ops 结构体定义（行 277-292）、FTRACE_OPS_FL_* 标志枚举（行 190-209）、回调签名（行 129-130）、属性标志约束注释（行 137-189）
- `kernel/trace/ftrace.c` — `__register_ftrace_function()`（行 299-340）、`ftrace_free_filter()`（行 1249-1258）、`ftrace_hash_ipmodify_update()`（行 1909-1917）、`register_ftrace_function()` 包装（行 8239-8283）
- `kernel/trace/fgraph.c` — 函数图跟踪核心实现
- `arch/arm64/kernel/ftrace.c` — arm64 动态补丁、graph 支持
- `arch/arm64/kernel/entry-ftrace.S` — arm64 汇编 trampoline

**关键论断出处**：
- "属性标志注册后不可修改" — 来源：`include/linux/ftrace.h:137-141`
- "SAVE_REGS 在无 WITH_REGS 架构上注册失败" — 来源：`kernel/trace/ftrace.c:313-315`
- "ftrace_free_filter 仅在注销后调用" — 来源：`kernel/trace/ftrace.c:5856`（注释）及 `kernel/trace/ftrace.c:1249-1258`（实现）
- "IPMODIFY 与 DIRECT 互斥" — 来源：`kernel/trace/ftrace.c:1916`（`WARN_ON_ONCE(is_ipmodify && is_direct)`）
- "RECURSION 标志启用 assist 函数" — 来源：`kernel/trace/ftrace.c:204`

**未验证项**：
- `ftrace_ops_assist_func` 的完整递归保护逻辑未逐行展开（仅确认其作为 `ftrace_ops_get_func()` 的返回值）
- arm64 PLT trampoline 的具体距离计算（`ftrace_find_callable_addr()` 内部细节）未完整追踪
- BPF trampoline 与 ftrace direct call 的交互冲突场景未覆盖
