---
title: 总览
---

# 网卡驱动子系统总览

基线 `f6e38ae624cf`（Linux 6.1.158），arm64-only。本篇是整套文档的复习入口：隔几个月回来只读这一篇，应该能恢复全貌，需要细节时再按 §8 的导读跳到对应篇。

## §1 这个子系统是什么

「网卡驱动」在本文档集里指的是三块东西的合集：

| 范围 | 路径 | 规模 |
|---|---|---|
| 以太网驱动 | `drivers/net/ethernet/` | 2542 个 `.c`/`.h` 文件、2,319,156 行、93 个厂商目录 |
| 网络设备核心 | `net/core/dev.c` 及邻居、`net/ethernet/eth.c` | `dev.c` 183 个导出符号 |
| 驱动契约层 | `include/linux/netdevice.h`、`include/linux/etherdevice.h`、`include/linux/ethtool.h` | `net_device_ops` 86 个函数指针槽位、`ethtool_ops` 68 个 |

不在范围内的：协议栈本身（TCP/IP、netfilter、路由）、qdisc 调度算法（只讲和驱动接口相关的那一小块）、无线（`drivers/net/wireless/`）、虚拟网络设备（tun/tap/veth/bridge）。PHY/MDIO 侧（`drivers/net/phy/`、`drivers/net/mdio/`、`drivers/net/pcs/`）作为 MAC 驱动的必要邻居讲到，但不展开成独立体系。

### §1.1 子系统类型判定

按 skill 的两道门判定：

| 门 | 命令口径 | 结果 |
|---|---|---|
| 门 1：有没有自己的 `bus_type` | 在 `net/core/`、`include/linux/netdevice.h` 找 `struct bus_type` | **空** |
| 门 2：协议 ABI 在不在 uapi | `include/uapi/linux/` 下匹配 `^(if\|ethtool\|net)` 且以 `.h` 结尾的条目 | 48 个（另有 5 个 `netfilter*` 子目录，不计入） |

门 1 空 → 不是硬件总线型也不是虚拟总线型。结合门 2 的大量 uapi 头文件和「设备是逻辑的、路径是协议栈→驱动」这个形状，**判定为协议/网络型**。这个判定的直接后果：

- 本篇 §2 走**路径 B（问题背景）**，讲「要解决什么问题」而不是「硬件是怎么工作的」
- `02-datastructures.md` 画的是「核心抽象 ↔ 消费者」关系图，而不是「控制器总线 ↔ 设备总线」
- 网络接口注册到 `static struct class net_class`（`net/core/net-sysfs.c:1926`），不是注册到总线。`net_device` 里嵌了 `struct device dev;`（`include/linux/netdevice.h:2317`），**但它不会被绑定任何 driver**——详细的辨析在 `08-feynman.md` §2

唯一有 `bus_type` 的是 MDIO 那一侧：`struct bus_type mdio_bus_type`（`drivers/net/phy/mdio_bus.c:1045`）。所以同一块网卡上 PHY 侧是经典的「设备等驱动」模型、MAC 侧不是。

### §1.2 本树相对上游的改动

`drivers/net/ethernet/` 及邻居上有一批本地改动，性质是 **PREEMPT_RT 的一次回合并**：

| 文件 | 改动 | 影响 |
|---|---|---|
| `include/linux/u64_stats_sync.h` | 删掉 12 行 | `u64_stats_fetch_begin_irq()` / `u64_stats_fetch_retry_irq()` 不存在了，写这两个名字编译失败 |
| `drivers/net/ethernet/` 下 47 个文件 | 204 处插入 / 204 处删除 | 100% 一对一机械改名，把上面那对 `_irq` 变体换成不带后缀的版本 |
| `net/core/dev.c` | 34 插 / 11 删 | `trigger_rx_softirq()` 按 `#ifndef CONFIG_PREEMPT_RT` 拆成两个实现 |
| `include/linux/netdevice.h` | +4 行 | `struct softnet_data` 增加 `defer_csd` / `defer_work`（`:3202-3206`） |

⚠️ **移植上游驱动到本树时，唯一会撞上的编译错误来源就是那对 `_irq` 变体。** 详见 `01-api.md` §0.6、`02-datastructures.md` §8.3、`03-internals.md` §B7、`05-usage.md` §1.7。

## §2 问题背景：为什么网络设备层长成这个样子

这一节不讲硬件手册，讲的是「如果从零设计一个网卡驱动框架，你会依次撞上哪些问题」。理解了这些约束，后面所有看起来绕的设计都会变得显然。

### §2.1 网络设备和别的外设，量级上不是一回事

先看一个数字。1 Gbps 的以太网跑满小包（64 字节）时，每秒约 148 万个包。10 Gbps 是 1488 万。这意味着：

- 在 1 GHz 的 CPU 上，每个包能分到的时间预算是 **675 纳秒**（1 Gbps）或 **67 纳秒**（10 Gbps）
- 一次 cache miss 大约 100 纳秒。10 Gbps 小包场景下，**每个包只够两三次 cache miss**
- 一次中断的进出开销（保存现场、跳转、恢复）在数百纳秒量级。一个包一个中断在 1 Gbps 上就已经跑不动了

对比一下别的子系统：I2C 总线的典型速率是 100 kHz，一次寄存器读写几十微秒；SPI flash 读一页几十微秒；一个按键中断一秒钟最多几十次。这些子系统的框架可以放心地用互斥锁、可以为每次操作分配内存、可以在路径上做几次链表遍历。

