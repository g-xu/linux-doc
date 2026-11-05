---
title: 扩展机制
---

# virtio 04：子框架

## §0 组织轴

**本篇的组织轴是"一节一个独立子框架，互不调用"。**
⚠️ **但 virtio 的"子框架"和 timer/of 的不太一样**：
它们不是"核心之上的扩展"，而是 **两层可插拔的具体实现**（02 §1.0.F）——
传输层实现和设备驱动实现。

**所以本篇分三类**：
- **§1–§4 是层 1（传输层）的实现**
- **§5–§7 是层 2（设备驱动）里住在 `drivers/virtio/` 的三个**
- **§8–§9 是真正的辅助模块**

### §0.1 分诊表：arm64 上哪些真会碰到

| # | 子框架 | 文件（行数） | arm64 上会碰到吗 | 谁会碰 |
|---|---|---|---|---|
| §1 | **PCI 传输层** | `virtio_pci_common.c`(654) + `virtio_pci_modern.c`(556) + `virtio_pci_modern_dev.c`(711) + `virtio_pci_legacy.c`(235) + `virtio_pci_legacy_dev.c`(222) + `virtio_pci_common.h`(141) | **会**（虚拟机里有 PCI） | 几乎所有人 |
| §2 | **MMIO 传输层** | `virtio_mmio.c`(860) | **会** —— **arm64 虚拟机的另一条主路** | 嵌入式/轻量 VMM |
| §3 | **vDPA 传输层** | `virtio_vdpa.c`(417) | 有 vDPA 硬件才会 | 数据中心网卡 |
| §4 | **vringh（主机侧）** | ⚠️ **`drivers/vhost/vringh.c`(1511)** —— **不在 `drivers/virtio/`** | vhost/vDPA 场景 | 写 vhost 后端的人 |
| §5 | **balloon 设备驱动** | `virtio_balloon.c`(1132) | **会**（内存超分） | — |
| §6 | **virtio_mem 设备驱动** | `virtio_mem.c`(2952) | **会** —— **`depends on X86_64 \|\| ARM64`**（01 §0.6） | 内存热插拔 |
| §7 | **virtio_input 设备驱动** | `virtio_input.c`(414) | 有图形界面才会 | — |
| §8 | **dma-buf 集成** | `virtio_dma_buf.c`(89) | virtio-gpu 才会 | — |
| §9 | **anchor（受限内存访问）** | `virtio_anchor.c`(18) | ⚠️ **arm64 上目前不用**（§9.2） | AMD SEV / s390 PV |

⇒ **真正要读懂的是 §1 和 §2**（两条传输层主路）。
**§9 只有 18 行但结论最有意思**——它是 arm64-only 视角下"存在但用不上"的例子。

### §0.2 各子框架挂在 03 时间线的哪一点

**传输层挂在 §A1（探测设备）之前**，设备驱动挂在 §A4 的 `probe` 里：

| 子框架 | 挂载点 |
|---|---|
| PCI 传输层 | **PCI 总线的 probe**（`vendor 0x1af4`）→ 03 §A1 |
| MMIO 传输层 | **platform_driver 的 probe**（`drivers/virtio/virtio_mmio.c:831`）→ 03 §A1 |
| vDPA 传输层 | vDPA 总线的 probe |
| vringh | ⚠️ **完全不在 virtio 的时间线上** ——它是**对端侧**的库（§4） |
| balloon / mem / input | **`virtio_dev_probe()` 里的 `drv->probe()`**（03 §A4 第 ⑩ 步） |
| dma-buf | 由设备驱动（virtio-gpu）按需调 |
| anchor | ⚠️ **架构初始化时**（`arch/x86/mm/mem_encrypt_amd.c` / `arch/s390/mm/init.c`）——**比 virtio 核心还早** |

⚠️ **anchor 的挂载点是本篇最特殊的一个**：它由**架构代码**在启动早期
调 `virtio_require_restricted_mem_acc()` 设一个全局函数指针，
**然后 virtio 核心在 `virtio_features_ok()` 里读它**（03 §A5）。
⇒ **这是一条"架构 → virtio"的反向依赖**，方向和别的都相反。

