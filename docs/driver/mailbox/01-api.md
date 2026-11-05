---
title: API 参考
---

# 01 代码地图与对外 API

> 本篇回答两个查询：**「代码在哪个文件」** 和 **「这个 API 怎么用」**。
> 两者放在一篇，是因为它们通常是同一次提问的两半。
>
> 格式约定：
> - API 表格固定三列 **API / 说明 / 备注**；签名**只写函数名和形参名，不带类型**，
>   完整原型见每节开头指出的头文件。
> - 每节开头一行交代「头文件」和「实现文件」，**逐 API 不再重复位置**。
> - 需要精确到行的内容集中在 `03-internals.md` 和 `99-evidence.md`。
>
> arm64-only。ACPI 相关的 PCC 通道（`drivers/mailbox/pcc.c`）已裁掉，理由见 `99-evidence.md` §2.7。

---

## §0 代码地图

### §0.1 一句话定位

mailbox 是**片上处理器之间**（主 CPU ↔ 协处理器 / 电源管理核 / DSP / SCP）
的**队列化消息 + 中断信号**框架。它只管**通道的申请、消息的排队和发送完成的通告**；
消息内容的格式完全由两端的固件约定，内核核心**不解释一个字节**。

官方文档开篇就把这件事的后果说明白了
（`Documentation/driver-api/mailbox.rst:7-18`）：远端固件通常是私有的，
所以**客户端驱动很难跨平台复用** —— 这不是设计缺陷，是这个子系统的先天处境。

### §0.2 核心文件（全部详读）

| 文件 | 行数 | 职责 |
|---|---|---|
| `drivers/mailbox/mailbox.c` | 626 | **整个框架的全部逻辑**：通道申请/释放、TX 环形队列、三种 TX 完成检测、控制器注册 |
| `drivers/mailbox/mailbox.h` | 10 | 只有三个 `TXDONE_BY_*` 位定义，核心内部用 |
| `include/linux/mailbox_client.h` | 49 | 消费者侧：`struct mbox_client` + 7 个 API 声明 |
| `include/linux/mailbox_controller.h` | 140 | 控制器侧：`struct mbox_chan_ops` / `mbox_controller` / `mbox_chan` + 6 个 API 声明 |

**核心一共 825 行。** 这是本系列里最小的子系统 —— 小到可以逐行读完，
所以本套文档的论断几乎全是一等证据（`99-evidence.md` §2.1）。

### §0.3 消息负载头文件（`include/linux/mailbox/`）

这个目录里**没有框架代码**，只有几个控制器和它的客户端约定的**消息结构体**：

| 文件 | 行数 | 谁在用 |
|---|---|---|
| `include/linux/mailbox/mtk-cmdq-mailbox.h` | 83 | MediaTek GCE（`drivers/mailbox/mtk-cmdq-mailbox.c` ↔ `drivers/soc/mediatek/mtk-cmdq-helper.c`） |
| `include/linux/mailbox/brcm-message.h` | 59 | Broadcom FlexRM / PDC |
| `include/linux/mailbox/arm_mhuv2_message.h` | 20 | ARM MHUv2 |
| `include/linux/mailbox/zynqmp-ipi-message.h` | 20 | Xilinx ZynqMP IPI |

它们的存在本身就是一条重要事实：**框架传的 `void *mssg` 完全不透明**，
两端必须靠这种头文件对齐。没有共享头文件的控制器（多数）就靠**约定**。

### §0.4 Kconfig

| 配置项 | 位置 | 说明 |
|---|---|---|
| `MAILBOX` | `drivers/mailbox/Kconfig:2-3` | `menuconfig` 型 **bool**（不是 tristate）—— 核心**不能编成模块** |
| `MAILBOX_TEST` | `drivers/mailbox/Kconfig:167-170` | tristate，依赖 `OF` + `HAS_IOMEM`。唯一的调试出口，见 `07-debug.md` §3 |
| `OMAP_MBOX_KFIFO_SIZE` | `drivers/mailbox/Kconfig:83` | 唯一一个可调的数值型配置，属 OMAP 驱动私有 |
| `PCC` | `drivers/mailbox/Kconfig:101-103` | `depends on ACPI` ⇒ **arm64 DT 启动用不到，本套裁掉** |

编译入口：`drivers/mailbox/Makefile:4` 把 `mailbox.o` 挂在 `CONFIG_MAILBOX` 上，
`drivers/mailbox/Makefile:6` 挂 `mailbox-test.o`。整个 Makefile 64 行，一行一个驱动。

`menuconfig MAILBOX` 是 bool 而各个控制器驱动多为 tristate，
意味着**控制器可以是模块，核心必须内建**。这一点在 §2.5 的 `try_module_get` 那里会再次出现。

### §0.5 控制器驱动清单（arm64 可能碰到的）

`drivers/mailbox/` 下共 **32 个 `.c`、17594 行**。按 Kconfig 的 `depends on` 过滤出
arm64 平台真正会编进去的（`COMPILE_TEST` 不算）：

