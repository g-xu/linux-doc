---
title: 费曼学习法
---

# reset 08 —— 大白话解释这个子系统里最难想通的几件事

八个题目，每个四段：**问题 → 先举个例子 → 回到代码 → 这个类比什么时候会误导你**。
类比只负责把你送到门口，进门之后一律看行号。arm64-only。

| # | 问题 |
|---|---|
| §1 | 复位不就是拉一根线吗，为什么要一整个框架？ |
| §2 | shared 和 exclusive 到底在共享什么？它和"脉冲/电平"是同一件事吗？ |
| §3 | 已经有 exclusive 了，为什么还要 acquire / release？ |
| §4 | `rearm()` 在"重新武装"什么？ |
| §5 | 为什么 `deassert()` 可能什么都没做，还返回 0？ |
| §6 | 为什么一个时钟驱动会顺手提供复位？ |
| §7 | 为什么核心完全不管 DTS 里那几个数字是什么意思？ |
| §8 | 为什么 clk 有一大棵 debugfs 树，reset 一个观测点都没有？ |

---

## §1 复位不就是拉一根线吗，为什么要一整个框架？

### 先举个例子

想象一栋写字楼的**总闸箱**。箱子里有一排小开关，每个开关管一层楼。
如果每层楼的租户都能直接伸手进箱子拨开关，会出两种事故：

- 3 楼的人要重启自己的设备，顺手把 4 楼的闸也碰了；
- 5 楼和 6 楼共用一个闸，5 楼下班时把闸拉了，6 楼还在加班。

于是物业做了两件事：**箱子锁起来**，谁要动闸都得打电话给物业；
物业记一个本子，写清"这个闸现在有几家在用"。

reset 框架就是这个物业。它本身不发电、不管设备，只做两件事：
**把"哪一位对应哪一层"这件知识收在一个地方**，以及**记账**。

### 回到代码

「箱子锁起来」= 消费者永远拿不到寄存器地址，只能拿到一个不透明的
`struct reset_control *`（`02-datastructures.md` §1.1），
真正碰硬件的是控制器驱动的四个回调。

「记本子」= 每个句柄身上三个计数器：`deassert_count`、`triggered_count`、`acquired`
（`03-internals.md` §B2 那张三计数表）。

「哪一位对应哪一层」= DTS 里的 cell + 控制器的 `of_xlate`（§7）。

整个核心只有 **1289 行**（`drivers/reset/core.c`），
里面**没有一行碰寄存器**——这是判断"框架"和"驱动"分界的最直接办法。

### 这个类比什么时候会误导你？

物业的本子是**权威**的：本子上写"没人在用"，闸就一定是拉下来的。
**reset 框架的账本不是权威的。**
官方文档专门写了这一段（`Documentation/driver-api/reset.rst:98-104`）：
调用 `assert()` 之后，**框架不保证那条线真的处于被断言状态**。

原因有两层：(a) shared 语义下别人可能还持着（§2）；
(b) 控制器可以根本没实现 `.assert` 回调，而这在框架看来是合法的（§5）。

所以正确的心智模型是：**框架管的是"谁申请了什么"，不是"硬件现在是什么样"。**
想知道硬件真实状态，只有读寄存器（`07-debug.md` §3.2）。

---

## §2 shared 和 exclusive 到底在共享什么？它和"脉冲/电平"是同一件事吗？

这是整个子系统**最容易被类比错的一组关系**，也是我写这套文档时自己搞错过的一处。

### 先举个例子

先说两件**完全无关**的事。

**第一件事：这台打印机是我一个人的，还是办公室公用的？**

- 我一个人的（exclusive）→ 我想什么时候关机就关机。
- 办公室公用的（shared）→ 我不能因为自己打完了就拔电源，
  得等所有人都说"我不用了"才能关。于是需要一个计数：**还有几个人在用**。

**第二件事：这台机器的电源开关是什么形状？**

- **点动按钮**（自解除脉冲）：按下去它自己弹回来，机器重启一次。
  你没法"一直按住"。
