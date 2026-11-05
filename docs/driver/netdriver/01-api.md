---
title: API 参考
---

# 网卡驱动：代码地图与对外 API

本篇回答两类查询：**"这件事的代码在哪个文件"**（§0）和**"这个 API 怎么用、返回什么"**（§1 起）。

行号只在 `03-internals.md` 和 `99-evidence.md` 里维护——本篇的位置一律只精确到文件，签名只写函数名加形参名。

---

## §0 代码地图

### §0.1 本套文档的范围

"网卡驱动"这个题目跨了三块代码，边界必须先划清：

| 块 | 路径 | 本套怎么处理 |
|---|---|---|
| 驱动契约的定义方 | `include/linux/netdevice.h`、`include/linux/etherdevice.h`、`include/linux/ethtool.h` | 逐字段读，是 `02-datastructures.md` 的主体 |
| 契约的执行方 | `net/core/dev.c`、`net/ethernet/eth.c`、`net/core/link_watch.c`、`net/core/dev_addr_lists.c`、`net/ethtool/` | 核心路径逐行读，是 `03-internals.md` 的主体 |
| 契约的实现方 | `drivers/net/ethernet/`（93 个厂商目录，2542 个 `.c`/`.h`，2319156 行） | 只讲契约，加四个代表驱动（§0.4） |

不在范围内：协议栈本身（`net/ipv4/`、`net/ipv6/`、`net/sched/`）、无线（`drivers/net/wireless/`、`net/mac80211/`）、虚拟网卡（`drivers/net/veth.c`、`tun.c`、`bonding/`）、交换机 offload 的完整语义（`net/switchdev/`、`net/dsa/` 只在 `04-extensions.md` 点到）。

### §0.2 核心文件

| 文件 | 行数 | 职责 |
|---|---|---|
| `net/core/dev.c` | 11617 | 全部核心：注册、注销、open/close、发送、接收、NAPI、features 重算、统计聚合 |
| `net/core/rtnetlink.c` | 6276 | netlink 侧的 link 管理，`rtnl_lock` 的主要持有者 |
| `net/core/skbuff.c` | 6706 | `sk_buff` 的分配、克隆、线性化、分片 |
| `net/core/net-sysfs.c` | 2077 | `/sys/class/net/*` 的全部属性，含 `statistics/`、`queues/` |
| `net/core/dev_addr_lists.c` | 1050 | 单播/组播地址表（`dev_uc_*` / `dev_mc_*`） |
| `net/core/page_pool.c` | 938 | 收包页缓存 |
| `net/core/gro.c` | 817 | 收包合并 |
| `net/core/xdp.c` | 707 | XDP 内存模型（`xdp_rxq_info`、`xdp_frame`） |
| `net/core/dev_ioctl.c` | 619 | `SIOCGIFxxx` 系列 ioctl 转发 |
| `net/core/net-procfs.c` | 407 | `/proc/net/dev` |
| `net/core/link_watch.c` | 280 | 载波变化的延迟上报队列 |
| `net/ethernet/eth.c` | 640 | 以太网层：`ether_setup()`、`eth_type_trans()`、MAC 地址获取 |
| `net/ethtool/` | 11354（26 个 `.c`） | ethtool 的 ioctl 与 netlink 两套前端 |

`net/core/dev.c` 的 11617 行不是均匀的一块。按 `03-internals.md` §0 的分区，它实际是六段拼起来的：设备查找与命名、发送路径、接收路径与 NAPI、features 与地址表、注册/注销状态机、初始化。**读它的时候按段读，不要从头读**。

### §0.3 头文件

| 头文件 | 行数 | 内容 |
|---|---|---|
| `include/linux/netdevice.h` | 5242 | `struct net_device`、`net_device_ops`、`napi_struct`、`netdev_queue`、几乎全部 `netif_*` 内联辅助 |
| `include/linux/skbuff.h` | 5141 | `struct sk_buff` 与它的全部访问器 |
| `include/linux/ethtool.h` | 846 | `struct ethtool_ops` 与内核侧的 ethtool 类型 |
| `include/linux/etherdevice.h` | 633 | `alloc_etherdev*`、MAC 地址判定与设置 |
| `include/net/page_pool.h` | 386 | `page_pool` 的 API 与 `page_pool_params` |
| `include/linux/netdev_features.h` | 264 | 63 个 feature 位与它们的组合宏 |
| `include/linux/u64_stats_sync.h` | 217 | 64 位统计计数器的读写协议——**本树改过这个文件**，见 §0.6 |
| `include/net/netdev_queues.h` | 144 | 无锁队列停/唤的四个宏 |
| `include/uapi/linux/if_link.h` | 1390 | `rtnl_link_stats64` 等用户态可见的 link ABI |
| `include/uapi/linux/ethtool.h` | 2156 | ethtool 的 ioctl ABI |

`include/uapi/linux/` 下以 `if`、`ethtool`、`net` 开头的头文件共 48 个（口径：`ls include/uapi/linux/ | grep -E '^(if|ethtool|net)' | grep -c '\.h$'`；5 个 `netfilter*` 是目录不是头文件，不计入）。这个数量本身是一条结论：**网络子系统的用户态 ABI 面积远大于任何一条硬件总线**，所以 `06-devicetree.md` 的分诊表里 DT 只占很小一格。

### §0.4 驱动侧与代表驱动

`drivers/net/ethernet/` 下 93 个厂商目录，规模最大的五个：

| 目录 | 行数 |
|---|---|
| `drivers/net/ethernet/intel/` | 353971 |
| `drivers/net/ethernet/mellanox/` | 271162 |
| `drivers/net/ethernet/broadcom/` | 198648 |
| `drivers/net/ethernet/qlogic/` | 147719 |
| `drivers/net/ethernet/sfc/` | 139789 |

`drivers/net/ethernet/Kconfig` 只有 196 行、10 个 `config`——它基本上只是一串 `source`，真正的开关都在各厂商目录自己的 Kconfig 里。

契约的实例数（可重跑口径见 `99-evidence.md` §3）：`drivers/net/ethernet/` 下有 **284 个 `net_device_ops` 实例**、**237 个 `ethtool_ops` 实例**。一个厂商目录里通常两者成对出现，数量差说明有些驱动共用 ethtool 实现或者干脆不提供。

本套选四个代表驱动，选法：都在 `arch/arm64/boot/dts/` 里有真实节点，都在本树被打过补丁（§0.6），规模跨度大：

| 代表驱动 | 行数 | 选它的理由 |
|---|---|---|
| `drivers/net/ethernet/ti/am65-cpsw-nuss.c` | 2893 | 最小的完整实现，`ti,j721e-cpsw-nuss` 在 arm64 DTS 里有 3 处 |
| `drivers/net/ethernet/mediatek/mtk_eth_soc.c` | 4397 | `mediatek,eth-mac` 5 处，带 XDP 与 page_pool |
| `drivers/net/ethernet/marvell/mvneta.c` | 5872 | 单队列到多队列都覆盖，page_pool 与 XDP 的常用参照 |
| `drivers/net/ethernet/marvell/mvpp2/mvpp2_main.c` | 7757 | 全功能：多端口、硬件分类、per-CPU 队列 |

其余驱动一律只讲契约，不逐个读。另外两个非 arm64 但在 `04-extensions.md` 里会点到的：`drivers/net/ethernet/intel/ixgbe/ixgbe_main.c`（11667 行，offload 最全）和 `drivers/net/ethernet/stmicro/stmmac/stmmac_main.c`（7682 行，DT 属性最多）。

### §0.5 导出符号

按 `^EXPORT_SYMBOL` 行数计（口径注意见 `99-evidence.md` §3）：

| 文件 | 导出行数 |
|---|---|
| `net/core/dev.c` | 183 |
| `net/core/dev_addr_lists.c` | 28 |
| `net/ethernet/eth.c` | 21 |
| `net/core/page_pool.c` | 13 |
| `net/core/gro.c` | 11 |
| `net/core/link_watch.c` | 1 |

但驱动实际用到的对外接口远不止这些——`include/linux/netdevice.h` 里大量能力是 `static inline`，不需要导出。所以"驱动能用什么"的判据在这里必须是**头文件里有声明**，导出表只覆盖其中一部分。

### §0.6 本树特有的改动

⚠️ 这是本套文档里最需要先读的一节。基线 `f6e38ae624cf`（Linux 6.1.158）在本子系统范围内**被打过补丁**，`git status --porcelain` 的输出：

| 范围 | 改动文件数 |
|---|---|
| `drivers/net/ethernet/` | 47 |
| `net/core/` | 4（其中 `net/core/dev.c` 属本套范围） |
| `include/linux/netdevice.h` | 1 |
| `include/linux/u64_stats_sync.h` | 1 |

改动分成**两组，都指向 PREEMPT_RT**：

**第一组：统计读侧 API 的 `_irq` 变体被删掉。** `include/linux/u64_stats_sync.h` 删了 12 行，`u64_stats_fetch_begin_irq()` 与 `u64_stats_fetch_retry_irq()` 两个包装函数（原注释就写着 "Obsolete interfaces"）整个消失。47 个驱动文件的全部改动就是把调用点换成不带 `_irq` 的版本——**204 增 204 删，纯替换，一行语义都没变**。`net/core/dev.c` 里 `dev_fetch_sw_netstats()` 也一样改了两处。

