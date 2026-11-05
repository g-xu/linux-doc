---
title: API 参考
---

# base 代码地图与对外 API

这一篇回答两类问题：**「这件事的代码在哪个文件」**和**「这个 API 怎么调、有什么坑」**。两者放在同一篇，是因为查它们通常是同一次动作。

范围：`drivers/base/` 的驱动模型核心。arm64-only（裁掉了什么见 `99-evidence.md` §2.7）。

表格统一三列 `API / 说明 / 备注`。签名只写函数名加形参名，类型信息搬进备注。**位置只精确到文件**——行号是 `03-internals.md` 和 `99-evidence.md` 的事，在这一篇里是噪声。

## §0 代码地图

### §0.1 一等文件：核心，全部读过

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `core.c` | 5190 | `struct device` 的全部生命周期：初始化、注册、sysfs 建立、uevent、重命名、移动、销毁；device_link 全部实现；`dev_printk` 的后端 |
| `platform.c` | 1529 | platform 总线与 platform 设备/驱动；资源与中断查询；`platform_bus_type` 的六个回调 |
| `dd.c` | 1352 | 绑定：设备找驱动、驱动找设备、`really_probe()`、延迟探测（deferred probe）、解绑 |
| `devres.c` | 1239 | 设备托管资源（`devm_*` 的底座）：链表、分组、`devm_kmalloc` 一族 |
| `bus.c` | 1184 | `struct bus_type` 的注册与 sysfs 呈现；`bus_for_each_*` 迭代；subsys 三种注册形态 |
| `class.c` | 592 | `struct class` 的注册与迭代；class_interface；class_compat 兼容层 |
| `driver.c` | 303 | `struct device_driver` 的注册、sysfs 属性、按名查找 |
| `base.h` | 207 | 目录私有头：`bus_type_private` / `driver_private` / `device_private` 三个私有结构，以及核心内部函数声明 |
| `map.c` | 154 | `kobj_map`：主设备号到 kobject 的映射表，字符/块设备号查找的底座 |
| `module.c` | 93 | `/sys/module/<mod>/drivers/` 那几个符号链接 |
| `init.c` | 42 | `driver_init()`：整个驱动模型的启动入口，决定各子层的初始化次序 |

合计 11885 行。这 11 个文件之外的东西，要么是二等，要么已裁掉。

### §0.2 二等文件：读了符号表和关键区段

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `property.c` | 1421 | 统一属性访问层：`device_property_*` / `fwnode_property_*`，把 DT 与 software node 收在同一组 API 后面 |
| `swnode.c` | 1177 | software node：不来自固件的属性源，主要给没有 DT 的场合和测试用 |
| `component.c` | 824 | 组件聚合：多个设备都 probe 完之后才一起 bind（DRM 是主要用户） |
| `attribute_container.c` | 548 | 按类给设备挂一组 sysfs 属性；只被 transport_class 使用 |
| `devcoredump.c` | 459 | 设备崩溃时把一段内存倒到 `/sys/class/devcoredump/` |
| `auxiliary.c` | 419 | 辅助总线：让一个物理设备拆成多个功能子设备 |
| `platform-msi.c` | 351 | 非 PCI 设备的 MSI 中断域 |
| `transport_class.c` | 299 | 传输层类，SCSI 用得最多 |
| `soc.c` | 265 | `/sys/bus/soc/` 下的 SoC 标识信息 |
| `isa.c` | 182 | 一条给老 ISA 设备用的伪总线 |
| `syscore.c` | 128 | 不属于任何设备的系统级挂起/恢复回调 |
| `pinctrl.c` | 105 | 在 probe 之前替设备申请默认引脚配置 |
| `trace.h` | 56 | 两个 tracepoint 的定义（细节见 `07-debug.md`） |
| `firmware.c` | 26 | 只做一件事：建 `/sys/firmware/` 这个目录 |

### §0.3 显式裁掉的部分

裁掉不等于不重要，只是它们的归属在别的子系统。

| 裁掉的 | 理由 |
| --- | --- |
| `power/`（13433 行） | 电源管理是独立子系统，`dev_pm_ops` 那一整套自成体系 |
| `regmap/`（10998 行） | 寄存器访问抽象，已有独立文档集 |
| `firmware_loader/`（3317 行） | 固件加载，是本批次后面单独的一套 |
| `memory.c` `node.c` `arch_numa.c` | 内存热插拔与 NUMA，归 `mm/` |
| `arch_topology.c` `cacheinfo.c` `topology.c` | CPU 拓扑与缓存信息，只在讲 `cpu_subsys` 时提一句 |
| `cpu.c` | 只讲它定义 `cpu_subsys` 这一件事，CPU 上下线归 cpu hotplug |
| `devtmpfs.c` | 一个内嵌的 tmpfs 实例，属文件系统侧 |
| `container.c` `hypervisor.c` | 两个几十行的目录桩 |
| `physical_location.c` `physical_location.h` | `drivers/base/Makefile:28` 是 `obj-$(CONFIG_ACPI)`，按 arm64-only 裁掉，登记在 `99-evidence.md` §2.7 |
| `test/`（809 行） | KUnit 测试 |

### §0.4 头文件

| 头文件 | 行数 | 里面有什么 |
| --- | --- | --- |
| `include/linux/device.h` | 1122 | `struct device`、`struct device_link`、`DL_FLAG_*`、`devm_kzalloc` 一族的内联包装、`device_lock()` 一族、属性宏 |
| `include/linux/device/bus.h` | 292 | `struct bus_type`、`bus_*` API、`subsys_interface` |
| `include/linux/device/driver.h` | 296 | `struct device_driver`、`driver_*` API、`module_driver()` 宏 |
| `include/linux/device/class.h` | 280 | `struct class`、`class_*` API、`class_interface` |
| `include/linux/platform_device.h` | 381 | `struct platform_device`、`struct platform_driver`、`module_platform_driver()` |
| `include/linux/property.h` | 517 | 统一属性访问的全部声明，以及 `PROPERTY_ENTRY_*` 构造宏 |
| `include/linux/fwnode.h` | 217 | `struct fwnode_handle`、`struct fwnode_operations`、`FWNODE_FLAG_*` |
| `include/linux/dev_printk.h` | — | `dev_err` / `dev_warn` / `dev_dbg` 全族，见 §12 |

⚠️ `include/linux/device.h` 只是**入口**。它在开头把 `bus.h` / `driver.h` / `class.h` 三个都 include 进来，所以 `#include <linux/device.h>` 一句话能拿到全部四组 API——这也是为什么很多人以为这些结构都定义在 `device.h` 里。

### §0.5 Kconfig

`drivers/base/Kconfig` 有 25 个 `config` 项。和写驱动直接相关的是这几个：

| 配置项 | 位置 | 作用 |
| --- | --- | --- |
| `AUXILIARY_BUS` | `drivers/base/Kconfig:4` | 编入 `auxiliary.c`；由使用者 `select`，不给用户选 |
| `DEVTMPFS` | `drivers/base/Kconfig:31` | 内核自己维护 `/dev` |
| `DEVTMPFS_MOUNT` | `drivers/base/Kconfig:50` | 由内核自动挂载它 |
| `DEBUG_DRIVER` | `drivers/base/Kconfig:130` | 给整个目录加 `-DDEBUG`（`drivers/base/Makefile:32`），让所有 `dev_dbg`/`pr_debug` 出声 |
| `DEBUG_DEVRES` | `drivers/base/Kconfig:141` | devres 的每次分配/释放都打印 |
| `DEBUG_TEST_DRIVER_REMOVE` | `drivers/base/Kconfig:153` | probe 成功后立刻 remove 再 probe 一次，专门炸出错误路径的 bug |
| `DEV_COREDUMP` | `drivers/base/Kconfig:125` | 编入 `devcoredump.c`；由 `WANT_DEV_COREDUMP` 与 `ALLOW_DEV_COREDUMP` 两个开关共同决定 |
| `SOC_BUS` | `drivers/base/Kconfig:194` | 编入 `soc.c` |
| `GENERIC_MSI_IRQ_DOMAIN` | 不在本目录 | 编入 `platform-msi.c`（`drivers/base/Makefile:25`） |

⚠️ 有两个 `config` 长在这个文件里，代码却不在这个目录：`DMA_SHARED_BUFFER`（`drivers/base/Kconfig:200`）和 `DMA_FENCE_TRACE`（`drivers/base/Kconfig:210`），它们管的是 `drivers/dma-buf/`。找 dma-buf 的开关时会先在错误的地方找到它——这是个纯历史遗留。

