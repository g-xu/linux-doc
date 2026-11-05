---
title: 数据结构
---

# 02 核心数据结构：regulator

> 基线 Linux 6.1.158（`f6e38ae624cf`），arm64-only。
> 本篇讲"有哪些结构、字段是什么意思、它们怎么互相指"。
> API 清单在 01 篇，代码流程在 03 篇。

## 本篇怎么用

| 你想做的事 | 去哪一节 |
|---|---|
| 分不清 `regulator` 和 `regulator_dev` | §1.0（第一组关系） |
| 分不清 `regulator_desc` 和 `regulation_constraints` | §1.0（第二组关系） |
| 想知道一颗 PMIC 在设备模型里长什么样 | §1.0（第三组关系）+ §2 |
| 查某个结构有哪些字段 | §3（消费者侧）/ §4（提供者侧）/ §5（板级） |
| 看结构之间怎么指 | §6 关系图 |
| 看一次 `regulator_enable` 的数据流 | §7 |
| 全局链表和锁 | §8 |
| 命名约定（`rdev` / `rstate` / `_uV`） | §9 |

---

## §1.0 先分清四组关系

本子系统的结构名字**成对地像**，而含义完全不同。
先把四组错误类比否掉，后面的图才看得懂。

### 第一组：`struct regulator` ≠ `struct regulator_dev`

**错误直觉**：`regulator` 是本体，`regulator_dev` 是它的 device 部分
（像 `spi_master` 和它内嵌的 `struct device` 那样）。

**实际上**：两者是**消费者句柄**和**硬件本体**的关系，
一个硬件本体对应 **N 个**消费者句柄。

| | `struct regulator` | `struct regulator_dev` |
|---|---|---|
| 定义在哪 | `drivers/regulator/internal.h:45-59` | `include/linux/regulator/driver.h:611-653` |
| 对外可见吗 | **不可见** —— 消费者只拿到不透明指针 | 可见（提供者要用它的字段） |
| 谁创建 | `create_regulator`（`drivers/regulator/core.c:1868`），由 `regulator_get` 触发 | `regulator_register`（`core.c:5508`） |
| 一个硬件有几个 | **N 个**，每个消费者一个 | 1 个 |
| 内嵌 `struct device` 吗 | 没有，只有一个指向消费者 `dev` 的指针（`internal.h:46`） | 有，`dev` 字段（`driver.h:631`） |
| 谁的引用计数 | `enable_count`（`internal.h:52`）—— **这个消费者** enable 了几次 | `use_count`（`driver.h:614`）—— **所有消费者**合计 |
| 注释怎么说 | "One for each consumer device"（`internal.h:37`） | "One for each regulator"（`driver.h:605`） |

**这一组是本子系统所有困惑的源头**。记住一句话：
**`regulator` 是"我对这路电源的看法"，`regulator_dev` 是"这路电源本身"。**

由此可以直接解释 01 篇 §2.2 里那条反直觉的规则 ——
`regulator_disable` 减的是 `regulator->enable_count`（自己那本账），
硬件真正关不关看的是 `rdev->use_count`（总账）归零没有。
代码在 `core.c:3012` 起的 `_regulator_disable`。

命名上也有线索：**核心代码里局部变量叫 `regulator` 的是句柄，
叫 `rdev` 的是本体**（§9）。

### 第二组：`regulator_desc` ≠ `regulation_constraints`

**错误直觉**：一个是"描述"，一个是"约束"，都是配置，差不多。

**实际上**：**前者是"硬件能做什么"，后者是"这块板子允许做什么"**，
来源、生命期、可写性全都不同。

| | `struct regulator_desc` | `struct regulation_constraints` |
|---|---|---|
| 定义 | `include/linux/regulator/driver.h:361-434` | `include/linux/regulator/machine.h:157-222` |
| 语义 | 芯片的**能力**：几档电压、寄存器在哪、步长多少 | 板子的**授权**：允许调到多少、允许改什么 |
| 谁写 | 芯片驱动，通常 `static const` 编进内核 | **DTS**（`of_regulator.c:80` 的 `of_get_regulation_constraints`） |
| 换块板子会变吗 | 不会（同一颗芯片全世界一样） | 会（每块板子的电路不同） |
| 挂在哪 | `rdev->desc`（`driver.h:612`），**只存指针** | `rdev->constraints`（`driver.h:632`），核心 kzalloc |
| 生命期 | 必须比 rdev 活得久 | 由核心管理，`regulator_unregister` 时释放 |

由此可推出 01 篇 §5.1 里那一串 `-EPERM` 的来源：
**`desc` 里有能力、`constraints` 里没授权 → `-EPERM`**。
判据函数是 `regulator_ops_is_valid`（`core.c:117`），
它查的是 `rdev->constraints->valid_ops_mask`，
**完全不看 `desc`**。

反过来也成立：DTS 里给了授权、驱动却没实现对应回调 →
在注册期就被 `set_machine_constraints`（`core.c:1471`）挡掉。
这条路径在 03 篇 §A 展开。

### 第三组：regulator **不是**一条总线上的设备

