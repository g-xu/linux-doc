---
title: API 参考
---

# pci 01：对外接口

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇只讲**核心层导出给外部的接口**。范围口径（哪 19 个文件算核心、为什么把
`controller/` `hotplug/` `pcie/` `endpoint/` `quirks.c` 划到核心之外）见 99 §1.3。

---

## §1 先看规模：232 个导出符号

| 项 | 数字 |
|---|---|
| 核心层导出符号 | **232** 个 |
| 其中 `EXPORT_SYMBOL_GPL` | **92** 个 |
| ⚠️ 其中裸 `EXPORT_SYMBOL` | **140** 个 —— 占六成 |
| 主头文件 | `include/linux/pci.h`，**2597 行**（本套最大的单个头文件） |

⚠️⚠️ **"裸 `EXPORT_SYMBOL` 占六成"不是疏漏，是历史**：PCI 的接口面从 2.4 时代
就已成形，`_GPL` 只出现在后来新增的那批上（`pcie_*`、`pci_reset_*`、
`pci_cfg_access_*`、`of_pci_*`）。⇒ **看一个 PCI 接口是不是"新"的，
最快的判据是它导出时带不带 `_GPL`。**

**按文件分布**（每行是 `EXPORT_SYMBOL` 出现次数）：

| 文件 | 导出数 | 这一组接口是干什么的 |
|---|---|---|
| `drivers/pci/pci.c` | **103** | ⚠️ 通用设备操作：使能/master/region/PM/复位/链路 |
| `drivers/pci/access.c` | 29 | 配置空间读写 |
| `drivers/pci/probe.c` | 26 | ⚠️ 枚举 —— **主要给主机桥驱动用**（§10） |
| `drivers/pci/msi/msi.c` | 16 | 中断向量分配 |
| `drivers/pci/bus.c` | 10 | 总线级资源与遍历 |
| `drivers/pci/pci-driver.c` | 9 | ⚠️ 驱动注册 + `pci_bus_type` 本体 |
| `drivers/pci/search.c` | 8 | 按 ID 找设备 |
| `drivers/pci/of.c` | 8 | ⚠️ DT 解析（arm64 上全都在用，06 篇） |
| `drivers/pci/setup-bus.c` | 7 | 桥窗口计算与资源分配 |
| `drivers/pci/remove.c` | 5 | 拆除 |
| `drivers/pci/setup-res.c` | 4 | 单个 BAR 的分配/回收 |
| `drivers/pci/host-bridge.c` | 4 | 总线地址 ↔ CPU 地址换算 |
| `drivers/pci/irq.c` | 2 | `pci_request_irq` / `pci_free_irq` |
| `drivers/pci/msi/irqdomain.c` | 1 | `pci_msi_create_irq_domain` |

⚠️⚠️ **这张表本身就是一条重要判断**：**232 个接口里有近 60 个
（`probe.c` 26 + `setup-bus.c` 7 + `setup-res.c` 4 + `host-bridge.c` 4 + `of.c` 8
+ `remove.c` 5 + `bus.c` 部分）不是给设备驱动用的，而是给主机桥（控制器）驱动用的。**
⇒ **读 01 篇要先分清自己是哪一侧**：

| 你在写 | 看哪几节 |
|---|---|
| 一个 PCIe 设备的驱动（网卡/NVMe/GPU…） | §2–§9 |
| ⚠️ 一个 SoC 的 PCIe 控制器驱动 | §10（arm64 上的实际形态见 06 篇） |
| 一个"顺便扫一眼 PCI 设备"的别处代码 | §8 |

⚠️ **官方文档口径**：`Documentation/driver-api/pci/pci.rst` 用 13 条
`kernel-doc :export:` 指令（`:4-41`）把这些函数的 kerneldoc 抓成一篇 API 手册。
⇒ ⚠️⚠️ **它只是抓取，没有一行人写的说明** ——
真正给人读的只有 `Documentation/PCI/pci.rst`（578 行）和 `msi-howto.rst`（287 行），
本篇的"该怎么用"全部以它们为准（05 篇展开）。

---

## §2 驱动注册与匹配

### §2.1 注册：只有一个宏要记

```c
/* include/linux/pci.h:1555-1556 */
#define module_pci_driver(__pci_driver) \
        module_driver(__pci_driver, pci_register_driver, pci_unregister_driver)
```

⚠️ **全树 423 个 `.c` 文件在用 `module_pci_driver`**，是 PCI 最常见的写法。
它展开成 `module_init` + `module_exit` 两个样板函数。

| 接口 | 声明 | 实现/导出 | 说明 |
|---|---|---|---|
| `module_pci_driver(drv)` | `pci.h:1555` | 宏 | ⚠️ **默认选它** |
| `builtin_pci_driver(drv)` | `pci.h:1566` | 宏 | 只能编进内核（无 `module_exit`） |
| `pci_register_driver(drv)` | `pci.h:1542-1543` | 宏 | ⚠️ 需要在 `module_init` 里做别的事时用 |
| `__pci_register_driver()` | `pci.h:1538` | `pci-driver.c:1464` | ⚠️ **别直接调** —— 宏会替你填 `THIS_MODULE` 和 `KBUILD_MODNAME` |
| `pci_unregister_driver()` | `pci.h:1545` | `pci-driver.c:1481` | — |

⚠️ `__pci_register_driver()` 带 `__must_check`（`pci.h:1538`）⇒ **返回值必须检查**。

⚠️⚠️ **`id_table` 必须非空** —— 头文件里写在注释里：
`const struct pci_device_id *id_table;  /* Must be non-NULL for probe to be called */`
（`include/linux/pci.h:923`）。⇒ **填了 `probe` 但忘了 `id_table`，`probe` 永远不会被调，
而且没有任何报错。**

### §2.2 `struct pci_driver` 的 17 个成员

`include/linux/pci.h:920-938`。⚠️ **常填的只有五个**（完整逐字段见 02 §4）：

```c
static struct pci_driver my_driver = {
        .name           = "mydrv",              /* :922 */
        .id_table       = my_ids,               /* :923 ⚠️ 必须非空 */
        .probe          = my_probe,             /* :924 */
        .remove         = my_remove,            /* :925 ⚠️ 不支持热拔可为 NULL */
        .driver.pm      = &my_pm_ops,           /* ⚠️ 不要用 :926/:927 那两个 */
};
```

⚠️⚠️ **`.suspend` / `.resume`（`:926-927`）是遗留字段** ——
`pci.h:926` 的注释只写了 `/* Device suspended */`，没写"已过时"。
**新代码一律用 `.driver.pm`**（`struct dev_pm_ops`），理由与 `pci_bus_type.pm`
的分发逻辑有关（03 §4）。

⚠️ 另有 SR-IOV 三个（`.sriov_configure` `:929`、`.sriov_set_msix_vec_count` `:930`、
`.sriov_get_vf_total_msix` `:931`）和错误恢复一个（`.err_handler` `:932`）——
都属扩展面，04 篇讲。

⚠️ `.driver_managed_dma`（`:937`）**是 6.x 新增且容易漏**：
驱动自己管 DMA（如 VFIO/vfio-pci 那类把设备交给用户态的）必须置 `true`，
否则 IOMMU 层会拒绝。

### §2.3 `pci_device_id`：⚠️ 它不在 `pci.h` 里

```c
/* include/linux/mod_devicetable.h:43-49 */
struct pci_device_id {
        __u32 vendor, device;           /* Vendor and device ID or PCI_ANY_ID */
        __u32 subvendor, subdevice;     /* Subsystem ID's or PCI_ANY_ID */
        __u32 class, class_mask;        /* (class,subclass,prog-if) triplet */
        kernel_ulong_t driver_data;     /* Data private to the driver */
        __u32 override_only;
};
```

⚠️⚠️ **找不到它是读 PCI 代码的第一个坑**：`grep 'struct pci_device_id' include/linux/pci.h`
只能找到用它的地方，**定义在 `mod_devicetable.h`** ——
因为 `scripts/mod/file2alias.c` 要在**不包含 `pci.h`** 的情况下解析它来生成模块别名。

### §2.4 运行期加 ID：`pci_add_dynid()`

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_add_dynid()` | `pci.h:1570` | `pci-driver.c:79`（`_GPL`） |
| `pci_match_id()` | `pci.h:1575` | `pci-driver.c:117` |

⚠️ 它的用户态入口是 sysfs 的 `new_id`，`Documentation/PCI/pci.rst:96-113` 写了完整用法：

```sh
echo "8086 10f5" > /sys/bus/pci/drivers/foo/new_id
```

⚠️⚠️ **省略的字段有默认值，而默认值不直观**（`pci.rst:101-106`）：
`subvendor`/`subdevice` → `PCI_ANY_ID`，`class`/`class_mask` → `0`，
`driver_data` → `0UL`，`override_only` → `0`。
⚠️ **而且 `driver_data` 必须与驱动 `id_table` 里已有的某一项相同** ——
`pci.rst:108-110` 明确写了原因：**驱动会拿 `driver_data` 当索引去查自己的表**，
给一个表里没有的值等于让驱动读越界。

### §2.5 引用计数

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_dev_get()` | `pci.h:1163` | `pci-driver.c:1552` |
| `pci_dev_put()` | `pci.h:1164` | `pci-driver.c:1566` |
| `pci_dev_driver()` | `pci.h:1569` | `pci-driver.c:1506` |

