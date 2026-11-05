---
title: 内部实现
---

# irq 03：实现剖析

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ 本篇所有 `文件:行号` 都写完整路径；ASCII 图里不写行号，行号一律放在图下方的表里。

---

## §0 导航

### §0.1 本篇只承诺一条主轴，其余显式分区

| 分区 | 主轴 | 章节号 |
|---|---|---|
| **Part A** | **时间线**：从内核启动到一次中断被处理完，再到拆除 | §A1 … §A10 |
| **Part B** | **横切机制**：锁、内部状态机、lazy disable、层级域、共享中断 | §B1 … §B7 |
| **Part C** | **评价**：源码设计债与整体判断 | §C1 … §C5 |

### §0.2 分层图

```
   ┌── 消费者（设备驱动）─────────────────────────────────────┐
   │  request_threaded_irq / free_irq / enable_irq            │
   └──────────────────────────┬──────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────┐
   │  管理层  kernel/irq/manage.c                             │
   │  __setup_irq / __free_irq / 亲和性 / 唤醒 / 线程化        │
   └──────────────────────────┬──────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────┐
   │  分发层  kernel/irq/chip.c + kernel/irq/handle.c         │
   │  14 个流处理器 → handle_irq_event → action 链            │
   └──────────────────────────┬──────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────┐
   │  映射层  kernel/irq/irqdomain.c + kernel/irq/irqdesc.c   │
   │  hwirq ↔ virq · irq_desc 的分配与查找                    │
   └──────────────────────────┬──────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────┐
   │  硬件层  drivers/irqchip/irq-gic-v3.c                    │
   │  irq_chip 回调 + irq_domain_ops + gic_handle_irq         │
   └──────────────────────────────────────────────────────────┘
```

### §0.3 生命周期时间线

```
   启动
    │
    ├─ §A1  early_irq_init()                 建 irq_desc 基础设施
    │
    ├─ §A2  of_irq_init()                    早于设备模型！
    │        └─ gic_of_init()                irqchip 驱动被调用
    │             ├─ §A3 irq_domain_create_tree()   建域
    │             └─ set_handle_irq()               装上 arch 入口
    │
    ├─ §A4  设备驱动 probe                    platform_get_irq() → virq
    │        └─ 域的映射被建立
    │
    ├─ §A5  request_threaded_irq()            挂上 action，使能中断
    │
    ├─ §A6  ──── 运行期：中断来了 ────         流处理器 → action 链
    │        §A7 线程化的那一半
    │
    ├─ §A9  运行期：改亲和性
    ├─ §A10 运行期：CPU 下线，中断迁走
    │
    └─ §A8  free_irq()                        摘掉 action，可能关掉中断
```

### §0.4 哪些内容不在本篇

| 内容 | 去哪 |
|---|---|
| API 签名、错误码全表 | 01 篇 |
| 结构体字段、状态位、全局变量 | 02 篇 |
| MSI / generic-chip / IPI / 乱中断检测这四个独立框架 | 04 篇 |
| 怎么写 irqchip 驱动 / 消费者代码 | 05 篇 |
| DTS 的 `interrupts` / `interrupt-parent` 怎么写 | 06 篇 |
| tracepoint、`/proc/interrupts`、故障注入 | 07 篇 |
| 「为什么是这样」的大白话解释 | 08 篇 |

---

# Part A：时间线

## §A1 早期初始化：`early_irq_init()`

`kernel/irq/irqdesc.c` 里有**两个** `early_irq_init()`，靠 `CONFIG_SPARSE_IRQ` 二选一：

| 实现 | 位置 | arm64 走哪个 |
|---|---|---|
| sparse 版 | `kernel/irq/irqdesc.c:526` | **这个** |
| 静态数组版 | `kernel/irq/irqdesc.c:565` | 不走 |

两套实现的分界线：`kernel/irq/irqdesc.c:136` 的 `#ifdef CONFIG_SPARSE_IRQ`，
到 `kernel/irq/irqdesc.c:555` 的 `#else`，`kernel/irq/irqdesc.c:639` 的 `#endif`。
⇒ `irq_to_desc()` 因此有两个定义（`kernel/irq/irqdesc.c:354` 和 `kernel/irq/irqdesc.c:589`）。

sparse 版建立的三样东西（02 §7.1）：

| 东西 | 位置 |
|---|---|
| `allocated_irqs` 位图 | `kernel/irq/irqdesc.c:134` |
| `irq_desc_tree`（radix tree） | `kernel/irq/irqdesc.c:347` |
| `sparse_irq_lock` | `kernel/irq/irqdesc.c:133` |

此时**还没有任何 `irq_desc`**。它们要等到域建立映射时才按需分配（§A4）。

## §A2 中断控制器初始化：早于设备模型

```
   start_kernel()
     └─ init_IRQ()                    ← arch/arm64/kernel/irq.c
          └─ irqchip_init()
               └─ of_irq_init(__irqchip_of_table)
                    │  ① 扫描 DT，找出所有匹配 __irqchip_of_table 的节点
                    │  ② 按 interrupt-parent 排出拓扑序（父先于子）
                    │  ③ 逐个调用它们的 init 回调
                    ▼
                 gic_of_init(node, parent)
```

| 关键位置 | 文件 |
|---|---|
| `of_irq_init()` | `drivers/of/irq.c:553` |
| `IRQCHIP_DECLARE` 宏 | `include/linux/irqchip.h:36` |
| GIC v3 的登记 | `drivers/irqchip/irq-gic-v3.c:2255` |
| `gic_of_init()` | `drivers/irqchip/irq-gic-v3.c:2185` |
| `gic_init_bases()` | `drivers/irqchip/irq-gic-v3.c:1930` |

**三个后果**，全套文档反复用到：

| 后果 | 依据 |
|---|---|
| 一级中断控制器**没有 `struct device`** | `of_irq_init()` 只传 `struct device_node *`（`drivers/of/irq.c:553`） |
| ⇒ **不能用 `devm_*`** | 01 §8 末尾那条 |
| ⇒ **不能返回 `-EPROBE_DEFER`** | `of_irq_init()` 没有重试机制；依赖关系靠第 ② 步的拓扑排序解决 |

⚠️ 第 ② 步的排序**只看 `interrupt-parent`**。
如果你的控制器依赖一个不是它的中断父节点的资源（比如一个时钟），
那个资源在这一刻可能还不存在 ⇒ 只能在驱动里直接读寄存器 / 用 `of_clk_get` 之类的早期接口。

## §A3 域的创建

GIC v3 建的是**树型层级域**：

```
   gic_init_bases()
     ├─ irq_domain_create_tree(handle, &gic_irq_domain_ops, &gic_data)
     │     └─ __irq_domain_add(fwnode, 0, ~0, 0, ops, host_data)
     │            ├─ 分配 struct irq_domain（变长，revmap[] 长度 = size = 0）
     │            ├─ 初始化 revmap_tree（radix tree）
     │            └─ list_add(&domain->link, &irq_domain_list)   ← 持 irq_domain_mutex
     │
     └─ set_handle_irq(gic_handle_irq)      把 arch 层的中断入口指向自己
```

| 关键位置 | 行号 |
|---|---|
| `irq_domain_create_tree()` 调用点 | `drivers/irqchip/irq-gic-v3.c:1972` |
| `set_handle_irq()` 调用点 | `drivers/irqchip/irq-gic-v3.c:1998` |
| `__irq_domain_add()` | `kernel/irq/irqdomain.c:245` |
| `set_handle_irq()` 实现 | `kernel/irq/handle.c:218` |
| `gic_irq_domain_ops` | `drivers/irqchip/irq-gic-v3.c:1687` |

