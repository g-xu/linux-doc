---
title: 内部实现
---

# pci 03：内部实现

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

本篇只讲 19966 行核心层里的三条主链和一套锁。控制器驱动、热插拔、SR-IOV、AER 不在这里（范围见 99 §1.3）。

---

## §1 三条链，先看它们怎么接起来

pci 核心做的事可以精确地切成三段，而且这三段是 **顺序执行、互不重入** 的：

```
① 枚举        probe.c        读配置空间 → 造出 pci_dev / pci_bus 对象树
                             ⚠️ 此时 BAR 里的地址还可能是 0 或垃圾
                                    │
② 资源分配    setup-bus.c    自底向上算尺寸 → 自顶向下写 BAR 和桥窗口
                             ⚠️ 只有这一段能改硬件的地址译码
                                    │
③ 绑定驱动    bus.c + pci-driver.c   device_attach() → 匹配 → probe()
```

⚠️⚠️ **三段之间没有锁来保证顺序，靠的是"调用方按顺序调"这个约定。**
arm64 上这个约定由 `pci_host_probe()` 一个函数替你履行（`drivers/pci/probe.c:3086`）：

```c
int pci_host_probe(struct pci_host_bridge *bridge)   /* probe.c:3086 */
{
        ret = pci_scan_root_bus_bridge(bridge);       /* ① */
        ...
        pci_bus_size_bridges(bus);                    /* ② 上半 */
        pci_bus_assign_resources(bus);                /* ② 下半 */
        ...
        pci_bus_add_devices(bus);                     /* ③ */
}
```

⚠️ **看懂这一段，就看懂了为什么 ③ 不能提前**：
`device_attach()` 一旦触发，驱动的 `probe()` 里马上会 `pci_resource_start()`，
而 BAR 是在 ② 里才写进硬件的。**顺序颠倒的表现是驱动读到全 0 的资源。**

⚠️ 对应的开关是 `pci_dev.match_driver` 这个位——见 §4，那是整个核心里最不容易看出来的一处设计。

---

## §2 枚举主链：从主机桥到 `pci_dev`

### §2.1 十一层调用，一层不能跳

全部在 `drivers/pci/probe.c`。下表的行号是 **函数定义行**，不是 `EXPORT_SYMBOL` 行：

| 层 | 函数 | 定义 | 干什么 |
|---|---|---|---|
| 1 | `pci_host_probe()` | `:3086` | arm64 控制器驱动的唯一入口 |
| 2 | `pci_scan_root_bus_bridge()` | `:3180` | 注册 host bridge 设备 + 建根总线 |
| 3 | `pci_create_root_bus()` | `:3057` | 造出 bus 0，挂 `pcibus_class` |
| 4 | `pci_scan_child_bus()` | `:3031` | 薄封装，转调下一层 |
| 5 | `pci_scan_child_bus_extend()` | `:2907` | ⚠️ 一条总线的完整扫描 + 总线号分配 |
| 6 | `pci_scan_slot()` | `:2691` | 一个 slot（devfn 0..7）的多功能探测 |
| 7 | `pci_scan_single_device()` | `:2603` | 一个 devfn |
| 8 | `pci_scan_device()` | `:2443` | 读 vendor id、`pci_alloc_dev`、`pci_setup_device` |
| 9 | `pci_setup_device()` | `:1851` | ⚠️ 把配置空间翻译成结构体字段 |
| 10 | `pci_device_add()` | `:2557` | 挂链表、初始化能力、`device_add()` |
| 11 | `pci_init_capabilities()` | `:2487` | 逐个能力初始化 |

递归发生在第 5 层和 `pci_scan_bridge_extend()`（`:1263`）之间：
桥被扫到 → `pci_add_new_bus()`（`:1175`）造下级 `pci_bus` → 回到第 5 层扫那条总线。

### §2.2 总线号是怎么分出来的

`pci_scan_child_bus_extend()` 对同一条总线要走 **三遍** `for_each_pci_bridge`，
这是整个枚举里唯一需要盯着变量看的地方：

```c
for (devfn = 0; devfn < 256; devfn += 8)      /* probe.c:2919 附近 */
        pci_scan_slot(bus, devfn);            /* 第一步：先把设备都发现 */

used_buses = pci_iov_bus_range(bus);          /* :2922 SR-IOV 预留 */
max += used_buses;                            /* :2923 */

for_each_pci_bridge(dev, bus) {               /* :2940 第一遍：只数桥 */
        if (dev->is_hotplug_bridge)
                hotplug_bridges++;            /* :2942 */
        ...
}
for_each_pci_bridge(dev, bus) {               /* :2952 第二遍：实际扫描 */
        max = pci_scan_bridge_extend(bus, dev, max, 0, 0);   /* :2954 */
        used_buses++;                                        /* :2960 */
}
for_each_pci_bridge(dev, bus) {               /* :2966 第三遍：重新配总线号 */
        buses = available_buses / hotplug_bridges;            /* :2982 */
        max = pci_scan_bridge_extend(bus, dev, cmax, buses, 1);/* :2987 */
}
```

