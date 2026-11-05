# 第 2 章 进程管理 — 深度总结

> 原书：《Linux内核深度解析》余华兵著，基于 ARM64 / Linux 4.x
> 本文档：**基于 Linux 6.1.158 内核源码重新整理**，结合网络资料更新

---

## 2.1 进程

- Linux 内核把进程称为**任务（task）**，用 `task_struct` 结构体描述（定义在 `include/linux/sched.h`）
- 进程的虚拟地址空间分为**用户虚拟地址空间**和**内核虚拟地址空间**
- 所有进程共享内核虚拟地址空间，每个进程有独立的用户虚拟地址空间
- **内核线程**：没有用户虚拟地址空间（`mm = NULL`）
- **用户线程**：共享同一用户虚拟地址空间的多个进程（`CLONE_VM`），构成线程组

## 2.2 命名空间

| 命名空间 | 隔离资源 |
|---|---|
| cgroup | 控制组根目录 |
| IPC | 进程间通信和 POSIX 消息队列 |
| network | 网络协议栈 |
| mount | 挂载点 |
| PID | 进程号 |
| user | 用户标识和权限 |
| UTS | 主机名和 NIS 域名 |
| time (Linux 5.6 引入) | 系统时钟偏移 |

---

## 2.3 进程标识符（基于 Linux 6.1 重写）

### 四种标识符类型

Linux 6.1 中 `enum pid_type` 定义了四种标识符类型（`include/linux/pid.h`）：

```c
enum pid_type {
    PIDTYPE_PID,    // 进程标识符
    PIDTYPE_TGID,   // 线程组标识符（Linux 4.19 引入独立类型，更早版本复用 PID）
    PIDTYPE_PGID,   // 进程组标识符
    PIDTYPE_SID,    // 会话标识符
    PIDTYPE_MAX,
};
```

**与早期内核的变化**：4.19 之前的内核只有 `PIDTYPE_PID`、`PIDTYPE_PGID`、`PIDTYPE_SID` 三种，TGID 通过 `task_struct->tgid` 字段存储。4.19 将 TGID 提升为独立的 `PIDTYPE_TGID`，使其与其他标识符统一管理。

### task_struct 中的 PID 相关字段

```c
struct task_struct {
    pid_t  pid;          // 全局 PID 数值（缓存，快速访问）
    pid_t  tgid;         // 全局 TGID 数值（缓存）

    struct pid *thread_pid;                  // 指向本任务的 struct pid
    struct hlist_node pid_links[PIDTYPE_MAX]; // 挂载到 struct pid 的哈希链

    struct task_struct *group_leader;         // 线程组组长
    struct list_head   thread_group;          // 线程组链表
    // ...
};
```

### struct pid 核心结构

```c
struct upid {
    int nr;                      // 在该命名空间中的数值 ID
    struct pid_namespace *ns;    // 所属的 PID 命名空间
};

struct pid {
    refcount_t count;            // 引用计数
    unsigned int level;          // 所在命名空间的层级深度
    spinlock_t lock;
    struct hlist_head tasks[PIDTYPE_MAX]; // 四条链表，分别挂载不同角色的 task
    struct hlist_head inodes;    // 关联的 /proc inode
    wait_queue_head_t wait_pidfd;// pidfd 等待队列（6.1 新增）
    struct rcu_head rcu;
    struct upid numbers[];       // 柔性数组：每层命名空间一个 upid
};
```

### 标识符之间的关系图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PID 命名空间层次                             │
│                                                                     │
│  ┌─────────────────┐                                                │
│  │ init_pid_ns      │  level=0 (根命名空间)                          │
│  │ (全局命名空间)    │                                                │
│  └────────┬────────┘                                                │
│           │ 创建子命名空间                                            │
│  ┌────────▼────────┐                                                │
│  │ child_pid_ns     │  level=1                                      │
│  │ (容器命名空间)    │                                                │
│  └─────────────────┘                                                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    struct pid 与 task 的关系                         │
│                                                                     │
│  struct pid (PID=100)                                               │
│  ┌──────────────────────────────┐                                   │
│  │ count = 3                    │                                   │
│  │ level = 0                    │                                   │
│  │ tasks[PIDTYPE_PID]  ──────────────► task A (thread_pid=this)     │
│  │ tasks[PIDTYPE_TGID] ──────────────► task A (线程组组长)            │
│  │ tasks[PIDTYPE_PGID] ──────────────► task A ──► task B ──► task C │
│  │ tasks[PIDTYPE_SID]  ──────────────► task A ──► task D            │
│  │ numbers[0] = {nr=100, ns=init_pid_ns}                           │
│  └──────────────────────────────┘                                   │
│                                                                     │
│  含义：PID 100 同时是：                                               │
│    - task A 的进程 ID                                                │
│    - task A 所领导的线程组 ID                                         │
│    - task A/B/C 所在进程组的组长 ID                                    │
│    - task A/D 所在会话的会话 ID                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 线程组内的 PID 关系

```
┌─────────────────────────────────────────────────────────────────────┐
│                       线程组 (Thread Group)                         │
│                                                                     │
│  ┌──────────────────┐                                               │
│  │ 主线程 (组长)     │  pid=100, tgid=100                            │
│  │ group_leader=self │  thread_pid → struct pid (nr=100)            │
│  └───────┬──────────┘                                               │
│          │ thread_group 链表                                         │
│  ┌───────▼──────────┐                                               │
│  │ 子线程 1          │  pid=101, tgid=100                            │
│  │ group_leader ─────────► 主线程                                    │
│  │ thread_pid → struct pid (nr=101)                                 │
│  └───────┬──────────┘                                               │
│  ┌───────▼──────────┐                                               │
│  │ 子线程 2          │  pid=102, tgid=100                            │
│  │ group_leader ─────────► 主线程                                    │
│  │ thread_pid → struct pid (nr=102)                                 │
│  └──────────────────┘                                               │
│                                                                     │
│  用户态 getpid() 返回 tgid=100 (所有线程看到相同的 PID)               │
│  内核内部 task->pid 是各线程独立的 (100, 101, 102)                    │
└─────────────────────────────────────────────────────────────────────┘
```

### 线程组、进程组、会话 — 三个层次的进程聚合

Linux 有三个不同层次的进程分组机制，容易混淆。它们解决不同的问题：

| 概念 | 标识符 | 粒度 | 创建方式 | 核心用途 |
|---|---|---|---|---|
| 线程组 (Thread Group) | TGID | 最细 — 同一进程的多个线程 | `clone(CLONE_THREAD)` / `pthread_create` | 线程管理，信号投递 |
| 进程组 (Process Group) | PGID | 中等 — 多个相关进程 | `setpgid()` / shell 管道 | 作业控制，信号批量发送 |
| 会话 (Session) | SID | 最粗 — 多个进程组 | `setsid()` | 终端关联，登录会话管理 |

