---
title: 设备树
---

# 06 设备树：mailbox 的 DTS 怎么写，配错了怎么定位

> **本篇的组织轴：先把"核心真正会读的属性"数清楚（只有 3 条），
> 再一条一条展开"谁写、谁读、写错了什么症状"。**
>
> 读本篇前先记住一个总的形状：**mailbox 的 DTS 出错，绝大多数不报错。**
> 核心里跟 DT 有关的四个失败出口，三个是 `pr_debug`/`dev_dbg`
> （`drivers/mailbox/mailbox.c:347`、`:356`、`:377`），
> 只有一个是 `dev_err`（`:398`）。也就是说：**默认日志级别下你什么都看不到**，
> 消费者的 probe 只是安静地返回错误码。这决定了本篇 §8 的定位方法必须以
> 「主动打开 dynamic debug」开头，而不是「看看 dmesg 有没有报错」。
>
> 格式约定同前：`mailbox.c` 一律指 `drivers/mailbox/mailbox.c`。
> arm64-only；ACPI PCC（`drivers/mailbox/pcc.c`）已裁掉，
> 所以本篇**完全不涉及 PCCT 表**那一套非 DT 的通道描述方式。

---

## §0 按任务找章节

| 我现在要做的事 | 去哪节 |
|---|---|
| 写一个**新控制器**的 DT 节点 | §2（`#mbox-cells` 填几）+ §7 挑一个同类实例 |
| 写一个**新消费者**的 DT 节点 | §3（`mboxes`）+ §4（要不要写 `mbox-names`） |
| 决定用 `mbox_request_channel` 还是 `_byname` | §4.3 决策树 |
| 我的 cell 语义不是"通道下标" | §2.3（自定义 `of_xlate`）+ §2.4（`#mbox-cells = <0>`） |
| 对接 SCMI / SCPI / BPMP，要写 `shmem` | §5 |
| 消费者 probe 拿不到通道 | §8.2 + §9 错误消息表 |
| 通道拿到了但收不到对端响应 | §5.3（顺序有语义）+ §8.3 |
| 想用 `mailbox-test` 手工验证控制器 | §6 |
| 我在别人的 DTS 里看到一条属性，想知道谁读它 | §1.1 总表（表里没有的就是死字，见 §4.4） |

---

## §1 三方分工：一条属性到底由谁消费

### §1.0 先把三层分清

mailbox 的 DT 处理**没有**"子系统自己解析一遍 DTS"这一层。
真正读 DTS 的只有三方，而且职责边界非常干净：

```
①  通用 OF / 驱动模型层
    of_platform_populate → platform_device
    ├─ compatible / reg / interrupts / clocks / resets / power-domains
    └─ 与 mailbox 无关，控制器只是一个普通 platform 设备
                    │
                    ▼
②  mailbox 核心（mailbox.c，只读 3 条属性）
    ├─ mboxes        + #mbox-cells   ← mbox_request_channel   :353-359
    └─ mbox-names                    ← mbox_request_channel_byname :422/:428
                    │
                    ▼
③  控制器驱动的 of_xlate（16 个驱动自己实现）
    └─ 解释 mboxes 里那几个 cell 到底什么意思 → 挑出一个 mbox_chan
```

关键结论：**核心只负责"把 phandle + cells 解析出来"，
不负责"cells 是什么意思"**。cell 语义 100% 由控制器驱动决定
（`03-internals.md` §A3 有 `of_xlate` 的完整调用位置）。
所以看别人的 DTS 时，**`mboxes = <&x 1 2>` 里的 `1` 和 `2` 一定要去
那个控制器驱动的 `of_xlate` 里查，绑定文档只是二手资料**。

还有一层不在上面这张图里：**上层协议框架**（SCMI / SCPI / BPMP）
会在消费者节点上再读几条属性（`shmem`、`arm,smc-id`…），
那些属性 mailbox 核心一个字都不读 —— 见 §5。

### §1.1 核心真正会读的属性总表

**这张表是本篇的骨架，后面每节是其中一行的展开。**

| 属性 | 写在哪个节点 | 必选？ | 谁消费（定位到行） | 类型 | 写错了的症状 |
|---|---|---|---|---|---|
| **`#mbox-cells`** | **控制器节点** | 必选 | `mailbox.c:353-359`（作为 `of_parse_phandle_with_args` 的 cells 名） | u32 | 填错个数 ⇒ 整条 `mboxes` 解析错位，`_byname` 也一起废掉。§2.2 |
| **`mboxes`** | **消费者节点** | 必选 | `mailbox.c:353-359` | phandle + N cells 的列表 | phandle 指错 ⇒ `-EPROBE_DEFER` 或 `-ENOENT`；cells 填错 ⇒ 拿到别的通道。§3 |
| **`mbox-names`** | **消费者节点** | **可选** | **只有 `mbox_request_channel_byname` 读**：`mailbox.c:422`（判存在）+ `:428`（逐串遍历） | 字符串列表 | 消费者用 `_byname` 却没写 ⇒ `-EINVAL`；名字顺序和 `mboxes` 不一致 ⇒ 静默拿错通道。§4 |
| **`shmem`** | 消费者节点 | 看框架 | **mailbox 核心完全不读**。只有上层框架读，见 §5.2 的 5 个文件 | phandle 列表 | 缺了 ⇒ 上层框架 probe 失败（错误来自框架，不是 mailbox）。§5 |

一句话记住这张表：**`#mbox-cells` 在控制器上，另外三条全在消费者上；
只有前两条是 mailbox 核心的必选项。**

官方绑定文档 `Documentation/devicetree/bindings/mailbox/mailbox.txt`
（60 行，通用绑定）和这张表完全对应：`#mbox-cells` 在
「Mailbox Controller / Required property」下（`:8-10`），
`mboxes` 在「Mailbox Client / Required property」下（`:21-22`），
`mbox-names` 和 `shmem` 在 Optional 下（`:24-29`）。
**唯一冲突的一句是 `:9`「Must be at least 1」—— 现实里有 `<0>`，见 §2.4。**

### §1.2 一个最小的完整例子（控制器 + 消费者）

先看官方通用绑定给的两段（`mailbox.txt:12-16` 和 `:32-37`），
这是全树最短的一对：

```dts
	/* 控制器侧 —— mailbox.txt:13-16 */
	mailbox: mailbox {
		...
		#mbox-cells = <1>;
	};

	/* 消费者侧 —— mailbox.txt:33-37 */
	pwr_cntrl: power {
		...
		mbox-names = "pwr-ctrl", "rpc";
		mboxes = <&mailbox 0 &mailbox 1>;
	};
```

三点值得当场读出来：

1. **控制器节点里除了 `#mbox-cells` 没有任何 mailbox 专属属性** ——
   `reg`/`interrupts`/`clocks` 都是普通 platform 设备的东西。
2. `mboxes` 里 `<&mailbox 0 &mailbox 1>` 是**两个条目**
   （`#mbox-cells = <1>` 所以每个条目 1 个 cell）。写成
   `<&mailbox 0>, <&mailbox 1>` 完全等价，后者更常见、更不容易看错。
3. `mbox-names` 的两个名字**按位置**对应上面两个条目 ——
   这个"按位置"是 §4.2 所有坑的根源。

---

## §2 `#mbox-cells`（控制器节点，必选）

### §2.1 它唯一的作用

`mailbox_request_channel` 里那一次解析（`mailbox.c:353-359`）：

