# atomic_t — 原子操作

> 内核最基础的并发原语。对 `int`（或 `long`/`s64`）的不可分割读写和算术操作，保证在多 CPU 并发访问下不会产生中间状态。arm64 上使用 LSE（Large System Extensions）原子指令实现，性能远优于早期的 LL/SC 循环。

---

## §1 核心设计洞察

### 1.1 三个层次

```
atomic_read(&v) 调用
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  API 层:  atomic_t (int) / atomic_long_t (long)         │
│           / atomic64_t (s64)                            │
│           include/linux/atomic.h 统一入口               │
├─────────────────────────────────────────────────────────┤
│  仪器层:  atomic-instrumented.h                         │
│           KASAN 检查 + KCSAN 数据竞争检测               │
│           2222 行，宏生成所有包装                        │
├─────────────────────────────────────────────────────────┤
│  架构层:  arch/arm64/include/asm/atomic.h (280 行)      │
│           LSE: ldadd/stadd/clrex 等硬件原子指令          │
│           回退: LL/SC (ldxr/stxr) 循环                  │
└─────────────────────────────────────────────────────────┘
```

### 1.2 结构体定义

`include/linux/types.h:222-231`：

```c
typedef struct {
    int counter;
} atomic_t;

typedef struct {
    long counter;
} atomic_long_t;

typedef struct {
    s64 counter;
} atomic64_t;
```

三个类型结构完全相同，只是内部整数宽度不同。在 arm64 上 `atomic_long_t` 和 `atomic64_t` 都是 64 位。

### 1.3 arm64 实现：LSE vs LL/SC

arm64 有两种实现路径（`arch/arm64/include/asm/atomic.h`、`arch/arm64/include/asm/lse.h`）：

**LSE 路径**（`CONFIG_ARM64_LSE_ATOMICS`，armv8.1+）：
```c
// atomic_add_return — 一条硬件指令完成
static inline int atomic_add_return_relaxed(int i, atomic_t *v)
{
    return __lse_atomic_add_return_relaxed(i, v);
    // 展开为: ldaddal w0, w1, [x2]  — 硬件原子加 + acquire/release
}
```

**LL/SC 回退**（无 LSE 支持时）：
```c
// atomic_add_return — 循环直到成功
static inline int atomic_add_return_relaxed(int i, atomic_t *v)
{
    unsigned long tmp;
    int result;
    asm volatile(
    "   prfm    pstl1strm, %2\n"
    "1: ldxr   %w0, %2\n"        // 加载独占
    "   add     %w0, %w0, %w3\n"  // 加法
    "   stlxr   %w1, %w0, %2\n"  // 存储独占（可能失败）
    "   cbnz    %w1, 1b\n"        // 失败则重试
    : ... );
}
```

**性能差异**：LSE `ldadd` 是单条指令，硬件保证原子性；LL/SC 循环在竞争下可能多次重试。在大型系统（> 8 核）上 LSE 优势明显。

### 1.4 内存序：四种变体

每个原子操作最多有四种内存序变体：

| 变体 | 后缀 | 语义 | 典型用途 |
|------|------|------|---------|
| 全序 | 无后缀 | acquire + release | 默认，最强保证 |
| Relaxed | `_relaxed` | 无额外屏障 | 计数器，不需要同步 |
| Acquire | `_acquire` | 后续读写不重排到此操作之前 | 读锁获取 |
| Release | `_release` | 此前读写不重排到此操作之后 | 写锁释放 |

**arm64 映射**：
- 全序 = `ldar`/`stlr`（或 LSE + `dmb`）
- `_relaxed` = `ldr`/`str`（或 LSE 无屏障）
- `_acquire` = `ldar`
- `_release` = `stlr`

### 1.5 atomic_read / atomic_set 的特殊性

```c
// include/linux/atomic/atomic-instrumented.h:36
static __always_inline int atomic_read(const atomic_t *v)
{
    int r = __atomic_read(v);    // arch_atomic_read = READ_ONCE(v->counter)
    kcsan_atomic_op(r);
    return r;
}

// arch/arm64/include/asm/atomic.h:16
#define arch_atomic_read(v)     READ_ONCE((v)->counter)
#define arch_atomic_set(v,i)    WRITE_ONCE((v)->counter, (i))
```