**错误直觉**：仿照 i2c/spi，PMIC 是控制器、每一路 LDO 是挂在
"regulator 总线"上的设备，内核帮它们匹配 driver。

**实际上**：**regulator 没有 `bus_type`**，只有一个 class。

```c
/* drivers/regulator/core.c:5857-5864 */
static struct class regulator_class = {
	.name = "regulator",
	.dev_release = regulator_dev_release,
	.dev_groups = regulator_dev_groups,
	...
};
```

三条硬证据：

| 检查点 | 结果 | 证据 |
|---|---|---|
| 有 `bus_type` 吗 | **没有** | `/usr/bin/grep -rn 'struct bus_type' drivers/regulator/ include/linux/regulator/` 无输出 |
| `rdev->dev` 会绑 driver 吗 | **永远不会** | class 设备不参与 driver 匹配；`regulator_class` 里没有 `.match`/`.probe` |
| 谁给 `rdev->dev` 起名 | 核心 | `regulator_register` 里 `dev_set_name(&rdev->dev, "regulator.%lu", ...)`（`core.c:5631-5632`） |

**所以 `/sys/class/regulator/regulator.7/` 下面那个 device
不是"一个 LDO 设备"，它只是 sysfs 的一个挂点**（属性表见 07 篇 §3）。

那消费者怎么找到提供者？不靠总线匹配，靠**三张查找表**（§8）：
DT 的 phandle（`of_find_regulator_by_node`，`of_regulator.c:556`）、
`regulator_map_list`（非 DT 平台）、`regulator_supply_alias_list`（别名）。

### 第四组：一颗 PMIC ≠ 一个 `regulator_dev`

**错误直觉**：`regulator_register` 一次注册一颗 PMIC。

**实际上**：**一次只注册一路输出**。一颗有 20 路输出的 PMIC
要调 20 次，产生 20 个 `regulator_dev`，它们共享**同一个** parent device。

```
       platform / i2c / spi 总线上的那一个设备
                （PMIC 芯片本体）
                        │  dev
        ┌───────────────┼───────────────┬─────────────┐
        │               │               │             │
   rdev(DCDC1)     rdev(DCDC2)     rdev(LDO1)    ... rdev(LDO14)
   desc[0]         desc[1]         desc[2]           desc[19]
   ↑ 20 个 rdev，共享同一个 parent，各有一份 constraints
```

驱动侧的写法就是一个 `static const struct regulator_desc xxx_regulators[]`
数组 + 一个 for 循环调 `devm_regulator_register`。
05 篇 §3 有骨架。

由此可解释一个实际现象：**`/sys/class/regulator/` 下面的编号
和 DTS 里的节点名毫无对应关系** —— 编号是全局递增的注册序号
（`core.c:5632` 那个 `atomic_inc_return(&regulator_no)`），
要认哪个是哪个只能读 `name` 属性。

---

## §2 结构清单

按"谁拥有它"分四组：

| 结构 | 定义位置 | 属于 | 一句话 |
|---|---|---|---|
| `struct regulator` | `drivers/regulator/internal.h:45-59` | 核心私有 | 消费者句柄 |
| `struct regulator_voltage` | `internal.h:29-32` | 核心私有 | 一个 `{min_uV, max_uV}` 对 |
| `struct regulator_dev` | `driver.h:611-653` | 提供者 + 核心 | 一路电源的本体 |
| `struct regulator_desc` | `driver.h:361-434` | 提供者 | 芯片能力（静态） |
| `struct regulator_ops` | `driver.h:159-244` | 提供者 | 回调表 |
| `struct regulator_config` | `driver.h:452-460` | 提供者 | 注册时的运行期参数 |
| `struct regulation_constraints` | `machine.h:157-222` | 板级（DTS） | 板子授权 |
| `struct regulator_init_data` | `machine.h:262-273` | 板级 | 约束 + 消费者映射 |
| `struct regulator_state` | `machine.h:77-84` | 板级 | 一个 suspend 态的目标 |
| `struct notification_limit` | `machine.h:88-92` | 板级 | 三级阈值 |
| `struct coupling_desc` | `driver.h:594-599` | 核心 | 耦合关系 |
| `struct regulator_coupler` | `coupler.h:41-51` | 耦合策略 | 平衡算法的回调表 |
| `struct regulator_err_state` | `driver.h:470-475` | irq helper | 一路的错误状态 |
| `struct regulator_irq_data` | `driver.h:494-499` | irq helper | 一次中断的全部状态 |
| `struct regulator_irq_desc` | `driver.h:562-575` | irq helper | 中断处理的静态描述 |
| `struct regulator_map` | `core.c:52-57` | 核心私有 | 非 DT 平台的 supply 映射表项 |
| `struct regulator_enable_gpio` | `core.c:64-69` | 核心私有 | 共享 enable GPIO 的引用计数 |
| `struct regulator_supply_alias` | `core.c:76-82` | 核心私有 | supply 别名表项 |

最后三个**定义在 `.c` 文件里**，头文件里找不到 ——
这是"核心私有、任何驱动都不该碰"的显式表达。

---

## §3 消费者侧：`struct regulator`

