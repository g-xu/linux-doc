# 调试杂项 — dynamic_debug / stacktrace / BUG·WARN / kallsyms

> 本文覆盖四个 B 级调试模块的简要介绍。

---

## 1. dynamic_debug

**核心文件**：`lib/dynamic_debug.c`（1460 行）、`include/linux/dynamic_debug.h`（350 行）

### 定位

**功能**：提供内核调试日志的运行时逐调用点控制能力。开发者在代码中使用 `pr_debug()`/`dev_dbg()` 埋点，无需重新编译即可通过 debugfs 选择性启用或禁用任意调用点的输出，实现按需调试。

**实现方式**：每个 `pr_debug()`/`dev_dbg()` 调用点通过 `DEFINE_DYNAMIC_DEBUG_METADATA()` 在 `__dyndbg` ELF 段创建一个 `struct _ddebug` 条目，记录模块名、函数名、文件名、行号和格式字符串。使用 jump label 机制：当 `_DPRINTK_FLAGS_PRINT` 标志为 0 时调用为 NOP，写入 `<debugfs>/dynamic_debug/control` 由 `ddebug_change()`（`lib/dynamic_debug.c:173`）翻转标志激活或停用调用点。

**应用场景**：全内核广泛使用——`pr_debug` 1087 处、`dev_dbg` 1712 处调用点，覆盖 `drivers/`、`net/`、`sound/`、`mm/`、`crypto/` 等几乎所有子系统。是内核开发者调试硬件驱动、协议栈、文件系统时最常用的动态日志工具。

### 机制

每个 `pr_debug()`/`dev_dbg()` 调用点通过 `DEFINE_DYNAMIC_DEBUG_METADATA()` 在 `__dyndbg` ELF 段创建一个 `struct _ddebug` 条目（`include/linux/dynamic_debug.h:16-50`）。字段包含：`modname`、`function`、`filename`、`format`、`lineno`、`flags`。

启动时 `ddebug_add_module()`（`lib/dynamic_debug.c:1270`）注册段边界（`__start___dyndbg`..`__stop___dyndbg`）。使用 jump label 机制：当 `_DPRINTK_FLAGS_PRINT` 标志为 0 时调用为 NOP。

### 控制方式

- 写入 `<debugfs>/dynamic_debug/control`
- 内核参数 `dyndbg=` / `$module.dyndbg=`
- `ddebug_change()`（`lib/dynamic_debug.c:173`）翻转标志激活/停用调用点

### 标志位

| 标志 | 含义 |
|------|------|
| `_DPRINTK_FLAGS_PRINT` | 启用打印 |
| `_DPRINTK_FLAGS_INCL_MODNAME` | 包含模块名 |
| `_DPRINTK_FLAGS_INCL_FUNCNAME` | 包含函数名 |
| `_DPRINTK_FLAGS_INCL_LINENO` | 包含行号 |
| `_DPRINTK_FLAGS_INCL_TID` | 包含线程 ID |

### 使用量

`pr_debug` 13062 处、`dev_dbg` 24617 处调用点。

---

## 2. stacktrace

**核心文件**：`kernel/stacktrace.c`（402 行）、`include/linux/stacktrace.h`（111 行）、`arch/arm64/kernel/stacktrace.c`（232 行）

### 定位

**功能**：提供统一的内核栈回溯（stack backtrace）能力，支持保存当前上下文、指定任务、或从 `pt_regs` 出发的栈回溯，输出为地址数组供上层消费者使用。

**实现方式**：`stack_trace_save()` 系列函数（`kernel/stacktrace.c:112`）调用架构相关的 `arch_stack_walk()`。arm64 实现（`arch/arm64/kernel/stacktrace.c:198`）基于帧指针展开：循环调用 `unwind_next()` 读取 FP+0（前一个 FP）和 FP+8（返回地址 PC），验证链的合法性（无循环、栈边界检查）。支持 task、IRQ、overflow（VMAP_STACK）、SDEI、EFI 等多种栈类型。

