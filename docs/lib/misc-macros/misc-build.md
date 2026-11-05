# 杂项：BUILD_BUG_ON / ARRAY_SIZE / stringify — 编译期工具

> B 级文档。三个在编译期进行断言和计算的宏。

## 1 BUILD_BUG_ON — 编译期断言

### 定位

**功能**：在编译期检查条件表达式，若条件为真或表达式无效则让编译失败。用于确保结构体大小、常量范围、类型布局等在设计时已确定的约束不被违反。

**实现方式**：核心机制是利用编译器对非法构造的检查——零宽位域（`int :-(condition)`）、负数组大小等会在条件不满足时触发编译错误。较新的变体 `static_assert` 直接包装 C11 `_Static_assert`，支持自定义错误消息。`BUILD_BUG_ON_ZERO` 利用相同原理但展开为 0 值，可嵌入表达式中。

**应用场景**：全树约 3000+ 处使用，遍布 mm（页面大小验证）、kernel（信号/锁结构体布局）、net（协议头大小）、fs（超级块结构校验）等子系统。是内核编译期质量保证的基础工具。

头文件：`include/linux/build_bug.h`（80 行）

| 宏 | 说明 |
|----|------|
| `BUILD_BUG_ON(condition)` | 条件为真时编译失败 |
| `BUILD_BUG_ON_MSG(cond, msg)` | 带自定义错误消息 |
| `BUILD_BUG_ON_ZERO(e)` | 如果 `e` 非零则编译失败，否则展开为 0 |
| `BUILD_BUG()` | 无条件编译失败 |
| `BUILD_BUG_ON_INVALID(e)` | 如果表达式无效则编译失败 |
| `BUILD_BUG_ON_NOT_POWER_OF_2(n)` | 检查是否为 2 的幂 |
| `static_assert(expr)` / `static_assert(expr, msg)` | C11 静态断言 |

**原理**：利用编译器对零宽位域、负数组大小等的检查来触发编译错误。

**使用示例**：
```c
BUILD_BUG_ON(sizeof(struct header) != 64);  // 确保结构体大小
BUILD_BUG_ON(MAX_CPUS > 4096);              // 确保常量范围
```

**使用统计**：全树约 3000+ 处。

---

## 2 ARRAY_SIZE — 数组元素计数

### 定位

**功能**：计算静态数组的元素个数，是内核中最常用的数组长度宏。

**实现方式**：通过 `sizeof(arr) / sizeof((arr)[0])` 计算元素数，并附加 `__must_be_array(arr)` 安全检查。`__must_be_array` 利用 `__same_type` 比较参数类型与指针类型——若参数是指针而非数组，类型相同检查失败，触发编译错误，防止对退化为指针的数组参数返回错误结果。

**应用场景**：全树约 3 万+ 处使用，是内核中使用最频繁的宏之一。几乎所有遍历静态数组的代码都依赖它：for 循环边界、`kcalloc` 分配大小、sysfs 属性表遍历等。

头文件：`include/linux/kernel.h:55`

```c
#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]) + __must_be_array(arr))
```

`__must_be_array(arr)` 确保参数是真正的数组而不是指针（对指针 `sizeof` 只返回指针大小）。

**使用统计**：全树约 3 万+ 处。

---

## 3 stringify — 宏转字符串

### 定位

**功能**：将宏参数展开后转换为字符串字面量。解决 C 预处理器中直接使用 `#x` 不会先展开宏参数的问题。

**实现方式**：采用两级间接展开——`__stringify(x)` 调用 `__stringify_1(x)`，后者执行 `#x` 字符串化。第一级让宏参数充分展开，第二级才做字符串化。如果只用一级 `#x`，传入 `FOO` 会得到字符串 `"FOO"` 而非 `"42"`。

**应用场景**：全树约 2000+ 处使用。典型场景包括：模块参数描述（`MODULE_PARM_DESC`）、tracepoint 事件名称生成、编译期版本号/配置名拼接、`__stringify(__LINE__)` 调试信息等。

头文件：`include/linux/stringify.h`（12 行）

```c
#define __stringify_1(x...)  #x
#define __stringify(x...)    __stringify_1(x)
```

两级间接：先展开宏参数，再字符串化。如果只用一级 `#x`，宏参数不会被展开。

**使用示例**：
```c
#define FOO 42
__stringify(FOO)  // → "42"（不是 "FOO"）
```

**使用统计**：全树约 2000+ 处。
