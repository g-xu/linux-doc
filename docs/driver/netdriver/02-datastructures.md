---
title: 数据结构
---

# 网卡驱动核心数据结构

这一篇讲结构体：每个字段是什么、谁写谁读、字段之间怎么连。定位是「读代码时手边摊开的那张图」。

读之前先看 §1.0——网卡这块有六组关系特别容易混，混了之后后面的字段表怎么读都是错的。

行号基线 `f6e38ae624cf`（Linux 6.1.158）。本树在这个范围内有一处 PREEMPT_RT 补丁，落点见 §1.0.7 和 §8.3。

---

## §1.0 先分清六组关系

### §1.0.1 `net_device` / `struct device` / 私有区

这是第一组，也是最基础的一组。三者不是三个平行的东西，是**一块内存里的三段**。

先否掉两个常见的错误理解：

| 错误理解 | 实际 |
|---|---|
| `net_device` 里有个指针指向 `struct device` | 是**嵌入**：`struct device dev;`（`include/linux/netdevice.h:2317`），不是指针 |
| 驱动的私有结构体里嵌一个 `net_device` | 反过来——私有区在 `net_device` **后面**，同一次分配，靠 `netdev_priv()` 算偏移拿到 |

摊开对比：

| | `struct net_device` | 内嵌的 `struct device` | 私有区 |
|---|---|---|---|
| 谁分配 | `alloc_netdev_mqs()`（`net/core/dev.c:10716`） | 跟着一起，不单独分配 | 跟着一起，同一次 `kvzalloc` |
| 大小 | `sizeof(struct net_device)`，固定 | 固定 | 由 `alloc_etherdev(sizeof(struct foo_priv))` 的实参决定 |
| 怎么拿到 | 驱动手里就是它 | `&dev->dev` | `netdev_priv(dev)` |
| 反向怎么拿 | `to_net_dev(d)` | —— | 驱动自己在私有结构体里存一个 `netdev` 指针 |
| 谁释放 | `free_netdev()`（`net/core/dev.c:10846`） | 同左 | 同左 |
| 出现在 sysfs 哪里 | `/sys/class/net/eth0`（由内嵌的 `dev` 提供） | 同左，是同一个目录 | 不出现 |

第三行是最要紧的一条：**私有区没有独立的分配和释放**。所以驱动的私有结构体里**不能**放需要单独 `kfree` 的裸内存以外的东西的所有权假设——它随 `free_netdev()` 一起消失，而 `free_netdev()` 的时机由核心决定（见 `01-api.md` §2.1）。

内存布局图见 §1.1。

### §1.0.2 `net_device_ops` 和 `ethtool_ops`

两张回调表，都挂在 `net_device` 上，都由驱动填，看着像一回事。分界线只有一条：**谁来调**。

| | `net_device_ops` | `ethtool_ops` |
|---|---|---|
| 字段 | `dev->netdev_ops`（`include/linux/netdevice.h:2064`） | `dev->ethtool_ops`（`:2106`） |
| 定义 | `include/linux/netdevice.h:1400` | `include/linux/ethtool.h:640` |
| 函数指针成员数 | 86 | 68 |
| 谁调 | 协议栈、驱动模型、rtnetlink——**都在数据路径或控制路径的内核侧** | 只有 `net/ethtool/` 那一层，入口是用户态的 `ethtool` 命令或 netlink |
| 调用频率 | `ndo_start_xmit` 每个包一次 | 人敲一次命令一次 |
| 能不能睡 | 大部分不能（见 `01-api.md` §3.4） | 基本都可以，全程持 rtnl |
| 缺了会怎样 | `ndo_start_xmit` 缺了发不了包；`ndo_open`/`ndo_stop` 缺了 `ip link set up` 失败 | 少一个回调只是对应的 `ethtool` 子命令报 `-EOPNOTSUPP` |

⚠️ 一个具体的判据：**看到"这个回调可以 sleep"就该怀疑它是不是 `ethtool_ops` 的**。`net_device_ops` 里能睡的只有 `ndo_open` / `ndo_stop` / `ndo_change_mtu` / `ndo_set_mac_address` 这一小批持 rtnl 的，其余全在软中断或持 `_xmit_lock` 的上下文里。

两张表的成员数怎么数的，口径见 `99-evidence.md` §3。

### §1.0.3 `napi_struct` / `netdev_queue` / `netdev_rx_queue`

三个都跟"队列"有关，但**只有一个是队列**。

| | `napi_struct` | `netdev_queue` | `netdev_rx_queue` |
|---|---|---|---|
| 定义 | `include/linux/netdevice.h:344` | `:604` | `:775` |
| 是什么 | 一个**收包轮询实例**（不是队列，是"谁来轮询"） | 一个**发送队列**的核心侧状态 | 一个**接收队列**的 sysfs / RPS 侧状态 |
| 数组在哪 | 不是数组，是 `dev->napi_list`（`:2050`）串起来的链表 | `dev->_tx[]`（`:2234`），`num_tx_queues` 个 | `dev->_rx[]`（`:2201`），`num_rx_queues` 个 |
| 谁分配 | 驱动自己（通常嵌在私有结构体里），`netif_napi_add()` 挂进链表 | 核心，`netif_alloc_netdev_queues()`（`net/core/dev.c:10021`） | 核心，`netif_alloc_rx_queues()`（`net/core/dev.c:9954`） |
| 里面有 skb 吗 | 有 GRO 的暂存（`gro_hash[]`、`rx_list`） | 没有——包在 `qdisc` 里 | 没有 |
| 驱动会读写吗 | 会，天天读写 | 只通过 `netif_tx_*` / `netdev_tx_*` 间接碰 | 基本不碰（`xdp_rxq` 除外） |
| 数量关系 | 与队列数**无关**，驱动自己定（常见是每个中断向量一个） | `num_tx_queues` 个，注册后基本固定 | `num_rx_queues` 个 |

三条最容易踩的：

- **`napi_struct` 不是"接收队列"**。一个 NAPI 实例可以轮询多个硬件队列（收 + 发合一是常见做法），也可以多个 NAPI 对一个队列（少见）。`dev->_rx[]` 的下标和 NAPI 之间没有内核维护的对应关系，那个对应关系只存在于驱动自己的私有结构体里。
- **`netdev_rx_queue` 里没有任何驱动状态**。它存在的理由是 RPS 表（`rps_map`、`rps_flow_table`）和 sysfs 目录（`kobj`）。驱动真正的接收环在私有结构体里，核心看不见。
- **发送方向的 `netdev_queue` 里也没有硬件环**。它管的是 `qdisc`、`_xmit_lock`、BQL（`dql`）和停/唤状态位（`state`）。硬件环同样在驱动私有区。

### §1.0.4 四个 features 字段

`net_device` 里有六个 `netdev_features_t`，其中四个构成一组关系。搞错这组的后果是"改了没生效"或者"改了之后掉包"。

| 字段 | 位置 | 谁写 | 含义 |
|---|---|---|---|
| `hw_features` | `include/linux/netdevice.h:2079` | 驱动，probe 时一次 | 硬件**能**做的，也就是"用户允许改哪些位" |
| `wanted_features` | `:2080` | 核心（用户态 `ethtool -K` 落到这里） | 用户**希望**开哪些 |
| `features` | `:2078` | 核心算出来 | 当前**实际**生效的 |
| `vlan_features` | `:2081` | 驱动 | 这个设备之上的 VLAN 子设备能继承哪些 |

关系是单向的：`wanted_features` 与 `hw_features` 求交、再叠上不可改的位，得到 `features`。算这一步的是 `netdev_update_features()`（`net/core/dev.c:9901`）。

⚠️ 两条一定要记：

- **驱动只该在 probe 里写 `hw_features` 和 `features` 的初值**，之后要改就调 `netdev_update_features()`，不要直接赋值 `dev->features`——直接赋值不会走 `ndo_set_features`，硬件不知道。
- **`vlan_features` 不是 `features` 的子集也不报错**。写宽了的后果是 VLAN 子设备宣称支持某个 offload 而底层其实不支持，表现为收发正常但校验和错误——这类问题极难定位。

另外两个 `netdev_features_t` 与这组无关：`hw_enc_features`（`:2082`，隧道内层的能力）和 `mpls_features`（`:2083`）。`gso_partial_features`（`:2084`）是第七个，语义又不同。

### §1.0.5 `qdisc` 队列 / `netdev_queue` / 硬件环

发送方向上有三层「队列」，名字里都带 queue，但是三个东西：

```
   协议栈
     │  dev_queue_xmit()
     ▼
  ┌─────────────────────────────────────┐
  │ qdisc  ← 软件排队规则（pfifo_fast/mq/…）│   包真正排在这里
  │        struct Qdisc，net/sched/ 管    │
  └─────────────────────────────────────┘
     │  qdisc->dequeue() → dev_hard_start_xmit()
     ▼
  ┌─────────────────────────────────────┐
  │ netdev_queue  ← 核心侧的一个发送口     │   不存包，只存锁 / BQL / 停唤位
  │        dev->_tx[i]                   │
  └─────────────────────────────────────┘
     │  ndo_start_xmit()
     ▼
  ┌─────────────────────────────────────┐
  │ 驱动私有的发送环 ← 真正的硬件描述符环   │   包的 DMA 地址排在这里
  │        驱动自己定义，核心看不见         │
  └─────────────────────────────────────┘
```

三条推论：

