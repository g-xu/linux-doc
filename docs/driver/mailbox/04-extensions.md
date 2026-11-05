---
title: 扩展机制
---

# 04 扩展与外围：核心之外的五层

> **本篇的组织轴：一节一个独立的扩展，五节之间互不调用。**
> 它们唯一的共同点是"都以 `drivers/mailbox/mailbox.c` 的 13 个导出符号为界面"。
> 所以本篇**没有**贯穿全篇的时间线，读者可以只挑自己碰到的那一节看。
>
> mailbox 和 i2c/spi 那种"核心 + mux/slave 子框架"的形状不一样：
> 它的核心（826 行）里**一个子框架都没有**。本篇讲的五层全部住在核心之外 ——
> 三层在 `drivers/mailbox/` 和 `include/linux/mailbox/` 里，
> 两层完全在别的子系统目录下。
>
> 格式约定同前：`mailbox.c` 指 `drivers/mailbox/mailbox.c`。arm64-only，ACPI PCC 已裁掉。

---

## §0 分诊表：arm64 上你会碰到哪几层

| § | 这一层是什么 | 住在哪 | 挂在 03 时间线的哪一点 | arm64 会碰到？ |
|---|---|---|---|---|
| §1 | **`mailbox-test`**：内核自带的测试客户端 | `drivers/mailbox/mailbox-test.c`（454 行） | 完整走 T1→T2→T3'→T4 | ✅ **写新控制器驱动时必用** |
| §2 | **消息载荷契约层**：4 个厂商 message 头文件 | `include/linux/mailbox/`（182 行） | T2 的 `mssg` 参数**是什么** | ✅ 用到对应控制器时 |
| §3 | **厂商私有 API 扩展**：OMAP 的三个额外导出符号 | `include/linux/omap-mailbox.h`（26 行） | 绕过核心，直接操作硬件中断掩码 | ⚠️ **全树零调用者**（详见 §3.2） |
| §4 | **上层协议框架**：SCMI / SCPI / RTKit / BPMP / SCU | `drivers/firmware/`、`drivers/soc/` | 把 T1–T4 整套包成"RPC 会话" | ✅ **arm64 上绝大多数 mailbox 流量都在这一层** |
| §5 | **反向用法**：MediaTek CMDQ | `drivers/mailbox/mtk-cmdq-mailbox.c`（695 行）+ 两个头文件 | 把 T2 当"命令包提交"而不是"发消息" | ✅ MediaTek SoC 的显示/MDP 路径 |

读这张表的一个用法：**如果你在调 arm64 平台上的 mailbox 问题，
第一件事是判断自己在 §4 还是 §5**。这两层各自有一套自己的错误处理和超时，
往往在 mailbox 核心之外就把问题吞掉了 —— `07-debug.md` §5 有分诊步骤。

---

## §1 `mailbox-test`：内核自带的测试客户端

### §1.1 它是什么

`drivers/mailbox/mailbox-test.c`，454 行，`Kconfig:167-173` 的
`MAILBOX_TEST`（tristate，`depends on OF` + `HAS_IOMEM`）。
Kconfig 帮助文本一句话就说明了定位：
「Test client to help with testing new Controller driver implementations.」

它是**唯一一个"用户态能直接驱动一次完整 mailbox 收发"的入口** ——
核心自己零 debugfs（`03-internals.md` §C1），
所以写新控制器驱动时它几乎是唯一的黑盒验证手段。

### §1.2 它注册了两个 debugfs 文件

`mbox_test_add_debugfs`（`mailbox-test.c:263-281`）：

```
/sys/kernel/debug/<dev_name>/message    0600   读+写   mbox_test_message_ops :254-261
/sys/kernel/debug/<dev_name>/signal     0200   只写    mbox_test_signal_ops  :82-86
```

目录名是 `dev_name(&pdev->dev)`（`:269`），也就是 DT 节点的
`<地址>.<名字>` 形式 —— 不是固定的 `mailbox-test`。
`debugfs_create_dir` 失败只 `dev_err`（`:271`）不中止 probe。