⚠️ `pci.h:1165` 有一行值得注意：
`DEFINE_FREE(pci_dev_put, struct pci_dev *, if (_T) pci_dev_put(_T))`
—— 这是 6.11+ 的 `cleanup.h` 自动释放语法被回合进 6.1.158 的痕迹（99 §1.1）。
⚠️ **6.1 主线原版没有这一行**，用了它的代码不能往更早的 6.1.y 上搬。

⚠️⚠️ **哪些接口会替你加引用、必须配对 `pci_dev_put()`** —— 见 §8.2，
这是 PCI 上最常见的一类泄漏。

---

## §3 使能、BAR 与总线主控

⚠️ 本节这一组的调用顺序是**硬规定**，`Documentation/PCI/pci.rst:55-66` 列成了
八步清单，05 §2 逐条展开。这里只给签名与陷阱。

### §3.1 使能

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_enable_device()` | `pci.h:1304` | `pci.c:2097` | ⚠️ **必须先调它，之后才能碰 BAR** |
| `pci_enable_device_mem()` | `pci.h:1306` | `pci.c:2080` | 只使能 MEM 空间 |
| `pci_enable_device_io()` | `pci.h:1305` | `pci.c:2066` | 只使能 I/O 空间 |
| `pcim_enable_device()` | `pci.h:1308` | `pci.c:2179` | ⚠️⚠️ **devm 版，247 个文件在用** |
| `pcim_pin_device()` | `pci.h:1309` | `pci.c:2198` | ⚠️ 取消 devm 版的自动 disable |
| `pci_reenable_device()` | `pci.h:1307` | `pci.c:1995` | 复位/恢复后重新使能 |
| `pci_disable_device()` | `pci.h:1330` | `pci.c:2299` | — |

⚠️ 前六个全带 `__must_check`（`pci.h:1304-1308`）。

⚠️⚠️ **`pcim_enable_device()` 改变的不止是 disable 的时机** ——
它让**同一设备上后续所有 `pcim_*` / `pci_request_regions()` 都变成受 devres 管理的**
（03 §5 讲 devres 组的展开）。⇒ **一个设备上不要混用 `pci_enable_device()` 与
`pcim_iomap_regions()`。**

⚠️ **资源要到使能之后才知道能不能用** —— `pci.rst:251-255` 的原话是：
可用性"只有在 `pci_enable_device()` 之后才能确定"。
⇒ **在 probe 里"先检查 BAR 大小再决定要不要使能"的写法是错的。**

### §3.2 ⚠️⚠️ 一条官方承认的内核缺陷

`Documentation/PCI/pci.rst:210-218` 有一段标题就叫 **"OS BUG"** 的警告：

- 内核**在使能设备之前不检查资源分配是否合法**；
- ⇒ 两个设备的 BAR 落在同一段地址上时，**驱动无法察觉**；
- 原文措辞："not a common problem and unlikely to get fixed soon"。

⚠️ 这段文字**至今还写着 "as of 2.6.19"，引用的邮件链接是 2006 年的**。
⇒ ⚠️⚠️ **值得记住的结论**：**"`pci_enable_device()` 返回 0" 不等于
"这个设备的地址窗口是它自己的"。** 在 arm64 上这类冲突通常表现为
读回全 `0xff` 或直接 SError（§4.4）。

### §3.3 BAR 的申请与释放

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_request_regions()` | `pci.h:1459` | `pci.c:4164` |
| `pci_request_regions_exclusive()` | `pci.h:1460` | `pci.c:4186` |
| `pci_release_regions()` | `pci.h:1461` | `pci.c:4144` |
| `pci_request_region()` | `pci.h:1462` | `pci.c:4069` |
| `pci_release_region()` | `pci.h:1463` | `pci.c:3999` |
| `pci_request_selected_regions()` | `pci.h:1464` | `pci.c:4120` |
| `pci_request_selected_regions_exclusive()` | `pci.h:1465` | `pci.c:4128` |
| `pci_release_selected_regions()` | `pci.h:1466` | `pci.c:4087` |
| `pci_select_bars()` | `pci.h:1384` | `pci.c:6368` |

⚠️ 前两个带 `__must_check`。

⚠️⚠️ **释放顺序是反直觉的**：`pci.rst:247-249` 明确要求
**`pci_release_region()` 要放在 `pci_disable_device()` 之后**。
⇒ ⚠️ 与"申请顺序反过来释放"的直觉冲突，**但文档就是这么写的**，
原因是 disable 过程本身还要访问那些窗口。

⚠️ `pci.h:1813-1840` 有四个 inline 便捷包装
（`pci_request_io_regions` / `pci_release_io_regions` /
`pci_request_mem_regions` / `pci_release_mem_regions`），
实现就是 `pci_select_bars()` + `pci_request_selected_regions()` 两行。

### §3.4 BAR 的地址：四个宏

```c
/* include/linux/pci.h:2024-2028 */
#define pci_resource_n(dev, bar)        (&(dev)->resource[(bar)])
#define pci_resource_start(dev, bar)    (pci_resource_n(dev, bar)->start)
#define pci_resource_end(dev, bar)      (pci_resource_n(dev, bar)->end)
#define pci_resource_flags(dev, bar)    (pci_resource_n(dev, bar)->flags)
#define pci_resource_len(dev,bar)                                       \
```

⚠️⚠️ **不要直接从配置空间读 BAR** —— `pci.rst:238-241` 的原话：
MMIO 与 I/O 端口地址 **"should NOT be read directly from config space"**。
理由是**架构可能做了重映射**：配置空间里的数是总线地址，
`dev->resource[]` 里的是 CPU 能用的地址。
⇒ ⚠️ **arm64 上这两者几乎总是不同**（有 `ranges` 换算，见 §10.4、06 §3）。

⚠️ 这五个宏是**纯宏、不做任何检查**：`bar` 越界（>5，或 ROM 用 `PCI_ROM_RESOURCE`）
会静默读到相邻字段。

### §3.5 映射

| 接口 | 声明 | 说明 |
|---|---|---|
| `pci_iomap()` | `include/asm-generic/pci_iomap.h:13` | ⚠️ 128 个文件在用 |
| `pci_iomap_wc()` | `pci_iomap.h:14` | write-combining |
| `pci_iomap_range()` | `pci_iomap.h:15` | 只映射 BAR 的一段 |
| `pci_iomap_wc_range()` | `pci_iomap.h:18` | — |
| `pcim_iomap()` | `pci.h:2197` | devm 版 |
| `pcim_iounmap()` | `pci.h:2198` | — |
| `pcim_iomap_table()` | `pci.h:2199` | ⚠️ 返回 `void __iomem * const *`，按 BAR 号索引 |
| `pcim_iomap_regions()` | `pci.h:2200` | ⚠️⚠️ **112 个文件在用**：一次 request+map 一组 BAR |
| `pcim_iomap_regions_request_all()` | `pci.h:2201` | 申请全部 BAR，只映射 mask 里的 |
| `pcim_iounmap_regions()` | `pci.h:2203` | — |

⚠️ **`pcim_iomap_regions()` 是设备驱动里最省事的写法**：
`pcim_enable_device()` + `pcim_iomap_regions(pdev, BIT(0), name)` +
`pcim_iomap_table(pdev)[0]` 三行拿到指针，出错路径一行都不用写（05 §3）。

