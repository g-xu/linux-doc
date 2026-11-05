# 通知与事件机制

> 四个 B 级通知/事件模块：eventfd（内核到用户态的轻量事件通知）、eventpoll（epoll 内核侧集成）、kobject/uevent（设备模型到用户态的 uevent 通知）、fsnotify（文件系统事件通知框架）。

---

## eventfd — 内核到用户态的轻量事件通知

### 定位

**功能**：提供内核到用户态的轻量事件通知机制。eventfd 创建一个文件描述符，内核通过 `eventfd_signal()` 递增其内部计数器并唤醒等待者，用户态通过 `read()` 读取并重置计数器。它是异步 I/O 完成通知、中断转发到用户态的标准通道。

**实现方式**：`struct eventfd_ctx`（`fs/eventfd.c:30-44`）包含 `wait_queue_head_t wqh`（等待队列）、`__u64 count`（内部计数器）和 `flags`（`EFD_SEMAPHORE`、`EFD_NONBLOCK` 等）。`eventfd_signal()` 在 spinlock 保护下递增计数器并 `wake_up_locked_poll()` 唤醒等待者，可在原子上下文调用。`EFD_SEMAPHORE` 模式下 `read()` 每次只减一（信号量语义），否则清零。`eventfd_signal` 检查 `current->in_eventfd` 防止递归调用（`fs/eventfd.c:58-59`）。计数器上限 `ULLONG_MAX`，溢出时返回实际递增量。

**应用场景**：`eventfd_signal` 全树 63 处调用，`eventfd_ctx_fdget` 38 处。主要消费者包括：KVM irqfd（`virt/kvm/eventfd.c`，将中断转发到用户态）、VHOST（`drivers/vhost/vhost.c`，virtio 后端通知）、VFIO 设备（`drivers/vfio/`，设备中断通知）、VDPA（`drivers/vdpa/`）、USB gadget functionfs（`drivers/usb/gadget/function/f_fs.c`）、以及 habanalabs AI 加速器驱动。

### 核心结构体（`fs/eventfd.c:30-44`）

```c
struct eventfd_ctx {
    struct kref kref;
    wait_queue_head_t wqh;
    __u64 count;              // 内部计数器
    unsigned int flags;       // EFD_SEMAPHORE, EFD_NONBLOCK 等
    int id;
};
```

### 主要 API

头文件：`include/linux/eventfd.h`。

| API | 说明 | 备注 |
|-----|------|------|
| `eventfd_signal(ctx, n)` | 递增计数器 n 并唤醒等待者 | 可在原子上下文调用；返回实际递增值（溢出时小于 n） |
| `eventfd_signal_mask(ctx, n, mask)` | 同上，附加 poll mask | |
| `eventfd_ctx_fdget(fd)` | 通过 fd 获取 ctx | 增加引用计数 |
| `eventfd_ctx_fileget(file)` | 通过 file 获取 ctx | |
| `eventfd_ctx_put(ctx)` | 释放引用 | |
| `eventfd_fget(fd)` | 获取 eventfd 的 file 结构 | |
| `eventfd_ctx_remove_wait_queue(ctx, wait, cnt)` | 移除等待队列项 | |
| `eventfd_ctx_do_read(ctx, cnt)` | 内部读取（清零或减一） | EFD_SEMAPHORE 模式减一，否则清零 |

### 溢出行为

计数器上限 `ULLONG_MAX`。溢出时 `eventfd_signal` 返回实际递增量（小于请求量），`poll()` 报告 `EPOLLERR`。

### 重入保护

`eventfd_signal` 检查 `current->in_eventfd` 防止递归调用（`fs/eventfd.c:58-59`）。如果检测到重入，打印警告并返回 0。

### 使用规模

`eventfd_signal` 63 处调用，`eventfd_ctx_fdget` 38 处，`eventfd_ctx_put` 73 处。

---

## eventpoll — epoll 内核侧集成

### 定位

**功能**：实现 epoll 机制的内核侧。用户态通过 `epoll_create`、`epoll_ctl`、`epoll_wait` 系统调用管理监控的文件描述符集合。内核侧的集成点是各文件操作的 `poll` 回调——当文件状态变化时通过 wait queue 唤醒 epoll 等待者，epoll 自动将就绪的 fd 加入就绪链表。

**实现方式**：核心实现在 `fs/eventpoll.c`（约 2300 行）。`struct eventpoll` 包含红黑树管理的 epitem 集合、就绪链表和 wait queue。`struct epitem` 表示被监控的 fd，包含目标 file、event 配置、rbtree 节点和就绪链表节点。`struct eppoll_entry` 挂在目标文件的 wait queue 上。当设备/socket 状态变化时，驱动调用 `wake_up_locked_poll()` 或 `wake_up_poll()`，epoll 的回调函数 `ep_poll_callback` 自动将就绪的 epitem 加入就绪链表。用户态接口结构 `struct epoll_event`（`include/uapi/linux/eventpoll.h:83`）包含 `events`（`EPOLLIN`/`EPOLLOUT`/`EPOLLERR` 等）和 `data`。

