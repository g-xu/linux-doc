---
title: 总览
---

# PCI 子系统总览

本篇是其余九篇的浓缩，不引入新结论。每一节末尾给出对应的详篇位置，需要证据链就去 99 篇。

代码基线 Linux 6.1.158，arm64，ACPI 相关路径一律裁掉（裁剪清单在 99 §2.7）。

---

## §1 一句话定位

PCI 子系统是内核里**唯一注册了 `bus_type` 的那一类子系统** —— 它管的不是"编号到硬件资源的映射"，而是一整套设备的生命周期：枚举出来、分配地址、匹配驱动、供电、复位、出错恢复、拔掉。

```c
/* drivers/pci/pci-driver.c:1685-1699 */
struct bus_type pci_bus_type = {
        .name           = "pci",
        .match          = pci_bus_match,
        .probe          = pci_device_probe,
        .remove         = pci_device_remove,
        .shutdown       = pci_device_shutdown,
        .dma_configure  = pci_dma_configure,
        ...
};
EXPORT_SYMBOL(pci_bus_type);                          /* :1700 */
```

本项目做过的 23 个子系统里，前 22 个（pinctrl、gpio、clk、dmaengine、regulator、mailbox、reset、cpufreq、remoteproc、irq、cdev、of、timer、virtio、regmap、phy、watchdog、tty、pwm、dma-buf、mfd、dma-mapping）全是 provider/consumer 模式：核心是一张全局表加一把锁，加上 dts 里的 phandle 解析。PCI 是唯一的例外。

这个差别不是规模问题，是**问题域**问题：前 22 个的消费者是别的驱动，PCI 的"消费者"就是设备本身。详见 08 §1。

规模：`drivers/pci/` 共 **171 个 `.c` + 20 个 `.h`，140042 行**。核心层约 **19966 行 / 19 个文件**，对外导出 **232 个符号**（92 个 `_GPL` + 140 个非 GPL）。主头文件 `include/linux/pci.h` **2597 行**。

---

## §2 问题背景与核心抽象

### §2.1 问题域：一块板子上的四个鸡蛋问题

PCI 要解决的不是"怎么访问设备"，而是四个互相纠缠的启动期难题：

| 难题 | 为什么难 | PCI 的解法 |
|---|---|---|
| 先通信还是先分地址 | 要告诉设备它的地址，得先能和设备说话；要和设备说话，得先有地址 | **配置空间**：用 `(bus, devfn, offset)` 位置寻址，不需要分配 |
| 地址由谁定 | 设备出厂默认值全一样，各自选号必然冲突 | 设备只**声明**大小和对齐（BAR 的硬连线低位），内核统一分配 |
| 桥的窗口多大 | 桥的窗口要覆盖子树全部 BAR，而窗口本身又要在父桥窗口里分配 | **两遍法**：自底向上算尺寸，自顶向下分配 |
| 200 个设备怎么送中断 | 拉线拉不过来，共享线又分不清是谁、还会被卡住的线盖住 | **MSI**：把中断变成一次内存写事务 |

四个解法对应 08 篇的 §3、§4、§5、§6 四节。

### §2.2 核心抽象：一条总线 + 一棵拓扑树 + 一套访问回调

```
struct pci_ops           ← 怎么读写配置空间（主机桥提供）
     │
struct pci_host_bridge   ← 一个根，绑一个 platform_device（dts 节点）
     │
struct pci_bus (root) ──► pci_dev (桥) ──subordinate──► pci_bus ──► pci_dev (端点)
                                                                        │
                                                                   struct pci_driver
```

四层各管一件事：

| 抽象 | 定义 | 管什么 |
|---|---|---|
| `pci_ops` | `include/linux/pci.h:778-784` | 三个回调：`map_bus` / `read` / `write`。整个配置空间访问的唯一入口 |
| `pci_host_bridge` | `include/linux/pci.h:569-571` | 一个 PCI domain 的根，持有地址窗口列表和 `pci_ops` |
| `pci_bus` | `include/linux/pci.h:652-656` | 一条总线：`parent` / `children` / `devices` 三个指针撑起拓扑树 |
| `pci_dev` | `include/linux/pci.h:330-332` | 一个 function：`bus_list` 挂在总线上，`dev` 接入驱动核心 |

