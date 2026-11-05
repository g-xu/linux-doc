---
title: 取证附录
---

# 网卡驱动文档集取证附录

本篇不讲机制，只回答一个问题：**前面九篇里的每句话，凭什么这么说。**

读法有三种。想核对一个具体数字或行号，直接跳 §3 的逐篇取证表。想知道哪些结论可以直接拿去用、哪些要自己再确认一遍，读 §2 的证据分层和 §5 的未验证项。想复核整套文档，读 §7。

## §1 基线

### §1.1 树的状态

```
$ git log -1 --oneline
f6e38ae624cf Linux 6.1.158

$ git status --porcelain | wc -l
178
```

⚠️ **这棵树不干净**：178 个文件相对上游 6.1.158 有改动。所以第一件必须查清的事是——**本子系统被改了没有**。答案是被改了，而且改动落在最核心的位置上。下一小节逐条摊开。

### §1.2 本子系统被动过哪些地方

```bash
git status --porcelain -- drivers/net net/core include/linux/netdevice.h include/linux/u64_stats_sync.h
```

四组，全部属于同一批 PREEMPT_RT 回合并：

| 位置 | 改动量 | 性质 |
|---|---|---|
| `drivers/net/ethernet/` 47 个文件 | +204 / -204 | 机械改名，一对一 |
| `drivers/net/` 非 ethernet 16 个文件 | 同类改名 | 在本文档集声明范围之外（虚拟/隧道设备） |
| `net/core/` 4 个文件 | 见下表 | 有真实语义改动 |
| `include/linux/` 2 个头文件 | +4 / -12 | 删了 API，加了字段 |

`git diff --stat` 的原始输出：

```
 include/linux/netdevice.h      |  4 ++++
 include/linux/u64_stats_sync.h | 12 ------------
 net/core/dev.c                 | 41 ++++++++++++++++++++++++++++++-----------
 net/core/drop_monitor.c        |  8 ++++----
 net/core/gen_stats.c           | 16 ++++++++--------
 net/core/skbuff.c              |  7 ++++++-
 6 files changed, 52 insertions(+), 36 deletions(-)
```

`drivers/net/ethernet/` 的汇总（`git diff --shortstat`）：

```
 47 files changed, 204 insertions(+), 204 deletions(-)
```

**插入数等于删除数**，这是「机械改名」的机器可验证证据——不是我读出来的印象。

### §1.3 这批改动拆成两组，性质完全不同

这个区分很重要，因为它决定读者会撞上什么样的问题：

**第一组：纯改名。** `include/linux/u64_stats_sync.h` 删掉的 12 行是 `u64_stats_fetch_begin_irq()` 和 `u64_stats_fetch_retry_irq()` 这对带 `_irq` 后缀的变体；47 个以太网驱动文件里的 204 处改动就是把这两个名字换成不带后缀的版本。后果只有一个——**在本树上写这两个函数名会编译失败**。这是移植上游驱动过来时唯一会撞上的编译错误来源。

验证方式（全树残留为 0）：

```bash
rg -c 'u64_stats_(fetch_begin|fetch_retry)_irq' -g '*.[ch]' . | wc -l
# → 0
```

**第二组：改时序。** `net/core/dev.c` 的 41 行改动里，实质内容是给 `struct softnet_data` 加了 `defer_csd`/`defer_work` 两条路径（字段声明在 `include/linux/netdevice.h:3202-3206`，就是那 +4 行），并把 `trigger_rx_softirq()` 做成 `CONFIG_PREEMPT_RT` 双实现。非 RT 配置下走 IPI（`defer_csd`），RT 配置下走 workqueue（`defer_work`）。这一组**不是改名，会改变 skb 延迟释放的执行上下文**。

⚠️ 两组的区别写在 `01-api.md:121`，展开在 `03-internals.md` §B7，对驱动的影响在 `05-usage.md` §1.7。**只看 diffstat 会把两组混成一件事**——都在同一批补丁里，插入删除数看着都很小。

### §1.4 子系统类型判定

按 skill 的两道门跑：

```bash
# 门 1：有没有自己的 bus_type
rg -n 'struct bus_type' net/core/*.c include/linux/netdevice.h
# → 空
```

**门 1 无输出。** 全树没有 `net_bus_type`。网络设备注册的是 class 而不是 bus：

```bash
rg -n 'struct class net_class' net/core/net-sysfs.c
# → 1926:static struct class net_class __ro_after_init = {
```

门 1 空的时候 skill 不要求跑门 2，但这里还是跑了一遍当辅助证据（`00-overview.md` §1.1 那张表的第二行）：`include/uapi/linux/` 下 48 个网络头文件，说明用户态 ABI 面积很大——这是「协议型」而不是「框架型」的正面证据。

结论：**协议/网络型**（不是硬件总线型，也不是虚拟总线型）。

这个判定改变了四篇的走法：

| 篇 | 因为是协议型而变成什么 |
|---|---|
| `00-overview.md` §2 | 走路径 B（问题背景），不是硬件背景 |
| `02-datastructures.md` | §1.0 第一组关系就是破「网卡注册在网络总线上」这个错误类比；关系图画的是「父总线 ↔ net_device」而不是两条总线 |
| `05-usage.md` | 角色按协议层枚举（收包侧/发包侧/控制面/链路层），不按硬件层 |
| 本篇 §5.5 | **不适用**（见 §5.5） |

`08-feynman.md` §2 整节就是这个判定的展开——它是本子系统最容易被类比错的一组关系。

### §1.5 架构范围

**arm64-only**，这个决定在动笔之前就定死了，不是写完再删。裁掉了什么记在 §2.7。

代表驱动五个，全部是 arm64 SoC 上真实在用的（选法见 §2.2）：am65-cpsw / mtk_eth / mvneta / mvpp2 / stmmac。

## §2 证据分层

三个档次的判据：

- **一等**：逐行读过。文档里对这些文件的任何论断都可以直接用
- **二等**：详读了部分行，其余只看符号表（函数签名、结构体字段名）。⚠️ 只看符号表的部分，行为是**推断**的
- **三等**：`Documentation/` 下的官方文档。它给设计意图，但**可能落后于代码**；冲突处以代码为准

### §2.1 一等证据（逐行读过的代码）

| 文件 | 行数 | 读了的什么 |
|------|------|----------|
| `include/linux/netdev_features.h` | 264 | 全文。63 个 feature 位的定义、掩码宏、`NETDEV_FEATURE_COUNT` |
| `include/linux/u64_stats_sync.h` | 217 | 全文 + 与上游的 diff |
| `include/linux/dynamic_queue_limits.h` | 108 | 全文 |
| `lib/dynamic_queue_limits.c` | 138 | 全文。BQL 的完整反馈算法 |
| `net/devres.c` | 95 | 全文。devm 版本的 alloc/register，含 kerneldoc |
| `net/core/link_watch.c` | 280 | 全文。链路状态变化的延迟上报机制 |
| `net/ethtool/common.c` | 599 | 全文。`netdev_features_strings[]`、`netif_msg_class_names[]`、公共 getter |
| `net/core/gro.c` | 817 | 全文 |
| `net/core/page_pool.c` | 938 | 全文 |
| `include/net/page_pool.h` | 386 | 全文 |