**网络设备层的所有非常规设计，根源都是这个量级差。** 每个包的处理路径上：不能加互斥锁（要么无锁要么 per-CPU）、不能随意分配内存（要用池）、不能多做一次指针跳转（所以有大量 `static inline`）、不能每包一次中断。

### §2.2 第一个问题：中断太多

最朴素的驱动设计是「收到包 → 触发中断 → 中断处理函数把包交给协议栈」。这个设计在低速率下没问题，在高速率下会进入一个恶性循环：中断处理占满 CPU，导致协议栈来不及处理，导致队列积压，导致更多中断。极端情况下系统对用户完全无响应，这叫中断活锁。

解决思路是**把「什么时候干活」和「什么时候被通知」解耦**：第一个包触发中断，驱动立刻关掉网卡中断，然后在软中断上下文里连续处理攒下来的包，处理完了再打开中断。一次中断摊到 N 个包上。

这就是 NAPI。它带来的新问题是「一次处理多少」——处理太少浪费了摊销的机会，处理太多会饿死其它软中断和用户进程。于是需要预算机制：全局预算 `netdev_budget = 300`（`net/core/dev.c:4424`）、单设备权重 `dev_rx_weight = 64`（`:4430`）、还有时间上限 `netdev_budget_usecs`（`:4426`）。

预算用光时不能简单地「等下一个中断」——中断还是关着的，不会有下一个。所以 NAPI 实例要留在队列里等下一轮软中断，这个状态由返回值等于 budget 来表达。`/proc/net/softnet_stat` 的第三列 `time_squeeze` 数的就是这种情况。

⚠️ NAPI 引入了一个 NAPI 之前不存在的竞态：关中断和「再检查一次接收环」这两步之间到达的包，既不会触发中断（已关）也不在这轮 poll 的视野里（当时还没到）。**每个 NAPI 驱动都必须在关中断后重新检查环**，这是这一类驱动最经典的 bug 来源。

### §2.3 第二个问题：一个 CPU 不够

NAPI 把中断开销摊薄了，但收包处理本身还是串行的：一块网卡一个中断、一个 NAPI 实例、一个 CPU。1 Gbps 时代这够了，10 Gbps 以上不够。

硬件侧的答案是多队列：网卡内部按包头哈希把流量分到 N 个接收队列，每个队列独立中断。软件侧要跟着改三处：

1. `net_device` 不再有「一个」接收/发送队列，而是有队列数组，队列数在 `alloc_etherdev_mqs()` 时确定
2. 每条队列一个 NAPI 实例，绑到不同 CPU（靠中断亲和性）
3. 统计计数器要按队列分开，否则多核同时更新同一个计数器就成了新的瓶颈

第 3 点带出一个连锁问题：per-queue 计数器读的时候要加起来，而「加起来」不是原子的，所以汇总值不对应任何真实瞬间。这是可以接受的代价——统计的精确性换吞吐。

多队列的另一半是发送侧：包该进哪条发送队列？默认按 CPU 选（同一个 CPU 发的包走同一条队列，避免锁竞争），也可以用 XPS 显式指定映射（`/sys/class/net/<if>/queues/tx-N/xps_cpus`）。

硬件不支持多队列的时候，还可以在软件里模拟：RPS 把收到的包按哈希分发到别的 CPU 处理（`queues/rx-N/rps_cpus`）。它救不了中断开销，但能把协议栈处理摊开。

### §2.4 第三个问题：每个包的固定开销

即使解决了中断和并行度，每个包还有一堆固定开销：分配 skb、分配数据缓冲区、DMA 映射、计算校验和、协议头解析、释放。在小包场景下这些加起来就是全部成本。

框架层针对每一项都长出了对应机制：

| 开销 | 机制 | 关键接口 |
|---|---|---|
| skb 分配 | 用 NAPI 的 per-CPU 缓存 | `napi_alloc_skb()`（`include/linux/skbuff.h:3283`）、`napi_build_skb()`（`:1259`） |
| skb 释放 | 走 NAPI 的批量回收 | `napi_consume_skb()`（`include/linux/skbuff.h:3288`） |
| 数据页分配 + DMA 映射 | 页池，映射只做一次 | `page_pool_dev_alloc_pages()`（`include/net/page_pool.h:214`） |
| 校验和 | 硬件卸载 | `NETIF_F_*_CSUM` 特性位 |
| 协议头解析 | 硬件给出哈希和类型 | `skb_set_hash()`、`eth_type_trans()`（`include/linux/etherdevice.h:40`） |
| 每包的协议栈进入 | GRO 合并成大包 | `napi_gro_receive()` |

⚠️ **这些机制没有一个是必需的。** 一个只填了最少槽位的驱动照样能工作，只是慢。所以读驱动代码时会看到巨大的实现差异——同一件事有「朴素做法」和「优化做法」，两者在同一棵树里并存。`04-extensions.md` §0.2 给了 arm64 五个代表驱动分别用了哪几个机制的分诊表。

### §2.5 第四个问题：卸载让「包」这个概念变得模糊

硬件卸载不只是「帮 CPU 算校验和」这么简单。分段卸载（TSO/GSO）意味着**协议栈交给驱动的不是一个包，而是一段能被硬件切成多个包的数据**；GRO 意味着**驱动交给协议栈的不是一个包，而是多个包合起来的东西**。

于是「一个包」在不同层看到的数量不一样：

