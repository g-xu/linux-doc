---
title: 总览
---

# irq 00：总览（复习入口）

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3** · 本篇定位：**复习入口**
>
> 隔几个月回来，只读这一篇就能恢复全貌；要细节再翻后面九篇（§8 有导读表）。
>
> ⚠️ 本篇是**浓缩**，不是新内容。所有论断的取证都在对应的分篇里。
> ⚠️ 本树 `include/linux/interrupt.h` 有一处 PREEMPT_RT 本地补丁（99 §1.1）。

---

## §1 定位：irq 子系统解决什么问题

### §1.1 一句话

**irq 子系统把"一堆互不相同的中断控制器硬件"抽象成"一个整数（virq）+ 一个回调"，
让设备驱动完全不必知道中断控制器长什么样。**

### §1.2 它管什么、不管什么

| | 内容 |
|---|---|
| **管** | 中断号的分配与映射（hwirq ↔ virq）· 中断的使能/屏蔽/亲和性/唤醒 · 把中断分发到正确的 handler · 线程化 · 共享 · 乱中断检测 · CPU 热插拔时的迁移 |
| **不管** | **CPU 的异常向量表** —— 那是 `arch/arm64/kernel/entry.S` 的事 |
| 不管 | **softirq / tasklet / workqueue** —— 那是"中断下半部"，属于另一套机制（虽然 `interrupt.h` 里也声明了它们） |
| 不管 | 具体控制器的寄存器操作 —— 那在 `drivers/irqchip/` 的 129 个驱动里 |
| 不管 | 设备树的解析 —— 那在 `drivers/of/irq.c`（06 §1.1） |

### §1.3 四个关键判据

| 判据 | 答案 | 出处 |
|---|---|---|
| 它有自己的 `struct bus_type` 吗？ | **没有** ⇒ **框架型**子系统 | 99 §1.3 |
| 它读设备树吗？ | **一条属性都不读** | 06 §1.1 |
| 它有 tracepoint 吗？ | **有 5 个**（2 个 irq + 3 个 softirq） | 07 §3.1 |
| 中断控制器是"设备"吗？ | **一级控制器不是**（早于设备模型）；二级控制器是 | 02 §2.2 · 08 §9 |

### §1.4 规模

| 项 | 数值 |
|---|---|
| 核心 | `kernel/irq/` **22 个 `.c` + 3 个 `.h`，共 15044 行**（`.c` 部分 14294 行） |
| 最大三个文件 | `manage.c` 2934 · `irqdomain.c` 2006 · `chip.c` 1607 |
| 头文件 | `irq.h` 1278 · `interrupt.h` 867 · `irqdomain.h` 641 · `irqdesc.h` 251 · `internals.h` 513 |
| 控制器驱动 | `drivers/irqchip/` **129 个 `.c`、48150 行** |
| arm64 主力驱动 | `irq-gic-v3.c` 2584 · `irq-gic-v3-its.c` 5608 |
| 导出符号 | **151**（21 个 `EXPORT_SYMBOL` + 130 个 `_GPL`） |
| Kconfig | **30** 个 `config` 项（**全部由架构/驱动 `select`**） |
| 官方文档 | `Documentation/core-api/irq/` 5 篇、454 行（`irq-domain.rst` 297 行是主体） |
| DT 绑定 | `Documentation/devicetree/bindings/interrupt-controller/` **113 个文件** |
| 错误码出现次数 | 208（`-EINVAL` 占 121，58%） |
| WARN 家族 | **126** 次 vs `pr_err` 仅 14 次 |

**对比 remoteproc**：那套核心 5068 行、导出 33 个符号。
**irq 的对外面积是它的 4.5 倍**，因为它同时服务**三类完全不同的使用者**（§3.1）。

---

## §2 硬件背景：arm64 上中断是怎么送到 CPU 的

### §2.1 中断的物理形态

一个外设要"打扰"CPU，硬件上只有三种做法：

| 形态 | 物理实现 | arm64 上的名字 |
|---|---|---|
| **一根线** | 外设的一个引脚接到中断控制器的一个输入 | SPI（Shared Peripheral Interrupt） |
| **一次内存写** | 外设往一个特定地址写一个值，控制器识别出这是中断 | LPI（Locality-specific Peripheral Interrupt）= MSI |
| **CPU 之间互发** | 一个 CPU 写寄存器，另一个 CPU 收到中断 | SGI（Software Generated Interrupt） |

还有一种特殊的"线"：**每个 CPU 各有一根**（如每个核自己的定时器）——
叫 PPI（Private Peripheral Interrupt）。

### §2.2 GIC v3 的三个部件

```
   ┌──────────────────────── GIC v3 ────────────────────────────┐
   │                                                             │
   │  ┌──────────────────┐        外设的中断线（SPI）             │
   │  │   Distributor    │◀────── 一堆物理引脚                    │
   │  │   （GICD）        │                                       │
   │  │  · 全局唯一一个    │  职责：                               │
   │  │  · 管 SPI         │  · 使能/屏蔽每条 SPI                   │
   │  │  · 决定路由       │  · 设优先级、触发类型                   │
   │  └────────┬─────────┘  · 决定这条中断送给哪个 CPU        │
   │           │                                                 │
   │  ┌────────▼─────────┐  ┌──────────────────┐                │
   │  │  Redistributor   │  │  Redistributor   │  … 每 CPU 一个   │
   │  │   （GICR）        │  │                  │                │
   │  │  · 管 PPI 和 SGI  │  │                  │  职责：          │
   │  │  · 管 LPI     │  │                  │  · per-CPU 状态  │
   │  └────────┬─────────┘  └────────┬─────────┘                │
   │           │                     │                           │
   │  ┌────────▼─────────┐  ┌────────▼─────────┐                │
   │  │  CPU Interface   │  │  CPU Interface   │  … 每 CPU 一个   │
   │  │   （GICC/ICC）    │  │                  │                │
   │  │  不是 MMIO！  │  │                  │  职责：          │
   │  │  用系统寄存器访问  │  │                  │  · ack / EOI     │
   │  └────────┬─────────┘  └────────┬─────────┘  · 优先级屏蔽    │
   │           │                     │                           │
   └───────────┼─────────────────────┼───────────────────────────┘
               ▼                     ▼
            CPU0 的 IRQ 引脚      CPU1 的 IRQ 引脚
```

