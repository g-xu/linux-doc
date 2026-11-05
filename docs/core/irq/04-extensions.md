---
title: 扩展机制
---

# irq 04：独立框架与展示层

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 组织轴

### §0.1 一节一个独立框架，节与节之间互不调用

本篇的组织方式和 03 篇**相反**：
03 是一条时间线（必须顺读），本篇是**十一个互不依赖的模块**（可以任意跳读）。
⚠️ **跳读是设计意图**，不要按顺序啃。

判据：这十一节里的代码**互相不出现在对方的调用链上**（唯一的例外在 §4 和 §11 之间标了出来）。

### §0.2 分诊表：哪几个在 arm64 上真会碰到

| § | 框架 | 文件 / 行数 | Kconfig | arm64 相关度 |
|---|---|---|---|---|
| §1 | **generic-chip** | `kernel/irq/generic-chip.c` 668 | `GENERIC_IRQ_CHIP` | 写简单二级控制器时 |
| §2 | **MSI 域** | `kernel/irq/msi.c` 1042 | `GENERIC_MSI_IRQ_DOMAIN` | **PCIe / GIC ITS 必经** |
| §3 | **IPI 域** | `kernel/irq/ipi.c` 345 | `GENERIC_IRQ_IPI` | arm64 用 SGI，**不走这个框架** |
| §4 | **乱中断检测** | `kernel/irq/spurious.c` 478 | 总是编译 | `nobody cared` 就是它 |
| §5 | **procfs 展示** | `kernel/irq/proc.c` 538 | `PROC_FS` | `/proc/interrupts` |
| §6 | **debugfs 展示** | `kernel/irq/debugfs.c` 261 | `GENERIC_IRQ_DEBUGFS`（**不默认开**） | 看层级域的唯一窗口 |
| §7 | **中断模拟器** | `kernel/irq/irq_sim.c` 250 | `IRQ_SIM` | 只有测试用 |
| §8 | **间隔预测** | `kernel/irq/timings.c` 958 | `IRQ_TIMINGS` | 给 cpuidle governor 用 |
| §9 | **矩阵分配器** | `kernel/irq/matrix.c` 513 | `GENERIC_IRQ_MATRIX_ALLOCATOR` | **x86 专用**，arm64 不开 |
| §10 | **PM（suspend/resume）** | `kernel/irq/pm.c` 260 | `PM_SLEEP` | 唤醒源相关 |
| §11 | **软件重发** | `kernel/irq/resend.c` 184 | `HARDIRQS_SW_RESEND` | `irq_inject_interrupt()` 靠它 |

**只有 §2 §4 §5 §6 是"一定会碰到"的**，先读这四节。

### §0.3 各框架挂在 03 时间线的哪一点

```
   §A1 early_irq_init
       │
   §A2 of_irq_init ──▶ irqchip 驱动
       │                  ├─▶ §1 generic-chip（可选，替代手写 irq_chip）
       │                  ├─▶ §2 MSI 域（GIC ITS 在这里建）
       │                  └─▶ §3 IPI 域（arm64 不走）
       │
   §A4 映射建立 ──▶ §5 register_irq_proc()（建 /proc/irq/N/）
       │        ──▶ §6 irq_add_debugfs_entry()（建 debugfs 条目）
       │
   §A5 request_irq ──▶ §10 irq_pm_install_action()（登记 PM 属性）
       │
   §A6 中断分发 ──▶ §4 note_interrupt()（每次都调）
       │         ──▶ §8 record_irq_time()（每次都调，若开了 IRQ_TIMINGS）
       │
   §A9/§A10 亲和性 ──▶ §9 矩阵分配器（x86）
       │
   任意时刻 ──▶ §11 check_irq_resend() / irq_inject_interrupt()
   任意时刻 ──▶ §7 irq_sim（完全独立，自己建域）
```

| 挂载点 | 位置 |
|---|---|
| §5 `register_irq_proc()` | `kernel/irq/proc.c:338` |
| §6 `irq_add_debugfs_entry()` | `kernel/irq/debugfs.c:231` |
| §10 `irq_pm_install_action()` | `kernel/irq/pm.c:33` |
| §4 `note_interrupt()` 被调点 | `kernel/irq/handle.c:198` |
| §8 `record_irq_time()` 被调点 | `kernel/irq/handle.c:145` |
| §11 `irq_inject_interrupt()` | `kernel/irq/resend.c:155` |
| §1 `irq_generic_chip_ops` | `kernel/irq/generic-chip.c:454` |
| §2 `msi_domain_ops` | `kernel/irq/msi.c:575` |
| §7 `irq_sim_domain_ops` | `kernel/irq/irq_sim.c:149` |

---

## §1 generic-chip：给"一个寄存器一位一条中断"的控制器省代码

### §1.1 它替你做了什么

一个典型的简单中断控制器长这样：

```
   寄存器基址 base
     base + 0x00   STATUS   ← 每位一条中断，1 = pending
     base + 0x04   MASK     ← 每位一条中断，1 = 屏蔽
     base + 0x08   ACK      ← 写 1 清 pending
```

手写 `irq_chip` 你要写 mask / unmask / ack 三个回调，每个都是
"算出 bit → 读寄存器 → 改位 → 写回"。generic-chip 把这套模板化了：