```
应用写 64 KB
    ↓  协议栈（TSO 开启）
交给驱动：1 个 skb，gso_size = 1448
    ↓  硬件分段
线上：45 个以太网帧
    ↓  对端网卡 + GRO
对端驱动交给协议栈：可能是 3 个 skb
    ↓
对端应用读到 64 KB
```

这带来一系列必须显式处理的后果：统计数字该数哪一层（`/proc/net/dev` 数的是驱动这一层）、抓包工具在不同位置看到的包数不同、MTU 校验在哪一层做、转发场景下收进来的 GRO 大包要重新 GSO 切开。

特性位（`include/linux/netdev_features.h` 里 63 个）就是这套卸载能力的开关矩阵。它们之间有依赖关系（比如 TSO 依赖校验和卸载），核心会做一致性修正——这就是为什么 `ethtool -K` 关掉一个特性会连带关掉另一个，也是为什么核心会打出 `netdev_WARN(dev, "failed to disable LRO!\n")`（`net/core/dev.c:1647`）这类「驱动不听话」的告警。

### §2.6 第五个问题：队列太深反而更慢

前面所有努力都在提吞吐。到某个点上会发现一个反直觉的现象：把队列做深、把批量做大，吞吐上去了，但交互延迟变得极差——一个 ping 包要等前面几千个大包发完。这就是 bufferbloat。

问题不在总缓冲量，在**缓冲在哪一层**。包一旦交给硬件发送环，qdisc 的优先级调度、公平队列就再也管不到它了——硬件是先进先出的。所以硬件环里堆得越多，qdisc 越无效。

答案是 BQL：限制「已交给硬件、未发完」的**字节数**（不是包数——同样的包数对应的传输时间可以差几十倍）。上限自适应调整，只要保证硬件不饿着就尽量压低。实现只有 138 行（`lib/dynamic_queue_limits.c`）。

⚠️ **BQL 的目的不是提吞吐，是让 qdisc 的调度真的有效。** 吞吐几乎不变，延迟大幅下降。这一点经常被误解成「BQL 是个性能优化开关」。

BQL 对驱动的要求是成对调用：发送时报字节数、完成回收时也报。⚠️ **漏了完成侧，BQL 会认为字节永远在飞、把上限压到底、吞吐掉到地板，而且不打任何日志**——`04-extensions.md` §12.2 里点出了本树中只有发送侧、缺完成侧配对的 6 个驱动文件。

### §2.7 第六个问题：硬件多样性在 MAC 之外

到这里讲的都是「包怎么进出」。但网卡还有另一半复杂度：物理层。

以太网的 MAC 和 PHY 在历史上就是两颗芯片，中间用一组并行/串行信号连接（MII、RMII、RGMII、SGMII、2500Base-X、10GBase-R……），另外用 MDIO 这条低速总线做管理。SoC 集成的是 MAC，PHY 通常还是外挂的。

这个分离给驱动框架带来的问题是：

- **PHY 是一个独立的设备，有自己的驱动。** 所以有 `mdio_bus_type`、有 `phy_driver`，是标准的设备-驱动 match 模型
- **链路状态是 PHY 先知道的。** 速率、双工、协商结果由 PHY 检测，然后通知 MAC 去配置自己。方向是 PHY → MAC，和「MAC 是主、PHY 是从」的直觉相反
- **MAC 和 PHY 之间的接口模式必须两边一致。** 这个信息硬件自己发现不了，只能由设备树告诉（`phy-mode` 属性，33 个合法取值，见 `06-devicetree.md` §3.1）
- **可插拔模块（SFP）让「PHY 是什么」变成运行期才知道的事**，甚至可能压根没有 PHY（直连光模块）

所以在 phylib（`phy_driver` 模型）之上又长出了 phylink：多一层状态机，管「MAC 能力 ∩ PHY 能力 ∩ 模块能力」的协商，以及「链路好但没有 PHY」这类状态。

⚠️ **arm64 上这件事已经一边倒了**：五个代表驱动里用 `phylink_create` 的 5 个、用 `phy_connect` 的 **0** 个。全树的数字（phylink 15 个文件 vs phylib 28 个）会让人以为 phylib 还是主流，**但在 arm64 SoC 这一侧它已经不是了**。

### §2.8 第七个问题：可观测性的历史包袱

网络子系统的用户态接口经历了三代，而且**三代全部还在**：

| 代 | 形态 | 现状 |
|---|---|---|
| 一代 | `SIOCGIFxxx` 系列 ioctl | 仍在用（`ifconfig` 走这条），能力有限 |
| 二代 | `SIOCETHTOOL` ioctl + 一个大 switch | 仍是主力，`net/ethtool/ioctl.c` 3344 行 |
| 三代 | netlink（`ethtool` netlink family + rtnetlink + devlink） | 新功能只加在这里，`net/ethtool/netlink.c` 1079 行 |

统计数据同样有三套口径，互不相同（详见 `07-debug.md` §7.4）：`/proc/net/dev` 的标准字段、`ethtool -S` 的**完全由驱动自定义**的字段、以及新加的标准化统计组。⚠️ **`ethtool -S` 里同名字段在两个厂商的网卡上可能是完全不同的东西**，跨机器对比数字之前必须确认是同一个驱动。