#### 线程组 (Thread Group)

```
线程组 = 用户态看到的"一个进程"

用户态视角:                    内核视角:
┌─────────────────┐           一个线程组 = 多个 task_struct
│  进程 PID=100    │           共享: 地址空间(mm), 文件描述符, 信号处理函数
│  (3 个线程)      │           各自独立: 内核栈, 寄存器, 调度实体
└─────────────────┘
                               task_struct (pid=100, tgid=100) ← 组长 group_leader
                               task_struct (pid=101, tgid=100) ← 子线程
                               task_struct (pid=102, tgid=100) ← 子线程

getpid() 返回 tgid (100)      → 所有线程看到相同的 PID
gettid() 返回 pid (100/101/102) → 每个线程有独立 TID
kill(100, sig)                → 信号发给整个线程组
tkill(101, sig)               → 信号发给指定线程
```

#### 进程组 (Process Group)

```
进程组 = shell 中一条命令行（含管道）产生的所有进程

Shell 执行: cat file.txt | grep "error" | wc -l

  ┌────────────────────────────────────────────────────────────────┐
  │  进程组 PGID=200 (前台作业)                                     │
  │                                                                │
  │  cat (pid=200, pgid=200) ← 进程组组长 (pid == pgid)            │
  │    │ pipe                                                      │
  │  grep (pid=201, pgid=200)                                      │
  │    │ pipe                                                      │
  │  wc (pid=202, pgid=200)                                        │
  └────────────────────────────────────────────────────────────────┘

关键行为:
  shell 创建管道中的第一个进程 → 其 PID 成为 PGID (进程组组长)
  后续管道进程通过 setpgid() 加入同一个进程组

  Ctrl+C → 终端驱动发送 SIGINT 给前台进程组的所有进程
  Ctrl+Z → 终端驱动发送 SIGTSTP 给前台进程组 → 作业暂停
  kill(-pgid, sig) → 信号发送给整个进程组

  fg / bg → shell 通过 tcsetpgrp() 切换前台进程组
```

#### 会话 (Session)

```
会话 = 一次用户登录（通常绑定一个控制终端）

用户通过 SSH 登录:

  ┌──────────────────────────────────────────────────────────────────────┐
  │  会话 SID=150                                                       │
  │  控制终端: /dev/pts/0                                               │
  │                                                                     │
  │  ┌── 进程组 PGID=150 (会话首进程) ──┐                                │
  │  │  bash (pid=150, pgid=150, sid=150)│  ← 会话首进程 (session leader)│
  │  │  setsid() 创建了此会话             │     同时是控制终端的控制进程   │
  │  └──────────────────────────────────┘                                │
  │                                                                     │
  │  ┌── 进程组 PGID=200 (前台作业) ────┐                                │
  │  │  vim (pid=200, pgid=200, sid=150) │  ← 当前前台作业               │
  │  └──────────────────────────────────┘                                │
  │                                                                     │
  │  ┌── 进程组 PGID=300 (后台作业 1) ──┐                                │
  │  │  make (pid=300, pgid=300, sid=150)│  ← 后台运行                   │
  │  │  cc1 (pid=301, pgid=300, sid=150) │                               │
  │  └──────────────────────────────────┘                                │
  │                                                                     │
  │  ┌── 进程组 PGID=400 (后台作业 2) ──┐                                │
  │  │  find (pid=400, pgid=400, sid=150)│  ← 后台运行                   │
  │  └──────────────────────────────────┘                                │
  └──────────────────────────────────────────────────────────────────────┘

关键行为:
  setsid() → 创建新会话，调用进程成为会话首进程
             同时创建新进程组，调用进程成为组长
             脱离原来的控制终端

  终端关闭 (SSH 断连) → 内核向会话首进程发 SIGHUP
                       → bash 收到 SIGHUP 后向所有作业发 SIGHUP
                       → 所有前台/后台进程收到 SIGHUP 退出
                       → nohup / disown / tmux 可避免此行为

  守护进程 (daemon) 的标准做法:
    fork() → 父进程 exit → 子进程 setsid() → 脱离终端
    这就是为什么 daemon 的 SID == PID (自己是会话首进程)
```

#### 三个层次的嵌套关系

```
┌──── 会话 (Session, SID=150) ──────────────────────────────────────┐
│  控制终端: /dev/pts/0                                              │
│                                                                    │
│  ┌── 进程组 (PGID=150) ──┐  ┌── 进程组 (PGID=200) ─────────────┐ │
│  │ bash                   │  │ cat ──pipe── grep ──pipe── wc     │ │
│  │ (会话首进程)            │  │ (前台作业, 3个进程)               │ │
│  └────────────────────────┘  └───────────────────────────────────┘ │
│                                                                    │
│  ┌── 进程组 (PGID=300) ──────────────────────────────────────────┐│
│  │ make ── cc1 ── as ── ld                                       ││
│  │ (后台作业, 4个进程)                                            ││
│  │                                                               ││
│  │ 其中 make 可能有多个线程:                                      ││
│  │ ┌── 线程组 (TGID=300) ──────┐                                ││
│  │ │ 主线程 (pid=300, tid=300)  │                                ││
│  │ │ 工作线程 (pid=303, tid=303)│                                ││
│  │ │ 工作线程 (pid=304, tid=304)│                                ││
│  │ └───────────────────────────┘                                 ││
│  └───────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘

层次:  会话 ⊃ 进程组 ⊃ 进程 ⊃ 线程组(=进程的线程)
标识:  SID    PGID     PID    TGID(=PID) + 各线程 TID
```

#### task_struct 中的相关字段

```c
struct task_struct {
    pid_t pid;                    // 内核级线程 ID (用户态的 TID)
    pid_t tgid;                   // 线程组 ID (用户态的 PID)
    struct task_struct *group_leader; // 线程组组长

    // 以下通过 struct pid 的 tasks[] 链表关联:
    // PIDTYPE_PID  → 唯一标识此 task
    // PIDTYPE_TGID → 标识线程组 (线程组组长的 pid 结构)
    // PIDTYPE_PGID → 标识进程组 (进程组组长的 pid 结构)
    // PIDTYPE_SID  → 标识会话   (会话首进程的 pid 结构)

    struct signal_struct *signal;
    // signal->pgrp  → 进程组 ID (struct pid *)
    // signal->session → 会话 ID (struct pid *)
    // signal->leader → 是否是会话首进程
};

// 获取各标识符的接口:
// task_tgid_nr(task)    → 线程组 ID  (用户态 getpid())
// task_pgrp_nr(task)    → 进程组 ID  (用户态 getpgrp())
// task_session_nr(task) → 会话 ID    (用户态 getsid())
```

