## devres
Linux 内核中的 **`devres` (Managed Device Resource)** 是一个旨在简化设备驱动开发、减少内存泄漏和资源管理错误的框架。

简单来说，**`devres` 将资源的生命周期绑定到设备（device）的生命周期上**。当设备驱动加载失败（Probe fail）或者设备被卸载（Remove）时，内核会自动释放该设备申请的所有“托管资源”。

以下是关于 `devres` 的详细技术介绍，包括其背景、原理、常用 API 以及底层机制。

---

### 1. 为什么要引入 `devres`？

在 `devres` 出现之前，编写 Linux 驱动的 `probe` 和 `remove` 函数非常痛苦且容易出错。

#### 传统方式 (The Old Way)
你需要手动处理每一个资源的申请和释放。特别是在 `probe` 函数中，如果第 5 个资源申请失败了，你必须小心翼翼地通过 `goto` 标签跳到错误处理代码，手动释放前 4 个已申请的资源。

```c
// 传统写法：容易出错的 goto 阶梯
static int my_driver_probe(struct platform_device *pdev)
{
    // 1. 申请内存
    ptr = kmalloc(...);
    if (!ptr) return -ENOMEM;

    // 2. 映射 IO
    io = ioremap(...);
    if (!io) {
        kfree(ptr); // 必须手动释放
        return -ENOMEM;
    }

    // 3. 申请中断
    ret = request_irq(...);
    if (ret) {
        iounmap(io); // 必须手动释放
        kfree(ptr);  // 必须手动释放
        return ret;
    }

    return 0;
}
```

#### Devres 方式 (The New Way)
使用前缀为 `devm_` 的函数，内核会记录这些资源。如果出错或退出，内核自动按**先进后出 (LIFO)** 的顺序释放资源。

```c
// Devres 写法：极其简洁
static int my_driver_probe(struct platform_device *pdev)
{
    struct device *dev = &pdev->dev;

    // 1. 托管内存
    ptr = devm_kmalloc(dev, ...);
    if (!ptr) return -ENOMEM;

    // 2. 托管 IO
    io = devm_ioremap(dev, ...);
    if (!io) return -ENOMEM; // 不需要手动释放 ptr

    // 3. 托管中断
    ret = devm_request_irq(dev, ...);
    if (ret) return ret; // 不需要手动释放 io 和 ptr

    return 0;
}
```

---

### 2. `devres` 的核心原理

`devres` 的核心思想是：**Resource tracking（资源追踪）**。

1.  **数据结构**：每个 `struct device` 内部都有一个链表头（`dev->devres_head`），用于存放该设备所有托管的资源节点。
2.  **分配过程**：
    *   当你调用 `devm_kmalloc` 时，内核不仅分配你请求的内存，还会额外分配一个小的 `devres` 结构体头。
    *   这个结构体记录了“如何释放该资源”的回调函数（例如 `kfree`）。
    *   内核将这个结构体挂载到 `struct device` 的链表上。
3.  **释放过程**：
    *   **Driver Detach**：当驱动卸载（`driver_detach`）或者 `probe` 返回错误时，内核核心层会遍历该设备的 `devres` 链表。
    *   **LIFO 顺序**：由于通过链表头部插入，释放时是从最近申请的资源开始释放。这非常重要，因为硬件依赖关系通常是后申请的依赖先申请的（例如先 `clk_enable` 才能 `write_regs`）。
    *   **回调执行**：内核依次调用每个资源注册的 `release` 函数，完成清理。

---

### 3. 常用的 `devm_` API

几乎所有常用的内核资源申请函数都有对应的 `devm_` 版本。

*   **内存管理**：
    *   `devm_kmalloc()` / `devm_kzalloc()`
    *   `devm_kstrdup()`
    *   `devm_kmalloc_array()`
*   **IO 映射 (IOMEM)**：
    *   `devm_ioremap()`
    *   `devm_ioremap_resource()` (最常用，集成了 `request_mem_region` 和 `ioremap` 并带有错误打印)
    *   `devm_platform_ioremap_resource()` (针对 platform driver 的进一步封装)
*   **中断 (IRQ)**：
    *   `devm_request_irq()`
    *   `devm_request_threaded_irq()`
*   **时钟与电源**：
    *   `devm_clk_get()`
    *   `devm_regulator_get()`
