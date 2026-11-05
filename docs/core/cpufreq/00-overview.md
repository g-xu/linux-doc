---
title: 总览
---

# cpufreq 子系统总览

**这一篇的定位是「复习入口」。** 隔几个月回来，只读这一篇就能恢复对
cpufreq 的整体认识；需要细节再按 §8 的导读表往后翻。

- 版本：Linux **6.1.158**（本地树未对 cpufreq 打任何补丁，验证方法见 99 篇）
- 架构：**arm64 only**。x86 的 `intel_pstate` / `amd-pstate` / `acpi-cpufreq`
  以及 ACPI 相关内容一律不写（裁掉了什么见 99 §2.7）
- 类型：**框架型**子系统 —— 没有自己的 `bus_type`，但**有完整的 sysfs 界面**

---

## §1 这个子系统是干什么的

一句话：**cpufreq 负责决定"CPU 现在应该跑多快"，并把这个决定落到硬件上。**

它要解决的矛盾很朴素：CPU 跑得越快越费电、越热；跑得越慢越省电，但活干不完。
而"该跑多快"这个问题**没有唯一答案** —— 它取决于当前有多少活、
温度余量还有多少、用户是插着电还是用电池、以及这台机器是要低延迟还是要长续航。

所以 cpufreq 不是一个"设置频率的函数"，而是一套**把多方意见汇总成一个频率值**的框架。
它的三层职责：

| 层 | 谁 | 职责 | 代表代码 |
|---|---|---|---|
| 决策 | governor | 看负载，决定目标频率 | `kernel/sched/cpufreq_schedutil.c`（895 行） |
| 仲裁 | 核心 | 收集约束、裁剪到合法档位、维护 sysfs 和统计 | `drivers/cpufreq/cpufreq.c`（**2998 行**） |
| 执行 | driver | 把频率变成时钟/电压/固件命令 | `drivers/cpufreq/cpufreq-dt.c` 等 **89 个驱动** |

### §1.1 它管什么，不管什么

**管**：

- CPU 核心的**频率**（以及通过 OPP 框架顺带管的**电压**）
- 频率的**合法档位表**（哪些频率是可用的）
- 频率的**上下限约束**（thermal、用户、电源策略各投一票）
- 频率变更的**通知**和**统计**（`stats/` 目录）
- 给调度器提供**能量模型**（Energy Model）的输入

**不管**（这些是相邻子系统的事）：

| 不管的事 | 谁管 |
|---|---|
| CPU 的空闲状态（进不进 WFI/掉电） | `cpuidle` |
| 频率-电压对应关系表的解析 | **OPP 框架**（`drivers/opp/`） |
| 时钟树、PLL 怎么配 | **clk 子系统** |
| 电压怎么调 | **regulator 子系统** |
| 温度超了要降到多少 | **thermal 框架**（它只往 cpufreq 投一票） |
| 把任务放到哪个核上 | **调度器**（EAS，但它读 cpufreq 提供的 EM） |
| GPU / DDR / NPU 的调频 | `devfreq`（另一套框架，接口相似但独立） |

最后一行值得记住：**cpufreq 只管 CPU**。名字里的 "cpu" 是限定词，
不是修饰词 —— 其他 IP 的 DVFS 走 devfreq。

### §1.2 规模速览

| 文件 | 行数 | 定位 |
|---|---|---|
| `drivers/cpufreq/cpufreq.c` | 2998 | 核心：policy 生命周期、sysfs、限值仲裁、driver 注册 |
| `include/linux/cpufreq.h` | 1269 | 全部对外结构体和 API |
| `kernel/sched/cpufreq_schedutil.c` | 895 | 默认 governor（arm64） |
| `drivers/cpufreq/cpufreq_governor.c` | 580 | 旧式采样 governor 的公共骨架 |
| `drivers/cpufreq/cpufreq_ondemand.c` | 487 | 旧式 governor 之一 |
| `drivers/cpufreq/freq_table.c` | 370 | 频率表遍历与裁剪 |
| `drivers/cpufreq/cpufreq_conservative.c` | 344 | 旧式 governor 之一 |
| `drivers/cpufreq/cpufreq_stats.c` | 290 | `stats/` 目录 |
| `drivers/cpufreq/cpufreq_userspace.c` | 142 | 完全听用户的 governor |
| `kernel/sched/cpufreq.c` | **74** | 调度器 ↔ cpufreq 的挂钩机制，就这么小 |
| `drivers/cpufreq/cpufreq_performance.c` | 45 | 永远最高频 |
| `drivers/cpufreq/cpufreq_powersave.c` | 39 | 永远最低频 |
| `include/linux/sched/cpufreq.h` | **38** | 两个子系统之间的全部接口 |
| 驱动目录 | 89 个 `.c` / **39396** 行 | 只讲契约，选代表驱动详读（见 99 §2.2） |

⇒ 注意最后两行小文件的行数：**调度器和 cpufreq 之间的耦合面只有 112 行**。
这个数字是理解 §5.2 的关键。

---

## §2 硬件背景：arm64 上的 DVFS 到底在动什么

**这一节和内核代码无关**，讲的是硬件。不了解这些，后面所有"为什么内核要这样设计"
都会变成死记硬背。

