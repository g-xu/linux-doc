---
title: 费曼学习法
---

# 08 费曼式解释：mailbox 里最容易想错的八件事

> **本篇的组织轴：一节一个"我以前理解错了"的问题。**
> 每节四段：**问题** → **先举个例子**（生活类比）→ **回到代码**（行号）→
> **这个类比什么时候会误导你？**（类比的边界）。
>
> 第四段是本篇的核心。类比的价值在于让你先站住，
> 但每个类比都有失效的地方 —— 不点出失效点，类比就变成新的误解来源。
>
> 格式约定同前：`mailbox.c` 指 `drivers/mailbox/mailbox.c`。arm64-only。
> 本篇每个技术断言都带行号，费曼篇不豁免取证。

---

## §1 `mbox_client` 和 `mbox_controller`，是不是 device 和 driver 的关系？

### 先举个例子

想想**小区的快递柜**。

快递柜（`mbox_controller`）装在小区门口，一共 12 个格口（`mbox_chan`）。
你（`mbox_client`）是住户，想寄件时去柜子那儿领一个格口用。

现在问一个问题：**快递柜和你，是"设备和驱动"的关系吗？**

显然不是。驱动和设备是"一个软件模块管一类硬件"的关系 ——
驱动去 *探测*、*初始化*、*管理* 设备。
但你和快递柜之间不是这样：**你只是去借一个格口，用完还回去**。
柜子不管你是谁，你也不管柜子内部怎么工作。
你们之间是**资源出借方和借用方**。

而且注意一个细节：**你不需要"注册"成一个住户**。
你走到柜子跟前，说"给我一个格口"，柜子给你一个，就完了。
没有任何"住户名册"。

### 回到代码

三件事印证这个类比：

1. **`mbox_client` 不是 `struct device`，也不注册到任何总线上。**
   `include/linux/mailbox_client.h` 里 `struct mbox_client` 只有 7 个字段
   （`dev`、`tx_block`、`tx_tout`、`knows_txdone`、三个回调），
   **没有一个字段是 `struct device`** —— 它只有一个指向消费者自己 device 的
   `dev` **指针**。

2. **mailbox 根本没有 `bus_type`。**
   ```
   /usr/bin/grep -rn 'bus_type' drivers/mailbox/ include/linux/mailbox*.h
      → 零输出
   ```
   ⇒ 没有总线，就没有 match、没有 probe、没有 device/driver 绑定。
   全局状态只有一个链表 + 一把锁（`mailbox.c:23-24`）：
   ```c
   static LIST_HEAD(mbox_cons);          /* :23  只有控制器进这个链表 */
   static DEFINE_MUTEX(con_mutex);       /* :24 */
   ```
   **消费者从来不进任何链表** —— 就像住户不进名册。

3. **`mbox_client` 由消费者按值内嵌，不是 kzalloc 出来的。**
   典型写法（`drivers/rpmsg/qcom_smd.c:131`）：
   ```c
   struct qcom_smd_edge {
   	…
   	struct mbox_client mbox_client;      /* 按值内嵌 */
   	struct mbox_chan *mbox_chan;
   };
   ```
   ⇒ 生命周期完全跟着消费者自己的结构体走，**核心不负责它的分配和释放**。
   这也是为什么 `mbox_request_channel` 只需要一个 `cl` 指针
   （`mailbox.c:337`），不需要任何"注册消费者"的 API。

### 这个类比什么时候会误导你？

**三处失效**：

1. **快递柜的格口是"谁先来谁用"，mailbox 的通道是 DTS 指定的。**
   你不能说"随便给我一个空格口" —— 消费者必须在 DTS 里
   写清楚"我要 `&mailbox` 的第 2 条通道"（`06-devicetree.md` §3.1）。
   ⇒ mailbox 的通道**不是资源池，是静态绑定**。

2. **快递柜的格口可以两个人轮流用，mailbox 的通道被独占。**
   `mailbox.c:376` 那句 `if (chan->cl || !try_module_get(...))` ⇒
   已经有主的通道**直接返回 `-EBUSY`**（`:379`），核心不排队、不共享。

3. **最重要的一处**：快递柜坏了你会知道（打不开），
   但**mailbox 的通道申请失败你可能什么都看不到** ——
   `mbox_request_channel` 的四个失败出口里三个是 `dev_dbg`/`pr_debug`，
   一个（`:371-374`）连日志都没有（`07-debug.md` §2.1）。
   ⇒ 这个类比让你以为"失败会有反馈"，实际上**默认是静默的**。