### §3.6 总线主控与 MWI

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_set_master()` | `pci.h:1333` | `pci.c:4499` | ⚠️⚠️ **519 个文件在用，全树 PCI 接口里第一名** |
| `pci_clear_master()` | `pci.h:1334` | `pci.c:4509` | — |
| `pci_set_mwi()` | `pci.h:1338` | `pci.c:4578` | ⚠️ `__must_check` |
| `pcim_set_mwi()` | `pci.h:1339` | `pci.c:4599` | `__must_check` |
| `pci_try_set_mwi()` | `pci.h:1340` | `pci.c:4618` | ⚠️ 失败可忽略的版本 |
| `pci_clear_mwi()` | `pci.h:1341` | `pci.c:4638` | — |
| `pci_set_cacheline_size()` | — | `pci.c:4547`（`_GPL`） | ⚠️ MWI 的前置 |

⚠️ **设备要做 DMA 就必须 `pci_set_master()`** —— 它置的是命令寄存器的
Bus Master 位，没有它设备发不出 DMA 请求。⚠️ **这是"驱动跑起来了但收不到数据"
的经典原因。**

⚠️⚠️ **DMA 掩码必须自己设**：`pci.rst:274-285` 要求
**所有 PCI-X 和 PCIe 驱动都必须调 `dma_set_mask()`** ——
`pci_set_master()` 不含这一步。

---

## §4 配置空间访问

### §4.1 三组访问器，选错会静默出错

| 组 | 接口 | 何时用 |
|---|---|---|
| ① 按设备 | `pci_read_config_{byte,word,dword}()` `pci.h:1227-1229`<br>`pci_write_config_{byte,word,dword}()` `pci.h:1230-1232` | ⚠️ **设备驱动只该用这一组** |
| ② 按总线+devfn | `pci_bus_read_config_*()` / `pci_bus_write_config_*()` `access.c:73-78` | ⚠️ 枚举阶段（设备对象还不存在） |
| ③ PCIe 能力 | `pcie_capability_read_{word,dword}()` `pci.h:1234-1235`<br>`pcie_capability_write_*()` `pci.h:1236-1237` | ⚠️⚠️ **读 PCIe 能力寄存器一律用这组** |

导出位置：① `access.c:550 :560 :571 :579 :587 :596`；
② `access.c:73-78`；③ `access.c:440 :468 :480 :492`。

⚠️⚠️ **第三组为什么不能用第一组替代** —— `pci.h:2331-2334` 的注释给了答案：
它内部用 `dev->pcie_cap` 这个缓存过的偏移，**而且对某些寄存器做了兼容性处理**
（老设备上不存在的寄存器返回 0 而不是垃圾）。
⚠️ 注释同时说明：**这个字段只是缓存，为可读性起见"请用 `pci_find_capability()`"** ——
即**不要自己去读 `dev->pcie_cap`**。

### §4.2 ⚠️ 读改写：6.1.158 上这一组被拆过

```c
/* include/linux/pci.h:1238-1243 */
int pcie_capability_clear_and_set_word_unlocked(struct pci_dev *dev, int pos, ...);
int pcie_capability_clear_and_set_word_locked(struct pci_dev *dev, int pos, ...);
int pcie_capability_clear_and_set_dword(struct pci_dev *dev, int pos, ...);
```

⚠️⚠️ **`pcie_capability_clear_and_set_word()` 现在是一个 inline 分发器**
（`pci.h:1257-1269`）：按 `pos` 判断该走 `_locked` 还是 `_unlocked` 版本。
⇒ **调用方仍写不带后缀的名字**（`pci.h:1246-1256` 的 kerneldoc 就在讲这件事），
但 ⚠️ **两个带后缀的符号是导出的**（`access.c:509 :523`），
grep 时会看到三个名字。**新代码只用不带后缀的那个。**

⚠️ 另外四个只是包装（`pci.h:1272-1294`）：
`pcie_capability_set_word` / `set_dword` / `clear_word` / `clear_dword`。

### §4.3 能力（Capability）查找

| 接口 | 声明 | 导出 | 找什么 |
|---|---|---|---|
| `pci_find_capability()` | `pci.h:1180` | `pci.c:509` | ⚠️ 传统能力链（偏移 < 0x100） |
| `pci_find_next_capability()` | `pci.h:1181` | `pci.c:458`（`_GPL`） | 同类能力的第二个 |
| `pci_bus_find_capability()` | — | `pci.c:536` | ⚠️ 设备对象还没建好时用 |
| `pci_find_ext_capability()` | `pci.h:1184` | `pci.c:608`（`_GPL`） | ⚠️⚠️ **PCIe 扩展能力（偏移 ≥ 0x100）** |
| `pci_find_next_ext_capability()` | — | `pci.c:588`（`_GPL`） | — |
| `pci_find_vsec_capability()` | `pci.h:1187` | `pci.c:745`（`_GPL`） | 厂商专有扩展能力 |
| `pci_find_dvsec_capability()` | `pci.h:1188` | `pci.c:777`（`_GPL`） | Designated VSEC |
| `pci_find_ht_capability()` | — | `pci.c:712`（`_GPL`） | HyperTransport |
| `pci_find_next_ht_capability()` | — | `pci.c:689`（`_GPL`） | — |
| `pci_get_dsn()` | — | `pci.c:642`（`_GPL`） | 设备序列号（DSN 扩展能力） |

⚠️⚠️ **`pci_find_capability()` 和 `pci_find_ext_capability()` 是两条完全不同的链，
不能互换**：前者从 `PCI_CAPABILITY_LIST` 出发走 8 位 next 指针
（`pci.c:499-507`），后者用 `PCI_EXT_CAP_ID`/`PCI_EXT_CAP_NEXT` 走 0x100 以上的链
（`pci.c:574-586`）。
⇒ 传错了返回 0，而 **0 是"没找到"的合法返回值** ⇒ 症状是"这个能力我的设备没有"。

⚠️⚠️ **官方文档在这一点上是错的，必须知道**：`pci.rst:466-468` 写的是
"要访问 Extended PCI Capability 寄存器，只要调 `pci_find_capability()`"。
**照它做在 PCIe 扩展能力（AER/DPC/SR-IOV/…）上一律返回 0** ——
`pci_find_capability()` 的实现里根本没有 0x100 以上的路径。
⇒ 本条记进 99 §2 的文档陈旧清单。

⚠️ 全部返回 **0 表示没找到**（不是负错误码）：`pci_find_capability()` 返回 `u8`，
`pci_find_ext_capability()` 返回 `u16`（`pci.h:1180` `:1184`）。

### §4.4 ⚠️⚠️ 配置空间访问的返回值不是 errno

```c
/* include/linux/pci.h:744-750 */
#define PCIBIOS_SUCCESSFUL              0x00
#define PCIBIOS_FUNC_NOT_SUPPORTED      0x81
#define PCIBIOS_BAD_VENDOR_ID           0x83
#define PCIBIOS_DEVICE_NOT_FOUND        0x86
#define PCIBIOS_BAD_REGISTER_NUMBER     0x87
#define PCIBIOS_SET_FAILED              0x88
#define PCIBIOS_BUFFER_TOO_SMALL        0x89
```

⚠️ **全是正数**，而 errno 是负数 ⇒ `if (ret < 0)` **检查不出 PCIBIOS 错误**。

**转换器**：`pcibios_err_to_errno()`（`include/linux/pci.h:753`）——
它先做一次判断 `if (err <= PCIBIOS_SUCCESSFUL) return err;`（"假定已经是 errno"），
再按表映射：`FUNC_NOT_SUPPORTED`→`-ENOENT`、`BAD_VENDOR_ID`→`-ENOTTY`、
`DEVICE_NOT_FOUND`→`-ENODEV`、`BAD_REGISTER_NUMBER`→`-EFAULT`、
`SET_FAILED`→`-EIO`、`BUFFER_TOO_SMALL`→`-ENOSPC`，其余 `-ERANGE`。

⚠️⚠️ **`pci.rst:452-458` 只说了"返回 0 或 `PCIBIOS_...`，可以用
`pcibios_strerror` 翻译"** —— 而 ⚠️ **`pcibios_strerror` 在 6.1 树里不存在**
（99 §2 记了这条文档陈旧项）。**实际能用的是上面这个 `pcibios_err_to_errno()`。**

⚠️ **更常见的一种失败是"不报错"**：设备掉线时配置空间读回全 `1`
（`0xffffffff`），函数返回 **`PCIBIOS_SUCCESSFUL`**。
⇒ **判据是数据本身，不是返回值** ⇒ 用 `pci_device_is_present()`（§9.3）
或检查读回值是否全 `f`。

### §4.5 ⚠️ 与热插拔/复位互斥：`pci_cfg_access_*`

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_cfg_access_lock()` | `pci.h:1767` | `access.c:284`（`_GPL`） |
| `pci_cfg_access_trylock()` | `pci.h:1768` | `access.c:308`（`_GPL`） |
| `pci_cfg_access_unlock()` | `pci.h:1769` | `access.c:333`（`_GPL`） |

⚠️⚠️ 它保护的是"**设备正在复位/被移除，此刻的配置空间访问必须被挡住**"这件事，
底层是 `pci_lock` 这把 ⚠️ **`DEFINE_RAW_SPINLOCK`**（`drivers/pci/access.c:15`）。
⇒ **普通设备驱动不该调这三个**；它们的使用者是复位路径和热插拔路径（03 §3）。

⚠️ `access.c:239` `:257` 那两个导出（`pci_user_read_config_##size` /
`pci_user_write_config_##size`）是**宏生成的**：一次展开出 byte/word/dword 六个符号，
服务于 sysfs/proc 的用户态访问路径（07 §3）。
⚠️⚠️ **索引里看不到它们的真名** —— grep `pci_user_read_config_dword` 找不到定义行，
只能找到宏（99 §2 记了这条口径）。

---

## §5 中断

⚠️ 本节的规范来源是 `Documentation/PCI/msi-howto.rst`（287 行，读完了）。
⚠️⚠️ **它比代码严格得多**：明确点名了五个"不要再用"的接口（§5.5）。

### §5.1 唯一该用的分配接口

```c
/* include/linux/pci.h:1984-1990 —— 注意它在 #endif /* CONFIG_PCI */ 之外 */
static inline int
pci_alloc_irq_vectors(struct pci_dev *dev, unsigned int min_vecs,
                      unsigned int max_vecs, unsigned int flags)
{
        return pci_alloc_irq_vectors_affinity(dev, min_vecs, max_vecs, flags,
                                              NULL);
}
```

