---
title: 设备树
---

# remoteproc 06：设备树怎么写、谁消费、配错了怎么定位

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 按任务找章节

| 你想干的事 | 去哪一节 |
|---|---|
| 我只想知道 remoteproc **核心**读哪些属性 | §1.1（答案是 **1 条**） |
| 我要换固件文件名 | §2 |
| 我要给远端分内存（固件段 / vring / buffer） | §3 |
| 我要配门铃（内核 ↔ 远端互相通知） | §4 |
| 我在看某个厂商的 `xxx,yyy` 属性 | §5 |
| 我想抄一份能跑的 arm64 完整例子 | §6 |
| 配好了但不工作，怎么定位 | §7 |
| dmesg 里有一句报错，想知道是哪条属性错了 | §8 |
| 绑定文档里写了但没人读的属性（死字） | §9 |

---

## §1 三方分工与属性总表

### §1.1 remoteproc 核心只读一条 DT 属性

```
                    ┌──────────────────────────────────────┐
   DTS 里的属性  →  │  谁把它变成行为？                     │
                    └──────────────────────────────────────┘

  compatible / reg / interrupts / clocks / resets / power-domains
        │
        └──→ ① 通用 DT + 驱动模型（不是 remoteproc 的事）

  firmware-name
        │
        └──→ ② remoteproc 核心   唯一的一条

  memory-region
        │
        ├──→ ③ reserved-memory 框架（drivers/of/）
        └──→ ④ 各个 remoteproc 驱动（自己解析、自己登记 carveout）

  mboxes / mbox-names
        │
        └──→ ⑤ mailbox 框架（drivers/mailbox/）

  ti,* / fsl,* / st,* / qcom,* …
        │
        └──→ ④ 只有对应的那一个驱动
```

| 消费方 | 读什么 | 位置 |
|---|---|---|
| ② remoteproc 核心 | **只有 `firmware-name`** | `drivers/remoteproc/remoteproc_core.c:1000` |
| ③ reserved-memory 框架 | `memory-region`（按索引） | `drivers/of/of_reserved_mem.c:342` |
| ③ reserved-memory 框架 | `memory-region-names`（按名字） | `drivers/of/of_reserved_mem.c:392` |
| ⑤ mailbox 框架 | `mbox-names` | `drivers/mailbox/mailbox.c:422`、`drivers/mailbox/mailbox.c:428` |

**这一节是本篇最重要的结论**：`remoteproc_core.c` 全文只有一处 `of_property_read_*`
（`drivers/remoteproc/remoteproc_core.c:1000`，读 `firmware-name`）。
⇒ **"remoteproc 的设备树绑定"这个说法本身就不准确** ——
DT 里那一堆属性，绝大多数是各家驱动自己的约定，核心根本不看。
⇒ 排查 DT 问题时，**先确认是谁该读这条属性**，再去那个消费者里 grep，不要在核心里找。

### §1.2 核心真正会读的属性总表

| 属性 | 写在哪个节点 | 类型 | 必需 | 消费代码 | 缺失时 |
|---|---|---|---|---|---|
| `firmware-name` | **远端处理器自己的节点** | string（可为 string-list，按 index 取） | 否 | `drivers/remoteproc/remoteproc_core.c:1000` | 返回 `-EINVAL`，驱动通常回退到编译期默认名 |

就这一条。下面 §3–§5 讲的都是"驱动 / 框架读、核心不读"的属性。

### §1.3 remoteproc 侧属性全表（按消费者分组）

