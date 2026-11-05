---
title: 设备树
---

# 06 设备树：dmaengine 的 DTS 怎么写、谁在读、写错了怎么查

> 本篇只讲 arm64。所有举例都取自 `arch/arm64/boot/dts/` 里真实存在的节点，
> 读者可以直接 `git diff` 式地对照。

## 按任务找章节

| 你现在要做的事 | 去哪一节 |
|---|---|
| 我在写一个**外设**节点，要让它能申请 DMA 通道 | §3 |
| 我在写一个**DMA 控制器**节点 | §2 |
| 我想知道 `#dma-cells = <1>` 里那个 `1` 到底是什么 | §4 |
| 我抄了别人的 `dma-channels = <32>`，想知道它有没有用 | §6 |
| 我在写 router / dmamux 节点 | §5（arm64 上基本用不到） |
| 驱动 probe 一直返回 `-EPROBE_DEFER` | §7.3 + §8 |
| 完全没报错，但 DMA 就是没跑起来 | §7.2 静默失效 |
| 我想知道内核打出的那句话是从哪来的 | §8 |

---

## §0 先分清：三方各读什么

DMA 的 DTS 属性有一个特别容易搞错的地方 —— **绝大多数属性都不是
dmaengine 核心读的**。核心读的只有 3 个，剩下的全在具体控制器驱动里，
换个 SoC 就换一套。

| 谁 | 读什么 | 代码在哪 |
|---|---|---|
| 通用 DT / 驱动模型层 | `compatible` `reg` `interrupts` `clocks` `status` … | `drivers/of/`、`drivers/base/` |
| **dmaengine 核心（`of-dma.c`）** | **只有 3 个：`dmas` / `dma-names` / `#dma-cells`** | `drivers/dma/of-dma.c` |
| 具体控制器驱动 | `dma-requests` `dma-channels` `dma-channel-mask` `mediatek,dma-33bits` `arm,pl330-*` … | `drivers/dma/<vendor>/*.c` |

一条可以自己复现的事实：`drivers/dma/dmaengine.c`（1655 行，整个框架的心脏）
**一个 DT 属性都不读**。

```bash
grep -n 'of_property\|of_find_property\|device_property' drivers/dma/dmaengine.c   # 空
```

所有 DT 相关逻辑集中在 `drivers/dma/of-dma.c`（371 行），而它里面真正
读属性的代码只有两个函数：`of_dma_match_channel`（`drivers/dma/of-dma.c:226-242`）
和 `of_dma_request_slave_channel`（`:251-306`）。

---

## §1 属性总表

### §1.1 核心真正会读的 3 个属性

| 属性 | 写在哪个节点 | 类型 | 消费代码（定位到行） | 必需性 |
|---|---|---|---|---|
| `dmas` | **消费者**（外设） | phandle + N 个 cell 的数组 | `of-dma.c:267`（探测存在性）、`:237-238`（真正解析） | 没有它 → 静默返回 `-ENODEV`，见 §7.2 |
| `dma-names` | **消费者**（外设） | 字符串数组，与 `dmas` 一一对应 | `of-dma.c:231`（按 index 取）、`:234`（`strcmp`）、`:270`（数个数） | 有 `dmas` 却没它 → `pr_err` 报错，见 §8 |
| `#dma-cells` | **提供者**（控制器 / router） | u32 | `of-dma.c:237-238`（作为 `of_parse_phandle_with_args` 的 cells 名） | 绑定里唯一 `required` 的通用属性（`dma-common.yaml:50-51`） |

三者的关系用一句话说清：**`dmas` 提供"指向谁 + 几个参数"，`#dma-cells`
决定"几个参数"，`dma-names` 给每一项起个名字让消费者驱动能按名字要。**

### §1.2 `#dma-cells`：数量由绑定定，含义由驱动定

`Documentation/devicetree/bindings/dma/dma-common.yaml:20-25` 只约束了范围：
最小 1（`:21`）、最大 255（`:23`）。**它不说这些 cell 是什么意思** ——
含义完全由控制器驱动的 `of_dma_xlate` 回调决定，见 §4。

arm64 上实际见到的值几乎清一色是 1。两个真实例子：

