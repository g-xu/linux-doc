# 杂项：KMSAN、KCSAN、UBSAN — 运行时错误检测器

> B 级文档。三个编译器辅助的运行时检测工具，各自检测一类内存/并发/语义错误。与 KASAN（见 `kasan.md`）同属"sanitizer 家族"，但检测目标和实现机制完全不同。

## 1 KMSAN — 未初始化内存检测

### 定位

**功能**：检测内核中使用未初始化内存的错误。通过 Clang 的 `-fsanitize=memory` 插桩，在每次内存读取前自动检查该字节是否已被初始化，发现使用未初始化值时输出包含 origin 链（追溯到分配点）的诊断报告。

**实现方式**：为每个内存字节维护两份元数据——shadow memory（每字节 1 bit，0=已初始化，1=未初始化）和 origin memory（每 4 字节块 32-bit origin ID，记录未初始化内存的产生位置即栈回溯哈希）。编译器（Clang `-fsanitize=memory`）在每次内存读取前插入 shadow 检查，条件分支的条件值、函数返回值、系统调用参数是关键检查点。shadow 和 origin 内存在启动早期通过 `memblock` 分配（`mm/kmsan/shadow.c`）。

**应用场景**：`kmsan_` 系列函数在 14 个文件的 30 处被显式调用，主要消费者是内存管理子系统：`mm/page_alloc.c`（页面分配通知）、`mm/slub.c`（slab 分配通知）、`mm/kmsan/hooks.c`（slab/page 钩子，7 处）、`mm/memory.c`（页面拷贝元数据传递）、`kernel/entry/common.c`（入口路径，4 处）、`kernel/kcov.c`（覆盖率工具交互）。大量检测点由编译器自动插入，不体现在源码中。

### 1.1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `mm/kmsan/core.c` | 461 | 核心：shadow/origin 查询与更新 |
| `mm/kmsan/shadow.c` | 308 | shadow 内存映射管理 |
| `mm/kmsan/instrumentation.c` | 308 | 编译器插桩回调 |
| `mm/kmsan/hooks.c` | 425 | slab / page 分配钩子 |
| `mm/kmsan/init.c` | 235 | 启动初始化 |
| `mm/kmsan/report.c` | 219 | 错误报告 |
| `mm/kmsan/kmsan.h` | 内部头文件 | 内部数据结构 |
| `include/linux/kmsan.h` | 公开 API | 子系统使用的接口 |

总计 2167 行（不含测试）。

### 1.2 核心概念

KMSAN 为每个内存字节维护两份元数据：

- **Shadow memory**：每个字节对应 1 bit shadow。0 = 已初始化，1 = 未初始化
- **Origin memory**：每个 4 字节块对应 32-bit origin ID，记录"这块未初始化内存是在哪里产生的"（栈回溯哈希）

```
实际地址:  0xFFFF_0000_0000_0000
Shadow:    计算方式类似 KASAN，但 scale = 1（每 8 字节实际内存 → 1 字节 shadow）
Origin:    与 shadow 同比例，每 4 字节 shadow → 4 字节 origin
```

### 1.3 关键常量

`mm/kmsan/kmsan.h:22-29`：

| 常量 | 值 | 含义 |
|------|---|------|
| `KMSAN_ALLOCA_MAGIC_ORIGIN` | `0xabcd0100` | alloca 区域的 origin 魔数 |
| `KMSAN_CHAIN_MAGIC_ORIGIN` | `0xabcd0200` | origin 链魔数 |
| `KMSAN_POISON_NOCHECK` | 0 | 不检查 |
| `KMSAN_POISON_CHECK` | 1 | 检查 |
| `KMSAN_POISON_FREE` | 2 | 释放时标记 |
| `KMSAN_ORIGIN_SIZE` | 4 | 每个 origin 条目 4 字节 |
| `KMSAN_MAX_ORIGIN_DEPTH` | 7 | origin 回溯最大深度 |

### 1.4 公开 API

头文件：`include/linux/kmsan.h`

