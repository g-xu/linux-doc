---
title: 设备树
---

# virtio 06：发现路径与设备树

## §0 发现路径分诊表

⚠️ **别先问"virtio 有没有 DT 绑定"** ——
**virtio 有五条并存的发现路径，而 DT 只覆盖其中一条半。**

| # | 发现路径 | 谁扫描 | 走 DT 吗 | arm64 用不用 | 本篇覆盖到哪 |
|---|---|---|---|---|---|
| 1 | **PCI 传输层** | PCI 总线 probe（`vendor 0x1af4`） | **不走**（PCI 自己枚举） | **主路之一** | §0.3 说明，§5 讲 pci-iommu 那半条 |
| 2 | **MMIO + DT** | platform 总线，`compatible = "virtio,mmio"` | **走** | **主路之一** | **§1–§4 全覆盖** |
| ⚠️ 3 | **MMIO + 内核命令行** | `virtio_mmio.device=` 参数 | **不走** | 调试/裸机 | §6 |
| 4 | **virtio 设备子节点** | `virtio_device_of_init()`（`drivers/virtio/virtio.c:366`） | **走** | **两条主路都会用** | **§7 —— 本篇最容易被漏掉的一节** |
| 5 | **其它传输层** | vDPA 总线 / remoteproc 资源表 / CCW / UML | **不走** | vDPA 才会 | ⚠️ 不覆盖（§8.3 登记） |

### §0.1 三篇 DT 绑定文档

```bash
wc -l Documentation/devicetree/bindings/virtio/*.yaml
```

| 文件 | 行数 | 讲什么 | 本篇 |
|---|---|---|---|
| `Documentation/devicetree/bindings/virtio/mmio.yaml` | **67** | **MMIO 传输层的节点** | **§1–§4** |
| `Documentation/devicetree/bindings/virtio/virtio-device.yaml` | **41** | **virtio 设备的子节点** | **§7** |
| `Documentation/devicetree/bindings/virtio/pci-iommu.yaml` | 101 | **virtio-iommu 挂在 PCI 上的特殊情况** | §5 |
| — 合计 | **209** | — | — |

**另外两篇住在别处**（都是路径 4 的实例）：
`Documentation/devicetree/bindings/i2c/i2c-virtio.yaml` ·
`Documentation/devicetree/bindings/gpio/gpio-virtio.yaml`

### §0.2 为什么 PCI 路径不需要 DT

**PCI 自己就有枚举机制**：配置空间里有 vendor/device id，
不需要 DT 描述。⇒ **虚拟机里 virtio-pci 设备是"插上就能被发现"的。**

⚠️ **但有两个例外要写 DT**：
① **virtio-iommu**（§5）——它要被别的设备引用，必须有 phandle；
② **给 virtio 设备加子节点**（§7）——那需要一个 DT 节点作为父。

### §0.3 按任务找章节

| 我想… | 去 |
|---|---|
| 抄一个能用的 virtio-mmio 节点 | §4 |
| 知道核心到底读哪些属性 | §1 |
| 搞明白 `iommus` / `#iommu-cells` | §3 |
| 给一个 virtio 设备加 DT 子节点 | **§7** |
| 用命令行凭空造设备 | §6 |
| 设备没被发现 | §9 |
| 看到报错想知道什么意思 | §10 |

---

## §1 核心真正会读的属性总表

**以 `mmio.yaml`（67 行）为准**，逐条给消费者：

| 属性 | 写在哪个节点 | 必需 | 谁消费 | 消费位置 |
|---|---|---|---|---|
| `compatible` | **virtio-mmio 设备节点** | **是** | platform 匹配 | `virtio_mmio_match[]`（`drivers/virtio/virtio_mmio.c:817-818`），挂在 `:836` 的 `.of_match_table` |
| `reg` | 同上 | **是** | 传输层 ioremap | `drivers/virtio/virtio_mmio.c`（`devm_platform_ioremap_resource` 一路） |
| `interrupts` | 同上 | **是**（`maxItems: 1`） | 传输层 request_irq | 同上 |
| `dma-coherent` | 同上 | 否 | **通用 DT 层**，不是 virtio 消费 | `of_dma_configure()`（of 那套的 API 篇讲了它） |
| `iommus` | 同上 | 否 | **通用 IOMMU-DT 层** | 见 §3 |
| `#iommu-cells` | **虚拟的 iommu 节点上** | **是 virtio-iommu 时必需** | IOMMU 层 + `drivers/iommu/virtio-iommu.c` | 见 §3 |
| `wakeup-source` | 同上 | 否 | **把 irq 设成唤醒源** | 见 §2.3 |
| `compatible = "virtio,deviceNN"` | ⚠️ **virtio-mmio 节点的子节点** | — | **virtio 核心** | `virtio_device_of_init()`（`drivers/virtio/virtio.c:366-400`），见 §7 |