⚠️ 为什么要三遍：**热插拔桥必须预留空的总线号区间**，
否则以后插上去的设备没有总线号可用。而"要预留几个"取决于"这条总线上有几个热插拔桥"，
所以必须先数一遍（第一遍），再按份额分（第三遍）。中间的第二遍先按最紧凑方式扫一次，
得到"实际用了多少"，第三遍才知道还剩多少可分。

⚠️⚠️ **`available_buses` 为 0 时第三遍等于空转** ——
arm64 上大多数平台走 DT，`is_hotplug_bridge` 为 0，
所以这段复杂逻辑在典型嵌入式板子上完全不生效。看代码时容易误判它的重要性。

### §2.3 多功能设备的探测终止条件

`pci_scan_slot()`（`:2691`）的逻辑值得单独看，因为它决定了 **一个设备能不能被发现**：

先扫 function 0，若不存在则整个 slot 放弃（不扫 1..7）。
只有 function 0 的 `multifunction` 位被置起，或者已经发现过 ARI，才继续扫 1..7。

⚠️ **后果**：硬件把功能挂在 function 1 而 function 0 是空的，Linux 就看不见它。
这不是 bug，是 PCI 规范的要求，但故障现场很容易误判成"内核不认设备"。

### §2.4 `pci_device_add()` 里的最后 20 行是全篇最关键的顺序

```c
        pci_init_capabilities(dev);            /* probe.c:2581 */

        down_write(&pci_bus_sem);               /* :2587 */
        list_add_tail(&dev->bus_list, &bus->devices);   /* :2588 */
        up_write(&pci_bus_sem);                 /* :2589 */

        ret = pcibios_device_add(dev);          /* :2591 体系结构钩子 */
        WARN_ON(ret < 0);

        pci_set_msi_domain(dev);                /* :2595 */

        /* Notifier could use PCI capabilities */
        dev->match_driver = false;              /* :2598 ⚠️⚠️ 全篇重点 */
        ret = device_add(&dev->dev);            /* :2599 */
        WARN_ON(ret < 0);
```

四点值得记住：

1. ⚠️ `pci_init_capabilities()` 在 `device_add()` **之前**。注释（`:2597`）解释了原因：
   `device_add()` 会发 uevent，通知链上的回调可能立刻去读能力偏移，那时必须已经填好。
2. ⚠️ 挂 `bus->devices` 链表用的是 `pci_bus_sem` 的 **写锁**，
   而遍历侧（`pci_get_slot()` 等）拿读锁——这是核心里唯一保护对象树的锁（§7.3）。
3. ⚠️⚠️ 两个 `WARN_ON(ret < 0)` 之后 **没有回滚**。`device_add()` 失败了，
   设备照样留在 `bus->devices` 链表里。这是核心的一处已知薄弱面（99 §7）。
4. ⚠️⚠️ `match_driver = false` 是 §4 的全部内容。

### §2.5 能力初始化的顺序

`pci_init_capabilities()`（`:2487`）是一串平铺的调用，顺序上有一处依赖：
电源管理（`pci_pm_init`）必须早于其它，因为后面几项要读寄存器，
而读寄存器要求设备不在 D3cold。其余各项（VPD、SR-IOV、ATS、PRI、PASID、ACS、PTM、DPC、AER）
彼此独立，`CONFIG_` 关掉时对应函数是空实现。

⚠️ `pcie_report_downtraining()`（`:2468`）在这里被调用，
它是那条 `link is capable of ... but ... only` 内核日志的来源。
链路降速在 arm64 板子上多半是 DT 里 `num-lanes` 写多了，而不是硬件坏了。

### §2.6 三个 `__weak` 钩子和它们在 arm64 上的样子

| 钩子 | 位置 | arm64 |
|---|---|---|
| `pcibios_root_bridge_prepare()` | `probe.c:3044` | ⚠️ 空实现，返回 0 |
| `pcibios_add_bus()` / `pcibios_remove_bus()` | `probe.c:3049` / `:3053` | ⚠️ 空实现 |
| `pcibios_resource_survey_bus()` | `bus.c:320` | ⚠️ 空实现 |
| `pcibios_bus_add_device()` | `bus.c:322` | ⚠️ 空实现 |

⚠️⚠️ **四个钩子在 arm64 上全是空的。** 它们存在是为了 x86 的固件交互和
其它体系结构的地址空间修补。读代码时可以直接跳过，
但改核心时要记得：这几个名字不是死代码，别删。

---

## §3 `pci_setup_device()`：填字段的顺序就是硬件规范的顺序

`probe.c:1851` 起 140 行，是 `pci_dev` 那 134 个字段（02 §2）里绝大多数的填充点。
它的结构不是"逻辑分组"，而是 **配置空间头的物理布局顺序**：

