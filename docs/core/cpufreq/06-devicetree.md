---
title: 设备树
---

# cpufreq 06：设备树配置

## §0 按任务找章节

| 你要干的事 | 去哪一节 |
|---|---|
| 先搞清楚「谁读 DT」 —— 本篇最重要的一节 | §1 |
| 一张表看完所有会被读的属性 | §1.1 |
| 写一个普通 arm64 板子的调频（`cpufreq-dt` 路线） | §2 |
| 写一个 big.LITTLE 双簇的调频 | §2.4 |
| 写 SCMI 固件调频 | §3 |
| 写高通硬件自治调频 | §4 |
| 按芯片体质选不同频率表（speed binning） | §5 |
| 属性写了但没人读 / 少写一个会怎样 | §6 决策树 |
| DTS 配错了怎么定位 | §7 |
| 内核打了某条错误消息，什么原因 | §7.4 映射表 |

**范围**：arm64-only。ACPI（`CPPC`）路线一律不写，理由记在 99 §2.7。
**引用的 DTS 全部是本树 in-tree 真实节点**，可以直接 `git show` diff。

---

## §1 cpufreq 核心一条 DT 属性都不读

这是理解 cpufreq 设备树配置的**唯一前提**，也是它和 pinctrl / gpio / clk 最大的不同：

```bash
$ grep -c 'of_property\|of_find_property\|of_get_property\|device_node' \
      drivers/cpufreq/cpufreq.c drivers/cpufreq/freq_table.c \
      drivers/cpufreq/cpufreq_stats.c drivers/cpufreq/cpufreq_governor.c
```

四个文件的计数**全部是零**：`cpufreq.c`（2998 行）、`freq_table.c`（370 行）、
`cpufreq_stats.c`（290 行）、`cpufreq_governor.c`（580 行），一处都没有。

cpufreq 核心只认 `struct cpufreq_driver` 和 `policy->freq_table` 这两个内存里的东西，
它不知道设备树存在。

所以「cpufreq 的 DTS 怎么写」这个问题，实际上是在问**另外三方**怎么写：

```
        ┌───────────────────────────────────────────────┐
        │  DTS                                          │
        └───────┬───────────────┬───────────────┬───────┘
                │               │               │
        ┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼──────────┐
        │ 通用 DT /     │ │  OPP 核心     │ │ 具体 cpufreq   │
        │ 驱动模型      │ │ drivers/opp/ │ │ 驱动           │
        │              │ │              │ │                │
        │ /cpus 节点    │ │ opp-hz       │ │ qcom,freq-     │
        │ compatible   │ │ opp-microvolt│ │   domain       │
        │ clocks       │ │ opp-shared   │ │ cpu-supply     │
        │ power-domains│ │ turbo-mode   │ │ nvmem-cells    │
        └──────────────┘ └──────┬───────┘ └─────┬──────────┘
                                │               │
                         生成频率表 ────────► policy->freq_table
                                                │
                                        ┌───────▼────────┐
                                        │  cpufreq 核心   │
                                        │ （不看 DTS）    │
                                        └────────────────┘
```

⚠️ **实践后果**：调频相关的 DTS 报错，**去 `drivers/opp/` 里 grep 错误消息，
不要在 `drivers/cpufreq/` 里找**。§7.4 的映射表里绝大多数消息出自 `drivers/opp/of.c`。

### §1.1 核心真正会读的属性总表

「核心」= 通用 DT + OPP 核心 + 本树 arm64 三个代表驱动。只被单个 vendor 驱动读的
在「消费者」列点名。

