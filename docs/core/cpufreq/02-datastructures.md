---
title: 数据结构
---

# cpufreq 02 —— 核心数据结构与关系

本篇只讲**结构体的字段、它们之间的指针关系、以及全局状态和锁**。
函数在 `01-api.md`，执行流程在 `03-internals.md`。

所有行号指向 6.1.158 的原始文件，可以直接 `sed -n` 打开核对。

| 你想搞清楚的事 | 去哪一节 |
|---|---|
| policy 到底代表一颗 CPU 还是一组 CPU | §1.1 |
| driver 和 governor 是不是 device/driver 那种关系 | §1.2 |
| 为什么 governor 的参数要单独一层引用计数 | §1.3 |
| cpufreq 的 sysfs 目录挂在哪个 kobject 下 | §1.4 |
| `struct cpufreq_policy` 每个字段是什么 | §2 |
| `struct cpufreq_driver` 与它的 flags | §3 |
| governor 侧的四个结构体 | §4 |
| 频率表、统计、通知参数 | §5 |
| 全局变量和五把锁 | §6 |
| 一张总图 | §7 |
| 一次调频的数据流 | §8 |

---

## §1 先分清四组关系

这一节的目的是**先否掉四个错误类比**。这四条是 cpufreq 最容易被想错的地方，
每一条都给出"为什么不会发生"的代码依据。

### 1.1 `cpufreq_policy` 不是"每颗 CPU 一个"

**错误类比**：一颗 CPU 一个 policy，`/sys/devices/system/cpu/cpu0/cpufreq/` 就是 cpu0 的。

**实际**：**policy 是"共享同一个调频域的一组 CPU"**。
一个 4 大核 + 4 小核的 arm64 SoC 通常只有 **2 个 policy**。
`/sys/.../cpu0/cpufreq/` 和 `/sys/.../cpu1/cpufreq/` 很可能是**同一个目录的两个链接**。

证据：policy 里有**三个** cpumask，而不是一个 `cpu` 字段就完事
（`include/linux/cpufreq.h:60-62`）：

```c
	cpumask_var_t		cpus;	/* Online CPUs only */
	cpumask_var_t		related_cpus; /* Online + Offline CPUs */
	cpumask_var_t		real_cpus; /* Related and present */
```

| mask | 含义 | 谁在用 |
|---|---|---|
| `related_cpus` | **这个调频域的全部成员**，不管在线不在线 | `related_cpus` sysfs 属性；判断一颗 CPU 归哪个 policy |
| `cpus` | 其中**当前在线**的那些 | `affected_cpus` sysfs 属性；governor 采样和下发的实际对象 |
| `real_cpus` | `related_cpus` 中**物理存在**（present）的那些 | 决定 policy 的 sysfs 链接建在哪几个 CPU 目录下 |

另有一个 `cpu` 字段（`:66`），注释写得很清楚：
`/* cpu managing this policy, must be online */` ——
**它是"当前由哪颗 CPU 代表这个 policy 干活"，不是"这个 policy 属于哪颗 CPU"**。
CPU 热插拔时这个字段会换人，`related_cpus` 不变。

⚠️ 由此产生的最常见误判：`cpufreq_cpu_get(3)` 和 `cpufreq_cpu_get(2)` 很可能
返回**同一个指针**。想"只调 cpu3 的频率"在硬件上根本做不到 —— 见 `08-feynman.md` §1。

三个 mask 的填充时机和顺序在 `03-internals.md` §A2 逐行讲。

### 1.2 `cpufreq_driver` 和 `cpufreq_governor` 不是 device/driver 关系

**错误类比**：driver 是"设备驱动"，governor 是"策略驱动"，两者像 device↔driver 那样匹配绑定。

**实际**：**它们是分层关系，不是匹配关系**：

| 维度 | `cpufreq_driver` | `cpufreq_governor` |
|---|---|---|
| 全系统能有几个 | **只有一个**（全局变量 `cpufreq_driver`，`drivers/cpufreq/cpufreq.c:58`） | 多个（链表 `cpufreq_governor_list`，`:47`） |
| 每个 policy 用几个 | 同一个（就是那唯一的） | 一个，可运行期切换 |
| 职责 | **怎么把频率写进硬件** | **该用多少频率** |
| 谁调谁 | 被核心调用 | 调用核心的 `cpufreq_driver_target()` 等 |
| 注册进哪 | 全局单指针 | 全局链表 |

"为什么不会有两个 driver"的代码依据：`cpufreq_register_driver()` 里发现
`cpufreq_driver` 已非空就直接返回 `-EEXIST`
（`drivers/cpufreq/cpufreq.c` 的 `cpufreq_register_driver`，参见 `03-internals.md` §A1）。

这条约束的实际后果：**一颗芯片上不能同时用两种调频机制**。
如果一个 SoC 的大核走 SCMI、小核走裸 clk，也必须写成**一个** driver 内部分支，
不能注册两个。这是 `04-extensions.md` §2 那张驱动家族表的前提。

### 1.3 governor 的参数对象和 policy 生命周期**不同步**

**错误类比**：`sampling_rate` 存在 policy 里，policy 没了参数就没了。

**实际**：多个 policy 可以**共用一份** governor 参数。所以参数对象自己带引用计数：

```c
struct gov_attr_set {          /* include/linux/cpufreq.h:657-662 */
	struct kobject kobj;
	struct list_head policy_list;
	struct mutex update_lock;
	int usage_count;
};
```