`message` 这个 file_operations 挂了四个方法（`:254-261`），
比一般的 debugfs 文件丰富得多：

| 方法 | 实现 | 用途 |
|---|---|---|
| `.write` | `mbox_test_message_write` `:95` | 发一条消息 |
| `.read` | `mbox_test_message_read` `:170` | 阻塞读收到的消息 |
| `.fasync` | `mbox_test_message_fasync` `:88-93` | 支持 `SIGIO` 异步通知 |
| `.poll` | `mbox_test_message_poll` `:242-252` | 支持 `select`/`poll`/`epoll` |

`.poll` 和 `.fasync` 的存在意味着**可以写一个用户态程序等 mailbox 的
RX 事件**，而不用轮询。具体用法见 `07-debug.md` §4。

### §1.3 `message` 和 `signal` 的分工（这是最容易搞混的地方）

两个长度常量（`:23-24`）：

```c
#define MBOX_MAX_SIG_LEN	8
#define MBOX_MAX_MSG_LEN	128
```

`mbox_test_message_write`（`:95-156`）里的关键分叉（`:130-142`）：

```c
	/*
	 * A separate signal is only of use if there is
	 * MMIO to subsequently pass the message through
	 */
	if (tdev->tx_mmio && tdev->signal) {
		print_hex_dump_bytes("Client: Sending signal: ", ...,
				     tdev->signal, MBOX_MAX_SIG_LEN);
		data = tdev->signal;                       /* :137 */
	} else
		data = tdev->message;                      /* 走这条 */
```

分工是这样的：

| DTS 里有没有 `mmio` 资源 | 走哪条 | `mbox_send_message` 的 `mssg` 是什么 | 128 字节的 message 怎么过去 |
|---|---|---|---|
| **有** `tx_mmio` 且写过 `signal` | `data = signal` | 那 8 字节的"信号" | 由 `tx_prepare` 回调 `memcpy_toio` 搬进共享内存（`:313`/`:315`） |
| 没有 | `data = message` | 那 128 字节的缓冲区指针 | 直接当 `void *mssg` 传给控制器 |

⇒ **`signal` 文件模拟的是"共享内存 + 门铃"型控制器**，
`message` 文件单用时模拟的是"寄存器直接搬数据"型控制器。
这正好覆盖了 mailbox 硬件的两大类型（`00-overview.md` §2 有硬件背景）。

`signal` 的内存是**一次性**的：`mbox_test_message_write:149-151` 在发完之后
`kfree(tdev->signal)` 并置 NULL —— 所以每发一条带 signal 的消息，
都要先重写一次 `signal` 文件。这个行为没有任何文档，只能从代码看出来。

### §1.4 它是一份"教科书级"的消费者实现

`mbox_test_request_channel`（`mailbox-test.c:330-355`）把消费者该填的字段填全了：

```c
	client->dev		= &pdev->dev;              /* :340 */
	client->rx_callback	= mbox_test_receive_message; /* :341 */
	client->tx_prepare	= mbox_test_prepare_message; /* :342 */
	client->tx_done		= mbox_test_message_sent;    /* :343 */
	client->tx_block	= true;                    /* :344 */
	client->knows_txdone	= false;                   /* :345 */
	client->tx_tout		= 500;                     /* :346 */

	channel = mbox_request_channel_byname(client, name);   /* :348 */
	if (IS_ERR(channel)) {
		dev_warn(&pdev->dev, "Failed to request %s channel\n", name);
		return NULL;                               /* :351  错误被吞成 NULL */
	}
```

它是全树**唯一同时实现了 `tx_prepare`、`tx_done`、`rx_callback` 三个回调
的客户端**（`02-datastructures.md` §2.1 的两张普查表交叉一下就能看出来：
`tx_prepare` 只有 3 个实现者、`tx_done` 只有 4 个，两边都出现的只有它）。
⇒ **抄消费者骨架就抄它**，`05-usage.md` §3 的骨架直接以它为原型。