调试日志也有历史分层：`netdev_*` 一族无条件输出，`netif_*` 一族要过 `msg_enable` 位（15 个位，`include/linux/netdevice.h:4279-4298`），`netdev_dbg` 还要过 `dynamic_debug`。⚠️ **这一族只有 `_once` 变体，没有 `_ratelimited` 变体**（`include/net/net_debug.h` 全文 157 行里 `_ratelimited` 出现 0 次）。

### §2.9 第八个问题：设备拆不掉

最后一个问题和数据路径无关，但它是网络子系统最难缠的一类故障。

`net_device` 被大量对象引用：路由表项、邻居表项、套接字、qdisc、上层虚拟设备（bridge/bond/vlan）、netfilter 规则。拆除一个接口要等所有引用释放，而这些引用散在整个协议栈里，有的还挂在 RCU 宽限期后面。

现象是 dmesg 里反复刷 `unregister_netdevice: waiting for <if> to become free`，然后卡住。核心自己也知道这里容易出问题，所以 `net_device` 的注册状态被编进了日志——`netdev_reg_state()`（`include/linux/netdevice.h:5178`）会在接口名后面追加 `" (unregistering)"` 这类后缀，`netdev_WARN`（`:5201`）把它带上。看到 `(unregistering)` 就说明有代码在拆除之后还在碰这个设备。

排查手段是 `CONFIG_NET_DEV_REFCNT_TRACKER`（`net/Kconfig.debug:3`）：记录每次 `dev_hold`/`dev_put` 的栈，超时时把还持有引用的栈全打出来。⚠️ **不开这个选项就只有那句反复刷屏的等待消息，看不出谁在持有。**

### §2.10 第九个问题：有人根本不想经过协议栈

前面所有机制都在优化「包进协议栈」这条路。但有一类需求是「这个包我看一眼就决定丢掉/转发/交给用户态，别让它进协议栈」——负载均衡、DDoS 过滤、简单转发。对这类需求来说，协议栈的每一层都是纯开销。

历史上的答案是绕过内核（用户态驱动直接接管网卡），代价是这块网卡对内核就消失了：没有 `ip`、没有 `tcpdump`、没有防火墙。内核侧的答案是 XDP：**在驱动收到包、还没构造 skb 之前**跑一段 eBPF 程序，程序返回「丢」「过」「重定向」「送用户态」。

对驱动框架的要求是三处新契约：

- 驱动要在收包路径的最前面留一个挂载点，且此时还没有 skb（操作的是裸的页 + 长度）
- 要提供 `ndo_bpf`（`include/linux/netdevice.h:1606`）让程序挂上来、`ndo_xdp_xmit`（`:1608`）支持重定向发送
- 收包内存的所有权模型要变：页可能被 XDP 保留、可能被重定向到别的设备，所以 page_pool 和 XDP 的内存模型是绑在一起的

⚠️ **XDP 不是一个能后加的开关，而是一次收包路径重写。** 全树填了 `.ndo_bpf` 的有 33 个驱动文件、`xdp_rxq_info_reg` 30 个、`.ndo_xdp_xmit` 26 个、`.ndo_xsk_wakeup`（AF_XDP 零拷贝）只有 7 个（口径见 `04-extensions.md` §12.2）——**支持程度是分级的，看到「支持 XDP」不代表支持零拷贝**。

### §2.11 第十个问题：智能网卡的状态在固件里

高端网卡（主要是 mlx5、nfp 这一类）内部跑着自己的固件，有自己的资源分配、自己的错误状态、自己的固件版本和升级流程。这些东西 `net_device` 这个抽象装不下——它是「一个网络接口」的抽象，而一块智能网卡上可能有多个接口、多个 PF/VF、还有交换逻辑。

于是有了 devlink：一条独立于 `net_device` 的 netlink 通道，专门管「设备级」而不是「接口级」的事情——固件版本、参数配置、端口划分、资源限额、健康状态上报。它的规模说明了这一层有多重：`net/devlink/leftover.c` 有 **12530** 行，`struct devlink_ops`（`include/net/devlink.h:1218`）加上邻居结构合计 71 个函数指针。

其中和排障最相关的是 health reporter（`struct devlink_health_reporter_ops`，`include/net/devlink.h:692-704`）：驱动可以注册「发送队列健康」「固件健康」这类报告器，提供 `diagnose`、`dump`、`recover` 甚至 `test` 回调。

⚠️ **这一整套是可选的，而且用得很少。** 全树 `devlink_alloc` 21 到 25 个文件（两种口径）、`devlink_port_register` 只有 8 个。arm64 五个代表驱动里只有 `ti/am65-cpsw-nuss.c` 用了 devlink，而且没有 health reporter。**`devlink health show` 没输出通常意味着驱动没实现，不是命令用错。**

### §2.12 这十个问题和代码的对应关系

| 问题 | 机制 | 主要落点 |
|---|---|---|
| 中断太多（§2.2） | NAPI | `net/core/dev.c`、`include/linux/netdevice.h` |
| 一个 CPU 不够（§2.3） | 多队列、RPS/XPS、per-queue 统计 | `net/core/dev.c`、`net/core/net-sysfs.c` |
| 每包固定开销（§2.4） | page_pool、napi skb 缓存、GRO | `net/core/page_pool.c`（938 行）、`net/core/gro.c`（817 行） |
| 卸载模糊了「包」（§2.5） | 特性位、GSO/GRO | `include/linux/netdev_features.h`（63 个位，其中 62 个在 `ethtool -k` 里有名字） |
| 队列太深（§2.6） | BQL | `lib/dynamic_queue_limits.c`（138 行） |
| 物理层多样性（§2.7） | phylib / phylink / MDIO / SFP | `drivers/net/phy/`（85 文件 / 59610 行） |
| 可观测性包袱（§2.8） | ioctl + ethtool + netlink 三代并存 | `net/ethtool/`（26 个 `.c` / 11354 行） |
| 设备拆不掉（§2.9） | 引用计数 + 追踪器 | `net/core/dev.c`、`net/Kconfig.debug` |
| 绕过协议栈（§2.10） | XDP、AF_XDP | `net/core/xdp.c`（707 行）、`net/xdp/`（3504 行） |
| 固件状态（§2.11） | devlink | `net/devlink/leftover.c`（12530 行） |

