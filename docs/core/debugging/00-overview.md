---
title: 总览
---

# 调试与跟踪 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **ftrace** | 函数级动态跟踪框架 | 编译器插桩实现零侵入全函数跟踪，支持 function / function_graph / direct call 多种模式 | [ftrace.md](ftrace.md) |
| **tracepoint** | 内核静态跟踪点 | 源码级预声明的零开销探针，未启用时通过 jump label 补丁为 NOP | [tracepoint.md](tracepoint.md) |
| **kprobes** | 动态探针框架 | 在任意内核地址插入探针，无需重编译、无需源码预埋点 | [kprobes.md](kprobes.md) |
| **printk** | 内核日志基础设施 | 全内核通用的消息输出通道，支持 console / kmsg / devkmsg 多路输出 | [printk.md](printk.md) |
| **dynamic_debug** | 运行时可控调试日志 | 按需启用/禁用 `pr_debug()`/`dev_dbg()` 输出，无需重编译 | [misc-debug.md](misc-debug.md) §1 |
| **stacktrace** | 栈回溯基础设施 | 统一的 `arch_stack_walk()` 接口，供 KASAN / lockdep / page_owner 等共享 | [misc-debug.md](misc-debug.md) §2 |
| **BUG/WARN** | 内核断言机制 | `BUG()` 触发 panic，`WARN()` 仅报告——arm64 用 `BRK #0x800` 实现 | [misc-debug.md](misc-debug.md) §3 |
| **kallsyms** | 内核符号表 | 地址↔符号名双向映射，`/proc/kallsyms` 和 `sprint_symbol()` 的基础 | [misc-debug.md](misc-debug.md) §4 |
| **KASAN** | 内存错误检测 | 越界访问 / use-after-free 检测，Generic 和 HW_TAGS（MTE）两种模式 | [kasan.md](kasan.md) |
| **KMSAN** | 未初始化内存检测 | shadow + origin 双层元数据追踪未初始化值来源，仅 Clang | [misc-san.md](misc-san.md) §1 |
| **KCSAN** | 数据竞争检测 | 概率采样检测并发访问竞争，`-fsanitize=thread` 插桩 | [misc-san.md](misc-san.md) §2 |
| **UBSAN** | 未定义行为检测 | 整数溢出 / 移位越界 / 对齐错误等，`-fsanitize=undefined` 插桩 | [misc-san.md](misc-san.md) §3 |
| **fault-injection** | 故障注入框架 | 模拟资源不足（内存分配失败、I/O 错误等），测试内核健壮性 | [misc-fault.md](misc-fault.md) |
| **livepatch** | 内核热补丁框架 | 不重启替换函数实现，基于 ftrace `FTRACE_OPS_FL_IPMODIFY`，一致性栈检查过渡 | [misc-patch.md](misc-patch.md) |
| **ring_buffer** | 无锁 per-CPU 环形缓冲区 | ftrace / tracepoint 的底层数据通道，5 级上下文嵌套写入 | [misc-trace.md](misc-trace.md) |

---

## §2 选型决策

```
需要调试/跟踪什么？
│
├─ 运行时日志输出
│   ├─ 需要运行时开关控制？ ──→ dynamic_debug（pr_debug/dev_dbg）
│   └─ 通用日志 ──→ printk（pr_err/dev_err 等）
│
├─ 函数调用行为
│   ├─ 需要跟踪所有函数调用 ──→ ftrace（function tracer）
│   ├─ 需要函数调用图（含返回值）──→ ftrace（function_graph tracer）
│   ├─ 需要在特定代码位置插桩（已有 TRACE_EVENT）──→ tracepoint
│   └─ 需要在任意地址动态插桩 ──→ kprobes
│
├─ 内存/并发/语义错误
│   ├─ 越界访问 / use-after-free ──→ KASAN
│   ├─ 使用未初始化内存 ──→ KMSAN（需 Clang）
│   ├─ 数据竞争 ──→ KCSAN
│   └─ 整数溢出/移位越界/对齐错误 ──→ UBSAN
│
├─ 系统健壮性测试
│   └─ 模拟资源不足 ──→ fault-inject
│
├─ 不停机修复
│   └─ 热补丁 ──→ livepatch
│
└─ 辅助工具
    ├─ 获取调用栈 ──→ stacktrace
    ├─ 不可恢复错误断言 ──→ BUG()/WARN()
    └─ 地址 ↔ 符号名转换 ──→ kallsyms
```