### §2.1 为什么调频能省电：两个平方关系

CMOS 电路的动态功耗有一个经典近似：

```
P_dynamic ≈ α · C · V² · f
```

其中 `α` 是翻转率、`C` 是等效电容、`V` 是供电电压、`f` 是时钟频率。
关键在于 **`V` 是平方项，`f` 是一次项**。

单独降频（`V` 不变）：功耗线性下降，但**同一份工作要跑更久** ——
总能量几乎没省，因为 `能量 = 功率 × 时间`。这就是"只降频不降压等于白降"的由来。

真正省电的是**降频 + 降压**：频率降一半、电压降 20%，功耗降到
`0.8² × 0.5 = 32%`，而时间只翻一倍 ⇒ **总能量降到 64%**。

⇒ 这解释了 cpufreq 最核心的一个设计事实：**频率和电压必须成对调整**，
而"哪个频率配哪个电压"是硅片的物理特性，内核不可能算出来 ——
只能查表。**这张表就是 OPP（Operating Performance Point）表。**

还有一项不随频率变的功耗：

```
P_static ≈ V · I_leakage        （漏电，和温度强正相关）
```

漏电只跟电压和温度有关，跟频率无关。⇒ 由此得出一个反直觉的结论：
**有时候"快点跑完然后整个核掉电"比"慢慢跑一直开着"更省电** ——
这个策略叫 race-to-idle。它是 cpufreq 和 cpuidle 会互相打架的根源，
也是为什么 `performance` governor 在某些负载上反而更省电。

### §2.2 为什么电压不能随便降：时序余量

数字电路里，一级组合逻辑的传播延迟大致随电压下降而增加。
电压越低，门电路翻转越慢，一个时钟周期内信号"跑不完"关键路径就会**采到错值**。

所以每个频率都有一个**最低可用电压**（`V_min@f`）。低于它，芯片不会报错 ——
它会**算错**，表现为随机的数据损坏、异常、死机。

这个下限还要留余量，因为它随以下因素漂移：

- **工艺偏差**：同一批晶圆上不同 die 的速度不一样（快片 / 慢片）
- **温度**：低温和高温下延迟特性都会变差（temperature inversion）
- **老化**：用久了会变慢

⇒ 所以量产芯片会**逐片测速分档**（speed binning），把结果烧进片上
eFuse / OTP。这就是 arm64 上一类很常见的驱动形态：
`sun50i-cpufreq-nvmem.c`、`ARM_QCOM_CPUFREQ_NVMEM`（`drivers/cpufreq/Kconfig.arm:153`）——
**先读 eFuse，再决定用哪一套 OPP 表**。同一个 DTS，不同的片子跑不同的电压。

这也解释了为什么 OPP 表里的电压常常是三个值（`opp-microvolt` 可以写
`<目标 最小 最大>`）：给 regulator 一个容差窗口，而不是一个死值。

### §2.3 频率是怎么变的：PLL、分频器、和"换挡"的代价

arm64 SoC 上给 CPU 供时钟的典型结构：

```
  晶振 (19.2/24/26 MHz)
        │
        ▼
   ┌─────────┐
   │   PLL   │  倍频到 GHz 级
   └────┬────┘
        │
        ▼
   ┌─────────┐
   │  分频器 │  /1 /2 /4 ...
   └────┬────┘
        │
        ▼
   CPU 簇时钟  ← 一路时钟供整个簇，不是每核一路
```

改频率有两条路，代价差一个数量级：

| 手段 | 做法 | 代价 | 频率粒度 |
|---|---|---|---|
| **改分频器** | 只动分频比 | 快，通常几微秒 | 粗（只有 /1 /2 /4…） |
| **重锁 PLL** | 改倍频系数，等 PLL 重新锁定 | 慢，几十~几百微秒，**期间时钟不可用** | 细 |

"PLL 重锁期间时钟不可用"意味着 CPU 得先切到一个备用时钟源（通常是晶振或另一个
固定 PLL）上，等新频率锁定后再切回来。⇒ 这就是 DTS 里
`clock-latency-ns` 属性的物理含义：**换一次挡要多久**。

这个延迟直接决定了 governor 的采样周期：如果换挡要 300µs，
每 100µs 采样一次就是在自找麻烦 —— 大部分时间都花在切换上。
内核确实是这么用它的：schedutil 的默认节流阈值 `rate_limit_us`
就是从这个延迟推出来的（`kernel/sched/cpufreq_schedutil.c:721`，07 §4.5）。

### §2.4 电压是怎么变的：PMIC 和摆率

电压由**外部 PMIC**（电源管理芯片）提供，通过 I2C / SPI / 或专用串行总线
（如 SPMI）控制。改电压是**跨芯片操作**：

- 要发一次总线事务（I2C 上就是几百微秒起）
- 电压不会瞬变，有**摆率**（slew rate，典型几 mV/µs）
- 升压要**等稳定**才能升频；降频要**先降频再降压**

⇒ 这就是"升频降频顺序不同"这条规则的物理来源：

```
升频：先升压 ──等稳定──> 再升频     （否则时序不够，算错）
降频：先降频 ──────────> 再降压     （否则时序不够，算错）
```

