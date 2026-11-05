---
title: 使用指南
---

# 网卡驱动代码骨架

## §0 本篇怎么读

### §0.1 先说清楚：本篇所有代码都没有编译过

本篇的每一段 C 代码都是**按本树 6.1.158 的真实原型手写的骨架**，用途是「让你知道该填哪些槽、按什么顺序填」，不是可以直接 `make` 的模块。具体地：

- 没有编译过，没有加载过，没有跑过流量。
- 寄存器读写一律写成 `xxx_wr32()` / `xxx_rd32()` 这种占位符——真实的偏移只有你手上的芯片手册知道。
- 错误处理为了让主线清楚而做了压缩，真实驱动的 `goto` 标签链会更长。
- 每个符号的**存在性**都在本树 grep 确认过（判据见 §8），但「符号存在」不等于「这样组合起来能跑」。

### §0.2 角色枚举：写网卡相关代码的六种身份

| 角色 | 你在写什么 | 落在哪个目录 | 本篇哪一节 | 大致代码量 |
|------|-----------|-------------|-----------|-----------|
| A | SoC 内置以太网 MAC 驱动 | `drivers/net/ethernet/<vendor>/` | §1 | 2000–6000 行 |
| B | PCIe 网卡驱动 | 同上 | §2（只列与 A 的差异） | 与 A 同量级 |
| C | MDIO 总线驱动 | `drivers/net/mdio/` 或并进 MAC 驱动 | §3 | 100–300 行 |
| D | PHY 驱动 | `drivers/net/phy/` | §4 | 100–800 行 |
| E | 给已有驱动补 ethtool 能力 | 已有驱动的 `*_ethtool.c` | §5 | 50–500 行 |
| F | 给已有驱动做增量改造（BQL / page_pool / GRO） | 已有驱动的收发路径 | §6 | 每项 10–100 行 |

arm64 上最常见的是 **A + C + E**：SoC 网卡通常自带一条 MDIO 总线，PHY 一般能被 `drivers/net/phy/` 里现成的驱动或 `genphy_*` 通用驱动接管，所以角色 D 往往不用自己写。

### §0.3 按任务找章节

| 你要做的事 | 去哪一节 |
|-----------|---------|
| 从零起一个 SoC 网卡驱动 | §1 全节，按 §1.2 的七步走 |
| 手上有 PCIe 网卡的 datasheet | 先读 §1，再读 §2 的差异表 |
| MAC 里有一组 MDIO 寄存器要暴露出去 | §3 |
| `ethtool -S` 想多出几行计数 | §5.3 |
| 想让 `ping` 的延迟不再抖 | §6.2（BQL） |
| 名字太像分不清该用哪个 | §7 |
| 想确认某个函数在 6.1 里到底能不能用 | §8 |

---

## §1 角色 A：SoC 内置以太网 MAC 驱动

### §1.1 文件与 Kconfig 骨架

一个中等规模的 SoC 网卡驱动通常拆成三到四个文件。以本树 `drivers/net/ethernet/ti/am65-cpsw-nuss.c`（2893 行）为参照，最小拆法是：

```
drivers/net/ethernet/acme/
├── Kconfig
├── Makefile
├── acme_eth.h        寄存器偏移、私有结构体
├── acme_eth_main.c   probe/remove、net_device_ops、NAPI、收发
└── acme_eth_ethtool.c ethtool_ops
```

`Kconfig` 里要挂到本树 `drivers/net/ethernet/Kconfig`（196 行，其中 10 个 `^config`）已有的 `source` 链上：

```
config ACME_ETH
	tristate "Acme SoC Ethernet support"
	depends on OF && HAS_DMA
	select PHYLINK
	select MDIO_DEVICE
	help
	  Driver for the Ethernet MAC found on Acme SoCs.
```

`select PHYLINK` 不是可选项——见 §1.2 第 5 步。

### §1.2 probe 的七步

顺序是有讲究的：**先把 `net_device` 和私有区拿到手，最后一步才 `register_netdev()`**。理由在 03 §A4：`register_netdev()` 一返回，用户态就可能立刻 `ip link set up`，此时 `ndo_open` 会被调用，所以注册之前所有 `ndo_*` 依赖的资源必须已经就绪。

```c
static int acme_eth_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct net_device *ndev;
	struct acme_priv *priv;
	int ret;

	/* 1. 分配 net_device + 私有区（一次分配，priv 紧跟在后面） */
	ndev = devm_alloc_etherdev_mqs(dev, sizeof(*priv), ACME_NUM_TXQ,
				       ACME_NUM_RXQ);
	if (!ndev)
		return -ENOMEM;
	priv = netdev_priv(ndev);
	priv->ndev = ndev;
	priv->dev  = dev;
	SET_NETDEV_DEV(ndev, dev);
	platform_set_drvdata(pdev, ndev);

	/* 2. 硬件资源：寄存器、时钟、中断 */
	priv->regs = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(priv->regs))
		return PTR_ERR(priv->regs);

	priv->irq = platform_get_irq(pdev, 0);
	if (priv->irq < 0)
		return priv->irq;

	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(40));
	if (ret)
		return dev_err_probe(dev, ret, "no usable DMA mask\n");

	/* 3. MAC 地址：DT → 平台固件 → 随机，三级回退 */
	ret = of_get_ethdev_address(dev->of_node, ndev);
	if (ret == -EPROBE_DEFER)
		return ret;
	if (ret) {
		u8 mac[ETH_ALEN];

		if (!eth_platform_get_mac_address(dev, mac))
			eth_hw_addr_set(ndev, mac);
		else
			eth_hw_addr_random(ndev);
	}

	/* 4. 填 net_device 的字段和 ops */
	ndev->netdev_ops   = &acme_netdev_ops;
	ndev->ethtool_ops  = &acme_ethtool_ops;
	ndev->min_mtu      = ETH_MIN_MTU;
	ndev->max_mtu      = ACME_MAX_MTU;
	ndev->watchdog_timeo = 5 * HZ;
	ndev->needed_headroom = ACME_TX_HDR_LEN;
	ndev->hw_features  = NETIF_F_SG | NETIF_F_IP_CSUM | NETIF_F_RXCSUM;
	ndev->features     = ndev->hw_features;

	/* 5. phylink：建，但不启 */
	priv->phylink_config.dev  = dev;
	priv->phylink_config.type = PHYLINK_NETDEV;
	priv->phylink_config.mac_capabilities =
		MAC_10FD | MAC_100FD | MAC_1000FD;
	__set_bit(PHY_INTERFACE_MODE_RGMII_ID,
		  priv->phylink_config.supported_interfaces);

	priv->phylink = phylink_create(&priv->phylink_config,
				       of_fwnode_handle(dev->of_node),
				       PHY_INTERFACE_MODE_RGMII_ID,
				       &acme_phylink_ops);
	if (IS_ERR(priv->phylink))
		return PTR_ERR(priv->phylink);

	/* 6. NAPI：注册，但不 enable */
	netif_napi_add(ndev, &priv->rx_napi, acme_rx_poll);
	netif_napi_add_tx(ndev, &priv->tx_napi, acme_tx_poll);

	/* 7. 最后一步：注册 */
	ret = register_netdev(ndev);
	if (ret) {
		phylink_destroy(priv->phylink);
		return ret;
	}
	return 0;
}
```

对应的头文件位置：`devm_alloc_etherdev_mqs()` 在 `include/linux/etherdevice.h:61`（单队列的简写宏 `devm_alloc_etherdev()` 在 `:64`）；`SET_NETDEV_DEV()` 是宏，展开成一句 `dev.parent` 赋值，在 `include/linux/netdevice.h:2567`；`register_netdev()` 在 `include/linux/netdevice.h:4540`。