**GIC v3 的 `irq_domain_ops` 填的是层级域那一套**
（`drivers/irqchip/irq-gic-v3.c:1688-1689` 的 `.translate` 和 `.alloc`），
不是 `.xlate` + `.map`（01 §5.2 的二选一）。
⇒ 这是因为 ITS 要挂在它之上（02 §1.0.B）。

为什么用 **tree** 而不是 linear：GIC v3 的 hwirq 空间是稀疏的 ——
SPI 在 32~1019，PPI 在 16~31，LPI 从 8192 开始，SGI 在 0~15。
linear 域要开一个 8192+ 的数组才装得下 LPI（02 §5.5）。

## §A4 消费者取中断号：映射在这一刻建立

```
   你的驱动 probe
     └─ platform_get_irq(pdev, 0)
          └─ of_irq_get(np, 0)                    ← drivers/of/irq.c
               ├─ of_irq_parse_one()              解析 interrupts 单元格
               │                                   → struct of_phandle_args
               ├─ irq_find_host(args.np)          找到 GIC 的域
               │     └─ 找不到 ⇒ return -EPROBE_DEFER
               └─ irq_create_of_mapping(&args)
                    └─ irq_create_fwspec_mapping(&fwspec)
                         ├─ domain->ops->translate(d, fwspec, &hwirq, &type)
                         │      ← gic_irq_domain_translate()
                         ├─ irq_find_mapping(domain, hwirq)   已有就直接返回
                         └─ 【层级域】irq_domain_alloc_irqs(domain, 1, NUMA_NO_NODE, &fwspec)
                              ├─ irq_domain_alloc_descs()      这里才分配 irq_desc
                              ├─ irq_domain_alloc_irq_data()   每层一个 irq_data
                              └─ irq_domain_alloc_irqs_hierarchy()
                                   └─ domain->ops->alloc()  ← gic_irq_domain_alloc()
                                        ├─ irq_domain_set_hwirq_and_chip()
                                        └─ （若有父域）irq_domain_alloc_irqs_parent()
```

| 关键位置 | 行号 |
|---|---|
| `irq_create_fwspec_mapping()` | `kernel/irq/irqdomain.c:807` |
| `irq_create_mapping_affinity()`（非层级路径） | `kernel/irq/irqdomain.c:745` |
| `__irq_domain_alloc_irqs()` | `kernel/irq/irqdomain.c:1554` |
| `irq_domain_alloc_irq_data()` | `kernel/irq/irqdomain.c:1329` |
| `irq_domain_alloc_irqs_hierarchy()` | `kernel/irq/irqdomain.c:1474` |
| `irq_domain_set_hwirq_and_chip()` | `kernel/irq/irqdomain.c:1380` |
| `irq_domain_set_info()` | `kernel/irq/irqdomain.c:1409`（另有 `!CONFIG_IRQ_DOMAIN_HIERARCHY` 版在 `kernel/irq/irqdomain.c:1923`） |
| `__irq_alloc_descs()` | `kernel/irq/irqdesc.c:791` |
| `alloc_descs()` | `kernel/irq/irqdesc.c:472` |
| `alloc_desc()` | `kernel/irq/irqdesc.c:392` |
| GIC 的 `translate` / `alloc` | `drivers/irqchip/irq-gic-v3.c:1530` / `:1603` |

**`irq_desc` 是在这一刻（而不是启动时）分配的** ——
这是 `CONFIG_SPARSE_IRQ` 的全部意义：
一台机器可能有几万条潜在中断（GIC v3 的 LPI），但实际用到的只有几十条。

**非层级域走的是另一条**：`irq_create_mapping_affinity()`
（`kernel/irq/irqdomain.c:745`）→ `irq_domain_associate()`
（`kernel/irq/irqdomain.c:634`）→ `ops->map()`。
两条路的分叉点在 `irq_create_fwspec_mapping()` 里看 `IRQ_DOMAIN_FLAG_HIERARCHY`。

## §A5 `request_threaded_irq()`：挂上 action

```
   request_threaded_irq(irq, handler, thread_fn, flags, name, dev)
     ├─ ① 参数与状态检查
     │     · (IRQF_SHARED && !dev_id) ⇒ -EINVAL
     │     · irq_settings_is_per_cpu_devid() 相关检查
     │     · irq_to_desc(irq) 为空 ⇒ -EINVAL
     │     · !irq_settings_can_request() ⇒ -EINVAL   ← 级联入口在这里被拒（02 §1.0.B）
     │
     ├─ ② kzalloc(struct irqaction) 并填 handler/thread_fn/flags/name/dev_id
     │
     ├─ ③ irq_chip_pm_get()            让 chip 所在设备的 runtime PM 计数 +1
     │
     ├─ ④ __setup_irq(irq, desc, action)      主体
     │     ├─ !desc ⇒ -EINVAL
     │     ├─ chip == &no_irq_chip ⇒ -ENOSYS
     │     ├─ !try_module_get(desc->owner) ⇒ -ENODEV
     │     ├─ flags 里没写触发类型 ⇒ 用 irqd_get_trigger_type() 的默认值
     │     ├─ irq_setup_forced_threading(new)     ← threadirqs 时把它改成线程化
     │     ├─ 需要线程 ⇒ setup_irq_thread() 建 kthread
     │     ├─ mutex_lock(&desc->request_mutex)
     │     ├─ irq_request_resources(desc)         ← chip->irq_request_resources
     │     ├─ raw_spin_lock_irqsave(&desc->lock)
     │     ├─ 遍历已有 action 链：检查 IRQF_SHARED / 触发类型 / ONESHOT 兼容性
     │     │      不兼容 ⇒ -EBUSY（并打一条 dev_err）
     │     ├─ 分配 thread_mask 位
     │     ├─ 首个 action：__irq_set_trigger() + irq_startup() 或 __enable_irq()
     │     ├─ *old_ptr = new                     挂进链表尾
     │     ├─ raw_spin_unlock_irqrestore
     │     ├─ mutex_unlock(&desc->request_mutex)
     │     └─ wake_up_and_wait_for_irq_thread_ready()
     │
     └─ ⑤ 失败则回滚 ③ 和 ②
```

| 关键位置 | 行号 |
|---|---|
| `request_threaded_irq()` | `kernel/irq/manage.c:2138` |
| `__setup_irq()` | `kernel/irq/manage.c:1497` |
| `!desc ⇒ -EINVAL` | `kernel/irq/manage.c:1503-1504` |
| `chip == &no_irq_chip ⇒ -ENOSYS` | `kernel/irq/manage.c:1506-1507` |
| `try_module_get ⇒ -ENODEV` | `kernel/irq/manage.c:1508-1509` |
| 触发类型默认值 | `kernel/irq/manage.c:1516-1517` |
| `irq_setup_forced_threading()` | `kernel/irq/manage.c:1358` |
| `irq_request_resources()` | `kernel/irq/manage.c:1397` |
| `__irq_set_trigger()` | `kernel/irq/manage.c:946` |
| `can_request_irq()` | `kernel/irq/manage.c:928` |
| `__setup_irq()` 的五个调用点 | `kernel/irq/manage.c:2198`（threaded）· `:2339`（any_context）· `:2567`（percpu）· `:2626`（nmi）· `:2695`（percpu_nmi） |
| `irq_default_primary_handler()` | `kernel/irq/manage.c:1023` |
| `irq_nested_primary_handler()` | `kernel/irq/manage.c:1032` |
| `irq_forced_secondary_handler()` | `kernel/irq/manage.c:1038` |

