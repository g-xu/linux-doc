## OF
Linux内核中的 **OF (Open Firmware)** 模块，在现代嵌入式Linux体系（特别是ARM、RISC-V、PowerPC架构）中，核心体现为 **设备树（Device Tree, DT）** 机制。它是内核中负责**解析硬件描述信息**、**构建设备拓扑结构**以及**辅助驱动与硬件匹配**的子系统。

以下是从原理、数据结构、启动流程到API使用的详细技术介绍。

---

### 1. 核心设计理念：机制与策略分离

在引入OF/设备树之前，Linux内核（尤其是ARM架构）面临着**代码耦合度极高**的问题。板级信息（Board info）被硬编码在 `.c` 文件中。

OF模块引入后，实现了：
*   **机制 (Mechanism)：** 内核代码（驱动程序）。只负责操作某一类硬件的通用逻辑（如：如何操作PL011串口）。
*   **策略 (Policy/Data)：** 设备树文件 (`.dts`)。负责描述具体板子上有哪些硬件、地址是多少、中断号是多少。

**OF模块就是连接这两者的桥梁。**

---

### 2. 内部架构与数据结构

OF模块在内核内存中维护了一棵树状结构，这棵树是 `.dtb`（二进制设备树）在内存中的映射。

#### 2.1 关键结构体 (`include/linux/of.h`)

**1. `struct device_node` (核心节点)**
这是OF模块中最基本的单位，对应设备树中的每一个节点（Node）。
```c
struct device_node {
    const char *name;          // 节点名称 (例如 "uart")
    phandle phandle;           // 句柄，用于节点间引用 (如 &gpio0)
    const char *full_name;     // 完整路径 (例如 "/soc/serial@10000000")
    struct fwnode_handle fwnode;

    struct property *properties; // 属性链表头
    struct property *deadprops;  // 已删除的属性
    struct device_node *parent;  // 父节点指针
    struct device_node *child;   // 第一个子节点
    struct device_node *sibling; // 下一个兄弟节点
    
    struct kobject kobj;       // 用于在sysfs中展示 (/sys/firmware/devicetree/base)
    unsigned long _flags;
    void *data;                // 指向私有数据的指针
};
```
*注意：内核通过 `child` 和 `sibling` 指针实现了多叉树的遍历。*

**2. `struct property` (属性)**
对应节点中的键值对（Key-Value），如 `reg = &lt;0x0 0x100&gt;;`。
```c
struct property {
    char *name;       // 属性名 (如 "reg", "compatible")
    int length;       // 数据长度 (字节)
    void *value;      // 数据内容的指针 (大端序存储)
    struct property *next; // 下一个属性
    // ...
};
```

---

### 3. OF模块的初始化流程 (Boot Process)

OF模块的工作贯穿了内核启动的早期到后期，主要分为两个阶段：

#### 阶段一：扁平设备树 (FDT - Flattened Device Tree)
*   **时间点：** 内核汇编启动阶段到 `setup_arch`。
*   **状态：** 内存管理系统尚未初始化。
*   **操作：** Bootloader将 `.dtb` 加载到内存，并将物理地址传给内核（寄存器 `r2` 或 `x0`）。内核使用 `libfdt` 库直接解析原始二进制数据。
*   **目的：** 获取最基础的硬件信息，如**内存大小 (`/memory`)**、**命令行参数 (`/chosen/bootargs`)**，以便建立页表和初始化内存分配器。

#### 阶段二：展开设备树 (Unflattening)
*   **时间点：** `setup_arch` -> `unflatten_device_tree()`。
*   **操作：** 内存分配器（Bootmem/Memblock）就绪后，内核申请内存，解析 FDT 二进制流，动态创建 `struct device_node` 和 `struct property` 结构体，并将它们链接成树状结构。
*   **结果：** 生成了 **Live Tree**（全局变量 `of_root` 指向根节点）。