一等证据合计 3843 行。

### §2.2 二等证据（详读 + 符号表）

| 文件 | 行数 | 详读了哪些行 | 只看符号表的部分 |
|------|------|------------|----------------|
| `include/linux/netdevice.h` | 5242 | `:99-129` 返回值编码；`:458` `__napi_schedule`；`:1757-1762` 设计债注释；`net_device_ops` 全部 86 个指针的声明；`struct net_device` 主体；`:2317` 内嵌 `struct device`；`:3202-3206` RT 新字段；`:5156` TODO | 大量 `static inline` 辅助函数的函数体、`netdev_*_dbg` 宏族的展开 |
| `net/core/dev.c` | 11617 | `free_netdev()` `:10846-10894`；`register_netdevice()`/`unregister_netdevice_many()` 主体；`net_dev_init()`；`trigger_rx_softirq()`；`skb_defer_free_flush()`；budget 相关变量 `:4424-4430` | qdisc 交互、`dev_ioctl` 分发、netns 迁移的实现细节 |
| `include/linux/ethtool.h` | 846 | `ethtool_ops` 全部 68 个指针的声明及其相邻注释 | 各参数结构体的字段语义 |
| `include/uapi/linux/ethtool.h` | 2156 | 命令码与 `ETHTOOL_*` 常量分组 | 绝大多数命令的参数结构体 |
| `net/core/net-sysfs.c` | 2077 | `net_class` `:1926`；`bql_show_inflight()` `:1370` 及相邻 BQL 属性；队列 kobject 的创建路径 | 大部分 `*_show`/`*_store` 的实现体 |
| `net/ethtool/ioctl.c` | 3344 | `dev_ethtool()` `:3021`、`__dev_ethtool()` `:2722` 的 switch 结构 | 各 case 的具体实现 |
| `net/ethtool/netlink.c` | 1079 | `ethnl_default_doit()` `:351`、`ethtool_genl_ops[]` `:742` | 各消息的属性解析 |
| `drivers/net/phy/phylink.c` | 3530 | `phylink_create()` `:1450`、`phylink_start()` `:1889`、`phylink_stop()` `:1961` 的签名与开头的参数校验 | 整个链路状态机、SFP 交互 |
| `include/linux/phylink.h` | 633 | `:53-72` `MAC_*` 能力标志；`:133-144` `phylink_config`；`:160-179` `phylink_mac_ops` | 其余回调结构 |
| `include/linux/phy.h` | 1883 | `:784` `phy_driver`；`:1041` 起的 `phy_read` 一族 | phylib 的大部分 API |
| `drivers/net/phy/mdio_bus.c` | 1079 | `:1045-1046` `mdio_bus_type` 及其 `.name` | 总线注册与 match 的实现 |
| `net/ethernet/eth.c` | 640 | `alloc_etherdev_mqs()`、`eth_type_trans()`、`eth_validate_addr()` | 其余辅助函数 |
| `net/core/skbuff.c` | 用于 RT diff | 仅 diff 涉及的 7 行 | 全文其余部分 |

**分界线为什么划在这里**：`netdevice.h` 和 `dev.c` 加起来 16859 行，全读的成本与收益不成比例。取舍标准是——**凡是文档里给出了行为断言的函数，实现必须详读；凡是只在 API 表里列出签名的，允许只读符号表**。这条线的直接后果记在 §5.2。

### §2.2.1 代表驱动的选法

`drivers/net/ethernet/` 是 5000+ 个文件的量级，不可能全读。选了五个，选法有明确判据：

| 驱动 | 为什么选它 |
|---|---|
| `stmmac` | arm64 SoC 上出现频率最高的 MAC IP（Synopsys DesignWare），且是「核心 + 平台 glue」分层的典型 |
| `mvneta` | 相对简单的全功能实现，用了 page_pool + XDP |
| `mvpp2` | 多队列 + 硬件分类，复杂度上限的代表 |
| `mtk_eth` | 带 SoC 内部交换的形态 |
| `am65-cpsw` | TI 的 CPSW，DT 描述方式和上面四个都不一样 |

其余驱动只讲契约，不讲实现。**凡是文档里写「全树 N 个驱动这么做」的地方，靠的是 grep 计数不是阅读**——计数命令一律写在正文旁边。

### §2.3 三等证据（Documentation/）

三处搜索路径的实际情况：

```bash
ls Documentation/networking/*.rst | wc -l          # → 108
ls Documentation/devicetree/bindings/net/ | wc -l  # → 176（含子目录）
ls Documentation/driver-api/ | grep -iE 'net|eth'  # → 空，退出码 1
```

⚠️ **第三条命令返回空**。网络这边没有 `driver-api` 侧的文档，全部住在 `Documentation/networking/`。这不是漏搜——是这个子系统的实际情况，登记在这里免得下一轮又搜一遍。

108 篇里跟网卡驱动相关的是下面这些（其余是 TCP 拥塞控制、IPsec、DSA、各种 L3 协议，与本文档集无关）：

