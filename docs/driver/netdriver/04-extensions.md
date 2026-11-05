---
title: 扩展机制
---

# 网卡驱动扩展框架

## §0 本篇怎么读

### §0.1 组织轴：一节一个独立框架，彼此不互相调用

前三篇讲的是**每一块网卡都躲不开**的东西：`net_device` 的字段、`net_device_ops` 的必填项、注册与收发的主流程。本篇讲的是**挂在主流程旁边、可以整块不要**的东西。

组织轴只有一条：**一节一个框架**。判据是「关掉它，前三篇讲的主流程照样跑通」。所以本篇的章节之间**没有调用关系**，可以任意跳读——`§5 BQL` 不依赖 `§1 GRO`，`§9 devlink` 不依赖 `§8 TC offload`。唯一的例外在 §2 与 §3 之间：AF_XDP 的缓冲池是通过 XDP 的那一个回调（`XDP_SETUP_XSK_POOL`）装进驱动的，这一处耦合在 §3.1 单独交代。

每一节固定四段：**它解决什么问题 / 驱动要实现什么 / 挂在 03 时间线哪一点 / arm64 上会不会碰到**。

### §0.2 arm64 分诊表

「本树 arm64 上写一个 SoC 内置网卡驱动，这十一个框架里哪几个真会碰到」——这是本篇最该先回答的问题。列里的「代表驱动命中数」是本树五个驱动里出现该框架入口符号的次数（重跑命令见 §12.2）。

| 节 | 框架 | am65-cpsw | mtk_eth | mvneta | mvpp2 | stmmac | arm64 上要不要写 |
|----|------|-----------|---------|--------|-------|--------|------------------|
| §1 | GRO | 1 | 1 | 3 | 1 | 2 | **必写**：收包只有 `napi_gro_receive` 一条常规出口 |
| §2 | XDP | 0 | 1 | 1 | 1 | 1 | 想要就写，不写也能上线 |
| §3 | AF_XDP | 0 | 0 | 0 | 0 | 0 | **本树 arm64 SoC 网卡基本没有**，7 个实现全是服务器网卡 |
| §4 | page_pool | 0 | 1 | 1 | 1 | 1 | 新写驱动建议写，收包重构的主要收益点 |
| §5 | BQL | 1 | 0 | 1 | 0 | 2 | 建议写，两行调用换来延迟收敛 |
| §6 | ethtool | 5 个全有 | | | | | **必写**：不写 `ethtool -i` 都出不来 |
| §7 | PHY/phylink | 1 | 1 | 1 | 1 | 1 | **必写**：SoC 网卡的链路状态全从这里来 |
| §8 | TC offload | 1 | 1 | 1 | 0 | 1 | 硬件有分类器才写 |
| §9 | devlink | 1 | 0 | 0 | 0 | 0 | 多端口/可升级固件才写 |
| §10 | RPS/RFS/XPS | 核心侧，驱动零代码 | | | | | 不用写，但要知道它改变了 03 §A8 的路径 |
| §11 | netpoll | 0 | 1 | 0 | 0 | 1 | 只在要 netconsole/kgdboe 时写 |

⚠️ 表里 `phy_connect` 在五个驱动里全是 0 命中、`phylink_create` 全是 1——**不是没接 PHY，是 arm64 上新驱动一律走 phylink 而不是裸 `phy_connect`**。这一点在 §7.2 展开。

### §0.3 每个框架挂在 03 时间线的哪一点

03 §0.3 的时间线是 `alloc → setup → register → open → 收发 → close → unregister → run_todo → free`。本篇的十一个框架各自插在下面这些位置：

| 节 | 框架 | 挂载点（03 时间线） | 具体动作 |
|----|------|---------------------|----------|
| §1 | GRO | **收包**（03 §A8 之后） | 驱动在 poll 里把 skb 交给 `napi_gro_receive()` 而不是 `netif_receive_skb()` |
| §2 | XDP | **register 之后**任意时刻 | 用户态 attach 时核心回调 `ndo_bpf`；驱动在 open 时 `xdp_rxq_info_reg()` |
| §3 | AF_XDP | **open 之后**、单队列粒度 | `ndo_bpf(XDP_SETUP_XSK_POOL)` 把池装进某个队列，要重启该队列 |
| §4 | page_pool | **open**（建环时）/ **close**（拆环时） | `page_pool_create()` ↔ `page_pool_destroy()` |
| §5 | BQL | **发送**与**发送完成**两侧 | 发时 `netdev_tx_sent_queue()`，回收时 `netdev_tx_completed_queue()` |
| §6 | ethtool | **alloc 之后、register 之前** | `dev->ethtool_ops = &xxx_ethtool_ops;` |
| §7 | PHY/phylink | **probe**（建）/ **open**（启）/ **close**（停） | `phylink_create()` → `phylink_start()` → `phylink_stop()` |
| §8 | TC offload | **register 之后** | 用户态下规则时核心回调 `ndo_setup_tc` |
| §9 | devlink | **register 之前**（先 devlink 后 netdev） | `devlink_alloc()` → 注册端口 → 再 `register_netdev()` |
| §10 | RPS/RFS/XPS | **收包**（03 §A8 的 backlog 那一支） | 驱动零代码，核心在 `netif_rx_internal()` 里改道 |
| §11 | netpoll | **open/close 的两侧**（禁用窗口） | 核心已经在 `__dev_open()`/`__dev_close_many()` 里插好了 `netpoll_poll_disable/enable` |

---

## §1 GRO：把小包在进协议栈之前拼成大包

### §1.1 它解决什么问题

千兆以上的线速下，1500 字节 MTU 意味着每秒几十万个包，每个包都要走一遍完整的协议栈。GRO（Generic Receive Offload）的做法是：**在 NAPI poll 的上下文里，把属于同一条流的连续小包合并成一个大 skb，再交给协议栈**，让协议栈的每包开销摊薄到几分之一。

实现全在 `net/core/gro.c`（817 行）。核心是 `dev_gro_receive()`（`net/core/gro.c:483`）——它拿一个 skb，去 NAPI 的 GRO 哈希桶里找能合并的伙伴，找到就合并进去、返回；找不到就把它自己挂进桶里等下一个。

### §1.2 驱动要实现什么

**一行**：收包路径的最后一步，把

```c
netif_receive_skb(skb);
```

换成

```c
napi_gro_receive(&priv->napi, skb);
```

`napi_gro_receive()` 在 `net/core/gro.c:649`。仅此一处改动，驱动就吃到 GRO 了。第二个入口是 `napi_gro_frags()`（`net/core/gro.c:779`），给那些「拿到的是页片段、还没建 skb」的驱动用——本树 `drivers/net/ethernet/` 下 93 个文件用前者、17 个文件用后者（重跑命令见 §12.2）。

⚠️ **驱动不需要、也不应该实现任何 GRO 回调**。合并逻辑是**按协议**分发的，注册者是协议层不是驱动：

```c
struct offload_callbacks {          /* include/linux/netdevice.h:2667 */
	...
	struct sk_buff		*(*gro_receive)(struct list_head *head,
						struct sk_buff *skb);
	int			(*gro_complete)(struct sk_buff *skb, int nhoff);
};
struct packet_offload {             /* include/linux/netdevice.h:2675 */
	__be16			 type;
	u16			 priority;
	struct offload_callbacks callbacks;
	struct list_head	 list;
};
```

注册函数是 `dev_add_offload()`（`net/core/gro.c:29`），全树只有 6 个文件调用它、共 7 个调用点：`net/ethernet/eth.c:477`（以太网）、`net/ipv4/af_inet.c:1953`、`net/ipv6/ip6_offload.c:481`、`net/8021q/vlan_core.c:553`（在数组上循环）、`net/mpls/mpls_gso.c:95` 与 `:96`、`net/nsh/nsh.c:138`。**没有一个在 `drivers/` 下**——这就是「驱动只管交包」的证据。

### §1.3 什么时候真的往上交

合并出来的大包不会一直攒着。三个刷出点：

| 刷出点 | 位置 | 触发条件 |
|--------|------|----------|
| 攒够一批 | `net/core/gro.c:15` 的 `gro_normal_batch` | 默认 8 个，`net/core/sysctl_net_core.c:586-587` 可调 |
| 桶满了挤掉最老的 | `gro_flush_oldest()` `net/core/gro.c:464` | 链长 ≥ `MAX_GRO_SKBS`，判断在 `net/core/gro.c:563` |
| poll 结束 | `napi_gro_flush()` `net/core/gro.c:352` | 由 `napi_complete_done()` 调用（见 03 §A8.4） |