那么正确的类比是什么？**它更像"从内核里的某个全局资源表按下标取一项"** ——
比如 `clk_get`、`gpiod_get`、`reset_control_get`。
mailbox 属于典型的**框架型子系统**：靠 API 被消费，而不是靠总线被绑定
（`02-datastructures.md` §1.1 有完整的对比表）。

---

## §2 一条"什么都不传"的消息，为什么是合法的？

### 先举个例子

想想**门铃**和**信件**。

信件里有内容 —— 你写了字，对方读到字。
门铃里没有内容 —— 你按一下，对方只知道"有人来了"。

但这两件事在"通信"这个层面是等价的：**都传递了信息**。
门铃传的信息量是 1 bit（有人 / 没人），但这 1 bit 够用了。

更妙的一种组合：**你把一封信塞进对方门口的信箱，然后按一下门铃**。
门铃说"去看信箱"，信箱里放着真正的内容。
⇒ 这时门铃**本身不需要携带任何数据**，但整套机制传递了任意多的数据。

这正是 arm64 上绝大多数 mailbox 的真实工作方式：
**共享内存 + 门铃**。

### 回到代码

`mbox_send_message(chan, mssg)` 的 `mssg` 类型是 **`void *`**，
而核心**从头到尾不解引用它一次**：

| 核心对 `mssg` 做的事 | 行 |
|---|---|
| 存进环形队列 | `mailbox.c:40` `chan->msg_data[idx] = mssg;` |
| 从队列取出、赋给 `active_req` | `:72` `data = chan->msg_data[idx];` / `:79` `chan->active_req = data;` |
| 原样传给控制器的 `send_data` | `:77` `err = chan->mbox->ops->send_data(chan, data);` |

三处全是**指针搬运**，一次都没有 `*mssg` 或 `mssg->xxx`。
⇒ **`NULL` 是完全合法的载荷。**

真实例子（Tegra BPMP，arm64）：

```c
	/* drivers/firmware/tegra/bpmp-tegra186.c:86 */
	err = mbox_send_message(channel->ib->mbox, NULL);
	…
	mbox_client_txdone(channel->ib->mbox, 0);      /* :90 */
```

真实数据早就通过 IVC（共享内存）放好了，
mailbox 在这里**纯粹是门铃**。同一份逻辑在 SCMI 上也一样 ——
`shmem` 属性指向共享内存，mailbox 只负责踢一脚
（`06-devicetree.md` §5.1，而且 **mailbox 核心完全不读 `shmem`**）。

### 这个类比什么时候会误导你？

1. **不是所有 mailbox 都是门铃。** 有一大类控制器的"消息"就是数据本身：
   `bcm2835-mailbox.c:83` 就地 `*(u32 *)data` 解引用、`:86` 把这个 `u32` 写进寄存器；
   `imx-mailbox.c:395-402` 在循环里**逐字**把载荷搬进硬件 FIFO。
   ⇒ 判断自己是哪一类的方法：**看 DTS 有没有 `shmem` / `reg`**
   （`06-devicetree.md` §5、`07-debug.md` §4.2）。

2. **门铃类比会让你低估"载荷格式错了"的危险性。**
   按门铃按错了会有反应（对方开门发现没人），
   但 mailbox 传错格式**核心一个字都不会报** ——
   因为它从不解引用（上面那三行）。
   症状是远端行为异常或上层框架超时，**不是 API 返回错误码**
   （`04-extensions.md` §2.3、`07-debug.md` §7.2）。

3. **门铃是"按了就完了"，mailbox 还要等一个"发完了"的确认。**
   即使载荷是 NULL，`tx_block = true` 的客户端仍会阻塞等
   `tx_complete`（`mailbox.c:280`）。这就是下一节的题目。

---

## §3 "消息发出去了"，为什么需要三种不同的说法？

### 先举个例子

你把一封信投进邮筒。**什么时候算"寄出去了"？**

三种答案，对应三种邮筒：

| 邮筒的样子 | 你怎么知道寄出去了 | 对应 |
|---|---|---|
| 投递口带一个**回弹的挡板**，信滑下去时挡板"啪"一声 | **听到响声就知道了** —— 邮筒主动告诉你 | `TXDONE_BY_IRQ` |
| 投递口是个**透明的小窗**，能看见信还卡在口子里 | **你得每隔几秒低头看一眼**，直到看不见了 | `TXDONE_BY_POLL` |
| 投递口啥都没有，但**收信人会给你发个微信说"收到了"** | **要等收信人告诉你**，邮筒完全帮不上忙 | `TXDONE_BY_ACK` |

