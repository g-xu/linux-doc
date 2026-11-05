---
title: 内部实现
---

# 网卡驱动核心流程剖析

本篇只讲 `net/core/dev.c` 一带的**核心逻辑怎么跑**。数据结构去 `02-datastructures.md`，API 清单去 `01-api.md`。

---

## §0 导航

### §0.1 本篇的三条轴

一篇文档只能承诺一条主轴，其余显式分区。本篇分三块，章节号自带前缀：

| 轴 | 章节 | 组织方式 | 什么时候读 |
|---|---|---|---|
| Part A | §A1–§A12 | 按**时间**排：从 `alloc_netdev_mqs()` 到 `free_netdev()` | 想知道"某一步之前发生了什么" |
| Part B | §B1–§B7 | 按**横切关注点**排：锁、状态机、队列、统计、通知 | 想知道"这个机制到底怎么保证正确" |
| Part C | §C1–§C3 | 评价：源码自陈的设计债、做得好的地方、改不动的地方 | 想改核心代码之前 |

Part A 的每个章节标题后面带**函数的完整行号区间**，可以直接 `sed -n '10111,10281p' net/core/dev.c` 打开对照。

### §0.2 分层图

```
      ┌────────────────────────────────────────────────┐
      │ 用户态 / 协议栈                                 │
      │  ip link · ethtool · socket 发包                │
      └───────┬────────────────────────┬───────────────┘
              │ rtnetlink / ioctl      │ dev_queue_xmit
              ↓                        ↓
      ┌───────────────────────────────────────────────┐
      │ net/core/dev.c ← 本篇主体                      │
      │                                                │
      │  注册侧            发送侧          接收侧       │
      │  register_netdevice  __dev_queue_xmit  netif_rx │
      │  dev_open            dev_hard_start_xmit        │
      │  dev_close                          net_rx_action│
      │  unregister_*        （经 qdisc）    process_backlog│
      └───────┬───────────────┬───────────────┬────────┘
              │ ndo_* 回调    │ ndo_start_xmit│ napi->poll
              ↓               ↓               ↓
      ┌───────────────────────────────────────────────┐
      │ drivers/net/ethernet/<vendor>/ ← 驱动          │
      │  284 个 net_device_ops 实例                     │
      └───────┬───────────────────────────────────────┘
              │ MMIO / DMA / 中断
              ↓
      ┌───────────────────────────────────────────────┐
      │ 硬件：MAC + 描述符环 + PHY                      │
      └───────────────────────────────────────────────┘
```

⚠️ 图里 `net/core/dev.c` 那一层**不是一个单向的栈**。发送方向经过 qdisc（`net/sched/`）绕了一圈才回到驱动；接收方向的 `netif_rx()` 和 NAPI 是两条并列入口，不是上下关系。这一点在 §A6 和 §A7 展开。

### §0.3 生命周期时间线

```
 驱动 probe()                                          驱动 remove()
     │                                                       │
     ├─ alloc_netdev_mqs()          §A1                       │
     │     一次分配出 net_device + 私有区                       │
     │                                                       │
     ├─ ether_setup() / 填 netdev_ops  §A2                    │
     │     此刻 reg_state = NETREG_UNINITIALIZED               │
     │                                                       │
     ├─ register_netdev()           §A3                       │
     │     ndo_init → 分配队列 → features 初始化 →              │
     │     进 sysfs → reg_state = NETREG_REGISTERED →          │
     │     NETDEV_REGISTER 通知 → RTM_NEWLINK                  │
     │                                                       │
     │  ┌──── 此后可以被 ip link 操作 ────┐                     │
     │  │                                │                    │
     │  │  ip link set up   →  dev_open()      §A5            │
     │  │      ndo_open，IFF_UP 置位，dev_activate            │
     │  │           │                                         │
     │  │           ├─ 发包    §A6 §A7                         │
     │  │           ├─ 收包    §A8                             │
     │  │           └─ ethtool / features 重算  §A4 §B4        │
     │  │           │                                         │
     │  │  ip link set down →  dev_close()     §A9            │
     │  │      IFF_UP 清位，dev_deactivate，ndo_stop           │
     │  └────────────────────────────────────┘                │
     │                                                        │
     └────────────────────→ unregister_netdev()     §A10 ←────┘
                                │
                                ├─ dev_close_many（如果还 UP）
                                ├─ 摘链表，reg_state = NETREG_UNREGISTERING
                                ├─ flush_all_backlogs + synchronize_net
                                ├─ NETDEV_UNREGISTER 通知
                                └─ net_set_todo → 挂 net_todo_list
                                        │
                            rtnl_unlock() 里的 netdev_run_todo()   §A11
                                        │
                                ├─ reg_state = NETREG_UNREGISTERED
                                ├─ 等引用计数归 1
                                └─ free_netdev()                  §A12
```

三条最容易搞错的界限：

- **`unregister_netdev()` 返回时设备还没被释放**。它只走到 `net_todo_list`，真正的收尾在 `netdev_run_todo()` 里，而后者是 `rtnl_unlock()` 顺手带出来的（§A11）。
- **`free_netdev()` 可能什么都不做就返回**。如果此刻 `reg_state == NETREG_UNREGISTERING`，它只置一个标志然后 `return`，把释放交给 `netdev_run_todo()`（`net/core/dev.c:10852-10860`，见 §A12.1）。
- **`dev_open()` / `dev_close()` 可以来回无数次**，`register` / `unregister` 各只能一次。

### §0.4 哪些内容不在本篇

| 你想找 | 去哪 |
|---|---|
| `net_device` 每个字段是什么 | `02-datastructures.md` §2 |
| `netdev_queue` / `napi_struct` 的状态位含义 | `02-datastructures.md` §3.1、§4.1 |
| 某个函数的签名和头文件 | `01-api.md` |
| qdisc 内部怎么排队（`net/sched/`） | 不在本套范围，本篇只到 `__dev_xmit_skb()` 的调用点 |
| GRO 怎么合包 | `04-extensions.md`（`net/core/gro.c`） |
| XDP / page_pool | `04-extensions.md` |
| ethtool 的 netlink 消息编解码（`net/ethtool/`） | `04-extensions.md` |
| 驱动侧该怎么写 | `05-usage.md` |
| DTS 属性谁消费 | `06-devicetree.md` |
| 出问题了看哪个计数器 | `07-debug.md` |
| 为什么这样设计（大白话） | `08-feynman.md` |

---

# Part A：一条时间线

## §A1 分配：`alloc_netdev_mqs()`（`net/core/dev.c:10716-10834`）

驱动 probe 的第一步。这个函数做的事比名字暗示的多得多——它把**三段内存合成一次分配**，并且把队列数组一并建好。

### §A1.1 一次分配三段

`net/core/dev.c:10737-10744` 算总大小，`:10746` 一次 `kvzalloc()` 拿到全部：

```c
	alloc_size = sizeof(struct net_device);
	if (sizeof_priv) {
		/* ensure 32-byte alignment of private area */
		alloc_size = ALIGN(alloc_size, NETDEV_ALIGN);
		alloc_size += sizeof_priv;
	}
	/* ensure 32-byte alignment of whole construct */
	alloc_size += NETDEV_ALIGN - 1;

	p = kvzalloc(alloc_size, GFP_KERNEL_ACCOUNT | __GFP_RETRY_MAYFAIL);
```

然后 `:10750-10751` 做对齐并把偏移记下来：

```c
	dev = PTR_ALIGN(p, NETDEV_ALIGN);
	dev->padded = (char *)dev - (char *)p;
```

`dev->padded` 存在的唯一理由是 `free_netdev()` 要还原原始指针。`NETDEV_ALIGN` 是 32（`include/linux/netdevice.h:2386`）。内存布局图见 `02-datastructures.md` §1.1.2。

⚠️ **`__GFP_RETRY_MAYFAIL` 意味着这次分配可能失败**，而它的大小随 `sizeof_priv` 和队列数增长。多队列大网卡在内存紧张时 probe 失败不是 bug。

### §A1.2 队列数组

`:10754-10760` 处理引用计数的两种形态（`CONFIG_PCPU_DEV_REFCNT` 决定 `pcpu_refcnt` 还是 `dev_refcnt`），`:10753` 初始化引用追踪器：

```c
	ref_tracker_dir_init(&dev->refcnt_tracker, 128);
```

发送队列由 `netif_alloc_netdev_queues()`（`:10021-10040`）建，接收队列由 `netif_alloc_rx_queues()`（`:9954-9986`）建。**两者都在这里就分配好了，不是等 `open` 才建**——所以 `real_num_tx_queues` 只能在 `num_tx_queues` 范围内调（§B3）。

### §A1.3 调用者传进来的 `setup` 回调

`alloc_netdev_mqs()` 的第五个参数是 `void (*setup)(struct net_device *)`，绝大多数以太网驱动传的是 `ether_setup`（经 `alloc_etherdev_mqs()` 包装）。它在返回之前被调用，所以驱动拿到的 `net_device` 已经是"以太网形态"。

## §A2 定型：`ether_setup()`（`net/ethernet/eth.c:347-364`）

这个函数只有 18 行，但它决定了这块网卡在协议栈眼里是什么。逐行是（行号即赋值发生处）：

