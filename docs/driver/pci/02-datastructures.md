---
title: 数据结构
---

# pci 02：数据结构

Linux 6.1.158 · arm64 · 只读源码树 `/workspace/safety/linux-6.1.y`

⚠️ 本篇只讲结构布局与字段语义。接口签名在 01 篇，运行期流程在 03 篇。

---

## §1 六个结构，先分清谁是谁

| 结构 | 位置 | 字段数 | 谁分配 | 谁填 |
|---|---|---|---|---|
| `struct pci_dev` | `include/linux/pci.h:330-538` | ⚠️⚠️ **134**（107 无条件 + 27 条件） | 核心（`pci_alloc_dev`） | ⚠️ **核心**，驱动只读 |
| `struct pci_bus` | `include/linux/pci.h:652-686` | **26**（25 + 1 条件） | 核心 | 核心 |
| `struct pci_host_bridge` | `include/linux/pci.h:569-603` | **27** | ⚠️ 核心分配，控制器驱动填 | ⚠️ **控制器驱动** |
| `struct pci_driver` | `include/linux/pci.h:920-938` | **17** | ⚠️⚠️ **驱动作者静态定义** | 驱动 |
| `struct pci_ops` | `include/linux/pci.h:778-784` | **5** | 控制器驱动静态定义 | 控制器驱动 |
| `struct pci_error_handlers` | `include/linux/pci.h:842-859` | **6** | 驱动静态定义 | 驱动 |

⚠️⚠️ **判据一句话**：**上三个是核心的私产（驱动只读），下三个是驱动交给核心的表。**
⇒ 写设备驱动只需要填 `pci_driver`（+ 可选 `pci_error_handlers`），
写控制器驱动才碰 `pci_host_bridge` 和 `pci_ops`。

**对象关系**：

```
   pci_host_bridge  ──bus──▶  pci_bus (root)  ──devices──▶  pci_dev
        │  .ops                    │  .ops ↑继承             │  .bus ↑
        │  .windows                │  .children              │  .subordinate
        │  .sysdata                │  .parent                │     └─▶ pci_bus (子)
        └─ private[]（控制器私有数据，pci_host_bridge_priv()）
                                                              │  .driver ─▶ pci_driver
                                                              │  .dev ─▶ struct device
                                                              └─ .resource[17]
```

⚠️ **`pci_dev` 与 `pci_bus` 是双向的**：`dev->bus` 指向"我在哪条总线上"，
桥设备的 `dev->subordinate` 指向"我下面那条总线"，
而那条总线的 `bus->self` 又指回这个桥设备（`pci.h:333` / `:657`）。
⇒ **`bus->self == NULL` 通常意味着根总线，但不完全可靠**，见 §3.2。

---

## §2 `struct pci_dev`：134 个字段

⚠️⚠️ **这是本套二十二个子系统里最大的单个结构**。134 个字段里
**62 个是 `:1` 位域**（`pci.h:331-537` 范围内），
真正需要驱动关心的**不到 10 个**。

⚠️ 按用途分成十组来读，就不会被 134 个名字压垮。

### §2.1 组① 拓扑与平台钩子（5 + 2）

| 字段 | 行 | 说明 |
|---|---|---|
| `struct list_head bus_list` | `:331` | 挂在 `bus->devices` 上 |
| `struct pci_bus *bus` | `:332` | ⚠️ **我在哪条总线上** |
| `struct pci_bus *subordinate` | `:333` | ⚠️ **桥才有：我下面那条总线** |
| `struct pci_slot *slot` | `:337` | 物理槽位（`pci.h:75-81`） |
| `struct device dev` | `:411` | ⚠️⚠️ **驱动模型入口**，`to_pci_dev()` 反向（`pci.h:551`） |
| `void *sysdata` | `:335` | ⚠️ 体系/控制器私有钩子 |
| `struct proc_dir_entry *procent` | `:336` | `/proc/bus/pci` 条目（07 §4） |

⚠️ **`sysdata` 是"从 `pci_host_bridge.sysdata` 一路继承下来"的**：
控制器驱动放进 bridge 的东西，最终在每个 `pci_dev` 上都取得到。
⚠️⚠️ **但现代 arm64 控制器驱动不该用它** —— 用 `pci_host_bridge_priv()`
（`pci.h:607-610`）+ `pci_find_host_bridge()` 更清楚（01 §10.2）。

### §2.2 组② 设备身份（8）

| 字段 | 行 | 来自配置空间 |
|---|---|---|
| `unsigned int devfn` | `:339` | ⚠️ **编码后的 device+function**，用 `PCI_SLOT()`/`PCI_FUNC()` 拆 |
| `unsigned short vendor` | `:340` | `0x00` |
| `unsigned short device` | `:341` | `0x02` |
| `unsigned short subsystem_vendor` | `:342` | `0x2c` |
| `unsigned short subsystem_device` | `:343` | `0x2e` |
| `unsigned int class` | `:344` | ⚠️ **三字节**：base / sub / prog-if |
| `u8 revision` | `:345` | class 字的低字节 |
| `u8 hdr_type` | `:346` | ⚠️ **multi 位已被核心掩掉** |

⚠️ **这八个就是 `pci_device_id` 匹配用的全部输入**（§8）。
⚠️⚠️ **`devfn` 里没有总线号** —— 要唯一标识一个设备需要
`pci_dev_id(dev)`（`pci.h:690-693`，`bus->number << 8 | devfn`），
再加上 domain 才是全系统唯一（arm64 多控制器上 domain 必需，01 §8.2）。

### §2.3 组③ 能力偏移缓存（10）

| 字段 | 行 | 缓存的是 |
|---|---|---|
| `u32 devcap` | `:355` | PCIe Device Capabilities |
| `u8 pcie_cap` | `:356` | ⚠️⚠️ **PCIe 能力结构的偏移**（0 = 不是 PCIe） |
| `u8 msi_cap` | `:357` | MSI 能力偏移 |
| `u8 msix_cap` | `:358` | MSI-X 能力偏移 |
| `u8 pcie_mpss:3` | `:359` | Max Payload Size Supported |
| `u8 rom_base_reg` | `:360` | 控制 ROM 的配置寄存器号 |
| `u8 pin` | `:361` | ⚠️ INTx 引脚（A/B/C/D，`pci.h:146-152`） |
| `u16 pcie_flags_reg` | `:362` | ⚠️ 缓存的 PCIe Capabilities Register |
| `u8 pm_cap` | `:378` | PM 能力偏移 |
| `u16 acs_cap` | `:525` | ACS 能力偏移 |

⚠️⚠️ **这一组是"为什么不能自己读 `PCI_CAPABILITY_LIST`"的答案**：
核心在枚举时就把常用能力的偏移缓存下来了。
⇒ **`pci_pcie_cap()` / `pci_find_capability()` 走的是缓存或封装，
自己走链表既慢又要处理各种 quirk**（01 §4.1）。

⚠️ **`pcie_flags_reg` 是三个高频 inline 的数据源**：

```c
/* include/linux/pci.h:2347-2368 —— 节选 */
static inline bool pci_is_pcie(struct pci_dev *dev)
{ return pci_pcie_cap(dev); }              /* :2347-2350，全树 70 个文件用 */

static inline u16 pcie_caps_reg(const struct pci_dev *dev)
{ return dev->pcie_flags_reg; }            /* :2356-2359 */

static inline int pci_pcie_type(const struct pci_dev *dev)
{ return (pcie_caps_reg(dev) & PCI_EXP_FLAGS_TYPE) >> 4; }   /* :2365-2368 */
```

