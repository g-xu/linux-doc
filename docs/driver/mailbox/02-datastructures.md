---
title: 数据结构
---

# 02 数据结构：三个结构体、一条环形队列、两把锁

> 本篇的目标是让你**看着字段就知道这个字段是谁填的、谁读的、什么时候变**。
>
> mailbox 的数据结构少得反常 —— 核心一共只有 **4 个结构体**
> （`mbox_client` / `mbox_controller` / `mbox_chan` / `mbox_chan_ops`），
> 加起来不到 60 个字段。但字段少不等于关系简单：
> **同一件事（"TX 什么时候算完"）被三个结构体的四个字段分别表达**，
> 这是本子系统最大的理解障碍，也是 §1.4 的主题。
>
> 格式约定：
> - 字段表固定四列 **字段 / 谁填 / 谁读 / 说明**。「谁填」「谁读」写的是角色
>   （消费者 / 控制器驱动 / 核心），不是函数名 —— 函数名进「说明」。
> - `mailbox.c` 一律指 `drivers/mailbox/mailbox.c`；
>   `mailbox_client.h` / `mailbox_controller.h` / `mailbox.h`
>   分别指 `include/linux/mailbox_client.h`、`include/linux/mailbox_controller.h`、
> `drivers/mailbox/mailbox.h`（注意最后一个**在 drivers 下，不是 include 下**）。
> - arm64-only；ACPI PCC（`drivers/mailbox/pcc.c`）已裁掉，理由见 `99-evidence.md` §2.7。

---

## §1 先分清四组关系

四组里前两组是"别把它当成什么"，后两组是"同名的东西其实是几个"。
每一组都给出**否定性论断的行号** —— "为什么不会发生"和"会发生什么"同样需要取证。

### §1.1 `mbox_client` 不是 device，`mbox_controller` 不是 driver

这是从 i2c / spi 那类子系统过来的人第一个会犯的错：
看到"client / controller"两个词就往 `device` / `driver` 上套。

| 问题 | i2c（硬件总线型） | mailbox（框架型） |
|---|---|---|
| 有自己的 `bus_type` 吗 | 有（`i2c_bus_type`） | **没有** |
| `client` 是 `struct device` 吗 | 是（`i2c_client` 内嵌 `struct device`） | **不是**，`mbox_client` 只有一个 `struct device *dev` 指针（`mailbox_client.h:30`） |
| 谁触发 probe | 总线的 match + probe | **没有 probe**，消费者在自己的 probe 里主动调 `mbox_request_channel` |
| controller 会被 driver 绑定吗 | adapter 注册成设备，算法由驱动提供 | `mbox_controller` **不是设备**，它由某个 platform 驱动在 probe 里填好后注册 |

取证：`mailbox_client.h` 和 `mailbox_controller.h` 全文（49 + 140 行）里
**没有 `struct bus_type`、没有 `struct device_driver`、没有 `.probe`**；
`mailbox_controller.h:6-10` 的 include 列表只有
`of.h` / `types.h` / `hrtimer.h` / `device.h` / `completion.h` ——
连 `platform_device.h` 都没有。

所以正确的心智模型是：
**mailbox 核心是一个"通道租借中心"**。
控制器驱动把自己有几条通道登记进来（`mbox_controller_register`），
消费者拿着 DT 里的 phandle 来租一条（`mbox_request_channel`），
核心负责把两边接上、并且**在这条通道上维护一个发送队列**。
`mbox_client` 是**租借申请单**，`mbox_controller` 是**房源登记表**，
`mbox_chan` 是**核心为每条通道开的一本账**。

### §1.2 `mbox_client` 由消费者**内嵌**，不是 kzalloc 出来的指针

看两个真实例子：

```c
/* drivers/soc/qcom/qcom_aoss.c:73-74 —— 按值嵌在驱动私有结构里 */
struct mbox_client mbox_client;
struct mbox_chan *mbox_chan;
```

```c
/* drivers/rpmsg/qcom_smd.c:131-132 —— 一模一样的写法 */
struct mbox_client mbox_client;
struct mbox_chan *mbox_chan;
```

注意这两行的**不对称**：`mbox_client` 是**值**，`mbox_chan` 是**指针**。
原因在下面这条硬约束里：

**`mbox_client` 的生命周期必须覆盖整条通道的生命周期。**
核心只存指针（`mailbox.c:386`，`chan->cl = cl`），不复制内容。
所以它**不能是栈上变量、不能是临时对象** ——
按值嵌进 devm 分配的驱动私有结构，是全树的标准写法。

回调里怎么从 `mbox_client *` 找回驱动自己的结构？`container_of`：

```c
/* drivers/hwmon/xgene-hwmon.c:80 */
container_of(cl, struct xgene_hwmon_dev, mbox_client)
```

这也解释了为什么 `mbox_client` **没有 `void *priv` 字段** ——
不需要，`container_of` 就是它的私有数据通道。
（对照：控制器侧反而有 `con_priv`，原因见 §4.3。）

### §1.3 `active_req` 和 `msg_data[]` 是两个不同的东西

`mbox_chan` 里有两处存"待发的消息"，很容易看成一处：

| | `msg_data[MBOX_TX_QUEUE_LEN]`（`mailbox_controller.h:125`） | `active_req`（`mailbox_controller.h:123`） |
|---|---|---|
| 是什么 | **环形队列**，存"已提交给核心、还没轮到发"的消息 | 单个指针，存"**已经交给硬件、正在飞**的那一条" |
| 深度 | 20（`mailbox_controller.h:103`） | 1 |
| 谁写 | `add_to_rbuf`（`mailbox.c:26-51`） | `msg_submit`（`mailbox.c:79`）/ `tx_tick`（`mailbox.c:100`） |
| 什么时候空 | `msg_count == 0` | `active_req == NULL` ⇒ 硬件空闲，可以提交下一条 |

