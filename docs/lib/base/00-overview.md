---
title: 总览
---

# drivers/base 总览：Linux 驱动模型核心

这一篇是复习入口。隔几个月回来，只读这一篇就该恢复全貌；要细节再按 §8 的导读表跳。

---

## §1 定位

### §1.1 一句话

`drivers/base/` 是 Linux 的**驱动模型核心**：它定义"设备是什么、驱动是什么、两者怎么配上、配上之后谁负责收尾"，但它自己**不管任何一件具体硬件**。

### §1.2 它管什么 / 不管什么

| 管 | 不管 |
|---|---|
| `struct device` / `struct device_driver` / `struct bus_type` 三元组的语义 | 任何一条真实总线的电气协议 |
| 匹配（match）、绑定（probe）、解绑（remove）的时序与锁 | 具体驱动怎么读写寄存器 |
| 延迟探测（`-EPROBE_DEFER`）与依赖关系（device link） | 依赖的具体内容（时钟、电源、引脚） |
| 资源的自动回收（devres / `devm_*`） | 被回收的那些资源本身长什么样 |
| sysfs 里 `/sys/devices`、`/sys/bus`、`/sys/class` 的骨架 | 各子系统往 sysfs 里加的属性 |
| 设备属性的统一读取接口（`fwnode_*` / `device_property_*`） | 设备树/ACPI 的解析实现（在 `drivers/of/`） |
| 关机、离线、component 聚合、devcoredump | 电源管理策略（在 `drivers/base/power/`，本套不覆盖） |

### §1.3 子系统类型判定

按本套文档的四类划分，`drivers/base` 是**元框架型**——它既不是硬件总线型，也不是普通框架型。判定依据（两道门的实际输出记在 `99-evidence.md` §1.2）：

- 门 1「有没有自己的 `bus_type`」：**有，而且有八条**。但它们全部没有电气层，是纯软件构造（见 §3.3）。
- 门 2「协议 ABI 在不在 uapi 里」：**不在**。`include/uapi/linux/` 下没有任何 `base*` 头文件，说明它不面向用户态定义协议。

于是它落在一个特殊位置：**它是别人用来搭建总线的那套工具，同时自己也用这套工具搭了八条**。这个双重身份是读 `drivers/base` 时最需要先摆正的一件事，也是 `02-datastructures.md` §1.0 那几组关系的由来。

### §1.4 规模与基线

基线 `f6e38ae624cf`（Linux 6.1.158）。`git status --porcelain -- drivers/base include/linux/device.h include/linux/device/` 无输出——**本子系统在这个树上未被打过补丁**，所有结论对上游 6.1.158 有效。

对外导出的符号：`drivers/base/*.c` 里 `EXPORT_SYMBOL*` 共 312 行，但实际导出 **318** 个符号——差额来自 `drivers/base/core.c:4979` 的 `define_dev_printk_level` 宏，它一行展开出 7 个带 `EXPORT_SYMBOL` 的函数。逐条口径见 `01-api.md` §0.4。

---

## §2 背景：驱动模型要解决的什么问题

这一节讲"为什么会有 `drivers/base`"。不读这一节也能用它，但会看不懂它为什么做了那么多看起来跟硬件无关的事。

### §2.1 没有驱动模型的世界

`Documentation/driver-api/driver-model/overview.rst` 开篇（该文件第 14-21 行）自己交代了动机，原文值得逐字读：

> The Linux Kernel Driver Model is a unification of all the disparate driver models that were previously used in the kernel. … Traditional driver models implemented some sort of tree-like structure (sometimes just a list) for the devices they control. There wasn't any uniformity across the different bus types.

注意这份文档的落款是 "Drafted 26 August 2002 / Updated 31 January 2006"（`Documentation/driver-api/driver-model/overview.rst:7-8`）。也就是说驱动模型的核心设计已经二十年没有大改，而 6.1 里的 `drivers/base/core.c` 有五千多行——**长出来的四千多行几乎全是"横向问题"的答案，不是核心抽象的改动**。