**`required` 段只有三条**（`mmio.yaml` 的 `required:` 段）：
`compatible` · `reg` · `interrupts`。

⚠️ **`additionalProperties` 不是 `false`，是 `type: object`**
（`mmio.yaml` 的 `additionalProperties:` 段）：

**这一条极其特殊**：它的意思是"**额外的属性必须是对象（也就是子节点）**"。
⇒ **这正是为了让 §7 的 `virtio,deviceNN` 子节点合法**。
⚠️ **和 of 那套讲的 `additionalProperties: false`（拼错属性会被 `dtbs_check` 抓）不同**——
**virtio-mmio 的绑定故意放宽了这一条**，所以**拼错的标量属性会被 `dtbs_check` 报**，
但**多写的子节点不会**。

---

## §2 三个必填属性

### §2.1 `compatible = "virtio,mmio"`

```dts
	virtio@3000 {
		compatible = "virtio,mmio";
	};
```

**绑定里是 `const: virtio,mmio`** —— **只有这一个值，不能写别的**。

**驱动侧的匹配表只有一项**：

```c
static const struct of_device_id virtio_mmio_match[] = {
	{ .compatible = "virtio,mmio", },
	{},
};
```
—— `drivers/virtio/virtio_mmio.c:817-820`

### §2.2 `reg` 和 `interrupts`

| 属性 | 约束 | 说明 |
|---|---|---|
| `reg` | **`maxItems: 1`** | **一段寄存器窗口**，通常 0x100 或 0x200 字节 |
| `interrupts` | **`maxItems: 1`** | **只有一个中断** ——所有队列共用 |

⚠️ **"只有一个中断"是 MMIO 和 PCI 的关键差别**：
PCI 可以用 MSI-X 给每个队列一个向量，**MMIO 只有一根线**
⇒ **中断处理函数要读 `VIRTIO_MMIO_INTERRUPT_STATUS`(0x060) 判断
"是队列事件还是配置变更"**（04 §2.2）。

**`reg` 的长度要够**：04 §2.2 那张表里最大的偏移是 `0x0bc`
（`VIRTIO_MMIO_SHM_BASE_HIGH`）⇒ **至少要 0x100**。
绑定的例子给的就是 `reg = <0x3000 0x100>`。

### §2.3 `wakeup-source`

**绑定的原话**（`mmio.yaml` 的 `wakeup-source:` 段）：

> Required for setting irq of a virtio_mmio device as wakeup source.

⇒ **让这个设备的中断能把系统从睡眠中唤醒。**
⚠️ **它是 `type: boolean`** ⇒ 写了就是真，**删掉才是假**（写 `= <0>` 也是真）。

---

## §3 `iommus` 与 `#iommu-cells`：virtio-iommu 的特殊性

### §3.1 两个方向的属性

| 属性 | 写在哪 | 含义 |
|---|---|---|
| `iommus = <&viommu 23>` | **被保护的设备节点上** | **"我的访问要经过这个 IOMMU，我的端点 ID 是 23"** |
| `#iommu-cells = <1>` | **virtio-iommu 自己的节点上** | **"引用我时要带 1 个参数（端点 ID）"** |

**绑定文档的原话**：
- `iommus`：`Required for devices making accesses thru an IOMMU.`
- `#iommu-cells`：`Required when the node corresponds to a virtio-iommu device.`

### §3.2 完整例子（绑定文档自带，可直接抄）

```dts
	virtio@3000 {
		compatible = "virtio,mmio";
		reg = <0x3000 0x100>;
		interrupts = <41>;

		/* Device has endpoint ID 23 */
		iommus = <&viommu 23>;
	};

	viommu: iommu@3100 {
		compatible = "virtio,mmio";
		reg = <0x3100 0x100>;
		interrupts = <42>;

		#iommu-cells = <1>;
	};
```
—— `Documentation/devicetree/bindings/virtio/mmio.yaml` 的 `examples` 段

