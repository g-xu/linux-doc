---
title: API 参考
---

# irq 01：代码地图与对外 API

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> ⚠️ **本套的树不是纯上游**：`include/linux/interrupt.h` 有一处 **29 行的 PREEMPT_RT
> 本地补丁**（插在 `raise_softirq()` 声明之后、`DECLARE_PER_CPU(…, ksoftirqd)` 之前）。
> ⇒ **该插入点之后的 `interrupt.h` 行号比上游 6.1.158 大 29**。
> 本套引用的一律是**当前树**的行号。`kernel/irq/` 和 `include/linux/irq*.h` 全部干净。
> 详见 99 §1.1。
>
> 本篇 API 表统一三列 **API / 说明 / 备注**；签名只写**函数名 + 形参名**，不带类型；
> 位置只到**文件**，每节开头统一交代头文件和实现文件。

---

## §0 代码地图

### §0.1 核心：`kernel/irq/`（22 个 `.c` + 3 个 `.h`，共 15044 行）

| 文件 | 职责 | 本套的证据等级 |
|---|---|---|
| `manage.c` | **消费者 API 的主战场**：`request_threaded_irq` / `free_irq` / `enable_irq` / 亲和性 / 唤醒 / 线程化 | 一等（关键路径） |
| `irqdomain.c` | **hwirq ↔ virq 映射**：域的创建、映射、层级域分配 | 一等（关键路径） |
| `chip.c` | **流处理器 + `irq_chip` 默认实现**：`handle_level_irq` / `handle_fasteoi_irq` / `irq_chip_*_parent` | 一等（关键路径） |
| `irqdesc.c` | `irq_desc` 的分配与查找（sparse / 数组两套实现） | 一等 |
| `handle.c` | `__handle_irq_event_percpu()` —— **真正调用你的 handler 的地方** | 一等（全文 242 行） |
| `msi.c` | MSI 域与 MSI 描述符 | 二等 |
| `generic-chip.c` | 给"寄存器长得很规整"的控制器省代码的模板 | 二等 |
| `proc.c` | `/proc/interrupts` 和 `/proc/irq/N/*` | 一等 |
| `debugfs.c` | `/sys/kernel/debug/irq/` | 一等 |
| `devres.c` | `devm_request_irq` 等 6 个 devres 包装 | 一等（全文 284 行） |
| `spurious.c` | 乱中断检测与 `irqfixup` / `irqpoll` | 二等 |
| `matrix.c` | 向量分配器（x86 用得多，arm64 走 ITS） | 三等（arm64 基本不涉及） |
| `timings.c` | 中断间隔预测（给 cpuidle 用） | 三等 |
| `cpuhotplug.c` | CPU 下线时把中断迁走 | 二等 |
| `migration.c` | `GENERIC_PENDING_IRQ` 的延迟迁移 | 二等 |
| `resend.c` | 软件重发（`HARDIRQS_SW_RESEND`） | 二等 |
| `pm.c` | suspend/resume 时的中断屏蔽 | 二等 |
| `ipi.c` | IPI 作为 irq_domain 的一种 | 二等 |
| `affinity.c` | `irq_calc_affinity_vectors()` 等多队列辅助 | 二等 |
| `autoprobe.c` | 老式 ISA 中断自动探测 | 三等（arm64 不涉及） |
| `irq_sim.c` | 中断模拟器（给测试用） | 三等 |
| `dummychip.c` | `no_irq_chip` / `dummy_irq_chip` | 一等（全文 64 行） |
| `internals.h` | 核心内部契约（513 行），**不对外开放** | 一等 |
| `settings.h` · `debug.h` | 状态位访问器宏 · 调试打印 | 二等 |

### §0.2 头文件（这是最高信息密度的入口）

| 头文件 | 行数 | 给谁看 |
|---|---|---|
| `include/linux/interrupt.h` | 867 | **中断消费者**（写驱动的人）。⚠️ 有本地补丁 |
| `include/linux/irq.h` | 1278 | **中断控制器驱动**（`irq_chip` 实现者） |
| `include/linux/irqdomain.h` | 641 | **域的提供者与使用者** |
| `include/linux/irqdesc.h` | 251 | `struct irq_desc` + `generic_handle_*` |
| `include/linux/irqchip.h` | —— | `IRQCHIP_DECLARE` 注册宏 |
| `include/linux/of_irq.h` | —— | 从 DT 拿中断号 |
| `include/linux/irqchip/arm-gic-v3.h` | 661 | GIC v3 的寄存器定义 |
| `include/linux/msi.h` | —— | MSI 域 |
| `kernel/irq/internals.h` | 513 | **只给 `kernel/irq/` 自己用** |

### §0.3 arm64 上的中断控制器驱动（`drivers/irqchip/`，129 个 `.c`，48150 行）

| 文件 | 行数 | 本套的处理 |
|---|---|---|
| `drivers/irqchip/irq-gic-v3.c` | 2584 | **arm64 主力**，本套的代表驱动（二等证据） |
| `drivers/irqchip/irq-gic-v3-its.c` | 5608 | MSI/LPI，**只讲契约**（三等） |
| `drivers/irqchip/irq-gic.c` | 1764 | GIC v2，作对照（三等） |
| `drivers/irqchip/irq-gic-common.c` | 150 | v2/v3 共用的配置代码（二等） |
| 其余 125 个 | —— | 不读，只出现在统计里 |

### §0.4 Kconfig（`kernel/irq/Kconfig`，**30** 个 `config` 项）

可重跑：`grep -c '^config ' kernel/irq/Kconfig` → **30**。
其中**没有一个是用户可直接选的** —— 全部由架构或驱动 `select`。
arm64 上被打开的关键项：

| 选项 | 作用 |
|---|---|
| `SPARSE_IRQ` | `irq_desc` 用 radix tree 存而不是静态数组 |
| `IRQ_DOMAIN` | 域机制总开关 |
| `IRQ_DOMAIN_HIERARCHY` | **层级域**（GIC v3 + ITS 必需） |
| `GENERIC_IRQ_CHIP` | `generic-chip.c` 模板 |
| `GENERIC_MSI_IRQ` / `GENERIC_MSI_IRQ_DOMAIN` | MSI |
| `IRQ_FORCED_THREADING` | 允许 `threadirqs` 命令行参数把所有中断线程化 |
| `GENERIC_IRQ_SHOW` | `/proc/interrupts` 的内容 |
| `GENERIC_IRQ_DEBUGFS` | `/sys/kernel/debug/irq/`（**不默认开**） |
| `GENERIC_IRQ_EFFECTIVE_AFF_MASK` | `effective_affinity` 文件 |
| `GENERIC_IRQ_MULTI_HANDLER` | arm/arm64 的 `set_handle_irq()` |
| `HARDIRQS_SW_RESEND` | 软件重发 |