#### 阶段三：设备注册 (Population)
*   **时间点：** `do_initcalls` 阶段 -> `of_platform_default_populate()`。
*   **操作：** 遍历 `device_node` 树，将符合条件的节点转换为 Linux 设备模型中的设备（主要是 `struct platform_device`，也包含 `i2c_client`, `spi_device` 等）。
*   **注意：** 并非所有节点都会生成设备（例如 `cpus` 节点、中断控制器节点通常不作为 platform_device 注册）。

---

### 4. 核心功能模块与API

OF模块对外提供了丰富的API，主要可以分为以下几类：

#### 4.1 节点查找 (Node Lookup)
驱动初始化时，首先需要找到对应的节点。
*   `of_find_node_by_path("/soc/i2c@1234")`: 绝对路径查找。
*   `of_find_compatible_node(NULL, NULL, "vendor,device")`: **最常用**，根据兼容性字符串查找。
*   `of_get_parent(node)`: 获取父节点。
*   `of_get_child_by_name(node, "name")`: 获取指定子节点。

#### 4.2 属性读取 (Property Access)
读取节点中的键值对。注意：**DTB中存储的数值是大端序 (Big Endian)**，这些API会自动处理字节序转换，返回CPU对应的字节序。
*   `of_property_read_u32(node, "prop-name", &val)`: 读单个32位整数。
*   `of_property_read_u32_array(...)`: 读数组。
*   `of_property_read_string(node, "prop-name", &str)`: 读字符串。
*   `of_property_read_bool(node, "prop-name")`: 读布尔值（判断属性是否存在）。

#### 4.3 地址资源映射 (Address Mapping)
将设备树中的 `reg` 属性（物理地址 + 长度）转换为内核可用的虚拟地址。
*   `of_address_to_resource(node, index, &res)`: 将 `reg` 属性解析为 `struct resource`。
*   `of_iomap(node, index)`: 解析 `reg` 并直接调用 `ioremap`，返回 `void __iomem *` 虚拟地址。**这是驱动最常用的简便函数。**

#### 4.4 中断处理 (Interrupt Handling)
解析 `interrupts` 和 `interrupt-parent` 属性。
*   `of_irq_get(node, index)`: 解析节点的中断，并映射为内核的虚拟IRQ号（Virtual IRQ Number）。驱动随后可调用 `request_irq()`。

#### 4.5 GPIO获取
*   `of_get_named_gpio(node, "led-gpios", 0)`: 获取GPIO编号。

---

### 5. 匹配机制 (Matching Mechanism)

OF模块如何将 `device_node` 与 `driver` 结合？

1.  **总线匹配：** 当系统启动时，`platform_bus_type` 的 `match` 函数 (`platform_match`) 会被调用。
2.  **OF匹配优先级：** 该函数首先检查设备和驱动是否有 `of_match_table`。
3.  **字符串比较：** 将设备树节点的 `compatible` 属性列表，与驱动程序中定义的 `of_device_id` 表进行逐一比对。
4.  **Probe：** 一旦匹配成功，内核调用驱动的 `probe` 函数，并将 `struct platform_device` 传入。

**代码示例：**

```c
/* 驱动中的匹配表 */
static const struct of_device_id my_driver_ids[] = {
    { .compatible = "myvendor,mydevice-v1", .data = &ver1_data },
    { .compatible = "myvendor,mydevice-v2", .data = &ver2_data },
    { /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, my_driver_ids);

/* 在Probe中获取匹配数据 */
static int my_probe(struct platform_device *pdev)
{
    const struct of_device_id *match;
    match = of_match_device(my_driver_ids, &pdev->dev);
    if (match) {
        // 获取 .data 中存储的特定版本配置
        struct config *cfg = match->data;
    }
    // ...
}
```

---

### 6. 高级特性：Overlay (动态覆盖)

标准的设备树是静态的，但现代嵌入式系统（如树莓派接入HAT扩展板）需要动态修改硬件描述。

**Device Tree Overlay** 允许在内核运行时加载 `.dtbo` 文件：
1.  解析 `.dtbo`。
2.  将其中的片段（Fragments）挂载到现有的 Live Tree 上（例如，修改状态为 `okay`，或者添加新的子节点）。
3.  触发内核通知链，创建新的设备并触发驱动加载。

