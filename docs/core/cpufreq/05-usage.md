---
title: 使用指南
---

# cpufreq 05：各角色代码骨架

> ⚠️ **本篇所有代码骨架都没有编译过、没有上板跑过。** 它们是从本树 v6.1.y 的真实驱动
> 里抽出来的最小形状，用途是「告诉你该填哪几个字段、按什么顺序调哪几个函数」，
> **不是可以直接 `make` 的代码**。抄进项目里必须自己补错误处理、补 `#include`、
> 补 `MODULE_*`，并对着本篇给的行号回看真实驱动。
> 这条免责声明不只写在 99 篇，是因为本篇是唯一会被直接抄进代码的一篇。
>
> ✅ **符号存在性已逐个核对**：正文出现的每个函数名 / 宏名 / 标志名 / 内核打印的字符串，
> 都在本树全树 grep 过。核对清单和命令记在 99 §3。

---

## §0 本篇覆盖哪些角色

cpufreq 是**框架型**子系统（没有自己的 `bus_type`，但有完整 sysfs）。
「使用 cpufreq」在本树里有七种完全不同的含义，**每种对应一节**：

| 角色 | 你要写的东西 | 本树代表 | 本篇章节 | arm64 相关度 |
|---|---|---|---|---|
| ① 频率表型驱动 | `cpufreq_driver` + `->target_index` | `cpufreq-dt.c` | §1 | |
| ② 固件型驱动 | 同上 + `->fast_switch` | `scmi-cpufreq.c` | §2 | |
| ③ 硬件自治型驱动 | 同上 + `->ready` + 限频回报 | `qcom-cpufreq-hw.c` | §3 | |
| ④ OPP/nvmem 预处理器 | **不注册** `cpufreq_driver` | `sun50i-cpufreq-nvmem.c` | §4 | |
| ⑤ governor 作者 | `cpufreq_governor` | `cpufreq_userspace.c` | §5 | |
| ⑥ 内核内消费者 | 读频率 / 加限值 / 收通知 | `cpufreq_cooling.c` | §6 | |
| ⑦ 用户态调频与观测 | shell / sysfs | — | §7 | |

### §0.1 三个「先想清楚」的问题

写驱动之前（角色 ①②③ 三选一）先答这三个问题，答案直接决定你落在哪一节：

```
Q1：频率最终由谁写下去？
    ├─ 本地时钟树（clk + regulator）        → §1（不能有 fast_switch，见 04 §6.1）
    ├─ 固件（SCMI / SCPI / mailbox 协议）   → §2
    └─ CPU 旁边的硬件状态机（写个寄存器就完事）→ §3

Q2：调频路径会不会睡眠？
    ├─ 会（要 regulator_set_voltage）→ 不填 ->fast_switch，schedutil 走慢路径
    └─ 不会（一次 writel）           → 填 ->fast_switch，schedutil 走快路径

Q3：频率表从哪来？
    ├─ DTS 的 operating-points-v2 → 交给 OPP 生成（§1）
    └─ 从硬件 LUT / 固件查询读出来 → 自己 kcalloc 拼（§2/§3）
```

**Q2 的答案不是自由选择**，它由 Q1 决定：`->fast_switch()` 在
`cpufreq_driver_fast_switch()`（`drivers/cpufreq/cpufreq.c:2142`）里
被调度器上下文直接调用，那个上下文不允许睡眠。所以「本地时钟树类」
天生没有快路径 —— 详见 04 §6.1。

### §0.2 不在本篇的内容

| 你想找的 | 去哪 |
|---|---|
| 每个 API 的完整签名与返回码 | 01 篇 |
| `cpufreq_policy` 各字段谁写谁读 | 02 篇 |
| 注册流程、锁、状态机为什么是这个顺序 | 03 篇 |
| governor 家族 / boost / stats / 频率表框架内部 | 04 篇 |
| DTS 属性怎么写、写在哪个节点 | 06 篇 |
| 骨架跑不起来怎么定位 | 07 篇 |

---

## §1 角色①：频率表型驱动（本地时钟树，`->target_index`）

**形状**：一个 `platform_driver`，probe 里把 OPP 表变成频率表，
`->target_index()` 里调 `dev_pm_opp_set_rate()`。**自己不碰 clk 也不碰 regulator**
—— 那两步在 OPP 核心里做（04 §7.3）。

**参照**：`drivers/cpufreq/cpufreq-dt.c`（全篇 300 行左右，是本树最值得抄的一份）。

### §1.1 最小骨架