```c
/* drivers/regulator/internal.h:45-59 */
struct regulator {
	struct device *dev;
	struct list_head list;
	unsigned int always_on:1;
	unsigned int bypass:1;
	unsigned int device_link:1;
	int uA_load;
	unsigned int enable_count;
	unsigned int deferred_disables;
	struct regulator_voltage voltage[REGULATOR_STATES_NUM];
	const char *supply_name;
	struct device_attribute dev_attr;
	struct regulator_dev *rdev;
	struct dentry *debugfs;
};
```

| 字段 | 含义 | 备注 |
|---|---|---|
| `dev` | 消费者设备 | 可以是 NULL（`regulator_get(NULL, ...)` 是合法的） |
| `list` | 串进 `rdev->consumer_list` | 这就是"一个 rdev 挂 N 个 regulator"的物理体现 |
| `always_on` | 这个消费者是否要求常开 | 从约束继承 |
| `bypass` | 这个消费者是否请求了 bypass | bypass 也是引用计数的（`rdev->bypass_count`，`driver.h:616`） |
| `device_link` | 是否建立了 devlink | 影响 suspend/resume 顺序，见 03 篇 |
| `uA_load` | 这个消费者声明的负载电流 | `regulator_set_load` 写它；DRMS 靠它算最优模式 |
| `enable_count` | **这个消费者** enable 了几次 | 和 `rdev->use_count` 是两本账（§1.0 第一组） |
| `deferred_disables` | 挂起的延迟 disable 次数 | `regulator_disable_deferred` 用 |
| `voltage[]` | **每个 PM 状态各一份**电压区间 | 数组长度 `REGULATOR_STATES_NUM` = `PM_SUSPEND_MAX + 1`（`internal.h:16`） |
| `supply_name` | 消费者用的那个名字（"vdd" 之类） | 就是 `regulator_get(dev, id)` 的 `id` |
| `dev_attr` | 这个消费者在 rdev sysfs 目录下的那个属性文件 | 见 07 篇 §3 |
| `rdev` | 指回本体 | |
| `debugfs` | 这个消费者的 debugfs 目录 | 只在 `CONFIG_DEBUG_FS` 下有意义 |

`voltage[]` 是**一个数组而不是一对值**，这一点很容易漏。
它的意思是：**同一个消费者对"运行态"和"睡眠态"可以要求不同电压**。
索引由 `regulator_check_states`（`core.c:519`）校验。
`PM_SUSPEND_ON` 那一档是日常用的那个。

`struct regulator_voltage`（`internal.h:29-32`）只有两个 `int`：
`min_uV` / `max_uV`。**核心存的是区间不是值**，这是 01 篇 §2.3
"传区间不传值"的存储侧证据。

---

## §4 提供者侧

### §4.1 `struct regulator_dev`

字段分六组看（定义在 `driver.h:611-653`）：

**(a) 身份与配置**

| 字段 | 行 | 含义 |
|---|---|---|
| `desc` | `:612` | 指向芯片能力（只存指针，不拷贝） |
| `constraints` | `:632` | 指向板级授权，核心 kzalloc |
| `owner` | `:630` | 提供者模块，用于 `try_module_get` |
| `dev` | `:631` | 内嵌的 class 设备（不是 parent） |
| `reg_data` | `:639` | 驱动私有数据，`rdev_get_drvdata` 取它 |
| `regmap` | `:635` | helpers.c 那一族全靠它 |

**(b) 引用计数（四个，含义各不同）**

| 字段 | 行 | 数的是什么 | 想错了会怎样 |
|---|---|---|---|
| `use_count` | `:614` | **所有消费者 enable 的总次数** | 以为它是"有几个消费者" |
| `open_count` | `:615` | **有几个消费者持有句柄**（get 过几次） | 和上面混淆 |
| `bypass_count` | `:616` | 有几个消费者请求了 bypass | |
| `ref_cnt` | `:629` | **ww_mutex 的递归加锁深度**，不是对象引用计数 | 以为它是 kref |
| `exclusive` | `:613` | 是否被独占 get 了 | 非 0 时别人 get 返回 `-EPERM` |

**`use_count` 和 `open_count` 的区别是本结构最容易错的一处**：
`regulator_get` 加 `open_count`（`core.c:2289`），
`regulator_enable` 加 `use_count`。
一个消费者可以 get 了却从不 enable。

**而 sysfs 里那个叫 `num_users` 的属性读的不是 `open_count`，
是 `use_count`**（`core.c:857-862`，`sprintf(buf, "%d\n", rdev->use_count)`）——
名字和内容不一致，已记入 99 §6。想看真正的"有几个消费者持有句柄"
只能读 debugfs 里的 `open_count`（`core.c:5283-5284`）。

**(c) 链表与关系**

| 字段 | 行 | 含义 |
|---|---|---|
| `list` | `:619` | 串进 `regulator_class` 的设备列表（注释说 "list of all regulators"） |
| `consumer_list` | `:622` | 我供电的所有消费者（挂 `struct regulator` 的 `list`） |
| `supply` | `:633` | **我自己的供电者**，类型是 `struct regulator *` —— **regulator 自己也是消费者** |
| `supply_name` | `:634` | 我要的那路 supply 叫什么 |
| `coupling_desc` | `:624` | 耦合关系（§4.4） |

