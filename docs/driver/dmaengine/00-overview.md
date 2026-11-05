---
title: 总览
---

# 00 总览：dmaengine 子系统（复习入口）

> Linux 6.1.y / arm64-only。
> **这一篇的定位是"复习入口"**：隔几个月回来只读这一篇，就能把整个
> 子系统的骨架恢复出来；需要细节再按 §8 的导读表往后翻。
> 所有行号级的取证在 `99-evidence.md`。

---

## §1 这个子系统解决什么问题

### §1.1 一句话

**dmaengine 是内核里"把搬数据这件事外包给专用硬件"的统一抽象层。**
它让 UART / SPI / I2C / MMC / 音频这些外设驱动，
不必知道自己板子上装的是哪家的 DMA 控制器，
就能说出"帮我把这 4KB 从内存搬到我的 FIFO 去"。

### §1.2 它不是什么（先排除三个常见误解）

| 误解 | 事实 |
|---|---|
| "dmaengine 就是 `dma_alloc_coherent` 那一套" | **完全是两回事。** `dma_alloc_coherent` / `dma_map_single` 属于 **DMA API**（`Documentation/core-api/dma-api.rst`，代码在 `kernel/dma/`），管的是"内存的地址翻译和 cache 一致性"。dmaengine 管的是"驱动一个 DMA 控制器芯片"。**用 dmaengine 的驱动几乎一定同时要用 DMA API**，但反过来不成立 —— 网卡内置 DMA 引擎，只用 DMA API，不用 dmaengine |
| "DMA 控制器是外设的父设备" | 不是。二者在设备模型里平级，都是 platform 设备，只由一条 sysfs 符号链接关联（`08-feynman.md` §1） |
| "dmaengine 有自己的总线，会自动 probe" | 没有 `bus_type`，只有一个 class（`drivers/dma/dmaengine.c:240-244`）。服务必须由消费者主动申请 |

### §1.3 子系统定性

| 维度 | 结论 |
|---|---|
| 类型 | **框架型**（无 `bus_type`，通过 API 被其他驱动消费） |
| 核心代码规模 | `dmaengine.c` 1655 行 + `of-dma.c` 371 行 + `virt-dma.c` 142 行 + `include/linux/dmaengine.h` 1637 行 |
| 驱动规模 | `drivers/dma/` 递归 147 个 `.c` / 144616 行 / 18 个厂商子目录（顶层 63 个 `.c` / 76530 行） |
| 本树补丁影响 | **核心零改动** —— 6.1.158 的补丁集没有碰 dmaengine 核心（`99-evidence.md` §1） |
| 可观测性 | **很薄**：**核心零 tracepoint**（全树唯一带 tracepoint 的 DMA 驱动是 arm32 的 tegra20-apb-dma），1 个 debugfs 文件，3 个只读 sysfs 属性（`07-debug.md` §0） |
| arm64 相关性最高的部分 | slave DMA（`dma_request_chan` + `prep_slave_sg`）、DT 绑定、virt-dma |
| arm64 上基本用不到的部分 | async_tx / RAID offload、router / dmamux、ACPI 路径（已裁，见 `99-evidence.md` §2.7） |

---

## §2 硬件背景：DMA 控制器到底是个什么东西

> 本节是理解后面所有软件抽象的前提。
> ⚠️ **本节的硬件描述属于三等证据** —— 来自公开架构文档和绑定文档，
> 不是从本树代码推出来的。凡是能对上代码的地方都给了行号；
> 给不出行号的地方就是"硬件手册级"的说法，见 `99-evidence.md` §5。

### §2.1 为什么需要一块专门搬数据的芯片

考虑一个最普通的场景：串口以 115200 波特率收数据。

**没有 DMA 时**，CPU 要这样干：每来一个字节（或 FIFO 半满），
串口拉一次中断，CPU 停下手里的活、保存现场、跳进中断处理函数、
从串口 FIFO 读几个字节写进内存、恢复现场、回去继续。

代价分三块：
1. **中断本身的开销**：进出中断的现场保存/恢复，在 arm64 上是几十到几百个周期
2. **cache 污染**：搬数据把不相关的数据挤出 L1/L2
3. **CPU 完全被占住**：搬 1MB 就是 1MB 的 load/store，CPU 这段时间干不了别的

**有 DMA 时**：CPU 只做两件事 —— 开头告诉 DMA 控制器
"从这个地址搬 N 字节到那个地址"，结尾收一个"搬完了"的中断。
中间那段时间 CPU 可以去跑别的线程。

所以 DMA 的价值**不是"更快"，而是"解放 CPU"**。
一次 4 字节的传输用 DMA 反而更慢（配置寄存器的开销 > 直接 load/store）。
这就是为什么几乎所有 arm64 串口/SPI 驱动都有一个阈值：
小包走 PIO，大包才走 DMA。

### §2.2 一次 slave 传输在硬件上发生什么

这是最需要在脑子里建立的一张图。以"串口收数据"为例：

