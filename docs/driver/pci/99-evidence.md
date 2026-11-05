---
title: 取证附录
---

# PCI 子系统文档集：取证与未验证项登记

这一篇不讲 PCI，讲**前九篇的每个数字是怎么来的、哪些没验证、哪些验证失败了**。

读法：想质疑某条结论就来这里查它的等级；想复现某个数字就抄这里的命令。全部命令的工作目录都是内核树根 `/workspace/safety/linux-6.1.y`。

---

## §1 基线与范围

### §1.1 树的状态：`drivers/pci` 全干净

本项目所在的树有大量本地改动（`git status --porcelain | wc -l` 在三位数），所以每套文档动笔前都要先确认自己读的那几个目录没被改过：

```console
$ git status --porcelain drivers/pci include/linux/pci.h include/linux/pci-ecam.h \
      include/uapi/linux/pci.h include/uapi/linux/pci_regs.h Documentation/PCI
（无输出）
```

⇒ **PCI 相关的全部文件都与 `v6.1.158` 标签一致。** 这一点比 tty 那套省事：tty 核心层外围有 14 个脏文件需要逐个排除。

### §1.2 基线（全部可重跑）

```console
$ git log -1 --format='%H %s'
f6e38ae624cf7eb96fb444a8ca2d07caa8d9c8fe Linux 6.1.158

$ find drivers/pci -name '*.c' | wc -l          # 171
$ find drivers/pci -name '*.h' | wc -l          #  20
$ find drivers/pci \( -name '*.c' -o -name '*.h' \) -exec cat {} + | wc -l
140042
```

架构口径：**arm64**。所有"某属性在 dts 里出现几次"的统计只扫 `arch/arm64/boot/dts`。

### §1.3 子系统类型判定：两道门

前二十一个子系统都是 provider/consumer 型。判定 PCI 属于哪一类，用的是两个可执行判据：

**门一：有没有注册 `bus_type`？**

```console
$ grep -rn 'struct bus_type .* = {' drivers/pci
drivers/pci/pci-driver.c:1685:struct bus_type pci_bus_type = {
drivers/pci/pci-driver.c:1724:struct bus_type pcie_port_bus_type = {
```

两条。这在本项目做过的 23 个子系统里是唯一的 —— 其余 22 个都是 `class` 或纯全局表。

**门二：设备是注册进来的还是扫出来的？**

```console
$ grep -n 'pci_scan_single_device\|pci_scan_slot' drivers/pci/probe.c | head -4
```

命中 `drivers/pci/probe.c:2603`（`pci_scan_single_device`）与 `drivers/pci/probe.c:2691`（`pci_scan_slot`）。设备来自读硬件，不来自 dts 节点或驱动调用。

⇒ 两道门都过 ⇒ PCI 是**总线型**，文档骨架必须以"枚举 → 分配资源 → 绑驱动"为主轴，而不是 provider/consumer 那套"注册表 + phandle 解析"。这决定了 03 篇的组织方式，也是 08 §1 那一问的出处。

### §1.4 官方文档登记

`Documentation/PCI/` 共 10 篇 2491 行，全部读过，逐篇登记：

| 文件 | 行数 | 用在哪 | 等级 |
|---|---|---|---|
| `Documentation/PCI/pci.rst:1` | 578 | 01/05 的主要来源；卸载七步在 `:361-373` | 一等 |
| `Documentation/PCI/msi-howto.rst:1` | 287 | 05 §6、08 §6；DMA 顺序保证在 `Documentation/PCI/pci.rst:351-355` | 一等 |
| `Documentation/PCI/pci-error-recovery.rst:1` | 426 | 05 §10、08 §8 | 一等 |
| `Documentation/PCI/sysfs-pci.rst:1` | 138 | 07 §1/§3；legacy 段 `:98-117` | 一等 |
| `Documentation/PCI/pci-iov-howto.rst:1` | 171 | 04 的 SR-IOV 节 | 二等 |
| `Documentation/PCI/pcieaer-howto.rst:1` | 300 | 04 的 AER 节、07 §6 | 二等 |
| `Documentation/PCI/pciebus-howto.rst:1` | 220 | 04 的 portdrv 节、08 §2 | 二等 |
| `Documentation/PCI/acpi-info.rst:1` | 192 | ⚠️ 裁掉（§2.7） | 只读了标题与结论段 |
| `Documentation/PCI/boot-interrupts.rst:1` | 159 | ⚠️ 裁掉：x86 专属（IOAPIC） | 三等 |
| `Documentation/PCI/index.rst:1` | 20 | 只用来核对篇目清单 | —— |

另外读过但归到"三等"的：`Documentation/PCI/endpoint/pci-endpoint.rst:1`(231 行) 与 `Documentation/PCI/endpoint/pci-ntb-function.rst:1`(348 行)，理由见 00 §9 第 3 条（端点模式视角相反）。

`Documentation/ABI/testing/sysfs-bus-pci:1`(503 行) 与 `Documentation/ABI/testing/sysfs-bus-pci-devices-aer_stats:1`(119 行) 是 07 篇属性表的权威来源，逐行读过。

### §1.5 官方文档与代码的不一致（六条，全部实测）

这一节是**验证失败的记录**，不是抱怨。写文档时如果照抄官方原文，这六处都会错。

| # | 官方原文 | 实际 | 后果 |
|---|---|---|---|
| 1 | `Documentation/PCI/pci.rst:48` 与 `:185` 提到 `pci_allocate_coherent()` | 树里**不存在**这个函数（`grep -rn 'pci_allocate_coherent' .` 无命中） | 照抄会写出编译不过的示例；05 篇改用 `dma_alloc_coherent()` |
| 2 | `Documentation/PCI/pci.rst:452-458` 建议用 `pcibios_strerror` | 树里**不存在** | 05 §11 改成"直接看返回的 errno" |
| 3 | `Documentation/PCI/pci.rst:466-468` 讲扩展能力的找法 | 与 `pci_find_ext_capability()` 的实际语义不符 | 01 §6 按代码写，并在此登记 |
| 4 | `Documentation/PCI/pci.rst:273-278` 说 DMA mask 的设法 | 与 `include/linux/pci.h` 里 `dma_mask` 字段上方的注释互相矛盾 | 05 §5 按 `dma_set_mask_and_coherent()` 的实现写 |
| 5 | `Documentation/PCI/sysfs-pci.rst:66` 脚注称某类文件"rw for `IORESOURCE_IO` only" | 与 `drivers/pci/pci-sysfs.c:1215` 的 `pci_create_attr()` 实际建文件条件对得上，但脚注没说 `arch_can_pci_mmap_io()` 这道门（`drivers/pci/pci-sysfs.c:1238-1241`） | 07 §3.2 补上这道门，否则 arm64 上的读者会找不到文件 |
| 6 | `Documentation/PCI/pci.rst:413-414` 自称"several mature drivers didn't get this right" | 属实，但没说哪几步 `devm_*` 管不了 | 05 §9.5 补出"停硬件、释放中断向量"这两步 |

