---
title: 费曼学习法
---

# pinctrl 的费曼解释：八个最容易想错的地方

> **本篇的定位**：前面七篇讲"是什么、在哪里、怎么用"。这一篇只做一件事——**把 pinctrl 里最容易被类比错的那几个概念，用大白话讲对**。
>
> **为什么需要这一篇**：pinctrl 里几乎每个名词都有一个"看起来很像但其实不是"的日常对应物。`pinctrl_get()` 看起来像 `clk_get()`，但它拿到的不是资源；`state` 看起来像状态机，但它没有状态迁移规则；`function` 看起来像"引脚的功能"，但它压根不描述单个引脚。**这些错误类比比不知道更危险**，因为它们让你自信地写错 DTS。
>
> **每一节的结构固定四段**：问题标题 → 先举个例子 → 回到代码 → **这个类比什么时候会误导你？**。最后一段是本篇的核心：一个不给出边界的类比只是故事。
>
> **arm64-only**：所有代码引用按 arm64 路径。裁掉了什么见 `99-evidence.md` §2.7。

---

## §1 pinctrl 为什么不是一条总线？我 `pinctrl_get()` 拿到的到底是什么？

**这是本子系统最容易被类比错的一组关系**，所以放在第一节。

### 先举个例子

想想一个老式电话交换机房。房间里有一面配线架，上面有 500 个接线端子（**引脚**）。配线架和它旁边那个负责插拔跳线的机械装置，合起来是**一台机器**——只有一台，全楼共用。

现在楼里有 30 个办公室要打电话。他们不会各自搬来一台配线架。他们做的是：**去跟配线架的管理员申请一张工单**。

工单上写着："我是 3 楼的传真机。我平时（`default`）要用端子 12 和 13，接到‘传真线路’上；我休息的时候（`sleep`）这两个端子要拉到地。"

- 配线架那台机器 = `struct pinctrl_dev`，**全系统只有几个**（一个 SoC 通常 1~3 个）
- 一张工单 = `struct pinctrl`（就是 `pinctrl_get()` 的返回值），**每个消费者设备一张**
- 工单上"平时" / "休息时"这些**场景** = `struct pinctrl_state`
- 每个场景下面"端子 12、13 接到传真线路" 这样一条**具体动作** = `struct pinctrl_setting`

关键点：**工单不是配线架**。你手里那张纸不给你任何硬件。它只是"当你说‘切到 sleep’时，我该去拨哪几根线"的一份预先算好的清单。

### 回到代码

四层结构在 `drivers/pinctrl/core.h` 里挨着定义，一眼就能看出层级：

```c
struct pinctrl_dev {          /* core.h:42  ——「配线架」，控制器驱动注册出来的 */
	struct list_head node;
	struct pinctrl_desc *desc;
	...
	struct pinctrl_state *hog_default;   /* :59 */
	struct pinctrl_state *hog_sleep;     /* :60 */
};

struct pinctrl {              /* core.h:77  ——「工单」，消费者持有的 handle */
	struct list_head node;
	struct device *dev;                  /* :79  谁的工单 */
	struct list_head states;             /* :80  这张工单上有哪些场景 */
	struct pinctrl_state *state;         /* :81  当前处于哪个场景 */
	struct list_head dt_maps;            /* :82 */
	struct kref users;                   /* :83  引用计数 */
};

struct pinctrl_state {        /* core.h:92  ——「一个场景」 */
	struct list_head node;
	const char *name;                    /* :94  就是 DTS 里的 "default"/"sleep" */
	struct list_head settings;           /* :95  这个场景下要做的所有动作 */
};

struct pinctrl_setting {      /* core.h:131 ——「一条具体动作」 */
	struct list_head node;
	enum pinctrl_map_type type;          /* :133 是 mux 动作还是 config 动作 */
	struct pinctrl_dev *pctldev;         /* :134 要去哪台配线架上做 */
	const char *dev_name;
	union {
		struct pinctrl_setting_mux mux;          /* :137 → core.h:103 {group, func} */
		struct pinctrl_setting_configs configs;  /* :138 → core.h:116 {group_or_pin, configs[], n} */
	} data;
};
```

注意 `pinctrl_setting` 里的 `pctldev` 字段（`core.h:134`）：**一张工单上的不同动作可以落在不同的配线架上**。一个设备的引脚横跨两个 pin 控制器是完全合法的。

全树 grep 确认 pinctrl **没有自己的 `bus_type`**：

```bash
grep -rn 'struct bus_type' drivers/pinctrl/ include/linux/pinctrl/
# 空
```

所以它不是总线，也就没有"device 和 driver 配对"这回事。消费者靠**函数调用**取得服务，不靠总线匹配。

### 这个类比什么时候会误导你？

**误导点一：工单不是"申请到"的资源，而是"预编译"的结果。**

`clk_get()` / `regulator_get()` 拿到的东西背后有一个真实的、被引用计数保护的硬件资源。而 `pinctrl_get()` 做的事情本质上是**编译**：它把全局 map 表里所有 `map->dev_name == dev_name(dev)` 的条目筛出来（`core.c:1065-1067`），逐条解析成 setting（`core.c:1080`），挂进对应的 state 链表。**这一步一个寄存器都不碰**。

真正碰硬件的是 `pinctrl_select_state()`。这也是为什么它们必须是两个 API：解析可能因为控制器还没就绪而 `-EPROBE_DEFER`（`core.c:1094-1098`），但一旦解析成功，切场景就是纯执行、不会再失败于"找不到名字"。

**误导点二：`kref users`（`core.h:83`）不是"多少人在用这些引脚"，是"多少人持有这张工单"。**

