---
title: 调试方法
---

# PCI 子系统 07 —— 调试与观测

本篇只讲一件事：**PCI 出问题时，你能从哪里读到状态、能往哪里写进去改变状态**。

代码基线 Linux 6.1.158，arm64。三个用户态面全部实现在这三个文件里：

| 面 | 文件 | 行数 | 挂载点 | initcall |
|---|---|---|---|---|
| sysfs | `drivers/pci/pci-sysfs.c:1` | 1706 | `/sys/bus/pci/`、`/sys/devices/pci*/` | `late_initcall(pci_sysfs_init)` `drivers/pci/pci-sysfs.c:1583` |
| procfs | `drivers/pci/proc.c:1` | 472 | `/proc/bus/pci/` | `device_initcall(pci_proc_init)` `drivers/pci/proc.c:472` |
| 系统调用 | `drivers/pci/syscall.c:1` | 137 | `pciconfig_read` / `pciconfig_write` | 无（syscall 表） |

第三个面在 arm64 上编不进去，见 §8 —— 但你在老代码里会看到它，所以要知道它长什么样、为什么不用它。

---

## §1 三个面的分工

### §1.1 全景

```
用户态
  │
  ├── /sys/bus/pci/devices/0000:01:00.0/     ← 每设备属性（§2 §3 §4）
  │     ├── vendor device class revision ... ← 只读身份，ascii
  │     ├── config                            ← 256/4096 字节二进制，rw
  │     ├── resource0 .. resource5            ← BAR，mmap
  │     ├── rom  vpd                          ← 可选二进制
  │     ├── enable remove reset rescan        ← 写入即动作
  │     └── aer_dev_correctable ...           ← AER 计数（§6）
  │
  ├── /sys/bus/pci/rescan                     ← 全局重扫
  ├── /sys/bus/pci/slots/<slot>/              ← drivers/pci/slot.c:268
  ├── /sys/class/pci_bus/0000:01/             ← legacy_io / legacy_mem / cpuaffinity
  ├── /sys/bus/pci_express/devices/           ← 端口服务（portdrv 建的）
  │
  └── /proc/bus/pci/
        ├── devices                           ← 一行一设备，lspci 的老数据源
        └── 01/00.0                           ← 配置空间字符设备 + 4 个 ioctl
```

两个面的**数据源完全相同**（都是 `struct pci_dev`），但权限模型、粒度、可扩展性差别很大：

| 维度 | sysfs | procfs |
|---|---|---|
| 一个文件一个值 | 是 | 否，`devices` 一行 12 个字段 |
| 配置空间访问 | `config` 二进制文件 | `01/00.0` 二进制文件 |
| 域（domain）支持 | 目录名里带 `0000:` | 只有 `pci_proc_domain(bus)` 为真时才带，`drivers/pci/proc.c:425-430` |
| mmap BAR | `resourceN`，走 `pci_mmap_resource_range()` | 走 `PCIIOC_MMAP_IS_MEM` ioctl 切状态后 mmap |
| 新属性加在哪 | 加一个 `DEVICE_ATTR` 就行 | 得改 `show_device()` 的 `seq_printf` 格式，会破坏兼容 |
| 新代码该用 | 用这个 | 只为兼容而存在 |

`drivers/pci/proc.c:384` 那行注释把 procfs 的处境说透了：

```c
/* only print standard and ROM resources to preserve compatibility */
for (i = 0; i <= PCI_ROM_RESOURCE; i++) {
```

也就是 `/proc/bus/pci/devices` 里**永远看不到 SR-IOV BAR、看不到 bridge window**，因为多打一列就会让二十年前编译的 `lspci` 解析错位。sysfs 的 `resource` 文件没有这个限制。

### §1.2 谁在读这些文件

| 工具 | 读什么 | 落点 |
|---|---|---|
| `lspci` | 优先 `/sys/bus/pci/devices/*/config`，回退 `/proc/bus/pci/*` | §3.1 |
| `lspci -v` 的 `Region` 行 | `resource` | §2.4 |
| `setpci` | `config` 的 pwrite | §3.1 |
| `dpdk` / `vfio` | `resourceN` 的 mmap（更常见的是走 vfio 而非直接 mmap） | §3.2 |
| `dmidecode -t slot` 对不上时 | `/sys/bus/pci/slots/` | `drivers/pci/slot.c:87-95` |
| `ethtool -i` 里的 `bus-info` | 设备目录名本身 | — |

---

## §2 每设备 ascii 属性

全部由 `drivers/pci/pci-sysfs.c` 用 `DEVICE_ATTR*` 宏定义，挂进 `pci_dev_attrs[]` `drivers/pci/pci-sysfs.c:638-668`。下面按"你会拿它干什么"分组。

### §2.1 身份四件套 + 一个宏

```c
/* drivers/pci/pci-sysfs.c:32-46 —— 六个属性由一个宏生成 */
#define pci_config_attr(field, format_string)                           \
static ssize_t                                                          \
field##_show(struct device *dev, struct device_attribute *attr, char *buf) \
{                                                                       \
        struct pci_dev *pdev;                                           \
        pdev = to_pci_dev(dev);                                         \
        return sysfs_emit(buf, format_string, pdev->field);             \
}                                                                       \
static DEVICE_ATTR_RO(field)
```

宏在 `drivers/pci/pci-sysfs.c:46` 收尾，展开出 `vendor`、`device`、`subsystem_vendor`、`subsystem_device`、`class`、`revision` 六个只读文件。

⚠️ 这六个属性在代码里 **grep 不到**。你搜 `dev_attr_vendor` 只能搜到 `pci_dev_attrs[]` 里的引用，搜不到定义处 —— 这是 03 篇讲过的宏生成盲区在观测面上的具体表现。要定位就搜 `pci_config_attr(`。

| 文件 | 值来自 | 备注 |
|---|---|---|
| `vendor` `device` | 配置空间 offset 0x00/0x02，探测时读进 `pci_dev` | 十六进制带 `0x` 前缀 |
| `subsystem_vendor` `subsystem_device` | offset 0x2c/0x2e | bridge 上通常是 0 |
| `class` | 24 位 class code | `lspci` 显示的那个类别名是 `pci.ids` 查表得来的，内核只给数字 |
| `revision` | offset 0x08 | ABI 文档 `Documentation/ABI/testing/sysfs-bus-pci:368` |
| `modalias` `drivers/pci/pci-sysfs.c:314` | 拼 `pci:vNNNNNNNNdNNNNNNNNsv...sd...bc..sc..i..` | udev 靠它匹配模块 |

`modalias` 是热插拔自动加载模块的唯一入口。设备插上后没自动加载驱动，第一件事是 `cat modalias` 再 `modinfo -F alias <mod>` 对比。

### §2.2 链路状态

| 文件 | 定义 | 读的是 |
|---|---|---|
| `max_link_speed` | `drivers/pci/pci-sysfs.c:199` | Link Capabilities 里协商上限 |
| `max_link_width` | `drivers/pci/pci-sysfs.c:214` | 同上，lane 数上限 |
| `current_link_speed` | `drivers/pci/pci-sysfs.c:235` | Link Status 里当前速率 |
| `current_link_width` | `drivers/pci/pci-sysfs.c:253` | 当前 lane 数 |

这四个是**排查 PCIe 链路降级的第一现场**。典型场景：设备标称 Gen3 x4，实际只跑起 Gen1 x1。

```sh
# 一条命令看全链路（端点 + 它上面每一级桥）
for d in $(basename $(readlink -f /sys/bus/pci/devices/0000:01:00.0) ); do :; done
cat /sys/bus/pci/devices/0000:01:00.0/current_link_speed   # 8.0 GT/s PCIe
cat /sys/bus/pci/devices/0000:01:00.0/current_link_width   # 4
cat /sys/bus/pci/devices/0000:00:00.0/current_link_speed   # 上游桥
```

⚠️ 降级要**逐级看**。端点报 x1 不代表端点有问题 —— 很可能是根端口和它之间某一级只训练起 1 lane。arm64 SoC 上最常见的原因是 dts 里 `num-lanes` 和实际布线不符，或 `max-link-speed` 写小了（06 §4.2）。

