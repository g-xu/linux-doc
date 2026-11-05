---
title: 费曼学习法
---

# PCI 子系统：八个概念的费曼式讲解

前面七篇都在讲"是什么、在哪一行"。这一篇换个方向：**假如让你从零设计 PCI 这一层，你会不会做出和内核一样的选择？** 每节先讲一个不涉及代码的例子，再回到代码看内核实际怎么写的，最后指出这个类比在哪里会骗你。

代码基线 Linux 6.1.158，arm64。

---

## §1 为什么 PCI 有 `bus_type`，而 pinctrl、clk、regulator 都没有？

### 先举个例子

想想两种不同的"提供服务"的方式。

第一种：写字楼里的**中央空调**。你租了一间办公室，空调管道已经通到你天花板上，你不需要"申请"，只要打开风口。楼里有多少家公司、谁在用几个风口，物业不需要为每家公司建一份档案 —— 它只维护一张"风口编号 → 哪层哪间"的表。你要用，报编号；不用，闭嘴。

第二种：写字楼里的**租户**本身。物业必须为每一家租户建档：名字、面积、合同、水电表读数、什么时候进场、什么时候退租、有没有欠费。租户会搬走、会转租、会换名字，物业必须有一套完整的生命周期管理。

pinctrl、clk、regulator、reset、pwm、mailbox 全是第一种 —— 它们是**中央空调**。它们管的是"编号 → 硬件资源"的映射表，消费者是别的驱动，不是设备本身。所以它们不需要 `bus_type`：没有"总线上挂着的设备"这个概念，只有"谁引用了第几号资源"。

PCI 是第二种。PCI 上挂的每一个东西**本身就是一个设备**，有身份（vendor/device ID）、有生命周期（插入/移除）、有自己的驱动、有自己的电源状态。它需要的不是一张映射表，而是一整套租户管理系统。

### 回到代码

这个区别在代码里就是一行：

```c
/* drivers/pci/pci-driver.c:1685-1699 */
struct bus_type pci_bus_type = {
        .name           = "pci",
        .match          = pci_bus_match,
        .uevent         = pci_uevent,
        .probe          = pci_device_probe,
        .remove         = pci_device_remove,
        .shutdown       = pci_device_shutdown,
        .dev_groups     = pci_dev_groups,
        .bus_groups     = pci_bus_groups,
        .drv_groups     = pci_drv_groups,
        .pm             = PCI_PM_OPS_PTR,
        .num_vf         = pci_bus_num_vf,
        .dma_configure  = pci_dma_configure,
};
EXPORT_SYMBOL(pci_bus_type);                          /* :1700 */
```

对比一下：整套 23 个子系统里，**只有 PCI 注册了 `bus_type`**。前面 22 个都是 provider/consumer 模式，核心数据结构是一个全局链表加一把锁，加上 dts 里的 phandle 解析（06 篇讲的那些）。

`bus_register()` 的调用点在 `drivers/pci/pci-driver.c:1740`，跑在 `postcore_initcall(pci_driver_init)` `drivers/pci/pci-driver.c:1747`。同一阶段还注册了 class：`class_register()` `drivers/pci/probe.c:107`，`postcore_initcall` `drivers/pci/probe.c:109`。

有了 `bus_type`，你就免费得到了驱动核心的全部机制：`.match` 一返回真，`.probe` 自动被调；`/sys/bus/pci/drivers/*/bind`、`unbind`、`new_id`、`remove_id` 四个文件由驱动核心生成，PCI 一行代码没写（ABI 文档 `Documentation/ABI/testing/sysfs-bus-pci:1`、`:17`、`:33`、`:52`）；`modalias` 一发出来 udev 就能自动加载模块。

provider 模式的子系统想要"自动匹配 + 自动 probe"，得自己造 —— 而它们不需要，因为它们没有"设备"可匹配。

### 这个类比什么时候会误导你？

租户类比会让你以为**设备是先存在、后被登记的**。PCI 反过来：设备是**被枚举出来的**。物业不是等租户上门签合同，而是拿着钥匙一间间开门看，看到里面有东西就登记一个。

```c
/* drivers/pci/probe.c:2691 —— 一次扫一个 slot 的所有 function */
int pci_scan_slot(struct pci_bus *bus, int devfn)
```

内核对每个 `(bus, devfn)` 组合去读一次配置空间 offset 0（vendor ID）。读回 `0xffff` 就是"这一格没人"，读回别的就 `pci_scan_single_device()` `drivers/pci/probe.c:2603` 建一个 `pci_dev`。

这个差别有个直接后果：**PCI 设备的存在与否，靠读硬件来判断，而不是靠有人告诉内核**。dts 里 06 篇说的那些属性只描述**主机桥**，桥下面挂了什么内核一概不知，全靠扫。这就是为什么 06 篇反复强调"其余信息都在配置空间里"—— 不是设计洁癖，是这套机制的必然结果。

第二处误导：类比里物业只有一套档案系统。PCI 有两套，见 §2。

---