⚠️ **两个节点的 `compatible` 一模一样**（都是 `virtio,mmio`）——
**区别只在 `#iommu-cells` 的有无**。
⇒ **"这是不是一个 IOMMU"由属性决定，不由 `compatible` 决定**，
因为**设备类型编号是运行期从寄存器 `VIRTIO_MMIO_DEVICE_ID`(0x008) 读出来的**（04 §2.2）。

**这是 virtio 的 DT 绑定和普通硬件绑定最不一样的一点**：
**DT 不知道这个 virtio 节点是什么设备** ——它只知道"这里有个 virtio-mmio 传输窗口"。

### §3.3 PCI 上的 virtio-iommu：另一篇绑定

`Documentation/devicetree/bindings/virtio/pci-iommu.yaml`(101 行)。

**要解决的问题**：virtio-iommu 如果是个 **PCI 设备**，它本来不需要 DT
（§0.2），**但别的设备要用 phandle 引用它** ⇒ 必须给它造一个 DT 节点。

⇒ **本篇不展开**（§8.3 登记）——arm64 虚拟机上更常见的是 MMIO 形态的 virtio-iommu。

---

## §4 完整的 arm64 实例（本树真实节点，可以 diff）

```bash
sed -n '223,227p' arch/arm64/boot/dts/arm/foundation-v8.dtsi
```

```dts
	virtio@130000 {
		compatible = "virtio,mmio";
		reg = <0x130000 0x200>;
		interrupts = <42>;
	};
```
—— `arch/arm64/boot/dts/arm/foundation-v8.dtsi:223-227`

**本树 arm64 下一共 3 个文件有 virtio-mmio 节点**：

```bash
grep -rl 'virtio,mmio' arch/arm64/boot/dts/
```

| 文件 | 说明 |
|---|---|
| `arch/arm64/boot/dts/arm/foundation-v8.dtsi` | **ARM Foundation 模型** |
| `arch/arm64/boot/dts/arm/rtsm_ve-motherboard.dtsi` | RTSM 虚拟平台（`:225-229`，内容一致） |
| `arch/arm64/boot/dts/arm/rtsm_ve-motherboard-rs2.dtsi` | RTSM 的 RS2 变体 |

⇒ **三个都是 ARM 官方的模拟/虚拟平台** ——这符合直觉：
virtio 设备是虚拟的，只有虚拟平台的 dts 里才会写死。

**四点值得注意**：

| 观察 | 说明 |
|---|---|
| **只有三个属性** | **正好就是 `required` 的那三条**（§1）——**健康的节点就该这么短** |
| **`reg` 长度是 `0x200`** | 够覆盖 04 §2.2 那张表里最大的偏移 `0x0bc` |
| **中断是裸数字 `42`** | 因为这个平台的中断控制器声明了 `#interrupt-cells = <1>`（`arch/arm64/boot/dts/arm/foundation-v8.dtsi:128`）；**在用 GIC 的平台上（`#interrupt-cells = <3>`）要写成 `<GIC_SPI 42 IRQ_TYPE_LEVEL_HIGH>`** |
| **没有 `iommus`、没有子节点** | 最基础的形态 |

⚠️ **注意它是 SPI 而不是 PPI** ——virtio-mmio 是一个外设，
不是每 CPU 的（和 timer 的 4 个 PPI 正好相反）。

**真实的虚拟机场景里节点通常由 VMM 动态生成**（QEMU 的 `-M virt`
会造一批 virtio-mmio 窗口），而不是写在内核树的 dts 里
⇒ **所以上面这三个文件是"能 diff 的样本"，不是"生产配置"。**

## §5 PCI 路径：不需要 DT，除了一个例外

**PCI 上的 virtio 设备靠 PCI 枚举**（§0.2），DT 里通常一个字都不用写。

**唯一的例外是 virtio-iommu**（§3.3）——它需要被 phandle 引用。

⚠️ **还有一个 DT 相关的细节**：`virtio_device_of_init()` 的注释
（`drivers/virtio/virtio.c:392-395`）专门提到 powerpc/pseries 上
virtio 设备是 PCI 设备，PCI 的 vendor/device id 起了 `compatible` 的作用
⇒ **那种情况下核心刻意不初始化 `of_node`**。

