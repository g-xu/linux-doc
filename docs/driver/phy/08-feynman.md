---
title: 费曼学习法
---

# phy 08：大白话解释

**本篇的目标不是完整，是"讲透最容易想错的那几处"。**
每个主题四段式：**问题 → 先举个例子 → 回到代码 → 这个类比什么时候会误导你。**
**类比后面一定跟源码行号** ——只讲故事不给依据的段落在本套文档里不算通过。

**八个主题**，**其中 §1 是本子系统最容易被类比错的那组关系。**

---

## §1 内核里为什么有两个叫 "phy" 的子系统？

### 先举个例子

一家公司里有两个都叫"张工"的人。
**一个在基建部，负责"把楼和外面的市政管网接起来"**；
**另一个在通信部，负责"跟运营商谈网线的带宽和协商"。**

**两个人不在同一个部门、不做同一件事、互不认识。**
**但你在走廊里喊一声"张工"，两个人都会回头。**

**内核里的情况一模一样**：
**"PHY" 是 physical layer 的缩写，凡是"把逻辑信号变成线上的电信号"的东西都能叫 PHY。**
**于是两个完全独立的子系统撞了同一个名字。**

| | **通用 PHY 框架**（本文档，"基建部张工"） | **PHYLIB**（"通信部张工"） |
|---|---|---|
| 管什么 | **USB / PCIe / SATA / UFS / MIPI / DP 的物理层初始化和上电** | **以太网 PHY 芯片的链路协商** |
| 你要它做的事 | **"把这条链路配好、供上电"** | **"跟对面协商到 1000Mbps 全双工"** |

### 回到代码

**两者的分界线在头文件路径上，只差一层目录**：

| | 通用 PHY | PHYLIB |
|---|---|---|
| 头文件 | **`include/linux/phy/phy.h`**（带 `phy/`） | **`include/linux/phy.h`**（不带） |
| 实例结构 | **`struct phy`**（8 字段，`include/linux/phy/phy.h:152-161`） | `struct phy_device` |
| 驱动侧 | **`struct phy_ops`**（12 字段，`:84-127`） | `struct phy_driver` |
| 有 `bus_type` 吗 | **没有**（§8） | **有**（`mdio_bus_type`） |

**官方 DT 绑定文档自己就在提醒这件事**
（`Documentation/devicetree/bindings/phy/phy-bindings.txt` 里 `phys` 属性的说明，原文）：
> *"phys : the phandle for the PHY device (used by the PHY subsystem; not to be confused with the
> Ethernet specific 'phy' and 'phy-handle' properties …)"*

⇒ **DT 属性也是三套**：**本框架用 `phys` / `#phy-cells` / `phy-names`；
以太网用 `phy` / `phy-handle` / `phy-mode`**（06 §8）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"两个人互不认识"这句话有一个真实的例外**：

**`enum phy_mode` 里有 `PHY_MODE_ETHERNET`**（`include/linux/phy/phy.h:41`）。
⇒ **一条通用 PHY 确实可以"当以太网链路用"**（SerDes 就是这种）——
**但那说的是"这条物理链路跑的是以太网信号"，不是"这是一个以太网 PHY 芯片"。**

**树里的真实例子（两个都核对过）**：
**`drivers/phy/microchip/sparx5_serdes.c:2167` 和
`drivers/phy/freescale/phy-fsl-lynx-28g.c:388`（还有 `:436`）
里都有 `if (mode != PHY_MODE_ETHERNET)` 这样的判断** ——
**它们各有 1 个 `struct phy_ops` 实例，是不折不扣的通用 PHY 驱动，但服务的是网口。**
⇒ **一块板子上"以太网"可能同时牵动两个 phy 子系统**：
**通用 PHY 管 SerDes 那一段，PHYLIB 管外接的以太网 PHY 芯片。**

⚠️ **第二个误导**：**`phy-supply` 这个 DT 属性两边都在用。**
**树里有 8 个不同的消费者在 `regulator_get*(dev, "phy")`，其中 5 个是网络驱动**（06 §4.2）。
⇒ **看到 `phy-supply` 不能推断这是通用 PHY 节点** ——**判据是有没有 `#phy-cells`。**

---

## §2 为什么 `phy_init` 和 `phy_power_on` 要分两步？

### 先举个例子

想象一台老式收音机。**让它出声要做两件事**：

