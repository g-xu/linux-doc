---
title: 总览
---

# 用户空间接口 — 总结与对比

## §1 一句话定位表

| 模块 | 一句话定位 | 独特价值 | 文档 |
|------|-----------|---------|------|
| **sysfs** | 设备模型属性导出 | 将 kobject/kset/device 的属性和层级关系暴露为 `/sys` 文件系统，有 ABI 稳定性保证 | [sysfs.md](sysfs.md) |
| **debugfs** | 调试信息导出 | 开发者调试用途，无 ABI 保证，使用 `simple_*` VFS 辅助函数，Lockdown 下可能受限 | [debugfs.md](debugfs.md) |
| **procfs** | 进程与系统信息 | 最古老的接口（~16101 行），导出进程状态（`/proc/PID/`）和系统信息（`/proc/meminfo` 等） | [实现与 API](misc-proc.md)、[/proc 文件与字段详解](proc-files.md) |
| **configfs** | 用户空间驱动的配置 | 与 sysfs 相反：目录由用户空间 `mkdir`/`rmdir` 创建，用于复杂配置场景（USB gadget、SCSI target） | [misc-config.md](misc-config.md) |
| **sysctl** | 运行时内核参数控制 | 通过 `/proc/sys` 读写内核参数，旧 API 已废弃，使用新 API `register_sysctl(path, table)` | [sysctl.md](sysctl.md) |
| **kernfs** | 伪文件系统基础框架 | 为 sysfs 提供底层层级管理，不直接面向用户空间 | [misc-kernfs.md](misc-kernfs.md) |
| **module_param** | 模块加载参数 | 模块加载时或启动时传递配置值，参数暴露在 `/sys/module/*/parameters/` | [misc-module.md](misc-module.md) |

---

## §2 选型决策

```
需要向用户空间暴露什么？
│
├─ 设备/驱动的属性（与设备模型绑定）
│   └─ sysfs（通过 kobject + attribute）
│
├─ 调试信息（无 ABI 要求）
│   └─ debugfs（手动创建文件/目录）
│
├─ 运行时可调的内核参数
│   ├─ 已有 sysctl 路径 ──→ sysctl（register_sysctl）
│   └─ 模块加载参数 ──→ module_param
│
├─ 进程或系统级状态信息
│   └─ procfs（proc_create / proc_create_seq）
│
├─ 需要用户空间主动配置复杂对象
│   └─ configfs（mkdir/rmdir 驱动配置）
│
└─ 需要自建层级文件系统
    └─ kernfs（kernfs_create_root + kernfs_create_dir_ns）
```

**关键区分**：

| 问题 | 选择 |
|------|------|
| 需要稳定 ABI？ | sysfs（有 ABI 保证）vs debugfs（无保证） |
| 需要用户空间创建目录？ | configfs（用户驱动）vs sysfs（内核驱动） |
| 需要与设备模型绑定？ | sysfs（kobject 层级）vs procfs（独立层级） |
| 需要简单参数控制？ | sysctl（`/proc/sys` 文本接口）vs module_param（`/sys/module/` 属性） |
| 需要底层文件系统引擎？ | kernfs（sysfs 的底层）vs debugfs（独立实现） |
| 需要大量属性文件？ | sysfs（ATTRIBUTE_GROUPS 宏简化）vs debugfs（逐个创建） |

---

## §3 核心对比表

### 3.1 文件系统接口对比

| 维度 | sysfs | debugfs | procfs | configfs |
|------|-------|---------|--------|----------|
| 挂载点 | `/sys` | `/sys/kernel/debug` | `/proc` | 用户指定 |
| 底层引擎 | kernfs | `simple_*` VFS 辅助 | 独立实现 | 独立实现 |
| ABI 稳定性 | 有（严格） | 无 | 部分（`/proc/PID/` 稳定） | 有 |
| 创建方式 | 内核自动（kobject 注册） | 内核手动创建 | 内核手动创建 | 用户空间 mkdir |
| 方向 | 内核→用户（只读为主） | 内核→用户（调试） | 内核→用户 | 用户→内核（配置） |
| Lockdown | 标准权限 | `debugfs_locked_down()` 限制 | 部分受限 | 标准权限 |
| 代码量 | `fs/sysfs/` 1827 行 | `fs/debugfs/` 2158 行 | `fs/proc/` 16101 行 | `fs/configfs/` 3319 行 |

### 3.2 参数控制接口对比

