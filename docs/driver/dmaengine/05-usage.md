---
title: 使用指南
---

# dmaengine 使用示例：各角色的代码骨架

> ⚠️ **本篇所有代码骨架均未编译、未运行。**
> 它们是从本树真实驱动里抽出的**结构**，用来说明"调用次序和成对关系"，
> 不是可以直接 `make` 的完整驱动。每个骨架下面都点名了它抄自哪个文件，
> 照着写之前请先读那个文件。行号一律基于本树（`linux-6.1.158`）。

---

## §0 本篇的角色枚举

dmaengine 是**框架型**子系统，围着它有五个角色。下表是本篇的目录：

| 角色 | 你是谁 | 要实现/调用什么 | 本篇章节 | 难度 |
|---|---|---|---|---|
| **消费者**（consumer） | 写 UART / SPI / MMC / 音频等外设驱动，想用 DMA 搬数据 | 调 `dma_request_chan` → `prep` → `submit` → `issue_pending` | §1 | |
| **提供者 · virt-dma 版**（provider） | 写 DMA 控制器驱动，用核心的虚拟通道层 | 填 `dma_device` 回调 + `vchan_*` 一族 | §2 | |
| **提供者 · 裸版** | 写 DMA 控制器驱动，自己管队列 | 填 `dma_device` 回调 + 自建队列 | §3 | |
| **router 驱动** | 手上的硬件是 mux / crossbar 不是控制器 | 实现 `route_allocate` / `route_free` + `of_dma_router_register` | §4 | |
| **测试者** | 要验证一个新写的控制器驱动 | 加载 `dmatest` 并读它的输出 | §5（骨架）+ `07-debug.md`（完整操作） | |

**DT 作者不在本篇**：DTS 怎么写、每个属性谁消费，全在 `06-devicetree.md`。
**async_tx 消费者不在本篇**：本树唯一的实际消费者是 md-raid，
你几乎不会自己写一个，机制说明在 `04-extensions.md` §2。

### §0.1 五个骨架的取材来源

| 章节 | 抄自 | 行数 | 为什么挑它 |
|---|---|---|---|
| §1 | `drivers/tty/serial/8250/8250_dma.c` | 311 | 本树最短的完整 slave 消费者，收发双向、含能力检查和完整拆除路径 |
| §2 | `drivers/dma/mediatek/mtk-uart-apdma.c` | 656 | 本树最短的 virt-dma slave 控制器，arm64（MT8xxx）在用 |
| §3 | 与 §2 对照说明 | — | 裸版没有短样本，本篇只列"你要自己补上哪几件事" |
| §4 | `drivers/dma/stm32-dmamux.c` | 403 | 四个 router 里结构最清楚的（虽然是 arm32 平台） |
| §5 | `drivers/dma/dmatest.c` | 1360 | 官方示范的消费者，同时是验收工具 |

---

## §1 消费者驱动：外设驱动怎么用 DMA

### §1.1 五个阶段与不可省的成对关系

```
probe/open 时                                    每次传输                          remove/close 时
─────────────                                    ────────                          ──────────────
① dma_request_chan(dev, "rx")   ─────────────────────────────────────────────►  ⑧ dma_release_channel(chan)
② dma_get_slave_caps(chan, &caps)  先问清能力
③ dmaengine_slave_config(chan, &conf)  每次改方向都要重发
④ dma_map_single / dma_alloc_coherent ────────────────────────────────────────►  ⑦ dma_unmap_single / dma_free_coherent
                                     ⑤ dmaengine_prep_slave_single/_sg
                                        desc->callback = my_cb
                                        dmaengine_submit(desc)
                                        dma_async_issue_pending(chan)
                                     ⑥ 回调里 dma_sync / 补下一笔
                                                                                 ⑨ dmaengine_terminate_sync(chan)
                                                                                    必须在 ⑦⑧ 之前
```

**三条成对关系，错了就是 use-after-free 或内存泄漏**：

| 必须成对 | 顺序不能反的理由 |
|---|---|
| `dma_request_chan` ↔ `dma_release_channel` | 引用计数；release 内部会调 `device_free_chan_resources`，还会归还 router 占用（`drivers/dma/dmaengine.c:510-514`） |
| `dma_map_*` ↔ `dma_unmap_*` | 一致性域；未 unmap 前 CPU 读到的可能是旧数据 |
| `dmaengine_terminate_sync` **在** `dma_unmap_*` / `dma_free_*` **之前** | 不 terminate 就 unmap = 引擎还在往已释放的内存里写。见 §1.4 |

### §1.2 申请与配置（骨架，抄自 `drivers/tty/serial/8250/8250_dma.c:195-284`）