| API | 说明 | 备注 |
|-----|------|------|
| `kmsan_init_shadow()` | 启动时初始化 shadow 内存 | `__init` |
| `kmsan_init_runtime()` | 启用 KMSAN 运行时 | `__init` |
| `kmsan_alloc_page(page, order, flags)` | 通知分配了页面 | 标记为未初始化 |
| `kmsan_free_page(page, order)` | 通知释放页面 | 标记为未初始化 |
| `kmsan_copy_page_meta(dst, src)` | 复制页面元数据 | fork / page cache 使用 |
| `kmsan_slab_alloc(s, obj, flags)` | 通知 slab 分配 | — |
| `kmsan_slab_free(s, obj)` | 通知 slab 释放 | — |
| `kmsan_kmalloc_large(ptr, size, flags)` | 大对象分配通知 | — |
| `kmsan_check_memory(addr, size, name)` | 显式检查内存是否已初始化 | 手动触发报告 |
| `kmsan_unpoison(ptr, size)` | 标记为已初始化 | 用于硬件 DMA 写入等场景 |
| `kmsan_poison(ptr, size, origin)` | 标记为未初始化 | — |
| `kmsan_get_metadata(addr, is_origin)` | 获取 shadow 或 origin | 内部使用 |
| `kmsan_task_create(task)` | 为新任务初始化 KMSAN 状态 | — |
| `kmsan_task_exit(task)` | 任务退出时清理 | — |

### 1.5 检测原理

编译器（Clang `-fsanitize=memory`）在每次内存读取前插入检查：

```c
// 源代码: int x = *ptr;
// 编译器生成:
if (kmsan_get_shadow(ptr)[0] != 0)
    __kmsan_warn_uninit_use(...);  // 报告使用未初始化值
*x = ...;  // 同时更新 shadow
```

检查点：
- 条件分支（`if` / `switch`）的条件值
- 函数返回值（返回值未初始化时调用方使用）
- 系统调用参数（传递给用户空间或硬件的值）

### 1.6 arm64 支持

- KMSAN 在 arm64 上需要 `CONFIG_KMSAN=y` + Clang 编译
- shadow 内存映射在 `mm/kmsan/shadow.c` 中通过 `memblock` 在启动早期分配
- arm64 的 `arch/arm64/mm/kasan_init.c` 中的早期页表建立逻辑也被 KMSAN 复用

### 1.7 使用统计

- `kmsan_` 符号在树内 503 处引用
- 主要消费者：`mm/`（slab/page 钩子）、`kernel/fork.c`（任务创建）、编译器自动插桩

---

## 2 KCSAN — 数据竞争检测

### 定位

**功能**：检测内核中的数据竞争（data race）。通过编译器插桩在每次内存访问前插入检查，使用软件观察点（watchpoint）池概率采样，当两个 CPU 同时访问同一地址且至少一个是写操作时报告竞争对及其栈回溯。

**实现方式**：编译器（`-fsanitize=thread`）在每次内存访问前插入 `__kcsan_check_access(ptr, size, type)`（`kernel/kcsan/core.c`）。运行时从观察点池中分配 slot 监控该地址，使用概率采样（默认约 1/1000）控制开销。支持 `KCSAN_ACCESS_WRITE`、`KCSAN_ACCESS_ATOMIC`、`KCSAN_ACCESS_ASSERT`、`KCSAN_ACCESS_SCOPED` 等访问类型标志，可标记原子访问和作用域抑制竞争报告。每个任务维护 `struct kcsan_ctx` 上下文（`include/linux/kcsan.h:22-60`），跟踪观察点使用状态、嵌套深度和原子区域标记。

**应用场景**：`__kcsan_check_access` 和 `kcsan_` 系列函数在 5 个文件中被显式调用（`kernel/kcsan/core.c`、`mm/slub.c`、`mm/slab.c`、`mm/kfence/core.c`、`init/main.c`），但主要消费者是编译器自动插桩。`init/init_task.c` 中初始化 init 任务的 KCSAN 上下文。手动调用场景包括 `mm/kmemleak.c`（kmemleak 扫描时抑制竞争报告）和 `kernel/kcsan/debugfs.c`（debugfs 运行时参数调整）。

### 2.1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `kernel/kcsan/core.c` | 1371 | 核心：软件观察点 + 竞争检测 |
| `kernel/kcsan/report.c` | 715 | 竞争报告 |
| `kernel/kcsan/debugfs.c` | 273 | debugfs 接口 |
| `kernel/kcsan/selftest.c` | 272 | 自测试 |
| `kernel/kcsan/kcsan.h` | 内部头文件 | — |
| `include/linux/kcsan-checks.h` | 公开检查 API | 手动检查与屏障插桩 |
| `include/linux/kcsan.h` | 上下文结构 | `struct kcsan_ctx` |

总计 2969 行（不含测试）。

### 2.2 核心概念