**应用场景**：`stack_trace_save` 在 29 个文件的 39 处被调用，主要消费者包括 `kernel/locking/lockdep.c`（锁依赖检测）、`mm/kmemleak.c`（内存泄漏检测）、`mm/page_owner.c`（页面分配者追踪）、`kernel/trace/trace_stack.c`（ftrace 栈追踪）、`mm/kasan/common.c`（KASAN 报告）、`lib/fault-inject.c`（故障注入栈过滤）。`arch_stack_walk` 还被 `arch/arm64/kernel/perf_callchain.c`（perf 调用链）和 `arch/arm64/kernel/return_address.c` 使用。

### 核心 API

| API | 位置 | 说明 |
|-----|------|------|
| `stack_trace_save(store, size, skipnr)` | `stacktrace.c:112` | 保存当前栈回溯 |
| `stack_trace_save_tsk(task, store, size, skipnr)` | `stacktrace.c:136` | 保存指定任务的栈 |
| `stack_trace_save_regs(regs, store, size, skipnr)` | `stacktrace.c:164` | 从 pt_regs 保存栈 |
| `stack_trace_print(trace, nr, spaces)` | `stacktrace.c:24` | 打印栈回溯 |
| `arch_stack_walk(consume, cookie, task, regs)` | `arch/arm64/.../stacktrace.c:198` | arm64 架构栈遍历 |

### arm64 实现

基于帧指针的展开。`arch_stack_walk()` 初始化 `unwind_state`，循环调用 `unwind_next()`。`unwind_next()` 读取帧记录：FP+0 是前一个 FP，FP+8 是返回地址（PC）。验证链（无循环、栈边界检查）。处理 `CONFIG_FUNCTION_GRAPH_TRACER` 返回地址重写和 `CONFIG_KRETPROBES` trampoline 解析。

支持多种栈类型：task、IRQ、overflow（VMAP_STACK）、SDEI、EFI。

### 使用量

`stack_trace_save` 40 处、`save_stack_trace` 29 处调用点，合计 69 处。

---

## 3. BUG / WARN

**核心文件**：`include/asm-generic/bug.h`（225 行）、`arch/arm64/include/asm/bug.h`（28 行）、`arch/arm64/include/asm/asm-bug.h`（44 行）

### 定位

**功能**：提供内核断言原语——`BUG()` 触发不可恢复的 panic，`WARN()`/`WARN_ON()` 打印警告但继续执行。用于标记"不应发生"的条件，是内核中最基础的运行时自检机制。

**实现方式**：arm64 上 `BUG()` 展开为 `__BUG_FLAGS(0)`，在 `__bug_table` ELF 段放置 `struct bug_entry`（文件、行号、标志），后跟 `brk BUG_BRK_IMM`（BRK immediate `0x800`）。BRK 触发同步异常，陷阱处理程序查找 `__bug_table` 条目获取诊断信息。`__WARN_FLAGS()` 使用相同 BRK 机制但设置 `BUGFLAG_WARNING` 标志，陷阱处理程序调用 `__warn()` 而非 `panic()`。支持 `WARN_ON_ONCE` 等单次触发变体（通过 `BUGFLAG_ONCE` + `BUGFLAG_DONE` 位实现）。

**应用场景**：内核中最 pervasive 的断言机制——`BUG_ON` 1655 处、`WARN_ON`/`WARN_ON_ONCE` 1449 处，覆盖 `mm/`、`net/`、`kernel/`、`lib/`、`drivers/` 等全部子系统。典型消费者：`mm/page_alloc.c`（页面分配器参数校验）、`net/core/dev.c`（网络栈状态断言）、`kernel/locking/lockdep.c`（锁正确性检查）、`kernel/signal.c`（信号处理断言）。

### arm64 实现

`BUG()` 展开为 `__BUG_FLAGS(0)`，发出 `ASM_BUG_FLAGS(0)`——在 `__bug_table` 段放置条目（文件、行、标志），后跟 `brk BUG_BRK_IMM`（BRK immediate `0x800`）。

BRK 触发同步异常；陷阱处理程序查找 `__bug_table` 条目获取文件/行信息。

`__WARN_FLAGS(flags)` 发出 `__BUG_FLAGS(BUGFLAG_WARNING|flags)`——相同 BRK 机制但设置了 WARNING 标志，陷阱处理程序调用 `__warn()` 而非 `panic()`。

### 标志位

