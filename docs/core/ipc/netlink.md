# Netlink 通信框架

> A 级文档 · 核心 `net/netlink/`（5,302 行）· 头文件 `include/net/netlink.h`（2,004 行）+ `include/net/genetlink.h`（464 行）+ UAPI 头文件

## 1 定位

Netlink 是内核与用户空间之间的双向通信机制，也是内核子系统之间通信的桥梁。它基于 socket（`AF_NETLINK`），提供请求-应答和组播通知两种模式。两层 API：

1. **原始 netlink**：底层消息构造/解析，用于 NETLINK_ROUTE 等老协议
2. **通用 netlink（genl）**：多路复用框架，现代子系统首选——动态 family ID、属性验证、权限检查

## 2 代码地图

| 文件 | 行数 | 职责 |
|------|------|------|
| `net/netlink/af_netlink.c` | 2,920 | 核心 socket 实现：创建、发送、接收、dump |
| `net/netlink/genetlink.c` | 1,565 | 通用 netlink：family 注册、命令分派 |
| `net/netlink/policy.c` | 479 | 属性策略验证 |
| `net/netlink/diag.c` | 262 | socket 诊断 |
| `include/net/netlink.h` | 2,004 | 原始 netlink API：消息/属性构造与解析 |
| `include/net/genetlink.h` | 464 | 通用 netlink API |
| `include/linux/netlink.h` | 288 | 内核内部结构（netlink_sock、netlink_callback） |
| `include/uapi/linux/netlink.h` | 378 | UAPI：nlmsghdr、nlattr、sockaddr_nl |
| `include/uapi/linux/genetlink.h` | 103 | UAPI：genlmsghdr |

## 3 消息格式

### 原始 netlink 消息

```
<--- nlmsg_total_size(payload) --->
+----------+---------+-------------+
| nlmsghdr | Padding |   Payload   |
+----------+---------+-------------+
   16 bytes              nlmsg_data(nlh)
```

`struct nlmsghdr`（`include/uapi/linux/netlink.h:52`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `nlmsg_len` | `__u32` | 消息总长度（含头部） |
| `nlmsg_type` | `__u16` | 消息类型（内容类型或控制类型） |
| `nlmsg_flags` | `__u16` | NLM_F_REQUEST / NLM_F_MULTI / NLM_F_ACK / NLM_F_DUMP |
| `nlmsg_seq` | `__u32` | 序列号（请求-应答匹配） |
| `nlmsg_pid` | `__u32` | 发送方 port ID（内核 = 0） |

### 属性（TLV 格式）

```
<--- nla_total_size(payload) --->
+----------+---------+-----------+
|  nlattr  | Padding |  Payload  |
+----------+---------+-----------+
   4 bytes             nla_data(nla)
```

`struct nlattr`（`include/uapi/linux/netlink.h:229`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `nla_len` | `__u16` | 属性总长度（含头部） |
| `nla_type` | `__u16` | 类型（14 位）+ NLA_F_NESTED（bit 15）+ NLA_F_NET_BYTEORDER（bit 14） |

### 通用 netlink 消息

```
+----------+-------------+----------+-------------+
| nlmsghdr | genlmsghdr  | User Hdr | Attributes  |
+----------+-------------+----------+-------------+
   16 bytes    4 bytes     variable    variable
```

`struct genlmsghdr`（`include/uapi/linux/genetlink.h:13`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `cmd` | `__u8` | 命令 ID |
| `version` | `__u8` | 协议版本 |
| `reserved` | `__u16` | 保留（必须为 0） |

## 4 核心数据结构

### `struct netlink_sock`（`net/netlink/af_netlink.h:24`）

每个 netlink socket 的状态。

| 字段 | 说明 |
|------|------|
| `sk` | 底层 sock（必须是第一个成员） |
| `portid` | 唯一端口标识符 |
| `dst_portid` / `dst_group` | 目标端口和组播组 |
| `groups` | 组播订阅掩码 |
| `netlink_rcv` | 内核 socket 的输入处理回调 |
| `cb` | dump 操作状态（`struct netlink_callback`） |
| `cb_running` | dump 是否正在运行 |

### `struct genl_family`（`include/net/genetlink.h:62`）

通用 netlink 家族定义。

