# printk — 内核日志子系统

> **定位**：printk 是 Linux 内核的日志基础设施。它提供从内核代码输出日志消息的能力，管理日志缓冲区（ringbuffer），将消息分发到注册的 consoles，并通过 `/dev/kmsg` 和 `/proc/kmsg` 暴露给用户空间。

---

## 1. 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/printk/printk.c` | 4755 | 核心：ringbuffer、console 管理、syslog、kmsg_dump |
| `kernel/printk/printk_ringbuffer.h` | 384 | ringbuffer 数据结构定义 |
| `kernel/printk/printk_ringbuffer.c` | 2124 | ringbuffer 实现 |
| `kernel/printk/printk_safe.c` | 79 | NMI/安全上下文 printk |
| `kernel/printk/sysctl.c` | 85 | sysctl 接口 |
| `include/linux/printk.h` | 780 | 公开 API：`printk`、`pr_*` 宏 |
| `include/linux/console.h` | 266 | `struct console` 定义 |
| `include/linux/kern_levels.h` | 40 | 日志级别常量 |
| `include/linux/dev_printk.h` | 278 | `dev_*` 系列宏 |
| `include/linux/kmsg_dump.h` | 110 | kmsg_dump 接口 |

---

## 2. 核心数据结构

### 2.1 struct console（`include/linux/console.h:155-193`）

```c
struct console {
    char    name[16];
    void    (*write)(struct console *, const char *, unsigned);
    void    (*write_atomic)(struct console *, const char *, unsigned);
    int     (*read)(struct console *, char *, unsigned);
    struct tty_driver *(*device)(struct console *, int *);
    void    (*unblank)(void);
    int     (*setup)(struct console *, char *);
    int     (*exit)(struct console *);
    int     (*match)(struct console *, char *name, int idx, char *options);
    short   flags;
    short   index;
    int     cflag;
    u64     seq;                    // 下一条要输出的消息序号
    atomic_long_t dropped;          // 丢弃的消息计数
    struct task_struct *thread;     // per-console 打印线程
    bool    blocked;
    struct mutex lock;              // per-console 锁
    void    *data;
    struct  console *next;          // 链表
};
```

### 2.2 struct printk_ringbuffer（`kernel/printk/printk_ringbuffer.h:87-91`）

两级无锁设计：
- **描述符环**（`prb_desc_ring`）：`prb_desc` 数组，每个含原子 `state_var` 和文本块位置
- **文本数据环**（`prb_data_ring`）：消息文本的循环字节缓冲区

### 2.3 struct printk_info（`kernel/printk/printk_ringbuffer.h:15-25`）

每条消息的元数据：`seq`、`ts_nsec`、`text_len`、`facility`、`level`、`caller_id`、`dev_info`。

---

## 3. 核心 API

头文件：`include/linux/printk.h`；实现：`kernel/printk/printk.c`

### 3.1 日志输出

| API | 说明 | 备注 |
|-----|------|------|
| `printk(fmt, ...)` | 主日志函数 | 宏，包装 `_printk` 并支持 printk index |
| `_printk(fmt, ...)` | 实际实现 | `printk.c:2480`；调用 `vprintk()` |
| `vprintk_emit(facility, level, dev_info, fmt, args)` | 核心入口 | `printk.c:2407`；存入 ringbuffer，触发 console 输出 |
| `vprintk_deferred(fmt, args)` | 延迟 console 输出 | NMI/安全上下文使用；通过 irq_work 延迟 |

### 3.2 pr_* 系列（`include/linux/printk.h`）

| 宏 | 级别 | 行号 |
|----|------|------|
| `pr_emerg` | KERN_EMERG (0) | 484 |
| `pr_alert` | KERN_ALERT (1) | 494 |
| `pr_crit` | KERN_CRIT (2) | 504 |
| `pr_err` | KERN_ERR (3) | 514 |
| `pr_warn` | KERN_WARNING (4) | 524 |
| `pr_notice` | KERN_NOTICE (5) | 534 |
| `pr_info` | KERN_INFO (6) | 544 |
| `pr_debug` | KERN_DEBUG (7) | 595/598/601 |
| `pr_cont` | KERN_CONT | 557 |

### 3.3 dev_* 系列（`include/linux/dev_printk.h`）

