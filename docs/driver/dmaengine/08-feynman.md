---
title: 费曼学习法
---

# 08 费曼解释：dmaengine 里最容易想错的十个地方

> arm64-only。每个类比后面都跟着源码行号 —— 类比是脚手架，
> 拆掉脚手架之后留下的必须是代码。

## 本篇解决什么

前面七篇讲的是"是什么"和"在哪里"。这一篇只讲一件事：
**你脑子里那个直觉模型，在哪几处和代码不一样。**

十个题目，按"想错了代价有多大"排序：

| # | 问题 | 想错了会怎样 |
|---|---|---|
| 1 | DMA 控制器和用 DMA 的外设，是 device / driver 的关系吗？ | 整套心智模型错位 |
| 2 | `dma_request_chan(dev, "rx")` 里那个字符串到底是什么？ | 改 DTS 时改错地方 |
| 3 | `#dma-cells = <1>` 里那个 `1` 是什么？ | 抄 DTS 抄出静默故障 |
| 4 | 为什么 `prep → submit → issue_pending` 要分三步？ | 写驱动漏一步就永远不动 |
| 5 | cookie 为什么是个整数，不是指针？ | 查状态时用错 API |
| 6 | 为什么 memcpy 通道能匿名要，slave 通道必须报名字？ | 以为 dmatest 过了就没事 |
| 7 | virt-dma 那五条链表在干什么？ | 读驱动读不下去 |
| 8 | `terminate_async` + `synchronize` 为什么不合成一个？ | 释放内存时踩内存 |
| 9 | `dma_slave_config` 为什么由外设驱动填？ | 以为控制器该自己知道 |
| 10 | 音频为什么用 cyclic，不用一次次 submit？ | 音频卡顿/爆音查错方向 |

---

## §1 DMA 控制器和用 DMA 的外设，是 device / driver 的关系吗？

**不是。而这是本子系统最容易被类比错的一组关系。**

### 先举个例子

写过 i2c 或 spi 驱动的人，脑子里有一个很顺的模型：
控制器（adapter/master）在 platform 总线上，挂在它下面的设备
（i2c_client / spi_device）在自己的子系统总线上，
内核帮你把设备和驱动配对，`probe` 自动被调起来。

DMA **完全不是这个形状**。打个比方：

i2c 像是**公司里的部门和员工** —— 部门（控制器）下面有编制，
员工（设备）入职时被分到某个部门，人事系统（总线匹配）负责配对。
你能在组织架构图上看到"谁属于谁"。

DMA 更像是**写字楼里的货运电梯**。电梯是楼的公共设施，
不属于任何一家公司；哪家公司要搬东西，自己打电话到物业
**按名字预约一个时间段**，用完了释放。
电梯不知道自己会被谁用，公司也不"属于"电梯。
**楼层平面图上根本画不出"这家公司在电梯下面"这种关系。**

所以：一个 UART 用了 DMA，**UART 不是 DMA 控制器的子设备**。
它俩在设备模型里是**平级**的，都是 platform 设备。

### 回到代码

三条硬证据：

1. **dmaengine 没有自己的 `bus_type`。** 它只注册了一个 class：
   `static struct class dma_devclass`（`drivers/dma/dmaengine.c:240-244`，
   `.name = "dma"` 在 `:241`），在 `dma_bus_init` 里
   `class_register`（`:1649`）。**class 不做 driver 匹配**，
   它只是给 sysfs 一个挂点。
2. **通道不是"设备"，只是被包了一层 device 用来放 sysfs 属性。**
   `chan->dev` 的类型是 `struct dma_chan_dev`，名字由
   `dev_set_name(&chan->dev->device, "dma%dchan%d", ...)`
   （`:1069-1070`）生成，它**永远不会绑上任何 driver** ——
   没有 `bus_type` 就没有匹配。
3. **两边的关联只是一条 sysfs 符号链接**，`dmaengine.c:866-870`：
   通道目录下建一个叫 `slave` 的链接指向使用者，
   使用者目录下建一个叫 `dma:<名字>` 的链接指回通道。
   这两条链接是**唯一**能在 sysfs 上看出"谁在用谁"的东西。

获取服务的方式也不是"被 probe 到"，而是**外设驱动主动去要**：
`dma_request_chan(dev, "rx")`（`dmaengine.c:813-873`）。