`usage_count` 就是"有几个 policy 在共用这组参数"。
`gov_attr_set_put()` 返回**剩余引用数**，为 0 时调用者才释放
（`drivers/cpufreq/cpufreq_governor_attr_set.c`）。

共用还是独享由 `have_governor_per_policy()` 决定，它只看驱动有没有设
`CPUFREQ_HAVE_GOVERNOR_PER_POLICY` flag（`drivers/cpufreq/cpufreq.c:105-110`）。
sysfs 目录也跟着分岔：`get_governor_parent_kobj()`（`:113-121`）在共用时返回
**全局** kobject，独享时返回 policy 自己的 kobject。

⚠️ 于是同一个参数名会出现在两个不同位置：
`/sys/devices/system/cpu/cpufreq/ondemand/sampling_rate`（共用）
vs `/sys/devices/system/cpu/cpufreq/policy0/ondemand/sampling_rate`（独享）。
**写脚本时不能假定路径** —— `07-debug.md` §1.3 有一份适配两种布局的写法。

注意这一层**只对采样型 governor（ondemand / conservative）存在**。
schedutil 的参数（`rate_limit_us`）走的是同一套 `gov_attr_set`
（`kernel/sched/cpufreq_schedutil.c`），但 performance / powersave 根本没有参数。

### 1.4 cpufreq 没有自己的总线；policy 的目录挂在 CPU device 下

**错误类比**：既然有 `/sys/.../cpufreq/policy0/`，那 policy 应该是个 `struct device`。

**实际**：policy 里只有一个裸 `struct kobject kobj`（`include/linux/cpufreq.h:94`），
**不是 `struct device`**。全框架没有 `bus_type`：

```bash
grep -n 'bus_type' include/linux/cpufreq.h drivers/cpufreq/cpufreq.c    # 无输出
```

| 问题 | 答案 |
|---|---|
| policy 是 `struct device` 吗 | ✗ 只是 `kobject` |
| 会被 driver 匹配绑定吗 | ✗ 没有 bus，没有 match，没有 probe |
| 那 `scaling_governor` 这些属性是什么 | `struct freq_attr`（`:293-297`），一个**自制的属性类型**，不是 `device_attribute` |
| 目录挂在哪 | 挂在 `cpu` 这个 subsys 的 kobject 下，并在各 CPU 目录里建**符号链接** |
| 那 cpufreq driver 是怎么被 probe 的 | **它不被 probe**。它由具体平台驱动（如 `cpufreq-dt.c` 对应的 platform device）在自己的 probe 里调 `cpufreq_register_driver()` |

最后一行是理解整个子系统入口的关键：**cpufreq 驱动不是设备驱动**。
`cpufreq-dt` 的真实形态是"一个 platform driver 在 probe 里注册了一个 cpufreq driver"，
两层要分开看 —— `03-internals.md` §A1 和 `08-feynman.md` §3 都讲这一点。

另有一个**真的** device 出现在这里：policy 可以注册成 thermal cooling device
（`policy->cdev`，`include/linux/cpufreq.h:164`），那是 thermal 子系统的对象，
不是 cpufreq 的 —— 边界见 `04-extensions.md` §6。

---

## §2 `struct cpufreq_policy`（`include/linux/cpufreq.h:58-168`）

整个子系统的中心对象，111 行、30 多个字段。按用途分七组看。

### 2.1 我管哪些 CPU

| 字段 | 说明 |
|---|---|
| `cpus` · `related_cpus` · `real_cpus` | 见 §1.1 |
| `cpu` | 当前代表这个 policy 的 CPU（必须在线） |
| `shared_type` | `CPUFREQ_SHARED_TYPE_*`（`:192-195`）。**注释明写 `/* Only for ACPI */`（`:191`）⇒ arm64 DT 路径不用** |

### 2.2 频率范围与当前值（单位统一是 kHz）

| 字段 | 说明 |
|---|---|
| `cpuinfo` | `struct cpufreq_cpuinfo`（`:50`）：`min_freq` / `max_freq` / `transition_latency` —— **硬件能力，注册后不再变** |
| `min` · `max` | **当前生效的限制**，是"硬件范围 ∩ 用户设置 ∩ 所有 freq_qos 请求"的结果 |
| `cur` | 当前频率。注释说明只有用 governor 时才需要维护（`:73-74`） |
| `suspend_freq` | 挂起前要切到的频率，0 表示不切 |

**`cpuinfo.max_freq` 和 `max` 的区别是这个子系统最常被问的问题之一**：
前者是"这颗核最高能跑多少"，后者是"现在允许跑多少"。
热限制、`freq_qos`、用户写 `scaling_max_freq` 都只改后者。
对应到 sysfs 就是 `cpuinfo_max_freq` vs `scaling_max_freq`（`07-debug.md` §1.1）。

### 2.3 策略与 governor

| 字段 | 说明 |
|---|---|
| `policy` | `CPUFREQ_POLICY_POWERSAVE` / `_PERFORMANCE` / `_UNKNOWN`（`:565-572`）。**只有 `setpolicy` 型驱动用**，其余驱动这个字段不参与决策 |
| `last_policy` | 热插拔下线前的 `policy`，上线后恢复 |
| `governor` | 指向当前 governor |
| `governor_data` | governor 的私有数据，由 governor 的 `init()` 分配、`exit()` 释放。核心只负责搬这个指针 |
| `last_governor[]` | 下线前用的 governor 名字（字符串，不是指针 —— **因为 governor 模块可能已卸载**） |