| 属性 | 写在哪个节点 | 消费代码 | 必需？ | 说明 |
|---|---|---|---|---|
| `operating-points-v2` | **每个** `cpu@N` 节点 | `drivers/opp/of.c:32`（解析 phandle）；`cpufreq-dt-platdev.c:179`（探测用） | cpufreq-dt 路线必需 | 指向一个 OPP 表节点；同簇的 CPU 必须指向**同一个** phandle |
| `compatible = "operating-points-v2"` | OPP 表节点自己 | OPP 核心 | 必需 | 少了这行整张表不被认 |
| `opp-hz` | 每个 `opp-*` 子节点 | `drivers/opp/of.c:783`（找）/`:798`（读） | 必需 | 类型是 `/bits/ 64`，写成 32 位就解析失败 |
| `opp-microvolt` | 每个 `opp-*` 子节点 | `drivers/opp/of.c:590-599` | 视情况 | ⚠️ OPP 管着 regulator 时**必需**，没管时写了会报错 —— 见 §6.2 |
| `opp-shared` | OPP 表节点 | `drivers/opp/of.c:237` / `:1353` | 强烈建议 | **它决定 cpufreq policy 的边界**，见 §2.4 |
| `turbo-mode` | 单个 `opp-*` 子节点 | `drivers/opp/of.c:945` | 可选 | 决定 `/sys/.../cpufreq/boost` 这个文件**存不存在**，见 §6.3 |
| `opp-suspend` | 单个 `opp-*` 子节点 | `drivers/opp/of.c:974` | 可选 | 配合 `.suspend = cpufreq_generic_suspend`（05 §1.3） |
| `clock-latency-ns` | 每个 `opp-*` 子节点 | `drivers/opp/of.c:955` | 建议 | 变成 `cpuinfo_transition_latency`；不写则为 0，某些 governor 会当成「无限快」 |
| `opp-supported-hw` | 每个 `opp-*` 子节点 | `drivers/opp/of.c:539`/`:545`/`:559` | binning 用 | 配合驱动调 `dev_pm_opp_set_supported_hw()`（05 §4） |
| `opp-level` | 单个 `opp-*` 子节点 | `drivers/opp/of.c:888` | 高通用 | 见 `Documentation/devicetree/bindings/opp/opp-v2-qcom-level.yaml` |
| `voltage-tolerance` | `cpu@N` 节点 | `drivers/opp/of.c:224` | 可选 | 百分比容差 |
| `dynamic-power-coefficient` | `cpu@N` 节点 | `drivers/opp/of.c:1511` / `:1602` | EM 需要 | 不写 Energy Model 就建不起来 → EAS 调度失效（04 §7.2） |
| `clocks` | `cpu@N` 节点 | `cpufreq-dt.c:118`（`clk_get`）；`scmi-cpufreq.c:320`（判定用） | 视路线 | 在 SCMI 路线里它决定驱动**要不要 probe**，见 §3.2 |
| `cpu-supply` | `cpu@N` 节点 | `cpufreq-dt.c:91` | 视路线 | 有电压调节才需要；`imx-cpufreq-dt.c:92` 也读它 |
| `cpu0-supply` | `cpu@0` 节点 | `cpufreq-dt.c:84` | 已废弃 | ⚠️ 只为兼容老 DTS 保留（注释 `cpufreq-dt.c:67`），新板子不要用 |
| `power-domains` + `power-domain-names = "perf"` | `cpu@N` 节点 | `scmi-cpufreq.c:326-327` | SCMI 新式 | 与 `clocks` 二选一 |
| `qcom,freq-domain` | `cpu@N` 节点 | `qcom-cpufreq-hw.c:305-306` / `:527-528` | 高通必需 | 指向 cpufreq 硬件节点 + 域号 |
| `#freq-domain-cells` | 高通 cpufreq 节点 | 同上（作为 cells 名） | 高通必需 | 值为 1 |
| `nvmem-cells` | 驱动自己的节点 | `imx6q-cpufreq.c:233` / `:288` | binning 用 | 见 §5 |
| `capacity-dmips-mhz` | `cpu@N` 节点 | **不在 cpufreq/OPP 里**，在 `arch/arm64` 的 topology 代码里 | 建议 | 异构核算力归一化；cpufreq 只是受益者 |
| `#cooling-cells` | `cpu@N` 节点 | thermal 核心 | 要热控就必需 | 值为 2；配合 `CPUFREQ_IS_COOLING_DEV`（04 §7.1） |

### §1.2 ⚠️ 三条「死字」提醒

写在 DTS 里但**在本树 v6.1.y 的 arm64 路径上没有消费者**的属性，容易让人以为配好了：

| 属性 | 谁读它 | 结论 |
|---|---|---|
| `operating-points`（**没有 `-v2`**，v1 格式） | `drivers/opp/of.c:1088` 仍然认 | ✅ 不是死字，但 **v1 表不支持 `opp-shared`**，所以 big.LITTLE 板子必须用 v2 |
| `clock-latency`（不带 `-ns`） | `imx6q-cpufreq.c:460`、`spear-cpufreq.c:185` 两个 vendor 驱动 | ⚠️ **OPP 核心不读它**（核心读的是 `clock-latency-ns`，`drivers/opp/of.c:955`）。写在 OPP 子节点里等于没写 |
| `dynamic-power-step` | 只有 `pmac32-cpufreq.c`（`:549` `:579` `:635`，PowerPC） | arm64 上是死字 |

`clock-latency` vs `clock-latency-ns` 是本子系统最典型的「名字很像」陷阱：
两个都真实存在、都会被某段代码读，但**读者不同、位置不同**。
OPP 子节点里只有 `clock-latency-ns` 有效。

---

## §2 路线一：通用 DT（`cpufreq-dt`）

这是 arm64 上最常见的形态：CPU 挂在本地时钟树上，电压靠 regulator，
频率表来自 OPP。**不需要写任何 cpufreq 专属节点** —— 驱动的 platform_device
是 `cpufreq-dt-platdev.c` 凭根节点 compatible 现造的（04 §6.3）。

### §2.1 完整例子（本树真实节点）

`arch/arm64/boot/dts/renesas/r8a77951.dtsi`，R-Car H3，两簇 A57 + A53：

```dts
/* OPP 表：cluster0（四个 A57 共用） */
cluster0_opp: opp-table-0 {
	compatible = "operating-points-v2";
	opp-shared;                              /* 四个核一个 policy */

	opp-500000000 {
		opp-hz = /bits/ 64 <500000000>;  /* 必须 /bits/ 64 */
		opp-microvolt = <830000>;
		clock-latency-ns = <300000>;
	};
	opp-1000000000 {
		opp-hz = /bits/ 64 <1000000000>;
		opp-microvolt = <830000>;
		clock-latency-ns = <300000>;
	};
	opp-1500000000 {
		opp-hz = /bits/ 64 <1500000000>;
		opp-microvolt = <830000>;
		clock-latency-ns = <300000>;
		opp-suspend;                     /* 挂起前降到这一档 */
	};
	opp-1600000000 {
		opp-hz = /bits/ 64 <1600000000>;
		opp-microvolt = <900000>;
		clock-latency-ns = <300000>;
		turbo-mode;                      /* boost 档 */
	};
	opp-1700000000 {
		opp-hz = /bits/ 64 <1700000000>;
		opp-microvolt = <960000>;
		clock-latency-ns = <300000>;
		turbo-mode;
	};
};

/* CPU 节点：每个核都要指回 OPP 表 */
a57_0: cpu@0 {
	compatible = "arm,cortex-a57";
	reg = <0x0>;
	device_type = "cpu";
	clocks = <&cpg CPG_CORE R8A7795_CLK_Z>;   /* 调频靠这个 clk */
	operating-points-v2 = <&cluster0_opp>;    /* 指向表 */
	dynamic-power-coefficient = <854>;        /* Energy Model 要它 */
	capacity-dmips-mhz = <1024>;              /* 异构算力归一化 */
	#cooling-cells = <2>;                     /* 让 thermal 能压它 */
	/* 省略 power-domains / cpu-idle-states / next-level-cache */
};
```