```c
static int my_request_dma(struct my_dev *my)
{
	struct dma_slave_caps caps;
	struct dma_slave_config rxconf = { }, txconf = { };
	int ret;

	/* ① 申请通道：DT 里 dma-names 写的名字 */
	my->rxchan = dma_request_chan(my->dev, "rx");
	if (IS_ERR(my->rxchan))
		return PTR_ERR(my->rxchan);   /* 可能是 -EPROBE_DEFER，要原样返回 */

	/* ② 先问能力，再决定要不要用它 —— 8250_dma.c:222-228 的做法 */
	ret = dma_get_slave_caps(my->rxchan, &caps);
	if (ret)
		goto release_rx;
	if (!caps.cmd_pause || !caps.cmd_terminate ||
	    caps.residue_granularity == DMA_RESIDUE_GRANULARITY_DESCRIPTOR) {
		ret = -EINVAL;                /* 这个控制器不满足我的需求 */
		goto release_rx;
	}

	/* ③ 配置从设备侧参数：地址 / 位宽 / 方向 */
	rxconf.direction      = DMA_DEV_TO_MEM;
	rxconf.src_addr       = my->phys_base + MY_RX_FIFO;
	rxconf.src_addr_width = DMA_SLAVE_BUSWIDTH_1_BYTE;
	dmaengine_slave_config(my->rxchan, &rxconf);

	my->txchan = dma_request_chan(my->dev, "tx");
	if (IS_ERR(my->txchan)) {
		ret = PTR_ERR(my->txchan);
		goto release_rx;
	}
	txconf.direction      = DMA_MEM_TO_DEV;
	txconf.dst_addr       = my->phys_base + MY_TX_FIFO;
	txconf.dst_addr_width = DMA_SLAVE_BUSWIDTH_1_BYTE;
	dmaengine_slave_config(my->txchan, &txconf);

	/* ④ 缓冲区：RX 用一致性内存长期挂着，TX 一次性 map */
	my->rx_buf = dma_alloc_coherent(my->rxchan->device->dev, MY_RX_SIZE,
					&my->rx_addr, GFP_KERNEL);
	if (!my->rx_buf) {
		ret = -ENOMEM;
		goto err;
	}
	my->tx_addr = dma_map_single(my->txchan->device->dev, my->tx_buf,
				     MY_TX_SIZE, DMA_TO_DEVICE);
	if (dma_mapping_error(my->txchan->device->dev, my->tx_addr)) {
		dma_free_coherent(my->rxchan->device->dev, MY_RX_SIZE,
				  my->rx_buf, my->rx_addr);
		ret = -ENOMEM;
		goto err;
	}
	return 0;
err:
	dma_release_channel(my->txchan);
release_rx:
	dma_release_channel(my->rxchan);
	return ret;
}
```

**四个几乎人人写错的点**：

| 点 | 错法 | 对法 | 依据 |
|---|---|---|---|
| **map 用哪个 device** | `dma_map_single(my->dev, ...)` | `dma_map_single(chan->device->dev, ...)` 或 `dmaengine_get_dma_device(chan)` | 做 DMA 的是**控制器**，不是外设。`8250_dma.c:265`、`drivers/dma/dmatest.c:609` |
| **返回值判空** | `if (!chan)` | `if (IS_ERR(chan))` | `dma_request_chan` 返回 `ERR_PTR`，从不返回 `NULL`（`drivers/dma/dmaengine.c:813-873`） |
| **`-EPROBE_DEFER`** | 当成失败 `dev_err` 后 return `-ENODEV` | 原样 `return PTR_ERR(chan)`，让驱动模型重试 | 控制器可能还没 probe 完 |
| **能力检查** | 直接 prep，失败再说 | 先 `dma_get_slave_caps` | prep 回调不存在时核心会返回 `NULL`，症状比 `-EINVAL` 难查 |

一个**反面教材**要点名：`8250_dma.c:214-217`/`:233-236` 用的是
`dma_request_slave_channel_compat(mask, fn, param, dev, name)`。
它在 `include/linux/dmaengine.h:1595-1609`，内部先调
`dma_request_slave_channel`（`:1602`）—— 而后者在 `:1586-1587` 明确标了
`/* Deprecated, please use dma_request_chan() directly */` + `__deprecated`。
**`_compat` 自己没打 `__deprecated` 标记，所以编译时不会警告**，
但它一样是过时接口。**新驱动一律用 `dma_request_chan`**。
（登记在 `99-evidence.md` §6。）

### §1.3 一笔传输（骨架，抄自 `8250_dma.c:100-130`）

```c
static int my_start_tx(struct my_dev *my, unsigned int len)
{
	struct dma_async_tx_descriptor *desc;

	/* ⑤-1 prep：拿一个描述符。失败返回 NULL 而不是 ERR_PTR */
	desc = dmaengine_prep_slave_single(my->txchan, my->tx_addr, len,
					   DMA_MEM_TO_DEV,
					   DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!desc)
		return -EBUSY;

	/* ⑤-2 挂回调。必须在 submit 之前填 —— submit 之后描述符可能已被引擎接手 */
	desc->callback       = my_tx_done;
	desc->callback_param = my;

	/* ⑤-3 CPU 侧的写要先对设备可见 */
	dma_sync_single_for_device(my->txchan->device->dev, my->tx_addr,
				   len, DMA_TO_DEVICE);

	/* ⑤-4 submit：拿到 cookie。cookie 只在同一通道内可比 */
	my->tx_cookie = dmaengine_submit(desc);

	/* ⑤-5 踢一脚，硬件才真的动 */
	dma_async_issue_pending(my->txchan);
	return 0;
}

static void my_tx_done(void *param)          /* 运行在 tasklet 上下文，不能睡 */
{
	struct my_dev *my = param;
	unsigned long flags;

	spin_lock_irqsave(&my->lock, flags);
	dma_sync_single_for_cpu(my->txchan->device->dev, my->tx_addr,
				MY_TX_SIZE, DMA_TO_DEVICE);
	/* ... 统计 / 唤醒 / 补下一笔 ... */
	spin_unlock_irqrestore(&my->lock, flags);
}
```

**四条上下文规则**（依据 `03-internals.md` §A4 / §B2）：

| 规则 | 依据 |
|---|---|
| `prep_*` **可以睡**（多数驱动里会 `kzalloc(GFP_NOWAIT)`，但契约允许睡） | `Documentation/driver-api/dmaengine/provider.rst` |
| `dmaengine_submit` / `dma_async_issue_pending` **不能睡** | 常在中断/自旋锁内调用 |
| **回调运行在 tasklet（软中断）上下文，不能睡、不能调 `dmaengine_synchronize`** | `vchan_complete`（`drivers/dma/virt-dma.c:83-110`）是 tasklet |
| **回调里可以调 `dmaengine_terminate_async`，但绝不能调 `dmaengine_terminate_sync`** | `dmaengine_synchronize` 有 `might_sleep()`（`include/linux/dmaengine.h:1157`） |

