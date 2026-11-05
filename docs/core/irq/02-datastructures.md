---
title: 数据结构
---

# irq 02：核心数据结构与它们的关系

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §1.0 先分清五组关系

> 图画得再全，也补不上一个没被显式否掉的错误类比。本节先否掉五个。

### §1.0.A `hwirq` 和 `virq`：不是"两个编号系统的简单双射"

**错误类比**：「hwirq 是硬件编号，virq 是 Linux 编号，两者一一对应，就像物理页帧号和虚拟页号」。

**为什么错**：

| 断言 | 对不对 | 依据 |
|---|---|---|
| 一个 hwirq 只能对应一个 virq | **在同一个域里对** | 域的反向映射表是单值的 |
| 一个 virq 只对应一个 hwirq | **错** —— 层级域里**每层一个 hwirq** | `struct irq_data` 里既有 `hwirq` 又有 `parent_data`（`include/linux/irq.h:182`、`include/linux/irq.h:187`） |
| hwirq 全局唯一 | **错** —— hwirq **只在它所属的域内唯一** | 两个 GPIO 控制器都可以有 hwirq 5 |
| virq 全局唯一 | **对** | `allocated_irqs` 位图是全局的（`kernel/irq/irqdesc.c:134`） |
| virq 0 是合法的 | **错** | 0 被用作"失败"返回值（01 §3.2、§10.4） |

**正确的说法**：

```
   virq 是"全局的、Linux 内部的、一维的"中断标识；
   hwirq 是"域内的、硬件的、可能多层的"中断标识。
   (域, hwirq) → virq 是多对一的定义域，virq → (域, hwirq) 在层级域里是一对多。
```

**`hwirq` 的类型是 `irq_hw_number_t`**，也就是 `unsigned long`
（`include/linux/types.h:165` 定义了这个 typedef；字段本身在 `include/linux/irq.h:182`），因为 MSI 域会把
device-ID + 事件号编码进一个 hwirq；而 `virq` 是 `unsigned int`（`include/linux/irq.h:181`）。
⇒ **两者位宽不同**，这本身就说明它们不是同一类东西。

### §1.0.B 「级联」和「层级」：本子系统最容易混的一组

| | **级联**（chained） | **层级**（hierarchy） |
|---|---|---|
| 一句话 | 父中断的 handler 里**手工分发**子中断 | 一个 virq 在**每一层**都有自己的 `irq_data` |
| 有几个 virq | **两个以上**：父控制器占一个 virq，每个子中断各占一个 | **一个** virq |
| 有几个 `irq_desc` | 每个 virq 一个 | **一个** |
| 有几个 `irq_data` | 每个 `irq_desc` 一个 | **每层一个**，用 `parent_data` 串起来 |
| 怎么建 | `irq_set_chained_handler_and_data()` | `irq_domain_create_hierarchy()` + `ops->alloc` |
| 父层怎么被调用 | 子中断的 handler **不经过**父 virq 的 `irq_chip` | 上层的 `irq_chip` 回调里调 `irq_chip_*_parent()` |
| arm64 上的例子 | GPIO 控制器挂在 GIC 的一条 SPI 上 | **GIC ITS（MSI）挂在 GIC v3 之上** |
| 中断号数量关系 | 父 1 : 子 N（N 个额外 virq） | 1 : 1（同一个 virq） |

**为什么"级联的父 virq 不能再被 `request_irq()`"**：
`__irq_do_set_handler()` 在 `is_chained` 为真时做了三件事
（`kernel/irq/chip.c:1036-1038`）：`set_noprobe` / **`set_norequest`** / `set_nothread`，
并把 `desc->action` 直接指向内核自己的 `chained_action`（`kernel/irq/chip.c:1039`），
然后**立刻启用中断**（`kernel/irq/chip.c:1041` 的 `irq_activate_and_startup()`）。

而 `request_irq()` 的第一道门是 `can_request_irq()`（`kernel/irq/manage.c:928`），
它检查 `irq_settings_can_request()` —— 也就是 `_IRQ_NOREQUEST` 位
（`kernel/irq/settings.h:100-102`）。
⇒ **级联入口被 `set_norequest` 之后，`request_irq()` 必然失败，这是设计而不是 bug。**

⚠️ 反过来也要注意：级联入口**不需要**也**不能**再 `request_irq()` 去使能它 ——
`kernel/irq/chip.c:1041` 已经使能过了。

### §1.0.C `irq_desc` 和 `irq_data`：内嵌 + 链，不是 1:1

```
   ┌──── struct irq_desc ────────────────────────┐
   │  irq_common_data   ← 每个 virq 一份，全层共享 │
   │  irq_data          ← 内嵌的"第 0 层"       │
   │     └─ parent_data ─────▶ irq_data（第 1 层）│
   │                              └─ parent_data ─▶ …
   │  handle_irq        ← 流处理器                │
   │  action            ← 设备 handler 链表        │
   │  lock                                        │
   └──────────────────────────────────────────────┘
```

| 关系 | 依据 |
|---|---|
| `irq_common_data` **内嵌**在 `irq_desc` 里 | `include/linux/irqdesc.h:56` |
| 第 0 层的 `irq_data` **内嵌**在 `irq_desc` 里 | `include/linux/irqdesc.h:57` |
| 每层的 `irq_data` 都指向**同一个** `irq_common_data` | `include/linux/irq.h:183` 的 `common` 字段 |
| 上层往下层的链 | `include/linux/irq.h:187` 的 `parent_data`（只在 `CONFIG_IRQ_DOMAIN_HIERARCHY` 下存在） |