### §0.6 哪些文件是无条件编入的

`drivers/base/Makefile:4-9` 那一条 `obj-y` 是**没有任何开关能关掉**的：`component.o core.o bus.o dd.o syscore.o driver.o class.o platform.o cpu.o firmware.o init.o map.o devres.o attribute_container.o transport_class.o topology.o container.o property.o cacheinfo.o swnode.o`。

这条清单有两个值得注意的地方。一是 `component.o` 和 `attribute_container.o` 这种「只有少数用户」的东西也在无条件里，没有 Kconfig 保护。二是 `platform.o` 无条件——**platform 总线在任何配置下都存在**，这是可以放心依赖的前提。

## §1 device：生命周期与身份

头文件：`include/linux/device.h`；实现：`drivers/base/core.c`。

### §1.1 创建与销毁

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_initialize(dev)` | 把一个已分配的 `struct device` 初始化到可用状态 | 只做内存初始化，不上 sysfs。⚠️ 调过它之后**只能**用 `put_device()` 释放，不能直接 `kfree` |
| `device_add(dev)` | 把已 initialize 的设备加入驱动模型 | 返回 0 或负错误码；成功后会触发 `KOBJ_ADD` uevent 并尝试匹配驱动 |
| `device_register(dev)` | `device_initialize()` + `device_add()` | 最常用的入口。⚠️ 失败时**仍要**调 `put_device()` |
| `device_del(dev)` | 从驱动模型摘掉，但不放引用 | 与 `device_add()` 配对 |
| `device_unregister(dev)` | `device_del()` + `put_device()` | 与 `device_register()` 配对 |
| `get_device(dev)` | 加引用，返回同一指针 | 传 NULL 返回 NULL；⚠️ 引用已归零的设备返回 NULL，不是崩溃 |
| `put_device(dev)` | 放引用，归零时调 `dev->release()` | ⚠️ 没有 `release` 回调会 `WARN`，然后**内存泄漏** |
| `kill_device(dev)` | 标记设备为 dead，阻止后续绑定 | 给热移除路径用；返回 bool 表示这次调用是否真的改变了状态 |
| `device_create(class, parent, devt, drvdata, fmt, ...)` | 分配 + 注册一个属于某个 class 的设备 | 变参格式化名字；返回 `struct device *` 或 `ERR_PTR`。释放用 `device_destroy()` |
| `device_create_with_groups(...)` | 同上，额外挂一组属性 | 属性在 uevent 之前建好，见 §11 的时序陷阱 |
| `device_destroy(class, devt)` | 按设备号找到并注销 | 与 `device_create()` 配对 |
| `__root_device_register(name, owner)` | 造一个没有父设备的虚拟根设备 | ⚠️ 别直接调，用宏 `root_device_register(name)` |
| `root_device_unregister(dev)` | 与上配对 | — |

### §1.2 身份、命名与拓扑

| API | 说明 | 备注 |
| --- | --- | --- |
| `dev_set_name(dev, fmt, ...)` | 设置设备名 | ⚠️ 必须在 `device_add()` **之前**；名字决定 sysfs 目录名 |
| `dev_name(dev)` | 取设备名 | 内联；⚠️ 未设名时返回 `init_name`，`device_add()` 之后 `init_name` 会被置 NULL |
| `dev_driver_string(dev)` | 取一个「尽量有意义」的名字用于打印 | 优先 driver 名，退化到 bus 名、class 名，最后是空串。⚠️ 保证非 NULL |
| `device_rename(dev, new_name)` | 改名 | ⚠️ 源码自陈这是个坏主意（见 `03-internals.md` Part C），会破坏 udev 的假设 |
| `device_move(dev, new_parent, dpm_order)` | 改父设备 | 第三个参数决定电源管理链表里的重排方式 |
| `device_change_owner(dev, kuid, kgid)` | 改 sysfs 目录的属主 | 给用户命名空间用 |
| `device_for_each_child(dev, data, fn)` | 遍历直接子设备 | 回调返回非 0 即停；⚠️ 遍历期间对每个子设备**已加引用** |
| `device_for_each_child_reverse(dev, data, fn)` | 反向遍历 | 拆除路径常用 |
| `device_find_child(dev, data, match)` | 找一个子设备 | ⚠️ 返回值**带一个引用**，用完必须 `put_device()` |
| `device_find_child_by_name(dev, name)` | 按名字找子设备 | 同样带引用 |
| `device_find_any_child(dev)` | 随便返回一个子设备 | 同样带引用；用于「还有没有孩子」的判断 |
| `device_set_node(dev, fwnode)` | 设置设备的固件节点 | — |
| `device_set_of_node_from_dev(dev, dev2)` | 把另一个设备的 of_node 借过来 | 会同时加引用 |
| `set_primary_fwnode(dev, fwnode)` / `set_secondary_fwnode(dev, fwnode)` | 设置主/次固件节点 | 主节点通常是 DT，次节点通常是 software node |

### §1.3 匹配谓词

这一组是给 `bus_find_device()` / `class_find_device()` / `driver_find_device()` 当 `match` 回调用的现成函数，签名统一是 `(struct device *dev, const void *data)`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_match_name(dev, name)` | 名字相等 | data 是 `const char *` |
| `device_match_of_node(dev, np)` | of_node 指针相等 | data 是 `struct device_node *` |
| `device_match_fwnode(dev, fwnode)` | fwnode 相等 | 会同时看主/次节点 |
| `device_match_devt(dev, pdevt)` | 设备号相等 | data 是 `dev_t *`，注意是指针 |
| `device_match_any(dev, unused)` | 恒真 | 用来「拿第一个」 |
| `device_match_acpi_dev(dev, adev)` / `device_match_acpi_handle(dev, handle)` | ACPI 匹配 | arm64-only 范围内不涉及，见 `99-evidence.md` §2.7 |

### §1.4 私有数据

| API | 说明 | 备注 |
| --- | --- | --- |
| `dev_get_drvdata(dev)` | 取驱动私有指针 | 内联 |
| `dev_set_drvdata(dev, data)` | 设驱动私有指针 | 内联；⚠️ 这一个字段是**驱动**的，总线层不该动它 |
| `dev_get_platdata(dev)` | 取平台数据 | 内联；来源是 `platform_device_add_data()` 或板级代码 |

⚠️ 每一层都想往设备上挂自己的指针，于是有了多个看起来能用的字段。判据只有一条：**`drvdata` 归绑上来的那个驱动**，总线层要存东西得用自己的私有结构（`base.h` 里的 `device_private`）。

## §2 bus：总线

头文件：`include/linux/device/bus.h`；实现：`drivers/base/bus.c`。

### §2.1 注册与三种 subsys 形态

| API | 说明 | 备注 |
| --- | --- | --- |
| `bus_register(bus)` | 注册一条总线，建出 `/sys/bus/<name>/` | 返回 0 或负错误码；会建 `devices/` `drivers/` 两个子目录和 `drivers_probe` `drivers_autoprobe` 两个属性 |
| `bus_unregister(bus)` | 注销 | — |
| `subsys_system_register(subsys, groups)` | 注册一条「系统总线」 | 除了 `/sys/bus/<name>/` 还建 `/sys/devices/system/<name>/`；`cpu_subsys` 用的是这个 |
| `subsys_virtual_register(subsys, groups)` | 注册一条「虚拟总线」 | 根设备挂在 `/sys/devices/virtual/` 下 |
| `bus_get_kset(bus)` | 取总线的 kset | 给需要直接操作 kobject 的场合 |
| `bus_get_device_klist(bus)` | 取设备链表 | ⚠️ 拿到之后的并发安全归调用者 |

⚠️ **`bus_register()` 只建目录，不做匹配。** 总线上已有的设备与驱动的配对发生在 `device_add()` 和 `driver_register()` 里，见 `03-internals.md` Part A。

### §2.2 迭代与查找