### PID 在多层命名空间中的映射

```
┌─────────────────────────────────────────────────────────────────────┐
│                    PID 命名空间多层映射                               │
│                                                                     │
│  进程 P 在容器中创建，struct pid 的 numbers[] 数组：                   │
│                                                                     │
│  numbers[0] = { nr=12345, ns=init_pid_ns  }  ← 宿主机看到 PID=12345│
│  numbers[1] = { nr=1,     ns=container_ns }  ← 容器内看到 PID=1     │
│                                                                     │
│  level = 1 (最深层级)                                                │
│                                                                     │
│  API:                                                               │
│    pid_nr(pid)      → 12345  (全局 ID, numbers[0].nr)               │
│    pid_vnr(pid)     → 取决于调用者所在命名空间                         │
│    pid_nr_ns(pid,ns)→ 取指定命名空间中的 ID                           │
└─────────────────────────────────────────────────────────────────────┘
```

### PID 分配与查找

```
分配：alloc_pid(ns, set_tid, set_tid_size)
  → 为每一层命名空间（从 level 到 0）分配一个唯一的 nr
  → 每层使用 idr/位图 分配
  → 注册到 pid_hash 哈希表

查找：
  find_pid_ns(nr, ns)  → 从哈希表中按 (nr, ns) 查找 struct pid
  find_vpid(nr)        → find_pid_ns(nr, current 的 pid_ns)
  pid_task(pid, type)  → 从 struct pid 的 tasks[type] 链表取第一个 task
```

---

## 2.4 进程关系

```
                    real_parent
  init(1) ◄──────────────────── bash(100)
    │                              │
    │ children                     │ children
    ▼                              ▼
  bash(100) ─── sibling ──── sshd(200)
    │
    │ children
    ▼
  vim(300) ─── sibling ──── grep(301)

  group_leader: 线程组组长指针
  thread_group: 同线程组内的线程链表
```

---

## 2.5 进程创建与装载（基于 Linux 6.1 重写）

### 2.5.1 创建进程

#### 系统调用入口（6.1 变化：统一到 kernel_clone）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    进程创建的系统调用入口                              │
│                                                                     │
│  用户态                         内核态                               │
│  ──────                         ──────                               │
│  fork()    ──────────────►  sys_fork()                               │
│  vfork()   ──────────────►  sys_vfork()                              │
│  clone()   ──────────────►  sys_clone()     ───► kernel_clone()      │
│  clone3()  ──────────────►  sys_clone3()    ───┘       │             │
│                                                        │             │
│  kernel_thread() ──────────────────────────────────────┘             │
│  (内核线程创建)                                                      │
│                                                                     │
│  注意：Linux 6.1 中 _do_fork() 已更名为 kernel_clone()               │
│  所有入口统一填充 struct kernel_clone_args 后调用 kernel_clone()       │
└─────────────────────────────────────────────────────────────────────┘
```

#### kernel_clone() 主流程

```
kernel_clone(args)
│
├─ 1. 确定 ptrace 事件类型（FORK/VFORK/CLONE）
│
├─ 2. p = copy_process(NULL, trace, NUMA_NO_NODE, args)
│     │
│     │  ══════════════════════════════════════════════
│     │  copy_process() 是创建进程的核心函数
│     │  ══════════════════════════════════════════════
│     │
│     ├─ (a) 参数合法性检查
│     │     - CLONE_NEWNS 与 CLONE_FS 互斥
│     │     - CLONE_THREAD 必须同时 CLONE_SIGHAND
│     │     - CLONE_SIGHAND 必须同时 CLONE_VM
│     │
│     ├─ (b) dup_task_struct(current, node)
│     │     - 分配新的 task_struct
│     │     - 分配新的内核栈
│     │     - 复制当前进程的 task_struct 内容
│     │     - 设置 stack 指针
│     │
│     ├─ (c) 资源限制检查
│     │     - RLIMIT_NPROC 进程数限制
│     │     - 用户命名空间计数
│     │
│     ├─ (d) 安全模块检查
│     │     - security_task_alloc()
│     │
│     ├─ (e) 调度器初始化
│     │     - sched_fork(clone_flags, p)
│     │       设置调度策略、优先级、vruntime
│     │       将新进程设为 TASK_NEW 状态
│     │
│     ├─ (f) 逐项复制/共享父进程资源
│     │     ┌──────────────────────────────────────────┐
│     │     │  copy_creds()      - 安全凭证(uid/gid/cap)│
│     │     │  copy_semundo()    - System V 信号量      │
│     │     │  copy_files()      - 文件描述符表         │
│     │     │  copy_fs()         - 文件系统信息         │
│     │     │  copy_sighand()    - 信号处理函数         │
│     │     │  copy_signal()     - 信号描述符           │
│     │     │  copy_mm()         - 内存描述符（地址空间）│
│     │     │  copy_namespaces() - 命名空间             │
│     │     │  copy_io()         - I/O 上下文           │
│     │     │  copy_thread()     - 体系结构相关线程状态  │
│     │     └──────────────────────────────────────────┘
│     │     每个 copy_xxx 根据 clone_flags 决定：
│     │       CLONE_XXX 置位 → 共享（引用计数+1）
│     │       CLONE_XXX 清零 → 复制（创建新副本）
│     │
│     ├─ (g) 分配 PID
│     │     - pid = alloc_pid(ns, set_tid, set_tid_size)
│     │     - p->pid = pid_nr(pid)
│     │     - 如果是线程组组长：p->tgid = p->pid
│     │     - 如果是子线程：    p->tgid = current->tgid
│     │
│     ├─ (h) 建立进程关系（持 tasklist_lock 写锁）
│     │     - 设置 real_parent 和 parent
│     │     - init_task_pid(p, PIDTYPE_PID, pid)
│     │     - 如果是线程组组长（fork/vfork）：
│     │         init_task_pid(p, PIDTYPE_TGID, pid)
│     │         init_task_pid(p, PIDTYPE_PGID, ...)
│     │         init_task_pid(p, PIDTYPE_SID, ...)
│     │         attach_pid(p, PIDTYPE_TGID/PGID/SID)
│     │         加入父进程的 children 链表
│     │         加入全局进程链表 init_task.tasks
│     │     - 如果是子线程（CLONE_THREAD）：
│     │         加入 group_leader 的 thread_group 链表
│     │         signal->nr_threads++
│     │     - attach_pid(p, PIDTYPE_PID)
│     │
│     └─ (i) 返回新的 task_struct *p
│
├─ 3. trace_sched_process_fork(current, p)
│
├─ 4. 获取新进程的 PID 数值
│     - pid = get_task_pid(p, PIDTYPE_PID)
│     - nr = pid_vnr(pid)
│
├─ 5. 如果 CLONE_PARENT_SETTID：写回用户态 parent_tid
│
├─ 6. wake_up_new_task(p)
│     - 将新进程加入运行队列
│     - 设置为 TASK_RUNNING
│     - 检查是否能抢占当前进程
│
├─ 7. 如果 CLONE_VFORK：wait_for_vfork_done()
│     - 父进程阻塞，等待子进程 exec 或 exit
│
└─ 8. 返回子进程的 PID 给父进程
```

#### fork/clone/vfork 的区别

| 系统调用 | 地址空间 | 文件描述符 | 信号处理 | 父进程行为 | 典型用途 |
|---|---|---|---|---|---|
| fork | 复制（COW） | 复制 | 复制 | 立即返回 | 创建子进程 |
| vfork | **共享** | 复制 | 复制 | 阻塞等待子进程 exec/exit | 快速 exec |
| clone(CLONE_VM) | **共享** | 可选 | 可选 | 立即返回 | 创建线程 |
| clone3 | 同 clone | 同 clone | 同 clone | 同 clone | clone 的扩展版本 |

### 2.5.2 程序装载（execve）

```
execve("./program", argv, envp)
│
├─ 1. sys_execve()
│     └─ do_execveat_common(AT_FDCWD, filename, argv, envp, 0)
│
├─ 2. 准备 linux_binprm 结构体
│     ├─ 分配 bprm = kzalloc(sizeof(*bprm))
│     ├─ 打开可执行文件：bprm->file = do_open_execat()
│     ├─ 读取文件头部 256 字节到 bprm->buf[]（BINPRM_BUF_SIZE=256）
│     ├─ 准备参数和环境变量页
│     └─ 安全检查：security_bprm_creds_for_exec()
│
├─ 3. bprm_execve(bprm, fd, filename, flags)
│     ├─ exec_binprm()
│     │   └─ search_binary_handler(bprm)
│     │       │
│     │       │  遍历 formats 链表，尝试每种格式处理器：
│     │       │
│     │       ├─ ELF 格式 → load_elf_binary()
│     │       │   ├─ 解析 ELF header 和 Program header
│     │       │   ├─ 检查是否需要动态链接器（PT_INTERP 段）
│     │       │   ├─ begin_new_exec() ← 关键：不可逆转的分界点
│     │       │   │   ├─ exec_mmap() → 替换进程的地址空间
│     │       │   │   │   旧的 mm_struct 被销毁
│     │       │   │   │   创建全新的 mm_struct
│     │       │   │   ├─ 刷新信号处理函数为默认值
│     │       │   │   ├─ 关闭标记为 CLOEXEC 的文件描述符
│     │       │   │   └─ set_task_comm() 设置进程名
│     │       │   ├─ setup_new_exec() → 设置 ASLR 等
│     │       │   ├─ 按 PT_LOAD 段映射 ELF 各个段到用户空间
│     │       │   │   ├─ 代码段(.text)：只读+可执行
│     │       │   │   ├─ 数据段(.data)：读写
│     │       │   │   └─ BSS 段：读写，零初始化
│     │       │   ├─ 设置 brk（堆起始地址）
│     │       │   ├─ 如果需要动态链接器：
│     │       │   │   └─ 加载 ld-linux.so，入口点设为链接器入口
│     │       │   ├─ 创建用户栈，拷贝 argv/envp/auxv
│     │       │   ├─ install_exec_creds() 设置新的安全凭证
│     │       │   └─ start_thread() 设置用户态寄存器
│     │       │       ├─ PC = ELF entry（或动态链接器入口）
│     │       │       └─ SP = 新栈顶
│     │       │
│     │       ├─ 脚本格式 → load_script()
│     │       │   解析 #! 行，递归调用 search_binary_handler
│     │       │
│     │       └─ 其他格式（misc, a.out...）
│     │
│     └─ 返回到用户态时，开始执行新程序
│
└─ 注意：execve 成功后不返回（地址空间已被替换）
         execve 失败则返回错误码，原进程继续运行