统一之前的形状是这样：PCI 层有自己的设备链表和自己的 `pci_driver`；USB 层有自己的一套；ISA、SCSI 各有一套。每套都能工作，因为每套只需要回答"这条总线上有哪些设备、哪个驱动管它"。

问题出在**跨总线的问题**上。

### §2.2 五个横向问题

这五个问题的共同点是：**答案不在任何一条总线内部**。

**问题一：关机时按什么顺序通知设备？**

一台机器上同时有 PCI 设备、USB 设备、I2C 设备。关机时不能随便关——USB 控制器是 PCI 设备，先关 PCI 再关 USB 就等于对着已经掉电的控制器发命令。要正确关机，必须知道**跨总线的父子关系**。

6.1 里的答案是 `device_shutdown()`（`drivers/base/core.c:4808` 起）。它遍历的不是任何一条总线的链表，而是全局的 `devices_kset`，按**注册的逆序**走。逆序之所以正确，是因为父设备必然比子设备先注册。

**问题二：休眠/唤醒时呢？**

同样的问题，但更严格：唤醒时要按注册的正序，而且中途任何一步失败都得回滚。这套逻辑住在 `drivers/base/power/`（本套不覆盖），但它依赖的父子关系、依赖的 `dev->power` 字段（`include/linux/device.h:578`）都在 `drivers/base/` 定义。

**问题三：一个设备依赖另一条总线上的设备，怎么办？**

一个 I2C 上的触摸屏需要一路电源，而那路电源来自一个 SPI 上的 PMIC。触摸屏的 `probe()` 跑的时候，PMIC 可能还没被探测到。

这个问题在"每条总线各管一摊"的模型下**无解**——I2C 核心不知道 SPI 上有什么。6.1 里的答案是两层：`-EPROBE_DEFER` 重试机制（`drivers/base/dd.c`）加上显式的依赖关系记录（device link，`drivers/base/core.c` 前两千行的大半）。

**问题四：驱动申请的资源怎么保证被放掉？**

一个 `probe()` 里可能申请十来样东西：内存、寄存器映射、中断、时钟、GPIO、DMA 通道。任何一步失败都要把前面的放掉，`remove()` 里也要全放掉。手写就是一串 `goto err_xxx` 标签，而且 probe 和 remove 两处必须严格对称——这是驱动里错误率最高的代码形态。

答案是 devres（`drivers/base/devres.c`）：**把"怎么放掉"和"申请"绑在一起记账，解绑时逆序自动执行**。这个机制跟总线类型完全无关，所以只能住在公共层。

**问题五：用户态怎么知道系统里有什么？**

在"每条总线各管一摊"的模型下，用户态得为每条总线学一套接口。答案是 `sysfs`：所有设备统一出现在 `/sys/devices/` 下，按物理拓扑组织；`/sys/bus/` 和 `/sys/class/` 是同一批对象的两种索引视图。

### §2.3 解法：一张台账加三个抽象

驱动模型的全部设计可以压缩成两句话：

**第一句：给每个设备发一张统一的卡片。** 这就是 `struct device`（`include/linux/device.h:557`）。它里面没有任何硬件语义，装的全是上面五个问题的答案：`parent`（拓扑）、`bus`（归属）、`driver`（当前绑定）、`links`（依赖）、`power`（电源）、`devres_head`（资源账本）。

具体总线的设备结构体把它**嵌进去**（不是指过去），于是一个 `pci_dev` 既能被 PCI 层当 PCI 设备用，又能被公共层当 `struct device` 用。`Documentation/driver-api/driver-model/overview.rst:48-56` 用 `struct pci_dev` 做了官方示范，并特意点明"the struct device dev within the struct pci_dev is statically allocated"。为什么必须嵌入而不能指向，见 `08-feynman.md` §3。

**第二句：把"配对"这件事抽象成一个回调。** `Documentation/driver-api/driver-model/binding.rst:30-33` 的原话：