行号：OPP 表 `:54-86`（`opp-shared` 在 `:56`，`opp-suspend` 在 `:72`，
两个 `turbo-mode` 在 `:78` 和 `:84`）；`cpu@0` 节点 `:150-158`
（`clocks` 在 `:154`，`operating-points-v2` 在 `:155`，
`dynamic-power-coefficient` 在 `:153`，`capacity-dmips-mhz` 在 `:156`，
`#cooling-cells` 在 `:157`）；cluster1 的表从 `:88` 开始。

### §2.2 这份 DTS 会产生什么

| DTS 里的东西 | 内核里变成什么 | 用户能看到什么 |
|---|---|---|
| `opp-shared` + 四个核指同一 phandle | 一个 `cpufreq_policy` 覆盖 CPU0-3 | `affected_cpus` 有四个数字 |
| 五个 `opp-hz` | `policy->freq_table` 五项 | `scaling_available_frequencies` 三项（见下） |
| 两个 `turbo-mode` | 频率表里带 `CPUFREQ_BOOST_FREQ` 的两项 | `scaling_boost_frequencies` 两项 + **全局** `boost` 文件出现 |
| `opp-microvolt` | OPP 供电约束 | 无直接对应文件 |
| `clock-latency-ns = <300000>` | `cpuinfo_transition_latency` | `cat cpuinfo_transition_latency` → `300000` |
| `dynamic-power-coefficient` | Energy Model | `/sys/kernel/debug/energy_model/`（07 篇） |

**注意第三行**：五个 OPP，`scaling_available_frequencies` 只列**三个**。
带 `turbo-mode` 的两档被拆到 `scaling_boost_frequencies` 去了，判据是
`freq_table.c:254` 的那个异或（04 §5.6）。**这不是配错了。**

### §2.3 最小可用版本

砍到不能再砍：

```dts
cpu_opp_table: opp-table {
	compatible = "operating-points-v2";
	opp-shared;

	opp-1000000000 {
		opp-hz = /bits/ 64 <1000000000>;
		opp-microvolt = <900000>;
	};
	opp-1400000000 {
		opp-hz = /bits/ 64 <1400000000>;
		opp-microvolt = <1000000>;
	};
};

cpu@0 {
	/* ... */
	clocks = <&cpu_clk>;
	cpu-supply = <&vdd_cpu>;
	operating-points-v2 = <&cpu_opp_table>;
};
/* cpu@1..N 同样写 operating-points-v2 = <&cpu_opp_table>; */
```

**一个 OPP 也能跑**（表里只有一档），但那样调频没有意义；
⚠️ **零个 OPP 会直接失败**：`cpufreq-dt.c:270` 打
`OPP table can't be empty` 并返回错误。

### §2.4 big.LITTLE：policy 边界完全由 DTS 决定

cpufreq 自己**不知道**哪些 CPU 是一簇。它问的是 OPP：
`cpufreq-dt.c:232` 调 `dev_pm_opp_of_get_sharing_cpus()`，
那个函数看的就是 `opp-shared`（`drivers/opp/of.c:1353`）。

```
DTS 怎么写                                   结果
─────────────────────────────────────────────────────────────────
两张表 + 各自 opp-shared              ✅ 两个 policy：policy0 / policy4
（R-Car H3 的做法）                      affected_cpus = "0 1 2 3" / "4 5 6 7"

一张表 + opp-shared，八个核都指它      ⚠️ 一个 policy 管八个核 ——
                                         大小核被强行同频，性能功耗都不对

两张表，都不写 opp-shared             ⚠️ 八个 policy，每核一个 ——
                                         同簇的核各自调频，硬件上做不到
                                         （它们共用一个 PLL），会互相打架

一张表，不写 opp-shared               ⚠️ 八个 policy 且共享一张表 ——
                                         最难查的一种：sysfs 看着"很正常"
```

只有第一行是对的。中间两种**都不会报错**，这是本篇最重要的静默失效
—— 检查方法见 §7.2。

---

## §3 路线二：SCMI 固件调频

CPU 频率由固件（SCP / ATF）管。DTS 里**不出现任何 cpufreq 字样**，
只出现一个 SCMI perf 协议节点，再让 CPU 指向它。

### §3.1 完整例子（本树真实节点）

`arch/arm64/boot/dts/arm/juno-scmi.dtsi`：

```dts
/* SCMI 协议 0x13 = Performance Domain Management */
scmi_dvfs: protocol@13 {
	reg = <0x13>;
	#clock-cells = <1>;
	mbox-names = "tx", "rx";
	mboxes = <&mailbox 1 0 &mailbox 1 1>;
	shmem = <&cpu_scp_hpri0 &cpu_scp_hpri1>;
};

/* CPU 节点只加一行：把 clocks 指到 perf 协议上 */
&A53_0 {
	clocks = <&scmi_dvfs 1>;   /* 1 = perf domain id */
};
&A53_1 {
	clocks = <&scmi_dvfs 1>;   /* 同一个 domain ⇒ 同一个 policy */
};
```

行号：`protocol@13` 节点 `:58-64`（`reg = <0x13>` 在 `:59`，
`#clock-cells = <1>` 在 `:60`）；四个 A53 的 `clocks` 覆盖在 `:106-117`
（`&A53_0` 的那行在 `:107`）；大核用 `<&scmi_dvfs 2>`（`:183`）。

