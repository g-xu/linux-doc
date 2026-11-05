---
title: 总览
---

# 00 mailbox 子系统总览

> **这一篇的定位：复习入口。**
> 隔几个月回来，只读这一篇就能恢复对 mailbox 的全貌认识；
> 需要细节再按 §8 的导读表下钻。
>
> **范围**：Linux 6.1.158（`f6e38ae624cf`），**arm64 only**。
> ACPI PCC（`drivers/mailbox/pcc.c`）已裁掉，理由和裁剪清单见 `99-evidence.md` §2.7。
>
> **基线**：`git status --porcelain -- drivers/mailbox include/linux/mailbox*` 空输出
> ⇒ 本树的 mailbox 子系统**未被任何补丁改动**，可以按上游 6.1 理解。

---

## §1 它是什么，解决什么问题

### 1.1 一句话

> **mailbox 是"CPU 和 SoC 里另一颗处理器之间互相踢一脚"的通用抽象层。**

它不是总线，不是设备驱动框架，不定义任何数据格式。
它只做两件事：

1. 把"我要给对端发一条消息"变成一次**排队 + 异步提交**；
2. 在"这条消息确实发出去了"的时候**通知发起方**。

载荷内容、协议语义、超时重传，**全部不属于 mailbox 的职责**。

### 1.2 为什么需要它

现代 arm64 SoC 里除了 Cortex-A 主核，还有一堆别的处理器：

| 协处理器 | 干什么 | arm64 实例 |
|---|---|---|
| **SCP / SCMI 固件** | 时钟、电压、电源域、传感器 | Arm Juno、Amlogic、大多数 Arm 参考平台 |
| **BPMP** | 同上，NVIDIA 自己的一套 | Tegra186/194/234 |
| **M4 / DSP 协处理器** | 音频、传感器融合、低功耗待机 | i.MX8、MediaTek ADSP、Qualcomm ADSP/CDSP |
| **GCE（命令引擎）** | 帮显示/相机流水线批量写寄存器 | MediaTek MT8173/8183/8192/8195 |
| **VideoCore** | GPU + 固件服务 | BCM2711（树莓派 4） |

这些处理器和主核**不共享中断控制器，也不在同一条总线上被枚举**。
主核想跟它们说话，硬件上只有一条路：**共享一小块寄存器或内存，
再加一个能触发对方中断的机制**。

这个"能触发对方中断的机制"就是硬件 mailbox。
Linux 的 mailbox 子系统把各家硬件的差异藏起来，
对上只暴露五个函数（`01-api.md` §2）。

### 1.3 它在内核里的位置

```
   ┌───────────────────────────────────────────────────────────┐
   │  上层协议框架（真正懂"消息内容"的人）                        │
   │  arm_scmi · arm_scpi · tegra BPMP · rpmsg/SMD · remoteproc │
   │  mtk-cmdq-helper · bcm2835 firmware · …                    │
   └───────────────────────┬───────────────────────────────────┘
                           │  五个 API（mbox_request_channel / send_message / …）
   ┌───────────────────────▼───────────────────────────────────┐
   │  mailbox 核心   drivers/mailbox/mailbox.c   626 行          │
   │  只有：20 格指针队列 + 串行化 + txdone 通知 + DT 解析      │
   │  零 bus_type · 零 tracepoint · 零 sysfs · 零 debugfs      │
   └───────────────────────┬───────────────────────────────────┘
                           │  struct mbox_chan_ops（6 个回调）
   ┌───────────────────────▼───────────────────────────────────┐
   │  控制器驱动   drivers/mailbox/*.c   32 个 .c               │
   │  arm_mhu · arm_mhuv2 · tegra-hsp · imx-mailbox · mtk-cmdq  │
   │  · qcom-apcs-ipc · rockchip · bcm2835 · apple · …           │
   └───────────────────────┬───────────────────────────────────┘
                           │  readl/writel
                    ┌──────▼──────┐
                    │  硬件寄存器  │ ──中断──▶ 对端处理器
                    └─────────────┘
```

注意这张图里**没有 `struct bus_type`**。
mailbox 是**框架型子系统**（判据：`rg 'bus_type' drivers/mailbox/ include/linux/mailbox*.h`
零输出），消费者靠**调 API** 拿到服务，不靠总线匹配。
这是最容易被类比错的一点，`08-feynman.md` §1 专门讲它。

### 1.4 规模

| 部分 | 文件 | 行数 | 本文档集怎么读的 |
|---|---|---|---|
| 核心实现 | `drivers/mailbox/mailbox.c` | 626 | 全部详读 |
| 核心内部头 | `drivers/mailbox/mailbox.h` | 10 | 全部详读 |
| 消费者 API | `include/linux/mailbox_client.h` | 49 | 全部详读 |
| 控制器 API | `include/linux/mailbox_controller.h` | 140 | 全部详读 |
| **核心合计** | | **825** | **一等证据** |
| Kconfig | `drivers/mailbox/Kconfig` | 297 | 全部详读 |
| 控制器驱动 | `drivers/mailbox/*.c`（32 个） | 约 2.3 万 | 代表驱动详读，其余只读契约 |
| 官方文档 | `Documentation/driver-api/mailbox.rst` | 129 | 全部详读（三等证据） |
| DT 绑定 | `Documentation/devicetree/bindings/mailbox/` | 29 个文件 | 按需 |

**核心只有 825 行** —— 这是整个子系统最重要的一个数字。
mailbox 的复杂度**不在核心，在"核心什么都不管"这件事本身**：
配置错误、协议错误、方向错误全都不由核心发现（§5.5、`07-debug.md` §5.3）。

---

## §2 硬件背景：处理器间通信到底是怎么实现的

> 这一章不讲 Linux，只讲硬件和协议。
> 不了解这一层，后面所有"为什么核心要这样设计"的问题都答不上来。