| 属性 | 写在哪个节点 | 消费者 | 消费代码 |
|---|---|---|---|
| `firmware-name` | 远端节点 | 核心 | `drivers/remoteproc/remoteproc_core.c:1000` |
| `memory-region` | 远端节点 | reserved-memory + 各驱动 | `drivers/of/of_reserved_mem.c:342`、`drivers/remoteproc/stm32_rproc.c:222`、`drivers/remoteproc/imx_rproc.c:449`、`drivers/remoteproc/ti_k3_r5_remoteproc.c:987` |
| `memory-region`（**父节点**，索引 0） | ⚠️ virtio 平台设备的父 = 远端节点 | `remoteproc_virtio.c` 兜底 | `drivers/remoteproc/remoteproc_virtio.c:429` |
| `mboxes` | 远端节点 | mailbox 框架 | `drivers/remoteproc/stm32_rproc.c:382`、`drivers/remoteproc/imx_rproc.c:680`、`drivers/remoteproc/ti_k3_r5_remoteproc.c:396` |
| `mbox-names` | 远端节点 | mailbox 框架 | `drivers/mailbox/mailbox.c:422` |
| `wakeup-source` | 远端节点 | 只有 stm32 | `drivers/remoteproc/stm32_rproc.c:712` |
| `st,auto-boot` | 远端节点 | 只有 stm32 | `drivers/remoteproc/stm32_rproc.c:754` |
| `fsl,auto-boot` | 远端节点 | 只有 i.MX | `drivers/remoteproc/imx_rproc.c:831` |
| `syscon` | 远端节点 | 只有 i.MX | `drivers/remoteproc/imx_rproc.c:726` |
| `sram` | 远端节点 | 只有 TI K3 R5F | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1457`、`:1469` |
| `ti,cluster-mode` | **集群节点**（父） | 只有 TI K3 R5F | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1742` |
| `ti,atcm-enable` / `ti,btcm-enable` / `ti,loczrama` | 远端节点 | 只有 TI K3 R5F | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1555`、`:1562`、`:1569` |
| `ti,sci-proc-ids` | 远端节点 | 只有 TI K3 | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1512` |

注意 `ti,cluster-mode` 写在**父节点**（`r5fss@...`）而不是核心节点（`r5f@...`）。
这是 06 篇「写在哪个节点」这一列存在的理由 —— 写错层级的属性会被静默忽略。

---

## §2 `firmware-name`：核心唯一读的属性

### §2.1 语义

```dts
mcu_r5fss0_core0: r5f@41000000 {
	compatible = "ti,am654-r5f";
	firmware-name = "am65x-mcu-r5f0_0-fw";
};
```

| 事实 | 出处 |
|---|---|
| 读取函数 `rproc_of_parse_firmware()` | `drivers/remoteproc/remoteproc_core.c:996-1004` |
| 用 `of_property_read_string_index()`，所以**支持 string-list + index** | `drivers/remoteproc/remoteproc_core.c:1000` |
| 属性缺失时返回 `-EINVAL` | `drivers/remoteproc/remoteproc_core.c:1000` 的返回值直接透传 |
| 本树只有 4 个驱动调它 | `drivers/remoteproc/imx_dsp_rproc.c:1081`、`drivers/remoteproc/mtk_scp.c:831`、`drivers/remoteproc/ti_k3_dsp_remoteproc.c:689`、`drivers/remoteproc/ti_k3_r5_remoteproc.c:1219` |

⚠️ **这里有个反直觉的点**：`rproc_of_parse_firmware()` 是核心提供的，
但**核心自己不调它**。是**驱动**在 probe 里调，然后把结果传给 `rproc_alloc()`。
⇒ 一个驱动如果没调这个函数（本树 34 个驱动里有 30 个没调），
**它的 DTS 写 `firmware-name` 完全无效**，固件名只能来自驱动里的编译期字符串。
这是"死字属性"的第一类（§9）。

判断你的平台支不支持：`grep rproc_of_parse_firmware drivers/remoteproc/<你的驱动>.c`。

### §2.2 运行期改固件名的正路

不依赖 DT：

```sh
echo other-fw.elf > /sys/class/remoteproc/remoteproc0/firmware
```

| 事实 | 出处 |
|---|---|
| sysfs 入口 | `drivers/remoteproc/remoteproc_sysfs.c:149`、`:156` |
| 核心实现 `rproc_set_firmware()` | `drivers/remoteproc/remoteproc_core.c:2167` |
| ⚠️ 远端不在 offline 状态时会失败 | 01 §10.1 |

所以 `firmware-name` 的定位是"开机默认值"，不是"唯一入口"。
调试期换固件走 sysfs 更方便，不用改 DTS 重新编译。

---

## §3 `memory-region`：本子系统最容易配错的地方

### §3.1 三种内存，用途完全不同

| 用途 | 谁在用 | 典型大小 | 必须 `no-map`？ |
|---|---|---|---|
| ① **固件段**（代码 + 数据） | ELF 加载器往里写（04 §2） | MB 级 | 是 |
| ② **vring**（virtio 的描述符环） | 核心自动分配（04 §1.3） | KB 级 | 是 |
| ③ **vdev buffer**（rpmsg 的数据缓冲） | `rproc_add_virtio_dev()`（04 §1.7） | 几百 KB | 是 |

