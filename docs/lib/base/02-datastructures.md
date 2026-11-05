---
title: 数据结构
---

# base 数据结构

这一篇是字段级参考。查「某个字段是什么意思、谁写它、什么时候有效」来这里；查 API 怎么调去 `01-api.md`；查这些字段在流程里什么时候被填去 `03-internals.md`。

⚠️ 开头的 §1.0 不是导言，是**必须先读的部分**。这个子系统的结构体不多，但它们互相之间的关系有六处特别容易想错，图画得再全也补不上一个没被显式否掉的错误类比。

## §1.0 先分清六组关系

### §1.0.1 四个核心结构各回答什么问题

| 结构 | 回答的问题 | 谁分配它 | 生命周期 |
| --- | --- | --- | --- |
| `struct device` | 「系统里有这么一个东西」 | 发现它的那一层（DT 扫描、总线枚举、驱动手工造） | 引用计数归零时由 `release()` 回调释放 |
| `struct device_driver` | 「有这么一段代码能驱动某类设备」 | 驱动模块，通常是静态变量 | 模块加载到卸载 |
| `struct bus_type` | 「这类设备是怎么被发现和匹配的」 | 总线实现者，通常是静态变量 | ⚠️ 通常永不注销 |
| `struct class` | 「这个设备对用户态呈现成什么」 | 类实现者 | 通常永不注销 |

⚠️ **`bus_type` 和 `class` 不是「两种总线」，是两个正交的维度。** 一个设备同时有 `dev->bus` 和 `dev->class`，两个都可以非空。总线管「怎么找到它、谁来驱动它」，类管「它在 `/sys/class/` 下叫什么、devnode 怎么命名」。

⚠️ **`device_driver` 不是「驱动某个设备的代码」，是「驱动某一类设备的代码」。** 一个 `device_driver` 可以同时绑在几十个设备上，绑定关系记在 `driver->p->klist_devices` 里，不在 `device_driver` 本体的任何字段上。

### §1.0.2 `bus_type` 与 `class` 共用同一个私有结构

这一条最反直觉，也最容易在读代码时卡住。

`struct bus_type` 的 `p` 字段和 `struct class` 的 `p` 字段**类型完全相同**，都是 `struct subsys_private *`（`drivers/base/base.h:40-55`）。这个结构里同时有 `bus` 和 `class` 两个反向指针（`drivers/base/base.h:51` 和 `drivers/base/base.h:54`），哪个非空就表示这个 `subsys_private` 服务的是哪一方。

后果：

- `class.c` 和 `bus.c` 里大量代码走的是同一套 kset / klist 机制，只是外壳不同
- 读到一个操作 `sp->klist_devices` 的函数时，不能从代码本身判断它服务的是总线还是类，要看调用者
- `subsys_private` 里的 `mutex`（`drivers/base/base.h:44`）既是总线的设备/接口链表锁，也是类的

⚠️ 名字里的 `subsys` 也是个坑：它和 `subsys_system_register()` / `subsys_interface` 里的 `subsys` **不是同一个概念**。前者是「bus 与 class 的公共实现」，后者是「一种特殊形态的总线」。

### §1.0.3 三个 `p` 指针不是同一件事

驱动模型里有三个结构都有个叫 `p` 的私有指针，类型各不相同：

| 谁的 `p` | 类型 | 定义位置 | 装什么 |
| --- | --- | --- | --- |
| `dev->p` | `struct device_private *` | `drivers/base/base.h:88-99` | 子设备 klist、在四条链表里的节点、延迟探测项、`deferred_probe_reason`、`dead` 标记 |
| `drv->p` | `struct driver_private *` | `drivers/base/base.h:58-64` | 驱动的 kobject、它绑定的设备 klist、在总线驱动链表里的节点 |
| `bus->p` / `cls->p` | `struct subsys_private *` | `drivers/base/base.h:40-55` | 总线/类的 kset、devices 与 drivers 两个子目录、两条 klist、通知链、`drivers_autoprobe` 位 |

⚠️ 三个结构**全部只声明在 `drivers/base/base.h` 里**，头注释（`drivers/base/base.h:9-10`）明确写着不许在这个目录之外共享，`device_private` 的 kerneldoc 又补了一句「Nothing outside of the driver core should ever touch these fields」（`drivers/base/base.h:86`）。所以在 `include/linux/` 下找不到它们的定义不是漏了——是设计如此。这也意味着**驱动代码没有任何合法途径读这三个结构的字段**。

### §1.0.4 父子关系与 device_link 是两种依赖

| | 父子（`dev->parent`） | device_link |
| --- | --- | --- |
| 表达什么 | 拓扑归属：「我挂在它下面」 | 功能依赖：「我要用它提供的服务」 |
| 建立时机 | `device_add()` 之前设好 | `device_add()` 之后随时 |
| 数量 | 一个设备只有一个父 | 任意多条，两个方向都有 |
| 保证什么 | 父先 add、子后 add；子先 del、父后 del | supplier 先 probe 完，consumer 才 probe |
| 存在哪 | `dev->parent` 一个指针 | `dev->links.suppliers` 与 `dev->links.consumers` 两条链表 |
| 能不能成环 | 结构上不可能 | ⚠️ 可能，核心会检测并拒绝 |

⚠️ **父子关系不保证 probe 顺序。** 父设备的 `probe()` 可能还没跑完，子设备就已经开始 probe 了。要保证顺序必须建 device_link。这一条是「明明是父子，为什么还要显式建链接」的答案。

⚠️ 反过来：**父设备不能通过 device_link 依赖子设备**，`device_link_add()` 会拒绝这种方向。

### §1.0.5 `platform_device` 与 `device` 是包含关系，不是继承

```c
struct platform_device {
	const char	*name;
	int		id;
	bool		id_auto;
	struct device	dev;          /* 就是一个内嵌的完整 device */
	...
};
```

C 语言里没有继承，驱动模型用的是「内嵌加 `container_of`」。三个后果：

- `&pdev->dev` 和 `pdev` 是**两个不同的地址**（`dev` 不是第一个字段），指针不能直接互转
- 从 `struct device *` 回到 `struct platform_device *` 必须用 `to_platform_device()`，而这个宏**不做任何检查**——对一个不是 platform 设备的 `struct device *` 用它，会算出一个野指针
- 判断能不能转的办法是 `dev_is_platform(dev)`，它比较的是 `dev->bus`

⚠️ 同样的形状出现在 `struct device_link` 里：它内嵌了 `struct device link_dev`（`include/linux/device.h:675`）。**一条 device_link 自己也是一个设备**，会在 sysfs 里占一个目录。这一条在 `08-feynman.md` 有专节讲。

### §1.0.6 本子系统有几层可插拔

| 层 | ops 结构 | 实现者在哪 | 本目录内的实现数 |
| --- | --- | --- | --- |
| 总线层 | `struct bus_type`（13 个回调） | 各总线子系统 | 8 个（见 `01-api.md` §2.4） |
| 类层 | `struct class`（7 个回调） | 各类实现者 | 若干，散在全树 |
| 驱动层 | `struct device_driver`（7 个回调） | 每个驱动 | 全树数千 |
| 设备类型层 | `struct device_type`（3 个回调） | 总线或类的实现者 | — |
| 固件节点层 | `struct fwnode_operations`（22 个回调） | `drivers/of/`、`drivers/base/swnode.c`、ACPI（已裁掉） | 本目录内 1 个（software node） |

