---
title: 内部实现
---

# 03 实现剖析：626 行核心的每一段

> **本篇只承诺一条主轴：Part A 是时间线。**
> 从控制器注册开始，到通道被申请、消息被发出、TXDone 汇入、通道被释放、
> 控制器被注销 —— 按**运行时发生的先后**排，不按文件里的先后排。
>
> 横切性的内容（锁、生命周期不对称、三条路径的防护强度差异、边界检查）
> 全部收进 **Part B**；对设计的评价和源码里的设计债收进 **Part C**。
> 章节号自带 A/B/C 前缀，所以任何一处交叉引用都能一眼看出"在哪条轴上"。
>
> 格式约定与 `02-datastructures.md` 一致：`mailbox.c` 指
> `drivers/mailbox/mailbox.c`（626 行，**本篇逐行读过**）；
> `mailbox_client.h` / `mailbox_controller.h` / `mailbox.h` 分别指
> `include/linux/mailbox_client.h`、`include/linux/mailbox_controller.h`、
> `drivers/mailbox/mailbox.h`。arm64-only，ACPI PCC 已裁掉。

---

## §0 导航

### §0.1 分层图：626 行分成四段

```
┌────────────────────────────────────────────────────────────────────────┐
│  消费者侧 API（EXPORT_SYMBOL_GPL，头文件 mailbox_client.h）             │
│    mbox_request_channel :337   mbox_request_channel_byname :409         │
│    mbox_send_message    :256   mbox_flush                  :305         │
│    mbox_client_txdone   :197   mbox_client_peek_data       :223         │
│    mbox_free_channel    :445                                            │
├────────────────────────────────────────────────────────────────────────┤
│  控制器侧 API（EXPORT_SYMBOL_GPL，头文件 mailbox_controller.h）         │
│    mbox_controller_register   :485   ..._unregister        :537         │
│    mbox_chan_received_data    :158   mbox_chan_txdone      :176         │
│    devm_ 两个变体 + devres 胶水              :558-626                   │
├────────────────────────────────────────────────────────────────────────┤
│  核心状态机（全部 static，外部看不见）                                  │
│    add_to_rbuf :26    msg_submit :53    tx_tick :93                    │
│    txdone_hrtimer :117    of_mbox_index_xlate :467                     │
├────────────────────────────────────────────────────────────────────────┤
│  全局状态（就两个）                                                     │
│    mbox_cons :23（控制器链表）    con_mutex :24                         │
└────────────────────────────────────────────────────────────────────────┘
```

读这张图的关键：**中间那层 5 个 static 函数是整个子系统的全部逻辑**。
上面两层基本都是"参数校验 + 转调下面"。所以理解 mailbox
只需要真正读懂 `msg_submit` 和 `tx_tick` 这两个函数（合计 63 行）。

### §0.2 生命周期时间线

```
时刻    谁在动                        发生了什么                            §
─────────────────────────────────────────────────────────────────────────────
 T0    控制器驱动 probe         填 mbox_controller，调 register           §A1
        └─ 核心：选定 txdone 方法、初始化每条 chan、挂进 mbox_cons
 T1    消费者驱动 probe         mbox_request_channel(cl, index)           §A2
        └─ 核心：解析 DT → of_xlate 找通道 → 占用 → 调 startup           §A4
 T2    消费者随时              mbox_send_message(chan, mssg)              §A5
        └─ 核心：入队 → 可能立刻 send_data → 可能阻塞等
 T3    三种来源之一            TXDone 上报 → tx_tick → 推进队列           §A6
 T3'   控制器中断              mbox_chan_received_data → rx_callback      §A7
 T4    消费者 remove           mbox_free_channel(chan)                    §A8
        └─ 核心：调 shutdown、清 cl、ACK 翻成 POLL、module_put
 T5    控制器驱动 remove       mbox_controller_unregister(mbox)           §A9
        └─ 核心：摘链、逐通道 free、可能取消 hrtimer
─────────────────────────────────────────────────────────────────────────────
```

**T4 和 T5 的顺序在核心里没有任何保障。**
如果控制器先 remove，`mbox_controller_unregister` 会**替消费者**调
`mbox_free_channel`（`mailbox.c:549`），而消费者手里那个 `chan` 指针
**不会收到任何通知**。这是 §B2 的主题。

### §0.3 哪些内容不在本篇

| 你在找 | 去哪 |
|---|---|
| API 的签名、参数、返回值/错误码语义 | `01-api.md` |
| `mbox_chan_ops` 六个回调各自的契约与必需性普查 | `01-api.md` §3 |
| 结构体字段"谁填谁读"、环形队列的形状、锁清单 | `02-datastructures.md` §4 / §8 |
| ACK→POLL 隐患的完整链条 | `01-api.md` §5.3（本篇 §B3 只讲它在时间线上的位置） |
| `mailbox-test` 这个调试用客户端 | `04-extensions.md` |
| 怎么写一个控制器驱动 / 消费者 | `05-usage.md` |
| DT 属性怎么被解析成通道 | 本篇 §A4 只讲核心那一侧的解析；DTS 写法和属性消费者见 `06-devicetree.md` |
| 观测手段（core 零 tracepoint 零 debugfs 的绕法） | `07-debug.md` |

---

# Part A 时间线

## §A1 T0：控制器注册（`mbox_controller_register`，`mailbox.c:485-530`）

46 行，做五件事，**顺序有讲究**。

### §A1.1 逐段

**① 参数校验（`:489-491`）**

```c
/* Sanity check */
if (!mbox || !mbox->dev || !mbox->ops || !mbox->num_chans)
	return -EINVAL;
```

四个条件合成一条 `-EINVAL`，**不打任何日志**。
所以控制器驱动 probe 失败拿到 `-EINVAL` 时，
从 dmesg 里分不出是哪一项缺了 —— `01-api.md` §4.3 有完整的 `-EINVAL` 归因表。
注意 `chans` **不在**校验列表里：`mbox->chans` 为 NULL 也能过这一关，
到 `:514` 才崩。

**② 选定 TXDone 方法（`:493-498`）**

```c
if (mbox->txdone_irq)
	txdone = TXDONE_BY_IRQ;
else if (mbox->txdone_poll)
	txdone = TXDONE_BY_POLL;
else /* It has to be ACK then */
	txdone = TXDONE_BY_ACK;
```

三选一，IRQ 优先。**"两个都不设"落到 ACK**，不是"不支持"（`02-datastructures.md` §1.4）。
全树普查（`rg -n 'txdone_irq[[:space:]]*=|txdone_poll[[:space:]]*=' drivers/`，
排除 ACPI 的 `pcc.c`）：**没有任何驱动真的把两个都设成 true**。
三个看起来可疑的文件全是 if/else 两个分支：
`drivers/mailbox/arm_mhuv2.c:993-994` vs `:1007-1008`（中断申请失败就退回轮询）、
`drivers/mailbox/hi6220-mailbox.c:333-338`（看 DT 里有没有 `hi6220,mbox-tx-noirq`）、
`drivers/mailbox/mailbox-altera.c:321-328`。
⇒ `mailbox_controller.h:36-42` 那条「never set both」的约定，**本树是遵守的**。

**③ POLL 专属的初始化（`:500-511`）** —— 这一段是三个分支里**唯一**做事的：

```c
if (txdone == TXDONE_BY_POLL) {
	if (!mbox->ops->last_tx_done) {
		dev_err(mbox->dev, "last_tx_done method is absent\n");
		return -EINVAL;                       /* :504 */
	}
	hrtimer_init(&mbox->poll_hrt, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	mbox->poll_hrt.function = txdone_hrtimer;
	spin_lock_init(&mbox->poll_hrt_lock);     /* :510 */
}
```

三件事：校验回调、初始化 hrtimer、初始化 `poll_hrt_lock`。
**IRQ 和 ACK 分支这三件事一件都不做** ——
所以任何让通道在运行期变成 POLL 的路径都会踩到未初始化的 hrtimer。
链条见 §B3.2。

`:503` 是本函数**唯一**的日志。`last_tx_done` 缺失是唯一"能从 dmesg 看出来"的注册失败原因。

**④ 逐通道初始化（`:513-520`）**

```c
for (i = 0; i < mbox->num_chans; i++) {
	struct mbox_chan *chan = &mbox->chans[i];
	chan->cl = NULL;
	chan->mbox = mbox;
	chan->txdone_method = txdone;
	spin_lock_init(&chan->lock);
}
```

四个字段。注意**没有** `init_completion(&chan->tx_complete)` ——
那是申请通道时才做的（`:387`），因为 completion 是"这一次占用"的状态，
不是"这条通道"的状态。
也**没有**清 `msg_count` / `msg_free` / `active_req` / `msg_data[]` ——
同理，清零发生在 `:383-385`。这意味着**注册后立刻发消息是未定义行为**，
但由于必须先 `mbox_request_channel` 才拿得到 `chan`，实际不可达。

**⑤ 装默认 `of_xlate` 并挂链（`:522-527`）**

```c
if (!mbox->of_xlate)
	mbox->of_xlate = of_mbox_index_xlate;

mutex_lock(&con_mutex);
list_add_tail(&mbox->node, &mbox_cons);
mutex_unlock(&con_mutex);
```

注意**它改写了驱动的结构体字段**（`:523`）。后果：
控制器驱动如果 unregister 之后想再 register 同一个 `mbox_controller`，
`of_xlate` 已经被填过了 —— 这次是无害的（填的是同一个值），
但同类写法在别的地方就不一定（比如 `chan->txdone_method` 被驱动改写过之后，
再注册会被 `:518` 无条件覆盖回去）。

