---
title: 使用指南
---

# 05 使用举例：四种角色的代码骨架

> **本篇所有代码都是骨架，未编译、未运行。**
> 它们是从 in-tree 驱动**逐行摘出来再简化**的（每段都注明原型出处），
> 用途是"照着填空"，不是"复制即用"。凡是可以直接抄的地方我会点明抄哪个文件。
> 骨架里出现的每个符号、每个内核打印字符串都在 6.1.158 树上 grep 过。
>
> **本篇的组织轴：一个角色一节，按"从零写一个 mailbox 系统"的顺序排。**
>
> 格式约定同前：`mailbox.c` 指 `drivers/mailbox/mailbox.c`。arm64-only，ACPI PCC 已裁掉。

---

## §0 mailbox 系统里有几个角色

一条 mailbox 通路能跑起来，**必须有四个角色同时到位**，缺一个都是静默失败：

| § | 角色 | 谁写 | 落在哪 | 不到位的症状 |
|---|---|---|---|---|
| §2 | **控制器驱动** | SoC 厂商 / 你 | `drivers/mailbox/<soc>-mailbox.c` | 消费者拿不到通道，`mbox_request_channel` 返回 `-EPROBE_DEFER` 后一直 defer |
| §3 | **消费者驱动** | 用这条通路的人 | 任意子系统目录 | 通道注册了但没人用，dmesg 里什么都没有 |
| §5 | **消息载荷约定** | 两边协商 | 头文件 / 注释 / 上层协议 | **核心一个字都不报错**，症状是远端不响应或超时 |
| §6 | **DTS 节点 + 验证手段** | 板级维护者 | `arch/arm64/boot/dts/…` + `mailbox-test` | `of_parse_phandle_with_args` 失败 ⇒ `-ENODEV`/`-EINVAL` |

和 i2c/spi 不同：mailbox **没有"设备驱动自动匹配"这一步**。
消费者是主动 `mbox_request_channel` 去拿通道的（`03-internals.md` §A2），
所以"控制器注册了但没人用"是完全正常且无声的状态。

### §0.1 按任务找章节

| 你要做的事 | 去哪节 |
|---|---|
| 给一块新 SoC 写 mailbox 控制器驱动 | §2（先看 §2.1 的三个决定） |
| 让自己的驱动用上已有的 mailbox 通道 | §3 |
| `remove` / 出错回滚该怎么写 | §4 |
| 决定 `void *mssg` 到底传什么 | §5 |
| 手上没有远端固件，先验证控制器驱动 | §6 |
| 写完了发现不工作 | §7 的症状对照表 → `07-debug.md` |

---

## §1 前置：三样东西的位置

| 你需要 | 写什么 |
|---|---|
| 控制器侧头文件 | `#include <linux/mailbox_controller.h>` |
| 消费者侧头文件 | `#include <linux/mailbox_client.h>` |
| Kconfig | 控制器驱动 `depends on MAILBOX`；消费者驱动同样要 `depends on MAILBOX`（或 `select MAILBOX`） |

两个头文件**互不包含**，各自只有 49 行和 140 行，
建议写代码前整读一遍（`01-api.md` §0 有清单）。

`MAILBOX` 本身是 bool（`drivers/mailbox/Kconfig:1-9`），
关掉之后 `mailbox_client.h` 里的函数**没有空桩（stub）** ——
所以消费者驱动必须真的依赖它，不能靠 `#ifdef` 绕。

---

## §2 控制器驱动骨架

### §2.1 动手前必须做的三个决定

这三件事定错，后面全部要返工：

| 决定 | 选项 | 判据 | 影响 |
|---|---|---|---|
| **① TXDone 模式** | `txdone_irq` / `txdone_poll` / 都不填（=ACK） | 硬件**有没有**"对端已取走"的中断；没中断但有状态位 ⇒ poll；两者都没有 ⇒ ACK，让消费者自己报 | 决定你要不要实现 `last_tx_done`、要不要调 `mbox_chan_txdone`（`03-internals.md` §A6） |
| **② 通道私有数据怎么放** | `chan->con_priv` / 平行数组 | 见 §2.2 的两种惯例 | 决定回调里怎么从 `struct mbox_chan *` 找回自己的数据 |
| **③ 注册用 devm 还是裸调** | `devm_mbox_controller_register` / `mbox_controller_register` | **默认选 devm** | 决定要不要写 `.remove`（§4） |