> Instead of trying to derive a complex state machine and matching algorithm, it is up to the bus driver to provide a callback to compare a device against the IDs of a driver.

也就是 `bus_type->match()`（`include/linux/device/bus.h:92`）。公共层负责"什么时候该配对、配上之后干什么"，总线层只负责回答"这一对配不配"。**这一刀切得极准**：匹配规则是唯一真正因总线而异的部分，其余（时序、锁、错误处理、资源回收）全部可以共用。

三个抽象由此确定：

```
struct bus_type      ── 一类设备的集合 + 匹配规则 + 生命周期钩子
    ├── klist_devices ── 这条总线上的设备
    └── klist_drivers ── 这条总线上的驱动
struct device        ── 一个设备实例（那张卡片）
struct device_driver ── 一个驱动实例
```

绑定的触发点只有两个，`Documentation/driver-api/driver-model/binding.rst:15-20` 说得很清楚：`device_register()` 的时候（新设备来了，遍历驱动找一个）和 `driver_register()` 的时候（新驱动来了，遍历设备找一批）。6.1 里对应 `drivers/base/dd.c` 的 `device_initial_probe()` 和 `driver_attach()` 两条入口。

### §2.4 sysfs：副产品还是目的？

容易误以为 sysfs 是驱动模型的输出。实际是反过来的——**`kobject` 层先存在，驱动模型建在它之上**。`struct device` 的第一个字段就是 `struct kobject kobj`（`include/linux/device.h:558`），不是巧合：

- 引用计数直接用 `kobject` 的。`put_device()` 只有一行有效代码（`drivers/base/core.c:3801-3806`），就是 `kobject_put(&dev->kobj)`。
- 父子关系直接用 `kobject` 的 parent。`device_add()` 里 `kobject_add(&dev->kobj, dev->kobj.parent, NULL)`（`drivers/base/core.c:3632`）一步同时完成"进拓扑"和"建目录"。
- uevent 也是 `kobject` 的（`kobject_uevent()`）。

所以准确的说法是：**驱动模型和 sysfs 共用一套对象层**，sysfs 是那套对象层的可视化。这解释了一件容易困惑的事——为什么 `device_del()` 和 `put_device()` 是两个动作（`drivers/base/core.c:3925-3930` 的 `device_unregister()` 把它们串起来）：前者从拓扑里摘掉、目录消失，后者才是放引用。目录不见了不等于内存放了。

### §2.5 二十年长出来的东西

核心三元组没变，长出来的是横向能力。按加入的逻辑顺序（不是严格的历史顺序）：

**devres（`drivers/base/devres.c`，1239 行）** —— 解决 §2.2 问题四。核心只有 `devres_node`（`drivers/base/devres.c:19-24`）四个字段和一条逆序释放的循环（`drivers/base/devres.c:493-505`）。这是全内核投入产出比最高的一段代码：一千二百余行里真正的核心不到两百行，消灭了几万个 `goto err` 标签。

**延迟探测（`drivers/base/dd.c`）** —— 解决 §2.2 问题三的第一层。机制极简：失败就挂链表，有新驱动注册就整条重试。代价是最坏 O(n²)，收益是不需要任何全局依赖知识。

**device link（`drivers/base/core.c`，约前 1700 行）** —— 解决 §2.2 问题三的第二层，也回头解决了问题一和二。它把"A 依赖 B"变成一个显式的对象（`struct device_link`），于是关机顺序、休眠顺序、`sync_state()` 时机全都有了准确依据，不再只靠父子关系近似。

**fw_devlink（`drivers/base/core.c:1721-2260` 一带）** —— 让 device link **在第一次 probe 之前就建好**。做法是扫设备树，凡是引用了别人 phandle 的地方就推定一条依赖（规则表在 `drivers/of/property.c:1341-1376`，34 条）。这是 arm64 上启动顺序变得可预测的主要原因。原理和它的三处失真见 `08-feynman.md` §6。

**统一属性接口（`drivers/base/property.c`，1421 行）** —— 让驱动用同一套 `device_property_read_u32()` 读设备树、ACPI 表或者内建的 `property_entry` 数组。arm64 上实际只走设备树那一路。