`supply` 字段是**树形拓扑**的实现：DCDC 供电给 LDO，
LDO 的 rdev 里 `supply` 指向一个 `struct regulator` 句柄，
那个句柄的 `rdev` 才是 DCDC。**所以电源树在数据结构上是单向父指针链**，
没有子指针 —— 想遍历子节点只能扫全局链表。

**(d) 锁与通知**

| 字段 | 行 | 含义 |
|---|---|---|
| `mutex` | `:627` | `struct ww_mutex`（不是普通 mutex），注释写 "consumer lock" |
| `mutex_owner` | `:628` | 当前持有者，配合 `ref_cnt` 做递归 |
| `notifier` | `:626` | `blocking_notifier_head`，阻塞式 = **回调里可以睡** |

用 **ww_mutex（wait/wound mutex）而不是普通 mutex** 是本子系统
锁设计的核心决定：耦合调压需要**同时持有两个 rdev 的锁**，
而加锁顺序无法预先确定 → 必然可能死锁 → 用 ww_mutex 让其中一方
"被打伤"（返回 `-EDEADLK`）并重试。锁类在 `core.c:36`
（`DEFINE_WW_CLASS(regulator_ww_class)`），详见 §8 和 03 篇 §B。

**(e) enable GPIO 与开关**

| 字段 | 行 | 含义 |
|---|---|---|
| `ena_pin` | `:643` | 指向共享的 `struct regulator_enable_gpio` |
| `ena_gpio_state` | `:644` | 本 rdev 认为 GPIO 该是什么状态 |
| `is_switch` | `:646` | 这一路只是个开关（不能调压） |
| `disable_work` | `:637` | `regulator_disable_deferred` 用的 delayed_work |
| `last_off` | `:649` | 上次关闭的时刻（`ktime_t`），用于 `off_on_delay` 时序 |

`ena_pin` **是共享的** —— 多路输出用同一个 GPIO 使能是常见电路。
`regulator_enable_gpio_list`（`core.c:40`）就是这个共享池，
`struct regulator_enable_gpio`（`core.c:64-69`）自己有
`enable_count` / `request_count` 两个计数。
**于是"打开一路"可能实际什么都没做**（GPIO 已经被别人拉高了）。

**(f) 错误缓存**

| 字段 | 行 | 含义 |
|---|---|---|
| `cached_err` | `:650` | 缓存的错误标志 |
| `use_cached_err` | `:651` | 是否用缓存 |
| `err_lock` | `:652` | `spinlock_t` —— 全结构唯一的自旋锁 |

用自旋锁是因为这条路径可能在**中断上下文**里被访问
（irq helper 读错误标志）。其余所有字段都靠那把 ww_mutex。

### §4.2 `struct regulator_desc`

74 个字段（`driver.h:361-434`），按用途分五组。**不需要全填**。

| 组 | 字段 | 什么时候要填 |
|---|---|---|
| 身份 | `name`（`:362`）、`supply_name`（`:363`）、`id`（`:370`）、`owner`（`:377`）、`type`（`:376`） | `name` 必填 |
| DT 匹配 | `of_match`（`:364`）、`of_match_full_name`（`:365`）、`regulators_node`（`:366`）、`of_parse_cb`（`:367`）、`of_map_mode`（`:433`） | 多路 PMIC 必填，见 06 篇 §4 |
| 回调表 | `ops`（`:374`）、`n_voltages`（`:372`）、`n_current_limits`（`:373`）、`irq`（`:375`） | `ops` 必填 |
| 电压模型（三种选一种） | 线性：`min_uV`/`uV_step`/`linear_min_sel`（`:379-381`）· 多段：`linear_ranges`/`n_linear_ranges`（`:386`/`:389`）· 查表：`volt_table`（`:391`）· 固定：`fixed_uV`（`:382`） | 想用 helpers.c 的现成实现就必填 |
| 寄存器布局 | `vsel_reg`/`vsel_mask`（`:396-397`）、`enable_reg`/`enable_mask`（`:403-404`）、`bypass_*`（`:408-411`）、`apply_reg`/`apply_bit`（`:401-402`）、`ramp_*`（`:422-425`）、`soft_start_*`（`:416-418`）、`pull_down_*`（`:419-421`）、`active_discharge_*`（`:412-415`）、`csel_*`（`:399-400`) | 只有用 `*_regmap` 助手时才需要 |
| 时序 | `enable_time`（`:427`）、`off_on_delay`（`:429`）、`poll_enabled_time`（`:431`）、`ramp_delay`（`:383`） | 硬件有建立时间要求时 |
| 其他 | `continuous_voltage_range`（`:371`）、`min_dropout_uV`（`:384`）、`vsel_step`（`:398`）、`enable_val`/`disable_val`/`enable_is_inverted`（`:405-407`）、`linear_range_selectors`（`:387`）、`curr_table`（`:392`）、`vsel_range_reg`/`_mask`（`:394-395`） | 按需 |