两个方向都必须"高电压那一侧先满足"。内核里这套顺序**不是 cpufreq 实现的**，
是 OPP 框架的 `dev_pm_opp_set_rate()` 实现的 —— 这也是为什么 cpufreq 驱动
常常只有一行（05 §1.2 那个一行式 `->target_index`）。

另一个后果：**改电压这条路会睡**（I2C 传输要等）。而调度器热路径上不能睡。
这一条约束一路传导，最后长成了 `->fast_switch` 和 `->target_index` 两套回调
（§5.3）。

### §2.5 为什么一个簇只能有一个频率

看 §2.3 那张图的最后一行：**一路时钟供整个簇**。

物理上就是一个 PLL 的输出走一棵时钟树，扇出到簇内所有核。要做到"核 A 1.8GHz、
核 B 800MHz"，需要**每核一个独立 PLL + 独立电压域** —— 面积和成本都不划算，
主流 arm64 SoC 不这么做。

所以硬件的真实约束是：

- **簇内同频、同压**
- **簇间独立**

⇒ 这是内核里 `struct cpufreq_policy` 存在的全部理由：
它就是"共用一路时钟的那组核"。08 §1 把这一条当成整个子系统**最容易被类比错**
的关系单独讲了一遍。

一个典型 arm64 大小核 SoC 的分区：

```
┌──────────────────────────┐   ┌──────────────────┐   ┌──────────┐
│  小核簇 (4×Cortex-A55)   │   │ 大核 (3×A78)     │   │ 超大核   │
│  PLL_LITTLE / VDD_CPU_L  │   │ PLL_BIG/VDD_CPU_B│   │ (1×X1)   │
│  400MHz ~ 1.8GHz         │   │ 700M ~ 2.4GHz    │   │ ~2.8GHz  │
└──────────────────────────┘   └──────────────────┘   └──────────┘
        policy0                      policy4            policy7
```

⇒ 三个簇 = 三个 policy = sysfs 下三个 `policyN` 目录（编号是簇内第一个
CPU 号，不是 0/1/2）。

### §2.6 DynamIQ 之后的复杂化

Arm 的 DSU（DynamIQ Shared Unit）把不同类型的核放进**同一个 cluster**
（共享 L3），但**电压/频率域仍然可以分开**。⇒ 由此产生一个容易搞混的点：

**"cluster"（拓扑上的簇，共享 cache）和"频率域"（共用一路时钟）
在 DynamIQ 上不是一回事。** cpufreq 关心的是后者。
所以不要用 `/sys/devices/system/cpu/cpu0/topology/` 下的信息去推断 policy 边界 ——
要看 `related_cpus`（08 §1）。

### §2.7 三条硬件控制路线

arm64 上"内核怎么把频率落到硬件"有三种截然不同的形态。这是理解驱动层的骨架：

| 路线 | 内核做什么 | 硬件/固件做什么 | 代表驱动 |
|---|---|---|---|
| **① 通用 DT / 直控** | 自己调 clk 和 regulator | 被动执行 | `cpufreq-dt.c` |
| **② 固件代理** | 发一条消息给固件 | 固件（运行在更高特权级或独立 MCU）去配 PLL/PMIC | `scmi-cpufreq.c` |
| **③ 硬件自治** | 往寄存器写一个"目标性能等级" | 专用硬件模块自己完成整个调频，还会自己限频 | `qcom-cpufreq-hw.c` |

三条路线的差别不只是"谁动手"，还包括**内核能看到什么**：

| | ① 直控 | ② 固件 | ③ 自治 |
|---|---|---|---|
| 频率表来源 | DTS 的 OPP 表 | 固件（协议查询） | 硬件 LUT 寄存器 |
| policy 边界来源 | DTS 的 `opp-shared` | 固件的 perf domain | 寄存器 + DT 的 domain 号 |
| 换挡会不会睡 | 会（I2C 改压） | 看协议（SCMI 有快通道） | 不会（写寄存器） |
| 硬件自己降频 | 不会 | 可能 | **会，而且不告诉内核** |
| 出问题好不好查 | 好（和 DTS 对得上） | 中 | 差（表和 DTS 对不上） |

⇒ 最后一行那个 是很多"频率明明设了却没生效"问题的答案：
**路线 ③ 上硬件会因为温度或电流限制自己降频，`scaling_cur_freq` 里完全看不出来**
（07 §4.2、08 §7）。

### §2.8 温度和电流限制：另一条"降频的手"

CPU 满负荷跑几秒钟就会撞上两种限制：

- **热限制**：结温超标会加速老化甚至损坏 ⇒ 必须降频降压
- **电流/功率限制**：PMIC 和供电路径有上限（尤其手机电池老化后）

内核侧的做法是让 **thermal 框架成为 cpufreq 的一个投票者** ——
它不直接改频率，而是**降低上限**（08 §6 讲了为什么必须是投票）。
硬件侧（路线 ③）还会有自己独立的一套限流（LMH 之类），
两套机制**同时存在且互不知情**。

⇒ 这就是"看起来像 cpufreq 的 bug，其实是热"这一大类问题的来源。
排查的第一步永远是：**先确认现在是不是被限了**（07 §8.1 的速查命令）。

