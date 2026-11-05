# Unix 域套接字（AF_UNIX）

> B 级文档 · `net/unix/`（5,054 行）· 头文件 `include/net/af_unix.h`（145 行）

## 1 定位

**功能**：Unix 域套接字（AF_UNIX）提供同一主机上的进程间通信，支持三种类型：`SOCK_STREAM`（字节流）、`SOCK_DGRAM`（数据报）、`SOCK_SEQPACKET`（有序数据包）。独特能力是 SCM_RIGHTS 文件描述符传递——通过 socket 将 fd 从一个进程传给另一个进程，以及 SCM_CREDENTIALS 凭证传递。

**实现方式**：核心结构 `struct unix_sock` 嵌入标准 `struct sock`，通过 `peer` 指针指向已连接的对端，`unix_address` 管理绑定地址。SCM_RIGHTS  fd 传递通过 `unix_attach_fds()`/`unix_detach_fds()` 将文件指针列表（`struct scm_fp_list`）附加到 skb，接收方获得文件描述符副本。垃圾回收由 `net/unix/garbage.c` 的 `unix_gc()` 实现，使用 Tarjan SCC 算法检测 socket 间的循环 fd 引用，当 `unix_tot_inflight` 超过阈值时触发。全局哈希表（`UNIX_HASH_SIZE = 512`）按地址和抽象名称索引组织所有 unix socket。

**应用场景**：用户空间的 systemd 用 AF_UNIX 接收服务管理命令，X Window 系统用 AF_UNIX 进行客户端-服务器通信。内核中 `net/core/sock.c` 和 `net/core/skbuff.c` 处理 SCM_RIGHTS 的底层 skb 管理。`net/core/filter.c` 和 `kernel/bpf/cgroup.c` 为 AF_UNIX 提供 BPF sockmap 集成。`fs/smb/server/smb2pdu.c`（SMB 服务器）和 `fs/smb/client/inode.c`（SMB 客户端）用 AF_UNIX 进行本地通信。`net/vmw_vsock/af_vsock.c` 引用 AF_UNIX 进行虚拟机 socket的地址族转换。全树共 20 个 C 文件引用 `AF_UNIX`。

## 2 核心数据结构

头文件：`include/net/af_unix.h`

```c
struct unix_sock {                    /* :76 */
    struct sock       sk;             /* 必须是第一个成员 */
    struct unix_address *addr;        /* 绑定地址 */
    struct path       path;           /* 文件系统路径（绑定 socket） */
    struct mutex      iolock, bindlock;
    struct sock       *peer;          /* 已连接的对端 */
    struct sock       *listener;      /* 监听 socket（已接受的） */
    struct unix_vertex *vertex;       /* GC 图遍历用 */
    struct scm_stat   scm_stat;       /* fd 传递统计 */
};

struct unix_address {                 /* :51 */
    refcount_t refcnt;
    int len;
    struct sockaddr_un name[];        /* 柔性数组 */
};

struct unix_skb_parms {               /* :57 */
    struct pid     *pid;              /* skb 凭证 */
    kuid_t         uid;
    kgid_t         gid;
    struct scm_fp_list *fp;           /* 传递的文件（SCM_RIGHTS） */
    u32            secid;             /* 安全 ID */
};
```

## 3 协议操作

`net/unix/af_unix.c` 定义三组 `proto_ops`：

| 类型 | ops 结构 | 特点 |
|------|----------|------|
| SOCK_STREAM | `unix_stream_ops`（`:825`） | listen/accept/connect，字节流 |
| SOCK_DGRAM | `unix_dgram_ops`（`:851`） | 无连接，数据报 |
| SOCK_SEQPACKET | `unix_seqpacket_ops`（`:876`） | 类似 stream 但保留消息边界 |

## 4 SCM_RIGHTS 文件描述符传递

这是 AF_UNIX 最复杂的特性：

1. **附加**（`unix_attach_fds()`，`:1751`）：发送方带 `SCM_RIGHTS` 发消息时，`scm_fp_dup()` 将文件指针列表复制到 skb 中
2. **分离**（`unix_detach_fds()`，`:1770`）：接收或 skb 销毁时，文件从 inflight 图中移除
3. **垃圾回收**（`net/unix/garbage.c`）：`unix_gc()` 用 Tarjan SCC 算法检测 socket 间的循环 fd 引用。当 `unix_tot_inflight > UNIX_INFLIGHT_TRIGGER_GC` 时触发 GC
4. **凭证传递**：`SOCK_PASSCRED` 设置时，`maybe_add_creds()`（`:1827`）将 pid/uid/gid 附加到 skb

## 5 哈希表

Unix socket 组织在全局哈希表中（`UNIX_HASH_SIZE = 512`），按地址哈希和抽象名称哈希索引。

## 6 文件组成

| 文件 | 行数 | 职责 |
|------|------|------|
| `af_unix.c` | 3,837 | 核心协议实现 |
| `garbage.c` | 613 | SCM_RIGHTS GC |
| `diag.c` | 343 | netlink socket 诊断 |
| `unix_bpf.c` | 201 | BPF sockmap 集成 |
| `sysctl_net_unix.c` | 60 | sysctl 注册 |

共 69 个文件引用 Unix 域套接字。