```
   ┌──────────┐          ┌──────────────────┐          ┌─────────┐
   │   UART   │          │  DMA 控制器      │          │  DRAM   │
   │          │          │                  │          │         │
   │  RX FIFO │          │  ┌────────────┐  │          │         │
   │   ▒▒▒▒   │          │  │ 通道 0     │  │          │  ┌────┐ │
   │    │     │          │  │ 源: UART   │  │          │  │ 缓 │ │
   │    │     │  ① 请求线 │  │ 目: 0x8000 │  │  ③ AXI  │  │ 冲 │ │
   │    └─────┼─────────►│  │ 长: 4096   │  ├─────────►│  │ 区 │ │
   │          │  (DRQ)   │  └────────────┘  │  写事务  │  └────┘ │
   │          │          │        │         │          │         │
   │          │◄─────────┼────────┘         │          │         │
   │          │  ② 应答   │                  │          │         │
   └──────────┘  (DACK)  └────────┬─────────┘          └─────────┘
                                  │ ④ 传完了，拉中断
                                  ▼
                              ┌───────┐
                              │  GIC  │──► CPU
                              └───────┘
```

四个动作，逐个说：

**① 请求线（DMA request / DRQ）**：UART 的 RX FIFO 攒到一定量，
就把一根**物理信号线**拉起来。这根线是**硬连线**的 ——
SoC 设计时就把 UART3 的 RX 请求接到 DMA 控制器的第 N 号请求输入上。
**这个 N 就是 DTS 里 `dmas = <&dmac 10>` 那个数字**（对 pl330 类控制器）。
它是**焊死的**，软件改不了，只能"如实登记"。这是 `06-devicetree.md` §1.2
和 `08-feynman.md` §3 反复强调的那件事。

**② 应答（DACK）**：DMA 控制器搬完一个 burst，回一个应答，
UART 才继续往 FIFO 里放/取。这就是**硬件握手**。

**③ 总线事务**：arm64 SoC 上一般是 AXI/AHB（ARM AMBA 总线族）。
DMA 控制器作为**总线主设备（bus master）**发起读写。
这是它和 CPU 的地位差别 —— 它不是外设，它和 CPU 一样能主动访存。
这一点是后面所有 cache 一致性问题的根源（§2.7）。

**④ 完成中断**：整笔传输（或每个周期）结束时拉一根中断线到 GIC。
DTS 里 DMA 控制器节点的 `interrupts` 属性就是它。

### §2.3 burst：为什么要有 `maxburst` 这个参数

DMA 不是一个字节一个字节搬的 —— 那样总线事务的开销太大。
它一次搬一小簇，叫 **burst**。

打个比方：**搬砖不是一块一块搬，是一车一车搬。**
`maxburst` 就是"一车最多装几块"。

- 车太小 → 跑的趟数多 → 总线事务多 → 效率低
- 车太大 → **外设的 FIFO 装不下** → 溢出丢数据

所以 `maxburst` 的正确值是由**外设的 FIFO 深度**决定的，
不是由 DMA 控制器决定的。这就是为什么 `dma_slave_config` 里的
`src_maxburst` / `dst_maxburst`（`include/linux/dmaengine.h:443-444`）
**必须由外设驱动填**（`08-feynman.md` §9）。

配套的还有**总线宽度**：一次搬 1/2/4/8… 字节。
内核把它枚举成了 `enum dma_slave_buswidth`
（`include/linux/dmaengine.h:373-384`），从
`DMA_SLAVE_BUSWIDTH_1_BYTE`（`:375`）一直到
`DMA_SLAVE_BUSWIDTH_128_BYTES`（`:383`）。
注意里面有个 `DMA_SLAVE_BUSWIDTH_3_BYTES = 3`（`:377`）——
非 2 的幂，是给某些音频格式（24 位采样）留的。

### §2.4 scatter-gather：为什么内存侧是一张链表

用户态给你的一个"连续"缓冲区，在物理内存里几乎**从来不是连续的** ——
它由分散的物理页拼成。DMA 控制器看到的是物理地址（或 IOVA），
所以必须能处理"搬到 A 页，再搬到 B 页，再搬到 C 页"。

两种硬件能力：

| 硬件能力 | 表现 | 软件后果 |
|---|---|---|
| 支持 scatter-gather | 控制器能读一张**描述符链表**，自己按链表逐段搬 | 一次 `prep` 一次中断 |
| 不支持 | 每段都要 CPU 重新配一次寄存器 | 驱动只能拆成多笔，中断多得多 |

内核侧对应的是 `dmaengine_prep_slave_sg`
（`include/linux/dmaengine.h:977`）—— 传进去的就是一个
`struct scatterlist` 数组。**控制器驱动负责把 scatterlist 翻译成
自己硬件认识的描述符格式**，这个翻译过程是各驱动差异最大的地方。

所以内核里"描述符"这个词有两个层次：
- **硬件描述符**：DMA 控制器硬件读的那个内存结构（各家格式不同）
- **软件描述符**：`struct dma_async_tx_descriptor`
  （`include/linux/dmaengine.h:603` 那个结构）—— 内核的统一句柄

`03-internals.md` 里讲的都是后者。

### §2.5 cyclic 模式：为什么音频要它

普通传输是"搬完 N 字节就停，拉中断"。
音频不能停 —— 声音必须连续输出，一停就"卡吱"。