**component（`drivers/base/component.c`）** —— 解决"一个逻辑设备由多个硬件块拼成"（典型是显示子系统）。它不在核心绑定路径上，是并列的一套聚合机制，见 `04-extensions.md`。

**devcoredump（`drivers/base/devcoredump.c`）** —— 设备崩了把内部状态倒出来给用户态。跟绑定无关，纯诊断设施。

### §2.6 元框架型的特殊性

`drivers/base/` 里有八个 `bus_type` 实例，但**没有一条对应真实的电气总线**：

| 总线 | 定义位置 | 上面的设备从哪来 |
|---|---|---|
| `platform` | `drivers/base/platform.c:1478` | 设备树展开、代码显式注册 |
| `cpu`（实际名为 `cpu_subsys`） | `drivers/base/cpu.c:128` | 每个 CPU 一个 |
| `memory`（`memory_subsys`） | `drivers/base/memory.c:71` | 每个内存块一个 |
| `node`（`node_subsys`） | `drivers/base/node.c:25` | 每个 NUMA 节点一个 |
| `container`（`container_subsys`） | `drivers/base/container.c:27` | ACPI 容器（arm64 上不涉及） |
| `soc`（`soc_bus_type`） | `drivers/base/soc.c:30` | SoC 标识信息 |
| `auxiliary`（`auxiliary_bus_type`） | `drivers/base/auxiliary.c:247` | 一个驱动拆成多块时自己造 |
| `isa`（`CONFIG_ISA_BUS_API`） | `drivers/base/isa.c:85` | arm64 上不涉及 |

（清单与验证命令见 `01-api.md` §2.4。）

这份清单说明了 `drivers/base` 的真实性格：**它是"总线"这个概念的提供者，而它自己造的那些总线全都是为了"把某类东西塞进统一台账"**。CPU 不需要匹配驱动，`memory` 更不需要，但把它们变成 `struct device` 之后就能免费获得 sysfs 目录、属性、uevent 和电源管理钩子。这是"复用抽象"而不是"抽象硬件"。

其中 `platform` 是量级完全不同的一条：全树 `struct bus_type` 实例有 146 个，而使用 `platform_driver_register()` 或 `module_platform_driver()` 的 .c 文件有 4019 个。arm64 上一颗 SoC 的绝大多数外设都在 platform 总线上。为什么"假总线"反而最忙，见 `08-feynman.md` §7。

### §2.7 arm64-only 裁掉了什么

本套文档统一按 arm64 单架构写。被明确裁掉的部分：

| 裁掉的 | 依据 | 影响 |
|---|---|---|
| `physical_location.c` / `.h` | `obj-$(CONFIG_ACPI)`（`drivers/base/Makefile:28`） | 整个文件在 arm64 常见配置下不编译 |
| ACPI 设备发现路径 | `platform_match()` 里的 `acpi_driver_match_device()`（`drivers/base/platform.c:1345-1346`）在无 ACPI 时是空实现 | `06-devicetree.md` §0 的分诊表只保留 DT 与代码注册两行 |
| `container` 总线 | ACPI 容器热插拔专用 | §2.6 表里标注，不展开 |
| `isa` 总线 | `CONFIG_ISA_BUS_API`（`drivers/base/Makefile:13`），arm64 无此配置 | 同上 |
| x86/ppc 的架构特化 | —— | 本套不做多架构对照 |

⚠️ 一处必须提醒的**上游文档时效问题**：`Documentation/driver-api/driver-model/overview.rst:29-35` 那段把 ACPI 说成驱动模型的目标形态（"the model dictated by Intel and Microsoft (namely ACPI)"）。这是 2002 年的语境，对 arm64 完全不适用——arm64 上等价物是设备树，而设备树的发现路径与 ACPI 差异很大。读那份文档时要跳过这一段。其余上游文档的时效问题记在 `99-evidence.md` §1.3。

---

## §3 概念模型