⚠️ **五层不是自上而下调用的一条链。** 总线层与驱动层在 probe 路径上确实是先后关系，但类层和设备类型层只参与 sysfs 与 uevent，固件节点层只参与属性读取——它们互不调用。这也是为什么 `03-internals.md` 要分 A/B/C 三条轴而不是画一条流水线。

### §1.0.7 名字里带 device 的一堆结构

| 名字 | 是什么 | 别和谁混 |
| --- | --- | --- |
| `struct device` | 设备本体 | — |
| `struct device_private` | 设备的核心私有数据 | 不是 `driver_data` |
| `struct device_driver` | 驱动本体 | 不是 `driver_private` |
| `struct device_type` | 一小组回调加属性组，给「同一条总线上的几种设备」分类 | ⚠️ 与 `bus_type` 完全不同层次 |
| `struct device_link` | 两个设备之间的一条依赖 | 不是 `fwnode_link` |
| `struct device_attribute` | 一个 sysfs 文件 | — |
| `struct device_node` | ⚠️ **DT 节点，不属于本子系统**，定义在 `include/linux/of.h` | 不是 `struct device` |
| `struct device_dma_parameters` | DMA 的段大小与边界约束 | 归 `dma-mapping` 那一套 |
| `struct device_physical_location` | 设备在机箱上的物理位置 | ACPI 才有，已裁掉 |

⚠️ **`device_node` 与 `device` 是全内核最容易串的一对。** 前者是固件（DT）里的一个节点，后者是内核对象。一个 `device_node` 可能对应零个、一个或多个 `device`。它们由 `dev->of_node` 单向关联。

## §2 `struct device`

位置：`include/linux/device.h:557-655`。共 48 个字段，其中 **9 个带条件编译**、39 个无条件。这是本子系统最大的结构，但绝大多数字段不属于驱动模型自己——它们是别的子系统在这里寄居的状态。

### §2.1 身份与拓扑（无条件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kobj` | `struct kobject` | sysfs 里的那个目录，也是引用计数的所在。⚠️ **它是第一个字段**，所以 `&dev->kobj` 与 `dev` 地址相同 |
| `parent` | `struct device *` | 父设备。⚠️ 必须在 `device_add()` 之前设好 |
| `p` | `struct device_private *` | 核心私有数据，见 §9.1 |
| `init_name` | `const char *` | 初始名字。⚠️ `device_add()` 成功后会被置 NULL，之后名字存在 `kobj.name` 里 |
| `type` | `const struct device_type *` | 设备类型，见 §5.2 |
| `id` | `u32` | 实例编号 |
| `devt` | `dev_t` | 字符/块设备号。非 0 时 sysfs 里会出现 `dev` 文件 |

### §2.2 归属与绑定（无条件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `bus` | `struct bus_type *` | 它在哪条总线上。⚠️ 决定用哪个 `match()` 找驱动 |
| `driver` | `struct device_driver *` | 当前绑上来的驱动，未绑定时为 NULL |
| `class` | `struct class *` | 它属于哪个类，可以为 NULL |
| `groups` | `const struct attribute_group **` | ⚠️ 额外的 sysfs 属性组，**必须在 `device_add()` 之前设好**，否则用户态收不到通知（`01-api.md` §11） |
| `release` | `void (*)(struct device *)` | 引用归零时的释放函数。⚠️ 不设它会在 `put_device()` 时 `WARN` 并泄漏内存 |

⚠️ `release` 的查找顺序是三级的：`dev->release` → `dev->type->release` → `dev->class->dev_release`。三个都没有才 `WARN`。所以「我没设 release 但也没报警」通常意味着 `device_type` 或 `class` 替你设了。

### §2.3 私有数据（无条件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `platform_data` | `void *` | 板级代码给的数据。注释明确写着「device core doesn't touch it」 |
| `driver_data` | `void *` | 驱动私有指针，用 `dev_set_drvdata()`/`dev_get_drvdata()` 存取 |

⚠️ 这两个字段的所有权归属不同：`platform_data` 归**造出这个设备的那一方**，`driver_data` 归**绑上来的驱动**。驱动改 `platform_data` 是错的。

### §2.4 锁与依赖（无条件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `mutex` | `struct mutex` | ⚠️ 就是 `device_lock()` 操作的那把锁，注释说它「同步对这个设备的驱动的调用」。**不要直接访问它**，用 `device_lock()`/`device_unlock()`/`device_trylock()`（`include/linux/device.h:838`/`:853`/`:848`） |
| `links` | `struct dev_links_info` | device_link 的两条链表加状态，见 §6 |
| `devres_lock` | `spinlock_t` | 保护 `devres_head` |
| `devres_head` | `struct list_head` | 托管资源链表，见 §8 |

### §2.5 固件描述（无条件）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `of_node` | `struct device_node *` | 关联的 DT 节点 |
| `fwnode` | `struct fwnode_handle *` | 统一的固件节点句柄，见 §10 |
| `of_node_reused` | `bool:1` | 这个 `of_node` 是从别的设备借来的 |

⚠️ **`of_node` 与 `fwnode` 有冗余。** DT 场景下 `fwnode` 指向的就是 `of_node` 里内嵌的那个 `fwnode_handle`。新代码应该只用 `fwnode` 和 `property.c` 那组 API；直接读 `of_node` 会把驱动锁死在 DT 上，software node 场景下就不工作了。

### §2.6 电源、中断、IOMMU 与 DMA（部分条件编译）

这一组字段本身不由本子系统实现，只是寄居在这里。

| 字段 | 条件 | 归谁 |
| --- | --- | --- |
| `power` | 无条件 | PM 子系统（`drivers/base/power/`，已裁掉） |
| `pm_domain` | 无条件 | PM 域 |
| `em_pd` | `CONFIG_ENERGY_MODEL` | 能耗模型 |
| `pins` | `CONFIG_PINCTRL` | pinctrl，本目录的 `pinctrl.c` 会填它 |
| `msi` | 无条件 | 中断子系统 |
| `iommu_group` / `iommu` | 无条件 | `drivers/iommu/` |
| `dma_ops` | `CONFIG_DMA_OPS` | `dma-mapping` |
| `dma_mask` / `coherent_dma_mask` / `bus_dma_limit` / `dma_range_map` / `dma_parms` / `dma_pools` | 无条件 | `dma-mapping` |
| `dma_mem` | `CONFIG_DMA_DECLARE_COHERENT` | `dma-mapping` 的 per-device 一致性池 |
| `cma_area` | `CONFIG_DMA_CMA` | CMA |
| `dma_io_tlb_mem` | `CONFIG_SWIOTLB` | swiotlb |
| `dma_ops_bypass` | `CONFIG_DMA_OPS_BYPASS` | `dma-mapping` |
| `dma_coherent` | 三个 `ARCH_HAS_SYNC_DMA_FOR_*` 任一 | `dma-mapping`；arm64 上存在 |
| `archdata` | 无条件 | 体系结构；arm64 上是空结构 |
| `numa_node` | `CONFIG_NUMA` | mm |