```c
/* 每个 policy 一份的私有数据，挂在 policy->driver_data 上 */
struct my_cpu_data {
	struct device			*cpu_dev;
	struct cpufreq_frequency_table	*freq_table;
};

static int my_set_target(struct cpufreq_policy *policy, unsigned int index)
{
	struct my_cpu_data *priv = policy->driver_data;
	unsigned long freq = policy->freq_table[index].frequency;

	/* 频率表单位是 kHz，OPP 单位是 Hz —— 差 1000 倍，这里最容易写错 */
	return dev_pm_opp_set_rate(priv->cpu_dev, freq * 1000);
}

static int my_cpufreq_init(struct cpufreq_policy *policy)
{
	struct my_cpu_data *priv;
	struct device *cpu_dev;
	int ret;

	cpu_dev = get_cpu_device(policy->cpu);
	if (!cpu_dev)
		return -EPROBE_DEFER;

	priv = kzalloc(sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	priv->cpu_dev = cpu_dev;

	/* (1) 谁和我共享频率 —— 决定 policy 边界，来源是 DTS 的 opp-shared */
	ret = dev_pm_opp_of_get_sharing_cpus(cpu_dev, policy->cpus);
	if (ret)
		goto err_free;

	/* (2) 为这一簇里所有 CPU 建 OPP 表 */
	ret = dev_pm_opp_of_cpumask_add_table(policy->cpus);
	if (ret)
		goto err_free;

	/* (3) OPP 表 → cpufreq 频率表 */
	ret = dev_pm_opp_init_cpufreq_table(cpu_dev, &priv->freq_table);
	if (ret)
		goto err_opp;

	policy->driver_data = priv;
	policy->freq_table  = priv->freq_table;
	policy->cpuinfo.transition_latency = 5 * NSEC_PER_USEC;	/* 换成真实值 */
	policy->dvfs_possible_from_any_cpu = true;

	return 0;

err_opp:
	dev_pm_opp_of_cpumask_remove_table(policy->cpus);
err_free:
	kfree(priv);
	return ret;
}

static int my_cpufreq_exit(struct cpufreq_policy *policy)
{
	struct my_cpu_data *priv = policy->driver_data;

	dev_pm_opp_free_cpufreq_table(priv->cpu_dev, &priv->freq_table);
	dev_pm_opp_of_cpumask_remove_table(policy->related_cpus);
	kfree(priv);
	return 0;
}

static struct cpufreq_driver my_cpufreq_driver = {
	.name		= "my-cpufreq",
	.flags		= CPUFREQ_NEED_INITIAL_FREQ_CHECK |
			  CPUFREQ_IS_COOLING_DEV,
	.verify		= cpufreq_generic_frequency_table_verify,
	.target_index	= my_set_target,
	.get		= cpufreq_generic_get,
	.init		= my_cpufreq_init,
	.exit		= my_cpufreq_exit,
	.register_em	= cpufreq_register_em_with_opp,
	.attr		= cpufreq_generic_attr,
};

static int my_probe(struct platform_device *pdev)
{
	return cpufreq_register_driver(&my_cpufreq_driver);
}

static int my_remove(struct platform_device *pdev)
{
	cpufreq_unregister_driver(&my_cpufreq_driver);
	return 0;
}
```

### §1.2 每一行的依据

| 骨架里的写法 | 真实出处 | 为什么必须这么写 |
|---|---|---|
| `->target_index` 只调 `dev_pm_opp_set_rate()` | `cpufreq-dt.c:57-63` | 电压/时钟顺序由 OPP 核心决定，驱动插手就会和 `opp-microvolt` 打架 |
| `freq * 1000` | 同上 | 频率表 kHz，OPP Hz —— 04 §7.3 |
| `dev_pm_opp_of_get_sharing_cpus()` | `cpufreq-dt.c:232` | **policy 边界不是 cpufreq 定的**，是 DTS `opp-shared` 定的 |
| `dev_pm_opp_init_cpufreq_table()` | `cpufreq-dt.c:283`；原型 `include/linux/cpufreq.h:703` | 顺带把 `turbo-mode` 变成 boost 项（04 §5.6） |
| `CPUFREQ_NEED_INITIAL_FREQ_CHECK` | `cpufreq-dt.c:176` | 不填会撞 03 §C1.1 的 `BUG_ON` —— 25 个驱动都填了 |
| `CPUFREQ_IS_COOLING_DEV` | `cpufreq-dt.c:177` | 让核心自动注册 cooling device（04 §7.1） |
| `cpufreq_generic_frequency_table_verify` | 实现 `drivers/cpufreq/freq_table.c:109-115` | 有频率表就该用它；⚠️ 它**会改写** `policy->max`（04 §3.4） |
| `cpufreq_generic_get` | 实现 `drivers/cpufreq/cpufreq.c:191-202` | ⚠️ 它读 `policy->clk`，**你没设 `policy->clk` 就会 `pr_err` 并返回 0** |
| `.register_em = cpufreq_register_em_with_opp` | `include/linux/cpufreq.h:1264-1268` | 一行拿到 Energy Model；注意它传的是 `related_cpus`（04 §7.2） |
| `.attr = cpufreq_generic_attr` | `include/linux/cpufreq.h:1256` | 提供 `scaling_available_frequencies` |

**骨架里故意留的坑**：上面用了 `cpufreq_generic_get`，但从没给 `policy->clk` 赋值。
真实的 `cpufreq-dt.c` 在 `:118` 做了 `clk_get()` 并把结果存进 `policy->clk`。
**你要么照抄那一步，要么改成自己的 `->get`**，否则 `scaling_cur_freq` 恒为 0
—— 这是 07 篇「频率读出来是 0」的第一号原因。

### §1.3 三个可选加项

| 加项 | 一行写法 | 代价 |
|---|---|---|
| 挂起时先降到安全频率 | `.suspend = cpufreq_generic_suspend`（`cpufreq-dt.c:188`） | 必须同时设 `policy->suspend_freq`（`cpufreq-dt.c:133`），否则这个回调什么也不做 |
| 支持 boost | `cpufreq_enable_boost_support()`（`cpufreq-dt.c:140`） | 只有 OPP 表里有 `turbo-mode` 时才该调；失败要不要让 probe 挂掉见 04 §5.6 |
| 中间频率 | `.get_intermediate` + `.target_intermediate` | 见 `include/linux/cpufreq.h:353-371` 的注释；本树 arm64 无人用 |

### §1.4 ⚠️ platform_device 从哪来