| 行 | 赋值 | 含义 |
|---|---|---|
| :349 | `header_ops = &eth_header_ops` | 二层头怎么构造/解析 |
| :350 | `type = ARPHRD_ETHER` | ARP 硬件类型，用户态看到的 `link/ether` |
| :351 | `hard_header_len = ETH_HLEN` | 14 |
| :352 | `min_header_len = ETH_HLEN` | 收到短于 14 字节的帧直接丢 |
| :353 | `mtu = ETH_DATA_LEN` | 1500 |
| :354 | `min_mtu = ETH_MIN_MTU` | 68 |
| :355 | `max_mtu = ETH_DATA_LEN` | **1500** |
| :356 | `addr_len = ETH_ALEN` | 6 |
| :357 | `tx_queue_len = DEFAULT_TX_QUEUE_LEN` | 1000 |
| :358 | `flags = IFF_BROADCAST \| IFF_MULTICAST` | 初始 flags，注意**不含 `IFF_UP`** |
| :359 | `priv_flags \|= IFF_TX_SKB_SHARING` | 允许多个队列共享同一个 skb |
| :361 | `eth_broadcast_addr(dev->broadcast)` | 广播地址填 `ff:ff:ff:ff:ff:ff` |

⚠️ **`max_mtu` 被设成 1500**。想支持 jumbo frame 的驱动必须在 `ether_setup()` **之后**自己抬高 `dev->max_mtu`，否则 `ip link set mtu 9000` 会被 `dev_set_mtu()`（§A4）里的范围检查挡掉，而且报的是 `-EINVAL`，看不出是谁设的上限。这是 arm64 SoC 网卡驱动最常见的一个遗漏。

`alloc_etherdev_mqs()` 的 kerneldoc（`net/ethernet/eth.c:366-379`）特意写明私有区是 **32 字节对齐**而不是 32 位对齐（第 377-378 行），措辞是 "A 32-byte (not bit) alignment is enforced"——这句话是给读错的人准备的。

## §A3 注册：`register_netdevice()`（`net/core/dev.c:10111-10281`）

170 行，是本子系统最长也最关键的一条流程。`register_netdev()`（`:10341-10350`）只是它加一把 `rtnl_lock()` 的包装。

### §A3.1 进门检查（:10116-10134）

```c
	BUILD_BUG_ON(sizeof(netdev_features_t) * BITS_PER_BYTE <
		     NETDEV_FEATURE_COUNT);
	BUG_ON(dev_boot_phase);
	ASSERT_RTNL();
	might_sleep();
	BUG_ON(dev->reg_state != NETREG_UNINITIALIZED);
```

四件事：`netdev_features_t` 的位宽必须装得下所有 feature（编译期）；不能在启动早期调；**必须持 rtnl**；**这个函数会睡**（所以不能在原子上下文调）；`reg_state` 必须是初始值。

紧接着 `ethtool_check_ops(dev->ethtool_ops)`（:10127）——它检查驱动填的 `ethtool_ops` 有没有同时实现互斥的新旧接口。然后 `:10131-10132` 初始化 `addr_list_lock` 并挂 lockdep class，`:10134` 的 `dev_get_valid_name()` 处理 `%d` 形式的名字模板（`eth%d` → `eth0`）。

### §A3.2 第一次进驱动：`ndo_init`（:10144-10151）

```c
	if (dev->netdev_ops->ndo_init) {
		ret = dev->netdev_ops->ndo_init(dev);
		if (ret) {
			if (ret > 0)
				ret = -EIO;
			goto err_free_name;
		}
	}
```

⚠️ **`ndo_init` 返回正数会被改写成 `-EIO`**。这是一个静默的语义转换：驱动如果不小心 `return 1`，日志里看到的是 `-EIO`，跟真正的 IO 错误无法区分。

紧跟着一条一致性检查（:10153-10160）：如果驱动声明了 `NETIF_F_HW_VLAN_CTAG_FILTER` 却没实现 `ndo_vlan_rx_add_vid` / `ndo_vlan_rx_kill_vid`（或反之），直接：

```c
		netdev_WARN(dev, "Buggy VLAN acceleration in driver!\n");
		ret = -EINVAL;
		goto err_uninit;
```

这条 WARN 的字符串可以直接 `dmesg | grep` 找。

### §A3.3 索引与统计（:10162-10170）

`netdev_do_alloc_pcpu_stats()`（:10162）按 `dev->pcpu_stat_type` 分配 per-CPU 统计容器（三种类型见 `02-datastructures.md` §6.2）。然后 `dev_new_index()`（:10168）取一个 ifindex，并用 `__dev_get_by_index()` 确认没撞（:10169-10170）——撞了就 `-EBUSY` 跳 `err_free_pcpu`。

### §A3.4 features 的初始值是怎么定的（:10175-10212）

这一段是整个 features 机制的起点，也是最容易看错的地方。关键只有一行：

```c
	dev->wanted_features = dev->features & dev->hw_features;
```

在 `net/core/dev.c:10183`。**驱动在 probe 里往 `dev->features` 里塞的东西，只有同时出现在 `dev->hw_features` 里才会被保留**——因为 `__netdev_update_features()`（§A4）之后会拿 `wanted_features` 当基准重算。忘记设 `hw_features` 的驱动会发现自己开的 offload 在注册后消失了，而且没有任何日志。

这一行之前，核心先自己往两个字段里加了软件 feature（:10175-10176）：

```c
	dev->hw_features |= (NETIF_F_SOFT_FEATURES | NETIF_F_SOFT_FEATURES_OFF);
	dev->features |= NETIF_F_SOFT_FEATURES;
```

也就是 GSO / GRO 这类纯软件实现的能力，驱动不需要声明也会有。

这一行之后是一串继承规则（都在 `:10185-10212`）：

| 行 | 规则 |
|---|---|
| :10185-10186 | 非 loopback 设备加 `NETIF_F_NOCACHE_COPY` |
| :10193-10200 | `TSO_MANGLEID` 在四个 features 字段里跟随 `TSO` |
| :10204 | `vlan_features \|= NETIF_F_HIGHDMA` |
| :10208 | `hw_enc_features \|= NETIF_F_SG \| NETIF_F_GSO_PARTIAL` |
| :10212 | `mpls_features \|= NETIF_F_SG` |

四个 features 字段的分工见 `02-datastructures.md` §1.0.4。

### §A3.5 上线（:10214-10266）

顺序在这里非常重要，逐步是：

| 行 | 动作 | 为什么在这个位置 |
|---|---|---|
| :10214 | `call_netdevice_notifiers(NETDEV_POST_INIT)` | 给别的子系统一个"设备已成形但还没进 sysfs"的钩子 |
| :10219 | `netdev_register_kobject(dev)` | 建 `/sys/class/net/<name>/` |
| :10220-10222 | `write_lock(&dev_base_lock)` + `reg_state = NETREG_REGISTERED` | 只有 kobject 建成功才算注册成功 |
| :10226 | `__netdev_update_features(dev)` | 第一次 features 重算，**在 sysfs 之后**，所以用户态第一次读到的就是最终值 |
| :10233 | `set_bit(__LINK_STATE_PRESENT, &dev->state)` | 从此 `netif_device_present()` 为真 |
| :10235 | `linkwatch_init_dev(dev)` | 接入链路状态机（§B6） |
| :10237 | `dev_init_scheduler(dev)` | 挂上 `noop_qdisc` |
| :10239 | `netdev_hold(..., &dev->dev_registered_tracker, ...)` | **注册本身持一个引用**，`unregister` 时才放（§A9） |
| :10240 | `list_netdevice(dev)` | 进全局链表和哈希表，从此 `dev_get_by_name()` 能找到 |
| :10242 | `add_device_randomness(dev->dev_addr, dev->addr_len)` | 把 MAC 喂给随机数池 |
| :10248-10249 | `NET_ADDR_PERM` → `memcpy(dev->perm_addr, dev->dev_addr, ...)` | 只有地址类型是"永久"时才记 `perm_addr` |
| :10252 | `call_netdevice_notifiers(NETDEV_REGISTER)` | 通知全部订阅者 |
| :10264-10266 | `rtmsg_ifinfo(RTM_NEWLINK, ...)` | 通知用户态 |

⚠️ **`NETDEV_REGISTER` 通知失败会触发一次完整的回滚**（:10254-10258）：

```c
		/* Expect explicit free_netdev() on failure */
		dev->needs_free_netdev = false;
		unregister_netdevice_queue(dev, NULL);
		goto out;
```

注意它**把 `needs_free_netdev` 强制清成 false**，也就是这条路径上核心不替你释放，驱动必须自己 `free_netdev()`。这跟成功路径的约定相反，是 `register_netdev()` 返回错误后驱动最容易写错的地方。

### §A3.6 三个错误标签

| 标签 | 行 | 做什么 |
|---|---|---|
| `err_free_pcpu:` | :10271 | 释放 per-CPU 统计 |
| `err_uninit:` | :10273 | 调 `ndo_uninit`（:10274-10275），再调 `priv_destructor`（:10276-10277） |
| `err_free_name:` | :10278 | 释放 name_node |

**回滚是顺序倒着来的部分回滚**，不是全量。所以 `ndo_init` 里申请的东西必须在 `ndo_uninit` 里放，不能指望 `remove()`。

## §A4 features 重算：`__netdev_update_features()`（`net/core/dev.c:9801-9891`）

这是唯一会调 `ndo_set_features` 的地方，三个包装函数（`netdev_update_features` `:9901-9905`、`netdev_change_features` `:9918-9922`）都落到它身上。

### §A4.1 五步计算

```
 wanted_features                          §A3.4 定的基准
      ↓  ndo_fix_features(dev, features)   :9812-9813   驱动可以砍
      ↓  netdev_fix_features(dev, features):9816        核心按依赖关系砍
      ↓  netdev_sync_upper_features(...)   :9819-9820   上层设备关了的不能开
      ↓  == dev->features ?  → goto sync_lower  :9822-9823
      ↓  ndo_set_features(dev, features)   :9828-9829   真正下发硬件
 sync_lower:
      ↓  netdev_sync_lower_features(...)   :9847-9848   下层设备跟着关
      ↓  dev->features = features           :9887
```

两个"砍"的顺序不能反：**驱动的 `ndo_fix_features` 先跑，核心的 `netdev_fix_features` 后跑**。注释（:9815）说明了理由——"driver might be less strict about feature dependencies"，核心兜底。