内核配置宏：`CONFIG_OF_OVERLAY`。

---

### 7. 总结

Linux内核的OF模块是一个**配置管理子系统**。

1.  **输入：** `.dtb` 文件（由硬件工程师或BSP工程师根据电路图编写）。
2.  **处理：** 解析二进制 -> 构建节点树 -> 映射资源（地址、中断）。
3.  **输出：** 标准化的 `platform_device` 设备对象，以及供驱动查询的API。

掌握OF模块，对于理解Linux驱动架构、移植内核以及解决"驱动加载失败"（通常是compatible不匹配或reg地址错误）至关重要。

## APIs

以下是Linux内核OF（Open Firmware/设备树）模块主要API的详细汇总。

这些API主要定义在 **`include/linux/of.h`**, **`include/linux/of_address.h`**, **`include/linux/of_irq.h`** 等头文件中。

### 1. 节点查找与获取 (Node Lookup & Traversal)
用于在设备树结构中寻找特定的节点。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_find_node_by_path`** | 根据绝对路径（如 `/soc/i2c@1234`）查找节点。 | 成功返回 `device_node` 指针，失败返回 NULL。<br>**注意：** 增加引用计数，用完需调用 `of_node_put`。 |
| **`of_find_node_by_name`** | 根据节点名称（如 `led`）查找节点。通常不推荐，因为名字不唯一。 | 同上。 |
| **`of_find_compatible_node`** | 根据 `compatible` 属性查找节点。是驱动开发中最常用的查找方式之一。 | 同上。例如：`of_find_compatible_node(NULL, NULL, "fsl,imx6-uart");` |
| **`of_find_node_by_phandle`** | 根据 `phandle`（句柄数值）查找节点。通常用于解析引用关系。 | 同上。 |
| **`of_get_parent`** | 获取指定节点的父节点。 | 返回父节点指针。需要 `of_node_put` 释放。 |
| **`of_get_child_by_name`** | 获取指定节点下名为 `name` 的子节点。 | 返回子节点指针。 |
| **`of_node_put`** | 减少节点的引用计数。 | 无返回值。**防止内存泄漏的关键函数**。 |

### 2. 属性读取 (Property Access)
用于读取节点中定义的属性值（键值对）。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_property_read_u32`** | 读取一个32位整数属性。 | 成功返回0，失败返回负的错误码（如 `-EINVAL`）。 |
| **`of_property_read_u64`** | 读取一个64位整数属性。 | 同上。 |
| **`of_property_read_string`** | 读取字符串属性。 | 成功返回0，参数指针指向字符串首地址。 |
| **`of_property_read_string_index`** | 读取字符串数组中的第 `index` 个字符串。 | 成功返回0。用于如 `compatible` 这种包含多个字符串的属性。 |
| **`of_property_read_bool`** | 读取布尔属性（检查属性是否存在）。 | 存在返回 `true`，不存在返回 `false`。<br>通常用于标志位，如 `enable-active-high;`。 |
| **`of_property_read_u32_array`** | 读取32位整数数组。 | 需传入预分配的数组指针和大小。 |
| **`of_property_count_elems_of_size`** | 计算属性中包含多少个元素。 | 返回元素个数。常用于动态分配数组内存。 |
| **`of_get_property`** | 获取属性的原始指针和长度（底层函数）。 | 返回 `void*` 原始数据指针。不涉及字节序转换（返回大端序）。 |