| 你提供 | 核心提供 |
|---|---|
| 寄存器基址 + 各寄存器的偏移 | 十个现成的回调（下表） |
| 每个 chip 管几条中断（`irqs_per_chip`） | 掩码缓存（`gc->mask_cache`）省一次读 |
| 触发类型有几种（`num_ct`） | 一套 `irq_domain_ops`（`irq_generic_chip_ops`） |

### §1.2 十个现成回调

| 回调 | 位置 | 动作 |
|---|---|---|
| `irq_gc_noop()` | `kernel/irq/generic-chip.c:25` | 什么都不做（硬件不需要这一步时填它） |
| `irq_gc_mask_disable_reg()` | `kernel/irq/generic-chip.c:37` | 往"禁用寄存器"写位 |
| `irq_gc_mask_set_bit()` | `kernel/irq/generic-chip.c:57` | mask 寄存器**置位** = 屏蔽 |
| `irq_gc_mask_clr_bit()` | `kernel/irq/generic-chip.c:77` | mask 寄存器**清位** = 屏蔽（极性相反的硬件） |
| `irq_gc_unmask_enable_reg()` | `kernel/irq/generic-chip.c:97` | 往"使能寄存器"写位 |
| `irq_gc_ack_set_bit()` | `kernel/irq/generic-chip.c:114` | ack 寄存器置位 |
| `irq_gc_ack_clr_bit()` | `kernel/irq/generic-chip.c:130` | ack 寄存器清位 |
| `irq_gc_mask_disable_and_ack_set()` | `kernel/irq/generic-chip.c:153` | mask + ack 一步做完（给 `handle_level_irq` 的 `irq_mask_ack`） |
| `irq_gc_eoi()` | `kernel/irq/generic-chip.c:170` | 写 EOI 寄存器 |
| `irq_gc_set_wake()` | `kernel/irq/generic-chip.c:190` | 维护 `gc->wake_active` 掩码 |

**注意 `set_bit` / `clr_bit` 两组成对存在** ——
因为不同厂商的"1 表示屏蔽"和"1 表示使能"两种约定都有。
选错的表现是**中断行为完全反了**（一直不来 或 一直来）。

### §1.3 和 `irq_domain` 的配合

```
   irqchip 驱动 probe
     ├─ d = irq_domain_add_linear(node, nr_irqs, &irq_generic_chip_ops, NULL)
     │      ops 用核心提供的 irq_generic_chip_ops
     │
     ├─ irq_alloc_domain_generic_chips(d, 32, 1, "myctl",
     │                                 handle_level_irq, clr, set, flags)
     │      └─ __irq_alloc_domain_generic_chips()
     │            按 32 条一组，分配 N 个 irq_chip_generic
     │
     └─ 逐组填寄存器基址：
          gc = irq_get_domain_generic_chip(d, i * 32);
          gc->reg_base = base;
          gc->chip_types[0].regs.mask = 0x04;
          gc->chip_types[0].regs.ack  = 0x08;
          gc->chip_types[0].chip.irq_mask   = irq_gc_mask_set_bit;
          gc->chip_types[0].chip.irq_unmask = irq_gc_mask_clr_bit;
          gc->chip_types[0].chip.irq_ack    = irq_gc_ack_set_bit;
```

| 关键位置 | 行号 |
|---|---|
| `irq_generic_chip_ops` | `kernel/irq/generic-chip.c:454`（导出于 `kernel/irq/generic-chip.c:459`） |
| `__irq_alloc_domain_generic_chips()` | `kernel/irq/generic-chip.c:285` |
| `irq_setup_generic_chip()` | `kernel/irq/generic-chip.c:473` |
| `irq_setup_alt_chip()` | `kernel/irq/generic-chip.c:518` |
| `irq_remove_generic_chip()` | `kernel/irq/generic-chip.c:544` |
| 唯一的 `-ENOTSUPP` | `kernel/irq/generic-chip.c:402` |

**`irq_generic_chip_ops` 里已经填好了 `.map` / `.unmap` / `.xlate`** ——
所以用 generic-chip 时**你连 `irq_domain_ops` 都不用写**。
这就是它能省一两百行的原因。

### §1.4 `irq_setup_alt_chip()`：一个控制器上两种触发类型

有的控制器"电平中断"和"边沿中断"用**不同的寄存器组**。
generic-chip 用 `num_ct > 1` 支持这种硬件：每种触发类型一个 `irq_chip_type`。

`irq_setup_alt_chip()`（`kernel/irq/generic-chip.c:518`）在
`irq_chip->irq_set_type` 里被调用，按 `type` 切换到对应的那一组。

⚠️ 它返回 `-ENOTSUPP`（`kernel/irq/generic-chip.c:402`，
在 `__irq_get_domain_generic_chip()` 的路径上）——
03 §C2 记录了这个非标准错误码的问题。

### §1.5 ⚠️ 什么时候**不能**用 generic-chip

| 情况 | 为什么 |
|---|---|
| 寄存器不是"一位一条中断" | 比如 GIC 的 `GICD_IROUTER` 是每条中断 8 字节 |
| 需要层级域 | `irq_generic_chip_ops` 只有 `.map`/`.xlate`，**没有 `.alloc`**（01 §5.2 的二选一） |
| 需要 per-CPU 中断 | 模板不支持 |
| mask/unmask 要走慢总线 | 模板里是直接 `readl`/`writel`，没有 `chip_bus_lock` |