②③ 通常合在一个 "DMA memory region" 里，①单独一块。
TI 的绑定文档把这个约定写得最清楚（`Documentation/devicetree/bindings/remoteproc/ti,k3-r5f-rproc.yaml` 的 `memory-region` 描述段）：
**至少两块**，第一块给"vring 和 vring buffer 这类动态 DMA 分配"，第二块给"固件镜像段"。

### §3.2 索引顺序有语义，不能随便换

TI 驱动的读法（`drivers/remoteproc/ti_k3_r5_remoteproc.c:957`、`:987`）：

```c
	num_rmems = of_property_count_elems_of_size(np, "memory-region", sizeof(phandle));
	/* … */
	for (i = 0; i < num_rmems; i++) {
		rmem_np = of_parse_phandle(np, "memory-region", i + 1);	/* i + 1 */
		/* … */
	}
```

注意那个 **`i + 1`** —— 它**跳过了索引 0**。
索引 0 那块留给 reserved-memory 框架自动绑定（也就是 DMA 池），
从索引 1 开始才是驱动手工登记成 carveout 的固件段。

⇒ **DTS 里两块内存的顺序不能颠倒**：
```dts
	memory-region = <&..._dma_memory_region>,	/* 索引 0：DMA 池，给 vring + buffer */
			<&..._memory_region>;		/* 索引 1：固件段 */
```

⚠️ 颠倒之后会怎样？固件段被当成 DMA 池、DMA 池被当成 carveout。
表现是"固件加载报地址错，或者加载成功但远端跑飞" —— **没有一句明确的日志说"你顺序反了"**。

### §3.3 静默失效决策树：virtio buffer 落在哪块内存上？

```
                 rproc_add_virtio_dev() 找名为 "vdev%dbuffer" 的 carveout
                                    │
              ┌─────────────────────┴─────────────────────┐
            找到了                                     没找到
              │                                           │
    ┌─────────┴─────────┐                    ┌────────────┴────────────┐
 of_resm_idx != -1   of_resm_idx == -1        从【父节点】的 memory-region
    │                     │                    索引 0 继承
    │                     │                            │
 走 reserved-memory   走 dma_declare_        ⚠️ 返回值不检查！
 正路（分支 A）        coherent_memory       失败也继续走下去
                      （分支 B）              （分支 C）
```

| 分支 | 代码位置 | 说明 |
|---|---|---|
| A | `drivers/remoteproc/remoteproc_virtio.c:389-398` | `of_reserved_mem_device_init_by_idx()`，失败会报 `Can't associate reserved memory` |
| B | `drivers/remoteproc/remoteproc_virtio.c:400-417` | `mem->va` 已映射时先打一句 `vdev %d buffer already mapped`（`drivers/remoteproc/remoteproc_virtio.c:401`），然后 `dma_declare_coherent_memory()`（`drivers/remoteproc/remoteproc_virtio.c:410`） |
| C | `drivers/remoteproc/remoteproc_virtio.c:418-429` | ⚠️ **`of_reserved_mem_device_init_by_idx(dev, np, 0)` 的返回值完全没检查**（`drivers/remoteproc/remoteproc_virtio.c:429`） |

分支 C 是本子系统**最阴的一个静默失效**。三个条件叠加：

1. 你的驱动没有登记名为 `vdev0buffer` 的 carveout（大多数驱动都没有）；
2. 于是核心去父节点（= 你的远端 DT 节点）的 `memory-region` 拿**索引 0**；
3. **拿不到也不报错**，rpmsg 后面就在一块"随便哪里"的内存上跑。

⇒ **规避办法二选一**：
- 像 stm32 那样，在 `.parse_fw` 里用 `rproc_of_resm_mem_entry_init()` 显式登记一块名叫 `vdev0buffer` 的 carveout（05 §3.2）；
- 或者接受分支 C，但**确保 DTS 的 `memory-region` 索引 0 就是你想给 virtio 用的那块**。

⚠️ 顺带说清一件事：分支 C 里的 `np` 是**父设备的 of_node**。
virtio 平台设备本身没有 DT 节点（它是 `platform_device_register_data()` 造的，04 §1.2），
所以这里借用的是远端处理器节点。**这也解释了为什么 §3.2 的索引 0 那么重要**。

### §3.4 `no-map` 为什么必须写

