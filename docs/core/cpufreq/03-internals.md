---
title: 内部实现
---

# cpufreq 03 —— 实现剖析

本篇是逐行剖析。**只承诺一条主轴**，其余内容显式分区：

| 分区 | 轴 | 章节号 |
|---|---|---|
| **Part A** | **时间线**：从驱动注册到 policy 拆除，按"谁先发生"排 | `§A1`–`§A8` |
| **Part B** | **横切机制**：锁、极限传递、通知、内存序 —— 跨越多个时间点 | `§B1`–`§B7` |
| **Part C** | **评价**：设计债与好的设计 | `§C1`–`§C3` |

引用本篇时请带 A/B/C 前缀（如 `03 §A6`），这样引用自带"在哪条轴上"的信息。

---

## §0 导航

### 0.1 分层图（本篇要走的就是这张图上的每一条竖线）

```
              ┌──────────────────────────────────────────────┐
   用户态     │ /sys/devices/system/cpu/cpufreq/policyN/...   │
              └───────────────┬──────────────────────────────┘
                              │ store_scaling_max_freq 等
              ┌───────────────▼──────────────────────────────┐
   极限聚合   │ freq_qos（policy->constraints）               │  ← §B3
              │  ├─ sysfs 的 min/max 请求                    │
              │  └─ thermal / 其他内核用户的请求              │
              └───────────────┬──────────────────────────────┘
                              │ 通知 → cpufreq_notifier_min/max
              ┌───────────────▼──────────────────────────────┐
   唯一漏斗   │ cpufreq_set_policy()                         │  ← §B2
              │  verify() → 写 policy->min/max → governor    │
              └───────────────┬──────────────────────────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
 ┌──────▼──────┐    ┌─────────▼─────────┐   ┌────────▼────────┐
 │ 采样式       │    │ schedutil          │   │ setpolicy 驱动   │
 │ ondemand /   │    │ (kernel/sched/)    │   │ （硬件自治）      │
 │ conservative │    │                    │   │ arm64 不常见      │
 │  §A4         │    │  §A5               │   │  §B2 短路        │
 └──────┬───────┘    └────┬──────────┬────┘   └─────────────────┘
        │                 │          │
        │  路 C           │  路 B    │  路 A
        └────────┬────────┘          │
                 │                   │
        ┌────────▼────────┐  ┌───────▼──────────────┐
        │ __cpufreq_      │  │ cpufreq_driver_      │
        │  driver_target  │  │  fast_switch         │
        │  → __target_    │  │  （不能睡、不发通知） │
        │     index  §A6  │  │      §B4             │
        └────────┬────────┘  └───────┬──────────────┘
                 │                   │
        ┌────────▼───────────────────▼──────────────┐
        │ 驱动回调 ->target_index / ->fast_switch    │
        └───────────────────────────────────────────┘
```

### 0.2 生命周期时间线

```
core_initcall(cpufreq_core_init)          建 /sys/.../cpu/cpufreq 目录  §A1
        │                                 记下编译期默认 governor 名字
        ▼
platform_driver probe → cpufreq_register_driver()                      §A1
        │  校验回调组合 → 装全局单例 → 开 FIE 静态键
        │  → subsys_interface_register() 触发每颗 CPU 的 add_dev
        ▼
cpufreq_online(cpu)                                                    §A2
        │  policy_alloc → ->init() → 表排序 → 三个 mask 填好
        │  → freq_qos 请求建好 → sysfs 建好 → register_em()
        ▼
cpufreq_init_policy() → cpufreq_set_policy()                           §A2/§A3
        │  挑 governor → ->init() → ->start()
        ▼
====================== 稳态运行 ======================
   采样周期 §A4        schedutil 更新 §A5        sysfs 写入 §B3
        └──────────────┬───────────────┬────────────┘
                       ▼               ▼
                  路 C §A6         路 A/B §A5
====================================================
        │
        ▼  CPU 下线 / 驱动卸载
cpufreq_offline(cpu) → __cpufreq_offline()                             §A7
        │  stop governor → 清 mask →（最后一颗）exit governor → ->offline()
        ▼
cpufreq_remove_dev()  → 拆 cooling → ->exit() → policy_free()          §A7
        ▼
cpufreq_unregister_driver()                                            §A7
```

### 0.3 哪些内容不在本篇

| 你想找的 | 去处 |
|---|---|
| 函数签名、参数、返回码清单 | `01-api.md` |
| 结构体字段逐个解释 | `02-datastructures.md` |
| governor 家族分类、驱动家族分类 | `04-extensions.md` |
| 可抄的驱动 / governor 骨架 | `05-usage.md` |
| DTS 属性谁消费 | `06-devicetree.md` |
| tracepoint、sysfs、错误码分诊 | `07-debug.md` |
| "为什么要分 policy 和 CPU 两层"这类大白话 | `08-feynman.md` |
| 每条论断的证据等级 | `99-evidence.md` |

本篇正文里所有 `文件:行号` 都指 6.1.158 原树。核心文件是
`drivers/cpufreq/cpufreq.c`（2998 行），下文凡是只写 `:NNN` 的地方都指它。

---

# Part A · 时间线

## §A1 驱动注册

### A1.1 先有目录，再有驱动

`cpufreq_core_init()`（`drivers/cpufreq/cpufreq.c:2981`）是 `core_initcall`（`:2998`），
比任何 platform 驱动都早。它只干三件事：

```c
	if (cpufreq_disabled())
		return -ENODEV;

	cpufreq_global_kobject = kobject_create_and_add("cpufreq", &cpu_subsys.dev_root->kobj);
	BUG_ON(!cpufreq_global_kobject);

	if (!strlen(default_governor))
		strncpy(default_governor, cpufreq_default_governor()->name, CPUFREQ_NAME_LEN);
```

1. **建目录**：`/sys/devices/system/cpu/cpufreq/`（`:2988`）。后面每个
   `policyN` 都挂在它下面 —— 这就是 02 篇说的"policy 的 kobject 父节点是它，
   不是某个 CPU 设备"。
2. **`BUG_ON(!cpufreq_global_kobject)`（`:2989`）**：建不出这个目录直接死机。
   这是核心三个 `BUG_ON` 之一，见 §C1。
3. **确定默认 governor 的名字**（`:2991-2992`）：只有内核命令行
   （`cpufreq.default_governor=`，`module_param_string` 在 `:2997`）**没给**时，
   才去问 `cpufreq_default_governor()`。

注意顺序：**命令行优先，编译期默认垫底**。而 `cpufreq_default_governor()`
本身是编译期的（六个 governor 文件各一份定义，Kconfig 保证只编进一个，
01 §5.1 已述）。所以"默认 governor"实际有三层：
**命令行 > 编译期 Kconfig > （启动失败时）`cpufreq_fallback_governor()`**。

`module_param(off, int, 0444)`（`:2996`）是另一条早期开关：`cpufreq.off=1`
会让 `cpufreq_disabled()` 恒真，整个子系统不起来。

### A1.2 `cpufreq_register_driver()` 的六道校验

函数体在 `:2850-2952`。**它是全子系统唯一一处对驱动做形式检查的地方**，
一旦通过，后面的路径就假定回调组合是合法的。按顺序：

| # | 位置 | 判据 | 返回 |
|---|---|---|---|
| 0 | `:2856` | `cpufreq_disabled()` | `-ENODEV` |
| 1 | `:2863` | `!get_cpu_device(0)` —— CPU 设备还没注册 | `-EPROBE_DEFER` |
| 2 | `:2865` | `driver_data` / `verify` / `setpolicy` 三者的存在性 | `-EINVAL` |
| 3 | `:2866-2867` | `setpolicy`、`target`、`target_index`、`fast_switch` **一个都没有** | `-EINVAL` |
| 4 | `:2868` | `target` 和 `target_index` **同时**存在 | `-EINVAL` |
| 5 | `:2870` | `get_intermediate` 与 `target_intermediate` **没有成对** | `-EINVAL` |
| 6 | `:2871` | `online` 与 `offline` **没有成对** | `-EINVAL` |

第 5、6 条用的是同一个写法：

```c
	    (!driver_data->get_intermediate != !driver_data->target_intermediate) ||
	    (!driver_data->online != !driver_data->offline))
```

`!x != !y` 是"两个指针的空/非空状态不一致"的紧凑写法 —— 要求**要么都给，要么都不给**。
这比"只检查其中一个"强：单独给 `get_intermediate` 而不给 `target_intermediate`
会在运行期崩，所以在注册时就拦掉。

第 3、4 条合起来的语义值得单独说：**`fast_switch` 不算"下发频率的主回调"，
它只是加速通道**。第 3 条把它算进"至少有一个"，但第 4 条只禁止
`target` + `target_index` 并存 —— 所以 **`target_index` 和 `fast_switch`
是可以、而且经常同时存在的**（02 §3.2 已说过），schedutil 的两条路
（§A5）正好各走一个。

### A1.3 装单例、开静态键、触发遍历

校验过了之后：

```
cpus_read_lock()                                    :2877   ← 挡住 CPU 热插拔
write_lock_irqsave(&cpufreq_driver_lock)             :2879
    if (cpufreq_driver) → -EEXIST                    :2882   ← 全局单例
    cpufreq_driver = driver_data;                    :2885
write_unlock_irqrestore                              :2886

if (driver->setpolicy) driver->flags |= CPUFREQ_CONST_LOOPS;    :2889
if (cpufreq_boost_supported()) create_boost_sysfs_file()        :2891-2895
if (!cpufreq_driver->setpolicy)
        static_branch_enable_cpuslocked(&cpufreq_freq_invariance);   :2902

ret = subsys_interface_register(&cpufreq_interface);             :2906
if (!(driver->flags & CPUFREQ_STICKY) && list_empty(&cpufreq_policy_list))
        ret = -ENODEV;                                          :2912
cpuhp_setup_state_nocalls_cpuslocked(CPUHP_AP_ONLINE_DYN,
        "cpufreq:online", cpuhp_cpufreq_online, cpuhp_cpufreq_offline); :2918
cpus_read_unlock()
```

四个点要注意：

1. **`-EEXIST`（`:2882`）就是"一个内核只能有一个 cpufreq 驱动"的执行点。**
   02 §1.2 从数据结构角度说过（`cpufreq_driver` 是 `static struct cpufreq_driver *`，
   在 `:58`），这里是它的强制点。**异构 SoC 的大小核不能各注册一个驱动**，
   只能一个驱动内部按 policy 分别处理。
2. **`CPUFREQ_CONST_LOOPS` 自动加给 `setpolicy` 驱动（`:2889`）。**
   语义是"频率变了不用调整 `loops_per_jiffy`"—— 因为硬件自治型驱动的频率变化
   核心根本不知道。这一条在 arm64 上基本用不到（`adjust_jiffies()` 在 `:309`，
   只在 `!CONFIG_SMP` 时有实体）。
3. **频率不变性（FIE）静态键只在 `!setpolicy` 时打开（`:2902`）。**
   `cpufreq_supports_freq_invariance()`（`:63`）读的就是这个键。
   它决定 `arch_scale_freq_invariant()` 的返回值，进而决定 schedutil
   用 `cpuinfo.max_freq` 还是 `policy->cur` 做基准（`get_next_freq()`，
   `kernel/sched/cpufreq_schedutil.c:167-168`）。链路见 §B7。
4. **`subsys_interface_register()`（`:2906`）是引爆点**：它会对每个已在线的
   CPU 设备回调 `.add_dev`（`struct subsys_interface cpufreq_interface` 在 `:1873`，
   `.add_dev = cpufreq_add_dev` 在 `:1876`），于是 §A2 立刻开始，
   **在 `cpufreq_register_driver()` 还没返回的时候**。所以 `:2912` 那句
   "`cpufreq_policy_list` 还是空的就返回 `-ENODEV`"能成立 —— 注释原文是
   "if all ->init() calls failed"。

四个回卷标签：`err_if_unreg`（`:2930`，注销 subsys interface）、
`err_boost_unreg`（`:2932`）、`err_null_driver`（`:2936`，把全局指针清回 NULL）、
`out`（`:2940`）。这是核心里回卷写得最完整的一处。

---

## §A2 policy 从无到有

### A2.1 三种入口

`cpufreq_online()`（`:1339`）开头就分三岔（`:1350-1367`）：