这四个属性挂在 `pcie_dev_attrs[]` `drivers/pci/pci-sysfs.c:677-683` 里，只对 PCIe 设备可见 —— 传统 PCI 设备的目录下没有它们。

### §2.3 拓扑

| 文件 | 定义 | 说明 |
|---|---|---|
| `secondary_bus_number` | `drivers/pci/pci-sysfs.c:272` | 只在桥上，读配置空间 `PCI_SECONDARY_BUS` `drivers/pci/pci-sysfs.c:264` |
| `subordinate_bus_number` | `drivers/pci/pci-sysfs.c:291` | 只在桥上，`PCI_SUBORDINATE_BUS` `drivers/pci/pci-sysfs.c:283` |
| `ari_enabled` | `drivers/pci/pci-sysfs.c:301` | ARI 转发是否开了 |
| `irq` | `drivers/pci/pci-sysfs.c:72` | `pdev->irq` |
| `devspec` | `drivers/pci/pci-sysfs.c:608` | ⚠️ 只在 `CONFIG_OF` 下存在 `drivers/pci/pci-sysfs.c:662-664`，值是 dts 节点全路径 |

`secondary_bus_number` / `subordinate_bus_number` 属于 `pci_bridge_attrs[]` `drivers/pci/pci-sysfs.c:670-675`。这两个数配上 `resource` 就能验证"桥的 bus 号窗口够不够"—— 06 §7 rk3568 那个 `bus-range = <0x0 0xf>` 只给了 16 个 bus，挂多级交换机会不够。

⚠️ `irq` 的值在 MSI 场景下**没有意义**。MSI-X 设备每个向量一个 IRQ 号，`pdev->irq` 只保存 INTx 或第一个 MSI 的号。真实向量列表在 `/sys/bus/pci/devices/*/msi_irqs/`，见 §2.6。

### §2.4 地址窗口

`resource` `drivers/pci/pci-sysfs.c:183` 是唯一一个把所有 BAR 一次吐出来的文件：

```
0x0000004000000000 0x00000040000fffff 0x0000000000140204
0x0000000000000000 0x0000000000000000 0x0000000000000000
...
```

每行 `start end flags`，共 `PCI_STD_NUM_BARS + 若干` 行（含 bridge window 和 ROM）。`flags` 的低位就是 `IORESOURCE_*` 位，`0x200` 是 `IORESOURCE_MEM_64`，`0x2000` 是 `IORESOURCE_PREFETCH`。

⚠️ 这里打印的是 **CPU 物理地址**，不是 PCI 总线地址。06 §7 里 rk3568 的 bus 地址 `0x40000000` 对应 CPU 地址 `0x3_40000000`，你在 `resource` 里看到的是后者。要看总线地址得看 `/sys/kernel/debug/` 下的桥窗口或者 dmesg 里的 `pci_bus 0000:00: root bus resource [mem ... (bus address ...)]`。这是"BAR 地址和固件手册上写的对不上"的头号原因。

全 0 的行 = 那个 BAR 没实现，或者没分配上。**没分配上**的情况在 dmesg 里必有一行 `BAR N: no space for`，见 §11。

### §2.5 CPU 亲和

| 文件 | 定义 | 格式 |
|---|---|---|
| `local_cpus` | `drivers/pci/pci-sysfs.c:119` | cpumask 位图，如 `ff` |
| `local_cpulist` | `drivers/pci/pci-sysfs.c:126` | 列表，如 `0-7` |
| `numa_node` | `drivers/pci/pci-sysfs.c:387`（RW） | NUMA 节点号 |

⚠️ `numa_node` 只在 `CONFIG_NUMA` 下存在（`drivers/pci/pci-sysfs.c:651-653`），并且它**可写** —— 写它是为了绕过固件报错的 node 号。单节点 arm64 SoC 上恒为 `-1`，看到 `-1` 不用管。

同样的信息在 bus 层面有一份：`cpuaffinity` `drivers/pci/pci-sysfs.c:138` 和 `cpulistaffinity` `drivers/pci/pci-sysfs.c:147`，挂在 `pcibus_attrs[]` `drivers/pci/pci-sysfs.c:685-690` 下，路径是 `/sys/class/pci_bus/0000:01/cpuaffinity`。

### §2.6 中断与 DMA

| 文件 | 位置 | 说明 |
|---|---|---|
| `msi_irqs/<N>` | 由 `msi_device_populate_sysfs()` 建，调用点 `drivers/pci/msi/legacy.c:72` | 每个已分配向量一个文件，内容是 `msi` 或 `msix` |
| `msi_bus` | `drivers/pci/pci-sysfs.c:452`（RW） | 桥上写 0 可以**禁掉整条子树的 MSI**，ABI 文档 `Documentation/ABI/testing/sysfs-bus-pci:78` |
| `dma_mask_bits` | `drivers/pci/pci-sysfs.c:397` | 驱动设的 streaming mask 位宽 |
| `consistent_dma_mask_bits` | `drivers/pci/pci-sysfs.c:405` | coherent mask 位宽 |

`ls msi_irqs/` 是确认"驱动到底拿到了几个向量"最快的办法 —— 比 `/proc/interrupts` 靠谱，因为后者只列出**已注册 handler** 的号。分配了 16 个向量但只注册了 1 个 handler，`msi_irqs/` 有 16 个文件，`/proc/interrupts` 只有 1 行。

⚠️ `msi_bus` 写 0 之后**已经在用 MSI 的设备不会立刻掉回 INTx**，只影响之后新分配的。要生效得先 unbind 驱动。

`dma_mask_bits` 显示 32 而设备本该支持 64 位，说明驱动的 `dma_set_mask_and_coherent()` 失败并回退了 —— 05 篇讲过这个回退是静默的。

---

## §3 二进制属性

这一组用 `BIN_ATTR` 而不是 `DEVICE_ATTR`，因为内容不是文本。

### §3.1 `config`

```c
/* drivers/pci/pci-sysfs.c:860 */
static BIN_ATTR(config, 0644, pci_read_config, pci_write_config, 0);
```

挂在 `pci_dev_config_attr_group` `drivers/pci/pci-sysfs.c:879`，这个 group 又列在 `pci_dev_attr_groups[]` `drivers/pci/pci-sysfs.c:1651` 里。

读侧有一个**必须知道的截断规则**：

```c
/* drivers/pci/pci-sysfs.c:716-738 */
unsigned int size = 64;
/* Several chips lock up trying to read undefined config space */
if (file_ns_capable(filp, &init_user_ns, CAP_SYS_ADMIN))
        size = dev->cfg_size;                          /* :727 */
else if (dev->hdr_type == PCI_HEADER_TYPE_CARDBUS)
        size = 128;                                    /* :729 */
if (off > size)
        return 0;
```

| 你是谁 | 能读到 | 后果 |
|---|---|---|
| root（`CAP_SYS_ADMIN`） | `dev->cfg_size`，即 256 或 4096 | 全部 capability 可见 |
| 普通用户 | **前 64 字节** | 看不到任何 capability，`lspci` 输出残缺 |

所以"普通用户跑 `lspci -vv` 看不到 Capabilities" 不是 bug，是 `drivers/pci/pci-sysfs.c:721` 那个 `size = 64` 决定的。注释 `:725` 给了原因：有些芯片读未定义的配置空间会挂死。

写侧多一道锁：

```c
/* drivers/pci/pci-sysfs.c:801 */
ret = security_locked_down(LOCKDOWN_PCI_ACCESS);
```

⚠️ 开了 kernel lockdown（Secure Boot 常带）之后，`setpci` 会直接 `-EPERM`，和权限无关。这是"root 也写不进 config" 的唯一常见原因。

### §3.2 `resourceN` 与 `resourceN_wc`

由 `pci_create_attr()` `drivers/pci/pci-sysfs.c:1215` 逐 BAR 创建，入口 `pci_create_resource_files()` `drivers/pci/pci-sysfs.c:1270`：