**一、调好频率、接好天线、把波段开关拨到 FM。**（配置）
**二、按下电源开关。**（供电）

**这两件事有两个明显不同的性质**：
- **配置做一次就够了，断电再来电还在。**
- **供电可以反复开关，而且开关电源不该丢掉配置。**

⇒ **所以电源键和调频旋钮是两个独立的东西，各有各的状态。**

**PHY 一样**：**`init` 是"把 PLL 配好、时钟接好、寄存器初始化"；
`power_on` 是"给模拟前端供电，让链路真的开始跑"。**
**系统 suspend 时通常只 `power_off` 不 `exit`** ——**省电但保留配置，resume 快**（05 §3.3）。

### 回到代码

**两个计数完全独立**（`include/linux/phy/phy.h:157-158`）：

```c
	int			init_count;
	int			power_count;
```

**各自的回调只在 0↔1 的边界上被调**：

| API | 判断条件 | 位置 |
|---|---|---|
| `phy_init` | **`if (phy->init_count == 0 && phy->ops->init)`** | `drivers/phy/phy-core.c:261` |
| `phy_exit` | **`if (phy->init_count == 1 && phy->ops->exit)`** | `:298` |
| `phy_power_on` | **`if (phy->power_count == 0 && phy->ops->power_on)`** | `:342` |
| `phy_power_off` | **`if (phy->power_count == 1 && phy->ops->power_off)`** | `:380` |

⇒ **两个消费者共享一条 PHY 时，第二个调 `phy_power_on` 什么硬件动作都没有** ——
**只是把计数加到 2。**

**框架还会检查两者的相对关系**：
```c
/* drivers/phy/phy-core.c:258-259 */
	if (phy->power_count > phy->init_count)
		dev_warn(&phy->dev, "phy_power_on was called before phy_init\n");
```

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"电源键可以随便按"在代码里不成立** ——**计数没有下溢保护。**

**收音机上多按一次电源键，最多是"本来就是关的，还是关的"。**
⚠️ **`phy_power_off()` 多调一次，会发生三件事**（`drivers/phy/phy-core.c:380-393`）：

| 动作 | 位置 | 后果 |
|---|---|---|
| **`if (power_count == 1)` 不成立** | `:380` | **不调 `ops->power_off`（对） |
| ⚠️ **但 `--phy->power_count` 照样执行** | `:388` | **计数变成 -1** |
| ⚠️ **`phy_pm_runtime_put(phy)` 照样执行** | `:390` | **PM runtime 计数被打穿** |
| ⚠️ **`regulator_disable(phy->pwr)` 照样执行** | `:392-393` | **regulator 子系统会自己报 unbalanced** |

⇒ **警告会出现在 regulator 子系统，而根因在 phy 消费者**（07 §5.3）。
⚠️ **`phy_exit()` 同理**（`:305`）。

**所以这个类比要修正成**：**"电源键是一个计数器，按下和松开必须严格配对，
多松一次会把整条供电链的账搞坏。"**

---

## §3 为什么 `NULL` 是一个合法的 phy？

### 先举个例子

你写一份操作手册，其中一步是"打开侧面的散热风扇"。
**但这台设备有两个型号：高配带风扇，低配没有。**

**写法一（到处判断）**：
> *"如果你的机器有风扇，打开它；如果没有，跳过这一步。"* ——**每一步都要写一遍这句话。**

**写法二（发明一个"空风扇"）**：
> *"打开侧面的散热风扇。"* ——**低配机器上，"打开风扇"这个动作被定义为"什么都不做，算成功"。**

**内核选了写法二。** **官方文档原话**（`Documentation/driver-api/phy/phy.rst`）：
> *"It should be noted that NULL is a valid phy reference. All phy consumer calls on the NULL phy
> become NOPs. … The NULL phy is useful in devices for handling optional phy devices."*

⇒ **消费者驱动可以一路写 `phy_init()` / `phy_power_on()`，不用判 NULL**（05 §4.1）。

### 回到代码

**九个 API 确实是"NULL ⇒ 返回 0"**（01 §4.3 有完整清单）：

```c
/* drivers/phy/phy-core.c:249-250 —— phy_init 里 */
	if (!phy)
		return 0;
/* :289-290（exit）· :326-327（power_on，goto out 且 ret 初值 0）· :376-377（power_off）
   :403-404（set_mode_ext）· :421-422（set_media）· :436-437（set_speed）
   :451-452（reset）· :482-483（calibrate）—— 同一个形状 */
```