## §2 为什么会有第二条总线 `pcie_port_bus_type`？

### 先举个例子

接着上面的写字楼。有一家租户是**物业公司自己的分部**：它占一间办公室，有门牌，交水电（所以必须在租户档案里）。但它同时提供四项楼内服务：消防报警、电梯调度、门禁、备用发电。

现在问题来了：这四项服务各有一个专业团队，各自需要独立上岗、独立排班、独立故障处理。你有两个选择：

（甲）让这一家租户的档案里塞四个字段，物业的租户管理系统里多出四套逻辑。
（乙）在租户系统之外再建一套"服务商登记系统"，把这四个团队登记成四个独立主体，各自和那间办公室关联。

选甲，租户系统会被四项和"租房"毫无关系的逻辑污染，而且以后加第五项服务又要改一遍。选乙，两套系统各管一件事。

PCIe 的 Root Port 和 Switch Port 就是那家"物业分部"。它是一个标准 PCI 设备（有配置空间、有 BDF、有 BAR），同时提供四项服务：PME（唤醒）、AER（错误报告）、DPC（下游端口容错）、Hotplug。

### 回到代码

内核选了乙：

```c
/* drivers/pci/pci-driver.c:1724 */
struct bus_type pcie_port_bus_type = {
        ...
};
EXPORT_SYMBOL_GPL(pcie_port_bus_type);                /* :1728 */
```

于是同一块硬件在 sysfs 里出现在两个地方：

```
/sys/bus/pci/devices/0000:00:01.0/          ← 作为 PCI 设备
/sys/bus/pci_express/devices/0000:00:01.0:pcie001/   ← 作为 PME 服务
/sys/bus/pci_express/devices/0000:00:01.0:pcie002/   ← 作为 AER 服务
```

后缀那个数字就是服务类型。四项服务各由一个"端口服务驱动"绑定，互不影响。`pcie_ports=` 启动参数（`drivers/pci/pcie/portdrv_pci.c:49`，文档 `Documentation/admin-guide/kernel-parameters.txt:4395-4403`）控制的就是这套：`compat` 全关，`native` 全开，`dpc-native` 只开 DPC。

这个设计的收益很具体：AER 服务挂了不影响 Hotplug 服务；`pcie_ports=dpc-native` 这种"只要一项"的组合不需要任何特殊代码，就是少绑一个驱动。

### 这个类比什么时候会误导你？

两套系统听着对称，其实**地位完全不对等**：

| 维度 | `pci_bus_type` | `pcie_port_bus_type` |
|---|---|---|
| 导出符号 | `EXPORT_SYMBOL` `drivers/pci/pci-driver.c:1700`（GPL 无关） | `EXPORT_SYMBOL_GPL` `drivers/pci/pci-driver.c:1728` |
| 设备来自 | 硬件枚举 | 由 portdrv **人为造出来** |
| 外部驱动会用吗 | 几乎所有 PCI 驱动 | 只有 PCI 核心自己的四个服务 |
| 少了它会怎样 | PCI 完全不工作 | 少四项可选服务 |

第二条总线上的设备**不是真设备**，是软件构造物。所以你在 `/sys/bus/pci_express/devices/` 下看不到 `config`、`resource`、`vendor` 这些 —— 它们没有配置空间可读。

还有一处：这两条总线的**导出属性不同**这件事本身就值得记住。`pci_bus_type` 用非 GPL 导出是历史包袱（早期闭源驱动依赖它），新代码不该学。

---

## §3 配置空间为什么必须是一个"独立的地址空间"，不能就用普通 MMIO？

### 先举个例子

你搬进一间**完全空的**新房子，墙上没有门牌号，房间里的每个设备（热水器、空调、洗衣机）都还没接线。现在要给每个设备分配一个"通信地址"。

问题是：你要**先能和设备说话，才能告诉它地址**。但你还没给它地址，怎么和它说话？

这是个鸡生蛋问题。唯一的解法是：留一条**不需要分配就能用**的通道。比如每个设备都有一个物理拨码开关，位置在标准位置，出厂就能用 —— 你通过它读出设备型号，然后通过它写入分配好的地址。

配置空间就是这条通道。

### 回到代码

关键在于访问方式：**配置空间不用地址寻址，用"位置"寻址**。

```c
/* include/linux/pci.h:778-784 */
struct pci_ops {
        int (*add_bus)(struct pci_bus *bus);
        void (*remove_bus)(struct pci_bus *bus);
        void __iomem *(*map_bus)(struct pci_bus *bus, unsigned int devfn, int where);
        int (*read)(struct pci_bus *bus, unsigned int devfn, int where, int size, u32 *val);
        int (*write)(struct pci_bus *bus, unsigned int devfn, int where, int size, u32 val);
};
```

看 `read` 的参数：`(bus, devfn, where)`。不是一个地址，是**"哪条总线上第几个设备的第几个字节"**。设备在物理拓扑里的位置就是它的地址 —— 这个位置由布线决定，出厂就固定，不需要分配。