| 驱动 | 行数 | Kconfig 位置 | 平台 |
|---|---|---|---|
| `drivers/mailbox/bcm-flexrm-mailbox.c` | 1690 | `drivers/mailbox/Kconfig:222-225` | 唯一显式 `depends on ARM64` |
| `drivers/mailbox/bcm-pdc-mailbox.c` | 1645 | `drivers/mailbox/Kconfig:214-216` | Broadcom iProc |
| `drivers/mailbox/arm_mhuv2.c` | 1138 | `drivers/mailbox/Kconfig:31` | ARM MHUv2 |
| `drivers/mailbox/imx-mailbox.c` | 1057 | `drivers/mailbox/Kconfig:38-40` | NXP i.MX |
| `drivers/mailbox/tegra-hsp.c` | 956 | `drivers/mailbox/Kconfig:195-197` | NVIDIA Tegra |
| `drivers/mailbox/ti-msgmgr.c` | 944 | `drivers/mailbox/Kconfig:136-138` | TI K3/Keystone |
| `drivers/mailbox/omap-mailbox.c` | 933 | `drivers/mailbox/Kconfig:74-76` | TI OMAP2+/K3 |
| `drivers/mailbox/zynqmp-ipi-mailbox.c` | 723 | `drivers/mailbox/Kconfig:260-262` | Xilinx ZynqMP |
| `drivers/mailbox/mtk-cmdq-mailbox.c` | 695 | `drivers/mailbox/Kconfig:250-252` | MediaTek GCE |
| `drivers/mailbox/mailbox-sti.c` | 497 | `drivers/mailbox/Kconfig:129-131` | STi |
| `drivers/mailbox/apple-mailbox.c` | 441 | `drivers/mailbox/Kconfig:11-13` | Apple SoC |
| `drivers/mailbox/sprd-mailbox.c` | 430 | `drivers/mailbox/Kconfig:279-281` | Unisoc |
| `drivers/mailbox/hi6220-mailbox.c` | 373 | `drivers/mailbox/Kconfig:157` | HiSilicon |
| `drivers/mailbox/qcom-ipcc.c` | 369 | `drivers/mailbox/Kconfig:287-289` | Qualcomm IPCC |
| `drivers/mailbox/sun6i-msgbox.c` | 319 | `drivers/mailbox/Kconfig:270-272` | Allwinner |
| `drivers/mailbox/hi3660-mailbox.c` | 297 | `drivers/mailbox/Kconfig:147` | HiSilicon |
| `drivers/mailbox/rockchip-mailbox.c` | 262 | `drivers/mailbox/Kconfig:92-94` | Rockchip |
| `drivers/mailbox/mailbox-xgene-slimpro.c` | 260 | `drivers/mailbox/Kconfig:204-206` | APM X-Gene |
| `drivers/mailbox/armada-37xx-rwtm-mailbox.c` | 206 | `drivers/mailbox/Kconfig:64-66` | Marvell |
| `drivers/mailbox/qcom-apcs-ipc-mailbox.c` | 189 | `drivers/mailbox/Kconfig:187-189` | Qualcomm APCS |
| `drivers/mailbox/mtk-adsp-mailbox.c` | 184 | `drivers/mailbox/Kconfig:241-243` | MediaTek ADSP |

逐驱动剖析不在本套范围内（`99-evidence.md` §2.2 登记了选读理由）。
本套讲的是**契约**：这 21 个驱动做的事情，全部是填 §3 那张回调表。

### §0.6 客户端在哪里

**mailbox 的客户端几乎全部不在 `drivers/mailbox/` 下** ——
全树有 **38 个文件**调用 `mbox_request_channel*`，分布在：

| 目录 | 代表文件 | 干什么 |
|---|---|---|
| `drivers/remoteproc/` | `imx_rproc.c` / `ti_k3_r5_remoteproc.c` / `st_remoteproc.c` | 启停协处理器、跑 rpmsg |
| `drivers/rpmsg/` | `qcom_smd.c` / `qcom_glink_native.c` | 用 mailbox 当"敲门铃"，数据走共享内存 |
| `drivers/soc/` | `qcom/qcom_aoss.c` / `qcom/smp2p.c` / `ti/wkup_m3_ipc.c` / `apple/rtkit.c` | SoC 杂项协处理器接口 |
| `drivers/firmware/` | `ti_sci.c` / `imx/imx-dsp.c` | 固件服务调用 |
| `drivers/hwmon/`、`drivers/i2c/busses/` | `xgene-hwmon.c` / `i2c-xgene-slimpro.c` | 把 mailbox 当成一条**远端总线**用 |
| `drivers/tty/serial/` | `tegra-tcu.c` | 串口输出经由协处理器转发 |
| `drivers/gpu/drm/mediatek/` | `mtk_drm_crtc.c` | 显示流水线命令队列 |

这张表是理解本子系统的关键：**mailbox 自己不提供任何功能，
它是别的子系统的底座**。所以"mailbox 出问题"的现象，
通常表现为 remoteproc 起不来、rpmsg 不通、hwmon 读不到值。

---

## §1 消费者（client）侧 API

> **头文件**：`include/linux/mailbox_client.h`（声明在 `:40-47`）
> **实现文件**：`drivers/mailbox/mailbox.c`
>
> 消费者侧**只有 7 个函数**，全部导出为 `EXPORT_SYMBOL_GPL`。
> 头文件的声明行自带上下文注释（`/* atomic */`、`/* may sleep */`），
> 是本子系统最可靠的上下文说明来源 —— 比 kernel-doc 可靠，见 §1.2 的 。

### §1.1 申请与释放通道

