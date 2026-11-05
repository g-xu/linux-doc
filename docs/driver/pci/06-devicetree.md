---
title: 设备树
---

# pci 06：设备树与 arm64 主机桥

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

前面五篇讲的是"PCI 设备已经在那里了怎么用"。
本篇讲的是 arm64 上 **PCI 设备是怎么冒出来的** ——
设备树描述主机桥，主机桥驱动把 DT 翻成 `pci_host_bridge`，
核心再从那里开始枚举（03 篇 §2）。

⚠️ 本篇的读者是写主机桥驱动的人，以及要看懂板级 dts 的人。
只写端点驱动的话，05 篇够了。

---

## §1 为什么 PCI 需要设备树

PCI 是可枚举总线：设备的 ID、BAR 大小、能力链表全都能从配置空间读出来。
**唯一读不出来的是"配置空间自己在哪"** —— 这是个鸡生蛋问题。

设备树补的正是这一段，一共四类信息：

| 类别 | DT 属性 | 谁用 |
|---|---|---|
| 配置空间在哪 | `reg` + `compatible` | ⚠️ 主机桥驱动，`pci_ecam_create()` |
| 地址窗口（总线地址 ↔ CPU 地址） | `ranges` | `devm_of_pci_get_host_bridge_resources()` |
| INTx 中断怎么接到 GIC | `interrupt-map` / `interrupt-map-mask` | `of_irq_parse_and_map_pci()` |
| MSI 和 IOMMU 的 sideband 数据怎么算 | `msi-map` / `iommu-map` | ⚠️ 03 §9 的 `pci_dma_configure()` |

⚠️⚠️ 这四类之外的一切（设备有几个 BAR、支持不支持 MSI-X、
链路跑到几代）**都不该写进 dts**，从配置空间读。
dts 里出现端点设备的描述通常是设计错误 —— 例外见 §7.3。

### §1.1 权威文档

| 文档 | 行数 | 内容 |
|---|---|---|
| `Documentation/devicetree/bindings/pci/pci.txt` | 84 | ⚠️ Linux 自己加的四个属性 + P2P 桥节点 |
| `Documentation/devicetree/bindings/pci/host-generic-pci.yaml` | 177 | ⚠️⚠️ **最该读的一份**，CAM/ECAM 的偏移算法在 `Documentation/devicetree/bindings/pci/host-generic-pci.yaml:26-34` |
| `Documentation/devicetree/bindings/pci/pci-msi.txt` | 220 | `msi-map` / `msi-map-mask` / `msi-parent`，格式定义在 `Documentation/devicetree/bindings/pci/pci-msi.txt:32-44` |
| `Documentation/devicetree/bindings/pci/pci-iommu.txt` | 171 | `iommu-map` / `iommu-map-mask`，格式定义在 `Documentation/devicetree/bindings/pci/pci-iommu.txt:35-42` |

⚠️ `Documentation/devicetree/bindings/pci/pci.txt:3-9` 把真正的规范指向了两份外部 PDF：
`PCI Bus Binding to IEEE Std 1275-1994` 和
`Open Firmware Recommended Practice: Interrupt Mapping`。
**内核文档只描述增量，`ranges` 和 `interrupt-map` 的格式定义在那两份里。**

### §1.2 arm64 上的实际规模

| 统计 | 数量 |
|---|---|
| `arch/arm64/boot/dts/` 里含 `device_type = "pci"` 的文件 | 71 |
| 含 `num-lanes` | 67 |
| 含 `dma-ranges` | 52 |
| 含 `iommu-map` | 23 |
| 含 `linux,pci-domain` | 22 |
| 含 `max-link-speed` | 19 |
| 含 `msi-map` | 12 |
| 含 `linux,pci-probe-only` | ⚠️⚠️ **0** |
| `Documentation/devicetree/bindings/pci/` 条目 / 其中 `.yaml` | 72 / 43 |

⚠️⚠️ `linux,pci-probe-only` 在 arm64 dts 里 **一个都没有** ——
它是给 chosen 节点、由固件或 bootloader 注入的（§4.3），不是板级 dts 的内容。

⚠️ 43/72 已经转成 yaml，剩下 29 个还是 `.txt`。
新写绑定必须用 yaml（会被 `make dt_binding_check` 校验）。

---

## §2 最小的一条路：pci-host-generic

固件已经把一切配好、内核只需要读的场景，用 `pci-host-generic`。
⚠️ 这是理解整条链最好的入口，因为它只有 89 行。

### §2.1 三个文件，450 行

| 文件 | 行数 | 职责 |
|---|---|---|
| `drivers/pci/controller/pci-host-generic.c` | 89 | ⚠️ 只有 `compatible` 表和 `ops` 表 |
| `drivers/pci/controller/pci-host-common.c` | 101 | probe/remove 的通用骨架 |
| `drivers/pci/ecam.c` | 233 | ECAM 配置空间窗口的创建与映射 |
| `include/linux/pci-ecam.h` | 98 | ⚠️⚠️ **唯一对外的 ECAM 头文件** |

Kconfig 依赖链：`PCI_HOST_GENERIC`（`drivers/pci/controller/Kconfig:90`）
`select PCI_HOST_COMMON`（`:93`）→ `PCI_HOST_COMMON`（`:86`）
`select PCI_ECAM`（`:88`）→ `PCI_ECAM`（`drivers/pci/Kconfig:127`）。

⚠️ 另有 5 个控制器也 `select PCI_HOST_COMMON`
（`drivers/pci/controller/Kconfig:110`、`:198`、`:206`、`:308`、`:330`）——
即 xilinx-cpm、thunder-pem、thunder-ecam、microchip、mt7621。

### §2.2 `compatible` 表：五个 + 两套 ops

```c
/* drivers/pci/controller/pci-host-generic.c:59-76 */
{ "pci-host-cam-generic",           &gen_pci_cfg_cam_bus_ops },  /* :60-61 */
{ "pci-host-ecam-generic",          &pci_generic_ecam_ops },     /* :63-64 */
{ "marvell,armada8k-pcie-ecam",     &pci_dw_ecam_bus_ops },      /* :66-67 */
{ "socionext,synquacer-pcie-ecam",  &pci_dw_ecam_bus_ops },      /* :69-70 */
{ "snps,dw-pcie-ecam",              &pci_dw_ecam_bus_ops },      /* :72-73 */
```

