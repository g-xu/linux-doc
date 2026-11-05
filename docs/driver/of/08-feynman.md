---
title: 费曼学习法
---

# of 08：用大白话讲清最难理解的几组关系

> 版本：Linux 6.1.158 · 架构基准：arm64
>
> 本篇的写法：每个主题四段 —— **问题** / **先举个例子** / **回到代码** /
> **这个类比什么时候会误导你？**
> 最后那一段是重点：类比只是脚手架，知道它在哪里塌才是真正的理解。
>
> ⚠️ 所有 `文件:行号` 都写完整路径。

---

## §1 `device_node`、`platform_device`、`struct device` 是什么关系？

> **这是本子系统最容易被类比错的一组关系**，先讲它。

### 先举个例子

你要在一个工业园区里建一座厂房。整个过程有三样东西：

| # | 东西 | 谁做的 | 什么时候有 | 生命周期 |
|---|---|---|---|---|
| ① | **一张图纸**（"这块地上要建一座 3 层厂房，水电接口在东墙"） | **设计院**（园区外部） | 开工前就交给你了 | **整个项目期间都在档案室，几乎不销毁** |
| ② | **一个"在建项目"的立项档案** | **园区管委会** | 按图纸立项时 | 从立项到销号 |
| ③ | **管委会档案柜里的那个标准表格** | 管委会 | 立项时随 ② 一起 | 内嵌在 ② 里 |

**关键点**：
- ① 是**外部给的描述**，管委会只能读，不能改；
- **不是每张图纸都会立项** —— 只有"这块地在管委会管的片区里"才会（§5）；
- **② 里有一个字段指向 ①**（"本项目依据 XX 号图纸"），
  但 ① 上**没有**任何字段指回 ②。

### 回到代码

| # | 内核里 | 定义 | 谁造 |
|---|---|---|---|
| ① 图纸 | `struct device_node` | `include/linux/of.h:52` | **bootloader 给的 DTB**，`unflatten_device_tree()` 展开（`drivers/of/fdt.c:1327`） |
| ② 立项档案 | `struct platform_device` | —— | `of_platform_populate()`（`drivers/of/platform.c:465`） |
| ③ 标准表格 | `struct device`（内嵌在 ② 里） | —— | 同上 |

**唯一的连接是三行赋值**（`of_device_alloc()` 里）：

| 赋值 | 位置 |
|---|---|
| `dev->dev.of_node = of_node_get(np)` | `drivers/of/platform.c:144` |
| `dev->dev.fwnode = &np->fwnode` | `drivers/of/platform.c:145` |
| `dev->dev.parent = parent ? : &platform_bus` | `drivers/of/platform.c:146` |

**方向是单向的**：`struct device` → `device_node` 有指针
（`include/linux/device.h:619`），**反过来没有**。
⇒ 从节点找设备只能 `of_find_device_by_node()`（`drivers/of/platform.c:55`），
它**遍历 platform 总线**去找。

**`device_node` 只有 name / phandle / full_name / fwnode / 属性链 /
父子兄弟指针 / flags / data**（`include/linux/of.h:52-72`）——
⇒ **它连 `struct device` 的影子都没有**。

### 这个类比什么时候会误导你？

⚠️ **四处**：

1. **"图纸在档案室永远不销毁"—— 取决于配置**。
   `!CONFIG_OF_DYNAMIC` 时 `device_node` 确实永不释放（§6）；
   开了 overlay 就会（`include/linux/of.h:126-130` 那两个空实现是关键）。

2. ⚠️ **"立项档案的父项目就是图纸的父图纸"—— 不一定**。
   `dev->parent` 是**调用者传的**（`drivers/of/platform.c:146`）——
   `of_platform_device_create(np, id, NULL)` 传 NULL 时父设备是 `&platform_bus`（根），
   **和 DT 的树形无关**（02 §1.0.A.3）。