**`common` 这个字段是关键**：亲和性掩码、状态位、`handler_data`、`msi_desc`
都住在 `irq_common_data` 里，**全层共享一份**（`include/linux/irq.h:147` 起）。
⇒ 所以"改亲和性"这个动作虽然要沿层级往下传到硬件，
但**记录在哪一层都是同一份数据**。

### §1.0.D `irq_chip` 不是"一个中断控制器"

**错误类比**：「`struct irq_chip` 就是一个中断控制器的对象，一个 GIC 一个 `irq_chip`」。

**为什么错**：

| 断言 | 实际 |
|---|---|
| 一个控制器一个 `irq_chip` | 一个控制器可以有**多个** `irq_chip`（GIC v3 就有 SPI/PPI 用的和 LPI 用的） |
| `irq_chip` 里存着控制器的状态 | **错** —— `irq_chip` 通常是 `static const`，**没有实例数据**。实例数据在 `chip_data` 里 |
| `irq_chip` 是"一层" | 接近了：它是**"某一层对某一条中断的操作集"** |

6.1 里 `irq_set_chip()` 的形参已经是 `const struct irq_chip *`
（01 §4.2）—— 这就是"`irq_chip` 是纯操作集"的机器可证形式。

**正确的说法**：`irq_chip` 是**一张函数表**（33 个回调 + 名字 + flags，
`include/linux/irq.h:488` 起）。
"哪个控制器"由 `irq_data->chip_data` 决定，"哪一层"由 `irq_data` 自己在链里的位置决定。

### §1.0.E 流处理器和设备 handler：两个完全不同的东西

| | **流处理器**（flow handler） | **设备 handler** |
|---|---|---|
| 字段 | `irq_desc.handle_irq`（`include/linux/irqdesc.h:59`） | `irqaction.handler` |
| 类型 | `irq_flow_handler_t`（收 `struct irq_desc *`） | `irq_handler_t`（收 `int irq, void *dev_id`） |
| 谁写 | **中断控制器驱动** | **设备驱动** |
| 怎么设 | `irq_set_chip_and_handler()` | `request_irq()` |
| 有几个 | **一个** | **一条链**（`irqaction.next`，共享中断时多个） |
| 干什么 | 处理**硬件确认协议**（mask / ack / eoi）并调用 handler 链 | 处理**设备逻辑** |
| 返回值 | void | `irqreturn_t` |
| 典型值 | `handle_fasteoi_irq` / `handle_level_irq` / `handle_edge_irq` | 你写的那个函数 |

**两者的接合点**：流处理器内部调 `handle_irq_event()` →
`__handle_irq_event_percpu()`（`kernel/irq/handle.c`），
后者遍历 `desc->action` 链，逐个调 `action->handler`。
⇒ 03 篇的核心数据流图就是这条链。

⚠️「中断处理函数」这个中文词**同时指这两个东西**，是本子系统最大的术语坑。
本套文档一律区分为「**流处理器**」和「**设备 handler**」。

---

## §2 核心抽象与消费者的关系

### §2.1 irq 没有 `bus_type`，它有三类使用者

```
        ┌───────────────── 消费者（写设备驱动的人）──────────────────┐
        │  request_irq / free_irq / enable_irq / irq_set_affinity   │
        │  头文件：include/linux/interrupt.h                        │
        └────────────────────────────┬─────────────────────────────┘
                                     │
        ┌────────────────────────────▼─────────────────────────────┐
        │              irq 核心（kernel/irq/）                       │
        │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐  │
        │  │  irq_desc   │  │  irq_domain  │  │  流处理器 7 个   │  │
        │  │（每 virq 一）│  │（hwirq↔virq）│  │  handle_*_irq   │  │
        │  └─────────────┘  └──────────────┘  └─────────────────┘  │
        └───────────┬──────────────────────────────┬───────────────┘
                    │                              │
     ┌──────────────▼───────────┐   ┌──────────────▼───────────────┐
     │ 中断控制器驱动             │   │ 域的提供者                    │
     │ 填 struct irq_chip        │   │ 填 struct irq_domain_ops      │
     │ 头文件：include/linux/    │   │ 头文件：include/linux/         │
     │        irq.h              │   │        irqdomain.h            │
     │ 例：drivers/irqchip/      │   │ 通常和左边是同一个驱动        │
     │     irq-gic-v3.c          │   │                               │
     └───────────────────────────┘   └───────────────────────────────┘
                    │
                    ▼
              真实硬件（GIC distributor / redistributor / ITS）
```

**三类使用者用三个不同的头文件** —— 这是本子系统 API 面积大（151 个导出符号）的原因。
判断你该读哪个头文件：

| 你在写 | 头文件 | 01 篇的哪几节 |
|---|---|---|
| 设备驱动（消费中断） | `include/linux/interrupt.h` + `include/linux/of_irq.h` | §1 §2 §3 |
| 中断控制器驱动 | `include/linux/irq.h` + `include/linux/irqchip.h` | §4 |
| 域（通常和上一条同一个驱动） | `include/linux/irqdomain.h` | §5 §6 |

### §2.2 ⚠️ 和 platform 总线的关系：**不对称**

| 控制器类型 | 谁创建它 | 有 `struct device` 吗 | 能用 devres / `-EPROBE_DEFER` 吗 |
|---|---|---|---|
| 一级控制器（GIC） | `of_irq_init()`，**早于设备模型** | **没有** | **不能** |
| 二级控制器（GPIO / PCI / I2C 扩展器） | 正常的 platform / i2c / pci 驱动 | 有 | 能 |

⚠️ 这个不对称是 irq 子系统和 i2c/spi 最大的结构差异：
**它的"控制器"一部分在设备模型里、一部分在设备模型之外**。
⇒ 01 §8 那条"`irq_domain` 没有 devres 版本"就是这个不对称的直接后果。