⚠️ 注意 `pci_bus` 和 `pci_dev` 是**两棵树的节点**，不是一棵：`pci_bus->devices` 是 PCI 拓扑树，`pci_dev->dev.parent` 是驱动核心设备树。再加 `pci_slot`（`include/linux/pci.h:75-76`）就是三套结构。拓扑树才是权威的。详见 08 §7、02 篇。

### §2.3 为什么不能绕过这个框架直接映射 BAR

理论上你可以在 dts 里写死一个 `reg`，用 platform 驱动 `ioremap` 它。实际上不行，四个原因：

1. **BAR 的地址不是固定的**。arm64 上内核无条件重新分配（`drivers/pci/controller/pci-host-common.c:78` 无条件设 `PCI_REASSIGN_ALL_BUS`），dts 里写的数字下一版内核就变了。
2. **设备默认不响应 MMIO**。得先写 `PCI_COMMAND` 的 `MEMORY` 位，而那是配置空间。
3. **中断号是运行时分出来的**。MSI 向量由 ITS 分配，dts 里没有。
4. **`dma_configure` 挂在 `bus_type` 上**（`drivers/pci/pci-driver.c:1698`）。绕过总线就拿不到正确的 IOMMU/DMA 域，DMA 地址会算错。

结论：PCI 设备**只能**写成 `pci_driver`。详见 05 §1。

### §2.4 用户态 ABI 边界

PCI 的用户态面在本项目做过的子系统里排第二大（tty 第一）：

| 面 | 文件 | 行数 | 初始化 |
|---|---|---|---|
| sysfs | `drivers/pci/pci-sysfs.c:1` | 1706 | `late_initcall(pci_sysfs_init)` `:1583` |
| procfs | `drivers/pci/proc.c:1` | 472 | `device_initcall(pci_proc_init)` `:472` |
| 系统调用 | `drivers/pci/syscall.c:1` | 137 | ⚠️ arm64 不注册，是死码 |

sysfs 侧约 40 个 ascii 属性 + 5 类二进制属性（`config` / `resourceN` / `rom` / `vpd` / legacy），分布在 12 个 `attribute_group` 里。procfs 侧是 `devices` 一行 18 字段 + 每设备一个配置空间节点 + 4 个 ioctl（`include/uapi/linux/pci.h:36-40`）。

⚠️ ABI 稳定性的代价具体可见：`drivers/pci/proc.c:384` 那行注释 `only print standard and ROM resources to preserve compatibility` 意味着 `/proc/bus/pci/devices` 里**永远看不到 SR-IOV BAR 和桥窗口** —— 多打一列会让老 `lspci` 解析错位。详见 07 篇。

### §2.5 与邻近机制的对比

| 场合 | 该用 | 不该用 PCI 的理由 |
|---|---|---|
| SoC 内部外设（UART、I2C 控制器） | platform 驱动 + dts | 没有配置空间，没有枚举 |
| PCI 设备直通给虚机 | vfio-pci（配 `driver_override` `drivers/pci/pci-sysfs.c:636`） | 你要的是"别让原生驱动碰它" |
| PCIe 主机桥本身 | platform 驱动，然后调 `pci_host_probe()` `drivers/pci/probe.c:3086` | 桥是 platform 设备，桥下面的才是 PCI 设备 |
| 只想读一次配置空间做诊断 | `/sys/bus/pci/devices/*/config` 的 pread | 不用写内核代码 |
| PCIe 端口的四项服务（PME/AER/DPC/Hotplug） | 第二条总线 `pcie_port_bus_type` `drivers/pci/pci-driver.c:1724` | 服务不是设备，混进 `pci_bus_type` 会污染 |

### §2.6 arm64 上的实际形态

四条与 x86 不同的事实，贯穿全套文档：