⇒ 通用结论：**`Documentation/PCI/pci.rst` 的 API 名字不能照抄**。它成文早于 `dma_*` 统一 API，里面残留了 `pci_*` 时代的名字。

---

## §2 阅读范围与证据等级

`drivers/pci` 有 140042 行。逐行读完不现实，所以分三级，并明确登记各自的边界。

### §2.1 一等证据：逐行读过

| 文件 | 行数 | 用在 |
|---|---|---|
| `drivers/pci/pci-driver.c:1` | 1747 | 两个 `bus_type`、匹配与 probe 派发（03 §5、08 §1/§2） |
| `drivers/pci/of.c:1` | 699 | 全部 dts 解析（06 篇） |
| `drivers/pci/pci-sysfs.c:1` | 1706 | 40 个属性与 12 个 group（07 篇） |
| `drivers/pci/proc.c:1` | 472 | procfs 面（07 §7） |
| `drivers/pci/syscall.c:1` | 137 | 系统调用面（07 §8） |
| `drivers/pci/ecam.c:1` | 233 | ECAM 通用实现（06 §2） |
| `drivers/pci/host-bridge.c:1` | 101 | `pci_host_bridge` 访问器（02 §4） |
| `drivers/pci/controller/pci-host-common.c:1` | 100 | arm64 代表实现（06 §2.4） |
| `drivers/pci/controller/pci-host-generic.c:1` | 96 | 同上（06 §2.3） |
| `include/linux/pci.h:1` | 2597 | 全部结构与内联（01/02 篇） |
| `include/linux/pci-ecam.h:1` | 98 | 06 §2.2 |
| `include/uapi/linux/pci.h:1` | 42 | 07 §7.3 的 4 个 ioctl |
| `drivers/pci/msi/legacy.c:1` | 80 | 08 §6 的两条写消息路径 |

合计约 8100 行。

### §2.2 二等证据：读了关键函数与全部结构定义

| 文件 | 行数 | 读了什么 |
|---|---|---|
| `drivers/pci/pci.c:1` | 6998 | 使能/电源/复位/能力查找/`pci_setup()` 参数表（`:6913-6979`）/`reset_method` 属性（`:5276-5394`）/`resource_alignment`（`:6761-6806`） |
| `drivers/pci/probe.c:1` | 3393 | `pci_host_probe()` `:3086-3117`、`pci_scan_*` 四个入口、`pcibus_class_init` `:107-109`、`pci_setup_device` 附近 |
| `drivers/pci/setup-bus.c:1` | 2341 | 两遍法主循环 `:2039-2076`、`__pci_bus_size_bridges()` `:1199-1225`、`pbus_size_io()` `:874`、`pbus_size_mem()` `:985` |
| `drivers/pci/msi/msi.c:1` | 1157 | `__pci_write_msi_msg()` `:151-223`、`free_msi_irqs()` `:225`、屏蔽两函数 `:104`/`:116` |
| `drivers/pci/access.c:1` | 596 | 锁与 `pci_user_*` 宏（`:239`/`:257`） |
| `drivers/pci/pcie/aer.c:1` | 1489 | `aer_stats` 全部属性与两层可见性 `:589-621` |
| `drivers/pci/iov.c:1` | 1234 | 全部 SR-IOV sysfs 属性 `:221-562` |
| `drivers/pci/vpd.c:1` | 596 | 读写与二进制属性 `:155-308` |
| `drivers/pci/slot.c:1` | 383 | `pci_slots_kset` 与槽位属性 `:15-376` |
| `drivers/pci/setup-res.c:1` | 522 | BAR 写回与 `pci_resize_resource()` |
| `drivers/pci/bus.c:1` | 457 | `pci_bus_add_devices()` 一路 |
| `drivers/pci/remove.c:1` | 173 | 拆除顺序 |
| `drivers/pci/search.c:1` | 392 | `pci_get_*` 家族 |
| `drivers/pci/pci.h:1` | 808 | 私有声明，用来判"这个导出外部能不能用" |

### §2.3 三等证据：只做了符号级统计，没有逐行读

| 范围 | 文件数 | 行数 | 文档里的处理 |
|---|---|---|---|
| `drivers/pci/hotplug/` | 35 | 25232 | 04 篇只讲框架接口，不讲任何具体 hotplug 驱动 |
| `drivers/pci/controller/`（除两个代表） | 38 | ≈33500 | 06 §7.2 只统计 dwc 的 config 数量，不逐个读 |
| `drivers/pci/endpoint/` | 4 | 2427 | 明确裁掉（00 §9） |
| `drivers/pci/switch/switchtec.c:1` | 1915 | 1915 | 04 篇一句话提及 |
| `drivers/pci/pcie/aspm.c:1` | 1419 | 1419 | 04 篇讲策略与参数，不讲状态机 |
| `drivers/pci/pcie/dpc.c:1` | 409 | 409 | 04 篇讲它与 AER 的关系 |

⚠️ **这一档的结论都带"按符号统计"字样。** 例如 06 §7.2 说 dwc 有 40 个 config，那是数 `drivers/pci/controller/dwc/Kconfig` 里的 `config` 关键字，不是读过 40 个驱动。

### §2.4 代表实现的选法

arm64 主机桥有 40 个 `.c`（`drivers/pci/controller/*.c`），选 `pci-host-generic` 做代表，判据可执行：

```console
$ grep -rl 'pci-host-ecam-generic' arch/arm64/boot/dts | wc -l    # 7
$ grep -rl 'device_type = "pci"' arch/arm64/boot/dts | wc -l      # 71
```