判据：`IRQCHIP_DECLARE()` 登记的是 `__irqchip_of_table`，
被 `of_irq_init()` 遍历；而 platform 驱动登记的是 `platform_driver`，
被 `driver_register()` 处理。**两条完全独立的路。**

---

## §3 核心操作数据流：一次中断从硬件到设备驱动

### §3.1 全景图（arm64 / GIC v3）

```
  GIC 硬件拉高 IRQ 线
        │
        ▼
  arm64 异常向量 (arch/arm64/kernel/entry.S)
        │
        ▼
  handle_arch_irq  ← 由 set_handle_irq() 注册（GENERIC_IRQ_MULTI_HANDLER）
        │
        ▼
  gic_handle_irq()                    ← drivers/irqchip/irq-gic-v3.c
        │  读 ICC_IAR1_EL1 拿到 hwirq
        ▼
  generic_handle_domain_irq(domain, hwirq)
        │  irq_resolve_mapping() → irq_desc
        ▼
  desc->handle_irq(desc)              ← 流处理器，GIC v3 用 handle_fasteoi_irq
        │
        ├─ 【流处理器内部】
        │    ① 必要时 chip->irq_mask / irq_ack
        │    ② handle_irq_event(desc)
        │         └─ __handle_irq_event_percpu(desc)   ← kernel/irq/handle.c
        │              │
        │              └─ for (action = desc->action; action; action = action->next)
        │                     res = action->handler(irq, action->dev_id)   ← 你的代码
        │                     if (res == IRQ_WAKE_THREAD)
        │                            __irq_wake_thread(desc, action)
        │    ③ note_interrupt(desc, retval)          ← 乱中断记账
        │    ④ chip->irq_eoi / irq_unmask
        ▼
  返回异常向量，恢复上下文
```

| 关键位置 | 文件 |
|---|---|
| 流处理器（共 **14 个**，01 §4.3） | `kernel/irq/chip.c`（`handle_bad_irq` 在 `kernel/irq/handle.c`） |
| `__handle_irq_event_percpu()` | `kernel/irq/handle.c` |
| `note_interrupt()` | `kernel/irq/spurious.c` |
| `generic_handle_domain_irq()` | `kernel/irq/irqdesc.c` |
| `irq_resolve_mapping()` | `kernel/irq/irqdomain.c` |
| GIC v3 的入口 | `drivers/irqchip/irq-gic-v3.c` |

**这张图上有两次"查表"**：
① `hwirq → irq_desc`（域的反向映射）；
② `irq_desc → action 链`（共享中断时遍历）。
两次都在硬中断上下文里，所以①用 radix tree 或数组（不能用哈希）、②用简单链表。

### §3.2 线程化中断的分叉

```
  action->handler 返回 IRQ_WAKE_THREAD
        │
        ▼
  __irq_wake_thread(desc, action)
        │  设置 thread_mask，wake_up_process(action->thread)
        ▼
  ─────── 硬中断上下文结束（如果有 IRQF_ONESHOT，中断保持屏蔽）───────
        │
        ▼（调度器择机运行）
  irq_thread()                        ← kernel/irq/manage.c
        │
        ├─ action->thread_fn(irq, dev_id)   ← 你的线程处理函数
        │
        └─ irq_finalize_oneshot(desc, action)
             └─ 若 IRQF_ONESHOT：unmask 中断
```

**`IRQF_ONESHOT` 的作用就在这条路径的两端**：
硬中断结束时不 unmask，线程跑完才 unmask。
⇒ 没有它，电平触发的中断会在线程还没跑之前就再次触发（中断风暴）。
这就是 01 §1.4 里"注册 `thread_fn` 且 handler 为 NULL 时必须设 `IRQF_ONESHOT`"的原因。

### §3.3 层级域里一次 `irq_set_affinity()` 的传导

```
  irq_set_affinity(virq, mask)
        │
        ▼
  irq_desc.irq_data.chip->irq_set_affinity(&desc->irq_data, mask, false)
        │   ← 第 0 层（例：ITS 的 irq_chip）
        │
        └─ irq_chip_set_affinity_parent(data, dest, force)
               │  取 data->parent_data
               ▼
           parent_data->chip->irq_set_affinity(parent_data, mask, force)
               │   ← 第 1 层（例：GIC v3 的 irq_chip）
               ▼
           写 GICD_IROUTER / GICR 寄存器
```

**注意 `mask` 全程只有一份，`irq_data` 每层一个**。
掩码本身存在 `irq_common_data.affinity`（`include/linux/irq.h:147` 那个结构里），
全层共享（§1.0.C）。
⇒ 这就是层级域"上层负责语义、下层负责硬件"的具体形态。

---

## §4 命名约定速查

| 前缀 / 词 | 含义 | 例子 |
|---|---|---|
| `virq` | Linux 全局中断号 | `request_irq(virq, …)` |
| `hwirq` | 域内硬件中断号 | `generic_handle_domain_irq(d, hwirq)` |
| `irq_` | 消费者或通用 API | `irq_set_affinity()` |
| `irq_chip_*_parent` | 层级域里转发给父层 | `irq_chip_mask_parent()` |
| `irq_domain_*` | 域相关 | `irq_domain_add_linear()` |
| `irq_gc_*` | generic-chip 的现成回调 | `irq_gc_mask_set_bit()` |
| `handle_*_irq` | **流处理器**（不是设备 handler） | `handle_fasteoi_irq()` |
| `generic_handle_*` | 控制器驱动用来"往核心里分发"的入口 | `generic_handle_domain_irq()` |
| `__irq_*` | 核心内部（已持锁 / 全参数版） | `__irq_alloc_descs()` |
| `IRQ_*` | `irq_desc.status_use_accessors` 的位 | `IRQ_NOREQUEST` |
| `_IRQ_*` | 上面那些位在 `kernel/irq/settings.h` 里的内部别名 | `_IRQ_NOREQUEST` |
| `IRQD_*` | `irq_common_data.state_use_accessors` 的位 | `IRQD_LEVEL` |
| `IRQF_*` | `request_irq()` 的 flags | `IRQF_SHARED` |
| `IRQCHIP_*` | `irq_chip.flags` 的位 | `IRQCHIP_EOI_IF_HANDLED` |
| `IRQ_TYPE_*` | 触发类型 | `IRQ_TYPE_LEVEL_HIGH` |
| `IRQ_DOMAIN_FLAG_*` | `irq_domain.flags` 的位 | `IRQ_DOMAIN_FLAG_HIERARCHY` |