*   **GPIO 与 Pinctrl**：
    *   `devm_gpiod_get()`
    *   `devm_pinctrl_get()`
*   **其他**：
    *   `devm_phy_get()`
    *   `devm_pwm_get()`

---

### 4. 进阶：自定义托管动作 (`devm_add_action`)

有时候你需要执行一些没有现成 `devm_` API 的操作，比如：
*   初始化时设置某个寄存器位，退出时必须复位。
*   调用某个没有 `devm` 版本的旧子系统注册函数。

这时可以使用 **`devm_add_action_or_reset()`**。

**示例：**

```c
// 自定义释放函数
static void my_device_disable(void *data)
{
    struct my_device *my_dev = data;
    hardware_reset_bit(my_dev); // 硬件复位操作
}

static int my_probe(struct platform_device *pdev)
{
    struct my_device *my_dev = ...;

    // 硬件使能
    hardware_set_bit(my_dev);

    // 注册一个自定义的清理动作
    // 如果 probe 失败或驱动卸载，my_device_disable 会被自动调用
    ret = devm_add_action_or_reset(&pdev->dev, my_device_disable, my_dev);
    if (ret)
        return ret;

    return 0;
}
```

*注意：推荐使用 `devm_add_action_or_reset` 而不是 `devm_add_action`。前者在添加动作失败时，会自动立刻调用一次清理函数，防止泄漏。*

---

### 5. `devres` 的内部数据结构（源码视角）

在内核源码 `drivers/base/devres.c` 中可以看到实现细节。

最基础的结构体是 `struct devres`：

```c
struct devres {
    struct devres_node      node;   // 链表节点
    void                    *data;  // 指向实际数据的指针（如 kmalloc 的地址）
    void (*release)(struct device *dev, void *res); // 释放回调函数
};
```

当你调用 `devm_kmalloc` 时，内核实际上调用了 `devres_alloc` 分配这个结构，然后把用户的内存附在后面，最后通过 `devres_add` 加到设备链表中。

---

### 6. 使用注意事项与陷阱

虽然 `devres` 很好用，但也有几个坑需要注意：

1.  **不要混合使用**：
    *   如果你用了 `devm_kmalloc` 申请内存，**千万不要** 再手动调用 `kfree`。这会导致 double free（双重释放）或者内核崩溃。
    *   如果确实需要提前释放某个 devm 资源，应使用 `devm_kfree` 或 `devm_release_resource`。

2.  **释放顺序 (Ordering)**：
    *   资源是 **后进先出 (LIFO)** 释放的。
    *   **陷阱**：在 `probe` 中，通常我们先申请内存/锁，最后申请中断 (`devm_request_irq`)。这意味着在 `remove` 时，中断会最先被释放（disable）。这是对的。
    *   但是，如果你在 `probe` 里通过 `devm_add_action` 注册了一些复杂逻辑，一定要理清楚它们和中断释放的顺序关系，防止中断处理函数在资源释放后还在运行。

3.  **Scope（作用域）**：
    *   `devres` 绑定的是 `struct device`。如果你的驱动创建了子设备，并且把资源挂在父设备上，但子设备卸载时父设备还在，那资源就不会释放。必须搞清楚资源挂在哪个 `device` 指针上。

4.  **调试**：
    *   内核提供了 `CONFIG_DEBUG_DEVRES` 选项。开启后，可以在 dmesg 中看到详细的资源分配和释放日志，有助于排查泄漏问题。

### 总结

Linux 内核的 **devres** 模块是驱动开发的现代标准。它通过将资源管理托管给设备模型核心，极大地简化了代码逻辑，消除了“goto 错误处理”的混乱，是编写高质量 Linux 驱动必不可少的工具。

## APIs

下面是 Linux 内核中 `devres` 模块主要 API 的列表，按功能类别进行了整理。

### 核心资源管理 API 速查表