⚠️⚠️ **一条容易被 grep 误导的事实**：`pci_alloc_irq_vectors()`
**本身不是导出符号** —— 它是个 inline 包装（`pci.h:1985`），
真正导出的是 `pci_alloc_irq_vectors_affinity()`（`msi.c:1059`）。
⇒ **`grep EXPORT_SYMBOL.*pci_alloc_irq_vectors` 只会命中带 `_affinity` 的那个**，
而 `msi-howto.rst:96-98` 全篇讲的都是不带 `_affinity` 的这个（99 §2）。

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_alloc_irq_vectors()` | `pci.h:1985`（inline） | ⚠️ **无**（包装） |
| `pci_alloc_irq_vectors_affinity()` | `pci.h:1632` | `msi.c:1059` |
| `pci_free_irq_vectors()` | `pci.h:1636` | `msi.c:1072` |
| `pci_irq_vector()` | `pci.h:1637` | `msi.c:1096` |
| `pci_irq_get_affinity()` | `pci.h:1638` | `msi.c:1134` |
| `pci_msi_enabled()` | `pci.h:1620` | `msi.c:1157` |
| `msi_desc_to_pci_dev()` | — | `msi.c:1140` |

**契约**（`msi-howto.rst:99-112`）：

- 返回**实际分配到的向量数**，或负错误码；
- ⚠️⚠️ **`min_vecs` 都满足不了时返回 `-ENOSPC`** —— 这是本组唯一有明确文档约定的错误码；
- ⚠️ **成功后必须用 `pci_irq_vector(dev, nr)` 把序号翻译成 Linux IRQ 号**，
  **不能直接用 `dev->irq`**（多向量时 `dev->irq` 无意义）。

### §5.2 `flags` 的五个位

```c
/* include/linux/pci.h:1068-1071 */
#define PCI_IRQ_LEGACY          (1 << 0) /* Allow legacy interrupts */
#define PCI_IRQ_MSI             (1 << 1) /* Allow MSI interrupts */
#define PCI_IRQ_MSIX            (1 << 2) /* Allow MSI-X interrupts */
#define PCI_IRQ_AFFINITY        (1 << 3) /* Auto-assign affinity */
```

⚠️ 另有 `PCI_IRQ_VIRTUAL`（`pci.h:1602`，SR-IOV VF 用）和
`PCI_IRQ_ALL_TYPES`（`pci.h:1604-1605`，= `LEGACY|MSI|MSIX`）。

⚠️⚠️ **`pci.rst:336-338` 的建议是"总是也带上 `PCI_IRQ_LEGACY`"** ——
理由是**总有平台不支持 MSI**，不带它就是白白拒绝一部分硬件。
⚠️ 内核对此有配合：`!CONFIG_PCI_MSI` 时的 inline 版本
（`pci.h:1657-1665`）会在 `(flags & PCI_IRQ_LEGACY) && min_vecs == 1 && dev->irq`
成立时返回 1，**即自动退回单个传统中断**。

⚠️ **优先级是 MSI-X > MSI > legacy**（`msi-howto.rst:121-123`），
且 **MSI 和 MSI-X 同时只能开一种**（`msi-howto.rst:32-33`）。

**两者的能力差**（`msi-howto.rst:121-131`）：

| | MSI | MSI-X |
|---|---|---|
| 向量数 | ⚠️ **最多 32**，且**必须是 2 的幂** | ⚠️ **1–2048**，任意个 |
| 向量号 | ⚠️ **必须连续** | 任意 |
| 亲和性 | ⚠️⚠️ **可能全部落在同一组 CPU 上** | 每个向量独立 |
| 屏蔽 | PCI 3.0 起才有 per-vector masking | 有 |

⇒ ⚠️ **要多队列/多 CPU 分发就必须要 MSI-X**，MSI 的"2 的幂 + 连续 + 可能同 CPU"
三条限制会让队列绑核失效。

### §5.3 三种标准调用形态

`msi-howto.rst:135-157` 给了三种，**照抄即可**：

```c
/* ① 能用几个用几个（网卡多队列的典型写法） */
nvec = pci_alloc_irq_vectors(pdev, 1, want, PCI_IRQ_ALL_TYPES);
if (nvec < 0)
        return nvec;

/* ② 必须正好 N 个，少一个都不行 */
ret = pci_alloc_irq_vectors(pdev, N, N, PCI_IRQ_ALL_TYPES);

/* ③ 只要一个（最常见） */
ret = pci_alloc_irq_vectors(pdev, 1, 1, PCI_IRQ_ALL_TYPES);
```

⚠️ **随后按 `msi_enabled` / `msix_enabled` 选处理函数**（`pci.rst:331-343`）——
传统中断与 MSI 的处理函数往往不同（前者要判"是不是我的中断"，后者不用）。

⚠️⚠️ **传统中断的处理函数必须带 `IRQF_SHARED`**（`pci.rst:311-315`）：
INTx 是共享线，不带这个标志 `request_irq()` 会失败或独占别人的线。

⚠️⚠️ **多向量设备的自旋锁必须用 `spin_lock_irqsave()` 或 `spin_lock_irq()`**
（`msi-howto.rst:195-202`）—— 用普通 `spin_lock()` 会**自己把自己锁死**：
同一驱动的另一个向量在别的 CPU 上进来，拿不到锁又不能被抢占。

### §5.4 用 MSI 的两个真实好处

`pci.rst:345-358` 和 `msi-howto.rst:39-66` 讲的是同一件事，值得单列：

1. ⚠️ **独占一个向量** ⇒ 处理函数不必判断"这次是不是我的"；
2. ⚠️⚠️ **MSI 本质上是"设备向 Local APIC 发的一次 DMA 写"**（`pci.rst:325-330`）
   ⇒ **它与设备的 DMA 数据之间有写序保证** ⇒
   **处理函数开头那个"为了刷新写缓冲而做的 MMIO 读"可以省掉。**

⚠️ 第 2 条是**性能上的实质差别**：那个刷新读在 PCIe 上是一次完整往返。

⚠️ 顺带记一条诊断知识（`pci.rst:376-393`）：中断风暴（"screaming interrupt"）的
判据是内核在同一根线上连续 **100,000** 次都拿不到"有人认领"，然后禁掉这根线。

### §5.5 ⚠️⚠️ 五个明确废弃的接口

`msi-howto.rst:171-181` 点名"**不要在新代码里用**"：

| 废弃接口 | 声明 | 导出 | 用什么替代 |
|---|---|---|---|
| `pci_enable_msi()` | `pci.h:1621` | `msi.c:935` | `pci_alloc_irq_vectors(d,1,1,PCI_IRQ_MSI)` |
| `pci_disable_msi()` | `pci.h:1616` | `msi.c:782` | `pci_free_irq_vectors()` |
| `pci_enable_msix_range()` | `pci.h:1622` | `msi.c:994` | `pci_alloc_irq_vectors()` |
| `pci_enable_msix_exact()` | `pci.h:1624`（inline） | ⚠️ 无 | `pci_alloc_irq_vectors(d,N,N,…)` |
| `pci_disable_msix()` | `pci.h:1618` | `msi.c:871` | `pci_free_irq_vectors()` |

⚠️ 文档还建议**别用** `pci_msi_vec_count()`（`pci.h:1615`，`msi.c:749`）和
`pci_msix_vec_count()`（`pci.h:1617`，`msi.c:802`）——
"能分几个"应该由 `pci_alloc_irq_vectors()` 的返回值告诉你，而不是先问再要。

⚠️⚠️ **注意 `struct msix_entry`（`pci.h:1609`）只服务于废弃的
`pci_enable_msix_range()`** ⇒ **新代码里出现这个结构体就是信号**。

### §5.6 传统中断的直接操作

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_intx()` | `pci.h:1343` | `pci.c:4687`（`_GPL`） | ⚠️ 开/关 INTx（改命令寄存器的 INTX_DISABLE 位） |
| `pci_check_and_mask_intx()` | — | `pci.c:4745`（`_GPL`） | ⚠️ 读状态位并屏蔽，用于共享线上的"是不是我" |
| `pci_check_and_unmask_intx()` | — | `pci.c:4759`（`_GPL`） | — |