⇒ 纯 ECAM 的板子只有 7 个，71 个 PCI 节点里绝大多数是厂商控制器。所以 06 篇的结构是"**先用 `pci-host-generic` 讲清最小骨架（`drivers/pci/controller/pci-host-common.c:52`），再用 dwc 讲厂商控制器多出来的东西**"，而不是只讲一个。

第二代表选 dwc 的判据：`drivers/pci/controller/dwc/` 有 29 个 `.c`，是最大的一族。

### §2.5 裁掉的范围登记

| 裁掉 | 规模 | 理由 |
|---|---|---|
| x86 专属参数与路径 | `Documentation/admin-guide/kernel-parameters.txt:4186-4200` 一整段 | arm64 无意义 |
| PCI Endpoint 框架 | `drivers/pci/endpoint/` 2427 行 + 8 篇 rst | 视角相反（SoC 当设备） |
| 具体 hotplug 驱动 | `drivers/pci/hotplug/` 25232 行 | 04 篇只要框架 |
| `drivers/pci/pcie/aer_inject.c:1` 的注入细节 | 500 余行 | 07 §6.3 只说"它只伪造软件状态" |
| ACPI 全部路径 | 见 §2.7 | arm64-only 口径 |

### §2.6 与"相邻层"的边界怎么划的

三条硬边界，写进各篇的"本篇边界"节：

1. **driver core 那一侧不讲**。`pci_bus_type` 的 `.probe` 被谁调用、`device_lock` 怎么实现，都属于 driver core。PCI 篇只写"挂上去就免费得到什么"（08 §1）。
2. **irqchip/ITS 那一侧不讲**。06 §5 写到 `msi-map` 怎么被解析、映射错的症状是什么，但 GIC ITS 的 DeviceID 表怎么建不写。
3. **iommu 那一侧不讲**。只写 `bus_type.dma_configure` 挂着 `pci_dma_configure`（`drivers/pci/pci-driver.c:1698`）这个事实和它的后果。

### §2.7 ACPI 的裁剪登记

arm64 上服务器固件走 ACPI、嵌入式走 dts。本套按 dts 写，以下全部裁掉，逐项登记，方便将来做 ACPI 版时接上：

| 裁掉的东西 | 规模/位置 | 被裁掉后文档里怎么说 |
|---|---|---|
| `drivers/pci/pci-acpi.c:1` | 1518 行 | 完全不提；06 篇明说"主机桥的全部信息来自 dts" |
| `drivers/pci/pcie/edr.c:1` | 246 行 | 04 篇提一句"EDR 依赖 ACPI，本套不覆盖" |
| `include/linux/pci-ecam.h:80-91` 的 10 个 `pci_ecam_ops` 外部声明 | 被 `#if defined(CONFIG_ACPI) && defined(CONFIG_PCI_QUIRKS)` 包住（`include/linux/pci-ecam.h:80`） | 06 §2.2 只讲 `pci_generic_ecam_ops`（`include/linux/pci-ecam.h:78`） |
| `aer_recover_queue()` / `cper_severity_to_aer()` 的调用方 | 在 `drivers/pci/pcie/aer.c` 内，由 GHES 驱动调 | 04 篇的 AER 只讲原生中断那条路 |
| ASPM 的 DMI 黑名单 | `drivers/pci/pcie/aspm.c:1419` 文件内 | 04 篇只讲 `pcie_aspm=` 参数（`drivers/pci/pcie/aspm.c:1400`） |
| `d3cold_allowed` 属性 | `drivers/pci/pci-sysfs.c:594`，数组里被 `CONFIG_PM && CONFIG_ACPI` 包住（`drivers/pci/pci-sysfs.c:659-661`） | ⚠️ 07 §5.2 明说"无 ACPI 时这个文件不存在"，因为这是现场最容易误判的一个 |
| `Documentation/PCI/acpi-info.rst:1` | 192 行 | §1.4 里标"只读了结论段" |

⚠️ **裁剪不等于假装不存在**：上表第 6 行那种"文件在别人机器上有、在你机器上没有"的情况，必须在正文里写出来，否则读者会以为文档错了。

---

## §3 符号存在性核查

### §3.1 核查口径（两级，全套统一）

| 说法 | 判据 |
|---|---|
| "**存在**" | `grep` 在树里有命中 |
| "**外部驱动可用**" | 有 `EXPORT_SYMBOL*` **并且** 在 `include/linux/` 下的某个头里有声明 |

两级分开，因为 PCI 里"有导出但用不上"的情况特别多，具体三类：

1. **只在私有头里声明**。`drivers/pci/of.c` 的 4 个导出（`pci_parse_request_of_pci_ranges` 等）声明在 `drivers/pci/pci.h:611-614`，那个头不在 `include/` 下 ⇒ 外部模块拿不到原型。01 篇把它们标成"仅树内可用"。
2. **头文件在 `drivers/` 里**。portdrv 的几个导出声明在 `drivers/pci/pcie/portdrv.h:1`(145 行)，同理不可用。
3. **是 inline，根本没有符号**。`pci_alloc_irq_vectors()` 是 `include/linux/pci.h:1985-1990` 的内联，`Module.symvers` 里查不到 ⇒ ⚠️ **"驱动最常用的函数不在导出表里"这件事必须在 01/05 篇写明**，否则读者会以为文档漏了。

### §3.2 ⚠️ 抓到的计数口径问题：`pci_dev` 到底几个字段

02 篇的核心数字是"`pci_dev` 有 134 个字段"。这个数字第一次数出来是 **125**，两次差 9，追出来的原因值得记下来 —— **它是所有"数结构体字段"任务的通用坑**。

第一版脚本用的是"逐行去掉 `/* ... */` 后看是不是以 `;` 结尾"：

```python
s = re.sub(r'/\*.*?\*/', '', line).strip()
if depth == 1 and s.endswith(';'): n += 1
```

⚠️ 这个正则**只能去掉同一行内闭合的注释**。`include/linux/pci.h` 里大量字段的行尾注释跨两行：

```c
	unsigned int	ltr_path:1;	/* Latency Tolerance Reporting
					   supported from root to here */
```

第一行去掉不了那个没闭合的 `/*`，`s` 不以 `;` 结尾 ⇒ 整个字段被漏掉。`pci_dev` 里这样的字段有 7 个。

第二个问题是**匿名 union**。`include/linux/pci.h:506-509` 有：

```c
#ifdef CONFIG_PCI_ATS
	union {
		struct pci_sriov	*sriov;		/* PF: SR-IOV info */
		struct pci_dev		*physfn;	/* VF: related PF */
	};
```