- **翘板开关**（电平保持）：拨到 OFF 它就停在 OFF，机器一直处于关闭状态，
  要拨回 ON 才恢复。

现在关键问题来了：**第一件事和第二件事有关系吗？**

没有。公用的机器可以是点动按钮，独占的机器可以是翘板开关，四种组合都存在。

**这就是 reset 框架里两条正交的轴**：

| 轴 | 取值 | 谁决定的 | 对应哪对 API |
|---|---|---|---|
| **归属** | exclusive / shared | **你写驱动时选的 get 变体** | 决定是否记账 |
| **硬件形状** | 自解除脉冲 / 电平保持 | **硬件**（看绑定文档和手册） | `reset()`+`rearm()` / `deassert()`+`assert()` |

**绝大多数人的误解是把两条轴叠成一条**，认为
"shared 就该用 `reset()`、exclusive 才能用 `assert()`"。**错。**

### 回到代码

**证据一：官方文档明说 shared 要配平的是 assert/deassert。**

`Documentation/driver-api/reset.rst:94-96`：

> For shared reset controls, calls to the two functions must be balanced.

也就是说"shared + 电平保持"这一格是**官方推荐写法**，不是变通。

**证据二：`drivers/ata/libahci_platform.c` 把第二条轴写成了两段并列代码，
而它的句柄一直是 shared。**

```c
	if (hpriv->f_rsts & AHCI_PLATFORM_RST_TRIGGER)
		return reset_control_reset(hpriv->rsts);

	return reset_control_deassert(hpriv->rsts);
```

（`drivers/ata/libahci_platform.c:161-164`；下电侧的对称写法在 `:180-183`）

那个 `if` 的条件是 `AHCI_PLATFORM_RST_TRIGGER` —— **一个描述硬件形状的标志**，
不是"是不是共享"。句柄来自 `devm_reset_control_array_get_optional_shared()`
（`:536`），两条分支都是 shared。

**证据三：真正的禁忌被两个 `WARN_ON` 钉住了。**

```c
	if (rstc->shared) {
		if (WARN_ON(atomic_read(&rstc->triggered_count) != 0))
			return -EINVAL;

		if (WARN_ON(atomic_read(&rstc->deassert_count) == 0))
			return -EINVAL;
```

（`drivers/reset/core.c:453-458`）

第一条是：**你之前用 `reset()` 触发过，现在又来 `assert()`** —— 混用两对 API。
第二条是：**`assert()` 比 `deassert()` 多了一次** —— 没配平。

⇒ 规则一句话：**一个 shared 句柄，两对 API 二选一，选定之后必须配平。**
不是"shared 不能用 assert"。

### 这个类比什么时候会误导你？

打印机的类比在一个地方会骗你：**办公室里最后一个人关机时，机器一定会关。**

reset 框架里不一定。`deassert_count` 从 1 掉到 0 时框架**会**去调
`.assert` 回调——但如果那个控制器驱动**没实现 `.assert`**，
shared 分支下框架直接返回 0（`drivers/reset/core.c:467-468`，
注释原文是 "doing nothing is a valid option"）。

于是就有了这个反直觉的结果：**你的代码完全正确、返回值全是 0、计数全归零，
而那条线从头到尾没被断言过。** 这不是 bug，是 §5 的主题。

还有一个更细的不对称：同一个"回调缺失"的情况，
**exclusive 分支返回 `-ENOTSUPP`**（`:475-476`）而 shared 分支返回 0。
**同一块硬件，你换个 get 变体，错误码就变了。**

---

## §3 已经有 exclusive 了，为什么还要 acquire / release？

### 先举个例子

会议室预订系统。有两种"独占"：

- **长租**：这间会议室整年归市场部，别人连预订页面都看不到。
- **按小时预订**：会议室谁都能约，但同一时段只能一家。
  你约了 10-11 点，这一小时内它就是你独占的；11 点一到自动交还。

普通的 `get_exclusive()` 是长租：第一个拿到的人从此独占，
第二个来的人直接被拒（`WARN_ON` + `-EBUSY`）。

