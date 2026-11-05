---
title: 使用指南
---

# pci 05：怎么写一个 PCI 设备驱动

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

本篇按 `Documentation/PCI/pci.rst`（578 行）的步骤清单组织，
每一步补上 6.1 里的实际接口位置和 arm64 的差异。
写主机桥（控制器）驱动不看本篇，看 06 篇。

---

## §1 骨架：一个能编译的最小驱动

```c
#include <linux/module.h>
#include <linux/pci.h>

static const struct pci_device_id my_ids[] = {
        { PCI_DEVICE(0x1234, 0x5678) },
        { 0, }                                  /* ⚠️ 必须的全零结束项 */
};
MODULE_DEVICE_TABLE(pci, my_ids);               /* ⚠️ 少了这行不会自动加载 */

static int my_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
        struct my_priv *priv;
        int ret;

        ret = pcim_enable_device(pdev);          /* ⚠️ devm 版，失败自动回滚 */
        if (ret)
                return ret;

        ret = pcim_iomap_regions(pdev, BIT(0), pci_name(pdev));  /* 只要 BAR0 */
        if (ret)
                return ret;

        priv = devm_kzalloc(&pdev->dev, sizeof(*priv), GFP_KERNEL);
        if (!priv)
                return -ENOMEM;
        priv->regs = pcim_iomap_table(pdev)[0];

        ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
        if (ret)
                return ret;

        pci_set_master(pdev);                    /* ⚠️ 要 DMA 才调 */

        ret = pci_alloc_irq_vectors(pdev, 1, 1, PCI_IRQ_ALL_TYPES);
        if (ret < 0)
                return ret;

        ret = devm_request_irq(&pdev->dev, pci_irq_vector(pdev, 0),
                               my_isr, 0, pci_name(pdev), priv);
        if (ret)
                goto err_free_irq_vectors;

        pci_set_drvdata(pdev, priv);
        return 0;

err_free_irq_vectors:
        pci_free_irq_vectors(pdev);
        return ret;
}

static void my_remove(struct pci_dev *pdev)
{
        struct my_priv *priv = pci_get_drvdata(pdev);

        /* ⚠️⚠️ 先停硬件，再放中断，最后才让 devm 回收 —— 见 §9 */
        my_quiesce_hw(priv);
        pci_free_irq_vectors(pdev);
}

static struct pci_driver my_driver = {
        .name           = "mydrv",
        .id_table       = my_ids,
        .probe          = my_probe,
        .remove         = my_remove,
};
module_pci_driver(my_driver);                    /* ⚠️ 全树 423 个文件这么写 */
MODULE_LICENSE("GPL");
```

⚠️⚠️ **这个骨架里没有 `pci_request_regions()` / `pci_release_regions()` /
`pci_disable_device()` / `pci_iounmap()`** ——
因为用的是 `pcim_*` 系列，这些都由 devm 自动做。
**混用 `pci_enable_device()` 和 `pcim_iomap_regions()` 是常见错误**，见 §5.3。

---

## §2 注册与 ID 表

### §2.1 三个接口

| 接口 | 声明 | 定义 | 用法 |
|---|---|---|---|
| `pci_register_driver()` | `include/linux/pci.h:1542`（宏） | `__pci_register_driver` 声明在 `:1538` | ⚠️ 宏，自动填 `THIS_MODULE` 和 `KBUILD_MODNAME`（`:1543`） |
| `pci_unregister_driver()` | `pci.h:1545` | — | 卸载 |
| `module_pci_driver()` | `pci.h:1555`（宏） | — | ⚠️⚠️ **首选**，一行代替 init/exit（`:1556`） |

⚠️ 使用面：`module_pci_driver` **423 个文件** · `pci_register_driver` **362 个文件**。
⚠️⚠️ 两者相加超过实际驱动数，因为有些文件同时出现两者（条件编译）。
**新驱动一律用 `module_pci_driver()`。**

### §2.2 ID 表的规矩

`pci.rst:84-85` 的原话：数组必须以全零项结束，且 **建议 `static const`**。

七个构造宏（02 §6 有完整表），实际只需记三个：

| 宏 | 用途 | 使用面 |
|---|---|---|
| `PCI_DEVICE(vend, dev)` | ⚠️ 绝大多数情况 | 326 个文件 |
| `PCI_VDEVICE(vendor, dev)` | ⚠️⚠️ 厂商用符号名，且**后面还要补 `, 0`** | 223 个文件 |
| `PCI_DEVICE_CLASS(class, mask)` | 按类匹配（如所有 NVMe） | 15 个文件 |

```c
/* ⚠️⚠️ PCI_VDEVICE 的陷阱：它没有把 driver_data 位置留出来 */
{ PCI_VDEVICE(INTEL, 0x1234), 0 },        /* 对：末尾要补 driver_data */
{ PCI_DEVICE(0x8086, 0x1234) },           /* 对：PCI_DEVICE 不需要 */
```

⚠️ `pci.rst:108-110` 有一条容易忽略的规则：
**如果表里所有条目的 `driver_data` 都非零，那么运行时通过
`/sys/bus/pci/drivers/<drv>/new_id` 添加的新 ID 也必须给一个已存在的 `driver_data` 值。**