---

## §1 PCI 传输层：五个文件，两套代码路径

### §1.1 文件分工

| 文件 | 行数 | 职责 |
|---|---|---|
| `virtio_pci_common.c` | 654 | **两条路径共用的部分**：中断分配（MSI-X / INTx）、`find_vqs` 的框架、PM |
| `virtio_pci_modern.c` | 556 | **现代（1.0+）设备的 `virtio_config_ops`**（两份，01 §1.1） |
| `virtio_pci_modern_dev.c` | 711 | **一个库**（`CONFIG_VIRTIO_PCI_LIB`），**19 个导出符号** |
| `virtio_pci_legacy.c` | 235 | legacy（0.9.X）设备的 ops |
| `virtio_pci_legacy_dev.c` | 222 | 库（`CONFIG_VIRTIO_PCI_LIB_LEGACY`），**12 个导出符号** |
| `virtio_pci_common.h` | 141 | 内部共享声明 |

**`*_dev.c` 那两个是库这件事很重要**：它们导出 31 个符号（01 §0.4），
⇒ **给 vDPA 之类的"不是 PCI 但要复用 PCI 侧寄存器操作"的代码用**。

**使用者统计**：

```bash
grep -rlw 'vp_modern_probe\|virtio_pci_modern_probe' --include=*.c . | wc -l   # → 4
```

⇒ **4 个文件** ——说明这个库确实被 `drivers/virtio/` 之外的代码复用了。

### §1.2 modern 有两份 `virtio_config_ops`

| ops | 位置 | 用在哪 |
|---|---|---|
| `virtio_pci_config_nodev_ops` | `drivers/virtio/virtio_pci_modern.c:484` | **没有配置空间的设备** |
| `virtio_pci_config_ops` | `drivers/virtio/virtio_pci_modern.c:504` | 有配置空间的设备 |

⇒ **这是 9 个 `virtio_config_ops` 里唯一"同一个文件两份"的情况**（01 §1.1）。
**为什么要分**：规范允许设备没有配置空间（PCI 上表现为没有那个 capability），
分两份 ops 就不用在每个访问器里判空。
这和 timer 把 `HARD`/`SOFT` 编码进 8 个 clock_base 是同一个思路——
**把判断编码进数据结构**。

---

## §2 MMIO 传输层：arm64 上的另一条主路

### §2.1 三条发现路径

| 路径 | 怎么触发 | 代码 |
|---|---|---|
| **DT** | `compatible = "virtio,mmio"` | `virtio_mmio_match[]`（`drivers/virtio/virtio_mmio.c:817-818`），挂在 platform_driver 上（`:831` / `:836`） |
| ⚠️ **内核命令行** | `virtio_mmio.device=<size>@<base>:<irq>` | `vm_cmdline_set()`（`drivers/virtio/virtio_mmio.c:703`），要 `CONFIG_VIRTIO_MMIO_CMDLINE_DEVICES` |
| ACPI | 通过 platform 设备的常规 ACPI 路径 | — |

⚠️ **命令行那条路很值得注意**：它用
`platform_device_register_resndata()`（`drivers/virtio/virtio_mmio.c:753-754`）
**凭空造出一个 platform 设备**，父设备是一个静态的
`vm_cmdline_parent`（`:696`）。
⇒ **设备可以完全不出现在 DT 里**（06 §0 的分诊表会展开）。

**Kconfig 的 help 文本自己给了警告**（`drivers/virtio/Kconfig:163-164`）：
参数写错（**特别是基地址**）会让系统崩溃。
⇒ **这条路是调试/裸机用的，不是生产用的。**

### §2.2 寄存器布局（`include/uapi/linux/virtio_mmio.h`）

**这是 uapi ——也就是说寄存器偏移是规范定的，不是 Linux 定的**：

