---
title: 调试方法
---

# 网卡驱动调试手段

基线 `f6e38ae624cf`（Linux 6.1.158），arm64-only。本篇只讲**网卡驱动与网络设备核心**这一层的调试手段：`net/core/`、`net/ethtool/`、`net/sched/sch_generic.c` 的看门狗、以及 `drivers/net/ethernet/` 驱动侧共用的日志与统计约定。协议栈内部（TCP/IP 拥塞控制、netfilter、qdisc 调度算法）不在本篇。ACPI 相关裁剪记录见 `99-evidence.md` §2.7。

## §0 本篇怎么读

### §0.1 按症状找工具

| 症状 | 第一手段 | 落在本篇 | 为什么先看这个 |
|---|---|---|---|
| 接口 up 了但 `ip link` 显示 `NO-CARRIER` | `ethtool <if>` + `ethtool --show-eee` | §3、§10 | 链路层没起来，驱动的收发路径根本没被调用 |
| 有 carrier 但收不到包 | `/proc/net/softnet_stat` + `napi_poll` tracepoint | §5.3、§10 | 先分清「中断没来」和「NAPI 跑了但没收到」 |
| 收到包但被丢掉 | `kfree_skb` tracepoint 的 `reason` 字段 | §7 | 64 个 drop reason 直接点出丢在哪一层 |
| `NETDEV WATCHDOG ... transmit queue N timed out` | §8 全节 | §8 | 这是网卡驱动最常见的单条故障信号 |
| 发送很慢 / 延迟抖动大 | BQL 的 `inflight` + `limit` | §4.3 | 队列深度是发送侧延迟的第一嫌疑 |
| 驱动一句日志都不打 | `msg_enable` 位 + `dynamic_debug` | §1、§2、§3 | 网卡驱动的日志默认大半是关的 |
| 统计数字对不上 | `ethtool -S` + `/proc/net/dev` + 标准化统计 ops | §7.4、§10 | 三个来源的口径不同，不是同一份数据 |
| 想知道某个 `ethtool` 子命令为什么返回「Operation not supported」 | §11 错误码分诊 | §11 | `-EOPNOTSUPP` 在 ethtool 通路上有多个来源 |
| 厂商网卡固件层面出错 | devlink health reporter | §13 | 核心层看不到固件状态，只有 devlink 通道 |

### §0.2 哪些手段需要重编内核

| 手段 | 需要重编 | 运行期可切 | 出处 |
|---|---|---|---|
| `netdev_err` / `netdev_warn` / `netdev_info` | 否 | 无开关，始终输出 | `include/net/net_debug.h:20-26` |
| `netdev_dbg` / `netif_dbg` | 取决于 `CONFIG_DYNAMIC_DEBUG` | 是（有 DYNAMIC_DEBUG 时） | `include/net/net_debug.h:53-67` |
| `netif_*` 一族（`netif_err` 等） | 否 | 是，靠 `ethtool -s msglvl` | `include/net/net_debug.h:90-108` |
| `netdev_vdbg` / `netif_vdbg` | 是（要 `VERBOSE_DEBUG`） | 否 | `include/net/net_debug.h:70-80`、`:139-148` |
| tracepoint | 否（发行内核默认开 `CONFIG_TRACEPOINTS`） | 是 | §5 |
| `/proc/net/*`、`/sys/class/net/*` | 否 | 是 | §4、§10 |
| `DEBUG_NET_WARN_ON_ONCE` 的检查 | 是（`CONFIG_DEBUG_NET`） | 否 | `include/net/net_debug.h:151-155` |
| netdev 引用计数追踪 | 是（`CONFIG_NET_DEV_REFCNT_TRACKER`） | 否 | `net/Kconfig.debug:3` |
| drop monitor | 是（`CONFIG_NET_DROP_MONITOR`，可为模块） | 是 | `net/Kconfig:343` |
| 故障注入 | —— 本子系统没有，见 §12 | —— | §12 |

## §1 日志可见性：`netdev_*` 与 `netif_*` 两族

网卡驱动的日志宏**不在 `include/linux/netdevice.h` 里**，而在 `include/net/net_debug.h`（157 行）。`netdevice.h` 只留了两个：`netdev_WARN`（`include/linux/netdevice.h:5201`）和 `netdev_WARN_ONCE`（`:5205`）。找日志宏定义时直接翻错文件是常见弯路。

### §1.1 两族的分工

| 族 | 形态 | 第一参数 | 有没有开关 |
|---|---|---|---|
| `netdev_<level>` | `netdev_err(dev, fmt, ...)` | `struct net_device *` | 无（`_dbg` 除外） |
| `netdev_<level>_once` | 同上 | 同上 | 无，但只打一次 |
| `netif_<level>` | `netif_err(priv, type, dev, fmt, ...)` | 驱动私有结构（要有 `msg_enable` 字段） | 有，按 `type` 查 `msg_enable` 位 |
| `netif_cond_dbg` | `netif_cond_dbg(priv, type, netdev, cond, level, ...)` | 同上 | 有，且按 `cond` 决定级别 |

`netif_*` 一族的实现只有三行（`include/net/net_debug.h:90-94`）：

```c
90  #define netif_level(level, priv, type, dev, fmt, args...)	\
91  do {								\
92  	if (netif_msg_##type(priv))				\
93  		netdev_##level(dev, fmt, ##args);		\
94  } while (0)
```

也就是说 `netif_err` 最终落到 `netdev_err`，**只是前面多了一道 `msg_enable` 位的门**。`netif_msg_##type` 由宏拼接，`type` 必须是 `drv`/`probe`/`link`/… 之一（完整 15 个见 §3.1）。

### §1.2 可见性等级速查

| 宏 | 默认可见性 | 出处 |
|---|---|---|
| `netdev_emerg` / `alert` / `crit` / `err` / `warn` / `notice` / `info` | 始终输出，受 `printk` 级别过滤 | `include/net/net_debug.h:14-26` |
| `netdev_emerg_once` … `netdev_info_once` | 每个调用点**整个内核生命周期只打一次** | `include/net/net_debug.h:38-50` |
| `netdev_dbg` | 三种编译结果，见 §2.1 | `include/net/net_debug.h:53-67` |
| `netdev_vdbg` | 只在定义了 `VERBOSE_DEBUG` 时是真日志，否则编译期消失 | `include/net/net_debug.h:70-80` |
| `netif_err` 等七个 | 始终输出**且** `msg_enable` 对应位为 1 | `include/net/net_debug.h:96-108` |
| `netif_dbg` | 四道门，见 §2.2 | `include/net/net_debug.h:111-128` |
| `netdev_WARN` | 始终输出，带栈回溯 | `include/linux/netdevice.h:5201` |
| `netdev_WARN_ONCE` | 同上，只一次 | `include/linux/netdevice.h:5205` |

⚠️ **这一族只有 `_once` 变体，没有 `_ratelimited` 变体。** 全文件 157 行里 `_once` 出现在 `netdev_level_once`（`:28`）加七个封装（`:38`/`:40`/`:42`/`:44`/`:46`/`:48`/`:50`），`_ratelimited` 一个都没有。想限速只能自己调 `net_ratelimit()`（`net/core/utils.c:42` 导出）配合普通 `printk` 族，或者用 `net_dbg_ratelimited()` 那一组（定义在 `include/linux/net.h`，不接受 `net_device` 参数，所以打不出接口名）。这意味着**驱动收发路径上的高频错误如果用了 `netdev_err`，日志会打满**——实际驱动里这类点通常改用 `_once` 或干脆只加计数器。

`_once` 的实现值得看一眼，因为它决定了「只一次」的粒度：

```c
28  #define netdev_level_once(level, dev, fmt, ...)			\
29  do {								\
30  	static bool __section(".data.once") __print_once;	\
31  								\
32  	if (!__print_once) {					\
33  		__print_once = true;				\
34  		netdev_printk(level, dev, fmt, ##__VA_ARGS__);	\
35  	}							\
36  } while (0)
```

`__print_once` 是**每个调用点一个静态变量**，不是每个 `net_device` 一个。所以 8 张网卡里第一张打过之后，另外 7 张同样的错误一句都不会打。排查多网卡问题时看到「只有 eth0 报错」，先怀疑这个而不是真的只有 eth0 有问题。

### §1.3 `netdev_WARN` 为什么不是 `netdev_err`

`include/linux/netdevice.h:5196-5200` 的注释直说了理由：用 `WARN` 是为了拿到文件名、行号和栈回溯。展开是：