### 这个类比什么时候会误导你？

"货运电梯"这个类比在**独占性**上会骗你。

真实的货运电梯是**分时复用**的：你用完我用。
`dma_chan` 却是**独占**的 —— `dma_chan_get` 成功之后
`client_count` 就归你了，别人来申请会在
`dmaengine.c:641` 那条 `dev_dbg("<chan> busy")` 上被挡回去。
**申请到的通道在 `dma_release_channel` 之前一直是你的**，
哪怕你一整天不传一个字节。

所以 arm64 板子上"通道不够用"是常见故障，而它长得像
`-EPROBE_DEFER` 或者申请失败，不像"排队等一会儿"。
真正分时复用的东西叫 router / dmamux（`06-devicetree.md` §5），
arm64 上基本用不到。

---

## §2 `dma_request_chan(dev, "rx")` 里那个字符串到底是什么？

### 先举个例子

很多人第一次看到这行代码，会以为 `"rx"` 是某种**内核约定的关键字** ——
像 `"clk"`、`"reset"` 那样，内核认识它。

它不是。它更像是**你自己给行李箱贴的标签**。

你去机场托运两个箱子，自己在上面贴了"衣服""电器"两张纸条。
到了目的地你说"给我拿'电器'那个"。
**机场不认识"电器"这两个字**，它只是把你贴的标签和箱子的位置
记在一张表上，你报哪个标签它查哪一行。
你要是贴的是"A""B"，说"给我 B"一样能取到。
但如果你**寄的时候贴"电器"、取的时候说"电子"**，就永远取不到。

### 回到代码

那张"表"就是 DTS 里的两个属性，**靠下标对齐**：

```dts
dmas      = <&apdma 0>, <&apdma 1>;
dma-names = "tx",       "rx";
```

核心比对的代码在 `drivers/dma/of-dma.c:226-242`：

```c
/* :231 */ if (of_property_read_string_index(np, "dma-names", index, &s))
/* :232 */         return -ENODEV;
/* :234 */ if (strcmp(name, s))
/* :235 */         return -ENODEV;
/* :237 */ if (of_parse_phandle_with_args(np, "dmas", "#dma-cells", index,
/* :238 */                                dma_spec))
```

`:234` 就是一次**纯字符串比较**。内核对 `"rx"` 这三个字节没有任何
预设含义。循环上界来自 `dma-names` 的项数
（`of_property_count_strings`，`of-dma.c:270`）。

### 这个类比什么时候会误导你？

行李标签贴错了，地勤会告诉你"没有这个标签的箱子"。
**`dma-names` 写错了，内核什么都不说。**

`of_dma_match_channel` 返回 `-ENODEV`（`:232`/`:235`），
调用方在 `of-dma.c:282-286` 的循环里默默试下一个 index，
全试完就返回预置的 `ret_no_channel`，也就是
`-EPROBE_DEFER`（`of-dma.c:294`）。**日志里一个字都没有。**

所以症状是"驱动一直 probe 不上"，不是"名字非法"。
查法见 `06-devicetree.md` §7.3 的决策树。

---

## §3 `#dma-cells = <1>` 里那个 `1` 是什么？

### 先举个例子

`#dma-cells = <1>` 说的是"引用我的时候后面跟 1 个数字"。
问题在于**那个数字是什么意思，DTS 里看不出来**。

想象两栋写字楼都规定"预约电梯时报一个号码"：

- A 楼：报的是**你在几号装卸口**。物业按装卸口找对应的电梯。
- B 楼：报的是**你要用几号电梯**。

两栋楼的预约单**长得一模一样**：`<&电梯 3>`。
但在 A 楼这意味着"我在 3 号装卸口"，在 B 楼意味着"给我 3 号电梯"。
**你拿着 A 楼的单子去 B 楼填，填出来的是能通过的、但意思完全不同的东西。**

### 回到代码

核心把 `dmas` 切成 `struct of_phandle_args` 之后**不做任何解释**，
直接交给控制器驱动的 `of_dma_xlate` 回调
（`drivers/dma/of-dma.c:292`）。所以答案永远在那个回调里。

两个真实的 arm64 例子（都写死 `#dma-cells = <1>`）：

