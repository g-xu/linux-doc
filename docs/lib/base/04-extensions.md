---
title: 扩展机制
---

# base 的子框架

## §0 这一篇的组织方式

⚠️ 这一篇的组织轴是「一节一个独立框架，互不调用」。`drivers/base/` 除了 `03-internals.md` 讲的驱动模型核心之外，还塞了十来个各自独立的小框架。它们的共同点只有一个：都住在同一个目录、都建立在驱动模型之上。彼此之间几乎不互相调用——唯一的例外是 component 用了 devres 的分组（§1.3）。

所以：这一篇的任何一节都可以单独读，不需要按顺序。

### §0.1 分诊表：哪几个在 arm64 上真会碰到

编译条件一律取自 `drivers/base/Makefile`，行号见表。

| 框架 | 源文件 | 编译条件 | arm64 上 | 本篇 |
| --- | --- | --- | --- | --- |
| component（聚合驱动） | `component.c`（824 行） | ⚠️ 无条件 `obj-y`（`drivers/base/Makefile:4`） | ⚠️ 高频：DRM 显示驱动几乎都用 | §1 |
| auxiliary bus（辅助总线） | `auxiliary.c`（419 行） | `CONFIG_AUXILIARY_BUS`（`:10`），13 处 `select` | 看装了哪些驱动 | §2 |
| software node（软件节点） | `swnode.c`（1177 行） | 无条件 `obj-y`（`:9`） | ⚠️ 有：无 DT 时的属性来源 | §3 |
| soc bus（SoC 标识） | `soc.c`（265 行） | `CONFIG_SOC_BUS`（`:22`），33 处 `select` | ⚠️ 高频：`soc_device_match()` 是 SoC 勘误的主要手段 | §4 |
| devcoredump（崩溃转储） | `devcoredump.c`（459 行） | `CONFIG_DEV_COREDUMP`（`:24`） | 看驱动，24 个文件在用 | §5 |
| syscore ops | `syscore.c`（128 行） | 无条件 `obj-y`（`:4`） | ⚠️ 有：101 个文件在用 | §6 |
| platform MSI | `platform-msi.c`（351 行） | `CONFIG_GENERIC_MSI_IRQ_DOMAIN`（`:25`） | 有：8 个文件（含 arm-smmu-v3、mbigen） | §7 |
| attribute container / transport class | `attribute_container.c`（548 行）+ `transport_class.c`（299 行） | 无条件 `obj-y`（`:7`），但只有 SCSI/FC/SAS 在用 | ⚠️ 除非做 SCSI，否则碰不到 | §8 |
| class_compat（兼容类） | `class.c:491-576` | 无条件（在 `class.c` 里面） | 只有 3 个文件在用 | §9 |
| isa bus | `isa.c`（182 行） | `CONFIG_ISA_BUS_API`（`:13`） | ⚠️ 碰不到：`select` 它的全是 x86 ISA 板卡 | §10 |
| kobj_map（设备号映射） | `map.c`（154 行） | 无条件 `obj-y`（`:6`） | 间接会碰到（字符/块设备注册） | §11 |
| devtmpfs | `devtmpfs.c` | `CONFIG_DEVTMPFS`（`:11`） | 有，但已裁掉 | 不在本篇 |
| cpu / node / memory / container 伪总线 | `cpu.c` / `node.c` / `memory.c` / `container.c` | 见 `03-internals.md` §C3 | 有 | 不在本篇（归 mm / CPU 拓扑） |

⚠️ component 是无条件编译的，没有 `CONFIG_COMPONENT` 这个开关。这一点容易想错——它长得像一个可选的辅助框架，实际上永远在内核里。`attribute_container.c` / `transport_class.c` 同理：明明只有 SCSI 一家在用，却是 `obj-y`（`drivers/base/Makefile:7`），谁都关不掉。

### §0.2 每个框架挂在 `03-internals.md` 时间线的哪一点

| 框架 | 挂载点 |
| --- | --- |
| component | 组件侧在 `probe()` 里调 `component_add()`；聚合侧在 `probe()` 里调 `component_master_add_with_match()`。⚠️ 两者都在 §A7 的 `probe()` 内部 |
| auxiliary bus | 父设备的 `probe()` 里造 auxiliary device → 走一整套 §A5 到 §A8 |
| software node | 在 `device_add()`（§A5）之前挂到 `dev->fwnode`，或者用 `device_add_software_node()` 之后挂 |
| soc bus | `soc_device_register()` 建一个设备，走 §A5；`soc_device_match()` 是纯查询，任何时候都能调 |
| devcoredump | 任何时候，通常在驱动检测到硬件挂死时 |
| syscore ops | ⚠️ 完全不在这条时间线上：它绕过驱动模型，直接挂在系统级挂起/恢复上 |
| platform MSI | platform 设备的 `probe()` 里 |
| attribute container | `attribute_container_register()` 通常在模块初始化；设备侧由 transport class 在 §A5/§A11 前后触发 |
| class_compat | 模块初始化时建，`class_compat_create_link()` 在设备注册之后 |
| kobj_map | ⚠️ 不挂在这条时间线上：它服务的是 `cdev_add()`，和 `device_add()` 是两件事（§11） |