| 阶段 | 填什么 | 关键点 |
|---|---|---|
| 1 | `hdr_type`、`devfn`、`vendor`、`device`、`class`、`revision` | 从 offset 0x00–0x0c 顺序读下来 |
| 2 | `dev.parent`、`dev.bus`、`dev.type`、设备名 | ⚠️ 设备名 `pci_name()` 在这里成型 |
| 3 | `set_pcie_port_type()`（`:1537`） | ⚠️ 填 `pcie_cap`、`pcie_flags_reg`、`is_pcie` |
| 4 | `pci_dev_assign_slot()`、`pci_fixup_device(pci_fixup_early, ...)` | ⚠️ early quirk 在读 BAR 之前 |
| 5 | 按 `hdr_type` 分支：NORMAL / BRIDGE / CARDBUS | 各自 `pci_read_bases()` 参数不同 |
| 6 | `dev->irq` 从 interrupt line 寄存器读出 | ⚠️ arm64 上这个值几乎总是 0，靠 §9 的 DT/MSI 覆盖 |

⚠️⚠️ **第 4 步的位置是有意的**：early quirk 必须能在核心解释 BAR 之前
修正那些"BAR 报告的尺寸是错的"设备。`quirks.c`（6191 行，不在本套范围）里
大量条目依赖这个时序。

### §3.1 BAR 的读法：`__pci_read_base()`

`pci_read_bases()`（`:322`）循环调用 `__pci_read_base()`（`:177`）。
后者是核心里最"硬件"的一段代码，做四件事：

1. 写全 1 到 BAR、读回、算出掩码 → 得到尺寸。
   ⚠️ 期间必须先保存原值、之后恢复，否则会破坏固件已经配好的地址。
2. 判断 I/O 还是 MEM、32 位还是 64 位、是否 prefetchable。
3. 64 位 BAR 要吃掉下一个 BAR 槽位，所以循环索引会跳 2。
4. ⚠️⚠️ 尺寸算出来是 0 或者上半 32 位非 0 而 `pci_bus_addr_t` 只有 32 位时，
   把这个资源标成 `IORESOURCE_UNSET | IORESOURCE_DISABLED` 并打日志。

⚠️ 这解释了 02 §9 里那个条件类型 `pci_bus_addr_t`：
arm64 是 64 位，`CONFIG_ARCH_DMA_ADDR_T_64BIT` 成立，所以 64 位 BAR 能用；
32 位 arm 上同一段代码会把 64 位 BAR 直接禁掉。

---

## §4 两阶段拆分：`match_driver` 那一个位

这是核心里最容易看漏、后果最大的一处设计。

### §4.1 两处代码配对起来看

```c
/* drivers/pci/probe.c:2597-2600 —— 枚举阶段 */
        /* Notifier could use PCI capabilities */
        dev->match_driver = false;
        ret = device_add(&dev->dev);
```

```c
/* drivers/pci/pci-driver.c:1523-1524 —— pci_bus_match() 里 */
        if (!pci_dev->match_driver)
                return 0;
```

`device_add()` 会走驱动核心的标准流程，其中包含"顺便试着找个驱动绑上"。
⚠️⚠️ **pci 不允许这件事在枚举阶段发生**，于是用 `match_driver = false`
让 `pci_bus_match()` 对所有驱动都返回"不匹配"，
把绑定推迟到 `pci_bus_add_devices()`。

### §4.2 谁把它翻回 true

`pci_bus_add_device()`（`bus.c:330`）：

```c
void pci_bus_add_device(struct pci_dev *dev)      /* bus.c:330 */
{
        pci_fixup_device(pci_fixup_final, dev);   /* :339 */
        pci_create_sysfs_dev_files(dev);          /* :340 */
        pci_proc_attach_device(dev);              /* :341 */
        pci_bridge_d3_update(dev);                /* :342 */

        dev->match_driver = true;                 /* :344 ⚠️⚠️ 翻回 true */
        retval = device_attach(&dev->dev);        /* :345 紧接着立刻绑定 */
        if (retval < 0 && retval != -EPROBE_DEFER)
                pci_warn(dev, "device attach failed (%d)\n", retval);  /* :347 */

        pci_dev_assign_added(dev, true);          /* :349 */
}
```

⚠️ 全树只有三处碰这个位：置 false（`probe.c:2598`）、置 true（`bus.c:344`）、
读取（`pci-driver.c:1523`）；声明在 `include/linux/pci.h:423`，注释写的是 `Skip attaching driver`。
⚠️⚠️ 实际后果：**在 `pci_bus_add_device()` 跑到之前，
这个设备用 sysfs 的 `bind` 或任何 `device_attach()` 路径都绑不上，而且不报错**——静默无事发生。
热插拔和 `rescan` 之后"设备在 lspci 里有、驱动却没起来"，第一个要排除的就是这条路径没走完。