### §3.2 `clocks` 在这条路线上决定驱动要不要 probe

`scmi-cpufreq` 不是 platform_driver，它是 SCMI 总线上的设备。
它 probe 的第一件事是**反查有没有 CPU 在用自己**：

```c
	/* scmi_dev_used_by_cpus()：drivers/cpufreq/scmi-cpufreq.c:303-344 */
	for_each_possible_cpu(cpu) {
		np = of_parse_phandle(cpu_np, "clocks", 0);        /* :320 */
		if (np == scmi_np)
			return true;                              /* :324 */

		idx = of_property_match_string(cpu_np,
			"power-domain-names", "perf");            /* :326 */
		np = of_parse_phandle(cpu_np, "power-domains", idx);  /* :327 */
		if (np == scmi_np)
			return true;                              /* :331 */
	}
	return false;                                             /* :343 */
```

`scmi_cpufreq_probe()` 在 `:354` 判 `!scmi_dev_used_by_cpus(dev)`，
成立就直接 `return -ENODEV`（`:355`）。

⚠️ **静默失效点**：CPU 节点的 `clocks` 指错了（比如还指着 SoC 自己的
`&cpg`，没改成 `&scmi_dvfs`），驱动**连 probe 都不进**，
`dmesg` 里一条相关消息都没有，`/sys/devices/system/cpu/cpu0/cpufreq/`
整个目录不存在。这是 07 篇「cpufreq 目录压根没出现」的第一号原因。

两种写法**二选一即可**（`||` 关系，不是 `&&`）：

| 写法 | 在 CPU 节点里写什么 | 判定行 |
|---|---|---|
| 旧式（时钟） | `clocks = <&scmi_dvfs N>;` | `:320-324` |
| 新式（性能域） | `power-domains = <&scmi_perf N>;` + `power-domain-names = "perf";` | `:326-331` |

还有一条**兼容性后门**：根节点 compatible 是 `brcm,brcmstb` 时，
上面两条都不匹配也返回 true（`:340-341`，注释 `:334-339` 说明是为了兼容老 DTS）。
arm64 板子不要依赖它。

### §3.3 SCMI 路线的 OPP 表在哪

**不在 DTS 里。** 频率表由驱动向固件查询后 kcalloc 出来（05 §2.3）。
但 `dev_pm_opp_of_get_sharing_cpus()` 仍然在 `scmi-cpufreq.c:169` 被调用
—— 它读的是 `operating-points-v2` / `opp-shared`。

所以 SCMI 路线上会出现**两套边界**：
频率来自固件的 perf domain（`clocks` 的那个 cell），
OPP 共享信息来自 DTS 的 `opp-shared`。两者可以不一致 —— 这正是 04 §7.2
「三套边界」问题的来源，也是 08 篇的一个选题。

### §3.4 需不需要 `#clock-cells`

⚠️ 一个容易漏的细节：如果用 `clocks` 写法，OPP 核心会试图去
`clk_get()`。为此 `scmi_cpufreq_probe()` 在 `:363` 检查自己节点上有没有
`#clock-cells`，有就注册一个 dummy clock provider（`:364`）；
失败则 `dev_err_probe(...,"registering clock provider failed")`（`:366`）。
所以 `protocol@13` 里的 `#clock-cells = <1>`（本例 `:60`）**不是装饰**。

---

## §4 路线三：高通硬件自治（`qcom,cpufreq-hw`）

频率由 CPU 旁边的硬件状态机管，DTS 里要**显式写一个 cpufreq 节点**
—— 这是三条路线里唯一需要的。

### §4.1 完整例子（本树真实节点）

`arch/arm64/boot/dts/qcom/sdm845.dtsi`：

```dts
cpufreq_hw: cpufreq@17d43000 {
	compatible = "qcom,cpufreq-hw";
	reg = <0 0x17d43000 0 0x1400>,      /* 一段 reg = 一个频率域 */
	      <0 0x17d45800 0 0x1400>;
	reg-names = "freq-domain0", "freq-domain1";

	interrupts-extended = <&lmh_cluster0 0>, <&lmh_cluster1 0>;  /* 限频中断 */

	clocks = <&rpmhcc RPMH_CXO_CLK>, <&gcc GPLL0>;
	clock-names = "xo", "alternate";

	#freq-domain-cells = <1>;           /* CPU 节点靠它引用 */
};

/* 每个 CPU 节点指回来 */
cpu@0 {
	/* ... */
	qcom,freq-domain = <&cpufreq_hw 0>;   /* 小核 → 域 0 */
};
```

行号：cpufreq 节点 `arch/arm64/boot/dts/qcom/sdm845.dtsi:5213-5224`
（`compatible` 在 `:5214`，两段 `reg` 在 `:5215`，`reg-names` 在 `:5216`，
`interrupts-extended` 在 `:5218`，`clocks` / `clock-names` 在 `:5220-5221`，
`#freq-domain-cells` 在 `:5223`）；CPU 侧的 `qcom,freq-domain` 见 `:202`（cpu0）、
`:226`、`:247`。

### §4.2 关键点

| 关键点 | 依据 | 说明 |
|---|---|---|
| policy 边界 = `qcom,freq-domain` 的**第二个 cell** | `qcom-cpufreq-hw.c:527-528` 解析，`:305-306` 是另一处 | 和 `opp-shared` **无关**；写错了两簇会并成一个 policy |
| `reg` 有几段就有几个频率域 | `reg-names = "freq-domainN"` | 段数要 ≥ 用到的最大域号 + 1 |
| `interrupts-extended` 指向 LMH | 04 §6.4 的限频回报路径 | ⚠️ 不写就没有 thermal pressure 上报，硬件降频对内核不可见 |
| **频率表不在 DTS 里** | 驱动读硬件 LUT（04 §5.6 的 `:274`） | 所以 sdm845.dtsi 里没有 CPU 的 OPP 表 |
| `boost` 由硬件 LUT 决定 | 04 §5.6 | 和 `turbo-mode` 无关，DTS 管不着 |