## §1 component：聚合驱动

### §1.1 它解决什么问题

源码的 `DOC: overview`（`drivers/base/component.c:13-37`）说得很清楚：一个逻辑设备（典型是 DRM 显示驱动）的功能散在 SoC 的多个硬件块上（扫描引擎、混合块、各种输出的编码器）。这些块各有自己的 DT 节点、各自被 platform 总线绑上驱动，但只有全部就位之后，那个逻辑设备才能工作。

⚠️ 注释同时声明了它不解决什么（`:27-28`）：「The component helper also doesn't solve runtime dependencies, e.g. for system suspend and resume operations」，并指向 device link。所以 component 和 device_link 是两件不同的事，不能互相替代。

### §1.2 三个角色与三个结构

| 角色 | 结构 | 位置 |
| --- | --- | --- |
| 聚合方（aggregate driver） | `struct aggregate_device` | `drivers/base/component.c:56-63` |
| 组件（component） | `struct component` | `drivers/base/component.c:65-73` |
| 匹配清单 | `struct component_match` + `struct component_match_array` | `:50-54` + `:41-48` |

两组回调：

- `struct component_master_ops`：聚合方实现 `bind()` / `unbind()`
- `struct component_ops`：组件实现 `bind()` / `unbind()`

⚠️ `aggregate_device` 里的 `parent` 是聚合方那个设备，而不是组件的父设备。聚合方本身通常也是一个 platform 设备。

### §1.3 收敛机制：`try_to_bring_up_aggregate_device()`（`drivers/base/component.c:227-260`）

每次有组件注册（`component_add()`）或聚合方注册（`component_master_add_with_match()`）时都会试一次。逻辑五步：

1. `find_components(adev)`（`drivers/base/component.c:173`）返回非 0（有组件还没到）→ `dev_dbg("master has incomplete components\n")`（`:235`），返回 0（不是错误）
2. 传进来的组件不属于这个聚合方（`:239`）→ 也返回 0
3. ⚠️ `devres_open_group(adev->parent, adev, GFP_KERNEL)`（`:245`）—— 开一个 devres 组，id 是 `adev` 自己；失败返回 `-ENOMEM`（`:246`）
4. `adev->ops->bind(adev->parent)`（`:249`）—— 聚合方的 bind
5. 失败 → `devres_release_group(adev->parent, NULL)`（`:251`），且 `ret != -EPROBE_DEFER` 时打 `dev_info("adev bind failed: %d\n")`（`:252-253`）；成功 → `devres_close_group()`（`:257`）并置 `adev->bound = true`（`:258`），返回 1

⚠️ 这里是 component 与 devres 分组机制的唯一交汇点（分组机制见 `03-internals.md` §B2）。目的是：聚合方 `bind()` 里申请的所有托管资源被圈进一个组，`take_down_aggregate_device()`（`:278-285`）时用 `devres_release_group(adev->parent, adev)` 精确释放这一组，不动聚合方 `probe()` 里申请的其他资源。

⚠️ 注意第 3 步和第 5 步的 id 不一样：`devres_open_group()` 传的是 `adev`，`devres_release_group()`/`devres_close_group()` 在失败/成功路径传的是 `NULL`（表示最近一个开放的组），而 `take_down_aggregate_device()` 传的是 `adev`。这是 devres 分组 API 的正常用法，但读起来容易以为是 bug。

⚠️ 返回值 0 / 1 / 负数三种语义都不一样：0 = 「还没凑齐，不算失败」，1 = 「凑齐并绑上了」，负数 = 真失败。`try_to_bring_up_masters()`（`:262-276`）在遇到非 0 时就 `break`，所以 1 和负数都会终止遍历。

### §1.4 组件侧与聚合侧的 API