**读驱动代码时按这张表定位：任何一段看不懂的逻辑，先问它在解决上面哪个问题。** 十个问题之外的代码基本只有两类——厂商硬件的寄存器操作，和历史兼容。

## §3 概念模型

### §3.1 四个层次

```
   ┌─────────────────────────────────────────────────────┐
   │  用户态                                              │
   │  ip / ethtool / devlink / tc      socket 收发         │
   └──────┬──────────────────────────────────┬───────────┘
          │ netlink + ioctl（控制面）          │ 数据面
   ┌──────┴──────────────────────────────────┴───────────┐
   │  协议栈（本文档集不展开）                              │
   │  TCP/UDP · IP · netfilter · 路由 · qdisc              │
   └──────┬──────────────────────────────────┬───────────┘
          │ ndo_start_xmit / netif_receive_skb            │
   ┌──────┴──────────────────────────────────┴───────────┐
   │  网络设备核心 —— 本文档集的主体                        │
   │  net_device + net_device_ops + ethtool_ops           │
   │  NAPI · 多队列 · 特性位 · BQL · GRO · page_pool · XDP  │
   │  net/core/dev.c · net/ethernet/eth.c · net/ethtool/  │
   └──────┬──────────────────────────────────┬───────────┘
          │ 驱动填的函数指针                    │ MDIO
   ┌──────┴───────────────────┐   ┌──────────┴───────────┐
   │  MAC 驱动                 │   │  PHY 侧               │
   │  drivers/net/ethernet/    │   │  phylib / phylink     │
   │  93 个厂商目录             │   │  drivers/net/phy/     │
   └──────┬───────────────────┘   └──────────┬───────────┘
          │ 寄存器 + DMA                      │ MDIO 读写
   ┌──────┴──────────────────────────────────┴───────────┐
   │  硬件：MAC 控制器 ── SerDes ── PHY / SFP ── 线          │
   └─────────────────────────────────────────────────────┘
```

关键在于**中间那一层是「被填空」的**：核心定义了 86 个 `net_device_ops` 槽位和 68 个 `ethtool_ops` 槽位，驱动填一部分。填得越多，用户态能用的功能越多。所以「这块网卡支持不支持某个功能」这个问题，答案几乎总是「看驱动填了没有」。

### §3.2 三个方向的控制流

网络设备层的调用方向有三个，混淆它们是读代码时最常见的迷路原因：

| 方向 | 谁调谁 | 典型入口 | 上下文 |
|---|---|---|---|
| 下行（发送、配置） | 核心 → 驱动 | `ndo_start_xmit`、`ndo_open`、`ethtool_ops` 各槽位 | 进程上下文或软中断 |
| 上行（收包、事件） | 驱动 → 核心 | `napi_gro_receive()`、`netif_carrier_on()` | 软中断（收包）或工作队列（事件） |
| 旁路（物理层通知） | PHY → MAC 驱动 | `phylink_mac_ops` 的 `mac_link_up` 等回调 | PHY 的工作队列或中断 |

⚠️ **第三个方向最容易被忽略。** `phylink_mac_ops`（`include/linux/phylink.h:160-179`）里那些回调是「MAC 提供给 phylink 调用的」，方向和名字给人的第一印象相反。而且它们不在 `ndo_open` 的调用栈上，所以不能假设任何 `ndo_*` 层面的锁状态。

### §3.3 一个包的三种身份

同一段数据在三个地方有三种表示，`02-datastructures.md` §1.0 详细辨析，这里只记形状：

| 身份 | 表示 | 谁拥有 |
|---|---|---|
| 线上的帧 | 字节流 | 硬件 |
| 环里的一项 | 描述符（厂商自定义结构） | 驱动 + 硬件共享，靠 DMA 一致性协议 |
| 内核里的包 | `struct sk_buff` + 若干页 | 谁持有引用谁拥有 |

⚠️ **驱动的核心工作就是在这三种表示之间转换，而错误几乎全部出在「所有权什么时候转移」上。** 典型例子：`ndo_start_xmit` 返回 `NETDEV_TX_BUSY` 时 skb 的所有权**不**转移（`08-feynman.md` §7），page_pool 的页要打 `skb_mark_for_recycle()` 标记才会还给池子（`08-feynman.md` §5）。

## §4 数据结构速览

完整字段表在 `02-datastructures.md`，这里只列「记住这几个就能读代码」的部分。