| 偏移 | 寄存器 | 位置 | 说明 |
|---|---|---|---|
| `0x000` | `VIRTIO_MMIO_MAGIC_VALUE` | `include/uapi/linux/virtio_mmio.h:43` | **魔数**——`drivers/virtio/virtio_mmio.c:623` 读它做第一道校验 |
| `0x004` | `VIRTIO_MMIO_VERSION` | `:46` | **1 = legacy，2 = 现代** |
| `0x008` | `VIRTIO_MMIO_DEVICE_ID` | `:49` | **设备类型**（对应 `VIRTIO_ID_*`） |
| `0x00c` | `VIRTIO_MMIO_VENDOR_ID` | `:52` | — |
| `0x010` / `0x014` | `DEVICE_FEATURES` / `_SEL` | `:56` / `:59` | **分页读 64 位 feature**（一次 32 位） |
| `0x020` / `0x024` | `DRIVER_FEATURES` / `_SEL` | `:63` / `:66` | 同上，写方向 |
| ⚠️`0x028` | `GUEST_PAGE_SIZE` | `:72` | **legacy 专用** |
| `0x030`–`0x038` | `QUEUE_SEL` / `QUEUE_NUM_MAX` / `QUEUE_NUM` | `:78` / `:81` / `:84` | **先选队列，再配** |
| ⚠️`0x03c` / `0x040` | `QUEUE_ALIGN` / `QUEUE_PFN` | `:90` / `:93` | **legacy 专用**：用页帧号表示队列地址 |
| `0x044` | `QUEUE_READY` | `:99` | 现代：队列就绪 |
| `0x050` | `QUEUE_NOTIFY` | `:102` | **`virtqueue_notify()` 写的就是它** |
| `0x060` / `0x064` | `INTERRUPT_STATUS` / `_ACK` | `:105` / `:108` | **中断处理要读+清** |
| `0x070` | `STATUS` | `:111` | **状态字节**（03 §A7 那六个位） |
| `0x080`–`0x0a4` | `QUEUE_DESC/AVAIL/USED_LOW/HIGH` | `:114-123` | **现代：直接写 64 位物理地址**（三段各一对） |
| `0x0ac`–`0x0bc` | `SHM_SEL` / `SHM_LEN_*` / `SHM_BASE_*` | `:126-134` | 共享内存区（`get_shm_region`） |

**两条路径的关键差别一眼可见**：

| | legacy（VERSION=1） | 现代（VERSION=2） |
|---|---|---|
| 队列地址怎么给 | **`QUEUE_PFN`（页帧号）+ `QUEUE_ALIGN`** | **三段各一对 LOW/HIGH，直接给物理地址** |
| 隐含约束 | **三段必须物理连续**（因为只给一个 PFN） | **三段可以分开分配** |

⇒ **这解释了 02 §5.1 那张布局图为什么是"一整块连续内存"** ——
它是 legacy 时代的产物，现代路径其实不要求连续，但内核仍然一起分配
（`drivers/virtio/virtio_ring.c:1068` 那条 `TODO: allocate each queue chunk individually`
就是想改这个，03 §C1）。

**`GUEST_PAGE_SIZE`(0x028) 也是 legacy 遗迹**：现代设备不需要知道 guest 页大小。

---

## §3 vDPA 传输层：对端是硬件

`drivers/virtio/virtio_vdpa.c`(417)，`virtio_config_ops` 在 `:334`。

**vDPA = "virtio Data Path Acceleration"**：
**控制面还是软件（vhost/QEMU），数据面直接由硬件处理。**

| | 普通 virtio | vDPA |
|---|---|---|
| 对端（控制面） | hypervisor | **hypervisor** |
| 对端（数据面） | **hypervisor 软件** | **网卡硬件直接读写 vring** |
| `weak_barriers` | **可以为真** | ⚠️ **必须为假**（03 §B1.2）——对端是硬件 |
| DMA | 可以不走 DMA API | **必须走**（硬件要真实的总线地址） |

⇒ **vDPA 是"对端是什么会改变 Linux 侧代码路径"最清楚的例子**
（03 §B1.2、§B2）。

---

## §4 vringh：**对端侧**的 vring 库

⚠️ **它不在 `drivers/virtio/`**：