### §2.3 `MODULE_DEVICE_TABLE` 是自动加载的唯一依据

全树 **755 个文件** 有 `MODULE_DEVICE_TABLE(pci, ...)`。
⚠️⚠️ 它在编译期被 `scripts/mod/file2alias.c` 处理成 modalias 字符串，
和 `pci_uevent()`（`drivers/pci/pci-driver.c:1568`）生成的 `MODALIAS=` 对上，
udev 才会 `modprobe`。**漏了这一行，驱动编译通过、内置时能工作、做成模块就不自动加载。**

### §2.4 不用 `pci_register_driver` 的场合

`pci.rst:145-149` 说得很硬：**要有非常好的理由。** 唯一被认可的理由是
"一个 PCI 设备实现了多个不同的硬件服务，要由多个驱动分别控制"。

手工查找三种（`pci.rst:153-169`）：

```c
pci_get_device(vendor, device, from);            /* 按 vendor/device */
pci_get_class(class, from);                      /* 按 class */
pci_get_subsys(vendor, device, ss_vendor, ss_device, from);
```

⚠️⚠️ `pci.rst:171-173` 的警告要记牢：**这三个函数会加引用计数，
必须自己 `pci_dev_put()`。** 对比 `pci_find_*` 系列不加计数（01 §8）。

⚠️ `PCI_ANY_ID` 可以做通配（`pci.rst:167`，值是 `(~0)`，
定义在 `include/linux/mod_devicetable.h:17`）。

---

## §3 probe 的八步（`pci.rst:182-189` 原文顺序）

```
① Enable the device                                        §4
② Request MMIO/IOP resources                               §5
③ Set the DMA mask size（coherent 和 streaming 都要）        §6
④ Allocate and initialize shared control data              §6.3
⑤ Access device configuration space（如果需要）
⑥ Register IRQ handler（request_irq()）                     §7
⑦ Initialize non-PCI parts（LAN/SCSI/… 芯片本身）
⑧ Enable DMA/processing engines
```

⚠️⚠️ **顺序不是建议，是要求**。三处硬约束：

| 约束 | 出处 | 原因 |
|---|---|---|
| ③ 必须在 ④ 之前 | `pci.rst:294-297` | 掩码决定 `dma_alloc_coherent()` 从哪个区分配 |
| ⑥ 之前设备必须已静默 | `pci.rst:322-324` | ⚠️ 否则注册的瞬间就进中断 |
| ⑧ 必须最后 | `pci.rst:189` | 引擎一开就可能 DMA 到还没准备好的结构 |

⚠️ `pci.rst:191-194` 顺带说明：**配置空间任何时候都能访问**（BIST 期间例外）。
所以 ⑤ 的位置其实是自由的。

---

## §4 第一步：使能设备

### §4.1 两个版本，选 devm 的

| 接口 | 声明 | 定义 | 使用面 |
|---|---|---|---|
| `pci_enable_device()` | `pci.h:1304` | `drivers/pci/pci.c:2093` | 478 个文件 |
| `pcim_enable_device()` | `pci.h:1308` | `drivers/pci/pci.c:2161` | ⚠️ 247 个文件 |
| `pci_disable_device()` | `pci.h:1330` | `drivers/pci/pci.c:2281` | — |
| `pci_enable_device_mem()` | `pci.h:1306` | `drivers/pci/pci.c:2076` | 只要 MEM BAR |
| `pci_enable_device_io()` | `pci.h:1305` | `drivers/pci/pci.c:2062` | ⚠️ arm64 上几乎不用 |

`pci_enable_device()` 做三件事（`pci.rst:202-204`）：
唤醒 D3 里的设备 · 分配 I/O 与 MEM 区间（如果固件没分）· 分配 IRQ（如果固件没分）。

⚠️ `pci.rst:206-207` 单独强调：**它会失败，必须检查返回值。**

### §4.2 官方文档自己承认的一个 OS BUG

```
/* Documentation/PCI/pci.rst:209-215 —— 原文 */
.. warning::
   OS BUG: we don't check resource allocations before enabling those
   resources. The sequence would make more sense if we called
   pci_request_resources() before calling pci_enable_device().
   Currently, the device drivers can't detect the bug when two
   devices have been allocated the same range. This is not a common
   problem and unlikely to get fixed soon.
```

⚠️⚠️ **这是文档里唯一自称 "OS BUG" 的地方**，而且从 2.6.19 至今未改
（`pci.rst:217-218` 附了 2006 年的邮件链接）。
后果：**两个设备被分到同一段地址时，驱动无法察觉。**
现场表现是两个驱动都能 probe 成功，但读写互相串扰。

### §4.3 `pci_set_master()`：只有要 DMA 才调

它做两件事（`pci.rst:221-223`）：置 `PCI_COMMAND` 的 bus master 位；
⚠️ **顺手修正被固件写坏的 latency timer。**

| 接口 | 声明 | 定义 | 使用面 |
|---|---|---|---|
| `pci_set_master()` | `pci.h:1333` | `drivers/pci/pci.c:4494` | ⚠️⚠️ **519 个文件，全树第一** |
| `pci_clear_master()` | `pci.h:1334` | `drivers/pci/pci.c:4505` | — |