| 情形 | 判据 | 动作 |
|---|---|---|
| policy 已存在且**活跃** | `policy && !policy_is_inactive(policy)` | `cpufreq_add_policy_cpu()`（`:1354`）后直接返回 |
| policy 已存在但**空转** | `policy` 存在，mask 空 | `new_policy = false`，取写锁，`policy->cpu = cpu`，**`policy->governor = NULL`**（`:1358-1360`） |
| policy **不存在** | — | `cpufreq_policy_alloc(cpu)`（`:1363`），失败 `-ENOMEM`（`:1365`） |

第二种情形里那句 `policy->governor = NULL` 值得停一下：
**空转的 policy 重新上线时，governor 指针被主动清空**，
而 `policy->last_governor`（一个字符串）留着。原因在 02 §2.3 讲过 ——
governor 模块可能已经卸载了，指针不能信，名字可以。
后面 `cpufreq_init_policy()` 再按名字找回来（§A2.4）。

### A2.2 `cpufreq_policy_alloc()`：一次分配八样东西

`:1206-1288`。按代码顺序：

```
get_cpu_device(cpu)                                  :1209   失败 → NULL
kzalloc(sizeof(*policy))                             :1215
alloc_cpumask_var(&policy->cpus)                     :1219   ← 不清零
zalloc_cpumask_var(&policy->related_cpus)            :1222   ← 清零
zalloc_cpumask_var(&policy->real_cpus)               :1225   ← 清零
init_completion(&policy->kobj_unregister)            :1228
kobject_init_and_add(&policy->kobj, &ktype_cpufreq,
        cpufreq_global_kobject, "policy%u", cpu)     :1229
init_rwsem(&policy->rwsem)                           :1242
freq_constraints_init(&policy->constraints)          :1244
policy->nb_min.notifier_call = cpufreq_notifier_min  :1246
policy->nb_max.notifier_call = cpufreq_notifier_max  :1247
freq_qos_add_notifier(..., FREQ_QOS_MIN, &policy->nb_min)  :1249
freq_qos_add_notifier(..., FREQ_QOS_MAX, &policy->nb_max)  :1257
INIT_LIST_HEAD(&policy->policy_list)                 :1265
spin_lock_init(&policy->transition_lock)             :1266
init_waitqueue_head(&policy->transition_wait)        :1267
INIT_WORK(&policy->update, handle_update)            :1268
policy->cpu = cpu;                                   :1270
```

**`cpus` 用 `alloc_cpumask_var` 而另两个用 `zalloc_`**（`:1219` vs `:1222`/`:1225`）：
不是笔误。`policy->cpus` 在 `cpufreq_online()` 里一定会被整体覆盖
（`:1371` 或 `:1380`，见 A2.3），另两个是逐位 `set` 的，必须从零开始。

`policy%u` 这个名字（`:1230`）里的 `%u` 是**第一次上线的那颗 CPU 号**，
不是"policy 的序号"。所以 `policy0`、`policy4` 这种不连续的编号是正常的 ——
大小核各一个 policy 时就长这样。名字定了之后**再也不改**，
即便后来 `policy->cpu` 换成了别的 CPU（`:1611-1612`）。

六个回卷标签在 `:1273-1287`，顺序和分配严格相反。
`kobject_init_and_add()` 失败时有 `dev_err`（`:1232`），
两个 `freq_qos_add_notifier()` 失败时各有一个 `dev_err`（`:1251`、`:1259`）
—— 这是 policy 创建路径上少见的**普通日志**，其余全是 `pr_debug`。

### A2.3 三个 cpumask 的填充顺序（02 §1.1 承诺在这里讲）

这是最容易看错的一段。分两条路：

**路 1：`->online()` 存在（轻量恢复）**，`:1369-1378`

```c
	if (!new_policy && cpufreq_driver->online) {
		/* Recover policy->cpus using related_cpus mask */
		cpumask_copy(policy->cpus, policy->related_cpus);
		ret = cpufreq_driver->online(policy);
```

`related_cpus` **是这条路上的唯一真相来源**（`:1371`）。
它在上一次下线时没被清（`__cpufreq_offline()` 只清 `policy->cpus`，`:1607`），
所以能拿来恢复。

**路 2：`->init()`（完整初始化）**，`:1379-1404`

```
cpumask_copy(policy->cpus, cpumask_of(cpu));     :1380  ← 先只有自己
cpufreq_driver->init(policy);                    :1386  ← 驱动在这里往 cpus 里加同组 CPU
cpufreq_table_validate_and_sort(policy);         :1398
cpumask_copy(policy->related_cpus, policy->cpus);:1403  ← related = init 完的 cpus
```

**然后两条路合流**：

```
cpumask_and(policy->cpus, policy->cpus, cpu_online_mask);   :1410
```

⇒ 三个 mask 的语义因此完全确定：

| mask | 由谁写定 | 含义 |
|---|---|---|
| `related_cpus` | `:1403`（一次性，来自驱动 `->init()` 填完的 `cpus`） | **这个 policy 名下所有 CPU**，之后不随上下线变化 |
| `cpus` | `:1410` 起持续变化（`:1607` 清位、`cpufreq_add_policy_cpu()` 置位） | **当前在线的成员** |
| `real_cpus` | `:1413-1416` 循环里按 `cpu_present` 置位 | **物理存在的成员**，决定往哪些 CPU 目录建符号链接 |

关键结论：**驱动的 `->init()` 是唯一能决定"哪些 CPU 共享一个 policy"的地方**。
`cpufreq-dt.c` 通过 OPP 表的 `opp-shared` 或 DT 的 `cpu-supply` 关系推出这一组；
`scmi-cpufreq.c` 问固件要。核心自己一个字都不参与。

### A2.4 新 policy 独有的两段

**第一段（`:1412-1455`）—— 建 qos 请求**：

```
for_each_cpu(j, policy->related_cpus) {
        per_cpu(cpufreq_cpu_data, j) = policy;
        add_cpu_dev_symlink(policy, j, get_cpu_device(j));
}                                                          :1413-1416

policy->min_freq_req = kzalloc(2 * sizeof(*policy->min_freq_req), GFP_KERNEL);  :1418
freq_qos_add_request(&policy->constraints, policy->min_freq_req,
                     FREQ_QOS_MIN, FREQ_QOS_MIN_DEFAULT_VALUE);                 :1425
policy->max_freq_req = policy->min_freq_req + 1;                                :1443
freq_qos_add_request(&policy->constraints, policy->max_freq_req,
                     FREQ_QOS_MAX, FREQ_QOS_MAX_DEFAULT_VALUE);                 :1445

blocking_notifier_call_chain(&cpufreq_policy_notifier_list,
                             CPUFREQ_CREATE_POLICY, policy);                    :1453
```

**一次 `kzalloc` 分两个请求**（`:1418` 的 `2 * sizeof`，`:1443` 的 `+ 1`）。
`:1438-1442` 有一段注释专门解释 `max_freq_req` 为什么必须在**这个位置**赋值：
中间的 `freq_qos_add_request()` 会触发通知，通知处理函数
（`cpufreq_notifier_min`）会走到 `refresh_frequency_limits()`，
那时如果 `max_freq_req` 还是野指针就完了。
**这是"为了省一次 kmalloc 而引入的一个顺序约束"**，见 §C2。

**第二段（`:1505-1528`）—— 建 sysfs 与能量模型**：

```
cpufreq_add_dev_interface(policy);                    :1506
if (cpufreq_driver->get && has_target())
        cpufreq_stats_create_table(policy);           :1510
write_lock_irqsave(&cpufreq_driver_lock);
list_add(&policy->policy_list, &cpufreq_policy_list); :1513
write_unlock_irqrestore;
if (cpufreq_driver->register_em)
        cpufreq_driver->register_em(policy);          :1526
```

`:1516-1525` 那段注释给出了 `register_em()` 位置的三条约束：必须在
`sched_cpufreq_governor_change()` **之前**（因为要重建调度域）、必须在
policy 注册进 cooling 框架**之前**、而且要在 sysfs 建好之后。
换句话说 **`register_em` 的位置不是随便挑的，是被三个下游需求夹出来的**。

`cpufreq_stats_create_table()` 有条件（`:1509`）：**驱动没有 `->get()`
或者是 `setpolicy` 型，就没有 `stats/` 目录**。这是 07 篇"为什么我的机器上
没有 `stats/`"的答案。

### A2.5 `CONFIG_CPUFREQ` 最凶的一段：开机频率不在表里

`:1457-1503`。先读一次当前频率：

```c
	if (cpufreq_driver->get && has_target()) {
		policy->cur = cpufreq_driver->get(policy->cpu);
		if (!policy->cur) {
			pr_err("%s: ->get() failed\n", __func__);
			goto out_destroy_policy;      /* -EIO */
		}
	}
```

（`:1457-1464`，返回 `-EIO`）。然后：

```c
	if (cpufreq_driver->flags & CPUFREQ_NEED_INITIAL_FREQ_CHECK) {
		...
		ret = __cpufreq_driver_target(policy, old_freq - 1, CPUFREQ_RELATION_L);
		if (ret) {
			/* ... we can't, -> BUG_ON() */
			BUG_ON(ret);
		}
		pr_info("%s: CPU%d: Running at unlisted initial frequency: %u KHz, changing to: %u KHz\n",
			__func__, policy->cpu, old_freq, policy->cur);
	}
```

（`:1484-1503`，`BUG_ON` 在 `:1499`，`pr_info` 在 `:1500`）

`:1466-1483` 的长注释解释了整件事：**bootloader 可能把 CPU 停在频率表里没有的
某个值上**，而后面所有代码都假定 `policy->cur` 是表里的一项。
所以核心要主动做一次"矫正"。三个细节：

1. **目标是 `old_freq - 1` 配 `CPUFREQ_RELATION_L`**（`:1491`）：
   减 1 是为了排除 `old_freq` 本身，`RELATION_L` 再向下取表里最近的一项 ——
   合起来 = "**降到表里第一个严格低于当前值的频率**"。
   选降不选升，是因为升频可能超出散热/供电预算。
2. **`BUG_ON(ret)`（`:1499`）是本子系统唯一一处"业务失败就死机"**。
   注释的理由是：矫正失败意味着 CPU 会长时间跑在一个未知频率上，
   继续运行比死机更危险。见 §C1。
3. 这段只对带 `CPUFREQ_NEED_INITIAL_FREQ_CHECK` 标志的驱动生效，
   而这个标志**在 arm64 上非常普遍**：`cpufreq-dt`
   （`drivers/cpufreq/cpufreq-dt.c:176`）、`scmi-cpufreq`
   （`drivers/cpufreq/scmi-cpufreq.c:291`）、`qcom-cpufreq-hw`
   （`drivers/cpufreq/qcom-cpufreq-hw.c:649`）**三个主力驱动全都设了它**。
   ⇒ **这条死机路径在 arm64 主流平台上是真实可达的**，见 §C1。

### A2.6 选 governor：三层优先级

`cpufreq_init_policy()`（`:1074-1120`）在 `cpufreq_online()` 的 `:1530` 被调用。
`has_target()` 为真时（即驱动不是 `setpolicy` 型）：

```
1. policy->last_governor 非空 → get_governor(last_governor)      :1081-1082
2. 否则 → get_governor(default_governor)（那个字符串）           :1086-1087
3. 还没有 → cpufreq_default_governor() + __module_get(owner)     :1090-1092
4. 都没有 → return -ENODATA                                      :1094
```

第 1 和第 2 步用的是 `get_governor()`（`:648`），它在链表里按名字找**并且**
`try_module_get()`；第 3 步已经拿到编译进内核的那个，所以用 `__module_get()`。
这个区别的意义：**前两步允许 governor 是模块且可能没加载（找不到就往下走），
第三步的 governor 一定在内核里**。

`setpolicy` 驱动走另一支（`:1095-1113`）：用 `policy->last_policy`，
或者把 `default_governor` 这个字符串当 policy 名字解析
（`cpufreq_parse_policy()`，`:666`），解析不出 PERFORMANCE / POWERSAVE
就返回 **`-ENODATA`**（`:1112`）。

最后一行是 `return cpufreq_set_policy(policy, gov, pol)`（`:1115`）
配 `module_put()`（`:1117`）—— **也就是说，"第一次装 governor"和"后来换 governor"
走的是同一个函数**。这就是 §B2 的主题。

