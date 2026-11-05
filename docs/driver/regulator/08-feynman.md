---
title: 费曼学习法
---

# 08 费曼解释：regulator 里最容易想错的九件事

> 本篇不是教程，是**纠偏**。
> 每一节的形状都一样：先说要解释的问题，再用一个日常场景引入，
> 然后回到源码看真实机制，最后交代**这个类比在哪里会骗你** ——
> 最后那一段是本篇的重点，因为类比的价值全在它的边界上。
>
> 类比里的每个技术断言都带行号，本篇不豁免取证。
> arm64-only。

| # | 想错的地方 | 一句话结论 |
|---|---|---|
| §1 | 「`regulator_enable` 返回 0 = 通电了」 | 可能通的是空号 |
| §2 | 「`regulator` 和 `regulator_dev` 是同一个东西」 | 一个是借书证，一个是书 |
| §3 | 「`regulator_disable` = 断电」 | 是"我不需要了"，不是"关掉" |
| §4 | 「`regulator-always-on` 只是让它开着」 | 它同时**没收了**你的开关权 |
| §5 | 「调压就是设一个电压值」 | 你提的是区间，最终值是交集 |
| §6 | 「DTS 里那个名字就是 regulator 的名字」 | 有三个名字，匹配只认其中一个 |
| §7 | 「`num_users` = 用它的驱动个数」 | 它是 `use_count`，含义完全不同 |
| §8 | 「没人管的电源会一直保持开机时的状态」 | 开机 30 秒后有人来收 |
| §9 | 「一把 mutex 就够了，为什么用 ww_mutex」 | 锁的对象是一棵会长成菱形的树 |

---

## §1 为什么 `regulator_enable` 返回 0，设备还是没通电？

### 先举个例子

你打电话订水，报了个号码，那头「嘟」一声接通了，你说「送两桶水到 3 号楼」，
对方回「好的，已受理」。你挂了电话，等了一天，水没来。

复查才发现：你打的是空号，而运营商配了一个「无人应答自动受理」的服务台 ——
所有打不通的号码都转到它那里，它对任何请求都回「好的，已受理」。
它从不失败，因为它什么都不做。

内核里这个服务台叫 **dummy regulator**。

### 回到代码

消费者拿电源走 `regulator_get`。DTS 里对应的 `xxx-supply` 没写、
或者写了但指向的 PMIC 那一路根本没注册，正常应该是"找不到"。
但 `drivers/regulator/core.c:2225-2231` 有一段兜底：

`have_full_constraints()`（`drivers/regulator/core.c:112-114`）为真时，
核心不返回错误，而是把 `dummy_regulator_rdev` 交给你，
外加一条 `dev_warn`（`drivers/regulator/core.c:2230`）：

```
<你的设备>: supply <名字> not found, using dummy regulator
```

**arm64 上 `have_full_constraints()` 恒真** ——
`drivers/regulator/core.c:6291-6292` 在 `late_initcall_sync` 阶段
只要 `of_have_populated_dt()` 就把 `has_full_constraints` 置真，
而 arm64 一定是 DT 启动的。所以这条兜底路径在 arm64 上**总是生效**。

dummy 为什么"从不失败"？看 `drivers/regulator/dummy.c:30`：

```c
static const struct regulator_ops dummy_ops;
```

一个**没有初始化任何字段**的 `static const` 结构 ——
全零，所有回调都是 NULL。`dummy_desc`（`drivers/regulator/dummy.c:32`）
把它填进 `.ops`（`drivers/regulator/dummy.c:37`）。
核心调 `enable` 时看到回调是 NULL，就只记账、返回 0。

于是：`regulator_get` 成功、`regulator_enable` 返回 0、
`regulator_get_voltage` 返回 0 或错误、你的 probe 一路成功，
**那一路电从头到尾没被碰过**。

### 这个类比什么时候会误导你？

三处：

1. **"空号"暗示 dummy 是个错误。它不是。**
   dummy 的存在是**故意的设计**：很多板子上某一路电源是硬连线常开的，
   DTS 里根本不需要描述，但驱动代码是通用的、还是会去 get。
   dummy 让"驱动要电源、板子上不需要软件控制"这种组合能跑通。
   所以你不能把 `using dummy regulator` 当成必须消灭的告警。
   官方设计笔记把这个取向写得很直白
   （`Documentation/power/regulator/design.rst:11-16`）：
   「**无法自动判定一个系统的供电配置** —— 同一颗芯片的软件等价变体
   可能有不同的供电需求，而且并非所有有供电需求的器件都对软件可见」，
   因此「除非明确知道某项改动在这个具体系统上是安全的，
   **API 不应该改变硬件状态**」。
   dummy 就是这条原则的产物：**信息不足时选择什么都不做，而不是报错**。