| 字段 | 说明 |
|------|------|
| `name[GENL_NAMSIZ]` | 家族名（16 字节） |
| `version` | 协议版本 |
| `maxattr` | 最大属性类型 |
| `netnsok` | 是否允许网络命名空间 |
| `parallel_ops` | ops 是否可并行执行 |
| `policy` | 默认属性验证策略 |
| `pre_doit` / `post_doit` | 命令前后的钩子 |
| `ops` / `small_ops` | 操作数组 |
| `mcgrps` | 组播组数组 |

### `struct genl_ops`（`include/net/genetlink.h:171`）

单个命令的处理。

| 字段 | 说明 |
|------|------|
| `doit` | 请求-应答处理函数 |
| `dumpit` | dump 处理函数 |
| `start` / `done` | dump 生命周期回调 |
| `cmd` | 命令 ID |
| `flags` | GENL_ADMIN_PERM 等 |
| `policy` | 每命令策略（覆盖 family 默认） |

### `struct genl_info`（`include/net/genetlink.h:100`）

接收消息时传递给 `doit` 的上下文。

| 字段 | 说明 |
|------|------|
| `snd_portid` | 发送方 port ID |
| `nlhdr` | netlink 消息头 |
| `genlhdr` | genl 消息头 |
| `userhdr` | 用户自定义头部 |
| `attrs` | 解析后的属性数组 |
| `extack` | 扩展错误报告 |

### `struct nla_policy`（`include/net/netlink.h:320`）

属性验证策略。

| 字段 | 说明 |
|------|------|
| `type` | 期望类型（NLA_U8/U16/U32/U64/STRING/NESTED/...） |
| `len` | 长度约束 |
| `range` / `min` / `max` | 值范围 |
| `validate` | 自定义验证回调 |

## 5 核心 API

### 原始 netlink — 消息构造

头文件：`include/net/netlink.h`

| API | 说明 | 备注 |
|-----|------|------|
| `nlmsg_new(payload, flags)` | 分配消息 skb | `:989` |
| `nlmsg_put(skb, portid, seq, type, payload, flags)` | 添加 nlmsghdr | `:931`，空间不足返回 NULL |
| `nlmsg_end(skb, nlh)` | 完成消息（设置 nlmsg_len） | `:1003` |
| `nlmsg_cancel(skb, nlh)` | 取消消息构造 | `:1042` |
| `nla_put(skb, attrtype, attrlen, data)` | 添加二进制属性 | |
| `nla_put_u8/u16/u32/u64(skb, type, val)` | 添加整数属性 | |
| `nla_put_string(skb, type, str)` | 添加字符串属性 | |
| `nla_put_flag(skb, type)` | 添加标志属性 | 零长度 |
| `nla_nest_start(skb, type)` | 开始嵌套属性 | `:1821` |
| `nla_nest_end(skb, start)` | 结束嵌套属性 | `:1836` |

### 原始 netlink — 消息解析

| API | 说明 | 备注 |
|-----|------|------|
| `nlmsg_parse(nlh, hdrlen, tb, maxtype, policy, extack)` | 解析消息属性 | `:764` |
| `nla_parse(tb, maxtype, head, len, policy, extack)` | 解析属性流 | `:665` |
| `nla_parse_nested(tb, maxtype, nla, policy, extack)` | 解析嵌套属性 | `:1240` |
| `nla_get_u8/u16/u32/u64(nla)` | 提取属性值 | |

### 原始 netlink — 消息发送

| API | 说明 | 备注 |
|-----|------|------|
| `nlmsg_unicast(sk, skb, portid)` | 单播发送 | `:1084` |
| `nlmsg_multicast(sk, skb, portid, group, flags)` | 组播发送 | `:1064` |
| `nlmsg_notify(sk, skb, portid, group, report, flags)` | 通知（可选回报） | `:513` |

### 通用 netlink — 家族注册

头文件：`include/net/genetlink.h`；实现：`net/netlink/genetlink.c`

| API | 说明 | 备注 |
|-----|------|------|
| `genl_register_family(family)` | 注册 genl 家族 | `genetlink.c:409`，分配 family ID |
| `genl_unregister_family(family)` | 注销 genl 家族 | `genetlink.c:478` |

### 通用 netlink — 消息构造与发送