⚠️ **`pci_pcie_type()` 返回的是端口类型**（Endpoint / Root Port /
Upstream Port / Downstream Port …）—— 03 篇的枚举流程靠它判断"要不要往下扫"。

### §2.4 组④ DMA（3 + 1）

| 字段 | 行 | 说明 |
|---|---|---|
| `u64 dma_mask` | `:366-370` | ⚠️ **不要直接写**，用 `dma_set_mask_and_coherent()` |
| `struct device_dma_parameters dma_parms` | `:372` | 段长/边界约束 |
| `unsigned long *dma_alias_mask` | `:363` | ⚠️ 这个设备会用哪些别名 devfn 发 DMA（quirk 用） |
| `unsigned int driver_managed_dma`（在 `pci_driver` 里） | `:937` | 见 01 §2.2 |

⚠️⚠️ **`dma_mask` 字段上的注释（`:366-370`）说"通常是 `0xffffffff`，
只有设备有 DMA 缺陷或支持 64 位时才需要改"** ——
⚠️ **这句话对 PCIe 设备已经过时**：官方文档要求
**所有 PCI-X / PCIe 驱动必须显式调 `dma_set_mask()`**
（`Documentation/PCI/pci.rst:280-281`，01 §3.6）。
⇒ **文档与头注释在这一点上互相矛盾，按文档做**（99 §2）。

### §2.5 组⑤ 电源管理（15）

| 字段 | 行 | 类型 |
|---|---|---|
| `pci_power_t current_state` | `:374-376` | ⚠️ **当前 D 状态** |
| `unsigned int imm_ready:1` | `:377` | 支持 Immediate Readiness |
| `unsigned int pme_support:5` | `:379-380` | ⚠️ **哪些 D 状态能发 PME#**（位掩码，不是 `:1`） |
| `unsigned int pme_poll:1` | `:381` | 需要轮询 PME 状态位 |
| `unsigned int d1_support:1` / `d2_support:1` | `:382` / `:383` | 支持 D1 / D2 |
| `unsigned int no_d1d2:1` | `:384` | ⚠️ quirk：禁 D1/D2 |
| `unsigned int no_d3cold:1` | `:385` | 禁 D3cold |
| `unsigned int bridge_d3:1` | `:386` | 允许桥进 D3 |
| `unsigned int d3cold_allowed:1` | `:387` | ⚠️ **用户通过 sysfs 控制**（07 §3） |
| `unsigned int mmio_always_on:1` | `:388-389` | BAR sizing 时不许关 io/mem 解码 |
| `unsigned int wakeup_prepared:1` | `:390` | — |
| `unsigned int skip_bus_pm:1` | `:391` | ⚠️ 核心内部标记 |
| `unsigned int d3hot_delay` | `:398` | ⚠️ **D3hot→D0 恢复毫秒数**（quirk 常改这个） |
| `unsigned int d3cold_delay` | `:399` | D3cold→D0 恢复毫秒数 |

⚠️ `pci_power_t` 的取值在 `pci.h:170-178`：
`PCI_D0`(0) · `PCI_D1`(1) · `PCI_D2`(2) · `PCI_D3hot`(3) · `PCI_D3cold`(4) ·
`PCI_UNKNOWN`(5) · ⚠️ `PCI_POWER_ERROR`(-1)。
`pci_power_name()`（`pci.h:183-186`）把它变成字符串 ——
⚠️⚠️ **注意它下标是 `1 + state`**，因为 `-1` 也要能查表。

⚠️⚠️ **arm64 上这一组大半是死数据**：D 状态的实际切换要靠平台方法
（ACPI `_PS0`/`_PS3`），DT 系统上 `pci_platform_power_transition()` 是空转
⇒ **`current_state` 能被设成 `PCI_D3hot`，但真正的 D3cold 进不去**（01 §6）。

### §2.6 组⑥ 资源与中断（4）

| 字段 | 行 | 说明 |
|---|---|---|
| `int cfg_size` | `:413` | ⚠️ 配置空间大小（256 或 4096） |
| `unsigned int irq` | `:419` | ⚠️⚠️ **用它，不要读配置空间的 Interrupt Line** |
| `struct resource resource[DEVICE_COUNT_RESOURCE]` | `:420` | ⚠️⚠️ **17 项** |
| `struct resource driver_exclusive_resource` | `:421` | 驱动独占区间 |

⚠️ **`:415-418` 的注释是本结构里最重要的一句**：
"不要直接碰 interrupt line 和 base address 寄存器，用这里存的值 ——
**它们可能不一样**"。⇒ 01 §3.4 那条硬约束的原文就在这里。

⚠️⚠️ **`resource[17]` 的下标分配（`pci.h:95-133`）必须记住**：

| 下标 | 名字 | 数量 |
|---|---|---|
| 0–5 | `PCI_STD_RESOURCES` … `PCI_STD_RESOURCE_END` | ⚠️ **6 个标准 BAR** |
| 6 | `PCI_ROM_RESOURCE` | 扩展 ROM |
| 7–12 | ⚠️ `PCI_IOV_RESOURCES`（`#ifdef CONFIG_PCI_IOV`） | ⚠️⚠️ **6 个 SR-IOV BAR，配置相关** |
| 13–16 | `PCI_BRIDGE_RESOURCES` … `_END` | ⚠️ **4 个桥窗口** |
| — | `PCI_NUM_RESOURCES` = `DEVICE_COUNT_RESOURCE` | 总数 |

⚠️⚠️ **`resource[]` 的长度随 `CONFIG_PCI_IOV` 变**：
关掉 SR-IOV 时数组是 11 项而不是 17 项，桥窗口的下标也跟着前移。
⇒ **绝对不能写死数字下标**，用 `PCI_BRIDGE_RESOURCES + n` 这些宏
（`pci.h:110-118` 定义了 P2P 和 CardBus 两套窗口别名）。

⚠️ 桥窗口的三个语义别名在 `pci.h:110-112`：
`PCI_BRIDGE_IO_WINDOW` · `PCI_BRIDGE_MEM_WINDOW` · `PCI_BRIDGE_PREF_MEM_WINDOW`
—— ⚠️⚠️ **CardBus 用同样的 4 个槽位但语义不同**（`:115-118`），
所以读 `setup-bus.c` 时必须先看 `hdr_type`。

### §2.7 组⑦ 状态位与已使能标记（26）

