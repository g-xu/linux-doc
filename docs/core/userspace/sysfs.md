# sysfs — 内核设备模型文件系统

## §0 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `fs/sysfs/file.c` | 774 | 属性文件读写、show/store 分发、`sysfs_emit` |
| `fs/sysfs/group.c` | 579 | 属性组创建/删除/合并 |
| `fs/sysfs/symlink.c` | 198 | kobject 间符号链接 |
| `fs/sysfs/dir.c` | 161 | 目录创建/删除/重命名/移动 |
| `fs/sysfs/mount.c` | 115 | sysfs 挂载、kernfs root 创建 |
| `include/linux/sysfs.h` | 660 | 全部公开 API、结构体定义、`__ATTR*` 宏 |

头文件：`include/linux/sysfs.h`；实现：`fs/sysfs/`。

---

## §1 定位

sysfs 将内核设备模型（kobject / kset / device）导出为用户空间可见的文件层级，挂载在 `/sys`。它是**内核 ABI 的一部分**——属性名、格式、语义一旦发布就不能随意更改。

sysfs 是 **kernfs 的消费者**。所有层级管理、文件创建、VFS 集成都委托给 kernfs。sysfs 本身只负责：
1. 将 kobject 层级映射为 kernfs 目录
2. 将 `attribute` 映射为 kernfs 文件
3. 分发 show/store 回调

---

## §2 核心数据结构

### 2.1 `struct attribute`（`include/linux/sysfs.h:30-38`）

```c
struct attribute {
    const char    *name;
    umode_t        mode;
#ifdef CONFIG_DEBUG_LOCK_ALLOC
    bool           ignore_lockdep:1;
    struct lock_class_key *key;
    struct lock_class_key skey;
#endif
};
```

最基本的 sysfs 文件描述符。`mode` 决定权限（`0444` 只读、`0644` 可写）。

### 2.2 `struct attribute_group`（`sysfs.h:84-92`）

```c
struct attribute_group {
    const char    *name;          // 可选子目录名
    umode_t       (*is_visible)(struct kobject *, struct attribute *, int);
    umode_t       (*is_bin_visible)(struct kobject *, struct bin_attribute *, int);
    struct attribute    **attrs;      // NULL 终止
    struct bin_attribute **bin_attrs;  // NULL 终止
};
```

将多个属性打包为一组。`is_visible` 回调可动态控制每个属性的可见性。

### 2.3 `struct bin_attribute`（`sysfs.h:175-186`）

```c
struct bin_attribute {
    struct attribute attr;
    size_t         size;
    void           *private;
    struct address_space *(*f_mapping)(void);
    ssize_t (*read)(struct file *, struct kobject *, struct bin_attribute *,
                    char *, loff_t, size_t);
    ssize_t (*write)(struct file *, struct kobject *, struct bin_attribute *,
                     char *, loff_t, size_t);
    int    (*mmap)(struct file *, struct kobject *, struct bin_attribute *,
                   struct vm_area_struct *);
};
```

二进制属性，用于导出大块数据（如固件、PCI 配置空间）。

### 2.4 `struct sysfs_ops`（`sysfs.h:254-257`）

```c
struct sysfs_ops {
    ssize_t (*show)(struct kobject *, struct attribute *, char *);
    ssize_t (*store)(struct kobject *, struct attribute *, const char *, size_t);
};
```

嵌入在 `struct kobj_type` 中，由 kobject 的类型决定。sysfs 在读文件时调用 `show`，写文件时调用 `store`。

---

## §3 属性宏速查

头文件：`include/linux/sysfs.h`

### 3.1 普通属性

| 宏 | 行号 | 展开 |
|----|------|------|
| `__ATTR(name, mode, show, store)` | 101 | 完整自定义 |
| `__ATTR_PREALLOC(name, mode, show, store)` | 108 | 带 `SYSFS_PREALLOC` 标志 |
| `__ATTR_RO(name)` | 115 | 只读，`name_show` |
| `__ATTR_RO_MODE(name, mode)` | 120 | 只读，自定义权限 |
| `__ATTR_RW(name)` | 138 | 读写，`0644`，`name_show` + `name_store` |
| `__ATTR_RW_MODE(name, mode)` | 126 | 读写，自定义权限 |
| `__ATTR_WO(name)` | 133 | 只写，`name_store` |

### 3.2 二进制属性

| 宏 | 行号 | 展开 |
|----|------|------|
| `__BIN_ATTR(name, mode, read, write, size)` | 201 | 完整自定义 |
| `__BIN_ATTR_RO(name, size)` | 208 | 只读 |
| `__BIN_ATTR_RW(name, size)` | 220 | 读写 |
| `BIN_ATTR(name, mode, read, write, size)` | 225 | 声明 `struct bin_attribute` |
| `BIN_ATTR_RO(name, size)` | 229 | 声明只读 |

### 3.3 属性组

| 宏 | 行号 | 作用 |
|----|------|------|
| `ATTRIBUTE_GROUPS(name)` | 159 | 声明 `name_group` + `name_groups[]` |
| `__ATTRIBUTE_GROUPS(name)` | 153 | 只声明 `name_groups[]` |

---

## §4 API 速览

头文件：`include/linux/sysfs.h`；实现：`fs/sysfs/`

### 4.1 目录操作

| API | 实现位置 | 说明 |
|-----|----------|------|
| `sysfs_create_dir_ns(kobj, ns)` | `dir.c:59` | 为 kobject 创建目录 |
| `sysfs_remove_dir(kobj)` | `dir.c` | 删除目录 |
| `sysfs_rename_dir_ns(kobj, new_name, new_ns)` | `dir.c` | 重命名 |
| `sysfs_move_dir_ns(kobj, new_parent, new_ns)` | `dir.c` | 移动 |
| `sysfs_create_mount_point(parent_kobj, name)` | `mount.c` | 创建挂载点 |
| `sysfs_remove_mount_point(parent_kobj, name)` | `mount.c` | 删除挂载点 |