DTS 里**没有任何节点**的 compatible 是 `cpufreq-dt`。通用 DT 驱动的设备是
`drivers/cpufreq/cpufreq-dt-platdev.c:186-212` 里凭**根节点 compatible**
现造出来的（04 §6.3）。你自己写驱动时有两条路：

- **挂在别的设备上**（推荐）：让 CPU 旁边真实存在的那个节点（时钟控制器 / 固件通道）
  probe 时顺手 `cpufreq_register_driver()` —— §2 §3 都是这么做的。
- **也造一个 platform_device**：那你得先让 `cpufreq-dt-platdev.c` 的
  blocklist（`:103`）收下你的 compatible，否则通用驱动先注册成功，
  你会拿到 `-EEXIST`（03 §A1.3）。

---

## §2 角色②：固件型驱动（带 `->fast_switch`）

**形状**：频率不是自己写寄存器，而是发一条固件请求。因为固件通道是
「写一个共享内存槽 + 敲一下门铃」，**不睡眠**，所以可以填 `->fast_switch`。

**参照**：`drivers/cpufreq/scmi-cpufreq.c`（本树 arm64 最标准的固件型驱动）。

### §2.1 和 §1 的三处不同

```c
static struct cpufreq_driver my_fw_driver = {
	.name	= "my-fw-cpufreq",
	.flags	= CPUFREQ_HAVE_GOVERNOR_PER_POLICY |	/* 不同点 1 */
		  CPUFREQ_NEED_INITIAL_FREQ_CHECK |
		  CPUFREQ_IS_COOLING_DEV,
	.verify		= cpufreq_generic_frequency_table_verify,
	.attr		= cpufreq_generic_attr,
	.target_index	= my_fw_set_target,
	.fast_switch	= my_fw_fast_switch,		/* 不同点 2 */
	.get		= my_fw_get_rate,
	.init		= my_fw_init,
	.exit		= my_fw_exit,
	.register_em	= my_fw_register_em,		/* 不同点 3 */
};

/* 慢路径：允许睡眠，可以等固件回执 */
static int my_fw_set_target(struct cpufreq_policy *policy, unsigned int index)
{
	struct my_fw_data *priv = policy->driver_data;

	return my_fw_set_level(priv->handle, policy->freq_table[index].frequency);
}

/* 快路径：调度器上下文，绝对不能睡；返回"实际生效的频率"，失败返回 0 */
static unsigned int my_fw_fast_switch(struct cpufreq_policy *policy,
				      unsigned int target_freq)
{
	struct my_fw_data *priv = policy->driver_data;

	if (my_fw_set_level_nowait(priv->handle, target_freq))
		return 0;

	return target_freq;
}
```

三处不同的依据：

| 不同点 | 出处 | 含义 |
|---|---|---|
| `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` | `scmi-cpufreq.c:290` | 每个 policy 一套 governor tunable；不加就是全局一套（04 §2.3） |
| `.fast_switch` | `scmi-cpufreq.c:296`，实现在 `:68` | schedutil 走 `cpufreq_driver_fast_switch()`（`drivers/cpufreq/cpufreq.c:2142`）；**无 notifier、无 `->target_index`** |
| 自己的 `.register_em` | `scmi-cpufreq.c:262-286` | 固件知道真实功耗数字，不用 OPP 那份估算（04 §7.2） |

### §2.2 `->fast_switch` 的四条硬规矩

1. **不能睡眠**。里面出现 `mutex_lock` / `regulator_*` / `clk_set_rate`（多数
   clk 实现会睡）就是错的。
2. **返回值是「实际生效的频率」，不是错误码**。失败返回 `0`
   —— 见 `drivers/cpufreq/cpufreq.c:2142` 起的函数体怎么用这个返回值。
3. **不发 notifier**。`policy->cur` 由核心在快路径里更新，但
   `CPUFREQ_POSTCHANGE` 不会发出去。依赖 notifier 的模块（07 §4）在有
   `fast_switch` 的板子上会「什么都收不到」。cpufreq_stats **仍然被记账**
   （两个记录点，04 §4.2）。
4. **填了不等于会被用**。核心还要在 `cpufreq_enable_fast_switch()`
   （`drivers/cpufreq/cpufreq.c:494`）那里确认没有 transition notifier 注册者：
   `cpufreq_fast_switch_count >= 0` 才置 `policy->fast_switch_enabled = true`
   （`:502-504`），否则打
   `pr_warn("CPU%u: Fast frequency switching not enabled\n")`（`:506-507`）
   并把在场的 notifier 列出来（`:508`）。这条 warn 是 07 篇
   「填了 fast_switch 却没生效」的唯一线索。

### §2.3 频率表自己拼

固件型驱动的表不来自 DTS，得自己 kcalloc（`scmi-cpufreq.c` 的做法）：

```c
	nr = my_fw_count_levels(handle);
	freq_table = kcalloc(nr + 1, sizeof(*freq_table), GFP_KERNEL);
	if (!freq_table)
		return -ENOMEM;

	for (i = 0; i < nr; i++)
		freq_table[i].frequency = my_fw_level_to_khz(handle, i);
	freq_table[nr].frequency = CPUFREQ_TABLE_END;	/* 哨兵别忘 */

	policy->freq_table = freq_table;
```

`CPUFREQ_TABLE_END` 哨兵是**必须**的：`cpufreq_for_each_entry()`
（`include/linux/cpufreq.h:728-729`）就靠它停下来，漏了就是越界读。
表**按升序写**能让核心走二分查找而不是线性扫描（04 §3.3）。

---

## §3 角色③：硬件自治型驱动（`->ready` + 限频回报）