关于决定 ③ 的现状普查：全树 **29 个控制器驱动**里，
**27 个用 `devm_` 变体**，裸调的只有 `sun6i-msgbox.c:276`
和已裁掉的 `pcc.c:708`。`drivers/mailbox/` 下写了 `.remove` 的只有 13 个文件。
⇒ **除非你有"必须在 unregister 之后再关时钟"这类顺序要求，就用 devm。**

关于决定 ①：不填任何 flag **不是"不支持 TXDone"**，
而是明确选了 ACK 模式（`mailbox.c:493-498` 的 `else /* It has to be ACK then */`），
消费者必须调 `mbox_client_txdone`，否则第二条消息永远发不出去
（`active_req` 不会被清，见 `03-internals.md` §A5.3）。

### §2.2 `con_priv` 的两种惯例

核心只保证 `chan->con_priv` 这个 `void *` 存在且不被它碰
（`02-datastructures.md` §3.2）。全树两种用法：

**惯例 A：每通道一个私有结构体**（最常见，`arm_mhu.c:129` / `arm_mhu_db.c` / `apple-mailbox.c:362`）

```c
	for (i = 0; i < num; i++)
		mbox->chans[i].con_priv = &priv->link[i];      /* 原型 arm_mhu.c:129 */

	/* 回调里取回： */
	struct mhu_link *mlink = chan->con_priv;               /* 原型 arm_mhu.c:43 */
```

**惯例 B：`con_priv` 存下标**（门铃型，`qcom-apcs-ipc-mailbox.c:104-105`）

```c
	for (i = 0; i < ARRAY_SIZE(apcs->mbox_chans); i++)
		apcs->mbox_chans[i].con_priv = (void *)i;      /* :105  整数当指针存 */

	/* 回调里： */
	unsigned long idx = (unsigned long)chan->con_priv;     /* 原型 :64-70 */
```

还有一种**不要学的写法**：`rockchip-mailbox.c` 完全不用 `con_priv`，
`send_data:56` 写的是 `struct rockchip_mbox_chan *chans = mb->chans;`
然后用 `chans->idx`（`:68`/`:70`/`:72`）—— 这实际上**永远取第 0 个通道的下标**，
多通道时四个通道会互相踩。它靠 `dev_get_drvdata(chan->mbox->dev)`（`:54`）
拿控制器是对的，坏的是后半截。
⇒ **抄控制器骨架不要抄 rockchip 的 `send_data`**；`con_priv` 是唯一可靠的通道标识。
记进 `99-evidence.md` §6。

### §2.3 骨架：ops + probe（原型 `sun6i-msgbox.c`，POLL 模式最全）