2. **"运营商配的服务台"暗示它是全局唯一的一个实体 —— 这点反倒是对的**，
   但它没有约束（`rdev->constraints` 为 NULL）。
   这意味着任何走 `regulator_ops_is_valid`
   （`drivers/regulator/core.c:117-127`）的操作都会打
   `no constraints`（`drivers/regulator/core.c:120`）然后失败。
   **`enable` 不打这条，`set_voltage` 会打。**
   所以"从不失败"只对开关成立，调压是会失败的。

3. **打电话你会等回音，代码不会。**
   真正的危险不在于 dummy 骗了你，而在于**那条 `dev_warn` 混在几千行
   开机日志里没人看**。唯一可靠的做法是 probe 之后主动查
   （07 篇 §2.1），或者在你确信这一路必需时用 `_optional` 变体
   让它老实报 `-ENODEV`（05 篇 §1.3）。

---

## §2 `struct regulator` 和 `struct regulator_dev` 是同一个东西的两种写法吗？

这是本子系统**最容易被类比错的那组关系**。名字只差一个后缀，
但它们是完全不同的两种东西，而且数量关系是多对一。

### 先举个例子

图书馆里有一本《内核设计》。这本书是**一个实体**：它有书架位置、
有装订、有页数、被借出去过多少次。

你去借它，馆员给你的不是书的所有权，而是**一条借阅记录**：
「张三，2026-08-19 借出，应还 9-19」。李四也来借同一本书的副本，
又是一条独立记录。记录上写着你的名字、你的借期、你的续借次数;
书本身只有一个"当前被借出几本"的计数。

- 书 = `struct regulator_dev`（简称 **rdev**）—— 一路真实的电源
- 借阅记录 = `struct regulator` —— **一个消费者对这路电源的一次引用**

**所以 `struct regulator` 不是"一个 regulator"，
它是"某个驱动对某一路电源的一张凭证"。** 名字起得很糟糕。

### 回到代码

凭证的定义在 `drivers/regulator/internal.h:45-59`，
它是**内部结构**（不在 `include/linux/` 下，消费者看不到字段）：

```c
struct regulator {
	struct device *dev;          /* 是谁借的         [internal.h:46] */
	struct list_head list;       /* 挂在 rdev 的消费者链上 [:47]     */
	unsigned int enable_count;   /* 这张凭证 enable 了几次 [:52]  */
	int uA_load;                 /* 这个消费者声明的负载 [:51]       */
	const char *supply_name;     /* DTS 里的 "vdd" 之类  [:55]       */
	struct regulator_dev *rdev;  /* 指向那本"书"    [:57]         */
	struct dentry *debugfs;      /* 这张凭证自己的 debugfs 目录 [:58]*/
};
```

`regulator_get` 返回的就是**新建的一张凭证**
（`create_regulator`，`drivers/regulator/core.c:1868`）。
同一个设备对同一路电源 get 两次，就是两张凭证。

反过来，rdev 是 `include/linux/regulator/driver.h` 里的公开结构，
它才是"书"：有 `desc`、有 `constraints`、有 `ops`、
有 `dev`（一个真的 `struct device`，`dev_to_rdev` 在
`drivers/regulator/internal.h:63-66` 做反向转换）、
有 sysfs 目录 `/sys/class/regulator/regulator.N/`。

计数分居两处，这是理解一切的关键：

| 计数 | 住在哪 | 含义 |
|---|---|---|
| `regulator->enable_count` | 凭证里（`internal.h:52`） | **这一个消费者** enable 了几次还没 disable |
| `rdev->use_count` | 书里 | 有几个消费者当前**要求它开着** |
| `rdev->open_count` | 书里 | 有几张凭证存在（get 了还没 put） |

### 这个类比什么时候会误导你？

1. **借书是排他的，借电不是。**
   图书馆一本书同时只能一个人借走。电源恰恰相反：
   N 个消费者同时"借"同一路电，各自 enable，硬件只开一次。
   唯一像借书的是 `regulator_get_exclusive` ——
   它要求"只有我能操作这一路"，第二个人来就
   `-EPERM`（`drivers/regulator/core.c:2245`）。
   这个 API 极少用，它存在恰恰说明**默认是不排他的**。

2. **还书是把书放回去，还电不是。**
   你 `regulator_disable`，只是把你那张凭证的 `enable_count` 减到 0；
   电源关不关取决于**其他凭证还有没有人要求它开着**。详见 §3。