⚠️ **四组 flag 极易混淆**：`IRQ_*`（desc 状态）、`IRQD_*`（irq_data 状态）、
`IRQF_*`（申请标志）、`IRQCHIP_*`（chip 能力）。
判断办法：看它作用在哪个结构上 —— 这四组分别对应
`irq_desc.status_use_accessors`、`irq_common_data.state_use_accessors`、
`irqaction.flags`、`irq_chip.flags`。

---

## §5 结构体逐个看

### §5.1 `struct irq_desc`：每个 virq 一份

定义在 `include/linux/irqdesc.h:55`，末尾带 `____cacheline_internodealigned_in_smp`
（说明它是热路径数据，要避免伪共享）。

| 分组 | 字段 | 行号 | 要点 |
|---|---|---|---|
| 层级数据 | `irq_common_data` | `include/linux/irqdesc.h:56` | 内嵌，全层共享 |
| | `irq_data` | `include/linux/irqdesc.h:57` | 内嵌的第 0 层 |
| 统计 | `kstat_irqs` | `include/linux/irqdesc.h:58` | per-CPU 计数，`/proc/interrupts` 读它 |
| | `tot_count` | `include/linux/irqdesc.h:65` | 非 per-CPU 中断的快速总数 |
| 分发 | `handle_irq` | `include/linux/irqdesc.h:59` | **流处理器**（§1.0.E） |
| | `action` | `include/linux/irqdesc.h:60` | **设备 handler 链表头** |
| 状态 | `status_use_accessors` | `include/linux/irqdesc.h:61` | `IRQ_*` 位，**只能通过 `kernel/irq/settings.h` 的访问器读写** |
| | `core_internal_state__do_not_mess_with_it` | `include/linux/irqdesc.h:62` | 名字就是文档：驱动不许碰 |
| 计数 | `depth` | `include/linux/irqdesc.h:63` | `disable_irq()` 的嵌套计数 |
| | `wake_depth` | `include/linux/irqdesc.h:64` | `irq_set_irq_wake()` 的嵌套计数 |
| 乱中断检测 | `irq_count` / `last_unhandled` / `irqs_unhandled` | `include/linux/irqdesc.h:66-68` | `kernel/irq/spurious.c` 用 |
| 线程 | `threads_handled` / `threads_handled_last` | `include/linux/irqdesc.h:69-70` | 判断"线程到底干活了没" |
| | `threads_oneshot` / `threads_active` / `wait_for_threads` | —— | `IRQF_ONESHOT` 的 unmask 时机靠 `threads_oneshot` |
| 锁 | `lock` | `include/linux/irqdesc.h:71` | **`raw_spinlock_t`** —— 因为要在硬中断上下文持 |
| per-CPU | `percpu_enabled` / `percpu_affinity` | `include/linux/irqdesc.h:72-73` | —— |
| 亲和性（SMP） | `affinity_hint` / `affinity_notify` / `pending_mask` | —— | `pending_mask` 需要 `GENERIC_PENDING_IRQ` |
| PM | `nr_actions` / `no_suspend_depth` / `cond_suspend_depth` / `force_resume_depth` | —— | 四个计数配合 `IRQF_NO_SUSPEND` / `IRQF_COND_SUSPEND` |
| 可观测 | `dir`（procfs）/ `debugfs_file` / `dev_name` | —— | 条件编译 |
| sparse | `rcu` / `kobj` | —— | `CONFIG_SPARSE_IRQ` 下 `irq_desc` 是**动态分配 + RCU 释放**的 |
| 其它 | `request_mutex`（`include/linux/irqdesc.h:101`）· `parent_irq` · `owner` · `name` | —— | `request_mutex` 保护 `request_irq`/`free_irq` 的整个过程，**在 `desc->lock` 之外** |

**两个 `depth` 计数是 irq 子系统最容易踩的坑**（01 §2.1、§2.4），
它们的存在就是"`enable_irq()` 必须和 `disable_irq()` 配平"的物理原因。

### §5.2 `struct irq_data`：每层一份

定义在 `include/linux/irq.h:179`。**只有 8 个字段**，非常小：

| 字段 | 行号 | 要点 |
|---|---|---|
| `mask` | `include/linux/irq.h:180` | 预算好的位掩码，给 generic-chip 用 |
| `irq` | `include/linux/irq.h:181` | **virq，每层都一样** |
| `hwirq` | `include/linux/irq.h:182` | **每层不一样** |
| `common` | `include/linux/irq.h:183` | 指向那唯一一份 `irq_common_data` |
| `chip` | `include/linux/irq.h:184` | **这一层**的操作集 |
| `domain` | `include/linux/irq.h:185` | **这一层**所属的域 |
| `parent_data` | `include/linux/irq.h:187` | 下一层（`CONFIG_IRQ_DOMAIN_HIERARCHY`） |
| `chip_data` | `include/linux/irq.h:189` | **这一层**的实例数据（§1.0.D） |