**`-ENOSYS` 在 `kernel/irq/manage.c:1507` 的语义**：
这条中断的 chip 还是 `no_irq_chip`（02 §5.4 末），
也就是"这条 virq 的 `irq_desc` 存在，但没有任何控制器认领它"。
⇒ 常见原因：`irq_alloc_descs()` 手工分配了 desc 却忘了
`irq_set_chip_and_handler()`（01 §4.2）。

**三个内部 primary handler** 的用途：

| handler | 什么时候被塞进来 |
|---|---|
| `irq_default_primary_handler` | 调用者传了 `thread_fn` 但 `handler` 为 NULL —— 它直接返回 `IRQ_WAKE_THREAD` |
| `irq_nested_primary_handler` | `IRQ_NESTED_THREAD` 的中断 —— 它只 `WARN` 一次（这类中断不该走正常分发） |
| `irq_forced_secondary_handler` | `threadirqs` 强制线程化 + `IRQF_ONESHOT` 时的第二个 action（02 §5.6 的 `secondary`） |

## §A6 一次中断的完整分发（arm64 / GIC v3）

```
   ① 硬件：GIC 拉高 IRQ，CPU 进异常向量
        arch/arm64/kernel/entry.S → el1_irq / el0_irq
   ② arch 通用入口
        generic_handle_arch_irq(regs)      ← kernel/irq/handle.c
             └─ handle_arch_irq(regs)      ← 函数指针，由 set_handle_irq() 装
   ③ irqchip 入口
        gic_handle_irq(regs)               ← drivers/irqchip/irq-gic-v3.c
             ├─ irqnr = read ICC_IAR1_EL1  硬件给出 hwirq 并"acknowledge"
             ├─ SGI/PPI/SPI ⇒ generic_handle_domain_irq(gic_data.domain, irqnr)
             └─ LPI          ⇒ 走 ITS 的域
   ④ 核心查表
        generic_handle_domain_irq(domain, hwirq)      ← kernel/irq/irqdesc.c
             ├─ __irq_resolve_mapping(domain, hwirq)  → irq_desc
             └─ handle_irq_desc(desc)
                  └─ desc->handle_irq(desc)           流处理器
   ⑤ 流处理器（GIC v3 用 handle_fasteoi_irq）
        raw_spin_lock(&desc->lock)
        irq_may_run(desc)?              ← 三道并发/唤醒检查
        !action || IRQD_IRQ_DISABLED?   ⇒ 置 IRQS_PENDING + mask + goto out
        kstat_incr_irqs_this_cpu(desc)
        IRQS_ONESHOT?                   ⇒ mask_irq(desc)
        handle_irq_event(desc)          见 ⑥
        cond_unmask_eoi_irq(desc, chip) EOI（可能推迟到线程）
        raw_spin_unlock(&desc->lock)
   ⑥ 跑 action 链
        handle_irq_event(desc)
             ├─ desc->istate &= ~IRQS_PENDING
             ├─ irqd_set(IRQD_IRQ_INPROGRESS)
             ├─ raw_spin_unlock(&desc->lock)    ← 释放锁再跑 handler！
             ├─ handle_irq_event_percpu(desc)
             │     ├─ __handle_irq_event_percpu(desc)
             │     │     └─ for_each_action_of_desc(desc, action):
             │     │            trace_irq_handler_entry(irq, action)
             │     │            res = action->handler(irq, action->dev_id)   你的代码
             │     │            trace_irq_handler_exit(irq, action, res)
             │     │            WARN_ONCE(!irqs_disabled(), …)   handler 开了中断就报
             │     │            res == IRQ_WAKE_THREAD ⇒ __irq_wake_thread()
             │     │            retval |= res
             │     ├─ add_interrupt_randomness(irq)
             │     └─ !IRQ_NO_DEBUG ⇒ note_interrupt(desc, retval)
             ├─ raw_spin_lock(&desc->lock)
             └─ irqd_clear(IRQD_IRQ_INPROGRESS)
```

| 关键位置 | 行号 |
|---|---|
| `generic_handle_arch_irq()` | `kernel/irq/handle.c:232` |
| `set_handle_irq()` | `kernel/irq/handle.c:218` |
| `gic_handle_irq()` | `drivers/irqchip/irq-gic-v3.c:834` |
| `generic_handle_domain_irq()` | `kernel/irq/irqdesc.c:706` |
| `__irq_resolve_mapping()` | `kernel/irq/irqdomain.c:949` |
| `handle_fasteoi_irq()` | `kernel/irq/chip.c:689` |
| `irq_may_run()` | `kernel/irq/chip.c:504` |
| `!action \|\| disabled` 分支 | `kernel/irq/chip.c:704-708` |
| `IRQS_ONESHOT ⇒ mask_irq()` | `kernel/irq/chip.c:711-712` |
| `handle_irq_event()` 调用点 | `kernel/irq/chip.c:714` |
| `cond_unmask_eoi_irq()` 调用点 | `kernel/irq/chip.c:716` |
| `out:` 分支的 `IRQCHIP_EOI_IF_HANDLED` 判断 | `kernel/irq/chip.c:720-722` |
| `handle_irq_event()` | `kernel/irq/handle.c:202` |
| 释放锁 / 重新加锁 | `kernel/irq/handle.c:208` / `kernel/irq/handle.c:212` |
| `handle_irq_event_percpu()` | `kernel/irq/handle.c:189` |
| `note_interrupt()` 调用点 | `kernel/irq/handle.c:198` |
| `__handle_irq_event_percpu()` | `kernel/irq/handle.c:139` |
| `trace_irq_handler_entry` / `_exit` | `kernel/irq/handle.c:157` / `kernel/irq/handle.c:159` |
| 调你的 handler | `kernel/irq/handle.c:158` |
| `WARN_ONCE(!irqs_disabled(), …)` | `kernel/irq/handle.c:161-163` |
| `IRQ_WAKE_THREAD` 分支 | `kernel/irq/handle.c:166-177` |
| `warn_no_thread()` 调用点 | `kernel/irq/handle.c:172` |
| `__irq_wake_thread()` 调用点 | `kernel/irq/handle.c:176` |
| `retval \|= res` | `kernel/irq/handle.c:183` |
| `handle_bad_irq()` | `kernel/irq/handle.c:33` |

**`kernel/irq/handle.c:208` 那一句 `raw_spin_unlock(&desc->lock)` 是全子系统最关键的一行**：
设备 handler 是在**不持 `desc->lock`** 的情况下跑的。
两个后果：
- 好处：handler 里可以调 `disable_irq_nosync()` 这类需要拿锁的函数；
- ⇒ 并发保护改由 **`IRQD_IRQ_INPROGRESS`** 承担（`kernel/irq/handle.c:207` 置位、`:213` 清位），
  而 `irq_may_run()`（`kernel/irq/chip.c:504`）在入口处检查它。

**`kernel/irq/handle.c:161` 那个 `WARN_ONCE` 是最有用的一条内核告警**：
`irq %u handler %pS enabled interrupts` —— 你的 handler 里调了 `local_irq_enable()`
或者用了会开中断的函数（如 `spin_unlock_irq()` 配错）。
核心会替你 `local_irq_disable()` 兜底（`kernel/irq/handle.c:163`），但一定要修。