KCSAN 使用 **软件数据断点（watchpoint）** 检测数据竞争：

1. 编译器在每次内存访问前插入 `__kcsan_check_access(ptr, size, type)`
2. 运行时从观察点池中分配一个 slot 监控该地址
3. 当另一个 CPU 同时访问同一地址时，检测到竞争
4. 报告竞争对（地址、两个访问的栈回溯、访问类型）

**关键设计**：不是所有访问都被监控——使用概率采样（默认约 1/1000）来控制开销。

### 2.3 访问类型标志

`include/linux/kcsan-checks.h:17-22`：

| 标志 | 值 | 含义 |
|------|---|------|
| `KCSAN_ACCESS_WRITE` | `1 << 0` | 写访问（不设则为读） |
| `KCSAN_ACCESS_COMPOUND` | `1 << 1` | 复合读写（`x++`） |
| `KCSAN_ACCESS_ATOMIC` | `1 << 2` | 原子访问 |
| `KCSAN_ACCESS_ASSERT` | `1 << 3` | 断言访问 |
| `KCSAN_ACCESS_SCOPED` | `1 << 4` | 作用域访问 |

### 2.4 公开 API

头文件：`include/linux/kcsan-checks.h`

| API | 说明 | 备注 |
|-----|------|------|
| `__kcsan_check_access(ptr, size, type)` | 检查通用访问的竞争 | 编译器自动插入 |
| `__kcsan_mb()` | 全内存屏障插桩 | 对应 `smp_mb()` |
| `__kcsan_wmb()` | 写屏障插桩 | 对应 `smp_wmb()` |
| `__kcsan_rmb()` | 读屏障插桩 | 对应 `smp_rmb()` |
| `kcsan_check_access(ptr, size, type)` | 同 `__kcsan_check_access` 的别名 | — |
| `kcsan_nestable_atomic_begin/end()` | 嵌套原子区域 | 抑制该区域的竞争报告 |
| `kcsan_atomic_enter/exit()` | 原子上下文标记 | — |

头文件：`include/linux/kcsan.h`

| API | 说明 | 备注 |
|-----|------|------|
| `kcsan_init()` | 初始化 KCSAN 运行时 | 启动早期调用 |

### 2.5 `struct kcsan_ctx`

`include/linux/kcsan.h:22-60`：每个任务的 KCSAN 上下文，包含：
- 观察点使用状态
- 嵌套深度计数
- 原子区域标记
- `reorder_access`：用于检测编译器重排序导致的竞争

### 2.6 竞争报告格式

```
BUG: KCSAN: data-race in func_a+0x10/0x30 / func_b+0x20/0x40

Write to 0xffff000012345678 of 4 bytes by CPU 1:
 func_a+0x10/0x30
 ...

Read to 0xffff000012345678 of 4 bytes by CPU 0:
 func_b+0x20/0x40
 ...

Reported by KCSAN as: data-race
```

### 2.7 使用统计

- `kcsan_` / `__kcsan_` 符号在树内 645 处引用
- 编译器插桩（`-fsanitize=thread`）是主要消费者，手动调用较少
- debugfs 接口：`/sys/kernel/debug/kcsan`

---

## 3 UBSAN — 未定义行为检测

### 定位

**功能**：检测内核中的 C 语言未定义行为（undefined behavior）。通过编译器插桩，在检测到有符号整数溢出、移位越界、数组下标越界、对齐错误、null 指针解引用等问题时调用 `__ubsan_handle_*` 处理函数输出诊断信息。

**实现方式**：编译器（GCC/Clang `-fsanitize=undefined`）在潜在未定义行为前自动插入对 `__ubsan_handle_*` 函数的调用。`lib/ubsan.c`（386 行）实现了 13 个处理函数，覆盖加减乘溢出、除法溢出/除零、取负溢出、指针溢出、移位越界、数组越界、类型不匹配/对齐错误、无效 bool/enum 值等场景。与用户空间 UBSAN 不同，内核版本默认只打印警告不中止——可通过 `CONFIG_UBSAN_TRAP` 转为 `BUG()` 致命行为。

**应用场景**：处理函数在源码中只有 2 个文件显式引用（`lib/ubsan.c` 定义、`tools/objtool/check.c` 排除处理），但实际插桩点远多于此——编译器在编译时自动插入，不体现在源码中。通过 `CONFIG_UBSAN_SANITIZE_ALL` 控制全内核插桩范围，各子系统可通过 `UBSAN_SANITIZE := y/n` 做 per-directory 控制。主要用于开发和测试阶段的全内核代码质量审查，与 KASAN（检测越界访问/use-after-free）互补——UBSAN 关注语言层面的未定义行为。