```dts
	mcu_r5fss0_core0_memory_region: r5f-memory@a0100000 {
		compatible = "shared-dma-pool";
		reg = <0 0xa0100000 0 0xf00000>;
		no-map;
	};
```

`no-map` 让这块内存**不进入内核的线性映射**。
不写的后果是内核和远端可能用不同的 cache 属性访问同一块物理内存 ——
远端写了、内核读到的还是旧数据，或者反过来。
TI 的绑定文档明确要求（`Documentation/devicetree/bindings/remoteproc/ti,k3-r5f-rproc.yaml`，
`memory-region` 描述里那句「should be defined with a "no-map" property」）。
⚠️ 这个文件名带逗号，本套文档一律**不给它写行号**（`check_line_refs.py` 会按逗号切基名，见 99 §8）。

`compatible = "shared-dma-pool"` 是让它能被 `dma_alloc_coherent()` 用的关键；
纯固件段那块可以不写 compatible（本树 `rtos_ipc_memory_region` 就没写，
见 `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:63-67`）。

### §3.5 一个没人用的备选：`memory-region-names`

`drivers/of/of_reserved_mem.c:392` 提供了 `of_reserved_mem_device_init_by_name()`，
它读 `memory-region-names` 按名字匹配 —— 本来能彻底解决 §3.2 的顺序依赖问题。

⚠️ 但 **remoteproc 目录下没有一个驱动用它**
（`grep -rn memory-region-names drivers/remoteproc/` 无输出）。
⇒ 这是一个"框架已经提供、子系统还没跟上"的例子，属于 03 §C 那类设计债。

---

## §4 `mboxes` / `mbox-names`：门铃

### §4.1 两种取通道的方式，决定 DTS 要不要写名字

| 驱动的取法 | DTS 需要 `mbox-names`？ | 例子 |
|---|---|---|
| `mbox_request_channel(cl, index)` | **不需要** | `drivers/remoteproc/ti_k3_r5_remoteproc.c:396`（index 0） |
| `mbox_request_channel_byname(cl, "tx")` | **必须写** | `drivers/remoteproc/imx_rproc.c:680`、`:685` |
| `mbox_request_channel_byname(cl, name)` 循环 | 必须写 | `drivers/remoteproc/stm32_rproc.c:382` |

`mbox-names` 是 **mailbox 框架**读的，不是 remoteproc：
`drivers/mailbox/mailbox.c:422` 先判断属性是否存在，不存在就直接报
`… requires an "mbox-names" property`（`drivers/mailbox/mailbox.c:424`），
然后 `of_property_for_each_string()` 逐个比对（`drivers/mailbox/mailbox.c:428`）。

⇒ **DTS 里少写 `mbox-names` 的报错来自 `drivers/mailbox/`，不是 remoteproc**。
在 remoteproc 里 grep 这句话是找不到的 —— §8 的映射表里会点明。

### §4.2 一个通道还是多个通道

| 平台 | 通道数 | 名字 | 语义 |
|---|---|---|---|
| TI K3 R5F | 1 | 无（按 index） | 双向共用一个 |
| i.MX | 2 | `tx` / `rx` | 分方向 |
| STM32 | 多个 | 见绑定文档 | 分方向 + 分用途（kick / stop / detach） |

STM32 把 mailbox 用得最细：不只是 kick，
`stop` 和 `detach` 也各发一条 mailbox 消息
（`drivers/remoteproc/stm32_rproc.c:494`、`drivers/remoteproc/stm32_rproc.c:512`）。
⇒ 这类平台**少配一个通道，表现是"能启动不能停"**。

### §4.3 门铃和 vring 的对应关系

⚠️ DTS 里配几个 mailbox 通道，和固件资源表里有几个 vring，**是两件独立的事**。
连接点是驱动的 `.kick` 实现：核心传进来一个 `vqid`（= 资源表里回写的 notifyid，04 §1.3），
驱动决定往哪个 mailbox 通道上发。

⇒ 常见错误：固件声明了 2 个 vring（notifyid 0 和 1），
驱动的 `.kick` 无论 `vqid` 是多少都往同一个通道发。
**这在多数平台上是正确的**（远端自己查两个 vring），但如果远端固件按通道区分方向就会错。
⇒ **判据看远端固件，不看 DTS**。

---

## §5 厂商私有属性：只有一个驱动认

### §5.1 TI K3 R5F（arm64 上最常见的一套）