**关键区分**：

| 问题 | 选择 |
|------|------|
| 需要全函数跟踪 vs 特定点跟踪？ | ftrace（全函数）vs tracepoint（预声明点）vs kprobes（任意地址） |
| 需要稳定探针 vs 临时调试？ | tracepoint（源码预埋，ABI 稳定）vs kprobes（动态，无 ABI 保证） |
| 需要内存安全 vs 并发安全？ | KASAN（内存越界/UAF）vs KCSAN（数据竞争） |
| 需要日志输出 vs 数据采集？ | printk（文本日志）vs ring_buffer（结构化二进制数据） |
| 需要热补丁 vs 重编译部署？ | livepatch（不停机）vs 重编译（更安全） |

---

## §3 核心对比表

### 3.1 跟踪与探针

| 维度 | ftrace | tracepoint | kprobes |
|------|--------|-----------|---------|
| 插桩方式 | 编译器 `-fpatchable-function-entry` | 源码 `TRACE_EVENT()` 宏 | 运行时断点指令 |
| 粒度 | 函数入口 | 代码中预声明的点 | 任意指令地址 |
| 开销（未启用） | NOP（jump label） | NOP（jump label） | 无（未注册时不存在） |
| arm64 约束 | 支持 | 支持 | 不支持 jump 优化和 kprobe-on-ftrace |
| 返回值获取 | function_graph 支持 | 需额外 kretprobe | kretprobe 支持 |
| 代码量 | `kernel/trace/ftrace.c` 8488 行 | `kernel/tracepoint.c` + 事件定义 | `kernel/kprobes.c` 3048 行 |

### 3.2 运行时诊断器

| 维度 | KASAN | KMSAN | KCSAN | UBSAN |
|------|-------|-------|-------|-------|
| 检测目标 | 越界 / UAF | 未初始化内存 | 数据竞争 | 未定义行为 |
| 插桩方式 | `-fsanitize=kernel-address` | `-fsanitize=memory`（Clang） | `-fsanitize=thread` | `-fsanitize=undefined` |
| 内存开销 | Generic: ~1/8；HW_TAGS: ~1/16 | shadow + origin: ~2x | 低（概率采样） | 低 |
| 性能开销 | Generic: ~x3；HW_TAGS: ~x1.3 | ~x2-3 | ~x2-5（取决于采样率） | ~x2+ |
| 编译器要求 | GCC / Clang | 仅 Clang | GCC / Clang | GCC / Clang |
| 代码量 | `mm/kasan/` 5915 行 | `mm/kmsan/` 2554 行 | `kernel/kcsan/` 4249 行 | `lib/ubsan.c` 386 行 |
| 误报率 | 低 | 低 | 中（概率采样） | 中（内核依赖"未定义"行为） |

### 3.3 日志与输出

| 维度 | printk | dynamic_debug | stacktrace | BUG/WARN | kallsyms |
|------|--------|--------------|-----------|---------|---------|
| 核心用途 | 通用日志输出 | 按需调试日志 | 栈回溯 | 断言 | 符号解析 |
| 核心 API | `printk()` / `pr_*()` / `dev_*()` | `pr_debug()` / `dev_dbg()` | `stack_trace_save()` | `BUG()` / `WARN()` | `kallsyms_lookup()` |
| 控制方式 | `console_loglevel`、`dmesg` | debugfs `control` 文件 | 无（被动调用） | 无（触发即执行） | 构建时压缩 |
| 代码量 | `kernel/printk/printk.c` 4755 行 | `lib/dynamic_debug.c` 1460 行 | `kernel/stacktrace.c` 402 行 | `arch/arm64/kernel/traps.c` | `kernel/kallsyms.c` 1049 行 |

---

## §4 使用规模

以下数字由 `grep` 在 6.1.y 全树上统计（`--include='*.c'`）：