| API | 说明 | 备注 |
|-----|------|------|
| `genlmsg_put(skb, portid, seq, family, flags, cmd)` | 添加 genl 头部 | `genetlink.h:209`，返回 userhdr 指针 |
| `genlmsg_end(skb, hdr)` | 完成 genl 消息 | `genetlink.h:301` |
| `genlmsg_new(payload, flags)` | 分配 genl 消息 skb | `genetlink.h:429` |
| `genlmsg_parse(nlh, family, tb, maxtype, policy, extack)` | 解析 genl 消息 | `genetlink.h:253` |
| `genlmsg_reply(skb, info)` | 回复请求 | `genetlink.h:381` |
| `genlmsg_multicast(family, skb, portid, group, flags)` | 组播 | `genetlink.h:344` |

### Socket API

头文件：`include/linux/netlink.h`

| API | 说明 | 备注 |
|-----|------|------|
| `netlink_kernel_create(net, unit, cfg)` | 创建内核 netlink socket | `:60`，unit = NETLINK_* 常量 |
| `netlink_unicast(ssk, skb, portid, nonblock)` | 单播 | `af_netlink.c:1327` |
| `netlink_broadcast(ssk, skb, portid, group, allocation)` | 组播 | `af_netlink.c:1493` |

## 6 端到端调用路径

### 通用 netlink 命令分派

```
用户 sendmsg() → netlink_sendmsg() → netlink_unicast()
  → netlink_unicast_kernel() → nlk->netlink_rcv(skb)
    → genl_rcv(skb)
      → netlink_rcv_skb(skb, genl_rcv_msg)
        → genl_rcv_msg(skb, nlh, extack)
          → genl_family_find_byid(nlh->nlmsg_type)  // 找 family
          → genl_get_cmd(hdr->cmd, family, &op)      // 找命令
          → 权限检查 (GENL_ADMIN_PERM → CAP_NET_ADMIN)
          → genl_family_rcv_msg_doit()
            → genl_family_rcv_msg_attrs_parse()      // 属性解析+验证
            → 填充 genl_info
            → family->pre_doit(ops, skb, &info)       // 前置钩子
            → ops->doit(skb, &info)                   // 实际处理
            → family->post_doit(ops, skb, &info)      // 后置钩子
```

### Dump 流程（多消息回复）

```
用户发送 NLM_F_DUMP 请求
  → genl_family_rcv_msg_dumpit()
    → __netlink_dump_start()
      → 初始化 netlink_callback
      → control->start(cb)                        // 初始化
      → netlink_dump(sk)
        → 分配 skb
        → cb->dump(skb, cb)                       // 填充数据
        → 如果 skb 满或还有数据 → 发送 → 再次调用
        → 如果 dump 返回 0 → 发送 NLMSG_DONE → cb->done()

用户接收:
  Message 1: NLM_F_MULTI, data...
  Message 2: NLM_F_MULTI, data...
  ...
  Message N: NLMSG_DONE
```

### 内核 socket 创建与消息接收

```
子系统初始化:
  netlink_kernel_create(&init_net, NETLINK_FOO, &cfg)
    → sock_create_lite(PF_NETLINK, SOCK_DGRAM, NETLINK_FOO)
    → __netlink_create() → 初始化 socket
    → 设置 nlk->netlink_rcv = cfg->input
    → 插入哈希表 (portid = 0)

收到消息时:
  netlink_unicast_kernel() → nlk->netlink_rcv(skb)
    → 子系统自定义处理函数
```

## 7 属性验证策略

```c
static const struct nla_policy my_policy[MY_ATTR_MAX+1] = {
    [MY_ATTR_NAME]  = { .type = NLA_STRING, .len = 32 },
    [MY_ATTR_VALUE] = { .type = NLA_U32 },
    [MY_ATTR_DATA]  = NLA_POLICY_EXACT_LEN(sizeof(struct mydata)),
    [MY_ATTR_NEST]  = NLA_POLICY_NESTED(nested_policy),
    [MY_ATTR_FLAGS] = NLA_POLICY_BITFIELD32(valid_flags),
};
```

| 验证宏 | 说明 |
|--------|------|
| `NLA_POLICY_MIN(tp, min)` | 最小值/长度 |
| `NLA_POLICY_MAX(tp, max)` | 最大值/长度 |
| `NLA_POLICY_RANGE(tp, min, max)` | 范围验证 |
| `NLA_POLICY_EXACT_LEN(len)` | 精确长度 |
| `NLA_POLICY_NESTED(policy)` | 嵌套属性子策略 |

## 8 消费者统计

通用 netlink 家族注册：全树约 131 个 `struct genl_family` 定义。主要消费者：

