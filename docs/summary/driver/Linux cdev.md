
## APIs

以下详细汇总的 Linux 内核 **cdev (Character Device)** 模块的核心 API。这些接口主要声明在 `&lt;linux/cdev.h&gt;` 和 `&lt;linux/fs.h&gt;` 头文件中。

`cdev` 是 Linux 内核中用于抽象字符设备的核心结构体，它将**设备号**与**文件操作集 (fops)** 连接起来。

为了方便理解，我将 API 分为 **核心对象操作** 和 **设备号管理** 两大类。

### 1. cdev 核心对象操作 API

这部分 API 直接操作 `struct cdev` 结构体，负责驱动的初始化、注册和注销。

| API 函数接口              | 功能分类      | 详细功能描述                                                   | 核心注意事项                                                              |
| :-------------------- | :-------- | :------------------------------------------------------- | :------------------------------------------------------------------ |
| **`cdev_init`**       | **初始化**   | 初始化一个**已分配内存**的 `cdev` 结构体，并建立与 `file_operations` 的绑定关系。 | 用于结构体嵌入在自定义设备结构体中的情况（最常用）。<br>`cdev_init(&my_dev->cdev, &my_fops);` |
| **`cdev_alloc`**      | 分配        | 动态从内核堆内存中申请一个 `struct cdev` 结构体。                         | 此时 `cdev.ops` 为空，申请后必须手动赋值 `.ops`。<br>如果用 `cdev_init`，通常不需要用这个。     |
| **`cdev_add`**        | **注册/添加** | 将初始化好的 `cdev` 结构体添加到内核的字符设备映射表中（`cdev_map`）。             | **关键点**：调用成功后，设备就“活”了，用户空间立刻可以通过设备号 `open` 该设备。                     |
| **`cdev_del`**        | **注销/删除** | 从内核中移除该字符设备，使其不再响应用户请求。                                  | 通常在模块卸载函数中调用。<br>**注意**：它不会释放 `cdev` 结构体本身的内存（如果是动态申请的，需额外 kfree）。  |
| **`cdev_device_add`** | 组合操作      | (较新内核) 同时执行 `cdev_add` 和 `device_add`。                   | 这是一个辅助函数，旨在简化同时注册 cdev 和 device (sysfs节点) 的过程。                      |
| **`cdev_device_del`** | 组合操作      | (较新内核) 同时执行 `device_del` 和 `cdev_del`。                   | 对应 `cdev_device_add` 的反向操作。                                         |
|                       |           |                                                          |                                                                     |

---

### 2. 设备号管理 API (前置条件)

在使用 `cdev` 之前，必须先申请设备号（Major/Minor）。虽然这属于文件系统层 (`fs`)，但它是 cdev 流程中不可分割的一部分。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`alloc_chrdev_region`** | **动态申请** | 让内核自动分配一个可用的主设备号，并申请一系列次设备号。 | **推荐方式**。避免主设备号冲突。<br>`alloc_chrdev_region(&dev_id, 0, 1, "name");` |
| **`register_chrdev_region`** | 静态申请 | 开发者指定一个明确的主设备号向内核申请。 | 如果该号已被占用，函数会失败。通常用于已知固定设备号的特殊驱动。 |
| **`unregister_chrdev_region`** | **释放** | 归还申请的设备号范围。 | 必须在模块卸载时调用，否则再次加载驱动会报错。 |
| **`MAJOR(dev_t)`** | 宏 | 从 `dev_t` 类型（32位整数）中提取**主**设备号。 | 用于打印调试信息或逻辑判断。 |
| **`MINOR(dev_t)`** | 宏 | 从 `dev_t` 类型中提取**次**设备号。 | 用于在 `open` 函数中判断打开的是哪个具体子设备。 |
| **`MKDEV(ma, mi)`** | 宏 | 将主设备号和次设备号合并为一个 `dev_t` 类型。 | 用于 `cdev_add` 等函数的参数构建。 |

---

### 3. 老式/简化 API (不推荐用于现代复杂驱动)

Linux 2.4 时代的遗留接口，但在简单驱动或教学中仍偶尔见到。

| API 函数接口 | 功能分类 | 详细功能描述 | 核心注意事项 |
| :--- | :--- | :--- | :--- |
| **`register_chrdev`** | 一键注册 | 申请主设备号 + 注册 cdev 的“二合一”函数。 | **缺点**：它会占用该主设备号下的**所有 256 个**次设备号，浪费资源。现代驱动通常不建议使用。 |
| **`unregister_chrdev`** | 一键注销 | 释放主设备号 + 注销 cdev。 | 对应 `register_chrdev`。 |