ECAM 把这个"位置"再翻译成一个真的 MMIO 地址（06 §2.2 的 `bus_shift`），但那是主机桥的实现细节。语义上，配置空间的地址是 `(domain, bus, device, function, offset)` 这个五元组，不是一个数。

于是启动流程就顺了：

```
1. 主机桥驱动从 dts 拿到 ECAM 窗口       ← 06 §2.5
2. 通过 (bus,devfn,0) 读 vendor ID       ← 位置寻址，不需要 BAR
3. 读到有效值 → 建 pci_dev
4. 读 BAR 的大小要求（往 BAR 写全 1 再读回）
5. 分配地址，写回 BAR                    ← §4
6. 打开 PCI_COMMAND_MEMORY，MMIO 才可用
```

第 2 到 5 步全靠配置空间。如果 BAR 也要通过 MMIO 才能访问，这个流程根本启动不了。

### 这个类比什么时候会误导你？

拨码开关的类比会让你以为配置空间是"只读的身份标签"。它不是 —— 配置空间是**可写的控制面**，而且写它有真实副作用：

| offset | 写它会发生什么 |
|---|---|
| `PCI_COMMAND` | 打开/关闭 MMIO 响应、IO 响应、总线主控、INTx |
| BAR (0x10..0x24) | 改变设备响应哪段地址 |
| `PCI_MSI_ADDRESS_LO` `include/uapi/linux/pci_regs.h:313` | 改变 MSI 往哪写 |
| `PCI_MSI_FLAGS_ENABLE` `include/uapi/linux/pci_regs.h:307` | 开关 MSI |
| Secondary Bus Number | 改变桥的转发范围，写错整条子树消失 |

正因为可写且危险，07 §3.1 那三道防线才存在：普通用户只能读前 64 字节（`drivers/pci/pci-sysfs.c:721`），写要过 lockdown（`drivers/pci/pci-sysfs.c:801`），procfs 那边还额外要 `CAP_SYS_RAWIO`（`drivers/pci/proc.c:250`）。

第二处误导：类比里"拨码开关出厂就能用"。实际上 ECAM 窗口本身要从 dts 拿，dts 写错了这条通道也是死的 —— 06 §2.5 讲的 `bus-range` 截断就是这条通道被配小了的表现。

---

## §4 BAR 为什么由内核分配，而不是设备自己决定？

### 先举个例子

一栋楼里所有房间共用一套邮政地址空间。每个房间都需要一个门牌号，而且门牌号必须唯一、连续、对齐（比如"占 4 个号的房间必须从 4 的倍数开始"）。

方案甲：每个房间自己选号。结果是所有房间都选"1 号"，因为出厂默认值一样。
方案乙：房间只声明"我需要占几个号，且必须按几对齐"，由物业统一编号。

PCI 选乙。BAR 的机制就是这个"声明"：

- 设备出厂时 BAR 的低位是硬连线的 0（表示对齐要求和大小）。
- 内核往 BAR 写全 1，再读回来。读回来的值里，**从最低的 1 开始的那些位是可写的**，往下全是 0 —— 0 的个数就告诉你大小和对齐。
- 内核算出一个合适的地址写回去。

设备从来不选自己的地址，只声明需求。

### 回到代码

分配的入口在 `pci_host_probe()`：

```c
/* drivers/pci/probe.c:3099-3112 */
/*
 * We insert PCI resources into the iomem_resource and
 * ioport_resource trees in either pci_bus_claim_resources()
 * or pci_bus_assign_resources().
 */
if (pci_has_flag(PCI_PROBE_ONLY)) {
        pci_bus_claim_resources(bus);                          /* :3105 */
} else {
        pci_bus_size_bridges(bus);                             /* :3107 */
        pci_bus_assign_resources(bus);                         /* :3108 */

        list_for_each_entry(child, &bus->children, node)
                pcie_bus_configure_settings(child);            /* :3110-3111 */
}
```

注意这里的两条路：

| 路径 | 何时走 | 做什么 |
|---|---|---|
| `pci_bus_claim_resources()` `drivers/pci/setup-bus.c:1482` 导出 | `PCI_PROBE_ONLY` 置位 | **接受固件已经分配好的地址**，只登记进 resource 树 |
| `pci_bus_size_bridges()` + `pci_bus_assign_resources()` `drivers/pci/setup-bus.c:1411`，导出 `:1415` | 默认 | 自己重新算、自己分配 |

06 §4.3 讲过 `linux,pci-probe-only` 在 arm64 dts 里出现 0 次 —— 所以 arm64 走的**永远是右边那条**：内核完全无视固件分配的地址，从头算一遍。这也解释了 06 §2.4 里 `pci_host_common_probe()` 无条件设 `PCI_REASSIGN_ALL_BUS` `drivers/pci/controller/pci-host-common.c:78` 的原因。

### 这个类比什么时候会误导你？

门牌号类比漏掉了最难的一半：**桥**。

楼里如果只有平层的房间，编号很简单。但 PCI 是树，每个桥有一个"转发窗口"，桥只转发落在窗口内的地址。这意味着：

