
## Kobject & Kset

**kobject** 和 **kset** 是 Linux 内核设备模型（Linux Device Model）的**基石**。如果不理解它们，就无法真正理解 Linux 驱动是如何在 `/sys` 下生成目录、如何管理电源、以及如何实现热插拔的。

可以用一句话来概括：**Linux 内核是用 C 语言写的，但 kobject 赋予了它“面向对象（OOP）”的能力。**

---

### 1. Kobject (Kernel Object) —— 内核对象的“原子”

`struct kobject` 是 Linux 设备模型中最基本的对象。你可以把它看作是 C++ 或 Java 中的 `Object` 基类，或者是建筑里的**一块砖**。

#### 它的核心职责：
1.  **引用计数 (Reference Counting)**：这是 kobject 最重要的功能。通过 `kref` 成员，内核记录有多少个地方正在使用这个对象。只有当引用计数降为 0 时，内存才会被释放。这解决了内核中著名的“竞态资源释放”问题。
2.  **Sysfs 表现**：每一个 kobject 都会在 `/sys` 文件系统中对应**一个目录**。
3.  **父子层级**：kobject 通过 `parent` 指针指向另一个 kobject，从而形成了 `/sys` 下面的目录树结构。
4.  **热插拔事件 (Uevent)**：当 kobject 被添加、移动或删除时，它可以向用户空间（如 `udev`）发送消息（UEVENT）。

#### 结构体简览：
```c
struct kobject {
    const char          *name;      // 对象名称，对应 sysfs 目录名
    struct list_head    entry;      // 用于链入 kset 的链表
    struct kobject      *parent;    // 指向父对象，决定 sysfs 层级
    struct kset         *kset;      // 所属的集合
    struct kobj_type    *ktype;     // 定义对象的行为（读写属性、析构函数）
    struct kref         kref;       // 引用计数
    // ... 其他标志位
};
```

#### 谁在使用它？
几乎所有的高级对象都**内嵌**了 kobject。
*   `struct device` 包含一个 `kobject`。
*   `struct cdev` 包含一个 `kobject`。
*   `struct bus_type` 包含一个 `subsys_private`，里面藏着 `kobject`。
这就是为什么注册一个设备，`/sys` 下就会自动弹出一个目录的原因。

---

### 2. Kset (Kernel Set) —— 对象的“容器”

如果说 kobject 是“砖”，那么 kset 就是“**一筐砖**”或者“**分类标签**”。

`struct kset` 本身也是一个对象（它内部包含了一个 kobject），但它的主要功能是**容器**。它把一组相关的 kobject 集合在一起。

#### 它的核心职责：
1.  **容器与遍历**：kset 维护了一个链表，把所有属于它的 kobject 串起来。内核可以通过 kset 遍历下面所有的对象。
2.  **统一管理**：kset 是 kobject 的顶层类或子系统。例如，所有的“输入设备”可能属于同一个 kset。
3.  **事件过滤与分发**：当 kobject 状态改变发送 uevent 时，**必须**通过它所属的 kset 来处理。如果一个 kobject 没有所属的 kset，它是发不出热插拔事件的。

#### 结构体简览：
```c
struct kset {
    struct list_head list;      // 链表头，挂着该集合下所有的 kobject
    spinlock_t list_lock;       // 保护链表的锁
    struct kobject kobj;        // kset 自己也是一个 kobject (继承)
    const struct kset_uevent_ops *uevent_ops; // 热插拔事件的操作函数集
};
```

---

### 3. Kobject 与 Kset 的复杂关系（必考点）

初学者最容易晕的地方在于：**`parent` 和 `kset` 到底有什么区别？**

*   **`parent` (父指针)**：决定了 **物理层级（目录结构）**。
    *   如果 `kobj->parent = A`，那么在 sysfs 里，目录结构就是 `.../A/kobj/`。
*   **`kset` (集合指针)**：决定了 **逻辑分类** 和 **事件处理**。
    *   如果 `kobj->kset = B`，说明这个对象属于 B 这个类别。

**通常情况下的默认行为：**
如果在初始化 kobject 时，`parent` 为 NULL，但设置了 `kset`，那么内核会默认把 `kset` 内部的 `kobj` 当作这个对象的 `parent`。
此时：**物理层级 == 逻辑分类**。