| API | 说明 | 备注 |
| --- | --- | --- |
| `component_add(dev, ops)` | 注册一个组件 | `drivers/base/component.c:787`；全树 98 个文件、106 个调用点 |
| `component_add_typed(dev, ops, subcomponent)` | 带子类型编号的注册 | `:762`；同一个设备提供多个组件时用 |
| `component_del(dev, ops)` | 注销组件 | `:802` |
| `component_match_add_release(...)` | 往匹配清单里加一项，带释放回调 | `:445` |
| `component_match_add_typed(...)` | 加一项，指定子类型 | `:472` |
| `component_master_add_with_match(dev, ops, match)` | 注册聚合方 | `:512`；全树 30 个文件、35 个调用点 |
| `component_master_del(dev, ops)` | 注销聚合方 | `:557` |
| `component_bind_all(master, data)` | 在聚合方 `bind()` 里逐个调组件的 `bind()` | `:677` |
| `component_unbind_all(master, data)` | 反向 | `:594` |
| `component_compare_of` / `component_compare_dev` / `component_compare_dev_name` | 三个现成的比较函数 | `:296` / `:323` / `:338` |
| `component_release_of` | 配套的释放函数（`of_node_put`） | `:309` |

重跑上面两个计数：

```
grep -rlw component_add --include=*.c . | wc -l
grep -rlw component_master_add_with_match --include=*.c . | wc -l
```

⚠️ `component_add` 的使用者（98 个文件）远多于 `component_master_add_with_match`（30 个），因为一个聚合方通常要收好几个组件。

⚠️ `component_match_add()` 是宏而不是函数（包装 `component_match_add_release()`），所以在导出符号表里查不到它。

### §1.5 调试

`core_initcall(component_debug_init)`（`drivers/base/component.c:111-118`）建 `/sys/kernel/debug/device_component/`。每个聚合方一个文件，内容是它的匹配清单和每一项的就位状态。⚠️ 「聚合驱动一直不 bind」这个症状，第一步就是看这个目录，别去猜哪个组件没到。详见 `07-debug.md`。

## §2 auxiliary bus：把一个大设备切成几块

### §2.1 它解决什么问题

源码 `DOC: PURPOSE`（`drivers/base/auxiliary.c:20-31`）列了三种场景：核心设备（PCI 之类）的功能对单个驱动来说太复杂（Sound Open Firmware）；多个设备实现同一组功能的交集（网卡 + RDMA）；驱动想把一个接口导出给另一个子系统去驱动（SIOV 的 VF 管理）。

⚠️ 和 component 的关系是反向的：component 是「多个设备凑成一个逻辑设备」，auxiliary bus 是「一个设备拆成多个逻辑设备」。两者都不涉及电气层。

### §2.2 匹配规则

`auxiliary_match()`（`drivers/base/auxiliary.c:180-186`）只有一句实质逻辑：`!!auxiliary_match_id(auxdrv->id_table, auxdev)`。

`auxiliary_match_id()`（`:161-178`）按名字前缀匹配。⚠️ auxiliary 驱动的名字是 `<模块名>.<驱动名>` 的形式（`__auxiliary_driver_register()` 在 `:386-389` 用 `kasprintf()` 拼出来），驱动的 `id_table` 里写的就是这种带点的字符串（源码注释里的例子在 `:140`：`{ .name = "foo_mod.foo_dev" }`）。

`auxiliary_bus_type`（`drivers/base/auxiliary.c:247-256`）填的字段：`name = "auxiliary"`、`match`、`uevent`、`probe`、`remove`、`shutdown`。

⚠️ `auxiliary_bus_probe()`（`:204`）调驱动的 `probe` 时会把匹配到的 `auxiliary_device_id *` 一起传进去（`:216` 那一行把 `auxiliary_match_id()` 又跑了一遍作为第二个实参），这一点和 platform 不同（platform 驱动得自己再查一次 `of_device_get_match_data()`）。

⚠️ `__auxiliary_driver_register()` 里有两个 `WARN_ON`（`:382`）：`!auxdrv->probe` 和 `!auxdrv->id_table` 各一个——auxiliary 驱动这两项都是必填，不像 platform 驱动可以只靠 `.driver.of_match_table`。

### §2.3 两段式注册

⚠️ `auxiliary_device_init()`（`:274`）和 `__auxiliary_device_add()`（`:313`）必须成对且分开调，中间那一步是调用者设置 `auxdev->name` 和 `auxdev->id`。这个两段式和 `device_initialize()` + `device_add()` 是同一个套路（`03-internals.md` §A4/§A5）。

`auxiliary_bus_init()`（`:416-419`）在 `driver_init()` 里被调（`03-internals.md` §A1 第二批），⚠️ `WARN_ON(bus_register(&auxiliary_bus_type))` —— 注册失败只告警，不 panic。