3. **借阅记录是可以查的，凭证不行。**
   `struct regulator` 在 `drivers/regulator/internal.h` 里，
   消费者驱动**拿不到它的任何字段**，只能拿到不透明指针。
   想看某一路上都有谁、各自要求什么，只有一个途径：
   debugfs 的 `regulator_summary`（07 篇 §6.2）。

---

## §3 `regulator_disable` 为什么可能一点电都没关？

### 先举个例子

合租的三个人共用一台空调。规矩是：想凉就在墙上的白板上画一道，
不需要了就擦掉自己那道。**只要白板上还有道，空调就开着。**

你热，画了一道，空调开了。室友也画了一道。
后来你觉得够凉了，把自己那道擦掉 —— 空调**继续开着**，
因为白板上还有室友那道。你"关空调"这个动作，
实际含义是"**我不再要求它开着**"。

`regulator_disable` 就是擦掉自己那道。

### 回到代码

`_regulator_disable`（`drivers/regulator/core.c:3017` 起）的判断是三层套的：

```c
if (WARN(regulator->enable_count == 0,               /* [core.c:3019-3021] */
	 "unbalanced disables for %s\n", ...))
	return -EIO;

if (regulator->enable_count == 1) {                  /* [core.c:3023] 我这张凭证最后一次 */
	if (rdev->use_count == 1 &&                  /* [core.c:3026] 全局最后一个要求者 */
	    (rdev->constraints && !rdev->constraints->always_on)) {   /* [core.c:3027] */
		if (regulator_ops_is_valid(rdev, REGULATOR_CHANGE_STATUS)) {  /* [:3030] */
			...
			ret = _regulator_do_disable(rdev);   /* [core.c:3037] 只有这里真断电 */
			...
		}
		rdev->use_count = 0;                 /* [core.c:3049] */
	} else if (rdev->use_count > 1) {
		rdev->use_count--;                   /* [core.c:3050-3051] 只减数，不动硬件 */
	}
}
```

**四道门全过才会真断电**：
① 我这张凭证的 `enable_count` 降到 0（`:3023`）；
② 全局 `use_count` 是 1，即我是最后一个要求者（`:3026`）；
③ DTS 没写 `regulator-always-on`（`:3027`）；
④ `valid_ops_mask` 里有 `CHANGE_STATUS`（`:3030`，见 §4）。

任何一道不过，`regulator_disable` **返回 0，硬件不动**。

**这一条官方文档也明确写了** ——
`Documentation/driver-api/regulator.rst:83-88`：
「由于可能有多个消费者在用同一路电源、而板级约束也可能不允许关闭它，
**调用 `regulator_disable()` 并不保证这一路电真的会被关掉**。
消费者驱动应当假设这个 regulator 随时可能是开着的。」
换句话说：本节讲的不是实现缺陷，是**写进契约的语义**。

还有一条容易漏的连锁反应：真断电成功后，
`drivers/regulator/core.c:3061-3062` 会**递归 disable 上游**（`rdev->supply`）。
所以关掉一路 LDO，可能顺带把给它供电的 buck 一起关掉 ——
如果那个 buck 也刚好没别人要。

### 这个类比什么时候会误导你？

1. **白板上的道是"人头数"，代码里是两级计数。**
   你在白板上只能画一道，代码里同一个消费者可以 `regulator_enable`
   五次（`enable_count` = 5），得 disable 五次才算擦掉。
   多擦一次会撞上 `WARN`（`drivers/regulator/core.c:3019-3021`）并返回
   **`-EIO`** —— 注意不是 `-EINVAL`，这一条很容易记错。

2. **室友擦道你能看见，凭证不能。**
   你无法从代码里查"还有谁要求它开着"。
   `use_count` 只在 debugfs（`drivers/regulator/core.c:5281-5282`）
   和 sysfs 的 `num_users`（§7）里露出来。

3. **空调不会有"物业规定必须常开"这种情况，电源会。**
   `regulator-always-on`（第 ③ 道门）是板级配置，
   它让第 ② 道门就算过了也没用。这是 §4 的主题。

4. **最要紧的一条：白板的规矩是三个人商量好的，代码里的规矩是内核定的，
   而你的驱动可能是唯一守规矩的那个。**
   如果这一路上另一个驱动 enable 了却忘了 disable，
   `use_count` 永远回不到 1，你的 disable 永远走不到 `:3037`。
   这类"关不掉"的排查入口是 `regulator_summary` 的消费者行（07 篇 §6.2）。

---