```c
	ret = of_parse_phandle_with_args(dev->of_node, "mboxes", "#mbox-cells",
					 index, &spec);
	if (ret) {
		dev_dbg(dev, "%s: can't parse \"mboxes\" property\n", __func__);
		mutex_unlock(&con_mutex);
		return ERR_PTR(ret);
	}
```

`#mbox-cells` 的值**只被通用 OF 代码用来"跳格子"**：它决定
`mboxes` 列表里每个条目占几个 u32。核心自己既不校验它的值，
也不关心 cell 内容 —— cell 内容原样装进
`struct of_phandle_args`（`include/linux/of.h:75-79`）交给 `of_xlate`。

⇒ **`#mbox-cells` 填错的破坏力被严重低估**：它不是"某个通道拿错了"，
而是**从填错的那个条目起，整条 `mboxes` 列表全部错位**。

### §2.2 填几？由控制器驱动的 `of_xlate` 决定

| `#mbox-cells` | 谁在用 | cell 语义 | 对应的 `of_xlate` |
|---|---|---|---|
| **`<1>`** | 绝大多数（arm64 DTS 里 **101 处**） | 通道下标 | 多数用核心默认的 `of_mbox_index_xlate`（`mailbox.c:467-477`） |
| **`<2>`** | arm64 DTS **41 处** | 两级寻址，如 Tegra HSP 的 `<类型, 实例>` | 必须自己实现 |
| **`<3>`** | arm64 DTS **4 处** | 三段式，如 hi6220 的 `<方向, ack, 中断号>` | 必须自己实现 |
| **`<0>`** | arm64 DTS **3 处** | 无 cell，"这个控制器只有一条通道" | 必须自己实现，见 §2.4 |

（分布数字的复现命令在 `99-evidence.md` §3。）

**判定方法（唯一可靠的一条）：去控制器驱动里找 `of_xlate` 的赋值，
读它函数体里访问了 `sp->args[]` 的哪几个下标，就填几。**
如果驱动没赋 `of_xlate`（13 个驱动如此，见 `03-internals.md` §A3），
那就走核心默认实现 —— 它只读 `sp->args[0]`（`mailbox.c:471`）⇒ **必须填 `<1>`**。

常见误区：以为"控制器有 4 条通道所以填 `<4>`"。
**`#mbox-cells` 和通道数没有任何关系**，它是"一个通道说明符要几个数字"。

### §2.3 `<3>` 的四个实例：cell 语义完全是私有的

arm64 DTS 里 `#mbox-cells = <3>` 只有四处：
`arch/arm64/boot/dts/broadcom/stingray/stingray-fs4.dtsi:43` 和 `:52`、
`arch/arm64/boot/dts/hisilicon/hi3660.dtsi:388`、
`arch/arm64/boot/dts/hisilicon/hi6220.dtsi:797`。

以 hi6220 为例，它的消费者写成
`mboxes = <&mailbox 1 0 11>`（`arch/arm64/boot/dts/hisilicon/hi6220.dtsi:302`）——
三个数字的意思要去 `drivers/mailbox/hi6220-mailbox.c` 的
`hi6220_mbox_xlate`（赋值在 `:313`）里读，它自己还带一层范围检查
（`:233-234` 同时检查三个下标是否越界）。

这就是本节的教训：**同一个 `#mbox-cells` 值在不同 SoC 上语义毫不相干。**
写 DTS 时不能横向抄别家的 cell，只能抄自家控制器绑定文档里的示例，
并且**回驱动源码验一遍**。

### §2.4 `#mbox-cells = <0>`：官方文档说不许，现实里有三处

`Documentation/devicetree/bindings/mailbox/mailbox.txt:9` 明确写
「Must be at least 1」，但 arm64 DTS 里有三处 `<0>`：

| 位置 | 控制器 | 为什么能成立 |
|---|---|---|
| `arch/arm64/boot/dts/apple/t8103.dtsi:391` | `drivers/mailbox/apple-mailbox.c` | `apple_mbox_of_xlate`（`:313-320`）**`args_count != 0` 才返回 `-EINVAL`** —— 也就是它只接受 0 个 cell，然后固定返回 `&mbox->chans[0]` |
| `arch/arm64/boot/dts/mediatek/mt8195.dtsi:817` 和 `:824` | `drivers/mailbox/mtk-adsp-mailbox.c` | `mtk_adsp_mbox_xlate`（`:55-59`）函数体只有一句 `return mbox->chans;`，**连 `sp` 都不看** |

⇒ **规则：`#mbox-cells = <0>` 只在"控制器驱动自己实现了一个无视 cell 的
`of_xlate`"时才合法**，而且这类控制器一律是"一个 platform 设备只有一条通道"
（`mtk-adsp-mailbox.c` 的 `num_chans` 是 1）。

反过来说这是一个**很硬的判据**：看到 `#mbox-cells = <0>`，
就知道这个控制器**不可能**用核心默认的 `of_xlate` ——
默认实现会读 `sp->args[0]`（`mailbox.c:471`），读一个不存在的 cell。

决策树：

```
控制器有几条通道？
├─ 只有 1 条，而且不打算将来扩
│    ├─ 想让 DTS 最干净 → #mbox-cells = <0> + 自己写 of_xlate（照抄 mtk-adsp-mailbox.c:55-59）
│    └─ 想留扩展余地   → #mbox-cells = <1> + 用核心默认（推荐，DTS 不用改就能加通道）
└─ 多条
     ├─ 通道就是一维下标 → #mbox-cells = <1> + 核心默认 of_mbox_index_xlate
     └─ 需要多维寻址     → #mbox-cells = <N> + 自己写 of_xlate
                            必须在 of_xlate 里自己查 args_count
                              （核心不查；16 个实现里只有 8 个查，见 03 §C4）
```

---

## §3 `mboxes`（消费者节点，必选）

### §3.1 语法与消费点

```dts
	mboxes = <&控制器phandle cell0 cell1 …>, <&控制器phandle cell0 …>, …;
```

消费点只有一处：`mailbox.c:353-359`（§2.1 已引），
`index` 参数就是 `mbox_request_channel(cl, index)` 的第二个参数。

所以 **`mboxes` 的条目顺序 = `mbox_request_channel` 的 index 编号**，
从 0 开始。这是整个 mailbox DT 里最需要盯住的一条对应关系。

同一个消费者的 `mboxes` **可以指向不同的控制器**。真实例子：
`Documentation/devicetree/bindings/mailbox/sti-mailbox.txt:49`
写的是 `mboxes = <&mailbox2 0 1>, <&mailbox0 2 1>;` ——
两个条目分属两个控制器实例。核心对此毫无察觉，它每次只解析一个条目。

### §3.2 三种失败与各自的错误码

`mbox_request_channel`（`mailbox.c:337-406`）从 DT 角度看有**四个**失败出口 ——
三个是 debug 级日志，**第三个一个字都不打**：