- `Documentation/devicetree/bindings/dma/arm,pl330.yaml` 在 `'#dma-cells'`
  一项下写死 `const: 1`，并注明 "Contains the DMA request number for the consumer"
- `Documentation/devicetree/bindings/dma/mediatek,uart-dma.yaml` 在同一项下也写死
  `const: 1`，注明 "The first cell specifies the UART APDMA channel number"

**注意这两句话的语义完全不一样**：pl330 那个 1 是"外设请求线号"，
mtk 那个 1 是"通道号"。同样写 `<&xxx 3>`，一个是"我接在第 3 根请求线上"，
一个是"给我第 3 号通道"。**抄 DTS 之前必须先看绑定文档说这一个 cell 是什么。**

### §1.3 `dmas` / `dma-names` 的成对约束

核心是这样取的（`drivers/dma/of-dma.c:226-242`，`of_dma_match_channel`）：

```c
/* :231 */ if (of_property_read_string_index(np, "dma-names", index, &s))
/* :232 */         return -ENODEV;
/* :234 */ if (strcmp(name, s))
/* :235 */         return -ENODEV;
/* :237 */ if (of_parse_phandle_with_args(np, "dmas", "#dma-cells", index,
/* :238 */                                dma_spec))
```

关键：**先按 index 读 `dma-names` 的第 index 项做 `strcmp`，
匹配上了才去 `dmas` 里取第 index 项。** 也就是说 —— **两个属性靠下标对齐，
不靠任何标记**。`dma-names = "tx", "rx"` 配 `dmas = <&x 0>, <&x 1>`，
那么 `"tx"` 一定对应 `<&x 0>`。写反了不会有任何报错，只会数据方向反掉。

调用方 `of_dma_request_slave_channel` 的循环在 `:282-286`，
上界来自 `of_property_count_strings(np, "dma-names")`（`:270`）——
**循环次数由 `dma-names` 的项数决定，不由 `dmas` 决定**。所以
`dmas` 比 `dma-names` 多写的项永远不会被访问到（多写了也不报错）。

---

## §2 提供者节点：DMA 控制器怎么写

### §2.1 通用形状

绑定分两层：
`Documentation/devicetree/bindings/dma/dma-common.yaml`（53 行，纯属性定义）
和 `Documentation/devicetree/bindings/dma/dma-controller.yaml`（37 行）。
后者极短，只做两件事：`allOf: $ref dma-common.yaml`（`:12-13`），
以及规定节点名必须是 `dma-controller` 或 `dma-controller@...`（`:17-18`）。

所以**新写的 DMA 控制器节点名必须叫 `dma-controller@<addr>`**，
不能叫 `dma@`、`dmac@`。老的 DTS 里有历史遗留的名字，但新增会被
`make dtbs_check` 拦下。

### §2.2 真实例子一：pl330（arm64 上最常见的 DMA 控制器）

`arch/arm64/boot/dts/rockchip/rk3399.dtsi:1415-1424`：

```dts
dmac_bus: dma-controller@ff6d0000 {
        compatible = "arm,pl330", "arm,primecell";   /* :1416 */
        reg = <0x0 0xff6d0000 0x0 0x4000>;
        interrupts = <GIC_SPI 5 IRQ_TYPE_LEVEL_HIGH 0>,
                     <GIC_SPI 6 IRQ_TYPE_LEVEL_HIGH 0>;
        #dma-cells = <1>;                            /* :1420 */
        arm,pl330-periph-burst;                      /* :1421 */
        clocks = <&cru ACLK_DMAC0_PERILP>;
        clock-names = "apb_pclk";
};
```

同文件 `:1426-1435` 还有一个 `dmac_peri`，形状完全一样。
`arm,pl330-periph-burst` 是 pl330 的私有 quirk，绑定在
`Documentation/devicetree/bindings/dma/arm,pl330.yaml` 的 `arm,pl330-periph-burst`
一项（`type: boolean`）；驱动里由 `drivers/dma/pl330.c:3032` 的 `of_property_read_bool(np, of_quirks[i].quirk)`
一次性扫一张 quirk 表读进来。**核心看不见这个属性。**