`con_mutex` 的持有区间**只有两行**（`:525-527`），
注册操作本身几乎不串行化 —— 真正串行的是**申请通道**（§A2）。

### §A1.2 注册失败之后需要驱动自己做什么

| 失败点 | 返回 | 核心已经做过的事 | 驱动需要撤销的 |
|---|---|---|---|
| `:491` | `-EINVAL` | 无 | 无 |
| `:504` | `-EINVAL` | 无（`hrtimer_init` 在校验之后） | 无 |
| —— | 0 | 全部 | 用 `mbox_controller_unregister` |

这张表是"空的"，这本身就是结论：**`mbox_controller_register`
是全有或全无的**，中途失败不留残留状态。用 `devm_` 变体（`mailbox.c:587-608`）
连 unregister 都不用写。

---

## §A2 T1：消费者申请通道（`mbox_request_channel`，`mailbox.c:337-406`）

70 行，是核心里**唯一持 `con_mutex` 做实质工作**的函数，
也是错误路径最多的函数（4 个 `return ERR_PTR`）。

### §A2.1 逐段

**① 前置校验（`:346-349`）**

```c
if (!dev || !dev->of_node) {
	pr_debug("%s: No owner device node\n", __func__);
	return ERR_PTR(-ENODEV);
}
```

用的是 `pr_debug` 而不是 `dev_dbg` —— 因为这时候还不确定 `dev` 有效。
这里同时校验了 `!dev` 和 `!dev->of_node`，
而 `mbox_request_channel_byname`（`:412`）**直接解引用 `cl->dev->of_node`
没有校验 `cl->dev`** —— 两个入口的防护强度不一致，见 §B5.2。

**② 取 `con_mutex`（`:351`）** —— 一直持到 `:404`，覆盖后面全部步骤。

**③ 解析 DT（`:353-359`）**

```c
ret = of_parse_phandle_with_args(dev->of_node, "mboxes", "#mbox-cells",
				 index, &spec);
```

**属性名 `mboxes` 和 cells 名 `#mbox-cells` 在核心里硬编码**，
这是 `06-devicetree.md` §2 的起点。
失败时**原样返回 `of_parse_phandle_with_args` 的错误码**（`:358`）——
所以消费者可能拿到 `-ENOENT`（没有 `mboxes` 属性）或
`-EINVAL`（index 越界），核心不做归一化。

**④ 遍历控制器链表 + `of_xlate`（`:361-367`）**

```c
chan = ERR_PTR(-EPROBE_DEFER);
list_for_each_entry(mbox, &mbox_cons, node)
	if (mbox->dev->of_node == spec.np) {
		chan = mbox->of_xlate(mbox, &spec);
		if (!IS_ERR(chan))
			break;
	}
```

**`-EPROBE_DEFER` 是"预设值"，不是某个判断的结果。**
只要循环没找到匹配的控制器（或者找到了但 `of_xlate` 全部失败），
`chan` 就保持 `-EPROBE_DEFER`。这解释了 mailbox 消费者
"probe 一直 defer" 这类现象的两种成因（DT 指向的控制器还没注册 / 已注册但通道号不对），
**而这两种在返回码上无法区分** —— `01-api.md` §4.2 有区分办法。

匹配判据是 `mbox->dev->of_node == spec.np`，**指针相等**，
不是 `of_node_cmp` 也不是 phandle 值比较。
循环里的 `break` 只在成功时执行，所以**同一个 `of_node` 挂了多个控制器时会依次尝试** ——
这正是 `drivers/mailbox/tegra-hsp.c` 那种"一个 platform 设备注册两个
`mbox_controller`"的写法能工作的原因（`02-datastructures.md` §3.2）。
但注意它们的 `dev` 是**同一个** `struct device`，
所以 `spec.np` 相等对两个控制器都成立，**顺序由 `list_add_tail` 的注册次序决定**。

**⑤ 占用检查 + 模块引用（`:376-380`）**

```c
if (chan->cl || !try_module_get(mbox->dev->driver->owner)) {
	dev_dbg(dev, "%s: mailbox not free\n", __func__);
	mutex_unlock(&con_mutex);
	return ERR_PTR(-EBUSY);
}
```

一行里塞了两件不同的事，短路求值：

| 条件 | 真实含义 | 返回 |
|---|---|---|
| `chan->cl` 非空 | 通道已被别的消费者占用 | `-EBUSY` |
| `try_module_get` 失败 | 控制器驱动模块正在卸载 | `-EBUSY`（**同一个码**） |

两种截然不同的原因共用一个返回码和一条 `dev_dbg`
（`mailbox not free` 这句话只对前一种成立）。
还有一处更硬的问题：`mbox->dev->driver->owner` **三级解引用，
中间那级没有校验**。控制器的 `dev` 如果没有绑定驱动（`dev->driver == NULL`），
这里就是 NULL 解引用。实际上不可达 —— 控制器是在自己的 probe 里注册的，
那时 `dev->driver` 一定已经被驱动核心填好了。**列为"防护缺失但当前不可达"**，
记在 `99-evidence.md` §6。

**⑥ 占用（`:382-392`，持 `chan->lock`）**

```c
spin_lock_irqsave(&chan->lock, flags);
chan->msg_free = 0;
chan->msg_count = 0;
chan->active_req = NULL;
chan->cl = cl;
init_completion(&chan->tx_complete);

if (chan->txdone_method == TXDONE_BY_POLL && cl->knows_txdone)
	chan->txdone_method = TXDONE_BY_ACK;

spin_unlock_irqrestore(&chan->lock, flags);
```

队列在这里被清空 —— 意味着**上一个消费者没发完的消息被静默丢弃**
（`mbox_free_channel:455` 的注释已经明说了这件事：
`The queued TX requests are simply aborted, no callbacks are made`）。

`:389` 是 `cl->knows_txdone` 在**整个核心里唯一被读的地方**。
注意它用的是 `==` 而不是位测试 —— 因为 `txdone_method` 在这一刻
必然只有一个位（`:518` 刚赋的值，或者驱动改写过的单一值）。
完整讨论见 `01-api.md` §5.4。

**⑦ 调 `startup`（`:394-402`）**

```c
if (chan->mbox->ops->startup) {
	ret = chan->mbox->ops->startup(chan);
	if (ret) {
		dev_err(dev, "Unable to startup the chan (%d)\n", ret);
		mbox_free_channel(chan);
		chan = ERR_PTR(ret);
	}
}
```

三条要点，都很重要：

1. **`startup` 是在 `con_mutex` 里面被调的。**
   它允许睡眠（`mailbox_controller.h:26-31`），但**绝对不能调任何 mailbox API** ——
   `mbox_request_channel` / `mbox_controller_register` / `mbox_controller_unregister`
   全都要这把锁，会立刻自死锁。
2. **失败时调了 `mbox_free_channel`，而 `mbox_free_channel` 会调 `shutdown`**
   （`mailbox.c:452-453`）—— 也就是说 **`startup` 失败之后 `shutdown` 仍然会被调一次**。
   控制器驱动的 `shutdown` 必须能承受"从未成功 startup"的状态。全树没有一个驱动
   在注释里提到这一点，属于**未文档化的隐式契约**（`99-evidence.md` §6）。
3. 这条路径同时是 ACK→POLL 隐患**最近的触发点**：
   `mbox_free_channel:459-460` 会把 ACK 翻成 POLL。
   所以一次 `startup` 失败就足以让这条通道进入坏状态，
   而 `startup` 失败最常见的原因恰恰是"资源还没就绪、稍后重试" ——
   下一次重试就落在坏状态上。完整链条见 §B3.2 与 `01-api.md` §5.3。

注意 `dev_err`（`:398`）是本函数唯一的非 debug 级日志。
前面四个失败点全是 `pr_debug` / `dev_dbg`，**默认看不见** ——
这是 `07-debug.md` §2 里"申请通道失败为什么 dmesg 一片安静"的答案。

### §A2.2 五条退出路径一览

| 行 | 返回 | 已持有的东西 | 是否泄漏 |
|---|---|---|---|
| `:348` | `-ENODEV` | 无 | 否 |
| `:358` | 原样透传 | `con_mutex`（已释放 `:357`） | 否 |
| `:373` | `of_xlate` 的错误 或 `-EPROBE_DEFER` | 同上（`:372` 已释放） | 否 |
| `:379` | `-EBUSY` | 同上（`:378` 已释放） | **见下** |
| `:405` | `chan` 或 `ERR_PTR(startup 的错误)` | `con_mutex`（`:404` 释放） | 否 |

`:376` 那一行的短路有一个细节：如果 `chan->cl` 为 NULL 但
`try_module_get` **成功**了，条件为假、继续往下走 —— 正确。
如果 `chan->cl` 非空，`try_module_get` **根本不会被调用**（短路），
所以不会有引用泄漏。这是短路求值在这里恰好正确的一个例子，
但它依赖读者看出 `||` 的短路语义 —— 换成两个独立的 `if` 会更清楚。
`of_node_put(spec.np)` 在 `:369`，**在所有后续错误路径之前**，也不会泄漏。

---

## §A3 T1 变体：按名字申请（`mbox_request_channel_byname`，`mailbox.c:409-437`）

29 行，**全部是包装**，最后一定落到 `mbox_request_channel`。