- **`netif_stop_queue()` 停的是 `netdev_queue`，不是硬件环**。它只是置一个位（`__QUEUE_STATE_DRV_XOFF`），让 `qdisc` 不再往下送。硬件环里已经排着的包不受影响，会继续发完。
- **"队列满了"这句话要问清哪一层**。硬件环满 → 驱动要 `netif_stop_queue()`；`qdisc` 满 → 包被丢弃并计入 `tx_dropped`，驱动完全看不到。
- **BQL 管的是"允许 `qdisc` 往下送多少字节"**，它的状态（`dql`）住在 `netdev_queue` 里，但它统计的是硬件环里未完成的字节数。所以 BQL 是唯一一个跨两层的机制，也是唯一需要驱动在完成中断里主动记账的（`netdev_tx_completed_queue()`）。

### §1.0.6 三个统计来源

`net_device` 上跟统计有关的字段有三处，语义完全不同：

| 字段 | 位置 | 谁写 | 谁读 | 用途 |
|---|---|---|---|---|
| `stats` | `include/linux/netdevice.h:2094` | 驱动（老写法） | `dev_get_stats()` | 注释原文写了 "not used by modern drivers"——新驱动不要用 |
| `core_stats` | `:2096` | **只有核心**（per-CPU） | 同上，合并进结果 | 核心自己丢的包：`rx_dropped` / `tx_dropped` / `rx_nohandler` / `rx_otherhost_dropped` |
| `tstats` / `lstats` / `dstats`（union） | `:2302-2306` | 驱动（per-CPU，配 `pcpu_stat_type`） | `dev_fetch_sw_netstats()` 等 | 现代 per-CPU 统计的标准位置 |

⚠️ **`core_stats` 驱动一个字节都不能写。** 它的四个计数由 `dev_core_stats_*_inc()` 这类核心辅助函数维护，代表"包到了核心但没能继续走"。驱动往里写会让"丢包发生在哪一层"这个信息彻底失真。

第三行那个 union 用 `dev->pcpu_stat_type`（`:2301`，8 位枚举）区分类型，枚举在 `:1750-1755`。三种 per-CPU 结构体的字段见 §6。

### §1.0.7 本子系统有几层可插拔

| 层 | 抽象 | 实现者住在 | 实现数（本树） |
|---|---|---|---|
| 设备操作 | `struct net_device_ops` | `drivers/net/ethernet/` 及其他 net 驱动 | `drivers/net/ethernet/` 下 284 个实例 |
| ethtool 操作 | `struct ethtool_ops` | 同上 | 同上目录 237 个实例 |
| 二层封装 | `struct header_ops`（`include/linux/netdevice.h:303`） | `net/ethernet/eth.c` 提供以太网那一套，`ether_setup()` 装上 | 以太网驱动基本不自己实现 |
| 排队规则 | `struct Qdisc_ops` | `net/sched/` | 与驱动无关，驱动只感知 `dev->qdisc` |
| 收包分流 | `rx_handler`（`dev->rx_handler`） | bonding / bridge / macvlan | 驱动不参与 |

也就是说**驱动真正要填的是前两层**，第三层由 `ether_setup()` 代填，第四五层驱动碰不到。这跟有 `bus_type` 的子系统不一样——网卡驱动没有"我实现一个总线"这个角色（判定过程见 `99-evidence.md` §1）。

### §1.0.8 字段随配置分叉的三处

结构体定义里带 `#ifdef` 的地方，读代码时最容易看漏。本子系统有三处值得单独记：

| 位置 | 分叉条件 | 两侧分别是什么 |
|---|---|---|
| `include/linux/netdevice.h:3202-3206` | `CONFIG_PREEMPT_RT` | 关闭时 `call_single_data_t defer_csd`（走 IPI），打开时 `struct work_struct defer_work`（走工作队列）。**这是本树补丁的落点**，详见 §8.3 |
| `include/linux/netdevice.h:2265-2267` | `CONFIG_PCPU_DEV_REFCNT` | 打开时 `int __percpu *pcpu_refcnt`，关闭时 `refcount_t dev_refcnt`。`dev_hold()`/`dev_put()` 两侧实现完全不同 |
| `include/linux/u64_stats_sync.h:64-68` | `BITS_PER_LONG == 32` | 32 位上 `struct u64_stats_sync` 里有一个 `seqcount_t seq`，64 位上**是个空结构体**。这解释了为什么 arm64 上 `u64_stats_fetch_begin()` 几乎零开销 |

第三处的实际影响：arm64 是 64 位，所以 `u64_stats_*` 那一套的读侧循环在本架构上是空转。但驱动代码仍然必须写全，因为同一份代码要在 32 位架构上编译。

---

## §1.1 关系图与内存布局

### §1.1.1 `net_device` 挂在哪

门 1 的判定结果是「本子系统没有自己的 `bus_type`」（`99-evidence.md` §1），所以这里画的不是"两条总线怎么缝"，而是**`net_device` 与驱动模型的接合点**：

```
  /sys/devices/platform/…/ethernet@ff0e0000     ← 控制器的 platform_device
        │                                          （由 DTS 展开，见 06 篇）
        │  probe() 里 alloc_etherdev() + SET_NETDEV_DEV(ndev, &pdev->dev)
        ▼
  ┌──────────────────────────────────┐
  │ struct net_device                │
  │   ├─ struct device dev;  ────────┼──► dev.parent = &pdev->dev
  │   │      (:2317)                 │    dev.class  = &net_class
  │   │                              │       net/core/net-sysfs.c:1996
  │   ├─ netdev_ops   ← 驱动填        │
  │   ├─ ethtool_ops  ← 驱动填        │
  │   └─ …                           │
  └──────────────────────────────────┘
        │
        ├──► /sys/class/net/eth0            （net_class 提供的视图）
        └──► /sys/devices/platform/…/net/eth0  （拓扑视图，同一个目录）
```

要点三条：

- **`net_device` 不注册在任何总线上**，`dev->dev.bus` 是 NULL。它只有 `class`。所以它**不会被匹配、不会被 probe**——驱动是先有 `platform_device` 被 probe，然后在 probe 里造出 `net_device`。
- 缝合点是 `SET_NETDEV_DEV()`，它只做一件事：设 `dev.parent`。不调它设备也能注册成功，代价是 sysfs 里飘在 `/sys/devices/virtual/net/` 下面，`ethtool -i` 看不到 bus-info。
- `net_class` 的定义在 `net/core/net-sysfs.c:1926`，`class_register()` 在 `:2076`。属性组是 `net_class_attrs[]`（`:621`）。

### §1.1.2 一次分配，三段内存

`alloc_netdev_mqs()`（`net/core/dev.c:10716`）只做一次 `kvzalloc`。布局是这样：

```
  p = kvzalloc(alloc_size)          ← 原始指针，free 时要用
  │
  │  0 ~ padded 字节：对齐填充（dev->padded 记着有几个字节）
  │     net/core/dev.c:10750-10751
  ▼
  dev = PTR_ALIGN(p, 32)            ← 32 字节对齐，NETDEV_ALIGN
  ┌────────────────────────────────────────────┐  ← 驱动手里的 net_device *
  │ struct net_device                          │
  │   name / netdev_ops / features / _tx / _rx │
  │   … 约 167 个字段 …                         │
  │   struct device dev;         (:2317)       │
  └────────────────────────────────────────────┘
  │  ALIGN(sizeof(struct net_device), 32) 处
  ▼
  ┌────────────────────────────────────────────┐  ← netdev_priv() 返回这里
  │ 驱动私有区 sizeof_priv 字节                  │     include/linux/netdevice.h:2561
  │   struct foo_priv { … 发送环 / 接收环 / … } │
  └────────────────────────────────────────────┘
```

对应的三行源码（`net/core/dev.c:10737-10744`）：

```c
	alloc_size = sizeof(struct net_device);
	if (sizeof_priv) {
		/* ensure 32-byte alignment of private area */
		alloc_size = ALIGN(alloc_size, NETDEV_ALIGN);
		alloc_size += sizeof_priv;
	}
	/* ensure 32-byte alignment of whole construct */
	alloc_size += NETDEV_ALIGN - 1;
```

`NETDEV_ALIGN` 定义在 `include/linux/netdevice.h:2386`，值是 32。

四条推论：

- **`netdev_priv()` 是纯算术，没有查表**（`include/linux/netdevice.h:2559-2562`）。所以它可以在任何上下文调，包括中断里，代价是零。
- **私有区一定是 32 字节对齐的**，驱动可以放心在里面用 `____cacheline_aligned` 之类的标注（arm64 的 cacheline 是 64 字节，需要更强对齐时仍要自己写明）。
- **`dev->padded`（`:2140`）是给 `free_netdev()` 用的**，从 `dev` 减回 `p`。驱动不要读它。
- **`_tx[]` 和 `_rx[]` 不在这块内存里**。它们是 `dev->_tx` / `dev->_rx` 两个指针，指向另外两次分配（`netif_alloc_netdev_queues()` / `netif_alloc_rx_queues()`）。这是"一次分配"这个说法的唯一例外。

### §1.1.3 `dev_addr` 指向哪

一个容易看错的间接：`dev->dev_addr`（`include/linux/netdevice.h:2199`）是 `const unsigned char *`，不是数组。它指向 `dev->dev_addrs` 列表里**第一个** `netdev_hw_addr` 的 `addr` 字段：

```
  dev->dev_addrs  (struct netdev_hw_addr_list, :2147)
    └─ list ──► netdev_hw_addr #1 ─┬─ addr[MAX_ADDR_LEN] ◄──── dev->dev_addr 指这里
                                   └─ type = NETDEV_HW_ADDR_T_LAN
                    netdev_hw_addr #2 …（多 MAC 的设备才有）
```