| API | 说明 | 备注 |
| --- | --- | --- |
| `bus_for_each_dev(bus, start, data, fn)` | 遍历总线上的设备 | 回调返回非 0 即停并把该值返回。⚠️ 回调期间**不持锁**，但每个设备已加引用 |
| `bus_for_each_drv(bus, start, data, fn)` | 遍历总线上的驱动 | 同上语义 |
| `bus_find_device(bus, start, data, match)` | 找设备 | ⚠️ 返回值**带引用**，用完 `put_device()`；`match` 可用 §1.3 的现成谓词 |
| `subsys_find_device_by_id(subsys, id, hint)` | 在 subsys 里按数字 id 找设备 | `hint` 给顺序扫描提速 |
| `subsys_dev_iter_init(iter, subsys, start, type)` / `_next(iter)` / `_exit(iter)` | 显式迭代器三件套 | 需要在循环体里跳出/重入时用它，比 `for_each` 灵活 |
| `bus_sort_breadthfirst(bus, compare)` | 按广度优先重排设备链表 | 影响挂起/恢复次序 |
| `bus_rescan_devices(bus)` | 让总线上所有未绑定的设备重新尝试匹配 | 对应 sysfs 的 `drivers_probe` |
| `device_reprobe(dev)` | 单个设备先解绑再重新绑定 | ⚠️ 会真的把当前驱动踢掉 |

### §2.3 通知链与接口

| API | 说明 | 备注 |
| --- | --- | --- |
| `bus_register_notifier(bus, nb)` | 注册总线事件通知 | 事件有 `BUS_NOTIFY_ADD_DEVICE` / `BIND_DRIVER` / `BOUND_DRIVER` / `UNBIND_DRIVER` / `UNBOUND_DRIVER` / `DRIVER_NOT_BOUND` / `DEL_DEVICE` / `REMOVED_DEVICE` |
| `bus_unregister_notifier(bus, nb)` | 注销 | — |
| `subsys_interface_register(sif)` | 注册一个「对总线上每个设备都做点什么」的接口 | 注册时会对已有设备逐个调 `add_dev()` |
| `subsys_interface_unregister(sif)` | 注销，对每个设备调 `remove_dev()` | — |
| `bus_create_file(bus, attr)` / `bus_remove_file(bus, attr)` | 手工增删总线属性 | 更推荐用 `bus->bus_groups` |

### §2.4 本目录里的 8 条总线

`drivers/base/` 自己定义了 8 个 `struct bus_type`，一条都没有电气层：

| 总线 | 在哪 | 是什么 |
| --- | --- | --- |
| `platform_bus_type` | `drivers/base/platform.c:1478` | 内存映射设备的默认归属地，arm64 上绝大多数设备在这里 |
| `cpu_subsys` | `drivers/base/cpu.c:128` | 每个 CPU 一个设备 |
| `auxiliary_bus_type` | `drivers/base/auxiliary.c:247` | 把一个物理设备拆成功能子设备 |
| `soc_bus_type` | `drivers/base/soc.c:30` | SoC 标识信息 |
| `isa_bus_type` | `drivers/base/isa.c:85` | 老 ISA 设备的容身处 |
| `node_subsys` | `drivers/base/node.c:25` | NUMA 节点（已裁掉） |
| `memory_subsys` | `drivers/base/memory.c:71` | 内存块（已裁掉） |
| `container_subsys` | `drivers/base/container.c:27` | 容器设备（已裁掉） |

⚠️ 这张表是本子系统类型判定的关键证据，为什么「有 8 条总线却不算总线型子系统」在 `99-evidence.md` §1.2 和 `08-feynman.md` §1 各讲了一遍。

## §3 driver：驱动

头文件：`include/linux/device/driver.h`；实现：`drivers/base/driver.c`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `driver_register(drv)` | 注册驱动，随后尝试绑定总线上的设备 | ⚠️ `drv->bus` 必须已设好；同名驱动重复注册会 `printk` 警告但**仍然返回成功** |
| `driver_unregister(drv)` | 注销，解绑所有已绑定的设备 | — |
| `driver_find(name, bus)` | 在某条总线上按名字找驱动 | ⚠️ **不加引用**，与 `bus_find_device()` 的约定相反 |
| `driver_for_each_device(drv, start, data, fn)` | 遍历这个驱动绑定的所有设备 | 每个设备已加引用 |
| `driver_find_device(drv, start, data, match)` | 在这个驱动的设备里找一个 | ⚠️ 返回值带引用 |
| `driver_create_file(drv, attr)` / `driver_remove_file(drv, attr)` | 手工增删驱动属性 | 更推荐 `drv->groups` |
| `driver_set_override(dev, override, s, len)` | 设置设备的驱动名强制覆盖 | 给 `driver_override` sysfs 属性用，vfio 一类的主要手段 |
| `module_driver(drv, reg, unreg, ...)` | 生成 `module_init`/`module_exit` 的通用宏 | 在 `include/linux/device/driver.h:262`；各总线的 `module_*_driver` 都是它的特化 |
| `builtin_driver(drv, reg, ...)` | 只生成 `device_initcall` | 在 `include/linux/device/driver.h:289`；给不能做模块的驱动用 |

⚠️ **`driver_find()` 不加引用，`driver_find_device()` 加。** 名字只差三个字母，责任完全不同。这一组连同其余「加不加引用」的成对 API 在 `05-usage.md` §10 集中列出。

## §4 class：类

头文件：`include/linux/device/class.h`；实现：`drivers/base/class.c`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `class_register(cls)` | 注册一个类，建出 `/sys/class/<name>/` | ⚠️ 这是宏，实际调 `__class_register()`，宏负责塞进 lockdep 的 key |
| `class_unregister(cls)` | 注销 | — |
| `class_create(owner, name)` | 分配 + 注册一个类 | ⚠️ 也是宏，包 `__class_create()`；返回 `struct class *` 或 `ERR_PTR`。配对 `class_destroy()` |
| `class_destroy(cls)` | 注销并释放 | — |
| `class_for_each_device(cls, start, data, fn)` | 遍历类里的设备 | 回调返回非 0 即停 |
| `class_find_device(cls, start, data, match)` | 找一个设备 | ⚠️ 返回值带引用 |
| `class_dev_iter_init(iter, cls, start, type)` / `_next(iter)` / `_exit(iter)` | 显式迭代器三件套 | `type` 非 NULL 时只返回该 `device_type` 的设备 |
| `class_interface_register(intf)` | 注册类接口，对已有设备逐个调 `add_dev()` | 与 `subsys_interface` 是两套东西，别混 |
| `class_interface_unregister(intf)` | 注销 | — |
| `class_create_file_ns(cls, attr, ns)` / `class_remove_file_ns(...)` | 带命名空间的类属性增删 | 不带 `_ns` 的版本是内联包装，`ns` 传 NULL |
| `show_class_attr_string(cls, attr, buf)` | 现成的只读字符串属性 show 函数 | 配合 `CLASS_ATTR_STRING()` 宏用 |
| `class_compat_register(name)` | 造一个只为兼容旧路径存在的假类目录 | 返回 `struct class_compat *` |
| `class_compat_create_link(cls, dev, target)` / `class_compat_remove_link(...)` | 在假类目录里建/删指向设备的符号链接 | ⚠️ 纯兼容设施，新代码不要用 |

⚠️ **class 和 bus 的分工经常被想反。** 总线回答「这个设备是怎么被发现的」，类回答「这个设备对用户态长什么样」。一个设备可以既在 platform 总线上、又属于某个类。这组关系在 `02-datastructures.md` §1.0 展开。

## §5 绑定：设备与驱动的配对

头文件：`include/linux/device.h` 与 `include/linux/device/driver.h`；实现：`drivers/base/dd.c`。