```c
/* drivers/pci/pci-sysfs.c:1282-1286 */
retval = pci_create_attr(pdev, i, 0);                          /* resourceN   */
if (!retval && arch_can_pci_mmap_wc() && ...)
        retval = pci_create_attr(pdev, i, 1);                  /* resourceN_wc */
```

| 属性 | 何时出现 | 权限 |
|---|---|---|
| `resourceN` | BAR N 有长度 | mmap 恒可；read/write 仅 `IORESOURCE_IO` |
| `resourceN_wc` | 且 `arch_can_pci_mmap_wc()` 为真、BAR 可预取 | 只 mmap |

那条"rw 只对 IO 区"的规则写在 `Documentation/PCI/sysfs-pci.rst:66`（脚注 `[1]`），代码侧体现在 `drivers/pci/pci-sysfs.c:1238-1241`：只有 `arch_can_pci_mmap_io()` 为真时 IO BAR 才装 mmap 钩子。

mmap 路径同样过 lockdown：`drivers/pci/pci-sysfs.c:1099`。还过一道边界检查 `pci_mmap_fits(pdev, bar, vma, PCI_MMAP_SYSFS)` `drivers/pci/pci-sysfs.c:1106` —— 想 mmap 超出 BAR 长度会 `-EINVAL`，这是有意的，不要靠改 offset 绕。

⚠️ arm64 上 `resourceN` 是 **Device 内存**属性映射，不是 Normal。用户态对它做 `memcpy` 可能触发未对齐访问 fault。要按宽度访问。

### §3.3 `rom`

```c
/* drivers/pci/pci-sysfs.c:1363 */
static BIN_ATTR(rom, 0600, pci_read_rom, pci_write_rom, 0);
```

`Documentation/PCI/sysfs-pci.rst:83-89` 给了完整用法，三个前提**缺一不可**：

1. 先 `echo 1 > rom` 打开（写入走 `pci_write_rom` `drivers/pci/pci-sysfs.c:1337`，过 lockdown `drivers/pci/pci-sysfs.c:1180`）。
2. 设备必须处于 enabled 状态 —— 没绑驱动就先 `echo 1 > enable`（`Documentation/PCI/sysfs-pci.rst:87-89`）。
3. 读完 `echo 0 > rom` 关掉。

漏掉第 1 步的表现是 `read` 返回 0 字节，看着像"这卡没 ROM"。属性本身由 `pci_dev_rom_attr_group` `drivers/pci/pci-sysfs.c:1386` 控制可见性，没有 ROM 资源的设备连文件都不出现。

### §3.4 `vpd`

```c
/* drivers/pci/vpd.c:290 */
static BIN_ATTR(vpd, 0600, vpd_read, vpd_write, 0);
```

group 在 `drivers/pci/vpd.c:308`，初始化入口 `pci_vpd_init()` `drivers/pci/vpd.c:264`。ABI 说明 `Documentation/ABI/testing/sysfs-bus-pci:177`。

VPD 是网卡上刻的序列号/型号字符串。⚠️ 读它会**真的走配置空间轮询硬件**，有的固件实现得很差会卡几百毫秒。别在监控脚本里高频 `cat vpd`。

### §3.5 legacy 面

```c
/* drivers/pci/pci-sysfs.c:1011-1016 */
b->legacy_io->attr.name = "legacy_io";
b->legacy_io->size = 0xffff;
b->legacy_io->attr.mode = 0600;
b->legacy_io->read = pci_read_legacy_io;
b->legacy_io->write = pci_write_legacy_io;
b->legacy_io->mmap = pci_mmap_legacy_io;
```

`legacy_mem` 紧跟着分配（`drivers/pci/pci-sysfs.c:1024`，注释 `:1023` 说明它是 `legacy_io + 1`，一次 `kcalloc(2, ...)` `drivers/pci/pci-sysfs.c:1005` 分配两个），大小 1 MB `drivers/pci/pci-sysfs.c:1027`。用法在 `Documentation/PCI/sysfs-pci.rst:111-117`。

⚠️ **arm64 上这两个文件不存在**。它们要 `HAVE_PCI_LEGACY`（`Documentation/PCI/sysfs-pci.rst:136-138`），arm64 不定义。所以 VGA 的 0xa0000 那套老把戏在 arm64 上没有。这一段留在文里只是为了让你在别人的 x86 脚本里认出它。

---

## §4 写入即动作的属性

这一节的每个文件，`echo` 进去就会改变硬件或内核状态。全部要 root，多数还要 `CAP_SYS_ADMIN` 显式检查。

### §4.1 `enable` —— 它是计数器，不是开关

```c
/* drivers/pci/pci-sysfs.c:316-351 */
static ssize_t enable_store(...)
{
        /* this can crash the machine when done on the "wrong" device */   /* :323 */
        if (!capable(CAP_SYS_ADMIN))
                return -EPERM;                                            /* :324-325 */
        device_lock(dev);                                                 /* :330 */
        if (dev->driver)
                result = -EBUSY;                                          /* :331-332 */
        else if (val)
                result = pci_enable_device(pdev);                         /* :334 */
        else if (pci_is_enabled(pdev))
                pci_disable_device(pdev);                                 /* :336 */
        else
                result = -EIO;                                            /* :338 */
        device_unlock(dev);
        ...
}
static ssize_t enable_show(...)
{ return sysfs_emit(buf, "%u\n", atomic_read(&pdev->enable_cnt)); }        /* :350 */
```

三个反直觉点：

| 现象 | 原因 |
|---|---|
| `cat enable` 返回 `4` | 它是 `enable_cnt` 引用计数，不是布尔。`Documentation/PCI/sysfs-pci.rst:77-81` 明说"echo 1 进去会从 4 变 5" |
| 绑了驱动就 `-EBUSY` | `drivers/pci/pci-sysfs.c:331-332`。想手动 enable 必须先 unbind |
| 归零了设备也没完全复位 | `Documentation/PCI/sysfs-pci.rst:80-81`："even when it returns to 0, some of the initialisation may not be reversed" |

`drivers/pci/pci-sysfs.c:323` 那句注释值得抄下来：往"错的设备"上写这个能让机器当场死。

### §4.2 `remove` 与 `rescan`

```c
/* drivers/pci/pci-sysfs.c:514-515 */
if (device_remove_file_self(dev, attr))
        pci_stop_and_remove_bus_device_locked(to_pci_dev(dev));
```

属性定义 `DEVICE_ATTR_IGNORE_LOCKDEP(remove, 0220, ...)` `drivers/pci/pci-sysfs.c:518` —— 用 `IGNORE_LOCKDEP` 是因为它在自己的 store 里删自己的文件，正常会被 lockdep 判环。

对应的三个 rescan：

| 路径 | store 函数 | 干什么 |
|---|---|---|
| `/sys/bus/pci/rescan` | `BUS_ATTR_WO(rescan)` `drivers/pci/pci-sysfs.c:470`，数组 `drivers/pci/pci-sysfs.c:472` | 扫全部根总线 |
| `<dev>/rescan` | `dev_rescan_store` `drivers/pci/pci-sysfs.c:486`，属性 `drivers/pci/pci-sysfs.c:503` | `pci_rescan_bus(pdev->bus)` `drivers/pci/pci-sysfs.c:498` |
| `<dev>/pci_bus/<B>/rescan` | `bus_rescan_store` `drivers/pci/pci-sysfs.c:521`，属性 `drivers/pci/pci-sysfs.c:541` | 空桥走 `pci_rescan_bus_bridge_resize()` `drivers/pci/pci-sysfs.c:533-534`，否则 `pci_rescan_bus()` `drivers/pci/pci-sysfs.c:536` |

⚠️ `remove` + `rescan` 这套是**排查驱动 probe/remove 路径的标准手法**，比 `rmmod`/`modprobe` 更接近真实热插拔（它真的走 `pci_stop_and_remove_bus_device`）。但 `Documentation/PCI/sysfs-pci.rst:91-96` 提醒两点：写的是**非零整数**（不是字符串 `"remove"`），且**根总线不允许 remove**。