| 部件 | 怎么访问 | DTS 里的体现 |
|---|---|---|
| GICD | MMIO | `reg` 的第一块 |
| GICR | MMIO（每 CPU 一段，连续排列） | `reg` 的第二块 |
| CPU Interface | **系统寄存器**（`ICC_*_EL1`），不是 MMIO | **DTS 里没有** |

**CPU Interface 用系统寄存器而不是 MMIO，是 GIC v3 相对 v2 最大的变化**：
v2 时代每次 ack 中断都要一次 MMIO 读（几十到几百纳秒），
v3 改成 `mrs x0, ICC_IAR1_EL1`（几个周期）。
⇒ 这就是 GIC v3 的中断延迟远低于 v2 的原因。

代码里的体现：`gic_handle_irq()`（`drivers/irqchip/irq-gic-v3.c:834`）
读 `ICC_IAR1_EL1` 拿 INTID，全程不碰 MMIO。

### §2.3 INTID 空间：六段编号

GIC v3 把所有中断放在一个统一的编号空间里，叫 **INTID**：

| INTID 范围 | 类型 | 含义 | 数量 |
|---|---|---|---|
| **0–15** | **SGI** | CPU 间中断（IPI） | 16 |
| **16–31** | **PPI** | 每 CPU 私有（arch timer、PMU） | 16 |
| **32–1019** | **SPI** | 共享外设中断（绝大多数设备） | 988 |
| 1020–1023 | 特殊 | 保留（1023 = "没有中断"） | 4 |
| **8192+** | **LPI** | MSI（由 ITS 分配） | 数万 |
| 4096–5119 | ESPI | 扩展 SPI（SPI 不够用时） | 1024 |
| 1056–1119 | EPPI | 扩展 PPI | 64 |

⚠️ **DTS 里写的不是 INTID，是"段内偏移"**（06 §3.1）：

| DTS 写的 | INTID（= hwirq） |
|---|---|
| `<GIC_SPI 164 …>` | **196**（164 + 32） |
| `<GIC_PPI 9 …>` | **25**（9 + 16） |

换算在 `gic_irq_domain_translate()` 里（`drivers/irqchip/irq-gic-v3.c:1547` / `:1550`）。

**`/proc/interrupts` 的 hwirq 列显示的是 INTID**（07 §4.1）——
所以那一列应该是 196 而不是 164。**这是把 DTS 和现实对起来最快的一步**。

### §2.4 亲和性路由：GIC v3 怎么决定送给哪个 CPU

| 中断类型 | 路由方式 | 能送给多个 CPU 吗 |
|---|---|---|
| SPI | GICD 里每条中断一个 `GICD_IROUTER<n>` 寄存器（64 位，写目标 CPU 的 affinity 值） | **不能** —— 只能一个（或"任意一个"模式） |
| PPI | 天生 per-CPU，无需路由 | —— |
| SGI | 发送时指定目标 | 能（掩码） |
| LPI | ITS 的转换表里记录目标 redistributor | 不能 |

⚠️ **"SPI 只能送给一个 CPU"是 arm64 上最常见的困惑来源**：
你写 `echo ff > /proc/irq/42/smp_affinity`，
读 `effective_affinity` 却只有一位。
⇒ 这不是 bug。GIC 驱动为此设了 `IRQD_SINGLE_TARGET`（02 §6.2），
`effective_affinity` 就是用来暴露这个事实的（04 §5.2）。

GIC v3 也支持 "1-of-N" 模式（硬件自己选一个空闲 CPU），
但 Linux 默认**不用** —— 因为它让中断的落点不可预测，不利于实时性和 cache 局部性。

### §2.5 优先级与抢占

GIC 给每条中断一个 8 位优先级（**数值越小优先级越高**）。
CPU Interface 里有一个 `ICC_PMR_EL1`（Priority Mask Register）：
**只有优先级高于 PMR 的中断才会被送到 CPU**。

**Linux 基本不用 GIC 的优先级** —— 所有普通中断设成同一个优先级。
理由：Linux 自己的中断模型是"中断处理很短，不需要嵌套抢占"。

⚠️ **唯一的例外是 pseudo-NMI**：
arm64 用"给 NMI 一个更高的 GIC 优先级 + 用 PMR 而不是 DAIF 来关中断"这个技巧，
实现了"能打断关中断临界区"的伪 NMI。
⇒ 这就是 `request_nmi()`（01 §1.1）和 `handle_fasteoi_nmi()`（01 §4.3）背后的硬件基础。

### §2.6 触发类型：电平 vs 边沿

| 类型 | 硬件行为 | 内核要做什么 |
|---|---|---|
| **电平触发**（level） | 只要条件成立，信号线**一直拉着** | **必须先清中断源，否则一放开就再来** |
| **边沿触发**（edge） | 只在"变化的那一瞬间"产生一个脉冲 | 处理期间又来一次脉冲会被硬件记住（pending 位） |

⚠️ **GIC v3 只支持两种**（06 §3.2）：

| 常量 | GIC v3 支持吗 |
|---|---|
| `IRQ_TYPE_LEVEL_HIGH` | **支持（最常用）** |
| `IRQ_TYPE_EDGE_RISING` | 支持 |
| `IRQ_TYPE_LEVEL_LOW` | **不支持** |
| `IRQ_TYPE_EDGE_FALLING` / `EDGE_BOTH` | **不支持** |