关键在于：**这三种是硬件能力决定的，不是软件选择的**。
你的邮筒有挡板就是有，没有就是没有。

### 回到代码

三种模式的判定在 `mbox_controller_register`（`mailbox.c:493-498`）：

```c
	if (mbox->txdone_irq)
		txdone = TXDONE_BY_IRQ;        /* :494  邮筒会响 */
	else if (mbox->txdone_poll)
		txdone = TXDONE_BY_POLL;       /* :496  能看见 */
	else /* It has to be ACK then */
		txdone = TXDONE_BY_ACK;        /* :498  只能等收信人 */
```

三种模式下"谁调 `tx_tick`"完全不同（`tx_tick` 是唯一会
`complete(&chan->tx_complete)` 的地方，`mailbox.c:93-115`，落点在 `:113-114`）：

| 模式 | 谁调 `tx_tick` | 入口 |
|---|---|---|
| IRQ | 控制器的中断处理函数 | `mbox_chan_txdone` `:176-185` |
| POLL | 核心自己的 hrtimer，反复问控制器 `last_tx_done` | `txdone_hrtimer` `:117-146`（`:129` 调 `last_tx_done`） |
| ACK | **客户端**（收到对端的应答后） | `mbox_client_txdone` `:197-205` |

配错了会怎样？两条日志（`07-debug.md` §6.4）：

```c
	/* :178-182 —— 控制器调了 mbox_chan_txdone，但通道不是 IRQ 模式 */
	if (unlikely(!(chan->txdone_method & TXDONE_BY_IRQ))) {
		dev_err(chan->mbox->dev, "Controller can't run the TX ticker\n");
		return;                        /* 直接 return，tx_tick 不会被调 */
	}
```

⇒ `tx_complete` 永远不 complete ⇒ 阻塞客户端等到超时（`-ETIME`，`:282`）。
**所以这两条日志后面通常紧跟一个 `-ETIME`。**

### 这个类比什么时候会误导你？

1. **邮筒的类比暗示"三选一"，但代码里 `txdone_method` 是个位掩码**，
   而且**可以同时置两位**：`mbox_request_channel:389-390`
   在 `cl->knows_txdone` 时会**往已有模式上再加一个 ACK 位**。
   ⇒ 真实世界里有"既会响、又能收到微信"的邮筒。
   而 `mbox_controller_unregister:551` 检查的是**控制器的 flag** 而不是
   推导出的 `txdone_method`，这是个健壮性缺口
   （`99-evidence.md` §4 记录了对它的交叉验证：全树无人同时设两个 flag，**不是活 bug**）。

2. **"硬件能力决定模式"这句话有例外。** 有 3 个驱动**在运行期直接改写
   `chan->txdone_method`**（`02-datastructures.md` §1.4）：
   `drivers/mailbox/tegra-hsp.c:511` 在 `startup` 里把共享邮箱强制改成 IRQ
   （门铃通道不改）、`drivers/mailbox/omap-mailbox.c:376` 在
   `send_no_irq` 时强制 ACK，第三处在已裁的 ACPI PCC 里。
   ⇒ **"控制器没设 flag ⇒ 一定是 ACK"这个推断必须再查一次驱动有没有自己改**。
   本文档集在这一点上曾经把 tegra-hsp 误判成 bug，被 `:511` 推翻。

3. **`knows_txdone` 不是 ACK 模式的开关。** 名字很像，但
   `mbox_client_txdone` 的准入条件看的是 `chan->txdone_method`
   的 ACK 位（`:199`），而 `cl->knows_txdone` 在全核心**只被读一次**（`:389`）。
   ⇒ 有三个 arm64 实例设了 `knows_txdone = false` 却照样在用
   `mbox_client_txdone`（`02-datastructures.md` §1.4）。

---

## §4 为什么控制器的 `send_data` 里绝对不能睡觉？

### 先举个例子

想想**收银台**。

一个收银员（`chan->lock` 这把 spinlock）一次只服务一个顾客。
规则很简单：**你占着收银台的时候，不能去干别的事**。
不能说"等一下，我去货架上再拿瓶水" —— 后面排着队，
而且**这个队是不能睡的**：所有人都得站着干等（spinlock 是忙等，不睡眠）。

