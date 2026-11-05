# ERR_PTR / IS_ERR — 错误指针框架

## §0 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/err.h` | 68 | 全部宏和内联函数定义 |
| `lib/errname.c` | ~200 | 错误码到名称的映射（用于打印） |

头文件：`include/linux/err.h`。

---

## §1 定位

内核函数需要同时返回"成功时的指针"和"失败时的错误码"。C 语言没有多返回值，ERR_PTR 框架用一种巧妙的方式解决了这个问题：**把错误码编码为指针值**。

---

## §2 核心原理

```
地址空间布局：

0x00000000_00000000  ← NULL
    ...
0x00000000_00000FFF  ← 最后 4096 字节（MAX_ERRNO = 4095）
                       这些地址永远不会被合法分配

-1  → 0xFFFF_FFFF_FFFF_FFFF  = ERR_PTR(-1)  = -EPERM
-2  → 0xFFFF_FFFF_FFFF_FFFE  = ERR_PTR(-2)  = -ENOENT
-12 → 0xFFFF_FFFF_FFFF_FFF4  = ERR_PTR(-12) = -ENOMEM
```

内核永远不会在地址空间顶部 4096 字节内分配内存，所以这些地址可以安全地用作错误码。

---

## §3 核心 API

头文件：`include/linux/err.h`

| API | 行号 | 说明 |
|-----|------|------|
| `ERR_PTR(error)` | 24 | 将错误码转换为指针：`(void *)error` |
| `PTR_ERR(ptr)` | 29 | 将指针转换回错误码：`(long)ptr` |
| `IS_ERR(ptr)` | 34 | 检查指针是否为错误值 |
| `IS_ERR_VALUE(x)` | 22 | 底层检查：`(unsigned long)(x) >= (unsigned long)-MAX_ERRNO` |
| `IS_ERR_OR_NULL(ptr)` | 39 | 检查是否为错误或 NULL |
| `ERR_CAST(ptr)` | 51 | 显式类型转换（保留错误值） |
| `PTR_ERR_OR_ZERO(ptr)` | 57 | 如果是错误返回错误码，否则返回 0 |

### 常量

```c
#define MAX_ERRNO  4095  // err.h:18
```

---

## §4 使用模式

### 4.1 基本模式

```c
struct my_struct *create_thing(void)
{
    struct my_struct *thing;

    thing = kmalloc(sizeof(*thing), GFP_KERNEL);
    if (!thing)
        return ERR_PTR(-ENOMEM);  // 错误码编码为指针

    if (!init_thing(thing)) {
        kfree(thing);
        return ERR_PTR(-EINVAL);
    }

    return thing;  // 成功：返回合法指针
}

void use_thing(void)
{
    struct my_struct *thing = create_thing();

    if (IS_ERR(thing)) {
        int err = PTR_ERR(thing);  // 提取错误码
        pr_err("failed: %d\n", err);
        return;
    }

    // 使用 thing...
    kfree(thing);
}
```

### 4.2 PTR_ERR_OR_ZERO 简化

```c
int my_init(void)
{
    struct resource *res = get_resource();
    return PTR_ERR_OR_ZERO(res);  // 成功返回 0，失败返回错误码
}
```

### 4.3 ERR_CAST 传递错误

```c
struct b *get_b(void)
{
    struct a *a = get_a();
    if (IS_ERR(a))
        return ERR_CAST(a);  // 类型转换但保留错误值
    ...
}
```

---

## §5 为什么这样设计

### 5.1 替代方案的问题

| 方案 | 问题 |
|------|------|
| 返回 `NULL` 表示失败 | 无法区分不同错误（ENOMEM vs EINVAL vs EBUSY） |
| 通过输出参数返回指针 | 函数签名变复杂，调用者需要声明变量 |
| 返回 `struct { ptr, errno }` | C 没有多返回值，需要结构体 |

### 5.2 ERR_PTR 的优势

1. **零开销**：只是类型转换，无额外内存或计算
2. **类型安全**：`__force` 标注让 sparse 检查类型转换
3. **信息丰富**：保留完整错误码
4. **调用者友好**：一个返回值，IS_ERR 检查

---

## §6 arm64 特殊性

ERR_PTR 是架构无关的。`MAX_ERRNO = 4095` 在所有架构上相同。arm64 使用 48 位虚拟地址（或 52 位 with LVA），顶部 4096 字节永远不会被映射。

---

## §7 使用统计

| API | 使用文件数 |
|-----|-----------|
| `IS_ERR` | ~3000+ |
| `PTR_ERR` | ~2500+ |
| `ERR_PTR` | ~2000+ |
| `IS_ERR_OR_NULL` | ~500+ |
| `PTR_ERR_OR_ZERO` | ~300+ |
| `ERR_CAST` | ~100+ |

ERR_PTR 是内核中最广泛使用的错误传递机制之一。