| 事实 | 后果 | 详篇 |
|---|---|---|
| 没有 ACPI（或不走 ACPI 路径） | 主机桥全靠 dts；`pci-acpi.c`(1518 行) 等整块裁掉 | 06 篇、99 §2.7 |
| `linux,pci-probe-only` 在 arm64 dts 里出现 **0 次** | 资源分配**永远**走重算路径，不接受固件分配 | 06 §1.2、08 §4 |
| 主机桥基本都是 `pci-host-generic` 或 DesignWare（dwc） | 前者 4 文件 450 行，后者 `dwc/Kconfig` 有 40 个 config | 06 §2、§7.2 |
| MSI 走 GIC ITS，靠 `msi-map` 做 RID→DeviceID 映射 | 映射错时向量分配成功但中断永不来 | 06 §5、07 §2.6 |
| 无 `HAVE_PCI_LEGACY` | `legacy_io` / `legacy_mem` 两个文件不存在 | 07 §3.5 |
| `pciconfig_read/write` 不在 syscall 表里 | 那 137 行是死码，工具别用 | 07 §8 |

---

## §3 概念模型：一句话记住每个角色

| 角色 | 一句话 | 定义处 |
|---|---|---|
| `pci_ops` | 主机桥交给核心的三个回调，是配置空间的唯一门 | `include/linux/pci.h:778` |
| `pci_host_bridge` | 一个 domain 的根，装着地址窗口和 `pci_ops` | `include/linux/pci.h:569` |
| `pci_bus` | 一条总线，撑起 `parent`/`children`/`devices` 三向指针 | `include/linux/pci.h:652` |
| `pci_dev` | 一个 function（不是一块卡）—— 双 function 的卡有两个 | `include/linux/pci.h:330` |
| `pci_slot` | 一个物理槽位，和 `pci_dev` 是一对多 | `include/linux/pci.h:75` |
| `pci_driver` | 驱动侧的注册体，12 个可选回调 | `include/linux/pci.h:920-938` |
| `pci_device_id` | 匹配表的一项，`modalias` 由它生成 | `include/linux/pci.h` 的 ID 段 |
| `pci_error_handlers` | 错误恢复的四步回调 + 两个复位钩子 | `include/linux/pci.h:842-859` |
| BAR | 设备**声明**地址需求的地方，值由内核填 | 配置空间 0x10..0x24 |
| 配置空间 | 位置寻址（不是地址寻址）的 256 或 4096 字节控制面 | `include/uapi/linux/pci_regs.h` |
| 桥窗口 | 桥只转发落在窗口内的地址，窗口大小由两遍法算出 | `drivers/pci/setup-bus.c` |
| MSI | 一次内存写事务代替一根中断线，附带 DMA 顺序保证 | `drivers/pci/msi/msi.c:151` |
| domain | 一套独立的 `(bus,devfn)` 编号空间，一个主机桥一个 | dts 的 `linux,pci-domain` |

三个最容易混的：

| 容易混 | 区别 |
|---|---|
| `pci_dev` vs 一块卡 | `pci_dev` 是 function。双口网卡是两个 `pci_dev`，共享一个 `pci_slot` |
| `pci_host_bridge` vs `pci_bus` | 前者是 dts 节点对应的软件对象，后者是它的根总线；`bridge->bus` 指过去（`include/linux/pci.h:571`） |
| `pci_bus_type` vs `pcie_port_bus_type` | 前者上挂真设备（硬件枚举来的），后者上挂 portdrv 人造的服务设备 |

---

## §4 数据结构摘要

### §4.1 核心层的文件分工

约 19966 行 / 19 个文件（下表 15 行，其中 `msi/` 一行含 5 个文件），按职责：