```

#### exec 前后进程状态对比

```
                exec 之前                        exec 之后
            ┌──────────────────┐            ┌──────────────────┐
  PID       │      不变        │            │      不变        │
  TGID      │      不变        │            │      不变        │
  PPID      │      不变        │            │      不变        │
  地址空间   │ 旧程序的 mm     │    ──►     │ 新程序的 mm     │
  代码       │ 旧程序代码      │    ──►     │ 新程序代码      │
  打开文件   │ 继承（非CLOEXEC）│            │ 保留非CLOEXEC   │
  信号处理   │ 自定义处理函数   │    ──►     │ 全部重置为默认   │
  内核栈     │      不变        │            │      不变        │
  调度策略   │      不变        │            │      不变        │
            └──────────────────┘            └──────────────────┘
```

---

## 2.6 进程退出

- `exit_group`：终止整个线程组（用户态 exit() 实际调用此接口）
- `exit`/`do_exit`：终止单个线程
- `waitpid`/`wait4` → `do_wait()`：父进程回收僵尸子进程
- 孤儿进程被 init 进程（或 subreaper）收养

## 2.7 进程状态

| 状态 | ps | 说明 |
|---|---|---|
| TASK_RUNNING | R | 就绪或运行中 |
| TASK_INTERRUPTIBLE | S | 可被信号唤醒的睡眠 |
| TASK_UNINTERRUPTIBLE | D | 不可中断的睡眠 |
| TASK_KILLABLE | D | UNINTERRUPTIBLE + 可被 SIGKILL 杀 |
| TASK_IDLE | I | UNINTERRUPTIBLE + TASK_NOLOAD，不计入 loadavg（内核线程空闲等待） |
| TASK_STOPPED | T | 被 SIGSTOP 停止 |
| TASK_TRACED | t | 被 ptrace 跟踪 |
| EXIT_ZOMBIE | Z | 僵尸，等待 wait() |
| EXIT_DEAD | X | 最终死亡 |

---

## 2.8 进程调度（基于 Linux 6.1 重写）

### 2.8.1 调度类层次结构

Linux 6.1 的调度类以链表形式组织，优先级从高到低：

```
┌─────────────────────────────────────────────────────────────────────┐
│                      调度类优先级链（高 → 低）                       │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  │  stop     │───►│ deadline │───►│    rt    │───►│   fair   │───►│   idle   │
│  │ sched_class│   │sched_class│   │sched_class│   │sched_class│   │sched_class│
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
│  停机类          限期调度类       实时调度类       完全公平调度类    空闲调度类
│  (内部使用)      SCHED_DEADLINE   SCHED_FIFO      SCHED_NORMAL    (CPU空闲时)
│                                  SCHED_RR        SCHED_BATCH
│                                                  SCHED_IDLE
│                                                                     │
│  源码文件:                                                           │
│  stop_task.c    deadline.c       rt.c            fair.c            idle.c
└─────────────────────────────────────────────────────────────────────┘
```

### 2.8.2 各调度类详解

#### (1) Stop 调度类（最高优先级，内部使用）

- **用途**：不面向用户，仅内核内部使用
- **典型用户**：CPU 停机任务（migration/N 线程），用于：
  - 进程迁移（active load balance）
  - CPU 热插拔
  - RCU 同步
- **特点**：不可被任何其他任务抢占

#### (2) Deadline 调度类（SCHED_DEADLINE）

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SCHED_DEADLINE 参数模型                          │
│                                                                     │
│  ◄───────────────── period (周期) ─────────────────►                │
│  ◄── runtime ──►                                                    │
│  ┌──────────────┐                              ┌──────────────┐    │
│  │██████████████│                              │██████████████│    │
│  └──────────────┘                              └──────────────┘    │
│  │◄── deadline ──────────────────────────────►│                    │
│  t=0            t=runtime                    t=deadline       t=period
│                                                                     │
│  约束：runtime ≤ deadline ≤ period                                   │
│  调度算法：EDF (Earliest Deadline First)                             │
│  运行队列：红黑树，按绝对 deadline 排序                                │
│  准入控制：Σ(runtime_i / period_i) ≤ CPU 总容量                      │
└─────────────────────────────────────────────────────────────────────┘
```