CAM 与 ECAM 的差别只有 `bus_shift`：

| 机制 | `bus_shift` | 每功能配置空间 | 出处 |
|---|---|---|---|
| CAM（传统 PCI） | 16 | 256 B | `pci-host-generic.c:18` |
| ECAM（PCIe） | ⚠️ 20（默认，不用写） | 4 KB | `include/linux/pci-ecam.h:23` |

```c
/* include/linux/pci-ecam.h:23-37 —— ECAM 偏移的完整定义 */
#define PCIE_ECAM_BUS_SHIFT     20                 /* :23 */
#define PCIE_ECAM_DEVFN_SHIFT   12                 /* :24 */
#define PCIE_ECAM_REG_MASK      0xfff              /* :28 限制在 4K 内 */
#define PCIE_ECAM_OFFSET(bus, devfn, where) \
        (PCIE_ECAM_BUS(bus) | PCIE_ECAM_DEVFN(devfn) | PCIE_ECAM_REG(where))  /* :34-37 */
```

⚠️ `Documentation/devicetree/bindings/pci/host-generic-pci.yaml:26-34` 用算式写了同一件事，
可以拿来交叉验证：CAM 是 `bus<<16 | dev<<11 | fn<<8 | reg`，
ECAM 是 `bus<<20 | dev<<15 | fn<<12 | reg`。

### §2.3 那个 DesignWare 过滤器：一个具体的硬件缺陷

```c
/* drivers/pci/controller/pci-host-generic.c:26-40 */
static bool pci_dw_valid_device(struct pci_bus *bus, unsigned int devfn)
{
        struct pci_config_window *cfg = bus->sysdata;
        /*
         * The Synopsys DesignWare PCIe controller in ECAM mode will not filter
         * type 0 config TLPs sent to devices 1 and up on its downstream port,
         * resulting in devices appearing multiple times on bus 0 unless we
         * filter out those accesses here.                       :30-35
         */
        if (bus->number == cfg->busr.start && PCI_SLOT(devfn) > 0)
                return false;                                  /* :36-37 */
        return true;
}
```

⚠️⚠️ 这段值得记住，因为它解释了一类真实现场：
**同一个设备在 `lspci` 里出现多次。**
根因是控制器没过滤发给 device 1 及以上的 type 0 配置事务，
于是 bus 0 上每个 slot 都"看到"同一个设备。
软件侧的补救就是这 3 行。

⚠️ `Documentation/devicetree/bindings/pci/host-generic-pci.yaml:75-78` 补了另一半：
如果 IP 综合时最小 ATU 窗口是 64 KB，**通用 ECAM 驱动根本带不了**，
必须用完整的 dwc 驱动。

### §2.4 `pci_host_common_probe()`：34 行的骨架

```c
/* drivers/pci/controller/pci-host-common.c:52-86 */
int pci_host_common_probe(struct platform_device *pdev)
{
        ops = of_device_get_match_data(&pdev->dev);            /* :59 拿 ecam_ops */
        if (!ops)
                return -ENODEV;

        bridge = devm_pci_alloc_host_bridge(dev, 0);           /* :63 */
        if (!bridge)
                return -ENOMEM;
        platform_set_drvdata(pdev, bridge);                    /* :67 */

        of_pci_check_probe_only();                             /* :69 见 §4.3 */

        cfg = gen_pci_init(dev, bridge, ops);                  /* :72 解析 reg，建 ECAM 窗口 */
        if (IS_ERR(cfg))
                return PTR_ERR(cfg);

        /* Do not reassign resources if probe only          :76 */
        if (!pci_has_flag(PCI_PROBE_ONLY))
                pci_add_flags(PCI_REASSIGN_ALL_BUS);           /* :78 */

        bridge->sysdata = cfg;                                 /* :80 */
        bridge->ops = (struct pci_ops *)&ops->pci_ops;         /* :81 */
        bridge->msi_domain = true;                             /* :82 */

        return pci_host_probe(bridge);                         /* :84 交给核心，见 03 §2 */
}
EXPORT_SYMBOL_GPL(pci_host_common_probe);                      /* :86 */
```

四个要点：

1. ⚠️ **`devm_pci_alloc_host_bridge()` 里已经调过 `devm_of_pci_bridge_init()`**，
   所以到 `:63` 返回时 `bridge->windows` 和 `bridge->dma_ranges` 已经填好了（§3）。
   probe 里看不到解析 `ranges` 的代码，不是漏了。
2. ⚠️⚠️ `:78` 那行是 arm64 上的默认行为：**不是 probe-only，就重排所有总线号**。
   这直接决定了 03 §2.2 那个三趟总线号算法走哪条分支。
3. `:82` 无条件把 `msi_domain` 设为 true —— MSI domain 从 `msi-map`/`msi-parent`
   现查（§5），不在这里判。
4. ⚠️ `:84` 之后主机桥驱动就退出舞台了，剩下全是核心的事。

`pci_host_common_remove()`（`:88-99`）是标准四步：
`pci_lock_rescan_remove()` → `pci_stop_root_bus()` →
`pci_remove_root_bus()` → `pci_unlock_rescan_remove()`。
⚠️ 那把锁就是 03 §7.4 的 `pci_rescan_remove_lock`。

### §2.5 `pci_ecam_create()`：一个尺寸裁剪的细节

```c
/* drivers/pci/ecam.c:54-61 */
bus_range = resource_size(&cfg->busr);
bus_range_max = resource_size(cfgres) >> bus_shift;
if (bus_range > bus_range_max) {
        bus_range = bus_range_max;
        cfg->busr.end = busr->start + bus_range - 1;
        dev_warn(dev, "ECAM area %pR can only accommodate %pR (reduced from %pR desired)\n",
                 cfgres, &cfg->busr, busr);                    /* :59-60 */
}
```