| 文件 | 行数 | 用途 | 与代码冲突之处 |
|------|------|------|--------------|
| `Documentation/networking/driver.rst` | 124 | 发送路径的官方约定（不许在 `ndo_start_xmit` 里睡、Auto-close 语义、锁）；`01-api.md` 引用 18 次，是本文档集引用最多的一篇官方文档 | 未发现冲突 |
| `Documentation/networking/multiqueue.rst` | 78 | 多队列的 qdisc 侧约定；`01-api.md` 的队列 API 一节 | ⚠️ 举例用 `sch_multiq`（`:33`、`:38`、`:51`）。这个 qdisc 本版本仍在（`net/sched/sch_multiq.c`），但驱动侧的主线是 `mqprio`（`net/sched/sch_mqprio.c`）。本文档集按代码写，没跟这篇的举例 |
| `Documentation/networking/statistics.rst` | 220 | 统计口径（哪些计数器由核心维护、哪些由驱动）；`01-api.md` 的统计 API 一节 | 未发现冲突 |
| `Documentation/networking/netdev-features.rst` | 205 | feature 位的语义与依赖关系；`01-api.md` §9 的骨架 | 未发现冲突 |
| `Documentation/networking/netdevices.rst` | 299 | `net_device` 生命周期与锁约定、`ndo_*` 的调用上下文、NAPI 的官方说明 | 未发现冲突 |
| `Documentation/networking/scaling.rst` | 523 | RPS/RFS/XPS/RSS 的权威解释；`04-extensions.md` §10 | 未发现冲突 |
| `Documentation/networking/af_xdp.rst` | 640 | AF_XDP 的用户态视角；`04-extensions.md` §3 | 未发现冲突 |
| `Documentation/networking/page_pool.rst` | 223 | page_pool 设计意图；`04-extensions.md` §4 | 未发现冲突 |
| `Documentation/networking/netif-msg.rst` | 95 | `NETIF_MSG_*` 15 个消息类的官方分级建议；`07-debug.md` §3.1 | 未发现冲突 |
| `Documentation/networking/timestamping.rst` | 772 | 硬件时间戳；本文档集**只给指针不展开**（见 `04-extensions.md` §13） |  — |
| `Documentation/networking/ethtool-netlink.rst` | 1790 | netlink 前端的属性清单；本文档集只给指针（见 `04-extensions.md` §6.2） | — |
| `Documentation/networking/checksum-offloads.rst` | 143 | 校验和卸载语义；`01-api.md` §9.4 | — |
| `Documentation/networking/segmentation-offloads.rst` | 184 | GSO/TSO 语义；`01-api.md` §9.4 | — |
| `Documentation/devicetree/bindings/net/ethernet-controller.yaml` | 244 | 通用 MAC 属性的权威定义；`06-devicetree.md` §1.1 的来源 | 未发现冲突 |
| `Documentation/devicetree/bindings/net/mdio.yaml` | 116 | MDIO 总线节点 | 未发现冲突 |

⚠️ **`Documentation/driver-api/` 下没有网卡驱动的主文档**——网络这边的官方文档全部住在 `Documentation/networking/`。这个事实本身要登记，否则下一个人会以为是漏搜了。

### §2.4 反向覆盖率：为什么这里不能用脚本

`check_doc_coverage.sh` 在本子系统上**不可用**，原因和处置都要写清楚：

1. 脚本按「子系统名」去 `Documentation/` 下匹配目录。本子系统的中文名 `网卡驱动` 匹配不到任何目录
2. 把参数换成 `networking` 之后，脚本改用裸词匹配，`Documentation/networking/` 下有 200+ 篇 `.rst`，其中绝大多数（TCP 拥塞控制、IPsec、DSA、各种 L3 协议）跟网卡驱动没关系。而裸词匹配会给出「`driver` 被引用 332 次」这种无意义的输出

处置：**手工圈定相关文档集合**（就是 §2.3 那张表），然后人工反查「本文档集引用了几次」。这一轮反查的结果是补了 5 处「官方原文见 Documentation/…」的指针：

| 官方文档 | 原先被引用次数 | 补在哪 |
|---|---|---|
| `af_xdp.rst` | 0 | `04-extensions.md` §3.1 |
| `page_pool.rst` | 0 | `04-extensions.md` §4.1 |
| `scaling.rst` | 0 | `04-extensions.md` §10.1 |
| `netif-msg.rst` | 0 | `07-debug.md` §3.1 |
| `timestamping.rst` | 0 | `04-extensions.md` §13 的「相邻但不在本篇」表 |
| `ethtool-netlink.rst` | 0 | `04-extensions.md` §6.2 |
| `checksum-offloads.rst` / `segmentation-offloads.rst` | 0 | `01-api.md` §9.4 |

⚠️ 这是**人工做的**，不是脚本跑出来的。所以它的完整性不如脚本——可能还有被漏掉的相关文档。这条限制记在 §5.3。

### §2.7 arm64-only 裁掉了什么

必须显式登记，否则读者会当成遗漏：

| 裁掉的内容 | 为什么与 arm64 无关 |
|---|---|
| ACPI 发现路径（`fwnode` 的 ACPI 分支、`ACPI_PTR()`） | arm64 服务器网卡走 ACPI 的极少，SoC 网卡一律 DT。`06-devicetree.md` §0 的分诊表里这一行标了「本篇不覆盖」 |
| x86 特有的 IOMMU/DMA 行为差异 | page_pool 的收益分析只按 arm64 SMMU 讨论（`08-feynman.md` §5.3 第三点） |
| `arch/powerpc` / `arch/arm`（32 位）下的网卡相关代码 | 不在范围内 |
| 32 位平台上 `u64_stats_sync` 的真实作用 | arm64 上整套是空操作。⚠️ 这一点**反过来写进了文档**（`08-feynman.md` §8.3 第三点），因为「arm64 上测不出问题」正是它的危险之处 |
| PCIe 网卡的枚举路径 | 只在 `02-datastructures.md` 的父总线关系里作为对照提一句，不展开。arm64 SoC 场景的主线是 platform |
| 大端序相关的字节序讨论 | arm64 实际部署一律小端 |

⚠️ **裁掉不等于不存在**。上面每一条在别的架构上都可能是主线。

## §3 逐篇取证表

每篇挑「如果这条错了，读者会走错路」的论断。最后一列是自审过程中真实发生的修正——**空着的格子说明这一篇这一轮没抓到错，不代表它没错**。

### §3.1 `00-overview.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 子系统类型是协议/网络型，判定靠两道门 | 门 1 `rg 'struct bus_type'` 空；`net/core/net-sysfs.c:1926` `net_class` | — |
| 网络的用户态 ABI 面积远大于任何硬件总线 | uapi 头文件 48 个（口径见 §3.2 第 2 行） | ⚠️ 原写 53 个，53 把 5 个 `netfilter*` **目录**当成了头文件。已改 |
| feature 位 63 个 / `ethtool -k` 可见 62 个 | `include/linux/netdev_features.h:14-101`；`net/ethtool/common.c:11-77` | ⚠️ 原写「62 个」，把两个口径混成一个。已改，且两处都补了口径说明 |
| 端到端路径：中断 → NAPI → GRO → 协议栈 | `03-internals.md` Part A 的浓缩，行号在那边 | — |