- **调度算法**：EDF（最早截止期限优先）+ CBS（恒定带宽服务器）
- **关键数据结构**：`struct sched_dl_entity`，包含 `dl_runtime`、`dl_deadline`、`dl_period`
- **带宽回收**：6.1 支持 GRUB（Greedy Reclamation of Unused Bandwidth），空闲带宽可被其他 DL 任务使用
- **与 RT 的区别**：DL 有明确的时间约束和准入控制，更适合硬实时系统

#### (3) RT 调度类（SCHED_FIFO / SCHED_RR）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    RT 运行队列结构                                    │
│                                                                     │
│  struct rt_rq                                                       │
│  ┌────────────────────────────────────────────┐                     │
│  │ active                                     │                     │
│  │ ┌────────────────────────────┐              │                     │
│  │ │ bitmap[0]: 1100...0        │ ← 位图快速找最高优先级             │
│  │ │ bitmap[1]: 0010...0        │                                   │
│  │ │ ...                        │                                   │
│  │ └────────────────────────────┘                                   │
│  │ queue[0]:  task_A → task_B   │ ← 内部 prio=0 (用户 rt_priority=99, 最高)│
│  │ queue[1]:  task_C            │ ← 内部 prio=1 (用户 rt_priority=98)    │
│  │ ...                          │                                   │
│  │ queue[98]: (空)              │ ← 内部 prio=98 (用户 rt_priority=1, 最低)│
│  └────────────────────────────────────────────┘                     │
│                                                                     │
│  SCHED_FIFO：同优先级内先进先出，不会被同优先级抢占                    │
│  SCHED_RR：  同优先级内轮转调度，有时间片（默认 100ms）               │
│             时间片用完后放到同优先级队列尾部                           │
└─────────────────────────────────────────────────────────────────────┘
```

- **优先级范围**：用户态 rt_priority 1~99（数值越大优先级越高），内核内部映射为 prio = 99 - rt_priority（prio 越小优先级越高）
- **选择算法**：位图扫描，O(1) 找到最高优先级的非空队列
- **RT 带宽限制**：`/proc/sys/kernel/sched_rt_runtime_us` (默认 950000) / `sched_rt_period_us` (默认 1000000)，防止 RT 任务饿死普通进程

#### (4) CFS 调度类（SCHED_NORMAL / SCHED_BATCH / SCHED_IDLE）

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CFS 运行队列与红黑树                             │
│                                                                     │
│  struct cfs_rq                                                      │
│  ┌──────────────────────────────────────────────┐                   │
│  │ min_vruntime = 1000                          │                   │
│  │ nr_running = 5                               │                   │
│  │ load.weight = 总权重                          │                   │
│  │                                              │                   │
│  │     红黑树 (tasks_timeline)                   │                   │
│  │              ┌───┐                            │                   │
│  │              │1050│                           │                   │
│  │             ╱     ╲                           │                   │
│  │         ┌───┐     ┌───┐                       │                   │
│  │         │1020│    │1080│                       │                   │
│  │        ╱     ╲       ╲                        │                   │
│  │    ┌───┐    ┌───┐   ┌───┐                     │                   │
│  │    │1005│   │1030│   │1100│                    │                   │
│  │    └───┘    └───┘   └───┘                     │                   │
│  │     ▲                                         │                   │
│  │     └── rb_leftmost (vruntime 最小 = 下一个运行)                   │
│  └──────────────────────────────────────────────┘                   │
│                                                                     │
│  vruntime 计算：                                                     │
│    vruntime += delta_exec × (NICE_0_LOAD / se.load.weight)          │
│                                                                     │
│  nice 0 的权重 = 1024 (NICE_0_LOAD)                                 │
│  nice -1 → 权重 1277, vruntime 增长慢 → 更多 CPU 时间               │
│  nice +1 → 权重  820, vruntime 增长快 → 更少 CPU 时间               │
│  相邻 nice 级 CPU 时间差约 10%                                       │
└─────────────────────────────────────────────────────────────────────┘
```

- **时间片计算**：CFS 没有固定时间片，而是根据进程数量和权重动态分配
  - `sched_latency`（默认 6ms × log2(nr_cpus)）：所有进程至少运行一轮的目标周期
  - 每个进程的理想运行时间 = `sched_latency × (进程权重 / 总权重)`
- **新进程/唤醒进程**：vruntime 设为 `max(自身vruntime, min_vruntime - sched_latency/2)`，防止饿死老进程又不让新进程等太久

#### (5) Idle 调度类

- 仅用于**per-CPU idle 线程**（swapper/N），当没有任何可运行任务时执行
- idle 线程执行 `cpu_idle_loop()`，执行 ARM64 WFI（Wait For Interrupt）指令进入低功耗状态
- 永远不会被放入运行队列