| 字段 | 行 | 含义 |
|---|---|---|
| `bool match_driver` | `:423` | ⚠️ **false = 不自动绑驱动** |
| `unsigned int transparent:1` | `:425` | 减法解码桥 |
| `io_window:1` / `pref_window:1` / `pref_64_window:1` | `:426` / `:427` / `:428` | 桥有哪些窗口 |
| `multifunction:1` | `:429` | 多功能设备 |
| `is_busmaster:1` | `:431` | ⚠️ `pci_set_master()` 设的 |
| `no_msi:1` / `no_64bit_msi:1` | `:432` / `:433` | quirk 禁用 |
| `block_cfg_access:1` | `:434` | ⚠️ `pci_cfg_access_lock()` 设的（01 §4.5） |
| `msi_enabled:1` / `msix_enabled:1` | `:437` / `:438` | ⚠️⚠️ **两者互斥** |
| `ari_enabled` / `ats_enabled` / `pasid_enabled` / `pri_enabled` | `:439`–`:442` | IOMMU 相关 |
| `is_managed:1` | `:443` | ⚠️ **用了 `pcim_*` 版本** |
| `is_msi_managed:1` | `:444` | MSI 走 devres 释放 |
| `needs_freset:1` | `:445` | 需要 fundamental reset |
| `state_saved:1` | `:446` | 配置空间已保存 |
| `is_physfn:1` / `is_virtfn:1` | `:447` / `:448` | SR-IOV PF / VF |
| `is_hotplug_bridge:1` / `is_pciehp:1` | `:449` / `:450` | ⚠️ 语义差别见 `:322-329` 的 kerneldoc |
| `is_thunderbolt:1` | `:452` | — |
| `untrusted:1` | `:460` | ⚠️⚠️ **可能发起 DMA 攻击 ⇒ IOMMU 必须给完整映射**（`:453-459`） |
| `external_facing:1` | `:466` | ⚠️ **自己在内部，但下游设备是外部的**（`:461-465`） |
| `irq_managed:1` | `:469` | — |
| `is_probed:1` | `:471` | probe 进行中 |
| `link_active_reporting:1` | `:472` | 能上报链路 active |
| `pasid_no_tlp:1` / `eetlp_prefix_path:1` | `:407` / `:408` | — |
| `no_vf_scan:1` | `:473` | 使能 SR-IOV 后不扫 VF |

⚠️⚠️ **`untrusted`（`:460`）和 `external_facing`（`:466`）是这个结构里
唯一两条带完整散文注释的位域**（各 6 行 / 5 行）。
⇒ **它们是 Thunderbolt DMA 攻击那波安全工作留下的痕迹** ——
在 arm64 SoC 上通常都是 0，但读 IOMMU 代码时会遇到。

⚠️ **`is_managed`（`:443`）是"能不能混用两套 API"的判据**：
`pcim_enable_device()` 会把它置 1，之后 `pci_enable_device()` 的
反向操作语义就变了（01 §3.1）。

### §2.8 组⑧ quirk 与硬件缺陷标记（12）

| 字段 | 行 | 缺陷 |
|---|---|---|
| `broken_parity_status:1` | `:435` | 报假的奇偶校验错 |
| `irq_reroute_variant:2` | `:436` | ⚠️ 需要 IRQ 重路由（`pci.h:251-254` 两个变体） |
| `broken_intx_masking:1` | `:467` | ⚠️ **INTx 屏蔽不能用** |
| `io_window_1k:1` | `:468` | Intel 桥的 1K I/O 窗口 |
| `non_compliant_bars:1` | `:470` | ⚠️⚠️ **BAR 是坏的，直接忽略** |
| `no_command_memory:1` | `:474` | 没有 `PCI_COMMAND_MEMORY` 位 |
| `rom_bar_overlap:1` | `:475` | 关 ROM BAR 会出问题 |
| `clear_retrain_link:1` | `:396-397` | 要手动清 Retrain Link 位 |
| `ignore_hotplug:1` | `:392` | ⚠️ `pci_ignore_hotplug()` 设的（01 §9.3） |
| `hotplug_user_indicators:1` | `:393-395` | SlotCtl 指示灯只由 sysfs 控制 |
| `shpc_managed:1` | `:451` | SHPC 归 shpchp 管 |
| `pci_dev_flags_t dev_flags` | `:476` | ⚠️⚠️ **另外 13 个 quirk 标志**，见下 |

⚠️ **`dev_flags` 是位掩码而不是位域**，取值在 `pci.h:222-249`（13 个）：
`MSI_INTX_DISABLE_BUG`(1<<0) · `NO_D3`(1<<1) · `ASSIGNED`(1<<2) ·
`ACS_ENABLED_QUIRK`(1<<3) · ⚠️ **(1<<4) 空缺** · `PCIE_BRIDGE_ALIAS`(1<<5) ·
`NO_BUS_RESET`(1<<6) · `NO_PM_RESET`(1<<7) · `VPD_REF_F0`(1<<8) ·
`BRIDGE_XLATE_ROOT`(1<<9) · `NO_FLR_RESET`(1<<10) ·
`NO_RELAXED_ORDERING`(1<<11) · `HAS_MSI_MASKING`(1<<12) ·
`MSIX_TOUCH_ENTRY_DATA_FIRST`(1<<13)。

⚠️⚠️ **`NO_BUS_RESET` / `NO_PM_RESET` / `NO_FLR_RESET` 这三个决定
01 §7 的复位方式探测会跳过哪几种** ——
⇒ **"这块卡为什么 `pci_reset_function()` 返回 `-ENOTTY`"的答案常在这里。**

⚠️ **组⑦ + 组⑧ 合起来 38 个字段，其中 34 个是位域** ——
这解释了 62 个位域的绝大部分：
⇒ **`pci_dev` 之所以巨大，主因是"三十年的硬件缺陷都记在这里"，
而不是抽象设计复杂。**

### §2.9 组⑨ 保存/恢复与 sysfs（8）

| 字段 | 行 | 说明 |
|---|---|---|
| `atomic_t enable_cnt` | `:477` | ⚠️⚠️ **`pci_enable_device()` 的引用计数** |
| `spinlock_t pcie_cap_lock` | `:479` | ⚠️ 保护能力访问器里的读改写（01 §4.2） |
| `u32 saved_config_space[16]` | `:480` | 挂起时保存的 64 字节头 |
| `struct hlist_head saved_cap_space` | `:481` | 保存的各能力结构 |
| `int rom_attr_enabled` | `:482` | ROM sysfs 属性是否打开 |
| `struct bin_attribute *res_attr[]` | `:483` | `resource0..N` 文件 |
| `struct bin_attribute *res_attr_wc[]` | `:484` | `resourceN_wc` 文件 |
| `struct pci_vpd vpd` | `:499` | VPD 状态 |

⚠️⚠️ **`enable_cnt` 就是 `sysfs-pci.rst` 里"`enable` 文件是个计数器"的实体**
（07 §3）：`echo 1 > enable` 递增它，减到 0 也**不会**撤销所有初始化。
⚠️ **驱动侧的后果**：`pci_enable_device()` 调两次要 `pci_disable_device()` 两次。

⚠️ **`pcie_cap_lock`（`:479`）是 6.1.x 后期回合并进来的**：
它配合 01 §4.2 那对 `_locked`/`_unlocked` 访问器解决
"两个 CPU 同时读改写同一个 PCIe 能力寄存器"的竞争（99 §1.1）。

### §2.10 组⑩ 其余（5）

| 字段 | 行 | 说明 |
|---|---|---|
| `pci_channel_state_t error_state` | `:410` | ⚠️ **连通性状态**，见下 |
| `struct pci_driver *driver` | `:365` | 绑定的驱动 |
| `const char *driver_override` | `:528-532` | ⚠️⚠️ **不要直接赋值**，用 `driver_set_override()`（核心会 free 它） |
| `unsigned long priv_flags` | `:534` | 留给驱动的私有标志 |
| `u8 reset_methods[PCI_NUM_RESET_METHODS]` | `:536-537` | ⚠️ **按优先级排的复位方式索引**（7 项，`pci.h:53`） |