注意 `arm,pl330.yaml` 末尾写的是 `unevaluatedProperties: false` ——
pl330 节点里**不允许**出现绑定没列出的属性，包括 `dma-channels`
和 `dma-requests`（绑定里确实没列，见 §6）。

### §2.3 真实例子二：MediaTek UART APDMA（每通道一段 reg + 一根中断）

`arch/arm64/boot/dts/mediatek/mt2712e.dtsi:384-415`（为省篇幅省略了中间重复项）：

```dts
apdma: dma-controller@11000400 {
        compatible = "mediatek,mt2712-uart-dma",     /* :385 */
                     "mediatek,mt6577-uart-dma";     /* :386 */
        reg = <0 0x11000400 0 0x80>,                 /* :387 起，共 12 段 */
              ... ,
              <0 0x11000980 0 0x80>;                 /* :398 */
        interrupts = <GIC_SPI 103 IRQ_TYPE_LEVEL_LOW>,  /* :399 起，共 12 根 */
                     ... ,
                     <GIC_SPI 114 IRQ_TYPE_LEVEL_LOW>;  /* :410 */
        dma-requests = <12>;                         /* :411 */
        clocks = <&pericfg CLK_PERI_AP_DMA>;         /* :412 */
        clock-names = "apdma";                       /* :413 */
        #dma-cells = <1>;                            /* :414 */
};
```

这个节点值得单独讲，因为它示范了一个反常的形状：**`reg` 有 12 段、
`interrupts` 有 12 根，而不是 1 段 + 1 根**。原因在驱动里 ——
`drivers/dma/mediatek/mtk-uart-apdma.c:531-549` 的 for 循环按通道号
逐个 `devm_platform_ioremap_resource(pdev, i)`（`:538`）和
`platform_get_irq(pdev, i)`（`:546`），一个通道一套。

它的 `dma-requests` 在这个驱动里的含义也很特殊：
`mtk-uart-apdma.c:525` 用 `of_property_read_u32(np, "dma-requests", &mtkd->ddev.max_burst)`
读出来当**通道数**用，读不到就 `:526` 给一个默认值。
绑定 `Documentation/devicetree/bindings/dma/mediatek,uart-dma.yaml` 的
`dma-requests` 一项把它描述成 "Number of virtual channels"，上限 16 —— 和通用
`dma-common.yaml:45-48` 里"请求线数"的语义**不是一回事**。

同一份绑定还定义了 `mediatek,dma-33bits`（`type: boolean`），
由 `mtk-uart-apdma.c:496` 的 `of_property_read_bool` 读取。
注意这份绑定末尾写的是 `additionalProperties: false`，
比 pl330 更严 —— 连 `dma-channels` 都不许写。

### §2.4 提供者侧的注册动作（DTS 之外的另一半）

DTS 写对了只是一半，控制器驱动还必须主动把自己挂进 `of_dma_list`：

```c
of_dma_controller_register(np, my_xlate, my_data);   /* of-dma.c:118-145 */
```

- 参数检查 `!np || !of_dma_xlate` 在 `of-dma.c:122`，失败打
  `"%s: not enough information provided\n"`（`:123`）并返回 `-EINVAL`（`:124`）
- 三个字段的赋值：`ofdma->of_node = np`（`:133`）、
  `of_dma_xlate`（`:134`）、`of_dma_data = data`（`:135`）
- 挂链在 `of_dma_lock` 保护下：`:138` 上锁、`:139` `list_add_tail`、`:140` 解锁
- 对应的拆除是 `of_dma_controller_free(np)`（`:153-167`）

**匹配的键是 `of_node` 指针本身，不是 compatible、不是地址。**
`of_dma_find_controller`（`of-dma.c:32-44`）遍历 `of_dma_list`（`:36`），
判断条件就一行：`if (ofdma->of_node == dma_spec->np)`（`:37`），命中就
`return ofdma`（`:38`）。
所以消费者 `dmas` 里的 phandle 必须指向**控制器驱动 probe 时用的那个
`np`**。如果控制器驱动注册时传的是子节点、而 DTS 里 phandle 指的是父节点，
就永远匹配不上 —— 而且**不会有任何 error 级别的日志**，只有一句
`pr_debug`（`of-dma.c:40-41`），默认看不见。这是本篇第一个 静默失效点，
详见 §7.2。