⚠️⚠️ **`bus-range` 会被 `reg` 的实际大小静默裁短**，只留一条 warn。
现场表现是"dts 里写了 `bus-range = <0 0xff>`，但只枚举到 bus 3 就没了"。
`dmesg | grep "can only accommodate"` 一查就知道。

⚠️ 另一个 32 位/64 位分叉在 `ecam.c:19`：
`static const bool per_bus_mapping = !IS_ENABLED(CONFIG_64BIT);`
arm64 走 `else` 分支（`:81-85`），**一次 `pci_remap_cfgspace()` 映射整个窗口**；
32 位平台才按总线逐个映射。所以 arm64 上 `cfg->win` 有效、`cfg->winp` 是 NULL。

⚠️ `pci_remap_cfgspace()`（`include/linux/io.h:83`）不是普通 `ioremap()` ——
配置空间要求非投机、非合并的访问属性。**用错会读到垃圾。**

### §2.6 `struct pci_config_window`：sysdata 的实体

```c
/* include/linux/pci-ecam.h:55-66 */
struct pci_config_window {
        struct resource                 res;        /* :56 ECAM 区间本身 */
        struct resource                 busr;       /* :57 覆盖的总线号 */
        unsigned int                    bus_shift;  /* :58 */
        void                            *priv;      /* :59 ⚠️ 控制器私有 */
        const struct pci_ecam_ops       *ops;       /* :60 */
        union {
                void __iomem            *win;       /* :62 64 位：单个映射 */
                void __iomem            **winp;     /* :63 32 位：每总线一个 */
        };
        struct device                   *parent;    /* :65 */
};
```

⚠️⚠️ 这个结构体被塞进 `pci_host_bridge->sysdata`（`pci-host-common.c:80`），
再通过 `pci_bus->sysdata` 传到每个 `map_bus()` 调用里。
**`bus->sysdata` 在 ECAM 控制器上就是 `pci_config_window *`** ——
`pci_dw_valid_device()` 第一行 (`pci-host-generic.c:28`) 就是这么用的。

`struct pci_ecam_ops`（`pci-ecam.h:44-48`）只有三个字段：
`bus_shift`（`:45`）· `pci_ops`（`:46`，⚠️ **内嵌不是指针**）· `init`（`:47`，可选钩子）。

⚠️ `pci-ecam.h:80-91` 那十个 `extern const struct pci_ecam_ops` 全在
`#if defined(CONFIG_ACPI) && defined(CONFIG_PCI_QUIRKS)` 里 —— 纯 ACPI 路径，
本文档集裁掉，记入 99 §2.7。

---

## §3 `ranges`：地址窗口

### §3.1 三段式编码

每条 `ranges` 是 `<总线地址(3 cell) CPU 地址(2 cell) 长度(2 cell)>`。
第一个 cell 的高字节是类型标志，解码逻辑在 `drivers/of/address.c:136-159`：

```c
/* drivers/of/address.c:144-158 */
switch((w >> 24) & 0x03) {
case 0x01: flags |= IORESOURCE_IO;                        break;  /* :145-147 */
case 0x02: flags |= IORESOURCE_MEM;                       break;  /* :148-150 32 位 */
case 0x03: flags |= IORESOURCE_MEM | IORESOURCE_MEM_64;   break;  /* :152-154 64 位 */
}
if (w & 0x40000000)
        flags |= IORESOURCE_PREFETCH;                             /* :156-157 */
```

所以那个魔数第一个 cell 是这么读的：

| 值 | 含义 |
|---|---|
| `0x01000000` | I/O 空间 |
| `0x02000000` | 32 位非预取 MEM |
| `0x03000000` | ⚠️ 64 位非预取 MEM |
| `0x42000000` | 32 位预取 MEM（bit 30 置位） |
| `0x43000000` | 64 位预取 MEM |

⚠️⚠️ **`0x03` 只表示"这条窗口用 64 位地址描述"，
不等于"支持 64 位 BAR"** —— 后者由 BAR 自己的 type 位决定（03 §3.1）。
这两件事经常被混为一谈。

### §3.2 解析在哪里发生

```
devm_pci_alloc_host_bridge()
  └─ devm_of_pci_bridge_init()                        drivers/pci/of.c:591
       ├─ bridge->swizzle_irq = pci_common_swizzle;   :596
       ├─ bridge->map_irq = of_irq_parse_and_map_pci; :597  ⚠️ 见 §6
       └─ pci_parse_request_of_pci_ranges()           :599 → :543
            ├─ INIT_LIST_HEAD(&bridge->windows)       :550
            ├─ INIT_LIST_HEAD(&bridge->dma_ranges)    :551
            ├─ devm_of_pci_get_host_bridge_resources() :553-554 → :274
            ├─ devm_request_pci_bus_resources()        :558
            └─ 逐条窗口后处理                          :562-583
```

⚠️ `devm_of_pci_bridge_init()` 第一件事是 `if (!dev->of_node) return 0;`（`of.c:593-594`）——
**没有 of_node 就什么都不做**，这是 ACPI 路径能共用同一个 alloc 函数的原因。

### §3.3 两条容易踩的检查

```c
/* drivers/pci/of.c:574-586 */
case IORESOURCE_MEM:
        res_valid |= !(res->flags & IORESOURCE_PREFETCH);          /* :575 */
        if (!(res->flags & IORESOURCE_PREFETCH))
                if (upper_32_bits(resource_size(res)))
                        dev_warn(dev, "Memory resource size exceeds max for 32 bits\n");  /* :578-579 */
        break;
...
if (!res_valid)
        dev_warn(dev, "non-prefetchable memory resource required\n");   /* :585-586 */
```

⚠️⚠️ 第一条：**`ranges` 里必须至少有一段非预取 MEM 窗口。**
`Documentation/devicetree/bindings/pci/host-generic-pci.yaml:102-104` 也这么要求。
只写了预取窗口的 dts 会得到 `non-prefetchable memory resource required`，
然后**所有普通 BAR 都分不到地址**。

⚠️ 第二条：非预取窗口大小超过 32 位会 warn。这不是错误，
但意味着 32 位 BAR 可能落不进去。