| 类别               | API 函数名                          | 功能描述                                              | 对应非托管函数                          |
| :--------------- | :------------------------------- | :------------------------------------------------ | :------------------------------- |
| **内存分配**         | `devm_kmalloc`                   | 分配内核内存。设备卸载时自动 kfree。                             | `kmalloc`                        |
|                  | `devm_kzalloc`                   | 分配并清零内存（最常用）。                                     | `kzalloc`                        |
|                  | `devm_kmalloc_array`             | 分配数组内存（带溢出检查）。                                    | `kmalloc_array`                  |
|                  | `devm_kstrdup`                   | 复制字符串到新分配的托管内存中。                                  | `kstrdup`                        |
|                  | `devm_kfree`                     | **手动**释放托管内存（通常不需要调用，除非想提前释放）。                    | `kfree`                          |
| **IO 映射**        | `devm_ioremap`                   | 将物理地址映射到虚拟地址。                                     | `ioremap`                        |
|                  | `devm_ioremap_resource`          | **推荐**。申请内存区域(`request_mem_region`)并映射。自带错误打印。    | `ioremap` + `request_mem_region` |
|                  | `devm_platform_ioremap_resource` | **最推荐(针对Platform)**。直接从 pdev 取 resource 并映射，一步到位。 | 同上，封装更深                          |
|                  | `devm_iounmap`                   | 手动取消映射（通常不需要调用）。                                  | `iounmap`                        |
| **中断 (IRQ)**     | `devm_request_irq`               | 注册中断处理函数。卸载时自动 free_irq。                          | `request_irq`                    |
|                  | `devm_request_threaded_irq`      | 注册线程化中断（下半部在线程中执行）。                               | `request_threaded_irq`           |
|                  | `devm_free_irq`                  | 手动释放中断（通常不需要调用）。                                  | `free_irq`                       |
| **时钟 (Clock)**   | `devm_clk_get`                   | 获取时钟句柄。卸载时自动 clk_put。                             | `clk_get`                        |
|                  | `devm_clk_get_optional`          | 获取可选时钟，如果时钟不存在返回 NULL 而不是错误。                      | `clk_get`                        |
| **GPIO/Pinctrl** | `devm_gpiod_get`                 | 获取 GPIO 描述符。卸载时自动释放。                              | `gpiod_get`                      |
|                  | `devm_gpiod_get_optional`        | 获取可选 GPIO。                                        | `gpiod_get_optional`             |
|                  | `devm_pinctrl_get`               | 获取 Pinctrl 句柄。                                    | `pinctrl_get`                    |
| **电源/复位**        | `devm_regulator_get`             | 获取电源调节器（Regulator）。                               | `regulator_get`                  |
|                  | `devm_reset_control_get`         | 获取复位控制器句柄。                                        | `reset_control_get`              |
| **DMA**          | `dmam_alloc_coherent`            | 分配 DMA 一致性内存（注意前缀是 `dmam_`）。                      | `dma_alloc_coherent`             |
| **自定义动作**        | `devm_add_action_or_reset`       | **神器**。添加自定义回调。若添加失败会自动运行回调以防泄漏。                  | 无                                |
|                  | `devm_remove_action`             | 移除已添加的自定义动作（不执行）。                                 | 无                                |

---

### 重点 API 详细说明

#### 1. `devm_add_action_or_reset` (自定义救星)
这是处理没有 `devm_` 前缀的旧 API 或自定义硬件操作的通用方法。

*   **场景**：你需要调用 `my_func_init()`，并且必须在退出时调用 `my_func_exit()`，但内核没有 `devm_my_func_init`。
*   **用法**：
    ```c
    // 1. 初始化
    my_func_init(data);
    
    // 2. 注册清理函数
    ret = devm_add_action_or_reset(dev, my_func_exit_wrapper, data);
    if (ret)
        return ret; // 如果注册失败，wrapper 会立即被调用，确保 cleanup
    ```

#### 2. `devm_platform_ioremap_resource` (驱动开发最常用)
这是写 Platform Driver 时最简化的 helper 函数。它一行代码完成了以下三件事：
1.  `platform_get_resource(pdev, IORESOURCE_MEM, 0)` 获取资源。
2.  `devm_request_mem_region(...)` 申请资源占用。
3.  `devm_ioremap(...)` 映射地址。

*   **用法**：
    ```c
    void __iomem *base;
    // 以前需要写 10 行代码，现在只需要 1 行
    base = devm_platform_ioremap_resource(pdev, 0);
    if (IS_ERR(base))
        return PTR_ERR(base);
    ```

### 总结
记忆规律很简单：**在原有内核 API 前面加上 `devm_`，并将第一个参数改为 `struct device *dev`**。如果不确定某个资源是否有 managed 版本，可以在内核源码的 include 目录下 grep 搜索 `devm_`。