关键推论：**`mbox_send_message` 返回不等于消息进了硬件。**
它可能只是**进了 `msg_data[]` 排队**（`mailbox.c:263` 的 `add_to_rbuf`），
真正调 `send_data` 的是 `msg_submit`（`mailbox.c:77`），
而 `msg_submit` 一开头就会判**上一条还没飞完就直接放弃**：

```c
/* mailbox.c:62-63 */
if (!chan->msg_count || chan->active_req)
	goto exit;
```

所以 `active_req` 是**硬件占用标志**，不是"当前消息"。
它被清零只有一个地方 —— `tx_tick`（`mailbox.c:100`），
而 `tx_tick` 只由三条 TXDone 路径触发（§1.4）。
**`active_req` 卡住 = 队列永远不再前进**，这是 mailbox 死锁类问题的唯一形态。
排查入口见 `07-debug.md` §3。

### §1.4 "TX 什么时候算完"由**四个字段**表达，分住三个结构体

这是本篇最需要一次看清的一张表。四个字段名字都带 `txdone` 或 `tx`，
但它们的**性质完全不同**：一个是核心的最终决定，两个是控制器的意愿声明，
一个是消费者的能力声明。

| 字段 | 住哪 | 性质 | 谁填 | 谁读 |
|---|---|---|---|---|
| `mbox_controller.txdone_irq` | 控制器（`mailbox_controller.h:79`） | **意愿**：我有 RTR 中断 | 控制器驱动 | 核心，只在注册时读一次（`mailbox.c:493`） |
| `mbox_controller.txdone_poll` | 控制器（`mailbox_controller.h:80`） | **意愿**：我能查上一次 TX 状态 | 控制器驱动 | 核心，只在注册时读一次（`mailbox.c:495`） |
| `mbox_chan.txdone_method` | **通道**（`mailbox_controller.h:120`） | **决定**：这条通道实际用哪种 | 核心（`mailbox.c:518`），**但驱动可以改**（见下） | 核心，每次收 TXDone 都读（`mailbox.c:178`、`:199`） |
| `mbox_client.knows_txdone` | 客户端（`mailbox_client.h:33`） | **能力声明**：我能自己推进状态机 | 消费者 | 核心，**全核心只读一次**（`mailbox.c:389`） |

三个方法常量在 `mailbox.h:6-8`，注释就是它们的定义：

```c
#define TXDONE_BY_IRQ	BIT(0) /* controller has remote RTR irq */
#define TXDONE_BY_POLL	BIT(1) /* controller can read status of last TX */
#define TXDONE_BY_ACK	BIT(2) /* S/W ACK received by Client ticks the TX */
```

**注意这个头文件在 `drivers/mailbox/` 下，不在 `include/linux/` 下** ——
意味着这三个常量**不是控制器驱动的公开接口**，
驱动本来不该直接碰 `txdone_method`。但全树有 3 个驱动确实碰了（见下）。

**核心的决定逻辑是三选一、有优先级、没有"两个都"这回事**
（`mailbox.c:493-498`）：

```
txdone_irq  为真 ────────────────→ TXDONE_BY_IRQ
否则 txdone_poll 为真 ───────────→ TXDONE_BY_POLL
否则 ───────────────────────────→ TXDONE_BY_ACK   /* 源码注释：It has to be ACK then */
```

所以**"两个 flag 都不设"不是"不支持 TXDone"，而是"选 ACK"**。
`mailbox_controller.h:36-42` 的 kernel-doc 明确要求驱动
「must give priority to IRQ method over polling and **never set both**」。

而 `txdone_method` 是**可以被驱动直接改写**的 —— 全树只有 3 处，
但这 3 处足以让"读控制器 flag 推断行为"这条判据失效：

| 驱动 | 行 | 改成什么 | 条件 |
|---|---|---|---|
| `drivers/mailbox/tegra-hsp.c` | `:511` | `TXDONE_BY_IRQ` | 在 `startup` 里无条件改；**只对共享邮箱通道，门铃通道（`:366-370`）不改** |
| `drivers/mailbox/omap-mailbox.c` | `:376` | `TXDONE_BY_ACK` | `if (mbox->send_no_irq)` |
| `drivers/mailbox/pcc.c` | `:306-307`、`:349-350` | — | ACPI，本套已裁掉 |

⇒ **判据修正**：看到一个控制器两个 flag 都不设，**不能**直接下结论说"它是 ACK"，
必须再 grep 一次这个驱动有没有自己写 `txdone_method`。
（这条判据是被实例推翻出来的，过程记在 `99-evidence.md` §4。）

`knows_txdone` 的角色最容易误解，单独说清：

**它不是 `mbox_client_txdone` 的开关。**
`mbox_client_txdone` 的准入检查看的是 `chan->txdone_method` 的 ACK 位
（`mailbox.c:199`），跟 `cl->knows_txdone` 没有关系。
`knows_txdone` 在核心里**只被读一次** —— `mailbox.c:389`，
作用是"控制器选了 POLL、但客户端说自己能 ACK 时，把这条通道翻成 ACK"。
完整讨论和三个 arm64 实例见 `01-api.md` §5.4。

---

## §2 `struct mbox_client` —— 消费者填的申请单

**定义**：`mailbox_client.h:29-38`，kernel-doc 在 `:15-28`。
**由谁分配**：消费者，按值内嵌在自己的私有结构里（§1.2）。
**什么时候读**：`dev` 在申请通道时（`mailbox.c:341`、`:429` 用它找 `of_node`）；
三个回调在通道存续期间随时被调；`tx_block` / `tx_tout` 每次
`mbox_send_message` 都读（`mailbox.c:271`、`:275`、`:277`）。