⚠️ `error_state` 只有三个取值（`pci.h:197-206`）：
`pci_channel_io_normal`(1) · `pci_channel_io_frozen`(2) ·
⚠️ `pci_channel_io_perm_failure`(3, 卡已死)。
判断用 inline：

```c
/* include/linux/pci.h:554-557 */
static inline int pci_channel_offline(struct pci_dev *pdev)
{
        return (pdev->error_state != pci_channel_io_normal);
}
```

⚠️⚠️ **AER/DPC 恢复流程就是围绕这个字段转的**（04 篇）：
`error_detected()` 回调收到的那个 `pci_channel_state_t` 参数
（`pci.h:844-845`）正是它。

### §2.11 27 个条件字段：按 `CONFIG_` 分栏

⚠️⚠️ **这一节的意义**：**你在自己的 `.config` 下看到的 `pci_dev` 布局
和别人的不一样** ⇒ 读到某个字段找不到时先查这张表。

| `CONFIG_` | 字段数 | 行 | 字段 |
|---|---|---|---|
| `PCIEAER` | 2 | `:348-349` | `aer_cap` · `aer_stats` |
| `PCIEPORTBUS` | 2 | `:352-353` | `rcec_ea` · `rcec` |
| `PCIEASPM` | 3 | `:402` `:403` `:405` | `link_state` · `ltr_path:1` · `l1ss` |
| `HOTPLUG_PCI_PCIE` | 1 | `:487` | `broken_cmd_compl:1` |
| `PCIE_PTM` | 4 | `:490-493` | `ptm_cap` · `ptm_root:1` · `ptm_enabled:1` · `ptm_granularity` |
| ⚠️ `PCI_MSI` | 2 | `:496-497` | `msix_base` · ⚠️⚠️ **`raw_spinlock_t msi_lock`** |
| `PCIE_DPC` | 3 | `:501-503` | `dpc_cap` · `dpc_rp_extensions:1` · `dpc_rp_log_size` |
| ⚠️ `PCI_ATS` | 4 | `:506-511` | ⚠️⚠️ **`union { sriov; physfn; }`** · `ats_cap` · `ats_stu` |
| `PCI_PRI` | 3 | `:514-516` | `pri_cap` · `pri_reqs_alloc` · `pasid_required:1` |
| `PCI_PASID` | 2 | `:519-520` | `pasid_cap` · `pasid_features` |
| `PCI_P2PDMA` | 1 | `:523` | `p2pdma`（`__rcu`） |

⚠️⚠️ **`CONFIG_PCI_ATS` 里那个 union（`:506-509`）是最容易读错的一处**：

```c
/* include/linux/pci.h:505-512 —— 节选 */
#ifdef CONFIG_PCI_ATS
        union {
                struct pci_sriov        *sriov;         /* PF: SR-IOV info */
                struct pci_dev          *physfn;        /* VF: related PF */
        };
```

⇒ **同一块内存在 PF 上是 `sriov`、在 VF 上是 `physfn`**，
判据是 `is_physfn` / `is_virtfn`（`:447-448`）。
⚠️ **访问 `physfn` 必须经过 inline 包装**：

```c
/* include/linux/pci.h:540-547 */
static inline struct pci_dev *pci_physfn(struct pci_dev *dev)
{
#ifdef CONFIG_PCI_IOV
        if (dev->is_virtfn)
                dev = dev->physfn;
#endif
        return dev;
}
```

⚠️ **注意它的守卫是 `CONFIG_PCI_IOV` 而字段的守卫是 `CONFIG_PCI_ATS`** ——
两个不同的 Kconfig 守着同一块内存。⇒ 这是一处真实的脆弱点（99 §2）。

⚠️ **`msi_lock` 是 `raw_spinlock_t`（`:497`）**，和
`access.c:15` 的 `pci_lock`（`DEFINE_RAW_SPINLOCK`）一样
—— ⇒ **MSI 掩码操作要在中断关闭的上下文里能跑**（03 §7.1）。

### §2.12 驱动真正会读的字段：只有这几个

| 字段 | 驱动能不能写 | 替代 |
|---|---|---|
| `dev->irq` | ⚠️⚠️ **只读**；用了 MSI 后**不要读它**，用 `pci_irq_vector()` | 01 §5.1 |
| `dev->vendor` / `device` / `revision` / `class` | 只读 | — |
| `dev->devfn` / `dev->bus->number` | 只读，且⚠️**不要用来认设备** | `pci_name()`（`pci.h:2047`，390 个文件） |
| `dev->dev`（`struct device`） | ⚠️ 传给 `dma_*` / `devm_*` / `dev_err()` | — |
| `dev->resource[i]` | ⚠️ **只读**，用 `pci_resource_start/len` 宏 | 01 §3.4 |
| `dev->error_state` | 只读，用 `pci_channel_offline()` | `pci.h:554` |
| `dev->msi_enabled` / `msix_enabled` | ⚠️ 只读；判"用了消息中断"用 `pci_dev_msi_enabled()`（`pci.h:735-738`） | — |
| `dev->driver_override` | ⚠️⚠️ **绝对不要直接写** | `driver_set_override()` |
| 其余 120+ 个 | ⚠️⚠️ **核心私产** | — |

⚠️ **驱动私有数据不放在 `pci_dev` 里**，用这一对（`pci.h:2037-2045`，全树 676 个文件）：

```c
pci_set_drvdata(pdev, priv);       /* 通常在 probe 末尾 */
struct my_priv *priv = pci_get_drvdata(pdev);
```

⚠️⚠️ **`priv_flags`（`:534`）虽然写着"private flags for the PCI driver"，
但它是 `unsigned long` 位标志，不是指针** —— 想放结构体指针只有 drvdata 一条路。

---

## §3 `struct pci_bus`：26 个字段

```c
/* include/linux/pci.h:652-686 —— 节选骨架 */
struct pci_bus {
        struct list_head node;                          /* :653 全局/父链表节点 */
        struct pci_bus  *parent;                        /* :654 */
        struct list_head children;                      /* :655 子总线 */
        struct list_head devices;                       /* :656 本线上的设备 */
        struct pci_dev  *self;                          /* :657 ⚠️ 上游桥设备 */
        struct list_head slots;                         /* :658 ⚠️ pci_slot_mutex 保护 */
        struct resource *resource[PCI_BRIDGE_RESOURCE_NUM];  /* :660 ⚠️ 4 项 */
        struct list_head resources;                     /* :661 ⚠️ 额外窗口 */
        struct resource busn_res;                       /* :662 总线号区间 */
        struct pci_ops  *ops;                           /* :664 ⚠️⚠️ 配置访问后端 */
        void            *sysdata;                       /* :665 */
        ...
};
```

### §3.1 字段分组

| 组 | 字段 | 行 |
|---|---|---|
| 拓扑 | `node` · `parent` · `children` · `devices` · `self` · `slots` | `:653`–`:659` |
| 资源 | ⚠️ `resource[4]` · `resources` · `busn_res` | `:660`–`:662` |
| 访问 | ⚠️⚠️ `ops` · `sysdata` · `procdir` | `:664`–`:666` |
| 编号 | `number` · `primary` | `:668` · `:669` |
| 速率 | `max_bus_speed` · `cur_bus_speed`（都是 `unsigned char`，实为 `enum pci_bus_speed`） | `:670` · `:671` |
| ⚠️ 条件 | `int domain_nr`（`#ifdef CONFIG_PCI_DOMAINS_GENERIC`） | `:672-674` |
| 标识 | `name[48]` · `bridge_ctl` · `bus_flags` | `:676` · `:678` · `:679` |
| 设备模型 | `bridge` · `dev` | `:680` · `:681` |
| sysfs | `legacy_io` · `legacy_mem` | `:682` · `:683` |
| 状态 | `is_added:1` · `unsafe_warn:1` | `:684` · `:685` |

