# kobject 与 kset

### 定位

**功能**：设备模型的基石——每个在 sysfs 里出现的目录背后都是一个 kobject，kset 把同类 kobject 组织在一起并统一处理 uevent。kobject 提供引用计数、sysfs 目录表示、父子层级关系；kset 在此基础上增加成员链表和 uevent 过滤/修饰能力。

**实现方式**：`struct kobject`（`include/linux/kobject.h:64-80`）包含名字、父指针、kset 指针、`ktype`（定义 release 回调 + sysfs_ops + 默认属性组）、`kernfs_node`（sysfs 目录项）、`kref`（引用计数）。生命周期由 `kobject_init` → `kobject_add` → `kobject_put` 管理：init 设 kref=1，add 创建 sysfs 目录并加入 kset 链表，put 到零时调 `ktype->release` 释放外层结构。kset（`kobject.h:172-177`）内嵌一个 kobject，通过 `list` + `list_lock` 管理成员，通过 `kset_uevent_ops`（filter/name/uevent 回调）过滤和修饰 uevent 通知。

**应用场景**：`kobject_get`/`kobject_put` 全树约 674 处调用。`struct device` 内嵌 kobject，是所有设备模型代码的基础。全局预定义 kobject 构成 sysfs 顶层目录：`kernel_kobj`（`/sys/kernel/`）、`mm_kobj`（`/sys/kernel/mm/`）、`power_kobj`（`/sys/power/`）、`firmware_kobj`（`/sys/firmware/`）、`hypervisor_kobj`（`/sys/hypervisor/`）。`kset_register`/`kset_create_and_add` 约 81 处调用，用于组织同类设备（如 `/sys/bus/platform/`）。

> uevent 通知机制的传输层细节（netlink 广播、kobject_action 枚举、用户态 helper）见 `notification/misc-events.md` 的 uevent 节。

---

## 1. kobject

### 1.1 核心结构

头文件：`include/linux/kobject.h`；实现：`lib/kobject.c`

```c
struct kobject {                          // kobject.h:64-80
    const char          *name;            // 在 sysfs 中显示的名字
    struct list_head    entry;            // 挂入所属 kset 的链表
    struct kobject      *parent;          // 父 kobject（决定 sysfs 层级）
    struct kset         *kset;            // 所属 kset（可为 NULL）
    const struct kobj_type *ktype;        // 类型：release 回调 + sysfs_ops + 默认属性组
    struct kernfs_node  *sd;              // 对应的 sysfs 目录项
    struct kref         kref;             // 引用计数
    unsigned int state_initialized:1;
    unsigned int state_in_sysfs:1;
    unsigned int state_add_uevent_sent:1;
    unsigned int state_remove_uevent_sent:1;
    unsigned int uevent_suppress:1;
};
```

`struct kobj_type` 定义在 `kobject.h:120-127`：

| 字段 | 作用 |
|------|------|
| `release` | 引用计数归零时调用，释放包含 kobject 的外层结构 |
| `sysfs_ops` | sysfs 读/写回调（`show` / `store`） |
| `default_groups` | 创建 sysfs 目录时自动创建的属性组 |
| `child_ns_type` / `namespace` | 命名空间支持（容器场景） |
| `get_ownership` | sysfs 文件属主定制 |

### 1.2 生命周期

```
kobject_init(kobj, ktype)     // 初始化 kref=1，标记 state_initialized
  → kobject_add(kobj, parent, fmt, ...)
      → kobject_set_name_vargs()  // 设置名字
      → kobject_add_internal()
          → kobj_kset_join()      // 如果有关联 kset，加入链表
          → create_dir()          // 创建 sysfs 目录 + 默认属性
  ... 使用中 ...
  → kobject_put()                 // kref_put → 计数归零时调 kobject_cleanup()
      → __kobject_del()           // 从 sysfs 移除
      → ktype->release()          // 释放外层结构
```

关键规则（`lib/kobject.c:325-352`）：
- `kobject_init()` 之后**必须**用 `kobject_put()` 清理，**不能**直接 `kfree()`
- `kobject_add()` 返回错误后同样必须 `kobject_put()`
- 名字中的 `/` 会被替换为 `!`（`lib/kobject.c:275-284`）

### 1.3 常用 API

