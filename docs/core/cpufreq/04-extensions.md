---
title: 扩展机制
---

# cpufreq 04 —— 子框架与驱动家族

## §0 本篇的组织方式

**组织轴：一节一个独立框架，各节之间互不调用。**

这一点要先说清楚，因为它和 03 篇正好相反。03 篇是**一条时间线**——注册、创建、
挂 governor、采样、下发、拆除，前一节的结尾就是后一节的开头。本篇不是：
下面七节里的任意两节，都**不会互相调用**。它们的共同点只有一个——
**都挂在 03 篇那条时间线的某一点上**，然后各自独立地长出去。

所以本篇的正确读法是：**先看 §0.2 的分诊表决定你要读哪几节，然后单独读那一节。**
从头读到尾没有意义，因为没有贯穿的因果链。

每节的固定结构：

1. **它是什么**（一句话）
2. **挂载点**：它挂在 03 时间线的哪一点上（带行号）
3. **它自己的内部结构**
4. **arm64 上会不会碰到**

### 0.1 七个框架的全景

```
                        cpufreq 核心（drivers/cpufreq/cpufreq.c）
                                     │
     ┌───────────┬───────────┬────────┼────────┬───────────┬──────────┐
     │           │           │        │        │           │          │
  §1 governor  §2 dbs     §3 频率   §4 stats §5 boost  §6 驱动   §7 外部
     家族       共享层      表框架            框架       家族      集成
     │           │           │        │        │           │          │
  6 个 gov   ondemand/    freq_      sysfs   sysfs 全局  cpufreq-dt  thermal
  分三类     conservative table.c   四个文件  一个开关   scmi/qcom   Energy Model
             共用的采样层  370 行    290 行   +2721-2819  25+ 驱动    OPP
```

注意这张图**不是调用关系图**，是"挂在同一个核心上的七个东西"。
真正的调用关系只有两条：§2 被 §1 里的两个 governor 使用；§3 被 §6 的驱动使用。
其余五个之间零调用。

### 0.2 分诊表：arm64 上你会碰到哪几节

| 节 | 框架 | arm64 主流平台 | 建议 |
|---|---|---|---|
| §1 | governor 家族 | 一定碰到（schedutil 是 arm64 默认） | 必读 |
| §2 | dbs 共享层 | 只有手工切到 ondemand/conservative 才碰到 | 选读 |
| §3 | 频率表框架 | 一定碰到（`cpufreq-dt` 完全靠它） | 必读 |
| §4 | cpufreq_stats | 默认编进来，是排查频率行为的第一现场 | 建议读 |
| §5 | boost 框架 | arm64 上多数驱动不实现 `->set_boost` | 选读 |
| §6 | 驱动家族 | 一定碰到 | 必读 |
| §7 | 外部集成（thermal / EM / OPP） | 一定碰到 | 必读 |

⚠️ 显式裁掉的部分：`intel_pstate` / `amd-pstate` / `acpi-cpufreq` 三个 x86+ACPI
驱动**不在本篇范围内**。它们不只是"另一个驱动"——`intel_pstate` 会**整体替换掉
governor 层**（自己实现 P-state 选择），所以它的存在会让 §1 的结论看起来不成立。
本篇的所有结论都限定在"驱动老老实实实现 `->target_index` / `->fast_switch`"这个前提下。
这条裁剪记进 99 §2.7。

---

## §1 governor 家族：六个，分三类

### 1.1 挂载点

governor 挂在 03 时间线的**两点**上：

- **模块加载时**：`cpufreq_register_governor()` 把自己挂进全局链表
  （`drivers/cpufreq/cpufreq.c:2466`），由每个 governor 文件末尾的
  `cpufreq_governor_init()` 宏触发；
- **policy 创建时**：核心从链表里按名字找出一个，走 init → start
  （见 03 §A3）。

### 1.2 三类的分界线：谁来决定"什么时候该换频率"

这是 governor 家族**唯一重要的分类轴**。不是"简单/复杂"，也不是"省电/性能"，
而是**触发源在哪里**：

| 类 | 成员 | 触发源 | 实现的回调 | 挂载文件 |
|---|---|---|---|---|
| **静态类** | performance · powersave | **不触发**，只在 limits 变化时动一次 | 只有 `.limits` | `cpufreq_performance.c` · `cpufreq_powersave.c` |
| **用户态类** | userspace | 用户写 sysfs | `.limits` + `.store_setspeed` / `.show_setspeed` | `cpufreq_userspace.c` |
| **采样类** | ondemand · conservative · schedutil | **调度器回调**（见 03 §A4/§A5） | 全套 init/exit/start/stop/limits | `cpufreq_ondemand.c` · `cpufreq_conservative.c` · `kernel/sched/cpufreq_schedutil.c` |

**静态类完全没有"周期性动作"**。这一点最容易被想错——很多人以为
`performance` 是"每次都把频率顶到最高"，其实它一辈子只在三个时刻被调用：
policy 刚 start 时、limits 变化时、resume 时。`cpufreq_performance.c:14-18`
是这个 governor 的**全部**逻辑：

```c
static void cpufreq_gov_performance_limits(struct cpufreq_policy *policy)
{
	pr_debug("setting to %u kHz\n", policy->max);
	__cpufreq_driver_target(policy, policy->max, CPUFREQ_RELATION_H);
}
```

`powersave` 是它的镜像（`cpufreq_powersave.c:14-18`），只差两个字：
`policy->min` 和 `CPUFREQ_RELATION_L`。

### 1.3 静态类的两个附加事实

**(a) 两者都设 `CPUFREQ_GOV_STRICT_TARGET`**
（`cpufreq_performance.c:23` · `cpufreq_powersave.c:24`）。

这个标志的后果在 03 §A3.2 讲过：它会让核心把 `policy->strict_target` 置位，
从而**禁止驱动做"频率就近取整"之类的自由裁量**。逻辑上很自然——
你要求"最高"就是最高，驱动不该给你一个"差不多最高"。

**(b) `performance` 是全树唯一的 fallback governor。**

```bash
grep -rn 'cpufreq_fallback_governor' drivers/cpufreq/ include/linux/cpufreq.h
```

除了 `include/linux/cpufreq.h` 里的声明和 `drivers/cpufreq/cpufreq.c` 里的调用点，
**全树只有一处定义**：`drivers/cpufreq/cpufreq_performance.c:34`。而且它带条件：

```c
#ifndef CONFIG_CPU_FREQ_GOV_PERFORMANCE_MODULE
struct cpufreq_governor *cpufreq_fallback_governor(void)
{
	return &cpufreq_gov_performance;
}
#endif
```

这个 `#ifndef` 是关键：**如果你把 performance governor 编成模块，
内核就没有 fallback governor 了。** 03 §A3.2 那条"动态切换被禁止时回落到
fallback"的路，在这种配置下会拿到 NULL。这不是理论风险——`CONFIG_CPU_FREQ_GOV_PERFORMANCE`
的默认值是 `y`（内建），但它是可以改成 `m` 的。

### 1.4 `cpufreq_default_governor()`：四个候选，编译期选一个