### §2.9 这一节和后面各篇的对应关系

| 硬件事实 | 内核里长成了什么 | 详见 |
|---|---|---|
| 频率电压必须成对 | OPP 框架，cpufreq 只调它 | 04 篇 |
| 一簇一时钟 | `struct cpufreq_policy` + 三张 CPU 掩码 | 08 §1 |
| 换挡有延迟 | `clock-latency-ns` → `rate_limit_us` | 06 §6.5 |
| 改电压会睡 | `->target_index`（可睡）vs `->fast_switch`（不可睡） | 05 §2.2 |
| 芯片逐片分档 | nvmem/eFuse 类驱动，运行时选 OPP 表 | 04 篇 |
| 硬件自己会限频 | 路线 ③ 的可观测性缺口 | 08 §7 |
| 热和电流是外部约束 | freq_qos 投票机制 | 08 §6 |

---

## §3 概念模型：五个名词和它们的关系

### §3.1 分层图

```
        ┌───────────────────────────────────────────────┐
        │  用户态                                        │
        │  /sys/.../cpufreq/policyN/*  ·  cpupower       │
        └───────────────┬───────────────────────────────┘
                        │ sysfs read/store
   ┌────────────────────┼────────────────────────────────────────┐
   │  投票者（约束）     │                                        │
   │  thermal ──┐       │       ┌── 用户 (scaling_min/max_freq)  │
   │  电源策略 ─┼──────► freq_qos 仲裁 ◄──┘                       │
   └────────────┼───────┼────────────────────────────────────────┘
                │       │  policy->min / policy->max
   ┌────────────┴───────▼────────────────────────────────────────┐
   │  governor（决策层）                                          │
   │  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐ │
   │  │ schedutil │  │ ondemand /    │  │ performance /    │ │
   │  │ (调度器驱动) │  │ conservative  │  │ powersave /      │ │
   │  │ kernel/sched │  │ (定时器采样)  │  │ userspace        │ │
   │  └──────┬───────┘  └───────┬───────┘  └────────┬─────────┘ │
   └─────────┼──────────────────┼───────────────────┼───────────┘
             │  目标频率         │                   │
   ┌─────────▼──────────────────▼───────────────────▼───────────┐
   │  核心 drivers/cpufreq/cpufreq.c                             │
   │  · policy 生命周期    · 频率表裁剪 __resolve_freq()          │
   │  · 限值仲裁           · 通知链 + tracepoint + stats          │
   └─────────┬──────────────────────────────┬────────────────────┘
             │ ->target_index (可睡)         │ ->fast_switch (不可睡)
   ┌─────────▼──────────────────────────────▼────────────────────┐
   │  driver（执行层，89 个）                                      │
   │  cpufreq-dt · scmi-cpufreq · qcom-cpufreq-hw · tegra194 …    │
   └─────────┬───────────────┬───────────────┬────────────────────┘
             │               │               │
        ┌────▼────┐    ┌─────▼─────┐   ┌─────▼──────┐
        │ OPP 框架│    │  SCMI 固件│   │ 硬件 LUT   │
        │ clk+reg │    │           │   │ 寄存器     │
        └─────────┘    └───────────┘   └────────────┘
```

### §3.2 五个名词

| 名词 | 是什么 | 关键认知 |
|---|---|---|
| **policy** | 共用一路时钟的那组 CPU | **不是"一个 CPU 的策略"**。它是频率域。08 §1 |
| **governor** | 决定目标频率的算法 | 每个 policy 挂一个；arm64 默认 **schedutil** |
| **driver** | 把频率落到硬件的那一层 | **全系统只能注册一个** cpufreq driver |
| **频率表** | 合法档位的数组 | 来自 OPP / 固件 / 硬件 LUT 三者之一（§2.7） |
| **约束（freq_qos）** | 上下限的多方投票 | 写 sysfs 是投票不是赋值。08 §6 |

**"全系统只能注册一个 driver"这一点最容易被忽略**，它带来两个后果：
(1) 一块板子上不能同时用 `cpufreq-dt` 和 `scmi-cpufreq`；
(2) 平台白名单（`drivers/cpufreq/cpufreq-dt-platdev.c:179`）的作用就是
决定"这块板子归谁"。

### §3.3 三个数量关系

记住这三条，很多困惑会自动消失：

```
1 个 driver  :  N 个 policy   :  M 个 CPU        （N ≤ M）
   全系统唯一     一簇一个         每核一个

1 个 policy  :  1 个 governor  :  1 套 tunable
   但 tunable 目录可能是全局共享的（07 §4.5）
```

⇒ 第三行那个"可能"是 `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` 标志决定的。
不带这个标志时，**改小核的 governor 参数会同时改掉大核的** —— 这不是 bug。

---

## §4 数据结构速览

完整字段和关系图在 02 篇，这里只留复习需要的骨架。

### §4.1 三个主结构