### A2.7 收尾三步

```
up_write(&policy->rwsem);                              :1537
kobject_uevent(&policy->kobj, KOBJ_ADD);               :1539
if (cpufreq_driver->ready) cpufreq_driver->ready(policy);   :1542
if (cpufreq_thermal_control_enabled(cpufreq_driver))
        policy->cdev = of_cpufreq_cooling_register(policy);  :1546
```

注意 `up_write()` 在 `kobject_uevent()` **之前**（`:1537` vs `:1539`）：
uevent 会让 udev 立刻回来读 sysfs，读 sysfs 要拿读锁 —— 如果还持着写锁就死等。
这是一处**有意为之的锁释放时机**。

`of_cpufreq_cooling_register()`（`:1546`）在最后：thermal 一旦注册成功，
就可能马上通过 freq_qos 压 `max` 下来（§B3），所以必须等 policy 完全可用。

五个回卷标签：`out_destroy_policy`（`:1553`）、`out_offline_policy`（`:1557`）、
`out_exit_policy`（`:1561`）、`out_free_policy`（`:1565`）。
`out_destroy_policy` 里做的事是 `cpufreq_stats_free_table` + `cpufreq_remove_dev_symlink`，
**和正常拆除路径共用**（§A7）。

---

## §A3 governor 上任与换班

### A3.1 四个动作的实际内容

核心侧的四个包装函数都很短，但**每个都有前置守卫**，这些守卫比函数体本身重要：

| 包装函数 | 位置 | 守卫 | 做的事 |
|---|---|---|---|
| `cpufreq_init_governor` | `:2358` | `cpufreq_suspended` → **返回 0**（`:2363`）；`!policy->governor` → `-EINVAL` | 动态切换兼容性检查 → `try_module_get` → `->init()` → 记 `strict_target` |
| `cpufreq_start_governor` | `:2417` | `cpufreq_suspended` → **返回 0**（`:2421`）；`!governor` → `-EINVAL` | 校准当前频率 → `->start()` → `->limits()` |
| `cpufreq_stop_governor` | `:2444` | `cpufreq_suspended \|\| !governor` → **直接返回** | `->stop()` |
| `cpufreq_exit_governor` | `:2404` | 同上 | `->exit()` → `module_put` |

**四个函数都在 `cpufreq_suspended` 为真时变成空操作。**
这是整个 suspend/resume 机制的实现方式（§A8）：不是加一把锁，
而是让所有 governor 操作在挂起窗口里**静默成功**。
副作用：挂起窗口里的一次 `scaling_governor` 写入会"成功"但什么也没发生。

### A3.2 `cpufreq_init_governor` 里那段动态切换兼容检查

```c
	if (policy->governor->flags & CPUFREQ_GOV_DYNAMIC_SWITCHING &&
	    cpufreq_driver->flags & CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING) {
		struct cpufreq_governor *gov = cpufreq_fallback_governor();

		if (gov) {
			pr_warn("Can't use %s governor as dynamic switching is disallowed. Fallback to %s governor\n",
				policy->governor->name, gov->name);
			policy->governor = gov;
		} else {
			return -EINVAL;
		}
	}
```

（`:2373-2384`）

这是 **governor 侧标志和驱动侧标志唯一一处直接对撞的地方**：
`CPUFREQ_GOV_DYNAMIC_SWITCHING`（governor 说"我会动态调频"）碰上
`CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING`（驱动说"平台不许自动调频"）。
处理方式是**降级而不是报错** —— 换成 `cpufreq_fallback_governor()`。
而 01 §5.1 已确认：**全树只有 performance governor 覆盖了这个弱符号**，
所以降级的落点一定是 performance。

`policy->strict_target = !!(governor->flags & CPUFREQ_GOV_STRICT_TARGET)`（`:2399`）
在 `->init()` **之后**赋值。驱动读这个字段来决定"要不要严格照目标频率设"
（典型是 powersave/performance 这种定频 governor）。

### A3.3 `cpufreq_start_governor` 里那次意外的频率校准

```c
	if (cpufreq_driver->get)
		cpufreq_verify_current_freq(policy, false);
```

（`:2429-2430`）

每次 governor 启动都会**重新问一次硬件当前频率**。这一句解释了一个常见现象：
**换 governor 会在 dmesg 里冒出一条 "CPU frequency out of sync" 的 `pr_debug`**
（`cpufreq_out_of_sync()`，`:1724`）。它不是错误，只是核心发现自己缓存的
`policy->cur` 和硬件不一致，并顺手补发了一对转换通知（§B6）。
注意第二个参数是 `false` —— **不调度 `handle_update`**，仅同步认知。

### A3.4 换 governor：停 → 退 → 换 → 起

在 `cpufreq_set_policy()` 里（`:2628-2658`）：

```
old_gov = policy->governor;                          :2628
if (old_gov) {
        cpufreq_stop_governor(policy);                :2631
        cpufreq_exit_governor(policy);                :2632
}
policy->governor = new_gov;                           :2636
ret = cpufreq_init_governor(policy);                  :2637
if (!ret) {
        ret = cpufreq_start_governor(policy);         :2639
        if (!ret) {
                sched_cpufreq_governor_change(policy, old_gov);   :2642
                return 0;
        }
        cpufreq_exit_governor(policy);                :2645
}
/* new governor failed, so re-start old one */
policy->governor = old_gov;                           :2649
if (old_gov) {
        if (cpufreq_init_governor(policy))
                policy->governor = NULL;              :2653
        else
                cpufreq_start_governor(policy);       :2655
}
```

**失败回滚的终点是 `policy->governor = NULL`（`:2653`、`:2656` 附近）。**
也就是说：新 governor 起不来、旧 governor 也起不来时，
**这个 policy 就变成"没有 governor"的状态**，频率从此不再变化，
而且 `scaling_governor` 读出来会是空。这是一个**真实可达的降级态**，
07 篇要教读者怎么认出它。

`sched_cpufreq_governor_change()`（`:2642`）只在**成功路径**上调。
它的实现（`kernel/sched/cpufreq_schedutil.c:882-894`）只做一件事：
如果切换涉及 schedutil（切入或切出），就 `schedule_work(&rebuild_sd_work)`
重建调度域 —— 因为 **EAS（能量感知调度）只在 schedutil 下才有意义**。
用 work 而不是直接调的理由写在 `:886-890` 的注释里：
从 `cpufreq_register_driver()` 路径进来时 `cpu_hotplug_lock` 已经被持有，
直接 `rebuild_sched_domains()` 会嵌套上锁。

只换极限不换 governor 时走短路（`:2619-2623`）：
`cpufreq_governor_limits(policy)`（`:2455`）→ `->limits()`，不停不起。

### A3.5 CPU 加入已有 policy

`cpufreq_add_policy_cpu()`（`:1122-1143`）是"同 policy 的第二颗 CPU 上线"的路径：

```
if (cpumask_test_cpu(cpu, policy->cpus)) return 0;    :1127   ← 幂等
down_write(&policy->rwsem);
if (has_target()) cpufreq_stop_governor(policy);      :1131
cpumask_set_cpu(cpu, policy->cpus);                   :1133
if (has_target()) {
        ret = cpufreq_start_governor(policy);
        if (ret) pr_err("%s: Failed to start governor\n", __func__);   :1139
}
up_write(&policy->rwsem);
```

**"停 governor → 改 mask → 起 governor"是这个子系统改 `policy->cpus` 的
标准三步。** 原因：governor 在 `->start()` 里按 `policy->cpus` 逐个挂调度器钩子
（`sugov_start()`，`kernel/sched/cpufreq_schedutil.c:805-809`；
`gov_set_update_util()`，`drivers/cpufreq/cpufreq_governor.c:332-337`），
mask 变了必须重挂。**不能在 governor 运行时改 mask。**

---

## §A4 采样式 governor 的一个周期

这是 02 §4.3 那四层的运行时串联。以 ondemand 为例，**一次采样从调度器进来，
经过四次上下文切换才把频率写下去**。

### A4.1 谁调谁

```
 调度器（rq->lock 内，不可睡）
     │  cpufreq_update_util()
     ▼
 dbs_update_util_handler()        cpufreq_governor.c:270
     │  各种早退判断（A4.2）
     │  irq_work_queue(&policy_dbs->irq_work)          :320
     ▼
 dbs_irq_work()                   cpufreq_governor.c:262   ← 硬中断上下文
     │  schedule_work_on(smp_processor_id(), &policy_dbs->work)  :267
     ▼
 dbs_work_handler()               cpufreq_governor.c:233   ← 进程上下文，可睡
     │  mutex_lock(&policy_dbs->update_mutex)          :247
     │  gov_update_sample_delay(policy_dbs, gov->gov_dbs_update(policy))  :248
     │       └── od_update()      cpufreq_ondemand.c:137
     │              └── dbs_update()  cpufreq_governor.c:114   ← 算负载
     │              └── __cpufreq_driver_target(...)           ← 下发（§A6）
     │  mutex_unlock                                   :249
     │  atomic_set(&policy_dbs->work_count, 0)         :252
     │  smp_wmb()                                      :258
     │  policy_dbs->work_in_progress = false           :259
     ▼
```

**两级延迟（irq_work → work）不是冗余。** 调度器钩子在
`rq->lock` 里且不可睡；`irq_work` 能在硬中断上下文安全执行但同样不可睡；
只有 `schedule_work_on()` 派出去的 `work` 才在进程上下文，才敢
`mutex_lock` 和调用可能睡眠的驱动回调。
**这就是 01 §5.3 那句"`fast_switch` 和 `target` 不是快慢之分，而是能不能睡之分"
在采样式 governor 上的体现** —— 采样式 governor 永远走"能睡"的那条路，
所以它必须自己搭这两级跳板。

`schedule_work_on(smp_processor_id(), ...)`（`:267`）绑在**当前 CPU** 上，
不是 policy 的代表 CPU。

### A4.2 钩子里的四道早退

`dbs_update_util_handler()`（`cpufreq_governor.c:270-321`）在真正排队之前要过四关：

| # | 位置 | 判据 | 为什么 |
|---|---|---|---|
| 1 | `:277` | `!cpufreq_this_cpu_can_update(policy)` | 本 CPU 不属于这个 policy 且不允许跨 CPU 调频 |
| 2 | `:286` | `policy_dbs->work_in_progress` | 上一轮还没做完 |
| 3 | `:296` | `delta_ns < sample_delay_ns` | 采样间隔没到 |
| 4 | `:304-316` | 共享 policy 下的 `atomic_add_unless(&work_count, 1, 1)` | 多颗 CPU 共享一个 policy 时只让一颗排队 |

第 2 关和第 3 关之间有一个 **`smp_rmb()`（`:293`）**，
注释（`:289-292`）说得很直：如果下面读 `last_sample_time` / `sample_delay_ns`
被重排到 `work_in_progress` 检查之前，就会用到**过期的采样间隔**。
配对的 `smp_wmb()` 在 `dbs_work_handler()` 的收尾（`:258`）。
⇒ **这一对屏障保护的是 `sample_delay_ns` 的新鲜度，不是计数器本身。**

第 4 关里还嵌了一次二次确认（`:312-315`）：拿到计数名额之后再看一眼
`last_sample_time` 有没有被别的 CPU 改过，改过就把名额还回去。
**这是一个手写的 compare-and-bail，不是标准同步原语。**

### A4.3 `dbs_update()`：负载是怎么算出来的

`cpufreq_governor.c:114-231`，对 `policy->cpus` 里每颗 CPU 循环，取最大值：

```
cur_idle_time = get_cpu_idle_time(j, &update_time, io_busy);   :143
time_elapsed  = update_time - j_cdbs->prev_update_time;        :145
idle_time     = cur_idle_time - j_cdbs->prev_cpu_idle;         :161（可能为 0，见下）
load = 100 * (time_elapsed - idle_time) / time_elapsed;        :209
```

四个特殊处理，**每一个都对应一类真实的测量失真**：

1. **`idle_time` 可能倒退**（`:160-163`）：`NOHZ` 关闭时
   `get_cpu_idle_time_jiffy()`（`:123`）用"经过时间 − 忙时间"反算 idle，
   两次采样的微小误差会让累计 idle 变小。处理：**钳到 0**。
   注释在 `:148-159`。