⇒ **GIC 不用 generic-chip**，这四条各占一个理由。

---

## §2 MSI 域：设备自己"写"出中断

### §2.1 MSI 和普通中断的根本区别

| | 普通中断（线中断） | MSI |
|---|---|---|
| 硬件形式 | 一根物理信号线 | **一次内存写事务** |
| 中断号从哪来 | DT 里写死 | **运行期分配**，写进设备的配置空间 |
| 有几条 | 硬件决定 | 设备申请、系统按需分配 |
| 需要什么额外结构 | 无 | **`struct msi_desc`** 记录"分配给了哪个设备的第几个中断" |
| arm64 上的实现 | GIC 的 SPI/PPI | **GIC ITS 的 LPI** |

**"MSI 域一定是层级域"**（01 §7）：
因为"写一次内存产生中断"这个动作最终还是要落到 GIC 上，
所以 MSI 域必须挂在 GIC 域之上。

### §2.2 核心提供的那一层

`kernel/irq/msi.c` 提供的是**通用的 MSI 域行为**，
具体的硬件动作由 `msi_domain_info->chip` 里的回调完成。

| 核心的 `irq_domain_ops` 成员 | 位置 | 干什么 |
|---|---|---|
| `.alloc` = `msi_domain_alloc` | `kernel/irq/msi.c:530` | 向父域申请，然后给每条中断设 chip 和 `msi_desc` |
| `.free` = `msi_domain_free` | `kernel/irq/msi.c:562` | 反操作 |
| `.activate` = `msi_domain_activate` | `kernel/irq/msi.c:510` | **调 `irq_chip_write_msi_msg()` 把消息写进设备** |
| `.deactivate` = `msi_domain_deactivate` | `kernel/irq/msi.c:521` | 把消息清掉 |
| 整个 ops 表 | `kernel/irq/msi.c:575` | —— |

**`.activate` 才是"MSI 真正生效"的那一步**（03 §B4 的两阶段）：
`.alloc` 只是占了一个 LPI 号，`.activate` 才把
"往哪个地址写什么值"这条信息写进设备的 MSI 能力寄存器。

⇒ **这就是层级域为什么需要 `activate` 阶段**：普通线中断不需要这一步。

### §2.3 域的创建与 ops 的补全

| 关键位置 | 行号 |
|---|---|
| `msi_create_irq_domain()` | `kernel/irq/msi.c:679` |
| `msi_domain_ops_init()` | `kernel/irq/msi.c:601` |
| `__msi_domain_alloc_irqs()` | `kernel/irq/msi.c:850` |
| `msi_domain_alloc_irqs_descs_locked()` | `kernel/irq/msi.c:929` |
| `msi_domain_alloc_irqs()` | `kernel/irq/msi.c:957` |
| `__msi_domain_free_irqs()` | `kernel/irq/msi.c:967` |
| `msi_get_domain_info()` | `kernel/irq/msi.c:1037` |

`msi_domain_ops_init()`（`kernel/irq/msi.c:601`）的作用是
**给没填的 `msi_domain_ops` 成员补上默认实现** ——
和 remoteproc 的 `rproc_alloc_ops()` 是同一个套路，
但 MSI 这边是**逐个补**（每个成员独立判断），
⚠️ **不是"全有或全无"**。

### §2.4 `msi_desc` 的遍历接口

| API | 位置 |
|---|---|
| `msi_lock_descs()` / `msi_unlock_descs()` | `kernel/irq/msi.c:230` / `:242` |
| `msi_first_desc()` / `msi_next_desc()` | `kernel/irq/msi.c:279` / `:308` |
| `msi_get_virq()` | `kernel/irq/msi.c:346` |
| `get_cached_msi_msg()` | `kernel/irq/msi.c:176` |

**`msi_get_virq(dev, index)` 是设备驱动拿 MSI 中断号的入口** ——
它对应线中断世界里的 `platform_get_irq()`（01 §3.1）。

⚠️ 遍历 `msi_desc` **必须持 `msi_lock_descs()`**。
6.x 把 MSI 描述符从数组改成了 xarray，锁语义比以前严格。

### §2.5 ⚠️ arm64 上的两个 MSI 提供者

| 提供者 | 文件 | 说明 |
|---|---|---|
| GIC ITS | `drivers/irqchip/irq-gic-v3-its.c`（5608 行） | PCIe MSI + platform MSI 都走它 |
| mbi-gen（Message Based Interrupt） | `drivers/irqchip/irq-gic-v3-mbi.c` | 没有 ITS 的平台的替代方案 |

本套对这两个实现**只讲契约不讲实现**（99 §2.2）——
ITS 一个文件就比整个 `kernel/irq/` 的三分之一还大。

---

## §3 IPI 域：把处理器间中断也当成 irq

### §3.1 这个框架在 arm64 上不用

`kernel/irq/ipi.c`（345 行）需要 `CONFIG_GENERIC_IRQ_IPI`。
**arm64 不开这个选项** —— 它的 IPI 走 GIC 的 SGI，
由 `arch/arm64/kernel/smp.c` 直接操作，不经过 `irq_domain`。

