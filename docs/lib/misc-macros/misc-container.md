# 杂项：container_of — 从成员指针反推容器结构

> B 级文档。内核中最基础也最常用的宏之一，用于从嵌入成员的指针反推包含该成员的结构体指针。

## container_of — 从成员指针反推容器结构

### 定位

**功能**：已知结构体中某嵌入成员的指针，反推出包含该成员的完整结构体指针。这是内核"组合优于继承"设计模式的核心支撑——子系统通过嵌入通用结构体（如 `struct device`、`struct kobject`）实现多态，回调函数收到的往往是通用结构体指针，需要 `container_of` 找回外层私有结构。

**实现方式**：将成员指针减去该成员在结构体中的偏移量（`offsetof(type, member)`），得到结构体起始地址。通过 `static_assert` + `__same_type` 在编译期检查指针类型与成员类型是否匹配，防止类型错误。`container_of_safe` 额外检查 `IS_ERR_OR_NULL`，指针为错误时直接返回 `ERR_CAST` 而不做偏移计算。

**应用场景**：全树约 10 万+ 处使用，是内核中使用最广泛的宏之一。几乎所有使用嵌入结构体的子系统都依赖它：设备模型（`device->driver_data` 反推驱动私有结构）、链表（`list_head` 反推节点结构）、工作队列（`work_struct` 反推工作项）、定时器（`timer_list` 反推调用者结构）、kobject 子系统等。

### 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `include/linux/container_of.h` | 40 | `container_of` 和 `container_of_safe` |

### 核心宏

**`container_of(ptr, type, member)`**（`container_of.h:17-22`）：

```c
#define container_of(ptr, type, member) ({
    void *__mptr = (void *)(ptr);
    static_assert(__same_type(*(ptr), ((type *)0)->member) ||
                  __same_type(*(ptr), void),
                  "pointer type mismatch in container_of()");
    ((type *)(__mptr - offsetof(type, member)));
})
```

**原理**：已知结构体中某成员的地址，减去该成员在结构体中的偏移量，得到结构体起始地址。

**`container_of_safe(ptr, type, member)`**（`container_of.h:32-38`）：

如果 `ptr` 是 `IS_ERR_OR_NULL`，直接返回 `ERR_CAST(ptr)` 而不做偏移计算。用于回调中指针可能为错误的场景。

### 使用示例

```c
struct my_device {
    int id;
    struct device dev;  // 嵌入的通用设备结构
    char name[32];
};

// 从 device 指针反推 my_device 指针
static int my_probe(struct device *dev)
{
    struct my_device *mdev = container_of(dev, struct my_device, dev);
    // 现在可以使用 mdev->id, mdev->name 等
}
```

### 使用统计

`container_of` 在全树出现约 **10 万+** 次，是内核中使用最广泛的宏之一。几乎所有使用嵌入结构体的子系统都依赖它（设备模型、链表、工作队列、定时器、kobject 等）。
