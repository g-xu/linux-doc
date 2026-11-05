---
title: 数据结构
---

# SPI 核心数据结构（Linux 6.1.158, arm64）

> 本篇回答「这个字段是什么意思、谁写它、和别的结构什么关系」。
>
> **开篇不画图，先破歧义**——图画得再全，也补不上一个没被显式否掉的错误类比。
> §1.0 用三组最容易搞错的关系开场，每组都给出「为什么不会发生」的行号。

---

## 目录

| 节 | 内容 |
|----|------|
| §1.0 | **先分清三组关系**（错误类比 → 对比表 → 否定性证据） |
| §2 | 七个核心结构体的字段表 |
| §3 | 结构关系 ASCII 图（含两个缝合点的行号） |
| §4 | 传输数据流图 |
| §5 | 全局变量与锁 |
| §6 | 命名约定速查 |
| §7 | 内部结构表（不对外的那些） |

---

## §1.0 先分清三组关系

### 第 1 组：`spi_controller` 里的回调 —— SPI **没有** `spi_algorithm`

**先否掉错误类比。** 从 i2c 过来的人第一反应是：

> ❌「`i2c_adapter` 配 `i2c_algorithm`，那 SPI 应该是 `spi_controller` 配
> `spi_algorithm`。前者是数据、后者是方法。」

**SPI 里没有 `spi_algorithm` 这个东西。** `transfer_one`、`set_cs`、`can_dma`、
`prepare_message` 这些方法**直接内嵌在 `struct spi_controller` 里**
（`include/linux/spi/spi.h:668-696` 一带），和 `num_chipselect`、`max_speed_hz`
这些数据字段并列在同一个结构体里。

**摊开对比：**

| 维度 | i2c | spi |
|-----|-----|-----|
| 数据与方法是否分开 | 分开：`i2c_adapter` + `i2c_algorithm` | **不分开**：全在 `spi_controller` 里 |
| 方法表能不能被多个实例共享 | 能。一个 `i2c_algorithm` 常量可以被 N 个 adapter 指向 | 不能共享——每个 `spi_controller` 各自 `kzalloc` 出来，回调逐个赋值 |
| 方法表能不能是 `const` | 能，而且惯例就是 `static const struct i2c_algorithm` | 不能，它是可写结构体的一部分 |
| 核心能不能替你填一个默认方法 | 不容易（要换整张表） | **容易，而且核心真的这么做**：不填 `transfer_one_message` 时核心直接给你装上自己的默认实现 |

**为什么 SPI 选了「不分开」，代码依据在哪：**

因为 SPI 核心需要**逐个回调地**判断「你有没有实现这个」，然后分别决定走哪条路。
这在合并式布局里就是几个 `if (ctlr->xxx)`，在分离式布局里会变成一堆
`if (adap->algo->xxx)`。三处证据：

| 判断 | 位置 | 核心据此做什么 |
|-----|------|--------------|
| `if (ctlr->transfer)` | `drivers/spi/spi.c:4009-4010` | 没有就直接 `-ENOTSUPP` |
| `if (ctlr->transfer == spi_queued_transfer)` | `drivers/spi/spi.c:2233` | 判断这个控制器用的是不是核心自带的排队实现 |
| `if (!ctlr->transfer_one_message) ctlr->transfer_one_message = spi_transfer_one_message;` | `spi_controller_initialize_queue()`，`drivers/spi/spi.c:2192` 起 | **核心替驱动填上默认实现** |

最后一条是关键：**核心可以往 `spi_controller` 里写东西**。如果方法表是共享的
`const` 结构体，这个操作根本没法做。所以这不是「SPI 抄 i2c 抄漏了」，而是一个
有代价也有收益的不同选择：代价是每个控制器多占几十个字节的函数指针，
收益是回调可以逐个降级、可以被核心动态替换。

**那么在 SPI 里，谁扮演 driver 的角色？**

| i2c 里的角色 | SPI 里对应什么 |
|------------|--------------|
| `i2c_adapter`（总线实例） | `spi_controller` |
| `i2c_algorithm`（总线方法） | `spi_controller` 里的一组回调字段 |
| `i2c_client`（挂在总线上的芯片） | `spi_device` |
| `i2c_driver`（芯片的驱动） | `spi_driver` |

**只有最后一行是真正的 device↔driver 关系**（`spi_bus_type` 定义在
`drivers/spi/spi.c:471`，匹配函数 `spi_match_device()` 在 `:375`）。
`spi_controller` 既不是 device 侧也不是 driver 侧——它是**总线本身**。

### 第 2 组：`spi_controller` 为什么不注册在 `spi_bus_type` 上

**先否掉错误类比。**

> ❌「`spi_controller` 里有一个 `struct device dev`，那它当然是注册在
> `spi_bus_type` 上的 SPI 设备。」

它有 `struct device`，但**它注册在一个 class 上，不是 bus 上**。

**逐行摊开三个问题：**

| 问题 | `spi_controller` | `spi_device` |
|-----|-----------------|-------------|
| 内嵌 `struct device` 吗 | ✓ `include/linux/spi/spi.h:510` | ✓ `include/linux/spi/spi.h:177` |
| `dev.bus` 被赋值了吗 | **✗ 从不赋值** | ✓ `= &spi_bus_type`，`drivers/spi/spi.c:598` |
| `dev.class` 被赋值了吗 | ✓ `= &spi_master_class` 或 `&spi_slave_class`，`drivers/spi/spi.c:2928` / `:2926` | ✗ 从不赋值 |
| `dev.parent` 指向谁 | 传进来的 `dev`，也就是**platform device**，`drivers/spi/spi.c:2929` | `&ctlr->dev`，`drivers/spi/spi.c:597` |
| 会被 driver 绑定吗 | **永远不会** | 会，`spi_probe()` `drivers/spi/spi.c:410` |
| 在 sysfs 里出现在哪 | `/sys/class/spi_master/spiB/` | `/sys/bus/spi/devices/spiB.C/` |

**「为什么不会被绑定」的否定性证据。** 驱动模型只对 `dev->bus` 非空的设备跑
match/probe 流程（`bus_probe_device()` → `device_attach()`）。`spi_controller`
的 `dev.bus` 从头到尾没有任何一行赋值——可以自己验：

```bash
grep -n "ctlr->dev\.\(bus\|class\)" drivers/spi/spi.c
# 只有 :2926 和 :2928 两行，都是 .class，没有一行 .bus
```

反过来，`spi_device` 的 `dev.class` 也从不赋值。**两者在设备模型里走的是
两条完全不相交的路**：一条 class（纯粹为了在 sysfs 里成组 + 提供
`class_find_device_by_of_node()` 这类反查，用例见 `drivers/spi/spi.c:4442`），
一条 bus（为了 match + probe）。