```c
5201 #define netdev_WARN(dev, format, args...)			\
5202 	WARN(1, "netdevice: %s%s: " format, netdev_name(dev),	\
5203 	     netdev_reg_state(dev), ##args)
```

`netdev_reg_state()`（`include/linux/netdevice.h:5178`）会在接口名后面追加注册状态后缀，取值 `" (uninitialized)"` / `""`（已注册）/ `" (unregistering)"` / `" (unregistered)"` / `" (released)"` / `" (dummy)"`，未知状态还会自己再 `WARN_ONCE(1, "%s: unknown reg_state %d\n")`（`:5189`）。读日志时**接口名后面括号里的那个词就是注册状态**，它经常是问题的直接线索：`(unregistering)` 说明有代码在设备拆除之后还在碰它。

`net/core/dev.c` 里核心自己用 `netdev_WARN` 的五处都属于「驱动违约」类：`:1647` LRO 关不掉、`:1668` GRO_HW 关不掉、`:9693` 上层设备关不掉某个 feature、`:10157` `"Buggy VLAN acceleration in driver!"`、`:10504` `"run_todo but not unregistering"`。看到这五句中任何一句，问题都在驱动侧而不是核心。

## §2 调试开关的多道门

### §2.1 `netdev_dbg` 的三种编译结果

`include/net/net_debug.h:53-67` 是三分支：

```
CONFIG_DYNAMIC_DEBUG（或 DYNAMIC_DEBUG_CORE + DYNAMIC_DEBUG_MODULE）
    → dynamic_netdev_dbg(...)          :55-58   运行期可开关
elif  定义了 DEBUG（驱动自己 #define DEBUG 或 Makefile 加 -DDEBUG）
    → netdev_printk(KERN_DEBUG, ...)   :60-61   编译进去，始终输出
else
    → ({ if (0) netdev_printk(...); }) :63-67   编译期消失，但仍做格式检查
```

第三分支的 `if (0)` 写法保留了 `printf` 格式检查，所以格式串写错在任何配置下都会编译告警——但**日志本身在发行内核的多数驱动里是不存在的代码**。这条路上有一个容易误判的点：`grep -n netdev_dbg drivers/net/ethernet/<vendor>/` 有一堆命中，不等于运行时能看到任何一条。

### §2.2 `netif_dbg` 的四道门

`netif_dbg`（`include/net/net_debug.h:111-128`）在 `netdev_dbg` 的三分支之外又套了 `netif_msg_##type(priv)`。完整的门是：

```
门 1  编译期：CONFIG_DYNAMIC_DEBUG 有没有开？
        没开且没 DEBUG → 代码不存在，后面三道门无意义
门 2  运行期：msg_enable 的对应位是 1 吗？
        ethtool -s <if> msglvl <name> on   （见 §3）
门 3  运行期：dynamic_debug 这条 pr_debug 站点开了吗？
        echo 'file <驱动>.c +p' > /sys/kernel/debug/dynamic_debug/control
门 4  printk 级别：KERN_DEBUG 有没有被 console_loglevel 挡掉？
        dmesg 能看到，串口 console 默认看不到
```

⚠️ **门 2 和门 3 是「与」关系，而且顺序上门 2 在前。** `dynamic_debug` 把某个站点打开了却看不到输出，八成是 `msg_enable` 位没开；反过来 `msglvl` 全开了还是没输出，就去查门 3。这两道门分属两个完全无关的机制（一个是驱动私有字段，一个是内核基础设施），互相不知道对方存在，也没有任何地方会提示你「另一道门是关的」。

### §2.3 `netif_cond_dbg`：把级别当成条件

`include/net/net_debug.h:131-137` 定义了一个少见但好用的形态，源码注释写的是「if @cond then downgrade to debug, else print at @level」：

```c
131 #define netif_cond_dbg(priv, type, netdev, cond, level, fmt, args...) \
132 	do {                                                          \
133 		if (cond)                                             \
134 			netif_dbg(priv, type, netdev, fmt, ##args);   \
135 		else                                                  \
136 			netif_ ## level(priv, type, netdev, fmt, ##args); \
137 	} while (0)
```

典型用法是「首次探测失败降级为 debug、后续失败按 err 打」这类语义。读驱动日志时如果同一句话有时候出现有时候不出现，检查它是不是走了这个宏——**不是限速，是条件降级**，两者的排查方向完全不同。

## §3 `msg_enable`：15 个位与它们的字符串名

### §3.1 位定义与字符串名的对应

官方原文见 `Documentation/networking/netif-msg.rst`（95 行，讲的是这套位当年的设计意图）。位枚举在 `include/linux/netdevice.h:4279-4298`，共 15 个，以 `NETIF_MSG_CLASS_COUNT` 收尾，并有 `static_assert(NETIF_MSG_CLASS_COUNT <= 32)`（`:4301`）保证塞得进一个 `u32`。用户态看到的字符串名来自另一个文件的数组 `netif_msg_class_names[][ETH_GSTRING_LEN]`（`net/ethtool/common.c:358-374`），同样有 `static_assert(ARRAY_SIZE(...) == NETIF_MSG_CLASS_COUNT)`（`:375`）把两处钉在一起。

| 位 | 掩码宏 | 判定函数 | `ethtool` 字符串 | 语义 |
|---|---|---|---|---|
| 0 | `NETIF_MSG_DRV`（`:4306`） | `netif_msg_drv`（`:4322`） | `drv` | 驱动级事件（probe 之后的常规路径） |
| 1 | `NETIF_MSG_PROBE` | `netif_msg_probe` | `probe` | 探测/初始化阶段 |
| 2 | `NETIF_MSG_LINK` | `netif_msg_link` | `link` | 链路 up/down、速率协商 |
| 3 | `NETIF_MSG_TIMER` | `netif_msg_timer` | `timer` | 定时器/轮询 |
| 4 | `NETIF_MSG_IFDOWN` | `netif_msg_ifdown` | `ifdown` | 接口关闭路径 |
| 5 | `NETIF_MSG_IFUP` | `netif_msg_ifup` | `ifup` | 接口打开路径 |
| 6 | `NETIF_MSG_RX_ERR` | `netif_msg_rx_err` | `rx_err` | 收方向错误 |
| 7 | `NETIF_MSG_TX_ERR` | `netif_msg_tx_err` | `tx_err` | 发方向错误 |
| 8 | `NETIF_MSG_TX_QUEUED` | `netif_msg_tx_queued` | `tx_queued` | 每个发送描述符入队 |
| 9 | `NETIF_MSG_INTR` | `netif_msg_intr` | `intr` | 中断 |
| 10 | `NETIF_MSG_TX_DONE` | `netif_msg_tx_done` | `tx_done` | 发送完成回收 |
| 11 | `NETIF_MSG_RX_STATUS` | `netif_msg_rx_status` | `rx_status` | 每个接收描述符 |
| 12 | `NETIF_MSG_PKTDATA` | `netif_msg_pktdata` | `pktdata` | 报文内容十六进制转储 |
| 13 | `NETIF_MSG_HW` | `netif_msg_hw` | `hw` | 硬件寄存器层面 |
| 14 | `NETIF_MSG_WOL`（`:4320`） | `netif_msg_wol`（`:4336`） | `wol` | Wake-on-LAN |

判定函数全部是同一个形状 `((p)->msg_enable & NETIF_MSG_X)`，掩码由 `__NETIF_MSG_BIT`（`:4303`）和 `__NETIF_MSG`（`:4304`）两层宏从枚举值算出。

⚠️ **`tx_queued` / `tx_done` / `rx_status` / `pktdata` 这四位是每包一条日志。** 在跑流量的接口上打开它们等于把机器打死——不是慢一点，是 `printk` 把 CPU 吃满、串口卡住。要用就先把流量停掉，或者只对着一条手工发的包开。

### §3.2 怎么读、怎么写

```
读：  ethtool <if>  的输出末尾一行   Current message level: 0x00000007 (7)
                                     drv probe link
写：  ethtool -s <if> msglvl 0x2f            按整数值整体覆盖
      ethtool -s <if> msglvl link on         按名字单个打开
      ethtool -s <if> msglvl pktdata off     按名字单个关闭
```

字符串必须逐字匹配 §3.1 表里第四列——那些字符串就是 `net/ethtool/common.c:358-374` 数组里的字面量，不是 `NETIF_MSG_*` 宏名的小写。写 `msglvl rx_error on` 会失败，正确的是 `rx_err`。