### §3.2 `01-api.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `net_device_ops` 86 个函数指针 | `awk '/^struct net_device_ops \{/,/^\};/' include/linux/netdevice.h \| grep -cE '\(\*[A-Za-z0-9_]+\)'` → 86 | ⚠️ 自审时我数出 85，以为文档错了。**是我的 grep 错**——`[a-z_]` 漏掉了名字里带数字的 `ndo_get_stats64`。文档原文正确，未改 |
| `ethtool_ops` 68 个函数指针 | 同上口径，换 `include/linux/ethtool.h` → 68 | — |
| uapi 网络头文件 48 个 | `ls include/uapi/linux/ \| grep -E '^(if\|ethtool\|net)' \| grep -c '\.h$'` → 48 | 见 §3.1 第 2 行 |
| 63 个 feature 位中差的那一个是 `NETIF_F_GSO_UDP_BIT` | 定义 `netdev_features.h:54`，`netdev_features_strings[]` 里无对应字符串 | 这一条是修正 62/63 时**新补**的，不是原有内容 |
| `devm_alloc_etherdev_mqs()` 实现在 `net/devres.c` 而不是 `net/ethernet/eth.c` | `net/devres.c:21-40` | ⚠️ 备注原先没点明文件位置，读者按 `eth.c` 找不到 |
| devm 版本与 `register_netdev()` 混用的真实危险是 `free_netdev()` 里的 `BUG_ON` | `net/core/dev.c:10889` `BUG_ON(dev->reg_state != NETREG_UNREGISTERED)`；`:10893` `put_device()` | ⚠️ **内容错**。原文写「只要走 `register_netdev()` 这条路，就别用 devm 版本」，与本文档集 `05-usage.md` §1.2 自己给的骨架矛盾，也与树内 25 个这样用的驱动矛盾。已重写为「危险不在混用，在 `remove()` 里没先 `unregister_netdev()`」 |
| `devm_register_netdev()` 存在且要求先用过 devm 分配 | `net/devres.c:67-95`，kerneldoc `:57-66`，`WARN_ON(!devres_find(...))` → `-EINVAL` 在 `:76-78` | ⚠️ 这个 API 原先整套文档一次都没提。`net/devres.c` 全文 95 行在本轮之前**被引用 0 次** |
| RT 补丁分两组：改名组 / 改时序组 | §1.3 | ⚠️ `:121` 原先只有一句含糊的过渡，没说清第二组会改时序 |

### §3.3 `02-datastructures.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `net_device` 注册到 class 不是 bus，因此永远不会被绑定 driver | `net/core/net-sysfs.c:1926`；全树无 `net_bus_type`（否定性断言，grep 全树） | — |
| `net_device` 内嵌 `struct device dev`，与父设备靠 `dev.parent` 缝合 | `include/linux/netdevice.h:2317` | — |
| feature 位共 63 个 | 同 §3.1 | ⚠️ 原写 62。已改并补「见 `01-api.md` §9」 |
| NAPI 的 SCHED/MISSED 状态靠 cmpxchg 而不是锁 | `net/core/dev.c` 的 `napi_schedule_prep()` / `napi_complete_done()` | — |
| RT 补丁给 `softnet_data` 加了 `defer_csd`/`defer_work` | `include/linux/netdevice.h:3202-3206`（就是 diff 的 +4 行） | — |

### §3.4 `03-internals.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| Part A 时间线的次序按「谁调用谁」验证过，不是凭直觉排 | 各节标题带函数完整行号区间 | — |
| `free_netdev()` 在 `reg_state != NETREG_UNREGISTERED` 时直接 `BUG_ON` | `net/core/dev.c:10889` | — |
| `free_netdev()` 最后只做 `put_device()`，真正释放可能被延迟 | `net/core/dev.c:10893` | — |
| `trigger_rx_softirq()` 在本树有 RT / 非 RT 两个实现 | `net/core/dev.c` 的 `#ifdef CONFIG_PREEMPT_RT` 分支 | — |
| 源码自陈设计债清单（§C1） | 见 §4.2，逐条带行号 | — |

### §3.5 `04-extensions.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `enum tc_setup_type` 21 个成员 | `include/linux/netdevice.h:954-976`。⚠️ 口径要注意：`grep -c 'TC_SETUP_'` 只得 20，第 21 个成员叫 `TC_QUERY_CAPS`（`:955`），不带 `TC_SETUP_` 前缀 | 自审时按 `TC_SETUP_` 数出 20，怀疑文档错；打开枚举后确认文档的 21 正确。未改 |
| `net/ethtool/` 26 个 `.c`、11354 行 | `find net/ethtool -name '*.c' \| xargs wc -l` | — |
| ioctl 与 netlink 两条前端最终落到同一个 `dev->ethtool_ops` | `net/ethtool/ioctl.c:2722` 的 switch；`net/ethtool/netlink.c:351` + `:742` | — |
| `drivers/net/phy/` 85 个文件、59610 行 | `find drivers/net/phy -name '*.[ch]' \| wc -l` → 85 | 自审时我按 `ls drivers/net/phy/*.[ch]` 只得到少一些的数，差额是 `mscc` 子目录。文档的 85 含子目录，正确 |
| page_pool 的使用者只有 12~13 个驱动文件（两种口径） | 两种口径都写在 §12.2 | 保留两个数是**故意的**，因为「用了 page_pool」按 `page_pool_create` 调用点数和按文件数算不一样 |
| BQL 实现只有 138 行 | `wc -l lib/dynamic_queue_limits.c` → 138 | — |

### §3.6 `05-usage.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 骨架代码**未编译未运行** | 篇首声明 + §5.1 | — |
| 正文出现的每个符号在本版本都存在 | 抽查见 §6.1 的符号存在性一轮 | — |
| `remove()` 必须严格倒序，`unregister_netdev()` 在最前 | §1.8，依据是 `net/core/dev.c:10889` 的 `BUG_ON` | ⚠️ §1.8 是本轮**新增**的一节，原先这条纪律只散在 §1.2 骨架的注释里 |
| RT 补丁导致的编译错误只有 `_irq` 变体这一类 | §1.7 + §1.3 的 0 残留验证 | — |

### §3.7 `06-devicetree.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| §0 分诊表：DT 只是发现路径之一，ACPI 路径在 arm64 上不覆盖 | §2.7 登记了裁剪 | — |
| 每条属性都定位到消费代码；找不到消费者的显式标出 | §1.1 总表逐行给行号 | — |
| phylink 场景下有三条「返回 0 但没有连上 PHY」的路径，它们不是错误路径 | §4.4，依据是 `drivers/net/phy/phylink.c` 的 SFP 分支 | — |
| 举例节点全部取自 `arch/arm64/boot/dts/` | 每个例子都带 DTS 文件路径 | — |

### §3.8 `07-debug.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| `NETIF_MSG_*` 共 15 个消息类 | `net/ethtool/common.c:358-374` 的 `netif_msg_class_names[]` → 15 条 | — |
| BQL 的 `inflight` 是现算的，不是存起来的字段 | `net/core/net-sysfs.c:1370` `bql_show_inflight()` = `num_queued - num_completed` | — |
| 驱动漏调 `netdev_tx_completed_queue()` 会静默压死吞吐、不打任何日志 | §4.3。依据是 `lib/dynamic_queue_limits.c` 全文里没有任何打印 | — |
| `netdev_drivername()` 在 `dev.parent` 为 NULL 时返回空串 | §8.1 | — |
| 三套发送返回值编码的完整对照 | §11.1，依据 `include/linux/netdevice.h:99-129` | — |
| 故障注入：本子系统**没有**专门的故障注入框架 | §「故障注入」一节。这是一条否定性断言，grep 过 `fail_`/`should_fail` | — |

