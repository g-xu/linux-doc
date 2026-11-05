# 杂项：livepatch — 内核热补丁框架

> B 级文档。允许在不重启内核的情况下替换函数实现。基于 ftrace 实现，使用一致性模型保证安全过渡。

### 定位

**功能**：提供内核热补丁能力——在不重启系统的情况下替换运行中内核的函数实现。用于紧急修复安全漏洞或关键 bug，同时通过一致性模型（栈检查过渡）保证所有 CPU 安全过渡到新的函数实现。

**实现方式**：基于 ftrace 的 `FTRACE_OPS_FL_IPMODIFY` 标志，在目标函数入口拦截并修改指令指针（arm64 上修改 `pt_regs->pc`）跳转到替代函数（`kernel/livepatch/patch.c`）。一致性模型通过栈检查过渡保证安全：所有任务在系统调用返回、中断返回、调度切换时被检查，确认不再执行旧函数后才完成过渡（`kernel/livepatch/transition.c`）。影子变量（`klp_shadow_*`）机制允许补丁扩展数据结构而不修改原始结构体定义，解决 ABI 兼容性问题。

**应用场景**：`klp_enable_patch` 在 12 个文件中被调用，主要是 `lib/livepatch/` 下的自测试模块（`test_klp_livepatch.c`、`test_klp_callbacks_demo.c`、`test_klp_state.c` 等）和 `samples/livepatch/` 下的示例模块（`livepatch-sample.c`、`livepatch-shadow-fix1.c` 等）。实际生产使用通常是树外的 livepatch 模块。arm64 通过 `arch/arm64/Kconfig` 中的 `select HAVE_LIVEPATCH` 支持此功能。

## 1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/livepatch/core.c` | 1,291 | 核心：注册/启用/禁用/补丁 |
| `kernel/livepatch/transition.c` | 666 | 一致性模型：栈检查与过渡 |
| `kernel/livepatch/state.c` | 119 | 状态管理 |
| `kernel/livepatch/shadow.c` | 299 | 影子变量 |
| `kernel/livepatch/patch.c` | 289 | 底层 ftrace 操作 |
| `include/linux/livepatch.h` | 250+ | 对外 API 与数据结构 |

总计约 2664 行。

## 2 核心数据结构

### 2.1 `struct klp_func`（`include/linux/livepatch.h:55-77`）

```c
struct klp_func {
    /* external */
    const char *old_name;       // 被替换的函数名
    void *new_func;             // 替代函数指针
    const char *old_sympos;     // 符号位置（消歧义）

    /* internal */
    struct ftrace_ops *fops;    // ftrace 操作结构
    unsigned long old_addr;     // 解析后的原函数地址
    ...
};
```

### 2.2 `struct klp_object`（`include/linux/livepatch.h:116-129`）

```c
struct klp_object {
    /* external */
    const char *name;           // 模块名（NULL = vmlinux）
    struct klp_func *funcs;     // 该对象中要补丁的函数列表
    struct klp_callbacks callbacks; // 回调

    /* internal */
    struct kobject kobj;
    struct list_head func_list;
    struct list_head node;
    struct module *mod;         // 关联的加载模块
    bool dynamic;               // 动态分配的 nop 函数
    bool patched;               // 是否已激活
};
```

### 2.3 `struct klp_patch`（`include/linux/livepatch.h:157-172`）

```c
struct klp_patch {
    /* external */
    struct module *mod;         // livepatch 模块
    struct klp_object *objs;    // 要补丁的对象列表
    struct klp_state *states;   // 系统状态修改
    bool replace;               // 是否替换所有已活跃的补丁

    /* internal */
    struct list_head list;      // 全局补丁链表
    struct kobject kobj;
    struct list_head obj_list;  // 动态对象列表
    bool enabled;               // 是否启用
    bool forced;                // 是否经历了强制过渡
    ...
};
```

### 2.4 `struct klp_state`（`include/linux/livepatch.h:137-141`）

```c
struct klp_state {
    unsigned long id;           // 状态标识符
    unsigned int version;       // 版本
    void *data;                 // 自定义数据
};
```

## 3 核心 API

头文件：`include/linux/livepatch.h`；实现：`kernel/livepatch/core.c`

**补丁生命周期**

| API | 说明 | 备注 |
|-----|------|------|
| `klp_enable_patch(patch)` | 注册并启用补丁 | 补丁模块的 `init` 函数中调用 |
| `klp_module_coming(mod)` | 模块加载通知 | 由模块加载器自动调用 |
| `klp_module_going(mod)` | 模块卸载通知 | 由模块加载器自动调用 |

**状态管理**