⇒ 需要低电平/下降沿的信号必须**板级加反相器**，或者**挂在支持它的 GPIO 控制器上**。

**触发类型决定了用哪个流处理器**（01 §4.3）：
电平用 `handle_level_irq`（先 mask 再 unmask）、
边沿用 `handle_edge_irq`（有重跑循环）、
GIC 这种硬件自带排队的用 `handle_fasteoi_irq`。
⇒ **选错的表现：电平用 edge 会中断风暴，边沿用 level 会丢中断**（05 §7）。

### §2.7 EOI：告诉控制器"我处理完了"

中断处理有两个"确认"动作：

| 动作 | 含义 | GIC v3 的寄存器 |
|---|---|---|
| **ack**（acknowledge） | "我看到了这个中断，把 INTID 告诉我" | 读 `ICC_IAR1_EL1`（读的动作本身就是 ack） |
| **EOI**（End Of Interrupt） | "我处理完了，可以送下一个同优先级的了" | 写 `ICC_EOIR1_EL1` |

⚠️ **不 EOI 的后果**：这条中断（以及所有同等或更低优先级的中断）
**永远不会再被送到这个 CPU**。
⇒ 这就是级联时忘了 `chained_irq_exit()` 会"中断只来一次"的原因（05 §3.4、08 §2）。

GIC 还有两种 EOI 模式（`EOImode`）：
- **模式 0**：写 `ICC_EOIR1_EL1` 同时完成"优先级下降"和"deactivate"；
- **模式 1**：两者分开（`ICC_EOIR1_EL1` + `ICC_DIR_EL1`）——
  **KVM 直通中断给虚拟机时必须用模式 1**（宿主机先降优先级，虚拟机稍后 deactivate）。

### §2.8 ITS：MSI 是怎么变成中断的

```
   PCIe 设备想发中断
     │
     │ ① 往一个特定物理地址写一个 32 位值（EventID）
     ▼
   ┌─────────────────── ITS ────────────────────┐
   │  ② 用发起者的 DeviceID（= 硬件 stream ID）  │
   │     查"设备表"                              │
   │  ③ 用 EventID 查该设备的"中断转换表"(ITT)   │
   │  ④ 得到 (LPI 号, 目标 collection)           │
   └──────────────────────┬─────────────────────┘
                          ▼
   ⑤ 转发给目标 CPU 的 Redistributor ⇒ 变成一条 LPI 中断
```

| 概念 | 含义 | 内核里对应什么 |
|---|---|---|
| **DeviceID** | 发起写操作的设备身份（通常 = SMMU 的 stream ID） | DTS 的 `msi-map` / `msi-parent` 第二个参数（06 §6） |
| **EventID** | 设备内部的中断编号（第几条 MSI） | MSI 描述符的 index |
| **LPI 号** | 全局的 INTID（≥ 8192） | hwirq |
| **ITT** | 每个设备一张表，EventID → LPI | ITS 驱动维护 |
| **collection** | "一组 CPU"，实际就是目标 redistributor | 亲和性 |

⚠️ **DeviceID 配错的后果**：ITS 收到一个它没登记过的 DeviceID，**直接丢弃** ——
**中断永远不来，一句日志都没有**（06 §8.4）。
⇒ 判据：和同节点的 `iommus = <&smmu 0xNNNN 0x0>` 里那个数对比。

**这也解释了为什么 MSI 域必须是层级域**（04 §2.1）：
① ITS 层要分配 LPI 号并填 ITT；② GIC/redistributor 层要接收这条 LPI。
两层各有自己的配置动作 ⇒ 一个 virq、两个 `irq_data`（08 §2）。

还解释了为什么 MSI 需要 `.activate` 阶段（04 §2.2、08 §8）：
"往哪个地址写什么值"这条信息要**写进设备的配置空间**，
而那个值是 ITS 层算出来的。

### §2.9 arm64 上的中断入口路径

```
   GIC 拉高 CPU 的 IRQ 引脚
     │
     ▼
   CPU 跳到异常向量（arch/arm64/kernel/entry.S）
     │  · 保存现场
     │  · 切到内核栈
     ▼
   handle_arch_irq(regs)          ← 一个函数指针
     │                              由 set_handle_irq() 装上
     ▼
   gic_handle_irq(regs)           ← drivers/irqchip/irq-gic-v3.c:834
     │  · mrs ICC_IAR1_EL1  拿 INTID（顺便 ack）
     ▼
   generic_handle_domain_irq(domain, hwirq)   ← 进入 irq 核心
```

| 关键位置 | 行号 |
|---|---|
| `handle_arch_irq` 的注册 | `kernel/irq/handle.c:218` 的 `set_handle_irq()` |
| GIC 侧的调用 | `drivers/irqchip/irq-gic-v3.c:1998` |
| 通用入口 | `kernel/irq/handle.c:232` 的 `generic_handle_arch_irq()` |
| GIC 的中断入口 | `drivers/irqchip/irq-gic-v3.c:834` |

`handle_arch_irq` 是函数指针而不是直接调用，
是因为一台机器上可能有多种一级控制器（`CONFIG_GENERIC_IRQ_MULTI_HANDLER`，01 §0.4）。

### §2.10 硬件事实 → 框架设计的映射

**把这张表记住，整个框架的形状就推得出来了**：