| 子系统 | 用途 |
|--------|------|
| cfg80211/nl80211 | WiFi 配置 |
| NETLINK_ROUTE | 路由、接口、邻居 |
| NETLINK_NETFILTER | conntrack |
| devlink | 设备配置 |
| ethtool | 网络设备设置 |
| NETLINK_XFRM | IPsec |
| NETLINK_AUDIT | 审计 |
| taskstats | 进程统计 |
| NETLINK_SELINUX | SELinux |

## 9 使用模式

### 注册 genl 家族

```c
static const struct genl_ops my_ops[] = {
    { .cmd = MY_CMD_DO, .doit = my_doit, .flags = GENL_ADMIN_PERM },
    { .cmd = MY_CMD_DUMP, .dumpit = my_dumpit },
};

static struct genl_family my_family = {
    .name = "my_subsystem",
    .version = 1,
    .maxattr = MY_ATTR_MAX,
    .policy = my_policy,
    .netnsok = true,
    .ops = my_ops,
    .n_ops = ARRAY_SIZE(my_ops),
};

static int __init my_init(void)
{
    return genl_register_family(&my_family);
}
```

### 实现 doit 命令

```c
static int my_doit(struct sk_buff *skb, struct genl_info *info)
{
    if (info->attrs[MY_ATTR_NAME])
        name = nla_data(info->attrs[MY_ATTR_NAME]);

    reply = genlmsg_new(REPLY_SIZE, GFP_KERNEL);
    hdr = genlmsg_put(reply, info->snd_portid, info->snd_seq,
                      &my_family, 0, MY_CMD_DO);
    nla_put_u32(reply, MY_ATTR_VALUE, result);
    genlmsg_end(reply, hdr);
    return genlmsg_reply(reply, info);
}
```

### 实现 dumpit 命令

```c
static int my_dumpit(struct sk_buff *skb, struct netlink_callback *cb)
{
    int idx = 0, start = cb->args[0];

    list_for_each_entry(item, &my_list, list) {
        if (idx < start) { idx++; continue; }

        void *hdr = genlmsg_put(skb, NETLINK_CB(cb->skb).portid,
                                cb->nlh->nlmsg_seq, &my_family,
                                NLM_F_MULTI, MY_CMD_DUMP);
        if (!hdr) {
            cb->args[0] = idx;     /* 保存位置 */
            return -EMSGSIZE;      /* skb 满，下次继续 */
        }
        nla_put_string(skb, MY_ATTR_NAME, item->name);
        genlmsg_end(skb, hdr);
        idx++;
    }
    return 0;  /* 完成 */
}
```

## 10 注意事项

- **nlmsg_type 即 family ID**：genl 消息的 `nlmsg_type` 字段存放的是动态分配的 family ID，不是固定常量
- **NLM_F_DUMP = NLM_F_GET | NLM_F_ROOT**：dump 请求的标志组合
- **`parallel_ops`**：设置后允许多个 dump 并行执行，不持有全局 genl_lock
- **`pre_doit`/`post_doit`**：常用于获取/释放锁，所有命令共享
- **`extack`**：扩展错误报告——可以告诉用户空间哪个属性有问题、缺什么字段
- **网络命名空间**：`netnsok = false` 的 family 只在 init_net 中可用

---

## §4 常见陷阱

### 陷阱 1：genl_family 的 maxattr 与 policy 数组大小不匹配

**错误写法**：
```c
static const struct nla_policy my_policy[MY_ATTR_MAX + 1] = {
    [MY_ATTR_NAME]  = { .type = NLA_STRING, .len = 32 },
    [MY_ATTR_VALUE] = { .type = NLA_U32 },
};

static struct genl_family my_family = {
    .maxattr = MY_ATTR_MAX + 5,  /* 错误！大于 policy 数组大小 */
    .policy = my_policy,
    /* ... */
};
/* 属性解析时可能读取 policy 数组越界 */
```

**正确写法**：
```c
static struct genl_family my_family = {
    .maxattr = MY_ATTR_MAX,  /* 必须 = ARRAY_SIZE(policy) - 1 */
    .policy = my_policy,
    /* ... */
};
```

**原因**：`genl_family_rcv_msg_attrs_parse()` 使用 `ops->maxattr` 分配属性指针数组（`net/netlink/genetlink.c:560-561`：`kmalloc_array(ops->maxattr + 1, sizeof(struct nlattr *))`），然后用 `ops->policy` 进行验证。如果 `maxattr` 大于 policy 数组的实际大小，`__nlmsg_parse()` 会读取 policy 数组越界内存，导致不可预测的行为。如果 ops 没有设置自己的 `maxattr`，则继承 `family->maxattr`（`net/netlink/genetlink.c:131-132`）。