对读者的直接后果：**在本树上写 `u64_stats_fetch_begin_irq()` 会编译失败**。全树残留计数为 0（`grep -rn 'u64_stats_fetch_begin_irq\|u64_stats_fetch_retry_irq' --include=*.c --include=*.h .` 无输出）。逐驱动清单和判定见 `99-evidence.md` §4.1。

**第二组：`skb` 延迟释放的跨 CPU 通知在 PREEMPT_RT 下改用工作队列。** `include/linux/netdevice.h` 给 `struct softnet_data` 加了条件字段：非 RT 下是 `call_single_data_t defer_csd`，RT 下换成 `struct work_struct defer_work`。`net/core/dev.c` 里 `trigger_rx_softirq()` 因此有了两份实现：非 RT 那份从硬中断（IPI）上下文抬 `NET_RX_SOFTIRQ`；RT 那份跑在工作队列里，自己 `local_bh_disable()` 后直接调 `skb_defer_free_flush()`。初始化点也分了岔——`INIT_CSD()` 与 `INIT_WORK()` 二选一。

**这一组改动会改变时序**，不像第一组只是改名。展开在 `03-internals.md` §B7；对驱动的影响在 `05-usage.md` §1.7。

### §0.7 找 API 用哪一节

| 你要做的事 | 去 |
|---|---|
| 分配一个 netdev | §1 |
| 注册/注销，管生命周期 | §2 |
| 决定实现哪些 `ndo_*` | §3 |
| 写发送函数 | §4 |
| 队列满了怎么停、什么时候唤 | §5 |
| 写 NAPI poll 与收包 | §6 |
| 上报链路状态 | §7 |
| 报统计数字 | §8 |
| 声明 offload 能力 | §9 |
| 实现 `ethtool -x` 之类的命令 | §10 |
| 拿 MAC 地址 | §11 |
| 支持 XDP、用 page_pool | §12 |
| 返回什么错误码 | §13 |

---

## §1 分配与释放

头文件：`include/linux/netdevice.h`、`include/linux/etherdevice.h`；实现：`net/core/dev.c`、`net/ethernet/eth.c`。

| API | 说明 | 备注 |
|---|---|---|
| `alloc_netdev_mqs(sizeof_priv, name, name_assign_type, setup, txqs, rxqs)` | 唯一的真正分配入口，其余都是它的包装 | 失败返回 `NULL`，不是错误码指针。`txqs`/`rxqs` 为 0 时 `WARN` 并返回 `NULL` |
| `alloc_netdev_mq(sizeof_priv, name, name_assign_type, setup, count)` | 收发队列数相同 | 宏，`include/linux/netdevice.h` |
| `alloc_netdev(sizeof_priv, name, name_assign_type, setup)` | 单队列 | 宏 |
| `alloc_etherdev_mqs(sizeof_priv, txqs, rxqs)` | 以太网版本，`setup` 固定为 `ether_setup` | 实现在 `net/ethernet/eth.c` |
| `alloc_etherdev_mq(sizeof_priv, count)` | 同上，收发队列数相同 | 宏，`include/linux/etherdevice.h` |
| `alloc_etherdev(sizeof_priv)` | 同上，单队列 | 宏，展开成 `alloc_etherdev_mq(sizeof_priv, 1)` |
| `devm_alloc_etherdev_mqs(dev, sizeof_priv, txqs, rxqs)` | 挂在 `struct device` 上的 devres 版本 | 实现在 `net/devres.c`（不在 `eth.c`）。⚠️ `remove()` 里必须先 `unregister_netdev()`，见下面的陷阱 |
| `devm_register_netdev(dev, ndev)` | 把 unregister 也交给 devres | 要求 `ndev` 本身也是 devres 管理的，否则 `WARN_ON` + `-EINVAL`。实现在 `net/devres.c` |
| `netdev_priv(dev)` | 拿驱动私有区的指针 | `static inline`，就是 `dev` 加一个对齐后的偏移。**不要保存这个指针再去猜 `dev`** |
| `free_netdev(dev)` | 释放 | 只在两种情形下由驱动调用，见 §2 的表 |
| `init_dummy_netdev(dev)` | 造一个不注册的壳，只给 NAPI 用 | 少数驱动用它做"没有 netdev 的 NAPI 宿主" |

`sizeof_priv` 的内存**不是单独分配的**：`alloc_netdev_mqs()` 一次性分配 `struct net_device` 加对齐填充加私有区，`netdev_priv()` 只是加偏移。所以私有区跟 netdev 同生共死，不需要单独 `kfree`。但如果私有区里又指向了别的堆内存，那部分要驱动自己收——`Documentation/networking/netdevices.rst`（第 21-24 行）明确写了这一点。

⚠️ **`devm_alloc_etherdev_mqs()` 的风险点不在"能不能配 `register_netdev()`"，而在释放次序**。devres 在驱动解绑时才释放，届时它调的是 `free_netdev()`（`net/devres.c:14-19`），而 `free_netdev()` 里有一条 `BUG_ON(dev->reg_state != NETREG_UNREGISTERED)`（`net/core/dev.c:10889`）。所以判据是：**`remove()` 里必须先 `unregister_netdev()`**，漏了就是 `BUG_ON`，而不是"别用 devm"。

本树 `drivers/net/ethernet/` 下 34 个文件用 `devm_alloc_etherdev*`，其中 25 个就是配着裸 `register_netdev()` 用的（如 `drivers/net/ethernet/lantiq_xrx200.c`、`drivers/net/ethernet/realtek/r8169_main.c`），另有 6 个用 `devm_register_netdev()` 把 unregister 也交给 devres——后者的 kerneldoc（`net/devres.c:57-66`）明确要求 netdev 本身也是 devres 管理的，不是的话直接 `WARN_ON` 返回 `-EINVAL`（`:76-78`）。所以"devm 版本和 `register_netdev()` 不能配"是错的。

`Documentation/networking/netdevices.rst`（第 15-19 行）用 `rmmod mydriver </sys/class/net/myeth/mtu` 举的那个病态场景，说的是**另一件事**：netdev 的最后一次引用可能晚于模块卸载，所以真正的释放要走 `put_device()`（`net/core/dev.c:10893`）而不是立刻 `kfree`——这条由 `free_netdev()` 自己保证，与用不用 devm 无关。展开在 `05-usage.md` §1.8。

### §1.1 `setup` 回调该干什么

`alloc_netdev_mqs()` 的第四个参数是个 `void (*setup)(struct net_device *)`，在 netdev 内存清零之后、返回给调用者之前调用。以太网驱动用的 `ether_setup()`（`net/ethernet/eth.c`）设置这些：

| 字段 | 值 | 含义 |
|---|---|---|
| `header_ops` | `eth_header_ops` | 以太网头的构造与解析 |
| `type` | `ARPHRD_ETHER` | 用户态看到的 link type |
| `hard_header_len` | `ETH_HLEN` | 14 |
| `min_header_len` | `ETH_HLEN` | 收包时的最小头长 |
| `mtu` | `ETH_DATA_LEN` | 1500 |
| `min_mtu` / `max_mtu` | `ETH_MIN_MTU` / `ETH_DATA_LEN` | 68 / 1500，**驱动支持 jumbo 时必须自己抬 `max_mtu`** |
| `addr_len` | `ETH_ALEN` | 6 |
| `tx_queue_len` | `DEFAULT_TX_QUEUE_LEN` | qdisc 的默认队列长度 |
| `flags` | `IFF_BROADCAST \| IFF_MULTICAST` | |
| `priv_flags` | `\|= IFF_TX_SKB_SHARING` | |
| `broadcast` | 全 `ff` | `eth_broadcast_addr()` |

自己再写一个 `setup` 时的常见做法是先调 `ether_setup(dev)` 再改几个字段。**只改 `max_mtu` 不改 `mtu` 是对的**——前者是能力上限，后者是当前值。

---

## §2 注册与注销

头文件：`include/linux/netdevice.h`；实现：`net/core/dev.c`。

`Documentation/networking/netdevices.rst`（第 26-31 行）把这里分成两组，**分组的依据是 `rtnl_lock` 有没有被调用者持着**：

| API | 说明 | 备注 |
|---|---|---|
| `register_netdev(dev)` | 自己取 `rtnl_lock` 再注册 | 普通驱动 probe 路径用这个 |
| `unregister_netdev(dev)` | 自己取 `rtnl_lock` 再注销 | 普通驱动 remove 路径用这个 |
| `register_netdevice(dev)` | 假定调用者已持 `rtnl_lock` | ⚠️ **失败时会调 `priv_destructor`**，见下 |
| `unregister_netdevice(dev)` | 同上 | 宏，展开成 `unregister_netdevice_queue(dev, NULL)` |
| `unregister_netdevice_queue(dev, head)` | 攒进 `head` 批量注销；`head` 为 `NULL` 时立即注销 | |
| `unregister_netdevice_many(head)` | 把攒起来的一批一次注销 | 多端口驱动省 RCU 等待时间的手段 |
| `free_netdev(dev)` | 释放 | 见下面的三行判据 |
| `netdev_run_todo()` | 把注销的收尾部分做完 | 核心内部调用，驱动不碰 |