### §3.1 四个名词，各回答一个问题

| 名词 | 回答的问题 | 结构体 |
|---|---|---|
| device | 系统里有什么 | `struct device` |
| driver | 谁会开这类东西 | `struct device_driver` |
| bus | 这一类里怎么判断"配不配" | `struct bus_type` |
| class | 怎么把它交给用户态 | `struct class` |

⚠️ **`bus` 和 `class` 最容易被当成一回事**，它们连私有结构都共用（都是 `struct subsys_private`，见 `02-datastructures.md` §1.0.2）。分界线只有一条：**`bus` 有 `match()`，`class` 没有**。展开见 `08-feynman.md` §2。

### §3.2 三阶段生命周期

```
       ┌──────────── 阶段 1：存在 ────────────┐
       device_initialize()  →  device_add()
                                   │
                        （台账里有了，sysfs 有目录了，
                          但还没有驱动）
                                   ↓
       ┌──────────── 阶段 2：被驱动 ──────────┐
       match()  →  probe()  →  driver_bound()
                                   │
                        （devres 账本开始记账，
                          driver 符号链接出现）
                                   ↓
       ┌──────────── 阶段 3：解绑与销毁 ──────┐
       device_release_driver()   ← 可以反复来回
             ↓
       device_del()  →  put_device()  →  release()
```

两个必须分清的界限：

- **阶段 2 可以反复进出**。`unbind` / `bind` 就是在阶段 1 和阶段 2 之间来回（`07-debug.md` §5.2）。
- **阶段 3 的两步是独立的**。`device_del()` 之后设备从拓扑消失、目录不见了，但只要还有人持引用，内存就不释放。混淆这两步是读 `drivers/base` 最常见的错误。

### §3.3 八条伪总线

见 §2.6。要点：**`drivers/base` 自己造的八条总线一条都没有电气层**，它们的存在理由是复用 `struct device` 带来的 sysfs、属性和电源钩子。

### §3.4 两种"依赖"

这是本子系统里第二组容易混的关系：

| | 父子关系（`dev->parent`） | device link |
|---|---|---|
| 表达什么 | 拓扑上的从属（挂在谁下面） | 功能上的依赖（用了谁的服务） |
| 数量 | 每个设备最多一个父 | 任意多条，双向链表 |
| 谁建立 | 创建设备时由创建者指定 | 显式 `device_link_add()`，或 `fw_devlink` 推断 |
| 影响 probe 顺序吗 | 不直接影响 | 直接影响（供应方没就绪就 defer） |
| 影响关机顺序吗 | 影响（逆注册序天然满足） | 影响（`device_links_check_suppliers()` 之外还调整 `devices_kset` 次序） |

细化对比在 `02-datastructures.md` §1.0.4。

---

## §4 数据结构速览

只列"读代码时一定会撞上"的十个。字段级细节在 `02-datastructures.md`。

| 结构体 | 定义位置 | 一句话 | 细节 |
|---|---|---|---|
| `struct device` | `include/linux/device.h:557` | 那张统一卡片，60 多个字段 | `02` §2 |
| `struct device_driver` | `include/linux/device/driver.h:96` | 驱动侧的对应物，7 个回调 | `02` §4 |
| `struct bus_type` | `include/linux/device/bus.h:84-118` | 一类设备的集合 + 13 个回调 | `02` §3 |
| `struct class` | `include/linux/device/class.h:54-78` | 用户态视图，没有 `match()` | `02` §5.1 |
| `struct device_link` | `include/linux/device.h` | 一条显式依赖 | `02` §6.2 |
| `struct fwnode_handle` | `include/linux/fwnode.h` | 固件节点的抽象（arm64 上背后是 `device_node`） | `02` §6.4 |
| `struct platform_device` | `include/linux/platform_device.h` | 内嵌 `device` + `resource[]` | `02` §7.1 |
| `struct devres_node` | `drivers/base/devres.c:19-24` | 资源账本的一条，4 个字段 | `02` §8.1 |
| `struct device_private` | `drivers/base/base.h` | 核心私有，`dev->p` 指向它 | `02` §9.1 |
| `struct subsys_private` | `drivers/base/base.h` | `bus` 和 `class` 共用的私有 | `02` §9.3 |