- 桥的窗口必须**覆盖它下面所有设备的 BAR**。
- 而桥的窗口本身也是一段地址，需要在**它的父桥**窗口里分配。
- 桥的窗口对齐要求比 BAR 更粗（MEM 窗口 1 MB 对齐，IO 窗口 4 KB 对齐）。

所以不能一遍扫完就分配 —— 必须先自底向上算出每个桥要多大，再自顶向下真正分配。这就是 §5。

第二处误导：类比暗示"声明需求"是设备主动做的。实际是内核**探测**出来的（写全 1 再读回）。这个探测过程本身会短暂改变 BAR 的值，所以必须在设备未使能时做 —— 又一处对"配置空间独立于 MMIO"的依赖。

---

## §5 为什么资源分配必须"先自底向上算尺寸，再自顶向下分配"？

### 先举个例子

给一支旅行团订酒店。团里有 3 个小组，每组人数不同，每组内部又分成若干家庭，每个家庭要一个房间。

酒店按"楼层"卖 —— 你得整层整层地订，一层 10 间。

现在问：你该先订几层？

答案是你**必须先把每个家庭数清楚**，加起来算出每组要几间，再算出总共要几层。反过来 —— 先订 3 层再往里塞人 —— 一定会出现"某组人多了塞不进"或者"订多了浪费"。

而"订"这个动作又只能自顶向下：得先确定订了 3 层、是哪 3 层，才能说"第 1 组住 5 层的 1-4 号房"。

### 回到代码

内核就是这两遍：

```c
/* drivers/pci/setup-bus.c:2061-2076 */
again:
        /*
         * Last try will use add_list, otherwise will try good to have as must
         * have, so can realloc parent bridge resource
         */
        if (tried_times + 1 == pci_try_num)
                add_list = &realloc_head;                      /* :2066-2067 */
        /*
         * Depth first, calculate sizes and alignments of all subordinate buses.
         */
        __pci_bus_size_bridges(bus, add_list);                 /* :2071 */

        pci_root_bus_distribute_available_resources(bus, add_list);  /* :2073 */

        /* Depth last, allocate resources and update the hardware. */  /* :2075 */
```

`:2069` 和 `:2075` 两行注释就是这一节的全部：**Depth first 算尺寸，Depth last 分配**。

第一遍的递归结构：

```c
/* drivers/pci/setup-bus.c:1209-1223 */
list_for_each_entry(dev, &bus->devices, bus_list) {
        struct pci_bus *b = dev->subordinate;
        if (!b)
                continue;                                      /* :1211-1212 */
        switch (dev->hdr_type) {
        case PCI_HEADER_TYPE_BRIDGE:
        default:
                __pci_bus_size_bridges(b, realloc_head);        /* :1221 —— 先递归到底 */
                break;
        }
}
```

先把所有子总线递归算完（`:1221`），回来才算自己这一层需要多大 —— 尺寸信息是从叶子往根冒上来的。`pbus_size_io()` `drivers/pci/setup-bus.c:874` 和 `pbus_size_mem()` `drivers/pci/setup-bus.c:985` 是每层实际做加法和对齐的地方。

### 这个类比什么时候会误导你？

酒店类比假设"人数是确定的"。PCI 里不是 —— 有两类不确定：

**一，热插拔预留。** 空的热插拔槽位现在没设备，但插进来就需要窗口。窗口是桥上的寄存器，改它意味着整条子树的地址重排，不能在运行时随便动。所以必须**提前预留**。这就是 07 §10.1 那四个参数的用途：`pci=hpiosize=` `drivers/pci/pci.c:6945-6946`、`hpmmiosize=` `:6947-6948`、`hpmmioprefsize=` `:6949-6950`、`hpbussize=` `:6954-6956`。

**二，"想要"和"必须"的区别。** 有些资源是可选的（比如 Resizable BAR 想要更大的窗口，07 §4.4）。内核的做法是**分两类，试多轮**：

```c
/* drivers/pci/setup-bus.c:2052-2058 */
enable_local = pci_realloc_detect(bus, pci_realloc_enable);
if (pci_realloc_enabled(enable_local)) {
        int max_depth = pci_bus_get_depth(bus);
        pci_try_num = max_depth + 1;                           /* :2056 */
        dev_info(&bus->dev, "max bus depth: %d pci_try_num: %d\n",
                 max_depth, pci_try_num);                      /* :2057-2058 */
}
```

轮数 = 树的深度 + 1。每一轮先按"只满足必须的"试，失败就释放一层桥的窗口重算（`rel_type = leaf_only` `drivers/pci/setup-bus.c:2045`，逐轮放宽），最后一轮才把 `add_list` 接上去让"想要的"也参与（`:2066-2067`）。`pci=realloc` / `realloc=off`（`drivers/pci/pci.c:6929-6932`）控制的就是这个开关。