| 文件 | 行数 | 管什么 |
|---|---|---|
| `drivers/pci/pci.c:1` | 6998 | 通用 API：使能、电源、复位、能力查找、启动参数 |
| `drivers/pci/probe.c:1` | 3393 | 枚举：扫总线、建 `pci_dev`、读 BAR 需求 |
| `drivers/pci/setup-bus.c:1` | 2341 | 资源分配的两遍法与多轮试错 |
| `drivers/pci/pci-driver.c:1` | 1747 | 两个 `bus_type`、匹配与 probe/remove、PM 派发 |
| `drivers/pci/of.c:1` | 699 | dts 解析（8 个导出全是 `_GPL`） |
| `drivers/pci/access.c:1` | 596 | 配置空间读写的锁与宏生成 |
| `drivers/pci/setup-res.c:1` | 522 | 单个 BAR 的写回与对齐 |
| `drivers/pci/bus.c:1` | 457 | 总线级增删、resource 遍历 |
| `drivers/pci/search.c:1` | 392 | 各种 `pci_get_*` 查找 |
| `drivers/pci/remove.c:1` | 173 | 设备/总线拆除 |
| `drivers/pci/host-bridge.c:1` | 101 | `pci_host_bridge` 的分配与访问器 |
| `drivers/pci/irq.c:1` | 76 | INTx 相关 |
| `drivers/pci/setup-irq.c:1` | 64 | 探测期的中断号赋值 |
| `drivers/pci/pci.h:1` | 808 | **私有**头，外部驱动不可见 |
| `drivers/pci/msi/`（5 文件） | 1599 | MSI/MSI-X，16 个导出集中在 `msi.c` |

用户态面另计：`pci-sysfs.c`(1706) + `proc.c`(472) + `syscall.c`(137)。

### §4.2 命名约定：看前缀就知道该找谁

| 前缀 | 含义 | 例 |
|---|---|---|
| `pci_` | 通用 API，绝大多数导出 | `pci_enable_device` |
| `pcie_` | 只对 PCIe 有意义 | `pcie_capability_read_word` |
| `pcibios_` | 架构可覆盖的 `__weak` 钩子 | `pcibios_get_phb_of_node` `drivers/pci/of.c:61` |
| `of_pci_` / `devm_of_pci_` | dts 解析 | `of_pci_get_max_link_speed` `drivers/pci/of.c:619` |
| `pci_host_` | 主机桥驱动用 | `pci_host_probe` `drivers/pci/probe.c:3086` |
| `pci_ecam_` | ECAM 通用实现 | `pci_ecam_create` `drivers/pci/ecam.c:27` |
| `pci_user_` | 用户态访问路径专用（宏生成，grep 不到） | `pci_user_read_config_byte` |
| `__pci_` | 内部或已持锁版本 | `__pci_reset_function_locked` |

⚠️ 双下划线前缀在 PCI 里**统一表示"调用者已持锁"**，不是"内部函数"。`pci_reset_function()` 和 `pci_reset_function_locked()` 是一对（`include/linux/pci.h:1362`/`:1363`），后者要求已持 `device_lock`。

### §4.3 三个"字段在哪个结构里"的判断

| 想要的东西 | 在哪 |
|---|---|
| BAR 的地址和长度 | `pci_dev->resource[]` `include/linux/pci.h:420` |
| 地址窗口列表（桥的 ranges） | `pci_host_bridge->windows`（06 §2.4 由 `pci_parse_request_of_pci_ranges()` 填） |
| 配置空间访问回调 | `pci_bus->ops`，从 `bridge->ops` 复制下来 |
| 主机桥私有数据 | `pci_host_bridge_priv()` `include/linux/pci.h:607`，也就是 `bus->sysdata` |
| AER 计数 | `pci_dev->aer_stats`，`drivers/pci/pcie/aer.c:383` 分配 |
| 复位方法列表 | `pci_dev->reset_methods[]`，0 结尾（`drivers/pci/pci.c:5265` 的注释） |

---

## §5 机制摘要

### §5.1 五个 initcall 的顺序

| 阶段 | 函数 | 位置 | 干什么 |
|---|---|---|---|
| `postcore_initcall` | `pcibus_class_init` | `drivers/pci/probe.c:109` | 注册 `pcibus_class`（`class_register()` `:107`） |
| `postcore_initcall` | `pci_driver_init` | `drivers/pci/pci-driver.c:1747` | `bus_register()` `:1740` 两条总线 |
| `device_initcall` | `pci_proc_init` | `drivers/pci/proc.c:472` | 建 `/proc/bus/pci`，并补扫已有设备 `:467-468` |
| `late_initcall` | `pci_sysfs_init` | `drivers/pci/pci-sysfs.c:1583` | 建 resource 文件和 legacy 文件 |
| `late_initcall` | `pci_resource_alignment_sysfs_init` | `drivers/pci/pci.c:6806` | `resource_alignment` 的运行时可写面 |