```c
struct device_node *np = cl->dev->of_node;          /* :412  无 !cl->dev 校验 */
...
if (!np) {
	dev_err(cl->dev, "%s() currently only supports DT\n", __func__);
	return ERR_PTR(-EINVAL);                    /* :419 */
}

if (!of_get_property(np, "mbox-names", NULL)) {
	dev_err(cl->dev, "%s() requires an \"mbox-names\" property\n", __func__);
	return ERR_PTR(-EINVAL);                    /* :425 */
}

of_property_for_each_string(np, "mbox-names", prop, mbox_name) {
	if (!strncmp(name, mbox_name, strlen(name)))    /* :429  前缀匹配 */
		return mbox_request_channel(cl, index);
	index++;
}

dev_err(cl->dev, "%s() could not locate channel named \"%s\"\n",
	__func__, name);
return ERR_PTR(-EINVAL);                            /* :436 */
```

三处要点：

1. **`:429` 是前缀匹配，不是相等比较。**
   `strncmp(name, mbox_name, strlen(name))` 比较的长度取自**调用者传入的 `name`**，
   所以 DT 里写 `mbox-names = "tx-alt", "tx"` 时，
   请求 `"tx"` 会命中 **index 0 的 `tx-alt`**。
   这是**静默的错通道**，没有任何警告。归因与规避见 `06-devicetree.md` §5。
2. **三条失败路径全用 `dev_err`（`:418`、`:423`、`:434`），全部可见** ——
   和 `mbox_request_channel` 那四条 `pr_debug`/`dev_dbg` 形成鲜明对比。
   ⇒ **调试建议：申请通道失败但 dmesg 一片安静时，说明用的是
   `mbox_request_channel` 而不是 byname 变体**（`07-debug.md` §2.1）。
3. `:412` 在函数开头就解引用 `cl->dev`，而它转调的
   `mbox_request_channel:346` 才校验 `!dev` —— **校验在解引用之后**，
   顺序反了。`cl->dev == NULL` 会在 byname 路径上直接崩，
   在直接路径上得到 `-ENODEV`。见 §B5.2。

注意本函数**没有任何一处对 `name` 做 NULL 校验**，
`strlen(name)` 会先崩。全树 38 个客户端全部传字面量，实际不可达。

---

## §A4 T1 内部：DT 参数 → 通道指针（`of_xlate`）

### §A4.1 核心的默认实现（`of_mbox_index_xlate`，`mailbox.c:467-477`）

全函数 11 行：

```c
static struct mbox_chan *
of_mbox_index_xlate(struct mbox_controller *mbox,
		    const struct of_phandle_args *sp)
{
	int ind = sp->args[0];                     /* :471 */

	if (ind >= mbox->num_chans)                /* :473 */
		return ERR_PTR(-EINVAL);

	return &mbox->chans[ind];                  /* :476 */
}
```

**两处校验缺失，都在这 11 行里：**

| 缺什么 | 后果 | 触发条件 |
|---|---|---|
| **下界** | `args[0]` 的类型是 `uint32_t`（`include/linux/of.h:78`），赋给 `int ind`。DT 里写 `≥ 0x80000000` 就变成负数，**通过 `>=` 检查**，返回 `&mbox->chans[负下标]` —— 越界指针交到客户端手里 | DT 里 mailbox cell 写了一个超大值 |
| **`args_count`** | `sp->args[0]` 在 `args_count == 0` 时读的是未初始化内容。`Documentation/devicetree/bindings/mailbox/mailbox.txt:9-10` 明确说 `#mbox-cells` 「Must be at least 1」，但核心不检查 | 控制器 DT 节点写了 `#mbox-cells = <0>` |

**对照：驱动自己写的 `of_xlate` 比核心的默认实现更严。**
全树 **16 个**自己实现 `of_xlate` 的控制器里，有 **8 个显式校验 `args_count`**：

| 驱动 | 行 | 判据 |
|---|---|---|
| `drivers/mailbox/arm_mhuv2.c` | `:799` | `!= 2` |
| `drivers/mailbox/imx-mailbox.c` | `:639-640` `:675-676` `:697-698` | `!= 2` / `!= 2` / `< 1`，三处都带 `dev_err` |
| `drivers/mailbox/bcm-flexrm-mailbox.c` | `:1451` | `< 3` |
| `drivers/mailbox/bcm2835-mailbox.c` | `:130` | `!= 0` |
| `drivers/mailbox/apple-mailbox.c` | `:316` | `!= 0` |
| `drivers/mailbox/ti-msgmgr.c` | `:592-594` | `!= ncells`，带 `dev_err` |
| `drivers/mailbox/qcom-ipcc.c` | `:174` | `!= 2` |

⇒ 结论不是"驱动作者都很小心"，而是**核心的默认实现是全树里最不设防的那一个**，
而它恰好是另外 13 个没写 `of_xlate` 的控制器（去掉 ACPI 的 `pcc.c` 是 12 个）所使用的那一个。
记进 `99-evidence.md` §6，标注 **静态推断，需要畸形 DT 才能触发（属配置错误而非攻击面）**。

### §A4.2 为什么有 16 个驱动要自己写

因为默认实现假定 **`#mbox-cells = <1>` 且那一个 cell 就是通道下标**。
不成立的三种情况：

| 情况 | 例子 | 自定义 `of_xlate` 做什么 |
|---|---|---|
| cells 不是下标，而是 (类型, 编号) 二元组 | `arm_mhuv2.c:799` | 从两个 cell 算出通道 |
| 单通道控制器，`#mbox-cells = <0>` | `bcm2835-mailbox.c:130`、`apple-mailbox.c:316` | 校验 `args_count == 0`，恒返回 `&chans[0]` |
| 通道要按 DT 描述动态建立 | `ti-msgmgr.c:592` | 用 `desc` 里的 `num_valid_queues` 折算 |

完整的 DT 侧写法在 `06-devicetree.md` §3；本节只交代**核心这一侧**发生了什么。

---

## §A5 T2：发一条消息（`mbox_send_message`，`mailbox.c:256-288`）

33 行，但它调的两个 static 函数才是重点。

### §A5.1 三层结构

```
mbox_send_message :256
  ├─ 校验 chan / chan->cl                              :260-261
  ├─ add_to_rbuf(chan, mssg)                            :263   ← 入队，持 chan->lock
  │    └─ 满了返回 -ENOBUFS                             :34-37
  ├─ msg_submit(chan)                                   :269   ← 可能立刻发出
  │    ├─ 持 chan->lock                                  :60
  │    ├─ active_req 非空就直接退出（串行化）             :62-63
  │    ├─ 现算下标取出队头                                :65-70
  │    ├─ cl->tx_prepare(cl, data)                       :74-75  锁内
  │    ├─ ops->send_data(chan, data)                     :77     锁内
  │    ├─ 成功：active_req = data; msg_count--            :79-80
  │    └─ POLL 且没排队：hrtimer_start                    :85-90  嵌套第二把锁
  └─ tx_block ? wait_for_completion_timeout : return t   :271-287
```

### §A5.2 `add_to_rbuf`（`:26-51`）：唯一的入队点

```c
spin_lock_irqsave(&chan->lock, flags);                  /* :31 */

if (chan->msg_count == MBOX_TX_QUEUE_LEN) {             /* :34 */
	spin_unlock_irqrestore(&chan->lock, flags);
	return -ENOBUFS;                                /* :36 */
}

idx = chan->msg_free;                                   /* :39 */
chan->msg_data[idx] = mssg;                             /* :40 */
chan->msg_count++;                                      /* :41 */

if (idx == MBOX_TX_QUEUE_LEN - 1)                       /* :43 */
	chan->msg_free = 0;
else
	chan->msg_free++;

spin_unlock_irqrestore(&chan->lock, flags);
return idx;                                             /* :50 */
```

**返回值是"存进了第几格"，不是 0**。`mbox_send_message:263-267`
只判 `t < 0`，所以正常路径上这个下标被丢掉了 ——
但它同时是 `mbox_send_message` 成功时返回给调用者的**令牌**（`:287` 返回 `t`）。
这个令牌**没有任何 API 消费它**：`mbox_flush` 不接令牌，
`tx_done` 回调也不带令牌。它唯一的作用是"非负即成功"。
详见 `01-api.md` §2.2 与 `02-datastructures.md` §7 的读图注 ①。

队列深度硬编码 `MBOX_TX_QUEUE_LEN`（`mailbox_controller.h:100-101`，值 20，
旁边就是那条 `/* Try increasing MBOX_TX_QUEUE_LEN if error */` 的 `REVISIT`），
**控制器驱动无法调整**。见 §C2。

### §A5.3 `msg_submit`（`:53-91`）：核心里最关键的 39 行

三条必须记住的性质：

**① `active_req` 就是"串行化闸门"（`:62-63`）**

```c
if (!chan->msg_count || chan->active_req)
	goto exit;
```

在前一条消息 TXDone 之前，后续 `mbox_send_message` 只会入队，**不会碰硬件**。
所以 `send_data` 天然是串行的，控制器驱动不需要自己防重入。

**② `tx_prepare` 和 `send_data` 都在 `chan->lock` 里（`:74-77`）**

```c
if (chan->cl->tx_prepare)
	chan->cl->tx_prepare(chan->cl, data);       /* :75 */

err = chan->mbox->ops->send_data(chan, data);       /* :77 */
if (!err) {
	chan->active_req = data;                    /* :79 */
	chan->msg_count--;                          /* :80 */
}
```