⚠️ MWI 三个（`pci.rst:226-233`）：`pci_set_mwi()` · `pci_try_set_mwi()` · `pci_clear_mwi()`。
⚠️⚠️ **arm64 上不要用**：MWI 是 conventional PCI 的事务类型，
PCIe 没有这个概念；`pci_set_mwi()` 在 PCIe 设备上返回错误或空操作。

---

## §5 第二步：申请资源并映射

### §5.1 不要从配置空间读地址

`pci.rst:238-241` 的原话：MMIO 和 I/O 端口地址 **不应该** 直接从配置空间读，
要用 `pci_dev` 里的值 —— 因为"PCI 总线地址"可能已被体系结构代码重映射成"主机物理地址"。

⚠️⚠️ **arm64 上这条尤其重要**：主机桥的 DT `ranges` 定义了总线地址到 CPU 地址的偏移，
`pci_dev->resource[]` 里存的是 **CPU 侧地址**，配置空间 BAR 里是 **总线侧地址**。
两者在有偏移的平台上不相等（01 §10.4）。

三个访问宏：

| 宏 | 声明 | 用途 |
|---|---|---|
| `pci_resource_start(dev, i)` | `pci.h:2025` | ⚠️ 527 个文件用；底层是 `pci_resource_n()`（`:2024`） |
| `pci_resource_end(dev, i)` | `pci.h:2026` | — |
| `pci_resource_len(dev, i)` | `pci.h:2028` | ⚠️ 长度为 0 表示这个 BAR 不存在 |
| `pci_resource_flags(dev, i)` | `pci.h:2027` | 判 `IORESOURCE_MEM` / `_IO` |

### §5.2 申请与释放

| 接口 | 声明 | 定义 |
|---|---|---|
| `pci_request_region()` | `pci.h:1462` | `drivers/pci/pci.c:4065` |
| `pci_release_region()` | `pci.h:1463` | `drivers/pci/pci.c:3982` |
| `pci_request_regions()` | `pci.h:1459` | `drivers/pci/pci.c:4159` |
| `pci_release_regions()` | `pci.h:1461` | `drivers/pci/pci.c:4140` |
| `pci_request_selected_regions()` | `pci.h:1464` | `drivers/pci/pci.c:4115` |
| `pci_release_selected_regions()` | `pci.h:1466` | `drivers/pci/pci.c:4079` |

⚠️⚠️ `pci.rst:248-249` 的顺序要求：
**`pci_release_region()` 必须在 `pci_disable_device()` 之后调。**
这与直觉相反（一般是"反序释放"），原因是 disable 过程中还可能访问这些区间。

⚠️ `pci.rst:257-260`：不由标准 BAR 描述的地址范围用通用的
`request_mem_region()` / `request_region()`。

### §5.3 devm 版本与混用陷阱

| 接口 | 声明 | 说明 |
|---|---|---|
| `pcim_iomap_regions()` | `pci.h:2200` | ⚠️ 112 个文件用；一次搞定 request + ioremap |
| `pcim_iomap_table()` | `pci.h:2199` | 取映射后的指针数组 |
| `pcim_iomap()` | `pci.h:2197` | 单个 BAR |
| `pcim_iomap_regions_request_all()` | `pci.h:2201` | ⚠️ 申请全部 BAR，只映射指定的 |
| `pci_iomap()` | ⚠️ `include/asm-generic/pci_iomap.h:13` | 128 个文件；⚠️ 非 devm，**不在 `include/linux/pci.h` 里** |

⚠️⚠️ **混用陷阱**：`pcim_*` 系列依赖一个"devm 已启用"的标记，
这个标记由 `pcim_enable_device()` 设置。
**用 `pci_enable_device()` 之后再调 `pcim_iomap_regions()`，
资源不会被自动释放** —— 这是一类真实存在的泄漏。
**规则：要么全用 `pcim_*`，要么全用 `pci_*`。**

---

## §6 第三步：DMA 掩码

### §6.1 必须显式设置

`pci.rst:280-281` 的原话：**所有 PCI-X 和 PCIe 兼容设备的驱动都必须调用
`dma_set_mask()`，因为它们都是 64 位 DMA 设备。**

```c
ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
if (ret) {
        /* ⚠️ 退回 32 位，而不是直接失败 */
        ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
        if (ret)
                return ret;
}
```

⚠️⚠️ **注意用的是 `&pdev->dev`，不是 `pdev`。** DMA API 全部走 `struct device`。
`pci_set_dma_mask()` 一类的 PCI 专用包装在 6.1 里已经不存在了。

### §6.2 一处文档与代码的矛盾

⚠️⚠️ `include/linux/pci.h` 里 `pci_dev.dma_mask` 字段的注释说
"Mask of the bits of bus address this device implements"，
把它描述成设备属性；而 `pci.rst:273-278` 把 `dma_set_mask()` 描述成
"driver 向系统注册能力"的动作。
**两种说法在"谁是权威"上不一致**：实际上 `dma_mask` 由驱动写、DMA 层读，
设备硬件本身并不参与。记入 99 §5。

### §6.3 共享控制数据

`pci.rst:294-297`：掩码设完才能分配 coherent 内存。
⚠️ 注意 `pci.rst:48` 和 `:185` 都写着 `pci_allocate_coherent()`
—— **这个函数名在 6.1 里不存在**，正确的是 `dma_alloc_coherent()`。
文档陈旧，记入 99 §5。