### §A4.2 失败时怎么办

```c
	if (unlikely(err < 0)) {
		netdev_err(dev,
			"set_features() failed (%d); wanted %pNF, left %pNF\n",
			err, &features, &dev->features);
		/* return non-0 since some features might have changed and
		 * it's better to fire a spurious notification than miss it
		 */
		return -1;
	}
```

`net/core/dev.c:9833-9841`。⚠️ **`ndo_set_features` 失败时 `dev->features` 不更新**，但函数返回非 0，调用者会照样发 `NETDEV_FEAT_CHANGE` 通知。注释直说这是"宁可多发一次"的取舍。这个 `netdev_err` 的字符串是排查 offload 开不起来的第一现场。

### §A4.3 三个 offload 的特殊处理

`:9850-9885` 有三段几乎相同的代码，处理 `NETIF_F_RX_UDP_TUNNEL_PORT`、`NETIF_F_HW_VLAN_CTAG_FILTER`、`NETIF_F_HW_VLAN_STAG_FILTER`。共同点：**`dev->features` 必须在调 `*_get_*_info()` 之前更新、在调 `*_drop_*_info()` 之后更新**。注释（:9854-9860）把理由写清了——那些函数自己会检查 `dev->features`。

所以 `:9862`、`:9871`、`:9880` 三处 `dev->features = features` 是"提前赋值"，跟结尾 `:9887` 的那次不是重复。

⚠️ 这三段用的是 `err |=`（`:9872`、`:9881`）而不是 `err =`。返回值语义因此变成"任意一个失败就非 0"，函数末尾 `return err < 0 ? 0 : 1;`（:9890）再把它折成 0/1。**返回 0 表示"没变化"，返回 1 表示"变了"**，跟常见的 0=成功约定相反。

### §A4.4 三个触发点

见 §B4。

## §A5 打开：`dev_open()`（`net/core/dev.c:1517-1532`）→ `__dev_open()`（`:1455-1502`）

### §A5.1 前置条件

`__dev_open()` 开头 `ASSERT_RTNL()`（:1460）和 `dev_addr_check(dev)`（:1461），然后一段容易被忽略的逻辑（:1463-1469）：

```c
	if (!netif_device_present(dev)) {
		/* may be detached because parent is runtime-suspended */
		if (dev->dev.parent)
			pm_runtime_resume(dev->dev.parent);
		if (!netif_device_present(dev))
			return -ENODEV;
	}
```

**如果设备被 detach 了，核心会先尝试把父设备（arm64 上通常是 platform device）从 runtime suspend 里拉起来**，再检查一次。这是 `ip link set up` 在省电状态下仍能工作的原因。

### §A5.2 顺序

| 行 | 动作 |
|---|---|
| :1475 | `netpoll_poll_disable(dev)`——注释（:1471-1474）说明是防止 `ndo_poll_controller` 与 open 并发 |
| :1477-1480 | `NETDEV_PRE_UP` 通知，**任何订阅者否决就直接返回**（这是唯一能阻止 up 的通知） |
| :1482 | `set_bit(__LINK_STATE_START, &dev->state)`——`netif_running()` 从此为真 |
| :1484-1485 | `ndo_validate_addr`（如果有） |
| :1487-1488 | `ndo_open`——驱动真正上电、申请中断、填描述符环 |
| :1490 | `netpoll_poll_enable(dev)` |
| :1493 | 失败 → 清 `__LINK_STATE_START` |
| :1495 | 成功 → `dev->flags \|= IFF_UP` |
| :1496 | `dev_set_rx_mode(dev)`——下发地址表 |
| :1497 | `dev_activate(dev)`——把真 qdisc 换上 |
| :1498 | `add_device_randomness(dev->dev_addr, dev->addr_len)` |

⚠️ **`__LINK_STATE_START` 在 `ndo_open` 之前置位，`IFF_UP` 在之后**。这两个"起来了"的标志有一段时间不一致，`netif_running()` 为真但 `IFF_UP` 未置。驱动的 `ndo_open` 里如果去读 `dev->flags & IFF_UP` 会得到 0——要判断"是不是正在 up"应该用 `netif_running()`。

## §A6 发送：`__dev_queue_xmit()`（`net/core/dev.c:4252-4375`）

124 行，协议栈交出 skb 的唯一入口。`dev_queue_xmit()` 是它的一个 `static inline` 包装。

### §A6.1 一把锁管两件事

```c
	/* Disable soft irqs for various locks below. Also
	 * stops preemption for RCU.
	 */
	rcu_read_lock_bh();
```

`net/core/dev.c:4266-4269`。注释把两个作用都写明了：**它既是 RCU 读侧临界区，又关掉了软中断**。整个发送路径都在这个保护里跑，所以 `ndo_start_xmit` 一定在关软中断的上下文——驱动里不能睡。

### §A6.2 前半段：加工 skb

| 行 | 动作 | 备注 |
|---|---|---|
| :4260 | `skb_reset_mac_header(skb)` | |
| :4261 | `skb_assert_len(skb)` | 长度为 0 的 skb 在这里被抓住 |
| :4263-4264 | `SKBTX_SCHED_TSTAMP` → `__skb_tstamp_tx(..., SCM_TSTAMP_SCHED)` | 硬件时间戳的"入队"点 |
| :4271 | `skb_update_prio(skb)` | 按 cgroup net_prio 改 `skb->priority` |
| :4273 | `qdisc_pkt_len_init(skb)` | 算 qdisc 计费用的长度（含 GSO 展开后的估算） |
| :4275 | `skb->tc_at_ingress = 0` | 明确标成出方向 |

### §A6.3 egress hook（:4278-4295）

这一整段被 `static_branch_unlikely(&egress_needed_key)` 包着，**没有配 tc egress 规则或 nftables egress hook 时是零开销**：

| 行 | 动作 |
|---|---|
| :4280 | `nf_hook_egress(skb, &rc, dev)` |
| :4285 | `netdev_xmit_skip_txqueue(false)` |
| :4288 | `sch_handle_egress(skb, &rc, dev)` |
| :4294 | `netdev_tx_queue_mapping(dev, skb)` |

⚠️ egress hook 可以**改变队列选择结果**（:4294 那一步），也可以直接吃掉 skb。排查"包在出方向消失"时这一段是第一嫌疑。

### §A6.4 选队列并分叉（:4300-4314）

```c
	if (dev->priv_flags & IFF_XMIT_DST_RELEASE)
		skb_dst_drop(skb);
	else
		skb_dst_force(skb);
```

（`:4300-4303`）然后：

| 行 | 动作 |
|---|---|
| :4306 | `txq = netdev_core_pick_tx(dev, skb, sb_dev)` |
| :4308 | `q = rcu_dereference_bh(txq->qdisc)` |
| :4310 | `trace_net_dev_queue(skb)` ← 唯一的入队 tracepoint |
| :4311-4314 | **`if (q->enqueue) { rc = __dev_xmit_skb(skb, q, dev, txq); goto out; }`** |

`netdev_core_pick_tx()` 是 `netdev_pick_tx()`（`:4176-4200`）的包装，后者依次考虑 `ndo_select_queue`、XPS、`skb_tx_hash`。

**有 qdisc 就到这里结束**——`__dev_xmit_skb()`（起于 `:3881`）负责入队、`__qdisc_run()`、最终经 `sch_direct_xmit()` 回到 `dev_hard_start_xmit()`。这是绝大多数以太网卡的路径。

### §A6.5 无队列路径（:4316-4370）

只有 `IFF_NO_QUEUE` 的设备（loopback、隧道）走这条。它有一段很长的注释（:4316-4327）解释为什么必须自己加锁：

| 行 | 动作 |
|---|---|
| :4328 | `if (dev->flags & IFF_UP)` |
| :4334 | `if (READ_ONCE(txq->xmit_lock_owner) != cpu)` |
| :4335-4336 | `dev_xmit_recursion()` → `goto recursion_alert` |
| :4338 | `validate_xmit_skb(skb, dev, &again)` |
| :4342 | `HARD_TX_LOCK(dev, txq, cpu)` |
| :4344 | `if (!netif_xmit_stopped(txq))` |
| :4345-4347 | `dev_xmit_recursion_inc()` / `dev_hard_start_xmit()` / `dev_xmit_recursion_dec()` |
| :4348 | `if (dev_xmit_complete(rc)) goto out` |

两条 `net_crit_ratelimited` 的字符串可以直接搜：

- `"Virtual device %s asks to queue packet!\n"`（:4354-4355）——队列停了但设备说没队列。
- `"Dead loop on virtual device %s, fix it urgently!\n"`（:4361-4362）——递归深度超限。

失败收尾：`rc = -ENETDOWN`（:4366）、`dev_core_stats_tx_dropped_inc(dev)`（:4369）、`kfree_skb_list(skb)`（:4370）。

⚠️ 注意 `-ENETDOWN` 这个返回值：**协议栈看到设备没 UP 时得到的是 `-ENETDOWN`，而不是丢包成功**。同时 `tx_dropped` 也加了一次，所以同一个包在 `/proc/net/dev` 和 socket 的 errno 里都会体现。

## §A7 交给驱动：`dev_hard_start_xmit()`（`net/core/dev.c:3676-3702`）

27 行，是核心与驱动的最后一道边界。三条路径（qdisc 的 `sch_direct_xmit`、无队列的直发、`netpoll`）都汇到这里。

```c
	while (skb) {
		struct sk_buff *next = skb->next;

		skb_mark_not_on_list(skb);
		rc = xmit_one(skb, dev, txq, next != NULL);
		if (unlikely(!dev_xmit_complete(rc))) {
			skb->next = next;
			goto out;
		}
```