建立这条指向的是 `dev_addr_init()`（`net/core/dev_addr_lists.c:548`），关键三行在 `:565-567`。

⚠️ 这就是为什么**改 MAC 地址必须用 `eth_hw_addr_set()` 而不能 `memcpy(dev->dev_addr, …)`**：类型是 `const`，绕过去写虽然内存改对了，但 `netdev_hw_addr` 的 rbtree 索引不会更新，地址表的查找会和实际不一致。这条约束在 6.1 之前不存在（那时 `dev_addr` 是数组），是本版本时效性上最容易踩的一处。

---

## §1.2 核心操作数据流图

### §1.2.1 发送

```
 应用 write()
   │
 协议栈 (TCP/IP)
   │  dev_queue_xmit(skb)                include/linux/netdevice.h:3049
   ▼
 __dev_queue_xmit()                      net/core/dev.c:4252
   │  ① 选队列：netdev_pick_tx()          net/core/dev.c:4176
   │       → skb->queue_mapping
   │  ② txq = netdev_get_tx_queue(dev, i) include/linux/netdevice.h:2443
   │  ③ 有 qdisc → __dev_xmit_skb() 入队 / 出队
   │     无 qdisc（IFF_NO_QUEUE）→ 直接往下
   ▼
 dev_hard_start_xmit()                   net/core/dev.c:3676
   │  持 txq->_xmit_lock（除非 NETIF_F_LLTX）
   ▼
 ndo_start_xmit(skb, dev)                ← 驱动的函数
   │  ④ 填硬件描述符环（驱动私有区）
   │  ⑤ netdev_tx_sent_queue(txq, len)   BQL 记账
   │  ⑥ 环快满 → netif_stop_queue()      置 __QUEUE_STATE_DRV_XOFF
   ▼
 硬件 DMA 出去
   │
   │ 发送完成中断
   ▼
 驱动的完成处理（通常在 NAPI poll 里）
   ⑦ napi_consume_skb() / dev_kfree_skb_any()
   ⑧ netdev_tx_completed_queue(txq, pkts, bytes)   BQL 归还
   ⑨ 环有空位 → netif_wake_queue()                 清 DRV_XOFF
```

⑤⑧ 必须配对，⑥⑨ 必须配对。任一处漏掉的症状见 `07-debug.md`。

### §1.2.2 接收

```
 硬件收到包 → DMA 进驱动的接收环 → 触发中断
   │
 驱动的 ISR
   │  ① napi_schedule(&priv->napi)      include/linux/netdevice.h:480
   │     （置 NAPI_STATE_SCHED，挂进 softnet_data.poll_list）
   │  ② 关硬件中断
   ▼
 NET_RX_SOFTIRQ → net_rx_action()
   │  ③ 调 napi->poll(napi, budget)
   ▼
 驱动的 poll 函数
   │  ④ 从接收环取描述符
   │  ⑤ 建 skb（page_pool / build_skb / netdev_alloc_skb）
   │  ⑥ 填 skb->protocol、校验和状态、hash
   │  ⑦ napi_gro_receive(napi, skb)     net/core/gro.c:649
   │  ⑧ 收够 budget → 返回 budget（核心会再调）
   │     收完了   → napi_complete_done() 然后开中断
   ▼
 GRO 聚合（napi->gro_hash[]）
   │
 netif_receive_skb() 系列               net/core/dev.c:5825
   │  ⑨ ptype_all / rx_handler / ptype_specific
   ▼
 协议栈 (IP/TCP) → 应用 read()
```

⚠️ ⑧ 那个分叉是 NAPI 契约里唯一没有编译器帮你检查的地方：**返回 `budget` 表示"还有活"，返回小于 `budget` 的值表示"干完了"，而后者必须先调 `napi_complete_done()`**。顺序倒过来（先开中断再 complete）会丢一次调度。

### §1.2.3 三条路径的交汇点

```
             ┌─────────────────────────────────────┐
   发送 ─────┤ netdev_queue (dev->_tx[i])          │
             │   state / _xmit_lock / dql / qdisc  │
             └─────────────────────────────────────┘

             ┌─────────────────────────────────────┐
   接收 ─────┤ napi_struct (驱动私有区里)            │
             │   state / poll / gro_hash           │
             │        ↕ 挂进                        │
             │ softnet_data (per-CPU, :3153)       │
             │   poll_list / backlog / defer_list  │
             └─────────────────────────────────────┘

             ┌─────────────────────────────────────┐
   控制 ─────┤ net_device 本体                      │
             │   flags / features / stats / uc / mc │
             │   全部在 rtnl_lock 保护下改           │
             └─────────────────────────────────────┘
```

三条路径几乎不共享数据结构——这是网卡驱动能做到高并发的根本原因，也是"哪个字段在哪把锁下"这个问题必须逐字段回答的原因（见 §9）。

---

## §1.3 命名约定速查

前缀能告诉你「谁调它、在哪个上下文」，比读函数体快：

| 前缀 | 含义 | 谁实现 | 谁调 | 例 |
|---|---|---|---|---|
| `ndo_` | `net_device_ops` 的成员名 | 驱动 | 核心 | `ndo_start_xmit` / `ndo_open` |
| `netif_` | 供驱动调的核心接口（多为 `static inline`） | 核心 | 驱动 | `netif_rx` / `netif_stop_queue` / `netif_carrier_on` |
| `dev_` | 操作一个 `net_device` 的核心函数，多数持 rtnl | 核心 | 核心 / rtnetlink | `dev_open` / `dev_set_mtu` / `dev_queue_xmit` |
| `netdev_` | 与 `net_device` 相关的辅助、或 BQL 一族 | 核心 | 两侧都有 | `netdev_priv` / `netdev_tx_sent_queue` / `netdev_update_features` |
| `__netif_` / `__dev_` | 同名函数的"调用者已持锁"版本 | 核心 | 核心 | `__dev_open` / `__netif_tx_lock` |
| `eth_` / `ether_` | 以太网专用（`net/ethernet/eth.c`） | 核心 | 驱动 | `ether_setup` / `eth_hw_addr_set` / `eth_validate_addr` |
| `napi_` | NAPI 一族 | 核心 | 驱动 | `napi_schedule` / `napi_complete_done` / `napi_gro_receive` |
| `NETIF_F_` | features 位 | —— | —— | `NETIF_F_SG` / `NETIF_F_HW_CSUM` |
| `IFF_` | `dev->flags` / `dev->priv_flags` 的位 | —— | —— | `IFF_UP` / `IFF_NO_QUEUE` |
| `__LINK_STATE_` | `dev->state` 的位 | —— | —— | `__LINK_STATE_START` |
| `__QUEUE_STATE_` | `netdev_queue->state` 的位 | —— | —— | `__QUEUE_STATE_DRV_XOFF` |
| `NAPI_STATE_` | `napi->state` 的位 | —— | —— | `NAPI_STATE_SCHED` |

⚠️ 两组容易混的前缀：

- **`netif_` 和 `netdev_` 不是同一族**。`netif_*` 大多操作"接口状态"（起停、载波、队列），`netdev_*` 里混着两类：BQL 记账（`netdev_tx_*`）和纯辅助（`netdev_priv`、`netdev_err`）。判断办法是看第一个参数——`netdev_tx_*` 收的是 `struct netdev_queue *`，`netif_*` 收的是 `struct net_device *`。
- **`dev_` 前缀在网络代码里指 `net_device`，在驱动模型里指 `struct device`**。同一份 .c 文件里两者可能同时出现（`dev_get_by_name()` 和 `dev_err()`），靠参数类型区分。

---

## §2 `struct net_device`

定义在 `include/linux/netdevice.h:2029`，到 `:2325` 结束，约 167 个字段。这是内核里最大的设备结构体之一。

先读它自己的自白（`include/linux/netdevice.h:1757-1762`）：

```
 *	struct net_device - The DEVICE structure.
 *
 *	Actually, this whole structure is a big mistake.  It mixes I/O
 *	data with strictly "high-level" data, and it has to know about
 *	almost every data structure used in the INET module.
```

`:2025-2026` 还有一条 `FIXME: cleanup struct net_device such that network protocol info moves out.`。这两处注释是理解这个结构体的钥匙：**它不是设计出来的，是长出来的**。所以字段不按逻辑分组，而是按历史顺序堆叠——读的时候必须靠下面的分组表重排，不能顺着行号读。

设计债的完整清单见 `03-internals.md` Part C。

### §2.1 身份与名字

| 字段 | 行 | 类型要点 | 谁写 |
|---|---|---|---|
| `name[IFNAMSIZ]` | `:2030` | 16 字节含结尾 NUL，实际可用 15 字符 | `alloc_netdev_mqs()` 的实参，或 `dev_change_name()` |
| `name_node` | `:2031` | 哈希表节点，`dev_get_by_name()` 用它查 | 核心 |
| `ifalias` | `:2032` | RCU 保护的别名字符串，`ip link set alias` 写 | 核心 |
| `ifindex` | `:2065` | 全局唯一接口号，注册时分配 | 核心 |
| `index_hlist` | `:2229` | 按 `ifindex` 查的哈希节点 | 核心 |
| `group` | `:2092` | 接口组，`ip link set group` 用 | 核心 |
| `type` | `:2088` | `ARPHRD_ETHER` 等，`ether_setup()` 设 | `setup` 回调 |
| `name_assign_type` | `:2090` | 名字是怎么来的（内核给 / 用户给），影响 udev 改名策略 | `alloc_netdev_mqs()` 实参 |