**形状**：CPU 旁边有一个自己会调频的硬件状态机。驱动做的事只有三件：
把硬件里的频率查找表（LUT）读成频率表、把「请给我这一档」写进一个寄存器、
以及**把硬件自己降下来的频率报回内核**。

**参照**：`drivers/cpufreq/qcom-cpufreq-hw.c`（本树最完整的一份）。

### §3.1 ops 上多出来的两个字段

```c
static struct cpufreq_driver my_hw_driver = {
	.flags		= CPUFREQ_NEED_INITIAL_FREQ_CHECK |
			  CPUFREQ_HAVE_GOVERNOR_PER_POLICY |
			  CPUFREQ_IS_COOLING_DEV,
	.verify		= cpufreq_generic_frequency_table_verify,
	.target_index	= my_hw_target_index,
	.get		= my_hw_get,
	.init		= my_hw_cpufreq_init,
	.exit		= my_hw_cpufreq_exit,
	.online		= my_hw_cpufreq_online,		/* 多出来 1 */
	.offline	= my_hw_cpufreq_offline,	/* 多出来 1 */
	.register_em	= cpufreq_register_em_with_opp,
	.fast_switch	= my_hw_fast_switch,
	.name		= "my-hw-cpufreq",
	.attr		= my_hw_cpufreq_attr,
	.ready		= my_hw_cpufreq_ready,		/* 多出来 2 */
};

/* 全框架最罕见的回调：核心把 policy 彻底装好之后才调 */
static void my_hw_cpufreq_ready(struct cpufreq_policy *policy)
{
	struct my_hw_data *data = policy->driver_data;

	if (data->throttle_irq >= 0)
		enable_irq(data->throttle_irq);
}
```

依据：`->ready` 原型在 `include/linux/cpufreq.h:389`（注释 `:388` 一句话说明
「驱动完全初始化之后才调」），本树唯一的实现是
`qcom-cpufreq-hw.c:634-640`（注册在 `:663`），核心唯一的调用点是
`drivers/cpufreq/cpufreq.c:1542-1543`。

**为什么限频中断必须放在 `->ready` 里而不是 probe 里**：中断处理函数会去读
`policy->driver_data`、会调 `dev_pm_opp_find_freq_floor()`、会更新 thermal pressure。
policy 还没装完就来一发中断，这些字段还是半成品。`->ready` 是**唯一**能保证
「policy 已经完整、governor 还没启动」的时刻。

### §3.2 限频回报：不经过 cpufreq 核心的那条路

硬件自己降频（温度、电流墙）时，cpufreq 核心**完全不知道**。
`qcom-cpufreq-hw.c` 的做法是自己把结果喂给调度器：

```c
static void my_hw_report_throttle(struct cpufreq_policy *policy)
{
	unsigned long throttled_freq, freq_hz;
	struct dev_pm_opp *opp;

	throttled_freq = my_hw_read_lmh_limit(policy);	/* 从硬件寄存器读 */
	freq_hz = throttled_freq * HZ_PER_KHZ;

	/* 归一化到 OPP 表里真实存在的一档：先向下找，越界再向上找 */
	opp = dev_pm_opp_find_freq_floor(dev, &freq_hz);
	if (IS_ERR(opp) && PTR_ERR(opp) == -ERANGE)
		opp = dev_pm_opp_find_freq_ceil(dev, &freq_hz);
	if (!IS_ERR(opp)) {
		throttled_freq = freq_hz / HZ_PER_KHZ;
		dev_pm_opp_put(opp);
	}

	/* 关键的一行：告诉调度器"这一簇 CPU 的算力上限被压低了" */
	arch_update_thermal_pressure(policy->related_cpus, throttled_freq);
}
```

依据：真实实现是 `qcom-cpufreq-hw.c:317` 起的函数，floor 在 `:331`、
`-ERANGE` 后改 ceil 在 `:333`（注释 `:325-328` 解释为什么要归一化），
`arch_update_thermal_pressure()` 在 `:344`。中断与轮询自动来回切换在 `:354-362`
（`:376` 的中断处理里先关中断再启动轮询，`:368` 是轮询函数）。

⚠️ **这条路完全绕开 cpufreq 核心**：不发 notifier、不改 `policy->cur`、
不进 cpufreq_stats。所以「硬件在限频但 `scaling_cur_freq` 一切正常」是**预期行为**
—— 07 篇把它列为独立症状。要看真实值只能读 thermal pressure
（`/sys/devices/system/cpu/cpu*/cpu_capacity` 不是它，别搞错）。

### §3.3 `.online` / `.offline` 什么时候需要

| 情况 | 填什么 | 理由 |
|---|---|---|
| CPU 下线时要关中断、上线时要开回来 | `.online` + `.offline` | 不填的话核心会走 `->exit()` + `->init()` 全拆全建（03 §A7） |
| 什么资源都不用管 | 都不填 | 多数驱动（`cpufreq-dt.c` / `scmi-cpufreq.c`）都不填 |

---

## §4 角色④：OPP/nvmem 预处理器（**不注册** `cpufreq_driver`）

这是最容易被漏掉的一个角色。**同一份 DTS，不同批次的芯片要跑不同的频率表**
（体质分档 / speed binning）。做这件事的驱动**根本不注册 `cpufreq_driver`**
—— 它只是在通用驱动起来之前，把 OPP 表改一改。

**本树三个代表**：