| 属性 | 写在哪个节点 | 默认值 | 消费代码 |
|---|---|---|---|
| `ti,cluster-mode` | **集群节点** `r5fss@…` | 无默认，必须写 | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1742` |
| `ti,atcm-enable` | 核心节点 `r5f@…` | 省略 = **禁用** | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1555` |
| `ti,btcm-enable` | 核心节点 | 省略 = **启用** | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1562` |
| `ti,loczrama` | 核心节点 | 省略 = **1（ATCM 在 0x0）** | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1569` |
| `ti,sci-proc-ids` | 核心节点 | 无默认 | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1512` |
| `sram` | 核心节点 | 可选，最多 4 个 | `drivers/remoteproc/ti_k3_r5_remoteproc.c:1457`、`:1469` |

⚠️ **`ti,atcm-enable` 和 `ti,btcm-enable` 的默认值相反**
（一个默认关、一个默认开），这个不对称写在绑定文档里但很容易看漏。
绑定文档还建议「Recommended to enable it for maximizing TCMs」——
所以实际的 DTS 基本都把两个都显式写成 1
（`arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:319-320`）。

`ti,loczrama` 决定"远端看到地址 0 是 ATCM 还是 BTCM"。
它和 `reg-names = "atcm", "btcm"` 一起决定了 §3 里 `da` 的取值 ——
**改了它，固件的链接脚本也要跟着改**。

### §5.2 i.MX

| 属性 | 语义 | 消费代码 |
|---|---|---|
| `syscon` | phandle 到控制远端复位/启动地址的 syscon 寄存器块 | `drivers/remoteproc/imx_rproc.c:726` |
| `fsl,auto-boot` | bool，写了就自动启动 | `drivers/remoteproc/imx_rproc.c:831` |
| `mbox-names = "tx", "rx"` | 见 §4.1 | `drivers/remoteproc/imx_rproc.c:680`、`:685` |

### §5.3 STM32（arm32，本篇只作对照）

| 属性 | 语义 | 消费代码 |
|---|---|---|
| `st,auto-boot` | bool，自动启动 | `drivers/remoteproc/stm32_rproc.c:754` |
| `wakeup-source` | 让远端的 mailbox 中断能唤醒系统 | `drivers/remoteproc/stm32_rproc.c:712` |

⚠️ STM32MP1 是 armv7，不在本套的 arm64 基准内（99 §2.7 有记录）。
列在这里只是因为它是**回调填得最全的参考实现**，05 §2 的骨架取自它。

### §5.4 `auto_boot`：三种来源，DT 只是其中一种

`rproc->auto_boot` 决定 `rproc_add()` 之后要不要立刻启动远端（03 §A5）。

| 来源 | 例子 | 位置 |
|---|---|---|
| **核心默认 = `true`** | 所有驱动 | `drivers/remoteproc/remoteproc_core.c:2457` |
| DT 属性 | `fsl,auto-boot` / `st,auto-boot` | `drivers/remoteproc/imx_rproc.c:831`、`drivers/remoteproc/stm32_rproc.c:754` |
| 驱动硬编码 `false` | i.MX DSP / PRU / q6v5-mss | `drivers/remoteproc/imx_dsp_rproc.c:1121`、`drivers/remoteproc/pru_rproc.c:811`、`drivers/remoteproc/qcom_q6v5_mss.c:1954` |
| 驱动按 SoC 变体查表 | 高通 | `drivers/remoteproc/qcom_q6v5_pas.c:496`（值来自 `desc->auto_boot`）、`drivers/remoteproc/qcom_q6v5_adsp.c:597` |
| 模块参数 | Ingenic（MIPS） | `drivers/remoteproc/ingenic_rproc.c:180` |

⚠️ **核心默认是 `true`**（`drivers/remoteproc/remoteproc_core.c:2457`）。
⇒ 如果你的驱动既不读 DT 也不显式赋值，**`rproc_add()` 会立刻尝试加载固件并启动远端**。
固件文件此时可能还不在 rootfs 上（`/lib/firmware` 还没挂载），
表现是开机时一句 `request_firmware failed` —— 但**远端后面还能通过 sysfs 手工启动**。
⇒ 想要"开机不自动启动"，驱动里必须显式 `rproc->auto_boot = false;`，**DT 里没有通用开关**。

---

## §6 完整的 arm64 实例（本树真实节点，可以 diff）

### §6.1 第一部分：reserved-memory（板级文件）

```dts
/* arch/arm64/boot/dts/ti/k3-am654-base-board.dts */
	reserved-memory {
		#address-cells = <2>;
		#size-cells = <2>;
		ranges;

		/* 索引 0：DMA 池 —— vring + vdev buffer 从这里出 */
		mcu_r5fss0_core0_dma_memory_region: r5f-dma-memory@a0000000 {
			compatible = "shared-dma-pool";
			reg = <0 0xa0000000 0 0x100000>;	/* 1 MB */
			no-map;
		};

		/* 索引 1：固件段 */
		mcu_r5fss0_core0_memory_region: r5f-memory@a0100000 {
			compatible = "shared-dma-pool";
			reg = <0 0xa0100000 0 0xf00000>;	/* 15 MB */
			no-map;
		};
	};
