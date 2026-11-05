# 信号（signal）

> B 级文档 · `kernel/signal.c`（4,814 行）· 头文件 `include/linux/signal.h`（492 行）+ `include/linux/signal_types.h`（88 行）

## 1 定位

**功能**：信号是 POSIX 进程间异步通知机制，用于通知进程各类事件（段错误、子进程退出、定时器到期、I/O 就绪等）。进程可以对信号设置捕获处理函数、忽略或按默认动作处理。实时信号（SIGRTMIN~SIGRTMAX）支持排队，标准信号不排队。

**实现方式**：每个线程有私有挂起队列（`task->pending`），每个线程组有共享挂起队列（`signal->shared_pending`），均用 `sigset_t` 位图标记哪些信号已挂起，用 `struct sigqueue` 链表存储实时信号的详细信息。发送路径 `do_send_sig_info()` → `send_signal_locked()` → `prepare_signal()` 处理 SIGCONT 与 stop 信号的互斥逻辑，`complete_signal()` 将致命信号传播到线程组内合适的线程。递送路径在返回用户态时由 `get_signal()` → `dequeue_signal()` 取出信号，通过 `signal_setup_done()` 构造信号帧保存到用户栈并跳转到处理函数。

**应用场景**：信号子系统是内核基础设施，全树 206 个 C 文件引用信号相关头文件。`kernel/exit.c` 在进程退出时发送 SIGCHLD。`mm/oom_kill.c` 在 OOM 时发送 SIGKILL。`kernel/seccomp.c` 对违反 seccomp 过滤规则的系统调用发送 SIGSYS。`kernel/ptrace.c` 用 SIGTRAP 实现调试器通知。`drivers/tty/tty_io.c` 在终端事件时发送 SIGINT/SIGQUIT。`mm/memory-failure.c` 在硬件内存错误时发送 SIGBUS。`kernel/cgroup/cgroup.c` 通过 `send_sig_info()` 通知 cgroup 事件。

## 2 核心数据结构

头文件：`include/linux/signal_types.h`

```c
struct sigaction {                    /* :37 */
    __sighandler_t  sa_handler;       /* 处理函数或 SIG_DFL/SIG_IGN */
    unsigned long   sa_flags;         /* SA_RESTART, SA_SIGINFO 等 */
    __sigrestore_t  sa_restorer;      /* 栈帧恢复 */
    sigset_t        sa_mask;          /* 处理期间阻塞的信号集 */
};

struct ksignal {                      /* :67 */
    struct k_sigaction ka;            /* 包装 struct sigaction */
    kernel_siginfo_t   info;          /* 信号详细信息 */
    int                sig;           /* 信号编号 */
};

struct sigqueue {                     /* :22 */
    struct list_head list;            /* 挂入 sigpending.list */
    kernel_siginfo_t info;
};

struct sigpending {                   /* :32 */
    struct list_head list;
    sigset_t signal;                  /* 位图：哪些信号已挂起 */
};
```

`sigset_t` 是位图数组（`unsigned long sig[_NSIG_WORDS]`），通过 `sigaddset()`/`sigdelset()`/`sigismember()` 操作。

## 3 核心 API

| API | 说明 | 备注 |
|-----|------|------|
| `force_sig(sig)` | 强制发送信号（不可忽略） | `kernel/signal.c:1655` |
| `do_send_sig_info(sig, info, t, type)` | 发送信号到线程/进程组 | `:1294` |
| `send_signal_locked(sig, info, t, type)` | 内部发送（加锁版本） | `:1225` |
| `group_send_sig_info(sig, info, t, type)` | 发送到线程组 | `:1439` |
| `kill_pgrp(pid, sig, priv)` | 杀进程组 | `:1909` |
| `kill_pid_info(sig, info, pid)` | 杀指定 PID | `:1474` |
| `sigprocmask(how, set, oldset)` | 修改信号掩码 | `:3071` |
| `get_signal(ksig)` | 取出待处理信号 | `:2640`，返回到用户态路径调用 |
| `dequeue_signal(tsk, mask, info, type)` | 从挂起队列取出信号 | `:634` |

## 4 信号传递流程

```
1. 发送: do_send_sig_info() → send_signal_locked()
   → prepare_signal()：处理 SIGCONT/stop 互斥
   → 加入 task->pending（私有）或 signal->shared_pending（线程组共享）
   → complete_signal()：传播到组内合适的线程
   → signal_wake_up_state()：唤醒目标线程

2. 取出: get_signal()（返回用户态路径调用）
   → dequeue_signal()：先查 tsk->pending，再查 shared_pending
   → 确定动作：SIG_DFL / SIG_IGN / 用户处理函数

3. 递送: signal_setup_done()
   → 构造信号帧（保存寄存器到用户栈）
   → 跳转到用户处理函数
```

## 5 设计要点

- **SIGCONT 与 stop 互斥**：`prepare_signal()` 收到 SIGCONT 时清除所有 stop 信号，反之亦然
- **致命信号传播**：`complete_signal()` 对致命信号标记整个线程组退出
- **实时信号**：SIGRTMIN~SIGRTMAX 支持排队（通过 `sigqueue` 链表），标准信号不排队
- **pid 命名空间**：信号在内核内部用 `kernel_siginfo`，跨命名空间时自动转换信号编号

共 206 个文件引用信号子系统。