### §4.3 `pci_bus_add_devices()` 为什么要遍历两遍

```c
void pci_bus_add_devices(const struct pci_bus *bus)   /* bus.c:359 */
{
        list_for_each_entry(dev, &bus->devices, bus_list) {   /* :364 第一遍 */
                if (pci_dev_is_added(dev))                    /* :366 跳过已加的 */
                        continue;
                pci_bus_add_device(dev);                      /* :368 */
        }

        list_for_each_entry(dev, &bus->devices, bus_list) {   /* :371 第二遍 */
                if (!pci_dev_is_added(dev))                   /* :373 */
                        continue;
                if (child)
                        pci_bus_add_devices(child);           /* :377 递归下级 */
        }
}
```

⚠️ **本级全部绑完，才下探下一级。** 原因是桥驱动（如 `pcieport`）
可能在 probe 里改动下级总线的状态；先把桥绑好再扫下级，语义才确定。

⚠️ `pci_dev_is_added()` 这个位（`pci_dev.added`，02 §2.9）就是给这两遍循环用的，
它也是热插拔重复添加的防线。

---

## §5 资源分配主链：`setup-bus.c` 的 2341 行

这是核心里唯一会 **改写硬件地址译码** 的一段，也是本套里算法密度最高的一段。

### §5.1 两个方向

```
自底向上（size）      叶子设备的 BAR 尺寸 → 汇总成父桥窗口需要多大 → 再往上汇总
   __pci_bus_size_bridges()  → pbus_size_io() / pbus_size_mem()
                     │
自顶向下（assign）    从根总线开始，把地址区间切给各个桥窗口和 BAR
   __pci_bus_assign_resources() → pci_setup_bridge() → 写配置空间
```

⚠️⚠️ **顺序不能反**：不先知道子树要多大，就没法给父桥窗口定尺寸；
不先把父桥窗口的地址定下来，子树里的 BAR 就没有可用区间。

### §5.2 入口和调用图

| 函数 | 定义 | 角色 |
|---|---|---|
| `pci_assign_unassigned_bus_resources()` | `:2327` | ⚠️ arm64 热插拔/rescan 后的入口 |
| `pci_assign_unassigned_root_bus_resources()` | `:2039` | 整棵树重来，带重试和回退 |
| `pci_bus_size_bridges()` | `:1328` | 导出的薄封装 |
| `__pci_bus_size_bridges()` | `:1199` | ⚠️ 递归主体 |
| `pbus_size_io()` | `:874` | I/O 窗口定尺寸 |
| `pbus_size_mem()` | `:985` | MEM / prefetchable 窗口定尺寸 |
| `pci_bus_assign_resources()` | `:1411` | 导出的薄封装 |
| `__pci_bus_assign_resources()` | `:1375` | ⚠️ 递归主体 |
| `pci_setup_bridge()` | `:688` | 写一个桥的三组窗口寄存器 |
| `__pci_setup_bridge()` | `:665` | 按 type 分派 io / mmio / mmio_pref |
| `pci_claim_bridge_resource()` | `:698` | 认领固件已配好的桥窗口 |
| `pci_bus_claim_resources()` | `:1477` | ⚠️ `linux,pci-probe-only` 走这条 |
| `pci_bus_release_bridge_resources()` | `:1587` | 失败回退时释放 |
| `pci_bus_dump_resources()` | `:1632` | 打印最终布局 |
| `pci_bus_distribute_available_resources()` | `:1828` | 热插拔预留的均分 |
| `adjust_bridge_window()` | `:1744` | 扩大已配好的桥窗口 |

### §5.3 `pci_assign_unassigned_bus_resources()` 只有 12 行，但信息量大

```c
void pci_assign_unassigned_bus_resources(struct pci_bus *bus)  /* setup-bus.c:2327 */
{
        LIST_HEAD(add_list);                                   /* :2331 */

        down_read(&pci_bus_sem);                               /* :2333 */
        for_each_pci_bridge(dev, bus)
                if (pci_has_subordinate(dev))
                        __pci_bus_size_bridges(dev->subordinate, &add_list);  /* :2336 */
        up_read(&pci_bus_sem);
        __pci_bus_assign_resources(bus, &add_list, NULL);      /* :2338 */
        BUG_ON(!list_empty(&add_list));                        /* :2339 */
}
```

三处值得记：

1. ⚠️ `add_list` 是"可选的额外尺寸"清单——先按最小需求分，
   分完还有余量再从这个清单里加。这是 `pci=realloc` 一类行为的机制。
2. ⚠️ size 阶段拿 `pci_bus_sem` 的 **读锁**，assign 阶段 **不拿锁**。
   ⚠️⚠️ 也就是说：**资源分配对并发拓扑变化没有保护**，
   靠上层的 `pci_rescan_remove_lock`（§7.4）串行化。
3. ⚠️⚠️ 最后一行是 `BUG_ON`，不是 `WARN_ON`。清单没排空就整机 panic。
   这是核心里对自己算法正确性最强的一处断言。