⚠️ 这就是为什么 07 §11 说"资源分配日志看不懂就开 `drivers/pci/setup-bus.c` 的 dyndbg" —— 你看到的不是一次分配，是最多 `depth+1` 轮的试错，每轮的中间结果都会打出来。看到一堆 `no space for` 后面又跟着成功的 `assigned`，是正常的，不是 bug。

那行 `dev_info` `:2057-2058` 是免费的 —— 它 `pci_realloc_enabled` 为真时无条件打，所以 `dmesg | grep 'pci_try_num'` 能直接告诉你这台机器试了几轮。

---

## §6 MSI 为什么是"往内存写一个值"，而不是"拉一根线"？

### 先举个例子

一栋楼里有 200 个房间，每个房间都可能需要叫保安。

方案甲（INTx）：从每个房间拉一根电铃线到保安室。200 根线布不下，于是四个房间共用一根 —— 铃响了保安得挨个房间敲门问"是你按的吗"。而且线是**电平触发**的：某个房间的按钮卡住了，铃一直响，保安室别的铃全被盖住。

方案乙（MSI）：保安室墙上挂一个**信箱**，房间要叫保安就往信箱投一张写着自己房号的纸条。信箱地址所有房间都知道，纸条内容各不相同。保安看纸条就知道是谁，不需要挨个问。而且是**边沿性质**的：投了就是投了，不会"一直投"。

第二个方案的关键洞察：**楼里已经有一套"往任意地址送东西"的机制了**（PCI 的内存写事务），为什么还要再拉一套专门的中断线？直接复用。

### 回到代码

MSI 的全部实现就是"把中断变成一次内存写"：

```c
/* include/linux/msi.h:54 */
struct msi_msg {
        ...
};
```

三个字段：`address_lo`、`address_hi`、`data`。地址是 GIC ITS 的门铃寄存器（arm64 上），data 是这个向量的编号。

写进硬件的地方分两条路：

```c
/* drivers/pci/msi/msi.c:151 */
void __pci_write_msi_msg(struct msi_desc *entry, struct msi_msg *msg)
{
        ...
        /* MSI-X：写 BAR 里的表项 */
        writel(msg->address_lo, base + PCI_MSIX_ENTRY_LOWER_ADDR);   /* :176 */
        writel(msg->data, base + PCI_MSIX_ENTRY_DATA);               /* :178 */
        ...
        /* MSI：写配置空间的 capability */
        pci_write_config_dword(dev, ..., msg->address_lo);           /* :195 */
        pci_write_config_word(dev, ..., msg->data);                  /* :200 */
}
```

两条路的区别正好解释了 MSI 和 MSI-X 的所有差异：

| | MSI | MSI-X |
|---|---|---|
| 消息存在哪 | 配置空间的 capability，`include/uapi/linux/pci_regs.h:313` 起 | BAR 里的表，表偏移在 `PCI_MSIX_TABLE` `include/uapi/linux/pci_regs.h:327` |
| 存几份 | **一份地址 + 一份 data** | 每向量一份 |
| 多向量怎么做 | data 的低几位由硬件递增 ⇒ **向量号必须连续** | 每项独立 ⇒ 任意 |
| 最多几个 | 32 | 2048 |
| 单独屏蔽某个向量 | 可选功能，很多设备没实现 | 每项都有 mask 位 |

"向量号必须连续"这一条是 05 §7.2 那条纪律的根源，也是 `pci.rst:326-330` 专门写一段的原因。

`pci_write_msi_msg()` 的导出在 `drivers/pci/msi/msi.c:223`，屏蔽/解屏蔽是 `pci_msi_mask_irq()` `drivers/pci/msi/msi.c:104` 和 `pci_msi_unmask_irq()` `drivers/pci/msi/msi.c:116`。

### 这个类比什么时候会误导你？

信箱类比漏掉了 MSI 最重要的**副作用**，而这个副作用比"省线"值钱得多。

`Documentation/PCI/pci.rst:345-355` 列了用 MSI 的两个理由。第一个是"独占向量"（`:347-349`），符合信箱类比。第二个不符合：

```
（pci.rst:351-355 的意思）
MSI 是一次内存写事务，它和设备之前发出的 DMA 写在同一条通路上，
且 PCI 保证同一方向的写事务不会乱序。所以中断到达时，
DMA 数据一定已经对 CPU 可见 —— 驱动可以省掉那次"假读 MMIO 寄存器
以刷新 DMA"的操作。
```

这是**顺序保证**，不是省线。INTx 是一根独立的线，它和 DMA 数据走不同通路，中断可能比数据先到 —— 所以 INTx 驱动的 handler 第一件事往往是读一个无关寄存器来"挤"出未完成的 DMA 写。MSI 把中断塞进数据通路，这个假读就不需要了。

信箱类比里没有"纸条和快递走同一辆车所以纸条到了快递必然也到了"这层意思。但这恰恰是 MSI 在性能上真正的贡献。