**"每层一样 / 每层不一样"这一栏就是层级域的全部**：
`irq` 相同（所以用户态只看到一个中断号），
`hwirq` / `chip` / `domain` / `chip_data` 各层独立（所以每层能操作自己的硬件）。

### §5.3 `struct irq_common_data`：全层共享一份

定义在 `include/linux/irq.h:147`。

| 字段 | 要点 |
|---|---|
| `state_use_accessors` | `IRQD_*` 位（§6.2）。带 `__private` 标注 ⇒ **只能用访问器** |
| `node` | NUMA 节点（`CONFIG_NUMA`） |
| `handler_data` | **流处理器**的私有数据（不是 chip 的，01 §4.2） |
| `msi_desc` | MSI 描述符 |
| `affinity` | 期望的亲和性掩码（`CONFIG_SMP`） |
| `effective_affinity` | **实际生效的**掩码（`CONFIG_GENERIC_IRQ_EFFECTIVE_AFF_MASK`） |
| `ipi_offset` | IPI 域用（`CONFIG_GENERIC_IRQ_IPI`） |

⚠️ **`affinity` 和 `effective_affinity` 是两个不同的东西**：
前者是"你要求的"，后者是"硬件真正在往哪儿送"。
GIC v3 的 SPI 只能送给**一个** CPU（`IRQD_SINGLE_TARGET`），
所以你写 `smp_affinity=0xff`，`effective_affinity` 只会是其中一位。
⇒ 07 篇会给这两个 sysfs 文件的对照。

### §5.4 `struct irq_chip`：一张函数表

定义在 `include/linux/irq.h:488`，共 **名字 + 33 个回调 + flags**。
按用途分五组：

| 组 | 回调 | 谁必须实现 |
|---|---|---|
| **生命周期** | `irq_startup` / `irq_shutdown` / `irq_enable` / `irq_disable` | 可选 —— 不实现则核心退化成用 mask/unmask |
| **确认协议** | `irq_ack` / `irq_mask` / `irq_mask_ack` / `irq_unmask` / `irq_eoi` | **至少要有 mask+unmask 或 eoi**，看用哪个流处理器 |
| 配置 | `irq_set_affinity` / `irq_retrigger` / `irq_set_type` / `irq_set_wake` | 不实现则对应的消费者 API 返回 `-ENOSYS`（01 §10.2） |
| 慢总线 | `irq_bus_lock` / `irq_bus_sync_unlock` | **I2C/SPI 上的中断扩展器必须实现** —— 核心会把多次寄存器操作合批 |
| PM | `irq_suspend` / `irq_resume` / `irq_pm_shutdown` | 可选 |
| 展示 | `irq_print_chip` | 影响 `/proc/interrupts` 那一列 |
| 资源 | `irq_request_resources` / `irq_release_resources` | `request_irq()` 时调 |
| MSI | `irq_compose_msi_msg` / `irq_write_msi_msg` | MSI 域 |
| 状态 | `irq_get_irqchip_state` / `irq_set_irqchip_state` | 01 §2.5 |
| 虚拟化 | `irq_set_vcpu_affinity` | —— |
| IPI | `ipi_send_single` / `ipi_send_mask` | IPI 域 |
| NMI | `irq_nmi_setup` / `irq_nmi_teardown` | `request_nmi()` |
| 亲和性辅助 | `irq_calc_mask` | generic-chip |
| 已废弃 | `irq_cpu_online` / `irq_cpu_offline` | 只在 `CONFIG_DEPRECATED_IRQ_CPU_ONOFFLINE` 下存在，arm64 **不开** |

**`irq_bus_lock` / `irq_bus_sync_unlock` 这一对最容易被漏掉**：
如果你的中断控制器挂在 I2C 上，每次 mask/unmask 都是一次 I2C 传输 —— 而且**会睡**。
核心的做法是：在调用一串 chip 操作之前调 `irq_bus_lock`，
之后调 `irq_bus_sync_unlock` 一次性刷出去。⇒ 05 篇会给骨架。

### §5.5 `struct irq_domain`：变长结构

定义在 `include/linux/irqdomain.h:164`。**它是变长的**（末尾 `revmap[]`）。

| 字段 | 行号 | 要点 |
|---|---|---|
| `link` | `include/linux/irqdomain.h:165` | 挂进全局 `irq_domain_list` |
| `name` | `include/linux/irqdomain.h:166` | 出现在 debugfs 里 |
| `ops` | `include/linux/irqdomain.h:167` | `struct irq_domain_ops` |
| `host_data` | `include/linux/irqdomain.h:168` | 驱动的私有数据 |
| `flags` | `include/linux/irqdomain.h:169` | `IRQ_DOMAIN_FLAG_*`（§6.3） |
| `mapcount` | `include/linux/irqdomain.h:170` | 已建立的映射数 |
| `fwnode` | `include/linux/irqdomain.h:173` | 域的身份（DT 节点或 ACPI/软件 fwnode） |
| `bus_token` | `include/linux/irqdomain.h:174` | 同一个 fwnode 上区分多个域（PCI-MSI / platform-MSI / …） |
| `gc` | `include/linux/irqdomain.h:175` | generic-chip 关联 |
| `dev` | `include/linux/irqdomain.h:176` | 可能为 NULL（一级控制器没有 device，§2.2） |
| `parent` | `include/linux/irqdomain.h:178` | 层级域的父域 |
| `hwirq_max` | `include/linux/irqdomain.h:182` | 反向映射的上界 |
| `revmap_size` | `include/linux/irqdomain.h:183` | **线性数组部分的大小** |
| `revmap_tree` | `include/linux/irqdomain.h:184` | **radix tree 部分** |
| `revmap_mutex` | `include/linux/irqdomain.h:185` | 保护上面两个 |
| `revmap[]` | `include/linux/irqdomain.h:186` | **变长数组，紧跟在结构体后面** |