---

## §3 消费者节点：外设怎么申请 DMA

### §3.1 最小形状就两行

没有 `dma-client.yaml` 这种东西 —— 消费者侧**没有任何独立的通用绑定文件**，
规则完全隐含在 `of-dma.c` 的代码里和各外设自己的绑定里。
最小形状永远是这两个属性：

```dts
dmas = <&<控制器 phandle> <#dma-cells 个参数>>, <...>;
dma-names = "<名字1>", "<名字2>";
```

### §3.2 真实例子一：SPI 控制器要一对 tx/rx（pl330）

`arch/arm64/boot/dts/rockchip/rk3399.dtsi:727-739`：

```dts
spi0: spi@ff1c0000 {
        compatible = "rockchip,rk3399-spi", "rockchip,rk3066-spi";
        ...
        dmas = <&dmac_peri 10>, <&dmac_peri 11>;   /* :733 */
        dma-names = "tx", "rx";                     /* :734 */
        ...
};
```

`10` 和 `11` 是 pl330 的**外设请求线号**（见 §1.2），
不是通道号 —— pl330 有几条物理通道由硬件寄存器决定，DTS 管不着。

### §3.3 真实例子二：串口要一对 tx/rx（MediaTek APDMA）

`arch/arm64/boot/dts/mediatek/mt2712e.dtsi:433-435`（`serial@11002000`）：

```dts
dmas = <&apdma 0
        &apdma 1>;      /* :433-434 */
dma-names = "tx", "rx";  /* :435 */
```

同文件 `:304-306` 是另一个串口（`serial@1000f000`），用的是
`<&apdma 10 &apdma 11>`。**注意这里的 `0`/`1`/`10`/`11` 是通道号**，
所以两个串口不能撞号；而 §3.2 里 pl330 那组数字是请求线号，
语义完全不同 —— 同样长相的 DTS，含义取决于控制器绑定。

这两个例子还示范了一个格式细节：`dmas` 的多项之间既可以用逗号分隔
（rk3399 的写法），也可以在一个 `<>` 里换行连写（mt2712e 的写法）。
DTC 看到的是同一个 cell 流，**分组完全由 `#dma-cells` 决定，
逗号和换行都只是排版**。这也是为什么 `#dma-cells` 写错时错位是静默的。

### §3.4 消费者驱动侧要配合的一行

DTS 里的名字必须和驱动里 `dma_request_chan(dev, name)` 的第二个参数
**字符串完全相同**（`strcmp`，`of-dma.c:234`）。链路是：

```
驱动: dma_request_chan(dev, "rx")
        └─ dmaengine.c:818  if (dev->of_node)
             └─ dmaengine.c:819  of_dma_request_slave_channel(dev->of_node, "rx")
                  └─ of-dma.c:282-286  逐 index 调 of_dma_match_channel(np, "rx", i, &spec)
                       └─ of-dma.c:231  读 dma-names[i]
                       └─ of-dma.c:234  strcmp("rx", dma-names[i])
                       └─ of-dma.c:237  取 dmas[i]（按 #dma-cells 切）
                  └─ of-dma.c:289  of_dma_find_controller(&spec)
                  └─ of-dma.c:292  ofdma->of_dma_xlate(&spec, ofdma)
```

名字没有任何约定俗成的强制。`"tx"`/`"rx"` 只是惯例；
用 `"x"`/`"y"` 也能跑，只要 DTS 和驱动一致。反过来说 ——
**改 DTS 里的 `dma-names` 而不改驱动，症状是"通道申请不到"，不是"名字非法"。**

---

## §4 `#dma-cells` 里那几个数到底是什么：三种 xlate 对照

这是本篇最需要理解的一节。核心把 `dmas` 切成 `struct of_phandle_args`
之后**不做任何解释**，直接交给控制器驱动的 `of_dma_xlate` 回调
（`of-dma.c:292`）。所以"那个数是什么意思"这个问题，
答案永远在 `of_dma_xlate` 的实现里。

本树里能碰到的三种实现：