`policy` 和 `governor` 两个字段**互斥使用**：驱动实现 `setpolicy` 时核心
根本不启动 governor。这就是 x86 上 `intel_pstate` 那种"看不到 governor"的形态
—— 本套裁掉了 x86，但机制在 `04-extensions.md` §2.2 提了一句。

### 2.4 限制来源：freq_qos

| 字段 | 说明 |
|---|---|
| `constraints` | `struct freq_constraints` —— **限制请求的聚合器**，实体在 `include/linux/pm_qos.h` |
| `min_freq_req` · `max_freq_req` | policy 自己持有的两个 qos 请求，代表 `scaling_min_freq`/`scaling_max_freq` 的用户设置 |
| `nb_min` · `nb_max` | 两个 `notifier_block`：**别人改了 qos，通过这两个回调通知到 policy** |

这一组是 cpufreq 的**限制系统**，也是 07 篇最容易查错的地方：
`scaling_max_freq` 的写入**不是直接改 `policy->max`**，而是提交一个 qos 请求；
最终值由 `freq_qos` 聚合所有请求算出，再经 `nb_max` 回调落到 `policy->max`。
所以"我写了 `scaling_max_freq` 但没生效"通常意味着**有别的 qos 请求更严格**
（典型是 thermal）。完整链路在 `03-internals.md` §B3。

### 2.5 频率表

| 字段 | 说明 |
|---|---|
| `freq_table` | 指向频率表数组，`CPUFREQ_TABLE_END` 结尾 |
| `freq_table_sorted` | `enum cpufreq_table_sorting`（`:44`）：未排序 / 升序 / 降序。**决定查表用二分还是线性扫** |
| `efficiencies_available` | 表里有没有标记过低效频率，决定 `CPUFREQ_RELATION_E` 这个 flag 是否被尊重（`:124-129` 的注释原话） |

### 2.6 切换机制与同步

| 字段 | 说明 |
|---|---|
| `fast_switch_possible` | **驱动**设置：我能在任意成员 CPU 上无锁改频 |
| `fast_switch_enabled` | **governor** 设置（通过 `cpufreq_enable_fast_switch()`）：我要走这条路 |
| `strict_target` | 当前 governor 设了 `CPUFREQ_GOV_STRICT_TARGET`（`:118-122`） |
| `transition_delay_us` | 驱动建议的最小切换间隔，0 = 无偏好（`:131-135`） |
| `dvfs_possible_from_any_cpu` | 能否代替**别的 policy** 的 CPU 调频（`:138-144` 注释解释了为什么放在 policy 而不是 driver 里：**不想让调度器热路径去访问另一个结构体**） |
| `cached_target_freq` · `cached_resolved_idx` | `cpufreq_driver_resolve_freq()` 的一级缓存 |
| `transition_ongoing` · `transition_lock` · `transition_wait` · `transition_task` | 两段式通知协议的状态机（`:151-155`）。`transition_task` 存着"谁在做这次切换"，用来**检测同一任务重入** |
| `rwsem` | 见 §6 |

两个 `fast_switch_*` 字段的分工（`:107-116` 的注释是权威解释）：
**`possible` 是硬件/驱动能力，`enabled` 是 governor 的选择**。
两者都为真才走无锁路径。这也解释了为什么切到 ondemand 之后
`fast_switch` 就不再被用 —— 见 `08-feynman.md` §5。

### 2.7 其余

| 字段 | 说明 |
|---|---|
| `clk` | 可选：驱动把 CPU 时钟存在这里，`cpufreq_generic_get()` 就靠它读频率 |
| `update` | 一个 `work_struct`（`:83-84`），注释说明用途：**在中断上下文里需要 `update_policy()` 时改走工作队列** |
| `policy_list` | 挂进全局 `cpufreq_policy_list` |
| `kobj` · `kobj_unregister` | sysfs 对象 + 释放同步的 completion |
| `stats` | 指向 `struct cpufreq_stats`（见 §5.2），`CONFIG_CPU_FREQ_STAT=n` 时为空 |
| `driver_data` | 驱动私有数据（和 `cpufreq_driver.driver_data` **不是一个东西**，后者是全局的） |
| `cdev` | thermal cooling device（见 §1.4） |

⚠️ **同名不同物提醒**：`policy->driver_data`（每 policy）
vs `cpufreq_driver.driver_data`（每系统）。
`cpufreq_get_driver_data()` 拿的是**后者**（`01-api.md` §2.3）。

---

## §3 `struct cpufreq_driver`（`include/linux/cpufreq.h:324-402`）

**全系统唯一**的那个对象（§1.2）。字段分三类：身份、回调、能力标志。

### 3.1 身份

| 字段 | 说明 |
|---|---|
| `name[]` | 会直接出现在 `/sys/.../scaling_driver` 里，长度上限 `CPUFREQ_NAME_LEN`（`:38`） |
| `flags` | `u16`，见 §3.3 |
| `driver_data` | 全局私有数据，由 `cpufreq_get_driver_data()` 取（不是 per-policy 的那个，见 §2.7） |
| `attr` | `struct freq_attr **`：驱动想额外挂的 sysfs 属性数组，`NULL` 结尾。通用值 `cpufreq_generic_attr` 只含 `scaling_available_frequencies` |
| `boost_enabled` | 当前 boost 状态（是**状态**不是能力；能力看有没有 `set_boost` 或调过 `cpufreq_enable_boost_support()`） |