| 宏 | 说明 |
|----|------|
| `dev_err(dev, fmt, ...)` | 设备错误日志 |
| `dev_warn(dev, fmt, ...)` | 设备警告 |
| `dev_info(dev, fmt, ...)` | 设备信息 |
| `dev_dbg(dev, fmt, ...)` | 设备调试（dynamic debug 控制） |

### 3.4 Console 管理

| API | 说明 | 备注 |
|-----|------|------|
| `register_console(con)` | 注册 console 驱动 | `printk.c:3633`；处理 bootconsole vs 真实 console |
| `unregister_console(con)` | 注销 console | `printk.c:3760` |
| `console_lock()` | 可睡眠锁 | 阻塞 console kthreads |
| `console_trylock()` | 非阻塞尝试 | 返回 1 表示成功 |
| `console_unlock()` | 释放锁 | 刷新待发消息 |
| `console_flush_on_panic(mode)` | panic 时强制刷新 | 模式：ATOMIC/FLUSH/REPLAY_ALL |

### 3.5 kmsg_dump 接口

| API | 说明 | 备注 |
|-----|------|------|
| `kmsg_dump(reason)` | 触发所有注册的 dumper | `printk.c:4457` |
| `kmsg_dump_register(dumper)` | 注册崩溃 dumper | |
| `kmsg_dump_unregister(dumper)` | 注销 dumper | |
| `kmsg_dump_get_line(iter, syslog, line, size, len)` | 获取一行日志 | |
| `kmsg_dump_rewind(iter)` | 重置迭代器 | |

### 3.6 限速变体

| 宏 | 行号 |
|----|------|
| `printk_ratelimited` | 659 |
| `pr_err_ratelimited` | 679 |
| `pr_warn_ratelimited` | 681 |
| `pr_info_ratelimited` | 685 |

---

## 4. 机制剖析

### 4.1 日志缓冲区

- **静态缓冲区**：`static char __log_buf[__LOG_BUF_LEN]`（`printk.c:541`）
- **大小**：`__LOG_BUF_LEN = (1 << CONFIG_LOG_BUF_SHIFT)`；最大 `1 << 31`
- **Ringbuffer**：`printk_rb_static`（静态）→ `printk_rb_dynamic`（调整后）
- **活跃指针**：`prb` 初始指向静态，log_buf 调整后切换到动态

### 4.2 Console 注册与选择

`register_console()`（`printk.c:3633`）流程：
1. 检查重复注册
2. 分离 bootconsole（`CON_BOOT` 标志）和真实 console
3. 如果已有真实 console，拒绝 bootconsole 注册
4. 如果没有首选 console，为第一个有 tty 绑定的 console 调用 `try_enable_default_console()`
5. 匹配 `console_cmdline[]` 数组（来自内核命令行 `console=` 参数）
6. bootconsole 在真实 console 注册时自动注销

### 4.3 printk 安全上下文（NMI 安全）

**文件**：`kernel/printk/printk_safe.c`（79 行）

- per-CPU 计数器 `printk_context`
- `__printk_safe_enter()`：递增计数器
- `vprintk()`：如果 `in_nmi()` 或 `printk_context` 已设置，路由到 `vprintk_deferred()`——存入 ringbuffer 但通过 irq_work 延迟 console 输出

### 4.4 printk kthreads

- `printk_kthread_func()`（`printk.c:4074`）：per-console 打印线程
- `printk_start_kthread()`（`printk.c:4193`）：创建名为 `"pr/%s%d"` 的 kthread
- `printk_activate_kthreads()`（`printk.c:3913`）：`early_initcall`，为所有注册的 console 启动 kthreads
- 回退：如果 kthread 分配失败，启用直接打印

### 4.5 Early printk

```c
#ifdef CONFIG_EARLY_PRINTK
struct console *early_console;
asmlinkage __visible void early_printk(const char *fmt, ...)
```

格式化到 512 字节栈缓冲区，直接调用 `early_console->write()`。不涉及 ringbuffer。

---

## 5. 日志级别

| 级别 | 字符串 | 整数值 | 描述 |
|------|--------|--------|------|
| `KERN_EMERG` | `\0010` | 0 | 系统不可用 |
| `KERN_ALERT` | `\0011` | 1 | 必须立即采取行动 |
| `KERN_CRIT` | `\0012` | 2 | 严重条件 |
| `KERN_ERR` | `\0013` | 3 | 错误条件 |
| `KERN_WARNING` | `\0014` | 4 | 警告条件 |
| `KERN_NOTICE` | `\0015` | 5 | 正常但重要 |
| `KERN_INFO` | `\0016` | 6 | 信息 |
| `KERN_DEBUG` | `\0017` | 7 | 调试级别 |
| `KERN_CONT` | `\001c` | -- | 续行 |