### 3. 地址与资源映射 (Address & Resource Mapping)
用于处理 `reg` 属性，将物理地址转换为内核可用的虚拟地址或资源结构。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_iomap`** | 直接从节点解析 `reg` 属性并进行 `ioremap` 映射。 | 成功返回虚拟地址 `void __iomem *`，失败返回 NULL。<br>**驱动开发极高频API**。 |
| **`of_address_to_resource`** | 将 `reg` 属性解析为标准的 `struct resource` 结构体。 | 成功返回0。用于获取物理地址范围而不立即映射。 |
| **`of_translate_address`** | 将设备树中的总线地址转换为CPU视角的物理地址（处理 `ranges` 属性）。 | 返回 CPU 物理地址 (`u64`)。 |

### 4. 中断处理 (Interrupt Handling)
用于解析 `interrupts` 和 `interrupt-parent` 属性。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_irq_get`** | 获取节点的第 `index` 个中断，并映射为Linux虚拟中断号 (Virq)。 | 成功返回正整数中断号，0或负数表示错误。<br>常用于 `request_irq` 的参数。 |
| **`of_irq_get_byname`** | 根据 `interrupt-names` 属性获取指定名字的中断号。 | 同上。 |
| **`irq_of_parse_and_map`** | 解析并映射中断（功能类似 `of_irq_get`，是较老的接口但依然通用）。 | 返回 Linux Virq。 |

### 5. 遍历辅助宏 (Iteration Helpers)
用于在代码中方便地遍历子节点或特定节点，避免手动处理链表。

| API 宏名称 | 功能描述 | 备注 |
| :--- | :--- | :--- |
| **`for_each_child_of_node`** | 遍历指定节点的所有子节点。 | `(parent, child)`。循环会自动处理引用计数，但在**break跳出循环时需手动 put child**。 |
| **`for_each_available_child_of_node`** | 遍历所有状态为 "okay" 的子节点。 | 忽略 `status = "disabled"` 的节点。 |
| **`for_each_compatible_node`** | 遍历整个设备树中所有匹配 `compatible` 的节点。 | 全局搜索，开销较大。 |
| **`for_each_node_by_name`** | 遍历所有匹配名称的节点。 | - |

### 6. 驱动匹配与检查 (Matching & Checking)
用于在 `probe` 函数中确认设备详情或提取匹配数据。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_device_is_compatible`** | 检查节点是否包含指定的兼容性字符串。 | 匹配返回非0，否则返回0。 |
| **`of_match_device`** | 将 `device` 与驱动的 `of_device_id` 表进行匹配。 | 成功返回匹配的 `of_device_id` 指针（含 `.data` 私有数据），失败返回 NULL。<br>用于区分同一驱动支持的不同硬件版本。 |
| **`of_match_node`** | 底层函数，将节点与 ID 表匹配。 | - |
| **`of_device_get_match_data`** | 简化版 API，直接获取匹配项中的 `.data` 字段。 | 返回私有数据指针。 |

### 7. GPIO 相关 (GPIO Handling)
虽然现在推荐使用 GPIO 子系统 (`gpiod_get`), 但 OF 层仍有特定 API。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_get_named_gpio`** | 从属性（如 `led-gpios`）中解析 GPIO 编号。 | 成功返回 GPIO 号，失败返回负数。<br>获取后需调用 `gpio_request`。 |
| **`of_gpio_count`** | 统计节点中定义的 GPIO 数量。 | - |

### 8. 平台设备填充 (Populate)
通常在板级初始化代码中使用，驱动工程师较少直接调用。

| API 函数名 | 功能描述 | 返回值/备注 |
| :--- | :--- | :--- |
| **`of_platform_populate`** | 遍历指定节点，将子节点转换为 `platform_device` 并注册到系统。 | 核心启动代码，用于生成 `/sys/bus/platform/devices` 下的设备。 |

---

### ⚠ 开发注意事项

1.  **引用计数 (Refcount):** 凡是以 `of_find_` 开头的函数，或者 `of_get_parent`，都会增加节点的引用计数。如果在使用完节点后（且不需要长期持有），**必须**调用 `of_node_put(node)`，否则会导致内核内存泄漏。
2.  **字节序:** 设备树二进制文件 (`.dtb`) 采用**大端序 (Big Endian)** 存储。直接使用指针访问 `value` 需要手动转换（`be32_to_cpup`），但推荐使用 `of_property_read_` 系列函数，它们会自动处理字节序转换。
3.  **错误处理:** 读取属性时务必检查返回值，因为设备树文件可能编写错误或缺少属性。