同一个 device 第二次调 `pinctrl_get()` 不会新建工单，而是把老的引用计数加一然后返回同一个指针，并打一条 `dev_dbg`：

```c
/* core.c:1136 */
dev_dbg(dev, "obtain a copy of previously claimed pinctrl\n");
```

这条 dbg 默认看不见（`07-debug.md` §2.2）。如果你的驱动里 `pinctrl_get()` 和 `devm_pinctrl_get()` 混用，两边拿到的是同一个对象，谁先 `put` 谁后 `put` 就成了实际问题。

**误导点三：`pinctrl_dev` 上那两个 `hog_*` 字段（`core.h:59-60`）说明配线架自己也会开工单给自己。**

管理员也可以是用户。这就是 hog，见 §7。

---

## §2 为什么我的驱动一行 pinctrl 代码都没写，引脚却已经配好了？

这是新手最常问的问题，答案不在 pinctrl 子系统里，在**驱动核心**里。

### 先举个例子

你去一家装修好的公寓入住。你没有联系电力公司、没有接水管、没有布网线——**你签合同的时候，物业已经替你把这些都办完了**。合同上写着"入住前完成基础设施接通"，这是标准流程的一部分，不是你的工作。

你只有在需要"特殊场景"时才自己动手：比如你想在阳台加一个插座（对应 `sleep` state 之外的自定义 state），这时你才需要自己打电话。

驱动核心对每一个即将 probe 的 device 做的就是"物业接通基础设施"这件事。

### 回到代码

`drivers/base/dd.c:612-618`，逐字：

```c
re_probe:
	dev->driver = drv;

	/* If using pinctrl, bind pins now before probing */
	ret = pinctrl_bind_pins(dev);
	if (ret)
		goto pinctrl_bind_failed;
```

**注意注释的位置和 `re_probe:` 标签**：这在 `really_probe()` 里，在**调用驱动的 `.probe()` 之前**，也在 `dev->bus->dma_configure`（`dd.c:620`）之前。**任何总线上的任何设备都会经过这一步**，跟它是 platform / i2c / spi 设备无关。

`pinctrl_bind_pins()` 全文只有 84 行（`drivers/base/pinctrl.c:21-105`），做的事情按顺序是：

| 行 | 动作 | 失败了怎么办 |
|---|---|---|
| `:25-26` | `dev->of_node_reused` 为真就直接返回 0 | 这是给"两个 device 共用一个 DT 节点"的场合留的后门 |
| `:32` | `devm_pinctrl_get(dev)`——申请工单 | `dev_dbg "no pinctrl handle"`（`:34`）然后走 cleanup |
| `:39-40` | 查 `PINCTRL_STATE_DEFAULT` | `dev_dbg "no default pinctrl state"`（`:42`）+ **`ret = 0`**（`:43`）——**没有 default state 不是错误** |
| `:47-48` | 查 `PINCTRL_STATE_INIT` | 逐字注释 `/* Not supplying this state is perfectly legal */`（`:50`） |
| `:53-57` | **有 `init` 就选 `init`，没有就选 `default`** | |
| `:70-80` | `CONFIG_PM=y` 时再查 `sleep` 和 `idle` | 两个都可以没有，各打一条 dbg（`:74`、`:80`）。**这里只查不选**——真正切换由 PM 回调在运行时做 |

**最关键的是收尾那段错误过滤**（`drivers/base/pinctrl.c:96-104`，逐字）：

```c
	/* Return deferrals */
	if (ret == -EPROBE_DEFER)
		return ret;
	/* Return serious errors */
	if (ret == -EINVAL)
		return ret;
	/* We ignore errors like -ENOENT meaning no pinctrl state */

	return 0;
```

这九行解释了 pinctrl 最让人困惑的一件事：**为什么 DTS 里 pinctrl 写错了，有时设备照样 probe 成功、只是不工作？** 因为只有 `-EPROBE_DEFER` 和 `-EINVAL` 会往上传，别的错误码在这里被吞掉了。

### 这个类比什么时候会误导你？

**误导点一：物业只接通"标准"设施——`init`/`default` 二选一，`sleep`/`idle` 只查不用。**

`sleep` state **不会**被 `pinctrl_bind_pins()` 应用（`:70-80` 只有 `pinctrl_lookup_state`，没有 `pinctrl_select_state`）。想让它生效，你的驱动必须在 `.suspend()` 里自己调 `pinctrl_pm_select_sleep_state(dev)`。**DTS 里写了 `pinctrl-1 = <&x_sleep>` 却没在驱动里调，那份配置永远不会被应用**——而且没有任何日志提醒你。

**误导点二："物业办好了"这句话在 `-EINVAL` 的情况下是假的。**

`-EINVAL` 是唯一会让 probe 直接失败的错误码。而 `07-debug.md` §4 已经证明：`-EINVAL` 在 pinctrl 里有七种语义。**所以一个 DTS 里的拼写错误（function 名写错，`pinmux.c:358`）会让你的设备驱动整个 probe 失败**，而错误消息里说的是 pinctrl 的事，跟你的驱动看起来毫无关系。看到自己的驱动 probe 返回 `-EINVAL` 但自己代码里根本没返回过它，第一个怀疑对象就是这里。

**误导点三：这一步是"before probe"，所以它比你的 `.probe()` 早——但不比另一个设备的 `.probe()` 早。**

pin 控制器本身也是一个 device，也要 probe。如果 pin 控制器排在你后面 probe，你这次就会拿到 `-EPROBE_DEFER`，被放回队列稍后重试。**这不是 bug，是正常的收敛过程**。`07-debug.md` §5.6 讲了为什么核心自己（`core_initcall`）永远不是瓶颈。

---

## §3 mux 和 config 为什么是完全独立的两套东西？