⚠️ I/O 窗口的处理在 `:566-573`：调 `devm_pci_remap_iospace()`，
**失败就把这条窗口从链表里删掉**（`:571` 的 `resource_list_destroy_entry`），
只留一条 warn。arm64 上 PIO 本身就是模拟的，失败很常见。

### §3.4 `dma-ranges` 是反方向

`ranges` 说的是"CPU 怎么访问设备"，`dma-ranges` 说的是"设备怎么访问内存"。

⚠️ 解析出来放在 `pci_host_bridge->dma_ranges`（`include/linux/pci.h:578`），
由同一个 `devm_of_pci_get_host_bridge_resources()` 的 `ib_resources` 参数填
（`of.c:277`、`:554`）。释放在 `probe.c:577`，初始化在 `probe.c:584`。

⚠️⚠️ 但真正把它变成 DMA 掩码和偏移的不是 PCI 核心，
是 03 §9 那条 `pci_dma_configure()` → `of_dma_configure(dev, bridge->parent->of_node, true)`。
**注意传的是 `bridge->parent->of_node`，也就是主机桥的 DT 节点，
不是设备自己的** —— PCI 端点没有 of_node。

arm64 dts 里 52 个文件带 `dma-ranges`，用途通常是两类：
限制设备可见的物理内存范围，或声明总线地址与 CPU 地址之间的固定偏移。

---

## §4 三个 Linux 私有属性

`pci.txt:14-34` 定义了四个，其中三个在 arm64 上常见。

### §4.1 `linux,pci-domain`

```c
/* drivers/pci/of.c:216-227 */
int of_get_pci_domain_nr(struct device_node *node)
{ ... }   /* 读 "linux,pci-domain"；EXPORT_SYMBOL_GPL 在 :227 */
```

⚠️⚠️ `pci.txt:17-20` 的约束很硬：**要么一个主机桥都不设，
要么全都设**。混着来会导致域号冲突。
不设时内核分配一个"跨启动不稳定"的唯一号 —— 这会让
`/sys/bus/pci/devices/` 下的路径每次启动都变，
**脚本里硬编码 `0000:00:00.0` 就会时好时坏。**

arm64 dts 里 22 个文件设了这个属性。⚠️ rk3568 的两个控制器分别是 `<1>` 和 `<2>`
（`arch/arm64/boot/dts/rockchip/rk3568.dtsi:86`）。

### §4.2 `max-link-speed` 与 `num-lanes`

| 属性 | 读取函数 | 值 |
|---|---|---|
| `max-link-speed` | `of_pci_get_max_link_speed()` `drivers/pci/of.c:619`，导出 `:629` | ⚠️ `pci.txt:25-26`：1/2/3/4 分别对应 gen1..gen4，**其它值非法** |
| `num-lanes` | ⚠️ 控制器驱动各自读（如 dwc 的 `dw_pcie.num_lanes`，`drivers/pci/controller/dwc/pcie-designware.h:320`） | 通道数 |

⚠️⚠️ `Documentation/devicetree/bindings/pci/pci.txt:22-24` 说明了 `max-link-speed` 的用意：
**让主机桥驱动避免为不支持的速率做无用功**，比如别去训练一个跑不到的速率。
它不是"强制降速"的开关，也不保证链路真能跑到这个速率。

arm64 dts 里 `num-lanes` 出现在 67 个文件（比 `max-link-speed` 的 19 个多得多）——
因为 dwc 系控制器把它当必填项用。

`of_pci_get_max_link_speed()` 的返回约定写在 kerneldoc `of.c:611-614`：
`> 0` 成功；`-EINVAL` 表示属性值非法**或**属性不存在。
⚠️ 两种失败共用一个错误码，**分不出"没写"和"写错了"**。

### §4.3 `linux,pci-probe-only`：从 chosen 读

```c
/* drivers/pci/of.c:233-252 */
void of_pci_check_probe_only(void)
{
        ret = of_property_read_u32(of_chosen, "linux,pci-probe-only", &val);  /* :238 */
        if (ret) {
                if (ret == -ENODATA || ret == -EOVERFLOW)
                        pr_warn("linux,pci-probe-only without valid value, ignoring\n");  /* :241 */
                return;
        }
        if (val)
                pci_add_flags(PCI_PROBE_ONLY);     /* :246 */
        else
                pci_clear_flags(PCI_PROBE_ONLY);   /* :248 */
        pr_info("PROBE_ONLY %s\n", val ? "enabled" : "disabled");   /* :250 */
}
EXPORT_SYMBOL_GPL(of_pci_check_probe_only);        /* :252 */
```

⚠️⚠️ 三个关键点：

1. **读的是 `of_chosen`，不是主机桥节点**（`:238`）。所以它是全局开关，
   一台机器只有一个值，写在 `/chosen` 里 —— 这就是为什么 71 个 arm64 板级 dts
   里一个都搜不到（§1.2）。
2. **值为 0 会主动清标志**（`:248`），不是"不管"。
3 `dmesg | grep PROBE_ONLY`（`:250`）是确认这个模式是否生效的唯一手段。

四个相关标志（`include/linux/pci.h:1059-1063`）：

| 标志 | 值 | 含义 |
|---|---|---|
| `PCI_REASSIGN_ALL_RSRC` | `0x1` | 忽略固件的资源分配 |
| `PCI_REASSIGN_ALL_BUS` | `0x2` | ⚠️ 重排所有总线号 |
| `PCI_PROBE_ONLY` | `0x4` | ⚠️⚠️ 沿用现有配置，什么都不改 |
| `PCI_ENABLE_PROC_DOMAINS` | `0x10` | `/proc` 里显示域号 |

⚠️⚠️ `PCI_PROBE_ONLY` 的实际生效点不在这里，
在 03 §5.4 的 `pbus_size_io()` / `pbus_size_mem()` ——
它们看到 `b_res->parent` 非空就直接返回，跳过整个尺寸计算。
**"probe-only 模式下资源分配被跳过"是通过这个间接机制实现的，
不是有个 if 直接绕过。** 排查时要顺着 `b_res->parent` 找。

### §4.4 `reset-gpios` 与 `supports-clkreq`