### §2.4 使用统计

| 口径 | 数字 | 重跑 |
| --- | --- | --- |
| `struct auxiliary_driver` 实例 | 16 | `grep -rn 'struct auxiliary_driver [a-zA-Z_]* = {' --include=*.c . \| wc -l` |
| 注册接口的使用文件 | 22 | `grep -rlw '__auxiliary_driver_register\|auxiliary_driver_register\|module_auxiliary_driver' --include=*.c . \| wc -l` |
| `select AUXILIARY_BUS` 的 Kconfig 项 | 13 | `grep -rn 'select AUXILIARY_BUS' --include=Kconfig* . \| wc -l` |

⚠️ 实例数（16）小于使用文件数（22），因为有的文件只是调注册接口而结构定义在别处，还有 `module_auxiliary_driver()` 宏展开的情况。这两个数字口径不同，别混着引用。

## §3 software node：没有 DT 时的属性来源

### §3.1 它是 `fwnode_operations` 的第二个实现

`03-internals.md` 和 `02-datastructures.md` §10 讲过 `fwnode_operations` 全树只有 5 个实现，其中 DT 的 `of_fwnode_ops`（`drivers/of/property.c:1461`）和 software node 的 `software_node_ops`（`drivers/base/swnode.c:665`）是唯二完整的。

⚠️ software node 存在的意义是：让不走 DT 的设备也能用统一的 `device_property_read_*()` 系列 API。属性以 `struct property_entry` 数组的形式写死在 C 代码里。

### §3.2 三档接口

| 档 | API | 生命周期 |
| --- | --- | --- |
| 裸节点 | `software_node_register()`（`drivers/base/swnode.c:949`）/ `software_node_unregister()`（`:967`） | 调用者管 |
| 节点组 | `software_node_register_node_group()`（`:896`）/ `..._unregister_node_group()`（`:929`） | 整组一起 |
| 挂到设备上 | `device_add_software_node()`（`:1026`）/ `device_remove_software_node()`（`:1067`） | 跟设备 |
| 托管 | `device_create_managed_software_node()`（`:1099`） | ⚠️ 跟设备自动释放 |

⚠️ `device_create_managed_software_node()` 是唯一自动清理的那档，其余三档漏了 unregister 就是泄漏。它内部走的是 `fwnode_create_software_node()`（`:978`，在 `:1109` 被调）而不是 `software_node_register()`。

`software_node_register_nodes()`（`:835`）/ `..._unregister_nodes()`（`:874`）是数组版本，和「节点组」不是一回事：数组版本接的是 `struct software_node` 数组（以全零项结尾），组版本接的是指向 `struct software_node` 的指针数组。⚠️ 这两对名字只差一个 `_group`，用错会编译报错但错误信息不直观。

`property_entries_dup()`（`:311`）/ `property_entries_free()`（`:349`）用来深拷贝属性数组——⚠️ 注册节点时属性数组不会被拷贝，调用者必须保证它的生命周期长于节点，除非自己先 dup 一份。

`software_node_find_by_name()`（`:696`）按名字在某个父节点下找子节点。`postcore_initcall(software_node_init)`（`drivers/base/swnode.c:1163-1170`）建 `swnode_kset`。

### §3.3 使用统计

| 口径 | 数字 | 重跑 |
| --- | --- | --- |
| 注册裸节点/组的文件 | 9 | `grep -rlwE 'software_node_register\|software_node_register_nodes\|software_node_register_node_group' --include=*.c . \| wc -l` |
| `device_add_software_node()` 的文件 | 15 | `grep -rlw device_add_software_node --include=*.c . \| wc -l` |

⚠️ 数量不大，但集中在 USB Type-C、Intel 平台驱动、部分 I2C/SPI 板级代码里。arm64 上如果全靠 DT，通常碰不到；有 EC/固件描述缺失的板子会碰到。

## §4 soc bus：SoC 型号与勘误匹配

### §4.1 两种用法，一读一写

| 方向 | API | 位置 |
| --- | --- | --- |
| 写（SoC 支持代码注册自己的身份） | `soc_device_register(soc_dev_attr)` / `soc_device_unregister()` | `drivers/base/soc.c:114` / `:172` |
| 读（驱动查「我跑在哪个 SoC 上」） | `soc_device_match(matches)` | `drivers/base/soc.c:241` |
| 取出底层 device | `soc_device_to_device(soc_dev)` | `drivers/base/soc.c:40` |

⚠️ 读的使用者（54 个文件）是写的（26 个文件）的两倍多，说明这个框架主要是被当「硬件勘误开关」用的，不是当「设备枚举」用的。重跑：