### 2.1 最小可行的处理器间通信

两颗处理器 A 和 B 在同一颗 SoC 里，要互相通信，硬件上必须解决三件事：

| 要解决的问题 | 硬件手段 |
|---|---|
| ① **数据放哪** | 一块双方都能访问的存储：共享 SRAM、DDR 的一段，或专用寄存器 |
| ② **怎么让对方知道"有新东西了"** | 一个寄存器，A 写它 ⇒ B 收到中断 |
| ③ **怎么知道对方处理完了** | 同样的机制反向来一次，或者 A 轮询某个"忙/闲"位 |

**第 ② 件事就是 mailbox 硬件的全部职责。**
第 ① 件事有时也由 mailbox 硬件顺便提供（几个数据寄存器），
但更多时候是独立的共享内存。

一次典型交互的时序：

```
   主核 (Cortex-A)                          协处理器 (SCP/BPMP/M4)
        │                                            │
   ①    │ 往共享内存写请求                            │
        │  memcpy_toio(shmem, request)                │
        │                                            │
   ②    │ 敲 mailbox 寄存器                           │
        │  writel(BIT(n), TRIGGER) ────中断──────────▶│
        │                                            │ 醒来
        │                                            │ 读共享内存
   ③    │                                            │ 干活
        │                                            │ 写回应到共享内存
        │◀────────────中断──────── writel(...)        │ 敲反向 mailbox
        │                                            │
   ④    │ 中断处理里 memcpy_fromio(response, shmem)   │
        ▼                                            ▼
```

注意 ② 和 ③ 之间**没有任何数据经过 mailbox**。
mailbox 传的信息量是 1 bit：**"去看共享内存"**。
这正是 `08-feynman.md` §2 讲的"门铃"模型，
也是 mailbox 核心为什么把载荷做成不透明 `void *` 的根本原因。

### 2.2 硬件形态一：纯门铃（doorbell）

**最简单的一类。** 寄存器只有"触发"和"状态"，没有数据通道。

**实例：NVIDIA Tegra HSP 的 doorbell 部分**（`drivers/mailbox/tegra-hsp.c:39-42`）

```c
#define HSP_DB_TRIGGER	0x0     /* 写这里 ⇒ 对方收中断 */
#define HSP_DB_ENABLE	0x4     /* 哪些 master 允许敲我 */
#define HSP_DB_RAW	0x8     /* 原始状态 */
#define HSP_DB_PENDING	0xc     /* 谁敲了我（读它来分辨来源） */
```

Tegra 用一个"master ID"来区分谁敲谁（`drivers/mailbox/tegra-hsp.c:55-57`）：

```c
#define HSP_DB_CCPLEX		1   /* 主核集群 */
#define HSP_DB_BPMP		3   /* BPMP 协处理器 */
#define HSP_DB_MAX		7
```

⇒ `HSP_DB_PENDING` 是一个位图，第 N 位置 1 表示 master N 敲过门。
中断处理里读它、清它、然后按 master 分发
（`drivers/mailbox/tegra-hsp.c:183-193`）。

**纯门铃的特点：`mbox_send_message(chan, NULL)` 是常态。**
真实数据早就通过共享内存放好了。
`drivers/firmware/tegra/bpmp-tegra186.c:86` 就是这么写的。

### 2.3 硬件形态二：门铃 + 少量数据寄存器

**寄存器组里带 1~4 个 32 位数据槽。** 适合传"命令号 + 一两个参数"。

**实例 A：Arm MHU（Message Handling Unit）**（`drivers/mailbox/arm_mhu.c:16-25`）

```c
#define INTR_STAT_OFS	0x0      /* 状态：哪些位被置了 */
#define INTR_SET_OFS	0x8      /* 写 1 置位 ⇒ 触发对端中断 */
#define INTR_CLR_OFS	0x10     /* 写 1 清位 */

#define MHU_LP_OFFSET	0x0      /* low priority  通道 */
#define MHU_HP_OFFSET	0x20     /* high priority 通道 */
#define MHU_SEC_OFFSET	0x200    /* secure        通道 */
#define TX_REG_OFFSET	0x100    /* 发送方向的寄存器偏移 */

#define MHU_CHANS	3        /* 硬件固定 3 条通道 */
```

MHU 的巧妙之处：**"数据"就是那 32 个中断位本身**。
`arm_mhu.c:70` 的发送就一行：

```c
	writel_relaxed(*arg, mlink->tx_reg + INTR_SET_OFS);
```

⇒ 载荷是一个 `u32 *`，值被直接当成"要置哪些位"写进 `INTR_SET`。
接收方读 `INTR_STAT` 拿到同一个 `u32`（`arm_mhu.c:46`），清掉（`:52`）。
所以 MHU 能传 32 bit 数据，但**代价是这 32 bit 同时是 32 个独立的中断源**。

**实例 B：Broadcom BCM2835（树莓派）**（`drivers/mailbox/bcm2835-mailbox.c:80-90`）

```c
static int bcm2835_send_data(struct mbox_chan *link, void *data)
{
	struct bcm2835_mbox *mbox = bcm2835_link_mbox(link);
	u32 msg = *(u32 *)data;              /* :83  就地解引用成 u32 */

	spin_lock(&mbox->lock);
	writel(msg, mbox->regs + MAIL1_WRT); /* :86  一次写入就是一条消息 */
	dev_dbg(mbox->controller.dev, "Request 0x%08X\n", msg);
	spin_unlock(&mbox->lock);
	return 0;
}
```

这是"载荷是 `u32 *`"这一类的最干净范例。**记住这个签名差异** ——
它是 `mailbox-test` 那个 128 字节 `memcpy` 缺陷的根源（`07-debug.md` §4.5 ③）。

**实例 C：i.MX MU（Messaging Unit）**（`drivers/mailbox/imx-mailbox.c:35-57`）