**"电压模型四选一"是填 `desc` 时最容易搞混的地方**：
选了哪一种，`ops` 里的 `list_voltage`/`map_voltage` 就要填对应的
helpers 实现（01 篇 §3.3 的第二张表）。填错的症状是电压设不上
或者设到错误的档位，**不报错**。

`min_dropout_uV`（`:384`）是"我要正常工作，输入电压至少要比输出高多少"——
核心用它自动抬高上游 regulator 的电压（03 篇 §B）。
这是**唯一一个会让核心主动去改别人电压的 desc 字段**。

### §4.3 `struct regulator_ops`

37 个回调（`driver.h:159-244`）。**全部可选**，
但和 `constraints->valid_ops_mask` 必须配套（§1.0 第二组）。

分组见 01 篇 §3.2，这里只补三条结构性的观察：

1. **电压有两套 setter**：`set_voltage`（`:165`，直接给微伏）
   和 `set_voltage_sel`（`:168`，给档位号）。**填哪一个决定核心走哪条路**：
   填了 `set_voltage_sel` 核心会先调 `map_voltage`（`:167`）把微伏
   翻成档位。两个都填时核心优先用 `set_voltage`（03 篇 §A）。
2. **`get_status`（`:216`）和 `get_mode`（`:195`）不是一回事**：
   注释（`:211-214`）明说 `get_status` 报的是"输入和实际负载综合后的结果"，
   其余 accessor 报的是"控制输入"。**一个是"我要求它怎样"，
   一个是"它实际怎样"。**
3. **四个保护回调签名一致**（`:178-185`）：
   `(rdev, lim, severity, enable)`。`severity` 取
   `enum regulator_detection_severity`（`driver.h:43-50`），
   同一个回调要同时处理"硬保护"和"只通知"两种语义。

### §4.4 耦合：`struct coupling_desc`

```c
/* include/linux/regulator/driver.h:594-599 */
struct coupling_desc {
	struct regulator_dev **coupled_rdevs;
	struct regulator_coupler *coupler;
	int n_resolved;
	int n_coupled;
};
```

| 字段 | 含义 | 备注 |
|---|---|---|
| `coupled_rdevs` | 耦合伙伴数组 | 注释（`driver.h:590`）明说**包含自己** |
| `coupler` | 用哪个平衡策略 | NULL 时用核心默认的 |
| `n_coupled` | 应该有几个 | 从 DTS 的 `regulator-coupled-with` 数出来 |
| `n_resolved` | 已经解析到几个 | `n_resolved < n_coupled` 时任何调压都 `-EPERM`（`core.c:4163`） |

`n_resolved != n_coupled` 是一个**会导致"调压突然全部失败"的中间状态** ——
耦合伙伴还没注册上来的时候就是这样。日志是
`"Not all coupled regulators registered"`。
06 篇 §5 讲 DTS 怎么写才能避免。

### §4.5 中断助手的三个结构

| 结构 | 位置 | 谁填 | 含义 |
|---|---|---|---|
| `struct regulator_irq_desc` | `driver.h:562-575` | 驱动，静态 | 中断怎么处理：`map_event` 回调、重试次数、`die` 策略 |
| `struct regulator_irq_data` | `driver.h:494-499` | 核心，每次中断 | 传给 `map_event` 的上下文（含 `states` 数组） |
| `struct regulator_err_state` | `driver.h:470-475` | `map_event` 填 | 单路的结果：哪些 `REGULATOR_ERROR_*`、要发哪些通知 |

数据流是：硬件中断 → 核心读状态 → 填 `regulator_irq_data`
→ 调驱动的 `map_event` → 驱动填每一路的 `regulator_err_state`
→ 核心按 `severity` 决定关不关电 + 发通知。详见 04 篇 §3。

---

## §5 板级：约束与初始化数据

### §5.1 `struct regulation_constraints`

这是**整套 DTS 属性的落点**。33 个字段（`machine.h:157-222`），
每一个都能在 06 篇 §1.1 的属性表里找到对应的 DT 属性。