| 控制器 | 那个 `1` 是 | 证据 |
|---|---|---|
| ARM pl330 | **外设请求线号** | `of_dma_pl330_xlate`（`drivers/dma/pl330.c:2142-2160`）把 `args[0]` 当索引去查 `pl330->peripherals[]`（`:2155`/`:2159`）；绑定 `arm,pl330.yaml` 也写着 "Contains the DMA request number for the consumer" |
| MediaTek UART APDMA | **通道号** | 用的是 `of_dma_xlate_by_chan_id`（`of-dma.c:351-370`），按 `channels` 链表的序号取；绑定 `mediatek,uart-dma.yaml` 写着 "The first cell specifies the UART APDMA channel number" |

第三种是 `of_dma_simple_xlate`（`of-dma.c:321-335`），那个数字被
原封不动当成 filter 函数的 `void *param`（`:333-334` 传的是
`&dma_spec->args[0]`，取地址）—— **连"是个号码"都不保证。**

### 这个类比什么时候会误导你？

写字楼填错单子，前台会当面驳回。**DTS 写错这个数字，
一层报错都没有。**

pl330 的越界检查是这样的：

```c
/* pl330.c:2156 */ if (chan_id >= pl330->num_peripherals)
/* pl330.c:2157 */         return NULL;
```

三个 `return NULL`（`:2149`、`:2152`、`:2157`）**全是静默的**。
NULL 一路回到 `of-dma.c`，被覆盖成 `-EPROBE_DEFER`。

**结论：抄 DTS 之前必须先读控制器的绑定文档，
确认这一个 cell 是什么。** 光看别人的 DTS 是看不出来的。

---

## §4 为什么 `prep → submit → issue_pending` 要分三步？

### 先举个例子

一次 slave 传输在代码里长这样：

```c
desc = dmaengine_prep_slave_sg(chan, sgl, n, dir, flags);
desc->callback = my_done;
cookie = dmaengine_submit(desc);
dma_async_issue_pending(chan);
```

四行才搬一次数据，看着很啰嗦。为什么不是一个
`dma_transfer(chan, src, dst, len)` 完事？

因为它是**餐馆点菜**的形状：

1. **prep = 写菜单**。你告诉服务员要什么，服务员写在纸上，
   **厨房还不知道**。这时候你还能在纸上加备注（"少放辣"）。
2. **submit = 把单子交给服务员**。单子进了待下单的队列，
   你拿到一个**取餐号**。厨房还是不知道。
3. **issue_pending = 服务员按铃通知后厨**。
   这一下**把队列里所有攒着的单子一起送进厨房**。

分三步的价值全在第 3 步：**你可以先点五道菜，最后按一次铃。**
硬件只被打扰一次，而不是五次。

### 回到代码

- `prep` 阶段返回的 `struct dma_async_tx_descriptor`
  （`include/linux/dmaengine.h:603` 那个结构）里，`cookie` 字段
  此时是 `-EBUSY`（`:582` 的 kerneldoc 明说 "set to -EBUSY if
  the tx has been submitted"之前）。**"写在纸上，还没交"这个状态
  是显式表示的。** `callback` 就是在这个窗口里填的。
- `submit` 实际上是调驱动自己的 `tx_submit` 回调
  （`dmaengine_submit`，`include/linux/dmaengine.h:1212`，
  转手调 `desc->tx_submit(desc)`）。用 virt-dma 的驱动
  这里指向 `vchan_tx_submit`（`drivers/dma/virt-dma.h:63` 赋值，
  实现在 `drivers/dma/virt-dma.c:19` 起），它做的事就是分配 cookie
  并把描述符挪到 `desc_submitted` 链表（`virt-dma.c:32` 那条
  `dev_dbg` 打的就是这一步）。
- `issue_pending`（`include/linux/dmaengine.h:1418`）调
  `device_issue_pending`。virt-dma 的实现是
  `vchan_issue_pending`（`drivers/dma/virt-dma.h:82-86`），
  一行 `list_splice_tail_init(&vc->desc_submitted, &vc->desc_issued)`
  （`:84`）—— **"按铃"就是把整条链表挪个位置**。

### 这个类比什么时候会误导你？

点菜可以只点不吃 —— **DMA 不行，漏了第 3 步就永远不动。**