所以有 **cyclic（循环）模式**：给控制器一个环形缓冲区，
它搬到尾就自动绕回头，**永不结束**，每搬完一个"周期"（period）
拉一次中断通知你"这块用完了，可以填新数据了"。

类比：**不是"送一趟货就下班"，而是"绕着环线一直跑"，
每到一个站点摇一下铃。**

内核侧是 `dmaengine_prep_dma_cyclic`
（`include/linux/dmaengine.h:1003`），能力位是 `DMA_CYCLIC`
（`enum dma_transaction_type` 里，`include/linux/dmaengine.h:51-70`）。

这也是 `07-debug.md` §5.1 那条结论的一部分：
**`dmatest` 测不了 cyclic**，而 arm64 上音频全靠它。

### §2.6 residue：传到一半时"还剩多少"

串口有个特殊需求：**收数据时不知道对方会发多少。**
你申请了一次 4096 字节的传输，对方只发了 17 字节就停了。
这时候你需要问："已经收了多少？"

这个"还剩多少没搬"就是 **residue**。它完全依赖硬件 ——
控制器必须有某种进度寄存器才能算出来。内核把硬件能力分成三档
（`enum dma_residue_granularity`，`include/linux/dmaengine.h:452` 起的 kerneldoc）：

| 档位 | 硬件有什么 | 能回答什么 |
|---|---|---|
| `DESCRIPTOR` | 只知道"这段完了没" | **residue 恒为 0**，问不出进度 |
| `SEGMENT` | 每段完成时有中断/标志位 | 精度 = 一段 |
| `BURST` | 有当前地址或已传计数寄存器 | 精度 = 一个 burst |

消费者驱动用 `dma_get_slave_caps`（`drivers/dma/dmaengine.c:571-617`）
查这个能力。查不到就不能依赖 residue —— 8250 串口驱动就有这条判断。

### §2.7 arm64 特有之一：cache 一致性

这是 arm64 上最容易出灵异问题的地方。

**问题**：CPU 有 cache，DMA 控制器（一般）没有，而且它直接访问 DRAM。

所以：
- **CPU 写了数据让 DMA 去发** → 数据可能还在 CPU 的 cache 里，
  DRAM 里是旧的 → **DMA 发出去的是垃圾**
- **DMA 收了数据放进 DRAM** → CPU 的 cache 里可能还留着这块地址的旧内容
  → **CPU 读到的是旧数据**

类比：**你在便签上改了地址（cache），但没抄到档案柜（DRAM）里。
快递员（DMA）只看档案柜。**

解决办法有两条，都**不在 dmaengine 里**，而在 DMA API 里：

| 办法 | 做什么 | 谁负责 |
|---|---|---|
| 显式维护 | 传输前后调 `dma_map_single` / `dma_sync_single_for_*` 刷/无效 cache | 消费者驱动 |
| 硬件一致 | SoC 的互连支持硬件监听（cache coherent interconnect），不需要软件刷 | DTS 里写 `dma-coherent` |

`dma-coherent` 这个属性在本树 `arch/arm64/boot/dts/` 下
出现在 **45 个文件**里（可复现：
`grep -rl 'dma-coherent' arch/arm64/boot/dts/ | wc -l`）。
它由**通用 OF 层**读取（不是 dmaengine），
影响的是后续 DMA API 走不走刷 cache 的路径。

**这是一个静默故障源**：`dma-coherent` 写错（该写没写、
或不该写却写了）不会有任何报错，症状是**偶发的数据错误**，
和 DMA 配置完全无关的样子。查它的第一步是
`cat /proc/device-tree/<节点>/dma-coherent`（存在即为真，
是个空属性）。

### §2.8 arm64 特有之二：SMMU / IOMMU

arm64 服务器和部分 SoC 上，DMA 控制器发出的地址**不是物理地址**，
而要先过一遍 SMMU（ARM 的 IOMMU）翻译。

类比：**DMA 控制器手里拿的是"内部编号"，
门口的登记处（SMMU）把它换成真实门牌号。**

三个后果：

1. DTS 里 DMA 控制器节点要写 `iommus = <&smmu N>`
   （由 IOMMU 子系统读，不是 dmaengine）
2. **忘了写**：DMA 访存被 SMMU 拦下，症状是
   `dmesg` 里的 SMMU fault 而不是 DMA 超时
3. 地址翻译由 DMA API 透明处理 —— 消费者驱动拿到的
   `dma_addr_t` 已经是 IOVA，代码写法不变

`06-devicetree.md` §2.2 提到 `arm,pl330.yaml` 绑定里
就列了 `iommus` 这个属性。**dmaengine 核心不读它。**

### §2.9 arm64 特有之三：地址位宽

DMA 控制器能发多少位的地址，是硬件参数。常见的坑：
控制器只有 32 位地址线，但板子有 8GB 内存 ——
**高地址的内存 DMA 到不了**。

内核对付这个的办法在 DMA API 层（`dma_set_mask_and_coherent`），
但有些控制器把它做成了 DT 属性。真实例子：
`mediatek,dma-33bits`（由
`drivers/dma/mediatek/mtk-uart-apdma.c:496` 读取）——
33 位，比 32 多一位，是 MediaTek 的一个具体硬件档位。