⚠️ 三个在 arm64 上**不开**的：`GENERIC_IRQ_LEGACY`、`GENERIC_IRQ_PROBE`（ISA 自动探测）、
`DEPRECATED_IRQ_CPU_ONOFFLINE`。⇒ 对应的代码路径本套不覆盖（99 §2.7）。

### §0.5 导出符号总量

| 文件 | 导出数 |
|---|---|
| `kernel/irq/irqdomain.c` | **41** |
| `kernel/irq/chip.c` | 34 |
| `kernel/irq/manage.c` | 26 |
| `kernel/irq/generic-chip.c` | 14 |
| `kernel/irq/irqdesc.c` | 11 |
| `kernel/irq/devres.c` · `kernel/irq/msi.c` | 各 6 |
| `kernel/irq/autoprobe.c` · `kernel/irq/ipi.c` · `kernel/irq/irq_sim.c` | 各 3 |
| `kernel/irq/handle.c` | 2 |
| `kernel/irq/dummychip.c` · `kernel/irq/resend.c` | 各 1 |
| **合计** | **151**（21 个 `EXPORT_SYMBOL` + 130 个 `EXPORT_SYMBOL_GPL`） |

```bash
grep -rho 'EXPORT_SYMBOL[_A-Z]*' kernel/irq/*.c | sort | uniq -c
```

对比：remoteproc 核心导出 33 个。**irq 的对外面积是它的 4.5 倍** ——
因为它同时服务三类完全不同的使用者（§1 / §4 / §5）。

---

## §1 消费者 API：申请与释放中断

> 头文件：`include/linux/interrupt.h`（⚠️ 这一节涉及的声明全在**补丁插入点之前**，行号不受影响）
> 实现：`kernel/irq/manage.c`

### §1.1 申请

| API | 说明 | 备注 |
|---|---|---|
| `request_irq(irq, handler, flags, name, dev)` | 申请一条中断，注册硬中断处理函数 | 只是 `request_threaded_irq(…, NULL, …)` 的 inline 包装；`__must_check` |
| `request_threaded_irq(irq, handler, thread_fn, flags, name, dev)` | 同时注册硬中断处理函数和线程处理函数 | **真正的实现入口**。`handler` 传 NULL 则核心用 `irq_default_primary_handler`；`thread_fn` 非空会创建一个内核线程 |
| `request_any_context_irq(irq, handler, flags, name, dev)` | 让核心决定是硬中断还是线程 | 返回值是 `IRQC_IS_HARDIRQ` 或 `IRQC_IS_NESTED`，**不是 0** —— 这是唯一一个"成功也返回非 0"的申请函数 |
| `request_percpu_irq(irq, handler, devname, percpu_dev_id)` | 申请一条 per-CPU 中断 | `__request_percpu_irq(…, 0, …)` 的包装；arm64 上 GIC 的 PPI（如 arch timer）走这条 |
| `__request_percpu_irq(irq, handler, flags, devname, percpu_dev_id)` | 带 flags 的 per-CPU 版本 | —— |
| `request_nmi(irq, handler, flags, name, dev)` | 把一条中断申请成 NMI | arm64 上需要 GIC v3 + `pseudo-NMI` 支持 |
| `devm_request_irq(dev, irq, handler, irqflags, devname, dev_id)` | devres 版 | 见 §9 |

⚠️ **`request_irq()` 的 `dev` 参数不是可选的**：
共享中断（`IRQF_SHARED`）时它是区分 action 的唯一标识，
`free_irq()` 也靠它找到要摘掉的那个 action。传 NULL 且带 `IRQF_SHARED` 会被拒绝。

### §1.2 释放

| API | 说明 | 备注 |
|---|---|---|
| `free_irq(irq, dev_id)` | 摘掉一个 action | **返回值是 `const void *`**（原来的 `dev_id`），不是 int。会**等所有正在执行的 handler 和线程结束**才返回 —— 所以**不能在中断上下文里调** |
| `free_percpu_irq(irq, percpu_dev_id)` | per-CPU 版 | 返回 void |
| `free_nmi(irq, dev_id)` | NMI 版 | 返回 `const void *` |
| `free_percpu_nmi(irq, percpu_dev_id)` | —— | —— |
| `devm_free_irq(dev, irq, dev_id)` | devres 版，提前释放 | 一般不需要显式调 |

### §1.3 处理函数的返回值

| 返回值 | 含义 | 备注 |
|---|---|---|
| `IRQ_NONE` | "不是我的中断" | 共享中断时**必须**正确返回它，否则乱中断检测会误判 |
| `IRQ_HANDLED` | 处理完了 | —— |
| `IRQ_WAKE_THREAD` | "剩下的交给线程" | 只有注册了 `thread_fn` 时才能返回；核心会唤醒那个线程 |
| `no_action(cpl, dev_id)` | 内核提供的空 handler | 用于"只需要占住这条中断线"的场合 |

### §1.4 `request_irq` 的 flags 速查（最容易配错的地方）

| flag | 说明 | 备注 |
|---|---|---|
| `IRQF_SHARED` | 允许多个设备共享 | **必须所有共享者都设**，否则第二个申请者失败 |
| `IRQF_TRIGGER_*`（RISING / FALLING / HIGH / LOW） | 触发方式 | **DT 里已经写了触发方式时不要在代码里再设**，冲突会被拒绝 |
| `IRQF_ONESHOT` | 线程跑完之前保持中断屏蔽 | **注册 `thread_fn` 且 handler 为 NULL 时必须设**，否则 `request_threaded_irq()` 返回 `-EINVAL` |
| `IRQF_NO_THREAD` | 禁止被 `threadirqs` 强制线程化 | 给不能睡的路径用 |
| `IRQF_NO_SUSPEND` | suspend 期间保持使能 | 和 `enable_irq_wake()` **不是一回事**（见 §2.4） |
| `IRQF_NO_AUTOEN` | 申请后**不自动使能** | 6.x 才有；需要自己调 `enable_irq()` |
| `IRQF_PERCPU` | per-CPU 中断 | —— |
| `IRQF_NOBALANCING` | 不参与亲和性自动均衡 | —— |
| `IRQF_IRQPOLL` | 参与 `irqpoll` 轮询 | —— |
| `IRQF_TIMER` | 定时器中断 | 等价于 `IRQF_NO_SUSPEND \| IRQF_NO_THREAD` 的组合语义 |
| `IRQF_COND_SUSPEND` | 和 `IRQF_NO_SUSPEND` 共享时的条件 | —— |