⚠️ **DMA 相关字段占了 `struct device` 的四分之一。** 它们的语义全部由 DMA 映射子系统定义，本子系统只负责在 `bus->dma_configure()` 里触发初始化。字段级说明去 dma-mapping 那套文档，不在这里重复。

### §2.7 状态位（无条件，共 5 个加 2 个条件位）

| 字段 | 说明 |
| --- | --- |
| `offline_disabled:1` | 不允许下线 |
| `offline:1` | 当前处于下线状态 |
| `state_synced:1` | ⚠️ `sync_state()` 已经调过了。这个位保证 `sync_state()` **每个设备只调一次** |
| `can_match:1` | 允许参与驱动匹配 |
| `removable` | `enum device_removable`，是不是可拔插；⚠️ 是枚举不是位域 |

## §3 `struct bus_type`

位置：`include/linux/device/bus.h:84-118`。共 24 个字段，其中 **13 个是回调**。

### §3.1 标识与 sysfs

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `const char *` | 总线名，也是 `/sys/bus/<name>/` 的目录名 |
| `dev_name` | `const char *` | 给这条总线上自动命名的设备用的名字前缀 |
| `dev_root` | `struct device *` | 总线的根设备 |
| `bus_groups` | `const struct attribute_group **` | 挂在 `/sys/bus/<name>/` 上的属性 |
| `dev_groups` | `const struct attribute_group **` | ⚠️ 挂在**这条总线上每一个设备**上的属性 |
| `drv_groups` | `const struct attribute_group **` | 挂在这条总线上每一个驱动上的属性 |

⚠️ 三个 `*_groups` 的挂载对象完全不同，名字只差一个词。写总线时把设备属性填进 `bus_groups` 的结果是：属性出现在总线目录里而不是每个设备下面，而且不报错。

### §3.2 十三个回调

| 回调 | 什么时候调 | 备注 |
| --- | --- | --- |
| `match(dev, drv)` | 每次尝试配对时 | 返回非 0 表示匹配。⚠️ 这是总线**必须**实现的那一个；不实现意味着这条总线上永远配不上驱动 |
| `probe(dev)` | 匹配成功后 | 总线层的 probe，负责再去调驱动的 probe。⚠️ 与驱动的 `probe()` 是两层 |
| `remove(dev)` | 解绑时 | 同样是总线层的 |
| `sync_state(dev)` | 消费方就绪之后 | 见 §3.3 |
| `shutdown(dev)` | 系统关机 | — |
| `uevent(dev, env)` | 产生 uevent 时 | 往环境变量里加总线特有的键值 |
| `online(dev)` / `offline(dev)` | sysfs 的 `online` 属性被写 | 逻辑上下线，与热插拔不同 |
| `suspend(dev, state)` / `resume(dev)` | 旧式电源管理 | ⚠️ 新代码用 `pm` 字段，这两个是遗留路径 |
| `num_vf(dev)` | 查询虚拟功能数 | SR-IOV，PCI 专用 |
| `dma_configure(dev)` | probe **之前** | ⚠️ 本子系统与 `dma-mapping` / `iommu` 的交接点就在这里：DMA mask、`dma_ops`、IOMMU 组都在这一步确定 |
| `dma_cleanup(dev)` | 解绑时 | 与上一个配对 |

### §3.3 `sync_state()`：一个只调一次的回调

它解决的问题是「内核启动时接管 bootloader 留下的硬件状态」。典型场景：bootloader 打开了几路电源和时钟，内核不能一上来就关掉，得等所有会用到它们的驱动都 probe 完，才知道哪些是真没人要的。

三个规则：

- 第一次尝试触发的时机是 `late_initcall_sync()`（`drivers/base/core.c:1282` 的 `sync_state_resume_initcall()`）
- 之后每当一个 consumer 成功 probe，会再检查一次它的所有 supplier 够不够条件
- ⚠️ **每个设备只会被调一次**，由 `dev->state_synced` 位保证

用 `device_links_supplier_sync_state_pause()` 可以人为压住这个机制，等一批 supplier 都注册完再统一放行。

### §3.4 其余字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `pm` | `const struct dev_pm_ops *` | 现代电源管理回调集 |
| `iommu_ops` | `const struct iommu_ops *` | 这条总线默认的 IOMMU ops |
| `p` | `struct subsys_private *` | 见 §1.0.2 和 §9.3 |
| `lock_key` | `struct lock_class_key` | 给 lockdep 区分不同总线的锁 |
| `need_parent_lock` | `bool` | ⚠️ probe/remove 时要不要连父设备的锁一起拿。少数总线（USB）需要 |

## §4 `struct device_driver`

位置：`include/linux/device/driver.h:96-122`。共 19 个字段，7 个是回调。

### §4.1 标识与匹配

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `const char *` | 驱动名。⚠️ platform 总线在没有 DT 匹配表时就是拿它和设备名字符串比 |
| `bus` | `struct bus_type *` | 挂在哪条总线上。⚠️ 必填，`driver_register()` 会解引用它 |
| `owner` | `struct module *` | 所属模块，用 `module_driver` 系列宏时自动填 |
| `mod_name` | `const char *` | 注释写明是给内建模块用的 |
| `of_match_table` | `const struct of_device_id *` | DT compatible 匹配表。⚠️ 必须以全零项结尾 |
| `acpi_match_table` | `const struct acpi_device_id *` | ACPI 匹配表，本文档集裁掉 |

### §4.2 行为控制

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `suppress_bind_attrs` | `bool` | 注释写明是「禁掉 sysfs 的 bind/unbind」。⚠️ 置上之后用户态无法手工解绑这个驱动 |
| `probe_type` | `enum probe_type` | 同步/异步探测偏好，三个取值见 §4.4 |
| `groups` | `const struct attribute_group **` | 挂在**驱动**目录下的属性 |
| `dev_groups` | `const struct attribute_group **` | ⚠️ 挂在**这个驱动绑定的每个设备**下的属性 |

### §4.3 七个回调

| 回调 | 返回 | 说明 |
| --- | --- | --- |
| `probe(dev)` | `int` | 绑定。返回 `-EPROBE_DEFER` 表示条件不足、稍后重试（两条硬约束见 `01-api.md` §5.1） |
| `remove(dev)` | `int` | 解绑。⚠️ 返回值**被核心忽略**，写错误处理是无效的 |
| `shutdown(dev)` | `void` | 关机 |
| `sync_state(dev)` | `void` | 与 `bus_type.sync_state` 同机制，驱动级 |
| `suspend(dev, state)` / `resume(dev)` | `int` | 遗留电源管理路径 |
| `coredump(dev)` | `void` | 触发设备转储，配合 `devcoredump.c` |

⚠️ **6.1 里 `device_driver.remove` 仍然返回 `int`。** 只有 `platform_driver` 处在改成 `void` 的中途（见 §7.2）。不要以为整个驱动模型都改完了。

### §4.4 `enum probe_type`