2. **`time_elapsed == 0`**（`:174-180`）：函数被连着调了两次。处理：**复用上次的 load**。
3. **突发唤醒失真**（`:181-206`）：CPU 完全空闲了很久（`idle_time > 2 * sampling_rate`）
   然后刚有任务醒过来，按公式算 load 会接近 0，
   **对延迟敏感的突发型负载极不公平**。处理：**复用上次的 load，然后把
   `prev_load` 清 0**（`:206`）—— 只让它救一次，避免长期卡在高频。
   这段注释（`:183-200`）是全子系统写得最详细的一处，值得完整读。
4. `io_busy`（`:134`）：`io_is_busy` 为真时**不把等 IO 的时间算作 idle**。
   注释（`:130-132`）的理由是"等磁盘 IO 说明你对性能敏感，不是系统真的闲着"。

`idle_periods`（`:216-221`、`:227`）算的是"完整空闲了几个采样周期"，
conservative 用它做降频决策。

### A4.4 `sampling_rate` 改了之后

`sampling_rate_store()`（`cpufreq_governor.c:44`）不只写字段：它遍历
`attr_set->policy_list` 上所有 policy，对每个调 `gov_update_sample_delay()`,
并且**如果新间隔更短，还要把 `work_count` 清掉让下一次采样立刻发生**。
这是 02 §4.2 说的"tunable 是一组 policy 共享的"的运行时后果：
**改一次 `sampling_rate` 会影响所有共享这套 tunable 的 policy。**

---

## §A5 schedutil 的两条路

schedutil 住在 `kernel/sched/cpufreq_schedutil.c`（895 行）。下文只写 `:NNN` 的
地方都指这个文件。

### A5.1 `sugov_start()` 挑哪个钩子函数

```c
	if (policy_is_shared(policy))
		uu = sugov_update_shared;
	else if (policy->fast_switch_enabled && cpufreq_driver_has_adjust_perf())
		uu = sugov_update_single_perf;
	else
		uu = sugov_update_single_freq;
```

（`:798-803`）

**三个钩子的选择是在 `->start()` 时一次定死的**，运行期不再判断。
判据的优先级是：**先看 policy 是否多 CPU 共享，再看驱动能力**。
所以一个大小核 arm64 板子上，如果每个 cluster 一个 policy 且 cluster 内多核，
走的一定是 `sugov_update_shared`。

`->start()` 之前还有 `->init()`（`sugov_init()`，`:679`），它负责建
`sg_policy`、tunable、以及 **只在 `!fast_switch_enabled` 时创建
`sugov:N` 内核线程**（`sugov_kthread_create()`，`:598`，
`:618-620` 的 `if (policy->fast_switch_enabled) return 0;`）。

那个线程的调度参数（`:601-614`）是 `SCHED_DEADLINE` + `SCHED_FLAG_SUGOV`，
带一份注释明说是 **"Fake (unused) bandwidth; workaround to 'fix'
priority inheritance"**（`:607-610`）。
⇒ **这是源码自陈的一处 workaround**，是 Part C 唯一的"官方承认的将就"。

### A5.2 路 A：`fast_switch`（不能睡的那条）

以单 CPU policy 为例，`sugov_update_single_freq()`（`:352-394`）：

```
sugov_update_single_common()                     :360   ← 取 util、算 iowait boost
next_f = get_next_freq(sg_policy, util, max)     :363
（"刚忙过就不降频"的抑制，:370-377）
if (!sugov_update_next_freq(sg_policy, time, next_f)) return;   :379
if (sg_policy->policy->fast_switch_enabled)
        cpufreq_driver_fast_switch(policy, next_f);              :388
else
        raw_spin_lock(&sg_policy->update_lock);
        sugov_deferred_update(sg_policy);                        :391
        raw_spin_unlock(...)
```

`:382-385` 的注释解释了为什么 fast_switch 分支**不加锁**：
这段代码跑在目标 CPU 的 `rq->lock` 里，同一个目标不会并发。
**锁只加在 deferred 分支上** —— 这是一处有意的不对称。

`cpufreq_driver_fast_switch()`（`drivers/cpufreq/cpufreq.c:2142-2165`）做五件事：

```
clamp_val(target_freq, policy->min, policy->max)     :2148
freq = cpufreq_driver->fast_switch(policy, target_freq);
if (!freq) return 0;                                 :2151
policy->cur = freq;                                  :2154
arch_set_freq_scale(policy->related_cpus, freq, policy->cpuinfo.max_freq);  :2155
cpufreq_stats_record_transition(policy, freq);       :2157
for_each_cpu(cpu, policy->cpus) trace_cpu_frequency(freq, cpu);   :2159-2162
```

**这条路上有 tracepoint（`:2161`）和统计（`:2157`），但没有通知链。**
02 §8 已给出结论，原因在 §B4。
⇒ 实践含义：**arm64 默认配置下要看频率变化，用 `cpu_frequency` tracepoint
和 `stats/`，别指望注册转换通知。**

`trace_cpu_frequency` 外面套了 `if (trace_cpu_frequency_enabled())`（`:2159`）——
因为要遍历 mask，不能白遍历。

### A5.3 路 B：kthread（能睡的那条）

`fast_switch_enabled` 为假时走 `sugov_deferred_update()`（`:133-139`）：

```
irq_work_queue(&sg_policy->irq_work)     :137
   ▼
sugov_irq_work()                         :511   ← 硬中断上下文
   kthread_queue_work(&sg_policy->worker, &sg_policy->work)   :517
   ▼
sugov_work()                             :485   ← sugov:N 线程上下文
   raw_spin_lock_irqsave(&sg_policy->update_lock)   :501
   freq = sg_policy->next_freq;                     :502
   sg_policy->work_in_progress = false;             :503
   raw_spin_unlock_irqrestore                       :504
   mutex_lock(&sg_policy->work_lock);               :506
   __cpufreq_driver_target(policy, freq, CPUFREQ_RELATION_L);  :507
   mutex_unlock(&sg_policy->work_lock);             :508
```

和采样式 governor 一样是**两级跳板**，但第二级用的是
**专属的 `kthread_work` 而不是共享 workqueue**。
理由在 `sugov_kthread_create()` 里：这个线程是 `SCHED_DEADLINE` 的，
优先级高于普通 worker —— **调频不能被别的工作项排在后面**。

`:491-499` 的注释指出一个精细的时序问题：
`next_freq` 在锁外被读会漏掉紧接着到来的更新。
处理方式是"**短暂持锁读一次**"，并且注释明说漏掉也不致命 ——
`kthread_work` 机制会再调一次 `sugov_work()`。
⇒ **这是一处"接受一次冗余调用换取更短临界区"的取舍。**

### A5.4 频率是怎么算出来的

`get_next_freq()`（`:163-178`）：

```c
	unsigned int freq = arch_scale_freq_invariant() ?
				policy->cpuinfo.max_freq : policy->cur;

	util = map_util_perf(util);
	freq = map_util_freq(util, freq, max);

	if (freq == sg_policy->cached_raw_freq && !sg_policy->need_freq_update)
		return sg_policy->next_freq;

	sg_policy->cached_raw_freq = freq;
	return cpufreq_driver_resolve_freq(policy, freq);
```

**基准频率的选择（`:167-168`）就是频率不变性（FIE）的全部意义所在**：

| `arch_scale_freq_invariant()` | 基准 | 公式（注释 `:147-157`） |
|---|---|---|
| 真 | `cpuinfo.max_freq` | `next = C * max_freq * util / max` |
| 假 | `policy->cur` | `next = C * cur_freq * util_raw / max` |

`C = 1.25` 来自 `map_util_perf()`（在 `include/linux/sched/cpufreq.h`），
注释解释为"**让拐点落在 util/max = 0.8**"。
FIE 的开关点在 §A1.3（`drivers/cpufreq/cpufreq.c:2902`），链路总结在 §B7。

`cached_raw_freq` 的短路（`:173-174`）省的是
`cpufreq_driver_resolve_freq()` 里的一次频率表二分查找。

`sugov_update_next_freq()`（`:107-131`）是**最后一道去重**：
频率没变就返回 false，调用方直接 return。
唯一的例外是驱动带了 `CPUFREQ_NEED_UPDATE_LIMITS`（`:121`）——
那种驱动要求"极限变了就必须回调一次，即便频率没变"。

### A5.5 `->limits()` 与那对屏障

`sugov_limits()`（`:829-849`）：

```c
	if (!policy->fast_switch_enabled) {
		mutex_lock(&sg_policy->work_lock);
		cpufreq_policy_apply_limits(policy);
		mutex_unlock(&sg_policy->work_lock);
	}
	smp_wmb();
	WRITE_ONCE(sg_policy->limits_changed, true);
```

配对的读侧在 `sugov_should_update_freq()`（`:85-99`）：

```c
	if (unlikely(READ_ONCE(sg_policy->limits_changed))) {
		WRITE_ONCE(sg_policy->limits_changed, false);
		sg_policy->need_freq_update = true;
		smp_mb();
		return true;
	}
```

两处注释（`:839-845` 和 `:89-96`）互相点名配对。
它们防的是：**极限已经改了，但下一次更新还用老极限算频率**。
⚠️ 注意读侧用的是 **`smp_mb()`** 而不是 `smp_rmb()` ——
因为它既要保证前面的 `WRITE_ONCE(limits_changed, false)` 先落地，
又要保证后面读 `policy->min/max` 不被提前。

**fast_switch 路径上 `sugov_limits()` 什么都不做（除了置标志）**：
`cpufreq_policy_apply_limits()` 被 `if (!fast_switch_enabled)` 挡住了。
理由：fast_switch 分支下一次更新马上就会用新极限重算，
没必要在这里同步下发一次（而且 `mutex_lock` 在那条路上也不合适）。

---

## §A6 路 C：`target_index`

### A6.1 `__cpufreq_driver_target()` 的三道过滤

`drivers/cpufreq/cpufreq.c:2294-2334`：

```
if (!policy->cur) return -ENODEV;                        :2301
target_freq = __resolve_freq(policy, target_freq,
              policy->min, policy->max, relation);       :2303
if (target_freq == policy->cur &&
    !(cpufreq_driver->flags & CPUFREQ_NEED_UPDATE_LIMITS))
        return 0;                                        :2315-2317
if (cpufreq_driver->target)                              :2319
        ... 走已废弃的 ->target()（含剥掉 RELATION_E）
if (!cpufreq_driver->target_index) return -EINVAL;       :2330
return __target_index(policy, policy->cached_resolved_idx);  :2333
```

`:2309-2314` 的注释承认第三道过滤是**故意冗余**的：
调用方（governor）通常已经比较过了，但"不是所有调用方都会比"，
所以核心再拦一次。

**`__resolve_freq()`（`:530-546`）通过副作用传递结果**：

```c
	idx = cpufreq_frequency_table_target(policy, target_freq, min, max, relation);
	policy->cached_resolved_idx = idx;
	policy->cached_target_freq = target_freq;
	return policy->freq_table[idx].frequency;
```

它返回频率值，但把**下标**写进 `policy->cached_resolved_idx`，
`:2333` 再把这个下标取出来交给 `__target_index()`。
⇒ **这是一处隐式的数据传递：两个函数通过一个 struct 字段耦合，
中间隔了 20 多行代码。** 见 §C2。

已废弃的 `->target()` 分支（`:2319-2327`）里有一句
`if (!policy->efficiencies_available) relation &= ~CPUFREQ_RELATION_E;`，
注释在 `:2320-2323`：老驱动不认识"效率优先"这个修饰位，得先剥掉。
**`target_index` 路径不需要这一步**，因为 `__resolve_freq()` 里的
频率表查找函数自己会处理。

### A6.2 `__target_index()` 与两阶段通知

`:2234-2292`：

```
if (newfreq == policy->cur) return 0;                     :2242
freqs.old = policy->cur;  freqs.new = newfreq;
restore_freq = policy->cur;                               :2246
notify = !(cpufreq_driver->flags & CPUFREQ_ASYNC_NOTIFICATION);   :2248

if (notify) {
        （中间频率处理，:2251-2260）
        cpufreq_freq_transition_begin(policy, &freqs);    :2266
}
retval = cpufreq_driver->target_index(policy, index);     :2269
if (retval)
        pr_err("%s: Failed to change cpu frequency: %d\n", __func__, retval);  :2271
if (notify) {
        cpufreq_freq_transition_end(policy, &freqs, retval);   :2275
        if (retval) {                                      :2283-2288
                freqs.old = restore_freq;
                freqs.new = policy->restore_freq;
                cpufreq_freq_transition_begin(policy, &freqs);
                cpufreq_freq_transition_end(policy, &freqs, 0);
        }
}
```