```

| 元素 | 行号 |
|---|---|
| `reserved-memory` 节点开头 | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:28` |
| DMA 池（索引 0） | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:39-43` |
| 固件段（索引 1） | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:45-49` |
| core1 的两块 | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:51-61` |
| 一块**不带** `compatible` 的（纯 IPC） | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:63-67` |

### §6.2 第二部分：远端节点（SoC 级文件）

```dts
/* arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi */
	mcu_r5fss0: r5fss@41000000 {
		compatible = "ti,am654-r5fss";
		ti,cluster-mode = <1>;			/* 写在集群节点上 */
		#address-cells = <1>;
		#size-cells = <1>;
		ranges = <0x41000000 0x00 0x41000000 0x20000>,
			 <0x41400000 0x00 0x41400000 0x20000>;
		power-domains = <&k3_pds 129 TI_SCI_PD_EXCLUSIVE>;

		mcu_r5fss0_core0: r5f@41000000 {
			compatible = "ti,am654-r5f";
			reg = <0x41000000 0x00008000>,	/* ATCM */
			      <0x41010000 0x00008000>;	/* BTCM */
			reg-names = "atcm", "btcm";
			ti,sci = <&dmsc>;
			ti,sci-dev-id = <159>;
			ti,sci-proc-ids = <0x01 0xff>;
			resets = <&k3_reset 159 1>;
			firmware-name = "am65x-mcu-r5f0_0-fw";	/* 核心唯一读的属性 */
			ti,atcm-enable = <1>;
			ti,btcm-enable = <1>;
			ti,loczrama = <1>;
		};
	};
```

| 元素 | 行号 |
|---|---|
| 集群节点 | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:300` |
| `ti,cluster-mode` | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:302` |
| 核心节点 core0 | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:309` |
| `reg-names = "atcm", "btcm"` | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:313` |
| `resets` | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:317` |
| `firmware-name` | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:318` |
| 三个 TCM 属性 | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:319-321` |
| core1 | `arch/arm64/boot/dts/ti/k3-am65-mcu.dtsi:324` |

### §6.3 第三部分：板级补上 `memory-region` 和 `mboxes`

```dts
/* arch/arm64/boot/dts/ti/k3-am654-base-board.dts */
&mcu_r5fss0_core0 {
	memory-region = <&mcu_r5fss0_core0_dma_memory_region>,
			<&mcu_r5fss0_core0_memory_region>;
	mboxes = <&mailbox0_cluster0 &mbox_mcu_r5fss0_core0>;
};
```

| 元素 | 行号 |
|---|---|
| override 块 | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:483` |
| `memory-region` 两项 | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:484-485` |
| `mboxes` | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:486` |
| core1 的 `mboxes` | `arch/arm64/boot/dts/ti/k3-am654-base-board.dts:492` |

**这个三段分工是 arm64 上的标准做法，值得记住**：

| 层 | 放什么 | 为什么 |
|---|---|---|
| SoC `.dtsi` | 寄存器、复位、电源域、TCM 配置、`firmware-name` | 芯片决定的，所有板子一样 |
| 板级 `.dts` reserved-memory | 具体的物理地址和大小 | 取决于这块板子有多少 DDR |
| 板级 `.dts` override | `memory-region` + `mboxes` | 取决于板子的内存划分和 IPC 方案 |

另一个 arm64 例子（同样的三段结构）：
`arch/arm64/boot/dts/ti/k3-j721e-sk.dts:1110-1113`。