`prep_*` 的第 4 个参数那些 flag（`DMA_PREP_INTERRUPT` / `DMA_CTRL_ACK` /
`DMA_PREP_FENCE` …）的完整语义在 `01-api.md` §5 和
`02-datastructures.md` §4 里，本篇不重复。
**不给 `DMA_PREP_INTERRUPT` 就可能没有回调** —— 这是最常见的"传输成功但驱动卡住"。

### §1.4 拆除（骨架，抄自 `8250_dma.c:287-310`）

```c
static void my_release_dma(struct my_dev *my)
{
	/* ⑨ 先停 —— terminate_sync 会等回调跑完才返回 */
	dmaengine_terminate_sync(my->rxchan);
	/* ⑦ 再还内存 */
	dma_free_coherent(my->rxchan->device->dev, MY_RX_SIZE,
			  my->rx_buf, my->rx_addr);
	/* ⑧ 最后还通道 */
	dma_release_channel(my->rxchan);
	my->rxchan = NULL;                        /* 置 NULL，防二次释放 */

	dmaengine_terminate_sync(my->txchan);
	dma_unmap_single(my->txchan->device->dev, my->tx_addr,
			 MY_TX_SIZE, DMA_TO_DEVICE);
	dma_release_channel(my->txchan);
	my->txchan = NULL;
}
```

**`terminate_sync` 的一个静默陷阱**（`03-internals.md` §A5.1 / §C1 ⑤）：

`dmaengine_terminate_sync`（`include/linux/dmaengine.h:1177-1188`）分两步：
先 `dmaengine_terminate_async`（`:1181`），再 `dmaengine_synchronize`（`:1185`）。
而 `dmaengine_synchronize`（`:1155-1161`）的第二步是**有条件的**：

```c
	if (chan->device->device_synchronize)      /* :1159 */
		chan->device->device_synchronize(chan);
```

**`device_synchronize` 是可选回调**。控制器驱动没实现它时，
`dmaengine_terminate_sync` 就**静默降级成 `terminate_async`** ——
函数名里的 "sync" 是假的，你以为回调已经停了，其实 tasklet 可能正在跑。
接着 `dma_unmap_single` + `kfree` 就是 use-after-free。

**两条自保措施**：
1. 在 `dma_get_slave_caps` 之后**顺手查一次**
   `chan->device->device_synchronize != NULL`（`dma_slave_caps` 里**没有**
   对应的能力位，只能直接看字段）；
2. 拆除路径上自己再补一次同步（如 `flush_work` / 自己的 completion）。

用 virt-dma 的控制器驱动通常都实现了（`vchan_synchronize`，
`drivers/dma/virt-dma.h:211-225`），裸版驱动漏掉的概率高。
统计方法与结论见 `99-evidence.md` §3。

### §1.5 查询进度：`dmaengine_tx_status` 的两个坑

```c
	enum dma_status st;
	struct dma_tx_state state;

	st = dmaengine_tx_status(my->rxchan, my->rx_cookie, &state);
	/* state.residue = 还没搬完的字节数 */
```

| 坑 | 说明 |
|---|---|
| **residue 的精度取决于 `residue_granularity`** | `DMA_RESIDUE_GRANULARITY_DESCRIPTOR` 只在整笔结束时才更新 —— 循环 RX 场景里等于没有。这就是 `8250_dma.c:224-228` 显式拒绝这一档的原因 |
| `enum dma_status` 有 5 个取值，kerneldoc 只列了 4 个 | `DMA_OUT_OF_ORDER`（`include/linux/dmaengine.h:42`）在 kerneldoc（`:31-36`）里没写。`switch` 记得带 `default` |

---

## §2 提供者 · virt-dma 版：控制器驱动骨架

取材 `drivers/dma/mediatek/mtk-uart-apdma.c`（656 行）。
它是本树最短的 virt-dma slave 控制器，MT8xxx（arm64）在用。

### §2.1 先看全景：你要填的六个格子

```
你的私有结构                              核心/virt-dma 提供的
──────────                                ──────────────────
struct my_dmadev {
    struct dma_device ddev;  ◄──────────── 必须是第一个成员或用 container_of
    ...
};
struct my_chan {
    struct virt_dma_chan vc; ◄──────────── vc.chan 就是对外的 struct dma_chan
    void __iomem *base;
    struct my_desc *desc;    ◄──────────── 「当前正在跑的那一笔」，你自己管
    int irq;
};
struct my_desc {
    struct virt_dma_desc vd; ◄──────────── 必须第一个成员（vchan_tx_prep 要 &vd->tx）
    dma_addr_t addr;
    size_t len;
};

  probe                 每笔传输                 中断              拆除
  ─────                 ────────                 ────              ────
  ① 填 ddev 能力/回调    ④ prep_slave_sg          ⑥ 你的 ISR       ⑧ free_chan_resources
  ② 每通道 vchan_init    ⑤ issue_pending           └ vchan_cookie_    ├ tasklet_kill
  ③ dma_async_device_       ├ vchan_issue_pending      complete       ├ vchan_free_chan_
    register +              ├ vchan_next_desc                         │  resources
    of_dma_controller_      └ 摸寄存器启动                            └ terminate_all
    register                                                            ├ vchan_get_all_
                                                                        │  descriptors
                                                                        └ vchan_dma_desc_
                                                                           free_list
```

**virt-dma 帮你做的是"五个链表 + 一个 tasklet"**，
不帮你做调度 —— 「下一笔跑哪个」永远是你自己在 `issue_pending` 里决定的。
机制细节见 `04-extensions.md` §1，链表状态机见 `03-internals.md` §A4.

### §2.2 probe：三段（抄自 `mtk-uart-apdma.c:477-575`）