**特殊情况（高级用法）：**
你可以把一个对象归类到 `Input` 集合（kset），但物理上让它挂在 `PCI` 总线设备（parent）下面。
*   sysfs 路径：`/sys/devices/pci0000:00/.../input0` (由 parent 决定)
*   逻辑归属：它依然属于 Input 子系统，Input 子系统可以遍历到它。

---

### 4. 总结对比

| 特性 | Kobject | Kset |
| :--- | :--- | :--- |
| **本质** | 基本对象 (基类) | 对象的容器 (容器类) |
| **Sysfs 表现** | 目录 | 目录 (因为它内嵌了 kobject) |
| **包含关系** | 被包含在 kset 中 | 包含一组 kobject |
| **主要功能** | 引用计数、层级链接 | 遍历管理、热插拔事件处理 |
| **比喻** | 一个学生 | 班级 (班级本身也是学校的一个对象) |

### 5. 实际代码演示

如何在 `/sys/kernel/` 下创建一个名为 `my_test_kset` 的目录，并在里面创建一个 `my_obj` 目录？

```c
#include <linux/module.h>
#include <linux/kobject.h>
#include <linux/slab.h>

struct kset *example_kset;
struct kobject *example_kobj;

static int __init my_module_init(void)
{
    /* 1. 创建 kset
     * 这会在 /sys/kernel/ 下创建一个名为 "my_test_kset" 的目录
     * kernel_kobj 是 /sys/kernel 的代表
     */
    example_kset = kset_create_and_add("my_test_kset", NULL, kernel_kobj);
    if (!example_kset)
        return -ENOMEM;

    /* 2. 创建 kobject */
    example_kobj = kzalloc(sizeof(struct kobject), GFP_KERNEL);
    
    /* 3. 初始化并设置 kobject 所属的 kset */
    example_kobj->kset = example_kset;
    
    /* 4. 添加 kobject
     * 第一个参数是 &example_kobj
     * 第二个参数是 ktype (这里简单设为 NULL)
     * 第三个参数是 parent (NULL 表示默认跟随 kset)
     * 第四个参数是名字格式
     */
    kobject_init_and_add(example_kobj, NULL, NULL, "my_obj");
    
    // 结果：生成目录 /sys/kernel/my_test_kset/my_obj/
    return 0;
}

static void __exit my_module_exit(void)
{
    kobject_put(example_kobj); // 引用计数减1，触发释放
    kset_unregister(example_kset); // 销毁 kset
}

module_init(my_module_init);
module_exit(my_module_exit);
MODULE_LICENSE("GPL");
```

### 总结
*   **kobject** 是为了解决引用计数和 sysfs 目录映射的底层机制。
*   **kset** 是为了管理一群 kobject 并处理它们的热插拔事件。
*   我们在写驱动时（比如 `device_register`），内核已经在后台默默帮我们处理好了这些复杂的 kobject/kset 关系。

## APIs

以下为 Linux 内核 **Kobject** 和 **Kset** 模块的核心 API。这些接口主要声明在 `&lt;linux/kobject.h&gt;` 头文件中。

为了方便阅读，我将 API 分为 **Kobject 基础操作**、**Kset 操作**、**引用计数管理** 和 **事件与属性** 四大类。

### 1. Kobject 核心操作 API

这部分接口主要用于对象的生命周期管理（创建、初始化、添加到 Sysfs）。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`kobject_create_and_add`** | **创建并注册** | **(最常用)** 动态分配一个 kobject 结构体，初始化它，并将其添加到 sysfs 中。 | 一步到位。返回指针，失败返回 NULL。常用于创建简单的 sysfs 目录。 |
| **`kobject_init_and_add`** | **初始化并注册** | 初始化一个**已经分配好内存**（如嵌入在其他结构体中）的 kobject，并将其添加到 sysfs。 | 需要自己传入 `ktype`。如果失败，必须手动调用 `kobject_put` 来清理。 |
| **`kobject_init`** | 初始化 | 仅初始化 kobject 的内部数据结构（链表、引用计数设为1），**不**会在 sysfs 中建立目录。 | 必须紧接着调用 `kobject_add` 才能在 sysfs 中显示。 |
| **`kobject_add`** | 注册/添加 | 将已初始化的 kobject 链接到内核层级结构中，并在 sysfs 中创建对应的目录。 | 必须先调用过 `kobject_init`。 |
| **`kobject_del`** | 移除 | 从 sysfs 中移除 kobject 对应的目录，断开层级联系，但**不释放内存**。 | 通常不需要手动调用，`kobject_put` 降为0时会自动触发清理逻辑。 |
| **`kobject_set_name`** | 命名 | 设置 kobject 的名称（即 sysfs 中的目录名）。支持类似 `printf` 的格式化字符串。 | `kobject_set_name(&kobj, "dev%d", id);` |
| **`kobject_rename`** | 重命名 | 修改已注册 kobject 的名称，并同步修改 sysfs 中的目录名。 | 这是一个耗时操作，尽量在注册前确定好名字。 |