高通的形状完全不同（PAS 方案，固件由 TrustZone 加载）：
`arch/arm64/boot/dts/qcom/sdm845.dtsi:734`，
它的 `memory-region` 只有一块（`arch/arm64/boot/dts/qcom/sdm845.dtsi:747`），
门铃走 `qcom,smem-states` 而不是 `mboxes`
（`arch/arm64/boot/dts/qcom/sdm845.dtsi:751-752`），
rpmsg 走单独的 `glink-edge` 子节点（`arch/arm64/boot/dts/qcom/sdm845.dtsi:756`）。
⇒ **别把 TI 的写法照搬到高通平台上**。

---

## §7 定位：三层排查

### §7.1 第一层：编译期（DTS 还没进内核）

| 检查 | 命令 |
|---|---|
| DTS 语法 + 绑定校验 | `make dtbs_check DT_SCHEMA_FILES=Documentation/devicetree/bindings/remoteproc/` |
| 属性名拼错 | 上一条能查出来（绑定 yaml 里 `additionalProperties: false`） |
| ⚠️ 属性写错层级 | **查不出来** —— 父/子节点的 schema 都允许 `additionalProperties`，见 §7.4 |

TI 的 R5F 绑定确实写了 `additionalProperties: false`
（`Documentation/devicetree/bindings/remoteproc/ti,k3-r5f-rproc.yaml` 顶层），
所以拼错的属性名会被 `dtbs_check` 抓到。但**不是所有绑定都这么严**。

### §7.2 第二层：运行期静态（内核起来了，看设备有没有建出来）

```sh
# 1. remoteproc 设备建出来了吗？
ls /sys/class/remoteproc/
# → 空的：驱动没 probe 成功，看 dmesg

# 2. probe 到哪一步失败的？
dmesg | grep -i -E 'rproc|remoteproc'

# 3. DT 节点真的被 probe 了吗？
ls /sys/firmware/devicetree/base/            # 看节点在不在
ls /sys/bus/platform/devices/ | grep -i r5f  # 看设备建出来了没

# 4. reserved-memory 生效了吗？
cat /proc/iomem | grep -i -E 'reserved|dma'
dmesg | grep -i 'reserved memory'
```

### §7.3 第三层：运行期动态（设备在，但行为不对）

```sh
cd /sys/kernel/debug/remoteproc/remoteproc0/

# carveout 对不对：看 da / dma / len 和你 DTS 里写的一致吗
cat carveout_memories

# 资源表回写对不对：vring 的 da 不该是 0，notifyid 该是 0 和 1
cat resource_table

# 固件的日志
cat trace0
```

**`carveout_memories` 是把 DTS 和实际行为对起来的唯一窗口**。
`dma` 列应该等于你 reserved-memory 里的物理地址；
`da` 列是远端看到的地址，应该和固件的链接脚本一致。
两者不一致就是 §3.2 的顺序问题或者驱动的 `pa_to_da` 换算问题。

### §7.4 ⚠️ 属性写错层级：三层都查不出来

举例：把 `ti,cluster-mode` 从集群节点挪到核心节点。

| 层 | 会不会报错 |
|---|---|
| `dtbs_check` | 可能报（如果核心节点的 schema 是 `additionalProperties: false`） |
| probe | **不报** —— `of_property_read_u32(np, "ti,cluster-mode", …)` 在集群节点上读不到，驱动按默认值走 |
| 运行期 | **不报** —— 远端可能"启动了但配置不对" |

⇒ **唯一可靠的检查办法**：`cat /sys/firmware/devicetree/base/<路径>/<属性>` 逐条对，
或者在驱动的读取点加 `dev_info`。绑定文档里「写在哪个节点」这一列（§1.3）就是为此存在的。

---

## §8 错误消息 → 原因映射表