| 出口 | 行 | 日志（级别） | 返回 | 什么 DTS 错误会走到这 |
|---|---|---|---|---|
| 消费者节点没有 `of_node` | `:346-349` | `pr_debug "%s: No owner device node\n"` | `-ENODEV` | 不是 DTS 错 —— 消费者是非 DT 设备（如手工创建的 platform 设备） |
| 解析 `mboxes` 失败 | `:353-359` | `dev_dbg "%s: can't parse \"mboxes\" property\n"` | **原样返回 `of_parse_phandle_with_args` 的错误码** | 没写 `mboxes` / index 超出条目数 / cell 个数与 `#mbox-cells` 不符 / phandle 指向不存在的节点 |
| 没有匹配的控制器，**或 `of_xlate` 返回了错误** | `:371-374` | **零日志** | `-EPROBE_DEFER`（初值 `:361`）或 `of_xlate` 自己的错误码 | phandle 指向的节点不是 mailbox 控制器；cell 值越界或 `args_count` 不对。见 §3.3 |
| 找到了控制器但通道被占 | `:376-380` | `dev_dbg "%s: mailbox not free\n"` | `-EBUSY` | 两个消费者的 `mboxes` 指向了**同一条通道**，见 §3.4 |

第五个出口 `:397-401` 的 `dev_err "Unable to startup the chan (%d)\n"` 是**唯一的 err 级**，
但它已经和 DTS 无关（通道申请到了，是控制器 `startup` 回调失败）——
见 `03-internals.md` §A3 和 `05-usage.md` §2.5。

第二行那个"原样返回"是本节最重要的实用信息，它决定了你看到的错误码：

| 你看到的错误码 | 真正的原因 |
|---|---|
| `-EINVAL` | `mboxes` 属性根本不存在，或格式不是 phandle+cells |
| `-ENOENT` | index 超出了 `mboxes` 的条目数（**最常见**：只写了 1 个条目却申请 index 1） |
| `-EPROBE_DEFER` 相关（消费者自己转的） | phandle 指向的控制器还没注册 —— 注意**核心不产生 `-EPROBE_DEFER`**，见 §3.3 |

### §3.3 `-EPROBE_DEFER` 是一个"初值"，不是一个判断

这段是 mailbox DT 调试的核心知识，**必须逐字读**（`mailbox.c:361-374`）：

```c
	chan = ERR_PTR(-EPROBE_DEFER);                       /* :361  ← 初值 */
	list_for_each_entry(mbox, &mbox_cons, node)
		if (mbox->dev->of_node == spec.np) {         /* :363  比 of_node 指针 */
			chan = mbox->of_xlate(mbox, &spec);  /* :364 */
			if (!IS_ERR(chan))
				break;                       /* :366  成功才跳出 */
		}

	of_node_put(spec.np);                                /* :369 */

	if (IS_ERR(chan)) {
		mutex_unlock(&con_mutex);
		return chan;                                 /* :373  一个字都不打 */
	}
```

三条结论：

1. **`-EPROBE_DEFER` 是 `:361` 的初值**，只在
   「遍历完 `mbox_cons`（`mailbox.c:23`）一个 `of_node` 都没对上」时被原样返回。
   所以它的真实含义是 **"这个 phandle 指的节点还没注册成 mailbox 控制器"**，
   而不是"资源暂时忙"。
2. **如果 `of_node` 对上了但 `of_xlate` 返回错误，返回的是 `of_xlate` 的错误码**
   （典型是 `-EINVAL`，来自 cell 个数或范围检查），
   而 `:371-374` **没有任何日志** —— 这是全篇最"哑"的一个出口。
   ⇒ **消费者 probe 拿到 `-EINVAL` 时，八成是 cell 写错了，不是 `mboxes` 语法错。**
3. `:363` 比的是 **`of_node` 指针**，不是 compatible ——
   所以 phandle 指到一个"长得像 mailbox 但驱动没注册控制器"的节点，
   永远只会得到 `-EPROBE_DEFER`。

于是 `-EPROBE_DEFER` 无法区分两种情形：

| 情形 | 症状一样 | 怎么分辨 |
|---|---|---|
| 控制器驱动还没 probe（正常，稍后会成功） | `-EPROBE_DEFER` | 二者症状完全一致，只能靠「是否一直停在 deferred」区分 |
| phandle 指向了一个**永远不会注册 mailbox 控制器**的节点（DTS 写错） | `-EPROBE_DEFER` | **消费者永远停在 deferred 状态**，`/sys/kernel/debug/devices_deferred` 里一直有它 |

⇒ **定位手段：看 `/sys/kernel/debug/devices_deferred`**（需要 `CONFIG_DEBUG_FS`）。
这是 §8.2 的第一步，比读 dmesg 有用得多。

### §3.4 同一条通道被两个消费者指向：`-EBUSY`

核心的通道占用是**一对一硬绑定**：`mbox_request_channel:374-378` 检查
`chan->cl` 非空就返回 `-EBUSY`（`03-internals.md` §A3 有完整流程）。

⇒ DTS 层面的表现：**两个消费者节点的 `mboxes` 解析到同一个 `mbox_chan`**，
后 probe 的那个拿 `-EBUSY`，而且**日志是 debug 级**，默认看不到。

顺带纠正一个容易搞反的细节：`:376` 的条件是
`chan->cl || !try_module_get(...)` —— **`-EBUSY` 也可能是「控制器模块正在卸载」**
（`try_module_get` 失败），和 DTS 无关。两种原因共用一条日志，无法从消息区分。

这个坑在"cell 语义写错"时特别容易撞上：如果控制器驱动的 `of_xlate` 有 bug
（比如 `03-internals.md` §C 里记的 `rockchip-mailbox.c` 那类"永远返回 0 号通道"），
DTS 写得完全正确也会四个消费者抢一条通道 —— **三个拿 `-EBUSY`**。
所以看到大量 `-EBUSY` 时，先怀疑 `of_xlate` 而不是自己的 DTS。

---

## §4 `mbox-names`（消费者节点，可选）

### §4.1 它只有一个读者

```c
	if (!of_get_property(np, "mbox-names", NULL)) {                /* :422 */
		dev_err(cl->dev,
			"%s() requires an \"mbox-names\" property\n", __func__);
		return ERR_PTR(-EINVAL);
	}

	of_property_for_each_string(np, "mbox-names", prop, mbox_name) {  /* :428 */
		if (!strncmp(name, mbox_name, strlen(name)))              /* :429 */
			return mbox_request_channel(cl, index);           /* :430 */
		index++;                                                  /* :431 */
	}
```

三条硬事实：

1. **`mbox-names` 只被 `mbox_request_channel_byname`（`mailbox.c:409-437`）读。**
   消费者如果用 `mbox_request_channel(cl, index)`，
   写了 `mbox-names` 也**没有任何人看** —— 这正是 §4.4 那条死字的成因。
2. **它的作用只是"把名字翻译成下标"**（`:430` 又调回 `mbox_request_channel`）。
   ⇒ **`mbox-names` 和 `mboxes` 必须一一对应、同序。**
   核心**不校验两张表等长**：`mbox-names` 比 `mboxes` 长时，
   匹配到超出范围的名字会一路走到 `of_parse_phandle_with_args` 失败，
   最终得到 `-ENOENT`（§3.2 第二行），而日志只有一句 debug 级的 "can't parse"。
3. **一路上不查 `cl->dev` 是否为 NULL**：`:412` 直接
   `cl->dev->of_node`。`mbox_request_channel` 是查的（`:346`）——
   ⇒ **`_byname` 比 index 版更不设防**，`cl->dev` 忘填就是空指针解引用。
   这条已记进 `99-evidence.md` §6。

### §4.2 前缀匹配：本篇最大的静默失效陷阱

`:429` 用的是 **`strncmp(name, mbox_name, strlen(name))`**，
长度取的是**请求名**的长度。含义是：

> **请求名只要是 DTS 里某个字符串的前缀，就算命中。**

