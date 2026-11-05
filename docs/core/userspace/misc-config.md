# 杂项：configfs — 用户空间驱动的配置文件系统

> B 级文档。configfs 与 sysfs 相反：sysfs 由内核驱动层级，configfs 由用户空间通过 mkdir/rmdir 驱动。

### 定位

**功能**：configfs 提供一个由用户空间驱动的虚拟文件系统，允许通过标准的 mkdir/rmdir 系统调用来创建和删除配置对象。与 sysfs 只能查看内核已有数据不同，configfs 允许用户空间主动组装复杂配置——目录的创建和删除本身就是配置动作，属性文件的读写则用于调整参数。

**实现方式**：每个配置对象对应一个 `struct config_item`（`include/linux/configfs.h:38-47`），可容纳子项的容器对应 `struct config_group`（`configfs.h:77-83`），整个子系统对应 `struct configfs_subsystem`（`configfs.h:224-227`）。用户空间的 `mkdir` 触发 `configfs_group_operations` 中的 `make_item` 或 `make_group` 回调，`rmdir` 触发 `drop_item` 回调。configfs 维护自己的 dentry/inode 实现（`fs/configfs/inode.c`），不依赖 kernfs，通过 `configfs_register_subsystem()`（`fs/configfs/dir.c:1849`）挂载到 VFS。

**应用场景**：主要用于需要动态组装的复杂子系统配置。典型消费者包括 USB gadget 框架（`drivers/usb/gadget/configfs.c`，用于组合 USB 设备功能）、SCSI target 框架（`drivers/target/target_core_configfs.c`，用于配置存储后端和 LUN）、NVMe target（`drivers/nvme/target/configfs.c`）、DLM 分布式锁管理（`fs/dlm/config.c`）、OCFS2 集群节点管理（`fs/ocfs2/cluster/nodemanager.c`）、PCI endpoint 框架（`drivers/pci/endpoint/pci-ep-cfs.c`）、netconsole（`drivers/net/netconsole.c`）以及 IIO 子系统（`drivers/iio/industrialio-configfs.c`）。全树共 15 个文件直接调用 `configfs_register_subsystem`。

## 1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `fs/configfs/dir.c` | 1955 | 目录操作、subsystem 注册/注销 |
| `fs/configfs/file.c` | 482 | 属性读写 |
| `fs/configfs/inode.c` | 244 | inode 管理 |
| `fs/configfs/symlink.c` | 270 | 符号链接 |
| `fs/configfs/item.c` | 190 | `config_item` 生命周期 |
| `fs/configfs/mount.c` | 178 | 文件系统挂载 |
| `include/linux/configfs.h` | 273 | 公开 API |

总计约 3319 行。

## 2 核心数据结构

**`struct config_item`**（`include/linux/configfs.h:38-47`）：

```c
struct config_item {
    char                    *ci_name;
    struct kref              ci_kref;
    struct list_head         ci_entry;
    struct config_item      *ci_parent;
    struct config_group     *ci_group;
    struct config_item_type *ci_type;
    struct dentry           *ci_dentry;
};
```

**`struct config_group`**（`configfs.h:77-83`）：

```c
struct config_group {
    struct config_item      cg_item;
    struct list_head        cg_children;
    struct configfs_subsystem *cg_subsys;
    struct config_group    **default_groups;
    struct list_head        group_entry;
};
```

**`struct configfs_subsystem`**（`configfs.h:224-227`）：

```c
struct configfs_subsystem {
    struct config_group  su_group;
    struct mutex         su_mutex;
};
```

**`struct configfs_item_operations`**（`configfs.h:210-214`）：

```c
struct configfs_item_operations {
    void (*release)(struct config_item *);
    int (*allow_link)(struct config_item *, struct config_item *);
    void (*drop_link)(struct config_item *, struct config_item *);
};
```

**`struct configfs_group_operations`**（`configfs.h:216-222`）：

```c
struct configfs_group_operations {
    struct config_item *(*make_item)(struct config_group *, const char *);
    struct config_group *(*make_group)(struct config_group *, const char *);
    int (*commit_item)(struct config_item *);
    void (*disconnect_notify)(struct config_group *, struct config_item *);
    void (*drop_item)(struct config_group *, struct config_item *);
};
```

## 3 核心 API

| API | 位置 | 说明 |
|-----|------|------|
| `config_group_init(group)` | `item.c` | 初始化 group |
| `config_group_init_type_name(group, name, type)` | `item.c` | 初始化并设置名称/类型 |
| `configfs_register_subsystem(subsys)` | `dir.c:1849` | 注册并创建根 dentry |
| `configfs_unregister_subsystem(subsys)` | `dir.c:1909` | 注销 |

## 4 与 sysfs 的区别

| 维度 | sysfs | configfs |
|------|-------|----------|
| 驱动方 | 内核 | 用户空间 |
| 目录创建 | 内核自动 | `mkdir` → `make_item`/`make_group` |
| 目录删除 | 内核自动 | `rmdir` → `drop_item` |
| 底层 | kernfs | 自有文件系统 |
| 典型用途 | 设备/驱动属性 | 复杂配置（如 USB gadget、target） |

## 5 使用统计

| API | 使用文件数 |
|-----|-----------|
| `config_group_init` | 19 |
| `configfs_register_subsystem` | 18 |
| `configfs_unregister_subsystem` | 18 |

典型使用者：USB gadget（`drivers/usb/gadget/`）、SCSI target（`drivers/target/`）、`drivers/vdpa/`。