⚠️ **`dev->name` 在注册前后都可能变**。注册时如果名字里有 `%d`，`register_netdevice()` 会替换成实际编号；注册后 udev 还可能改名。所以驱动**不要缓存 `dev->name`**，打日志一律用 `netdev_err(dev, …)` 而不是 `pr_err("%s: …", cached_name)`。

### §2.2 状态与链表

| 字段 | 行 | 说明 |
|---|---|---|
| `state` | `:2047` | 位图，位定义见 §2.2.1。⚠️ 与 `flags` 完全不同 |
| `flags` | `:2062` | `IFF_UP` 等**用户态可见**的标志，`ip link` 显示的就是它 |
| `priv_flags` | `:2063` | `IFF_*` 里的内核私有部分，枚举在 `:1675` |
| `gflags` | `:2066` | `flags` 的影子，记录用户显式设过哪些位 |
| `operstate` | `:2124` | RFC 2863 的运行状态（`IF_OPER_UP` 等），`ip link` 里那个 `state UP` |
| `link_mode` | `:2125` | `operstate` 的推导策略（默认 / dormant） |
| `dev_list` | `:2049` | 挂进 `net->dev_base_head`，全局设备链表 |
| `napi_list` | `:2050` | 本设备的所有 NAPI 实例 |
| `unreg_list` | `:2051` | 批量注销时的临时链表 |
| `close_list` | `:2052` | 批量关闭时的临时链表 |
| `todo_list` | `:2262` | `netdev_run_todo()` 的待办链表 |
| `link_watch_list` | `:2271` | 载波变化的延迟处理队列（`net/core/link_watch.c`） |
| `unlink_list` | `:2153` | 上下层设备解链的临时链表 |
| `adj_list`（匿名 struct） | `:2056` | 上层/下层设备链表（bonding、VLAN 用） |
| `upper_level` / `lower_level` | `:2134` / `:2135` | 层叠深度，防止无限嵌套 |
| `nested_level` | `:2159` | 锁嵌套深度标注，给 lockdep 用 |
| `dismantle` | `:2281` | 正在拆除的标记 |

#### §2.2.1 `dev->state` 的位

枚举 `enum netdev_state_t` 在 `include/linux/netdevice.h:321-328`：

| 位 | 行 | 含义 | 怎么读 |
|---|---|---|---|
| `__LINK_STATE_START` | `:322` | 设备已 open（`ndo_open` 成功过） | `netif_running(dev)`（`:3592`） |
| `__LINK_STATE_PRESENT` | `:323` | 硬件在位（热插拔的设备可能不在） | `netif_device_present(dev)`（`:4265`） |
| `__LINK_STATE_NOCARRIER` | `:324` | **没有**载波（注意是反的） | `netif_carrier_ok(dev)`（`:4152`）返回它的取反 |
| `__LINK_STATE_LINKWATCH_PENDING` | `:325` | 载波变化正在排队处理 | 核心内部用 |
| `__LINK_STATE_DORMANT` | `:326` | 休眠（等 802.1X 认证之类） | `netif_dormant()` |
| `__LINK_STATE_TESTING` | `:327` | 自检中，`ethtool -t` 期间 | `netif_testing()` |

⚠️ 两处反直觉：

- **`__LINK_STATE_NOCARRIER` 是"没有载波"**，所以 `alloc_netdev` 之后这个位是清的，等于"有载波"。驱动如果链路实际是断的，**必须在注册前调 `netif_carrier_off()`**，否则会有一个短暂的假 UP 窗口。
- **`state` 里没有 `IFF_UP`**。`IFF_UP` 在 `flags` 里，而 `__LINK_STATE_START` 在 `state` 里，两者含义接近但不同步：`dev_open()` 先置 `START` 再置 `IFF_UP`（`net/core/dev.c:1455` 起）。驱动一律用 `netif_running()` 判断，不要自己看 `flags & IFF_UP`。

### §2.3 回调表与操作集

| 字段 | 行 | 类型 | 必填 |
|---|---|---|---|
| `netdev_ops` | `:2064` | `struct net_device_ops *` | 是 |
| `ethtool_ops` | `:2106` | `struct ethtool_ops *` | 强烈建议 |
| `header_ops` | `:2122` | `struct header_ops *` | `ether_setup()` 代填 |
| `l3mdev_ops` | `:2108` | L3 主设备（VRF） | 否，普通网卡不填 |
| `ndisc_ops` | `:2111` | IPv6 邻居发现定制 | 否 |
| `xfrmdev_ops` | `:2115` | IPsec offload，结构体在 `:1041` | 否 |
| `tlsdev_ops` | `:2119` | TLS offload | 否 |
| `rtnl_link_ops` | `:2321` | 虚拟设备的创建/销毁（`ip link add` 用） | 物理网卡不填 |
| `wireless_handlers` / `wireless_data` | `:2103` / `:2104` | 老 WEXT 接口 | 以太网不涉及 |

`net_device_ops` 的成员分组和契约在 `01-api.md` §3。这里只强调一条：**这些指针注册后不该改**。核心在多个上下文里无锁读 `dev->netdev_ops`，运行期替换是数据竞争。唯一合法的动态切换是 `dev->ethtool_ops`，而实践中也没人这么做。

### §2.4 二层参数

| 字段 | 行 | 谁设 | 说明 |
|---|---|---|---|
| `mtu` | `:2074` | `ether_setup()` 设 1500，用户可改 | 改的入口是 `dev_set_mtu()`（`net/core/dev.c:8810`） |
| `min_mtu` / `max_mtu` | `:2086` / `:2087` | 驱动 | 核心据此校验用户的 MTU，**驱动不填就是 0，等于任何值都拒**（`ether_setup()` 填了 68 / 1500） |
| `hard_header_len` | `:2067` | `ether_setup()` 设 `ETH_HLEN` | 二层头长度 |
| `min_header_len` | `:2089` | 同上 | 收包时的最短头校验 |
| `needed_headroom` | `:2075` | 驱动 | 驱动要求 skb 前面预留多少字节 |
| `needed_tailroom` | `:2076` | 驱动 | 同理，尾部 |
| `addr_len` | `:2133` | `ether_setup()` 设 6 | MAC 地址长度 |
| `type` | `:2088` | `ether_setup()` 设 `ARPHRD_ETHER` | 见 §2.1 |
| `tx_queue_len` | `:2238` | `ether_setup()` 设 `DEFAULT_TX_QUEUE_LEN` | qdisc 的默认深度 |
| `broadcast[MAX_ADDR_LEN]` | `:2225` | `ether_setup()` 填全 ff | 广播地址 |
| `if_port` | `:2127` | 驱动 | 端口类型（TP / AUI / …），老字段 |
| `dma` | `:2128` | —— | ISA 时代的 DMA 通道号，arm64 上无意义 |
| `mem_start` / `mem_end` / `base_addr` | `:2038` / `:2037` / `:2039` | —— | 同上，`ifconfig` 还在显示 |

⚠️ **`needed_headroom` 写小了的后果是隐性的**：协议栈会尽量按它预留，不够时 `skb_realloc_headroom()` 会做一次拷贝，性能掉但功能正常。所以这个字段写错不会报错，只会慢——属于必须靠 review 而不是靠测试发现的一类。

### §2.5 features 六个半

| 字段 | 行 | 见 |
|---|---|---|
| `features` | `:2078` | §1.0.4 |
| `hw_features` | `:2079` | 同上 |
| `wanted_features` | `:2080` | 同上 |
| `vlan_features` | `:2081` | 同上 |
| `hw_enc_features` | `:2082` | 隧道内层能力 |
| `mpls_features` | `:2083` | MPLS 封装能力 |
| `gso_partial_features` | `:2084` | 支持 GSO partial 的协议位 |

位定义在 `include/linux/netdev_features.h`，枚举从 `:14` 开始，共 63 个位（`ethtool -k` 里可见的是 62 个，见 `01-api.md` §9）。三个组合掩码值得记：`NETIF_F_NEVER_CHANGE`（`:195`，永不可改的位）、`NETIF_F_SOFT_FEATURES`（`:245`）、`NETIF_F_SOFT_FEATURES_OFF`（`:248`）。

相关的两个 GSO 上限也在这一带：`gso_max_size` 与 `gso_max_segs`，宏 `GSO_MAX_SEGS`（`include/linux/netdevice.h:2324`，65535）和 `GSO_LEGACY_MAX_SIZE`（`:2325`，65536）。接收侧对应 `gro_max_size`（`:2213`）。

### §2.6 统计字段

| 字段 | 行 | 见 |
|---|---|---|
| `stats` | `:2094` | §1.0.6，注释明写 "not used by modern drivers" |
| `core_stats` | `:2096` | §1.0.6，per-CPU，驱动禁写 |
| `pcpu_stat_type:8` | `:2301` | 选择下面那个 union 的类型，枚举在 `:1750-1755` |
| `lstats` / `tstats` / `dstats`（union） | `:2302-2306` | §6 |
| `carrier_up_count` | `:2099` | 载波变 up 的次数（`atomic_long_t`） |
| `carrier_down_count` | `:2100` | 同理 |

`carrier_*_count` 由 `netif_carrier_on()` / `netif_carrier_off()` 自动累加，驱动不用管，用户态在 `/sys/class/net/eth0/carrier_up_count` 读。链路抖动排查时这两个数比 dmesg 可靠。

### §2.7 MAC 地址与地址表