---

## §7 第六步：中断

### §7.1 只用一套接口

6.1 里申请中断向量只有一条推荐路径，就是 `pci_alloc_irq_vectors()`。
它把 MSI-X / MSI / INTx 三种机制藏在一个调用后面。

| 接口 | 位置 | 说明 |
|---|---|---|
| `pci_alloc_irq_vectors()` | ⚠️⚠️ **inline**，`include/linux/pci.h:1985-1990` | 不是导出符号，是对下一行的包装 |
| `pci_alloc_irq_vectors_affinity()` | 声明 `pci.h:1632`，定义 `drivers/pci/msi/msi.c:1014`，`EXPORT_SYMBOL` `:1059` | 真正的实现；多一个 `affd` 参数 |
| `pci_irq_vector()` | 声明 `pci.h:1637`，定义 `msi/msi.c:1086`，导出 `:1096` | ⚠️ 把"第 n 个向量"翻成 Linux irq 号 |
| `pci_free_irq_vectors()` | 声明 `pci.h:1636`，定义 `msi/msi.c:1067`，导出 `:1072` | 释放 |
| `pci_irq_get_affinity()` | 声明 `pci.h:1638`，定义 `msi/msi.c:1110`，导出 `:1134` | 查某向量的 CPU 亲和掩码 |
| `pci_msix_vec_count()` | 声明 `pci.h:1617`，定义 `msi/msi.c:792`，导出 `:802` | 硬件支持的 MSI-X 上限 |
| `pci_msi_enabled()` | 声明 `pci.h:1620`，定义 `msi/msi.c:1140`，导出 `:1157` | 全局 MSI 是否可用 |

⚠️⚠️ 第一行那个 inline 是个真陷阱：**`pci_alloc_irq_vectors` 在
`Module.symvers` 里查不到**，因为它根本不是导出符号。
排查"符号找不到"类问题时不要在这里绕。记入 99 §4。

### §7.2 flags 怎么填

```c
/* include/linux/pci.h:1068-1071, :1602, :1604-1605 */
#define PCI_IRQ_LEGACY          (1 << 0)   /* :1068 */
#define PCI_IRQ_MSI             (1 << 1)   /* :1069 */
#define PCI_IRQ_MSIX            (1 << 2)   /* :1070 */
#define PCI_IRQ_AFFINITY        (1 << 3)   /* :1071 自动分配亲和性 */
#define PCI_IRQ_VIRTUAL         (1 << 4)   /* :1602 只给 VF 用 */
#define PCI_IRQ_ALL_TYPES \
        (PCI_IRQ_LEGACY | PCI_IRQ_MSI | PCI_IRQ_MSIX)   /* :1604-1605 */
```

⚠️⚠️ `pci.rst:335-338` 的原话：很多体系结构、芯片组或 BIOS **不支持** MSI/MSI-X，
只带 `PCI_IRQ_MSI | PCI_IRQ_MSIX` 的调用会失败，
**所以尽量总是把 `PCI_IRQ_LEGACY` 一起带上。**
`PCI_IRQ_ALL_TYPES` 就是这三个的并集，绝大多数驱动直接用它。

⚠️ 回退到 INTx 的条件写在 inline 里，`pci.h:1661`：
`(flags & PCI_IRQ_LEGACY) && min_vecs == 1 && dev->irq` ——
**只要求 1 个向量时才可能回退**。要 4 个向量又指望回退 INTx 是不可能的。

### §7.3 注册处理函数

`pci.rst:313-315`：**所有 INTx 线的处理函数都应该用 `IRQF_SHARED` 注册，
并用 devid 把 IRQ 映到设备** —— 因为所有 PCI INTx 线都可以共享。

⚠️⚠️ `pci.rst:322-324`：`request_irq()` 顺手就把中断打开了。
**注册之前必须让设备静默、且没有 pending 中断。**
这条是 03 §2 那种"顺序即约束"的典型：反了就是启动瞬间进中断。

`pci.rst:340-343` 给出选处理函数的写法：

```c
if (pdev->msix_enabled || pdev->msi_enabled)
        handler = my_msi_isr;           /* 独占向量，不用判是不是自己 */
else
        handler = my_intx_isr;          /* 共享，必须先读状态寄存器确认 */
```

⚠️ 这两个标志位在 `struct pci_dev` 里（02 §2 的位域段），
**必须在 `pci_alloc_irq_vectors()` 返回之后才有意义。**

### §7.4 为什么值得优先用 MSI

`pci.rst:345-355` 给了两个理由，第二个才是关键：

| 理由 | 原文出处 | 实际影响 |
|---|---|---|
| MSI 向量按定义是独占的 | `pci.rst:347-349` | ⚠️ 处理函数不用再判"是不是我的设备"，也就没有共享中断风暴 |
| MSI 避免 DMA/IRQ 竞态 | `pci.rst:351-355` | ⚠️⚠️ **MSI 送达时，DMA 到主机内存的数据已保证对 CPU 可见** |

第二条的实用后果直接写在原文里：**驱动可以省掉那个"读一下 MMIO 把 DMA 流冲干净"的
假读**。很多老驱动的 `readl(dummy)` 就是为了这个。