| xlate 实现 | 位置 | 要求的 cells | `args[0]` 被当成 | 谁在用 |
|---|---|---|---|---|
| `of_dma_simple_xlate` | `drivers/dma/of-dma.c:321-335` | 必须 1（`:330`） | **filter 函数的 32 位参数** | 老式带 filter 的驱动 |
| `of_dma_xlate_by_chan_id` | `drivers/dma/of-dma.c:351-370` | 必须 1（kerneldoc `:345`） | **通道在 `channels` 链表里的序号** | mtk 三个驱动等 |
| 驱动自己写的 | 例：`drivers/dma/pl330.c:2142-2160` | 驱动自定（pl330 要 1，`:2151`） | **外设请求线号** | 绝大多数 |

三者的核心差异逐行看：

`of_dma_simple_xlate`（kerneldoc `of-dma.c:310-320`，其中 `:317-318`
明确写 "requires that #dma-cells is equal to 1 and the argument of the
dma specifier is the 32-bit filter_param"）：

```c
/* :323 */ int count = dma_spec->args_count;
/* :324 */ struct of_dma_filter_info *info = ofdma->of_dma_data;
/* :326 */ if (!info || !info->filter_fn)
/* :327 */         return NULL;
/* :330 */ if (count != 1)
/* :331 */         return NULL;
/* :333 */ return __dma_request_channel(&info->dma_cap, info->filter_fn,
/* :334 */                              &dma_spec->args[0], dma_spec->np);
```

注意 `:334` 传的是 **`&dma_spec->args[0]`（取地址）** ——
这个 32 位数字原封不动变成 filter 函数的 `void *param`。
filter 怎么解释它，是 filter 自己的事，核心不管。

`of_dma_pl330_xlate`（`drivers/dma/pl330.c:2142-2160`）是"驱动自定"的范本：

```c
/* :2144 */ int count = dma_spec->args_count;
/* :2145 */ struct pl330_dmac *pl330 = ofdma->of_dma_data;
/* :2148 */ if (!pl330)
/* :2149 */         return NULL;
/* :2151 */ if (count != 1)
/* :2152 */         return NULL;
/* :2155 */ chan_id = dma_spec->args[0];
/* :2156 */ if (chan_id >= pl330->num_peripherals)
/* :2157 */         return NULL;
/* :2159 */ return dma_get_slave_channel(&pl330->peripherals[chan_id].chan);
```

**三个 `return NULL` 全是静默的** —— 请求线号越界（`:2156`）、
cells 数不对（`:2151`）都只回 NULL，不打任何日志。
NULL 一路回到 `of_dma_request_slave_channel`，在那里被
`ret_no_channel = -EPROBE_DEFER`（`of-dma.c:294`）覆盖。
**所以"DTS 里请求线号写超了"的最终症状是消费者驱动永远
`-EPROBE_DEFER`，日志里一个字都没有。** 这是本篇最重要的 静默失效点，
决策树见 §7.3。

（这条链路上核心那一侧的实现缺陷，另有一条已记在 `99-evidence.md` §6 里：
router 路径 `of-dma.c:66-67` 把 route-allocate 的真实错误码也压成 NULL。）

---

## §5 router / dmamux 节点（arm64 上基本用不到）

绑定是 `Documentation/devicetree/bindings/dma/dma-router.yaml`（54 行）。
它同样 `allOf: $ref dma-common.yaml`（`:12-13`），
节点名要求 `dma-router` 或 `dma-router@...`（`:22-23`），
比控制器多一个属性：

| 属性 | 定义 | 必需 |
|---|---|---|
| `dma-masters` | `dma-router.yaml:25-31`，指向 router 后面真实控制器的 phandle 数组 | **是**（`:39`） |
| `dma-requests` | `:33-35`，router 能接的**入口**请求线数 | 否 |
| `#dma-cells` | 继承自 `dma-common.yaml` | **是**（`:38`） |

绑定自带的例子（`:45-52`）用的是 TI DRA7x：

```dts
sdma_xbar: dma-router@4a002b78 {
        compatible = "ti,dra7-dma-crossbar";
        #dma-cells = <1>;
        dma-requests = <205>;
        ti,dma-safe-map = <0>;      /* :50，TI 私有 */
        dma-masters = <&sdma>;      /* :51 */
};
```