⚠️ **两种边界机制不能混用**：高通路线下写 `opp-shared` 没有效果，
写 `qcom,freq-domain` 才有。同理，通用 DT 路线下写 `qcom,freq-domain`
不会被任何代码读。

### §4.3 `qcom,cpufreq-hw` 还有一个 EPSS 变体

新芯片用 `qcom,cpufreq-epss`（`sc7280.dtsi:5355`、`sm8250` 等），
`sc8280xp.dtsi:1796` 用的是两级 compatible
（`"qcom,sc8280xp-cpufreq-epss", "qcom,cpufreq-epss"`）。
DTS 写法完全一致，只是寄存器布局不同。绑定文档：
`Documentation/devicetree/bindings/cpufreq/cpufreq-qcom-hw.yaml`。

---

## §5 按芯片体质选频率表（speed binning）

同一份 DTS，不同批次的芯片跑不同频率上限。两种写法。

### §5.1 写法 A：`opp-supported-hw`（推荐）

DTS 里把所有档位都写出来，每档标一个位掩码，运行时由驱动决定哪些档有效：

```dts
cpu_opp_table: opp-table {
	compatible = "operating-points-v2";
	opp-shared;

	opp-1000000000 {
		opp-hz = /bits/ 64 <1000000000>;
		opp-microvolt = <900000>;
		opp-supported-hw = <0x7>;   /* bit0|bit1|bit2：三档体质都支持 */
	};
	opp-1800000000 {
		opp-hz = /bits/ 64 <1800000000>;
		opp-microvolt = <1000000>;
		opp-supported-hw = <0x4>;   /* 只有 bit2（最高体质）支持 */
	};
};
```

消费链：驱动从 efuse 读出体质位 → 调 `dev_pm_opp_set_supported_hw()`
（`imx-cpufreq-dt.c:156`）→ OPP 核心在
`drivers/opp/of.c:539`/`:545`/`:559` 逐档比对 → 不匹配的档不进表。

### §5.2 写法 B：`opp-microvolt-<name>` 多组电压

DTS 里给同一档写多组电压，驱动用 `dev_pm_opp_set_prop_name()` 选一组：

```dts
	opp-1200000000 {
		opp-hz = /bits/ 64 <1200000000>;
		opp-microvolt-speed0 = <1000000>;
		opp-microvolt-speed1 = <950000>;   /* 体质好的用低电压 */
	};
```

消费链：`sun50i-cpufreq-nvmem.c:113` 调 `dev_pm_opp_set_prop_name()`
设定后缀名 → OPP 核心在 `drivers/opp/of.c:590-592` 拼出
`opp-microvolt-<name>` 去找，找不到才退回裸 `opp-microvolt`（`:598-599`）。
绑定文档：`Documentation/devicetree/bindings/opp/allwinner,sun50i-h6-operating-points.yaml`。

### §5.3 两个陷阱

| 陷阱 | 后果 | 依据 |
|---|---|---|
| efuse 值读出来是 0（DTS 的 `nvmem-cells` 指错） | 所有档都不匹配 → 空表 → `OPP table can't be empty` | `cpufreq-dt.c:270` |
| 预处理器驱动所在平台没进 blocklist | `cpufreq-dt` 抢先注册，过滤条件来不及设 | `cpufreq-dt-platdev.c:103`（04 §6.3、05 §4.2） |

⚠️ 第二条是最难查的：`scaling_available_frequencies` 里会出现**这颗芯片
跑不到的频率**，而 dmesg 一句话都不说。

---

## §6 静默失效决策树

这一节只放**不报错但行为不对**的情况。会报错的去 §7.4。

### §6.1 policy 边界（`opp-shared`）

```
CPU 节点写了 operating-points-v2 吗？
├─ 没写 → cpufreq-dt-platdev.c:179 探测不到 ⇒ 通用驱动不给这块板子造设备
│         ⇒ /sys/.../cpufreq/ 不存在（唯一线索：什么线索都没有）
└─ 写了
   ├─ 同簇的核指向同一个 phandle 吗？
   │  ├─ 不是 → 每核一张表 ⇒ 每核一个 policy（哪怕表内容一样）
   │  └─ 是
   │     └─ 表里写了 opp-shared 吗？
   │        ├─ 没写 → drivers/opp/of.c:1353 返回"不共享"
   │        │           ⇒ 每核一个 policy
   │        │           ⇒ 症状：affected_cpus 只有一个数字，
   │        │             但硬件上这几个核共用一个 PLL ⇒ 互相覆盖对方的设置
   │        └─ 写了 → ✅ 一簇一个 policy
   └─ 大小核指向了同一张表？
      → ⚠️ 八核一个 policy：小核被大核的频率带着跑，
        capacity-dmips-mhz 再准也救不回来
```

**验证方法（一条命令）**：

```bash
for d in /sys/devices/system/cpu/cpufreq/policy*; do
	echo "$d: $(cat $d/affected_cpus)"
done
```

期望输出是「簇数 == policy 数」。四核一簇的板子看到四个 policy 就是 `opp-shared` 漏了。

### §6.2 `opp-microvolt` 写与不写

这条属性有**两个方向的错**，方向由「OPP 有没有拿到 regulator」决定：