**源码依据**：`net/netlink/genetlink.c:560-561` — `kmalloc_array(ops->maxattr + 1, ...)`；`net/netlink/genetlink.c:131-132` — `op->maxattr = family->maxattr` 继承逻辑。

### 陷阱 2：dumpit 回调中使用 GFP_KERNEL 分配

**错误写法**：
```c
static int my_dumpit(struct sk_buff *skb, struct netlink_callback *cb)
{
    /* 错误！dump 回调在持有 cb_lock 读锁时调用 */
    /* GFP_KERNEL 可能睡眠 */
    char *buf = kmalloc(1024, GFP_KERNEL);
    /* ... */
}
```

**正确写法**：
```c
static int my_dumpit(struct sk_buff *skb, struct netlink_callback *cb)
{
    /* 使用 GFP_ATOMIC 或避免分配 */
    char *buf = kmalloc(1024, GFP_ATOMIC);
    /* 或在 start 回调（genl_start）中预分配，存在 cb->args 中 */
}
```

**原因**：非 `parallel_ops` 家族的 dump 路径中，`genl_lock_dumpit()` 持有 `genl_mutex`（`net/netlink/genetlink.c:634-643`）。虽然 `genl_mutex` 本身不阻止睡眠，但 `netlink_dump()` 的调用上下文在 `netlink_rcv_skb` 处理链中，部分路径在 `rcu_read_lock` 或 spinlock 下执行。更重要的是，`genl_start()` 中的属性解析使用 `GFP_KERNEL`（`net/netlink/genetlink.c:561`），但这是在 dump 开始前的准备阶段；dump 回调本身应更谨慎。

**源码依据**：`net/netlink/genetlink.c:634-643` — `genl_lock_dumpit` 持有 `genl_lock()`；`net/netlink/genetlink.c:560-561` — `genl_family_rcv_msg_attrs_parse` 使用 `GFP_KERNEL`。

### 陷阱 3：resv_start_op 之前的命令不做属性验证

**错误写法**：
```c
static const struct genl_ops my_ops[] = {
    {
        .cmd = MY_CMD_OLD,
        .doit = my_old_doit,
        /* 未设置 policy 和 maxattr */
        /* 假设框架会自动验证属性——不会！ */
    },
    {
        .cmd = MY_CMD_NEW,
        .doit = my_new_doit,
        .policy = my_policy,
        .maxattr = MY_ATTR_MAX,
    },
};

static struct genl_family my_family = {
    .resv_start_op = MY_CMD_NEW,
    /* MY_CMD_OLD < resv_start_op，不做属性验证 */
};
```

**正确写法**：
```c
/* 对于 resv_start_op 之前的旧命令，必须在 doit 中手动验证属性 */
static int my_old_doit(struct sk_buff *skb, struct genl_info *info)
{
    /* info->attrs 可能为 NULL——没有自动解析 */
    /* 手动解析或跳过验证 */
}
/* 新命令应设置 resv_start_op = 0 或确保所有命令都有 policy */
```

**原因**：`genl_op_fill_in_reject_policy()` 只对 `op->cmd >= family->resv_start_op` 的命令填充拒绝策略（`net/netlink/genetlink.c:97-101`）。对于 `resv_start_op` 之前的命令，如果 ops 没有设置 `policy`，`genl_family_rcv_msg_attrs_parse()` 中 `ops->maxattr` 为 0 时直接返回 NULL（`net/netlink/genetlink.c:557-558`），不解析任何属性——`info->attrs` 为 NULL。

**源码依据**：`net/netlink/genetlink.c:97-101` — `genl_op_fill_in_reject_policy` 的条件检查；`net/netlink/genetlink.c:557-558` — `if (!ops->maxattr) return NULL`。

### 陷阱 4：genlmsg_multicast 的 skb 在失败后被释放

**错误写法**：
```c
struct sk_buff *skb = genlmsg_new(REPLY_SIZE, GFP_KERNEL);
/* 填充消息... */
genlmsg_multicast(&my_family, skb, 0, MY_GRP, GFP_KERNEL);
/* 无论成功失败都继续使用 skb —— 错误！ */
nla_put_u32(skb, MY_ATTR_VALUE, 42);  /* skb 可能已被释放 */
```

