---
title: 费曼学习法
---

# cpufreq 费曼解释篇

这一篇不讲 API，也不讲流程。它只做一件事：**用大白话把 cpufreq 里最容易想错的
那几个概念讲通**。每个概念四段：问题、先举个例子、回到代码、这个类比什么时候会误导你。

⚠️ 类比里的每个技术论断都带行号 —— 费曼篇不豁免取证。
⚠️ 架构范围 arm64。

**九个选题**：

| § | 问题 | 为什么值得单独讲 |
|---|---|---|
| §1 | policy 和 CPU 是什么关系？为什么不是一核一个？ | 本子系统**最容易被类比错的那组关系** |
| §2 | governor、driver、核心，到底谁在做决定？ | 三层职责最常被合并理解 |
| §3 | 为什么会有 policy 边界、perf domain 边界、OPP 共享边界**三套**？ | 04 §7.2 留下的问题 |
| §4 | schedutil 为什么住在 `kernel/sched/` 而不是 `drivers/cpufreq/`？ | 目录位置本身就是设计声明 |
| §5 | 为什么一个"只打日志"的 notifier 能把 `fast_switch` 关掉？ | 最反直觉的一条全局副作用 |
| §6 | 为什么设置最大频率要"投票"，不能直接写？ | freq_qos 的心智模型 |
| §7 | 硬件自己会调频了，为什么还要 cpufreq？ | 高通路线让整套模型看起来多余 |
| §8 | boost 为什么是"另一个文件"，不是一个更高的档位？ | 三个框架合起来的一条链 |
| §9 | 为什么 cpufreq 核心一条 DT 属性都不读？ | 06 篇的头号结论，值得再讲一遍 |

---

## §1 policy 和 CPU 是什么关系？为什么不是一核一个？

### 先举个例子

想象一栋写字楼，每层一个大开间，**一层只有一台中央空调**。

同一层里坐着四个人。张三觉得热，李四觉得冷 —— 但他们**没法各自设定温度**，
因为整层只有一台机器，只能出一个温度。要想让张三和李四各自舒服，
唯一的办法是把他们分到不同楼层去。

现在把"人"换成 CPU，"空调"换成 PLL/时钟源，"温度"换成频率：

- 一个**簇**（cluster）里的四个核共用一路时钟 ⇒ 它们**必须同频**。
- 你不可能给 cpu0 设 1.8GHz、给 cpu1 设 800MHz —— 硬件不支持。
- 所以内核不能"一核一套调频状态"，它必须有一个东西代表"一整层楼"。

这个东西就是 `struct cpufreq_policy`。**policy = 一台空调 = 一路时钟**，
而不是 "一个 CPU 的调频策略"。

所以名字其实起得有点误导：它听起来像"策略"（每个 CPU 各有各的策略），
实际上它是**共享同一个频率的那一组 CPU**。

### 回到代码

`struct cpufreq_policy`（`include/linux/cpufreq.h:58`）开头三行就是三张 CPU 掩码，
而不是一个 CPU 号：

| 字段 | 位置 | 注释原文 | 大白话 |
|---|---|---|---|
| `cpus` | `include/linux/cpufreq.h:60` | `Online CPUs only` | 这层楼里**现在坐着的人** |
| `related_cpus` | `include/linux/cpufreq.h:61` | `Online + Offline CPUs` | 这层楼**一共有几个座位** |
| `real_cpus` | `include/linux/cpufreq.h:62` | `Related and present` | 座位里**真实存在**的那些 |

CPU 号在第四个字段才出现，而且注释说得很明白：

```c
unsigned int		cpu;    /* cpu managing this policy, must be online */
```

位置 `include/linux/cpufreq.h:66`。**它是"代表"，不是"主人"** ——
是这一组里被挑出来干活（跑 governor、挂 sysfs 目录）的那个核。
它下线了，核心会换一个成员来当代表，policy 本身不变。

sysfs 上能直接看到这个结构：

```bash
cat /sys/devices/system/cpu/cpufreq/policy0/related_cpus   # 座位数（对应 :61）
cat /sys/devices/system/cpu/cpufreq/policy0/affected_cpus  # 在座的人（对应 :60）
```

两个文件的实现分别在 `drivers/cpufreq/cpufreq.c:881` 和 `:889`。

**为什么一定要是三张掩码而不是一张？**
因为 CPU 会热插拔。下线一个核时，"这层楼有几个座位"不能变（否则重新上线时
policy 边界就散了），但"现在在座的人"要变（governor 只该管在线的核）。
一张掩码做不到这件事。

### 这个类比什么时候会误导你？

**空调类比会让你以为 policy 边界是硬件属性，内核只是照抄。事实上边界是 DTS 说了算，
而且写错了不报错。**