---

## §2 使能、屏蔽、亲和性、唤醒

> 头文件：`include/linux/interrupt.h`（`irq_set_affinity` 等在 `#ifdef CONFIG_SMP` 分支里）
> 实现：`kernel/irq/manage.c` · `kernel/irq/chip.c`

### §2.1 使能与屏蔽

| API | 说明 | 备注 |
|---|---|---|
| `disable_irq(irq)` | 屏蔽并**等待正在执行的 handler 结束** | **会睡**，不能在中断上下文或持自旋锁时调 |
| `disable_irq_nosync(irq)` | 只屏蔽，不等 | 可以在中断上下文调 |
| `disable_hardirq(irq)` | 屏蔽并等硬中断部分结束，**不等线程** | 返回 bool：为 true 表示"有线程还在跑" |
| `enable_irq(irq)` | 解除屏蔽 | **是计数式的**：`disable` 调了 N 次就要 `enable` N 次（`irq_desc.depth`） |
| `enable_percpu_irq(irq, type)` / `disable_percpu_irq(irq)` | per-CPU 版 | 只影响当前 CPU |
| `irq_percpu_is_enabled(irq)` | 查 per-CPU 中断在本 CPU 是否使能 | —— |
| `irq_has_action(irq)` | 这条中断有没有注册过 handler | 返回 bool |
| `irq_wake_thread(irq, dev_id)` | 手工唤醒中断线程 | 给"handler 之外发现要干活"的场合 |
| `*_lockdep` / `*_lockdep_irqsave` 五个变体 | 带 lockdep 标注的版本 | ⚠️ 它们在 `CONFIG_PREEMPT_RT` 下行为不同（本树的 `interrupt.h` 有相关 `#if` 判断） |

⚠️ **`enable_irq()` 的计数语义是最高频的踩坑点**：
多调一次会打出 `Unbalanced enable for IRQ %d` 警告并直接返回，
少调一次则中断永久沉默且**没有任何日志**。

### §2.2 NMI 相关

| API | 说明 | 备注 |
|---|---|---|
| `enable_nmi(irq)` / `disable_nmi_nosync(irq)` | —— | 只有 `request_nmi()` 成功的中断才能用 |
| `enable_percpu_nmi(irq, type)` / `disable_percpu_nmi(irq)` | —— | —— |
| `prepare_percpu_nmi(irq)` / `teardown_percpu_nmi(irq)` | 在本 CPU 上准备/拆除 per-CPU NMI | 必须在**目标 CPU 上**调 |

### §2.3 亲和性（`CONFIG_SMP`）

| API | 说明 | 备注 |
|---|---|---|
| `irq_set_affinity(irq, cpumask)` | 设置亲和性 | 会被 `irq_can_set_affinity()` 挡住；最终落到 `irq_chip->irq_set_affinity` |
| `irq_force_affinity(irq, cpumask)` | 强制设置，绕过部分检查 | 给 CPU 热插拔路径用 |
| `irq_can_set_affinity(irq)` | 这条中断能不能改亲和性 | —— |
| `irq_select_affinity(irq)` | 让核心自动选 | 需要 `AUTO_IRQ_AFFINITY` |
| `irq_set_affinity_and_hint(irq, m)` / `irq_update_affinity_hint(irq, m)` | 设置/更新亲和性**提示** | **hint 只是给用户态看的**（`/proc/irq/N/affinity_hint`），**不改变实际路由** |
| `irq_set_affinity_hint(irq, m)` | 已废弃的老接口 | 仍然存在，是上面两个的组合 |
| `irq_update_affinity_desc(irq, affinity)` | 更新 `irq_desc` 里的亲和信息 | 给多队列设备预留 |
| `irq_calc_affinity_vectors(minvec, maxvec, affd)` | 算出该分配几个向量 | 多队列网卡/NVMe 用 |
| `irq_default_affinity` | 全局默认亲和掩码 | 命令行 `irqaffinity=` 可改 |
| `irq_set_affinity_locked(data, mask, force)` | 已持 desc 锁时用（在 `include/linux/irq.h` 里） | 给 irqchip 驱动用，不是消费者 API |

**`hint` 与真实亲和性的区别**是本节最该记住的一条：
`affinity_hint` 是驱动告诉用户态 irqbalance「我建议放这里」，
真正生效的是 `/proc/irq/N/smp_affinity`（07 篇会给对照）。

### §2.4 唤醒与电源管理

| API | 说明 | 备注 |
|---|---|---|
| `irq_set_irq_wake(irq, on)` | 设置这条中断能否唤醒系统 | **计数式**（`irq_desc.wake_depth`），落到 `irq_chip->irq_set_wake` |
| `enable_irq_wake(irq)` / `disable_irq_wake(irq)` | 上面那个的 inline 包装 | —— |
| `suspend_device_irqs()` / `resume_device_irqs()` | 系统级批量屏蔽/恢复 | PM 核心调，驱动不该调 |
| `rearm_wake_irq(irq)` | 重新武装唤醒中断 | —— |

⚠️ **`IRQF_NO_SUSPEND` 和 `enable_irq_wake()` 是两件不同的事**：
前者是"suspend 期间这条中断继续正常工作"，后者是"这条中断能把系统唤醒"。
两者混用会触发核心的告警（`kernel/irq/pm.c` 里有专门的检查）。

### §2.5 状态查询与注入

| API | 说明 | 备注 |
|---|---|---|
| `irq_get_irqchip_state(irq, which, state)` | 读硬件状态（pending / active / masked / line-level） | 需要 `irq_chip->irq_get_irqchip_state` |
| `irq_set_irqchip_state(irq, which, state)` | 写硬件状态 | 主要给虚拟化用 |
| `irq_inject_interrupt(irq)` | **软件注入一次中断** | 需要 `CONFIG_GENERIC_IRQ_INJECTION`；07 篇的故障注入靠它 |

---

## §3 取中断号：从 DT / platform 到 virq

> 头文件：`include/linux/of_irq.h` · `include/linux/platform_device.h`
> 实现：`drivers/of/irq.c` · `drivers/base/platform.c`

### §3.1 推荐用法（platform 驱动）