| 字段 | 行 | 说明 |
|---|---|---|
| `dev_addr` | `:2199` | `const unsigned char *`，指向 `dev_addrs` 的第一项，见 §1.1.3 |
| `dev_addrs` | `:2147` | 本设备自己的地址列表（多 MAC 设备会有多项） |
| `perm_addr[MAX_ADDR_LEN]` | `:2131` | 永久地址（固件烧的），`ethtool -P` 显示 |
| `addr_assign_type` | `:2132` | 地址是哪来的（永久 / 随机 / 偷的 / 设的） |
| `addr_len` | `:2133` | 见 §2.4 |
| `uc` | `:2145` | 额外的单播地址表（`netdev_hw_addr_list`） |
| `mc` | `:2146` | 组播地址表 |
| `addr_list_lock` | `:2142` | 保护 `uc` / `mc` / `dev_addrs` 的自旋锁 |
| `uc_promisc` | `:2157` | 因为单播表溢出而进了 promisc |
| `promiscuity` | `:2155` | promisc 模式的引用计数（不是布尔） |
| `allmulti` | `:2156` | allmulti 的引用计数 |
| `neigh_priv_len` | `:2137` | 邻居表项的额外私有长度 |

⚠️ **`promiscuity` 和 `allmulti` 是计数不是开关**。多个抓包程序同时开 promisc 时它是 2，一个退出后仍是 1，硬件不能关。驱动的 `ndo_set_rx_mode` 只该看 `dev->flags & IFF_PROMISC`，不要自己解释这两个计数。

结构体细节见 §7。

### §2.8 发送侧字段

| 字段 | 行 | 说明 |
|---|---|---|
| `_tx` | `:2234` | `struct netdev_queue *` 数组，带 `____cacheline_aligned_in_smp` |
| `num_tx_queues` | `:2235` | 分配了多少个（`alloc_etherdev_mqs()` 的实参） |
| `real_num_tx_queues` | `:2236` | 当前实际启用多少个，`netif_set_real_num_tx_queues()`（`net/core/dev.c:2937`）改 |
| `qdisc` | `:2237` | 根 qdisc |
| `tx_queue_len` | `:2238` | 见 §2.4 |
| `tx_global_lock` | `:2239` | `NETIF_F_LLTX` 设备用的全局发送锁 |
| `xps_maps[XPS_MAPS_MAX]` | `:2244` | XPS 映射（CPU → 队列），类型在 `:833` |
| `xdp_bulkq` | `:2241` | XDP 批量转发的 per-CPU 暂存 |
| `watchdog_timer` | `:2257` | 发送超时定时器 |
| `watchdog_timeo` | `:2258` | 超时阈值，驱动在 probe 里设 |
| `proto_down_reason` | `:2260` | 协议层强制 down 的原因位图 |
| `miniq_egress` | `:2247` | 出方向的 tc 快路径 |
| `nf_hooks_egress` | `:2250` | 出方向的 netfilter 钩子 |

⚠️ **`num_tx_queues` 和 `real_num_tx_queues` 的区别是发送路径最容易出错的一处**。`netdev_pick_tx()` 用的是 `real_num_tx_queues`，而驱动分配硬件环时按 `num_tx_queues`。改 `real_num_*` 必须在持 rtnl 且设备 down 的时候，否则正在跑的包可能选到一个已经被停用的队列。

`watchdog_timeo` 为 0 表示不启用发送超时检测。⚠️ 设了这个值就**必须**实现 `ndo_tx_timeout`，否则超时会走到 NULL 指针。

### §2.9 接收侧字段

| 字段 | 行 | 说明 |
|---|---|---|
| `_rx` | `:2201` | `struct netdev_rx_queue *` 数组 |
| `num_rx_queues` | `:2202` | 分配数 |
| `real_num_rx_queues` | `:2203` | 实际启用数，`netif_set_real_num_rx_queues()`（`net/core/dev.c:2989`） |
| `xdp_prog` | `:2205` | 挂着的 XDP 程序（通用 XDP 用；驱动原生 XDP 自己存） |
| `gro_flush_timeout` | `:2206` | NAPI 延迟触发的纳秒数（`/sys/class/net/*/gro_flush_timeout`） |
| `napi_defer_hard_irqs` | `:2207` | 允许推迟多少次开硬中断 |
| `gro_max_size` | `:2213` | GRO 聚合上限 |
| `rx_handler` / `rx_handler_data` | `:2215` 一带 | bridge / bonding 抢包用，驱动不填 |
| `miniq_ingress` | `:2218` | 入方向 tc 快路径 |
| `ingress_queue` | `:2220` | 入方向的伪 qdisc（`tc ... ingress`） |
| `nf_hooks_ingress` | `:2222` | 入方向 netfilter 钩子 |
| `rx_cpu_rmap` | `:2227` | 反向中断亲和映射（aRFS 用） |
| `ptype_all` / `ptype_specific` | `:2053` / `:2054` | 挂在本设备上的抓包/协议处理器 |

`gro_flush_timeout` 与 `napi_defer_hard_irqs` 这一对是**不改驱动就能调延迟/吞吐权衡**的两个旋钮，排查中断风暴时优先看它们，用法见 `07-debug.md`。

### §2.10 引用计数与注册状态

| 字段 | 行 | 说明 |
|---|---|---|
| `pcpu_refcnt` | `:2265` | `CONFIG_PCPU_DEV_REFCNT` 打开时用，per-CPU 计数 |
| `dev_refcnt` | `:2267` | 关闭时用的 `refcount_t`，见 §1.0.8 |
| `refcnt_tracker` | `:2269` | 引用泄漏追踪（`CONFIG_NET_DEV_REFCNT_TRACKER`） |
| `reg_state`（匿名 enum 里的位域） | `:2283` 一带 | 注册状态机，枚举值在 `:2273-2280` |
| `rtnl_link_state` | 同上匿名 enum | rtnetlink 侧的初始化状态 |
| `needs_free_netdev` | `:2288` | 置 1 则核心在 todo 阶段替你调 `free_netdev()` |
| `priv_destructor` | `:2289` | 释放前的回调，见 `01-api.md` §2.1 |
| `dismantle` | `:2281` | 拆除中标记 |

注册状态机的取值（`include/linux/netdevice.h:2273-2280`）：

```
NETREG_UNINITIALIZED = 0    刚 alloc 出来
NETREG_REGISTERED           register_netdevice() 成功
NETREG_UNREGISTERING        正在注销
NETREG_UNREGISTERED         注销完成，等 free
NETREG_RELEASED             已释放
NETREG_DUMMY                dummy 设备
```

⚠️ **`free_netdev()` 会看 `reg_state`**（`net/core/dev.c:10856`）：如果发现是 `NETREG_UNREGISTERING`，它不释放，而是置 `needs_free_netdev = true` 然后返回。这是为了处理"`register_netdevice()` 失败后立刻 `free_netdev()`"的竞态。**所以 `free_netdev()` 返回不代表内存已释放**——驱动在它之后不能再碰 `netdev_priv()` 的内容，但也不能假设已经释放而去做二次清理。

### §2.11 驱动模型与 sysfs

| 字段 | 行 | 说明 |
|---|---|---|
| `dev` | `:2317` | 内嵌的 `struct device`，见 §1.0.1 / §1.1.1 |
| `sysfs_groups[4]` | `:2318` | 额外挂的属性组，最多 3 个（末位留 NULL） |
| `sysfs_rx_queue_group` | `:2319` | 挂到每个 rx-N 目录下的属性组 |
| `queues_kset` | `:2150` | `/sys/class/net/eth0/queues/` 那个 kset |
| `nd_net` | `:2295` | 所属 network namespace |
| `padded` | `:2140` | 见 §1.1.2 |
| `dev_id` | `:2138` | 同一物理端口的多个逻辑设备编号 |
| `dev_port` | `:2139` | 物理端口号，`ethtool -i` 与 udev 命名用 |
| `irq` | `:2143` | 中断号，`ifconfig` 显示用，现代驱动多不填 |
| `npinfo` | `:2292` | netpoll（`CONFIG_NETPOLL`）状态 |

⚠️ **`dev_id` 和 `dev_port` 语义不同却极易写反**：`dev_port` 是"这是芯片上第几个物理口"，`dev_id` 是"同一个口上的第几个逻辑实例"。udev 的持久化命名规则会读 `dev_port`，写错会导致接口名在重启后变化。

### §2.12 各协议的挂钩指针

`net_device` 上有一整片"某个上层协议的私有指针"，正是 `:2025-2026` 那条 FIXME 抱怨的对象：

| 字段 | 行 | 属于谁 |
|---|---|---|
| `ip_ptr` | `:2165` | IPv4（`struct in_device`） |
| `ip6_ptr` | `:2166` | IPv6 |
| `vlan_info` | `:2168` | 8021q |
| `dsa_ptr` | `:2171` | DSA 交换机 |
| `tipc_ptr` | `:2174` | TIPC |
| `atalk_ptr` | `:2177` | AppleTalk |
| `ax25_ptr` | `:2180` | AX.25 |
| `ieee80211_ptr` | `:2183` | 无线（cfg80211） |
| `ieee802154_ptr` | `:2186` | 802.15.4 |
| `mpls_ptr` | `:2189` | MPLS |
| `mctp_ptr` | `:2192` | MCTP |
| `garp_port` | `:2309` | GARP（`CONFIG_GARP`） |
| `mrp_port` | `:2312` | MRP |
| `dm_private` | `:2315` | drop monitor |
| `ml_priv` / `ml_priv_type` | `:2298` / `:2299` | "中层"私有（bonding 之类），类型枚举在 `:1745` |

以太网驱动**一个都不该碰**。列在这里的唯一理由是：读 `net_device` 定义时这十几个字段占了很大篇幅，知道它们与驱动无关可以直接跳过。