但有两处**不要照抄**：

1. `:351` 把 `ERR_PTR` **吞成 NULL** —— 于是 probe 里
   `-EPROBE_DEFER` 这个语义丢失了，只能靠 `:392-393`
   「两条通道都没拿到就整体 `-EPROBE_DEFER`」硬凑回来。
   真实驱动应该原样返回 `PTR_ERR`。
2. `:395-397` 那段 Rx 通道别名：

   ```c
   	/* If Rx is not specified but has Rx MMIO, then Rx = Tx */
   	if (!tdev->rx_channel && (tdev->rx_mmio != tdev->tx_mmio))
   		tdev->rx_channel = tdev->tx_channel;
   ```

   **同一个 `mbox_chan *` 被两个字段指着**，而 `mbox_test_remove:428-431`
   会分别判断 `tx_channel` / `rx_channel` 并各调一次 `mbox_free_channel`
   —— 也就是**同一条通道被 free 两次**。
   第二次进 `mbox_free_channel:450` 时 `chan->cl` 已是 NULL，直接 return，
   所以**不崩**；但 `module_put`（`:463`）也因此只执行了一次，
   引用计数是对的。**纯属"靠核心的幂等保护活下来"**，不是好写法。
   记进 `99-evidence.md` §6。

### §1.5 DTS 怎么写（没有独立绑定文档）

`compatible` 是裸的 `"mailbox-test"`（`mailbox-test.c:437`），
**`Documentation/devicetree/bindings/mailbox/` 下没有它自己的绑定文件** ——
全树唯一的用法示例藏在别人的绑定里：
`Documentation/devicetree/bindings/mailbox/sti-mailbox.txt:46-47`。

而且**没有任何 in-tree DTS 实例**
（`/usr/bin/grep -rn 'mailbox-test' --include=*.dts --include=*.dtsi arch/` 零命中）——
它注定是"手工往 DTS 里加一个节点、或者用 DT overlay"的用法。
完整的节点写法见 `06-devicetree.md` §6。

---

## §2 消息载荷契约层：`include/linux/mailbox/`

### §2.1 为什么需要这一层

核心的 `mbox_send_message(chan, mssg)` 里 `mssg` 的类型是 **`void *`**，
`mailbox_client.h` 的 kernel-doc 只说「Client specific message typecasted as void *」。
⇒ **核心对载荷格式一个字都不定义**，控制器和消费者必须私下约定。

约定落在哪里？三种做法，`include/linux/mailbox/` 是其中最规范的一种：

| 做法 | 例子 | 问题 |
|---|---|---|
| 在 `include/linux/mailbox/` 放一个共享头 | 本节的 4 个 | 规范，但只有 4 个控制器这么做 |
| 载荷就是一个整数/指针，靠注释约定 | `qcom-apcs-ipc-mailbox`、多数门铃型 | 没有编译期检查 |
| 载荷结构体定义在消费者自己的头文件里 | Apple RTKit、Tegra BPMP | 控制器不知道格式（它只搬字节） |

### §2.2 四个头文件逐个

| 头文件 | 行数 | 定义了什么 | 谁生产 / 谁消费 |
|---|---|---|---|
| `include/linux/mailbox/arm_mhuv2_message.h` | 20 | `struct arm_mhuv2_mbox_msg { void *data; size_t len; }`（`:15-18`） | 只有 `drivers/mailbox/arm_mhuv2.c` 一个文件引用 —— **没有消费者** |
| `include/linux/mailbox/brcm-message.h` | 59 | `enum brcm_message_type`（`:14`）+ `struct brcm_sba_command`（`:22`）+ `struct brcm_message`（`:38`，内含 spu/sba/batch 三个 union 分支） | 控制器：`bcm-flexrm-mailbox.c`、`bcm-pdc-mailbox.c`；消费者：`drivers/crypto/bcm/cipher.h`、`drivers/dma/bcm-sba-raid.c` |
| `include/linux/mailbox/mtk-cmdq-mailbox.h` | 83 | CMDQ 命令包的指令编码 | 控制器：`mtk-cmdq-mailbox.c`；消费者：`include/linux/soc/mediatek/mtk-cmdq.h`（见 §5） |
| `include/linux/mailbox/zynqmp-ipi-message.h` | 20 | `struct zynqmp_ipi_message { size_t len; u8 data[]; }`（`:15-18`） | 控制器：`zynqmp-ipi-mailbox.c`；消费者：`drivers/soc/xilinx/zynqmp_power.c` |