`:2277-2282` 的注释解释了最后那对"假的" begin/end：
频率**没有**变成 `freqs.new`，但订阅者已经收到过 PRECHANGE/POSTCHANGE 了，
所以要**再发一对反向的通知把它们的认知拧回来**。
⇒ **通知链的一致性是靠"补发相反的通知"维持的，不是靠事务回滚。**

`notify` 这个变量（`:2248`）是 `CPUFREQ_ASYNC_NOTIFICATION` 的全部作用：
带这个标志的驱动**自己**负责发通知（因为频率变化是异步完成的，
核心不知道什么时候真的变了）。arm64 上没有这类驱动。

中间频率（`:2251-2260`）：`__target_intermediate()`（`:2209-2232`）先切到
一个"安全的过渡频率"，典型场景是切换时钟源需要先降到某个安全档。
`get_intermediate()` 返回 0 表示"这次不需要中间频率"。
这是 §A1.2 第 5 条要求成对实现的那对回调。

### A6.3 两阶段协议的实现

`cpufreq_freq_transition_begin()`（`:407-438`）：

```
WARN_ON(!(flags & CPUFREQ_ASYNC_NOTIFICATION) && current == policy->transition_task);  :419
wait:
wait_event(policy->transition_wait, !policy->transition_ongoing);   :423
spin_lock(&policy->transition_lock);                                :425
if (unlikely(policy->transition_ongoing)) { spin_unlock; goto wait; }  :427-430
policy->transition_ongoing = true;
policy->transition_task = current;                                  :432-433
spin_unlock(&policy->transition_lock);
cpufreq_notify_transition(policy, freqs, CPUFREQ_PRECHANGE);        :437
```

`:411-418` 的注释说明 `WARN_ON` 抓的是**同一个任务重复调 `_begin()`
导致自锁死**。`ASYNC_NOTIFICATION` 驱动被排除在检查外，因为核心不代它调。

`wait_event` + 重新检查 + `goto wait`（`:423-430`）是一个
**手写的条件变量**：`wait_event` 醒来后条件可能又被别人抢走，
所以在自旋锁里再确认一次。**没有用 mutex，因为这个"锁"可能被
不同任务 acquire/release**（`_begin()` 和 `_end()` 常常在同一个任务里，
但 ASYNC 驱动可以在中断里 `_end()`）。

`cpufreq_freq_transition_end()`（`:441-459`）：
`WARN_ON(!policy->transition_ongoing)` → 发 POST（可能带回滚，`:447`）→
**`arch_set_freq_scale()`（`:449-451`）** → 清标志 → `wake_up()`。

注意 `arch_set_freq_scale()` 在**两条路上都有**：
fast_switch 路在 `:2155`，target 路在 `:449`。
**这是 arm64 频率不变性的实际更新点** —— 它写的是 per-CPU 的
`arch_freq_scale`，调度器的 util 计算要用（§B7）。

### A6.4 `cpufreq_notify_transition()` 里的两个 case

`:343-392`。开头 `BUG_ON(irqs_disabled())`（`:349`）——
**第三个 `BUG_ON`，也是最直白的一个：通知链不能在关中断下发。**

| 阶段 | 动作 |
|---|---|
| PRECHANGE | 若 `policy->cur != freqs->old`，**以 `policy->cur` 为准改写 `freqs->old`**（`:366-370`）→ `srcu_notifier_call_chain(PRECHANGE)`（`:372`）→ `adjust_jiffies()` |
| POSTCHANGE | `adjust_jiffies()` → **`trace_cpu_frequency()` 逐 CPU**（`:383-384`）→ `srcu_notifier_call_chain(POSTCHANGE)`（`:386`）→ `cpufreq_stats_record_transition()`（`:389`）→ **`policy->cur = freqs->new`**（`:390`） |

**`policy->cur` 是在 POSTCHANGE 的最后一行才更新的（`:390`）。**
含义：**POSTCHANGE 通知的订阅者看到的 `policy->cur` 还是旧值**，
要用 `freqs->new`。这是一个非常容易写错的地方。

PRECHANGE 里那段改写（`:366-370`）配一条 `pr_debug`
"Warning: CPU frequency is %u, cpufreq assumed %u kHz" ——
**它是 `pr_debug` 而不是 `pr_warn`**，默认看不见，07 篇要提。

---

## §A7 拆除路径

### A7.1 三层拆除

cpufreq 的拆除是**三个入口 + 两级深度**，很容易看混：

| 入口 | 触发 | 深度 |
|---|---|---|
| `cpufreq_offline(cpu)`（`:1648`） | CPU 热插拔（`cpuhp_cpufreq_offline`，`:2833`） | 轻量：停/退 governor + `->offline()` |
| `cpufreq_remove_dev()`（`:1673`） | CPU 设备移除（`subsys_interface.remove_dev`，`:1877`） | 完整：拆 cooling + `->exit()` + 释放 policy |
| `cpufreq_unregister_driver()`（`:2954`） | 驱动模块卸载 | 全局：注销 interface + 清全局指针 |

### A7.2 `__cpufreq_offline()`：最后一颗 CPU 才真拆

`:1600-1646`。**两段结构**：

```c
	if (has_target())
		cpufreq_stop_governor(policy);

	cpumask_clear_cpu(cpu, policy->cpus);

	if (!policy_is_inactive(policy)) {
		/* Nominate a new CPU if necessary. */
		if (cpu == policy->cpu)
			policy->cpu = cpumask_any(policy->cpus);

		/* Start the governor again for the active policy. */
		if (has_target()) {
			ret = cpufreq_start_governor(policy);
			if (ret)
				pr_err("%s: Failed to start governor\n", __func__);
		}

		return;
	}
```

（`:1604-1622`）

**还有别的 CPU 在线，就只是换个代表然后把 governor 重启，然后 return。**
`policy->cpu = cpumask_any(policy->cpus)`（`:1612`）是 02 §1.1 说的
"`policy->cpu` 是当前代表"的唯一改写点。
注意**这里也是"停 governor → 改 mask → 起 governor"三步**（同 §A3.5）。

最后一颗下线时继续（`:1624-1645`）：

```
if (has_target())
        strncpy(policy->last_governor, policy->governor->name, CPUFREQ_NAME_LEN);   :1625
else
        policy->last_policy = policy->policy;                                       :1628
if (has_target()) cpufreq_exit_governor(policy);                                    :1631
if (cpufreq_driver->offline) { cpufreq_driver->offline(policy); return; }            :1637-1640
if (cpufreq_driver->exit) cpufreq_driver->exit(policy);                             :1642
policy->freq_table = NULL;                                                          :1645
```

**`last_governor` 在这里被写下**（`:1625`）—— 和 §A2.6 的第 1 步呼应：
下线记名字，上线按名字恢复。`setpolicy` 驱动记的是 `last_policy`（`:1628`）。

**`->offline()` 存在时直接 return，不调 `->exit()`（`:1637-1640`）。**
注释（`:1633-1636`）明说这是"轻量拆除，让 CPU 回来时能快速恢复"。
代价是 `policy->freq_table` **不清**（`:1645` 被跳过了）——
所以 §A2.3 路 1 才能靠 `related_cpus` 快速恢复。
⇒ **`->offline()`/`->online()` 这一对回调的全部意义就是"跳过重建频率表"。**

### A7.3 `cpufreq_remove_dev()`：真正的释放

`:1673-1709`：

```
if (!policy) return;                                     :1678
down_write(&policy->rwsem);
if (cpu_online(cpu)) __cpufreq_offline(cpu, policy);     :1683
remove_cpu_dev_symlink(policy, cpu, dev);                :1685
if (!cpumask_empty(policy->real_cpus)) { up_write; return; }   :1688-1691
if (cpufreq_thermal_control_enabled(cpufreq_driver)) {
        cpufreq_cooling_unregister(policy->cdev);         :1698
        policy->cdev = NULL;
}
if (cpufreq_driver->offline && cpufreq_driver->exit)
        cpufreq_driver->exit(policy);                     :1703
up_write(&policy->rwsem);
cpufreq_policy_free(policy);                              :1708
```

**`real_cpus` 是"能不能释放 policy"的判据（`:1688`）**，
不是 `cpus` 也不是 `related_cpus`。因为 `remove_cpu_dev_symlink()`
（`:1034`）会从 `real_cpus` 里清位 —— 所以这里问的是
"**还有物理存在的成员吗**"。02 §1.1 说 `real_cpus` "决定符号链接建在哪"，
这里补上第二个用途：**它是释放的引用计数**。

`:1702` 的注释 "We did light-weight exit earlier, do full tear down now"
配上 `:1703` 的条件 `offline && exit`：**只有走了轻量路的驱动，
才需要在这里补一次 `->exit()`**。没有 `->offline()` 的驱动在
`__cpufreq_offline()` 里已经 `->exit()` 过了。

`cpufreq_policy_free()`（`:1290`）里有一条
`pr_warn("%s: Freeing active policy\n", __func__)`（`:1300`），
配 `:1295-1298` 的注释（讲的是可能和并发的 show()/store() 竞争）。
**它是核心里唯一一条纯粹的"这不该发生"警告。**

### A7.4 `cpufreq_unregister_driver()`

`:2954-2977`：`if (!cpufreq_driver || driver != cpufreq_driver) return -EINVAL`
→ `cpus_read_lock()` → `subsys_interface_unregister()`（`:2965`）→
`remove_boost_sysfs_file()` → 写锁下 `cpufreq_driver = NULL` → `cpus_read_unlock()`。

**`subsys_interface_unregister()` 会对每颗 CPU 回调 `remove_dev`**，
所以 policy 的释放发生在这一句里面，**而不是之后**。
⇒ 顺序保证：**清 `cpufreq_driver` 全局指针一定发生在所有 policy 都拆完之后**，
因为 `__cpufreq_offline()` 全程要读 `cpufreq_driver->xxx`。

---

## §A8 suspend / resume

`cpufreq_suspend()`（`:1914-1940`）与 `cpufreq_resume()`（`:1948-1980`）
由 PM 核心调用，**不是 cpufreq 自己的时间线**，但会打断上面所有时间线。

```
suspend:  for_each_active_policy:
              down_write(rwsem); cpufreq_stop_governor(); up_write();   :1928-1930
              cpufreq_driver->suspend(policy)  → 失败仅 pr_err          :1933-1935
          cpufreq_suspended = true;                                     :1939

resume:   if (!cpufreq_suspended) return;      ← 幂等                    :1956
          cpufreq_suspended = false;                                    :1959
          for_each_active_policy:
              cpufreq_driver->resume(policy)   → 失败 pr_err            :1967
              else down_write(rwsem); cpufreq_start_governor(); up_write();  :1971-1973
```

**`cpufreq_suspended = true` 是在循环之后才置的（`:1939`）**，
而 `false` 是在循环之前（`:1959`）。
理由：§A3.1 那四个函数在 `cpufreq_suspended` 为真时全变空操作 ——
所以 suspend 时要**先真的停完再置位**，resume 时要**先清位才能真的启动**。
这个顺序反了整个机制就失效。

`:1907-1913` 的注释给出了为什么必须停 governor：
"某些平台在挂起流程的这个点之后就不能改频率了，
因为改频率要用的 i2c、regulator 等设备很快就会被挂起"。
⇒ **这是 cpufreq 和 regulator / i2c 的一处真实跨子系统时序依赖。**

`has_target()` 为假且驱动没有 `->suspend` 时直接 `goto suspend`（`:1921`），
只置标志。所以**硬件自治型驱动的挂起是零成本的**。

⚠️ 两条路的错误处理都只有 `pr_err`（`:1934`、`:1968`、`:1976`），
**没有任何回滚**。驱动 resume 失败的后果是这个 policy 的 governor 没起来 ——
和 §A3.4 的降级态一样，频率从此不动。

---

# Part B · 横切机制

