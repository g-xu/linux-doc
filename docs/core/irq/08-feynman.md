---
title: 费曼学习法
---

# irq 08：用大白话讲清最难理解的几组关系

> 版本：Linux 6.1.158 · 架构基准：arm64 · 中断控制器基准：**GIC v3**
>
> 本篇的写法：每个主题四段 —— **问题** / **先举个例子** / **回到代码** /
> **这个类比什么时候会误导你？**
> 最后那一段是重点：类比只是脚手架，知道它在哪里塌才是真正的理解。
>
> ⚠️ 所有 `文件:行号` 都写完整路径。

---

## §1 为什么要有 hwirq 和 virq 两套编号？

### 先举个例子

想象一栋大楼的**内部电话系统**。

每个部门有自己的一台小交换机（PBX），部门内部的分机号是 **1~32**。
财务部有 12 号，人事部也有 12 号 —— **部门内唯一，全楼不唯一**。

总台为了能统一转接，给全楼每一台在用的分机发了一个**全楼唯一的四位短号**：
财务部 12 号 → 全楼 3041；人事部 12 号 → 全楼 3042。

| 概念 | 大楼里 | 内核里 |
|---|---|---|
| 部门内分机号 | 财务部 12 号 | **hwirq** |
| 全楼短号 | 3041 | **virq** |
| 部门 | 财务部 | **irq_domain** |
| 总台的号码本 | 短号 ↔ (部门, 分机) 对照表 | 域的反向映射表 |

关键：**用户（设备驱动）只需要知道全楼短号**。它不用关心这个中断来自哪个控制器。

### 回到代码

| 概念 | 类型 | 位置 |
|---|---|---|
| `virq` | `unsigned int` | `include/linux/irq.h:181` |
| `hwirq` | `irq_hw_number_t`（= `unsigned long`） | `include/linux/irq.h:182`（typedef 在 `include/linux/types.h:165`） |
| 域 | `struct irq_domain` | `include/linux/irqdomain.h:164` |
| 反向映射表（数组部分） | `revmap[]` | `include/linux/irqdomain.h:186` |
| 反向映射表（radix tree 部分） | `revmap_tree` | `include/linux/irqdomain.h:184` |
| 全楼唯一性的保证 | `allocated_irqs` 全局位图 | `kernel/irq/irqdesc.c:134` |
| 查表：(域, hwirq) → `irq_desc` | `__irq_resolve_mapping()` | `kernel/irq/irqdomain.c:949` |

**两者位宽不同这件事本身就是证据**：
`virq` 是 32 位（够用几万个），`hwirq` 是 64 位 ——
因为 MSI 会把"设备 ID + 事件号"编码进一个 hwirq（04 §2.1）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"一个短号对应一个分机"在层级域里是错的**。
   MSI 中断的一个 virq 有**两个 hwirq**：ITS 层一个、GIC 层一个
   （`include/linux/irq.h:187` 的 `parent_data` 把它们串起来）。
   ⇒ 类比里没有"一个短号同时是财务部 12 号和总部 8192 号"这回事，但代码里有（§2）。

2. ⚠️ **"总台号码本一直在"是错的**。
   `virq` 是**用到才分配**的（`CONFIG_SPARSE_IRQ`，03 §A4）——
   一台 arm64 机器潜在有几万条 LPI，实际建映射的只有几十条。
   ⇒ 类比里"全楼所有分机都提前登记好"，代码里是"打电话时才登记"。

3. ⚠️ **virq 0 不是"0 号短号"，而是"无效"**。
   `irq_find_mapping()` 找不到返回 0，`irq_create_mapping()` 失败也返回 0（01 §3.2）。
   ⇒ 类比里 0 可以是一个合法号码，代码里它被征用成了错误值 ——
   这正是 03 §C4 那处设计债的来源。

---

## §2 级联和层级差在哪？

> **这是本子系统最容易被类比错的一组关系**，也是本篇最重要的一节。

### 先举个例子

还是那栋大楼。有两种"多级"结构，长得很像但完全不同。

**结构 A：分机再接一台小交换机（= 级联）**

```
   总台 ──── 3041 ──── 财务部 PBX ──┬── 12 号（会计）
                                   ├── 13 号（出纳）
                                   └── 14 号（审计）
```