**`free_netdev()` 什么时候该由驱动调**，只有三种情形，弄错就是 double free 或者泄漏：

| 情形 | 谁调 `free_netdev()` |
|---|---|
| `register_netdev()` 之前失败 | 驱动 |
| `register_netdev()` 本身返回错误 | 驱动 |
| 注册成功过，之后 `unregister_netdev()` | 驱动，紧跟在 `unregister_netdev()` 之后 |
| 注册成功且设置了 `needs_free_netdev` | 核心，驱动不要碰 |
| 由 `rtnl_link_ops` 生出来的设备（VLAN、bond 之类） | 核心，驱动**绝对**不要碰 |

⚠️ **`register_netdev()` 返回之后设备立刻对用户态可见**，`Documentation/networking/netdevices.rst`（第 74-77 行）的原话是 "Users can open it and start sending / receiving traffic immediately"。所以**所有初始化必须在 `register_netdev()` 之前做完**，包括 `netdev_ops`、`ethtool_ops`、队列数、MAC 地址、features。这条是本子系统里排第一的时序约束。

### §2.1 `needs_free_netdev` 与 `priv_destructor`

这两个字段是给"在已持 `rtnl_lock` 的上下文里建设备"准备的，普通硬件驱动一般用不到，但读别人代码时会撞上：

| 字段 | 语义 | 备注 |
|---|---|---|
| `needs_free_netdev` | 置 `true` 时核心在引用归零后自动 `free_netdev()` | **只有 `register_netdevice()` 成功之后才生效**；失败时仍要驱动自己释放 |
| `priv_destructor` | 注销后（或注册失败时）由核心调，用来拆私有状态 | 调用时可能持 `rtnl_lock` 也可能不持——**里面不能自己再取 `rtnl_lock`** |

`Documentation/networking/netdevices.rst`（第 152-164 行）给了完整的成对写法。它还点明一件反直觉的事：**没有构造回调**。驱动"构造"私有状态的时机就是 `alloc_netdev()` 返回之后、`register_netdevice()` 之前那段裸代码。

### §2.2 `ndo_init` / `ndo_uninit`

这两个是注册流程内部的钩子，`Documentation/networking/netdevices.rst`（第 174-183 行）：

- 两者都在 `rtnl_lock` 下调用。
- `ndo_init` 在设备对系统可见**之前**跑。features 的第一次重算就发生在它返回成功之后，所以 `hw_features` 要么在 `alloc` 之后设，要么在 `ndo_init` 里设，不能更晚。
- `ndo_uninit` 在注销过程中、设备已经 close 之后跑，但**别的子系统可能还持着引用**。所以这里不能释放会被别人读到的东西。

---

## §3 `net_device_ops` 契约

头文件：`include/linux/netdevice.h`。

`struct net_device_ops` 有 **86 个函数指针成员**。一个普通以太网驱动实际只填其中十来个。按"必须/常见/按需"分三档：

### §3.1 必须实现的

| 回调 | 说明 | 备注 |
|---|---|---|
| `ndo_open(dev)` | 上电、申请中断、建队列、启 NAPI，最后 `netif_start_queue()` | `rtnl_lock` 下，进程上下文，可以睡 |
| `ndo_stop(dev)` | `ndo_open` 的严格逆操作 | 同上。返回后**硬件必须完全静默**，见 `Documentation/networking/driver.rst` 的 Quiescence 一节 |
| `ndo_start_xmit(skb, dev)` | 发一个包 | 返回 `netdev_tx_t`，只能是 `NETDEV_TX_OK` 或 `NETDEV_TX_BUSY`。见 §4 |

`ndo_stop` 有一条容易漏的语义：`Documentation/networking/netdevices.rst`（第 219 行）说进入 `ndo_stop` 时 **`netif_running()` 保证已经是 false**。所以在 `ndo_stop` 里用 `netif_running()` 做判断是恒假。

`Documentation/networking/driver.rst` 的 Auto-close 一节还说明：**`unregister_netdevice()` 会在设备还 UP 时替你调 `ndo_stop`**，驱动 remove 路径不需要自己先 down。

### §3.2 以太网驱动几乎都会实现的

| 回调 | 说明 | 备注 |
|---|---|---|
| `ndo_get_stats64(dev, storage)` | 填 `rtnl_link_stats64` | **不能睡**（`/proc/net/dev` 会在原子上下文调它）。见 §8 |
| `ndo_set_mac_address(dev, addr)` | 改 MAC | 以太网驱动通常直接填 `eth_mac_addr` |
| `ndo_validate_addr(dev)` | 检查地址合法 | 通常直接填 `eth_validate_addr` |
| `ndo_set_rx_mode(dev)` | 同步单播/组播过滤表和 promisc | `netif_addr_lock` 下，BH 关，**不能睡** |
| `ndo_change_mtu(dev, new_mtu)` | 改 MTU | 只在 `min_mtu`/`max_mtu` 范围内被调用；核心已经查过界 |
| `ndo_tx_timeout(dev, txqueue)` | 看门狗超时 | `netif_tx_lock` 下、全部 TX 队列冻结、BH 关。进来时 `netif_queue_stopped()` 保证为真 |
| `ndo_eth_ioctl(dev, ifr, cmd)` | PHY 相关的 ioctl（`SIOCGMIIREG` 等） | `rtnl_lock` 下 |
| `ndo_set_features(dev, features)` | 按新 feature 集重配硬件 | 见 §9。⚠️ 错误码只进 dmesg，传不出去 |
| `ndo_fix_features(dev, features)` | 消解 feature 之间的依赖 | 必须**无副作用**，可能被连续调多次 |

### §3.3 按需实现的

| 回调 | 什么时候需要 |
|---|---|
| `ndo_init` / `ndo_uninit` | 有一部分初始化必须在 `rtnl_lock` 下做 |
| `ndo_select_queue(dev, skb, sb_dev)` | 自己决定 skb 进哪个 TX 队列，不用默认哈希 |
| `ndo_setup_tc(dev, type, type_data)` | 支持硬件 QoS、TC offload、mqprio |
| `ndo_bpf(dev, bpf)` | 支持 XDP。见 §12 |
| `ndo_xdp_xmit(dev, n, frames, flags)` | 支持 `XDP_REDIRECT` 的出向 |
| `ndo_xsk_wakeup(dev, qid, flags)` | 支持 AF_XDP 零拷贝 |
| `ndo_vlan_rx_add_vid` / `ndo_vlan_rx_kill_vid` | 硬件有 VLAN 过滤表 |
| `ndo_set_vf_*`（十余个） | 支持 SR-IOV |
| `ndo_get_phys_port_name` / `ndo_get_port_parent_id` | 多端口、switchdev |
| `ndo_features_check(skb, dev, features)` | 逐包否掉某些 offload（典型：隧道封装太深） |
| `ndo_poll_controller` | 支持 netconsole/netpoll |
| `ndo_get_devlink_port` | 接 devlink |

### §3.4 上下文与锁一览

这张表直接来自 `Documentation/networking/netdevices.rst` 的 synchronization rules 一节（第 210-299 行），是**驱动作者最需要背下来的一页**：

| 回调 | 同步 | 上下文 |
|---|---|---|
| `ndo_open` / `ndo_stop` | `rtnl_lock` | 进程，可睡 |
| `ndo_eth_ioctl` / `ndo_do_ioctl` / `ndo_siocwandev` | `rtnl_lock` | 进程 |
| `ndo_get_stats` / `ndo_get_stats64` | `rtnl_lock`、`dev_base_lock` 读锁、或 RCU | 原子，**不能睡** |
| `ndo_start_xmit` | `__netif_tx_lock` 自旋锁 | BH 关的进程上下文或 BH；netconsole 会在**关中断**下调它 |
| `ndo_tx_timeout` | `netif_tx_lock`，全部 TX 队列冻结 | BH 关 |
| `ndo_set_rx_mode` | `netif_addr_lock` 自旋锁 | BH 关 |
| `napi->poll` | `napi->state` 里的 `NAPI_STATE_SCHED` 位 | 软中断；netconsole 会在关中断下调 |

两处需要单独记：

- **`ndo_do_ioctl` 自 5.14 起不再由用户态 ioctl 直达**，只有网络子系统内部会调它（`Documentation/networking/netdevices.rst` 第 225-227 行）。新驱动要 PHY ioctl 就用 `ndo_eth_ioctl`。
- **`ndo_siocdevprivate` 明确写着 "should not be added to new drivers"**（同文件第 248 行）。看到老驱动在用不代表可以照抄。

### §3.5 `NETIF_F_LLTX` 的例外

`dev->features` 里置了 `NETIF_F_LLTX` 时，`ndo_start_xmit` **不再有 `__netif_tx_lock` 保护**，驱动要自己加锁，而且这把锁还得同时挡住 `ndo_set_rx_mode`。`Documentation/networking/netdevices.rst`（第 261-266 行）的原话是 "WARNING: use of NETIF_F_LLTX is deprecated. Don't use it for new drivers."

⚠️ 判断一个老驱动的 `ndo_start_xmit` 里那把自旋锁是不是多余的，先看它有没有置 `NETIF_F_LLTX`——**没置就是多余的，而且可能是死锁源**。