| API | 说明 | 备注 |
|-----|------|------|
| `klp_get_state(patch, id)` | 获取补丁关联的状态 | 用于累积状态变更 |
| `klp_get_prev_state(id)` | 获取前一个补丁的同 ID 状态 | 用于状态协调 |

**影子变量**

| API | 说明 | 备注 |
|-----|------|------|
| `klp_shadow_get(obj, id)` | 获取影子数据 | — |
| `klp_shadow_alloc(obj, id, size, gfp_flags, ctor, ctor_data)` | 分配影子数据 | 可带构造函数 |
| `klp_shadow_get_or_alloc(obj, id, size, gfp_flags, ctor, ctor_data)` | 获取或分配 | — |
| `klp_shadow_free(obj, id, dtor)` | 释放影子数据 | 可带析构函数 |
| `klp_shadow_free_all(id, dtor)` | 释放所有匹配 ID 的影子数据 | — |

**任务管理**

| API | 说明 | 备注 |
|-----|------|------|
| `klp_copy_process(child)` | fork 时复制补丁状态 | 由 `copy_process()` 调用 |
| `klp_update_patch_state(task)` | 更新任务的补丁状态 | 由信号返回路径调用 |

## 4 实现机制

### 4.1 函数替换原理

livepatch 使用 **ftrace 的 `FTRACE_OPS_FL_IPMODIFY`** 标志替换函数入口：

```
原始函数调用 → ftrace 拦截点
  │
  ├─ 无补丁 → 直接跳转到原函数（NOP）
  └─ 有补丁 → 修改 IP 为 new_func 地址
```

`kernel/livepatch/patch.c` 中创建 `ftrace_ops`，设置 `FTRACE_OPS_FL_IPMODIFY | FTRACE_OPS_FL_SAVE_REGS`，在回调中修改 `pt_regs->pc`（arm64）指向替代函数。

### 4.2 一致性模型

livepatch 使用 **堆栈检查过渡**（stack-check transition）保证安全：

1. 启用补丁时，所有 CPU 进入过渡状态
2. 每个任务在以下时机点被检查：
   - 从系统调用返回时
   - 从中断返回时
   - 调度切换时
3. `klp_check_stack()` 验证任务栈上是否还在执行旧函数
4. 当所有任务都已过渡到新函数 → 过渡完成
5. 如果超时（默认 15 秒）→ 可强制过渡或回滚

### 4.3 补丁模块结构

一个 livepatch 模块的典型结构：

```c
static struct klp_func funcs[] = {
    {
        .old_name = "target_function",
        .new_func = patched_function,
    },
    { }
};

static struct klp_object objs[] = {
    {
        .name = NULL,  // vmlinux
        .funcs = funcs,
    },
    { }
};

static struct klp_patch patch = {
    .mod = THIS_MODULE,
    .objs = objs,
};

static int __init patch_init(void)
{
    return klp_enable_patch(&patch);
}

static void __exit patch_exit(void)
{
    // 补丁自动禁用
}
```

### 4.4 影子变量

用于补丁需要修改数据结构布局的场景：

- 补丁不能直接修改已有结构体（ABI 兼容性问题）
- 通过 `klp_shadow_alloc()` 为对象附加额外数据
- 补丁函数通过 `klp_shadow_get()` 访问扩展数据
- 生命周期与补丁绑定：补丁卸载时自动清理

### 4.5 状态协调

多个补丁可能修改同一系统状态。`klp_state` 提供协调机制：

- 每个补丁声明自己修改的状态 ID 和版本
- `klp_get_prev_state()` 查询前一个补丁的同 ID 状态
- 新补丁可以基于前一个状态做增量修改
- `replace` 补丁替换所有旧补丁，继承它们的状态

## 5 arm64 特殊性

- ftrace 在 arm64 上使用 `BRK` 指令或 patchable function entry（见 `ftrace.md`）
- `pt_regs->pc` 修改在 `kernel/livepatch/patch.c` 的回调中完成
- 栈检查使用 `arch_stack_walk()` → 帧指针回溯（见 `stacktrace.md`）
- arm64 支持 `CONFIG_LIVEPATCH`（`arch/arm64/Kconfig` 中 `select HAVE_LIVEPATCH`）

## 6 使用统计

- `klp_enable_patch` 在树内约 16 处调用（主要是 livepatch 自测试和示例模块）
- `klp_shadow_*` 函数约 20 处引用
- `struct klp_patch` 在树内约 8 处定义（测试模块 + 示例）
- `CONFIG_LIVEPATCH` 在 arm64 defconfig 中默认关闭
- 实际生产使用通常是树外模块