外面的人打 3041 会进到**财务部的总机**，然后**财务部的接线员**（人）
问一句"找谁"，再转给 12/13/14。

- 号码有几个？**3041 是一个号，12/13/14 各自还要有自己的全楼短号** ⇒ 4 个号。
- 谁在做转接？**财务部的接线员** —— 这是一个额外的人力步骤。

**结构 B：同一个号码，两级设备都要设置（= 层级）**

```
   一个号码 3041
     │
     ├── 在总台的设备上：占一条中继线，要设"这条线路由到哪个话务员"
     └── 在财务部的设备上：占一个端口，要设"来电显示怎么显示"
```

这里**只有一个号码 3041**。但"让这个号码正常工作"需要
**总台设备和财务部设备各自做一次配置**。
没有接线员，来电直接到人。

| | 结构 A（级联） | 结构 B（层级） |
|---|---|---|
| 号码个数 | 1 + N | **1** |
| 有"接线员"这一步吗 | **有** | **没有** |
| 两级设备都要配置吗 | 不 —— 各自独立 | **是** |

### 回到代码

| | 级联（chained） | 层级（hierarchy） |
|---|---|---|
| virq 个数 | 父 1 + 子 N | **1** |
| `irq_desc` 个数 | 每个 virq 一个 | 一个 |
| `irq_data` 个数 | 每个 `irq_desc` 一个 | **每层一个**（`include/linux/irq.h:187`） |
| "接线员" | **级联流处理器** —— 你自己写的那个函数 | 无 |
| 建立方式 | `irq_set_chained_handler_and_data()` | `irq_domain_create_hierarchy()` |
| 上层操作硬件的方式 | 直接操作自己的寄存器 | 调 `irq_chip_*_parent()` 往下传（01 §4.4） |
| arm64 例子 | GPIO 控制器挂在 GIC 的一条 SPI 上 | **GIC ITS（MSI）挂在 GIC v3 之上** |

**"接线员"这一步的代码证据**（05 §3.2 的骨架）：
```c
static void my_sub_handler(struct irq_desc *desc)      /* ← 这就是接线员 */
{
	chained_irq_enter(parent_chip, desc);
	for_each_set_bit(hwirq, &pending, NR_MY_IRQS)
		generic_handle_domain_irq(ic->domain, hwirq);   /* ← 转接 */
	chained_irq_exit(parent_chip, desc);
}
```
| 元素 | 位置 |
|---|---|
| 真实例子 | `drivers/irqchip/irq-al-fic.c:107` |
| `chained_irq_enter()` | `drivers/irqchip/irq-al-fic.c:116`（定义在 `include/linux/irqchip/chained_irq.h:16`） |
| 转接那一句 | `drivers/irqchip/irq-al-fic.c:122` |
| `chained_irq_exit()` | `drivers/irqchip/irq-al-fic.c:124`（定义在 `include/linux/irqchip/chained_irq.h:32`） |

**"父号码不能再被普通人用"的代码证据**：
`__irq_do_set_handler()` 在 `is_chained` 为真时置了三个位并直接接管 action
（`kernel/irq/chip.c:1036-1039`），然后立刻使能中断
（`kernel/irq/chip.c:1041`）。
而 `request_irq()` 的第一道门 `can_request_irq()`（`kernel/irq/manage.c:928`）
检查的正是 `_IRQ_NOREQUEST`（`kernel/irq/settings.h:100-102`）。

**层级"两级都要配置"的代码证据**：
`__irq_domain_activate_irq()`（`kernel/irq/irqdomain.c:1821`）**先递归父层，再做本层**；
`irq_chip_mask_parent()` 等 21 个 `_parent` 辅助（01 §4.4）就是"往下传"的实现。

### 这个类比什么时候会误导你？

⚠️ **四处**：

1. **"接线员会问一句找谁"暗示级联有额外延迟 —— 这是对的，但代价方式不同**。
   级联的代价是**多一次寄存器读**（读子控制器的 pending 寄存器）+ 一次域查表。
   层级的代价是**多一层 `irq_chip` 间接调用**（但只在配置时，不在每次中断时）。
   ⇒ 中断**热路径**上层级几乎零开销，级联有固定开销。