| 请求的 `name` | DTS 里的 `mbox-names` | 命中哪个 | 是不是你想要的 |
|---|---|---|---|
| `"tx"` | `"tx", "rx"` | index 0 | ✅ |
| `"tx"` | `"tx0", "tx1", "rx"` | **index 0（`"tx0"`）** | ⚠️ **可能不是** —— 但不报错 |
| `"tx"` | `"rx", "tx"` | index 1 | ✅（顺序无所谓，名字对就行） |
| `"rx"` | `"rx0", "rx1"` | index 0（`"rx0"`） | ⚠️ 同上 |
| `"tx0"` | `"tx", "rx"` | **没命中** | 走到 `:434` 返回 `-EINVAL`（有 `dev_err`，反而好查） |
| `"m"` | `"mbox-tx"` | index 0 | ⚠️ 极端情形，一个字母就能匹配上 |

**方向性很重要：短的请求名匹配长的 DTS 名 ⇒ 静默拿错通道；
长的请求名匹配短的 DTS 名 ⇒ 报错（`dev_err` + `-EINVAL`）。**
前者危险，后者安全。

本树上这个陷阱**没有活的受害者**（我逐个核过 `_byname` 的调用者名字与
对应 DTS，见 `99-evidence.md` §3），但它是**按构造成立**的：
`arch/arm64/boot/dts/freescale/imx8-ss-vpu.dtsi:46`/`:57`/`:68` 都写
`mbox-names = "tx0", "tx1", "rx";`，其消费者
`drivers/media/platform/amphion/vpu_mbox.c:48` 请求的名字由 `scnprintf` 现拼
（`:60` `"tx0"`、`:63` `"tx1"`、`:66` `"rx"`）—— 恰好是全名，所以安全。
**只要有人把请求名从 `"tx0"` 改成 `"tx"`，或者给这类节点加一个用 `"tx"` 的消费者，
就会静默拿到 `tx0`。**

防御写法（两条，任选）：

```
① DTS 侧：同一节点里不要让一个名字成为另一个名字的前缀。
   ✗  mbox-names = "tx", "tx0";       ← "tx" 会先命中 "tx" 自己，看似没事，
                                        但换个顺序 "tx0", "tx" 就出问题
   ✓  mbox-names = "tx0", "tx1";      ← 互不为前缀
   ✓  mbox-names = "tx", "rx";        ← 互不为前缀

② 驱动侧：请求名写全，不要图省事截短。
   ✓  mbox_request_channel_byname(cl, "tx0");
```

### §4.3 用 `_byname` 还是用 index？决策树

```
消费者要几条通道？
├─ 1 条
│    └─ 直接 mbox_request_channel(cl, 0)
│       DTS 不用写 mbox-names（写了也没人读，见 §4.4）
│       实例：drivers/clk/hisilicon/clk-hi6220-stub.c:225
└─ 多条
     ├─ 通道有明确的角色名（tx / rx / doorbell…）
     │    └─ 用 _byname，DTS 必须写 mbox-names
     │       名字务必与 §4.2 的防御写法一致
     │       实例：drivers/firmware/ti_sci.c:3392/:3398（"rx" / "tx"）
     │             drivers/tty/serial/tegra-tcu.c:191/:246（"tx" / "rx"）
     └─ 通道是同质的、按序号编号，或者上层协议本身规定了顺序
          └─ 用 index，DTS 不写 mbox-names
             但要在绑定文档里把顺序语义写清楚 —— 这是 §5.3 的话题
             实例：drivers/firmware/arm_scmi/mailbox.c:149（TX=0 / RX=1）
```

一条实用建议：**新驱动优先用 `_byname`**。理由不是"更快"（它内部还是转成 index），
而是**DTS 里多一层自解释**，而且顺序写反了会报错而不是静默走错通道。
唯一的代价是 §4.2 的前缀陷阱 —— 按上面的防御写法就能避开。

### §4.4 一条真实的死字：hi6220 的 `mbox-names`

```dts
	/* arch/arm64/boot/dts/hisilicon/hi6220.dtsi:297-303 */
	stub_clock: stub_clock {
		compatible = "hisilicon,hi6220-stub-clk";
		hisilicon,hi6220-clk-sram = <&sram>;
		#clock-cells = <1>;
		mbox-names = "mbox-tx";              /* :301  ← 没有任何人读 */
		mboxes = <&mailbox 1 0 11>;          /* :302 */
	};
```

它唯一的消费者是 `drivers/clk/hisilicon/clk-hi6220-stub.c`
（compatible 匹配在 `:256`），而那个驱动申请通道用的是
**`mbox_request_channel(&stub_clk->cl, 0)`（`:225`）—— 按下标**。
⇒ **`:301` 这条 `mbox-names` 在本树上是死字。**

定性要准确：它**不是错**（DT 允许写多余属性，也可能是给别的 OS / 未来的驱动看的），
但它符合本套文档 06 篇的判据 ——「找不到消费者的属性就是死字，必须显式标出来」。
记进 `99-evidence.md` §6。

这条同时给出一个通用的自查动作：
**看到消费者节点写了 `mbox-names`，去它的驱动里 grep 一次 `_byname`。
grep 不到，这条属性就是装饰。**

---

## §5 `mboxes` 的顺序语义与 `shmem`（上层框架的额外约定）

### §5.1 `shmem`：mailbox 核心一个字都不读

`Documentation/devicetree/bindings/mailbox/mailbox.txt:26-29` 定义了它：

```
- shmem : List of phandle pointing to the shared memory(SHM) area between the
	  users of these mailboxes for IPC, one for each mailbox. This shared
	  memory can be part of any memory reserved for the purpose of this
	  communication between the mailbox client and the remote.
```

**但它写在通用 mailbox 绑定里，是一个历史遗留的位置** ——
`/usr/bin/grep -n 'shmem' drivers/mailbox/*.c` 在整个 `drivers/mailbox/` 下**零命中**。
读它的全部是上层框架（§5.2）。

⇒ **调试含义：`shmem` 配错了，错误一定来自上层框架，不会来自 mailbox。**
不要在 `mailbox.c` 里找线索。

官方的 shmem 示例是 `mailbox.txt:39-60`，形状是
「一个 `mmio-sram` 节点 + 里面开子节点 + 消费者用 phandle 指过去」：

```dts
	/* mailbox.txt:41-53 */
	sram: sram@50000000 {
		compatible = "mmio-sram";
		reg = <0x50000000 0x10000>;
		#address-cells = <1>;
		#size-cells = <1>;
		ranges = <0 0x50000000 0x10000>;

		cl_shmem: shmem@0 {
			compatible = "client-shmem";
			reg = <0x0 0x200>;
		};
	};

	/* mailbox.txt:55-60 */
	client@2e000000 {
		...
		mboxes = <&mailbox 0>;
		shmem = <&cl_shmem>;
	};
```

`mailbox.txt:27` 那句 **「one for each mailbox」** 是关键约定：
**`shmem` 的条目要和 `mboxes` 的条目一一对应、同序**。
核心不查，上层框架也未必查 —— 见 §5.3。

### §5.2 谁真的读 `shmem`（五个文件，全部在上层）