现在关键问题：**"把货物递给收银员"这个动作，是在收银台前完成的，还是完成之后？**

如果是**在收银台前**，那么这个动作必须极快 —— 不能有任何"去拿"的余地。

mailbox 的答案就是：**在收银台前**。

### 回到代码

`msg_submit`（`mailbox.c:53-95`）的骨架：

```c
	spin_lock_irqsave(&chan->lock, flags);        /* :60  拿收银台 */
	…
	if (chan->cl->tx_prepare)
		chan->cl->tx_prepare(chan->cl, data);     /* :74-75  在锁内 */
	err = chan->mbox->ops->send_data(chan, data); /* :77     也在锁内 */
	…
	spin_unlock_irqrestore(&chan->lock, flags);   /* :83 */
```

**`tx_prepare` 和 `send_data` 两个回调都在 spinlock 内被调用。**
这是 mailbox 所有"不许睡眠"约束的唯一根因：

| 谁受约束 | 具体不能做什么 |
|---|---|
| 控制器的 `send_data` | 不能 `msleep`、不能 `mutex_lock`、不能 `regmap` 走 I2C/SPI、不能 `kmalloc(GFP_KERNEL)` |
| 客户端的 `tx_prepare` | 同上 —— 全树 3 个实现者都只做 `memcpy_toio`（`02-datastructures.md` §2.1） |

对比一下**锁外**的两个回调：

| 回调 | 在锁外调 | 行 |
|---|---|---|
| `tx_done` | ✅ | `:110-111` |
| `rx_callback` | ✅ | `:161` |

⇒ 这两个可以睡。**同一个子系统里，四个客户端/控制器回调分成两类，
判据就是"在不在 `chan->lock` 里"。**

### 这个类比什么时候会误导你？

1. **收银台类比会让你以为"排队的人在等"，但 mailbox 的调用者可能在等，也可能不在。**
   `mbox_send_message` 之后是不是阻塞，由 `cl->tx_block` 决定（`:271`）——
   而 `chan->lock` 在 `:83` 就放掉了，**阻塞发生在锁外**（`:280`
   `wait_for_completion_timeout`）。
   ⇒ **"不能睡"约束的范围只有 `:60-83` 这 24 行**，不是整个发送路径。

2. **`mbox_send_message` 的 kernel-doc 声称可以在原子上下文调用**
   （`mailbox.c:249-250`），**但 `tx_block` 分支明确会睡**（`:280`）。
   ⇒ 文档和代码矛盾，正确的说法是：**只有 `tx_block = false` 时才能在原子上下文调**。
   这条记在 `99-evidence.md` §6。

3. **收银台只有一个，但 mailbox 有两把锁。**
   `chan->lock`（per-channel spinlock）之外还有 `con_mutex`（全局 mutex，`:24`）。
   两者**只有一处嵌套**：`msg_submit` 在 `chan->lock` 内再取
   `poll_hrt_lock`（`:87-89`），反向不存在 ⇒ 不会 AB-BA
   （`02-datastructures.md` §8.2）。
   而 `con_mutex` 覆盖的是"申请/注册/注销"这类慢路径（`:351-404` 等三个区间），
   **`mbox_free_channel` 反而不取 `con_mutex`**（只取 `chan->lock`，`:456-462`）——
   申请全局串行、释放不串行，这个不对称是 `03-internals.md` §B2 的话题。

---
## §5 `mbox_send_message` 返回 5，是什么意思？

### 先举个例子

想想**排队取号机**。

你走进银行，按一下取号机，出来一张小票：**"A005"**。

这张小票是什么意思？

- ❌ 它**不是**"你的业务办好了"。
- ❌ 它**也不是**"你排在第 5 位"（前面的人可能已经走了）。
- ✅ 它是**"你的请求已经被受理，编号是 5"**。

关键在于：**"受理"和"办完"是两件事**。
小票只证明第一件。你还得坐着等叫号。

而且注意小票的另一个性质：**它一定是个非负数**。
如果取号机坏了，它不会给你一张"负数小票" —— 它什么都不给。
所以判断"取号成功了吗"，看的不是号码本身，而是**有没有拿到票**。

### 回到代码

`mbox_send_message` 返回的就是**队列下标**：

```c
	/* drivers/mailbox/mailbox.c:256-288 */
	int t;

	if (!chan || !chan->cl)
		return -EINVAL;              /* :261 */

	t = add_to_rbuf(chan, mssg);     /* :263  t 是环形队列下标 */
	if (t < 0) {
		dev_err(chan->mbox->dev, "Try increasing MBOX_TX_QUEUE_LEN\n");
		return t;                    /* :266 */
	}

	msg_submit(chan);                /* :269 */
	…
	return t;                        /* :287  原样返回那个下标 */
```