| 驱动 | 用的 OPP 改法 | 出处 |
|---|---|---|
| `sun50i-cpufreq-nvmem.c` | `dev_pm_opp_set_prop_name()` | `:113` |
| `qcom-cpufreq-nvmem.c` | `dev_pm_opp_set_config()` | `:349` |
| `imx-cpufreq-dt.c` | `dev_pm_opp_set_supported_hw()` | `:156` |

### §4.1 骨架

```c
static int my_nvmem_probe(struct platform_device *pdev)
{
	struct nvmem_cell *speedbin_nvmem;
	struct device_node *np;
	u32 speedbin;
	int i, ret;

	/* (1) 从 efuse 里读出这颗芯片的体质档位 */
	np = of_find_node_by_path("/cpus/cpu@0");
	speedbin_nvmem = of_nvmem_cell_get(np, NULL);
	if (IS_ERR(speedbin_nvmem))
		return PTR_ERR(speedbin_nvmem);
	/* 也可以用 nvmem_cell_read_u32()，见 imx-cpufreq-dt.c:116 */

	/* (2) 把档位翻译成 OPP 表能认的过滤条件 */
	for_each_possible_cpu(i) {
		struct device *cpu_dev = get_cpu_device(i);

		ret = dev_pm_opp_set_supported_hw(cpu_dev, &speedbin, 1);
		if (ret)
			return ret;
	}

	/* (3) 自己不注册 cpufreq_driver，而是把通用驱动叫起来 */
	platform_device_register_simple("cpufreq-dt", -1, NULL, 0);
	return 0;
}
```

依据：`nvmem_cell_read()` 的用法见 `sun50i-cpufreq-nvmem.c:63` 与
`qcom-cpufreq-nvmem.c:191`；`nvmem_cell_read_u32()` 见 `imx-cpufreq-dt.c:116`。

### §4.2 三个必须记住的后果

1. **初始化顺序是硬约束**。OPP 过滤条件必须在 `cpufreq-dt` 的
   `->init()` 建表之前设好，否则表已经建完了，改也没用。所以这类驱动
   一律用 `builtin_platform_driver` 或更早的 initcall，并且**由它来创建**
   `cpufreq-dt` 设备，而不是反过来。
2. **通用驱动必须被拦住**。`cpufreq-dt-platdev.c:103` 的 blocklist 就是为这个存在的
   （04 §6.3）：预处理器所在平台的 compatible 要进 blocklist，
   否则 `core_initcall` 抢先造出 `cpufreq-dt` 设备，你的过滤条件永远来不及。
3. **`scaling_available_frequencies` 会因为芯片不同而不同**，同一份镜像在两块板子上
   跑出不同的频率档数是正常的 —— 07 篇把这条列为「频率档位少了」的合法原因之一。

---

## §5 角色⑤：governor 作者

**先说结论：绝大多数人不需要写 governor。** arm64 上默认的 schedutil 住在
`kernel/sched/cpufreq_schedutil.c`，它拿得到调度器内部的利用率信号，
一个外部 governor 拿不到同样的输入。真正需要写 governor 的场景只有两种：
（a）频率必须由用户态某个进程决策；（b）有一套完全独立的负载模型。

**最小参照**：`drivers/cpufreq/cpufreq_userspace.c`（全篇 142 行，本树最短的完整 governor）。

### §5.1 五个回调 + 注册

```c
static int my_gov_init(struct cpufreq_policy *policy)
{
	unsigned int *data = kzalloc(sizeof(*data), GFP_KERNEL);

	if (!data)
		return -ENOMEM;
	policy->governor_data = data;		/* 每 policy 一份私有数据 */
	return 0;
}

static void my_gov_exit(struct cpufreq_policy *policy)
{
	kfree(policy->governor_data);
	policy->governor_data = NULL;
}

/* start：可以开始调频了。这里才允许启动定时器 / 注册 hook */
static int my_gov_start(struct cpufreq_policy *policy)
{
	unsigned int *data = policy->governor_data;

	*data = policy->cur;
	return 0;
}

static void my_gov_stop(struct cpufreq_policy *policy)
{
	/* 停掉一切异步的东西，返回后核心就要拆 policy 了 */
}

/* limits：policy->min / policy->max 变了，重新夹一下当前目标 */
static void my_gov_limits(struct cpufreq_policy *policy)
{
	unsigned int *data = policy->governor_data;

	if (policy->max < *data)
		__cpufreq_driver_target(policy, policy->max, CPUFREQ_RELATION_H);
	else if (policy->min > *data)
		__cpufreq_driver_target(policy, policy->min, CPUFREQ_RELATION_L);
	else
		__cpufreq_driver_target(policy, *data, CPUFREQ_RELATION_L);
}

static struct cpufreq_governor my_governor = {
	.name		= "mygov",		/* ≤ CPUFREQ_NAME_LEN */
	.init		= my_gov_init,
	.exit		= my_gov_exit,
	.start		= my_gov_start,
	.stop		= my_gov_stop,
	.limits		= my_gov_limits,
	.owner		= THIS_MODULE,
};
cpufreq_governor_init(my_governor);
cpufreq_governor_exit(my_governor);
```

字段清单的依据是 `struct cpufreq_governor`（`include/linux/cpufreq.h:582-596`）；
`.limits` 的三分支写法照抄 `cpufreq_userspace.c:99-115`；
注册宏 `cpufreq_governor_init` / `cpufreq_governor_exit` 定义在
`include/linux/cpufreq.h:629-641`。

### §5.2 五个易错点