```c
static int my_probe(struct platform_device *pdev)
{
	struct my_dmadev *d;
	struct my_chan *c;
	int i, rc;

	d = devm_kzalloc(&pdev->dev, sizeof(*d), GFP_KERNEL);   /* :485 */
	if (!d)
		return -ENOMEM;

	/* ───── 第 1 段：声明能力与回调（mtk 在 :506-521） ───── */
	dma_cap_set(DMA_SLAVE, d->ddev.cap_mask);               /* :506 */
	d->ddev.device_alloc_chan_resources = my_alloc_chan_resources;
	d->ddev.device_free_chan_resources  = my_free_chan_resources;
	d->ddev.device_tx_status            = my_tx_status;
	d->ddev.device_issue_pending        = my_issue_pending;
	d->ddev.device_prep_slave_sg        = my_prep_slave_sg;
	d->ddev.device_config               = my_slave_config;
	d->ddev.device_pause                = my_pause;
	d->ddev.device_terminate_all        = my_terminate_all;
	d->ddev.device_synchronize          = my_synchronize;   /* mtk 漏了这个，见 §2.5 */
	d->ddev.src_addr_widths    = BIT(DMA_SLAVE_BUSWIDTH_1_BYTE);  /* :517 位掩码！ */
	d->ddev.dst_addr_widths    = BIT(DMA_SLAVE_BUSWIDTH_1_BYTE);  /* :518 */
	d->ddev.directions         = BIT(DMA_DEV_TO_MEM) | BIT(DMA_MEM_TO_DEV); /* :519 */
	d->ddev.residue_granularity = DMA_RESIDUE_GRANULARITY_SEGMENT;/* :520 */
	d->ddev.dev = &pdev->dev;                               /* :521 */
	INIT_LIST_HEAD(&d->ddev.channels);                      /* :522 不做就崩 */

	/* ───── 第 2 段：每通道 vchan_init（mtk 在 :525-550） ───── */
	if (of_property_read_u32(pdev->dev.of_node, "dma-requests",
				 &d->nr_chans))                 /* :525 */
		d->nr_chans = MY_DEFAULT_CHANS;                 /* :526 属性缺省要兜底 */

	for (i = 0; i < d->nr_chans; i++) {                     /* :531 */
		c = devm_kzalloc(d->ddev.dev, sizeof(*c), GFP_KERNEL);  /* :532 */
		if (!c)
			return -ENOMEM;
		c->base = devm_platform_ioremap_resource(pdev, i);      /* :538 */
		if (IS_ERR(c->base))
			return PTR_ERR(c->base);

		vchan_init(&c->vc, &d->ddev);   /* :544 —— 它内部就把通道挂进
						 * ddev.channels 了（virt-dma.c:137），
						 * 你不要再自己 list_add */
		rc = platform_get_irq(pdev, i);                         /* :546 */
		if (rc < 0)
			return rc;
		c->irq = rc;                                            /* :549 */
	}

	/* ───── 第 3 段：注册（mtk 在 :552-566），次序不能换 ───── */
	pm_runtime_enable(&pdev->dev);                          /* :552 */
	pm_runtime_set_active(&pdev->dev);                      /* :553 */

	rc = dma_async_device_register(&d->ddev);               /* :555 */
	if (rc)
		goto err_pm;

	rc = of_dma_controller_register(pdev->dev.of_node,
					of_dma_xlate_by_chan_id,
					&d->ddev);  /* :562 见下面的 data 参数陷阱 */
	if (rc)
		goto err_unreg;

	platform_set_drvdata(pdev, d);
	return 0;
err_unreg:
	dma_async_device_unregister(&d->ddev);                  /* :569 */
err_pm:
	pm_runtime_disable(&pdev->dev);                         /* :571 */
	return rc;
}
```

**probe 里的四个硬次序**：

| 次序 | 违反的后果 | 依据 |
|---|---|---|
| `INIT_LIST_HEAD(&ddev.channels)` **在** `vchan_init` **之前** | `vchan_init` 里 `list_add_tail(&vc->chan.device_node, &dmadev->channels)`（`drivers/dma/virt-dma.c:137`）会往未初始化的头上挂 → 立即 oops | 见左 |
| `vchan_init` **全部做完** 再 `dma_async_device_register` | 注册函数会遍历 `ddev.channels` 给每个通道建 sysfs / percpu（`drivers/dma/dmaengine.c:1247-1251`），漏掉的通道对外不存在 | 见左 |
| `dma_async_device_register` **在** `of_dma_controller_register` **之前** | 反了的话消费者可能在通道还没建好时就 xlate 成功，拿到半成品 | `03-internals.md` §A1 |
| 能力/回调**全部填完**再 register | 注册函数的九道能力门（`dmaengine.c:1153-1214`）当场检查回调是否齐；后填的不算 | 见左 |

**`of_dma_xlate_by_chan_id`** 是核心提供的现成 xlate
（`drivers/dma/of-dma.c:351-370`），语义是"DT 里的第一个参数就是通道号"，
要求 `#dma-cells = <1>`。自己没有特殊路由需求时直接用它，不要自己写 xlate。

**`of_dma_controller_register` 第三个参数（`data`）的隐式布局依赖**：
用 `of_dma_xlate_by_chan_id` 时，它第一行就是
`struct dma_device *dev = ofdma->of_dma_data;`（`of-dma.c:354`），
kerneldoc 也明确写了 "must be a pointer to the dma_device struct"（`:346-347`）。
但本树四个驱动传的是**自己的私有结构指针**：
`drivers/dma/mediatek/mtk-uart-apdma.c:562`、
`drivers/dma/mediatek/mtk-hsdma.c:982`、
`drivers/dma/mediatek/mtk-cqdma.c:857`、
`drivers/dma/mpc512x_dma.c:1066`。
它们**能工作，只因为 `struct dma_device` 恰好是私有结构的第一个成员**
（`mtk-uart-apdma.c:78-79`、`mtk-hsdma.c:232-233`、
`mtk-cqdma.c:139-140`、`mpc512x_dma.c:215-216`）。
**在私有结构头上插一个字段就会静默崩溃**，且编译器不会警告（`void *`）。
写新驱动请老实传 `&d->ddev`。登记在 `99-evidence.md` §6。