### 先举个例子

装音响的时候你做两件不同的事：

1. **接线**：把功放的输出接到左音箱还是右音箱。这是**离散选择**——接哪儿就是哪儿，没有"接 70% 到左边"这回事。
2. **调参**：音量、高低音、阻抗匹配。这是**连续量**，而且可以同时调好几个，互不冲突。

这两件事用的是完全不同的工具（螺丝刀 vs 旋钮），发生在不同的时间（装机 vs 调试），出错的表现也不一样（没声音 vs 声音难听）。

pinctrl 里 mux 就是接线，config 就是调参。

### 回到代码

分离体现在四个层面，每层都能验证：

| 层面 | mux 侧 | config 侧 |
|---|---|---|
| **文件** | `drivers/pinctrl/pinmux.c` | `drivers/pinctrl/pinconf.c` + `pinconf-generic.c` |
| **Kconfig** | `CONFIG_PINMUX`（`Kconfig:14`） | `CONFIG_PINCONF`（`Kconfig:22`） |
| **ops 结构** | `desc->pmxops`（`struct pinmux_ops`） | `desc->confops`（`struct pinconf_ops`） |
| **map 类型** | `PIN_MAP_TYPE_MUX_GROUP` | `PIN_MAP_TYPE_CONFIGS_PIN` / `PIN_MAP_TYPE_CONFIGS_GROUP` |
| **debugfs** | `pinmux-functions`/`pinmux-pins`/`pinmux-select` | `pinconf-pins`/`pinconf-groups` |
| **应用顺序** | **先** | **后** |

最后那一行是设计上的硬约束，逐字见 `core.c` 的 `pinctrl_commit_state()`：

```c
	/* Apply all the settings for the new state - pinmux first */    /* core.c:1269 */
	list_for_each_entry(setting, &state->settings, node) {
		switch (setting->type) {
		case PIN_MAP_TYPE_MUX_GROUP:
			ret = pinmux_enable_setting(setting);
			...
	/* Apply all the settings for the new state - pinconf after */   /* core.c:1292 */
	list_for_each_entry(setting, &state->settings, node) {
		...
		case PIN_MAP_TYPE_CONFIGS_PIN:
		case PIN_MAP_TYPE_CONFIGS_GROUP:
			ret = pinconf_apply_setting(setting);
```

**同一个 state 的 settings 链表被遍历了两遍**，第一遍只处理 mux（config 分支 `ret = 0` 跳过，`core.c:1275-1278`），第二遍只处理 config（mux 分支 `ret = 0` 跳过，`core.c:1295-1297`）。

**为什么必须 mux 先**：很多 SoC 上引脚的 pull-up 强度寄存器在不同 mux 模式下含义不同，甚至只在特定模式下可写。**先接好线，再调参**。

三个 `CONFIG_PINMUX` / `CONFIG_PINCONF` 都是 `bool "..." if COMPILE_TEST`（`Kconfig:14`、`:22`）——**普通配置界面里看不见**，只能由控制器驱动 `select`。这是刻意的：某些控制器只能改 mux 不能改电气参数，某些反过来。**"两个都有"不是默认，是巧合**。

### 这个类比什么时候会误导你？

**误导点一：接线是"抢占式"的，调参不是。**

同一个引脚不能同时接到两个地方——所以 mux 有一整套所有权账本（`desc->mux_owner`、`desc->mux_usecount`，见 §6）。而 config **没有任何所有权概念**：两个 setting 先后对同一个引脚设 pull-up 和 drive-strength，后者不会报"已被占用"。

实践后果：**config 的冲突是静默的**。两处 DTS 都给同一个引脚设了 `bias-pull-up` 和 `bias-pull-down`，谁最后应用谁生效，不报错。`pinctrl-maps`（`07-debug.md` §3.3）是唯一能看出"有两条 config setting 指向同一个引脚"的地方。

**误导点二：调参这一步可能压根没执行，而且看不出来。**

`pinconf_apply_setting()` 依赖驱动实现 `pin_config_set` / `pin_config_group_set`。核心只在注册时检查"两个里至少有一个"（`pinconf.c:31-33`），所以一个只实现了 `pin_config_group_set` 的驱动，遇到 `PIN_MAP_TYPE_CONFIGS_PIN` 类型的 setting 时会走到 `pinconf.c:156` 那条 `-EINVAL`。这比音响的类比糟：**旋钮是画上去的**。

**误导点三：mux 不可撤销，config 可以。**

逐字引用 `core.c:1326-1332` 的注释：

```c
		/*
		 * All we can do here is pinmux_disable_setting.
		 * That means that some pins are muxed differently now
		 * than they were before applying the setting (We can't
		 * "unmux a pin"!), but it's not a big deal since the pins
		 * are free to be muxed by another apply_setting.
		 */
```

**`We can't "unmux a pin"!`** ——硬件上没有"断开"这个状态，只有"接到别处"。这直接决定了 §8 讲的回滚语义。

---

## §4 `state` 为什么是字符串，而不是枚举？

### 先举个例子

相机上的模式转盘写着「人像」「夜景」「运动」。这些名字不是相机厂商随便起的——它们是**厂商和用户之间的约定**："你转到夜景，我就把快门放慢、ISO 拉高"。

但转盘上还有 C1、C2 两档，叫**自定义模式**。这两档的含义完全由你自己定，相机只负责"你转到 C1，我就调出你存的那组参数"。

pinctrl 的 state 就是这个转盘：四个预定义档位（约定俗成的名字）+ 无限多个自定义档位。**核心不理解任何一个档位的含义**，它只做名字匹配。

### 回到代码

四个"预定义"的 state 名，逐字引用 `include/linux/pinctrl/pinctrl-state.h:33-36`：