```c
#include <linux/mailbox_controller.h>

struct foo_mbox {
	struct mbox_controller	controller;
	void __iomem		*regs;
	struct clk		*clk;
	spinlock_t		lock;		/* 保护寄存器读改写 */
};

/* ---- ① 发送：这里在 chan->lock 自旋锁内被调用，绝对不能睡 ---- */
static int foo_mbox_send_data(struct mbox_chan *chan, void *data)
{
	struct foo_mbox *mbox = chan->con_priv;
	int n = foo_chan_index(chan);
	u32 msg = *(u32 *)data;			/* 载荷格式见 §5 */

	if (!foo_last_tx_done(chan))
		return -EBUSY;			/* 上一条还没走，让核心排队 */

	writel(msg, mbox->regs + FOO_MSG_REG(n));
	return 0;
}

/* ---- ② POLL 模式必须实现：核心的 hrtimer 会周期性问它 ---- */
static bool foo_mbox_last_tx_done(struct mbox_chan *chan)
{
	struct foo_mbox *mbox = chan->con_priv;

	return !(readl(mbox->regs + FOO_STAT_REG) & FOO_TX_PENDING);
}

/* ---- ③ 通道被申请时开中断；在 con_mutex 里被调用 ---- */
static int foo_mbox_startup(struct mbox_chan *chan)
{
	struct foo_mbox *mbox = chan->con_priv;

	writel(FOO_RX_IRQ_EN, mbox->regs + FOO_IRQ_EN_REG);
	return 0;
}

/* ---- ④ 释放时关中断；startup 失败时它也会被调用（§2.5 第 3 条）---- */
static void foo_mbox_shutdown(struct mbox_chan *chan)
{
	struct foo_mbox *mbox = chan->con_priv;

	writel(0, mbox->regs + FOO_IRQ_EN_REG);
}

static const struct mbox_chan_ops foo_mbox_chan_ops = {
	.send_data	= foo_mbox_send_data,	/* 唯一必需的 */
	.startup	= foo_mbox_startup,
	.shutdown	= foo_mbox_shutdown,
	.last_tx_done	= foo_mbox_last_tx_done, /* 仅 POLL 模式需要 */
};

/* ---- ⑤ 收到消息：在硬中断或线程化中断里调，核心不加锁 ---- */
static irqreturn_t foo_mbox_irq(int irq, void *dev_id)
{
	struct foo_mbox *mbox = dev_id;
	u32 msg;
	int n;

	for (n = 0; n < FOO_NUM_CHANS; n++) {
		if (!(readl(mbox->regs + FOO_IRQ_STAT_REG) & BIT(n)))
			continue;
		msg = readl(mbox->regs + FOO_RX_REG(n));
		writel(BIT(n), mbox->regs + FOO_IRQ_CLR_REG);	/* 先清后报 */
		mbox_chan_received_data(&mbox->controller.chans[n], &msg);
	}
	return IRQ_HANDLED;
}

static int foo_mbox_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct mbox_chan *chans;
	struct foo_mbox *mbox;
	int i, ret;

	mbox = devm_kzalloc(dev, sizeof(*mbox), GFP_KERNEL);
	if (!mbox)
		return -ENOMEM;

	chans = devm_kcalloc(dev, FOO_NUM_CHANS, sizeof(*chans), GFP_KERNEL);
	if (!chans)
		return -ENOMEM;
	for (i = 0; i < FOO_NUM_CHANS; i++)
		chans[i].con_priv = mbox;		/* 原型 sun6i-msgbox.c:211-212 */

	mbox->regs = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(mbox->regs))
		return PTR_ERR(mbox->regs);

	/* 关中断要在注册 IRQ 之前，原型 sun6i-msgbox.c:255-256 */
	writel(0, mbox->regs + FOO_IRQ_EN_REG);

	ret = devm_request_irq(dev, platform_get_irq(pdev, 0),
			       foo_mbox_irq, 0, dev_name(dev), mbox);
	if (ret)
		return dev_err_probe(dev, ret, "Failed to register IRQ handler\n");

	mbox->controller.dev		= dev;		/* 必填，核心会解引用 */
	mbox->controller.ops		= &foo_mbox_chan_ops;
	mbox->controller.chans		= chans;
	mbox->controller.num_chans	= FOO_NUM_CHANS;
	mbox->controller.txdone_irq	= false;
	mbox->controller.txdone_poll	= true;
	mbox->controller.txpoll_period	= 5;		/* ms；不许填 0 */

	spin_lock_init(&mbox->lock);
	platform_set_drvdata(pdev, mbox);

	/* 这一行必须是 probe 的最后一步：返回后消费者立刻可能来申请 */
	return devm_mbox_controller_register(dev, &mbox->controller);
}
```

字段赋值那一段的顺序和取值直接抄 `sun6i-msgbox.c:265-271`；
`devm_` 注册的原型是 `rockchip-mailbox.c:242`。

### §2.4 `mbox_controller` 字段逐个交代

| 字段 | 必填？ | 说明 |
|---|---|---|
| `dev` | **必填** | 核心到处用它做日志和 `of_node` 匹配（`mailbox.c:365`）。不填直接崩 |
| `ops` | **必填** | 核心无条件解引用 `ops->send_data`（`:77`） |
| `chans` | **必填** | 核心的 `:489-491` 参数检查**不查它**，为 NULL 时会在 `:514` 崩 |
| `num_chans` | **必填** | `<= 0` 会被 `:489-491` 挡回 `-EINVAL`（无日志） |
| `txdone_irq` | 二选一 | 硬件有 TX ACK 中断时填 true，然后在中断里调 `mbox_chan_txdone` |
| `txdone_poll` | 二选一 | 只有 `txdone_irq == false` 时才生效（`:495`），且**必须**同时实现 `last_tx_done` |
| `txpoll_period` | POLL 时必填 | 单位 ms。核心不校验 0，填 0 会软死循环（`03-internals.md` §A6.3）。全树取值只有 1/5/10 |
| `of_xlate` | 可选 | 不填就用核心的 `of_mbox_index_xlate`（§2.6） |
| `poll_hrt` / `poll_hrt_lock` / `node` | **不要碰** | kernel-doc 明确标 `API private`（`include/linux/mailbox_controller.h:70-72`） |

### §2.5 `send_data` 里绝对不能做的四件事

根因只有一个：**`send_data` 和 `tx_prepare` 都在 `chan->lock` 自旋锁内被调用**
（`mailbox.c:74-77`，见 `03-internals.md` §A5.3）。于是：