### 3.2 回调分组

回调的完整逐个说明在 `01-api.md` §3.2。这里只讲**分组约束**，因为约束是结构性的：

```
必须实现 ──┬── init(policy)                「这颗 CPU 归我管，范围/表/延迟填好」
           └── verify(policy_data)         「用户提的范围合不合法」

四选一 ────┬── setpolicy(policy)           硬件自己决策 → 核心不启 governor
           ├── target_index(policy, idx)   现代常规做法，核心代发通知
           ├── target(policy, freq, rel)   /* Deprecated */（:338 原注释）
           └── fast_switch(policy, freq)   可与 target_index 并存，调度上下文用
                └── adjust_perf(...)       fast_switch 的抽象性能值版本

成对出现 ──┬── get_intermediate(policy, idx)
           └── target_intermediate(policy, idx)
              :353-367 注释明确：只对「有 target_index 且没设 ASYNC_NOTIFICATION」
                的驱动有效；get_intermediate 返回 0 表示这次不用中间频率

可选 ──────── get · update_limits · bios_limit(x86) · online · offline · exit
              suspend · resume · ready · set_boost · register_em
```

`target_index` 和 `fast_switch` **不是二选一而是可以都有**：
前者给 governor 的常规路径，后者给调度器热路径。arm64 上的
`cpufreq-dt`、`scmi-cpufreq` 都是两个都实现（`04-extensions.md` §2）。

`register_em` 的存在位置很讲究 —— `:397-400` 的注释说明它在
**policy 初始化完成之后、governor 启动之前**被调用，
因为能量模型要等频率表齐了才能建，而 schedutil 启动时又要用它。

### 3.3 七个能力标志（`:412-451`）

| flag | 值 | 含义（括注为源码注释原意） |
|---|---|---|
| `CPUFREQ_NEED_UPDATE_LIMITS` | `BIT(0)` | 目标频率**没变**但 `min/max` 变了时也要调驱动一次（驱动内部维护上下界） |
| `CPUFREQ_CONST_LOOPS` | `BIT(1)` | 调频不影响 `loops_per_jiffy`，核心不必修正它 |
| `CPUFREQ_IS_COOLING_DEV` | `BIT(2)` | 让核心**自动**把 policy 注册成 thermal cooling device（填 `policy->cdev`） |
| `CPUFREQ_HAVE_GOVERNOR_PER_POLICY` | `BIT(3)` | 多个时钟域各自独立的 governor 参数（§1.3）。注释点明用途：**同一个 governor 在不同 cluster 上用不同参数** |
| `CPUFREQ_ASYNC_NOTIFICATION` | `BIT(4)` | 驱动**自己**在 `->target()` 之外发 POSTCHANGE ⇒ 核心不代发（`01-api.md` §6.2） |
| `CPUFREQ_NEED_INITIAL_FREQ_CHECK` | `BIT(5)` | 启动时校验当前频率在不在表里；**不在且改不过去就 `BUG_ON()`** —— 注释里写明了 |
| `CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING` | `BIT(6)` | 拒绝带 `CPUFREQ_GOV_DYNAMIC_SWITCHING` 的 governor（即拒绝 ondemand/conservative/schedutil 这类） |

⚠️ `BIT(5)` 那条是全子系统**唯一会主动挂机的路径**（`BUG_ON`），
凡是设了这个 flag 的驱动，频率表和硬件实际值必须完全对得上。
`07-debug.md` §6 单列了这条。

---

## §4 governor 侧的结构体

### 4.1 `struct cpufreq_governor`（`include/linux/cpufreq.h:582-596`）

```c
struct cpufreq_governor {
	char	name[CPUFREQ_NAME_LEN];
	int	(*init)(struct cpufreq_policy *policy);
	void	(*exit)(struct cpufreq_policy *policy);
	int	(*start)(struct cpufreq_policy *policy);
	void	(*stop)(struct cpufreq_policy *policy);
	void	(*limits)(struct cpufreq_policy *policy);
	ssize_t	(*show_setspeed)(struct cpufreq_policy *policy, char *buf);
	int	(*store_setspeed)(struct cpufreq_policy *policy, unsigned int freq);
	struct list_head	governor_list;
	struct module		*owner;
	u8			flags;
};
```

**只有 11 个字段，比 driver 简单得多** —— 因为 governor 只需要回答
"该用多少频率"，下发的事全交给核心。

| flag（`:601`/`:604`） | 含义 |
|---|---|
| `CPUFREQ_GOV_DYNAMIC_SWITCHING` | "我会自己动态改频"。被 `CPUFREQ_NO_AUTO_DYNAMIC_SWITCHING` 驱动拒绝 |
| `CPUFREQ_GOV_STRICT_TARGET` | "请精确设成我要的值" ⇒ 传播到 `policy->strict_target`（§2.6） |

`LATENCY_MULTIPLIER`（`:580`）= 1000：采样型 governor 用
`transition_latency × 1000` 当默认采样周期的下限。

### 4.2 governor 参数的三件套