**关键**：`atomic_read` 和 `atomic_set` 只是 `READ_ONCE`/`WRITE_ONCE`——它们是**单次访问的原子性**（不会产生撕裂读），但**不提供任何内存序保证**。它们不与其他 CPU 上的操作同步。

这意味着 `atomic_set(&v, 1); atomic_read(&v)` 在两个 CPU 上可能看到不同的值——中间可能有其他 `atomic_add` 操作。

### 1.6 仪器层：KASAN 和 KCSAN

`include/linux/atomic/atomic-instrumented.h`（2222 行）为每个原子操作生成带指针检查的包装：
- **KASAN**：检查指针是否指向已释放/越界的内存
- **KCSAN**：标记数据竞争检测点

所有 `atomic_*()` 调用实际经过这层包装，然后才到达架构实现。

### 1.7 atomic_long_t：自动适配宽度

`include/linux/atomic/atomic-long.h`（342 行）：在 64 位系统上 `atomic_long_t` 直接映射到 `atomic64_t`，在 32 位上映射到 `atomic_t`。arm64 上两者等价。

---

## §2 API 速查

头文件：`include/linux/atomic.h`；架构实现：`arch/arm64/include/asm/atomic.h`

### 2.1 初始化

| API | 说明 | 备注 |
|-----|------|------|
| `ATOMIC_INIT(i)` | 静态初始化 | 编译期，`{ .counter = (i) }` |
| `atomic_set(v, i)` | 设置值 | `WRITE_ONCE`，无内存序保证 |
| `atomic_set_release(v, i)` | 设置值 + release 语义 | |

### 2.2 读取

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_read(v)` | 读取值 | `READ_ONCE`，无内存序保证 |
| `atomic_read_acquire(v)` | 读取 + acquire 语义 | |

### 2.3 算术操作

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_add(i, v)` | 加 i | 无返回值，relaxed |
| `atomic_add_return(i, v)` | 加 i 并返回新值 | 全序 |
| `atomic_add_return_relaxed(i, v)` | 加 i 并返回新值 | 无屏障 |
| `atomic_sub(i, v)` | 减 i | |
| `atomic_sub_return(i, v)` | 减 i 并返回新值 | |
| `atomic_inc(v)` | 加 1 | 无返回值 |
| `atomic_inc_return(v)` | 加 1 并返回新值 | |
| `atomic_dec(v)` | 减 1 | |
| `atomic_dec_return(v)` | 减 1 并返回新值 | |

### 2.4 条件操作

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_dec_and_test(v)` | 减 1，返回是否为 0 | **引用计数释放的常用模式** |
| `atomic_inc_not_zero(v)` | 仅在非 0 时加 1 | 引用计数获取，返回操作是否成功 |
| `atomic_add_unless(v, a, u)` | 除非等于 u 否则加 a | 通用条件加 |
| `atomic_sub_and_test(i, v)` | 减 i，返回是否为 0 | |
| `atomic_add_negative(i, v)` | 加 i，返回结果是否为负 | |
| `atomic_dec_if_positive(v)` | 仅在 > 0 时减 1 | 返回减后的值，-1 表示未执行 |

### 2.5 比较与交换

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_cmpxchg(v, old, new)` | 比较并交换 | 全序；返回旧值 |
| `atomic_cmpxchg_relaxed(v, old, new)` | 比较并交换 | 无屏障 |
| `atomic_xchg(v, new)` | 交换 | 全序；返回旧值 |
| `atomic_xchg_relaxed(v, new)` | 交换 | 无屏障 |

### 2.6 位操作（atomic_t 上的）

| API | 说明 | 备注 |
|-----|------|------|
| `atomic_and(i, v)` | 按位与 | |
| `atomic_or(i, v)` | 按位或 | |
| `atomic_xor(i, v)` | 按位异或 | |
| `atomic_andnot(i, v)` | 按位与非 | |