同一个函数名，在四个文件里各有一份定义，全部包在互斥的 `#ifdef` 里：

| 文件 | 守卫宏 | 位置 |
|---|---|---|
| `cpufreq_performance.c` | `CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE` | `:28` |
| `cpufreq_powersave.c` | `CONFIG_CPU_FREQ_DEFAULT_GOV_POWERSAVE` | `:32` |
| `cpufreq_userspace.c` | `CONFIG_CPU_FREQ_DEFAULT_GOV_USERSPACE` | `:135` |
| `cpufreq_ondemand.c` | `CONFIG_CPU_FREQ_DEFAULT_GOV_ONDEMAND` | `:479` |

另外两个（conservative / schedutil）也各有一份，配置项分别是
`CONFIG_CPU_FREQ_DEFAULT_GOV_CONSERVATIVE` 和 `CONFIG_CPU_FREQ_DEFAULT_GOV_SCHEDUTIL`。
这六个 Kconfig 选项是 `choice` 关系，**只能选一个**，所以链接期不会重定义。

⇒ 这就是 03 §A1.1 那条"三层默认 governor 优先级"里**最低那一层**的来源：
命令行 `cpufreq.default_governor=` > 编译期 `cpufreq_default_governor()` >
fallback。

### 1.5 userspace：唯一实现 setspeed 的 governor

`cpufreq_userspace.c:117-127` 的 ops 表里有两个别人都没有的字段：

```c
	.store_setspeed	= cpufreq_set,
	.show_setspeed	= show_speed,
```

这两个字段的存在，是 sysfs 里 `scaling_setspeed` 这个文件**时有时无**的原因
（02 篇讲 sysfs 属性表时提过）：核心只有在当前 governor 填了 `store_setspeed`
时才让这个文件生效。

它的 `.limits` 回调（`cpufreq_userspace.c:99-115`）是三分支的，很值得一读——
因为它**明确处理了"用户设的值超出当前 limits"这种情况**：

```c
	if (policy->max < *setspeed)
		__cpufreq_driver_target(policy, policy->max, CPUFREQ_RELATION_H);
	else if (policy->min > *setspeed)
		__cpufreq_driver_target(policy, policy->min, CPUFREQ_RELATION_L);
	else
		__cpufreq_driver_target(policy, *setspeed, CPUFREQ_RELATION_L);
```

注意它**没有修改 `*setspeed`**——用户设的值被原样记住，只是暂时按 limits 夹住。
limits 一放开，下次 `.limits` 回调就会把频率恢复到用户设的值。
这和 03 §B2.2 里核心自己的做法（`WRITE_ONCE(policy->min, ...)` 直接改写）
是两种不同的哲学：governor 层保留用户意图，核心层直接改写状态。

### 1.6 三类的选择建议（arm64）

| 场景 | 选谁 | 理由 |
|---|---|---|
| 通用（默认） | **schedutil** | 唯一能拿到调度器内部利用率信号的（03 §A5），也是唯一能走 `fast_switch` 的（03 §B4） |
| 性能基线测试 | performance | 频率钉死在 max，排除频率抖动这个变量 |
| 功耗基线测试 | powersave | 同上，钉死在 min |
| 手工扫频（做功耗曲线） | userspace | 唯一能从用户态精确指定频率的 |
| 老式负载采样 | ondemand | arm64 上一般不用：它靠 `dbs_update()` 从 `/proc/stat` 口径的 idle 时间反推负载（03 §A4.3），精度不如 schedutil 直接读 `cpu_util()` |
| 平滑升频 | conservative | 同上，且它每次只挪 `freq_step`（5%），响应更慢 |

---

## §2 dbs 共享层：ondemand 和 conservative 共用的采样机

### 2.1 它是什么

`cpufreq_governor.c`（**580 行**）+ `cpufreq_governor.h` + `cpufreq_governor_attr_set.c`
（**76 行**）三个文件，构成一个**只服务于两个 governor 的中间层**。

这是 cpufreq 里唯一一处"两个 governor 共享实现"的地方。schedutil 不用它，
静态类和 userspace 也不用它。所以本节的所有内容，在 arm64 默认配置下
（schedutil）**一行都不会执行**。

### 2.2 挂载点

`CPUFREQ_DBS_GOVERNOR_INITIALIZER` 宏（`cpufreq_governor.h:155`）把
`cpufreq_governor` 的五个回调**全部填成 dbs 层的通用实现**
（`cpufreq_governor.h:160-164`）：

```c
		.init = cpufreq_dbs_governor_init,
		.exit = cpufreq_dbs_governor_exit,
		.start = cpufreq_dbs_governor_start,
		.stop = cpufreq_dbs_governor_stop,
		.limits = cpufreq_dbs_governor_limits,
```

⇒ 所以从核心的角度看，ondemand 和 conservative 是**两个长得一模一样的 governor**；
差异全在 dbs 层内部的 `dbs_governor` 结构里（`cpufreq_governor.h:125`）。

### 2.3 三层结构：`dbs_governor` / `dbs_data` / `policy_dbs_info`

| 结构 | 定义 | 一份对应什么 | 装什么 |
|---|---|---|---|
| `dbs_governor` | `cpufreq_governor.h:125` | **一个 governor**（静态变量） | 内嵌 `cpufreq_governor`、`gov_dbs_timer` 回调、`alloc`/`free`/`init`/`exit` 四个钩子（`:136-139`） |
| `dbs_data` | `cpufreq_governor.h:38` | **一组共享 tunable** | `up_threshold` / `sampling_rate` / `sampling_down_factor` / `ignore_nice_load` / `io_is_busy` + 内嵌 `gov_attr_set` |
| `policy_dbs_info` | `cpufreq_governor.h:78` | **一个 policy** | 采样时间戳、`work_in_progress`、`irq_work`、`work`、`update_mutex`，以及指回 `dbs_data` 的指针（`:92`） |

这个三层结构解决的问题是：**tunable 到底该是每个 policy 一份，还是全局一份？**

答案是"取决于驱动"。`CPUFREQ_HAVE_GOVERNOR_PER_POLICY` 这个驱动标志决定了
`dbs_data` 是新建一份还是复用全局的 `dbs_governor.gdbs_data`
（`cpufreq_governor.h:133`）。所以在 arm64 大小核平台上：

- 驱动设了这个标志（`scmi-cpufreq.c:290` · `qcom-cpufreq-hw.c:650` 都设了）
  ⇒ 大核和小核各有一套 `up_threshold`；
- 驱动没设（`cpufreq-dt.c:176-177` **没设**）
  ⇒ 所有 policy 共享一套 tunable，改一个 CPU 的 `up_threshold`，另一族也跟着变。

### 2.4 `gov_attr_set`：让"一组 policy 共享一个 kobject"成立

`cpufreq_governor_attr_set.c` 只有 76 行，但它是上面那个"共享"能安全工作的原因。
四个函数：

| 函数 | 位置 | 做什么 |
|---|---|---|
| `gov_attr_set_init` | `:43` | `usage_count = 1`，把第一个 policy 挂进 `policy_list` |
| `gov_attr_set_get` | `:52` | `usage_count++`，再挂一个 policy 进链表 |
| `gov_attr_set_put` | `:61` | `usage_count--`；归零时 `mutex_destroy` + `kobject_put` |
| `governor_store` | `:24` | sysfs 写入的统一入口 |