**应用场景**：内核代码通常不直接调用 eventpoll API，集成通过标准的 `file_operations->poll` 回调实现。`fs/aio.c` 支持 aio ring buffer 的 epoll 监控，`fs/file_table.c` 在文件表操作中与 epoll 交互。核心实现在 `fs/eventpoll.c`，`fs/` 和 `include/linux/` 中共 46 处 epoll 相关引用。它是所有高性能网络服务器和事件驱动应用的内核基础。

### 核心结构体

定义在 `fs/eventpoll.c` 内部（非导出）：

- `struct eventpoll` — epoll 实例，包含 rbtree 管理的 epitem 集合、就绪链表、wait queue
- `struct epitem` — 被监控的 fd 项，包含目标 file、event 配置、rbtree 节点、就绪链表节点
- `struct eppoll_entry` — 挂在目标文件 wait queue 上的回调项

用户态接口结构（`include/uapi/linux/eventpoll.h:83`）：

```c
struct epoll_event {
    __u32 events;      // EPOLLIN, EPOLLOUT, EPOLLERR 等
    __u64 data;        // 用户数据
} __attribute__((__packed__));
```

### 内核集成方式

内核代码通常不直接调用 eventpoll API。集成通过标准的 `file_operations->poll` 回调实现：当设备/socket 状态变化时，驱动调用 `wake_up_locked_poll()` 或 `wake_up_poll()`，epoll 的回调函数 `ep_poll_callback` 自动将就绪的 epitem 加入就绪链表。

### 使用规模

`fs/` 和 `include/linux/` 中 46 处 epoll 相关引用。核心实现在 `fs/eventpoll.c`（约 2300 行）。

---

## uevent — 设备事件到用户态的通知

### 定位

**功能**：当内核对象（kobject）状态变化时（添加、移除、属性改变、驱动绑定/解绑），通过 uevent 机制向用户态发送环境变量格式的通知。udev/systemd-udevd 监听这些通知来执行设备配置（创建设备节点、加载驱动模块、设置权限等）。

**实现方式**：`lib/kobject_uevent.c` 实现。`kobject_uevent()` / `kobject_uevent_env()` 收集 kobject 的路径、子系统名、动作类型（`KOBJ_ADD`/`REMOVE`/`CHANGE`/`MOVE`/`ONLINE`/`OFFLINE`/`BIND`/`UNBIND`，定义在 `include/linux/kobject.h:53-61`）和额外环境变量，组装成 `struct kobj_uevent_env`。传输通过两种途径：Netlink socket（`NETLINK_KOBJECT_UEVENT` 组广播，默认方式）和用户态 helper（`/proc/sys/kernel/hotplug`，已废弃）。kset 的 `uevent_ops`（filter/name/uevent 回调）可以过滤、修饰或阻止特定 kobject 的 uevent 发送。

**应用场景**：`kobject_uevent` 全树 197 处调用，`kobject_uevent_env` 85 处。几乎所有设备模型代码都通过它通知用户态设备事件：`drivers/base/` 中的设备注册/注销、`block/genhd.c` 的块设备事件、`drivers/pci/` 的 PCI 热插拔、`drivers/md/dm.c` 的设备映射事件、`crypto/pcrypt.c` 的加密设备事件等。它是 Linux 设备自动配置的基础设施。

> kobject 基础设施本身（结构体、生命周期、kset 分组）见 `io-device/misc-kobject.md`。本节只讲 uevent 通知机制。

### 事件类型（`include/linux/kobject.h:53-61`）

```c
enum kobject_action {
    KOBJ_ADD,        // 对象添加
    KOBJ_REMOVE,     // 对象移除
    KOBJ_CHANGE,     // 属性变化
    KOBJ_MOVE,       // 移动
    KOBJ_ONLINE,     // 上线
    KOBJ_OFFLINE,    // 下线
    KOBJ_BIND,       // 驱动绑定
    KOBJ_UNBIND,     // 驱动解绑
};
```

### 核心结构体

`struct kobj_uevent_env`（`include/linux/kobject_uevent.h`）— 保存环境变量数组，传递给 netlink 广播。

### 主要 API

头文件：`include/linux/kobject.h`。实现：`lib/kobject_uevent.c`。

| API | 说明 | 备注 |
|-----|------|------|
| `kobject_uevent(kobj, action)` | 发送 uevent | 内部调用 `kobject_uevent_env`，无额外环境变量 |
| `kobject_uevent_env(kobj, action, envp)` | 发送 uevent 附带额外环境变量 | `lib/kobject_uevent.c:472` |
| `add_uevent_var(env, fmt, ...)` | 向 kobj_uevent_env 添加变量 | printf 格式 |