i.MX 的 MU 是形态最丰富的一个，它在**同一块硬件上同时提供五种通道类型**：

```c
enum imx_mu_chan_type {
	IMX_MU_TYPE_TX		= 0, /* Tx           带数据的发送 */
	IMX_MU_TYPE_RX		= 1, /* Rx           带数据的接收 */
	IMX_MU_TYPE_TXDB	= 2, /* Tx doorbell  纯门铃发送 */
	IMX_MU_TYPE_RXDB	= 3, /* Rx doorbell  纯门铃接收 */
	IMX_MU_TYPE_RST		= 4, /* Reset */
};
```

寄存器分两组（`drivers/mailbox/imx-mailbox.c:43-57`）：控制寄存器
（`CR`/`GIER`/`GCR`/`TCR`/`RCR`）和状态寄存器（`SR`/`GSR`/`TSR`/`RSR`）。
数据槽有 4 个（`:29` `#define IMX_MU_NUM_RR 4`），
整个控制器最多 17 条通道（`:22` `#define IMX_MU_CHANS 17`）。

**这就是为什么 `#mbox-cells` 的含义每个控制器自己定。**
i.MX 的 cell 必须同时表达"类型 + 索引"，
而 Juno MHU 的 cell 只需要一个优先级编号。核心不管这件事
（`06-devicetree.md` §4）。

### 2.4 硬件形态三：门铃 + FIFO / 共享邮箱寄存器

**数据槽变成一个真正的 FIFO 或者一组"满/空"带标志的寄存器。**

**实例：Tegra HSP 的 shared mailbox 部分**（`drivers/mailbox/tegra-hsp.c:44-51`）

```c
#define HSP_SM_SHRD_MBOX		0x0
#define HSP_SM_SHRD_MBOX_FULL		BIT(31)   /* bit31 = "里面有货" */
#define HSP_SM_SHRD_MBOX_FULL_INT_IE	0x04
#define HSP_SM_SHRD_MBOX_EMPTY_INT_IE	0x08

#define HSP_SHRD_MBOX_TYPE1_TAG		0x40
#define HSP_SHRD_MBOX_TYPE1_DATA0	0x48
#define HSP_SHRD_MBOX_TYPE1_DATA1	0x4c
```

关键设计：**bit31 是"满"标志，低 31 位是数据**。
发送方写入时连带把 bit31 置 1；接收方读走后清 bit31 ⇒
产生 EMPTY 中断 ⇒ 发送方知道"可以发下一条了"。

这正是 mailbox 核心里 `TXDONE_BY_IRQ` 模式的硬件来源：
**"对方把邮箱掏空了"这件事本身能产生一个中断**。
Tegra HSP 因此在 `startup` 里把共享邮箱通道强制改成 IRQ 模式
（`drivers/mailbox/tegra-hsp.c:511`），而门铃通道保持原样 ——
因为**纯门铃在硬件上没有"发完了"这个事件**。

HSP 还有一个"维度"寄存器告诉软件这颗芯片上到底有几个各类资源
（`drivers/mailbox/tegra-hsp.c:31-37`）：

```c
#define HSP_INT_DIMENSIONING	0x380
#define HSP_nSM_SHIFT		0    /* 有几个 shared mailbox */
#define HSP_nSS_SHIFT		4    /* 几个 shared semaphore */
#define HSP_nAS_SHIFT		8    /* 几个 arbitrated semaphore */
#define HSP_nDB_SHIFT		12   /* 几个 doorbell */
#define HSP_nSI_SHIFT		16   /* 几个 shared interrupt */
```

⇒ 通道数是**运行期从硬件读出来的**，不是 DTS 写死的。

**实例：Arm MHUv2** —— 同一块硬件支持两种协议模式
（`drivers/mailbox/arm_mhuv2.c:10`）：

> `protocol modes: data-transfer and doorbell, to be used on those channel`

具体用哪种由 DTS 的 `arm,mhuv2-protocols` 属性指定
（`drivers/mailbox/arm_mhuv2.c:46`），
最多 124 个"通道窗口"（`:39` `#define MHUV2_CH_WN_MAX 124`）。
doorbell 模式下每个窗口的 32 位可以当 32 个独立门铃用
（`drivers/mailbox/arm_mhuv2.c:224-226` 的 `doorbell` 字段注释）。

### 2.5 arm64 上真实存在的 mailbox 硬件普查

用 in-tree DTS 反查（`/usr/bin/grep -rl '<compatible>' arch/arm64/boot/dts/`）：

| 硬件 | compatible | 驱动 | arm64 DTS 平台数 | 形态 |
|---|---|---|---|---|
| Arm MHU | `arm,mhu` | `arm_mhu.c` | 3 | 门铃+32位数据 |
| Arm MHUv2 | `arm,mhuv2` | `arm_mhuv2.c` | 1 | 双模式 |
| Amlogic MHU | `amlogic,meson-gxbb-mhu` | `arm_mhu.c` | 2 | 同 MHU |
| Tegra HSP | `nvidia,tegra186-hsp` | `tegra-hsp.c` | 1（`arch/arm64/boot/dts/nvidia/tegra186.dtsi:1185`） | 门铃 + 共享邮箱 |
| Qualcomm APCS | `qcom,*-apcs-apps-global` | `qcom-apcs-ipc-mailbox.c` | 多（`sm8250` 等 20+ dtsi） | 纯门铃 |
| i.MX MU | `fsl,imx8qm-mu` 等 | `imx-mailbox.c` | 1+ | 五类通道 |
| MediaTek GCE | `mediatek,mt8173-gce` 等 | `mtk-cmdq-mailbox.c` | 1+ | 命令队列 |
| Rockchip | `rockchip,rk3368-mailbox` | `rockchip-mailbox.c` | 1 | 门铃+数据 |
| Apple ASC | `apple,t8103-asc-mailbox` | `apple-mailbox.c` | 1 | FIFO |
| BCM2835 | `brcm,bcm2835-mbox` | `bcm2835-mailbox.c` | 1（树莓派） | 单 u32 |