而且症状极难认：`prep` 成功了、`submit` 返回了一个正常的 cookie、
没有任何错误码、没有任何日志（`03-internals.md` Part A 里
这条路径上核心一句话都不打）。你只会看到"传输超时"。

更隐蔽的一条：`dmaengine.c:1224` 那个
`"Device issue_pending is not defined"` 检查是在**控制器注册期**
做的，保证的是"驱动实现了这个回调"，**不保证消费者调了它**。
两件事。

---

## §5 cookie 为什么是个整数，不是指针？

### 先举个例子

拿到 `cookie = dmaengine_submit(desc)` 之后，查状态用
`dma_async_is_tx_complete(chan, cookie, ...)`。
为什么不直接拿 `desc` 指针去问？

因为 cookie 是**银行的排队号**，而 `desc` 是**柜台前的那张单子**。

单子办完就被收走、碎掉、回收再用了。
**号码却可以留在你手里** —— 你回头问"我 47 号办完了吗"，
柜员看一眼"当前已办到 52 号"就能回答你，
根本不需要去翻那张已经不存在的单子。

### 回到代码

cookie 就是个 `s32`：

```c
/* include/linux/dmaengine.h:22 */ typedef s32 dma_cookie_t;
/* include/linux/dmaengine.h:23 */ #define DMA_MIN_COOKIE	1
```

每条通道自己维护一对水位（`include/linux/dmaengine.h:331-332`）：
`chan->cookie`（发出去的最大号）和 `chan->completed_cookie`
（已完成的最大号）。分配就是加一（`drivers/dma/dmaengine.h:34-37`），
回绕时钳到 `DMA_MIN_COOKIE`（`:35-36`）。

判完成的逻辑是两个整数比大小
（`dma_async_is_complete`，`include/linux/dmaengine.h:1457-1468`），
**完全不碰描述符**。

而描述符确实会被回收：`dma_cookie_complete`
（`drivers/dma/dmaengine.h:52-57`）在把 `completed_cookie` 前移之后，
**立刻把 `tx->cookie` 清零**（`:56`），注释（`:47`）写明
这是"防止意外重复完成"。virt-dma 更直接 —— 描述符被挪回
`desc_allocated` 链表复用（`drivers/dma/virt-dma.h:120`）。

所以：**`submit` 返回之后，那个 `desc` 指针就不该再碰了。**
唯一还有效的身份是 cookie。

### 这个类比什么时候会误导你？

排队号"52 号已办完"隐含"1 到 51 号也都办完了"。
DMA 的 cookie **在同一条通道上**确实是这个性质
（`dma_async_is_complete` 的判据就是 `cookie <= last_complete`）。

但**跨通道完全没有意义**：两条通道各自从 1 开始编号。
拿 dma0chan0 的 cookie 去问 dma0chan1，答案是垃圾且不会报错。

还有一条：`dma_cookie_t` 是 `s32`，**负数是错误码**
（`include/linux/dmaengine.h:20` 的 kerneldoc：">0 是 cookie，
<0 是错误码"），所以 `submit` 之后要用
`dma_submit_error(cookie)`（`:25-28`）判一下，
不能直接当号码用。

---

## §6 为什么 memcpy 通道能匿名要，slave 通道必须报名字？

### 先举个例子

两种要资源的方式：

- **"给我一辆共享单车"** —— 哪辆都行，能骑就好。
- **"给我 3 号仓库到装卸口的那条传送带"** —— 只有那一条能用，
  别的都到不了地方。

memcpy 是前者：内存搬到内存，**哪条通道都一样**。
slave 是后者：通道背后连着**具体那根外设请求线**，
换一条就接不到你的 UART 上。

### 回到代码

两条 API 路径是分开的：

| 用途 | API | 怎么选通道 |
|---|---|---|
| memcpy / xor / pq | `dma_request_channel(mask, filter, param)` | 按**能力位** mask 在全局找一条空闲的 |
| slave | `dma_request_chan(dev, name)` | 按 DTS 的 `dma-names` 精确指定（`06-devicetree.md` §3） |

而"匿名申请拿不到 slave 通道"这件事**是机制性的，不是习惯**：