第 4 步填的字段依次是 `netdev_ops`（`include/linux/netdevice.h:2064`）、`ethtool_ops`（`:2106`）、`min_mtu` / `max_mtu`（`:2086` / `:2087`）、`watchdog_timeo`（`:2258`）、`needed_headroom`（`:2075`）、`hw_features` / `features`（`:2079` / `:2078`）。

⚠️ **第 6 步的 `netif_napi_add()` 在 6.1 里是三个参数，没有 weight**。它是 `include/linux/netdevice.h:2592-2597` 的一个 inline 包装，内部用 `NAPI_POLL_WEIGHT`（`:2578`，值 64）去调真正的 `netif_napi_add_weight()`（`:2580`）。从更老的内核抄代码过来会带上第四个参数，编译直接报参数个数不对。只发送方向的 NAPI 用 `netif_napi_add_tx()`（`:2619`），它多做一件事：置上 `NAPI_STATE_NO_BUSY_POLL`，免得污染 busy-poll 的哈希表。

⚠️ **第 7 步失败后不能只 `return ret`**。第 5、6 两步申请的东西不受 `devm` 管：`phylink_create()` 要配 `phylink_destroy()`，`netif_napi_add()` 要配 `netif_napi_del()`。上面的骨架只拆了 phylink，是因为 `free_netdev()` 会顺带清理挂在 `dev->napi_list` 上的 NAPI——但如果你用的是裸 `alloc_etherdev_mqs()` 加手工 `free_netdev()`，这条依赖必须自己数清楚。

### §1.3 net_device_ops 的最小集

```c
static const struct net_device_ops acme_netdev_ops = {
	.ndo_open		= acme_open,
	.ndo_stop		= acme_stop,
	.ndo_start_xmit		= acme_start_xmit,
	.ndo_get_stats64	= acme_get_stats64,
	.ndo_set_rx_mode	= acme_set_rx_mode,
	.ndo_set_mac_address	= eth_mac_addr,
	.ndo_validate_addr	= eth_validate_addr,
	.ndo_change_mtu		= acme_change_mtu,
	.ndo_tx_timeout		= acme_tx_timeout,
	.ndo_eth_ioctl		= phy_do_ioctl_running,
};
```

槽位在 `include/linux/netdevice.h` 里的位置：`ndo_open` `:1403`、`ndo_stop` `:1404`、`ndo_start_xmit` `:1405`、`ndo_set_rx_mode` `:1415`、`ndo_set_mac_address` `:1416`、`ndo_validate_addr` `:1418`、`ndo_eth_ioctl` `:1421`、`ndo_change_mtu` `:1432`、`ndo_tx_timeout` `:1436`、`ndo_get_stats64` `:1439`。整个结构体一共 86 个函数指针成员（口径见 01 §0），你填 10 个就能跑。

三个可以直接用核心现成实现、不必自己写的槽：`eth_mac_addr`、`eth_validate_addr`（都在 `net/ethernet/eth.c`）、`phy_do_ioctl_running`（`drivers/net/phy/phy.c`）。⚠️ **`ndo_do_ioctl`（`:1419`）在 6.1 里已经不再承载 PHY 的 MII ioctl**，新驱动填错这个槽会让 `mii-tool` 直接失效而不报错——正确的槽是 `ndo_eth_ioctl`。

### §1.4 open 与 stop：成对的两半

```c
static int acme_open(struct net_device *ndev)
{
	struct acme_priv *priv = netdev_priv(ndev);
	int ret;

	ret = acme_alloc_rings(priv);		/* DMA 描述符环 */
	if (ret)
		return ret;

	ret = request_irq(priv->irq, acme_isr, 0, ndev->name, priv);
	if (ret)
		goto err_free_rings;

	acme_hw_init(priv);

	napi_enable(&priv->rx_napi);
	napi_enable(&priv->tx_napi);

	phylink_start(priv->phylink);		/* 这一步才开始跟 PHY 说话 */

	netif_tx_start_all_queues(ndev);
	return 0;

err_free_rings:
	acme_free_rings(priv);
	return ret;
}

static int acme_stop(struct net_device *ndev)
{
	struct acme_priv *priv = netdev_priv(ndev);

	netif_tx_stop_all_queues(ndev);
	phylink_stop(priv->phylink);

	napi_disable(&priv->tx_napi);
	napi_disable(&priv->rx_napi);

	acme_hw_stop(priv);
	free_irq(priv->irq, priv);
	acme_free_rings(priv);
	return 0;
}
```

位置：`napi_enable()` `include/linux/netdevice.h:533`、`napi_disable()` `:531`、`netif_tx_start_all_queues()` `:3275`、`netif_tx_stop_all_queues()` `:3333`；`phylink_start()` 声明在 `include/linux/phylink.h:580`、实现在 `drivers/net/phy/phylink.c:1889`，`phylink_stop()` 声明 `:581`、实现 `drivers/net/phy/phylink.c:1961`。

⚠️ **`stop` 的顺序不是 `open` 的简单倒序**。`netif_tx_stop_all_queues()` 必须在 `napi_disable()` 之前——先断掉新包的来源，再关轮询；顺序反了会出现「NAPI 已停、协议栈还在往 `ndo_start_xmit` 灌包」的窗口。同理 `free_irq()` 必须在 `napi_disable()` 之后，否则中断处理里 `napi_schedule()` 排上的那次 poll 可能落在环已经释放之后。

### §1.5 发送路径

```c
static netdev_tx_t acme_start_xmit(struct sk_buff *skb,
				   struct net_device *ndev)
{
	struct acme_priv *priv = netdev_priv(ndev);
	u16 qid = skb_get_queue_mapping(skb);
	struct acme_txq *txq = &priv->txq[qid];
	struct netdev_queue *nq = netdev_get_tx_queue(ndev, qid);
	dma_addr_t dma;

	if (acme_txq_avail(txq) < 1) {
		netif_tx_stop_queue(nq);
		return NETDEV_TX_BUSY;
	}

	dma = dma_map_single(priv->dev, skb->data, skb->len, DMA_TO_DEVICE);
	if (dma_mapping_error(priv->dev, dma))
		goto drop;

	acme_txq_fill_desc(txq, dma, skb);
	txq->skb[txq->head] = skb;

	skb_tx_timestamp(skb);			/* 软件时间戳，放在踢门铃之前 */
	netdev_tx_sent_queue(nq, skb->len);	/* BQL 记账，见 §6.2 */

	if (acme_txq_avail(txq) < MAX_SKB_FRAGS + 1)
		netif_tx_stop_queue(nq);

	acme_wr32(priv, ACME_TX_DOORBELL(qid), txq->head);
	return NETDEV_TX_OK;

drop:
	dev_kfree_skb_any(skb);
	ndev->stats.tx_dropped++;
	return NETDEV_TX_OK;
}
```

位置：`netif_tx_stop_queue()` `include/linux/netdevice.h:3309-3319`、`skb_tx_timestamp()` `include/linux/skbuff.h:4375`、`netdev_tx_sent_queue()` `include/linux/netdevice.h:3424-3452`、`dev_kfree_skb_any()` `include/linux/netdevice.h:3861`。

⚠️ **丢包时返回 `NETDEV_TX_OK` 而不是错误码**。`ndo_start_xmit` 的返回值只有两个有意义的取值：`NETDEV_TX_OK`（skb 的所有权已经转移给驱动，无论是发出去还是丢掉）和 `NETDEV_TX_BUSY`（skb 还给核心，核心会重新排队）。返回 `NETDEV_TX_BUSY` 时**不能碰 skb**，包括不能释放它。

⚠️ **停队列要在踢门铃之前判断，而且下限不是 1 而是 `MAX_SKB_FRAGS + 1`**。下一个包可能是分片的 skb，需要多个描述符；按 1 判断会在下一次进来时才发现空间不够，只能返回 `NETDEV_TX_BUSY`，那是一次白跑的软中断。