全树 `arch/arm64/boot/dts/` 里 **`mboxes` 属性一共出现 183 次**
（`/usr/bin/grep -rn 'mboxes' arch/arm64/boot/dts/ | wc -l`）
⇒ mailbox 在 arm64 上是**普遍存在**的基础设施，不是边缘功能。

`07-debug.md` §1.3 有按 Kconfig `depends on` 做的完整 21 行普查，
包括哪些控制器在 arm64 上根本编不出来。

### 2.6 协议层：mailbox 之上跑的到底是什么

mailbox 只管踢一脚。真正的"内容"由上层协议定义。arm64 上四大类：

| 协议 | 谁定义的 | 载荷放哪 | 内核实现 | 典型用途 |
|---|---|---|---|---|
| **SCMI** | Arm 标准（v2.0/v3.0） | 共享内存（`shmem` 属性） | `drivers/firmware/arm_scmi/` | 时钟/电压/电源域/传感器/复位 |
| **SCPI** | Arm 早期私有 | 共享内存 | `drivers/firmware/arm_scpi.c` | 同上（已被 SCMI 取代） |
| **BPMP-ABI** | NVIDIA 私有 | 共享内存 IVC 环 | `drivers/firmware/tegra/` | 同上 + Tegra 专有 |
| **rpmsg / SMD** | virtio / Qualcomm | 共享内存环形缓冲 | `drivers/rpmsg/` | 跟 DSP/modem 跑多路复用信道 |

**四种协议的共同点：mailbox 传的都是"去看共享内存"。**
所以 DTS 里几乎总是 `mboxes` + `shmem` 成对出现。
Juno 的 SCMI 节点是最标准的范例
（`arch/arm64/boot/dts/arm/juno-scmi.dtsi:44-64`）：

```dts
	firmware {
		scmi {
			compatible = "arm,scmi";
			mbox-names = "tx", "rx";                     /* :47 */
			mboxes = <&mailbox 0 0 &mailbox 0 1>;        /* :48  两条通道 */
			shmem = <&cpu_scp_lpri0 &cpu_scp_lpri1>;     /* :49  两块共享内存 */
			…
			scmi_dvfs: protocol@13 {
				reg = <0x13>;
				mbox-names = "tx", "rx";             /* :61 */
				mboxes = <&mailbox 1 0 &mailbox 1 1>;/* :62  换高优先级通道 */
				shmem = <&cpu_scp_hpri0 &cpu_scp_hpri1>;
			};
```

注意三件事：

1. **`mboxes` 里出现两次 `&mailbox`** —— TX 一条、RX 一条。
   **mailbox 通道是单向的**，双向通信需要两条。
2. **`shmem` 也是两个** —— 和通道一一对应。
   `shmem` 由 **SCMI 驱动自己解析**，mailbox 核心完全不读它
   （`06-devicetree.md` §5.1）。
3. **同一个 `firmware/scmi` 节点下的子协议可以覆盖 `mboxes`** ——
   DVFS 走高优先级通道，因为它对延迟敏感。

而"纯门铃 + 私有共享内存"的另一种写法（Amlogic SCPI，
`arch/arm64/boot/dts/amlogic/meson-gx.dtsi:244-247`）：

```dts
	scpi {
		compatible = "amlogic,meson-gxbb-scpi", "arm,scpi-pre-1.0";
		mboxes = <&mailbox 1 &mailbox 2>;         /* :246  只有一个 cell */
		shmem = <&cpu_scp_lpri &cpu_scp_hpri>;    /* :247 */
```

对比 Juno 的 `<&mailbox 0 0>`（两个 cell）：
**同一个 `arm_mhu.c` 驱动，两个平台的 cell 个数不一样** ——
因为 Amlogic 用的是 `amlogic,meson-gxbb-mhu` 这个 compatible，
`#mbox-cells` 是 1。**cell 的个数和含义完全由控制器决定，
不是 mailbox 子系统的属性**。

### 2.7 硬件层面的两个"坑"，直接决定了软件设计

**坑一：很多硬件没有"发完了"这个信号。**

纯门铃硬件（Qualcomm APCS、Tegra HSP 的 doorbell 部分）在硬件上
**只有"触发"，没有"对方收到了"**。
⇒ 软件必须由**上层协议**来确认（对端回一条应答）。
⇒ 这就是 mailbox 核心里 `TXDONE_BY_ACK` 模式存在的唯一原因
（`03-internals.md` §A6、`08-feynman.md` §3）。

**坑二：触发寄存器的写入必须极快、且不能睡眠。**

`writel` 之后对端立刻就会被中断。如果软件在写之前做了什么慢动作
（比如 `msleep` 或走 I2C 读一个值），时序就散了。
⇒ 核心因此把 `send_data` 回调**放在 spinlock 内调用**
（`drivers/mailbox/mailbox.c:77`）。
这条约束是 mailbox 所有"不许睡眠"规则的唯一根因
（`08-feynman.md` §4）。MediaTek CMDQ 的
`kzalloc(sizeof(*task), GFP_ATOMIC)`（`drivers/mailbox/mtk-cmdq-mailbox.c:347`）
就是这条约束的实证。

---
## §3 概念模型：五个名词和它们的关系

### 3.1 五个名词

| 名词 | C 类型 | 是什么 | 谁创建 |
|---|---|---|---|
| **消费者** | 任意驱动 | 想跟协处理器说话的人 | —— |
| **客户端** | `struct mbox_client` | 消费者填的一张"我是谁、怎么通知我"的表 | 消费者**按值内嵌**在自己的结构体里 |
| **通道** | `struct mbox_chan` | 一条单向的硬件通信路径 | 控制器驱动分配数组，核心不分配 |
| **控制器** | `struct mbox_controller` | 一块 mailbox 硬件 + 它的所有通道 | 控制器驱动 |
| **操作集** | `struct mbox_chan_ops` | 6 个函数指针，控制器怎么操作硬件 | 控制器驱动（通常是 `static const`） |