三条最容易踩的：

- `dev->p`、`drv->p`、`bus->p` **不是同一个类型**（`02-datastructures.md` §1.0.3）。
- `platform_device` 是**包含** `device` 而不是继承（`02-datastructures.md` §1.0.5），转换只能用 `to_platform_device()`。
- `struct devres_group` 在链表里占**两个**节点（`drivers/base/devres.c:38-43`），按节点数做统计会多算。

---

## §5 机制速览

| 机制 | 一句话 | 主要位置 | 细读 |
|---|---|---|---|
| 匹配 | 总线提供 `match()`，核心决定何时调 | `drivers/base/dd.c:906-947` | `03` §A6 |
| 探测 | `really_probe()` 一条长函数，含 sysfs、devres、通知链 | `drivers/base/dd.c:584-710` | `03` §A7 |
| 延迟探测 | 失败挂链表，有新驱动注册就整条重试 | `drivers/base/dd.c:81-196`、`:344-370` | `03` §A9 |
| 解绑 | 必须先解绑消费者，且要放锁重查 | `drivers/base/dd.c:1226-1277` | `03` §A10 |
| devres | 申请时记账，解绑时逆序释放 | `drivers/base/devres.c` | `03` §B2 |
| device link | 五态状态机，管 probe 顺序和 `sync_state()` | `drivers/base/core.c` 前段 | `03` §B3 |
| fw_devlink | 扫设备树预建依赖，34 条属性规则 | `drivers/base/core.c` + `drivers/of/property.c:1341-1376` | `03` §B4、`06` §7 |
| 异步探测 | 选择加入，三档；等待要等三个机制 | `drivers/base/dd.c:853-871`、`:749-762` | `03` §B7 |
| sysfs 骨架 | glue dir、属性组、uevent | `drivers/base/core.c` | `03` §B5、§B6 |
| component | 多个硬件块凑齐才 bind 一次 | `drivers/base/component.c` | `04` §1 |
| 属性访问 | `device_property_*` 统一转发到 fwnode ops | `drivers/base/property.c` | `01` §9 |

---

## §6 端到端调用路径

一个写在设备树里的外设，从上电到驱动跑起来，完整链条如下。每一步都标了细读位置。