| 文件 | 行数 |
|---|---|
| `drivers/vhost/vringh.c` | **1511** |
| `include/linux/vringh.h` | 311 |

**`virtio_ring.c` 是"驱动侧"，`vringh.c` 是"设备侧"** ——
同一份 vring 布局（02 §5），两边各写一套访问代码。

**和核心的唯一连接点**：`struct virtio_device.vringh_config`
（`include/linux/virtio.h:124`，类型 `const struct vringh_config_ops *`）。

**使用统计**：

```bash
grep -rlw 'vringh_init_kern\|vringh_init_user\|vringh_init_iotlb' --include=*.c . | wc -l   # → 4
```

⇒ **4 个文件** ——主要是 vhost 和 vDPA 的后端。

⚠️ **本套没有读 `vringh.c`**（99 §2.3 登记为三等证据）：
它属于"对端侧"，虽然在本树里，但**它实现的是 device 角色而不是 driver 角色**，
超出本套"只覆盖 driver 侧"的边界（01 §0.1）。

**但它的存在证明了一件事**：**对端不一定在树外** ——
vhost 场景下对端就在 Linux 内核里。⇒ 99 §5.5 会区分这两种情况。

---

## §5 balloon：内存超分

`drivers/virtio/virtio_balloon.c`(1132)，设备 ID **5**（`include/uapi/linux/virtio_ids.h:36`）。

**它做什么**：hypervisor 说"把这些页还给我"，驱动就把页分配出来并
告诉对端"这些页我不用了"，对端可以拿去给别的虚拟机。

⚠️ **有一个容易混的近邻**：`VIRTIO_ID_MEMORY_BALLOON` = **13**
（`include/uapi/linux/virtio_ids.h:44`）——**那是另一个设备类型**，
不是这个驱动。05 §7 会列。

**导出符号 0 个**（01 §0.4）⇒ 纯叶子驱动。

---

## §6 virtio_mem：真正的内存热插拔

`drivers/virtio/virtio_mem.c`(2952)，设备 ID **24**（`include/uapi/linux/virtio_ids.h:53`）。

**它是 `drivers/virtio/` 里最大的文件**（比 `virtio_ring.c` 还大 76 行）。

| 特征 | 说明 |
|---|---|
| Kconfig | **`depends on X86_64 \|\| ARM64`**（`drivers/virtio/Kconfig:120`）⇒ **arm64 上可用** |
| 还依赖 | `MEMORY_HOTPLUG`（`drivers/virtio/Kconfig:122`） |
| 设计债 | **5 条**（03 §C1）——`drivers/virtio/virtio_mem.c:544` · `:558` · `:653` · `:1094` · `:1164` |

**和 balloon 的区别**：balloon 只是"借走页"，内存总量不变；
**virtio_mem 真的插拔内存块**，`/proc/meminfo` 的总量会变。

⚠️ **它的 5 条设计债有两条指向 Linux MM 而不是 virtio**：
- `drivers/virtio/virtio_mem.c:653`：`TODO: Linux MM does not properly clean up yet in all cases`
- `drivers/virtio/virtio_mem.c:1164`：`TODO: We want an alloc_contig_range() mode that tries to allocate ...`

⇒ **这是"债在别人家"的情况** ——virtio_mem 受限于 MM 的能力。
这也是为什么它是本目录最大的文件：**要绕过很多 MM 的限制**。

---

## §7 virtio_input：把 hypervisor 的输入事件接进 evdev

`drivers/virtio/virtio_input.c`(414)，设备 ID **18**（`include/uapi/linux/virtio_ids.h:47`）。

**一个很薄的桥**：从 virtqueue 收 `struct virtio_input_event`，
翻译成 Linux 的 `input_event()`。
**设计债 0 条**，导出符号 0 个。

**它是"最小的完整 virtio 设备驱动"** ⇒ **想看一个真实的最简例子就读它**（05 §0.2）。

---

## §8 dma-buf 集成

`drivers/virtio/virtio_dma_buf.c`(89)，**4 个导出符号**（01 §8.1）。

**使用统计**：