**`mbox_client` 只有 7 个字段**
（`include/linux/mailbox_client.h:29-38`）：

```c
struct mbox_client {
	struct device *dev;                                        /* :30 */
	bool tx_block;                                             /* :31  要不要阻塞等 */
	unsigned long tx_tout;                                     /* :32  等多久（ms） */
	bool knows_txdone;                                         /* :33 */

	void (*rx_callback)(struct mbox_client *cl, void *mssg);    /* :35 */
	void (*tx_prepare)(struct mbox_client *cl, void *mssg);     /* :36 */
	void (*tx_done)(struct mbox_client *cl, void *mssg, int r); /* :37 */
};
```

**注意它不是 `struct device`，也不注册到任何地方。**
它就是一张参数表。这是 `08-feynman.md` §1 的主题。

### 3.2 关系图

```
  消费者驱动（如 arm_scmi）
  ┌────────────────────────────────────────────┐
  │ struct scmi_mailbox {                      │
  │     struct mbox_client cl;   按值内嵌     │
  │     struct mbox_chan  *chan; 指针，核心给 │
  │     …                                      │
  │ }                                          │
  └───────────┬────────────────────────────────┘
              │ ① mbox_request_channel(&cl, index)
              │    或 _byname(&cl, "tx")
              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  mailbox 核心                                               │
  │                                                            │
  │  static LIST_HEAD(mbox_cons);   全局唯一的注册表          │
  │  static DEFINE_MUTEX(con_mutex);                            │
  │        │                                                   │
  │        │  ② 按 DTS 里 mboxes 的 phandle 找 controller       │
  │        ▼                                                   │
  │  ┌──────────────────────────────────────────┐              │
  │  │ struct mbox_controller                    │              │
  │  │   dev            ← 控制器的 device        │              │
  │  │   ops            ← struct mbox_chan_ops * │              │
  │  │   chans[]  ─────┐  控制器驱动分配的数组 │              │
  │  │   num_chans     │                        │              │
  │  │   txdone_irq / txdone_poll                │              │
  │  │   of_xlate()    cell → 通道 的翻译函数  │              │
  │  │   poll_hrt      POLL 模式共用一个 hrtimer│             │
  │  └─────────────────┼────────────────────────┘              │
  │                    │  ③ of_xlate(mbox, spec) 挑出一条       │
  │                    ▼                                       │
  │  ┌─────────────────────────────────────────────┐           │
  │  │ struct mbox_chan                             │           │
  │  │   mbox        → 回指 controller              │           │
  │  │   cl          → ④ 这里被填上 &cl（=占用）  │           │
  │  │   txdone_method  位掩码，三种模式          │           │
  │  │   msg_data[20]   20 格裸指针队列           │           │
  │  │   msg_count / msg_free                       │           │
  │  │   active_req     当前在飞的那一条          │           │
  │  │   tx_complete    阻塞模式等这个            │           │
  │  │   lock           spinlock，per-channel     │           │
  │  │   con_priv       控制器驱动的私有指针      │           │
  │  └─────────────────────────────────────────────┘           │
  └────────────────────────────────────────────────────────────┘
              │ ⑤ ops->send_data(chan, mssg)   在 chan->lock 内
              ▼
  控制器驱动 → readl/writel → 硬件 → 中断 → 对端
```

图里的 ④ 是"占用"的全部含义：**`chan->cl` 非 NULL 就是被占了**
（`drivers/mailbox/mailbox.c:376` 检查它，`:379` 返回 `-EBUSY`）。
没有引用计数、没有共享、没有排队。

### 3.3 命名约定速查

| 前缀/后缀 | 含义 | 例子 |
|---|---|---|
| `mbox_*` | 对**消费者**的 API | `mbox_request_channel` `mbox_send_message` |
| `mbox_controller_*` | 对**控制器驱动**的 API | `mbox_controller_register` |
| `mbox_chan_*` | **控制器驱动**回调给核心的通知 | `mbox_chan_txdone` `mbox_chan_received_data` |
| `mbox_client_*` | **消费者**回调给核心的通知 | `mbox_client_txdone` |
| `ops->*` | 核心调**控制器** | `send_data` `startup` `shutdown` `last_tx_done` `peek_data` `flush` |
| `cl->*_callback` / `cl->tx_*` | 核心调**消费者** | `rx_callback` `tx_prepare` `tx_done` |

**最容易混的一对**：`mbox_chan_txdone`（控制器调，IRQ 模式）
vs `mbox_client_txdone`（消费者调，ACK 模式）。
名字里的 `chan`/`client` 指的是**谁在调**，不是操作对象。

---

## §4 数据结构速览

> 完整版（含每个字段"谁填谁读"）见 `02-datastructures.md`。这里只留骨架。

### 4.1 `struct mbox_chan` —— 状态全在这里

`include/linux/mailbox_controller.h`，核心运行期状态的唯一载体：

| 字段组 | 字段 | 谁写 | 作用 |
|---|---|---|---|
| **归属** | `mbox` | 核心（`:513-520` 注册时） | 回指控制器 |
| | `cl` | 核心（`:381` 占用 / `:459` 释放） | 非 NULL = 已被占用 |
| | `con_priv` | 控制器驱动 | 控制器自己的私有数据 |
| **队列** | `msg_data[20]` | 核心（`:40`） | 裸指针数组，**不拷贝载荷** |
| | `msg_count` / `msg_free` | 核心 | 环形队列的两个游标 |
| | `active_req` | 核心（`:79` 置 / `:100` 清） | 当前在飞的那一条；非 NULL ⇒ 不发下一条 |
| **完成通知** | `txdone_method` | 核心推导（`:493-498`）+ 少数驱动改写 | 位掩码：IRQ/POLL/ACK |
| | `tx_complete` | 核心 | 阻塞模式的 completion |
| | `cl->tx_block` / `cl->tx_tout` | 消费者 | 要不要等、等多久 |
| **锁** | `lock` | —— | per-channel spinlock |