```
struct gov_attr_set  (:657-662)      一组参数 + 引用计数 + 自己的 kobject
    ├── kobj                          → sysfs 目录
    ├── policy_list                   → 有哪些 policy 在共用（§1.3）
    ├── update_lock                   → 保护改参数
    └── usage_count                   → 剩几个用户

struct governor_attr (:677-682)      一个参数文件
    ├── attr
    ├── show (attr_set, buf)          收的是 gov_attr_set*，不是 policy*
    └── store(attr_set, buf, count)

governor_sysfs_ops                    把 kobject 的读写路由到上面两个回调
```

关键点：**`governor_attr` 的 show/store 拿到的是 `gov_attr_set` 而不是 policy**。
这是"参数可能被多个 policy 共用"的直接后果 —— 参数不属于任何一个 policy。
改完参数要生效到每个 policy，靠遍历 `attr_set->policy_list`。

### 4.3 采样型 governor 的四层（`drivers/cpufreq/cpufreq_governor.h`）

ondemand 和 conservative 共用这一整套骨架。四个结构体分四个层级：

| 结构体 | 位置 | 层级 | 关键字段 |
|---|---|---|---|
| `struct dbs_governor` | `:125-141` | **每 governor**（全局一份） | 首字段是 `struct cpufreq_governor gov`（可 `container_of` 反查，`dbs_governor_of()` `:143`）；`gdbs_data` 是共用参数时的那一份；`gov_dbs_update` 就是"看了负载算频率"这一个策略函数 |
| `struct dbs_data` | `:38-47` | **每组参数** | 首字段是 `struct gov_attr_set attr_set`；参数本体：`sampling_rate`、`up_threshold`、`sampling_down_factor`、`ignore_nice_load`、`io_is_busy`；`tuners` 指向 governor 私有的额外参数 |
| `struct policy_dbs_info` | `:78-100` | **每 policy** | `sample_delay_ns`、`last_sample_time`、`rate_mult`、`irq_work` + `work_struct`（**两级延后**：定时器 → irq_work → 工作队列）、`is_shared`、`work_in_progress` |
| `struct cpu_dbs_info` | `:109-122` | **每 CPU** | `prev_cpu_idle` / `prev_update_time` / `prev_cpu_nice` / `prev_load`（采样基准）、`update_util`（**连调度器钩子也用**）、回指 `policy_dbs` |

四层的存在理由是**四种不同的生命周期**：
governor 全局一份、参数可能共用、policy 会热插拔、每 CPU 要各自记采样基准。
`03-internals.md` §A4 沿着一次采样把这四层串起来走一遍。

`dbs_data` 里的 `sampling_rate` 等字段是**共用参数**，
`tuners` 指向的才是各自私有的（ondemand 有 `powersave_bias`，conservative 有 `freq_step`）。
`gov_show_one_common` / `gov_show_one` 两个宏（`:63`/`:54`）的区别就在这里。

### 4.4 schedutil 的两个结构体（`kernel/sched/cpufreq_schedutil.c`）

schedutil 不用 `cpufreq_governor.h` 那一套，它自己有一套更薄的：

| 结构体 | 位置 | 关键字段 |
|---|---|---|
| `struct sugov_tunables` | `:11-14` | 只有 `rate_limit_us` **一个参数**（对比 ondemand 的五个）+ `gov_attr_set attr_set` |
| `struct sugov_policy` | `:16-38` | `update_lock`（`raw_spinlock_t`，因为在调度路径上）、`last_freq_update_time`、`freq_update_delay_ns`、`next_freq`、`cached_raw_freq`、`thread`（**一个专用内核线程**，给不能 fast_switch 的驱动用）、`work_in_progress`、`limits_changed`、`need_freq_update` |
| `struct sugov_cpu` | `:40-57` | 首字段 `struct update_util_data update_util`（就是挂给调度器的那个钩子）、`iowait_boost` + `iowait_boost_pending`、`util` / `bw_dl` / `max`、`saved_idle_calls` |

**`sugov_policy.thread` 的存在解释了一个常见困惑**：
schedutil 明明说"在调度路径上直接下发"，为什么 `ps` 里能看到
`sugov:0` 这样的线程？答案是**驱动不支持 `fast_switch` 时，
schedutil 只能把下发丢给这个专用线程**（因为调度路径不能睡）。
完整两条路的对比在 `04-extensions.md` §3、`08-feynman.md` §5。

`sugov_cpu` 里**没有** `prev_cpu_idle` 这类字段 —— schedutil 不采样，
它拿的是调度器现成的利用率。这是它和 ondemand 最本质的区别。

---

## §5 频率表、统计与辅助结构

### 5.1 频率表（`include/linux/cpufreq.h:695-700`）

```c
struct cpufreq_frequency_table {
	unsigned int	flags;
	unsigned int	driver_data; /* driver specific data, not used by core */
	unsigned int	frequency;   /* kHz - doesn't need to be in ascending order */
};
```

只有三个字段，但**两个特殊值和两个 flag 极易搞混**：

| 常量 | 值 | 位置 | 放在哪个字段 | 含义 |
|---|---|---|---|---|
| `CPUFREQ_ENTRY_INVALID` | `~0u` | `:689` | `frequency` | 这一项**跳过**，但表没结束 |
| `CPUFREQ_TABLE_END` | `~1u` | `:690` | `frequency` | 表到此为止 |
| `CPUFREQ_BOOST_FREQ` | `1 << 0` | `:692` | `flags` | 这一档是超频档，只在 boost 打开时可用 |
| `CPUFREQ_INEFFICIENT_FREQ` | `1 << 1` | `:693` | `flags` | 这一档"低效"（有更高频率但功耗不更高）⇒ 配合 `CPUFREQ_RELATION_E` 跳过 |