⚠️ **`gro_normal_batch` 和 `MAX_GRO_SKBS` 都等于 8，但是两个不相干的量**：前者（`net/core/gro.c:15`）是「一次往协议栈交几个包」，可以用 sysctl 改；后者（`#define MAX_GRO_SKBS 8`，`net/core/gro.c:7`）是「一个哈希桶里最多挂几条待合并的链」，编译期定死。看到「8」不要以为是同一个旋钮。

挤掉最老的那一个用的是 `list_last_entry()`，并且**故意不减 `napi->gro_hash[].count`**——因为调用者正要往链上加一个新的，加减抵消。这条注释在 `net/core/gro.c:476-478`。

合并完成后的收尾是 `napi_gro_complete()`（`net/core/gro.c:294`），它反查协议的 `gro_complete` 回调修好校验和与长度字段，再把大包交上去。

第四个「刷出点」不在 gro.c 里而在 NAPI 侧：`gro_flush_timeout` 与 `napi_defer_hard_irqs` 这两个 sysfs 旋钮（`net/core/net-sysfs.c:389-421`）让核心在 poll 结束后**故意再等一小会儿**，攒到更多包才唤中断——对延迟敏感的场景要把它们调回 0。

### §1.4 特性开关的关系

GRO 是 `NETIF_F_GRO` 这个 feature 位，默认打开，`ethtool -K eth0 gro off` 关掉。它和 `NETIF_F_GRO_HW`（硬件做聚合）、`NETIF_F_LRO`（老式硬件 LRO）是三件不同的事：

| 名字 | 谁做 | 能不能转发 |
|------|------|------------|
| `NETIF_F_GRO` | 软件，本节 | 能，合并是可逆的 |
| `NETIF_F_GRO_HW` | 硬件 | 能 |
| `NETIF_F_LRO` | 硬件，且信息有损 | **不能**，所以核心会在设备入桥/入转发时强关它 |

强关那条路径就是 03 §B4 提到的 `dev_disable_lro()`（`net/core/dev.c:1638-1651`）与 `dev_disable_gro_hw()`（`net/core/dev.c:1662-1669`）。

---

## §2 XDP：在 skb 建立之前跑一段 BPF

### §2.1 它解决什么问题

GRO 省的是协议栈开销，XDP（eXpress Data Path）省的是**连 skb 都不建**。它在驱动收到硬件描述符、还没分配 `sk_buff` 的时候，先让一段 BPF 程序看一眼这段原始内存，程序返回一个动作码：丢掉、原路弹回、转发到别的网卡、交给 AF_XDP 套接字、或者放行走正常路径。典型用途是抗 DDoS 和四层负载均衡。

核心分成两半：

| 位置 | 规模 | 职责 |
|------|------|------|
| `net/core/xdp.c` | 707 行 | 内存模型注册、`xdp_frame` 的回收与转换 |
| `net/core/filter.c` | 11789 行 | BPF 辅助函数、`XDP_REDIRECT` 的目标解析 |

### §2.2 驱动要实现什么

三件事，缺一不可：

**(a) 一个 `ndo_bpf` 回调**，负责 attach/detach 程序。核心把命令码装在 `struct netdev_bpf`（`include/linux/netdevice.h:1015`）里传下来，命令码枚举是：

| 命令 | 位置 | 谁用 |
|------|------|------|
| `XDP_SETUP_PROG` | `include/linux/netdevice.h:989` | 装/卸驱动模式的 XDP 程序 |
| `XDP_SETUP_PROG_HW` | `:990` | 卸载到硬件（`XDP_MODE_HW`） |
| `BPF_OFFLOAD_MAP_ALLOC` | `:992` | BPF map 卸载，网卡侧极少 |
| `BPF_OFFLOAD_MAP_FREE` | `:993` | 同上 |
| `XDP_SETUP_XSK_POOL` | `:994` | AF_XDP 的池，见 §3 |

三种挂载模式在 `enum bpf_xdp_mode`（`include/linux/netdevice.h:1003-1008`）：`XDP_MODE_SKB`（核心的通用兜底，不需要驱动支持）、`XDP_MODE_DRV`（驱动原生）、`XDP_MODE_HW`（卸载给硬件）。

**(b) 每个接收队列注册一次内存模型**。open 时调 `xdp_rxq_info_reg()`，close 时 `xdp_rxq_info_unreg()`（`net/core/xdp.c:144`）。内存模型的注册在 `xdp_rxq_info_reg_mem_model()`（`net/core/xdp.c:344`），它会把「这些页是谁的、怎么还」记进一张全局 rhashtable（建表在 `__mem_id_init_hash_table()`，`net/core/xdp.c:206`；ID 分配在 `__mem_id_cyclic_get()`，`net/core/xdp.c:235`），这样 `XDP_REDIRECT` 出去的页片在别的网卡上发完之后还能凭这个 ID 找到路回家。回家的入口是 `xdp_return_frame()`（`net/core/xdp.c:404`）一族。

**(c) 支持 `ndo_xdp_xmit`**，如果你想当 `XDP_REDIRECT` 的**目标**。它一次收一批 `xdp_frame`，配合 `XDP_XMIT_FLUSH` 标志决定要不要立刻踢硬件门铃。只当**来源**不当目标的话这一项可以不写。

本树 `drivers/net/ethernet/` 下的实现数：`ndo_bpf` 33 个、`ndo_xdp_xmit` 26 个、用到 `xdp_rxq_info_reg` 的 30 个文件。

### §2.3 挂在时间线哪一点

`ndo_bpf` 可以在设备 **register 之后的任意时刻**被调用，包括接口正在跑流量的时候。这意味着驱动的 attach 实现通常要**重启接收队列**：停 NAPI、拆环、按新的 headroom 重建、再起 NAPI。这一段是 XDP 驱动支持里最容易出 bug 的地方，也是 §0.2 里把它标成「想要就写」的原因。

`xdp_rxq_info_reg` 相反，它跟着 open/close 走，和 03 §A5 / §A9 的两条路径成对。

⚠️ 6.1 里**没有** `xdp_features` 这套能力上报机制（后来版本才有），所以用户态无法查询「这块网卡支持哪些 XDP 动作」——只能试。这是本树的版本时效性事实，不是遗漏。

---

## §3 AF_XDP：把收包队列整个交给用户态

### §3.1 它和 §2 的唯一耦合点

AF_XDP（也叫 XSK，`AF_XDP` 套接字）让用户态进程直接读写网卡的 DMA 缓冲区，零拷贝。实现在 `net/xdp/` 目录（共 3504 行，其中 `net/xdp/xsk.c` 1526 行、`net/xdp/xsk_buff_pool.c` 680 行、`net/xdp/xdp_umem.c` 259 行）。官方原文见 `Documentation/networking/af_xdp.rst`（640 行，含用户态那一侧的完整用法，本节不复述）。

它和 §2 的接口只有一个：**用 `ndo_bpf` 的 `XDP_SETUP_XSK_POOL` 命令把缓冲池装进指定的那一个队列**。装上之后，那个队列的收包不再进协议栈，直接落到用户态的环里；其余队列不受影响。除了这一个命令码，AF_XDP 和 XDP 程序是两套独立的机制——**可以只支持 XDP 不支持 AF_XDP，反过来不行**。

### §3.2 驱动要实现什么

除了 `XDP_SETUP_XSK_POOL`，还要一个 `ndo_xsk_wakeup`：用户态往发送环里塞了东西之后，通过这个回调告诉驱动「去看一眼」。回调声明在 `include/linux/netdevice.h:1613`。

本树 `drivers/net/ethernet/` 下 `ndo_xsk_wakeup` 的实现只有 **7 个**——对比 `ndo_bpf` 的 33 个，比例很说明问题：**支持 XDP 的驱动里只有五分之一支持零拷贝 AF_XDP**。

### §3.3 arm64 上会不会碰到

§0.2 的分诊表里，五个 arm64 代表驱动对 AF_XDP 全部是 0 命中。7 个实现全部集中在服务器网卡厂商目录下。**在 arm64 SoC 网卡上，这一节是「知道它存在、知道它要求什么」就够了**，本篇不再展开队列重启的细节。

---

## §4 page_pool：收包页面的专用分配器

### §4.1 它解决什么问题