**`INIT_LIST_HEAD` 那一条容易被误判为"多余"**：
`dma_async_device_register` 自己**不会**初始化 `channels`
（它只遍历），所以这行必须驱动自己写。本树 147 个控制器驱动全都写了。

### §2.3 每笔传输：prep + issue_pending

```c
/* prep：只建描述符，不碰硬件（mtk :343-364） */
static struct dma_async_tx_descriptor *
my_prep_slave_sg(struct dma_chan *chan, struct scatterlist *sgl,
		 unsigned int sglen, enum dma_transfer_direction dir,
		 unsigned long tx_flags, void *context)
{
	struct my_chan *c = to_my_chan(chan);
	struct my_desc *d;

	if (!is_slave_direction(dir) || sglen != 1)   /* :351 能力之外一律拒 */
		return NULL;                          /* :352 返回 NULL 不是 ERR_PTR */

	d = kzalloc(sizeof(*d), GFP_NOWAIT);          /* :355 NOWAIT 不是 KERNEL */
	if (!d)
		return NULL;
	d->addr = sg_dma_address(sgl);                /* :360 */
	d->len  = sg_dma_len(sgl);                    /* :359 */
	c->dir  = dir;                                /* :361 */

	return vchan_tx_prep(&c->vc, &d->vd, tx_flags);  /* :363 唯一出口 */
}

/* issue_pending：把 submitted 搬到 issued，然后启动（mtk :366-384） */
static void my_issue_pending(struct dma_chan *chan)
{
	struct my_chan *c = to_my_chan(chan);
	struct virt_dma_desc *vd;
	unsigned long flags;

	spin_lock_irqsave(&c->vc.lock, flags);                 /* :372 必须持 vc.lock */
	if (vchan_issue_pending(&c->vc) && !c->desc) {         /* :373 */
		vd = vchan_next_desc(&c->vc);                  /* :374 只是 peek，不摘 */
		c->desc = to_my_desc(&vd->tx);                 /* :375 */
		my_hw_start(c);                                /* :377-380 摸寄存器 */
	}
	spin_unlock_irqrestore(&c->vc.lock, flags);            /* :383 */
}
```

**`vchan_next_desc` 只 peek 不摘链**（`drivers/dma/virt-dma.h:161-166`）。
描述符要一直留在 `desc_issued` 上，直到中断里你自己 `list_del`。
mtk 就是在 `mtk_uart_apdma_chan_complete_handler` 里
`list_del(&d->vd.node)`（`:246`）之后才 `vchan_cookie_complete`（`:247`）。
**忘了 `list_del` 就会重复完成同一个描述符** —— 症状是回调被调两次。

`&& !c->desc` 这个条件是 **"当前有笔在跑就不要重复启动"**。
硬件只有一条流水线时必须这么写；能排队的硬件才可以连续下发。

### §2.4 中断：三行做完（抄自 `mtk-uart-apdma.c:242-267`）

```c
static irqreturn_t my_irq(int irq, void *dev_id)
{
	struct my_chan *c = to_my_chan((struct dma_chan *)dev_id);
	unsigned long flags;

	spin_lock_irqsave(&c->vc.lock, flags);        /* :259 */
	my_hw_ack(c);                                 /* 清硬件中断标志 */
	if (c->desc) {
		list_del(&c->desc->vd.node);          /* :246 先摘链 */
		vchan_cookie_complete(&c->desc->vd);  /* :247 只是入队+踢 tasklet */
		c->desc = NULL;                       /* :248 */
	}
	spin_unlock_irqrestore(&c->vc.lock, flags);   /* :265 */
	return IRQ_HANDLED;
}
```

**`vchan_cookie_complete` 不调用消费者回调**
（`drivers/dma/virt-dma.h:94-106`）：它做三件事 ——
`dma_cookie_complete`（`:100`）、把描述符移到 `desc_completed`（`:103`）、
`tasklet_schedule(&vc->task)`（`:105`）。真正调回调的是 tasklet
`vchan_complete`（`drivers/dma/virt-dma.c:83-110`）。
**所以你的 ISR 里绝不会跑到消费者代码** —— 这正是 virt-dma 最大的价值：
它把"硬中断上下文"和"可能很慢的消费者回调"隔开了。

**循环（cyclic）传输不一样**：用 `vchan_cyclic_callback`
（`drivers/dma/virt-dma.h:131-137`）而不是 `vchan_cookie_complete`，
描述符不摘链、不入 completed 队列。`vchan_complete` 里那段
`vd = vc->cyclic`（`drivers/dma/virt-dma.c:92`）就是为它准备的。

### §2.5 拆除：三个回调 + mtk 的一个真实缺口

```c
/* terminate_all：停硬件 + 清光所有队列（mtk :396-441） */
static int my_terminate_all(struct dma_chan *chan)
{
	struct my_chan *c = to_my_chan(chan);
	unsigned long flags;
	LIST_HEAD(head);

	my_hw_stop(c);                                  /* :403-427 停 + 清标志 */
	synchronize_irq(c->irq);                        /* :432 等 ISR 退出 */

	spin_lock_irqsave(&c->vc.lock, flags);          /* :434 */
	vchan_get_all_descriptors(&c->vc, &head);       /* :435 五个链表全搬走 */
	spin_unlock_irqrestore(&c->vc.lock, flags);     /* :436 */

	vchan_dma_desc_free_list(&c->vc, &head);        /* :438 锁外释放 */
	return 0;
}

/* free_chan_resources（mtk :309-320） */
static void my_free_chan_resources(struct dma_chan *chan)
{
	struct my_chan *c = to_my_chan(chan);

	free_irq(c->irq, chan);                         /* :314 先摘中断 */
	tasklet_kill(&c->vc.task);                      /* :316 */
	vchan_free_chan_resources(&c->vc);              /* :318 */
	pm_runtime_put_sync(chan->device->dev);          /* :320 */
}

/* 这个 mtk 没有实现，但你应该实现 */
static void my_synchronize(struct dma_chan *chan)
{
	vchan_synchronize(&to_my_chan(chan)->vc);        /* drivers/dma/virt-dma.h:211-225 */
}
```