### 3.1 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `lib/ubsan.c` | 386 | 所有 `__ubsan_handle_*` 处理函数 |
| `include/linux/ubsan.h` | — | 数据类型定义 |

### 3.2 核心概念

编译器（GCC/Clang `-fsanitize=undefined`）在检测到潜在未定义行为时插入对 `__ubsan_handle_*` 函数的调用。内核实现这些处理函数，输出诊断信息。

与用户空间 UBSAN 不同，内核版本默认 **不中止**——只打印警告。

### 3.3 处理函数

`lib/ubsan.c` 中实现的处理器：

| 处理函数 | 行号 | 检测的未定义行为 |
|----------|------|-----------------|
| `__ubsan_handle_add_overflow` | — | 有符号整数加法溢出 |
| `__ubsan_handle_sub_overflow` | — | 有符号整数减法溢出 |
| `__ubsan_handle_mul_overflow` | — | 有符号整数乘法溢出 |
| `__ubsan_handle_divrem_overflow` | 160 | 除零或有符号除法溢出（`INT_MIN / -1`） |
| `__ubsan_handle_negate_overflow` | — | 取负有符号溢出（`-INT_MIN`） |
| `__ubsan_handle_pointer_overflow` | — | 指针算术溢出 |
| `__ubsan_handle_shift_out_of_bounds` | 286 | 移位量超范围或负移位 |
| `__ubsan_handle_out_of_bounds` | 269 | 数组下标越界 |
| `__ubsan_handle_type_mismatch` | 241 | 对齐错误 / null 指针解引用 / 类型不匹配 |
| `__ubsan_handle_type_mismatch_v1` | 255 | 同上（v1 格式） |
| `__ubsan_handle_builtin_unreachable` | 328 | `__builtin_unreachable()` 被执行 |
| `__ubsan_handle_load_invalid_value` | 338 | 加载了无效的 bool 或 enum 值 |
| `__ubsan_handle_alignment_assumption` | 357 | 对齐假设失败 |

### 3.4 Kconfig 选项

- `CONFIG_UBSAN`：启用 UBSAN 框架
- `CONFIG_UBSAN_SANITIZE_ALL`：对全内核插桩（默认关闭，开销大）
- `CONFIG_UBSAN_TRAP`：将警告转为 `BUG()`（致命）
- `CONFIG_UBSAN_NO_ALIGNMENT`：跳过对齐检查（某些架构需要）

### 3.5 与 KASAN 的区别

| 维度 | KASAN | UBSAN |
|------|-------|-------|
| 检测目标 | 越界访问 / use-after-free | 语言层面的未定义行为 |
| 实现方式 | shadow 内存 + 插桩 | 纯编译器插桩 |
| 开销 | 中等（Generic 模式 ~1.5x） | 高（全插桩 ~2x+） |
| 误报率 | 低 | 中（内核代码常依赖"未定义"但实际可用的行为） |

### 3.6 使用统计

- `__ubsan_handle_` 函数在树内 24 处引用（含定义和声明）
- 实际插桩点远多于此——编译器在编译时自动插入，不体现在源码中
- 大多数子系统通过 `UBSAN_SANITIZE_ALL` 或 per-directory `KASAN_SANITIZE := n` 风格的 `UBSAN_SANITIZE := y` 控制

---

## 4 三者对比

| 维度 | KMSAN | KCSAN | UBSAN |
|------|-------|-------|-------|
| 检测目标 | 使用未初始化内存 | 数据竞争 | 未定义行为 |
| 编译器要求 | Clang only | Clang or GCC | Clang or GCC |
| 核心机制 | shadow + origin 内存 | 软件观察点 | 编译器插桩回调 |
| 代码量 | 2167 行 | 2969 行 | 386 行 |
| 运行时开销 | 高（每次访问查 shadow） | 中（概率采样） | 高（每次运算检查） |
| 内存开销 | 2x（shadow + origin） | 低（观察点池） | 低（代码膨胀） |
| arm64 支持 | 需要 Clang + 特殊配置 | 需要特殊配置 | 需要特殊配置 |
| 典型使用场景 | 开发/测试内核 | 开发/测试并发代码 | 开发/测试全内核 |
