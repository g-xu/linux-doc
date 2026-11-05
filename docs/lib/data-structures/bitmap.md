# bitmap — 位图操作

## §1 定位

bitmap 不是容器——它是**集合运算工具包**。底层是 `unsigned long` 数组，每个元素承载 `BITS_PER_LONG` 个位（arm64 上 64 位）。它提供的不是"存取元素"，而是对整个位数组做集合操作：交并补、移位、查找连续零区域、字符串解析/打印、重映射。

**核心优化是编译期特化。** 当 `nbits` 是编译期常量且 ≤ `BITS_PER_LONG` 时（`small_const_nbits` 检查），所有操作退化为单条寄存器位运算——零函数调用、零内存访问。`bitmap_weight(mask, 32)` 在 arm64 上就是一条 `hweight_long(*mask & MASK)` 指令。

**与其他数据结构的核心差异：**

| 属性 | bitmap | xarray | `list_head` |
|------|--------|--------|-------------|
| 存储内容 | 单个 bit（0/1） | 指针 | 指针 |
| 操作类型 | 集合运算（and/or/xor/shift） | 按索引存取 | 前后遍历 |
| 快速路径 | 编译期常量 → 单寄存器运算 | 无 | 无 |
| 典型用途 | CPU 掩码、IRQ 位图、capability | 页缓存、fd 表 | 对象链表 |

**关键设计决策：**

1. **`unsigned long` 数组**——不是 `uint8_t` 数组，因为 CPU 的位操作指令天然操作寄存器宽度的字。arm64 上每次处理 64 位
2. **双层 API**——`bitmap_*` 函数操作整个位图（集合运算）；`*_bit` 函数（`set_bit` / `clear_bit` / `test_bit`）操作单个位，通常是原子操作。两套 API 互补：前者用于批量处理，后者用于并发安全的单 bit 操作
3. **编译期快速路径**——`small_const_nbits(nbits)` 检查让编译器把小位图操作内联为单条指令。这意味着声明位图时尽量用常量大小
4. **尾部位处理**——`bitmap_zero` / `bitmap_fill` 会操作到 `unsigned long` 边界（可能超出 `nbits`），而 `bitmap_set` / `bitmap_clear` 精确到指定位。这是两个容易混淆的行为

**定义位置：**

- 头文件：`include/linux/bitmap.h`（618 行）
- 实现：`lib/bitmap.c`（1551 行）
- 查找函数：`include/linux/find.h`（`find_next_bit` 等）
- 单 bit 操作：`include/linux/bitops.h` + `arch/arm64/include/asm/bitops.h`
- 声明宏：`include/linux/types.h`（`DECLARE_BITMAP`）

**使用规模：** `DECLARE_BITMAP` 全树 820 处使用；`for_each_set_bit` 1023 处；`bitmap_set` 244 处；`bitmap_weight` 184 处。

## §2 核心 API

头文件：`include/linux/bitmap.h`；实现：`lib/bitmap.c`。

### 2.1 声明

| API | 说明 | 备注 |
|-----|------|------|
| `DECLARE_BITMAP(name, bits)` | 声明位图变量 | 定义在 `include/linux/types.h`；展开为 `unsigned long name[BITS_TO_LONGS(bits)]` |