| 维度 | sysctl | module_param |
|------|--------|-------------|
| 暴露路径 | `/proc/sys/` | `/sys/module/*/parameters/` |
| 数据类型 | 字符串↔内核变量 | 字符串↔类型化参数（int/bool/charp 等） |
| 注册方式 | `register_sysctl(path, table)` | `module_param(name, type, perm)` |
| 回调模式 | `proc_handler(table, write, buf, lenp, ppos)` | `ops->get(buf, kp)` / `ops->set(val, kp)` |
| 代码量 | `kernel/sysctl.c` 2516 行 | `kernel/params.c` 978 行 |

### 3.3 底层框架对比

| 维度 | kernfs | VFS `simple_*` |
|------|--------|----------------|
| 使用者 | sysfs | debugfs |
| 层级管理 | kernfs_node 树 | dentry 缓存 |
| 属性文件 | kernfs_ops | file_operations |
| 命名空间 | `kernfs_node->ns` | 无 |
| 代码量 | `fs/kernfs/` 3991 行 | 分散在 `fs/libfs.c` |

---

## §4 使用规模

以下数字由 `grep` 在 6.1.y 全树上统计（`--include='*.c'`）：

### 4.1 sysfs

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `sysfs_create_group()` | 407 | `grep -r '\bsysfs_create_group\b' --include='*.c' \| wc -l` |
| `sysfs_create_file()` | 119 | `grep -r '\bsysfs_create_file\b' --include='*.c' \| wc -l` |
| `__ATTR_RO()` | 183 | `grep -r '__ATTR_RO\b' --include='*.c' --include='*.h' \| wc -l` |
| `__ATTR_RW()` | 62 | `grep -r '__ATTR_RW\b' --include='*.c' --include='*.h' \| wc -l` |
| `sysfs_emit()` | 2163 | `grep -r '\bsysfs_emit\b' --include='*.c' \| wc -l` |
| `ATTRIBUTE_GROUPS()` | 520 | `grep -r '\bATTRIBUTE_GROUPS\b' --include='*.c' \| wc -l` |

### 4.2 debugfs

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `debugfs_create_file()` | 1654 | `grep -r '\bdebugfs_create_file\b' --include='*.c' \| wc -l` |
| `debugfs_create_dir()` | 861 | `grep -r '\bdebugfs_create_dir\b' --include='*.c' \| wc -l` |
| `debugfs_create_*()` 总计 | 3450 | `grep -r '\bdebugfs_create_' --include='*.c' \| wc -l` |
| `DEFINE_DEBUGFS_ATTRIBUTE()` | 233 | `grep -r 'DEFINE_DEBUGFS_ATTRIBUTE\b' --include='*.c' --include='*.h' \| wc -l` |

### 4.3 procfs

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `proc_create()` | 96 | `grep -r '\bproc_create\b' --include='*.c' \| wc -l` |
| `proc_create_data()` | 59 | `grep -r '\bproc_create_data\b' --include='*.c' \| wc -l` |

### 4.4 其他接口

| 调用 | 次数 | grep 命令 |
|------|------|-----------|
| `register_sysctl()` | 29 | `grep -r '\bregister_sysctl\b' --include='*.c' \| wc -l` |
| `ctl_table` 引用 | 728 | `grep -r '\bctl_table\b' --include='*.c' \| wc -l` |
| `configfs_subsystem` | 37 | `grep -r '\bconfigfs_subsystem\b' --include='*.c' \| wc -l` |
| `kernfs_create_*()` | 10 | `grep -r '\bkernfs_create_' --include='*.c' \| wc -l` |
| `module_param()` | 4238 | `grep -r '\bmodule_param\b' --include='*.c' \| wc -l` |
| `MODULE_PARM_DESC()` | 5933 | `grep -r '\bMODULE_PARM_DESC\b' --include='*.c' \| wc -l` |

### 4.5 源码规模

| 子系统 | 行数 | 核心文件 |
|--------|------|---------|
| procfs | 16101 | `fs/proc/` 目录 |
| configfs | 3319 | `fs/configfs/` 目录 |
| kernfs | 3991 | `fs/kernfs/` 目录 |
| sysctl | 2516 | `kernel/sysctl.c` |
| debugfs | 2158 | `fs/debugfs/` 目录 |
| sysfs | 1827 | `fs/sysfs/` 目录 |
| module_param | 978 | `kernel/params.c` |

---

## §5 设计模式速览

### 5.1 底层实现分层