`net/core/dev.c:3682-3690`。两个关键点：

- **`xmit_one()` 的第四个参数是 `more`**（`:3686` 的 `next != NULL`）。它最终变成 `ndo_start_xmit` 之前设的 `skb->xmit_more` 提示，驱动据此决定要不要立刻敲门铃。批量发送的性能全靠这一位。
- **驱动返回非 `NETDEV_TX_OK` 时，skb 被重新挂回链表头**（`:3688`）由调用者重试。所以 `ndo_start_xmit` 返回 `NETDEV_TX_BUSY` 不会丢包，但会浪费一次 qdisc 循环。

然后是一段容易被误解的检查（`:3693-3696`）：

```c
		if (netif_tx_queue_stopped(txq) && skb) {
			rc = NETDEV_TX_BUSY;
			break;
		}
```

**驱动在 `ndo_start_xmit` 里停了队列，而链表还有剩，就整批中止**。这解释了为什么驱动应该在"还剩一个描述符时"就停队列而不是"用完了才停"——否则最后一个包会被反复重试。

## §A8 收包：`net_rx_action()`（`net/core/dev.c:6761-6809`）

NAPI 软中断的处理函数。它不认识任何具体驱动，只做"轮流给每个 napi 一点预算"。

### §A8.1 预算的两个维度

```c
	unsigned long time_limit = jiffies +
		usecs_to_jiffies(READ_ONCE(netdev_budget_usecs));
	int budget = READ_ONCE(netdev_budget);
```

`:6764-6766`。**包数和时间两个都会触发退出**，两个都是 sysctl 可调（见 `07-debug.md`）：

| 变量 | 默认值 | 定义 |
|---|---|---|
| `netdev_budget` | 300 | `net/core/dev.c:4424` |
| `netdev_budget_usecs` | `2 * USEC_PER_SEC / HZ`——HZ=1000 时 2000，HZ=250 时 8000 | `net/core/dev.c:4426` |
| `dev_rx_weight` | 64 | `net/core/dev.c:4430`，只作用于 backlog（§A8.3） |

⚠️ **`netdev_budget_usecs` 的默认值随 HZ 变**，写成固定数字会在不同配置上算错。它的 sysctl 下界也是同一个表达式（`net/core/sysctl_net_core.c:33`）。

### §A8.2 主循环

| 行 | 动作 |
|---|---|
| :6770-6772 | `local_irq_disable()` → `list_splice_init(&sd->poll_list, &list)` → `local_irq_enable()`。**先把链表整个摘下来**，后续处理不再跟硬中断竞争 |
| :6777 | `skb_defer_free_flush(sd)`——释放别的 CPU 攒给本 CPU 的 skb（§B7） |
| :6779-6783 | 链表空：没有待发的 RPS IPI 且 repoll 也空 → `goto end` |
| :6785-6786 | `n = list_first_entry(...)` → `budget -= napi_poll(n, &repoll)` |
| :6792-6796 | 预算耗尽或超时 → **`sd->time_squeeze++`** 然后 break |
| :6801-6803 | 把 `sd->poll_list`（循环期间新加的）、`repoll`、`list` 三段拼回去 |
| :6804-6805 | 还有剩 → `__raise_softirq_irqoff(NET_RX_SOFTIRQ)` |
| :6807 | `net_rps_action_and_irq_enable(sd)` |

⚠️ `sd->time_squeeze` 就是 `/proc/net/softnet_stat` 的**第 3 列**（`net/core/net-procfs.c:173`）。它涨得快说明单次软中断处理不完——但**不代表丢包**，只代表被推迟。把它当丢包指标是最常见的误读，详见 `07-debug.md`。

注释（:6788-6791）解释了为什么允许跑满 2 jiffies：`"Allow this to run for 2 jiffies since which will allow an average latency of 1.5/HZ."`

### §A8.3 `process_backlog()`（`net/core/dev.c:5994-6042`）：伪 NAPI

`netif_rx()`（`:5082-5095`）那条老路径把 skb 放进 `sd->input_pkt_queue`，然后靠一个假的 napi 实例（`sd->backlog`）来消费。`process_backlog()` 就是它的 `poll` 函数。

```c
	struct softnet_data *sd = container_of(napi, struct softnet_data, backlog);
```

`:5996`——从 napi 反推回 `softnet_data`，这是 `sd->backlog` 不是独立分配的证据。

两级队列的搬运在 `:6012-6037`：

- 外层循环从 `sd->process_queue` 取（`:6012`），逐个 `__netif_receive_skb()`（`:6014`）。
- 队空时在 rps 锁保护下把 `sd->input_pkt_queue` 整段 splice 到 `process_queue`（`:6035-6036`）。

⚠️ **它不用 `napi_complete_done()`，而是直接 `napi->state = 0`**（`:6032`）。注释（`:6024-6031`）给了理由——只有本 CPU 操作这个 napi，`NAPI_STATE_SCHED` 是唯一可能置的位，所以既不需要 `clear_bit()` 也不需要内存屏障。这是全内核少见的"手写一个 `__napi_complete()`"的地方，读代码时不要以为漏了。

`napi->weight = READ_ONCE(dev_rx_weight)`（`:6008`）说明 backlog 的权重是 sysctl 可调的，跟真驱动的 `weight` 参数不是一回事。

### §A8.4 收工：`napi_complete_done()`（`net/core/dev.c:6113-6182`）

驱动 `poll` 函数没跑满预算时调它。70 行，里面有本子系统最精巧的一段并发处理。

第一步是两个早退（`:6124-6126`）：`NAPIF_STATE_NPSVC`（netpoll 借用）或 `NAPIF_STATE_IN_BUSY_POLL` 时直接返回 false，调用者不该再开中断。

然后是 `gro_flush_timeout` / `napi_defer_hard_irqs` 的处理（`:6128-6138`）：如果配了 defer，**函数会返回 false 让驱动不要开硬中断**，靠 hrtimer 再来一次。

核心是那个 cmpxchg 循环（`:6156-6171`）：

```c
		WARN_ON_ONCE(!(val & NAPIF_STATE_SCHED));

		new = val & ~(NAPIF_STATE_MISSED | NAPIF_STATE_SCHED |
			      NAPIF_STATE_SCHED_THREADED |
			      NAPIF_STATE_PREFER_BUSY_POLL);

		/* If STATE_MISSED was set, leave STATE_SCHED set,
		 * because we will call napi->poll() one more time.
		 * This C code was suggested by Alexander Duyck to help gcc.
		 */
		new |= (val & NAPIF_STATE_MISSED) / NAPIF_STATE_MISSED *
						    NAPIF_STATE_SCHED;
```

`:6159-6170`。那个除法再乘法的写法（`:6169-6170`）是"如果 MISSED 置了就把 SCHED 也置上"的无分支版本，注释明确说是 Alexander Duyck 为了帮 gcc 生成更好代码提的。**读的时候不要试图理解算术含义，它就是一个条件设位。**

收尾（`:6173-6180`）：

```c
	if (unlikely(val & NAPIF_STATE_MISSED)) {
		__napi_schedule(n);
		return false;
	}
```

**MISSED 置了就立刻重新调度自己并返回 false**——中断在 poll 期间来过，不能开中断走人。否则如果有 timeout 就起 hrtimer（`:6178-6180`，模式是 `HRTIMER_MODE_REL_PINNED`）。

SCHED/MISSED 这对状态位的完整不变量见 §B2。

## §A9 关闭：`dev_close()`（`net/core/dev.c:1617-1626`）→ `__dev_close_many()`（`:1535-1577`）

真正的逻辑在 `__dev_close_many()`，`__dev_close()`（`:1579-1586`）只是把单个设备包成一个单元素链表转发过去。批量版本存在的理由是 §A10——注销一批设备时要一次性关掉。

### §A9.1 两轮循环

第一轮（`:1542-1557`）只做"宣布要关"：

| 行 | 动作 |
|---|---|
| :1544 | `netpoll_poll_disable(dev)` |
| :1546 | `call_netdevice_notifiers(NETDEV_GOING_DOWN, dev)` |
| :1548 | `clear_bit(__LINK_STATE_START, &dev->state)` |
| :1556 | `smp_mb__after_atomic()` |

注释（`:1550-1555`）说明为什么这里只清位不做别的：

> Synchronize to scheduled poll. We cannot touch poll list, it can be even on different cpu. So just clear netif_running(). dev->stop() will invoke napi_disable() on all of it's napi_struct instances on this device.

也就是说——**核心不负责停 NAPI，驱动的 `ndo_stop` 必须自己调 `napi_disable()`**。这是驱动侧最容易漏的一条契约，漏了的后果是 poll 在设备已下电后还在跑。

中间 `dev_deactivate_many(head)`（`:1559`）换回 `noop_qdisc` 并等 qdisc 停干净。

第二轮（`:1561-1576`）才真正调驱动：

```c
		if (ops->ndo_stop)
			ops->ndo_stop(dev);

		dev->flags &= ~IFF_UP;
		netpoll_poll_enable(dev);
```

注释（`:1564-1570`）写明 `ndo_stop` **不允许失败**（"This cannot fail"），而且**可能在 DETACH 之后被调用**。所以 `ndo_stop` 里访问寄存器前要检查 `netif_device_present()`。

`dev_close_many()`（`:1588` 起）在两轮之外多做一件事：先把没 UP 的设备从链表里剔掉（`:1593-1595`），最后逐个发 `rtmsg_ifinfo(RTM_NEWLINK, dev, IFF_UP|IFF_RUNNING, GFP_KERNEL)`（`:1600`）通知用户态。

## §A10 注销：`unregister_netdevice_many()`（`net/core/dev.c:10948-11047`）

100 行，五个 `list_for_each_entry` 分段，中间夹两次 `synchronize_net()`。`unregister_netdev()`（`:11061-11066`）和 `unregister_netdevice_queue()`（`:10926-10938`）都最终落到它。