发送完成侧（在 §1.6 的 tx poll 里）要做的三件事是：解 DMA 映射、`napi_consume_skb()` 释放 skb、`netdev_tx_completed_queue()` 归还 BQL 记账，然后按 §6.2 的条件唤醒队列。

### §1.6 中断与 NAPI poll

中断处理函数只做两件事：关硬件中断、调度 NAPI。所有实际工作都推到 poll 里。

```c
static irqreturn_t acme_isr(int irq, void *data)
{
	struct acme_priv *priv = data;
	u32 status = acme_rd32(priv, ACME_INT_STATUS);

	if (!status)
		return IRQ_NONE;

	acme_wr32(priv, ACME_INT_MASK, ACME_INT_ALL);	/* 关中断 */
	acme_wr32(priv, ACME_INT_STATUS, status);	/* 清中断 */

	if (status & ACME_INT_RX)
		napi_schedule_irqoff(&priv->rx_napi);
	if (status & ACME_INT_TX)
		napi_schedule_irqoff(&priv->tx_napi);

	return IRQ_HANDLED;
}

static int acme_rx_poll(struct napi_struct *napi, int budget)
{
	struct acme_priv *priv = container_of(napi, struct acme_priv, rx_napi);
	int done = 0;

	while (done < budget) {
		struct acme_rx_desc *desc = acme_rxq_next(&priv->rxq);
		struct sk_buff *skb;
		unsigned int len;

		if (!acme_desc_done(desc))
			break;

		len = acme_desc_len(desc);
		dma_unmap_single(priv->dev, desc->dma, ACME_RX_BUF_SZ,
				 DMA_FROM_DEVICE);

		skb = napi_alloc_skb(napi, len);
		if (!skb) {
			priv->rxq.alloc_fail++;
			break;			/* 别 return，让下面走完统计 */
		}
		skb_put_data(skb, desc->buf, len);
		skb->protocol = eth_type_trans(skb, priv->ndev);

		u64_stats_update_begin(&priv->rx_stats.syncp);
		u64_stats_add(&priv->rx_stats.packets, 1);
		u64_stats_add(&priv->rx_stats.bytes, len);
		u64_stats_update_end(&priv->rx_stats.syncp);

		napi_gro_receive(napi, skb);	/* 不是 netif_receive_skb */
		acme_rxq_refill_one(&priv->rxq);
		done++;
	}

	if (done < budget && napi_complete_done(napi, done))
		acme_wr32(priv, ACME_INT_MASK, 0);	/* 只有这里才开中断 */

	return done;
}
```

位置：`napi_schedule_irqoff()` `include/linux/netdevice.h:492`（可以在已关中断的上下文里省一次判断，普通版本是 `:480` 的 `napi_schedule()`）、`napi_alloc_skb()` `include/linux/skbuff.h:3283`、`eth_type_trans()` `include/linux/etherdevice.h:40`、`napi_gro_receive()` 实现在 `net/core/gro.c:649`、`napi_complete_done()` 声明在 `include/linux/netdevice.h:508`。

⚠️ **`napi_complete_done()` 的返回值不能忽略**。它返回 `false` 的意思是「不要开硬件中断」——因为核心决定继续轮询（`gro_flush_timeout` / `napi_defer_hard_irqs` 这两个旋钮生效时，见 04 §1.3），或者这个 NAPI 正在被 busy-poll 接管。把开中断的那一行写在 `if` 外面，就等于把这两个特性废掉了。

⚠️ **`done == budget` 时绝对不能调 `napi_complete_done()`**。收满预算意味着可能还有活，必须原样 `return budget` 让核心再排一次 poll。上面骨架用 `done < budget` 这个条件把两种情况分开，这是 NAPI 契约里最容易写错的一处（03 §A8.4 有核心侧的对应逻辑）。

⚠️ **分配 skb 失败时要 `break` 而不是 `return`**。`return` 会跳过 `napi_complete_done()`，NAPI 就永远停在「已调度、未完成」的状态，硬件中断也永远不会被重新打开——表现是接口收了几个包之后彻底静默，`ethtool -S` 的计数不再变化。

发送完成侧的 poll 长这样，注意唤醒队列的条件：

```c
static int acme_tx_poll(struct napi_struct *napi, int budget)
{
	struct acme_priv *priv = container_of(napi, struct acme_priv, tx_napi);
	struct netdev_queue *nq = netdev_get_tx_queue(priv->ndev, 0);
	unsigned int pkts = 0, bytes = 0;

	while (pkts < budget && acme_txq_has_completed(&priv->txq[0])) {
		struct sk_buff *skb = acme_txq_pop(&priv->txq[0]);

		dma_unmap_single(priv->dev, ..., DMA_TO_DEVICE);
		bytes += skb->len;
		pkts++;
		napi_consume_skb(skb, budget);	/* 不是 dev_kfree_skb_any */
	}

	netdev_tx_completed_queue(nq, pkts, bytes);

	if (netif_tx_queue_stopped(nq) &&
	    acme_txq_avail(&priv->txq[0]) > MAX_SKB_FRAGS + 1)
		netif_tx_wake_queue(nq);

	if (pkts < budget)
		napi_complete_done(napi, pkts);

	return pkts;
}
```

位置：`netdev_get_tx_queue()` `include/linux/netdevice.h:2443`、`napi_consume_skb()` `include/linux/skbuff.h:3288`、`netdev_tx_completed_queue()` `include/linux/netdevice.h:3506-3528`、`netif_tx_queue_stopped()` `:3335`、`netif_tx_wake_queue()` `:3285`。

### §1.7 统计：`ndo_get_stats64` 与本树补丁的那个坑

```c
static void acme_get_stats64(struct net_device *ndev,
			     struct rtnl_link_stats64 *s)
{
	struct acme_priv *priv = netdev_priv(ndev);
	unsigned int start;

	do {
		start = u64_stats_fetch_begin(&priv->rx_stats.syncp);
		s->rx_packets = u64_stats_read(&priv->rx_stats.packets);
		s->rx_bytes   = u64_stats_read(&priv->rx_stats.bytes);
	} while (u64_stats_fetch_retry(&priv->rx_stats.syncp, start));

	s->rx_dropped = ndev->stats.rx_dropped;
}
```

⚠️ **本树把 `u64_stats_fetch_begin_irq()` / `u64_stats_fetch_retry_irq()` 这一对整块删掉了**。删掉的是 `include/linux/u64_stats_sync.h` 里那个 `/* Obsolete interfaces */` 块，共 12 行（PREEMPT_RT 回合的一部分，见 01 §0.6）。你从 6.1 之前的驱动、或者从网上任何一篇 6.0 时代的教程里抄读统计的代码，都会带上 `_irq` 后缀，而它在本树**编译不过去**。现存的正确名字是 `u64_stats_fetch_begin()`（`include/linux/u64_stats_sync.h:206`）和 `u64_stats_fetch_retry()`（`:211`）。本树 `drivers/net/ethernet/` 下 47 个文件被这次改名扫过，改动是 204 行插入 / 204 行删除，纯机械替换。

写侧用 `u64_stats_update_begin()`（`:181`）/ `u64_stats_update_end()`（`:186`）。如果写侧可能在硬中断里跑，用 `u64_stats_update_begin_irqsave()`（`:191`）/ `u64_stats_update_end_irqrestore()`（`:199`）这一对。

### §1.8 remove：严格倒序

```c
static int acme_eth_remove(struct platform_device *pdev)
{
	struct net_device *ndev = platform_get_drvdata(pdev);
	struct acme_priv *priv = netdev_priv(ndev);

	unregister_netdev(ndev);	/* 内部会先把接口 down 掉 */
	phylink_destroy(priv->phylink);
	return 0;
}
```