真正决定边界的是 06 §6.1 那棵决策树：通用 DT 路线看 OPP 表里的 `opp-shared`
（消费点在 `drivers/opp/of.c:1353`，`cpufreq-dt.c:232` 去问它）。
DTS 里漏写 `opp-shared`，内核就以为"每个人一台空调"，于是建出四个 policy，
四个 governor 各自往同一路时钟上写频率 —— **互相覆盖，而且不打任何日志**。

⇒ 类比的边界在这里：**硬件决定了"应该"怎么分组，DTS 决定了"内核以为"怎么分组，
两者不一致时内核不会发现。**

第二个误导点：**空调是一台设备，policy 不是**。
policy 不是 `struct device`，不注册在任何总线上，没有 driver 来绑它。
它是核心为每一组 CPU 分配的一个内部对象（02 篇有完整对比）。

---

## §2 governor、driver、核心，到底谁在做决定？

### 先举个例子

一家餐厅点菜，三个角色：

- **顾客**（负载）：饿了，但不会说"给我 1.8GHz"，只会说"我很饿"。
- **服务员**（governor）：看你有多饿，决定"上一份大份的"。他懂需求，但不会做菜。
- **厨师**（driver）：只管把"大份"这个指令变成实际动作 —— 开火、下锅。
  他不判断你饿不饿。
- **经理**（核心）：不点菜也不做菜。他管的是：菜单上有哪几种份量（频率表）、
  今天哪几样停售（限值）、以及**把服务员写的单子改成菜单上确实有的份量**。

关键在最后一句：**你说"1.5 份"，经理会把它改成菜单上最近的那一档，然后才递给厨师。**
这一步很多人不知道，于是产生"我明明设了 X，怎么变成 Y"的困惑。

### 回到代码

三层的分界线非常清楚：

| 角色 | 代表代码 | 它**不**做什么 |
|---|---|---|
| governor | `kernel/sched/cpufreq_schedutil.c`、`drivers/cpufreq/cpufreq_ondemand.c` | 不碰寄存器，不知道频率表长什么样 |
| 核心 | `drivers/cpufreq/cpufreq.c` | 不决定该跑多快 |
| driver | `drivers/cpufreq/cpufreq-dt.c` 等 | 不判断负载，只执行 |

"经理改单子"这一步就是 `__resolve_freq()`。证据在
`drivers/cpufreq/cpufreq.c:2303-2304`（`cpufreq_driver_target()` 里）：
拿到 `target_freq` 之后**先解析**，再往下走。紧跟着的那条 `pr_debug`
（`:2306`）把**请求值和解析后的值同时打出来**：

```
target for CPU %u: %u kHz, relation %u, requested %u kHz
```

⇒ 这一条打印是理解三层关系最好的实证：它证明"服务员写的"和"厨师收到的"
不是同一个数。开法见 07 §2.3。

再往下，"厨师"收到的是**档位下标**而不是频率：驱动实现的是
`->target_index`（05 §1.2 的一行式实现），核心已经把频率翻译成了表里的第几档。

### 这个类比什么时候会误导你？

**餐厅类比暗示"顾客只提需求，不能指定份量"。但 cpufreq 里顾客真的可以指定 ——
`scaling_setspeed` 就是。**只不过它有一个前提：得先把服务员换成
"完全听顾客的"那一种（userspace governor）。

代码上的门在 `drivers/cpufreq/cpufreq.c:900-901`：当前 governor 没有
`store_setspeed` 就直接 `-EINVAL`。⇒ 这不是"不支持"，是"当前这位服务员不接受点份量"。

第二个误导点：**硬件自治路线上厨师会自己改份量**。
高通 `qcom-cpufreq-hw` 上，硬件在温度高时自己降频，经理和服务员都不知道
（06 §4.2）。餐厅类比里没有"厨师擅自减量"这一说，但这里真的会发生 —— 见 §7。

第三个：**schedutil 这个服务员其实坐在厨房外面的另一个部门**（`kernel/sched/`），
它不是餐厅雇的 —— 见 §4。

---

## §3 为什么会有 policy 边界、perf domain 边界、OPP 共享边界三套？

### 先举个例子

一栋楼有三份"分区图"，各自为不同目的画：

1. **空调分区图**（policy）：哪几个房间共用一台空调 —— 决定**同频范围**。
2. **电表分区图**（Energy Model 的 perf domain）：哪几个房间合一个电表 ——
   决定**算电费时怎么算**。
3. **供货合同分区图**（OPP 共享）：哪几个房间在供应商那里算一个客户 ——
   决定**能买到哪些规格**。

三份图**通常一致**，因为它们描述的是同一栋楼。但它们是**三个不同的部门各自画的**，
所以完全可能不一致。而且不一致时，没有人会发出警报 —— 每个部门都觉得自己的图没问题。

### 回到代码

三套边界各自的载体：