2. ⚠️ **"财务部接线员是一个人"暗示他可以睡 —— 级联流处理器不能睡**。
   它跑在硬中断上下文（03 §A6）。
   ⇒ 子控制器在 I2C 上时**不能用级联**，必须改成线程化方案（05 §4）。

3. ⚠️ **"两级设备各自配置"暗示可以只配一级 —— 不行**。
   层级域的 `.alloc` 必须调 `irq_domain_alloc_irqs_parent()` 往上申请
   （05 §5.2），而且**必须自己构造父域的 `fwspec`**（05 §5.3）——
   父域的参数格式和你这一层不一样。

4. ⚠️ **最容易错的判据**：很多人以为"两级中断控制器就是层级域"。
   **不是。** 判据是：
   > **这一层要不要有自己的 `irq_chip` 回调，作用在同一个 virq 上？**
   要 ⇒ 层级；不要（只是分发）⇒ 级联。
   ⇒ **绝大多数 GPIO 控制器是级联，不是层级。**

---

## §3 流处理器和设备 handler 为什么是两个东西？

### 先举个例子

一个快递驿站收件。

**驿站员工**（= 流处理器）的工作是固定流程：
① 在门口的登记本上签收 → ② **暂时关上门**（免得快递员一直往里塞）→
③ 把包裹交给**收件人** → ④ 在系统里点"已签收" → ⑤ **重新开门**。

**收件人**（= 设备 handler）的工作是：拆开看看是不是自己的东西，
是就拿走，不是就说"不是我的"。

关键点：
- 驿站流程是**驿站定的**（不同驿站流程不同：有的要签字、有的扫码即可）；
- 拆包裹是**收件人的事**，驿站不管里面是什么；
- 一个包裹可能有**几个人共用一个地址**（合租），驿站要挨个问过去。

### 回到代码

| | 驿站员工（流处理器） | 收件人（设备 handler） |
|---|---|---|
| 字段 | `irq_desc.handle_irq` | `irqaction.handler` |
| 位置 | `include/linux/irqdesc.h:59` | `include/linux/interrupt.h:118` 起的结构里 |
| 谁写 | **中断控制器驱动** | **设备驱动** |
| 怎么装上 | `irq_set_chip_and_handler()` | `request_irq()` |
| 有几个 | **一个** | **一条链**（`irqaction.next`） |
| 内核提供现成的吗 | **提供 14 个**（01 §4.3） | 只有 `no_action()` |

**"驿站流程各家不同"的证据** —— 14 个流处理器对应不同硬件（01 §4.3）：

| 流处理器 | "驿站流程" |
|---|---|
| `handle_level_irq()`（`kernel/irq/chip.c:630`） | 先关门+签收，交货，再开门 |
| `handle_fasteoi_irq()`（`kernel/irq/chip.c:689`） | **不用关门** —— 硬件自己排队（GIC 就是这种） |
| `handle_edge_irq()`（`kernel/irq/chip.c:775`） | 交货期间又来了包裹？回头再来一趟 |
| `handle_percpu_irq()`（`kernel/irq/chip.c:882`） | 极简：不登记、不统计 |

**"挨个问收件人"的证据**（`kernel/irq/handle.c:139` 的 `__handle_irq_event_percpu()`）：

| 动作 | 位置 |
|---|---|
| 遍历 action 链 | `kernel/irq/handle.c:147` |
| 调设备 handler | `kernel/irq/handle.c:158` |
| 累积返回值 `retval \|= res` | `kernel/irq/handle.c:183` |

**"驿站开关门"就是 mask/unmask**：`handle_fasteoi_irq()` 里
只有 `IRQS_ONESHOT` 时才 mask（`kernel/irq/chip.c:711-712`），
正常情况**不关门**（这就是 fasteoi 的"fast"）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"交货"这一步在代码里是不持锁的**。
   `handle_irq_event()` 在调 action 链之前**释放了 `desc->lock`**
   （`kernel/irq/handle.c:208`），之后重新加锁（`kernel/irq/handle.c:212`）。
   并发保护换成了 `IRQD_IRQ_INPROGRESS` 状态位（`kernel/irq/handle.c:207` / `:213`）。
   ⇒ 类比里"驿站员工全程盯着"，代码里是"员工把门看住，自己走开"。