```
grep -rlw soc_device_match --include=*.c . | wc -l
grep -rlw soc_device_register --include=*.c . | wc -l
```

### §4.2 一处需要注意的早期注册路径

`soc_device_register()`（`drivers/base/soc.c:114-168`）开头判 `!soc_bus_type.p`（`:120`）：总线还没注册时，把这次的 attr 暂存到 `early_soc_dev_attr`（`:112`、`:123`）然后返回 `NULL`（`:124`）。等 `core_initcall(soc_bus_register)`（`drivers/base/soc.c:179-192`）跑到时再补注册（`:187-188`）。

⚠️ 所以 `soc_device_register()` 可以在 `core_initcall` 之前调，但只能调一次——第二次早期调用会在 `:121-122` 撞上 `early_soc_dev_attr` 已非空而返回 `-EBUSY`。`early_soc_dev_attr` 是单个指针不是链表。

⚠️ 早期路径的返回值是 `NULL` 而不是错误指针，调用者用 `IS_ERR()` 判断会认为成功、随后解引用就是空指针。这条路径上真正的成功/失败要等到 `soc_bus_register()`。

⚠️ `soc_device_unregister()`（`:172-176`）里有一句 `early_soc_dev_attr = NULL;`（`:175`），看起来和「注销某个特定设备」无关——它是为了让早期暂存槽在注销后重新可用。注销任何一个 soc device 都会清掉这个全局槽。

⚠️ `soc_device_match()` 的匹配是字符串通配（`soc_device_match_attr()` `drivers/base/soc.c:194`），比对 `machine` / `family` / `revision` / `soc_id` 四个字段。写勘误代码时最常见的坑是只填了 `soc_id` 却期望 `revision` 也参与匹配，结果所有版本都命中。

## §5 devcoredump：驱动崩溃转储

三个入口，区别只在数据怎么给：

| API | 数据形式 | 位置 |
| --- | --- | --- |
| `dev_coredumpv(dev, data, datalen, gfp)` | 一块 vmalloc 的内存 | `drivers/base/devcoredump.c:273` |
| `dev_coredumpm(dev, owner, data, datalen, gfp, read_cb, free_cb)` | 自定义读回调 | `:340` |
| `dev_coredumpsg(dev, table, datalen, gfp)` | scatterlist | `:440` |

机制：每次转储建一个 `devcd_entry`（`:24`）里的 `struct device`，注册到 `devcd_class`（`:16`），用户态从 `/sys/class/devcoredump/devcdN/data` 读。

三条要点：

- ⚠️ `DEVCD_TIMEOUT` 是 `HZ * 60 * 5`（`drivers/base/devcoredump.c:22`），即 5 分钟。`devcd_del()`（`:104`）在延迟工作里到时自动删除（`schedule_delayed_work(&devcd->del_wk, DEVCD_TIMEOUT)` 在 `:388`）。不及时取走就没了
- ⚠️ `devcd_disabled`（`:19`）可以从 `/sys/class/devcoredump/disabled` 写 1 关掉，注释（`:64-65`）说明写它会同步销毁现有的转储
- ⚠️ 往 `data` 文件里写任何东西都会让这次转储的定时器立刻到期（`devcd_data_write()` `:130`）—— 这是「我取完了」的信号机制，不是写数据。注释（`:57-63`）把这条路径和 `disabled` 那条路径的竞争处理讲清了：前者用 `if (cancel_delayed_work()) { schedule_delayed_work() }` 防止已经触发过还再次装填

使用统计：24 个文件在用（`grep -rlwE 'dev_coredumpv|dev_coredumpm|dev_coredumpsg' --include=*.c . | wc -l`），主要是 WiFi、GPU、remoteproc 这类固件驱动。

## §6 syscore ops：绕过驱动模型的那一层

五个函数：`register_syscore_ops()`（`drivers/base/syscore.c:21-27`）、`unregister_syscore_ops()`（`:33-39`）、`syscore_suspend()`（`:47`）、`syscore_resume()`（`:91`）、`syscore_shutdown()`（`:114`）。⚠️ 只有前四个 `EXPORT_SYMBOL`，`syscore_shutdown()` 由 `kernel/reboot.c` 内部调，模块拿不到。

⚠️ 这是本目录里唯一一个和 `struct device` 完全无关的框架。它就是一条 `syscore_ops_list` 链表加一把 `syscore_ops_lock` 互斥锁，注册进去的 `suspend`/`resume`/`shutdown` 回调在系统级的挂起/恢复点被调。