---

### 📝 核心结构体补充：`struct cdev`

```c
struct cdev {
    struct kobject kobj;              // 内嵌的 kobject，用于引用计数和内核管理
    struct module *owner;             // 所属模块，通常是 THIS_MODULE
    const struct file_operations *ops;// 文件操作集合 (open, read, write...)
    struct list_head list;            // 内核链表节点
    dev_t dev;                        // 起始设备号
    unsigned int count;               // 关联的设备数量
};
```

### ⚙️ 标准驱动开发流程 (Best Practice)

编写一个现代字符设备驱动的标准步骤如下：

1.  **定义结构体**：定义一个包含 `struct cdev` 的自定义设备结构体。
2.  **申请设备号**：调用 **`alloc_chrdev_region`** 获取 `dev_t`。
3.  **初始化 cdev**：调用 **`cdev_init`**，填入 `file_operations`。
4.  **添加 cdev**：调用 **`cdev_add`**，传入设备号。
    *   *(可选但推荐)*：调用 `class_create` 和 `device_create` 创建 `/dev` 节点（这属于设备模型，不属于 cdev 模块本身，但紧密相关）。
5.  **卸载清理**：
    *   *(对应可选)*：`device_destroy`, `class_destroy`。
    *   **删除 cdev**：调用 **`cdev_del`**。
    *   **释放设备号**：调用 **`unregister_chrdev_region`**。

## 用户态到内核的路径：

`open打开一个设备的时候，系统是如何从 "/dev/xx"路径找到需要操作的设备open接口的`

这是一个非常经典的 Linux 内核 VFS（虚拟文件系统）与驱动子系统交互的流程问题。

从用户空间调用 `open("/dev/xx")` 到最终执行驱动程序里的 `.open` 函数，中间经历了一次精妙的“接力跑”。核心的连接点在于 **inode** 和 **设备号（dev_t）**。

整个过程可以分为四个主要阶段：

### 1. 第一阶段：系统调用与路径解析 (User -> VFS)

当你在用户层代码执行 `fd = open("/dev/xx", O_RDWR)` 时：

1.  **系统调用陷入**：程序通过系统调用（System Call）进入内核态，调用内核的 `sys_open`（现代内核通常是 `do_sys_open`）。
2.  **路径查找 (Path Lookup)**：
    *   内核调用 `path_openat` 等函数，开始解析字符串路径 `"/dev/xx"`。
    *   内核通过 VFS 遍历目录树，最终在 `/dev` 文件系统（通常是 `devtmpfs`）中找到了 `xx` 这个文件对应的 **dentry（目录项）** 和 **inode（索引节点）**。

### 2. 第二阶段：识别设备文件 (Inode 初始化)

这是 VFS 识别出“这是一个字符设备”的关键步骤。

1.  **读取 Inode**：内核获取到 `xx` 的 `struct inode`。
2.  **检查类型**：内核检查 `inode->i_mode`。
    *   如果是一个**普通文件**，内核会使用普通文件的操作集。
    *   但对于 `/dev/xx`，`i_mode` 标记为 **S_IFCHR**（字符设备）。
3.  **加载默认操作集**：
    *   在 inode 被初始化时（或者从磁盘/内存读取时），内核会发现它是字符设备，于是调用 `init_special_inode`。
    *   该函数会将 inode 的文件操作集 (`i_fop`) 指向一个内核默认的“跳板”操作集：**`def_chr_fops`**。
    *   同时，inode 中存储了该设备的 **设备号 (`i_rdev`)**，即主设备号和次设备号。

### 3. 第三阶段：中转跳板 (The Relay)

现在，内核创建了一个代表“打开的文件”的结构体 `struct file`。

1.  **关联 file 与 inode**：`struct file` 的 `f_op` 指针被赋值为 inode 的 `i_fop`（也就是刚才说的 `def_chr_fops`）。
2.  **调用默认 open**：内核尝试打开文件，执行 `file->f_op->open(...)`。
3.  **进入 `chrdev_open`**：
    *   `def_chr_fops` 中的 `.open` 函数指向的是内核通用的 **`chrdev_open`** 函数。
    *   **这就是关键的中转站**。这个函数负责寻找真正的驱动。

### 4. 第四阶段：查找 cdev 并偷梁换柱 (Lookup & Replace)

