# kprobes — 内核动态探针框架

> **定位**：kprobes 允许在内核任意地址动态插入探针，无需重新编译。支持三种探针类型：kprobe（函数内任意位置）、kretprobe（函数返回时）、以及通过 fprobe 的函数入口/出口探针。本文覆盖 kprobes 核心机制及 arm64 实现。

---

## 1. 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/kprobes.c` | 3048 | 核心引擎：注册、断点管理、单步执行、黑名单 |
| `include/linux/kprobes.h` | 597 | 公开 API 和数据结构 |
| `arch/arm64/kernel/probes/kprobes.c` | 443 | arm64 断点插入、单步、XOL |
| `arch/arm64/kernel/probes/kprobes_trampoline.S` | 86 | kretprobe trampoline 汇编 |
| `arch/arm64/kernel/probes/decode-insn.c` | 174 | arm64 指令解码 |
| `arch/arm64/kernel/probes/simulate-insn.c` | 198 | 分支/PC 相对指令模拟 |
| `arch/arm64/include/asm/kprobes.h` | 47 | arm64 kprobes 定义 |

---

## 2. 核心数据结构

### 2.1 struct kprobe（`include/linux/kprobes.h:60-95`）

```c
struct kprobe {
    struct hlist_node hlist;          // 哈希表链接
    struct list_head list;            // 多处理器聚合链表
    unsigned long nmissed;            // 临时解除次数
    kprobe_opcode_t *addr;            // 探针点地址
    const char *symbol_name;          // 符号名（可选）
    unsigned int offset;              // 符号内偏移
    kprobe_pre_handler_t pre_handler; // 指令执行前回调
    kprobe_post_handler_t post_handler; // 指令执行后回调
    kprobe_opcode_t opcode;           // 保存的原始指令
    struct arch_specific_insn ainsn;  // 架构相关指令副本/槽位
    u32 flags;                        // 状态标志
};
```

**回调签名**：
```c
typedef int (*kprobe_pre_handler_t)(struct kprobe *, struct pt_regs *);
typedef void (*kprobe_post_handler_t)(struct kprobe *, struct pt_regs *, unsigned long flags);
```

### 2.2 struct kretprobe（`include/linux/kprobes.h:147-160`）

```c
struct kretprobe {
    struct kprobe kp;                  // 内嵌 kprobe（用于入口探测）
    kretprobe_handler_t handler;       // 返回处理函数
    kretprobe_handler_t entry_handler; // 可选入口处理
    int maxactive;                     // 最大并发实例数
    int nmissed;                       // 被忽略的返回次数
    size_t data_size;                  // 每实例数据大小
    struct rethook *rh;               // rethook（CONFIG_KRETPROBE_ON_RETHOOK）
};
```

### 2.3 struct kretprobe_instance（`include/linux/kprobes.h:164-178`）

每次被探测函数调用时分配一个实例，保存返回地址和用户数据。

### 2.4 状态标志

| 标志 | 值 | 含义 |
|------|-----|------|
| `KPROBE_FLAG_GONE` | 1 | 探针点已消失 |
| `KPROBE_FLAG_DISABLED` | 2 | 临时禁用 |
| `KPROBE_FLAG_OPTIMIZED` | 4 | 已优化（arm64 不支持） |
| `KPROBE_FLAG_FTRACE` | 8 | 使用 ftrace（arm64 不支持） |

---

## 3. 核心 API

头文件：`include/linux/kprobes.h`；实现：`kernel/kprobes.c`

### 3.1 Kprobe 注册

| API | 说明 | 备注 |
|-----|------|------|
| `register_kprobe(p)` | 注册 kprobe | `EXPORT_SYMBOL_GPL`；解析地址、检查黑名单、安装断点 |
| `unregister_kprobe(p)` | 注销 kprobe | `EXPORT_SYMBOL_GPL` |
| `disable_kprobe(p)` | 临时禁用 | 断点被移除但 kprobe 结构保留 |
| `enable_kprobe(p)` | 重新启用 | |

### 3.2 Kretprobe 注册

| API | 说明 | 备注 |
|-----|------|------|
| `register_kretprobe(rp)` | 注册返回探针 | `EXPORT_SYMBOL_GPL`；预分配 `maxactive` 个实例 |
| `unregister_kretprobe(rp)` | 注销返回探针 | `EXPORT_SYMBOL_GPL` |

### 3.3 注意事项