这类属性是**厂商私有**的，核心完全不认识（`06-devicetree.md` §6）。

### §2.10 两种 DMA 形态：集中式 vs 外设内置

最后要分清一件事，否则会到处找不到 DMA 控制器：

| 形态 | 长什么样 | 用 dmaengine 吗 |
|---|---|---|
| **集中式 DMA 控制器** | SoC 上一块独立的 IP，DTS 里一个 `dma-controller@...` 节点，多个外设共享 | ✅ **这才是 dmaengine 管的** |
| **外设内置 DMA 引擎** | 网卡、GPU、NVMe 自带，寄存器在外设自己的地址空间里 | ❌ 不用。驱动自己写，只用 DMA API |

判断方法很简单：**DTS 里那个外设有没有 `dmas` 属性？**
有 → 用集中式，走 dmaengine。没有但明显在搬大量数据 → 内置引擎。

本树 `arch/arm64/boot/dts/` 下有 **89 个文件**包含
`#dma-cells`（即至少有一个 dmaengine 控制器节点），
其中 **17 个文件**用的是 `arm,pl330`。
（可复现：`grep -rl '#dma-cells' arch/arm64/boot/dts/ | wc -l`、
`grep -rl 'arm,pl330' arch/arm64/boot/dts/ | wc -l`）

### §2.11 硬件差异 → 软件后果 总表

**这张表是本节的收敛点。** 左边是硬件参数，右边是它在代码里的落点：

| 硬件事实 | 代码里的体现 | 细节在 |
|---|---|---|
| 请求线是焊死的 | DTS `dmas` 里那个数字；`of_dma_xlate` 回调 | `06` §1.2 / §4 |
| FIFO 深度决定 burst | `dma_slave_config.src_maxburst` / `dst_maxburst` | `08` §9 |
| 外设寄存器物理地址 | `dma_slave_config.src_addr` / `dst_addr`（`phys_addr_t`） | `08` §9 |
| 支不支持 scatter-gather | 驱动实现 `device_prep_slave_sg` 的方式 | `03` Part A |
| 支不支持 cyclic | 能力位 `DMA_CYCLIC` + `device_prep_dma_cyclic` | `01` §3 |
| 有没有进度寄存器 | `dma_get_slave_caps` 报的 `residue_granularity` | `02` §4 |
| 物理通道比外设少 | virt-dma 那一层（五条链表） | `08` §7 / `04` |
| cache 一致不一致 | DTS `dma-coherent`（**不是 dmaengine 读的**） | 本节 §2.7 |
| 地址位宽 | 厂商私有属性，如 `mediatek,dma-33bits` | `06` §6 |
| 中断线数量 | DTS `interrupts` 的项数；有的控制器一通道一根 | `06` §2.3 |

---

## §3 概念模型：五个词就够了

整个子系统的心智模型可以压缩成五个概念。**记住这五个词之间的关系，
比记住任何 API 都重要。**

```
     ┌────────────────────────────────────────────────────────────┐
     │  dma_device  ——  一个 DMA 控制器（一块硬件 IP）             │
     │                                                            │
     │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
     │   │  dma_chan 0  │  │  dma_chan 1  │  │  dma_chan N  │    │
     │   │              │  │              │  │              │    │
     │   │ 能力位 mask   │  │              │  │              │    │
     │   │ client_count │  │              │  │              │    │
     │   └──────┬───────┘  └──────────────┘  └──────────────┘    │
     └──────────┼─────────────────────────────────────────────────┘
                │  ① dma_request_chan(dev,"rx")  ← 消费者主动申请
                │
                │  ② dmaengine_slave_config(chan,&cfg)  ← 一次性配置
                │
                ▼
        ┌───────────────────────────────────────────┐
        │  dma_async_tx_descriptor  ——  一笔传输     │
        │                                            │
        │  ③ prep  → 生成（cookie 还是 -EBUSY）      │
        │  ④ submit → 入队，返回 cookie              │
        │  ⑤ issue_pending → 通知硬件                │
        │  ⑥ callback / tx_status(cookie) → 收尾     │
        └───────────────────────────────────────────┘
```

| 概念 | 是什么 | 一句话要点 |
|---|---|---|
| `dma_device` | **一个控制器** | 由控制器驱动 `dma_async_device_register` 注册进核心 |
| `dma_chan` | **一条通道**，控制器的一份"搬运能力" | **独占**：申请到就归你，直到 `dma_release_channel` |
| `dma_slave_config` | **通道的稳定配置**（外设地址、宽度、burst） | 由**外设驱动**填，配一次 |
| `dma_async_tx_descriptor` | **一笔具体传输** | 由 `prep` 系列函数生成，`submit` 之后就不该再碰 |
| `dma_cookie_t` | **一笔传输的收据**（一个 `s32`） | 描述符会被回收，cookie 是唯一还有效的身份 |

**最关键的一条关系**：`dma_device` / `dma_chan` **不是** device / driver
的关系，控制器和外设**不是**父子关系。二者只由一条 sysfs 符号链接关联。
这是本子系统最容易被类比错的地方，展开在 `08-feynman.md` §1。

---