| 结构 | 定义 | 规模 | 一句话 |
|---|---|---|---|
| `struct net_device` | `include/linux/netdevice.h` | 约 167 个字段 | 一个网络接口的全部状态。里面嵌了 `struct device dev`（`:2317`） |
| `struct net_device_ops` | `include/linux/netdevice.h` | 86 个函数指针 | 驱动实现的数据面 + 配置面契约 |
| `struct ethtool_ops` | `include/linux/ethtool.h:640` | 68 个函数指针 | 驱动实现的诊断/配置契约 |
| `struct sk_buff` | `include/linux/skbuff.h` | —— | 一个包。分片数组上限 `MAX_SKB_FRAGS`（`:358`/`:360`） |
| `struct napi_struct` | `include/linux/netdevice.h` | —— | 一个轮询实例，通常一条队列一个 |
| `struct softnet_data` | `include/linux/netdevice.h` | —— | per-CPU 的软中断状态。本树加了 `defer_csd`/`defer_work`（`:3202-3206`） |
| `struct netdev_queue` | `include/linux/netdevice.h` | —— | 一条发送队列。`trans_timeout`（`:624`）是看门狗计数 |
| `struct page_pool_params` | `include/net/page_pool.h:74-85` | —— | 页池的配置，`PP_FLAG_*` 在 `:37-49` |
| `struct phylink_config` | `include/linux/phylink.h:133-144` | —— | MAC 向 phylink 声明自己的能力 |
| `struct phylink_mac_ops` | `include/linux/phylink.h:160-179` | —— | phylink 回调 MAC 的接口 |
| `struct phy_driver` | `include/linux/phy.h:784` | —— | PHY 驱动，被 `mdio_bus_type` match |
| `struct mii_bus` | `include/linux/phy.h:358` | —— | 一条 MDIO 总线 |
| `struct devlink_ops` | `include/net/devlink.h:1218` | 头文件内合计 71 个函数指针 | 设备级（非接口级）控制面 |

### §4.1 实例数量：契约用得有多广

| 结构 | 在 `drivers/net/ethernet/` 里的实例数 |
|---|---|
| `net_device_ops` | 284 |
| `ethtool_ops` | 237 |
| `phy_driver` 数组（在 `drivers/net/phy/`） | 47 |

⚠️ **`net_device_ops` 284 个实例 vs `ethtool_ops` 237 个**：差出来的那些是根本没提供 ethtool 支持的驱动。这个差值本身就是一条有用的信息——`ethtool <if>` 完全失败的情况是真实存在的，不一定是命令用错。

### §4.2 关系图

```
       platform_device / pci_dev              ← 真正被 probe 的设备
                 │  dev.parent
                 ▼
          struct net_device ──────────────► net_class（/sys/class/net/eth0）
           │        │        │                （不是 bus_type，不绑 driver）
           │        │        └─► netdev_queue[] ──► BQL 状态（per-queue）
           │        └──────────► napi_struct[]  ──► poll 回调
           ├── net_device_ops   （驱动填 86 个槽位的一部分）
           ├── ethtool_ops      （驱动填 68 个槽位的一部分）
           ├── phylink / phy_device ──► mii_bus ──► mdio_bus_type
           └── page_pool（可选） ──► 预映射的页

       sk_buff ──► 页（可能来自 page_pool，要打 recycle 标记）
```

**这张图里唯一的 `bus_type` 在右下角的 MDIO 那一侧。** 详细的辨析在 `02-datastructures.md` §1.0 和 `08-feynman.md` §2。

## §5 机制速览

每个机制一行，展开在 `04-extensions.md`。「用得广不广」一列是本树 `drivers/net/ethernet/` 里的文件数（口径见 `04-extensions.md` §12.2），它比「这个机制好不好」更能说明你读驱动时会不会碰到它。

| 机制 | 解决什么 | 驱动侧入口 | 用得广不广 |
|---|---|---|---|
| NAPI | 中断太多 | `napi_gro_receive()` | 93 个文件（`napi_gro_frags` 另 17 个） |
| 多队列 | 单核瓶颈 | `alloc_etherdev_mqs()`（`include/linux/etherdevice.h:56`） | 几乎全部 |
| 特性位与卸载 | 每包开销 | `dev->features` / `ndo_set_features`（`include/linux/netdevice.h:1538`） | 全部 |
| BQL | bufferbloat | `netdev_tx_sent_queue()` / `netdev_tx_completed_queue()` | 发送侧并集 44、完成侧 42 |
| page_pool | DMA 映射开销 | `page_pool_create()` | 12–13 个 |
| XDP | 绕过协议栈 | `ndo_bpf`（`include/linux/netdevice.h:1606`） | 33 个 |
| AF_XDP 零拷贝 | 用户态直收 | `ndo_xsk_wakeup`（`include/linux/netdevice.h:1613`） | 7 个 |
| TC 卸载 | 把 tc 规则下到硬件 | `ndo_setup_tc`（`include/linux/netdevice.h:1493`） | 49 个 |
| devlink | 设备级控制面 | `devlink_alloc()`（`include/net/devlink.h:1554`，inline 版 `:1557`） | 21–25 个 |
| phylink | 物理层协商 | `phylink_create()`（`drivers/net/phy/phylink.c:1450`） | 15 个 |
| phylib | 物理层（老模型） | `phy_connect()` | 28 个 |
| netpoll | 崩溃时也能收发 | `ndo_poll_controller`（`include/linux/netdevice.h:1452`） | 108 个 |
| 看门狗 | 发送卡死检测 | `ndo_tx_timeout` | 填了才有看门狗 |