这是**整个子系统里所有"不许睡眠"约束的唯一根因**：
`chan->lock` 是 `spin_lock_irqsave`，所以

- 控制器的 `send_data` 不许睡（连 `msleep(1)` 都不行）；
- 消费者的 `tx_prepare` 不许睡；
- 两者都不许再调任何 mailbox API（`mbox_send_message` 要同一把锁）。

而 `tx_done`（`:110-111`）和 `rx_callback`（`:161`）**在锁外**，
所以它们的约束宽得多。完整矩阵见 §B1.2。

`send_data` 失败时**不撤销入队**：消息还在 `msg_data[]` 里、`msg_count` 没减，
下一次 TXDone 或下一次 `msg_submit` 会重试同一条。
但如果通道上此刻**没有** in-flight 请求（`active_req` 仍为 NULL），
就没人会再来触发 `msg_submit` —— 消息会**卡在队列里直到下一次
`mbox_send_message`**。`send_data` 返回非 0 的错误在核心里**不上报给消费者**
（`mbox_send_message` 此时已经返回了那个令牌）。记进 `99-evidence.md` §6。

**③ POLL 模式在这里启动 hrtimer（`:85-90`）**

```c
exit:
	spin_unlock_irqrestore(&chan->lock, flags);

	/* kick start the timer immediately to avoid delays */
	if (!err && (chan->txdone_method & TXDONE_BY_POLL)) {
		/* but only if not already active */
		spin_lock_irqsave(&chan->mbox->poll_hrt_lock, flags);   /* :87 */
		if (!hrtimer_active(&chan->mbox->poll_hrt))
			hrtimer_forward_now(&chan->mbox->poll_hrt,
					    ms_to_ktime(0));
		spin_unlock_irqrestore(&chan->mbox->poll_hrt_lock, flags);
	}
```

注意 `:86` 这里用的是**位测试 `&`**，而 `mbox_request_channel:389`
用的是 `==` —— 两种写法在核心里并存。`02-datastructures.md` §1.4 已列表对比。
这是全核心**唯一的锁嵌套**（`chan->lock` 已在 `:83` 释放，
所以严格说不是嵌套，而是**顺序获取**）—— 反向顺序不存在，不会 AB-BA。
这也是 ACK→POLL 隐患的**崩溃现场**：如果通道的 `txdone_method`
被翻成了 POLL 而控制器从未走过 `mailbox.c:500-511` 的初始化分支，
`hrtimer_active(&chan->mbox->poll_hrt)` 读的就是未初始化内存。见 §B3.2。

### §A5.4 阻塞与超时（`:271-287`）

```c
if (chan->cl->tx_block) {
	unsigned long wait;
	int ret;

	if (!chan->cl->tx_tout)          /* wait forever */
		wait = msecs_to_jiffies(3600000);       /* :276 */
	else
		wait = msecs_to_jiffies(chan->cl->tx_tout);

	ret = wait_for_completion_timeout(&chan->tx_complete, wait);  /* :280 */
	if (ret == 0) {
		t = -ETIME;
		tx_tick(chan, t);                       /* :283 */
	}
}
```

四条要点：

1. **`/* wait forever */` 是 1 小时，不是无限**（`:275-276`）。
   `tx_tout == 0` 落到这个分支。3600000 ms 这个数字没有任何技术依据。
2. **kernel-doc `:249-250` 说本函数「can be called from atomic context」，
   但 `tx_block == true` 时 `:280` 会睡。** 两者矛盾。
   实际含义是"只有 `tx_block == false` 时才能原子上下文调"，文档没说清。
   记进 `99-evidence.md` §6。
3. 超时时**由调用线程自己调 `tx_tick`**（`:283`）—— 这是 `tx_tick`
   四个调用点里唯一不在中断/软中断上下文的那个，也是唯一传非 0 错误码的那个。
   `tx_tick:113` 里那个 `r != -ETIME` 的判断正是为这条路径准备的，见 §A6.2。
4. **`tx_block == false` 的客户端永远不知道超时发生了**：
   没有 `wait_for_completion`，就没有 `-ETIME`，也没人调 `tx_tick(chan, -ETIME)`。
   这类客户端必须靠自己的协议超时。见 `05-usage.md` §3.3。

---

## §A6 T3：三条 TXDone 路径汇入 `tx_tick`

### §A6.1 三个入口

| 方法 | 入口函数 | 谁调 | 上下文 | 关键行 |
|---|---|---|---|---|
| **IRQ** | `mbox_chan_txdone` `:176-185` | 控制器驱动的中断处理 | 硬中断/线程化中断 | 校验 `:178`，调 `tx_tick(chan, 0)` `:184` |
| **POLL** | `txdone_hrtimer` `:117-146` | hrtimer 软中断 | 软中断 | 校验 `:128`，调 `last_tx_done` `:129`，`tx_tick(chan, 0)` `:131` |
| **ACK** | `mbox_client_txdone` `:197-205` | 消费者代码 | 任意（不许持 `chan->lock`） | 校验 `:199`，调 `tx_tick(chan, err)` `:204` |

只有 ACK 这一条能传**非 0 的错误码**（`mbox_client_txdone(chan, err)`），
IRQ 和 POLL 两条硬编码 0 —— 也就是说**控制器无法通过 TXDone 路径报告
"发出去了但对方 NACK 了"**。第四个调用点是超时路径（`:283`，传 `-ETIME`）。

### §A6.2 `tx_tick`（`:93-115`）：状态机的另一半

```c
static void tx_tick(struct mbox_chan *chan, int r)
{
	unsigned long flags;
	void *mssg;

	spin_lock_irqsave(&chan->lock, flags);              /* :98 */
	mssg = chan->active_req;
	chan->active_req = NULL;                            /* :100  闸门在这里开 */
	spin_unlock_irqrestore(&chan->lock, flags);         /* :101 */

	/* Submit next message */
	msg_submit(chan);                                   /* :104  在 tx_done 之前 */

	if (!mssg)
		return;                                     /* :107  幂等保护 */

	/* Notify the client */
	if (chan->cl->tx_done)
		chan->cl->tx_done(chan->cl, mssg, r);       /* :111 */

	if (r != -ETIME && chan->cl->tx_block)              /* :113 */
		complete(&chan->tx_complete);               /* :114 */
}
```

**`msg_submit` 在 `tx_done` 之前（`:104` vs `:110-111`）。**
后果：消费者的 `tx_done` 回调**看到的已经不是"通道空闲"的状态** ——
下一条消息可能已经在硬件上跑了。想在 `tx_done` 里"发下一条"的写法是多余的，
核心已经发了。

`:107` 那个 `if (!mssg) return` 是**重复 TXDone 的幂等保护**：
IRQ 和 ACK 两条路径都可能被调多次（比如控制器中断抖动、消费者手滑多调一次
`mbox_client_txdone`），第二次进来 `active_req` 已是 NULL，
就在 `:107` 静默返回。但注意 **`msg_submit`（`:104`）在这个保护之前** ——
一次多余的 TXDone 会白跑一次 `msg_submit`，而 `msg_submit:62-63`
会因为 `active_req` 非空（新消息刚发出）而立刻退出。**无害，但不优雅。**

**`:113` 的 `r != -ETIME` 是一处容易读漏的短路**：超时路径下
`complete()` **不会**被调。原因是超时是 `mbox_send_message:280-283` 里
**等待线程自己**发现的 —— 它已经放弃等待了，这时再 `complete()`
会把 completion 的计数加到 1，让**下一次** `wait_for_completion_timeout`
立刻返回、误判成"发送成功"。所以这个条件不是优化，是必需的。
对应地，`tx_done` 回调（`:110-111`）**照样会被调**，参数 `r` 就是 `-ETIME`
—— 这是 `tx_block == true` 的客户端得知超时的第二条途径
（第一条是 `mbox_send_message` 返回 `-ETIME`）。

### §A6.3 `txdone_hrtimer` 的额外细节（`:117-146`）

```c
container_of(hrtimer, struct mbox_controller, poll_hrt);     /* :119-120 */

for (i = 0; i < mbox->num_chans; i++) {                      /* :125 */
	struct mbox_chan *chan = &mbox->chans[i];            /* :126 */

	if (chan->active_req && chan->cl) {                  /* :128 */
		txdone = chan->mbox->ops->last_tx_done(chan);/* :129 */
		if (txdone)
			tx_tick(chan, 0);                    /* :131 */
		else
			resched = true;                      /* :133 */
	}
}

if (resched) {
	spin_lock_irqsave(&mbox->poll_hrt_lock, flags);      /* :138 */
	if (!hrtimer_is_queued(hrtimer))                     /* :139 */
		hrtimer_forward_now(hrtimer, ms_to_ktime(mbox->txpoll_period));
	spin_unlock_irqrestore(&mbox->poll_hrt_lock, flags);
	return HRTIMER_RESTART;                              /* :143 */
}
return HRTIMER_NORESTART;                                    /* :145 */
```

一次定时器回调**扫这个控制器的全部通道**，不是每通道一个定时器。
所以 `txpoll_period`（`02-datastructures.md` §3.1 有 10 处赋值的普查）
是**控制器级**的粒度，忙通道和闲通道共享同一个轮询周期。

`:128` 是三条 TXDone 路径里**校验最严**的那一个（`active_req && cl` 两项），
`mbox_chan_txdone:178` 只查一项，`mbox_client_txdone:199` 查的是另一回事。
防护强度的完整对比见 §B3.1。

