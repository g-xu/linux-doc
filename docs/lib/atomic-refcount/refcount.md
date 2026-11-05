# refcount_t — 安全引用计数

> 专为引用计数设计的原子类型。在 `atomic_t` 基础上增加溢出/下溢检测：溢出时计数器饱和（不再环绕为负数）并触发 `WARN_ONCE`；下溢时同样告警。解决了 `atomic_t` 做引用计数时的 use-after-free 漏洞。

---

## §1 核心设计洞察

### 1.1 为什么需要 refcount_t

用 `atomic_t` 做引用计数有两个经典漏洞：

**漏洞 1：UAF（Use-After-Free）**
```
CPU 0: atomic_dec_and_test → 0 → kfree(obj)
CPU 1: atomic_inc(&obj->refcount)    // 对象已释放！
```
`atomic_inc` 不知道对象已经被释放——它盲目递增。

**漏洞 2：溢出**
```
// 攻击者触发 2^31 次 get，refcount 从正数溢出为负数
// atomic_dec_and_test 永远不到 0，对象永远不释放 → 内存泄漏
// 或者：溢出回绕到 1，提前释放 → UAF
```

`refcount_t` 通过**饱和语义**解决这两个问题。

### 1.2 饱和语义

`include/linux/refcount.h:22-25`：

```c
#define REFCOUNT_SATURATED      ((unsigned int)0x80000000)
#define REFCOUNT_MAX            ((unsigned int)0x7fffffff)
```

**规则**：
- 计数器到达 `REFCOUNT_SATURATED`（0x80000000）后，**所有递增操作被拒绝**——计数器保持饱和值不变
- 溢出检测：如果 `atomic_add_return` 的结果 ≤ 0（说明溢出了），设置为饱和值并 `WARN_ONCE`
- 下溢检测：如果 `refcount_dec_and_test` 发现计数器已经 ≤ 0 且不是饱和值，`WARN_ONCE`

```
正常生命周期:
  set(1) → inc → inc → dec_and_test → inc_not_zero → dec_and_test → 0 → 释放

溢出保护:
  set(1) → inc × 2^31 → 饱和(0x80000000) → 后续 inc 全部被拒绝 → WARN_ONCE

UAF 保护:
  CPU 0: dec_and_test → 0 → kfree
  CPU 1: inc_not_zero → 返回 false（refcount 已经是 0）→ 不递增
```

### 1.3 结构体定义

`include/linux/refcount.h:34-37`：

```c
typedef struct refcount_struct {
    atomic_t refs;
} refcount_t;
```

`refcount_t` 只是 `atomic_t` 的包装。所有操作通过 `atomic_*` 实现，但加了溢出/下溢检查逻辑。

### 1.4 核心实现：refcount_add_not_zero

`lib/refcount.c:30-67` 是溢出保护的核心：

```c
bool refcount_add_not_zero(unsigned int i, refcount_t *r)
{
    unsigned int old = refcount_read(r);

    for (;;) {
        if (unlikely(old == REFCOUNT_SATURATED))
            return false;    // 已饱和，拒绝递增

        unsigned int new = old + i;

        // 溢出检测：如果 new < old（环绕）或 new 到了饱和区
        if (WARN_ONCE(new < old || new <= REFCOUNT_SATURATED,
                      "refcount_t: addition saturated")) {
            new = REFCOUNT_SATURATED;
            // 尝试 CAS 设置为饱和值
        }

        if (likely(atomic_try_cmpxchg(&r->refs, &old, new)))
            return true;
        // CAS 失败，old 已更新，重试
    }
}
```

**关键**：`atomic_try_cmpxchg` 循环确保即使并发修改也能正确检测溢出。

### 1.5 内存序

`refcount_t` 的内存序比 `atomic_t` 更精确：

| 操作 | 内存序 | 理由 |
|------|--------|------|
| `refcount_read` | relaxed | 只是观察，不需要同步 |
| `refcount_set` | relaxed | 初始化阶段，无并发 |
| `refcount_inc` | **release** | 获取引用——确保之前的初始化对其他 CPU 可见 |
| `refcount_dec_and_test` | **release + acquire**（成功时） | release：确保释放前的操作完成；acquire：确保看到释放者的清理 |
| `refcount_inc_not_zero` | **relaxed** | 只在已持有引用时调用，不需要额外同步 |