---

### 2. Kset 核心操作 API

Kset 是 Kobject 的容器，用于集合管理。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`kset_create_and_add`** | **创建并注册** | **(最常用)** 动态创建一个 kset，并将其注册到 sysfs 中。 | 它是顶层容器的快捷创建方式，内部封装了 `kset_register`。 |
| **`kset_register`** | 注册 | 初始化并添加一个 kset 到内核中。 | 如果 kset 结构体是静态定义的或嵌入的，用这个。 |
| **`kset_unregister`** | 注销 | 从 sysfs 中移除 kset。 | 实际上也是调用 `kobject_put(&kset->kobj)`。 |
| **`kset_find_obj`** | **查找** | 在指定的 kset 中，根据名字查找并返回对应的 kobject。 | **重要**：该函数返回时会增加 kobject 的引用计数，用完后必须调用 `kobject_put`。 |

---

### 3. 引用计数管理 API (核心机制)

这是内核防止“使用已释放内存（Use-after-free）”的关键机制。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`kobject_get`** | 增加引用 | 将 kobject 的引用计数 +1。返回 kobject 指针本身。 | 如果传入 NULL，直接返回 NULL。 |
| **`kobject_put`** | **减少引用** | **(最重要)** 将引用计数 -1。如果计数降为 0，则调用 `ktype->release` 释放内存。 | **千万不要用 `kfree` 直接释放包含 kobject 的结构体**，必须用 `kobject_put`。 |

---

### 4. 事件 (Uevent) 与 辅助 API

用于处理热插拔事件及其他辅助功能。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`kobject_uevent`** | **发送事件** | 向用户空间（udev/mdev）发送热插拔事件（KOBJ_ADD, KOBJ_REMOVE 等）。 | 实际上是调用所属 Kset 的 `uevent_ops`。 |
| **`kobject_uevent_env`** | 发送事件(带参) | 发送事件的同时，附带额外的环境变量（Environment Variables）。 | 用于传递额外信息给用户空间脚本。 |
| **`add_uevent_var`** | 辅助工具 | 辅助函数，用于在 `uevent` 回调中向事件包添加环境变量。 | 格式如 `add_uevent_var(env, "MY_VAR=%s", val);` |

---

### 💡 核心结构体补充：`struct kobj_type`

虽然它不是 API 函数，但它是使用上述 API 时必须了解的数据结构。在使用 `kobject_init` 时必须传入。

```c
struct kobj_type {
    void (*release)(struct kobject *kobj);        // 析构函数：引用计数为0时调用
    const struct sysfs_ops *sysfs_ops;            // 属性操作：定义读写文件的行为
    struct attribute **default_attrs;             // 默认属性：自动创建的文件列表
};
```

### 📝 最佳实践总结

1.  **内存释放原则**：永远不要手动 `kfree` 一个已经注册过的 kobject。必须依赖 `kobject_put` 触发 `ktype->release` 回调来释放。
2.  **创建流程**：
    *   简单需求（创建一个目录）：用 `kobject_create_and_add`。
    *   复杂需求（嵌入在自己的设备结构体中）：用 `kobject_init_and_add`，并自定义 `kobj_type`。
3.  **父子关系**：如果希望在 `/sys/kernel/mm/` 下建目录，需要将 `kobj.parent` 设置为 `mm_kobj`（或者相应的上级 kobject），然后再 add。