
## 概述
[Linux 设备模型](../summary/Linux%20%E8%AE%BE%E5%A4%87%E6%A8%A1%E5%9E%8B.md) 概述介绍

## APIs
Linux 内核设备模型（Device Model）是一个非常庞大的子系统，这里是主要的API 列表，将不仅列出常用函数，还会加入一些底层控制、遍历查找以及资源管理的高级 API。我们将按照**核心对象层级**进行拆解。

主要头文件引用参考：
*   `&lt;linux/device.h&gt;`
*   `&lt;linux/platform_device.h&gt;`
*   `&lt;linux/kobject.h&gt;`
*   `&lt;linux/sysfs.h&gt;`

---

### 1. 核心设备对象管理 (Core Device Object)
这是所有设备（无论挂在 PCI、USB 还是 Platform 总线下）的基类操作。

| API 函数 | 参数简述 | 功能详情 | 备注/上下文 |
| :--- | :--- | :--- | :--- |
| **`device_register`** | `struct device *` | **一键注册**。初始化设备结构并将其添加到系统。 | 相当于 `init` + `add`。底层驱动少用，总线代码常用。 |
| **`device_unregister`** | `struct device *` | **一键注销**。移除设备并释放引用。 | 对应 `register`。 |
| **`device_initialize`** | `struct device *` | **仅初始化**。初始化 kobject、锁、链表，不加入系统。 | 用于需要分步设置属性的复杂场景。 |
| **`device_add`** | `struct device *` | **添加到系统**。建立父子关系，创建 sysfs 节点，触发 uevent。 | 在 `initialize` 之后调用。 |
| **`device_del`** | `struct device *` | **从系统移除**。删除 sysfs 节点，解绑驱动。 | 对应 `add`。 |
| **`put_device`** | `struct device *` | **减少引用计数**。计数为 0 时调用 `release` 回调。 | **内存管理核心**。未配对会导致泄漏。 |
| **`get_device`** | `struct device *` | **增加引用计数**。 | 持有设备指针超过函数范围时必须调用。 |
| **`device_rename`** | `dev`, `new_name` | **重命名设备**。修改 sysfs 中的目录名。 | 尽量避免在注册后重命名，易引发竞争。 |
| **`device_move`** | `dev`, `new_parent` | **移动设备**。改变设备的父节点（在 sysfs 中移动目录）。 | 用于拓扑结构调整。 |
| **`device_is_registered`**| `dev` | **检查状态**。判断设备是否已注册。 | 返回 bool。 |
| **`dev_name`** | `const struct device *` | **获取设备名**。 | 返回字符串指针（`dev->init_name` 或 kobj name）。 |
| **`dev_set_name`** | `dev`, `fmt`, ... | **设置设备名**。支持格式化字符串。 | 必须在 `device_add` 之前调用。 |

---

### 2. 驱动程序核心 (Driver Core)
管理 `struct device_driver`，通常由总线级代码使用，但驱动开发者在编写特定总线逻辑时需要了解。

| API 函数 | 参数简述 | 功能详情 | 备注/上下文 |
| :--- | :--- | :--- | :--- |
| **`driver_register`** | `struct device_driver *` | **注册驱动**。加入总线链表，开始匹配设备。 | 底层 API。 |
| **`driver_unregister`** | `struct device_driver *` | **注销驱动**。 | |
| **`driver_find`** | `name`, `bus` | **查找驱动**。根据名字和总线查找已注册的驱动。 | 返回驱动指针（需 `put_driver` 释放）。 |
| **`driver_for_each_device`**| `drv`, `start`, `data`, `fn` | **遍历设备**。遍历所有绑定到该驱动的设备。 | `fn` 是回调函数。非常有用。 |
| **`driver_create_file`** | `drv`, `attr` | **创建驱动属性**。在 `/sys/bus/x/drivers/y/` 下创建文件。 | 用于全局驱动配置。 |
| **`driver_remove_file`** | `drv`, `attr` | **删除驱动属性**。 | |

---

### 3. 总线子系统 (Bus Subsystem)
用于遍历总线上的设备或驱动，或者编写新的总线驱动。