但硬件上常有这种情况：**一条复位线物理上只能一个主人，可是这个主人要换。**
Tegra 的 2D 引擎和 3D 引擎共用一条内存控制器复位线，
谁在跑谁独占，跑完交还。长租模型下这根本没法表达——
两个驱动都要 probe，第二个必然失败。

于是有了"按小时预订"：**拿句柄和拿所有权分成两步。**

### 回到代码

第一步的关键是 `__reset_control_get_internal()` 里那个 `break`：

```c
		if (!rstc->shared && !shared && !acquired)
			break;

		if (WARN_ON(!rstc->shared || !shared))
			return ERR_PTR(-EBUSY);
```

（`drivers/reset/core.c:763-767` 一带，完整函数在 `03-internals.md` §A3）

`_released` 变体传 `acquired = false`，于是走 `break` 去**新建一个独立句柄**——
两个驱动都能 probe 成功。

第二步是运行时抢所有权：

```c
	list_for_each_entry(rc, &rstc->rcdev->reset_control_head, list) {
		if (rstc != rc && rstc->id == rc->id) {
			if (rc->acquired) {
				mutex_unlock(&reset_list_mutex);
				return -EBUSY;
			}
		}
	}
```

（`drivers/reset/core.c:660-667`）—— 扫一遍同 id 的其他句柄，
有人 `acquired` 就 `-EBUSY`。

第三步是**忘了 acquire 会被抓住**：

```c
			WARN(1, "reset %s (ID: %u) is not acquired\n",
```

（`drivers/reset/core.c:479`，deassert 路径；`:549` 是 reset 路径）
后面跟 `-EPERM`（`:481`）。

### 这个类比什么时候会误导你？

会议室系统在你预订的瞬间就检查冲突。**reset 框架不是。**

看 `:763` 那行判据：它只看**新请求**的 `acquired` 是不是 false，
**完全不看已有句柄现在有没有被 acquire**。
所以"第二个驱动拿句柄"这一步永远成功——
冲突被推迟到运行期第一次 `reset_control_acquire()` 才暴露。

实际后果：**probe 阶段一切正常，压力测试跑起来才出 `-EBUSY`。**
这也解释了 `drivers/gpu/drm/tegra/gr3d.c:354-366` 那段写法——
probe 时特意 get → acquire → release → put 一轮，
就是为了在 probe 阶段提前把冲突探出来（`05-usage.md` §5.2）。

另一个误导点：会议室到点自动释放，**`release()` 不会自动发生**，
而且它**不加锁、不检查、也不改硬件状态**。先 `release()` 再 `assert()`
= 对着已经交还的句柄操作。所以那个四步顺序必须背下来（`05-usage.md` §5.1）。

---

## §4 `rearm()` 在"重新武装"什么？

### 先举个例子

老式相机的**一次性闪光灯泡**。装一个泡，按快门闪一次，泡就废了；
要再闪一次，得先换个新泡——这个"换泡"的动作就是 rearm（重新装填）。

现在把它放进办公室共用的场景：一台共用相机，三个人都想闪一次。
第一个人按快门 → 闪了。第二、三个人按快门 → 什么也没发生（泡废了）。
**要等三个人都说"我拍完了，换泡吧"，才允许下一次闪光。**

这正是共享的自解除脉冲复位线：**第一个 `reset()` 真的发脉冲，
后面的调用不发；所有消费者都 `rearm()` 过之后，才允许下一次真的发。**

### 回到代码

官方解释在 `Documentation/driver-api/reset.rst:115-122`。
计数就是 `triggered_count`：

```c
	if (rstc->shared) {
		if (WARN_ON(atomic_read(&rstc->deassert_count) != 0))
			return -EINVAL;

		WARN_ON(atomic_dec_return(&rstc->triggered_count) < 0);
	} else {
		if (!rstc->acquired)
			return -EPERM;
	}
```

（`drivers/reset/core.c:412-420`，完整函数 `:401-423`）

三点：