union 本身没名字（深度 1 上没有可数的成员），两个成员在深度 2。按"深度 1 成员"数是 0，按"驱动能写出名字的成员"数是 2。

修好注释状态机后的权威数字：

```console
$ # 跨行注释用状态机剥掉，再数 depth==1 且以 ; 结尾的行
$ # 结果：include/linux/pci.h:330-538
depth-1 成员 = 132   其中位域 = 62   被 #if* 包住 = 25
```

⇒ **132 + 匿名 union 的 2 个成员 = 134，25 + 2 = 27** ⇒ 02 篇的"134 个字段（107 无条件 + 27 条件）、62 个位域"三个数字**全部正确**，口径是"驱动能写出名字的成员"。

同一把脚本核对其余五个结构，全部与 02 篇一致：

| 结构 | 范围 | 字段数 | 位域 | 02 篇写的 |
|---|---|---|---|---|
| `struct pci_dev` | `include/linux/pci.h:330-538` | 134 | 62 | 134 ✓ |
| `struct pci_bus` | `include/linux/pci.h:652-686` | 26 | 2 | 26 ✓ |
| `struct pci_host_bridge` | `include/linux/pci.h:569-603` | 27 | 12 | 27 ✓ |
| `struct pci_driver` | `include/linux/pci.h:920-938` | 17 | 0 | 17 ✓ |
| `struct pci_ops` | `include/linux/pci.h:778-784` | 5 | 0 | 5 ✓ |
| `struct pci_error_handlers` | `include/linux/pci.h:842-859` | 6 | 0 | 6 ✓ |
| `struct pci_slot` | `include/linux/pci.h:75-81` | 5 | 0 | 5 ✓ |
| `struct pci_dynids` | `include/linux/pci.h:806-809` | 2 | 0 | 2 ✓ |

### §3.3 ⚠️ 抓到的第二个口径问题：`grep -oE '\-E[A-Z]+'` 的假命中

错误码普查（§6 表 6）第一版直接 `grep -rhoE '\-E[A-Z]+'`，结果里混进 4 类不是错误码的东西：

| 假命中 | 次数 | 真身 |
|---|---|---|
| `-ERRNO` | 4 | 注释里的 `An appropriate -ERRNO error value`（如 `drivers/pci/pci.c:4519`） |
| `-ECAM` | 1 | 注释里的 `non-ECAM-compliant ARM SoCs`（`drivers/pci/pci.h:795`） |
| `-EINVA` | 1 | 跨行断开的 `-EINVAL` |
| `-EPROBE` | 1 | `-EPROBE_DEFER` 被正则截断 |

另外 `-ENOTSUPP` 7 次里有一部分在注释里（如 `drivers/pci/pci.c:3726`），真返回点在 `drivers/pci/pci.c:3736`。

⇒ **纪律：错误码普查必须把注释行先剔掉，并且正则要允许 `_`**，否则 `-EPROBE_DEFER` 这类会被切成两半。§6 表 6 是剔除后的数字。

### §3.4 计数纪律（本套遵守的四条）

1. **`EXPORT_SYMBOL` 与 `EXPORT_SYMBOL_GPL` 分开数**：`grep -o 'EXPORT_SYMBOL\b'` 靠 `\b` 排除 `_GPL`（`_` 是词内字符，不构成边界）。
2. **宏生成的符号 grep 数不到**，要额外登记（§4）。
3. **行数一律 `wc -l`**，不去掉空行与注释，口径写在数字旁边。
4. **"某属性在 dts 里出现 N 次" 数的是文件数（`grep -rl | wc -l`）**，不是出现次数 —— 一个 dtsi 被多个板子 include 时按次数数会虚高。

---

## §4 索引工具（cbm）的盲区

本项目用 cbm 的代码索引做定位，PCI 上撞到四类盲区，全部靠 `rg` 复核：

| 盲区 | 例子 | 后果 |
|---|---|---|
| **宏生成的函数** | `drivers/pci/pci-sysfs.c:32-46` 的 `pci_config_attr` 一次生成 6 个属性（vendor / device / subsystem_vendor / subsystem_device / class / revision） | ⚠️ 搜 `vendor_show` **零命中**。07 §2.1 因此把宏原文贴出来 |
| **宏生成的导出** | `drivers/pci/access.c:239` / `:257` 生成 `pci_user_read_config_*` / `pci_user_write_config_*` 共 6 个导出 | 导出总数少算 6 个 |
| **宏生成的 sysfs 属性组** | `drivers/pci/pcie/aer.c:535-560` 的 `aer_stats_dev_attr` 生成 3 个、`:572-580` 的 rootport 版生成 3 个 | 07 §6.1 同样贴宏原文 |
| **函数指针间接调用** | `bus->ops->read()`（`include/linux/pci.h:782`）→ `pci_generic_config_read()` → `ops->map_bus()` | 调用图断在 `->read` 上，00 §6.2 那条路径是人工接的 |

⇒ **纪律（已在 `MEMORY.md` 里）：宏与间接调用一律 `rg` 复核后才写进文档。** PCI 是这条纪律代价最高的一个子系统 —— 07 篇有两处整节内容如果只靠索引就会写成"该属性不存在"。

---

## §5 未验证项（四层，从最不确定到最确定）

### §5.1 第一层：未编译、未运行

**全套文档没有编译过一行代码，没有在任何 arm64 硬件或 QEMU 上跑过。** 具体影响：

| 文档里的东西 | 风险 |
|---|---|
| 05 篇的驱动骨架代码 | ⚠️ 未编译。函数名与参数逐个对过头文件，但拼写与头文件包含没验证 |
| 07 篇的每条命令 | ⚠️ 未在真机执行。命令形式对着 `Documentation/ABI/testing/sysfs-bus-pci:1` 与属性的 `mode` 位写的 |
| 07 §11 的症状对照表 | ⚠️ 因果关系来自代码推理，不是现场复现 |
| 06 §7 的 rk3568 dts 解读 | 读的是 dts 与驱动代码，没有那块板子 |
| 03 篇的时序结论 | 来自 initcall 级别与函数调用顺序，不是 ftrace |

### §5.2 第二层：只读了符号或完全没读

§2.3 那一档的全部结论，加上以下三条具体的：