| 组 | 字段 | 对应的 DT 属性（06 篇详列） |
|---|---|---|
| 名字 | `name`（`:159`） | `regulator-name` |
| 电压范围 | `min_uV`/`max_uV`（`:162-163`）、`uV_offset`（`:165`） | `regulator-min-microvolt` / `-max-microvolt` |
| 电流范围 | `min_uA`/`max_uA`（`:168-169`）、`ilim_uA`（`:170`） | `regulator-min-microamp` / `-max-microamp` / `-input-current-limit-microamp` |
| 授权掩码 | `valid_ops_mask`（`:184`）、`valid_modes_mask`（`:181`） | `regulator-allow-*` 那一族 |
| 负载 | `system_load`（`:172`） | `regulator-system-load` |
| 耦合 | `max_spread`（`:175`） | `regulator-coupled-max-spread` |
| 步进 | `max_uV_step`（`:178`） | `regulator-max-step-microvolt` |
| 上游 | `input_uV`（`:187`） | 注释说"only if supply is another regulator" |
| suspend | `state_disk`/`state_mem`/`state_standby`（`:190-192`）、`initial_state`（`:197`） | `regulator-state-{disk,mem,standby}` 子节点 |
| 阈值 | `over_curr_limits`/`over_voltage_limits`/`under_voltage_limits`/`temp_limits`（`:193-196`） | `regulator-{oc,ov,uv,temp}-*-microamp/-microvolt/-kelvin` |
| 时序 | `ramp_delay`（`:202`）、`settling_time`/`_up`/`_down`（`:203-205`）、`enable_time`（`:206`） | `regulator-ramp-delay` / `regulator-settling-time-us` 等 |
| 模式 | `initial_mode`（`:200`） | `regulator-initial-mode` |
| 放电 | `active_discharge`（`:208`） | `regulator-active-discharge` |
| 11 个标志位 | `always_on`（`:211`）、`boot_on`（`:212`）、`apply_uV`（`:213`）、`ramp_disable`（`:214`）、`soft_start`（`:215`）、`pull_down`（`:216`）、`over_current_protection`（`:217`）、`over_current_detection`（`:218`）、`over_voltage_detection`（`:219`）、`under_voltage_detection`（`:220`）、`over_temp_detection`（`:221`） | `regulator-always-on` / `-boot-on` / `-pull-down` / `-over-current-protection` 等 |

**`over_current_protection`（`:217`）和 `over_current_detection`（`:218`）
是两件事**：注释写得很清楚 —— 前者 "auto disable on over current"，
后者 "notify on over current"。**一个是关电，一个是报警。**
这一组四对（OC/OV/UV/温度）在 06 篇 §6 会再出现一次。

`apply_uV`（`:213`）的语义是"min == max 时把这个电压真的写下去"。
它是**唯一一个在注册期就会写硬件**的约束标志（`core.c:1246` 的
`machine_constraints_voltage`）。

### §5.2 `struct regulator_init_data` 与其余三个小结构

| 结构 | 位置 | 字段 | 备注 |
|---|---|---|---|
| `struct regulator_init_data` | `machine.h:262-273` | `supply_regulator`、`constraints`、`num_consumer_supplies`、`consumer_supplies`、`regulator_init`、`driver_data` | arm64 DT 平台上由 `regulator_of_get_init_data`（`of_regulator.c:512`）现造，驱动不用自己填 |
| `struct regulator_consumer_supply` | `machine.h:233-236` | `dev_name`、`supply` | **只给非 DT 平台用**；DT 上靠 phandle |
| `struct regulator_state` | `machine.h:77-84` | `uV`、`min_uV`、`max_uV`、`mode`、`enabled`、`changeable` | `changeable`（对应 DT 有没有写这个子节点）为假时改它返回 `-EPERM`（`core.c:4217`） |
| `struct notification_limit` | `machine.h:88-92` | `prot`、`err`、`warn` | 三个字段对应三级 severity；特殊值 `REGULATOR_NOTIF_LIMIT_DISABLE`/`-ENABLE`（`machine.h:86-87`） |

`struct regulator_state` 的 `enabled` 字段类型是
`enum regulator_active_discharge`？**不是** —— 它取的是
`machine.h:48-50` 那三个值（`DO_NOTHING_IN_SUSPEND` /
`DISABLE_IN_SUSPEND` / `ENABLE_IN_SUSPEND`）。
**"不动"和"关掉"是两个不同的值，不是 bool** ——
DTS 里没写 `regulator-off-in-suspend` 时是"不动"，
不是"关掉"。

---

## §6 结构关系图

```
                     DTS: xxx-supply = <&ldo1>
                              │
                              │ of_find_regulator_by_node()
                              ▼
 消费者驱动                                            PMIC 驱动
 (mmc / uart / cpufreq)                    (i2c/spi/platform 上的一个设备)
        │                                                  │
        │ regulator_get(dev,"vdd")            ×N regulator_register(dev,desc,cfg)
        ▼                                                  ▼
 ┌──────────────────┐                        ┌─────────────────────────────┐
 │ struct regulator │  ──── rdev ────────▶   │   struct regulator_dev      │
 │  (internal.h)    │                        │       (driver.h)            │
 │ dev              │◀── consumer_list ────  │ consumer_list ──┐           │
 │ enable_count     │      (N 个句柄)         │ use_count       │           │
 │ uA_load          │                        │ open_count      │           │
 │ voltage[5]       │                        │ mutex(ww_mutex) │           │
 │ supply_name      │                        │ dev (class 设备) │           │
 └──────────────────┘                        │ notifier        │           │
                                             └──┬───┬───┬───┬──┘           │
                                                │   │   │   │              │
                       desc ─────────────────────┘   │   │   └── ena_pin ──▶ struct
                       (芯片能力, static const)       │   │       (共享)      regulator_
                              │                      │   │                  enable_gpio
                              ▼                      │   │                  (core.c)
                     ┌────────────────┐              │   │
                     │ regulator_ops  │              │   └── coupling_desc ─▶ coupled_rdevs[]
                     │  37 个回调      │              │                        （含自己）
                     └────────────────┘              │
                                    constraints ─────┘
                                    (板级授权, DTS 来的)
                                          │
                                          ▼
                              ┌──────────────────────────┐
                              │ regulation_constraints   │
                              │  valid_ops_mask   ◀──-EPERM 的来源
                              │  min_uV / max_uV         │
                              │  state_mem/disk/standby  │
                              └──────────────────────────┘

        rdev->supply (类型是 struct regulator *) ──▶ 上游 rdev
        电源树只有父指针，没有子指针
```