⚠️ 中断申请的便捷版（自带 `devm` 风格的命名与格式化）：

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_request_irq()` | `pci.h:1390` | `irq.c:56` |
| `pci_free_irq()` | `pci.h:1393` | `irq.c:76` |

⚠️ `pci_request_irq()` 带 `__printf(6, 7)`（`pci.h:1390`）⇒
**第 6 个参数是格式字符串**，中断名字可以直接拼（如 `"%s-rx%d"`），
不必自己 `kasprintf` 再记着释放。⚠️ **这是 `irq.c` 整个文件（76 行）存在的全部理由。**

---

## §6 电源管理

⚠️ **绝大多数驱动只需要 `.driver.pm` 里的 `dev_pm_ops`，本节这一组都不用碰。**
⚠️⚠️ 因为 `pci_bus_type.pm`（`pci-driver.c:1695`）已经把 save/restore/set_power_state
串在标准 PM 流程里了（03 §4）。**本节列出来是为了看懂那条流程。**

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_save_state()` | `pci.h:1402` | `pci.c:1718` | ⚠️ 保存配置空间 + 各能力寄存器 |
| `pci_restore_state()` | `pci.h:1403` | `pci.c:1843` | — |
| `pci_store_saved_state()` | `pci.h:1404` | `pci.c:1889`（`_GPL`） | ⚠️ 把保存的状态复制到一块新内存 |
| `pci_load_saved_state()` | `pci.h:1405` | `pci.c:1925`（`_GPL`） | — |
| `pci_load_and_free_saved_state()` | `pci.h:1407` | `pci.c:1941`（`_GPL`） | ⚠️ 配对上一个，省一次 `kfree` |
| `pci_set_power_state()` | `pci.h:1410` | `pci.c:1506` | D0–D3cold |
| `pci_set_power_state_locked()` | `pci.h:1411` | `pci.c:1514` | ⚠️ 已持有 `pci_bus_sem` 时用 |
| `pci_choose_state()` | `pci.h:1412` | `pci.c:2941` | ⚠️ 把 `pm_message_t` 映射成 `pci_power_t` |
| `pci_pme_capable()` | `pci.h:1413` | `pci.c:2425` | 某个 D 态能不能发 PME |
| `pci_pme_active()` | `pci.h:1414` | `pci.c:2565` | 开/关 PME 上报 |
| `pci_enable_wake()` | `pci.h:1415` | `pci.c:2654` | — |
| `pci_wake_from_d3()` | `pci.h:1416` | `pci.c:2676` | ⚠️ 上一个的常用特化 |
| `pci_prepare_to_sleep()` | `pci.h:1417` | `pci.c:2765` | 选最深可唤醒 D 态并进入 |
| `pci_back_from_sleep()` | `pci.h:1418` | `pci.c:2784` | 回 D0 并关 wake |
| `pci_dev_run_wake()` | `pci.h:1419` | `pci.c:2849`（`_GPL`） | ⚠️ 能不能做运行时唤醒 |
| `pci_d3cold_enable()` | `pci.h:1420` | `pci.c:3169`（`_GPL`） | — |
| `pci_d3cold_disable()` | `pci.h:1421` | `pci.c:3186`（`_GPL`） | ⚠️ 设备不耐 D3cold 时用 |
| `pci_platform_power_transition()` | — | `pci.c:1142`（`_GPL`） | ⚠️⚠️ **平台钩子，arm64 上基本是空的** |
| `pci_power_names[]` | — | `pci.c:42`（`_GPL`） | D 态名字表，打日志用 |
| `pci_choose_state()` 的输入 | — | — | 见 02 §7 的 `pci_power_t` |

⚠️⚠️ **`pci_platform_power_transition()` 在 arm64 上要特别注意**：
PCI 的 D 态切换有两条路 —— **配置空间的 PMCSR**（设备自己）和
**平台固件**（x86 上是 ACPI 的 `_PS0`/`_PS3`）。
**arm64 + DT 没有后者** ⇒ **这一路是空转**（本套 ACPI 一律裁掉，理由与清单在 99 §2.7）。
⇒ ⚠️ **在 arm64 上"设备进不了 D3cold"通常不是内核问题，而是没有平台电源控制手段。**

⚠️ `pci_pci_problems`（`pci.c:50`）和 `isa_dma_bridge_buggy`（`pci.c:46`）是
两个导出的**全局变量**，都是 x86 遗留的 quirk 标志。⚠️ **arm64 上恒为初始值。**

---

## §7 复位

⚠️ 这一组的使用者主要是 **VFIO、错误恢复路径和 sysfs 的 `reset` 属性**，
普通驱动很少直接调。⚠️⚠️ 但**每一个都可能让设备短暂消失**，
读复位相关代码时必须能分清"谁持锁、复位到什么粒度"。

| 接口 | 声明 | 导出 | 粒度 / 前置条件 |
|---|---|---|---|
| `pcie_flr()` | `pci.h:1360` | `pci.c:4803`（`_GPL`） | ⚠️ 功能级复位（Function Level Reset），**要求设备支持 FLR** |
| `pcie_reset_flr()` | `pci.h:1359` | `pci.c:4825`（`_GPL`） | ⚠️ `probe=true` 时只探测能不能做，不真做 |
| `__pci_reset_function_locked()` | `pci.h:1361` | `pci.c:5447`（`_GPL`） | ⚠️⚠️ **调用者必须已持 `pci_dev_lock()`** |
| `pci_reset_function()` | `pci.h:1362` | `pci.c:5514`（`_GPL`） | ⚠️ 自己加锁，**会先 save/restore 状态** |
| `pci_reset_function_locked()` | `pci.h:1363` | `pci.c:5548`（`_GPL`） | 同上但要求外部持锁 |
| `pci_try_reset_function()` | `pci.h:1364` | `pci.c:5573`（`_GPL`） | ⚠️ 拿不到锁就返回 `-EAGAIN` |
| `pci_reset_bus()` | `pci.h:1367` | `pci.c:5972`（`_GPL`） | ⚠️⚠️ **整条总线**（桥的 Secondary Bus Reset） |
| `pci_probe_reset_bus()` | `pci.h:1366` | `pci.c:5933`（`_GPL`） | 只探测 |
| `pci_probe_reset_slot()` | `pci.h:1365` | `pci.c:5830`（`_GPL`） | 只探测 |
| `pci_bridge_secondary_bus_reset()` | `pci.h:1497` | `pci.c:5136`（`_GPL`） | ⚠️ 直接拉桥的复位位 |
| `pci_wait_for_pending_transaction()` | `pci.h:1347` | `pci.c:4775` | ⚠️ 复位前等设备的未完成事务排空 |

**配套的锁**：

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_dev_lock()` | `pci.h:1771` | `pci.c:5196`（`_GPL`） |
| `pci_dev_trylock()` | `pci.h:1772` | `pci.c:5209`（`_GPL`） |
| `pci_dev_unlock()` | `pci.h:1773` | `pci.c:5216`（`_GPL`） |

⚠️ `pci.h:1774` 还有
`DEFINE_GUARD(pci_dev, struct pci_dev *, pci_dev_lock(_T), pci_dev_unlock(_T))`
—— 与 §2.5 的 `DEFINE_FREE` 一样是 6.11+ `cleanup.h` 语法的回合（99 §1.1）。

⚠️⚠️ **`pci_dev_lock()` 不只是一把互斥锁**：它同时会
`pci_cfg_access_lock()`（§4.5）⇒ **持有它期间任何配置空间访问都会被挡住/延后。**
⇒ **在它里面调 `pci_read_config_dword()` 是错的**（03 §7.3）。

⚠️ **`pci_reset_bus()` 的杀伤面要特别小心**：Secondary Bus Reset 会复位
**桥下面所有设备**，而 arm64 SoC 上常见的形态是"一个 Root Port 下只挂一个设备"
⇒ 看起来像功能级复位，**换一块板子挂了两个设备就会连带打死邻居**（07 篇）。

---

## §8 查找与遍历

### §8.1 按 ID 找设备

| 接口 | 声明 | 导出 | 找什么 |
|---|---|---|---|
| `pci_get_device()` | `pci.h:1192` | `search.c:335` | vendor + device |
| `pci_get_subsys()` | `pci.h:1194` | `search.c:314` | 再加 subvendor + subdevice |
| `pci_get_class()` | `pci.h:1200` | `search.c:364` | 按 class 码 |
| `pci_get_slot()` | `pci.h:1197` | `search.c:211` | 总线 + devfn |
| `pci_get_domain_bus_and_slot()` | `pci.h:1198` | `search.c:240` | ⚠️ domain + bus + slot |
| `pci_dev_present()` | `pci.h:1201` | `search.c:392` | ⚠️ 只问"在不在"，不返回设备 |
| `pci_find_bus()` | `pci.h:1127` | `search.c:155` | domain + busnr |
| `pci_find_next_bus()` | `pci.h:1186` | `search.c:178` | 迭代所有总线 |
| `pci_root_buses` | — | `probe.c:37` | ⚠️ 导出的**全局链表头** |

⚠️ 遍历宏：`for_each_pci_dev(d)`（`pci.h:552`）——
展开成 `while ((d = pci_get_device(PCI_ANY_ID, PCI_ANY_ID, d)) != NULL)`。

### §8.2 ⚠️⚠️ 引用计数：`pci_get_*` 必须配 `pci_dev_put()`

`Documentation/PCI/pci.rst:142-175` 用一整节讲这件事：
**`pci_get_device()` / `pci_get_class()` / `pci_get_subsys()` 返回的设备
带着一个引用**，用完必须 `pci_dev_put()`。

| 前缀 | 加引用吗 | 后果 |
|---|---|---|
| ⚠️⚠️ `pci_get_*` | **加** | 忘了 `pci_dev_put()` ⇒ **设备永远拔不掉** |
| `pci_find_*` | ⚠️ **不加** | ⇒ **返回的指针没有生命周期保证** |

⚠️ **`for_each_pci_dev()` 里 `break` 出来时要自己 `pci_dev_put()`** ——
宏帮你在正常迭代时释放上一个，**提前退出那次不会**。
⇒ 这是 PCI 上最常见的一类静态分析告警。

⚠️⚠️ **`pci.rst:522-536` 有一张"过时函数"对照表，必须记住**：

| 别用 | 用 | 为什么 |
|---|---|---|
| `pci_find_device()` | `pci_get_device()` | ⚠️ 前者不加引用 |
| `pci_find_subsys()` | `pci_get_subsys()` | 同上 |
| `pci_find_slot()` | `pci_get_domain_bus_and_slot()` | ⚠️ 前者没有 domain 概念 |
| `pci_get_slot()` | `pci_get_domain_bus_and_slot()` | ⚠️ 同上 |

⚠️ **arm64 上"没有 domain 概念"是硬伤**：多个 PCIe 控制器 = 多个 domain，
`pci_find_slot()` 那种只给 bus/slot 的接口**根本表达不出目标**（06 §5）。

⚠️⚠️ **而 `pci.rst:497-501` 还有一条更强的纪律**：
**驱动不该用总线号/槽号/功能号去认设备** ——
"设备的位置随插槽和主板而变"。**要显示设备就用 `pci_name(pci_dev)`**
（`pci.h:2047`，`pci.rst:493-496`）。

### §8.3 遍历一棵子树

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_walk_bus()` | `pci.h:1580` | `bus.c:436`（`_GPL`） | ⚠️ 回调返回非 0 就停 |
| `pci_walk_bus_locked()` | `pci.h:1582` | `bus.c:444`（`_GPL`） | ⚠️ 已持 `pci_bus_sem` 时用 |
| `pci_find_host_bridge()` | `pci.h:621` | `host-bridge.c:26`（`_GPL`） | 从任意 bus 回溯到主机桥 |