## §4 为什么写了 `regulator-always-on`，我反而失去了开关它的权力？

### 先举个例子

公司会议室的灯有两种挂牌。挂「请随手关灯」的房间，
墙上的开关是给你用的。挂「**此灯常亮，请勿操作**」的房间 ——
比如放服务器的那间 —— 物业会把开关面板整个拆掉。

不是"贴了个提示希望你别关"，是**物理上不给你关的手段**。

`regulator-always-on` 就是拆开关面板。

### 回到代码

核心判断"这个操作允许不允许"只走一个函数
（`drivers/regulator/core.c:117-127`）：

```c
static bool regulator_ops_is_valid(struct regulator_dev *rdev, int ops)
{
	if (!rdev->constraints) {
		rdev_err(rdev, "no constraints\n");    /* [core.c:120] */
		return false;
	}
	if (rdev->constraints->valid_ops_mask & ops)  /* [core.c:124] */
		return true;
	return false;
}
```

**它只看 `constraints->valid_ops_mask`，
完全不看 `rdev->desc`、也不看 `rdev->ops`。**
也就是说：**驱动实现了 `.enable` 回调这件事，
对"能不能 enable"没有任何影响。**

而 `valid_ops_mask` 里的 `REGULATOR_CHANGE_STATUS` 位来自
`drivers/regulator/of_regulator.c:130-131`：

```c
if (!of_property_read_bool(np, "regulator-always-on"))
	constraints->valid_ops_mask |= REGULATOR_CHANGE_STATUS;
```

**取反**。DTS 里**没写** `regulator-always-on` 才给这个位。
写了 = 不给。

于是 `regulator-always-on` 有两个后果，很多人只知道第一个：

| 后果 | 机制 |
|---|---|
| 开机后这一路保持开着 | `set_machine_constraints` 里的 always_on 处理 |
| **`REGULATOR_CHANGE_STATUS` 被没收** | `of_regulator.c:130-131` 的取反 |

第二个后果的具体表现：`regulator_enable` / `regulator_disable`
**返回 0 但什么都不做**（`_regulator_disable` 卡在
`drivers/regulator/core.c:3030` 那道门外，而
`drivers/regulator/core.c:3027` 那道门甚至更早就拦住了）。

### 这个类比什么时候会误导你？

1. **"拆开关"暗示彻底没救，其实核心里还有一层。**
   `always_on` 在 `drivers/regulator/core.c:3027` 被单独检查了一次,
   和 `valid_ops_mask` 是**两道独立的门**。所以即使某个板级配置
   歪打正着给了 `CHANGE_STATUS`，`always_on` 那道也还在。
   这是"双保险"，不是"一个开关"。

2. **拆开关只影响开关，DTS 这条属性的影响面更窄也更怪。**
   它**不影响调压**。`CHANGE_VOLTAGE` 是另一条规则：
   `regulator-min-microvolt` 和 `regulator-max-microvolt`
   **两者不相等**时才给（06 篇 §1.1）。
   所以写死一个值（min == max）的常见写法，是"能开关、不能调压"；
   而 `always-on` + 一个电压范围，是"不能开关、能调压"。
   两条属性各管各的。

3. **最误导人的一点：拆了开关，房间的灯还在你的电费单上。**
   `regulator_get` 照样成功，`regulator_enable` 照样返回 0，
   `rdev->use_count` 照样在涨。核心**没有任何地方**告诉你
   "你的 enable 是空操作"。
   唯一的自查途径是 `/sys/class/regulator/regulator.N/` 下的属性
   （07 篇 §3.2）—— 那里能看出 DTS 到底给了哪些权限。

---

## §5 `regulator_set_voltage` 为什么要传两个电压？

### 先举个例子

三个人合订一趟车。你说「我 8 点到 9 点之间出发都行」，
第二个人说「我必须 8:30 到 8:45」，第三个人说「9 点之前都行」。
调度员要找的是**三个区间的交集**：8:30–8:45。最后发车时间在这个交集里。

如果第四个人说「我只能 10 点」，交集空了 —— 调度员**没法发车**，
不是"发一趟折中的"。

`regulator_set_voltage(reg, min_uV, max_uV)` 提的就是一个区间。

### 回到代码

每次有人调压，核心先跑一遍
`regulator_check_consumers`（`drivers/regulator/core.c:527` 起）：
遍历这一路上**所有凭证**，把每张凭证记录的
`voltage[].min_uV` / `max_uV` 逐个收窄。

收窄完如果 `min > max`（交集为空），它打一条日志然后失败：