```
   struct cpufreq_driver          全系统唯一
   ├── .name / .flags
   ├── .init / .exit / .verify
   ├── .target_index  ← 可睡的换挡回调
   ├── .fast_switch   ← 不可睡的换挡回调
   ├── .get           ← 读硬件真实频率（可选）
   └── .attr          ← 驱动追加的 sysfs 文件
            │  注册时给每个频率域建一个 ↓
            ▼
   struct cpufreq_policy          一簇一个
   ├── cpumask cpus          在线成员      include/linux/cpufreq.h:60
   ├── cpumask related_cpus  全部成员      include/linux/cpufreq.h:61  这才是频率域边界
   ├── cpumask real_cpus     实际存在的    include/linux/cpufreq.h:62
   ├── unsigned int cpu      代表核        include/linux/cpufreq.h:66  不是"主人"
   ├── cpuinfo               硬件上下限    include/linux/cpufreq.h:69
   ├── min / max / cur       当前区间和值
   ├── constraints           freq_qos 仲裁器  include/linux/cpufreq.h:86
   ├── min_freq_req/max_..   核心自己那两票 include/linux/cpufreq.h:87-88
   ├── freq_table            频率表指针      include/linux/cpufreq.h:90
   ├── governor + governor_data
   ├── fast_switch_possible  驱动有这个能力  include/linux/cpufreq.h:115
   └── fast_switch_enabled   现在真的在用    include/linux/cpufreq.h:116
            │  挂一个 ↓
            ▼
   struct cpufreq_governor        每 policy 一个实例
   ├── .init / .exit / .start / .stop
   ├── .limits          ← 限值变了通知它
   ├── .store_setspeed  ← 只有 userspace governor 有（drivers/cpufreq/cpufreq.c:900-901 的门）
   └── .flags
```

`fast_switch_possible` 和 `fast_switch_enabled` 拆成两个字段的原因写在
`include/linux/cpufreq.h:109-114` 的注释里 —— 08 §5 讲了为什么。

### §4.2 调度器那一侧：只有两个结构

```
   struct update_util_data                 include/linux/sched/cpufreq.h:16-18
   └── void (*func)(data, time, flags)     ← 唯一的字段就是一个函数指针

   DEFINE_PER_CPU(struct update_util_data __rcu *, cpufreq_update_util_data)
                                           kernel/sched/cpufreq.c:9
```

装卸各一个函数：`cpufreq_add_update_util_hook()`（`kernel/sched/cpufreq.c:29`）、
`cpufreq_remove_update_util_hook()`（`:54`）。

⇒ **整个"调度器驱动调频"机制的数据结构就是一个 per-CPU 函数指针。**
schedutil 在 `sugov_start()`（`kernel/sched/cpufreq_schedutil.c:775`）里
把自己挂上去（`:808`），挂哪个函数取决于 policy 是不是多核（`:798-803`）。

### §4.3 相邻框架的结构（只记边界）

| 结构 | 属于 | 在哪 | cpufreq 怎么用它 |
|---|---|---|---|
| `struct em_perf_domain` | Energy Model | `include/linux/energy_model.h:55` | 注册给调度器做 EAS 输入 |
| OPP 表 | OPP 框架 | `drivers/opp/` | 频率表的来源（路线 ①） |
| `struct freq_qos_request` | PM QoS | — | policy 里的那两票 |

三者的边界**都不等于 policy 边界**，而且不一致时没有告警 —— 08 §3 专门讲这个。

---

## §5 机制速览

### §5.1 一次调频的三条路

```
 ┌── 慢路（可睡）────────────────────────────────────────────┐
 │  governor 算出目标                                        │
 │    → cpufreq_driver_target()                              │
 │       → __resolve_freq()  裁剪到表里的合法档  drivers/cpufreq/cpufreq.c:2303-2304
 │       → 通知链 PRECHANGE     drivers/cpufreq/cpufreq.c:343（BUG_ON(irqs_disabled()) :349）
 │       → 驱动 ->target_index()  → OPP → clk + regulator     │
 │       → 通知链 POSTCHANGE + trace_cpu_frequency  drivers/cpufreq/cpufreq.c:384
 └───────────────────────────────────────────────────────────┘

 ┌── 快路（不可睡）─────────────────────────────────────────┐
 │  schedutil 在调度热路径上直接                              │
 │    → cpufreq_driver_fast_switch()      drivers/cpufreq/cpufreq.c:2142     │
 │       → 驱动 ->fast_switch()   无通知链                  │
 │       → trace_cpu_frequency（包在 :2159 的开关里，:2161）  │
 └───────────────────────────────────────────────────────────┘

 ┌── 自治路（内核只表达意图）───────────────────────────────┐
 │  驱动写一个"性能等级"寄存器 → 硬件自己完成                │
 │  硬件还会自己降频，内核不知道（08 §7）                   │
 └───────────────────────────────────────────────────────────┘
```

快路和慢路**不是同一段代码**。这个事实导致了一个反直觉的后果：
**任何人注册一个频率变更 notifier，都会把整条快路关掉**
（`drivers/cpufreq/cpufreq.c:506-508` 打印占位者名单）—— 08 §5 详解。

### §5.2 governor 的两条路线