```bash
grep -rlw 'virtio_dma_buf_export' --include=*.c . | wc -l   # → 2
```

⇒ **2 个文件**（含实现本身）⇒ **真消费者只有 1 个**，
就是 virtio-gpu（`drivers/gpu/drm/virtio/`）。

**它解决的问题**：把一个 virtio-gpu 对象导出成 dma-buf 给别的子系统用，
同时带上 virtio 侧的 UUID 让对端能认出来。

---

## §9 anchor：18 行，但结论最有意思

### §9.1 机制

`drivers/virtio/virtio_anchor.c`(18)，**2 个导出符号**：

| 符号 | 位置 | 作用 |
|---|---|---|
| `virtio_require_restricted_mem_acc()` | `drivers/virtio/virtio_anchor.c:9` | **一个"总是返回真"的函数** |
| `virtio_check_mem_acc_cb` | `drivers/virtio/virtio_anchor.c:18` | **一个全局函数指针，默认指向"总是返回假"** |

**用法**：需要受限内存访问的平台在启动早期把
`virtio_check_mem_acc_cb` 改成 `virtio_require_restricted_mem_acc`，
**然后 `virtio_features_ok()` 就会强制要求 `VIRTIO_F_ACCESS_PLATFORM`**
（03 §A5、`drivers/virtio/virtio.c:177` 的 `if (virtio_check_mem_acc_cb(dev))`）。

### §9.2 ⚠️ 谁真的用它——arm64 **不在其中**

```bash
grep -rlw 'virtio_require_restricted_mem_acc' --include=*.c .
```

| 文件 | 平台 |
|---|---|
| `arch/x86/mm/mem_encrypt_amd.c` | **AMD SEV（机密虚拟机）** |
| `arch/s390/mm/init.c` | **s390 保护虚拟化** |
| — `drivers/virtio/virtio_anchor.c` | 实现本身 |

⇒ **只有 AMD SEV 和 s390 PV 会打开这条路。**
⚠️ **arm64 在 6.1.158 上不在这个列表里** ——
**所以本套（arm64-only）的读者实际上碰不到这条强制路径**。

**顺带一个更宽的统计**（`#include <linux/virtio_anchor.h>` 的文件，8 个）：
除上面三个，还有 `drivers/virtio/virtio.c`（消费方）、
`drivers/xen/grant-dma-ops.c`、`arch/arm/xen/enlighten.c`、
`arch/x86/xen/enlighten_pv.c`、`arch/x86/xen/enlighten_hvm.c`
⇒ **Xen 也参与这套机制**（它走的是另一个入口，改 DMA ops 而不是强制 feature）。

⚠️ **注意 `arch/arm/xen/` 是 arm32，不是 arm64** ——
**这条要小心，别把 arm 和 arm64 混为一谈**（99 §6 记了这条）。

**为什么这 18 行值得单独一节**：它是 **arm64-only 视角下
"存在但用不上"的典型例子** ——如果不做这个统计，很容易在文档里写成
"virtio 在机密计算场景下会强制 ACCESS_PLATFORM"，
**而读者在 arm64 上永远看不到这个行为**。

---

## §10 汇总表

### §10.1 子框架 × 类别 × 挂载点 × arm64 相关

| 子框架 | 类别 | 挂载点 | arm64 |
|---|---|---|---|
| PCI 传输层 | **层 1** | PCI probe | **相关** |
| MMIO 传输层 | **层 1** | platform probe（`drivers/virtio/virtio_mmio.c:831`）/ cmdline | **相关** |
| vDPA 传输层 | 层 1 | vDPA 总线 | 有硬件才相关 |
| vringh | ⚠️ **对端侧**，不是层 1 也不是层 2 | **不在 virtio 时间线上** | vhost 场景 |
| balloon | 层 2 | `drv->probe()` | 相关 |
| virtio_mem | 层 2 | 同上 | **相关**（`depends on ARM64`） |
| virtio_input | 层 2 | 同上 | 相关 |
| dma-buf | 辅助 | 设备驱动按需调 | virtio-gpu 才相关 |
| anchor | 辅助 | **架构初始化**（比 virtio 核心早） | ⚠️ **不相关**（§9.2） |