```
用户空间
    │
    ├── /sys          ← sysfs（kernfs 消费者）
    ├── /sys/kernel/debug ← debugfs（独立 simple_* 实现）
    ├── /proc         ← procfs（独立实现）
    ├── /proc/sys     ← sysctl（桥接到 procfs）
    └── /sys/module/*/parameters ← module_param（桥接到 sysfs）

内核层
    │
    ├── kernfs        ← 层级管理引擎（sysfs 使用）
    ├── kobject       ← 设备模型对象（sysfs 使用）
    └── VFS           ← 虚拟文件系统层（所有文件系统共用）
```

### 5.2 show/store 回调模式

sysfs、configfs、sysctl、module_param 都使用类似的回调模式：

| 模块 | 读回调 | 写回调 | 数据缓冲 |
|------|--------|--------|----------|
| sysfs | `show(kobj, attr, buf)` | `store(kobj, attr, buf, count)` | `PAGE_SIZE` 缓冲区 |
| configfs | `show_attribute(item, attr, buf)` | `store_attribute(item, attr, buf, count)` | `PAGE_SIZE` 缓冲区 |
| sysctl | `proc_handler(table, write=0, buf, lenp, ppos)` | 同左（`write=1`） | 用户缓冲区 |
| module_param | `ops->get(buf, kp)` | `ops->set(val, kp)` | 字符串 ↔ 类型转换 |

### 5.3 属性声明宏

sysfs 和 module_param 都提供简化属性声明的宏：

| 模块 | 宏 | 用途 | 使用量 |
|------|-----|------|--------|
| sysfs | `__ATTR_RO(name)` / `__ATTR_RW(name)` | 声明 `struct attribute` | 245 处 |
| sysfs | `ATTRIBUTE_GROUPS(name)` | 声明属性组 | 520 处 |
| module_param | `module_param(name, type, perm)` | 声明模块参数 | 4238 处 |
| debugfs | `DEFINE_DEBUGFS_ATTRIBUTE(fops, get, set, fmt)` | 声明 debugfs 属性 | 233 处 |

### 5.4 命名空间支持

sysfs（通过 kernfs）和 procfs/sysctl 都支持命名空间隔离：
- sysfs：`kernfs_node->ns` 字段 + `sysfs_create_dir_ns()`
- sysctl：`ctl_table_root->lookup()` 根据命名空间返回不同 `ctl_table_set`
- procfs：`/proc/net` 等目录 per-namespace

### 5.5 安全控制

所有接口都受 Linux Security Modules (LSM) 和 Lockdown 机制控制：
- debugfs：`debugfs_locked_down()` 在 Lockdown 模式下限制访问
- sysfs：标准文件权限 + LSM 标签
- sysctl：`dmesg_restrict` 等参数控制敏感信息访问
- procfs：`/proc/kcore` 等受 `CONFIG_PROC_KCORE` 控制

---

## §6 锁模型对比

本分类的模块主要涉及用户空间↔内核空间交互，锁模型相对简单：

| 模块 | 主要锁 | 锁类型 | 特点 |
|------|--------|--------|------|
| **sysfs** | `kernfs_node->kn_iattr_mutex` | mutex | 保护属性读写；show/store 回调在 mutex 下执行 |
| **debugfs** | `simple_transaction_lock` | mutex | 保护简单事务文件的数据缓冲 |
| **procfs** | per-proc_entry | 依赖具体实现 | 各 proc 条目自行管理锁 |
| **configfs** | `configfs_subsystem` mutex | mutex | 保护配置操作（mkdir/rmdir/show/store） |
| **sysctl** | `sysctl_mutex` | mutex | 保护 sysctl 表注册/注销 |
| **kernfs** | `kernfs_mutex` | mutex | 保护 kernfs_node 树操作 |
| **module_param** | 无全局锁 | N/A | 参数读写通过 sysfs 属性文件，锁在 sysfs 层 |

**关键特点**：
- 所有锁都是 **mutex**（非 spinlock），因为 show/store 回调可能睡眠
- 用户空间操作是**串行的**（同一属性文件的 read/write 不会并发）
- 不存在中断上下文调用这些接口的情况

---

## §7 组合使用模式

### 7.1 sysfs + kernfs（层级管理）

sysfs 是 kernfs 的主要消费者（`fs/sysfs/` 中 179 处 `kernfs_` 引用）：
1. `kernfs_create_root()` 创建 kernfs 根（5 处调用）
2. sysfs 将 kobject 层级映射为 kernfs 目录
3. `attribute` 映射为 kernfs 文件
4. show/store 回调通过 kernfs_ops 分发

### 7.2 module_param + sysfs（参数暴露）

module_param 通过 sysfs 暴露参数（`kernel/params.c` 中 105 处 sysfs/attribute/kobject 引用）：
1. `module_param(name, type, perm)` 声明参数
2. 参数自动在 `/sys/module/<module>/parameters/` 创建属性文件
3. 属性文件使用 sysfs 的 show/store 机制