| 硬件事实 | 框架里对应的设计 |
|---|---|
| 不同控制器的编号空间互不相同 | `irq_domain`（hwirq ↔ virq，02 §1.0.A · 08 §1） |
| 同一条中断要在多级硬件上分别配置 | **层级域** + `parent_data` + `irq_chip_*_parent()`（08 §2） |
| ack / EOI / mask / unmask 的顺序因硬件而异 | **14 个流处理器**（01 §4.3） |
| 电平触发必须先清源 | `IRQF_ONESHOT` + `IRQS_ONESHOT`（08 §6） |
| SPI 只能送给一个 CPU | `IRQD_SINGLE_TARGET` + `effective_affinity`（04 §5.2） |
| mask/unmask 可能要走 I2C（会睡） | `irq_bus_lock` / `irq_bus_sync_unlock`（05 §4） |
| 多个设备可能共用一根线 | `IRQF_SHARED` + action 链 + 乱中断检测（08 §7） |
| 中断处理必须短，但有的活很慢 | **线程化中断**（03 §A7） |
| 中断控制器必须在别的驱动之前工作 | `IRQCHIP_DECLARE` + `of_irq_init()`（08 §9） |
| MSI 是运行期分配的 | MSI 域的两阶段（分配 + 激活，08 §8） |
| 一台机器潜在几万条中断，实际用几十条 | `CONFIG_SPARSE_IRQ` + 按需分配 `irq_desc`（03 §B7） |

---

## §3 概念模型

### §3.1 三类使用者，三个头文件

```
   ┌─── 中断消费者（写设备驱动的人）──────────────────────────┐
   │  include/linux/interrupt.h                              │
   │  request_irq / free_irq / enable_irq / irq_set_affinity  │
   └────────────────────────┬───────────────────────────────┘
                            │
   ┌────────────────────────▼───────────────────────────────┐
   │              irq 核心（kernel/irq/）                     │
   │   irq_desc  ·  irq_domain  ·  14 个流处理器              │
   └──────────┬───────────────────────────┬─────────────────┘
              │                           │
   ┌──────────▼──────────┐  ┌─────────────▼─────────────────┐
   │ 中断控制器驱动        │  │ 域的提供者                     │
   │ include/linux/irq.h  │  │ include/linux/irqdomain.h      │
   │ struct irq_chip      │  │ struct irq_domain_ops          │
   └──────────┬──────────┘  └───────────────────────────────┘
              ▼
      真实硬件（GICD / GICR / ICC_*_EL1 / ITS）
```

| 你在写 | 头文件 | 01 篇的哪几节 |
|---|---|---|
| 设备驱动（消费中断） | `include/linux/interrupt.h` + `include/linux/of_irq.h` | §1 §2 §3 |
| 中断控制器驱动 | `include/linux/irq.h` + `include/linux/irqchip.h` | §4 |
| 域（通常和上一条同一个驱动） | `include/linux/irqdomain.h` | §5 |

### §3.2 五个必须分清的对象

```
   一条中断（virq = 42）
   ┌─── struct irq_desc ─────────────────────────────────────┐
   │  ① irq_common_data   ← 每 virq 一份，全层共享         │
   │  ② irq_data（第 0 层）                                    │
   │       └─ parent_data ─▶ irq_data（第 1 层）─▶ …           │
   │  ③ handle_irq  ──▶ 流处理器（一个，控制器驱动写）        │
   │  ④ action ──▶ irqaction ─next─▶ irqaction （一条链，   │
   │                                    设备驱动写）           │
   │  lock（raw_spinlock_t）· depth · wake_depth · kstat_irqs   │
   └──────────────────────────────────────────────────────────┘

   ⑤ struct irq_domain ── ops / host_data / parent / revmap
```

| # | 对象 | 一句话 | 详见 |
|---|---|---|---|
| ① | `irq_common_data` | 亲和性、状态位、`handler_data` —— **全层共享** | 02 §5.3 |
| ② | `irq_data` | **每层一份**：`hwirq` / `chip` / `domain` / `chip_data` 各层独立，`irq` 相同 | 02 §5.2 |
| ③ | 流处理器 | 处理**硬件确认协议**（mask/ack/eoi），控制器驱动选 | 01 §4.3 · 08 §3 |
| ④ | `irqaction` | 处理**设备逻辑**，设备驱动写；共享中断时成链 | 02 §5.6 |
| ⑤ | `irq_domain` | hwirq ↔ virq 的翻译器 | 02 §5.5 · 08 §1 |

### §3.3 五组最容易搞错的关系（02 §1.0 的浓缩）

| # | 错误理解 | 正确的 |
|---|---|---|
| A | hwirq 和 virq 是简单双射 | 层级域里**一个 virq 有多个 hwirq**（08 §1） |
| B | **两级中断控制器就是层级域** | **绝大多数是级联** —— 判据：这一层要不要有自己的 `irq_chip` 回调作用在同一个 virq 上（08 §2） |
| C | `irq_desc` 和 `irq_data` 是 1:1 | 内嵌 + `parent_data` 链（02 §1.0.C） |
| D | `irq_chip` 是"一个中断控制器" | 它是**一张函数表**（33 个回调），通常 `static const`，实例数据在 `chip_data` 里（02 §1.0.D） |
| E | **"中断处理函数"是一个东西** | 它同时指**流处理器**和**设备 handler**（02 §1.0.E · 08 §3） |

### §3.4 四组 flag（02 §4 的浓缩）

| 前缀 | 作用在哪个结构上 | 例子 | 谁设 |
|---|---|---|---|
| `IRQ_*` | `irq_desc.status_use_accessors` | `IRQ_NOREQUEST` · `IRQ_DISABLE_UNLAZY` | 控制器驱动（`irq_set_status_flags()`） |
| `IRQD_*` | `irq_common_data.state_use_accessors` | `IRQD_LEVEL` · `IRQD_IRQ_MASKED` · `IRQD_SINGLE_TARGET` | 核心 + 控制器驱动 |
| `IRQF_*` | `irqaction.flags` | `IRQF_SHARED` · `IRQF_ONESHOT` | **消费者**（`request_irq()` 的参数） |
| `IRQCHIP_*` | `irq_chip.flags` | `IRQCHIP_SKIP_SET_WAKE` · `IRQCHIP_EOI_THREADED` | 控制器驱动 |
| `IRQS_*` | `irq_desc.istate`（内部别名） | `IRQS_ONESHOT` · `IRQS_PENDING` | **只有 `kernel/irq/` 内部** |

