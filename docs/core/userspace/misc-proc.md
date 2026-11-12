# 杂项：procfs — 进程与系统信息文件系统

> B 级文档。`/proc` 是最古老的用户空间接口之一，提供进程信息和系统状态。

常见文件的输出格式、字段单位和排查方法，参见 [/proc 文件与字段详解](proc-files.md)。本文侧重内核实现与创建 API。

### 定位

**功能**：procfs 实现 `/proc` 虚拟文件系统，向用户空间暴露两类信息：一是 per-PID 的进程元数据（`/proc/<pid>/stat`、`maps`、`cmdline`、`fd/` 等），二是全局系统状态（`/proc/meminfo`、`cpuinfo`、`kcore`、`vmcore` 等）。它同时作为 sysctl 的用户空间入口（`/proc/sys/`），允许运行时调整内核参数。

**实现方式**：每个 `/proc` 节点对应一个 `struct proc_dir_entry`（`fs/proc/internal.h:30-67`），通过 rbtree 组织子目录。文件操作使用 `struct proc_ops`（`include/linux/proc_fs.h:33-47`），支持 open/read/write/mmap/ioctl 等回调。创建接口分三层：`proc_create()`（`fs/proc/generic.c:597`）用于简单文件，`proc_create_seq_private()`（`generic.c:631`）用于需要迭代器的 seq_file 接口，`proc_create_single_data()`（`generic.c:662`）用于单次输出。per-PID 目录由 `fs/proc/base.c`（3988 行）管理，内存映射信息由 `fs/proc/task_mmu.c`（2028 行）提供。

**应用场景**：procfs 是 Linux 系统管理的基础设施——全树 142 个文件调用 `remove_proc_entry`，75 个文件调用 `proc_mkdir`，67 个文件调用 `proc_create`。主要消费者包括：网络子系统（`net/core/pktgen.c`、NFS 相关模块）、存储子系统（`drivers/md/md.c`、`drivers/scsi/sg.c`）、文件系统（`fs/nfsd/nfsctl.c`）、调试工具（`kernel/kallsyms.c`、`drivers/tty/sysrq.c`）和崩溃分析（`fs/proc/vmcore.c`、`fs/proc/kcore.c`）。用户空间的 `ps`、`top`、`free` 等工具全部依赖 `/proc` 获取数据。

## 1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `fs/proc/base.c` | 3988 | per-PID 目录条目 |
| `fs/proc/task_mmu.c` | 2028 | maps、smaps 等内存信息 |
| `fs/proc/proc_sysctl.c` | 1953 | sysctl 桥接 |
| `fs/proc/vmcore.c` | 1608 | crash dump |
| `fs/proc/generic.c` | 834 | `proc_create`、`proc_set_size` 等通用 API |
| `fs/proc/array.c` | 812 | `/proc/PID/stat` |
| `fs/proc/inode.c` | 704 | inode 操作 |
| `fs/proc/kcore.c` | 730 | `/proc/kcore`（物理内存映像） |
| `fs/proc/root.c` | 375 | 初始化和根目录 |
| `include/linux/proc_fs.h` | 247 | 公开 API |
| `fs/proc/internal.h` | — | `struct proc_dir_entry` 定义 |

总计约 16 101 行。

## 2 核心数据结构

**`struct proc_dir_entry`**（`fs/proc/internal.h:30-67`）— 每个 `/proc` 节点的描述符：

- `refcnt`：引用计数
- `proc_ops` / `proc_dir_ops`：union，文件用 `proc_ops`，目录用 `proc_dir_ops`
- `seq_ops`：seq_file 操作（用于迭代器接口）
- `rb_root`：子目录 rbtree
- `uid`/`gid`：所有者
- `size`：文件大小
- `parent`：父节点指针
- `inline_name`：内联名称（短名称避免分配）

**`struct proc_ops`**（`include/linux/proc_fs.h:33-47`）— 文件操作：

```c
struct proc_ops {
    int (*proc_open)(struct inode *, struct file *);
    ssize_t (*proc_read)(struct file *, char __user *, size_t, loff_t *);
    ssize_t (*proc_read_iter)(struct kiocb *, struct iov_iter *);
    ssize_t (*proc_write)(struct file *, const char __user *, size_t, loff_t *);
    loff_t (*proc_lseek)(struct file *, loff_t, int);
    int (*proc_release)(struct inode *, struct file *);
    __poll_t (*proc_poll)(struct file *, struct poll_table_struct *);
    long (*proc_ioctl)(struct file *, unsigned int, unsigned long);
    int (*proc_mmap)(struct file *, struct vm_area_struct *);
};
```

## 3 核心 API

头文件：`include/linux/proc_fs.h`；实现：`fs/proc/generic.c`

| API | 位置 | 说明 |
|-----|------|------|
| `proc_create(name, mode, parent, proc_ops)` | `generic.c:597` | 创建 proc 文件 |
| `proc_create_data(name, mode, parent, proc_ops, data)` | `generic.c:583` | 创建并附加私有数据 |
| `proc_mkdir(name, parent)` | `proc_fs.h:88` | 创建目录 |
| `proc_create_seq_private(name, mode, parent, seq_ops, size, data)` | `generic.c:631` | 创建 seq_file 接口 |
| `proc_create_single_data(name, mode, parent, show, data)` | `generic.c:662` | 创建单次输出文件 |
| `proc_set_size(pde, size)` | `generic.c:677` | 设置文件大小 |
| `proc_set_user(pde, uid, gid)` | `generic.c:683` | 设置所有者 |
| `pde_data(inode)` | `proc_fs.h:122` | 获取私有数据（`inode->i_private`） |
| `remove_proc_entry(name, parent)` | `proc_fs.h:128` | 删除条目 |
| `proc_root_init()` | `root.c:285` | 启动时初始化 |

## 4 组织层级

```
/proc/
├── [pid]/          ← base.c 管理（stat, cmdline, maps, fd/...）
├── sys/            ← proc_sysctl.c 桥接 sysctl
├── net/            ← proc_net.c（每命名空间）
├── fs/             ← root.c
├── driver/         ← root.c
├── bus/            ← root.c
├── meminfo         ← meminfo.c
├── stat            ← stat.c
├── cpuinfo         ← cpuinfo.c
├── kcore           ← kcore.c
└── vmcore          ← vmcore.c（crash dump）
```

## 5 使用统计

| API | 使用文件数 |
|-----|-----------|
| `remove_proc_entry` | 142 |
| `proc_mkdir` | 75 |
| `proc_create` | 67 |
| `proc_create_seq` | 39 |
| `proc_create_data` | 34 |