⚠️ **`unregister_netdev()` 必须是第一步**。它内部会先走 `dev_close()` 把接口关掉（03 §A9），也就是说 `ndo_stop` 会在这一行里被同步调用。任何在它之前就被释放的资源，`ndo_stop` 都会踩到已释放内存。位置：`unregister_netdev()` `include/linux/netdevice.h:4541`，`phylink_destroy()` 实现在 `drivers/net/phy/phylink.c:1545`。

⚠️ **用了 `devm_alloc_etherdev_mqs()` 就不要再调 `free_netdev()`**。`devm` 版本已经把释放挂到设备的 devres 链上，手工再释放一次是 double free。用裸 `alloc_etherdev_mqs()`（`include/linux/etherdevice.h:56`）的驱动才需要在 `unregister_netdev()` 之后手工 `free_netdev()`（`include/linux/netdevice.h:3079`）。

---

## §2 角色 B：PCIe 网卡驱动——只列与角色 A 的差异

收发路径、NAPI、ethtool、BQL 这些全部和 §1 一模一样。真正不同的只有 probe/remove 的外壳和几处约束：

| 环节 | 角色 A（platform） | 角色 B（PCIe） |
|------|-------------------|---------------|
| 驱动骨架 | `platform_driver` + `of_match_table` | `pci_driver` + `pci_device_id` 表 |
| 使能设备 | 时钟 `devm_clk_get` + `clk_prepare_enable` | `pcim_enable_device()` + `pci_set_master()` |
| 映射寄存器 | `devm_platform_ioremap_resource(pdev, 0)` | `pcim_iomap_regions()` 或 `pci_iomap()` |
| 中断 | `platform_get_irq()` + `request_irq()` | `pci_alloc_irq_vectors()` + 每向量 `request_irq()` |
| MAC 地址来源 | DT 的 `local-mac-address` / `mac-address` | 芯片上的 EEPROM / OTP，DT 通常没有 |
| PHY | DT 里的 `phy-handle`，走 phylink | 多数集成在芯片内，可能完全不用 phylink |
| DMA 位宽 | `dma_set_mask_and_coherent(dev, DMA_BIT_MASK(40))` 之类 | 同函数，但常见 64 位；失败要回退 32 位 |
| 电源管理 | `dev_pm_ops` + runtime PM | 同上，另加 PCI 的 D 状态 |

⚠️ **PCIe 驱动的 `SET_NETDEV_DEV()` 参数是 `&pdev->dev`（PCI 设备），不是别的**。这一行决定了 `/sys/class/net/eth0/device` 指向哪里，也决定了 DMA 操作用谁的 `dma_ops`。写错会让 IOMMU 侧的映射挂在错误的设备上——arm64 上 SMMU 一开就是立刻的翻译错误。

本树 arm64 上 PCIe 网卡的常见形态是插卡（Intel / Mellanox / Broadcom），代码量都在几万行，不适合当学习范本。§0.2 里把角色 B 标成「与 A 同量级」，指的是**你自己写的部分**和 A 差不多，不是指要读完那些驱动。

---

## §3 角色 C：MDIO 总线驱动

SoC 的 MAC 里通常有一组「往 MDIO 线上打一次读/写事务」的寄存器。把它包成一条 `mii_bus`，PHY 层就能在上面枚举出 PHY 设备。

```c
static int acme_mdio_read(struct mii_bus *bus, int addr, int regnum)
{
	struct acme_priv *priv = bus->priv;
	u32 val;
	int ret;

	acme_wr32(priv, ACME_MDIO_CMD,
		  ACME_MDIO_GO | ACME_MDIO_OP_RD |
		  FIELD_PREP(ACME_MDIO_PHYAD, addr) |
		  FIELD_PREP(ACME_MDIO_REGAD, regnum));

	ret = readl_poll_timeout(priv->regs + ACME_MDIO_CMD, val,
				 !(val & ACME_MDIO_GO), 10, 10000);
	if (ret)
		return ret;

	return acme_rd32(priv, ACME_MDIO_DATA) & 0xffff;
}

static int acme_mdio_write(struct mii_bus *bus, int addr, int regnum, u16 val)
{
	/* 与 read 对称，略 */
}

static int acme_mdio_setup(struct acme_priv *priv)
{
	struct device_node *np;
	struct mii_bus *bus;
	int ret;

	bus = devm_mdiobus_alloc(priv->dev);
	if (!bus)
		return -ENOMEM;

	bus->name   = "acme-mdio";
	bus->read   = acme_mdio_read;
	bus->write  = acme_mdio_write;
	bus->parent = priv->dev;
	bus->priv   = priv;
	snprintf(bus->id, MII_BUS_ID_SIZE, "%s-mii", dev_name(priv->dev));

	np = of_get_child_by_name(priv->dev->of_node, "mdio");
	ret = of_mdiobus_register(bus, np ?: priv->dev->of_node);
	of_node_put(np);
	if (ret)
		return ret;

	priv->mii_bus = bus;
	return 0;
}
```

位置：`struct mii_bus` 在 `include/linux/phy.h:358`，你必须填的三个字段是 `name`（`:360`）、`read`（`:364`）、`write`（`:366`）；`reset`（`:368`）可选。`id` 是个定长数组，长度上限 `MII_BUS_ID_SIZE` = 61（`include/linux/phy.h:291`）。分配用 `devm_mdiobus_alloc()`（`:453`，包装 `devm_mdiobus_alloc_size()` `:452`）或裸 `mdiobus_alloc()`（`:438`，包装 `mdiobus_alloc_size()` `:430`）。

⚠️ **`of_mdiobus_register()` 不是一个函数，是 `include/linux/of_mdio.h:20` 的一个 inline 包装**。它转调 `__of_mdiobus_register()`（声明 `:17`）并额外塞进一个 `THIS_MODULE`。后果有两个：(1) 想在源码里找它的实现，grep `of_mdiobus_register` 只会命中包装，要 grep 带双下划线的名字；(2) 用 `devm` 版本时同理，`devm_of_mdiobus_register()`（`:29`）包装 `__devm_of_mdiobus_register()`（`:26`）。同一个坑在 `mdiobus_register()` 上也存在——真身是 `__mdiobus_register()`（`include/linux/phy.h:443`）。

⚠️ **`bus->priv` 要在 `of_mdiobus_register()` 之前赋值**。注册那一行会立刻开始扫总线、对每个地址发读事务，你的 `read` 回调在这一行之内就会被调用；`priv` 还是 NULL 就是空指针解引用。

拆除侧：`mdiobus_unregister()`（`include/linux/phy.h:450`）+ `mdiobus_free()`（`:451`）。用了 `devm_` 版本的分配和注册就都不用写。

本树 `drivers/net/mdio/` 是 28 个文件 / 6434 行，那里放的是**独立成模块的 MDIO 控制器**（比如 GPIO 模拟的 bit-bang）。MAC 内嵌的这一种通常就写在 MAC 驱动自己的文件里，不进那个目录。全树 36 个文件调用 `of_mdiobus_register`（口径见 04 §12.2）。

---

## §4 角色 D：PHY 驱动

绝大多数 arm64 SoC 板子不需要这一节：`drivers/net/phy/` 下有 85 个文件、47 个 `phy_driver` 数组定义，覆盖了常见的 PHY 厂商；即使一个都不匹配，`phy_device.c` 里的通用驱动也能靠标准的 IEEE 802.3 clause 22 寄存器把链路跑起来。**只有当 PHY 有非标准的初始化序列、私有寄存器页、或者需要特殊的中断确认方式时，才需要写这一节。**