图里三条最值得记住的线：

1. **`regulator` ↔ `regulator_dev` 是 N:1**，靠 `consumer_list` 串起来
2. **`desc` 和 `constraints` 从两个方向挂到同一个 rdev 上** ——
   一个来自驱动、一个来自 DTS，二者的差集就是 `-EPERM`
3. **`rdev->supply` 是一个 `struct regulator *` 而不是 `regulator_dev *`** ——
   regulator 自己是自己上游的消费者，走的是**完全相同的 get/enable 流程**

---

## §7 核心操作的数据流

### §7.1 `regulator_enable` 走到硬件

```
消费者:  regulator_enable(reg)                          [core.c:2969]
   │
   ├─ regulator_lock_dependent(rdev, &ww_ctx)           [core.c:393] 锁整条供电链
   │
   ├─ _regulator_enable(reg)                            [core.c:2893]
   │    │
   │    ├─ 若 rdev->supply → 先递归 enable 上游         深度优先，从根开始开
   │    │
   │    ├─ regulator_ops_is_valid(rdev, CHANGE_STATUS)  [core.c:117]
   │    │        └─ 不允许 → -EPERM                     [core.c:2926]
   │    │
   │    ├─ rdev->use_count == 0 ?
   │    │    ├─ 是 → _regulator_do_enable(rdev)         [core.c:2744]
   │    │    │        ├─ off_on_delay：和 last_off 比，不够就 sleep
   │    │    │        ├─ ena_pin ? regulator_ena_gpio_ctrl() [core.c:2645]
   │    │    │        │            : rdev->desc->ops->enable(rdev)   唯一真正碰硬件的一行
   │    │    │        └─ 等 enable_time / 轮询 poll_enabled_time
   │    │    └─ 否 → 什么都不做                          硬件已经开着
   │    │
   │    └─ rdev->use_count++ ; regulator->enable_count++  两本账各加一次
   │
   └─ regulator_unlock_dependent()                      [core.c:378]
```

图里 `ops->enable` 那一行是**整条链上唯一碰硬件的地方**。
上面那十几层全是仲裁：锁、递归上游、查授权、查引用计数、算时序。
**这就是 01 篇 §1 说的"核心的职责是仲裁不是转发"。**

`ena_pin` 那个分支意味着：**有 enable GPIO 的 regulator
根本不会调 `ops->enable`**。查故障时如果发现驱动的 enable
回调没被调到，先看 DTS 有没有 `gpio` 属性。

### §7.2 `regulator_set_voltage` 走到硬件

```
消费者:  regulator_set_voltage(reg, min, max)           [core.c:4191]
   │
   ├─ regulator_lock_dependent()                         同样锁整条链
   ├─ regulator_check_voltage(rdev, &min, &max)          [core.c:494]
   │        ├─ 没 constraints → -EPERM / 越界 → -EINVAL 或 -ERANGE
   │
   ├─ regulator->voltage[state] = {min, max}              先记账
   │
   ├─ regulator_balance_voltage(rdev, state)              [core.c:93 声明]
   │    ├─ regulator_check_consumers()  ← 取所有消费者区间的交集
   │    ├─ 有 coupler ? coupler->balance_voltage()
   │    │              : regulator_do_balance_voltage()
   │    └─ 顺带处理 min_dropout_uV → 可能去调上游电压
   │
   └─ _regulator_do_set_voltage(rdev, min, max)           [core.c:91 声明]
        ├─ ops->set_voltage ?      → 直接调（给微伏）
        └─ ops->set_voltage_sel ?  → 先 map_voltage 翻成档位再调
             └─ 之后按 ramp_delay / settling_time 等待
```

**"先记账再协商"这个顺序很关键**：
`regulator->voltage[]` 先被改写，然后核心去**求所有消费者的交集**。
所以 `regulator_set_voltage` 成功返回**不代表硬件是你要的那个值** ——
可能被别人的区间抬高了。想知道实际值必须 `regulator_get_voltage`。

这也解释了 01 篇 §2.3 为什么 API 收的是区间：
**区间才能求交集，单值只能覆盖。**

---

## §8 全局变量与锁

### §8.1 全局变量（全部在 `core.c` 开头）