3. ⚠️ **"管委会读图纸时会检查图纸有没有错"—— 基本不检查**。
   `of_device_alloc()` 里只有一个 `WARN_ON(rc)`（`drivers/of/platform.c:140`）——
   `reg` 翻译失败也只是**警告一下继续走**。
   ⇒ 这就是"寄存器读出全 0"这类问题没有报错的原因（07 §6.2）。

4. ⚠️ **"图纸和档案一一对应"—— 一张图纸可能对应零个档案**。
   `status = "disabled"` 或父节点不在白名单 ⇒ **不会立项**（§5）。

---

## §2 phandle 不是指针

### 先举个例子

一本论文集里，文章之间要互相引用。

**做法 A（用页码）**：「见第 137 页」。
问题：排版一变，页码全错。

**做法 B（用编号）**：给每篇文章一个**永久编号**（"文献 [17]"），
文末附一张"编号 → 文章"的对照表。
⇒ 引用写 `[17]`，读者去查表。

**DTS 的 phandle 就是做法 B**：
你在源码里写 `<&clkctrl>`，**编译器（dtc）给 `clkctrl` 节点分配一个编号**
并把 `&clkctrl` 替换成那个编号。

### 回到代码

| 事实 | 依据 |
|---|---|
| `phandle` 是 `u32` | `include/linux/of.h:54` 的 `phandle phandle;` 字段（`typedef u32 phandle`） |
| 对比：真正的指针在同一个结构里 | `parent` / `child` / `sibling`（`include/linux/of.h:60-62`） |
| 解析要查表 | `of_find_node_by_phandle()`（`drivers/of/base.c:1274`） |
| 有一个哈希缓存 | `phandle_cache[]`（`drivers/of/base.c:141`）+ `of_phandle_cache_hash()`（`:143`） |
| 启动时批量填缓存 | `drivers/of/base.c:181-182`（在 `of_core_init()` 里） |
| 命中判断 | `drivers/of/base.c:1257-1259` |

**`dtc -I dtb -O dts` 能看到编号**（07 §5.2）：
```dts
	clocks = <0x1f 0x05>;      /* 0x1f 就是 clkctrl 的 phandle */
```
⇒ 拿 `0x1f` 去 grep `phandle = <0x1f>` 就能找到目标节点。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"引用只写编号"—— DTS 的引用还带参数**。
   `clocks = <&clkctrl 5>` 里那个 `5` 是"要第几个时钟"。
   ⇒ **参数个数由被引用的节点声明**（`#clock-cells`，§4 会讲为什么）。

2. ⚠️ **"对照表是文末附的一张"—— 内核的表是按需建的缓存**。
   `phandle_cache[]` 是**单槽直接映射**（`drivers/of/base.c:141`）——
   哈希冲突时后来的**覆盖**前面的（`drivers/of/base.c:1265`）。
   ⇒ 它是 best-effort，未命中就退化成遍历全树。

3. ⚠️ **"编号永久唯一"—— overlay 场景下要重新编号**。
   两个独立编译的 `.dtbo` 可能用同一个 phandle 值 ⇒
   打 overlay 时必须 `of_resolve_phandles()`（`drivers/of/resolver.c:340`）重定位。
   ⇒ 这就是 04 §1.4 第 ③ 步存在的理由。

---

## §3 `reg` 不是 CPU 物理地址

### 先举个例子

一个大型办公楼里的房间编号。

**外面的人**（CPU）用的是**全楼绝对地址**：
"XX 路 100 号 A 座 12 层 1203 室"。

**楼里的人**（DTS 节点）用的是**相对编号**：
- 在 12 层的平面图上，那个房间就叫 **"03 室"**；
- 12 层的平面图上有一行说明："本层 01–20 室对应全楼 1201–1220 室"。

**要把 "03 室" 翻译成全楼地址，就得逐层往上查那些说明**：
12 层的说明 → A 座的说明 → 整栋楼的说明。

### 回到代码