| 易错点 | 依据 | 后果 |
|---|---|---|
| 注册宏用的是 `core_initcall`，不是 `module_init` | `include/linux/cpufreq.h:634` | governor 一定比驱动早注册；别在 init 里依赖任何设备 |
| `.limits` 是**必填** | 静态型 governor 也只填了它（`cpufreq_performance.c:14-18`） | 不填的话用户改 `scaling_max_freq` 不会有任何效果 |
| 调频用 `__cpufreq_driver_target()`（带下划线） | 定义 `drivers/cpufreq/cpufreq.c:2335` | 不带下划线的 `cpufreq_driver_target()`（`:2351`）自己拿 `policy->rwsem`；governor 回调里已经持锁，再拿一次就死锁 |
| 想动态调频要设 `CPUFREQ_GOV_DYNAMIC_SWITCHING` | `include/linux/cpufreq.h:601` | 不设的话有 `CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING` 的驱动（`:451`）会拒绝你 |
| 想让核心「精确设到这一档」要设 `CPUFREQ_GOV_STRICT_TARGET` | `include/linux/cpufreq.h:604` | 静态型两个都设了（`cpufreq_performance.c:23` / `cpufreq_powersave.c:24`） |

### §5.3 想把 tunable 挂到 sysfs

不要自己写 kobject。用 04 §2 讲的 dbs 共享层：
`CPUFREQ_DBS_GOVERNOR_INITIALIZER`（`drivers/cpufreq/cpufreq_governor.h:155`）
一次填满全部五个回调，tunable 走 `gov_attr_set`。
⚠️ 代价是你的 governor 必须接受采样式的骨架（定时器 + `->gov_dbs_update`），
而且 `governor_store` 在 `usage_count == 0` 时会返回 `-EBUSY`
（`cpufreq_governor_attr_set.c:32`）—— 那个 `-EBUSY` 的意思是「这个 governor 已经停了」，
不是「稍后重试」。

---

## §6 角色⑥：内核内消费者

不写驱动也不写 governor，只是**别的子系统想用 cpufreq**。这一节按「想干什么」分三档，
强烈建议**从下往上选**：能用 §6.1 就不要用 §6.2，能用 §6.2 就绝对不要用 §6.3。

| 想干什么 | 用什么 | 小节 |
|---|---|---|
| 只想知道频率是多少 | `cpufreq_quick_get()` 等 | §6.1 |
| 想给频率加个上限/下限 | freq_qos | §6.2 |
| 想在频率变化时收到通知 | notifier | §6.3（最后选择） |

### §6.1 只读频率

```c
	unsigned int khz;

	khz = cpufreq_quick_get(cpu);		/* 读 policy->cur，不问硬件，可能过期 */
	khz = cpufreq_get(cpu);			/* 会调驱动的 ->get()，可能睡眠 */
	khz = cpufreq_quick_get_max(cpu);	/* policy->max —— 当前生效的上限 */
	khz = cpufreq_get_hw_max_freq(cpu);	/* policy->cpuinfo.max_freq —— 硬件上限 */
```

原型都在 `include/linux/cpufreq.h:224-227`，实现分别在
`drivers/cpufreq/cpufreq.c` 的 `cpufreq_quick_get`（EXPORT 于 `:1799`）、
`cpufreq_quick_get_max`（`:1819`）、`cpufreq_get_hw_max_freq`（`:1839`）、
`cpufreq_get`（`:1871`）。

| 选哪个 | 判据 |
|---|---|
| `cpufreq_quick_get()` | 原子上下文、能容忍旧值。有 `fast_switch` 的板子上它是唯一实时的来源 |
| `cpufreq_get()` | 允许睡眠、要真值。⚠️ 它会去问硬件/固件，热路径里别用 |
| `cpufreq_quick_get_max()` vs `cpufreq_get_hw_max_freq()` | 前者会被 thermal / 用户态压低，后者不会。「算力归一化」要用后者，「现在最多能跑多快」要用前者 |

需要整个 policy 而不只是一个数时用 `cpufreq_cpu_get()`（`:238`）
—— 它**加了 kobject 引用**，必须配一次 `cpufreq_cpu_put()`（`:248`）。
原子上下文里想省掉这对引用可以用 `cpufreq_cpu_get_raw()`（`:183-188`），
但你得自己保证 policy 不会在你手里消失。

### §6.2 加限值（推荐做法）

**这是 v6.1 里正确的「限频」方式**。thermal 的 cpufreq cooling 就是这么做的，
用户态写 `scaling_max_freq` 走的也是同一条路 —— 两者只是不同的 requester（03 §B3.3）。

```c
	struct freq_qos_request my_req;
	struct cpufreq_policy *policy = cpufreq_cpu_get(cpu);

	if (!policy)
		return -ENODEV;

	/* 加一个"最高不许超过 1.2 GHz"的请求 */
	ret = freq_qos_add_request(&policy->constraints, &my_req,
				   FREQ_QOS_MAX, 1200000 /* kHz */);
	if (ret < 0)
		goto put;

	/* 后续想改就 update，不要 remove 再 add */
	freq_qos_update_request(&my_req, 800000);

	/* 撤销自己的意见（不是"恢复原值"，是"我不再有意见"） */
	freq_qos_remove_request(&my_req);
put:
	cpufreq_cpu_put(policy);
```

依据：三个函数的原型在 `include/linux/pm_qos.h:304-308`，
`FREQ_QOS_MIN` / `FREQ_QOS_MAX` 在 `:81-82`；
真实用法见 `drivers/thermal/cpufreq_cooling.c:555`（add）、`:463`（update）、
`:580` 和 `:666`（两条 remove 路径）；cpufreq 核心自己也用同一套
（`drivers/cpufreq/cpufreq.c:1425` 和 `:1445` 建 min/max 两个请求）。