`include/linux/mailbox/zynqmp-ipi-message.h:7-13` 的 kernel-doc 里有一句关键说明：
「This is the structure for data used in mbox_send_message」——
**这是全树唯一把"`mssg` 到底是什么"写进 kernel-doc 的地方**。
其余控制器要靠读代码才知道。

`arm_mhuv2_message.h` 这个头文件**只有控制器自己引用**
（`/usr/bin/grep -rln 'mailbox/arm_mhuv2_message.h'` 只命中
`drivers/mailbox/arm_mhuv2.c`）。也就是说：MHUv2 的数据传输协议模式
在本树上**没有任何消费者**。它是为 out-of-tree 或将来的消费者准备的。
这不是"死代码"（控制器自己在用那个结构体），
但对读者的含义是：**arm64 上想用 MHUv2 data-transfer 模式，
没有 in-tree 的参考消费者可抄**。

### §2.3 这一层挂在 03 时间线的哪一点

**T2（`mbox_send_message`）的第二个参数**，以及
**T3'（`rx_callback`）的第二个参数**。核心从不解引用它（`03-internals.md` §A5.3
的 `send_data` 只是把它原样传给控制器），所以：

**载荷格式错了，核心一点也不会报错** —— 症状是远端行为异常或超时，
而不是 mailbox API 返回错误码。这是 mailbox 调试最难的一类问题，
`07-debug.md` §5.2 有定位办法。

---

## §3 厂商私有 API 扩展：OMAP 的三个额外导出

### §3.1 它是什么

`include/linux/omap-mailbox.h`（26 行）在核心的 13 个导出符号之外，
又导出了三个 OMAP 专用的：

| 符号 | 声明 | 实现 | 作用 |
|---|---|---|---|
| `omap_mbox_request_channel(cl, chan_name)` | `include/linux/omap-mailbox.h:20-21` | `drivers/mailbox/omap-mailbox.c:414-462` | 按**名字**申请通道，但走的是 OMAP 自己的查找表，不是 `mboxes` 属性 |
| `omap_mbox_enable_irq(chan, irq)` | `:23` | `omap-mailbox.c:230-239` | 打开硬件的 TX 或 RX 中断掩码 |
| `omap_mbox_disable_irq(chan, irq)` | `:24` | `omap-mailbox.c:241-250` | 关掉 |

`irq` 参数是一个 `__bitwise` 枚举（`omap-mailbox.h:13-15`）：
`IRQ_TX = 1`、`IRQ_RX = 2`。

还有一个宏 `omap_mbox_message(data)`（`:11`）——
把 `void *` 载荷强转回 `u32`，说明 OMAP 的载荷就是一个 32 位整数。

### §3.2 三个符号在本树上全部零调用者

```
/usr/bin/grep -rn 'omap_mbox_request_channel\|omap_mbox_enable_irq\|omap_mbox_disable_irq' \
    --include=*.c --include=*.h .   |  grep -v 'omap-mailbox'
   → 零输出
```

⇒ 三个 `EXPORT_SYMBOL`（`omap-mailbox.c:239`、`:250`、`:462`）
**全部是死导出**：只有定义、没有 in-tree 使用者。