`ml_priv` 是唯一有点特殊的：它带一个 `ml_priv_type` 标记，因为历史上多个中间层抢用同一个指针，现在靠这个枚举区分。驱动如果误用会和 bonding 冲突。

---

## §3 `struct napi_struct`

定义在 `include/linux/netdevice.h:344`，到 `:371` 结束。**这个结构体由驱动持有**（通常嵌在私有结构体里），核心只通过 `dev->napi_list` 和 `softnet_data.poll_list` 串它。

| 字段 | 行 | 谁写 | 说明 |
|---|---|---|---|
| `poll_list` | `:351` | 核心 | 挂进 `softnet_data.poll_list`。**它在链表上等于"已被调度"** |
| `state` | `:353` | 两侧 | 位图，见 §3.1 |
| `weight` | `:354` | `netif_napi_add()` 设 | 每轮 poll 的预算，以太网标准值 64 |
| `defer_hard_irqs_count` | `:355` | 核心 | 已推迟开硬中断的次数，与 `dev->napi_defer_hard_irqs` 比 |
| `gro_bitmask` | `:356` | 核心 | 哪些 GRO 桶非空 |
| `poll` | `:357` | 驱动 | 轮询函数 |
| `poll_owner` | `:359` | 核心 | `CONFIG_NETPOLL` 下记录持有者 CPU |
| `dev` | `:361` | `netif_napi_add()` 设 | 回指 `net_device` |
| `gro_hash[GRO_HASH_BUCKETS]` | `:362` | 核心 | GRO 聚合中的流，桶元素类型 `struct gro_list`（`:330`） |
| `skb` | `:363` | 核心 | `napi_get_frags()` 复用的 skb |
| `rx_list` | `:364` | 核心 | 批量交栈的暂存链表 |
| `rx_count` | `:365` | 核心 | `rx_list` 的长度 |
| `timer` | `:366` | 核心 | `gro_flush_timeout` 用的高精度定时器 |
| `dev_list` | `:367` | 核心 | 挂进 `dev->napi_list` |
| `napi_hash_node` | `:368` | 核心 | 按 `napi_id` 查的哈希节点（busy poll 用） |
| `napi_id` | `:369` | 核心 | 全局唯一 NAPI 号，用户态 `SO_INCOMING_NAPI_ID` 能看到 |
| `thread` | `:370` | 核心 | 线程化 NAPI 的内核线程 |

⚠️ **驱动只该读写 `poll` 和 `weight`，而且只在 `netif_napi_add()` 之前**。其余字段全是核心的。特别是 `state`——驱动想改它只能通过 `napi_schedule()` / `napi_complete_done()` / `napi_disable()` 这些函数，直接置位会破坏调度不变量。

### §3.1 `napi->state` 的位

枚举在 `include/linux/netdevice.h:373-384`，对应的掩码宏在 `:386-397`（`NAPIF_STATE_*`）：

| 位 | 行 | 含义 |
|---|---|---|
| `NAPI_STATE_SCHED` | `:374` | 已调度或正在跑。**这是 NAPI 的核心互斥位** |
| `NAPI_STATE_MISSED` | `:375` | 跑的过程中又来了一次调度请求，poll 结束后要重新排 |
| `NAPI_STATE_DISABLE` | `:376` | `napi_disable()` 正在进行，拒绝新调度 |
| `NAPI_STATE_NPSVC` | `:377` | netpoll 独占中 |
| `NAPI_STATE_LISTED` | `:378` | 已挂进 `dev->napi_list` |
| `NAPI_STATE_NO_BUSY_POLL` | `:379` | 驱动声明不支持 busy poll |
| `NAPI_STATE_IN_BUSY_POLL` | `:380` | 当前正被 busy poll 占用 |
| `NAPI_STATE_PREFER_BUSY_POLL` | `:381` | 倾向 busy poll |
| `NAPI_STATE_THREADED` | `:382` | 线程化模式 |
| `NAPI_STATE_SCHED_THREADED` | `:383` | 线程化模式下已调度 |

理解 `SCHED` + `MISSED` 这一对是理解 NAPI 的关键：

```
  ISR 调 napi_schedule()
     │
     ├─ 若 SCHED 未置 → 置 SCHED，挂 poll_list，触发软中断
     └─ 若 SCHED 已置 → 只置 MISSED，直接返回（不重复入队）
                          napi_schedule_prep() 里判，:6070

  poll 跑完调 napi_complete_done()      net/core/dev.c:6113
     │
     ├─ 若 MISSED 已置 → 清 MISSED，保持 SCHED，重新排队（返回 false）
     └─ 若 MISSED 未置 → 清 SCHED（返回 true，此时才可以开硬中断）
```

⚠️ 这就是**`napi_complete_done()` 的返回值必须看**的原因：返回 `false` 时不能开硬中断，因为还要再跑一轮。`01-api.md` §6.2 有代码骨架。

---

## §4 `struct netdev_queue`

定义在 `include/linux/netdevice.h:604-646`。数组是 `dev->_tx[]`，取用 `netdev_get_tx_queue(dev, i)`（`:2443`）。

这个结构体的定义里带三段分区注释（`:605-607`、`:631-633`），把字段按访问模式分成两半——**这不是文档，是布局约束**：

| 字段 | 行 | 分区 | 说明 |
|---|---|---|---|
| `dev` | `:608` | 读多 | 回指 `net_device` |
| `dev_tracker` | `:609` | 读多 | 引用追踪 |
| `qdisc` | `:611` | 读多 | 这个队列的 qdisc |
| `qdisc_sleeping` | `:612` | 读多 | 设备 down 时暂存的 qdisc |
| `kobj` | `:614` | 读多 | `/sys/class/net/eth0/queues/tx-N`（`CONFIG_SYSFS`） |
| `numa_node` | `:617` | 读多 | XPS + NUMA 下的节点亲和 |
| `tx_maxrate` | `:619` | 读多 | 每队列限速（`ndo_set_tx_maxrate`） |
| `trans_timeout` | `:624` | 读多 | 这个队列超时过几次，注释直接点名 `/sys/class/net/DEV/Q/trans_timeout` |
| `sb_dev` | `:627` | 读多 | 子设备（`ndo_select_queue` 的 subordinate device） |
| `pool` | `:629` | 读多 | AF_XDP 的 umem 池（`CONFIG_XDP_SOCKETS`） |
| `_xmit_lock` | `:634` | 写多，`____cacheline_aligned_in_smp` | 发送串行化锁 |
| `xmit_lock_owner` | `:635` | 写多 | 持锁 CPU，`-1` 表示无人持 |
| `trans_start` | `:639` | 写多 | 上次成功发送的时间戳，watchdog 用 |
| `state` | `:641` | 写多 | 停/唤位图，见 §4.1 |
| `dql` | `:644` | 写多 | BQL 状态（`CONFIG_BQL`） |

⚠️ **`_xmit_lock` 起的那条 `____cacheline_aligned_in_smp`（`:634`）是性能的关键**：读多的字段（`qdisc`、`dev`）和写多的字段（`state`、`dql`）被强制分到不同 cacheline，否则每次 BQL 记账都会让别的 CPU 上的 `qdisc` 读失效。多队列网卡的每队列独立性就靠这个。

### §4.1 `netdev_queue->state` 的位

枚举 `enum netdev_queue_state_t` 在 `include/linux/netdevice.h:578-582`：

| 位 | 行 | 谁置 | 含义 |
|---|---|---|---|
| `__QUEUE_STATE_DRV_XOFF` | `:579` | **驱动**（通过 `netif_stop_queue()`） | 驱动说"我环满了，别给我" |
| `__QUEUE_STATE_STACK_XOFF` | `:580` | **核心**（BQL / qdisc） | 核心说"字节配额用完了" |
| `__QUEUE_STATE_FROZEN` | `:581` | 核心 | 队列冻结（配置变更期间） |

组合掩码（`:584-591`）：

| 宏 | 行 | 含义 |
|---|---|---|
| `QUEUE_STATE_DRV_XOFF` | `:584` | 只看驱动那一位 |
| `QUEUE_STATE_STACK_XOFF` | `:585` | 只看核心那一位 |
| `QUEUE_STATE_FROZEN` | `:586` | 冻结位 |
| `QUEUE_STATE_ANY_XOFF` | `:588` | 两个 XOFF 任一 |
| `QUEUE_STATE_ANY_XOFF_OR_FROZEN` | `:589` | 上面再加冻结 |
| `QUEUE_STATE_DRV_XOFF_OR_FROZEN` | `:591` | 驱动位或冻结 |

⚠️ **`:594-602` 那段注释是这一节的权威表述**，它把驱动和核心的分界写死了。要点两条：

- **驱动只碰 `DRV_XOFF`，只用 `netif_tx_*` 一族**（`netif_stop_queue` / `netif_wake_queue` / `netif_tx_stop_queue` / `netif_tx_wake_queue`）。
- **驱动不该调 `netif_xmit_stopped()` 那一族**——那些函数看的是 `ANY_XOFF`，包含核心自己置的位。驱动拿它判断"我能不能发"会得到错误答案：核心置了 `STACK_XOFF` 只是不再给你新包，不代表你手上这个包不能发。

判断"我停过队列吗"要用 `netif_tx_queue_stopped(txq)`（`:3335`）或 `netif_queue_stopped(dev)`（`:3346`），它们看的是 `DRV_XOFF`。

---

## §5 `struct netdev_rx_queue`

定义在 `include/linux/netdevice.h:775-788`。数组是 `dev->_rx[]`。