⚠️ `pci_proc_init` 是 `device_initcall`，而设备在 `postcore_initcall` 阶段就可能扫出来了 —— 所以它内部有个 `proc_initialized` 标志（检查点 `drivers/pci/proc.c:421-422`）加一个补扫循环。这是 initcall 顺序倒置的典型补丁写法。

### §5.2 主机桥驱动的完整启动路径

以 `pci-host-generic` 为例（06 §2.4）：

```
platform 驱动 probe
  └─ pci_host_common_probe()               drivers/pci/controller/pci-host-common.c:52
       ├─ of_device_get_match_data()                    :59   ← 拿 pci_ecam_ops
       ├─ devm_pci_alloc_host_bridge()                  :63
       ├─ of_pci_check_probe_only()                     :69   ← 读 of_chosen
       ├─ gen_pci_init()                                :72
       │    ├─ of_address_to_resource()                 :31   ← ECAM 窗口
       │    ├─ resource_list_first_type(...BUS)         :37   ← bus-range
       │    └─ pci_ecam_create()          drivers/pci/ecam.c:27
       │         ├─ bus-range 截断 + 警告                :54-61
       │         ├─ 资源冲突 → -EBUSY                    :69-75
       │         ├─ pci_remap_cfgspace()                 :82
       │         └─ ops->init()                          :87-91
       ├─ pci_add_flags(PCI_REASSIGN_ALL_BUS)           :78   ← arm64 默认
       ├─ bridge->sysdata / ops / msi_domain            :80-82
       └─ pci_host_probe()               drivers/pci/probe.c:3086
            ├─ pci_scan_root_bus_bridge()                :3091  ← 枚举（§5.3）
            ├─ pci_bus_size_bridges()                    :3107  ← 两遍法第一遍
            ├─ pci_bus_assign_resources()                :3108  ← 第二遍
            ├─ pcie_bus_configure_settings()             :3110-3111
            └─ pci_bus_add_devices()                     :3114  ← 才触发驱动 probe
```

⚠️ 桥的 `windows` 和 `dma_ranges` 在这条路之前就已经解析好了 —— `devm_of_pci_bridge_init()` `drivers/pci/of.c:591` 在 `devm_pci_alloc_host_bridge()` 内部被调，它同时装好 `swizzle_irq` `:596` 和 `map_irq` `:597`。详见 06 §6.1。

### §5.3 枚举

```
pci_scan_child_bus()          drivers/pci/probe.c:3031
  └─ 对每个 devfn: pci_scan_slot()        drivers/pci/probe.c:2691
       └─ 读 vendor ID，非 0xffff 则
            pci_scan_single_device()      drivers/pci/probe.c:2603
       └─ 是桥则 pci_scan_bridge_extend() drivers/pci/probe.c:1263 → 递归
```

⚠️ 一台机器上"同一个设备出现多次"的头号原因：主机桥没过滤非 0 devfn。`pci-host-generic` 的过滤在 `pci_dw_valid_device()` `drivers/pci/controller/pci-host-generic.c:26`（`PCI_SLOT(devfn) > 0` 直接返回 false `:36-37`）。详见 06 §2.3。

### §5.4 资源分配：两遍法 + 多轮试错

```c
/* drivers/pci/setup-bus.c:2061-2075 */
again:
        if (tried_times + 1 == pci_try_num)
                add_list = &realloc_head;                /* :2066-2067 最后一轮才带"想要的" */
        /* Depth first, calculate sizes and alignments of all subordinate buses. */
        __pci_bus_size_bridges(bus, add_list);            /* :2071 */
        pci_root_bus_distribute_available_resources(bus, add_list);   /* :2073 */
        /* Depth last, allocate resources and update the hardware. */ /* :2075 */
```