**四种反向映射其实是同一套字段的四种参数组合**：

| 域类型 | `revmap_size` | `hwirq_max` | 用哪个 |
|---|---|---|---|
| linear | = size | = size | 数组（`revmap[]`） |
| tree | 0 | `~0` | radix tree |
| simple / legacy | = size | = size | 数组 |
| nomap | 0 | = max_irq | **两个都不用** —— hwirq 直接存在 `irq_data` 里 |

⇒ 01 §5.1 那句「判断某个域用的是哪种反向映射，看它传给 `__irq_domain_add()` 的三个参数」
指的就是这张表。

`revmap[]` 是变长数组这件事有一个实际后果：
**域一旦建好，`revmap_size` 不能改**。想支持"运行期发现更多 hwirq"就得用 tree 型。

### §5.6 `struct irqaction`：一个设备 handler

定义在 `include/linux/interrupt.h:118`（在补丁插入点之前，行号未受影响）。
末尾同样带 `____cacheline_internodealigned_in_smp`。

| 字段 | 要点 |
|---|---|
| `handler` | 你的硬中断处理函数 |
| `dev_id` | 共享中断时的身份标识（01 §1.1） |
| `percpu_dev_id` | per-CPU 版 |
| `next` | **共享中断的链** |
| `thread_fn` | 线程处理函数 |
| `thread` | 那个内核线程 |
| `secondary` | **`IRQF_ONESHOT` + 强制线程化时的第二个 action** |
| `irq` / `flags` / `name` | —— |
| `thread_flags` / `thread_mask` | `thread_mask` 是"这个 action 在 `threads_oneshot` 里占哪一位" |
| `dir` | `/proc/irq/N/<name>` |

`thread_mask` 是一个 bit-per-action 的分配 ⇒
**一条中断上带 `IRQF_ONESHOT` 的 action 数量有上界**（机器字长）。
超了 `request_threaded_irq()` 会失败。

### §5.7 generic-chip 的三个结构

| 结构 | 定义 | 一句话 |
|---|---|---|
| `struct irq_chip_type` | `include/linux/irq.h:1006` | 一种触发类型对应的 chip + 寄存器偏移 |
| `struct irq_chip_generic` | `include/linux/irq.h:1048` | 一组（通常 32 条）中断共用的寄存器基址 + 掩码缓存 |
| `struct irq_domain_chip_generic` | `include/linux/irq.h:1098` | 一个域里的多个 `irq_chip_generic` |

---

## §6 四组状态位

### §6.1 `IRQ_*`：`irq_desc.status_use_accessors`（`include/linux/irq.h:78` 起）

| 位 | 行号 | 含义 |
|---|---|---|
| `IRQ_TYPE_*` | `include/linux/irq.h:78` 起 | 低 4 位存触发类型 |
| `IRQ_NOPROBE` | `include/linux/irq.h:92` | 不参与自动探测 |
| `IRQ_NOREQUEST` | `include/linux/irq.h:93` | **不能被 `request_irq()`** —— 级联入口（§1.0.B） |
| `IRQ_NOAUTOEN` | `include/linux/irq.h:94` | 申请后不自动使能（对应 `IRQF_NO_AUTOEN`） |
| `IRQ_NO_BALANCING` | `include/linux/irq.h:95` | 不参与亲和性均衡 |
| `IRQ_MOVE_PCNTXT` | `include/linux/irq.h:96` | 可在进程上下文迁移 |
| `IRQ_NESTED_THREAD` | `include/linux/irq.h:97` | 嵌套在别的线程里（`handle_nested_irq`） |
| `IRQ_PER_CPU_DEVID` | `include/linux/irq.h:99` | `dev_id` 是 per-CPU 变量 |
| `IRQ_NOTHREAD` | `include/linux/irq.h:98` | 不许被强制线程化 |
| `IRQ_IS_POLLED` | `include/linux/irq.h:100` | 被别的中断轮询 |
| `IRQ_DISABLE_UNLAZY` | `include/linux/irq.h:101` | 关掉 lazy disable（见 §6.2 末） |
| `IRQ_HIDDEN` | `include/linux/irq.h:102` | 不出现在 `/proc/interrupts` |
| `IRQ_NO_DEBUG` | `include/linux/irq.h:103` | 不参与 `note_interrupt()` 的乱中断统计 |
| `IRQ_LEVEL` / `IRQ_PER_CPU` | `:90` / `:91` | 和 `IRQD_LEVEL` / `IRQD_PER_CPU` 是**两套位**，靠 `irq_settings_*` 同步 |

可写的位由 `IRQF_MODIFY_MASK` 限定（`include/linux/irq.h:106-110`）——
`irq_modify_status()` 只允许改这个掩码内的位。⚠️ 注意 `IRQ_NO_DEBUG` **不在**这个掩码里。

**访问方式**：只能用 `kernel/irq/settings.h` 里的
`irq_settings_can_request()` / `irq_settings_set_norequest()` 等访问器
（`kernel/irq/settings.h:100` 起）。
驱动侧的入口是 `irq_set_status_flags()` / `irq_clear_status_flags()`（01 §9）。

### §6.2 `IRQD_*`：`irq_common_data.state_use_accessors`（`include/linux/irq.h:226` 起）

**22 个位**，是"这条中断当前的运行时状态"：