⚠️ `remove` 和 `rescan` 这两个属性对 VF **不可见**：

```c
/* drivers/pci/pci-sysfs.c:1609-1619 */
static umode_t pci_dev_hp_attrs_are_visible(...)
{
        struct pci_dev *pdev = to_pci_dev(kobj_to_dev(kobj));
        if (pdev->is_virtfn)
                return 0;                                    /* :1615-1616 */
        return a->mode;
}
```

VF 的生命周期由 PF 的 `sriov_numvfs` 管，不能单独摘。

### §4.3 复位三兄弟

| 文件 | 定义 | 作用域 | ABI |
|---|---|---|---|
| `reset` | `drivers/pci/pci-sysfs.c:1412`，数组 `drivers/pci/pci-sysfs.c:1414`，group `drivers/pci/pci-sysfs.c:1430` | 单个 function | `Documentation/ABI/testing/sysfs-bus-pci:156` |
| `reset_method` | `DEVICE_ATTR_RW` `drivers/pci/pci.c:5376`，group `drivers/pci/pci.c:5394` | 选哪种复位、按什么顺序 | `Documentation/ABI/testing/sysfs-bus-pci:139` |
| `reset_subordinate` | `drivers/pci/pci-sysfs.c:567` | 桥下**整条子树** | `Documentation/ABI/testing/sysfs-bus-pci:166` |

`reset_method` 是这三个里最有用也最容易被忽略的。读出来是一串名字，写进去改顺序：

```sh
cat /sys/bus/pci/devices/0000:01:00.0/reset_method
# flr bus

echo "bus" > .../reset_method     # 只允许总线复位，禁掉 FLR
echo "" > .../reset_method        # 彻底禁止复位这个设备
echo "default" > .../reset_method # 恢复全部支持的方法
```

行为定义在 `reset_method_store()` `drivers/pci/pci.c:5310`：空串走 `pdev->reset_methods[0] = 0` `drivers/pci/pci.c:5320`，`"default"` 走 `pci_init_reset_methods(pdev)` `drivers/pci/pci.c:5326`，其余按名字查表 `reset_method_lookup()` `drivers/pci/pci.c:5298`。

⚠️ **这是绕过坏 FLR 实现的标准手段**。有些设备声称支持 FLR 但复位后寄存器进入未定义状态，表现是 vfio 直通启动虚机时设备不响应。`echo "bus" > reset_method` 就能验证是不是 FLR 的问题 —— 不用改内核。

⚠️ `reset_subordinate` 的 ABI 文字 `Documentation/ABI/testing/sysfs-bus-pci:172-175` 写得很客气（"use with caution"），实际含义是：**桥下所有设备同时被复位**，包括你的根文件系统所在的 NVMe，如果它恰好在这条子树上。代码侧 `drivers/pci/pci-sysfs.c:559` 调 `__pci_reset_bus(bus)`，`CAP_SYS_ADMIN` 检查在 `drivers/pci/pci-sysfs.c:552-553`。

### §4.4 `resourceN_resize`

由宏 `pci_dev_resource_resize_attr(n)` 为 BAR 0..5 各生成一个（`drivers/pci/pci-sysfs.c:1511-1516`），属性 `DEVICE_ATTR_RW(resource##n##_resize)` `drivers/pci/pci-sysfs.c:1509`，数组 `drivers/pci/pci-sysfs.c:1518`，group `drivers/pci/pci-sysfs.c:1536`。

store 做的事按顺序是：

```
device_lock                                             :1505 对应的 unlock
  ├─ VGA 设备先踢掉抢占 aperture 的 fbdev     :1473-1477
  ├─ 清 PCI_COMMAND_MEMORY                    :1480-1482
  ├─ pci_remove_resource_files                :1486   ← sysfs 文件先删掉
  ├─ 释放同 flags 的所有 BAR                  :1488-1492
  ├─ pci_resize_resource(pdev, n, size)       :1494
  ├─ pci_assign_unassigned_bus_resources      :1496   ← 重新分配整条总线
  ├─ pci_create_resource_files                :1498   ← 重建文件，失败只 warn :1499
  └─ 恢复 PCI_COMMAND                         :1501
```

可见性由 `resource_resize_is_visible()` `drivers/pci/pci-sysfs.c:1528` 决定：`pci_rebar_get_current_size(pdev, n) < 0` 就整个隐藏 `drivers/pci/pci-sysfs.c:1533`。也就是**只有实现了 Resizable BAR capability 的 BAR 才有这个文件**，GPU 之外基本见不到。

⚠️ `drivers/pci/pci-sysfs.c:1498-1499` 那个 warn 是个真陷阱：resize 成功了但文件重建失败，你会得到一个 BAR 已经变大、但 `resourceN` 文件消失的设备，只有 dmesg 里一行 `Failed to recreate resource files after BAR resizing`。

### §4.5 其余可写属性

| 文件 | 定义 | 写它的场合 |
|---|---|---|
| `driver_override` | `drivers/pci/pci-sysfs.c:636` | 强制某设备绑到 `vfio-pci`，ABI `Documentation/ABI/testing/sysfs-bus-pci:334` |
| `d3cold_allowed` | `drivers/pci/pci-sysfs.c:594` | ⚠️ 只在 `CONFIG_PM && CONFIG_ACPI` 下存在（`drivers/pci/pci-sysfs.c:659-661`），**arm64 无 ACPI 时看不到** |
| `broken_parity_status` | `drivers/pci/pci-sysfs.c:96` | 屏蔽误报奇偶校验错的老设备 |
| `msi_bus` | `drivers/pci/pci-sysfs.c:452` | 见 §2.6 |
| `numa_node` | `drivers/pci/pci-sysfs.c:387` | 见 §2.5 |
| `sriov_numvfs` `sriov_drivers_autoprobe` | `drivers/pci/iov.c` | ABI `Documentation/ABI/testing/sysfs-bus-pci:313` / `:375` |

`driver_override` 是把设备从原生驱动手里抢过来给 vfio 的正规做法，比 `pci-stub` 的 `ids=` 模块参数（`drivers/pci/pci-stub.c:24`）灵活，因为它按设备而不是按 ID。

---

## §5 属性可见性 —— 为什么你的设备目录里没有那个文件

这是查 sysfs 时最容易卡住的地方。PCI 用了三层过滤，任一层不满足文件就不出现，**不报错、不留日志**。

### §5.1 三层过滤

| 层 | 机制 | 例子 |
|---|---|---|
| 编译期 | `#ifdef` 包住 `pci_dev_attrs[]` 的成员 | `numa_node` / `d3cold_allowed` / `devspec` |
| group 级 | 不同 group 挂给不同设备类型 | `pci_bridge_attr_group` 只给桥 |
| 属性级 | `is_visible` 回调返回 0 | `boot_vga` / hp attrs / resize / AER |

### §5.2 `pci_dev_attr_groups[]` 全表

数组本体在 `drivers/pci/pci-sysfs.c:1686`，成员及其条件：

| group | 定义 | 出现条件 |
|---|---|---|
| `pci_dev_group` | `drivers/pci/pci-sysfs.c:1645`，attrs `drivers/pci/pci-sysfs.c:1649` | 恒有 |
| `pci_dev_config_attr_group` | `drivers/pci/pci-sysfs.c:879` | 恒有（`config`） |
| `pci_dev_rom_attr_group` | `drivers/pci/pci-sysfs.c:1386` | 有 ROM 资源 |
| `pci_dev_reset_attr_group` | `drivers/pci/pci-sysfs.c:1430` | 设备支持复位 |
| `pci_dev_reset_method_attr_group` | `drivers/pci/pci.c:5394` | 同上，可见性 `drivers/pci/pci.c:5383` |
| `pci_dev_attr_group` | `drivers/pci/pci-sysfs.c:1671` | attrs `drivers/pci/pci-sysfs.c:1585`，`is_visible` `drivers/pci/pci-sysfs.c:1590` |
| `pci_dev_hp_attr_group` | `drivers/pci/pci-sysfs.c:1666` | attrs `drivers/pci/pci-sysfs.c:1603`，非 VF |
| `pci_bridge_attr_group` | `drivers/pci/pci-sysfs.c:1676` | 桥 |
| `pcie_dev_attr_group` | `drivers/pci/pci-sysfs.c:1681` | PCIe 设备 |
| `pci_dev_vpd_attr_group` | `drivers/pci/vpd.c:308` | 有 VPD capability |
| `pci_dev_resource_resize_group` | `drivers/pci/pci-sysfs.c:1536` | 有 Resizable BAR |
| `aer_stats_attr_group` | `drivers/pci/pcie/aer.c:618` | `pdev->aer_stats` 非空 |