定性要小心：这**不是"死代码"** ——
`_omap_mbox_enable_irq` / `_omap_mbox_disable_irq`（`omap-mailbox.c:200-228`）
这两个内部函数被驱动自己用得很多（`:272`、`:283`、`:296`、`:378`、`:389`、
`:550`、`:553`、`:573`）。死的只是那三层**对外包装**。
历史成因大概是 OMAP 的 remoteproc/DSP 客户端曾经用过它们，
后来改用了通用 API。**结论只写"本树零调用者"，不推测别人为什么删**。
记进 `99-evidence.md` §6。

### §3.3 arm64 相关性

`omap-mailbox.c:650-668` 的 `of_device_id` 表里有五个 compatible：
`ti,omap2-mailbox` / `ti,omap3-mailbox` / `ti,omap4-mailbox` /
**`ti,am654-mailbox`** / **`ti,am64-mailbox`**。

⇒ 后两个是 **arm64（TI K3 家族 AM65x / AM64x）**，
所以这个驱动在 arm64 上是活的，**只是那三个私有 API 没人用**。
绑定文档是 `Documentation/devicetree/bindings/mailbox/ti,omap-mailbox.yaml`。

顺带：K3 平台上另一个 mailbox 控制器是 `ti-msgmgr.c`
（绑定 `ti,message-manager.txt`），和 omap-mailbox 是**两个不同的硬件**，
同一块 SoC 上可能都有。别把两者的 DTS 写法搞混。

---

## §4 上层协议框架：arm64 上真正的 mailbox 大户

### §4.1 五个框架的对照

这一层的共同特征：**把 mailbox 通道包装成"发请求 → 等响应"的 RPC**，
消费者看到的是 `scmi_do_xfer()` 之类的接口，完全看不到 mailbox。

| 框架 | 文件 | 申请通道 | 发送 | TXDone 方式 | arm64 上的典型用途 |
|---|---|---|---|---|---|
| **ARM SCMI** | `drivers/firmware/arm_scmi/mailbox.c` | `mbox_request_channel(cl, tx ? 0 : 1)` `:149`（按 index，TX=0 / RX=1） | `:187` | ACK：`mbox_client_txdone(smbox->chan, ret)` `:207` | 时钟/电压/传感器/复位的固件代理 |
| **ARM SCPI** | `drivers/firmware/arm_scpi.c` | `mbox_request_channel(cl, idx)` `:982` | `:508` | 由控制器 IRQ | Juno 等早期 arm64 开发板 |
| **Apple RTKit** | `drivers/soc/apple/rtkit.c` | **两种都用**：`byname` `:700-701`，失败则 index `:704` | `:631` | `tx_done`（`:742`）+ `mbox_flush` `:653` | Apple Silicon 的所有协处理器（NVMe/SMC/显示…） |
| **Tegra BPMP** | `drivers/firmware/tegra/bpmp-tegra186.c` | `mbox_request_channel(&priv->mbox.client, 0)` `:227` | `mbox_send_message(chan, NULL)` `:86`（**载荷是 NULL**，纯门铃） | ACK：`mbox_client_txdone(chan, 0)` `:90` | Tegra186/194/234 的电源与时钟 |
| **i.MX SCU** | `drivers/firmware/imx/imx-scu.c` | `mbox_request_channel_byname(cl, chan_name)` `:311` | `:192`（**循环里逐字发**） | `tx_done` `:304` | i.MX8 的系统控制单元 |

三条值得单独记住的观察：

1. **Tegra BPMP 的 `mssg` 是 `NULL`**（`drivers/firmware/tegra/bpmp-tegra186.c:86`）——
   真实数据早已通过共享内存放好了，mailbox 只当"门铃"。
   这是 `08-feynman.md` 里"mailbox 为什么可以不传数据"的取材。
   它同时印证了 `03-internals.md` §A5.3 的一个细节：
   核心从不解引用 `mssg`，所以 NULL 是完全合法的载荷。