底层通路两条并存：ioctl 侧走 `ethtool_ops` 的 `get_msglevel`（`include/linux/ethtool.h:649`）/ `set_msglevel`（`:650`），kerneldoc 在 `:471-473`；netlink 侧走 `net/ethtool/debug.c`（128 行），请求处理 `debug_prepare_data` 在 `:24`，回包组装 `debug_fill_reply` 在 `:54`，ops 表 `ethnl_debug_request_ops` 在 `:66`，设置入口 `ethnl_set_debug` 在 `:86`。

⚠️ **驱动没有实现 `get_msglevel`/`set_msglevel` 时，`ethtool <if>` 的输出里就没有 `Current message level` 那一行，`-s msglvl` 返回 `-EOPNOTSUPP`。** 这时候 `netif_*` 一族的日志永远只有驱动自己在 probe 里给 `msg_enable` 设的那个初值，运行期改不了。这不是罕见情况——`ethtool_ops` 有 68 个槽位，多数驱动只填十几个。

### §3.3 `netif_msg_init()` 的「低 N 位」陷阱

多数驱动有一个 `debug` 模块参数，走 `netif_msg_init()`（`include/linux/netdevice.h:4338-4347`）转成 `msg_enable`。它的三条规则是：

| 传入 `debug_value` | 返回 |
|---|---|
| `< 0` 或 `>= 32` | 驱动传入的 `default_msg_enable_bits`（即模块参数当作「没给」） |
| `== 0` | `0`（全关） |
| 其它 | `(1U << debug_value) - 1` |

第三条是陷阱：`debug=3` 不是「打开第 3 位（link）」，而是「打开第 0、1、2 位」，也就是 `drv probe link` 三个。想只打开 `link` 一位得走 `ethtool -s <if> msglvl link on`，模块参数做不到。第一条也反直觉：`debug=99` 不会报错，会静默地退回驱动默认值。

## §4 可写调试文件的 setter 语义

`/sys/class/net/<if>/` 下的属性由 `net/core/net-sysfs.c`（2077 行）实现，读写属性统一走 `NETDEVICE_SHOW_RW` 宏（`:74-76`）。**写进去的值不一定就是存起来的值**，逐个说清。

### §4.1 NAPI 相关的两个：多一道 `CAP_NET_ADMIN`

| 文件 | 单位 | setter | 额外权限门 |
|---|---|---|---|
| `gro_flush_timeout` | 纳秒 | `change_gro_flush_timeout()`（`net/core/net-sysfs.c:389-393`） | 有 |
| `napi_defer_hard_irqs` | 次数 | `change_napi_defer_hard_irqs()`（`net/core/net-sysfs.c:406-410`） | 有 |

两个 store 函数（`:395-403` 和 `:412-420`）开头都是同一句：

```c
	if (!capable(CAP_NET_ADMIN))
		return -EPERM;
```

⚠️ **文件权限是 0644，但 root 之外的用户即便被 chown 了也写不进去**——权限检查在 `capable()` 而不在文件模式位。容器里 `CAP_NET_ADMIN` 常被裁掉，于是「文件看着可写、写就 EPERM」。同目录下的 `mtu`（`:364`）、`flags`（`:376`）、`tx_queue_len`（`:387`）走的是普通 `netdev_store` 路径，没有这道额外的门。

两个 change 函数都用 `WRITE_ONCE()` 落值，读侧在 NAPI 轮询热路径上不加锁读——所以**改这两个值是即时生效的，不需要 down/up 接口**，但也意味着不同队列可能在同一瞬间看到新旧两个值。

### §4.2 `tx_maxrate`：ndo 不存在就什么都不做

`tx_maxrate_store()`（`net/core/net-sysfs.c:1267-1300`）的顺序是关键：

```
:1280  if (!dev->netdev_ops->ndo_set_tx_maxrate)  → 提前返回
:1292  err = dev->netdev_ops->ndo_set_tx_maxrate(dev, index, rate)
:1296  queue->tx_maxrate = rate      ← 只有 ndo 成功了才缓存
```

⚠️ **`queue->tx_maxrate` 是「已生效值」的缓存，不是「用户请求值」。** 所以 `echo` 写进去之后回读，读到旧值就说明驱动那一步失败了，读到新值才说明硬件真的收到了。属性本体是 `queue_tx_maxrate = __ATTR_RW(tx_maxrate)`（`:1302-1303`），挂在 `netdev_queue_default_attrs[]`（`:1604-1612`）里，且只在 `CONFIG_XPS` 打开时才注册。

### §4.3 BQL 五个文件：一个只读、一个换单位

BQL 的属性在 `/sys/class/net/<if>/queues/tx-N/byte_queue_limits/`，目录名来自 `dql_group` 的 `.name = "byte_queue_limits"`（`net/core/net-sysfs.c:1411-1414`），整组在 `#ifdef CONFIG_BQL` 里（`:1415`）。

| 文件 | 权限 | 写进去的值 | 存起来的值 | 出处 |
|---|---|---|---|---|
| `limit` | 0644 | 字节数 | 同值，`> DQL_MAX_LIMIT` 返回 `-EINVAL` | `BQL_ATTR(limit, limit)`（`:1398`） |
| `limit_max` | 0644 | 字节数 | 同值 | `BQL_ATTR(limit_max, max_limit)`（`:1399`） |
| `limit_min` | 0644 | 字节数 | 同值 | `BQL_ATTR(limit_min, min_limit)`（`:1400`） |
| `hold_time` | 0644 | 毫秒 | jiffies | `bql_hold_time_attribute`（`:1366-1368`） |
| `inflight` | 0444 | —— | 不存，读时算 | `bql_inflight_attribute`（`:1378-1379`） |

两个需要单独记的点：

`hold_time` 写的是毫秒、存的是 jiffies——`bql_set_hold_time()`（`:1350-1363`）做 `dql->slack_hold_time = msecs_to_jiffies(value)`，读侧 `bql_show_hold_time()`（`:1342`）用 `jiffies_to_msecs()` 转回来。**在 `CONFIG_HZ=250` 的内核上写 1 会被量化成 4**（`msecs_to_jiffies(1)` 向上取整到 1 jiffy = 4 ms），回读就不是 1。要精细控制得先确认内核的 HZ。

`inflight` 是**算出来的差值**而不是存的字段：`bql_show_inflight()`（`:1370`）返回 `dql->num_queued - dql->num_completed`。它是「已经交给硬件、还没回收完成」的字节数，也是判断发送侧积压的最直接数字。⚠️ 这个值单调递增的两个计数器之差，**如果驱动漏调 `netdev_tx_completed_queue()`，`inflight` 会永远涨、`limit` 被压到最小、吞吐掉到地板**——而不会有任何日志。这是 BQL 类 bug 的典型现象，`04-extensions.md` §12.2 里记的那 6 个只有发送侧、没有完成侧配对的驱动文件就是这一类风险点。

`limit` 的写入校验只有上界（`bql_set()` 的 `:1332-1334` 判 `value > DQL_MAX_LIMIT` 返回 `-EINVAL`），没有下界——写 0 是合法的，效果是让 BQL 把队列压到最紧。

### §4.4 其余可写属性一览

| 路径 | 权限 | 备注 |
|---|---|---|
| `/sys/class/net/<if>/carrier` | RW | `DEVICE_ATTR_RW(carrier)`（`net/core/net-sysfs.c:201`），写它是**强行改 carrier 状态**，仅调试用 |
| `/sys/class/net/<if>/ifalias` | RW | `ifalias_store`（`:423`），`DEVICE_ATTR_RW(ifalias)`（`:466`） |
| `/sys/class/net/<if>/proto_down` | RW | `:493` |
| `/sys/class/net/<if>/threaded` | RW | `DEVICE_ATTR_RW(threaded)`（`:619`），把 NAPI 轮询搬到内核线程 |
| `/sys/class/net/<if>/queues/rx-N/rps_cpus` | 0644 | `rps_cpus_attribute`（`:980-981`），仅 `CONFIG_RPS` |
| `/sys/class/net/<if>/queues/rx-N/rps_flow_cnt` | 0644 | `rps_dev_flow_table_cnt`（`:983-985`），仅 `CONFIG_RPS` |
| `/sys/class/net/<if>/queues/tx-N/xps_cpus` | RW | `xps_cpus_attribute`（`:1539-1540`），仅 `CONFIG_XPS` |
| `/sys/class/net/<if>/queues/tx-N/xps_rxqs` | RW | `xps_rxqs_attribute`（`:1600-1601`），仅 `CONFIG_XPS` |