`dma-masters` 由 router 驱动自己读，例如
`drivers/dma/stm32-dmamux.c:131` 的
`of_parse_phandle(ofdma->of_node, "dma-masters", i - 1)`。**核心不读它。**

router 侧的注册函数是另一个：`of_dma_router_register`
（`of-dma.c:185-212`，kerneldoc `:170-184`）。它和
`of_dma_controller_register` 的唯一实质差别是把 xlate 写死：
`ofdma->of_dma_xlate = of_dma_router_xlate`（`:202`），
再存下 `of_dma_route_allocate`（`:203`）和 `dma_router`（`:204`）。
参数检查在 `:192`，日志文字和控制器那条一模一样（`:193`）。

**arm64 相关性：零。** 本树四个 router 驱动
（`drivers/dma/stm32-dmamux.c`、`drivers/dma/ti/dma-crossbar.c`、
`drivers/dma/lpc18xx-dmamux.c`、`drivers/dma/dw/rzn1-dmamux.c`）
对应的 Kconfig 全部依赖非 arm64 平台，`arch/arm64/boot/dts/` 下
没有任何 `dma-router` 节点。展开分析见 `04-extensions.md` §3。

---

## §6 死字属性：写了没人读的那几个

`dma-common.yaml` 一共只定义 4 个属性，但**其中 3 个在 dmaengine 核心里
完全没有消费者**：

| 属性 | 绑定定义 | 核心消费者 | 实际消费者 |
|---|---|---|---|
| `#dma-cells` | `dma-common.yaml:20-25` | ✅ `of-dma.c:237-238` | — |
| `dma-channels` | `:40-43` | ❌ **无** | 28 个控制器驱动各读各的 |
| `dma-requests` | `:45-48` | ❌ **无** | 13 个控制器驱动各读各的 |
| `dma-channel-mask` | `:27-38` | ❌ **无** | 6 个驱动，且其中 1 个读的还是别的名字 |

复现命令：

```bash
grep -rl '"dma-channels"'     drivers/dma/ | wc -l   # 28
grep -rl '"dma-requests"'     drivers/dma/ | wc -l   # 13
grep -rn '"dma-channel-mask"' drivers/dma/           # 见下表
```

`dma-channel-mask` 这一条特别值得点名，因为它最像"通用属性"却最不通用。
绑定 `dma-common.yaml:27-38` 说它是 uint32 数组、LSB 对应 0 号通道、
每 32 条通道一项。全树读它的只有：

| 驱动 | 行号 | 读法 |
|---|---|---|
| `drivers/dma/lgm/lgm-dma.c` | `:1636` | `device_property_read_u32` |
| `drivers/dma/qcom/gpi.c` | `:2176` | 读 `"dma-channel-mask"` |
| `drivers/dma/ti/edma.c` | `:2486-2496` | 读，且失败时有两句 warning（`:2493`、`:2496`） |
| `drivers/dma/ti/omap-dma.c` | `:1751` | 读 |
| `drivers/dma/k3dma.c` | `:864-867` | 读 |
| `drivers/dma/bcm2835-dma.c` | `:947` | 读的是 **`"brcm,dma-channel-mask"`**（带厂商前缀），不是通用名 |

**实践结论：在一个 DMA 控制器节点里写 `dma-channels` / `dma-requests` /
`dma-channel-mask`，必须先确认你的控制器驱动真的读它。** 抄来的属性
写上去不报错、`dtbs_check` 也放过（`dma-common.yaml:53`
是 `additionalProperties: true`），但一点作用都没有 —— 除了让下一个
读 DTS 的人以为通道数是可配的。

反过来也有个陷阱：如果绑定写了 `unevaluatedProperties: false`
（`arm,pl330.yaml`）或 `additionalProperties: false`
（`mediatek,uart-dma.yaml`），那么写上去反而会**编译期**被
`make dtbs_check` 拦下 —— 这属于 §7.1 的第一层。

---

## §7 配错了怎么定位：三层

### §7.1 第一层：编译期（DTS 还没进内核）

```bash
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- dtbs          # 语法 + phandle 解析
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- dtbs_check    # 再按 yaml 绑定校验
```