这一组是整个驱动模型里**最少被驱动作者直接调用、又最影响驱动行为**的一组——绝大多数驱动只是被动地被这套机制调用 `probe()`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_attach(dev)` | 给一个设备找驱动并绑定 | ⚠️ 三种返回值：1 = 绑上了，0 = 没找到匹配的，负数 = 找到了但 probe 失败。带 `__must_check` |
| `driver_attach(drv)` | 给一个驱动找设备并逐个尝试绑定 | 返回 0 或负错误码；`driver_register()` 内部调它 |
| `device_driver_attach(drv, dev)` | 把指定的驱动绑到指定的设备上 | 跳过匹配，直接试 probe；`bind` sysfs 属性走这条 |
| `device_bind_driver(dev)` | 手工宣告「这个设备已经归这个驱动了」 | ⚠️ **不调 probe()**，只做记账和建 sysfs 链接。调用前 `dev->driver` 必须已经设好 |
| `device_release_driver(dev)` | 解绑 | 会调驱动的 `remove()`；`unbind` sysfs 属性走这条 |
| `device_is_bound(dev)` | 查询有没有绑上驱动 | 返回 bool |
| `wait_for_device_probe()` | 等到当前所有正在进行的 probe 都结束 | ⚠️ 包括等延迟探测队列排空；`void` 返回，没有超时 |
| `driver_deferred_probe_add(dev)` | 手工把设备塞进延迟探测队列 | 一般不该由驱动调 |
| `driver_deferred_probe_check_state(dev)` | 「现在还该不该继续等」的判断 | 返回 `-EPROBE_DEFER`（还该等）、`-ENODEV`（别等了，供应方不会来了）或 `-ETIMEDOUT` |
| `driver_deferred_probe_timeout` | 一个**变量**不是函数 | 声明在 `include/linux/device/driver.h:243`；由内核参数 `deferred_probe_timeout=` 设置，单位秒 |

### §5.1 `-EPROBE_DEFER`：唯一一个有特殊含义的返回值

`probe()` 返回 `-EPROBE_DEFER` 不算失败，它的意思是「我依赖的东西还没就绪，过一会儿再叫我」。核心会把设备放进延迟探测队列，等下一个驱动注册成功时整队重试。

两条硬约束：

- ⚠️ **不能在已经创建了子设备之后返回它。** 官方文档在 `Documentation/driver-api/driver-model/driver.rst:179-184` 明确说这会导致 `probe()` 的无限循环——子设备的出现触发重扫，重扫又调 `probe()`，再建一批子设备。要返回就在 `probe()` 的最前面返回。
- ⚠️ **它会被计入超时。** `deferred_probe_timeout` 到期后，`driver_deferred_probe_check_state()` 开始返回 `-ETIMEDOUT`，此前一直返回 `-EPROBE_DEFER` 的驱动会突然拿到一个真失败。

`-EPROBE_DEFER` 在一等文件里出现 30 次（`grep -ohE '\-E[A-Z][A-Z0-9_]+' <11 个一等文件> | sort | uniq -c`），是除 `-EINVAL`(33) 和 `-ENOMEM`(32) 之外最密集的错误码——这个密度本身说明延迟探测在这套代码里的分量。

## §6 device_link：设备之间的依赖

头文件：`include/linux/device.h`；实现：`drivers/base/core.c`。

驱动模型原本只有一种依赖关系：父子。device_link 补上了「A 要用 B 提供的服务，但 A 不是 B 的孩子」这种关系——时钟、电源域、IOMMU 都是这个形状。

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_link_add(consumer, supplier, flags)` | 建一条 consumer 依赖 supplier 的链接 | 返回 `struct device_link *` 或 **NULL**。⚠️ 返回 NULL 的原因之一是**会成环**，此时还会打一条 WARNING |
| `device_link_del(link)` | 删一条链接 | ⚠️ 只能删 stateless 链接；托管链接（managed）删不掉 |
| `device_link_remove(consumer, supplier)` | 按两端删链接 | 第一个参数类型是 `void *`——它接受任何指针，用来支持「consumer 侧只有私有结构指针」的场合 |
| `device_link_wait_removal()` | 等所有正在进行的链接删除动作结束 | 在 `include/linux/device.h:1105`，实现在 `drivers/base/core.c:598` |
| `device_links_supplier_sync_state_pause()` | 暂停 `sync_state()` 的触发 | 给需要「等我把所有供应方都注册完再统一收尾」的场合 |

### §6.1 `DL_FLAG_*` 标志

定义在 `include/linux/device.h:322-331`，共 10 个：

| 标志 | 说明 | 备注 |
| --- | --- | --- |
| `DL_FLAG_STATELESS` | 这条链接不参与状态机，只表达顺序 | ⚠️ 与下面三个 AUTO 标志的**任意组合都非法** |
| `DL_FLAG_AUTOREMOVE_CONSUMER` | consumer 解绑时自动删链接 | — |
| `DL_FLAG_AUTOREMOVE_SUPPLIER` | supplier 解绑时自动删链接 | — |
| `DL_FLAG_AUTOPROBE_CONSUMER` | supplier 绑定成功后自动去 probe consumer | — |
| `DL_FLAG_PM_RUNTIME` | 链接参与运行时电源管理 | consumer 活跃时 supplier 不许睡 |
| `DL_FLAG_RPM_ACTIVE` | 建链接时就把 supplier 拉成活跃 | ⚠️ 与 `DL_FLAG_STATELESS` 同用且**重复 add 同一条链接**时会漏掉 PM-runtime 计数，`08-feynman.md` 有专节讲这个泄漏 |
| `DL_FLAG_MANAGED` | 这条链接由核心托管 | 核心自己置，调用者不该传 |
| `DL_FLAG_SYNC_STATE_ONLY` | 只影响 `sync_state()` 的时机，不影响 probe 顺序 | — |
| `DL_FLAG_INFERRED` | 这条链接是从固件描述推断出来的，不是显式声明的 | `fw_devlink` 机制用 |
| `DL_FLAG_CYCLE` | 这条链接位于一个依赖环上 | 核心自己置 |

⚠️ 官方文档 `Documentation/driver-api/device_link.rst` 只讲了前六个。`DL_FLAG_MANAGED` / `SYNC_STATE_ONLY` / `INFERRED` / `CYCLE` 四个在文档里找不到，只能读代码——`99-evidence.md` §1.3 把这条登记成官方文档的过期项之一。

### §6.2 建链接的时机

最早能建的时间点是固定的：**supplier 已经过了 `device_add()`，consumer 已经过了 `device_initialize()`**。早于这个点调 `device_link_add()` 拿不到有效链接。

方向上有一条不对称的规则：⚠️ **父设备不能依赖子设备**（`device_link_add()` 会拒绝），但子设备可以依赖父设备（虽然多余，父子关系本身已经保证了顺序）。

## §7 devres：设备托管资源

头文件：`include/linux/device.h`；实现：`drivers/base/devres.c`。

一句话：**把「这块资源在设备解绑时要释放」这件事登记给核心**，从此 `probe()` 的错误路径不用写一长串 `goto`。

### §7.1 通用机制

| API | 说明 | 备注 |
| --- | --- | --- |
| `devres_alloc(release, size, gfp)` | 分配一块带释放函数的托管内存 | ⚠️ 是宏，实际调 `__devres_alloc_node()`，宏负责把 `#release` 变成调试用的名字字符串 |
| `devres_alloc_node(release, size, gfp, nid)` | 同上，指定 NUMA 节点 | 也是宏 |
| `devres_add(dev, res)` | 把已分配的托管块挂到设备上 | ⚠️ **alloc 和 add 是两步**，中间失败要用 `devres_free()` 而不是 `kfree()` |
| `devres_free(res)` | 释放一块还没 add 的托管块 | 已经 add 过的不能用这个 |
| `devres_find(dev, release, match, match_data)` | 找一块托管块，不摘下来 | 返回数据区指针或 NULL |
| `devres_get(dev, new_res, match, match_data)` | 找不到就把 `new_res` 加进去 | 「有则用之，无则加之」 |
| `devres_remove(dev, release, match, match_data)` | 摘下来但不调 release | 返回数据区指针 |
| `devres_destroy(dev, release, match, match_data)` | 摘下来并释放，**不调** release | 返回 0 或 `-ENOENT` |
| `devres_release(dev, release, match, match_data)` | 摘下来、调 release、再释放 | 返回 0 或 `-ENOENT`。⚠️ 与 `devres_destroy()` 只差「调不调 release」 |
| `devres_for_each_res(dev, release, match, match_data, fn, data)` | 遍历匹配的托管块 | — |

⚠️ `devres_destroy()` 和 `devres_release()` 是本子系统里最容易选错的一对。判据：**这块资源的释放动作是不是必须发生**。要发生就用 `release`。

### §7.2 分组

| API | 说明 | 备注 |
| --- | --- | --- |
| `devres_open_group(dev, id, gfp)` | 开一个组，之后的托管分配都进这个组 | 返回组标识或 `ERR_PTR`；`id` 传 NULL 时由核心生成 |
| `devres_close_group(dev, id)` | 关组 | `id` 传 NULL 表示「最近打开的那个」 |
| `devres_remove_group(dev, id)` | 摘掉组标记，组里的资源升级为设备直属 | 不释放任何资源 |
| `devres_release_group(dev, id)` | 释放整组资源 | 返回释放的块数 |

分组的用途是「一段可能要整体回退的初始化」。⚠️ 组本身有开销：`Documentation/driver-api/driver-model/devres.rst:230-234` 给的数字是——每块托管资源的记账信息在 64 位上占 **24 字节**（三个指针对齐到 `unsigned long long`），而**每个组占 8 个指针**。组不是免费的分区标记，滥用会让小块分配的开销翻几倍。

### §7.3 现成的托管接口

`devres.c` 自己提供的：