`rx_queue_default_attrs[]`（`:988-994`）里只有那两个 RPS 属性，两个都在 `#ifdef CONFIG_RPS` 内——**关掉 `CONFIG_RPS` 之后 `queues/rx-N/` 目录是空的**，看到空目录不要以为是队列没建起来。只读但值得看的有 `queues/tx-N/traffic_class`（`traffic_class_show` `:1223`）和 `queues/tx-N/trans_timeout`（`queue_trans_timeout` `:1306`，读 `net/core/net-sysfs.c:1207` 的 `atomic_long_read(&queue->trans_timeout)`）。后者是**这条发送队列历史上被看门狗判超时的次数**，字段声明在 `include/linux/netdevice.h:624`（注释里直接写了 sysfs 路径，`:622`），唯一的递增点在 `net/sched/sch_generic.c:525`。它是排查间歇性发送卡死最省事的入口：不用抓 dmesg，直接看这个数字有没有在涨。

## §5 tracepoint 全清单

网络侧的 tracepoint 分散在九个头文件里，合计 1893 行：

| 头文件 | 行数 | 覆盖 | 本篇是否详列 |
|---|---|---|---|
| `include/trace/events/net.h` | 319 | 收发主路径 | §5.1 |
| `include/trace/events/skb.h` | 93 | skb 释放 | §5.2 |
| `include/trace/events/napi.h` | 44 | NAPI 轮询 | §5.3 |
| `include/trace/events/qdisc.h` | 153 | 排队规则 | §5.4 |
| `include/trace/events/page_pool.h` | 117 | 页池 | §5.5 |
| `include/trace/events/xdp.h` | 409 | XDP | §5.6 |
| `include/trace/events/devlink.h` | 234 | devlink | §5.7 |
| `include/trace/events/sock.h` | 269 | 套接字层 | 否（协议栈侧） |
| `include/trace/events/neigh.h` | 255 | 邻居子系统 | 否（协议栈侧） |

⚠️ **这七个头文件里 `TP_CONDITION` 出现零次。** 也就是说本子系统的 tracepoint 全部是「注册了就一定触发」，没有任何一个带内建过滤条件。这一点对排查很有利：`echo 1 > .../enable` 之后没看到事件，就说明代码路径真的没走到，不需要怀疑「条件没满足所以被跳过」。反面是**没有内建限速**，`net_dev_start_xmit` 这类每包触发的点在满速接口上会把 ring buffer 瞬间冲掉，要靠 `trace-cmd` 的 filter 或者加大 buffer 自己扛。

### §5.1 `net.h`：收发主路径

| tracepoint | 行 | 触发点语义 |
|---|---|---|
| `net_dev_start_xmit` | `:14` | 即将调用驱动的 `ndo_start_xmit` |
| `net_dev_xmit` | `:72` | `ndo_start_xmit` 返回之后 |
| `net_dev_xmit_timeout` | `:99` | 看门狗判定超时（见 §8） |
| `net_dev_queue` | `:144` | skb 交给发送队列 |
| `netif_receive_skb` | `:151` | 进入协议栈接收 |
| `netif_rx` | `:158` | 走 backlog 的接收 |
| `napi_gro_frags_entry` / `_exit` | `:229` / `:281` | GRO frags 收包进/出 |
| `napi_gro_receive_entry` / `_exit` | `:236` / `:288` | GRO 收包进/出 |
| `netif_receive_skb_entry` / `_exit` | `:243` / `:295` | 单包接收进/出 |
| `netif_receive_skb_list_entry` / `_exit` | `:250` / `:309` | 批量接收进/出 |
| `netif_rx_entry` / `_exit` | `:257` / `:302` | backlog 接收进/出 |

三个 `DECLARE_EVENT_CLASS` 决定了字段布局：`net_dev_template`（`:122`）给 `net_dev_queue`/`netif_receive_skb`/`netif_rx`，`net_dev_rx_verbose_template`（`:165`）给五个 `_entry`，`net_dev_rx_exit_template`（`:264`）给五个 `_exit`。同一个 class 里的 tracepoint 字段完全一样——所以 `netif_rx_entry` 和 `netif_receive_skb_entry` 的输出格式无法区分来源，只能靠事件名。

排查发送失败最有用的是 `net_dev_xmit`，字段在 `include/trace/events/net.h:81-96`：

| 字段 | 类型 | 来源 |
|---|---|---|
| `skbaddr` | `void *` | skb 指针，用于和 `kfree_skb` 对账 |
| `len` | `unsigned int` | 报文长度 |
| `rc` | `int` | `ndo_start_xmit` 的返回值 |
| `name` | `__string` | `dev->name` |

`TP_printk` 是 `"dev=%s skbaddr=%p len=%u rc=%d"`。`rc` 就是驱动返回的 `NETDEV_TX_OK`（0）或 `NETDEV_TX_BUSY`——**持续出现非零 `rc` 说明驱动在反压**，配合 §4.3 的 `inflight` 一起看。

### §5.2 `skb.h`：丢包与正常释放

三个 tracepoint：`kfree_skb`（`:24`）、`consume_skb`（`:54`）、`skb_copy_datagram_iovec`（`:71`）。

`kfree_skb` 的字段：`skbaddr`、`location`（谁调的，函数地址）、`protocol`（`ntohs(skb->protocol)`）、`reason`（`enum skb_drop_reason`）。`TP_printk` 是 `"skbaddr=%p protocol=%u location=%p reason: %s"`，最后那个 `%s` 由 `__print_symbolic(__entry->reason, DEFINE_DROP_REASON(FN, FNe))` 展开成可读的原因名——**这是网络排查里信息密度最高的一条 tracepoint**，详见 §7。

⚠️ **`consume_skb` 只有 `skbaddr` 一个字段，没有 `reason`。** 两者的分工是：正常消费走 `consume_skb`，异常丢弃走 `kfree_skb`。所以「`kfree_skb` 有事件」本身就是异常信号，不需要看别的；而某个 skb 只在 `consume_skb` 里出现说明它被正常处理完了。混着看会得出「丢包率 100%」的错误结论——因为绝大多数包压根不出现在 `kfree_skb` 里。

### §5.3 `napi.h`：只有一个

整个文件 44 行，唯一的 tracepoint 是 `napi_poll`（`include/trace/events/napi.h:14`）：

| 字段 | 来源 |
|---|---|
| `napi` | `struct napi_struct *` 指针 |
| `dev_name` | `napi->dev ? napi->dev->name : NO_DEV` |
| `work` | 本次轮询实际处理的包数 |
| `budget` | 本次给的预算 |

`TP_printk` 是 `"napi poll on napi struct %p for device %s work %d budget %d"`。这一条能回答三个问题：NAPI 到底有没有被调度（有事件 = 中断来了）、每次收多少包（`work`）、有没有打满预算（`work == budget` 说明还有包在排队、这次被 budget 截断了）。⚠️ **`dev_name` 可能是字面量 `NO_DEV`**，那是 `napi->dev` 为 NULL 的情况（例如 backlog 的伪 NAPI），不是取名失败。

### §5.4 `qdisc.h`：五个

`qdisc_dequeue`（`:14`）、`qdisc_enqueue`（`:49`）、`qdisc_reset`（`:77`）、`qdisc_destroy`（`:102`）、`qdisc_create`（`:127`）。对网卡驱动排查的用途主要是分清「包卡在 qdisc 里」还是「包已经交给驱动了」：`qdisc_dequeue` 有事件而 `net_dev_start_xmit` 没有，中间那一段就是嫌疑区。

### §5.5 `page_pool.h`：四个

`page_pool_release`（`:14`）、`page_pool_state_release`（`:42`）、`page_pool_state_hold`（`:67`）、`page_pool_update_nid`（`:92`）。用于排查页池泄漏：驱动卸载时 `page_pool_release` 迟迟不完成，说明还有页没归还——通常是 skb 上的页没被回收（少调了 `skb_mark_for_recycle()`）。

### §5.6 `xdp.h`：十二个

`xdp_exception`（`:28`）、`xdp_bulk_tx`（`:53`）、`xdp_redirect`（`:140`）、`xdp_redirect_err`（`:149`）、`xdp_redirect_map`（`:171`）、`xdp_redirect_map_err`（`:180`）、`xdp_cpumap_kthread`（`:189`）、`xdp_cpumap_enqueue`（`:232`）、`xdp_devmap_xmit`（`:267`）、`mem_disconnect`（`:320`）、`mem_connect`（`:347`）、`mem_return_failed`（`:381`）。四个 redirect 事件共用 `DECLARE_EVENT_CLASS(xdp_redirect_template)`（`:89`）。排 XDP 问题的第一站是 `xdp_exception`——它对应程序返回了驱动不支持的 action。

