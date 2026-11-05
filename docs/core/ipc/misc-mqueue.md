# POSIX 消息队列（mqueue）

> B 级文档 · `ipc/mqueue.c`（1,754 行）· UAPI 头文件 `include/uapi/linux/mqueue.h`（56 行）

## 1 定位

**功能**：POSIX 消息队列提供进程间有序消息传递能力，通过 `mq_open`/`mq_send`/`mq_receive` 等系统调用实现。每条消息带有优先级，接收方可以按优先级顺序取出消息，支持定时发送/接收和异步通知。

**实现方式**：内核将每个消息队列实现为虚拟文件系统中的一个文件（挂载在 `/dev/mqueue`），队列内部使用红黑树（`struct rb_root msg_tree`）按优先级组织消息，同优先级的消息通过链表串联。通知机制基于 netlink socket，当消息到达空队列时通过注册的 AF_NETLINK socket 向用户空间发送通知。所有访问由 `info->lock`（spinlock）保护，唤醒使用 `wake_q` 框架避免优先级反转。

**应用场景**：用户空间程序通过 POSIX 消息队列 API 进行跨进程通信；内核中 `kernel/auditsc.c`（审计子系统）引用 mqueue 头文件用于审计通知，`init/init_task.c` 初始化 init 进程的 IPC 命名空间。`drivers/scsi/qla2xxx/qla_dbg.c` 等驱动调试代码也引用 mqueue 接口。全树共 12 个 C 文件直接引用 `mqueue.h`。

## 2 核心数据结构

定义在 `ipc/mqueue.c` 内部（无独立内核头文件）。

```c
struct mqueue_inode_info {            /* :134 */
    spinlock_t lock;
    struct inode vfs_inode;
    wait_queue_head_t wait_q;
    struct rb_root msg_tree;          /* 按优先级排序的消息红黑树 */
    struct mq_attr attr;              /* 队列属性 */
    struct sigevent notify;           /* 通知注册 */
    struct sock *notify_sock;         /* netlink 通知 socket */
    struct ext_wait_queue e_wait_q[2]; /* [SEND] 和 [RECV] 等待队列 */
};

struct posix_msg_tree_node {          /* :61 */
    struct rb_node rb_node;
    struct list_head msg_list;        /* 同优先级的消息链表 */
    int priority;
};
```

`struct mq_attr`（`include/uapi/linux/mqueue.h:28`）：`mq_maxmsg`（最大消息数）、`mq_msgsize`（最大消息大小）、`mq_curmsgs`（当前消息数）。

## 3 核心 API（系统调用）

| API | 说明 | 备注 |
|-----|------|------|
| `mq_open(name, oflag, mode, attr)` | 打开/创建队列 | `ipc/mqueue.c:942` |
| `mq_timedsend(mqdes, msg_ptr, msg_len, prio, timeout)` | 定时发送 | `:1284` |
| `mq_timedreceive(mqdes, msg_ptr, msg_len, prio, timeout)` | 定时接收 | `:1298` |
| `mq_notify(mqdes, notification)` | 注册/取消通知 | `:1440` |
| `mq_getsetattr(mqdes, new, old)` | 获取/设置属性 | `:1496` |

## 4 VFS 集成

mqueue 实现了完整的虚拟文件系统：
- **`mqueue_fs_type`**（`:1682`）：注册为 `"mqueue"`，`FS_USERNS_MOUNT` 标志
- **`mqueue_file_operations`**（`:1663`）：`.read` 返回队列属性文本
- **命名空间支持**：每个 `ipc_namespace` 有独立的 `mq_mnt` 和限制

## 5 通知机制

使用 netlink socket 实现 `SIGEV_THREAD` 通知。当消息到达空队列时，通过注册的 AF_NETLINK socket 发送 cookie（`NOTIFY_WOKENUP` / `NOTIFY_REMOVED`）。

## 6 锁模型

`info->lock`（spinlock）保护所有访问。唤醒使用 `wake_q` 框架——锁已释放后才调用 `wake_up_q()`，避免优先级反转。

共 21 个文件引用 mqueue。