⇒ 本节只讲它**存在的理由**，不展开实现（三等证据，99 §2.2）。

### §3.2 它解决什么问题

在 MIPS / 某些 SoC 上，IPI 是通过一个**独立的中断控制器**发的。
那种硬件上，"给 CPU3 发一个 IPI"这个动作和"给 CPU3 送一条设备中断"没有区别 ——
于是把 IPI 也建成一个 `irq_domain` 就很自然。

| API | 位置 | 说明 |
|---|---|---|
| `irq_reserve_ipi(domain, dest)` | `kernel/irq/ipi.c:23` | 为一组 CPU 预留 IPI |
| `irq_destroy_ipi(irq, dest)` | `kernel/irq/ipi.c:115` | 反操作 |
| `ipi_get_hwirq(irq, cpu)` | 导出于 `kernel/irq/ipi.c:186` | virq + cpu → hwirq |
| `__ipi_send_single(desc, cpu)` | `kernel/irq/ipi.c:227` | 底层发送 |
| `__ipi_send_mask(desc, dest)` | `kernel/irq/ipi.c:269` | 底层广播 |
| `ipi_send_single(virq, cpu)` | `kernel/irq/ipi.c:313`（导出于 `:324`） | 对外接口 |
| `ipi_send_mask(virq, dest)` | `kernel/irq/ipi.c:334`（导出于 `:345`） | 对外接口 |

两种域形态（02 §6.3）：

| flag | 含义 |
|---|---|
| `IRQ_DOMAIN_FLAG_IPI_PER_CPU` | 每个 CPU 一个 virq |
| `IRQ_DOMAIN_FLAG_IPI_SINGLE` | 所有 CPU 共用一个 virq |

`kernel/irq/ipi.c:246` 那条 `FIXME`（03 §C1.1）就在
`__ipi_send_single()` 里判断这两种形态的地方。

---

## §4 乱中断检测与轮询

### §4.1 三条防线

```
   每次中断处理完
     └─ note_interrupt(desc, retval)          ← kernel/irq/spurious.c:272
          │
          ├─【防线 1】retval 里有 IRQ_HANDLED ⇒ 清零计数，返回
          │
          ├─【防线 2】retval == IRQ_NONE
          │     desc->irqs_unhandled++
          │     desc->irq_count++
          │     每 100000 次结算：未处理占比 > 99% ？
          │           └─ __report_bad_irq() 打印 "nobody cared"
          │              置 IRQS_SPURIOUS_DISABLED，屏蔽这条中断
          │              启动一个定时器周期性轮询（poll_spurious_irqs）
          │
          └─【防线 3】irqfixup / irqpoll（命令行开）
                misrouted_irq() 遍历其它中断，看是不是"接错线了"
```

| 关键位置 | 行号 |
|---|---|
| `note_interrupt()` | `kernel/irq/spurious.c:272` |
| `__report_bad_irq()` | `kernel/irq/spurious.c:192` |
| `nobody cared` 那句 printk | `kernel/irq/spurious.c:202` |
| `try_one_irq()` | `kernel/irq/spurious.c:62` |
| `misrouted_irq()` | `kernel/irq/spurious.c:118` |
| `poll_spurious_irqs()`（定时器回调） | `kernel/irq/spurious.c:144` |
| 定时器声明 | `kernel/irq/spurious.c:20` |
| `irq_wait_for_poll()` | `kernel/irq/spurious.c:36` |
| `noirqdebug_setup()` | `kernel/irq/spurious.c:436` |
| `irqfixup_setup()` | `kernel/irq/spurious.c:448` |
| `irqpoll_setup()` | `kernel/irq/spurious.c:464` |

### §4.2 唯一的跨节调用：§4 ↔ §11

`irq_wait_for_poll()`（`kernel/irq/spurious.c:36`）被
`irq_may_run()`（`kernel/irq/chip.c:504`）通过 `irq_check_poll()` 调用 ——
也就是 03 §B1.3 那三条出口里的第三条。

⇒ **这是本篇十一节里唯一一处"框架之间互相调用"**，
其余十节都是自成体系的。

### §4.3 arm64 上的处置建议

| 命令行参数 | 位置 | arm64 上该不该用 |
|---|---|---|
| `noirqdebug` | `kernel/irq/spurious.c:436` | 只在"确认是误报"时临时用 |
| `irqfixup` | `kernel/irq/spurious.c:448` | **不该用** —— 它是给 x86 老 PCI 硬件"接错线"设计的 |
| `irqpoll` | `kernel/irq/spurious.c:464` | **不该用**，同上 |

**arm64 上看到 `nobody cared` 基本一定是驱动 bug**：
某个共享中断的 handler 返回值错了（01 §1.3）。
⇒ 07 篇给排查流程。

---

## §5 procfs 展示层

### §5.1 目录结构

```
   /proc/interrupts                 ← 一张大表，每行一条中断
   /proc/irq/
     ├── default_smp_affinity       ← 全局默认亲和性
     └── <N>/                       ← 每条中断一个目录
          ├── smp_affinity          RW  位掩码形式
          ├── smp_affinity_list     RW  CPU 列表形式（如 "0-3,8"）
          ├── affinity_hint         RO  只是"建议"，不是实际
          ├── effective_affinity    RO  实际生效的
          ├── effective_affinity_list RO
          ├── node                  RO  NUMA 节点
          ├── spurious              RO  乱中断统计
          └── <handler-name>/       ← 每个 action 一个空目录（只为显示名字）
```