**四条必须理解的语义**：

1. **限值是「多方投票取交集」，不是「谁写谁赢」**。你的 `FREQ_QOS_MAX` 和
   thermal 的、用户态的一起取最小值。所以「我设了 1.2G 但实际只跑到 800M」
   多半是别人也投了票，不是你的请求失效了。
2. **`remove` 不等于「恢复」**。它只是撤掉你这一票，最终值由剩下的票决定。
3. **`add` 必须配 `remove`**，否则模块卸载后 `policy->constraints`
   里留着一个指向已释放内存的节点。
4. **不要直接改 `policy->min` / `policy->max`**。那两个字段是 freq_qos 的
   *输出*，你写进去下一次 `refresh_frequency_limits()`（`drivers/cpufreq/cpufreq.c:1153`）就覆盖了。

### §6.3 收频率变化通知（最后选择）

```c
static int my_notify(struct notifier_block *nb, unsigned long val, void *data)
{
	struct cpufreq_freqs *freq = data;

	if (val == CPUFREQ_POSTCHANGE)
		pr_info("cpu%u: %u -> %u kHz\n", freq->policy->cpu,
			freq->old, freq->new);
	return NOTIFY_OK;
}

static struct notifier_block my_nb = { .notifier_call = my_notify };

	/* 注册：list 只有两个合法值 */
	cpufreq_register_notifier(&my_nb, CPUFREQ_TRANSITION_NOTIFIER);
	...
	cpufreq_unregister_notifier(&my_nb, CPUFREQ_TRANSITION_NOTIFIER);
```

依据：原型 `include/linux/cpufreq.h:515-516`；两个 list 常量
`CPUFREQ_TRANSITION_NOTIFIER`(0) / `CPUFREQ_POLICY_NOTIFIER`(1) 在 `:503-504`；
transition 事件 `CPUFREQ_PRECHANGE`(0) / `CPUFREQ_POSTCHANGE`(1) 在 `:507-508`；
policy 事件 `CPUFREQ_CREATE_POLICY`(0) / `CPUFREQ_REMOVE_POLICY`(1) 在 `:511-512`；
`struct cpufreq_freqs` 在 `:184-189`。

**为什么放在最后**：注册一个 transition notifier 会**让全系统的
`->fast_switch` 失效**。`cpufreq_enable_fast_switch()`（`drivers/cpufreq/cpufreq.c:494`）在
`cpufreq_fast_switch_count < 0` 时拒绝启用快路径，而注册 notifier 正是
把那个计数变负的动作 —— 函数注释 `:489-492` 把这件事写得很直白：
「快速调频和 transition notifier 从根本上互相冲突」。

⚠️ 后果链：你加了一个只为打日志的 notifier → scmi / qcom 板子上的
schedutil 从快路径退回慢路径 → 调频延迟从微秒级变成毫秒级。
**能用 §6.2 的 freq_qos 或 §6.1 的读接口解决的，就不要用 notifier。**

---

## §7 角色⑦：用户态调频与观测

⚠️ 下面每条命令的**文件名**都对着源码核过（`cpufreq_attrs[]`，
`drivers/cpufreq/cpufreq.c:948-961`），但**输出内容是示意，不是真机粘贴**
（本篇没有上板运行，见篇首声明）。

### §7.1 目录在哪、有哪些文件

```bash
# 每个 policy 一个目录，符号链接从每个 CPU 指过来
ls /sys/devices/system/cpu/cpu0/cpufreq/
ls /sys/devices/system/cpu/cpufreq/          # policyN 目录 + 全局 boost 开关
```

| 文件 | 读/写 | 含义 | 定义处 |
|---|---|---|---|
| `cpuinfo_min_freq` / `cpuinfo_max_freq` | r | **硬件**上下限，不受限频影响 | `drivers/cpufreq/cpufreq.c:934-935` |
| `cpuinfo_transition_latency` | r | 驱动申报的调频耗时（ns） | `:936` |
| `cpuinfo_cur_freq` | r | 问硬件要的真实频率；权限是 `0400` | `:933` |
| `scaling_min_freq` / `scaling_max_freq` | rw | **当前生效**的上下限（freq_qos 的结果） | `:943-944` |
| `scaling_cur_freq` | r | 内核认为的当前频率（`policy->cur`） | `:939` |
| `scaling_governor` | rw | 当前 governor | `:945` |
| `scaling_available_governors` | r | 能切哪些 | `:937` |
| `scaling_driver` | r | 哪个驱动在管（`cpufreq-dt` / `scmi_cpufreq` / …） | `:938` |
| `scaling_setspeed` | rw | 只有 userspace governor 才有意义 | `:946` |
| `affected_cpus` / `related_cpus` | r | 前者只含在线的，后者含全部（02 篇） | `:941-942` |
| `bios_limit` | r | 需驱动实现 `->bios_limit`；arm64 一般没有 | `:940` |
| `scaling_available_frequencies` | r | 需驱动带 `cpufreq_generic_attr`（§1.2） | `freq_table.c:279` |
| `scaling_boost_frequencies` | r | 需驱动注册 boost 属性（04 §5） | `freq_table.c:291` |
| `boost`（**全局**，不在 policy 目录里） | rw | 唯一的全局开关 | `drivers/cpufreq/cpufreq.c:635` |
| `stats/` 子目录四个文件 | r/w | 见 04 §4 | `cpufreq_stats.c:175-181` |