三个取值：`PROBE_DEFAULT_STRATEGY`、`PROBE_PREFER_ASYNCHRONOUS`、`PROBE_FORCE_SYNCHRONOUS`（`include/linux/device/driver.h:45-49`）。

⚠️ **默认值不等于同步。** `PROBE_DEFAULT_STRATEGY` 是「听全局设置」，全局设置由内核参数 `driver_async_probe=` 与 `drivers/base/dd.c:63` 的 `async_probe_default` 决定。所以「我没设 `probe_type`，所以我的 probe 一定是同步的」这个推理是错的。

### §4.5 其余字段

`pm`（`const struct dev_pm_ops *`）、`p`（`struct driver_private *`，见 §9.2）。

## §5 `struct class`、`device_type`、`device_attribute`

### §5.1 `struct class`

位置：`include/linux/device/class.h:54-78`。共 15 个字段，7 个回调。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `const char *` | 类名，也是 `/sys/class/<name>/` 的目录名 |
| `owner` | `struct module *` | 所属模块 |
| `class_groups` | `const struct attribute_group **` | 挂在类目录本身上的属性 |
| `dev_groups` | `const struct attribute_group **` | 挂在这个类的每个设备上的属性 |
| `dev_kobj` | `struct kobject *` | 这个类的设备在 `/sys/dev/` 下归到哪（char 还是 block） |
| `dev_uevent(dev, env)` | 回调 | 往 uevent 里加类特有的键值 |
| `devnode(dev, mode)` | 回调 | ⚠️ 决定 `/dev` 下的**名字和权限**，返回一个 `kmalloc` 出来的字符串 |
| `class_release(class)` | 回调 | 类本身销毁 |
| `dev_release(dev)` | 回调 | ⚠️ `struct device` 的第三级 release 兜底（见 §2.2） |
| `shutdown_pre(dev)` | 回调 | 关机前，在总线的 `shutdown()` 之前 |
| `ns_type` | `const struct kobj_ns_type_operations *` | 命名空间类型，网络设备用 |
| `namespace(dev)` | 回调 | 返回这个设备属于哪个命名空间 |
| `get_ownership(dev, uid, gid)` | 回调 | sysfs 文件的属主，容器场景用 |
| `pm` | `const struct dev_pm_ops *` | 类级电源管理 |
| `p` | `struct subsys_private *` | ⚠️ 与 `bus_type.p` 同类型，见 §1.0.2 |

⚠️ **`class.devnode` 和 `device_type.devnode` 原型不一样。** 前者是 `(dev, mode)`，后者是 `(dev, mode, uid, gid)`（`include/linux/device.h:93-94`）。写的时候按错的原型来会直接编译失败，但两个都实现时哪个生效不明显——答案在 `03-internals.md` 的 sysfs 轴。

### §5.2 `struct device_type`

位置：`include/linux/device.h:89-98`。6 个字段：`name`、`groups`、`uevent()`、`devnode()`、`release()`、`pm`。

它的用途是给「同一条总线上的几类设备」再细分。⚠️ 它**不是** `bus_type` 的轻量版——`bus_type` 参与匹配和探测，`device_type` 完全不参与，只影响 sysfs 呈现、uevent 与释放。

### §5.3 `struct device_attribute`

位置：`include/linux/device.h:101-107`。3 个字段：`attr`（内嵌的 `struct attribute`）、`show()`、`store()`。

⚠️ `show()` 写入的 `buf` 大小是一个页。返回值是**写了多少字节**，不是 0。返回负数是错误码。这三条是 sysfs 属性最常见的三个写错点。

## §6 device_link 与 fwnode_link

这一节的两个结构名字只差一个词，但处在**两个完全不同的阶段**：`fwnode_link` 是从固件描述里推断出来的、设备还不一定存在时的依赖；`device_link` 是设备都在了之后的真实依赖。前者会被逐步转化成后者。

### §6.1 `struct dev_links_info`

位置：`include/linux/device.h:370-375`。4 个字段，就是 `dev->links`。

| 字段 | 说明 |
| --- | --- |
| `suppliers` | 我作为 consumer 的那些链接（链到别人的 `s_node`） |
| `consumers` | 我作为 supplier 的那些链接 |
| `defer_sync` | ⚠️ 挂在核心的 `deferred_sync` 全局链表上的节点，等 `sync_state()` 用 |
| `status` | `enum dl_dev_state`，四态见下 |

`enum dl_dev_state`（`include/linux/device.h:340-345`）：`DL_DEV_NO_DRIVER = 0`、`DL_DEV_PROBING`、`DL_DEV_DRIVER_BOUND`、`DL_DEV_UNBINDING`。

⚠️ **这是设备的驱动状态，不是链接的状态。** 链接自己另有一个五态枚举，见 §6.3。同一个 `device_link` 的两端各有自己的 `dl_dev_state`。

### §6.2 `struct device_link`

位置：`include/linux/device.h:671-683`。11 个字段。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `supplier` | `struct device *` | 提供方 |
| `s_node` | `struct list_head` | 挂在 `supplier->links.consumers` 上 |
| `consumer` | `struct device *` | 消费方 |
| `c_node` | `struct list_head` | 挂在 `consumer->links.suppliers` 上 |
| `link_dev` | `struct device` | ⚠️ **内嵌的一个完整设备**，见 §1.0.5 |
| `status` | `enum device_link_state` | 见 §6.3 |
| `flags` | `u32` | 十个 `DL_FLAG_*` 的组合（`01-api.md` §6.1） |
| `rpm_active` | `refcount_t` | 运行时 PM 的引用计数。⚠️ 这是个计数不是布尔，重复添加同一条链接会加它 |
| `kref` | `struct kref` | 重复添加同一条链接的计数 |
| `rm_work` | `struct work_struct` | ⚠️ 删链接是**异步**的，走工作队列 |
| `supplier_preactivated` | `bool` | 注释写明「Owned by consumer probe」 |

⚠️ **`s_node` 和 `c_node` 的挂载方向是交叉的**：`s_node` 挂在 supplier 的 **consumers** 链表上。名字取自「这个节点标识 supplier 一侧」，不是「这个节点挂在 supplier 的链表上」。读遍历代码时这一处最容易反过来。

⚠️ `rm_work` 意味着 `device_link_del()` 返回时链接可能还没真正消失，`link_dev` 也还没释放。任何「删完立刻重建同一条链接」的代码都要考虑这一点。

### §6.3 `enum device_link_state`

位置：`include/linux/device.h:300-307`。六个取值，注意 `DL_STATE_NONE` 是 **-1**：

| 取值 | 值 | 含义（取自 `include/linux/device.h:292-299` 的注释） |
| --- | --- | --- |
| `DL_STATE_NONE` | -1 | 不跟踪驱动的存在——即无状态链接 |
| `DL_STATE_DORMANT` | 0 | 两端的驱动都不在 |
| `DL_STATE_AVAILABLE` | 1 | supplier 的驱动在了，consumer 的还没 |
| `DL_STATE_CONSUMER_PROBE` | 2 | consumer 正在 probe |
| `DL_STATE_ACTIVE` | 3 | 两端驱动都在 |
| `DL_STATE_SUPPLIER_UNBIND` | 4 | supplier 正在解绑 |

