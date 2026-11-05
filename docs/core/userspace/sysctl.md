# sysctl — 内核参数控制接口

## §0 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/sysctl.c` | 2516 | `proc_handler` 实现、共享常量、EXPORT_SYMBOL |
| `fs/proc/proc_sysctl.c` | 1953 | `/proc/sys` VFS 层、注册/注销、rbtree 管理 |
| `include/linux/sysctl.h` | 314 | 全部公开数据结构和 API 声明 |

头文件：`include/linux/sysctl.h`；实现：`kernel/sysctl.c` + `fs/proc/proc_sysctl.c`。

---

## §1 定位

sysctl 提供 `/proc/sys` 下的层级参数接口，允许用户空间在运行时读取和修改内核行为。每个叶子节点是一个文件，内容是该参数的文本表示。

sysctl 有两套注册 API：
- **新 API（推荐）**：`register_sysctl(path, table)` — 扁平路径字符串
- **旧 API（已废弃）**：`register_sysctl_table(table)` — 使用 `child` 指针嵌套

---

## §2 核心数据结构

### 2.1 `struct ctl_table`（`include/linux/sysctl.h:135-145`）

```c
struct ctl_table {
    const char *procname;       // /proc/sys 下的文件名
    void *data;                 // 指向实际数据
    int maxlen;                 // 数据最大长度
    umode_t mode;               // 权限
    struct ctl_table *child;    // 已废弃：子表指针
    proc_handler *proc_handler; // 读写回调
    struct ctl_table_poll *poll; // poll/select 支持
    void *extra1;               // 最小值指针（或自定义数据）
    void *extra2;               // 最大值指针
} __randomize_layout;
```

以 NULL `{}` 条目终止数组。

### 2.2 `struct ctl_table_header`（`sysctl.h:154-171`）

```c
struct ctl_table_header {
    union {
        struct {
            struct ctl_table *ctl_table;
            int used;
            int count;
            int nreg;
        };
        struct rcu_head rcu;
    };
    struct completion *unregistering;
    struct ctl_table *ctl_table_arg;
    struct ctl_table_root *root;
    struct ctl_table_set *set;
    struct ctl_dir *parent;
    struct ctl_node *node;
    struct hlist_head inodes;
};
```

维护已注册表的生命周期：引用计数、所属 root/set、父目录、rbtree 节点数组。

### 2.3 `struct ctl_dir`（`sysctl.h:173-177`）

```c
struct ctl_dir {
    struct ctl_table_header header;  // 必须在开头
    struct rb_root root;             // 子条目 rbtree
};
```

目录节点。`rb_root` 存储该目录下所有子条目（按 `procname` 排序）。

### 2.4 `struct ctl_table_root`（`sysctl.h:184-190`）

```c
struct ctl_table_root {
    struct ctl_table_set default_set;
    struct ctl_table_set *(*lookup)(struct ctl_table_root *);
    void (*set_ownership)(struct ctl_table_header *, kuid_t *, kgid_t *);
    int (*permissions)(struct ctl_table_header *, struct ctl_table *);
};
```

顶层 root，支持命名空间隔离。`lookup()` 根据当前命名空间返回对应的 `ctl_table_set`。

### 2.5 `struct ctl_table_poll`（`sysctl.h:117-120`）

```c
struct ctl_table_poll {
    atomic_t event;
    wait_queue_head_t wait;
};
```

用于 `poll()`/`select()` 支持。参数变化时调用 `proc_sys_poll_notify()` 递增 `event`。

---

## §3 共享常量

`kernel/sysctl.c:86-89` 定义的全局常量数组，用作 `extra1`/`extra2` 的 min/max 边界：

| 宏 | 值 | 用途 |
|----|---|------|
| `SYSCTL_ZERO` | `&sysctl_vals[0]` | 最小值 0 |
| `SYSCTL_ONE` | `&sysctl_vals[1]` | 最小值 1 |
| `SYSCTL_TWO` | `&sysctl_vals[2]` | — |
| `SYSCTL_ONE_HUNDRED` | `&sysctl_vals[5]` | — |
| `SYSCTL_ONE_THOUSAND` | `&sysctl_vals[7]` | — |
| `SYSCTL_INT_MAX` | `&sysctl_vals[9]` | `INT_MAX` |
| `SYSCTL_MAXOLDUID` | `&sysctl_vals[10]` | 65535 |
| `SYSCTL_NEG_ONE` | `&sysctl_vals[11]` | -1 |
| `SYSCTL_LONG_ZERO` | `&sysctl_long_vals[0]` | long 0 |
| `SYSCTL_LONG_ONE` | `&sysctl_long_vals[1]` | long 1 |
| `SYSCTL_LONG_MAX` | `&sysctl_long_vals[2]` | `LONG_MAX` |

---

## §4 API 速览

### 4.1 注册与注销

| API | 实现位置 | 说明 | 备注 |
|-----|----------|------|------|
| `register_sysctl(path, table)` | `proc_sysctl.c:1419` | 推荐 API | 扁平路径 |
| `register_sysctl_init(path, table)` | `proc_sysctl.c:1446` | `__init` 变体 | 失败只打日志 |
| `register_sysctl_mount_point(path)` | `proc_sysctl.c:45` | 注册空挂载点 | — |
| `register_sysctl_table(table)` | `proc_sysctl.c:1667` | **已废弃** | 使用 `child` 嵌套 |
| `register_sysctl_paths(path[], table)` | `proc_sysctl.c:1650` | **已废弃** | 使用 `ctl_path` 数组 |
| `unregister_sysctl_table(header)` | `proc_sysctl.c:1744` | 注销 | 释放引用、递归清理 |

