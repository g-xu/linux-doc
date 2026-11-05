## math
在Linux内核开发中，数学运算与用户空间（User Space）有很大不同。内核通常不链接标准的 `libm`（数学库），且为了效率和移植性，尽量避免使用浮点运算。内核提供了一系列专用的宏和函数来处理整数运算、位运算、除法优化以及少量的算法支持。

以下是Linux内核中主要Math API的详细汇总表格。

### Linux内核 Math API 汇总表

主要涉及的头文件包括：
*   `&lt;linux/kernel.h&gt;` (基础宏)
*   `&lt;linux/math.h&gt;` (数学辅助)
*   `&lt;linux/math64.h&gt;` (64位除法)
*   `&lt;linux/bitops.h&gt;` (位操作)
*   `&lt;asm/div64.h&gt;` (底层除法实现)

| 分类 | API 接口名称 | 参数示例 | 功能描述 | 注意事项与返回值 |
| :--- | :--- | :--- | :--- | :--- |
| **基础比较** | `min(x, y)` | `min(a, b)` | 返回 `x` 和 `y` 中的较小值。 | **类型安全检查**：要求 x 和 y 类型必须严格一致，否则编译报错。 |
| | `max(x, y)` | `max(a, b)` | 返回 `x` 和 `y` 中的较大值。 | 同上，类型必须严格一致。 |
| | `min_t(type, x, y)` | `min_t(int, a, b)` | 强制将 `x` 和 `y` 转换为 `type` 类型后比较，返回较小值。 | 用于不同类型变量的比较，避免编译器警告。 |
| | `max_t(type, x, y)` | `max_t(int, a, b)` | 强制转换为 `type` 后比较，返回较大值。 | 同上。 |
| **范围限制** | `clamp(val, lo, hi)` | `clamp(x, 0, 100)` | 将 `val` 限制在 `[lo, hi]` 区间内。<br>若 `val &lt; lo` 返回 `lo`，若 `val &gt; hi` 返回 `hi`。 | 类型必须严格一致。常用于防止越界。 |
| | `clamp_t(type, val, lo, hi)` | `clamp_t(u8, x, 0, 255)` | 强制转换为 `type` 后进行范围限制。 | 用于不同类型的范围限制。 |
| | `clamp_val(val, lo, hi)` | `clamp_val(x, 0, 100)` | 类似于 `clamp_t`，但底层自动处理类型提升，通常用于 `val` 类型和 `lo/hi` 不一致时。 | 比较灵活的 clamp 实现。 |
| **整型除法** | `do_div(n, base)` | `u64 n; u32 base; rem = do_div(n, base);` | **32位系统核心API**。<br>计算 `n = n / base`，并**返回余数**。 | **注意**：`n` 会被原地修改为商！返回的是余数。`n` 必须是 `u64`，`base` 是 `u32`。 |
| (64位除法) | `div_u64(dividend, divisor)` | `u64 res = div_u64(u64_val, u32_val)` | 计算 `dividend / divisor`。 | 返回商。**不修改参数**。`divisor` 必须是 `u32`。 |
| | `div_s64(dividend, divisor)` | `s64 res = div_s64(s64_val, s32_val)` | 有符号 64 位除法。 | 返回商。 |
| | `div64_u64(dividend, divisor)` | `u64 res = div64_u64(u64_a, u64_b)` | 计算 `u64 / u64`。 | **非常昂贵**（在32位CPU上），尽量避免使用，除非 `divisor` 必须是64位。 |
| | `div64_long(dividend, divisor)` | `long res = div64_long(u64_val, long_val)` | 返回 `dividend / divisor` 的结果，结果截断为 `long`。 | 适用于预期商能放入 `long` 的情况。 |
| **除法辅助** | `DIV_ROUND_UP(n, d)` | `sz = DIV_ROUND_UP(bytes, block_size)` | 计算 `(n + d - 1) / d`，即向上取整除法。 | 非常常用，用于计算缓冲区大小、页数等。 |
| | `DIV_ROUND_CLOSEST(n, d)` | `val = DIV_ROUND_CLOSEST(10, 3)` | 四舍五入除法（最接近整数）。 | 适用于统计计算或传感器数据处理。 |
| **对齐与取整** | `ALIGN(x, a)` | `ALIGN(ptr, 4096)` | 将 `x` 向上对齐到 `a` 的倍数。 | `a` 必须是 2 的幂。常用于内存地址对齐。 |
| | `ALIGN_DOWN(x, a)` | `ALIGN_DOWN(ptr, 4096)` | 将 `x` 向下对齐到 `a` 的倍数。 | `a` 必须是 2 的幂。 |
| | `IS_ALIGNED(x, a)` | `if (IS_ALIGNED(addr, 4))` | 检查 `x` 是否是 `a` 的倍数。 | 返回布尔值。 |
| | `round_up(x, y)` | `round_up(13, 4) -> 16` | 将 `x` 向上取整到 `y` 的倍数。 | `y` 可以**不是** 2 的幂（与 ALIGN 的区别）。 |
| | `round_down(x, y)` | `round_down(13, 4) -> 12` | 将 `x` 向下取整到 `y` 的倍数。 | 同上。 |
| | `roundup_pow_of_two(n)` | `roundup_pow_of_two(7) -> 8` | 返回大于等于 `n` 的最小的 2 的幂。 | 常用于哈希表大小、RingBuffer 大小计算。 |
| **位运算数学** | `ilog2(n)` | `order = ilog2(PAGE_SIZE)` | 计算以 2 为底的对数（整数部分）。 | 编译时如果是常数会被优化。用于计算 Order。 |
| | `is_power_of_2(n)` | `if (is_power_of_2(size))` | 检查 `n` 是否是 2 的幂。 | 必须是非零值。 |
| **基本算法** | `abs(x)` | `val = abs(signed_var)` | 计算绝对值。 | 注意数据类型范围（如 `INT_MIN` 的绝对值）。 |
| | `int_sqrt(x)` | `root = int_sqrt(u32_val)` | 计算整数平方根。 | 位于 `&lt;linux/math.h&gt;`。精度为整数截断。 |
| | `gcd(a, b)` | `res = gcd(a, b)` | 计算最大公约数 (Greatest Common Divisor)。 | 需要 `&lt;linux/gcd.h&gt;`。 |
| | `lcm(a, b)` | `res = lcm(a, b)` | 计算最小公倍数 (Least Common Multiple)。 | 需要 `&lt;linux/lcm.h&gt;`。 |
| **溢出检查** | `check_add_overflow(a, b, &res)` | `if (check_add_overflow(x, y, &sum))` | 检查 `a + b` 是否溢出。 | 如果溢出返回 true，否则返回 false 并将结果存入 `res`。GCC 内建函数的封装。 |
| | `check_mul_overflow(a, b, &res)` | `if (check_mul_overflow(x, y, &prod))` | 检查 `a * b` 是否溢出。 | 同上，安全计算必备。 |
| **浮点运算** | `kernel_fpu_begin()` | | **开启**内核浮点运算上下文。 | **高危**：保存用户态 FPU 寄存器，关闭抢占。必须成对使用。 |
| (极少使用) | `kernel_fpu_end()` | | **结束**内核浮点运算上下文。 | 恢复寄存器，开启抢占。 |