---

## §4 发送路径

头文件：`include/linux/netdevice.h`、`include/linux/skbuff.h`；实现：`net/core/dev.c`。

驱动在这条路上只出现在一个点：`ndo_start_xmit`。但它前后的核心函数需要知道，否则读不懂"包为什么没到我这儿"。

| API | 说明 | 备注 |
|---|---|---|
| `dev_queue_xmit(skb)` | 协议栈的发送入口 | `static inline`，转 `__dev_queue_xmit(skb, NULL)` |
| `dev_queue_xmit_accel(skb, sb_dev)` | 带子设备（VLAN/macvlan 加速）的版本 | 同上 |
| `netdev_pick_tx(dev, skb, sb_dev)` | 选 TX 队列；有 `ndo_select_queue` 就调它 | 核心内部 |
| `netdev_get_tx_queue(dev, index)` | 按序号拿 `netdev_queue` | `static inline` |
| `skb_tx_timestamp(skb)` | 软件时间戳打点，**放在真正下发硬件之前** | 驱动必须自己调 |
| `dev_kfree_skb_any(skb)` | 释放 skb，自动判断当前是否软中断上下文 | 发送失败丢包时用它 |
| `dev_consume_skb_any(skb)` | 同上，但语义是"正常消费"，不计入 drop_monitor | TX 完成回收用它 |
| `napi_consume_skb(skb, budget)` | NAPI 上下文里回收，可进 per-CPU 缓存 | TX 清理在 poll 里做时用它 |

### §4.1 返回值只有两个值

`netdev_tx_t` 的合法返回值：

| 返回值 | 含义 | 之后 skb 归谁 |
|---|---|---|
| `NETDEV_TX_OK` | 已接管 | 驱动。**必须在有限时间内释放** |
| `NETDEV_TX_BUSY` | 没接管，请核心重排队 | 核心。驱动**不能持有任何引用，也不能释放它** |

`Documentation/networking/driver.rst` 的 Timely completions 一节把这两条的后果写得很直白：`NETDEV_TX_OK` 之后不及时释放会"deadlock sockets waiting for send buffer room"；`NETDEV_TX_BUSY` 之后驱动碰 skb 就是 use-after-free。

⚠️ **`NETDEV_TX_BUSY` 基本上等于一个 bug**。`Documentation/networking/driver.rst` 的原话是 "Usually a bug, means queue was full"，正确做法是**提前停队列**（§5），而不是在满了之后返回 BUSY。同一份文档给的标准写法是：进 xmit 先看剩余描述符够不够 `skb_shinfo(skb)->nr_frags + 1`，不够就 `netif_stop_queue()` 并打印一条 hard error，然后才 BUSY——也就是**走到 BUSY 意味着流控已经失效了**。

丢包时也**不能**返回 BUSY：包坏了、DMA 映射失败，都要 `dev_kfree_skb_any(skb)` 然后返回 `NETDEV_TX_OK` 并累加 `tx_dropped`。返回 BUSY 会让核心一直重投同一个坏包。

### §4.2 不能改共享的 skb

`Documentation/networking/driver.rst` 的 No exclusive ownership 一节：驱动拿到的 skb 可能是克隆（`skb_cloned()` 为真），此时**数据区是共享的**。要写数据必须先确认独占。相关判定：

| API | 说明 | 备注 |
|---|---|---|
| `skb_cloned(skb)` | 是不是克隆 | |
| `skb_shared(skb)` | `users > 1` | |
| `skb_header_cloned(skb)` | 头部是否共享 | 只改头时用这个判定更宽松 |
| `skb_cow_head(skb, headroom)` | 需要时复制头部，顺手保证 headroom | 驱动要在包前面塞私有头时的标准写法 |
| `skb_linearize(skb)` | 把分片合成一块 | 硬件不支持 scatter-gather 时用，**有分配失败的可能** |
| `skb_padto(skb, len)` | 补齐到最小长度 | 以太网小于 60 字节的包要补 |
| `skb_put_padto(skb, len)` | 同上并推进 `tail` | |

### §4.3 时间戳与 GSO

| API | 说明 | 备注 |
|---|---|---|
| `skb_tx_timestamp(skb)` | 软件时间戳 | 每个 `ndo_start_xmit` 都该调 |
| `skb_shinfo(skb)->tx_flags & SKBTX_HW_TSTAMP` | 用户请求了硬件时间戳 | 硬件打完戳后由驱动回填 |
| `skb_tstamp_tx(orig_skb, hwtstamps)` | 把硬件时间戳交回给 socket | |
| `skb_is_gso(skb)` | 这是个待分段的大包 | 有 `NETIF_F_TSO` 时硬件自己切 |
| `skb_shinfo(skb)->gso_size` / `gso_segs` / `gso_type` | 分段参数 | |
| `skb_checksum_help(skb)` | 软件算校验和 | 硬件不支持某种封装时的兜底 |

---

## §5 队列流控与 BQL

头文件：`include/linux/netdevice.h`、`include/net/netdev_queues.h`。

这一节是**以太网驱动最容易写出偶发卡死的地方**。核心概念只有一个：TX 队列的"停"和"唤"必须成对，而且唤的一侧要能看到停的一侧写下的全部状态。

### §5.1 单队列接口

| API | 说明 | 备注 |
|---|---|---|
| `netif_start_queue(dev)` | 允许发送，不触发调度 | `ndo_open` 末尾用 |
| `netif_stop_queue(dev)` | 禁止发送 | |
| `netif_wake_queue(dev)` | 允许发送**并**唤起 qdisc 继续送 | TX 清理后用这个，不是 `start` |
| `netif_queue_stopped(dev)` | 查询 0 号队列状态 | |
| `netif_tx_queue_stopped(txq)` | 查询指定队列 | |

⚠️ **`netif_start_queue()` 与 `netif_wake_queue()` 的区别是"要不要踢 qdisc"**。清完 TX 描述符后用 `start`，队列会一直空着直到下一个包自然到来——表现为吞吐忽高忽低，不是完全不通，所以极难发现。

### §5.2 多队列接口

| API | 说明 | 备注 |
|---|---|---|
| `netif_start_subqueue(dev, index)` | 同 `netif_start_queue` 但指定队列 | |
| `netif_stop_subqueue(dev, index)` | | |
| `netif_wake_subqueue(dev, index)` | | |
| `netif_subqueue_stopped(dev, skb)` | 按 skb 的 `queue_mapping` 查 | |
| `netif_tx_lock(dev)` / `netif_tx_unlock(dev)` | 锁住**全部** TX 队列 | 一般只有核心用 |
| `__netif_tx_lock(txq, cpu)` | 锁单个队列 | 核心在调 `ndo_start_xmit` 前已经持了 |

`Documentation/networking/multiqueue.rst` 的要求：多队列驱动必须用 `alloc_etherdev_mq()` 或 `alloc_netdev_mq()` 分配，并且**只用 subqueue 系列管队列**。混用 `netif_stop_queue()`（作用于 0 号队列）和 subqueue 接口是常见错误。

### §5.3 BQL：字节队列限制

| API | 说明 | 备注 |
|---|---|---|
| `netdev_tx_sent_queue(txq, bytes)` | 报告刚下发了多少字节 | 在 `ndo_start_xmit` 里，紧跟真正下发之后 |
| `netdev_sent_queue(dev, bytes)` | 单队列版本 | |
| `netdev_tx_completed_queue(txq, pkts, bytes)` | 报告完成了多少 | 在 TX 清理里，**一批只调一次** |
| `netdev_completed_queue(dev, pkts, bytes)` | 单队列版本 | |
| `netdev_tx_reset_queue(txq)` | 清零 | `ndo_stop` 里，或者重置队列后 |
| `netdev_reset_queue(dev)` | 单队列版本 | |

BQL 的作用是把"队列里排多少字节"限制在一个自适应的水位上，从而压低延迟。它对驱动的要求是**发送和完成的字节数必须严格配平**：漏报一次完成，队列水位就永久偏高一点，累积到上限后队列再也不放包——表现是网络跑一段时间后彻底停住，而 `ethtool -S` 看不出异常。

⚠️ **`netdev_tx_reset_queue()` 是必须的**。`ndo_stop`、队列重置、`ndo_tx_timeout` 恢复这三处都要清零，否则重启后水位是脏的。这是 BQL 相关问题里最常见的一个。

### §5.4 无锁停唤宏

头文件：`include/net/netdev_queues.h`。这六个宏把"停队列 → 再查一次 → 可能立刻恢复"这个必须的双重检查封装了起来。本版本里有的就是这六个（更晚的内核还加了带 BQL 记账的 `*_completed_wake`，**6.1 上没有**）：

| 宏 | 说明 | 备注 |
|---|---|---|
| `netif_txq_try_stop(txq, get_desc, start_thrs)` | 停队列，然后重查描述符，够了就重新开 | 返回是否真的停住了 |
| `netif_txq_maybe_stop(txq, get_desc, stop_thrs, start_thrs)` | 描述符低于 `stop_thrs` 才尝试停 | xmit 里最常用的一个 |
| `netif_txq_maybe_wake(txq, get_desc, start_thrs)` | 描述符够了就唤醒 | TX 清理里用 |
| `__netif_txq_maybe_wake(txq, get_desc, start_thrs, down_cond)` | 同上，多一个"设备正在下线就别唤"的条件 | 复位/关闭路径要用它 |
| `netif_subqueue_try_stop(dev, idx, get_desc, start_thrs)` | 按队列序号的版本 | |
| `netif_subqueue_maybe_stop(dev, idx, get_desc, stop_thrs, start_thrs)` | 同上 | |