| 概念 | DTS 里 | 内核里 |
|---|---|---|
| 房间的相对编号 | **`reg`** | `struct resource` 之前的原始值 |
| 每层的对照说明 | **`ranges`** | `of_translate_one()`（`drivers/of/address.c:433`） |
| 逐层往上查 | —— | `__of_translate_address()` 的主循环（`drivers/of/address.c:549`） |
| 到楼顶（= 根节点） | 根节点没有 `ranges` | `drivers/of/address.c:558-562`（`of_read_number()`） |

**`ranges` 的三种形态就是三种"说明"**（06 §3.2）：

| 形态 | 类比 | `pr_debug` |
|---|---|---|
| `ranges;`（空） | "本层编号就是全楼编号" | `empty ranges; 1:1 translation`（`drivers/of/address.c:471`） |
| `ranges = <…>;` | 一张对照表（可以有多个窗口） | `walking ranges...`（`drivers/of/address.c:475`） |
| **没有 `ranges`** | **"本层的编号和全楼编号没有对应关系"** | `no ranges; cannot translate`（`drivers/of/address.c:464`） |

驱动看到的 `struct resource` **已经翻译好了** ——
`of_device_alloc()` 在填充时就循环调 `of_address_to_resource()`
（`drivers/of/platform.c:129-142`）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"没有对照说明就是错的"—— 不是**。
   I2C 从设备的 `reg = <0x48>` 是**总线地址**，不是内存地址 ——
   它的父节点（I2C 控制器）**本来就不该有 `ranges`**（06 §3.2）。
   ⇒ **"没有 `ranges`"的正确含义是"这层下面的地址不在 CPU 地址空间里"**。

2. ⚠️ **"平面图上的编号总是一个数"—— 它可能是两个 u32**。
   arm64 上地址是 64 位 ⇒ `#address-cells = <2>`（§4）。
   ⇒ 这就是"`reg` 里到底有几个数"这个问题的来源。

3. ⚠️ **"翻译只是加个偏移"—— 不同"总线类型"的翻译规则不同**。
   `of_busses[]`（`drivers/of/address.c:354`）有 PCI / ISA / default 三套 ——
   PCI 的 `ranges` 里第一个 cell 是**空间类型标志**，不是地址。
   ⇒ 这就是 `of_match_bus()`（`drivers/of/address.c:402`）存在的理由。

---

## §4 为什么 `#address-cells` 写在**父节点**上？

### 先举个例子

回到 §3 那栋楼。**"房间编号用几位数"这件事，是谁定的？**

不是房间自己定的 —— 是**那一层的平面图**定的：
- 12 层的图纸说"本层房间编号是两位数"（01–20 室）；
- 地下车库的图纸说"本层车位编号是四位数"（B001–B500）。

**因为要让"读平面图的人"能把一串数字切开** ——
切法必须由**画图的那一方**（父）声明，不能由每个房间各说各话。

### 回到代码

```dts
	parent {
		#address-cells = <2>;      /* 我的孩子的 reg，地址占 2 个 u32 */
		#size-cells = <1>;

		child { reg = <0x0 0x12340000 0x1000>; };
		/*             ▲── 地址 ──▲  ▲ 大小 */
	};
```

| 事实 | 依据 |
|---|---|
| 沿父链往上找 | `of_n_addr_cells()`（`drivers/of/base.c:108`）· `of_n_size_cells()`（`:129`） |
| 地址翻译每层重新取 | `drivers/of/address.c:537`（本层）· `:579`（父层） |
| 每层可以不同 | **06 §9.2 的实证**：`arch/arm64/boot/dts/ti/k3-am65.dtsi:96` 是 2，`:114` 是 1 |
| 不合法时报错 | `pr_err("Bad cell count for %pOF")`（`drivers/of/address.c:581`） |

**`ranges` 一项的长度也是这么算的**（06 §9.2 那张表）：
**子的 `#address-cells` + 父的 `#address-cells` + 子的 `#size-cells`**。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"画图的人一定会声明"—— 可能漏**。
   父节点少写这两个属性 ⇒ **地址解析失败**，而且
   `of_device_alloc()` 只 `WARN_ON`（`drivers/of/platform.c:140`）不报错（§1 误导点 3）。