```
Restricting voltage, %u-%uuV        [core.c:550]
```

紧接着 `return -EINVAL`（`drivers/regulator/core.c:552`）。

**这条日志的措辞是有问题的。**
「Restricting voltage」听起来像"我帮你收窄了一下，办好了"，
实际含义是"**交集为空，什么都没做，报错返回**"。
07 篇 §2.2 单独讲了这条。

反过来，交集非空时最终电压是**交集里的某个硬件档位**，
不一定是你要的那个数。所以：

**`set_voltage` 返回 0 ≠ 电压是你写的值。**
要知道实际值只有一个办法：`regulator_get_voltage` 回读。

### 这个类比什么时候会误导你？

1. **发车时间是连续的，电压是离散档位。**
   交集算出 `[1.13V, 1.28V]` 之后，硬件可能只支持
   1.10 / 1.15 / 1.20 / 1.25 / 1.30 这几档。
   核心要在交集内**再找一个可用档位**。
   所以你给 `[1200000, 1200000]`（写死一个值）是合法的，
   但硬件档位对不上就是 `-EINVAL` —— 这是新手最常撞的墙。
   **给一个你能接受的区间，别写死。**

2. **调度员会告诉你最终几点发车，核心不会。**
   `set_voltage` 只返回 0 或错误码，**不回传最终电压**。

3. **交集只在"有权调压"时才算 —— 否则连交集都不算。**
   `valid_ops_mask` 里没有 `CHANGE_VOLTAGE`、
   而你的区间又落在约束区间内时，核心走的是"**只记账**"的路径：
   把你的偏好记进凭证、返回 0、**一个寄存器都不写**（03 篇 §A6）。
   这时候"没发车"和"发了车恰好合你意"是同一个返回值。

4. **合订车是一次性的，凭证是长期的。**
   你的区间偏好会**一直留在凭证里**，直到你 put 或者再次
   `set_voltage`。所以后来的第五个人来调压时，
   你早就退出的那个偏好可能还在参与交集计算 ——
   前提是你还没 `regulator_put`。

---

## §6 一路电源为什么有三个名字，DTS 匹配用哪一个？

### 先举个例子

一个人有：身份证上的姓名、公司花名册上的工号、同事喊的绰号。
三样都指同一个人，但**门禁刷卡只认工号**。
你把绰号填进门禁系统，刷不开。

一路电源的三个名字也是这样，而且**匹配只认其中一个**。

### 回到代码

看一个真实的 DTS 节点
（`arch/arm64/boot/dts/rockchip/rk3399-rock-pi-4.dtsi:266-267`）：

```dts
regulators {                                    /* [:265] 容器节点 */
	vdd_center: DCDC_REG1 {                 /* [:266] label: 节点名 */
		regulator-name = "vdd_center";  /* [:267] */
		regulator-always-on;            /* [:268] */
		...
	};
};
```

一行里就有三个名字：

| 名字 | 例子 | 谁用它 |
|---|---|---|
| **DTS label** | `vdd_center:` | 只给**别的 DTS 节点**引用用：`vdd-supply = <&vdd_center>;` |
| **DTS 节点名** | `DCDC_REG1` | **唯一参与驱动匹配的名字** |
| `regulator-name` 属性 | `"vdd_center"` | 显示用：sysfs / 日志 / `regulator_summary` |

匹配靠的是驱动 `regulator_desc` 里的 `of_match`
（`include/linux/regulator/driver.h:364`）和节点名对撞。
rk808 驱动用宏批量生成
（`drivers/regulator/rk808-regulator.c:74`）：

```c
.of_match        = of_match_ptr(_match),           /* [:74] */
.regulators_node = of_match_ptr("regulators"),     /* [:75] */
```

`regulators_node`（`drivers/regulator/rk808-regulator.c:75`，
字段声明在 `include/linux/regulator/driver.h:366`）
告诉核心"我的各路子节点在 PMIC 节点下的 `regulators` 容器里"，
然后核心在那个容器里找名字等于 `of_match` 的子节点。

**所以 `regulator-name` 完全不参与匹配。**
把 `regulator-name` 改成任意字符串，功能一点不变；
把节点名 `DCDC_REG1` 改一个字母，**这一路的所有 DTS 约束静默丢失**
—— 没有任何报错，因为"PMIC 有一路没在 DTS 里配约束"是合法状态。

那 sysfs 和日志里显示哪个？`rdev_get_name`
（`drivers/regulator/core.c:101-109`）：