| 变量 | 行 | 作用 | |
|---|---|---|---|
| `regulator_ww_class` | `:36` | ww_mutex 的锁类 | 每个 rdev 的 `mutex` 都属于它 |
| `regulator_nesting_mutex` | `:37` | 保护递归加锁的 `ref_cnt`/`mutex_owner` | 只在加解锁路径上短暂持有 |
| `regulator_list_mutex` | `:38` | 保护下面四条全局链表 | 普通 mutex |
| `regulator_map_list` | `:39` | 非 DT 平台的 supply 映射表 | 元素是 `struct regulator_map`（`:52`） |
| `regulator_ena_gpio_list` | `:40` | 共享 enable GPIO 池 | 元素是 `struct regulator_enable_gpio`（`:64`） |
| `regulator_supply_alias_list` | `:41` | supply 别名表 | 元素是 `struct regulator_supply_alias`（`:76`） |
| `regulator_coupler_list` | `:42` | 已注册的耦合策略 | |
| `has_full_constraints` | `:43` | 决定要不要给 dummy | 见 01 篇 §5.2 |
| `debugfs_root` | `:45` | debugfs 根目录 | 07 篇 §6 |

**没有"全局 rdev 链表"这个变量** —— 所有 rdev 由
`regulator_class` 自己的设备列表串着，遍历要用
`class_for_each_device` / `class_find_device`
（`regulator_lookup_by_name`，`core.c:1992`）。

### §8.2 锁的三层结构

| 层 | 是什么 | 保护什么 | 上下文 |
|---|---|---|---|
| `regulator_list_mutex` | 普通 mutex | 四条全局链表（map / ena_gpio / alias / coupler） | 可睡 |
| `rdev->mutex` | **ww_mutex** | 单个 rdev 的绝大多数字段 | 可睡 |
| `rdev->err_lock` | spinlock | `cached_err` / `use_cached_err` | 可在中断里用 |
| （辅助）`regulator_nesting_mutex` | 普通 mutex | 递归计数 `ref_cnt` / `mutex_owner` | 只在加解锁函数内部 |

**为什么需要 ww_mutex：耦合 + 供电树都要"同时锁多个 rdev"。**

- 供电树：`regulator_lock_dependent`（`core.c:393`）从一个 rdev
  出发把**整条上游链**全锁上（`regulator_lock_recursive`，`core.c:321`）
- 耦合：要同时锁两个平级的 rdev（`core.c:216` 那段注释：
  "Locks both rdevs using the regulator_ww_class"）

两者的加锁顺序**都无法静态确定** → 用 ww_mutex 允许"回退重试"。
所以核心代码里有若干 `while (... == -EDEADLK)` 形状的循环
（`core.c:420` / `:6144`）。

**递归加锁是被显式支持的**：同一个任务重复锁同一个 rdev 时，
`ref_cnt++` 而不是死锁（`core.c:338` 附近，
`WARN_ON_ONCE(err != -EALREADY)` 在 `:344`）。
这是因为供电链上可能出现同一个 rdev 被两条路径到达。

**对驱动作者的实际含义**：
`ops` 回调被调用时 `rdev->mutex` **已经被核心持有**。
在回调里再调任何 `regulator_*` 消费者 API 都可能死锁或触发那个
`-EALREADY` 警告。**回调里只做寄存器读写。**

---

## §9 命名约定速查

| 记法 | 含义 | 例子 |
|---|---|---|
| `rdev` | `struct regulator_dev *`（**本体**） | `rdev_get_name(rdev)` |
| `regulator` / `reg` | `struct regulator *`（**消费者句柄**） | `regulator_enable(reg)` |
| `desc` | `struct regulator_desc *`（芯片能力） | `rdev->desc->ops` |
| `rstate` | `struct regulator_state *`（一个 suspend 态） | `core.c:4217` 的 `!rstate->changeable` |
| `constraints` | `struct regulation_constraints *`（板级授权） | `rdev->constraints->valid_ops_mask` |
| `_uV` / `_uA` | **微伏 / 微安**，不是伏/毫安 | `min_uV`、`uA_load` |
| `sel` / `selector` | 电压/电流的**档位号**（无单位整数） | `set_voltage_sel` |
| `_regulator_xxx` | 前缀下划线 = **假定锁已持有**的内部版本 | `_regulator_enable`（`core.c:2893`）vs `regulator_enable`（`:2969`） |
| `rdev_err/warn/info/dbg` | 带 regulator 名字前缀的打印宏 | 定义在 `internal.h:18-27`，**用的是 `pr_*` 不是 `dev_*`** |

**`_regulator_xxx` 这条约定必须记住**：核心里几乎每个操作都有
带下划线和不带下划线两个版本，**不带的负责加锁、带的负责干活**。
读 03 篇的调用链时这是最重要的路标。

`rdev_err` 那一组宏用的是 `pr_err` 而不是 `dev_err`
（`internal.h:20-21`），所以**日志里不会带 sysfs 路径**，
只有 `<regulator名>: <消息>`。07 篇 §2 的日志表都是这个形状。

---

## §10 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 这些结构上的操作 API | `01-api.md` |
| 注册、get、enable、set_voltage 的逐段代码 | `03-internals.md` |
| 耦合、irq helper、dummy 这些可选层 | `04-extensions.md` |
| 照着写一个消费者 / PMIC 驱动 | `05-usage.md` |
| `constraints` 的每个字段对应哪条 DT 属性 | `06-devicetree.md` |
| sysfs 属性、debugfs、日志 | `07-debug.md` |
| 为什么 `regulator` 和 `regulator_dev` 要分开（大白话） | `08-feynman.md` |
