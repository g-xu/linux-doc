# debugfs — 内核调试文件系统

## §0 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `fs/debugfs/inode.c` | 919 | VFS superblock、dentry/inode 操作、创建/删除/重命名/查找 API |
| `fs/debugfs/file.c` | 1239 | 代理 file_operations、类型化创建辅助函数 |
| `fs/debugfs/internal.h` | 52 | `debugfs_fsdata`、代理 fops 声明 |
| `include/linux/debugfs.h` | 404 | 公开 API：声明 + `!CONFIG_DEBUG_FS` 桩函数 |

头文件：`include/linux/debugfs.h`；实现：`fs/debugfs/inode.c` + `fs/debugfs/file.c`。

---

## §1 定位

debugfs 是一个轻量级虚拟文件系统，挂载在 `/sys/kernel/debug`，专供内核开发者调试使用。它不是 sysfs 的替代品——sysfs 有严格的 ABI 稳定性要求，debugfs 没有。

**与 sysfs 的关键区别**：debugfs **不基于 kernfs**。它是一个独立的 `tmpfs` 风格的文件系统，使用 `simple_fill_super()` 和 `simple_*` VFS 辅助函数。

---

## §2 核心数据结构

### 2.1 `struct debugfs_fsdata`（`fs/debugfs/internal.h:18-28`）

每个 dentry 的 `d_fsdata` 存储此结构，用于代理机制：

```c
struct debugfs_fsdata {
    const struct file_operations *real_fops;
    union {
        struct {                          // automount 节点
            debugfs_automount_t automount;
            void *automount_data;
        };
        struct {                          // 普通文件
            atomic_t active_users;
            struct completion active_users_drained;
        };
    };
};
```

`d_fsdata` 的低位用作标记位（tag bit），区分"原始 fops 指针"和"已升级的 debugfs_fsdata 结构"。

### 2.2 `struct debugfs_blob_wrapper`（`debugfs.h:24-27`）

```c
struct debugfs_blob_wrapper {
    void *data;
    unsigned long size;
};
```

用于 `debugfs_create_blob()` 导出二进制数据块。

### 2.3 `struct debugfs_regset32`（`debugfs.h:34-39`）

```c
struct debugfs_regset32 {
    const struct debugfs_reg32 *regs;  // 寄存器名+偏移数组
    int nregs;
    void __iomem *base;                // MMIO 基地址
    struct device *dev;                // 可选，用于 Runtime PM
};
```

用于 `debugfs_create_regset32()` 导出硬件寄存器快照。

### 2.4 `struct debugfs_u32_array`（`debugfs.h:41-44`）

```c
struct debugfs_u32_array {
    u32 *array;
    u32 n_elements;
};
```

---

## §3 代理 file_operations 机制

debugfs 的核心设计是**代理 fops**：调用者传入自己的 `file_operations`，但 debugfs 在实际注册时用代理替换，在 open 时再恢复原始 fops。这保证了文件删除时不会访问已释放的 fops。

### 3.1 三级代理

| 代理类型 | 位置 | 保护范围 |
|----------|------|----------|
| `debugfs_noop_file_operations` | `file.c:41-46` | 无操作的默认 fops |
| `debugfs_open_proxy_file_operations` | `file.c:212-214` | 只代理 `.open`，open 后替换为完整代理 |
| `debugfs_full_proxy_file_operations` | `file.c:370-372` | 代理所有操作（llseek/read/write/poll/ioctl） |

### 3.2 代理流程

```
debugfs_create_file(name, mode, parent, data, fops)
  │
  ├─ dentry->d_fsdata = (void *)((unsigned long)fops | 1)  // 低位标记
  │
  ├─ 注册 debugfs_full_proxy_file_operations 作为实际 fops
  │
  └─ 用户 open() 时：
       └─ full_proxy_open()
            ├─ debugfs_file_get() → 升级 d_fsdata 为完整结构
            │    └─ 分配 debugfs_fsdata，设置 refcount
            ├─ 为每个打开文件分配新的 file_operations
            │    └─ 每个回调被 FULL_PROXY_FUNC 宏包装
            │         ├─ debugfs_file_get() → 引用计数 +1
            │         ├─ 调用 real_fops->read/write/...
            │         └─ debugfs_file_put() → 引用计数 -1
            └─ file->f_op = 新的代理 fops
```