### §5.7 `devlink.h`：六个

`devlink_hwmsg`（`:17`）、`devlink_hwerr`（`:52`）、`devlink_health_report`（`:81`）、`devlink_health_recover_aborted`（`:112`）、`devlink_health_reporter_state_update`（`:146`）、`devlink_trap_report`（`:177`）。这一组是厂商网卡（主要是 mlx5、nfp）的固件侧诊断通道，见 §13。

### §5.8 怎么开

```
按事件开：   echo 1 > /sys/kernel/debug/tracing/events/net/net_dev_xmit/enable
按组开：     echo 1 > /sys/kernel/debug/tracing/events/net/enable
读：         cat /sys/kernel/debug/tracing/trace
按接口过滤： echo 'name == "eth0"' > .../events/net/net_dev_xmit/filter
```

事件目录名是头文件里 `TRACE_SYSTEM` 的值，不是文件名：`net.h` → `events/net/`，`skb.h` → `events/skb/`，`napi.h` → `events/napi/`。过滤器可用的字段名就是 §5.1–§5.7 各表里列的那些。

## §6 成对 tracepoint 怎么读

`net.h` 里有五对 `_entry`/`_exit`。逐对打开调用点确认之后，结论是**五对全部无条件配对**（`netif_rx` 那一对有两处调用点，都列在下面）：

| 对 | entry 调用点 | exit 调用点 | 两点之间有提前退出吗 |
|---|---|---|---|
| `napi_gro_frags_*` | `net/core/gro.c:784` | `net/core/gro.c:787` | 没有，中间只有 `napi_frags_finish` 一行 |
| `napi_gro_receive_*` | `net/core/gro.c:654` | `net/core/gro.c:659` | 没有，中间只有 `skb_gro_reset_offset` 和 `napi_skb_finish` |
| `netif_rx_*`（内部） | `net/core/dev.c:5056` | `net/core/dev.c:5058` | 没有 |
| `netif_rx_*`（外部） | `net/core/dev.c:5089` | `net/core/dev.c:5091` | 没有，外面包着 `local_bh_disable/enable` |
| `netif_receive_skb_*` | `net/core/dev.c:5829` | `net/core/dev.c:5832` | 没有 |
| `netif_receive_skb_list_*` | `net/core/dev.c:5854-5857` | `net/core/dev.c:5859` | 没有 |

所以在这个子系统里，**只看到 `_entry` 没看到 `_exit` 是真的故障信号**——中间那段代码崩了、死锁了、或者进了不返回的路径。这一点不能推广到别的子系统：很多子系统的 `_entry`/`_complete` 之间有提前 `return`，缺 `_complete` 是正常的。

两个需要单独交代的细节：

批量接收那一对的 entry 是**逐包发一次**，而且套在一个 `_enabled()` 检查里（`net/core/dev.c:5854-5857`）：

```c
	if (trace_netif_receive_skb_list_entry_enabled()) {
		list_for_each_entry(skb, head, list)
			trace_netif_receive_skb_list_entry(skb);
	}
```

一次批量接收 N 个包会产生 N 个 entry 事件、**1 个** exit 事件。按「entry 数 == exit 数」对账会得出全都不配对的错误结论。

⚠️ **`netif_receive_skb_list_exit` 的返回值恒为 0**，因为它的调用点写的就是 `trace_netif_receive_skb_list_exit(0)`（`net/core/dev.c:5859`）——`netif_receive_skb_list()` 本身是 `void`，没有返回值可传。函数的 kerneldoc（`net/core/dev.c:5838-5840`）解释了为什么是 void：单包版本的返回值本来就通常被忽略，对一个列表更没有意义。**结果是这条 tracepoint 的 `ret` 字段是个返回值黑洞——它永远是 0，不代表「全部成功」。** 想知道批量接收里有没有包被丢，只能看 `kfree_skb`。

另外 `netif_receive_skb_list()` 在 `:5851-5852` 有一个 `list_empty(head)` 的提前返回，但它发生在 entry tracepoint **之前**，所以不会留下不配对的 entry。这个次序是刻意的，改动这段代码时要注意别把它调过来。

## §7 丢包定位

### §7.1 `kfree_skb` 与 `consume_skb` 的分工

两个函数对应两种语义，接口在 `include/linux/skbuff.h`：

| 声明 | 行 | 语义 |
|---|---|---|
| `kfree_skb_reason(skb, reason)` | `:1212` | 异常丢弃，带原因 |
| `kfree_skb(skb)` | `:1220` | 等于 `kfree_skb_reason(skb, SKB_DROP_REASON_NOT_SPECIFIED)` |
| `kfree_skb_list` | `:1225` | 列表版 |
| `skb_dump` | `:1226` | 把 skb 的头部和内容打到 dmesg |

实现在 `net/core/skbuff.c`：`kfree_skb_reason()` 在 `:885`（kerneldoc `:876`，`EXPORT_SYMBOL` `:895`），`kfree_skb_list_reason` 的导出在 `:907`，`__kfree_skb` 的导出在 `:873`，`kfree_skb_partial` 在 `:5432`。

⚠️ **`kfree_skb(skb)` 是「原因未指定」而不是「无原因」。** 它在 tracepoint 里显示成 `NOT_SPECIFIED`，跟真正分类过的原因混在同一条 tracepoint 里。大量 `NOT_SPECIFIED` 只说明调用方还没被改造成带原因的版本，不代表原因不明——去看 `location` 字段的函数地址，那才是真线索。

`kfree_skb_reason()` 内部有一道范围检查：`DEBUG_NET_WARN_ON_ONCE(reason <= 0 || reason >= SKB_DROP_REASON_MAX)`（`net/core/skbuff.c:890`）。⚠️ 这道检查**只在 `CONFIG_DEBUG_NET` 打开时存在**（见 §9.1）——所以自己写驱动传了越界的 reason，在发行内核上不会有任何提示，tracepoint 里会打出一个无意义的数字。

### §7.2 64 个 drop reason

原因表在 `include/net/dropreason.h`（323 行）。文件的组织方式是一个宏 `DEFINE_DROP_REASON(FN, FNe)`（`:6`）列出全部条目，然后 `enum skb_drop_reason`（`:78`）给出实际枚举值。全文件 `FN(` 出现 **64** 次（重跑：`grep -c 'FN(' include/net/dropreason.h`）。

| 原因 | 行 | 含义 |
|---|---|---|
| `FN(NOT_SPECIFIED)` | `:7` | 未分类（`kfree_skb()` 的默认值） |
| `FN(NO_SOCKET)` | `:8` | 找不到目标套接字 |
| `FN(PKT_TOO_SMALL)` | `:9` | 报文比头部还短 |
| `FN(TCP_CSUM)` | `:10` | TCP 校验和错 |
| `FN(SOCKET_FILTER)` | `:11` | 被 socket filter 拒绝 |
| `FN(UDP_CSUM)` | `:12` | UDP 校验和错 |
| `FN(NETFILTER_DROP)` | `:13` | netfilter 判丢 |

枚举侧 `SKB_DROP_REASON_NOT_SPECIFIED` 在 `:84`，哨兵 `SKB_DROP_REASON_MAX` 在 `:305`。给用户态看的字符串表是 `const char * const drop_reasons[]`（`net/core/skbuff.c:96`）——tracepoint 的 `__print_symbolic` 和 drop monitor 的 netlink 消息都从这里取名字。

对网卡驱动排查最相关的一批是校验和类（`TCP_CSUM`/`UDP_CSUM`/`IP_CSUM`）和长度类（`PKT_TOO_SMALL`）：**大量校验和错通常不是链路问题，而是驱动错报了 `CHECKSUM_UNNECESSARY` 或者硬件卸载的偏移算错了**。核心侧遇到硬件校验和不可信时会打一句 `netdev_err(dev, "hw csum failure\n")`（`net/core/dev.c:3491`），这句话就是「驱动说校验和已验过、核心自己重算发现不对」。

### §7.3 drop monitor

`net/core/drop_monitor.c`（1771 行）把 `kfree_skb` tracepoint 包装成 netlink 通道，配置项 `config NET_DROP_MONITOR` 在 `net/Kconfig:343`（tristate，`depends on INET && TRACEPOINTS`）。