### §5.4 `pbus_size_io()` 的注释就是它的全部难点

```c
/* setup-bus.c:869-872 —— 原注释 */
 * Sizing the I/O windows of the PCI-PCI bridge is trivial, since these
 * windows have 1K or 4K granularity and the I/O ranges of non-bridge PCI
 * devices are limited to 256 bytes.  We must be careful with the ISA
 * aliasing though.
```

它做的事：遍历本总线所有设备的 17 个 resource 槽位（`:896`），
把 `IORESOURCE_IO` 且还没有 parent 的累加，
再按 `window_alignment()`（`:892`）对齐到桥的粒度。

两处提前返回是排查"窗口没分到"时最先要看的（`:885` 和 `:889`）：

| 条件 | 位置 | 含义 |
|---|---|---|
| `!b_res` | `:885-886` | ⚠️ 桥根本没有这类窗口资源槽 |
| `b_res->parent` | `:889-890` | ⚠️⚠️ **已经被分配过了，直接跳过** |

⚠️⚠️ 第二条是 `linux,pci-probe-only` 生效的实际机制：
先用 `pci_bus_claim_resources()` 把固件的配置认领进 resource 树（`parent` 被填上），
后续 size/assign 就自动全部跳过。**这不是一个 if 判断的开关，而是靠数据状态实现的。**

⚠️ arm64 上 I/O 空间通常根本不存在（DT 里没有 `ranges` 的 I/O 项），
所以 `pbus_size_io()` 多半在 `:885` 就返回了。**这一条注释里的 ISA aliasing 在 arm64 上是死逻辑。**

### §5.5 `__pci_setup_bridge()`：真正写硬件的 18 行

```c
static void __pci_setup_bridge(struct pci_bus *bus, unsigned long type)  /* :665 */
{
        struct pci_dev *bridge = bus->self;                   /* :667 */

        if (type & IORESOURCE_IO)
                pci_setup_bridge_io(bridge);                  /* :673 */
        if (type & IORESOURCE_MEM)
                pci_setup_bridge_mmio(bridge);                /* :676 */
        if (type & IORESOURCE_PREFETCH)
                pci_setup_bridge_mmio_pref(bridge);           /* :679 */
}
```

⚠️ prefetchable 那一支（`:643-663`）里有一段值得注意的写序：
先把 `PCI_PREF_BASE_UPPER32` 清零、再写 base/limit、最后写回上 32 位（`:660`）。
原注释（`:636-639`）承认这会 **短暂关掉 PREF 窗口**，并说这是可以接受的。

⚠️⚠️ 这就是资源重分配期间不能有设备正在 DMA 的原因之一：
**窗口在写的过程中有一个短暂的空档。**

⚠️ `pcibios_setup_bridge()`（`:684-686`）是本文件的 `__weak` 钩子，arm64 上为空。

---

## §6 `pci_bus_type` 的 13 个回调

⚠️⚠️ **pci 是本套 23 个子系统里唯一自己建 bus type 的**（08 §1）。
那张表就是它作为总线的全部对外契约：

```c
struct bus_type pci_bus_type = {                  /* pci-driver.c:1685 */
        .name           = "pci",                  /* :1686 */
        .match          = pci_bus_match,          /* :1687 */
        .uevent         = pci_uevent,             /* :1688 */
        .probe          = pci_device_probe,       /* :1689 */
        .remove         = pci_device_remove,      /* :1690 */
        .shutdown       = pci_device_shutdown,    /* :1691 */
        .dev_groups     = pci_dev_groups,         /* :1692 */
        .bus_groups     = pci_bus_groups,         /* :1693 */
        .drv_groups     = pci_drv_groups,         /* :1694 */
        .pm             = PCI_PM_OPS_PTR,         /* :1695 */
        .num_vf         = pci_bus_num_vf,         /* :1696 */
        .dma_configure  = pci_dma_configure,      /* :1697 */
        .dma_cleanup    = pci_dma_cleanup,        /* :1698 */
};
EXPORT_SYMBOL(pci_bus_type);                      /* :1700 */
```

逐个定位（定义行）：