**`vchan_dma_desc_free_list` 必须在锁外调**：
它内部会调你的 `desc_free` 回调（`drivers/dma/virt-dma.c:112-121`），
而 `desc_free` 里通常有 `kfree`。持着 `vc.lock` 调用户回调是锁范围过大的经典错误。
mtk 的 `:434-438` 就是标准写法：**加锁只为摘链，释放在锁外**。

**mtk 的真实缺口**：它填了 `device_pause` 和 `device_terminate_all`
（`:515`/`:516`），但**没有填 `device_synchronize`**。
后果就是 §1.4 说的那条 —— 8250 这类消费者调
`dmaengine_terminate_sync(chan)` 时，`include/linux/dmaengine.h:1159`
的 `if (chan->device->device_synchronize)` 判假，
**同步那一步被静默跳过**。
mtk 用 `terminate_all` 里的 `synchronize_irq(c->irq)`（`:432`）挡了一半
（ISR 确实停了），但 tasklet `vchan_complete` 仍可能在跑。
**写新驱动时，只要用了 virt-dma，`device_synchronize = vchan_synchronize` 包装
就是一行的事，没有理由不填。** 登记在 `99-evidence.md` §6。

### §2.6 `remove` 的次序（与 probe 严格镜像）

```c
static int my_remove(struct platform_device *pdev)
{
	struct my_dmadev *d = platform_get_drvdata(pdev);

	of_dma_controller_free(pdev->dev.of_node);   /* 先断 DT 入口 */
	dma_async_device_unregister(&d->ddev);        /* 再摘核心链表 */
	pm_runtime_disable(&pdev->dev);
	return 0;
}
```

次序反了的窗口是真实的：`of_dma_controller_free`
（`drivers/dma/of-dma.c:153-167`）之前，消费者仍能通过 DT 拿到通道；
如果先 `dma_async_device_unregister`，就有"xlate 返回一个已注销设备上的通道"的窗口。
用 `dmaenginem_async_device_register`（`drivers/dma/dmaengine.c:1343-1362`）
可以让 unregister 自动化，但 `of_dma_controller_free` **没有** devm 版本，
仍要手写。

---

## §3 提供者 · 裸版：不用 virt-dma 时你要自己补什么

本树 147 个控制器 `.c` 里，43 个 include 了 `virt-dma.h`。
剩下的自己管队列。如果你的硬件有深队列/多描述符链，裸版可能更合适。

| virt-dma 帮你做的 | 裸版你要自己做 | 别漏 |
|---|---|---|
| 五个状态链表（`virt-dma.h:30-34`） | 自建链表或环形缓冲 | 至少要区分"已 submit 未 issue"和"已 issue" |
| tasklet 派发（`virt-dma.c:83-110`） | 自己 `tasklet_setup` 或用 threaded IRQ | **绝不能在硬中断里直接调消费者回调** |
| `dmaengine_desc_callback_invoke` 的封装 | 直接调 `dmaengine_desc_get_callback_invoke(&tx, &result)`（`drivers/dma/dmaengine.h:159`） | 要处理 `callback` 和 `callback_result` 两种 |
| cookie 记账（`dma_cookie_assign` / `_complete`） | 自己在 `tx_submit` 里 `dma_cookie_assign`（`drivers/dma/dmaengine.h:29-45`） | 这两个函数的 kerneldoc 都写了"必须持通道锁"（`:27`/`:50`） |
| `vchan_synchronize` | 自己写 `device_synchronize` | **裸版驱动漏这个的概率最高**，见 §1.4 |
| 描述符复用（`dmaengine_desc_test_reuse`，`virt-dma.h:116`） | 自己判 `DMA_CTRL_REUSE` | 不支持就在 prep 里拒掉带该 flag 的请求 |

**裸版最容易出的一个 bug**：把 `dma_cookie_complete` 放在锁外。
它内部有 `BUG_ON(tx->cookie <= 0)`（`drivers/dma/dmaengine.h:54`），
并发下 cookie 被改写就会直接 `BUG()`。

本篇不给裸版完整骨架 —— 本树没有足够短的样本可抄。
建议读 `drivers/dma/pl330.c`（arm64 上最常见的裸版控制器）的
`pl330_tasklet`（`:2069`）和 `pl330_tx_submit`（`:2507`），
后者挂在 `desc->txd.tx_submit`（`:2543`）上。

---

## §4 router 驱动：mux / crossbar 怎么接进来

取材 `drivers/dma/stm32-dmamux.c`（403 行）。
**前置提醒**：本树没有任何 arm64 平台用 dma-router（普查见
`04-extensions.md` §3.4）。这一节写给"手上有 mux 硬件、要新写驱动"的人，
以及想读懂 `of_dma_router_xlate` 那条路径的人。

### §4.1 你只需要实现两个函数