而 `add_to_rbuf`（`mailbox.c:26-51`）的最后一行就是：

```c
	return idx;                      /* :50 */
```

kernel-doc 说得很明确（`mailbox.c:242-254`）：

> `In non-blocking mode, the requests are buffered by the API and a`
> `non-negative token is returned for each queued request.`（`:242-243`）
> `Return: Non-negative integer for successful submission (non-blocking mode)`
> `or transmission over chan (blocking mode).`（`:252-253`）

所以正确的成功判据是 **`ret < 0` 才是失败**，
不是 `ret != 0`。真实世界里大家怎么写：

```c
	/* drivers/firmware/arm_scmi/mailbox.c:187-191 */
	ret = mbox_send_message(smbox->chan, xfer);

	/* mbox_send_message returns non-negative value on success, so reset */
	if (ret > 0)
		ret = 0;
```

SCMI 专门写了一行注释来解释为什么要把正数抹掉 —— 因为它自己的调用者
（`scmi_do_xfer` 那一层）用的是"0 = 成功"的约定。

### 这个类比什么时候会误导你？

1. **取号机的号码有用（你能对着叫号屏比对），mailbox 的这个下标对你完全没用。**
   核心没有任何 API 接受这个下标 —— 你不能用它查询状态、不能用它取消、
   不能用它跟 `tx_done` 回调里的消息对上号
   （`tx_done` 的参数是 `mssg` 指针和 `r`，`mailbox.c:110-111`）。
   ⇒ **它是一个纯粹的实现细节泄漏**。唯一正确的用法就是判断符号位。

2. **"排队"这个词会让你以为队列很深。** 队列长度是编译期硬编码的
   **20**：`include/linux/mailbox_controller.h:103` `#define MBOX_TX_QUEUE_LEN 20`
   （数组在 `:125` `void *msg_data[MBOX_TX_QUEUE_LEN];`）。
   而且它**不可配** —— 原作者在 `:100-101` 留了个 `REVISIT`：
   > `REVISIT: If too many platforms see the "Try increasing MBOX_TX_QUEUE_LEN"`
   > `print, it needs to be taken from config option or somesuch.`

   ⇒ 那条 `dev_err`（`:265`）**给的建议是做不到的** ——
   你改不了这个值，除非改内核源码。
   而且队列满的错误码是 **`-ENOBUFS`（-105）**，
   不是直觉上的 `-EAGAIN` 或 `-EBUSY`（`07-debug.md` §6.5）。

3. **阻塞模式下这个号码的含义悄悄变了。** `tx_block = true` 时，
   函数在 `:280` 等完 `tx_complete` 才返回，此时返回的 `t` 仍然是
   *当初入队的下标*（`:287`），但语义已经从"受理了"变成"发完了"。
   唯一的例外是超时：`:282` 把 `t` 覆盖成 `-ETIME`。
   ⇒ **同一个返回值，两种模式下语义不同** —— 这是 kernel-doc `:252-253`
   那两行里"(non-blocking mode)"和"(blocking mode)"括号的全部含义。

---

## §6 为什么 DTS 写错了，mailbox 一声不吭？

### 先举个例子

想想**填快递单**。

单子上有三栏：**"哪个快递柜"**、**"第几号格口"**、**"格口密码"**。

- 第一栏写错了（写了个不存在的柜子）—— 快递员会告诉你"没这地方"。
- 第二栏写错了（写了 99 号，柜子只有 12 个格口）—— 也会被拦下来。
- **第三栏写错了呢？** 密码是 4 位数字，你写了 4 位数字，
  格式完全合法 —— 快递员**没有任何理由拦你**。
  只是收件人打不开格口而已，而且这件事要几天后才会暴露。

mailbox 的 DTS 就是这个形状：**"哪个控制器"和"几个 cell"有人查，
"cell 里填什么值"没人查**。

### 回到代码

**有人查的部分** —— dtc 内建检查（`06-devicetree.md` §8.1）：

```c
	/* scripts/dtc/checks.c:1486 */
	WARNING_PROPERTY_PHANDLE_CELLS(mboxes, "mboxes", "#mbox-cells");
```