| 读它的文件 | 行 | 用途 |
|---|---|---|
| `drivers/firmware/arm_scmi/mailbox.c` | `:75`、`:86`、`:87`、`:122` | SCMI 的 TX / RX 共享内存区，按 `mboxes` 的 index 取对应的 shmem |
| `drivers/firmware/arm_scmi/optee.c` | `:374`、`:406` | OP-TEE 传输后端（不走 mailbox，但复用同一条属性） |
| `drivers/firmware/arm_scmi/smc.c` | `:59`、`:120` | SMC 传输后端 —— **完全没有 mailbox**，只用 `shmem` |
| `drivers/firmware/arm_scpi.c` | `:948` | SCPI 的共享内存 |
| `drivers/firmware/tegra/bpmp-tegra186.c` | `:176` | BPMP 的 IVC 队列内存 |

`arm_scmi/smc.c` 那一行值得单独记住：**`shmem` 可以在完全没有 mailbox 的
情况下使用**（SCMI 的 SMC 传输后端只要共享内存 + 一条 SMC 调用）。
⇒ 所以看到一个节点有 `shmem` 但没有 `mboxes`，**不是 DTS 写漏了**。

arm64 DTS 里写 `shmem` 的文件共 9 个：
`arch/arm64/boot/dts/arm/juno-base.dtsi`、`arch/arm64/boot/dts/arm/juno-scmi.dtsi`、
`arch/arm64/boot/dts/amlogic/meson-gx.dtsi`、`arch/arm64/boot/dts/amlogic/meson-axg.dtsi`、
`arch/arm64/boot/dts/nvidia/tegra186.dtsi`、`arch/arm64/boot/dts/nvidia/tegra194.dtsi`、
`arch/arm64/boot/dts/nvidia/tegra234.dtsi`、`arch/arm64/boot/dts/freescale/imx8ulp.dtsi`、
`arch/arm64/boot/dts/rockchip/rk356x.dtsi`。

### §5.3 `mboxes` 的顺序有语义：SCMI 的 TX=0 / RX=1

这是 `04-extensions.md` §4.1 埋下的那条引用。SCMI 申请通道的方式是
**按下标**（`drivers/firmware/arm_scmi/mailbox.c:149`）：

```c
	smbox->chan = mbox_request_channel(cl, tx ? 0 : 1);
```

⇒ **SCMI 的 DTS 里 `mboxes` 第 0 个条目必须是 TX、第 1 个必须是 RX**，
即使节点里同时写了 `mbox-names` 也不影响 —— SCMI 从不调 `_byname`。

写反的后果：

| | 现象 |
|---|---|
| 报错吗 | **不报错**。两条通道都申请成功，`startup` 也成功 |
| dmesg 里有线索吗 | 没有。mailbox 侧一切正常 |
| 实际症状 | **发出去的请求进了 RX 通道，响应永远收不到** ⇒ SCMI 自己的超时（不是 `tx_tout`） |
| 怎么定位 | 只能对着控制器的绑定文档核对 `mboxes` 两个条目的方向。见 §8.3 |

真实的正确写法（`arch/arm64/boot/dts/arm/juno-scmi.dtsi:45-64`）：

```dts
		scmi {
			compatible = "arm,scmi";                        /* :46 */
			mbox-names = "tx", "rx";                        /* :47 */
			mboxes = <&mailbox 0 0 &mailbox 0 1>;           /* :48 */
			shmem = <&cpu_scp_lpri0 &cpu_scp_lpri1>;        /* :49 */
			...
			scmi_dvfs: protocol@13 {                        /* :58 */
				reg = <0x13>;
				#clock-cells = <1>;
				mbox-names = "tx", "rx";                /* :61 */
				mboxes = <&mailbox 1 0 &mailbox 1 1>;   /* :62 */
				shmem = <&cpu_scp_hpri0 &cpu_scp_hpri1>;/* :63 */
			};
		};
```

这段有三个值得学的点：

1. **`mbox-names = "tx", "rx"` 和 `shmem` 的两个条目顺序一致**，
   而且和 `mboxes` 的顺序一致 —— 三张表同序。虽然 SCMI 不读 `mbox-names`，
   写上它让 DTS 自解释，读者一眼能看出方向。
2. **子节点 `protocol@13` 把三条属性全部重新写了一遍**（`:61-63`）——
   SCMI 允许某个协议用独立的通道对（这里 `<&mailbox 1 …>` 是另一个 MHU 通道），
   属性**不继承**，必须整组重写。漏写一条就会退回用父节点的通道。
3. **`#mbox-cells` 是被这个 dtsi 改掉的**：`juno-base.dtsi:25-31` 里
   `mailbox: mhu@2b1f0000` 的 compatible 是 `"arm,mhu"`、`#mbox-cells = <1>`，
   而 `juno-scmi.dtsi:187-190` 用 `&mailbox { ... }` 整体覆盖成：

   ```dts
	&mailbox {
		compatible = "arm,mhu-doorbell", "arm,primecell";   /* :188 */
		#mbox-cells = <2>;                                  /* :189 */
	};
   ```

   ⇒ 同一块板子的两个 dtb（`juno.dtb` 和 `juno-scmi.dtb`）里，
   **同一个节点的 `#mbox-cells` 一个是 1 一个是 2**，因为绑的是两个不同的驱动
   （`drivers/mailbox/arm_mhu.c` 用默认 xlate；
   `drivers/mailbox/arm_mhu_db.c` 自己实现 `of_xlate`，两个 cell 是
   `<通道, 门铃位>`）。
   **教训：改 `compatible` 就必须同时改 `#mbox-cells`，这两条是一对。**

另两个"顺序有语义"的实例（同样不报错、只表现为收不到）：

| 消费者 | 顺序约定 | 出处 |
|---|---|---|
| Tegra BPMP | 只申请 index 0（一条门铃通道） | `drivers/firmware/tegra/bpmp-tegra186.c:227` |
| Apple RTKit | 先试 `_byname`，失败退回 index 0 | `drivers/soc/apple/rtkit.c:700-704`（全树唯一两种都用的消费者） |

---

## §6 `mailbox-test` 的节点怎么写

这是 `04-extensions.md` §1.5 和 `05-usage.md` §6.1 都指向本节的那段。

### §6.1 唯一的 in-tree 示例

`mailbox-test` **没有自己的绑定文档**，全树唯一的示例藏在 STI 的绑定里
（`Documentation/devicetree/bindings/mailbox/sti-mailbox.txt:44-51`）：

```dts
	/* sti-mailbox.txt:46-51 */
	mailbox_test {
		compatible	= "mailbox-test";
		reg		= <0x[shared_memory_address], [shared_memory_size]>;
		mboxes		= <&mailbox2 0 1>, <&mailbox0 2 1>;
		mbox-names	= "tx",	"rx";
	};
```

**arm64 DTS 里零实例**
（`/usr/bin/grep -rn 'mailbox-test' --include=*.dts --include=*.dtsi arch/` 零命中）——
它注定是"手工往自己的 DTS 里加一个节点、或者用 DT overlay"的用法。

### §6.2 四条属性各自的作用

| 属性 | 必须吗 | 谁读 | 作用 |
|---|---|---|---|
| `compatible = "mailbox-test"` | 必须 | `drivers/mailbox/mailbox-test.c:437` | 裸字符串，没有厂商前缀 |
| `mboxes` + `mbox-names` | **必须** | 核心的 `_byname` | 名字**硬编码为 `"tx"` 和 `"rx"`**（`mailbox-test.c:389-390`），**不能改** |
| `reg` | 可选 | `mailbox-test.c:368-387` | 共享内存区。有它才能用 `signal` 文件（`04-extensions.md` §1.3） |

**`"tx"` / `"rx"` 这两个名字是硬编码的**，加上 §4.2 的前缀匹配，
就产生一条实用规则：