| 边界 | 载体 | 定义在哪 |
|---|---|---|
| policy 边界 | `policy->related_cpus` | `include/linux/cpufreq.h:61` |
| perf domain 边界 | `struct em_perf_domain` | `include/linux/energy_model.h:55`（注释 `:40`） |
| OPP 共享边界 | OPP 表的 `shared_opp` / DTS 的 `opp-shared` | 消费点 `drivers/opp/of.c:1353` |

它们的来源**互不相同**，这是三套图存在的根源：

| 路线 | policy 边界从哪来 | perf domain 边界从哪来 |
|---|---|---|
| 通用 DT | DTS 的 `opp-shared`（`cpufreq-dt.c:232`） | 抄 policy 的：`cpufreq_register_em_with_opp()` 直接把 `policy->related_cpus` 传进去（`include/linux/cpufreq.h:1264-1268`） |
| SCMI | **固件的 perf domain**（`clocks` 里的那个 cell，06 §3.2） | 驱动自己注册（`scmi-cpufreq.c:262-286`） |
| 高通硬件自治 | `qcom,freq-domain` 的第二个 cell（`qcom-cpufreq-hw.c:527-528`） | 驱动自己注册 |

**看第一行**：通用 DT 路线上 EM 是**抄 policy** 的
（`include/linux/cpufreq.h:1266-1267` 把 `policy->related_cpus` 原样传给
`dev_pm_opp_of_register_em()`）⇒ 两套图必然一致。

**再看第二行**：SCMI 路线上两套图**各有来源**。
更要命的是，`scmi-cpufreq.c:169` 那里仍然调了
`dev_pm_opp_of_get_sharing_cpus()` —— 也就是**它同时还去问了 DTS 的 `opp-shared`**。
⇒ 固件说的边界和 DTS 说的边界如果不一致，就有两份图在互相打架（06 §3.3）。

**怎么自己对照三套图？**（07 §5 的两条命令合起来用）

```bash
# 图一：policy 边界
grep . /sys/devices/system/cpu/cpufreq/policy*/related_cpus

# 图二：perf domain 边界
cat /sys/kernel/debug/energy_model/*/cpus

# 图三：OPP 表边界（目录数 = 独立 OPP 表数）
ls /sys/kernel/debug/opp/
```

三条输出的分组应当一模一样。**不一样就是有一套配错了**，
而且它不会报错 —— 只会表现为"功耗不对"或"小核被大核带着跑"。

### 这个类比什么时候会误导你？

**"三份图描述同一栋楼"这个说法把它们说得太对等了。实际上三者的严重程度差别很大：**

| 图错了 | 后果 |
|---|---|
| policy 边界错 | **功能错**：频率互相覆盖，或者小核被大核拖着跑高频 |
| perf domain 边界错 | **性能/功耗错**，功能正常：EAS 选错核，但频率还是对的（06 §6.4） |
| OPP 共享边界错 | 通常表现为上面两者之一，因为它是前两者的输入 |

第二个误导点：**"电表"暗示 EM 只用来算账。实际上 EM 是调度器做决策的输入**，
`em_cpu_get()`（`include/linux/energy_model.h:169`）被调度器用来算"把任务放这个核上要花多少能量"。
电表只记录，EM 会**改变行为**。

---

## §4 schedutil 为什么住在 `kernel/sched/` 而不是 `drivers/cpufreq/`？

### 先举个例子

一家工厂里，"决定生产线速度"这件事可以有两种做法：

- **做法一（旧式）**：调度室每 20 毫秒派人去车间数一次"堆了多少活"，
  回来算个百分比，再决定加速还是减速。**信息是采样来的，总是滞后的。**
- **做法二（新式）**：干脆让**排产系统自己**在派活的时候顺手说一句
  "接下来的活需要这个速度"。因为排产系统本来就知道有多少活、什么时候来 ——
  它不需要"去数"，它**就是那个知道的人**。

做法二不需要定时器，也没有采样延迟。代价是：这段逻辑必须住在排产系统里面，
不能住在车间。

`ondemand` 是做法一，`schedutil` 是做法二。所以 schedutil 的代码位置不是随意的 ——
**它必须在调度器里面，因为它要用的信息只有调度器有。**

### 回到代码

调度器和 cpufreq 之间的接口只有一个小头文件，`include/linux/sched/cpufreq.h`
（**全文 38 行**），注释就写着 "Interface between cpufreq drivers and the scheduler"
（`:8`）。核心是一个回调结构：

```c
struct update_util_data {
       void (*func)(struct update_util_data *data, u64 time, unsigned int flags);
};
```

位置 `include/linux/sched/cpufreq.h:16-18`。

挂钩机制在 `kernel/sched/cpufreq.c`（**全文 74 行**）：
一个 per-CPU 指针（`:9`）、一个装（`:29`）、一个卸（`:54`）。就这么点东西。

schedutil 做的事就是把自己的函数挂上去。`sugov_start()`
（`kernel/sched/cpufreq_schedutil.c:775`）里 `:808`：