```
① 内核启动
   driver_init()                       drivers/base/init.c:21-42        03 §A1
     ├─ devices_init()   建 /sys/devices
     ├─ buses_init()     建 /sys/bus
     ├─ classes_init()   建 /sys/class
     └─ platform_bus_init()            drivers/base/platform.c          03 §A1

② 设备树被展开成设备
   of_platform_default_populate_init()  drivers/of/platform.c            06 §5
     └─ of_platform_populate()
          └─ of_platform_device_create_pdata()
               └─ platform_device_add()  → device_add()

③ 登记那张卡片
   device_add()                        drivers/base/core.c:3578-3755    03 §A5
     ├─ kobject_add()                  :3632   建 sysfs 目录
     ├─ fw_devlink_link_device()       :3694   把设备树里的引用变成依赖
     └─ bus_probe_device()             :3697   触发匹配

④ 找驱动
   __device_attach()                   drivers/base/dd.c:985-1040       03 §A6
     └─ bus_for_each_drv(__device_attach_driver)
          └─ driver_match_device()      drivers/base/base.h:144
               └─ bus->match()          = platform_match()
                                        drivers/base/platform.c:1331-1354

⑤ 探测
   really_probe()                      drivers/base/dd.c:584-710        03 §A7
     ├─ device_links_check_suppliers()  drivers/base/core.c:1108-1160
     │     └─ 供应方没就绪 → -EPROBE_DEFER → 挂链表，回到 ⑨
     ├─ driver_sysfs_add()              建 driver 符号链接
     ├─ bus->dma_configure()            platform_dma_configure()
     ├─ call_driver_probe()             drivers/base/dd.c:562-579
     │     └─ bus->probe() = platform_probe()
     │          drivers/base/platform.c:1395-1412
     │            └─ drv->probe(pdev)   ← 驱动自己的代码在这里
     └─ driver_bound()                  drivers/base/dd.c:393-421       03 §A8

⑥ 驱动在 probe 里申请资源
   devm_ioremap_resource() / devm_request_irq() / devm_clk_get() ...
     └─ devres_alloc() + devres_add()   drivers/base/devres.c:240-249
          （每一样都在 dev->devres_head 上记一笔）

⑦ 绑定完成
   driver_bound() 里
     ├─ klist_add_tail(&dev->p->knode_driver)   进驱动的设备链表
     ├─ device_links_driver_bound()             把依赖标成 ACTIVE
     └─ kobject_uevent(KOBJ_BIND)               通知用户态

⑧ 解绑（unbind / 模块卸载 / 设备移除）
   __device_release_driver()            drivers/base/dd.c:1226-1277     03 §A10
     ├─ 先把消费者全解绑（while 循环 + 放锁重查）    :1234-1249
     ├─ device_remove()  → drv->remove()
     └─ device_unbind_cleanup()          :1265
          └─ devres_release_all()        逆序执行 ⑥ 记下的每一笔

⑨ 延迟探测循环（与 ④⑤ 并行存在）
   任何 driver_register() 成功         drivers/base/driver.c:246-255    03 §A9
     └─ deferred_probe_extend_timeout()
          → 整条 deferred 链表重试
   超时（deferred_probe_timeout=）到点
     └─ deferred_probe_timeout_work_func()  drivers/base/dd.c:306-320
          → 打印 "deferred probe pending" 然后放弃
```

三处最值得记住的分叉：

- **③ 里 `fw_devlink_link_device()` 在 `bus_probe_device()` 之前**（`drivers/base/core.c:3694` vs `:3697`）。所以依赖是在第一次探测之前就建好的，这是 `fw_devlink` 有效的前提。
- **⑤ 里 `-EPROBE_DEFER` 不进 ⑧**。defer 的设备走 `device_unbind_cleanup()` 清掉已申请的资源，但不算失败——它回到 ⑨ 等下一轮。
- **⑥ 记的账在 ⑧ 才执行，而且执行时驱动的 `remove()` 已经跑完**。所以 `remove()` 里可以放心使用 `devm_*` 申请的东西。

---

## §7 子系统关系

### §7.1 谁站在 `drivers/base` 上面

```
       所有设备驱动（arm64 上大多数是 platform_driver）
                        ↑
     ┌──────────┬───────┴────┬──────────┬──────────┐
   i2c 核心   spi 核心    pci 核心    usb 核心   ... 146 条总线
     └──────────┴────────────┴──────────┴──────────┘
                        ↑ 用 bus_register() / device_add() / devres
              ┌─────────────────────┐
              │   drivers/base/     │  ← 本套文档
              └─────────────────────┘
                        ↓ 用
     ┌──────────┬──────────────┬──────────────┐
   kobject    sysfs          klist        driver core 之下的基础设施
   (lib/)     (fs/sysfs/)    (lib/klist.c)
```

### §7.2 横向邻居

| 子系统 | 关系 | 具体接口 |
|---|---|---|
| `drivers/of/` | 设备树解析。`drivers/base` 只认 `fwnode`，`of` 提供其中一种实现 | `of_fwnode_ops`；`fw_devlink` 的属性规则表在 `drivers/of/property.c:1341-1376` |
| `drivers/acpi/` | 另一种 `fwnode` 实现。**arm64-only 下裁掉** | — |
| `drivers/base/power/` | 电源管理。用 `dev->power` 和父子/link 关系排序 | 本套不覆盖 |
| `drivers/base/regmap/` | 寄存器访问抽象。住在同一目录但完全独立 | 本套不覆盖 |
| `drivers/base/firmware_loader/` | 固件加载。同上 | 本套不覆盖 |
| `kernel/dma/` | DMA 映射。`bus->dma_configure()` 是它的入口挂钩 | 本套的 `dma-mapping` 文档集 |
| `drivers/iommu/` | 通过 `bus_type->iommu_ops`（`include/linux/device/bus.h:112`）挂进来 | 本套的 `iommu` 文档集 |
| `kernel/module/` | 模块参数决定异步探测（`include/linux/module.h:678-681`） | `07` §3.3 |
| `lib/kobject*.c` | 对象层与引用计数，`struct device` 的地基 | 见 §2.4 |