### 2.8.3 调度核心流程（__schedule）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    __schedule() 核心流程                              │
│                    (kernel/sched/core.c)                              │
│                                                                     │
│  __schedule(sched_mode)                                              │
│  │                                                                   │
│  ├─ 1. cpu = smp_processor_id()                                     │
│  │      rq = cpu_rq(cpu)                                            │
│  │      prev = rq->curr                                             │
│  │                                                                   │
│  ├─ 2. local_irq_disable()     关闭本地中断                          │
│  │      rq_lock(rq, &rf)       获取运行队列锁                       │
│  │      update_rq_clock(rq)    更新运行队列时钟                     │
│  │                                                                   │
│  ├─ 3. 处理 prev 的状态                                              │
│  │      if (prev 不是 TASK_RUNNING 且非抢占模式) {                    │
│  │          if (有待处理信号)                                        │
│  │              设回 TASK_RUNNING                                    │
│  │          else                                                    │
│  │              deactivate_task(rq, prev)  从运行队列移除            │
│  │      }                                                           │
│  │                                                                   │
│  ├─ 4. next = pick_next_task(rq, prev, &rf)                        │
│  │      │                                                           │
│  │      │  依次查询调度类（优先级高→低）：                            │
│  │      │  stop → deadline → rt → fair → idle                       │
│  │      │                                                           │
│  │      │  优化路径：如果所有可运行进程都是 CFS 类                    │
│  │      │  (__pick_next_task 中的快速路径)                            │
│  │      │  直接调用 fair_class->pick_next_task()                     │
│  │      │  跳过 stop/dl/rt 的检查                                   │
│  │      │                                                           │
│  │      └─ 返回 next（下一个要运行的 task）                          │
│  │                                                                   │
│  ├─ 5. clear_tsk_need_resched(prev)                                 │
│  │                                                                   │
│  ├─ 6. if (prev != next) {     需要切换                              │
│  │      │                                                           │
│  │      ├─ rq->curr = next                                          │
│  │      ├─ rq->nr_switches++                                        │
│  │      │                                                           │
│  │      └─ context_switch(rq, prev, next, &rf)                      │
│  │          │                                                       │
│  │          ├─ prepare_task_switch()                                 │
│  │          │                                                       │
│  │          ├─ 地址空间切换：                                        │
│  │          │   if (next 是内核线程)                                 │
│  │          │       借用 prev 的 active_mm                          │
│  │          │   else                                                │
│  │          │       switch_mm_irqs_off(prev->active_mm, next->mm)   │
│  │          │       → 切换页表基地址寄存器 TTBR0_EL1                │
│  │          │       → 刷新 ASID（地址空间标识符）避免全量 TLB flush  │
│  │          │                                                       │
│  │          └─ switch_to(prev, next, prev)                          │
│  │              → ARM64 实现：cpu_switch_to()                       │
│  │                (arch/arm64/kernel/entry.S)                       │
│  │                保存 prev 的: x19-x28, FP(x29), LR(x30), SP     │
│  │                恢复 next 的: x19-x28, FP(x29), LR(x30), SP     │
│  │                切换 TPIDR_EL0 (用户态 TLS 寄存器)               │
│  │                切换 SP_EL0 (内核 current 指针，6.1 ARM64       │
│  │                  通过 mrs x0, sp_el0 获取 current)             │
│  │                保存/恢复 FPSIMD 状态（延迟切换）                 │
│  │                                                                   │
│  └─ } else { 无需切换，解锁返回 }                                    │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.8.4 调度时机

```
┌─────────────────────────────────────────────────────────────────────┐
│                       三种调度时机                                    │
│                                                                     │
│  (1) 主动调度 (Voluntary)                                           │
│      进程调用 schedule() 主动让出 CPU                                │
│      - mutex_lock() 拿不到锁时                                      │
│      - wait_event() 等待条件时                                       │
│      - msleep() / schedule_timeout()                                │
│      - cond_resched() 显式让步                                       │
│                                                                     │
│  (2) 周期调度 (Periodic)                                            │
│      时钟中断触发 scheduler_tick()                                    │
│      → 更新当前进程的运行时间统计                                      │
│      → 调用当前调度类的 task_tick()                                   │
│        CFS: 检查 vruntime 是否超出理想运行时间                        │
│        RT:  SCHED_RR 检查时间片是否用完                               │
│        DL:  检查是否超出 runtime 预算                                 │
│      → 如果需要调度：set_tsk_need_resched(curr)                     │
│      → trigger_load_balance(rq) 触发负载均衡 softirq                │
│                                                                     │
│  (3) 唤醒抢占 (Wakeup Preemption)                                   │
│      try_to_wake_up(p) 唤醒进程时                                    │
│      → 调用 check_preempt_curr(rq, p, flags)                       │
│      → 如果被唤醒的进程优先级更高                                      │
│        set_tsk_need_resched(rq->curr)                               │
│      → 调度发生在：                                                  │
│        CONFIG_PREEMPTION=y: 最近的 preempt_enable() 或中断返回       │
│        CONFIG_PREEMPTION=n: 系统调用返回或 cond_resched()            │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.8.5 优先级体系总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Linux 优先级数值体系                              │
│                                                                     │
│   内核优先级值     调度类         用户态接口                          │
│  ┌──────────────────────────────────────────────────┐               │
│  │   -1          Deadline        sched_setattr()    │ ← 最高       │
│  │                                                  │               │
│  │    0          Stop            (内部使用)          │               │
│  │                                                  │               │
│  │   1 ~ 99      RT              sched_setscheduler()│              │
│  │               (FIFO/RR)      chrt -f/-r          │               │
│  │               99=最高实时     1=最低实时           │               │
│  │                                                  │               │
│  │ 100 ~ 139     CFS             nice -20 ~ +19     │               │
│  │               (NORMAL/BATCH)  renice              │               │
│  │               100=nice -20    139=nice +19        │               │
│  │               (最高普通)      (最低普通)           │               │
│  │                                                  │               │
│  │  MAX          Idle            (内部 idle 线程)    │ ← 最低       │
│  └──────────────────────────────────────────────────┘               │
│                                                                     │
│  task_struct 中的优先级字段：                                        │
│    prio          → 动态优先级（调度器实际使用）                       │
│    static_prio   → 静态优先级（nice 值映射，100~139）                │
│    normal_prio   → 归一化优先级（考虑调度策略）                       │
│    rt_priority   → 实时优先级（1~99，仅 RT 进程有效）                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2.9 SMP 调度管理（基于 Linux 6.1 重写）

### 2.9.1 调度域层次结构

```
┌─────────────────────────────────────────────────────────────────────┐
│                    调度域层次结构 (Scheduling Domains)                │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────┐      │
│  │  SD_NUMA (NUMA 域，如有多个 NUMA 节点)                     │      │
│  │  跨 NUMA 节点负载均衡，均衡间隔最长                         │      │
│  │  ┌─────────────────────────┬─────────────────────────┐    │      │
│  │  │  SD_MC (多核域)         │  SD_MC                   │    │      │
│  │  │  同一 cluster 内均衡     │  同一 cluster 内均衡      │    │      │
│  │  │  ┌───┬───┬───┬───┐     │  ┌───┬───┬───┬───┐      │    │      │
│  │  │  │C0 │C1 │C2 │C3 │     │  │C4 │C5 │C6 │C7 │      │    │      │
│  │  │  └───┴───┴───┴───┘     │  └───┴───┴───┴───┘      │    │      │
│  │  │    Cluster 0            │    Cluster 1             │    │      │
│  │  └─────────────────────────┴─────────────────────────┘    │      │
│  └───────────────────────────────────────────────────────────┘      │
│                                                                     │
│  ARM64 典型配置（如 NVIDIA Thor）：                                   │
│    SD_MC（多核域，cluster 内均衡）→ SD_DIE/SD_NUMA（跨 cluster）     │
│    ARM64 通常没有 SD_SMT 层级（无硬件超线程）                         │
│                                                                     │
│  均衡频率：MC > DIE > NUMA（越近均衡越频繁）                          │
│  迁移代价：MC < DIE < NUMA（越近迁移代价越低）                        │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.9.2 CFS 负载均衡