```c
cpufreq_add_update_util_hook(cpu, &sg_cpu->update_util, uu);
```

**挂哪个函数取决于 policy 里有几个 CPU**（`:798-803`）：

| 条件 | 挂上去的函数 |
|---|---|
| `policy_is_shared(policy)` 为真（`:798`） | `sugov_update_shared`（`:457`） |
| 单核 policy + 驱动支持 `adjust_perf` | `sugov_update_single_perf`（`:396`） |
| 单核 policy | `sugov_update_single_freq`（`:352`） |

⇒ 这里可以直接看到 §1 讲的 policy 概念在**代码路径**上的分叉：
"几个核共用一路时钟"决定了调度器每次派活时走哪一段代码。

**没有定时器的证据**：schedutil 的这三个函数都是被调度器调用的
（签名第一个参数是 `struct update_util_data *hook`），没有任何 `timer` / `workqueue`。
唯一的时间控制是节流：`sugov_should_update_freq()`（`:63`）比较
`delta_ns >= sg_policy->freq_update_delay_ns`（`:104`），
而那个阈值就是 `rate_limit_us` 换算来的（`:781`，07 §4.5）。

### 这个类比什么时候会误导你？

**"排产系统顺手说一句"暗示这个调用是免费的。它不是 —— 它在调度器的热路径上，
所以整条链被设计成"绝不能睡"。**

这条约束一路传导下去，最终变成了 05 §2.2 那四条规则：
驱动的 `->fast_switch` 不能睡、不能等固件回包、失败要返回 0 而不是错误码。
⇒ **schedutil 的代码位置和 `->fast_switch` 的"不能睡"是同一个决定的两面。**

第二个误导点：**"不需要定时器"不等于"随时更新"**。
`rate_limit_us` 这个节流阈值仍然存在（`:104`），
所以负载变了频率也可能暂时不动 —— 07 §3.5 里"tracepoint 一条记录都没有"
的第二种原因就是这个。

第三个：**schedutil 是 `bool` 不是 `tristate`**
（`drivers/cpufreq/Kconfig:191`）⇒ 它不能编成模块。
"住在调度器里"这件事在 Kconfig 上也有体现，不只是目录位置。

---

## §5 为什么一个"只打日志"的 notifier 能把 `fast_switch` 关掉？

### 先举个例子

一条工厂流水线有两种运行模式：

- **快车道**：机器直接换速，不通知任何人。
- **慢车道**：每次换速前后都要广播一次"注意，要换速了 / 已经换完了"，
  广播要一个个念给所有登记过的人听，**而且必须等他们都答应**。

现在来了一位实习生，说："我什么都不干，就想在旁边听着，记个笔记。"

听起来无害吧？但**只要有人要求收听广播，整条线就必须从快车道退回慢车道** ——
因为快车道**根本没有广播这个环节**。不是"广播给他一个人"，是"广播机制要重新打开"。

于是一个只想记笔记的人，把整条线的速度降下来了。

### 回到代码

两个标志摊开在结构体里，`include/linux/cpufreq.h:115-116`：

```c
bool			fast_switch_possible;
bool			fast_switch_enabled;
```

`possible` 是驱动的能力声明（我有 `->fast_switch`），
`enabled` 是当前实际有没有在用。上面 `:109-114` 那段注释解释了为什么要拆成两个。

**"实习生"就是 transition notifier。** 关键在于快慢两条路的**代码是分开的**：

| 路 | 函数 | 有没有通知 |
|---|---|---|
| 慢车道 | `cpufreq_notify_transition()`（`drivers/cpufreq/cpufreq.c:343`） | 有，`trace_cpu_frequency` 在 `:384` |
| 快车道 | `cpufreq_driver_fast_switch()`（`:2142`） | 没有通知链，只有 tracepoint（`:2161`，还包在 `:2159` 的开关里） |

⇒ 快车道**物理上没有那个广播环节**，所以只要有人要收听，就必须整体退回慢车道。

内核在这里做了两件事，取决于谁先来：

**情况一：notifier 先来，fast switch 后想开。** `cpufreq_enable_fast_switch()`
发现通知链非空就直接放弃，打这条 `pr_warn`（`drivers/cpufreq/cpufreq.c:506-507`）：

```
CPU%u: Fast frequency switching not enabled
```

紧跟着 `:508` 会**把占着位置的那几个 notifier 打印出来** ——
这是排查"为什么我的板子不走快车道"的唯一直接线索。

**情况二：fast switch 已经在跑，notifier 才来登记。** 这次轮到 notifier 被拒：
`drivers/cpufreq/cpufreq.c:2051` 那个门判断出 fast switch 正在使用，
`:2053` 返回 **`-EBUSY`**。

⇒ 所以同一件事有两个完全不同的表现：**先后顺序决定了谁失败**。
而且情况一是一条 `pr_warn`（能看见），情况二是一个返回码（模块加载失败，
`dmesg` 里可能只有一行 `insmod: ... Device or resource busy`）。