| API | 说明 | 备注 |
|---|---|---|
| `mbox_request_channel(cl, index)` | 按 DTS `mboxes` 属性里的**下标**申请一条通道 | 返回 `struct mbox_chan *`，**失败返回 `ERR_PTR`，必须用 `IS_ERR` 判断**。可能返回 `-ENODEV` / `-EPROBE_DEFER` / `-EBUSY` / `-EINVAL`，语义见 §4.2 |
| `mbox_request_channel_byname(cl, name)` | 按 DTS `mbox-names` 属性里的**名字**申请 | 内部用**前缀匹配**而不是全等匹配，`"tx"` 会匹配上 `"tx-lowprio"`（`99-evidence.md` §6 第 ⑧ 条）。找不到名字返回 `-EINVAL` |
| `mbox_free_channel(chan)` | 释放通道 | `/* may sleep */`（`include/linux/mailbox_client.h:47`）。**队列里没发完的消息被直接丢弃，不会有任何回调**；它还会**改写通道的 txdone 模式**，见 §5.3 |

**两个申请函数的共同前提**（都写在 `drivers/mailbox/mailbox.c` 的 `mbox_request_channel` 里）：

- `cl->dev` 和 `cl->dev->of_node` 必须都非空 —— 否则 `-ENODEV`。
  ⇒ **纯 platform_data 的设备用不了 mailbox**，必须有 DT 节点。
- 一条通道**同时只能有一个客户端**。已被占用返回 `-EBUSY`。
- 控制器还没注册时返回 **`-EPROBE_DEFER`** —— 客户端 probe 里应当原样往上传，
  让驱动模型稍后重试。这是 mailbox 客户端最常见的正常返回路径。

`mbox_request_channel_byname` 内部就是"查名字定下标、再调
`mbox_request_channel`"，所以上面所有错误码它都会原样透出，
外加两个自己的：`mbox-names` 属性不存在、或名字对不上，都是 `-EINVAL`。

### §1.2 发送消息

| API | 说明 | 备注 |
|---|---|---|
| `mbox_send_message(chan, mssg)` | 把一条消息放进通道的 TX 队列，并（如果队列空）立即触发发送 | **返回值不是 0/负数，成功时返回的是一个"令牌"（队列里的位置索引，≥0）**；失败返回负错误码。`mssg` 是 `void *`，**核心完全不解释它** |
| `mbox_flush(chan, timeout)` | 阻塞等待已提交的消息发完 | 控制器没实现 `flush` 回调就直接返回 **`-ENOTSUPP`**；全树只有 4 个控制器实现了它（§3.2）。⚠️ 它**不校验 `chan` 是否为 NULL**，和 `mbox_send_message` 不一致（`99-evidence.md` §6 第 ③ 条） |

**`mbox_send_message` 有两种完全不同的行为，取决于 `cl->tx_block`：**

| `cl->tx_block` | 行为 | 上下文 |
|---|---|---|
| `false` | 排队后立即返回令牌，**不等** | 可在原子上下文调用 |
| `true` | 排队后**睡眠等待**这一条发送完成 | **必须是可睡眠上下文** |

**kernel-doc 在这一点上是错的。** `drivers/mailbox/mailbox.c:249-250`
的注释写着"这个函数可以在原子上下文调用，因为它只是把数据排队并返回一个令牌"——
这句话**只在 `tx_block` 为 false 时成立**。`tx_block` 为 true 时函数会走
`wait_for_completion_timeout`，在原子上下文调用会直接触发 sleeping-while-atomic。
以头文件那行 `/* atomic */`（`include/linux/mailbox_client.h:45` 标在
`mbox_client_txdone` 上、**没有**标在 `mbox_send_message` 上）为准。

**队列满了返回 `-ENOBUFS`**，并打一条
`Try increasing MBOX_TX_QUEUE_LEN`（`drivers/mailbox/mailbox.c:265`）。
这条日志**不可操作** —— 队列长度是编译期常量 20
（`include/linux/mailbox_controller.h:103`），改它要重新编译内核。
上游自己在 `include/linux/mailbox_controller.h:100-101` 留了 `REVISIT` 注释承认这一点。

### §1.3 TX 完成的三种通告方式

这是 mailbox 最容易搞错的一块，因为**"消息发完了"这件事有三种检测途径**，
而客户端只在其中一种里需要动手。

| API | 说明 | 备注 |
|---|---|---|
| `mbox_client_txdone(chan, r)` | 客户端主动告诉核心"上一条发完了"，`r` 是结果码 | `/* atomic */`（`include/linux/mailbox_client.h:45`）。**只有 `cl->knows_txdone` 为真时才允许调用**，否则核心打 `Client can't run the TX ticker`（`drivers/mailbox/mailbox.c:200`）并忽略 |

三种模式的完整对照（机制细节见 `03-internals.md` Part B）：

| 模式 | 谁检测 | 客户端要做什么 | 控制器要做什么 |
|---|---|---|---|
| **IRQ**（首选） | 控制器的中断处理函数 | **什么都不做** | 置 `txdone_irq = true`，中断里调 `mbox_chan_txdone()` |
| **POLL** | 核心的 hrtimer 定时轮询 | **什么都不做** | 置 `txdone_poll = true` + `txpoll_period`，实现 `last_tx_done` 回调 |
| **ACK** | **客户端自己**（它能从消息内容看出远端收到了） | 置 `cl->knows_txdone = true`，收到确认后调 `mbox_client_txdone()` | 两个 flag 都不置 |