| 要素 | 位置 | 说明 |
|---|---|---|
| netlink family | `net/core/drop_monitor.c:94` | `net_drop_monitor_family` |
| 默认模式 | `:103` | `net_dm_alert_mode = NET_DM_ALERT_MODE_SUMMARY` |
| 组播组 | `:183` | `dropmon_mcgrps` |
| 告警命令 | `:149` | `NET_DM_CMD_ALERT` |
| 硬件 trap 名长度上限 | `:62` | `NET_DM_MAX_HW_TRAP_NAME_LEN 40` |
| skb 控制块 | `:128` | `NET_DM_SKB_CB` |

⚠️ **默认模式是 summary（只报数量和位置汇总），不是 packet（带报文内容）。** 用 `dropwatch` 之类工具看不到报文内容时先切模式，不要以为是权限或编译问题。它自己也有一道 reason 范围检查（`:507`）：`if (unlikely(reason >= SKB_DROP_REASON_MAX || reason <= 0))`，越界的会被归并成未指定——这道检查跟 §7.1 那道不同，它**不**依赖 `CONFIG_DEBUG_NET`。

### §7.4 三份统计数字，三种口径

| 来源 | 数据从哪来 | 口径 |
|---|---|---|
| `/proc/net/dev`、`ip -s link` | `dev->stats` 或 `ndo_get_stats64` | 核心定义的标准字段，驱动填 |
| `ethtool -S <if>` | `ethtool_ops` 的 `get_ethtool_stats` + `get_strings` | **完全由驱动定义**，名字和含义各厂商不同 |
| `ethtool -S <if> --groups` 等标准化组 | `get_eth_mac_stats` 等专用槽位 | 核心定义字段、驱动填，跨厂商可比 |

⚠️ **`ethtool -S` 的每一行都是驱动自定义的**，同名字段在两个厂商的网卡上可能是完全不同的东西。跨机器对比数字之前先确认是同一个驱动。想要可比的数字要用标准化统计槽位，`include/linux/ethtool.h` 里相关的有：`get_pause_stats`（`:676`）、`get_fec_stats`（`:733`）、`get_ethtool_phy_stats`（`:739`）、`get_eth_mac_stats`（`:750`）、`get_eth_ctrl_stats`（`:752`）、`get_rmon_stats`（`:754`）；netlink 侧实现在 `net/ethtool/stats.c`（412 行）。

`ethtool -S` 的通路要两个槽位同时存在。`ethtool_get_stats()`（`net/ethtool/ioctl.c:2037`）开头就是：

```c
	if (!ops->get_ethtool_stats || !ops->get_sset_count)
		return -EOPNOTSUPP;
```

⚠️ **只填了 `get_ethtool_stats` 而漏了 `get_sset_count` 的驱动，`ethtool -S` 直接报 not supported**，看起来像完全没有统计——这是驱动侧的常见漏填。字符串一侧 `__ethtool_get_sset_count()`（`:164-193`）的判断更严格：末尾是 `if (ops->get_sset_count && ops->get_strings) return ops->get_sset_count(dev, sset); else return -EOPNOTSUPP;`，**两个都要有**。

## §8 看门狗：网卡驱动最重要的单条故障信号

### §8.1 那条日志从哪来

```
NETDEV WATCHDOG: CPU: 2: transmit queue 3 timed out 5312 ms
```

它出自 `dev_watchdog()`（`net/sched/sch_generic.c:495`）。判定为超时之后的动作次序是：

| 行 | 动作 |
|---|---|
| `net/sched/sch_generic.c:525` | `atomic_long_inc(&txq->trans_timeout)`（就是 §4.4 那个 sysfs 计数） |
| `:533` | `trace_net_dev_xmit_timeout(dev, i)` |
| `:534` | `netdev_crit(dev, "NETDEV WATCHDOG: ...")` |
| `:538` | `dev->netdev_ops->ndo_tx_timeout(dev, i)` |

tracepoint 在日志**之前**触发，字段（`include/trace/events/net.h:99-121`）是 `name`、`driver`、`queue_index`，`TP_printk` 为 `"dev=%s driver=%s queue=%d"`。`driver` 由 `netdev_drivername(dev)`（`net/core/dev.c:11361`）算出——⚠️ **当 `dev->dev.parent` 为 NULL 时它返回空串**（`:11367-11369`），所以虚拟设备的这个字段是空的，不是取名失败。

### §8.2 没有 `ndo_tx_timeout` 就没有看门狗

`__netdev_watchdog_up()`（`net/sched/sch_generic.c:553`，`EXPORT_SYMBOL_GPL` 在 `:564`）只在一个条件成立时才装定时器（`:555`）：

```c
	if (dev->netdev_ops->ndo_tx_timeout) {
```

⚠️ **驱动没实现 `ndo_tx_timeout`（`include/linux/netdevice.h` 的 `net_device_ops` 里的槽位），整条看门狗机制对它就是不存在的。** 现象是：发送真的卡死了，dmesg 一个字都不打，`trans_timeout` 永远是 0，接口状态一切正常。排查「发送卡死但没有任何日志」时，第一件事是确认这个槽位有没有填，而不是加大 `watchdog_timeo`。

其余控制点：`dev_watchdog_up()` 在 `:566`、`dev_watchdog_down()` 在 `:571`，`:593` 是超时处理完之后重新武装，两个调用点在 `:1263` 和 `:1362`，定时器本体的初始化 `timer_setup(&dev->watchdog_timer, dev_watchdog, 0)` 在 `:1484`。超时阈值来自 `dev->watchdog_timeo`，由驱动在 probe 里自己设——设成 0 会走核心默认值。

### §8.3 看到这条日志之后看什么

| 下一步 | 看什么 | 说明 |
|---|---|---|
| 1 | `net_dev_xmit` 的 `rc` 字段 | 驱动是不是一直返回 `NETDEV_TX_BUSY` |
| 2 | BQL `inflight` | 是不是完成侧计数漏了（§4.3） |
| 3 | `napi_poll` 事件 | 中断/NAPI 还活着吗 |
| 4 | `ethtool -S` 里厂商自定义的 tx 错误计数 | 硬件层面的直接原因 |
| 5 | devlink health（有的话） | 固件是不是挂了（§13） |

## §9 编译期开关

### §9.1 `net/Kconfig.debug` 只有三项

整个文件 26 行，三个配置项：

| 配置 | 行 | 依赖 | 作用 |
|---|---|---|---|
| `NET_DEV_REFCNT_TRACKER` | `net/Kconfig.debug:3` | `DEBUG_KERNEL && STACKTRACE_SUPPORT && NET`，`select REF_TRACKER` | 记录每次 `dev_hold`/`dev_put` 的栈，定位「设备拆不掉」 |
| `NET_NS_REFCNT_TRACKER` | `:12` | 同上 | 同机制，作用于 net namespace |
| `DEBUG_NET` | `:21` | `DEBUG_KERNEL && NET` | 打开 `DEBUG_NET_WARN_ON_ONCE` 的全部检查 |

`NET_DEV_REFCNT_TRACKER` 的帮助文字里直接写了「This adds memory and cpu costs.」，`default n`——它是排查 `unregister_netdevice: waiting for <if> to become free` 这类挂起的唯一有效手段：打开之后拆除超时时会把还持有引用的那些栈全部打出来。没打开的话只有一句反复刷屏的等待消息，看不出谁在持有。

`DEBUG_NET` 控制的是 `include/net/net_debug.h:151-155`：

```c
151 #if defined(CONFIG_DEBUG_NET)
152 #define DEBUG_NET_WARN_ON_ONCE(cond) (void)WARN_ON_ONCE(cond)
153 #else
154 #define DEBUG_NET_WARN_ON_ONCE(cond) BUILD_BUG_ON_INVALID(cond)
155 #endif
```

⚠️ **关闭时 `cond` 完全不求值**（`BUILD_BUG_ON_INVALID` 只做类型检查），所以带副作用的表达式在两种配置下行为不同。同时也意味着**发行内核上这一族检查一条都不跑**——§7.1 那道 reason 越界检查就是例子。帮助文字说它「mostly used by fuzzers, but is safe to select」，自己调驱动时开着是划算的。

### §9.2 报文生成器

`config NET_PKTGEN`（`net/Kconfig:328`，tristate，`depends on INET && PROC_FS`）的标题里就带着「USE WITH CAUTION」。实现在 `net/core/pktgen.c`（4042 行），通过 `/proc/net/pktgen/` 控制。它绕过协议栈直接调驱动的发送路径，所以是**唯一能把驱动发送路径单独压测出来**的手段——排查「协议栈慢还是驱动慢」时用它。