| | 旧式（ondemand / conservative） | schedutil |
|---|---|---|
| 住在哪 | `drivers/cpufreq/` | `kernel/sched/` |
| 数据来源 | 定时采样 CPU 空闲时间 | 调度器直接告诉它 |
| 延迟 | 一个采样周期 | 基本无延迟 |
| 节流 | 采样周期 | `rate_limit_us`（`kernel/sched/cpufreq_schedutil.c:104` 的比较） |
| 能编成模块 | 能 | **不能**（`drivers/cpufreq/Kconfig:191` 是 `bool`） |
| arm64 默认 | 否 | **是**（`drivers/cpufreq/Kconfig:41`） |

⇒ **arm64 的默认 governor 是 schedutil，不是 ondemand** ——
`drivers/cpufreq/Kconfig:41` 那行 `default ... if ARM64 || ARM` 是唯一依据。
很多老资料按 x86 的习惯说"默认 ondemand"，在 arm64 上是错的。

schedutil 为什么必须住在 `kernel/sched/`：08 §4。

### §5.3 两套换挡回调，一条约束链

```
  硬件事实：改电压要走 I2C，会睡（§2.4）
        ↓
  慢路允许睡 ⇒ 通知链可以存在 ⇒ 有完整 tracepoint 和统计
        ↓
  但调度器热路径不能睡
        ↓
  ⇒ 必须有第二条不睡的路 = ->fast_switch
        ↓
  ⇒ 快路不能有通知链（通知要等回调答应）
        ↓
  ⇒ 有人要 notifier 时只能整体退回慢路（08 §5）
```

这一条链是本子系统**最值得记住的一条因果链** —— 它从一个硬件事实
（改电压要走总线）一路推到一个纯软件现象（加载某个模块会让调频变慢）。

### §5.4 限值仲裁

```
   thermal 投票 ─┐
   用户 sysfs   ─┼─► freq_qos 仲裁 ─► refresh_frequency_limits()  drivers/cpufreq/cpufreq.c:1153
   其他投票者   ─┘                        │
                                          ▼
                              cpufreq_set_policy()   drivers/cpufreq/cpufreq.c:2561
                              · WRITE_ONCE policy->min/max   :2596-2602
                              · trace_cpu_frequency_limits   :2606
                              · 通知 governor 的 .limits
```

⇒ 写 `scaling_max_freq` 的代码（宏在 `drivers/cpufreq/cpufreq.c:739`，
实例 `:754`/`:755`）**只调 `freq_qos_update_request()`**（`:750`），
从不给 `policy->max` 赋值。08 §6 讲了为什么必须这样。

### §5.5 sysfs 界面（框架型子系统里少见的完整界面）

```
/sys/devices/system/cpu/cpufreq/
├── boost                      全局总闸（08 §8），要满足一条六步链才存在
├── policy0/
│   ├── related_cpus           频率域边界   ← 最该先看的文件
│   ├── affected_cpus          在线成员
│   ├── scaling_driver         谁在执行
│   ├── scaling_governor       谁在决策
│   ├── scaling_available_frequencies
│   ├── scaling_min_freq / scaling_max_freq   ← 写它们是投票
│   ├── scaling_cur_freq       arm64 上是记账值，不是寄存器（07 §4.2）
│   ├── cpuinfo_cur_freq       0400，root 才能读，要驱动有 ->get()
│   ├── cpuinfo_transition_latency
│   ├── stats/                 要 CPU_FREQ_STAT
│   │   ├── total_trans        最快判断"governor 在不在干活"
│   │   ├── time_in_state
│   │   ├── trans_table
│   │   └── reset              延迟生效（07 §4.4）
│   └── <governor 名>/  或  ../<governor 名>/   ← 位置取决于 per-policy 标志
└── policy4/ …
```

**注意 `scaling_cur_freq` 那一行。**它是 arm64 上最容易得出错误结论的文件：
读到的是 `policy->cur`（内核的记账值），不是硬件。要硬件值只能用
`cpuinfo_cur_freq`，而它需要 root 且驱动实现了 `->get()`。

---

## §6 端到端调用路径

逐行剖析在 03 篇，这里只给两条骨架：**起来**和**跑起来之后**。

### §6.1 从上电到 sysfs 出现

```
 ① 平台白名单决定这块板子归谁
    cpufreq-dt-platdev.c  的 compatible 列表
    └─ 匹配上 → 创建 "cpufreq-dt" platform device
       └─ 没匹配上 → 什么都不发生，也不打日志
          （drivers/cpufreq/cpufreq-dt-platdev.c:179 一带）

 ② 驱动 probe → cpufreq_register_driver()
    ├─ 前置校验（不满足直接 -EINVAL）  drivers/cpufreq/cpufreq.c:2872
    │    这一步在下面那条 pr_debug 之前，所以校验失败连"尝试注册"都看不到
    ├─ pr_debug "trying to register driver %s"  drivers/cpufreq/cpufreq.c:2874
    │
 ③ 给每个"present" CPU 走一次 add_dev
    ├─ 已属于某个 policy 的核 → 加进去，不新建
    └─ 新的频率域 → 新建 policy
        ├─ 驱动 ->init()：定频率表、填 cpuinfo、
        │                 用 policy->cpus 声明频率域成员
        │                 （通用 DT 路线在这里问 OPP 要 opp-shared 的结论）
        ├─ 建 sysfs：policyN/ 目录 + 15 个属性文件
        ├─ 建 stats/（要 CPU_FREQ_STAT）
        ├─ 初始频率核对
        │    表里没有当前频率 → BUG_ON 或 pr_info
        │      drivers/cpufreq/cpufreq.c:1490-1500（注释在 :1494-1498）
        ├─ 注册 Energy Model（通用 DT 路线抄 related_cpus，见 08 §3）
        └─ 启动 governor → governor ->init() → ->start()
              schedutil 在这里挂调度器钩子
              kernel/sched/cpufreq_schedutil.c:775 → :808

 ④ 收尾
    ├─ 有 boost 档 → 创建全局 boost 文件  drivers/cpufreq/cpufreq.c:2891-2895
    ├─ 打开频率不变性静态键            drivers/cpufreq/cpufreq.c:2902
    └─ 一个 policy 都没建起来 → pr_debug "No CPU initialized for driver %s"
                                    drivers/cpufreq/cpufreq.c:2913
```