```c
if (rdev->constraints && rdev->constraints->name)   /* [core.c:103] */
	return rdev->constraints->name;             /* ← regulator-name */
else if (rdev->desc->name)                          /* [core.c:105] */
	return rdev->desc->name;                    /* ← 驱动里的 .name */
else
	return "";                                  /* [core.c:108] */
```

优先 `regulator-name`，没写就退回驱动的 `.name`。

顺带一个巧合会让人更晕：`rk808-regulator.c:72` 里
`.name = (_match)` —— 这个宏把 `name` 和 `of_match`
**填成同一个字符串**。所以在这颗芯片上"驱动里的名字"和
"要匹配的节点名"恰好一样，但这是宏的写法，**不是通用规律**。

### 这个类比什么时候会误导你？

1. **工号是唯一的，节点名不是。**
   节点名只在**它所在的容器节点内**唯一。
   两颗同型号 PMIC 各有一个 `DCDC_REG1` 是完全正常的。
   这带来一个真实的调试麻烦：`of_regulator.c` 里的报错用的是
   `pr_*` 而不是 `dev_*`（06 篇 §9），
   所以两个同名子节点报错时**日志上分不出是哪一颗**。

2. **绰号不重要，`regulator-name` 有点重要。**
   它不参与匹配，但它是你在 `regulator_summary`、
   sysfs `name` 属性、所有 `rdev_err` 日志里看到的那个名字。
   写得和 label 一致（上面例子就是）能省很多来回，
   写得不一致就等着看日志里一个陌生名字。

3. **"刷不开门会响一声"，这里不响。**
   门禁刷错卡会有反馈。`of_match` 写错**完全静默**：
   驱动注册成功、rdev 存在、sysfs 目录也有，
   只是 `constraints` 是一份空壳
   （`drivers/regulator/core.c:5635-5641` 在没有 init_data 时走
   `kzalloc`，所以约束全 0 而不是"未定义"）。
   表现是 `min_microvolts` 读出 `0` 而不是报错 —— 06 篇 §8.2 有决策树。

---

## §7 `num_users` 为什么不是"用它的驱动个数"？

### 先举个例子

健身房门口的电子屏写着「当前在馆人数：3」。
你以为是"办了卡的人里有 3 个在里面"。
实际上这个数字统计的是「**当前正在使用器械的人**」——
在休息区坐着、在更衣室换衣服的都不算。

会员数、进馆人数、正在用器械的人数，是三个不同的数。
电子屏上只显示了一个，而它的标签起错了。

### 回到代码

sysfs 里 `/sys/class/regulator/regulator.N/num_users`
的实现（`drivers/regulator/core.c:857-862`）：

```c
static ssize_t num_users_show(struct device *dev, struct device_attribute *attr,
			      char *buf)
{
	struct regulator_dev *rdev = dev_get_drvdata(dev);
	return sprintf(buf, "%d\n", rdev->use_count);     /* [core.c:861] */
}
```

**它读的是 `use_count`，不是 `open_count`。**

回到 §2 那张表：

| 你想知道 | 真正的字段 | 从哪看 |
|---|---|---|
| 有几个驱动 get 了这一路（"会员数"） | `rdev->open_count` | **只有 debugfs**：`drivers/regulator/core.c:5283-5284` |
| 有几个驱动当前要求它开着（"在用器械的人"） | `rdev->use_count` | sysfs `num_users` ✅ / debugfs `use_count`（`core.c:5281-5282`） |
| 具体是哪些驱动、各自要多少电压 | 凭证链表 | 只有 `regulator_summary`（07 篇 §6.2） |

所以：一个驱动 `regulator_get` 了但没 `enable`，
`num_users` 读出 **0**。三个驱动都 get 了、只有一个 enable，
`num_users` 读出 **1**。**这个文件从来没在数"用户"。**

一个反直觉的推论：`num_users` 读到 0 **不代表没人用这一路**，
只代表"没人当前要求它开着"。要判断"这一路有没有人认领"，
必须看 debugfs 的 `open_count`。

### 这个类比什么时候会误导你？

1. **健身房的三个数是同时可查的，这里不是。**
   `open_count` **完全没有 sysfs 出口** ——
   `CONFIG_DEBUG_FS` 关掉的产品内核上，
   "这一路有没有人 get 过"是**查不到的**。
   这是本子系统一个真实的可观测性缺口（记在 99 篇 §6）。

2. **"标签起错了"暗示这是个 bug，实际上是历史遗留。**
   这个属性名和它的语义不一致，但改名会破坏用户态 ABI。
   所以它会一直这样。**读文档比读文件名可靠。**