---

## §6 ⚠️ 内核命令行：凭空造设备

**要 `CONFIG_VIRTIO_MMIO_CMDLINE_DEVICES`**（`drivers/virtio/Kconfig:158`）。

### §6.1 格式与机制

```
	virtio_mmio.device=<size>@<base>:<irq>[:<id>]
```

**解析在 `vm_cmdline_set()`**（`drivers/virtio/virtio_mmio.c:703`）：

| 步 | 代码 | 说明 |
|---|---|---|
| ① 第一次调用时注册一个父设备 | `device_register(&vm_cmdline_parent)`（`drivers/virtio/virtio_mmio.c:738`），静态定义在 `:696` | **所有命令行设备的公共父** |
| ② 凭空造 platform 设备 | `platform_device_register_resndata(&vm_cmdline_parent, "virtio-mmio", vm_cmdline_id++, ...)`（`drivers/virtio/virtio_mmio.c:753-754`） | **不经过 DT，直接构造资源** |
| ③ 编号递增 | `vm_cmdline_id`（`:701`） | — |

⇒ **造出来的 platform 设备名是 `virtio-mmio`**，
正好被 `virtio_mmio_driver`（`:831`）匹配上。

### §6.2 ⚠️ Kconfig 自己给的警告

**`drivers/virtio/Kconfig:163-164` 的 help 文本原话**：

> Be aware that using incorrect parameters (base address in particular)
> can crash your system - you have been warned.

⇒ **写错基地址会直接崩** ——因为驱动会去 ioremap 并读那个地址的魔数
（`drivers/virtio/virtio_mmio.c:623` 读 `VIRTIO_MMIO_MAGIC_VALUE`）。
**这条路是调试/裸机用的，不该出现在生产配置里。**

**还能反向读回来**：`vm_cmdline_get()`（`drivers/virtio/virtio_mmio.c:774`）
让 `/sys/module/virtio_mmio/parameters/device` 可读。

---

## §7 virtio 设备的 DT 子节点（本篇最容易被漏掉的一节）

### §7.1 要解决的问题

**有些 virtio 设备本身是一条"总线"**：virtio-i2c 下面挂 i2c 从设备，
virtio-gpio 下面挂 gpio 消费者。
⚠️ **但 virtio 设备是 hypervisor 提供的，它下面挂什么 hypervisor 不知道。**

⇒ **解法：在 DT 里给那个 virtio-mmio 节点加一个子节点**，
子节点的 `compatible` 用 `virtio,deviceNN` 声明"我描述的是设备类型 NN"，
里面再写平台侧的信息。

### §7.2 `compatible` 的格式：**十六进制**

**绑定文档的正则**（`virtio-device.yaml` 的 `compatible:` 段）：

```
	pattern: "^virtio,device[0-9a-f]{1,8}$"
```

**描述原话**：
> "virtio,deviceID", where ID is the virtio device id. The textual
> representation of ID shall be in lower case hexadecimal with leading
> zeroes suppressed.

⇒ **三条硬要求**：**小写**、**十六进制**、**不补前导零**。

**核心侧的对应代码**（`drivers/virtio/virtio.c:388`）：

```c
	ret = snprintf(compat, sizeof(compat), "virtio,device%x", dev->id.device);
```

⇒ **`%x` 正好是小写十六进制不补零** ——两边严格对上。

**换算表**（`include/uapi/linux/virtio_ids.h`）：

| 设备 | 十进制 ID | 位置 | DT 里写 |
|---|---|---|---|
| SCMI | 32 | `include/uapi/linux/virtio_ids.h:61` | `virtio,device20` |
| I2C adapter | **34** | `include/uapi/linux/virtio_ids.h:63` | **`virtio,device22`** |
| GPIO | 41 | `include/uapi/linux/virtio_ids.h:70` | `virtio,device29` |
| balloon | 5 | `include/uapi/linux/virtio_ids.h:36` | `virtio,device5` |
| input | 18 | `include/uapi/linux/virtio_ids.h:47` | `virtio,device12` |
| mem | 24 | `include/uapi/linux/virtio_ids.h:53` | `virtio,device18` |

