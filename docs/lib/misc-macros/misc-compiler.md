# 杂项：likely/unlikely — 分支预测提示

> B 级文档。告诉编译器某个条件更可能为真或假，让编译器优化分支布局。

## likely/unlikely — 分支预测提示

### 定位

**功能**：向编译器提示条件分支的预期走向——`likely(x)` 表示 x 通常为真，`unlikely(x)` 表示 x 通常为假。编译器据此优化指令布局，将热路径放在 fall-through 位置，冷路径放到 `.text.unlikely` 段，改善指令缓存局部性。

**实现方式**：底层包装 GCC/Clang 的 `__builtin_expect` 内建函数。当开启 `CONFIG_PROFILE_ANNOTATED_BRANCHES` 时，`likely`/`unlikely` 会替换为 `__branch_check__` 调用，记录分支命中统计供 `annotate` 工具分析。另有 `likely_notrace`/`unlikely_notrace` 变体始终直接使用 `__builtin_expect`，不经过 profiling 路径。

**应用场景**：全树约 5 万+ 处使用，是内核中最常见的性能提示宏。典型消费者包括：错误路径处理（`unlikely(IS_ERR(ptr))`）、文件系统快速路径判断、网络包处理热路径、内存管理中的条件检查等。

### 代码地图

| 文件 | 职责 |
|------|------|
| `include/linux/compiler.h` | `likely()`/`unlikely()` 定义 |
| `include/linux/compiler_types.h` | `__builtin_expect` 包装 |

### 核心宏

`include/linux/compiler.h`:

```c
// 无 trace 变体（始终使用 __builtin_expect）
#define likely_notrace(x)   __builtin_expect(!!(x), 1)
#define unlikely_notrace(x) __builtin_expect(!!(x), 0)

// 有 PROFILING 时走分支概率统计
#ifdef CONFIG_PROFILE_ANNOTATED_BRANCHES
#  define likely(x)   (__branch_check__(x, 1, __builtin_constant_p(x)))
#  define unlikely(x) (__branch_check__(x, 0, __builtin_constant_p(x)))
#else
#  define likely(x)   __builtin_expect(!!(x), 1)
#  define unlikely(x) __builtin_expect(!!(x), 0)
#endif
```

### 原理

`__builtin_expect` 是 GCC/Clang 内建函数，告诉编译器某个表达式的预期值。编译器据此：
1. 将"热"路径放在 fall-through 位置（减少跳转）
2. 将"冷"路径放到 `.text.unlikely` 段（改善指令缓存局部性）

### 使用注意

- **只在有充分证据时使用**：错误路径、罕见条件用 `unlikely`；正常路径用 `likely`
- **不要滥用**：现代 CPU 的分支预测器很强，错误的提示反而有害
- **`unlikely(0)` 和 `likely(1)` 是常见的错误用法**——编译器不需要你告诉它常量

### 使用统计

`likely` 和 `unlikely` 在全树出现约 **5 万+** 次，是内核中最常见的性能提示宏。