⚠️ **`DL_STATE_NONE = -1` 是有意义的**：它把「无状态链接」和「有状态但还没就绪」区分开。用 `if (link->status)` 判断真假会把 `DL_STATE_DORMANT`（0）当假、把 `DL_STATE_NONE`（-1）当真，两边都错。

### §6.4 `struct fwnode_handle` 与 `struct fwnode_link`

`struct fwnode_handle`（`include/linux/fwnode.h:41-48`）6 个字段：

| 字段 | 说明 |
| --- | --- |
| `secondary` | ⚠️ 第二个 fwnode。一个设备可以同时有 DT 节点和 software node，属性查不到时会回落到这里 |
| `ops` | `const struct fwnode_operations *`，见 §10 |
| `dev` | 反向指回设备 |
| `suppliers` / `consumers` | `fwnode_link` 的两条链表 |
| `flags` | `u8`，六个 `FWNODE_FLAG_*`（`include/linux/fwnode.h:34-39`） |

`struct fwnode_link`（`include/linux/fwnode.h:59-65`）5 个字段：`supplier`、`s_hook`、`consumer`、`c_hook`、`flags`。两个 flag 位在 `include/linux/fwnode.h:56-57`：`FWLINK_FLAG_CYCLE`（成环，不要延迟探测）、`FWLINK_FLAG_IGNORE`（连环检测都跳过）。

⚠️ **`fwnode_link` 的链表字段叫 `s_hook`/`c_hook`，`device_link` 的叫 `s_node`/`c_node`。** 两套命名，同一个交叉挂载套路。

六个 `FWNODE_FLAG_*` 里最需要知道的两个：`FWNODE_FLAG_NOT_DEVICE`（这个节点永远不会变成设备，别为它等）和 `FWNODE_FLAG_BEST_EFFORT`（尽力而为模式，配合 `fw_devlink=rpm` 之类的宽松策略）。

## §7 platform 的两个结构

### §7.1 `struct platform_device`

位置：`include/linux/platform_device.h:23-45`。12 个字段。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | `const char *` | 设备名，参与与驱动 `name` 的字符串匹配 |
| `id` | `int` | 实例号 |
| `id_auto` | `bool` | ⚠️ 这个 `id` 是核心自动分配的，注销时要还回去 |
| `dev` | `struct device` | 内嵌的设备本体，见 §1.0.5 |
| `platform_dma_mask` | `u64` | ⚠️ 存放 mask 的**实体**，`dev.dma_mask` 指向它 |
| `dma_parms` | `struct device_dma_parameters` | 同上，实体在这里 |
| `num_resources` | `u32` | 资源条数 |
| `resource` | `struct resource *` | 资源数组，`platform_get_resource()` 查的就是它 |
| `id_entry` | `const struct platform_device_id *` | 匹配上的那个 id 表项 |
| `driver_override` | `const char *` | ⚠️ 注释明确写着「Do not set directly, because core frees it. Use driver_set_override()」 |
| `mfd_cell` | `struct mfd_cell *` | MFD 子设备信息 |
| `archdata` | `struct pdev_archdata` | 体系结构专用；arm64 上是空结构 |

⚠️ **`platform_dma_mask` 与 `dev.dma_mask` 是「实体与指针」的关系。** `struct device` 里的 `dma_mask` 是个 `u64 *`，它需要指向某块实际内存；platform 层就把这块内存放在 `platform_device` 里。所以手工造 `struct device` 时忘记给 `dma_mask` 找个落脚处，是一个典型的空指针来源。

⚠️ `driver_override` 那条注释是内核里少见的、直接写在字段旁边的禁令。直接赋一个字符串字面量给它，注销时核心会去 `kfree` 一个不在堆上的地址。

### §7.2 `struct platform_driver`

位置：`include/linux/platform_device.h:208-236`。10 个字段。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `probe(pdev)` | `int` | 探测 |
| `remove(pdev)` | `int` | ⚠️ 旧式，返回值被忽略 |
| `remove_new(pdev)` | `void` | ⚠️ 新式，**新驱动应该用这个** |
| `shutdown(pdev)` | `void` | 关机 |
| `suspend(pdev, state)` / `resume(pdev)` | `int` | 遗留电源管理 |
| `driver` | `struct device_driver` | 内嵌的驱动本体 |
| `id_table` | `const struct platform_device_id *` | 名字匹配表 |
| `prevent_deferred_probe` | `bool` | 不参与延迟探测 |
| `driver_managed_dma` | `bool` | 驱动自己管 DMA 地址空间（VFIO 类），IOMMU 层据此放行 |

⚠️ **`remove` 与 `remove_new` 并存是一次正在进行的迁移。** `include/linux/platform_device.h:211-218` 的注释交代了原因：`remove()` 的返回值一直被核心忽略，但驱动作者以为返回错误码是有效的错误处理，所以要改成返回 `void`；过渡期新驱动写 `remove_new()`。两个都填会怎样、核心先看哪个，在 `03-internals.md` 里。

⚠️ 这也意味着**「`platform_driver.remove` 返回 `int`」这句话在 6.1 上成立，但不能推广到 `device_driver`**——反过来的推广也不成立（见 §4.3）。

### §7.3 `platform_bus_type` 实际填了哪些字段

`drivers/base/platform.c:1478-1489`，24 个字段里只填了 10 个：`name`、`dev_groups`、`match`、`uevent`、`probe`、`remove`、`shutdown`、`dma_configure`、`dma_cleanup`、`pm`。

没填的里面有几个值得注意：

- `sync_state` 空 → **platform 总线不提供总线级的状态同步**，要靠 `device_driver.sync_state`
- `online` / `offline` 空 → platform 设备的 sysfs 里没有可用的 `online` 文件
- `iommu_ops` 空 → IOMMU 走的是 `dma_configure` 里的固件描述路径，不是总线默认 ops
- `need_parent_lock` 未置 → platform 设备 probe 时**不拿父设备的锁**
- `dev_root` 空 → 但另有一个独立的 `struct device platform_bus`（`drivers/base/platform.c:42`），在 `platform_bus_init()` 里被 `device_register()`（`drivers/base/platform.c:1519`）

⚠️ **`platform_bus` 与 `platform_bus_type` 是两个不同的东西**，名字只差一个后缀。前者是 `/sys/devices/platform` 那个根**设备**，后者是 `/sys/bus/platform` 那条**总线**。前者常被当作父设备用，后者决定匹配规则。

## §8 devres 的三个内部结构

这三个结构不在任何 `include/` 头文件里，只存在于 `drivers/base/devres.c`。驱动看不见它们，但它们的形状决定了托管资源的开销和分组语义。

### §8.1 `struct devres_node`

`drivers/base/devres.c:19-24`，4 个字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `entry` | `struct list_head` | 挂在 `dev->devres_head` 上 |
| `release` | `dr_release_t` | ⚠️ 释放函数。**它同时是「身份标识」**：`devres_find()`/`devres_destroy()` 靠比较这个指针来找资源 |
| `name` | `const char *` | 调试用名字，`CONFIG_DEBUG_DEVRES` 打开时才有意义 |
| `size` | `size_t` | 数据区大小 |