这个检查**默认是开的** —— 判据是
`scripts/Makefile.lib:339-347` 那份 "Disable noisy checks by default" 名单里
**没有** `mboxes_property`。它能抓到四类错误，最常见的一条是：

```
	Missing property '#mbox-cells' in node /soc/mailbox@... or bad phandle
	(referred from mboxes[0])          /* scripts/dtc/checks.c:1446-1450 */
```

**没人查的部分** —— 一张表：

| DTS 里写错什么 | 谁会发现 | 症状 |
|---|---|---|
| `mboxes` 指向不存在的 phandle | dtc 编译期 | 编译告警 |
| 控制器忘了写 `#mbox-cells` | dtc 编译期 | 编译告警（上面那条） |
| cell 个数不对 | dtc 编译期 | 编译告警 |
| **cell 的值不对**（写了 5，硬件只有 4 条通道） | **谁都不会** | 见下 |
| **TX/RX 通道写反了** | **谁都不会** | 上层框架超时 |
| `mbox-names` 拼错 | **谁都不会** | `_byname` 取不到，但**有** `dev_err`（`mailbox.c:418`） |

cell 值错了会走到哪一行？`mbox_request_channel` 里的这一段：

```c
	/* drivers/mailbox/mailbox.c:371-374 */
	if (!IS_ERR(chan))
		break;
	}
	…
```

这个出口**一条日志都没有**。`of_xlate` 返回的 `ERR_PTR` 被原样传出去，
调用者只看到一个错误码，dmesg 里**干干净净**
（`07-debug.md` §2.4 有完整的 verbatim 代码块）。

而且 `#mbox-cells` 的**含义**每个控制器自己定：
Tegra HSP 的 `#mbox-cells = <2>` 是 `<类型 索引>`，
其他控制器的 `<1>` 通常就是通道号。
⇒ **dtc 只数个数，不看意思**。

### 这个类比什么时候会误导你？

1. **快递单类比暗示"格式对了就一定能投递"，但 mailbox 还有个更早的失败点：
   控制器还没 probe。** 那时返回的是 `-EPROBE_DEFER`
   （`mailbox.c:361` 是遍历前的初值），这**不是错误**，是"待会儿再试"。
   区分方法：`cat /sys/kernel/debug/devices_deferred`
   （`06-devicetree.md` §8.2）。
   而且 `:363` 比较的是 **of_node 指针**，不是 compatible ——
   所以"驱动没编进内核"和"驱动还没 probe"这两种情况给出**同一个错误码**。

2. **"收件人打不开格口"这个比喻还是太温和了。**
   快递单的第三栏写错，最坏情况是东西取不出来。
   但 mailbox 的 cell 值写错，可能**踢到了另一条真实存在的通道** ——
   于是你的消息被送到了错的远端处理器，或者两个消费者
   在毫不知情的情况下共用了一条硬件通道（`06-devicetree.md` §8.4 的 C 组）。
   ⇒ 症状可以是"另一个毫不相关的子系统坏了"。

3. **`dtbs_check` 帮不上忙。** 你可能以为 YAML schema 能救 ——
   救不了：mailbox 的通用绑定是 `Documentation/devicetree/bindings/mailbox/mailbox.txt`，
   **不是 YAML**，所以 `mboxes` / `mbox-names` / `shmem` 三个通用属性
   **完全没有 schema**（`06-devicetree.md` §8.1）。
   只有各控制器自己的 YAML（比如 `nvidia,tegra186-hsp.yaml` 里
   `"#mbox-cells": const: 2`）能约束一点点。

---

## §7 为什么这个框架几乎什么都看不见？

### 先举个例子

想想**老式的自动门**。

自动门只做一件事：**有人来了就开**。它没有屏幕、没有计数器、
没有日志、没有"今天开了 137 次"的统计。为什么？

因为**设计它的时候假定它不会出问题，而且它上面还有别的系统在管事** ——
物业有监控摄像头，保安有值班记录。
自动门自己做得越简单，坏的可能性越小。

mailbox 核心就是按这个假设写的：**控制器很简单（就是几个寄存器），
上面的框架很聪明（SCMI / rpmsg / BPMP 自己有完整的超时和重传）**。
所以核心自己**什么观测手段都不提供**。

### 回到代码

零。全是零（`07-debug.md` §3.1、§5.1）：