**优先级是官方明确写死的** ——
`Documentation/driver-api/mailbox.rst:34-35`：「方式的优先顺序是
**IRQ → Poll → None**」。而且控制器**绝不能同时置两个 flag**
（`include/linux/mailbox_controller.h:36-42`）。

**这里有一个真实的坑**：ACK 模式是控制器"两个 flag 都不置"时的**默认落点**，
但核心在通道释放时会把它**改写成 POLL**，而 POLL 需要的定时器和回调都不存在。
完整推演见 §5.3 和 `99-evidence.md` §6 第 ① 条 —— 这是本套发现的最重要的问题。

### §1.4 接收消息

**接收侧没有 API，只有回调。** 客户端不"读"消息，
而是在 `struct mbox_client` 里填一个 `rx_callback`，由控制器驱动在收到数据时
经核心回调进来。

| API | 说明 | 备注 |
|---|---|---|
| `mbox_client_peek_data(chan)` | 主动催一下控制器"看看有没有数据到" | 返回 `bool`。`/* atomic */`（`include/linux/mailbox_client.h:46`）。控制器没实现 `peek_data` 回调时**返回 false**（不是错误码），全树只有 6 个控制器实现了它 |

`struct mbox_client` 里和接收相关的两个字段（完整字段表见 `02-datastructures.md` §2）：

| 字段 | 位置 | 上下文约束 |
|---|---|---|
| `rx_callback(cl, mssg)` | `include/linux/mailbox_client.h:35` | **在控制器的中断上下文里被调用** —— 不能睡眠，要快 |
| `tx_done(cl, mssg, r)` | `include/linux/mailbox_client.h:37` | 可选。TX 完成后被调，同样是原子上下文 |

`rx_callback` 是**必填**的吗？核心在回调前不检查它是否为 NULL
（`drivers/mailbox/mailbox.c:161`），所以**对任何会收消息的通道它就是必填** ——
不填就是 NULL 解引用。只发不收的通道可以不填，但这靠约定而非检查。
详见 `99-evidence.md` §6 第 ② 条。

---

## §2 控制器（controller）侧 API

> **头文件**：`include/linux/mailbox_controller.h`（声明在 `:130-138`）
> **实现文件**：`drivers/mailbox/mailbox.c`
>
> 控制器侧也只有 6 个函数：2 个注册 + 2 个 devres 版 + 2 个"向核心上报事件"。
> 真正的工作量在 §3 那张回调表里。

### §2.1 注册与注销

| API | 说明 | 备注 |
|---|---|---|
| `mbox_controller_register(mbox)` | 把一个填好的 `struct mbox_controller` 登记进核心 | `/* can sleep */`（`include/linux/mailbox_controller.h:130`）。返回 0 或 `-EINVAL`。调用前必须填好 `dev` / `ops` / `chans` / `num_chans`，缺一个就 `-EINVAL` |
| `mbox_controller_unregister(mbox)` | 注销 | `/* can sleep */`。它会**无条件释放该控制器下所有通道**（连带调每条通道的 `shutdown`），不管客户端还在不在用 |
| `devm_mbox_controller_register(dev, mbox)` | 上面那个的 devres 托管版 | **推荐用法**：probe 失败或驱动卸载时自动注销。返回 0 / `-ENOMEM` / 或 `mbox_controller_register` 的错误码 |
| `devm_mbox_controller_unregister(dev, mbox)` | 手工提前触发托管注销 | 官方 kernel-doc 自己说「**通常不需要调用这个函数**」（`drivers/mailbox/mailbox.c:618-619`） |

**`mbox_controller_register` 内部只做四件事**（逐段剖析见 `03-internals.md` §A2）：

1. **校验必填字段**，缺失即 `-EINVAL`（`drivers/mailbox/mailbox.c:490-491`）。
2. **确定这个控制器的 TX 完成检测模式**（`drivers/mailbox/mailbox.c:493-498`）：
   `txdone_irq` 为真 → IRQ；否则 `txdone_poll` 为真 → POLL；
   **否则注释写着 `/* It has to be ACK then */`，落到 ACK**。
   ⇒ **两个 flag 都不填不是配置错误，是一种有效配置**。
3. POLL 模式**必须**实现 `last_tx_done` 回调，否则打
   `last_tx_done method is absent` 并返回 `-EINVAL`
   （`drivers/mailbox/mailbox.c:502-505`）。
   这是核心唯一一处对回调存在性做的**强制**检查。
4. 初始化每条通道、装上默认的 `of_xlate`、挂进全局链表。

**注意第 2 步和第 3 步的不对称**：POLL 模式缺回调会被拦下，
而**落到 ACK 模式的控制器一个字都不检查** —— 核心不知道也不能知道
客户端将来会不会置 `knows_txdone`。这个不对称是 §5.3 那条问题链的起点。

### §2.2 向核心上报事件

这两个函数是**控制器中断处理函数里调的**，方向和 §2.1 相反：不是配置，是通知。

| API | 说明 | 备注 |
|---|---|---|
| `mbox_chan_received_data(chan, data)` | 「收到一条消息了」，核心随即转调客户端的 `rx_callback` | `/* atomic */`（`include/linux/mailbox_controller.h:132`）。**不校验 `chan->cl` 是否为 NULL**，通道已释放后调用就是 NULL 解引用（`99-evidence.md` §6 第 ② 条） |
| `mbox_chan_txdone(chan, r)` | 「上一条发完了」，`r` 是结果码 | `/* atomic */`（`include/linux/mailbox_controller.h:133`）。**只有 IRQ 模式的控制器允许调用**，否则核心打 `Controller can't run the TX ticker`（`drivers/mailbox/mailbox.c:179-180`）并忽略 |