`governor_store()` 里有一行值得单独看（`:32`）：

```c
	ret = attr_set->usage_count ? gattr->store(attr_set, buf, count) : -EBUSY;
```

这是**一个用引用计数当"对象还活着吗"标志**的写法。它防的是这种竞态：
最后一个 policy 已经走完 `gov_attr_set_put()` 把计数减到 0，但 kobject 还没真正
释放（`kobject_put` 是异步的），此时用户态的 `write()` 打进来。返回 `-EBUSY`
而不是 crash。

⚠️ 但这里有个**语义偏差值得记一笔**：`-EBUSY` 的字面意思是"忙，稍后重试"，
而这里的真实含义是"这个 tunable 组已经没有 policy 了，重试也不会成功"。
写脚本轮询重试的人会在这里死循环。这一条进 07 篇的错误码分诊表。

### 2.5 两个 governor 的 tunable 对照

| tunable | ondemand | conservative | 默认值 |
|---|---|---|---|
| `sampling_rate` | ✅ `:325` | ✅ `:253` | 由 `transition_latency` 推算 |
| `up_threshold` | ✅ `:327` | ✅ `:255` | **80**（`cpufreq_ondemand.c:21` · `cpufreq_conservative.c:31`） |
| `sampling_down_factor` | ✅ `:328` | ✅ `:254` | **1**（`cpufreq_ondemand.c:22` · `cpufreq_conservative.c:34`） |
| `ignore_nice_load` | ✅ `:329` | ✅ `:256` | 0 |
| `io_is_busy` | ✅ `:326` | ✗ | 见 03 §A4.3 |
| `powersave_bias` | ✅ `:330` | ✗ | 0 |
| `down_threshold` | ✗ | ✅ `:257` | **20**（`cpufreq_conservative.c:32`） |
| `freq_step` | ✗ | ✅ `:258` | **5**（%，`cpufreq_conservative.c:33`） |

这张表就是两个 governor 的**全部差异**（除了 `od_update()` / `cs_dbs_update()`
两个决策函数本身）。ondemand 多两个（io 感知 + 偏置），conservative 多两个
（下调门限 + 步长）。

`cpufreq_ondemand.c:24-25` 还有两个不出现在 sysfs 里的常量：
`MICRO_FREQUENCY_UP_THRESHOLD` 为 **95**、`MICRO_FREQUENCY_MIN_SAMPLE_RATE`
为 **10000**（微秒）。前者会在 init 时**覆盖掉 80 这个默认值**，判据不是"驱动快不慢"，
而是**内核有没有微秒级 idle 统计**（`cpufreq_ondemand.c:368-376`）：

```c
	cpu = get_cpu();
	idle_time = get_cpu_idle_time_us(cpu, NULL);
	put_cpu();
	if (idle_time != -1ULL) {
		/* Idle micro accounting is supported. Use finer thresholds */
		dbs_data->up_threshold = MICRO_FREQUENCY_UP_THRESHOLD;
	} else {
		dbs_data->up_threshold = DEF_FREQUENCY_UP_THRESHOLD;
	}
```

`get_cpu_idle_time_us()` 只在 `CONFIG_NO_HZ_COMMON` 下返回真实值，
否则返回 `-1ULL`。arm64 的 defconfig 开着 `NO_HZ_IDLE` ⇒ **arm64 上
`up_threshold` 的实际默认值是 95，不是表里那个 80**。
这是 07 篇排查"为什么我的 up_threshold 显示 95"时的答案。

---

## §3 频率表框架：`freq_table.c`，370 行

### 3.1 它是什么

一组**帮驱动把"一张频率数组"变成"cpufreq 核心能用的东西"的辅助函数**。
它不是必需的——驱动完全可以不给频率表，自己在 `->target()` 里做任意插值
（03 §A6.1 讲过那条 deprecated 分支）。但 arm64 上的主流驱动**全都用它**，
因为 OPP 表天然就是一张频率数组。

### 3.2 挂载点：三个

| 时机 | 谁调它 | 在 03 时间线的位置 |
|---|---|---|
| policy 创建 | 核心调 `cpufreq_table_validate_and_sort()`（`freq_table.c:354`） | 03 §A2 里 `->init()` 返回之后 |
| 每次限值校验 | 驱动把 `.verify` 填成 `cpufreq_generic_frequency_table_verify`（`freq_table.c:109`） | 03 §B2.1 第 3 步 |
| 每次下发 | 核心调 `cpufreq_frequency_table_target()`（inline，在 `include/linux/cpufreq.h`） | 03 §A6.1 的 `__resolve_freq()` |

⇒ 这三点覆盖了 policy 的**创建、约束、动作**三个环节，所以说"驱动只要给一张表，
剩下的核心全包了"。

### 3.3 排序：一次性做完，之后靠 `freq_table_sorted` 分流

`set_freq_table_sorted()`（`freq_table.c:299-352`）是这个框架里最值得读的一段。
它一次遍历就同时做完三件事：

```c
		if (pos->frequency == prev->frequency) {
			pr_warn("Duplicate freq-table entries: %u\n",
				pos->frequency);
			return -EINVAL;
		}
```

**(1) 查重**（`:313-316`）：这是 cpufreq **唯一一处会因为 DTS/OPP 写错而拒绝
整个 policy 的地方**。OPP 表里写了两条同频率的 opp 节点，policy 创建就会失败。
07 篇的错误消息映射表要收这一条。

**(2) 判方向**（`:319-338`）：用一个 `ascending` 计数器边走边加减。
一旦方向翻转就 `pr_debug("Freq table is unsorted\n")` 然后 `return 0`
（`:322-324` 升转降 · `:332-334` 降转升）。

注意这里 `return 0` 而不是返回错误——**乱序表是合法的**，只是走慢路。
`policy->freq_table_sorted` 在 `:305` 已经先设成 `CPUFREQ_TABLE_UNSORTED`，
所以提前返回就等于"保持乱序判定"。

**(3) 记结论**（`:343-346`）：走完全程才根据 `ascending` 的正负写入
`CPUFREQ_TABLE_SORTED_ASCENDING` / `..._DESCENDING`。

⇒ 这个字段的用途：`cpufreq_frequency_table_target()` 会据此在
**二分查找**（有序）和 `cpufreq_table_index_unsorted()`（`freq_table.c:118`，
**线性扫描 + optimal/suboptimal 双候选**）之间分流。
所以"OPP 表写成有序的"是一条真实的性能优化，不只是美观问题。

### 3.4 `cpufreq_frequency_table_verify()`：会悄悄改 `policy->max`

`freq_table.c:69-102`。这个函数的名字叫 "verify"，但它**会写**：

```c
	if (!found) {
		policy->max = next_larger;
		cpufreq_verify_within_cpu_limits(policy);
	}
```

逻辑是：在 `[min, max]` 区间内一个可用频率都没有时（`:93`），
把 `policy->max` **抬高**到"大于 max 的最小可用频率"（`next_larger`，`:89-90`）。