2. ⚠️ **"收件人可以慢慢拆"是错的**。
   设备 handler 在硬中断上下文，**不能睡**（05 §1.4）。
   要慢慢拆得返回 `IRQ_WAKE_THREAD`，把包裹交给一个"下班后再拆"的线程（03 §A7）。

3. ⚠️ **中文"中断处理函数"这个词同时指这两个东西** ——
   这是本子系统最大的术语坑。本套文档一律区分为
   「**流处理器**」和「**设备 handler**」（02 §1.0.E）。

---

## §4 为什么 `enable_irq()` 要计数？

### 先举个例子

一间会议室的门，有三个人都可能想"暂时别让人进来"：
主讲人、录像师、翻译。

如果门只有一个"锁/不锁"的开关：
主讲人锁上门 → 录像师也锁上门（其实已经锁了）→
主讲人讲完开了门 → **录像师还在录，门却开了**。

正确做法是门上挂一个**计数器**：
每个人要求锁门就 +1，解除就 -1，**只有计数归零才真的开门**。

### 回到代码

| 元素 | 位置 |
|---|---|
| 计数器 `irq_desc.depth` | `include/linux/irqdesc.h:63` |
| `__disable_irq()`（+1） | `kernel/irq/manage.c:681` |
| `__enable_irq()`（-1） | `kernel/irq/manage.c:776` |
| `disable_irq()` | `kernel/irq/manage.c:728` |
| `enable_irq()` | `kernel/irq/manage.c:815` |
| 唤醒也有一个独立计数 `wake_depth` | `include/linux/irqdesc.h:64` |
| `irq_set_irq_wake()` | `kernel/irq/manage.c:879` |

**多解一次会被抓住**：`__enable_irq()` 在 `depth` 已经是 0 时
打一条 `Unbalanced enable for IRQ %d` 并直接返回（01 §2.1、07 §2.3）。

⚠️ **少解一次不会有任何提示** —— 中断永久沉默，dmesg 干净。
⇒ 这是"没有日志的 bug"的典型（07 §6.7 最后一行）。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"锁上门就没人能进"是错的** —— 这是 §5 的整个内容：
   `disable_irq()` 之后**硬件上中断线仍然是活的**（lazy disable）。
   ⇒ 类比里"锁门"是一个物理动作，代码里它默认只是"记个账"。

2. ⚠️ **"三个人"暗示计数是 per-requester 的 —— 不是**。
   `depth` 是**每条中断一个全局计数**，不区分谁加的。
   ⇒ A 模块 `disable_irq()` 两次、B 模块 `enable_irq()` 两次，账面平了，
   但 A 以为自己还锁着 —— **这类 bug 内核挡不住**。
   ⇒ 所以约定是"谁 disable 谁 enable，别跨模块配平"。

---

## §5 为什么 `disable_irq()` 之后中断线还是活的？

### 先举个例子

你在办公室，门口有个门铃。你现在不想被打扰。

**做法 A（立即生效）**：走到门口，**把门铃的电线剪断**。
代价：走过去一趟、剪一次；恢复时还要走过去接回来。

**做法 B（懒惰）**：在心里记一句"我现在不接待"，**门铃照常响**。
有人按了铃 → 你听见了 → **这时候才走过去把线剪断**，并且记一句"刚有人来过"。
恢复时：如果这期间**没人按过铃**，你根本不用走过去 —— **零成本**。

内核默认用 **做法 B**。理由：绝大多数 `disable_irq()`/`enable_irq()`
成对出现，而且中间**没有中断到来**。做法 B 在这种情况下**一次寄存器都不用写**。

### 回到代码

| 阶段 | `IRQD_IRQ_DISABLED` | `IRQD_IRQ_MASKED` | 硬件 |
|---|---|---|---|
| 正常 | 0 | 0 | 使能 |
| `disable_irq()` 后 | **1** | **0** | **仍然使能** |
| 中断真的来了 | 1 | 0 → **1** | 此刻才 mask，并置 `IRQS_PENDING` |
| `enable_irq()` 后 | 0 | 0 | 使能（若之前 pending 则可能重发） |