| 标志 | 位 | 含义 |
|------|-----|------|
| `BUGFLAG_WARNING` | 0 | WARN 而非 BUG |
| `BUGFLAG_ONCE` | 1 | 只报告一次 |
| `BUGFLAG_DONE` | 2 | 已报告（用于 ONCE） |
| `BUGFLAG_TAINT(t)` | 8+ | 编码 taint 值 |

### struct bug_entry（`include/asm-generic/bug.h:33-48`）

```c
struct bug_entry {
    int bug_addr_disp;    // 相对 Bug 地址
    int file_disp;        // 相对文件名字符串
    unsigned short line;  // 行号
    unsigned short flags; // 标志
};
```

### 使用量

`BUG` 2733 处、`WARN` 2000 处、`WARN_ON` 12038 处、`__WARN_FLAGS` 14 处调用点，合计 16785 处。

---

## 4. kallsyms

**核心文件**：`kernel/kallsyms.c`（1049 行）、`scripts/kallsyms.c`（920 行，构建时工具）

### 定位

**功能**：提供内核符号地址与名称之间的双向查找能力——按名字查地址（`kallsyms_lookup_name`）和按地址查符号名（`kallsyms_lookup`、`sprint_symbol`），同时通过 `/proc/kallsyms` 向用户空间暴露完整符号表。

**实现方式**：构建时 `scripts/kallsyms.c` 收集所有内核符号，按名称排序，使用 256 条目的 token table 压缩符号名（`optimize_token_table()` 迭代替换最常见的 2 字节序列）。运行时数据（`kallsyms_addresses[]`、`kallsyms_names[]`、`kallsyms_token_table[]` 等）存储在 `kernel/kallsyms_internal.h` 声明的数组中。地址查找使用排序后 `kallsyms_addresses[]` 上的二分查找，名称解压通过 `kallsyms_expand_symbol()`（`kernel/kallsyms.c:42`）在 token table 中逐字节查找完成。

**应用场景**：`kallsyms_lookup_name` 在 19 个文件的 41 处被调用，主要消费者包括 `kernel/kprobes.c`（kprobe 符号解析）、`kernel/bpf/syscall.c`（BPF 辅助函数查找）、`kernel/trace/ftrace.c`（ftrace 符号解析）、`kernel/trace/trace_probe.c`（trace event 符号绑定）。`sprint_symbol` 在 5 个文件的 18 处被调用，用于 `kernel/trace/trace_output.c`（追踪输出格式化）和 `drivers/scsi/fnic/fnic_trace.c`（FC 驱动追踪）。`/proc/kallsyms` 供 `perf` 等用户空间工具使用。

### 机制

**构建时**（`scripts/kallsyms.c`）：收集所有内核符号，按名称排序。名称使用表查找方案压缩：`optimize_token_table()` 迭代查找最常见的 2 字节序列并替换为单个标记，构建 `kallsyms_token_table[256]` 和 `kallsyms_token_index[256]`。

**运行时数据**（`kernel/kallsyms_internal.h`）：

| 数组 | 说明 |
|------|------|
| `kallsyms_addresses[]` | 绝对符号地址 |
| `kallsyms_offsets[]` | 相对偏移（`CONFIG_KALLSYMS_BASE_RELATIVE`） |
| `kallsyms_names[]` | 压缩的符号名（字节流） |
| `kallsyms_num_syms` | 总符号数 |
| `kallsyms_token_table[]` | 压缩查找表（256 条目） |
| `kallsyms_token_index[]` | token_table 索引 |
| `kallsyms_markers[]` | 每 256 个符号的位置标记 |

**运行时解压**：`kallsyms_expand_symbol()`（`kallsyms.c:42`）通过在 token table 中查找每个字节来解压缩。地址查找使用排序后的 `kallsyms_addresses[]` 上的二分查找。

### 核心 API

| API | 位置 | 说明 |
|-----|------|------|
| `kallsyms_lookup_name(name)` | `kallsyms.c:267` | 按名称查找地址 |
| `kallsyms_lookup(addr, ...)` | `kallsyms.c:449` | 按地址查找符号名 |
| `sprint_symbol(buf, addr)` | `kallsyms.c:564` | 格式化符号为字符串 |
| `/proc/kallsyms` | `kallsyms.c:1046` | 用户空间符号表 |

### 使用量

`kallsyms_lookup` 20 处、`sprint_symbol` 16 处调用点，合计 36 处。