```
CPU 节点写了 cpu-supply 吗？（cpufreq-dt.c:91 读它）
├─ 写了 ⇒ OPP 管着 regulator
│  └─ 每个 opp-* 都写 opp-microvolt 了吗？
│     ├─ 是 → ✅
│     └─ 否 → ❌ 报错："opp-microvolt missing although OPP managing regulators"
│              （drivers/opp/of.c:613）
└─ 没写 ⇒ OPP 不管 regulator
   └─ 写了 opp-microvolt 吗？
      ├─ 没写 → ✅（纯变频，不调压）
      └─ 写了 → ❌ 报错："opp-microvolt wasn't expected"（drivers/opp/of.c:623）
```

两条消息都是 `dev_err`，**都会出现在 dmesg 里**，所以严格说不算静默 ——
放在这里是因为很多人以为「多写一个属性没坏处」。

### §6.3 `turbo-mode` → boost 文件存不存在

这是本子系统跨了三个框架的一条链，也是 04 §5.6 的结论：

```
OPP 表里有 opp-* 带 turbo-mode 吗？
├─ 没有
│  └─ freq_table.c:25 起的 policy_has_boost_freq() 返回 false
│     └─ cpufreq-dt.c:138 的条件不成立 ⇒ 不调 cpufreq_enable_boost_support()
│        └─ ❌ /sys/devices/system/cpu/cpufreq/boost 这个文件根本不存在
│           （不是"值为 0"，是"文件没有"）
└─ 有
   └─ drivers/opp/of.c:945 把 opp->turbo 置位
      └─ drivers/opp/cpu.c:69-71 建表时打上 CPUFREQ_BOOST_FREQ
         └─ cpufreq-dt.c:140 调 cpufreq_enable_boost_support()
            └─ drivers/cpufreq/cpufreq.c:2808 起把软件 set_boost 装上
               └─ drivers/cpufreq/cpufreq.c:2891-2895 创建全局 boost 文件
                  └─ ✅ 文件出现，默认值见 04 §5
```

⚠️ **高通路线是例外**：`qcom-cpufreq-hw.c:596-597` 的条件来自硬件 LUT
读出的档位（`:274`，注释 `:267-270`），**DTS 里写 `turbo-mode` 无效**。

⚠️ **SCMI 路线上根本没有 boost**：`scmi-cpufreq.c` 全文零个 boost 相关调用。

### §6.4 `dynamic-power-coefficient` 漏写

```
cpu@N 里写了 dynamic-power-coefficient 吗？
├─ 写了 → drivers/opp/of.c:1511 读到 ⇒ Energy Model 建起来 ⇒ EAS 生效
└─ 没写 → EM 建不起来
          ⇒ 调频还能用（sysfs 一切正常）
          ⇒ 但调度器退回非 EAS 路径：任务不再优先往小核放
          ⇒ 症状：功耗比预期高，而 cpufreq 那边看不出任何异常
```

这是「cpufreq 目录完全正常但省电效果不对」的头号原因。

### §6.5 `clock-latency-ns` 漏写

不写 → `cpuinfo_transition_latency` 为 0。后果分两种：

| governor | 后果 |
|---|---|
| schedutil（arm64 默认） | 影响不大，它用自己的 rate_limit_us |
| ondemand / conservative | ⚠️ 采样周期的计算基准变成 0，dbs 层会退到最小采样率（04 §2.5） |

---

## §7 配错了怎么定位

按「什么时候能发现」分三层。**从上往下查，别跳层。**

### §7.1 第一层：编译期（dtc 就能发现）

```bash
# 单独编译 DTB，把 dtc 的警告全打开
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     W=1 DTC_FLAGS=-Wno-unique_unit_address <board>.dtb

# 用 schema 校验（需要 pip install dtschema）
make ARCH=arm64 dt_binding_check DT_SCHEMA_FILES=cpufreq/
make ARCH=arm64 dtbs_check DT_SCHEMA_FILES=opp/
```

dtc + schema 能抓到的：

| 错误 | 会被抓到吗 |
|---|---|
| `opp-hz` 写成 32 位（漏了 `/bits/ 64`） | ✅ schema 报类型不符 |
| `#freq-domain-cells` 和 `qcom,freq-domain` 的 cell 数不一致 | ✅ dtc 报 phandle cells 不匹配 |
| phandle 指向不存在的 label | ✅ dtc 直接编译失败 |
| 漏写 `opp-shared` | ❌ **抓不到**（它是可选属性） |
| 大小核指向同一张 OPP 表 | ❌ **抓不到**（语法完全合法） |
| `clock-latency` 写成不带 `-ns` | ❌ **抓不到**（未知属性只是警告，甚至无警告） |

三个「抓不到」正是 §6 那几棵决策树存在的原因。

### §7.2 第二层：运行期静态（启动后一次性看完）

```bash
# 1) cpufreq 到底起来了没
ls -d /sys/devices/system/cpu/cpufreq/policy*   || echo "没有任何 policy"
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver

# 2) policy 边界对不对（§6.1 的验证）
for d in /sys/devices/system/cpu/cpufreq/policy*; do
	echo "$d -> affected=$(cat $d/affected_cpus) related=$(cat $d/related_cpus)"
done

# 3) 频率表对不对
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_boost_frequencies 2>/dev/null \
	|| echo "无 boost 档（turbo-mode 没写 / 不支持）"
cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_transition_latency

# 4) OPP 侧看得更细（需要 CONFIG_DEBUG_FS + CONFIG_PM_OPP）
ls /sys/kernel/debug/opp/
cat /sys/kernel/debug/opp/cpu0/opp:*/rate_hz    2>/dev/null

# 5) Energy Model 建起来了没（§6.4）
ls /sys/kernel/debug/energy_model/ 2>/dev/null || echo "EM 没建（缺 dpc？）"

# 6) 设备树里最终生效的值（比对 DTS 源文件）
cat /proc/device-tree/cpus/cpu@0/operating-points-v2 | xxd | head -2
```