⚠️ **`ndo_poll_controller` 的 108 个文件是这张表里最容易误读的数字。** 它多不代表 netpoll 重要，而是因为这个槽位历史很久、实现简单，很多驱动顺手填了。反过来 `page_pool` 只有 12–13 个不代表它不重要——它是新机制，而且 arm64 的五个代表驱动里有四个用了。**看这一列要结合「这个机制是哪年进内核的」，纯看绝对数会得出反向结论。**

## §6 端到端调用路径

四条路径，`03-internals.md` Part A 有逐段剖析，这里给骨架。

### §6.1 注册（驱动 probe → 接口出现）

```
platform_driver / pci_driver 的 .probe
   │
   ├─ 1. 分配 net_device
   │     devm_alloc_etherdev_mqs()   include/linux/etherdevice.h:61
   │     （非 devm 版 alloc_etherdev_mqs() 在 :56，单队列版 alloc_etherdev() 在 :58）
   │
   ├─ 2. 填契约
   │     dev->netdev_ops   = &xxx_netdev_ops;     （86 个槽位里填一部分）
   │     dev->ethtool_ops  = &xxx_ethtool_ops;    （68 个槽位里填一部分）
   │     dev->features / hw_features                （卸载能力声明）
   │     dev->watchdog_timeo                        （不填就用核心默认）
   │
   ├─ 3. 拿 MAC 地址（四条路径，见 06 篇 §2.4 的决策树）
   │     of_get_ethdev_address()  include/linux/of_net.h:17
   │       └─ 失败 → nvmem → 读寄存器 → eth_hw_addr_random()
   │                                    include/linux/etherdevice.h:268
   │
   ├─ 4. 物理层
   │     phylink_create()          drivers/net/phy/phylink.c:1450
   │     phylink_of_phy_connect()  drivers/net/phy/phylink.c:1763
   │     （老模型：of_mdiobus_register() + phy_connect()）
   │
   ├─ 5. 注册 NAPI 实例（每条队列一个）
   │
   └─ 6. register_netdev() / devm_register_netdev()
             include/linux/netdevice.h:4543
         └─ 此刻 /sys/class/net/<if> 出现，用户态可见
```

⚠️ **第 6 步之前接口不存在，第 6 步之后 `ndo_open` 随时可能被调用。** 所以第 2–5 步里所有会被 `ndo_open` 用到的东西必须在第 6 步之前就绪。这是驱动 probe 里最常见的顺序错误。

### §6.2 收包

```
硬件写 DMA + 触发中断
   │
   ├─ 驱动中断处理：关网卡中断 → __napi_schedule()
   │                            include/linux/netdevice.h:458
   ├─ ⚠️ 关中断后必须重新检查环（NAPI 的经典竞态，见 08 篇 §1.3）
   │
   └─ 软中断上下文：驱动的 poll 回调
         │
         ├─ 从环里取描述符，页来自 page_pool 或普通分配
         ├─ 构造 skb：napi_build_skb()   include/linux/skbuff.h:1259
         │            或 napi_alloc_skb() include/linux/skbuff.h:3283
         ├─ 打 recycle 标记（用 page_pool 时必须）
         │            skb_mark_for_recycle()  include/linux/skbuff.h:5127
         ├─ 解析二层头：eth_type_trans()  include/linux/etherdevice.h:40
         ├─ XDP 挂载点（如果支持，在构造 skb 之前）
         │
         ├─ napi_gro_receive()  ──► GRO 合并 ──► 协议栈
         │     tracepoint 在 net/core/gro.c:654 / :659
         │
         └─ work < budget ? napi_complete_done()（开中断）: 留在队列等下一轮
```

### §6.3 发包

```
协议栈 → qdisc（可能排队，返回 NET_XMIT_*）
   │
   └─ 核心选队列（XPS 或按 CPU）→ 调 ndo_start_xmit
         │
         ├─ 驱动填描述符、DMA 映射
         ├─ netdev_tx_sent_queue()   ──► BQL 记账
         ├─ 环快满 → netif_stop_queue()（比返回 NETDEV_TX_BUSY 好）
         └─ 返回 NETDEV_TX_OK（include/linux/netdevice.h:122）
              或 NETDEV_TX_BUSY（:123，skb 所有权不转移）
   │
   ├─ 硬件发送 → 完成中断
   │
   └─ 完成处理（通常在同一个 poll 回调里）
         ├─ 解 DMA 映射、napi_consume_skb()  include/linux/skbuff.h:3288
         ├─ netdev_tx_completed_queue()  ──► BQL 记账（⚠️ 漏这一步吞吐塌）
         └─ 队列有空间 → netif_wake_queue()
   │
   └─ 超时没完成 → dev_watchdog()  net/sched/sch_generic.c:495
         ├─ trans_timeout++            :525
         ├─ trace_net_dev_xmit_timeout :533
         ├─ netdev_crit("NETDEV WATCHDOG: ...")  :534
         └─ ndo_tx_timeout()           :538
```

⚠️ **看门狗只在驱动填了 `ndo_tx_timeout` 时才装定时器**（`net/sched/sch_generic.c:555` 的那个 `if`）。没填的驱动发送卡死时**一句日志都不打**。

### §6.4 拆除

```
rmmod / 设备移除 → driver 的 .remove
   │
   ├─ unregister_netdev()
   │     ├─ 如果接口还 up：先走 ndo_stop
   │     ├─ 通知链广播 NETDEV_UNREGISTER
   │     └─ 等所有引用释放
   │           ⚠️ 卡在这里 → dmesg 刷 "waiting for <if> to become free"
   │           排查靠 CONFIG_NET_DEV_REFCNT_TRACKER（net/Kconfig.debug:3）
   │
   ├─ phylink_destroy()   drivers/net/phy/phylink.c:1545
   ├─ page_pool 销毁（⚠️ 页没全还回来会一直等）
   └─ free_netdev()（devm 版本自动）
```