| 回调 | 实现 | 要点 |
|---|---|---|
| `.match` | `pci_bus_match()` `:1517` | ⚠️ 先查 `match_driver`（§4），再 `pci_match_device()` `:136` |
| `.uevent` | `pci_uevent()` `:1568` | 生成 `PCI_CLASS` / `PCI_ID` / `PCI_SUBSYS_ID` / `PCI_SLOT_NAME` / `MODALIAS` |
| `.probe` | `pci_device_probe()` `:444` | → `__pci_device_probe()` `:407` → `pci_call_probe()` `:350`（§8） |
| `.remove` | `pci_device_remove()` `:469` | ⚠️ 调 `drv->remove()` 后做 `pci_dev_put()` 和 `pcibios_free_irq()` |
| `.shutdown` | `pci_device_shutdown()` `:512` | ⚠️⚠️ 关中断、关 bus master，但 **不** 关设备电源 |
| `.dev_groups` 等三组 | `drivers/pci/pci-sysfs.c` | sysfs 属性，07 篇展开 |
| `.pm` | `PCI_PM_OPS_PTR` `:1424` / `:1432` | ⚠️ `CONFIG_PM` 开则指向 `pci_dev_pm_ops`（`:1401`），关则 NULL |
| `.num_vf` | `pci_bus_num_vf()` `:1637` | 只有一行，转调 `pci_num_vf()` |
| `.dma_configure` | `pci_dma_configure()` `:1649` | ⚠️⚠️ arm64 的关键，§9 |
| `.dma_cleanup` | `pci_dma_cleanup()` `:1677` | 与上面配对释放 IOMMU 默认域 |

### §6.1 `.shutdown` 里那件容易踩的事

`pci_device_shutdown()`（`:512`）里做的事之一是 **关掉 bus master 位**，
目的是保证 kexec 后新内核起来之前设备不会继续 DMA。
⚠️⚠️ 它 **故意不** 把设备切到 D3——因为有些设备一进 D3 就再也醒不过来，
而 shutdown 路径不允许失败。看到 shutdown 后设备还在耗电，这是设计如此，不是 bug。

### §6.2 `pci_uevent_ers()` 不在这张表里

`pci_uevent_ers()`（`:1606`）是给 AER/错误恢复用的单独通知点，
不是 `bus_type` 回调。⚠️ 它发的是 `PCI_ERS_RESULT` 类事件，
`04` 篇的错误恢复流程会用到。放在同一个文件里容易和 `.uevent` 混淆。

---

## §7 锁：四把，各管一层

| 锁 | 定义 | 类型 | 保护什么 |
|---|---|---|---|
| `pci_lock` | `access.c:15` | ⚠️⚠️ `DEFINE_RAW_SPINLOCK` | 所有配置空间访问 |
| `pci_bus_sem` | `drivers/pci/pci.h:160`（声明） | `rw_semaphore` | `bus->devices` / 总线树链表 |
| `pci_rescan_remove_lock` | `probe.c:3323` | `mutex` | rescan 与 remove 互斥 |
| `pci_slot_mutex` | `pci.c:37` | `mutex` | slot 链表 |

另有若干局部锁：`pci_pme_list_mutex`（`pci.c:57`）· `resource_alignment_lock`（`pci.c:6570`）。

### §7.1 `pci_lock` 为什么是 raw spinlock

```c
/* drivers/pci/access.c:10-15 */
/*
 * This interrupt-safe spinlock protects all accesses to PCI
 * configuration space.
 */
DEFINE_RAW_SPINLOCK(pci_lock);
```

⚠️⚠️ `raw_` 前缀的含义：**在 `PREEMPT_RT` 上它也不会变成可睡眠的 mutex。**
原因是配置空间访问会出现在中断处理和 NMI 类路径上（错误处理、EEH）。

代价直接写在代码里：

```c
#ifdef CONFIG_PCI_LOCKLESS_CONFIG                              /* access.c:27 */
# define pci_lock_config(f)	do { (void)(f); } while (0)     /* :28 */
# define pci_unlock_config(f)	do { (void)(f); } while (0)     /* :29 */
#else
# define pci_lock_config(f)	raw_spin_lock_irqsave(&pci_lock, f)      /* :31 */
# define pci_unlock_config(f)	raw_spin_unlock_irqrestore(&pci_lock, f) /* :32 */
#endif
```

⚠️ `CONFIG_PCI_LOCKLESS_CONFIG` 打开后这把锁 **整个消失**。
能这么做的前提是硬件的配置空间访问本身是原子的（ECAM 就是，
一次 MMIO 读写完成一次配置访问）。**arm64 + ECAM 正是这种情况。**

⚠️⚠️ 所以：**读 `access.c` 时不要假设锁一定存在。**
桥接式访问（间接寄存器对：先写地址、再读数据）才真正需要它。

### §7.2 `pci_lock` 的全部临界区

| 位置 | 内容 |
|---|---|
| `access.c:35-51` | `PCI_OP_READ` 宏：`pci_lock_config()` → `bus->ops->read()` → 解锁 |
| `access.c:53-64` | `PCI_OP_WRITE` 宏，同上 |
| `access.c:66-70` 起 | 六次宏展开，生成 byte/word/dword 读写 |
| `access.c:190-193` | `pci_bus_set_ops()`：换 `pci_ops` 指针必须持锁 |
| `access.c:208-216` | ⚠️⚠️ `pci_wait_cfg()`，见下 |
| `access.c:227-232` | `PCI_USER_READ_CONFIG` 宏的临界区 |
| `access.c:249-254` | `PCI_USER_WRITE_CONFIG` 宏的临界区 |
| `access.c:278-282` | `pci_cfg_access_lock()` |
| `access.c:299-304` | `pci_cfg_access_trylock()` |
| `access.c:320-329` | `pci_cfg_access_unlock()` |