这两条 `dev_err` 是对称的，措辞只差一个词，很容易看错：

| 日志 | 触发者 | 位置 | 含义 |
|---|---|---|---|
| `Controller can't run the TX ticker` | 控制器 | `drivers/mailbox/mailbox.c:179-180` | 控制器在非 IRQ 模式下调了 `mbox_chan_txdone` |
| `Client can't run the TX ticker` | 客户端 | `drivers/mailbox/mailbox.c:200` | 客户端没置 `knows_txdone` 却调了 `mbox_client_txdone` |

`mbox_chan_received_data` 的调用时机有一条**只写在注释里的契约**
（`drivers/mailbox/mailbox.c:154-156`）：只允许在**通道 `startup` 之后、
`shutdown` 之前**调用。核心不做任何检查，违约的后果是解引用空指针。

`mbox_chan_txdone` 传的 `r` 会一路传给客户端的 `tx_done` 回调，
并在 `tx_block` 模式下决定 `mbox_send_message` 的阻塞分支怎么收尾 ——
但**有一个例外**：`r == -ETIME` 时核心**不会唤醒等待者**
（`drivers/mailbox/mailbox.c:113-114`），因为那说明超时路径已经自己收过尾了。

### §2.3 一个下标解析的可选钩子

`struct mbox_controller` 里有一个 `of_xlate` 函数指针，
职责是把 DTS 里 `mboxes = <&mbox A B C>` 那几个 cell **翻译成一条具体通道**。

| 钩子 | 说明 | 备注 |
|---|---|---|
| `of_xlate(mbox, sp)` | 把 DT phandle 参数翻译成 `struct mbox_chan *` | **可以不填**：核心在注册时装一个默认实现（`drivers/mailbox/mailbox.c:522-523`），语义是"第一个 cell 就是通道下标"。全树 29 个控制器驱动里有 **16 个**自己实现了它，13 个用默认实现 |

默认实现叫 `of_mbox_index_xlate`（`drivers/mailbox/mailbox.c:467-477`），
它要求 `#mbox-cells = <1>` 且那个 cell 小于 `num_chans`，否则 `-EINVAL`。
⇒ **`#mbox-cells` 大于 1 的控制器必须自己实现 `of_xlate`**。
DTS 侧的写法见 `06-devicetree.md` §2。

### §2.4 devres 版的一处顺序细节

`devm_mbox_controller_register`（`drivers/mailbox/mailbox.c:587-608`）
的收尾两行顺序是**先挂进 devres 链表、后填内容**：

```c
devres_add(dev, ptr);      /* [drivers/mailbox/mailbox.c:604] */
*ptr = mbox;               /* [drivers/mailbox/mailbox.c:605] */
```

常规写法是先填后挂。这里反过来，意味着在 `:604` 和 `:605` 之间存在一个
`*ptr` 尚未初始化就已可被 devres 释放路径看到的窗口。
实际上 probe 是单设备串行的、这两行之间没有任何可能触发释放的调用，
所以**目前不会出问题**；但它的释放函数 `__devm_mbox_controller_unregister`
（`drivers/mailbox/mailbox.c:558-563`）会直接 `mbox_controller_unregister(*mbox)`，
一旦将来有并发释放路径就是野指针。记在 `99-evidence.md` §6 第 ⑬ 条。

### §2.5 模块引用计数

`mbox_request_channel` 在把通道交给客户端之前会
`try_module_get(mbox->dev->driver->owner)`，失败就 `-EBUSY`
（`drivers/mailbox/mailbox.c:376-380`）；`mbox_free_channel` 里对应
`module_put`（`drivers/mailbox/mailbox.c:463`）。

效果：**只要有客户端占着通道，控制器模块就 `rmmod` 不掉**。
这也解释了 §0.4 里"核心是 bool、控制器是 tristate"的组合为什么安全。

---

## §3 控制器必须实现的回调（`struct mbox_chan_ops`）

> **定义位置**：`include/linux/mailbox_controller.h:46-53`，
> 每个字段的语义注释在 `include/linux/mailbox_controller.h:14-45`。
>
> 写一个 mailbox 控制器驱动，**主要工作就是填这六个函数**。
> 代码骨架见 `05-usage.md` §2。

### §3.1 六个回调

| 回调 | 说明 | 备注 |
|---|---|---|
| `send_data(chan, data)` | 把一条消息真正写进硬件 | **唯一真正必需的回调** —— 核心无条件解引用它（`drivers/mailbox/mailbox.c:77`）。**不允许睡眠**（`include/linux/mailbox_controller.h:16-22`）。返回 0 = 已接受；返回 `-EBUSY` = 远端还没取走上一条，**只在 POLL 模式下允许这么返回** |
| `startup(chan)` | 通道被申请时的准备工作（申请中断、开时钟…） | 可选（核心 NULL 检查在 `drivers/mailbox/mailbox.c:394`）。返回非 0 会让 `mbox_request_channel` 失败 |
| `shutdown(chan)` | 通道被释放时的清理 | 可选（NULL 检查在 `drivers/mailbox/mailbox.c:452`）。无返回值，**清理失败无法上报** |
| `last_tx_done(chan)` | 「上一条发完了吗？」返回 `bool` | **POLL 模式下必需**，其它模式下不会被调用。核心在 hrtimer 里周期性调它（`drivers/mailbox/mailbox.c:129`） |
| `peek_data(chan)` | 「现在有数据可收吗？」返回 `bool` | 可选，只被 `mbox_client_peek_data` 触发（NULL 检查在 `drivers/mailbox/mailbox.c:225`） |
| `flush(chan, timeout)` | 阻塞把已提交的消息发完 | 可选，不实现则 `mbox_flush` 返回 `-ENOTSUPP`（`drivers/mailbox/mailbox.c:310`）。6.1 里**官方文档完全没提这个回调** |