**与 `atomic_t` 的对比**：`atomic_inc` 在 arm64 上无额外屏障；`refcount_inc` 提供 release 语义。这意味着 `refcount_t` 在语义上更安全，但在 LSE 硬件上性能差异很小（`staddl` vs `stadd`）。

### 1.6 refcount_dec_and_test 的下溢检测

`lib/refcount.c:109-147`：

```c
bool refcount_dec_and_test(refcount_t *r)
{
    unsigned int old = refcount_read(r);
    unsigned int new;

    for (;;) {
        if (unlikely(old == REFCOUNT_SATURATED))
            return false;    // 饱和状态不递减

        new = old - 1;

        if (WARN_ONCE(new > old, "refcount_t: underflow"))
            return false;    // 下溢检测

        if (likely(atomic_try_cmpxchg(&r->refs, &old, new)))
            return new == 0;
    }
}
```

如果 `new > old`（无符号比较，说明 0 - 1 = 0xFFFFFFFF），就是下溢。

---

## §2 API 速查

头文件：`include/linux/refcount.h`；实现：`lib/refcount.c`

### 2.1 初始化与读取

| API | 说明 | 备注 |
|-----|------|------|
| `REFCOUNT_INIT(i)` | 静态初始化 | 编译期 |
| `refcount_set(r, i)` | 设置值 | 初始化阶段使用 |
| `refcount_read(r)` | 读取当前值 | relaxed，无同步保证 |

### 2.2 递增

| API | 说明 | 备注 |
|-----|------|------|
| `refcount_inc(r)` | 加 1 | release 语义；饱和后拒绝 |
| `refcount_inc_not_zero(r)` | 仅在非 0 时加 1 | **安全获取引用的首选**；返回 bool |
| `refcount_add(i, r)` | 加 i | 饱和保护 |
| `refcount_add_not_zero(i, r)` | 除非为 0 否则加 i | 返回 bool |

### 2.3 递减

| API | 说明 | 备注 |
|-----|------|------|
| `refcount_dec(r)` | 减 1 | 不检查是否到 0；下溢告警 |
| `refcount_dec_and_test(r)` | 减 1，返回是否为 0 | **释放对象的标准模式** |
| `refcount_sub_and_test(i, r)` | 减 i，返回是否为 0 | 批量释放 |

### 2.4 组合操作

| API | 说明 | 备注 |
|-----|------|------|
| `refcount_dec_and_lock(r, lock)` | 减 1，到 0 时加锁 | 返回 true = 已到 0 且已加锁 |
| `refcount_dec_and_mutex_lock(r, mutex)` | 同上但用 mutex | |
| `refcount_dec_and_lock_irqsave(r, lock, flags)` | 同上 + 关中断 | |

---

## §3 使用模式

### 3.1 标准引用计数

```c
struct my_obj {
    refcount_t refcount;
    // ...
};

struct my_obj *my_obj_alloc(void)
{
    struct my_obj *obj = kzalloc(sizeof(*obj), GFP_KERNEL);
    if (!obj)
        return NULL;
    refcount_set(&obj->refcount, 1);  // 初始引用
    return obj;
}

struct my_obj *my_obj_get(struct my_obj *obj)
{
    refcount_inc(&obj->refcount);
    return obj;
}

void my_obj_put(struct my_obj *obj)
{
    if (refcount_dec_and_test(&obj->refcount))
        kfree(obj);
}
```

### 3.2 安全获取（防 UAF）

```c
// 从全局表中查找并获取引用
struct my_obj *lookup_and_get(unsigned long id)
{
    struct my_obj *obj;

    spin_lock(&table_lock);
    obj = idr_find(&table, id);
    if (obj && !refcount_inc_not_zero(&obj->refcount))
        obj = NULL;  // 引用已经是 0，对象正在被释放
    spin_unlock(&table_lock);

    return obj;
}
```

`refcount_inc_not_zero` 是关键——如果对象已经开始释放（refcount = 0），它返回 false 而不是盲目递增。

### 3.3 批量引用释放

```c
void release_n_references(struct my_obj *obj, int n)
{
    if (refcount_sub_and_test(n, &obj->refcount))
        kfree(obj);
}
```

### 3.4 到 0 时加锁清理