⚠️ `syscore_suspend()` 的执行环境注释（`drivers/base/syscore.c:45`）写明：「This function is executed with one CPU on-line and disabled interrupts.」 也就是回调里不能睡、不能等其他 CPU、不能做任何需要中断的事。这是整个内核里约束最紧的回调之一。

⚠️ `syscore_suspend()` / `syscore_resume()` 在 `#ifdef CONFIG_PM_SLEEP`（`drivers/base/syscore.c:41`）里面，`register_syscore_ops()` 却在外面。所以关掉 `CONFIG_PM_SLEEP` 时注册仍然合法，只是 `suspend`/`resume` 回调永远不会被调——只剩 `shutdown` 有效。

使用统计：101 个文件（`grep -rlw register_syscore_ops --include=*.c . | wc -l`），大量在 `arch/` 和中断控制器/时钟驱动里。⚠️ arm64 上的 GIC、架构定时器都用它，所以它虽然不起眼但一定会走到。

## §7 platform MSI

三个导出的 API：`platform_msi_create_irq_domain()`（`drivers/base/platform-msi.c:122`）、`platform_msi_domain_alloc_irqs()`（`:207`）、`platform_msi_domain_free_irqs()`（`:228`）。

⚠️ 同文件里还有一批声明在 `include/linux/msi.h` 但不 `EXPORT_SYMBOL` 的函数——`platform_msi_get_host_data()`（`drivers/base/platform-msi.c:243`）、`__platform_msi_create_device_domain()`（`:268`）、`platform_msi_device_domain_free()`（`:321`）、`platform_msi_device_domain_alloc()`（`:344`）。它们服务的是「再往上叠一层中断域」：kerneldoc（`:264-266`）说「This is for interrupt domains which stack on a platform-msi domain created by `platform_msi_create_irq_domain()`」。

⚠️ 这一组的两个使用者里有一个是 arm64 的：`drivers/irqchip/irq-mbigen.c`（海思 mbigen，在 `:274` 和 `:328` 调 `platform_msi_create_device_domain()`），另一个是 `drivers/irqchip/irq-mvebu-icu.c`（Marvell，arm64/arm 都有）。所以这条「不导出」的路径在 arm64 上是真会走到的——它只是不给模块用，因为使用者都是 irqchip 这种编进内核的东西。

⚠️ `platform_msi_create_device_domain()` 是宏而不是函数（`include/linux/msi.h:458-459`，展开成 `__platform_msi_create_device_domain(..., false, ...)`），另有 `platform_msi_create_device_tree_domain()`（`:460-461`，`is_tree` 传 `true`）。查符号表找不到前者。

⚠️ 它的作用是让「不是 PCI 但需要 MSI」的 platform 设备也能用 MSI 中断域。arm64 上的典型使用者是 `arm-smmu-v3`（它用 MSI 来收命令队列完成通知）和一些 mailbox/DMA 控制器。

使用统计：8 个文件（`grep -rlw platform_msi_create_irq_domain --include=*.c . | wc -l`）。

⚠️ 这一节和 `iommu` 那套文档是上下游关系：SMMU 自己既是 MSI 的消费者（用 platform MSI 收通知）又是 MSI 地址翻译的提供者。两个角色容易混。

## §8 attribute container 与 transport class

### §8.1 先说清它们值不值得读

⚠️ 除非你在做 SCSI/FC/SAS，这两个框架可以完全跳过。使用统计：

| 口径 | 数字 | 重跑 |
| --- | --- | --- |
| `attribute_container_register()` 的文件 | 3 | `grep -rlw attribute_container_register --include=*.c . \| wc -l` |
| `transport_class_register()` 的文件 | 8 | `grep -rlw transport_class_register --include=*.c . \| wc -l` |

847 行代码（548 + 299）、11 个文件在用——这是本目录里投入产出比最差的一块。

### §8.2 它们干什么

attribute container：一个「按条件给设备挂一组属性」的机制。`attribute_container_register()`（`drivers/base/attribute_container.c:73`）注册一个容器，容器带一个 `match()` 判据；有设备出现时匹配上的容器会给它建一个 class device 并挂上属性。反向是 `attribute_container_unregister()`（`:93`），另有两个查询接口 `attribute_container_classdev_to_container()`（`:54`）和 `attribute_container_find_class_device()`（`:530`）。

transport class：建在 attribute container 之上的一层，把「传输层属性」的生命周期拆成五段——`transport_setup_device()`（`drivers/base/transport_class.c:148`）、`transport_add_device()`（`:194`）、`transport_configure_device()`（`:227`）、`transport_remove_device()`（`:266`）、`transport_destroy_device()`（`:295`）。类本身的注册是 `transport_class_register()`（`:51`）/ `transport_class_unregister()`（`:65`）。