2. ⚠️ **"位数写错了会一眼看出来"—— 不会**。
   该 2 写了 1 ⇒ **高 32 位被当成地址、低 32 位被当成大小** ⇒
   `ioremap` 一个错地址，**不报任何错**，表现是寄存器读出全 0（06 §10.4）。

3. ⚠️ **"`#size-cells` 和 `#address-cells` 总是一样"—— 不一定**。
   `#size-cells = <0>` 是合法的，表示"孩子的 `reg` 里没有大小" ——
   **I2C 和 SPI 的从设备节点就是这样**（`reg = <0x48>`，只有地址）。

---

## §5 为什么"变成设备"需要一张白名单？

### 先举个例子

回到 §1 的园区。管委会拿到一沓图纸，问题是：**哪些图纸要立项？**

规则是：**只有"这块地在管委会明确接管的片区里"才立项**。
片区清单只有四项：
"标准工业区"、"标准综合区"、"老工业区"、"特殊区 A"。

如果你的地在**别人的院子里**（比如某个厂子内部的空地），
管委会**不管** —— 那块地上要不要建东西，由**那个厂子自己决定**。

### 回到代码

**白名单只有四项**（`drivers/of/platform.c:24-32`）：

| compatible | 行号 |
|---|---|
| `"simple-bus"` | `drivers/of/platform.c:25` |
| `"simple-mfd"` | `drivers/of/platform.c:26` |
| `"isa"` | `drivers/of/platform.c:27` |
| `"arm,amba-bus"` | `drivers/of/platform.c:29` |

**三个条件**（`of_platform_device_create_pdata()`）：

| # | 条件 | 位置 |
|---|---|---|
| ① | `status` 可用 | `drivers/of/platform.c:175` |
| ② | 还没填充过 | `drivers/of/platform.c:176` |
| ③ | **父节点在白名单里**（`of_match_node(matches, bus)`） | `drivers/of/platform.c:343` 那个函数里的判断 |

**"那个厂子自己决定"就是 `devm_of_platform_populate()`**
（`drivers/of/platform.c:692`）—— 父节点的驱动自己调它（05 §2）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"白名单是为了省事"—— 它是为了正确性**。
   有三类子节点**不该**变成 `platform_device`（06 §7.3）：
   ① 配置数据（pinctrl 的 pin group）；
   ② **别的总线上的设备**（I2C/SPI 从设备 —— 它们由 I2C/SPI 核心枚举）；
   ③ graph 的拓扑描述。
   ⇒ **如果没有白名单，这三类都会被错误地变成 platform 设备。**

2. ⚠️ **"厂子自己决定"和"管委会决定"时机不同**。
   加 `"simple-bus"`（管委会）⇒ 子节点在**启动填充阶段**就建；
   用 `devm_of_platform_populate()`（厂子）⇒ **父节点 probe 之后**才建。
   ⇒ **子设备依赖父设备先初始化时必须用后者**（06 §7.2）。

3. ⚠️ **"根节点的孩子也要看白名单"—— 不用**。
   `of_platform_populate()` 从 root 开始直接遍历
   （`drivers/of/platform.c:465`）⇒ 根节点的直接子节点总会被填充。

---

## §6 为什么 `of_node_get/put` "半真半假"？

### 先举个例子

图书馆的借书登记。

**情形 A（一次性的展览）**：所有书在开馆时摆好，闭馆前**一本都不会撤走**。
⇒ 这时"登记借阅"完全是形式主义 —— 反正书不会消失。
于是馆长决定：**这种馆干脆不做登记**（登记簿是个空壳）。

**情形 B（正常流通的图书馆）**：书会被借走、下架、报废。
⇒ **必须登记** —— 否则一本还在被人看的书可能被报废掉。

**内核的选择**：编译时决定用哪种模式。

### 回到代码