1. **dwc 的 40 个 config**（06 §7.2）：数的是 `drivers/pci/controller/dwc/Kconfig` 的 `config` 关键字，没读驱动。
2. **hotplug 框架接口**（04 篇）：只读了 `include/linux/pci_hotplug.h`，35 个驱动一个没读。
3. **ASPM 状态机**（04 篇）：只读了参数入口（`drivers/pci/pcie/aspm.c:1222` 的 `module_param_call`、`:1400` 的 `__setup`），`drivers/pci/pcie/aspm.c` 的 1419 行状态机没读。

### §5.3 ⚠️ 第三层：推断而非观察（六条，最需要警惕）

| # | 结论 | 推断链 | 怎么才能证实 |
|---|---|---|---|
| 1 | arm64 上 `pciconfig_read/write` 是死码（07 §8） | `drivers/pci/syscall.c:15` 定义了 `SYSCALL_DEFINE5`，但 arm64 的 syscall 表里没有对应项 | 编译后看 `arch/arm64/include/asm/unistd32.h` 与实际符号表 |
| 2 | arm64 无 `legacy_io` / `legacy_mem`（07 §3.5） | 建文件的代码在 `drivers/pci/pci-sysfs.c:1005-1029`，被 `HAVE_PCI_LEGACY` 包住，arm64 的 Kconfig 里没有 | 真机 `ls /sys/bus/pci/devices/*/` |
| 3 | `interrupt-map` 写错时表现为"`irq` 为 0 且无报错"（06 §6.3） | `drivers/pci/of.c:529` 的返回值语义 + `:536` 的注释 `Proper return code 0 == NO_IRQ` | 真机改坏 dts 复现 |
| 4 | `msi-map` 映射错时"向量分配成功但中断永不来"（06 §5、07 §2.6） | 分配路径不校验 DeviceID 的正确性，只校验域存在 | 真机复现 |
| 5 | dmesg 出现 `no space for` 后跟成功是正常的（00 §5.4、08 §5） | `drivers/pci/setup-bus.c:2061` 的 `again:` 多轮重试结构 | 真机看完整 dmesg |
| 6 | 非 root `lspci -vv` 看不到 Capabilities（07 §3.1） | `drivers/pci/pci-sysfs.c:721` 的 `size = 64` 加 `:726-727` 的 CAP 提权 | 真机以普通用户跑一次 |

⚠️ 这六条**全部是"某个现象的解释"**，代码依据都定位到行了，但"现象确实长这样"没有观察记录。用它们排查问题时，先确认现象本身。

### §5.4 第四层：版本时效性

`drivers/pci` 全部文件与 `v6.1.158` 一致（§1.1），但 6.1.y 是长期维护分支，**有从新版倒回来的补丁**，读代码时会看到不属于 6.1 时代的写法。已发现三处：

| 倒回来的东西 | 位置 | 表现 |
|---|---|---|
| `DEFINE_FREE(pci_dev_put, ...)` | `include/linux/pci.h:1165` | 6.11+ 的 `__free()` 清理机制 |
| `DEFINE_GUARD(pci_dev, ...)` | `include/linux/pci.h:1774` | 同上，配 `guard(pci_dev)` 用 |
| `pci_set_power_state_locked()` | 声明 `include/linux/pci.h:1411`、实现 `drivers/pci/pci.c:1508`、导出 `:1514`、空实现 `include/linux/pci.h:1917` | 是 6.1.158 里的新增，主线更晚 |

⇒ ⚠️ **后果**：如果你的树是较早的 6.1.y，这三个都可能不存在。01 篇引用它们时都注了"6.1.158 backport"。反过来，看到 `guard()` 这类写法**不能推断整棵树都有 cleanup.h 的全部设施**。

---

## §6 可重跑的数字

### 表 1 规模

| 口径 | 数字 | 命令 |
|---|---|---|
| `drivers/pci/` 的 `.c` | 171 | `find drivers/pci -name '*.c' \| wc -l` |
| `drivers/pci/` 的 `.h` | 20 | `find drivers/pci -name '*.h' \| wc -l` |
| `drivers/pci/` 总行数 | 140042 | `find drivers/pci \( -name '*.c' -o -name '*.h' \) -exec cat {} + \| wc -l` |
| 核心层 | **19966 行 / 19 个文件** | 见下方组成 |
| `include/linux/pci.h:1` | 2597 | `wc -l` |
| `include/uapi/linux/pci_regs.h:1` | 1134 | `wc -l` |
| `include/uapi/linux/pci.h:1` | 42 | `wc -l` |
| `include/linux/pci-ecam.h:1` | 98 | `wc -l` |
| `drivers/pci/pci.h:1`（私有） | 808 | `wc -l` |

⚠️ **"核心层 19966 行"的组成必须写清楚**，否则复现不出来：13 个通用 `.c`（`drivers/pci/pci.c:1` 6998 + `probe.c` 3393 + `setup-bus.c` 2341 + `pci-driver.c` 1747 + `of.c` 699 + `access.c` 596 + `setup-res.c` 522 + `bus.c` 457 + `search.c` 392 + `remove.c` 173 + `host-bridge.c` 101 + `irq.c` 76 + `setup-irq.c` 64 = 17559）+ `drivers/pci/pci.h` 808 + `drivers/pci/msi/` 5 个文件 1599 = **19966 行 / 19 个文件**。

不算进核心层的：用户态面 3 个文件（`pci-sysfs.c` 1706 + `proc.c` 472 + `syscall.c` 137）、`drivers/pci/ecam.c:1`(233)、`slot.c`(383)、`vpd.c`(596)、`iov.c`(1234)，以及全部子目录。

### 表 2 各子目录

| 目录 | `.c` 文件 | 行数 | 文档处理 |
|---|---|---|---|
| `drivers/pci/controller/` | 40 | 33740 | 两个代表逐行，其余符号级 |
| `drivers/pci/controller/dwc/` | 29 | （含在上一行） | 06 §7.2 只统计 Kconfig |
| `drivers/pci/hotplug/` | 35 | 25232 | 三等 |
| `drivers/pci/pcie/` | 11 | 6155 | AER 二等，其余三等 |
| `drivers/pci/endpoint/` | 4 | 2427 | 裁掉 |
| `drivers/pci/switch/` | 1 | 1915 | 三等 |
| `drivers/pci/msi/` | 4 + 1 头 | 1599 | 二等（`legacy.c` 一等） |

### 表 3 导出符号