`pci.txt:27-29`：`reset-gpios` 是 PERST# 引脚，主机桥驱动解析它给端点做基本复位。
`pci.txt:30-34`：`supports-clkreq` 声明 CLKREQ 信号从根端口连到了下游设备 ——
⚠️ **没有这个信号，根端口就不该宣告支持 ASPM L1 子状态**（04 §5）。

⚠️⚠️ 这条属性是 ASPM 类挂死问题的一个常见根因：
硬件没接 CLKREQ，dts 又没说明，L1SS 一进就再也出不来。

---

## §5 `msi-map` 与 `iommu-map`：RID 到 sideband

### §5.1 同一套机制，两个用途

`Documentation/devicetree/bindings/pci/pci-msi.txt:4-14` 和 `Documentation/devicetree/bindings/pci/pci-iommu.txt:4-14` 用了 **完全相同的措辞** 定义 RID：

```
* Bits [15:8] 是 Bus number
* Bits [7:3]  是 Device number
* Bits [2:0]  是 Function number
* 其余填充位必须为零
```

两个属性的格式也一样，都是四元组：

| 属性 | 四元组 | 出处 |
|---|---|---|
| `msi-map` | `<rid-base, msi-controller, msi-base, length>` | `pci-msi.txt:32-44` |
| `iommu-map` | `<rid-base, iommu, iommu-base, length>` | `pci-iommu.txt:35-42` |

映射规则也一样（`pci-msi.txt:46-47`、`pci-iommu.txt:41-42`）：
区间 `[rid-base, rid-base + length)` 里的 RID `r`，
对应的 specifier 是 `r - rid-base + msi-base`。

⚠️ 各配一个 mask：`msi-map-mask`（`pci-msi.txt:49-50`）、
`iommu-map-mask`（`pci-iommu.txt:44-45`），**先与 RID 相与再查表**。

### §5.2 恒等映射的写法

```
/* pci-msi.txt:82 —— sideband 数据就是 RID 本身 */
msi-map = <0x0 &msi_a 0x0 0x10000>;

/* pci-iommu.txt:70 —— 同理 */
iommu-map = <0x0 &iommu 0x0 0x10000>;
```

⚠️ `0x10000` 就是 RID 的全空间（16 位）。

真实平台通常不是恒等的，比如 rk3568：

```
/* arch/arm64/boot/dts/rockchip/rk3568.dtsi:90 */
msi-map = <0x0 &gic 0x1000 0x1000>;
```

⚠️⚠️ 读法：本控制器下 RID 0x0000..0x0fff 映射到 GIC ITS 的
DeviceID 0x1000..0x1fff。**另一个控制器必须用不重叠的区间** ——
两个控制器都写 `0x1000` 会让 ITS 把两条总线的设备当成同一个，
表现是 MSI 送错设备或者干脆不来。

### §5.3 `msi-parent`：没有 sideband 的情况

`pci-msi.txt:52-56`：当根复合体和 MSI 控制器之间 **不传 sideband 数据** 时，
用 `msi-parent` 描述根复合体自己的 MSI 父节点。

⚠️ `Documentation/devicetree/bindings/pci/host-generic-pci.yaml:111` 把 `msi-parent: true` 列为允许属性。
⚠️⚠️ **`msi-map` 和 `msi-parent` 是两种机制，不要同时写** ——
前者按 RID 区分设备，后者所有设备共用一个。arm64 上 GIC ITS 走前者。

### §5.4 一个不对称：MSI 是否可用有专门的查询

```c
/* drivers/pci/of.c:79, :106 */
struct irq_domain *pci_host_bridge_of_msi_domain(struct pci_bus *bus)   /* :79 */
bool pci_host_of_has_msi_map(struct device *dev)                        /* :106 */
```

⚠️ 这两个都 **没有 `EXPORT_SYMBOL`**，只在核心内部用
（声明在私有头 `drivers/pci/pci.h`）。
`pci_host_of_has_msi_map()` 的存在说明核心需要区分
"这个桥根本没配 MSI" 和 "配了但当前查不到 domain" 两种情况。

---

## §6 `interrupt-map`：INTx 怎么接到 GIC

### §6.1 两个回调，在 alloc 时就装好

```c
/* drivers/pci/of.c:596-597 */
bridge->swizzle_irq = pci_common_swizzle;
bridge->map_irq = of_irq_parse_and_map_pci;
```

⚠️ 所有 DT 主机桥都用这一对，**没有例外**（`devm_of_pci_bridge_init()`
是唯一设置点）。控制器驱动不需要也不应该自己填。

### §6.2 `of_irq_parse_pci()` 的四段逻辑

```c
/* drivers/pci/of.c:418-517，按顺序 */
① dn = pci_device_to_OF_node(pdev);              /* :430 端点自己有 DT 节点？ */
   if (dn) { rc = of_irq_parse_one(dn, 0, out_irq); if (!rc) return rc; }  /* :431-435 */

② rc = pci_read_config_byte(pdev, PCI_INTERRUPT_PIN, &pin);   /* :442 读 INTx 引脚 */
   if (pin == 0) return -ENODEV;                 /* :446-447 ⚠️ 无引脚，静默退出 */

③ if (of_get_property(dn, "interrupt-map", NULL)) {           /* :450 本地有 map？ */
        pin = pci_swizzle_interrupt_pin(pdev, pin);            /* :451 */
        ppnode = dn;
   }

④ while (!ppnode) { ... 沿 PCI 树往上找有 DT 节点的桥 ... }   /* :456-... */
   laddr[0] = cpu_to_be32((bus->number << 16) | (devfn << 8)); /* :500 */
   rc = of_irq_parse_raw(laddr, out_irq);                      /* :502 */
```

⚠️⚠️ 第 ④ 段那个 `laddr[0]` 的编码（`:500`）就是 §5.1 的 RID 格式：
bus 左移 16、devfn 左移 8。**这就是 `interrupt-map-mask` 第一个 cell
要写 `0xf800` 之类值的原因** —— 它掩的是 devfn 里的 device 号那 5 位。