**那控制器驱动是怎么被 probe 的？** 靠**另一条总线**。控制器的 DT 节点
（`spi@ff1d0000`）由 `of_platform_populate()` 变成一个 **platform device**，
platform 总线上的 `rockchip_spi_driver` 匹配上它、跑 `probe()`，
`probe()` 里才 `spi_alloc_master()` + `spi_register_controller()`。

这就是 SPI 子系统的**双总线形状**，也是 §3 那张图要画的东西。

> 大白话版本 + 「为什么非要这样」的动机，见 `08-feynman.md` §2。

### 第 3 组：`spi_message` 和 `spi_transfer` 的原子性边界

**先否掉两个错误类比。**

> ❌ 错误 A：「一个 `spi_transfer` 就是一次片选周期。」
> ❌ 错误 B：「一个 `spi_message` 就是一次片选周期。」

**A 明确错**：一个 message 里的多个 transfer 默认**共享一次片选**——这正是
「先发命令再读数据」能工作的原因（`00-overview.md` §2.4 (b)）。

**B 是「默认对，但可以被打破」**：`spi_transfer.cs_change`
（`include/linux/spi/spi.h:1032`）可以让片选在 transfer 之间翻一下。

**摊开三层边界：**

| 层次 | 边界的含义 | 谁能打破它 |
|-----|----------|----------|
| `spi_transfer` | 一段**连续时钟**，参数（速率/位宽/线宽）在这段里不变 | 不能打破，这是定义 |
| `spi_message` | 一次**原子事务**：核心保证期间没有别的 message 插进来 | 不能打破，这是核心的承诺 |
| 片选周期 | 默认与 message 边界重合 | **能**：`cs_change` 可以在中间翻，`cs_off` 可以让某段传输时片选是放开的 |

**「原子」到什么程度，代码在哪保证：**

`struct spi_message` 的 kerneldoc（`include/linux/spi/spi.h:1059-1089`）用的词是
"atomic sequence"。实际的保证机制是 `io_mutex`——`__spi_pump_messages()`
在处理一个 message 的整个过程中持有它（`drivers/spi/spi.c:1772` 加锁，
`:1837` 解锁），同步快路径的 `__spi_transfer_message_noqueue()` 也持有它
（`:4139`）。所以：

| 保证 | 有 | 没有 |
|-----|---|------|
| 同一控制器上两个 message 不会交错 | ✓（`io_mutex`） | |
| message 内部不会被别的 message 插入 | ✓ | |
| message 执行期间不会被中断/抢占打断 | | ✗ **没有这个保证**。核心会 `cond_resched()`（`drivers/spi/spi.c:1841`），transfer 之间还可能 `usleep_range()` |
| 片选一定全程保持 | | ✗ 看 `cs_change` / `cs_off` |

**一个必须记住的推论**：`spi_message` 的内存**归提交者所有**。kerneldoc
（`include/linux/spi/spi.h:1082-1088` 一带）明确说明：从提交到完成回调返回之前，
message、transfer 数组和数据缓冲都不能被释放或改写。核心不会给你复制一份。

**`is_dma_mapped` 是个例外要注意**：`spi_message.is_dma_mapped`
（`include/linux/spi/spi.h:1095`）表示「缓冲已经由提交者映射过了，核心别再映射」。
它旁边有一段 REVISIT 注释（`:1097-1106`）说明作者当年想加一个「只影响最后一个
transfer」的标志但没加——这条设计债记在 `03-internals.md` Part C。

---

## §2 核心结构体

### §2.1 `struct spi_controller`

**定义**：`include/linux/spi/spi.h:509-729`（约 60 个数据字段 + 20 个回调）。
**谁分配**：控制器驱动，通过 `spi_alloc_master()` 等。
**谁写**：驱动在 probe 里填能力和回调，核心在注册和运行时改状态字段。

#### 身份与链接

| 字段 | 行 | 谁写 | 含义 |
|-----|---|------|------|
| `dev` | 510 | 核心 | 内嵌 device。`dev.class` 被赋值、`dev.bus` 永不赋值（§1.0 第 2 组） |
| `list` | 512 | 核心 | 挂在全局 `spi_controller_list` 上 |
| `bus_num` | 520 | 驱动可预设，核心分配 | 总线号。**驱动填 -1（或不填，`__spi_alloc_controller` 已置 -1，`drivers/spi/spi.c:2921`）表示自动分配**；填具体值表示要固定编号（DT 的 `aliases` 会走这条路） |
| `devm_allocated` | 559 | 核心 | 是否由 `devm_spi_alloc_*` 分配。决定 unregister 时要不要自己 `put` |

#### 能力声明（驱动 probe 时必须填对）

| 字段 | 行 | 含义 | 填错的后果 |
|-----|---|------|----------|
| `num_chipselect` | 525 | 支持几个片选 | 默认已被置 1（`drivers/spi/spi.c:2922`）。**填小了，`reg` 大于它的子节点全部注册失败** |
| `mode_bits` | 533 | 支持哪些模式位（`SPI_CPOL`/`SPI_CPHA`/`SPI_CS_HIGH`/…） | 芯片要的位不在里面 → `spi_setup()` 返 `-EINVAL` → `probe` 失败 |
| `buswidth_override_bits` | 536 | 强制给所有子设备叠加的线宽位 | 会被写进 `spi_device.mode` 的初值（`drivers/spi/spi.c:600`） |
| `bits_per_word_mask` | 539 | 支持哪些位宽的**位掩码**，第 N-1 位表示支持 N 位 | ⚠️ **为 0 时视为「只支持 8 位」，不是「全支持」**。用 `SPI_BPW_MASK(n)`（`:540`）或 `SPI_BPW_RANGE_MASK(min,max)`（`:541`）构造 |
| `min_speed_hz` / `max_speed_hz` | 544 / 545 | 速率上下界 | `max` 填 0 时不做上限裁剪；`min` 填大了会拒掉低速芯片 |
| `dma_alignment` | 530 | DMA 缓冲对齐要求 | — |
| `max_dma_len` | 669 | 单次 DMA 传输上限 | — |
| `flags` | 548 | 约束标志，见下面的子表 | — |

**`flags` 子表**（全部定义在 `include/linux/spi/spi.h`）：