在 `chrdev_open` 函数内部，发生了“移花接木”的操作：

1.  **查找 cdev (`kobj_lookup`)**：
    *   `chrdev_open` 根据 `inode->i_rdev`（即主/次设备号），在内核全局的 **`cdev_map`** 哈希表中查找对应的 `struct cdev` 对象。
    *   *注意：这就是为什么我们在写驱动时要调用 `cdev_add`，它就是把驱动的 cdev 放入这个 map 中供查找。*
2.  **获取真正的 fops**：
    *   找到 `cdev` 结构体后，通过 `cdev->ops` 获取驱动程序开发者定义的 `file_operations`（其中包含了你写的 `my_driver_open`）。
3.  **替换操作集 (The Hook)**：
    *   `chrdev_open` 将当前 `struct file` 的 `f_op` 指针，**修改**为你驱动里的 `file_operations`。
    *   *意义：* 下次用户再在这个 fd 上调用 `read/write` 时，就会直接调用驱动的函数，而不再经过 `chrdev_open` 这个中转站了。
4.  **调用驱动的 open**：
    *   最后，`chrdev_open` 执行 `filp->f_op->open`，也就是执行了**你驱动里的 `my_driver_open`**。

---

### 总结流程图

为了更直观，我们可以把这个流程画成一个调用链：

```text
用户空间:  open("/dev/xx")
              |
              v (系统调用)
---------------------------------------------------------
内核空间:
    1. do_sys_open()
        |
        v
    2. path_openat() -> 解析路径 "/dev/xx" -> 找到 struct inode
        |               (inode->i_rdev = 设备号)
        |               (inode->i_fop  = &def_chr_fops) <--- 默认字符设备操作集
        |
        v
    3. do_dentry_open() -> 创建 struct file
        |                  (file->f_op = inode->i_fop)
        |
        v
    4. file->f_op->open()  --> 实际上调用的是 chrdev_open()
        |
        +---> [ chrdev_open ] ---------------------------------------+
                |                                                    |
                | A. 根据 inode->i_rdev (设备号)                     |
                |    在 cdev_map 中查找 struct cdev                  |
                |                                                    |
                | B. 找到 cdev 后，提取 cdev->ops (你的驱动fops)     |
                |                                                    |
                | C. 替换: file->f_op = cdev->ops                    |
                |                                                    |
                | D. 调用: file->f_op->open() ---------------------->|
                                                                     |
                                                                     v
                                                            [ 你的驱动 my_driver_open ]
```

### 核心回答
系统之所以能找到你的 open 接口，靠的是 **inode 中存储的设备号** 作为索引，通过 **`chrdev_open`** 函数在内核的 **`cdev_map`** 中查找到你注册的 **`cdev` 结构体**，进而找到你挂载在 cdev 上的 **`file_operations`**。

## 设备poll实现

Linux 内核驱动中 `poll` 函数的实现是连接用户空间 `select()` / `poll()` / `epoll()` 机制与内核设备状态的关键桥梁。

实现 `poll` 函数的核心逻辑其实非常简单，可以概括为 **“两步走”**：
1.  **挂号**：把当前进程加入到驱动的等待队列中（但**不阻塞**进程）。
2.  **检查**：立即查看当前设备是否可读或可写，并返回状态掩码。

下面我将从核心原理、代码模版、以及它如何配合工作三个方面详细介绍。

---

### 1. 核心原理

在驱动的 `file_operations` 结构体中，`poll` 函数的原型如下：

```c
__poll_t (*poll) (struct file *filp, struct poll_table_struct *wait);
```

**驱动工程师必须牢记的两个概念：**
*   **`poll_wait()` 不会睡觉**：这是一个极其常见的误区。`poll_wait()` 只是把当前进程添加到了驱动维护的等待队列中。驱动里的 `poll` 函数必须**立即返回**，不能在函数内部睡眠。
*   **谁让进程睡觉？**：是调用驱动 `poll` 的内核上层（VFS 层）。如果驱动返回的掩码是 `0`（表示没准备好），内核上层才会让进程进入睡眠状态。

---

### 2. 标准实现模版 (Code Example)

假设我们写一个字符设备驱动，支持读操作（Reader）。我们需要一个等待队列头 `wait_queue_head_t`。

#### A. 设备结构体定义
首先，在设备结构体里定义等待队列和状态标志：