```c
static int acmephy_config_init(struct phy_device *phydev)
{
	int ret;

	/* 切到厂商私有页，改一个 LED 配置，再切回来 */
	ret = phy_write(phydev, ACMEPHY_PAGE_SEL, ACMEPHY_PAGE_LED);
	if (ret < 0)
		return ret;

	ret = phy_write(phydev, ACMEPHY_LED_CTRL, ACMEPHY_LED_LINK_ACT);
	if (ret < 0)
		return ret;

	return phy_write(phydev, ACMEPHY_PAGE_SEL, ACMEPHY_PAGE_STD);
}

static struct phy_driver acmephy_drivers[] = {
	{
		PHY_ID_MATCH_MODEL(0x001cc916),
		.name		= "Acme AC1000",
		.features	= PHY_GBIT_FEATURES,
		.config_init	= acmephy_config_init,
		.soft_reset	= genphy_soft_reset,
		.read_status	= genphy_read_status,
		.suspend	= genphy_suspend,
		.resume		= genphy_resume,
	},
};
module_phy_driver(acmephy_drivers);

static struct mdio_device_id __maybe_unused acmephy_tbl[] = {
	{ PHY_ID_MATCH_MODEL(0x001cc916) },
	{ }
};
MODULE_DEVICE_TABLE(mdio, acmephy_tbl);
```

位置：`struct phy_driver` 在 `include/linux/phy.h:784`，前几个字段是 `phy_id`（`:786`）、`name`（`:787`）、`phy_id_mask`（`:788`）、`features`（`:789`）；常用回调是 `soft_reset`（`:796`）、`config_init`（`:802`）、`probe`（`:808`）、`get_features`（`:814`）、`suspend`（`:833`）、`resume`（`:835`）、`config_aneg`（`:843`）、`read_status`（`:849`）、`config_intr`（`:856`）、`handle_interrupt`（`:859`）、`match_phy_device`（`:869`）。

三个匹配宏一次填两个字段：`PHY_ID_MATCH_EXACT()`（`include/linux/phy.h:982`，32 位全比）、`PHY_ID_MATCH_MODEL()`（`:983`，忽略低 4 位的 revision）、`PHY_ID_MATCH_VENDOR()`（`:984`，只比高 22 位）。绝大多数 PHY 用 `_MODEL`——低 4 位是硅片改版号，同一个型号会有好几个。

`module_phy_driver()`（`include/linux/phy.h:1877`）展开成 `phy_module_driver()`（`:1865`），后者生成 `module_init` / `module_exit` 两个函数并调 `phy_drivers_register()`（`:1694`）/ `phy_drivers_unregister()`（`:1692`）。它自动用 `ARRAY_SIZE()` 数数组长度，所以数组必须是完整定义而不是指针。

⚠️ **`mdio_device_id` 表和 `phy_driver` 数组里的匹配是两份独立的信息**。前者（`include/linux/mod_devicetable.h:620`）只给 udev 用来在需要时自动加载模块，后者才是真正的匹配依据。改了一个忘了另一个的表现是：模块手工 `insmod` 之后 PHY 正常，但开机自动起不来。

⚠️ **`phy_read()` / `phy_write()` 的返回值是 `int`，出错时是负值**，寄存器内容只占低 16 位。写成 `u16 val = phy_read(...)` 会把 `-EIO` 变成 `0xfffb`，是一个看起来像正常寄存器值的错误值。两个函数都是 inline：`phy_read()` 在 `include/linux/phy.h:1041`、`phy_write()` 在 `:1081`。

---

## §5 角色 E：给已有驱动补 ethtool 能力

`ethtool_ops`（`include/linux/ethtool.h:640`）有 68 个函数指针（口径见 01 §0），一个都不填也能注册成功，但 `ethtool -i` 会空着。三档实现：

### §5.1 最低档：三个回调

```c
static void acme_get_drvinfo(struct net_device *ndev,
			     struct ethtool_drvinfo *info)
{
	strscpy(info->driver, "acme_eth", sizeof(info->driver));
	strscpy(info->bus_info, dev_name(&ndev->dev), sizeof(info->bus_info));
}

static const struct ethtool_ops acme_ethtool_ops = {
	.get_drvinfo		= acme_get_drvinfo,
	.get_link		= ethtool_op_get_link,
	.nway_reset		= acme_nway_reset,
};
```

`get_drvinfo` 在 `include/linux/ethtool.h:644`、`get_link` 在 `:652`、`nway_reset` 在 `:651`。`get_link` 有核心现成实现 `ethtool_op_get_link()`（`include/linux/ethtool.h:110`），它读 `netif_carrier_ok()`，对绝大多数驱动都是对的，不要自己写。

### §5.2 用了 phylink 的驱动：链路相关全部转发

```c
static int acme_get_link_ksettings(struct net_device *ndev,
				   struct ethtool_link_ksettings *cmd)
{
	struct acme_priv *priv = netdev_priv(ndev);

	return phylink_ethtool_ksettings_get(priv->phylink, cmd);
}
```

四个可以整条转发的槽：`get_link_ksettings`（`include/linux/ethtool.h:729`）→ `phylink_ethtool_ksettings_get()`（`include/linux/phylink.h:589`）、`set_link_ksettings`（`:731`）→ `phylink_ethtool_ksettings_set()`（`include/linux/phylink.h:591`）、`nway_reset`（`:651`）→ `phylink_ethtool_nway_reset()`（`include/linux/phylink.h:593`）、`set_pauseparam`（`:680`）→ `phylink_ethtool_set_pauseparam()`（`include/linux/phylink.h:596`）。

⚠️ **自己实现 `get_link_ksettings` 去读 MAC 寄存器是错的**。速率/双工是协商结果，只有 PHY 侧知道；MAC 寄存器里那份是 phylink 通过 `mac_link_up` 回调写进去的**副本**，链路刚断时它还是旧值。

### §5.3 `ethtool -S` 的三件套

三个回调必须相互一致，任何一处不一致都是越界读：

```c
static const char acme_gstrings[][ETH_GSTRING_LEN] = {
	"rx_alloc_fail",
	"tx_doorbell_ring",
};

static int acme_get_sset_count(struct net_device *ndev, int sset)
{
	if (sset != ETH_SS_STATS)
		return -EOPNOTSUPP;
	return ARRAY_SIZE(acme_gstrings);
}

static void acme_get_strings(struct net_device *ndev, u32 sset, u8 *data)
{
	if (sset == ETH_SS_STATS)
		memcpy(data, acme_gstrings, sizeof(acme_gstrings));
}

static void acme_get_ethtool_stats(struct net_device *ndev,
				   struct ethtool_stats *stats, u64 *data)
{
	struct acme_priv *priv = netdev_priv(ndev);

	data[0] = priv->rxq.alloc_fail;
	data[1] = priv->tx_doorbell;
}
```

位置：`get_strings` `include/linux/ethtool.h:683`、`get_ethtool_stats` `:685`、`get_sset_count` `:691`；`ETH_GSTRING_LEN` = 32 在 `include/uapi/linux/ethtool.h:659`，`ETH_SS_STATS` 在 `:691`。

⚠️ **`get_sset_count` 返回的数字就是核心分配 `data[]` 的长度**。`get_ethtool_stats` 写到第 N 个元素而 `get_sset_count` 只报了 N-1，就是内核堆越界写。字符串数组和填值代码放在同一个文件、紧挨着写，是唯一可靠的防线。用 `ethtool_sprintf()`（`include/linux/ethtool.h:845`）动态生成带队列号的名字时更要小心——它会推进 `data` 指针，循环次数必须和 `get_sset_count` 用同一个表达式算。

⚠️ **`sset != ETH_SS_STATS` 时必须返回 `-EOPNOTSUPP`，不能返回 0**。核心还会用别的 `ETH_SS_*` 值来问（比如测试名、私有标志名），返回 0 会让那些查询看起来「支持但是空的」。

---

## §6 角色 F：给已有驱动做增量改造

这一节的三项改造互不依赖，可以单独做。每一项的框架背景在 04 里，这里只给要动的代码。

### §6.1 从 `netif_receive_skb` 改成 GRO