⚠️ `~0u` 和 `~1u` 只差 1，**写错一个整张表的语义就反了**（把 END 写成 INVALID
会让核心一直往后扫过界）。`driver_data` 字段核心**完全不看**（注释原话），
驱动可以往里塞硬件索引 —— `qcom-cpufreq-hw` 等驱动就这么用。

### 5.2 `struct cpufreq_stats`（`drivers/cpufreq/cpufreq_stats.c:15-28`）

这个结构体**不在公共头文件里**，只有 `policy->stats` 这个不透明指针对外。

| 字段 | 说明 |
|---|---|
| `total_trans` | 累计切换次数 → `total_trans` 属性（`:67`） |
| `time_in_state[]` | 每档累计驻留时间 → `time_in_state`（`:100`） |
| `trans_table[]` | N×N 的转移矩阵（从第 i 档到第 j 档几次）→ `trans_table`（`:173`） |
| `max_state` · `state_num` · `last_index` · `last_time` | 表长、有效档数、当前档、上次更新时刻 |
| `reset_pending` · `reset_time` | **延迟清零**：写 `reset` 属性（`:122`）不立刻清，而是打标记，下次更新时才真清 |

延迟清零这个设计有一处显式的内存序处理（`:50-55` 的注释 + `smp_rmb()`），
是核心里少见的显式屏障 —— `03-internals.md` §B5 讲它防的是什么重排。

四个属性通过 `sysfs_create_group()`（`:256`）挂成 `stats/` 子目录，
和 policy 自己的属性不是同一批。

### 5.3 三个小结构体

| 结构体 | 位置 | 用途 |
|---|---|---|
| `struct cpufreq_cpuinfo` | `:50-56` | 硬件能力：`max_freq` / `min_freq` / `transition_latency`（**单位是纳秒**，注释明写） |
| `struct cpufreq_policy_data` | `:176-182` | **只给 `->verify()` 用的裁剪视图**：只有 `cpuinfo`、`freq_table`、`cpu`、`min`、`max` 五个字段 |
| `struct cpufreq_freqs` | `:184-189` | 通知链参数：`policy`、`old`、`new`、`flags` |

`cpufreq_policy_data` 是一个值得注意的设计：**核心故意只给 `verify()`
看五个字段**，防止驱动在校验阶段乱动 policy 的其他状态。
写驱动时最容易在这里编译报错 —— `01-api.md` §3.4 有提醒。

`cpufreq_cpuinfo.transition_latency` 的单位是**纳秒**，
而 `transition_delay_us`（§2.6）和 `sampling_rate` 都是**微秒** ——
三个时间量三个单位，`06-devicetree.md` §3 讲 DTS 里 `clock-latency-ns` 怎么落到这里。

---

## §6 全局状态与五把锁

全部集中在 `drivers/cpufreq/cpufreq.c` 开头 100 行里。

### 6.1 全局变量

| 变量 | 行 | 说明 |
|---|---:|---|
| `cpufreq_policy_list` | `:34` | 所有 policy 的链表 |
| `cpufreq_governor_list` | `:47` | 所有已注册 governor 的链表 |
| `default_governor[]` | `:51` | 默认 governor 的**名字**（字符串），可由内核参数 `cpufreq.default_governor=` 覆盖 |
| `cpufreq_driver` | `:58` | **唯一的驱动指针**（§1.2 的依据） |
| `cpufreq_cpu_data` | `:59` | `DEFINE_PER_CPU(struct cpufreq_policy *, ...)` —— **CPU 号 → policy 的反查表**。多个 CPU 指向同一个 policy（§1.1） |
| `cpufreq_freq_invariance` | `:62` | 一个 static key：频率不变性（FIE）是否可用。`cpufreq_supports_freq_invariance()`（`:64-66`）读它 |
| `cpufreq_suspended` | `:69` | 系统是否处于挂起状态；挂起期间所有调频请求被吞掉 |
| `off` | `:95` | `disable_cpufreq()` 设的总开关。`cpufreq_disabled()`（`:96`）返回它 ⇒ 之后一切 API 返回 `-ENODEV` |
| `cpufreq_policy_notifier_list` | `:92` | `BLOCKING_NOTIFIER_HEAD` |
| `cpufreq_transition_notifier_list` | `:93` | `SRCU_NOTIFIER_HEAD_STATIC` —— **用 SRCU 而不是 blocking**，因为要能在更受限的上下文里通知 |

`has_target()`（`:71-75`）是核心里一个高频判断：**驱动实现了 target/target_index
就返回真，实现的是 `setpolicy` 就返回假**。它决定"要不要启动 governor"这条大分叉。

### 6.2 五把锁