## §4 数据结构速览

> 完整字段和结构关系图在 `02-datastructures.md`。这里只留"看驱动时必须认得的"。

### §4.1 五个结构的隶属关系

```
struct dma_device               (include/linux/dmaengine.h，控制器)
 ├── cap_mask                   能力位图（DMA_SLAVE / DMA_CYCLIC / DMA_MEMCPY …）
 ├── channels                   链表头，串起所有 dma_chan
 ├── device_prep_slave_sg       ┐
 ├── device_prep_dma_cyclic     ├─ 回调，由控制器驱动实现
 ├── device_config              │  这一组就是"控制器驱动要交的作业"
 ├── device_issue_pending       │
 ├── device_tx_status           │
 ├── device_terminate_all       │
 └── device_synchronize         ┘ 可选！不实现会静默降级（§5.4）

struct dma_chan                 (一条通道)
 ├── device                     反指 dma_device
 ├── cookie / completed_cookie  两个水位（:331 / :332）
 ├── client_count               >0 表示被占用
 ├── slave                      使用者的 struct device（dmaengine.c:864 赋值）
 ├── name                       "dma:<名字>"（:861 拼出来）
 └── dev                        struct dma_chan_dev，只用来挂 sysfs

struct dma_async_tx_descriptor  (一笔传输)
 ├── cookie                     :603；submit 前是 -EBUSY
 ├── flags                      :604，DMA_PREP_INTERRUPT 等
 ├── tx_submit                  :607，指向驱动的 submit 实现
 ├── callback / callback_param   完成回调
 └── callback_result             带 result 的新式回调

struct virt_dma_chan            (drivers/dma/virt-dma.h:22-37，可选层)
 └── 五条链表 :30 ~ :34         详见 §5.3
```

### §4.2 四个必须认得的枚举

| 枚举 | 位置 | 干什么 |
|---|---|---|
| `enum dma_transaction_type` | `include/linux/dmaengine.h:51-70` | 能力位。`DMA_SLAVE`（`:62`）是 arm64 的主角 |
| `enum dma_transfer_direction` | `:79-84` | `DMA_MEM_TO_DEV`（`:81`）/ `DMA_DEV_TO_MEM`（`:82`） |
| `enum dma_status` | `:37-43` | `DMA_COMPLETE`（`:38`）/ `DMA_IN_PROGRESS`（`:39`）/ `DMA_PAUSED`（`:40`）/ `DMA_ERROR`（`:41`）/ `DMA_OUT_OF_ORDER`（`:42`） |
| `enum dma_slave_buswidth` | `:373-384` | 1 ~ 128 字节，另有反常的 3 字节（`:377`） |

`enum dma_ctrl_flags`（`:194-202`）里最常用的两个：
`DMA_PREP_INTERRUPT`（`:195`，要不要完成中断）和
`DMA_CTRL_ACK`（`:196`）。

一个已记录的小瑕疵：`enum dma_status` 的 kerneldoc（`:31-35`）
只写了前四个值，**`DMA_OUT_OF_ORDER`（`:42`）没有文档**。
见 `99-evidence.md` §6 第 ⑥ 条。

### §4.3 通道的三个"身份"别搞混

这是读 `dmaengine.c` 时最容易乱的地方：

| 叫法 | 类型 | 从哪来 | 例子 |
|---|---|---|---|
| **sysfs 名** | 字符串 | `dev_set_name(...,"dma%dchan%d", ...)`（`dmaengine.c:1069-1070`） | `dma0chan3` |
| **消费者视角的名** | 字符串 | DTS 的 `dma-names`，拼成 `chan->name = "dma:rx"`（`:861`） | `dma:rx` |
| **cookie** | `s32` | `dma_cookie_assign`（`drivers/dma/dmaengine.h:29-40`） | `47` |

前两个是**通道**的名字（一条通道一辈子一个），第三个是**一笔传输**的名字
（一条通道上不断递增）。

---

## §5 机制速览

> 逐段代码剖析在 `03-internals.md`。这里只留"骨架 + 那些坑在哪一步"。

### §5.1 控制器注册（提供者侧）

```
控制器驱动 probe
 └─ 填 struct dma_device：cap_mask、channels、device_* 回调
 └─ dma_async_device_register(dd)          dmaengine.c:1137
      ├─ 检查 dev/dev->dev 非空            :1143-1150
      ├─ 九道能力门：声明了某个 cap 就必须实现对应回调
      │     :1153-1214，每道门失败返回 -EIO 并打 dev_err
      ├─ 检查 device_tx_status / issue_pending  :1217-1226
      ├─ 分配 dev_id                       :1240-1242
      ├─ 逐通道 __dma_async_device_channel_register  :1247-1251
      └─ list_add_tail_rcu 挂进 dma_device_list      :1270
 └─ of_dma_controller_register(np, my_xlate, data)   of-dma.c:118-145
```

两条独立的注册：`dma_async_device_register` 让通道**存在**，
`of_dma_controller_register` 让通道**能被 DTS 找到**。
少后一个的症状是"控制器在 `summary` 里能看见，但消费者永远
`-EPROBE_DEFER`"（`06-devicetree.md` §7.3 的决策树第 2 步就是查这个）。