2. **Apple RTKit 是全树唯一同时用 `byname` 和 index 两种申请方式的消费者**
   （`:700-704`，先试名字、失败退回下标）—— 兼容两代 DTS 写法。
   它也是 3 个 `mbox_flush` 调用者之一。
3. **SCMI 用 index 0/1 区分 TX/RX**（`arm_scmi/mailbox.c:149`），
   而不是 `mbox-names` —— 所以 SCMI 的 DTS 里 `mboxes` 的**顺序有语义**。
   写错顺序不会报错，只会永远收不到响应。见 `06-devicetree.md` §5.3。

`arm_scmi/mailbox.c:189` 那句注释值得抄进文档：
「mbox_send_message returns non-negative value on success, so reset」——
**上层框架必须自己把那个"队列下标令牌"归一化成 0**
（`03-internals.md` §A5.2）。这是本层最常见的适配点。

### §4.2 这一层挂在 03 时间线的哪一点

**整条时间线 T1–T4 都被它包住了。** 它额外加的东西是：

| 上层加了什么 | 为什么核心没有 | 例子 |
|---|---|---|
| 请求-响应配对（序列号 / token） | 核心的令牌不能用（`03-internals.md` §A5.2） | SCMI 的 `xfer->hdr.seq` |
| 响应超时（独立于 `tx_tout`） | 核心的超时只覆盖"发出去"，不覆盖"回来" | SCMI/SCPI 都有自己的 timeout |
| 共享内存的布局与同步 | 核心不定义载荷（§2） | SCMI 的 shmem、BPMP 的 IVC |
| 并发多请求 | 核心一条通道同时只有 1 个 in-flight（`active_req`） | SCMI 的 xfer 池 |

**调试含义：上层框架报超时时，先分清是"没发出去"还是"没回来"。**
前者在 mailbox 核心里有痕迹（`-ETIME` 来自 `mailbox.c:282`），
后者完全在上层。`07-debug.md` §5.1 给了区分办法。

---

## §5 反向用法：MediaTek CMDQ

### §5.1 它把 mailbox 用成了什么

`drivers/mailbox/mtk-cmdq-mailbox.c`（695 行）注册的是一个正常的
`mbox_controller`，但它的"消息"是**一个 GPU/显示命令包**，
"远端"是 SoC 里的 GCE（Global Command Engine）硬件。

三层结构：

```
消费者（15 个文件，主要在 drivers/gpu/drm/mediatek/）
    ↓  cmdq_pkt_write / cmdq_pkt_wfe / cmdq_pkt_flush_async …
include/linux/soc/mediatek/mtk-cmdq.h（280 行，24 个 API）
    ↓  cmdq_mbox_create → mbox_request_channel
    ↓  cmdq_pkt_flush_async → mbox_send_message
drivers/mailbox/mtk-cmdq-mailbox.c（mbox_controller）
    ↓  写 GCE 的命令队列寄存器
GCE 硬件
```

关键 API（`include/linux/soc/mediatek/mtk-cmdq.h`）：
`cmdq_mbox_create(dev, index)` `:52` / `cmdq_mbox_destroy(client)` `:58` /
`cmdq_pkt_create(client, size)` `:67` / `cmdq_pkt_finalize(pkt)` `:265` /
`cmdq_pkt_flush_async(pkt)` `:278`，中间是 20 来个"往命令包里追加一条指令"的函数
（`cmdq_pkt_write` `:84`、`cmdq_pkt_wfe` `:182`、`cmdq_pkt_poll` `:215` …）。

### §5.2 为什么值得单独一节

因为它**颠倒了 mailbox 的语义模型**：

| | 常规 mailbox（§4） | CMDQ |
|---|---|---|
| 消息是什么 | 一个请求或一个门铃 | **一段可执行的指令流**（含条件等待、轮询、跳转） |
| 远端是什么 | 另一个 CPU / 协处理器上的固件 | **一个硬件状态机**，没有软件 |
| 为什么用 mailbox 而不是直接写寄存器 | 因为对端是独立的处理器 | 因为要**排队 + 异步完成通知**，正好是 mailbox 核心提供的两件事 |
| 载荷生命周期 | 发完就不管 | 命令包在 GCE 执行期间必须一直有效（DMA 内存） |