三个"没有"：**没有引用计数、没有状态枚举、没有任何计数器**。
⇒ 这是 `07-debug.md` §5.3 那六行观测缺口的结构性原因。

### 4.2 `struct mbox_chan_ops` —— 六个回调，只有一个是必需的

| 回调 | 核心在哪调 | 上下文 | 必需？ |
|---|---|---|---|
| `send_data` | `drivers/mailbox/mailbox.c:77` | **spinlock 内，不许睡** | **是**（唯一无条件解引用） |
| `startup` | `:394` | `con_mutex` 内，可睡 | 否 |
| `shutdown` | `:465` | 可睡 | 否 |
| `last_tx_done` | `:129`（hrtimer 内） | 原子上下文 | POLL 模式必需（`:502-505` 会查） |
| `peek_data` | `mbox_chan_received_data` 路径外 | —— | 否 |
| `flush` | `:310` | 可睡 | 否（全树只有 4 个实现） |

**官方文档 `Documentation/driver-api/mailbox.rst:30` 说"除 `peek_data` 全部必需"，
这是错的** —— 核心只无条件解引用 `send_data`。
这条冲突记在 `99-evidence.md` §4。

### 4.3 全局状态：只有两个

```c
/* drivers/mailbox/mailbox.c:23-24 */
static LIST_HEAD(mbox_cons);        /* 所有已注册控制器 */
static DEFINE_MUTEX(con_mutex);     /* 保护上面那个链表 */
```

**`mbox_cons` 从来没有被任何接口 dump 出来。**
⇒「系统里注册了哪些 mailbox 控制器」这个问题在标准内核上无法回答
（`07-debug.md` §5.3）。

### 4.4 两把锁的关系

| 锁 | 范围 | 类型 | 保护什么 |
|---|---|---|---|
| `con_mutex` | 全局 | mutex（可睡） | `mbox_cons` 链表 + 通道占用/释放的串行化 |
| `chan->lock` | per-channel | spinlock_irqsave | 队列游标 + `active_req` + `send_data` 调用 |
| `mbox->poll_hrt_lock` | per-controller | spinlock_irqsave | POLL 模式的 hrtimer 启停 |

**唯一的嵌套**：`msg_submit` 在 `chan->lock` 内再取 `poll_hrt_lock`
（`drivers/mailbox/mailbox.c:87-89`）。反向不存在 ⇒ 不会 AB-BA。

**一个不对称**：`mbox_request_channel` 全程持 `con_mutex`（`:351-404`），
但 `mbox_free_channel` **不取 `con_mutex`**（只取 `chan->lock`，`:456-462`）。
申请串行、释放不串行 —— `03-internals.md` §B2 讨论这件事。

---

## §5 机制速览

> 完整版见 `03-internals.md`。这里只留"是什么 + 在哪一行"。

### 5.1 通道申请：一次遍历 + 一次翻译

```
  mbox_request_channel(cl, index)                      drivers/mailbox/mailbox.c:337
      │
      ├─ ① cl->dev / cl->dev->of_node 为 NULL？ → -ENODEV  pr_debug   :347-348
      ├─ ② of_parse_phandle_with_args("mboxes", "#mbox-cells", index)
      │       失败 → 原样返回 OF 的错误码       dev_dbg  :356-357
      ├─ ③ mutex_lock(&con_mutex)                                      :359
      ├─ ④ chan = ERR_PTR(-EPROBE_DEFER)        初值                 :361
      ├─ ⑤ 遍历 mbox_cons，比较 of_node 指针     不是 compatible      :363
      │       命中 → chan = mbox->of_xlate(mbox, &spec)
      │       失败 → 无任何日志，原样传出 of_xlate 的错误码       :371-374
      ├─ ⑥ chan->cl 非 NULL 或 try_module_get 失败 → -EBUSY dev_dbg   :376-379
      ├─ ⑦ 填 chan->cl、init_completion、按 knows_txdone 加 ACK 位       :381-390
      ├─ ⑧ ops->startup(chan)（若有）失败 → 唯一的 dev_err            :394-399
      └─ ⑨ mutex_unlock                                                :404
```

**四个失败出口里三个是 debug 级、一个完全无日志。**
默认 dmesg 上什么都看不到 —— 这是 mailbox 最大的排查障碍
（`07-debug.md` §2.1、§7.1）。

### 5.2 发送：入队 + 串行提交

```
  mbox_send_message(chan, mssg)                                     :256
      │
      ├─ add_to_rbuf(chan, mssg)                                :26-51
      │     队列满（20）→ -ENOBUFS + dev_err "Try increasing…"  :36 / :265
      │     成功 → 返回下标 idx                                    :50
      │
      ├─ msg_submit(chan)                                       :53-91
      │     active_req 非 NULL → 直接 goto exit（串行化）        :62-63
      │     tx_prepare + send_data 都在 chan->lock 内            :74-77
      │     成功 → active_req = data                             :79
      │     POLL 模式 → 立刻踢 hrtimer                           :85-90
      │
      └─ cl->tx_block ？                                           :271
            tx_tout == 0 ⇒ 等 3600000 ms（"wait forever"）        :275-276
            wait_for_completion_timeout                             :280
            超时 → t = -ETIME; tx_tick(chan, t)                     :281-283

  返回值 = 队列下标（非负）或负错误码            见 08-feynman.md §5
```

### 5.3 完成：`tx_tick` 是唯一出口