⇒ 用户可观察到的后果：`scaling_max_freq` 写进去 500000，读回来变成 600000
（因为表里最低档是 600MHz）。**没有任何日志说明发生了这件事**——
只有两条 `pr_debug`（`:76-77` 请求前、`:98-99` 校验后）。07 篇要把
"写 sysfs 后读回来不一样"这条症状指到这里。

附一条：`cpufreq_generic_frequency_table_verify()`（`:109-115`）是给
`.verify` 用的一行包装，它是**全框架唯一会返回 `-ENODEV` 的校验路径**——
驱动填了这个函数但忘了设 `policy->freq_table`（`:111-112`）。

### 3.5 `scaling_available_frequencies` 是这里生的

`freq_table.c:265-297` 用一个宏生成两个 sysfs 属性：

| 属性文件 | show 函数 | 传给 `show_available_freqs()` 的 `show_boost` |
|---|---|---|
| `scaling_available_frequencies` | `freq_table.c:273` | **false** |
| `scaling_boost_frequencies` | `freq_table.c:285` | **true** |

但这两个属性**不是默认挂上的**。`cpufreq_generic_attr[]`（`freq_table.c:293-296`）
里**只有前一个**：

```c
struct freq_attr *cpufreq_generic_attr[] = {
	&cpufreq_freq_attr_scaling_available_freqs,
	NULL,
};
```

⇒ 所以驱动分三种做法：

| 驱动 | `.attr` 指向 | 有 `scaling_boost_frequencies` 吗 |
|---|---|---|
| `scmi-cpufreq.c:294` | `cpufreq_generic_attr` | ✗ **永远没有** |
| `qcom-cpufreq-hw.c:662` | 自己的 `qcom_cpufreq_hw_attr`（`:642-646`） | ✅ **无条件有** |
| `cpufreq-dt.c:187` | 自己的 `cpufreq_dt_attr`（`:39-43`） | **看 OPP 表**（见下） |

`cpufreq_dt_attr` 的第二个槽位是**故意留空的**：

```c
static struct freq_attr *cpufreq_dt_attr[] = {
	&cpufreq_freq_attr_scaling_available_freqs,
	NULL,   /* Extra space for boost-attr if required */
	NULL,
};
```

它在 `cpufreq-dt.c:143` 被运行期填上，条件是 `policy_has_boost_freq(policy)`
（`cpufreq-dt.c:138`）。所以同一份 `cpufreq-dt` 驱动在两块板子上会长出不同的
sysfs 文件集 —— 差别只在 DTS 的 OPP 表里有没有 `turbo-mode`。这条链在 §5.6 讲全。

⚠️ 这里有个**真实的隐患**：`cpufreq_dt_attr` 是一个 **static 全局数组**，
被 per-policy 的 `cpufreq_init()` 修改。大小核平台上小核先 init（没有 turbo）、
大核后 init（有 turbo），那个槽位会被大核填上，**结果小核的 policy 目录里也会
出现 `scaling_boost_frequencies`**（内容为空）。这一条记进 03 §C2 之外的补充债，
99 §5.3 登记为本篇自己的判断。

---

## §4 cpufreq_stats：290 行，四个 sysfs 文件

### 4.1 它是什么

一个**纯观测**框架：不参与任何决策，只统计"在每个频率上待了多久"和
"从哪个频率跳到哪个频率跳了几次"。

### 4.2 挂载点：三个，全在核心里

| 函数 | 位置 | 被核心什么时候调 |
|---|---|---|
| `cpufreq_stats_create_table` | `cpufreq_stats.c:212` | policy 创建（03 §A2） |
| `cpufreq_stats_free_table` | `cpufreq_stats.c:196` | policy 释放（03 §A7） |
| `cpufreq_stats_record_transition` | `cpufreq_stats.c:266` | **每次频率真的变了**（03 §A5.2 的 `:2157` 和 §A6.4） |

第三个的两个调用点很重要：`cpufreq_driver_fast_switch()` 里有一个，
`cpufreq_notify_transition()` 里有一个。这意味着**快慢两条路都记账**——
这是 stats 值得信任的原因，也是 03 §A5.2 那条"fast_switch 有 stats 但没有
notifier"的另一面。

### 4.3 一次 kzalloc 分三块

`cpufreq_stats.c:227-244` 是内核里典型的"一块内存三个数组"写法：

```c
	alloc_size = count * sizeof(int) + count * sizeof(u64);
	alloc_size += count * count * sizeof(int);
	/* Allocate memory for time_in_state/freq_table/trans_table in one go */
	stats->time_in_state = kzalloc(alloc_size, GFP_KERNEL);
```

然后 `:240` 和 `:242` 两行做指针切分：`freq_table` 紧跟在 `time_in_state` 后面，
`trans_table` 紧跟在 `freq_table` 后面。

注意 `count * count`（`:233`）：**转移矩阵是 N×N 的**。
一张 20 档的 OPP 表要 400 个 int，即 1.6 KB。这个二次增长是 `trans_table`
的 sysfs 输出会撞 `PAGE_SIZE` 的原因：

```c
	if (len >= PAGE_SIZE - 1) {
		pr_warn_once("cpufreq transition table exceeds PAGE_SIZE. Disabling\n");
		return -EFBIG;
	}
```

`cpufreq_stats.c:167-170`。这是 cpufreq 里**唯一一条 `pr_warn_once`**，
而且它带 `-EFBIG` 返回码 —— `cat trans_table` 会得到 "File too large"。
07 篇要收这一条。

### 4.4 "延迟清零"：为什么 reset 不是当场清

`struct cpufreq_stats`（`cpufreq_stats.c:15-28`）末尾有两个字段带注释
`/* Deferred reset */`（`:25`）：`reset_pending`（`:26`）和 `reset_time`（`:27`）。

`store_reset()`（`cpufreq_stats.c:103`）**不清任何计数**，只做两件事：

1. `WRITE_ONCE(stats->reset_time, local_clock())`（`:112`）
2. `WRITE_ONCE(stats->reset_pending, 1)`（`:118`），中间隔一个内存屏障（注释 `:114-117`）

真正的清零发生在**下一个读者或下一次频率转移**：

| 谁 | 位置 | 看到 `reset_pending` 时做什么 |
|---|---|---|
| `show_total_trans` | `:62` | 直接返回 **0**（不清表） |
| `show_time_in_state` | `:72` · `:85` | 用 `local_clock() - reset_time` 算"复位以来"的时间（不清表） |
| `show_trans_table` | `:127` | 同上，按"复位后"口径输出（不清表） |
| `cpufreq_stats_record_transition` | `:275-276` | **真的调 `cpufreq_stats_reset_table()`** |

这就是 03 §B5.3 里那个"没有配对写屏障"的第三对屏障所在。
设计意图很清楚：**清零动作要在持有 policy 的上下文里做，而 sysfs 读不持有**。
所以读者只"假装"看到清零后的结果，真清零推迟到写侧。

