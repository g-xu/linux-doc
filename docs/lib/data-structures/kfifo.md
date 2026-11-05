# kfifo — 通用内核 FIFO

## §1 定位

kfifo 是内核的通用 FIFO 队列。它的核心设计是**单调递增索引 + 2 的幂掩码**：`in` 和 `out` 永远递增、从不回绕，通过 `index & mask`（等价于 `index % size`）映射到环形 buffer 的物理位置。这使得"满"和"空"的判定变成简单的算术比较（`in - out == size` vs `in == out`），不需要额外的标志位或牺牲一个槽位。

在**单读者 + 单写者**场景下完全无锁——`in` 只由写者修改，`out` 只由读者修改，`smp_wmb()` 保证数据在指针更新前可见。

**与其他数据结构的核心差异：**

| 属性 | `list_head` | kfifo | xarray |
|------|-------------|-------|--------|
| 访问模式 | 任意位置 | 严格先进先出 | 按索引随机访问 |
| 锁需求 | 用户自管 | 1R1W 无锁 | 内建 RCU + spinlock |
| 大小 | 动态链表 | 固定（2 的幂） | 动态 |
| 环形缓冲 | 不支持 | 天然支持 | 不适用 |
| 典型用途 | 对象管理 | 中断→进程通信、串行缓冲 | 页缓存、fd 表 |

**关键设计决策：**

1. **单调递增索引，不回绕**——`in` 和 `out` 是 `unsigned int`，只增不减。物理位置由 `in & mask` 计算。这避免了传统环形 buffer "满 vs 空"的二义性（传统方案要么牺牲一个槽位，要么用一个额外标志位）
2. **大小必须是 2 的幂**——`mask = size - 1`，`in & mask` 代替 `in % size`。位运算比取模快一个数量级，且保证环形回绕的正确性
3. **1R1W 无锁**——写者独占 `in`，读者独占 `out`。二者之间只需 `smp_wmb()` 保证内存序，不需要任何原子操作或锁
4. **类型泛型宏**——所有公共 API 都是宏，通过 `typeof` 推导元素类型，不需要 `void *`。存 `int` 就拷贝 `sizeof(int)`，存 `struct foo` 就拷贝 `sizeof(struct foo)`
5. **两种分配方式**——静态内联（`DEFINE_KFIFO`，buffer 嵌在结构体内，零额外分配）和动态分配（`kfifo_alloc`，buffer 从 kmalloc 来）

**定义位置：**

- 头文件：`include/linux/kfifo.h`（893 行）
- 实现：`lib/kfifo.c`（592 行）

**使用规模：** `kfifo_in` 全树 69 处调用；`kfifo_out` 56 处；`kfifo_alloc` 67 处；`kfifo_is_empty` 90 处。

## §2 核心 API

头文件：`include/linux/kfifo.h`；实现：`lib/kfifo.c`。

### 2.1 声明与初始化

| API | 说明 | 备注 |
|-----|------|------|
| `DECLARE_KFIFO(name, type, size)` | 声明 FIFO 变量 | `size` 必须是 2 的幂；buffer 内嵌 |
| `DEFINE_KFIFO(name, type, size)` | 定义并初始化 | 静态分配，buffer 内嵌在结构体中 |
| `DECLARE_KFIFO_PTR(name, type)` | 声明指针型 FIFO | buffer 动态分配，结构体只含指针 |
| `INIT_KFIFO(name)` | 初始化已声明的 FIFO | 配合 `DECLARE_KFIFO` 使用 |
| `kfifo_alloc(fifo, size, gfp)` | 动态分配 buffer | 返回 0 或 `-EINVAL` / `-ENOMEM` |
| `kfifo_init(fifo, buffer, size)` | 用预分配 buffer 初始化 | buffer 大小必须是 2 的幂 |
| `kfifo_free(fifo)` | 释放动态分配的 buffer | 只用于 `kfifo_alloc` 分配的 |

**内联 vs 指针型：**