```c
/* dmaengine.c:273 */ dma_cap_clear(DMA_INTERRUPT, dma_cap_mask_all);
/* dmaengine.c:274 */ dma_cap_clear(DMA_PRIVATE,   dma_cap_mask_all);
/* dmaengine.c:275 */ dma_cap_clear(DMA_SLAVE,     dma_cap_mask_all);
```

`dma_channel_table_init` 在 `subsys_initcall` 期就把 `DMA_SLAVE`
从全局能力 mask 里**抹掉了**（`:275`）。匿名路径查的是这张表，
所以它永远看不见 slave 通道。

这条机制有个很实际的后果：**`dmatest` 测不了 slave。**
`dmatest` 走的正是 `dma_request_channel`（`drivers/dma/dmatest.c:1077`），
一共只申请四种能力（`:1111`–`:1114`：MEMCPY / MEMSET / XOR / PQ）。
官方文档 `Documentation/driver-api/dmaengine/dmatest.rst:19-22`
的 note 也是这四项。

**arm64 上绝大多数 DMA 用途（UART/SPI/I2C/MMC/音频）都是 slave。
所以 `dmatest` 全绿只证明 memcpy 路径没问题。** 详见 `07-debug.md` §5.1。

### 这个类比什么时候会误导你？

"共享单车哪辆都行"暗示 memcpy 通道之间**完全等价**。不是的。

匿名申请那条路上有 filter 函数（`dma_filter_match`，
`dmaengine.c:786-804`），驱动可以在这里挑挑拣拣；
还有 per-cpu 的通道表做负载分摊（`dma_channel_table_init`
建的就是这张表）。所以"随便给一条"背后其实有策略，
只是消费者不需要关心。

反过来，"传送带只有那一条"也略过头了：pl330 那类控制器上，
**物理通道是动态分配的**，DTS 里指定的是"外设请求线号"
（§3），不是通道。同一条物理通道在不同时刻可以服务不同外设。
真正被钉死的是"请求线 ↔ 外设"这一层，不是"通道 ↔ 外设"。

---

## §7 virt-dma 那五条链表在干什么？

### 先举个例子

翻开任何一个 arm64 DMA 控制器驱动，都会看到
`vchan_tx_prep`、`vchan_cookie_complete`、`desc_issued` 这些东西。
`drivers/dma/virt-dma.c` 只有 142 行，`virt-dma.h` 227 行，
但不懂它就读不下去任何一个驱动。

它解决的问题是：**硬件通道数比外设数少得多。**

想象一个只有两个灶台的小馆子，但同时有八桌客人在点菜。
后厨的做法是：

- 前台给**每桌**开一本单子（这叫"虚拟通道"）
- 灶台只有两个，谁的菜先上由后厨调度（这是"物理通道"）
- 每本单子里的菜按状态分堆：还没写完的 / 已交前台的 /
  已进厨房的 / 已出锅的 / 客人取消了的

**五条链表就是这五堆。**

### 回到代码

`struct virt_dma_chan`（`drivers/dma/virt-dma.h:22-37`）里五条链表：

| 链表 | 字段行 | 描述符什么时候在这里 |
|---|---|---|
| `desc_allocated` | `:30` | `prep` 完成、还没 `submit`（`vchan_tx_prep` 在 `:70` 放进来） |
| `desc_submitted` | `:31` | `submit` 之后、`issue_pending` 之前（`vchan_tx_submit`，`virt-dma.c:19` 起） |
| `desc_issued` | `:32` | `issue_pending` 之后，等硬件（`vchan_issue_pending` 的 `:84` 整条挪过来） |
| `desc_completed` | `:33` | 硬件完成、回调还没跑（`vchan_cookie_complete` 的 `:103`） |
| `desc_terminated` | `:34` | 被 terminate 掉的（`vchan_terminate_vdesc` 的 `:149`） |

**§4 那三步和这五条链表是一一对应的** ——
`prep` → allocated，`submit` → submitted，`issue_pending` → issued。
"分三步"的物理体现就是"描述符在链表间挪三次"。

回调不在中断里跑：`vchan_cookie_complete` 只是挪链表 + 调度 tasklet，
真正跑回调的是 `vchan_complete`（`virt-dma.c:83-110`，
`:91` 把 `desc_completed` 整条摘走再逐个回调）。
初始化是 `vchan_init`（`virt-dma.c:123-138`），
最后一行 `:137` 把通道挂进 `dmadev->channels`。