⚠️ **这五组极易混淆** —— 判断办法就是"它作用在哪个结构上"。
debugfs 的 `irqs/<N>` 会把四组一次全部译成人话（07 §4.3）。

---

## §4 数据结构速览

### §4.1 `struct irq_desc`（`include/linux/irqdesc.h:55`）

| 分组 | 字段 | 要点 |
|---|---|---|
| 层级 | `irq_common_data`（`:56`）· `irq_data`（`:57`） | 都是**内嵌** |
| 分发 | `handle_irq`（`:59`）· `action`（`:60`） | 两条不同的路 |
| 状态 | `status_use_accessors`（`:61`）· `core_internal_state__do_not_mess_with_it`（`:62`） | 后者的名字就是文档 |
| 计数 | `depth`（`:63`）· `wake_depth`（`:64`） | `enable_irq()` / `enable_irq_wake()` 都是计数式（08 §4） |
| 统计 | `kstat_irqs`（`:58`）· `tot_count`（`:65`） | `/proc/interrupts` 读它 |
| 乱中断 | `irq_count`（`:66`）· `last_unhandled`（`:67`）· `irqs_unhandled`（`:68`） | `/proc/irq/N/spurious` 读它（07 §7.2） |
| 线程 | `threads_handled`（`:69`）· `threads_oneshot` · `threads_active` | `threads_oneshot` 是**位图**（08 §6） |
| 锁 | `lock`（`:71`，**`raw_spinlock_t`**）· `request_mutex`（`:101`） | 锁序：`request_mutex` → `chip_bus_lock` → `lock`（03 §B1.2） |

### §4.2 `struct irq_data`（`include/linux/irq.h:179`）—— 只有 8 个字段

| 字段 | 层间关系 |
|---|---|
| `irq`（`:181`） | **每层相同**（所以用户态只看到一个中断号） |
| `hwirq`（`:182`） | **每层不同** |
| `common`（`:183`） | **全部指向同一处**（所以亲和性只有一份） |
| `chip`（`:184`）· `domain`（`:185`）· `chip_data`（`:189`） | 每层独立 |
| `parent_data`（`:187`） | 层级域的脊椎 |
| `mask`（`:180`） | generic-chip 用的预算位掩码 |

**这张表就是层级域的全部**（02 §5.2）。

### §4.3 `struct irq_chip`（`include/linux/irq.h:488`）—— 名字 + 33 回调 + flags

| 组 | 回调 | 必须实现？ |
|---|---|---|
| 确认协议 | `irq_ack` / `irq_mask` / `irq_mask_ack` / `irq_unmask` / `irq_eoi` | 至少 mask+unmask 或 eoi（看用哪个流处理器） |
| 生命周期 | `irq_startup` / `irq_shutdown` / `irq_enable` / `irq_disable` | 可选（不填退化成 mask/unmask） |
| 配置 | `irq_set_affinity` / `irq_set_type` / `irq_set_wake` / `irq_retrigger` | 不填 ⇒ 消费者拿 `-ENOSYS` |
| 慢总线 | `irq_bus_lock` / `irq_bus_sync_unlock` | **I2C/SPI 扩展器必须** |
| MSI | `irq_compose_msi_msg` / `irq_write_msi_msg` | MSI 域 |
| IPI | `ipi_send_single` / `ipi_send_mask` | IPI 域（arm64 不走） |
| NMI | `irq_nmi_setup` / `irq_nmi_teardown` | `request_nmi()` |

### §4.4 `struct irq_domain`（`include/linux/irqdomain.h:164`）—— 变长

| 字段 | 要点 |
|---|---|
| `ops`（`:167`） | `xlate`+`map`（非层级）**或** `translate`+`alloc`+`free`（层级），**二选一** |
| `host_data`（`:168`） | 驱动私有 |
| `parent`（`:178`） | ⚠️ **`parent` 是"更靠近硬件"的那一层**（08 §8 的方向陷阱） |
| `revmap_size`（`:183`）· `revmap_tree`（`:184`）· `revmap[]`（`:186`） | 四种反向映射是这三个字段的四种参数组合（02 §5.5） |
| `fwnode`（`:173`）· `bus_token`（`:174`） | 域的身份 |

### §4.5 全局状态（只有五个）

| 变量 | 位置 | 保护 |
|---|---|---|
| `allocated_irqs`（virq 位图） | `kernel/irq/irqdesc.c:134` | `sparse_irq_lock` |
| `irq_desc_tree`（radix tree） | `kernel/irq/irqdesc.c:347` | 读侧 RCU |
| `irq_desc[NR_IRQS]`（静态数组） | `kernel/irq/irqdesc.c:557` | **arm64 不走这条** |
| `irq_domain_list` | `kernel/irq/irqdomain.c:23` | `irq_domain_mutex` |
| `irq_default_affinity` | `kernel/irq/manage.c:144` | 无专门锁 |

---

## §5 机制速览

### §5.1 十条一句话