| API | 说明 | 备注 |
|---|---|---|
| `platform_get_irq(pdev, num)` | **首选**。按索引取中断号 | 失败时**已经打过一条日志**了，驱动不必再打；返回负错误码，**0 不再是合法返回值** |
| `platform_get_irq_optional(pdev, num)` | 同上但失败不打日志 | 给"这个中断可有可无"的场合 |
| `platform_get_irq_byname(pdev, name)` | 按 `interrupt-names` 取 | **多中断设备的首选**，比索引稳 |
| `platform_get_irq_byname_optional(pdev, name)` | 同上，不打日志 | —— |
| `platform_irq_count(pdev)` | 这个设备有几条中断 | 返回负值表示 `-EPROBE_DEFER` 等错误 |

### §3.2 直接操作 DT 节点

| API | 说明 | 备注 |
|---|---|---|
| `of_irq_get(dev_node, index)` | 按索引解析并映射 | **能返回 `-EPROBE_DEFER`**（中断控制器还没 probe）—— 这是它比 `irq_of_parse_and_map()` 好的关键 |
| `of_irq_get_byname(dev_node, name)` | 按名字 | 同上 |
| `irq_of_parse_and_map(node, index)` | 老接口 | ⚠️ **失败返回 0，无法区分"没配"和"控制器还没就绪"** ⇒ 新代码别用 |
| `of_irq_count(dev_node)` | 数一数有几条 | —— |
| `of_irq_parse_one(device, index, out_irq)` | 只解析不映射，产出 `of_phandle_args` | 写中断控制器驱动时才需要 |
| `of_irq_to_resource(dev_node, index, r)` / `of_irq_to_resource_table(…)` | 填进 `struct resource` | platform 设备创建时用 |

⚠️ **`irq_of_parse_and_map()` 返回 0 这件事**：
0 在 Linux 里**不是合法的 virq**（virq 0 保留），所以"返回 0 == 失败"。
但它把"DT 里没写"和"控制器还没 probe，应该 `-EPROBE_DEFER`"混成了同一个值 ——
这是老代码里一类真实的启动顺序 bug。⇒ 用 `of_irq_get()` 或 `platform_get_irq()`。

### §3.3 中断控制器侧：把 hwirq 交给核心

> 头文件：`include/linux/irqdesc.h`

| API | 说明 | 备注 |
|---|---|---|
| `generic_handle_irq(irq)` | 用 virq 分发一次中断 | 要求已在硬中断上下文 |
| `generic_handle_irq_safe(irq)` | 同上，但自己关本地中断 | 给"在进程上下文里要分发中断"的场合（如 I2C GPIO 扩展器） |
| `generic_handle_domain_irq(domain, hwirq)` | **首选**：直接用 (域, hwirq) 分发 | 省掉一次 `irq_find_mapping()` 调用 |
| `generic_handle_domain_irq_safe(domain, hwirq)` | 同上 + 关中断 | —— |
| `generic_handle_irq_desc(desc)` | 已经有 `desc` 时用 | 最底层，GIC 之类的一级控制器用 |

**级联控制器（如 GPIO 控制器）的标准写法就是这一节的 `generic_handle_domain_irq()`** ——
05 篇会给完整骨架。

---

## §4 中断控制器驱动侧 API

> 头文件：`include/linux/irq.h` · `include/linux/irqchip.h`
> 实现：`kernel/irq/chip.c` · `kernel/irq/irqdesc.c` · `kernel/irq/dummychip.c`

### §4.1 注册入口

| API | 说明 | 备注 |
|---|---|---|
| `IRQCHIP_DECLARE(name, compat, fn)` | **把一个 compatible 和初始化函数登记进 `__irqchip_of_table`** | 它不是 platform_driver —— 中断控制器在 `of_irq_init()` 里被**提前**初始化，早于设备模型 |
| `IRQCHIP_MATCH(compat, fn)` | 同上，用于手工构造匹配表 | —— |
| `IRQCHIP_ACPI_DECLARE(...)` | ACPI 版 | **本套 arm64-only 不覆盖 ACPI**（99 §2.7） |

⚠️ **中断控制器的初始化时机是本子系统最特殊的一点**：
它由 `of_irq_init()` 在**设备模型建立之前**驱动，
所以 irqchip 驱动**拿不到 `struct platform_device`**，
只能拿到 `struct device_node`。
⇒ 这也是为什么它不能用 `devm_*`、不能返回 `-EPROBE_DEFER`（05 篇会展开）。

例外：少数二级控制器（GPIO 扩展器、PCI 上的控制器）确实是 platform/i2c 驱动，
它们走正常的设备模型。

### §4.2 把 chip 和流处理器挂到一条中断上

| API | 说明 | 备注 |
|---|---|---|
| `irq_set_chip(irq, chip)` | 设置 `irq_chip` | 单独用得少 |
| `irq_set_handler(irq, handle)` | 设置流处理器 | `__irq_set_handler(irq, handle, 0, NULL)` 的包装 |
| `irq_set_chip_and_handler(irq, chip, handle)` | **最常用**：两件事一起做 | 是 `irq_set_chip_and_handler_name(…, NULL)` 的包装 |
| `irq_set_chip_and_handler_name(irq, chip, handle, name)` | 带名字版 | 名字会出现在 `/proc/interrupts` |
| `__irq_set_handler(irq, handle, is_chained, name)` | 底层 | `is_chained` 为 1 时会把这条中断标成"级联入口" |
| `irq_set_chained_handler(irq, handle)` | **级联专用** | 它会**顺带把中断使能起来**，而且这条 virq 之后**不能再被 `request_irq()`** |
| `irq_set_chained_handler_and_data(irq, handle, data)` | 同上 + 设置 handler_data | **级联控制器的推荐写法** |
| `irq_set_chip_data(irq, data)` / `irq_get_chip_data(irq)` | 给 **chip** 用的私有数据 | —— |
| `irq_set_handler_data(irq, data)` / `irq_get_handler_data(irq)` | 给**流处理器**用的私有数据 | ⚠️ 和上面那对**是两个不同的字段**，容易混 |

⚠️ **`irq_set_chained_handler*()` 的两个副作用**必须记住：
① 它把中断**立刻使能**（不需要 `request_irq()`）；
② 它设置 `IRQ_NOREQUEST`，之后任何 `request_irq()` 都会失败。
⇒ 一条中断线**要么是级联入口，要么是设备中断，不能两者兼任**。

### §4.3 十四个流处理器（flow handler）

全部声明在 `include/linux/irq.h:639-658`（`handle_bad_irq` 也在里面），
实现在 `kernel/irq/chip.c`（`handle_bad_irq` 在 `kernel/irq/handle.c`）。

**常用的六个**：