- `DEFINE_KFIFO(fifo, int, 64)`：buffer 在结构体内，64 个 `int` 直接占用栈/数据段空间
- `DECLARE_KFIFO_PTR(fifo, int)` + `kfifo_alloc`：buffer 从 kmalloc 分配，结构体只有指针

`kfifo_alloc` 会自动 `roundup_pow_of_two(size)`（`lib/kfifo.c:31`），所以传非 2 的幂不会失败（只要 ≥ 2）。但 `DEFINE_KFIFO` 在编译时检查——传非 2 的幂会触发编译错误（`include/linux/kfifo.h:65`）。

### 2.2 查询

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_is_empty(fifo)` | 是否为空 | `in == out` |
| `kfifo_is_full(fifo)` | 是否已满 | `len > mask` |
| `kfifo_avail(fifo)` | 可用空间（元素数） | 记录模式下会减去 recsize 开销 |
| `kfifo_len(fifo)` | 已用元素数 | `in - out` |
| `kfifo_size(fifo)` | 总容量（元素数） | `mask + 1` |
| `kfifo_esize(fifo)` | 每个元素的字节数 | — |
| `kfifo_recsize(fifo)` | 记录长度字段的字节数 | 非记录模式为 0 |
| `kfifo_initialized(fifo)` | 是否已初始化 | 检查 `mask != 0` |

### 2.3 带锁查询

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_is_empty_spinlocked(fifo, lock)` | 持 `spin_lock_irqsave` 检查空 | — |
| `kfifo_is_empty_spinlocked_noirqsave(fifo, lock)` | 持 `spin_lock` 检查空 | 不禁中断 |

### 2.4 单元素操作

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_put(fifo, val)` | 入队一个元素 | 返回 1（成功）或 0（满） |
| `kfifo_get(fifo, val)` | 出队一个元素 | 返回 1（成功）或 0（空） |
| `kfifo_peek(fifo, val)` | 读队首但不取出 | 返回 1 或 0 |

`val` 的语义：`kfifo_put` 的 `val` 是**值**（不是指针）；`kfifo_get` / `kfifo_peek` 的 `val` 是**存放结果的指针**。宏内部通过 `typeof` 区分：

```c
int x = 42;
kfifo_put(&fifo, x);       /* 正确——x 是 int 值 */
kfifo_put(&fifo, &x);      /* 错误——&x 是 int* */

int result;
kfifo_get(&fifo, &result); /* 正确——&result 是 int* */
```

### 2.5 批量操作

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_in(fifo, buf, n)` | 入队 n 个元素 | 返回实际入队数（可能 < n） |
| `kfifo_out(fifo, buf, n)` | 出队 n 个元素 | 返回实际出队数 |
| `kfifo_out_peek(fifo, buf, n)` | 读但不取出 | — |

`n` 是**元素个数**，不是字节数。返回值也是元素个数。

### 2.6 带锁变体

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_in_spinlocked(fifo, buf, n, lock)` | 入队 + `spin_lock_irqsave` | 多写者场景 |
| `kfifo_in_spinlocked_noirqsave(fifo, buf, n, lock)` | 入队 + `spin_lock` | 不禁中断 |
| `kfifo_out_spinlocked(fifo, buf, n, lock)` | 出队 + `spin_lock_irqsave` | 多读者场景 |
| `kfifo_out_spinlocked_noirqsave(fifo, buf, n, lock)` | 出队 + `spin_lock` | 不禁中断 |

**已废弃别名：** `kfifo_in_locked` 和 `kfifo_out_locked` 分别是 `kfifo_in_spinlocked` 和 `kfifo_out_spinlocked` 的别名，将在未来版本移除。

### 2.7 用户空间拷贝

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_from_user(fifo, from, len, copied)` | 从用户空间拷贝数据入队 | `from` 是 `__user` 指针；`copied` 输出实际拷贝字节数 |
| `kfifo_to_user(fifo, to, len, copied)` | 出队数据拷贝到用户空间 | 返回 0 或 `-EFAULT` |

注意：`len` 和 `copied` 的单位是**字节**（不是元素数），与其他 API 不同。