收包路径每秒要分配几十万个页面、做几十万次 DMA 映射，然后在协议栈用完之后释放掉。`alloc_page()` + `dma_map_page()` 这一对在这个频率下就是瓶颈——尤其是 IOMMU 打开时，映射操作要走页表。官方原文见 `Documentation/networking/page_pool.rst`（223 行）。

page_pool 的思路是**页面不还给伙伴系统，DMA 映射也不解除**，而是攒在池里循环用。实现在 `net/core/page_pool.c`（938 行），对外头文件 `include/net/page_pool.h`（386 行）。

### §4.2 两层缓存

池内部是**两层**，这是理解它性能特征的关键：

| 层 | 数据结构 | 位置 | 谁访问 | 要不要锁 |
|----|----------|------|--------|----------|
| 快速缓存 | `struct pp_alloc_cache` 里的 `cache[]` 数组 | `include/net/page_pool.h:69` / `:71`，池里的字段在 `:182` | 只有 NAPI 上下文（生产者=消费者） | **不要**，天然串行 |
| 归还环 | `struct ptr_ring` | 池里的字段在 `include/net/page_pool.h:195` | 任意上下文释放的页从这里回来 | 要，环自带锁 |

取页走 `__page_pool_get_cached()`（`net/core/page_pool.c:306`），空了就从归还环批量搬一批过来——`page_pool_refill_alloc_cache()`（`net/core/page_pool.c:253`）。都空了才真去 `alloc_pages()`，走 `__page_pool_alloc_pages_slow()`（`net/core/page_pool.c:399`）。

⚠️ **快速缓存的「不要锁」是有前提的**：只有 NAPI poll 上下文能碰它。这个前提在 XDP 的 `ndo_xdp_xmit` 路径上不成立，所以 6.1 里有一对 `page_pool_producer_lock()` / `page_pool_producer_unlock()`（`net/core/page_pool.c:136` / `:149`）专门保护归还环的生产端。

### §4.3 驱动要实现什么

**一对创建/销毁 + 换一个分配调用**：

| 动作 | API | 位置 |
|------|-----|------|
| 建池（open 建收包环时） | `page_pool_create()` | `net/core/page_pool.c:230` |
| 取整页 | `page_pool_alloc_pages()` | `net/core/page_pool.c:458` |
| 取页片（一页切几份） | `page_pool_alloc_frag()` | `net/core/page_pool.c:720` |
| 归还 | `page_pool_put_defragged_page()` | `net/core/page_pool.c:628` |
| 批量归还 | `page_pool_put_page_bulk()` | `net/core/page_pool.c:641` |
| 脱离池（页要长期留在 skb 里时） | `page_pool_release_page()` | `net/core/page_pool.c:497` |
| 销毁（close 拆环时） | `page_pool_destroy()` | `net/core/page_pool.c:874` |

建池时填 `struct page_pool_params`（`include/net/page_pool.h:74-85`），四个标志位决定池替你做多少事：

| 标志 | 位置 | 含义 |
|------|------|------|
| `PP_FLAG_DMA_MAP` | `include/net/page_pool.h:37` | 池负责 DMA 映射与解映射 |
| `PP_FLAG_DMA_SYNC_DEV` | `:40` | 池负责回收时的 `dma_sync_for_device` |
| `PP_FLAG_PAGE_FRAG` | `:48` | 允许一页切成多个片段 |
| `PP_FLAG_ALL` | `:49` | 上面三个的并集 |

### §4.4 销毁不是立即的

⚠️ **`page_pool_destroy()` 返回不代表池已经没了**。协议栈手里可能还攥着这个池分配的页，池必须等它们全部回来。机制是 `page_pool_release()`（`net/core/page_pool.c:825`）这三行：

1. `page_pool_scrub()`（`net/core/page_pool.c:814`）先把能收的收掉；
2. `page_pool_inflight()`（`net/core/page_pool.c:478`）算出还有多少页在外面；
3. 为零才真正 `page_pool_free()`（`net/core/page_pool.c:781`），不为零就把 inflight 数返回给延迟工作 `page_pool_release_retry()`（`net/core/page_pool.c:837`）继续重试。

重试的退出条件是 `inflight <= 0`，注释（`net/core/page_pool.c:844-849`）明确写了负数的含义：**驱动 bug 导致的记账错误，永远不会恢复，所以也不再重试**。等太久会周期性打印：

```
%s() stalled pool shutdown %d inflight %d sec
```

出自 `net/core/page_pool.c:857-858`。**在 dmesg 里看到这一句，说明有页面被永久漏掉了**——排查方向是驱动的归还路径，不是 page_pool 本身。

页从 skb 侧回家的入口是 `page_pool_return_skb_page()`（`net/core/page_pool.c:911`）——skb 释放时会认出「这一页属于某个 page_pool」并把它送回去，而不是还给伙伴系统。

这条「不立即」的性质对驱动的直接后果：**卸载模块时不能假设 `page_pool_destroy()` 之后 DMA 就安全了**，要靠池自己的引用计数收尾。

### §4.5 统计走 ethtool

池自带一套统计，通过三个专用函数暴露给 §6 的 ethtool 框架：`page_pool_ethtool_stats_get_strings()`（`net/core/page_pool.c:91`）、`page_pool_ethtool_stats_get_count()`（`:104`）、`page_pool_ethtool_stats_get()`（`:110`）。驱动在自己的 `ethtool_ops.get_ethtool_stats` 里把它们串进去即可，不用自己数。原始取数是 `page_pool_get_stats()`（`net/core/page_pool.c:60`）。

本树 `drivers/net/ethernet/` 下 12 个文件调用 `page_pool_create()`，扩到整个 `drivers/net/` 是 13 个——**page_pool 的用户几乎全是网卡驱动本身**，没有中间层替你用。

---

## §5 BQL / DQL：让发送队列里的字节数自己收敛

### §5.1 它解决什么问题

发送环有 1024 个描述符，驱动一路填满，这 1024 个包就都排在硬件里。此时 qdisc 层再想做优先级调度已经晚了——包已经过了软件能干预的边界。这就是 buffer bloat 在网卡这一层的形态：**队列越深，延迟越大，而深度是编译期定死的**。

BQL（Byte Queue Limits）的做法是给每条发送队列加一个**动态的字节上限**，观察「硬件多快消化完」来自动调整这个上限，让队列里只留刚好够跑满线速的字节数。算法本体在 `lib/dynamic_queue_limits.c`（138 行），头文件 `include/linux/dynamic_queue_limits.h`（108 行），网络侧的包装在 `include/linux/netdevice.h`。

### §5.2 驱动要实现什么

**两处调用**，分别在发送和发送完成两侧：

| 时机 | 多队列 API | 单队列包装 |
|------|-----------|-----------|
| 把包交给硬件之后 | `netdev_tx_sent_queue()` `include/linux/netdevice.h:3424-3452` | `netdev_sent_queue()` `:3484` |
| 回收完成的描述符之后 | `netdev_tx_completed_queue()` `:3506-3528` | `netdev_completed_queue()` `:3540` |
| 队列复位（down/reset 时） | `netdev_tx_reset_queue()` `:3546-3552` | — |

⚠️ **两侧的 `bytes` 必须严格相等**。这不是建议——`netdev_tx_completed_queue()` 的 kerneldoc（`include/linux/netdevice.h:3497-3505`）明确写了它「每个完成轮最多调用一次」，而 `netdev_tx_sent_queue()` 的文档（`:3414-3423`）要求字节数与之精确匹配。算错了不会立刻炸，而是队列上限收敛到一个错的值，表现为无法解释的吞吐损失。

第四个入口是 `__netdev_tx_sent_queue()`（`include/linux/netdevice.h:3460`），它返回一个 bool 告诉驱动「现在该不该踢门铃」，让 BQL 记账和门铃合并决策合成一次调用。本树 12 个文件用它。

### §5.3 停唤队列的那一段在 03 里

BQL 的停队列动作（`__QUEUE_STATE_STACK_XOFF`）和它与 `netif_tx_stop_queue()` 的 `__QUEUE_STATE_DRV_XOFF` 是**两个不同的位**——这一组关系已经在 03 §B3 完整展开（含两处内存屏障的配对、以及「关掉 `CONFIG_BQL` 只丢掉一条 `trans_start` 记录路径、不影响 tx timeout 检测」这个结论）。本节不重复。

### §5.4 三个数字与它们的关系

| 名字 | 含义 | 从哪儿看 |
|------|------|----------|
| `limit` | 当前允许排队的字节上限 | `/sys/class/net/<if>/queues/tx-N/byte_queue_limits/limit` |
| `inflight` | 现在排着多少字节 | 同目录 `inflight`（只读） |
| `hold_time` | 上限调整的时间常数 | 同目录 `hold_time` |