1. **不能睡**：不能 `msleep` / `mutex_lock` / `wait_for_completion` /
   `GFP_KERNEL` 分配 / `regmap` 走 I2C-SPI 后端（那些会睡）。
   `regmap_write` 在 MMIO 后端上是安全的（`qcom-apcs-ipc-mailbox.c:70` 就这么用）。
2. **不能调任何 mailbox 核心 API**：`mbox_chan_txdone` 会取同一把 `chan->lock`（`:98`）⇒ 死锁。
   ACK 模式的驱动想"发完立刻算完成"，正确做法是让**消费者**调 `mbox_client_txdone`。
3. **失败要用返回值，不要自己回滚队列**：
   `send_data` 返回负值时，核心**既不出队也不告诉消费者**
   （`03-internals.md` §A5.3 ②）。返回 `-EBUSY` 是唯一有意义的失败
   —— 它的含义是"硬件忙，等下一轮 `tx_tick` 再试"。
4. **不要假设 `chan->cl` 非空**：`mbox_flush`（`:309`）不检查它，
   已释放通道上的 flush 会走到 `tx_tick`（`:313-314`）。

一条容易漏的：`shutdown` **会在 `startup` 失败之后被调用**
（`mailbox.c:399-401` 在 `startup` 失败时调 `mbox_free_channel`）。
所以 `shutdown` 必须能承受"从未成功 startup"的状态 —— 全树 26 个
`shutdown` 实现**没有一个在注释里提这件事**（`03-internals.md` §B5）。

### §2.6 什么时候必须自己写 `of_xlate`

不填 `of_xlate` 时核心用 `of_mbox_index_xlate`（`mailbox.c:467-477`），
它**只做一件事**：把 `mboxes` 里的第一个 cell 当通道下标。
必须自己写的三种情况：

| 情况 | 例子 |
|---|---|
| `#mbox-cells > 1`（要多个参数，如"通道 + 方向"） | `imx-mailbox.c:639-640` |
| 通道不是数组下标（如按硬件 ID 查表） | `qcom-ipcc.c:174` |
| 想校验 `args_count`（核心不校验） | 8 个驱动都自己校验，见 `03-internals.md` §A4.1 |

自己写时**一定要同时查上下界**：

```c
static struct mbox_chan *foo_mbox_of_xlate(struct mbox_controller *mbox,
					   const struct of_phandle_args *sp)
{
	int ind;

	if (sp->args_count != 1)		/* 核心不查这个 */
		return ERR_PTR(-EINVAL);

	ind = sp->args[0];
	if (ind < 0 || ind >= mbox->num_chans)	/* 核心只查上界（:473）*/
		return ERR_PTR(-EINVAL);

	return &mbox->chans[ind];
}
```

返回值必须是 `ERR_PTR()` 而不是 NULL —— `mailbox.c:371-373` 用
`IS_ERR(chan)` 判断（`03-internals.md` §A2）。

---

## §3 消费者驱动骨架

### §3.1 骨架（原型 `mailbox-test.c:330-355`，但修掉了它的两个反面写法）

```c
#include <linux/mailbox_client.h>

struct foo_client {
	struct device		*dev;
	struct mbox_client	cl;		/* 按值内嵌，不是指针 */
	struct mbox_chan	*chan;
	struct completion	rx_done;
	u32			rx_msg;
};

/* ---- ① 收到消息：原子上下文，通常在控制器的中断里 ---- */
static void foo_rx_callback(struct mbox_client *cl, void *mssg)
{
	struct foo_client *foo = container_of(cl, struct foo_client, cl);

	foo->rx_msg = *(u32 *)mssg;		/* 只搬走，不在这里做事 */
	complete(&foo->rx_done);
}

static int foo_client_probe(struct platform_device *pdev)
{
	struct foo_client *foo;
	int ret;

	foo = devm_kzalloc(&pdev->dev, sizeof(*foo), GFP_KERNEL);
	if (!foo)
		return -ENOMEM;

	foo->dev = &pdev->dev;
	init_completion(&foo->rx_done);

	foo->cl.dev		= &pdev->dev;	/* 必填，核心解引用它 */
	foo->cl.rx_callback	= foo_rx_callback;
	foo->cl.tx_block	= true;		/* 让 mbox_send_message 阻塞 */
	foo->cl.tx_tout	= 500;		/* ms；填 0 = 等一小时（§3.3）*/
	foo->cl.knows_txdone	= false;

	foo->chan = mbox_request_channel_byname(&foo->cl, "foo-tx");
	if (IS_ERR(foo->chan))
		return dev_err_probe(&pdev->dev, PTR_ERR(foo->chan),
				     "failed to request mailbox channel\n");
					/* 不要像 mailbox-test.c:351 那样
					 * 把 ERR_PTR 吞成 NULL —— 会丢掉
					 * -EPROBE_DEFER 这个语义 */

	platform_set_drvdata(pdev, foo);
	return 0;
}

/* ---- ② 发一条消息并等对端回话 ---- */
static int foo_do_xfer(struct foo_client *foo, u32 req)
{
	int ret;

	ret = mbox_send_message(foo->chan, &req);
	if (ret < 0)				/* 只判负，见 §3.4 */
		return ret;

	if (!wait_for_completion_timeout(&foo->rx_done,
					 msecs_to_jiffies(1000)))
		return -ETIMEDOUT;		/* 这是"没回来"，不是"没发出去" */

	return 0;
}
```