| 标志 | 行 | 含义 | 核心据此做什么 |
|-----|---|------|--------------|
| `SPI_CONTROLLER_HALF_DUPLEX` | 549 | 硬件不能同时收发 | 有 transfer 同时带 tx 和 rx 时 `__spi_validate()` 拒掉 |
| `SPI_CONTROLLER_NO_RX` | 550 | 完全不能收 | 带 `rx_buf` 的 transfer 被拒 |
| `SPI_CONTROLLER_NO_TX` | 551 | 完全不能发 | 带 `tx_buf` 的 transfer 被拒 |
| `SPI_CONTROLLER_MUST_RX` | 552 | **必须**有接收缓冲（硬件不接受"只发不收"） | `rx_buf` 为 NULL 时核心分配 `ctlr->dummy_rx` 顶上 |
| `SPI_CONTROLLER_MUST_TX` | 553 | 必须有发送缓冲 | 同理用 `ctlr->dummy_tx` |
| `SPI_MASTER_GPIO_SS` | 555 | 用 GPIO 片选时**也要**调 `set_cs` | `spi_set_cs()` `drivers/spi/spi.c:1018-1020` 会两个都拉 |
| `SPI_CONTROLLER_SUSPENDED` | 556 | 已挂起 | `__spi_sync()` 见到就返 `-ESHUTDOWN`（`drivers/spi/spi.c:4184-4187`） |

> ⚠️ 命名不一致：`flags` 一共 **7 个标志位**（BIT(0)~BIT(6)），三种情况：
> **5 个新旧双名**（`HALF_DUPLEX`/`NO_RX`/`NO_TX`/`MUST_RX`/`MUST_TX`，旧名 `SPI_MASTER_*` 的 `#define` 在 `:1595-1599`）；
> **`SPI_MASTER_GPIO_SS`（BIT(5)，`:555`）只有旧名**——本树里 **不存在** `SPI_CONTROLLER_GPIO_SS`；
> **`SPI_CONTROLLER_SUSPENDED`（BIT(6)，`:556`）只有新名**，它是后加的，从来没有 `SPI_MASTER_` 版。

#### 回调（谁必填、返回值语义见 `01-api.md` §1.3）

| 字段 | 行 | 全树实现数 |
|-----|---|----------|
| `setup` | 594 | **62**——最常被实现的回调 |
| `set_cs_timing` | 604 | **3**（`spi-mt65xx.c`、`atmel-quadspi.c`、`spi-tegra114.c`） |
| `transfer` | 625 | **2**（`spi-topcliff-pch.c`、`spi-mpc52xx.c`）——旧式接口，只剩这两个覆盖核心默认值 |
| `cleanup` | 629 | **26**（`setup` 里分配过私有区的才需要） |
| `can_dma` | 638 | **24** |
| `prepare_transfer_hardware` / `unprepare_transfer_hardware` | 671 / 674 | 10 / 13 |
| `transfer_one_message` | 672 | 29 |
| `prepare_message` / `unprepare_message` | 675 / 677 | **28** / 11 |
| `slave_abort` / `target_abort`（union） | 679-682 | **9** / **0** |
| `set_cs` | 688 | **30** |
| `transfer_one` | 689 | **55** |
| `handle_err` | 691 | 11 |
| `mem_ops` / `mem_caps` | 695 / 696 | **24** / 3 |
| `max_transfer_size` / `max_message_size` | 572 / 573 | 10 / 5 |
| `fw_translate_cs` | 715 | **1**（`spi-pxa2xx.c`，非 arm64） |

> ⚠️ `setup` 的 kerneldoc（`include/linux/spi/spi.h:588-593`）有一句 IMPORTANT：
> **调用它的时候总线上可能正有别的设备在传输**。所以 `setup` 里不能碰共享寄存器，
> 只能把参数存进 `spi_get_ctldata()` 那块私有区，等真要传输时再写硬件。

> 上表"实现数"的口径和两个坑（不能用 `.field =`、不能全树 grep）
> 见 `01` §1.3 表后的说明和 `99` §2.5。全部数字在定稿时重跑过一遍。

#### 消息泵状态（核心写，驱动只读）

| 字段 | 行 | 含义 |
|-----|---|------|
| `queued` | 651 | 是否已建好队列（`spi_controller_initialize_queue()` 成功） |
| `kworker` | 652 | 消息泵 kthread worker |
| `pump_messages` | 653 | 泵的 work 项 |
| `queue_lock` | 654 | 保护 `queue` 和 `cur_msg` 的 spinlock |
| `queue` | 655 | 待处理 message 链表 |
| `cur_msg` | 656 | 正在处理的 message |
| `cur_msg_completion` | 657 | 泵等待 message 完成用 |
| `cur_msg_incomplete` / `cur_msg_need_completion` | 658 / 659 | 一对配合 `smp_wmb`/`smp_mb` 的标志，用来**跳过不必要的 completion**。设计意图见 `drivers/spi/spi.c:1718-1730` 的长注释 |
| `busy` | 660 | 泵正在忙 |
| `running` | 661 | 泵没有被 suspend |
| `queue_empty` | 727 | **同步快路径的判据**，`READ_ONCE` 读（`drivers/spi/spi.c:4204`） |
| `must_async` | 728 | 禁用快路径。全树只有 `drivers/spi/spi-mux.c:165` 设它 |
| `cur_msg_mapped` | 664 | 当前 message 已做 DMA 映射 |
| `fallback` | 667 | 本次 DMA 失败已回退 PIO |
| `xfer_completion` | 668 | 等单个 transfer 完成用，被 `spi_finalize_current_transfer()` 唤醒 |

#### 片选状态缓存

| 字段 | 行 | 含义 |
|-----|---|------|
| `last_cs` | 665 | 上次激活的片选号，未激活时是 -1 |
| `last_cs_mode_high` | 666 | 上次的 `SPI_CS_HIGH` 状态 |
| `cs_gpiods` | 699 | GPIO 片选描述符数组（`spi_get_gpio_descs()` 填，`drivers/spi/spi.c:2985`） |
| `use_gpio_descriptors` | 700 | 驱动声明用新式 gpiod |
| `unused_native_cs` / `max_native_cs` | 701 / 702 | 混用 GPIO 和 native 片选时的记账 |

> ⚠️ `last_cs`/`last_cs_mode_high` 让 `spi_set_cs()` 可以提前返回
> （`drivers/spi/spi.c:983-986`），**所以驱动的 `set_cs` 不一定每次传输都被调**。
> 调「片选没动」这类问题必须先看这两个字段。

#### 其它

| 字段 | 行 | 含义 |
|-----|---|------|
| `io_mutex` / `add_lock` / `bus_lock_spinlock` / `bus_lock_mutex` / `bus_lock_flag` | 576 / 579 / 582 / 583 / 586 | 四把锁 + 一个标志，见 §5.2 |
| `slave` / `target`（union） | 561-566 | 是不是从模式。两个名字是同一个 bool |
| `rt` | 662 | 消息泵跑 RT 优先级 |
| `auto_runtime_pm` | 663 | 让核心替驱动做 runtime PM |
| `dma_tx` / `dma_rx` | 708 / 709 | dmaengine 通道，**驱动自己填**，核心只用来做映射 |
| `dma_map_dev` / `cur_rx_dma_dev` / `cur_tx_dma_dev` | 641 / 642 / 643 | DMA 映射用的 device（可能不是控制器自己） |
| `dummy_rx` / `dummy_tx` | 712 / 713 | `MUST_RX`/`MUST_TX` 时顶上的哑缓冲，核心分配、核心释放 |
| `pcpu_statistics` | 705 | per-CPU 统计 |
| `ptp_sts_supported` | 721 | 支持 PTP 时间戳。全树只有 `drivers/spi/spi-fsl-dspi.c:1441` |
| `irq_flags` | 724 | 给 `spi_take_timestamp_*` 用的中断标志保存位置 |