### 2.2 分配与释放

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_alloc(nbits, gfp)` | 分配位图 | 返回 `unsigned long *`；不初始化内容 |
| `bitmap_zalloc(nbits, gfp)` | 分配并清零 | 最常用 |
| `bitmap_alloc_node(nbits, gfp, node)` | NUMA 节点分配 | — |
| `bitmap_zalloc_node(nbits, gfp, node)` | NUMA 节点分配并清零 | — |
| `bitmap_free(bitmap)` | 释放 | 接受 NULL |
| `devm_bitmap_alloc(dev, nbits, gfp)` | 设备管理分配 | 自动在设备 detach 时释放 |
| `devm_bitmap_zalloc(dev, nbits, gfp)` | 设备管理分配并清零 | — |

### 2.3 初始化与拷贝

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_zero(dst, nbits)` | 全部清零 | ★ 尾部位也清零（到 `unsigned long` 边界） |
| `bitmap_fill(dst, nbits)` | 全部置 1 | ★ 尾部位也置 1 |
| `bitmap_copy(dst, src, nbits)` | 拷贝 | 尾部不保证清零 |
| `bitmap_copy_clear_tail(dst, src, nbits)` | 拷贝并清零尾部 | 安全版本 |
| `bitmap_copy_and_extend(to, from, count, size)` | 拷贝 + 扩展 | 从 `count` 位扩展到 `size` 位，多余位清零 |
| `bitmap_copy_le(dst, src, nbits)` | 拷贝为小端格式 | arm64 上 `#define bitmap_copy_le bitmap_copy` |

### 2.4 集合运算

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_and(dst, src1, src2, nbits)` | dst = src1 & src2 | **返回结果是否非空**（bool） |
| `bitmap_or(dst, src1, src2, nbits)` | dst = src1 \| src2 | void |
| `bitmap_xor(dst, src1, src2, nbits)` | dst = src1 ^ src2 | void |
| `bitmap_andnot(dst, src1, src2, nbits)` | dst = src1 & ~src2 | **返回结果是否非空**（bool） |
| `bitmap_complement(dst, src, nbits)` | dst = ~src | void |
| `bitmap_replace(dst, old, new, mask, nbits)` | dst = (old & ~mask) \| (new & mask) | 按 mask 混合 |

注意 `bitmap_and` 和 `bitmap_andnot` 返回 `bool`（结果是否非空），而 `bitmap_or` / `bitmap_xor` 返回 `void`。

### 2.5 比较

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_equal(src1, src2, nbits)` | 是否相等 | 对齐时走 `memcmp` 快速路径 |
| `bitmap_or_equal(src1, src2, src3, nbits)` | (src1 \| src2) == src3 | — |
| `bitmap_intersects(src1, src2, nbits)` | 是否有交集 | src1 & src2 != 0 |
| `bitmap_subset(src1, src2, nbits)` | src1 ⊆ src2 | src1 & ~src2 == 0 |
| `bitmap_empty(src, nbits)` | 是否全零 | — |
| `bitmap_full(src, nbits)` | 是否全 1 | — |

### 2.6 计数

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_weight(src, nbits)` | 置位位数（Hamming weight） | 小常量走 `hweight_long` |
| `bitmap_weight_and(src1, src2, nbits)` | (src1 & src2) 的置位数 | — |

### 2.7 查找

这些函数定义在 `include/linux/find.h`，但是 bitmap 的核心操作。

| API | 说明 | 备注 |
|-----|------|------|
| `find_first_bit(addr, nbits)` | 第一个置位的位号 | 返回 `nbits` 表示未找到 |
| `find_first_zero_bit(addr, nbits)` | 第一个清零的位号 | — |
| `find_next_bit(addr, nbits, offset)` | offset 之后第一个置位 | — |
| `find_next_zero_bit(addr, nbits, offset)` | offset 之后第一个清零 | — |
| `find_next_and_bit(addr1, addr2, nbits, offset)` | (addr1 & addr2) 中下一个置位 | — |

### 2.8 遍历宏

| API | 说明 | 备注 |
|-----|------|------|
| `for_each_set_bit(bit, addr, size)` | 遍历所有置位的位 | 全树 1023 处调用 |
| `for_each_clear_bit(bit, addr, size)` | 遍历所有清零的位 | — |
| `for_each_set_bit_from(bit, addr, size)` | 从当前 bit 开始遍历置位 | 不重置 bit 到 0 |

```c
unsigned long mask = 0b10110;
unsigned int bit;
for_each_set_bit(bit, &mask, BITS_PER_LONG) {
    printk("bit %u is set\n", bit);
}
/* 输出：bit 1, bit 2, bit 4 */
```

### 2.9 区域操作

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_set(dst, pos, nbits)` | 设置 [pos, pos+nbits) 区域 | ★ 精确到指定位，不碰尾部 |
| `bitmap_clear(dst, pos, nbits)` | 清除 [pos, pos+nbits) 区域 | ★ 精确到指定位 |
| `bitmap_find_next_zero_area(buf, size, pos, nr, align_mask)` | 找连续零区域 | `align_mask` 是 2^n - 1 |
| `bitmap_find_next_zero_area_off(buf, size, pos, nr, align_mask, off)` | 带偏移版本 | — |
| `bitmap_find_free_region(bitmap, bits, order)` | 找并分配 2^order 大小的对齐区域 | 返回起始位号或负值 |
| `bitmap_release_region(bitmap, pos, order)` | 释放区域 | — |
| `bitmap_allocate_region(bitmap, pos, order)` | 分配指定区域 | 如果已被占用返回 `-EBUSY` |