### 4.2 文件操作

| API | 实现位置 | 说明 |
|-----|----------|------|
| `sysfs_create_file_ns(kobj, attr, ns)` | `file.c:254` | 创建属性文件 |
| `sysfs_create_files(kobj, attr[])` | `file.c` | 批量创建 |
| `sysfs_remove_file_ns(kobj, attr, ns)` | `file.c:480` | 删除属性文件 |
| `sysfs_remove_file_self(kobj, attr)` | `file.c` | 删除自身（用于"一次性"文件） |
| `sysfs_chmod_file(kobj, attr, mode)` | `file.c` | 修改权限 |
| `sysfs_add_file_to_group(kobj, attr, group)` | `file.c:370` | 添加到已有组 |
| `sysfs_remove_file_from_group(kobj, attr, group)` | `file.c` | 从组中删除 |
| `sysfs_create_bin_file(kobj, attr)` | `file.c` | 创建二进制文件 |
| `sysfs_remove_bin_file(kobj, attr)` | `file.c` | 删除二进制文件 |

### 4.3 组操作

| API | 实现位置 | 说明 |
|-----|----------|------|
| `sysfs_create_group(kobj, grp)` | `group.c:108` | 创建属性组 |
| `sysfs_create_groups(kobj, groups[])` | `group.c` | 批量创建组 |
| `sysfs_update_group(kobj, grp)` | `group.c` | 更新组（重新评估 `is_visible`） |
| `sysfs_remove_group(kobj, grp)` | `group.c` | 删除组 |
| `sysfs_remove_groups(kobj, groups[])` | `group.c` | 批量删除 |
| `sysfs_merge_group(kobj, grp)` | `group.c:318` | 合并到已有组 |
| `sysfs_unmerge_group(kobj, grp)` | `group.c` | 从组中取消合并 |

### 4.4 链接与通知

| API | 实现位置 | 说明 |
|-----|----------|------|
| `sysfs_create_link(kobj, target, name)` | `symlink.c:68` | 创建符号链接 |
| `sysfs_remove_link(kobj, name)` | `symlink.c` | 删除链接 |
| `sysfs_notify(kobj, dir, attr)` | `file.c:178` | 通知属性变化（用于 poll） |

### 4.5 辅助函数

| API | 实现位置 | 说明 |
|-----|----------|------|
| `sysfs_emit(buf, fmt, ...)` | `file.c:731` | 安全的 `snprintf`（限制 `PAGE_SIZE`） |
| `sysfs_emit_at(buf, at, fmt, ...)` | `file.c` | 带偏移的安全打印 |
| `sysfs_streq(a, b)` | `lib/string_helpers.c:895` | 忽略尾部空白比较 |
| `sysfs_match_string(array, str)` | `lib/string_helpers.c:963` | 在字符串数组中查找 |
| `sysfs_break_active_protection(kobj, attr)` | `file.c` | 打破活跃保护（用于自删除文件） |

---

## §5 show/store 分发机制

```
用户 read() / write()
  │
  └─ kernfs 调用 kernfs_ops.seq_show / .write
       │
       ├─ sysfs_kf_seq_show()  [file.c:40]
       │    ├─ 获取 kobj->ktype->sysfs_ops
       │    └─ ops->show(kobj, kn->priv, buf)
       │         └─ 驱动实现的 show 函数
       │              └─ 使用 sysfs_emit(buf, ...) 格式化输出
       │
       └─ sysfs_kf_write()  [file.c:127]
            ├─ 获取 kobj->ktype->sysfs_ops
            └─ ops->store(kobj, kn->priv, buf, count)
                 └─ 驱动实现的 store 函数
```

`kn->priv` 指向 `struct attribute *`，由 kernfs 在创建文件时保存。

---

## §6 与 kernfs 的关系

sysfs 是 kernfs 的**薄封装层**：

| sysfs 操作 | 委托的 kernfs 操作 |
|------------|-------------------|
| `sysfs_create_dir_ns` | `kernfs_create_dir_ns` |
| `sysfs_add_file_mode_ns` | `__kernfs_create_file` |
| `sysfs_create_link` | `kernfs_create_link` |
| `sysfs_remove_dir` | `kernfs_remove` |
| 挂载 | `kernfs_create_root` + `kernfs_mount` |

`struct kobject` 的 `sd` 字段（`include/linux/kobject.h:73`）是 `struct kernfs_node *`，将 kobject 连接到 kernfs 层级。

---

## §7 arm64 特殊性

sysfs 本身是架构无关的。arm64 上的典型使用：

| 文件 | 用途 |
|------|------|
| `arch/arm64/kernel/cpuinfo.c` | CPU 信息暴露 |
| `arch/arm64/kernel/cpufeature.c` | CPU 能力属性 |
| `arch/arm64/kernel/perf_event.c` | PMU sysfs 注册 |
| `arch/arm64/kernel/mte.c` | MTE 控制 |

这些使用 `__ATTR` / `__ATTR_RO` 宏和 `sysfs_create_group` 暴露属性。

---

## §8 使用统计

| API | 使用文件数 |
|-----|-----------|
| `sysfs_emit` | 419 |
| `sysfs_create_group` | 278 |
| `sysfs_streq` | 87 |
| `sysfs_create_link` | 85 |
| `sysfs_create_file` | 63 |
| `sysfs_notify` | 47 |
| `sysfs_remove_file` | 34 |
| `sysfs_match_string` | 31 |
| `sysfs_add_file_to_group` | 13 |
| `sysfs_merge_group` | 9 |