| 元素 | 位置 |
|---|---|
| 两个状态位（分离的原因） | `include/linux/irq.h:235`（DISABLED）· `include/linux/irq.h:236`（MASKED） |
| `IRQS_PENDING` | `kernel/irq/internals.h:64` |
| "来了才 mask"那一段 | `kernel/irq/chip.c:704-707` |
| `irq_disable()`（chip 层）—— 有 `chip->irq_disable` 才立即生效 | `kernel/irq/chip.c:385` |
| 关掉这个优化的开关 `IRQ_DISABLE_UNLAZY` | `include/linux/irq.h:101` |
| `enable_irq()` 时补发丢掉的那次 | `kernel/irq/resend.c:106` 的 `check_irq_resend()` |

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"门铃照常响不影响什么"在某些硬件上是错的**。
   有的外设在中断**未被确认**时会**卡住**（比如它的 FIFO 满了就不再收数据）。
   ⇒ 这类硬件必须设 `IRQ_DISABLE_UNLAZY`（`include/linux/irq.h:101`）强制做法 A。
   ⇒ **典型场景：GPIO + 电平触发的外设。**

2. ⚠️ **"记一句刚有人来过"暗示只记一次 —— 但补发只补一次**。
   `disable_irq()` 期间来了 5 次中断，`IRQS_PENDING` 只是一个**位**，
   `enable_irq()` 时只补发一次。⇒ **计数会丢**，不能靠中断次数做精确计数。

3. ⚠️ **`disable_irq()` 本身会睡** —— 它要等正在执行的 handler 跑完
   （`kernel/irq/manage.c:728` → `synchronize_irq()`）。
   ⇒ 类比里"心里记一句"是零成本的，代码里这个函数**不能在中断上下文调**。
   要零成本版用 `disable_irq_nosync()`（`kernel/irq/manage.c:710`）。

---

## §6 `IRQF_ONESHOT` 到底在防什么？

### 先举个例子

一个自动门上的**红外感应器**：**只要有人站在门口，它就一直输出"有人"信号**
（这是电平触发）。

门控系统的处理流程是：
① 收到"有人"信号 → ② 开门（这个动作要 3 秒）→ ③ 检查还有人吗。

**问题**：如果系统在 ① 之后**立刻就去看信号**，它会发现"还是有人"（人还没走过去），
于是又触发一次 ① → 又开一次门 → 无限循环。

**正确做法**：① 之后**先把感应器的输入忽略掉**，等 ② 的三秒动作**完全做完**，
再重新开始看信号。

这就是 `IRQF_ONESHOT`：**在线程处理完之前，一直屏蔽中断**。

### 回到代码

四步闭环（03 §B6）：

| 步骤 | 动作 | 位置 |
|---|---|---|
| ① 申请时 | `desc->istate \|= IRQS_ONESHOT`；分配 `action->thread_mask` 的一位 | `kernel/irq/manage.c:1497` 的 `__setup_irq()` |
| ② 中断到达 | `IRQS_ONESHOT` ⇒ `mask_irq(desc)` | `kernel/irq/chip.c:711-712` |
| ③ handler 返回 `IRQ_WAKE_THREAD` | `desc->threads_oneshot \|= action->thread_mask` | `kernel/irq/handle.c:61` 的 `__irq_wake_thread()` |
| ④ 线程跑完 | `threads_oneshot` 归零 ⇒ `unmask_irq(desc)` | `kernel/irq/manage.c:1074` 的 `irq_finalize_oneshot()` |

**`threads_oneshot` 是位图不是计数器**：
一条共享中断上有多个 ONESHOT action 时，**必须所有线程都跑完才开门**。
⇒ 这也解释了为什么 ONESHOT action 的数量有机器字长的上界（02 §5.6）。

**强制线程化时有个特殊处理**：`irq_finalize_oneshot()` 的第一句就把
`irq_forced_secondary_handler` 排除掉（`kernel/irq/manage.c:1077-1079`）——
`threadirqs` 造出来的第二个 action 不该重复开门。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"边沿触发不需要"这个推论只有一半对**。
   边沿触发的硬件确实不会持续拉信号，所以理论上不需要 ONESHOT。
   ⚠️ 但如果你**在 `thread_fn` 里才清中断源**，那么在线程跑之前
   硬件可能又产生一次边沿 ⇒ 还是要 ONESHOT。
   ⇒ 正确判据是 05 §1.3 那句：
   **"handler 返回 `IRQ_WAKE_THREAD` 之后，硬件的中断线还会不会继续拉着 / 再次触发？"**

