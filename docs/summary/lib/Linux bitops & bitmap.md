## bitops & bitmap
以下是 Linux 内核中 **Bitops (位操作)** 和 **Bitmap (位图)** 主要 API 的功能描述表。

### 1. 基础位操作 (Bitops)
**头文件**: `&lt;linux/bitops.h&gt;`
**适用场景**: 操作单个 `unsigned long` 变量中的特定位（通常用于标志位 Flags）。

| API 函数 / 宏 | 功能描述 | 原子性 / 备注 |
| :--- | :--- | :--- |
| **设置与清除** | | |
| `set_bit(nr, addr)` | 将 `addr` 的第 `nr` 位置为 **1**。 | **原子操作** (SMP 安全)。 |
| `__set_bit(nr, addr)` | 将 `addr` 的第 `nr` 位置为 **1**。 | **非原子** (速度快，但多核不安全)。 |
| `clear_bit(nr, addr)` | 将 `addr` 的第 `nr` 位置为 **0**。 | **原子操作**。 |
| `__clear_bit(nr, addr)` | 将 `addr` 的第 `nr` 位置为 **0**。 | **非原子**。 |
| `change_bit(nr, addr)` | **翻转** `addr` 的第 `nr` 位 (0变1，1变0)。 | **原子操作**。 |
| `__change_bit(nr, addr)` | **翻转** `addr` 的第 `nr` 位。 | **非原子**。 |
| **测试与修改** | | |
| `test_bit(nr, addr)` | **检查**第 `nr` 位的值。返回 0 或 1。 | **原子操作** (即使在非原子上下文中也是如此)。 |
| `test_and_set_bit(nr, addr)` | 设置第 `nr` 位为 1，并**返回**修改之前的值。 | **原子操作**。常用于实现简单的锁。 |
| `__test_and_set_bit(...)` | 同上，但不保证原子性。 | **非原子**。 |
| `test_and_clear_bit(nr, addr)` | 清除第 `nr` 位为 0，并**返回**修改之前的值。 | **原子操作**。 |
| `test_and_change_bit(...)` | 翻转第 `nr` 位，并**返回**修改之前的值。 | **原子操作**。 |

---

### 2. 位图操作 (Bitmap)
**头文件**: `&lt;linux/bitmap.h&gt;`
**适用场景**: 操作**任意长度**的位数组（例如 `cpumask`，或者超过 64 位的标志集合）。它是基于 `bitops` 的封装。

| API 分类 | API 函数 / 宏 | 功能描述 | 备注 |
| :--- | :--- | :--- | :--- |
| **声明与内存** | `DECLARE_BITMAP(name, bits)` | **静态**定义一个名为 `name` 的位图数组。 | 自动计算所需的 `unsigned long` 数组长度。<br>例如 `DECLARE_BITMAP(map, 100);` |
| | `bitmap_alloc(nbits, flags)` | **动态**分配一个位图内存。 | 底层调用 `kmalloc_array`。 |
| | `bitmap_free(bitmap)` | 释放位图内存。 | 底层调用 `kfree`。 |
| | `bitmap_zalloc(nbits, flags)` | 动态分配并**清零**。 | |
| **整体操作** | `bitmap_zero(dst, nbits)` | 将位图所有位**清零**。 | |
| | `bitmap_fill(dst, nbits)` | 将位图所有位置为 **1**。 | |
| | `bitmap_copy(dst, src, nbits)` | 将 `src` 的内容拷贝到 `dst`。 | |
| **逻辑运算** | `bitmap_and(dst, src1, src2, n)` | `dst = src1 & src2` (按位与)。 | 支持任意长度 `n`。 |
| | `bitmap_or(dst, src1, src2, n)` | `dst = src1 \| src2` (按位或)。 | |
| | `bitmap_xor(dst, src1, src2, n)` | `dst = src1 ^ src2` (按位异或)。 | |
| | `bitmap_complement(dst, src, n)` | `dst = ~src` (按位取反)。 | |
| **查找与遍历** | `find_first_bit(addr, size)` | 查找范围内**第一个被置为 1** 的位索引。 | 如果没找到，返回 `>= size` 的值。 |
| | `find_first_zero_bit(addr, size)`| 查找范围内**第一个为 0** 的位索引。 | |
| | `find_next_bit(addr, size, offset)` | 从 `offset` 开始查找下一个为 1 的位。 | 常配合循环使用。 |
| | `find_next_zero_bit(...)` | 从 `offset` 开始查找下一个为 0 的位。 | |
| **状态查询** | `bitmap_empty(src, nbits)` | 检查位图是否**全为 0**。 | 返回布尔值。 |
| | `bitmap_full(src, nbits)` | 检查位图是否**全为 1**。 | 返回布尔值。 |
| | `bitmap_equal(src1, src2, nbits)`| 检查两个位图是否完全相等。 | |
| | `bitmap_weight(src, nbits)` | 统计位图中 **1 的总个数** (Hamming Weight)。 | |

---

### 3. 位图遍历宏 (Iterators)

内核提供了方便的宏来遍历位图中所有置位（或清零）的位，无需手动写 `find_next_bit` 循环。

| 宏名称 | 使用示例 | 功能描述 |
| :--- | :--- | :--- |
| `for_each_set_bit` | `for_each_set_bit(bit, addr, size) { ... }` | 遍历位图中**所有为 1** 的位。<br>`bit` 是循环变量（索引），`addr` 是位图地址，`size` 是总位数。 |
| `for_each_clear_bit`| `for_each_clear_bit(bit, addr, size) { ... }` | 遍历位图中**所有为 0** 的位。 |

---

### 4. 关键区别总结

1.  **Bitops (`set_bit`)**：
    *   针对**单个位**的操作。
    *   通常用于 `unsigned long flags` 这种简单的标志位。
    *   **非常关注原子性**（Atomic vs Non-atomic）。

2.  **Bitmap (`bitmap_and`)**：
    *   针对**位数组**的操作。
    *   底层其实是一组 `unsigned long` 数组。
    *   适用于位数很多的情况（如 1024 位）。
    *   **通常不是原子的**（除了基于 bitops 的单个位操作外，整体操作如 `bitmap_copy` 不是原子的，多线程访问需要加锁）。