```
  tx_tick(chan, r)                                              :93-115
      ├─ active_req = NULL（锁内）                                :99-100
      ├─ msg_submit(chan)  立刻发下一条                         :104
      ├─ cl->tx_done(cl, mssg, r)  锁外，可睡                   :110-111
      └─ if (r != -ETIME && cl->tx_block) complete(…)         :113-114
```

**超时路径故意不 `complete()`** —— 因为等的那个人已经自己醒了。
这一条极容易看漏（`07-debug.md` §6.3）。

三个入口进 `tx_tick`：

| 模式 | 入口函数 | 谁调 |
|---|---|---|
| IRQ | `mbox_chan_txdone` `:176-185` | 控制器中断处理 |
| POLL | `txdone_hrtimer` `:117-146` | 核心 hrtimer（`:129` 问 `last_tx_done`） |
| ACK | `mbox_client_txdone` `:197-205` | 消费者 |

两条"配错了"的 `dev_err`：`:179-180`（控制器调了但不是 IRQ 模式）
和 `:200`（消费者调了但不是 ACK 模式）。**两条后面都 `return`** ⇒
`tx_tick` 不被调 ⇒ 后续必然 `-ETIME`。

### 5.4 接收：一条直路

```
  控制器中断 → mbox_chan_received_data(chan, mssg)               :155-165
      └─ cl->rx_callback(cl, mssg)   锁外                       :161
```

核心**不缓存 RX 消息、不排队、没有队列**。
消费者的 `rx_callback` 必须当场处理完。

### 5.5 一句话总结机制部分

> **mailbox 核心 = 20 格指针队列 + per-channel 串行化 + 三种"发完了"的通知路径 + DT phandle 解析。**
> 其余一切（载荷格式、协议、重传、超时策略、观测）都不在它的职责内。

---

## §6 端到端调用路径：一次 SCMI 时钟设置

把上面的机制串成一条真实的链（arm64 + Juno + SCMI）：

```
 用户态: cpufreq 调频
    │
 ▼ ── clk_set_rate(clk, rate)                     drivers/clk/clk.c
    │
 ▼ ── scmi_clk_set_rate()                         drivers/clk/clk-scmi.c
    │
 ▼ ── scmi_do_xfer(ph, xfer)                      drivers/firmware/arm_scmi/driver.c
    │      ① 把命令写进共享内存（shmem）
    │      ② 调 transport 的 send_message
    │
 ▼ ── mailbox_send_message(cinfo, xfer)            drivers/firmware/arm_scmi/mailbox.c:181
    │      ret = mbox_send_message(smbox->chan, xfer);         :187
    │      if (ret > 0) ret = 0;   ← 把"队列下标"抹成 0      :190-191
    │
 ▼ ══ mbox_send_message(chan, mssg)                drivers/mailbox/mailbox.c:256
    │      ├─ add_to_rbuf → 拿到下标                            :263
    │      ├─ msg_submit
    │      │     ├─ tx_prepare(cl, data)  ← SCMI 在这里 memcpy_toio  :74-75
    │      │     └─ ops->send_data(chan, data)                  :77
    │      └─ tx_block ⇒ wait_for_completion_timeout            :280
    │
 ▼ ── mhu_send_data(chan, data)                    drivers/mailbox/arm_mhu.c:70
    │      writel_relaxed(*arg, mlink->tx_reg + INTR_SET_OFS);
    │
 ▼ ══ 硬件：MHU INTR_SET 置位 ⇒ SCP 收到中断
    │
 ▼ ══ SCP 固件：读共享内存 → 设置 PLL → 写回应到共享内存 → 敲反向 MHU
    │
 ▼ ── mhu_rx_interrupt(irq, p)                     drivers/mailbox/arm_mhu.c:44
    │      读 INTR_STAT、清 INTR_CLR
    │      mbox_chan_received_data(chan, (void *)&val);
    │
 ▼ ══ mbox_chan_received_data                      drivers/mailbox/mailbox.c:155
    │      └─ cl->rx_callback(cl, mssg)                         :161
    │
 ▼ ── mailbox_rx_callback()                        drivers/firmware/arm_scmi/mailbox.c
    │      scmi_rx_callback(...) → 从共享内存读回应
    │
 ▼ ── complete(&xfer->done)  → scmi_do_xfer 返回 → clk_set_rate 返回
```

这条链上有**三次"完成"的概念**，别混：

| 完成 | 谁在等 | 靠什么醒 |
|---|---|---|
| ① **mailbox TX 完成** | `mbox_send_message`（`tx_block` 时） | `tx_tick` → `complete(&chan->tx_complete)` |
| ② **SCMI 事务完成** | `scmi_do_xfer` | `complete(&xfer->done)`，由 RX 路径触发 |
| ③ **时钟真的变了** | `clk_set_rate` 的调用者 | 上面 ② 返回 |

**①≠②**。TX 完成只表示"寄存器写下去了"，不表示对端处理了。
所以 mailbox 的 `-ETIME` 和 SCMI 的超时是**两个完全不同的超时**
（`07-debug.md` §6.3）。

另一条更短的链（纯门铃，无 TX 阻塞）——
`drivers/rpmsg/qcom_smd.c:380-381`：

```c
	mbox_send_message(edge->mbox_chan, NULL);   /* 载荷是 NULL */
	mbox_client_txdone(edge->mbox_chan, 0);     /* 自己立刻宣布"发完了" */
```

⇒ 这两行就是 ACK 模式的完整形态：**核心退化成一个纯队列**
（`08-feynman.md` §8 第 3 点）。

---

## §7 mailbox 和其他子系统的关系

### 7.1 谁在用 mailbox

全树 `mbox_request_channel*` 调用点普查
（`/usr/bin/grep -rn 'mbox_request_channel' --include=*.c drivers/ arch/ sound/ net/`，
去掉核心自身）：