**①、②、③ 三处失败的可见性完全不同**：
① 静默、② 有 `pr_debug`（默认不显示）、③ 部分有日志。
这就是"`/sys/.../cpufreq/` 目录不存在但 dmesg 干净"的成因（07 §7.2 列了四个原因）。

### §6.2 跑起来之后：一次调频

```
  调度器有任务入队/出队/tick
        │
        ▼  per-CPU 函数指针（kernel/sched/cpufreq.c:9）
  sugov_update_single_freq / _perf / _shared
        │   kernel/sched/cpufreq_schedutil.c:352 / :396 / :457
        ├─ 算 util → 映射成频率（map_util_freq）
        ├─ 节流：距上次不足 rate_limit_us 就返回
        │    kernel/sched/cpufreq_schedutil.c:104
        ▼
   ┌────分岔────────────────────────────────────────────┐
   │ fast_switch_enabled ？                              │
   ├── 是 ──► cpufreq_driver_fast_switch()               │
   │            drivers/cpufreq/cpufreq.c:2142           │
   │          → 驱动 ->fast_switch()（原子上下文）        │
   │          → trace_cpu_frequency（:2161）              │
   │          无通知链、无 stats 之外的记账             │
   │                                                      │
   └── 否 ──► 唤醒 kthread → cpufreq_driver_target()      │
                → __resolve_freq()  drivers/cpufreq/cpufreq.c:2303-2304
                → pr_debug "target for CPU %u..." :2306   │
                → 通知链 PRECHANGE   :343                 │
                → 驱动 ->target_index() → OPP → clk/regulator（会睡）
                → 通知链 POSTCHANGE + trace_cpu_frequency :384
```

两条路都会更新 `policy->cur`，而 `scaling_cur_freq` 读的就是它 ——
所以**这个文件反映的是"内核认为的频率"**（07 §4.2、08 §7）。

### §6.3 另一条常被忽略的路径：限值变了

```
  thermal 降上限 / 用户写 scaling_max_freq / 电源策略变化
        │  各自更新自己那一票（freq_qos）
        ▼
  refresh_frequency_limits()      drivers/cpufreq/cpufreq.c:1153
        │  pr_debug "updating policy for CPU %u"  :1148
        ▼
  cpufreq_set_policy()            drivers/cpufreq/cpufreq.c:2561
        ├─ WRITE_ONCE(policy->min/max)   :2596-2602
        ├─ trace_cpu_frequency_limits    :2606
        └─ governor ->limits()  → 立刻重新评估一次频率
```

注意这条路径**不经过 governor 的算法** —— 它只是改区间然后通知 governor。
所以"我降了上限，频率没马上变"是可能的（要等 governor 下一次评估）。

---

## §7 和相邻子系统的关系

### §7.1 关系图

```
                        ┌──────────────┐
                        │   调度器      │ ← EAS 用 EM 选核
                        │ kernel/sched │
                        └───┬──────┬───┘
        挂 update_util 钩子 │      │ 读 Energy Model
                            ▼      ▼
   ┌────────────┐      ┌───────────────────┐      ┌──────────────┐
   │  thermal   │投票─►│                   │─注册►│ Energy Model │
   │ (cpu 降频) │      │     cpufreq       │      │ kernel/power │
   └────────────┘      │                   │      └──────────────┘
   ┌────────────┐      │                   │
   │  PM QoS    │◄─────┤ 仲裁上下限         │
   │ (freq_qos) │      │                   │
   └────────────┘      └───┬───────┬───────┘
                           │       │
              ┌────────────▼──┐  ┌─▼────────────────┐
              │   OPP 框架     │  │ SCMI / 固件协议  │
              │  drivers/opp  │  └──────────────────┘
              └───┬───────┬───┘
                  ▼       ▼
            ┌────────┐ ┌───────────┐
            │  clk   │ │ regulator │
            └────────┘ └───────────┘

   ┌────────────┐        ┌───────────────────┐
   │  cpuidle   │ 同级   │ arch_topology     │ ← 频率不变性
   │            │ 无调用 │ drivers/base/     │   （见 §7.2）
   └────────────┘        └───────────────────┘
   ┌────────────┐
   │  devfreq   │ 同级，接口相似但完全独立（管 GPU/DDR，不管 CPU）
   └────────────┘
```