1. **shared 分支**：递减 `triggered_count`，就是"换泡"。
   减到负数会 WARN —— 说明你 rearm 的次数比 reset 多。
2. **第一个 `WARN_ON`**：`deassert_count != 0` 就报错，
   这是在拦"你把两对 API 混着用了"（和 §2 那两条拦截同源）。
3. **exclusive 分支什么计数都不动**，只检查 `acquired`。
   所以对 exclusive 句柄，`rearm()` 的唯一作用是"如果你没 acquire 就骂你 `-EPERM`"。

### 这个类比什么时候会误导你？

闪光灯泡的类比有一个硬伤：**灯泡是物理耗材，装了就是装了。**
`triggered_count` 只是一个内存里的整数，**硬件根本不知道它的存在**。

具体误导在哪：如果三个消费者里有一个**忘了 `rearm()`**，
`triggered_count` 永远回不到 0，于是**后面所有人的 `reset()` 都变成静默的空操作**
——没有错误码、没有 WARN、没有日志。硬件明明还能闪，框架却认定"泡没换"。

这个故障的现场特征是：**系统跑一段时间之后，某个设备的复位突然不生效了，
重启就好**。排查办法只有一条：审计每一处 `reset_control_reset()`
是否都有配对的 `rearm()`（`07-debug.md` §6 第 6 行）。

---

## §5 为什么 `deassert()` 可能什么都没做，还返回 0？

### 先举个例子

你去柜台办一件事，工作人员说"好的，办完了"。有两种"办完了"：

- **真的办完了**：他确实在系统里点了确认。
- **"这项业务本来就不需要办"**：比如你要求"取消欠费停机状态"，
  而这家营业厅的系统里根本没有"停机"这个状态——从来就没停过，
  所以他也确实不用做任何操作，"办完了"这个回答也不算撒谎。

第二种在 reset 框架里是**被官方允许的**。

### 回到代码

官方文档在讲控制器驱动要提供哪些回调时，用一句话收尾
（`Documentation/driver-api/reset.rst:155-161`）：

> All functions are optional.

**四个回调（assert / deassert / reset / status）一个都不实现，也是一个合法的
复位控制器驱动。** 而 `deassert` 是这四个里态度最宽松的一个：

```c
	/*
	 * If the reset controller does not implement .deassert(), we assume
	 * that it handles self-deasserting reset lines via .reset(). In that
	 * case, the reset lines are deasserted by default. If that is not the
	 * case, the reset controller driver should implement .deassert() and
	 * return -ENOTSUPP.
	 */
	if (!rstc->rcdev->ops->deassert)
		return 0;
```

（`drivers/reset/core.c:555-563`）

这段注释就是那句"这项业务本来就不需要办"的原文：
**核心默认"没实现 `.deassert` 就说明这条线是自解除的、本来就处于解除状态"**，
于是无条件返回 0 —— **shared 和 exclusive 都一样**。

对比一下另外两个动作，才看得出这个选择有多特殊：

| 动作 | 回调缺失时 | 位置 |
|---|---|---|
| `reset` | `-ENOTSUPP`，两种语义一致 | `drivers/reset/core.c:340-341` |
| `assert` | shared 返回 0、exclusive 返回 `-ENOTSUPP` | `drivers/reset/core.c:467-468` / `:475-476` |
| **`deassert`** | **一律 0** | `drivers/reset/core.c:562-563` |

现实里为什么会有这种驱动？三种典型：

| 形态 | 例子 | 为什么只实现一部分 |
|---|---|---|
| 固件只提供"复位一次"这一个命令 | `drivers/reset/reset-raspberrypi.c:68-71`（ops 表里只有 `.reset`） | 固件接口没有"保持断言"的语义 |
| 固件协议不提供读状态 | `drivers/reset/reset-scmi.c:83-87`（无 `.status`） | SCMI 规范里就没这条消息 |
| 平台上这条线其实是常释放的 | 只注册占位，让消费者驱动的通用代码跑通 | 让一份消费者代码同时适配"有复位线"和"没复位线"两种板子 |