| 元素 | 位置 |
|---|---|
| `root_irq_dir` | `kernel/irq/proc.c:35` |
| `init_irq_proc()` | `kernel/irq/proc.c:430` |
| `proc_mkdir("irq", NULL)` | `kernel/irq/proc.c:436` |
| `register_irq_proc()` | `kernel/irq/proc.c:338` |
| 建目录前的两道门 | `kernel/irq/proc.c:344` |
| `proc_mkdir(name, root_irq_dir)` | `kernel/irq/proc.c:360` |
| `smp_affinity` | `kernel/irq/proc.c:366` |
| `affinity_hint` | `kernel/irq/proc.c:370` |
| `smp_affinity_list` | `kernel/irq/proc.c:374` |
| `node` | `kernel/irq/proc.c:377` |
| `effective_affinity` | `kernel/irq/proc.c:380` |
| `effective_affinity_list` | `kernel/irq/proc.c:382` |
| `spurious` | `kernel/irq/proc.c:386` |
| `unregister_irq_proc()` | `kernel/irq/proc.c:393` |
| `register_handler_proc()` | `kernel/irq/proc.c:319` |
| `unregister_handler_proc()` | `kernel/irq/proc.c:417` |
| `show_interrupts()`（`/proc/interrupts` 的内容） | `kernel/irq/proc.c:460` |
| `name_unique()` | `kernel/irq/proc.c:300` |

**`kernel/irq/proc.c:344` 那两道门很重要**：
`!root_irq_dir` 或 `chip == &no_irq_chip` 时**不建目录**。
⇒ 一条中断在 `/proc/irq/` 下**没有目录**，说明它还没被任何控制器认领（03 §A5）。

`name_unique()`（`kernel/irq/proc.c:300`）：多个 action 同名时只建一个目录。
⇒ `/proc/irq/N/` 下的子目录数可能少于 action 数。

### §5.2 三组亲和性文件的区别

| 文件 | 读什么 | 能写吗 | 语义 |
|---|---|---|---|
| `smp_affinity` | `irq_common_data.affinity` | 能 | **你要求的** |
| `affinity_hint` | `irq_desc.affinity_hint` | 不能 | **驱动建议给 irqbalance 的**，不影响硬件 |
| `effective_affinity` | `irq_common_data.effective_affinity` | 不能 | **硬件实际在往哪送** |

| 实现 | 位置 |
|---|---|
| `show_irq_affinity()`（三个 show 的公共实现） | `kernel/irq/proc.c:46` |
| `irq_affinity_hint_proc_show()` | `kernel/irq/proc.c:83` |
| `irq_affinity_proc_show()` | `kernel/irq/proc.c:104` |
| `irq_affinity_list_proc_show()` | `kernel/irq/proc.c:109` |
| `write_irq_affinity()` | `kernel/irq/proc.c:137` |
| `irq_effective_aff_proc_show()` | `kernel/irq/proc.c:218` |
| `irq_effective_aff_list_proc_show()` | `kernel/irq/proc.c:223` |
| `default_affinity_show()` / `_write()` | `kernel/irq/proc.c:229` / `:235` |
| `irq_node_proc_show()` | `kernel/irq/proc.c:279` |
| `irq_spurious_proc_show()` | `kernel/irq/proc.c:288` |
| `no_irq_affinity` 全局开关 | `kernel/irq/proc.c:103` |

**GIC v3 的 SPI 有 `IRQD_SINGLE_TARGET`**（02 §6.2）：
你写 `smp_affinity=0xff`，`effective_affinity` 只会显示**一位**。
⇒ 这不是 bug，是硬件只能选一个目标 CPU。**必须看 `effective_affinity` 才知道真相。**

⚠️`write_irq_affinity()`（`kernel/irq/proc.c:137`）会先查
`irq_can_set_affinity_usr()`（03 §A9）——
托管中断（多队列设备）在这里返回 `-EPERM`（01 §10.4）。

---

## §6 debugfs 展示层：看层级域的唯一窗口

### §6.1 ⚠️ 它默认不开

`CONFIG_GENERIC_IRQ_DEBUGFS` 在 `kernel/irq/Kconfig:128`，
帮助文本里明确说了这是给内核开发者用的、生产环境不该开。
⇒ **发行版内核通常没有** `/sys/kernel/debug/irq/`。

### §6.2 目录结构

```
   /sys/kernel/debug/irq/
     ├── domains/
     │     ├── default        ← 所有域的一览
     │     └── <domain-name>  ← 单个域的详情（含层级）
     └── irqs/
           └── <N>            ← RW！单条中断的全部状态 + 故障注入
```

