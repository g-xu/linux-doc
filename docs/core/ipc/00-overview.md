---
title: 总览
---

# 进程间通信 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **Netlink** | 内核↔用户空间双向通信 | 两层 API（原始 + genl），支持组播通知和 dump 大数据量，现代子系统控制面首选 | [netlink.md](netlink.md) |
| **Pipe** | 单向字节流通道 | 环形页面缓冲区 + splice 零拷贝，shell 管道和进程间数据传输的基础 | [misc-pipe.md](misc-pipe.md) |
| **Signal** | 异步事件通知 | POSIX 标准信号机制，标准信号不排队、实时信号排队，不可用于数据传输 | [misc-signal.md](misc-signal.md) |
| **shmem/tmpfs** | 基于页面的内存共享 | MAP_SHARED 匿名映射、dma-buf backing、memfd，支持交换（swap） | [misc-shmem.md](misc-shmem.md) |
| **mqueue** | POSIX 有序消息传递 | 消息按优先级排序，支持超时和异步通知，虚拟文件系统 `/dev/mqueue` | [misc-mqueue.md](misc-mqueue.md) |
| **Relay** | 高速 per-CPU 日志通道 | 写入端中断安全无锁，读取端通过 debugfs，per-CPU 独立缓冲区 | [misc-relay.md](misc-relay.md) |
| **Unix socket** | 同主机全双工通信 | 三种类型（STREAM/DGRAM/SEQPACKET）+ SCM_RIGHTS 文件描述符传递 | [misc-socket.md](misc-socket.md) |

---

## §2 选型决策

```
需要什么样的通信？
│
├─ 内核 → 用户空间 或 用户空间 → 内核
│   ├─ 配置/查询/事件通知 ──→ Netlink（genl 优先）
│   │   ├─ 需要组播通知 ──→ 注册 multicast group
│   │   └─ 需要 dump 大量数据 ──→ NLM_F_DUMP + 多部分消息
│   │
│   └─ 高速跟踪/调试数据 ──→ Relay
│       └─ 中断上下文写入，用户空间 debugfs 读取
│
├─ 进程 → 进程（同主机）
│   │
│   ├─ 需要传递文件描述符 ──→ Unix socket（SCM_RIGHTS）
│   │
│   ├─ 需要共享大块内存
│   │   ├─ 匿名共享（mmap MAP_SHARED）──→ shmem
│   │   └─ 命名文件共享 ──→ tmpfs 文件 或 memfd
│   │
│   ├─ 需要字节流/数据报通信
│   │   ├─ 需要消息边界 ──→ Unix SOCK_SEQPACKET 或 SOCK_DGRAM
│   │   └─ 纯字节流 ──→ Unix SOCK_STREAM 或 Pipe
│   │       └─ 只需单向 ──→ Pipe（更简单）
│   │
│   ├─ 需要异步事件通知（不是数据）──→ Signal
│   │   ├─ 标准信号（SIGTERM/SIGKILL）──→ kill() / force_sig()
│   │   └─ 实时信号（带数据排队）──→ sigqueue()
│   │
│   └─ 需要有序优先级消息 ──→ POSIX mqueue
│       └─ 消息按优先级排序，支持超时和异步通知
│
└─ 不需要 IPC，只是内核内部数据传递
    └─ 看 scatterlist / iov_iter（杂项工具宏类别）
```

**关键区分**：

| 问题 | 选择 |
|------|------|
| 内核↔用户空间 vs 进程↔进程？ | Netlink/Relay（内核↔用户）vs Pipe/Unix socket/shmem（进程↔进程） |
| 需要双向 vs 单向？ | Unix socket / Netlink（双向）vs Pipe（单向） |
| 需要传 fd？ | Unix socket（SCM_RIGHTS，唯一支持） |
| 需要共享大块内存？ | shmem（mmap MAP_SHARED）vs Pipe（需要拷贝） |
| 需要消息优先级？ | mqueue（优先级排序）vs Unix socket（FIFO） |
| 需要中断安全写入？ | Relay（per-CPU 无锁写入）vs 其他（需进程上下文） |
| 只是通知不是数据？ | Signal（异步通知）vs Pipe/Unix socket（数据传输） |

---

## §3 核心对比表

### 3.1 数据通道类

| 维度 | Pipe | Unix socket | shmem/tmpfs | Relay |
|------|------|-------------|-------------|-------|
| 方向 | 单向 | 全双工 | 双向（mmap） | 单向（写→读） |
| 数据单位 | 字节流 | 字节流/数据报/有序包 | 页面 | 子缓冲区 |
| 零拷贝 | splice（`try_steal`） | sendmsg/recvmsg | mmap 直接访问 | splice / mmap |
| 写入上下文 | 进程上下文 | 进程上下文 | 中断安全（page fault） | 中断安全（`local_irq_save`） |
| 代码量 | `fs/pipe.c` 1515 行 | `net/unix/` 5054 行 | `mm/shmem.c` | `kernel/relay.c` 1247 行 |
| 暴露方式 | 文件系统 fd | socket fd | mmap 地址 | debugfs 文件 |