2. ⚠️ **"忽略输入"暗示是软件层面的过滤 —— 不是**。
   `mask_irq()` 是**真的操作硬件寄存器**屏蔽这条中断线。
   ⇒ 所以在慢总线上（I2C 扩展器）这一步的代价很高，
   需要 `IRQCHIP_ONESHOT_SAFE`（02 §6.4）告诉核心"我硬件天然满足，别 mask 了"。

3. ⚠️ **`handler` 传 NULL + `thread_fn` 非空时，`IRQF_ONESHOT` 不是"建议"而是"必须"** ——
   `request_threaded_irq()` 会直接返回 `-EINVAL`（01 §1.4、05 §1.3）。
   类比里"不加也能凑合跑"，代码里根本不让你申请。

---

## §7 为什么共享中断的 handler 必须正确返回 `IRQ_NONE`？

### 先举个例子

一间合租房，**三个人共用一个门铃**。门铃响了，三个人都出来看。

正确的做法：每个人看一眼门口 —— 「是找我的吗？」
- 是我的客人 ⇒ 「**是我的**」（= `IRQ_HANDLED`）
- 不是 ⇒ 「**不是我的**」（= `IRQ_NONE`）

现在假设室友甲**懒得看**，每次都说「是我的」。
后果：某天门铃因为**线路故障**自己响个不停 ——
房东（内核）想判断"是不是没人管这个铃"，
但甲一直说"是我的" ⇒ **房东永远发现不了故障**，门铃响一整年。

反过来，如果室友乙**明明是自己的客人却说"不是我的"** ——
房东数了 100000 次，发现 99% 的响铃"没人认领"，
于是**把门铃拆了**并贴一张纸："nobody cared"。
⇒ **乙的客人从此再也进不来。**

### 回到代码

| 返回值 | 值 | 含义 |
|---|---|---|
| `IRQ_NONE` | 0 | 不是我的 |
| `IRQ_HANDLED` | 1 | 处理了 |
| `IRQ_WAKE_THREAD` | 2 | 交给线程 |

| 环节 | 位置 |
|---|---|
| 累积返回值 `retval \|= res` | `kernel/irq/handle.c:183` |
| 交给乱中断检测 | `kernel/irq/handle.c:198` 的 `note_interrupt()` |
| 检测实现 | `kernel/irq/spurious.c:272` |
| 报告并"拆门铃" | `kernel/irq/spurious.c:192` 的 `__report_bad_irq()` |
| `nobody cared` 那句 | `kernel/irq/spurious.c:202` |
| 置 `IRQS_SPURIOUS_DISABLED` | `kernel/irq/internals.h:59` |
| 提前发现的窗口 | `/proc/irq/N/spurious`（`kernel/irq/proc.c:288`） |

**`retval |= res` 那个"或"是关键**（`kernel/irq/handle.c:183`）：
三个 handler 里**只要有一个**返回 `IRQ_HANDLED`，
`note_interrupt()` 就认为"这次中断有人管"。
⇒ 所以**一个说谎的 handler 就能瞒住整个机制**（室友甲的情况）。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"房东数 100000 次"这个门槛在类比里像是很宽松，实际很容易撞上**。
   一个每秒响 10000 次的中断（网卡、DMA）只要 10 秒就能凑够 100000 次。
   ⇒ **误报确实存在** —— 但 arm64 上误报的概率远低于 x86
   （x86 老 PCI 有真实的"接错线"问题，arm64 没有）。
   ⇒ 04 §4.3：**arm64 上看到 `nobody cared` 基本一定是驱动 bug。**

2. ⚠️ **"拆门铃"不是永久的**。
   `IRQS_SPURIOUS_DISABLED` 之后核心会启动一个定时器**周期性轮询**这条中断
   （`kernel/irq/spurious.c:144` 的 `poll_spurious_irqs()`）——
   所以中断不是彻底死了，只是变成了低频轮询。⇒ 症状是"设备变得极慢"而不是"完全不工作"。