第 6 条最有用：它读的是**内核实际拿到的** DTB，能排除「改了 dts 但装的还是旧 dtb」。

### §7.3 第三层：运行期动态（要触发才看得到）

```bash
# 打开 cpufreq 核心的 pr_debug（大量输出，只在排查时开）
echo 'file drivers/cpufreq/cpufreq.c +p' > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/opp/of.c +p'         > /sys/kernel/debug/dynamic_debug/control
echo 'file drivers/opp/core.c +p'       > /sys/kernel/debug/dynamic_debug/control

# 触发一次调频，看真的走到哪一步
echo 1200000 > /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
dmesg | tail -40
```

关键的 `pr_debug` 落点（07 篇会展开）：

| 消息 | 位置 | 说明 |
|---|---|---|
| `updating policy for CPU %u` | `drivers/cpufreq/cpufreq.c:1148` | 限值刷新真的发生了 |
| `notification %u of frequency transition to %u kHz` | `:356` | 慢路径调频；有 `fast_switch` 时**不会出现** |
| `Freq table is unsorted` | `freq_table.c:322-324` / `:332-334` | OPP 表没按序写（04 §3.3） |
| `Warning: CPU frequency is %u, cpufreq assumed %u kHz` | `:367` | 硬件实际频率和内核认知不一致 |
| `Warning: CPU frequency out of sync: ...` | `:1724` | 同上，更晚的检查点 |
| `OPP table not found (%d)` | `drivers/opp/core.c:451`（dbg）/ `:558`（err） | OPP 表压根没建起来 |

### §7.4 错误消息 → 原因映射表

按「消息出自哪一层」排序。⚠️ 注意绝大多数出自 `drivers/opp/`，不是 `drivers/cpufreq/`。

| 内核消息 | 出处 | DTS 上的原因 |
|---|---|---|
| `OPP table can't be empty` | `cpufreq-dt.c:270` | OPP 表节点里一个 `opp-*` 子节点都没有；或 binning 把所有档都过滤掉了（§5.3） |
| `Empty OPP table` | `drivers/opp/of.c:167` | 同上，OPP 侧先发现 |
| `OPP table empty` | `drivers/opp/of.c:448` | 同上，另一条路径 |
| `failed to init cpufreq table: %d` | `cpufreq-dt.c:285` | `dev_pm_opp_init_cpufreq_table()` 失败，往上看 OPP 的消息 |
| `%s: failed to mark OPPs as shared: %d` | `cpufreq-dt.c:279` | `opp-shared` 语义和实际 cpumask 冲突 |
| `%s: failed to get clk: %d` | `cpufreq-dt.c:121` | `cpu@N` 的 `clocks` 指错或对应 clk 驱动没起来 |
| `no regulator for cpu%d`（`dev_dbg`） | `cpufreq-dt.c:97` | 没写 `cpu-supply`。**这是 debug 级，正常情况也会出现**，不是错误 |
| `opp-microvolt missing although OPP managing regulators` | `drivers/opp/of.c:613` | 写了 `cpu-supply` 但 OPP 子节点漏了 `opp-microvolt`（§6.2） |
| `opp-microvolt wasn't expected` | `drivers/opp/of.c:623` | 反向：没有 regulator 却写了 `opp-microvolt`（§6.2） |
| `Invalid opp-supported-hw property (%d)` | `drivers/opp/of.c:547` | `opp-supported-hw` 的元素个数和驱动传的版本数不一致（§5.1） |
| `failed to read opp-supported-hw property at index %d` | `drivers/opp/of.c:562` | 同上，某一项读不出来 |
| `Count mismatch between opp-hz and clk_count (%d %d)` | `drivers/opp/of.c:789` | `opp-hz` 的项数和设备的时钟数不匹配 |
| `Error parsing opp-hz: %d` | `drivers/opp/of.c:800` | `opp-hz` 少了 `/bits/ 64`，或值写坏了 |
| `opp key field not found` | `drivers/opp/of.c:934` | OPP 子节点既没有 `opp-hz` 也没有 `opp-level` |
| `%s: failed to set voltage (%lu %lu %lu mV): %d` | `drivers/opp/core.c:916` | `opp-microvolt` 超出 regulator 的可调范围 —— **去 regulator 那边查约束** |
| `%s: failed to set clock rate: %d` | `drivers/opp/core.c:943` | `opp-hz` 那个频率时钟树给不出来 —— **去 clk 那边查** |
| `multiple regulators are not supported` | `drivers/opp/core.c:995` | 一个 CPU 写了多个 supply |
| `%s: failed to get cpu%d device` | `drivers/opp/cpu.c:173`（另一处 `:122`） | `opp-shared` 的 cpumask 里含不存在的 CPU |
| `failed to add opp-dev for cpu%d device` | `drivers/opp/cpu.c:180` | 同上，注册阶段 |
| `%s: No %s associated to cpu: %d` | `drivers/cpufreq/cpufreq.c:196` | 用了 `cpufreq_generic_get` 但 `policy->clk` 是空（05 §1.2） |
| `%s: Failed to initialize policy for cpu: %d (%d)` | `drivers/cpufreq/cpufreq.c:1532` | 驱动 `->init()` 返回了错，真正原因在上面几行 |
| `%s: ->get() failed` | `drivers/cpufreq/cpufreq.c:1461` | 驱动读不到当前频率；`clocks` 或固件通道有问题 |
| `%s: unable to set suspend-freq: %u. err: %d` | `drivers/cpufreq/cpufreq.c:1899` | `opp-suspend` 那一档设不上去 |
| `%s: suspend_freq not defined`（`pr_debug`） | `drivers/cpufreq/cpufreq.c:1889` | 用了 `cpufreq_generic_suspend` 但没写 `opp-suspend` |
| **什么消息都没有，`/sys/.../cpufreq/` 不存在** | — | 三种可能：① 根节点 compatible 不在 `cpufreq-dt-platdev.c` 的 allowlist（`:18`）里且 `cpu@0` 没有 `operating-points-v2`（`:179`）；② SCMI 路线 `clocks` 没指向 perf 协议（§3.2 的 `-ENODEV`）；③ `cpufreq.off=1` |