**`retval |= res` 的语义**（`kernel/irq/handle.c:183`）：
`IRQ_NONE`=0、`IRQ_HANDLED`=1、`IRQ_WAKE_THREAD`=2，
所以"或"起来只要有一个 handler 认领了就是 handled。
⇒ 这是共享中断的乱中断检测能工作的基础（§B5）。

---

## §A7 线程化的那一半

```
   ── 硬中断上下文 ──────────────────────────────────────────────
   action->handler 返回 IRQ_WAKE_THREAD
        └─ __irq_wake_thread(desc, action)
              ├─ set_bit(IRQTF_RUNTHREAD, &action->thread_flags)
              ├─ desc->threads_oneshot |= action->thread_mask
              ├─ atomic_inc(&desc->threads_active)
              └─ wake_up_process(action->thread)
   （若 IRQS_ONESHOT：中断线仍处于 mask 状态）
   ────────────────────────────────────────────────────────────

   ── 线程上下文（SCHED_FIFO）─────────────────────────────────
   irq_thread(action)
     ├─ irq_thread_set_ready(desc, action)
     ├─ sched_set_fifo(current)                 实时优先级
     ├─ handler_fn = 强制线程化 ? irq_forced_thread_fn : irq_thread_fn
     ├─ task_work_add(irq_thread_dtor)          线程被杀时的清理
     ├─ irq_thread_check_affinity(desc, action) 跟随中断的亲和性
     └─ while (!irq_wait_for_interrupt(action)) {
              irq_thread_check_affinity(desc, action)
              action_ret = handler_fn(desc, action)
                   └─ irq_thread_fn:
                        ret = action->thread_fn(action->irq, action->dev_id)  你的代码
                        irq_finalize_oneshot(desc, action)   这里 unmask
              action_ret == IRQ_WAKE_THREAD ⇒ irq_wake_secondary(desc, action)
              wake_threads_waitq(desc)          唤醒等在 synchronize_irq() 的人
        }
   ────────────────────────────────────────────────────────────
```

| 关键位置 | 行号 |
|---|---|
| `__irq_wake_thread()` | `kernel/irq/handle.c:61` |
| `irq_thread()` | `kernel/irq/manage.c:1286` |
| `irq_thread_set_ready()` 调用点 | `kernel/irq/manage.c:1294` |
| `sched_set_fifo(current)` | `kernel/irq/manage.c:1296` |
| 选 `handler_fn` | `kernel/irq/manage.c:1298-1302` |
| `task_work_add(irq_thread_dtor)` | `kernel/irq/manage.c:1305` |
| 主循环 | `kernel/irq/manage.c:1309` |
| 调 `handler_fn` | `kernel/irq/manage.c:1314` |
| `irq_wake_secondary()` 调用点 | `kernel/irq/manage.c:1316` |
| `wake_threads_waitq()` 调用点 | `kernel/irq/manage.c:1318` |
| 正常退出路径的注释 | `kernel/irq/manage.c:1321-1326` |
| `irq_thread_fn()` | `kernel/irq/manage.c:1200` |
| `irq_finalize_oneshot()` | `kernel/irq/manage.c:1074` |
| `irq_wait_for_interrupt()` | `kernel/irq/manage.c:1044` |
| `wake_threads_waitq()` | `kernel/irq/manage.c:1213` |
| `irq_thread_dtor()` | `kernel/irq/manage.c:1219` |
| `irq_wake_secondary()` | `kernel/irq/manage.c:1246` |
| `irq_thread_set_ready()` | `kernel/irq/manage.c:1261` |
| `wake_up_and_wait_for_irq_thread_ready()` | `kernel/irq/manage.c:1272` |
| `irq_wake_thread()`（对外的手工唤醒） | `kernel/irq/manage.c:1337` |

**`sched_set_fifo()`（`kernel/irq/manage.c:1296`）是线程化中断的定性**：
它们**不是普通内核线程**，而是实时优先级线程。
⇒ 一个 `thread_fn` 里的死循环会饿死同 CPU 上的普通任务。

**`irq_thread_check_affinity()` 在循环里被调了两次**
（`kernel/irq/manage.c:1307` 和 `kernel/irq/manage.c:1312`）——
因为中断的亲和性可能在线程睡着的时候被改了，
线程必须跟着迁移到新的 CPU 上（否则跨 CPU 访问硬件）。

**`irq_finalize_oneshot()` 的第一句就把 `irq_forced_secondary_handler` 排除掉**
（`kernel/irq/manage.c:1077-1079`）——
因为强制线程化产生的 secondary action 不该重复 unmask。
它还有一个 `again:` 重试标签（`kernel/irq/manage.c:1080`），
配合 `chip_bus_lock()`（`kernel/irq/manage.c:1081`）应对慢总线上的竞争。

## §A8 `free_irq()`：拆除

```
   free_irq(irq, dev_id)
     ├─ 各种前置检查（per-cpu 中断不能用这个）
     ├─ chip_bus_lock(desc)
     ├─ __free_irq(desc, dev_id)
     │    ├─ WARN(in_interrupt(), "Trying to free IRQ %d from IRQ context!")
     │    ├─ mutex_lock(&desc->request_mutex)
     │    ├─ chip_bus_lock(desc)
     │    ├─ raw_spin_lock_irqsave(&desc->lock)
     │    ├─ 遍历 action 链按 dev_id 找到目标；找不到 ⇒ WARN + 返回 NULL
     │    ├─ 摘链：*action_ptr = action->next
     │    ├─ 最后一个 action ⇒ irq_shutdown_and_deactivate(desc)
     │    ├─ raw_spin_unlock_irqrestore
     │    ├─ synchronize_hardirq(irq)     等硬中断部分跑完
     │    ├─ 有线程 ⇒ kthread_stop() + put_task_struct()
     │    ├─ irq_release_resources(desc)
     │    ├─ mutex_unlock(&desc->request_mutex)
     │    └─ module_put(desc->owner)
     ├─ chip_bus_sync_unlock(desc)
     ├─ irq_chip_pm_put(&desc->irq_data)
     ├─ kfree(action->secondary)
     └─ return action->dev_id       返回值就是你传进来的 dev_id
```

| 关键位置 | 行号 |
|---|---|
| `free_irq()` | `kernel/irq/manage.c:2018` |
| `__free_irq()` | `kernel/irq/manage.c:1866` |
| `WARN(in_interrupt(), …)` | `kernel/irq/manage.c:1872` |
| 三重加锁 | `kernel/irq/manage.c:1874-1876` |
| 遍历找 action | `kernel/irq/manage.c:1882-1884` |
| `irq_shutdown_and_deactivate()` | `kernel/irq/chip.c:322` |
| `synchronize_hardirq()` | `kernel/irq/manage.c:98` |
| `__synchronize_hardirq()` | `kernel/irq/manage.c:38` |
| `synchronize_irq()` | `kernel/irq/manage.c:126` |
| `irq_release_resources()` | `kernel/irq/manage.c:1405` |

**`free_irq()` 不能在中断上下文调**（`kernel/irq/manage.c:1872` 的 `WARN`）：
它要 `mutex_lock` 和 `kthread_stop`，两者都会睡。
⇒ 01 §1.2 那条"会等所有正在执行的 handler 和线程结束"就是这个。