| 观测手段 | mailbox 核心 | 对比 |
|---|---|---|
| tracepoint | **0** | clk 24 · regulator 11 · i2c 4 |
| sysfs 属性 | **0**（无 `DEVICE_ATTR` / 无 `class_create`） | — |
| debugfs 目录 | **0** | — |
| 全部日志 | **11 处**，其中 3 处是 debug 级 | — |
| 调试 Kconfig | **0** —— 全 297 行 `drivers/mailbox/Kconfig` 里没有 `*_DEBUG` | — |

更彻底的一个空缺：**`mbox_cons` 这个全局链表（`mailbox.c:23`）
从来没有被任何接口 dump 出来**。
⇒ 「系统里现在注册了哪几个 mailbox 控制器」这个最基本的问题，
**在标准内核上没有任何办法回答**。

`07-debug.md` §5.3 列了完整的六行观测缺口：
控制器清单 / 通道归属 / 队列深度 / 通道的 txdone 模式 / 任何计数器 / 延迟。
**六项全部没有接口。**

那能看什么？两条路（`07-debug.md` §2.3、§4）：

```bash
# 1) 把三条 debug 级日志打开（只能用这个形式，见下面第 3 点）
echo 'file drivers/mailbox/mailbox.c +p' > /sys/kernel/debug/dynamic_debug/control

# 2) 用 mailbox-test 做二分：先证明"控制器本身能收发"
ls -d /sys/kernel/debug/*/message      # 目录名是 dev_name()，不是 mailbox-test
```

### 这个类比什么时候会误导你？

1. **"自动门不会坏"这个假设是错的 —— mailbox 恰恰是最容易配错的一类子系统。**
   它的正确性依赖三份彼此独立的东西必须一致：DTS 的 cell 值、
   控制器驱动的 `of_xlate`、远端固件的通道约定。
   ⇒ **观测手段最少的地方，恰好是配置错误最多的地方**。这不是好设计，是历史债。

2. **不是所有平台都一样黑。** 有三个驱动自己建了 debugfs：
   `drivers/mailbox/mailbox-test.c:269`、
   `drivers/mailbox/bcm-flexrm-mailbox.c:1608`、
   `drivers/mailbox/bcm-pdc-mailbox.c:499`。
   ⇒ **在 Broadcom iProc / Stingray 上你有统计可看；
   在其他所有 arm64 平台上你只有 dmesg。**

3. **`mailbox.dyndbg=+p` 这个内核参数不存在。**
   `drivers/mailbox/Kconfig:2-7` 的 `menuconfig MAILBOX` 是 **`bool`** 而不是
   `tristate` ⇒ 核心永远编进 vmlinux ⇒ 没有 `mailbox` 这个模块名可以挂
   `dyndbg=` 参数。想在启动早期就打开，只能用
   `dyndbg="file drivers/mailbox/mailbox.c +p"`（`07-debug.md` §2.3）。

4. **"上层框架很聪明"确实成立，而且这是你最好的抓手。**
   核心自己不报，但 SCMI / rpmsg / BPMP 会因为收不到应答而超时并打日志。
   ⇒ 排查顺序应该是**先看上层框架的日志，再回头怀疑 mailbox**
   （`07-debug.md` §7.2 给了排序，其中第一件该做的事是
   `cat /proc/interrupts` 看控制器的中断有没有在涨）。

---

## §8 它叫"邮箱"，但它其实不是邮箱

### 先举个例子

再想一次快递柜 —— 但这次换个角度问：**快递柜到底是什么？**

如果你只看名字，会以为它是"存快递的地方"。
但如果你看它的**行为**，它其实是：

> **一个把"我要交付一件东西"这个动作，
> 从"立刻完成"变成"排队 + 稍后通知"的装置。**

装的是不是快递，其实不重要。你可以用它交接钥匙、交接文件、
交接一张写着"去看抽屉"的纸条 —— 甚至可以什么都不放，只按一下让对方来看。

这才是 mailbox 核心的真实职责。"邮箱"是它的**典型用法**，不是它的**定义**。
它的定义是：**带 20 格队列的异步提交器 + 一个"提交完成"的通知机制**。

### 回到代码

三条证据说明核心确实不关心"邮件"这件事：

1. **载荷是不透明的 `void *`，核心不解引用**（§2 已证：`mailbox.c:40`/`:72`/`:77`）。
   ⇒ 核心**不定义任何载荷格式**（`04-extensions.md` §2）。