### 4.1 日志输出

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `printk()` | 21281 | `grep -r '\bprintk(' --include='*.c' \| wc -l` |
| `pr_err()` | 19960 | `grep -r '\bpr_err(' --include='*.c' \| wc -l` |
| `dev_err()` | 57882 | `grep -r '\bdev_err(' --include='*.c' \| wc -l` |
| `pr_debug()` | 12828 | `grep -r '\bpr_debug(' --include='*.c' \| wc -l` |
| `dev_dbg()` | 24479 | `grep -r '\bdev_dbg(' --include='*.c' \| wc -l` |
| `dynamic_debug` 总调用点 | 37307 | pr_debug + dev_dbg |

### 4.2 跟踪与探针

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `TRACE_EVENT()` 定义 | 2048 | `grep -r '^TRACE_EVENT(' --include='*.c' --include='*.h' \| wc -l` |
| `DECLARE_EVENT_CLASS` | 747 | `grep -r '^DECLARE_EVENT_CLASS(' --include='*.c' --include='*.h' \| wc -l` |
| `DEFINE_EVENT` | 1549 | `grep -r '^DEFINE_EVENT(' --include='*.c' --include='*.h' \| wc -l` |
| `register_ftrace_function()` | 65 | `grep -r 'register_ftrace_function\b' --include='*.c' \| wc -l` |
| `register_kprobe()` | 47 | `grep -r 'register_kprobe\b' --include='*.c' \| wc -l` |
| `register_kretprobe()` | 24 | `grep -r 'register_kretprobe\b' --include='*.c' \| wc -l` |

### 4.3 断言与栈回溯

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `BUG()` | 1770 | `grep -r '\bBUG()' --include='*.c' \| wc -l` |
| `WARN_ON()` | 11544 | `grep -r '\bWARN_ON(' --include='*.c' \| wc -l` |
| `WARN_ON_ONCE()` | 4535 | `grep -r '\bWARN_ON_ONCE(' --include='*.c' \| wc -l` |
| `stack_trace_save()` | 39 | `grep -r 'stack_trace_save\b' --include='*.c' \| wc -l` |

### 4.4 诊断与补丁

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `should_fail()` | 41 | `grep -r 'should_fail\b' --include='*.c' \| wc -l` |
| `klp_enable_patch()` | 16 | `grep -r 'klp_enable_patch\b' --include='*.c' \| wc -l` |
| `FTRACE_OPS_FL_IPMODIFY` | 8 | `grep -r 'FTRACE_OPS_FL_IPMODIFY' --include='*.c' --include='*.h' \| wc -l` |
| `ring_buffer_lock_reserve()` | 11 | `grep -r 'ring_buffer_lock_reserve\b' --include='*.c' \| wc -l` |

---

## §5 设计模式速览

### 5.1 编译器插桩家族

多个模块依赖编译器插桩，但插桩方式和目的不同：

| 模块 | 插桩方式 | 插桩点 | 目的 |
|------|----------|--------|------|
| ftrace | `-fpatchable-function-entry` | 每个函数入口 | 函数调用跟踪 |
| KASAN | `-fsanitize=kernel-address` | 每次内存访问 | 越界/UAF 检测 |
| KMSAN | `-fsanitize=memory` | 每次内存读取前 | 未初始化检测 |
| KCSAN | `-fsanitize=thread` | 每次内存访问 | 数据竞争检测 |
| UBSAN | `-fsanitize=undefined` | 每个 UB 运算 | 语义错误检测 |

### 5.2 Static Key / Jump Label 零开销控制

ftrace、tracepoint、dynamic_debug 都使用 jump label 机制实现零开销禁用：
- ftrace：函数入口 NOP ↔ BL 指令切换
- tracepoint：`static_key_false()` 控制 probe 调用路径（`kernel/tracepoint.c` 中 5 处 `static_key` 引用）
- dynamic_debug：`_DPRINTK_FLAGS_PRINT` 标志控制 `pr_debug` 输出

arm64 实现在 `arch/arm64/kernel/jump_label.c`，使用 `aarch64_insn_gen_branch_imm()` 和 `aarch64_insn_gen_nop()` 进行指令补丁。

### 5.3 Ring Buffer 共享

ftrace 和 tracepoint 共享 `ring_buffer` 基础设施（`kernel/trace/ring_buffer.c`，6234 行）：
- tracepoint 触发时调用 `trace_event_buffer_lock_reserve()` → `ring_buffer_lock_reserve()`（全树 4 处桥接调用）
- ftrace 提交时调用 `ring_buffer_unlock_commit()`（12 处调用）
- 两者通过 `trace_event_reg()` 桥接（5 处调用）