### §10.2 全树使用统计（每条都能重跑）

| 框架 | 口径 | 值 | 重跑命令 |
|---|---|---|---|
| 设备驱动总数 | **`struct virtio_driver X = {`**（必须带 `0-9`） | **25** | `grep -rn 'struct virtio_driver [a-z_0-9]* = {' --include=*.c . \| wc -l` |
| 传输层总数 | `struct virtio_config_ops X = {` | **9** | `grep -rn 'struct virtio_config_ops [a-z_]* = {' --include=*.c . \| wc -l` |
| PCI modern 库的消费者 | `vp_modern_probe` 类符号的文件数 | **4** | `grep -rlw 'vp_modern_probe\|virtio_pci_modern_probe' --include=*.c . \| wc -l` |
| vringh 消费者 | `vringh_init_*` 文件数 | **4** | `grep -rlw 'vringh_init_kern\|vringh_init_user\|vringh_init_iotlb' --include=*.c . \| wc -l` |
| dma-buf 消费者 | `virtio_dma_buf_export` 文件数 | **2**（含实现 ⇒ 真消费者 **1**） | `grep -rlw 'virtio_dma_buf_export' --include=*.c . \| wc -l` |
| anchor 的**强制路径**使用者 | `virtio_require_restricted_mem_acc` 文件数 | **3**（含实现 ⇒ 真使用者 **2**：AMD SEV、s390 PV） | `grep -rlw 'virtio_require_restricted_mem_acc' --include=*.c . \| wc -l` |
| anchor 的**广义**参与者 | `#include <linux/virtio_anchor.h>` 文件数 | **8** | `grep -rln 'linux/virtio_anchor.h' --include=*.c . \| wc -l` |
| uapi 规范头文件 | — | **27** | `ls include/uapi/linux/virtio_*.h \| wc -l` |
| DT 绑定 | `Documentation/devicetree/bindings/virtio/` | **3 篇 209 行** | `wc -l Documentation/devicetree/bindings/virtio/*.yaml` |

⚠️ **两条口径警告**（99 §6 详记）：
1. **设备驱动数必须用 `[a-z_0-9]*`**：用 `[a-z_]*` 会漏掉 2 个
   （`drivers/i2c/busses/i2c-virtio.c`、`net/9p/trans_virtio.c`，变量名带数字）⇒ 得到 23 而不是 25
2. **anchor 有两个口径**：**强制路径使用者 2 个**（AMD SEV / s390 PV）
   vs **广义参与者 8 个**（含 Xen 的另一条路）——**别把两个口径混着报**

### §10.3 裁掉了什么

| 裁掉的 | 为什么 |
|---|---|
| **`drivers/vhost/vringh.c`(1511) 的实现** | **它实现的是 device 角色，超出本套"只覆盖 driver 侧"的边界**（§4、01 §0.1）。只交代它存在和连接点 |
| **s390 CCW 传输层** | 非目标架构（`drivers/s390/virtio/virtio_ccw.c:1021` 只在 01 §1.1 点名） |
| **UML 传输层** | 同上（`arch/um/drivers/virtio_uml.c:1094`） |
| **BlueField tmfifo 传输层** | 同上（`drivers/platform/mellanox/mlxbf-tmfifo.c:1088`） |
| **AMD SEV / s390 PV 的机密计算细节** | **§9.2 已确认 arm64 不走这条路** ⇒ 只讲机制存在，不展开 |
| **22 个住在别处的设备驱动** | 它们各自属于自己的子系统（net / blk / fs / …）。**`virtio_net` 会在网卡驱动那套文档里再遇到** |
| **legacy（0.9.X）路径的寄存器细节** | §2.2 的表里标了哪些是 legacy 专用，但**不展开 legacy 的完整流程** |
| **balloon / mem / input 的内部算法** | 它们是设备驱动不是框架；**`virtio_mem` 那 5 条设计债只列位置不解释 MM 细节**（99 §2.3 登记为三等证据） |