`container_of` 惯例的 in-tree 例子：`drivers/hwmon/xgene-hwmon.c:80`；
按值内嵌 `struct mbox_client` 的例子：`drivers/soc/qcom/qcom_aoss.c:73`、
`drivers/rpmsg/qcom_smd.c:131`（`02-datastructures.md` §3.1）。

### §3.2 `mbox_client` 五个字段的决定表

kernel-doc 在 `include/linux/mailbox_client.h:15-28`，结构体本体 `:29-38`。

| 字段 | 怎么定 | 陷阱 |
|---|---|---|
| `dev` | 必填 | `mbox_request_channel:346-349` 检查 `!cl \|\| !cl->dev` 后返回 `-ENODEV`（只有 `pr_debug`）；`_byname` 变体（`:412`）**不检查** `cl->dev`，靠后面解引用时崩 |
| `tx_block` | 能睡就填 true | 填 false 时**超时事件消费者收不到**，见 §3.3 |
| `tx_tout` | `tx_block == true` 时必填 | 填 0 不是"不超时"，是 **1 小时**（`mailbox.c:275-276` 的 `/* wait forever */` 实际是 `msecs_to_jiffies(3600000)`） |
| `knows_txdone` | 只在 ACK 模式下填 true | 它**不是** `mbox_client_txdone` 的开关（准入看 `chan->txdone_method` 的 ACK 位，`:199`）；核心只在申请时读它一次（`:389`） |
| `rx_callback` | 事实上必填 | `mbox_chan_received_data:158-163` **不检查它是否为 NULL**，控制器一收到消息就会崩 |
| `tx_prepare` | 共享内存型才要 | 全树只有 3 个实现者，都是共享内存型（`02-datastructures.md` §2.1） |
| `tx_done` | 想知道每条消息结果时填 | 全树只有 4 个实现者；它拿到的 `r` 是 `-ETIME` 或控制器给的码 |

**回调全部在原子上下文**：kernel-doc 三处都写了 "Atomic callback"
（`:24`/`:25`/`:27`）。`rx_callback` 和 `tx_done` 实际是在锁外调用的
（`mailbox.c:161`、`:110-111`），但**仍然可能在硬中断里** —— 所以
"不能睡"这条约束照样成立。完整的九行上下文矩阵在 `03-internals.md` §B1.2。

### §3.3 `tx_block == false` 时超时要自己管

这是消费者侧最容易踩的一个坑。`tx_tick`（`mailbox.c:113`）是这样写的：

```c
	if (r != -ETIME && chan->cl->tx_block)
		complete(&chan->tx_complete);
```

⇒ **`tx_block == false` 的客户端从头到尾不会被通知"这条消息超时了"。**
`tx_done` 回调会被调（参数 `r == -ETIME`），但如果你也没实现 `tx_done`，
那么这条消息就是**彻底静默地消失**了。

三种正确做法：

| 做法 | 适用 |
|---|---|
| ① `tx_block = true` + 合理的 `tx_tout` | 能睡的路径（probe、workqueue、ioctl），**首选** |
| ② `tx_block = false` + 实现 `tx_done` | 不能睡但要知道结果（中断路径） |
| ③ `tx_block = false` + 自己在上层做请求-响应超时 | arm64 上的 SCMI/SCPI/RTKit 全走这条（`04-extensions.md` §4.2） |

不要选"第四种"：`tx_block = false` 且不实现 `tx_done` 且上层也不管超时
—— 那就等于放弃了所有错误反馈。

### §3.4 `mbox_send_message` 的返回值必须只判负

成功时它返回的是**内部环形队列的下标**（0..19），不是 0：