| # | 机制 | 一句话 | 详见 |
|---|---|---|---|
| 1 | **控制器初始化** | `of_irq_init()` 在**设备模型之前**按拓扑序调 `IRQCHIP_DECLARE` 的回调 | 03 §A2 · 08 §9 |
| 2 | **域与映射** | 消费者取中断号那一刻才建映射、才分配 `irq_desc` | 03 §A4 |
| 3 | **申请** | `__setup_irq()` 检查 flags 兼容性、建线程、挂链、使能 | 03 §A5 |
| 4 | **分发** | arch → `gic_handle_irq` → 域查表 → **流处理器** → **action 链** | 03 §A6 |
| 5 | **线程化** | `IRQ_WAKE_THREAD` → SCHED_FIFO 线程 → `irq_finalize_oneshot()` unmask | 03 §A7 |
| 6 | **lazy disable** | `disable_irq()` 默认**不真的关硬件**，中断来了才 mask | 03 §B3 · 08 §5 |
| 7 | **层级域两阶段** | 分配自顶向下，**激活自底向上** | 03 §B4 · 08 §8 |
| 8 | **共享与乱中断** | `retval \|= res`，未处理率过高 ⇒ `nobody cared` | 03 §B5 · 08 §7 |
| 9 | **亲和性** | 沿层级往下传到硬件；`smp_affinity` ≠ `effective_affinity` | 03 §A9 · 04 §5.2 |
| 10 | **CPU 热插拔** | 普通中断迁走；托管中断被关闭并打 `IRQD_MANAGED_SHUTDOWN` | 03 §A10 |

### §5.2 分发路径上"故意不持锁"的那一段

| 位置 | 动作 |
|---|---|
| `kernel/irq/handle.c:207` | `irqd_set(IRQD_IRQ_INPROGRESS)` |
| `kernel/irq/handle.c:208` | **`raw_spin_unlock(&desc->lock)`** |
| `kernel/irq/handle.c:210` | 跑 action 链（你的 handler） |
| `kernel/irq/handle.c:212` | `raw_spin_lock(&desc->lock)` |

⇒ **并发保护从"锁"换成了状态位**，
入口处由 `irq_may_run()`（`kernel/irq/chip.c:504`）检查（03 §B1.3）。

### §5.3 三个"没有日志"的坑

| 现象 | 原因 | 判据 |
|---|---|---|
| 中断永远不来，dmesg 干净 | DTS 的 hwirq / `interrupt-parent` / MSI DeviceID 错 | `cat /proc/interrupts` 看 hwirq 列（06 §8.4） |
| `enable_irq()` 少调一次 ⇒ 中断永久沉默 | `depth` 没归零 | `cat /sys/kernel/debug/irq/irqs/<N>` 看 `IRQD_IRQ_DISABLED` |
| `disable_irq()` 之后硬件还在发中断 | **lazy disable** | 需要 `IRQ_DISABLE_UNLAZY`（08 §5） |

### §5.4 三个"必须成对"的东西

| 成对 | 不成对的后果 |
|---|---|
| `disable_irq()` ↔ `enable_irq()` | 多解报 `Unbalanced enable`；少解 ⇒ **静默沉默** |
| `enable_irq_wake()` ↔ `disable_irq_wake()` | 同上（独立的 `wake_depth`） |
| `chained_irq_enter()` ↔ `chained_irq_exit()` | **中断只来一次**（不 EOI）或**中断风暴**（不 unmask）（05 §3.4） |

---

## §6 端到端调用路径

### §6.1 路径一：启动（控制器就位）

```
   start_kernel()
     ├─ early_irq_init()                    kernel/irq/irqdesc.c:526（sparse 版）
     │    · allocated_irqs 位图 · irq_desc_tree · sparse_irq_lock
     │
     └─ init_IRQ() → irqchip_init() → of_irq_init(__irqchip_of_table)
          │  ① 扫 DT 找匹配的节点
          │  ② 按 interrupt-parent 排拓扑序
          │  ③ 逐个调 init 回调
          ▼
        gic_of_init(node, parent)            drivers/irqchip/irq-gic-v3.c:2185
          └─ gic_init_bases()                drivers/irqchip/irq-gic-v3.c:1930
               ├─ irq_domain_create_tree(…, &gic_irq_domain_ops, …)
               │       drivers/irqchip/irq-gic-v3.c:1972
               └─ set_handle_irq(gic_handle_irq)
                       drivers/irqchip/irq-gic-v3.c:1998
```

**此刻还没有任何 `irq_desc`** —— 它们要等映射建立才分配（§6.2）。

### §6.2 路径二：设备驱动取中断号（映射建立）

```
   你的 probe
     └─ platform_get_irq(pdev, 0)
          └─ of_irq_get(np, 0)                drivers/of/irq.c:459
               ├─ of_irq_parse_one()          drivers/of/irq.c:358
               │    · 读 interrupts-extended 或 interrupt-parent+interrupts
               ├─ irq_find_host() 找不到 ⇒ -EPROBE_DEFER
               └─ irq_create_fwspec_mapping() kernel/irq/irqdomain.c:807
                    ├─ ops->translate()       drivers/irqchip/irq-gic-v3.c:1530
                    │    <GIC_SPI 164 …> → hwirq 196
                    ├─ irq_find_mapping() 已有就返回
                    └─【层级】irq_domain_alloc_irqs()
                         ├─ 分配 irq_desc      kernel/irq/irqdesc.c:791
                         ├─ 每层建 irq_data    kernel/irq/irqdomain.c:1329
                         └─ ops->alloc()       drivers/irqchip/irq-gic-v3.c:1603
                              └─ irq_domain_set_info() kernel/irq/irqdomain.c:1409
```

### §6.3 路径三：`request_threaded_irq()`

```
   request_threaded_irq()                    kernel/irq/manage.c:2138
     ├─ 参数检查（IRQF_SHARED 必须带 dev_id 等）
     ├─ kzalloc(struct irqaction)
     ├─ irq_chip_pm_get()
     └─ __setup_irq()                        kernel/irq/manage.c:1497
          ├─ !desc ⇒ -EINVAL                 kernel/irq/manage.c:1503-1504
          ├─ chip == no_irq_chip ⇒ -ENOSYS  kernel/irq/manage.c:1506-1507
          ├─ try_module_get ⇒ -ENODEV        kernel/irq/manage.c:1508-1509
          ├─ irq_setup_forced_threading()    kernel/irq/manage.c:1358
          ├─ mutex_lock(request_mutex) → chip_bus_lock → raw_spin_lock
          ├─ 遍历 action 链查 SHARED/触发类型/ONESHOT ⇒ 不兼容 -EBUSY
          │       kernel/irq/manage.c:1671
          ├─ 首个 action ⇒ __irq_set_trigger() + irq_startup()
          └─ 挂链 + 解锁 + 等线程就绪
```