- **jprobes**：在 6.1 中已不存在（~v4.14 移除）
- **kprobe-on-ftrace**：arm64 不支持（未选择 `HAVE_KPROBES_ON_FTRACE`）
- **jump 优化**：arm64 不支持（未选择 `HAVE_OPTPROBES`）

---

## 4. 机制剖析

### 4.1 断点插入（arm64）

**arm64 实现**（`arch/arm64/kernel/probes/kprobes.c:140-146`）：
```c
void arch_arm_kprobe(struct kprobe *p)
{
    void *addr = p->addr;
    u32 insn = BRK64_OPCODE_KPROBES;  // BRK #0x004
    aarch64_insn_patch_text(&addr, &insn, 1);
}
```

原始指令被替换为 `BRK #0x004` 指令。`aarch64_insn_patch_text` 执行原子文本补丁，包含 I-cache 维护和上下文同步。

### 4.2 断点命中处理流程

1. CPU 执行到 `BRK #0x004`，触发同步异常
2. 异常处理匹配 BRK immediate，调用 `kprobe_breakpoint_handler`
3. `kprobe_handler` 执行 `p->pre_handler(p, regs)`
4. 进入单步执行阶段

### 4.3 单步执行（arm64：XOL 或模拟）

arm64 使用两种路径：

**路径 A — XOL（Execute-Out-of-Line）**（用于 `INSN_GOOD` 指令）：
- 原始指令复制到 per-CPU 可执行槽位（`p->ainsn.api.insn`，通过 `__vmalloc_node_range` 分配 `PAGE_KERNEL_ROX`）
- `arch_prepare_ss_slot` 在槽位写入：原始指令 + `BRK #0x006`（单步陷阱）
- PC 重定向到 XOL 槽位
- DAIF 保存并屏蔽所有中断
- CPU 从槽位执行原始指令，然后命中 SS BRK
- `kprobe_breakpoint_ss_handler` 捕获 SS BRK，恢复 IRQ 标志，调用 `post_handler`

**路径 B — 模拟**（用于 `INSN_GOOD_NO_SLOT` 指令）：
- 不能安全 XOL 的指令（分支、PC 相对加载等）在软件中模拟
- 模拟的指令类型：`B.cond`、`CBZ/CBNZ`、`TBZ/TBNZ`、`ADR/ADRP`、`B/BL`、`BR/BLR/RET`、`LDR literal`

### 4.4 Kretprobe 返回探针机制

1. `register_kretprobe` 在函数入口安装 kprobe，`pre_handler = pre_handler_kretprobe`
2. 函数入口时，`pre_handler_kretprobe` 获取 `kretprobe_instance`，调用 `arch_prepare_kretprobe`：
   ```c
   ri->ret_addr = (kprobe_opcode_t *)regs->regs[30];  // 保存原始 LR
   regs->regs[30] = (long)&__kretprobe_trampoline;     // 劫持 LR
   ```
3. 函数返回时跳转到 `__kretprobe_trampoline`（`kprobes_trampoline.S:64-86`）：
   - 保存所有基寄存器
   - 调用 `trampoline_probe_handler` → `kretprobe_trampoline_handler`
   - 遍历 per-task 实例链表，调用每个 `rp->handler`
   - 恢复真实返回地址到 LR
   - 恢复所有寄存器，`ret` 到真实调用者

### 4.5 黑名单

**通用黑名单**（`kernel/kprobes.c:1373-1412`）：
- `__kprobes` 标记的函数
- `__start_kprobe_blacklist`..`__stop_kprobe_blacklist` 链接器段
- 动态 `kprobe_blacklist` 链表

**arm64 特有黑名单**（`arch/arm64/kernel/probes/kprobes.c:392-415`）：
- 异常入口代码（`__entry_text_start..__entry_text_end`）
- IRQ 入口（`__irqentry_text_start..__irqentry_text_end`）
- 恒等映射代码（`__idmap_text_start..__idmap_text_end`）
- Hypervisor 代码（`__hyp_text_start..__hyp_text_end`）

通过 debugfs `kprobes/blacklist` 暴露给用户空间。

### 4.6 指令解码限制

`arm_kprobe_decode_insn`（`decode-insn.c:132-173`）：

**不可探测的指令**：
- 分支指令（无模拟路径时）
- MSR/MRS（除 DAIF 读取）
- 异常生成（SVC/HVC/SMC）
- ERET
- 独占加载/存储（ldxr/stxr）——会破坏原子性
- 原子序列内（`MAX_ATOMIC_CONTEXT_SIZE = 32` 条指令窗口）

---

## 5. arm64 特有特性与限制