```c
	ret = mbox_send_message(chan, &msg);
	if (ret < 0)
		return ret;		/* 对 */
	/* if (ret) return ret;  ← 错，正常发送也会返回 3、7、19… */
```

in-tree 的标准处理是显式归一化，`drivers/firmware/arm_scmi/mailbox.c:189`
的注释把这件事写明了：「mbox_send_message returns non-negative value on success, so reset」。

失败码只有两种可能（`03-internals.md` §A5）：

| 返回 | 含义 | 你该做什么 |
|---|---|---|
| `-EINVAL` | `chan` 或 `chan->cl` 为 NULL（`mailbox.c:260-261`） | 代码 bug，检查是不是用了已释放的通道 |
| `-ENOMEM` | 环形队列 20 个槽满了（`add_to_rbuf`），伴随 `dev_err` "Try increasing MBOX_TX_QUEUE_LEN"（`:265`） | 这条日志**不可操作**（那是编译期常量）；真实含义是"对端不消费或你发得太快" |
| `-ETIME` | 只在 `tx_block == true` 时出现（`:282`） | 对端没取走 |

注意 `send_data` 自己返回的失败**不会**变成 `mbox_send_message` 的返回值
（`03-internals.md` §A5.3 ②）—— 这是核心的一个已知缺口。

### §3.5 `rx_callback` 里能做什么、不能做什么

| 想做 | 行不行 |
|---|---|
| 拷走数据 + `complete()` / `wake_up()` | ✅ 推荐，唯一该做的事 |
| `schedule_work()` / `queue_work()` | ✅ 可以 |
| 再调 `mbox_send_message` 回一条 | ✅ **可以** —— `mbox_chan_received_data:161` 不持任何核心锁（`03-internals.md` §A7） |
| `mutex_lock` / `kmalloc(GFP_KERNEL)` / 拷贝到用户态 | ❌ 原子上下文 |
| 假设 `mssg` 在回调返回后还有效 | ❌ 控制器通常传的是栈上变量的地址（骨架 §2.3 ⑤ 就是 `&msg`），**必须当场拷走** |

另一件没有任何 API 能做的事：**RX 没有流控**。
`mailbox.c:156` 的注释是 `/* No buffering the received data */` ——
消费者来不及处理时，数据就是丢了，核心不会告诉你丢了几条。
需要不丢就得自己在 `rx_callback` 里做环形缓冲（`mailbox-test.c` 用的是
一个 128 字节的单缓冲 `rx_buffer`，后来的会覆盖前面的）。

### §3.6 什么时候需要 `mbox_flush`

`mbox_flush(chan, timeout)`（`mailbox.c:305-317`）是给
**"要阻塞但不能睡"** 的场合准备的：控制器在 `flush` 回调里做忙等
（kernel-doc `include/linux/mailbox_controller.h:23-25`）。

全树只有 3 个真实调用者：
`drivers/gpu/drm/mediatek/mtk_drm_crtc.c:604`、
`drivers/soc/apple/rtkit.c:653`、
`drivers/tty/serial/tegra-tcu.c:60`（后者是 console 路径，典型的"不能睡"场景）。

用之前必须确认两件事：

1. **你的控制器实现了 `flush` 吗** —— 全树只有 4 个实现者
   （`tegra-hsp.c:577` / `sprd-mailbox.c:289` / `apple-mailbox.c:308` /
   `mtk-cmdq-mailbox.c:494`）。没实现就返回 `-ENOTSUPP`（`:310`，
   注意是 `-ENOTSUPP` 不是 `-EOPNOTSUPP`）。
2. **通道还活着吗** —— `:309` 既不查 `chan` 也不查 `chan->cl`，
   在已 `mbox_free_channel` 的通道上调它会在 `tx_tick`（`:110`）里 NULL 解引用。

---

## §4 拆除骨架：`remove` 与出错回滚

### §4.1 消费者侧

```c
static int foo_client_remove(struct platform_device *pdev)
{
	struct foo_client *foo = platform_get_drvdata(pdev);

	/* ① 先把"还会再发消息"的入口关掉（debugfs / sysfs / 定时器 / workqueue）*/
	foo_stop_all_submitters(foo);

	/* ② 可选：等在途消息真的出去（仅当控制器实现了 flush，§3.6）*/
	if (foo->chan)
		mbox_flush(foo->chan, 100);	/* 返回 -ENOTSUPP 时忽略即可 */

	/* ③ 最后释放通道；它可能睡（mailbox_client.h:47 注释）*/
	if (foo->chan)
		mbox_free_channel(foo->chan);

	return 0;
}
```