| 流处理器 | 适用硬件 | 备注 |
|---|---|---|
| `handle_level_irq(desc)` | 电平触发 | 先 mask+ack，跑 handler，再 unmask |
| `handle_edge_irq(desc)` | 边沿触发 | 会处理"handler 执行期间又来了一次"的情况（重跑循环） |
| `handle_fasteoi_irq(desc)` | **GIC v2/v3 用的就是这个** | 硬件自带优先级和 EOI，不需要 mask/unmask |
| `handle_simple_irq(desc)` | 无硬件确认动作 | 给纯软件中断、`dummy_irq_chip` 用 |
| `handle_percpu_irq(desc)` | per-CPU，**不共享、不统计** | 无锁 |
| `handle_percpu_devid_irq(desc)` | per-CPU + per-CPU dev_id | **arch timer 走这条** |

**兜底与嵌套两个**：

| 流处理器 | 备注 |
|---|---|
| `handle_bad_irq(desc)` | `irq_desc` 初始化时的默认值；统计一次乱中断 |
| `handle_nested_irq(irq)` | 嵌套线程中断（如 I2C 扩展器）；在父线程上下文里直接跑子中断的 handler |

**特殊场景的六个（容易被漏掉）**：

| 流处理器 | 什么时候用 | 备注 |
|---|---|---|
| `handle_untracked_irq(desc)` | 频率极高、不想让它进乱中断统计和 `kstat` | 跳过 `note_interrupt()` 和计数 |
| `handle_edge_eoi_irq(desc)` | 边沿触发**但硬件还要 EOI** | 需要 `CONFIG_IRQ_EDGE_EOI_HANDLER` |
| `handle_fasteoi_nmi(desc)` | **`request_nmi()` 的中断**（arm64 pseudo-NMI） | 只调 `->eoi()`，NMI-safe |
| `handle_percpu_devid_fasteoi_nmi(desc)` | per-CPU 的 NMI | —— |
| `handle_fasteoi_ack_irq(desc)` | fasteoi + 需要先 ack | 硬件要求 ack 在 handler 之前 |
| `handle_fasteoi_mask_irq(desc)` | fasteoi + 需要先 mask | 硬件要求 mask 在 handler 之前 |

**选错流处理器是级联控制器最常见的 bug**：
电平触发的硬件用 `handle_edge_irq` 会导致中断风暴，
边沿触发用 `handle_level_irq` 会丢中断。

**导出情况**：十四个里有 10 个 `EXPORT_SYMBOL(_GPL)`
（`handle_edge_irq` 是唯一非 GPL 的，`kernel/irq/chip.c:827`）；
`handle_percpu_irq` / `handle_percpu_devid_irq` / `handle_percpu_devid_fasteoi_nmi` /
`handle_edge_eoi_irq` **不导出** ⇒ 模块里用不了它们。

### §4.4 层级域里的"转发给父层"辅助（21 个）

| API | 转发给父层的 | 备注 |
|---|---|---|
| `irq_chip_enable_parent(data)` / `irq_chip_disable_parent(data)` | enable / disable | —— |
| `irq_chip_ack_parent(data)` | ack | —— |
| `irq_chip_mask_parent(data)` / `irq_chip_unmask_parent(data)` / `irq_chip_mask_ack_parent(data)` | mask / unmask / mask_ack | **GIC 之上的 MSI 域大量用它们** |
| `irq_chip_eoi_parent(data)` | eoi | —— |
| `irq_chip_set_affinity_parent(data, dest, force)` | set_affinity | ITS 域把亲和性透传给 GIC |
| `irq_chip_set_type_parent(data, type)` | set_type | —— |
| `irq_chip_set_wake_parent(data, on)` | set_wake | ⚠️ 父层没实现时返回 `-ENOSYS`（§10.2） |
| `irq_chip_set_vcpu_affinity_parent(data, vcpu_info)` | set_vcpu_affinity | 虚拟化 |
| `irq_chip_get_parent_state(data, which, state)` / `irq_chip_set_parent_state(data, which, val)` | get/set_irqchip_state | —— |
| `irq_chip_request_resources_parent(data)` / `irq_chip_release_resources_parent(data)` | request/release_resources | —— |
| `irq_chip_retrigger_hierarchy(data)` | 沿层级往上找**第一个**实现了 `irq_retrigger` 的 | —— |

**这一整族的存在就是层级域的核心机制**：
一个 virq 在层级域里有**多个 `irq_data`**（每层一个，用 `parent_data` 串起来），
上层的 `irq_chip` 回调里调这些 `_parent` 函数把动作传下去。
02 §1.0 会把这组关系和"级联"的区别摊开讲。

### §4.5 `irq_desc` 的分配（不用域时）

| API | 说明 | 备注 |
|---|---|---|
| `irq_alloc_descs(irq, from, cnt, node)` | 分配 cnt 个连续的 `irq_desc` | 宏，展开成 `__irq_alloc_descs(…, THIS_MODULE, NULL)` |
| `irq_alloc_desc(node)` / `irq_alloc_desc_at(at, node)` / `irq_alloc_desc_from(from, node)` / `irq_alloc_descs_from(from, cnt, node)` | 四个便捷宏 | —— |
| `irq_free_descs(irq, cnt)` | 释放 | —— |
| `devm_irq_alloc_descs(dev, irq, from, cnt, node)` + 三个变体 | devres 版 | 见 §9 |
| `irq_to_desc(irq)` | virq → `irq_desc` | ⚠️ **不导出给模块**（只在 `kernel/irq/` 和少数架构代码里用） |

用了 `irq_domain` 就**不需要**手工 `irq_alloc_descs()` ——
域的 `irq_create_mapping()` / `irq_domain_alloc_irqs()` 内部会做（§5.3）。

### §4.6 两个哑 chip

| 符号 | 用途 | 备注 |
|---|---|---|
| `no_irq_chip` | 所有回调都是空的 | `irq_desc` 初始化时的默认 chip |
| `dummy_irq_chip` | 回调都是 `noop`，但**有名字** | 给纯软件中断用（如 `irq_sim`） |

两者都定义在 `kernel/irq/dummychip.c`（全文 64 行，本套一等证据）。

---

## §5 `irq_domain`：hwirq ↔ virq 映射

> 头文件：`include/linux/irqdomain.h`
> 实现：`kernel/irq/irqdomain.c`（导出 41 个符号，全子系统最多）

### §5.1 创建域（线性 / 树 / legacy / nomap）