### §A10.1 五段

**第一段（`:10959-10974`）状态检查。** 从没注册过的设备（`reg_state == NETREG_UNINITIALIZED`）被 `WARN_ON(1)` 之后剔出链表继续（`:10964-10971`），注释说这是给"初始化失败回滚"留的后门。其余设备 `dev->dismantle = true`（`:10972`）并 `BUG_ON(reg_state != NETREG_REGISTERED)`（`:10973`）。

**第二段（`:10977-10979`）关掉还在跑的。** 全部塞进 `close_head` 然后 `dev_close_many(&close_head, true)`。

**第三段（`:10981-10990`）从链表摘掉。**

```c
		write_lock(&dev_base_lock);
		unlist_netdevice(dev, false);
		dev->reg_state = NETREG_UNREGISTERING;
		write_unlock(&dev_base_lock);
	}
	flush_all_backlogs();

	synchronize_net();
```

`flush_all_backlogs()`（实现在 `:5919`）把每个 CPU 的 backlog 里属于这些设备的 skb 丢掉。`synchronize_net()` 保证所有正在跑的 RCU 读侧（比如 §A6 那个 `rcu_read_lock_bh()`）都退出了。**这一步之后没人能再通过查表拿到这个设备。**

**第四段（`:10992-11037`）真正拆。** 顺序是：

| 行 | 动作 |
|---|---|
| :10996 | `dev_shutdown(dev)`——拆 qdisc |
| :10998 | `dev_xdp_uninstall(dev)` |
| :11000 | `netdev_offload_xstats_disable_all(dev)` |
| :11005 | `call_netdevice_notifiers(NETDEV_UNREGISTER, dev)` |
| :11007-11010 | 先**构造** `RTM_DELLINK` 消息（`rtmsg_ifinfo_build_skb`），还不发 |
| :11015-11016 | `dev_uc_flush(dev)` / `dev_mc_flush(dev)` |
| :11018-11019 | `netdev_name_node_alt_flush` / `netdev_name_node_free` |
| :11021-11022 | `ndo_uninit(dev)` |
| :11024-11025 | **现在才发** `rtmsg_ifinfo_send(skb, dev, GFP_KERNEL)` |
| :11028-11029 | `WARN_ON(netdev_has_any_upper_dev(dev))` / `..._lower_dev(dev)` |
| :11032 | `netdev_unregister_kobject(dev)`——sysfs 目录消失 |
| :11035 | `netif_reset_xps_queues_gt(dev, 0)`（`CONFIG_XPS`） |

⚠️ **`RTM_DELLINK` 消息是"先构造后发送"的**（`:11009` vs `:11025`）。理由是构造需要读设备属性，而 `ndo_uninit` 之后那些属性可能已经不可读了。这个拆分是有意的，不是遗留代码。

注释（`:11027`）"Notifier chain MUST detach us all upper devices"说明那两个 `WARN_ON` 是在检查订阅者有没有履行义务——**bond / bridge / vlan 必须在 `NETDEV_UNREGISTER` 里把自己摘掉**。

**第五段（`:11041-11044`）交给 todo。**

```c
		netdev_put(dev, &dev->dev_registered_tracker);
		net_set_todo(dev);
```

`netdev_put` 放的正是 §A3.5 `:10239` 那一次 `netdev_hold`。**放完之后引用计数通常还不是 0**——协议栈、路由表、socket 都可能还持着。所以不能在这里释放，只能 `net_set_todo()`（实现 `:9651`）挂进 `net_todo_list`。

## §A11 收尾：`netdev_run_todo()`（`net/core/dev.c:10475-10538`）

这个函数的调用点很反直觉：**它由 `rtnl_unlock()` 调用**。也就是说注销的最后一段在"释放 rtnl 锁"这个动作里顺带完成。函数自己 `:10496` 还会再 `__rtnl_unlock()` 一次——它进来时是持锁的。

### §A11.1 三段

**第一段（`:10479-10500`）取快照。**

```c
	list_replace_init(&net_todo_list, &list);

	__rtnl_unlock();

	/* Wait for rcu callbacks to finish before next phase */
	if (!list_empty(&list))
		rcu_barrier();
```

`:10494-10500`。**先摘链表再放锁**，所以后续新来的注销请求会进一个空链表，不会跟本轮混。`rcu_barrier()` 等的是 RCU 回调（比 `synchronize_net()` 更强）。

`CONFIG_LOCKDEP` 下前面还有一段（`:10482-10490`）处理 `nested_level`，与注销逻辑无关。

**第二段（`:10502-10513`）改状态。**

```c
		if (unlikely(dev->reg_state != NETREG_UNREGISTERING)) {
			netdev_WARN(dev, "run_todo but not unregistering\n");
			list_del(&dev->todo_list);
			continue;
		}

		write_lock(&dev_base_lock);
		dev->reg_state = NETREG_UNREGISTERED;
		write_unlock(&dev_base_lock);
		linkwatch_forget_dev(dev);
```

那条 `netdev_WARN` 的字符串（`:10504`）在 dmesg 里出现意味着状态机被破坏了。

**第三段（`:10515-10537`）等引用归零然后释放。**

```c
		dev = netdev_wait_allrefs_any(&list);
		list_del(&dev->todo_list);

		/* paranoia */
		BUG_ON(netdev_refcnt_read(dev) != 1);
		BUG_ON(!list_empty(&dev->ptype_all));
		BUG_ON(!list_empty(&dev->ptype_specific));
		WARN_ON(rcu_access_pointer(dev->ip_ptr));
		WARN_ON(rcu_access_pointer(dev->ip6_ptr));

		netdev_do_free_pcpu_stats(dev);
		if (dev->priv_destructor)
			dev->priv_destructor(dev);
		if (dev->needs_free_netdev)
			free_netdev(dev);
```

`:10516-10530`。`netdev_wait_allrefs_any()`（`:10383`，kerneldoc 在 `:10372`）是**会睡很久**的那个函数。那条著名的日志就在它里面（`net/core/dev.c:10441`），级别是 `pr_emerg`：

```
unregister_netdevice: waiting for %s to become free. Usage count = %d
```

看到它意味着有人漏放了引用，`Usage count` 就是当前的 `netdev_refcnt_read(dev)`。排查方法见 `07-debug.md`。

⚠️ **`priv_destructor` 在 `free_netdev()` 之前调**（`:10527-10530`）。所以驱动的析构器里还能安全访问私有区。这跟 §A3.6 `err_uninit` 路径的顺序一致。

最后 `:10532-10533` 唤醒 `netdev_unregistering_wq`，`:10536` `kobject_put(&dev->dev.kobj)` 放掉 sysfs 那一份引用。

## §A12 释放：`free_netdev()`（`net/core/dev.c:10846-10894`）

### §A12.1 它可能不释放

```c
	if (dev->reg_state == NETREG_UNREGISTERING) {
		/* NETREG_UNINITIALIZED is done by free_netdev() */
		dev->needs_free_netdev = true;
		return;
	}
```

`:10852-10860` 一带。**驱动在 `unregister_netdev()` 之后立刻调 `free_netdev()` 是允许的，但那次调用什么也不做**，只是把 `needs_free_netdev` 置上，让 §A11 第三段来释放。

### §A12.2 真释放的顺序

| 行 | 动作 |
|---|---|
| :10862 | `netif_free_tx_queues(dev)`（实现 `:10016-10019`） |
| :10863 | `netif_free_rx_queues(dev)`（实现 `:9988-10000`） |
| :10865 | `kfree(rcu_dereference_protected(dev->ingress_queue, 1))` |
| :10868 | `dev_addr_flush(dev)` |
| :10870-10871 | `list_for_each_entry_safe(...) netif_napi_del(p)` |
| :10878 | `free_percpu(dev->core_stats)` |

最后按 `dev->padded` 还原原始指针并 `kvfree()`。

⚠️ **`free_netdev()` 会自己删掉所有 napi**（`:10870-10871`），驱动不需要在 `remove()` 里逐个 `netif_napi_del()`。但**如果驱动想在 `free_netdev()` 之前复用 napi，就必须自己删**——两次删除是安全的（`__netif_napi_del()` `:6548-6564` 会检查 `NAPI_STATE_LISTED`）。

---

# Part B：横切关注点

Part A 是"什么时候发生什么"，Part B 是"为什么这样是对的"。七节互不依赖，可以单独读。

## §B1 rtnl 锁：一把巨锁管住整个配置面

### §B1.1 它是什么

`DEFINE_MUTEX(rtnl_mutex)` 在 `net/core/rtnetlink.c:70`，`rtnl_lock()` 紧接在 `:72`。**它是一把睡眠锁，粒度是"整个网络配置面"**——不分设备、不分 namespace。

Part A 里 `ASSERT_RTNL()` 出现在：`register_netdevice` `:10119`、`__dev_open` `:1460`、`__dev_close_many` `:1539`、`unregister_netdevice_many` `:10954`、`__netdev_update_features` `:9808`。也就是**每一个改变设备状态的动作都要它**。

### §B1.2 它保护什么、不保护什么

| rtnl 保护 | rtnl 不保护 |
|---|---|
| `dev->flags`、`dev->features`、`dev->mtu` 等配置字段 | 发送/接收路径读到的同一批字段（靠 `READ_ONCE` 和 RCU） |
| 设备的注册状态（`reg_state`） | `napi->state`（靠原子位操作） |
| `net_device` 链表的**写**侧 | 链表的**读**侧（`dev_get_by_name` 用 RCU） |
| 地址表的增删（配合 `addr_list_lock`） | `netdev_queue->state`（靠原子位操作） |
| 通知链的发布 | per-CPU 统计（靠 `u64_stats_sync`） |