### §3.2 「除了 peek_data 全都必需」这句官方说法是错的

`Documentation/driver-api/mailbox.rst:30` 写着：
「填好 `mbox_chan_ops`，**除了 `peek_data()` 之外都是必需的**」。

全树数一遍实现者（32 个 `.c` 文件为分母）：

| 回调 | 实现者数量 | 官方说的"必需"？ | 核心实际怎么处理缺失 |
|---|---|---|---|
| `send_data` | **29** | 是 | 无 NULL 检查，缺了就崩 —— **只有这一个真必需** |
| `startup` | 26 | 是 | NULL 检查后跳过 |
| `shutdown` | 26 | 是 | NULL 检查后跳过 |
| `last_tx_done` | **14** | 是 | 只在 POLL 模式被强制（`drivers/mailbox/mailbox.c:502-505`） |
| `peek_data` | 6 | 明确说不必需 | NULL 检查后返回 false |
| `flush` | **4** | **文档里根本没提** | 返回 `-ENOTSUPP` |

结论有三层：

1. **文档过时**：`flush` 是后加的回调，`Documentation/driver-api/mailbox.rst` 没跟上。
2. **文档过严**：`startup` / `shutdown` / `last_tx_done` 都有 NULL 检查，
   26/26/14 个实现者也说明它们事实上是可选的。
3. **文档漏了真正的硬约束**：唯一无条件解引用的是 `send_data`
   （29 个实现者 —— 剩下 3 个文件是 `mailbox.c` / `mailbox-test.c` 和一个
   不含 ops 的辅助文件）。**这条最重要的事实文档里没有。**

⇒ 本套一律以代码为准，理由和取证命令记在 `99-evidence.md` §4。

### §3.3 一条隐藏的锁契约

`send_data` 是在核心持有 `chan->lock`（一把 spinlock）的情况下被调用的
（`03-internals.md` §B2 有完整推演）。由此推出三条硬规矩：

| 规矩 | 后果 |
|---|---|
| `send_data` 里**不能睡眠** | 官方也写了（`include/linux/mailbox_controller.h:16-22`） |
| `send_data` 里**不能调 `mbox_chan_txdone`** | 立刻自死锁 |
| `send_data` 里**不能调任何客户端侧 `mbox_*` API** | 同上 |

`last_tx_done` 同样是在持锁状态下从 hrtimer 回调里被调的，
所以**它也不能睡眠**。这一条官方文档没写。

---

## §4 错误码语义

### §4.1 全部错误码一览

整个核心只用了 **8 个** 错误码，全部集中在 `drivers/mailbox/mailbox.c`。
一条命令就能复现这张表（`99-evidence.md` §2.4）：

| 错误码 | 出现位置 | 谁返回 | 含义 |
|---|---|---|---|
| `-EINVAL` | `drivers/mailbox/mailbox.c:261`、`:419`、`:425`、`:436`、`:474`、`:491`、`:504` | 全部 API | 用得最滥：7 处、5 种完全不同的原因，见 §4.3 |
| `-ENOBUFS` | `drivers/mailbox/mailbox.c:36` | `mbox_send_message` | TX 队列（20 条）满了 |
| `-ETIME` | `drivers/mailbox/mailbox.c:282` | `mbox_send_message` | `tx_block` 模式下等待超时；同时在 `:113` 被用作"不要唤醒等待者"的内部标记 |
| `-ENOTSUPP` | `drivers/mailbox/mailbox.c:310` | `mbox_flush` | 控制器没实现 `flush` 回调 |
| `-ENODEV` | `drivers/mailbox/mailbox.c:348` | `mbox_request_channel` | `cl->dev` 或 `cl->dev->of_node` 为空 |
| `-EPROBE_DEFER` | `drivers/mailbox/mailbox.c:361` | `mbox_request_channel` | 控制器还没注册 —— **正常路径**，原样往上传 |
| `-EBUSY` | `drivers/mailbox/mailbox.c:379` | `mbox_request_channel` | 通道已被占用，**或** `try_module_get` 失败 |
| `-ENOMEM` | `drivers/mailbox/mailbox.c:596` | `devm_mbox_controller_register` | devres 分配失败 |

`-ENOTSUPP` 是**非 POSIX** 的内核内部码。POSIX 的写法是 `-EOPNOTSUPP`，
而且 `checkpatch.pl` 会对 `-ENOTSUPP` 报警。这里返回给用户态是不合适的，
但 `mbox_flush` 的调用者都是内核驱动，所以没造成实际问题。
记在 `99-evidence.md` §6 第 ④ 条。