sysfs 这一组的实现是 `net/core/net-sysfs.c:1402` 的 `dql_attrs[]` 数组与 `:1411` 的 `dql_group`，挂载点在 `net/core/net-sysfs.c:1670`。`limit` 的三个变体（`limit` / `limit_max` / `limit_min`）由 `net/core/net-sysfs.c:1394-1395` 的宏批量生成，读写函数是 `bql_show()`（`:1316`）与 `bql_set()`（`:1321`）。

算法侧对应 `dql_completed()`（`lib/dynamic_queue_limits.c:18`）——上限的调整全在这一个函数里，`dql_queued()` 只是记账（`include/linux/dynamic_queue_limits.h:75`）。驱动侧还有一个 `netdev_queue_set_dql_min_limit()`（`include/linux/netdevice.h:3378`），给「硬件有最小批量要求」的场合抬地板。

### §5.5 两侧调用文件数不对称

| 符号 | `drivers/net/ethernet/` 文件数 |
|------|------------------------------|
| `netdev_tx_sent_queue` | 32 |
| `__netdev_tx_sent_queue` | 12 |
| `netdev_tx_completed_queue` | 42 |
| `netdev_tx_reset_queue` | 41 |
| `netdev_sent_queue`（单队列） | 21 |
| `netdev_completed_queue`（单队列） | 22 |

发送侧两个入口合起来命中 44 个文件、完成侧 42 个，两边取差集后只剩 6 个「有 completed 无 sent」的文件：`cavium/thunder/nicvf_main.c`、`marvell/octeon_ep/octep_tx.c`、`qlogic/qede/qede_main.c`、`sfc/efx_channels.c`、`sfc/falcon/efx.c`、`sfc/siena/efx_channels.c`（路径均相对 `drivers/net/ethernet/`）。**这 6 个不是漏调用，而是同一个驱动的两侧写在不同文件里**——按文件数统计天然会出现这种偏差，重跑命令与差集命令都在 §12.2。

---

## §6 ethtool：唯一必写的扩展框架

### §6.1 它解决什么问题

`ethtool` 是网卡的通用控制面：查速率/双工、开关 offload 特性、调环大小与中断合并、读寄存器与 EEPROM、跑自检、点灯定位物理端口、拉私有统计。**它是唯一一个「不写就明显缺功能」的扩展框架**——连 `ethtool -i eth0` 显示驱动名都要 `ethtool_ops` 里的一个回调。

### §6.2 两套前端，一个后端

6.1 里 ethtool 有**两条完全独立的用户态入口**，这是本节最需要先分清的一组关系：

| | ioctl 前端 | netlink 前端 |
|---|-----------|--------------|
| 入口 | `dev_ethtool()` `net/ethtool/ioctl.c:3021` | genl 家族，`net/ethtool/netlink.c` |
| 分发 | `__dev_ethtool()` `net/ethtool/ioctl.c:2722` 的大 switch | `ethnl_default_doit()` `net/ethtool/netlink.c:351` + `ethtool_genl_ops[]` `:742` |
| 从哪儿被调 | `net/core/dev_ioctl.c:524` | 通用 netlink 收包路径 |
| 规模 | 3344 行 | 1079 行 |
| ABI 稳定性 | 固定结构体，加字段就是 ABI 事件 | 属性可扩展 |

⚠️ **驱动只面对后端，看不到前端的区别**。两套前端最终都落到同一个 `dev->ethtool_ops` 上，共用 `net/ethtool/common.c`（599 行）里的公共实现。所以驱动作者不需要为 netlink 写第二份代码——但**要知道有些新特性只有 netlink 前端能表达**，用老的 `ethtool` 二进制看不到。

整个 `net/ethtool/` 是 26 个 `.c`、共 11354 行（另有 3 个 `.h`：`bitset.h`、`common.h`、`netlink.h`，`.c`+`.h` 合计 11860 行）。

netlink 前端每条消息的属性定义、以及「哪些特性只有 netlink 能表达」的完整清单，官方原文见 `Documentation/networking/ethtool-netlink.rst`（1790 行）——它是本子系统里最长的一篇官方文档，本文档集只覆盖到「驱动侧看不到前端区别」这一层为止，具体属性不展开。

### §6.3 驱动要实现什么

一句赋值，位置在 alloc 之后、`register_netdev()` 之前：

```c
dev->ethtool_ops = &xxx_ethtool_ops;
```

`struct ethtool_ops` 有 **68** 个函数指针（口径见 99 §3），全部可选。本树 `drivers/net/ethernet/` 下有 **237** 个 `ethtool_ops` 实例——对比 `net_device_ops` 的 284 个，说明**绝大多数驱动都写了 ethtool**，只有少数几十个没写。

最低限度通常是三个：报驱动名与固件版本、报链路状态、报环大小。arm64 SoC 网卡的实际情况通常还要加上 §7 的 phylink 转发——`phylink_ethtool_ksettings_get()`（`drivers/net/phy/phylink.c:2138`）之类的函数就是为了让驱动把 ethtool 的链路查询整块转给 phylink。

### §6.4 特性开关不走这里

⚠️ 容易搞混的一点：`ethtool -K eth0 tso off` **不经过 `ethtool_ops`**，它走的是 features 那条路——`dev->wanted_features` 改动 + `__netdev_update_features()` 重算 + `ndo_set_features` 回调。这条路径已经在 03 §A4 与 §B4 完整展开，触发点里的 `net/ethtool/features.c:260`、`net/ethtool/ioctl.c:156`、`:285`、`:340` 四处就是 ethtool 前端的落点。**`ethtool_ops` 管的是「查询与调参」，features 管的是「开关能力」，两套机制。**

头文件规模供参照：内核侧 `include/linux/ethtool.h` 846 行，uapi 侧 `include/uapi/linux/ethtool.h` 2156 行——后者比前者大，正是「ABI 结构体多、内核抽象少」的体现。

---

## §7 PHY / MDIO / phylink：链路状态从哪儿来

### §7.1 三个目录的分工

SoC 网卡的 MAC 只管收发帧，「线插没插、协商成什么速率」是 PHY 芯片的事，而 PHY 挂在一条叫 MDIO 的低速管理总线上。本树把这三件事分了三个目录：

| 目录 | 文件数 | 行数 | 职责 |
|------|--------|------|------|
| `drivers/net/phy/` | 85 | 59610 | PHY 驱动 + phylib + phylink |
| `drivers/net/mdio/` | 28 | 6434 | MDIO 总线控制器驱动 |
| `drivers/net/pcs/` | 6 | 2678 | PCS/SerDes 层（高速接口用） |

`drivers/net/phy/` 里最大的三个文件：`phylink.c` 3530 行、`phy_device.c` 3283 行、`phy.c` 1546 行。对外头文件 `include/linux/phy.h` 1883 行、`include/linux/phylink.h` 633 行。

### §7.2 phylib 与 phylink 是两代 API，不要混用

这是本节最关键的一组关系。**§0.2 的分诊表里五个 arm64 代表驱动全是 `phylink_create` 1 命中、`phy_connect` 0 命中**——不是巧合：

| | phylib（老） | phylink（新） |
|---|-------------|--------------|
| 连接 | `phy_connect()` `drivers/net/phy/phy_device.c:1113` | `phylink_create()` `drivers/net/phy/phylink.c:1450` + `phylink_of_phy_connect()` `:1763` |
| 启停 | `phy_start()` `drivers/net/phy/phy.c:1152` / `phy_stop()` `:1105` | `phylink_start()` `drivers/net/phy/phylink.c:1889` / `phylink_stop()` `:1961` |
| 断开 | `phy_disconnect()` `drivers/net/phy/phy_device.c:1145` | `phylink_disconnect_phy()` `drivers/net/phy/phylink.c:1835` |
| 销毁 | — | `phylink_destroy()` `drivers/net/phy/phylink.c:1545` |
| 能表达什么 | 「MAC 后面挂一个 PHY」 | 还能表达 SFP 笼、背板、固定链路、在线换介质 |
| 谁调 MAC 的配置 | 驱动自己在 adjust_link 回调里写寄存器 | phylink 通过 `phylink_mac_ops` 回调驱动 |