⚠️⚠️ **`pci_walk_bus()` 与 `pci_walk_bus_locked()` 选错会死锁**：
错误恢复路径（`pcie/err.c`）在已持锁的上下文里调，**必须用后者**。
⇒ 这两个符号的存在本身就说明这条路踩过（03 §7.4）。

---

## §9 链路、速率与设备状态

### §9.1 PCIe 链路参数

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pcie_get_mps()` | `pci.h:1353` | `pci.c:6144` | Max Payload Size |
| `pcie_set_mps()` | `pci.h:1354` | `pci.c:6172` | ⚠️ **不要在驱动里改**，见下 |
| `pcie_get_readrq()` | `pci.h:1351` | `pci.c:6081` | Max Read Request Size |
| `pcie_set_readrq()` | `pci.h:1352` | `pci.c:6128` | ⚠️ 这个驱动可以调 |
| `pcie_bandwidth_available()` | `pci.h:1355` | `pci.c:6229` | ⚠️ 返回可用带宽并指出瓶颈设备 |
| `pcie_get_speed_cap()` | `pci.h:307` | `pci.c:6265` | 链路能跑多快 |
| `pcie_get_width_cap()` | `pci.h:308` | `pci.c:6284` | 几条 lane |
| `pcie_print_link_status()` | `pci.h:1358` | `pci.c:6351` | ⚠️⚠️ **一行打印链路状态，并在带宽不足时警告** |
| `pcie_link_speed[]` | — | `probe.c:693`（`_GPL`） | 速率枚举 → 数值表 |
| `pci_speed_string()` | — | `probe.c:731`（`_GPL`） | 速率枚举 → 字符串 |
| `pcie_update_link_speed()` | — | `probe.c:737`（`_GPL`） | 链路速率变化后刷新缓存 |
| `pcie_bus_configure_settings()` | — | `probe.c:2884`（`_GPL`） | ⚠️ **全总线统一 MPS**，主机桥驱动调 |

⚠️⚠️ **`pcie_set_mps()` 是设备驱动不该碰的**：MPS 必须在**一条链路上所有设备之间一致**，
统一由 `pcie_bus_configure_settings()` 在枚举后设定。
⇒ **单个驱动改自己的 MPS = 制造不一致 = 事务被对端丢弃。**
⚠️ 相对地 `pcie_set_readrq()` 只影响本设备发出的读请求长度，**改它是安全的**。

⚠️ **`pcie_print_link_status()` 值得每个高带宽设备驱动都调一次**：
它会在"设备想要的带宽 > 链路能给的带宽"时打一条 warning
⇒ **把"插在 x1 槽里的 x8 卡"这种现场问题直接暴露在 dmesg 里。**

### §9.2 PCI-X 遗留

| 接口 | 声明 | 导出 |
|---|---|---|
| `pcix_get_max_mmrbc()` | `pci.h:1348` | `pci.c:5995` |
| `pcix_get_mmrbc()` | `pci.h:1349` | `pci.c:6018` |
| `pcix_set_mmrbc()` | `pci.h:1350` | `pci.c:6065` |

⚠️ **arm64 上不存在 PCI-X 硬件**，这三个恒返回错误。列在这里只为说明
"grep 到 `pcix_` 就可以跳过"。

### §9.3 设备还在不在

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_device_is_present()` | `pci.h:1385` | `pci.c:6524`（`_GPL`） | ⚠️⚠️ **判断设备是否还在（读 vendor ID 是否全 f）** |
| `pci_status_get_and_clear_errors()` | `pci.h:1388` | `pci.c:221`（`_GPL`） | 读并清状态寄存器的错误位 |
| `pci_ignore_hotplug()` | `pci.h:1386` | `pci.c:6535`（`_GPL`） | ⚠️ 告诉核心"这个设备的链路抖动不算热插拔" |
| `pci_ats_disabled()` | — | `pci.c:161`（`_GPL`） | 全局 ATS 开关 |
| `pci_pr3_present()` | — | `pci.c:6455`（`_GPL`） | ⚠️ ACPI `_PR3` 相关，arm64 恒 false（99 §2.7） |

⚠️ **`pci_device_is_present()` 是 §4.4 那个"读回全 f 但返回成功"问题的标准解法** ——
错误路径里先问它一句，能把"设备掉了"和"寄存器值真的是 0xffffffff"分开。

---

## §10 主机桥与枚举（控制器驱动侧）

⚠️⚠️ **本节是给写 SoC PCIe 控制器驱动的人看的。设备驱动作者可以整节跳过。**
⚠️ arm64 上这一侧的实际形态（`pci-host-generic.c`、`dwc/`、ECAM）见 06 篇。

### §10.1 一个 arm64 控制器驱动的最短路径

```
probe()
  ① devm_pci_alloc_host_bridge(dev, priv_size)   pci.h:618 / probe.c:646
  ② 填 bridge->sysdata / ->ops / ->busnr ...
  ③ pci_host_probe(bridge)                       pci.h:1133 / probe.c:3117
        ├─ pci_scan_root_bus_bridge()             probe.c:3218
        │     └─ pci_scan_child_bus()             probe.c:3035
        ├─ pci_assign_unassigned_bus_resources()  setup-bus.c:2341
        └─ pci_bus_add_devices()                  bus.c:380
```

⚠️⚠️ **`pci_host_probe()` 是本套里"一个函数替掉一整段样板"的极端例子**：
`devm_pci_alloc_host_bridge` 全树 **31 个文件**在用，`pci_host_probe` **26 个** ——
⇒ **arm64 的控制器驱动基本都是这两行加上自己的时钟/复位/PHY 初始化。**