### §6.4 路径四：一次中断（热路径）

```
   ① GIC 拉高 IRQ ⇒ arch 异常向量
   ② generic_handle_arch_irq()               kernel/irq/handle.c:232
   ③ gic_handle_irq()                        drivers/irqchip/irq-gic-v3.c:834
        · mrs ICC_IAR1_EL1（拿 INTID + ack）
   ④ generic_handle_domain_irq(domain, hwirq) kernel/irq/irqdesc.c:706
        └─ __irq_resolve_mapping()            kernel/irq/irqdomain.c:949
   ⑤ desc->handle_irq(desc)   流处理器（GIC 用 handle_fasteoi_irq）
        handle_fasteoi_irq()                  kernel/irq/chip.c:689
          · raw_spin_lock(&desc->lock)        kernel/irq/chip.c:693
          · irq_may_run(desc)?                kernel/irq/chip.c:695
          · !action || disabled ⇒ PENDING+mask+out  kernel/irq/chip.c:704-708
          · IRQS_ONESHOT ⇒ mask_irq()         kernel/irq/chip.c:711-712
          · handle_irq_event(desc)             kernel/irq/chip.c:714
          · cond_unmask_eoi_irq()              kernel/irq/chip.c:716
   ⑥ handle_irq_event()                       kernel/irq/handle.c:202
        · istate &= ~IRQS_PENDING              kernel/irq/handle.c:206
        · irqd_set(IRQD_IRQ_INPROGRESS)        kernel/irq/handle.c:207
        · raw_spin_unlock(&desc->lock)     kernel/irq/handle.c:208
        · __handle_irq_event_percpu()          kernel/irq/handle.c:139
             for_each_action_of_desc:          kernel/irq/handle.c:147
               trace_irq_handler_entry()       kernel/irq/handle.c:157
               res = action->handler(…)   kernel/irq/handle.c:158
               trace_irq_handler_exit()        kernel/irq/handle.c:159
               WARN_ONCE(!irqs_disabled(), …)  kernel/irq/handle.c:161
               IRQ_WAKE_THREAD ⇒ __irq_wake_thread()  kernel/irq/handle.c:176
               retval |= res                   kernel/irq/handle.c:183
        · note_interrupt(desc, retval)         kernel/irq/handle.c:198
        · raw_spin_lock(&desc->lock)           kernel/irq/handle.c:212
```

### §6.5 路径五：线程化的那一半

```
   irq_thread()                              kernel/irq/manage.c:1286
     ├─ sched_set_fifo(current)              kernel/irq/manage.c:1296  实时优先级
     ├─ irq_thread_check_affinity()          kernel/irq/manage.c:1307
     └─ while (!irq_wait_for_interrupt())    kernel/irq/manage.c:1309
             handler_fn(desc, action)        kernel/irq/manage.c:1314
                └─ irq_thread_fn()            kernel/irq/manage.c:1200
                     · action->thread_fn(…)   你的代码
                     · irq_finalize_oneshot() kernel/irq/manage.c:1074 这里 unmask
             wake_threads_waitq(desc)        kernel/irq/manage.c:1318
```

### §6.6 路径六：拆除

```
   free_irq()                                kernel/irq/manage.c:2018
     └─ __free_irq()                         kernel/irq/manage.c:1866
          ├─ WARN(in_interrupt(), "Trying to free IRQ %d from IRQ context!")
          │       kernel/irq/manage.c:1872
          ├─ mutex_lock(request_mutex) → chip_bus_lock → raw_spin_lock
          │       kernel/irq/manage.c:1874-1876
          ├─ 按 dev_id 找到并摘链              kernel/irq/manage.c:1882-1884
          ├─ 最后一个 ⇒ irq_shutdown_and_deactivate()  kernel/irq/chip.c:322
          ├─ synchronize_hardirq()        kernel/irq/manage.c:98
          ├─ kthread_stop() + put_task_struct()
          └─ module_put()
```

---

## §7 子系统关系

### §7.1 irq 依赖谁

```
                    ┌──────────────┐
                    │  irq 核心     │
                    └──────┬───────┘
       ┌───────────┬───────┼────────┬───────────┬──────────┐
       ▼           ▼       ▼        ▼           ▼          ▼
    arch 异常     of/     radix    kthread    debugfs   devres
    向量          irq.c   tree     （线程化）  procfs    （devm_*）
   （必需）      （DT）   （sparse）
```

| 依赖 | 强度 | 说明 |
|---|---|---|
| arch 异常向量 | 必需 | `set_handle_irq()` 把 arch 入口指向 irqchip（`kernel/irq/handle.c:218`） |
| `drivers/of/irq.c` | 实际必需（DT 平台） | **irq 核心自己不读 DT**（06 §1.1） |
| radix tree | `CONFIG_SPARSE_IRQ` 下必需 | `irq_desc_tree` + 域的 `revmap_tree` |
| kthread | 线程化中断必需 | `irq_thread()` |
| procfs / debugfs | 可选 | 04 §5 §6 |
| devres | 可选 | **一级控制器用不了**（08 §9） |

### §7.2 谁依赖 irq

**几乎所有设备驱动** —— 这是内核里被依赖最广的子系统之一。
特别值得点名的四个：

| 使用者 | 关系 | 边界 |
|---|---|---|
| **gpiolib** | 大量 GPIO 控制器同时是二级中断控制器 | gpiolib 提供 `girq` 那套辅助设施（`girq->handler` / `girq->threaded`，05 §4.3） |
| **PCI / MSI** | 通过 MSI 域 | 04 §2 |
| **regmap-irq** | 给"寄存器长得规整的 I2C/SPI 扩展器"再包一层 | 它内部用 `irq_domain` + `irq_bus_lock` |
| **cpuidle governor** | 读 `irq_timings_next_event()` 预测下一次中断 | 04 §8 |