**console_printk 数组**（`printk.c:64-69`）：
```c
int console_printk[4] = {
    CONSOLE_LOGLEVEL_DEFAULT,   /* console_loglevel */
    MESSAGE_LOGLEVEL_DEFAULT,   /* default_message_loglevel */
    CONSOLE_LOGLEVEL_MIN,       /* minimum_console_loglevel */
    CONSOLE_LOGLEVEL_DEFAULT,   /* default_console_loglevel */
};
```

---

## 6. 用户空间接口

### 6.1 /dev/kmsg

- **文件操作**：`kmsg_fops`（`printk.c:1029-1036`）
- **导出格式**：`<level>,<sequnum>,<timestamp>,<contflag>[,...];<message text>\n`
- **用户状态**：`struct devkmsg_user`（`printk.c:765-774`）跟踪 per-reader 序号

### 6.2 /proc/kmsg

- 通过 `do_syslog()`（`printk.c:1790`）接口
- 提供更简单的非结构化文本流
- 受 `dmesg_restrict` sysctl 控制

### 6.3 syslog() 系统调用

- `do_syslog()` 处理所有 `SYSLOG_ACTION_*` 操作
- 操作：CLOSE, OPEN, READ, READ_CLEAR, READ_ALL, CLEAR, CONSOLE_OFF/ON, CONSOLE_LEVEL, SIZE_UNREAD, SIZE_BUFFER

---

## 7. arm64 特性：Early Console

arm64 使用通用 earlycon 框架（`drivers/tty/serial/earlycon.c`）。

常见 arm64 earlycon 驱动：

| 驱动 | 兼容字符串 |
|------|-----------|
| PL011 | `"arm,pl011"` |
| SBSA UART | `"arm,sbsa-uart"` |
| 8250/16550 | `"nvidia,tegra20-uart"`, `"snps,dw-apb-uart"` |
| Samsung | `"samsung,s3c2410-uart"` |
| Qualcomm GENI | `"qcom,geni-debug-uart"` |
| i.MX | `"fsl,imx6q-uart"` |

PL011 是最常见的 arm64 UART。

---

## 8. 使用统计

| API | 调用点数 |
|-----|---------|
| `dev_err()` | 62,994 |
| `printk()` | 37,016 |
| `pr_err()` | 20,311 |
| `dev_info()` | 11,264 |
| `pr_debug()` | 13,110 |
| `pr_info()` | 9,284 |
| `pr_warn()` | 6,335 |

`dev_err()` 是内核中使用最频繁的日志调用。

---

## §4 常见陷阱

### 陷阱 1：在原子上下文中使用 printk 导致 console 输出阻塞或死锁

**错误写法**：
```c
void my_irq_handler(void)
{
    /* 在中断处理程序中直接 printk */
    printk(KERN_ERR "IRQ happened: %d\n", irq);
    /* 在非 PREEMPT_RT 上可能尝试获取 console_sem 并自旋 */
}
```

**正确写法**：
```c
void my_irq_handler(void)
{
    /* 方式 A：使用 printk_deferred，仅写入 ringbuffer，延迟 console 输出 */
    printk_deferred(KERN_ERR "IRQ happened: %d\n", irq);

    /* 方式 B：依赖 printk_safe 机制（NMI/irq 路径自动路由到 deferred） */
    printk(KERN_ERR "IRQ happened: %d\n", irq);
    /* vprintk() 检测到 in_nmi() 或 printk_context 时自动走 deferred 路径 */
}
```

**原因**：`kernel/printk/printk.c:2432-2462` 中，`vprintk_emit()` 在非调度上下文时尝试通过 `console_trylock_spinning()` 直接输出到 console。在原子上下文中（如持有 spinlock 或中断关闭），如果 console 驱动需要获取锁（如串口驱动的 port_lock），可能产生死锁。`printk_deferred` 通过 `vprintk_emit(0, LOGLEVEL_SCHED, ...)` 路径（行 4318-4321）跳过直接 console 输出，仅写入 ringbuffer 并通过 `defer_console_output()` 延迟到安全上下文。