⚠️ arm64 上还有一层：MSI 走 ITS，`PCI_IRQ_MSI`（非 X）在多数 arm64 平台上
其实也是通过 ITS 实现的，和 INTx 走 GIC SPI 是两条完全不同的路。
控制器侧怎么接见 06 篇。

---

## §8 电源管理

### §8.1 现代写法：不碰 PCI 专用接口

`struct pci_driver` 里有 `.suspend`（`include/linux/pci.h:926`）
和 `.resume`（`:927`）两个字段，但 ⚠️⚠️ **新驱动不要用它们** ——
用 `.driver.pm`（`:935` 的 `struct device_driver driver` 里的 `pm`），
配 `SIMPLE_DEV_PM_OPS()` / `DEFINE_SIMPLE_DEV_PM_OPS()`。
`pci_bus_type` 的 `.pm` 回调（03 §6）会把两条路都接过去。

### §8.2 手工用得上的几个

| 接口 | 声明 | 定义 | 用途 |
|---|---|---|---|
| `pci_save_state()` | `pci.h:1402` | `drivers/pci/pci.c:1693` | ⚠️ 复位前保存配置空间 |
| `pci_restore_state()` | `pci.h:1403` | `drivers/pci/pci.c:1809` | 复位后恢复 |
| `pci_set_power_state()` | `pci.h:1410` | `drivers/pci/pci.c:1502` | 进/出 D 状态 |
| `pci_choose_state()` | `pci.h:1412` | `drivers/pci/pci.c:2934` | 由系统睡眠状态推 D 状态 |
| `pci_pme_capable()` | `pci.h:1413` | `drivers/pci/pci.c:2418` | 该 D 状态能不能发 PME |
| `pci_enable_wake()` | `pci.h:1415` | `drivers/pci/pci.c:2647` | 使能唤醒 |
| `pci_wake_from_d3()` | `pci.h:1416` | — | ⚠️ 上一个的常用包装 |
| `pci_prepare_to_sleep()` | `pci.h:1417` | `drivers/pci/pci.c:2747` | save + choose + wake + set 一条龙 |
| `pci_back_from_sleep()` | `pci.h:1418` | `drivers/pci/pci.c:2774` | 反向 |

⚠️ 6.1.158 里多了一个 `pci_set_power_state_locked()`（`pci.h:1411`，
`pci.c:1508`）——这是后期回合进来的，**上游 6.1.0 没有**，
写要兼容多版本的代码时注意。记入 99 §3。

### §8.3 复位与探活

| 接口 | 声明 | 定义 | 说明 |
|---|---|---|---|
| `pci_reset_function()` | `pci.h:1362` | `drivers/pci/pci.c:5497` | ⚠️ 自己拿锁 |
| `pci_reset_function_locked()` | `pci.h:1363` | `drivers/pci/pci.c:5533` | 调用者已持锁 |
| `pci_try_reset_function()` | `pci.h:1364` | `drivers/pci/pci.c:5556` | 拿不到锁就返回 `-EAGAIN` |
| `pci_reset_bus()` | `pci.h:1367` | `drivers/pci/pci.c:5967` | 整条总线 |
| `pci_wait_for_pending_transaction()` | `pci.h:1347` | `drivers/pci/pci.c:4767` | ⚠️ 复位前等事务排空 |
| `pci_device_is_present()` | `pci.h:1385` | `drivers/pci/pci.c:6514` | ⚠️⚠️ 读到全 1 就认为设备没了 |

⚠️⚠️ `pci_device_is_present()` 是排查"设备突然消失"的第一现场手段：
它读厂商 ID，全 1 说明链路断了或设备被拔了。**返回 false 时不要再去
读写寄存器**，读回来的全是 `~0`。

⚠️ 顺序：`pci_save_state()` → `pci_wait_for_pending_transaction()` →
复位 → `pci_restore_state()`。**少了 save/restore，复位后 BAR 和
Command 寄存器全清零，设备变砖。**

---

## §9 卸载：七步

`pci.rst:367-373` 的原文清单：

```
① Disable the device from generating IRQs
② Release the IRQ (free_irq())
③ Stop all DMA activity
④ Release DMA buffers（streaming 和 coherent 都要）
⑤ Unregister from other subsystems（scsi / netdev / …）
⑥ Disable device from responding to MMIO/IO Port addresses
⑦ Release MMIO/IO Port resource(s)
```

⚠️⚠️ **这个顺序不是 probe 的简单倒序**，四处需要单独理解：

### §9.1 ① 为什么必须在 ② 之前：screaming interrupt

`pci.rst:378-389` 用一整段解释了这个失败模式，值得记住机制：

设备还在拉中断线，处理函数已经被摘掉。系统看到线上有中断，
以为是共享同一根线的其它设备，逐个问过去，**没人认领**。
于是系统一直卡着，**直到跑满 100000 次迭代**才把这根 IRQ 屏蔽掉。
一屏蔽，**共享这根线的其它设备全部停止工作。**

⚠️ `pci.rst:391-393`：这是又一个优先用 MSI 的理由 ——
MSI/MSI-X 按定义独占，**不存在 screaming interrupt 问题**。