九道能力门只覆盖 16 个能力位中的 9 个 —— `DMA_SLAVE` 本身**不在**被检查
之列（`99-evidence.md` §6 第 ③ 条）。

### §5.2 通道申请（消费者侧）

```
dma_request_chan(dev, "rx")                 dmaengine.c:813-873
 ├─ if (dev->of_node)                       :818
 │    └─ of_dma_request_slave_channel        :819  → of-dma.c
 │         ├─ 逐 index 比 dma-names（strcmp）   of-dma.c:234
 │         ├─ 取 dmas[index]（按 #dma-cells 切） :237-238
 │         ├─ of_dma_find_controller            :289（按 of_node 指针匹配）
 │         └─ ofdma->of_dma_xlate(&spec, ofdma) :292 含义在驱动里
 ├─ （fallback）按 filter map 找                :833-848
 ├─ chan->slave = dev                          :864
 └─ 建两条 sysfs 符号链接                       :866-870
```

**这条路径几乎全程静默**。`of_dma_match_channel` 名字不匹配返回
`-ENODEV`（`of-dma.c:232`/`:235`），循环全试完统一被覆盖成
`-EPROBE_DEFER`（`of-dma.c:294`）。DTS 名字写错、请求线号越界、
phandle 指错节点 —— **三种完全不同的错误，症状一模一样**。
唯一的破法是打开 `of-dma.c` 的 dynamic debug（`06-devicetree.md` §7.3）。

### §5.3 一笔传输的三步 + virt-dma 的五条链表

```
  ① dmaengine_prep_slave_sg(chan, sgl, n, dir, flags)
        └─ 描述符进 desc_allocated       virt-dma.h:70
  ② dmaengine_submit(desc)  → cookie     include/linux/dmaengine.h:1212
        └─ 描述符挪到 desc_submitted      virt-dma.c:19 起
  ③ dma_async_issue_pending(chan)        include/linux/dmaengine.h:1418
        └─ 整条链表挪到 desc_issued       virt-dma.h:84（一行 list_splice）
  ── 硬件干活，中断 ──
  ④ vchan_cookie_complete                virt-dma.h:103 → desc_completed
  ⑤ vchan_complete (tasklet)             virt-dma.c:83-110 → 跑回调
  ── 或者被终止 ──
  ⑥ vchan_terminate_vdesc                virt-dma.h:149 → desc_terminated
  ⑦ vchan_synchronize                    virt-dma.h:220 → 安全释放
```

**三步 API 与前三条链表一一对应** —— "分三步"的物理体现就是
"描述符在链表间挪三次"。分三步的价值全在第 ③ 步：可以攒多笔一次通知硬件。

**漏了第 ③ 步的症状**：`prep` 成功、`submit` 返回正常 cookie、
零错误码、零日志，只有"传输超时"。展开在 `08-feynman.md` §4。

### §5.4 终止与同步：本子系统最危险的一处

| 函数 | 语义 | 陷阱 |
|---|---|---|
| `dmaengine_terminate_async` | 下达停止命令 | **返回时硬件可能还在写你的 buffer**，回调可能还在另一个 CPU 上跑 |
| `dmaengine_synchronize` | 等到真的停了 | `device_synchronize` 为 NULL 时**什么都不做也不报错**（`:1159` 落空） |
| `dmaengine_terminate_sync` | 上面两步的封装（`:1177-1187`） | 因为上一条，它的 "sync" 可能是假的 |

本树里就有活例子：`drivers/dma/mediatek/mtk-uart-apdma.c`
**没有设置 `device_synchronize`**。在那个控制器上
`terminate_sync` 之后立刻释放 buffer 是一个真实的 use-after-free 窗口。
完整论证在 `08-feynman.md` §8。

### §5.5 状态查询

```
dmaengine_tx_status(chan, cookie, &state)   include/linux/dmaengine.h:1207-1209
 └─ device_tx_status 回调
      └─ 驱动一般调 dma_cookie_status       drivers/dma/dmaengine.h:68-83
           └─ 两个整数比大小，不碰描述符
```

`state.residue` 能不能信，取决于硬件档位（§2.6）。
先用 `dma_get_slave_caps`（`drivers/dma/dmaengine.c:571-617`）问。

---

## §6 端到端：从上电到搬完一次数据

**把 §5 的片段接成一条完整时间线。** 场景：MediaTek 板子上串口收数据。