Part A 是"什么时候发生什么"。Part B 是"贯穿所有时间点的那几件事"。
**读 Part B 之前不需要读完 Part A**，两部分可以互相跳。

## §B1 锁：核心五把，外加四把在 governor 侧

| # | 锁 | 位置 | 保护什么 | 上下文 |
|---|---|---|---|---|
| 1 | `cpufreq_driver_lock`（**rwlock**） | `drivers/cpufreq/cpufreq.c:60` | 全局 `cpufreq_driver` 指针 · `cpufreq_policy_list` · per-CPU `cpufreq_cpu_data` | 关中断（`_irqsave`），**读侧极短** |
| 2 | `cpufreq_governor_mutex` | `:104` | `cpufreq_governor_list` | 可睡 |
| 3 | `cpufreq_fast_switch_lock` | `:467` | `cpufreq_fast_switch_count` | 可睡 |
| 4 | `policy->rwsem` | 初始化在 `:1242` | **一个 policy 的几乎所有字段** | 可睡，写锁 |
| 5 | `policy->transition_lock`（spinlock） | 初始化在 `:1244` | `transition_ongoing` / `transition_task` | 可在中断里拿 |
| — | `cpus_read_lock()` | `:2877`、`:2754` 等 | CPU 热插拔（不是本子系统的锁） | 可睡 |
| 6 | `gov_attr_set.update_lock` | `include/linux/cpufreq.h:660` | 一组 policy 共享的 tunable | 可睡 |
| 7 | `policy_dbs->update_mutex` | 采样式 governor | 一次采样的完整过程 | 可睡 |
| 8 | schedutil `sg_policy->work_lock`（mutex） | `kernel/sched/cpufreq_schedutil.c` | kthread 下发频率 | 可睡 |
| 9 | schedutil `sg_policy->update_lock`（raw spinlock） | 初始化在 `kernel/sched/cpufreq_schedutil.c:589` | `next_freq` / `work_in_progress` | **raw**，调度器上下文 |

### B1.1 为什么 1 号是 rwlock 而不是 mutex

因为 `cpufreq_cpu_get()`（`:225-234`）和 `cpufreq_quick_get()`（`:1781-1789`）
用的是 **`read_lock_irqsave`** —— 这两个函数要能在**关中断的路径上**被调用
（典型是 `cpufreq_get()` 被 arch 代码在早期调用）。
mutex 做不到这件事。
代价：临界区里绝对不能睡，所以这把锁**只用来查表和取引用，不做任何实际工作**。

### B1.2 4 号锁的两个惯例

```c
	lockdep_assert_held(&policy->rwsem);
```

出现在 `cpufreq_enable_fast_switch()`（`:496`）和 `cpufreq_cpu_release()`（`:259`）。
**这是核心对驱动的一个隐式要求**：`->init()` 里调
`cpufreq_enable_fast_switch()` 是合法的，因为 `cpufreq_online()` 全程持写锁。

`cpufreq_cpu_acquire()`（`:285`）/ `cpufreq_cpu_release()`（`:259-261`）是
"取引用 + 拿写锁"的配对包装，注释在 `:268-277`。
唯一使用者是 `cpufreq_update_policy()`（`:2674`、`:2690`）。

### B1.3 一处刻意的顺序：`up_write` 在 `kobject_uevent` 之前

`cpufreq_online()` 结尾（§A2.7，`:1537`、`:1539`）先释放写锁再发 uevent。
理由：udev 收到 uevent 会立刻回来读 sysfs 属性，
而所有 `show()` 函数（`:976-979`）要拿 **读锁** ——
在写锁里发 uevent 就等于给自己设了一个自死锁窗口。
⇒ **凡是会触发用户态回读的动作，都要在放锁之后做。**

---

## §B2 唯一漏斗：`cpufreq_set_policy()`

`:2561-2661`。**所有"改极限"和"换 governor"的请求最终都汇到这里。**
它是 static 函数，只有三个调用者：

| 调用者 | 位置 | 场景 |
|---|---|---|
| `cpufreq_init_policy()` | `:1115` | policy 首次上线，挑好 governor |
| `refresh_frequency_limits()` | `:1150` | freq_qos 聚合值变了 |
| `store_scaling_governor()` | sysfs | 用户写 `scaling_governor` |

### B2.1 五个步骤

```
1. 组装 new_data（cpuinfo / freq_table / cpu 三项照抄）        :2569-2571
2. 从 freq_qos 读聚合后的 min/max                              :2576-2577
3. cpufreq_driver->verify(&new_data)  失败直接返回              :2586-2588
4. 把 min/max 对齐到频率表上真实存在的频率                      :2599-2604
5. 分三条路：setpolicy / 同 governor 只更极限 / 换 governor      :2613-2658
```

**第 2 步是 cpufreq 极限语义的定义点**：`policy->min/max`
**不是任何人直接写进去的**，而是 freq_qos 聚合的结果（§B3）。
注释（`:2572-2575`）原文："PM QoS framework collects all the requests
from users and provide us the final aggregated value here."

### B2.2 第 4 步那三行为什么要 `WRITE_ONCE`

```c
	WRITE_ONCE(policy->max, __resolve_freq(policy, new_data.max,
					       new_data.min, new_data.max,
					       CPUFREQ_RELATION_H));
	new_data.min = __resolve_freq(policy, new_data.min, new_data.min,
				      new_data.max, CPUFREQ_RELATION_L);
	WRITE_ONCE(policy->min, new_data.min > policy->max ? policy->max : new_data.min);
```

（`:2599-2604`）

`:2595-2597` 的注释说得很清楚：**不能在 `policy->max`/`policy->min` 里存中间值，
也不能让编译器围着它们做优化，因为 `cpufreq_driver_resolve_freq()`
会并发读它们。** 配对的读侧就是 `:562-563` 的那两个 `READ_ONCE`。

两次 `__resolve_freq()` 用的 relation **不一样**：
max 用 `CPUFREQ_RELATION_H`（向下取到表里第一个 ≤ 它的），
min 用 `CPUFREQ_RELATION_L`（向上取到表里第一个 ≥ 它的）。
注释（`:2590-2593`）：这样保证**既不会超过请求的上限，也不会低于请求的下限**。

最后那个三元表达式（`:2604`）是 min/max 交叉时的兜底：
**min 被钳到 max，而不是报错**。所以"写 `scaling_min_freq` 比 max 还大"
不会失败，只会让两者相等 —— 07 篇要提这个静默行为。

`policy->cached_target_freq = UINT_MAX`（`:2608`）作废 §A6.1 那个缓存：
极限变了，上一次解析结果就不能再用。
`trace_cpu_frequency_limits(policy)`（`:2606`）是**这里唯一的 tracepoint**。

### B2.3 三条出路

| 条件 | 位置 | 动作 |
|---|---|---|
| `cpufreq_driver->setpolicy` | `:2613-2617` | 写 `policy->policy` → `->setpolicy()` → **直接返回**，governor 完全不参与 |
| `new_gov == policy->governor` | `:2619-2623` | `cpufreq_governor_limits()` → `->limits()` |
| 其余 | `:2625-2658` | 换 governor（§A3.4） |

第一条路解释了 02 §3.1 说的"`setpolicy` 驱动没有 governor"：
它在这一行就 return 了，`policy->governor` 从来没被设置过。
⇒ arm64 上这条路基本不走（intel_pstate 的主动模式才走），但**代码路径是活的**。

### B2.4 `->limits()` 的默认实现

`cpufreq_policy_apply_limits()`（`include/linux/cpufreq.h:646-654`）：

```c
	if (policy->max < policy->cur)
		__cpufreq_driver_target(policy, policy->max, CPUFREQ_RELATION_HE);
	else if (policy->min > policy->cur)
		__cpufreq_driver_target(policy, policy->min, CPUFREQ_RELATION_LE);
```

**只在当前频率越界时才动**。极限收窄但当前频率还在新区间内 ⇒ 什么都不做，
等下一次 governor 决策自然落进新区间。
⇒ **写 `scaling_max_freq` 不一定立刻看到频率变化**，这是正常行为不是 bug。

用的是 `_HE` / `_LE` 后缀（带 `CPUFREQ_RELATION_E` 效率位），
和 `__target_index` 那边剥位的逻辑（§A6.1）呼应。

---

## §B3 极限是怎么传下来的：freq_qos 链

### B3.1 完整链路

```
写 /sys/.../policyN/scaling_max_freq
     │  store_one(scaling_max_freq, max)          :739 宏 / :755 展开
     │  freq_qos_update_request(policy->max_freq_req, val)   :750
     ▼
freq_qos 重新聚合（kernel/power/qos.c）
     │  聚合值变了才通知
     ▼
cpufreq_notifier_max()                            :1175-1182
     │  schedule_work(&policy->update)            :1180
     ▼
handle_update()                                   :1155-1164
     │  down_write(&policy->rwsem)                :1161
     │  refresh_frequency_limits(policy)          :1162
     ▼
refresh_frequency_limits()                        :1145-1152
     │  if (!policy_is_inactive(policy))
     │  cpufreq_set_policy(policy, policy->governor, policy->policy)  :1150
     ▼
§B2
```

**中间隔着一个 workqueue（`:1180` → `:1155`）。**
含义有三条：
1. **写 sysfs 返回时，极限还没生效** —— `store` 只更新了 QoS 请求；
2. 通知回调在**不能睡的上下文**里被调（freq_qos 的通知链是原子的），
   而 `cpufreq_set_policy()` 要拿 rwsem，所以必须推迟；
3. 多个请求密集到达时，workqueue 会**自然合并**成一次 `refresh`。

`refresh_frequency_limits()` 传的第二、三个参数是
**`policy->governor` 和 `policy->policy` 本身** —— 即"governor 不变"，
所以一定走 §B2.3 的第二条路（`->limits()`）。

`policy_is_inactive()` 的守卫（`:1147`）：policy 已经全部下线了就不刷。
因为 workqueue 可能在 offline 之后才跑到。

### B3.2 两个请求，一次分配

§A2.4 说过 `min_freq_req` 和 `max_freq_req` 是**一次 `kzalloc(2 * ...)`**
分配出来的（`:1418`），`max_freq_req = min_freq_req + 1`（`:1443`）。
`:1438-1442` 的注释解释了为什么不能反：
**这两个请求在 QoS 的链表上必须按这个顺序**，否则聚合结果不对。

释放时只 `kfree(policy->min_freq_req)`（在 `cpufreq_policy_free()` 里），
因为第二个是同一块内存的第二个元素。**这是一个必须成对理解的分配技巧**，
单看释放代码会以为漏了。

### B3.3 内核侧的其他请求者

thermal 的 `cpufreq_cooling` 通过同一套 freq_qos 加自己的 `FREQ_QOS_MAX` 请求。
⇒ **用户写的 `scaling_max_freq` 和 thermal 的限频是"两个请求，取最小"的关系**，
不是互相覆盖。这解释了 07 篇一个常见困惑：
**`scaling_max_freq` 写进去了，但 `cpuinfo_cur_freq` 上不去**。

另一条不经过 freq_qos 的路：`cpufreq_update_limits()`（`:2701-2716`）。
它优先调驱动的 `->update_limits()`（`:2710`），没有才退回
`cpufreq_update_policy()`（`:2712`）。**这是给硬件自治驱动准备的钩子。**

`cpufreq_update_policy()`（`:2672-2691`）比 `refresh_frequency_limits()`
多做一件事：先 `cpufreq_verify_current_freq()` 校准（`:2684`），
而且那个条件里套了 **`WARN_ON()`** —— 校准返回 0（拿不到频率）会打栈。
注释（`:2679-2682`）："BIOS might change freq behind our back"。

---

## §B4 为什么 `fast_switch` 和转换通知不能共存

### B4.1 一个计数器管两件事

`cpufreq_fast_switch_count`（`:466`）的语义写在 `:462-465` 的注释里：

| 值 | 含义 |
|---|---|
| **> 0** | 已经有 N 个 policy 启用了 fast_switch ⇒ **拒绝注册转换通知** |
| **< 0** | 已经有 |N| 个转换通知注册了 ⇒ **拒绝启用 fast_switch** |
| **0** | 还没定，两边都可以 |

**一个 int 表达三态互斥，靠正负号分向。**
这是全子系统最紧凑的一处设计。

### B4.2 两侧的实现