**`synchronize_hardirq()` 和 `synchronize_irq()` 的区别**：
前者只等硬中断部分（`kernel/irq/manage.c:98`），
后者还等线程（`kernel/irq/manage.c:126`，多一个 `wait_event(desc->wait_for_threads, …)`）。
`__free_irq()` 用的是**前者**，因为线程它自己 `kthread_stop()`。

## §A9 改亲和性

```
   irq_set_affinity(virq, mask)
     └─ __irq_set_affinity(irq, mask, force=false)
          ├─ irq_get_desc_buslock(irq, &flags, IRQ_GET_DESC_CHECK_GLOBAL)
          ├─ irq_set_affinity_locked(&desc->irq_data, mask, force)
          │    ├─ irq_set_affinity_deactivated()?   还没 activate ⇒ 只记下来
          │    ├─ irqd_is_setaffinity_pending()?    已有 pending ⇒ 只更新 pending_mask
          │    ├─ irq_try_set_affinity(data, mask, force)
          │    │    └─ chip->irq_set_affinity(data, mask, force)
          │    │         【层级域】irq_chip_set_affinity_parent() 往下传
          │    │         返回 IRQ_SET_MASK_OK / _NOCOPY / _DONE
          │    ├─ IRQ_SET_MASK_OK ⇒ irq_copy_pending / cpumask_copy(desc→affinity)
          │    ├─ irq_validate_effective_affinity(data)   检查 chip 有没有填
          │    └─ irq_set_thread_affinity(desc)           线程跟着迁
          └─ irq_put_desc_busunlock()
```

| 关键位置 | 行号 |
|---|---|
| `irq_set_affinity()` | `kernel/irq/manage.c:473` |
| `irq_force_affinity()` | `kernel/irq/manage.c:490` |
| `__irq_set_affinity()` | `kernel/irq/manage.c:450` |
| `irq_set_affinity_locked()` | `kernel/irq/manage.c:347` |
| `irq_set_affinity_deactivated()` | `kernel/irq/manage.c:323` |
| `irq_try_set_affinity()` | `kernel/irq/manage.c:308` |
| `irq_set_affinity_pending()`（两个版本） | `kernel/irq/manage.c:291` / `kernel/irq/manage.c:301` |
| `irq_do_set_affinity()` | `kernel/irq/manage.c:212` |
| `irq_validate_effective_affinity()`（两个版本） | `kernel/irq/manage.c:198` / `kernel/irq/manage.c:209` |
| `irq_set_thread_affinity()` | `kernel/irq/manage.c:188` |
| `__irq_can_set_affinity()` | `kernel/irq/manage.c:146` |
| `irq_can_set_affinity_usr()` | `kernel/irq/manage.c:171` |
| `irq_setup_affinity()`（两个版本） | `kernel/irq/manage.c:589` / `kernel/irq/manage.c:631` |
| `irq_update_affinity_desc()` | `kernel/irq/manage.c:395` |
| `-EOPNOTSUPP` 那一处 | `kernel/irq/manage.c:408` |
| `__irq_apply_affinity_hint()` | `kernel/irq/manage.c:496` |
| `irq_affinity_notify()` | `kernel/irq/manage.c:512` |

**`irq_validate_effective_affinity()`（`kernel/irq/manage.c:198`）只在
`CONFIG_GENERIC_IRQ_EFFECTIVE_AFF_MASK` 下有实体**，否则是空函数
（`kernel/irq/manage.c:209`）。它的作用是**在 chip 忘了设 `effective_affinity` 时打一条
`pr_warn_once`** —— 这是 02 §5.3 那对字段的守卫。

**`irq_can_set_affinity_usr()`（`kernel/irq/manage.c:171`）比
`irq_can_set_affinity()` 多一道门**：它额外拒绝 `IRQD_AFFINITY_MANAGED` 的中断。
⇒ 这就是"多队列网卡的中断在 `/proc/irq/N/smp_affinity` 上写不进去（`-EPERM`）"的原因（01 §10.4）。

## §A10 CPU 下线：中断迁走

| 步骤 | 位置 |
|---|---|
| CPU 下线钩子 | `kernel/irq/cpuhotplug.c:155` 的 `irq_migrate_all_off_this_cpu()` |
| 逐条判断要不要迁 | `kernel/irq/cpuhotplug.c:20` 的 `irq_needs_fixup()` |
| 迁一条 | `kernel/irq/cpuhotplug.c:53` 的 `migrate_one_irq()` |
| CPU 上线时恢复 | `kernel/irq/cpuhotplug.c:218` 的 `irq_affinity_online_cpu()` |
| 逐条恢复 | `kernel/irq/cpuhotplug.c:189` 的 `irq_restore_affinity_of_irq()` |
| 是否该隔离（`nohz_full`） | `kernel/irq/cpuhotplug.c:175` 的 `hk_should_isolate()` |

**托管中断（`IRQD_AFFINITY_MANAGED`）在最后一个可用 CPU 下线时不会被"随便迁走"**，
而是被**关闭**并打上 `IRQD_MANAGED_SHUTDOWN`（02 §6.2）。
CPU 回来时 `irq_affinity_online_cpu()` 再把它启回来。
⇒ 这是多队列设备"队列跟着 CPU 走"的实现。

---

# Part B：横切机制

## §B1 四把锁与锁序

### §B1.1 锁的清单（02 §7.2 的机制展开）

| 锁 | 类型 | 能睡吗 | 用在哪 |
|---|---|---|---|
| `desc->lock` | `raw_spinlock_t` | 不能 | 硬中断上下文 + 一切 desc 状态修改 |
| `desc->request_mutex` | mutex | 能 | `request_irq` / `free_irq` 全程 |
| `sparse_irq_lock` | mutex | 能 | `allocated_irqs` + `irq_desc_tree` 写 |
| `irq_domain_mutex` | mutex | 能 | `irq_domain_list` |
| `domain->revmap_mutex` | mutex | 能 | 单个域的反向映射 |
| `chip_bus_lock()` | 由 chip 决定 | **可能睡** | 慢总线上的 chip（I2C/SPI 扩展器） |

### §B1.2 锁序

`request_irq()` / `free_irq()` 的顺序（以 `__free_irq()` 为例，
`kernel/irq/manage.c:1874-1876`）：

```
   ① mutex_lock(&desc->request_mutex)
   ② chip_bus_lock(desc)                 ← 可能睡，所以必须在 ③ 之前
   ③ raw_spin_lock_irqsave(&desc->lock)
```

**`include/linux/irqdesc.h:50` 的字段注释直接把这个锁序写成了文档**：
「mutex to protect request/free **before** locking desc->lock」。

⚠️ **反序必然死锁**：`chip_bus_lock()` 可能睡，而 `desc->lock` 是 raw spinlock
（在 `PREEMPT_RT` 下更是不能睡的临界区）。

### §B1.3 分发路径上"故意不持锁"的那一段

| 位置 | 动作 |
|---|---|
| `kernel/irq/handle.c:207` | `irqd_set(IRQD_IRQ_INPROGRESS)` |
| `kernel/irq/handle.c:208` | **`raw_spin_unlock(&desc->lock)`** |
| `kernel/irq/handle.c:210` | 跑 action 链（你的 handler） |
| `kernel/irq/handle.c:212` | `raw_spin_lock(&desc->lock)` |
| `kernel/irq/handle.c:213` | `irqd_clear(IRQD_IRQ_INPROGRESS)` |