一行替换，见 04 §1.2。改完唯一要检查的是：**`napi_gro_receive()` 的第一个参数必须是当前正在 poll 的那个 NAPI**，传别的 NAPI 会把包挂到错误的 GRO 桶上，表现是偶发的乱序和重传。

### §6.2 加 BQL

三个落点，缺一不可（框架说明在 04 §5）：

| 落点 | 加什么 | 位置 |
|------|--------|------|
| `ndo_start_xmit` 里踢门铃之前 | `netdev_tx_sent_queue(nq, skb->len);` | `include/linux/netdevice.h:3424-3452` |
| 发送完成回收之后 | `netdev_tx_completed_queue(nq, pkts, bytes);` | `:3506-3528` |
| `ndo_stop` / 复位路径里拆环之后 | `netdev_tx_reset_queue(nq);` | `:3546-3552` |

⚠️ **两侧统计的 bytes 必须是同一个口径**。发送侧记的是 `skb->len`，完成侧就必须累加同一个 `skb->len`（在 `napi_consume_skb()` 之前取，之后 skb 已经不属于你了）。记成描述符长度、或者忘记加上分片的长度，DQL 的限额会跑偏——表现是队列被停在一个远小于或远大于合理值的深度上，而且不报任何错。

⚠️ **忘了第三个落点的后果只在复位后出现**。`netdev_tx_reset_queue()` 把 DQL 的两侧计数清零；不清零而直接重建环，DQL 会认为有一批永远不会完成的字节在飞，`dql_avail()` 一直是负数，队列再也不会被唤醒。接口表现为「down/up 一次之后发不出包」，而收包正常。

改完的验证：`/sys/class/net/eth0/queues/tx-0/byte_queue_limits/` 这个目录会出现（组名 `byte_queue_limits` 定义在 `net/core/net-sysfs.c:1412`），里面的 `inflight` 在打流时应该是个非零且波动的值，`limit` 应该随负载自动收敛。`inflight` 恒为 0 说明发送侧那一行没生效，`limit` 恒等于 `limit_max` 说明完成侧没生效。

### §6.3 把收包缓冲改成 page_pool

框架说明在 04 §4。驱动侧要动四处：

```c
/* 1. open 建环时创建 */
static int acme_create_pool(struct acme_priv *priv, struct acme_rxq *rxq)
{
	struct page_pool_params pp = {
		.flags		= PP_FLAG_DMA_MAP | PP_FLAG_DMA_SYNC_DEV,
		.order		= 0,
		.pool_size	= rxq->size,
		.nid		= dev_to_node(priv->dev),
		.dev		= priv->dev,
		.dma_dir	= DMA_FROM_DEVICE,
		.max_len	= ACME_RX_BUF_SZ,
		.offset		= ACME_RX_HEADROOM,
	};

	rxq->page_pool = page_pool_create(&pp);
	return PTR_ERR_OR_ZERO(rxq->page_pool);
}

/* 2. 填环时取页（不再用 dev_alloc_pages） */
	page = page_pool_dev_alloc_pages(rxq->page_pool);

/* 3. 建 skb 之后打上回收标记 */
	skb_mark_for_recycle(skb);

/* 4. close 拆环时销毁 */
	page_pool_destroy(rxq->page_pool);
```

位置：`struct page_pool_params` 在 `include/net/page_pool.h:74-85`，十个字段全在那里；`PP_FLAG_DMA_MAP` `:37`、`PP_FLAG_DMA_SYNC_DEV` `:40`；`page_pool_dev_alloc_pages()` 是 `:214` 的 inline，包装 `page_pool_alloc_pages()`（`:212`，实现在 `net/core/page_pool.c:458`）；`skb_mark_for_recycle()` 在 `include/linux/skbuff.h:5127`；`page_pool_create()` 实现在 `net/core/page_pool.c:230`、`page_pool_destroy()` 在 `:874`。

⚠️ **`PP_FLAG_DMA_SYNC_DEV` 一开，`max_len` 和 `offset` 就必须填对**。池会按这两个值替你做部分同步；填 0 等于不同步，在非一致性 DMA 的 arm64 SoC 上就是收到脏数据。

⚠️ **XDP 程序改写过包之后不能直接把页还给池**。走 `XDP_TX` / `XDP_REDIRECT` 的页要交给 XDP 的内存模型（04 §2.2 的 `xdp_return_frame()` 一族），只有走 `XDP_PASS` 建成 skb 的那部分才用 `skb_mark_for_recycle()`。混起来的表现是 04 §4.4 里那条 `stalled pool shutdown` 警告，或者 inflight 变负数。

改完的验证：`page_pool_create()` 之后 `page_pool_destroy()` 如果计数不平，`dmesg` 里会周期性打 `stalled pool shutdown` 那条警告（原文见 04 §4.4）。看到它就说明有页没还。

### §6.4 注册 XDP 的接收队列信息

即使暂时不打算支持 XDP，也建议在 open/close 里成对加上这两行——它让 `xdp_rxq_info` 里的队列号和 NAPI id 对上，将来接 XDP 时不用再动一遍环的生命周期：

```c
	/* open 里，每个接收队列一次 */
	ret = xdp_rxq_info_reg(&rxq->xdp_rxq, ndev, qid, priv->rx_napi.napi_id);
	/* close 里 */
	xdp_rxq_info_unreg(&rxq->xdp_rxq);
```

`xdp_rxq_info_reg()` 是 `include/net/xdp.h:363-369` 的 inline，包装 `__xdp_rxq_info_reg()`（声明 `:360`，实现 `net/core/xdp.c:163`）；`xdp_rxq_info_unreg()` 声明 `include/net/xdp.h:371`、实现 `net/core/xdp.c:144`。全树 30 个文件用到它（口径见 04 §12.2）。

---

## §7 近邻符号区分

本节是独立一节而不是散在各处的原因：这几组名字**编译期全都合法**，错用不报错，只在运行时以奇怪的方式表现。

### §7.1 `napi_gro_receive()` 与 `netif_receive_skb()`

| 符号 | 位置 | 用在哪 |
|------|------|--------|
| `napi_gro_receive` | `net/core/gro.c:649` | NAPI poll 上下文里的常规出口 |
| `netif_receive_skb` | `include/linux/netdevice.h:3878` | 不在 NAPI 上下文、或明确不要 GRO 时 |

**错用会怎样**：在 poll 里用 `netif_receive_skb()`，功能完全正常，只是白扔掉 GRO 的收益——这是最隐蔽的一种，因为没有任何报错。反过来在非 NAPI 上下文调 `napi_gro_receive()`，第一个参数拿不到有效的 NAPI，是空指针解引用。

### §7.2 `netdev_tx_sent_queue()` 与 `netdev_sent_queue()`

| 符号 | 位置 | 面向 |
|------|------|------|
| `netdev_tx_sent_queue` | `include/linux/netdevice.h:3424` | 多队列，第一个参数是 `struct netdev_queue *` |
| `netdev_sent_queue` | `:3484` | 单队列的简写，第一个参数是 `struct net_device *` |
| `__netdev_tx_sent_queue` | `:3460` | 同 `netdev_tx_sent_queue`，但**返回 bool** 告诉你是否需要踢门铃 |

**错用会怎样**：混用不会编译错（类型不同会报，但同族里选错层次不会）。真正的坑是 `__netdev_tx_sent_queue()` 的返回值——忽略它就等于放弃了它唯一的价值（合并门铃），而且不会有任何提示。全树两侧的文件数是 44（发送侧，两个变体的并集）对 42（完成侧），差集那 6 个文件的解释见 04 §5.5。

### §7.3 `netif_tx_stop_queue()` 与 `netif_stop_queue()`

| 符号 | 位置 | 粒度 |
|------|------|------|
| `netif_tx_stop_queue` | `include/linux/netdevice.h:3309-3319` | 单条队列 |
| `netif_stop_queue` | `:3328` | 整个设备（内部就是对队列 0 调上一个） |
| `netif_tx_stop_all_queues` | `:3333` | 所有队列 |