```c
#define PINCTRL_STATE_DEFAULT "default"
#define PINCTRL_STATE_INIT "init"
#define PINCTRL_STATE_IDLE "idle"
#define PINCTRL_STATE_SLEEP "sleep"
```

**注意它们是 `#define` 出来的字符串常量，不是 `enum`**。整个文件只有这四行有效代码，前面 22 行全是注释——**约定写在注释里，不写在类型系统里**。

查找的实现是纯 `strcmp`（`core.c:922-932`，全文）：

```c
static struct pinctrl_state *find_state(struct pinctrl *p, const char *name)
{
	struct pinctrl_state *state;

	list_for_each_entry(state, &p->states, node)
		if (!strcmp(state->name, name))     /* core.c:928 */
			return state;

	return NULL;
}
```

**一次线性扫链表 + `strcmp`**。一个设备通常只有 2~4 个 state，所以没人在意性能。

state 名从哪来？DTS 里的 `pinctrl-names`（`06-devicetree.md` §2.2）。**没写 `pinctrl-names` 时第 0 个默认叫 `"default"`**，这是 DT 解析层的行为，不是核心的。

### 这个类比什么时候会误导你？

**误导点一：转盘有档位限制，state 名没有。**

你可以在 DTS 里写 `pinctrl-names = "fast", "slow", "banana";`。核心会老老实实建三个 state。**只是没有任何代码会去 `pinctrl_lookup_state(p, "banana")`**，所以那份配置永远不会被应用，而且**没有任何警告**。

判据：`pinctrl-handles`（`07-debug.md` §3.4）会列出这个 state，但 `current state:` 那行永远不是它。**"state 存在"和"state 会被用"是两件事。**

**误导点二：转到某个档位，相机保证会进入那个状态。state 不保证。**

`pinctrl_lookup_state()` 有一个反直觉的旁路（`core.c:1213-1229`）：

```c
	state = find_state(p, name);
	if (!state) {
		if (pinctrl_dummy_state) {
			/* create dummy state */
			dev_dbg(p->dev, "using pinctrl dummy state (%s)\n",   /* :1222-1223 */
				name);
			state = create_state(p, name);
		} else
			state = ERR_PTR(-ENODEV);                             /* :1226 */
	}
```

**如果某个板级代码调用过 `pinctrl_provide_dummies()`（`core.c:69-71`，把静态变量 `pinctrl_dummy_state` 置 1，`core.c:40`），那么查找任何不存在的 state 都会"成功"**——返回一个空的 state 对象。之后 `pinctrl_select_state()` 遍历一个空 settings 链表，返回 0。**从返回值上完全看不出什么都没做**。

**全树只有一个调用者**，而且它进不了 arm64：

```bash
grep -rln 'pinctrl_provide_dummies' --include=*.c .
# drivers/pinctrl/core.c        （定义）
# drivers/pinctrl/renesas/core.c（唯一调用者）
```

逐字看那个调用（`drivers/pinctrl/renesas/core.c:1377-1379`）：

```c
	/* Enable dummy states for those platforms without pinctrl support */
	if (!of_have_populated_dt())
		pinctrl_provide_dummies();
```

**`!of_have_populated_dt()`** ——只在**没有设备树**的老式板级文件平台上才打开。arm64 必然有 DT，所以这条路在 arm64 上永远不走。但它值得知道，因为它解释了为什么"`pinctrl_select_state()` 返回 0"在原理上不等于"配置生效了"。这也是 `07-debug.md` §3.3 里 `DUMMY_STATE` map 类型的来历。

**误导点三：转盘是互斥的（同时只能在一个档），state 也是——但"当前档位"只对一个 handle 有效。**

`p->state`（`core.h:81`）是**每张工单一个**。同一个引脚被两个设备的 handle 分别管着时，两边各自记着自己的 state，互相不知道。这就是为什么冲突检测必须在**引脚**这一层做（`desc->mux_owner`，§6），而不是在 state 这一层。

---

## §5 `group` 和 `function` 为什么要分成两个维度？

### 先举个例子

一家连锁咖啡店的菜单上有「招牌拿铁」。这家连锁有 3 家门店：三里屯店、望京店、亦庄店。**招牌拿铁在三里屯和望京都能买到，亦庄店没有**。

现在你要下单。光说"我要招牌拿铁"不够——店员会问"哪家店？"。光说"三里屯店"也不够——"要喝什么？"。**你必须同时说清"要什么"和"在哪儿"**。

- 「招牌拿铁」= **function**：一个逻辑功能的名字（`spi0`、`i2c3`、`uart1_rts`）
- 「三里屯店」= **group**：一组具体的物理引脚（`{pin30, pin31, pin32}`）
- 「招牌拿铁在三里屯和望京有供应」= `get_function_groups()` 返回的那个列表

为什么会有多个门店？因为 SoC 封装上，同一个外设常常能引到**几组不同的引脚**上——板子布线方便就用这组，另一块板子用另一组。这叫 pin 复用的"可选路由"。

### 回到代码

驱动侧的契约，逐字引用 `include/linux/pinctrl/pinmux.h:34-38` 的 kerneldoc：

```
 * @get_function_groups: return an array of groups names (in turn
 *	referencing pins) connected to a certain function selector. The group
 *	name can be used with the generic @pinctrl_ops to retrieve the
 *	actual pins affected. The applicable groups will be returned in
 *	@groups and the number of groups in @num_groups
```

注意 `(in turn referencing pins)` 这个插入语——**这是三层间接**：`function` → 一堆 `group` 名 → 每个 group 再展开成一堆 pin 号。所以 `pinctrl_ops.get_group_pins` 和 `pinmux_ops.get_function_groups` 是两个不同 ops 里的两个回调，中间靠**字符串**衔接。