| 位 | 行号 | 含义 |
|---|---|---|
| `IRQD_TRIGGER_MASK` | `include/linux/irq.h:226` | 低 4 位：触发类型（和 `IRQ_TYPE_*` 同编码） |
| `IRQD_SETAFFINITY_PENDING` | `include/linux/irq.h:227` | 亲和性改动待生效 |
| `IRQD_ACTIVATED` | `include/linux/irq.h:228` | 域的 `activate` 已跑过 |
| `IRQD_NO_BALANCING` / `IRQD_PER_CPU` / `IRQD_AFFINITY_SET` | `:229`-`:231` | —— |
| `IRQD_LEVEL` | `include/linux/irq.h:232` | **电平触发** —— 流处理器和 EOI 时机都看它 |
| `IRQD_WAKEUP_STATE` / `IRQD_WAKEUP_ARMED` | `:233` / `:238` | 唤醒相关 |
| `IRQD_MOVE_PCNTXT` | `include/linux/irq.h:234` | —— |
| `IRQD_IRQ_DISABLED` / `IRQD_IRQ_MASKED` | `:235` / `:236` | **"逻辑上禁用"和"硬件上屏蔽"是两件事** |
| `IRQD_IRQ_INPROGRESS` | `include/linux/irq.h:237` | 正在执行 handler —— `disable_irq()` 等的就是它 |
| `IRQD_FORWARDED_TO_VCPU` | `include/linux/irq.h:239` | 直通给虚拟机 |
| `IRQD_AFFINITY_MANAGED` | `include/linux/irq.h:240` | 亲和性由内核托管（多队列设备），用户态改不了 |
| `IRQD_IRQ_STARTED` | `include/linux/irq.h:241` | `irq_startup` 跑过 |
| `IRQD_MANAGED_SHUTDOWN` | `include/linux/irq.h:242` | 托管中断因 CPU 下线而关闭 |
| `IRQD_SINGLE_TARGET` | `include/linux/irq.h:243` | **只能送给一个 CPU** —— GIC SPI 就是（§5.3） |
| `IRQD_DEFAULT_TRIGGER_SET` | `include/linux/irq.h:244` | —— |
| `IRQD_CAN_RESERVE` | `include/linux/irq.h:245` | MSI 预留模式 |
| `IRQD_HANDLE_ENFORCE_IRQCTX` | `include/linux/irq.h:246` | 强制要求在硬中断上下文处理 |
| `IRQD_AFFINITY_ON_ACTIVATE` | `include/linux/irq.h:247` | 亲和性延迟到 activate 时才设 |
| `IRQD_IRQ_ENABLED_ON_SUSPEND` | `include/linux/irq.h:248` | suspend 期间保持使能 |

⚠️ **`IRQD_IRQ_DISABLED` 与 `IRQD_IRQ_MASKED` 的区别是本子系统一个精妙点**：
核心支持 **lazy disable** —— `disable_irq()` 只置 `DISABLED`，
真正的硬件 mask 推迟到中断真的来了那一刻（省一次寄存器访问）。
⇒ 想关掉这个优化就设 `IRQ_DISABLE_UNLAZY`（§6.1）。

### §6.3 `IRQ_DOMAIN_FLAG_*`：`irq_domain.flags`（`include/linux/irqdomain.h:190` 起）

| 位 | 含义 |
|---|---|
| `IRQ_DOMAIN_FLAG_HIERARCHY` | 这是个层级域 |
| `IRQ_DOMAIN_NAME_ALLOCATED` | 名字是 `__irq_domain_add()` 分配的（销毁时要 free） |
| `IRQ_DOMAIN_FLAG_IPI_PER_CPU` / `IRQ_DOMAIN_FLAG_IPI_SINGLE` | IPI 域的两种形态 |
| `IRQ_DOMAIN_FLAG_MSI` / `IRQ_DOMAIN_FLAG_MSI_REMAP` | MSI 域 / 支持 MSI 重映射 |

### §6.4 `IRQCHIP_*`：`irq_chip.flags`（`include/linux/irq.h:559` 起）

| 位 | 行号 | 含义 |
|---|---|---|
| `IRQCHIP_SET_TYPE_MASKED` | `include/linux/irq.h:559` | 调 `irq_set_type` 前先 mask |
| `IRQCHIP_EOI_IF_HANDLED` | `include/linux/irq.h:560` | 只在真处理了才 EOI |
| `IRQCHIP_MASK_ON_SUSPEND` | `include/linux/irq.h:561` | suspend 时屏蔽非唤醒中断 |
| `IRQCHIP_ONOFFLINE_ENABLED` | `include/linux/irq.h:562` | 配合已废弃的 cpu_online/offline 回调 |
| `IRQCHIP_SKIP_SET_WAKE` | `include/linux/irq.h:563` | **"我不需要 set_wake，但唤醒是支持的"** —— 避免返回 `-ENOSYS` |
| `IRQCHIP_ONESHOT_SAFE` | `include/linux/irq.h:564` | 硬件天然满足 oneshot 语义，核心可以省掉 mask |
| `IRQCHIP_EOI_THREADED` | `include/linux/irq.h:565` | **EOI 推迟到线程跑完** |
| `IRQCHIP_SUPPORTS_LEVEL_MSI` | `include/linux/irq.h:566` | —— |
| `IRQCHIP_SUPPORTS_NMI` | `include/linux/irq.h:567` | `request_nmi()` 的前置条件 |
| `IRQCHIP_ENABLE_WAKEUP_ON_SUSPEND` | `include/linux/irq.h:568` | —— |
| `IRQCHIP_AFFINITY_PRE_STARTUP` | `include/linux/irq.h:569` | 在 startup 之前设亲和性 |
| `IRQCHIP_IMMUTABLE` | `include/linux/irq.h:570` | **禁止别人（如 gpiolib）改这个 chip 的回调** |

`IRQCHIP_IMMUTABLE` 是 6.x 新加的：以前 gpiolib 会偷偷替换 `irq_chip` 的回调，
这个标志让 chip 作者能拒绝。⇒ GIC v3 就设了它。