2. **有人拿它当"命令队列"用。** MediaTek GCE / CMDQ：
   ```c
   /* drivers/mailbox/mtk-cmdq-mailbox.c:336-355 */
   static int cmdq_mbox_send_data(struct mbox_chan *chan, void *data)
   {
   	struct cmdq_pkt *pkt = (struct cmdq_pkt *)data;    /* :338 */
   	…
   	task = kzalloc(sizeof(*task), GFP_ATOMIC);        /* :347  注意 GFP_ATOMIC */
   	…
   	task->pa_base = pkt->pa_base;                     /* :353 */
   ```
   这里的"消息"是 `struct cmdq_pkt`（`include/linux/mailbox/mtk-cmdq-mailbox.h:73-79`），
   里面装的是 **一段 DMA 内存里的 GPU/显示命令缓冲**（`va_base`/`pa_base`/`cmd_buf_size`）。
   ⇒ **这已经完全不是"邮件"了，是"命令队列的提交入口"**
   （`04-extensions.md` §5.2）。
   而 `:347` 的 `GFP_ATOMIC` 正好是 §4 那条"`send_data` 在 spinlock 内"约束的实证。

3. **有人拿它当"纯门铃"用**（§2 已证：`drivers/rpmsg/qcom_smd.c:380-381`
   和 `drivers/firmware/tegra/bpmp-tegra186.c:86` 都传 `NULL`）。

三种用法（邮件 / 门铃 / 命令队列）共享同一套核心代码，
一行都不用改。这正说明核心抽象的是**提交+通知**，而不是**消息内容**。

### 这个类比什么时候会误导你？

1. **"通用异步提交器"这个理解会让你高估它的能力。**
   它的队列是**硬编码 20 格**（`include/linux/mailbox_controller.h:103`），
   而且**队列里存的是裸指针**（`:125` `void *msg_data[...]`）——
   核心不拷贝载荷。
   ⇒ kernel-doc 明确要求：`The pointer to message should be preserved`
   `until it is sent over the chan, i.e, tx_done() is made.`
   （`drivers/mailbox/mailbox.c:247-248`）
   **在栈上构造消息然后立刻返回 = 释放后使用。**
   这是"提交器"这个类比最危险的失效点。

2. **它也不是"工作队列"。** 没有 worker 线程、没有并发、
   同一通道**严格串行**（`msg_submit` 在 `mailbox.c:62-63` 直接 `goto exit`，只有 `active_req == NULL` 时才发下一条）。
   ⇒ 一条消息卡住，后面 19 条全部卡住。

3. **`knows_txdone` 揭示了它连"完成通知"都不总是自己给。**
   MediaTek CMDQ 就是这样：控制器 `txdone_irq` 和 `txdone_poll`
   **两个都是 false**（`drivers/mailbox/mtk-cmdq-mailbox.c:593-594`）⇒ ACK 模式；
   于是客户端自己设 `knows_txdone = true`
   （`drivers/soc/mediatek/mtk-cmdq-helper.c:84`、
   `drivers/gpu/drm/mediatek/mtk_drm_crtc.c:1006`）
   并在拿到硬件中断后自己调
   `mbox_client_txdone`（`drivers/soc/mediatek/mtk-cmdq-helper.c:438`、
   `drivers/gpu/drm/mediatek/mtk_drm_crtc.c:624`）。
   ⇒ 在这种配置下，**核心退化成一个纯粹的"20 格指针队列 + 串行化器"**，
   连"什么时候算发完"都是别人告诉它的。
   这也是 §3 里 `TXDONE_BY_ACK` 那一栏在真实世界的样子。

---

## §9 读完本篇之后

| 你现在想干什么 | 去哪一篇 |
|---|---|
| 找某个 API 的完整签名和错误码 | `01-api.md` |
| 把 §1 的"不是 device/driver"看完整版对比表 | `02-datastructures.md` §1.1 |
| 把 §3 的三种 txdone 模式看代码逐段剖析 | `03-internals.md` §A5、§A6 |
| 把 §4 的锁约束看全（两把锁 + 嵌套关系） | `02-datastructures.md` §8.1、§8.2 |
| 把 §8 的 CMDQ 当命令队列用看展开 | `04-extensions.md` §5.2 |
| 抄一份能编的客户端 / 控制器骨架 | `05-usage.md` |
| 把 §6 的 DTS 陷阱看全（含决策树） | `06-devicetree.md` §8 |
| 把 §7 的观测缺口和错误码分诊看全 | `07-debug.md` §5.3、§6 |
| 想知道本篇每条断言是怎么取证的 | `99-evidence.md` §3 |
| 隔几个月回来复习整个子系统 | `00-overview.md` |