| 特性 | 状态 |
|------|------|
| 断点指令 | `BRK #0x004`（探针）、`BRK #0x006`（单步） |
| XOL 槽位 | per-CPU `vmalloc` + `PAGE_KERNEL_ROX` |
| 指令模拟 | 分支、PC 相对加载等 |
| kprobe-on-ftrace | 不支持 |
| jump 优化 | 不支持 |
| jprobes | 已移除 |
| AArch32 探测 | 不支持 |
| DAIF 屏蔽 | XOL 期间屏蔽所有调试异常 |
| `kretprobe_blacklist_size` | 0（仅使用动态黑名单） |

---

## 6. 使用统计

| 模式 | 数量 |
|------|------|
| `register_kprobe` 调用者 | 51 |
| `register_kretprobe` 调用者 | 28 |
| `kprobe` 总引用 | 1774 |

主要消费者：`kernel/trace/`（tracing）、BPF、架构代码。

---

## 7. 与其他子系统的关系

- **ftrace**：arm64 不支持 kprobe-on-ftrace。在支持的架构上，kprobe 可使用 ftrace 的 mcount 调用点补丁代替断点。
- **uprobes**：共享指令解码基础设施（`arm_probe_decode_insn`）。uprobes 使用硬件单步（`user_enable_single_step`）而非第二个 BRK。
- **perf**：通过 PMU 类型注册 kprobe/uprobe。`perf_kprobe_event_init` 创建本地跟踪 kprobe。
- **BPF**：`BPF_PROG_TYPE_KPROBE` 程序可附加到 kprobe/uprobe 事件。`bpf_kprobe_multi_link_attach` 支持通过 fprobe 多探针附加。
- **trace events**：`kernel/trace/trace_kprobe.c` 实现 `kprobe_events` 动态跟踪接口。

---

## §4 常见陷阱

### 陷阱 1：在抢占禁用区域注册 kprobe

**错误写法**：
```c
void my_func(void)
{
    preempt_disable();
    /* 在抢占禁用区域注册 kprobe */
    register_kprobe(&my_kp);  /* 可能死锁或触发 WARN */
    preempt_enable();
}
```

**正确写法**：
```c
void my_func(void)
{
    /* 在可抢占上下文中注册 kprobe */
    register_kprobe(&my_kp);
}
```

**原因**：`register_kprobe()` 内部调用 `check_kprobe_address_safe()`（`kernel/kprobes.c:1568`），该函数使用 `preempt_disable()` 来保护模块引用计数查询。如果在已禁用抢占的上下文中调用，`preempt_disable()` 会嵌套，而 `__module_text_address()` 等函数可能尝试获取可能休眠的锁。此外，`register_kprobe()` 获取 `kprobe_mutex`（行 1646），在原子上下文中获取 mutex 会触发 `might_sleep()` 警告。

**源码依据**：`kernel/kprobes.c:1568` — `check_kprobe_address_safe()` 中的 `preempt_disable()`；`kernel/kprobes.c:1646` — `mutex_lock(&kprobe_mutex)`。

---

### 陷阱 2：kretprobe 的 maxactive 设置过小导致返回事件丢失

**错误写法**：
```c
static struct kretprobe my_rp = {
    .kp = { .symbol_name = "highly_recursive_func" },
    .handler = my_handler,
    .maxactive = 1,  /* 错误！递归函数只分配 1 个实例 */
};
register_kretprobe(&my_rp);
/* 每次递归调用都会导致 nmissed++ */
```

**正确写法**：
```c
static struct kretprobe my_rp = {
    .kp = { .symbol_name = "highly_recursive_func" },
    .handler = my_handler,
    .maxactive = 0,  /* 0 = 自动选择 max(10, 2*NR_CPUS) */
};
register_kretprobe(&my_rp);
/* 或者对于已知高并发的函数，手动设置足够大的值 */
```

**原因**：`register_kretprobe()` 在行 2231-2232 处理 `maxactive <= 0` 的情况：自动设置为 `max(10, 2*num_possible_cpus())`。但对于递归函数或高并发函数，这个默认值可能不够。当所有预分配的 `kretprobe_instance` 都被占用时，`pre_handler_kretprobe()` 在行 2099-2102 中 `freelist_try_get()` 返回 NULL，`rp->nmissed++` 递增，该次调用的返回探针被静默跳过。

**源码依据**：`kernel/kprobes.c:2231-2232` — `maxactive` 默认值计算；`kernel/kprobes.c:2099-2102` — `pre_handler_kretprobe()` 中实例耗尽时 `nmissed++`。

---