### 5.4 Ftrace Ops 共享

ftrace、livepatch、kprobes（部分架构）共享 `struct ftrace_ops` 机制：
- livepatch 使用 `FTRACE_OPS_FL_IPMODIFY` 修改函数入口 IP（全树 8 处引用）
- ftrace 的 function tracer 注册普通 ops（`register_ftrace_function()` 65 处调用）
- fprobe（BPF 使用）也基于 ftrace 实现（`kernel/trace/fprobe.c` 中 30 处 ftrace 引用）

### 5.5 报告与栈回溯共享

所有诊断模块（KASAN、KMSAN、KCSAN、UBSAN）和断言机制（BUG/WARN）共享：
- `stack_trace_save()` / `arch_stack_walk()` 获取调用栈
- `kallsyms_lookup()` / `sprint_symbol()` 格式化符号
- `printk()` 输出报告
- KASAN 报告代码中 23 处引用栈回溯相关函数（`mm/kasan/report.c`）

---

## §6 锁模型对比

| 模块 | 并发安全级别 | 锁机制 | 关键约束 |
|------|------------|--------|---------|
| **ftrace** | 全局 + per-function | `ftrace_lock`（mutex）保护注册表；`arch_spinlock_t` 保护代码补丁 | 补丁期间停止所有被修改函数的执行 |
| **tracepoint** | 无锁读路径 | `static_key` jump label + RCU 保护 probe 数组 | 读侧零锁开销；probe 注册用 SRCU 保护 |
| **kprobes** | per-probe | `kprobe_mutex` 保护全局列表；`arch_spinlock_t` 保护断点指令修改 | arm64 不支持 jump 优化（无多探针共享 slot） |
| **printk** | per-console | `console_lock`（mutex/semaphore）；per-console kthread；NMI 上下文用延迟输出 | NMI 中不能直接打印，进入 NMI 缓冲区，退出时 flush |
| **ring_buffer** | per-CPU 无锁 | 写者用 `arch_spinlock_t`（同 CPU 内序列化）；读者用 `raw_spinlock_t`；不同 CPU 间无竞争 | 5 级上下文嵌套（NMI→IRQ→SOFTIRQ→NORMAL），`current_context` 跟踪 |
| **KASAN** | 无锁 | 编译器插桩在每次内存访问前/后自动检查，无全局锁 | Generic 模式 shadow 内存全局共享但无锁（只读查询） |
| **KCSAN** | 无锁采样 | 概率采样，每个被采样的访问用原子操作检查 shadow 内存 | 不保证检测所有竞争（概率性的） |
| **livepatch** | 全局 + per-task | `klp_mutex` 保护补丁注册；栈检查过渡期间 per-task 检查 | 一致性模型：所有任务必须离开旧函数后才完成过渡（默认 15 秒超时） |
| **fault-injection** | per-instance | `fail_*` 结构体内部 spinlock 保护概率状态 | 注入决策本身无锁（per-CPU 随机数） |
| **dynamic_debug** | 无锁读 | jump label 控制；写入 `control` 文件时 `ddebug_mutex` 保护 | 读侧零开销（jump label NOP） |
| **stacktrace** | 无锁 | 纯读取当前栈帧，不涉及共享状态 | NMI 上下文安全 |
| **BUG/WARN** | 无锁 | `BRK #0x800` 触发同步异常，异常处理中打印 | `BUG()` 导致 panic（不可恢复）；`WARN()` 仅报告 |
| **kallsyms** | 无锁读 | 构建时压缩的 token table，运行时只读二分查找 | 完全只读，无并发问题 |

---

## §7 组合使用模式

### 7.1 性能分析组合

**ftrace + tracepoint + ring_buffer**

这是内核性能分析的核心三角：
1. tracepoint 在源码中预声明事件点（全树 2048 个 `TRACE_EVENT` 定义）
2. 事件触发时通过 `trace_event_buffer_lock_reserve()` 桥接到 `ring_buffer`
3. ftrace 的 function tracer 使用 `register_ftrace_function()` 注册回调（65 处使用），同样写入 ring_buffer
4. 用户空间通过 tracefs 读取 ring_buffer 数据

### 7.2 内存 bug 排查组合