**代码示例：一个最小控制器的字段填法**（改编自 `drivers/spi/spi-rockchip.c:850` 一带）：

```c
ctlr = devm_spi_alloc_master(&pdev->dev, sizeof(struct my_spi));
ctlr->auto_runtime_pm    = true;                  /* 让核心管 runtime PM */
ctlr->bus_num            = pdev->id;              /* 或者不填，让核心自动分配 */
ctlr->mode_bits          = SPI_CPOL | SPI_CPHA | SPI_CS_HIGH | SPI_LOOP;
ctlr->bits_per_word_mask = SPI_BPW_MASK(16) | SPI_BPW_MASK(8) | SPI_BPW_MASK(4);
ctlr->num_chipselect     = 2;
ctlr->max_speed_hz       = min(clk_get_rate(my->spiclk) / 2, MAX_SCLK_OUT);
ctlr->set_cs             = my_set_cs;
ctlr->transfer_one       = my_transfer_one;
ctlr->handle_err         = my_handle_err;
ctlr->max_transfer_size  = my_max_transfer_size;
ctlr->dev.of_node        = pdev->dev.of_node;     /* 核心据此扫子节点 */
ret = devm_spi_register_controller(&pdev->dev, ctlr);
```

### §2.2 `struct spi_device`

**定义**：`include/linux/spi/spi.h:176-220`。
**谁分配**：**核心**（`spi_alloc_device()` `drivers/spi/spi.c:576`）。
协议驱动和控制器驱动都不该自己 `kzalloc` 它。

| 字段 | 行 | 谁写 | 含义 |
|-----|---|------|------|
| `dev` | 177 | 核心 | 内嵌 device。`dev.bus = &spi_bus_type`（`drivers/spi/spi.c:598`），`dev.parent = &ctlr->dev`（`:597`） |
| `controller` | 178 | 核心 | 指回所属控制器 |
| `master` | 179 | 核心 | ⚠️ **和 `controller` 指向同一个东西**，一行里一起赋值（`drivers/spi/spi.c:596`）。注释就写着 `/* Compatibility layer */`。新代码只用 `controller` |
| `max_speed_hz` | 180 | DT / 驱动 | 这颗芯片能接受的最高速率 |
| `chip_select` | 181 | 核心（从 DT 的 `reg`） | 片选号，**类型 `u8`** |
| `bits_per_word` | 182 | DT / 驱动 | 默认位宽，0 视为 8 |
| `rt` | 183 | 驱动 | 要求消息泵跑 RT 优先级 |
| `mode` | 196 | DT / 驱动 | 模式位。低 17 位是用户可见的（`SPI_MODE_USER_MASK`，`include/uapi/linux/spi/spi.h:40`），高位是内核内部的 |
| `irq` | 197 | DT | 这颗芯片的中断号（DT 的 `interrupts`）。**核心不申请它**，协议驱动自己 `request_irq` |
| `controller_state` | 198 | 控制器驱动 | 控制器给这颗芯片留的私有状态，用 `spi_get_ctldata()`/`spi_set_ctldata()` 存取 |
| `controller_data` | 199 | 板级代码 | 非 DT 平台传进来的板级私有数据。arm64 上基本不用 |
| `modalias` | 200 | 核心 | 匹配用的字符串，长度上限 `SPI_NAME_SIZE`。**是 `compatible` 去掉厂商前缀之后的部分** |
| `driver_override` | 201 | sysfs | 强制绑定的驱动名 |
| `cs_gpiod` | 202 | 核心 | 片选 GPIO 描述符。⚠️ **是单数**，6.1 里一颗设备只能有一个片选 GPIO |
| `word_delay` | 203 | 驱动 | 字间延时 |
| `cs_setup` / `cs_hold` / `cs_inactive` | 205 / 206 / 207 | 驱动 | 片选三段时序。只有实现了 `set_cs_timing` 的 3 个控制器才真的编程进硬件 |
| `pcpu_statistics` | 210 | 核心 | per-CPU 统计 |

**两个内部模式位**（不是用户可见的模式，注意别和 `SPI_CPOL` 混在一起）：

| 位 | 行 | 含义 |
|---|---|------|
| `SPI_NO_TX` | 184 | `BIT(31)`，这颗设备不用发送 |
| `SPI_NO_RX` | 185 | `BIT(30)`，不用接收 |
| `SPI_MODE_KERNEL_MASK` | 195 | `(~(BIT(30) - 1))`，即高 2 位。用来把内核内部位和用户位分开 |

编译期断言保证两个空间不重叠（`include/linux/spi/spi.h:223-224`）：

```c
static_assert((SPI_MODE_KERNEL_MASK & SPI_MODE_USER_MASK) == 0,
              "SPI_MODE_USER_MASK & SPI_MODE_KERNEL_MASK must be 0");
```

**四个 `idx` 被忽略的 accessor**（`include/linux/spi/spi.h:266` / `271` / `276` / `281`）：

```c
static inline u8 spi_get_chipselect(const struct spi_device *spi, u8 idx)
{
        return spi->chip_select;        /* idx 完全没被用到 */
}
```

`spi_set_chipselect()`、`spi_get_csgpiod()`、`spi_set_csgpiod()` 同样忽略 `idx`。
这是为**将来的多片选支持**预留的接口形状，6.1 里传什么都一样。看到这四个函数
不要以为已经支持一设备多片选了。

### §2.3 `struct spi_driver`

**定义**：`include/linux/spi/spi.h:313-320`。

| 字段 | 谁写 | 含义 |
|-----|------|------|
| `id_table` | 驱动 | `struct spi_device_id` 数组，比对 `spi_device.modalias` |
| `probe` | 驱动 | 探测。核心在调它之前已经调过 `spi_setup()`（`spi_probe()` `drivers/spi/spi.c:410`） |
| `remove` | 驱动 | ⚠️ **返回 `void`**。6.1 里已完成 `int → void` 转换 |
| `shutdown` | 驱动 | 关机回调，走 `spi_shutdown()` `drivers/spi/spi.c:461` |
| `driver` | 驱动 | 内嵌 `device_driver`。`owner` 由 `__spi_register_driver()` 自动填（`drivers/spi/spi.c:490-492`），驱动不用写 |