### 3.2 控制面与通知类

| 维度 | Netlink | Signal | mqueue |
|------|---------|--------|--------|
| 方向 | 双向（内核↔用户） | 单向（内核→进程） | 双向（进程↔进程） |
| 数据单位 | nlmsghdr + nlattr TLV | 信号编号 + sigval | 优先级消息 |
| 组播 | 支持（multicast group） | 不支持 | 不支持 |
| 优先级 | 无 | 标准 vs 实时 | 消息优先级排序 |
| 代码量 | `net/netlink/` 5226 行 | `kernel/signal.c` | `ipc/mqueue.c` |
| 暴露方式 | socket fd | 信号递送 | `/dev/mqueue` 文件 |

### 3.3 规模统计

| 模块 | 关键调用 | 次数 | grep 命令 |
|------|---------|------|-----------|
| Netlink | `genl_register_family()` | 52 | `grep -r 'genl_register_family\b' --include='*.c' \| wc -l` |
| Netlink | `netlink_kernel_create()` | 19 | `grep -r 'netlink_kernel_create\b' --include='*.c' \| wc -l` |
| Netlink | `nlmsg_new()` | 309 | `grep -r '\bnlmsg_new\b' --include='*.c' \| wc -l` |
| Netlink | `nla_put()` | 561 | `grep -r '\bnla_put\b' --include='*.c' \| wc -l` |
| Relay | `relay_open()` | 12 | `grep -r '\brelay_open\b' --include='*.c' \| wc -l` |
| Signal | `force_sig()` | 163 | `grep -r '\bforce_sig\b' --include='*.c' \| wc -l` |
| Signal | `send_sig()` | 70 | `grep -r '\bsend_sig\b' --include='*.c' \| wc -l` |
| shmem | `shmem_file_setup()` | 11 | `grep -r '\bshmem_file_setup\b' --include='*.c' \| wc -l` |

---

## §4 使用规模

### 4.1 Netlink 生态

Netlink 是内核中使用最广泛的内核↔用户空间通信机制：

| 指标 | 数量 | grep 命令 |
|------|------|-----------|
| `genl_family` 定义 | 131 | `grep -r 'struct genl_family\b' --include='*.c' \| wc -l` |
| `genl_register_family()` 调用 | 52 | `grep -r 'genl_register_family\b' --include='*.c' \| wc -l` |
| `netlink_kernel_create()` 调用 | 19 | `grep -r 'netlink_kernel_create\b' --include='*.c' \| wc -l` |
| `nlmsg_new()` 消息构造 | 309 | `grep -r '\bnlmsg_new\b' --include='*.c' \| wc -l` |
| `nla_put()` 属性填充 | 561 | `grep -r '\bnla_put\b' --include='*.c' \| wc -l` |

### 4.2 其他 IPC 使用量

| 指标 | 数量 | grep 命令 |
|------|------|-----------|
| `pipe_buf_operations` 实现 | 17 | `grep -r 'struct pipe_buf_operations' --include='*.c' --include='*.h' \| wc -l` |
| `relay_open()` 通道创建 | 12 | `grep -r '\brelay_open\b' --include='*.c' \| wc -l` |
| `force_sig()` 强制信号 | 163 | `grep -r '\bforce_sig\b' --include='*.c' \| wc -l` |
| `send_sig()` 发送信号 | 70 | `grep -r '\bsend_sig\b' --include='*.c' \| wc -l` |
| `signal_wake_up()` 唤醒 | 12 | `grep -r '\bsignal_wake_up\b' --include='*.c' \| wc -l` |
| `shmem_file_setup()` 共享内存 | 11 | `grep -r '\bshmem_file_setup\b' --include='*.c' \| wc -l` |

### 4.3 源码规模

| 子系统 | 行数 | 核心文件 |
|--------|------|---------|
| Netlink | 5226 | `net/netlink/af_netlink.c`（2920 行）、`genetlink.c`（1565 行） |
| Unix socket | 5054 | `net/unix/af_unix.c`、`net/unix/garbage.c` |
| IPC（含 mqueue + SysV） | 9653 | `ipc/` 目录 |
| Pipe | 1515 | `fs/pipe.c` |
| Relay | 1247 | `kernel/relay.c` |

---

## §5 设计模式速览

### 5.1 等待队列驱动阻塞