⚠️ **发送路径完全不碰 rtnl**。§A6 那个 `rcu_read_lock_bh()` 是它的全部同步。所以配置变更和数据面并发时，靠的是"配置侧改完发 RCU 宽限期，数据面看到的要么是旧值要么是新值"这个约定，而不是互斥。

### §B1.3 锁层级

```
   rtnl_mutex（睡眠锁，配置面）
       │
       ├── dev_base_lock（rwlock，链表写侧）      net/core/dev.c:189
       ├── dev->addr_list_lock（spinlock）        register 时 init，:10131
       ├── xps_map_mutex                          net/core/dev.c:2422
       ├── ifalias_mutex                          net/core/dev.c:192
       └── napi_hash_lock                         net/core/dev.c:195

   （数据面，与上面无交集）
   rcu_read_lock_bh
       ├── qdisc 的 root lock            net/sched/
       ├── HARD_TX_LOCK → txq->_xmit_lock
       └── lweventlist_lock（linkwatch）  net/core/link_watch.c:34
```

**`lweventlist_lock` 是唯一一个两侧都会拿的**——`linkwatch_fire_event()` 可能从驱动的中断上下文调用（所以是 `spin_lock_irqsave`，`net/core/link_watch.c:110`），而 `__linkwatch_run_queue()` 在工作队列里拿它。这是 §B6 那套延迟机制的存在理由之一。

### §B1.4 rtnl 的代价

`netdev_run_todo()` 由 `rtnl_unlock()` 调用（§A11），而它里面有 `rcu_barrier()`（`:10500`）和可能睡很久的 `netdev_wait_allrefs_any()`（`:10516`）。**结论：`rtnl_unlock()` 可能阻塞很久**，而这段时间里所有其他 `ip link` 操作都在等。大量虚拟设备（容器场景）批量删除时 `ip` 命令卡住就是这个机理。

## §B2 NAPI 的 SCHED / MISSED 不变量

这是本子系统最值得逐位理解的一处并发设计。状态位定义在 `include/linux/netdevice.h:373-384`，掩码在 `:386-397`。

### §B2.1 三条不变量

1. **同一时刻只有一个 CPU 能跑一个 napi 的 `poll()`**。执行权就是 `NAPI_STATE_SCHED` 位——谁把它从 0 置成 1，谁就获得执行权。
2. **`SCHED` 置位期间来的调度请求不能丢，也不能重复入队**。它被记在 `NAPI_STATE_MISSED` 里。
3. **只有把 `SCHED` 清成 0 的那一方才可以开硬中断**。

### §B2.2 三个函数怎么合作

```
   驱动中断处理函数
       │
       └─ napi_schedule()
              └─ napi_schedule_prep()          net/core/dev.c:6070-6091
                     │   SCHED 已置 → 置 MISSED，返回 false
                     │   SCHED 未置 → 置 SCHED，返回 true
                     ↓ true
                 __napi_schedule()             net/core/dev.c:6051-6058
                     └─ 挂 sd->poll_list + raise NET_RX_SOFTIRQ

   软中断
       │
       └─ net_rx_action()                       §A8
              └─ napi_poll() → driver poll()
                     └─ 没跑满预算 → napi_complete_done()   §A8.4
                            │  MISSED 置了 → 保留 SCHED，重新 __napi_schedule，返回 false
                            └─ MISSED 没置 → 清 SCHED，返回 true（驱动可以开中断）
```

### §B2.3 为什么必须这样

假设没有 MISSED 位，只有 SCHED：

- CPU0 在跑 `poll()`，快结束时读了一遍描述符环发现是空的。
- CPU1 上来了个中断，发现 SCHED 已置，于是什么也不做就返回。
- CPU0 清掉 SCHED，开中断，返回。
- **那个新到的包永远不会被处理，直到下一个包触发中断。**

MISSED 位把"我来过但没抢到"这件事记下来，`napi_complete_done()` 在清 SCHED 之前检查它（`net/core/dev.c:6173-6176`），发现有就重新调度自己。**关键是这个检查和清 SCHED 是同一个 cmpxchg 里完成的**（`:6156-6171`），所以中间没有窗口。

⚠️ 这也意味着 **`napi_complete_done()` 返回 false 时驱动绝对不能开硬中断**。返回 false 有两种原因（MISSED 或 defer），驱动无法区分也不需要区分——**唯一正确的写法是 `if (napi_complete_done(napi, work)) enable_irq()`**。

### §B2.4 `napi_disable()` 的等待（`net/core/dev.c:6485-6509`）

关闭设备时必须确保没有 `poll()` 在跑。做法是先置 `NAPI_STATE_DISABLE`（`:6490`），然后在一个循环里等到 SCHED 和 NPSVC 都没被别人占（`:6494-6497`）：

```c
		val = READ_ONCE(n->state);
		if (val & (NAPIF_STATE_SCHED | NAPIF_STATE_NPSVC)) {
			usleep_range(20, 200);
			continue;
		}

		new = val | NAPIF_STATE_SCHED | NAPIF_STATE_NPSVC;
		new &= ~(NAPIF_STATE_THREADED | NAPIF_STATE_PREFER_BUSY_POLL);
```

`:6493-6500`。**它是"把这两位抢过来自己占着"而不是清掉**——占着 SCHED 就等于永久持有执行权，别人再也调度不进来。等待用的是 `usleep_range(20, 200)`，函数开头有 `might_sleep()`（`:6489`），所以**不能在原子上下文调**。§A9.1 的注释要求驱动在 `ndo_stop` 里调它，`ndo_stop` 正好是可睡的上下文。

收尾（`:6506-6508`）取消 hrtimer 并清 `NAPI_STATE_DISABLE`——**SCHED 和 NPSVC 仍然被占着**，靠 `napi_enable()`（`:6519-6531`）来释放。`__netif_napi_del()`（`:6548-6564`）才真正从链表和哈希里摘掉。

### §B2.5 线程化 NAPI（`net/core/dev.c:6404-6445`）

`dev_set_threaded()` 把这套状态机搬进内核线程：置 `NAPI_STATE_THREADED` 后，`napi_schedule` 唤醒一个内核线程而不是 raise 软中断。开关在 `/sys/class/net/<dev>/threaded`。

顺序是先给每个 napi 建好线程（`:6412-6422` 的 `napi_kthread_create()`），再 `dev->threaded = threaded`（`:6424`），中间隔一个 `smp_mb__before_atomic()`（`:6429`），最后才逐个设/清 `NAPI_STATE_THREADED`（`:6437-6442`）。注释（`:6426-6428`）写明屏障的作用是"Make sure kthread is created before THREADED bit is set"。

⚠️ **它可以在设备 UP 的状态下切换**，函数里没有 `netif_running()` 检查。注释（`:6431-6435`）明确说：正在被 poll 的 napi 不会立刻生效，切换发生在下一轮 `napi_schedule()`，而且"This should not cause hiccups/stalls to the live traffic"。所以在线切换是设计意图，不是漏检查。

## §B3 队列停唤与 BQL

### §B3.1 两个 XOFF 位，两个主人

`netdev_queue->state` 的权威说明在 `include/linux/netdevice.h:594-602`，本篇只讲流程：

| 位 | 谁置 | 谁清 | 语义 |
|---|---|---|---|
| `__QUEUE_STATE_DRV_XOFF` | 驱动（`netif_tx_stop_queue()`，`include/linux/netdevice.h:3309`） | 驱动（`netif_tx_wake_queue()`，`net/core/dev.c:3168-3179`） | 硬件环满了 |
| `__QUEUE_STATE_STACK_XOFF` | BQL（`netdev_tx_sent_queue()`，`include/linux/netdevice.h:3439`） | BQL（`netdev_tx_completed_queue()`，`:3525`） | 在途字节数超限 |
| `__QUEUE_STATE_FROZEN` | 核心（冻结整个设备） | 核心 | 拆队列/改配置 |

`netif_xmit_stopped()` 检查前两位的**或**，§A6.5 和 §A7 都用它。

### §B3.2 唤醒是"清位 + 重新调度 qdisc"

```c
void netif_tx_wake_queue(struct netdev_queue *dev_queue)
{
	if (test_and_clear_bit(__QUEUE_STATE_DRV_XOFF, &dev_queue->state)) {
		struct Qdisc *q;

		rcu_read_lock();
		q = rcu_dereference(dev_queue->qdisc);
		__netif_schedule(q);
		rcu_read_unlock();
	}
}
```

`net/core/dev.c:3168-3179`。⚠️ **只有真的清掉了位才会重新调度**——`test_and_clear_bit` 返回 0 时函数什么也不做。所以驱动重复调 `netif_tx_wake_queue()` 是安全的，但**如果驱动用 `clear_bit()` 手动清位就不会触发重调度，队列会僵死**。

BQL 侧对应的是 `netif_schedule_queue()`（`net/core/dev.c:3156-3165`），它多一道 `netif_xmit_stopped()` 检查——因为清掉 STACK_XOFF 之后 DRV_XOFF 可能还在。

### §B3.3 BQL 的两个内存屏障

`netdev_tx_sent_queue()`（`include/linux/netdevice.h:3424-3452`）和 `netdev_tx_completed_queue()`（`:3506-3528`）之间有一个经典的双向屏障配对。发送侧：

```c
	set_bit(__QUEUE_STATE_STACK_XOFF, &dev_queue->state);

	/*
	 * The XOFF flag must be set before checking the dql_avail below,
	 * because in netdev_tx_completed_queue we update the dql_completed
	 * before checking the XOFF flag.
	 */
	smp_mb();

	/* check again in case another CPU has just made room avail */
	if (unlikely(dql_avail(&dev_queue->dql) >= 0))
		clear_bit(__QUEUE_STATE_STACK_XOFF, &dev_queue->state);
```