```c
#include <linux/poll.h>
#include <linux/wait.h>

struct my_device_data {
    struct cdev cdev;
    
    // 1. 定义等待队列头
    wait_queue_head_t read_queue; 
    
    // 2. 定义数据是否准备好的标志
    bool data_ready; 
    
    // ... 数据缓冲区等 ...
};
```

#### B. 驱动初始化
在 `probe` 或 `init` 函数中初始化等待队列：

```c
init_waitqueue_head(&my_dev->read_queue);
my_dev->data_ready = false;
```

#### C. 实现 `.poll` 函数 (核心)

```c
static __poll_t my_driver_poll(struct file *filp, struct poll_table_struct *wait)
{
    struct my_device_data *dev = filp->private_data;
    __poll_t mask = 0;

    // 【第一步：挂号】
    // 调用 poll_wait 将当前文件句柄对应的进程加入到 read_queue 等待队列中。
    // 注意：这不会阻塞，只是为了将来设备有数据时，能找到人去唤醒。
    poll_wait(filp, &dev->read_queue, wait);

    // 【第二步：检查状态】
    // 检查是否可读
    if (dev->data_ready) {
        // EPOLLIN: 有数据可读
        // EPOLLRDNORM: 有普通数据可读
        mask |= EPOLLIN | EPOLLRDNORM;
    }

    // 如果设备支持写，且缓冲区不满，也可以检查：
    // if (buffer_not_full) {
    //     mask |= EPOLLOUT | EPOLLWRNORM;
    // }

    // 【第三步：返回掩码】
    // 如果返回 0，VFS 层会让进程睡眠（如果用户设置了超时）。
    // 如果返回非 0，VFS 层会告诉用户空间“设备好了”。
    return mask;
}
```

#### D. 唤醒机制 (在 ISR 或 Write 中)
`poll` 函数只是负责“查”和“登记”，真正改变状态并唤醒进程的通常是中断处理函数（硬件数据到了）或者 `write` 函数。

```c
// 假设这是中断处理函数，或者其他产生数据的地方
void data_arrived_handler(struct my_device_data *dev)
{
    // 1. 设置标志位
    dev->data_ready = true;

    // 2. 唤醒在队列上等待的进程
    // 这会让之前调用 poll/select 睡眠的进程醒来
    wake_up_interruptible(&dev->read_queue);
}
```

---

### 3. 系统工作流程图解

为了理解 `poll` 是如何工作的，看看这个流程：

1.  **用户空间**：调用 `select()` 或 `poll()`。
2.  **内核空间**：`sys_poll` 遍历用户传入的所有 fd。
3.  **驱动空间**：针对每个 fd，调用驱动的 `my_driver_poll`。
    *   `poll_wait()` 把进程挂到驱动的 `read_queue` 上。
    *   检查 `data_ready`。如果是 false，返回 mask = 0。
4.  **内核空间**：如果所有 fd 的驱动都返回 0：
    *   当前进程状态设为 `TASK_INTERRUPTIBLE`（睡眠）。
    *   调度器切换其他进程运行。
5.  **硬件中断**：数据到达，ISR 运行。
6.  **驱动空间**：ISR 调用 `wake_up_interruptible(&dev->read_queue)`。
7.  **内核空间**：进程被唤醒，重新变为 `TASK_RUNNING`。
8.  **内核空间**：**再次** 遍历所有 fd，调用驱动的 `my_driver_poll`。
    *   这次 `poll_wait` 一般不再做操作（或者只是检查）。
    *   检查 `data_ready`。现在是 true 了，返回 `EPOLLIN`。
9.  **用户空间**：`select/poll` 返回，用户程序知道有数据了，调用 `read()`。

### 4. 常见掩码含义

在 `poll` 实现中，你需要根据设备状态组合以下掩码返回：

*   **`EPOLLIN`**：设备可读（无阻塞）。
*   **`EPOLLRDNORM`**：有普通数据可读（通常等同于 EPOLLIN）。
*   **`EPOLLOUT`**：设备可写（无阻塞）。
*   **`EPOLLWRNORM`**：有普通数据可写。
*   **`EPOLLERR`**：设备发生错误。
*   **`EPOLLHUP`**：设备挂起（如串口线断了，Pipe 另一端关闭了）。

### 总结
实现 Linux 驱动的 poll 只需要三行核心代码：
1.  `poll_wait(file, &queue, wait);` // 加入队列
2.  `if (condition) mask |= EPOLLIN;` // 判断条件
3.  `return mask;` // 返回结果

剩下的工作（睡眠、调度、超时管理）都由 Linux 内核 VFS 子系统自动完成。