### 4.2 内部注册

| API | 实现位置 | 说明 |
|-----|----------|------|
| `__register_sysctl_table(set, path, table)` | `proc_sysctl.c:1330` | 核心引擎：遍历路径、创建 `ctl_dir`、插入 rbtree |
| `__register_sysctl_base(base_table)` | `proc_sysctl.c:1675` | 内部：创建顶层目录 |
| `DECLARE_SYSCTL_BASE(name, table)` | `sysctl.h:199` | 宏：声明基础表 |

### 4.3 proc_handler 回调

所有 handler 共享签名：`int (*)(struct ctl_table *, int write, void *buffer, size_t *lenp, loff_t *ppos)`

| Handler | 实现位置 | 处理的数据类型 |
|---------|----------|---------------|
| `proc_dostring()` | `sysctl.c:260` | 字符串 |
| `proc_dobool()` | `sysctl.c:718` | bool（0/1） |
| `proc_dointvec()` | `sysctl.c:738` | int 数组 |
| `proc_douintvec()` | `sysctl.c:778` | unsigned int 数组 |
| `proc_dointvec_minmax()` | `sysctl.c:884` | int 数组 + 边界检查 |
| `proc_douintvec_minmax()` | `sysctl.c:953` | unsigned int + 边界 |
| `proc_dou8vec_minmax()` | `sysctl.c:981` | u8 数组 + 边界 |
| `proc_doulongvec_minmax()` | `sysctl.c:1140` | unsigned long + 边界 |
| `proc_doulongvec_ms_jiffies_minmax()` | `sysctl.c:1163` | ms ↔ jiffies 转换 + 边界 |
| `proc_dointvec_jiffies()` | `sysctl.c:1284` | int（jiffies 单位） |
| `proc_dointvec_ms_jiffies_minmax()` | `sysctl.c:1291` | int ms ↔ jiffies + 边界 |
| `proc_dointvec_userhz_jiffies()` | `sysctl.c:1317` | int USER_HZ ↔ jiffies |
| `proc_dointvec_ms_jiffies()` | `sysctl.c:1340` | int ms ↔ jiffies |
| `proc_do_large_bitmap()` | `sysctl.c:1386` | 大位图（范围列表格式） |
| `proc_do_static_key()` | `sysctl.c:1605` | static key 切换 |

---

## §5 /proc/sys 层级映射

```
proc_sys_init()  [fs/proc/proc_sysctl.c:1788]
  │
  ├─ 创建 /proc/sys proc_dir_entry
  │    └─ proc_sys_dir_operations / proc_sys_dir_file_operations
  │
  └─ sysctl_init_bases()  [kernel/sysctl.c:2491]
       ├─ register_sysctl_base(kernel)  → /proc/sys/kernel/
       ├─ register_sysctl_base(vm)      → /proc/sys/vm/
       ├─ register_sysctl_base(debug)   → /proc/sys/debug/
       └─ register_sysctl_base(dev)     → /proc/sys/dev/
```

每个 `ctl_table` 条目的 `procname` 成为目录下的文件名。`ctl_dir` 中的 rbtree 按 `procname` 索引所有子条目。

查找流程：
```
用户 open("/proc/sys/kernel/hostname")
  │
  └─ proc_sys_lookup()
       ├─ 遍历路径 "kernel" → 找到 ctl_dir
       ├─ rbtree 查找 "hostname" → 找到 ctl_node
       └─ 创建 inode（proc_sys_inode_operations）
```

---

## §6 使用示例

```c
#include <linux/sysctl.h>

static int my_value = 42;
static struct ctl_table my_table[] = {
    {
        .procname   = "my_value",
        .data       = &my_value,
        .maxlen     = sizeof(int),
        .mode       = 0644,
        .proc_handler = proc_dointvec_minmax,
        .extra1     = SYSCTL_ZERO,
        .extra2     = SYSCTL_ONE_HUNDRED,
    },
    { }
};

static int __init my_init(void)
{
    register_sysctl_init("my_subsystem", my_table);
    return 0;
}
```

用户空间：
```bash
cat /proc/sys/my_subsystem/my_value    # 读
echo 50 > /proc/sys/my_subsystem/my_value  # 写
```

---

## §7 arm64 特殊性

arm64 上有 5 处 sysctl 注册：

| 文件 | 路径 | 用途 |
|------|------|------|
| `arch/arm64/kernel/fpsimd.c:576` | `abi/` | SVE 默认向量长度 |
| `arch/arm64/kernel/fpsimd.c:600` | `abi/` | SME 默认向量长度 |
| `arch/arm64/kernel/perf_event.c:1236` | `kernel/` | PMU 用户访问控制 |
| `arch/arm64/kernel/process.c:731` | `abi/` | 标记地址 ABI |
| `arch/arm64/kernel/armv8_deprecated.c:259` | `abi/` | 废弃指令模拟 |

自定义 `proc_handler`：`vec_proc_do_default_vl()`（`fpsimd.c:536`）、`armv8pmu_proc_user_access_handler()`（`perf_event.c:1207`）、`emulation_proc_handler()`（`armv8_deprecated.c:206`）。

---

## §8 使用统计

| 指标 | 数值 |
|------|------|
| `register_sysctl()` 调用者 | 99 处 |
| `register_sysctl_init()` 调用者 | 42 处 |
| `register_sysctl_table()`（已废弃）调用者 | 103 处 |
| `struct ctl_table` 定义数 | 758 处 |
| `CONFIG_SYSCTL` arm64 defconfig | 默认启用 |