⇒ 结论：**mailbox 核心提供的其实只是"带队列的异步提交 + 完成回调"，
"邮箱"这个名字限制了想象**。CMDQ 是全树最有说服力的例子。
这一条是 `08-feynman.md` 的一个主题。

`mtk-cmdq-mailbox.c:593-594` 把 `txdone_irq` 和 `txdone_poll` **都显式写成 false**
（`02-datastructures.md` §1.4 的普查表里有），也就是走 ACK 模式 ——
因为"命令执行完"是 GCE 中断告诉驱动的，驱动再自己调 `mbox_chan_txdone`。

### §5.3 arm64 相关性

15 个消费者文件里 13 个在 `drivers/gpu/drm/mediatek/` 和
`drivers/soc/mediatek/`，全部是 arm64（MT8173/MT8183/MT8192…）。
另外两个是 `drivers/media/platform/mediatek/mdp3/mtk-mdp3-cmdq.h`（配套的 `.c` 在同目录）
（MDP3 图像处理）和 `drivers/soc/mediatek/mtk-mutex.c`。

顺带一个跨篇线索：`drivers/media/platform/mediatek/mdp3/mtk-mdp3-cmdq.c:447` 是
`02-datastructures.md` §1.4 那张 `knows_txdone` 普查表里
"没设 `knows_txdone` 也能用 `mbox_client_txdone`"的三个实例之一。

---

## §6 汇总表：五层 × 职责 × 挂载点 × arm64

| § | 层 | 核心职责 | 用到核心的哪几个导出符号 | 挂在 03 时间线 | arm64 | 读者什么时候需要它 |
|---|---|---|---|---|---|---|
| §1 | `mailbox-test` | 用户态驱动一次完整收发 | `request_channel_byname` / `send_message` / `free_channel` | T1→T2→T3'→T4 全套 | ✅ | **调新控制器驱动时** |
| §2 | 消息载荷契约 | 定义 `void *mssg` 到底是什么 | 不用任何符号（纯类型约定） | T2 / T3' 的参数 | ✅ | 对接特定控制器时 |
| §3 | OMAP 私有 API | 额外暴露硬件中断掩码 | 在核心之外另开三个导出 | 绕过时间线 | ⚠️ 驱动活、API 零调用者 | 基本不需要 |
| §4 | 上层协议框架 | 请求-响应配对、响应超时、并发 | 全部消费者侧符号 + `mbox_flush` | 包住 T1–T4 | ✅ **最常见** | 调 SCMI/RTKit/BPMP 问题时 |
| §5 | MediaTek CMDQ | 把 mailbox 当命令队列提交口 | `request_channel` / `send_message` | 重解释 T2 | ✅ MTK 平台 | 调 MTK 显示/MDP 时 |

一句话总结本篇：**mailbox 核心 826 行里没有子框架，
但它外面长了五层性质完全不同的东西** ——
一层测试工具、一层类型约定、一层历史遗留、一层协议封装、一层语义颠覆。
理解"哪一层的问题"是调 mailbox 的第一步。

---

## §7 读完本篇之后

| 想知道 | 去哪篇 |
|---|---|
| 怎么把 `mailbox-test` 的节点写进 DTS | `06-devicetree.md` §6 |
| 抄一份消费者 / 控制器骨架 | `05-usage.md` §3 / §2 |
| `mailbox-test` 的 debugfs 具体怎么操作、poll 怎么用 | `07-debug.md` §4 |
| 上层框架超时时怎么分清"没发出去"还是"没回来" | `07-debug.md` §5.1 |
| 核心为什么不定义载荷格式 | `08-feynman.md` |
| 本篇每条普查数字的复现命令 | `99-evidence.md` §3 |