`mbox->txpoll_period` 为 0 时 `ms_to_ktime(0)` ⇒ 定时器立刻再触发 ⇒
**软死循环**（核心不校验 0，全树也确实无人填 0）。记进 `99-evidence.md` §6。

---

## §A7 T3'：接收路径（`mbox_chan_received_data`，`mailbox.c:158-163`）

**全核心最短的函数，6 行，而且不加任何锁：**

```c
/**
 * mbox_chan_received_data - A way for controller driver to push data
 *				received from remote to the upper layer.
 * @chan: Pointer to the mailbox channel on which RX happened.
 * @mssg: Client specific message typecasted as void *
 *
 * After startup and before shutdown any data received on the chan
 * is passed on to the API via atomic mbox_chan_received_data().
 * The controller should ACK the RX only after this call returns.
 */
void mbox_chan_received_data(struct mbox_chan *chan, void *mssg)
{
	/* No buffering the received data */
	if (chan->cl->rx_callback)               /* :161 */
		chan->cl->rx_callback(chan->cl, mssg);
}
```

四条要点：

1. **它解引用 `chan->cl` 却不检查 `chan->cl` 是否为 NULL。**
   契约只写在 kernel-doc 注释里（`:154-156`：「After startup and before shutdown」）。
   控制器驱动如果在 `shutdown` 之后仍然有中断进来并调这个函数，
   就是 NULL 解引用。对比 `mbox_send_message:260-261` 同时查 `chan` 和 `chan->cl`
   —— **两个方向的入口防护强度完全不对称**。见 §B3.1。
2. **`/* No buffering the received data */`（`:160`）—— RX 侧完全没有流控。**
   TX 侧有 20 格环形队列，RX 侧一格都没有。中断来一次就回调一次，
   `rx_callback` 处理不过来就丢数据（或者阻塞中断）。
   这是"为什么 mailbox 消费者的 `rx_callback` 必须极短"的结构性原因。
3. `rx_callback` 事实上是**必需的**：不填它，控制器收到的数据会被静默丢弃，
   而核心一句话都不说。全树 38 个客户端全部填了。见 `02-datastructures.md` §2。
4. kernel-doc 说 `atomic mbox_chan_received_data()` —— 意思是
   **`rx_callback` 必须能在原子上下文运行**（多数控制器在硬中断里调它）。
   但注意 `rx_callback` 本身**不在任何 mailbox 锁里**（`:161` 无锁），
   所以它可以调 `mbox_send_message`（会去拿 `chan->lock`）—— 这正是
   "收到请求 → 立刻回响应"这种协议能实现的原因。

---

## §A8 T4：释放通道（`mbox_free_channel`，`mailbox.c:445-464`）

20 行，是**整个核心里最需要小心读的函数**。

```c
void mbox_free_channel(struct mbox_chan *chan)
{
	unsigned long flags;

	if (!chan || !chan->cl)
		return;                                 /* :450 */

	if (chan->mbox->ops->shutdown)
		chan->mbox->ops->shutdown(chan);        /* :453 */

	/* The queued TX requests are simply aborted, no callbacks are made */
	spin_lock_irqsave(&chan->lock, flags);          /* :456 */
	chan->cl = NULL;                                /* :457 */
	chan->active_req = NULL;                        /* :458 */
	if (chan->txdone_method == TXDONE_BY_ACK)       /* :459 */
		chan->txdone_method = TXDONE_BY_POLL;   /* :460 */
	spin_unlock_irqrestore(&chan->lock, flags);

	module_put(chan->mbox->dev->driver->owner);     /* :463 */
}
```

五条要点，前两条是本子系统最重要的两个坑：

1. **`:459-460` 把 ACK 翻成 POLL。**
   动机大概是"恢复 `mbox_request_channel:389-390` 做的那次翻转"，
   但那次翻转的**前提是 `cl->knows_txdone` 为真**，而这里**无条件翻**。
   于是：一个从未被 `knows_txdone` 客户端占用过的 ACK 通道
   （控制器两个 flag 都没设 ⇒ `:497-498` 判成 ACK），
   走一次 request→free 之后 `txdone_method` 就变成了 POLL，
   而 `poll_hrt` 从未初始化（`:500-511` 只在 POLL 分支执行）。
   下一次有人发消息，`msg_submit:87-88` 就会去操作未初始化的 hrtimer。
   **完整链条与两个触发点见 §B3.2 和 `01-api.md` §5.3。静态推断，未复现。**
2. **本函数不取 `con_mutex`。** 申请通道全局串行（`:351-404`），
   释放通道只取 `chan->lock`。这个不对称的后果见 §B2。
3. **`shutdown` 在锁外调（`:453`），在 `chan->cl = NULL` 之前。**
   所以 `shutdown` 里还能安全读 `chan->cl`（拿消费者的 `dev` 打日志），
   而且**允许睡眠**（`mailbox_controller.h:26-31`）。
   但它是在 `mbox_request_channel` 的 `con_mutex` 里被间接调到的
   （`:399` → `:453`），所以照样不许调 mailbox API。
4. 队列里没发完的消息**静默丢弃**，注释（`:455`）已明说
   `no callbacks are made` —— `tx_done` 不会被调，`tx_complete` 不会被 complete。
   后果：正卡在 `wait_for_completion_timeout`（`:280`）里的线程
   **会一直等到超时**（默认 1 小时），不会被提前唤醒。
   ⇒ 消费者 remove 时必须**先确保没有 in-flight 请求**再 `mbox_free_channel`。
   这是 `05-usage.md` §4 的 remove 骨架里那一步 `mbox_flush` 的理由。
5. `:463` 再一次三级解引用 `chan->mbox->dev->driver->owner`，
   与 `:376` 配对（`try_module_get` ↔ `module_put`），同样没校验 `->driver`。

---

## §A9 T5：控制器注销与 devres（`mailbox.c:537-555` / `:558-626`）

### §A9.1 `mbox_controller_unregister`（`:537-555`）

```c
void mbox_controller_unregister(struct mbox_controller *mbox)
{
	int i;

	if (!mbox)
		return;                                 /* :542 */

	mutex_lock(&con_mutex);                         /* :544 */

	list_del(&mbox->node);                          /* :546 */

	for (i = 0; i < mbox->num_chans; i++)
		mbox_free_channel(&mbox->chans[i]);     /* :549 */

	if (mbox->txdone_poll)
		hrtimer_cancel(&mbox->poll_hrt);        /* :552 */

	mutex_unlock(&con_mutex);                       /* :554 */
}
```

**先摘链再释放通道**（`:546` 在 `:549` 之前）：保证释放过程中不会有新的
`mbox_request_channel` 匹配到这个控制器。这一步顺序是对的。

**`:549` 替消费者调 `mbox_free_channel`，消费者不会收到任何通知** ——
`mbox_free_channel` 里没有"回调消费者告诉它通道没了"的机制，
连 `dev_warn` 都没有。消费者手里那个 `chan` 指针从此指向一条 `cl == NULL`
的通道；它下一次 `mbox_send_message` 会在 `:260-261` 拿到 `-EINVAL`。
**这是 §B2 的主题。**

**`:551` 检查的是控制器 flag `mbox->txdone_poll`，而 hrtimer 的初始化
（`:500-511`）判据是推导出来的 `txdone == TXDONE_BY_POLL`。** 两者在什么情况下会分叉？

| 情形 | `txdone_poll` | 初始化过 hrtimer？ | `:551` 会 cancel？ | 结论 |
|---|---|---|---|---|
| 只设 `txdone_poll` | true | 是 | 是 | 正确 |
| 只设 `txdone_irq` | false | 否 | 否 | 正确 |
| 两个都不设（ACK） | false | 否 | 否 | 正确 |
| 两个都设 | true | **否**（`:493-494` 判成 IRQ） | **是** | cancel 未初始化的 hrtimer |
| ACK 通道被 `:460` 翻成 POLL | false | 否 | 否 | 漏 cancel（但 `:88` 已经先崩了） |

第 4 行是真的分叉，但**全树无人触发**：`mailbox_controller.h:36-42`
明令「never set both」，而 §A1.1 的普查证实 22 个驱动全部遵守
（三个 grep 候选全是 if/else 分支）。
⇒ **这条只能记成"约定被违反时的健壮性缺口"，不能当成活 bug**。
它在 `99-evidence.md` §4 里作为一条交叉验证实例（初始判断被三次 Read 推翻）。
第 5 行在链条上排在 `:88` 崩溃之后，实际观察不到。

### §A9.2 devres 变体（`:558-626`）

```
devm_mbox_controller_register :587-608
  ├─ ptr = devres_alloc(__devm_mbox_controller_unregister, ...)   :594
  ├─ ret = mbox_controller_register(mbox)                          :599
  ├─ 失败：devres_free(ptr); return ret                            :600-603
  ├─ *ptr = mbox                                                   :605
  └─ devres_add(dev, ptr)                                          :606
devm_mbox_controller_unregister :623-626
  └─ WARN_ON(devres_release(dev, __devm_mbox_controller_unregister,
                            devm_mbox_controller_match, mbox))     :625
```

注意 `:605` 和 `:606` 的顺序：**先填 `*ptr` 再 `devres_add`** ——
正确的顺序（反了就会有一个已加入 devres 链表但内容未初始化的节点）。
早先记在 PROGRESS 里的"先 `devres_add` 后填 `*ptr`"是笔误，此处以源码为准。