| 元素 | 位置 |
|---|---|
| `irq_debugfs_init()` | `kernel/irq/debugfs.c:243` |
| `debugfs_create_dir("irq", NULL)` | `kernel/irq/debugfs.c:248` |
| `irq_domain_debugfs_init()` 调用点 | `kernel/irq/debugfs.c:250` |
| `debugfs_create_dir("irqs", root_dir)` | `kernel/irq/debugfs.c:252` |
| 遍历已有中断补建条目 | `kernel/irq/debugfs.c:254-257` |
| `irq_add_debugfs_entry()` | `kernel/irq/debugfs.c:231` |
| 单条中断的文件（**0644**） | `kernel/irq/debugfs.c:239` |
| `irq_debugfs_copy_devname()` | `kernel/irq/debugfs.c:222` |
| `irq_domain_debugfs_init()` 实现 | `kernel/irq/irqdomain.c:1993` |
| `domains/default` 的创建 | `kernel/irq/irqdomain.c:2000` |
| 单个域文件的创建 | `kernel/irq/irqdomain.c:1985` |
| `irq_domain_debug_show_one()`（**递归打印层级**） | `kernel/irq/irqdomain.c:1949` |
| 递归调用父域那一句 | `kernel/irq/irqdomain.c:1961` |
| `irq_domain_debug_show()` | `kernel/irq/irqdomain.c:1965` |

**`kernel/irq/irqdomain.c:1961` 那句递归是本节的价值所在**：
`cat /sys/kernel/debug/irq/domains/<name>` 会**从这个域一路打印到最顶层的父域**，
每层缩进 4 格。⇒ 这是**唯一能直接看到层级域结构的地方**。

### §6.3 单条中断的视图：四组状态位一次全给

`irq_debug_show()`（`kernel/irq/debugfs.c:162`）会打印：

| 内容 | 数据来自 |
|---|---|
| `handler` / `device` / `status` | `irq_desc` |
| `istate`（`IRQS_*`） | `irqdesc_istates[]`（`kernel/irq/debugfs.c:149`） |
| `status`（`IRQ_*`） | `irqdesc_states[]`（`kernel/irq/debugfs.c:137`） |
| `chip flags`（`IRQCHIP_*`） | `irqchip_flags[]`（`kernel/irq/debugfs.c:50`） |
| `data state`（`IRQD_*`） | `irqdata_states[]`（`kernel/irq/debugfs.c:100`） |
| 亲和性掩码 | `irq_debug_show_masks()`（`kernel/irq/debugfs.c:30`；非 SMP 时是空函数，`kernel/irq/debugfs.c:47`） |
| 每一层的 chip / hwirq / domain | 沿 `parent_data` 递归 |

**这一个文件把 02 §6 的四组 flag 全部译成了人话** ——
这就是 03 §C1.2 里 `___PD` 空实现"不要紧"的原因：debugfs 这边是完整的。

### §6.4 故障注入：往 `irqs/<N>` 里写 `trigger`

```c
static ssize_t irq_debug_write(struct file *file, const char __user *user_buf,
			       size_t count, loff_t *ppos)
{
	struct irq_desc *desc = file_inode(file)->i_private;
	char buf[8] = { 0, };
	size_t size;

	size = min(sizeof(buf) - 1, count);
	if (copy_from_user(buf, user_buf, size))
		return -EFAULT;

	if (!strncmp(buf, "trigger", size)) {
		int err = irq_inject_interrupt(irq_desc_get_irq(desc));

		return err ? err : count;
	}

	return count;
}
```

| 元素 | 行号 |
|---|---|
| `irq_debug_write()` | `kernel/irq/debugfs.c:194` |
| `char buf[8]` | `kernel/irq/debugfs.c:198` |
| `copy_from_user` 失败 ⇒ `-EFAULT` | `kernel/irq/debugfs.c:202-203` |
| `strncmp(buf, "trigger", size)` | `kernel/irq/debugfs.c:205` |
| `irq_inject_interrupt()` 调用 | `kernel/irq/debugfs.c:206` |
| 认不出的字符串**静默返回 count** | `kernel/irq/debugfs.c:211` |
| `dfs_irq_ops` | `kernel/irq/debugfs.c:214` |

⚠️ **两个坑**：
1. `buf[8]` 且用 `strncmp(buf, "trigger", size)` 比较 —— `size` 是**用户写入的字节数**。
   `echo trigger > …` 写进来的是 8 字节（含换行），`size` 被 `min()` 限成 7，
   刚好比完 `"trigger"` 七个字符 ⇒ **`echo` 能用**。
2. **写一个认不出的字符串会静默成功**（`kernel/irq/debugfs.c:211` 直接 `return count`），
   不报错。⇒ 写错了不会有任何提示。

这个 `trigger` 是 irq 子系统**唯一的故障注入入口**，07 篇会展开。

---

## §7 中断模拟器 `irq_sim`

`CONFIG_IRQ_SIM`，250 行。用途：**给不需要真硬件的测试提供"假中断"**。
典型使用者是 gpio-mockup 之类的测试驱动。

| 元素 | 位置 |
|---|---|
| `irq_sim_irqchip`（一个最小的 `irq_chip`） | `kernel/irq/irq_sim.c:91` |
| `irq_sim_irqmask()` / `irq_sim_irqunmask()` | `kernel/irq/irq_sim.c:27` / `:34` |
| `irq_sim_set_type()` | `kernel/irq/irq_sim.c:41` |
| `irq_sim_get_irqchip_state()` / `_set_` | `kernel/irq/irq_sim.c:52` / `:70` |
| `irq_sim_handle_irq()`（`irq_work` 回调） | `kernel/irq/irq_sim.c:100` |
| `irq_sim_domain_ops` | `kernel/irq/irq_sim.c:149` |
| `irq_domain_create_sim()` | `kernel/irq/irq_sim.c:164`（导出于 `:195`） |
| `irq_domain_remove_sim()` | `kernel/irq/irq_sim.c:203`（导出于 `:213`） |
| `devm_irq_domain_create_sim()` | `kernel/irq/irq_sim.c:233`（导出于 `:250`） |