BQL 的记账（`netdev_tx_completed_queue()`）在 6.1 上要**自己单独调**，这几个宏不代劳。

这几个宏的三条使用前提写在该头文件的开头注释里，都是硬性的：

- **只能在 xmit 处理函数里调用 try-stop 系列**。它内部用的是 `netif_tx_start_queue()` 而不是 `netif_tx_wake_queue()`，在别处调会出现"队列开了但没人去跑"的状态。
- **调用之前必须把全部环形索引更新完**。宏依赖 `get_desc` 读到的是最新值。
- **它不保证没有假唤醒**。环满的情况下仍可能唤起一次，驱动的 xmit 必须能处理"被唤醒了但还是没位置"。

`Documentation/networking/driver.rst` 直接把这个头文件的 kernel-doc 拉进了正文（该文件第 73 行的 `kernel-doc` 指令），所以这几个宏是**官方推荐写法**，不是某个驱动的私货。

---

## §6 接收路径与 NAPI

头文件：`include/linux/netdevice.h`；实现：`net/core/dev.c`、`net/core/gro.c`。

### §6.1 NAPI 生命周期

| API | 说明 | 备注 |
|---|---|---|
| `netif_napi_add(dev, napi, poll)` | 注册一个 NAPI 实例，权重取默认 64 | `static inline`，转 `netif_napi_add_weight()` |
| `netif_napi_add_weight(dev, napi, poll, weight)` | 指定权重 | 实现在 `net/core/dev.c` |
| `netif_napi_add_tx(dev, napi, poll)` | 只做 TX 清理的 NAPI，权重 0 | 权重 0 表示不参与 RX 预算统计 |
| `netif_napi_add_tx_weight(dev, napi, poll, weight)` | 同上，指定权重 | |
| `netif_napi_del(napi)` | 注销 | 在 `free_netdev()` 之前 |
| `napi_enable(napi)` | 允许被调度 | `ndo_open` 里，**在使能硬件中断之前** |
| `napi_disable(napi)` | 禁止并等待当前 poll 结束 | `ndo_stop` 里，**会睡** |
| `napi_synchronize(napi)` | 等 poll 跑完但不禁用 | 改配置时用 |

⚠️ **`netif_napi_add()` 必须在 `register_netdev()` 之前调**，`napi_enable()` 必须在 `ndo_open` 里调。两者混起来是常见错误：在 `ndo_open` 里 `netif_napi_add()`，在 `ndo_stop` 里 `netif_napi_del()`，看着对称，但反复 up/down 会重复注册。

`Documentation/networking/netdevices.rst`（第 292-299 行）说明 `napi->poll` 的同步靠 `napi->state` 里的 `NAPI_STATE_SCHED` 位，而 `ndo_stop` 要调 `napi_disable()` 来确保 poll 不再跑。

### §6.2 调度与完成

| API | 说明 | 备注 |
|---|---|---|
| `napi_schedule(napi)` | 需要时调度 | `static inline`，内部先 `napi_schedule_prep()` |
| `napi_schedule_irqoff(napi)` | 已知中断关着的版本 | 省一次 `local_irq_save` |
| `napi_schedule_prep(napi)` | 只做"能不能调度"的判定 | 想自己控制流程时用 |
| `__napi_schedule(napi)` | 真正入队 | 要先 `prep` 返回真 |
| `__napi_schedule_irqoff(napi)` | 同上 | |
| `napi_complete_done(napi, work_done)` | 退出 poll；返回是否真的退出了 | **返回假时不能重开中断** |
| `napi_complete(napi)` | `napi_complete_done(napi, 0)` | 不支持 busy-poll 时用 |

poll 函数的标准结构是固定的：清 TX、收 RX 到 `budget` 为止，**收满 `budget` 就直接返回 `budget` 不调 complete**；没收满才 `napi_complete_done()` 并重开中断。

⚠️ **`napi_complete_done()` 的返回值必须检查**。它返回假意味着 NAPI 又被重新调度了（busy-poll 或者期间来了新包），此时**重开硬件中断是错的**——会出现中断和 poll 同时跑。

### §6.3 交包给协议栈

| API | 说明 | 备注 |
|---|---|---|
| `napi_gro_receive(napi, skb)` | NAPI 上下文的标准入口，走 GRO | 实现在 `net/core/gro.c` |
| `napi_gro_frags(napi)` | 从 `napi->skb` 的 frags 直接建包 | 零拷贝路径，少数驱动用 |
| `netif_receive_skb(skb)` | 不走 GRO 直接进栈 | 需要 BH 关 |
| `netif_receive_skb_list(head)` | 批量版本 | |
| `netif_rx(skb)` | 老式入口，会先进 per-CPU backlog | 新驱动不该用 |
| `eth_type_trans(skb, dev)` | 解析以太网头，填 `skb->protocol` 和 `pkt_type` | 每个以太网驱动收包必调 |
| `napi_alloc_skb(napi, len)` | NAPI 上下文里分配小 skb | 走 per-CPU 缓存 |
| `napi_build_skb(data, frag_size)` | 围着已有数据建 skb | page_pool 路径常用 |
| `netdev_alloc_skb_ip_align(dev, len)` | 分配并做 IP 对齐 | 非 NAPI 上下文用 |
| `skb_record_rx_queue(skb, index)` | 记录来自哪个 RX 队列 | RPS/RFS 会用 |
| `skb_set_hash(skb, hash, type)` | 填硬件算的哈希 | 有 `NETIF_F_RXHASH` 时 |

`eth_type_trans()` 顺序上必须在 `napi_gro_receive()` 之前——GRO 依赖 `skb->protocol`。

### §6.4 校验和卸载的三种上报

这是收包侧唯一容易报错的地方：

| `skb->ip_summed` | 含义 | 驱动什么时候用 |
|---|---|---|
| `CHECKSUM_NONE` | 硬件没查，协议栈自己算 | 默认，也是不支持时的正确值 |
| `CHECKSUM_UNNECESSARY` | 硬件已验证通过 | 硬件能识别协议并给出"对/错"结论时 |
| `CHECKSUM_COMPLETE` | `skb->csum` 里是整包的补和 | 硬件只会算和、不判协议时 |

⚠️ **把 `CHECKSUM_UNNECESSARY` 用在硬件其实没验证的包上，是一类静默的数据损坏**——坏包会被当成好包送上去。判据是硬件手册里那一位到底表示"校验和正确"还是"校验和已计算"。

### §6.5 线程化 NAPI

| API | 说明 | 备注 |
|---|---|---|
| `dev_set_threaded(dev, threaded)` | 把该 netdev 的所有 NAPI 切成内核线程 | 实现在 `net/core/dev.c` |

用户态入口是 `/sys/class/net/<dev>/threaded`。这条对 PREEMPT_RT 有意义，展开在 `04-extensions.md` §1.4 和 `07-debug.md` §5。

---

## §7 链路状态

头文件：`include/linux/netdevice.h`；实现：`net/sched/sch_generic.c`、`net/core/link_watch.c`。

| API | 说明 | 备注 |
|---|---|---|
| `netif_carrier_on(dev)` | 报告链路 up | 实现在 `net/sched/sch_generic.c` |
| `netif_carrier_off(dev)` | 报告链路 down | 同上 |
| `netif_carrier_ok(dev)` | 查询 | `static inline` |
| `netif_carrier_event(dev)` | 只发一次事件，不改状态 | 用于"链路抖了一下" |
| `netif_device_attach(dev)` | 设备回到可用（如从 suspend 恢复） | |
| `netif_device_detach(dev)` | 设备不可用，停所有队列 | suspend / 复位时用 |
| `netif_device_present(dev)` | 查询 | `static inline` |
| `netif_running(dev)` | 查询管理状态（是否 `ndo_open` 过） | 与 carrier 是两件事 |
| `netif_dormant_on` / `_off` / `netif_dormant` | 802.1X 之类"物理通了但不许转发" | 少数场景 |

⚠️ **`netif_carrier_ok()` 与 `netif_running()` 必须分清**：前者是"线插了没"，后者是"管理上 up 了没"。两者都真才会真正收发包。`ip link` 输出里的 `state UP` 与 `NO-CARRIER` 分别对应这两个。

`netif_carrier_on/off` 不会同步上报——它把设备挂进 `link_watch` 的队列，由 `linkwatch_run_queue()` 延迟处理（`net/core/link_watch.c`）。所以**调完这两个函数之后立刻读 `/sys/class/net/*/operstate` 可能还是旧值**，这不是 bug。用 phylink 或 phylib 的驱动通常不直接调这两个，由 PHY 层代劳。

---

## §8 统计

头文件：`include/linux/netdevice.h`、`include/linux/u64_stats_sync.h`；实现：`net/core/dev.c`。

### §8.1 上报接口