原型 `mailbox-test.c:422-433`（先 `debugfs_remove_recursive` 再 free，顺序对）。

顺序不能反的原因：`mbox_free_channel:455` 的注释是
`/* The queued TX requests are simply aborted, no callbacks are made */`
—— **在途消息被静默丢弃**，而正在 `mbox_send_message` 里阻塞等待的线程
不会被唤醒，它会睡满整个 `tx_tout`（`tx_tout == 0` 时是一小时）。
所以 ① 必须真的挡住所有新提交。

两处不要学 `mailbox-test.c`：`:395-397` 让 `rx_channel = tx_channel`，
于是 `:428-431` 对同一条通道 free 了两次（靠核心 `:450` 的幂等保护才没崩，
`04-extensions.md` §1.4）。**每条通道只 free 一次。**

### §4.2 控制器侧

用了 `devm_mbox_controller_register` 就**什么都不用写** —— devres 会在
设备 `remove` 之后自动 unregister（`mailbox.c:594-606`）。

需要裸 `mbox_controller_register` 的唯一理由是**顺序**：
unregister 必须发生在关时钟/断电之前。原型 `sun6i-msgbox.c:290-299`：

```c
static int foo_mbox_remove(struct platform_device *pdev)
{
	struct foo_mbox *mbox = platform_get_drvdata(pdev);

	mbox_controller_unregister(&mbox->controller);	/* 先摘链、再 shutdown 每条通道 */
	clk_disable_unprepare(mbox->clk);		/* 必须在 unregister 之后 */

	return 0;
}
```

但要知道你在做什么：`mbox_controller_unregister:549` 会把
**还被消费者持有的通道**直接收回（对每条通道调 `shutdown` 并把 `cl` 置 NULL），
**一条日志都不打**，消费者也没有任何 `remove`/`detach` 回调可以知道这件事
（`03-internals.md` §B2.2）。⇒ **控制器模块的 unbind/rmmod 在 mailbox 上不是安全操作。**

还有一个 `03-internals.md` §A8 说过的副作用：`mbox_free_channel:459-460`
会无条件把 ACK 模式翻成 POLL 模式。ACK 模式的控制器要留意这一点。

---

## §5 消息载荷约定怎么定

核心对 `void *mssg` 一个字都不定义（`04-extensions.md` §2），所以这是你的活。
三种做法，按推荐度排：

| 做法 | 怎么写 | in-tree 例子 |
|---|---|---|
| **共享头文件定结构体** | 在 `include/linux/mailbox/<soc>-message.h` 里定义，控制器和消费者都 include | `include/linux/mailbox/zynqmp-ipi-message.h`（它的 kernel-doc `:7-13` 是全树唯一把这件事写进文档的） |
| **载荷就是一个整数** | `u32 *` 或直接把整数塞进指针 | 门铃型控制器普遍如此 |
| **载荷是 NULL** | 纯门铃，真实数据走共享内存 | `drivers/firmware/tegra/bpmp-tegra186.c:86` 就是 `mbox_send_message(chan, NULL)` |

三条硬规则：

1. **格式写错不会有任何错误码** —— 症状是远端不响应或上层超时。
   定位办法见 `07-debug.md` §5.2。
2. **载荷的生命周期**：`send_data` 返回之后核心就不再碰它，但**控制器**可能
   还在用（DMA 型的如 CMDQ 会一直用到硬件执行完，`04-extensions.md` §5.2）。
   ⇒ 生命周期是**控制器与消费者之间的约定**，必须写进头文件注释里。
3. **`rx_callback` 收到的指针出了回调就无效**（§3.5），要拷走。

---

## §6 用 `mailbox-test` 验证新控制器（远端固件还没好的时候）

### §6.1 三步

```bash
# 1. 打开测试客户端
CONFIG_MAILBOX_TEST=m            # drivers/mailbox/Kconfig:167-173

# 2. DTS 里加一个节点（完整属性说明见 06-devicetree.md §6）
#    唯一的 in-tree 示例在 Documentation/devicetree/bindings/mailbox/sti-mailbox.txt:46-50

# 3. 收发
ls /sys/kernel/debug/                        # 目录名是 dev_name()，形如 30a0.mailbox-test
echo -n "ping" > /sys/kernel/debug/<dev>/message
cat /sys/kernel/debug/<dev>/message          # 阻塞直到收到；输出是 hexdump
```