### 这个类比什么时候会误导你？

**"实习生"暗示这是个可以赶走的人。但很多时候占位的不是你的代码 ——
它可能是内核自己的某个功能。**

07 §6.2 把这条列成了故障注入手段之一：**加载任何一个注册 transition notifier
的模块**都能触发。你自己没写 notifier，不等于系统里没有。
⇒ 排查动作固定为：先看 `:508` 打出来的占位者名单，再决定动谁。

第二个误导点：**"退回慢车道"听起来只是慢一点。实际上它还改变了另一件事 ——
频率不变性（frequency invariance）的开关。**

`drivers/cpufreq/cpufreq.c:62` 定义了静态键 `cpufreq_freq_invariance`，
读取点 `:65`，打开在 `:2902`，关闭在 `:2934` 和 `:2967`。
它影响的是调度器算负载时用不用"按频率折算"。
⇒ 快慢车道之争不只是延迟问题，它牵动的是**调度器看到的负载数值本身**。

第三个：**慢车道并不是"每次都广播"** —— 它广播的是 `PRECHANGE` 和 `POSTCHANGE`
两次，而且 `:349` 那里有一句 `BUG_ON(irqs_disabled())`（07 §2.4）。
这条 `BUG_ON` 本身就是"这条路会睡"的证明。

---

## §6 为什么设置最大频率要"投票"，不能直接写？

### 先举个例子

一个会议室的空调温度，有三方都想管：

- **前台**（用户写 `scaling_max_freq`）："别超过 26 度。"
- **消防**（thermal 框架）："着火了，最高 20 度。"
- **省电模式**："白天没人，最高 28 度。"

如果三方都直接去改同一个旋钮，**最后一个动手的人赢** ——
消防刚设了 20 度，省电模式一秒后覆盖成 28 度，房间就烧了。

正确做法是：三方各自**登记一个上限**，由一个仲裁者取"所有上限里最严的那个"。
每一方都只能改自己那一票，谁也覆盖不了别人。这就是投票（QoS 约束）。

注意投票机制的一个副作用：**你写进去的值和你读出来的值可能不一样** ——
你投的是 26，仲裁结果是 20，你读到的就是 20。这不是 bug，是仲裁。

### 回到代码

policy 里带着一整套投票基础设施，`include/linux/cpufreq.h:86-88`：

| 字段 | 位置 | 作用 |
|---|---|---|
| `constraints` | `include/linux/cpufreq.h:86` | 仲裁器本体（所有票都挂在这里） |
| `min_freq_req` | `include/linux/cpufreq.h:87` | **核心自己那一票**（下限） |
| `max_freq_req` | `include/linux/cpufreq.h:88` | **核心自己那一票**（上限） |

⇒ 注意：核心不是仲裁者兼选民，它**也只是一个选民**。
`min_freq_req` / `max_freq_req` 就是"用户通过 sysfs 投的那一票"的载体。

写 sysfs 的代码坐实了这件事。`store_one()` 宏在
`drivers/cpufreq/cpufreq.c:739`，两个实例在 `:754` 和 `:755`
（也就是 `scaling_min_freq` / `scaling_max_freq`），宏体里唯一的动作是
`:750` 调 `freq_qos_update_request()`，然后 `:751`：

```c
return ret >= 0 ? count : ret;
```

**它从头到尾没有给 `policy->max` 赋过值。** 写 sysfs = 改自己那一票，
仲裁结果由别人算。

仲裁结果落地在 `refresh_frequency_limits()`（`drivers/cpufreq/cpufreq.c:1153`）
→ `cpufreq_set_policy()`（`:2561`），后者把仲裁完的值写进
`policy->min` / `policy->max`（`:2596-2602` 的两条 `WRITE_ONCE`），
紧接着 `:2606` 打一条 tracepoint。

⚠️ **不要试图直接写 `policy->min/max`**（比如在自己的驱动里）——
下一次任何一方改票，`refresh_frequency_limits()` 就会按仲裁结果覆盖掉你。

### 这个类比什么时候会误导你？

**"投票"暗示能看见谁投了什么。恰恰相反 —— 结果可见，投票者不可见。**

`cpu_frequency_limits` 这个 tracepoint（`include/trace/events/power.h:174`，
唯一调用点 `drivers/cpufreq/cpufreq.c:2606`）记录的是**仲裁后的最终区间**，
它**不带任何来源信息**。你只知道上限变成了 1.2GHz，不知道是 thermal 投的还是用户投的。

⇒ 07 §3.3 给的办法是把它和 PM QoS 自己的 tracepoint 配对看：
`pm_qos_update_target`（`include/trace/events/power.h:454`）。
两条合起来才能还原"谁改了票、结果变成多少"。