| 字段 | 谁填 | 谁读 | 说明 |
|---|---|---|---|
| `dev` | 消费者（**必填**） | 核心 | 核心用 `cl->dev->of_node` 找 DT 里的 `mboxes` 属性；为 NULL 时 `mbox_request_channel` 直接 `ERR_PTR(-ENODEV)`（`mailbox.c:348`） |
| `tx_block` | 消费者 | 核心（`mailbox.c:271`） | 真 ⇒ `mbox_send_message` 阻塞等 TXDone；假 ⇒ 立即返回 token。决定了这个 API 能不能在原子上下文调 |
| `tx_tout` | 消费者 | 核心（`mailbox.c:275-278`） | 阻塞上限，**单位 ms**。**填 0 不是"不等"而是"等 1 小时"**（`mailbox.c:275-276` 的 `/* wait forever */` 实际是 `msecs_to_jiffies(3600000)`） |
| `knows_txdone` | 消费者 | 核心（**只读一次**，`mailbox.c:389`） | 见 §1.4。名字暗示"我知道 TX 完了"，实际语义是"我能**推进**状态机"（能调 `mbox_client_txdone`） |
| `rx_callback` | 消费者 | 控制器经核心（`mailbox.c:161`） | **事实上必填**：`mbox_chan_received_data` 不做 NULL 检查，控制器一收到数据就无条件解引用 |
| `tx_prepare` | 消费者（可选） | 核心（`mailbox.c:74-75`） | 在调 `send_data` **之前**被调，用来往共享内存里填 payload。核心这里**有** NULL 检查 |
| `tx_done` | 消费者（可选） | 核心（`mailbox.c:110-111`） | TX 结束通知，**带结果码 `r`**。核心这里**有** NULL 检查 |

三个回调的上下文：`mailbox_client.h:24`、`:25-26`、`:27` 三行 kernel-doc
**全部标了 "Atomic callback"** —— 都不许睡眠。

### §2.1 三个回调的实际使用率（全树普查）

普查方法：在所有引用了 `mbox_client` 的 `.c` 里 grep `(\.|->)<字段>[[:space:]]*=`。

| 回调 | 真实现者 | 显式写 `= NULL` 的 | 备注 |
|---|---|---|---|
| `rx_callback` | 绝大多数客户端 | — | 事实必填，见上表 |
| `tx_prepare` | **3 个**：`drivers/mailbox/mailbox-test.c:342`、`drivers/firmware/arm_scpi.c:970`（函数在 `:421`）、`drivers/firmware/arm_scmi/mailbox.c:144`（函数在 `:35`） | 2 处：`drivers/soc/ti/wkup_m3_ipc.c:642`、`drivers/firmware/mtk-adsp-ipc.c:92` | 三个实现者**都是共享内存型**传输：payload 不走寄存器，得先写内存再敲门铃 |
| `tx_done` | **4 个**（arm64 相关）：`drivers/soc/apple/rtkit.c:742`、`drivers/mailbox/mailbox-test.c:343`、`drivers/hwmon/xgene-hwmon.c:642`、`drivers/firmware/imx/imx-scu.c:304` | 11 处 | ACPI 的 `drivers/acpi/cppc_acpi.c:373` 已裁掉 |

两条观察：

1. **`tx_prepare` 存在的唯一理由是"payload 不在消息里"。**
   共享内存型 mailbox 的 `mbox_send_message(chan, mssg)` 里那个 `mssg`
   往往只是个句柄甚至 NULL，真正的数据在 SRAM 里。
   核心保证 `tx_prepare` 在 `send_data` 之前跑（`mailbox.c:74-77` 相邻两行），
   这个**顺序保证**就是这个回调的全部价值。

2. **11 处显式写 `tx_done = NULL` 值得注意。**
   这些结构基本都是 devm/kzalloc 出来的，本来就是零。
   驱动还是把 NULL 写出来 —— 因为**光看结构体名字判断不出该不该填哪几个回调**，
   写出来是给下一个读代码的人看的。这是本子系统缺少"客户端模板"的一个侧面证据
   （对照 `05-usage.md` §2 给出的骨架）。

---

## §3 `struct mbox_controller` —— 控制器填的房源登记表

**定义**：`mailbox_controller.h:74-88`，kernel-doc 在 `:55-73`。
**由谁分配**：控制器驱动，通常按值内嵌在自己的私有结构里
（例：`drivers/mailbox/imx-mailbox.c:848` 附近的 `priv->mbox`）。
**什么时候读**：注册时集中读一遍（`mbox_controller_register`，`mailbox.c:490-530`），
之后只有 `ops` / `chans` / `num_chans` / `txpoll_period` 在运行期被读。

| 字段 | 谁填 | 谁读 | 说明 |
|---|---|---|---|
| `dev` | 控制器驱动（**必填**） | 核心 | 用于 `dev_err` 归属和 DT 匹配（`mailbox.c:355` 比对 `mbox->dev->of_node`）；`mailbox.c:490-491` 校验它非空，否则 `-EINVAL` |
| `ops` | 控制器驱动（**必填**） | 核心 | 指向 `mbox_chan_ops`，见 §5。同一个 `ops` 被这个控制器的所有通道共用 |
| `chans` | 控制器驱动（**必填**） | 核心 | 指向一个 `num_chans` 长的 `mbox_chan` **数组**，由驱动负责分配；核心只填字段不分配 |
| `num_chans` | 控制器驱动 | 核心（`mailbox.c:513`、`:125`） | 通道数。`mailbox.c:490-491` 校验 `> 0` |
| `txdone_irq` | 控制器驱动 | 核心（`mailbox.c:493`） | 见 §1.4 |
| `txdone_poll` | 控制器驱动 | 核心（`mailbox.c:495`） | 见 §1.4。kernel-doc `:64-66` 明写「**Ignored if 'txdone_irq' is set**」 |
| `txpoll_period` | 控制器驱动 | 核心（`mailbox.c:140`） | 轮询周期，**单位 ms**。只在 POLL 模式有意义 |
| `of_xlate` | 控制器驱动（可选） | 核心（`mailbox.c:365`、`:439`） | DT phandle → 通道的翻译函数。不填则核心装默认实现（`mailbox.c:522-523`），细节见 `01-api.md` §2.3 |
| `poll_hrt` | **核心**（`mailbox.c:506-508`） | 核心 | kernel-doc `:70-72` 标 "API private"。**只在 POLL 分支初始化** —— §4.2 的隐患就出在这里 |
| `poll_hrt_lock` | **核心**（`mailbox.c:510`） | 核心（`mailbox.c:138`） | 同上，API private |
| `node` | **核心**（`mailbox.c:527`） | 核心 | 挂进全局 `mbox_cons` 链表的钩子，API private，见 §8.1 |