3. **人数会自己变回 0，`use_count` 不一定。**
   健身房关门时人都走了。`use_count` 靠的是驱动老实配对
   enable/disable；有驱动漏了 disable，这个数就永远下不来 ——
   而这恰好是 §3 里"我关不掉它"的最常见原因。

---

## §8 开机 30 秒后，为什么一批电源自己关掉了？

### 先举个例子

新装修的办公室，电工把所有灯都打开了留着 —— 他不知道哪盏该开哪盏该关，
先全开着，等各部门搬进来。

物业的规矩是：**搬迁开始半分钟后来收一次**。
到时候还没人在那盏灯下办公、又没挂「此灯常亮」牌子的，物业就给关掉。
挂了牌的不动，有人在用的不动。

内核这个"物业"叫 `regulator_late_cleanup`。

### 回到代码

`late_initcall_sync` 阶段（`drivers/regulator/core.c:6310`），
核心排一个延时任务（`drivers/regulator/core.c:6305-6306`）：

```c
schedule_delayed_work(&regulator_init_complete_work,
		      msecs_to_jiffies(30000));         /* 30 秒 */
```

上面 `drivers/regulator/core.c:6294-6303` 那段注释很老实：
"我们随便挑了一个时长……理想情况该由用户态通知我们，
但没有这个机制，所以就等一会儿、希望等够了"。
**30000 这个数没有任何技术依据。**

30 秒后 `regulator_late_cleanup`（`drivers/regulator/core.c:6215` 起）
遍历所有 rdev，四道门都过才关：

| 门 | 行 | 判据 |
|---|---|---|
| ① | `drivers/regulator/core.c:6221` | `constraints->always_on` 为假（"没挂常亮牌") |
| ② | `drivers/regulator/core.c:6224` | 有 `REGULATOR_CHANGE_STATUS`（有权关） |
| ③ | `drivers/regulator/core.c:6229` | **`rdev->use_count` 为 0**（没人要求它开着） |
| ④ | `drivers/regulator/core.c:6236` | `have_full_constraints()`（arm64 恒真） |

过了就打日志并关掉（`drivers/regulator/core.c:6240`）：

```
<regulator 名>: disabling
```

**第 ③ 道门用的是 `use_count` 而不是 `open_count`。**
后果很直接：一个驱动 `regulator_get` 了、但**还没 `regulator_enable`**
（比如它在等自己的 firmware、或者是个按需上电的设备），
`use_count` 是 0 —— **物业会把它的电关掉**。

这就是"开机三十几秒后设备突然挂掉"这类问题的经典成因。

### 这个类比什么时候会误导你？

1. **"物业只收一次"这点是对的，而且很重要。**
   这不是周期性巡检，是**一次性的**。所以 30 秒之后开机的设备
   不受影响，而 30 秒**附近**上电的设备会遇到"时序赛跑"——
   同一份镜像在快机器上正常、慢机器上挂掉。
   这类问题**不可靠复现**，是它最难查的地方。

2. **"关灯"暗示只影响那一路，实际会顺着供电链往上传。**
   `_regulator_do_disable` 之后
   （和 §3 里 `drivers/regulator/core.c:3061-3062` 同一个机制）
   上游 supply 也可能被带着关掉。所以你会看到一串 `disabling`。

3. **物业关不掉的灯会记一笔，这里也会 —— 但措辞不一样。**
   关失败打的是 `couldn't disable: %pe`
   （`drivers/regulator/core.c:6243`），是另一条消息。
   两条要分清：`disabling` = 关成功了（你的设备要出事），
   `couldn't disable` = 想关但失败了（硬件层面反而没事）。

4. **别把它当成"内核在帮你省电"就放任它。**
   正确的修法不是想办法躲过这次清理，而是让你的驱动
   **在需要电的时候就 enable**。
   `regulator-always-on` 虽然能挡住第 ① 道门，
   但代价是永久失去开关权（§4）—— 那是板级的最后手段，
   不是驱动 bug 的补丁。

---

## §9 为什么核心用 ww_mutex 这种"会失败的锁"？

### 先举个例子

图书馆有个规定：借阅参考书必须**同时**借到一整套。
你要《卷一》和《卷三》，别人要《卷三》和《卷一》。
你拿到卷一、他拿到卷三，两人都在等对方 —— 死锁。

图书馆的解法有两种。一种是**规定必须按卷号从小到大取**，
谁都不许乱序 —— 简单，但如果你事先不知道要哪几卷（要翻开卷一
才知道还需要卷三），这规矩就没法遵守。

第二种：允许你乱序取，但**取不到时你必须把已经拿到的全部放回，
从头再来**。这叫 wound/wait，`ww_mutex` 就是它。