`devm_mbox_controller_unregister`（`:623-626`）**存在但几乎无人用**：
devres 的意义就是不用手写 unregister。它是给"想提前释放"的驱动准备的，
带 `WARN_ON` 是因为传错 `mbox` 会静默什么都不做。

`devres_alloc` 用的是 `GFP_KERNEL`（`:594`），所以
**`devm_mbox_controller_register` 会睡** —— 只能在 probe 这类可睡上下文调。

---

## §A10 Part A 汇总：主流程表（含拆除路径）

| # | 阶段 | 入口 | 关键 static 函数 | 持哪把锁 | 可以睡？ | 失败怎么表现 |
|---|---|---|---|---|---|---|
| 1 | 控制器注册 | `mbox_controller_register` `:485` | — | `con_mutex` 两行 `:525-527` | ✅（`devm_` 变体分配内存） | `-EINVAL`，只有 `last_tx_done` 缺失有日志 `:503` |
| 2 | 申请通道 | `mbox_request_channel` `:337` | `of_mbox_index_xlate` `:467` | `con_mutex` 全程 `:351-404` + `chan->lock` `:382-392` | ✅（`startup` 在锁内但允许睡） | 4 个 debug 级 + 1 个 `dev_err` `:398` |
| 2' | 按名申请 | `..._byname` `:409` | 转调 #2 | 同上 | ✅ | 3 处 `dev_err`，全部可见 |
| 3 | 发消息 | `mbox_send_message` `:256` | `add_to_rbuf` `:26` → `msg_submit` `:53` | `chan->lock` `:31-48`/`:60-83` → `poll_hrt_lock` `:87-89` | 仅 `tx_block` 分支会睡 `:280` | `-EINVAL`/`-ENOBUFS`/`-ETIME`；`send_data` 的错误不上报 |
| 4 | 冲刷 | `mbox_flush` `:305` | — | 无（控制器 `flush` 自己管） | 看驱动实现 | `-ENOTSUPP`（不是 `-EOPNOTSUPP`）`:310` |
| 5 | TXDone·IRQ | `mbox_chan_txdone` `:176` | `tx_tick` `:93` | `chan->lock` `:98-101` | ❌（中断上下文） | `dev_err` `:179-180` 然后返回 |
| 5' | TXDone·POLL | `txdone_hrtimer` `:117` | `tx_tick` | `chan->lock` + `poll_hrt_lock` `:138-141` | ❌（软中断） | 无（`last_tx_done` 返 false 就续期） |
| 5'' | TXDone·ACK | `mbox_client_txdone` `:197` | `tx_tick` | `chan->lock` | ❌（不许持 `chan->lock` 调） | `dev_err` `:200` 然后返回 |
| 6 | 收消息 | `mbox_chan_received_data` `:158` | — | **无锁** | ❌（多数在硬中断） | 无任何校验，`cl` 为空直接崩 |
| **7** | **释放通道** | `mbox_free_channel` `:445` | — | **只有 `chan->lock`**，**无 `con_mutex`** | ✅（`shutdown` 在锁外 `:453`） | 静默：`!chan \|\| !chan->cl` 直接 return `:450` |
| **8** | **控制器注销** | `mbox_controller_unregister` `:537` | `mbox_free_channel` ×N `:549` | `con_mutex` `:544-554` | ✅ | 静默：`!mbox` 直接 return `:542`；抢走通道不通知消费者 |

读这张表的两个用法：
「可以睡？」一列直接告诉控制器驱动作者哪些回调里能用 `mutex`/`msleep`；
「失败怎么表现」一列是 `07-debug.md` §2 那张错误消息映射表的来源。

---

# Part B 横切

> Part A 按时间排，读起来顺；但有四件事横跨多个时刻，
> 拆到时间线里就永远看不全。这四件事是：锁与上下文、生命周期不对称、
> 三条 TXDone 路径的防护强度差异、边界与错误路径的防护缺口。

## §B1 锁与上下文

### §B1.1 三把锁的全景

| 锁 | 定义 | 保护什么 | 持有区间 |
|---|---|---|---|
| `con_mutex` | `mailbox.c:24`（全局） | `mbox_cons` 链表 + 通道占用的原子性 | `:351-404`（request，含 `startup`）· `:525-527`（register）· `:544-554`（unregister） |
| `chan->lock` | `mailbox.c:519`（`spin_lock_irqsave`） | 环形队列 + `active_req` + `cl` + `txdone_method` | `:31-48`(入队) · `:60-83`(提交) · `:98-101`(tick) · `:382-392`(占用) · `:456-462`(释放) |
| `poll_hrt_lock` | `mailbox.c:510`（**只在 POLL 分支初始化**） | `poll_hrt` 的 forward/queued 判断 | `:87-89`(kick) · `:138-141`(续期) |

**嵌套只有一处方向**：`msg_submit` 在 `:83` 释放 `chan->lock` 之后、
`:87` 才取 `poll_hrt_lock` —— 严格说是**顺序获取而非嵌套**。
`txdone_hrtimer` 那一侧（`:138`）取 `poll_hrt_lock` 时也不持 `chan->lock`
（`tx_tick` 在 `:131` 已经取完放完）。**反向顺序在全核心不存在 ⇒ 不会 AB-BA。**

**`con_mutex` 与 `chan->lock` 之间也没有真正的嵌套**：
`:382-392` 那段 spinlock 在 `con_mutex` 里面，是 mutex→spinlock 的正确方向；
反向（spinlock 里取 mutex）在核心里不存在。

### §B1.2 回调的上下文约束矩阵（这是本篇最实用的一张表）

| 回调 | 谁的 | 调用点 | 持锁？ | 可以睡？ | 可以调 mailbox API？ |
|---|---|---|---|---|---|
| `startup` | 控制器 | `:395` | `con_mutex` | ✅ | ❌ **绝对不行**（自死锁） |
| `shutdown` | 控制器 | `:453` | 间接可能在 `con_mutex` 里（`:399`→`:453`） | ✅ | ❌ 同上 |
| `send_data` | 控制器 | `:77` | `chan->lock`（spinlock，irq 关闭） | ❌ | ❌ |
| `last_tx_done` | 控制器 | `:129` | 无（软中断上下文） | ❌ | ❌ |
| `flush` | 控制器 | `:312` | 无 | ✅ 视实现（要等硬件） | 语义是「忙等到发完」（`mailbox_controller.h:23-25`） |
| `peek_data` | 控制器 | `:228` | 无 | ❌ | ❌ |
| `tx_prepare` | 消费者 | `:75` | `chan->lock` | ❌ | ❌ |
| `tx_done` | 消费者 | `:111` | **无锁** | ❌（可能在硬中断） | ⚠️ 可以，但没必要（`:104` 已经发了下一条） |
| `rx_callback` | 消费者 | `:161` | **无锁** | ❌（多数在硬中断） | ✅ 可以 `mbox_send_message`（收→回响应的常见写法） |

读这张表的方式：**「持锁？」一列是「可以睡？」的原因，不是并列的两件事。**
凡是 `chan->lock` 里的回调，睡眠就是死锁；凡是锁外的，
限制只来自"调用者所处的上下文"（多数是中断）。

`flush` 的 kernel-doc（`mailbox_controller.h:23-25`）说它
「will implement a busy loop waiting for the data to flush out」——
所以它是唯一被明确要求**忙等**的回调。
而 `mbox_flush:312-314` 在它返回负值时会替调用者调 `tx_tick`，
所以 `flush` **不需要**自己去调 `mbox_chan_txdone` 报失败；
成功路径上则由控制器原有的 TXDone 机制负责。全树 4 个实现者见 `01-api.md` §3。

---

## §B2 生命周期不对称（三处）

> 01 §5 和 02 §8.1 都把这一节列为落点，这里给完整的三条。

### §B2.1 申请全局串行，释放不串行

| | `mbox_request_channel` | `mbox_free_channel` |
|---|---|---|
| `con_mutex` | 全程持有 `:351-404` | **完全不取** |
| `chan->lock` | 持 `:382-392` | 持 `:456-462` |
| 改 `chan->cl` | 在 spinlock 内 `:386` | 在 spinlock 内 `:457` |
| 调控制器回调 | `startup`，在 `con_mutex` 内 `:395` | `shutdown`，**在两把锁都不持的时候** `:453` |

**后果：`shutdown` 与 `startup` 之间没有互斥。**
构造场景：CPU-A 正在 `mbox_free_channel:453` 里跑 `shutdown`（可以睡，可能很慢），
此时 CPU-B 调 `mbox_request_channel` —— 它拿到 `con_mutex`（CPU-A 没持），
读 `chan->cl` 发现**还是非 NULL**（CPU-A 要到 `:457` 才清），
于是在 `:376` 得到 `-EBUSY` 并返回。**结果只是一次假 `-EBUSY`，不是内存错误。**

那如果 CPU-A 已经跑过 `:457`（`cl` 清了）而 `shutdown` 还没返回呢？
不可能 —— `:453` 在 `:457` 之前，`shutdown` 返回了才会清 `cl`。
⇒ **这个不对称是安全的，代价只是"释放期间申请会拿到 `-EBUSY`"。**
但它同时意味着：**`startup`/`shutdown` 这一对回调在控制器驱动看来
不保证配对出现在同一把锁下**，驱动内部要自己做互斥。
静态推断，未构造复现。记进 `99-evidence.md` §6。

### §B2.2 控制器注销时抢走通道，消费者收不到通知

`mbox_controller_unregister:548-549` 对每条通道调 `mbox_free_channel`。
这条路径上：