| 口径 | 数字 | 命令 |
|---|---|---|
| `drivers/pci` 全树 `EXPORT_SYMBOL_GPL` | 254 | `grep -rho 'EXPORT_SYMBOL_GPL' drivers/pci --include='*.c' \| wc -l` |
| `drivers/pci` 全树 `EXPORT_SYMBOL` | 168 | `grep -rho 'EXPORT_SYMBOL\b' drivers/pci --include='*.c' \| wc -l` |
| **核心层 19 文件合计** | **232**（92 `_GPL` + 140 非 GPL） | 同上，限定 19 个文件 |

⚠️ **非 GPL 占 60%**，在本项目所有子系统里比例最高。原因是历史：PCI 的接口早于 `EXPORT_SYMBOL_GPL` 的普及。⚠️ 这也意味着**不能用"是不是 `_GPL`"判断一个接口新旧或是否推荐**。

按文件分布（`grep -c 'EXPORT_SYMBOL' <file>`）：

| 文件 | 导出数 | 文件 | 导出数 |
|---|---|---|---|
| `drivers/pci/pci.c` | 103 | `drivers/pci/bus.c` | 10 |
| `drivers/pci/access.c` | 29 | `drivers/pci/pci-driver.c` | 9 |
| `drivers/pci/probe.c` | 26 | `drivers/pci/of.c` | 8 |
| `drivers/pci/iov.c` | 10 | `drivers/pci/search.c` | 8 |
| `drivers/pci/vpd.c` | 8 | `drivers/pci/setup-bus.c` | 7 |
| `drivers/pci/slot.c` | 5 | `drivers/pci/remove.c` | 5 |
| `drivers/pci/setup-res.c` | 4 | `drivers/pci/host-bridge.c` | 4 |
| `drivers/pci/ecam.c` | 4 | `drivers/pci/irq.c` | 2 |
| `drivers/pci/setup-irq.c` | 0 | —— | —— |

⚠️ `drivers/pci/access.c` 的 29 个里有 6 个是宏生成的（§4），grep 数得到是因为宏展开处也写了 `EXPORT_SYMBOL`；真正 grep 不到的是**函数名**。

### 表 4 用户态 ABI

| 口径 | 数字 | 依据 |
|---|---|---|
| sysfs 属性宏（`DEVICE_ATTR` / `BUS_ATTR` / `BIN_ATTR`） | 33 处 | `grep -cE 'DEVICE_ATTR\|BUS_ATTR\|BIN_ATTR' drivers/pci/pci-sysfs.c` |
| ⚠️ 宏生成的额外属性 | +6 | `drivers/pci/pci-sysfs.c:32-46` 的 `pci_config_attr` |
| `attribute_group` 定义（`drivers/pci/*.c` + `pcie/*.c`） | 19 | `grep -rho 'struct attribute_group [a-z_]* = {' \| wc -l` |
| `pci_dev_attr_groups[]` 里挂的组 | 12 | `drivers/pci/pci-sysfs.c:1686` |
| procfs ioctl | 4 | `include/uapi/linux/pci.h:36-40` |
| 系统调用 | 2 | `drivers/pci/syscall.c:15`（read）与 write 版，⚠️ arm64 不注册 |
| `Documentation/ABI/testing/sysfs-bus-pci:1` 条目 | 约 40 | 逐条对照，见 07 §5.2 |

### 表 5 错误码普查（核心 19 文件，已剔除注释与假命中）

| errno | 次数 | 主要含义 |
|---|---|---|
| `-EINVAL` | 76 | 参数不合法（含 BAR 号越界、能力 ID 非法） |
| `-ENOTTY` | 31 | ⚠️ **全部 31 次都在 `drivers/pci/pci.c`**，主要来自复位方式探测：这块设备不支持这种复位 |
| `-ENOMEM` | 29 | 分配失败 |
| `-ENODEV` | 18 | 设备不在了 / 桥不存在 |
| `-EIO` | 15 | 配置空间访问失败（含 `drivers/pci/syscall.c:49-51` 把一切错误压成 `-EIO`） |
| `-ENOSPC` | 11 | 资源分配放不下 |
| `-EBUSY` | 9 | 已使能 / 资源已被占（如 `drivers/pci/pci-sysfs.c:331-332`） |
| `-EAGAIN` | 9 | 重试（两遍法多轮） |
| `-ENOTSUPP` | 7 | 特性不支持（如可变 BAR，`drivers/pci/pci.c:3736`） |
| 其余 | ≤4 各 | `-ENOENT` 4 / `-EEXIST` 3 / `-ERANGE` 2 / `-EOVERFLOW` 2 / `-ENODATA` 2 / `-ENXIO` 1 / `-ENOSYS` 1 |

⚠️ **`-ENOTTY` 是 PCI 的特色错误码**：在别的子系统里它只出现在 ioctl 分发，在 PCI 里它表示"这块硬件不支持你要的操作"。看到 `pci_reset_function()` 返回 `-ENOTTY` 要查 `reset_methods[]`（07 §4.3），不是查 ioctl。

### 表 6 打印与设计债（核心 19 文件）

| 宏 | 次数 | 说明 |
|---|---|---|
| `pci_info` | 76 | 最多，含资源分配结果 |
| `pci_err` | 33 | —— |
| `pci_dbg` | 22 | ⚠️ 默认不打，开法见 07 §9.2 |
| `pci_warn` | 18 | —— |
| `dev_info` / `dev_err` / `dev_warn` / `dev_dbg` | 17 / 9 / 7 / 8 | ⚠️ **混用**：核心层里 41 处用 `dev_*` 而不是 `pci_*`，说明 `pci_*` 宏（`include/linux/pci.h:2576-2586`）不是历史悠久的统一约定 |
| `pr_err` / `pr_info` | 4 / 5 | ⚠️ 无设备上下文，多在启动参数解析（`drivers/pci/pci.c:6913-6979`）里 |
| `BUG_ON` | 5 | ⚠️ 全在核心不变量上 |
| `WARN_ON` | 9 | —— |

### 表 7 全树实例统计（说明"这套接口有多少用户"）