### 2.7 atomic_long_t 等价 API

每个 `atomic_*` 都有 `atomic_long_*` 对应版本。在 arm64 上直接映射到 `atomic64_*`。

---

## §3 使用模式

### 3.1 引用计数（经典模式）

```c
struct my_obj {
    atomic_t refcount;
    // ...
};

struct my_obj *get_obj(struct my_obj *obj)
{
    atomic_inc(&obj->refcount);
    return obj;
}

void put_obj(struct my_obj *obj)
{
    if (atomic_dec_and_test(&obj->refcount))
        kfree(obj);
}
```

**注意**：这种模式有 use-after-free 风险——如果 `refcount` 到 0 后对象被释放，另一个 CPU 的 `atomic_inc` 就是访问已释放内存。生产代码应使用 `refcount_t`（见 `output/atomic-refcount/refcount.md`）。

### 3.2 状态标志

```c
#define DEV_INITIALIZING    0
#define DEV_READY           1
#define DEV_SHUTDOWN        2

atomic_t state;

int init_device(void)
{
    // ... 初始化 ...
    atomic_set_release(&state, DEV_READY);
}

int use_device(void)
{
    if (atomic_read_acquire(&state) != DEV_READY)
        return -ENODEV;
    // 安全使用设备
}
```

### 3.3 原子计数器

```c
static atomic_t active_requests = ATOMIC_INIT(0);

int submit_request(struct request *req)
{
    if (atomic_read(&active_requests) >= MAX_CONCURRENT)
        return -EBUSY;
    atomic_inc(&active_requests);
    // ... 提交请求 ...
}

void complete_request(struct request *req)
{
    atomic_dec(&active_requests);
}
```

### 3.4 一次性事件（inc_not_zero 模式）

```c
// 安全获取引用——仅在对象还存活时
bool try_get_ref(struct my_obj *obj)
{
    return atomic_inc_not_zero(&obj->refcount);
    // 返回 true = 成功获取引用
    // 返回 false = refcount 已经是 0，对象正在被释放
}
```

### 3.5 cmpxchg 实现无锁算法

```c
// 无锁栈的 push
void push(struct node *new_head)
{
    struct node *old;
    do {
        old = atomic_read(&stack_head);
        new_head->next = old;
    } while (atomic_cmpxchg(&stack_head, old, new_head) != old);
}
```

---

## §4 常见陷阱

### 4.1 atomic_read 不是"原子读"

**错误**：认为 `atomic_read` 会看到"最新"值或与其他操作同步。

**事实**：`atomic_read` = `READ_ONCE`。它保证不产生撕裂读（32 位完整读出），但**不提供任何内存序保证**。如果另一个 CPU 在 `atomic_set` 之后做了 `atomic_add`，`atomic_read` 可能看到 `set` 的值而不是 `add` 后的值。

**正确**：需要跨操作同步时用 `atomic_read_acquire` 或在读后加 `smp_mb()`。

### 4.2 atomic_t 做引用计数的 UAF 风险

**错误**：用 `atomic_t` 做引用计数，`atomic_inc` 在 `atomic_dec_and_test` 返回 0 之后执行。

```c
// CPU 0                          // CPU 1
put_obj(obj):                    get_obj(obj):
  atomic_dec_and_test → 0          // 对象已被 kfree！
  kfree(obj)                       atomic_inc(&obj->refcount)  // UAF!
```

**正确**：使用 `refcount_t`，它在饱和后拒绝递增并触发 `WARN_ONCE`。或者使用 `atomic_inc_not_zero` 做安全获取。

### 4.3 非返回值的原子操作不提供同步点

**错误**：依赖 `atomic_inc(&v)` 的顺序性来同步其他数据。

**事实**：`atomic_inc`（无 `_return` 后缀）在 arm64 上是 `stadd`（LSE）或 `ldxr/stxr`（LL/SC），它们**不提供 acquire/release 语义**。如果需要与其他操作同步，用 `atomic_add_return` 或显式加屏障。