第二个误导点：**"最严的那个赢"不完全对 —— 下限的仲裁方向和上限相反**。
下限取所有票里**最大**的，上限取所有票里**最小**的。
所以有可能出现"下限票 > 上限票"的交叉情况，这时区间由 freq_qos 自己收敛，
表现为 `scaling_min_freq` 读回来的值超过你写的。

第三个：**投票只约束区间，不产生动作**。
票改完之后频率会不会立刻变，取决于 governor 什么时候再算一次
（schedutil 要等下一次调度事件 + `rate_limit_us` 节流，§4）。
⇒ "我写了 `scaling_max_freq`，`scaling_cur_freq` 没马上变"通常不是错。

---

## §7 硬件自己会调频了，为什么还要 cpufreq？

### 先举个例子

老式汽车：油门踏板直接连着节气门拉线，你踩多深、进多少气，一一对应。

新式汽车：踏板只是个**传感器**。你踩下去，是在告诉行车电脑"我想要这么多动力"，
电脑再结合水温、档位、排放、防滑，自己决定喷多少油。你的踏板不再"控制"发动机，
它只是**表达意图**。

那既然电脑自己会算，还要踏板干什么？—— 因为**电脑不知道你想干什么**。
它知道当前转速和温度，但不知道你是要超车还是要停车。

cpufreq 在硬件自治平台上就是这个踏板：**它提供意图，硬件提供执行。**

### 回到代码

高通那条路线（`drivers/cpufreq/qcom-cpufreq-hw.c`）是最典型的自治实现：
频率表不来自 DTS，而是从硬件的 LUT 寄存器里读出来（`:274` 附近，
`:267-270` 的注释说明了为什么这么做）。硬件在温度高时**自己降频**，
不通知内核（06 §4.2）。

这条路线带来一个非常具体的后果，也是 07 §4.2 的头号结论：

**arm64 上 `scaling_cur_freq` 读到的不是硬件寄存器，是内核的记账值。**

链条是这样的：`show_scaling_cur_freq()`（`drivers/cpufreq/cpufreq.c:721`）
先试 `arch_freq_get_on_cpu(policy->cpu)`（`:726`）——

| 架构 | 谁实现了 `arch_freq_get_on_cpu` |
|---|---|
| x86 | `arch/x86/kernel/cpu/aperfmperf.c:414`（真读硬件计数器） |
| arm64 | **没有人实现** ⇒ 落到 `drivers/cpufreq/cpufreq.c:716` 的 `__weak` 桩，`:718` 返回 0 |

（桩函数的声明在 `include/linux/cpufreq.h:1243`。）

于是 arm64 只剩最后一条分支：`:731-732` 直接返回 `policy->cur`，
也就是**内核以为的频率**。⇒ 高通硬件因为温度自己降了频，
`scaling_cur_freq` 里一点看不出来。

想看真实频率要用另一个文件：`cpuinfo_cur_freq`（`drivers/cpufreq/cpufreq.c:760`），
它调驱动的 `->get()`。但它是 `0400`
（`cpufreq_freq_attr_ro_perm(cpuinfo_cur_freq, 0400)` 在 `:933`）⇒ **只有 root 能读**，
而且驱动没实现 `->get()` 时这个文件根本不存在。

**那 cpufreq 在这种平台上还剩什么用？** 三件：

1. **表达意图**：governor 算出的目标值写给硬件（踏板的作用）。
2. **表达约束**：thermal / 用户 / 电源策略的投票（§6）—— 这是硬件自己**不可能知道**的信息。
3. **给调度器提供模型**：频率表和 Energy Model 是 EAS 选核的输入（§3）。
   硬件自治不影响调度器**需要知道**这些档位。

### 这个类比什么时候会误导你？

**"踏板"暗示意图一定会被采纳。硬件自治平台上不是 —— 硬件可以完全无视你。**

而且无视得**很安静**：既没有返回错误，也没有 tracepoint。
07 §3.5 把这一条列为"打开了 tracepoint 却一条记录都没有"的第一种原因，
并明确标为**设计如此，不是 bug**。

⇒ 由此得出一条通用判据：**看到一条 `cpu_frequency` 记录，只能说明
"内核认为它成功了"，不能说明硬件真的换到了那个频率。**

第二个误导点：**"新式汽车更聪明"暗示自治路线全面更优。它有一个明确的退化点 ——
可观测性。**通用 DT 路线上 OPP debugfs 目录和 DTS 一一对应，
自治路线上那张表来自硬件 LUT，**和任何 DTS 都对不上**（07 §5.2 的 ⚠️）。
排查时"和 DTS 对照"这个最常用的手段直接失效。

第三个：**踏板类比只有一路输入。真实系统里"意图"有好几路**，
而且它们之间的仲裁在内核侧就已经做完了（§6），硬件只收到一个最终值。

---

## §8 boost 为什么是"另一个文件"，不是一个更高的档位？

### 先举个例子

一台电梯，正常运行到 20 层。但机械上其实能到 22 层 —— 只是 21、22 层的钢缆负荷
超出了长期使用的设计值，只能短时间用。