第二处误导：类比说"信箱地址所有房间都知道"。实际上每个设备的 MSI 地址**可以不同**，而且必须经过 IOMMU/ITS 的 DeviceID 映射 —— 06 §5 那个 `msi-map = <0x0 &gic 0x1000 0x1000>` 就是在告诉 ITS"这个 RID 对应哪个 DeviceID"。映射错了的表现是中断永远不来，而 `ls msi_irqs/` 却显示向量分配成功（07 §2.6）。这是 arm64 上最难查的一类 PCI 问题，因为软件侧一切正常。

---

## §7 为什么 `pci_dev` 同时挂在两棵树上？

### 先举个例子

一个人在公司里同时存在于两套结构中：

- **行政汇报线**：他向组长汇报，组长向总监汇报，总监向 CEO 汇报。
- **办公位置**：他坐在 3 楼 A 区 12 号工位，工位属于 A 区，A 区属于 3 楼。

这两套结构**通常吻合但不必须吻合**。同组的人可能坐在不同楼层；同一片工位区可能坐着三个组的人。你不能用一套结构表达另一套 —— 想知道"火警时该疏散哪些人"要看位置，想知道"审批谁签字"要看汇报线。

### 回到代码

`pci_dev` 的头几个字段就是这两套：

```c
/* include/linux/pci.h:330-332 */
struct pci_dev {
        struct list_head bus_list;      /* Node in per-bus list */     /* :331 */
        struct pci_bus  *bus;           /* Bus this device is on */    /* :332 */
        ...
        struct device   dev;                                           /* 驱动核心的那一套 */
```

| 树 | 节点类型 | 父指针 | 子链表 | 谁在遍历它 |
|---|---|---|---|---|
| PCI 拓扑树 | `struct pci_bus` `include/linux/pci.h:652` | `parent` `include/linux/pci.h:654` | `children` `:655` + `devices` `:656` | PCI 核心自己：枚举、资源分配、复位 |
| 驱动核心设备树 | `struct device` | `dev.parent` | `dev` 的 klist | 驱动核心：probe、电源管理、sysfs |

两套结构在**大多数情况下同构** —— `pci_dev` 的 `dev.parent` 指向它所在 `pci_bus` 的 `dev`。那为什么还要两套？

三个理由，都很实际：

**一，总线本身不是设备。** `pci_bus` 是一条总线，不是一个设备。它有 `struct device dev` 只是为了在 sysfs 里有个目录（`/sys/class/pci_bus/0000:01/`，07 §2.5），但它不参与 probe、不匹配驱动、没有 vendor ID。硬塞进 `struct device` 的父子关系里会污染驱动核心。

**二，PCI 核心需要按拓扑遍历，而驱动核心的树里混着别的东西。** 比如复位一条子树（07 §4.3 的 `reset_subordinate`）需要"这个桥下面的所有 PCI 设备"。走 `struct device` 的树会遍历到桥下面挂的 platform 设备、class 设备之类。走 `pci_bus->devices` 就干净。

**三，还有第三套结构。** 物理槽位：

```c
/* include/linux/pci.h:75-76 */
struct pci_slot {
        struct pci_bus  *bus;           /* Bus this slot is on */      /* :76 */
        ...
};
```

槽位和设备是**多对一**（一个槽位可以有多个 function），和总线是多对一。它在 sysfs 里是第三棵树：`/sys/bus/pci/slots/`，由 `pci_slots_kset` `drivers/pci/slot.c:15` 撑着（导出 `drivers/pci/slot.c:16`），挂在 `pci_bus_type` 的 kset 下面 `drivers/pci/slot.c:373-375`。设备挂进去的地方是 `drivers/pci/slot.c:268`。

再加上主机桥：

```c
/* include/linux/pci.h:569-571 */
struct pci_host_bridge {
        struct device dev;
        struct pci_bus *bus;            /* Root bus */                 /* :571 */
        ...
};
```

所以完整图景是**四种节点、三套指针**：

```
platform_device (dts 里那个 pcie3x1 节点)         ← 06 §7
     │ platform_set_drvdata
     ▼
pci_host_bridge  ──bus──►  pci_bus (root)
     │ dev.parent                │ devices
     ▼                           ▼
   ...                      pci_dev (桥)  ──subordinate──►  pci_bus (子)
                                 │                              │ devices
                                 │                              ▼
                            pci_slot ◄────────────────────  pci_dev (端点)
```

### 这个类比什么时候会误导你？

公司类比假设两套结构**同时建立**。PCI 里不是 —— 它们分两个阶段建，中间隔着资源分配：

```
阶段一：pci_scan_root_bus_bridge()      ← 只建 pci_bus / pci_dev（拓扑树）
        ↓
阶段二：pci_bus_size_bridges()          ← §4 §5，此时驱动核心一无所知
        pci_bus_assign_resources()
        ↓
阶段三：pci_bus_add_devices()  drivers/pci/probe.c:3114
        ↓                        ← 才把 pci_dev 注册进驱动核心，才触发 probe
      驱动的 .probe() 被调用
```