```c
#ifdef CONFIG_OF_DYNAMIC
extern struct device_node *of_node_get(struct device_node *node);
extern void of_node_put(struct device_node *node);
#else
static inline struct device_node *of_node_get(struct device_node *node)
{	return node;	}
static inline void of_node_put(struct device_node *node) { }
#endif
```

| 分支 | 位置 |
|---|---|
| 有实体 | `include/linux/of.h:121-123`（实现在 `drivers/of/dynamic.c:33` / `:46`） |
| **空实现** | `include/linux/of.h:124-131` |
| 引用计数的载体 | `np->kobj`（`include/linux/of.h:64`，`CONFIG_OF_KOBJ`） |
| release 回调 | `drivers/of/kobj.c:21` 的 `of_node_release()` |

**两条抓 bug 的日志**（`OF_UNITTEST` 时才容易触发）：

| 日志 | 位置 | 含义 |
|---|---|---|
| `ERROR: memory leak, expected refcount 1 instead of %d, of_node_get()/of_node_put() unbalanced …` | `drivers/of/dynamic.c:471` | **put 少了** |
| `ERROR: Bad of_node_put() on %pOF` | `drivers/of/dynamic.c:336` | **put 多了** |

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"不做登记的馆里可以随便不登记"—— 错**。
   同一份驱动代码可能被放到开了 overlay 的板子上 ⇒ 那里就漏了。
   ⇒ **`drivers/of/` 自己的代码坚持配平**
   （如 `__of_translate_address()` 的 `drivers/of/address.c:527` 和 `:599-601`）。

2. ⚠️ **"漏登记只是内存泄漏"—— 还有更糟的**。
   `!OF_DYNAMIC` 下确实无害；但**代码可读性受损** ——
   get/put 的配对标出了"这个引用管到哪里"，缺了就看不出生命周期。

3. ⚠️ **"循环宏会自动登记归还"—— `break` 出去就不会**。
   `for_each_child_of_node()`（`include/linux/of.h:1373`）靠
   "下一次 `of_get_next_child()` 会 put 上一个"来配平（01 §3.2）——
   ⇒ **`break` 跳过了那一次** ⇒ 用 `_scoped` 版本
   （`include/linux/of.h:1377`）才安全。

---

## §7 为什么设备树的绑定是"稳定 ABI"？

### 先举个例子

想象一种**插座标准**。

你家墙上的插座是十年前装的。今天你买了一台新电器 ——
**它必须能插进十年前的插座**，否则这个标准就没意义了。

反过来：**你今天换了新插座，十年前的旧电器也必须还能用**。

⇒ 这意味着**插座的形状一旦发布，就永远不能改** ——
只能"加新的针位"（而且旧电器不插那些针位时也要能工作）。

### 回到代码

**DTB 和内核是两个独立发布的东西**：

| | 谁发布 | 什么时候更新 |
|---|---|---|
| DTB | **bootloader / 固件 / 设备厂商** | 可能**永远不更新**（出厂就固化在 flash 里） |
| 内核 | 你 | 经常更新 |

⇒ **新内核必须能读懂老 DTB** ⇒ 三条硬约束（02 §1.0.E、05 §4.1）：

| # | 约束 |
|---|---|
| ① | **不能改已发布属性的语义** |
| ② | **新属性缺失时必须有合理的默认行为** |
| ③ | **不能要求老 DTB 里没有的属性** |

**代码层面唯一的体现**：属性不存在时返回 `-EINVAL`，
**驱动必须处理它**（01 §14.2、05 §1.3）：
```c
	ret = of_property_read_u32(np, "new-prop", &v);
	if (ret == -EINVAL)
		v = LEGACY_DEFAULT;	/* 老 DTB 走这条 */
```

⚠️ **这里最容易写成 `-ENOENT`**（"不存在"直觉上就该是 ENOENT），
但属性读取家族一次都不返回它 ⇒ 那个分支永远不进，老 DTB 上就变成"必需属性缺失"报错，
**向后兼容当场失效**。`-ENOENT` 是 phandle 列表家族的码（01 §14.2.1）。