### 2.10 移位

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_shift_right(dst, src, n, nbits)` | dst = src >> n | — |
| `bitmap_shift_left(dst, src, n, nbits)` | dst = src << n | — |

### 2.11 映射与重映射

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_remap(dst, src, old, new, nbits)` | 按映射表重映射 | old/new 定义映射关系 |
| `bitmap_bitremap(oldbit, old, new, nbits)` | 映射单个位 | — |
| `bitmap_onto(dst, orig, relmap, nbits)` | 相对映射 | orig 相对于 relmap 的映射 |
| `bitmap_fold(dst, orig, sz, nbits)` | dst = orig mod sz | 位号对 sz 取模 |
| `bitmap_cut(dst, src, first, n, nbits)` | 切掉 [first, first+n) 位 | 后面的位前移 |

### 2.12 解析与打印

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_parse(buf, buflen, dst, nbits)` | 从 hex 字符串解析 | `"f,00ff"` → 位图 |
| `bitmap_parse_user(ubuf, ulen, dst, nbits)` | 从用户空间解析 | — |
| `bitmap_parselist(buf, dst, nbits)` | 从范围列表解析 | `"0-3,5,7-15"` → 位图 |
| `bitmap_parselist_user(ubuf, ulen, dst, nbits)` | 从用户空间解析 | — |
| `bitmap_print_to_pagebuf(list, buf, mask, nmaskbits)` | 打印到页面缓冲区 | `list=true` 用范围格式，`false` 用 hex 掩码 |
| `bitmap_print_bitmask_to_buf(buf, mask, nmaskbits, off, count)` | 打印 hex 掩码到缓冲区 | 支持偏移和长度（sysfs 用） |
| `bitmap_print_list_to_buf(buf, mask, nmaskbits, off, count)` | 打印范围列表到缓冲区 | 同上 |

### 2.13 数组转换

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_from_arr32(bitmap, buf, nbits)` | 从 `u32[]` 拷贝 | arm64 上是函数（`BITS_PER_LONG == 64`） |
| `bitmap_to_arr32(buf, bitmap, nbits)` | 拷贝到 `u32[]` | — |
| `bitmap_from_arr64(bitmap, buf, nbits)` | 从 `u64[]` 拷贝 | arm64 上是 `bitmap_copy_clear_tail` 的宏 |
| `bitmap_to_arr64(buf, bitmap, nbits)` | 拷贝到 `u64[]` | — |
| `bitmap_from_u64(dst, mask)` | 从 `u64` 值拷贝 | 处理 32 位 BE 的字序问题 |