3. ⚠️ **"三个人都出来看"暗示顺序无关 —— 但有个陷阱**：
   `handle_fasteoi_irq()` 在**没有任何 action** 时会置 `IRQS_PENDING` 并 mask
   （`kernel/irq/chip.c:704-707`）。
   ⇒ 共享中断的三个驱动如果**加载顺序不对**（第一个还没 `request_irq`，
   第二个的中断先来了），会走到这条路上。这不是返回值的问题，是时序问题。

---

## §8 为什么层级域"分配自顶向下、激活自底向上"？

### 先举个例子

装一条**跨三个运营商的国际专线**：本地接入商 → 国内骨干 → 国际出口。

**第一阶段：申请线路（= 分配）**
你先找**本地接入商**申请 → 本地接入商发现自己要占用国内骨干的一条通道，
于是去找**国内骨干**申请 → 国内骨干又去找**国际出口**申请。
**顺序：从你最近的一层开始，往外一层层申请。**

**第二阶段：开通（= 激活）**
线路都占好了，现在要通电。
**必须从最外层（国际出口）先通** —— 如果本地先通了但国际出口还没通，
数据发出去就丢了。
⇒ **顺序：从最远的一层开始，往回一层层开通。**

**两个阶段方向相反，而且都是必须的。**

### 回到代码

| 阶段 | 方向 | 入口 | 递归位置 |
|---|---|---|---|
| **分配** | **自顶向下**（子 → 父） | `irq_domain_alloc_irqs()` | 你的 `.alloc` 里调 `irq_domain_alloc_irqs_parent()`（05 §5.2） |
| **激活** | **自底向上**（父 → 子） | `irq_domain_activate_irq()`（`kernel/irq/irqdomain.c:1850`） | `__irq_domain_activate_irq()`（`kernel/irq/irqdomain.c:1821`）**先递归 `parent_data`** |

| 相关位置 | 行号 |
|---|---|
| `__irq_domain_alloc_irqs()` | `kernel/irq/irqdomain.c:1554` |
| 每层建 `irq_data` | `kernel/irq/irqdomain.c:1329` |
| 调本层 `ops->alloc` | `kernel/irq/irqdomain.c:1474` |
| 激活的递归 | `kernel/irq/irqdomain.c:1821` |
| 反操作 | `kernel/irq/irqdomain.c:1869` 的 `irq_domain_deactivate_irq()` |
| 激活标记 `IRQD_ACTIVATED` | `include/linux/irq.h:228` |
| 消费者看不见这一切（`irq_startup()` 里顺手做了） | `kernel/irq/chip.c:254` |

**MSI 就是"激活必须自底向上"的最好例子**（04 §2.2）：
`.activate` 那一步做的是**把 MSI 消息写进设备的配置空间**
（`kernel/irq/msi.c:510` 的 `msi_domain_activate`）。
写进去的那个"往哪个地址写什么值"，是 **GIC/ITS 层算出来的** ——
所以必须先激活下层拿到那个值，才能激活上层去写它。

### 这个类比什么时候会误导你？

⚠️ **三处**：

1. **"国际出口"在类比里是最外层，代码里它是 `parent`（父）**。
   ⚠️ 内核的命名会让人搞反方向：**`parent` 是"更靠近硬件的那一层"**，
   不是"更靠近消费者的那一层"。
   ⇒ GIC v3 是 ITS 的 `parent`（02 §5.2 那张图）。**记住这个方向**。

2. ⚠️ **"申请和开通是两个独立的动作"在多数域上被合并了**。
   普通线中断的域**根本不填 `.activate`**（05 §5.4）——
   `irq_startup()` 里调 `irq_domain_activate_irq()` 时，
   `__irq_domain_activate_irq()` 递归下去发现没人实现，直接返回。
   ⇒ **只有 MSI 类的域才真的需要两阶段**。

3. ⚠️ **"占好线路就不会变"是错的**：`irq_domain_alloc_irqs()` 之后
   还可以 `irq_set_affinity()` 改路由，而层级里每一层都要跟着改
   （02 §3.3 那张传导图）。
   ⇒ 类比里"线路装好就固定了"，代码里它是可以运行期重新路由的。