### §3.9 `08-feynman.md`

| 关键论断 | 出处 | 自审修正记录 |
|---|---|---|
| 八节每节都指出了一个真实的类比边界，不是泛泛而谈 | 每节 §x.3 | — |
| `netdev_budget = 300` 是一轮软中断里所有设备的总预算，单设备是 `dev_rx_weight = 64` | `net/core/dev.c:4424` / `:4430` | — |
| `NETDEV_TX_BUSY` 之后 skb 归调用方，驱动一个字节都不许动 | `include/linux/netdevice.h:120-124` 的枚举 + `:127-129` 的数值区间注释 | — |
| `u64_stats_sync` 在 arm64 上整套是空操作 | `include/linux/u64_stats_sync.h` 的 64 位分支 | — |
| 篇末给出八节与其它篇的对应关系 | `:320-329` 的三列映射表 | ⚠️ 原先是一句把八组对应关系全串起来的长句，**内容对但读不懂**。改成表，事实一条没动 |

### §3.10 数字口径表

同一个对象按不同口径数会得到不同的数。凡是文档里出现过两个数的地方，两个口径都在这里：

| 对象 | 数字 | 口径 | 重跑命令 |
|---|---|---|---|
| `net_device_ops` 成员 | 86 | 函数指针声明行数 | `awk '/^struct net_device_ops \{/,/^\};/' include/linux/netdevice.h \| grep -cE '\(\*[A-Za-z0-9_]+\)'` |
| `ethtool_ops` 成员 | 68 | 同上 | 同上，换 `include/linux/ethtool.h` 与结构体名 |
| feature 位 | 63 | `NETIF_F_*_BIT` 名字数 | 见 §6 交叉验证第 2 条 |
| feature 位 | 62 | `ethtool -k` 里有名字的 | `awk '/netdev_features_strings\[/,/^};/' net/ethtool/common.c \| grep -c '\[NETIF_F_'` |
| feature 位 | 64 | `NETDEV_FEATURE_COUNT` 的值 | 63 个名字 + 1 个历史空洞 `__UNUSED_NETIF_F_1`（`netdev_features.h:17`，占 bit 2） |
| `tc_setup_type` 成员 | 21 | 枚举全部成员 | 打开 `include/linux/netdevice.h:954-976` |
| `tc_setup_type` 成员 | 20 | 带 `TC_SETUP_` 前缀的 | `grep -c 'TC_SETUP_'`。⚠️ 这个口径会漏掉 `TC_QUERY_CAPS` |
| uapi 网络头文件 | 48 | `if`/`ethtool`/`net` 开头且以 `.h` 结尾 | `ls include/uapi/linux/ \| grep -E '^(if\|ethtool\|net)' \| grep -c '\.h$'` |
| uapi 网络头文件 | 53 | 上面的加 5 个 `netfilter*` 目录 | ⚠️ **这个口径是错的**，目录不是头文件。登记在此免得下次又数出 53 |
| `drivers/net/phy/` 文件 | 85 | 含 `mscc` 子目录 | `find drivers/net/phy -name '*.[ch]' \| wc -l` |
| page_pool 使用者 | 12 / 13 | 调用点数 / 文件数 | 见 `04-extensions.md` §12.2 |
| RT 补丁改动的以太网文件 | 47 | `git status --porcelain -- drivers/net/ethernet \| wc -l` | 同左 |

## §4 源码自陈问题

### §4.1 本树 RT 补丁触及的驱动分布

47 个以太网驱动文件按厂商目录分组（`git status --porcelain -- drivers/net/ethernet | awk '{print $2}' | awk -F/ '{print $4}' | sort | uniq -c | sort -rn`）：

```
     13 intel          3 netronome     3 marvell       2 ti
      2 microsoft      2 huawei        2 google        2 emulex
      2 broadcom       2 amazon        1 xilinx        1 via
      1 socionext      1 realtek       1 qualcomm      1 nvidia
      1 mellanox       1 mediatek      1 hisilicon     1 fungible
      1 cortina        1 asix          1 aquantia      1 alacritech
```

这张表的用处不是「谁被改得多」，而是**证明这批改动与厂商无关**——它命中的是「用了 `u64_stats_fetch_*_irq` 的驱动」这个集合，跟硬件形态没有关系。

⚠️ 本文档集的五个代表驱动里，`mediatek`（1 个文件）、`marvell`（3 个）、`ti`（2 个）在列。所以**读代表驱动的统计代码时，看到的是改过之后的样子**，和上游 6.1.158 不一样。

### §4.2 源码自己写下的设计债

这一节的内容全部是内核源码里的原话，不是我的评价。展开在 `03-internals.md` §C1。

| 位置 | 原文 / 内容 | 性质 |
|---|---|---|
| `include/linux/netdevice.h:1757-1762` | `Actually, this whole structure is a big mistake. It mixes I/O data with strictly "high-level" data, and it has to know about almost every data structure used in the INET module.` | 对 `struct net_device` 本身的整体否定评价，写在它的 kerneldoc 开头 |
| `include/linux/netdevice.h:2025-2026` | `FIXME: cleanup struct net_device such that network protocol info moves out.` | 上一条的具体化 |
| `include/linux/netdevice.h:2035` | `FIXME: Merge these and struct ifmap into one` | 字段冗余 |
| `include/linux/netdevice.h` 共 10 处 | `XXX: need comments on this one`，分别在 `:1860`（`addr_list_lock`）、`:1901`（`rx_handler_data`）、`:1904`（`ingress_queue`）、`:1919`（`tx_global_lock`）、`:1923`（`xps_maps`）、`:1937`（`link_watch_list`）、`:1946`（`npinfo`）、`:1980`（`tc_to_txq`）、`:1981`（`prio_tc_map`）、`:1985`（`priomap`） | ⚠️ **十个字段官方承认没有文档**。其中 `addr_list_lock` 和 `tx_global_lock` 是锁——「这把锁保护什么」在源码里没有答案 |
| `include/linux/netdevice.h:5156` | `TODO: reserve and use an additional IFF bit, if we get more users` | IFF 标志位快用完了 |
| `net/core/dev.c:3503` | `XXX: check that highmem exists at all on the given machine.` | HIGHMEM 相关，arm64 上不涉及 |

⚠️ **一个假阳性要点名**：`grep TODO include/linux/netdevice.h` 会命中 `:4702` 的 `NESTED_SYNC_TODO_BIT` 和 `:4709` 的 `#define NESTED_SYNC_TODO`。**这两个不是设计债**——`TODO` 在这里是这个枚举值的正式名字（表示「这一层嵌套还没同步」的状态位），不是留给维护者的备注。按关键词 grep 设计债时这类假阳性必须逐条打开确认。

重跑命令：