### 3.3 `DEFINE_DEBUGFS_ATTRIBUTE` 宏（`debugfs.h:48-67`）

简化自定义属性的创建：

```c
#define DEFINE_DEBUGFS_ATTRIBUTE(__fops, __get, __set, __fmt)
```

生成一个 `file_operations` 结构，`.read` 使用 `debugfs_attr_read`，`.write` 使用 `debugfs_attr_write`（或 `_signed` 变体）。内部调用 `simple_attr_open()` 将 `__get`/`__set` 回调与格式化字符串 `__fmt` 绑定。

---

## §4 API 速览

### 4.1 目录与节点管理

| API | 说明 | 备注 |
|-----|------|------|
| `debugfs_create_dir(name, parent)` | 创建目录 | `parent=NULL` 在根目录创建 |
| `debugfs_create_file(name, mode, parent, data, fops)` | 创建自定义文件 | 使用完整代理 |
| `debugfs_create_file_unsafe(name, mode, parent, data, fops)` | 创建文件（仅代理 open） | 性能更好但不安全删除 |
| `debugfs_create_file_size(name, mode, parent, data, fops, file_size)` | 创建文件并设置初始大小 | — |
| `debugfs_create_symlink(name, parent, dest)` | 创建符号链接 | — |
| `debugfs_create_automount(name, parent, f, data)` | 创建自动挂载点 | `f` 返回 `vfsmount *` |
| `debugfs_remove(dentry)` | 删除节点（递归） | `debugfs_remove_recursive` 是其别名 |
| `debugfs_lookup_and_remove(name, parent)` | 查找并删除 | — |
| `debugfs_lookup(name, parent)` | 查找节点 | 返回 `ERR_PTR(-ENODEV)` 而非 NULL |
| `debugfs_rename(old_dir, old_dentry, new_dir, new_name)` | 重命名 | — |

### 4.2 类型化创建函数

所有类型化函数在 `fs/debugfs/file.c` 中实现，使用 `DEFINE_DEBUGFS_ATTRIBUTE` 生成 fops：

| API | 位置 | 格式 |
|-----|------|------|
| `debugfs_create_u8(name, mode, parent, value)` | `file.c:466` | `%llu` |
| `debugfs_create_u16(name, mode, parent, value)` | `file.c:502` | `%llu` |
| `debugfs_create_u32(name, mode, parent, value)` | `file.c:538` | `%llu` |
| `debugfs_create_u64(name, mode, parent, value)` | `file.c:575` | `%llu` |
| `debugfs_create_ulong(name, mode, parent, value)` | `file.c:614` | `%llu` |
| `debugfs_create_x8(name, mode, parent, value)` | `file.c:659` | `0x%02llx` |
| `debugfs_create_x16(name, mode, parent, value)` | `file.c:677` | `0x%04llx` |
| `debugfs_create_x32(name, mode, parent, value)` | `file.c:695` | `0x%08llx` |
| `debugfs_create_x64(name, mode, parent, value)` | `file.c:713` | `0x%016llx` |
| `debugfs_create_size_t(name, mode, parent, value)` | `file.c:747` | `%llu` |
| `debugfs_create_atomic_t(name, mode, parent, value)` | `file.c:783` | `%lld`（有符号） |
| `debugfs_create_bool(name, mode, parent, value)` | `file.c:868` | `Y\n` / `N\n` |
| `debugfs_create_str(name, mode, parent, value)` | `file.c:960` | 字符串 + `\n` |
| `debugfs_create_xul(name, mode, parent, value)` | `debugfs.h:394` | inline，按 `sizeof` 分发到 x32/x64 |

### 4.3 特殊类型

| API | 说明 | 备注 |
|-----|------|------|
| `debugfs_create_blob(name, mode, parent, blob)` | 导出二进制数据块 | 只读 |
| `debugfs_create_regset32(name, mode, parent, regset)` | 导出硬件寄存器 | seq_file 格式 |
| `debugfs_create_u32_array(name, mode, parent, array)` | 导出 u32 数组 | 只读 |
| `debugfs_create_devm_seqfile(dev, name, parent, read_fn)` | 设备绑定的 seq_file | 自动关联设备 |
| `debugfs_print_regs32(s, regs, nregs, base, prefix)` | 打印寄存器到 seq_file | 辅助函数 |

### 4.4 辅助函数