所有 IPC 机制都依赖等待队列实现阻塞/唤醒：

| 模块 | 等待队列 | 等待条件 |
|------|----------|---------|
| Pipe | `rd_wait` / `wr_wait` | 管道空（读等待）/ 管道满（写等待） |
| Signal | `signal_wake_up_state()` | 信号递送时唤醒目标线程 |
| mqueue | `wait_q` | 队列空（接收等待）/ 队列满（发送等待） |
| Relay | `read_wait` | 无新数据时读者等待 |
| Unix socket | `sk_sleep`（`struct sock` 中） | 无数据到达时等待 |
| Netlink | 继承 socket 层 | 无消息时等待 |
| shmem | 不直接使用 | 通过 page fault 路径按需分配 |

### 5.2 VFS 集成模式

多个 IPC 机制通过虚拟文件系统暴露给用户空间：

| 模块 | 文件系统 | 挂载点 | 实现方式 |
|------|----------|--------|---------|
| mqueue | `mqueue_fs_type` | `/dev/mqueue` | 自有文件系统 |
| shmem/tmpfs | `tmpfs_fs_type` | 用户挂载或内部 `shm_mnt` | 基于页面的文件系统 |
| Relay | `relayfs_fs_type` | debugfs 子目录 | 通过 `relay_create_buf_file()` 回调创建 |
| Pipe | `pipefs_fs_type` | 内部 pipefs（不直接挂载） | 匿名 inode |

### 5.3 命名空间隔离

现代内核为 IPC 提供命名空间隔离：

| 模块 | 命名空间 | 隔离方式 |
|------|----------|---------|
| mqueue | `ipc_namespace` | 每个命名空间独立 `mq_mnt` 和限制 |
| Signal | `pid_namespace` | 信号在内核用 `kernel_siginfo`，跨命名空间自动转换 |
| Unix socket | `net_namespace` | socket 表按网络命名空间隔离 |
| shmem | `user_namespace` | 内存配额按用户命名空间 |

### 5.4 通知/事件推送模式

| 模块 | 通知方式 | 特点 |
|------|----------|------|
| Netlink | 组播（multicast group） | 内核主动推送，多消费者 |
| Signal | 异步递送 | 内核向进程发送信号，不可阻塞（SIGKILL） |
| mqueue | `SIGEV_THREAD` 通知 | 通过内部 netlink socket 发送 cookie |
| Relay | `irq_work` 延迟唤醒 | 从原子上下文延迟到进程上下文唤醒读者 |
| Pipe / Unix socket | 阻塞 I/O | 读者等待数据到达 |
| shmem | 无通知 | 通过 page fault 按需访问 |

---

## §6 锁模型对比

| 模块 | 主要锁 | 锁类型 | 特点 |
|------|--------|--------|------|
| **Pipe** | `pipe_inode_info.mutex` | mutex | 互斥锁保护环形缓冲区，读写互斥 |
| **Signal** | `task_struct->sighand->siglock` | spinlock | 中断安全（`spin_lock_irqsave`），保护信号队列 |
| **mqueue** | `mqueue_inode_info.lock` | spinlock + `wake_q` | spinlock 保护消息树，`wake_q` 延迟唤醒避免死锁 |
| **Relay** | 无全局锁 | `local_irq_save()` | per-CPU 设计，仅保护当前 CPU 的缓冲区 |
| **Unix socket** | `unix_sock.iolock` + 哈希表锁 | mutex + hash lock | 双锁结构：iolock 保护 I/O 操作，哈希表锁保护地址查找 |
| **Netlink** | `netlink_table.lock` + cb mutex | rwlock + mutex | 读写锁保护分派表，cb mutex 保护 dump 回调 |
| **shmem** | `shmem_inode_info.lock` | spinlock | 保护 inode 元数据和 swap 操作 |

**关键差异**：
- **Relay 是唯一中断安全写入的 IPC**：通过 `local_irq_save()` 保护当前 CPU 的 per-CPU 缓冲区，不同 CPU 之间完全无竞争
- **Pipe 和 Unix socket 需要进程上下文**：使用 mutex，不能在原子上下文中调用
- **Signal 的 siglock 是中断安全的 spinlock**：因为信号可以在任何上下文中递送（包括中断处理）

---

## §7 组合使用模式

### 7.1 Pipe + splice 零拷贝

Pipe 的 `pipe_buf_operations` 被多个子系统实现（全树 17 处 `struct pipe_buf_operations` 引用）：
- `fs/splice.c`：splice 系统调用通过 `try_steal()` 直接转移页面所有权
- `kernel/trace/trace.c`：ftrace 环形缓冲区使用 pipe 基础设施
- `net/smc/smc_rx.c`：SMC 网络接收使用 pipe 缓冲