### §4.2 `mbox_request_channel` 的四种失败怎么区分

这是客户端 probe 里唯一需要分情况处理的返回值：

| 返回 | 该怎么办 |
|---|---|
| `-EPROBE_DEFER` | **原样 `return`**，让驱动模型稍后重试。**绝不能当成致命错误**，也不该打 `dev_err` |
| `-ENODEV` | 你的设备没有 DT 节点 —— 代码/平台问题，不是配置问题 |
| `-EBUSY` | 通道被别人占了，或控制器模块正在卸载。检查有没有两个 DT 节点写了同一条通道 |
| `-EINVAL` | DTS 问题：`mboxes` 属性缺失/下标越界，或（byname 版）`mbox-names` 缺失/名字对不上。定位见 `06-devicetree.md` §5 |

用 `dev_err_probe()` 打这条日志是推荐做法 —— 它对 `-EPROBE_DEFER`
自动降级为 debug 级，不会在正常启动时刷屏。

### §4.3 `-EINVAL` 的五种原因（同码多源）

`-EINVAL` 在 7 个地方返回，语义完全不同。按调用者能不能自己修分组：

| 组 | 位置 | 原因 | 谁的问题 |
|---|---|---|---|
| **参数为空** | `drivers/mailbox/mailbox.c:261` | `mbox_send_message` 收到 `chan` 或 `chan->cl` 为 NULL | 客户端代码 |
| | `drivers/mailbox/mailbox.c:419` | `mbox_request_channel_byname` 的 `cl->dev->of_node` 为空 | 客户端代码 |
| **DTS 配置** | `drivers/mailbox/mailbox.c:425` | 节点里没有 `mbox-names` 属性 | DTS |
| | `drivers/mailbox/mailbox.c:436` | `mbox-names` 里找不到（前缀匹配）这个名字 | DTS |
| | `drivers/mailbox/mailbox.c:474` | `#mbox-cells` 不是 1，或通道下标 ≥ `num_chans` | DTS（默认 `of_xlate` 才有这条） |
| **控制器代码** | `drivers/mailbox/mailbox.c:491` | 注册时 `dev`/`ops`/`chans`/`num_chans` 缺失 | 控制器驱动 |
| | `drivers/mailbox/mailbox.c:504` | 声明 POLL 却没实现 `last_tx_done` | 控制器驱动 |

**这三组的日志可见性完全不同**：只有 `:265`（`-ENOBUFS`）、`:503`
和 `:179`/`:200` 会打 `dev_err`，上面 7 处 `-EINVAL` **一条日志都不打**。
所以"客户端 probe 返回 `-EINVAL` 而 dmesg 干净"是本子系统的典型现象，
排查手段见 `07-debug.md` §2.1。

---

## §5 上下文与约束速查

### §5.1 每个 API 能在什么上下文调用

依据优先取头文件声明行的注释（`include/linux/mailbox_client.h:40-47`、
`include/linux/mailbox_controller.h:130-138`），因为 kernel-doc 有一处是错的（§1.2）。

| API | 可否原子上下文 | 依据 |
|---|---|---|
| `mbox_request_channel` / `_byname` | ❌ 会睡（内部取 `con_mutex`） | `drivers/mailbox/mailbox.c:351` |
| `mbox_send_message`（`tx_block = false`） | ✅ | `drivers/mailbox/mailbox.c:249-250` |
| `mbox_send_message`（`tx_block = true`） | ❌ **会睡** | `drivers/mailbox/mailbox.c:280` |
| `mbox_flush` | ⚠️ 取决于控制器的 `flush` 实现 | kernel-doc 说这个回调正是**为原子上下文**准备的（`drivers/mailbox/mailbox.c:296-297`） |
| `mbox_client_txdone` | ✅ | `include/linux/mailbox_client.h:45` |
| `mbox_client_peek_data` | ✅ | `include/linux/mailbox_client.h:46` |
| `mbox_free_channel` | ❌ 可能睡 | `include/linux/mailbox_client.h:47` |
| `mbox_controller_register` / `unregister` | ❌ 可能睡 | `include/linux/mailbox_controller.h:130-131` |
| `mbox_chan_received_data` | ✅ | `include/linux/mailbox_controller.h:132` |
| `mbox_chan_txdone` | ✅ | `include/linux/mailbox_controller.h:133` |

客户端的两个回调（`rx_callback` / `tx_done`）**总是在原子上下文被调用** ——
它们的调用点在中断处理路径或 hrtimer 回调里。

### §5.2 `tx_block` 和 `tx_tout` 的组合

`struct mbox_client` 的这两个字段一起决定 `mbox_send_message` 的行为
（`drivers/mailbox/mailbox.c:271-285`）：

| `tx_block` | `tx_tout` | 行为 |
|---|---|---|
| `false` | 忽略 | 立即返回令牌 |
| `true` | 非 0 | 最多等 `tx_tout` 毫秒，超时返回 `-ETIME` |
| `true` | **0** | 注释写「**wait forever**」，实际是 `msecs_to_jiffies(3600000)` = **1 小时**（`drivers/mailbox/mailbox.c:275-276`） |

**「wait forever」是错的措辞。** 1 小时后 `wait_for_completion_timeout`
会返回 0，函数走超时分支返回 `-ETIME`。差别不是学术性的：
一条永远等不到 TX-done 的消息，会把调用线程挂住一小时而不是永久 ——
前者在日志里表现为"一小时后突然出现一条 `-ETIME`"，极难关联到起因。
记在 `99-evidence.md` §6 第 ⑦ 条。