| dmesg 里的话 | 出自 | 最可能的 DT 原因 |
|---|---|---|
| `… requires an "mbox-names" property` | `drivers/mailbox/mailbox.c:424` | 驱动用 `byname` 取通道，DTS 少写了 `mbox-names` |
| `unable to acquire memory-region` | `drivers/remoteproc/stm32_rproc.c:227` | `memory-region` 指向的节点不在 `reserved-memory` 下 |
| `memory region not valid %pa` | `drivers/remoteproc/stm32_rproc.c:233` | 物理地址不在远端能寻址的范围内 |
| `Can't associate reserved memory` | `drivers/remoteproc/remoteproc_virtio.c:396` | vdev buffer 那块 reserved-memory 没有 `compatible = "shared-dma-pool"` |
| `vdev %d buffer already mapped` | `drivers/remoteproc/remoteproc_virtio.c:401` | 通常无害（分支 B），但说明 carveout 已被映射过 |
| `Failed to associate buffer` | `drivers/remoteproc/remoteproc_virtio.c:414` | `dma_declare_coherent_memory()` 失败，地址/大小不合法 |
| `.kick method not defined for %s` | `drivers/remoteproc/remoteproc_virtio.c:380` | 不是 DT 问题 —— 驱动没填 `.kick`（05 §1.1） |
| `too many vrings: %d` | `drivers/remoteproc/remoteproc_core.c:502-505` | 不是 DT 问题 —— 固件资源表里 vring 数超了 |
| `vdev rsc is truncated` | `drivers/remoteproc/remoteproc_core.c:487-490` | 不是 DT 问题 —— 固件资源表本身坏了 |
| `invalid qsz (%d) or alignment (%d)` | `drivers/remoteproc/remoteproc_core.c:396-400` | 不是 DT 问题 —— 固件资源表里 vring 参数为 0 |
| `request_firmware failed` 类 | `firmware_class` | `firmware-name` 指的文件不在 `/lib/firmware`，或 `auto_boot=true` 时 rootfs 还没挂上（§5.4） |
| `Elf class is not set` | `drivers/remoteproc/remoteproc_coredump.c:251-254` | 不是 DT 问题 —— 驱动没调 `rproc_coredump_set_elf_info()`（04 §3.1） |
| **什么都没打，但 rpmsg 不通** | —— | §3.3 分支 C：vdev buffer 落在了意料之外的内存上 |

表里**一半的报错跟 DT 无关**。这正是 §1.1 那个结论的实际后果：
remoteproc 的失败模式主要来自**固件的资源表**和**驱动的回调**，DT 只占一小部分。
⇒ 看到报错先查这张表的「出自」列，别默认是 DTS 写错了。

---

## §9 死字属性：写了也没人读

| 属性 | 为什么是死字 | 判据 |
|---|---|---|
| `firmware-name` 在 30/34 个驱动上 | 只有 4 个驱动调 `rproc_of_parse_firmware()`（§2.1） | `grep -l rproc_of_parse_firmware drivers/remoteproc/*.c` 只有 4 个文件 |
| `memory-region-names` | 框架支持（`drivers/of/of_reserved_mem.c:392`），remoteproc 目录 **0 个使用者** | `grep -rn memory-region-names drivers/remoteproc/` 无输出 |
| `st,auto-boot` 在非 STM32 平台 | 只有 `drivers/remoteproc/stm32_rproc.c:754` 读 | 全树只有 1 处 |
| `fsl,auto-boot` 在非 i.MX 平台 | 只有 `drivers/remoteproc/imx_rproc.c:831` 读 | 全树只有 1 处 |
| **通用的 auto-boot 开关** | **不存在** —— 核心不读任何 DT 属性来决定 `auto_boot`（§5.4） | `drivers/remoteproc/remoteproc_core.c` 只有一处 `of_property_read_*` |

⚠️ 最后一条值得单独强调：很多人会想"DTS 里加个 `auto-boot` 就能控制自动启动"——
**在 remoteproc 里没有这个通用属性**。要么用厂商私有的（只有两家有），
要么改驱动，要么开机后用 sysfs 手工启停。

---

## §10 本篇覆盖情况

| 内容 | 状态 |
|---|---|
| 核心读的属性 | 全部（就 1 条），有定位到行的消费代码 |
| reserved-memory 相关 | 完整，含三个分支的静默失效决策树 |
| mailbox 相关 | 完整，指明报错来自 `drivers/mailbox/` |
| 厂商私有属性 | 只覆盖了 arm64 上的主流三家（TI / i.MX / 高通）+ STM32 作对照。本树共 25 个绑定文件（含 6 个老式 `.txt`），其余未逐个展开 |
| ⚠️ ACPI | 本套 arm64-only，**ACPI 一律裁掉**（记录在 99 §2.7）。remoteproc 本身也没有 ACPI 绑定 |

⚠️ 本篇的 DTS 片段都是从本树**原样摘录**的（§6 三段都给了行号），
可以用 `sed -n` 逐条对照。§3.2 那段 C 代码同样是原样摘录。

---

**下一篇**：07 篇讲编译期开关、tracepoint、sysfs/debugfs、故障注入和错误码分诊。