**注意这张表后三行的性质**：`mbox_controller` 是驱动分配的结构，
但**最后三个字段由核心写**。驱动只要按值内嵌了这个结构，
就等于同时提供了核心的私有存储 —— 这是内核里常见的做法，
但它带来一条约束：**驱动不能在注册后 memset / 重新初始化这个结构**。

### §3.1 `txpoll_period` 的实际取值（全树普查）

`rg -n 'txpoll_period' drivers/` 共 10 处赋值（9 个常量 + 1 个来自描述符）：

| 值 | 驱动 |
|---|---|
| 1 ms | `drivers/mailbox/platform_mhu.c:152`、`drivers/mailbox/arm_mhu.c:141`、`drivers/mailbox/arm_mhuv2.c:1009`、`drivers/mailbox/bcm-pdc-mailbox.c:1455` |
| 5 ms | `drivers/mailbox/sun6i-msgbox.c:271`、`drivers/mailbox/hi6220-mailbox.c:337`、`drivers/mailbox/zynqmp-ipi-mailbox.c:588` |
| 10 ms | `drivers/mailbox/mailbox-mpfs.c:258` |
| 由描述符给 | `drivers/mailbox/ti-msgmgr.c:920`（`desc->tx_poll_timeout_ms`） |

两条观察：(1) **没有一个驱动填 0** —— 核心也没有校验 0，
填 0 会让 `hrtimer_forward_now(hrtimer, ms_to_ktime(0))`（`mailbox.c:140`）
退化成"立刻再来"，等于软死循环；这是一个**没有防护的填法陷阱**
（记在 `99-evidence.md` §6）。
(2) `hi6220-mailbox.c:337` 那行前面有 `if` —— 说明这个驱动
**运行期才决定用不用 POLL**，是本子系统里少见的动态选择。

### §3.2 `chans` 数组的两种分配写法

普查 `drivers/mailbox/*.c` 里 `chans =` 的赋值，只有两种形状：

```c
/* 形状一：静态数组成员，取首元素地址 —— 通道数编译期已知 */
priv->mbox.chans = &priv->chan[0];        /* drivers/mailbox/sprd-mailbox.c:394 */
apcs->mbox.chans = apcs->mbox_chans;      /* drivers/mailbox/qcom-apcs-ipc-mailbox.c:109 */

/* 形状二：devm_kcalloc —— 通道数由 DT 或硬件寄存器决定 */
mb->mbox.chans = devm_kcalloc(&pdev->dev, drv_data->num_chans,
			      ...);            /* drivers/mailbox/rockchip-mailbox.c:187 */
```

判断用哪种的标准很简单：**`num_chans` 是不是常量**。
`drivers/mailbox/tegra-hsp.c:799` 和 `:827` 同时用了形状二两次 ——
因为它是**一个 platform 设备注册两个 `mbox_controller`**
（门铃 `mbox_db` 和共享邮箱 `mbox_sm`），这是全树唯一的这种写法，
也是 §1.4 那张表里"tegra-hsp 只有一半通道被改 `txdone_method`"的根源。

---

## §4 `struct mbox_chan` —— 核心自己的账本

**定义**：`mailbox_controller.h:118-128`，kernel-doc 在 `:105-117`。
**由谁分配**：控制器驱动（作为 `chans[]` 数组的元素，§3.2）。
**由谁初始化**：**核心**，在 `mbox_controller_register` 的 per-chan 循环里
（`mailbox.c:513-520`）。驱动只需要把内存准备好，字段不用自己填 ——
唯一例外是 `con_priv`（§4.3）。

| 字段 | 谁填 | 谁读 | 说明 |
|---|---|---|---|
| `mbox` | 核心（`mailbox.c:513-520` 循环内） | 双方 | 回指所属控制器。控制器驱动的回调靠它拿到自己的私有结构 |
| `txdone_method` | 核心（`mailbox.c:518`）／驱动可改写 | 核心（`mailbox.c:178`、`:199`、`:459`） | 见 §1.4。**这是"TX 何时算完"的唯一权威字段** |
| `cl` | 核心（`mailbox.c:386` 置、`mailbox.c:457` 清） | 双方 | **它同时是"这条通道有没有被占用"的标志**：`mbox_request_channel` 靠 `chan->cl` 非空判 `-EBUSY`（`mailbox.c:377-379`） |
| `tx_complete` | 核心（`mailbox.c:387` 初始化） | 核心（`mailbox.c:114` 唤醒、`:280` 等待） | 只有 `tx_block` 的客户端会真的等它 |
| `active_req` | 核心（`mailbox.c:79` 置、`:100` 清） | 核心（`mailbox.c:62`、`:128`） | 见 §1.3。**硬件占用标志** |
| `msg_count` / `msg_free` | 核心（`add_to_rbuf` / `msg_submit`） | 核心 | 环形队列的计数和写指针。**没有读指针** —— 读位置由 `msg_free - msg_count` 现算（`mailbox.c:65-70`） |
| `msg_data[20]` | 核心（`mailbox.c:40`） | 核心（`mailbox.c:65-70` 现算下标取出） | 见 §1.3 |
| `lock` | 核心（`mailbox.c:513-520` 循环内） | 核心 | **per-channel spinlock**，见 §8.2 |
| `con_priv` | **控制器驱动** | 控制器驱动 | 核心**完全不碰**，见 §4.3 |