`set_mux()` 的签名同时要两个 selector（`include/linux/pinctrl/pinmux.h:70-71`）：

```c
	int (*set_mux) (struct pinctrl_dev *pctldev, unsigned func_selector,
			unsigned group_selector);
```

逐字引用它的 kerneldoc 末句（`include/linux/pinctrl/pinmux.h:44`）：`On simple controllers the latter argument may be ignored`。也就是说**"每个 function 只有一个 group"的简单控制器可以无视 group 参数**——二维退化成一维。

在 debugfs 上，这个二维关系就是 `pinmux-functions` 的每一行（`07-debug.md` §3.8）：

```
function: spi0, groups = [ spi0_a spi0_b ]
```

左边是 function，方括号里是它的**允许 group 列表**。DTS 里的 `pinmux` 子节点必须同时给出这两个名字，而且组合必须落在这张表里。

### 这个类比什么时候会误导你？

**误导点一：不说门店名，店员不会问你——会随便给你一家。**

这是本节最实用的一条。逐字引用 `pinmux.c:376-387`：

```c
	if (map->data.mux.group) {
		group = map->data.mux.group;
		ret = match_string(groups, num_groups, group);
		if (ret < 0) {
			dev_err(pctldev->dev,
				"invalid group \"%s\" for function \"%s\"\n",
				group, map->data.mux.function);
			return ret;
		}
	} else {
		group = groups[0];              /* pinmux.c:386 */
	}
```

**`else` 分支没有任何日志**：map 里没指定 group 时，核心**静默取该 function 的第 0 个 group**。如果这个 function 有两个 group（`spi0_a` 和 `spi0_b`），你没写 group，拿到的就是 `spi0_a`——**取决于驱动表里谁排在前面**。

这条只影响手工注册 map 的板级代码。走 `pinctrl-single` 或 generic DT 绑定的路径 group 名总是有的（`06-devicetree.md` §3.1）。但它解释了一类"换了内核版本引脚就跑到别的地方去了"的现象——驱动表里两个 group 的次序被人调过。

**误导点二：菜单是店家定的，你查不到"这杯拿铁具体用了哪些咖啡豆"。**

`get_group_pins` 是**可选回调**（`core.c:459` 在它为 NULL 时返回 `-EINVAL`）。驱动不实现它，`pingroups` 里每个 group 就显示零个引脚（`07-debug.md` §3.6），你**无法从内核侧知道 `spi0_a` 到底含哪几个 pin**——只能去查 SoC 手册或读驱动源码里的静态表。

**误导点三：真实的 pinctrl 里"门店"可以只有一个引脚，也可以有几十个。**

group 的粒度完全由驱动决定，核心不做任何约束。`pinctrl-single` 系的驱动常常把**每个引脚各自当成一个 group**（group 名就是 pin 名），这时二维关系在视觉上就消失了。**看到 `pingroups` 里有 500 行、每行一个引脚，不是驱动写错了**，是它选了这种粒度。

---

## §6 `strict` 是什么？为什么同一个引脚能同时"是 SPI 时钟"又"是 GPIO"？

### 先举个例子

一间会议室的白板。默认规矩是"谁在开会谁用"——**签个到就行，签到本上写谁的名字**。

现在有两种签到本：
- **一本**给"正式会议"用（对应 **mux**：这个引脚被配成了 SPI 时钟）
- **另一本**给"临时借用"用（对应 **GPIO**：某个驱动把它当普通开关口在读写）

宽松制度（**非 strict**）：两本签到本各管各的。一个人在正式会议本上签了名，另一个人还能在临时借用本上签同一间会议室——**物理上两边都能进门**，撞车了自己看着办。

严格制度（**strict**）：两本合成一本。任何一本上有名字，另一本就不许再签。

哪种制度取决于**这栋楼的物业规定**，也就是控制器驱动的一个布尔字段。

### 回到代码

字段就一个 `bool`（`include/linux/pinctrl/pinmux.h:82`），kerneldoc 逐字（`include/linux/pinctrl/pinmux.h:56-58`）：

```
 * @strict: do not allow simultaneous use of the same pin for GPIO and another
 *	function. Check both gpio_owner and mux_owner strictly before approving
 *	the pin request.
```

两本签到本就是 `struct pin_desc`（`core.h:159-171`）上的几个字段：

```c
	/* These fields only added when supporting pinmux drivers */   /* core.h:164 */
#ifdef CONFIG_PINMUX
	unsigned mux_usecount;                       /* :166  正式会议本的签到人数 */
	const char *mux_owner;                       /* :167  正式会议本上的名字 */
	const struct pinctrl_setting_mux *mux_setting;  /* :168 */
	const char *gpio_owner;                      /* :169  临时借用本上的名字 */
#endif
```

注意整段包在 `#ifdef CONFIG_PINMUX` 里（`core.h:165-170`）——**`CONFIG_PINMUX=n` 时这四个字段压根不存在**，也就没有所有权这回事（§3 讲过这个开关）。

`pin_request()` 里两个检查条件，逐字（`pinmux.c:127-128` 和 `:135`）：

```c
	if ((!gpio_range || ops->strict) &&
	    desc->mux_usecount && strcmp(desc->mux_owner, owner)) {      /* :127-128 */
		...
	if ((gpio_range || ops->strict) && desc->gpio_owner) {           /* :135 */
```

把 `ops->strict` 代入，四种情形一张表看完：