### 传输机制

uevent 通过两种途径发送到用户态：
1. **Netlink socket**（`CONFIG_UEVENT_HELPER` 关闭时的默认方式）— 广播到 `NETLINK_KOBJECT_UEVENT` 组
2. **用户态 helper**（`/proc/sys/kernel/hotplug`，已废弃）— 执行外部程序

### 使用规模

`kobject_uevent` 197 处调用，`kobject_uevent_env` 85 处调用。几乎所有设备模型代码（`drivers/base/`）都通过它通知用户态设备事件。

---

## fsnotify — 文件系统事件通知框架

### 定位

**功能**：内核文件系统事件通知的基础框架，inotify 和 fanotify 都构建在它之上。当文件系统对象（inode、dentry、挂载点）发生访问、修改、创建、删除等事件时，fsnotify 通知注册的监听者。它提供统一的事件分发机制，下层由 inotify（单目录监控）和 fanotify（全文件系统监控 + 权限控制）两个后端消费。

**实现方式**：核心实现在 `fs/notify/` 目录。`struct fsnotify_group` 表示监听组，包含操作回调、事件队列和配置。`struct fsnotify_mark` 附着在 inode/dentry/vfsmount 上，包含事件掩码。`fsnotify()`（`fs/notify/fsnotify.c`）是核心分发函数，遍历 inode/dentry/mount 上的 mark，将事件分发给匹配的 group。`fsnotify_inode_delete()` 在 inode 被驱逐时清除所有关联 mark。inotify 实现在 `fs/notify/inotify/`，fanotify 在 `fs/notify/fanotify/`——fanotify 额外支持 `FAN_OPEN_PERM` 等权限事件，可以允许或拒绝文件操作。

**应用场景**：`fsnotify_mark` 全树 118 处引用。VFS 层（`fs/inode.c`、`fs/file_table.c`、`fs/libfs.c`）在文件操作时调用 fsnotify 钩子。安全子系统（`security/selinux/hooks.c`、`security/security.c`）使用 fanotify 实现文件访问审计。`fs/debugfs/inode.c` 和 `drivers/android/binderfs.c` 等虚拟文件系统也集成 fsnotify。典型用户态应用包括文件管理器（inotify）、防病毒软件和备份系统（fanotify）。

### 核心文件

| 文件 | 职责 |
|------|------|
| `include/linux/fsnotify.h` | 内联包装函数（fsnotify_inode 等） |
| `include/linux/fsnotify_backend.h` | 后端 API 和结构体定义 |
| `fs/notify/fsnotify.c` | 核心事件分发 |
| `fs/notify/group.c` | fsnotify_group 管理 |
| `fs/notify/mark.c` | fsnotify_mark 管理 |
| `fs/notify/inotify/` | inotify 实现 |
| `fs/notify/fanotify/` | fanotify 实现 |

### 核心结构体

- `struct fsnotify_group` — 监听组，包含操作回调、事件队列、配置
- `struct fsnotify_mark` — 附着在 inode/dentry/vfsmount 上的监听标记，包含事件掩码
- `struct fsnotify_ops` — 后端操作回调（处理事件、释放组等）

### 主要 API

头文件：`include/linux/fsnotify.h`、`include/linux/fsnotify_backend.h`。

| API | 说明 | 备注 |
|-----|------|------|
| `fsnotify(inode, mask, ...)` | 核心事件分发函数 | 遍历 inode/dentry/mount 上的 mark |
| `fsnotify_inode(inode, mask)` | inode 事件通知 | 内联包装 |
| `fsnotify_inode_delete(inode)` | inode 被驱逐时清理 | 清除所有关联 mark |
| `fsnotify_add_inode_mark()` | 在 inode 上添加 mark | |
| `fsnotify_put_mark()` | 释放 mark 引用 | |
| `fsnotify_alloc_group()` | 创建监听组 | |
| `fsnotify_destroy_group()` | 销毁监听组 | |

### inotify vs fanotify

| | inotify | fanotify |
|---|---------|----------|
| 监控范围 | 单个目录 | 整个文件系统/挂载点 |
| 权限控制 | 无 | 有（`FAN_OPEN_PERM` 等可允许/拒绝操作） |
| 用户接口 | `inotify_add_watch` / `inotify_rm_watch` | `fanotify_init` / `fanotify_mark` |
| 典型用户 | 文件管理器、IDE 插件 | 防病毒软件、备份系统 |

### 使用规模

`fsnotify_mark` 118 处引用。inotify 实现在 `fs/notify/inotify/`（2 个 .c 文件），fanotify 在 `fs/notify/fanotify/`（2 个 .c 文件）。