一个容易看漏的事实：**这张表里只有一行是驱动填的**（`con_priv`）。
`mbox_chan` 虽然住在驱动分配的数组里，实质是**核心的私有账本**。
kernel-doc `:108` 说得很直白：「@txdone_method: Way to detect TXDone
**chosen by the API**」。

### §4.1 环形队列没有读指针

队列只有 `msg_count`（有几条）和 `msg_free`（下一条写哪）两个数。
取出位置是现算的（`mailbox.c:65-70`）：

```c
idx = chan->msg_free - chan->msg_count;   /* 可能为负 */
if (idx < 0)
	idx += MBOX_TX_QUEUE_LEN;
data = chan->msg_data[idx];
```

这个写法的好处是**两个数就够表达空/满**：
`msg_count == 0` 是空，`msg_count == MBOX_TX_QUEUE_LEN` 是满
（`mailbox.c:34` 就靠这个判 `-ENOBUFS`）——
不需要经典环形队列那种"留一格区分空满"的技巧。

代价是：**队列深度硬编码为 20**（`mailbox_controller.h:103`），
而且**没有任何运行期出口能看到当前深度**。
`mailbox_controller.h:90-102` 那段注释解释了为什么 20 够用
（「每次传输都是中断驱动的……中断延迟才是瓶颈，不是缓冲区长度」），
但同一段注释末尾也留了 `REVISIT`：如果太多平台看到
`Try increasing MBOX_TX_QUEUE_LEN` 这条打印，就该做成配置项。
这条打印在 `mailbox.c:265`，而**它建议的操作用户做不到** ——
`MBOX_TX_QUEUE_LEN` 是头文件里的 `#define`，不是 Kconfig。
（记在 `99-evidence.md` §6。）

### §4.2 `poll_hrt` 只在 POLL 分支初始化 —— 一条静态推断的隐患

注册时的 POLL 分支（`mailbox.c:500-511`）做三件事：
校验 `ops->last_tx_done` 存在（不存在就 `dev_err` + `-EINVAL`）、
`hrtimer_init` + 装 `txdone_hrtimer`、`spin_lock_init(&mbox->poll_hrt_lock)`。
**ACK 和 IRQ 两个分支这三件事一件都不做。**

而轮询定时器的回调 `txdone_hrtimer`（`mailbox.c:117-146`）
**无条件解引用 `last_tx_done`**：

```c
/* mailbox.c:128-129 */
if (chan->active_req && chan->cl)
	txdone = chan->mbox->ops->last_tx_done(chan);
```

于是只要有办法让一条**ACK 模式的通道**变成 POLL，就同时踩两个雷：
`hrtimer_start` 打在未初始化的 hrtimer 上（`mailbox.c:88`），
以及 `last_tx_done` 为 NULL 的解引用（`mailbox.c:129`）。

**这个办法存在**：`mbox_free_channel`（`mailbox.c:459-460`）
把 ACK 无条件翻成 POLL，而再次申请时只有 `cl->knows_txdone` 为真才翻回来
（`mailbox.c:389-390`）。完整链条和"最近的触发点其实是
`mbox_request_channel:399`（startup 失败时就会调 free）"这一节，
见 `01-api.md` §5.3。**本条是静态推断，未在硬件上复现**，
定性记在 `99-evidence.md` §6 第 ① 条。

顺带注意 `txdone_hrtimer:128` 的 `chan->active_req && chan->cl` 这道检查 ——
**ACK 路径（`mbox_client_txdone`）和 IRQ 路径（`mbox_chan_txdone`）都没有这道检查**，
它们只查 `txdone_method` 的对应位（`mailbox.c:178`、`:199`）就直接进 `tx_tick`。
三条路径的防护强度不一致，这是 `03-internals.md` §B3 的话题。

### §4.3 `con_priv` —— 控制器驱动挂私有数据的唯一钩子

`mailbox_controller.h:127`，kernel-doc `:116` 写「@con_priv: Hook for
controller driver to attach private data」。核心**从不读写它**。

它解决的问题是：控制器的回调签名只有 `struct mbox_chan *chan`
（`mailbox_controller.h:47-52` 六个回调全是这个形状），
驱动必须只靠这个指针找到"这是我的第几条通道、对应哪个寄存器"。
两条路都通：

| 路径 | 怎么走 | 适合什么 |
|---|---|---|
| `chan->con_priv` | 驱动在 probe 里给每条通道挂一个自己的小结构 | 主流做法，全树 **45 个文件**用到 `con_priv` |
| `chan - mbox->chans` | 用指针相减算下标 | 通道之间只差一个索引时够用 |

对比 §1.2：消费者侧**没有** `priv` 字段（用 `container_of`），
控制器侧**有** `con_priv`。差别的原因是方向不同 ——
消费者是"我自己的结构里嵌了 `mbox_client`"，能反推；
控制器拿到的 `mbox_chan` 是**数组元素**，`container_of` 反推不到驱动结构，
只能靠 `chan->mbox` 再 `container_of` 一次，或者直接读 `con_priv`。

---

## §5 `struct mbox_chan_ops` —— 控制器要实现的六个回调

**定义**：`mailbox_controller.h:46-53`，kernel-doc 在 `:14-45`。
**由谁分配**：控制器驱动，通常是 `static const`。
**核心怎么用**：`chan->mbox->ops->xxx`，一个控制器的所有通道共用同一份。

本节只给**字段表和填法约束**；每个回调的完整语义、必需性普查、
以及"官方文档说的必需性是错的"这一节，都在 `01-api.md` §3。