```c
/* 私有结构：dma_router 必须内嵌（stm32-dmamux.c:35-36） */
struct my_mux_data {
	struct dma_router dmarouter;
	void __iomem *iomem;
	spinlock_t lock;
	DECLARE_BITMAP(inuse, MY_MAX_REQ);
};

/* ①占用：核心在 of_dma_router_xlate 里调（抄自 stm32-dmamux.c:84-169） */
static void *my_route_allocate(struct of_phandle_args *dma_spec,
			       struct of_dma *ofdma)
{
	struct platform_device *pdev = of_find_device_by_node(ofdma->of_node);
	struct my_mux_data *mux_data = platform_get_drvdata(pdev);
	struct my_mux *mux;
	unsigned long flags;
	int ret;

	/* a. 参数校验 —— 必须做，DTS 写错在这里才拦得住 */
	if (dma_spec->args_count != 3)                        /* :94 */
		return ERR_PTR(-EINVAL);                      /* :96 */
	if (dma_spec->args[0] > mux_data->nr_requests) {      /* :99 */
		dev_err(&pdev->dev, "invalid mux request %d\n",
			dma_spec->args[0]);                   /* :100-101 */
		return ERR_PTR(-EINVAL);                      /* :102 */
	}

	mux = kzalloc(sizeof(*mux), GFP_KERNEL);              /* :105 KERNEL 可以，
							       * 这条路径能睡 */
	if (!mux)
		return ERR_PTR(-ENOMEM);                      /* :107 */

	/* b. 占一个 mux 通道 */
	spin_lock_irqsave(&mux_data->lock, flags);            /* :109 */
	mux->chan_id = find_first_zero_bit(mux_data->inuse,
					   mux_data->nr_chans);   /* :110-111 */
	if (mux->chan_id == mux_data->nr_chans) {             /* :113 */
		spin_unlock_irqrestore(&mux_data->lock, flags);
		dev_err(&pdev->dev, "Run out of free DMA requests\n");  /* :115 */
		ret = -ENOMEM;                                /* :116 */
		goto error_chan_id;
	}
	set_bit(mux->chan_id, mux_data->inuse);               /* :119 */
	spin_unlock_irqrestore(&mux_data->lock, flags);       /* :120 */

	/* c. 把 dma_spec 改写成"真控制器认识的样子" */
	dma_spec->np = of_parse_phandle(ofdma->of_node,
					"dma-masters", i - 1); /* :131 */
	if (!dma_spec->np) {
		ret = -EINVAL;
		goto error;                                   /* :134-135 */
	}
	dma_spec->args[3]   = dma_spec->args[2] | mux->chan_id << 16;  /* :150 */
	dma_spec->args[2]   = dma_spec->args[1];              /* :151 */
	dma_spec->args[1]   = 0;                             /* :152 */
	dma_spec->args[0]   = mux->chan_id - min;            /* :153 */
	dma_spec->args_count = 4;                            /* :154 个数也要改 */

	/* d. 真的写寄存器 */
	my_mux_write(mux_data, MY_CCR(mux->chan_id), mux->request);  /* :156-157 */

	return mux;   /* :161 返回值会被核心存进 chan->route_data */
error:
	clear_bit(mux->chan_id, mux_data->inuse);             /* :164 */
error_chan_id:
	kfree(mux);
	return ERR_PTR(ret);
}

/* ②归还：核心在 dma_chan_put 里调（抄自 stm32-dmamux.c:62-82） */
static void my_route_free(struct device *dev, void *route_data)
{
	struct my_mux_data *mux_data = dev_get_drvdata(dev);  /* :64 */
	struct my_mux *mux = route_data;                      /* :65 */
	unsigned long flags;

	spin_lock_irqsave(&mux_data->lock, flags);            /* :69 */
	my_mux_write(mux_data, MY_CCR(mux->chan_id), 0);      /* :71 关掉路由 */
	clear_bit(mux->chan_id, mux_data->inuse);             /* :72 */
	pm_runtime_put_sync(dev);                             /* :74 */
	spin_unlock_irqrestore(&mux_data->lock, flags);       /* :76 */

	kfree(mux);                                           /* :81 allocate 里 kzalloc 的 */
}

/* ③注册（抄自 stm32-dmamux.c:271-288） */
	mux_data->dmarouter.dev        = &pdev->dev;          /* :271 */
	mux_data->dmarouter.route_free = my_route_free;       /* :272 */
	platform_set_drvdata(pdev, mux_data);                 /* :274 route_free 靠它找回自己 */
	...
	ret = of_dma_router_register(node, my_route_allocate,
				     &mux_data->dmarouter);   /* :286-287 */
```

**router 的四个必须注意**：

| 点 | 说明 |
|---|---|
| **`dma_spec` 是核心给你的一份副本，可以随便改** | 核心在 `of_dma_router_xlate` 里先 `memcpy` 了一份（`drivers/dma/of-dma.c:64`），你改的是 `dma_spec_target`，原始的没动 |
| **`of_node_put` 不要自己调** | 你 `of_parse_phandle` 拿到的 `dma_spec->np`，由核心在 `of-dma.c:101` 统一 put。stm32 在 `:130` 专门写了注释提醒 |
| **`platform_set_drvdata` 必须在注册之前** | `route_free` 的唯一入参是 `struct device *`，只能靠 `dev_get_drvdata` 找回私有结构 |
| **返回 `ERR_PTR(x)` 的 `x` 会被丢掉** | `of_dma_router_xlate:66-67` 一律转成 `NULL` → 消费者看到 `-EPROBE_DEFER`。所以**你必须自己 `dev_err`**，否则用户完全无法定位。这是 stm32 在 `:100`/`:115` 都打 `dev_err` 的原因 |

**没有 `remove` 骨架**：`of_dma_controller_free`
（`drivers/dma/of-dma.c:153-167`）对 router 和普通控制器是同一个函数，
router 驱动的 `remove` 里照常调它。

---

## §5 用 dmatest 验收你的新驱动

本节只给"最短可用序列"，参数全表、输出解读、失败分诊在 `07-debug.md`。

```bash
# 1. 编译进内核或 modprobe
modprobe dmatest

# 2. 选通道（名字来自 /sys/class/dma/ 下的目录名）
echo dma0chan0 > /sys/module/dmatest/parameters/channel

# 3. 设参数
echo 1000 > /sys/module/dmatest/parameters/iterations
echo 1    > /sys/module/dmatest/parameters/timeout   # 秒，0=不超时

# 4. run 是"动作"不是"标志"：写 1 立刻开跑
echo 1 > /sys/module/dmatest/parameters/run

# 5. 看结果
dmesg | tail -30
cat /sys/module/dmatest/parameters/test_list
```