本树 `drivers/net/ethernet/` 下的文件数：`phy_connect` 28 个、`phylink_create` 15 个。**看起来 phylib 还占多数——但那 28 个里绝大部分是老驱动**，而 arm64 上活跃维护的 SoC 网卡（cpsw、mtk、mvneta、mvpp2、stmmac）无一例外走 phylink。写新驱动就写 phylink。

`struct phylink_mac_ops` 在 `include/linux/phylink.h:160`，该头文件里共 15 个函数指针。驱动要实现的核心是「按 phylink 给的速率/双工/接口模式去配 MAC 寄存器」。

### §7.3 MDIO 总线：MAC 驱动通常要自己注册一条

SoC 上的 MDIO 控制器往往就在 MAC 的寄存器块里，所以 MAC 驱动经常要顺手注册一条 MDIO 总线。DT 路径的入口在 `drivers/net/mdio/of_mdio.c`（445 行），本树 `drivers/net/ethernet/` 下 36 个文件用到它。

⚠️ **`of_mdiobus_register()` 是一个 `static inline` 包装**，定义在 `include/linux/of_mdio.h:20`，真身是 `__of_mdiobus_register()`（声明在 `include/linux/of_mdio.h:17`）。同理 `mdiobus_register()` 的真身是 `__mdiobus_register()`（`drivers/net/phy/mdio_bus.c:528`）。**多出的那个参数是 `THIS_MODULE`**——所以在 grep 找调用者时两个名字都要查，只查一个会漏。还有一对 devm 版本：`devm_of_mdiobus_register()` / `__devm_of_mdiobus_register()`（`include/linux/of_mdio.h:29` / `:26`）。

PHY 芯片自己的驱动是 `struct phy_driver`（`include/linux/phy.h:784`），本树 `drivers/net/phy/` 下有 47 个 `phy_driver` 数组定义——**写 MAC 驱动一般不用碰这一层**，常见 PHY 都有现成驱动，认不出来时 phylib 还有 generic 兜底。

### §7.4 挂在时间线哪一点

phylink 的生命周期比 netdev 的略宽：

```
probe:  phylink_create()  →  phylink_of_phy_connect()
                                      ↓
open:                            phylink_start()      ← 03 §A5 的 ndo_open 里
                                      ↓
                              （链路事件 → phylink → netif_carrier_on/off
                                      → 03 §B6 的 linkwatch）
                                      ↓
close:                           phylink_stop()       ← 03 §A9 的 ndo_stop 里
remove: phylink_disconnect_phy()  →  phylink_destroy()
```

⚠️ **建在 probe、启在 open**，这两处分开是必须的：`phylink_create()` 要在 `register_netdev()` 之前完成，否则接口一上线就可能有人来查链路状态而 phylink 还没准备好。链路事件最终汇入 `netif_carrier_on()`/`netif_carrier_off()`，之后就是 03 §B6 讲的 linkwatch 三级门——**本节到这里为止，carrier 之后的事在 03**。

---

## §8 TC / flow offload：把分类规则下到硬件

### §8.1 它解决什么问题

`tc` 命令可以在软件里做包分类、限速、优先级调度。硬件网卡里往往也有分类器（TCAM）和整形器，能以零 CPU 开销做同样的事。TC offload 就是那条把软件规则翻译成硬件配置的通道。

驱动侧只有**一个回调**：`ndo_setup_tc`（`include/linux/netdevice.h:1493`，kerneldoc 在 `:1206`）。核心把「要装什么」编码成一个类型码传下来。

### §8.2 21 种类型码，驱动只需要认自己支持的

类型码是 `enum tc_setup_type`（`include/linux/netdevice.h:954-976`，21 个成员）。它们不是一类东西，粗分三组：

| 组 | 成员 | 驱动要做什么 |
|----|------|--------------|
| 能力查询 | `TC_QUERY_CAPS` `include/linux/netdevice.h:955` | 报告自己支持什么，让上层不下无效规则 |
| 分类器 | `TC_SETUP_CLSU32` `:957`、`TC_SETUP_CLSFLOWER` `:958`、`TC_SETUP_CLSMATCHALL` `:959`、`TC_SETUP_CLSBPF` `:960`、`TC_SETUP_BLOCK` `:961`、`TC_SETUP_FT` `:970`、`TC_SETUP_ACT` `:975` | 把匹配键+动作装进硬件表 |
| qdisc | `TC_SETUP_QDISC_MQPRIO` `:956`、`_CBS` `:962`、`_RED` `:963`、`_PRIO` `:964`、`_MQ` `:965`、`_ETF` `:966`、`_GRED` `:968`、`_TAPRIO` `:969`、`_ETS` `:971`、`_TBF` `:972`、`_FIFO` `:973`、`_HTB` `:974`，加 `TC_SETUP_ROOT_QDISC` `:967` | 配置硬件的队列与整形器 |

⚠️ **不支持的类型码要返回 `-EOPNOTSUPP`，不能返回 0**。返回 0 意味着「我装好了」，上层就不会再在软件里做这件事——规则静默失效。这是 TC offload 最典型的静默故障。

`TC_SETUP_QDISC_TAPRIO` 与 `TC_SETUP_QDISC_ETF` 在 arm64 上值得单独一提：它们是 TSN（时间敏感网络）用的，而 TSN 的主要落地场景恰好是 arm64 工业 SoC。本树 `ti/am65-cpsw-nuss.c` 与 `stmicro/stmmac/stmmac_main.c` 都实现了 `ndo_setup_tc`。

### §8.3 匹配键的解析由 `flow_offload.c` 代劳

分类器那一组的 `type_data` 里装的是一套中间表示——`struct flow_rule`。驱动不用自己解析 netlink 属性，而是用 `net/core/flow_offload.c`（624 行）提供的一大堆取值函数：

| 函数 | 位置 | 取什么 |
|------|------|--------|
| `flow_rule_match_basic()` | `net/core/flow_offload.c:65` | 协议号 |
| `flow_rule_match_eth_addrs()` | `:79` | 源/目的 MAC |
| `flow_rule_match_vlan()` | `:86` | VLAN 标签 |
| `flow_rule_match_ipv4_addrs()` | `:100` | IPv4 地址对 |
| `flow_rule_match_ipv6_addrs()` | `:107` | IPv6 地址对 |
| `flow_rule_match_ports()` | `:121` | 四层端口 |
| `flow_rule_match_ports_range()` | `:128` | 端口范围 |
| `flow_rule_match_enc_*()` | `:156`–`:203` | 隧道内层各字段 |

这一族函数的形状完全一致：传规则和一个输出结构，函数把 key 与 mask 都填好。规则本身由 `flow_rule_alloc()`（`net/core/flow_offload.c:10`）创建。

另一半是「回调块」机制：`TC_SETUP_BLOCK` 让驱动往一个共享的块上注册回调，之后同一个块上的所有规则变更都会通知到。入口是 `flow_block_cb_alloc()`——本树 `drivers/net/` 下 8 个文件用它。

### §8.4 使用面

本树 `drivers/net/ethernet/` 下 `ndo_setup_tc` 有 **49** 个赋值点。这个数字比 `ndo_bpf` 的 33 还大，但要注意**其中相当一部分只支持 `TC_SETUP_QDISC_MQPRIO` 一个类型码**——即只做「把发送队列分组成多个 traffic class」，不做分类器卸载。判断某个驱动的真实卸载能力，要打开它的 `ndo_setup_tc` 看 switch 里有几个 case，不能只看有没有这个回调。

---

## §9 devlink：设备级（而不是接口级）的控制面

### §9.1 它解决什么问题

`net_device` 是**接口**粒度的。但很多东西不属于某一个接口，而属于整块**设备**：固件版本与升级、多个物理端口的拆分与合并、共享资源的配额、硬件健康监控与自恢复、参数持久化。这些放在 `ethtool` 里是错位的——一块四端口网卡会有四份重复的固件信息。

devlink 就是设备粒度的那一层。本树它住在 `net/devlink/`，目录里只有 `Makefile` 和一个 **12530 行**的 `leftover.c`——文件名本身就是历史：这是从 `net/core/devlink.c` 拆出来的过程中间态。

### §9.2 驱动要实现什么

| 动作 | API | 位置 |
|------|-----|------|
| 建 devlink 实例 | `devlink_alloc()` | `include/net/devlink.h:1557`（`static inline`，真身 `devlink_alloc_ns()` `:1554`） |
| 填 ops | `struct devlink_ops` | `include/net/devlink.h:1218` |
| 注册端口 | `devlink_port_register()` | 本树 `drivers/net/` 下 8 个文件用 |
| 把 netdev 关联到端口 | `ndo_get_devlink_port` | `include/linux/netdevice.h:1615` |