| API | 说明 | 备注 |
|---|---|---|
| `ndo_get_stats64(dev, storage)` | 驱动实现的回调，填 `rtnl_link_stats64` | **不能睡** |
| `dev_get_stats(dev, storage)` | 核心的读取入口 | 会把 `core_stats` 合进去 |
| `dev_fetch_sw_netstats(s, netstats)` | 把 per-CPU 的 `pcpu_sw_netstats` 汇总 | ⚠️ **本树改过这个函数的内部实现**，见 §0.6 |
| `dev_get_tstats64(dev, s)` | 可以直接填给 `ndo_get_stats64` 的现成实现 | 私有区里放 `pcpu_sw_netstats` 时用 |
| `dev_core_stats_rx_dropped_inc(dev)` 一族 | 核心侧的兜底计数 | 驱动一般不用 |

`Documentation/networking/statistics.rst` 给驱动作者的四条硬约束：

- **凡是能对上 `rtnl_link_stats64` 字段的统计，必须且只能通过 `.ndo_get_stats64` 上报**，不要再在 ethtool 里重复一遍。
- **细分的错误计数必须同时汇总进 `rx_errors` / `tx_errors`**，否则用户看不到总量。
- **ethtool 统计的条目数必须恒定**，不能因为链路状态变化而增减——用户态工具按第一次读到的字符串表解析。
- **统计必须跨 down/up 保持**，而且**不能把没实现的字段写 0**（核心已经清零，写 0 等于宣称"确实是 0"）。

### §8.2 per-CPU 计数与 `u64_stats_sync`

⚠️ **本节涉及本树补丁，写代码前务必读完。**

驱动通常在每个队列或每个 CPU 上放一组计数，用 `u64_stats_sync` 保护。写侧和读侧的配对：

| API | 说明 | 备注 |
|---|---|---|
| `u64_stats_init(syncp)` | 初始化 | 32 位下是宏，64 位下是空函数 |
| `u64_stats_update_begin(syncp)` | 写侧开始 | |
| `u64_stats_update_end(syncp)` | 写侧结束 | |
| `u64_stats_update_begin_irqsave(syncp)` | 写侧可能被中断打断时 | 返回 flags |
| `u64_stats_update_end_irqrestore(syncp, flags)` | 配对 | |
| `u64_stats_add(p, val)` / `u64_stats_inc(p)` | 累加 | 操作 `u64_stats_t` |
| `u64_stats_read(p)` | 读一个计数值 | |
| `u64_stats_set(p, val)` | 设置 | |
| `u64_stats_fetch_begin(syncp)` | 读侧开始，返回 seq | |
| `u64_stats_fetch_retry(syncp, seq)` | 读侧判定要不要重读 | |

**本树上没有 `u64_stats_fetch_begin_irq()` 和 `u64_stats_fetch_retry_irq()`**——它们已被删除（§0.6）。写了会编译失败。上游 6.1.158 里这两个是纯转发包装，删掉它们不改变任何语义，只是逼调用者用统一的名字。

`include/linux/u64_stats_sync.h` 开头的注释还写着一条**写侧的约束**：同一个 `syncp` 的写侧必须互斥。per-CPU 计数天然满足（只有本 CPU 写），但如果写侧可能被软中断或硬中断抢占，就要用 `_irqsave` 版本。

判定规则：

| 写侧可能来自 | 用什么 |
|---|---|
| 只有 NAPI poll（软中断） | `u64_stats_update_begin()` / `_end()` |
| NAPI poll 加硬中断处理 | `u64_stats_update_begin_irqsave()` / `_end_irqrestore()` |
| 只有进程上下文（如 `ndo_start_xmit` 关 BH 下） | `u64_stats_update_begin()` / `_end()` |

读侧永远是 `u64_stats_fetch_begin()` 加 `u64_stats_fetch_retry()` 的 `do/while` 循环，**不需要考虑中断**——这正是 `_irq` 变体被判定为多余、进而被删掉的原因。展开在 `03-internals.md` §B7 和 `08-feynman.md` §5。

### §8.3 三种统计源

`Documentation/networking/statistics.rst` 把统计分成三类，驱动要清楚自己填的是哪一类：

| 源 | 用户态怎么看 | 谁定义字段名 |
|---|---|---|
| 标准 link 统计（`rtnl_link_stats64`） | `ip -s link`、`/proc/net/dev`、`/sys/class/net/*/statistics/` | 内核，字段固定 |
| ethtool 标准组 | `ethtool -S <dev> --groups eth-mac` 等 | 内核，`eth-phy` / `eth-mac` / `eth-ctrl` / `rmon` 四组 |
| 驱动私有 ethtool 统计 | `ethtool -S <dev>` | 驱动自己，通过 `get_strings` 声明 |

用哪一类的判据很简单：**能对上标准字段就用标准的**。把 `rx_packets` 塞进私有 ethtool 统计是错的，因为标准工具看不到。

---

## §9 features 与 offload

头文件：`include/linux/netdev_features.h`、`include/linux/netdevice.h`；实现：`net/core/dev.c`。

`include/linux/netdev_features.h` 里定义了 **63 个 feature 位**，其中 **62 个**在 `ethtool -k` 里有对应名字——差的那一个是 `NETIF_F_GSO_UDP_BIT`（`:54`，注释写着 UFO、除 tuntap 外已废弃），`net/ethtool/common.c` 的 `netdev_features_strings[]`（`:11-77`）里没给它字符串，所以用户看不到它。两个口径都要标清楚，只说"62 个位"是错的（口径见 `99-evidence.md` §3）。驱动跟它们打交道靠四个字段和三个回调，弄清这四个字段的分工是本节的全部内容。

### §9.1 四个集合

| 字段 | 谁写 | 语义 |
|---|---|---|
| `dev->hw_features` | **只有驱动**，且只在注册前或 `ndo_init` 里 | 「这些位用户可以开关」 |
| `dev->features` | 核心；驱动只在 `ndo_set_features` 的错误路径里改 | 「现在实际生效的是这些」 |
| `dev->vlan_features` | 驱动 | 「基于我建的 VLAN 子设备能继承这些」 |
| `dev->wanted_features` | 核心 | 「用户希望开这些」——⚠️ **驱动不得引用** |

`Documentation/networking/netdev-features.rst` 明确写了 `hw_features` "should be initialized in ndo_init callback and not changed later"，`wanted_features` 是 "kernel-only" 的、驱动不该碰。

一个位要能被 `ethtool -K` 打开，条件是它同时在 `hw_features` 里。只放进 `features` 不放进 `hw_features` 的位是**强制开启、用户关不掉**的。

### §9.2 重算的三个触发点

`features` 不是驱动直接写的，是核心算出来的。重算发生在：

1. 注册流程里 `ndo_init` 返回成功之后（第一次）；
2. 用户通过 ethtool 请求改动；
3. 驱动主动调 `netdev_update_features(dev)`。

| API | 说明 | 备注 |
|---|---|---|
| `netdev_update_features(dev)` | 重算并在有变化时下发 `ndo_set_features` | 需持 `rtnl_lock` |
| `netdev_change_features(dev)` | 强制下发一次，即使算出来没变 | 硬件状态被外部改动后用 |
| `netdev_features_change(dev)` | 只发通知，不重算 | |

### §9.3 三个回调的分工

| 回调 | 什么时候调 | 约束 |
|---|---|---|
| `ndo_fix_features(dev, features)` | 每次重算，在下发之前 | **必须无状态**、可被连续调多次、返回修正后的集合 |
| `ndo_set_features(dev, features)` | 算出的集合与当前不同时 | 真正去配硬件 |
| `ndo_features_check(skb, dev, features)` | 逐包，发送路径上 | 极热，**不能睡也不能加锁** |

`ndo_fix_features` 的用途是表达位之间的依赖——比如"关了 RX 校验和就必须关 LRO"。它有两条禁令（同一份 Documentation）：**不能改 `NETIF_F_SOFT_FEATURES` 里的位**，**不能改 `NETIF_F_NEVER_CHANGE` 里的位**（唯一例外是 `NETIF_F_VLAN_CHALLENGED`）。这两个组合宏都在 `include/linux/netdev_features.h` 里。

⚠️ **`ndo_set_features` 的返回值是个黑洞**。上游文档的原话是 errors "are not (and cannot be) propagated anywhere except dmesg"，而且**返回正数被当作"静默错误"**，只有 0 才是成功。也就是说驱动在这里失败时无法通知用户态，只能自己把 `dev->features` 改回去。

### §9.4 常用位的分组