⚠️ **十进制/十六进制搞混是本节最容易犯的错**：
I2C 是 34 ⇒ **写 `virtio,device34` 是错的**（那会被当成 0x34 = 52）。
**必须写 `virtio,device22`。**

### §7.3 完整例子（绑定文档自带）

```dts
	virtio@3000 {
		compatible = "virtio,mmio";
		reg = <0x3000 0x100>;
		interrupts = <43>;

		i2c {
			compatible = "virtio,device22";	/* 0x22 = 34 = I2C adapter */
		};
	};
```
—— `Documentation/devicetree/bindings/virtio/virtio-device.yaml` 的 `examples` 段

**子节点里可以继续写这个设备类型自己的绑定要求的东西**
（`virtio-device.yaml` 的 `additionalProperties: true`）——
**比如 virtio-i2c 下面再挂真正的 i2c 从设备节点**。

### §7.4 ⚠️ 三条硬约束

| 约束 | 依据 | 违反的后果 |
|---|---|---|
| ⚠️ **只能有一个子节点** | `WARN_ON(count > 1)`（`drivers/virtio/virtio.c:381` 一带，注释 `There can be only 1 child node`） | **`WARN` + 返回 `-EINVAL`** ⇒ 设备注册失败 |
| **父节点必须有 DT 节点** | `dev_of_node(dev->dev.parent)` 为 NULL 就直接返回 0（`drivers/virtio/virtio.c:371-372`） | **PCI 路径上通常没有** ⇒ 这条机制用不上 |
| **`compatible` 不匹配不算错** | `ret = 0; goto out;`（`drivers/virtio/virtio.c:397-399`） | **静默不关联** ⇒ 你的子节点被忽略且**没有任何日志** |

⚠️ **第三条是本节最阴的一个坑**：`virtio,device34` 写错成十进制
⇒ **不报错、不 WARN，只是 `of_node` 没被设上** ⇒
子节点里的所有信息都被忽略，而设备照常工作（只是少了那些平台信息）。
**判据见 §9.3。**

---

## §8 本篇覆盖情况

### §8.1 覆盖了什么

| 内容 | 覆盖 |
|---|---|
| `mmio.yaml` 的全部 7 个属性 | **每条都有消费者说明**（§1 的表） |
| `virtio-device.yaml` 的全部内容 | **§7**（含十六进制换算表和三条硬约束） |
| 五条发现路径 | **§0 分诊表**，其中 1/2/3/4 展开 |
| 可抄的节点 | §3.2（绑定自带）· §4（最小可用）· §7.3（带子节点） |

### §8.2 ⚠️ 没有覆盖的

| 没覆盖 | 为什么 |
|---|---|
| `pci-iommu.yaml`(101 行) | arm64 虚拟机上更常见的是 MMIO 形态的 virtio-iommu（§3.3） |
| `i2c-virtio.yaml` / `gpio-virtio.yaml` | **它们是 §7 那条机制的实例**，形状已在 §7.3 讲清；具体属性属于 i2c/gpio 子系统 |
| PCI 侧的 MSI-X 与 DT 无关的部分 | 属于 PCI 子系统 |

### §8.3 显式裁掉的

| 裁掉的 | 为什么 |
|---|---|
| **vDPA / remoteproc / CCW / UML 的发现路径** | **都不走 DT**（§0 的路径 5）；且 CCW/UML 非目标架构（04 §10.3） |
| **ACPI 路径** | **本套 arm64-only 限定在 DT**；virtio-mmio 走 platform 总线，理论上能被 ACPI 枚举，但**本套不覆盖** |
| powerpc/pseries 的特殊处理 | 非目标架构（§5 只引注释） |

---

## §9 定位：三层排查

### §9.1 第一层：编译期

```bash
# ① 绑定校验
make dtbs_check DT_SCHEMA_FILES=virtio/mmio.yaml

# ② 看编译出来的 DTB
dtc -I dtb -O dts your.dtb | grep -A6 'virtio'
```

⚠️ **`mmio.yaml` 的 `additionalProperties` 是 `type: object` 不是 `false`**（§1）
⇒ **多写的子节点不会被 `dtbs_check` 报**，
**所以 §7 的 `compatible` 写错（十进制/十六进制）编译期抓不到。**

### §9.2 第二层：设备有没有被创建

