# 杂项：module_param — 内核模块参数

> B 级文档。模块参数允许在加载模块或启动时通过命令行传递配置值，并在 `/sys/module/*/parameters/` 下暴露。

### 定位

**功能**：module_param 提供一套宏驱动的内核模块参数机制，允许开发者声明可配置变量，在模块加载时（`modprobe foo.bar=1`）或内核启动时（命令行 `foo.bar=1`）自动赋值，并在 `/sys/module/<name>/parameters/` 下生成对应的 sysfs 文件供运行时读写。它同时支持内建代码（`core_param`）和可加载模块（`module_param`），覆盖从启动早期到模块加载的不同时间窗口。

**实现方式**：每个参数声明通过宏（如 `module_param`、`module_param_named`）在 `__param` 链接段中生成一个 `struct kernel_param`（`include/linux/moduleparam.h:69-81`），包含名称、类型操作回调（`struct kernel_param_ops`）、权限和级别。模块加载后 `module_param_sysfs_setup()`（`kernel/params.c:706`）扫描该段，为每个参数创建 `param_attribute` 并通过 `sysfs_create_group()` 暴露。内建参数则在启动时由 `param_sysfs_builtin()`（`kernel/params.c:830`）扫描 `__start___param` 到 `__stop___param` 区段统一注册。标准类型（int、bool、charp 等）的 set/get 函数已在 `kernel/params.c` 中实现，开发者也可通过 `module_param_cb` 提供自定义回调。

**应用场景**：这是内核中使用最广泛的配置机制之一——全树 1751 个文件使用 `module_param`，543 个文件使用 `module_param_named`。几乎所有可加载驱动都通过它暴露调试开关、队列深度、超时值等可调参数。典型例子包括网络驱动（`drivers/net/ethernet/intel/ixgbe/ixgbe_main.c`）、串口驱动（`drivers/tty/serial/8250/8250_core.c`）、RCU 测试模块（`kernel/rcu/rcutorture.c`）和打印子系统（`kernel/printk/printk.c`）。`core_param` 被 `kernel/panic.c`、`mm/mmap.c`、`init/main.c` 等核心代码用于启动早期的关键配置。

## 1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/params.c` | 978 | 参数解析、sysfs 桥接 |
| `include/linux/moduleparam.h` | 614 | 宏、结构体定义 |

## 2 核心数据结构

**`struct kernel_param_ops`**（`include/linux/moduleparam.h:47-56`）：

```c
struct kernel_param_ops {
    unsigned int flags;
    int (*set)(const char *val, const struct kernel_param *kp);
    int (*get)(char *buffer, const struct kernel_param *kp);
    void (*free)(void *arg);
};
```

**`struct kernel_param`**（`moduleparam.h:69-81`）：

```c
struct kernel_param {
    const char *name;
    struct module *mod;
    const struct kernel_param_ops *ops;
    const u16 perm;
    s8 level;
    u8 flags;
    union {
        void *arg;
        const struct kparam_string *str;
        const struct kparam_array *arr;
    };
};
```

**`struct kparam_string`**（`moduleparam.h:86-89`）：

```c
struct kparam_string {
    unsigned int maxlen;
    char *string;
};
```

**`struct kparam_array`**（`moduleparam.h:92-99`）：

```c
struct kparam_array {
    unsigned int max;
    unsigned int elemsize;
    unsigned int *num;
    const struct kernel_param_ops *ops;
    void *elem;
};
```

## 3 核心宏

头文件：`include/linux/moduleparam.h`

| 宏 | 行号 | 说明 |
|----|------|------|
| `module_param(name, type, perm)` | 126 | 声明模块参数 |
| `module_param_named(name, value, type, perm)` | 149 | 使用不同变量名 |
| `module_param_string(name, string, len, perm)` | 355 | 字符串参数 |
| `module_param_array(name, type, num, perm)` | 499 | 数组参数 |
| `core_param(name, var, type, perm)` | 327 | 内建代码参数（非模块） |
| `module_param_cb(name, ops, arg, perm)` | 175 | 自定义回调 |
| `core_param_cb(name, ops, arg, perm)` | 194 | 自定义回调（core 级别） |
| `MODULE_PARM_DESC(name, desc)` | 33 | 参数描述（出现在 modinfo） |

标准类型：`byte`、`hexint`、`short`、`ushort`、`int`、`uint`、`long`、`ulong`、`charp`、`bool`、`invbool`。

## 4 内置 set/get 函数

`kernel/params.c` 实现的标准类型处理器：

| 函数 | 行号 | 类型 |
|------|------|------|
| `param_set_bool()` | 307 | bool |
| `param_get_bool()` | 317 | bool |
| `param_set_charp()` | 264 | char * |
| `param_get_charp()` | 287 | char * |
| `param_set_copystring()` | 510 | 字符串副本 |
| `param_get_string()` | 524 | 字符串 |
| `param_set_bint()` | 389 | 二进制 int |
| `param_set_invbool()` | 363 | 反相 bool |
| `param_set_bool_enable_only()` | 331 | 只能开启的 bool |
| `param_set_uint_minmax()` | 246 | 带边界的 uint |

## 5 /sys/module/*/parameters/ 桥接

`module_param_sysfs_setup()`（`kernel/params.c:706`）在模块初始化后被调用：

```
module_param(name, int, 0644)
  │
  ├─ 在 __param 段中创建 struct kernel_param
  │
  └─ 模块加载后：
       └─ module_param_sysfs_setup()
            └─ add_sysfs_param()  [params.c:625]
                 ├─ 创建 param_attribute（包装 kernel_param）
                 └─ sysfs_create_group()  [params.c:728]
                      └─ 在 /sys/module/<name>/parameters/ 下创建文件
```

show/store 回调（`param_attr_show` at `params.c:557`、`param_attr_store` at `params.c:573`）委托到 `ops->get`/`ops->set`。

对于内建参数，`param_sysfs_builtin()`（`params.c:830`）在启动时通过 `param_sysfs_init()`（`params.c:961`，`subsys_initcall`）扫描 `__start___param` 到 `__stop___param` 区段。

## 6 使用统计

| 宏 | 使用文件数 |
|----|-----------|
| `module_param` | 1751 |
| `module_param_named` | 543 |
| `module_param_array` | 247 |
| `module_param_string` | 74 |
| `core_param` | 20 |

## 7 参数级别（level）

`struct kernel_param` 的 `level` 字段控制参数何时被解析：

| level | 含义 |
|-------|------|
| -1 | 默认：模块加载时或启动时 |
| 1 | core 级别：在 core initcall 之前 |
| 2 | postcore 级别：在 postcore initcall 之前 |
| 3 | arch 级别：在 arch initcall 之前 |

`core_param_cb()` 和 `postcore_param_cb()` 宏用于设置这些级别。