### 2.14 8 位值操作

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_get_value8(map, start)` | 读取 8 位值 | `start` 必须是 8 的倍数 |
| `bitmap_set_value8(map, value, start)` | 写入 8 位值 | `start` 必须是 8 的倍数 |

### 2.15 辅助宏

| API | 说明 | 备注 |
|-----|------|------|
| `bitmap_size(nbits)` | 位图占用的字节数 | `ALIGN(nbits, BITS_PER_LONG) / BITS_PER_BYTE` |
| `BITMAP_FIRST_WORD_MASK(start)` | 第一个字的掩码 | 从 `start` 位开始的高位掩码 |
| `BITMAP_LAST_WORD_MASK(nbits)` | 最后一个字的掩码 | 覆盖前 `nbits` 位的低位掩码 |
| `BITMAP_FROM_U64(n)` | u64 转位图初始化列表 | 32 位 BE 需要交换字序 |

### 2.16 单 bit 操作（底层）

这些定义在 `include/linux/bitops.h` 和架构头文件中，是 bitmap 的原子操作层。

| API | 说明 | 备注 |
|-----|------|------|
| `set_bit(nr, addr)` | 原子置位 | arm64 上用 `ldset` |
| `clear_bit(nr, addr)` | 原子清位 | — |
| `change_bit(nr, addr)` | 原子翻转 | — |
| `test_bit(nr, addr)` | 测试位 | 非原子读 |
| `test_and_set_bit(nr, addr)` | 原子置位并返回旧值 | — |
| `test_and_clear_bit(nr, addr)` | 原子清位并返回旧值 | — |
| `test_and_change_bit(nr, addr)` | 原子翻转并返回旧值 | — |

**非原子版本：** `__set_bit` / `__clear_bit` / `__change_bit` / `__test_and_set_bit` 等——不加锁，用于已知无竞争的场景（如初始化阶段）。

## §3 使用模式

### 模式 1：资源分配（IRQ / 设备 ID）

```c
static DECLARE_BITMAP(irq_bitmap, 256);
static DEFINE_SPINLOCK(irq_lock);

int alloc_irq(void)
{
    int irq;
    unsigned long flags;

    spin_lock_irqsave(&irq_lock, flags);
    irq = find_first_zero_bit(irq_bitmap, 256);
    if (irq >= 256) {
        spin_unlock_irqrestore(&irq_lock, flags);
        return -ENOSPC;
    }
    set_bit(irq, irq_bitmap);
    spin_unlock_irqrestore(&irq_lock, flags);
    return irq;
}

void free_irq(int irq)
{
    clear_bit(irq, irq_bitmap);
}
```

### 模式 2：CPU 集合遍历

```c
static DECLARE_BITMAP(cpu_mask, NR_CPUS);

/* 遍历所有标记的 CPU */
unsigned int cpu;
for_each_set_bit(cpu, cpu_mask, nr_cpu_ids) {
    smp_call_function_single(cpu, my_func, NULL, 1);
}
```

### 模式 3：capability / feature 标志

```c
enum {
    FEAT_A,
    FEAT_B,
    FEAT_C,
    NR_FEATS
};

static DECLARE_BITMAP(device_features, NR_FEATS);

void init_features(void)
{
    set_bit(FEAT_A, device_features);
    set_bit(FEAT_C, device_features);
}

bool has_feature(int feat)
{
    return test_bit(feat, device_features);
}

int count_features(void)
{
    return bitmap_weight(device_features, NR_FEATS);
}
```

### 模式 4：连续区域分配

```c
static DECLARE_BITMAP(mem_pool, 4096);

/* 分配 16 个连续位（order=4 → 2^4=16，对齐到 16 位边界） */
int pos = bitmap_find_free_region(mem_pool, 4096, 4);
if (pos < 0)
    return -ENOMEM;

/* 释放 */
bitmap_release_region(mem_pool, pos, 4);
```

### 模式 5：从字符串解析（内核参数 / sysfs）

```c
/* 解析 "0-3,5,7-15" 格式的字符串 */
DECLARE_BITMAP(mask, 64);
int ret = bitmap_parselist("0-3,5,7-15", mask, 64);
/* mask 现在包含位 0,1,2,3,5,7,8,...,15 */

/* 打印 */
char buf[128];
bitmap_print_to_pagebuf(true, buf, mask, 64);
/* buf: "0-3,5,7-15\n" */
```

### 模式 6：集合运算

```c
DECLARE_BITMAP(active, 256);
DECLARE_BITMAP(requested, 256);
DECLARE_BITMAP(result, 256);