| 检查 | 命令 | 期望 |
|---|---|---|
| 总线上有设备吗 | `ls /sys/bus/virtio/devices/` | **`virtio0` / `virtio1` …**（03 §A2 的命名） |
| 设备类型对吗 | `cat /sys/bus/virtio/devices/virtio0/device` | 应该等于 `VIRTIO_ID_*` |
| 绑上驱动了吗 | `ls -l /sys/bus/virtio/devices/virtio0/driver` | **没有这个符号链接 ⇒ 没匹配上驱动** |
| platform 设备造出来了吗（MMIO） | `ls /sys/bus/platform/devices/ \| grep -i virtio` | — |
| 状态字节 | `cat /sys/bus/virtio/devices/virtio0/status` | **正常应是 `0x0000000f`** —— 格式是 `0x%08x`（`drivers/virtio/virtio.c:34`），03 §A7 有累积过程 |
| 协商结果 | `cat /sys/bus/virtio/devices/virtio0/features` | 一串 0/1（`drivers/virtio/virtio.c:58` 生成） |

⚠️ **`status` 不是 `0x0000000f` 就说明启动序列中断了**（下表按低 8 位说）：

| 值 | 卡在哪 |
|---|---|
| `0x1` | 只到 ACKNOWLEDGE ⇒ **没匹配到驱动** |
| `0x3` | 到 DRIVER ⇒ **feature 协商失败** |
| `0xb` | 到 FEATURES_OK ⇒ **`drv->probe()` 失败** |
| 带 `0x80` | **FAILED**（`drivers/virtio/virtio.c:468` 或 `:320`） |
| 带 `0x40` | **NEEDS_RESET —— 设备自己报告坏了**（03 §A7） |

### §9.3 第三层：DT 子节点有没有被关联（§7 那个静默坑）

**这是本篇最需要专门检查的一件事**，因为它**完全静默**：

```bash
# 看那个 virtio 设备有没有 of_node
ls -l /sys/bus/virtio/devices/virtio0/of_node
#   有符号链接 ⇒ virtio_device_of_init() 关联成功
#   ⚠️ 没有       ⇒ 三种可能（§7.4）：
#       ① 父节点没有 DT 节点（PCI 路径正常如此）
#       ② compatible 写错了（最常见：写了十进制）
#       ③ 根本没写子节点

# 反查：设备类型编号是多少，换成十六进制看对不对
cat /sys/bus/virtio/devices/virtio0/device        # 十进制
printf 'virtio,device%x\n' $(cat /sys/bus/virtio/devices/virtio0/device)
#   输出的就是 DT 里应该写的那个字符串
```

⇒ **最后那条 `printf` 是本篇最实用的一条命令** ——
它直接给出"这个设备的子节点 `compatible` 该写什么"。

---

## §10 错误消息 → 原因映射表

| 消息 | 出处 | 原因 |
|---|---|---|
| ⚠️`WARN` + 设备注册失败 | `WARN_ON(count > 1)`（`drivers/virtio/virtio.c:381` 一带） | **virtio-mmio 节点下写了超过一个子节点**（§7.4） |
| 系统在启动早期崩 | `drivers/virtio/virtio_mmio.c:623` 读魔数 | **命令行的基地址写错**（§6.2，Kconfig 自己警告过） |
| `virtio: device refuses features: %x` | `drivers/virtio/virtio.c:197-198` | **设备拒绝了协商结果**（03 §A5）—— 不是 DT 问题 |
| `device must provide VIRTIO_F_VERSION_1` | `drivers/virtio/virtio.c:180` | 受限内存访问场景下设备太老（03 §A5）；⚠️ **arm64 上一般不会触发**（04 §9.2） |
| `device must provide VIRTIO_F_ACCESS_PLATFORM` | `drivers/virtio/virtio.c:186` | 同上 |
| ⚠️ **子节点被忽略但没有任何消息** | `drivers/virtio/virtio.c:397-399` 的静默 `goto out` | **`compatible` 写错（十进制 vs 十六进制）**（§7.4 第三条）⇒ 只能靠 §9.3 查 |

⚠️ **这张表最重要的是最后一行**：virtio 的 DT 错误里
**最常见的那一种（`virtio,deviceNN` 写错）完全没有日志**。
⇒ **所以 §9.3 的 `printf` 检查不是可选的。**