⚠️ 一个真实的可观测后果：**`echo 1 > reset` 之后 `total_trans` 立刻读到 0，
但如果 CPU 频率一直不变，`trans_table` 里的旧数据会一直躺在内存里**——
它只是不被显示。这不是 bug，但排查时容易误判"reset 没生效"。

---

## §5 boost 框架：一个全局开关 + 一个驱动回调

### 5.1 它是什么

允许把频率表里标了 `CPUFREQ_BOOST_FREQ` 的那几档**整体启用/禁用**的机制。
它是 cpufreq 里**唯一一个全局（非 per-policy）的 sysfs 开关**。

### 5.2 挂载点

| 环节 | 位置 |
|---|---|
| 驱动注册时创建 sysfs 文件 | `drivers/cpufreq/cpufreq.c:2891-2895`（失败则 `goto err_boost_unreg`，`:2908`） |
| 驱动注销时删除 | `:2932-2935` 与 `:2966` |
| 用户写 sysfs | `store_boost`（`:615`）→ `cpufreq_boost_trigger_state`（`:624`） |

`define_one_global_rw(boost)`（`:635`）—— `global` 这个词就是本节的关键：
这个文件挂在 `/sys/devices/system/cpu/cpufreq/boost`，**不在任何 policy 目录下**。
大小核平台上你**没法只给大核开 boost**。

### 5.3 三条路，看驱动填了什么

| 驱动的做法 | `->set_boost` 是什么 | 后果 |
|---|---|---|
| 什么都不做 | NULL | `cpufreq_boost_supported()`（`:2777-2779`）返回 false ⇒ **sysfs 里没有 `boost` 文件** |
| 调 `cpufreq_enable_boost_support()`（`:2800`） | 核心的 `cpufreq_boost_set_sw`（`:2808` 安装） | 走软件路：改 policy 的 max 限值 |
| 自己填一个 | 驱动自己的函数 | 走硬件路：驱动直接跟硬件谈 |

`cpufreq_boost_supported()` 的实现就一行：

```c
static bool cpufreq_boost_supported(void)
{
	return cpufreq_driver->set_boost;
}
```

（`:2777-2779`）⇒ **"支持 boost"的定义就是"填了这个回调"**，
不涉及硬件能力，也不看频率表里有没有 boost 档。

### 5.4 软件路：`cpufreq_boost_set_sw` 做了什么

`drivers/cpufreq/cpufreq.c:2721`。它的核心动作是**对每个 policy 重跑一次
freq_qos 更新**：

```c
		freq_qos_update_request(policy->max_freq_req, policy->max);
```

（`:2734`）⇒ 这条路完全复用了 03 §B3 那条 freq_qos 链，**没有新机制**。
`cpufreq_boost_enabled()` 一变，`cpufreq_frequency_table_cpuinfo()`
（`freq_table.c:32`）重新扫表时就会把 boost 档算进 `max_freq`：

```c
		if (!cpufreq_boost_enabled()
		    && (pos->flags & CPUFREQ_BOOST_FREQ))
			continue;
```

（`freq_table.c:43-45`）这个 `continue` 是 boost 档"消失"的唯一实现——
关掉 boost 时，那几档在算 `cpuinfo.max_freq` 时被跳过。

### 5.5 `cpufreq_boost_trigger_state`：带回滚的开关

`drivers/cpufreq/cpufreq.c:2741`。四个要点：

| 行 | 内容 |
|---|---|
| `:2747` | 值没变就直接返回 0（**幂等**） |
| `:2751` | 先改全局状态 `cpufreq_driver->boost_enabled` |
| `:2754` | `cpus_read_lock()` —— 和注册路径（03 §A1.3 的 `:2877`）用同一把 |
| `:2756` | 调 `->set_boost` |
| `:2768` | 失败时**把状态改回去**，然后返回错误 |

`:2751` 在 `:2756` 之前是有意的：`cpufreq_boost_set_sw` 内部会调
`cpufreq_boost_enabled()`，必须先看到新值。这是"先改状态再执行动作、
失败回滚"的标准写法，和 03 §A8 里 suspend/resume **只 `pr_err` 不回滚**
形成对比 —— 同一个文件里两种错误处理哲学。

### 5.6 arm64 上会碰到吗：会，但取决于 DTS 里有没有一个属性

```bash
grep -rn '\.set_boost\|cpufreq_enable_boost_support' drivers/cpufreq/*.c
```

⇒ 全树只有五个驱动碰这套机制。裁掉 x86/ACPI 三个（`acpi-cpufreq` · `amd-pstate`
· `cppc_cpufreq`）和 powerpc 一个（`powernv-cpufreq.c:1143`）之后，
**arm64 上有两个**，而且**都不是直接填 `.set_boost`，而是调
`cpufreq_enable_boost_support()` 走软件路**：

| 驱动 | 调用点 | 条件 | 失败怎么办 |
|---|---|---|---|
| `cpufreq-dt.c` | `:140` | `policy_has_boost_freq(policy)`（`:138`） | `goto out_clk_put`（`:142`）—— **整个 policy init 失败** |
| `qcom-cpufreq-hw.c` | `:597` | 同上（`:596`） | 只 `dev_warn`（`:599`），**继续往下走** |

同一件事两种错误处理哲学。`cpufreq-dt` 认为"用户声明了 turbo 却拿不到 boost
机制"是致命的；qcom 认为只是少个功能。⚠️ `scmi-cpufreq.c` **完全不涉及 boost**
（grep 该文件零命中）。

**那 `policy_has_boost_freq()` 什么时候为真？** `freq_table.c:17-29` 只是扫表看
`CPUFREQ_BOOST_FREQ` 这个 flag。而这个 flag 在 arm64 上**只有一个来源**：

```
DTS: opp-table 里某个 opp 节点写了 turbo-mode
  → drivers/opp/of.c:945    new_opp->turbo = of_property_read_bool(np, "turbo-mode")
  → drivers/opp/cpu.c:69-71 dev_pm_opp_is_turbo(opp) ⇒ flags = CPUFREQ_BOOST_FREQ
  → freq_table.c:25         policy_has_boost_freq() 返回 true
  → cpufreq-dt.c:140        cpufreq_enable_boost_support()
  → drivers/cpufreq/cpufreq.c:2808          ->set_boost = cpufreq_boost_set_sw
  → drivers/cpufreq/cpufreq.c:2891-2895     创建 /sys/devices/system/cpu/cpufreq/boost
```

**所以结论是：arm64 上 `boost` 这个文件在不在，取决于 DTS 的 OPP 表里
有没有一个 `turbo-mode` 属性。** 这条链跨了 cpufreq / OPP / DT 三层，
06 篇会把 `turbo-mode` 收进属性表。

`qcom-cpufreq-hw.c` 是个例外：它不靠 DTS，而是**自己在读硬件 LUT 时判定**
最后一档是 boost（`:274` 设 flag，注释 `:267-270` 说"只把可能是 boost 的最后
一个频率当成 boost"）。所以 qcom 平台无论 DTS 怎么写都会有 boost 档。