| 消费者会看到什么 | 什么时候 |
|---|---|
| `shutdown` 被调 | 立刻（`:453`），但那是**控制器**的回调，消费者看不到 |
| `chan->cl` 变 NULL | 立刻（`:457`），消费者手里的 `chan` 指针仍然有效但已"空壳化" |
| 卡在 `wait_for_completion_timeout` 的线程 | **不会被唤醒**，一直等到超时（`tx_block` 且 `tx_tout == 0` ⇒ **1 小时**） |
| 下一次 `mbox_send_message` | 在 `:260-261` 拿到 `-EINVAL` |
| 任何日志 | **一条都没有**。`mbox_free_channel` 里连 `dev_warn` 都没有 |

**核心没有任何"通道被回收"的回调或通知机制** ——
`mbox_client` 里没有 `remove`/`detach` 之类的字段（`02-datastructures.md` §2 的 7 行字段表）。
⇒ 正确的做法是靠 **驱动模型的依赖顺序**：`try_module_get`（`:376`）
保证控制器模块在有消费者时无法 `rmmod`，
而 `devm_mbox_controller_register` 保证控制器设备 unbind 时才注销。
**但"控制器 unbind 而消费者仍在"这条路径没有任何保护**（unbind 不看模块引用计数）。
静态推断，未复现；记进 `99-evidence.md` §6。

### §B2.3 `mbox_flush` 的入口校验与 `mbox_send_message` 不一致

```c
int mbox_flush(struct mbox_chan *chan, unsigned long timeout)
{
	int ret;

	if (!chan->mbox->ops->flush)     /* :309  直接三级解引用 chan */
		return -ENOTSUPP;        /* :310  不是 -EOPNOTSUPP */

	ret = chan->mbox->ops->flush(chan, timeout);
	if (ret < 0)
		tx_tick(chan, ret);      /* :314 */

	return ret;
}
```

三处问题，都在这 13 行里：

1. **`:309` 不校验 `chan` 也不校验 `chan->cl`**，而
   `mbox_send_message:260-261` 两个都校验。同一篇文件里的两个消费者侧 API，
   防护强度不一致。
2. **`:314` 会调 `tx_tick`，而 `tx_tick:110` 解引用 `chan->cl->tx_done`。**
   ⇒ 在一条已经 `mbox_free_channel` 过的通道上调 `mbox_flush`，
   如果驱动的 `flush` 返回负值，就是 **NULL 解引用**。
   `mbox_send_message` 在同样场景下只是返回 `-EINVAL`。
   全树只有 3 个 `mbox_flush` 调用者，都在正常生命周期内调，实际不可达。
3. `-ENOTSUPP` 是内核内部约定不该出现在返回给用户态的路径上的码
   （正确的是 `-EOPNOTSUPP`），这里是消费者侧 API，多数消费者会原样往上传。
   记进 `99-evidence.md` §6。

---

## §B3 三条 TXDone 路径的防护强度不一致

> 02 §4.2 把这一节列为落点。

### §B3.1 三个入口的校验对照

| 路径 | 校验代码 | 校验了什么 | 漏了什么 |
|---|---|---|---|
| IRQ `:176-185` | `if (unlikely(!(chan->txdone_method & TXDONE_BY_IRQ))) { dev_err(...); return; }` `:178-182` | 方法位 | 不查 `chan`、不查 `chan->cl` |
| POLL `:117-146` | `if (chan->active_req && chan->cl)` `:128` | 状态 + `cl` **两项** | 不查方法位（但它只从 POLL 分支的 hrtimer 进来，隐含成立） |
| ACK `:197-205` | `if (unlikely(!(chan->txdone_method & TXDONE_BY_ACK))) { dev_err(...); return; }` `:199-203` | 方法位 | 不查 `chan`、不查 `chan->cl`、不查 `active_req` |
| 超时 `:283` | 无 | — | 由调用者保证（它刚从 `:280` 醒来，`chan->cl` 必然有效） |

**POLL 是唯一查 `chan->cl` 的路径**，而它恰恰是最不需要查的那一条
（hrtimer 属于控制器，通道被释放时 `active_req` 已清 `:458`）。
IRQ 和 ACK 这两条**由外部代码任意时刻调用**的路径反而不查。
⇒ 这不是"设计取向"，只能解释为历史累积。记进 `99-evidence.md` §6。

三条路径都有一个共同的兜底：`tx_tick:106-107` 的 `if (!mssg) return`。
所以"通道空闲时收到多余 TXDone"不会崩，只会白跑一次 `msg_submit`。
真正会崩的是 `chan->cl == NULL`（`tx_tick:110` 解引用它）——
而这只有在通道被释放之后仍有 TXDone 进来才会发生。

两条 `dev_err`（`:179-180` 和 `:200`）的文本几乎一样，但函数名不同：

```
:179-180   "Controller can't run the TX ticker\n"   ← mbox_chan_txdone，控制器错了
:200       "Client can't run the TX ticker\n"       ← mbox_client_txdone，消费者错了
```

这一对是本子系统最容易看错的近邻符号（`02-datastructures.md` §9 已列表）。
在 dmesg 里认它们的办法：**看 `Controller` 还是 `Client`**。

### §B3.2 ACK→POLL 隐患在时间线上的位置

完整的因果链（`01-api.md` §5.3 有逐条取证，这里只给它在时间线上的落点）：

```
T0  控制器 register，两个 flag 都不设
      └→ :497-498  txdone = TXDONE_BY_ACK
      └→ :500-511  POLL 分支【跳过】⇒ poll_hrt / poll_hrt_lock 从未初始化
      └→ :518      每条 chan 的 txdone_method = ACK

T1  消费者 A 申请通道（cl->knows_txdone = false）
      └→ :389      条件 (== POLL && knows_txdone) 为假，不翻转 ⇒ 仍是 ACK

T4  消费者 A 释放通道（或 T1 的 startup 失败走 :399）
      └→ :459-460  无条件把 ACK 翻成 POLL

T1' 消费者 B 申请同一条通道（cl->knows_txdone = false）
      └→ :389      条件 (== POLL && knows_txdone)：POLL 成立但 knows_txdone 假
                   ⇒ 【不翻回来】通道此刻是 POLL，而 poll_hrt 从未初始化

T2' 消费者 B 发消息
      └→ :86       chan->txdone_method & TXDONE_BY_POLL 为真
      └→ :87-88    spin_lock_irqsave(&mbox->poll_hrt_lock)  ← 未初始化的 spinlock
                   hrtimer_active(&mbox->poll_hrt)           ← 未初始化的 hrtimer
```

**两个触发点，近的那个在 T1 就发生**：
`mbox_request_channel:399` 在 `startup` 失败时立刻调 `mbox_free_channel` ——
不需要等到 unbind。而 `startup` 失败最常见的原因是"资源没就绪、稍后重试"，
下一次重试就落在坏状态上。

**为什么全树没人报这个 bug？** 三道现实屏障：
① 走 ACK 的控制器不多（`:497-498` 的默认分支，需要两个 flag 都不设）；
② 这些控制器的消费者绝大多数是"申请一次、用到 unbind"，从不释放再申请；
③ `startup` 失败在正常硬件上极少发生。
⇒ 结论定性为 **静态推断，未复现，需要"ACK 控制器 + 通道复用"两个条件同时成立**。
记进 `99-evidence.md` §6 第 ① 条。

---

## §B4 边界检查缺口汇总

| 位置 | 缺什么 | 可达性 |
|---|---|---|
| `of_mbox_index_xlate:471-473` | 下界（`uint32_t` → `int`）+ `args_count` | 需要畸形 DT（配置错误） |
| `mbox_controller_register:490` | `mbox->chans` 不在 sanity 列表里，`:514` 才崩 | 需要驱动 bug |
| `mbox_controller_register` | `txpoll_period == 0` 不校验 ⇒ `ms_to_ktime(0)` 软死循环（`:140`） | 全树无人填 0 |
| `mbox_request_channel:376` | `mbox->dev->driver` 可能为 NULL | 不可达（控制器在自己 probe 里注册） |
| `mbox_request_channel_byname:412` | `cl->dev` 未校验就解引用 | 需要消费者 bug |
| `mbox_chan_received_data:161` | `chan->cl` 未校验 | 需要控制器在 shutdown 后仍上报 |
| `mbox_flush:309` | `chan` / `chan->cl` 未校验，且 `:314` 会解引用 `chan->cl` | 全树 3 个调用者都在生命周期内 |
| `mbox_chan_txdone:178` / `mbox_client_txdone:199` | 只查方法位，不查 `chan` / `chan->cl` | 需要控制器/消费者 bug |

这张表的用法：**它不是"内核有 8 个 bug"的清单**，而是
"写控制器/消费者驱动时核心不会替你挡住的 8 件事"。
每一行的"可达性"列都说明了需要谁先犯错。

---

## §B5 错误路径的两处可疑之处

### §B5.1 `startup` 失败之后 `shutdown` 仍会被调

```
mbox_request_channel:395   ret = ops->startup(chan)
                    :399   mbox_free_channel(chan)      ← 失败时
mbox_free_channel   :453   ops->shutdown(chan)          ← 于是 shutdown 也跑了
```

**控制器驱动的 `shutdown` 必须能承受"从未成功 startup"的状态。**
典型的坑：`startup` 里 `request_irq` 失败返回，`shutdown` 里无条件 `free_irq`。
全树 26 个 `shutdown` 实现**没有一个在注释里提到这一点**，
`Documentation/driver-api/mailbox.rst` 也没提 ⇒ **未文档化的隐式契约**。
记进 `99-evidence.md` §6。