> 如果被测控制器的通道在你自己的 DTS 里叫 `"tx0"`，
> `mailbox-test` 请求 `"tx"` 会**静默命中 `"tx0"`** ——
> 恰好是你想要的。但如果 `mbox-names` 的第一个是 `"txdb0"`（门铃），
> 请求 `"tx"` 也会命中它，测的就不是你以为的那条通道。
> ⇒ **给 `mailbox-test` 节点单独写一份 `mbox-names = "tx", "rx";`，不要复用别人的。**

### §6.3 最小可用节点（写给自己的板子）

```dts
	/* 未编译未运行；配合 CONFIG_MAILBOX_TEST=y|m
	 * （drivers/mailbox/Kconfig:167-173） */
	mailbox_test {
		compatible = "mailbox-test";
		mboxes = <&my_mbox 0>, <&my_mbox 1>;   /* cell 语义按 §2.2 查驱动 */
		mbox-names = "tx", "rx";               /* 名字不能改 */
	};
```

只有一条通道的控制器（`#mbox-cells = <0>` 那一类，§2.4）怎么测？
把两个条目都指向同一条通道**不行** —— 第二次申请会拿 `-EBUSY`（§3.4）。
`mailbox-test.c:392-393` 的逻辑是"两条都没拿到才 `-EPROBE_DEFER`"，
所以只写 `mboxes = <&my_mbox>;` + `mbox-names = "tx";`
能 probe 成功，只是没有 RX，读 `message` 会打印
`<NO RX CAPABILITY>`（`mailbox-test.c:186`）。

用法（三步）见 `05-usage.md` §6.1；能验证 / 不能验证什么见 `05-usage.md` §6.3。

---

## §7 arm64 真实节点集：按控制器类型挑一个抄

以下六个都取自 `arch/arm64/boot/dts/`，可以直接 diff。

### §7.1 `#mbox-cells = <1>`：最常见的形状（Qualcomm APCS）

```dts
	/* arch/arm64/boot/dts/qcom/msm8916.dtsi:1866-1873 */
	apcs: mailbox@b011000 {
		compatible = "qcom,msm8916-apcs-kpss-global", "syscon";
		reg = <0x0b011000 0x1000>;
		#mbox-cells = <1>;
		clocks = <&a53pll>, <&gcc GPLL0_VOTE>;
		clock-names = "pll", "aux";
		#clock-cells = <0>;
	};
```

控制器驱动 `drivers/mailbox/qcom-apcs-ipc-mailbox.c` **没有 `of_xlate`** ⇒
走核心默认，cell 就是通道下标。绑定文档是
`Documentation/devicetree/bindings/mailbox/qcom,apcs-kpss-global.yaml`。
注意这个节点**同时是 syscon 和时钟提供者** —— mailbox 控制器经常兼职。

### §7.2 `#mbox-cells = <2>`：两级寻址（Tegra HSP）

```dts
	/* 控制器 —— arch/arm64/boot/dts/nvidia/tegra186.dtsi:1184-1190 */
	hsp_top0: hsp@3c00000 {
		compatible = "nvidia,tegra186-hsp";
		reg = <0x03c00000 0xa0000>;
		interrupts = <GIC_SPI 176 IRQ_TYPE_LEVEL_HIGH>;
		interrupt-names = "doorbell";
		#mbox-cells = <2>;
	};

	/* 消费者 —— arch/arm64/boot/dts/nvidia/tegra194.dtsi:3106-3111 */
	tcu: tcu {
		compatible = "nvidia,tegra194-tcu";
		mboxes = <&hsp_top0 TEGRA_HSP_MBOX_TYPE_SM TEGRA_HSP_SM_RX(0)>,
			 <&hsp_aon  TEGRA_HSP_MBOX_TYPE_SM TEGRA_HSP_SM_TX(1)>;
		mbox-names = "rx", "tx";
	};
```

三个看点：

1. 两个 cell 是 `<类型, 实例>`，**用宏而不是裸数字** ——
   这是 `#mbox-cells >= 2` 的推荐写法，可读性靠宏名。
2. **两个条目指向两个不同的控制器**（`hsp_top0` 和 `hsp_aon`）——
   §3.1 说的"同一消费者可以跨控制器"的真实例子。
3. 消费者 `drivers/tty/serial/tegra-tcu.c:191`/`:246` 用的是 `_byname`
   （`"tx"` / `"rx"`），所以 `mbox-names` 是**活的**，
   而且 `mboxes` 的顺序（rx 在前）无所谓。
   对比 §5.3 的 SCMI —— 用 `_byname` 就摆脱了顺序依赖。

绑定文档：`Documentation/devicetree/bindings/mailbox/nvidia,tegra186-hsp.yaml`
（`"#mbox-cells": const: 2`）。

### §7.3 `#mbox-cells = <0>`：MediaTek ADSP

```dts
	/* 消费者 —— arch/arm64/boot/dts/mediatek/mt8195.dtsi 的 adsp 节点，
	 * 三条 mailbox 相关属性在 :810-811 */
	adsp: adsp@10680000 {
		compatible = "mediatek,mt8195-dsp";
		...
		mbox-names = "rx", "tx";                        /* :810 */
		mboxes = <&adsp_mailbox0>, <&adsp_mailbox1>;    /* :811  零 cell */
		status = "disabled";                            /* :812 */
	};

	/* 两个控制器 —— :815-827，各自只有一条通道 */
	adsp_mailbox0: mailbox@10816000 {
		compatible = "mediatek,mt8195-adsp-mbox";
		#mbox-cells = <0>;                              /* :817 */
		reg = <0 0x10816000 0 0x1000>;
		interrupts = <GIC_SPI 702 IRQ_TYPE_LEVEL_HIGH 0>;
	};
	adsp_mailbox1: mailbox@10817000 {                       /* :822-827，同款，只是地址+1页 */
		...
		#mbox-cells = <0>;                              /* :824 */
	};
```

这是 §2.4 的完整形态：**"一个控制器一条通道"的硬件，
用两个 platform 设备 + 两个 `#mbox-cells = <0>` 节点表达方向**，
而不是"一个控制器两条通道"。
消费者 `drivers/firmware/mtk-adsp-ipc.c:97` 用 `_byname`（名字表在同文件）。

### §7.4 消费者跨到别的子系统：TI K3 的 DMSC

```dts
	/* arch/arm64/boot/dts/ti/k3-am62-main.dtsi:148-155 */
	dmsc: system-controller@44043000 {
		compatible = "ti,k2g-sci";
		ti,host-id = <12>;
		mbox-names = "rx", "tx";
		mboxes = <&secure_proxy_main 12>,
			 <&secure_proxy_main 13>;
		reg-names = "debug_messages";
		reg = <0x00 0x44043000 0x00 0xfe0>;
	};
```

消费者是 `drivers/firmware/ti_sci.c`（`:3392` 请求 `"rx"`、`:3398` 请求 `"tx"`）——
**`mbox-names` 顺序和请求顺序都对得上，是最省心的写法**。
控制器 `drivers/mailbox/ti-msgmgr.c` 自己实现 `of_xlate`，cell 是"secure proxy 线程号"
（12/13 不是"第 12、13 条通道"，是硬件线程编号）——
又一次印证 §1.0 的结论：**cell 语义必须回驱动查**。

### §7.5 一张速查表