⚠️⚠️ **`resource[4]` 和 `resources` 是两套并存的窗口存储**，
原因写在 `pci.h:629-634` 的注释里：
**前 4 个（对应 P2P 或 CardBus 桥窗口）放数组，多出来的放链表**
⇒ **遍历必须用 `pci_bus_for_each_resource()`（`pci.h:1497-1502`），
不能只扫数组**：

```c
/* include/linux/pci.h:1501 —— 循环条件节选 */
(res = pci_bus_resource_n(bus, i)) || i < PCI_BRIDGE_RESOURCE_NUM;
```

⚠️ **这个宏的条件写得很别扭**（`||` 的两侧顺序），是为了让
"数组里的空洞"也能被遍历到。⇒ 03 §4 逐行拆。

### §3.2 三条判据 inline

```c
/* include/linux/pci.h:703-706 */
static inline bool pci_is_root_bus(struct pci_bus *pbus)
{ return !(pbus->parent); }                   /* 全树 45 个文件用 */

/* include/linux/pci.h:715-719 */
static inline bool pci_is_bridge(struct pci_dev *dev)
{ return dev->hdr_type == PCI_HEADER_TYPE_BRIDGE ||
         dev->hdr_type == PCI_HEADER_TYPE_CARDBUS; }

/* include/linux/pci.h:725-732 */
static inline struct pci_dev *pci_upstream_bridge(struct pci_dev *dev)
{
        dev = pci_physfn(dev);
        if (pci_is_root_bus(dev->bus))
                return NULL;
        return dev->bus->self;
}
```

⚠️⚠️ **`pci.h:695-702` 的注释点名了一个常见错误**：
"有些代码假设 `bus->self == NULL` 就是根总线 —— **这是错的**，
因为 SR-IOV 加出来的虚拟总线也满足 `bus->self == NULL`"。
⇒ **判根总线一律用 `pci_is_root_bus()`（看 `parent`），不要看 `self`。**

⚠️ **`pci_is_bridge()` 不看 `subordinate`**：它的 kerneldoc（`:708-714`）
明确写"不管有没有 subordinate 都返回 true"
⇒ **枚举中途的桥（还没建子总线）也算桥**。
配套的遍历宏 `for_each_pci_bridge()` 在 `:721-723`。

⚠️ `domain_nr` 只在 `CONFIG_PCI_DOMAINS_GENERIC` 下存在（`:672-674`），
访问器有三个互斥版本：`:1785`（不支持 domain，恒 0）·
`:1795-1798`（generic，读 `bus->domain_nr`）· `:1950`（`!CONFIG_PCI`，恒 0）。
⚠️⚠️ **arm64 走的是 generic 那条** ⇒ `pci_domain_nr()` 是真值，
全树 103 个文件依赖它。

---

## §4 `struct pci_host_bridge`：27 个字段

⚠️ **这是控制器驱动唯一要填的大结构**。分四组看。

### §4.1 组① 必填的骨架（8）

| 字段 | 行 | 谁填 |
|---|---|---|
| `struct device dev` | `:570` | ⚠️ 核心（`pci_alloc_host_bridge` 里） |
| `struct pci_bus *bus` | `:571` | ⚠️ 核心（扫描后回填根总线） |
| `struct pci_ops *ops` | `:572` | ⚠️⚠️ **控制器驱动必填** |
| `struct pci_ops *child_ops` | `:573` | ⚠️ 可选：子总线用不同的访问方式 |
| `void *sysdata` | `:574` | 控制器私有钩子 |
| `int busnr` | `:575` | 起始总线号 |
| `int domain_nr` | `:576` | ⚠️ **`PCI_DOMAIN_NR_NOT_SET`(-1) 表示"我没设"** |
| `struct list_head windows` | `:577` | ⚠️⚠️ **地址窗口（`resource_entry`）** |

⚠️ `PCI_DOMAIN_NR_NOT_SET` 定义在 `pci.h:567`，
注释（`:559-566`）说明：**留 `-1` 时
`CONFIG_PCI_DOMAINS_GENERIC=y` 的体系会用 `pci_bus_find_domain_nr()` 自动分配**
⇒ ⚠️⚠️ **arm64 的 DT 控制器通常什么都不填，靠 `linux,pci-domain` 或自动分配**（06 §3）。

### §4.2 组② DMA 与中断（3）

| 字段 | 行 | 说明 |
|---|---|---|
| `struct list_head dma_ranges` | `:578` | ⚠️ **DT `dma-ranges` 解析结果** |
| `u8 (*swizzle_irq)(struct pci_dev *, u8 *)` | `:579` | 平台 IRQ 旋转（默认 `pci_common_swizzle`） |
| `int (*map_irq)(const struct pci_dev *, u8, u8)` | `:580` | ⚠️ INTx 引脚 → IRQ 号 |

⚠️⚠️ **arm64 DT 系统上这两个回调基本用不到**：
INTx 走 DT 的 `interrupt-map`，由 `of_irq_parse_and_map_pci()`
（`of.c:540`）处理 ⇒ **`map_irq`/`swizzle_irq` 是给没有 DT 的老平台留的**（06 §5）。

### §4.3 组③ 12 个能力/策略位（`:583`–`:594`）

| 位 | 行 | 含义 |
|---|---|---|
| `ignore_reset_delay:1` | `:583` | 整棵树跳过复位延时 |
| `no_ext_tags:1` | `:584` | 不支持 Extended Tags |
| `no_inc_mrrs:1` | `:585` | 不许增大 MRRS |
| ⚠️ `native_aer:1` | `:586` | ⚠️⚠️ **OS 可以用 AER**（否则归固件） |
| `native_pcie_hotplug:1` | `:587` | OS 可以用 PCIe 热插拔 |
| `native_shpc_hotplug:1` | `:588` | OS 可以用 SHPC |
| `native_pme:1` | `:589` | OS 可以用 PME |
| `native_ltr:1` | `:590` | OS 可以用 LTR |
| `native_dpc:1` | `:591` | OS 可以用 DPC |
| ⚠️ `preserve_config:1` | `:592` | ⚠️⚠️ **保留固件分好的资源，不重新分配** |
| `size_windows:1` | `:593` | 允许给根总线定窗口大小 |
| `msi_domain:1` | `:594` | 这个桥要 MSI 域 |

⚠️⚠️ **那 6 个 `native_*` 位是"OS 和固件抢功能所有权"的记录**：
在 ACPI 系统上它们由 `_OSC` 协商决定；
⚠️ **arm64 DT 系统上没有协商方，`pci_init_host_bridge()` 直接全置 1**
⇒ **DT 上 AER/DPC/热插拔的所有权天然归内核**（99 §2.7）。

⚠️ **`preserve_config`（`:592`）对应 DT 的 `linux,pci-probe-only`**
（`of_pci_check_probe_only()`，`of.c:252`）
⇒ ⚠️⚠️ **有些 SoC 的 bootloader 已经把 BAR 分好且不能重分，必须打开它**（06 §3）。

### §4.4 组④ 对齐回调与私有数据（2）