### §7.2 常用动作

```bash
C=/sys/devices/system/cpu/cpu0/cpufreq

# 看现状
cat $C/scaling_driver $C/scaling_governor
cat $C/scaling_cur_freq                       # 内核视角（kHz）
cat $C/cpuinfo_cur_freq                       # 硬件视角，需要 root
cat $C/scaling_available_frequencies          # 没这个文件 = 驱动没给 attr

# 切 governor
cat $C/scaling_available_governors
echo performance > $C/scaling_governor

# 锁死一个频率（两步，顺序有讲究）
echo userspace   > $C/scaling_governor
echo 1200000     > $C/scaling_setspeed        # kHz

# 只压上限（不换 governor，推荐）
echo 1200000 > $C/scaling_max_freq
cat  $C/scaling_max_freq                      # 一定回读，见下

# 全局 boost
cat  /sys/devices/system/cpu/cpufreq/boost
echo 0 > /sys/devices/system/cpu/cpufreq/boost

# 调频统计
cat $C/stats/time_in_state
cat $C/stats/total_trans
echo 1 > $C/stats/reset
```

### §7.3 四个「写进去和读出来不一样」的陷阱

| 现象 | 原因 | 出处 |
|---|---|---|
| 写 `scaling_max_freq=500000`，读回来是 600000 | `->verify` 会把上限**抬到频率表里存在的下一档**，而且**一条日志都不打** | 04 §3.4（`freq_table.c:89-95`） |
| 写了 `scaling_max_freq` 但没生效 | thermal 也投了票，取交集后不是你的值 | §6.2 第 1 条 |
| 切到 `userspace` 后 `scaling_setspeed` 才出现 | 它是唯一实现 `store_setspeed` 的 governor | 04 §1.5（`cpufreq_userspace.c:117-127`） |
| `echo 1 > stats/reset` 之后再看 `trans_table` 还是老数据 | reset 是**延迟生效**的，下一次真实调频才清 | 04 §4.4（`cpufreq_stats.c:275-276`） |

### §7.4 两个内核启动参数

| 参数 | 作用 | 定义处 |
|---|---|---|
| `cpufreq.off=1` | 整个 cpufreq 不启动 | `drivers/cpufreq/cpufreq.c:2996` |
| `cpufreq.default_governor=<名字>` | 覆盖编译期选的默认 governor | `:2997` |

这是 cpufreq 核心**全部**的模块参数（两条 `module_param`，
在 `drivers/cpufreq/cpufreq.c` 里 grep `module_param` 只有这两行）。
governor 的 tunable 一律走 sysfs，不走 cmdline。

---

## §8 反面清单与导航

### §8.1 十条「别这么写」

| # | 别这么写 | 为什么 | 去哪看 |
|---|---|---|---|
| 1 | 在 `->fast_switch()` 里 `mutex_lock` / 调 regulator | 调度器上下文不许睡 | §2.2 |
| 2 | 让 `->fast_switch()` 返回错误码 | 它的返回值是频率，失败要返回 `0` | §2.2 |
| 3 | 只为打日志注册 transition notifier | 全系统快路径被关掉 | §6.3 |
| 4 | 直接写 `policy->min` / `policy->max` | 下一次 limits 刷新就被覆盖 | §6.2 第 4 条 |
| 5 | 用 `cpufreq_get_hw_max_freq()` 判断「现在最快能跑多少」 | 它是硬件上限，不含限频 | §6.1 |
| 6 | 忘记 `CPUFREQ_TABLE_END` 哨兵 | 遍历宏靠它停 | §2.3 |
| 7 | 忘记 `CPUFREQ_NEED_INITIAL_FREQ_CHECK` | 撞核心的 `BUG_ON` | §1.2 |
| 8 | 用 `cpufreq_generic_get` 却不设 `policy->clk` | `scaling_cur_freq` 恒为 0 | §1.2 |
| 9 | 在 governor 回调里调 `cpufreq_driver_target()`（不带下划线） | 重复取 `policy->rwsem` | §5.2 |
| 10 | OPP 预处理器不进 blocklist | 通用驱动抢先，过滤条件来不及生效 | §4.2 第 2 条 |

### §8.2 角色 → 最小改动清单

| 角色 | 最少要填的字段 / 调的函数 |
|---|---|
| ① 频率表型 | `.init` `.exit` `.verify` `.target_index` `.get` `.name` + `CPUFREQ_NEED_INITIAL_FREQ_CHECK` |
| ② 固件型 | ① 的全部 + `.fast_switch` + `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` |
| ③ 硬件自治型 | ② 的全部 + `.ready` +（可选）`.online` `.offline` + 限频回报 |
| ④ 预处理器 | 一个 `dev_pm_opp_set_*()` + 造出 `cpufreq-dt` 设备 + 进 blocklist |
| ⑤ governor | `.name` `.limits` `.owner` + `cpufreq_governor_init()` |
| ⑥ 消费者 | 首选 `cpufreq_quick_get*()`；要限频用 `freq_qos_add_request()` |
| ⑦ 用户态 | `scaling_governor` + `scaling_max_freq` 两个文件够用九成场景 |

### §8.3 读完这篇去哪

| 下一步 | 去哪 |
|---|---|
| DTS 要写哪些属性（`operating-points-v2` / `opp-shared` / `turbo-mode`） | 06 篇 |
| 骨架跑起来了但行为不对 | 07 篇 |
| 想搞懂 policy 和 CPU 为什么是两层 | 08 篇 |
| 想快速复习整个子系统 | 00 篇 |