**"拿到 NULL"这件事本身也是设计好的**：
```c
/* drivers/phy/phy-core.c:785-793 */
struct phy *phy_optional_get(struct device *dev, const char *string)
{
	struct phy *phy = phy_get(dev, string);

	if (PTR_ERR(phy) == -ENODEV)
		phy = NULL;

	return phy;
}
```

**而"找不到 PHY"的四条不同原因全部被归一成 `-ENODEV`**（02 §3 / 05 §4.3）：
**`phys` 属性缺失（`:577-578`）· `usb-nop-xceiv` 特例（`:581-584`）·
节点 `disabled`（`:593-596`）· lookup 表没命中（`:128`）。**

### ⚠️ 这个类比什么时候会误导你？

⚠️⚠️ **两个反例 —— 这是本篇最重要的一条。**

**"所有动作在低配机器上都是空操作"这句话有例外**：

```c
/* drivers/phy/phy-core.c:508-509 —— phy_configure */
	if (!phy)
		return -EINVAL;
/* drivers/phy/phy-core.c:543-544 —— phy_validate，一模一样 */
```

⇒ **`phy_configure()` 和 `phy_validate()` 对 NULL 返回 `-EINVAL`，不是 0。**
⇒ **官方文档那句 "All phy consumer calls … become NOPs" 在本版本上不成立**（03 §C2 第 2 条）。
⇒ **用了 `devm_phy_optional_get()` 又调 `phy_configure()` 的驱动，在没有 PHY 的板子上 probe 会失败**
（05 §4.2）。

⚠️ **第二个误导更隐蔽**：**"归一成 `-ENODEV`"顺手把真实的配置错误也吞掉了。**

| 情形 | 你希望的 | 实际 |
|---|---|---|
| **DTS 故意没写 `phys`** | **静默跳过** ✔ | **静默跳过** ✔ |
| ⚠️ **DTS 想写但 `phy-names` 拼错了** | **报错** | ⚠️ **同样静默跳过** ✘ |
| ⚠️ **DTS 的 cell 个数写错** | **报错** | ⚠️ **同样静默跳过** ✘ |

⇒ **"可选 PHY"这个便利的代价是"DT 写错了看不出来"**（03 §C4 第 3 条 / 06 §5）。
**判据：必需的 PHY 一定要用非 `optional` 版**（05 §7.2）。

---

## §4 为什么驱动不实现 `set_mode`，`phy_set_mode()` 还返回成功？

### 先举个例子

你在餐厅点菜，说"这道菜请少放辣"。
**服务员回了一句"好的"。**

**有两种可能**：
**① 厨房收到了，真的少放了辣；②服务员只是礼貌地应了一声，厨房根本没有"辣度"这个选项。**
⚠️ **你从"好的"这句话里分辨不出是哪一种。**

**更糟的是**：**服务员还在你的点菜单上写下了"微辣"** ——
**下次你问"我这单是什么辣度"，单子上明明白白写着"微辣"。**

### 回到代码

```c
/* drivers/phy/phy-core.c:406-411 */
	mutex_lock(&phy->mutex);
	if (phy->ops->set_mode)
		ret = phy->ops->set_mode(phy, mode, submode);
	if (!ret)
		phy->attrs.mode = mode;		/* 就是"在单子上写微辣" */
	mutex_unlock(&phy->mutex);

	return ret;
```

**`ret` 初值是 0**（`:401`）⇒
**驱动没实现 `set_mode` ⇒ `if` 不进 ⇒ `ret` 保持 0 ⇒ `attrs.mode` 被写成新值 ⇒ 返回成功。**

**然后 `phy_get_mode()` 只读这个软字段**：
```c
/* include/linux/phy/phy.h:238-241（大意） */
static inline enum phy_mode phy_get_mode(struct phy *phy)
{
	return phy->attrs.mode;
}
```

⇒ **框架层面完全无法区分"切换成功"和"没人管这件事"**（07 §3.2）。

**`phy_set_media` / `phy_set_speed` / `phy_calibrate` / `phy_reset` 也是"ops 缺失返回 0"**，
**但它们不写任何软状态**（`drivers/phy/phy-core.c:421-422` / `:436-437` / `:482-483` / `:451-452`）
⇒ **`set_mode` 是唯一"既撒谎又留痕"的那个。**

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"服务员礼貌应声"暗示这是个疏忽，其实它是有意的设计。**