**四条路径里，注册和拆除是「一次性、出错就完全不工作」，收发是「每包都跑、出错表现为性能或偶发故障」。** 排查时先按这个二分：接口压根没出来 → 看 §6.1；接口在但不通 → 看 §6.2/§6.3；拆不掉 → 看 §6.4。

## §7 和其它子系统的关系

| 子系统 | 关系 | 接触点 |
|---|---|---|
| driver core / platform | MAC 控制器是 `platform_device` 或 `pci_dev`，probe 在那一层发生 | `dev.parent`、`platform_driver` |
| PCI | 独立网卡的发现路径，**完全不经过设备树** | `pci_driver`、`06-devicetree.md` §0.1 |
| 设备树（OF） | SoC 内置 MAC 的发现和配置：MAC 地址、`phy-mode`、`phy-handle` | `06-devicetree.md` 全篇 |
| MDIO / PHY | 物理层管理，是**唯一有 `bus_type` 的一侧** | `drivers/net/phy/mdio_bus.c:1045` |
| DMA-mapping / IOMMU | 收发缓冲区都要 DMA 映射；arm64 上普遍走 SMMU，映射开销是 page_pool 存在的理由 | `dma_map_page()`、`PP_FLAG_DMA_MAP` |
| 中断子系统 | 每条队列一个中断，亲和性决定哪个 CPU 收包 | `request_irq()`、`/proc/irq/*/smp_affinity` |
| 软中断 / NAPI | 收包处理跑在 `NET_RX_SOFTIRQ` 上 | `net/core/dev.c`、`softnet_data` |
| eBPF | XDP 程序、TC 卸载 | `ndo_bpf`（`include/linux/netdevice.h:1606`） |
| netlink | 三代用户态接口里最新的一代，也是 devlink 的载体 | `net/ethtool/netlink.c`、`net/devlink/` |
| 内存管理 | skb 和数据页的分配；page_pool 是它上面的一层缓存 | `net/core/page_pool.c` |
| PREEMPT_RT | 本树打了回合并补丁，影响 `u64_stats_sync` 和 `softnet_data` | §1.2 |
| 时钟 / 复位 / pinctrl / 稳压器 | SoC MAC 驱动 probe 时的常规依赖 | 各自的 `devm_*_get()` |

⚠️ **两条关系容易被忽略。** 一是 **IOMMU**：arm64 SoC 上 SMMU 的存在直接决定了 page_pool 有没有收益，同一份驱动代码在有/无 SMMU 的平台上性能差别可以很大。二是 **中断亲和性**：多队列网卡的性能瓶颈经常不在驱动而在中断分布，「CPU0 软中断打满」这个现象的根因通常在 `/proc/irq/` 而不在网络代码里。

## §8 导读

### §8.1 按你想做什么

| 想做什么 | 从哪篇开始 | 具体入口 |
|---|---|---|
| 第一次接触，想先有个框架 | 本篇 | §2 十个问题 → §3 概念模型 |
| 查一个函数怎么用 | `01-api.md` | §0 代码地图 |
| 搞清某个结构体的字段 | `02-datastructures.md` | §1.0 先分清几组关系 |
| 跟一遍完整流程 | `03-internals.md` | Part A 时间线 |
| 了解某个机制（page_pool / XDP / BQL / devlink） | `04-extensions.md` | 一节一个框架，§0.2 有 arm64 分诊表 |
| 动手写一个驱动 | `05-usage.md` | 按角色分节，篇首有「骨架未编译」声明 |
| 写 DTS 或排查 DTS 问题 | `06-devicetree.md` | §0.1 发现路径分诊表 → §1.1 属性总表 |
| 现场排查一个故障 | `07-debug.md` | §0.1 按症状找工具 → §14 一页速查 |
| 觉得某个概念别扭 | `08-feynman.md` | 八个主题，§2 讲最容易类比错的那组关系 |
| 核对任何一个数字或行号 | `99-evidence.md` | 逐篇取证表 |

### §8.2 建议的阅读顺序

第一次读：**00（本篇）→ 08 → 02 → 03**。先拿形状（00）、再破除直觉误区（08）、然后看数据结构（02）、最后跟流程（03）。反过来先读 03 容易被细节淹没。

要写驱动：**05 → 01 → 04 → 06**。骨架、API、可选机制、设备树。

要排故障：**07 → 06**。先按症状找工具，定位到配置问题再翻设备树篇。

### §8.3 三条贯穿全套文档的注意事项

- **`ethtool_ops` 和 `net_device_ops` 都是「填一部分」的契约。** 「这块网卡支持不支持 X」这个问题的答案几乎总是「看驱动填了没有」，不是「看内核支持不支持」。
- **全树的使用计数不等于 arm64 上的普及度。** phylink vs phylib 是最明显的例子：全树 15 vs 28，arm64 五个代表驱动 5 vs 0。凡是看到本文档集里的计数，都要看一眼旁边的口径说明。
- **本树打了 PREEMPT_RT 回合并补丁**（§1.2），移植上游驱动会撞上 `u64_stats_fetch_begin_irq()` 不存在的编译错误。这是唯一一处本树特有的坑。