| 谁在申请 | `strict = false` | `strict = true` |
|---|---|---|
| **mux 侧**（`gpio_range == NULL`） | 检查 `mux_owner` ✔ / 不检查 `gpio_owner` | 检查 **两本** |
| **GPIO 侧**（`gpio_range != NULL`） | 不检查 `mux_owner` / 检查 `gpio_owner` ✔ | 检查 **两本** |

也就是说 **`strict` 的全部作用就是把那两个 `||` 短路条件变成恒真**。

本树上 **29 个驱动设了 `.strict = true`**（重跑命令和名单见 `07-debug.md` §3.9）：

```bash
grep -rln '\.strict[[:space:]]*=[[:space:]]*true' drivers/pinctrl/ | wc -l
```

**qcom 系（arm64 上最常见的一族）全都没设**，所以在高通平台上 mux 和 GPIO 共占是允许的。

### 这个类比什么时候会误导你？

**误导点一：签到本上有名字 ≠ 硬件真的被那个人占着。**

`pinmux-select`（`07-debug.md` §3.10）直接调 `set_mux()`，**完全绕过 `pin_request()`**。硬件被改了，`mux_owner` 一个字没动。所以"签到本"和"会议室里坐着谁"在你 `echo` 过一次之后就永久脱节了，**除了重启没有办法修**。

**误导点二：宽松制度下"撞车了自己看着办"这句话对硬件不成立——撞车的后果是不可诊断的。**

非 strict 控制器上，一个驱动把引脚配成 SPI 时钟，另一个驱动同时用 GPIO API 去拉低它，**内核不会报任何错**。你看到的现象是 SPI 传输偶发失败。`pinmux-pins`（`07-debug.md` §3.9）会同时显示 mux owner 和 gpio owner——**这是唯一能发现它的地方**，而且要你事先怀疑到才会去看。

**误导点三：同一个人在同一本上重复签到是允许的，而且不计入冲突。**

逐字看 `pinmux.c:145-147`：

```c
		desc->mux_usecount++;
		if (desc->mux_usecount > 1)
			return 0;
```

**`mux_usecount` 是计数器不是布尔**。`strcmp(desc->mux_owner, owner)` 那个条件（`:128`）意味着**同名 owner 重复 request 直接放行**。这是 hog 和消费者能共用引脚的机制基础（§7），也是 `pinmux.c:225` 那个 `WARN_ON(!desc->mux_usecount)` 会响的原因——**释放次数多于申请次数**。

---

## §7 hog 是什么？为什么"配线架的管理员"要给自己开工单？

### 先举个例子

回到 §1 那个配线架机房。绝大多数端子都是给楼里的办公室用的。但有几个端子是**机房自己要用**的：机房的照明开关、门禁读卡器、还有一个必须一开机就拉高否则整栏机柜断电的使能脚。

这些端子的特点是：**没有哪个"办公室"会来申请它们**，但它们必须在开机时就配好，而且之后一直保持。

管理员的做法是给自己也填一张工单，收件人写自己的名字。这张工单在机房开门（控制器注册）的那一刻就自动执行。

英文里 hog 是"占着不放的人"。这个词就是这个意思：**控制器自己占住的引脚，从此不再对外开放**。

### 回到代码

"是不是 hog"这件事**不是一个标志位，而是一个字符串比较**。逐字引用 `include/linux/pinctrl/machine.h:56-58`（`struct pinctrl_map.dev_name` 的 kerneldoc）：

```
 *	must be the same as in your struct device*. If this name is set to the
 *	same name as the pin controllers own dev_name(), the map entry will be
 *	hogged by the driver itself upon registration
```

**判据就是 `map->dev_name == map->ctrl_dev_name`**。所以那两个便利宏只是把同一个参数填两遍（`machine.h:102-106`，逐字）：

```c
#define PIN_MAP_MUX_GROUP_HOG(dev, state, grp, func)			\
	PIN_MAP_MUX_GROUP(dev, state, dev, grp, func)

#define PIN_MAP_MUX_GROUP_HOG_DEFAULT(dev, grp, func)			\
	PIN_MAP_MUX_GROUP(dev, PINCTRL_STATE_DEFAULT, dev, grp, func)
```

对比非 hog 版 `PIN_MAP_MUX_GROUP(dev, state, pinctrl, grp, func)`（`machine.h:87-97`）——第 3 个参数 `pinctrl` 是控制器名。hog 版把 `dev` 传了两次。**没有 `.is_hog = true` 这种字段**。

执行的地方是 `pinctrl_claim_hogs()`（`core.c:2073-2109`），全文 37 行：

| 行 | 动作 | 值得注意的 |
|---|---|---|
| `:2075` | `create_pinctrl(pctldev->dev, pctldev)` | **第二个参数非 NULL** 是关键——它让 `create_pinctrl()` 走 hog 筛选分支（见下） |
| `:2076-2080` | 返回 `-ENODEV` → `dev_dbg "no hogs found"` + **`return 0`** | **没有 hog 是完全正常的**，绝大多数控制器就是这样 |
| `:2082-2087` | 其他错误 → `dev_err "error claiming hogs: %li"` + 往上传 | 这条会让控制器 probe 失败 |
| `:2089-2099` | 查 `default` 并**立即应用** | 查不到只打 `dev_dbg "failed to lookup the default state"`（`:2092-2093`）；应用失败才打 `dev_err "failed to select default state"`（`:2097-2098`） |
| `:2101-2106` | 查 `sleep`，**只查不用** | 存到 `pctldev->hog_sleep`（`core.h:60`），等 PM 回调用 |
| `:2108` | `return 0` | |

hog 筛选的那三行在 `create_pinctrl()` 里，逐字引用 `core.c:1069-1078`：