| 锁 | 类型 | 保护什么 | 用量 |
|---|---|---|---|
| `cpufreq_driver_lock` | `DEFINE_RWLOCK`（`:60`） | `cpufreq_driver` 指针 + `cpufreq_cpu_data` 反查表 + policy 链表 | 3 处读、7 处写（都是 `_irqsave` 变体） |
| `cpufreq_governor_mutex` | `DEFINE_MUTEX`（`:104`） | governor 链表 | governor 注册/注销/查找 |
| `policy->rwsem` | `rw_semaphore`（`include/linux/cpufreq.h:105`） | **policy 的全部可变状态** | 2 处读锁、12 处写锁 |
| `policy->transition_lock` | `spinlock_t`（`:153`） | `transition_ongoing` 状态机 | 只在 `cpufreq_freq_transition_begin/end`（`drivers/cpufreq/cpufreq.c:425` 与 `:453`） |
| `gov_attr_set.update_lock` | `mutex`（`include/linux/cpufreq.h:660`） | governor 参数与共用者列表 | 参数 store 路径 |

**`policy->rwsem` 的规则写在结构体注释里（`include/linux/cpufreq.h:97-104`），
是本子系统唯一一处把锁契约写成注释的地方**，值得原文引用：

> 读 policy 结构 → `down_read`；
> 写 policy 结构**或可能把 policy 整个拿走**（如 CPU 热插拔）→ 以写模式持有。

读锁只用了 2 处、写锁 12 处 —— 这个悬殊比例说明
**核心几乎所有路径都是写路径**（sysfs store、governor 切换、热插拔、限制刷新），
真正的"只读"场景反而走的是无锁的 `cpufreq_quick_get()` 这类接口（`01-api.md` §2.1）。

**调度器热路径一把锁都不拿**：`cpufreq_driver_fast_switch()` 的整条路
（schedutil → fast_switch → 驱动写寄存器）不碰上面任何一把锁，
只靠 `sugov_policy.update_lock` 这个 `raw_spinlock_t`（§4.4）。
这是"为什么 fast_switch 不能发通知"的根本原因 —— `03-internals.md` §B4。

⚠️ **锁序**：`policy->rwsem` 在外、`cpufreq_driver_lock` 在内。
sysfs store 路径持 `rwsem` 写锁再去改反查表；反过来的顺序会死锁。
这一点源码里没有 lockdep 注解，只能靠遵守惯例。

---

## §7 结构关系总图

```
                        ┌─────────────────────────────────────┐
        全系统唯一 ───→ │  cpufreq_driver (:58 全局指针)       │
                        │  init/verify/target_index/          │
                        │  fast_switch/get/...                │
                        └──────────────┬──────────────────────┘
                                       │ 被核心调用
                                       ▼
  per-CPU 反查表                ┌──────────────────────────┐
  cpufreq_cpu_data (:59)        │   struct cpufreq_policy   │
   cpu0 ─┐                      │  (整个子系统的中心对象)   │
   cpu1 ─┼──────────────────→   │                          │
   cpu2 ─┤   （多对一！）        │  cpus/related_cpus/      │
   cpu3 ─┘                      │  real_cpus  ← §1.1       │
                                │  cpuinfo(硬件) min/max/cur│
   cpu4 ─┐                      │  freq_table ─────────────┼──→ cpufreq_frequency_table[]
   cpu5 ─┼──→ 另一个 policy      │  constraints ────────────┼──→ freq_qos（thermal 等在此提请求）
   ...   ┘   （大小核两个域）     │  stats ──────────────────┼──→ cpufreq_stats（stats/ 子目录）
                                │  kobj ───────────────────┼──→ /sys/.../cpufreq/policyN/
                                │  rwsem / transition_lock │
                                │  governor ───────┐        │
                                │  governor_data ──┼───┐    │
                                └──────────────────┼───┼────┘
                                                   │   │
                          ┌────────────────────────┘   │ （类型由 governor 自己决定）
                          ▼                            ▼
        ┌──────────────────────────────┐   ┌──────────────────────────────────┐
        │  struct cpufreq_governor      │   │ 采样型：policy_dbs_info          │
        │  链表 cpufreq_governor_list   │   │   ├── dbs_data ──→ gov_attr_set  │
        │  init/exit/start/stop/limits  │   │   │     （可被多 policy 共用）    │
        └───────────┬──────────────────┘   │   └── cpu_dbs_info[]（每 CPU）    │
                    │                       ├──────────────────────────────────┤
        六个候选实现 │                       │ schedutil：sugov_policy          │
    ┌───────────────┼───────────┐           │   ├── sugov_tunables ──→ gov_attr_set
    ▼               ▼           ▼           │   ├── sugov_cpu[]（含 update_util）
performance    ondemand     schedutil       │   └── thread（驱动不支持 fast_switch 时）
powersave      conservative  （在 kernel/sched/）└──────────────────────────────┘
userspace
```

**读图三个要点**：

1. **左上角那条"多对一"是全图最重要的一条线**：per-CPU 反查表里
   多个 CPU 指向同一个 policy（§1.1）。
2. `governor_data` 是一个 `void *`，**类型由 governor 自己决定** ——
   采样型放 `policy_dbs_info`，schedutil 放 `sugov_policy`。
   核心从不解引用它，这就是 governor 能有两套完全不同内部结构的原因。
3. `gov_attr_set` 出现在两条支路上（`dbs_data` 里和 `sugov_tunables` 里），
   **都是首字段** ⇒ 两边都用 `container_of` 反查。这是全子系统复用度最高的一个基类。

---

## §8 一次调频的数据流

从"谁发起"到"谁写寄存器"，三条路完全不同。