这个顺序有个硬约束：**驱动的 `probe()` 一定在资源分配之后**。所以 05 篇讲的"probe 里 `pci_resource_start()` 拿到的地址是有效的"这件事是有保证的，不是运气。反过来，PCI 核心在阶段一二里**不能用任何 `struct device` 的设施** —— 不能 `devm_*`、不能 `dev_get_drvdata`、日志只能靠 `dev_*` 打在还未注册的 `dev` 上（这是允许的，`dev_printk` 不要求设备已注册）。

第二处误导：类比暗示两套结构地位平等。实际上 `pci_bus` 那棵树才是**权威**的 —— 它先建、由硬件决定、资源分配全靠它。`struct device` 那套是为了接入驱动核心而附加的一层。搞反了会写出"遍历 sysfs 找 PCI 拓扑"这种脆弱代码。

---

## §8 错误恢复为什么要分四步，不能一个回调搞定？

### 先举个例子

一栋楼的某一层失火。消防流程不是"喊一声大家自己想办法"，而是分段的：

1. **报警**：广播告诉这一层所有办公室"着火了"。此时电梯已停、这一层的电源可能已切断 —— 各办公室要做的是**停手、别再往打印机发任务**，不是收拾东西。
2. **确认可通行**：消防确认走廊可以走了，广播第二次。此时办公室可以拿门口的东西，但**还不能回工位**。
3. **本层重置**：这一层被断电重启、消防喷淋清理完毕。广播第三次。此时办公室里的**一切都回到初始状态**——保险箱开着、电脑关机、桌上东西被水泡了。要重新布置。
4. **恢复营业**：广播第四次，可以正常工作了。

为什么不能只广播一次"着火了你们自己处理"？因为**每一步硬件的可用性不同**。第 1 步时你不能读寄存器（总线可能已经隔离），第 3 步时你读寄存器能读但读到的是复位值。办公室没法自己判断现在处于哪一步。

### 回到代码

四步就是四个回调：

```c
/* include/linux/pci.h:842-859 */
struct pci_error_handlers {
        /* PCI bus error detected on this device */
        pci_ers_result_t (*error_detected)(struct pci_dev *dev,
                                          pci_channel_state_t error);   /* :844-845 */

        /* MMIO has been re-enabled, but not DMA */
        pci_ers_result_t (*mmio_enabled)(struct pci_dev *dev);           /* :848 */

        /* Device has been reset */
        pci_ers_result_t (*slot_reset)(struct pci_dev *dev);             /* :851 */

        /* PCI function reset prepare or completed */
        void (*reset_prepare)(struct pci_dev *dev);                      /* :854 */
        void (*reset_done)(struct pci_dev *dev);                         /* :855 */

        /* Device driver may resume normal operations */
        void (*resume)(struct pci_dev *dev);                             /* :858 */
};
```

注释就是四步的说明书。对上面的类比：

| 回调 | 类比里的哪一步 | 此刻能做 | 此刻不能做 |
|---|---|---|---|
| `error_detected` `include/linux/pci.h:844-845` | 第 1 次广播 | 设标志、停软件侧的提交 | 读写任何寄存器 |
| `mmio_enabled` `:848` | 第 2 次广播 | 读寄存器收集诊断 | 依赖 DMA |
| `slot_reset` `:851` | 第 3 次广播 | 完整重新初始化硬件 | 假设任何寄存器还保持着旧值 |
| `resume` `:858` | 第 4 次广播 | 恢复正常收发 | —— |

`error_detected` 多一个 `error` 参数（`pci_channel_state_t`），告诉你严重程度。05 §10.3 提过：**忽略这个参数是二次崩溃的常见来源** —— `pci_channel_io_perm_failure` 时设备已经永久没了，你还去写寄存器就会再触发一次错误。

`reset_prepare` / `reset_done`（`:854`、`:855`）不属于这条链 —— 它们是**主动复位**（07 §4.3 那三个 sysfs 文件、`pci_reset_function()`）的前后钩子，走的是另一条路径。放在同一个结构体里容易看混。

### 这个类比什么时候会误导你？

消防类比有一个致命的不对：**类比里有一个统一的指挥中心，PCI 里没有。**

谁来广播？在 arm64 上有三种可能：

| 谁发现错误 | 谁驱动这四步 | 前提 |
|---|---|---|
| 内核 AER 服务 | `drivers/pci/pcie/aer.c` | `pcie_ports=native` 或平台放权 |
| 内核 DPC 服务 | portdrv 的 DPC 分支 | 硬件支持 DPC |
| 固件 | 固件先处理完再通知内核，或者**根本不通知** | 平台实现 |

第三种情况下，你的四个回调**一个都不会被调**，而链路确实出错了 —— 07 §6.1 提的"AER 计数恒为 0 但链路一直在报错"就是这个。这时唯一的线索是设备行为异常（超时、读回全 1）。

⚠️ 第二处误导更隐蔽：`pci_ers_result_t` 的枚举**从 1 开始**：

```c
/* include/linux/pci.h:819-839 */
typedef unsigned int __bitwise pci_ers_result_t;
/* 六个取值分别在 :823 :826 :829 :832 :835 :838，没有 0 */
```