```c
		/*
		 * If pctldev is not null, we are claiming hog for it,
		 * that means, setting that is served by pctldev by itself.
		 *
		 * Thus we must skip map that is for this device but is served
		 * by other device.
		 */
		if (pctldev &&
		    strcmp(dev_name(pctldev->dev), map->ctrl_dev_name))
			continue;
```

同一个 `create_pinctrl()` 被两种调用方复用：消费者路径传 `pctldev = NULL`（不过滤 `ctrl_dev_name`），hog 路径传非 NULL（**只要那些自己伺候自己的 map**）。

hog 的 sleep 由**控制器专用的那两个 helper** 应用——`pinctrl_force_sleep()`（`core.c:1510-1515`）和 `pinctrl_force_default()`（`:1522-1527`），注意它们的名字里没有 `pm_select`，和消费者用的 `pinctrl_pm_select_sleep_state()` 是两套。两者都先检查 `!IS_ERR(pctldev->p) && !IS_ERR(pctldev->hog_sleep)`（`:1512` / `:1524`），**hog 没查到 sleep state 时静默返回 0**。

### 这个类比什么时候会误导你？

**误导点一：管理员给自己开工单，会造成一个循环——代码里为此埋了三个特判。**

配线架要给自己开工单，就得先有配线架。这是个鸡生蛋问题。代码用三处特判绕过：

| 位置 | 逐字注释 | 干什么 |
|---|---|---|
| `core.c:980` | `/* Do not defer probing of hogs (circular loop) */` | **hog 的 setting 解析失败时返回 `-ENODEV` 而不是 `-EPROBE_DEFER`**——deferring 一个还没注册完的自己会死锁 |
| `core.c:1287` | `/* Do not link hogs (circular dependency) */` | mux 应用后不建 device_link（判据是 `p != setting->pctldev->p`，`:1288`） |
| `core.c:1311` | 同上 | config 应用后同样不建 |

后两处的判据 `p != setting->pctldev->p` 很值得读一遍：**"这张工单就是控制器自己那张工单吗？"** 是就跳过。这比用一个标志位更省事，也更容易读错。

**误导点二：hog 占住的引脚在 `pinmux-pins` 里有专门的标记，但那个标记的判据是间接的。**

`pinmux-pins` 会在行尾加 ` (HOG)`——strict 分支在 `pinmux.c:620`，非 strict 分支在 `:634`。判据是 `pinmux.c:612-614` 那三行（逐字）：

```c
		if (desc->mux_owner &&
		    !strcmp(desc->mux_owner, pinctrl_dev_get_name(pctldev)))
			is_hog = true;
```

**owner 字符串等于控制器自己的名字**，就算 hog。所以 `(HOG)` 不是从 map 里带过来的，而是**每次 `cat` 时重新比字符串算出来的**。

实践含义：一个控制器如果换了 `dev_name`（比如 DTS 节点地址改了），`(HOG)` 标记会消失，而实际配置一点没变。

**误导点三：hog 不代表"不许别人用"——它只是先占了一份计数。**

回到 §6 那个 `mux_usecount`。hog 占住引脚后 `mux_usecount = 1`、`mux_owner = <控制器名>`。如果后来一个消费者设备也要这个引脚，`strcmp(desc->mux_owner, owner)` 不相等（`pinmux.c:128`），**会报 `already requested by`**。

但如果那个消费者恰好也叫同一个名字（不可能）或者是**同一个 hog 的第二条 map**，`mux_usecount` 会加到 2 而不报错（`pinmux.c:146-147`）。所以 hog **不是排他锁，是第一个签到的人**。

---

## §8 pinctrl 应用失败时的"回滚"，为什么回滚不干净？

### 先举个例子

你在改一个变电站的接线，手上有一张 8 步的作业单。做到第 5 步发现继电器坏了，做不下去。

按理说你要"撤销前 4 步"。但接线这种事有个特点：**你没法把一根线"拔出来悬空"**——线要么接在 A 上，要么接在 B 上。所谓"撤销"，只能是"再接回原来那个位置"。

而如果第 3 步做完之后有人偷偷用了那个端子，你"接回去"的时候端子已经不在原来的样子了。

pinctrl 的回滚就是这个处境。

### 回到代码

`pinctrl_commit_state()` 的失败出口，逐字引用 `core.c:1320-1339`：

```c
unapply_new_state:
	dev_err(p->dev, "Error applying setting, reverse things back\n");    /* :1321 */

	list_for_each_entry(setting2, &state->settings, node) {
		if (&setting2->node == &setting->node)                      /* :1324 */
			break;
		/*
		 * All we can do here is pinmux_disable_setting.
		 * That means that some pins are muxed differently now
		 * than they were before applying the setting (We can't
		 * "unmux a pin"!), but it's not a big deal since the pins
		 * are free to be muxed by another apply_setting.
		 */
		if (setting2->type == PIN_MAP_TYPE_MUX_GROUP)               /* :1333 */
			pinmux_disable_setting(setting2);
	}

	/* There's no infinite recursive loop here because p->state is NULL */  /* :1337 */
	if (old_state)
		pinctrl_select_state(p, old_state);
```

三件事逐条看：

| 代码 | 语义 | 后果 |
|---|---|---|
| `:1323-1325` | 从头遍历，遇到**出错的那一条**就 `break` | 只撤销已经成功的部分。判据是**指针相等**（`&setting2->node == &setting->node`），依赖 `setting` 这个循环变量在跳转时还指着出错的那条 |
| `:1333-1334` | **只对 `PIN_MAP_TYPE_MUX_GROUP` 做 disable，config 一律不动** | **已经写下去的 pull-up / drive-strength 留在硬件里**。回滚是不完整的，而且这一点不体现在任何返回值上 |
| `:1337-1339` | 有旧 state 就递归切回去 | 逐字注释解释了为什么不会无限递归：`p->state` 在 `:1267` 已被置 NULL |