| 口径 | 数字 | 命令 |
|---|---|---|
| `struct pci_driver X = {` 实例 | 525（分布在 510 个文件） | `grep -rho 'struct pci_driver [a-z_]* = {' --include='*.c' . \| wc -l` |
| `module_pci_driver()` | 426 | 同法 |
| `struct pci_ops X = {` 实例 | 69 | 同法 |
| `struct pci_ecam_ops X = {` 实例 | 12 | 同法 |
| **`struct pci_error_handlers X = {` 实例** | **49** | 同法 |
| `pcim_enable_device()` 调用方 | 247 个文件 | `grep -rl ... \| wc -l` |
| `pci_alloc_irq_vectors()` 调用方 | 142 个文件 | 同法 |

⚠️⚠️ **最值得记住的一个比例：525 个 `pci_driver` 里只有 49 个实现了 `pci_error_handlers`（9%）。** 这解释了 08 §8 最后那条 —— 错误恢复的返回值是"合议制"，一棵子树里只要有一个驱动没实现，整条恢复流程就退化。**不是所有驱动都参与，是绝大多数都不参与。**

### 表 8 arm64 与设备树（`grep -rl ... arch/arm64/boot/dts | wc -l`，数文件）

| 键 | 文件数 | 用在 |
|---|---|---|
| `device_type = "pci"` | 71 | 06 §1.1 的总量口径 |
| `num-lanes` | 67 | 06 §7.2（dwc 属性） |
| `iommu-map` | 23 | 06 §5.2 |
| `linux,pci-domain` | 22 | 06 §4.1 |
| `max-link-speed` | 19 | 06 §7.1 |
| `msi-map` | 12 | 06 §5.1 |
| `pci-host-ecam-generic` | 7 | §2.4 的代表选法 |
| **`linux,pci-probe-only`** | **0** | ⚠️⚠️ 06 §4.3、08 §4 的关键结论：arm64 dts 世界里没人用固件分配 |

---

## §7 三个批量自检脚本的结果

三个脚本都在 `~/.claude/skills/kernel-subsystem-docs/scripts/` 下。

**1. `check_line_refs.py`（行号引用核查）**

```console
$ python3 ~/.claude/skills/kernel-subsystem-docs/scripts/check_line_refs.py output/pci .
扫描 1160 条唯一 file:line 引用（消歧表 61 个基名 / 24 个前缀目录，忽略项 0 条）
== AMBIGUOUS（1 个简写文件名，树里有多个候选）==
  pci.h → 按 include/linux/pci.h 校验；另有 drivers/pci/pci.h, include/uapi/linux/pci.h
干净：没有解析不到的文件，也没有越界行号。
```

⚠️ 那条 AMBIGUOUS **是接受的**：`pci.h` 这个基名在树里有三个候选，本套的规则是"**指私有头或 uapi 头时一律写全路径**"（如 `drivers/pci/pci.h:611-614`、`include/uapi/linux/pci.h:36-40`），裸 `pci.h` 只在 `include/linux/pci.h` 已经在同段里写过全路径之后作为缩写出现。1160 条引用中越界 0 条、解析不到 0 条。

写作过程中这个脚本抓到过两次真错误，都在收尾时修掉：`Documentation/PCI/pci-endpoint.rst` 与 `pci-ntb-function.rst` 的真实路径带 `endpoint/` 目录（`Documentation/PCI/endpoint/pci-endpoint.rst:1`）。

**2. `check_xrefs.py`（篇间交叉引用）**

最终结果：**10 篇，跨篇引用命中 272 条，失效 0 条**。被引用到的本篇小节：§1.1 §1.3 §2 §2.1 §2.7 §3 §4 §5 §7 —— 全部存在。

⚠️ 这个脚本抓到过三条真错误，收尾时修掉：01 篇两处、02 篇一处把"内部实现篇讲锁的那节"写成了第 3 章下的小节号（`§3.2` / `§3.3`），而 03 篇的锁在 **§7**（`§7.1` raw spinlock / `§7.3` 持锁睡眠 / `§7.4` rescan 锁），它的 §3 是 `pci_setup_device()`，且根本没有 `§3.2`/`§3.3` 这两个小节。**教训：跨篇节号只能在目标篇定稿后回填，凭记忆写必错。**

**3. `check_doc_coverage.sh`（官方文档反向覆盖度）**

```console
$ bash .../check_doc_coverage.sh pci output/pci .
=== 【必查】概念/框架文档 ===
  p2pdma        引用 3 次
  pci           引用 3512 次
=== 【参考】DT 绑定：共 43 篇，被引用 2 篇 ===
  host-generic-pci（9 次）
  snps,dw-pcie（1 次）
【必查】共 2 篇，其中 0 篇 0 引用。
```

必查档全绿。DT 绑定档 43 篇只引用 2 篇 —— 按脚本注释的口径这是**预期的**：单控制器绑定不逐篇文档化，06 篇的硬要求是"每条属性都有定位到行的消费者"，那一条在 06 §7 逐条落实了。

**4. 星号与加粗自检**

| 篇 | 行数 | 实心五角星 | 加粗对 | 比值 |
|---|---|---|---|---|
| 00-overview.md | 439 | 0 | 36 | 0.082 |
| 01-api.md | 1045 | 0 | 242 | ⚠️ 0.232 |
| 02-datastructures.md | 894 | 0 | 212 | ⚠️ 0.237 |
| 03-internals.md | 685 | 0 | 51 | 0.074 |
| 04-extensions.md | 671 | 0 | 47 | 0.070 |
| 05-usage.md | 756 | 0 | 85 | 0.112 |
| 06-devicetree.md | 855 | 0 | 68 | 0.080 |
| 07-debug.md | 936 | 0 | 48 | 0.051 |
| 08-feynman.md | 629 | 0 | 75 | 0.119 |

⚠️ 01 与 02 的加粗比值 0.232 / 0.237 逼近 0.25 的上限（表格密集的篇天然偏高，因为每行都想标一个关键短语）。**登记在此，不做返工**：这两篇是查表型文档，加粗集中在"⚠️ 这里有坑"的短语上，没有整格加粗的情况。

---

## §8 逐篇取证表