最后一个反直觉的点：**boost 关闭时那几档并没有从
`scaling_boost_frequencies` 里消失**。`show_available_freqs()` 的判据是
`show_boost ^ (pos->flags & CPUFREQ_BOOST_FREQ)`（`freq_table.c:254`，
注释 `:246-253` 把四种组合列全了）—— 它只看 flag，**不看 `cpufreq_boost_enabled()`**。
⇒ 关掉 boost 后，`scaling_boost_frequencies` 照样列出那几档，只是
`cpuinfo_max_freq` 降下来了（§5.4 那个 `continue`）。这是 07 篇"看着有这个频率
却调不上去"的答案。

---

## §6 驱动家族：按"谁真正改频率"分三类

### 6.1 分类轴

驱动目录有 **89 个 `.c` / 39396 行**，不可能逐个读。但它们只有三种形态，
分界线是**"改频率"这个动作最终落到哪里**：

| 类 | 代表 | `->target_index` 里做什么 | `->fast_switch` | arm64 |
|---|---|---|---|---|
| **本地时钟树类** | `cpufreq-dt.c` | 调 OPP / clk / regulator，**内核自己操作寄存器** | ✗ 不能（要调 regulator，会睡） | 最常见 |
| **固件类** | `scmi-cpufreq.c` | 发一条消息给固件（SCMI / SCPI / BPMP） | ✅ 有（固件提供无阻塞通道） | 服务器 / 高端 SoC |
| **硬件自治类** | `qcom-cpufreq-hw.c` | 往一个寄存器写一个 index，硬件自己完成 | ✅ 有（一次 writel） | 高通平台 |

这条轴同时解释了 03 §B4 那个问题：**为什么 `fast_switch` 不是所有驱动都能实现？**
因为第一类必须调 `regulator_set_voltage()`，那是个会睡的调用。
第二、三类不碰 regulator，所以能在 `rq->lock` 下完成。

### 6.2 三张 ops 表的逐行对照

**(a) `cpufreq-dt.c:175-189`**

```c
	.flags = CPUFREQ_NEED_INITIAL_FREQ_CHECK |
		 CPUFREQ_IS_COOLING_DEV,
	.verify = cpufreq_generic_frequency_table_verify,
	.target_index = set_target,
	.get = cpufreq_generic_get,
```

后面依次是 `.init`（`:181`）、`.exit`（`:182`）、`.online`（`:183`）、
`.offline`（`:184`）、`.register_em = cpufreq_register_em_with_opp`（`:185`）、
`.name = "cpufreq-dt"`（`:186`）、`.attr`（`:187`）、
**`.suspend = cpufreq_generic_suspend`**（`:188`）。

三个要点：

1. **没有 `.fast_switch`** —— 见 §6.1。
2. **是三个驱动里唯一填了 `.suspend` 的**。`cpufreq_generic_suspend()` 会把频率
   切到 `policy->suspend_freq`，而那个值来自 OPP 的 `opp-suspend` 属性
   （`cpufreq-dt.c:133`）。这是 03 §A8 里 `->suspend()` 那个回调在 arm64 上的
   **唯一实际使用者**。
3. `.target_index = set_target` 的实现只有一行（`cpufreq-dt.c:57-63`）：
   ```c
   	return dev_pm_opp_set_rate(priv->cpu_dev, freq * 1000);
   ```
   ⇒ **cpufreq-dt 本身不碰 clk 也不碰 regulator**，全部委托给 OPP 框架。
   §7.3 会讲这条委托线。

**(b) `scmi-cpufreq.c:288-301`**

`.name = "scmi"`（`:289`）、`.flags`（`:290-292`，三个标志：
`CPUFREQ_HAVE_GOVERNOR_PER_POLICY | CPUFREQ_NEED_INITIAL_FREQ_CHECK |
CPUFREQ_IS_COOLING_DEV`）、`.verify`（`:293`）、
`.attr = cpufreq_generic_attr`（`:294`）、`.target_index`（`:295`）、
**`.fast_switch = scmi_cpufreq_fast_switch`**（`:296`，实现在 `:68`）、
`.get`（`:297`）、`.init`（`:298`）、`.exit`（`:299`）、
`.register_em = scmi_cpufreq_register_em`（`:300`）。

和 (a) 的三处关键差异：多了 `CPUFREQ_HAVE_GOVERNOR_PER_POLICY`
（§2.3 讲过它的后果）、多了 `.fast_switch`、**`.register_em` 不是通用版而是自己写的**
（§7.2 讲）。

**(c) `qcom-cpufreq-hw.c:648-663`**

`.flags`（`:649-651`，和 (b) 同三个）、`.verify`（`:652`）、
`.target_index`（`:653`）、`.get`（`:654`）、`.init`（`:655`）、`.exit`（`:656`）、
`.online`（`:657`）、`.offline`（`:658`）、
`.register_em = cpufreq_register_em_with_opp`（`:659`）、
`.fast_switch`（`:660`）、`.name`（`:661`）、`.attr`（`:662`）、
**`.ready = qcom_cpufreq_ready`**（`:663`）。

`.ready` 是**全框架最少见的回调**，核心只在一处调它（03 §A2 之后，
`drivers/cpufreq/cpufreq.c:1542-1543`，注释 `:1541` 写着
"Callback for handling stuff after policy is ready"）。qcom 用它做一件事
（`qcom-cpufreq-hw.c:634-640`）：

```c
	if (data->throttle_irq >= 0)
		enable_irq(data->throttle_irq);
```

⇒ **必须等 policy 完全就绪才敢开限频中断**，否则中断处理里访问 `policy` 会踩空。
这是"为什么需要 `.ready` 而不能在 `.init` 里做"的完整答案。

### 6.3 `cpufreq-dt` 的两段式：谁创建那个 platform_device？

这是 cpufreq-dt 最容易困惑的地方：**DTS 里没有 `compatible = "cpufreq-dt"`
这样的节点**，那设备是谁造的？

答案是一个专门的 213 行小文件：`cpufreq-dt-platdev.c`。它的 `__init` 函数
（`:186-212`）做的事：

```c
	match = of_match_node(allowlist, np);
	if (match) {
		data = match->data;
		goto create_pdev;
	}

	if (cpu0_node_has_opp_v2_prop() && !of_match_node(blocklist, np))
		goto create_pdev;
```

注意它 match 的是 `of_find_node_by_path("/")`（`:188`）——**根节点的
`compatible`，也就是整机型号**。两条路：

| 路 | 判据 | 用途 |
|---|---|---|
| allowlist（`:18`） | 整机 compatible 在白名单里 | 老平台，OPP 用 v1 绑定 |
| 默认路（`:201`） | CPU0 有 `operating-points-v2` **且**整机不在黑名单里 | arm64 的主流路 |

然后 `platform_device_register_data(NULL, "cpufreq-dt", ...)`（`:209-211`），
`core_initcall`（`:213`）。

⇒ **blocklist（`:103`）的存在意义**：某块板子的 CPU0 确实有 `operating-points-v2`，
但它该用别的驱动（比如 `qcom-cpufreq-hw`）。不进黑名单就会两个驱动抢注册，
后来那个拿到 03 §A1.3 的 `-EEXIST`。06/07 篇要收这一条："我的板子起了
`cpufreq-dt` 但我想用厂商驱动" ⇒ 去看 blocklist。