`:3439-3450`。完成侧的注释（`:3515-3519`）把后果写明了：`"Without the memory barrier there is a small possiblity that netdev_tx_sent_queue will miss the update and cause the queue to be stopped forever"`。

⚠️ **`netdev_tx_completed_queue()` 每轮完成只能调一次**，kerneldoc（`:3503-3504`）明确要求。驱动如果在完成循环里每个包调一次，BQL 的限额会被算错——它靠"两次调用之间的字节数"估算队列长度。

`netdev_tx_sent_queue()` 里还有一处顺带的活（`:3434`）：`WRITE_ONCE(dev_queue->trans_start, jiffies)`，配对的读在 `net/sched/sch_generic.c` 的 `dev_watchdog()`（`:495`）里。同一对写入 + 屏障也出现在 `netif_tx_stop_queue()`（`include/linux/netdevice.h:3312-3315`），而**那一处不在 `#ifdef CONFIG_BQL` 里面**：

```c
static __always_inline void netif_tx_stop_queue(struct netdev_queue *dev_queue)
{
	/* Paired with READ_ONCE() from dev_watchdog() */
	WRITE_ONCE(dev_queue->trans_start, jiffies);

	/* This barrier is paired with smp_mb() from dev_watchdog() */
	smp_mb__before_atomic();

	/* Must be an atomic op see netif_txq_try_stop() */
	set_bit(__QUEUE_STATE_DRV_XOFF, &dev_queue->state);
}
```

`include/linux/netdevice.h:3309-3319`。所以 **tx timeout 检测在两条路上都能拿到 `trans_start`**：驱动主动停队列时由 `netif_tx_stop_queue()` 记，BQL 停队列时由 `netdev_tx_sent_queue()` 记。关掉 `CONFIG_BQL` 只丢掉后者。

### §B3.4 队列数量的运行时调整

`netif_set_real_num_tx_queues()`（`net/core/dev.c:2937-2975`）和 `netif_set_real_num_rx_queues()`（`:2989-3007`）只能在 `num_*_queues`（§A1.2 分配的上限）范围内调。**两者的对称性是假的**：发送侧还要处理 XPS 映射和 tc 映射，接收侧只需要改 sysfs 目录数。

## §B4 features 重算的触发点

`__netdev_update_features()`（§A4）的调用点不多，全树可数：

| 位置 | 触发原因 |
|---|---|
| `net/core/dev.c:10226` | 注册（§A3.5） |
| `net/core/dev.c:9690` | 同步到 lower device |
| `net/ethtool/ioctl.c:156`、`:285`、`:340` | ethtool ioctl 改 offload / SG / TSO |
| `net/ethtool/features.c:260` | ethtool netlink 改 features |

包装函数 `netdev_update_features()` 的调用点分两类：

| 位置 | 触发原因 |
|---|---|
| `net/core/dev.c:1644` | `dev_disable_lro()`——先清 `wanted_features` 里的 `NETIF_F_LRO` 再重算 |
| `net/core/dev.c:1665` | `dev_disable_gro_hw()`——同上，清 `NETIF_F_GRO_HW` |
| `net/8021q/vlan.c:338`、`net/bridge/br.c:93`、`net/bridge/br_if.c:655`、`:751`、`net/bridge/br_device.c:181`、`net/hsr/hsr_slave.c:206`、`:226` | 上层设备成员变化 |

前两处是"关掉某个 offload"的标准写法：**改 `wanted_features` 然后重算，不直接改 `dev->features`**。两处都紧跟一条校验（`net/core/dev.c:1646-1647`、`:1667-1668`），关不掉就 `netdev_WARN`：`"failed to disable LRO!\n"` / `"failed to disable GRO_HW!\n"`。这两条字符串可以直接 `dmesg | grep`。`dev_disable_lro()` 还会递归到所有 lower device（`:1649-1650`）。

重跑命令：`grep -rn '__netdev_update_features\|netdev_update_features(' net/ | grep -v netdevice.h`。

⚠️ **驱动自己不应该直接调 `__netdev_update_features()`**——它要求持 rtnl 而且不发通知。驱动改了硬件能力之后该调 `netdev_update_features()`（会加锁并发 `NETDEV_FEAT_CHANGE`）。`netdev_change_features()`（`:9918-9922`）是第三个变体，用于"features 已经被别人改了，只需要发通知"的场合。

## §B5 统计的三层聚合

### §B5.1 `dev_get_stats()`（`net/core/dev.c:10586-10616`）

用户态每次读 `/proc/net/dev` 或 `ip -s link` 都走这里。它做两件事：

**第一件：三选一拿驱动的数（`:10592-10599`）。**

```c
	if (ops->ndo_get_stats64) {
		memset(storage, 0, sizeof(*storage));
		ops->ndo_get_stats64(dev, storage);
	} else if (ops->ndo_get_stats) {
		netdev_stats_to_stats64(storage, ops->ndo_get_stats(dev));
	} else {
		netdev_stats_to_stats64(storage, &dev->stats);
	}
```

⚠️ **只有 `ndo_get_stats64` 那一路会先 `memset`**。另两路靠 `netdev_stats_to_stats64()` 全量覆盖。驱动实现 `ndo_get_stats64` 时**可以只填自己关心的字段**，因为核心已经清零；但如果驱动误以为传进来的是累加基准就会算错。

**第二件：叠加核心自己的丢包计数（`:10601-10614`）。** 遍历 per-CPU 的 `core_stats`，把 `rx_dropped`、`tx_dropped`、`rx_nohandler`、`rx_otherhost_dropped` 四项加上去。

所以 **`/proc/net/dev` 里的 `drop` 列是"驱动报的 + 核心报的"之和**，驱动自己的寄存器里查不到全部。这是排查丢包时第一个要分清的口径，详见 `07-debug.md`。

注释（`:10601`）"This READ_ONCE() pairs with the write in netdev_core_stats_alloc()"说明 `core_stats` 是**懒分配**的——第一次有人要加计数时才分配（`02-datastructures.md` §6.2）。

### §B5.2 `dev_fetch_sw_netstats()`（`net/core/dev.c:10626-10650`）

给用 `pcpu_sw_netstats` 的驱动准备的辅助函数：遍历 per-CPU，用 `u64_stats_sync` 的 seqcount 循环读四项，累加。

```c
		do {
			start = u64_stats_fetch_begin(&stats->syncp);
			rx_packets = u64_stats_read(&stats->rx_packets);
			...
		} while (u64_stats_fetch_retry(&stats->syncp, start));
```

`:10637-10643`。⚠️ **这里用的是 `u64_stats_fetch_begin()` 而不是 `_irq` 变体**——这正是本树补丁改过的地方，见 §B7。

### §B5.3 三层的关系

```
   用户态                     dev_get_stats()
      │                             │
      │      ┌──────────────────────┴──────────────────────┐
      │      ↓                                             ↓
      │  驱动的数                                      核心的数
      │  ndo_get_stats64 / ndo_get_stats / dev->stats   dev->core_stats
      │      │                                          （per-CPU，懒分配）
      │      ↓
      │  驱动内部可能用：
      │    pcpu_sw_netstats  → dev_fetch_sw_netstats()
      │    pcpu_dstats / pcpu_lstats
      │    或自己的 per-queue 结构
      ↓
   rtnl_link_stats64（唯一的上报格式）
```

`ethtool -S` 走的是完全另一条路（`ethtool_ops->get_ethtool_stats`），**跟这三层没有任何交集**，两边的数对不上是正常的。

## §B6 linkwatch：为什么链路状态要延迟上报

`net/core/link_watch.c` 只有 280 行，但它解决的是一个很实际的问题：**PHY 抖动时不能让每次抖动都变成一次完整的状态变更处理**。

### §B6.1 三级门

驱动调 `netif_carrier_on/off()` 最终到 `linkwatch_fire_event()`（`net/core/link_watch.c:269-279`），然后：

```
   linkwatch_fire_event(dev)
       │
       ├─ linkwatch_urgent_event(dev)        :91-103   判断是否紧急
       ├─ linkwatch_add_event(dev)           :106-116  挂 lweventlist（持 netdev 引用）
       └─ linkwatch_schedule_work(urgent)    :119-145  决定什么时候干活
```

紧急的判据（`:93-102`）有四条：设备没 running → 不紧急直接 false；`ifindex != dev_get_iflink()`（隧道类）→ 紧急；LAG 成员或 master → 紧急；否则 `netif_carrier_ok(dev) && qdisc_tx_changing(dev)`。

### §B6.2 延迟的算法

```c
	unsigned long delay = linkwatch_nextevent - jiffies;

	if (test_bit(LW_URGENT, &linkwatch_flags))
		return;

	/* Minimise down-time: drop delay for up event. */
	if (urgent) {
		if (test_and_set_bit(LW_URGENT, &linkwatch_flags))
			return;
		delay = 0;
	}

	/* If we wrap around we'll delay it by at most HZ. */
	if (delay > HZ)
		delay = 0;
```

`net/core/link_watch.c:121-135`。三个要点：

- **已经有紧急任务在排队时直接返回**（`:123-124`），不重复排。
- **紧急事件把延迟压成 0**（`:130`），注释说理由是"Minimise down-time"。
- `delay > HZ` 判为回绕，也压成 0（`:134-135`）。

最后（`:141-144`）紧急用 `mod_delayed_work(..., 0)` 抢占已有的定时器，非紧急用 `queue_delayed_work()` **不覆盖**已有的定时器。工作队列是 `system_unbound_wq`。

### §B6.3 干活的那一步

`linkwatch_do_dev()`（`:148-174`）是每个设备实际被处理的地方：