### §10.2 主机桥对象

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_alloc_host_bridge()` | `pci.h:617` | `probe.c:616` |
| `devm_pci_alloc_host_bridge()` | `pci.h:618` | `probe.c:646` |
| `pci_free_host_bridge()` | `pci.h:620` | `probe.c:652` |
| `pci_find_host_bridge()` | `pci.h:621` | `host-bridge.c:26`（`_GPL`） |
| `pci_set_host_bridge_release()` | `pci.h:623` | `host-bridge.c:49`（`_GPL`） |

### §10.3 枚举

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `pci_host_probe()` | `pci.h:1133` | `probe.c:3117`（`_GPL`） | ⚠️⚠️ **默认入口** |
| `pci_scan_root_bus_bridge()` | `pci.h:1140` | `probe.c:3218` | ⚠️ 需要自己接着分配资源时用 |
| `pci_create_root_bus()` | `pci.h:1130` | `probe.c:3084`（`_GPL`） | ⚠️ 更底层，很少直接用 |
| `pci_scan_root_bus()` | `pci.h:1137` | `probe.c:3252` | 遗留形态 |
| `pci_scan_bus()` | `pci.h:1129` | `probe.c:3271` | ⚠️ 更老的遗留形态 |
| `pci_scan_child_bus()` | `pci.h:1155` | `probe.c:3035`（`_GPL`） | 递归扫一棵子树 |
| `pci_scan_slot()` | — | `probe.c:2724` | 扫一个 slot 的 8 个 function |
| `pci_scan_single_device()` | — | `probe.c:2621` | 扫一个 function |
| `pci_scan_bridge()` | — | `probe.c:1513` | ⚠️ 递归下探桥 |
| `pci_add_new_bus()` | — | `probe.c:1188` | 建一条子总线 |
| `pci_alloc_dev()` | — | `probe.c:2352` | 只分配 `pci_dev`，不注册 |
| `pci_bus_read_dev_vendor_id()` | — | `probe.c:2437` | ⚠️ "这个 devfn 上有设备吗" |
| `no_pci_devices()` | — | `probe.c:84` | 全系统一个 PCI 设备都没有？ |
| `pci_bus_add_device()` | `pci.h:1156` | `bus.c:351`（`_GPL`） | ⚠️ **单个设备走 bus 匹配** |
| `pci_bus_add_devices()` | `pci.h:1128` | `bus.c:380` | ⚠️⚠️ **这一步之后 probe 才会被调** |
| `pci_hp_add_bridge()` | — | `probe.c:3393`（`_GPL`） | 热插拔加桥 |
| `pcie_relaxed_ordering_enabled()` | — | `probe.c:2153` | — |

⚠️⚠️ **"扫到" ≠ "驱动 probe 了"**：`pci_scan_child_bus()` 只是把设备对象建好、
挂进 `pci_bus_type`，**真正触发匹配和 probe 的是 `pci_bus_add_devices()`**。
⇒ **这个两阶段拆分是资源分配能插在中间的原因**（03 §2 全流程）。

### §10.4 资源分配与地址换算

| 接口 | 声明 | 导出 | 粒度 |
|---|---|---|---|
| `pci_assign_unassigned_bus_resources()` | `pci.h:1451` | `setup-bus.c:2341`（`_GPL`） | ⚠️⚠️ **一条根总线（控制器驱动用这个）** |
| `pci_assign_unassigned_bridge_resources()` | `pci.h:1450` | `setup-bus.c:2221`（`_GPL`） | 一个桥下面 |
| `pci_assign_unassigned_resources()` | `pci.h:1449` | ⚠️ 未导出 | 全系统（`pci_init` 用） |
| `pci_assign_unassigned_root_bus_resources()` | `pci.h:1452` | ⚠️ 未导出 | — |
| `pci_bus_size_bridges()` | `pci.h:1446` | `setup-bus.c:1332` | ⚠️ **算桥窗口要多大** |
| `pci_bus_assign_resources()` | `pci.h:1444` | `setup-bus.c:1415` | ⚠️ **把算好的窗口写下去** |
| `pci_bus_claim_resources()` | `pci.h:1445` | `setup-bus.c:1482` | ⚠️⚠️ **认领固件已经分好的**，不重新分 |
| `pci_claim_resource()` | `pci.h:1447` | `setup-res.c:172` | 单个 BAR 认领 |
| `pci_assign_resource()` | `pci.h:1371` | `setup-res.c:368` | 单个 BAR 分配（`__must_check`） |
| `pci_release_resource()` | `pci.h:1373` | `setup-res.c:423` | — |
| `pci_resize_resource()` | `pci.h:1383` | `setup-res.c:476` | ⚠️ Resizable BAR |
| `pci_rebar_get_possible_sizes()` | — | `pci.c:3781` | 配合上一个 |
| `pci_bus_alloc_resource()` | `pci.h:1504` | `bus.c:274` | 从总线资源池里切一段 |
| `pci_add_resource()` | `pci.h:1484` | `bus.c:39` | ⚠️ 建 bridge 的 `windows` 列表 |
| `pci_add_resource_offset()` | `pci.h:1485` | `bus.c:33` | ⚠️⚠️ **带偏移的窗口 = 总线地址 ≠ CPU 地址** |
| `pci_free_resource_list()` | `pci.h:1487` | `bus.c:45` | — |
| `pci_bus_resource_n()` | `pci.h:1490` | `bus.c:77`（`_GPL`） | 按序号取总线资源 |
| `devm_request_pci_bus_resources()` | — | `bus.c:141`（`_GPL`） | — |
| `pci_flags` | — | `setup-bus.c:29`（`_GPL`） | ⚠️ 全局分配策略标志 |

**地址换算（arm64 上必读）**：

| 接口 | 声明 | 导出 | 方向 |
|---|---|---|---|
| `pcibios_resource_to_bus()` | `pci.h:1122` | `host-bridge.c:68` | ⚠️ CPU 地址 → 总线地址 |
| `pcibios_bus_to_resource()` | `pci.h:1124` | `host-bridge.c:101` | ⚠️ 总线地址 → CPU 地址 |
| `pci_find_parent_resource()` | — | `pci.c:821` | 一个 BAR 属于哪个上游窗口 |
| `pci_find_resource()` | — | `pci.c:845` | — |
| `pci_pio_to_address()` | `pci.h:1518` | `pci.c:4236`（`_GPL`） | ⚠️ I/O 端口号 → 物理地址 |
| `pci_remap_iospace()` | `pci.h:1519` | `pci.c:4283` | ⚠️⚠️ **arm64 上 PCI I/O 空间是"映射出来的"** |
| `devm_pci_remap_iospace()` | `pci.h:1520` | `pci.c:4340` | — |
| `devm_pci_remap_cfgspace()` | `pci.h:1523` | `pci.c:4370` | ⚠️ 配置空间专用（禁 write-combining） |
| `devm_pci_remap_cfg_resource()` | `pci.h:1526` | `pci.c:4429` | ⚠️ **控制器驱动映射自己的 ECAM 窗口用这个** |
| `pci_common_swizzle()` | — | `pci.c:3970`（`_GPL`） | INTx 引脚在桥上的旋转规则 |

⚠️⚠️ **`pci_add_resource_offset()` 那个 offset 是 arm64 的核心区别**：
x86 上 PCI 总线地址通常等于 CPU 物理地址（offset = 0），
**arm64 SoC 上两者往往不同** ⇒ DT 的 `ranges` 给出的就是这个偏移，
⇒ **所以 §3.4 那条"不要直接读配置空间里的 BAR"在 arm64 上是硬约束，不是建议。**

⚠️ **`devm_pci_remap_cfgspace()` 不是 `devm_ioremap()` 的别名**：
配置空间必须是**强序、不可合并**的映射，`pci.c:4370` 单独存在就是为了保证这一点。
⇒ **控制器驱动拿 `devm_ioremap()` 映射 ECAM 窗口是一类隐蔽错误。**

### §10.5 拆除与重扫

| 接口 | 声明 | 导出 |
|---|---|---|
| `pci_stop_and_remove_bus_device()` | `pci.h:1167` | `remove.c:120` |
| `pci_stop_and_remove_bus_device_locked()` | `pci.h:1168` | `remove.c:128`（`_GPL`） |
| `pci_stop_root_bus()` | `pci.h:1169` | `remove.c:146`（`_GPL`） |
| `pci_remove_root_bus()` | `pci.h:1170` | `remove.c:173`（`_GPL`） |
| `pci_remove_bus()` | — | `remove.c:64` |
| `pci_rescan_bus()` | `pci.h:1432` | `probe.c:3317`（`_GPL`） |
| `pci_lock_rescan_remove()` | `pci.h:1433` | `probe.c:3329`（`_GPL`） |
| `pci_unlock_rescan_remove()` | `pci.h:1434` | `probe.c:3335`（`_GPL`） |

⚠️⚠️ **控制器驱动的 `remove()` 必须是 `pci_stop_root_bus()` + `pci_remove_root_bus()`
两步**，顺序不能换：前者停驱动、后者拆对象。⚠️ 只调后者会在设备还在跑时释放它。

⚠️ **`pci_lock_rescan_remove()` 保护的是 `pci_rescan_remove_lock`**
（`drivers/pci/probe.c:3323`）—— 一切"改变 PCI 拓扑"的操作都要持它（03 §3.1）。

### §10.6 配置空间访问后端（`pci_ops`）

| 接口 | 导出 | 说明 |
|---|---|---|
| `pci_generic_config_read()` | `access.c:98`（`_GPL`） | ⚠️⚠️ **ECAM 控制器直接拿去填 `pci_ops`** |
| `pci_generic_config_write()` | `access.c:118`（`_GPL`） | — |
| `pci_generic_config_read32()` | `access.c:136`（`_GPL`） | ⚠️ 只支持 32 位访问的控制器用 |
| `pci_generic_config_write32()` | `access.c:176`（`_GPL`） | ⚠️ 读改写实现字节访问 |
| `pci_bus_set_ops()` | `access.c:196` | ⚠️ 运行期换掉访问后端（quirk 用） |

⚠️⚠️ **`*_config_read32`/`write32` 这一对是 arm64 上的高频选择**：
很多 SoC 的配置空间窗口不支持字节/半字访问，硬要按字节写会写坏整个 dword。
⇒ **控制器手册里出现"config space supports only 32-bit access"就用这一对**（06 §4）。

### §10.7 DT 解析（`of.c` 的 8 个）

| 接口 | 声明 | 导出 | 说明 |
|---|---|---|---|
| `of_pci_find_child_device()` | `include/linux/of_pci.h:12` | `of.c:149`（`_GPL`） | 按 devfn 找 DT 子节点 |
| `of_pci_get_devfn()` | `of_pci.h:14` | `of.c:171`（`_GPL`） | 从 `reg` 解出 devfn |
| `of_pci_check_probe_only()` | `of_pci.h:15` | `of.c:252`（`_GPL`） | ⚠️⚠️ **`linux,pci-probe-only`** |
| `of_irq_parse_and_map_pci()` | `of_pci.h:32` | `of.c:540`（`_GPL`） | ⚠️ INTx 的 `interrupt-map` 解析 |
| `of_pci_parse_bus_range()` | `drivers/pci/pci.h:611` | `of.c:197`（`_GPL`） | `bus-range` 属性 |
| `of_get_pci_domain_nr()` | `drivers/pci/pci.h:612` | `of.c:227`（`_GPL`） | ⚠️ `linux,pci-domain` |
| `of_pci_get_max_link_speed()` | `drivers/pci/pci.h:613` | `of.c:629`（`_GPL`） | `max-link-speed` |
| `of_pci_get_slot_power_limit()` | `drivers/pci/pci.h:614` | `of.c:699`（`_GPL`） | `slot-power-limit-milliwatt` |

⚠️⚠️ **后四个的声明在 `drivers/pci/pci.h`（私有头）里，却是 `EXPORT_SYMBOL_GPL` 的** ——
⇒ **外部模块拿不到声明，实际只有 `drivers/pci/` 内部能用。**
⚠️ 这是"导出了但不算对外接口"的典型，99 §2 记了这条口径差。

### §10.8 MSI 的控制器侧

| 接口 | 导出 | 说明 |
|---|---|---|
| `pci_msi_create_irq_domain()` | `msi/irqdomain.c:189`（`_GPL`） | ⚠️ 控制器建自己的 MSI 域 |
| `pci_msi_mask_irq()` / `pci_msi_unmask_irq()` | `msi/msi.c:104` / `:116`（`_GPL`） | ⚠️ 给 irqchip 回调用 |
| `pci_write_msi_msg()` | `msi/msi.c:223`（`_GPL`） | 把 MSI 地址/数据写进设备 |
| `pci_restore_msi_state()` | `msi/msi.c:324`（`_GPL`） | 复位/恢复后重写 |

⚠️ arm64 上 MSI 通常由 **GIC ITS** 提供域，控制器驱动多数**不需要**
`pci_msi_create_irq_domain()`（DT 里 `msi-parent` 指向 ITS 即可，06 §4）。
⇒ ⚠️⚠️ **需要它的是那些自带 MSI 控制器的老 SoC**（如部分 dwc 变体）。

---

## §11 返回值口径：三套并存

⚠️⚠️ **这是读 PCI 代码最容易出错的一处**：同一个子系统里有三种返回值约定。

| 约定 | 出现在 | 成功值 | 失败值 | 判据 |
|---|---|---|---|---|
| ① 标准 errno | 绝大多数 | `0` | ⚠️ **负数** | `if (ret)` / `if (ret < 0)` |
| ② `PCIBIOS_*` | ⚠️ **配置空间访问**（§4.4） | `PCIBIOS_SUCCESSFUL` = 0 | ⚠️⚠️ **正数** | ⚠️ `if (ret)`，**不能用 `< 0`** |
| ③ 偏移/计数 | 能力查找、向量分配 | ⚠️ **正数** | ⚠️ 能力查找返回 **0**；向量分配返回**负数** | 分函数看 |

⚠️ **③ 内部还分两种，必须分清**：

| 函数 | 失败返回 |
|---|---|
| `pci_find_capability()` / `pci_find_ext_capability()` | ⚠️ **0**（不是负数） |
| `pci_alloc_irq_vectors()` | ⚠️ **负数**（`-ENOSPC` 等） |
| `pci_irq_vector()` | ⚠️ 负数 |
| `pci_select_bars()` | ⚠️⚠️ **返回 BAR 位掩码；0 表示"一个都没有"，也是合法值** |

**核心层实际用到的错误码分布**（口径与已知误差见 99 §2）：

| 错误码 | 出现次数 | 典型来源 |
|---|---|---|
| `-EINVAL` | **76** | ⚠️ 参数校验（远超本套其它子系统） |
| `-ENOTTY` | 31 | ⚠️⚠️ **"设备不支持这个操作"** —— PCI 特有的用法 |
| `-ENOMEM` | 29 | 分配失败 |
| `-ENODEV` | 17 | 设备不存在/已消失 |
| `-EIO` | 15 | 访问失败 |
| `-ENOSPC` | 11 | ⚠️ 资源/向量不够 |
| `-EBUSY` | 9 | 资源已被占用 |
| `-EAGAIN` | 9 | ⚠️ `pci_try_*` 系列拿不到锁 |
| `-ENOTSUPP` | 7 | ⚠️ 与 `-ENOTTY` 语义重叠，两种写法并存 |
| `-ENOENT` | 4 | — |
| `-EEXIST` | 3 | — |
| `-ERANGE` / `-EOVERFLOW` / `-ENODATA` | 2 each | — |
| `-EPROBE_DEFER` / `-ENXIO` / `-ENOSYS` | 1 each | — |

⚠️⚠️ **`-ENOTTY` 31 次是 PCI 的特色**：在别处 `-ENOTTY` 意思是"不是 tty/ioctl 不支持"，
**在 PCI 里它被当作"这个设备/桥没有这项能力"**（复位方式探测、能力查询）。
⇒ **看到 PCI 返回 `-ENOTTY` 不要去查 ioctl，那是"不支持"。**

⚠️ `-ENOTSUPP`（7 次）与 `-ENOTTY` 语义重叠但取值不同（`-ENOTSUPP` 是 `524`，
不在 uapi errno 表里）⇒ ⚠️⚠️ **它不能传给用户态**，
传出去用户看到的是一个无意义的数字。**新代码别用它。**

---

## §12 一页速查

**设备驱动最小骨架用到的接口，按调用顺序**：

```c
module_pci_driver(my_driver);                   /* pci.h:1555 */
  → probe(pdev, id)
      pcim_enable_device(pdev);                 /* pci.h:1308 */
      pci_set_master(pdev);                     /* pci.h:1333 */
      dma_set_mask_and_coherent(&pdev->dev, …); /* ⚠️ pci.rst:274-285 要求 */
      pcim_iomap_regions(pdev, BIT(0), name);   /* pci.h:2200 */
      base = pcim_iomap_table(pdev)[0];         /* pci.h:2199 */
      n = pci_alloc_irq_vectors(pdev, 1, want,
              PCI_IRQ_ALL_TYPES);               /* pci.h:1985 */
      irq = pci_irq_vector(pdev, 0);            /* pci.h:1637 */
      request_irq(irq, h, IRQF_SHARED, …);      /* ⚠️ pci.rst:311-315 */
  → remove(pdev)
      /* 反序；⚠️ pci_release_region() 要在 disable 之后，pci.rst:247-249 */