**错用会怎样**：多队列驱动在 `ndo_start_xmit` 里调 `netif_stop_queue()`，停的永远是队列 0——当前这条队列没停，下一个包照样进来，返回 `NETDEV_TX_BUSY`，软中断空转；而队列 0 被莫名停住，那条队列上的流量卡死。这个症状看起来像「某些流量正常、某些不通」，很难往这一行上想。

### §7.4 `napi_consume_skb()` 与 `dev_kfree_skb_*` 三兄弟

| 符号 | 位置 | 上下文 |
|------|------|--------|
| `napi_consume_skb` | `include/linux/skbuff.h:3288` | NAPI poll 里回收发送完成的 skb |
| `dev_kfree_skb_any` | `include/linux/netdevice.h:3861` | 任意上下文，安全但慢 |
| `dev_kfree_skb_irq` | `:3851` | 硬中断上下文 |
| `dev_consume_skb_any` | `:3866` | 同 `_kfree_` 但语义是「正常消费」，不计入丢包 drop 追踪 |

**错用会怎样**：在 poll 里用 `dev_kfree_skb_any()` 功能正确但放弃了批量归还的优化。反过来，把 `napi_consume_skb()` 用在硬中断里是真错——它的第二个参数 `budget` 为非零时会走 NAPI 的本地缓存路径，那条路径假定自己在软中断上下文、不会被抢占。传 `budget = 0` 是官方留给「不在 poll 里」的写法。

⚠️ 还有一层语义区别：`kfree` 系列表示**异常丢弃**（会被 `drop_monitor` 和 `kfree_skb` 的 tracepoint 记录），`consume` 系列表示**正常消费**。发送成功的 skb 用 `kfree` 系列，会让丢包统计工具看到大量假丢包。

### §7.5 `napi_complete_done()` 与 `napi_complete()`

`napi_complete()` 是 `include/linux/netdevice.h:517-520` 的 inline，等于 `napi_complete_done(n, 0)`。

**错用会怎样**：传 0 意味着告诉核心「这一轮处理了 0 个包」，`gro_flush_timeout` / `napi_defer_hard_irqs` 的延迟中断逻辑拿不到有效的 work_done，那两个旋钮对你的驱动就失效了。新驱动一律用 `napi_complete_done()` 并传真实的 `done`。

### §7.6 `netif_napi_add()` 三个变体

| 符号 | 位置 | 差别 |
|------|------|------|
| `netif_napi_add` | `include/linux/netdevice.h:2592-2597` | 三参数，weight 固定 `NAPI_POLL_WEIGHT`（`:2578`，值 64） |
| `netif_napi_add_weight` | `:2580` | 四参数，自定 weight |
| `netif_napi_add_tx` | `:2619` | 三参数，另外置 `NAPI_STATE_NO_BUSY_POLL`（`:379`） |

**错用会怎样**：从老内核抄来的四参数写法在 6.1 上是编译错（参数个数不对），这一类反而安全。真正的坑是发送专用 NAPI 用了 `netif_napi_add()`：它会被加进 busy-poll 的哈希表，用户态 `SO_BUSY_POLL` 的套接字可能轮询到一个只处理发送完成的 NAPI 上，白耗 CPU。

### §7.7 MDIO 与 phylink 的 inline 包装

| 你会写的名字 | 真身 | 位置 |
|-------------|------|------|
| `of_mdiobus_register` | `__of_mdiobus_register` | `include/linux/of_mdio.h:20` → `:17` |
| `devm_of_mdiobus_register` | `__devm_of_mdiobus_register` | `:29` → `:26` |
| `mdiobus_register` | `__mdiobus_register` | 真身声明在 `include/linux/phy.h:443` |
| `mdiobus_alloc` | `mdiobus_alloc_size` | `include/linux/phy.h:438` → `:430` |
| `devlink_alloc` | `devlink_alloc_ns` | `include/net/devlink.h:1557` → `:1554` |

**错用会怎样**：代码上不会错（包装就是给你用的）。这一组的价值在**读代码和统计的时候**：grep 不带双下划线的名字找不到实现，grep 带双下划线的名字找不到调用者。数「有多少驱动用了 MDIO」这类问题必须两个名字都查，否则会漏掉直接调真身的那些。

### §7.8 `phy_connect()` 与 `phylink_of_phy_connect()`

| 符号 | 位置 | 属于哪一代 |
|------|------|-----------|
| `phy_connect` | `drivers/net/phy/phy_device.c:1113` | phylib，老一代 |
| `phylink_of_phy_connect` | `drivers/net/phy/phylink.c:1763` | phylink，新一代 |

**错用会怎样**：两者不能混用——phylink 已经在 `phylink_create()` 里接管了 PHY 状态机，再调一次 `phy_connect()` 会有两套状态机同时写 PHY 寄存器。表现是链路状态乱跳。arm64 上五个代表驱动里 `phy_connect` 命中数全为 0（04 §0.2），新驱动没有理由用它。

### §7.9 已经被本树删掉的 `u64_stats_*_irq` 一对

这一组的特殊之处是：**它不是「容易选错」，而是「你要用的那个已经不存在了」**。`u64_stats_fetch_begin_irq()` / `u64_stats_fetch_retry_irq()` 所在的整个 `/* Obsolete interfaces */` 块被本树的 PREEMPT_RT 补丁删掉了（12 行）。现存的名字是 `u64_stats_fetch_begin()`（`include/linux/u64_stats_sync.h:206`）/ `u64_stats_fetch_retry()`（`:211`）。

**错用会怎样**：编译期就报 `implicit declaration of function`。这是本组里唯一一个会被编译器拦住的，所以危害最小——但因为网上所有 6.0 之前的资料都写着 `_irq`，它是新手最先撞上的那一个。

---

## §8 「符号存在」不等于「你能用」

判据是两条**同时**成立，不是「有没有 kerneldoc」：

1. 有 `EXPORT_SYMBOL` 或 `EXPORT_SYMBOL_GPL`（模块才链接得上）；
2. 在 `include/linux/` 或 `include/net/` 下的头文件里有声明（你才能包含到）。

⚠️ **写满了 kerneldoc 却是 `static` 的内部函数比没注释的更危险**——它长得像公开 API。本子系统里这类函数密度很高，`net/core/dev.c` 一个文件就有 183 个导出符号，剩下的几百个函数全是内部的。

### §8.1 本篇用到的符号，逐个过判据