### §7.5 症状清单（按坏掉的范围分组）

**范围一：整个子系统没起来**

| 症状 | 最可能的 DTS 原因 | 去哪 |
|---|---|---|
| `/sys/devices/system/cpu/cpufreq/` 目录不存在 | `operating-points-v2` 漏写 / compatible 不在名单 | §7.4 最后一行 |
| `policy0` 存在但 `scaling_driver` 是意料之外的名字 | vendor 驱动没进 blocklist，被 `cpufreq-dt` 抢了 | §5.3、04 §6.3 |

**范围二：policy 拓扑不对**

| 症状 | 原因 | 去哪 |
|---|---|---|
| policy 数 == CPU 数（而不是簇数） | `opp-shared` 漏写 | §6.1 |
| 八核只有一个 policy | 大小核指了同一张表 | §6.1 |
| `affected_cpus` 和 `related_cpus` 不一致 | ✅ **正常**：前者只含在线 CPU | 02 篇 |

**范围三：频率档位不对**

| 症状 | 原因 | 去哪 |
|---|---|---|
| `scaling_available_frequencies` 比 DTS 里的档数少 | ① 带 `turbo-mode` 的被拆到 boost 文件；② binning 过滤 | §2.2、§5 |
| 频率档数在两块同型号板子上不同 | efuse 体质不同 | §5 |
| 明明写了高频档，`cpuinfo_max_freq` 上不去 | 那一档 `opp-supported-hw` 不匹配 | §5.1 |
| dmesg 有 `Freq table is unsorted` | OPP 子节点没按频率升序写 | 04 §3.3（不是错误，只是慢） |

**范围四：单个属性没效果**

| 症状 | 原因 | 去哪 |
|---|---|---|
| `boost` 文件不存在 | `turbo-mode` 没写；或路线不支持 | §6.3 |
| `cpuinfo_transition_latency` 是 0 | 写成了 `clock-latency`（漏 `-ns`） | §1.2、§6.5 |
| `scaling_cur_freq` 恒为 0 | `clocks` 没配好 → `policy->clk` 空 | §7.4 的 `No %s associated to cpu` |
| 一切正常但功耗偏高 | `dynamic-power-coefficient` 漏写 → EM 没建 | §6.4 |
| 高通板子上写了 `opp-shared` 没反应 | 高通用 `qcom,freq-domain` 定边界 | §4.2 |

---

## §8 绑定文档与导航

### §8.1 本树相关绑定文档

| 文档 | 讲什么 | arm64 相关 |
|---|---|---|
| `Documentation/devicetree/bindings/opp/opp-v2.yaml` | 最该读的一篇：所有 `opp-*` 属性 | ✅ |
| `Documentation/devicetree/bindings/opp/opp-v2-base.yaml` | v2 的公共 schema | ✅ |
| `Documentation/devicetree/bindings/opp/opp-v1.yaml` | 老式 `operating-points` | ⚠️ 不要用 |
| `Documentation/devicetree/bindings/cpufreq/cpufreq-dt.txt` | 通用 DT 路线要哪些属性 | ✅ |
| `Documentation/devicetree/bindings/cpufreq/cpufreq-qcom-hw.yaml` | 高通硬件自治 | ✅ §4 |
| `Documentation/devicetree/bindings/opp/opp-v2-kryo-cpu.yaml` | 高通 binning | ✅ §5 |
| `Documentation/devicetree/bindings/opp/opp-v2-qcom-level.yaml` | `opp-level` 用法 | ✅ |
| `Documentation/devicetree/bindings/cpufreq/qcom-cpufreq-nvmem.yaml` | 高通 efuse 选表 | ✅ §5 |
| `Documentation/devicetree/bindings/opp/allwinner,sun50i-h6-operating-points.yaml` | 全志 `opp-microvolt-<name>` | ✅ §5.2 |
| `Documentation/devicetree/bindings/cpufreq/imx-cpufreq-dt.txt` | i.MX binning | ✅ §5 |
| `Documentation/devicetree/bindings/cpufreq/cpufreq-mediatek-hw.yaml` | MTK 硬件自治 | ✅ |
| 其余 7 篇（`brcm,stb-*` / `cpufreq-spear` / `cpufreq-st` / `nvidia,tegra*` / `ti-cpufreq` / `cpufreq-mediatek.txt`） | 各家 vendor | 部分（99 §1.4 登记） |

⚠️ **注意 `cpufreq/` 这个目录里没有「通用属性」文档**。
真正的通用属性全在 `opp/` 目录里 —— 这正是 §1 那张图的另一种体现。

### §8.2 读完这篇去哪

| 下一步 | 去哪 |
|---|---|
| 代码骨架怎么写（和本篇的 DTS 配套） | 05 篇 |
| DTS 没问题但运行时行为不对 | 07 篇 |
| 为什么 policy 和 CPU 是两层、为什么边界由 OPP 定 | 08 篇 |
| 快速复习整个子系统 | 00 篇 |