| 篇 | 主要一等证据 | 主要推断项（§5.3） | 已知薄弱处 |
|---|---|---|---|
| 00-overview.md | 其余九篇 | 无（纯浓缩，不新增结论） | 数字全部转引，口径见本篇 §6 |
| 01-api.md | `include/linux/pci.h:1`、`drivers/pci/pci.c` 的导出点 | —— | ⚠️ 232 个导出没逐个读实现；分组依据是头文件里的注释分节 |
| 02-datastructures.md | `include/linux/pci.h:330-938` | —— | 字段分组是人工分的十组，不是内核的正式分类 |
| 03-internals.md | `drivers/pci/probe.c`、`setup-bus.c`、`pci-driver.c` | 第 5 条（多轮重试的 dmesg 表现） | ⚠️ 时序结论无 ftrace 支撑（§5.1） |
| 04-extensions.md | `drivers/pci/pcie/aer.c`、`iov.c` | —— | ⚠️ ASPM/DPC/hotplug 三块是三等证据 |
| 05-usage.md | `Documentation/PCI/pci.rst:1`、`msi-howto.rst:1` | —— | ⚠️ 骨架代码未编译（§5.1） |
| 06-devicetree.md | `drivers/pci/of.c:1`、两个代表控制器 | 第 3、4 条 | ⚠️ rk3568 那节无硬件（§5.1） |
| 07-debug.md | `pci-sysfs.c:1`、`proc.c:1`、`syscall.c:1`、两份 ABI 文档 | 第 1、2、6 条 | ⚠️ 每条命令未在真机执行 |
| 08-feynman.md | 引用其余篇的已核对结论 | 全篇的类比都是解释性的 | 类比的"什么时候会误导你"节是自审，不是外部验证 |

---

## §9 交叉验证实例：初始假设被推翻的七条

这一节记录写作过程中**先写错、后被证据推翻**的地方。价值在于：这七个坑下一个子系统还会遇到。

### 1. `pci_dev` 的字段数

第一版数出 125，与 02 篇要写的分组合计对不上。追出两个口径问题（跨行注释、匿名 union），修正后 134 —— 详细过程在 §3.2。**结论：结构体字段数这类"看起来最简单"的数字，是最容易错的一类。**

### 2. `pci_alloc_irq_vectors()` 以为是导出函数

按"驱动最常用"的直觉，先假设它是 `drivers/pci/msi/msi.c` 里的导出函数。`grep -rn 'EXPORT_SYMBOL.*pci_alloc_irq_vectors'` **零命中**，翻头文件才发现是 `include/linux/pci.h:1985-1990` 的内联包装（内部调 `pci_alloc_irq_vectors_affinity()`）。⇒ 01 篇为此单列一条"inline 不占导出名额"的说明。

### 3. sysfs 的 `vendor` / `device` 属性以为不存在

`grep -rn 'vendor_show' drivers/pci/` 零命中，第一反应是"这两个属性由 driver core 提供"。实际是 `drivers/pci/pci-sysfs.c:32-46` 的 `pci_config_attr` 宏一次生成 6 个。⇒ 07 §2.1 把宏原文贴出来，并写明"搜不到函数名是正常的"。

### 4. 以为 `Documentation/PCI/pci.rst` 的 API 名可以直接抄

`pci_allocate_coherent()`（`Documentation/PCI/pci.rst:48`、`:185`）与 `pcibios_strerror`（`:452-458`）在树里都不存在。⇒ §1.5 的六条不一致由此展开，05 篇全部改用现行 `dma_*` API。

### 5. 以为 `of_irq_parse_and_map_pci()` 失败返回负值

按内核惯例先写成"检查返回值 < 0"。读 `drivers/pci/of.c:529` 才看到 `:536` 的注释 `Proper return code 0 == NO_IRQ` —— **失败返回 0**。⇒ 06 §6.3 改写成"`interrupt-map` 写错的症状是 `irq` 为 0 且完全没有报错"，这是 06 篇最实用的一条。

### 6. 以为 arm64 上会有板子用 `linux,pci-probe-only`

先假设服务器/嵌入式里总有几块板子接受固件分配的地址。`grep -rl 'linux,pci-probe-only' arch/arm64/boot/dts | wc -l` = **0**。⇒ 08 §4 的整节从"两种模式各有适用场景"改写成"arm64 上只有一种模式，另一种是给别的架构留的"。

### 7. 以为核心层是 17 个文件

00 §1 与 §4.1 第一版写"19966 行 / 17 个文件"。19966 这个数字是对的（13 个 `.c` + `pci.h` + `msi/` 5 个），但**文件数按表格行数（15 行）估的，`msi/` 那一行含 5 个文件**。⇒ 收尾核对时改成 19 个文件，并在表格上方注明组成。**教训：行数与文件数要分开数，不能从表格行数倒推。**

---

## §10 对照与回写

### §10.1 rule-29 独立对照：本子系统**没有对照套**

`output/qoder/` 下只有 clk、dma、gpio、irq、pinctrl、spi 六套，**没有 pci**。所以规则 29 要求的"与独立生成的另一套做逐节对比"这一步**无法执行**，登记为「**无对照套**」，PROGRESS 表里同样记「无」。

⚠️ 代价要说清楚：**本套 PCI 文档缺少一次外部视角的交叉检查**，§5.3 那六条推断项没有第二个来源印证。使用时优先信有行号的事实陈述，对"症状 → 原因"这类因果链保留怀疑。

### §10.2 要回写 skill 的四条

1. **结构体字段数必须用带跨行注释状态机的脚本数**，并明确"匿名 union 的成员算不算"。第一版正则会稳定少算 5%–7%（§3.2）。
2. **错误码普查要先剔注释行，正则要允许 `_`**，否则 `-ERRNO` / `-EPROBE_DEFER` 这类会污染统计（§3.3）。
3. **"核心层 N 个文件"要与行数分开数**，不能从表格行数倒推（§9 第 7 条）。
4. **官方 `.rst` 里的 API 名一律要 grep 验证存在性**再写进文档 —— PCI 这套里踩到 2 个不存在的函数名（§1.5）。

### §10.3 明确不吸收的两条

1. **不因为"总线型子系统只有一个"就给它单开一套骨架。** 现有十篇骨架（01 API / 02 结构 / 03 内部 / 04 扩展 / 05 用法 / 06 dts / 07 调试 / 08 直觉 / 00 总览 / 99 取证）在 PCI 上完全够用，只是各篇的重心不同（03 篇变成"枚举+分配"，06 篇变成"只描述主机桥"）。
2. **不把 `Documentation/` 的不一致回写成"官方文档质量差"的通用结论。** PCI 的 `pci.rst` 有历史包袱是它自己的情况，别的子系统（如 `Documentation/driver-api/` 下较新的几篇）与代码吻合得很好。**逐套现场核对，不做跨子系统的推广。**