### 这个类比什么时候会误导你？

"每桌一本单子"暗示虚拟通道数**可以随便多**。
实际上虚拟通道数是驱动 probe 时定死的，
通常就等于"外设请求线数"或"硬件通道数"，
DTS 里那个 `dma-requests`/`dma-channels` 常常就是它
（`06-devicetree.md` §6 讲了谁真的读这些属性）。

另一处：`desc_terminated` 这一堆**不是"取消的菜扔掉"**。
它存在的唯一目的是给 `vchan_synchronize`
（`drivers/dma/virt-dma.h:211-225`，`:220` 把它整条摘走）
留一个安全释放的时机 —— 这正好是下一节的题目。

---

## §8 `terminate_async` + `synchronize` 为什么不合成一个？

### 先举个例子

这是本篇**代价最大**的一条：想错了会踩内存。

`dmaengine_terminate_async(chan)` 返回之后，DMA 停了吗？
**没有。**

类比：**踩刹车不等于停稳。**

你踩下刹车踏板的那一刻，"制动"这个命令已经下达了 ——
但车还在滑行。这时候你打开车门跳下去，会受伤。
必须等到"车真的不动了"才能开门。

- `terminate_async` = 踩刹车（命令下达）
- `synchronize` = 等到车真的停稳
- 释放 buffer = 开门下车

### 回到代码

kerneldoc 说得很直白（`include/linux/dmaengine.h:1112-1121`）：

> It is not guaranteed though that the transfer for the active
> descriptor has stopped when the function returns. Furthermore it is
> possible the complete callback of a submitted transfer is still
> running when this function returns.
>
> `dmaengine_synchronize()` needs to be called before it is safe to free
> any memory that is accessed by previously submitted descriptors ...

两个后果：**硬件可能还在写你的 buffer**，而且
**回调可能还在另一个 CPU 上跑**。

`dmaengine_synchronize`（`:1155-1161`）转手调驱动的
`device_synchronize` 回调（字段在 `:934`，kerneldoc `:835`）。
用 virt-dma 的驱动一般指向 `vchan_synchronize`
（`drivers/dma/virt-dma.h:211-225`）—— 它做的事是
`tasklet_kill` + 释放 `desc_terminated` 那一堆（`:220`）。

`dmaengine_terminate_sync`（`:1177-1187`）就是这两步的封装：
先 `terminate_async`（`:1181`），成功了再 `synchronize`（`:1185`）。
**能用它就用它。** 分开用只有两种情况：在原子上下文里，
或者在同一条通道的完成回调里（kerneldoc `:1123-1124` 明说了）。

### 这个类比什么时候会误导你？

刹车总是有效的。**这两个函数在驱动没实现回调时会静默降级。**

三条要盯住：

1. `dmaengine_terminate_all`（`:1100-1106`）没回调时返回
   **`-ENOSYS`**（`:1105`）
2. `dmaengine_terminate_async`（`:1129-1135`）没回调时返回
   **`-EINVAL`**（`:1134`）
   —— 同样的条件、两个不同的错误码，已记入 `99-evidence.md` §6
3. `dmaengine_synchronize`（`:1155-1161`）在
   `device_synchronize` 为 NULL 时**什么都不做，也不报错**
   （`:1159` 的 `if` 直接落空）。于是
   `dmaengine_terminate_sync` 悄悄退化成 `terminate_async` ——
   **名字里的 sync 是假的。**

而 `device_synchronize` 是**可选**回调。本树里就有活例子：
`drivers/dma/mediatek/mtk-uart-apdma.c` 没有设置它。
所以在那个控制器上调 `dmaengine_terminate_sync` 之后立刻
`dma_free_coherent`，是一个真实存在的 use-after-free 窗口。

**实践结论：调完 `terminate_sync` 别急着释放 —— 先确认
你的控制器驱动实现了 `device_synchronize`。**
一条 grep 就能查：`grep -n device_synchronize <你的驱动>.c`。

---

## §9 `dma_slave_config` 为什么由外设驱动填？

### 先举个例子

`dmaengine_slave_config(chan, &cfg)`（`include/linux/dmaengine.h:946`）
要填的东西不少（`struct dma_slave_config`，`:437-450`）：
方向、外设侧的物理地址、总线宽度、burst 长度……