⚠️ `of.c:439-440` 有一句硬假设：
"we assume `#interrupt-cells` is 1, which is standard for PCI.
If you do different, then don't use that routine."
**`#interrupt-cells` 不是 1 就不能用这条通用路径**，得自己写 `map_irq`。

### §6.3 一个静默失败：返回 0 表示无中断

```c
/* drivers/pci/of.c:529-539 */
int of_irq_parse_and_map_pci(const struct pci_dev *dev, u8 slot, u8 pin)
{
        ret = of_irq_parse_pci(dev, &oirq);
        if (ret)
                return 0; /* Proper return code 0 == NO_IRQ */     /* :536 */
        return irq_create_of_mapping(&oirq);
}
```

⚠️⚠️ **解析失败返回 0，不是负值。** 0 在这套约定里就是 "NO_IRQ"。
后果：dts 的 `interrupt-map` 写错时，端点驱动看到的是 `pdev->irq == 0`，
`request_irq(0, ...)` 失败，**但错误信息里完全没有 DT 的影子。**

⚠️ 好消息是有专门的 warn，在 `of.c:507-512`：
`rc == -ENOENT` 时打 `no interrupt-map found, INTx interrupts not available`，
另加一条 `pr_warn_once` 说"可能有些 PCI 插槽没有电平触发中断能力"。
**排查 `pdev->irq == 0` 第一步就是 `dmesg | grep interrupt-map`。**

⚠️ `of.c:525-527` 的 kerneldoc 明说 `slot` 和 `pin` 两个参数 **没被使用**，
留着只是为了能直接当 `map_irq` 回调。

### §6.4 一份完整的真实例子

`Documentation/devicetree/bindings/pci/host-generic-pci.yaml:167-174` 的四行是最标准的写法：

```
// PCI_DEVICE(3)  INT#(1)  CONTROLLER(PHANDLE)  CONTROLLER_DATA(3)
interrupt-map = <   0x0 0x0 0x0  0x1  &gic  0x0 0x4 0x1>,
                < 0x800 0x0 0x0  0x1  &gic  0x0 0x5 0x1>,
                <0x1000 0x0 0x0  0x1  &gic  0x0 0x6 0x1>,
                <0x1800 0x0 0x0  0x1  &gic  0x0 0x7 0x1>;
// PCI_DEVICE(3)  INT#(1)
interrupt-map-mask = <0xf800 0x0 0x0  0x7>;
```

⚠️ `0x0 / 0x800 / 0x1000 / 0x1800` 是 device 0..3 左移 11 位后的值
（devfn 的 device 部分在 bit 3-7，再左移 8 位到 laddr 里 ⇒ bit 11-15）。
`0xf800` 正好掩住这 5 位。**这四行说的是"slot 0..3 的 INTA 分别接到 GIC SPI 4..7"。**

---

## §7 完整的 arm64 实例：rk3568

`arch/arm64/boot/dts/rockchip/rk3568.dtsi:63-114`，一个 dwc 系控制器。
逐段对照上面的规则：

| 行 | 属性 | 对应本篇 |
|---|---|---|
| `:64` | `compatible = "rockchip,rk3568-pcie"` | 不是 generic，走完整 dwc 驱动 |
| `:65-66` | `#address-cells = <3>` / `#size-cells = <2>` | ⚠️ PCI 节点固定这个组合 |
| `:67` | `bus-range = <0x0 0xf>` | §2.5 会被 ECAM 大小裁剪（dwc 不走 ECAM，这里由驱动自己用） |
| `:73` | `device_type = "pci"` | ⚠️⚠️ **少了这行整个节点不会被当 PCI 桥** |
| `:74-79` | 5 个 GIC SPI + `interrupt-names` | 控制器自身的中断，⚠️ 不是端点的 |
| `:80-85` | `#interrupt-cells = <1>` + `interrupt-map` | §6，映到内部 `legacy-interrupt-controller` |
| `:86` | `linux,pci-domain = <1>` | §4.1 |
| `:89` | `max-link-speed = <3>` | §4.2，gen3 |
| `:90` | `msi-map = <0x0 &gic 0x1000 0x1000>` | §5.2 |
| `:91` | `num-lanes = <1>` | §4.2 |
| `:95-97` + `:101` | 三段 `reg` + `reg-names = "dbi","apb","config"` | ⚠️ dwc 特有的三窗口 |
| `:98-100` | `ranges` 三条 | §3.1 |
| `:105` | `status = "disabled"` | ⚠️ 板级 dts 里要 override 成 okay |

`ranges` 三条逐条解码（§3.1 的表）：

```
<0x01000000 0x0 0xf2100000  0x0 0xf2100000  0x0 0x00100000>  /* :98  I/O，1 MB */
<0x02000000 0x0 0xf2200000  0x0 0xf2200000  0x0 0x01e00000>  /* :99  32 位 MEM，30 MB */
<0x03000000 0x0 0x40000000  0x3 0x40000000  0x0 0x40000000>  /* :100 64 位 MEM，1 GB */
```

⚠️⚠️ 注意第三条：总线地址 `0x0_40000000`，CPU 地址 `0x3_40000000`。
**这两个不相等** —— 正是 05 §5.1 那条"BAR 里是总线地址、
`pci_dev->resource[]` 里是 CPU 地址"的实例。偏移是 12 GB。

### §7.1 那个内部的 legacy 中断控制器

```
/* rk3568.dtsi:107-113 */
pcie3x1_intc: legacy-interrupt-controller {
        interrupt-controller;
        #address-cells = <0>;
        #interrupt-cells = <1>;
        interrupt-parent = <&gic>;
        interrupts = <GIC_SPI 157 IRQ_TYPE_EDGE_RISING>;   /* :112 */
};
```

⚠️ 这是 dwc 系的常见结构：四条 INTx 在控制器内部被合成一根 GIC SPI，
再由控制器驱动做二级 demux。所以 `:82-85` 的 `interrupt-map` 指向的是
这个内部控制器（`&pcie3x1_intc`），**不是直接指向 `&gic`**。

⚠️⚠️ 对比 §6.4 的 generic 写法（直接指 `&gic`），
两种都合法。**看到 `interrupt-map` 指向一个 `legacy-interrupt-controller`
子节点，就说明 INTx 是 demux 出来的**，`/proc/interrupts` 里会看到
一个共享计数器而不是四个。