物业有两种做法：

- **做法一**：把 21、22 层加进楼层按钮面板，跟其他楼层一样随便按。
- **做法二**：楼层按钮面板不动，另外在管理室装一个**总闸**。总闸不合上时，
  21、22 层的按钮按了不响应；合上之后才生效。

内核选了做法二。原因很直接：**"能不能用"和"要不要用"是两个决定，做主的人也不同。**
"能到 22 层"是硬件属性（钢缆规格），"允不允许用"是运营策略（今天要不要冒这个风险）。
把两件事塞进同一个面板，就没法让管理员一键收回权限了。

### 回到代码

从 DTS 到那个"总闸文件"是**六步、跨三个框架**的一条链（06 §6.3 有完整版）：

| 步 | 在哪 | 做什么 |
|---|---|---|
| 1 | `drivers/opp/of.c:945` | 读 OPP 节点的 `turbo-mode`，把这一档标成 turbo |
| 2 | `drivers/opp/cpu.c:69-71` | 生成频率表时给这一档打上 `CPUFREQ_BOOST_FREQ` 标记 |
| 3 | `drivers/cpufreq/freq_table.c:25` | `policy_has_boost_freq()` 扫表，看有没有带这个标记的档 |
| 4 | `drivers/cpufreq/cpufreq-dt.c:138` | 有的话就往下走 |
| 5 | `drivers/cpufreq/cpufreq-dt.c:140` | 给驱动置上 boost 能力 |
| 6 | `drivers/cpufreq/cpufreq.c:2891-2895` | 创建那个全局 `boost` 文件 |

⇒ **注意第 6 步的位置：`boost` 文件是全局的（在 `cpufreq/` 根下），
不在 `policyN/` 里面。**这就是"管理室的总闸"——不是每层楼一个开关。

再看"档位其实一直在表里"这件事。turbo 档**从来没有被从频率表里删掉** ——
它只是被打了标记。所以：

- 关着的时候，`scaling_available_frequencies` 里也列不出它（频率表遍历会跳过 boost 档）；
- 打开之后它才参与调频。

⇒ 这正是"面板按钮没变、总闸变了"的代码对应物。

写 `boost` 文件的入口在 `drivers/cpufreq/cpufreq.c:2808` 一带，
它有两个值得记住的返回行为（07 §7.5）：

| 行为 | 位置 | 语义 |
|---|---|---|
| 已经是目标状态就直接返回 0 | `drivers/cpufreq/cpufreq.c:2805-2806` | **幂等**：重复打开不报错 |
| 驱动不支持 boost | `drivers/cpufreq/cpufreq.c:2803` | `-EINVAL` |

### 这个类比什么时候会误导你？

**"总闸"暗示所有平台都有这个闸。arm64 上很多平台没有 ——
而且原因不是硬件不支持，是那条链在第 1 步就断了。**

链条第 1 步读的是 DTS 里的 `turbo-mode`。**DTS 不写这个属性，
后面五步全都不发生，`boost` 文件根本不会出现。**
不写不是错，也没有任何提示 —— 它就是"这块板子不开 turbo"。

第二个误导点：**"内核统一管理这个闸"不完全对 —— 自治路线自己造闸。**
高通那条路线不走上面第 1~5 步，它在
`drivers/cpufreq/qcom-cpufreq-hw.c:596-597` 自己判断硬件 LUT 里有没有 boost 档。
⇒ 同一个 `boost` 文件，两条完全不同的来路。而 SCMI 路线**根本没有 boost**。

第三个：**"21 层只能短时间用"这个物理约束内核并不强制**。
打开 `boost` 之后没有任何"用够了自动收回"的机制 ——
把频率拉回来是 thermal 的事（通过 §6 的投票），不是 boost 开关的事。

---

## §9 为什么 cpufreq 核心一条 DT 属性都不读？

### 先举个例子

一家公司的报销系统。你以为"报销规则"写在财务部的系统里，
结果去看代码发现：财务系统**一条规则都没有硬编码**。
所有规则都在一份外部配置里，由**人事系统**和**采购系统**分别去读，
读完把结论（"这笔能报，上限 500"）告诉财务系统。

财务系统只做一件事：**按别人给的结论执行**。

这样做有个明显的好处 —— 换一套规则不用改财务系统。
但也有一个明显的代价：**出问题时你在财务系统里找不到任何线索**。
日志里只有"上限 500"，没有"这个 500 是从哪条规则来的"。

`drivers/cpufreq/cpufreq.c` 就是这个财务系统。

### 回到代码

06 §1.1 那张总表的结论：**cpufreq 核心不解析任何 DT 属性。**
DTS 的读取全部发生在三个别的地方：