| API | 说明 | 备注 |
| --- | --- | --- |
| `devm_kmalloc(dev, size, gfp)` | 托管的 kmalloc | — |
| `devm_kzalloc(dev, size, gfp)` | 清零版 | 内联包装，在 `include/linux/device.h:208` |
| `devm_kcalloc(dev, n, size, gfp)` / `devm_kmalloc_array(dev, n, size, gfp)` | 数组版，带溢出检查 | 内联包装 |
| `devm_krealloc(dev, ptr, new_size, gfp)` | 托管的 realloc | ⚠️ 传进去的 `ptr` 必须是托管分配来的 |
| `devm_kfree(dev, p)` | 提前释放一块托管内存 | 传 NULL 或 `ERR_PTR` 是安全的空操作 |
| `devm_kstrdup(dev, s, gfp)` / `devm_kstrdup_const(dev, s, gfp)` | 托管的字符串复制 | `_const` 版对 `.rodata` 里的字符串不复制，直接返回原指针 |
| `devm_kasprintf(dev, gfp, fmt, ...)` / `devm_kvasprintf(...)` | 托管的格式化字符串 | — |
| `devm_kmemdup(dev, src, len, gfp)` | 托管的内存复制 | — |
| `devm_get_free_pages(dev, gfp, order)` / `devm_free_pages(dev, addr)` | 托管的页分配 | 返回的是 `unsigned long` 地址不是指针 |
| `devm_alloc_percpu(dev, type)` / `devm_free_percpu(dev, pdata)` | 托管的 per-cpu 分配 | ⚠️ `devm_alloc_percpu` 是宏，底下是 `__devm_alloc_percpu()` |
| `devm_add_action(dev, action, data)` | 登记一个任意的清理动作 | ⚠️ 自己分配失败时**不会**执行 action |
| `devm_add_action_or_reset(dev, action, data)` | 同上，但登记失败时立刻执行一次 action | 内联，在 `include/linux/device.h:250`。⚠️ 大多数场合应该用这个而不是上一个 |
| `devm_remove_action(dev, action, data)` | 撤销登记，不执行 | — |
| `devm_release_action(dev, action, data)` | 撤销登记并执行 | — |
| `devm_ioremap_resource(dev, res)` / `devm_ioremap_resource_wc(dev, res)` | 申请并映射一段资源 | 声明在 `include/linux/device.h:236` 和 `:238`，实现不在本目录（在 `lib/devres.c`）。返回 `ERR_PTR` 而不是 NULL |
| `device_add_group(dev, grp)` 系列的 `devm_` 版 | 托管的 sysfs 属性组增删 | 见 §11 |

⚠️ **`devm_` 前缀的接口远不止本目录提供的这些。** 各子系统各自提供了自己的托管版本（`devm_clk_get`、`devm_gpiod_get`、`devm_request_irq`……），完整清单在 `Documentation/driver-api/driver-model/devres.rst` 里按子系统分组列了约 200 行。那份清单的一部分条目对应的符号在 6.1 上是否存在需要自己 grep 确认，`99-evidence.md` §5.3 登记了这一点。

⚠️ **devres 只托管「释放」这一个动作。** 它不检查你有没有正确初始化、不检查参数、不做引用计数。`probe()` 里除释放之外的所有错误处理仍然是驱动自己的事。

## §8 platform：内存映射设备

头文件：`include/linux/platform_device.h`；实现：`drivers/base/platform.c`。

arm64 上绝大多数设备都是 platform 设备——DT 里一个带 `compatible` 的节点，默认就变成一个 platform 设备。所以这一节的 API 是本篇里驱动作者用得最多的一组。

### §8.1 驱动注册

| API | 说明 | 备注 |
| --- | --- | --- |
| `platform_driver_register(drv)` | 注册 platform 驱动 | ⚠️ 是宏（`include/linux/platform_device.h:244`），底下是 `__platform_driver_register()`，宏负责把 `THIS_MODULE` 塞进去 |
| `platform_driver_unregister(drv)` | 注销 | — |
| `module_platform_driver(drv)` | 生成 `module_init`/`module_exit` | 在 `include/linux/platform_device.h:274`；99% 的 platform 驱动只需要这一行 |
| `builtin_platform_driver(drv)` | 只生成 `device_initcall` | 在 `include/linux/platform_device.h:284` |
| `platform_driver_probe(drv, probe)` | 注册并**立刻**探测，之后注销驱动 | ⚠️ 宏，底下是 `__platform_driver_probe()`。它假设设备已经存在，用完就把驱动摘掉，**不支持延迟探测和后续热插拔** |
| `module_platform_driver_probe(drv, probe)` / `builtin_platform_driver_probe(drv, probe)` | 上面那个的模块封装 | 在 `include/linux/platform_device.h:292` 和 `:311` |
| `platform_register_drivers(drivers, count)` | 批量注册，失败时回滚已注册的 | 宏，底下 `__platform_register_drivers()` |
| `platform_unregister_drivers(drivers, count)` | 批量注销 | — |
| `platform_create_bundle(drv, probe, res, n_res, data, size)` | 一次造出设备加驱动 | ⚠️ 宏，底下 `__platform_create_bundle()`；只适合极简的板级代码 |

### §8.2 设备创建

| API | 说明 | 备注 |
| --- | --- | --- |
| `platform_device_alloc(name, id)` | 分配一个 platform 设备 | `id` 可传 `PLATFORM_DEVID_NONE`(-1) 表示名字里不带编号、`PLATFORM_DEVID_AUTO`(-2) 表示自动编号 |
| `platform_device_add_resources(pdev, res, num)` | 加资源，**会复制** | 返回 0 或 `-ENOMEM` |
| `platform_device_add_data(pdev, data, size)` | 加平台数据，**会复制** | 同上 |
| `platform_device_add(pdev)` | 注册 | — |
| `platform_device_del(pdev)` | 反注册，不放引用 | — |
| `platform_device_put(pdev)` | 放引用 | — |
| `platform_device_unregister(pdev)` | `del` + `put` | 与 `platform_device_register()` 配对 |
| `platform_device_register(pdev)` | 注册一个**静态定义**的 platform 设备 | ⚠️ 要求 `pdev` 的生命周期由调用者保证 |
| `platform_device_register_full(info)` | 用一个描述结构一次造完 | 参数是 `struct platform_device_info *`，能同时给资源、平台数据、DMA mask、fwnode |
| `platform_device_register_simple(name, id, res, num)` | 内联包装（`include/linux/platform_device.h:166`） | 只给资源 |
| `platform_device_register_data(parent, name, id, data, size)` | 内联包装（`:190`） | 只给平台数据 |
| `platform_device_register_resndata(...)` | 内联包装（`:125`） | 资源加平台数据 |
| `platform_add_devices(devs, num)` | 批量注册静态设备数组 | 失败时回滚 |
| `platform_find_device_by_driver(start, drv)` | 找这个驱动绑定的 platform 设备 | ⚠️ 返回值带引用 |

### §8.3 资源与中断查询

这一组是 `probe()` 里最常见的几行代码。

| API | 说明 | 备注 |
| --- | --- | --- |
| `platform_get_resource(pdev, type, num)` | 按类型和序号取资源 | 返回 `struct resource *` 或 NULL |
| `platform_get_resource_byname(pdev, type, name)` | 按名字取 | 名字来自 DT 的 `reg-names` |
| `platform_get_mem_or_io(pdev, num)` | 取第 num 个「内存或 IO」资源 | 两类合起来数 |
| `devm_platform_ioremap_resource(pdev, index)` | 取资源 + 申请 + 映射，一步到位 | ⚠️ 返回 `ERR_PTR` 不是 NULL，必须用 `IS_ERR()` 判 |
| `devm_platform_ioremap_resource_byname(pdev, name)` | 同上，按名字 | 同样返回 `ERR_PTR` |
| `devm_platform_get_and_ioremap_resource(pdev, index, res)` | 同上，额外把 `struct resource *` 回传出来 | 需要资源的物理地址或大小时用它 |
| `platform_get_irq(pdev, num)` | 取中断号 | ⚠️ 返回负错误码表示失败，**0 也是失败**（历史上 0 曾表示「没有中断」）。失败时**会打印错误信息** |
| `platform_get_irq_optional(pdev, num)` | 同上，但失败时不打印 | 中断是可选的时候用这个 |
| `platform_get_irq_byname(pdev, name)` | 按名字取中断，失败会打印 | 名字来自 DT 的 `interrupt-names` |
| `platform_get_irq_byname_optional(pdev, name)` | 同上，不打印 | — |
| `platform_irq_count(pdev)` | 数一共有几个中断 | ⚠️ 可能返回 `-EPROBE_DEFER`（中断控制器还没就绪） |
| `devm_platform_get_irqs_affinity(pdev, affd, minvec, maxvec, irqs)` | 一次取一批带 CPU 亲和性的中断 | 多队列设备用 |