| 字段 | 行 | 说明 |
|---|---|---|
| `xdp_rxq` | `:776` | XDP 的接收队列信息，驱动要调 `xdp_rxq_info_reg()` 注册 |
| `rps_map` | `:778` | RPS 的 CPU 映射（`CONFIG_RPS`），结构体在 `:698` |
| `rps_flow_table` | `:779` | RFS 的流表，结构体在 `:720` |
| `kobj` | `:781` | `/sys/class/net/eth0/queues/rx-N` |
| `dev` | `:782` | 回指 |
| `dev_tracker` | `:783` | 引用追踪 |
| `pool` | `:786` | AF_XDP umem 池（`CONFIG_XDP_SOCKETS`） |

对比 §4 的 `netdev_queue`（43 行、15 个字段）：接收侧只有 7 个字段，**而且一个锁都没有**。原因是接收路径的串行化靠 NAPI 的 `SCHED` 位，不需要队列级锁。这个不对称是网卡收发路径设计差异的最直接体现。

`rx_queue_attribute`（`:793`）是 rx-N 目录下 sysfs 属性的类型，`dev->sysfs_rx_queue_group`（`:2319`）挂的就是这类属性。

---

## §6 统计相关结构体

### §6.1 三个上报格式

| 结构体 | 定义 | 用途 |
|---|---|---|
| `struct net_device_stats` | `include/linux/netdevice.h:180` | 老格式，`unsigned long` 字段。`dev->stats` 是它 |
| `struct rtnl_link_stats64` | `include/uapi/linux/if_link.h:218` | **上报给用户态的格式**，全 `__u64`。`ndo_get_stats64` 填它 |
| `struct net_device_core_stats` | `include/linux/netdevice.h:210-215` | 核心自己的四个丢包计数 |

`net_device_core_stats` 只有四个字段：

| 字段 | 行 |
|---|---|
| `rx_dropped` | `:211` |
| `tx_dropped` | `:212` |
| `rx_nohandler` | `:213` |
| `rx_otherhost_dropped` | `:214` |

`:215` 那行 `__aligned(4 * sizeof(unsigned long))` 保证四个计数落在同一 cacheline 上。

⚠️ **`net_device_stats` 和 `rtnl_link_stats64` 字段名几乎一样但类型不同**（`unsigned long` vs `__u64`），32 位架构上前者会溢出。这就是 `ndo_get_stats` 被 `ndo_get_stats64` 取代的原因。新驱动只实现后者。

### §6.2 三个 per-CPU 容器

由 `dev->pcpu_stat_type`（`include/linux/netdevice.h:2301`）选择，枚举在 `:1750-1755`：

| 枚举值 | 结构体 | 定义 | 字段 |
|---|---|---|---|
| `NETDEV_PCPU_STAT_LSTATS` | `struct pcpu_lstats` | `:2701-2705` | `packets` / `bytes` / `syncp`，`__aligned(2 * sizeof(u64))` |
| `NETDEV_PCPU_STAT_TSTATS` | `struct pcpu_sw_netstats` | `:2683-2689` | `rx_packets` / `rx_bytes` / `tx_packets` / `tx_bytes` / `syncp`，`__aligned(4 * sizeof(u64))` |
| `NETDEV_PCPU_STAT_DSTATS` | `struct pcpu_dstats` | `:2691-2699` | 收发各三项含 drops，`__aligned(8 * sizeof(u64))` |

三者的对齐标注都是"字段数对齐"，目的是让整个结构体不跨 cacheline。以太网驱动最常用 `pcpu_sw_netstats`，配套的读取函数是 `dev_fetch_sw_netstats()`（`net/core/dev.c:10626`）和 `dev_get_tstats64()`（`:10661`）。

⚠️ `pcpu_dstats` 的字段是裸 `u64` 而不是 `u64_stats_t`——**它没有走 `u64_stats_add()` 那套封装**。混用会在 32 位上出问题。选了哪个类型就用配套的辅助函数。

### §6.3 `u64_stats_sync`

定义在 `include/linux/u64_stats_sync.h:64-68`。64 位上是空结构体，32 位上含一个 `seqcount_t`（见 §1.0.8）。

写侧规则在 `:22` 一带的注释里，配套函数分两组：

| 场合 | 函数 | 位置（64 位分支） |
|---|---|---|
| 初始化 | `u64_stats_init()` | `:97` |
| 写侧开始 | `u64_stats_update_begin()` | `:98` 一带 |
| 累加 | `u64_stats_add()` / `u64_stats_inc()` | `:87` / `:92` |
| 读取单值 | `u64_stats_read()` | `:77` |
| 读侧重试 | `u64_stats_fetch_begin()` / `_retry()` | `:102` 一带 |
| 中断安全写 | `u64_stats_update_begin_irqsave()` | `:191`（32 位分支） |

⚠️⚠️ **本树把 `u64_stats_fetch_begin_irq()` / `u64_stats_fetch_retry_irq()` 整个删掉了**（那是原先 `/* Obsolete interfaces */` 一节，12 行）。写这两个名字**编译不过**。全树零残留。这是本树 PREEMPT_RT 补丁影响面最大的一处，47 个以太网驱动文件被机械替换，详见 `01-api.md` §0.6 和 `99-evidence.md` §4.1。

---

## §7 地址表结构体

### §7.1 `struct netdev_hw_addr`

定义在 `include/linux/netdevice.h:230-244`：

| 字段 | 行 | 说明 |
|---|---|---|
| `list` | `:231` | 挂进 `netdev_hw_addr_list.list` |
| `node` | `:232` | 红黑树节点，加/删时加速查找 |
| `addr[MAX_ADDR_LEN]` | `:233` | 地址本体。`dev->dev_addr` 指向的就是这里 |
| `type` | `:234` | 四种类型，宏在 `:235-238` |
| `global_use` | `:239` | 被上层设备共享 |
| `sync_cnt` | `:240` | 已同步到硬件的次数 |
| `refcount` | `:241` | 引用计数 |
| `synced` | `:242` | 已同步标记 |
| `rcu_head` | `:243` | RCU 延迟释放 |

四种 `type`：`NETDEV_HW_ADDR_T_LAN`（`:235`）、`_SAN`（`:236`）、`_UNICAST`（`:237`）、`_MULTICAST`（`:238`）。`dev_addr_init()` 建的第一项是 `_LAN`（`net/core/dev_addr_lists.c:558-559`）。

### §7.2 `struct netdev_hw_addr_list`

定义在 `:246-252`，三个字段：`list`（`:247`）、`count`（`:248`）、`tree`（`:251`，注释说明是"加删加速用的辅助树"）。

遍历宏在 `:254-257`，`dev->uc` / `dev->mc` 的便捷宏从 `:259` 开始。驱动在 `ndo_set_rx_mode` 里用 `netdev_for_each_mc_addr()` 遍历组播表——具体用法见 `01-api.md` §11.3 和 `05-usage.md`。

⚠️ **`count` 是链表长度，不是"硬件里装了几个"**。表比硬件槽位多时驱动应该退化到 allmulti，而不是截断。这个判断只能驱动自己做，核心不管。

---

## §8 `struct softnet_data`（per-CPU）

定义在 `include/linux/netdevice.h:3153-3207`，声明 `DECLARE_PER_CPU_ALIGNED(struct softnet_data, softnet_data)` 在 `:3224`，定义在 `net/core/dev.c:437`。

**驱动不直接碰它**，但排查收包路径的问题必须认识它——`/proc/net/softnet_stat` 的每一行就是一个 CPU 的这个结构体。

### §8.1 字段

| 字段 | 行 | 说明 |
|---|---|---|
| `poll_list` | `:3154` | 本 CPU 上待轮询的 NAPI 链表 |
| `process_queue` | `:3155` | backlog 正在处理的一批 |
| `processed` | `:3158` | 处理过的包数 |
| `time_squeeze` | `:3159` | 软中断预算用尽而中止的次数。⚠️ 这个数持续增长意味着收包压力超过预算 |
| `received_rps` | `:3160` | 通过 RPS 从别的 CPU 收到的次数 |
| `rps_ipi_list` | `:3162` | 待发 RPS IPI 的 CPU 链表 |
| `flow_limit` | `:3165` | 流量限制表（`CONFIG_NET_FLOW_LIMIT`） |
| `output_queue` / `output_queue_tailp` | `:3167` / `:3168` | 待运行的 qdisc 链表 |
| `completion_queue` | `:3169` | 待释放的 skb |
| `xfrm_backlog` | `:3171` | IPsec 的待处理队列 |
| `xmit`（匿名 struct） | `:3174-3180` | 发送递归深度 `recursion`（`:3175`）、`more`（`:3176`）、`skip_txqueue`（`:3178`） |
| `input_queue_head` | `:3185` | backlog 队头序号 |
| `csd` | `:3188` | RPS IPI 用的 call_single_data |
| `rps_ipi_next` | `:3189` | IPI 链表的下一项 |
| `cpu` | `:3190` | 本 CPU 号 |
| `input_queue_tail` | `:3191` | backlog 队尾序号 |
| `dropped` | `:3193` | backlog 溢出丢的包数 |
| `input_pkt_queue` | `:3194` | backlog 队列本体（非 NAPI 驱动的 `netif_rx()` 进这里） |
| `backlog` | `:3195` | backlog 的伪 NAPI 实例 |
| `defer_lock` | `:3198` | 延迟释放链表的锁 |
| `defer_count` | `:3199` | 延迟释放的 skb 数 |
| `defer_ipi_scheduled` | `:3200` | 延迟释放 IPI 已排 |
| `defer_list` | `:3201` | 延迟释放的 skb 链表 |
| `defer_csd` / `defer_work` | `:3202-3206` | **随配置分叉**，见 §8.3 |