顺带：`:399` 之后 `chan->cl` 已被 `:457` 清空，
所以 `mbox_request_channel` 返回 `ERR_PTR(ret)` 时通道确实是干净的（除了 §B3.2 的 POLL 翻转）。

### §B5.2 `-EBUSY` 与 `-ENODEV` 的归因混乱

| 消费者拿到 | 可能的真实原因 | 怎么区分 |
|---|---|---|
| `-ENODEV` | `cl->dev` 为 NULL **或** `cl->dev->of_node` 为 NULL（`:346`） | 无法从返回码区分；byname 路径下第一种会直接崩（`:412`） |
| `-EBUSY` | 通道已被占用 **或** 控制器模块正在卸载（`:376`） | 无法区分；`dev_dbg` 的文本只对前一种成立 |
| `-EPROBE_DEFER` | 控制器还没注册 **或** 已注册但 `of_xlate` 失败（`:361`） | 无法区分 —— `01-api.md` §4.2 给了从 DT 侧反查的办法 |
| `-EINVAL` | `mboxes` index 越界（透传）**或** byname 三种失败（`:419` `:425` `:436`） | byname 三种都有 `dev_err`，可见 |

结论：**mailbox 的错误码归因能力很弱**，四个码里有三个是多对一。
唯一可靠的调试手段是**打开 dynamic debug**：
```
echo 'file drivers/mailbox/mailbox.c +p' > /sys/kernel/debug/dynamic_debug/control
```
详见 `07-debug.md` §2.1。

---

# Part C 评价

## §C1 设计取向：三个"没有"

| 没有什么 | 代价 | 收益 |
|---|---|---|
| **没有 `bus_type`** | 通道在 `/sys` 下完全不可见，无法枚举"谁占了哪条通道" | 826 行就够了；消费者不需要是 `struct device` |
| **没有 tracepoint、核心没有 debugfs** | 观测只能靠 dynamic debug 和驱动自己的 debugfs | 零运行时开销；无 ABI 承诺要维护 |
| **没有"通道被回收"的通知机制** | §B2.2 那一整格 | `mbox_client` 只有 7 个字段，消费者写起来极简 |

这三个"没有"是**同一个取向的三个表现**：
mailbox 把自己定位成"一层薄薄的队列 + 串行化"，
而不是"一个设备模型子系统"。对比 regulator（8000+ 行、完整的 sysfs、
11 条 tracepoint）就很清楚 —— 后者管理的是**可共享的、有状态的资源**，
mailbox 管理的是**独占的、无状态的管道**。

13 个 `EXPORT_SYMBOL_GPL`（`/usr/bin/grep -c EXPORT_SYMBOL drivers/mailbox/mailbox.c`）
就是全部对外接口 —— 消费者侧 7 个 + 控制器侧 6 个（含两个 `devm_`）。

## §C2 源码里的设计债

### §C2.1 唯一一条 `REVISIT`

`include/linux/mailbox_controller.h:90-103` 那段注释是核心里唯一的自陈设计债：

```
 * ... Besides, mbox_send_message could be called from atomic context and
 * the client could also queue another message from the notifier 'tx_done'
 * of the last transfer done.
 * REVISIT: If too many platforms see the "Try increasing MBOX_TX_QUEUE_LEN"
 * print, it needs to be taken from config option or somesuch.
 */
#define MBOX_TX_QUEUE_LEN	20             /* :103 */
```

三层问题，一层套一层：

1. **深度 20 硬编码**，控制器驱动无法调整（不是 `mbox_controller` 的字段）。
2. 它承诺的诊断出口是 `mbox_send_message:265` 的
   `dev_err(chan->mbox->dev, "Try increasing MBOX_TX_QUEUE_LEN\n")` ——
   **但这条建议对读者不可操作**：改它要重编内核，而且是全局改。
3. `REVISIT` 的条件（"too many platforms see the print"）
   **无法被观察到** —— 没有计数器、没有 tracepoint，
   只能靠用户上报 dmesg。这条债从 mailbox 引入至今没有被偿还。

顺带注意 `:97-99` 那句注释是"为什么 20 够用"的论证：
中断延迟才是瓶颈，而且 `tx_done` 里还能再排一条。
这句话与 §A6.2 的发现一致：`msg_submit`（`:104`）在 `tx_done`（`:111`）
**之前**跑，所以从 `tx_done` 里排的那一条不会被立刻发出，
只会进队列 —— 注释描述的行为是对的。

### §C2.2 没有 `FIXME` / `TODO`，但有 6 处"事实上的债"

`/usr/bin/grep -n 'REVISIT\|FIXME\|TODO\|XXX\|HACK' ` 在核心四个文件上只命中 1 处（上面那条）。
但下面这些同样是债，只是没有注释标记：

| # | 债 | 位置 | 证据类型 |
|---|---|---|---|
| ① | ACK→POLL 无条件翻转 | `:459-460` | 静态推断（§B3.2） |
| ② | 默认 `of_xlate` 无下界、无 `args_count` | `:471-473` | 静态推断（§A4.1） |
| ③ | kernel-doc 说可原子上下文，`tx_block` 分支会睡 | `:249-250` vs `:280` | 直接矛盾 |
| ④ | `/* wait forever */` 其实是 1 小时 | `:275-276` | 直接矛盾 |
| ⑤ | `-ENOTSUPP` 应为 `-EOPNOTSUPP` | `:310` | 约定违背 |
| ⑥ | byname 用前缀匹配 | `:429` | 静默错通道（§A3） |
| ⑦ | 三条 TXDone 路径校验不一致 | `:178` / `:128` / `:199` | 历史累积（§B3.1） |
| ⑧ | 官方文档说"除 `peek_data` 全部必需"，实测 `flush` 只有 4 个实现者 | `Documentation/driver-api/mailbox.rst:30` | 文档与代码冲突 |

全部条目的取证与定性见 `99-evidence.md` §6，那里同时记了**哪些被推翻过**。

## §C3 与三个邻居子系统的对比

| | mailbox | clk | regulator | dmaengine |
|---|---|---|---|---|
| 核心行数 | **826** | ~4400（`clk.c`） | ~6100（`core.c`） | ~1700（`dmaengine.c`） |
| 有 `bus_type`？ | ❌ | ❌ | ❌ | ❌ |
| 资源可共享？ | ❌ **独占** | ✅ 引用计数 | ✅ 引用计数 | ❌ 独占（channel） |
| sysfs 出口 | **无** | `/sys/kernel/debug/clk` | `/sys/class/regulator` | `/sys/class/dma` |
| tracepoint | **0** | 20+ | 11 | 0（核心） |
| 通道/资源查找 | DT `mboxes` + `of_xlate` | DT `clocks` + `of_clk_get` | DT `*-supply` | DT `dmas` + filter |
| 释放语义 | 排队消息静默丢弃 | 引用计数减一 | 引用计数减一 | 终止传输 |

**和 dmaengine 最像**（都是"独占管道 + DT 描述 + 零 tracepoint"），
和 clk/regulator 最不像（后两者的核心工作是**共享资源的引用计数与约束求解**，
mailbox 一个引用计数都没有）。
⇒ 学习顺序建议：**读过 dmaengine 再读 mailbox，成本最低**。

## §C4 本篇结论清单（可以直接背的 10 条）

1. 826 行里只有 5 个 static 函数是真逻辑；`msg_submit`（39 行）+ `tx_tick`（23 行）就是全部状态机。
2. `active_req` 是串行化闸门；它非空时 `send_data` 绝不会被再调（`:62-63`）。
3. `send_data` 和 `tx_prepare` 都在 `chan->lock`（spinlock，关中断）里 —— **所有"不许睡"约束的唯一根因**。
4. `tx_done` 和 `rx_callback` 在锁外 —— 所以 `rx_callback` 里可以 `mbox_send_message`。
5. `msg_submit` 在 `tx_done` **之前**跑（`:104` vs `:111`）—— 在 `tx_done` 里"发下一条"是多余的。
6. 三条 TXDone 路径只有 POLL 那条查 `chan->cl`（`:128`），另两条不查。
7. `mbox_request_channel` 全程持 `con_mutex`（含 `startup`），`mbox_free_channel` 一把全局锁都不持。
8. `mbox_free_channel:459-460` 无条件把 ACK 翻成 POLL —— 本子系统最值得警惕的一行。
9. 默认 `of_xlate`（`:471-473`）是全树 17 个实现里最不设防的那一个（16 个驱动实现中有 8 个查 `args_count`）。
10. 申请通道失败时 dmesg 可能一片安静（4 个 debug 级出口）；byname 变体的三个出口都是 `dev_err`。

---

## §C5 读完本篇之后

| 想知道 | 去哪篇 |
|---|---|
| `mailbox-test` 这个内核自带的调试客户端怎么用 | `04-extensions.md` |
| 照抄一个控制器驱动 / 消费者的骨架 | `05-usage.md` |
| DTS 怎么写、`#mbox-cells` 到底填几 | `06-devicetree.md` |
| 打开 dynamic debug、绕过零 tracepoint 的观测手段 | `07-debug.md` |
| 用大白话理解"为什么 mailbox 没有 bus" | `08-feynman.md` |
| 本篇每条论断的取证与定性 | `99-evidence.md` §3 / §6 |