⚠️⚠️ 现场特征：`/proc/interrupts` 里某根共享 INTx 计数暴涨然后停住，
`dmesg` 出现 `irq NN: nobody cared`。看到这个就去查最近卸载的驱动。

### §9.2 ③ 必须在 ④ 之前

`pci.rst:406-408` 的措辞很重：**在试图释放 DMA 控制数据之前停掉所有
DMA 操作，这一点极其重要。做不到会导致内存损坏、挂死，
在某些芯片组上是直接硬崩。**

`pci.rst:410-411` 补了一条容易漏的：**停 DMA 要在停中断之后 ——
否则中断处理函数可能把 DMA 引擎又启起来。**

⚠️ `pci.rst:413-414` 加了一句罕见的自我批评：
**好几个"成熟"驱动过去都没把这一步做对。**

### §9.3 ④ 的内部顺序

`pci.rst:419-423`：先清 streaming（unmap 数据缓冲、把缓冲还给上层），
**再清 coherent（放控制数据的那块）。**
理由是控制数据里还存着 streaming 缓冲的描述符。

### §9.4 ⑦ 在 ⑥ 之后

`pci.rst:439-441`：先 `io_unmap()`，再 `pci_disable_device()`，
⚠️⚠️ **`pci_disable_device()` 之后不要再访问设备寄存器。**

`pci.rst:446-447`：然后才 `pci_release_region()`。
⚠️ 漏了这一步的典型症状是 **驱动重新加载不上**（资源还被占着）。

### §9.5 用 devm 之后哪几步还得自己做

| 步骤 | devm 管吗 | 备注 |
|---|---|---|
| ① 停中断产生 | ⚠️⚠️ **不管** | 只有你知道怎么关这块硬件 |
| ② free_irq | 管（`devm_request_irq`） | ⚠️ 但 `pci_free_irq_vectors()` 要自己调 |
| ③ 停 DMA | ⚠️⚠️ **不管** | 同 ① |
| ④ 释放 DMA 缓冲 | 用 `dmam_*` 才管 | — |
| ⑤ 从其它子系统注销 | 部分（`devm_register_netdev` 等） | — |
| ⑥⑦ disable + release | 管（`pcim_*`） | — |

⚠️⚠️ 所以 §1 骨架的 `my_remove()` 里只剩两行：**停硬件 + 放向量**。
剩下的由 devm 按注册的反序自动做。**"用了 devm 就不用写 remove"是错的。**

---

## §10 错误恢复回调怎么填

### §10.1 六个回调

```c
/* include/linux/pci.h:842-859 */
struct pci_error_handlers {
        pci_ers_result_t (*error_detected)(struct pci_dev *dev,
                                          pci_channel_state_t error);  /* :844-845 */
        pci_ers_result_t (*mmio_enabled)(struct pci_dev *dev);         /* :848 */
        pci_ers_result_t (*slot_reset)(struct pci_dev *dev);           /* :851 */
        void (*reset_prepare)(struct pci_dev *dev);                    /* :854 */
        void (*reset_done)(struct pci_dev *dev);                       /* :855 */
        void (*resume)(struct pci_dev *dev);                           /* :858 */
};
```

挂在 `pci_driver.err_handler`（`pci.h:932`）。
⚠️ 使用面 **143 个文件**，约占 PCI 驱动的四成 —— 不填是常态，不是错误。
⚠️⚠️ 但一旦不填，AER 恢复流程对这个设备只能走
`PCI_ERS_RESULT_NO_AER_DRIVER`（04 §4.1），后果通常是直接断开。

### §10.2 返回值从 1 开始

```c
/* include/linux/pci.h:819-839 */
typedef unsigned int __bitwise pci_ers_result_t;                  /* :819 */
PCI_ERS_RESULT_NONE           = 1   /* :823 驱动不支持/无结果 */
PCI_ERS_RESULT_CAN_RECOVER    = 2   /* :826 不用复位就能恢复 */
PCI_ERS_RESULT_NEED_RESET     = 3   /* :829 要求复位插槽 */
PCI_ERS_RESULT_DISCONNECT     = 4   /* :832 彻底失败，不可恢复 */
PCI_ERS_RESULT_RECOVERED      = 5   /* :835 已完全恢复 */
PCI_ERS_RESULT_NO_AER_DRIVER  = 6   /* :838 驱动没注册 AER 能力 */
```

⚠️⚠️ **枚举从 1 开始，没有 0**。所以 `return 0;` 是非法值 ——
它会被 `merge_result()`（04 §4.2）当成一个未知严重级参与合并，
行为未定义。**忘了 return 具体值的回调，编译期查不出来。**

⚠️ 而且它是 `__bitwise` 类型，**必须用 sparse 才能查出裸整数赋值**。
`make C=1` 是唯一能在编译期抓到这个错的办法。

### §10.3 每个回调里能做什么