| 字段 | 上下文 | 返回 0 的含义 | 填法约束 |
|---|---|---|---|
| `send_data` | **原子**（`mailbox_controller.h:16-22`：「It must not sleep」） | 远端已收下这一条 | **唯一被核心无条件解引用的回调**（`mailbox.c:77`）—— 不填必崩。返回 `-EBUSY` 表示远端还没读走上一条，但 kernel-doc `:39-41` 说这个返回值**只在 POLL 模式下被期待** |
| `flush` | 原子（在调用方的上下文里忙等） | 队列已排空 | 可选。`mailbox_controller.h:23-25`：客户端要求阻塞但上下文不允许睡眠时用；实现里**必须自己调 `mbox_chan_txdone`**（`mailbox.c:298` 的 kernel-doc） |
| `startup` | **可以睡** | 通道已就绪 | 可选。`:26-31`：返回之后控制器**必须**开始用 `mbox_chan_received_data` 上报收到的数据 |
| `shutdown` | **可以睡** | — | 可选，返回 `void`。`:32-35`：必须停止上报 |
| `last_tx_done` | **原子**（hrtimer 软中断里被调，`mailbox.c:129`） | — 返回 `bool` | **选了 POLL 就必填** —— 注册时校验，缺了直接 `dev_err` + `-EINVAL`（`mailbox.c:502-505`） |
| `peek_data` | 原子（`:43-44`） | — 返回 `bool` | 可选。不实现时 `mbox_client_peek_data` 恒返回 false（`mailbox.c:225`、`:228`） |

三条容易踩的填法约束，合起来说：

1. **不许两个 flag 都设**（`mailbox_controller.h:36-42`）——
   `last_tx_done` 的 kernel-doc 里同时写了这条，因为它是 POLL 模式的专属回调：
   「Used only if txdone_poll:=true && txdone_irq:=false」。
2. **`startup` / `shutdown` 可以睡，其余四个不可以。**
   原因在 §8.2：`send_data` 和 `tx_prepare` 是在 `chan->lock` 这把 spinlock **里面**被调的。
3. **回调里不许回调回 mailbox API。** `startup` 在 `con_mutex` 里被调（§8.1），
   `send_data` 在 `chan->lock` 里被调 —— 两者都会立即自死锁。

---

## §6 核心抽象 ↔ 消费者关系图

mailbox 没有自己的 `bus_type`（§1.1），所以这里画的不是"两条总线"，
而是**三方之间谁指向谁**。箭头方向 = 指针方向。

```
   ┌──────────────────────── 消费者驱动（任意子系统） ────────────────────────┐
   │  struct my_priv {                                                       │
   │      struct device        *dev;                                         │
   │      struct mbox_client    cl;      ← 按值内嵌（§1.2）                   │
   │      struct mbox_chan     *chan;    ← mbox_request_channel() 的返回值    │
   │  };                     ▲     │                                         │
   └─────────────────────────┼─────┼─────────────────────────────────────────┘
                             │     │
              cl->dev / 三个回调    │ chan（不透明句柄，消费者不该解引用）
                             │     ▼
   ┌═════════════════════════╪═══════════════════════════════════════════════┐
   ║  mailbox 核心           │   drivers/mailbox/mailbox.c（626 行）          ║
   ║                         │                                               ║
   ║   static LIST_HEAD(mbox_cons)   ← 全局：所有已注册控制器（`:23`）        ║
   ║          │                                                              ║
   ║          │  遍历比对 mbox->dev->of_node（`:355`）                        ║
   ║          ▼                                                              ║
   ║   ┌─────────────────────┴────────────────────────┐                      ║
   ║   │ struct mbox_chan  chans[i]  ← 核心的账本      │                      ║
   ║   │   .cl            ─────────────────────────────┼──→ 回上面的 mbox_client
   ║   │   .mbox          ─────┐                      │                      ║
   ║   │   .txdone_method      │                      │                      ║
   ║   │   .active_req / msg_data[20] / msg_count      │  ← 发送队列（§1.3）  ║
   ║   │   .lock （per-channel spinlock）              │                      ║
   ║   │   .con_priv      ─────┼──→ 控制器驱动的私有小结构（§4.3）           ║
   ║   └───────────────────────┼──────────────────────┘                      ║
   ╚═══════════════════════════╪══════════════════════════════════════════════╝
                               │ chan->mbox
                               ▼
   ┌──────────────────────── 控制器驱动（一个 platform 驱动） ─────────────────┐
   │  struct my_mbox_priv {                                                   │
   │      void __iomem          *base;                                        │
   │      struct mbox_controller mbox;   ← 按值内嵌                           │
   │      struct mbox_chan       chan[N];  ← 数组由驱动提供（§3.2）           │
   │  };                                                                      │
   │        mbox.ops ──→ static const struct mbox_chan_ops（§5，六个回调）    │
   │        mbox.chans ──→ &chan[0]                                           │
   │        mbox.txdone_irq / txdone_poll  ← 只在注册时被读一次（§1.4）        │
   └──────────────────────────────────────────────────────────────────────────┘
```

三条读图要点：

1. **`mbox_chan` 的存储归控制器驱动，内容归核心。**
   数组是驱动分配的（`chans`），但除 `con_priv` 外每个字段都是核心写的（§4）。
   这解释了为什么控制器驱动 remove 时**不能先释放 `chans` 再 unregister**。
2. **消费者和控制器驱动之间没有任何直接指针。**
   两边唯一的联系是 `chan`：消费者持 `chan`，控制器的回调收 `chan`。
   所以"某条通道被谁占了"这个信息只存在核心的 `chan->cl` 里，
   而它**没有任何 sysfs / debugfs 出口**（`07-debug.md` §4）。
3. **`mbox_cons` 是全局唯一的注册表。** 没有 per-controller 的 class / device，
   所以 `/sys/class/` 下**看不到 mailbox 控制器** ——
   只能在 platform 总线下看到承载它的那个设备。