⚠️ **`release` 兼任身份标识这一条是 devres 的核心设计**，也是「两个不同资源用了同一个 release 函数，结果 `devres_find()` 找错」的根源。要区分就得再传 `match` 回调。

### §8.2 `struct devres`

`drivers/base/devres.c:26-36`，2 个字段：`node`（内嵌的 `devres_node`）加一个柔性数组 `data[]`。

数据区带 `__aligned(ARCH_KMALLOC_MINALIGN)`，源码注释（`drivers/base/devres.c:28-34`）解释了原因：有些体系结构要直接 DMA 进 kmalloc 缓存，所以对齐必须和 plain `kmalloc()` 给出的一样。

⚠️ **这个对齐意味着 `devm_kmalloc()` 分配的内存可以安全地做 DMA**，跟裸 `kmalloc()` 一样。这不是巧合，是显式保证。

记账开销：官方文档（`Documentation/driver-api/driver-model/devres.rst:230-234`）写的是 32 位上 16 字节、**64 位上 24 字节**——就是上面那三个指针加一个 `size_t`，按 `unsigned long long` 对齐取整的结果。

### §8.3 `struct devres_group`

`drivers/base/devres.c:38-43`，3 个字段：

| 字段 | 说明 |
| --- | --- |
| `node[2]` | ⚠️ **两个** `devres_node`，一个标组的开头、一个标组的结尾 |
| `id` | 组标识，`devres_open_group()` 传 NULL 时核心用组自己的地址当 id |
| `color` | 遍历时的标记位 |

源码末尾自带一行注释 `/* -- 8 pointers */`（`drivers/base/devres.c:42`），和官方文档里「每个 group 占 8 个指针」的说法一致。

⚠️ **一个组占两个链表节点，不是一个。** 组的实现方式是在链表里插入一对哨兵，组内的资源夹在中间。所以「组」不是一个容器，是一个区间标记——这解释了为什么组可以嵌套、为什么 `devres_release_group()` 能精确释放一段而不动其余。

## §9 三个私有结构

三者的公共点：只在 `drivers/base/base.h` 里，都装 klist 节点，都由核心在注册时分配、注销时释放。

### §9.1 `struct device_private`

`drivers/base/base.h:88-99`，10 个字段。

| 字段 | 说明 |
| --- | --- |
| `klist_children` | 这个设备的子设备表 |
| `knode_parent` | ⚠️ 在**父设备的** `klist_children` 里的节点 |
| `knode_driver` | 在绑定驱动的设备表里的节点 |
| `knode_bus` | 在总线设备表里的节点 |
| `knode_class` | 在类设备表里的节点 |
| `deferred_probe` | 在 `deferred_probe_pending_list` / `_active_list` 里的表项 |
| `async_driver` | 等待异步探测的那个驱动 |
| `deferred_probe_reason` | ⚠️ 延迟原因字符串，`/sys/kernel/debug/devices_deferred` 显示的就是它 |
| `device` | 反向指回 `struct device` |
| `dead:1` | 注释写明：设备正在或已经被移除，为它排的异步事件应当**直接返回不做动作** |

⚠️ **一个设备同时在四条 klist 上**（父、驱动、总线、类）。所以「把设备从系统里摘掉」不是一次链表删除，是四次；漏掉任何一条都会留下悬挂引用。这也是为什么 `device_del()` 的实现那么长。

### §9.2 `struct driver_private`

`drivers/base/base.h:58-64`，5 个字段：`kobj`、`klist_devices`（这个驱动绑定的所有设备）、`knode_bus`（在总线驱动表里的节点）、`mkobj`（`struct module_kobject *`）、`driver`（反向指针）。

⚠️ **「这个驱动绑了哪些设备」这个信息只存在这里**，`struct device_driver` 本体上没有任何字段能回答它。所以 `driver_for_each_device()` 是唯一的合法途径。

### §9.3 `struct subsys_private`

`drivers/base/base.h:40-55`，12 个字段。见 §1.0.2 讲它为什么同时服务 bus 和 class。

| 字段 | 说明 |
| --- | --- |
| `subsys` | `struct kset`，`/sys/bus/<name>` 或 `/sys/class/<name>` 本体 |
| `devices_kset` | `devices` 子目录 |
| `interfaces` | `subsys_interface` 或 `class_interface` 链表 |
| `mutex` | 保护 `interfaces` 与设备遍历 |
| `drivers_kset` | `drivers` 子目录（类不用） |
| `klist_devices` | 这条总线/类上的所有设备 |
| `klist_drivers` | 这条总线上的所有驱动 |
| `bus_notifier` | `blocking_notifier_head`，八个 `BUS_NOTIFY_*` 事件从这里发 |
| `drivers_autoprobe:1` | ⚠️ 对应 sysfs 的 `drivers_autoprobe` 文件，置 0 后新设备不会自动去匹配驱动 |
| `bus` | 反向指针，是总线时非空 |
| `glue_dirs` | 中间层目录的 kset，见下 |
| `class` | 反向指针，是类时非空 |

⚠️ `glue_dirs` 对应的是 sysfs 里那些「凭空多出来的一层目录」。一个有 `class` 但父设备不在 class 目录树里的设备，核心会造一个中间目录把它挂进去，这个目录就归 `glue_dirs` 管，由 `gdp_mutex`（`drivers/base/core.c:3242`）保护。

## §10 `fwnode_operations` 与 software node

### §10.1 `struct fwnode_operations`

`include/linux/fwnode.h:125-169`，22 个回调，按用途分四组：

| 组 | 回调 |
| --- | --- |
| 引用与可用性 | `get`、`put`、`device_is_available`、`device_get_match_data`、`device_dma_supported`、`device_get_dma_attr` |
| 属性读取 | `property_present`、`property_read_int_array`、`property_read_string_array` |
| 拓扑遍历 | `get_name`、`get_name_prefix`、`get_parent`、`get_next_child_node`、`get_named_child_node`、`get_reference_args` |
| 图绑定与资源 | `graph_get_next_endpoint`、`graph_get_remote_endpoint`、`graph_get_port_parent`、`graph_parse_endpoint`、`iomap`、`irq_get`、`add_links` |

⚠️ **调用这些回调必须先过 `fwnode_has_op()`**（`include/linux/fwnode.h:171-172`）。这个宏检查三件事：fwnode 非 NULL 且非 `ERR_PTR`、`ops` 非空、那个具体的回调非空。没有哪个实现填满 22 个，直接解引用就是空指针。

全树的实现（`grep -rn 'struct fwnode_operations [a-z_]* = {' --include=*.c .`）落在 5 处：`drivers/of/property.c:1461`（`of_fwnode_ops`，DT）、`drivers/base/swnode.c:665`（`software_node_ops`，本目录）、`drivers/firmware/efi/sysfb_efi.c:346`、`kernel/irq/irqdomain.c:55`、`drivers/acpi/property.c:1632`（在宏里，展开成多个；ACPI 已裁掉）。

⚠️ **`irqchip_fwnode_ops` 是个几乎全空的实现。** 它存在的目的不是描述属性，而是给中断控制器一个「可以当 fwnode 用」的句柄。所以「凡是 fwnode 就能读属性」这个推理不成立。