| API | 说明 | 备注 |
|-----|------|------|
| `kobject_init_and_add` | `init` + `add` 合一 | 出错仍需 `kobject_put` |
| `kobject_create_and_add` | 动态分配 + 注册 | 内部用 `dynamic_kobj_ktype`，自带 release |
| `kobject_get` / `kobject_put` | 引用计数 +/-1 | `put` 到零触发 release |
| `kobject_get_unless_zero` | 仅在计数非零时 +1 | 避免竞态下拿到已释放对象 |
| `kobject_rename` | 改名，发 `KOBJ_MOVE` uevent | 调用者负责互斥 |
| `kobject_move` | 移到新 parent 下 | 同上 |
| `kobject_del` | 从 sysfs  unlink，不释放 | 通常由 `kobject_put` 自动触发 |
| `kobject_get_path` | 分配内存拼出 sysfs 路径 | 调用者 `kfree` |

全树使用量：`kobject_get` + `kobject_put` 约 667 处（`.c` 文件）。

### 1.4 便捷宏

`struct kobj_attribute`（`kobject.h:143-149`）是 `struct attribute` 的扩展，带 `show` / `store` 回调。配合 `__ATTR` / `__ATTR_RW` 使用：

```c
static struct kobj_attribute my_attr = __ATTR_RW(my_show, my_store);
```

`kobj_sysfs_ops`（`lib/kobject.c:836-840`）把 `attribute` 转成 `kobj_attribute` 再调回调。

---

## 2. kset

### 2.1 核心结构

头文件：`include/linux/kobject.h:172-177`；实现：`lib/kobject.c`

```c
struct kset {
    struct list_head        list;        // 所有成员 kobject 的链表
    spinlock_t              list_lock;   // 保护链表迭代
    struct kobject          kobj;        // 内嵌 kobject（kset 自身在 sysfs 中也是一个目录）
    const struct kset_uevent_ops *uevent_ops;  // uevent 过滤/命名/附加环境变量
};
```

**关键关系**：kset **内嵌**了一个 kobject。所以 kset 本身在 sysfs 中也表现为一个目录，其下的成员 kobject 出现在该目录里。

### 2.2 kset vs kobject 的区别

| 维度 | kobject | kset |
|------|---------|------|
| 本质 | 单个 sysfs 目录 + 引用计数 | 一组 kobject 的集合 |
| 内嵌关系 | 被 `struct device` 等内嵌 | 内嵌了一个 kobject |
| uevent | 不直接处理 | 通过 `uevent_ops` 过滤/修饰 |
| 典型用途 | 表示单个对象 | 表示一类对象的容器（如 `/sys/bus/platform/`） |

### 2.3 API

| API | 说明 | 备注 |
|-----|------|------|
| `kset_create_and_add` | 动态创建 kset 并注册到 sysfs | 最常用入口 |
| `kset_register` | 注册已初始化的 kset | 需先设好 `kobj.ktype` |
| `kset_unregister` | 注销并释放引用 | 内部调 `kobject_del` + `kobject_put` |
| `kset_find_obj` | 在 kset 链表中按名字查找 | 找到后返回带引用的 kobject |
| `kset_get` / `kset_put` | kset 的引用计数操作 | 实际操作内嵌的 kobj |

全树使用量：`kset_register` + `kset_create_and_add` 约 79 处。

### 2.4 uevent 机制

`struct kset_uevent_ops`（`kobject.h:137-141`）：

| 回调 | 作用 |
|------|------|
| `filter` | 决定是否为此 kobject 发送 uevent |
| `name` | 返回 uevent 中的子系统名 |
| `uevent` | 向环境变量中添加额外键值对 |

uevent 通过 netlink socket（`NETLINK_KOBJECT_UEVENT`）发送到用户空间，由 udev 等处理。动作类型定义在 `enum kobject_action`（`kobject.h:53-62`）：`KOBJ_ADD` / `REMOVE` / `CHANGE` / `MOVE` / `ONLINE` / `OFFLINE` / `BIND` / `UNBIND`。

---

## 3. 全局预定义 kobject

`include/linux/kobject.h:208-217` 声明了几个全局 kobject，供子系统挂载：

| 变量 | sysfs 路径 | 用途 |
|------|-----------|------|
| `kernel_kobj` | `/sys/kernel/` | 内核参数、调试开关 |
| `mm_kobj` | `/sys/kernel/mm/` | 内存管理相关 |
| `hypervisor_kobj` | `/sys/hypervisor/` | 虚拟化支持 |
| `power_kobj` | `/sys/power/` | 电源管理 |
| `firmware_kobj` | `/sys/firmware/` | 固件信息 |

---

## 4. 使用统计

| 模式 | 调用点数 | 说明 |
|------|---------|------|
| `kobject_get` / `kobject_put` | 674 | 引用计数核心 |
| `kset_register` / `kset_create_and_add` | 81 | kset 创建 |
| `kobject_create_and_add` | 广泛使用 | 快速创建独立 sysfs 目录 |