⚠️ **`devlink_alloc()` 又是一个 `static inline` 包装**，多出的参数是网络命名空间（与 §7.3 的 `__of_mdiobus_register` 同一个套路）。grep 调用者时两个名字都要查。

`include/net/devlink.h` 里共有 71 个函数指针（该头文件全量口径）——devlink 的回调面比 `ethtool_ops` 还要宽，但**绝大多数驱动只实现三五个**。

### §9.3 顺序：devlink 先，netdev 后

这是本节唯一的硬顺序约束：**`devlink_alloc()` 与端口注册要在 `register_netdev()` 之前完成**。理由是 `ndo_get_devlink_port` 可能在接口一上线就被查询，端口还没注册就会拿到空。

### §9.4 arm64 上会不会碰到

本树 `drivers/net/ethernet/` 下 21 个文件调用 `devlink_alloc`，扩到整个 `drivers/` 是 25 个——**多出的 4 个不在网卡目录下**，devlink 已经被别的子系统借去用了。

五个 arm64 代表驱动里只有 `ti/am65-cpsw-nuss.c` 有 devlink（1 命中）。判据很清楚：**单端口、固件不可在线升级的 SoC 网卡不需要 devlink**；多端口交换式 MAC（cpsw 就是）才需要。

---

## §10 RPS / RFS / XPS：驱动零代码的软件多队列

### §10.1 它解决什么问题

单队列网卡的中断只落在一个 CPU 上，收包处理也就困在那一个核。RPS（Receive Packet Steering）在软件里补上这一层：**收到包之后按流的哈希算出一个目标 CPU，把 skb 塞进那个 CPU 的 backlog 队列，用 IPI 叫它起来处理**。RFS（Receive Flow Steering）再进一步，把目标 CPU 对准「应用进程实际在哪个核上 recv」。XPS（Transmit Packet Steering）是发送侧的对偶：按当前 CPU 选发送队列。这三个的官方原文是 `Documentation/networking/scaling.rst`（523 行）——它同时讲了 RSS（硬件侧的多队列哈希），本节只讲软件这三个。

**本节驱动一行代码都不用写。** 之所以要在本篇出现，是因为它**改变了 03 §A8 的收包路径形状**——开了 RPS 之后，包不是在收包中断那个 CPU 上直接进协议栈，而是先排队、再跨核。

### §10.2 路径怎么改道

改道点在 `netif_rx_internal()`（`net/core/dev.c:5010`）：

| 步骤 | 位置 | 动作 |
|------|------|------|
| 检查开关 | `net/core/dev.c:5019` | `static_branch_unlikely(&rps_needed)` |
| 算目标 CPU | `get_rps_cpu()` `net/core/dev.c:4529` | 哈希 + RFS 的流表查询 |
| 入队 | `enqueue_to_backlog()` `net/core/dev.c:4743` | 挂到目标 CPU 的 `softnet_data.input_pkt_queue` |
| 记下要叫醒谁 | `napi_schedule_rps()` `net/core/dev.c:4682` | 见下面的两条分支 |
| 收尾统一发 IPI | `net_rps_action_and_irq_enable()` `net/core/dev.c:5968` | 由 `net_rx_action()` 在退出前调用（03 §A8 的最后一行） |
| IPI 落地 | `rps_trigger_softirq()` `net/core/dev.c:4667` | 在目标 CPU 上调度伪 NAPI |

⚠️ **`napi_schedule_rps()` 自己不发 IPI**，这是最容易记反的一点。它的两条分支是：

- **目标就是本 CPU**：直接 `__napi_schedule_irqoff(&mysd->backlog)`（`net/core/dev.c:4695`），返回 0；
- **目标是别的 CPU**：把目标的 `softnet_data` 挂进**本 CPU** 的 `rps_ipi_list` 链（`net/core/dev.c:4688-4689`），再置起**本 CPU** 的 `NET_RX_SOFTIRQ`（`:4691`），返回 1。

IPI 真正发出的时刻在 `net_rps_action_and_irq_enable()` 里——它把攒下的整条 `rps_ipi_list` 摘走，**先开中断再** `net_rps_send_ipi()`（`net/core/dev.c:5979`）。**攒起来批量发**，而不是每个包一次 IPI，这就是这一层设计的全部要点。

⚠️ **被叫醒的那个「NAPI」就是 03 §A8.3 讲的 `process_backlog()` 伪 NAPI**——不是驱动的 poll。所以开了 RPS 之后，`/proc/net/softnet_stat` 的第 1 列会出现在「没有收包中断」的 CPU 上。这是排查时最容易看糊的一点。

RFS 侧多一张全局流表 `rps_sock_flow_table`（`net/core/dev.c:4469`），由套接字层在 recv 时写入（`rps_record_sock_flow()`，`include/linux/netdevice.h:750`），结构定义在 `include/linux/netdevice.h:738`。硬件加速版 RFS 还有一个回收判断 `rps_may_expire_flow()`（`net/core/dev.c:4639`）——**这一个是驱动要调的**，实现了 `ndo_rx_flow_steer` 的驱动用它来决定某条硬件过滤规则能不能回收。

### §10.3 三个开关与三个上限

| 名字 | 默认值 | 位置 | 作用 |
|------|--------|------|------|
| `rps_needed` | 关（静态键） | `net/core/dev.c:4474` | RPS 总开关，由 sysfs 写 `rps_cpus` 时开 |
| `rfs_needed` | 关（静态键） | `net/core/dev.c:4476` | RFS 总开关 |
| `netdev_max_backlog` | **1000** | `net/core/dev.c:4419` | 每 CPU backlog 上限，超了丢包 |
| `netdev_tstamp_prequeue` | **1** | `net/core/dev.c:4422` | 时间戳在入队前还是出队后打 |
| `rps_sock_flow_entries` | 0 | `net/core/sysctl_net_core.c:483` | RFS 全局流表大小，写它才真正开 RFS |

两个静态键的增减在 `net/core/sysctl_net_core.c:100-101`（inc）与 `:104-105`（dec）。

`netdev_max_backlog` 被读两次，含义不同，别看混：

| 位置 | 判断 | 含义 |
|------|------|------|
| `net/core/dev.c:4758` | `qlen <= netdev_max_backlog && !skb_flow_limit(skb, qlen)` | **真正的入队门限**，不过就丢包 |
| `net/core/dev.c:4710` | `qlen < (netdev_max_backlog >> 1)` | 在 `skb_flow_limit()`（`net/core/dev.c:4703`）内部，**队列不到半满就不启用流量限制**，直接放行 |

`skb_flow_limit()` 是 `CONFIG_NET_FLOW_LIMIT` 下的一层额外保护：防止单条大流把整个 backlog 占满饿死其他流，表大小由 `netdev_flow_limit_table_len`（`net/core/dev.c:4700`，默认 `1 << 12`）决定。

sysfs 面：

| 文件 | 实现位置 | 粒度 |
|------|----------|------|
| `queues/rx-N/rps_cpus` | `net/core/net-sysfs.c:980-981` | 每接收队列 |
| `queues/rx-N/rps_flow_cnt` | `net/core/net-sysfs.c:984` | 每接收队列 |
| `queues/tx-N/xps_cpus` | `net/core/net-sysfs.c:1539-1540` | 每发送队列 |
| `queues/tx-N/xps_rxqs` | `net/core/net-sysfs.c:1600-1601` | 每发送队列 |
| `queues/tx-N/tx_maxrate` | `net/core/net-sysfs.c:1302-1303` | 每发送队列，会回调 `ndo_set_tx_maxrate` |

XPS 的内核侧入口是 `netif_set_xps_queue()`（`net/core/dev.c:2801`），真正干活的是 `__netif_set_xps_queue()`（`net/core/dev.c:2597`）。驱动一般不调它，但**多队列驱动可以主动设置 XPS 映射**，让「哪个 CPU 发的包走哪条队列」和自己的中断亲和性对齐。

---

## §11 netpoll：在中断不可用的时候还能收发包

### §11.1 它解决什么问题

内核 panic 时、或者 kgdb 停住所有 CPU 时，中断和调度都停了，但你还想把日志送出去（netconsole）或者接受远程调试（kgdboe）。netpoll 就是这条「不依赖中断和软中断」的旁路：**主动轮询网卡**，把包直接塞进发送环，或者直接把收到的包喂给上层。