| 行 | 动作 |
|---|---|
| :154 | `smp_mb__before_atomic()`——注释（`:150-153`）说明是保证前面的读完成 |
| :159 | `clear_bit(__LINK_STATE_LINKWATCH_PENDING, &dev->state)`——**先清位再处理**，这样处理期间的新事件不会丢 |
| :161 | `rfc2863_policy(dev)`——按 RFC 2863 算 `operstate` |
| :163-166 | carrier 有 → `dev_activate(dev)`；没有 → `dev_deactivate(dev)` |
| :168 | `netdev_state_change(dev)`（`net/core/dev.c:1383-1394`）——发 `NETDEV_CHANGE` 通知和 `RTM_NEWLINK` |
| :173 | `__dev_put(dev)` |

注释（`:170-172`）解释为什么用 `__dev_put()` 而不是 `dev_put()`："our callers are responsible for calling netdev_tracker_free()"。

⚠️ **`dev_activate()` / `dev_deactivate()` 在这里被调用**，也就是说**链路 down 会导致 qdisc 被停掉**，不只是 `operstate` 变个字符串。这是"网线拔掉之后队列里的包去哪了"的答案。

## §B7 本树的 PREEMPT_RT 补丁：两条 defer 路径

基线 `f6e38ae624cf`（Linux 6.1.158）**对本子系统打了一批 PREEMPT_RT 回移补丁**。`git status --porcelain -- drivers/net/ethernet/ net/core/dev.c include/linux/netdevice.h include/linux/u64_stats_sync.h` 有输出，全套结论的适用范围以此为准。补丁的完整清单和判定过程在 `99-evidence.md` §1，本节只讲它改变了什么流程。

### §B7.1 `u64_stats_fetch_begin_irq()` 被删了

`include/linux/u64_stats_sync.h` 里那个 `/* Obsolete interfaces */` 块（定义 `u64_stats_fetch_begin_irq()` 和 `u64_stats_fetch_retry_irq()`）**整块删除**。连带 `drivers/net/ethernet/` 下 47 个文件做了纯机械替换，改到非 `_irq` 的名字。

⚠️ **在这棵树上写 `u64_stats_fetch_begin_irq()` 编译不过**。参考上游 6.1 的驱动代码或者网上的老示例时会撞上。§B5.2 引用的 `dev_fetch_sw_netstats()` 就是被改过的那个函数。

### §B7.2 `trigger_rx_softirq()` 分成两条路

`net/core/dev.c` 的 `trigger_rx_softirq()` 被 `#ifndef CONFIG_PREEMPT_RT` 切成两个实现：

| 配置 | 触发方式 | 干什么 |
|---|---|---|
| 非 RT | IPI（`call_single_data_t defer_csd`） | `__raise_softirq_irqoff(NET_RX_SOFTIRQ)` 然后 `smp_store_release(&sd->defer_ipi_scheduled, 0)` |
| RT | 工作队列（`struct work_struct defer_work`） | `smp_store_release` 之后 `local_bh_disable(); skb_defer_free_flush(sd); local_bh_enable();` |

选择在 `net_dev_init()` 里做：非 RT 是 `INIT_CSD(&sd->defer_csd, trigger_rx_softirq, sd)`，RT 是 `INIT_WORK(&sd->defer_work, trigger_rx_softirq)`。

`struct softnet_data` 因此多了一个二选一的成员（`include/linux/netdevice.h:3202-3206`）：

```c
#ifndef CONFIG_PREEMPT_RT
	call_single_data_t	defer_csd;
#else
	struct work_struct	defer_work;
#endif
```

### §B7.3 对 §A8 的影响

`net_rx_action()` 主循环里那个 `skb_defer_free_flush(sd)`（`net/core/dev.c:6777`）是"跨 CPU 延迟释放 skb"机制的消费端。**非 RT 下它由 IPI 唤起的软中断执行；RT 下由工作队列线程直接执行**，绕开了软中断。

⚠️ 后果是 **RT 内核上 `skb_defer_free_flush()` 的时机不再跟 `NET_RX_SOFTIRQ` 绑定**。如果读者在 RT 配置下用 tracepoint 观察 skb 释放时机，看到的顺序会和非 RT 不同——这不是 bug。

除此之外，**本子系统的核心流程（Part A 的 A1–A12）在这棵树上与上游 6.1.158 完全一致**，没有被补丁改动。

---

# Part C：评价

## §C1 源码自陈的设计债

`net_device` 自己的注释是最诚实的一份评价。`include/linux/netdevice.h:1757-1762`：

> Actually, this whole structure is a big mistake. It mixes I/O data with strictly "high-level" data, and it has to know about almost every data structure used in the INET module.

紧接着 `:2025-2026` 是一条具体的待办：

```c
 *	FIXME: cleanup struct net_device such that network protocol info
 *	moves out.
```

以及 `:2035` 的另一条：

```c
	 *	FIXME: Merge these and struct ifmap into one
```

### §C1.1 十处"没人写注释"

`include/linux/netdevice.h` 的 kerneldoc 块里有 **10 处** `XXX: need comments on this one`：

| 行 | 字段 |
|---|---|
| :1860 | `addr_list_lock` |
| :1901 | `rx_handler_data` |
| :1904 | `ingress_queue` |
| :1919 | `tx_global_lock` |
| :1923 | `xps_maps` |
| :1937 | `link_watch_list` |
| :1946 | `npinfo` |
| :1980 | `tc_to_txq` |
| :1981 | `prio_tc_map` |
| :1985 | `priomap` |

重跑：`grep -c 'XXX: need comments on this one' include/linux/netdevice.h` → `include/linux/netdevice.h` → 10。

这份清单有实际用处：**这 10 个字段是读代码时最没有文档支撑的部分**，要靠 grep 使用者反推语义。其中 `tx_global_lock`、`xps_maps`、`ingress_queue` 三个恰好是并发相关的，风险最高。

### §C1.2 另外两处

| 位置 | 内容 |
|---|---|
| `net/core/dev.c:3503` | `/* XXX: check that highmem exists at all on the given machine. */`——它是 `illegal_highdma()`（`:3504`）的头注释，函数体整个包在 `#ifdef CONFIG_HIGHMEM`（`:3506`）里。arm64 上没有 `CONFIG_HIGHMEM`，所以这条债在本架构上不生效 |
| `include/linux/netdevice.h:5156` | `/* TODO: reserve and use an additional IFF bit, if we get more users */`——`priv_flags` 的位快用完了 |

重跑：`grep -nE '(FIXME|TODO|XXX|REVISIT|HACK)' net/core/dev.c include/linux/netdevice.h net/ethernet/eth.c net/core/link_watch.c net/core/gro.c`。⚠️ 命中里还有 `NESTED_SYNC_TODO`（`include/linux/netdevice.h:4702`、`:4709` 和 `net/core/dev.c` 里五处使用），那是一个**正常的标志名**而不是待办注释，统计时要排掉。

## §C2 做得好的地方

**一、SCHED/MISSED 那个 cmpxchg（§B2）。** 它用两个位、一次 cmpxchg 解决了"多 CPU 抢一个 poll 执行权且不能丢事件"这个问题，没有引入任何锁。而且注释把无分支写法的来源（Alexander Duyck 的建议）都记了下来，`net/core/dev.c:6167-6168`。

**二、`register_netdevice()` 的错误标签是严格倒序的（§A3.6）。** 三个标签、三段回滚，跟正向的三步申请一一对应。170 行的函数能做到这一点不容易。

**三、BQL 那对内存屏障（§B3.3）。** 两处注释各自解释了"为什么必须在这个位置"，而且互相指向对方。这是内核里屏障注释的范本。

**四、`unregister` 的三段式（§A10 / §A11）。** 把"从表里摘掉"、"通知别人"、"等引用归零"分成三段，中间用 `synchronize_net()` 和 `rcu_barrier()` 隔开。代价是 `netdev_run_todo()` 挂在 `rtnl_unlock()` 上这个奇怪的耦合，但换来的是注销路径可以安全地睡。

## §C3 改不动的地方

**一、`net_device` 的字段布局。** 167 个字段、5242 行的头文件，`:1757-1762` 自己承认是个错误，但它被上千个驱动和几十个协议模块直接访问，拆不动。**只能靠 `/* Cache lines mostly used on transmit path */` 这类注释分组来缓解 cache 问题。**

**二、rtnl 是一把巨锁（§B1）。** 每一个配置动作都要它，而 `rtnl_unlock()` 又可能睡很久（§B1.4）。这在容器密集场景是实测的瓶颈，但拆锁需要重新审计所有 `ASSERT_RTNL()` 的调用点。

**三、`netif_rx()` 和 NAPI 两条并列的接收入口（§A8.3）。** `process_backlog()` 是一个"伪 NAPI"，为了兼容不用 NAPI 的老驱动而存在。它自己手写了一个 `__napi_complete()`（`net/core/dev.c:6024-6032`），是全内核少见的重复实现。

**四、统计的三层叠加（§B5.3）。** `/proc/net/dev`、`ethtool -S`、per-queue 计数器三套口径互不相通，而且 `dev_get_stats()` 的三选一分支里只有一路会 memset（§B5.1）。这个不一致对驱动作者是长期的陷阱来源。

---

## §D 读下一篇

| 你接着想知道 | 去 |
|---|---|
| GRO / XDP / page_pool / ethtool netlink 的内部 | `04-extensions.md` |
| 自己写一个驱动的骨架 | `05-usage.md` |
| DTS 里的 phy-mode / fixed-link 谁消费 | `06-devicetree.md` |
| `time_squeeze` 涨了怎么办、丢包看哪个数 | `07-debug.md` |
| 为什么 SCHED/MISSED 要两个位（大白话） | `08-feynman.md` |
| 本篇每条论断的出处 | `99-evidence.md` |
| 字段和状态位的完整定义 | `02-datastructures.md` |
| 函数签名和错误码 | `01-api.md` |