**`compatible` 列表的多项写法就是"向前兼容"的机制**（06 §6.1）：
新 SoC 声明 `compatible = "new-soc-uart", "old-soc-uart";` ⇒
老驱动匹配到第二项也能工作（`of_device_is_compatible()` 在整个列表里找，
`drivers/of/base.c:558`）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"标准由标准委员会强制"—— 内核代码强制不了**。
   `drivers/of/` 里**没有一行代码**在检查"你有没有破坏兼容性" ——
   这全靠**社区评审 + 绑定文档**（06 §8）。
   ⇒ 这就是"没有绑定文档的属性等于不存在"这条规则的意义。

2. ⚠️ **"加针位总是安全的"—— 不是**。
   加一个属性时如果**驱动的默认行为变了**，老 DTB 的行为就变了。
   ⇒ 正确做法：新属性缺失时**保持原有行为**（约束 ②）。

3. ⚠️ **"内核只读不写"—— kexec 时会写**。
   `drivers/of/kexec.c`（472 行，04 §6）会**构造一份新 DTB** 给下一个内核。
   ⇒ 但它是**在原 DTB 基础上改 `/chosen`**，不改绑定语义。

---

## §8 为什么有两套"bus"抽象，名字还一样？

### 先举个例子

一个快递公司里有两个部门都用"线路"这个词：

| 部门 | "线路"指什么 | 决定什么 |
|---|---|---|
| **地址翻译部** | "这个地区的**地址格式**"（有的地区门牌是"路+号"，有的是"村+组"） | **怎么把地址解析成坐标** |
| **网点管理部** | "这里**要不要设网点**" | **要不要在这里派人** |

一个地方可以"地址格式是标准的"，但"不设网点"（因为没有业务量）。
⇒ **两个部门的"线路"清单完全不同，但都叫线路。**

### 回到代码

| 抽象 | 位置 | 决定什么 |
|---|---|---|
| `of_busses[]` | `drivers/of/address.c:354` | **`ranges` / `reg` 怎么解析**（PCI / ISA / default 三种格式） |
| `of_default_bus_match_table[]` | `drivers/of/platform.c:24-32` | **要不要往下填充子设备** |

| 谁用它 | 位置 |
|---|---|
| `of_busses[]` ← `of_match_bus()` | `drivers/of/address.c:402`（调用于 `:534` · `:578`） |
| `of_default_bus_match_table[]` ← `of_platform_default_populate()` | `drivers/of/platform.c:501` |

**两张表毫无关系**：
一个 `compatible = "myvendor,mybus"` 的节点 ——
在 `of_busses[]` 里被当成 "default bus"（地址按常规格式解析），
但**不在** `of_default_bus_match_table[]` 里（子节点不会被填充）。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"两个部门总会打招呼"—— 它们完全不知道对方**。
   ⇒ 所以会出现"地址能翻译但设备没建出来"（§5）
   或"设备建出来了但地址翻译失败"（§4 误导点 2）这两种独立的故障。

2. ⚠️ **"名字一样说明是同一个概念"—— 不是**。
   DT 规范里 `"simple-bus"` 的语义确实包含两层：
   "我的孩子的地址可以翻译" + "我的孩子是独立设备"。
   ⇒ 但内核**用两张独立的表实现它们** ⇒ 一个节点可以只满足其中一层。
   这就是 03 §C5 记录的那条命名混淆。

---

## §9 为什么这个子系统这么"沉默"？

### 先举个例子

一个图书馆的查询台。读者一天问几万次"有没有 XX 这本书"。

如果每次"没有"都广播一遍"读者 A 要的书没有！" ——
**广播会把整个图书馆吵翻**，而且**绝大多数"没有"都是正常的**
（读者只是随便问问）。

⇒ 所以查询台**只在真正异常时才出声**（比如"目录索引坏了"），
平时只回一句"没有"。