直觉上会觉得别扭：**"DMA 控制器难道不知道 UART 的寄存器在哪吗？"**

不知道，而且不该知道。类比：

**搬家公司不知道你家在哪。** 它有卡车、有工人、有路线规划能力，
但"从哪搬到哪、几楼、电梯多大"必须**你告诉它**。
搬家公司要是内置一张"全城住户地址表"，
每来一个新住户就得改一次公司系统 —— 荒谬。

DMA 控制器是搬家公司，UART 驱动是住户。
**UART 驱动知道自己的 FIFO 在哪个物理地址、多宽、
一次能吞几个字节，所以由它来说。**

### 回到代码

`struct dma_slave_config`（`include/linux/dmaengine.h:437-450`）的字段
分成三组：

| 组 | 字段 | 谁知道 |
|---|---|---|
| 方向 | `direction`（`:438`） | 外设驱动（这条通道是收还是发） |
| 外设侧地址 | `src_addr`（`:439`）、`dst_addr`（`:440`） | **外设驱动** —— 是**物理地址**，不是虚拟地址 |
| 总线特性 | `src_addr_width`/`dst_addr_width`（`:441-442`）、`src_maxburst`/`dst_maxburst`（`:443-444`） | 外设驱动（看自己的 FIFO 深度） |
| 流控 | `device_fc`（`:447`） | 外设驱动，但含义由控制器驱动解释（见本节末） |

注意 `src_addr` / `dst_addr` 的类型是 `phys_addr_t` ——
填 `ioremap` 之后的虚拟地址是一个很常见的错误，
而且**核心不校验**，会一路传到硬件寄存器。

`:448-449` 还有 `peripheral_config` / `peripheral_size` 两个
"驱动私有配置"的口子 —— 这是给实在装不进标准字段的东西留的后门。

内存那一侧的地址**不在这个结构里** —— 它随每次传输走，
在 `dmaengine_prep_slave_sg`（`:977`）的 scatterlist 里。
**配置是一次性的（换个说法：稳定的），
地址是每次都变的**，所以分成两个 API。

### 这个类比什么时候会误导你？

搬家公司来之前你可以一直改地址。**`dma_slave_config` 不是。**

`dmaengine_slave_config` 只是转手调 `device_config` 回调
（`:949-950`），**驱动通常立刻把值写进硬件寄存器**；
驱动没实现 `device_config` 时它返回 `-ENOSYS`（`:952`）。
如果一条通道被两个用途轮流用（少见但存在），
后一次 config 会覆盖前一次，而**已经 submit 但还没 issue 的
描述符可能用的是旧配置** —— 核心没有任何保护。

另一处：`device_fc`（`:447`）这个 bool 的含义
（谁做流控：DMA 还是外设）**完全取决于控制器驱动怎么解释**，
和 `#dma-cells` 那个数字一样（§3）。填了不报错，
但可能什么效果都没有。

---

## §10 音频为什么用 cyclic，不用一次次 submit？

### 先举个例子

前面九节讲的都是"一次传输"。但音频不是一次传输 ——
它是**永不停止的一条流**。48kHz 立体声 16 位，一秒 192KB，
你打算每秒 submit 多少次？

如果按 §4 那套做法（prep → submit → issue_pending → 回调 → 再 prep），
每次传输结束都要**在中断回调里重新组织下一次传输**。
一旦某次调度晚了几十微秒，硬件就**没有数据可搬** —— 你听到的是爆音。

cyclic 是另一种形状。类比：

**普通传输像叫外卖，cyclic 像订牛奶。**

叫外卖：每次饿了下一单，等送到，吃完，再下一单。
中间任何一环慢了，你就得饿着。

订牛奶：你和奶站说好"每天早上放一瓶到门口的箱子里，一直放，
直到我说停"。奶站**不需要你每天下单**。
你的任务从"下单"变成了"**在牛奶变质前把它拿走**"。

这个转变是关键：**cyclic 把消费者的责任从"及时提交"
改成了"及时取走"。** 前者有硬实时要求，后者只要求平均速度够。

### 回到代码

入口是 `dmaengine_prep_dma_cyclic`
（`include/linux/dmaengine.h:1003-1013`）：