| 目录 | 调用点数 | 典型消费者 |
|---|---|---|
| `drivers/remoteproc/` | 16 | 最大用户：启动/管理协处理器 |
| `drivers/firmware/` | 12 | SCMI · SCPI · Tegra BPMP |
| `drivers/soc/` | 8 | MediaTek CMDQ helper · Qualcomm |
| `drivers/mailbox/` | 8 | `mailbox-test` 自己 |
| `drivers/media/` | 5 | 相机/编解码固件 |
| `drivers/tty/` · `drivers/rpmsg/` · `drivers/i2c/` · `drivers/hwmon/` · `drivers/clk/` | 各 2 | —— |
| `drivers/irqchip/` · `drivers/gpu/` · `drivers/dma/` · `drivers/crypto/` | 各 1 | —— |

**横跨 15 个子系统目录** ⇒ mailbox 是典型的**底层基础设施**，
不是某个领域的专用框架。

### 7.2 mailbox 依赖谁

| 依赖 | 用在哪 | 行 |
|---|---|---|
| **OF / devicetree** | 通道解析的唯一入口 | `of_parse_phandle_with_args`，`drivers/mailbox/mailbox.c:353` |
| **driver model（device）** | 只用 `cl->dev` 和 `mbox->dev` 两个指针 | 不建 device、不建 class |
| **devres** | `devm_mbox_controller_register` | `drivers/mailbox/mailbox.c:596-610` |
| **hrtimer** | POLL 模式 | `:507-510` 初始化，`:117-146` 回调 |
| **completion** | 阻塞发送 | `:280` |
| **module refcount** | 占用通道时 `try_module_get` | `:376` |

**不依赖 platform bus**（虽然控制器驱动几乎都是 platform driver）、
**不依赖 irq 子系统**（中断由控制器驱动自己申请）、
**不依赖 DMA/IOMMU**。核心只有 825 行是有道理的。

### 7.3 容易搞混的邻居

| 邻居 | 和 mailbox 的区别 |
|---|---|
| **remoteproc** | 管协处理器的**生命周期**（加载固件、启停）；mailbox 管**通信**。remoteproc 是 mailbox 最大的消费者 |
| **rpmsg** | 在 mailbox 之上跑的**多路复用协议**（virtio 环 + 命名端点）；mailbox 只是它的"踢一脚"层 |
| **SCMI / SCPI** | 具体的**消息协议**；mailbox 是它们的 transport 之一（另一个是 SMC/HVC，不走 mailbox） |
| **IPI（处理器间中断）** | 同架构核之间（如 CPU0→CPU3），走 GIC；mailbox 是**异构**处理器之间 |
| **`hwspinlock`** | 只做互斥，不传消息，也不产生中断 |
| **`smem`（Qualcomm）** | 只是共享内存分区表；踢一脚仍然靠 mailbox |

一个实用的判据：
**如果两个通信方跑的是同一个 Linux 内核 ⇒ 用 IPI / smp_call_function，不是 mailbox。**
mailbox 的对端**永远是另一份固件**。

---

## §8 导读：接下来看哪一篇

| 你想干什么 | 去哪一篇 | 关键小节 |
|---|---|---|
| 找某个函数叫什么、在哪个头文件、返回什么 | **`01-api.md`** | §0 代码地图 · §2 消费者 API · §3 控制器 API · §5 错误码 |
| 搞清楚 `mbox_chan` 每个字段谁填谁读、几把锁 | **`02-datastructures.md`** | §1.1 四组关系 · §4 字段表 · §8 锁 |
| 逐段读注册 / 申请 / 发送 / txdone / 注销的实现 | **`03-internals.md`** | §0 导航 · Part A 时间线 · Part B 横切 · Part C 评价 |
| 了解 `mailbox-test`、`of_xlate` 的各种写法、CMDQ 这类特殊用法 | **`04-extensions.md`** | §1 `mailbox-test` · §3 `of_xlate` 普查 · §5.2 CMDQ |
| 抄一份能用的消费者 / 控制器骨架 | **`05-usage.md`** | §1 消费者 · §2 控制器 · §4 常见错误 |
| 写 DTS、查某个属性谁在读、DTS 配错了怎么定位 | **`06-devicetree.md`** | §1.1 属性总表 · §4 `#mbox-cells` · §8 定位三层 · §9 错误消息映射 |
| dmesg 干净但通道申请失败 / 一直超时 / 想加日志 | **`07-debug.md`** | §0 分诊入口 · §2 日志 11 处 · §4 `mailbox-test` · §6 错误码分诊 · §8 一页速查 |
| "我理解不了为什么要这么设计" | **`08-feynman.md`** | §1 不是 device/driver · §3 三种 txdone · §5 返回值不是成功 · §7 为什么不可观测 |
| 想知道某条断言的取证过程、哪些结论没验证 | **`99-evidence.md`** | §2 阅读范围 · §3 逐篇取证 · §4 交叉验证 · §5 未验证项 · §6 自陈问题 |

### 8.1 三条"记住这个就够了"

1. **核心只有 825 行，什么都不管。**
   载荷格式、协议语义、重传、观测 —— 全在上层。
   ⇒ 出问题时**先怀疑 DTS 和上层框架，最后才怀疑 mailbox 核心**。

2. **默认几乎完全静默。**
   通道申请的 4 个失败出口里 3 个是 debug 级、1 个无日志；
   零 tracepoint、零 sysfs、零 debugfs、零调试 Kconfig。
   ⇒ 第一个动作永远是
   `echo 'file drivers/mailbox/mailbox.c +p' > /sys/kernel/debug/dynamic_debug/control`。

3. **"发完了"有三种说法，配错了症状是超时而不是报错。**
   IRQ / POLL / ACK 由控制器的两个 flag 推导（`drivers/mailbox/mailbox.c:493-498`），
   但有 3 个驱动会在运行期改写它。
   ⇒ 看到 `-ETIME` 先查 txdone 模式，别先查硬件。