所以回调里写 `return 0` 是**非法的**，会被上层当成未定义值。而因为 `__bitwise` 是 sparse 的注解、不是 C 类型系统的一部分，编译器**不报错** —— 只有 `make C=1` 的 sparse 检查能发现。这是整个 PCI API 里最容易犯且最难发现的错误之一。

第三处：类比里"办公室"是被动的。实际上每个回调的**返回值会影响流程**：所有驱动都返回 `PCI_ERS_RESULT_CAN_RECOVER` 才会进下一步，有一个返回 `PCI_ERS_RESULT_DISCONNECT` 整条链就转向"放弃这个设备"。也就是说**一个写得不对的驱动能让同一条子树上别的设备也恢复不了**。

`Documentation/PCI/pci-error-recovery.rst:1` 那 426 行是这一节的权威文档，但 05 §10.4 提过它的 powerpc-EEH 偏向：文里很多"平台会怎么做"的描述来自 EEH，arm64 上的实际行为要看 `drivers/pci/pcie/aer.c` 和 DPC 的代码。

---

## §9 阅读下一篇

这一篇的八个问题分别是哪几篇的"为什么"层：

| 本篇 | 对应的"是什么" |
|---|---|
| §1 §2 总线型 vs provider 型、两条总线 | 01 篇（API 全景）、03 篇（注册流程） |
| §3 配置空间是独立地址空间 | 02 篇（`pci_ops`）、05 §3（配置空间访问） |
| §4 §5 BAR 分配与两遍法 | 03 篇（枚举流程）、05 §5（资源 API） |
| §6 MSI 是内存写 | 05 §7（中断）、06 §5（`msi-map`） |
| §7 三套结构 | 02 篇（数据结构关系图） |
| §8 错误恢复四步 | 05 §10（回调契约） |

按顺序读的话：**00（速览）→ 08（本篇，建立直觉）→ 02（结构）→ 01（API）→ 05（写驱动）**，需要改主机桥或 dts 再看 06，出问题看 07，怀疑某条结论看 99。

---

## §10 常见误解对照表

| 常见说法 | 实际情况 | 依据 |
|---|---|---|
| PCI 和 clk/pinctrl 一样是"资源提供者" | PCI 是唯一注册 `bus_type` 的，管的是设备生命周期不是资源映射 | `drivers/pci/pci-driver.c:1685`，§1 |
| dts 里要写出 PCI 设备 | dts 只描述主机桥，桥下全靠枚举 | 06 §1，§1 结尾 |
| 配置空间就是一段特殊的 MMIO | 它用 `(bus,devfn,where)` 位置寻址，是解决"先通信还是先分地址"的鸡蛋问题的唯一手段 | `include/linux/pci.h:778-784`，§3 |
| BAR 的地址由固件/设备决定 | arm64 上内核无条件重算重分配 | `drivers/pci/controller/pci-host-common.c:78`，§4 |
| 资源分配是一遍扫描完成的 | 最多 `树深度+1` 轮试错，每轮两遍（算尺寸、分配） | `drivers/pci/setup-bus.c:2056`，§5 |
| dmesg 里有 `no space for` 就是失败了 | 可能是中间某一轮的试探，后面还有 `assigned` | §5 |
| MSI 的好处是省中断线 | 更重要的是它和 DMA 同通路，带来顺序保证，能省掉 handler 里的假读 | `Documentation/PCI/pci.rst:351-355`，§6 |
| MSI 和 MSI-X 只差向量数量 | 差在消息存哪：MSI 存配置空间一份（向量号必须连续），MSI-X 存 BAR 里每项一份 | `drivers/pci/msi/msi.c:176`/`:195`，§6 |
| `ls msi_irqs/` 有文件说明中断通了 | 只说明向量分配成功；`msi-map` 配错时中断永远不来 | 07 §2.6，§6 |
| `pci_dev` 的父子关系就是 sysfs 的父子关系 | 有三套结构（`pci_bus` 拓扑树、`struct device` 树、`pci_slot`），拓扑树才是权威 | `include/linux/pci.h:331-332`，§7 |
| 驱动 probe 时资源可能还没分配好 | `pci_bus_add_devices()` 在资源分配之后才调，有硬保证 | `drivers/pci/probe.c:3114`，§7 |
| 错误恢复回调里可以随便读寄存器 | `error_detected` 阶段总线可能已隔离；`slot_reset` 后寄存器是复位值 | `include/linux/pci.h:844-851`，§8 |
| 回调 `return 0` 表示成功 | `pci_ers_result_t` 枚举从 1 开始，0 是未定义值，编译器不报错 | `include/linux/pci.h:819-839`，§8 |
| 一个驱动的错误处理写错只影响它自己 | 返回 `DISCONNECT` 会让整条恢复链放弃，影响同子树的别的设备 | §8 |
| 没有 AER 日志说明链路没问题 | 固件可能把 AER 吃掉了，计数恒为 0 | 07 §6.1，§8 |