| 平台 | 控制器 compatible | `#mbox-cells` | 消费者怎么申请 | `mbox-names` 是活的吗 |
|---|---|---|---|---|
| Juno（`juno.dtb`） | `arm,mhu` | `<1>` | —（本树无消费者用它） | — |
| Juno（`juno-scmi.dtb`） | `arm,mhu-doorbell` | `<2>` | index（TX=0/RX=1） | ✗ 写了但 SCMI 不读（§5.3） |
| Tegra186/194 | `nvidia,tegra186-hsp` | `<2>` | `_byname` | ✅ |
| MSM8916 | `qcom,msm8916-apcs-kpss-global` | `<1>` | 上层是 SMP2P/SMSM，index | 多数节点不写 |
| MT8195 ADSP | `mediatek,mt8195-adsp-mbox` | `<0>` | `_byname` | ✅ |
| TI AM62 | `ti,am654-secure-proxy` | `<1>` | `_byname` | ✅ |
| hi6220 stub clk | `hisilicon,hi6220-mbox` | `<3>` | index 0 | ✗ **死字**（§4.4） |

---

## §8 配错了怎么定位：编译期 / 运行期静态 / 运行期动态

### §8.1 编译期：dtc 能查的和不能查的

**好消息：`mboxes` 是 dtc 内建检查的 20 条 phandle 属性之一。**
注册在 `scripts/dtc/checks.c:1486`：

```c
WARNING_PROPERTY_PHANDLE_CELLS(mboxes, "mboxes", "#mbox-cells");
```

（进 check 表在 `:1952-1953`；宏展开在 `:1474-1477`，它同时装了两个检查：
`mboxes_property` 查列表本身，`mboxes_is_cell` 查 `#mbox-cells` 必须是单个 cell。）

**而且它默认就是开的** —— `scripts/Makefile.lib:339-347` 那份
「Disable noisy checks by default」名单里**没有** `mboxes_property`，
所以普通 `make dtbs` 就会报。不用加 `W=1`。

它能抓四类错（消息原文在 `scripts/dtc/checks.c:1380-1458`）：

| dtc 消息 | 出处 | 什么错 |
|---|---|---|
| `cell N is not a phandle reference` | `:1427-1429` | 该放 phandle 的位置放了一个数字（cell 个数算错导致的错位） |
| `Could not get phandle node for (cell N)` | `:1434-1437` | phandle 指向不存在的 label |
| `Missing property '#mbox-cells' in node <路径> or bad phandle (referred from mboxes[N])` | `:1446-1450` | **控制器节点忘了写 `#mbox-cells`** —— 最常见的一条 |
| `property size (N) too small for cell size M` | `:1453-1457` | 最后一个条目的 cell 少写了 |

**dtc 查不到的（本篇所有静默陷阱都在这里）**：

| 查不到 | 为什么 | 去哪节 |
|---|---|---|
| cell 的**值**是否有意义（通道号越界、类型号写错） | dtc 不知道 cell 语义 | §2.2，只能回驱动 `of_xlate` 看 |
| `mbox-names` 与 `mboxes` **是否等长同序** | dtc 完全不检查 `mbox-names` | §4.1 |
| 方向写反（TX/RX 互换） | 结构完全合法 | §5.3 |
| `mbox-names` 是不是死字 | 属性合法，只是没人读 | §4.4 |
| `#mbox-cells = <0>` 与官方绑定冲突 | dtc 只查"是不是单个 cell"，不查值 ≥ 1 | §2.4 |

另一道编译期门是 **`make dtbs_check`**（目标定义在顶层 `Makefile`，
它依赖 `dt_binding_check`；顶层 `Makefile` 是全树同名文件最多的一个，
本篇一律不给它带行号）—— 它拿 `Documentation/devicetree/bindings/` 下的 YAML schema 校验。
但通用 mailbox 绑定是 **`.txt`（`mailbox.txt`）而不是 `.yaml`**，
所以 **`mboxes` / `mbox-names` / `shmem` 这三条通用属性没有 schema 可查**；
只有控制器自己的 YAML（如
`Documentation/devicetree/bindings/mailbox/nvidia,tegra186-hsp.yaml` 里的
`"#mbox-cells": const: 2`）能卡住 `#mbox-cells` 的值。
⇒ **`dtbs_check` 对 mailbox 的覆盖是不完整的，取决于你的控制器有没有 YAML 绑定。**

### §8.2 运行期静态：dtb 已加载，消费者还没起来

四条命令，按顺序做：

```bash
# ① 确认 dtb 里的属性真的进去了（比看 .dts 可靠 —— overlay / bootloader 可能改过）
ls   /sys/firmware/devicetree/base/<控制器节点>/
hexdump -C /sys/firmware/devicetree/base/<控制器节点>/'#mbox-cells'
hexdump -C /sys/firmware/devicetree/base/<消费者节点>/mboxes
cat  /sys/firmware/devicetree/base/<消费者节点>/mbox-names | tr '\0' '\n'

# ② 反编译成 dts 通读一遍（需要主机侧的 dtc）
dtc -I fs -O dts /sys/firmware/devicetree/base > /tmp/live.dts

# ③ 控制器驱动 probe 了吗？—— 这是"通道申请不到"最常见的根因
ls /sys/bus/platform/drivers/<控制器驱动名>/
ls -l /sys/bus/platform/devices/<节点名>/driver

# ④ 谁停在 deferred 上？（§3.3 的关键判据）
cat /sys/kernel/debug/devices_deferred
```

第 ④ 条是 mailbox DT 调试**最高性价比的一条命令**。
因为 `-EPROBE_DEFER` 在核心里一句日志都不打（§3.3），
`devices_deferred` 是唯一的可见出口。判读方法：

| `devices_deferred` 的表现 | 结论 |
|---|---|
| 启动早期有、后来消失 | ✅ 正常的 probe 顺序问题，自愈 |
| **一直有你的消费者节点** | ❌ `mboxes` 的 phandle 指向了一个永远不会注册 mailbox 控制器的节点，或控制器驱动没编进内核 |
| 消费者不在里面，但也没工作 | ⇒ 不是 `-EPROBE_DEFER`，去 §8.3 抓真实错误码 |

注意：**mailbox 核心自己零 debugfs**（`03-internals.md` §C1），
所以**没有** `/sys/kernel/debug/mailbox/` 这种东西可看。
唯一的 debugfs 出口是 `mailbox-test` 自己创建的那两个文件（§6）。

### §8.3 运行期动态：把那三条 debug 日志打开

这是本篇最重要的一条操作。因为核心跟 DT 有关的四个失败出口里
三个是 debug 级、一个连日志都没有（§3.2），**默认什么都看不到**：

```bash
# 需要 CONFIG_DYNAMIC_DEBUG=y
echo 'file drivers/mailbox/mailbox.c +p' > /sys/kernel/debug/dynamic_debug/control

# 然后重新触发消费者 probe
echo <消费者设备名> > /sys/bus/platform/drivers/<消费者驱动>/unbind
echo <消费者设备名> > /sys/bus/platform/drivers/<消费者驱动>/bind
dmesg | tail -40
```

打开之后你会看到 §9 表里的三条 `dev_dbg`。
**如果打开了还是一条都没有**，说明走的是"零日志"那个出口（`mailbox.c:371-374`）——
即 `of_xlate` 返回了错误。这时改用 ftrace 直接看返回值：

```bash
cd /sys/kernel/debug/tracing
echo 'mbox_request_channel' > set_ftrace_filter
echo function_graph > current_tracer
echo 1 > tracing_on
# 触发 probe，然后
cat trace | head -40
```