**它是"如何写一个最小 irq_chip + irq_domain"的现成范例**（250 行、无硬件依赖）。
⇒ 05 篇的骨架会拿它做对照。

它用 `irq_work` 而不是直接调 `generic_handle_irq()`（`kernel/irq/irq_sim.c:100`），
因为"注入中断"的调用者可能在任意上下文里，
`irq_work` 保证 handler 在硬中断上下文运行。

---

## §8 中断间隔预测 `timings`

`CONFIG_IRQ_TIMINGS`，958 行 —— **`kernel/irq/` 里第六大的文件**，
但它的服务对象只有一个：**cpuidle governor**（`teo` / `menu`）。

| 元素 | 位置 |
|---|---|
| `irq_timings_enable()` / `irq_timings_disable()` | `kernel/irq/timings.c:26` / `:31` |
| `irq_timings_next_event(now)` | `kernel/irq/timings.c:536` |
| `irq_timings_alloc(irq)` / `irq_timings_free(irq)` | `kernel/irq/timings.c:605` / `:594` |
| 采样点（每次中断） | `kernel/irq/handle.c:145` 的 `record_irq_time(desc)` |
| 参与开关 | `IRQS_TIMINGS`（`kernel/irq/internals.h:66`） |

**原理**：记录每条中断的到达间隔，用一个"周期检测"算法预测下一次何时到来，
让 cpuidle 决定"要不要进深睡眠"。

⚠️ arm64 上默认**不开**（Kconfig 里没有 `default y`，要由 governor 侧 `select`）。
⇒ 三等证据（99 §2.2），本套不展开算法。

---

## §9 矩阵分配器 `matrix`：x86 专用

`CONFIG_GENERIC_IRQ_MATRIX_ALLOCATOR`，513 行。**arm64 不开**。

它解决的是 x86 特有的问题：**中断向量是 per-CPU 的稀缺资源**
（每个 CPU 只有 ~220 个可用向量），分配时要在
"哪个 CPU 上分配"和"分配哪个向量号"两个维度上做决策。

| 数据结构 | 位置 |
|---|---|
| `struct cpumap`（per-CPU 的位图 + 四个计数） | `kernel/irq/matrix.c:13` |
| `IRQ_MATRIX_SIZE` | `kernel/irq/matrix.c:11` |
| 五处 `-ENOSPC` | `kernel/irq/matrix.c:236` · `:297` · `:305` · `:392` · `:397` |

**arm64 为什么不需要**：GIC 的 SPI 号是全局的、DT 里写死的；
LPI 由 ITS 从一个 16 位空间里分配，不分 CPU。
⇒ 没有"per-CPU 向量稀缺"这个问题。

本节列在这里只为**说明那五处 `-ENOSPC` 和 arm64 无关**（01 §10.1）。

---

## §10 PM：suspend / resume 时的中断处理

### §10.1 三类中断的不同待遇

| 类别 | 判据 | suspend 时 |
|---|---|---|
| 普通中断 | 默认 | 被屏蔽（置 `IRQS_SUSPENDED`） |
| 唤醒源 | `enable_irq_wake()` 设过（`wake_depth > 0`） | **保持使能** |
| `IRQF_NO_SUSPEND` | 申请时设的 | 保持使能，但**不能唤醒系统** |

| 元素 | 位置 |
|---|---|
| `irq_pm_install_action()` | `kernel/irq/pm.c:33` |
| `irq_pm_remove_action()` | `kernel/irq/pm.c:57` |
| `suspend_device_irq()`（单条） | `kernel/irq/pm.c:70` |
| `suspend_device_irqs()` | `kernel/irq/pm.c:131` |
| `resume_irq()`（单条） | `kernel/irq/pm.c:151` |
| `resume_irqs()` | `kernel/irq/pm.c:183` |
| `rearm_wake_irq()` | `kernel/irq/pm.c:208` |
| `irq_pm_syscore_resume()` | `kernel/irq/pm.c:233` |
| `irq_pm_syscore_ops` | `kernel/irq/pm.c:238` |
| `resume_device_irqs()` | `kernel/irq/pm.c:257` |

**`irq_pm_install_action()`（`kernel/irq/pm.c:33`）在 `__setup_irq()` 里被调**，
它维护 `irq_desc` 的四个 PM 计数（02 §5.1）：
`nr_actions` / `no_suspend_depth` / `cond_suspend_depth` / `force_resume_depth`。

⚠️ **`IRQF_NO_SUSPEND` 和 `enable_irq_wake()` 混用会被告警**（01 §2.4）：
`irq_pm_install_action()` 里有专门的一致性检查 ——
一条中断上一部分 action 设了 `IRQF_NO_SUSPEND`、一部分没设，
就必须都带 `IRQF_COND_SUSPEND`，否则报警。

### §10.2 唤醒后的两阶段 resume

`resume_irqs(bool want_early)`（`kernel/irq/pm.c:183`）被调**两次**：