⇒ **并发保护从"锁"换成了"状态位"**：
`irq_may_run()`（`kernel/irq/chip.c:504`）在流处理器入口检查
`IRQD_IRQ_INPROGRESS | IRQD_WAKEUP_ARMED`，两者都没置才继续。

`irq_may_run()` 的三条出口：

| 条件 | 结果 |
|---|---|
| 两个位都没置 | `return true` —— 正常跑 |
| 是已武装的唤醒源 | `irq_pm_check_wakeup(desc)` 处理后 `return false` |
| 否则（说明别的 CPU 正在跑或正在轮询） | `return irq_check_poll(desc)` |

## §B2 `istate`：核心内部状态机

`irq_desc` 里那个字段的真名叫
`core_internal_state__do_not_mess_with_it`（02 §5.1），
`kernel/irq/internals.h:20` 用一个宏把它改名成 `istate`：

```c
#define istate core_internal_state__do_not_mess_with_it
```

⇒ **`istate` 这个名字只在 `kernel/irq/` 内部存在**，
驱动看到的是那个又长又难打的名字。这是"用命名做访问控制"的一个典型例子。

11 个 `IRQS_*` 位（`kernel/irq/internals.h:58-68`）：

| 位 | 行号 | 语义 |
|---|---|---|
| `IRQS_AUTODETECT` | `kernel/irq/internals.h:58` | ISA 自动探测中（arm64 不涉及） |
| `IRQS_SPURIOUS_DISABLED` | `kernel/irq/internals.h:59` | **因乱中断被强制关掉** |
| `IRQS_POLL_INPROGRESS` | `kernel/irq/internals.h:60` | 正在被轮询 |
| `IRQS_ONESHOT` | `kernel/irq/internals.h:61` | `IRQF_ONESHOT` 的内部形式 |
| `IRQS_REPLAY` | `kernel/irq/internals.h:62` | 软件重发中 |
| `IRQS_WAITING` | `kernel/irq/internals.h:63` | 自动探测时"还没来" |
| `IRQS_PENDING` | `kernel/irq/internals.h:64` | **来了但没处理**（disabled 或 edge 重入） |
| `IRQS_SUSPENDED` | `kernel/irq/internals.h:65` | suspend 期间被屏蔽 |
| `IRQS_TIMINGS` | `kernel/irq/internals.h:66` | 参与间隔预测 |
| `IRQS_NMI` | `kernel/irq/internals.h:67` | 是 NMI |
| `IRQS_SYSFS` | `kernel/irq/internals.h:68` | sysfs 目录已建 |

**`IRQS_PENDING` 是理解 lazy disable 和 edge 重入的钥匙**（§B3、§B6）：

| 谁置它 | 谁清它 |
|---|---|
| 流处理器发现中断被 disable（`kernel/irq/chip.c:705`） | `handle_irq_event()` 开头（`kernel/irq/handle.c:206`） |
| `handle_edge_irq` 发现重入 | `handle_fasteoi_irq` 入口（`kernel/irq/chip.c:698` 清 REPLAY/WAITING） |
| `__enable_irq()` 时用它决定要不要 resend | —— |

## §B3 lazy disable：`IRQD_IRQ_DISABLED` 与 `IRQD_IRQ_MASKED` 分离

| 阶段 | `IRQD_IRQ_DISABLED` | `IRQD_IRQ_MASKED` | 硬件状态 |
|---|---|---|---|
| 正常运行 | 0 | 0 | 使能 |
| `disable_irq()` 之后（lazy） | **1** | **0** | **硬件上仍然使能！** |
| 中断真的来了 | 1 | 0→**1** | 此刻才 mask，并置 `IRQS_PENDING` |
| `enable_irq()` 之后 | 0 | 0 | 使能（若之前 pending 则可能 resend） |

| 关键位置 | 行号 |
|---|---|
| `__disable_irq()` | `kernel/irq/manage.c:681` |
| `disable_irq()` | `kernel/irq/manage.c:728` |
| `__enable_irq()` | `kernel/irq/manage.c:776` |
| `enable_irq()` | `kernel/irq/manage.c:815` |
| `irq_disable()`（chip 层） | `kernel/irq/chip.c:385` |
| `irq_enable()`（chip 层） | `kernel/irq/chip.c:334` |
| 流处理器里的"来了才 mask" | `kernel/irq/chip.c:704-707` |

**为什么要 lazy**：很多控制器 mask/unmask 是一次寄存器写（甚至是一次 I2C 传输）。
如果 `disable_irq()` / `enable_irq()` 成对且中间没有中断到来（常见情况），
lazy 模式下**一次寄存器都不用写**。

⚠️ **lazy disable 的可见后果**：`disable_irq()` 之后，
**中断线在硬件上还是活的** —— 如果你的硬件在这期间会因为"未确认的中断"而卡住，
就必须设 `IRQ_DISABLE_UNLAZY`（02 §6.1）关掉这个优化。
典型场景：GPIO 中断 + 电平触发的外设。

`irq_disable()`（`kernel/irq/chip.c:385`）的实现就是这个策略的核心：
有 `chip->irq_disable` 就直接调（那说明 chip 想要立即生效），
否则**什么都不做**，只置状态位。

## §B4 层级域的分配与激活：两阶段

层级域的中断有**两个独立的阶段**，不能混：

| 阶段 | 做什么 | 状态位 | 入口 |
|---|---|---|---|
| **① 分配**（alloc） | 占住资源（向量号、ITS 表项），建好每层的 `irq_data` | —— | `irq_domain_alloc_irqs()` |
| **② 激活**（activate） | 真正"接上线"（MSI 里是**写 MSI 消息到设备**） | `IRQD_ACTIVATED` | `irq_domain_activate_irq()` |

| 关键位置 | 行号 |
|---|---|
| `__irq_domain_alloc_irqs()` | `kernel/irq/irqdomain.c:1554` |
| `irq_domain_alloc_irq_data()` | `kernel/irq/irqdomain.c:1329` |
| `irq_domain_alloc_irqs_hierarchy()` | `kernel/irq/irqdomain.c:1474` |
| `irq_domain_free_irqs()` | `kernel/irq/irqdomain.c:1755` |
| `irq_domain_free_irqs_parent()` | `kernel/irq/irqdomain.c:1799` |
| `irq_domain_free_irqs_common()` | `kernel/irq/irqdomain.c:1426` |
| `irq_domain_free_irqs_top()` | `kernel/irq/irqdomain.c:1447` |
| `__irq_domain_activate_irq()`（**递归**） | `kernel/irq/irqdomain.c:1821` |
| `irq_domain_activate_irq()` | `kernel/irq/irqdomain.c:1850` |
| `irq_domain_deactivate_irq()` | `kernel/irq/irqdomain.c:1869` |
| 两处 `-ENOSYS`（域回调缺失） | `kernel/irq/irqdomain.c:1480` · `kernel/irq/irqdomain.c:1786` |

**`__irq_domain_activate_irq()`（`kernel/irq/irqdomain.c:1821`）是递归的**：
它先递归激活 `parent_data`，再调本层的 `ops->activate`。
⇒ **激活是自底向上的**（先父后子），而分配是自顶向下的（先子后父）。
这个方向差异是层级域最容易搞错的地方，08 篇会用类比讲。

`irq_startup()`（`kernel/irq/chip.c:254`）里会调 `irq_domain_activate_irq()`
—— 所以对消费者来说，"激活"发生在 `request_irq()` 里，看不见。
`irq_activate_and_startup()`（`kernel/irq/chip.c:297`）是把两步合起来的便捷函数
（级联入口就用它，`kernel/irq/chip.c:1041`）。