**dmatest 只能测 memcpy / xor / pq，测不到 slave 传输**。
原因是它用 `dma_request_channel(mask, filter, params)`
（`drivers/dma/dmatest.c:1077`）走 percpu 表路径，
而 `DMA_SLAVE` 被 `dma_channel_table_init` 的三个 `clear_bit`
（`drivers/dma/dmaengine.c:273-275`）排除在 percpu 表之外。

| 你的驱动是 | dmatest 能验收吗 | 替代做法 |
|---|---|---|
| 有 `device_prep_dma_memcpy` 的通用引擎 | ✅ 全套都能测 | — |
| 只有 `device_prep_slave_sg` 的 slave 控制器（如 §2 的 mtk） | ❌ 拿不到通道 | 用真实外设驱动测；或临时加一个 `DMA_MEMCPY` 能力做 bring-up |
| RAID 引擎（`DMA_XOR` / `DMA_PQ`） | ✅ 需 `CONFIG_DMA_ENGINE_RAID`（`drivers/dma/Kconfig:806-807`） | — |

一个易踩的坑：**`DMATEST` 编进内核（`=y`）时入口是
`late_initcall(dmatest_init)`（`drivers/dma/dmatest.c:1347`）而不是
`module_init`** ——注释在 `:1346`。所以它一定在控制器驱动之后跑；
但如果你的控制器是 `module_init` 的模块，dmatest 启动时通道还不存在，
必须之后手动 `echo 1 > run` 一次。

---

## §6 五个角色的自查清单

写完之后逐条对一遍，比读一百行文档有用：

### §6.1 消费者（§1）

- [ ] `dma_request_chan` 的返回值用 `IS_ERR` 判，`-EPROBE_DEFER` 原样返回
- [ ] `dma_map_*` 的第一个参数是 `chan->device->dev`，**不是**自己的 `dev`
- [ ] 每次改传输方向都重发一次 `dmaengine_slave_config`
- [ ] 需要回调就带 `DMA_PREP_INTERRUPT`
- [ ] `desc->callback` 在 `dmaengine_submit` **之前**赋值
- [ ] 回调里没有任何可能睡眠的调用（含 `terminate_sync` / `mutex_lock` / `GFP_KERNEL`）
- [ ] 拆除次序是 `terminate_sync` → `unmap/free` → `release_channel`
- [ ] 确认过控制器实现了 `device_synchronize`，否则自己补同步

### §6.2 virt-dma 提供者（§2）

- [ ] `INIT_LIST_HEAD(&ddev.channels)` 在所有 `vchan_init` 之前
- [ ] 每个通道都 `vchan_init` 了，且没有自己再 `list_add`
- [ ] `src_addr_widths` / `dst_addr_widths` / `directions` 全用 `BIT()` 包
- [ ] `prep_*` 失败返回 `NULL`（不是 `ERR_PTR`），分配用 `GFP_NOWAIT`
- [ ] `issue_pending` 全程持 `vc.lock`
- [ ] ISR 里先 `list_del` 再 `vchan_cookie_complete`
- [ ] `vchan_dma_desc_free_list` 在 `vc.lock` **之外**调
- [ ] 填了 `device_synchronize = vchan_synchronize` 包装
- [ ] `free_chan_resources` 里 `free_irq` → `tasklet_kill` → `vchan_free_chan_resources`
- [ ] register 次序：`dma_async_device_register` → `of_dma_controller_register`
- [ ] `remove` 次序：`of_dma_controller_free` → `dma_async_device_unregister`
- [ ] `of_dma_controller_register` 第三参传的是 `&d->ddev`（见 §2.2）

### §6.3 裸版提供者（§3）

- [ ] 上面 virt-dma 那一列里"核心相关"的项全部照做
- [ ] `dma_cookie_assign` / `dma_cookie_complete` 都在通道锁内
- [ ] 消费者回调只在 tasklet / threaded IRQ 里调，不在硬中断里
- [ ] 用 `dmaengine_desc_get_callback_invoke` 而不是自己判 `callback` 字段
- [ ] 实现了 `device_synchronize`

### §6.4 router（§4）

- [ ] 参数个数和范围都校验了，且失败时**自己打了 `dev_err`**
- [ ] 没有自己调 `of_node_put(dma_spec->np)`
- [ ] `platform_set_drvdata` 在 `of_dma_router_register` 之前
- [ ] `route_allocate` 里 `kzalloc` 的东西，`route_free` 里 `kfree` 了
- [ ] `args_count` 改写后的值和真控制器的 `#dma-cells` 一致

### §6.5 验收（§5）

- [ ] 能 memcpy 的引擎跑过 dmatest（`iterations` ≥ 1000，`noverify=0`）
- [ ] slave 控制器用真实外设跑过收发双向 + 中途 terminate
- [ ] 跑过一次"消费者 rmmod 时正好有传输在飞"的场景
- [ ] `cat /sys/kernel/debug/dmaengine/summary` 里通道状态正常
  （前提是 `CONFIG_DEBUG_FS`，见 `07-debug.md`）

---

## §7 阅读下一篇

| 你接着想知道 | 去 |
|---|---|
| 本篇用到的每个 API 的完整签名和错误码 | `01-api.md` |
| `dma_device` / `virt_dma_chan` 每个字段的含义 | `02-datastructures.md` |
| 为什么次序必须是这样（核心内部在做什么） | `03-internals.md` |
| virt-dma / router / metadata 的机制细节 | `04-extensions.md` |
| DTS 怎么写、`dma-names` 和 `dmas` 怎么对应 | `06-devicetree.md` |
| dmatest 参数全表、debugfs、错误码分诊 | `07-debug.md` |
| "虚拟通道""cookie""为什么要 issue_pending"的大白话解释 | `08-feynman.md` |
| 本篇每条论断的出处、哪些没验证 | `99-evidence.md` |