**源码依据**：`kernel/printk/printk.c:2432-2462` — `vprintk_emit()` 中的直接打印路径；`kernel/printk/printk.c:4318-4321` — `vprintk_deferred()` 使用 `LOGLEVEL_SCHED` 跳过直接输出。

---

### 陷阱 2：printk 消息缺少日志级别导致继承默认级别

**错误写法**：
```c
/* 没有指定日志级别 */
printk("Device initialized\n");
/* 实际级别 = default_message_loglevel（通常为 4 = KERN_WARNING） */
```

**正确写法**：
```c
/* 明确指定日志级别 */
printk(KERN_INFO "Device initialized\n");
/* 或使用 pr_info 宏 */
pr_info("Device initialized\n");
```

**原因**：`kernel/printk/printk.c:2339-2343` 中，`vprintk_store()` 调用 `printk_parse_prefix()` 解析消息前缀中的日志级别。如果消息没有 `\001N` 格式的级别前缀（`facility == 0` 时），`level` 保持 `LOGLEVEL_DEFAULT`，然后被替换为 `default_message_loglevel`（行 2342-2343），通常是 4（KERN_WARNING）。这意味着"普通" `printk()` 消息会以 WARNING 级别出现在控制台上，可能触发不必要的 console 输出或被日志收集工具误分类。

**源码依据**：`kernel/printk/printk.c:2339-2343` — 默认级别回退逻辑；`include/linux/printk.h:50` — `MESSAGE_LOGLEVEL_DEFAULT` 定义。

---

### 陷阱 3：panic 时非 panic CPU 的 printk 被静默丢弃

**错误写法**：
```c
/* 在 panic 处理中，所有 CPU 都执行 printk */
void my_panic_notifier(struct notifier_block *nb, unsigned long action, void *data)
{
    /* 非 panic CPU 上的 printk 会被静默丢弃 */
    printk(KERN_EMERG "My panic handler running on CPU %d\n", smp_processor_id());
}
```

**正确写法**：
```c
void my_panic_notifier(struct notifier_block *nb, unsigned long action, void *data)
{
    /* 确认在 panic CPU 上运行 */
    if (atomic_read(&panic_cpu) != raw_smp_processor_id()) {
        /* 非 panic CPU：消息会被 suppress_panic_printk 丢弃 */
        /* 使用其他方式记录或直接跳过 */
        return;
    }
    printk(KERN_EMERG "My panic handler running\n");
}
```

**原因**：`kernel/printk/printk.c:2418-2420` 中，`vprintk_emit()` 检查 `suppress_panic_printk` 标志和 `panic_cpu`。当 `suppress_panic_printk` 被设置（通常因为 ringbuffer 溢出过多，行 3105-3106）且当前 CPU 不是 panic CPU 时，`printk` 直接返回 0，消息被静默丢弃。这是为了防止非 panic CPU 的大量日志输出导致 panic CPU 的 livelock。

**源码依据**：`kernel/printk/printk.c:2418-2420` — `suppress_panic_printk` 检查；`kernel/printk/printk.c:3105-3106` — `suppress_panic_printk` 的设置条件。

---

### 陷阱 4：连续 printk 消息（KERN_CONT）在多 CPU 下交错

**错误写法**：
```c
void dump_data(const u8 *buf, int len)
{
    int i;
    printk(KERN_INFO "Data: ");
    for (i = 0; i < len; i++)
        printk(KERN_CONT "%02x ", buf[i]);  /* 多 CPU 下可能交错 */
    printk(KERN_CONT "\n");
}
```

**正确写法**：
```c
void dump_data(const u8 *buf, int len)
{
    char line[256];
    int offset = 0, i;

    /* 先格式化到缓冲区，一次性输出 */
    offset += scnprintf(line + offset, sizeof(line) - offset, "Data: ");
    for (i = 0; i < len && offset < sizeof(line) - 4; i++)
        offset += scnprintf(line + offset, sizeof(line) - offset, "%02x ", buf[i]);
    printk(KERN_INFO "%s\n", line);
}
```