### §10.2 `struct swnode`

`drivers/base/swnode.c:16-27`，8 个字段：`kobj`、`fwnode`（内嵌）、`node`（指向 `struct software_node`）、`id`、`child_ids`（`struct ida`）、`entry`、`children`、`parent`。

⚠️ 又一次「内嵌加 `container_of`」：`struct swnode` 内嵌一个 `struct fwnode_handle`。所以拿到 `fwnode` 之后能不能转回 `swnode`，取决于它的 `ops` 是不是 `software_node_ops`。

`struct software_node` 与 `struct property_entry` 是**公开**的（`include/linux/property.h`），`struct swnode` 是私有的。前者是「你要提供的描述」，后者是「核心为它建的运行时对象」。

## §11 关系图

### §11.1 核心抽象与它的实例

本子系统的特殊之处：它自己就是抽象层。`bus_type` / `class` / `device_driver` 这些结构由本目录定义，实例几乎全在别处。

```
              drivers/base/  定义抽象
        ┌──────────────┴──────────────┐
        │  struct bus_type            │  ← include/linux/device/bus.h:84-118
        │  struct class               │  ← include/linux/device/class.h:54-78
        │  struct device_driver       │  ← include/linux/device/driver.h:96-122
        │  struct device              │  ← include/linux/device.h:557-655
        └──────────────┬──────────────┘
                       │ 实例化
   ┌───────────────────┼───────────────────────────┐
   │                   │                           │
本目录内 8 条伪总线   全树各硬件总线            全树各驱动
platform / cpu /      spi / i2c / pci /         数千个
auxiliary / soc /     usb / mmc / …             device_driver
isa / node /          （都有电气层）
memory / container
（都没有电气层）
```

⚠️ **本目录内那 8 条总线一条都没有电气层**（清单和取证见 `01-api.md` §2.4）。它们是「拿总线机制当组织工具」的产物：`node` 与 `memory` 表达 NUMA 与内存热插拔，`container` 表达容器化设备，`soc` 表达片上系统标识。把它们理解成「另一种硬件总线」会一路误解下去。

### §11.2 设备、驱动、总线三者怎么连起来

```
 struct bus_type "platform"
        │ p
        ▼
 struct subsys_private ─── klist_drivers ──► driver_private.knode_bus
        │                                          ▲
        │ klist_devices                             │ p
        ▼                                    struct device_driver
 device_private.knode_bus                          │
        ▲                                          │ klist_devices
        │ p                                        ▼
 struct device ◄─────── knode_driver ───────────────┘
        │
        │ parent                    ┌── links.suppliers ──► device_link.c_node
        ▼                           │
 struct device（父）                 └── links.consumers ──► device_link.s_node
```

三条要点：

- **设备与驱动之间有两个方向的记录**：`dev->driver` 是一个裸指针，反方向靠 `drv->p->klist_devices`。前者可以直接读，后者只能遍历
- **总线不直接指向设备**，中间隔着 `subsys_private`
- **`dev->parent` 与 device_link 是两套并行的边**（见 §1.0.4）

### §11.3 与父总线的关系

本子系统提供的 `platform_bus_type` 就是绝大多数子系统的父总线，所以这里的关系图方向和别的子系统相反：

```
 /sys/devices/platform            ← struct device platform_bus（drivers/base/platform.c:42）
        │  dev.parent
        ├── 4000000.i2c    ─── bus = i2c 控制器所在的 platform_bus_type
        │       │  dev.parent
        │       └── 1-0050  ─── bus = i2c_bus_type   ← 归 i2c 子系统
        └── 4001000.spi    ─── bus = platform_bus_type
                │  dev.parent
                └── spi0.0  ─── bus = spi_bus_type   ← 归 spi 子系统
```

⚠️ **控制器和它下面的设备在两条不同的总线上，只由 `dev.parent` 缝在一起。** 这句话在别的子系统文档里是需要证明的结论；在这里它是本子系统提供的机制本身。`platform_bus_type` 的匹配逻辑（`platform_match()`，`drivers/base/platform.c:1331`）完全不知道子设备的存在。

## §12 核心操作数据流

### §12.1 一个设备是怎么被驱动上的

从「谁调用谁」验证过的次序（详细逐段剖析在 `03-internals.md` Part A）：

```
调用方（DT 扫描 / 总线枚举 / 驱动手工造）
  │
  ├─ device_initialize()              初始化 kobj、mutex、devres_head、links
  │
  └─ device_add()
       ├─ 分配 device_private
       ├─ 建 sysfs 目录、加 dev->groups 的属性
       ├─ 发 KOBJ_ADD uevent        ← ⚠️ 属性必须已经在了（01-api.md §11）
       ├─ 上四条 klist
       └─ bus_probe_device()                                   drivers/base/bus.c:480
            └─ device_initial_probe()                          drivers/base/dd.c:1062
                 └─ __device_attach(dev, true)                 drivers/base/dd.c:985
                 └─ 遍历总线上的驱动
                      └─ __device_attach_driver()               drivers/base/dd.c:906
                           ├─ bus->match(dev, drv)              ← 不匹配就到此为止
                           ├─ device_links_check_suppliers()    ← 供方没就绪 → -EPROBE_DEFER
                           └─ driver_probe_device()             drivers/base/dd.c:809
                                └─ __driver_probe_device()      drivers/base/dd.c:764
                                     └─ really_probe()          drivers/base/dd.c:584
                                          ├─ bus->dma_configure(dev)   ← DMA/IOMMU 在这里定
                                          ├─ devres 分组开
                                          ├─ call_driver_probe()       drivers/base/dd.c:553
                                          │    └─ bus->probe 或 drv->probe
                                          └─ 成功 → 建 sysfs 链接、发 BUS_NOTIFY_BOUND_DRIVER
                                             失败 → devres_release_all()、dma_cleanup
```

⚠️ **`-EPROBE_DEFER` 可以从三个不同的深度返回**：`device_links_check_suppliers()`（供方未就绪）、`drv->probe()` 里（资源拿不到）、`bus->probe()` 里。三者进入延迟队列的路径相同，但 `deferred_probe_reason` 记的字符串不同——这是排查「为什么我的驱动没起来」的第一手线索。

### §12.2 一次托管分配的数据流

```
驱动调 devm_kzalloc(dev, size, gfp)
  │
  ├─ alloc_dr()：kmalloc(sizeof(struct devres) + size)
  │    └─ 填 node.release = devm_kmalloc_release
  │
  └─ devres_add(dev, data)          drivers/base/devres.c:240
       ├─ spin_lock_irqsave(&dev->devres_lock, flags)
       ├─ add_dr() → list_add_tail(&node->entry, &dev->devres_head)
       └─ spin_unlock_irqrestore

驱动 remove 或 probe 失败
  │
  └─ devres_release_all(dev)                drivers/base/devres.c:514
       └─ 从链表尾往头遍历，逐个调 node->release
```

⚠️ **释放是逆序的**（后申请的先释放），这正是「托管资源能替代手写 goto 错误处理」的前提。写在 `probe()` 里的申请顺序就是释放顺序的逆序，不需要额外声明。