⚠️ **`platform_get_irq()` 与 `_optional` 版本的区别只在打印。** 两者的返回值语义完全一样。选错不会出功能问题，只会在「这个中断本来就可以没有」的情况下往日志里塞噪声。

### §8.4 杂项

| API | 说明 | 备注 |
| --- | --- | --- |
| `platform_get_drvdata(pdev)` / `platform_set_drvdata(pdev, data)` | 私有指针 | 内联（`include/linux/platform_device.h:258` 和 `:263`），转手给 `dev_get_drvdata()` |
| `platform_get_device_id(pdev)` | 取匹配上的 `platform_device_id` 表项 | 宏（`include/linux/platform_device.h:47`），就是读 `pdev->id_entry` |
| `dev_is_platform(dev)` | 判断一个设备是不是 platform 设备 | 宏（`:49`），比较 `dev->bus` |
| `to_platform_device(dev)` | 从 `struct device *` 拿到 `struct platform_device *` | 宏（`:50`），`container_of` 模式 |
| `platform_bus_type` | platform 总线本身 | 变量，`drivers/base/platform.c:1478` |
| `platform_bus` | platform 总线的根设备 | 变量，所有没指定 parent 的 platform 设备挂在它下面 |

## §9 property：统一属性访问

头文件：`include/linux/property.h`、`include/linux/fwnode.h`；实现：`drivers/base/property.c`、`drivers/base/swnode.c`。

这一层的目的是**让驱动读属性时不必知道属性从哪来**。同一句 `device_property_read_u32()` 在 DT 板子上读 DT、在 software node 上读内存里的表。

⚠️ 这一层是 `06-devicetree.md` 的主角。这里只列 API 面，属性怎么写、谁消费在那一篇。

### §9.1 有没有 / 读值

`device_*` 版本接受 `struct device *`，`fwnode_*` 版本接受 `struct fwnode_handle *`。前者是后者的内联转发（转发点是 `__dev_fwnode()`）。

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_property_present(dev, propname)` | 属性存不存在 | 返回 bool |
| `fwnode_property_present(fwnode, propname)` | 同上 | — |
| `device_property_read_u8_array` / `_u16_array` / `_u32_array` / `_u64_array` | 读整数数组 | ⚠️ `val` 传 NULL 时**返回元素个数**而不是读值——这是「先问长度再分配」的标准用法 |
| `device_property_read_string_array(dev, propname, val, nval)` | 读字符串数组 | 同样支持 `val` 传 NULL 问个数 |
| `device_property_read_string(dev, propname, val)` | 读单个字符串 | ⚠️ 返回的是**指向固件数据的指针**，不是副本，不要 free |
| `device_property_match_string(dev, propname, string)` | 在字符串数组里找一项 | 返回下标或负错误码 |
| `device_property_read_u8` / `_u16` / `_u32` / `_u64` | 读单个整数 | 内联，转发到对应的 `_array` 版本，长度传 1 |
| `fwnode_property_*` | 上面每一个都有对应的 fwnode 版 | 语义相同 |
| `device_get_match_data(dev)` | 取匹配表里那一项挂的 `.data` | 返回 `const void *`；DT 匹配走 `of_match_table`，这是「一个驱动支持多个变体」的标准手段 |

### §9.2 遍历子节点与引用

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_get_next_child_node(dev, child)` | 遍历子节点 | ⚠️ 返回值**带引用**；遍历中途 break 要自己 `fwnode_handle_put()` |
| `fwnode_get_next_child_node(fwnode, child)` | 同上 | — |
| `fwnode_get_next_available_child_node(fwnode, child)` | 只返回 `status` 可用的子节点 | — |
| `device_get_named_child_node(dev, childname)` / `fwnode_get_named_child_node(...)` | 按名字取子节点 | 带引用 |
| `device_get_child_node_count(dev)` | 数子节点个数 | 返回 `unsigned int` |
| `fwnode_get_parent(fwnode)` / `fwnode_get_next_parent(fwnode)` | 取父节点 | ⚠️ `_next_parent` 会**放掉传入节点的引用**，专门用于向上循环 |
| `fwnode_count_parents(fwnode)` / `fwnode_get_nth_parent(fwnode, depth)` | 数/取第 n 层父节点 | — |
| `fwnode_handle_get(fwnode)` / `fwnode_handle_put(fwnode)` | 加/放引用 | — |
| `fwnode_get_name(fwnode)` | 取节点名 | — |
| `fwnode_device_is_available(fwnode)` | 节点是否可用 | 对应 DT 的 `status` |
| `fwnode_find_reference(fwnode, name, index)` | 顺着一个引用属性找到目标节点 | ⚠️ 返回 `ERR_PTR` 或带引用的节点 |
| `fwnode_property_get_reference_args(...)` | 取引用加参数（phandle 加 cells） | 时钟、GPIO 这类「引用加参数」的属性都是这个形状 |

### §9.3 图（graph）绑定

一组专门服务「数据通路」类硬件（摄像头、显示、音频）的 API，对应 DT 的 `ports`/`port`/`endpoint` 三层结构。

| API | 说明 | 备注 |
| --- | --- | --- |
| `fwnode_graph_get_next_endpoint(fwnode, prev)` | 遍历本节点的 endpoint | 带引用 |
| `fwnode_graph_get_port_parent(fwnode)` | 从 endpoint 找到拥有它的设备节点 | 跳过中间的 `port` 和 `ports` 两层 |
| `fwnode_graph_get_remote_endpoint(fwnode)` | 找到对端的 endpoint | — |
| `fwnode_graph_get_remote_port(fwnode)` / `_remote_port_parent(fwnode)` | 找到对端的 port / 对端设备 | — |
| `fwnode_graph_get_endpoint_by_id(fwnode, port, endpoint, flags)` | 按编号取 endpoint | — |
| `fwnode_graph_get_endpoint_count(fwnode, flags)` | 数 endpoint 个数 | — |
| `fwnode_graph_parse_endpoint(fwnode, endpoint)` | 把 endpoint 的 port/id 解析进结构 | — |