```bash
grep -n 'FIXME\|XXX\|TODO' include/linux/netdevice.h
grep -n 'FIXME\|XXX' net/core/dev.c
```

## §5 未验证项

四层分层。**层次越靠前越危险**——第一层是「可能压根跑不起来」，第四层只是「换个版本可能不一样」。

### §5.1 未编译未运行

- `05-usage.md` 里全部代码骨架。它们**没有编译过，更没有在硬件上跑过**。篇首已声明，这里再登记一次
- `07-debug.md` 里的示例命令输出：凡是带具体数字的输出片段，都是按源码里的 `seq_printf` 格式串**推出来的格式**，不是真机抓的。⚠️ 列的顺序和个数可信（来自格式串），具体数值不可信
- 06 篇的 DTS 例子：节点内容取自树内真实 DTS，但**没有在真机上验证过我的解读**（比如「这个属性不写会怎样」）

### §5.2 只读符号表的部分

判据是 §2.2 那张表的第四列。后果按危险程度排：

| 只读了符号表的部分 | 文档里哪条论断因此是推断 |
|---|---|
| `drivers/net/phy/phylink.c` 的链路状态机（3530 行里绝大部分） | 「phylink 的回调不在 `ndo_open` 的调用栈上」（`08-feynman.md` §6.3 第一点）——⚠️ 这是从「状态检测跑在独立工作队列上」推的，没有逐行跟过状态机 |
| `net/ethtool/ioctl.c` 各 case 的实现体 | 「两套前端最终落到同一个 `ethtool_ops`」——从 switch 结构和 `common.c` 的共用推的 |
| `net/core/dev.c` 的 qdisc 交互 | 「BQL 限制之后 qdisc 的调度才有机会起作用」（`08-feynman.md` §3.2）——机制方向确定，但**没跟过 qdisc 侧的代码** |
| `include/uapi/linux/ethtool.h` 的参数结构体 | ethtool 各命令的参数语义大部分来自 `Documentation/`（三等证据）而不是代码 |
| `net/core/dev.c` 的 netns 迁移 | 「网络接口是 per-namespace 的，不同 ns 里可以各有一个 eth0」（`08-feynman.md` §2.3）——这条是常识性事实，但代码路径没读 |

### §5.3 推断而非观察

- **RT 补丁的运行时影响**。§1.3 说第二组「会改变 skb 延迟释放的执行上下文」，依据是两条代码路径（IPI vs workqueue）的差异。**没有在 RT 内核上测过，也没有测过延迟数字**
- **性能类论断全部是推断**。`08-feynman.md` §5.3 说「没有 IOMMU 的平台上 page_pool 的收益可能小到测不出来」、§4.3 说「流特别多的场景 GRO 是净损失」——这些是从机制推的，本文档集**没有做过任何性能测量**
- **`04-extensions.md` §0.2 的「arm64 上 phylink 一边倒」**：依据是五个代表驱动的 grep 结果（`phylink_create` 5 个、`phy_connect` 0 个）。样本只有五个，「一边倒」这个措辞比证据强
- **官方文档反向覆盖率是人工圈定的**（§2.4）。可能还有相关的 `Documentation/networking/*.rst` 被漏掉

### §5.4 版本时效性

本文档集只对 `f6e38ae624cf`（6.1.158 + 本树 RT 补丁）成立。已知会随版本变的地方：

| 内容 | 时效风险 |
|---|---|
| `u64_stats_fetch_*_irq` 不存在 | ⚠️ **这是本树特有的**。上游 6.1.158 里它们存在。往新版本走，上游最终也删了，但删除的时间点和本树不同 |
| `defer_csd` / `defer_work` 双路径 | 同上，本树 RT 补丁引入 |
| feature 位 63 个 / `NETDEV_FEATURE_COUNT` = 64 | 每个版本都在加位 |
| `net_device_ops` 86 个 / `ethtool_ops` 68 个 | 同上，只增不减 |
| `tc_setup_type` 21 个 | 同上 |
| 所有行号 | 6.1.y 的后续 stable 更新就可能偏移 |
| `Documentation/networking/napi.rst` **在 6.1 里不存在** | ⚠️ NAPI 的官方说明这一版还在 `netdevices.rst` 里。见 §6 交叉验证第 5 条 |

### §5.5 对端 / 树外实现

**不适用。**

理由：§1.4 的类型判定结果是协议/网络型，不是虚拟总线-跨边界 ABI 型。本子系统的两端都在本树里——一端是 `drivers/net/ethernet/` 的驱动代码，另一端是 `net/core/` 的协议栈。没有「对端是 hypervisor / 远端固件 / 树外规范」这种边界。

⚠️ 两个**看起来像**跨边界但不是的地方，说明一下免得被当成漏掉：

| 看起来像跨边界 | 为什么不算 |
|---|---|
| 网卡固件 | 驱动确实要跟固件握手（`ethtool -i` 里的 firmware-version 就是从固件读的）。但**本文档集的五个代表驱动都不涉及固件下载**，DMA 描述符格式由驱动自己定义在头文件里。真正有固件 ABI 的是 `mellanox`/`broadcom` 这类，它们不在范围内 |
| DMA 描述符环 | 它是「CPU 写、硬件读」的共享内存，形式上像跨边界 ABI。但格式定义在本树的驱动头文件里，没有树外规范文本——所以是一等/二等证据能覆盖的，不需要单列证据档次 |

## §6 交叉验证实例

每一条都是「我先有一个结论，然后被某个具体动作推翻」。列出来的目的不是自我批评，而是**告诉读者哪一类结论最不可靠**。