⚠️ 但**「逆序」只在同一个设备的 devres 链表内成立**。跨设备（比如父子）没有任何顺序保证，那要靠 device_link。

## §13 全局变量与锁

### §13.1 锁

| 锁 | 位置 | 保护什么 | 类型 |
| --- | --- | --- | --- |
| `dev->mutex` | `struct device` 字段 | 对这个设备的驱动调用（probe/remove/PM） | mutex，经 `device_lock()` 用 |
| `dev->devres_lock` | `struct device` 字段 | 这个设备的 `devres_head` | spinlock；⚠️ 用的是 `spin_lock_irqsave`（`drivers/base/devres.c:245`），即中断上下文里也能加托管资源 |
| `sp->mutex` | `drivers/base/base.h:44` | 一条总线/类的 `interfaces` 与设备遍历 | mutex |
| `deferred_probe_mutex` | `drivers/base/dd.c:54` | 两条延迟探测链表 | mutex |
| `device_links_lock` | `drivers/base/core.c:251` | device_link 的增删 | mutex |
| `device_links_srcu` | `drivers/base/core.c:252` | ⚠️ device_link 的**读**侧，与上面那把是读写分离的两套 | SRCU |
| `fwnode_link_lock` | `drivers/base/core.c:54` | `fwnode_link` 的增删 | mutex |
| `device_hotplug_lock` | `drivers/base/core.c:2286` | 设备上下线 | mutex |
| `gdp_mutex` | `drivers/base/core.c:3242` | glue 目录的创建与销毁 | mutex |

⚠️ **device_link 有两套访问机制**：写侧 `device_links_lock`（mutex），读侧 `device_links_srcu`（SRCU，入口是 `device_links_read_lock()`，`drivers/base/core.c:264`）。用错一侧的表现是死锁而不是数据竞争。⚠️ `drivers/base/core.c:302` 和 `:314` 还各有一份 `device_links_read_lock()` 的**另一个实现**（`CONFIG_SRCU` 关闭时的版本），读代码时注意别看串。

### §13.2 全局状态

| 变量 | 位置 | 说明 |
| --- | --- | --- |
| `deferred_probe_pending_list` | `drivers/base/dd.c:55` | 等着重试的设备 |
| `deferred_probe_active_list` | `drivers/base/dd.c:56` | ⚠️ **正在这一轮被重试的设备**，与上面是两条独立链表 |
| `deferred_trigger_count` | `drivers/base/dd.c:57` | `atomic_t`，重试轮次计数 |
| `initcalls_done` | `drivers/base/dd.c:58` | initcall 阶段是否结束 |
| `async_probe_default` | `drivers/base/dd.c:63` | 异步探测的全局默认，见 §4.4 |
| `defer_all_probes` | `drivers/base/dd.c:70` | 把所有探测都推迟掉，PM 恢复期用 |
| `driver_deferred_probe_enable` | `drivers/base/dd.c:156` | 延迟探测机制的总开关 |
| `driver_deferred_probe_timeout` | `drivers/base/dd.c:260` / `:262` | ⚠️ **两处定义**，走 `#ifdef` 分支；一支默认 10 秒，一支默认 0 |
| `probe_count` | `drivers/base/dd.c:512` | `atomic_t`，正在进行的探测数 |
| `probe_waitqueue` | `drivers/base/dd.c:513` | `wait_for_device_probe()` 等的就是它 |
| `deferred_sync` | `drivers/base/core.c:52` | 等 `sync_state()` 的设备链表 |
| `defer_sync_state_count` | `drivers/base/core.c:53` | ⚠️ 初值是 **1** 不是 0——`sync_state` 默认就是压住的，要靠 initcall 放行 |
| `fw_devlink_drv_reg_done` | `drivers/base/core.c:57` | 驱动注册阶段是否结束 |
| `fw_devlink_best_effort` | `drivers/base/core.c:58` | 尽力而为模式 |
| `fw_devlink_strict` | `drivers/base/core.c:1740` | 严格模式，内核参数 `fw_devlink.strict=` |
| `system_kset` | `drivers/base/bus.c:25` | `/sys/devices/system` |
| `bus_kset` | `drivers/base/bus.c:181` | `/sys/bus` |
| `class_kset` | `drivers/base/class.c:85` | `/sys/class` |
| `log_devres` | `drivers/base/devres.c:53` | devres 日志开关，`CONFIG_DEBUG_DEVRES` 下可写 |
| `swnode_root_ids` | `drivers/base/swnode.c:32` | software node 根节点的 ida |
| `swnode_kset` | `drivers/base/swnode.c:33` | `/sys/kernel/software_nodes` |

⚠️ **`defer_sync_state_count` 初值为 1 是一个容易读反的设计**。它是个「暂停计数」，非 0 表示暂停。所以启动早期 `sync_state()` 一定不会被调，直到 `sync_state_resume_initcall()`（`drivers/base/core.c:1282`）把它减下去。

## §14 命名约定速查

| 形态 | 含义 | 例 |
| --- | --- | --- |
| `device_*` | 操作一个 `struct device` | `device_add`、`device_del`、`device_lock` |
| `dev_*` | 也操作 `struct device`，但偏「读取属性/打印」 | `dev_name`、`dev_get_drvdata`、`dev_err` |
| `driver_*` | 操作一个 `struct device_driver` | `driver_register`、`driver_attach` |
| `bus_*` / `class_*` | 操作一条总线 / 一个类 | `bus_register`、`class_create` |
| `subsys_*` | ⚠️ **两义**：`subsys_system_register()` 是「注册一种特殊总线」，`subsys_private` 是「bus 与 class 的公共实现」 | — |
| `platform_*` | platform 总线专用 | `platform_get_resource` |
| `devm_*` | 托管版本，与设备生命周期绑定 | `devm_kzalloc`、`devm_ioremap` |
| `devres_*` | devres 机制本身的操作 | `devres_add`、`devres_open_group` |
| `device_link_*` | device_link 操作 | `device_link_add` |
| `fwnode_*` | 通过 fwnode 抽象读固件描述 | `fwnode_property_read_u32` |
| `device_property_*` | 同上，但入口参数是 `struct device *` | `device_property_read_u32` |
| `software_node_*` | software node 的注册与查找 | `software_node_register` |
| `__*` 前缀 | 已持锁、或跳过某项检查的内部版本 | `__device_attach`、`__driver_probe_device` |
| `*_ns` 后缀 | 带命名空间参数的版本 | `class_create_file_ns` |
| `to_*` | `container_of` 包装，从内层拿外层 | `to_platform_device`、`to_subsys_private` |

⚠️ **`fwnode_property_*` 与 `device_property_*` 是同一套读取逻辑的两个入口**，差别只在第一个参数。混用不会出错，但一个文件里两种都写会让「这段代码作用在谁身上」变得难看。

⚠️ **`device_*` 与 `dev_*` 的分界不严格。** 不要试图从前缀推断行为——`device_lock()` 和 `dev_name()` 都是操作同一个结构，只是历史上分了两批命名。判据只有一条：去头文件看签名。