### §2.4 `struct spi_message`

**定义**：`include/linux/spi/spi.h:1090-1127`。
**谁分配**：**提交者**（协议驱动）。核心不复制，见 §1.0 第 3 组。

| 字段 | 行 | 谁写 | 含义 |
|-----|---|------|------|
| `transfers` | 1091 | 提交者 | transfer 链表头。顺序 = 线上的顺序 |
| `spi` | 1093 | 核心 | 目标设备。`__spi_async()` 会替你填（`drivers/spi/spi.c:4012`），提交者不用管 |
| `is_dma_mapped` | 1095 | 提交者 | 缓冲已自己映射，核心别再映射 |
| `complete` | 1109 | 提交者 | 完成回调。⚠️ **不允许睡** |
| `context` | 1110 | 提交者 | 传给 `complete` 的私有指针 |
| `frame_length` | 1111 | 核心 | 全部 transfer 的长度之和，`__spi_validate()` 算 |
| `actual_length` | 1112 | 核心 | **实际**传了多少字节。传输失败时它小于 `frame_length` |
| `status` | 1113 | 核心 | 0 或负错误码 |
| `queue` | 1119 | 核心 | 挂在 `ctlr->queue` 上用 |
| `state` | 1120 | 核心 | 泵的内部游标 |
| `resources` | 1123 | 核心 | `spi_res` 链表（拆分 transfer 时的临时内存挂在这里） |
| `prepared` | 1126 | 核心 | `ctlr->prepare_message()` 已调过，决定要不要配套调 `unprepare_message()`（`drivers/spi/spi.c:1699`） |

**代码示例**（最常见的两 transfer 写法）：

```c
u8 cmd = 0x9f, id[3];
struct spi_transfer xfers[2] = {
        { .tx_buf = &cmd, .len = 1 },
        { .rx_buf = id,   .len = 3 },
};
struct spi_message msg;

spi_message_init_with_transfers(&msg, xfers, ARRAY_SIZE(xfers));
ret = spi_sync(spi, &msg);
/* ret == 0 只表示时钟摇完了，不表示 id[] 里的内容有意义 */
```

### §2.5 `struct spi_transfer`

**定义**：`include/linux/spi/spi.h:1015-1057`。

| 字段 | 行 | 谁写 | 含义 |
|-----|---|------|------|
| `tx_buf` / `rx_buf` | 1021 / 1022 | 提交者 | 发/收缓冲，任一可为 NULL |
| `len` | 1023 | 提交者 | ⚠️ **收发共用一个长度**——SPI 全双工，物理上必然相等 |
| `tx_dma` / `rx_dma` | 1025 / 1026 | 提交者 | 只在 `is_dma_mapped` 时有意义 |
| `tx_sg` / `rx_sg` | 1027 / 1028 | 核心 | 核心做 DMA 映射时建的 sg 表 |
| `dummy_data` | 1030 | 核心 | 这段用的是 `dummy_rx`/`dummy_tx` |
| `cs_off` | 1031 | 提交者 | ⚠️ **这一段传输时片选是放开的**（少见但真有硬件需要） |
| `cs_change` | 1032 | 提交者 | 这一段之后翻一下片选 |
| `tx_nbits` / `rx_nbits` | 1033 / 1034 | 提交者 | 线宽，取值 `SPI_NBITS_SINGLE/DUAL/QUAD`（`:1035-1037`）。**没有 OCTAL**，8 线只能走 `spi_device.mode` |
| `bits_per_word` | 1038 | 提交者 | 覆盖设备默认值，0 = 沿用 |
| `delay` | 1039 | 提交者 | 这一段**之后**的延时 |
| `cs_change_delay` | 1040 | 提交者 | `cs_change` 翻片选时的延时 |
| `word_delay` | 1041 | 提交者 | 字**之间**的延时 |
| `speed_hz` | 1042 | 提交者 | 覆盖设备默认速率，0 = 沿用 |
| `effective_speed_hz` | 1044 | **控制器驱动回填** | 分频取整后的**真实**速率。查「速率上不去」的第一现场 |
| `ptp_sts_word_pre` / `_post` | 1046 / 1047 | 提交者 | 要在第几个字上打时间戳 |
| `ptp_sts` | 1049 | 驱动 | 时间戳落点 |
| `timestamped` | 1051 | 驱动 | 已打过时间戳 |
| `transfer_list` | 1053 | 核心 | 挂进 `msg->transfers` 用 |
| `error` | 1056 | 驱动 | 目前只有一个位：`SPI_TRANS_FAIL_NO_START`（`:1055`），表示「传输**根本没启动**」——核心据此判断能不能安全地回退到 PIO 重试（`drivers/spi/spi.c:1542-1548`） |

### §2.6 `struct spi_delay`

**定义**：`include/linux/spi/spi.h:112-118`。

```c
struct spi_delay {
#define SPI_DELAY_UNIT_USECS    0        /* :113 */
#define SPI_DELAY_UNIT_NSECS    1        /* :114 */
#define SPI_DELAY_UNIT_SCK      2        /* :115 */
        u16     value;
        u8      unit;
};
```

`SPI_DELAY_UNIT_SCK` 是「N 个 SCK 周期」，换算需要知道速率，所以
`spi_delay_to_ns()` 必须传 `xfer`——传 `NULL` 时它返回 `-EINVAL`。
`value` 是 `u16`，所以微秒单位下最长约 65 ms。

### §2.7 `struct spi_statistics`

**定义**：`include/linux/spi/spi.h:63-83`。per-CPU 分配，`u64_stats_sync` 保护。

| 成员 | 含义 |
|-----|------|
| `messages` / `transfers` / `errors` / `timedout` | 计数 |
| `spi_sync` / `spi_sync_immediate` / `spi_async` | 三种提交路径各自的计数 |
| `bytes` / `bytes_rx` / `bytes_tx` | 字节数 |
| `transfer_bytes_histo[SPI_STATISTICS_HISTO_SIZE]` | 长度分布，`SPI_STATISTICS_HISTO_SIZE` = **17**（`:79`） |
| `transfers_split_maxsize` | 被拆分的次数 |

累加宏：`SPI_STATISTICS_ADD_TO_FIELD`（`:85`）、`SPI_STATISTICS_INCREMENT_FIELD`（`:96`）。
sysfs 侧的 `show` 由 `SPI_STATISTICS_ATTRS`（`drivers/spi/spi.c:139`）批量生成，
17 个桶的名字在 `drivers/spi/spi.c:197-213`（`"0-1"`、`"2-3"`、…、`"65536+"`）。