| API 函数 | 参数简述 | 功能详情 | 备注/上下文 |
| :--- | :--- | :--- | :--- |
| **`bus_register`** | `struct bus_type *` | **注册总线**。在 `/sys/bus/` 下创建新目录。 | 编写新总线架构时使用。 |
| **`bus_unregister`** | `struct bus_type *` | **注销总线**。 | |
| **`bus_for_each_dev`** | `bus`, `start`, `data`, `fn` | **遍历设备**。遍历挂在该总线上的所有设备。 | 不管有没有绑定驱动，都会遍历。 |
| **`bus_for_each_drv`** | `bus`, `start`, `data`, `fn` | **遍历驱动**。遍历注册在该总线上的所有驱动。 | |
| **`bus_find_device`** | `bus`, `start`, `data`, `match_fn` | **查找设备**。在总线上查找符合条件的设备。 | 需自定义 `match_fn`。 |
| **`bus_find_device_by_name`**| `bus`, `start`, `name` | **按名查找**。 | |
| **`subsys_system_register`**| `bus`, `groups` | **注册系统子系统**。 | 高级用法，用于注册 core subsystem。 |

---

### 4. 类与接口 (Class & Interfaces)
主要用于向用户空间提供统一的功能视图（如 `/sys/class/net`）。

| API 函数 | 参数简述 | 功能详情 | 备注/上下文 |
| :--- | :--- | :--- | :--- |
| **`class_register`** | `struct class *` | **注册类**。底层 API。 | |
| **`class_unregister`** | `struct class *` | **注销类**。 | |
| **`class_create`** | `owner`, `name` | **创建类**。便捷宏，动态分配并注册。 | **最常用**。模块初始化时调用。 |
| **`class_destroy`** | `struct class *` | **销毁类**。配合 `class_create` 使用。 | |
| **`device_create`** | `class`, `parent`, `dev_t`, `data`, `fmt` | **创建类设备**。自动并在 `/dev` 下生成节点。 | **最常用**。用于字符设备驱动。 |
| **`device_destroy`** | `class`, `dev_t` | **销毁类设备**。删除节点。 | |
| **`device_create_with_groups`**| ... `groups` | **创建带属性的设备**。 | **推荐**。避免创建设备后手动加属性的竞态。 |
| **`class_for_each_device`** | `class`, `start`, `data`, `fn` | **遍历类设备**。遍历属于该类的所有设备。 | |
| **`class_find_device`** | `class`, `start`, `data`, `match` | **查找类设备**。 | |
| **`class_interface_register`**| `struct class_interface *` | **注册类接口**。当设备加入类时自动调用回调。 | 高级用法，用于扩展类功能。 |

---

### 5. 平台总线 (Platform Bus) - 嵌入式开发核心
用于 SoC 内部外设或无法自动枚举的设备。

| API 函数 | 参数简述 | 功能详情 | 备注/上下文 |
| :--- | :--- | :--- | :--- |
| **`platform_driver_register`** | `struct platform_driver *` | **注册平台驱动**。 | 驱动入口标准动作。 |
| **`platform_driver_unregister`**| `struct platform_driver *` | **注销平台驱动**。 | |
| **`platform_device_register`** | `struct platform_device *` | **注册平台设备**。 | 通常在板级文件或测试代码中。 |
| **`platform_device_alloc`** | `name`, `id` | **动态分配设备**。 | 需配合 `platform_device_add`。 |
| **`platform_device_add`** | `pdev` | **添加动态设备**。 | |
| **`platform_get_resource`** | `pdev`, `type`, `num` | **获取资源**。如 MEM, IO。 | `probe` 中最常用。 |
| **`platform_get_resource_byname`**| `pdev`, `type`, `name` | **按名获取资源**。 | 用于设备树定义了 `reg-names` 的情况。 |
| **`platform_get_irq`** | `pdev`, `num` | **获取中断号**。 | 内部处理了设备树解析。 |
| **`platform_get_irq_optional`** | `pdev`, `num` | **获取可选中断**。 | 找不到时不打印错误日志。 |
| **`platform_set_drvdata`** | `pdev`, `data` | **保存私有数据**。 | 存入 `dev->driver_data`。 |
| **`platform_get_drvdata`** | `pdev` | **获取私有数据**。 | |

---

### 6. 托管资源管理 (Devres - Managed Resources)
**这是现代驱动开发的推荐方式**。内核会在驱动 detach 或 probe 失败时自动释放资源，避免 goto 错误处理地狱。