### 2.8 DMA 支持

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_dma_in_prepare(fifo, sgl, nents, len)` | 准备 DMA 入队的 scatterlist | 返回 scatterlist 条目数 |
| `kfifo_dma_in_finish(fifo, len)` | DMA 完成后推进 in 指针 | `len` 是实际接收的**字节数** |
| `kfifo_dma_out_prepare(fifo, sgl, nents, len)` | 准备 DMA 出队的 scatterlist | 返回 scatterlist 条目数 |
| `kfifo_dma_out_finish(fifo, len)` | DMA 完成后推进 out 指针 | `len` 是实际传输的**字节数** |

`kfifo_dma_in_finish` / `kfifo_dma_out_finish` 内部用 `len / sizeof(*type)` 把字节数转换为元素数来推进指针。

### 2.9 记录模式

记录模式支持变长记录的存取。`recsize` 指定用多少字节记录长度（1 或 2），最大记录分别为 255 字节和 65535 字节。

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_in(fifo, buf, n)` | 入队一条记录 | 记录模式下自动在数据前写入长度头 |
| `kfifo_out(fifo, buf, n)` | 出队一条记录 | `n` 是 buffer 大小，实际出队大小由记录长度头决定 |
| `kfifo_peek_len(fifo)` | 获取下一条记录的大小（字节） | 非记录模式返回 `len * esize` |
| `kfifo_skip(fifo)` | 跳过下一条记录 | 推进 out 指针越过整条记录 |

**结构体声明：**

| API | 说明 | 备注 |
|-----|------|------|
| `STRUCT_KFIFO_REC_1(size)` | 1 字节记录长度，buffer 内嵌 | 最大记录 255 字节 |
| `STRUCT_KFIFO_REC_2(size)` | 2 字节记录长度，buffer 内嵌 | 最大记录 65535 字节 |

对应的指针型结构体：`struct kfifo_rec_ptr_1` 和 `struct kfifo_rec_ptr_2`。

### 2.10 重置

| API | 说明 | 备注 |
|-----|------|------|
| `kfifo_reset(fifo)` | 清空 FIFO | 危险——只在独占访问时使用 |
| `kfifo_reset_out(fifo)` | 跳过所有数据 | 只在单读者线程中安全调用 |

## §3 使用模式

### 模式 1：中断→进程通信（1R1W 无锁）

最经典的用法：中断处理程序写，内核线程读。

```c
static DEFINE_KFIFO(event_fifo, struct event, 64);

/* 中断处理程序（写者） */
irqreturn_t my_irq_handler(int irq, void *dev)
{
    struct event ev = { .type = get_event_type(), .timestamp = ktime_get() };
    kfifo_put(&event_fifo, ev);
    return IRQ_HANDLED;
}

/* 内核线程（读者） */
static int my_worker(void *data)
{
    struct event ev;

    while (!kthread_should_stop()) {
        wait_event_interruptible(wq, !kfifo_is_empty(&event_fifo));
        while (kfifo_get(&event_fifo, &ev)) {
            process_event(&ev);
        }
    }
    return 0;
}
```

1R1W 无锁的关键：`in` 只由写者修改，`out` 只由读者修改。`smp_wmb()` 保证数据写入在指针更新之前可见（`lib/kfifo.c:110`、`lib/kfifo.c:149`）。

### 模式 2：多写者场景（加锁）

多个写者需要 spinlock 保护。读者仍然是单个。

```c
static DECLARE_KFIFO_PTR(log_fifo, struct log_entry);
static DEFINE_SPINLOCK(log_lock);

int log_init(void)
{
    return kfifo_alloc(&log_fifo, 256, GFP_KERNEL);
}

void log_event(struct log_entry *entry)
{
    kfifo_in_spinlocked(&log_fifo, entry, 1, &log_lock);
}

void log_drain(void)
{
    struct log_entry entry;
    while (kfifo_out(&log_fifo, &entry, 1))
        process_log(&entry);
}
```

### 模式 3：用户空间接口（字符设备缓冲）