### §5.3 两个具体的 `is_visible`

```c
/* drivers/pci/pci-sysfs.c:1590-1601 —— boot_vga 只给 VGA */
static umode_t pci_dev_attrs_are_visible(struct kobject *kobj,
                                         struct attribute *a, int n)
{
        struct pci_dev *pdev = to_pci_dev(kobj_to_dev(kobj));
        if (a == &dev_attr_boot_vga.attr)
                if ((pdev->class >> 8) != PCI_CLASS_DISPLAY_VGA)
                        return 0;                             /* :1596-1598 */
        return a->mode;
}
```

`boot_vga` 的 show 在 `drivers/pci/pci-sysfs.c:714`，数组在 `drivers/pci/pci-sysfs.c:1585-1588`（只有它一个成员）。

AER 那个更典型，见 §6.2。

### §5.4 排查动作

文件不见了，按这个顺序查：

1. `ls /sys/bus/pci/devices/<D>/` 确认真的没有，不是路径写错。
2. 在 `drivers/pci/pci-sysfs.c` 里 grep 属性名，找到它属于哪个数组。
3. 看那个数组成员上有没有 `#ifdef` —— 有就去查 `.config`。
4. 看它所在 group 有没有 `is_visible` —— 有就读那个函数的条件。
5. 都不是的话，才怀疑内核版本不带这个属性（对着 `Documentation/ABI/testing/sysfs-bus-pci` 里的 `Date:` 字段核对）。

### §5.5 SR-IOV 那一组

PF 侧属性集中在 `drivers/pci/iov.c`：`sriov_totalvfs` `drivers/pci/iov.c:531`、`sriov_numvfs` `drivers/pci/iov.c:532`（RW）、`sriov_offset` `drivers/pci/iov.c:533`、`sriov_stride` `drivers/pci/iov.c:534`、`sriov_vf_device` `drivers/pci/iov.c:535`、`sriov_drivers_autoprobe` `drivers/pci/iov.c:536`，数组 `drivers/pci/iov.c:539-540`，group `sriov_pf_dev_attr_group` `drivers/pci/iov.c:562`。VF 侧只有两个：`sriov_vf_total_msix` `drivers/pci/iov.c:221` 和 `sriov_vf_msix_count` `drivers/pci/iov.c:262`，group `drivers/pci/iov.c:284`。

⚠️ `sriov_numvfs_show()` `drivers/pci/iov.c:394` 特意加了锁，注释 `drivers/pci/iov.c:401` 说明原因：和 `sriov_numvfs_store()` `drivers/pci/iov.c:416` 串行化，否则读者会看到中间状态的 VF 数。这意味着**创建 VF 期间 `cat sriov_numvfs` 会阻塞**，脚本里加超时。

开 VF 前先 `echo 0 > sriov_drivers_autoprobe`，否则 VF 一出现就被 PF 驱动的 VF 分支抢走，vfio 直通拿不到 —— ABI 说明 `Documentation/ABI/testing/sysfs-bus-pci:375`。

---

## §6 AER 错误统计

PCIe 的错误计数不在 `config` 里手抠，内核把它们展成 sysfs 文件。

### §6.1 六个文件

```c
/* drivers/pci/pcie/aer.c:589-597 */
static struct attribute *aer_stats_attrs[] __ro_after_init = {
        &dev_attr_aer_dev_correctable.attr,
        &dev_attr_aer_dev_fatal.attr,
        &dev_attr_aer_dev_nonfatal.attr,
        &dev_attr_aer_rootport_total_err_cor.attr,
        &dev_attr_aer_rootport_total_err_fatal.attr,
        &dev_attr_aer_rootport_total_err_nonfatal.attr,
        NULL
};
```

前三个由宏 `aer_stats_dev_attr()` `drivers/pci/pcie/aer.c:535` 生成，实例化在 `drivers/pci/pcie/aer.c:562`、`:565`、`:568`；后三个由 `aer_stats_rootport_attr()` `drivers/pci/pcie/aer.c:572` 生成，实例化 `drivers/pci/pcie/aer.c:582`、`:584`、`:586`。

前三个的输出是**逐错误类型一行**：

```c
/* drivers/pci/pcie/aer.c:546-557 */
for (i = 0; i < ARRAY_SIZE(pdev->aer_stats->stats_array); i++) {
        ...
        len += sysfs_emit_at(buf, len, "%s %llu\n", ...);        /* :548 */
}
len += sysfs_emit_at(buf, len, "TOTAL_%s %llu\n", total_string,
                     pdev->aer_stats->total_field);              /* :556-557 */
```

所以 `cat aer_dev_correctable` 长这样，最后一行必是 `TOTAL_ERR_COR`：

```
RxErr 0
BadTLP 12
BadDLLP 0
Rollover 0
Timeout 0
NonFatalErr 0
CorrIntErr 0
HeaderOF 0
TOTAL_ERR_COR 12
```

字段含义见 `Documentation/ABI/testing/sysfs-bus-pci-devices-aer_stats:12`（correctable）、`:33`（fatal）、`:63`（nonfatal），根端口三项在 `:103`、`:109`、`:115`。

⚠️ 这些计数**只统计内核 AER 服务处理过的错误**。如果固件（FF-A / SDEI）先把 AER 中断吃掉了，或者 `pcie_ports=compat`（§10）把原生 AER 关了，计数恒为 0，而链路其实一直在报错。

### §6.2 为什么根端口那三个文件在端点上不存在

```c
/* drivers/pci/pcie/aer.c:599-616 */
static umode_t aer_stats_attrs_are_visible(struct kobject *kobj,
                                           struct attribute *a, int n)
{
        struct pci_dev *pdev = to_pci_dev(dev);

        if (!pdev->aer_stats)
                return 0;                                        /* :605-606 */

        if ((a == &dev_attr_aer_rootport_total_err_cor.attr || ...) &&
            ((pci_pcie_type(pdev) != PCI_EXP_TYPE_ROOT_PORT) &&
             (pci_pcie_type(pdev) != PCI_EXP_TYPE_RC_EC)))
                return 0;                                        /* :611-613 */

        return a->mode;
}
```

两层判断：

| 条件 | 后果 |
|---|---|
| `pdev->aer_stats == NULL` | 六个文件**全部消失** |
| 不是 Root Port 也不是 RC Event Collector | 只有前三个存在 |

`aer_stats` 在 `drivers/pci/pcie/aer.c:383` 用 `kzalloc` 分配，释放在 `drivers/pci/pcie/aer.c:404-405`。分配失败或 AER 没启用，整组就没了 —— 这就是"我的 NVMe 上没有 aer_dev_correctable 文件"的答案：不是内核老，是 AER 服务没在这个设备上跑起来。

计数递增点：设备级 `pci_dev_aer_stats_incr()` `drivers/pci/pcie/aer.c:623`，被 `drivers/pci/pcie/aer.c:706` 调用；根端口级 `pci_rootport_aer_stats_incr()` `drivers/pci/pcie/aer.c:656`，被 `drivers/pci/pcie/aer.c:1137` 调用。

### §6.3 主动注入错误来验证通路

`drivers/pci/pcie/aer_inject.c` 提供 `CONFIG_PCIEAER_INJECT`，有一个模块参数：

```c
/* drivers/pci/pcie/aer_inject.c:32 */
module_param(aer_mask_override, bool, 0);
```