### §7.2 dwc 框架的规模

| 文件 | 行数 | 说明 |
|---|---|---|
| `drivers/pci/controller/dwc/pcie-designware-ep.c` | 825 | ⚠️ endpoint 模式，本文档集裁掉 |
| `drivers/pci/controller/dwc/pcie-designware-host.c` | 771 | ⚠️⚠️ RC 模式的主体 |
| `drivers/pci/controller/dwc/pcie-designware.c` | 706 | 寄存器访问、ATU、链路 |
| `drivers/pci/controller/dwc/pcie-designware.h` | 539 | 全部结构体和寄存器定义 |
| `drivers/pci/controller/dwc/pcie-designware-plat.c` | 187 | 通用平台粘合 |

`drivers/pci/controller/dwc/Kconfig` 有 **40 个 `config`** ——
即 40 家厂商基于同一个 IP 做的变体。

对控制器驱动作者，dwc 的对外接口只有两个：

| 接口 | 位置 |
|---|---|
| `dw_pcie_host_init()` | `drivers/pci/controller/dwc/pcie-designware-host.c:385`，`EXPORT_SYMBOL_GPL` `:511` |
| `dw_pcie_host_deinit()` | `:513`，导出 `:528` |

另外 9 个导出在 `drivers/pci/controller/dwc/pcie-designware.c`：
`dw_pcie_find_capability` `:83` · `dw_pcie_find_ext_capability` `:124` ·
`dw_pcie_read` `:146` · `dw_pcie_write` `:164` · `dw_pcie_read_dbi` `:180` ·
`dw_pcie_write_dbi` `:195` · `dw_pcie_wait_for_link` `:464` ·
`dw_pcie_link_up` `:477` · `dw_pcie_upconfig_setup` `:487`。
加上 host 侧的 `dw_pcie_own_conf_map_bus` `:625` 和 `dw_pcie_setup_rc` `:771`。

⚠️⚠️ `struct dw_pcie`（`drivers/pci/controller/dwc/pcie-designware.h:305-324`）里几个字段直接来自 DT：
`num_ib_windows` `:311` / `num_ob_windows` `:312`（rk3568 的 `:87-88`）·
`num_lanes` `:320`（`:91`）· `link_gen` `:321`（由 `max-link-speed` 来，`:89`）。
**dts 里那些看着像魔数的属性，一一对应这个结构体的字段。**

### §7.3 什么时候该在 dts 里描述端点

`Documentation/devicetree/bindings/pci/pci.txt:39-42` 给了唯一的正当理由：
**PCIe 根端口和交换机端口可以显式描述**，因为有些属性
标准 PCIe 能力里根本没有。

`reg` 的格式很特别（`Documentation/devicetree/bindings/pci/pci.txt:46-50`）：五个 cell
`(phys.hi phys.mid phys.lo size.hi size.lo)`，
其中 `phys.hi` 放 BDF：`0b00000000 bbbbbbbb dddddfff 00000000`，
**其余四个 cell 全为零。**

⚠️ `pci.txt:52-57`：总线号由固件通过标准桥配置机制分配 ——
交换机端口的号是上级桥的 Secondary Bus Number 寄存器决定的，
根端口的号是 `bus-range` 的第一个数（默认 0）。
**所以 dts 里写的 BDF 必须和固件的分配结果一致，这是个隐式契约。**

⚠️⚠️ `pci.txt:59-63` 有个 ARI 的坑：如果固件把上级桥的
ARI Forwarding Enable 位留着，`phys.hi` 的格式变成
`0b00000000 bbbbbbbb ffffffff 00000000` —— **8 位功能号，没有 device 号了**。
同一份 dts 在固件行为不同时会解析出不同的设备。

目前唯一实用的这类属性是 `external-facing`（`pci.txt:67-72`）：
标记这个端口朝外，**下游一切设备都在机箱外面**，
OS 据此识别"不能给宽松 DMA 保护"的设备。用户能随手插恶意设备的口。

---

## §8 写一个新主机桥驱动

### §8.1 先判断走哪条路

| 情况 | 做法 |
|---|---|
| 固件全配好、ECAM 兼容、无 quirk | ⚠️⚠️ **什么都不写**，dts 用 `pci-host-ecam-generic` |
| ECAM 兼容但有访问 quirk | 写一份 `pci_ecam_ops`，probe 直接用 `pci_host_common_probe` |
| DesignWare IP | ⚠️ 用 dwc 框架，只写平台粘合层（时钟/复位/PHY/ATU 初值） |
| 完全自研控制器 | 自己 alloc bridge + 填 `pci_ops` + `pci_host_probe()` |

⚠️ 第二种就是 `pci-host-generic.c` 那 89 行的形态 —— 一个 `ops` 表加一个
`of_device_id` 表，probe/remove 全借 common 的。**这是最省事的自定义方式。**

### §8.2 第四种情况的最小骨架

```c
static int my_pcie_probe(struct platform_device *pdev)
{
        struct pci_host_bridge *bridge;
        struct my_pcie *pcie;

        bridge = devm_pci_alloc_host_bridge(&pdev->dev, sizeof(*pcie));
        if (!bridge)
                return -ENOMEM;             /* ⚠️ 此时 windows/dma_ranges 已解析好 */
        pcie = pci_host_bridge_priv(bridge);

        /* 这里做时钟、复位、PHY、控制器寄存器初始化 —— 全是平台私事 */

        bridge->sysdata = pcie;
        bridge->ops = &my_pci_ops;          /* ⚠️ 至少要 map_bus + read + write */

        return pci_host_probe(bridge);      /* 之后全交给核心，03 §2 */
}
```

⚠️⚠️ 三个不要做的事：

1. **不要自己填 `map_irq` / `swizzle_irq`** —— `devm_of_pci_alloc_host_bridge`
   已经装好了（§6.1）。填了反而绕过 DT 解析。
2. **不要自己解析 `ranges`** —— 同上。
3. **不要在 `pci_host_probe()` 之后还去改 `bridge->windows`** ——
   核心已经拿它去分配资源了。