⚠️ 注意 `PCI_OP_*` 用 `_irqsave` 版本（可在中断里调），
`PCI_USER_*` 和 `pci_cfg_access_lock()` 用 `raw_spin_lock_irq()`（只能在进程上下文）。
**这个差别就是"内核路径"和"用户态发起路径"的分界线。**

### §7.3 `pci_wait_cfg()`：核心里唯一一处"持锁睡眠"

```c
static noinline void pci_wait_cfg(struct pci_dev *dev)   /* access.c:208 */
        __must_hold(&pci_lock)                            /* :209 */
{
        do {
                raw_spin_unlock_irq(&pci_lock);           /* :212 放锁 */
                wait_event(pci_cfg_wait, !dev->block_cfg_access);  /* :213 睡 */
                raw_spin_lock_irq(&pci_lock);             /* :214 重新拿 */
        } while (dev->block_cfg_access);                  /* :215 复查 */
}
```

⚠️⚠️ 这是"进入时持锁、内部放锁睡觉、出去时仍持锁"的写法，
`__must_hold` 注解（`:209`）是给静态检查器看的。
⚠️ `while` 的复查不可省：醒来之后锁是重新拿的，期间别人可能又把 `block_cfg_access` 置起来了。

它服务于 `pci_cfg_access_lock()` / `_unlock()` 这对接口，
用途写在 `access.c:198-205` 的注释里：**BIST 期间和 D 状态切换期间必须禁止访问配置空间。**

⚠️ `pci_cfg_access_unlock()` 里有一句 `WARN_ON(!dev->block_cfg_access)`（`:326`），
注释（`:322-325`）说明了取舍：**多解一次只警告，多锁一次会死等**。

### §7.4 `pci_rescan_remove_lock`

`probe.c:3323` 的 mutex，只被 `pci_lock_rescan_remove()`（`:3327`）
和 `pci_unlock_rescan_remove()`（`:3333`）用。
⚠️ 热插拔驱动和 sysfs 的 `rescan`/`remove` 都必须夹在这对函数之间，
否则会和 §5.3 里那段不加锁的资源分配打架。

---

## §8 `pci_call_probe()`：把 probe 搬到设备所在的 NUMA 节点上跑

```c
static int pci_call_probe(...)                            /* pci-driver.c:350 */
{
        node = dev_to_node(&dev->dev);                    /* :361 */
        dev->is_probed = 1;                               /* :362 */
        cpu_hotplug_disable();                            /* :364 */

        if (node < 0 || node >= MAX_NUMNODES || !node_online(node) ||
            pci_physfn_is_probed(dev)) {                  /* :370-371 */
                cpu = nr_cpu_ids;                         /* :372 不搬 */
        } else {
                cpumask_and(wq_domain_mask,
                            housekeeping_cpumask(HK_TYPE_WQ),
                            housekeeping_cpumask(HK_TYPE_DOMAIN));  /* :380-382 */
                cpu = cpumask_any_and(cpumask_of_node(node), wq_domain_mask);  /* :384 */
        }

        if (cpu < nr_cpu_ids)
                error = work_on_cpu(cpu, local_pci_probe, &ddi);   /* :390 */
        else
                error = local_pci_probe(&ddi);            /* :392 */
out:
        dev->is_probed = 0;                               /* :394 */
        cpu_hotplug_enable();                             /* :395 */
```

目的写在注释里（`:356-359`）：**让驱动在 probe 里分配的内存落在正确的 NUMA 节点上。**

三处细节：

1. ⚠️ `cpu_hotplug_disable()` 包住全程——`work_on_cpu()` 的目标 CPU 中途下线会出事。
2. ⚠️⚠️ `pci_physfn_is_probed()`（`:341`）是防嵌套：
   VF 的 probe 可能是从 PF 的 `work_on_cpu()` 里发起的，再嵌一层会死锁。
   注释在 `:366-369`。
3. ⚠️ 用 `housekeeping_cpumask()` 过滤，是为了不打扰 `nohz_full` 隔离出来的 CPU。

⚠️⚠️ **arm64 上大多数平台只有一个 NUMA 节点**，`node` 为 0 或 -1，
所以这里几乎总是走 `:392` 的直接调用。**这段 40 行代码在典型 arm64 板子上是绕路。**
但 `dev->is_probed` 这个位的设置（`:362`/`:394`）是无条件的，别以为它跟着一起绕过了。

---

## §9 `pci_dma_configure()`：arm64 上 DMA 能不能工作的分水岭

