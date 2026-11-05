# 溢出检查算术

> B 级文档 · 头文件 `include/linux/overflow.h`（249 行）· 实现全在头文件内（编译器内建函数包装）

## 溢出检查算术

### 定位

**功能**：提供编译期溢出检查算术操作，防止 `count * sizeof(type)` 等计算在 `count` 来自不可信来源时发生整数溢出——分配小 buffer 却写入大对象的经典堆溢出漏洞。将 GCC/Clang 的 `__builtin_*_overflow` 内建函数包装成统一 API。

**实现方式**：`check_*` 系列（`check_add_overflow`/`check_mul_overflow` 等）直接包装编译器内建函数，返回 bool 表示是否溢出，结果通过指针参数返回——溢出时结果**不安全**使用。`size_*` 系列（`size_add`/`size_mul`/`size_sub`）采用饱和语义，溢出时返回 `SIZE_MAX`，适合直接传给分配器。`struct_size`/`flex_array_size` 用 `__builtin_choose_expr(__is_constexpr(...))` 在常量参数时走编译期纯算术路径。所有 `check_*` 返回值标记 `__must_check_overflow`，强制调用者检查。

**应用场景**：内核内存分配路径的安全守卫。`struct_size(p, member, count)` 是 `kmalloc` 分配带柔性数组成员结构体的惯用法；`array_size`/`array3_size` 用于多维数组字节数计算；`check_add_overflow` 用于头部+数据长度的总大小校验。广泛出现在 mm（页面分配）、net（skb 分配）、fs（目录项/inode 分配）等子系统中。

### 核心 API

头文件：`include/linux/overflow.h`

| API | 说明 | 备注 |
|-----|------|------|
| `check_add_overflow(a, b, d)` | 计算 a+b，溢出时返回 true | `*d` 在溢出时**不安全**使用；返回 0 表示成功 |
| `check_sub_overflow(a, b, d)` | 计算 a-b，下溢时返回 true | 同上 |
| `check_mul_overflow(a, b, d)` | 计算 a*b，溢出时返回 true | 同上 |
| `check_shl_overflow(a, s, d)` | 计算 a<<s，溢出时返回 true | 额外检查：s 为负、a 为负、结果设符号位 |
| `size_mul(a, b)` | size_t 乘法，溢出时返回 SIZE_MAX | 饱和语义，不返回错误码 |
| `size_add(a, b)` | size_t 加法，溢出时返回 SIZE_MAX | 同上 |
| `size_sub(a, b)` | size_t 减法，溢出时返回 SIZE_MAX | 任一参数为 SIZE_MAX 也返回 SIZE_MAX |
| `array_size(a, b)` | 二维数组字节数 = `size_mul(a, b)` | `array_size` 是 `size_mul` 的别名 |
| `array3_size(a, b, c)` | 三维数组字节数 | 两次 `size_mul` 嵌套 |
| `flex_array_size(p, member, count)` | 柔性数组成员字节数 | 常量 count 走编译期计算，非常量走 `size_mul` |
| `struct_size(p, member, count)` | 结构体 + 尾部柔性数组总大小 | 内核分配惯用法：`kmalloc(struct_size(p, arr, n), GFP_KERNEL)` |

### 辅助宏

| 宏 | 说明 |
|----|------|
| `type_max(T)` | 类型 T 的最大值，避免 UB 的写法 |
| `type_min(T)` | 类型 T 的最小值 |
| `is_non_negative(a)` | 判断 a ≥ 0，避免 `-Wtype-limits` 警告 |
| `is_negative(a)` | 判断 a < 0 |
| `__must_check_overflow(x)` | 给 bool 加 `__must_check`，强制调用者检查返回值 |

### 使用模式

**分配模式**——最常见的用法：

```c
/* 传统写法（有溢出风险）：*/
buf = kmalloc(count * sizeof(*buf), GFP_KERNEL);

/* 安全写法：*/
buf = kmalloc(struct_size(buf, data, count), GFP_KERNEL);
/* 或 */
bytes = array_size(count, sizeof(*buf));
if (bytes == SIZE_MAX)
    return -EOVERFLOW;
buf = kmalloc(bytes, GFP_KERNEL);
```

**check_* 模式**——需要精确错误处理时：

```c
size_t total;
if (check_add_overflow(header_len, data_len, &total))
    return -EOVERFLOW;
```

### 设计要点

- **编译器内建函数**：`__builtin_add_overflow` 等在 GCC 5.1+ / Clang 3.8+ 中可用，编译期能折叠成单条溢出检查指令（如 x86 `JO`），零额外开销
- **`__must_check`**：`check_*` 系列通过 `__must_check_overflow()` 包装，调用者不检查返回值会触发编译警告
- **饱和 vs 错误码**：`size_*` 系列返回 SIZE_MAX（饱和语义，适合直接传给分配器）；`check_*` 系列返回 bool（适合需要区分错误码的场景）
- **常量折叠**：`struct_size` 和 `flex_array_size` 用 `__builtin_choose_expr(__is_constexpr(count), ...)` 在 count 为编译期常量时走纯算术路径，不经过 `size_mul`