| 分组 | 位 | 备注 |
|---|---|---|
| 发送校验和 | `NETIF_F_IP_CSUM`、`NETIF_F_IPV6_CSUM`、`NETIF_F_HW_CSUM` | `HW_CSUM` 是前两者的超集，**新硬件只该声明 `HW_CSUM`** |
| 接收校验和 | `NETIF_F_RXCSUM` | |
| 分散聚合 | `NETIF_F_SG`、`NETIF_F_FRAGLIST` | 支持 `SG` 才能收到带 frags 的 skb |
| 分段卸载 | `NETIF_F_TSO`、`NETIF_F_TSO6`、`NETIF_F_TSO_ECN`、`NETIF_F_TSO_MANGLEID`、`NETIF_F_GSO_UDP_L4` | `TSO` 依赖 `SG` 加发送校验和 |
| 合并 | `NETIF_F_GRO`、`NETIF_F_LRO`、`NETIF_F_GRO_HW` | `GRO` 是软件的，`LRO`/`GRO_HW` 是硬件的 |
| VLAN | `NETIF_F_HW_VLAN_CTAG_TX/RX/FILTER`、`..._STAG_...` | CTAG 是 802.1Q，STAG 是 802.1ad |
| 哈希与队列 | `NETIF_F_RXHASH`、`NETIF_F_NTUPLE` | |
| 协议/规则卸载 | `NETIF_F_HW_TC`（TC 分类规则下硬件，见 `04-extensions.md` §8）、`NETIF_F_HW_ESP`（IPsec 加密卸载）、`NETIF_F_HW_TLS_TX/RX` | ⚠️ 这一组跟时间戳无关；硬件时间戳不走 feature 位，走 `ethtool_ops.get_ts_info` 与 `SIOCSHWTSTAMP` |
| 特殊 | `NETIF_F_LLTX`、`NETIF_F_VLAN_CHALLENGED`、`NETIF_F_NETNS_LOCAL` | 都在 `NETIF_F_NEVER_CHANGE` 里 |

校验和与分段这两组的官方原文分别是 `Documentation/networking/checksum-offloads.rst`（143 行）和 `Documentation/networking/segmentation-offloads.rst`（184 行）；每个位的用户可见名字与依赖关系在 `Documentation/networking/netdev-features.rst`（205 行）。

`vlan_features` 里通常放"对 VLAN 打了标签之后仍然有效"的位。硬件如果只对未打标的包能算校验和，就**不能**把校验和位放进 `vlan_features`。

---

## §10 `ethtool_ops`

头文件：`include/linux/ethtool.h`；实现：`net/ethtool/`（ioctl 前端在 `net/ethtool/ioctl.c`，netlink 前端在 `net/ethtool/netlink.c`）。

`struct ethtool_ops` 有 **68 个函数指针成员**。这里按用户命令分组，只列以太网驱动常填的：

### §10.1 基本信息

| 回调 | 对应命令 | 备注 |
|---|---|---|
| `get_drvinfo(dev, info)` | `ethtool -i` | 填驱动名、版本、固件版本、总线信息 |
| `get_link(dev)` | `ethtool <dev>` 里的 Link detected | 通常填 `ethtool_op_get_link` |
| `get_link_ksettings(dev, cmd)` | `ethtool <dev>` 的速率/双工/自协商 | 用 phylib 时填 `phy_ethtool_get_link_ksettings` |
| `set_link_ksettings(dev, cmd)` | `ethtool -s` | |
| `nway_reset(dev)` | `ethtool -r` | |
| `get_link_ext_state(dev, state)` | `ethtool <dev>` 里链路 down 的原因 | 能报告"为什么没链路" |
| `get_msglevel` / `set_msglevel` | `ethtool -s msglvl` | 通常配合 `netif_msg_*()` 宏 |

### §10.2 队列与中断

| 回调 | 对应命令 | 备注 |
|---|---|---|
| `get_ringparam` / `set_ringparam` | `ethtool -g` / `-G` | ⚠️ 支持 `set_` 时应同时填 `supported_ring_params` |
| `get_channels` / `set_channels` | `ethtool -l` / `-L` | 队列数 |
| `get_coalesce` / `set_coalesce` | `ethtool -c` / `-C` | ⚠️ **必须填 `supported_coalesce_params`**，否则核心拒绝所有 set |
| `get_per_queue_coalesce` / `set_per_queue_coalesce` | `ethtool -c queue_mask` | |
| `get_pauseparam` / `set_pauseparam` | `ethtool -a` / `-A` | |
| `get_pause_stats(dev, stats)` | `ethtool -a --include-statistics` | 用 `ethtool_pause_stats()` 帮忙初始化 |

`supported_coalesce_params` 是一个 `u32` 位掩码而非回调，容易漏。漏了的表现是 `ethtool -C` 一律报 `Operation not supported`，而 `get_coalesce` 却工作正常。

### §10.3 统计

| 回调 | 对应命令 | 备注 |
|---|---|---|
| `get_sset_count(dev, sset)` | 条目数 | 对 `ETH_SS_STATS` 返回统计条数；不支持的 set 返回 `-EOPNOTSUPP` |
| `get_strings(dev, sset, data)` | 字符串表 | 每条 `ETH_GSTRING_LEN` 字节 |
| `get_ethtool_stats(dev, stats, data)` | `ethtool -S` | 数量必须与 `get_sset_count` 一致 |
| `get_eth_phy_stats` / `get_eth_mac_stats` / `get_eth_ctrl_stats` / `get_rmon_stats` | `ethtool -S --groups` | 标准组，字段名由内核定义 |
| `self_test(dev, test, data)` | `ethtool -t` | 配合 `ETH_SS_TEST` 的字符串表 |

⚠️ **`get_sset_count` 与 `get_strings` 与 `get_ethtool_stats` 三者的条目数必须严格一致**，而且**不能随链路状态变化**。用户态先读一次字符串表再按下标解析数值，数量变了就会读到错位的数据（不是报错，是显示错误的数字）。

### §10.4 其他

| 回调 | 对应命令 | 备注 |
|---|---|---|
| `get_wol` / `set_wol` | `ethtool -s wol` | |
| `get_eee` / `set_eee` | `ethtool --show-eee` | |
| `get_ts_info(dev, info)` | `ethtool -T` | 报告支持哪些硬件时间戳 |
| `get_regs_len` / `get_regs` | `ethtool -d` | |
| `get_eeprom_len` / `get_eeprom` / `set_eeprom` | `ethtool -e` / `-E` | |
| `get_module_info` / `get_module_eeprom` | `ethtool -m` | 光模块 |
| `get_rxnfc` / `set_rxnfc` | `ethtool -n` / `-N` | 收包分类规则 |
| `get_rxfh_indir_size` / `get_rxfh_key_size` / `get_rxfh` / `set_rxfh` | `ethtool -x` / `-X` | RSS 表 |
| `get_priv_flags` / `set_priv_flags` | `ethtool --show-priv-flags` | 驱动自定义开关 |
| `get_fecparam` / `set_fecparam` | `ethtool --show-fec` | |
| `reset(dev, flags)` | `ethtool --reset` | |
| `begin(dev)` / `complete(dev)` | 每条 ethtool 命令的前后钩子 | 用来 `pm_runtime_get/put` |

`begin` 返回非 0 会让整条命令失败，`complete` 无返回值——**这一对是 ethtool 路径上唯一能做电源管理的地方**。

---

## §11 MAC 地址与 etherdevice 辅助

头文件：`include/linux/etherdevice.h`；实现：`net/ethernet/eth.c`。

### §11.1 从固件取地址

按优先级从高到低试，这是 arm64 上标准的做法：

| API | 说明 | 备注 |
|---|---|---|
| `device_get_ethdev_address(dev, netdev)` | 从 `fwnode`（arm64 上是 DT）取，直接写进 `netdev->dev_addr` | **首选**，一步到位 |
| `platform_get_ethdev_address(dev, netdev)` | 同上但只查 DT/platform | |
| `nvmem_get_mac_address(dev, addrbuf)` | 从 NVMEM cell 取 | `nvmem-cells` 属性 |
| `eth_platform_get_mac_address(dev, addrbuf)` | 老式接口，取到缓冲区 | 新驱动用带 `ethdev` 的版本 |
| `eth_hw_addr_random(netdev)` | 生成随机地址并置 `NET_ADDR_RANDOM` | 全都取不到时的兜底 |
| `eth_hw_addr_set(netdev, addr)` | 写 `dev_addr`（**唯一合法写法**） | |
| `eth_hw_addr_inherit(dst, src)` | 从另一个 netdev 继承 | 多端口驱动用 |

⚠️ **`dev->dev_addr` 在 6.1 里是 `const unsigned char *`**，不能直接 `memcpy` 进去。必须用 `eth_hw_addr_set()`（或 `dev_addr_set()`）。老代码里的 `memcpy(dev->dev_addr, ...)` 在这个版本上编译不过——这是从 5.x 移植驱动时最常撞的一处。

### §11.2 地址判定

| API | 说明 | 备注 |
|---|---|---|
| `is_valid_ether_addr(addr)` | 非组播、非全零 | 设置地址前必查 |
| `is_zero_ether_addr(addr)` | 全零 | |
| `is_multicast_ether_addr(addr)` | 最低位为 1 | |
| `is_broadcast_ether_addr(addr)` | 全 `ff` | |
| `is_local_ether_addr(addr)` | 本地管理位 | |
| `ether_addr_equal(a, b)` | 比较 | |
| `ether_addr_copy(dst, src)` | 拷贝六字节 | 要求两端都对齐到 2 字节 |
| `eth_zero_addr(addr)` / `eth_broadcast_addr(addr)` | 置零 / 置广播 | |

`Documentation/networking/driver.rst` 的 Address validation 一节要求：**驱动在设置地址前必须用 `is_valid_ether_addr()` 检查**。填 `ndo_validate_addr = eth_validate_addr` 就自动满足了这条——`eth_validate_addr()`（`net/ethernet/eth.c`）内部就是这个检查。