| 阶段 | 谁调 | 恢复哪些 |
|---|---|---|
| early | `irq_pm_syscore_resume()`（`kernel/irq/pm.c:233`） | `force_resume_depth > 0` 的（如定时器） |
| 正常 | `resume_device_irqs()`（`kernel/irq/pm.c:257`） | 其余全部 |

⇒ **定时器中断必须在其它设备之前恢复**，否则 resume 过程本身会卡住。

---

## §11 软件重发 `resend`

### §11.1 它解决什么问题

场景：中断在 `disable_irq()` 期间到达了（被置 `IRQS_PENDING`，03 §B2），
`enable_irq()` 时**这一次中断已经丢了** —— 硬件的 pending 位可能已经被清。
⇒ 需要"补一次"。

```
   __enable_irq()
     └─ check_irq_resend(desc, inject=false)      ← kernel/irq/resend.c:106
          ├─ ① try_retrigger(desc)                硬件能重发就用硬件
          │     └─ chip->irq_retrigger 或 irq_chip_retrigger_hierarchy()
          └─ ② 不行 ⇒ irq_sw_resend(desc)         软件模拟
                ├─ 把 virq 记进 irqs_resend 位图
                └─ tasklet_schedule(&resend_tasklet)
                      └─ resend_irqs(): 逐个 desc->handle_irq(desc)
```

| 元素 | 位置 |
|---|---|
| `irqs_resend` 位图 | `kernel/irq/resend.c:25` |
| `resend_irqs()`（tasklet 回调） | `kernel/irq/resend.c:30` |
| `resend_tasklet` | `kernel/irq/resend.c:48` |
| `irq_sw_resend()`（有实体的版本） | `kernel/irq/resend.c:50` |
| `irq_sw_resend()`（空实现，`!HARDIRQS_SW_RESEND`） | `kernel/irq/resend.c:83` |
| `try_retrigger()` | `kernel/irq/resend.c:89` |
| `check_irq_resend()` | `kernel/irq/resend.c:106` |
| `irq_inject_interrupt()` | `kernel/irq/resend.c:155`（导出于 `:183`） |
| 它内部调 `check_irq_resend(desc, true)` | `kernel/irq/resend.c:178` |

**`irq_inject_interrupt()` 就是 §6.4 那个 debugfs `trigger` 背后的函数**，
也是 01 §2.5 那个对外 API。⇒ **`GENERIC_IRQ_INJECTION` 依赖 `HARDIRQS_SW_RESEND`**。

⚠️`irq_sw_resend()` 用的是 **tasklet**（`kernel/irq/resend.c:48`），
所以重发的中断**不在原来的硬中断上下文里**，
而是在 softirq 上下文。⇒ 对 `IRQF_PERCPU` 中断这条路走不通（代码里有拒绝）。

---

## §12 汇总表

| § | 框架 | 职责 | 挂在 03 时间线的哪一点 | arm64 相关 | 最该记住的一条 |
|---|---|---|---|---|---|
| §1 | generic-chip | "一位一条中断"的控制器模板 | irqchip 驱动 probe 时 | | `irq_generic_chip_ops` 连 `irq_domain_ops` 都替你填了；但**不支持层级域** |
| §2 | MSI 域 | 运行期分配中断并写进设备 | 建域时 + `.activate` 时 | | `.activate` 才是"MSI 真正生效"那一步 |
| §3 | IPI 域 | 把 IPI 也当 irq 管 | 建域时 | | **arm64 不走这个框架**（SGI 由 arch 代码直接管） |
| §4 | 乱中断检测 | `nobody cared` | 每次中断分发后 | | arm64 上看到它 = 驱动的 handler 返回值错了 |
| §5 | procfs | `/proc/interrupts` + `/proc/irq/N/` | 映射建立时建目录 | | `smp_affinity`（你要的）≠ `effective_affinity`（实际的） |
| §6 | debugfs | 层级域 + 四组状态位 + 故障注入 | 映射建立时建条目 | | **看层级域的唯一窗口**；⚠️ 默认不开 |
| §7 | irq_sim | 假中断 | 自己建域，完全独立 | | 250 行的最小 `irq_chip` + 域范例 |
| §8 | timings | 预测下一次中断 | 每次分发时采样 | | 服务对象只有 cpuidle governor |
| §9 | matrix | per-CPU 向量分配 | 亲和性变更时 | | **x86 专用**；那五处 `-ENOSPC` 与 arm64 无关 |
| §10 | PM | suspend 时谁保持使能 | `request_irq` 时登记 | | `IRQF_NO_SUSPEND` ≠ 唤醒源；定时器要 early resume |
| §11 | resend | 补一次丢掉的中断 | `enable_irq()` 时 / 手工注入 | | 它是故障注入的底层实现 |

**十一节里只有 §2 参与"中断能不能建起来"**（MSI 设备），
其余十节分别是可观测性（§5 §6 §8）、健壮性（§4 §10 加 §11）、
省代码（§1）、非 arm64（§3 §9）、测试（§7）。
⇒ 调"中断收不到"看 03 篇的时间线 + 本篇 §5 §6；调"中断来了但不对"看 §4。

---

**下一篇**：05 篇给出消费者、一级控制器、二级/级联控制器、层级域四个角色的代码骨架。