**原因**：`KERN_CONT` 消息通过 `prb_reserve_in_last()`（`kernel/printk/printk.c:2350`）追加到上一条未完成的消息。但在多 CPU 并发场景下，不同 CPU 的 `KERN_CONT` 消息可能交错——CPU A 的 `printk(KERN_CONT "aa")` 和 CPU B 的 `printk(KERN_CONT "bb")` 可能互相穿插，导致输出混乱。`printk.c:2348-2359` 的 `LOG_CONT` 路径使用 `caller_id` 匹配上一条消息，但不同 `caller_id` 的续行会创建新记录。

**源码依据**：`kernel/printk/printk.c:2348-2359` — `LOG_CONT` 处理逻辑，`prb_reserve_in_last()` 按 `caller_id` 匹配；`include/linux/printk.h:557` — `pr_cont` 定义。

---

### 陷阱 5：console_lock 在 panic 路径中导致死锁

**错误写法**：
```c
void my_console_write(struct console *con, const char *s, unsigned int count)
{
    /* 错误：在 console write 回调中再次获取 console_lock */
    console_lock();
    /* ... 做一些需要同步的操作 ... */
    console_unlock();
}
```

**正确写法**：
```c
void my_console_write(struct console *con, const char *s, unsigned int count)
{
    /* console write 回调已在 console 输出路径中，不能再获取 console_lock */
    /* 使用 spinlock 保护硬件寄存器访问 */
    unsigned long flags;
    spin_lock_irqsave(&my_port->lock, flags);
    /* 直接写硬件寄存器 */
    spin_unlock_irqrestore(&my_port->lock, flags);
}
```

**原因**：`console_lock()` 获取 `console_sem`（`kernel/printk/printk.c:2794`），而 console 的 `write()` 回调正是在持有 `console_sem` 的 `console_unlock()` 路径中被调用的。如果在 `write()` 回调中再次调用 `console_lock()`，会导致死锁（`console_sem` 不可递归获取）。panic 路径使用 `console_trylock()`（行 3454）来避免此问题，但普通路径没有这种保护。

**源码依据**：`kernel/printk/printk.c:2975-2989` — `console_unlock()` 释放 `console_sem` 前调用各 console 的 `write()`；`kernel/printk/printk.c:3454` — panic 路径使用 `console_trylock()`。

---

## §6 取证摘要

**读取的文件**：
- `include/linux/printk.h` — `printk_get_level()`（行 19-29）、`pr_*` 宏定义（行 484-557）、`MESSAGE_LOGLEVEL_DEFAULT`（行 50）、`console_printk[]` 声明（行 65-70）、`printk_deferred_enter/exit`（行 169-170）
- `kernel/printk/printk.c` — `vprintk_emit()`（行 2407-2471）、`vprintk_store()`（行 2290-2405）、`vprintk_deferred()`（行 4318-4321）、`suppress_panic_printk` 检查（行 2418-2420）、`suppress_panic_printk` 设置（行 3105-3106）、`console_lock()`/`console_trylock()`（行 2794、2031）、`console_unlock()` 调用 write（行 2975-2989）、`LOG_CONT` 处理（行 2348-2359）、`printk_enter_irqsave()`/`printk_exit_irqrestore()`（行 2171-2193）
- `kernel/printk/printk_safe.c` — NMI/安全上下文 printk 路由
- `include/linux/kern_levels.h` — 日志级别常量定义

**关键论断出处**：
- "原子上下文中 printk 可能阻塞" — 来源：`kernel/printk/printk.c:2432-2462`（`console_trylock_spinning()` 路径）
- "缺少日志级别时回退到 default_message_loglevel" — 来源：`kernel/printk/printk.c:2342-2343`
- "panic 时非 panic CPU 消息被丢弃" — 来源：`kernel/printk/printk.c:2418-2420` 及行 3105-3106
- "KERN_CONT 按 caller_id 匹配上一条消息" — 来源：`kernel/printk/printk.c:2348-2359`
- "console write 回调中不能获取 console_lock" — 来源：`kernel/printk/printk.c:2975-2989`（调用路径）及行 2794（`console_lock` 获取 `console_sem`）

**未验证项**：
- `printk_kthread_func()` 的完整唤醒和输出循环未逐行追踪
- `prb_reserve_in_last()` 的 ringbuffer 内部实现细节（描述符状态机）未展开
- `CONFIG_PREEMPT_RT` 下的 console 输出路径差异（行 2433-2443）未详细分析
- `early_printk()` 的 arm64 earlycon 框架具体驱动初始化流程未覆盖