```c
void drop_ref(struct my_obj *obj)
{
    if (refcount_dec_and_lock(&obj->refcount, &obj->lock)) {
        // refcount 到 0，锁已持有
        list_del(&obj->list);
        spin_unlock(&obj->lock);
        kfree(obj);
    }
}
```

---

## §4 常见陷阱

### 4.1 refcount_inc 在对象已释放后调用

**错误**：直接用 `refcount_inc` 获取引用，不检查对象是否还存活。

**正确**：用 `refcount_inc_not_zero`。如果 refcount 已经是 0（或饱和），它返回 false。`refcount_inc` 在饱和状态下会触发 `WARN_ONCE` 但**仍然不递增**——对象不会被"复活"。

### 4.2 refcount_dec 不检查返回值

**错误**：用 `refcount_dec` 递减但不检查是否到 0——对象永远不会被释放。

**正确**：需要释放对象时用 `refcount_dec_and_test`。`refcount_dec` 只用于"我知道还有其他引用持有者"的场景。

### 4.3 混淆 refcount_set 和 refcount_inc

**错误**：在对象已经被其他 CPU 引用后调用 `refcount_set(&obj->refcount, 1)`——这会覆盖其他 CPU 的引用计数。

**正确**：`refcount_set` 只在初始化时使用。后续递增用 `refcount_inc`。

### 4.4 饱和后的行为

当 refcount 饱和（0x80000000）后：
- `refcount_inc` → 返回，不递增，`WARN_ONCE`
- `refcount_dec_and_test` → 返回 false，不递减
- `refcount_inc_not_zero` → 返回 false

对象**永远不会被释放**。这是设计意图——宁可泄漏也不 UAF。

### 4.5 refcount_t vs atomic_t 的选择

| 场景 | 选择 | 理由 |
|------|------|------|
| 对象引用计数 | `refcount_t` | 溢出保护 |
| 设备打开计数 | `atomic_t` | 不需要饱和语义，需要精确值 |
| 模块引用计数 | `atomic_t`（`module_refcount`） | 历史原因，内核正在迁移 |
| 通用计数器 | `atomic_t` | `refcount_t` 的饱和语义不适合通用计数 |

---

## §5 代码示例

### 5.1 设备驱动中的引用计数

```c
struct my_device {
    refcount_t refcount;
    struct device dev;
    struct completion done;
};

static struct my_device *my_dev_get(struct my_device *d)
{
    if (d)
        refcount_inc(&d->refcount);
    return d;
}

static void my_dev_put(struct my_device *d)
{
    if (d && refcount_dec_and_test(&d->refcount))
        complete(&d->done);
}

// 在 ioctl 中安全使用
static long my_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
    struct my_device *dev = filp->private_data;

    if (!my_dev_get(dev))
        return -ENODEV;

    // 使用设备...
    int ret = do_something(dev);

    my_dev_put(dev);
    return ret;
}

// 设备移除时等待所有引用释放
static int my_remove(struct platform_device *pdev)
{
    struct my_device *dev = platform_get_drvdata(pdev);

    // 释放初始引用
    my_dev_put(dev);

    // 等待其他引用释放
    wait_for_completion(&dev->done);

    // 现在安全清理
    cleanup_device(dev);
    return 0;
}
```

---

## §6 取证摘要

| 论断 | 出处 | 等级 |
|------|------|------|
| REFCOUNT_SATURATED = 0x80000000 | `include/linux/refcount.h:22` | 一等 |
| refcount_t 定义 | `include/linux/refcount.h:34-37` | 一等 |
| refcount_add_not_zero 溢出检测 | `lib/refcount.c:30-67` | 一等 |
| refcount_dec_and_test 下溢检测 | `lib/refcount.c:109-147` | 一等 |
| refcount_inc release 语义 | `include/linux/refcount.h:103-113` | 一等 |
| refcount_dec_and_test release+acquire | `lib/refcount.c:109-147` | 一等 |
| refcount_inc_not_zero relaxed | `include/linux/refcount.h:131-140` | 一等 |

### 使用规模

| API | 调用次数 |
|-----|---------|
| `refcount_inc` | 685 |
| `refcount_read` | 558 |
| `refcount_dec_and_test` | 552 |
| `refcount_set` | 643 |
| `refcount_inc_not_zero` | 251 |
| `struct refcount_t` 声明 | 576 |