通道名是**写死的 `"tx"` 和 `"rx"`**（`mailbox-test.c:389-390`），
DTS 的 `mbox-names` 必须正好用这两个名字。
而且 `mbox_request_channel_byname` 用的是**前缀匹配**
（`mailbox.c:429` 的 `strncmp(name, mbox_name, strlen(name))`）——
所以 `mbox-names = "tx-hi", "rx-hi"` 也会被匹配上，且不报错
（`03-internals.md` §A3）。

### §6.2 会看到什么

| 现象 | 含义 |
|---|---|
| probe 打 `"Successfully registered"`（`mailbox-test.c:417`，`dev_info`） | 两条通道**至少一条**拿到了 |
| probe 返回 `-EPROBE_DEFER`（`:392-393`） | 两条都没拿到 —— 控制器还没注册，或 `mbox-names` 写错 |
| `dev_warn` `"Failed to request %s channel"`（`:350`） | 单条失败；它只是 warn，probe 可能仍然成功 |
| `cat` 输出 `<NO RX CAPABILITY>`（`:186`） | 只申请到了 tx 通道 |
| `echo` 报 `"Channel cannot do Tx"`（`:105`，`dev_err`） | 只申请到了 rx 通道 |
| `echo` 报 `"Failed to send message via mailbox"`（`:146`） | `mbox_send_message` 返回了负值 —— 到这里说明核心和控制器已经连通，看 §3.4 的三个错误码 |

### §6.3 它能验证到哪一层

| 能验证 | 不能验证 |
|---|---|
| DTS 的 `mboxes` / `#mbox-cells` / `of_xlate` 通不通 | 载荷格式对不对（§5） |
| `startup`/`shutdown`/`send_data` 会不会崩 | 远端固件的协议 |
| TXDone 模式对不对（POLL 填错周期会表现为发送慢或超时） | 并发多通道的相互干扰（它只用两条） |
| `rx_callback` 路径通不通（需要远端或环回硬件真的回一条） | 上层协议框架的行为（`04-extensions.md` §4） |

更多用法（`poll`/`fasync` 写用户态程序等 RX、`signal` 文件配合共享内存）
见 `04-extensions.md` §1 和 `07-debug.md` §4。

---

## §7 症状 → 原因对照表

| 症状 | 最可能的原因 | 去哪查 |
|---|---|---|
| 消费者 probe 一直 `-EPROBE_DEFER` | 控制器没注册 / `of_node` 对不上 / `mboxes` 的 phandle 指错 | `03-internals.md` §A2 第 3 步；`06-devicetree.md` §5 |
| 拿通道时 `-EBUSY` | 通道已被别人占（`cl != NULL`）**或**控制器模块正在卸载 —— 两种情况同一个码 | `03-internals.md` §A2.2 |
| `_byname` 静默失败但 dmesg 没有 `dev_err` | 你用的是**非 byname** 变体（它四个失败出口全是 debug 级） | `03-internals.md` §A2.2、§B3.1 |
| 第一条消息成功，之后全部超时 | ACK 模式但消费者没调 `mbox_client_txdone` ⇒ `active_req` 一直不清 | §2.1 决定 ①；`03-internals.md` §A6 |
| 发送很慢 / 偶发超时 | POLL 模式的 `txpoll_period` 太大，或 `last_tx_done` 实现有误 | §2.4；`03-internals.md` §A6.3 |
| 整机软死锁在 mailbox 里 | `txpoll_period` 填了 0 | §2.4 |
| `send_data` 里偶发 "scheduling while atomic" | 在 `chan->lock` 里睡了（§2.5 第 1 条） | `03-internals.md` §B1 |
| 消息发出去了但远端毫无反应 | 载荷格式不对（核心不报错） | §5；`07-debug.md` §5.2 |
| 收到的数据是乱的 | `rx_callback` 没当场拷走，指针已失效 | §3.5 |
| 高负载下丢消息 | RX 无流控（核心不缓冲） | §3.5 |
| rmmod 控制器后消费者行为异常 | 通道被静默收回，无任何通知 | §4.2；`03-internals.md` §B2.2 |

---

## §8 读完本篇之后

| 想知道 | 去哪篇 |
|---|---|
| DTS 每条属性由谁消费、配错怎么定位 | `06-devicetree.md` |
| 上面每个错误码在核心里的确切出处 | `03-internals.md` Part A + `01-api.md` §4 |
| debugfs / 动态 debug / tracepoint 怎么用 | `07-debug.md` |
| 为什么 `send_data` 非得在自旋锁里 | `08-feynman.md` |
| 本篇每条普查数字（28/13/4/3 个…）的复现命令 | `99-evidence.md` §3 |