`input_queue_head_incr` 那个辅助在 `:3209`。

### §8.2 backlog 是什么

`backlog`（`:3195`）是一个**伪 NAPI 实例**：它的 `poll` 是核心的 `process_backlog()`，队列是 `input_pkt_queue`。作用是给**不支持 NAPI 的收包路径**（`netif_rx()`，`net/core/dev.c:5082`）一个落脚点。

对以太网驱动的意义：**如果你用 `netif_rx()` 而不是 `napi_gro_receive()`，包会先进 backlog 再被处理，多一次排队、丢掉 GRO、还会撞 `dropped` 计数上限**。现代驱动都走 NAPI，`netif_rx()` 只留给极简驱动和 tun/tap 之类。

排查时 `/proc/net/softnet_stat` 第二列就是 `dropped`，第三列是 `time_squeeze`。

### §8.3 本树补丁：延迟释放的两条路

`:3202-3206` 这五行是本树 PREEMPT_RT 补丁的落点：

```c
#ifndef CONFIG_PREEMPT_RT
	call_single_data_t	defer_csd;
#else
	struct work_struct	defer_work;
#endif
```

背景：`defer_list` 上挂的是"要在原 CPU 上释放的 skb"（这样能命中原 CPU 的 slab 缓存）。通知原 CPU 干活有两种手段：

| 配置 | 字段 | 通知方式 | 处理函数形态 |
|---|---|---|---|
| `CONFIG_PREEMPT_RT=n` | `defer_csd` | IPI（`__raise_softirq_irqoff(NET_RX_SOFTIRQ)`） | 在软中断上下文跑 |
| `CONFIG_PREEMPT_RT=y` | `defer_work` | 工作队列 | 在进程上下文跑，自己 `local_bh_disable()` / `_enable()` 包一层 |

配套改动在 `net/core/dev.c` 的 `trigger_rx_softirq()`（被 `#ifndef CONFIG_PREEMPT_RT` 拆成两个版本）和 `net_dev_init()`（用 `INIT_CSD()` 还是 `INIT_WORK()` 初始化）。

为什么 RT 上不能用 IPI：IPI 处理函数运行在硬中断上下文，而 RT 内核里 `local_bh_disable()` 是可睡眠的互斥，两者不兼容。**驱动侧完全不受影响**——这个分叉在核心内部，驱动看不到。

完整的补丁清单和验证命令见 `99-evidence.md` §4.1。

---

## §9 全局变量与锁

### §9.1 全局变量

| 变量 | 位置 | 说明 |
|---|---|---|
| `softnet_data`（per-CPU） | `net/core/dev.c:437` | 见 §8 |
| `dev_base_lock` | `net/core/dev.c:189` | `rwlock_t`，保护设备链表的读者 |
| `ptype_lock` | `:158` | 保护 `ptype_all` / `ptype_base` |
| `ifalias_mutex` | `:192` | 保护 `dev->ifalias` 的写 |
| `napi_hash_lock` | `:195` | 保护 `napi_id` 哈希表 |
| `xps_map_mutex` | `:2422` | 保护 XPS 映射 |
| `rtnl_mutex` | `net/core/rtnetlink.c:70` | **最重要的一把**，见 §9.2 |
| `net_class` | `net/core/net-sysfs.c:1926` | `/sys/class/net` 的 class 对象 |

四个静态键（用于零开销地关掉不常用的路径）：

| 静态键 | 位置 | 控制什么 |
|---|---|---|
| `ingress_needed_key` | `net/core/dev.c:2080` | 入方向 tc |
| `egress_needed_key` | `:2096` | 出方向 tc |
| `netstamp_needed_key` | `:2111` | 收包时间戳 |
| `generic_xdp_needed_key` | `:4978` | 通用 XDP |

⚠️ 静态键的意义：**没人用这些功能时，判断分支被编译成一条 nop**。所以"加了 tc 规则之后收包变慢"是真的——不是规则本身慢，是打开了一整条原本被跳过的路径。

### §9.2 rtnl_lock 与它保护什么

`rtnl_lock()`（`net/core/rtnetlink.c:72`，锁本体在 `:70`）是一把**巨锁**：几乎所有 `net_device` 的配置变更都在它下面。

| 谁持它 | 例 |
|---|---|
| 所有 `ndo_*` 的配置类回调 | `ndo_open` / `ndo_stop` / `ndo_change_mtu` / `ndo_set_mac_address` / `ndo_set_features` |
| 所有 `ethtool_ops` 回调 | 全部 |
| 注册与注销 | `register_netdev()`（`net/core/dev.c:10341`）自己拿锁；`register_netdevice()`（`:10111`）要求调用者已持 |
| sysfs 的多数写入 | 通过 `net-sysfs.c` 的包装 |

它**不保护**的：

| 不在 rtnl 下 | 靠什么 |
|---|---|
| `ndo_start_xmit` | `txq->_xmit_lock`（或 `NETIF_F_LLTX` 时驱动自己） |
| `napi->poll` | `NAPI_STATE_SCHED` 位 |
| `ndo_get_stats64` | 什么都不持，所以**不能睡** |
| 地址表遍历 | `dev->addr_list_lock`（`include/linux/netdevice.h:2142`） |
| 读设备链表 | `dev_base_lock` 或 RCU |

⚠️ **`register_netdev()` 和 `register_netdevice()` 的锁语义相反**，这是网卡驱动最经典的死锁来源：前者自己 `rtnl_lock()`，后者要求调用者已持。在 `ndo_*` 回调里（已持 rtnl）调 `register_netdev()` 会死锁。判断方法看名字有没有 `evice`——带 `device` 全称的那个要求已持锁。

### §9.3 锁的层级

```
  rtnl_mutex                      （可睡，最外层）
    ├─ dev_base_lock              （rwlock）
    ├─ dev->addr_list_lock        （spinlock）
    ├─ txq->_xmit_lock            （spinlock）
    └─ sd->defer_lock             （spinlock）

  独立于 rtnl 的：
    napi->state 的 SCHED 位        （原子位，起互斥作用）
    RCU 读侧                       （dev->netdev_ops 等指针）
```

⚠️ `_xmit_lock` 可以在持 rtnl 时被拿（配置变更要停队列），但**反过来不行**——持 `_xmit_lock` 时不能拿 rtnl（前者是自旋锁、在软中断上下文，后者可睡）。驱动的 `ndo_start_xmit` 里绝对不能调任何 `rtnl_lock()` 的函数。

`__netif_tx_lock()`（`:4349`）/ `__netif_tx_lock_bh()`（`:4367`）/ `netif_tx_lock()`（`:4430`）/ `netif_tx_lock_bh()`（`:4432`）四个包装的区别见 `01-api.md` §5.4 一带。

---

## §10 page_pool 侧的结构体

`struct page_pool_params` 定义在 `include/net/page_pool.h:74-85`。它是驱动**唯一需要自己填**的 page_pool 结构体（`struct page_pool` 本体对驱动是不透明的）：

| 字段 | 行 | 说明 |
|---|---|---|
| `flags` | `:75` | `PP_FLAG_DMA_MAP` / `PP_FLAG_DMA_SYNC_DEV` 等 |
| `order` | `:76` | 每次分配的页阶（0 = 单页） |
| `pool_size` | `:77` | 环形缓存能存多少页 |
| `nid` | `:78` | NUMA 节点，注释写明 "Numa node id to allocate from pages from" |
| `dev` | `:79` | DMA 预映射用的 `struct device *`。⚠️ 是父设备的 `dev`，不是 `&ndev->dev` |
| `dma_dir` | `:80` | 映射方向 |
| `max_len` | `:81` | DMA 同步的最大字节数 |
| `offset` | `:82` | 数据在页内的偏移 |
| `init_callback` | `:83` | 每页首次分配时的回调 |
| `init_arg` | `:84` | 回调参数 |

⚠️ **`dev` 必须是能做 DMA 的那个 `struct device`**，也就是 `pdev->dev`（platform 设备）或 `pci_dev->dev`，不能是 `net_device` 内嵌的那个——后者的 `dma_mask` 是空的，映射会失败或静默错误。这是 page_pool 初始化最常见的一处错。

`CONFIG_PAGE_POOL_STATS` 打开时还有 `struct page_pool_alloc_stats`（`:88` 起），字段 `fast` / `slow` / `slow_high_order` / `empty` 等，用来判断回收率。

`skb_mark_for_recycle()`（`include/linux/skbuff.h:5127`）是把 skb 和 page_pool 关联起来的那一步，漏掉它页就不会回到池里而是走普通释放——功能正常，性能退化到不用 page_pool 的水平。用法见 `01-api.md` §12.1。

---

## §11 读下一篇

| 想知道 | 去 |
|---|---|
| 这些结构体上的函数怎么调、有什么契约 | `01-api.md` |
| 注册/发送/收包流程逐段剖析、字段在流程哪一步被写 | `03-internals.md` |
| 队列、XDP、ethtool 这些子框架各自的边界 | `04-extensions.md` |
| 要照抄一个驱动骨架 | `05-usage.md` |
| DTS 里的属性最终落到哪个字段 | `06-devicetree.md` |
| 某个字段的当前值在哪看 | `07-debug.md` |
| 为什么发送和接收的结构体这么不对称 | `08-feynman.md` |
| 上面每个行号和计数的取证等级 | `99-evidence.md` |