再看开头那段，`core.c:1260-1264`——**切换开始前先把旧 state 的所有 mux setting 全 disable 掉**：

```c
		list_for_each_entry(setting, &old_state->settings, node) {
			if (setting->type != PIN_MAP_TYPE_MUX_GROUP)
				continue;
			pinmux_disable_setting(setting);
		}
```

逐字引用它上面那句注释（`core.c:1256-1258`）：`Any pingroups which are still owned by the new state will be re-acquired by the call to pinmux_enable_setting() in the loop below.` ——**先全放开、再全申请**，而不是逐个比对差异。

### 这个类比什么时候会误导你？

**误导点一：作业单可以"回到动工前"，pinctrl 的 mux 不能。**

`We can't "unmux a pin"!`（`core.c:1329-1330`，逐字）。`pinmux_disable_setting()`（`pinmux.c:486-534`）做的只是**把账本上的 owner 划掉**——真正动手的是它调的 `pin_free()`（`pinmux.c:521`）：`mux_usecount--`（`:227`）、`desc->mux_owner = NULL`（`:246`）、`desc->mux_setting = NULL`（`:247`）。

关键在于 **`set_mux` 没有对偶**：`pmxops->set_mux` 在整个核心里只被调用两次（`pinmux.c:464` 和 `:750`），两处都在"启用"侧；拆除侧一次都不调，也不存在"把上一个 mux 值写回去"的代码。所以硬件停在最后一次 `set_mux()` 写进去的模式上。

（唯一的例外通道是 `pmxops->free`（`pinmux.c:238-239`）——驱动可以在这里做点什么。但按 `include/linux/pinctrl/pinmux.h:27-29` 的定义它是 `request()` 的逆操作，不是"恢复 mux"，而且只有 29 个驱动实现了它。）

所以一次失败的 `pinctrl_select_state()` 之后，硬件处于一个**既不是新 state、也不是旧 state 的中间态**。日志里只有一行 `Error applying setting, reverse things back`，而 `pinmux-pins` 会显示"没人占着"——**账本干净，硬件是脏的**。这和 `pinmux-select` 造成的脱节（§6 误导点一）是同一类问题的两个方向。

**误导点二："先全放开再全申请"意味着切换过程中有一个窗口，所有引脚都是无主的。**

`core.c:1260-1264` 之后到 `:1289` 之间，旧 state 的引脚在账本上都没有 owner。如果这期间另一个 CPU 上有别的设备来 `pin_request()` 同一个引脚，它会**成功**。之后你这边 `pinmux_enable_setting()` 再去申请就会撞车。

这个窗口靠什么保护？`pinctrl_select_state()` 的调用者要拿 `p` 的锁，而所有权检查在 `pctldev->mutex` 下。**两把不同的锁**，所以窗口在理论上存在。实践中很少踩到是因为一个引脚通常只有一个消费者。这一条属于"读代码推断出的、未观测到的"，已登记在 `99-evidence.md` §5.3。

**误导点三：config 不回滚，通常不是 bug。**

看起来像遗漏，其实是有意的：config 是**幂等的绝对赋值**（"pull-up 设为 2000 欧"），不是增量操作。下一次成功的 state 切换会把它覆盖掉。只有在"新 state 设了某个参数、旧 state 没设"的情况下才会留下痕迹——**而 pinctrl 没有"恢复默认值"这个操作**，所以就算想回滚也无从下手。

判据：`pinconf.c` 里没有任何 `_undo` / `_restore` 类函数：

```bash
grep -n 'undo\|restore\|revert' drivers/pinctrl/pinconf.c drivers/pinctrl/pinmux.c
# 空
```

---

## 阅读下一篇

| 你现在的状态 | 去哪 |
|---|---|
| 概念清楚了，想快速看全貌 | `00-overview.md` |
| 要写代码，想查函数签名 | `01-api.md` |
| §1 那四层结构想看完整字段 | `02-datastructures.md` §2 |
| 想看 §2 那条时间线的完整版（注册 → hog → 消费者） | `03-internals.md` Part A |
| 想知道 §5 的泛型 group/function helper 是怎么实现的 | `04-extensions.md` |
| 要写控制器驱动，想看哪些回调必填 | `05-usage.md` §2 + `07-debug.md` §4.2 |
| 要写 DTS | `06-devicetree.md` |
| §6/§8 讲的那些"账本和硬件脱节"要怎么看出来 | `07-debug.md` §3.9、§3.10 |
| 想知道本篇每条论断的出处 | `99-evidence.md` §3（逐篇取证表） |

**本篇八个主题的一句话索引**：

| § | 一句话 |
|---|---|
| §1 | `pinctrl_get()` 拿到的是一份**预编译清单**，不是硬件资源；pinctrl 没有 `bus_type` |
| §2 | 引脚是**驱动核心**在你 `.probe()` 之前配好的（`dd.c:616`），而且它吞掉了除 `-EPROBE_DEFER`/`-EINVAL` 之外的所有错误 |
| §3 | mux 和 config 是两套独立子框架，**mux 必须先应用**；mux 有所有权、config 没有 |
| §4 | state 是**字符串**不是枚举，核心只做 `strcmp`；写了没人用的 state 名不会报错 |
| §5 | function × group 是**二维**的；map 里不写 group 会静默取第 0 个 |
| §6 | `strict` 只做一件事：把 mux 和 GPIO 两本签到本合成一本 |
| §7 | hog 的判据是 `dev_name == ctrl_dev_name` 这个**字符串相等**，不是标志位 |
| §8 | 回滚只撤 mux 的**账本**、不撤硬件，也完全不撤 config |