轮数 = 树深度 + 1（`drivers/pci/setup-bus.c:2056`），每轮的深度信息由 `dev_info` 打出来 `:2057-2058`。第一遍的递归在 `__pci_bus_size_bridges()` `drivers/pci/setup-bus.c:1199`，实际加法在 `pbus_size_io()` `:874` 和 `pbus_size_mem()` `:985`。

⚠️ dmesg 里出现 `no space for` 后面又跟着成功的 `assigned` 是**正常的**，那是中间某一轮的试探。详见 08 §5。

`PCI_PROBE_ONLY` 置位时整条路换成 `pci_bus_claim_resources()` `drivers/pci/setup-bus.c:1482`（只登记不重算），但 arm64 dts 里没人设这个标志。

### §5.5 中断

| 类型 | 消息存哪 | 向量号 | 单独屏蔽 |
|---|---|---|---|
| INTx | —— | 一根线，四设备共享 | 不能 |
| MSI | 配置空间 capability（`include/uapi/linux/pci_regs.h:313` 起） | **必须连续** | 可选功能，多数没实现 |
| MSI-X | BAR 里的表（偏移在 `PCI_MSIX_TABLE` `include/uapi/linux/pci_regs.h:327`） | 任意 | 每项都有 |

驱动侧只用两个函数：`pci_alloc_irq_vectors()`（⚠️ 是 inline，`include/linux/pci.h:1985-1990`，**不在 `Module.symvers` 里**）和 `pci_irq_vector()`（`include/linux/pci.h:1637` / `drivers/pci/msi/msi.c:1086`）。flags 恒要带 `PCI_IRQ_LEGACY`（`include/linux/pci.h:1068`，理由 `Documentation/PCI/pci.rst:335-338`），但 INTx 回退只在 `min_vecs == 1` 时才发生（条件在 `include/linux/pci.h:1661`）。

写消息的两条路在 `__pci_write_msi_msg()` `drivers/pci/msi/msi.c:151`：MSI-X 走 `writel` `:176`/`:178`，MSI 走 `pci_write_config_*` `:195`/`:200`。

⚠️ MSI 的第二个好处比"省线"重要：它和 DMA 走同一条通路，中断到达时 DMA 数据必已可见，驱动可以省掉 handler 里那次刷新用的假读（`Documentation/PCI/pci.rst:351-355`）。详见 08 §6。

### §5.6 错误恢复的四步

```c
/* include/linux/pci.h:842-859 */
struct pci_error_handlers {
        pci_ers_result_t (*error_detected)(struct pci_dev *dev,
                                          pci_channel_state_t error);   /* :844-845 */
        pci_ers_result_t (*mmio_enabled)(struct pci_dev *dev);           /* :848 */
        pci_ers_result_t (*slot_reset)(struct pci_dev *dev);             /* :851 */
        void (*reset_prepare)(struct pci_dev *dev);                      /* :854 */
        void (*reset_done)(struct pci_dev *dev);                         /* :855 */
        void (*resume)(struct pci_dev *dev);                             /* :858 */
};
```

| 回调 | 此刻能做 | 此刻不能做 |
|---|---|---|
| `error_detected` | 设标志、停软件侧提交 | 读写任何寄存器 |
| `mmio_enabled` | 读寄存器收诊断 | 依赖 DMA |
| `slot_reset` | 完整重新初始化 | 假设寄存器保持旧值 |
| `resume` | 恢复收发 | —— |

⚠️ 两个高频错误：一是忽略 `error` 参数（`pci_channel_io_perm_failure` 时再写寄存器会二次崩溃）；二是 `return 0` —— `pci_ers_result_t` 枚举**从 1 开始**（`include/linux/pci.h:819-839`），0 是未定义值，而 `__bitwise` 只有 sparse（`make C=1`）能查出来。详见 05 §10、08 §8。

`reset_prepare`/`reset_done` 不属于这条链，它们是主动复位的钩子。

---

## §6 端到端调用路径

### §6.1 一个 PCI 驱动的最小生命周期