```
 ┌── 阶段 1：控制器上线（内核启动早期）────────────────────────────┐
 │                                                                │
 │ subsys_initcall: dma_bus_init                dmaengine.c:1642  │
 │   ├─ class_register(&dma_devclass)                    :1649    │
 │   └─ dma_channel_table_init                           :273-275 │
 │        把 DMA_SLAVE / DMA_PRIVATE / DMA_INTERRUPT           │
 │          从全局能力 mask 里抹掉 —— 匿名申请永远看不到 slave    │
 │                                                                │
 │ APDMA 驱动 probe                                               │
 │   ├─ 读 reg / interrupts（每通道一套）  mtk-uart-apdma.c:531-549│
 │   ├─ 读 dma-requests 当通道数用                        :525    │
 │   ├─ vchan_init 逐通道               virt-dma.c:123-138        │
 │   ├─ dma_async_device_register       dmaengine.c:1137          │
 │   │    → /sys/class/dma/dma0chan0 … 出现                       │
 │   │    → /sys/kernel/debug/dmaengine/summary 里能看到          │
 │   └─ of_dma_controller_register      of-dma.c:118-145          │
 │        → 挂进 of_dma_list，DTS 从此能找到它                    │
 └────────────────────────────────────────────────────────────────┘
                              │
 ┌── 阶段 2：串口驱动申请通道（串口 probe）───────────────────────┐
 │                                                                │
 │ dma_request_chan(&pdev->dev, "rx")   dmaengine.c:813           │
 │   └─ of_dma_request_slave_channel                              │
 │        ├─ strcmp("rx", dma-names[i])   of-dma.c:234            │
 │        ├─ 取 dmas[i] → args[0] = 1     of-dma.c:237            │
 │        ├─ of_dma_find_controller       of-dma.c:289            │
 │        └─ of_dma_xlate_by_chan_id      of-dma.c:351-370        │
 │             这里 args[0] 被当成"通道序号"（mtk 的语义）      │
 │   └─ 建 sysfs 符号链接                 dmaengine.c:866-870     │
 │                                                                │
 │ dmaengine_slave_config(chan, &cfg)   include/linux/dmaengine.h:946 │
 │   └─ device_config 回调，驱动通常立刻写硬件寄存器              │
 │        cfg 里的 src_addr 是串口 FIFO 的**物理地址**            │
 └────────────────────────────────────────────────────────────────┘
                              │
 ┌── 阶段 3：一次实际传输（每次收数据）──────────────────────────┐
 │                                                                │
 │ ① dma_map_single / sg_init          ← 这是 DMA API 的活，不是   │
 │                                        dmaengine 的（§1.2）    │
 │ ② desc = dmaengine_prep_slave_sg(...)  → desc_allocated        │
 │ ③ desc->callback = uart_rx_done                                │
 │ ④ cookie = dmaengine_submit(desc)      → desc_submitted        │
 │      从这一刻起 desc 指针不该再碰，身份是 cookie             │
 │ ⑤ dma_async_issue_pending(chan)        → desc_issued           │
 │      漏了这一步：零日志、零错误码、只有超时                  │
 │ ── 硬件：请求线握手 → burst 搬运 → 完成中断 ──                 │
 │ ⑥ 驱动中断处理 → vchan_cookie_complete → desc_completed        │
 │ ⑦ tasklet: vchan_complete            virt-dma.c:83-110         │
 │      └─ 跑 uart_rx_done()                                      │
 │ ⑧ 串口驱动：dmaengine_tx_status 查 residue → 知道实际收了多少  │
 │ ⑨ dma_unmap_single                  ← 又是 DMA API             │
 └────────────────────────────────────────────────────────────────┘
                              │
 ┌── 阶段 4：收摊（串口 remove / 关闭）──────────────────────────┐
 │                                                                │
 │ dmaengine_terminate_sync(chan)      include/linux/dmaengine.h:1177 │
 │   先确认控制器实现了 device_synchronize，否则这一步是假的   │
 │ dma_release_channel(chan)           dmaengine.c:903-912        │
 │   └─ client_count 归零，通道回到可申请状态                     │
 └────────────────────────────────────────────────────────────────┘
```

这条时间线上**核心一句日志都不打**（除了注册期的能力门报错和
两条 sysfs 链接失败的 `dev_warn`）。所有 `dev_dbg` 集中在申请通道阶段
（`dmaengine.c:626`/`:641`/`:646`/`:675`/`:679`/`:710`），
默认全都看不见。这就是 `07-debug.md` §0 的结论"可观测性很薄"的来源。

---

## §7 和别的子系统什么关系

### §7.1 一张关系图

```
       ┌─────────────────────────────────────────────────┐
       │  消费者：8250 串口 / spi-*/ mmc / ASoC / crypto  │
       └───────────────┬─────────────────────────────────┘
                       │ dma_request_chan / prep_* / submit
                       ▼
       ┌─────────────────────────────────────────────────┐
       │            dmaengine 核心                        │
       │  drivers/dma/dmaengine.c + of-dma.c + virt-dma  │
       └──┬─────────┬────────────┬──────────┬────────────┘
          │         │            │          │
          │         │            │          └─► debugfs / sysfs（很薄）
          │         │            │
          │         │            └─► platform 驱动模型
          │         │                 （控制器就是个 platform_device）
          │         │
          │         └─► OF / device tree
          │              只读 3 个属性：dmas / dma-names / #dma-cells
          │
          └─► 控制器驱动 drivers/dma/<vendor>/*.c
                   │
                   ├─► clk（控制器要时钟）
                   ├─► reset（有的要复位）
                   ├─► irq / GIC（完成中断）
                   └─► IOMMU / SMMU（地址翻译，若有）

  ── 平行但不重叠 ──
       DMA API（kernel/dma/，dma_map_* / dma_alloc_coherent）
         管地址和 cache，和 dmaengine 是两件事，但几乎总是一起用
```

### §7.2 逐条说清边界