### 这个类比什么时候会误导你？

营业厅的类比会让你以为**"办完了"至少代表状态是对的**。
reset 框架里连这个都不保证——回调缺失时它连"这条线现在什么状态"都不知道。

更要命的是这个组合：**`_optional_` 变体 + 没写 `resets` 属性**。
此时句柄是 `NULL`，动作函数第一行就 `if (!rstc) return 0;`
（例如 `drivers/reset/core.c:403-404`），于是：

```
   驱动作者的心智：deassert 返回 0 → 复位解除了 → 可以访问寄存器了
   实际发生的事：  句柄是 NULL → 什么都没做 → 硬件仍在复位态 → 读寄存器全 0xffffffff
```

⇒ 一条实用规则：**用了 `_optional_` 就必须自己判断句柄是不是 NULL，
不能用返回值代替**。这不是防御性编程，这是这套 API 的语义要求。
完整的六种"返回 0 但硬件没动"在 `07-debug.md` §6。

---

## §6 为什么一个时钟驱动会顺手提供复位？

### 先举个例子

小区物业的**配电间**。同一个铁柜子里装了两样东西：
一排空气开关（管"通不通电"）和一排跳闸按钮（管"重启一下"）。
它们物理上就在一个柜子里，共用一把钥匙、共用一张回路图。

如果软件上非要把这个柜子拆成"电闸管理科"和"跳闸管理科"两个部门，
两个部门还要各自维护一份一模一样的回路图——那才是别扭的。

SoC 里的 CRU（Clock and Reset Unit）就是这个柜子：**同一片寄存器区域里，
一部分位控制时钟门控，另一部分位控制复位。** 于是写驱动的人自然会在
一个 probe 里注册两个身份。

### 回到代码

DTS 侧的证据是**一个节点同时声明两个 cell 数**：

```dts
	cru: clock-controller@ff760000 {
		compatible = "rockchip,rk3399-cru";
		...
		#clock-cells = <1>;
		#reset-cells = <1>;
```

（`arch/arm64/boot/dts/rockchip/rk3399.dtsi:1449-1456`，展开在 `06-devicetree.md` §7）

驱动侧的证据是数量：**`drivers/clk/` 下有 33 个文件在注册复位控制器**
（统计口径见 `04-extensions.md` §6）。也就是说，
「复位控制器驱动住在 `drivers/clk/` 而不是 `drivers/reset/`」是常态，不是例外。

极端一点的例子是**连 ops 表都直接借用**：

```c
	.ops = &reset_simple_ops,
```

（`drivers/clk/bcm/clk-bcm2711-dvp.c` 里指向 `drivers/reset/reset-simple.c` 导出的那张表）

这条能成立，是因为 `reset_simple_ops` 是 `EXPORT_SYMBOL_GPL` 的公共 ops
——`reset-simple` 在框架里的角色不只是"一个驱动"，还是"一段可复用的库代码"
（`04-extensions.md` §1）。

### 这个类比什么时候会误导你？

配电间的类比会让你以为**两个身份之间会互相协调**——比如"复位之前先自动把时钟开上"。

**不会。** 两个框架完全不知道对方存在。匹配的判据都只是
"DTS 里那个 `struct device_node *` 指针相等"，
clk 查 `of_clk_provider` 链表，reset 查 `reset_controller_list`
（`drivers/reset/core.c:847-852`），两条链表毫无关联
（图在 `06-devicetree.md` §7.1）。

于是"时钟和复位的先后顺序"这件事，**责任 100% 在消费者驱动**。
典型的正确顺序是：

```
   clk_prepare_enable()  →  reset_control_deassert()  →  访问寄存器
```

反过来（先解除复位再开时钟）在很多 IP 上会读到全 0 或直接挂死。
框架不会提醒你，因为它压根不知道有时钟这回事。