```

⚠️⚠️ **别用清单（本篇出现过的全部）**：

| 别用 | 用 | 出处 |
|---|---|---|
| `pci_enable_msi()` / `pci_disable_msi()` | `pci_alloc_irq_vectors()` / `pci_free_irq_vectors()` | §5.5 |
| `pci_enable_msix_range()` / `_exact()` / `pci_disable_msix()` | 同上 | §5.5 |
| `pci_msi_vec_count()` / `pci_msix_vec_count()` | 看 `pci_alloc_irq_vectors()` 返回值 | §5.5 |
| `pci_find_device()` / `pci_find_subsys()` / `pci_find_slot()` | `pci_get_*` 系列 | §8.2 |
| `pci_get_slot()` | `pci_get_domain_bus_and_slot()` | §8.2 |
| `pci_driver.suspend` / `.resume` | `.driver.pm` | §2.2 |
| 直接读配置空间的 BAR | `pci_resource_start()` | §3.4 |
| `pcie_set_mps()` | ⚠️ **不要调**，交给核心 | §9.1 |
| `dev->pcie_cap` | `pci_find_capability()` | §4.1 |
| `pci_find_capability()` 找扩展能力 | ⚠️ `pci_find_ext_capability()` | §4.3 |
| `-ENOTSUPP` | `-ENOTTY` 或 `-EOPNOTSUPP` | §11 |
| `devm_ioremap()` 映射配置空间 | `devm_pci_remap_cfgspace()` | §10.4 |
| `struct msix_entry` | ⚠️ 它只服务废弃 API | §5.5 |
| 用总线号/槽号认设备 | `pci_name()` + ID 匹配 | §8.2 |

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 三分钟版的全景 | 00-overview.md |
| `pci_dev` 那 130+ 个字段、`pci_bus`、`pci_host_bridge` | 02-datastructures.md |
| 枚举与资源分配的完整流程、`pci_bus_type` 的 13 个回调 | 03-internals.md |
| AER / ASPM / DPC / PME / SR-IOV 这些扩展 | 04-extensions.md |
| 我要写一个 PCIe 设备驱动 | 05-usage.md |
| DT 怎么写、arm64 控制器有哪些 | 06-devicetree.md |
| sysfs / proc / uapi 能看到什么 | 07-debug.md |
| 为什么 PCI 是本套唯一的真总线型 | 08-feynman.md |
| 本篇每个数字的口径与推导 | 99-evidence.md |