### 7.2 Netlink + 组播通知

Netlink 的组播机制是内核子系统向用户空间推送事件的标准方式：
- `NETLINK_ROUTE`：路由变更通知
- `NETLINK_KOBJECT_UEVENT`：设备热插拔事件
- 各 genl family 注册自定义组播组

### 7.3 Relay + debugfs + blktrace

Relay 的典型使用模式：
1. `relay_open()` 创建 per-CPU 通道（12 处调用）
2. 回调 `create_buf_file()` 在 debugfs 下创建文件
3. 内核代码（如 `kernel/trace/blktrace.c`，10 处 relay 引用）在中断上下文写入数据
4. 用户空间通过 debugfs 文件 read / mmap / splice 读取

### 7.4 Unix socket + SCM_RIGHTS

Unix socket 的文件描述符传递是 systemd 等用户空间服务的核心：
- `unix_attach_fds()` / `unix_detach_fds()` 将 `struct scm_fp_list` 附加到 skb
- 垃圾回收（`net/unix/garbage.c`）使用 Tarjan SCC 算法检测循环 fd 引用
- `unix_tot_inflight` 超过阈值时触发 GC

### 7.5 shmem + dma-buf

shmem 作为 dma-buf 的后端存储：
- `shmem_file_setup()` 创建匿名 shmem 文件（11 处调用）
- dma-buf 子系统使用 shmem 页面作为 GPU / 显示缓冲
- 支持交换（swap）——不活跃页面可换出到 swap 分区

### 7.6 Signal + wait_queue

信号递送与等待队列的配合：
- `signal_wake_up_state()`（12 处调用）唤醒被信号打断的等待者
- `force_sig()`（163 处调用）强制发送不可阻塞的信号
- 实时信号通过 `sigqueue()` 携带数据排队

---

## §8 阅读顺序建议

### 路径 A：理解内核↔用户空间通信

1. **Netlink** → 现代子系统控制面首选，genl 框架
2. **Pipe** → 最基本的字节流通道
3. **Relay** → 高速 per-CPU 数据传输

### 路径 B：理解进程间通信

1. **Pipe** → 单向字节流基础
2. **Unix socket** → 全双工 + fd 传递
3. **shmem** → 共享内存
4. **mqueue** → 优先级消息队列
5. **Signal** → 异步事件通知

### 路径 C：理解 IPC 底层机制

1. **Pipe** → 环形缓冲区 + 等待队列
2. **Signal** → 信号递送 + siglock
3. **Unix socket** → socket 层 + GC
4. **Netlink** → 消息构造/解析 + genl 框架

### 路径 D：跟踪/调试数据通道

1. **Relay** → per-CPU 无锁写入
2. **Pipe** → splice 零拷贝
3. **Netlink** → 事件通知

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 |
|-------------|----------|
| Pipe vs Unix socket | Pipe 单向字节流；Unix socket 全双工 + 支持 fd 传递 |
| Netlink vs Unix socket | Netlink 是内核↔用户空间通信；Unix socket 是进程↔进程通信 |
| shmem vs Pipe | shmem 通过 mmap 共享页面（零拷贝）；Pipe 需要数据拷贝（splice 除外） |
| Relay vs Pipe | Relay 中断安全写入（per-CPU 无锁）；Pipe 需要进程上下文 |
| Signal vs mqueue 通知 | Signal 是异步事件通知（不可传数据）；mqueue 的 `SIGEV_THREAD` 通过 netlink 传 cookie |
| Netlink 原始 vs genl | 原始 netlink 直接操作 nlmsghdr；genl 提供多路复用框架、属性验证、权限检查 |

---

## §10 文档索引

| 序号 | 文档 | 覆盖模块 |
|------|------|----------|
| 1 | [netlink.md](netlink.md) | Netlink 完整框架：原始 netlink + genl + 属性策略 |
| 2 | [misc-pipe.md](misc-pipe.md) | Pipe 环形缓冲区、splice 零拷贝 |
| 3 | [misc-signal.md](misc-signal.md) | Signal 传递流程、SIGCONT/stop 互斥、实时信号 |
| 4 | [misc-shmem.md](misc-shmem.md) | shmem/tmpfs 页面分配、MAP_SHARED、dma-buf |
| 5 | [misc-mqueue.md](misc-mqueue.md) | POSIX mqueue VFS 集成、优先级消息树、通知 |
| 6 | [misc-relay.md](misc-relay.md) | Relay per-CPU 无锁写入、debugfs 暴露 |
| 7 | [misc-socket.md](misc-socket.md) | Unix socket 三类型、SCM_RIGHTS fd 传递、GC |