### 4.4 atomic_set 和并发读的竞争

**错误**：一个 CPU `atomic_set(&v, 0)` 的同时另一个 CPU `atomic_add(&v, 5)`。

**事实**：`atomic_set` 是 `WRITE_ONCE`——它不与 RMW（read-modify-write）操作原子地协调。最终结果可能是 0 或 5，取决于时序。

**正确**：如果需要"重置并等待所有操作完成"，用 `atomic_xchg`（全序）或先确保没有其他操作在进行。

### 4.5 忘记 _relaxed 变体的存在

`atomic_add` 和 `atomic_add_return` 的内存序不同。前者只是 RMW 无额外屏障，后者提供全序。如果需要"加并返回"但不需要屏障，用 `atomic_add_return_relaxed` 可以获得更好的性能。

### 4.6 32 位溢出

`atomic_t` 是 32 位有符号整数。`atomic_add` 可能溢出（C 中有符号溢出是未定义行为，但内核编译器用 `-fno-strict-overflow` 保证环绕）。如果计数器可能超过 2^31，用 `atomic64_t`。

---

## §5 代码示例

### 5.1 设备驱动中的原子操作

```c
struct my_device {
    atomic_t open_count;
    atomic_t flags;
    atomic_t pending_io;
};

#define FLAG_SUSPENDED  BIT(0)
#define FLAG_ERROR      BIT(1)

static int my_open(struct inode *inode, struct file *filp)
{
    struct my_device *dev = container_of(inode->i_cdev, struct my_device, cdev);

    if (atomic_read(&dev->flags) & FLAG_ERROR)
        return -EIO;

    atomic_inc(&dev->open_count);
    filp->private_data = dev;
    return 0;
}

static int my_release(struct inode *inode, struct file *filp)
{
    struct my_device *dev = filp->private_data;
    atomic_dec(&dev->open_count);
    return 0;
}

// ioctl: 原子地检查并设置挂起标志
static long my_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
    struct my_device *dev = filp->private_data;
    int old;

    switch (cmd) {
    case MY_IOC_SUSPEND:
        old = atomic_cmpxchg(&dev->flags, 0, FLAG_SUSPENDED);
        if (old != 0)
            return -EBUSY;  // 已有标志设置
        // 等待所有 IO 完成
        while (atomic_read(&dev->pending_io) > 0)
            cpu_relax();
        return 0;

    case MY_IOC_RESUME:
        old = atomic_cmpxchg(&dev->flags, FLAG_SUSPENDED, 0);
        if (old != FLAG_SUSPENDED)
            return -EINVAL;
        return 0;
    }
    return -ENOTTY;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| atomic_t 定义 | `include/linux/types.h:222` | 一等 |
| atomic64_t 定义 | `include/linux/types.h:227` | 一等 |
| arch_atomic_read = READ_ONCE | `arch/arm64/include/asm/atomic.h:16` | 一等 |
| arch_atomic_set = WRITE_ONCE | `arch/arm64/include/asm/atomic.h:18` | 一等 |
| arm64 atomic.h 行数 | `arch/arm64/include/asm/atomic.h`（280 行） | 一等 |
| LSE 原子实现 | `arch/arm64/include/asm/lse.h`（173 行） | 一等 |
| atomic-instrumented.h KASAN 包装 | `include/linux/atomic/atomic-instrumented.h`（2222 行） | 一等 |
| atomic-long.h 宽度适配 | `include/linux/atomic/atomic-long.h`（342 行） | 一等 |
| atomic-arch-fallback.h | `include/linux/atomic/atomic-arch-fallback.h`（822 行） | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `atomic_read` | 5,863 |
| `atomic_set` | 3,289 |
| `atomic_inc` | 2,536 |
| `atomic_dec` | 1,597 |
| `atomic_add` | 267 |
| `atomic_sub` | 186 |
| `atomic_cmpxchg` | 200 |
| `atomic_inc_not_zero` | 81 |
| `atomic_dec_and_test` | 510 |
| `atomic_add_return` | 112 |