---

### 关键点补充说明

#### 1. 为什么 64 位除法很特殊？
在 32 位架构（如 ARM32, x86_32）上，CPU 寄存器是 32 位的。编译器（GCC）默认不支持直接对 64 位整数（`u64` / `long long`）使用 `/` 或 `%` 运算符。
如果你直接写 `u64 a = b / c;`，链接时会报错 `undefined reference to '__udivdi3'`。
**解决方案**：必须使用 `do_div` 或 `div_u64` 系列辅助函数。

#### 2. 浮点运算 (Floating Point)
Linux 内核代码**严禁**随意使用 `float` 或 `double` 类型。
*   **原因**：为了节省上下文切换的时间，进入内核态时，CPU 的 FPU（浮点单元）寄存器通常不会被保存。如果在内核中直接使用浮点指令，会破坏用户空间程序的浮点状态。
*   **例外**：如果必须使用（如 RAID 校验、加密算法），必须使用 `kernel_fpu_begin()` 和 `kernel_fpu_end()` 包裹代码块，但这会带来性能开销，且该区间内通常不能休眠。

#### 3. 溢出检查 (Overflow)
在分配内存计算 `size = count * element_size` 时，非常容易发生溢出导致分配过小的内存，进而引发堆溢出漏洞。
**推荐**：使用 `array_size(count, size)` 或 `check_mul_overflow` 来确保安全。

#### 4. 常用代码片段示例

**计算 Ring Buffer 对应的 2 的幂次大小：**
```c
int size = 1000;
int real_size = roundup_pow_of_two(size); // 结果为 1024
int order = ilog2(real_size);             // 结果为 10
```

**安全的 64 位除法：**
```c
u64 timestamp_ns = 1234567890123ULL;
u32 nsec_per_sec = 1000000000;
// timestamp_ns 变成了秒数，rem 是剩余纳秒
u32 rem = do_div(timestamp_ns, nsec_per_sec); 
```