这一层能抓到的、也只能抓到的：

| 能抓到 | 抓不到 |
|---|---|
| phandle 指向不存在的 label | phandle 指向了**存在但不对**的节点 |
| cell 数量与 `#dma-cells` 不匹配导致的解析失败 | cell 数量对、但**数值越界**（§4 那个 ） |
| `additionalProperties: false` 的绑定里写了多余属性 | `additionalProperties: true` 的绑定里写了死字属性（§6） |
| 节点名不符合 `^dma-controller(@.*)?$` | `dma-names` 与 `dmas` **顺序写反** |

第二列全部是**运行期静默**问题。这就是为什么本篇要单列 §7.3。

### §7.2 第二层：运行期静态（DTS 已经在跑，但还没到驱动）

先确认内核看到的树和你写的一致：

```bash
ls /proc/device-tree/soc/dma-controller@11000400/          # 属性一个一个躺在这
cat /proc/device-tree/serial@11002000/dma-names | tr '\0' '\n'
dtc -I fs -O dts /proc/device-tree > /tmp/running.dts       # 反编译回 DTS 再 diff
```

按"坏掉的范围"分组的症状清单：

| 范围 | 症状 | 最可能的原因 |
|---|---|---|
| **控制器级**（这个控制器下所有外设都没 DMA） | 所有消费者一直 `-EPROBE_DEFER` | 控制器驱动没 probe 成功（clk/reg/irq 缺）→ 根本没调 `of_dma_controller_register`；或 `status = "disabled"` |
| **控制器级** | 控制器 probe 成功但消费者仍拿不到 | phandle 指向的 `np` 不是驱动注册时用的那个（§2.4 ） |
| **单外设级**（只有一个外设没 DMA） | 该外设 `dma_request_chan` 失败 | `dma-names` 字符串与驱动里的不一致（§3.4） |
| **单外设级** | 报 `dma-names property ... missing or empty` | 写了 `dmas` 忘了 `dma-names`（§8 第 2 条） |
| **单外设级** | 完全没日志，驱动直接走了 PIO 分支 | **`dmas` 属性整个不存在** —— 核心在 `of-dma.c:267` 探测不到就 `return ERR_PTR(-ENODEV)`（`:268`），源码 `:266` 的注释原文是 "Silently fail if there is not even the 'dmas' property" |
| **参数级**（通道拿到了但传输不对） | 数据方向反了 / 收发串台 | `dma-names` 与 `dmas` 下标错位（§1.3 ） |
| **参数级** | 一直 `-EPROBE_DEFER` | 请求线号/通道号越界，被 xlate 静默判 NULL（§4 ） |
| **拓扑级** | 两个外设互相干扰 | 两个消费者写了同一个通道号（mtk 那类 by-chan-id 的控制器上会直接抢） |

### §7.3 第三层：运行期动态（把静默的部分逼出来）

**核心手段只有一个 —— 打开 `of-dma.c` 的 dynamic debug。**
上面列的静默失效里，唯一留下痕迹的是 `of_dma_find_controller` 的
`pr_debug`（`of-dma.c:40-41`）：

```bash
echo 'file drivers/dma/of-dma.c +p' > /sys/kernel/debug/dynamic_debug/control
# 然后触发一次消费者驱动的 probe（unbind/bind 即可）
echo <设备> > /sys/bus/platform/drivers/<驱动>/unbind
echo <设备> > /sys/bus/platform/drivers/<驱动>/bind
dmesg | tail -40
```

看到 `can't find DMA controller /soc/...` → 就是 §2.4 那个 phandle 指错节点。
**看不到任何东西** → 说明控制器找到了，问题在 xlate 里面，走下面的决策树：