### 陷阱 3：kprobe 的 pre_handler 中修改 pt_regs 导致不可预期行为

**错误写法**：
```c
static int my_pre_handler(struct kprobe *p, struct pt_regs *regs)
{
    /* 错误：在 pre_handler 中修改寄存器 */
    regs->pc += 4;  /* 跳过下一条指令 */
    regs->regs[0] = 0;  /* 修改返回值 */
    return 0;
}
```

**正确写法**：
```c
static int my_pre_handler(struct kprobe *p, struct pt_regs *regs)
{
    /* pre_handler 只用于观察，不应修改寄存器 */
    pr_info("Hit probe at %pS, LR=%lx\n", p->addr, regs->regs[30]);
    return 0;
}

/* 如果需要修改 IP，使用 ftrace_ops 的 IPMODIFY 功能 */
/* 如果需要修改返回值，使用 kretprobe 的 entry_handler */
```

**原因**：kprobe 的 `pre_handler` 在原始指令执行前被调用（`kernel/kprobes.c` 的 `kprobe_handler` 路径）。但 arm64 的 XOL（Execute-Out-of-Line）机制会将原始指令复制到槽位中执行，修改 `regs->pc` 不会影响 XOL 槽位的执行——原始指令仍会被执行。修改通用寄存器虽然可能影响后续代码，但这种做法绕过了 kprobe 的设计意图，可能导致栈不一致或后续探针行为异常。

**源码依据**：`arch/arm64/kernel/probes/kprobes.c:140-146` — `arch_arm_kprobe()` 使用 BRK 指令；`kernel/kprobes.c:1274-1291` — `init_aggr_kprobe()` 设置 `aggr_pre_handler`。

---

### 陷阱 4：在同一地址重复注册 kprobe 导致 -EINVAL

**错误写法**：
```c
static struct kprobe kp1 = { .symbol_name = "do_sys_open" };
static struct kprobe kp2 = { .symbol_name = "do_sys_open" };

register_kprobe(&kp1);
register_kprobe(&kp2);  /* 返回 -EINVAL，触发 WARN */
```

**正确写法**：
```c
static struct kprobe kp1 = { .symbol_name = "do_sys_open" };
static struct kprobe kp2 = {
    .symbol_name = "do_sys_open",
    /* 使用不同的 handler 或作为复合探针的一部分 */
};

register_kprobe(&kp1);
/* 第二个探针在同一地址会创建 aggr_kprobe，但 warn_kprobe_rereg() 会警告 */
/* 正确做法：使用一个 kprobe 带多个 handler，或使用 trace events */
```

**原因**：`register_kprobe()` 在行 1633 调用 `warn_kprobe_rereg()`，该函数检查是否已有探针在同一地址（`__get_valid_kprobe()`）。如果找到，返回 `-EINVAL` 并触发 `WARN_ON_ONCE`。虽然 kprobes 支持通过 `aggr_kprobe` 在同一地址聚合多个 handler，但 `warn_kprobe_rereg()` 的设计是防止意外的重复注册。对于需要多个 handler 的场景，应使用 trace events 的 `kprobe_events` 接口。

**源码依据**：`kernel/kprobes.c:1522-1532` — `warn_kprobe_rereg()` 实现；`kernel/kprobes.c:1633-1634` — `register_kprobe()` 中的检查。

---

### 陷阱 5：kretprobe 的 data_size 超过 KRETPROBE_MAX_DATA_SIZE

**错误写法**：
```c
struct my_large_data {
    char buffer[8192];  /* 超过 4096 字节 */
};

static struct kretprobe my_rp = {
    .kp = { .symbol_name = "my_func" },
    .handler = my_handler,
    .data_size = sizeof(struct my_large_data),  /* 8192 > 4096 */
    .maxactive = 20,
};
register_kretprobe(&my_rp);  /* 返回 -E2BIG */
```

**正确写法**：
```c
struct my_data {
    char buffer[256];  /* 保持在 4096 以内 */
};

static struct kretprobe my_rp = {
    .kp = { .symbol_name = "my_func" },
    .handler = my_handler,
    .data_size = sizeof(struct my_data),
    .maxactive = 20,
};
register_kretprobe(&my_rp);  /* OK */
```

**原因**：`register_kretprobe()` 在行 2224-2225 检查 `rp->data_size > KRETPROBE_MAX_DATA_SIZE`（定义为 4096，`include/linux/kprobes.h:162`）。超过限制返回 `-E2BIG`。每个 `kretprobe_instance` 在末尾有 `char data[]` 柔性数组（行 177），大小由 `data_size` 决定。过大的 `data_size` 会导致 `maxactive` 个实例的总内存分配过大。