```c
static int pci_dma_configure(struct device *dev)          /* pci-driver.c:1649 */
{
        struct pci_driver *driver = to_pci_driver(dev->driver);   /* :1651 */

        bridge = pci_get_host_bridge_device(to_pci_dev(dev));     /* :1655 */

        if (IS_ENABLED(CONFIG_OF) && bridge->parent &&
            bridge->parent->of_node) {                            /* :1657-1658 */
                ret = of_dma_configure(dev, bridge->parent->of_node, true);  /* :1659 */
        } else if (has_acpi_companion(bridge)) {                  /* :1660 */
                ret = acpi_dma_configure(dev, acpi_get_dma_attr(adev));      /* :1663 */
        }

        pci_put_host_bridge_device(bridge);                       /* :1666 */

        if (!ret && !driver->driver_managed_dma) {                /* :1668 */
                ret = iommu_device_use_default_domain(dev);       /* :1669 */
                if (ret)
                        arch_teardown_dma_ops(dev);               /* :1671 */
        }
        return ret;
}
```

### §9.1 四条必须记住的结论

1. ⚠️⚠️ **DMA 属性来自主机桥的父节点，不是设备自己的 DT 节点。**
   `bridge->parent->of_node`（`:1658`）指的是控制器驱动那个 `platform_device` 的节点。
   **`dma-coherent`、`dma-ranges`、`iommu-map` 都写在那里**（06 篇）。
2. ⚠️ PCI 设备在 DT 里根本没有节点（除了极少数 `pci@` 子节点），
   所以不可能从设备自己身上取——这条路是唯一的。
3. ⚠️⚠️ `driver_managed_dma` 为假（普通驱动的默认）时，
   核心替驱动 **占住 IOMMU 的默认域**（`:1669`）。
   这就是 02 §5 里那个字段的实质：只有 VFIO 一类要自己接管 IOMMU 域的驱动才置真。
4. ⚠️ `iommu_device_use_default_domain()` 失败会调 `arch_teardown_dma_ops()`（`:1671`）
   把已经建好的 DMA ops 拆掉，然后返回错误 —— **probe 会失败**，
   现场表现是 `dma_configure` 阶段就没走到驱动的 `probe()`。

### §9.2 与 `dev->irq` 一起看的一个共同点

⚠️ `pci_setup_device()` 从配置空间读出的 `dev->irq`（§3 阶段 6）在 arm64 上是 0；
真正的中断号来自 MSI 域（`pci_set_msi_domain()`，`probe.c:2541`）
或 DT 的 `interrupt-map`。
⚠️⚠️ **DMA 和中断这两样，arm64 上都不是从 PCI 配置空间拿的，
而是从主机桥的 DT 节点拿的。** 这是 arm64 与 x86 在 pci 核心里最本质的差别。

---

## §10 一页速查

| 你要改/查的东西 | 去哪个文件的哪一段 |
|---|---|
| 设备为什么没被发现 | `probe.c:2691` `pci_scan_slot()` 的 function 0 规则（§2.3） |
| 总线号怎么分的 | `probe.c:2907` 的三遍循环（§2.2） |
| BAR 尺寸怎么算的 | `probe.c:177` `__pci_read_base()`（§3.1） |
| 驱动为什么没绑上 | `bus.c:344` 的 `match_driver`（§4） |
| 桥窗口为什么是这个值 | `setup-bus.c:874` / `:985` 的两处提前返回（§5.4） |
| `probe-only` 怎么生效的 | `setup-bus.c:889` 的 `b_res->parent` 判断（§5.4） |
| 配置空间访问的锁 | `access.c:15` + `:27-33` 的编译开关（§7.1） |
| DMA / IOMMU 从哪配的 | `pci-driver.c:1657-1672`（§9） |

### §10.1 三条反直觉的结论

1. ⚠️⚠️ **保护配置空间的那把锁在 arm64 + ECAM 上可以整个编译掉**
   （`CONFIG_PCI_LOCKLESS_CONFIG`）。它是为间接寄存器访问存在的。
2. ⚠️⚠️ **资源分配阶段不持任何拓扑锁**，靠 `pci_rescan_remove_lock` 从外面串行化；
   而它最后一行是 `BUG_ON`。这两件事放在一起看，说明这段代码假定了单线程执行。
3. ⚠️⚠️ **枚举阶段刻意禁止驱动绑定**（`match_driver = false`），
   一个 bool 位撑起了"扫描—分配资源—绑定"这三段式。
   这个位在整棵源码树里只有三处引用。

---

## 下一步读什么

| 想知道 | 去 |
|---|---|
| `pcie/` 那 12 个文件（AER / ASPM / DPC / PME / portdrv）和 SR-IOV | 04-extensions.md |
| 写一个 PCI 设备驱动的完整步骤 | 05-usage.md |
| DT 怎么写、arm64 有哪些主机桥驱动 | 06-devicetree.md |
| sysfs / proc / uapi 怎么看现场 | 07-debug.md |
| 为什么 pci 是本套唯一自建 bus 的子系统 | 08-feynman.md |
| 本篇每个数字的来源 | 99-evidence.md |