更省事的替代：`trace-cmd`/`bpftrace` 抓 `mbox_request_channel` 的返回值。
注意 **mailbox 核心零 tracepoint**（`07-debug.md` §3），
所以只能用 kprobe / function tracer，没有现成的 `trace_mbox_*` 事件。

第三步：用 `mailbox-test`（§6）把消费者驱动整个绕开。
**能用 `mailbox-test` 收发 ⇒ DTS 和控制器都没问题，问题在消费者驱动。**
这是最干净的二分法。

### §8.4 症状清单（按坏掉的范围分组）

#### (A) 控制器级：所有消费者都拿不到通道

| 症状 | 最可能的原因 | 验证 |
|---|---|---|
| 全部消费者停在 `devices_deferred` | 控制器驱动没编进内核 / 没 probe | §8.2 第 ③ 条 |
| 控制器 probe 报 `Failed to register mailbox: -EINVAL` | 不是 DTS 问题 —— `mbox_controller_register` 的字段校验（`05-usage.md` §2.4） | 看控制器驱动自己的日志 |
| dtc 报 `Missing property '#mbox-cells'` | 控制器节点漏了 `#mbox-cells` | §8.1 |

#### (B) 单通道级：某一个消费者拿不到

| 症状 | 最可能的原因 | 验证 |
|---|---|---|
| `-ENOENT` | index 超出 `mboxes` 条目数（只写了 1 个却申请 index 1） | 数一下 `mboxes` 有几个条目（§8.2 第 ①） |
| `-EINVAL` + `dev_err "... requires an \"mbox-names\" property"` | 消费者用 `_byname` 但 DTS 没写 `mbox-names` | §4.1 |
| `-EINVAL` + `dev_err "... could not locate channel named"` | 名字拼错，或**请求名比 DTS 名长**（§4.2 的安全方向） | 对着 `mbox-names` 逐字核 |
| `-EINVAL` + **零日志** | `of_xlate` 拒绝了：cell 个数或值不对 | §8.3 的 ftrace |
| `-EBUSY` | 两个消费者指到同一条通道，或控制器模块正在卸载 | §3.4 |
| `-ENODEV` | 消费者不是 DT 设备（`cl->dev->of_node == NULL`） | §3.2 第一行 |

#### (C) cell 参数级：通道拿到了，但是错的那一条

**这一组全部没有任何错误提示**，只能靠现象反推：

| 症状 | 最可能的原因 | 验证 |
|---|---|---|
| 发送成功但对端毫无反应 | cell 值指到了一条没接对端的通道 | 回控制器 `of_xlate` 核 cell 语义（§2.2） |
| 多个消费者同时工作时互相串扰 | 控制器的 `of_xlate` 有 bug（如 §3.4 提到的"永远返回 0 号通道"），DTS 是对的 | 在 `of_xlate` 上下 kprobe 看返回的 `chan` 地址 |
| `mailbox-test` 能通、真实消费者不通 | 两者的 cell / 名字不一样 | 对比两个节点的 `mboxes` |

#### (D) 拓扑与方向级：全部申请成功，但收不到响应

| 症状 | 最可能的原因 | 验证 |
|---|---|---|
| 上层框架（SCMI/SCPI/BPMP）报自己的超时，mailbox 侧无任何错误 | `mboxes` 的 TX/RX 顺序写反（§5.3） | 对着控制器绑定文档核对两个条目 |
| `mbox_send_message` 返回 `-ETIME` | 这是"发不出去"，不是"收不到" —— 属于控制器/TXDone 问题，不是 DTS | `03-internals.md` §A5、`07-debug.md` §5.1 |
| `shmem` 相关报错 | 上层框架的问题，mailbox 核心不读 `shmem`（§5.1） | 去框架源码找 |
| 只有某个 SCMI 协议不工作 | 子节点的 `mboxes`/`shmem`/`mbox-names` 漏写了一条（§5.3 第 2 点） | 检查 `protocol@NN` 子节点 |

---

## §9 错误消息 → 原因映射表

全部来自 `mailbox.c`，一共只有这五条（核心日志普查见 `03-internals.md` §C1）。
**前三条是 debug 级，要按 §8.3 打开才看得见。**

| 内核消息（原文） | 行 | 级别 | 返回码 | 原因 | 去哪节 |
|---|---|---|---|---|---|
| `<func>: No owner device node` | `:347` | `pr_debug` | `-ENODEV` | 消费者 `cl->dev` 为空或不是 DT 设备 | §3.2 |
| `<func>: can't parse "mboxes" property` | `:356` | `dev_dbg` | 原样返回 OF 的错误码（`-EINVAL` / `-ENOENT`） | 没写 `mboxes` / index 越界 / cell 个数与 `#mbox-cells` 不符 | §3.2、§8.4(B) |
| **（无消息）** | `:371-374` | — | `-EPROBE_DEFER` 或 `of_xlate` 的错误码 | phandle 指的节点没注册控制器；或 cell 值/个数被 `of_xlate` 拒绝 | §3.3 |
| `<func>: mailbox not free` | `:377` | `dev_dbg` | `-EBUSY` | 通道已被别人占用；或控制器模块正在卸载 | §3.4 |
| `Unable to startup the chan (<err>)` | `:398` | `dev_err`（唯一） | 控制器 `startup` 的错误码 | 与 DTS 无关：通道已分配，是控制器初始化失败 | `05-usage.md` §2.5 |

`mbox_request_channel_byname`（`:409-437`）另有三条，**全部是 `dev_err`，默认可见**：

| 内核消息（原文） | 行 | 返回码 | 原因 |
|---|---|---|---|
| `<func>() currently only supports DT` | `:418` | `-EINVAL` | 消费者节点没有 `of_node` |
| `<func>() requires an "mbox-names" property` | `:423-424` | `-EINVAL` | 驱动用了 `_byname`，DTS 却没写 `mbox-names` |
| `<func>() could not locate channel named "<name>"` | `:434-435` | `-EINVAL` | 名字对不上（含"请求名比 DTS 名长"的情形，§4.2） |

实用推论：**如果你看到的是 `_byname` 的这三条 `dev_err` 之一，算运气好** ——
它们默认就打。真正难查的是上表第三行那个**零消息**出口。

控制器侧的 DTS 相关消息不在核心里，各驱动自己打。举一例：
`drivers/mailbox/rockchip-mailbox.c:244` 的
`Failed to register mailbox: %d`。这类消息的排查见 `05-usage.md` §2.4。

---

## §10 读完本篇之后

| 想知道 | 去哪篇 |
|---|---|
| `mbox_request_channel` / `_byname` 的完整流程与锁 | `03-internals.md` §A3 |
| `of_xlate` 的默认实现和 16 个自定义实现 | `03-internals.md` §A3、§C4 |
| 控制器驱动怎么写（含要不要自己写 `of_xlate`） | `05-usage.md` §2.6 |
| 消费者驱动怎么写（含 `_byname` 的用法） | `05-usage.md` §3.1 |
| `mailbox-test` 的 debugfs 怎么操作 | `05-usage.md` §6、`07-debug.md` §4 |
| dynamic debug / ftrace / 错误码分诊的完整手册 | `07-debug.md` |
| SCMI / BPMP / RTKit 这些上层框架各自的形状 | `04-extensions.md` §4 |
| 为什么 mailbox 的 DT 出错这么安静 | `08-feynman.md` |
| 本篇每条普查数字的复现命令 | `99-evidence.md` §3 |