**理由**：**消费者驱动是通用的**（一个 dwc3 驱动要跑在几十种 SoC 上），
**而"这颗 PHY 需不需要显式切模式"是平台相关的。**
⇒ **如果"没实现就报错"，每个消费者都得写一堆 `if (ret != -EOPNOTSUPP)` 的特例。**

⚠️ **但代价是真实的**：**`attrs.mode` 被无条件写入这一点很难说是有意的**——
**它让 `phy_get_mode()` 变成了一个会骗人的接口。**

**驱动侧的正确做法**（05 §1.2）：
**实现 `set_mode` 并对不支持的模式 `return -EINVAL`**，
**而不是干脆不实现。**

---

## §5 为什么 `union phy_configure_opts` 没有"这是哪一种"的字段？

### 先举个例子

你收到一个没贴标签的信封，里面是一张表格。
**可能是报销单，可能是请假单，可能是采购单** ——**三种表格的格子位置完全不一样。**

**收件人怎么知道该按哪种格式读？**
**答案是：他不需要知道，因为寄件人和收件人事先约好了"我这个部门只发采购单"。**

⚠️ **约定失效的那一天，收件人会把"金额"格子里的内容当成"天数"读出来。**

### 回到代码

```c
/* include/linux/phy/phy.h:64-68 —— 三个成员，0 个标签 */
union phy_configure_opts {
	struct phy_configure_opts_mipi_dphy	mipi_dphy;
	struct phy_configure_opts_dp		dp;
	struct phy_configure_opts_lvds		lvds;
};
```

**三个成员大小差得很远**（04 §2.1）：
**`mipi_dphy` 25 个字段 · `dp` 8 个 · `lvds` 4 个。**

**框架的转发是完全透明的**：
```c
/* drivers/phy/phy-core.c:514-516 */
	mutex_lock(&phy->mutex);
	ret = phy->ops->configure(phy, opts);
	mutex_unlock(&phy->mutex);
```
⇒ **没有任何检查，也没有办法检查。**

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"寄件人和收件人事先约好"在这里不是靠代码保证的，是靠人。**

**约定的载体只有两个**：
**① 消费者知道自己在配什么类型的 PHY；② 驱动知道自己是什么类型的 PHY。**
⚠️ **两者之间没有任何编译期或运行期的校验** ——
**`union` 的类型系统恰恰是"允许把同一块内存当不同类型读"。**

⇒ **给 DP PHY 传 `opts.mipi_dphy` 会编译通过、运行不报错、驱动读到重叠的垃圾值。**

**唯一的缓解手段是 `phy_validate()`**（01 §6.2）：
**驱动的 `validate` 回调至少能看出"这些数值完全不合理"** ——
**但它同样不知道 union 现在是哪一种，只是"按自己的类型读，发现值离谱"。**

⚠️ **另一个类比边界**：**`include/linux/phy/pcie.h` 不是第四种表格。**
**它定义的是 PCIe 的模式常量，给 `phy_set_mode_ext()` 的 `submode` 参数用**（04 §2.3）。

---

## §6 为什么 PHY 的电源由框架管，而时钟由驱动管？

### 先举个例子

你租了一间办公室。**有些设施是物业统一管的，有些是你自己管的**：

**物业管的：楼里的总电闸。** 你搬进来时物业就知道"你这间要通电"，
你下班关灯上锁，物业该断的时候就断，**你不用去配电房。**
**你自己管的：空调、打印机、咖啡机。** **每间办公室要什么设备天差地别，物业管不了。**

**PHY 框架的分工一模一样**：
**`phy-supply` 那个 regulator 是"总电闸"，框架统一管；
时钟、复位、pinctrl、厂商私有寄存器是"你的设备"，驱动自己管。**

### 回到代码

**框架在 `phy_create()` 里主动去取那个 regulator**：
```c
/* drivers/phy/phy-core.c:978-985 */
	phy->pwr = regulator_get_optional(&phy->dev, "phy");
	if (IS_ERR(phy->pwr)) {
		ret = PTR_ERR(phy->pwr);
		if (ret == -EPROBE_DEFER)
			goto put_dev;

		phy->pwr = NULL;	/* 没有就当没有 */
	}
```