| 回调 | 此刻硬件状态 | 能做 | ⚠️ 不能做 |
|---|---|---|---|
| `error_detected` | 链路可能已断 | 记录、停软件侧队列 | ⚠️⚠️ **不要读写设备寄存器**，`error` 参数为 `pci_channel_io_perm_failure` 时读回全 1 |
| `mmio_enabled` | MMIO 可用，DMA 不可用 | 读寄存器判断细节 | ⚠️ 不能启 DMA |
| `slot_reset` | 刚复位完，配置空间已恢复 | 重新初始化设备 | ⚠️ 不能假设内部状态还在 |
| `reset_prepare` | 即将被复位（非错误路径） | 停活动 | — |
| `reset_done` | 复位完成 | 重新初始化 | — |
| `resume` | 一切正常 | 恢复队列、通知上层 | ⚠️ 返回 void，出错也没法上报 |

⚠️⚠️ `error_detected` 是唯一带 `error` 参数的，判它：
`pci_channel_io_frozen` 时不能碰硬件，`pci_channel_io_normal` 时可以。
**大量驱动忽略这个参数直接读寄存器，这是 AER 路径上二次崩溃的常见来源。**

### §10.4 完整参考

`Documentation/PCI/pci-error-recovery.rst`（426 行）是权威，
配 04 §4 的五阶段广播表看。⚠️ 一个提醒：那份文档里的
"platform" 章节大量讲 powerpc EEH，**arm64 上不适用**。

---

## §11 SR-IOV PF 驱动要填什么

### §11.1 一个回调 + 一个现成实现

```c
/* include/linux/pci.h:929-931 */
int  (*sriov_configure)(struct pci_dev *dev, int num_vfs);            /* :929 On PF */
int  (*sriov_set_msix_vec_count)(struct pci_dev *vf, int cnt);        /* :930 On PF */
u32  (*sriov_get_vf_total_msix)(struct pci_dev *pf);                  /* :931 */
```

⚠️ 使用面：`sriov_configure` **76 个文件**。

⚠️⚠️ 如果 PF 不需要在开 VF 前后做任何私有准备，
**直接填 `pci_sriov_configure_simple`**（`drivers/pci/iov.c:1234`，
`EXPORT_SYMBOL_GPL`）：

```c
static struct pci_driver my_driver = {
        /* ... */
        .sriov_configure = pci_sriov_configure_simple,
};
```

需要私有准备时才自己写，模板是"`num_vfs == 0` 就 `pci_disable_sriov()`，
否则准备好资源再 `pci_enable_sriov()`"。完整调用链见 04 §9.2。

### §11.2 VF 驱动侧

⚠️ VF 是独立的 `pci_dev`，用独立的 `pci_driver` 和独立的 ID 表 ——
**和 PF 驱动是两个模块，或同一模块里两个 `pci_driver`。**
VF 驱动要从 PF 拿私有数据时用 `pci_iov_get_pf_drvdata()`（`iov.c:77`）。

⚠️⚠️ VF 上 `PCI_IRQ_VIRTUAL`（`pci.h:1602`）才有意义：
它允许分配"没有实际后备中断"的向量。普通设备驱动不要用。

---

## §12 十条纪律

| # | 纪律 | 依据 |
|---|---|---|
| 1 | ID 表 `static const` + 全零结尾 + `MODULE_DEVICE_TABLE` | `pci.rst:84-85`；§2.2 §2.3 |
| 2 | 用 `module_pci_driver()`，不手写 init/exit | §2.1 |
| 3 | 要么全 `pcim_*`，要么全 `pci_*`，⚠️ 不混用 | §5.3 |
| 4 | probe 里 DMA 掩码必须在分配 coherent 内存之前设 | `pci.rst:294-297` |
| 5 | `pci_alloc_irq_vectors()` 的 flags 总带 `PCI_IRQ_LEGACY` | `pci.rst:335-338` |
| 6 | 注册中断前设备必须静默 | `pci.rst:322-324` |
| 7 | INTx 处理函数必须 `IRQF_SHARED` + devid | `pci.rst:313-315` |
| 8 | remove 里"停硬件 → 放中断 → 停 DMA"这三步自己做 | §9.1 §9.2 §9.5 |
| 9 | `pci_release_region()` 在 `pci_disable_device()` **之后** | `pci.rst:248-249`、`:439-447` |
| 10 | 错误恢复回调返回枚举值，⚠️ 不返回 0 | §10.2 |

### §12.1 别用清单

| 别用 | 用什么 | 出处 |
|---|---|---|
| `pci_find_device()` | `pci_get_device()` | `pci.rst:531` |
| `pci_find_subsys()` | `pci_get_subsys()` | `pci.rst:532` |
| `pci_find_slot()` | `pci_get_domain_bus_and_slot()` | `pci.rst:533` |
| `pci_get_slot()` | `pci_get_domain_bus_and_slot()` | `pci.rst:534` |
| `pci_set_dma_mask()` | `dma_set_mask_and_coherent()` | §6.1；6.1 里已删 |
| `pci_set_mwi()` 一族 | ⚠️ arm64 上什么都不用 | §4.3 |
| Fast Back to Back | ⚠️ 平台代码的事，驱动不碰 | `pci.rst:503-505` |
| 往 `pci_ids.h` 加私有 ID | 写在自己驱动里，或用裸十六进制 | `pci.rst:511-513` |
| bus/slot/function 号做标识 | `struct pci_dev *` 指针 | `pci.rst:497-501` |
| 自己 printf 设备名 | `pci_name(pci_dev)` | `pci.rst:494-495` |