### §9.3 网络控制台与 netpoll

`net/core/netpoll.c`（890 行）提供在中断上下文里收发包的能力，`drivers/net/netconsole.c`（974 行）在它之上把 `printk` 送到远端。前者依赖驱动实现 `ndo_poll_controller`（`include/linux/netdevice.h:1452`）——全树有 108 个文件填了这个槽位。⚠️ 内核崩溃时串口拿不到日志、又想要 dmesg 的场合，这是唯一选择；但**驱动没填 `ndo_poll_controller` 时 netconsole 用不了**，而且报错不会指向这个原因。

### §9.4 没有 debugfs

`net/core/`、`net/ethtool/`、`net/sched/sch_generic.c` 里 `debugfs_create_dir` 出现 0 次——**网络设备核心层不提供任何 debugfs 接口**，全部走 sysfs、procfs、netlink 三条路。厂商驱动侧有 30 个文件用了 debugfs（重跑：`grep -rlw debugfs_create_dir drivers/net/ethernet/ | wc -l`），但目录结构和内容各家自定义，没有任何共同约定，只能逐个驱动看源码。

## §10 `/proc/net/` 四个文件

创建点全部在 `net/core/net-procfs.c`（407 行）：

| 路径 | 权限 | 创建行 | 内容 |
|---|---|---|---|
| `/proc/net/dev` | 0444 | `net/core/net-procfs.c:319` | 每接口一行标准统计（`dev_seq_show` `:105`） |
| `/proc/net/softnet_stat` | 0444 | `:322` | 每 CPU 一行软中断统计 |
| `/proc/net/ptype` | 0444 | `:325` | 已注册的协议处理器 |
| `/proc/net/dev_mcast` | 0 | `:385` | 多播地址表 |

`/proc/net/softnet_stat` 是排查收包侧最重要的文件，但它**没有表头**，13 列纯十六进制。列的含义由 `softnet_seq_show()`（`net/core/net-procfs.c:152-177`）的 `seq_printf` 参数顺序决定：

| 列 | 内容 |
|---|---|
| 1 | `sd->processed` — 处理过的包数 |
| 2 | `sd->dropped` — backlog 满导致的丢包 |
| 3 | `sd->time_squeeze` — NAPI 打满预算被迫让出的次数 |
| 4 | 恒为 0 |
| 5–8 | 恒为 0（源码注释：was fastroute） |
| 9 | 恒为 0（源码注释：was cpu_collision） |
| 10 | `sd->received_rps` — 被 RPS 转发过来的包数 |
| 11 | `flow_limit_count` — flow limit 命中次数（无 `CONFIG_NET_FLOW_LIMIT` 时恒 0） |
| 12 | `softnet_backlog_len(sd)` — 当前 backlog 长度 |
| 13 | CPU 编号 |

⚠️ **第 4 到第 9 列是六个恒零的历史遗留占位**，网上流传的旧版列表把它们解释成各种含义，全是错的——源码注释直说了 `/* was fastroute */` 和 `/* was cpu_collision */`。第 13 列是 CPU 编号而不是行号，`:167-170` 的注释解释了为什么要显式给出：离线 CPU 不显示，行号对不上 CPU 号。

要看的三个数字：**第 2 列在涨 = backlog 溢出**（调 `net.core.netdev_max_backlog`，默认 1000，`net/core/dev.c:4419`）；**第 3 列在涨 = 预算不够**（调 `net.core.netdev_budget`，默认 300，`net/core/dev.c:4424`，或 `netdev_budget_usecs` `:4426`）；**第 12 列一直很大 = 收包处理跟不上**。

## §11 错误码分诊

### §11.1 三套返回值编码并存

网卡驱动路径上有三套互不兼容的返回值编码，混淆它们是读错日志的常见原因：

| 编码 | 取值 | 定义 | 谁返回 |
|---|---|---|---|
| 收方向 | `NET_RX_SUCCESS` = 0、`NET_RX_DROP` = 1 | `include/linux/netdevice.h:83-84` | `netif_rx`、`netif_receive_skb` |
| qdisc 入队 | `NET_XMIT_SUCCESS` = 0x00、`NET_XMIT_DROP` = 0x01、`NET_XMIT_CN` = 0x02 | `include/linux/netdevice.h:106-108` | qdisc 的 `->enqueue()` |
| 驱动发送 | `NETDEV_TX_OK` = 0x00、`NETDEV_TX_BUSY` = 0x10 | `include/linux/netdevice.h:120-124`（`enum netdev_tx`） | `ndo_start_xmit` |

三者靠数值区间区分，`include/linux/netdevice.h:127-129` 的注释把这个约定写死了：`NETDEV_TX_MASK > NET_XMIT_MASK >= 0`，且 `hard_start_xmit()` 返回值小于 `NET_XMIT_MASK`（0x0f，`:109`）就意味着 skb 已被消耗。⚠️ **改动其中任何一个常量的数值会静默地破坏整条判定链**，因为没有任何 `static_assert` 守着这个不变量，只有那段注释。

### §11.2 被改写的路径

`NET_XMIT_*` 在往上传的过程中会被两个宏改写（`include/linux/netdevice.h:114-115`）：

```c
114 #define net_xmit_eval(e)	((e) == NET_XMIT_CN ? 0 : (e))
115 #define net_xmit_errno(e)	((e) != NET_XMIT_CN ? -ENOBUFS : 0)
```

⚠️ **`NET_XMIT_CN`（拥塞通知）经过 `net_xmit_eval` 之后变成 0，也就是「成功」。** `:111-113` 的注释解释了理由：CN 不保证这个包丢了，只是提示上层发慢点。所以**用户态看到 `send()` 成功不代表包没被丢**，反过来 `-ENOBUFS` 也不一定是内存不够，它可能只是 `net_xmit_errno` 把一个 `NET_XMIT_DROP` 翻译过来的结果。另外 `dev_queue_xmit()` 的 kerneldoc（`include/linux/netdevice.h:99-102`）说明了另一层改写：物理设备的发送是异步的，返回值不往上传；虚拟设备是同步的，驱动返回码被 `dev_queue_xmit()` 消费掉，其余的才往上传——**同一个错误码在物理网卡和虚拟网卡上到达用户态的可见性不一样**。

### §11.3 `-EOPNOTSUPP` 的来源

`ethtool` 报「Operation not supported」几乎总是 `-EOPNOTSUPP`，但来源至少有三层，逐层排除：

| 层 | 判据 | 点名函数 |
|---|---|---|
| ops 槽位没填 | `if (!ops->get_ethtool_stats \|\| !ops->get_sset_count)` | `ethtool_get_stats()`（`net/ethtool/ioctl.c:2037`） |
| 成对槽位只填了一个 | 末尾 `if (ops->get_sset_count && ops->get_strings)` 不成立 | `__ethtool_get_sset_count()`（`net/ethtool/ioctl.c:164-193`） |
| 硬件不支持该子功能 | 槽位填了，但驱动自己返回 `-EOPNOTSUPP` | 各驱动的 `ethtool_ops` 实现 |

区分方法：**第一层和第二层的失败在 `ethtool -S` 上表现为「一个字段都没有」，第三层通常只有某个具体子命令失败。** `__ethtool_get_sset_count()` 还有一批不依赖驱动的 string set（`ETH_SS_FEATURES`、`ETH_SS_RSS_HASH_FUNCS`、`ETH_SS_TUNABLES`、`ETH_SS_PHY_TUNABLES`、`ETH_SS_LINK_MODES`），它们直接返回内建数组的 `ARRAY_SIZE`——所以 `ethtool -k`（features）在任何驱动上都能工作，不能用它来判断 `ethtool_ops` 有没有填。

### §11.4 `net/core/dev.c` 里错误码的分布

口径：**在 `net/core/dev.c` 这一个文件里包含该串的行数**（`grep -cw -- "-EINVAL" net/core/dev.c`），不是返回点数，也不含 `net/ethtool/`：

| 错误码 | 行数 | 主要语义 |
|---|---|---|
| `-EINVAL` | 43 | 参数校验（队列数、MTU、feature 组合） |
| `-ENOMEM` | 17 | 分配失败 |
| `-EBUSY` | 10 | 设备正在被用（改名、改队列数时接口是 up 的） |
| `-EOPNOTSUPP` | 10 | ndo 槽位没填 |
| `-EEXIST` | 9 | 名字/地址已存在 |
| `-ENODEV` | 8 | 找不到设备 |
| `-EPERM` | 1 | 权限 |