⚠️ 这个模块**只伪造软件侧的错误状态寄存器**，不真的在链路上制造错误。它能验证"AER 服务 → 驱动 `err_handler` 回调 → 恢复流程"这条软件通路（05 §10），不能验证硬件是否真的报得上来。两者都要测的话，硬件侧只能靠拔插/垫片这类物理手段。

---

## §7 `/proc/bus/pci`

### §7.1 目录结构从哪来

```c
/* drivers/pci/proc.c:460-472 */
static int __init pci_proc_init(void)
{
        struct pci_dev *dev = NULL;
        proc_bus_pci_dir = proc_mkdir("bus/pci", NULL);          /* :463 */
        proc_create_seq("devices", 0, proc_bus_pci_dir,
                    &proc_bus_pci_devices_op);                   /* :464-465 */
        proc_initialized = 1;                                    /* :466 */
        for_each_pci_dev(dev)
                pci_proc_attach_device(dev);                     /* :467-468 */
        return 0;
}
device_initcall(pci_proc_init);                                  /* :472 */
```

注意那个 `proc_initialized` 标志：`pci_proc_attach_device()` 一开头就检查它 `drivers/pci/proc.c:421-422`，没初始化就返回 `-EACCES`。因为 `pci_proc_init` 是 `device_initcall`，而 PCI 设备在 `postcore_initcall` 阶段就扫出来了 —— 早期设备的 proc 节点是靠 `:467-468` 这个补扫循环建起来的，晚期热插拔的设备才走正常的 attach 路径。

目录名规则：

```c
/* drivers/pci/proc.c:424-436 */
if (!bus->procdir) {
        if (pci_proc_domain(bus))
                sprintf(name, "%04x:%02x", pci_domain_nr(bus), bus->number);  /* :426-427 */
        else
                sprintf(name, "%02x", bus->number);                            /* :429 */
        bus->procdir = proc_mkdir(name, proc_bus_pci_dir);                     /* :431 */
}
sprintf(name, "%02x.%x", PCI_SLOT(dev->devfn), PCI_FUNC(dev->devfn));          /* :436 */
```

⚠️ 目录名**带不带 domain 前缀取决于 `pci_proc_domain(bus)`**，也就是 `PCI_ENABLE_PROC_DOMAINS` 标志（06 §4.3 的标志表）。同一台机器上开关这个标志，`/proc/bus/pci/01/00.0` 和 `/proc/bus/pci/0000:01/00.0` 两种路径都可能出现。写脚本别硬编码。

文件大小设成 `dev->cfg_size` `drivers/pci/proc.c:441`，权限 `S_IRUGO | S_IWUSR` `drivers/pci/proc.c:437`。

### §7.2 `devices` 一行怎么读

`show_device()` `drivers/pci/proc.c:367` 的格式是：

```c
/* drivers/pci/proc.c:377-382 */
seq_printf(m, "%02x%02x\t%04x%04x\t%x",
                dev->bus->number, dev->devfn,
                dev->vendor, dev->device,
                dev->irq);
```

| 字段序号 | 内容 | 宽度 |
|---|---|---|
| 1 | `bus << 8 | devfn`，两个 `%02x` 拼在一起 | 4 hex |
| 2 | `vendor << 16 | device` 拼在一起 | 8 hex |
| 3 | `dev->irq` | 变长 |
| 4..10 | 7 个资源 start（含 flags 低位），`drivers/pci/proc.c:385-391` | 各 16 hex |
| 11..17 | 同 7 个资源的长度，`drivers/pci/proc.c:392-398` | 各 16 hex |
| 18 | 驱动名（有绑定才有），`drivers/pci/proc.c:400-401` | 变长 |

start 值上 OR 了 `flags & PCI_REGION_FLAG_MASK` `drivers/pci/proc.c:390` —— 所以**不能直接当地址用**，低 4 位是标志。长度那一列在 `start >= end` 时给 0 `drivers/pci/proc.c:396-397`。

两列都走 `pci_resource_to_user()`（`drivers/pci/proc.c:387`、`:394`），也就是转成"用户视角"的地址；arm64 上这是恒等变换，别的架构上不是。

### §7.3 四个 ioctl

定义在 `include/uapi/linux/pci.h:36-40`：

```c
#define PCIIOC_BASE             ('P' << 24 | 'C' << 16 | 'I' << 8)
#define PCIIOC_CONTROLLER       (PCIIOC_BASE | 0x00)  /* Get controller for PCI device. */
#define PCIIOC_MMAP_IS_IO       (PCIIOC_BASE | 0x01)  /* Set mmap state to I/O space. */
#define PCIIOC_MMAP_IS_MEM      (PCIIOC_BASE | 0x02)  /* Set mmap state to MEM space. */
#define PCIIOC_WRITE_COMBINE    (PCIIOC_BASE | 0x03)  /* Enable/disable write-combining. */
```

实现在 `proc_bus_pci_ioctl()` `drivers/pci/proc.c:194`：

| ioctl | 行为 | 落点 |
|---|---|---|
| `PCIIOC_CONTROLLER` | 返回 `pci_domain_nr(dev->bus)` | `drivers/pci/proc.c:209` |
| `PCIIOC_MMAP_IS_IO` | 置 `fpriv->mmap_state = pci_mmap_io`；`arch_can_pci_mmap_io()` 假则 `-EINVAL` | `drivers/pci/proc.c:213-217` |
| `PCIIOC_MMAP_IS_MEM` | 置 `pci_mmap_mem`，**无条件成功** | `drivers/pci/proc.c:219-221` |
| `PCIIOC_WRITE_COMBINE` | 置 `fpriv->write_combine`；`arch_can_pci_mmap_wc()` 假则 fallthrough 到 `-EINVAL` | `drivers/pci/proc.c:223-236` |

整个 ioctl 入口第一件事就是 lockdown 检查：

```c
/* drivers/pci/proc.c:203-205 */
ret = security_locked_down(LOCKDOWN_PCI_ACCESS);
if (ret)
        return ret;
```

⚠️ 三个 `HAVE_PCI_MMAP` 相关的 ioctl 被 `#ifdef` 包着（`drivers/pci/proc.c:212`、`:233`）。arm64 定义 `ARCH_GENERIC_PCI_MMAP_RESOURCE`（`Documentation/PCI/sysfs-pci.rst:122-125`），但 procfs 的 mmap 路径要的是 `HAVE_PCI_MMAP`。所以**在 arm64 上这三个 ioctl 大概率直接 `-EINVAL`**，只有 `PCIIOC_CONTROLLER` 能用。想 mmap BAR 就走 sysfs 的 `resourceN`。

procfs 的 mmap 还有一道权限：

```c
/* drivers/pci/proc.c:250 */
if (!capable(CAP_SYS_RAWIO) || ...
```

要的是 `CAP_SYS_RAWIO`，和 sysfs 那边的 `CAP_SYS_ADMIN` 不是一个 —— 这是两个面权限模型不统一的一个例子。

---

## §8 `pciconfig_read` / `pciconfig_write` 系统调用

`drivers/pci/syscall.c` 只有 137 行，头 6 行注释就说明了它的定位（`drivers/pci/syscall.c:2-6`）：给"想让用户态直接读写配置空间"的架构用，x86 上没必要因为大家直接抠北桥寄存器。

```c
/* drivers/pci/syscall.c:15-51 */
SYSCALL_DEFINE5(pciconfig_read, unsigned long, bus, unsigned long, dfn,
                unsigned long, off, unsigned long, len, void __user *, buf)
{
        err = -EPERM;
        if (!capable(CAP_SYS_ADMIN))
                goto error;                                      /* :26-27 */
        err = -ENODEV;
        dev = pci_get_domain_bus_and_slot(0, bus, dfn);           /* :30 */
        if (!dev)
                goto error;
        switch (len) {
        case 1: cfg_ret = pci_user_read_config_byte(dev, off, &byte); break;   /* :36 */
        case 2: cfg_ret = pci_user_read_config_word(dev, off, &word); break;   /* :39 */
        case 4: cfg_ret = pci_user_read_config_dword(dev, off, &dword); break; /* :42 */
        default: err = -EINVAL; goto error;                       /* :44-46 */
        }
        err = -EIO;
        if (cfg_ret)
                goto error;                                       /* :49-51 */
        ...
}
```