| API | 说明 | 备注 |
|-----|------|------|
| `debugfs_real_fops(filp)` | 获取代理背后的真实 fops | — |
| `debugfs_file_get(dentry)` / `debugfs_file_put(dentry)` | 手动引用计数 | 自定义 fops 中使用 |
| `debugfs_attr_read(file, buf, len, ppos)` | 属性读 | `DEFINE_DEBUGFS_ATTRIBUTE` 使用 |
| `debugfs_attr_write(file, buf, len, ppos)` | 属性写 | — |
| `debugfs_read_file_bool(file, buf, count, ppos)` | 读 bool 值 | — |
| `debugfs_write_file_bool(file, buf, count, ppos)` | 写 bool 值 | — |
| `debugfs_read_file_str(file, buf, count, ppos)` | 读字符串 | — |
| `debugfs_initialized()` | 检查 debugfs 是否已初始化 | — |

---

## §5 安全锁定（Lockdown）

### 5.1 挂载控制

`fs/debugfs/inode.c:38` 的 `debugfs_allow` 位掩码控制 debugfs 行为：

| Kconfig / 命令行 | 效果 |
|-----------------|------|
| `CONFIG_DEBUG_FS_ALLOW_ALL` | 默认：可挂载，API 可用 |
| `CONFIG_DEBUG_FS_DISALLOW_MOUNT` | 不可挂载但 API 可用（内部使用） |
| `CONFIG_DEBUG_FS_ALLOW_NONE` | 完全禁用 |
| `debugfs=on` | 命令行覆盖：完全启用 |
| `debugfs=no-mount` | 命令行覆盖：不可挂载 |
| `debugfs=off` | 命令行覆盖：完全禁用 |

### 5.2 Lockdown 检查

`fs/debugfs/file.c:154-169` 的 `debugfs_locked_down()`：

- 在 `open` 时调用
- **纯只读文件**（无 write/ioctl/mmap）且**全局可读** → 允许
- 其他情况 → 若 `security_locked_down(LOCKDOWN_DEBUGFS)` 为真 → 返回 `-EPERM`

`fs/debugfs/inode.c:45-56` 的 `debugfs_setattr()`：在 lockdown 下阻止修改权限。

---

## §6 挂载与初始化

`fs/debugfs/inode.c` 中的初始化流程：

```
debugfs_init()  [module_init]
  │
  ├─ register_filesystem(&debug_fs_type)
  │    └─ debug_fs_type.fill_super = debug_fill_super
  │         └─ simple_fill_super(sb, DEBUGFS_MAGIC, debug_files)
  │
  └─ sysfs_create_mount_point(kernel_kobj, "debug")
       └─ 在 /sys/kernel/ 下创建 "debug" 挂载点
```

挂载命令：`mount -t debugfs none /sys/kernel/debug`

---

## §7 arm64 特殊性

debugfs 本身是架构无关的。arm64 上有 3 处使用：

| 文件 | 用途 |
|------|------|
| `arch/arm64/kernel/debug-monitors.c:60` | `debugfs_create_bool("debug_enabled", ...)` |
| `arch/arm64/kvm/vgic/vgic-debug.c:274` | `debugfs_create_file("vgic-state", ...)` |
| `arch/arm64/mm/ptdump_debugfs.c:18` | 页表转储到 debugfs |

`arch_debugfs_dir`（`debugfs.h:46`）是架构特定的 debugfs 目录入口。

---

## §8 使用统计

| 指标 | 数值 |
|------|------|
| `debugfs_create_*` 调用总数 | 3772 处 |
| 涉及文件数 | 1917 个 |
| 最大使用者 | `drivers/gpu/drm/`（306）、`drivers/scsi/`（58）、`drivers/usb/`（59）、`net/bluetooth/`（15） |
| `CONFIG_DEBUG_FS` arm64 defconfig | 默认启用 |

---

## §9 与 sysfs / kernfs 的关系

| 维度 | debugfs | sysfs |
|------|---------|-------|
| 底层 | `simple_*` VFS 辅助函数 | kernfs |
| ABI 稳定性 | 无保证 | 严格 ABI |
| 挂载点 | `/sys/kernel/debug` | `/sys` |
| 用途 | 调试 | 设备/驱动模型 |
| 创建方式 | 手动 `debugfs_create_*` | 通过 kobject 自动 |

debugfs 和 sysfs **共享挂载点层级**（debugfs 挂在 sysfs 下的 `kernel/debug`），但实现完全独立。