**KASAN + stacktrace + printk**

1. KASAN 检测到越界/UAF 时调用 `stack_trace_save()` 获取调用栈（`mm/kasan/report.c` 中 23 处栈回溯引用）
2. 通过 `kallsyms_lookup()` / `sprint_symbol()` 将地址转为可读符号
3. 通过 `printk()` 输出完整诊断报告

### 7.3 并发 bug 排查组合

**KCSAN + KASAN + printk**

1. KCSAN 检测到数据竞争时记录访问信息
2. KASAN 可同时检测竞争导致的内存破坏
3. 两者共享 `printk()` 输出通道和 `stack_trace_save()` 调用栈

### 7.4 动态调试组合

**kprobes + ftrace + dynamic_debug**

1. kprobes 在任意地址插入探针（47 处 `register_kprobe` 使用）
2. ftrace 跟踪函数调用链
3. dynamic_debug 按需启用特定 `pr_debug()` 调用点（37307 个调用点可选）
4. 三者结合实现"不重编译、不重启"的动态调试

### 7.5 生产环境热修复组合

**livepatch + kallsyms + ftrace**

1. livepatch 通过 `FTRACE_OPS_FL_IPMODIFY`（8 处引用）劫持 ftrace 回调
2. kallsyms 解析目标函数地址（`klp_enable_patch()` 16 处调用依赖符号解析）
3. 一致性栈检查保证所有 CPU 安全过渡

### 7.6 压力测试组合

**fault-injection + printk + BUG/WARN**

1. fault-injection 通过 `should_fail()`（41 处调用，其中 drivers/ 20 处、block/ 2 处）模拟资源不足
2. 失败路径通过 `printk()` 记录日志
3. 不可恢复的故障通过 `BUG()`（1770 处）触发 panic

---

## §8 阅读顺序建议

### 路径 A：从零开始理解调试子系统

1. **printk** → 理解内核日志输出的基础
2. **BUG/WARN** → 理解内核断言和错误报告
3. **stacktrace** → 理解调用栈获取机制
4. **kallsyms** → 理解符号解析
5. **ftrace** → 理解函数跟踪框架
6. **tracepoint** → 理解静态跟踪点
7. **ring_buffer** → 理解数据传输通道
8. **kprobes** → 理解动态探针

### 路径 B：排查内存/并发 bug

1. **KASAN** → 内存错误检测
2. **KMSAN** → 未初始化内存检测
3. **KCSAN** → 数据竞争检测
4. **UBSAN** → 未定义行为检测
5. **stacktrace** → 调用栈获取（诊断器共享）

### 路径 C：生产环境运维

1. **printk** → 日志基础
2. **dynamic_debug** → 按需调试日志
3. **ftrace** → 性能分析
4. **livepatch** → 热补丁修复
5. **kallsyms** → 符号解析

### 路径 D：内核开发新探针

1. **tracepoint** → 理解 `TRACE_EVENT()` 宏
2. **ftrace** → 理解函数跟踪
3. **kprobes** → 理解动态探针
4. **ring_buffer** → 理解数据通道

---

## §9 文档索引

| 文档 | 覆盖模块 | 路径 |
|------|----------|------|
| ftrace 函数跟踪框架 | ftrace、fprobe、函数图跟踪 | `output/debugging/ftrace.md` |
| tracepoint 静态跟踪点 | tracepoint、TRACE_EVENT、jump label | `output/debugging/tracepoint.md` |
| printk 日志子系统 | printk、pr_*、dev_*、console、kmsg | `output/debugging/printk.md` |
| kprobes 动态探针 | kprobe、kretprobe、指令模拟 | `output/debugging/kprobes.md` |
| KASAN 地址消毒器 | KASAN Generic/SW_TAGS/HW_TAGS | `output/debugging/kasan.md` |
| 调试杂项 | dynamic_debug、stacktrace、BUG/WARN、kallsyms | `output/debugging/misc-debug.md` |
| 跟踪杂项 | ring_buffer | `output/debugging/misc-trace.md` |
| 诊断器杂项 | KMSAN、KCSAN、UBSAN | `output/debugging/misc-san.md` |
| 故障注入 | fault-inject | `output/debugging/misc-fault.md` |
| 热补丁 | livepatch | `output/debugging/misc-patch.md` |