三条要记住的：

| 事实 | 依据 |
|---|---|
| **domain 恒为 0** | `pci_get_domain_bus_and_slot(0, ...)` `drivers/pci/syscall.c:30` 第一个参数硬编码 |
| 只支持 1/2/4 字节 | `drivers/pci/syscall.c:34-47`，其余 `-EINVAL` |
| 配置空间错误统一变成 `-EIO` | `drivers/pci/syscall.c:49-51`，丢掉了底层的具体错误 |

⚠️ **arm64 不注册这两个系统调用**。它们在 `include/uapi/asm-generic/unistd.h` 里没有条目，只有 alpha/ia64/parisc/powerpc/sparc 那几个架构在自己的 syscall 表里挂了。所以 arm64 上这个文件编出来的代码是死码。写工具**不要用它** —— 用 `/sys/bus/pci/devices/*/config` 的 pread/pwrite，那条路在所有架构上都通、支持所有 domain、支持任意长度。

它调的 `pci_user_read_config_*` 一族本身是宏生成的（`drivers/pci/access.c:239` 的读侧、`:257` 的写侧），grep 找不到定义 —— 又一处宏盲区，记进 99 篇。

---

## §9 内核日志

### §9.1 用对打印宏

```c
/* include/linux/pci.h:2576-2581 */
#define pci_err(pdev, fmt, arg...)      dev_err(&(pdev)->dev, fmt, ##arg)
#define pci_warn(pdev, fmt, arg...)     dev_warn(&(pdev)->dev, fmt, ##arg)
#define pci_warn_once(pdev, fmt, arg...) dev_warn_once(&(pdev)->dev, fmt, ##arg)
#define pci_notice(pdev, fmt, arg...)   dev_notice(&(pdev)->dev, fmt, ##arg)
#define pci_info(pdev, fmt, arg...)     dev_info(&(pdev)->dev, fmt, ##arg)
#define pci_dbg(pdev, fmt, arg...)      dev_dbg(&(pdev)->dev, fmt, ##arg)
```

还有两个限流版：`pci_notice_ratelimited` `include/linux/pci.h:2583`、`pci_info_ratelimited` `include/linux/pci.h:2586`。

用 `pci_*` 而不是 `pr_*` 的唯一理由：**前缀自动带上 `pci 0000:01:00.0:`**。用 `pr_info` 打出来的行没有设备标识，多设备系统上等于没法用。PCI 核心自己就有 52 处 `pci_dbg`/`pci_info`/`pci_err`（`drivers/pci/pci.c`）。

### §9.2 打开 `pci_dbg`

`pci_dbg` 走 `dev_dbg`，也就是 dynamic debug。三种打开方式：

```sh
# 1. 启动参数（最常用，能抓到 postcore/late initcall 阶段的输出）
#    dyndbg 用法见 Documentation/admin-guide/dynamic-debug-howto.rst:49
pci.dyndbg=+p                                # PCI 核心（内建时无效，见下）
dyndbg="file drivers/pci/* +p"

# 2. 运行时（控制文件 Documentation/admin-guide/dynamic-debug-howto.rst:11）
echo 'file drivers/pci/setup-bus.c +p' > /proc/dynamic_debug/control
echo 'file drivers/pci/probe.c +pfl'    > /proc/dynamic_debug/control   # 带函数名+行号

# 3. 看当前哪些点开着
grep 'drivers/pci' /proc/dynamic_debug/control | grep '=p'
```

⚠️ `pci.dyndbg=` 这种"模块名.dyndbg"形式**只对可加载模块有效**。PCI 核心是内建的（`CONFIG_PCI` 是 bool 不是 tristate），必须用 `dyndbg="file ... +p"` 的全局形式，或者 `CONFIG_DYNAMIC_DEBUG_CORE` 配合 `-DDEBUG`。这一条踩过的人很多。

`drivers/pci/setup-bus.c` 的 dbg 输出是查资源分配失败的关键 —— 它会打出每一轮 `pbus_size_mem` 的候选大小和最终决定。

### §9.3 启动早期的配置空间快照

```c
/* drivers/pci/pci.c:6927-6928 */
} else if (!strcmp(str, "earlydump")) {
        pci_early_dump = true;
```

`pci=earlydump` 在内核动任何东西**之前**把配置空间全 dump 到 dmesg（`Documentation/admin-guide/kernel-parameters.txt:4184-4185`）。这是唯一能回答"是固件留下的状态就不对，还是内核改坏的"的办法。输出很长，配 `log_buf_len=4M` 用。

---

## §10 启动参数

`pci=` 由 `pci_setup()` `drivers/pci/pci.c:6913` 解析，注册为 `early_param("pci", pci_setup)` `drivers/pci/pci.c:6979`。文档在 `Documentation/admin-guide/kernel-parameters.txt:4160`。

### §10.1 arm64 上真正有用的那几个

| 参数 | 解析行 | 效果 |
|---|---|---|
| `pci=nomsi` | `drivers/pci/pci.c:6920-6921` | 全局禁 MSI，退回 INTx。**验证"是不是 MSI 路由问题"的一步到位手段** |
| `pci=noaer` | `drivers/pci/pci.c:6925-6926` | 关 AER 服务 |
| `pci=earlydump` | `drivers/pci/pci.c:6927-6928` | 见 §9.3 |
| `pci=realloc` / `realloc=off` | `drivers/pci/pci.c:6929-6932` | 强制/禁止重新分配 BAR。资源不够时先试这个 |
| `pci=nodomains` | `drivers/pci/pci.c:6933-6934` | 关多 domain |
| `pci=noari` | `drivers/pci/pci.c:6935-6936` | 关 ARI |
| `pci=noats` | `drivers/pci/pci.c:6922-6924` | 关 ATS，会打一行 `PCIe: ATS is disabled` |
| `pci=resource_alignment=` | `drivers/pci/pci.c:6941-6942` | 强制 BAR 按指定大小对齐，vfio 直通要求 BAR 页对齐时用 |
| `pci=hpiosize=` `hpmmiosize=` `hpmmioprefsize=` `hpbussize=` | `drivers/pci/pci.c:6945-6958` | 给热插拔桥预留多少窗口 / 多少 bus 号 |
| `pci=pcie_bus_safe` / `_perf` / `_peer2peer` / `_tune_off` | `drivers/pci/pci.c:6959-6966` | MPS/MRRS 策略 |
| `pci=pcie_scan_all` | `drivers/pci/pci.c:6967-6968` | 扫 PCIe 桥下的全部 devfn，不只 dev 0 |
| `pci=disable_acs_redir=` | `drivers/pci/pci.c:6969-6970` | 关 ACS 重定向，⚠️ 会破坏 IOMMU 组隔离（`Documentation/admin-guide/kernel-parameters.txt:4375-4383`） |

不认识的选项走 `pr_err("PCI: Unknown option ...")` `drivers/pci/pci.c:6972` —— 打错了会在 dmesg 里看到，不是静默忽略。

⚠️ `pci=off`、`pci=bios`、`pci=nobios`、`pci=conf1`、`pci=conf2`（`Documentation/admin-guide/kernel-parameters.txt:4186-4200`）全是 x86 专属，arm64 上进不到 `pci_setup` 就被 `pcibios_setup()` 吃掉或直接无效。

### §10.2 设备选择语法

`pci=resource_alignment=` 和 `pci=disable_acs_redir=` 接的是设备列表，两种格式（`Documentation/admin-guide/kernel-parameters.txt:4166-4167`）：

```
[<domain>:]<bus>:<dev>.<func>[/<dev>.<func>]*      ← 路径形式
pci:<vendor>:<device>[:<subvendor>:<subdevice>]    ← ID 形式
```