> **PREEMPT_RT 的 4 行改动全在这个结构的读取路径上**：
> `spi_pcpu_stats_totalize()`（`drivers/spi/spi.c:120-137`）里的
> `u64_stats_fetch_begin_irq` → `u64_stats_fetch_begin`（`:130`）和
> `u64_stats_fetch_retry_irq` → `u64_stats_fetch_retry`（`:133`）。
> 传输路径**没有**被 RT 补丁碰过——取证见 `99-evidence.md` §1.2。

### §2.8 `struct spi_res`

**定义**：`include/linux/spi/spi.h:879-883`。

```c
struct spi_res {
        struct list_head        entry;
        spi_res_release_t       release;
        unsigned long long      data[]; /* Guarantee ull alignment */
};
```

kerneldoc（`:871-878`）自己说明了定位：**"based on ideas from devres, but focused
on life-cycle management during spi_message processing"**——生命周期绑在一个
message 上，而不是绑在 device 上。

目前唯一的使用者是 transfer 拆分：`spi_replace_transfers()`
（`drivers/spi/spi.c:3427`）用 `spi_res_alloc()`（`:3441`）分配替换记录，
`spi_res_add()`（`:3519`）挂上，`spi_finalize_current_message()` 里的
`spi_res_release()`（`:2054`）统一释放。

### §2.9 `struct spi_board_info`

**定义**：`include/linux/spi/spi.h:1509`。非 DT 平台用来静态声明「哪条总线的哪个片选上
有一颗什么芯片」。arm64 上基本用不到（DT 走 `of_register_spi_device()` 那条路），
只在 `03-internals.md` §A3 顺带说明它和 `board_list` 的延迟实例化机制。

---

## §3 结构关系图

### §3.1 双总线全景（含两个缝合点的行号）

```
 ┌──────────────────── platform_bus_type ────────────────────┐
 │                                                            │
 │  platform_device                platform_driver            │
 │  "ff1d0000.spi"    ◄── match ──► "rockchip-spi"            │
 │   └ dev.of_node = spi@ff1d0000       └ probe()             │
 │       ▲                                  │                │
 └───────┼──────────────────────────────────┼────────────────┘
         │                                  │
         │  ①  ctlr->dev.parent = dev;      │ spi_alloc_master()
         │     drivers/spi/spi.c:2929        │ + devm_spi_register_controller()
         │     控制器侧缝合点               ▼
 ┌───────┼───────────────── spi_master_class ─────────────────┐
 │       │                                                    │
 │  struct spi_controller                                     │
 │    ├ dev            include/linux/spi/spi.h:510            │
 │    │   ├ .class = &spi_master_class   spi.c:2928  ← class! │
 │    │   ├ .bus   = 从不赋值             ← 所以永不被 probe   │
 │    │   └ .parent ────────────────────────────────► ①       │
 │    ├ num_chipselect / mode_bits / max_speed_hz  （能力）    │
 │    ├ transfer_one / set_cs / can_dma / …        （方法）    │
 │    │     方法和数据在同一个结构体里，没有 spi_algorithm    │
 │    ├ queue + kworker + pump_messages           （消息泵）   │
 │    └ io_mutex / add_lock / bus_lock_*          （四把锁）   │
 │       │                                                    │
 └───────┼────────────────────────────────────────────────────┘
         │
         │  ②  spi->dev.parent = &ctlr->dev;   drivers/spi/spi.c:597
         │     spi->dev.bus    = &spi_bus_type; drivers/spi/spi.c:598
         │     设备侧缝合点
         ▼
 ┌────────────────────── spi_bus_type ────────────────────────┐
 │   drivers/spi/spi.c:471                                    │
 │                                                            │
 │  struct spi_device "spi1.0"        struct spi_driver       │
 │    ├ dev  ─────────── match ────►    ├ driver.of_match_table│
 │    │   spi_match_device()             ├ id_table            │
 │    │   drivers/spi/spi.c:375          ├ probe()             │
 │    ├ controller ──► 上面那个 ctlr     └ remove()  (void!)   │
 │    ├ master     ──► 同一个指针（兼容层，spi.c:596）           │
 │    ├ chip_select / mode / max_speed_hz / bits_per_word      │
 │    ├ cs_gpiod（单数）                                       │
 │    └ modalias = compatible 去掉厂商前缀                      │
 │                                                            │
 │        只有这一对是真正的 device ↔ driver 关系             │
 └────────────────────────────────────────────────────────────┘
```

三条读图要点：

1. **两个 `dev.parent` 赋值（① 和 ②）就是整个子系统的骨架**。去掉它们，
   platform 世界和 SPI 世界就断开了。
2. `spi_controller` 在图上处于「总线本身」的位置——它上面是 platform 世界，
   下面是 SPI 世界，自己不属于任何一条 match 关系。
3. slave 模式下 `spi_master_class` 换成 `spi_slave_class`（`drivers/spi/spi.c:2926`），
   其余形状不变。

### §3.2 message / transfer 的内存归属

```
   协议驱动栈上 或 kzalloc 出来（内存归提交者，核心不复制）
   ┌─────────────────────────────────────────────────┐
   │  struct spi_message                             │
   │    spi          ── 核心填，drivers/spi/spi.c:4012 │
   │    transfers  ──┐                               │
   │    complete     │                               │
   │    context      │                               │
   │    status     ◄─┼── 核心写                       │
   │    actual_length│   核心写，spi.c:1601            │
   │    resources  ──┼──► spi_res 链表（核心挂/核心释放）│
   │    prepared   ◄─┘   核心写，spi.c:1699            │
   └─────────────────┼───────────────────────────────┘
                     │ transfer_list
     ┌───────────────┴──────┬──────────────────┐
     ▼                      ▼                  ▼
  spi_transfer #0       #1                   #N
   tx_buf ──► 用户数据缓冲（也归提交者，必须 DMA-safe）
   rx_buf ──► 用户数据缓冲
   len      （收发共用）
   tx_sg / rx_sg  ◄── 核心做 DMA 映射时才填
   effective_speed_hz ◄── 控制器驱动回填
   error ◄── 驱动置 SPI_TRANS_FAIL_NO_START，核心据此决定回退 PIO
```

---

## §4 传输数据流图

从协议驱动的一次 `spi_sync()` 到 MOSI 引脚，数据经过的**结构体**（不是函数，
函数视角见 `00-overview.md` §6）：