---

## §9 为什么中断控制器比设备模型还早？

### 先举个例子

一栋新楼交付。**装修队进场之前，配电箱必须先通电** ——
因为装修队要用电钻。

但配电箱本身也是"要装的东西"。怎么办？
**它由施工方在"正式装修流程"之外单独先装好**，
不排进装修队的工单系统里。

代价：配电箱的安装队**用不了装修队的那套后勤**（材料仓库、工单、延期申请）。
它必须自己带材料、一次搞定、**不能说"我今天缺件，改天再来"**。

### 回到代码

| 事实 | 位置 |
|---|---|
| 中断控制器由 `of_irq_init()` 初始化 | `drivers/of/irq.c:553` |
| 它扫的是 `__irqchip_of_table` | `include/linux/irqchip.h:36` 的 `IRQCHIP_DECLARE` |
| 回调只收 `struct device_node *`，**没有 `struct device`** | `drivers/irqchip/irq-gic-v3.c:2185` 的 `gic_of_init(node, parent)` |
| 登记 | `drivers/irqchip/irq-gic-v3.c:2255` |

**三个后果**（03 §A2）：

| 后果 | 为什么 |
|---|---|
| **不能用 `devm_*`** | 没有 `struct device` 挂 devres ⇒ 只能手工 `kfree` / `iounmap`（05 §3.2 那个 `goto err_*` 链） |
| **不能返回 `-EPROBE_DEFER`** | `of_irq_init()` 没有重试机制 |
| **`irq_domain` 没有 devres 版本** | 01 §8 末尾那条 —— 这是上面两条的直接结果 |

**依赖关系怎么解决**：`of_irq_init()` **按 `interrupt-parent` 排拓扑序**，
保证父控制器先初始化（03 §A2）。
⚠️ 但它**只看中断父子关系** —— 如果你的控制器还依赖一个时钟，那个时钟此刻可能不存在。

### 这个类比什么时候会误导你？

⚠️ **两处**：

1. **"所有中断控制器都这样"是错的**。
   只有**一级控制器**（直接接 CPU 的，如 GIC）走 `of_irq_init()`。
   二级控制器（GPIO 扩展器、PCI 上的控制器、I2C 扩展器）
   是**正常的 platform / i2c / pci 驱动**，有 `struct device`，
   能用 `devm_*` 和 `-EPROBE_DEFER`（02 §2.2）。
   ⇒ **这是 irq 子系统和 i2c/spi 最大的结构差异：它的"控制器"一半在设备模型里、一半在外面。**

2. ⚠️ **"配电箱一次装好"暗示不会失败 —— 会失败，而且很难查**。
   `of_irq_init()` 阶段的失败发生在 **console 可能还没起来**的时候，
   ⇒ 一句 `pr_err` 都可能看不到。
   ⇒ 排查一级控制器的 init 失败要用 `earlycon`。

---

## §10 阅读下一篇

| 你现在想知道什么 | 去哪 |
|---|---|
| 这些函数的完整签名和错误码 | 01 篇 |
| §1 那些结构体的字段和四组 flag | 02 篇 |
| §2 §5 §6 §8 的完整机制剖析 | 03 篇（Part A 时间线 / Part B 横切） |
| §7 提到的乱中断检测框架 | 04 篇 §4 |
| 我要动手写代码 | 05 篇（§2 对应本篇 §9，§3 对应本篇 §2） |
| DTS 怎么写、hwirq 换算 | 06 篇 |
| 这些现象怎么观察 | 07 篇 |
| 本套文档的取证方法和未验证项 | 99 篇 |

### §10.1 九个主题里最该记住的三条

1. **级联和层级是两回事**（§2）。判据是「这一层要不要有自己的 `irq_chip` 回调
   作用在**同一个** virq 上」。绝大多数 GPIO 控制器是**级联**。
2. **"中断处理函数"这个词同时指流处理器和设备 handler**（§3）。
   前者是控制器驱动写的、一个；后者是设备驱动写的、一条链。
3. **`disable_irq()` 默认不真的关硬件**（§5）。
   要立即生效必须设 `IRQ_DISABLE_UNLAZY`。