```
模块加载
  └─ pci_register_driver()
       └─ 驱动核心用 pci_bus_type.match 逐个比对已有设备
            └─ pci_device_probe()          drivers/pci/pci-driver.c 的 .probe
                 └─ 驱动的 .probe(pdev, id)
                      ├─ pcim_enable_device()        ← 打开 devm 模式
                      ├─ pcim_iomap_regions()        ← 映射 BAR
                      ├─ dma_set_mask_and_coherent()
                      ├─ pci_alloc_irq_vectors(..., PCI_IRQ_MSIX | PCI_IRQ_MSI | PCI_IRQ_LEGACY)
                      ├─ devm_request_irq() × N
                      ├─ pci_set_master()
                      └─ 注册进上层子系统（netdev / blkdev / ...）
运行
  └─ 中断 → handler → DMA 完成 → 上层
卸载
  └─ 驱动的 .remove(pdev)                 ← 七步，顺序不能错（05 §9）
       ├─ 从上层子系统注销
       ├─ 停硬件（清 PCI_COMMAND 的 MASTER）
       ├─ 停中断（free_irq / pci_free_irq_vectors）
       ├─ 停 DMA，再释放 DMA 缓冲
       ├─ streaming 先，coherent 后
       ├─ iounmap
       └─ pci_disable_device() / pci_release_region()
```

⚠️ 卸载七步的原文在 `Documentation/PCI/pci.rst:361-373`，其中 `:413-414` 明说"several mature drivers didn't get this right"。用 `devm_*` / `pcim_*` 能自动掉大半，但**停硬件和释放中断向量这两步 devm 管不了**（05 §9.5）。

### §6.2 一次配置空间读的完整路径

```
用户态: pread("/sys/bus/pci/devices/0000:01:00.0/config", buf, 4, 0)
  └─ pci_read_config()          drivers/pci/pci-sysfs.c:716
       ├─ 权限截断：非 root 只给 64 字节         :721 :726-727
       ├─ pci_config_pm_runtime_get()             :740
       └─ pci_user_read_config_dword()            ← 宏生成，drivers/pci/access.c:239
            └─ bus->ops->read()                   ← include/linux/pci.h:782
                 └─ pci_generic_config_read()
                      └─ ops->map_bus() + readl   ← ECAM: drivers/pci/ecam.c:167
```

三处会挡你的地方：非 root 的 64 字节截断（07 §3.1）、写侧的 `security_locked_down(LOCKDOWN_PCI_ACCESS)` `drivers/pci/pci-sysfs.c:801`、procfs 那条路要 `CAP_SYS_RAWIO` `drivers/pci/proc.c:250`。

### §6.3 dts 到 `pci_dev` 的信息流

| dts 属性 | 解析函数 | 落到哪 |
|---|---|---|
| `reg` + `compatible` | `gen_pci_init()` `drivers/pci/controller/pci-host-common.c:23` | `pci_config_window` |
| `bus-range` | 同上 `:37`，⚠️ 可能被 ECAM 大小截断 `drivers/pci/ecam.c:54-61` | `cfg->busr` |
| `ranges` | `devm_of_pci_get_host_bridge_resources()` `drivers/pci/of.c:274` | `bridge->windows` |
| `dma-ranges` | 同上 | `bridge->dma_ranges` |
| `interrupt-map` | `of_irq_parse_pci()` `drivers/pci/of.c:418` | `pdev->irq`（INTx） |
| `msi-map` / `msi-parent` | `pci_host_bridge_of_msi_domain()` `drivers/pci/of.c:79` | MSI 域 |
| `linux,pci-domain` | `of_get_pci_domain_nr()` `drivers/pci/of.c:216` | domain 号 |
| `max-link-speed` | `of_pci_get_max_link_speed()` `drivers/pci/of.c:619` | 控制器私有字段 |
| `linux,pci-probe-only`（在 `/chosen`） | `of_pci_check_probe_only()` `drivers/pci/of.c:233` | `PCI_PROBE_ONLY` 标志 |

⚠️ `of_irq_parse_and_map_pci()` `drivers/pci/of.c:529` **解析失败返回 0 而不是负值**（`:536` 的注释 `Proper return code 0 == NO_IRQ`）—— 所以 `interrupt-map` 写错的表现是"`irq` 属性为 0，没有任何报错"。详见 06 §6.3。