/* 计算交集 */
if (bitmap_and(result, active, requested, 256)) {
    /* 交集非空——遍历交集中所有置位的位 */
    unsigned int bit;
    for_each_set_bit(bit, result, 256)
        process(bit);
}
```

## §4 陷阱

### 陷阱 1：bitmap_zero / bitmap_fill 会越界操作

`bitmap_fill` 把最后一个 `unsigned long` 的**所有位**都置 1，包括超出 `nbits` 的位。`bitmap_zero` 同理清零到 `unsigned long` 边界。如果两个 bitmap 的尾部位不一致，`bitmap_equal` 可能返回 false，即使有效位相同。

解决方案：用 `bitmap_copy_clear_tail` 拷贝后清零尾部，或用 `bitmap_set` / `bitmap_clear`（精确到指定位）。

### 陷阱 2：bitmap_set / bitmap_clear 和 bitmap_zero / bitmap_fill 的区别

`bitmap_set(dst, pos, nbits)` 精确设置 [pos, pos+nbits) 范围的位，不碰其他位。`bitmap_fill(dst, nbits)` 设置 [0, nbits) 但会填充到 `unsigned long` 边界。名字相似但行为不同——`bitmap_fill` 不是 `bitmap_set(dst, 0, nbits)` 的等价物。

### 陷阱 3：set_bit 是原子的，__set_bit 不是

`set_bit(nr, addr)` 使用原子指令（arm64 上是 `ldset`），可以在多处理器环境下安全使用。`__set_bit(nr, addr)` 是普通位操作，不加锁——只用在已知无竞争的场景。

### 陷阱 4：bitmap_weight 的 nbits 不是数组大小

`bitmap_weight(src, nbits)` 只计算前 `nbits` 位中置位的位数。如果数组实际有更多位，多余的位不计入。确保 `nbits` 是你关心的有效位数。

### 陷阱 5：bitmap_find_free_region 的 order 是 2 的幂

`bitmap_find_free_region(bitmap, bits, order)` 找的是 `2^order` 大小的**对齐**区域。`order=3` 找的是 8 位对齐的连续零区域，不是任意 8 位。如果需要非对齐的连续零区域，用 `bitmap_find_next_zero_area`。

### 陷阱 6：小常量 nbits 的快速路径

当 `nbits` 是编译期常量且 ≤ `BITS_PER_LONG` 时，很多 bitmap 操作退化为单 `unsigned long` 的位运算（通过 `small_const_nbits` 检查，`include/linux/bitmap.h:246`）。这意味着：
- `bitmap_weight(mask, 32)` 在 arm64 上就是 `hweight_long(*mask & MASK)`——零函数调用
- `bitmap_equal(a, b, 64)` 就是 `!((*a ^ *b) & MASK)`

但如果 `nbits` 不是编译期常量，即使实际值很小，也会走 `__bitmap_*` 函数路径。

### 陷阱 7：bitmap_and 返回 bool，bitmap_or 返回 void

`bitmap_and` 和 `bitmap_andnot` 返回 `bool`（结果是否非空），而 `bitmap_or` / `bitmap_xor` / `bitmap_complement` 返回 `void`。如果需要用 `bitmap_or` 的结果做非空判断，得额外调 `bitmap_empty`。

### 陷阱 8：bitmap_parselist 的格式

`bitmap_parselist` 接受的范围格式是 `"0-3,5,7-15"`——逗号分隔，连字符表示范围。不支持空格。如果解析失败返回负错误码。

## §5 代码示例

### 示例 1：位图管理的设备池

```c
#include <linux/bitmap.h>
#include <linux/slab.h>

struct device_pool {
    unsigned long *allocated;
    unsigned int max_devices;
    spinlock_t lock;
};

struct device_pool *pool_create(unsigned int max_devices)
{
    struct device_pool *pool = kzalloc(sizeof(*pool), GFP_KERNEL);
    if (!pool)
        return NULL;