`cpufreq_enable_fast_switch()`（`:494-511`）：

```c
	if (!policy->fast_switch_possible)
		return;

	mutex_lock(&cpufreq_fast_switch_lock);
	if (cpufreq_fast_switch_count >= 0) {
		cpufreq_fast_switch_count++;
		policy->fast_switch_enabled = true;
	} else {
		pr_warn("CPU%u: Fast frequency switching not enabled\n", policy->cpu);
		cpufreq_list_transition_notifiers();
	}
	mutex_unlock(&cpufreq_fast_switch_lock);
```

`cpufreq_register_notifier()` 的 TRANSITION 分支（`:2048-2061`）：

```c
		mutex_lock(&cpufreq_fast_switch_lock);
		if (cpufreq_fast_switch_count > 0) {
			mutex_unlock(&cpufreq_fast_switch_lock);
			return -EBUSY;
		}
		ret = srcu_notifier_chain_register(&cpufreq_transition_notifier_list, nb);
		if (!ret)
			cpufreq_fast_switch_count--;
```

**注意两侧的失败方式完全不同**：
- 通知注册失败 ⇒ **返回 `-EBUSY`**，调用者知道；
- fast_switch 启用失败 ⇒ **只打一条 `pr_warn` 然后静默降级**（`:506-507`）。

⇒ 后果：**一个 out-of-tree 模块注册了转换通知，就会让整台机器的
schedutil 从路 A 退到路 B**，而唯一的痕迹是一条 `pr_warn` 和
`cpufreq_list_transition_notifiers()`（`:471`）打出的通知者列表。
**这个诊断函数存在的唯一目的就是回答"是谁把 fast_switch 挡掉了"**，
07 篇必须写它。

注销通知时（`:2097`）：`if (!ret && !WARN_ON(cpufreq_fast_switch_count >= 0))
cpufreq_fast_switch_count++;` —— 计数器归零的方向也带 `WARN_ON` 自查。
`cpufreq_disable_fast_switch()`（`:518-527`）同理（`:523`）。

### B4.3 根本原因

`:489-492` 的注释是官方答案：
"fast frequency switching is **quite fundamentally at odds** with
transition notifiers"。

技术上的理由：转换通知链是 **SRCU** 的，`cpufreq_notify_transition()`
开头就是 `BUG_ON(irqs_disabled())`（`:349`）；而 `fast_switch` 的调用点
（`sugov_update_single_freq()`，`kernel/sched/cpufreq_schedutil.c:388`）
在 `rq->lock` 里。**两者的上下文要求直接冲突，不是性能取舍。**

`cpufreq_driver_adjust_perf()`（`:2189-2195`）连这层判断都没有 ——
函数体只有一行 `cpufreq_driver->adjust_perf(...)`。
它的前置条件全部写在 `:2178-2188` 的注释里：
"never called twice in parallel for the same CPU"、
"never called in parallel with either ->target() or ->target_index()
or ->fast_switch() for the same CPU"。
⇒ **这是纯契约式接口：核心不校验，由 governor 保证。**

---

## §B5 内存序：三对屏障

cpufreq 里一共 **4 处显式屏障**，构成 **3 对**，每一对在源码里都有注释点名对方。

| # | 写侧 | 读侧 | 保护的字段 |
|---|---|---|---|
| 1 | `smp_wmb()` `drivers/cpufreq/cpufreq_governor.c:258` | `smp_rmb()` `drivers/cpufreq/cpufreq_governor.c:293` | `sample_delay_ns` / `last_sample_time` 相对 `work_in_progress` |
| 2 | `smp_wmb()` `kernel/sched/cpufreq_schedutil.c:846` | **`smp_mb()`** `kernel/sched/cpufreq_schedutil.c:97` | `policy->min/max` 相对 `limits_changed` |
| 3 | （无显式写屏障） | `smp_rmb()` `drivers/cpufreq/cpufreq_stats.c` 的 `cpufreq_stats_reset_table()` | `reset_time` 相对 `reset_pending` |

### B5.1 第 1 对：采样间隔的新鲜度

见 §A4.2。**写侧在"活干完了"之后**（`dbs_work_handler` 收尾），
**读侧在"要不要排新活"之前**（`dbs_update_util_handler`）。
`:253-257` 的注释指出：`work_in_progress = false` 必须**最后**写，
否则别的 CPU 可能看到 `work_in_progress == false` 但读到旧的 `sample_delay_ns`。

### B5.2 第 2 对：极限变更的可见性

见 §A5.5。读侧用 `smp_mb()`（全屏障）而不是 `smp_rmb()`，
因为它同时要挡住**自己那次 `WRITE_ONCE(limits_changed, false)`** 被推后。

### B5.3 第 3 对：统计复位的原子性

`cpufreq_stats_reset_table()` 里：`memset` 两个数组 →
`WRITE_ONCE(stats->reset_pending, 0)` → **`smp_rmb()`** →
`cpufreq_stats_update(stats, READ_ONCE(stats->reset_time))`。
源码注释说明这个屏障防的是 **`reset_time` 的读被重排到
`reset_pending` 的访问之前**（另一侧的访问在
`cpufreq_stats_record_transition()` 里）。

这一对是三对里**唯一不对称**的：只有读屏障没有写屏障。
⇒ 记在 §C2。

顺带一个可观测行为：`reset_pending` 为真期间，
`show_total_trans()` **返回字面量 0**，`show_time_in_state()` 也返回复位后的视图 ——
**"写 `stats/reset` 之后马上读，看到的是 0，但真正的 memset 还没发生"**。
这是有意的：把清零的代价推迟到下一次频率转换。

---

## §B6 认知同步：核心怎么发现自己记错了频率

核心缓存的 `policy->cur` 和硬件实际频率会漂移（BIOS/固件改频、驱动
`->target_index()` 部分失败、深度休眠恢复）。三个函数负责纠正：

### B6.1 `cpufreq_verify_current_freq()`（`:1734-1766`）

```
new_freq = cpufreq_driver->get(policy->cpu);       :1738
if (!new_freq) return 0;                           :1739-1740
if (policy->fast_switch_enabled || !has_target())
        return new_freq;                           :1746-1747   ← 只读不改
if (abs(policy->cur - new_freq) < KHZ_PER_MHZ)
        return policy->cur;                        :1757        ← 容差 1 MHz
cpufreq_out_of_sync(policy, new_freq);             :1760
if (update) schedule_work(&policy->update);        :1761-1762
```

**三层门槛**：
1. `->get()` 返回 0 ⇒ 放弃（返回 0，调用方的 `WARN_ON` 会打，见 §B3.3）；
2. fast_switch 或无 governor ⇒ **只报告不纠正**（`:1742-1745` 的注释：
   这两种情况下核心没有权威的"应该是多少"）；
3. **差值 < 1 MHz 就认为是相符的**（`:1757`）。
   `:1750-1756` 的注释举了例子：驱动可能报 499 MHz 而表里写 500 MHz，
   **舍入差异不该被当成失步**。

### B6.2 `cpufreq_out_of_sync()`（`:1719-1732`）

```c
	pr_debug("Warning: CPU frequency out of sync: cpufreq and timing core thinks of %u, is %u kHz\n",
		 policy->cur, new_freq);

	freqs.old = policy->cur;
	freqs.new = new_freq;

	cpufreq_freq_transition_begin(policy, &freqs);
	cpufreq_freq_transition_end(policy, &freqs, 0);
```

**一对"什么都不做"的 begin/end**（`:1730-1731`）。
频率没被改动，这对调用的唯一目的是**让 `policy->cur` 走一遍正规更新路径**
（§A6.4 说过 `policy->cur = freqs->new` 只发生在 POSTCHANGE 的最后一行），
顺带通知订阅者、更新统计、打 tracepoint。

⇒ 和 §A6.2 的回滚通知（`:2283-2288`）是同一个手法：
**"发一对通知"是这个子系统唯一的状态发布机制，
所以任何认知更新都得借它一用。**

这条消息是 `pr_debug`（`:1724`），**默认看不见**。
07 篇要写怎么打开它（`dynamic_debug` 或 `CONFIG_DYNAMIC_DEBUG` + `dyndbg=`）。

### B6.3 三个调用点

| 调用者 | `update` 参数 | 场景 |
|---|---|---|
| `cpufreq_start_governor()` `:2430` | `false` | 每次 governor 启动（§A3.3） |
| `cpufreq_update_policy()` `:2684` | `false` | 显式刷新，外面套了 `WARN_ON` |
| `show_cpuinfo_cur_freq()` / `cpufreq_get()` 路径 | `true` | 用户读 sysfs 时顺带纠正 |

只有第三类会 `schedule_work(&policy->update)`（`:1761`）——
也就是**读一次 `cpuinfo_cur_freq` 可能触发一次完整的极限重新应用**。
这是一个容易被忽略的副作用。

---

## §B7 频率不变性（FIE）在 arm64 上的完整链路

02 §5.3 只说了"有一个静态键"。这里给全链。

```
cpufreq_register_driver()                                    §A1.3
  │  if (!cpufreq_driver->setpolicy)
  │      static_branch_enable_cpuslocked(&cpufreq_freq_invariance)  :2902
  ▼
static key: cpufreq_freq_invariance                          :62
  │  读侧唯一入口：cpufreq_supports_freq_invariance()        :63-66
  ▼
（每次频率变化）
  ├─ 路 A：cpufreq_driver_fast_switch() → arch_set_freq_scale()      :2155
  └─ 路 C：cpufreq_freq_transition_end() → arch_set_freq_scale()     :449-451
                 │
                 ▼   arm64: #define arch_set_freq_scale topology_set_freq_scale
                             arch/arm64/include/asm/topology.h:23
  topology_set_freq_scale()          drivers/base/arch_topology.c:130-151
       │  WARN_ON_ONCE(!cur_freq || !max_freq) → return        :136-137
       │  if (supports_scale_freq_counters(cpus)) return;      :144
       │  scale = (cur_freq << SCHED_CAPACITY_SHIFT) / max_freq;   :147
       │  for_each_cpu(i, cpus) per_cpu(arch_freq_scale, i) = scale;  :149-150
       ▼
  per_cpu(arch_freq_scale)           drivers/base/arch_topology.c:127
       │
       ▼  调度器读它算 util；schedutil 反过来用它决定基准频率
  arch_scale_freq_invariant() → topology_scale_freq_invariant()
       arch/arm64/include/asm/topology.h:25 / drivers/base/arch_topology.c:36
       │
       ▼
  get_next_freq() 的基准选择        kernel/sched/cpufreq_schedutil.c:167-168
```

### B7.1 三个必须一起看的要点

**1. arm64 上 FIE 有两个来源，cpufreq 只是其中一个。**
`topology_set_freq_scale()` 在 `supports_scale_freq_counters(cpus)`
为真时**直接 return**（`drivers/base/arch_topology.c:144`），
注释（`:139-143`）明说：如果启用了基于计数器的 FIE
（arm64 的 AMU —— Activity Monitors Unit），就**不要用 cpufreq 的信息**，
改由 `arch_scale_freq_tick` 更新。
⇒ **有 AMU 的 arm64 平台上，`arch_set_freq_scale()` 这条路是空转的。**

**2. 静态键只在 `!setpolicy` 时打开（`:2902`）。**
硬件自治驱动（`setpolicy`）下 cpufreq 不知道真实频率，
所以不能声称支持频率不变性。

**3. `scale` 的计算（`:147`）用的是 `cpuinfo.max_freq` 做分母**，
不是 `policy->max`。所以**用户写 `scaling_max_freq` 限频不会改变 scale 的量程**，
只会让实测 scale 上不去 —— 这是正确行为。

### B7.2 这条链断在哪会有什么症状

| 断点 | 症状 |
|---|---|
| 静态键没开（`setpolicy` 驱动） | schedutil 用 `policy->cur` 做基准，**低频时容易起不来**（正反馈迟滞） |
| 驱动不调 `arch_set_freq_scale`（不走两条标准路） | `arch_freq_scale` 恒为 `SCHED_CAPACITY_SCALE`，**调度器认为 CPU 一直满速** |
| `cpuinfo.max_freq` 为 0 | `WARN_ON_ONCE`（`drivers/base/arch_topology.c:136`）打一次然后彻底不更新 |
| AMU 可用但校准失败 | cpufreq 这条路被 `:144` 挡住，AMU 那条路也不准 ⇒ **两边都失效** |