```
【路径 A：schedutil + fast_switch —— arm64 默认，最短】

  调度器更新利用率
      │ （每次 enqueue/dequeue/tick）
      ▼
  sugov_update_single_freq() / sugov_update_shared()
      │  用 map_util_freq() 把利用率换成频率
      │  检查 freq_update_delay_ns 速率限制
      ▼
  cpufreq_driver_fast_switch(policy, freq)
      │  不加锁、不发通知、不睡眠
      ▼
  driver->fast_switch(policy, freq)  →  写寄存器 / 写共享内存
      │
      ▼
  cpufreq_stats_record_transition()   统计仍然会记

【路径 B：schedutil + 无 fast_switch —— 多了一次线程唤醒】

  调度器 ... ─→ sugov_update_*()
      │  work_in_progress = true，唤醒 sg_policy->thread
      ▼
  sugov_work()（在专用内核线程里，可以睡）
      ▼
  __cpufreq_driver_target(policy, freq, CPUFREQ_RELATION_L)
      │  持 policy->rwsem 写锁
      ▼
  （下同路径 C 的后半段）

【路径 C：采样型 governor / sysfs 写入 —— 最长，通知齐全】

  定时器到期（dbs）  或  用户 echo 到 scaling_setspeed / scaling_max_freq
      ▼
  dbs_update() 算负载  或  freq_qos 聚合出新的 min/max
      ▼
  cpufreq_driver_target(policy, target_freq, relation)
      │  down_write(&policy->rwsem)
      ▼
  __cpufreq_driver_target()
      │  cpufreq_frequency_table_target() 把频率换成 index
      │  （可选）get_intermediate + target_intermediate 先过中间频率
      ▼
  cpufreq_freq_transition_begin()  ──→ 发 CPUFREQ_PRECHANGE 通知（SRCU 链）
      ▼
  driver->target_index(policy, index)  →  clk_set_rate / SCMI 消息 / 写寄存器
      ▼
  cpufreq_freq_transition_end()    ──→ 发 CPUFREQ_POSTCHANGE 通知
      ▼
  policy->cur 更新 + cpufreq_stats_record_transition()
```

**三条路的关键差异只有一个：能不能睡。**
路径 A 在调度器上下文里，绝对不能睡 ⇒ 不能加互斥锁、不能发通知；
路径 B 通过一个内核线程"买"到了睡眠权；
路径 C 本来就在可睡上下文里，所以协议最完整。

⚠️ 由此产生一个**实用的观测后果**：在 arm64 默认配置（schedutil + `cpufreq-dt` 或
`scmi-cpufreq`，两者都有 `fast_switch`）下走的是**路径 A**，
所以挂 `CPUFREQ_TRANSITION_NOTIFIER` 的代码**收不到任何事件**。
想观测调频只能看 `stats/` 或 tracepoint —— `07-debug.md` §4 有完整对照表。

三条路的逐行剖析分别在 `03-internals.md` §A5（A/B）和 §A6（C）。

---

## §9 命名约定速查

| 形式 | 含义 | 例 |
|---|---|---|
| `struct cpufreq_*` | 公共头文件里的类型 | `cpufreq_policy`、`cpufreq_driver` |
| `struct *_dbs_*` | 采样型 governor 专用（`dbs` = demand based switching） | `dbs_data`、`policy_dbs_info` |
| `struct sugov_*` | schedutil 专用（住在 `kernel/sched/`） | `sugov_policy`、`sugov_cpu` |
| `*_var_t` | cpumask 的可变长封装 | `cpumask_var_t cpus` |
| `gov_attr_set` / `governor_attr` | **前者是一组参数，后者是一个参数文件**。名字太像，是本子系统最容易看错的一对 | — |
| 字段 `cpuinfo.*` | 硬件能力，不变 | `cpuinfo.max_freq` |
| 字段 `min` / `max` / `cur` | 当前生效值，会变 | — |
| 后缀 `_us` / `_ns` | 单位在名字里。**`transition_latency` 是唯一一个没有后缀却是纳秒的**（§5.3） | `transition_delay_us`、`sample_delay_ns` |
| `*_possible` vs `*_enabled` | 能力 vs 选择（§2.6） | `fast_switch_possible` / `fast_switch_enabled` |
| `related_cpus` vs `cpus` vs `real_cpus` | 全部成员 / 在线 / 物理存在（§1.1） | — |

三对最容易混的名字，单独拎出来：

| 一对 | 区别 |
|---|---|
| `cpuinfo.max_freq` ↔ `max` | 硬件上限 ↔ 当前允许上限 |
| `fast_switch_possible` ↔ `fast_switch_enabled` | 驱动说能 ↔ governor 说要 |
| `gov_attr_set` ↔ `governor_attr` | 一组参数（带引用计数）↔ 一个参数文件（带 show/store） |

---

## §10 去哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| 查 API 和文件位置 | `01-api.md` |
| 看这些结构体在什么时刻被填、被谁填 | `03-internals.md` Part A |
| 看锁的临界区边界和 freq_qos 链路 | `03-internals.md` Part B |
| 看 governor 的三条路线、驱动的家族分类 | `04-extensions.md` |
| 抄骨架 | `05-usage.md` |
| DTS 里的 OPP 表怎么变成 `freq_table` | `06-devicetree.md` |
| sysfs 属性和结构体字段的对应关系 | `07-debug.md` §1 |
| 大白话讲 policy 为什么不是每 CPU 一个 | `08-feynman.md` §1 |
| 每条结论的证据等级 | `99-evidence.md` |