| 字段 | 行 | 说明 |
|---|---|---|
| `resource_size_t (*align_resource)(...)` | `:596-601` | ⚠️ 控制器有额外对齐要求时填 |
| `unsigned long private[] ____cacheline_aligned` | `:602` | ⚠️⚠️ **柔性数组：控制器私有数据就住在这里** |

⚠️⚠️ **`private[]` 是 `devm_pci_alloc_host_bridge(dev, priv_size)`
那个 `priv_size` 参数的落点**，取用靠两个 inline：

```c
/* include/linux/pci.h:607-615 */
static inline void *pci_host_bridge_priv(struct pci_host_bridge *bridge)
{ return (void *)bridge->private; }

static inline struct pci_host_bridge *pci_host_bridge_from_priv(void *priv)
{ return container_of(priv, struct pci_host_bridge, private); }
```

⇒ ⚠️ **这是 arm64 控制器驱动的标准写法**：
`bridge = devm_pci_alloc_host_bridge(dev, sizeof(*pcie))` 一次分配，
`pcie = pci_host_bridge_priv(bridge)` 拿到自己的结构，
**不需要单独 kzalloc**（06 §4）。

⚠️ `to_pci_host_bridge(n)` 宏在 `:605`，从 `struct device` 反向。

---

## §5 `struct pci_driver`：17 个字段

⚠️ 接口用法在 01 §2.2，本节只补结构侧的三件事。

```c
/* include/linux/pci.h:920-938 —— 全部 17 个 */
struct pci_driver {
        struct list_head        node;                    /* :921 核心用 */
        const char              *name;                   /* :922 ⚠️ 必填 */
        const struct pci_device_id *id_table;            /* :923 ⚠️⚠️ 必须非 NULL */
        int  (*probe)(struct pci_dev *, const struct pci_device_id *);  /* :924 */
        void (*remove)(struct pci_dev *);                /* :925 */
        int  (*suspend)(struct pci_dev *, pm_message_t); /* :926 ⚠️ 遗留 */
        int  (*resume)(struct pci_dev *);                /* :927 ⚠️ 遗留 */
        void (*shutdown)(struct pci_dev *);              /* :928 */
        int  (*sriov_configure)(struct pci_dev *, int);   /* :929 PF 侧 */
        int  (*sriov_set_msix_vec_count)(struct pci_dev *, int); /* :930 PF 侧 */
        u32  (*sriov_get_vf_total_msix)(struct pci_dev *); /* :931 */
        const struct pci_error_handlers *err_handler;     /* :932 */
        const struct attribute_group **groups;            /* :933 */
        const struct attribute_group **dev_groups;        /* :934 */
        struct device_driver    driver;                    /* :935 ⚠️⚠️ .pm 放这里 */
        struct pci_dynids       dynids;                     /* :936 核心用 */
        bool driver_managed_dma;                            /* :937 */
};
```

⚠️⚠️ **`:923` 的行内注释就是最重要的那条约束**：
"Must be non-NULL for probe to be called" ——
⇒ **忘了填 `id_table`，`pci_register_driver()` 照样返回成功，
但 `probe` 永远不会被调**（01 §2.1）。

⚠️ **`groups` 与 `dev_groups` 的区别**（kerneldoc `:907-909`）：
前者挂在**驱动**上，后者挂在**绑定后的设备**上。
⇒ ⚠️ **`dev_groups` 里的属性在 probe 成功后才出现，
不需要驱动自己 `device_add_group()`。**

⚠️ **`driver_managed_dma`（`:937`）的 kerneldoc 很长（`:912-918`）**，
核心意思：**只有 VFIO 这类"自己管 I/O 地址空间"的驱动才置 true**，
普通驱动一律不管它 ⇒ 置错会让 IOMMU 层放开约束。

⚠️ 反向宏在 `:940-943`：

```c
static inline struct pci_driver *to_pci_driver(struct device_driver *drv)
{
    return drv ? container_of(drv, struct pci_driver, driver) : NULL;
}
```

⚠️ **注意它带 NULL 检查**（`drv ?`）—— 这一点和绝大多数 `container_of` 包装不同，
因为 `dev->driver` 在未绑定时是 NULL。

**使用面**：`err_handler` 143 个文件 · `sriov_configure` 76 个文件
⇒ ⚠️ **17 个字段里有 3 个（SR-IOV 那组）只服务不到 5% 的驱动。**

---

## §6 `struct pci_device_id`：不在 `pci.h` 里

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

⚠️⚠️ **为什么在 `mod_devicetable.h`**：`scripts/mod/file2alias.c` 要在
**构建主机上**解析这个结构生成模块别名，**它不能 include `pci.h`**
（那会拖进整个内核头文件世界）⇒ 所有总线的 `*_device_id` 都集中在这一个头里。

⚠️ `PCI_ANY_ID` 定义在同一个头的 `:17`，值是 `(~0)`。

### §6.1 六个填表宏

| 宏 | 行 | 填什么 | 全树使用（文件数） |
|---|---|---|---|
| `PCI_DEVICE(vend, dev)` | `:954-956` | vendor+device，sub 两项设 `PCI_ANY_ID` | ⚠️⚠️ **326** |
| `PCI_VDEVICE(vend, dev)` | `:1025-1027` | ⚠️ vendor 用**名字**（`PCI_VENDOR_ID_` 前缀自动加），⚠️**末尾留 `0, 0` 让下一项接 driver_data** | ⚠️ **223** |
| `PCI_DEVICE_SUB(...)` | `:997` | 四项全填 | 31 |
| `PCI_DEVICE_DATA(vend, dev, data)` | `:1053` | ⚠️ 名字形式 + 直接填 `driver_data` | 22 |
| `PCI_DEVICE_CLASS(class, mask)` | `:1010-1013` | ⚠️⚠️ **按类匹配**，四个 ID 全 `PCI_ANY_ID` | 15 |
| `PCI_DEVICE_DRIVER_OVERRIDE(...)` | `:969-971` | 填 `override_only` | — |
| `PCI_DRIVER_OVERRIDE_DEVICE_VFIO(...)` | `:984` | ⚠️ VFIO 专用封装 | — |

⚠️⚠️ **`PCI_VDEVICE` 和 `PCI_DEVICE` 的差别不只是写法**：
`PCI_VDEVICE` 展开末尾多了 `, 0, 0`（`:1027`），
⇒ **它假定你紧接着写 `driver_data`**，
所以 `{ PCI_VDEVICE(INTEL, 0x1234), (kernel_ulong_t)&cfg }` 是合法的，
而对 `PCI_DEVICE` 用同样写法会填错字段。

⚠️ **`override_only`（`:48`）的语义**（kerneldoc `mod_devicetable.h:41`）：
**只有 `dev->driver_override` 正好是这个驱动时才匹配**
⇒ **这是 VFIO"抢设备"的机制**，普通驱动不该用。

⚠️ **`driver_data` 的官方建议**（`mod_devicetable.h:36-40`）：
**用它当"静态表的下标"，而不是当指针**。
⚠️⚠️ **但这条建议在 sysfs `new_id` 上是硬约束**：
通过 `new_id` 加进来的 ID，其 `driver_data` **必须与某条已有表项的值相同**
（`pci.rst:108-110`，01 §2.4）⇒ **当指针用就无法通过 `new_id` 加设备。**