**但代价是**：一个读者说"我明明记得有这本书"，
你**没法从广播记录里查出他到底问的是什么**。

### 回到代码

| 类别 | 次数 | 说明 |
|---|---|---|
| `pr_debug` | **113** | **默认编译成空** |
| `pr_err` | 75 | 但**分布极不均衡** |
| `address.c` 的比例 | **18 个 `pr_debug` : 2 个 `pr_err`** | 07 §2.3 |
| `base.c` 的 `pr_debug` | **只有 2** | **查找/遍历/属性定位基本不可观测**（07 §2.2） |

**为什么合理**：
"属性不存在"、"地址不需要翻译"在正常情况下**大量发生**
（可选属性、I2C 从设备的 `reg`）——
⇒ 打 `pr_err` 会把 dmesg 刷满（03 §C4）。

**代价是三条**（07 §8.2）：

| 代价 | 绕法 |
|---|---|
| 地址翻译失败没有线索 | `echo 'file address.c +p' > …/dynamic_debug/control` |
| 属性名拼错没有线索 | `ls /sys/firmware/devicetree/base/<路径>/` |
| 早期解析的日志错过了 | 内核命令行加 `dyndbg="file fdt.c +p"` |

**唯一"说得清楚"的一组是 phandle 解析**（07 §6.3）——
`base.c` 7 条 `pr_err` 里有 **5 条**在那里
（`drivers/of/base.c:1348` · `:1363` · `:1380` · `:1384`）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"沉默是刻意的设计"—— 一半是，一半是历史**。
   `overlay.c` 有 **22 条 `pr_err`**（07 §2.3）——
   它是**后来加的代码**，作者选择了"说清楚"。
   ⇒ 老代码（`base.c` / `address.c`）沉默更多是那个年代的风格。

2. ⚠️ **"打开 `pr_debug` 就都能看到"—— 早期的看不到**。
   `fdt.c` 的 24 条 `pr_debug` 发生在**动态调试可用之前**（07 §2.6）——
   ⇒ 必须用**内核命令行** `dyndbg=`。

3. ⚠️ **"沉默的代价只是难调试"—— 还有一类更糟的**。
   `of_device_alloc()` 里 `reg` 翻译失败只 `WARN_ON`
   （`drivers/of/platform.c:140`）**继续往下走** ⇒
   **设备建出来了但 resource 是垃圾** ⇒ 驱动 `ioremap` 一个错地址。
   ⇒ 这是"沉默"变成"错误被吞掉"的一个真实例子。

---

## §10 阅读下一篇

| 你现在想知道什么 | 去哪 |
|---|---|
| 130 个 API 的签名和 14 种错误码 | 01 篇 |
| §1 §2 §6 提到的结构体字段和两把锁 | 02 篇 |
| §3 §4 §5 的完整机制剖析 | 03 篇（Part A 时间线 / Part B 横切 / Part C 评价） |
| §6 §8 提到的独立框架（overlay / reserved-mem / graph / sysfs） | 04 篇 |
| 我要动手写驱动 | 05 篇 |
| §3 §4 §5 §7 的 DTS 写法 | **06 篇** |
| §9 说的那些绕法怎么做 | 07 篇 |
| 本套文档的取证方法和未验证项 | 99 篇 |

### §10.1 九个主题里最该记住的四条

1. **`device_node` 是"图纸"、`platform_device` 是"立项档案"**（§1）。
   连接只有 `dev->of_node` 一个**单向**指针，反查要遍历总线。
2. **`reg` 是相对父总线的地址，要沿 `ranges` 逐层翻译**（§3）。
   "没有 `ranges`" 的正确含义是"这层下面不在 CPU 地址空间里"。
3. **`#address-cells` 写在父节点上，而且每层可以不同**（§4）。
   写错了**不报错**，表现是寄存器读出全 0。
4. **不是所有节点都会变成设备** —— 需要父节点在四项白名单里，
   或者父驱动自己调 `devm_of_platform_populate()`（§5）。