| 谁读 | 在哪 | 读什么 |
|---|---|---|
| 通用 DT 驱动 | `drivers/cpufreq/cpufreq-dt.c` | 通过 OPP 框架间接读 |
| OPP 框架 | `drivers/opp/of.c` | `operating-points-v2`、`opp-hz`、`opp-microvolt`、`opp-shared`、`turbo-mode` … |
| 平台白名单 | `drivers/cpufreq/cpufreq-dt-platdev.c` | 只匹配 compatible，决定要不要给这块板子创建设备 |

**最关键的那一条属性（`opp-shared`，决定 policy 边界）
甚至不在 `drivers/cpufreq/` 里读** —— 消费点在 `drivers/opp/of.c:1353`，
`drivers/cpufreq/cpufreq-dt.c:232` 只是去问结论。

而"这块板子到底要不要走通用 DT 路线"是由一张白名单决定的：
`drivers/cpufreq/cpufreq-dt-platdev.c:179` 那里如果没匹配上，
**整个 cpufreq 就不会起来** —— 而且不打日志。

⇒ 这就是"财务系统里找不到线索"的具体形态：
`/sys/devices/system/cpu/cpufreq/` 目录不存在，`dmesg` 一片干净。
07 §7.2 把这一条列进了"目录不存在"的四个原因，其中三个都是静默的。

**这个分层的实际收益**：正因为核心不读 DT，同一个核心才能同时服务
通用 DT、SCMI 固件、高通硬件自治三条来路完全不同的路线（§3 那张来源表）。
如果核心自己解析 `opp-shared`，SCMI 就没法用固件的 perf domain 覆盖它。

### 这个类比什么时候会误导你？

**"财务系统完全不读配置"这个说法有一个例外 —— 核心确实读"配置"，
只不过读的是命令行，不是 DT。**

`drivers/cpufreq/cpufreq.c:2996` 和 `:2997` 是核心唯一的两个模块参数
（`cpufreq.off` 和 `cpufreq.default_governor`，07 §1.3）。
⇒ 排查"目录不存在"时，**`/proc/cmdline` 要和 DTS 一起看** ——
`cpufreq.off=1` 造成的症状和 DTS 配错**一模一样**：目录没有，日志没有。

第二个误导点：**"读结论不读规则"暗示核心完全被动。但核心自己会造一个 DTS
里没有的东西 —— sysfs 上的 15 个文件。**这些文件的存在与否有它自己的规则
（比如 `cpuinfo_cur_freq` 要求驱动实现 `->get()`，`stats/` 要求
`CPU_FREQ_STAT`，`boost` 要求 §8 那条链），和 DTS 无关。

第三个：**分层带来的错误消息归属**。06 §7.4 那张 24 行的
「错误消息 → 原因」映射表里，⚠️ **大部分消息来自 `drivers/opp/`，不是
`drivers/cpufreq/`**。所以按 "cpufreq" 去 grep 内核源码找报错来源，多半会白找。

---

## §10 读完这篇之后去哪

| 你现在想干什么 | 去哪篇 |
|---|---|
| 找某个函数怎么用、参数是什么 | `01-api.md` |
| 想看清 policy / governor / driver 三个结构体的字段和关系图 | `02-datastructures.md` |
| 想跟着注册流程逐行走一遍 | `03-internals.md` |
| 想弄清 governor 那几条路线、Energy Model、thermal 边界 | `04-extensions.md` |
| 要自己写一个 cpufreq 驱动或 governor | `05-usage.md` |
| DTS 该怎么写、写错了什么症状 | `06-devicetree.md` |
| 板子上出问题了，要开日志/tracepoint/看 sysfs | `07-debug.md` |
| 想快速复习整个子系统 | `00-overview.md` |
| 想知道本文档集哪些结论可信、哪些是推断 | `99-evidence.md` |

**九个概念的一句话浓缩**（复习用）：

1. **policy 不是"一个 CPU 的策略"，是"共用一路时钟的那组 CPU"** —— 三张掩码，不是一个 CPU 号。
2. governor 提需求、核心改档位、driver 只执行；`__resolve_freq()` 是那个改档位的人。
3. **policy / perf domain / OPP 共享是三套独立的边界图**，通用 DT 路线下后者抄前者，SCMI 路线下各有来源。
4. schedutil 住在调度器里，因为它要的信息只有调度器有 —— 代价是整条链不能睡。
5. 快慢车道的代码是分开的，所以**一个只听广播的 notifier 会让整条线退回慢车道**。
6. 写 `scaling_max_freq` 是**投一票**，不是赋值；结果可见，投票者不可见。
7. 硬件自治平台上 cpufreq 提供的是**意图和约束**，arm64 的 `scaling_cur_freq` 是记账值不是寄存器。
8. `boost` 是一个全局总闸，档位一直在表里；那条链从 DTS 的 `turbo-mode` 起算，共六步。
9. 核心一条 DT 属性都不读 —— 所以报错大多来自 `drivers/opp/`，而且最要命的失败是静默的。