#### 负载均衡触发时机

```
┌─────────────────────────────────────────────────────────────────────┐
│                  CFS 负载均衡触发路径                                 │
│                                                                     │
│  (1) 周期性均衡（tick 驱动）                                         │
│      scheduler_tick()                                               │
│        → trigger_load_balance(rq)                                   │
│          → raise_softirq(SCHED_SOFTIRQ)                            │
│            → run_rebalance_domains()    [softirq 上下文]            │
│              → rebalance_domains(rq, idle)                         │
│                → 遍历每层调度域 sd                                  │
│                  → 检查均衡间隔是否到期                              │
│                  → load_balance(cpu, rq, sd, idle)                 │
│                                                                     │
│  (2) Idle 均衡（CPU 空闲时主动拉取）                                 │
│      pick_next_task_fair()                                          │
│        → newidle_balance()                                          │
│          → load_balance(cpu, rq, sd, CPU_NEWLY_IDLE)               │
│      CPU 空闲时主动从忙碌 CPU 拉取任务                               │
│                                                                     │
│  (3) NOHZ Idle 均衡（tickless CPU 的均衡）                          │
│      nohz_balancer_kick(rq)                                        │
│        → 唤醒一个 idle CPU 来做负载均衡                              │
│        → 确保 nohz_full CPU 不被遗忘                                │
│                                                                     │
│  (4) Fork/Exec 均衡                                                 │
│      wake_up_new_task() / sched_exec()                              │
│        → select_task_rq()                                           │
│        → 为新进程/exec 后的进程选择最优 CPU                          │
└─────────────────────────────────────────────────────────────────────┘
```

#### load_balance() 核心流程

```
load_balance(this_cpu, this_rq, sd, idle)
│
├─ 1. find_busiest_group(sd, this_cpu, ...)
│     ├─ 遍历调度域中的每个调度组（sched_group）
│     ├─ 计算每个组的负载统计：
│     │   avg_load, group_load, group_capacity, group_util
│     ├─ 判断组类型：
│     │   group_has_spare     → 有空闲容量
│     │   group_fully_busy    → 满负载但无过载
│     │   group_overloaded    → 过载
│     │   group_imbalanced    → 不平衡
│     │   group_misfit_task   → 任务不匹配（big.LITTLE）
│     └─ 找到最忙的组，计算需要迁移的负载量 imbalance
│
├─ 2. find_busiest_queue(sd, group, ...)
│     └─ 在最忙组内找到最忙的 CPU 运行队列
│
├─ 3. detach_tasks(busiest_rq, ...)
│     ├─ 从最忙队列中选择合适的任务
│     │   - 跳过正在运行的任务
│     │   - 跳过不允许迁移到 this_cpu 的任务（亲和性）
│     │   - 跳过缓存热度高的任务（cache-hot）
│     └─ 将选中任务从 busiest_rq 的运行队列移除
│
├─ 4. attach_tasks(this_rq, ...)
│     └─ 将任务加入 this_rq 的运行队列
│
└─ 5. 如果迁移失败（如所有任务都 pinned），启动 active balancing
      → 向 busiest_cpu 发送 stop_one_cpu_nowait()
      → 在 busiest_cpu 上执行 active_load_balance_cpu_stop()
      → 使用 CPU stopper 线程强制迁移一个任务
```

#### PELT 负载追踪（Per-Entity Load Tracking）

```
┌─────────────────────────────────────────────────────────────────────┐
│                  PELT 负载计算原理                                    │
│                                                                     │
│  时间划分为 1024μs 的周期，每个周期的贡献按指数衰减：                  │
│                                                                     │
│  load = Σ (contribution_i × decay_factor^i)                         │
│                                                                     │
│  decay_factor ≈ 0.978（半衰期约 32ms）                               │
│                                                                     │
│  三个维度的追踪：                                                    │
│  ┌──────────┬──────────────────────────────────────────┐            │
│  │ 指标      │ 含义                                    │            │
│  ├──────────┼──────────────────────────────────────────┤            │
│  │ load_avg │ 权重化的负载：反映任务优先级的影响       │            │
│  │ util_avg │ 利用率：实际使用的 CPU 容量占比          │            │
│  │ runnable_avg│ 可运行时间占比（含等待运行队列时间）    │            │
│  └──────────┴──────────────────────────────────────────┘            │
│                                                                     │
│  追踪层次：                                                         │
│    sched_entity → task_group → cfs_rq → rq                         │
│    每一层都有独立的 PELT 统计                                        │
│                                                                     │
│  源码：kernel/sched/pelt.c                                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.9.3 RT 调度类的负载均衡

```
┌─────────────────────────────────────────────────────────────────────┐
│                  RT 负载均衡策略                                      │
│                                                                     │
│  RT 不使用 CFS 的周期性负载均衡，而是使用推拉模型：                    │
│                                                                     │
│  (1) Push（推）：RT 任务入队时                                       │
│      当一个 CPU 有多个 RT 任务时                                      │
│      → push_rt_tasks()                                              │
│      → 将次高优先级 RT 任务推到其他空闲/低优先级 CPU                  │
│      → 使用 cpupri 位图快速找到目标 CPU                              │
│                                                                     │
│  (2) Pull（拉）：当前 RT 任务被移除时                                 │
│      当前 CPU 没有 RT 任务可运行                                      │
│      → pull_rt_task()                                               │
│      → 从其他 CPU 拉取可迁移的 RT 任务                               │
│                                                                     │
│  (3) CPU 优先级追踪（cpupri）                                        │
│      每个 CPU 维护当前最高 RT 优先级                                  │
│      → cpupri_set() 更新                                            │
│      → cpupri_find() O(1) 查找适合的目标 CPU                        │
│                                                                     │
│  源码：kernel/sched/rt.c, kernel/sched/cpupri.c                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.9.4 Deadline 调度类的负载均衡