**正确写法**：
```c
struct sk_buff *skb1 = genlmsg_new(REPLY_SIZE, GFP_KERNEL);
/* 填充消息... */
genlmsg_multicast(&my_family, skb1, 0, MY_GRP, GFP_KERNEL);
/* skb1 的所有权已转移——不再使用 */

/* 如果还需要发消息，分配新的 skb */
struct sk_buff *skb2 = genlmsg_new(REPLY_SIZE, GFP_KERNEL);
/* 填充并使用 skb2... */
```

**原因**：`genlmsg_multicast()` 最终调用 `nlmsg_multicast()`，无论成功还是失败，skb 的所有权都被转移——成功时发送给订阅者，失败时 `kfree_skb()` 释放（`net/netlink/genetlink.c:1536`：`kfree_skb(skb)`）。调用者不能再使用已发送的 skb。`genlmsg_multicast_allns()` 同理（`net/netlink/genetlink.c:1540-1549`）。

**源码依据**：`net/netlink/genetlink.c:1536` — 错误路径 `kfree_skb(skb)`；`net/netlink/genetlink.c:1524` — 成功路径 `nlmsg_multicast` 消费 skb。

### 陷阱 5：组播组索引越界

**错误写法**：
```c
/* 假设 family 有 2 个组播组（n_mcgrps = 2） */
genlmsg_multicast(&my_family, skb, 0, 5, GFP_KERNEL);
/* group=5 远超 n_mcgrps——触发 WARN_ON 但不崩溃 */
```

**正确写法**：
```c
/* group 参数是 family 内组播组的偏移（0-based） */
/* 必须 < family->n_mcgrps */
BUILD_BUG_ON(MY_GRP >= ARRAY_SIZE(my_mcgrps));
genlmsg_multicast(&my_family, skb, 0, MY_GRP, GFP_KERNEL);
```

**原因**：`genlmsg_multicast_netns()` 在调用 `nlmsg_multicast` 前检查 `group >= family->n_mcgrps`（`include/net/genetlink.h:330-331`），越界时返回 `-EINVAL`。但实际发送给 `net->genl_sock` 的组播组号是 `family->mcgrp_offset + group`——如果越界检查被绕过，会发送到错误的组播组。`genlmsg_multicast_allns()` 也有同样的检查（`net/netlink/genetlink.c:1544-1545`）。

**源码依据**：`include/net/genetlink.h:330-331` — `WARN_ON_ONCE(group >= family->n_mcgrps)`；`net/netlink/genetlink.c:1544-1545` — 同样的边界检查。

---

## §6 取证摘要

**读取的文件**：
- `include/net/genetlink.h` — `struct genl_family`（maxattr/policy/resv_start_op 字段）、`struct genl_ops`（per-op policy/maxattr）、`genlmsg_multicast_netns` 组播组边界检查
- `net/netlink/genetlink.c` — 家族注册验证（`genl_validate_ops`）、命令分派（`genl_family_rcv_msg_doit`/`genl_family_rcv_msg_dumpit`）、属性解析（`genl_family_rcv_msg_attrs_parse`）、`resv_start_op` 策略填充逻辑
- `include/uapi/linux/netlink.h` — `nlmsghdr`/`nlattr` 格式定义（通过文档引用）

**关键论断出处**：
- "`maxattr` 决定属性数组大小和 policy 索引范围" — 来源：`net/netlink/genetlink.c:560-561`
- "`resv_start_op` 之前的命令不做属性验证" — 来源：`net/netlink/genetlink.c:97-101`（`genl_op_fill_in_reject_policy`）
- "ops 无 policy 时 `maxattr=0` 导致跳过属性解析" — 来源：`net/netlink/genetlink.c:557-558`
- "组播组越界检查" — 来源：`include/net/genetlink.h:330-331`
- "dump 路径中 `genl_lock_dumpit` 持有全局锁" — 来源：`net/netlink/genetlink.c:634-643`

**未验证项**：
- `netlink_dump()` 的完整调用链中是否始终在可睡眠上下文（需追踪 `__netlink_dump_start` → `netlink_dump` 的锁状态）
- `genlmsg_multicast_allns()` 中 `genlmsg_mcast()` 的 RCU 遍历网络命名空间时 skb_clone 失败的确切行为
- `ctrl_dumppolicy` 的完整策略导出逻辑（`net/netlink/genetlink.c:1312-1368`）