实践上 `tx_tout` 该填多少：`drivers/mailbox/mailbox-test.c:346` 填的是 **500**（毫秒）。
这是本树唯一一个可以当参考的取值。

### §5.3 一条真实存在的状态机隐患（申请-释放-再申请）

本节是本套文档最重要的一条发现，完整推演在 `03-internals.md` §B3，
证据链在 `99-evidence.md` §6 第 ① 条。这里只给结论，因为它直接影响
你写客户端时**要不要在 probe 失败路径上重试**。

三个已核实的事实：

1. 控制器两个 flag 都不置 ⇒ 通道注册为 **ACK** 模式
   （`drivers/mailbox/mailbox.c:497-498`、`:518`），而 POLL 模式所需的
   hrtimer **只在 POLL 分支里初始化**（`drivers/mailbox/mailbox.c:500-511`）。
2. `mbox_free_channel` 会把 ACK **改写成 POLL**（`drivers/mailbox/mailbox.c:459-460`）。
3. 再次申请时，只有客户端置了 `knows_txdone` 才会被改回 ACK
   （`drivers/mailbox/mailbox.c:389-390`）；没置就**留在 POLL**。

于是一条 ACK 模式的通道，经过一次「申请 → 释放 → 被一个没置 `knows_txdone`
的客户端再申请」之后，会停在 POLL 模式，而这个控制器：
**hrtimer 从未初始化**（`msg_submit` 在 `drivers/mailbox/mailbox.c:85-88`
会去 `hrtimer_start` 它），**`last_tx_done` 回调也不存在**
（`txdone_hrtimer` 在 `drivers/mailbox/mailbox.c:129` 会去调它）。

**触发路径比"unbind/rebind"更近**：`mbox_request_channel` 自己在
`startup` 失败时就会调 `mbox_free_channel`（`drivers/mailbox/mailbox.c:399`），
所以**一次失败的申请就足以把通道翻成 POLL**，
下一次 `-EPROBE_DEFER` 重试就踩在坏状态上。

⚠️ 这是**静态推断，没有在硬件上复现**。给客户端作者的实操建议只有一条：
**如果控制器是 ACK 模式（两个 flag 都不置），客户端就把
`cl->knows_txdone` 置为 true** —— 它会让 `:389-390` 把模式救回来。

### §5.4 `knows_txdone` 并不是 `mbox_client_txdone` 的开关

顺着 §5.3 会发现一件和直觉相反的事，值得单列：

`mbox_client_txdone` 的准入检查（`drivers/mailbox/mailbox.c:199`）看的是
**通道的 `txdone_method` 里有没有 ACK 位**，**不是** `cl->knows_txdone`。
而 `cl->knows_txdone` 在整个核心里**只被读一次**
（`drivers/mailbox/mailbox.c:389`），且只在控制器选了 POLL 时才起作用。

两个直接后果：

| 情形 | 结果 |
|---|---|
| 控制器是 ACK 模式，客户端**没**置 `knows_txdone` | `mbox_client_txdone` **照样能用** |
| 控制器是 IRQ 模式，客户端置了 `knows_txdone` | `mbox_client_txdone` **一定被拒**，打 `Client can't run the TX ticker` |

本树有三个 arm64 客户端正好落在第一行，全部工作正常：

| 客户端 | 调用点 | `knows_txdone` | 控制器 |
|---|---|---|---|
| `drivers/firmware/tegra/bpmp-tegra186.c` | `:90` | `:225` 置 **false** | tegra-hsp 门铃（两 flag 都不置 ⇒ ACK） |
| `drivers/clk/hisilicon/clk-hi3660-stub.c` | `:92` | `:135` 置 **false** | `drivers/mailbox/hi3660-mailbox.c`（两 flag 都不置） |
| `drivers/media/platform/mediatek/mdp3/mtk-mdp3-cmdq.c` | `:447` | 根本没设 | `drivers/mailbox/mtk-cmdq-mailbox.c:593-594`（显式 false/false） |

⇒ 文档和字段名都在暗示"`knows_txdone` 决定你能不能报 TX-done"，
**代码里不是这样**。写客户端时判断依据应当是**控制器的模式**，
而不是自己这个字段。记在 `99-evidence.md` §6 第 ⑭ 条。

---

## §6 读完本篇之后

| 你接下来想知道 | 去哪 |
|---|---|
| `mbox_client` / `mbox_controller` / `mbox_chan` 的字段全貌 | `02-datastructures.md` |
| 20 条 TX 环形队列、三种 TX-done 检测、锁的完整推演 | `03-internals.md` |
| `mailbox-test.c` 这个"既是驱动又是调试工具"的东西 | `04-extensions.md` |
| 客户端 / 控制器驱动的代码骨架 | `05-usage.md` |
| DTS 里 `mboxes` / `mbox-names` / `#mbox-cells` 怎么写 | `06-devicetree.md` |
| 这些错误码和 `dev_err` 字符串怎么查、debugfs 怎么用 | `07-debug.md` |
| "TX-done 到底谁负责"这类最容易想错的问题 | `08-feynman.md` |
| 全子系统的浓缩复习 | `00-overview.md` |
| 本篇每条论断的出处、哪些是推断 | `99-evidence.md` |