---

## §7 核心数据流：一条消息从 `mbox_send_message` 到硬件

竖线是时间。注意左半边（提交）和右半边（完成）是**两条独立的路径**，
它们唯一的交汇点是 `tx_tick`。

```
消费者                     核心 mailbox.c                     控制器驱动 / 硬件
  │
  │ mbox_send_message(chan, mssg)
  ├──────────────────────────→ :260  查 chan / chan->cl，NULL 则 -EINVAL
  │                            :263  add_to_rbuf() ─┐
  │                                                 │ 取 chan->lock (:31)
  │                                                 │ 满则 -ENOBUFS (:36)
  │                                                 │ msg_data[msg_free]=mssg (:40)
  │                                                 └ 返回 idx —— 这就是 token
  │                            :269  msg_submit() ──┐
  │                                                 │ 取 chan->lock (:60)
  │                                   上一条还在飞就直接放弃 (:62-63)
  │                                                 │ 现算下标取出 (:65-70)
  │   tx_prepare(cl, mssg) ←───────────────────────  :74-75  在 spinlock 里
  │                                                 │
  │                                                 │  send_data(chan, data) ────→ 写寄存器 /
  │                                                 │  :77  在 spinlock 里         敲门铃
  │                                                 │ active_req = data (:79)
  │                                                 │ msg_count-- (:80)
  │                              POLL 模式：hrtimer_start (:85-90)
  │                                                 └ 放 chan->lock (:83)
  │  ┌─ tx_block == false ─→ 立刻返回 token（:287）
  │  └─ tx_block == true  ─→ wait_for_completion_timeout（:280），
  │                          超时则 t = -ETIME 并自己调 tx_tick（:281-284）
  │
  ┊                        ── 以下是"完成"路径，三条来源 ──
  ┊
  ┊   ① IRQ 模式：控制器收到 RTR 中断 → mbox_chan_txdone(chan, r)  :176
  ┊       └ :178 校验 txdone_method 有 IRQ 位，否则 dev_err 并 return
  ┊   ② POLL 模式：hrtimer 到期 → txdone_hrtimer  :117
  ┊       └ :128 查 active_req && cl → :129 调 last_tx_done()
  ┊   ③ ACK 模式：消费者自己收到应答 → mbox_client_txdone(chan, r)  :197
  ┊       └ :199 校验 txdone_method 有 ACK 位，否则 dev_err 并 return
  ┊                              │
  ┊                              ▼
  ┊                        tx_tick(chan, r)   :93
  │                            :98   取 chan->lock
  │                            :100  active_req = NULL   释放硬件占用
  │                            :101  放 chan->lock
  │                            :104  msg_submit(chan)    立刻推进下一条
  │                            :106  队列空了就 return
  │  tx_done(cl, mssg, r) ←───  :110-111                 已在锁外
  │                            :113-114  r != -ETIME 且 tx_block 时 complete()
  │
  ┊                        ── 接收路径（完全独立，无队列无锁）──
  ┊
  │  rx_callback(cl, mssg) ←─── :161  mbox_chan_received_data ←── 控制器中断
  │                            不查 chan->cl，不加锁（§8.3）
```

四条读图要点：

1. **`mbox_send_message` 返回的是 `add_to_rbuf` 的下标**，不是 0。
   `01-api.md` §1.2 专门讲了这个 token 的性质。
2. **`tx_tick` 里 `msg_submit` 在 `tx_done` 之前**（`:104` vs `:110`）。
   意味着消费者的 `tx_done` 被调时，**下一条消息可能已经在飞了**。
   在 `tx_done` 里做"清理刚发完那条的资源"是安全的，
   做"现在硬件空闲了"这种假设是错的。
3. **超时不是由定时器发现的，是由等待方发现的**（`:281-284`）：
   `tx_block == false` 的客户端**永远不会得到超时通知** ——
   消息卡在 `active_req` 里，没有任何机制把它清掉。这是 §1.3 末尾那句话的完整含义。
4. **接收路径没有任何流控**。没有队列、没有锁、没有 NULL 检查，
   控制器中断直接穿透到消费者的 `rx_callback`。
   所以 `rx_callback` 的执行时间**就是**中断处理时间。

---

## §8 全局变量与锁

### §8.1 全局变量只有两个

| 符号 | 定义 | 是什么 |
|---|---|---|
| `mbox_cons` | `mailbox.c:23`（`static LIST_HEAD`） | 所有已注册 `mbox_controller` 的链表，用 `mbox_controller.node` 串起来 |
| `con_mutex` | `mailbox.c:24`（`static DEFINE_MUTEX`） | 保护上面这条链表 |

就这两个。**没有 idr、没有 class、没有全局计数器** ——
这也是本子系统"核心零 debugfs、零 tracepoint"的结构性原因：
没有可枚举的全局注册表出口，就没有地方挂观测点（`07-debug.md` §1）。

`con_mutex` 的持有区间（普查 `mailbox.c` 里的 `mutex_lock/unlock`）：

| 区间 | 函数 | 里面做了什么值得注意的事 |
|---|---|---|
| `:351` – `:404` | `mbox_request_channel` | **`startup` 回调在这个区间里被调**（`:396` 附近）—— 所以 `startup` 虽然允许睡眠，**绝对不能调任何 mailbox API** |
| `:525` – `:527` | `mbox_controller_register` | 只是 `list_add_tail`，区间极短 |
| `:544` – `:554` | `mbox_controller_unregister` | 摘链 + 逐通道 `mbox_free_channel` |

**一处不对称**：`mbox_free_channel`（`:445-464`）**不取 `con_mutex`**，
只取 `chan->lock`（`:456-462`）。所以
「申请通道」是全局串行的，「释放通道」不是。
`03-internals.md` §B2 会讨论这个不对称在 unbind 竞态里的后果。