⚠️ **表末必须是全零项**，且几乎所有驱动都要
`MODULE_DEVICE_TABLE(pci, my_ids)`（全树 **755 个文件**）
—— 没有它模块不会被自动加载。

---

## §7 `struct pci_ops`：5 个回调

```c
/* include/linux/pci.h:778-784 */
struct pci_ops {
        int (*add_bus)(struct pci_bus *bus);                    /* :779 */
        void (*remove_bus)(struct pci_bus *bus);                /* :780 */
        void __iomem *(*map_bus)(struct pci_bus *bus,
                                 unsigned int devfn, int where); /* :781 */
        int (*read)(struct pci_bus *, unsigned int devfn,
                    int where, int size, u32 *val);              /* :782 */
        int (*write)(struct pci_bus *, unsigned int devfn,
                     int where, int size, u32 val);              /* :783 */
};
```

⚠️⚠️ **`map_bus` + 通用 read/write 是 arm64 控制器的标准套路**：
只实现 `map_bus`（"给我 bus/devfn/offset，我返回可访问的地址"），
`read`/`write` 直接填 `pci_generic_config_read`/`write`（`access.c:98` / `:118`）
⇒ **一个 ECAM 控制器的配置访问后端只要三行**（01 §10.6、06 §4）。

⚠️ **`map_bus` 返回 NULL 表示"这个 devfn 不存在"** ——
通用访问器会把它变成 `PCIBIOS_DEVICE_NOT_FOUND`。

⚠️ **`add_bus`/`remove_bus` 是可选的**：在每条子总线被创建/销毁时回调，
用于控制器需要给子总线单独准备资源（比如每条总线一个 ECAM 窗口）。

⚠️ **`read`/`write` 的返回值是 `PCIBIOS_*` 而不是 errno**（01 §11）——
⇒ **自己实现时返回 0 表示成功，不要返回负数。**

⚠️ `pci_bus_set_ops()`（`access.c:196`）可以运行期替换整套 ops，
⚠️⚠️ **它是 quirk 的后门，正常驱动不该调**。

---

## §8 `struct pci_error_handlers`：6 个回调

```c
/* include/linux/pci.h:842-859 */
struct pci_error_handlers {
        pci_ers_result_t (*error_detected)(struct pci_dev *,
                                pci_channel_state_t error);   /* :844-845 */
        pci_ers_result_t (*mmio_enabled)(struct pci_dev *);   /* :848 */
        pci_ers_result_t (*slot_reset)(struct pci_dev *);     /* :851 */
        void (*reset_prepare)(struct pci_dev *);              /* :854 */
        void (*reset_done)(struct pci_dev *);                 /* :855 */
        void (*resume)(struct pci_dev *);                     /* :858 */
};
```

⚠️ **前三个返回 `pci_ers_result_t`，后三个是 `void`** ——
⇒ **`reset_prepare`/`reset_done` 不参与恢复决策**，
它们只是"复位前后通知你一声"，01 §7 的所有复位函数都会触发这一对。

`pci_ers_result_t` 的 6 个取值（`pci.h:821-839`，`enum pci_ers_result`）：

| 值 | 编号 | 含义 |
|---|---|---|
| `PCI_ERS_RESULT_NONE` | 1 | ⚠️ 驱动没提供结果/不支持 |
| `PCI_ERS_RESULT_CAN_RECOVER` | 2 | 不用复位就能恢复 |
| `PCI_ERS_RESULT_NEED_RESET` | 3 | ⚠️ **要求复位槽位** |
| `PCI_ERS_RESULT_DISCONNECT` | 4 | ⚠️⚠️ **彻底失败，不可恢复** |
| `PCI_ERS_RESULT_RECOVERED` | 5 | 已完全恢复 |
| `PCI_ERS_RESULT_NO_AER_DRIVER` | 6 | 驱动没注册 AER 能力 |

⚠️⚠️ **注意这些值从 1 开始而不是 0** ——
⇒ **回调里 `return 0;` 不是"成功"，而是一个非法值**，会被恢复流程当成未知处理。

⚠️ `error_detected()` 收到的 `pci_channel_state_t`（`:845`）就是 §2.10 那个
`dev->error_state` 的类型：`io_normal` / `io_frozen` / `io_perm_failure`。
⚠️ **`io_frozen` 意味着"此刻不能访问设备的任何寄存器"**
⇒ 回调里做 MMIO 读会拿到全 `0xff`（04 §2）。

⚠️ 结构体上方 `:812-817` 的注释指向
`Documentation/PCI/pci-error-recovery.rst`（kerneldoc `:906` 也指它）
⇒ **完整的状态机在 04 篇讲。**

---

## §9 五个小结构

| 结构 | 位置 | 字段 | 用途 |
|---|---|---|---|
| `struct pci_slot` | `pci.h:75-81` | 5：`bus` · `list` · `hotplug` · `number` · `kobj` | ⚠️ **物理槽位**；`pci_slot_name()` 在 `:83-86` |
| `struct pci_vpd` | `pci.h:310-314` | 3：`lock`(mutex) · `len` · `cap` | VPD 读取状态 |
| `struct pci_bus_region` | `pci.h:801-804` | 2：`start` · `end`（`pci_bus_addr_t`） | ⚠️ **总线地址区间**（≠ CPU 地址） |
| `struct pci_bus_resource` | `pci.h:644-648` | 3：`list` · `res` · `flags` | ⚠️ `bus->resources` 链表的节点 |
| `struct pci_dynids` | `pci.h:806-809` | 2：`lock`(spinlock) · `list` | ⚠️ `new_id` 加进来的 ID |

⚠️⚠️ **`pci_bus_addr_t` 是条件类型**（`pci.h:795-799`）：
`CONFIG_ARCH_DMA_ADDR_T_64BIT` 下是 `u64`，否则 `u32`。
⇒ **arm64 上是 64 位** ⇒ 与 `resource_size_t` 同宽，但**语义不同**
（一个是总线地址、一个是 CPU 地址，01 §10.4）。

⚠️ `pci_bus_resource.flags` 目前只有一个取值
`PCI_SUBTRACTIVE_DECODE`(0x1)（`pci.h:642`），
注释（`:636-641`）说明它表示"桥隐式转发这个窗口，没法编程告诉桥细节"。
⚠️ 掩码 `PCI_REGION_FLAG_MASK`(0x0f) 在 `:650`。

---

## §10 常量与枚举总表