### §11.3 地址表

头文件：`include/linux/netdevice.h`；实现：`net/core/dev_addr_lists.c`。

`ndo_set_rx_mode` 里要遍历地址表把硬件过滤器配上：

| API | 说明 | 备注 |
|---|---|---|
| `netdev_for_each_mc_addr(ha, dev)` | 遍历组播表 | 宏 |
| `netdev_for_each_uc_addr(ha, dev)` | 遍历额外的单播表 | 宏 |
| `netdev_mc_count(dev)` / `netdev_uc_count(dev)` | 条目数 | 硬件表放不下时的判据 |
| `netdev_mc_empty(dev)` / `netdev_uc_empty(dev)` | | |
| `dev_uc_add` / `dev_uc_del` / `dev_mc_add` / `dev_mc_del` | 增删（一般由上层调） | |
| `dev_set_promiscuity(dev, inc)` | 增减 promisc 引用计数 | 核心侧；驱动看 `dev->flags & IFF_PROMISC` |
| `dev_set_allmulti(dev, inc)` | 同上，allmulti | |

标准写法是：先看 `dev->flags` 里的 `IFF_PROMISC`，再看 `IFF_ALLMULTI`，再看 `netdev_mc_count()` 有没有超过硬件表容量——超了就退化成 allmulti。

---

## §12 XDP 与 page_pool

头文件：`include/net/xdp.h`、`include/net/page_pool.h`、`include/linux/netdevice.h`；实现：`net/core/xdp.c`、`net/core/page_pool.c`。

### §12.1 page_pool

| API | 说明 | 备注 |
|---|---|---|
| `page_pool_create(params)` | 建一个池，一般每个 RX 队列一个 | 返回 `ERR_PTR` |
| `page_pool_destroy(pool)` | 销毁 | 要在所有页归还之后 |
| `page_pool_dev_alloc_pages(pool)` | 取一页（用 `pool->p.nid`） | 收包时用 |
| `page_pool_alloc_pages(pool, gfp)` | 指定 gfp 的版本 | |
| `page_pool_put_page(pool, page, dma_sync_size, allow_direct)` | 归还 | `allow_direct` 只在 NAPI 上下文里可为真 |
| `page_pool_put_full_page(pool, page, allow_direct)` | 归还整页 | |
| `page_pool_recycle_direct(pool, page)` | NAPI 上下文的快路径 | |
| `page_pool_release_page(pool, page)` | 让页脱离池（解 DMA 映射） | 页要交给协议栈长期持有时用 |
| `page_pool_get_dma_addr(page)` | 拿池记下的 DMA 地址 | 省一次 `dma_map` |
| `page_pool_set_dma_addr(page, addr)` | | |
| `skb_mark_for_recycle(skb)` | 标记这个 skb 的页由 page_pool 回收 | 建好 skb 后调，**替代 `page_pool_release_page`** |

`page_pool_params` 里驱动必填的字段：`order`、`flags`（`PP_FLAG_DMA_MAP`、`PP_FLAG_DMA_SYNC_DEV`）、`pool_size`、`nid`、`dev`、`dma_dir`、`max_len`、`offset`。

⚠️ **置了 `PP_FLAG_DMA_SYNC_DEV` 之后就不要自己再 `dma_sync_single_for_device()`**——池会在归还时按 `max_len` 和 `offset` 自动同步。两边都做不会出错但白费时间；只依赖自己做而没置这个标志，则在归还时漏同步。

### §12.2 XDP

| API | 说明 | 备注 |
|---|---|---|
| `ndo_bpf(dev, bpf)` | 挂/摘 XDP 程序 | 看 `bpf->command`：`XDP_SETUP_PROG` 等 |
| `xdp_rxq_info_reg(xdp_rxq, dev, queue_index, napi_id)` | 每个 RX 队列注册一次 | `ndo_open` 或建队列时 |
| `xdp_rxq_info_unreg(xdp_rxq)` | 注销 | |
| `xdp_rxq_info_reg_mem_model(xdp_rxq, type, allocator)` | 声明内存模型 | 用 page_pool 时 type 是 `MEM_TYPE_PAGE_POOL` |
| `xdp_init_buff(xdp, frame_sz, rxq)` | 初始化 `xdp_buff` | 每个包一次 |
| `xdp_prepare_buff(xdp, hard_start, headroom, data_len, meta_valid)` | 填数据指针 | |
| `bpf_prog_run_xdp(prog, xdp)` | 跑程序，返回动作 | |
| `xdp_do_redirect(dev, xdp, prog)` | 处理 `XDP_REDIRECT` | |
| `xdp_do_flush()` | 一轮 poll 结束时刷 redirect 批 | **漏调会导致包延迟到下一轮甚至丢** |
| `xdp_convert_buff_to_frame(xdp)` | `xdp_buff` 转 `xdp_frame` | 要跨上下文传时 |
| `xdp_return_frame(frame)` / `xdp_return_frame_rx_napi(frame)` | 释放 frame | 后者在 NAPI 里 |
| `ndo_xdp_xmit(dev, n, frames, flags)` | 别的设备 redirect 过来的出向 | 支持 `XDP_XMIT_FLUSH` |
| `xdp_master_redirect(xdp)` | 从从设备转到主设备（bond/DSA） | |

⚠️ 6.1 上**没有 `xdp_features` 这套能力声明机制**（`xdp_set_features_flag()` 在更晚的内核才有）。驱动支持了什么只能靠 `ndo_bpf` 里对不同 `command` 的应答来体现，用户态无法统一查询。

五个动作驱动都要处理：`XDP_PASS`（继续正常收包）、`XDP_DROP`（直接回收页）、`XDP_TX`（从本队列发回去）、`XDP_REDIRECT`（交给 `xdp_do_redirect`）、`XDP_ABORTED`（调 `trace_xdp_exception()` 再丢）。

⚠️ **`XDP_ABORTED` 不能静默丢包**。它表示程序出错，必须打 tracepoint，否则出问题时用户完全看不到线索。

---

## §13 错误码语义

这一节按"读者看到这个错误码时该怀疑什么"组织。

### §13.1 注册路径

| 错误码 | 常见来源 | 怀疑什么 |
|---|---|---|
| `-EEXIST` | `register_netdev` 名字冲突 | `alloc_netdev` 时给的名字模板没带 `%d` |
| `-EINVAL` | `register_netdev` 参数检查 | 队列数为 0、名字非法、`netdev_ops` 为空 |
| `-ENOMEM` | 各处 | |
| `-EBUSY` | `register_netdev` | 设备已经注册过 |
| `-EPROBE_DEFER` | probe 里取时钟/PHY/regulator | 依赖的设备还没起来，核心会重试 |
| `-ENODEV` | `of_get_child_by_name` 之类 | DTS 里少了子节点 |

### §13.2 运行期

| 错误码 | 常见来源 | 怀疑什么 |
|---|---|---|
| `-EOPNOTSUPP` | ethtool 回调、`ndo_*` | 这个功能这个驱动没实现——**不是错误** |
| `-EINVAL` | `ndo_change_mtu`、`set_ringparam` 等 | 参数越界 |
| `-ERANGE` | `set_coalesce` 等 | 值超出硬件能力 |
| `-EADDRNOTAVAIL` | `eth_validate_addr` | MAC 地址非法（组播位或全零） |
| `-ENETDOWN` | 各处 | 设备没 up |
| `-EAGAIN` | 硬件忙 | |
| `-ETIMEDOUT` | 等硬件响应 | 复位没完成、固件没应答 |
| `-EIO` | 寄存器读回不对 | 时钟没开、复位没释放、地址映射错 |

### §13.3 两处特殊语义

⚠️ **`-EOPNOTSUPP` 在 ethtool 路径上是正常返回**。用户态 `ethtool` 会把它翻译成 `Operation not supported`，这只表示驱动没实现。把它当错误去排查会走弯路。

⚠️ **`ndo_set_features` 的返回值不遵守常规**：0 是成功，**正数被视为"静默错误"**，负数只会进 dmesg。它是本子系统里唯一一个"错误码传不出去"的回调。

### §13.4 发送路径没有错误码

`ndo_start_xmit` 的返回类型是 `netdev_tx_t`，只有两个值（§4.1）。**丢包不是通过错误码表达的**，而是通过 `dev_kfree_skb_any()` 加统计计数器（`tx_dropped`、`tx_errors`）表达。想让上层看到丢包原因，只能靠 ethtool 私有统计或者 drop_monitor（见 `07-debug.md` §7）。

---

## §14 读下一篇

| 想知道 | 去 |
|---|---|
| 这些结构体的字段逐个是什么 | `02-datastructures.md` |
| 这些函数内部怎么跑的、锁在哪 | `03-internals.md` |
| NAPI / GRO / XDP / page_pool 各自是什么框架 | `04-extensions.md` |
| 要写驱动，需要能抄的骨架 | `05-usage.md` |
| DTS 怎么写、哪个属性谁读 | `06-devicetree.md` |
| 网卡不通，怎么查 | `07-debug.md` |
| 为什么这么设计 | `08-feynman.md` |
| 上面每条论断的取证等级 | `99-evidence.md` |