⚠️ **`-EINVAL` 和 `-EBUSY` 的区别在这一层很关键**：`-EINVAL` 是「这个值不合法」，`-EBUSY` 是「值合法但现在不能改」。`ethtool -L` 改队列数、`ip link set name` 改名这类操作在接口 up 时返回 `-EBUSY`，先 down 再改就成功——看到 `-EBUSY` 不要去查值对不对。核心侧改名失败还有一句专门的日志：`netdev_err(dev, "name change rollback failed: %d\n", ...)`（`net/core/dev.c:1296`），配套的成功日志是 `netdev_info(dev, "renamed from %s\n", oldname)`（`:1254`）。

另一句值得记住的是 `netdev_err(dev, "refused to change device tx_queue_len\n")`（`net/core/dev.c:8850`）。它出在回滚标签上，触发条件是**通知链否决**（`:8837-8840`：`call_netdevice_notifiers(NETDEV_CHANGE_TX_QUEUE_LEN)` 的结果经 `notifier_to_errno()` 转换后非零）或者 qdisc 侧调整失败（`:8841-8843`），随后把 `dev->tx_queue_len` 恢复成原值并把那个错误码原样返回。⚠️ **所以这条日志对应的错误码不固定**——它是别人给的，不是这里产生的。看到它要去查是哪个通知链回调否决了，而不是猜错误码含义。

## §12 故障注入：本子系统没有

结论是**网络设备核心层没有接入内核的故障注入框架**。查证过程和它为什么容易看错，都值得写下来。

第一遍在 `net/`、`drivers/net/`、`include/net/`、`include/linux/netdevice.h` 上搜 `should_fail`、`fault_attr`、`DECLARE_FAULT_ATTR`，命中里出现了 `net/core/net-sysfs.c`——看起来核心层有故障注入。打开看，两处命中分别是 `rx_queue_default_attrs[]`（`net/core/net-sysfs.c:988`）和 `netdev_queue_default_attrs[]`（`:1604`）：**`def<ault_attr>s` 这个子串里包含 `fault_attr`**。两处都是假命中。

排掉之后剩下的命中全在 `net/sunrpc/`（`fail.h`、`svc.c`、`cache.c`、`xprt.c`），那是 NFS 的 RPC 层，不属于网卡驱动范围。`lib/Kconfig.debug` 里的 `FAIL_*` 配置项依赖的是 BLOCK / FUTEX / MMC / USERCOPY / DEBUG_FS，没有一个依赖 NET。

⚠️ **这个子串陷阱是「统计和存在性判断必须用 `grep -w` 或者打开命中逐条确认」的一个现成例子**（记入 `99-evidence.md` §3）。如果只看文件名列表就下结论，会写出一整节不存在的机制。

那么想做故障注入怎么办：

| 想模拟的故障 | 可用手段 | 出处 |
|---|---|---|
| 发送队列卡死 | `ethtool -L` 把队列数改小，或写 BQL `limit` 为极小值 | §4.3 |
| 收包丢弃 | `tc` 的 `netem loss`（在 qdisc 层，不经过驱动） | 不在本篇 |
| 链路抖动 | 写 `/sys/class/net/<if>/carrier` | §4.4 |
| 内存分配失败 | 通用的 `failslab` / `fail_page_alloc`（不针对网络，会波及全系统） | `lib/Kconfig.debug` |
| 硬件层面异常 | devlink health 的 `test` 回调（仅少数厂商实现） | §13 |

## §13 devlink health：厂商侧的诊断通道

核心层看不到网卡固件的状态，这条信息只能由驱动通过 devlink 上报。ops 结构是 `struct devlink_health_reporter_ops`（`include/net/devlink.h:692-704`，kerneldoc 在 `:682`）：

| 成员 | 行 | 语义 |
|---|---|---|
| `name` | `:693` | reporter 名字，用户态用它寻址 |
| `recover` | `:694` | 自动恢复动作 |
| `dump` | `:696` | 抓一份故障现场快照 |
| `diagnose` | `:699` | 输出当前诊断信息 |
| `test` | `:702` | 主动触发一次自测（也是唯一的「故障注入」形态） |

注册函数：`devlink_health_reporter_create`（`include/net/devlink.h:1778`）、`devlink_port_health_reporter_create`（`:1783`）；上报入口 `devlink_health_report`（`:1795`），它同时触发 §5.7 的 `devlink_health_report` tracepoint。

```
ls  /sys/class/net/<if>/device/net/            # 先确认设备有 devlink 实例
devlink health show
devlink health diagnose pci/0000:03:00.0 reporter tx
devlink health dump show pci/0000:03:00.0 reporter fw
devlink health test pci/0000:03:00.0 reporter fw
```

⚠️ **这一整套是可选的，绝大多数网卡驱动没有实现。** 全树 `devlink_port_register` 只出现在 8 个文件里（口径见 `04-extensions.md` §12.2）。`devlink health show` 什么都不输出，通常意味着驱动没实现，不是命令用错。arm64 的五个代表驱动里只有 `ti/am65-cpsw-nuss.c` 用了 devlink，而且没有 health reporter。

## §14 一页速查

本子系统**没有字符设备**——所有控制面都走 netlink（`ethtool`、`ip`、`devlink`）、sysfs 和 procfs。所以用户态工具就是这三个命令加 `ethtool`。

```
--- 先分清故障在哪一层 -------------------------------------------------
ip -s link show <if>              # 有没有 carrier、收发计数在不在动
ethtool <if>                      # 速率、双工、autoneg、msg level
ethtool -S <if>                   # 厂商自定义统计（可能整个不支持）
cat /proc/net/softnet_stat        # 第 2/3/12 列（§10）
cat /sys/class/net/<if>/queues/tx-0/trans_timeout   # 看门狗超时次数

--- 打开日志（两道门都要开）-------------------------------------------
ethtool -s <if> msglvl link on                       # 门 2：msg_enable 位
echo 'file <驱动>.c +p' > /sys/kernel/debug/dynamic_debug/control   # 门 3
# 注意：pktdata / tx_queued / tx_done / rx_status 是每包一条，别在跑流量时开

--- tracepoint ---------------------------------------------------------
cd /sys/kernel/debug/tracing
echo 1 > events/skb/kfree_skb/enable          # 丢包原因（最高优先级）
echo 1 > events/napi/napi_poll/enable         # NAPI 有没有跑、work vs budget
echo 1 > events/net/net_dev_xmit/enable       # 驱动发送返回值 rc
echo 1 > events/net/net_dev_xmit_timeout/enable
echo 'name == "eth0"' > events/net/net_dev_xmit/filter
cat trace

--- 发送侧队列深度 ----------------------------------------------------
cd /sys/class/net/<if>/queues/tx-0/byte_queue_limits
cat inflight limit limit_min limit_max hold_time     # inflight 只读、算出来的

--- NAPI 调参（都需要 CAP_NET_ADMIN）---------------------------------
echo 20000 > /sys/class/net/<if>/gro_flush_timeout    # 纳秒
echo 2     > /sys/class/net/<if>/napi_defer_hard_irqs
echo 1     > /sys/class/net/<if>/threaded             # NAPI 搬到内核线程

--- 全局 -------------------------------------------------------------
sysctl net.core.netdev_budget          # 默认 300
sysctl net.core.netdev_max_backlog     # 默认 1000
sysctl net.core.dev_weight             # 默认 64
```

三条最容易踩的：

- **日志两道门是「与」关系而且互不提示**（§2.2）——`dynamic_debug` 开了没输出先查 `msg_enable`
- **`_once` 是按调用点去重、不是按设备去重**（§1.2）——多网卡时只有一张报错是正常现象
- **没有 `ndo_tx_timeout` 就没有看门狗**（§8.2）——发送卡死却零日志时先查这个槽位

### 阅读下一篇

| 想知道 | 去哪篇 |
|---|---|
| 这些 ops 槽位分别是什么、谁调用 | `01-api.md` |
| `net_device` / `napi_struct` / `softnet_data` 的字段布局 | `02-datastructures.md` |
| 收发路径的完整时间线、锁 | `03-internals.md` |
| page_pool / XDP / BQL / devlink 各自是什么框架 | `04-extensions.md` |
| 写一个驱动要填哪些槽位 | `05-usage.md` |
| DTS 配错导致的故障 | `06-devicetree.md` |
| 用大白话理解 NAPI、BQL、GRO | `08-feynman.md` |
| 本篇每个数字的重跑命令、口径说明 | `99-evidence.md` |