### 6.4 `qcom-cpufreq-hw`：硬件自治 + 限频回报

这个驱动值得单独看，因为它是**唯一一个"频率变化不由内核发起"的代表**。
硬件（EPSS/OSM）自己会因为温度或电流限制把频率压下来，内核只能被动知道。

机制叫 LMH（Limits Management Hardware），四个函数：

| 函数 | 位置 | 角色 |
|---|---|---|
| `qcom_lmh_dcvs_handle_irq` | `:376` | 中断来了：**关中断、开轮询** |
| `qcom_lmh_dcvs_poll` | `:368` | 轮询工作项，转调 notify |
| `qcom_lmh_dcvs_notify` | `:317` | 干活的 |
| `qcom_lmh_get_throttle_freq` | `:125` | 读寄存器拿到硬件当前限值 |

`qcom_lmh_dcvs_notify()` 的核心两步：

1. 把硬件限值对齐到 OPP 表（`:331` 先 floor，`-ERANGE` 时 `:333` 改 ceil），
   注释 `:325-328` 说明为什么要"normalize"；
2. `arch_update_thermal_pressure(policy->related_cpus, throttled_freq)`
   （`:344`）—— **把限频这件事告诉调度器**。

然后是一个"中断/轮询自动切换"的收尾（`:354-362`）：限值恢复到 cpufreq 要求
之上就 `enable_irq()` 回到中断模式（`:359`），否则 10ms 后再轮询一次（`:361-362`）。
注释 `:354-357` 把这个判据写清楚了。

这条路**完全绕过 cpufreq 核心**：既不走 notifier，也不改 `policy->cur`。
调度器通过 thermal pressure 知道"这个 CPU 现在能力下降了"，但
`cat scaling_cur_freq` 读到的还是驱动 `->get()` 的结果。
07 篇排查"频率上不去但 cpufreq 说一切正常"要走到这里。

### 6.5 nvmem 家族：不是 cpufreq 驱动，是 OPP 预处理器

`sun50i-cpufreq-nvmem.c` · `qcom-cpufreq-nvmem.c` · `ti-cpufreq.c` ·
`imx-cpufreq-dt.c` 这四个文件住在 `drivers/cpufreq/`，但**它们不注册
`cpufreq_driver`**。

它们干的事是：从 efuse（nvmem）里读出这颗片子的"速度档位"，然后告诉 OPP 框架
"这张表里只有匹配这个档位的 OPP 才算有效"，最后再让 `cpufreq-dt` 上场。
三种告知方式：

| 文件 | API | 位置 |
|---|---|---|
| `sun50i-cpufreq-nvmem.c` | `dev_pm_opp_set_prop_name()`（按名字选一组电压属性） | `:113` |
| `qcom-cpufreq-nvmem.c` | `dev_pm_opp_set_config()`（一次设多项配置） | `:349` |
| `imx-cpufreq-dt.c` | `dev_pm_opp_set_supported_hw()`（位掩码匹配 `opp-supported-hw`） | `:156` |

共同的起点都是 `nvmem_cell_read()`（`sun50i-cpufreq-nvmem.c:63` ·
`qcom-cpufreq-nvmem.c:191`）或 `nvmem_cell_read_u32()`（`imx-cpufreq-dt.c:116`）。

⇒ **这是 06 篇的重要背景**：同一份 DTS 的 OPP 表，在两颗同型号但不同 bin 的芯片上
会解析出不同的频率表。看到 `scaling_available_frequencies` 比 DTS 里的 opp 节点少，
先查是不是这一层过滤掉了。

### 6.6 一个跨驱动的普查：`CPUFREQ_NEED_INITIAL_FREQ_CHECK`

```bash
grep -rl 'CPUFREQ_NEED_INITIAL_FREQ_CHECK' drivers/cpufreq/*.c
```

⇒ **26 个文件命中，去掉核心自己（`cpufreq.c`）后是 25 个驱动**，包括
`cpufreq-dt.c:176`、`scmi-cpufreq.c:291`、`qcom-cpufreq-hw.c:649` 三个
arm64 主力。

这条普查很重要，因为它**推翻了一个直觉**：这个标志会让核心在
`cpufreq_online()` 里走一段"启动频率不在表里就 BUG_ON"的代码
（03 §C1.1）。既然 25 个驱动都设它，**那条死机路径在 arm64 上是真实可达的**，
不是理论风险。03 §A2.5 和 §C1.1 都按这个事实写。

---

## §7 外部集成：thermal / Energy Model / OPP

这三个不是 cpufreq 的子框架，是**三个独立子系统与 cpufreq 的接缝**。
放在本篇是因为它们和 §1–§6 一样"挂在时间线上但不参与主流程"。

### 7.1 thermal cooling：一个标志 + 两行代码

**接缝只有两处**，都在 policy 的创建/销毁路径上：

```c
	/* Register cpufreq cooling only for a new policy */
	if (new_policy && cpufreq_thermal_control_enabled(cpufreq_driver))
		policy->cdev = of_cpufreq_cooling_register(policy);
```

`drivers/cpufreq/cpufreq.c:1545-1547`，对应的注销在 `:1697-1698`（03 §A7.3）。

`cpufreq_thermal_control_enabled()` 是个两条件的 inline
（`include/linux/cpufreq.h:460-463`）：

```c
	return IS_ENABLED(CONFIG_CPU_THERMAL) &&
		(drv->flags & CPUFREQ_IS_COOLING_DEV);
```

两个条件都要满足。`CPUFREQ_IS_COOLING_DEV`（`include/linux/cpufreq.h:421`，
注释 `:417-420` 写着"Set by drivers that want the core to automatically register
the cpufreq driver as a thermal cooling device"）**三个 arm64 代表驱动全都设了**
（`cpufreq-dt.c:177` · `scmi-cpufreq.c:292` · `qcom-cpufreq-hw.c:651`）。

`new_policy &&` 这个前缀值得注意：**CPU 热插拔回来重用旧 policy 时不会重复注册
cooling device**。这和 03 §A2 里 `new_policy` 那个分支是同一个判据。

⇒ thermal 侧怎么用它：注册成 cooling device 后，thermal governor 通过
`freq_qos` 加一个 `FREQ_QOS_MAX` 请求（03 §B3.3）。**所以 thermal 降频和用户写
`scaling_max_freq` 走的是完全同一条路，只是两个不同的 requester，取 min。**

### 7.2 Energy Model：`->register_em` 的两种实现

**挂载点**：`drivers/cpufreq/cpufreq.c:1526-1527`。它的注释（`:1516-1524`）
把两条时序约束都写明白了：

- 必须在 `sched_cpufreq_governor_change()` 之前（那会重建 sched domain，
  而重建时要读 EM）；
- 必须在注册 cooling device 之前。

⇒ 所以这三件事在 `cpufreq_online()` 里的顺序是死的：
**EM（`:1527`）→ governor（`:1530`）→ cooling（`:1547`）**。

两种实现：