### 7.3 debugfs + simple_*（轻量实现）

debugfs 使用 VFS 的 `simple_*` 辅助函数（`fs/debugfs/` 中 29 处 `simple_` 引用）：
1. `simple_pin_fs()` / `simple_release_fs()` 管理挂载
2. `simple_transaction_get()` / `simple_transaction_read()` 处理事务
3. 不需要 kernfs 的复杂层级管理

### 7.4 configfs + mkdir（用户空间驱动）

configfs 允许用户空间通过 mkdir/rmdir 创建配置对象（`fs/configfs/dir.c` 中 27 处 mkdir/rmdir 引用）：
1. 用户空间 `mkdir /sys/kernel/config/<subsystem>/<item>`
2. configfs 调用子系统的 `mkdir()` 回调
3. 属性通过 show/store 读写
4. `rmdir` 调用 `rmdir()` 回调清理

### 7.5 sysctl + procfs（参数桥接）

sysctl 桥接到 procfs：
1. `register_sysctl(path, table)` 注册参数表（29 处调用）
2. 参数在 `/proc/sys/<path>` 下创建文件
3. 读写通过 `proc_handler` 回调处理

### 7.6 procfs + 进程信息

procfs 是进程信息的唯一来源：
1. `proc_create()` 创建 proc 条目（96 处调用）
2. `/proc/PID/` 目录动态生成进程信息
3. `PDE_DATA()` 获取私有数据

---

## §8 阅读顺序建议

### 路径 A：驱动开发者（暴露设备属性）

1. **sysfs** → kobject + attribute + show/store
2. **module_param** → 模块参数声明
3. **debugfs** → 调试信息导出

### 路径 B：子系统开发者（控制面接口）

1. **sysctl** → 运行时参数控制
2. **configfs** → 复杂配置场景
3. **sysfs** → 设备模型集成

### 路径 C：理解底层机制

1. **kernfs** → 层级管理引擎
2. **sysfs** → kernfs 消费者
3. **procfs** → 独立实现对比
4. **debugfs** → simple_* 轻量实现

### 路径 D：安全与运维

1. **procfs** → 系统状态信息
2. **sysctl** → 运行时参数调整
3. **sysfs** → 设备属性查看
4. **debugfs** → 调试信息（Lockdown 限制）

---

## §9 容易混淆的关系

| 容易混淆的对 | 区别要点 | 代码依据 |
|-------------|----------|---------|
| sysfs vs debugfs | sysfs 有 ABI 保证（kernfs 底层）；debugfs 无保证（simple_* 底层） | `fs/sysfs/` vs `fs/debugfs/` |
| sysfs vs configfs | sysfs 内核驱动创建目录；configfs 用户空间 mkdir 创建 | `fs/sysfs/dir.c` vs `fs/configfs/dir.c` |
| sysfs vs procfs | sysfs 与设备模型绑定（kobject）；procfs 独立层级 | `fs/sysfs/` vs `fs/proc/` |
| sysctl vs module_param | sysctl 在 `/proc/sys/`（文本接口）；module_param 在 `/sys/module/`（属性文件） | `kernel/sysctl.c` vs `kernel/params.c` |
| kernfs vs debugfs | kernfs 是层级管理引擎（sysfs 底层）；debugfs 是独立实现 | `fs/kernfs/` vs `fs/debugfs/` |
| procfs vs sysfs | procfs 面向进程和系统信息；sysfs 面向设备模型 | `fs/proc/` 16101 行 vs `fs/sysfs/` 1827 行 |

---

## §10 文档索引

| 文档 | 覆盖模块 | 路径 |
|------|----------|------|
| sysfs 设备模型文件系统 | sysfs 属性、show/store、kobject 集成 | `output/userspace/sysfs.md` |
| debugfs 调试文件系统 | debugfs 代理机制、类型化创建、Lockdown | `output/userspace/debugfs.md` |
| sysctl 参数控制接口 | sysctl 注册、proc_handler、/proc/sys 层级 | `output/userspace/sysctl.md` |
| procfs 杂项 | procfs API、进程信息、系统状态 | `output/userspace/misc-proc.md` |
| configfs 杂项 | configfs 用户空间驱动配置 | `output/userspace/misc-config.md` |
| kernfs 杂项 | kernfs 层级管理引擎 | `output/userspace/misc-kernfs.md` |
| module_param 杂项 | 模块参数声明、解析、sysfs 桥接 | `output/userspace/misc-module.md` |