| API | 反向映射的实现 | 什么时候用 |
|---|---|---|
| `irq_domain_add_linear(of_node, size, ops, host_data)` | **数组** | **首选**：hwirq 从 0 开始密集编号且上界已知 |
| `irq_domain_create_linear(fwnode, size, ops, host_data)` | 同上 | 用 `fwnode` 而不是 `device_node` |
| `irq_domain_add_tree(of_node, ops, host_data)` | radix tree | hwirq 稀疏（如 MSI 的 device-ID 编码） |
| `irq_domain_create_tree(fwnode, ops, host_data)` | 同上 | —— |
| `irq_domain_add_simple(of_node, size, first_irq, ops, host_data)` | 线性 + 可选的 legacy 预映射 | `first_irq` 非 0 时会**立刻**建好映射 |
| `irq_domain_create_simple(fwnode, size, first_irq, ops, host_data)` | 同上 | —— |
| `irq_domain_add_legacy(of_node, size, first_irq, first_hwirq, ops, host_data)` | 固定偏移 | 只给"virq 号必须等于某个固定值"的老代码 |
| `irq_domain_create_legacy(fwnode, …)` | 同上 | —— |
| `irq_domain_add_nomap(of_node, max_irq, ops, host_data)` | **不做反向映射**（hwirq 存在 `irq_data` 里） | 需要 `CONFIG_IRQ_DOMAIN_NOMAP` |
| `__irq_domain_add(fwnode, size, hwirq_max, direct_max, ops, host_data)` | **上面全部的共同实现** | 四个参数的组合决定用哪种反向映射 |
| `irq_domain_remove(host)` | 销毁 | —— |

**判断某个域用的是哪种反向映射，看它传给 `__irq_domain_add()` 的
`size` / `hwirq_max` / `direct_max` 三个参数的组合** —— 02 §5 会给对照表。

### §5.2 `struct irq_domain_ops`：五个基础 + 五个层级

| 回调 | 干什么 | 必需？ |
|---|---|---|
| `xlate(d, node, intspec, intsize, out_hwirq, out_type)` | **把 DT 的 `interrupts` 单元格翻译成 (hwirq, 触发类型)** | 非层级域必需 |
| `map(d, virq, hw)` | 一条映射建立时调：**设置 chip 和流处理器** | 非层级域必需 |
| `unmap(d, virq)` | 映射拆除时调 | 可选 |
| `match(d, node, bus_token)` | 判断某个 DT 节点是不是本域 | 通常不填（用默认的 fwnode 比较） |
| `select(d, fwspec, bus_token)` | 更细的选择逻辑 | 多域共用一个节点时才需要 |
| `translate(d, fwspec, out_hwirq, out_type)` | **层级域版的 `xlate`** | 层级域必需 |
| `alloc(d, virq, nr_irqs, arg)` | 分配 nr_irqs 条中断，**并往下层递归** | 层级域必需 |
| `free(d, virq, nr_irqs)` | 释放 | 层级域必需 |
| `activate(d, irqd, reserve)` | 真正启用（MSI 里是"写 MSI 消息"） | MSI 域需要 |
| `deactivate(d, irq_data)` | 反操作 | 同上 |
| `debug_show(m, d, irqd, ind)` | debugfs 里额外打点东西 | 需要 `GENERIC_IRQ_DEBUGFS` |

⚠️ **`xlate` 和 `translate` 只能二选一**：
非层级域填 `xlate` + `map`；层级域填 `translate` + `alloc` + `free`。
混填会导致"域建起来了但映射建不出来"。

核心提供了两个现成的 `xlate`：
`irq_domain_translate_onecell()` 和 `irq_domain_translate_twocell()`
（还有对应的 `xlate` 版本），覆盖了绝大多数简单控制器。

### §5.3 建立与查找映射（非层级）

| API | 说明 | 备注 |
|---|---|---|
| `irq_create_mapping(host, hwirq)` | 建一条映射，返回 virq | 是 `irq_create_mapping_affinity(host, hwirq, NULL)` 的包装 |
| `irq_create_mapping_affinity(host, hwirq, affinity)` | 带亲和性 | —— |
| `irq_find_mapping(domain, hwirq)` | **查**已有映射 | 找不到返回 **0**；`irq_linear_revmap()` 是它的旧名（已合并） |
| `irq_create_fwspec_mapping(fwspec)` | 从 `irq_fwspec` 建映射 | `of_irq_get()` 内部走这条 |
| `irq_create_of_mapping(irq_data)` | 从 `of_phandle_args` 建映射 | `irq_of_parse_and_map()` 内部走这条 |
| `irq_dispose_mapping(virq)` | 拆一条映射 | —— |
| `irq_find_matching_fwnode(fwnode, bus_token)` / `irq_find_matching_fwspec(fwspec, bus_token)` | 按 fwnode/fwspec 找域 | —— |
| `irq_find_matching_host(node, bus_token)` / `irq_find_host(node)` | 按 DT 节点找域 | inline 包装 |
| `irq_get_default_host()` / `irq_set_default_host(domain)` | 默认域 | 只在"没指定域"的老代码里用 |
| `irq_domain_update_bus_token(domain, bus_token)` | 改域的 bus token | MSI 域区分 PCI/platform 时用 |

### §5.4 层级域（`CONFIG_IRQ_DOMAIN_HIERARCHY`）

| API | 说明 | 备注 |
|---|---|---|
| `irq_domain_create_hierarchy(parent, flags, size, fwnode, ops, host_data)` | 建一个挂在 `parent` 之上的域 | —— |
| `irq_domain_add_hierarchy(parent, flags, size, node, ops, host_data)` | 同上，`device_node` 版 | inline 包装 |
| `irq_domain_alloc_irqs(domain, nr_irqs, node, arg)` | 分配 nr_irqs 条，**沿层级向下递归** | 是 `__irq_domain_alloc_irqs(domain, -1, …, false, NULL)` 的包装 |
| `__irq_domain_alloc_irqs(domain, irq_base, nr_irqs, node, arg, realloc, affinity)` | 全参数版 | —— |
| `irq_domain_free_irqs(virq, nr_irqs)` | 释放 | —— |
| `irq_domain_alloc_irqs_parent(domain, irq_base, nr_irqs, arg)` | **在自己的 `alloc` 回调里调它，把请求转给父域** | —— |
| `irq_domain_free_irqs_parent(domain, irq_base, nr_irqs)` | 反操作 | —— |
| `irq_domain_alloc_irqs_hierarchy(domain, irq_base, nr_irqs, arg)` | 直接调本域的 `ops->alloc` | 核心内部用得多 |
| `irq_domain_set_hwirq_and_chip(domain, virq, hwirq, chip, chip_data)` | 在 `alloc` 回调里给**本层**的 `irq_data` 填 hwirq 和 chip | —— |
| `irq_domain_set_info(domain, virq, hwirq, chip, chip_data, handler, handler_data, handler_name)` | 上面那个 + 流处理器 | 最上层的域用这个 |
| `irq_domain_reset_irq_data(irq_data)` | 清空一层的 `irq_data` | —— |
| `irq_domain_free_irqs_common(domain, virq, nr_irqs)` / `irq_domain_free_irqs_top(domain, virq, nr_irqs)` | 两个现成的 `free` 实现 | `_top` 版会连 `irq_desc` 一起释放 |
| `irq_domain_get_irq_data(domain, virq)` | **在层级里取"属于某一层"的 `irq_data`** | 这是层级域调试的关键工具 |
| `irq_domain_push_irq(domain, virq, arg)` / `irq_domain_pop_irq(domain, virq)` | 运行期往层级中间插/拔一层 | 很少用（Intel VT-d 之类） |
| `irq_domain_disconnect_hierarchy(domain, virq)` | 断开某一层 | —— |