解析函数是 `pci_dev_str_match()` `drivers/pci/pci.c:371`，路径形式交给 `pci_dev_str_match_path()` `drivers/pci/pci.c:277`。

⚠️ 文档 `Documentation/admin-guide/kernel-parameters.txt:4169-4179` 明确警告：**BDF 形式会随硬件插拔、固件升级、其他内核参数而变**。要稳就用 `pci:vendor:device` 形式，或者带路径后缀的形式（`/<dev>.<func>` 一级级写下来，对重新编号免疫）。

`resource_alignment` 还有一个 sysfs 对应面：show 在 `drivers/pci/pci.c:6761-6762`，store 在 `drivers/pci/pci.c:6785-6790`，由 `late_initcall(pci_resource_alignment_sysfs_init)` `drivers/pci/pci.c:6806` 建起来。也就是**这个参数可以运行时改**（改完要 rescan 才生效）。生效逻辑在 `pci_dev_str_match` 的调用点 `drivers/pci/pci.c:6612`。

### §10.3 PCIe 服务相关的独立参数

| 参数 | `__setup` 位置 | 文档 |
|---|---|---|
| `pcie_ports=native|dpc-native|compat` | `drivers/pci/pcie/portdrv_pci.c:49` | `Documentation/admin-guide/kernel-parameters.txt:4395-4403` |
| `pcie_aspm=off|force` | `drivers/pci/pcie/aspm.c:1400` | `Documentation/admin-guide/kernel-parameters.txt:4389-4393` |
| `pcie_pme=nomsi` | `drivers/pci/pcie/pme.c:39` | `Documentation/admin-guide/kernel-parameters.txt:4409` |
| `pcie_port_pm=off|force` | `drivers/pci/pci.c:176` | `Documentation/admin-guide/kernel-parameters.txt:4405-4407` |

ASPM 还能运行时改策略：`module_param_call(policy, ...)` `drivers/pci/pcie/aspm.c:1222`，也就是 `/sys/module/pcie_aspm/parameters/policy`。

⚠️ `pcie_aspm=off` 是 arm64 SoC 上"链路随机掉线 / 设备偶发消失"的**第一个该试的参数**。06 §4.4 讲的 `supports-clkreq` 缺失导致 L1SS 挂死，症状一样，但那个要改 dts，这个改 cmdline 就能验证。

---

## §11 症状 → 落点

| 症状 | 先看 | 再看 | 常见根因 |
|---|---|---|---|
| `lspci` 里根本没这个设备 | `dmesg | grep -i 'pci\|PCIe'` | `current_link_speed`（上游桥的） | 链路没训练起来；dts 里 phy/clk/reset 没配对（06 §7） |
| 设备在但 BAR 全 0 | `resource` | `dmesg | grep 'no space for\|BAR'` | `ranges` 窗口太小（06 §3.3）；试 `pci=realloc` |
| 同一设备出现多次 | 桥的 `secondary_bus_number` | `pci_dw_valid_device()` `drivers/pci/controller/pci-host-generic.c:26` | 主机桥没过滤非 0 devfn（06 §2.3） |
| 驱动没自动加载 | `modalias` | `modinfo -F alias <mod>` | ID 表没覆盖；`driver_override` 被设了 |
| 中断一个都不来 | `ls msi_irqs/` | `/proc/interrupts` | 向量分了但 handler 没注册；`msi-map` 配错（06 §5） |
| 中断偶发丢 | `/proc/interrupts` 的计数 | `aer_dev_correctable` | 链路 correctable 错误在重传，先修链路 |
| `irq NN: nobody cared` | 设备的 `irq` | `pci=nomsi` 试一把 | INTx 共享 + handler 没返回 `IRQ_HANDLED`（05 §9.1） |
| MSI 疑似路由错 | `pci=nomsi` 能否恢复 | dts 的 `msi-map` | ITS 的 DeviceID 映射不对 |
| `setpci` 报 `-EPERM` | `dmesg | grep -i lockdown` | — | kernel lockdown（§3.1） |
| 普通用户 `lspci -vv` 无 Capabilities | — | — | 设计如此，`drivers/pci/pci-sysfs.c:721`（§3.1） |
| `cat rom` 返回 0 字节 | `cat enable` | — | 忘了 `echo 1 > rom`，或设备未 enable（§3.3） |
| vfio 直通后设备不响应 | `reset_method` | 试 `echo bus > reset_method` | FLR 实现有问题（§4.3） |
| 链路随机掉线 | `pcie_aspm=off` 试一把 | dts 的 `supports-clkreq` | L1SS（§10.3） |
| 速率降级 | 端点和每一级桥的 `current_link_*` | dts 的 `max-link-speed` / `num-lanes` | 逐级看，别只看端点（§2.2） |
| 复位后设备状态乱 | — | 05 §8.3 的 save/restore 顺序 | 没在复位前 `pci_save_state()` |
| `aer_*` 文件不存在 | `pci_pcie_type` | `drivers/pci/pcie/aer.c:605` | `aer_stats` 没分配（§6.2） |
| 资源分配日志看不懂 | 打开 `drivers/pci/setup-bus.c` 的 dyndbg | — | §9.2 |

### §11.1 三条必看的 dmesg 行

```sh
# 1. 根总线拿到了哪些窗口 —— 和 dts 的 ranges 对照（06 §3）
dmesg | grep 'root bus resource'

# 2. 有没有 BAR 没分配上
dmesg | grep -E 'no space for|BAR [0-9]+: (failed|assigned|reserving)'

# 3. bus-range 有没有被 ECAM 大小截断（06 §2.5）
dmesg | grep 'can only accommodate'
```

第一条里的 `(bus address ...)` 括号内容就是 §2.4 说的"总线地址 vs CPU 地址"那个差值，rk3568 上是 12 GB。

---

## §12 一页速查

| 想知道什么 | 命令 |
|---|---|
| 全部设备一览 | `lspci -tvnn` |
| 某设备的完整配置空间（需 root） | `hexdump -C /sys/bus/pci/devices/0000:01:00.0/config` |
| 链路当前速率/宽度 | `cat .../current_link_speed .../current_link_width` |
| 实际拿到几个中断向量 | `ls .../msi_irqs/` |
| BAR 的 CPU 物理地址 | `cat .../resource` |
| 驱动名 | `basename $(readlink .../driver)` |
| 摘掉再插回（不动模块） | `echo 1 > .../remove; echo 1 > /sys/bus/pci/rescan` |
| 只复位这一个 function | `echo 1 > .../reset` |
| 换一种复位方法 | `echo bus > .../reset_method` |
| 抢给 vfio | `echo vfio-pci > .../driver_override` |
| AER 计数 | `cat .../aer_dev_correctable` |
| 打开资源分配日志 | `echo 'file drivers/pci/setup-bus.c +p' > /proc/dynamic_debug/control` |
| 排除 MSI 因素 | 加 `pci=nomsi` 重启 |
| 排除 ASPM 因素 | 加 `pcie_aspm=off` 重启 |
| 看固件留下的原始状态 | 加 `pci=earlydump log_buf_len=4M` 重启 |

### §12.1 三个容易搞反

| 直觉 | 实际 |
|---|---|
| `enable` 是 0/1 开关 | 是引用计数，`cat` 出来可能是 4（§4.1） |
| `resource` 里是 PCI 总线地址 | 是 CPU 物理地址，两者在 arm64 上差一个大偏移（§2.4） |
| 文件不存在 = 内核太老 | 更可能是 `is_visible` 隐藏了，或 `#ifdef` 编掉了（§5） |

### §12.2 下一步读什么

| 想深入 | 去 |
|---|---|
| 这些属性背后的数据结构 | 02 篇 |
| sysfs 文件是在探测流程哪一步建起来的 | 03 篇 |
| AER/复位/PM 在驱动侧怎么配合 | 05 §8 §10 |
| dts 属性和这些观测值的对应 | 06 篇 |
| 为什么 PCI 是总线型而不是 provider 型 | 08 篇 |
| 本篇每条结论的核对记录 | 99 篇 |