### §7.3 与 `drivers/base/power/` 的分工

这条界线常被误判，单独说明：**`drivers/base/` 定义"顺序从哪来"，`drivers/base/power/` 定义"按这个顺序做什么"**。

- 顺序的来源：`devices_kset` 的注册次序、`dev->parent`、device link。全在 `drivers/base/core.c`。
- 用这个顺序做休眠/唤醒：`drivers/base/power/main.c` 的 `dpm_list`。
- 唯一在 `drivers/base/core.c` 里的电源相关代码是关机路径（`device_shutdown()`，`:4808` 起），它不走 `dpm_list`。

---

## §8 导读

### §8.1 按你要干什么

| 你要 | 读 |
|---|---|
| 写一个 platform 驱动 | `05-usage.md` §1 → `01-api.md` §8 |
| 自己实现一条新总线 | `05-usage.md` §4 → `03-internals.md` §A2 |
| 搞明白某个 `devm_*` 什么时候释放 | `01-api.md` §7 → `03-internals.md` §B2 → `08-feynman.md` §5 |
| 设备起不来，要排查 | `07-debug.md` §0.2（按症状找章节） |
| DTS 写好了但设备没出现 | `06-devicetree.md` §0 → §11 |
| 弄清某个字段是干什么的 | `02-datastructures.md` 按结构体找 |
| 弄清 probe 流程每一步 | `03-internals.md` Part A |
| 理解某个设计为什么这样 | `08-feynman.md` |
| 判断某条结论可不可信 | `99-evidence.md` |

### §8.2 十篇的定位

| 篇 | 定位 | 特别之处 |
|---|---|---|
| `00-overview.md` | 复习入口（本篇） | §2 是唯一讲"为什么"的长背景 |
| `01-api.md` | 代码地图 + 对外 API + 错误码 | §0 是"找文件"的唯一入口 |
| `02-datastructures.md` | 结构体逐字段 + 关系图 | §1.0 先破六组歧义，别跳过 |
| `03-internals.md` | 流程剖析，三条轴（A 时间线 / B 横切 / C 评价） | §C1 列了源码自陈的设计债 |
| `04-extensions.md` | 12 个独立子框架，互不调用 | §0.1 分诊表标了 arm64 上哪几个真会碰到 |
| `05-usage.md` | 九个角色的代码骨架 | §10 近邻符号区分；骨架**未编译** |
| `06-devicetree.md` | DTS 属性 → 消费代码 | §7 那 34 条 phandle 属性是 arm64 上最关键的一节 |
| `07-debug.md` | 开关 / 日志 / sysfs / debugfs / 错误码 | §0.2 是全套里最实用的一张表 |
| `08-feynman.md` | 九个"为什么"，带类比和类比边界 | §2 是最容易类比错的那组关系 |
| `99-evidence.md` | 取证等级、未验证项、自审记录 | §5 决定哪些结论要自己再确认 |

### §8.3 三条推荐路线

**新手第一遍（约两小时）**：本篇 §1–§3 → `08-feynman.md` §1–§3 → `02-datastructures.md` §1.0 → `05-usage.md` §1。

**要排故障（十分钟内定位）**：`07-debug.md` §0.2 直接跳章节。

**要改核心代码**：`03-internals.md` 全篇 → `99-evidence.md` §5（先看清哪些结论没被验证过）→ 对应源码逐行。