### §8.2 两把 spinlock，嵌套顺序是固定的

| 锁 | 粒度 | 初始化 | 保护什么 |
|---|---|---|---|
| `mbox_chan.lock` | **per-channel** | `mailbox.c:519`（注册时逐通道） | 环形队列 + `active_req` + `cl` |
| `mbox_controller.poll_hrt_lock` | **per-controller** | `mailbox.c:510`，**只在 POLL 分支** | `poll_hrt` 的重排定时 |

持有 `chan->lock` 的五处：`add_to_rbuf`（`:31-48`）、`msg_submit`（`:60-83`）、
`tx_tick`（`:98-101`）、`mbox_request_channel`（`:382-392`）、
`mbox_free_channel`（`:456-462`）。

**嵌套顺序：`chan->lock` → `poll_hrt_lock`，只有一处**：
`msg_submit` 在自己的 `chan->lock` 区间（`:60-83`）里面
又取了 `poll_hrt_lock`（`:87-89`）。
反向的嵌套不存在 —— `txdone_hrtimer` 取 `poll_hrt_lock`（`:138-141`）时
**没有**持 `chan->lock`（它调 `tx_tick`，由 `tx_tick` 自己去取）。
所以这两把锁不会 AB-BA。

**`send_data` 和 `tx_prepare` 都在 `chan->lock` 里被调**
（`:74-75` 和 `:77`，都落在 `:60-83` 这个区间内）。
这一条解释了本子系统几乎所有的"不许睡眠"约束：

- 控制器的 `send_data` 不能睡、不能等中断、不能 `msleep`
- 消费者的 `tx_prepare` 不能睡，**也不能在里面调 `mbox_send_message`**
- 反过来，`tx_done`（`:110-111`）和 `rx_callback`（`:161`）
  **是在锁外被调的** —— 但它们仍然被 kernel-doc 标为 "Atomic callback"
  （`mailbox_client.h:24`、`:27`），因为它们通常在控制器的**硬中断上下文**里

### §8.3 接收路径完全无锁

`mbox_chan_received_data`（`mailbox.c:158-163`）不取任何锁、不查 `chan->cl`。
契约只写在注释里（`mailbox.c:154-156`）。
这是本子系统一个真实的自陈问题：
控制器如果在 `shutdown` 之后仍然上报数据，核心会解引用一个已经被清成 NULL 的
`chan->cl`（`mailbox.c:457`）。核心**完全依赖控制器驱动遵守
"`shutdown` 返回后不再上报"这条口头约定**（`mailbox_controller.h:32-35`）。
记在 `99-evidence.md` §6。

---

## §9 命名约定速查

| 前缀 / 形状 | 含义 | 例子 |
|---|---|---|
| `mbox_*` | **对外 API**（消费者侧和控制器侧都用这个前缀） | `mbox_request_channel` / `mbox_controller_register` |
| `mbox_chan_*` | **控制器 → 核心**的上报方向 | `mbox_chan_received_data` / `mbox_chan_txdone` |
| `mbox_client_*` | **消费者 → 核心**的方向 | `mbox_client_txdone` / `mbox_client_peek_data` |
| 无前缀 `static` | 核心内部函数 | `add_to_rbuf` / `msg_submit` / `tx_tick` / `txdone_hrtimer` |
| `TXDONE_BY_*` | 通道的 TXDone 方法位 | 三个，定义在 `drivers/mailbox/mailbox.h:6-8` |
| `cl` | 局部变量惯例：`struct mbox_client *` | 全树一致 |
| `chan` | 局部变量惯例：`struct mbox_chan *` | 全树一致 |
| `mbox` | **歧义变量名**：既可能是 `mbox_controller *`，也可能是驱动私有结构 | 核心里指 `mbox_controller *`；驱动里两种都有 |

一组**最容易混的近邻符号**，必须成对记：

| 符号 | 谁调 | 准入条件 |
|---|---|---|
| `mbox_chan_txdone` | 控制器驱动 | `txdone_method` 有 **IRQ** 位（`mailbox.c:178`） |
| `mbox_client_txdone` | 消费者 | `txdone_method` 有 **ACK** 位（`mailbox.c:199`） |

两个函数体几乎一样，两条 `dev_err` 字符串也只差一个词
（`Controller can't run the TX ticker` `:179-180` vs
`Client can't run the TX ticker` `:200`）。
调错了不会崩，只会**每次发送都打一条 dev_err 而消息永远不推进** ——
这两条字符串是排查这类问题的关键线索（`07-debug.md` §2 的错误消息映射表）。

---

## §10 读完本篇之后

| 你接下来想知道 | 去哪 |
|---|---|
| 每个 API 的签名、返回值、错误码语义 | `01-api.md` |
| `mbox_chan_ops` 六个回调的完整语义和必需性普查 | `01-api.md` §3 |
| ACK→POLL 隐患、`knows_txdone` 的完整讨论 | `01-api.md` §5.3 / §5.4 |
| 注册 / 申请 / 发送 / 释放的逐段流程 | `03-internals.md` Part A |
| 三条 TXDone 路径的防护强度差异、free 不取 `con_mutex` 的后果 | `03-internals.md` §B2 / §B3 |
| `mailbox-test` 这个测试框架怎么用 | `04-extensions.md` |
| 消费者 / 控制器驱动的代码骨架（含本篇每条约束的落地写法） | `05-usage.md` |
| DTS 里 `mboxes` / `mbox-names` / `#mbox-cells` 怎么写 | `06-devicetree.md` |
| 上面每个"没有出口"的观测缺口，实际怎么绕 | `07-debug.md` |
| 为什么 TX 完成要分三种模式（大白话版） | `08-feynman.md` |
| 本篇每条论断的出处，以及哪些是推断而非观察 | `99-evidence.md` |