| 名字 | 位置 | 值 / 项数 |
|---|---|---|
| `PCI_NUM_RESET_METHODS` | `pci.h:53` | ⚠️ **7** |
| `PCI_RESET_PROBE` / `PCI_RESET_DO_RESET` | `pci.h:55-56` | `true` / `false`（⚠️ 同一个参数的两种语义） |
| `PCI_DEVID(bus, devfn)` / `PCI_BUS_NUM(x)` | `pci.h:70` / `:72` | ⚠️ 内核私有（uapi 里只有 `PCI_DEVFN`/`SLOT`/`FUNC`，`:66-68`） |
| `PCI_BRIDGE_RESOURCE_NUM` | `pci.h:121` | 4 |
| `PCI_NUM_RESOURCES` = `DEVICE_COUNT_RESOURCE` | `pci.h:129` `:132` | ⚠️ **随 `CONFIG_PCI_IOV` 变**（§2.6） |
| `PCI_NUM_INTX` | `pci.h:155` | 4 |
| ⚠️ `PCI_ERROR_RESPONSE` | `pci.h:162` | `(~0ULL)` |
| ⚠️⚠️ `PCI_POSSIBLE_ERROR(val)` | `pci.h:164` | **判"读回来的是不是全 f"** |
| `PCI_SET_ERROR_RESPONSE(val)` | `pci.h:163` | 把出参设成全 f |
| `PCI_DOMAIN_NR_NOT_SET` | `pci.h:567` | ⚠️ `(-1)` |
| `PCI_STATUS_ERROR_BITS` | `pci.h:45-50` | ⚠️ 6 个状态位的并 |
| `enum pci_mmap_state` | `pci.h:89-92` | 2：`pci_mmap_io` · `pci_mmap_mem` |
| `enum pci_interrupt_pin` | `pci.h:146-152` | 5：`UNKNOWN` · `INTA`–`INTD` |
| `pci_power_t` 取值 | `pci.h:172-178` | 7（含 `PCI_POWER_ERROR` = -1） |
| `pci_channel_state_t` 取值 | `pci.h:197-206` | 3 |
| `enum pcie_reset_state` | `pci.h:210-219` | 3：`deassert` · `warm` · `hot` |
| `enum pci_dev_flags` | `pci.h:222-249` | ⚠️ **13**（1<<4 空缺） |
| `enum pci_irq_reroute_variant` | `pci.h:251-254` | 2 |
| `enum pci_bus_flags` | `pci.h:257-262` | 4：`NO_MSI` · `NO_MMRBC` · `NO_AERSID` · `NO_EXTCFG` |
| `enum pcie_link_width` | `pci.h:265-275` | 9（x1…x32 + 两个特殊值） |
| `enum pci_bus_speed` | `pci.h:278-` | ⚠️ **PCI/PCI-X/AGP/PCIe 混在一个枚举里** |
| `enum pci_ers_result` | `pci.h:821-839` | 6（⚠️ 从 1 开始） |

⚠️⚠️ **`PCI_POSSIBLE_ERROR()`（`pci.h:164`）是 01 §4.4 那个
"读回全 f 但返回 `PCIBIOS_SUCCESSFUL`"问题的官方判据**，
注释（`:157-161`）自己说明了局限：
**"一次成功的读也可能返回 `~0`，所以你需要额外信息才能可靠区分错误"**
⇒ **配合 `pci_device_is_present()`（01 §9.3）一起用。**

⚠️ **`enum pci_bus_speed`（`:278`）把四代总线塞进一个枚举**，
其中 `AGP_*` 占 `0x0c`–`0x10`、PCIe 从 `0x14` 起。
⚠️⚠️ **`pci_bus.max_bus_speed`/`cur_bus_speed` 声明的类型是
`unsigned char` 而不是这个枚举**（`:670-671`，靠注释说明）
⇒ **编译器不会替你检查赋值，是一处类型安全缺口**（99 §2）。

---

## §11 生命周期与"谁能持有指针"

| 对象 | 创建 | 销毁 | 驱动能长期持有吗 |
|---|---|---|---|
| `pci_dev` | `pci_alloc_dev()`（`probe.c:2352`）+ `pci_device_add()` | ⚠️ 引用计数归零 | ⚠️⚠️ **必须 `pci_dev_get()`**，否则只在 probe–remove 之间有效 |
| `pci_bus` | `pci_add_new_bus()`（`probe.c:1188`） | `pci_remove_bus()`（`remove.c:64`） | ⚠️ 不要 |
| `pci_host_bridge` | `devm_pci_alloc_host_bridge()` | ⚠️ devm 自动 | 控制器驱动自己的 |
| `pci_driver` | ⚠️ 驱动静态定义 | 模块卸载 | — |
| `resource[i]` | 随 `pci_dev` | 随 `pci_dev` | ⚠️ 用宏取值，不要存指针 |

⚠️⚠️ **`pci_dev` 的引用计数规则（01 §8.2 的结构侧根据）**：
`pci_get_*()` 系列**加引用**，`pci_find_*()` 系列**不加**。
⇒ **`for_each_pci_dev()`（`pci.h:552`）中途 `break` 会漏掉最后一次
`pci_dev_put()`** —— 因为这个宏本身就是靠 `pci_get_device()` 迭代的。

⚠️ 6.1.158 里回合并了 `cleanup.h` 风格的自动释放（99 §1.1）：

```c
/* include/linux/pci.h:1165 */
DEFINE_FREE(pci_dev_put, struct pci_dev *, if (_T) pci_dev_put(_T))
/* include/linux/pci.h:1774 */
DEFINE_GUARD(pci_dev, struct pci_dev *, pci_dev_lock(_T), pci_dev_unlock(_T))
```

⚠️⚠️ **这是 6.11+ 的语法出现在 6.1 稳定分支里** ——
⇒ **写代码可以用，但要知道它在原版 6.1 里不存在**（移植时会踩）。

---

## §12 一页速查：字段数与"驱动要不要看"

| 结构 | 字段 | 位域 | 条件字段 | 设备驱动 | 控制器驱动 |
|---|---|---|---|---|---|
| `pci_dev` | **134** | ⚠️ **62** | ⚠️ 27 / 11 个 `CONFIG_` | ⚠️ **读 8 个** | 读 |
| `pci_bus` | 26 | 2 | 1 | ⚠️ 几乎不看 | ⚠️ **要看** |
| `pci_host_bridge` | 27 | 12 | 0 | ⚠️ 不看 | ⚠️⚠️ **全部** |
| `pci_driver` | 17 | 0 | 0 | ⚠️⚠️ **填 5 个** | 不看 |
| `pci_ops` | 5 | 0 | 0 | 不看 | ⚠️⚠️ **填 3 个** |
| `pci_error_handlers` | 6 | 0 | 0 | ⚠️ 可选填 3 个 | 不看 |
| `pci_device_id` | 7 | 0 | 0 | ⚠️ **用宏填** | 不看 |
| `pci_slot` / `pci_vpd` / `pci_bus_region` / `pci_bus_resource` / `pci_dynids` | 5/3/2/3/2 | 0 | 0 | 不看 | 少量 |

⚠️⚠️ **两个反直觉数字，值得记住**：

1. **134 个字段里 62 个是位域，而其中 34 个属于"状态标记 + 硬件缺陷标记"**
   （§2.7 + §2.8）⇒ **`pci_dev` 的体积主要来自三十年的兼容性包袱，
   不是抽象层次太多。**
2. **驱动作者真正要填的结构一共只有 `pci_driver`(17) + `pci_device_id`(7)**，
   其中常填的字段不到 10 个 ⇒ **134 个字段的大结构对驱动是透明的。**

---

## 下一步读什么

| 你的问题 | 去 |
|---|---|
| 三分钟版的全景 | 00-overview.md |
| 这些字段是被哪个接口读写的 | 01-api.md |
| 枚举时这些字段是按什么顺序填上的 | 03-internals.md |
| `aer_cap` / `dpc_cap` / `sriov` 那些条件字段做什么用 | 04-extensions.md |
| 我要写驱动，只想知道填哪几个 | 05-usage.md |
| `pci_host_bridge` 在 arm64 上具体怎么填 | 06-devicetree.md |
| 这些字段在 sysfs 里露出哪些 | 07-debug.md |
| 为什么 `pci_dev` 会长到 134 个字段 | 08-feynman.md |
| 本篇每个数字的口径与推导 | 99-evidence.md |