⚠️⚠️ `pci.rst:526-528` 说明了这四个 `pci_find_*` 为什么被废：
**不兼容热插拔、不兼容 PCI domain、没有正常的加锁。**
新写的代码里出现这些名字，说明是从很老的驱动抄的。

⚠️ 关于 ID 提交：`pci.rst:518-519` 要求新的 vendor/device ID
提交到 `https://pci-ids.ucw.cz/`（镜像 `https://github.com/pciutils/pciids`），
**而不是往内核头文件里塞。**

### §12.2 write posting：从 I/O 端口移到 MMIO 时的必修课

`pci.rst:544-551` 讲的是一个纯硬件语义差异：
**I/O 端口空间保证写事务在 CPU 继续执行之前到达设备；
MMIO 空间允许 CPU 在事务到达设备之前就继续。**
硬件工程师管这叫 write posting。

`pci.rst:557-568` 的对照代码：

```c
/* I/O 端口版本 —— 天然正确 */
for (i = 8; --i; val >>= 1) {
        outb(val & 1, ioport_reg);      /* write bit */
        udelay(10);
}

/* MMIO 版本 —— 必须补一次读 */
for (i = 8; --i; val >>= 1) {
        writeb(val & 1, mmio_reg);      /* write bit */
        readb(safe_mmio_reg);           /* ⚠️⚠️ flush posted write */
        udelay(10);
}
```

⚠️ `pci.rst:570-571`：那个 `safe_mmio_reg` **不能有任何影响设备正确工作的副作用**。

⚠️⚠️ `pci.rst:573-578` 还有一条 arm64 上非常要紧的：
**复位设备时，用配置空间读来冲刷 `writel()`，不要用 MMIO 读。**
原因是设备可能已经不响应了，MMIO 读会撞上 master abort ——
x86 多数平台允许（"Soft Fail"，返回 `~0` 之类的垃圾），
**但很多 RISC 平台会直接崩（"Hard Fail"）。**
arm64 属于后一类，这不是理论风险。

### §12.3 配置空间访问：文档有两处错

`pci.rst:453-457` 推荐的 API 本身没问题（01 §3 有完整表），
但同一段里有两个坑：

⚠️⚠️ 第一，`pci.rst:456` 说错误码"可以用 `pcibios_strerror` 翻成文本" ——
**这个函数在 6.1 里不存在**，全树搜不到。记入 99 §5。

⚠️⚠️ 第二，`pci.rst:466-468` 说"要访问扩展能力寄存器，
就调 `pci_find_capability()`" —— **这是错的**。
扩展能力（配置空间 0x100 以上）必须用 `pci_find_ext_capability()`。
`pci_find_capability()` 只走标准能力链表。记入 99 §5。

⚠️ 第三点不算错但要知道：`pci.rst:463-464` 要求
访问标准配置头字段时用 `<linux/pci.h>` 里的符号名 ——
实际那些符号在 `include/uapi/linux/pci_regs.h`（1134 行），
`pci.h` 只是把它 include 进来。

---

## §13 一页速查

| 我要做 | 调什么 | 别忘了 |
|---|---|---|
| 注册驱动 | `module_pci_driver()` | `MODULE_DEVICE_TABLE` |
| 使能设备 | `pcim_enable_device()` | 检查返回值 |
| 拿 BAR | `pcim_iomap_regions()` + `pcim_iomap_table()` | ⚠️ 不要混 `pci_enable_device` |
| 设 DMA | `dma_set_mask_and_coherent(&pdev->dev, ...)` | ⚠️ 64 位失败要退 32 位 |
| 开 DMA | `pci_set_master()` | 只有真要 DMA 才调 |
| 要中断 | `pci_alloc_irq_vectors(..., PCI_IRQ_ALL_TYPES)` | ⚠️ 设备先静默 |
| 拿 irq 号 | `pci_irq_vector(pdev, n)` | 不是 `pdev->irq` |
| 卸载 | 停硬件 → `pci_free_irq_vectors()` | ⚠️ 其余交给 devm |
| 支持 AER | 填 `err_handler` 的 6 个回调 | ⚠️ 返回枚举，不返回 0 |
| 支持 SR-IOV | `.sriov_configure = pci_sriov_configure_simple` | 无私有准备时够用 |

三条最容易搞反的：

1. ⚠️⚠️ **`pci_release_region()` 在 `pci_disable_device()` 之后**，不是之前。
2. ⚠️⚠️ **卸载时先停中断再停 DMA**，不是先停 DMA。
3. ⚠️⚠️ **`pci_alloc_irq_vectors()` 是 inline 不是导出符号**，`Module.symvers` 里没有。

---

## 下一步读什么

| 想知道 | 去哪篇 |
|---|---|
| 本篇提到的接口的完整清单和签名 | 01 篇 |
| `pci_dev` / `pci_driver` 每个字段的含义 | 02 篇 |
| 为什么 probe 里资源已经分配好了 | 03 篇 §5 |
| AER / ASPM / SR-IOV 内部怎么跑 | 04 篇 |
| arm64 主机桥和 DT 怎么配 | 06 篇 |
| 出问题了看哪些 sysfs 文件 | 07 篇 |
| 本篇引用的文档错误的完整清单 | 99 篇 §5 |