实现在 `net/core/netpoll.c`（890 行）。

### §11.2 驱动要实现什么

历史上是一个 `ndo_poll_controller` 回调（`include/linux/netdevice.h:1452`），驱动在里面手动跑一遍中断处理。本树 `drivers/net/ethernet/` 下有 **108** 个赋值点——数字很大，因为这是个老回调，很多老驱动都留着。

但**新驱动通常什么都不用写**。`netpoll_poll_dev()`（`net/core/netpoll.c:188`）的顺序是：

```c
	ops = dev->netdev_ops;
	if (ops->ndo_poll_controller)
		ops->ndo_poll_controller(dev);

	poll_napi(dev);
```

出自 `net/core/netpoll.c:208-212`。⚠️ **`poll_napi()` 不是「没有 `ndo_poll_controller` 时的兜底」，它无条件都跑**——两者是先后关系而不是二选一。所以只要驱动是标准 NAPI 结构，不写 `ndo_poll_controller` 也能被 netpoll 驱动起来；写了的话它先跑一遍、`poll_napi()` 再跑一遍。

`poll_napi()`（`net/core/netpoll.c:175`）遍历 `dev->napi_list`，用 `cmpxchg(&napi->poll_owner, -1, cpu)`（`net/core/netpoll.c:181`）抢所有权，抢到才调 `poll_one_napi()`（`net/core/netpoll.c:154`）——**抢不到就跳过这个实例**，绝不等。

另两个可选回调是 `ndo_netpoll_setup`（`include/linux/netdevice.h:1453`）与 `ndo_netpoll_cleanup`（`:1455`），只有需要额外准备的驱动（如 bonding 之类的虚拟设备）才实现。

### §11.3 禁用窗口已经由核心插好

netpoll 和正常路径会打架：正常路径正在 poll 的时候 netpoll 不能插进去。核心的解法是一对禁用/启用：

| 函数 | 位置 | 在 03 里被谁调 |
|------|------|----------------|
| `netpoll_poll_disable()` | `net/core/netpoll.c:220` | `__dev_open()` `net/core/dev.c:1475`、`__dev_close_many()` `:1544` |
| `netpoll_poll_enable()` | `net/core/netpoll.c:233` | `__dev_open()` `net/core/dev.c:1490` 与 `:1494` 一带的失败路径、`__dev_close_many()` `:1575` |

**这两处调用已经在 03 §A5 与 §A9 的顺序表里出现过**——当时只说了「禁用 netpoll」，现在知道它禁的是什么了。驱动不需要自己调这一对。

禁用的实现载体是一个信号量：`netpoll_poll_dev()` 在开头做 `down_trylock(&ni->dev_lock)`（`net/core/netpoll.c:197`），拿不到就直接返回。注释（`net/core/netpoll.c:193-196`）把用途说得很直白——open/close 路径拿着它来阻断 netpoll 活动，因为那两条路径正在改设备状态。⚠️ 是 **`down_trylock`** 而不是 `down`：netpoll 宁可这一轮不收包，也不会在这里睡。同一个函数里还有第二道门 `netif_running(dev) || netif_local_xmit_active(dev)`（`net/core/netpoll.c:203`）。

发送侧的两个并发检查是 `netif_local_xmit_active()`（`net/core/netpoll.c:140`）和 `netpoll_owner_active()`（`:314`）：前者判断本 CPU 是不是正在这块设备的发送路径里，后者判断发送锁是不是已经被本 CPU 持有。撞上就放弃这次发送而不是死等——netpoll 的一切设计都以「绝不阻塞」为前提。

### §11.4 三个能看出设计取向的细节

- **skb 预分配池**：`refill_skbs()`（`net/core/netpoll.c:244`）提前攒一批 skb，因为 panic 上下文不能调用可能睡眠的分配器。取用是 `find_skb()`（`:288`）。
- **发不出去就排队重试**：`queue_process()`（`net/core/netpoll.c:100`）是一个工作队列，把发送失败的包留着以后再试。
- **等 carrier 有超时**：`carrier_timeout` 模块参数（`net/core/netpoll.c:64`），因为 netpoll 建立时链路可能还没起来，而它不能无限等。

### §11.5 arm64 上会不会碰到

只在配了 netconsole 或 kgdboe 时。五个代表驱动里 `mtk_eth_soc.c` 与 `stmmac_main.c` 有 `ndo_poll_controller`、其余三个没有——**没有的那三个照样能被 netpoll 用**，走 §11.2 的 NAPI 遍历兜底。所以这一节对新驱动的实际要求是：**什么都不做，但别把 NAPI 结构写得不标准**。

---

## §12 汇总

### §12.1 框架 × 职责 × 挂载点 × arm64 是否相关

| 节 | 框架 | 职责一句话 | 驱动侧入口 | 挂在 03 时间线 | arm64 相关 |
|----|------|------------|-----------|----------------|-----------|
| §1 | GRO | 收包前把同流小包拼大 | `napi_gro_receive()` | 收包 | **必写** |
| §2 | XDP | 建 skb 之前跑一段 BPF | `ndo_bpf` + `xdp_rxq_info_reg()` | register 后任意时刻 / open | 选写 |
| §3 | AF_XDP | 收发队列整个交给用户态 | `ndo_bpf(XDP_SETUP_XSK_POOL)` + `ndo_xsk_wakeup` | open 后，单队列粒度 | 基本不碰 |
| §4 | page_pool | 收包页面循环用、DMA 映射不解除 | `page_pool_create()` / `_destroy()` | open 建环 / close 拆环 | 建议写 |
| §5 | BQL | 发送队列字节上限自动收敛 | `netdev_tx_sent_queue()` / `_completed_queue()` | 发送 / 发送完成 | 建议写 |
| §6 | ethtool | 通用控制面：查询与调参 | `dev->ethtool_ops` 赋值 | alloc 后、register 前 | **必写** |
| §7 | PHY/phylink | 链路状态与介质管理 | `phylink_create()` / `_start()` / `_stop()` | probe / open / close | **必写** |
| §8 | TC offload | 分类规则与整形器下到硬件 | `ndo_setup_tc` | register 后 | 硬件支持才写 |
| §9 | devlink | 设备级（非接口级）控制面 | `devlink_alloc()` + 端口注册 | **register 之前** | 多端口才写 |
| §10 | RPS/RFS/XPS | 软件补的多队列分流 | 零代码（`ndo_rx_flow_steer` 例外） | 收包（改道 backlog） | 不写但要懂 |
| §11 | netpoll | 中断不可用时的收发旁路 | 零代码（`ndo_poll_controller` 可选） | open/close 两侧 | 按需 |

### §12.2 每个框架的全树使用统计与重跑命令

⚠️ **两种口径不能混**：`-l | wc -l` 数的是**文件数**，`-n | wc -l` 数的是**赋值行数**。同一个框架换口径数字会变。命令一律在 `/workspace/safety/linux-6.1.y` 下执行。