| # | 初始假设 | 被什么推翻 | 正确结论 |
|---|---|---|---|
| 1 | 文档写的 `net_device_ops` 86 个成员是错的，我数出 85 | 把 `grep -cE '\(\*[a-z_]+\)'` 的字符类改成 `[A-Za-z0-9_]+` 后得到 86 | **86 正确，是我的正则错了**——它漏掉名字里带数字的 `ndo_get_stats64`。⚠️ 这条的教训是：**用 grep 去质疑一个数之前，先验证 grep 本身**。同一个字符类错误还差点让我改错 `ethtool_ops` |
| 2 | feature 位一共 62 个（文档五处都这么写） | `awk` 出整个枚举，逐个成员算值 | **三个数，三个口径**：`NETIF_F_*_BIT` 名字 63 个；`ethtool -k` 里有名字的 62 个（差 `NETIF_F_GSO_UDP_BIT`）；`NETDEV_FEATURE_COUNT` = 64（多出来的是 `__UNUSED_NETIF_F_1` 这个历史空洞）。原来五处「62」把口径混成了一个。⚠️ 而且**中途我一度算成 64 个名字**——因为 `NETIF_F_GSO_LAST = NETIF_F_GSO_UDP_L4_BIT,` 让后者在按行 grep 时被数了两遍。只有写脚本逐成员求值才拿到确定答案 |
| 3 | 「走 `register_netdev()` 就别用 `devm_alloc_etherdev_mqs()`」 | 想给这条禁令找一个树内反例，结果找到 25 个；而且本文档集自己的 `05-usage.md` §1.2 骨架就是这么写的 | 危险不在混用，在**释放次序**：devres 解绑时调 `free_netdev()`（`net/devres.c:14-19`），而 `free_netdev()` 里有 `BUG_ON(dev->reg_state != NETREG_UNREGISTERED)`（`net/core/dev.c:10889`）。判据是「`remove()` 里必须先 `unregister_netdev()`」。⚠️ **这条错误在文档里存在了整整八篇**，直到我去数反例才暴露 |
| 4 | `net/devres.c` 只有 95 行，没什么内容 | 打开读完 | 它是 `devm_alloc_etherdev_mqs()` 的**真正实现位置**（不在 `net/ethernet/eth.c`），还藏着一个整套文档一次都没提的 API `devm_register_netdev()`。⚠️ **这个文件在本轮之前被引用 0 次**——「行数少所以内容少」是一个坏的取样启发 |
| 5 | `Documentation/networking/napi.rst` 是 NAPI 的官方文档，可以列进三等证据 | `wc -l` 报 No such file or directory | **6.1 里没有这个文件**，NAPI 的官方说明这一版还在 `netdevices.rst` 里。⚠️ 这条是**写本篇 §2.3 的时候当场造出来的幻觉文件名**——证明「取证篇本身也会产生新错误」（skill 规则 15）。它没有污染 00-08，因为那八篇一次都没引用过它 |
| 6 | uapi 网络头文件 53 个 | `ls` 出来逐行看 | 53 里有 5 个 `netfilter*` 是**目录**不是头文件。正确是 48 |
| 7 | `enum tc_setup_type` 20 个成员（`grep -c 'TC_SETUP_'`） | 打开 `include/linux/netdevice.h:954-976` | 21 个。第一个成员叫 `TC_QUERY_CAPS`，不带 `TC_SETUP_` 前缀。⚠️ **按前缀数枚举成员这个手法本身有系统性风险** |
| 8 | `drivers/net/phy/` 的文件数按 `ls *.[ch]` 数 | 换成 `find` | 少了 `mscc` 子目录。85 正确 |
| 9 | 脚本输出 `\| tail -30` 看到 15 条失败，以为就这些 | 改成 `\| head -60` | 每条失败占 2 行，`tail -30` 截掉了 5 条——**其中 2 条是真失败**。⚠️ **截断脚本输出是一个会静默丢证据的动作** |

⚠️ 把 #1、#7、#8 放在一起看，有一个共同结论：**九条里有三条是「我的统计手法错了」而不是「文档错了」**。所以看到数字对不上时，第一件事是复核命令，不是改文档。

### §6.1 三个脚本的最近一次实际结果

```bash
S=~/.claude/skills/kernel-subsystem-docs/scripts
D=output/网卡驱动
python3 $S/check_xrefs.py     $D --ignore $D/.xref-ignore
python3 $S/check_line_refs.py $D . --ignore $D/.lineref-ignore
bash    $S/check_doc_coverage.sh 网卡驱动 $D .
```

| 脚本 | 上次结果 | 忽略项 |
|---|---|---|
| `check_xrefs.py` | 10 篇；跨篇引用命中 169 条，失效 0 条 | 0 条 |
| `check_line_refs.py` | 扫描 568 条唯一 `file:line` 引用；解析不到 0 个、越界 0 条（消歧表 82 个基名 / 44 个前缀目录） | 0 条 |
| `check_doc_coverage.sh` | **不可用**，见下 | — |

⚠️ **忽略项两个都是 0 条**，所以没有假阳性登记表——这不是因为没遇到假阳性，而是因为遇到的都按 skill 的优先级**消除了触发条件**而不是登记忽略。本轮消除掉的一条：`01-api.md:121` 原先在同一行里同时出现别篇文件名和本篇章节号，改写句子结构之后误报自然消失，信息一个字没少。

`check_doc_coverage.sh` 的实际输出（两种参数都跑了）：

- 传子系统中文名 → `没找到 Documentation/网卡驱动/、driver-api/网卡驱动* 或 bindings/网卡驱动/`。脚本自己建议「在 99 里明确登记这一点」，已登记在 §2.3
- 传 `networking` → 列出 144 篇，其中 109 篇「引用 0 次」。⚠️ **这个输出是噪声，不能当结论**：`Documentation/networking/` 下的绝大多数文档（6lowpan、arcnet、atm、batman-adv、dccp、DSA、各种 L3 协议）跟网卡驱动没有关系。而且它按裸词匹配，会给出 `alias 引用 15 次`、`can 引用 16 次`、`bridge 引用 8 次` 这种命中——那些「引用」是中文正文里恰好出现了这几个英文单词，不是引用了对应的 `.rst`

处置：手工圈定相关文档（§2.3 那张表），人工反查覆盖率（§2.4）。**这一项在本子系统上是人工做的，可信度低于脚本。**

### §6.2 缺陷统计与「靠什么手段发现的」

| 手段 | 抓出了什么 | 条数 |
|---|---|---|
| **数一个数、然后去找反例** | `devm` 那条内容错（§6 #3）；`net/devres.c` 从未被引用（#4） | 2（**两条都是本轮最严重的**） |
| 写脚本逐成员求值，而不是按行 grep | feature 位的三个口径（#2） | 1 |
| 打开枚举/结构体本体，不信前缀 grep | `tc_setup_type` 21（#7）；`net_device_ops` 86（#1） | 2 |
| 换 `find` 重数目录树 | `drivers/net/phy` 85（#8） | 1 |
| `ls` 出来逐行看，不信 `grep -c` | uapi 48（#6） | 1 |
| `wc -l` 每个引用的官方文档 | `napi.rst` 不存在（#5） | 1 |
| 不截断脚本输出 | 2 条被 `tail` 截掉的真失败（#9） | 2 |
| 三个脚本 | 13 条「没有 99-*.md」的失效引用（本篇写完自然清零）+ 1 条同行文件名/章节号的假阳性 | 14 |
| 重排「内容对但读不懂」的段落 | `08-feynman.md:318` 的长句改表（事实一条没动） | 1 |

结论有两条，都跟 skill 的既有经验一致：

1. **「去找一个反例」比「再读一遍」有效得多**。本轮最严重的两个问题（一个内容错、一个整文件漏读）都来自这个动作
2. **凡是「按前缀 / 按行 grep 去数枚举成员」的地方都要打开本体核对**。九条交叉验证里有四条是这一类

补一条本子系统特有的：**「行数少」不是「内容少」的可靠信号**。`net/devres.c` 95 行、`lib/dynamic_queue_limits.c` 138 行，两个都是短文件，两个都藏着文档必须讲的东西。