```c
static DECLARE_KFIFO_PTR(user_fifo, unsigned char);

static ssize_t my_write(struct file *file, const char __user *buf,
                        size_t count, loff_t *ppos)
{
    unsigned int copied;
    int ret;

    ret = kfifo_from_user(&user_fifo, buf, count, &copied);
    if (ret)
        return ret;
    return copied;
}

static ssize_t my_read(struct file *file, char __user *buf,
                       size_t count, loff_t *ppos)
{
    unsigned int copied;
    int ret;

    ret = kfifo_to_user(&user_fifo, buf, count, &copied);
    if (ret)
        return ret;
    return copied;
}
```

### 模式 4：DMA 缓冲

```c
struct scatterlist sg[4];
unsigned int nents;

/* 准备 DMA 接收 */
nents = kfifo_dma_in_prepare(&dma_fifo, sg, 4, 1024);
dma_map_sg(dev, sg, nents, DMA_FROM_DEVICE);
/* 启动 DMA ... */

/* DMA 完成中断——len 是实际接收的字节数 */
void dma_complete(void)
{
    kfifo_dma_in_finish(&dma_fifo, actual_bytes);
}
```

### 模式 5：记录模式（变长消息）

```c
/* 声明：buffer 1024 字节，1 字节记录长度（最大记录 255 字节） */
static STRUCT_KFIFO_REC_1(1024) rec_fifo;

void add_record(const char *msg, int len)
{
    kfifo_in(&rec_fifo, msg, len);
}

void drain_records(void)
{
    char buf[256];
    unsigned int len;

    while ((len = kfifo_peek_len(&rec_fifo)) > 0) {
        kfifo_out(&rec_fifo, buf, sizeof(buf));
        process_record(buf, len);
    }
}
```

## §4 陷阱

### 陷阱 1：大小必须是 2 的幂

`kfifo_alloc` 会自动 `roundup_pow_of_two`（`lib/kfifo.c:31`），所以传 100 实际分配 128。但 `DEFINE_KFIFO` 在编译时检查——传非 2 的幂触发编译错误（`include/linux/kfifo.h:65`：`((size < 2) || (size & (size - 1))) ? -1 : size`）。

### 陷阱 2：kfifo_put 的 val 不是指针

`kfifo_put(fifo, val)` 的 `val` 是**元素值**，不是指针。宏内部通过 `typeof(*__tmp->const_type)` 推导类型。如果 FIFO 存 `int`，传 `int` 值；如果存 `struct foo *`，传指针值。

### 陷阱 3：kfifo_in 返回的是元素数不是字节数

`kfifo_in(fifo, buf, n)` 的 `n` 是**元素个数**，返回值也是实际入队的**元素个数**。但 `kfifo_from_user` / `kfifo_to_user` 的 `len` 和 `copied` 是**字节数**——这是两套不同的约定。

### 陷阱 4：kfifo_reset 不是线程安全的

`kfifo_reset` 直接修改 `in` 和 `out`，没有任何同步。只在独占访问（如设备关闭时）使用。

### 陷阱 5：kfifo_reset_out 只在单读者场景安全

`kfifo_reset_out` 把 `out` 设为 `in`，跳过所有数据。如果还有另一个读者在读，会导致数据竞争。

### 陷阱 6：内联 FIFO 不能 kfifo_free

`DEFINE_KFIFO` 声明的 FIFO，buffer 在结构体内，不需要也不能调用 `kfifo_free`。只有 `kfifo_alloc` 分配的才需要。判断方法：`__is_kfifo_ptr(fifo)` 宏——它比较结构体大小，内联版和指针版大小不同。

### 陷阱 7：kfifo_dma_in_finish 的 len 是字节数

`kfifo_dma_in_finish(fifo, len)` 的 `len` 是实际接收的**字节数**（不是元素数）。内部会除以 `sizeof(*type)` 转换为元素数（`include/linux/kfifo.h:751`）。传错单位会导致指针推进错误。

## §5 代码示例

### 示例 1：完整的串行驱动缓冲