还有一个副作用：**`drivers/reset/` 的目录大小严重低估了这个子系统的真实规模。**
`drivers/reset/` 只有 39 个 `.c`（8132 行），但真正的复位控制器实现散落在
`drivers/clk/`、`drivers/soc/`、`drivers/pinctrl/` 等目录里。
所以"想看看某个平台的复位控制器怎么写的"，第一步不是 `ls drivers/reset/`，
而是 `git grep -l reset_controller_register`。

---

## §7 为什么核心完全不管 DTS 里那几个数字是什么意思？

### 先举个例子

**快递驿站的取件码。** 驿站的系统只做一件事：
把你手机上那串码交给货架管理员，管理员按自己的编号规则去找货。

驿站总部**故意不规定**这串码的格式：
A 店用"3 排 5 号"，B 店用一个 6 位流水号，C 店干脆按姓名首字母分格。
总部只规定一件事：**这串码有几段**。段数对不上就退回，段里的内容一律不看。

reset 核心就是这个总部。它只数 cell 的个数，内容原样交给控制器。

### 回到代码

**总部只数个数**：

```c
	if (WARN_ON(args.args_count != rcdev->of_reset_n_cells)) {
```

（`drivers/reset/core.c:859`，完整片段在 `06-devicetree.md` §2.2）

**原样转交**：整个 `struct of_phandle_args` 交给控制器的 `of_xlate`
（调用点 `drivers/reset/core.c:864`）。返回值是一个 `unsigned int id`
——核心从此只认这个 id，再也不关心它是怎么算出来的。

**总部提供了一个默认的最简规则**，给"一段码就是货架号"的店用：

```c
static int of_reset_simple_xlate(struct reset_controller_dev *rcdev,
			  const struct of_phandle_args *reset_spec)
{
	if (reset_spec->args[0] >= rcdev->nr_resets)
		return -EINVAL;

	return reset_spec->args[0];
}
```

（`drivers/reset/core.c:87-93`）注意它是 `static` 的——**控制器驱动无法显式指定它**，
只能靠核心在注册时自动补上（`drivers/reset/core.c:102-105`）。

这个设计的收益，看一眼 cell 语义的多样性就明白了：

| cell 数 | 语义 | 谁定的 |
|---|---|---|
| 0 | phandle 本身就是那条线（Apple M1 每个电源域一个节点） | `arch/arm64/boot/dts/apple/t8103-pmgr.dtsi` |
| 1 | 一个宏 = 一条线，最常见（212 处） | 各家 `include/dt-bindings/reset/*.h` |
| 2 | 寄存器偏移 + 位号 | `drivers/reset/hisilicon/reset-hi3660.c` |
| 7 | assert/deassert/status 各一对偏移+位号 + flags | `drivers/reset/reset-ti-syscon.c:183` |

**如果核心试图统一这四种，就得定义一套能表达全部形态的通用编码**
——那会比现在这一行 `args_count` 比较复杂几个数量级，而且每出一款新 SoC 就得改核心。
「把语义下沉给驱动」换来的是**核心 1289 行、二十年不用改**。

### 这个类比什么时候会误导你？

驿站的类比给人一个错觉：**总部至少会检查这串码是不是有效的。**

不会。核心只比对**段数**，段里的数字**一个都不校验**。后果：

- hi3660 那种 `<offset bit>` 绑定，offset 写成 `0x9999` 会被**原样写进 regmap**，
  唯一的兜底是 regmap 的 `max_register`（`06-devicetree.md` §4.2）。
- ti-syscon 的 cell 总数不是 7 的倍数时，多出来的几个数字被**整数除法静默丢弃**
  （`drivers/reset/reset-ti-syscon.c:183` 那行除法），
  症状是某条线永远没反应而日志全干净（`06-devicetree.md` §4.3）。

所以"cell 里的数字写错"这类问题，**框架层面从设计上就不可能替你发现**。
这不是缺陷，是"语义下沉"这个选择必然要付的代价——
而它的对偶面就是 §8 要讲的：**框架简单到没什么可观测的**。

---

## §8 为什么 clk 有一大棵 debugfs 树，reset 一个观测点都没有？

### 先举个例子

比较两样家用设备。