---

## §7 全局变量与锁

### §7.1 全部四个全局状态

| 变量 | 位置 | 保护 |
|---|---|---|
| `allocated_irqs`（virq 位图） | `kernel/irq/irqdesc.c:134` | `sparse_irq_lock` |
| `irq_desc_tree`（radix tree，virq → `irq_desc`） | `kernel/irq/irqdesc.c:347` | 读侧靠 RCU，写侧靠 `sparse_irq_lock` |
| `irq_desc[NR_IRQS]`（静态数组） | `kernel/irq/irqdesc.c:557` | **只在 `!CONFIG_SPARSE_IRQ` 下存在**（arm64 不走这条） |
| `irq_domain_list` | `kernel/irq/irqdomain.c:23` | `irq_domain_mutex` |
| `irq_default_affinity` | `kernel/irq/manage.c:144` | 只在 boot 和 procfs 写，无专门锁 |

```bash
grep -n 'sparse_irq_lock\|allocated_irqs\|RADIX_TREE(irq_desc_tree' kernel/irq/irqdesc.c
grep -n 'irq_domain_list\|irq_domain_mutex' kernel/irq/irqdomain.c
```

### §7.2 四把锁

| 锁 | 位置 | 保护什么 | 类型 |
|---|---|---|---|
| `irq_desc.lock` | `include/linux/irqdesc.h:71` | 单条中断的全部状态 | **`raw_spinlock_t`** |
| `irq_desc.request_mutex` | `include/linux/irqdesc.h:101` | `request_irq` / `free_irq` 的整个过程（字段注释在 `include/linux/irqdesc.h:50` 明确写了「protect request/free **before** locking desc->lock」——锁序的官方说明） | mutex |
| `sparse_irq_lock` | `kernel/irq/irqdesc.c:133` | `allocated_irqs` + `irq_desc_tree` 的写 | mutex |
| `irq_domain_mutex` | `kernel/irq/irqdomain.c:24` | `irq_domain_list` | mutex |
| `irq_domain.revmap_mutex` | `include/linux/irqdomain.h:185` | 单个域的反向映射 | mutex |

**`irq_desc.lock` 必须是 `raw_spinlock_t` 而不是 `spinlock_t`**：
它要在硬中断上下文里持有，而在 `CONFIG_PREEMPT_RT` 下 `spinlock_t` 变成了可睡眠的
rt-mutex。⇒ 本树打了 PREEMPT_RT 相关补丁（篇首），这一点尤其要注意。

**锁的层次**：`request_irq()` 的路径是
`request_mutex` → `sparse_irq_lock`（如果要分配 desc）→ `desc->lock`。
反序会死锁。03 §B 会给完整的锁序。

⚠️ 还有一对"公开的" sparse 锁：`irq_lock_sparse()` / `irq_unlock_sparse()`
（`include/linux/irqdesc.h` 里声明），给 CPU 热插拔路径用。

---

## §8 结构关系总图

```
   全局
   ┌──────────────────────────────────────────────────────────────┐
   │  allocated_irqs 位图 ──── 谁占了哪个 virq                     │
   │  irq_desc_tree (radix) ── virq → irq_desc                    │
   │  irq_domain_list ──────── 所有的域                            │
   └──────────────────────────────────────────────────────────────┘

   一条中断（virq = 42）
   ┌─── irq_desc ────────────────────────────────────────────────┐
   │                                                              │
   │  irq_common_data ◀──────────┬──────────┬───── (全层共享)      │
   │    · state_use_accessors    │          │                     │
   │    · affinity               │          │                     │
   │    · effective_affinity     │          │                     │
   │    · handler_data           │          │                     │
   │                             │          │                     │
   │  irq_data (第 0 层) ────────┘          │                     │
   │    · hwirq = 0x1234                    │                     │
   │    · chip  ──▶ its_irq_chip            │                     │
   │    · domain ─▶ irq_domain (ITS)        │                     │
   │    · chip_data ─▶ struct its_device    │                     │
   │    · parent_data ──▶ irq_data (第 1 层)┘                     │
   │                        · hwirq = 8192+n                      │
   │                        · chip ──▶ gic_chip                   │
   │                        · domain ─▶ irq_domain (GIC v3)       │
   │                        · parent_data = NULL                  │
   │                                                              │
   │  handle_irq ──▶ handle_fasteoi_irq   ← 流处理器（一个）      │
   │                                                              │
   │  action ──▶ irqaction ──next──▶ irqaction ──next──▶ NULL     │
   │              · handler            · handler                  │
   │              · dev_id             · dev_id                   │
   │              · thread_fn          · thread                   │
   │                                                              │
   │  lock (raw_spinlock_t) · depth · wake_depth · kstat_irqs      │
   └──────────────────────────────────────────────────────────────┘

   一个域
   ┌─── irq_domain ──────────────────────────────────────────────┐
   │  ops ──▶ irq_domain_ops（xlate/map 或 translate/alloc/free）  │
   │  host_data ──▶ 驱动私有                                       │
   │  parent ──▶ irq_domain（上一级）                              │
   │  revmap[] / revmap_tree ── hwirq → irq_data                  │
   └──────────────────────────────────────────────────────────────┘
```

**图里最该盯住的三条边**：
1. `irq_data.parent_data` —— 层级域的脊椎（§1.0.B、§5.2）。
2. `irq_data.common` 全部指向同一处 —— 所以亲和性只有一份（§5.3）。
3. `irq_desc.handle_irq` 和 `irq_desc.action` 是**两条不同的路** —— 一个是控制器的，一个是设备的（§1.0.E）。

---

**下一篇**：03 篇按时间线剖析注册、映射建立、一次中断的完整分发、以及锁与恢复。