```
消费者驱动一直 -EPROBE_DEFER
 │
 ├─ 打开 of-dma.c dynamic debug，dmesg 有 "can't find DMA controller"？
 │    ├─ 有  → phandle 指错节点 / 控制器没注册。查 §2.4
 │    └─ 无  → 控制器找到了，继续
 │
 ├─ /sys/kernel/debug/dmaengine/summary 里有这个控制器吗？
 │    ├─ 无  → 控制器 dma_async_device_register 没成功（不是 DTS 问题，转 07）
 │    └─ 有  → 通道存在，继续
 │
 ├─ summary 里的通道数 == 你以为的通道数？
 │    ├─ 不等 → 控制器驱动读的那个私有属性（dma-requests 等）值不对。查 §2.3 / §6
 │    └─ 相等 → 继续
 │
 └─ 最后剩下的唯一可能：dmas 里的 args[0] 越界，被 xlate 静默判 NULL。
      对 pl330 是 chan_id >= num_peripherals（pl330.c:2156）
      对 by-chan-id 型是序号超出 channels 链表长度
      → 把 args[0] 改小重试，或直接在 xlate 里加打印
```

`summary` 这个文件的来源是 `drivers/dma/dmaengine.c:130-131`
（`debugfs_create_file("summary", 0444, rootdir, NULL, &dmaengine_summary_fops)`），
它的内容格式和更多调试手段见 `07-debug.md`。

**本篇不给示例输出** —— 这套文档没有在真机上跑过（见
`99-evidence.md` §5），编造 `dmesg` / `summary` 的输出会误导读者。

---

## §8 错误消息 → 原因 映射表

dmaengine 的 DT 路径**一共只有 4 处会打印**，全在
`drivers/dma/of-dma.c` 里。记住这 4 条，就能反向定位所有非静默故障：

| 内核打印的原文 | 位置 | 级别 | 谁触发的 | 原因 |
|---|---|---|---|---|
| `<func>: not enough information provided` | `of-dma.c:262` | `pr_err` | 消费者路径 | 调 `of_dma_request_slave_channel` 时 `!np \|\| !name`（`:261`）—— 消费者驱动传了 NULL，**不是 DTS 问题**，返回 `-ENODEV`（`:263`） |
| `<func>: dma-names property of node '<节点>' missing or empty` | `of-dma.c:272-273` | `pr_err` | 消费者路径 | `of_property_count_strings(np, "dma-names")` 返回负值（`:270-271`）。**DTS 里写了 `dmas` 但没写 `dma-names`**，或者 `dma-names` 是空的 |
| `<func>: not enough information provided` | `of-dma.c:123` | `pr_err` | 提供者路径 | `of_dma_controller_register` 收到 `!np \|\| !of_dma_xlate`（`:122`）—— 控制器驱动的 bug，返回 `-EINVAL`（`:124`） |
| `<func>: not enough information provided` | `of-dma.c:193` | `pr_err` | router 路径 | `of_dma_router_register` 收到 `!np \|\| !of_dma_route_allocate \|\| !dma_router`（`:192`）—— router 驱动的 bug |
| `<func>: can't find DMA controller <节点>` | `of-dma.c:40-41` | **`pr_debug`** | 消费者路径 | **默认看不见**。phandle 指向的 `of_node` 不在 `of_dma_list` 里。开启方式见 §7.3 |

注意前三条**文字完全相同**（`"%s: not enough information provided\n"`），
只靠 `%s` 展开出来的函数名区分：
`of_dma_request_slave_channel` / `of_dma_controller_register` / `of_dma_router_register`。
看到这句话时**先看函数名再判断是 DTS 问题还是驱动问题**。

router 驱动自己还会打一些，例如
`drivers/dma/stm32-dmamux.c:115` 的 `"Run out of free DMA requests"`
和 `:95`、`:100-101` 的两条参数校验 —— 但那些都是驱动私有，
arm64 上碰不到（§5）。

---

## §9 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 这些 DT 属性最终变成了哪些内存里的结构体 | `02-datastructures.md` |
| `of_dma_request_slave_channel` 之后核心还做了什么 | `03-internals.md` Part A |
| router 到底怎么把自己伪装成控制器 | `04-extensions.md` §3 |
| 我要照着写一个控制器驱动 / 消费者驱动 | `05-usage.md` §1（消费者）、§2（提供者） |
| `summary` 文件长什么样、还有哪些调试开关 | `07-debug.md` |
| `#dma-cells` 为什么要设计成"含义由驱动定" | `08-feynman.md` |
| 本篇每条论断的出处和复现命令 | `99-evidence.md` §3 |