**中央空调**：你会想知道每个房间现在设定几度、实际几度、
压缩机在几档、总功率多少——因为**它一直在工作，而且各房间之间互相影响**
（总功率有上限，一个房间调低会挤占别的房间）。所以它必须有个控制面板。

**门口的墙壁开关**：拨上去灯亮，拨下去灯灭。
你**不需要**一块面板来告诉你"这个开关现在处于什么状态"——
看一眼开关本身就行了，而且它和别的开关互不影响。

clk 是中央空调：频率要沿着树往上聚合、父时钟的开关状态取决于所有孩子。
reset 是墙壁开关：**每条线独立，没有拓扑，没有聚合，没有需要推导的量。**

### 回到代码

三个零，三条空输出的命令（`07-debug.md` §3.1）：

```sh
grep -rn 'debugfs\|sysfs\|DEVICE_ATTR' drivers/reset/     # 无输出
grep -rn 'TRACE_EVENT\|trace_' drivers/reset/             # 无输出
ls /sys/kernel/debug/reset*                               # 不存在
```

为什么"没有需要推导的量"：

| clk 需要 debugfs 回答的问题 | reset 有没有对应问题 |
|---|---|
| 这个时钟的频率是从哪个父节点分频来的？ | ✗ 没有父子关系 |
| 我请求 100MHz，为什么实际是 96MHz？ | ✗ 没有"请求 vs 实际" |
| 谁把这个时钟 enable 住了导致我关不掉？ | △ 有类似问题（shared 计数），但**没人做这个接口** |
| 系统启动完还有哪些时钟没人用？（`clk_ignore_unused`） | ✗ 没有"未使用就关掉"的策略 |

注意第三行是个 △ 而不是 ✗：**"谁把 `deassert_count` 撑住了"确实是一个真实的、
会咬人的问题**（`07-debug.md` §6 第 6 行），而框架**确实没有**任何接口回答它。
数据其实是现成的——每个 rcdev 挂着 `reset_control_head` 链表，
每个句柄身上带着三个计数（`02-datastructures.md` §1.1）——**遍历一遍就能打印，
只是没人写这段代码**。

所以准确的说法是：**reset 的可观测性缺失，一半来自"确实没什么可看的"，
一半来自"有的东西也没人做"。**

### 这个类比什么时候会误导你？

墙壁开关的类比在一个地方彻底失效：**开关的状态是看得见的，
而复位线的状态在很多平台上连内核自己都读不到。**

`reset_control_status()` 看起来能回答"这条线现在什么状态"，但：

- 控制器没实现 `.status` → `-ENOTSUPP`，SCMI / bpmp / rpi 都是这种
  （`07-debug.md` §5.2）；
- 返回 `0` **同时代表**"句柄是 NULL"和"线未被断言"两件事
  （`drivers/reset/core.c:605-617`）。

⇒ 于是有了这个反直觉的排序：**在 reset 子系统里，最可靠的观测手段是 `devmem`
直接读寄存器**，比框架自己的 API 更可信。
一个框架的 status API 不如 `devmem` 可靠，听起来像缺陷，
但它和 §7 是同一件事的两面——**核心不理解 cell 语义，自然也无法在硬件之上
维护任何状态镜像**。简单是有代价的，代价就记在这里。

---

## §9 接下来看哪一篇

| 你接着想做的事 | 去处 |
|---|---|
| 从头建立全貌 / 隔几个月回来复习 | `00-overview.md` |
| 查某个函数叫什么、返回什么 | `01-api.md` |
| 看结构体字段和三个计数器的定义 | `02-datastructures.md` |
| 逐行读 get / assert / deassert 的实现 | `03-internals.md` |
| 看各控制器家族（MMIO / syscon / 固件）的差异 | `04-extensions.md` |
| 抄一份能用的驱动骨架 | `05-usage.md` |
| DTS 怎么写、配错了什么症状 | `06-devicetree.md` |
| 出问题了怎么查 | `07-debug.md` |
| 这些结论各自的证据等级 | `99-evidence.md` |