regulator 必须用第二种，因为**它事先不知道要锁哪些**。

### 回到代码

为什么不知道？因为电源是一棵**树**，而且会长成菱形：

```
      vcc5v0_sys
       /      \
   vdd_log   vcc3v3_sys
       \      /
        \    /
      （耦合关系）
```

调一路电压，需要同时持有：**它自己、它所有上游 supply、
所有和它耦合（`regulator-coupled-with`）的 rdev、以及那些的上游**。
这个集合只能**边走边发现** —— `regulator_lock_recursive`
（`drivers/regulator/core.c:321`）就是在递归地往上爬，
爬到哪个就锁哪个（`drivers/regulator/core.c:351` 递归进 supply）。

三个入口对应三种规模：

| 函数 | 锁几个 | 典型调用方 |
|---|---|---|
| `ww_mutex_lock` 单个（`drivers/regulator/core.c:157`） | 1 | 内部辅助路径 |
| `regulator_lock_two`（`drivers/regulator/core.c:218`） | 2 | 解析 supply 时（`drivers/regulator/core.c:2148`） |
| `regulator_lock_dependent`（`drivers/regulator/core.c:393`） | 整个依赖闭包 | `regulator_enable` / `disable`（`core.c:2975`、`:3085`） |

拿不到锁时的"放回重来"长这样
（`drivers/regulator/core.c:402-422`）：
`ww_acquire_init` 开一轮 → 递归取 → 撞车就整批回滚，
用 `ww_mutex_lock_slow`（`drivers/regulator/core.c:406`）
先把冲突那个稳稳拿下，再重新递归 → `ww_acquire_done`
（`drivers/regulator/core.c:422`）收尾。

**这就是为什么"死锁"错误码在这份代码里到处都是** ——
它不是错误，是"这轮撞车了，重来"的内部信号，**不会返回给消费者**。
顺带一个会让 grep 漏掉一半的细节：**同一个含义写了两种拼法**，
`-EDEADLK`（`drivers/regulator/core.c:164`、`:338`、`:420`）和
`-EDEADLOCK`（`drivers/regulator/core.c:231`、`:249`）。
arm64 上两者是同一个值，所以行为没差别，
但只 grep 一种拼法会以为另外几处不存在（记在 99 篇 §6）。

### 这个类比什么时候会误导你？

1. **"从头再来"听起来很贵，实际不贵。**
   撞车只发生在**两个 CPU 同时操作有交集的依赖闭包**时。
   多数板子上这种情况极少，所以慢路径几乎不走。
   你不需要为此做任何优化，也不该因为"锁很复杂"而避开这些 API。

2. **借书人知道自己在重试，消费者驱动不知道。**
   回滚重试全在核心内部，`regulator_enable` 的调用者
   **看不到任何迹象**。所以别指望用返回码判断有没有锁竞争。

3. **图书馆的书借完就还，这里有一条你必须遵守的反向约束。**
   `rdev->mutex` 在**整个 `ops` 回调执行期间是持有的**。
   所以你写 PMIC 驱动时，回调里
   **不能调任何消费者侧 `regulator_*` API、不能长睡眠**
   —— 会立刻重入取锁（03 篇 §B1.4、05 篇 §2.3 检查清单）。
   这是"锁会失败"这个类比最容易掩盖的一条硬规矩：
   **能失败的锁不等于能重入的锁。**

4. **这套机制也解释了 `regulator_summary` 为什么不能轮询。**
   打印一次要锁**整棵树**（`drivers/regulator/core.c:6119` →
   `:6099`）。拿它当监控指标定时 cat，会周期性地把
   所有电源操作卡住（07 篇 §6.2 的 警告）。

---

## §10 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 本篇提到的每个 API 的完整签名和错误码 | `01-api.md` |
| `regulator` / `regulator_dev` / `constraints` 的字段全貌（§2 的展开） | `02-datastructures.md` |
| enable / disable / set_voltage 的逐段流程（§3 §5 §9 的展开） | `03-internals.md` Part A / Part B |
| helpers、coupler、irq_helper 这些扩展框架 | `04-extensions.md` |
| 消费者和 PMIC 驱动怎么写（§1 §4 的实操版） | `05-usage.md` |
| DTS 每条属性怎么写、写错怎么定位（§4 §6 的展开） | `06-devicetree.md` |
| 上面每条症状用什么工具看（§7 §8 的实操版） | `07-debug.md` |
| 本篇每条论断的出处，以及哪些是推断而非观察 | `99-evidence.md` |