⚠️ **`irq_find_mapping()` 在层级域上通常没用**：
层级域的 virq 是 `irq_domain_alloc_irqs()` 分配的，
反向映射表可能压根没建。⇒ 层级域里要拿 `irq_data` 用 `irq_domain_get_irq_data()`。

---

## §6 `generic-chip`：寄存器规整的控制器省代码模板

> 头文件：`include/linux/irq.h`（`struct irq_chip_generic` / `irq_chip_type`）
> 实现：`kernel/irq/generic-chip.c`（导出 14 个符号）

| API | 说明 | 备注 |
|---|---|---|
| `irq_alloc_generic_chip(name, nr_ct, irq_base, reg_base, handler)` | 分配一个 generic chip | —— |
| `irq_setup_generic_chip(gc, msk, flags, clr, set)` | 把它挂到一段 virq 上 | —— |
| `irq_remove_generic_chip(gc, msk, clr, set)` | 摘掉 | —— |
| `irq_alloc_domain_generic_chips(d, irqs_per_chip, num_ct, name, handler, clr, set, flags)` | **和 irq_domain 配合的版本**（宏） | 展开成 `__irq_alloc_domain_generic_chips(…)` |
| `irq_get_domain_generic_chip(d, hw_irq)` | 从域里取某个 hwirq 对应的 chip | —— |
| `irq_setup_alt_chip(d, type)` | 按触发类型切换到备用 chip | 一个控制器上电平和边沿要用不同寄存器时 |
| `devm_irq_alloc_generic_chip(dev, name, num_ct, irq_base, reg_base, handler)` | devres 版 | 见 §9 |
| `devm_irq_setup_generic_chip(dev, gc, msk, flags, clr, set)` | devres 版 | 见 §9 |

现成的回调（直接填进 `irq_chip_type.chip`）：

| 回调 | 动作 |
|---|---|
| `irq_gc_mask_disable_reg(d)` | 往"禁用寄存器"写 bit |
| `irq_gc_mask_set_bit(d)` / `irq_gc_mask_clr_bit(d)` | 往 mask 寄存器置位 / 清位 |
| `irq_gc_ack_set_bit(d)` | 往 ack 寄存器置位 |
| `irq_gc_eoi(d)` | 写 EOI 寄存器 |
| `irq_gc_set_wake(d, on)` | 维护 wake 掩码 |
| `irq_gc_noop(d)` | 什么都不做 |

判断能不能用 generic-chip：**你的控制器是不是"一个 32 位寄存器一位一条中断"**。
是 ⇒ 能省一两百行；不是（如 GIC）⇒ 老老实实写 `irq_chip`。

---

## §7 MSI 域

> 头文件：`include/linux/msi.h` · 实现：`kernel/irq/msi.c`（导出 6 个符号）
> 本套对 MSI 只讲**契约**（二等证据）；arm64 上的实现主体是
> `drivers/irqchip/irq-gic-v3-its.c`（5608 行，三等证据）。

| API | 说明 | 备注 |
|---|---|---|
| `msi_create_irq_domain(fwnode, info, parent)` | 建一个 MSI 域（挂在 `parent` 之上） | MSI 域**一定是层级域** |
| `msi_domain_alloc_irqs(domain, dev, nvec)` | 给设备分配 nvec 条 MSI | —— |
| `msi_domain_alloc_irqs_descs_locked(domain, dev, nvec)` | 已持 msi 描述符锁时用 | —— |
| `msi_domain_free_irqs(domain, dev)` / `msi_domain_free_irqs_descs_locked(domain, dev)` | 释放 | —— |
| `msi_get_domain_info(domain)` | 取 `struct msi_domain_info` | —— |
| `platform_msi_create_irq_domain(fwnode, info, parent)` | platform 设备的 MSI 域 | arm64 上的非 PCI MSI（如 mbi-gen）走这条 |
| `platform_msi_domain_alloc_irqs(dev, nvec, write_msi_msg, data)` / `platform_msi_domain_free_irqs(dev)` | —— | —— |
| `platform_msi_create_device_domain(dev, nvec, write, ops, data)` | 设备自己当一层域（宏） | GIC ITS 之上的"设备内多中断"场景 |

---

## §8 devres 包装（6 个）

> 实现：`kernel/irq/devres.c`（全文 284 行，一等证据）

| API | 说明 | 备注 |
|---|---|---|
| `devm_request_threaded_irq(dev, irq, handler, thread_fn, irqflags, devname, dev_id)` | 主入口 | `devname` 传 NULL 时核心用 `dev_name(dev)` |
| `devm_request_irq(dev, irq, handler, irqflags, devname, dev_id)` | inline 包装 | —— |
| `devm_request_any_context_irq(dev, irq, handler, irqflags, devname, dev_id)` | 同 §1.1 的注意点：成功也返回非 0 | —— |
| `devm_free_irq(dev, irq, dev_id)` | 提前释放 | 一般不需要 |
| `devm_irq_alloc_descs(dev, irq, from, cnt, node)` + 三个变体 | 分配 `irq_desc`（宏） | 展开成 `__devm_irq_alloc_descs()` |
| `devm_irq_alloc_generic_chip(dev, name, num_ct, irq_base, reg_base, handler)` | —— | —— |
| `devm_irq_setup_generic_chip(dev, gc, msk, flags, clr, set)` | —— | —— |

⚠️ **`irq_domain` 没有 devres 版本**。
`irq_domain_add_*()` 全部要手工 `irq_domain_remove()`。
⇒ 这也和 §4.1 那条"irqchip 在设备模型之前初始化"对得上：
一级中断控制器**根本没有 `struct device` 可以挂 devres**。