**然后在 `phy_power_on` / `phy_power_off` 里自动开关**：

| 动作 | 位置 | 时机 |
|---|---|---|
| `regulator_enable(phy->pwr)` | `drivers/phy/phy-core.c:330` | **在 PM runtime 和 `ops->power_on` 之前** |
| `regulator_disable(phy->pwr)` | `:393` | **在 `ops->power_off` 和 PM runtime 之后** |
| `regulator_put(phy->pwr)` | `:1226`（`phy_release()` 里） | **设备最终释放时** |

⇒ **PHY 驱动的 `phy_ops` 里不该出现任何 regulator 代码**（05 §1.1 第 5 点）。
**而时钟必须驱动自己 `clk_prepare_enable`**（05 §1.1 的 `foo_phy_init`）。

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"物业统一管电闸"暗示只有一套电源，其实有三层**（02 §1.0.E）：

| 层 | 谁管 | `phy_power_on` 里的顺序 |
|---|---|---|
| **① regulator（`phy-supply`）** | **框架** | **第一** |
| **② PM runtime** | **框架**（但要 provider 先启用，03 §A2.1） | **第二** |
| **③ PHY 自己的电源域寄存器** | **驱动的 `ops->power_on`** | **第三** |

⚠️ **第二个误导**：**"物业知道你这间要通电"在代码里有个前提。**
**PM runtime 只在 provider 自己启用了 PM runtime 时才对 phy 启用**：
```c
/* drivers/phy/phy-core.c:991-994 */
	if (pm_runtime_enabled(dev)) {
		pm_runtime_enable(&phy->dev);
		pm_runtime_no_callbacks(&phy->dev);
	}
```
⇒ **官方文档说的是无条件的，代码是有条件的**（03 §C2 第 4 条）。

⚠️ **第三个误导**：**`phy-supply` 这个属性名不是本框架独占的。**
**树里 8 个消费者，5 个是网络驱动**（06 §4.2）⇒ **看到它不能假定是"物业"在管。**

---

## §7 为什么"提供者"和"PHY 实例"要分成两个东西？

### 先举个例子

一栋楼里有一个配电间，里面有 8 路独立的电闸，分别供 8 个房间。

**住户来报修时说的是"我 3 号房断电了"。**
**物业不能只知道"配电间在哪"，还得知道"3 号"对应哪一路电闸。**
**而"3 号房"到"第几路电闸"的映射，只有装这个配电间的人知道** ——
**可能 1 号房对应第 5 路，也可能编号完全一致。**

⇒ **所以需要两样东西：配电间（提供者）+ 一张翻译表（把住户的编号变成电闸编号）。**

### 回到代码

| | `struct phy_provider` | `struct phy` |
|---|---|---|
| 位置 | `include/linux/phy/phy.h:171-178` | `include/linux/phy/phy.h:152-161` |
| 是"配电间"还是"一路电闸" | **配电间** | **一路电闸** |
| 数量关系 | **1** | **N** |
| **翻译表** | **`of_xlate` 回调**（`:176-177`） | — |

**DT 里的写法就是"住户报的编号"**：
```dts
	phys = <&fooctrl 2>;	/* 那个 2 就是"3 号房"这个编号 */
```
**框架把它原样交给 `of_xlate`**：
```c
/* drivers/phy/phy-core.c:599 */
	phy = phy_provider->of_xlate(phy_provider->dev, &args);
```

**单实例的情况太常见，所以框架自带了一个"编号必然是 0"的翻译表**：
```c
/* drivers/phy/phy-core.c:713-721 —— of_phy_simple_xlate 的核心 */
	class_dev_iter_init(&iter, phy_class, NULL, NULL);
	while ((dev = class_dev_iter_next(&iter))) {
		phy = to_phy(dev);
		if (args->np != phy->dev.of_node)
			continue;
		…
		return phy;
	}
```

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"自带的翻译表按编号查"是错的** ——**`of_phy_simple_xlate()` 根本不看编号。**

**它比的是 `args->np != phy->dev.of_node`**（`:716`）——**也就是"DT 节点是不是同一个"。**
⇒ **所以它能服务的不只是"单实例 provider"，还包括
"每个实例一个 DT 子节点"这种布局**（06 §2.2 / 05 §2.3）。
⇒ **真正需要自写 `of_xlate` 的，只有"多个实例共用同一个 `of_node`"的情况。**