```c
#include <linux/kfifo.h>

#define RX_FIFO_SIZE 1024

struct my_uart {
    struct kfifo rx_fifo;
    spinlock_t rx_lock;
};

static int my_uart_probe(struct platform_device *pdev)
{
    struct my_uart *uart = devm_kzalloc(&pdev->dev, sizeof(*uart), GFP_KERNEL);
    spin_lock_init(&uart->rx_lock);
    return kfifo_alloc(&uart->rx_fifo, RX_FIFO_SIZE, GFP_KERNEL);
}

/* 接收中断——多写者用 spinlocked 变体 */
static irqreturn_t my_uart_rx_irq(int irq, void *dev)
{
    struct my_uart *uart = dev;
    unsigned char ch;

    while (hw_data_available(uart)) {
        ch = hw_read(uart);
        kfifo_in_spinlocked(&uart->rx_fifo, &ch, 1, &uart->rx_lock);
    }
    return IRQ_HANDLED;
}

/* 用户态读取 */
static ssize_t my_uart_read(struct file *file, char __user *buf,
                            size_t count, loff_t *ppos)
{
    struct my_uart *uart = file->private_data;
    unsigned int copied;
    int ret;

    ret = kfifo_to_user(&uart->rx_fifo, buf, count, &copied);
    if (ret)
        return ret;
    return copied;
}
```

### 示例 2：记录模式

```c
/* 声明：每条记录最大 256 字节，用 1 字节记录长度 */
static STRUCT_KFIFO_REC_1(1024) rec_fifo;

void add_record(const char *msg, int len)
{
    kfifo_in(&rec_fifo, msg, len);
}

void drain_records(void)
{
    char buf[256];
    unsigned int len;

    while ((len = kfifo_peek_len(&rec_fifo)) > 0) {
        kfifo_out(&rec_fifo, buf, sizeof(buf));
        process_record(buf, len);
    }
}
```

## §6 取证

### 已读文件

| 文件 | 行数 | 读法 |
|------|------|------|
| `include/linux/kfifo.h` | 893 | 全读 |
| `lib/kfifo.c` | 592 | 全读 |

### 关键论断出处

| 论断 | 出处 | 验证命令 |
|------|------|----------|
| kfifo_in 全树 69 处调用 | grep 统计 | `grep -r '\bkfifo_in\b' --include='*.c' . \| wc -l` → 69 |
| kfifo_out 全树 56 处调用 | grep 统计 | `grep -r '\bkfifo_out\b' --include='*.c' . \| wc -l` → 56 |
| kfifo_alloc 全树 67 处调用 | grep 统计 | `grep -r '\bkfifo_alloc\b' --include='*.c' . \| wc -l` → 67 |
| kfifo_is_empty 全树 90 处调用 | grep 统计 | `grep -r '\bkfifo_is_empty\b' --include='*.c' --include='*.h' . \| wc -l` → 90 |
| 1R1W 无锁设计 | `include/linux/kfifo.h:29-37` | 注释明确说明 |
| 大小必须 2 的幂 | `include/linux/kfifo.h:65` | `((size < 2) \|\| (size & (size - 1))) ? -1 : size` |
| kfifo_alloc 自动 round up | `lib/kfifo.c:31` | `size = roundup_pow_of_two(size)` |
| smp_wmb 在 copy_in 后 | `lib/kfifo.c:110` | `smp_wmb()` 在 memcpy 之后、函数返回前 |
| smp_wmb 在 copy_out 后 | `lib/kfifo.c:149` | 同上 |
| kfifo_in_locked 是废弃别名 | `include/linux/kfifo.h:572` | `/* alias for kfifo_in_spinlocked, will be removed */` |
| kfifo_out_locked 是废弃别名 | `include/linux/kfifo.h:646` | 同上 |
| dma_in_finish 用字节数 | `include/linux/kfifo.h:751` | `__kfifo->in += __len / sizeof(*__tmp->type)` |

### 未验证项

- DMA scatterlist 集成的完整流程（`setup_sgl_buf` 的跨页处理）未在实际驱动中逐行核实
- 记录模式的 `__kfifo_in_r` / `__kfifo_out_r` 实现已读，但未在实际使用中验证变长记录的正确性