---

## §9 其它

| API | 说明 | 备注 |
|---|---|---|
| `can_request_irq(irq, irqflags)` | 这条中断能不能被 `request_irq()` | 级联入口会返回 0（§4.2） |
| `note_interrupt(desc, action_ret)` | 乱中断记账 | 核心内部调，`kernel/irq/spurious.c` |
| `noirqdebug_setup(str)` | `noirqdebug` 命令行参数 | —— |
| `irq_set_status_flags(irq, set)` / `irq_clear_status_flags(irq, clr)` | 改 `irq_desc` 的状态位 | 在 `include/linux/irq.h` 里，给 irqchip 驱动用 |
| `irq_modify_status(irq, clr, set)` | 一次改两组 | —— |
| `irq_set_vcpu_affinity(irq, vcpu_info)` | 虚拟化直通 | —— |
| `irq_set_parent(irq, parent_irq)` | 记下父 virq | 只影响 `/proc/interrupts` 的展示和部分统计 |
| `handle_domain_irq` 家族 | 见 §3.3 | —— |

---

## §10 错误码语义

### §10.1 全量普查

`kernel/irq/*.c` 里 `-E*` 一共出现 **208** 次：

| 错误码 | 次数 | 主要语义 |
|---|---|---|
| `-EINVAL` | **121**（58%） | 参数不合法、flags 冲突、virq 不存在、状态不对 —— **信息量最低，必须配合 dmesg** |
| `-ENOMEM` | 33 | 分配 `irq_desc` / `irqaction` / cpumask 失败 |
| `-EBUSY` | 14 | **共享冲突**：这条中断已被别人占了且 flags 不兼容 |
| `-ENOSYS` | 9 | **"这一层没实现这个回调"** —— 层级域转发时最常见（§10.2） |
| `-ENOSPC` | 7 | 向量/位图分配不出来（`kernel/irq/matrix.c` 5 处 + `kernel/irq/msi.c` 2 处） |
| `-ENOTCONN` | 6 | 层级域里某一层没连上 |
| `-ENODEV` | 4 | 域或设备不存在 |
| `-EPERM` | 3 | `/proc/irq/N/smp_affinity` 写入被拒（不可改亲和性的中断） |
| `-EEXIST` | 2 | 映射已存在（`kernel/irq/irqdesc.c:816`、`kernel/irq/msi.c:539`） |
| `-ERANGE` · `-EOPNOTSUPP` · `-ENXIO` · `-ENOTSUPP` · `-ENOENT` · `-EIO` · `-EIDRM` · `-EFAULT` · `-EBADSLT` | 各 1 | 见 §10.3 |

```bash
grep -rho '\-E[A-Z_]*' kernel/irq/*.c | sort | uniq -c | sort -rn
```

按文件分布（前六）：`manage.c` 60 · `irqdomain.c` 39 · `ipi.c` 20 · `irqdesc.c` 20 · `chip.c` 10 · `generic-chip.c` 9。

⚠️ **`kernel/irq/` 里 `-EPROBE_DEFER` 出现 0 次**。
它来自 `drivers/of/irq.c` 的 `of_irq_get()`（§3.2）——
也就是说**"中断控制器还没就绪"这个概念不属于 irq 核心，属于 DT 层**。

### §10.2 `-ENOSYS` 的九处：层级域的"没实现"信号

| 位置 | 语义 |
|---|---|
| `kernel/irq/chip.c:1436` · `:1454` · `:1486` · `:1506` · `:1558` | 五个 `irq_chip_*_parent()` 沿层级往上找不到实现 |
| `kernel/irq/irqdomain.c:1480` · `:1786` | 域层面的回调缺失 |
| `kernel/irq/manage.c:656` · `:1507` | `irq_chip->irq_set_affinity` / `irq_set_wake` 未实现 |

**拿到 `-ENOSYS` 的第一反应应该是"哪一层的 `irq_chip` 少填了回调"**，
而不是"内核不支持这个功能"。用 `/sys/kernel/debug/irq/irqs/N` 看层级（07 篇）。

### §10.3 九个"只有一处"的错误码

| 错误码 | 位置 | 语义 |
|---|---|---|
| `-ENXIO` | `kernel/irq/manage.c:849` | `irq_set_irq_wake()` 里：这条 virq 没有 `irq_desc` |
| 其余八个（`-ERANGE` / `-EOPNOTSUPP` / `-ENOTSUPP` / `-ENOENT` / `-EIO` / `-EIDRM` / `-EFAULT` / `-EBADSLT`） | 分散在 `kernel/irq/{proc,timings,ipi,irq_sim,matrix}.c` | 07 §6 会逐个点名 |

注意 `-ENOTSUPP`（非标准，内部用）和 `-EOPNOTSUPP`（POSIX）**在同一个子系统里同时出现** ——
这是一处小的风格不统一，03 Part C 会记进设计债。

### §10.4 高频错误的第一嫌疑

| 拿到 | 第一嫌疑 |
|---|---|
| `request_irq()` → `-EBUSY` | 共享冲突：别人先占了且没设 `IRQF_SHARED`，或触发类型冲突 |
| `request_irq()` → `-EINVAL` | ① 传了 `thread_fn` 但 handler 为 NULL 且没设 `IRQF_ONESHOT`；② virq 是级联入口（`IRQ_NOREQUEST`）；③ virq 不存在 |
| `request_irq()` → `-ENOSYS` | 这条中断的 chip 没有 `irq_startup`/`irq_enable` 也没有 `irq_unmask` |
| `platform_get_irq()` → `-EPROBE_DEFER` | 中断控制器还没 probe（正常，返回它让核心重试） |
| `platform_get_irq()` → `-ENXIO` / 负值 | DT 里没有那一条 `interrupts` |
| `irq_of_parse_and_map()` → **0** | ⚠️ 可能是"没配"也可能是"控制器没就绪"——换 `of_irq_get()` |
| `irq_create_mapping()` → **0** | 域的 `ops->map` 返回了错误，或 `irq_alloc_descs` 失败 |
| `irq_set_affinity()` → `-EINVAL` | 目标 cpumask 和 `irq_desc` 允许的集合不交叠 |
| `irq_set_affinity()` → `-ENOSYS` | 这条中断的 chip（或层级里某一层）没实现 `irq_set_affinity` |
| 写 `/proc/irq/N/smp_affinity` → `-EPERM` | `IRQF_NOBALANCING` 或 chip 不支持 |

---

**下一篇**：02 篇讲结构体、它们之间怎么串、以及 hwirq/virq/irq_data/irq_desc 四层关系。