| 相关子系统 | 关系 | 容易搞错的点 |
|---|---|---|
| **DMA API**（`kernel/dma/`） | **平行**。dmaengine 驱动硬件，DMA API 管地址与 cache | 名字像，职责完全不同。见 §1.2 |
| **platform 总线** | DMA 控制器就是个 platform 设备 | dmaengine 没有自己的 `bus_type`，通道不参与任何匹配 |
| **OF / device tree** | 核心只读 3 个属性 | `dma-channels` / `dma-requests` / `dma-channel-mask` **核心不读**，是驱动私有（`06-devicetree.md` §6） |
| **clk / reset / pinctrl** | 控制器驱动的依赖 | 控制器 probe 失败 → 所有消费者 `-EPROBE_DEFER`，看起来像 DMA 配错了 |
| **IOMMU / SMMU** | 透明地作用在 DMA 地址上 | `iommus` 属性由 IOMMU 子系统读，dmaengine 核心不认识 |
| **serial / spi / mmc / ASoC** | **消费者**，arm64 上的主要用户 | 它们各自有 PIO fallback，所以 DMA 坏了往往只表现为"慢" |
| **async_tx / RAID**（`crypto/async_tx/`） | dmaengine 的另一类消费者 | arm64 上基本用不到；`CONFIG_ASYNC_TX_CHANNEL_SWITCH` 甚至不是个 Kconfig 符号（`99-evidence.md` §6 第 ⑫ 条） |
| **tracing** | **几乎零交集** —— dmaengine 核心（`dmaengine.c` / `of-dma.c` / `virt-dma.c` / `include/linux/dmaengine.h`）一个 tracepoint 都没有 | 全树唯一的例外是 `drivers/dma/tegra20-apb-dma.c`（3 个调用点，事件定义在 `include/trace/events/tegra_apb_dma.h`），**arm64 上碰不到**。想看传输时间线只能靠 kprobe / 驱动的 `dev_dbg`（`07-debug.md` §7） |

**一条实用推论**：在 arm64 上排查"DMA 不工作"，
**先怀疑上面那一列的依赖，而不是 dmaengine 自己**。
控制器 probe 失败（clk 名字错、reg 少一段）是最常见的根因，
而它的症状和 DTS 写错完全一样。

---

## §8 导读：接下来读哪一篇

### §8.1 按角色

| 你是 | 建议顺序 |
|---|---|
| **要给外设驱动加 DMA 支持** | `01`（API 清单）→ `05` §1（消费者骨架）→ `06` §3（DTS 消费者侧）→ `08` §4/§8/§9 |
| **要写一个 DMA 控制器驱动** | `02`（结构）→ `03` Part A（注册流程）→ `05` §2（提供者骨架）→ `04` §1（virt-dma）→ `06` §2 |
| **在排查一个 DMA 故障** | `07` §8（一页速查）→ `06` §7（三层定位）→ `07` §6（错误码分诊） |
| **只想搞懂它怎么设计的** | 本篇 → `08`（费曼）→ `03` Part C（设计评价） |
| **要复核这套文档的可信度** | `99`（取证篇） |

### §8.2 按篇目

| 篇 | 内容 | 什么时候翻它 |
|---|---|---|
| `01-api.md` | 代码地图 + 对外 API 全表 + 错误码语义 | 想不起某个函数叫什么 |
| `02-datastructures.md` | 结构体字段全貌 + 关系 ASCII 图 + 全局变量与锁 | 读驱动源码卡在某个字段上 |
| `03-internals.md` | Part A 时间线 / Part B 横切（锁、引用计数）/ Part C 设计评价 | 要弄清"这一步核心到底做了什么" |
| `04-extensions.md` | virt-dma、metadata、router/dmamux、async_tx | 碰到可选层 |
| `05-usage.md` | 消费者骨架 + 控制器驱动骨架（未编译） | 照着写代码 |
| `06-devicetree.md` | DTS 每个属性谁消费 + 三层定位 + 错误消息映射表 | 写 DTS 或 probe 失败 |
| `07-debug.md` | 开关全门表 + 日志可见性 + sysfs/debugfs + dmatest + 错误码分诊 | 手上有个坏板子 |
| `08-feynman.md` | 九个最容易想错的地方，每个四段式 | 觉得"哪里别扭说不出来" |
| `99-evidence.md` | 证据等级 + 逐篇取证表 + **16 条自陈问题** + 未验证项四层 | 要判断某条结论能不能直接用 |

### §8.3 如果只记三件事

1. **DMA 控制器和外设不是父子、不是 device/driver**，只由一条 sysfs
   符号链接关联；服务靠消费者按名字主动申请。（`08` §1）
2. **DTS 里 `dmas = <&x N>` 那个 `N` 的含义由控制器驱动定**，
   pl330 是请求线号、mtk 是通道号；写错了**全程静默**，
   最终症状统一是 `-EPROBE_DEFER`。（`06` §4 / `08` §3）
3. **`terminate_sync` 的 sync 可能是假的** ——
   `device_synchronize` 是可选回调，不实现就静默降级，
   之后立刻释放 buffer 是真实的 use-after-free 窗口。（`08` §8）