---

# Part C · 评价

⚠️ **先说证据强度。** 在 `drivers/cpufreq/cpufreq.c`、`cpufreq_governor.c`、
`cpufreq_stats.c`、`freq_table.c`、`include/linux/cpufreq.h`、
`kernel/sched/cpufreq_schedutil.c` 六个文件上跑
`grep -rn 'FIXME\|TODO\|REVISIT\|XXX\|HACK'` —— **零输出**。
唯一一处自陈的将就是 schedutil 那个假带宽注释
（`kernel/sched/cpufreq_schedutil.c:607-610`，§A5.1）。

⇒ **§C2 列的十条全部是本套文档自己的判断，不是维护者的自陈**，
读者完全可以不同意。这一点会记进 99 §5.3。

---

## §C1 三个硬失败点

核心里有 **3 处真实的 `BUG_ON`**（第 4 处匹配在 `:1497` 是解释性注释，不是代码）：

| # | 位置 | 条件 | 性质 |
|---|---|---|---|
| 1 | `drivers/cpufreq/cpufreq.c:2989` | `!cpufreq_global_kobject` | **初始化失败** —— sysfs 根目录建不出来 |
| 2 | `drivers/cpufreq/cpufreq.c:349` | `irqs_disabled()` | **契约违反** —— 在关中断下发转换通知 |
| 3 | `drivers/cpufreq/cpufreq.c:1499` | `__cpufreq_driver_target()` 返回非 0 | **业务失败** |

### C1.1 为什么第 3 个是问题

前两个是内核里常见的用法：**第 1 个是启动期不可恢复**，
**第 2 个是抓调用者的错**（这类 `BUG_ON` 的目的是让 bug 立刻可见，
因为在关中断下走 SRCU 通知链会以更难诊断的方式坏掉）。

第 3 个不一样。上下文（`:1484-1503`，见 §A2.5）：

```c
		ret = __cpufreq_driver_target(policy, old_freq - 1,
					      CPUFREQ_RELATION_L);
		...
		BUG_ON(ret);
		pr_info("%s: CPU%d: Running at unlisted initial frequency: %u kHz, changing to: %u kHz\n",
			__func__, policy->cpu, old_freq, policy->cur);
```

**一次调频失败就让整台机器停机。** 而调频失败的原因可以完全是外部的：
regulator 拿不到、时钟源切换超时、固件不响应。
这三种情况在启动早期都是可能发生的**暂态**。

⚠️ **没有缓解事实。** 这段代码只在驱动带
`CPUFREQ_NEED_INITIAL_FREQ_CHECK` 时执行，而这个标志在 `drivers/cpufreq/`
下有 **25 个驱动**在用（`grep -rl` 命中 26 个文件，减去核心自己），
其中包括 arm64 的三个主力：`cpufreq-dt`、`scmi-cpufreq`、`qcom-cpufreq-hw`
（行号见 §A2.5 第 3 点）。
⇒ **这是 arm64 上真实可达的一条死机路径**，不是理论风险。

真正的缓解只有一条：能走到 `BUG_ON` 的前提是
"启动频率不在频率表里"（`:1484-1490` 的判据），
而正常固件交接的频率通常是表里的某一档 ⇒ **这个 `if` 本身很少为真**。
风险是"少见 × 后果极重"，不是"不可能"。

同一段里的 `pr_info`（`:1500`）**是核心里唯一一条 `pr_info`**
（其余都是 `pr_debug` / `pr_warn` / `pr_err`），
说明作者认为"启动频率不在表里"这件事值得让用户看见。

---

## §C2 十条判定的设计债

### C2.1 隐式数据传递：`cached_resolved_idx`

`__resolve_freq()`（`:543`）写 `policy->cached_resolved_idx`，
`__cpufreq_driver_target()`（`:2333`）读它，中间隔了 30 行和三个分支。
**函数返回值只给了频率，真正要用的下标走 struct 字段**。
后果：`:2608` 那句 `policy->cached_target_freq = UINT_MAX`
成了一个必须记住的手动作废动作 —— 忘了就会用到过期下标。

### C2.2 一个 int 的正负号编码两种互斥状态

`cpufreq_fast_switch_count`（`:466`）。§B4.1 已述。
紧凑，但代价是：**读代码时必须先找到 `:462-465` 那段注释才能理解任何一处使用**。
两侧的比较符号（`>= 0` vs `> 0`）差一个等号，含义完全不同（`:502` vs `:2051`）。

### C2.3 同一件事，两侧的失败可见性不对称

| 动作 | 失败表现 |
|---|---|
| 注册转换通知被 fast_switch 挡住 | `-EBUSY`，**调用者知道** |
| 启用 fast_switch 被通知挡住 | 一条 `pr_warn`，**调用者不知道**（函数返回 void） |

`cpufreq_enable_fast_switch()` 返回 `void`（`:494`），
所以驱动**没有办法**知道自己请求的加速通道有没有拿到。
唯一的检查方式是回读 `policy->fast_switch_enabled`。

### C2.4 全局单例：异构 SoC 只能一个驱动

`-EEXIST`（`:2882`）。大小核 SoC 想给两个 cluster 用两套不同的调频机制
（比如一个走 SCMI、一个走硬件自治），**在这个框架下做不到**，
只能写一个驱动内部分流。

### C2.5 `cpufreq_suspended` 让 governor 操作静默成功

§A3.1。挂起窗口里写 `scaling_governor` 会返回成功但什么都没发生。
这不是竞态 —— 是有意设计。但**没有任何返回码或日志告诉用户"你的请求被吞了"**。

### C2.6 第三对屏障不对称

§B5.3：`cpufreq_stats_reset_table()` 有 `smp_rmb()`，
但配对的写侧（`cpufreq_stats_record_transition()` 里对 `reset_pending` 的访问）
**没有对应的写屏障**。可能是被 `WRITE_ONCE` 的顺序性掩盖了，
但和另外两对"注释互相点名"的严谨程度不一致。

### C2.7 `policy->governor == NULL` 是一个无声的终态

§A3.4。新旧 governor 都起不来 ⇒ `policy->governor = NULL`（`:2653`/`:2656`），
频率从此不动。痕迹只有一条 `pr_debug("starting governor %s failed\n")`（`:2649`）
—— **而且这条消息里打的是 `policy->governor->name`，
在赋 NULL 之前打，所以打出的是失败的那个新 governor 的名字，
不是最终状态**。用户看到的现象是"`scaling_governor` 读出来是空"。

### C2.8 关键诊断信息落在 `pr_debug` 上

三处：`cpufreq_out_of_sync()`（`:1724`，频率失步）、
PRECHANGE 里的认知修正（`:366-369` 附近）、
`handle_update` 的触发记录（`:1160`）。
`drivers/cpufreq/cpufreq.c` 里一共 **43 处 `pr_debug`**，
**默认全部不可见**。⇒ 07 篇必须先教怎么打开它们。

### C2.9 `BUG_ON(ret)`：见 §C1.1

### C2.10 已废弃的 `->target()` 仍占据主路径

`:2319-2327`。核心在每次调频时都要判一次
`if (cpufreq_driver->target)`，而这条路上还有一个只为老驱动存在的
`relation &= ~CPUFREQ_RELATION_E`（`:2324` 附近）。
`cpufreq.h` 里注释已标注这个回调 deprecated，但**核心没有给出移除路径**。

---

## §C3 十条值得学的设计

### C3.1 单一漏斗

`cpufreq_set_policy()`（§B2）是 static 的，只有三个调用者。
**"改极限"和"换 governor"这两件看起来无关的事共用一个入口**，
好处是 `verify()` 校验、频率表对齐、缓存作废这三个动作
**不可能被任何一条路径遗漏**。

### C3.2 用 freq_qos 而不是自己做聚合

用户 sysfs、thermal、其他内核用户各自提交请求，
框架负责取最严格的那个（§B3）。
好处：**新增一个限频来源不需要改 cpufreq 一行代码**。
对比反例：如果核心自己维护 `user_min` / `thermal_min` 两个字段，
每加一个来源都要改聚合逻辑。

### C3.3 注册时一次性校验回调组合

§A1.2 那六道校验，特别是 `!x != !y` 的成对检查。
**把"运行期崩溃"换成"注册期返回 `-EINVAL`"**，
而且校验点只有一个 —— 后面所有路径都可以假定组合合法。

### C3.4 轻量拆除 / 快速恢复

`->offline()` / `->online()` 这一对（§A7.2、§A2.3）。
唯一目的是**跳过频率表重建**。CPU 热插拔在
调频 + 空闲注入的场景下很频繁，这个优化是实打实的。
而且设计得很干净：驱动**不提供**这对回调时，行为退化成完整拆除，语义不变。

### C3.5 三个 cpumask 的职责分离

`related_cpus`（硬件共享域，不随热插拔变）/
`cpus`（当前在线，随时变）/
`real_cpus`（物理存在，是释放的引用计数）。
三个都是必要的，而且 §A7.3 证明了它们**不能互相替代**：
用 `cpus` 判断能不能释放 policy 会在所有 CPU 下线时误释放。

### C3.6 补偿式一致性

出错时不回滚状态，而是**再发一对相反的通知**（§A6.2 的 `:2283-2288`、
§B6.2 的 `:1730-1731`）。
这在"订阅者已经被通知过"的场景下是唯一可行的办法 ——
你没法收回一个已经发出去的通知，只能再发一个把它抵消。

### C3.7 每对屏障都在注释里点名对方

§B5 的前两对。`cpufreq_governor.c:253-257` 和 `:289-292`、
`cpufreq_schedutil.c:839-845` 和 `:89-96`。
**这是内核里少见的好习惯**：屏障最容易在重构中被误删，
注释里写明"我在和谁配对"让后来人不敢动。

### C3.8 `dbs_update()` 对四种测量失真各有对策

§A4.3。特别是突发唤醒那一段（`:181-206`）：
它不只是"复用上次的值"，还**主动把 `prev_load` 清 0** 防止长期误判。
一个只考虑正确性的实现不会想到这一层。

### C3.9 静态键做 FIE 开关

`cpufreq_freq_invariance`（`:62`）+ `static_branch_likely()`（`:65`）。
读侧在调度器热路径上，用静态键意味着**关闭时是一条 nop**，
不是一次内存读加分支。

### C3.10 把驱动的特殊需求外化成标志而不是回调

`CPUFREQ_NEED_UPDATE_LIMITS`（去重时不许短路）、
`CPUFREQ_GOV_STRICT_TARGET`（严格照目标频率设）、
`CPUFREQ_ASYNC_NOTIFICATION`（我自己发通知）、
`CPUFREQ_NEED_INITIAL_FREQ_CHECK`（启动时校准）。
每一个都对应一处 `if (flags & ...)` 而不是一个可选回调。
好处：**核心的控制流是可见的**，读一遍就知道有几种变体；
反例是回调式扩展 —— 你得把所有驱动读一遍才知道有哪些行为。

---

## §C4 一句话总结

cpufreq 的核心难点**不在算法**（算法在 governor 里，而且很简单），
**而在把三类互斥的约束同时满足**：

| 约束 | 来自 |
|---|---|
| 调频决策必须在**不能睡**的调度器上下文里做出 | schedutil / 调度器 |
| 调频动作可能**必须睡**（i2c regulator、固件往返） | 硬件 |
| 极限来自**多个互不知情的方**，且随时变 | 用户 + thermal + 其他 |

三条路（§A4 / §A5 / §A6）、两级跳板（irq_work → work/kthread）、
freq_qos 聚合、三对屏障，**全部是为了同时满足这三条约束而存在的**。
理解了这一点，剩下的代码就只是细节。

---

## §C5 去哪一篇

| 你想要的 | 去处 |
|---|---|
| 全貌 / 复习入口 | `00-overview.md` |
| 查 API 和文件位置 | `01-api.md` |
| 结构体与三个 cpumask | `02-datastructures.md` |
| governor 家族与驱动家族 | `04-extensions.md` |
| 可抄的骨架 | `05-usage.md` |
| DTS 写法 | `06-devicetree.md` |
| 出问题怎么查 | `07-debug.md` |
| 大白话解释难点 | `08-feynman.md` |
| 每条论断的证据等级 | `99-evidence.md` |
