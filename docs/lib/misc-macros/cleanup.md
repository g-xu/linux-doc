# cleanup.h — 基于作用域的资源管理

## §0 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/cleanup.h` | 171 | 全部宏定义 |
| `include/linux/compiler_attributes.h` | — | `__cleanup` 属性定义（行 82） |

头文件：`include/linux/cleanup.h`。

---

## §1 定位

cleanup.h 提供**基于作用域的资源自动释放**机制，是 Linux 6.1 引入的新特性。它利用 GCC/Clang 的 `__attribute__((__cleanup__(func)))` 扩展，在变量离开作用域时自动调用清理函数。

**核心价值**：消除 goto 链式清理模式，防止资源泄漏。

---

## §2 底层机制

`__cleanup` 属性（`include/linux/compiler_attributes.h:82`）：

```c
#define __cleanup(func) __attribute__((__cleanup__(func)))
```

当带此属性的变量离开作用域时，编译器自动插入对 `func(&var)` 的调用——无论是正常返回、提前 return、break 还是 continue。

---

## §3 三层 API

### 3.1 基础层：`DEFINE_FREE` + `__free`

```c
// 定义清理函数
DEFINE_FREE(kfree, void *, if (_T) kfree(_T))

// 使用
struct obj *p __free(kfree) = kmalloc(sizeof(*p), GFP_KERNEL);
if (!p)
    return NULL;
// p 离开作用域时自动调用 kfree(p)
```

| 宏 | 行号 | 说明 |
|----|------|------|
| `DEFINE_FREE(name, type, free)` | 37 | 定义清理函数。`_T` 是变量值的占位符 |
| `__free(name)` | 40 | 变量属性：附加名为 `name` 的清理函数 |
| `no_free_ptr(p)` | 42 | 转移所有权：读取并置 NULL，抑制清理 |
| `return_ptr(p)` | 45 | `return no_free_ptr(p)` — 返回但抑制清理 |

### 3.2 类层：`DEFINE_CLASS` + `CLASS`

用于需要构造和析构的类型（如文件描述符）：

```c
// 定义类
DEFINE_CLASS(fdget, struct fd, fdput(_T), fdget(fd), int fd)

// 使用
CLASS(fdget, f)(fd);
if (!f.file)
    return -EBADF;
// f 离开作用域时自动调用 fdput(f)
```

| 宏 | 行号 | 说明 |
|----|------|------|
| `DEFINE_CLASS(name, type, exit, init, init_args...)` | 71 | 定义析构函数和构造函数 |
| `EXTEND_CLASS(name, ext, init, init_args...)` | 78 | 从已有类派生新类 |
| `CLASS(name, var)` | 85 | 声明变量并附加清理 |

### 3.3 锁守卫层：`DEFINE_GUARD` + `guard` / `scoped_guard`

专门用于锁的简化封装：

```c
// 定义锁守卫
DEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))

// 使用方式 1：guard — 到函数末尾解锁
guard(mutex)(&my_mutex);
// 临界区...
// 函数返回时自动 unlock

// 使用方式 2：scoped_guard — 到下一个 compound statement 结束
scoped_guard(mutex, &my_mutex) {
    // 临界区
}
// 离开大括号时自动 unlock
```

| 宏 | 行号 | 说明 |
|----|------|------|
| `DEFINE_GUARD(name, type, lock, unlock)` | 105 | 定义锁守卫（封装 DEFINE_CLASS） |
| `guard(name)` | 108 | 匿名作用域守卫 |
| `scoped_guard(name, args...)` | 111 | for 循环技巧绑定到下一个语句块 |

### 3.4 带类型的锁守卫

| 宏 | 行号 | 说明 |
|----|------|------|
| `DEFINE_LOCK_GUARD_0(name, lock, unlock, ...)` | 167 | 无原生类型的锁（RCU、preempt） |
| `DEFINE_LOCK_GUARD_1(name, type, lock, unlock, ...)` | 163 | 有类型的锁（mutex、spinlock） |

`_0` 变体生成 `void *lock = (void*)1`，`_1` 变体生成 `type *lock`。

---

## §4 树内预定义的守卫

`include/linux/mutex.h:250-251`：

```c
DEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))
DEFINE_FREE(mutex, struct mutex *, if (_T) mutex_unlock(_T))
```

`include/linux/sched/task.h:153`：

```c
DEFINE_FREE(put_task, struct task_struct *, if (_T) put_task_struct(_T))
```

---

## §5 与传统 goto 清理的对比

### 传统方式

```c
int my_func(void)
{
    void *buf = kmalloc(1024, GFP_KERNEL);
    struct file *f = NULL;
    int ret = 0;

    if (!buf)
        return -ENOMEM;

    f = filp_open("/tmp/data", O_RDONLY, 0);
    if (IS_ERR(f)) {
        ret = PTR_ERR(f);
        goto out_free;
    }

    ret = process(buf, f);

    filp_close(f, NULL);
out_free:
    kfree(buf);
    return ret;
}
```

### cleanup.h 方式

```c
int my_func(void)
{
    void *buf __free(kfree) = kmalloc(1024, GFP_KERNEL);
    if (!buf)
        return -ENOMEM;

    // 假设也有 file 的 __free 定义
    // struct file *f __free(fput) = filp_open(...);

    return process(buf, f);
    // buf 和 f 自动释放，无需 goto
}
```

---

## §6 使用注意事项

1. **`__free` 变量不能跨作用域返回**：需要返回时用 `return_ptr(p)` 转移所有权
2. **清理函数必须处理 NULL**：`if (_T) kfree(_T)` — 因为分配可能失败
3. **`no_free_ptr` 是转移语义**：类似 `std::move`，将所有权从当前作用域移出
4. **`scoped_guard` 的作用域**：绑定到**下一个** compound statement（大括号块），不是当前作用域

---

## §7 arm64 特殊性

cleanup.h 完全架构无关。`__cleanup` 属性由 GCC/Clang 在代码生成阶段处理，与目标架构无关。

---

## §8 使用统计

| 符号 | 出现次数 |
|------|----------|
| `__free()` | 349 |
| `__cleanup` | 57 |
| `guard()` | 59 |
| `scoped_guard` | 19 |

这是一个相对新的特性，使用量正在快速增长。