---

## §7 与其他子系统的关系

| 子系统 | 关系 | 落点 |
|---|---|---|
| driver core | PCI 是它的一个 `bus_type` 客户，probe/PM/sysfs 全靠它 | `drivers/pci/pci-driver.c:1685` |
| of | 主机桥的全部配置来自 dts；`drivers/pci/of.c` 是薄封装 | 06 篇 |
| irq / irqchip | MSI 域挂在 GIC ITS 下，`msi-map` 做 RID→DeviceID | 06 §5 |
| dma / iommu | `bus_type.dma_configure` 挂 `pci_dma_configure` `drivers/pci/pci-driver.c:1698`；`iommu-map` 同 `msi-map` 语法 | 06 §5 |
| clk / reset / phy / regulator | 主机桥控制器驱动的**消费者**身份，与 PCI 核心无关 | 06 §7 |
| platform | 主机桥本身是 platform 设备 | 06 §2.4 |
| vfio | 通过 `driver_override` 抢设备，依赖 `reset_method` 选对复位方式 | 07 §4.3、§4.5 |
| sysfs / procfs | 三个用户态面 | 07 篇 |

一句话概括这张表：**PCI 核心向上是 driver core 的客户，向下是 of/irq/dma 的客户，只有主机桥那一层才和 clk/phy/reset 打交道。**

---

## §8 按角色的阅读指引

| 你要做的事 | 读哪几篇 | 顺序 |
|---|---|---|
| 写一个 PCI 设备驱动 | 05 → 01 → 02 | 05 是唯一必读的，其余按需查 |
| 写一个 arm64 主机桥驱动 | 06 → 03 → 04 | 先看 `pci-host-generic` 够不够用（06 §8） |
| 改 dts 让新板子上的 PCIe 跑起来 | 06 → 07 | 06 §7 是 rk3568 的逐行实例 |
| 查一个现场问题 | 07 → 06 | 07 §11 是症状对照表 |
| 理解为什么这么设计 | 08 → 02 | 08 不需要先读别的 |
| 评估某条结论可不可信 | 99 | 每条都有核对记录 |
| 只想快速有个印象 | 本篇 | —— |

推荐的完整路径：**00（本篇）→ 08（直觉）→ 02（结构）→ 01（API）→ 05（写驱动）**，然后按需 06 / 07 / 03 / 04，最后 99。

---

## §9 本篇的边界

这一篇**没有**覆盖的内容，以及它们在哪：

| 没讲 | 在哪 |
|---|---|
| 232 个导出符号的逐个说明 | 01 篇 |
| 每个结构体的字段级注释 | 02 篇 |
| 枚举、资源分配、probe 派发的逐行流程 | 03 篇 |
| SR-IOV、AER、ASPM、hotplug、P2PDMA 等可选特性 | 04 篇 |
| 驱动侧的十条纪律与"别用"清单 | 05 §12 |
| 四份 DT binding 文档的逐项解读 | 06 篇 |
| 40 个 sysfs 属性的逐个说明与可见性规则 | 07 篇 |
| 每条结论的核对方式、失败的核对、裁掉的 ACPI 路径 | 99 篇 |

三条**全套文档都不覆盖**的：

1. **x86 专属路径**。`pci=off|bios|nobios|conf1|conf2`（`Documentation/admin-guide/kernel-parameters.txt:4186-4200`）、MMCONFIG、PCI BIOS 一律不谈。
2. **ACPI 路径**。裁剪清单在 99 §2.7，包括 `pci-acpi.c`(1518 行)、`pcie/edr.c`(246 行)、`include/linux/pci-ecam.h:80-91` 那 10 个 ACPI 专用 ops 外部声明。
3. **端点模式（PCI Endpoint Framework）**。`Documentation/PCI/endpoint/pci-endpoint.rst:1`(231 行) 和 `Documentation/PCI/endpoint/pci-ntb-function.rst:1`(348 行) 描述的是"把 SoC 当成 PCI 设备"，和本套文档的主机侧视角是反的，04 篇只在特性清单里提一句。