    pool->allocated = bitmap_zalloc(max_devices, GFP_KERNEL);
    if (!pool->allocated) {
        kfree(pool);
        return NULL;
    }
    pool->max_devices = max_devices;
    spin_lock_init(&pool->lock);
    return pool;
}

int pool_alloc(struct device_pool *pool)
{
    unsigned long flags;
    int id;

    spin_lock_irqsave(&pool->lock, flags);
    id = find_first_zero_bit(pool->allocated, pool->max_devices);
    if (id >= pool->max_devices) {
        spin_unlock_irqrestore(&pool->lock, flags);
        return -ENOSPC;
    }
    set_bit(id, pool->allocated);
    spin_unlock_irqrestore(&pool->lock, flags);
    return id;
}

void pool_free(struct device_pool *pool, int id)
{
    clear_bit(id, pool->allocated);
}

unsigned int pool_count(struct device_pool *pool)
{
    return bitmap_weight(pool->allocated, pool->max_devices);
}

void pool_destroy(struct device_pool *pool)
{
    bitmap_free(pool->allocated);
    kfree(pool);
}
```

### 示例 2：位图交集遍历

```c
DECLARE_BITMAP(active, 256);
DECLARE_BITMAP(requested, 256);
DECLARE_BITMAP(result, 256);

/* 计算 active & requested */
if (bitmap_and(result, active, requested, 256)) {
    unsigned int bit;
    for_each_set_bit(bit, result, 256)
        process(bit);
}
```

## §6 取证

### 已读文件

| 文件 | 行数 | 读法 |
|------|------|------|
| `include/linux/bitmap.h` | 618 | 全读 |
| `lib/bitmap.c` | 1551 | 符号表 + 关键函数 |

### 关键论断出处

| 论断 | 出处 | 验证命令 |
|------|------|----------|
| DECLARE_BITMAP 全树 820 处使用 | grep 统计 | `grep -r '\bDECLARE_BITMAP\b' --include='*.c' --include='*.h' . \| wc -l` → 820 |
| for_each_set_bit 全树 1023 处调用 | grep 统计 | `grep -r '\bfor_each_set_bit\b' --include='*.c' --include='*.h' . \| wc -l` → 1023 |
| bitmap_set 全树 244 处调用 | grep 统计 | `grep -r '\bbitmap_set\b' --include='*.c' . \| wc -l` → 244 |
| bitmap_weight 全树 184 处调用 | grep 统计 | `grep -r '\bbitmap_weight\b' --include='*.c' --include='*.h' . \| wc -l` → 184 |
| small_const_nbits 快速路径 | `include/linux/bitmap.h:246` | `if (small_const_nbits(nbits)) *dst = 0` |
| bitmap_and 返回 bool | `include/linux/bitmap.h:332-338` | `return (*dst = ...) != 0` |
| bitmap_or 返回 void | `include/linux/bitmap.h:340-347` | 无 return |
| bitmap_copy_le 在 arm64 上是宏 | `include/linux/bitmap.h:226` | `#define bitmap_copy_le bitmap_copy`（非 `__BIG_ENDIAN`） |
| devm_bitmap_alloc 存在 | `include/linux/bitmap.h:129-132` | 两个 devm 变体 |
| bitmap_print_bitmask_to_buf 存在 | `include/linux/bitmap.h:231-232` | extern 声明 |
| bitmap_print_list_to_buf 存在 | `include/linux/bitmap.h:234-235` | extern 声明 |

### 未验证项

- `lib/bitmap.c` 中的 `__bitmap_*` 函数实现只看了符号表，未逐行核实
- 架构特定的位操作（`arch/arm64/include/asm/bitops.h`）未详细分析
- `bitmap_parse` / `bitmap_parselist` 的完整解析语法未从源码逐行核实
- `bitmap_remap` / `bitmap_onto` / `bitmap_fold` 的具体映射语义只从文档注释了解