## §B5 共享中断与乱中断检测

### §B5.1 共享的三个前提

`__setup_irq()` 遍历已有 action 链时检查三件事，任一不满足就 `-EBUSY`：

| 检查 | 说明 |
|---|---|
| 双方都设了 `IRQF_SHARED` | 老 action 和新 action 都要有 |
| 触发类型一致 | `IRQF_TRIGGER_MASK` 部分必须相同 |
| ONESHOT 一致 | 一方要 `IRQF_ONESHOT` 另一方不要 ⇒ 拒绝（除非 chip 有 `IRQCHIP_ONESHOT_SAFE`） |

### §B5.2 乱中断检测（`kernel/irq/spurious.c`）

```
   handle_irq_event_percpu()
     └─ !IRQ_NO_DEBUG ⇒ note_interrupt(desc, retval)
          ├─ retval == IRQ_NONE ⇒ desc->irqs_unhandled++
          ├─ 每 100000 次中断（desc->irq_count）结算一次
          └─ 未处理比例过高 ⇒ __report_bad_irq() + 关掉这条中断
                └─ printk "irq %d: nobody cared (try booting with the \"irqpoll\" option)"
                   置 IRQS_SPURIOUS_DISABLED
```

| 关键位置 | 行号 |
|---|---|
| `note_interrupt()` | `kernel/irq/spurious.c:272` |
| `__report_bad_irq()` | `kernel/irq/spurious.c:192` |
| `nobody cared` 那句 | `kernel/irq/spurious.c:202` |
| `try_one_irq()` | `kernel/irq/spurious.c:62` |
| `misrouted_irq()` | `kernel/irq/spurious.c:118` |
| `poll_spurious_irqs()` | `kernel/irq/spurious.c:144` |
| `irq_wait_for_poll()` | `kernel/irq/spurious.c:36` |
| `noirqdebug_setup()` | `kernel/irq/spurious.c:436` |
| `irqfixup_setup()` | `kernel/irq/spurious.c:448` |
| `irqpoll_setup()` | `kernel/irq/spurious.c:464` |

**`nobody cared` 的根因几乎总是"某个共享中断的 handler 该返回 `IRQ_NONE` 却返回了
`IRQ_HANDLED`，或者反过来"**（01 §1.3）。
⇒ 07 篇会给排查流程。

⚠️ 三个命令行开关：`noirqdebug`（关掉检测）、`irqfixup`（尝试重路由）、
`irqpoll`（更激进的轮询）。后两个主要给 x86 老硬件用，
**arm64 上遇到 `nobody cared` 应该去修驱动，不是加参数**。

## §B6 `IRQF_ONESHOT` 的完整闭环

```
   ① request_threaded_irq(…, IRQF_ONESHOT)
        └─ __setup_irq(): desc->istate |= IRQS_ONESHOT
                          分配 action->thread_mask 的一位

   ② 中断来了：handle_fasteoi_irq()
        └─ desc->istate & IRQS_ONESHOT ⇒ mask_irq(desc)     ← 屏蔽

   ③ handler 返回 IRQ_WAKE_THREAD
        └─ __irq_wake_thread(): desc->threads_oneshot |= action->thread_mask

   ④ 线程跑完 thread_fn
        └─ irq_finalize_oneshot(desc, action)
              ├─ desc->threads_oneshot &= ~action->thread_mask
              └─ threads_oneshot == 0 ⇒ unmask_irq(desc)    ← 解除屏蔽
```

| 关键位置 | 行号 |
|---|---|
| ② `IRQS_ONESHOT ⇒ mask_irq` | `kernel/irq/chip.c:711-712` |
| ③ `__irq_wake_thread()` | `kernel/irq/handle.c:61` |
| ④ `irq_finalize_oneshot()` | `kernel/irq/manage.c:1074` |
| ④ 排除 secondary handler 的第一句 | `kernel/irq/manage.c:1077-1079` |
| `unmask_irq()` | `kernel/irq/chip.c:431` |

**`threads_oneshot` 是一个位图，不是计数器**：
所以一条共享中断上有多个 ONESHOT action 时，
**必须所有线程都跑完才 unmask**。
⇒ 这也解释了 02 §5.6 那条"ONESHOT action 数量有机器字长上界"。

## §B7 两套 `irq_desc` 实现

| | `CONFIG_SPARSE_IRQ`（arm64） | `!CONFIG_SPARSE_IRQ` |
|---|---|---|
| 存储 | radix tree `irq_desc_tree`（`kernel/irq/irqdesc.c:347`） | 静态数组 `irq_desc[NR_IRQS]`（`kernel/irq/irqdesc.c:557`） |
| 分配时机 | **按需**（§A4） | 启动时全部就位 |
| `irq_to_desc()` | `kernel/irq/irqdesc.c:354` | `kernel/irq/irqdesc.c:589` |
| `early_irq_init()` | `kernel/irq/irqdesc.c:526` | `kernel/irq/irqdesc.c:565` |
| 释放 | `irq_kobj_release()`（`kernel/irq/irqdesc.c:428`）+ RCU | 不释放 |
| 代码分界 | `kernel/irq/irqdesc.c:136` … `:555` … `:639` | 同 |

**`irq_desc` 在 sparse 模式下带 `kobject`**（02 §5.1）——
所以每条中断在 `/sys/kernel/irq/N/` 下有一个目录，
而且它的释放是 kref + RCU 的（`kernel/irq/irqdesc.c:428`）。
⇒ `irq_to_desc()` 的返回值在 RCU 读侧临界区之外**不保证有效**，
这就是它不导出给模块的原因（01 §4.5）。

---

# Part C：评价

## §C1 源码里的自陈问题

全 `kernel/irq/` 的 `TODO` / `FIXME` / `XXX` / `REVISIT` / `HACK` 普查：

```bash
grep -rn 'TODO\|FIXME\|XXX\|REVISIT\|HACK' kernel/irq/*.c kernel/irq/*.h
```

| 标记 | 个数 | 位置 |
|---|---|---|
| `FIXME`（真正的注释） | **2** | `kernel/irq/ipi.c:246` · `kernel/irq/debug.h:8` |
| 名字里带 `FIXME` 的 **config 符号** | **1** | `kernel/irq/manage.c:2206` |
| `TODO` / `XXX` / `REVISIT` / `HACK` | **0** | —— |

⇒ **一个 15044 行的核心子系统只有两条 `FIXME`** —— 代码成熟度很高。

### §C1.1 `kernel/irq/ipi.c:246`：IPI 的 per-CPU 偏移查找

注释原文是「Store this information in irqdata flags」，
指的是紧随其后的 `irq_domain_is_ipi_per_cpu(data->domain)` 判断
（`kernel/irq/ipi.c:247`）—— 作者认为这个"是不是 per-CPU IPI 域"的信息
应该直接放在 `irq_data` 的 flags 里，而不是每次去问域。

影响：一次多余的间接访问，纯性能问题，不影响正确性。arm64 上 IPI 走 GIC 的 SGI，
这条路径不热。

### §C1.2 `kernel/irq/debug.h:8`：一个被禁用的调试打印

```c
#define ___P(f)  if (desc->status_use_accessors & f) printk("%14s set\n", #f)
#define ___PS(f) if (desc->istate & f) printk("%14s set\n", #f)
/* FIXME */
#define ___PD(f) do { } while (0)
```