```
  ┌ 协议驱动 ─────────────────────────────────────────────┐
  │  u8 buf[4];                                            │
  │  spi_transfer.tx_buf = buf                             │
  │  spi_message.transfers = { &xfer }                     │
  └──────────────────┬─────────────────────────────────────┘
                     │ spi_sync(spi, &msg)
                     ▼
  ┌ 核心：校验与补默认值 ────────────────────────────────┐
  │  __spi_validate()  spi.c:3850                         │
  │    读 spi_device.bits_per_word / max_speed_hz          │
  │    读 spi_controller.bits_per_word_mask / flags        │
  │    写 spi_transfer.speed_hz / bits_per_word（补默认）    │
  │    写 spi_message.frame_length                         │
  └──────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │ queue_empty && !must_async ?
        ▼ 是（快路径）              ▼ 否（慢路径）
  ┌──────────────────┐     ┌──────────────────────────────┐
  │ 当前线程直接跑    │     │ 挂进 spi_controller.queue     │
  │ noqueue          │     │ 唤醒 kworker（pump_messages） │
  │ spi.c:4134       │     │ spi.c:2155                    │
  └────────┬─────────┘     └───────────┬──────────────────┘
           └────────────┬──────────────┘
                        ▼
  ┌ 核心：准备硬件与内存 ────────────────────────────────┐
  │  __spi_pump_transfer_message()  spi.c:1641            │
  │   ① runtime PM get         （auto_runtime_pm）         │
  │   ② prepare_transfer_hardware()                       │
  │   ③ spi_split_transfers_maxsize()                     │
  │        → 超长 transfer 被替换成多个短的                 │
  │        → 原件记录进 spi_message.resources（spi_res）    │
  │   ④ prepare_message()  → 置 spi_message.prepared       │
  │   ⑤ spi_map_msg()                                      │
  │        can_dma() 为真 → 填 spi_transfer.tx_sg/rx_sg    │
  │        MUST_RX/MUST_TX → 顶上 ctlr->dummy_rx/dummy_tx  │
  │        置 spi_controller.cur_msg_mapped                │
  └──────────────────┬────────────────────────────────────┘
                     ▼
  ┌ 核心：逐 transfer 循环 ──────────────────────────────┐
  │  spi_transfer_one_message()  spi.c:1507               │
  │   读 spi_transfer.cs_off → spi_set_cs()               │
  │        → 比对/更新 spi_controller.last_cs             │
  │        → 读 spi_device.mode 的 SPI_CS_HIGH 决定极性    │
  │   累加 spi_statistics（字节数 + 17 桶）                 │
  │   调 ctlr->transfer_one(ctlr, spi, xfer)              │
  │   读 spi_transfer.cs_change → 中途翻片选               │
  │   累加 spi_message.actual_length                       │
  └──────────────────┬────────────────────────────────────┘
                     ▼
  ┌ 控制器驱动 ──────────────────────────────────────────┐
  │  读 spi_transfer.speed_hz / bits_per_word / tx_nbits  │
  │  写分频寄存器；回填 spi_transfer.effective_speed_hz    │
  │  DMA：用 spi_transfer.tx_sg 提交 dmaengine            │
  │  PIO：直接往 FIFO 写 spi_transfer.tx_buf              │
  │  完成后 spi_finalize_current_transfer()               │
  │        → complete(&spi_controller.xfer_completion)    │
  └──────────────────┬────────────────────────────────────┘
                     ▼
                  MOSI / SCLK 引脚
```

**回程**（完成路径写了哪些字段）：

```
  spi_finalize_current_message()  spi.c:2026
    ├ unprepare_message()（仅当 spi_message.prepared）
    ├ spi_res_release()  → 释放 spi_message.resources 上的全部 spi_res
    ├ 写 spi_message.status
    ├ 清 spi_controller.cur_msg / cur_msg_mapped / fallback
    └ 调 spi_message.complete(spi_message.context)
           └ spi_sync 的等待者被唤醒，读走 spi_message.status / actual_length
```

---

## §5 全局变量与锁

### §5.1 全局变量

| 变量 | 位置 | 保护它的锁 | 含义 |
|-----|------|----------|------|
| `spi_master_idr` | `drivers/spi/spi.c:45` | `board_lock` | 总线号 → 控制器 的 IDR。`bus_num` 从这里分配 |
| `board_list` | `drivers/spi/spi.c:549` | `board_lock` | 静态 `spi_board_info` 链表（等控制器出现时实例化） |
| `spi_controller_list` | `drivers/spi/spi.c:550` | `board_lock` | 全部已注册控制器 |
| `board_lock` | `drivers/spi/spi.c:557` | — | `DEFINE_MUTEX`，保护上面三个 |
| `spi_bus_type` | `drivers/spi/spi.c:471` | — | SPI 总线类型 |
| `spi_master_class` / `spi_slave_class` | `drivers/spi/spi.c:2764` / `:2867` | — | 控制器所属的 class |
| `buf`（`SPI_BUFSIZ` 字节） | `spi_write_then_read()` 内部 | 一把静态 mutex | ⚠️ **全局共享的传输缓冲**，这是 `spi_write_then_read()` 不适合热路径的原因 |
| `spi_of_notifier` / `spi_acpi_notifier` | `drivers/spi/spi.c:4452` / `:4515` 一带 | — | OF_DYNAMIC / ACPI 的重配置通知器 |

### §5.2 锁

| 锁 | 字段 / 位置 | 类型 | 保护什么 | 主要加锁点 |
|---|-----------|------|---------|----------|
| `bus_lock_mutex` | `include/linux/spi/spi.h:583` | mutex | `spi_sync()` 的串行化 + `spi_bus_lock()` 的独占语义 | `spi_sync()` `drivers/spi/spi.c:4262`；`spi_bus_lock()` `:4307` |
| `io_mutex` | `:576` | mutex | **实际的硬件访问**：一次只有一个 message 在跑 | `__spi_pump_messages()` `drivers/spi/spi.c:1772`；`__spi_transfer_message_noqueue()` `:4139` |
| `queue_lock` | `:654` | spinlock | `ctlr->queue` 链表、`cur_msg`、`busy`、`running`、`queue_empty` | `__spi_pump_messages()` `drivers/spi/spi.c:1775`；`__spi_queued_transfer()` `:2155` |
| `bus_lock_spinlock` | `:582` | spinlock | `bus_lock_flag`。**可以在原子上下文取**，所以 `spi_async()` 能在中断里调 | `spi_async()` `drivers/spi/spi.c:4070` |
| `add_lock` | `:579` | mutex | 防止两个线程同时给同一个片选加设备 | `spi_add_device()` `drivers/spi/spi.c:698` |
| `board_lock` | `drivers/spi/spi.c:557` | mutex（全局） | §5.1 的三个全局变量 | `spi_register_controller()`、`spi_register_board_info()` |

**锁序**：`bus_lock_mutex` → `io_mutex` → `queue_lock`。
`add_lock` 和 `board_lock` 只在注册/注销路径上，不与传输路径的三把锁嵌套。

**`spi-mux` 的 lockdep 特例**：`drivers/spi/spi-mux.c` 让一条 SPI 总线挂在另一条
SPI 总线下面，于是同类型的锁会嵌套。它显式降低了 lockdep 的子类：