| 驱动 | `.register_em` | 做法 |
|---|---|---|
| `cpufreq-dt.c:185` · `qcom-cpufreq-hw.c:659` | `cpufreq_register_em_with_opp` | 通用版，一行转调 OPP |
| `scmi-cpufreq.c:300` | `scmi_cpufreq_register_em`（`:262-286`） | 自己写，功率数据来自固件 |

通用版是个 inline（`include/linux/cpufreq.h:1264-1268`）：

```c
static inline void cpufreq_register_em_with_opp(struct cpufreq_policy *policy)
{
	dev_pm_opp_of_register_em(get_cpu_device(policy->cpu),
				  policy->related_cpus);
}
```

注意传的是 `related_cpus` 而不是 `cpus`（02 篇讲三个 cpumask 时说过差别）——
EM 描述的是**硬件性能域**，和"当前在线哪几个"无关。

scmi 版多做三件事：

1. `if (!priv->nr_opp) return;`（`:276`）—— 注释 `:269-275` 解释：
   **多个 policy 可能共享同一个 perf domain**，别的 policy 可能已经代表
   本 policy 的 CPU 注册过了；
2. 判断固件报的功率单位是 mW 还是 µW（`:279-281`）；
3. `em_dev_register_perf_domain()` 传的 mask 是 `priv->opp_shared_cpus`（`:284`），
   **又不是 `related_cpus`** —— 固件的 perf domain 可能比 cpufreq policy 更大。

⇒ 这是本篇最值得记的一个不对称：**cpufreq policy 的边界、EM perf domain
的边界、OPP sharing 的边界，三者可以互不相同**。08 篇会把这组关系单独拎出来讲。

### 7.3 OPP：`cpufreq-dt` 把几乎所有活都委托出去了

`cpufreq-dt.c` 只有 372 行，因为它把工作全交给了 OPP。`dt_cpufreq_early_init()`
（`:191`）里这条委托链：

| 行 | 调用 | 拿到什么 |
|---|---|---|
| `:118` | `clk_get(cpu_dev, NULL)` | CPU 时钟（只为读 `->get()` 和存进 `policy->clk`） |
| `:223` | `dev_pm_opp_set_regulators(cpu_dev, reg_name)` | 告诉 OPP 该操作哪个 regulator（注释 `:218-219`） |
| `:232` | `dev_pm_opp_of_get_sharing_cpus(cpu_dev, priv->cpus)` | **policy 覆盖哪几个 CPU** |
| `:257` | `dev_pm_opp_of_cpumask_add_table(priv->cpus)` | 从 DTS 解析 OPP 表 |
| `:268` | `dev_pm_opp_get_opp_count(cpu_dev)` | 有几档 |
| `:283` | `dev_pm_opp_init_cpufreq_table(cpu_dev, &priv->freq_table)` | **把 OPP 表变成 cpufreq 频率表** |

`:232` 那一行是 arm64 大小核平台"policy 边界从哪来"的答案：
**不是 cpufreq 决定的，是 OPP 表的 `opp-shared` 属性决定的。**
06 篇会把这条属性和 `#clock-cells` 等一起收进属性表。

`:283` 那一行是 §5.6 那条 boost 链的中段：`dev_pm_opp_init_cpufreq_table()`
的实现（`drivers/opp/cpu.c:69-71`）会把 turbo OPP 标成 `CPUFREQ_BOOST_FREQ`。
所以 `cpufreq-dt` 自己一行 boost 代码都没写，boost 档是 OPP 层给的。

⇒ 一句话总结这条接缝：**`cpufreq-dt` 是 OPP 框架的一个 cpufreq 适配器**，
`set_target()`（`:57-63`）只有一行 `dev_pm_opp_set_rate()`，
clk 和 regulator 的实际操作全在 OPP 里。**排查 `cpufreq-dt` 的调频失败，
第一现场是 OPP 和 regulator，不是 cpufreq。**

---

## §8 汇总表

| # | 框架 | 职责 | 挂在 03 时间线的哪一点 | 主要文件 | arm64 |
|---|---|---|---|---|---|
| §1 | governor 家族 | 决定"什么频率" | 模块加载（`drivers/cpufreq/cpufreq.c:2466`）+ policy 创建（03 §A3） | 6 个 governor 文件 | schedutil |
| §2 | dbs 共享层 | 给 ondemand/conservative 提供采样与 tunable | governor 的五个回调全部（`cpufreq_governor.h:160-164`） | `cpufreq_governor.c` · `cpufreq_governor_attr_set.c` | 默认不走 |
| §3 | 频率表框架 | 把一张数组变成可校验、可查找的频率表 | 创建（`freq_table.c:354`）· 校验（`:109`）· 下发（03 §A6.1） | `freq_table.c` | |
| §4 | cpufreq_stats | 纯观测：驻留时间 + 转移矩阵 | 创建/释放/**每次真的换频**（`cpufreq_stats.c:266`） | `cpufreq_stats.c` | |
| §5 | boost 框架 | 全局启用/禁用 turbo 档 | 驱动注册（`drivers/cpufreq/cpufreq.c:2891-2895`）+ 用户写 sysfs | `drivers/cpufreq/cpufreq.c:2721-2819` + `freq_table.c` | 看 DTS 有没有 `turbo-mode` |
| §6 | 驱动家族 | 真正执行调频 | `->target_index` / `->fast_switch`（03 §A6） | 89 个 `.c` | 三条路线 |
| §7 | thermal / EM / OPP | 三个外部子系统的接缝 | EM `drivers/cpufreq/cpufreq.c:1527` → governor `:1530` → cooling `:1547` | `include/linux/cpufreq.h:460` · `:1264` | |

### 8.1 三个最容易踩的跨框架陷阱

| 症状 | 根因在哪一节 |
|---|---|
| `scaling_max_freq` 写 500000 读回 600000 | §3.4 —— `verify` 会悄悄抬高 `max`，无日志 |
| `echo 1 > reset` 后 `trans_table` 里旧数据还在 | §4.4 —— 延迟清零，读者只是"假装"看到清零 |
| `scaling_boost_frequencies` 列出了某个频率但调不上去 | §5.6 末段 —— 那个文件只看 flag，不看 boost 开关 |
| 板子起了 `cpufreq-dt` 但我想用厂商驱动 | §6.3 —— 去 `cpufreq-dt-platdev.c:103` 的 blocklist 加一行 |
| 频率上不去，cpufreq 却报告一切正常 | §6.4 —— 硬件自治限频只走 thermal pressure，不改 `policy->cur` |
| `scaling_available_frequencies` 比 DTS 里的 opp 节点少 | §6.5 —— nvmem 档位过滤，或 §3.3 的查重把整表拒了 |

### 8.2 去哪一篇

| 你想要的 | 去处 |
|---|---|
| 这些框架挂上去之后核心怎么走 | `03-internals.md` Part A |
| 锁、屏障、freq_qos 的横切机制 | `03-internals.md` Part B |
| 写一个 cpufreq 驱动的骨架 | `05-usage.md` |
| `turbo-mode` / `opp-shared` / `operating-points-v2` 怎么写 | `06-devicetree.md` |
| 上面那六条症状怎么定位 | `07-debug.md` |
| policy / perf domain / OPP sharing 三个边界的区别 | `08-feynman.md` |