```
┌─────────────────────────────────────────────────────────────────────┐
│                  DL 负载均衡策略                                      │
│                                                                     │
│  类似 RT，使用推拉模型：                                              │
│                                                                     │
│  (1) Push：push_dl_tasks()                                          │
│      将非最紧迫的 DL 任务推到其他 CPU                                 │
│      优先推到 deadline 更宽松的 CPU                                   │
│                                                                     │
│  (2) Pull：pull_dl_task()                                           │
│      从其他 CPU 拉取 DL 任务                                         │
│                                                                     │
│  (3) CPU Deadline 追踪（cpudeadline）                                │
│      类似 cpupri，但使用最小堆                                       │
│      → cpudl_find() 找到 deadline 最宽松的 CPU                      │
│                                                                     │
│  (4) 准入控制                                                        │
│      dl_task_can_attach() 检查目标 CPU 是否有足够带宽                 │
│      全局约束：所有 CPU 的 DL 带宽总和不超过系统容量                   │
│                                                                     │
│  源码：kernel/sched/deadline.c, kernel/sched/cpudeadline.c          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.9.5 处理器隔离与亲和性

```
┌─────────────────────────────────────────────────────────────────────┐
│                  CPU 隔离机制                                        │
│                                                                     │
│  (1) isolcpus 内核参数                                               │
│      isolcpus=[flags,]cpulist                                       │
│      flags:                                                         │
│        domain  → 从调度域中移除，不参与负载均衡                       │
│        managed_irq → 由内核管理 IRQ 亲和性，不路由到隔离 CPU          │
│        nohz    → 从 nohz_full 角度隔离（已废弃，用 nohz_full=）     │
│                                                                     │
│      源码：kernel/sched/isolation.c                                  │
│                                                                     │
│  (2) cpuset (cgroup)                                                │
│      /sys/fs/cgroup/<group>/cpuset.cpus                             │
│      运行时动态调整进程可使用的 CPU 集合                               │
│                                                                     │
│  (3) sched_setaffinity()                                            │
│      进程级别的 CPU 亲和性设置                                       │
│      → 修改 task->cpus_mask                                        │
│      → 如果进程当前不在允许的 CPU 上，触发迁移                       │
│                                                                     │
│  三者的层次关系：                                                    │
│  ┌──────────────────────────────────────────────┐                   │
│  │ isolcpus（启动时固定，最底层硬隔离）           │                   │
│  │   ┌──────────────────────────────────┐        │                   │
│  │   │ cpuset（cgroup 层面动态限制）     │        │                   │
│  │   │   ┌──────────────────────┐       │        │                   │
│  │   │   │ sched_setaffinity    │       │        │                   │
│  │   │   │ （进程级别精细控制）   │       │        │                   │
│  │   │   └──────────────────────┘       │        │                   │
│  │   └──────────────────────────────────┘        │                   │
│  └──────────────────────────────────────────────┘                   │
│  实际允许的 CPU = isolcpus允许 ∩ cpuset允许 ∩ affinity允许           │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.9.6 进程迁移

- **迁移线程**：每个 CPU 一个 `migration/N` 内核线程（stop 调度类）
- **迁移触发**：
  - `sched_setaffinity()` 设置亲和性后，当前 CPU 不再允许 → 触发迁移
  - 负载均衡 `active_load_balance` → 通过 CPU stopper 强制迁移
  - CPU 热插拔 → 被下线 CPU 的所有任务必须迁移
- **迁移机制**：通过 `stop_one_cpu()` 或 `stop_one_cpu_nowait()` 在目标 CPU 上执行迁移回调

---

## 2.10 进程的安全上下文

| 字段 | 说明 |
|---|---|
| uid/gid | 真实用户/组标识 |
| euid/egid | 有效用户/组标识（权限检查用） |
| suid/sgid | 保存的用户/组标识 |
| fsuid/fsgid | 文件系统用户/组标识 |
| cap_effective | 有效能力集 |
| cap_permitted | 允许能力集 |
| cap_inheritable | 可继承能力集 |

- set-user-ID 机制：可执行文件设置 SUID 位后，进程的 euid 变为文件所有者（如 passwd 命令）

---

## 全章知识脉络

```
第 2 章 进程管理
├── 基础概念
│   ├── 2.1 进程（task_struct、内核线程 vs 用户线程）
│   ├── 2.2 命名空间（8 种，含 5.6 引入的 time namespace）
│   ├── 2.3 进程标识符
│   │   ├── 四种 pid_type: PID / TGID(4.19引入) / PGID / SID
│   │   ├── struct pid：内核对 PID 的抽象，支持多层命名空间
│   │   └── PID 命名空间层次映射
│   └── 2.4 进程关系（父子、兄弟、线程组）
│
├── 生命周期
│   ├── 2.5 创建与装载
│   │   ├── kernel_clone()（6.1，替代 4.x 的 _do_fork）
│   │   │   └── copy_process()：复制/共享资源 + 分配PID + 建立关系
│   │   └── execve → bprm_execve → load_elf_binary
│   │       begin_new_exec() 是不可逆分界点
│   ├── 2.6 退出（exit_group / do_exit / wait）
│   └── 2.7 进程状态（R/S/D/I/T/Z 及 TASK_KILLABLE）
│
├── 调度系统
│   ├── 2.8 调度类
│   │   ├── stop    → 最高优先级，内部使用
│   │   ├── deadline → EDF+CBS，三参数模型(runtime/deadline/period)
│   │   ├── rt      → FIFO/RR，优先级位图 O(1)
│   │   ├── fair    → CFS，vruntime 红黑树
│   │   └── idle    → CPU 空闲时运行
│   ├── 调度核心：__schedule() → pick_next_task → context_switch
│   ├── 调度时机：主动/周期/唤醒抢占
│   └── 优先级体系：DL(-1) > stop(0) > RT(1-99) > CFS(100-139) > idle
│
└── SMP 管理
    ├── 2.9.1 调度域层次：MC → DIE → NUMA（ARM64 无 SMT）
    ├── 2.9.2 CFS 负载均衡
    │   ├── 触发：tick/idle/nohz/fork
    │   ├── load_balance → find_busiest_group → detach/attach
    │   └── PELT 负载追踪（load_avg/util_avg/runnable_avg）
    ├── 2.9.3 RT 均衡：push/pull 模型 + cpupri
    ├── 2.9.4 DL 均衡：push/pull 模型 + cpudeadline 最小堆
    ├── 2.9.5 隔离：isolcpus ⊃ cpuset ⊃ sched_setaffinity
    └── 2.9.6 迁移：migration/N 线程 + CPU stopper
```