| 宏 | 打印什么 | 状态 |
|---|---|---|
| `___P(f)` | `IRQ_*` 位（02 §6.1） | 工作 |
| `___PS(f)` | `IRQS_*` 位（§B2） | 工作 |
| `___PD(f)` | **本该打 `IRQD_*` 位**（02 §6.2） | **被改成了空操作** |

⇒ `print_irq_desc()` 的输出里**缺了 `IRQD_*` 那一整组**，
而那一组恰好是"这条中断当前的运行时状态"（disabled / masked / inprogress / level）。
⇒ **调试时不要指望 `print_irq_desc()`，用 `/sys/kernel/debug/irq/irqs/N`**（07 篇）。

### §C1.3 ⚠️`kernel/irq/manage.c:2206`：一段永久死代码

```c
#ifdef CONFIG_DEBUG_SHIRQ_FIXME
	if (!retval && (irqflags & IRQF_SHARED)) {
		/* … 申请成功后立刻假触发一次 handler，验证驱动准备好了 … */
	}
#endif
```

| 检查 | 结果 |
|---|---|
| 全树有 `CONFIG_DEBUG_SHIRQ_FIXME` 这个 Kconfig 项吗 | **没有** |
| 真正存在的是哪个 | `DEBUG_SHIRQ`（在 `lib/Kconfig.debug:1000`） |
| 全树引用 `DEBUG_SHIRQ_FIXME` 的地方 | **只有 `kernel/irq/manage.c:2206` 这一处** |

```bash
grep -rn 'DEBUG_SHIRQ' --include=Kconfig --include='Kconfig.*' .   # → 只有 lib/Kconfig.debug:1000 的 DEBUG_SHIRQ
grep -rn 'DEBUG_SHIRQ_FIXME' --include=*.c --include=*.h .          # → 只有 kernel/irq/manage.c:2206
```

⇒ **`kernel/irq/manage.c:2206-2224` 这 19 行是永远不会被编译进内核的死代码**。
它原本的意图很有价值：申请共享中断成功后立刻假调一次 handler，
验证"驱动是否已经准备好被随时中断"。
但当年因为误报太多被这样"注释掉"了，而且用的是**改 config 名而不是删代码**的方式。

这条对读者的实际意义：**别指望 `CONFIG_DEBUG_SHIRQ` 会帮你验证 irq handler**。
`DEBUG_SHIRQ` 现在只在 `free_irq()` 路径上有作用（`__free_irq()` 里另有一段）。

## §C2 一处风格不统一：`-ENOTSUPP` vs `-EOPNOTSUPP`

| 错误码 | 位置 | 上下文 |
|---|---|---|
| `-ENOTSUPP` | `kernel/irq/generic-chip.c:402` | `irq_setup_alt_chip()`：这个 idx 在 `gc->unused` 里 |
| `-EOPNOTSUPP` | `kernel/irq/manage.c:408` | `irq_update_affinity_desc()`：预留模式下不支持 |

⚠️`-ENOTSUPP`（值 524）是**内核内部错误码，不该出现在返回给用户态的路径上**；
`-EOPNOTSUPP`（值 95）才是 POSIX 的。
`kernel/irq/generic-chip.c:402` 那个返回值最终会到 `irq_set_type` 的调用方，
理论上可能一路传到 `request_irq()` 的返回值 ⇒ **有可能被用户态看到一个古怪的 524**。

严重程度：低（`irq_setup_alt_chip()` 的调用者通常自己吞掉这个错误），
但它是一处真实的不一致。

## §C3 两个同名函数：`irq_domain_set_info()`

| 位置 | 编译条件 |
|---|---|
| `kernel/irq/irqdomain.c:1409` | `CONFIG_IRQ_DOMAIN_HIERARCHY` |
| `kernel/irq/irqdomain.c:1923` | `!CONFIG_IRQ_DOMAIN_HIERARCHY` |

这本身是正常的条件编译模式（`irq_to_desc()` / `early_irq_init()` 也这样，§B7）。
⚠️ 但它对**工具**是个坑：代码索引会给出两个同名符号，
grep 出来的行号必须自己判断走的是哪个分支。
⇒ 99 §2.4 会把这记成索引工具在本子系统上的一个盲区。

arm64 走的是 `kernel/irq/irqdomain.c:1409` 那个（`IRQ_DOMAIN_HIERARCHY` 打开）。

## §C4 一处真实的 API 设计债：`irq_of_parse_and_map()` 用 0 表示失败

| 事实 | 依据 |
|---|---|
| virq 0 不是合法中断号 | `allocated_irqs` 位图从 1 开始分配 |
| `irq_of_parse_and_map()` 失败返回 0 | 01 §3.2 |
| ⇒ **无法区分"DT 里没配"和"控制器还没 probe"** | 后者应该是 `-EPROBE_DEFER` |

**这不是"能不能修"的问题，而是"已经有替代品了但老代码还在用"**：
`of_irq_get()` 就是为此加的，它返回负错误码（含 `-EPROBE_DEFER`）。
`irq_create_mapping()` 也有同样的问题（返回 0 表示失败，01 §10.4）。

判断你的驱动有没有踩：
```bash
grep -rn 'irq_of_parse_and_map' drivers/<你的目录>/
```
有输出就检查一下它后面是不是只判了 `if (!irq)`。

## §C5 整体评价

| 维度 | 评价 |
|---|---|
| **抽象的清晰度** | `irq_desc` / `irq_data` / `irq_domain` / `irq_chip` 四层职责非常干净；层级域是内核里少见的"一次做对"的抽象 |
| **文档质量** | ☆ `Documentation/core-api/irq/` 五篇（含 `irq-domain.rst`）质量高；⚠️ 但 `attach`-类的新机制和 `IRQCHIP_IMMUTABLE` 之类的新 flag 没跟上 |
| **命名** | ☆☆ ⚠️ **四组 flag（`IRQ_*` / `IRQD_*` / `IRQF_*` / `IRQCHIP_*`）**极易混淆（02 §4）；"中断处理函数"同时指流处理器和设备 handler（02 §1.0.E） |
| **错误码的信息量** | ☆☆☆ ⚠️ `-EINVAL` 占 58%（01 §10.1），必须配 dmesg 才能定位 |
| **可观测性** | ☆ 有 tracepoint（§A6）、`/proc/interrupts`、`/sys/kernel/irq/`、debugfs 层级视图；⚠️ 但 debugfs 默认不开（01 §0.4） |
| **对慢总线的支持** | `chip_bus_lock` / `chip_bus_sync_unlock` 这套设计让 I2C 上的中断扩展器能干净地接进来（02 §5.4） |
| **代码成熟度** | 15044 行只有 2 条 `FIXME`（§C1） |
| **历史包袱** | ☆☆ ⚠️ `irq_of_parse_and_map()` 的 0 返回值（§C4）、19 行死代码（§C1.3）、`-ENOTSUPP`（§C2）、`___PD` 空实现（§C1.2） |

**一句话总结**：这是内核里抽象设计最好的子系统之一，
它的难度不在实现的复杂度，而在**术语和 flag 的密度** ——
读它的正确顺序是先把 02 §1.0 那五组关系分清，再看时间线。

---

**下一篇**：04 篇讲四个互不调用的独立框架（generic-chip / MSI / IPI / 乱中断与轮询）+ procfs/debugfs 展示层。