⚠️ **kerneldoc 在这里也误导人**：
**`drivers/phy/phy-core.c:700` 写着 `@args: of_phandle_args (not used here)`，
但 `:716` 明明用了 `args->np`。**
**准确的说法是"不用 `args->args[]` 里的 cell 值"**（03 §A3.2 / 99 §1.3）。

⚠️ **第三个误导**：**"配电间"这个类比会让你以为 provider 是个设备。**
**它不是** ——**`struct phy_provider` 没有内嵌 `struct device`，只有一个 `struct device *dev` 指针**
（`include/linux/phy/phy.h:172`）⇒ **它不出现在 sysfs 里、不参与设备模型**（02 §1.0.B）。

---

## §8 为什么 PHY 是 "class" 而不是 "bus"？

### 先举个例子

公司里有两种组织方式：

**一、部门（bus）**：每个人属于唯一一个部门，
**部门有招聘流程**（新人进来 → HR 匹配岗位 → 分配给某个主管）。
**二、兴趣小组名册（class）**：**只是一份"公司里所有会弹吉他的人"的名单。**
**上名单不需要面试，名单也不给你派活。**

**`struct phy` 上的是名册，不是部门。**

### 回到代码

```c
/* drivers/phy/phy-core.c:1233 —— 建名册 */
	phy_class = class_create(THIS_MODULE, "phy");

/* drivers/phy/phy-core.c:967-969 —— 每个实例登记进名册 */
	phy->dev.class = phy_class;
	phy->dev.parent = dev;
	phy->dev.of_node = node ?: dev->of_node;
```

⚠️ **全文没有一处 `phy->dev.bus = ...`**（02 §1.0.C）
⇒ **没有 `bus_type` 就没有 `match` / `probe` 流程**
⇒ **phy 设备永远处于"没有驱动"的状态，永远不会被 probe。**

**它借用 `struct device` 的三件事，仅此而已**：
**① 引用计数（`get_device`/`put_device`，`:764` / `:659`）；
② sysfs 里的一个位置（`/sys/class/phy/`）；
③ PM runtime 的父子传导（`dev.parent`，`:968`）。**

### ⚠️ 这个类比什么时候会误导你？

⚠️ **"名册不给你派活"这句话有一处例外，而且很重要。**

**`of_phy_simple_xlate()` 会遍历整个名册来找实例**（`:713-721`，§7）
⇒ **这份名册不只是给人看的，它是框架内部的查找结构。**
⚠️ **它遍历的是"全系统所有 phy"，不是"这个 provider 的 phy"**
⇒ **匹配条件只有 `of_node` 相等** ⇒ **谁的 `of_node` 对上就返回谁。**

⚠️ **第二个误导**：**"上名册不需要面试"暗示没有代价，其实有一个全局资源。**
**每个实例都要从全局 IDA 拿一个 id**（`:957` 的 `ida_simple_get(&phy_ida, 0, 0, GFP_KERNEL)`）
⇒ **设备名 `phy-<provider名>.<id>`（`:973`）里的 id 是全系统递增的**，
**不是"这个 provider 的第几个"**（07 §2.1）。
⇒ **同一个 provider 的实例 id 不一定连续。**

⚠️ **第三个误导**：**"名册只是名单，删掉一行就完了"** ——
**实际上销毁要走完整的设备生命周期**：
**`phy_destroy()` 做 `pm_runtime_disable` + `device_unregister`（`:1048-1049`），
最后由 `phy_class->dev_release`（`:1240`）指向的 `phy_release()`（`:1220-1229`）
做 `regulator_put` + `ida_simple_remove` + `kfree`。**

---

## §9 阅读下一篇

| 想知道 | 去 |
|---|---|
| 复习整个子系统 | `00-overview.md` |
| API 完整清单 | `01-api.md` |
| 结构体与六组易错关系 | `02-datastructures.md` |
| 主流程 / 横切 / 设计债 | `03-internals.md` |
| 子框架与边界 | `04-extensions.md` |
| **代码骨架 + 七组近邻符号** | `05-usage.md` |
| **DT 静默失效决策树** | `06-devicetree.md` §5 |
| 调试速查 | `07-debug.md` |
| **哪些结论不能直接信** | `99-evidence.md` §5 |