### §7.2 逐条边界

| 邻居 | 方向 | 接口 | 关键认知 |
|---|---|---|---|
| **OPP 框架** | cpufreq → OPP | `dev_pm_opp_*` | **DTS 的解析全在 OPP 那边**，所以报错消息大多来自 `drivers/opp/`（06 §7.4） |
| **clk / regulator** | OPP → 它们 | 常规 API | cpufreq **不直接调**它们（通用 DT 路线也是经 OPP） |
| **调度器（钩子）** | 调度器 → cpufreq | `include/linux/sched/cpufreq.h`（38 行） | 耦合面极小；schedutil 反而住在调度器目录里（08 §4） |
| **Energy Model** | cpufreq → EM | `em_dev_register_perf_domain()`（`include/linux/energy_model.h:171`） | EM 域边界**不一定**等于 policy 边界（08 §3） |
| **thermal** | thermal → cpufreq | freq_qos 投票 | 它**不改频率**，只降上限（08 §6） |
| **PM QoS** | 双向 | `freq_qos_*` | policy 里的 `min_freq_req`/`max_freq_req` 就是核心自己那两票 |
| **arch_topology** | cpufreq → 它 | `topology_set_freq_scale()` | 见下 |
| **cpuidle** | 无调用 | — | 同级子系统，但策略上会打架（race-to-idle，§2.1） |
| **devfreq** | 无调用 | — | 接口相似，容易搞混；它不管 CPU |
| **SCMI / 固件** | 驱动 → 固件 | SCMI perf 协议 | 走这条路时 DTS 的 OPP 表可能被固件覆盖（08 §3） |

**频率不变性（frequency invariance）这条线值得单独记一句。**
调度器算负载时需要"按当前频率折算"，否则低频下跑满的核和高频下跑一半的核
看起来一样忙。这个折算系数由 cpufreq 提供：

```
  cpufreq 换挡后
     → topology_set_freq_scale()      drivers/base/arch_topology.c:130
        → per-CPU arch_freq_scale 赋值   drivers/base/arch_topology.c:150
  arm64 上的接线：
     arch/arm64/include/asm/topology.h:23
        #define arch_set_freq_scale topology_set_freq_scale
  总开关（静态键）：
     drivers/cpufreq/cpufreq.c:62（定义）· :65（读）· :2902（开）· :2934/:2967（关）
```

⇒ 所以 cpufreq 出问题时，**受影响的不只是功耗，还有调度器看到的负载数值本身**。
这是"调频看起来正常但调度行为很怪"这类问题的一条线索。

---

## §8 导读：接下来该看哪一篇

| 你的问题 | 去哪 |
|---|---|
| 某个函数怎么用？参数是什么？错误码什么意思？ | `01-api.md` |
| 结构体有哪些字段？它们之间怎么连？全局变量和锁？ | `02-datastructures.md` |
| 注册流程逐行是怎么走的？锁怎么加的？拆除路径呢？ | `03-internals.md` |
| governor 那几条路线的区别？Energy Model / thermal / boost 框架？ | `04-extensions.md` |
| 我要写一个 cpufreq 驱动 / governor，骨架长什么样？ | `05-usage.md` |
| DTS 怎么写？哪个属性谁在读？写错了什么症状？ | `06-devicetree.md` |
| 板子上出问题了，开什么日志、看什么文件、错误码怎么分诊？ | `07-debug.md` |
| 这几个概念我理解不了，能不能打个比方？ | `08-feynman.md` |
| 这些结论哪些是读过代码的，哪些是推断的？ | `99-evidence.md` |

### §8.1 如果只记五件事

1. **policy 是频率域，不是"一个 CPU 的策略"** —— 三张 CPU 掩码，
   `related_cpus` 才是边界（08 §1）。
2. **arm64 的默认 governor 是 schedutil**，它住在 `kernel/sched/`，
   由调度器直接驱动，不能编成模块（`drivers/cpufreq/Kconfig:41`、`:191`）。
3. **写 `scaling_max_freq` 是投票，不是赋值**；thermal 也在投票，
   结果由 freq_qos 仲裁（08 §6）。
4. **arm64 的 `scaling_cur_freq` 是记账值**，不是硬件寄存器；
   要硬件值用 `cpuinfo_cur_freq`（root + 驱动有 `->get()`）（07 §4.2）。
5. **核心一条 DT 属性都不读** —— 所以最要命的配置错误（漏 `opp-shared`、
   平台不在白名单）**全都是静默的**（06 §6.1、07 §7.2）。

### §8.2 排查任何 cpufreq 问题的前三条命令

```bash
# 1. 频率域分对了吗？（一簇一行，行数应等于簇数）
grep . /sys/devices/system/cpu/cpufreq/policy*/related_cpus

# 2. 谁在决策、谁在执行？
grep . /sys/devices/system/cpu/cpufreq/policy*/scaling_{driver,governor}

# 3. governor 到底在不在干活？（数字不涨就是没在调频）
grep . /sys/devices/system/cpu/cpufreq/policy*/stats/total_trans
```

如果连 `/sys/devices/system/cpu/cpufreq/` 都不存在，直接跳到 07 §7.2 ——
四个可能原因里有三个不打任何日志。