| 框架 | 符号 | 数字 | 口径 | 重跑命令 |
|------|------|------|------|----------|
| GRO | `napi_gro_receive` | 93 | 文件 | `grep -rlw 'napi_gro_receive' drivers/net/ethernet/ --include=*.c \| wc -l` |
| GRO | `napi_gro_frags` | 17 | 文件 | 同上换符号 |
| GRO | `dev_add_offload` 调用点 | 7（6 个文件） | 行 | `grep -rn 'dev_add_offload(' --include=*.c net/ drivers/ \| grep -v 'net/core/gro.c'` |
| XDP | `.ndo_bpf` | 33 | 行 | `grep -rn '\.ndo_bpf' drivers/net/ethernet/ --include=*.c \| wc -l` |
| XDP | `.ndo_xdp_xmit` | 26 | 行 | 同上换符号 |
| XDP | `xdp_rxq_info_reg` | 30 | 文件 | `grep -rlw 'xdp_rxq_info_reg' drivers/net/ethernet/ --include=*.c \| wc -l` |
| AF_XDP | `.ndo_xsk_wakeup` | 7 | 行 | `grep -rn '\.ndo_xsk_wakeup' drivers/net/ethernet/ --include=*.c \| wc -l` |
| page_pool | `page_pool_create` | 12 / 13 | 文件（ethernet / 整个 `drivers/net`） | `grep -rlw 'page_pool_create' drivers/net/ethernet/ --include=*.c \| wc -l`；把路径换成 `drivers/net/` 得 13 |
| ethtool | `ethtool_ops` 实例 | 237 | 行 | `grep -rn 'struct ethtool_ops [a-z_0-9]* = {\|const struct ethtool_ops [a-z_0-9]* = {' drivers/net/ethernet/ --include=*.c \| wc -l` |
| BQL | `netdev_tx_sent_queue` | 32 | 文件 | `grep -rlw 'netdev_tx_sent_queue' drivers/net/ethernet/ --include=*.c \| wc -l` |
| BQL | `__netdev_tx_sent_queue` | 12 | 文件 | 同上换符号 |
| BQL | `netdev_tx_completed_queue` | 42 | 文件 | 同上换符号 |
| BQL | `netdev_tx_reset_queue` | 41 | 文件 | 同上换符号 |
| BQL | `netdev_sent_queue` | 21 | 文件 | 同上换符号 |
| BQL | `netdev_completed_queue` | 22 | 文件 | 同上换符号 |
| PHY | `phy_connect` | 28 | 文件 | `grep -rlw 'phy_connect' drivers/net/ethernet/ --include=*.c \| wc -l` |
| PHY | `phylink_create` | 15 | 文件 | 同上换符号 |
| PHY | `of_mdiobus_register` | 36 | 文件 | 同上换符号（注意它是 inline 包装，见 §7.3） |
| PHY | `phy_driver` 数组定义 | 47 | 行 | `grep -rn 'static struct phy_driver [a-z_0-9]*\[\]' drivers/net/phy/ --include=*.c \| wc -l` |
| TC offload | `.ndo_setup_tc` | 49 | 行 | `grep -rn '\.ndo_setup_tc' drivers/net/ethernet/ --include=*.c \| wc -l` |
| TC offload | `flow_block_cb_alloc` | 8 | 文件 | `grep -rlw 'flow_block_cb_alloc' drivers/net/ --include=*.c \| wc -l` |
| devlink | `devlink_alloc` | 21 / 25 | 文件（ethernet / 整个 `drivers`） | `grep -rlw 'devlink_alloc' drivers/net/ethernet/ --include=*.c \| wc -l`；把路径换成 `drivers/` 得 25 |
| devlink | `devlink_port_register` | 8 | 文件 | `grep -rlw 'devlink_port_register' drivers/net/ --include=*.c \| wc -l` |
| netpoll | `.ndo_poll_controller` | 108 | 行 | `grep -rn '\.ndo_poll_controller' drivers/net/ethernet/ --include=*.c \| wc -l` |

§0.2 分诊表里「代表驱动命中数」的重跑命令（五个驱动 × 九个符号）：

```bash
cd /workspace/safety/linux-6.1.y
for f in ti/am65-cpsw-nuss.c mediatek/mtk_eth_soc.c marvell/mvneta.c \
         marvell/mvpp2/mvpp2_main.c stmicro/stmmac/stmmac_main.c; do
  echo "--- $f"
  for s in napi_gro_receive page_pool_create netdev_tx_sent_queue \
           phylink_create phy_connect ndo_bpf ndo_setup_tc \
           devlink_alloc ndo_poll_controller; do
    printf "  %-24s %s\n" "$s" "$(grep -cw "$s" drivers/net/ethernet/$f)"
  done
done
```

§5.5 那 6 个「有 completed 无 sent」文件的差集命令：

```bash
cd /workspace/safety/linux-6.1.y
comm -13 \
  <(grep -rlw 'netdev_tx_sent_queue\|__netdev_tx_sent_queue' \
      drivers/net/ethernet/ --include=*.c | sort) \
  <(grep -rlw 'netdev_tx_completed_queue' \
      drivers/net/ethernet/ --include=*.c | sort)
```

⚠️ 本环境的 `grep` 是 ugrep：`^(static |)(A|B)` 这种写法会报 `empty (sub)expression`，要写成 `^(static )?(A|B)`。

### §12.3 各框架代码规模

| 框架 | 主要文件 | 行数 |
|------|----------|------|
| GRO | `net/core/gro.c` | 817 |
| XDP | `net/core/xdp.c` | 707 |
| AF_XDP | `net/xdp/`（全目录 `.c`+`.h`） | 3504 |
| page_pool | `net/core/page_pool.c` | 938 |
| page_pool 头 | `include/net/page_pool.h` | 386 |
| BQL 算法 | `lib/dynamic_queue_limits.c` | 138 |
| BQL 头 | `include/linux/dynamic_queue_limits.h` | 108 |
| ethtool | `net/ethtool/`（26 个 `.c`） | 11354 |
| ethtool ioctl 前端 | `net/ethtool/ioctl.c` | 3344 |
| ethtool netlink 前端 | `net/ethtool/netlink.c` | 1079 |
| PHY | `drivers/net/phy/`（85 个文件） | 59610 |
| MDIO | `drivers/net/mdio/`（28 个文件） | 6434 |
| PCS | `drivers/net/pcs/`（6 个文件） | 2678 |
| flow offload | `net/core/flow_offload.c` | 624 |
| devlink | `net/devlink/leftover.c` | 12530 |
| RPS/XPS sysfs | `net/core/net-sysfs.c` | 2077 |
| netpoll | `net/core/netpoll.c` | 890 |

⚠️ **devlink 那一行的 12530 行值得单独看一眼**：它比 `net/core/dev.c` 之外任何一个网络核心文件都大，比整个 `drivers/net/mdio/` 目录还大一倍——而 arm64 SoC 网卡里只有 cpsw 用它。**代码规模和「你要不要写」完全不相关**，这张表和 §0.2 的分诊表要一起看。

---

## §13 相邻但不在本篇的框架

下面这些也挂在网卡驱动旁边，但它们不满足本篇的「关掉它主流程照样跑」判据，或者主体不在 `drivers/net/ethernet/` 侧：

| 框架 | 规模 | 为什么不在本篇 | 去哪儿找 |
|------|------|----------------|----------|
| DSA（分布式交换架构） | `net/dsa/*.c` 共 13492 行，22 个 `dsa_switch_ops` 实例 | 它是**另一类设备**的驱动框架（交换芯片），不是网卡的扩展 | 独立子系统，本套文档未覆盖 |
| switchdev | `net/switchdev/switchdev.c` 948 行 | 同上，服务于交换芯片；网卡侧只留一个 `ndo_get_port_parent_id`（本树 `drivers/net/` 下 12 个赋值点） | 同上 |
| qdisc / 流量控制 | `net/sched/` | 它在 `net_device` **上面**，是队列纪律层而不是驱动扩展；驱动只通过 §8 的卸载与它打交道 | `net/sched/sch_generic.c`（03 §B3 引用过 `dev_watchdog`） |
| TLS / IPsec 卸载 | `net/tls/`、`net/xfrm/` | 协议层卸载，入口不在 `net_device_ops` 而在各协议自己的 ops | 协议子系统 |
| `XDP_REDIRECT` 的 map 层 | `net/core/filter.c`、`kernel/bpf/devmap.c` | 属于 BPF 子系统，网卡侧只需 §2.2 的 `ndo_xdp_xmit` | BPF 子系统 |
| bonding / team / VLAN | `drivers/net/bonding/`、`net/8021q/` | 它们是**虚拟 netdev**，消费 `net_device` 而不扩展驱动 | 02 §8 提到过它们对 features 的影响 |
| 硬件时间戳 / PTP | `drivers/ptp/`、`net/core/timestamping.c` | 驱动侧入口是 `ethtool_ops.get_ts_info` 与 `ndo_eth_ioctl` 里的 `SIOCSHWTSTAMP`，语义整个在 PTP 侧 | `Documentation/networking/timestamping.rst`（772 行），本套未覆盖 |

---

## §14 读下一篇

| 想知道 | 去哪篇 |
|--------|--------|
| 这些框架的入口函数怎么调、签名什么样 | `01-api.md` |
| `page_pool_params`、`netdev_bpf`、`phylink_config` 这些结构体的字段 | `02-datastructures.md` |
| GRO 刷出点、BQL 停唤队列、RPS 改道的完整流程剖析 | `03-internals.md` §A8 / §B3 |
| 写一个带 phylink + page_pool + BQL 的驱动骨架 | `05-usage.md` |
| PHY 与 MDIO 在 DTS 里怎么写、`phy-handle` 谁消费 | `06-devicetree.md` |
| BQL 的 sysfs 怎么看、XDP 装没装上怎么查 | `07-debug.md` |
| 为什么 GRO 能合并、page_pool 为什么快 | `08-feynman.md` |
| 本篇每个数字的证据等级与口径 | `99-evidence.md` |