`pci_ops` 三个必填成员（02 §5 的完整表）：`map_bus` · `read` · `write`。
⚠️ ECAM 类控制器的 `read`/`write` 直接用核心提供的
`pci_generic_config_read` / `pci_generic_config_write`
（`pci-host-generic.c:21-22`），只需要自己写 `map_bus`。

### §8.3 绑定文档必须同时提交

新 `compatible` 必须在 `Documentation/devicetree/bindings/pci/` 加 yaml，
并且 ⚠️ `allOf: - $ref: /schemas/pci/pci-bus.yaml#`
（照 `Documentation/devicetree/bindings/pci/host-generic-pci.yaml:118-119` 抄）——
这一句把 PCI 通用属性的校验规则引进来。

⚠️⚠️ `Documentation/devicetree/bindings/pci/host-generic-pci.yaml:143` 有 `unevaluatedProperties: false`，
意思是 **没在 schema 里声明过的属性会报错**。
新加属性时忘了写进 schema，`make dt_binding_check` 会失败。

---

## §9 排查清单

| 现象 | 先看 | 依据 |
|---|---|---|
| 根本没有 PCI 总线 | dts 有 `device_type = "pci"` 吗；`status` 是 okay 吗 | §7 |
| `lspci` 只到某个 bus 号就停 | ⚠️ `dmesg \| grep "can only accommodate"` | §2.5 |
| 同一设备出现多次 | ⚠️⚠️ 控制器没过滤 type 0 TLP，需要 `pci_dw_valid_device` 那类补丁 | §2.3 |
| 设备枚举到但 BAR 全是 0 | `ranges` 里有非预取 MEM 窗口吗 | §3.3 |
| `pdev->irq == 0` | ⚠️⚠️ `dmesg \| grep interrupt-map` | §6.3 |
| MSI 不来 / 送错设备 | `msi-map` 区间是否与其它控制器重叠 | §5.2 |
| DMA 地址错乱 | `dma-ranges` 与 `ranges` 的偏移是否一致 | §3.4 |
| 设备路径每次启动都变 | ⚠️ 没设 `linux,pci-domain` | §4.1 |
| 进 ASPM L1SS 后挂死 | ⚠️⚠️ 硬件没接 CLKREQ 但没写 `supports-clkreq` | §4.4 |
| 资源没被重排 | `dmesg \| grep PROBE_ONLY` | §4.3 |

### §9.1 三个必看的 dmesg 行

```
pci-host-generic ...: ECAM at [mem 0x...] for [bus 00-0f]     /* ecam.c:92  窗口建好了 */
pci_bus 0000:00: root bus resource [mem 0x...]                /* 窗口解析结果 */
pci 0000:00:01.0: BAR 0: assigned [mem 0x...]                 /* 资源分配结果 */
```

⚠️ 这三行按顺序出现说明 §2 → §3 → 03 §5 三段都通了。
少哪一行，问题就在对应那一段。

---

## §10 一页速查

| DT 属性 | 谁解析 | 位置 |
|---|---|---|
| `reg` + `compatible` | `pci_ecam_create()` | `drivers/pci/ecam.c:27` |
| `bus-range` | `of_pci_parse_bus_range()` | `drivers/pci/of.c:180`，导出 `:197` |
| `ranges` | `devm_of_pci_get_host_bridge_resources()` | `drivers/pci/of.c:274` |
| `dma-ranges` | 同上（`ib_resources` 参数） | `of.c:277`、`:554` |
| `interrupt-map` | `of_irq_parse_and_map_pci()` | `drivers/pci/of.c:529`，导出 `:540` |
| `linux,pci-domain` | `of_get_pci_domain_nr()` | `drivers/pci/of.c:216`，导出 `:227` |
| `max-link-speed` | `of_pci_get_max_link_speed()` | `drivers/pci/of.c:619`，导出 `:629` |
| `linux,pci-probe-only` | `of_pci_check_probe_only()` | `drivers/pci/of.c:233`，导出 `:252` |
| `msi-map` / `msi-parent` | 核心 + irqchip，⚠️ 未导出 | `drivers/pci/of.c:79`、`:106` |
| `iommu-map` | ⚠️ `of_dma_configure()`，不在 PCI 核心 | 03 §9 |
| `devfn`（P2P 桥 `reg`） | `of_pci_get_devfn()` | `drivers/pci/of.c:160`，导出 `:171` |
| 找子设备节点 | `of_pci_find_child_device()` | `drivers/pci/of.c:125`，导出 `:149` |
| 插槽功耗上限 | `of_pci_get_slot_power_limit()` | `drivers/pci/of.c:647`，导出 `:699` |

`drivers/pci/of.c` 一共 8 个 `EXPORT_SYMBOL_GPL`，就是上表里标了导出的那些。
⚠️⚠️ 但 99 篇会记一条：其中 4 个只在私有头 `drivers/pci/pci.h:611-614` 里声明，
**外部模块拿不到原型**，实际不可用。

三条最容易搞反的：

1. ⚠️⚠️ **`ranges` 第一个 cell 的 `0x03` 是"窗口用 64 位地址描述"，
   不是"支持 64 位 BAR"。**
2. ⚠️⚠️ **`of_irq_parse_and_map_pci()` 失败返回 0，不是负值** —— 0 就是 NO_IRQ。
3. ⚠️⚠️ **`linux,pci-probe-only` 读的是 `/chosen`，不是主机桥节点。**

---

## 下一步读什么

| 想知道 | 去哪篇 |
|---|---|
| 主机桥交给核心之后发生什么 | 03 篇 §2、§5 |
| `pci_host_bridge` 每个字段的含义 | 02 篇 §4 |
| `pci_ops` / `pci_config_window` 的字段 | 02 篇 §5 |
| 端点驱动怎么用这些资源 | 05 篇 §5 |
| AER/ASPM/DPC 与 DT 的关系 | 04 篇 §5、§6 |
| 跑起来后从 sysfs 怎么验证 | 07 篇 |
| 本篇裁掉的 ACPI 路径清单 | 99 篇 §2.7 |