| API 函数 | 对应的非托管 API | 功能详情 |
| :--- | :--- | :--- |
| **内存管理** | | |
| `devm_kmalloc` | `kmalloc` | 分配内存，设备卸载自动 kfree。 |
| `devm_kzalloc` | `kzalloc` | 分配并清零。 |
| `devm_kfree` | `kfree` | 手动释放托管内存（一般不需要调）。 |
| `devm_kmalloc_array` | `kmalloc_array` | 数组分配，防溢出。 |
| `devm_kstrdup` | `kstrdup` | 字符串复制。 |
| **IO 映射** | | |
| `devm_ioremap` | `ioremap` | 映射物理地址。 |
| `devm_ioremap_resource` | `request_mem_region` + `ioremap` | **推荐**。申请资源区域并映射，含错误检查。 |
| `devm_iounmap` | `iounmap` | 解除映射。 |
| **中断与时钟** | | |
| `devm_request_irq` | `request_irq` | 申请中断。 |
| `devm_free_irq` | `free_irq` | 释放中断。 |
| `devm_clk_get` | `clk_get` | 获取时钟。 |
| `devm_clk_get_optional` | `clk_get` | 获取可选时钟。 |
| **GPIO 与其他** | | |
| `devm_gpiod_get` | `gpiod_get` | 获取 GPIO 描述符。 |
| `devm_regulator_get` | `regulator_get` | 获取电源稳压器。 |
| `devm_pinctrl_get` | `pinctrl_get` | 获取引脚控制句柄。 |
| **万能自定义** | | |
| **`devm_add_action`** | N/A | **添加自定义清理回调**。极强大的 API，用于不支持 devm 的老接口。 |
| **`devm_add_action_or_reset`**| N/A | 同上，但如果添加失败会直接调用清理函数。 |

---

### 7. Sysfs 属性与文件 (Sysfs Attributes)
用于向用户空间暴露控制接口。

| API / 宏 | 参数 | 功能详情 |
| :--- | :--- | :--- |
| **`DEVICE_ATTR_RW`** | `name` | **定义宏**。声明 `dev_attr_name`，需实现 `name_show` 和 `name_store`。 |
| **`DEVICE_ATTR_RO`** | `name` | **定义宏**。只读，需实现 `name_show`。 |
| **`DEVICE_ATTR_WO`** | `name` | **定义宏**。只写，需实现 `name_store`。 |
| **`DRIVER_ATTR_RW`** | `name` | **定义宏**。用于驱动属性。 |
| **`BUS_ATTR_RW`** | `name` | **定义宏**。用于总线属性。 |
| **`device_create_file`** | `dev`, `attr` | **创建文件**。容易产生竞态，**不推荐**在 probe 中直接用。 |
| **`device_remove_file`** | `dev`, `attr` | **删除文件**。 |
| **`sysfs_create_group`** | `kobj`, `group` | **创建属性组**。一次性创建目录下的一组文件。 |
| **`sysfs_remove_group`** | `kobj`, `group` | **删除属性组**。 |
| **`sysfs_create_link`** | `kobj`, `target`, `name` | **创建软链接**。在 sysfs 中创建指向其他目录的链接。 |
| **`sysfs_remove_link`** | `kobj`, `name` | **删除软链接**。 |
| **`sysfs_notify`** | `kobj`, `dir`, `attr` | **通知更新**。通知用户空间文件内容已变（用于 `poll` 机制）。 |

---

### 8. 日志打印辅助 (Logging Helpers)
推荐使用这些代替 `printk`，因为它们会自动加上设备的名称（如 `eth0: link up`），方便调试。

| API 函数 | 功能详情 |
| :--- | :--- |
| **`dev_info(dev, fmt, ...)`** | 打印 INFO 级别日志。 |
| **`dev_err(dev, fmt, ...)`** | 打印 ERROR 级别日志。 |
| **`dev_warn(dev, fmt, ...)`** | 打印 WARNING 级别日志。 |
| **`dev_dbg(dev, fmt, ...)`** | 打印 DEBUG 级别日志（需开启 `DYNAMIC_DEBUG` 或编译时定义 `DEBUG`）。 |
| **`dev_ratelimited`** | 上述函数的变体，限制打印频率，防止刷屏。 |
| **`dev_warn_once`** | 警告只打印一次。 |

---

### 9. 辅助宏 (Helpers)

| 宏 | 功能详情 |
| :--- | :--- |
| **`container_of(ptr, type, member)`** | **内核第一宏**。通过成员指针反推结构体指针。在 `device` 回调中找回私有数据结构。 |
| **`module_platform_driver(drv)`** | 自动生成 `module_init/exit` 并注册平台驱动。 |
| **`module_i2c_driver(drv)`** | 自动生成 `module_init/exit` 并注册 I2C 驱动。 |
| **`module_pci_driver(drv)`** | 自动生成 `module_init/exit` 并注册 PCI 驱动。 |

这份列表涵盖了 Linux 设备模型开发中 95% 场景会用到的 API。对于更底层的 kobject 操作（如 `kobject_init`, `kobject_add`），通常被 `device_*` 系列函数封装了，除非编写核心子系统，否则很少直接调用。