### §7.3 和 gpiolib 的关系最需要小心

| 事实 | 说明 |
|---|---|
| 大多数 GPIO 中断是**级联**不是层级 | 08 §2 |
| gpiolib 以前会**偷改** `irq_chip` 的回调 | 6.x 起用 `IRQCHIP_IMMUTABLE`（`include/linux/irq.h:570`）禁止 |
| 慢总线上的 GPIO 扩展器要 `girq->threaded = true` | 05 §4.3 |
| `GPIOCHIP_IRQ_RESOURCE_HELPERS` 宏 | 配合 `IRQCHIP_IMMUTABLE` 的新写法 |

### §7.4 ⚠️ 和 softirq / tasklet 的关系：**只是同住一个头文件**

`include/linux/interrupt.h` 里既有 `request_irq()` 也有
`open_softirq()` / `tasklet_*` —— 但**它们是两套完全独立的机制**：

| | 硬中断（本套文档） | softirq / tasklet |
|---|---|---|
| 代码在哪 | `kernel/irq/` | `kernel/softirq.c` |
| tracepoint | `irq:irq_handler_*` | `irq:softirq_*`（同一个 `include/trace/events/irq.h`） |
| 本套覆盖吗 | **覆盖** | **不覆盖**（99 §2.7） |

⚠️ 本树那处 PREEMPT_RT 补丁**改的正是 softirq 那一段**（99 §1.1）——
所以它**不影响本套文档的任何结论**，只影响 `interrupt.h` 中该点之后的行号。

### §7.5 对比：irq 是框架型，不是总线型

| | i2c / spi（总线型） | remoteproc（框架型） | irq（框架型，但更特殊） |
|---|---|---|---|
| `bus_type` | 有 | 没有 | 没有 |
| 控制器有 `struct device` 吗 | 有 | 有 | **一级没有、二级有**（08 §9） |
| 读 DT 属性 | 读 | 读 1 条 | **0 条** |
| 消费者怎么用 | `i2c_transfer()` | 写 rpmsg 驱动 | `request_irq()` + 一个整数 |
| 导出符号 | —— | 33 | **151** |

---

## §8 导读：我该看哪一篇

### §8.1 按问题找篇目

| 我想知道 | 去哪篇 |
|---|---|
| 有哪些文件、151 个 API 分别干什么、错误码什么意思 | **01** |
| 结构体长什么样、五组关系、四组 flag、全局变量与锁 | **02** |
| 启动/映射/申请/分发/线程化/拆除的完整时间线，锁序，设计债 | **03** |
| generic-chip / MSI / IPI / 乱中断 / procfs / debugfs 这十一块 | **04** |
| 我要动手写代码（消费者 / 一级 / 级联 / 慢总线 / 层级域） | **05** |
| DTS 怎么写、hwirq 怎么换算、配错了怎么定位 | **06** |
| 编译开关、5 个 tracepoint、`/proc/interrupts`、故障注入、错误码分诊 | **07** |
| 「我理解不了 X」「为什么是这样」 | **08** |
| 这些结论怎么来的、哪些没验证 | **99** |

### §8.2 按角色推荐阅读顺序

| 角色 | 顺序 |
|---|---|
| **第一次接触** | 00 §2（硬件）→ 08 → 02 §1.0 → 03 §A |
| **只写设备驱动** | 00 §2.3/§2.6 → 05 §1 → 01 §1/§2 → 07 §8 |
| **要写一级中断控制器** | 00 §2 全部 → 08 §9 → 05 §2 → 03 §A2/§A3 → 06 |
| **要写级联控制器** | 08 §2 → 05 §3 → 01 §4.3（选流处理器） |
| **要写慢总线控制器** | 08 §2 → 05 §4 → 02 §5.4（`irq_bus_lock`） |
| **要写层级域 / MSI** | 08 §2/§8 → 04 §2 → 05 §5 → 03 §B4 |
| **在排查问题** | 07 §8 速查 → 07 §6 错误码 → 06 §8/§9 |
| **要评审代码** | 03 Part C（设计债）→ 07 §2.1（WARN 密度）|
| **要复核结论** | 99 |

### §8.3 只记七条的话

1. **hwirq 是域内的、virq 是全局的**；层级域里一个 virq 有多个 hwirq（§3.3.A · 08 §1）。
2. **级联 ≠ 层级**。判据：这一层要不要有自己的 `irq_chip` 回调作用在同一个 virq 上。
   **绝大多数 GPIO 控制器是级联**（§3.3.B · 08 §2）。
3. **"中断处理函数"同时指流处理器（控制器写、一个）和设备 handler（设备驱动写、一条链）**（§3.3.E · 08 §3）。
4. **DTS 里的 cell 1 是段内偏移，不是 hwirq**：`<GIC_SPI 164 …>` = hwirq **196**（§2.3 · 06 §3.1）。
5. **`disable_irq()` 默认不真的关硬件**（lazy disable）；要立即生效设 `IRQ_DISABLE_UNLAZY`（§5.3 · 08 §5）。
6. **`smp_affinity`（你要求的）≠ `effective_affinity`（硬件实际的）**；
   GIC 的 SPI 只能送给一个 CPU（§2.4 · 04 §5.2）。
7. **调 irq 问题的第一件事是打开 `CONFIG_GENERIC_IRQ_DEBUGFS`**（07 §1.1）——
   然后 `cat /proc/interrupts` 看 hwirq 列、`cat /sys/kernel/debug/irq/irqs/<N>` 看层级和四组 flag、
   `echo trigger` 把问题切成两半（07 §8.2）。

---

**取证与未验证项**：见 99 篇。