### §9.4 其他杂项转发

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_dma_supported(dev)` | 设备能不能做 DMA | 转发到固件描述；与 `dma-mapping` 那一套的 mask 是两件事 |
| `device_get_dma_attr(dev)` | 取 DMA 一致性属性 | 返回 `DEV_DMA_COHERENT` / `DEV_DMA_NON_COHERENT` / `DEV_DMA_NOT_SUPPORTED` |
| `device_get_phy_mode(dev)` / `fwnode_get_phy_mode(fwnode)` | 读 `phy-mode` 属性 | 网卡驱动用；返回 `phy_interface_t` 或负错误码 |
| `fwnode_iomap(fwnode, index)` | 按固件节点映射寄存器 | — |
| `fwnode_irq_get(fwnode, index)` / `fwnode_irq_get_byname(fwnode, name)` | 按固件节点取中断 | 与 `platform_get_irq()` 的区别是不需要 platform 设备 |
| `fwnode_connection_find_match(...)` / `_find_matches(...)` | 按连接关系找对端设备 | 给 USB Type-C、DisplayPort 一类需要「两个设备互相找」的场合 |

### §9.5 software node

不来自固件、由代码在内存里构造的属性源。主要用途是给没有 DT 描述的设备补属性，以及测试。

| API | 说明 | 备注 |
| --- | --- | --- |
| `software_node_register(node)` / `software_node_unregister(node)` | 注册单个 software node | — |
| `software_node_register_node_group(nodes)` / `_unregister_node_group(nodes)` | 注册一组 | 组内可以互相引用 |
| `software_node_register_nodes(nodes)` / `_unregister_nodes(nodes)` | 注册一个数组 | ⚠️ 与 `node_group` 版的区别是入参一个是指针数组、一个是结构数组 |
| `fwnode_create_software_node(properties, parent)` | 造一个匿名 software node | 返回 `ERR_PTR` 或 fwnode |
| `fwnode_remove_software_node(fwnode)` | 删掉 | — |
| `device_add_software_node(dev, swnode)` | 把 software node 挂到设备的次固件节点上 | ⚠️ 必须在 `device_add()` **之前** |
| `device_remove_software_node(dev)` | 摘掉 | — |
| `device_create_managed_software_node(dev, properties, parent)` | 造一个随设备一起销毁的 software node | — |
| `software_node_find_by_name(parent, name)` | 按名字找 | — |
| `is_software_node(fwnode)` / `to_software_node(fwnode)` / `software_node_fwnode(node)` | 类型判断与互转 | — |
| `property_entries_dup(properties)` / `property_entries_free(properties)` | 复制/释放属性表 | — |
| `PROPERTY_ENTRY_U32(name, val)` 等构造宏 | 在 `include/linux/property.h:332-386`，共 20 个 | 覆盖 u8/u16/u32/u64 的单值与数组、字符串、bool、引用 |

## §10 小框架

这一节的每一项都是一个**独立的、彼此不调用**的小框架，只列 API 面，机制在 `04-extensions.md`。

### §10.1 component：多设备一起 bind

头文件 `include/linux/component.h`；实现 `drivers/base/component.c`。

| API | 说明 | 备注 |
| --- | --- | --- |
| `component_add(dev, ops)` | 把自己登记为一个组件 | — |
| `component_add_typed(dev, ops, subcomponent)` | 同上，带子类型编号 | `subcomponent` 为 0 时等价于上一个 |
| `component_del(dev, ops)` | 注销组件 | — |
| `component_master_add_with_match(dev, ops, match)` | 登记聚合方并给出它要等哪些组件 | 组件齐了才调 `ops->bind()` |
| `component_master_del(dev, ops)` | 注销聚合方 | — |
| `component_bind_all(parent, data)` / `component_unbind_all(parent, data)` | 在聚合方的 `bind()` 里逐个绑定/解绑组件 | — |
| `component_match_add(parent, matchptr, compare, data)` | 往匹配表加一项 | 内联（`include/linux/component.h:123`），转发到 `component_match_add_release()` 并把 release 传 NULL |
| `component_match_add_release(...)` / `component_match_add_typed(...)` | 带释放回调 / 带子类型的版本 | — |
| `component_compare_of` / `component_release_of` / `component_compare_dev` / `component_compare_dev_name` | 四个现成的比较/释放回调 | 声明在 `include/linux/component.h:86-89` |

### §10.2 auxiliary bus：一个设备拆成多个

| API | 说明 | 备注 |
| --- | --- | --- |
| `auxiliary_device_init(auxdev)` | 初始化辅助设备 | ⚠️ 必须先设好 `name` 和 `id`，两者拼成设备名 |
| `auxiliary_device_add(auxdev)` | 注册 | 宏，底下是 `__auxiliary_device_add()`，宏负责传 `KBUILD_MODNAME` |
| `auxiliary_driver_register(auxdrv)` | 注册辅助驱动 | 宏，底下 `__auxiliary_driver_register()` |
| `auxiliary_driver_unregister(auxdrv)` | 注销 | — |
| `auxiliary_find_device(start, data, match)` | 找辅助设备 | ⚠️ 返回值带引用 |
| `module_auxiliary_driver(auxdrv)` | 模块封装宏 | — |

### §10.3 soc / isa / syscore / devcoredump

| API | 说明 | 备注 |
| --- | --- | --- |
| `soc_device_register(attr)` | 注册一个 SoC 标识设备 | 返回 `struct soc_device *` 或 `ERR_PTR` |
| `soc_device_unregister(soc_dev)` | 注销 | — |
| `soc_device_match(matches)` | 按 SoC 标识匹配 | ⚠️ 用来做「这颗芯片的这个版本要特殊处理」的判断；返回匹配到的表项或 NULL |
| `isa_register_driver(drv, ndev)` | 注册 ISA 驱动，同时造出 ndev 个设备 | arm64 上基本用不到 |
| `isa_unregister_driver(drv)` | 注销 | — |
| `register_syscore_ops(ops)` / `unregister_syscore_ops(ops)` | 登记系统级挂起/恢复回调 | ⚠️ 这些回调在**中断关闭、只有一个 CPU 在跑**的上下文里执行，不能睡 |
| `syscore_suspend()` / `syscore_resume()` | 由电源管理核心调用 | 驱动不该直接调 |
| `dev_coredumpv(dev, data, datalen, gfp)` | 把一块 vmalloc 内存倒成 coredump | ⚠️ 无论成功失败，`data` 的所有权都交出去了 |
| `dev_coredumpm(dev, owner, data, datalen, gfp, read, free)` | 用自定义读函数产生 coredump | 数据量大或需要惰性生成时用 |
| `dev_coredumpsg(dev, table, datalen, gfp)` | 用 scatterlist 提供数据 | — |

### §10.4 attribute_container / transport_class

只有 SCSI 一族在用，arm64 上除非做存储否则碰不到。

| API | 说明 | 备注 |
| --- | --- | --- |
| `attribute_container_register(cont)` / `_unregister(cont)` | 注册一个属性容器 | — |
| `attribute_container_classdev_to_container(classdev)` | 从 class 设备反查容器 | — |
| `attribute_container_find_class_device(cont, dev)` | 查某个设备在容器里对应的 class 设备 | — |
| `transport_class_register(tclass)` / `_unregister(tclass)` | 注册传输类 | — |
| `anon_transport_class_register(atc)` / `_unregister(atc)` | 注册匿名传输类 | 不需要 sysfs 目录的场合 |
| `transport_setup_device(dev)` / `transport_add_device(dev)` / `transport_configure_device(dev)` | 三段式建立 | ⚠️ 必须按这个顺序调，三者分别对应「准备」「上 sysfs」「配置完成」 |
| `transport_remove_device(dev)` / `transport_destroy_device(dev)` | 两段式拆除 | — |

### §10.5 platform-msi / pinctrl / map

| API | 说明 | 备注 |
| --- | --- | --- |
| `platform_msi_create_irq_domain(fwnode, info, parent)` | 给非 PCI 设备建 MSI 中断域 | 需要 `CONFIG_GENERIC_MSI_IRQ_DOMAIN` |
| `platform_msi_domain_alloc_irqs(dev, nvec, write_msi_msg)` | 分配 MSI 中断 | — |
| `platform_msi_domain_free_irqs(dev)` | 释放 | — |
| `pinctrl_bind_pins(dev)` | 在 probe 之前替设备申请默认引脚配置 | ⚠️ 不导出，由 `dd.c` 内部调用；驱动不该调 |
| `kobj_map` / `kobj_lookup` / `kobj_unmap` / `kobj_map_init` | 主设备号到 kobject 的映射表 | ⚠️ 不导出，只服务字符设备与块设备层 |

## §11 sysfs 属性

属性宏都在头文件里，`_show`/`_store` 函数名由宏按约定拼出来。

| 宏 | 说明 | 备注 |
| --- | --- | --- |
| `DEVICE_ATTR(name, mode, show, store)` | 定义一个设备属性 | `include/linux/device.h:127`；生成的变量名是 `dev_attr_<name>` |
| `DEVICE_ATTR_RO(name)` / `_RW(name)` / `_WO(name)` | 常用权限的简写 | `:132`–`:140`；分别要求 `<name>_show` / 两个都要 / 只要 `<name>_store` |
| `DEVICE_ATTR_ADMIN_RO(name)` / `_ADMIN_RW(name)` | 权限收到 0400 / 0600 | `:134` 和 `:138` |
| `DEVICE_ATTR_PREALLOC(name, mode, show, store)` | 预分配缓冲区的版本 | `:129` |
| `DEVICE_ATTR_IGNORE_LOCKDEP(...)` | 让 lockdep 不管这个属性 | `:151`；⚠️ 用它等于承认这里有个 lockdep 抓得到的问题 |
| `DEVICE_ULONG_ATTR(name, mode, var)` / `DEVICE_INT_ATTR(...)` / `DEVICE_BOOL_ATTR(...)` | 直接暴露一个变量 | `:142`–`:148`；底下用的是 `device_show_ulong`/`device_store_ulong` 那几个导出函数 |
| `BUS_ATTR_RO/RW/WO(name)` | 总线属性 | `include/linux/device/bus.h:132`–`:136` |
| `DRIVER_ATTR_RO/RW/WO(name)` | 驱动属性 | `include/linux/device/driver.h:143`–`:147` |
| `CLASS_ATTR_RO/RW/WO(name)` | 类属性 | `include/linux/device/class.h:199`–`:203` |
| `CLASS_ATTR_STRING(name, mode, str)` | 一个只读的固定字符串属性 | `include/linux/device/class.h:234`；配 `show_class_attr_string()` |
| `ATTRIBUTE_GROUPS(name)` | 把 `<name>_attrs[]` 包成 `<name>_groups[]` | 不在本目录，在 `include/linux/sysfs.h` |

运行期增删属性：

| API | 说明 | 备注 |
| --- | --- | --- |
| `device_create_file(dev, attr)` / `device_remove_file(dev, attr)` | 单个属性 | ⚠️ 见下面的时序陷阱 |
| `device_remove_file_self(dev, attr)` | 从属性自己的 `store()` 里删掉自己 | 专门解决「写这个文件导致对象消失」的死锁 |
| `device_create_bin_file(dev, attr)` / `device_remove_bin_file(dev, attr)` | 二进制属性 | — |
| `device_add_groups(dev, groups)` / `device_remove_groups(dev, groups)` | 一组属性 | `include/linux/device.h:1034` 和 `:1036` |
| `device_add_group(dev, grp)` / `device_remove_group(dev, grp)` | 单组的内联包装 | `:1039` 和 `:1047` |
| `devm_device_add_groups(dev, groups)` / `devm_device_add_group(dev, grp)` | 托管版本 | `:1055` 和 `:1059`，解绑时自动删 |
| `devm_device_remove_groups(...)` / `devm_device_remove_group(...)` | 提前手工删 | `:1057` 和 `:1061` |

⚠️ **属性必须在 `KOBJ_ADD` uevent 之前建好，否则用户态永远不知道它们存在。** `Documentation/driver-api/driver-model/device.rst:109-120` 专门警告了这一点：内核允许你在任何时候 `device_create_file()`，但 udev 只在收到 uevent 时看一次。**唯一正确的做法是用 `dev->groups` 在 `device_add()` 之前把属性组挂好**，让核心在建 sysfs 目录时一并建出来。在 `probe()` 里 `device_create_file()` 的驱动是错的——虽然文件确实出现在 sysfs 里。

## §12 dev_printk 宏族

头文件：`include/linux/dev_printk.h`；后端实现：`drivers/base/core.c`。

| 宏 | 说明 | 备注 |
| --- | --- | --- |
| `dev_printk(level, dev, fmt, ...)` | 带设备前缀的打印 | `include/linux/dev_printk.h:126` |
| `dev_emerg` / `dev_alert` / `dev_crit` / `dev_err` / `dev_warn` / `dev_notice` / `dev_info` | 七个等级 | `:147`–`:159`；前缀形如 `driver_name device_name: ` |
| `dev_dbg(dev, fmt, ...)` | 调试级 | ⚠️ 有三个不同的定义（`:164` / `:167` / `:170`），取哪一个取决于 `CONFIG_DYNAMIC_DEBUG` 与 `DEBUG`。三种行为分别是：动态可开关 / 编译进去总是打印 / 编译掉但仍做参数类型检查 |
| `dev_*_once(dev, fmt, ...)` | 八个 `_once` 变体 | `:192`–`:206`；⚠️ 「一次」是**按代码位置**算的，不是按设备算的——多个设备走同一行只出一条 |
| `dev_*_ratelimited(dev, fmt, ...)` | 八个限速变体 | `:218`–`:247` |
| `dev_err_probe(dev, err, fmt, ...)` | probe 失败时的标准写法 | ⚠️ 三个特性：返回值就是 `err`（可以 `return dev_err_probe(...)`）；`err` 是 `-EPROBE_DEFER` 时降级成 debug 级并把原因记进 sysfs；其余情况按 error 级打印 |
| `dev_WARN(dev, format, ...)` / `dev_WARN_ONCE(dev, condition, ...)` | 带设备信息的 WARN | 会打栈回溯 |

⚠️ **`dev_err_probe()` 应该是 probe 错误路径的默认写法。** 它解决的是「延迟探测的正常重试会往日志里刷一堆 error」这个实际问题：`deferred_probe_reason` 这个 sysfs 属性的内容就来自它。

### §12.1 那 7 个符号是宏生成的

`drivers/base/core.c:4979` 定义了 `define_dev_printk_level(func, kern_level)`，在 `:4996`–`:5002` 被调用 7 次，生成 `_dev_emerg` 到 `_dev_info` 七个函数，每个都带一句 `EXPORT_SYMBOL`。

这一条有两个后果：

- `grep -c "^EXPORT_SYMBOL" drivers/base/core.c` 给出 61，但真正导出的符号是 61 减 1 加 7 = **67 个**。整个 `drivers/base/*.c` 的口径同理：312 行 `EXPORT_SYMBOL` 对应 **318 个符号**。这个差额登记在 `99-evidence.md` §2.6。
- 用符号索引工具搜 `_dev_err` 的定义会**什么也搜不到**——它不以源码形式存在。这正是索引工具的宏盲区，`99-evidence.md` §2.4 用它作为本子系统的实例。

## §13 错误码语义

一等文件（`core.c` `platform.c` `dd.c` `devres.c` `bus.c` `class.c` `driver.c` `map.c` `module.c` `init.c` `base.h`）里的错误码分布，命令是 `grep -ohE '\-E[A-Z][A-Z0-9_]+' <这 11 个文件> | sort | uniq -c | sort -rn`：

| 错误码 | 次数 | 在这套代码里的含义 |
| --- | --- | --- |
| `-EINVAL` | 33 | 参数不合法。⚠️ 语义最杂的一个，见下面的分组 |
| `-ENOMEM` | 32 | 分配失败，几乎全在 `devres.c` 和 `core.c` 的 sysfs 建立路径 |
| `-EPROBE_DEFER` | 30 | 依赖未就绪，**不是失败**，见 §5.1 |
| `-ENODEV` | 21 | 设备不存在或已死。也是 `driver_deferred_probe_check_state()` 在「供应方永远不会来」时的返回值 |
| `-EIO` | 10 | 主要在 `core.c` 的 sysfs 与 uevent 路径 |
| `-EAGAIN` | 9 | 稍后重试。⚠️ 与 `-EPROBE_DEFER` 的区别是它**不进延迟探测队列** |
| `-ENXIO` | 7 | 资源不存在。`platform_get_resource()` 系列找不到资源时的典型返回 |
| `-EBUSY` | 6 | 对象正被占用。`device_release_driver()` 相关路径 |
| `-ENOENT` | 4 | 找不到条目。`devres_destroy()` / `devres_release()` 没找到匹配块时返回它 |
| `-ETIMEDOUT` | 2 | 延迟探测超时后 `driver_deferred_probe_check_state()` 的返回值 |
| `-EPERM` | 2 | 权限不足 |
| `-ENOSPC` | 2 | 空间不足 |
| `-ERANGE` | 1 | 超出范围 |

⚠️ **上面这个命令有一个假阳性**：`drivers/base/dd.c:1105` 的注释里写着「Returns 0 on success, -ERR on failure」，`-ERR` 会被 grep 命中但它不是错误码。重跑时看到 13 行而不是 12 行是正常的。

### §13.1 `-EINVAL` 的三类来源

33 次里语义分三类，混在一起看会以为「参数错了」，实际要查的地方不同：

| 类别 | 典型场合 | 该查什么 |
| --- | --- | --- |
| 空指针或空字段 | 注册时 `bus`/`name` 没设、`dev` 传了 NULL | 检查注册前的结构体填写 |
| 状态不对 | 在 `device_add()` 之前/之后做了只能反过来做的事 | 检查调用时序 |
| 标志组合非法 | `device_link_add()` 的 `DL_FLAG_*` 组合冲突 | 见 §6.1 |

### §13.2 返回 NULL 与返回 ERR_PTR 的两派

这套 API 在这一点上**不统一**，是实际写代码时最常踩的地方：

| 返回 NULL 表示失败 | 返回 ERR_PTR 表示失败 |
| --- | --- |
| `device_link_add()` | `device_create()` / `device_create_with_groups()` |
| `platform_get_resource()` / `_byname()` | `class_create()` |
| `devres_find()` | `devres_open_group()` |
| `bus_find_device()` / `class_find_device()` / `driver_find_device()`（找不到时） | `devm_platform_ioremap_resource()` 一族 |
| `driver_find()` | `devm_ioremap_resource()` |
| `soc_device_match()` | `soc_device_register()` |
| `fwnode_get_parent()` 一族 | `fwnode_create_software_node()` / `fwnode_find_reference()` |

⚠️ 判据没有规律可循，只能查。**用 `IS_ERR()` 判一个返回 NULL 的函数，等于把失败当成功**——这类 bug 在正常路径上完全看不出来。这一组连同其余易混对照在 `05-usage.md` §10 集中列出。