| 符号 | 导出 | 头文件声明 | 结论 |
|------|------|-----------|------|
| `alloc_etherdev_mqs` / `devm_alloc_etherdev_mqs` | `EXPORT_SYMBOL` | `include/linux/etherdevice.h:56` / `:61` | 可用 |
| `register_netdev` / `unregister_netdev` / `devm_register_netdev` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:4540` / `:4541` / `:4543` | 可用 |
| `free_netdev` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:3079` | 可用 |
| `netif_napi_add_weight` / `__netif_napi_del` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:2580` / `:2634` | 可用；上层 inline 见 §7.6 |
| `napi_enable` / `napi_disable` / `napi_complete_done` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:533` / `:531` / `:508` | 可用 |
| `__napi_schedule` / `napi_schedule_prep` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:458` / `:471` | 可用；日常用 `:480` 的 inline |
| `napi_gro_receive` / `napi_gro_frags` | `EXPORT_SYMBOL` | `include/linux/netdevice.h`（声明）/ `net/core/gro.c:649`、`:779`（实现） | 可用 |
| `netif_receive_skb` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:3878` | 可用 |
| `netif_tx_wake_queue` / `netif_tx_stop_all_queues` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:3285` / `:3333` | 可用 |
| `netif_carrier_on` / `netif_carrier_off` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:4161` / `:4162` | 可用，但用 phylink 时别自己调 |
| `netif_device_detach` / `netif_device_attach` | `EXPORT_SYMBOL` | `include/linux/netdevice.h:4270` / `:4272` | 可用 |
| `eth_type_trans` / `eth_get_headlen` | `EXPORT_SYMBOL` | `include/linux/etherdevice.h:40` / 同文件 | 可用 |
| `eth_mac_addr` / `eth_validate_addr` | 同上文件导出 | `include/linux/etherdevice.h:53` / `:54` | 可用，直接填进 ops |
| `of_get_ethdev_address` | `EXPORT_SYMBOL` | `include/linux/of_net.h:17` | 可用 |
| `eth_platform_get_mac_address` | `EXPORT_SYMBOL` | `include/linux/etherdevice.h:31` | 可用 |
| `napi_consume_skb` / `napi_build_skb` / `__napi_alloc_skb` | `EXPORT_SYMBOL` | `include/linux/skbuff.h:3288` / `:1259` / `:3281` | 可用 |
| `__dev_kfree_skb_any` | `EXPORT_SYMBOL` | 上层 inline `include/linux/netdevice.h:3861` | 可用 |
| `ethtool_sprintf` / `ethtool_op_get_link` | `EXPORT_SYMBOL` | `include/linux/ethtool.h:845` / `:110` | 可用 |
| `phylink_create` / `phylink_destroy` | `EXPORT_SYMBOL_GPL` | `include/linux/phylink.h`（声明）/ `drivers/net/phy/phylink.c:1450`、`:1545` | 可用，模块须 GPL |
| `phylink_of_phy_connect` / `phylink_disconnect_phy` | `EXPORT_SYMBOL_GPL` | `drivers/net/phy/phylink.c:1763` / `:1835` | 同上 |
| `phylink_start` / `phylink_stop` / `phylink_mac_change` | `EXPORT_SYMBOL_GPL` | `include/linux/phylink.h:580` / `:581` / `:578` | 同上 |
| `phylink_ethtool_ksettings_get` | `EXPORT_SYMBOL_GPL` | `include/linux/phylink.h:589` | 同上 |
| `mdiobus_alloc_size` / `devm_mdiobus_alloc_size` | `EXPORT_SYMBOL` | `include/linux/phy.h:430` / `:452` | 可用 |
| `__mdiobus_register` / `mdiobus_unregister` / `__of_mdiobus_register` | `EXPORT_SYMBOL` | `include/linux/phy.h:443` / `:450` / `include/linux/of_mdio.h:17` | 可用；日常用包装 |
| `phy_drivers_register` / `phy_drivers_unregister` | `EXPORT_SYMBOL` | `include/linux/phy.h:1694` / `:1692` | 可用；日常用 `module_phy_driver()` |
| `page_pool_create` / `page_pool_destroy` | `EXPORT_SYMBOL` | `include/net/page_pool.h`（声明）/ `net/core/page_pool.c:230`、`:874` | 可用 |
| `page_pool_release_page` | `EXPORT_SYMBOL` | `include/net/page_pool.h:252` | 可用，但优先用 `skb_mark_for_recycle()` |
| `dql_completed` | `EXPORT_SYMBOL` | `include/linux/dynamic_queue_limits.h:98` | 可用，但驱动不该直接调（走 BQL 宏） |

### §8.2 反例：看起来像 API，其实一个都不能用

| 符号 | 位置 | 为什么不能用 |
|------|------|-------------|
| `dev_gro_receive` | `net/core/gro.c:483` | `static`，无导出。GRO 的真正核心，但不对外 |
| `netif_rx_internal` | `net/core/dev.c:5010`（前置声明 `:162`） | `static`，无导出 |
| `enqueue_to_backlog` | `net/core/dev.c:4743` | `static`，无导出。RPS 的入口，只能被核心自己调 |
| `__netif_receive_skb_core` | `net/core/dev.c:5360` | `static`，无导出。协议分发的主函数 |
| `netif_receive_skb_internal` | `net/core/dev.c:5750` | `static`，无导出。名字里的 `internal` 是真的 |
| `xdp_set_features_flag` | 全树 0 命中 | 本版本**不存在**。XDP 能力上报机制是 6.1 之后才有的 |
| `netif_txq_completed_wake` | 全树 0 命中 | 本版本不存在。真正存在的六个宏在 `include/net/netdev_queues.h:34`、`:75`、`:103`、`:123`、`:128`、`:136` |

⚠️ 最后两行是本文档集在编写过程中真实撞到的幻觉符号——它们在更新的内核里存在，所以任何按「我记得有这个」写出来的代码在本树都编译不过。凡是拿不准的符号，跑一次 `grep -rw '<符号>' include/ net/ drivers/net/` 比回忆可靠。

---

## §9 落地自检清单

按顺序过一遍，每一条都是本篇某个 ⚠️ 的浓缩：

**probe / remove**

- [ ] `register_netdev()` 是 probe 的最后一步，它之前所有 `ndo_*` 依赖的资源都已就绪
- [ ] `unregister_netdev()` 是 remove 的第一步
- [ ] `phylink_create()` 与 `phylink_destroy()` 成对，包括 probe 的错误路径
- [ ] 用了 `devm_alloc_etherdev_mqs()` 就没有手工 `free_netdev()`
- [ ] `SET_NETDEV_DEV()` 传的是真正做 DMA 的那个 `struct device`

**open / stop**

- [ ] `netif_tx_stop_all_queues()` 在 `napi_disable()` 之前
- [ ] `free_irq()` 在 `napi_disable()` 之后
- [ ] `phylink_start()` 在 open 里、不在 probe 里

**收发路径**

- [ ] `ndo_start_xmit` 的所有出口都返回 `NETDEV_TX_OK` 或 `NETDEV_TX_BUSY`，返回 BUSY 的那条路径没有碰 skb
- [ ] 停队列的门限是 `MAX_SKB_FRAGS + 1` 而不是 1
- [ ] poll 里 `done == budget` 时不调 `napi_complete_done()`
- [ ] poll 里每一条提前退出的路径都走到了 `napi_complete_done()`（不是 `return`）
- [ ] `napi_complete_done()` 的返回值被用来决定是否开中断
- [ ] 收包出口是 `napi_gro_receive()`
- [ ] 发送完成用 `napi_consume_skb()` 且 `budget` 传的是真实值

**统计**

- [ ] 读统计用的是 `u64_stats_fetch_begin()`，没有 `_irq` 后缀
- [ ] `get_sset_count` 报的数量与 `get_strings` 的字符串数、`get_ethtool_stats` 写的元素数三者相等

**BQL（如果加了）**

- [ ] 三个落点都在：sent / completed / reset
- [ ] 两侧 bytes 是同一个口径
- [ ] `/sys/class/net/<if>/queues/tx-0/byte_queue_limits/inflight` 打流时非零

**MDIO（如果自己写了总线）**

- [ ] `bus->priv` 在 `of_mdiobus_register()` 之前赋值
- [ ] `bus->id` 用 `snprintf` 且长度上限是 `MII_BUS_ID_SIZE`

---

## §10 读下一篇

| 你还想知道 | 去哪 |
|-----------|------|
| DTS 该怎么写、每个属性谁在读 | `06-devicetree.md` |
| 上面这些没生效，怎么查 | `07-debug.md` |
| 为什么 NAPI 是这个形状、GRO 凭什么能合并 | `08-feynman.md` |
| 每个函数的完整签名和错误码语义 | `01-api.md` |
| `net_device` 的字段和结构关系 | `02-datastructures.md` |
| 注册/收发/拆除的核心侧逐段剖析 | `03-internals.md` |
| GRO / XDP / page_pool / BQL 这些框架本身 | `04-extensions.md` |
| 全篇的数字口径和重跑命令 | `99-evidence.md` |