⚠️ 两个 FIXME 都在同一个问题上（`drivers/base/attribute_container.c:179-181` 与 `:540`）：`klist_for_each_entry` 这个宏（就定义在 `:182-186` 那个 FIXME 底下）不能中途 `break`，必须先 `klist_iter_exit()`。`:537-543` 那处是老实照做的写法。这是 klist API 的限制，不是这个框架自己的问题——但它把宏和警告一起留在了这个文件里，而不是修掉。

⚠️ `anon_transport_class_register()`（`drivers/base/transport_class.c:91`）/ `anon_transport_class_unregister()`（`:113`）是「匿名」变体：不建 sysfs 目录，只走生命周期回调。名字里的 anon 不是「匿名结构体」的意思。

## §9 class_compat：一个自称临时的兼容层

四个 API 都在 `drivers/base/class.c`：`class_compat_register()`（`:502-516`）、`class_compat_unregister()`（`:522`）、`class_compat_create_link()`（`:536`）、`class_compat_remove_link()`（`:569`）。

⚠️ 源码注释（`drivers/base/class.c:499-500`）自称「a temporary user-space compatibility workaround」。它的作用是在 `/sys/class/<name>/` 下建一堆指向真实设备的符号链接，让还在依赖老 sysfs 布局的用户态工具继续能用。

⚠️ `struct class_compat`（`drivers/base/class.c:491-493`）里只有一个 `struct kobject *kobj`，它不是 `struct class`。这是本目录里最容易看错的一处命名——名字带 `class` 但和类机制没有任何数据结构上的关系，`class_compat_register()` 干的事就是 `kobject_create_and_add(name, &class_kset->kobj)`（`:509`），也就是在 `/sys/class/` 下凭空建一个空目录。

⚠️ 失败时返回 `NULL` 而不是错误指针（`:508`、`:512`），和内核里大多数 `*_register()` 返回 `ERR_PTR()` 的惯例相反。

使用统计：3 个文件（`grep -rlw class_compat_register --include=*.c . | wc -l`）。⚠️ arm64 上新写的驱动没有任何理由用它。

## §10 isa bus：确认碰不到的那一个

`isa_register_driver()`（`drivers/base/isa.c:113`）/ `isa_unregister_driver()`（`:100`），`isa_bus_type` 在 `:85`，`postcore_initcall(isa_bus_init)` 在 `:182`。

⚠️ `isa_register_driver()` 的使用者有 29 个文件，但一个都不在 arm64 上跑。`select ISA_BUS_API` 一共 10 处，只落在 5 个 Kconfig 文件里：`drivers/counter/Kconfig`、`drivers/gpio/Kconfig`、`drivers/iio/addac/Kconfig`、`drivers/iio/dac/Kconfig`、`drivers/watchdog/Kconfig`——全部是 x86 ISA 插卡驱动。重跑：

```
grep -rn 'select ISA_BUS_API' --include=Kconfig* . | sed 's/:.*//' | sort -u
grep -rn 'select ISA_BUS_API' --include=Kconfig* . | wc -l
grep -rlw isa_register_driver --include=*.c . | wc -l
```

⚠️ 这一节列在这里只是为了让「本目录有 8 条伪总线」这句话闭合（`03-internals.md` §C3），实际读的时候可以跳过。

## §11 kobj_map：设备号到 kobject 的映射

`drivers/base/map.c`（154 行）实现 `kobj_map()`（`:32`）/ `kobj_unmap()`（`:68`）/ `kobj_lookup()`（`:95`）/ `kobj_map_init()`（`:135`），核心数据结构是 `struct kobj_map`（`:19`）——一个 255 桶的哈希表。⚠️ 这四个符号一个都没有 `EXPORT_SYMBOL`（`grep -cE '^EXPORT_SYMBOL' drivers/base/map.c` → 0），所以模块用不到它们。

⚠️ 6.1 里它只有一个用户，就是字符设备层 `fs/char_dev.c`，共 4 个调用点：`chrdev_init()` 建映射域（`fs/char_dev.c:669`）、`cdev_add()` 登记一段设备号（`:489`）、`cdev_unmap()` 注销（`:580`）、`chrdev_open()` 反查（`:386`）。块设备层在 6.1 已经不用它了——所以「字符/块设备注册」这句话对块设备不成立。重跑：