```c
lockdep_set_subclass(&ctlr->io_mutex, 1);    /* drivers/spi/spi-mux.c:144 */
lockdep_set_subclass(&ctlr->add_lock, 1);    /* drivers/spi/spi-mux.c:145 */
```

这两行是「同一把锁类型合法嵌套」的教科书例子。它也解释了为什么 `spi-mux` 必须设
`must_async`（`drivers/spi/spi-mux.c:165`）——如果走同步快路径，
下层传输会在上层的 `io_mutex` 里再取一次自己的 `io_mutex`。

**per-CPU 统计的同步**：不用上面任何一把锁，用 `u64_stats_sync`。
读取侧 `spi_pcpu_stats_totalize()`（`drivers/spi/spi.c:120-137`）走
`u64_stats_fetch_begin`/`_retry` 重试循环，写入侧靠 per-CPU 天然串行。

---

## §6 命名约定速查

| 形式 | 含义 | 例子 |
|-----|------|------|
| `spi_xxx()` | 面向协议驱动的公共 API | `spi_sync()`、`spi_setup()` |
| `__spi_xxx()` | 内部实现，**调用者通常已持锁** | `__spi_sync()`、`__spi_async()`、`__spi_validate()`、`__spi_pump_messages()` |
| `spi_controller_xxx()` | 面向控制器驱动 | `spi_controller_suspend()`、`spi_controller_get_devdata()` |
| `spi_master_xxx()` / `spi_slave_xxx()` | **旧名字**，语义完全等价；实现形式有 `#define` / 独立 inline / 重复字段 / `union` 同址**四**种，见 `01-api.md` §1.1 | `spi_master_get()` 是 `#define`（`include/linux/spi/spi.h:1604`）；`spi_alloc_master()` 不是别名而是独立 inline（`:786`）|
| `devm_spi_xxx()` | devres 版本，自动清理 | `devm_spi_alloc_master()` |
| `spi_mem_xxx()` | `spi-mem` 子框架 | `spi_mem_exec_op()` |
| `spi_bitbang_xxx()` | bit-bang 复用层 | `spi_bitbang_start()` |
| `spidev_xxx()` | 字符设备实现（内部） | `spidev_ioctl()` |
| `of_spi_xxx()` / `of_register_spi_xxx()` | DT 相关 | `of_spi_parse_dt()` |
| `SPI_CPOL` / `SPI_MODE_0` / `SPI_TX_QUAD` | **用户可见**的模式位，定义在 `include/uapi/linux/spi/spi.h` | — |
| `SPI_NO_TX` / `SPI_NO_RX` / `SPI_MODE_KERNEL_MASK` | **内核内部**的模式位，在 `include/linux/spi/spi.h` | — |
| `SPI_CONTROLLER_*` | 控制器约束标志（`flags` 字段） | `SPI_CONTROLLER_MUST_TX` |
| `SPI_MASTER_*` | 上面的旧名字，**外加一个还没改名的 `SPI_MASTER_GPIO_SS`** | — |
| `SPI_STATISTICS_*` | 统计相关的宏 | `SPI_STATISTICS_INCREMENT_FIELD` |
| `SPI_MEM_OP_*` | 构造 `spi_mem_op` 的宏 | `SPI_MEM_OP_DATA_IN` |
| `SPI_IOC_*` | spidev 的 ioctl 号，在 `include/uapi/linux/spi/spidev.h` | `SPI_IOC_MESSAGE(N)` |
| `spiB` / `spiB.C` | sysfs / 设备名。B = 总线号，C = 片选号 | `spi1.0` |

**四个「名字很像但不是一回事」的坑**：

| 容易混 | 区别 |
|-------|------|
| `spi_device.mode` vs `spi_controller.mode_bits` | 前者是「这颗芯片要什么」，后者是「这个控制器支持什么」。`spi_setup()` 拿后者校验前者 |
| `spi_controller.flags` vs `spi_device.mode` | `flags` 是 `SPI_CONTROLLER_*` 约束，`mode` 是 `SPI_CPOL` 那一套。两组值域完全不同，别互相赋值 |
| `spi_transfer.delay` vs `word_delay` vs `cs_change_delay` | 分别是「这段之后」「字之间」「翻片选时」 |
| `spi_finalize_current_transfer()` vs `spi_finalize_current_message()` | 前者给 `transfer_one` 的实现者用（ISR 里调）；后者只给自己实现了 `transfer_one_message` 的驱动用 |

---

## §7 内部结构（不对外）

这些只在 `drivers/spi/` 内部出现，协议驱动和控制器驱动都不该碰。列在这里是为了
读核心代码时不至于卡住。

| 结构 / 符号 | 位置 | 作用 |
|-----------|------|------|
| `struct boardinfo` | `drivers/spi/spi.c:544` | 包一层 `spi_board_info`，挂在 `board_list` 上等控制器出现 |
| `struct spi_replaced_transfers` | `include/linux/spi/spi.h:1282` | transfer 拆分时保存被替换掉的原件，本身是一块 `spi_res` |
| `spi_dev_check()` | `drivers/spi/spi.c:620` | 遍历回调，判断「这个片选是不是已经被占了」，占了返回 `-EBUSY` |
| `spidev_release()` | `drivers/spi/spi.c:47` | `spi_device` 的 `dev.release`。⚠️ **和 `drivers/spi/spidev.c` 里的字符设备毫无关系**，只是同名 |
| `spi_controller_release()` | `drivers/spi/spi.c:2756` | 控制器的 `dev.release` |
| `spi_dev_attrs[]` / `spi_dev_groups[]` | `drivers/spi/spi.c:217` / `:264` | 设备侧 sysfs 属性组 |
| `spi_controller_statistics_attrs[]` / `spi_master_groups[]` | `drivers/spi/spi.c:270` / `:307` | 控制器侧 sysfs 属性组 |
| `spi_slave_attrs[]` / `spi_slave_groups[]` | `drivers/spi/spi.c:2852` / `:2861` | slave 模式专有的 `slave` 属性 |
| `drivers/spi/internals.h` 里的一组声明 | 全文件 43 行 | `spi_map_msg`/`spi_unmap_msg`/`spi_dma_sync_for_{device,cpu}`。**控制器驱动不该 include 它** |

---

## 相关章节

| 想知道 | 去哪 |
|-------|------|
| 这些字段在流程的哪一步被写 | `03-internals.md` Part A |
| 四把锁的完整锁序推导和死锁场景 | `03-internals.md` §B1 |
| `spi_mem_op` 等 `spi-mem` 的结构体 | `04-extensions.md` §2 |
| DT 的哪个属性写进哪个字段 | `06-devicetree.md` §2 |
| 「controller 为什么不是 device」的大白话版 | `08-feynman.md` §2 |
| 「SPI 为什么没有 spi_algorithm」的大白话版 | `08-feynman.md` §3 |