```c
/* :1003 */ static inline struct dma_async_tx_descriptor *dmaengine_prep_dma_cyclic(
/* :1004 */         struct dma_chan *chan, dma_addr_t buf_addr, size_t buf_len,
/* :1005 */         size_t period_len, enum dma_transfer_direction dir,
/* :1006 */         unsigned long flags)
```

和 §4 那条 `dmaengine_prep_slave_sg`（`:977`）比，多了一个
**`period_len`** —— 这就是"每天一瓶"里的"一瓶"。
`buf_len` 是那个门口的箱子（环形缓冲区）总大小，
`period_len` 是每完成多少字节通知你一次。**通常 `buf_len` 是
`period_len` 的整数倍**，箱子里能放 N 瓶。

**submit 一次，硬件跑到世界末日。** 没有"下一次 prep"——
描述符不会被回收，硬件搬到缓冲区末尾就自动绕回开头，
每过一个 period 触发一次回调。停下来的唯一办法是
`dmaengine_terminate_*`（§8）。

内核自己也这么说 —— `device_prep_dma_cyclic` 字段的 kerneldoc
（`include/linux/dmaengine.h:820`）原文就是
"prepare a cyclic dma operation **suitable for audio**"。
能力位是 `DMA_CYCLIC`（`:63`），注册期第 8 道门检查它
（`drivers/dma/dmaengine.c:1202-1205`）：声明了 `DMA_CYCLIC`
却没实现 `device_prep_dma_cyclic` 会被 `-EIO` 拒掉。

谁在用：`sound/core/pcm_dmaengine.c` 是 ALSA/ASoC 的通用 dmaengine PCM 层，
所有走 dmaengine 的声卡都经过它。**不止音频** ——
`drivers/tty/serial/imx.c`、`stm32-usart.c`、`fsl_lpuart.c`、
`atmel_serial.c` 都用 cyclic 做**串口接收**（同样是"不知道什么时候来、
但不能丢"的场景），`drivers/iio/adc/stm32-adc.c` 用它做连续采样。

### 这个类比什么时候会误导你？

"及时取走就行"听起来比"及时提交"轻松。**但它把一个新问题带进来了：
你怎么知道现在能取到哪儿？**

普通传输里"完成"是个布尔量。cyclic 里硬件**永远没完成**，
所以你必须问"它现在搬到哪了"—— 这就是 `residue`
（§7 提到的 `dma_tx_state`）。

**于是 cyclic 的正确性直接依赖 residue 的精度**，
而 residue 是三种粒度里的一种
（`include/linux/dmaengine.h:454`/`:458`/`:466`）：

| 粒度 | cyclic 下的实际效果 |
|---|---|
| `DESCRIPTOR`（`:454`） | **residue 恒为 0** —— kerneldoc 原话。你只能靠 period 回调计数，拿不到"period 内部搬到哪了" |
| `SEGMENT`（`:458`） | 每个 period 更新一次。够用于"哪些 period 已满"，不够用于低延迟对齐 |
| `BURST`（`:466`） | 硬件有进度寄存器，能问到字节级 |

所以"音频延迟调不下去"和"`residue` 读出来不动"往往是同一个问题：
控制器只有 `SEGMENT` 粒度，或者驱动压根没实现 residue
（查法见 `07-debug.md` §6.1）。

还有一处：订牛奶的类比暗示"箱子满了会有人提醒你"。
**没有。** 硬件绕回去就直接**覆盖你还没读的数据**，
不报错、不停下、不打日志。cyclic 的 overrun 是**静默丢数据**，
不是错误码。

---

## §11 阅读下一篇

| 你接下来想知道 | 去哪 |
|---|---|
| 这些概念对应的完整 API 列表 | `01-api.md` |
| `dma_chan` / `dma_device` / 描述符的字段全貌 | `02-datastructures.md` |
| 注册、申请、传输的逐段代码剖析 | `03-internals.md` |
| virt-dma、metadata、router 这些可选层 | `04-extensions.md` |
| 照着写一个消费者 / 控制器驱动 | `05-usage.md` |
| DTS 具体怎么写、写错怎么查 | `06-devicetree.md` |
| 上面提到的每个日志/开关在哪看 | `07-debug.md` |
| 本篇每条论断的出处、以及已发现的 16 条自陈问题 | `99-evidence.md` |