```
grep -rn 'kobj_map(\|kobj_lookup(\|kobj_map_init(\|kobj_unmap(' --include=*.c . | grep -v drivers/base/map.c
```

⚠️ `device_add()`（`03-internals.md` §A5）建 `/sys/dev/char/M:m` 入口时走的不是这里，那是 `device_create_sys_dev_entry()`（`drivers/base/core.c:3514`，在 `:3664` 被调）。两条路都以 `dev_t` 为键，但一条给用户态看、一条给 `chrdev_open()` 用。

## §12 汇总

### §12.1 框架 × 职责 × 挂载点 × arm64

| 框架 | 职责一句话 | 挂在时间线哪 | arm64 相关 |
| --- | --- | --- | --- |
| component | 多设备凑一个逻辑设备 | 组件与聚合方各自的 `probe()` 内 | 高（DRM） |
| auxiliary bus | 一个设备拆成多个 | 父设备 `probe()` 内 | 中 |
| software node | 无 DT 时提供属性 | `device_add()` 之前或之后 | 中 |
| soc bus | SoC 身份与勘误匹配 | 注册走 §A5；查询随时 | 高 |
| devcoredump | 崩溃转储给用户态 | 随时 | 中 |
| syscore ops | 系统级挂起/恢复回调 | ⚠️ 不在这条时间线上 | 高 |
| platform MSI | 非 PCI 设备用 MSI | platform `probe()` 内 | 中 |
| attribute container | 条件性挂属性 | 模块初始化 + 设备注册前后 | ⚠️ 低（只 SCSI） |
| transport class | 传输层属性的四段生命周期 | 同上 | ⚠️ 低（只 SCSI） |
| class_compat | 老 sysfs 布局兼容 | 模块初始化 | ⚠️ 极低 |
| isa bus | ISA 插卡 | 驱动注册 | ⚠️ 无 |
| kobj_map | `dev_t` → kobject | 字符设备注册的下层 | 间接 |

### §12.2 每个框架的全树使用统计

| 框架 | 口径 | 使用者数 | 重跑命令 |
| --- | --- | --- | --- |
| component（组件侧） | 文件数 | 98 | `grep -rlw component_add --include=*.c . \| wc -l` |
| component（聚合侧） | 文件数 | 30 | `grep -rlw component_master_add_with_match --include=*.c . \| wc -l` |
| auxiliary bus | `auxiliary_driver` 实例数 | 16 | `grep -rn 'struct auxiliary_driver [a-zA-Z_]* = {' --include=*.c . \| wc -l` |
| software node（裸/组） | 文件数 | 9 | `grep -rlwE 'software_node_register\|software_node_register_nodes\|software_node_register_node_group' --include=*.c . \| wc -l` |
| software node（挂设备） | 文件数 | 15 | `grep -rlw device_add_software_node --include=*.c . \| wc -l` |
| soc bus（写） | 文件数 | 26 | `grep -rlw soc_device_register --include=*.c . \| wc -l` |
| soc bus（读） | 文件数 | 54 | `grep -rlw soc_device_match --include=*.c . \| wc -l` |
| devcoredump | 文件数 | 24 | `grep -rlwE 'dev_coredumpv\|dev_coredumpm\|dev_coredumpsg' --include=*.c . \| wc -l` |
| syscore ops | 文件数 | 101 | `grep -rlw register_syscore_ops --include=*.c . \| wc -l` |
| platform MSI | 文件数 | 8 | `grep -rlw platform_msi_create_irq_domain --include=*.c . \| wc -l` |
| attribute container | 文件数 | 3 | `grep -rlw attribute_container_register --include=*.c . \| wc -l` |
| transport class | 文件数 | 8 | `grep -rlw transport_class_register --include=*.c . \| wc -l` |
| class_compat | 文件数 | 3 | `grep -rlw class_compat_register --include=*.c . \| wc -l` |
| isa bus | 文件数 | 29 | `grep -rlw isa_register_driver --include=*.c . \| wc -l` |
| kobj_map | 导出符号数 | ⚠️ 0 | `grep -cE '^EXPORT_SYMBOL' drivers/base/map.c` |

⚠️ 这张表里的「文件数」全部是 `grep -rlw`（按文件计，`-w` 全词匹配），不是调用点数。两个口径的差别在 component 那两行体现得最清楚：98 个文件对 106 个调用点。要调用点数就把 `-rlw` 换成 `-rnw` 再 `wc -l`。

⚠️ `syscore ops` 的 101 个文件里绝大多数在 `arch/` 和 `drivers/irqchip/`、`drivers/clocksource/`，它是这批框架里唯一「基础设施级」的。其余都是可选功能。
