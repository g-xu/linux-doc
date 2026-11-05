# 杂项：kernfs — 伪文件系统基础框架

> B 级文档。kernfs 提供 VFS 无关的层级树管理，是 sysfs 的底层引擎。

### 定位

**功能**：kernfs 是一个与 VFS 解耦的虚拟文件系统框架，负责管理树形层级中节点的创建、查找、引用计数、命名空间隔离和文件读写。它将"一棵层级树需要的所有通用操作"从 sysfs 中抽离成独立子系统，使得其他需要层级虚拟文件树的代码不必重复实现 inode/dentry 管理。

**实现方式**：核心数据结构是 `struct kernfs_node`（`include/linux/kernfs.h:190-226`），每个节点通过 rbtree 挂在父目录下实现 O(log n) 查找。全局根节点 `struct kernfs_root`（`fs/kernfs/kernfs-internal.h:34-50`）管理 inode 号分配（idr）、superblock 链表和全局读写锁。文件操作通过 `struct kernfs_ops`（`kernfs.h:269+`）回调实现，支持 seq_file 读、write、mmap 和 poll 通知。锁设计采用哈希锁（`NR_KERNFS_LOCKS`，最多 1024 把）减少全局竞争。

**应用场景**：直接使用者极少——sysfs（`fs/sysfs/mount.c:101` 调用 `kernfs_create_root`）是最大的消费者，所有 sysfs 操作最终委托给 kernfs。cgroup 文件系统（`kernel/cgroup/cgroup.c`）也直接使用 kernfs 构建层级结构。x86 的 resctrl（`arch/x86/kernel/cpu/resctrl/rdtgroup.c`）同样直接使用。绝大多数内核代码通过 sysfs 或 cgroup 间接使用 kernfs，不直接调用其 API。

## 1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `fs/kernfs/dir.c` | 1866 | `create_root`、`create_dir_ns`、`find_and_get`、`remove` |
| `fs/kernfs/file.c` | 1103 | open、read、write、mmap、notify |
| `fs/kernfs/inode.c` | 446 | inode 分配、权限 |
| `fs/kernfs/mount.c` | 423 | superblock、挂载 |
| `fs/kernfs/symlink.c` | 153 | 符号链接 |
| `include/linux/kernfs.h` | 632 | 公开 API |
| `fs/kernfs/kernfs-internal.h` | — | `struct kernfs_root` 定义 |

总计约 3991 行。

## 2 核心数据结构

**`struct kernfs_node`**（`include/linux/kernfs.h:190-226`）— 每个节点的描述符：

```c
struct kernfs_node {
    atomic_t        count;      // 引用计数
    atomic_t        active;     // 活跃引用
    struct kernfs_node *parent;
    const char      *name;
    struct rb_node  rb;         // 在父目录 rbtree 中
    const void      *ns;        // 命名空间标签
    unsigned int    hash;
    union {
        struct kernfs_elem_dir      dir;
        struct kernfs_elem_symlink  symlink;
        struct kernfs_elem_attr     attr;
    };
    void            *priv;
    unsigned short  flags;
    umode_t         mode;
    struct kernfs_iattrs *iattr;
};
```

**`struct kernfs_root`**（`fs/kernfs/kernfs-internal.h:34-50`）：

```c
struct kernfs_root {
    struct kernfs_node        *kn;       // 根节点
    unsigned int               flags;
    struct idr                 ino_idr;
    struct kernfs_syscall_ops *syscall_ops;
    struct list_head           supers;
    wait_queue_head_t          deactivate_waitq;
    struct rw_semaphore        kernfs_rwsem;
};
```

**`struct kernfs_ops`**（`kernfs.h:269+`）— 文件操作：

```c
struct kernfs_ops {
    int (*open)(struct kernfs_open_file *);
    void (*release)(struct kernfs_open_file *);
    int (*seq_show)(struct seq_file *, void *);
    ssize_t (*read)(struct kernfs_open_file *, char *, size_t, loff_t);
    ssize_t (*write)(struct kernfs_open_file *, char *, size_t, loff_t);
    int (*mmap)(struct kernfs_open_file *, struct vm_area_struct *);
    ...
};
```

**`struct kernfs_open_file`**（`kernfs.h:249-267`）— 每个打开文件的状态：

```c
struct kernfs_open_file {
    struct kernfs_node  *kn;
    struct file         *file;
    struct seq_file     *seq_file;
    void                *priv;
    struct mutex         mutex;
    int                  event;
    struct list_head     list;
    char                *prealloc_buf;
    size_t               atomic_write_len;
};
```

## 3 核心 API

| API | 位置 | 说明 |
|-----|------|------|
| `kernfs_create_root(syscall_ops, flags, priv)` | `dir.c:948` | 创建 kernfs 根 |
| `kernfs_create_dir_ns(parent, name, mode, uid, gid, priv, ns)` | `dir.c:1037` | 创建目录 |
| `__kernfs_create_file(parent, name, mode, uid, gid, size, ops, priv, ns, key)` | `kernfs.h:423` | 创建文件 |
| `kernfs_create_link(parent, name, target)` | `kernfs.h:430` | 创建符号链接 |
| `kernfs_remove(kn)` | `dir.c:1490` | 删除节点 |
| `kernfs_find_and_get_ns(parent, name, ns)` | `dir.c:899` | 查找节点 |
| `kernfs_notify(kn)` | `file.c:996` | poll 通知 |
| `kernfs_get(kn)` / `kernfs_put(kn)` | `dir.c` | 引用计数 |
| `kernfs_activate(kn)` | `dir.c` | 激活节点（配合 `DEACTIVATED` 标志） |

## 4 与 sysfs 的关系

sysfs 在 `fs/sysfs/mount.c:101` 调用 `kernfs_create_root()` 创建 sysfs 根。每个 sysfs 操作都委托给 kernfs：

- `sysfs_create_dir_ns` → `kernfs_create_dir_ns`
- `sysfs_add_file_mode_ns` → `__kernfs_create_file`
- `sysfs_create_link` → `kernfs_create_link`

`struct kobject` 的 `sd` 字段（`include/linux/kobject.h:73`）是 `struct kernfs_node *`。

**注意**：debugfs 在 6.1 中**不使用** kernfs——它有自己的 `simple_*` VFS 实现。

## 5 锁设计

kernfs 使用 `NR_KERNFS_LOCKS` 个哈希锁（`kernfs.h:62-68`）减少竞争：

| CPU 数 | 锁位数 | 锁数量 |
|--------|--------|--------|
| 1 | 1 | 2 |
| 2-3 | 2 | 4 |
| 4-7 | 4 | 16 |
| 8-15 | 6 | 64 |
| 16-31 | 8 | 256 |
| 32+ | 10 | 1024 |

## 6 使用统计

| API | 使用文件数 |
|-----|-----------|
| `kernfs_create_dir_ns` | 7 |
| `kernfs_remove` | 6 |
| `kernfs_notify` | 6 |
| `__kernfs_create_file` | 5 |
| `kernfs_find_and_get` | 5 |

直接使用者极少——绝大多数通过 sysfs 间接使用。