### §6.3 与另一套独立产出的文档集对比

**没有。** 本子系统目前只有这一套文档集，所以没有独立对照。⚠️ 这意味着 §6 的九条交叉验证**全部来自自审**，没有第二双眼睛——比有对照的子系统（如 spi、pinctrl）可信度低一档。

## §7 如何复核本文档集

按成本从低到高，四步：

**第一步：跑三个脚本（约 10 秒）。** 命令见 §6.1。它们抓「失效的跨篇引用」和「越界的行号」，抓不到内容错。⚠️ **不要截断输出**（§6 #9）。

**第二步：抽 §3 的高危论断逐个 `sed` 打开。** 优先级最高的五条：

```bash
sed -n '10889p'      net/core/dev.c              # BUG_ON(reg_state != NETREG_UNREGISTERED)
sed -n '14,19p;76,78p' net/devres.c              # devm_free_netdev / WARN_ON → -EINVAL
sed -n '120,129p'    include/linux/netdevice.h   # 三套发送返回值的数值区间
sed -n '3202,3206p'  include/linux/netdevice.h   # 本树 RT 补丁新增的两个字段
sed -n '1370p'       net/core/net-sysfs.c        # BQL inflight 是现算的
```

**第三步：重跑 §3.10 的数字口径表。** 每一行都有重跑命令。⚠️ **数字对不上时先复核命令**——§6 里九条有三条是统计手法错。

**第四步：全称/否定性断言的独立验证。** 本文档集里这类断言四条，各自的验证命令：

```bash
# 「全树没有 net_bus_type」
rg -n 'net_bus_type' .
# 「本树没有 u64_stats_fetch_*_irq 残留」
rg -c 'u64_stats_(fetch_begin|fetch_retry)_irq' -g '*.[ch]' . | wc -l
# 「本子系统没有故障注入框架」
rg -c 'should_fail|fail_nth|DECLARE_FAULT_ATTR' net/core/ net/ethtool/
# 「Documentation/driver-api 下没有网络文档」
ls Documentation/driver-api/ | grep -iE 'net|eth'
```

四条都应该返回空或 0。⚠️ **返回空的命令要看退出码**，`grep` 无匹配时退出码是 1，管道里很容易被当成「命令没跑」。

**复核不覆盖什么**：性能类论断（§5.3）、`05-usage.md` 的骨架能不能编译（§5.1）、phylink 状态机的细节（§5.2）。这三块只能靠上机器。

## §8 规模统计

全部可一条命令重跑。

### §8.1 文档集自身

```bash
wc -l output/网卡驱动/*.md
```

| 篇 | 行数 |
|---|---|
| `00-overview.md` | 514 |
| `01-api.md` | 899 |
| `02-datastructures.md` | 1058 |
| `03-internals.md` | 1280 |
| `04-extensions.md` | 753 |
| `05-usage.md` | 951 |
| `06-devicetree.md` | 732 |
| `07-debug.md` | 745 |
| `08-feynman.md` | 329 |
| 00-08 合计 | 7261 |
| `99-evidence.md` | 630 |
| 全集合计 | 7891 |

### §8.2 被文档化的代码规模

```bash
wc -l net/core/dev.c net/core/net-sysfs.c net/core/gro.c net/core/page_pool.c \
      net/core/link_watch.c net/core/dev_addr_lists.c net/devres.c net/ethernet/eth.c
wc -l include/linux/netdevice.h include/linux/netdev_features.h include/linux/ethtool.h
find net/ethtool -name '*.c' | xargs wc -l | tail -1
find drivers/net/phy -name '*.[ch]' | xargs wc -l | tail -1
```

| 位置 | 行数 | 证据等级 |
|---|---|---|
| `include/linux/netdevice.h` | 5242 | 二等 |
| `net/core/dev.c` | 11617 | 二等 |
| `net/core/net-sysfs.c` | 2077 | 二等 |
| `net/core/gro.c` | 817 | 一等 |
| `net/core/page_pool.c` | 938 | 一等 |
| `net/core/link_watch.c` | 280 | 一等 |
| `net/core/dev_addr_lists.c` | 1050 | 二等 |
| `net/devres.c` | 95 | 一等 |
| `net/ethernet/eth.c` | 640 | 二等 |
| `net/ethtool/`（26 个 `.c`） | 11354 | 二等（`common.c` 599 行是一等） |
| `drivers/net/phy/`（85 个 `.[ch]`） | 59610 | 二等 |
| `lib/dynamic_queue_limits.c` | 138 | 一等 |

一等证据合计 3843 行（§2.1）。

### §8.3 关键计数

| 对象 | 数 | 口径见 |
|---|---|---|
| `net_device_ops` 函数指针 | 86 | §3.10 |
| `ethtool_ops` 函数指针 | 68 | §3.10 |
| feature 位（名字 / ethtool 可见 / `FEATURE_COUNT`） | 63 / 62 / 64 | §3.10 |
| `NETIF_MSG_*` 消息类 | 15 | `net/ethtool/common.c:358-374` |
| `enum tc_setup_type` 成员 | 21 | §3.10 |
| uapi 网络头文件 | 48 | §3.10 |
| `Documentation/networking/*.rst` | 108（其中被本文档集引用的 14 篇见 §2.3） | §2.3 |
| `Documentation/devicetree/bindings/net/` 条目 | 176（含子目录） | §2.3 |
| RT 补丁改动：以太网驱动文件 | 47（+204 / -204） | §1.2 |
| RT 补丁改动：`net/core/` 文件 | 4（+52 / -36，含两个头文件） | §1.2 |
| `devm_alloc_etherdev*` 使用文件 | 34 | 下面的口径说明 |

⚠️ **`devm` 那三个数不构成一个划分**：34 个文件用 `devm_alloc_etherdev*`；其中 25 个同文件里出现裸 `register_netdev`；6 个用 `devm_register_netdev`。25 + 6 = 31 ≠ 34，而且两个集合还有 1 个交集（`drivers/net/ethernet/sun/sunhme.c` 两个都出现）。差额是 4 个把注册动作放在同目录另一个文件里的驱动（`micrel/ks8851_par.c`、`micrel/ks8851_spi.c` 注册在 `ks8851_common.c`；`wangxun/ngbe`、`wangxun/txgbe` 同理）。**三个数各自是独立的 grep 计数，不要相加。**

```bash
rg -l 'devm_alloc_etherdev' drivers/net/ethernet | wc -l   # → 34
rg -l 'devm_register_netdev' drivers/net/ethernet | wc -l  # → 6
comm -12 <(rg -l 'devm_alloc_etherdev' drivers/net/ethernet | sort) \
         <(rg -lw 'register_netdev'    drivers/net/ethernet | sort) | wc -l   # → 25
```