**源码依据**：`kernel/kprobes.c:2224-2225` — `data_size` 检查；`include/linux/kprobes.h:162` — `KRETPROBE_MAX_DATA_SIZE` 定义为 4096；`include/linux/kprobes.h:177` — `char data[]` 柔性数组。

---

### 陷阱 6：在 kprobe handler 中调用被探测的函数导致无限递归

**错误写法**：
```c
static int my_pre_handler(struct kprobe *p, struct pt_regs *regs)
{
    /* 错误：handler 中调用了 printk，而 printk 本身可能有 kprobe */
    printk(KERN_INFO "Probe hit at %p\n", p->addr);
    return 0;
}
```

**正确写法**：
```c
static int my_pre_handler(struct kprobe *p, struct pt_regs *regs)
{
    /* 使用 kprobe_busy_begin/end 保护 */
    kprobe_busy_begin();
    printk(KERN_INFO "Probe hit at %p\n", p->addr);
    kprobe_busy_end();
    return 0;
}
```

**原因**：`kernel/kprobes.c:1237-1255` 定义了 `kprobe_busy` 和 `kprobe_busy_begin()/kprobe_busy_end()`。当 kprobe handler 中调用可能触发其他 kprobe 的函数时，`current_kprobe` per-CPU 变量已被设置为当前 kprobe。如果被调用函数也有 kprobe，`kprobe_handler()` 会检测到 `current_kprobe` 非空，进入递归处理路径（行 1230-1233），递增 `nmissed` 并跳过。使用 `kprobe_busy_begin()` 将 `current_kprobe` 设为 `&kprobe_busy`，明确告知框架此区间内的断点应被忽略。

**源码依据**：`kernel/kprobes.c:1237-1255` — `kprobe_busy` 定义和 `kprobe_busy_begin/end` 实现；`kernel/kprobes.c:1230-1233` — 递归检测时 `nmissed++`。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/kprobes.h` — `struct kprobe`（行 60-95）、`struct kretprobe`（行 147-160）、`struct kretprobe_instance`（行 164-178）、`KRETPROBE_MAX_DATA_SIZE`（行 162）、状态标志定义（行 98-106）
- `kernel/kprobes.c` — `register_kprobe()`（行 1630-1650）、`register_kretprobe()`（行 2198-2270）、`pre_handler_kretprobe()`（行 2093-2118）、`warn_kprobe_rereg()`（行 1522-1532）、`check_kprobe_address_safe()`（行 1568）、`kprobe_busy_begin/end()`（行 1241-1255）、`__kretprobe_trampoline_handler()`（行 2025-2087）
- `arch/arm64/kernel/probes/kprobes.c` — `arch_arm_kprobe()`（行 140-146）、arm64 黑名单（行 392-415）
- `arch/arm64/kernel/probes/kprobes_trampoline.S` — `__kretprobe_trampoline` 汇编

**关键论断出处**：
- "抢占禁用区域注册 kprobe 可能死锁" — 来源：`kernel/kprobes.c:1568`（`preempt_disable()`）及行 1646（`mutex_lock`）
- "maxactive <= 0 时自动设置默认值" — 来源：`kernel/kprobes.c:2231-2232`
- "实例耗尽时 nmissed++ 并跳过" — 来源：`kernel/kprobes.c:2099-2102`
- "重复注册触发 WARN 并返回 -EINVAL" — 来源：`kernel/kprobes.c:1522-1532` 及行 1633-1634
- "data_size 超过 4096 返回 -E2BIG" — 来源：`kernel/kprobes.c:2224-2225` 及 `include/linux/kprobes.h:162`
- "kprobe_busy_begin 设置 current_kprobe 为 sentinel" — 来源：`kernel/kprobes.c:1237-1255`

**未验证项**：
- arm64 XOL 槽位的完整分配和映射流程（`__vmalloc_node_range` + `PAGE_KERNEL_ROX`）未逐行追踪
- `arch_prepare_kprobe()` 中指令解码和 `INSN_GOOD`/`INSN_GOOD_NO_SLOT`/`INSN_REJECTED` 分类逻辑未完整展开
- kretprobe 在 `CONFIG_KRETPROBE_ON_RETHOOK` 路径下的 rethook 交互未详细分析
- `__kretprobe_trampoline` 汇编（`kprobes_trampoline.S:64-86`）的寄存器保存/恢复细节未逐指令验证
