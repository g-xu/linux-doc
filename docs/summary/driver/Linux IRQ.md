## 接口定位
内核IRQ对外公共接口主要定义在 &lt;u&gt;/include/linux/interrupt.h&lt;/u&gt;
IRQ内部使用接口主要定义在 &lt;u&gt;/include/linux/irq.h&lt;/u&gt; , &lt;u&gt;/include/linux/irq*.h&lt;/u&gt; :

内部主要API头文件：
- irq_poll.h: IRQ polling, 一种混合了中断和轮询的技术，可以在高负载时减少中断开销，Linux内核中有类似的实现(如NAPI网络子系统)
- irq_work.h  提供了一种机制，允许在中断（IRQ）上下文中运行代码
- irq.h： 提供irq core层相关接口给chip等使用，
	- irqd_xxx : irq domain 相关简化接口
	- handle_xxx: 通用中断处理函数
	- irq_chip_xxx：irq chip处理函数
	- irq_xxx: chip/data相关处理
- irqdesc.h: irq descriptor 相关接口
- irqdomain.h:  irq domain 处理接口

## Public API

### 硬件IRQ

| API                                                              | 相似API                                                                                      | 说明                     |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------------------- |
| enable_irq                                                       | enable_percpu_irq<br>enable_nmi<br>enable_percpu_nmi                                       | 使能指定IRQ                |
| disable_irq                                                      | disable_irq_nosync<br>disable_hardirq<br>disable_percpu_irq<br>disable_percpu_nmi          | 关闭IRQ                  |
| request_irq<br>request_threaded_irq<br>devm_request_threaded_irq | request_any_context_irq<br>request_percpu_irq<br>request_percpu_nmi                        | 为指定irq增加handler处理函数    |
| free_irq                                                         | free_percpu_irq<br>free_nmi<br>free_percpu_nmi                                             | 释放irq                  |
| irq_set_affinity                                                 | irq_force_affinity<br>irq_can_set_affinity<br>irq_select_affinity<br>irq_set_affinity_hint | 设置irq亲和性               |

注意：上面的IRQ接口里面，使用的irq都是virq，也就是软件中断号，硬件中断号只给底层irqchip使用，virq和hwirq是通过每个中断控制器的domain来映射的，映射方式有很多种，直接映射，tree映射等等。

### 软中断与 Tasklet API 列表

| 机制分类                    | API 函数                        | 功能描述                                        | 备注 / 注意事项                                                                            |
| :---------------------- | :---------------------------- | :------------------------------------------ | :----------------------------------------------------------------------------------- |
| **通用控制**<br>(下半部锁)      | `local_bh_disable()`          | **关闭**当前 CPU 的软中断和 Tasklet 处理。              | 1. 用于在进程上下文中保护临界区，防止被软中断打断。<br>2. 支持嵌套调用（内部有计数器）。                                    |
|                         | `local_bh_enable()`           | **开启**当前 CPU 的软中断和 Tasklet 处理。              | 如果计数器减为 0 且有挂起的软中断，会立即触发软中断处理。                                                       |
| **Softirq**<br>(软中断)    | `open_softirq(nr, handler)`   | **注册**指定类型软中断的处理函数。                         | **仅限内核核心开发**。驱动开发通常无法新增软中断类型。                                                        |
|                         | `raise_softirq(nr)`           | **触发**一个软中断，将其标记为挂起状态。                      | 内核会在稍后（通常是硬中断退出时）执行它。                                                                |
|                         | `raise_softirq_irqoff(nr)`    | 同上，但假设当前硬中断**已经关闭**。                        | 性能稍高，但如果中断未关闭会出问题。                                                                   |
| **Tasklet**<br>(声明与初始化) | `DECLARE_TASKLET(name, func)` | **静态定义**并初始化一个 Tasklet。                     | 编译时分配内存。`func` 是回调函数。                                                                |
|                         | `tasklet_init(t, func, data)` | **动态初始化**一个已经分配内存的 `struct tasklet_struct`。 | 常用于结构体成员的初始化。<br>`data` 是传给回调函数的参数。                                                  |
| **Tasklet**<br>(调度与执行)  | `tasklet_schedule(t)`         | **调度** Tasklet 执行。                          | 1. 将 Tasklet 挂入当前 CPU 的待处理链表。<br>2. 只能运行一次（如果已调度未运行，再次调用无效）。                         |
|                         | `tasklet_hi_schedule(t)`      | 调度 **高优先级** 的 Tasklet。                      | 使用 `HI_SOFTIRQ`，优先级高于普通 Tasklet。                                                     |
| **Tasklet**<br>(控制与销毁)  | `tasklet_disable(t)`          | **禁用**指定的 Tasklet。                          | 如果该 Tasklet 正在运行，会**忙等待**直到它运行结束。                                                    |
|                         | `tasklet_disable_nosync(t)`   | **异步禁用**。                                   | 标记为禁用后立即返回，不等待正在运行的实例结束。                                                             |
|                         | `tasklet_enable(t)`           | **启用**此前被禁用的 Tasklet。                       | 必须与 `tasklet_disable` 配对使用。                                                          |
|                         | `tasklet_kill(t)`             | **销毁** Tasklet。                             | 1. 等待该 Tasklet 运行结束并将其从调度链表中移除。<br>2. **禁止在中断上下文调用**（因为它可能会睡眠/忙等）。<br>3. 常用于模块卸载函数中。 |
## IRQ

Linux 内核的 **IRQ (Interrupt Request) 子系统** 是操作系统中最复杂、最重要的部分之一。它是连接**硬件设备**与**CPU**的桥梁，负责管理中断的注册、响应、分发和执行。Linux 的 IRQ 子系统采用分层设计，称为 **Generic IRQ Subsystem (通用中断子系统)**，旨在屏蔽不同架构（x86, ARM, RISC-V 等）和不同中断控制器（GIC, APIC）的硬件差异，为驱动开发者提供统一的 API。

---

### 1. 整体架构 (三层模型)

通用中断子系统从上到下分为三层：

1.  **高层 (High-level) - 驱动 API 层：**
    *   这是驱动开发人员直接接触的部分。
    *   负责响应中断 (`request_irq`)、释放中断 (`free_irq`)、开关中断等。
    *   不关心底层硬件细节。

2.  **中间层 (Mid-level) - 通用逻辑层：**
    *   内核的核心逻辑。负责处理中断流控（Flow Control）。
    *   例如：当一个中断到来时，是应该先屏蔽它，还是允许嵌套？是边沿触发还是电平触发？
    *   它维护了核心数据结构 `irq_desc`。

3.  **底层 (Low-level) - 芯片驱动层 (`irq_chip`)：**
    *   直接操作硬件中断控制器（Interrupt Controller，如 ARM GIC 或 Intel APIC）。
    *   负责物理上的“屏蔽”、“解除屏蔽”、“确认中断(ACK)”等寄存器操作。

---

### 2. 核心数据结构

理解 IRQ 模块，必须理解这三个核心结构体：

#### 2.1 `struct irq_desc` (中断描述符)
这是整个子系统的核心“数据库”。系统中的每一个**虚拟中断号 (virq)** 都有一个对应的 `irq_desc`。
```c
struct irq_desc {
    struct irq_data      irq_data;   // 中断数据（硬件号、芯片数据等）
    irq_flow_handler_t   handle_irq; // 高层流控回调（如 handle_edge_irq）
    struct irqaction     *action;    // 指向驱动程序注册的处理函数链表
    const char           *name;      // 中断名称（在 /proc/interrupts 显示）
    // ... 锁、状态位、亲和性设置等
};
```
内核通常维护一个 `irq_desc` 数组（或基数树），通过中断号索引。

#### 2.2 `struct irqaction` (动作描述符)
当你调用 `request_irq` 时，内核会创建一个 `irqaction` 并挂载到 `irq_desc->action` 链表上。
*   **链表结构**：如果该中断线是**共享**的（Shared IRQ），这个链表会有多个节点。
*   包含：驱动提供的处理函数指针 (`handler`)、中断标志 (`flags`)、设备名 (`name`)、设备 ID (`dev_id`)。

#### 2.3 `struct irq_chip` (硬件抽象)
定义了底层硬件的操作方法。
```c
struct irq_chip {
    const char  *name;
    void (*irq_enable)(struct irq_data *data); // 开启
    void (*irq_disable)(struct irq_data *data);// 关闭
    void (*irq_ack)(struct irq_data *data);    // 确认收到
    void (*irq_mask)(struct irq_data *data);   // 屏蔽
    void (*irq_unmask)(struct irq_data *data); // 解除屏蔽
    // ...
};
```

---

### 3. 中断处理流程 (Life Cycle)

当硬件产生中断信号时，流程如下：

1.  **CPU 异常入口**：CPU 收到电信号，暂停当前执行流，跳转到异常向量表的中断入口。
2.  **保存现场**：保存寄存器。
3.  **`do_IRQ` (架构相关)**：读取中断控制器，获取硬件中断号。
4.  **`generic_handle_irq`**：根据中断号找到对应的 `irq_desc`。
5.  **流控处理 (`handle_irq`)**：调用芯片层的 `ack` 确认中断，根据是电平触发还是边沿触发，执行不同的屏蔽逻辑。
6.  **调用驱动处理函数 (`handle_irq_event`)**：
    *   遍历 `irq_desc->action` 链表。
    *   执行驱动注册的 `handler` 函数。
    *   **注意：** 这一步运行在**硬中断上下文 (Hard IRQ Context)**。
7.  **唤醒线程化中断 (可选)**：如果驱动使用了 `Threaded IRQ`，这里会唤醒对应的内核线程。
8.  **软中断检查**：硬中断结束前，检查并触发 Softirq（如下半部）。
9.  **恢复现场**：返回被中断的任务。

---
### 4. 高级概念：IRQ Domain (中断域)

在嵌入式系统（特别是 Device Tree 系统）中，硬件中断号 (HW Interrupt Number) 和 Linux 内核使用的虚拟中断号 (Virtual Interrupt Number, virq) 是不一样的。

*   **问题**：一个 SoC 可能有多个中断控制器（如 GIC 和 GPIO 控制器），它们可能都从 0 开始编号硬件中断。如果直接用硬件号，会冲突。
*   **解决**：引入 **IRQ Domain**。
    *   它负责将 `(Interrupt Controller, HW IRQ)` 映射到全局唯一的 `Linux IRQ`。
    *   驱动开发时，通常通过 `platform_get_irq()` 从设备树获取到的就是映射后的 Linux IRQ 号。

### 5. 关键注意事项 

1.  **硬中断处理函数不能睡眠**：
    *   在 `request_irq` 的 `handler` 中，绝对不能调用 `msleep`、`mutex_lock` 等会导致调度的函数。
    *   如果需要睡眠，必须使用 `request_threaded_irq` 把逻辑移到 `thread_fn` 中，或者使用工作队列。

2.  **共享中断 (Shared IRQ)**：
    *   多个设备（如 PCI 设备）可能共用一条物理中断线。
    *   注册时必须带 `IRQF_SHARED` 标志。
    *   处理函数必须检查设备寄存器，确认“是不是我产生的中断”。如果是，返回 `IRQ_HANDLED`；如果不是，返回 `IRQ_NONE`。

3.  **中断上下文的限制**：
    *   不能访问用户空间内存。
    *   栈空间非常有限（通常只有几 KB），不能分配大数组。

4.  **SMP 亲和性 (Affinity)**：
    *   多核 CPU 下，可以通过 `/proc/irq/N/smp_affinity` 控制某个中断主要由哪个 CPU 处理，以优化缓存命中率或负载均衡。

### 总结

Linux IRQ 模块通过 **Generic IRQ Subsystem** 实现了对硬件的完美抽象。对于驱动开发者来说，最重要的变化是从“大包大揽的硬中断处理”转向了 **Top Half (ACK/Mask) + Threaded IRQ (Process Logic)** 的模式，这既保证了系统的响应速度，又利用了内核线程的并发调度能力。

## 软中断

Linux 内核中的 **软中断 (Softirq)** 是内核**下半部 (Bottom Half)** 机制中最底层、最高效，但也是限制最严格的一种机制。它是 Linux 处理高并发、高吞吐量中断（特别是网络包处理）的基石。

---

### 1. 为什么需要软中断？

为了保证系统响应速度，Linux 将中断处理分为两个部分：

1.  **上半部 (Top Half / Hardirq)：**
    *   **特点：** 也就是我们常说的“硬中断处理程序”。它会**关闭中断**，必须**极快**执行，只做最核心的硬件交互（如读取寄存器、ACK 硬件、把数据拷贝到内存）。
    *   **问题：** 如果处理时间过长，会丢失后续的中断。
2.  **下半部 (Bottom Half)：**
    *   **特点：** 在中断开启的情况下运行，处理上半部遗留的耗时任务（如协议栈解析、数据解密）。
    *   **机制：** 软中断、Tasklet、工作队列 (Workqueue)。

**软中断**就是下半部机制中的“老大哥”，Tasklet 实际上也是基于软中断实现的。

---

### 2. 软中断的核心特点

1.  **静态编译 (Statically Allocated)：**
    *   软中断的类型是固定的，在内核编译时就确定了（定义在数组中）。
    *   **普通驱动开发者不能动态创建新的软中断**。如果你需要类似功能，应使用 Tasklet 或 Workqueue。
2.  **高并发性 (High Concurrency)：**
    *   **最显著的特征：** 同一个类型的软中断，可以同时在**多个 CPU** 上并行运行。
    *   *例如：* CPU0 和 CPU1 可以同时执行网络接收软中断 (`NET_RX_SOFTIRQ`)。
    *   **后果：** 软中断处理函数必须是**可重入的**，且必须使用**自旋锁 (Spinlock)** 极其小心地保护全局数据。
3.  **中断上下文 (Interrupt Context)：**
    *   软中断运行在中断上下文（Atomic Context）。
    *   **严禁睡眠 (No Sleeping)：** 不能调用 `msleep`、`mutex_lock`、`copy_from_user` 等可能导致调度的函数。
    *   **严禁访问用户空间：** 此时没有关联的用户进程上下文。
4.  **不可抢占：**
    *   软中断在执行时，不能被同 CPU 上的其他软中断抢占，只能被**硬中断**抢占。

---

### 3. 软中断的类型 (Linux 5.x)

内核定义了一个枚举 `enum` 来管理所有的软中断类型（优先级从高到低）：

| 优先级 | 类型名称 | 描述 | 应用场景 |
| :--- | :--- | :--- | :--- |
| 0 (最高) | `HI_SOFTIRQ` | 高优先级 Tasklet | 用于极高优先级的下半部任务 |
| 1 | `TIMER_SOFTIRQ` | 定时器软中断 | 处理内核定时器、高精度定时器 |
| 2 | `NET_TX_SOFTIRQ` | 网络发送软中断 | 网卡驱动发送数据包完成后的清理工作 |
| 3 | `NET_RX_SOFTIRQ` | **网络接收软中断** | **最繁忙的软中断**，负责协议栈处理 (IP/TCP) |
| 4 | `BLOCK_SOFTIRQ` | 块设备软中断 | 磁盘 IO 完成后的处理 |
| 5 | `IRQ_POLL_SOFTIRQ` | IO 轮询 | 用于高性能 IO 轮询 |
| 6 | `TASKLET_SOFTIRQ` | 普通 Tasklet | **驱动开发最常用**，基于此实现 Tasklet |
| 7 | `SCHED_SOFTIRQ` | 调度器软中断 | 负载均衡 (Load Balancing) |
| 8 | `HRTIMER_SOFTIRQ` | 高精度定时器 | (部分架构使用) |
| 9 (最低) | `RCU_SOFTIRQ` | RCU 锁处理 | 处理 RCU 回调函数 |

---

### 4. 执行时机与机制

软中断不会在随机时间触发，内核会在特定的“检查点”检查是否有软中断需要处理 (`do_softirq`)：

1.  **硬中断处理结束时 (`irq_exit`)**：这是最常见的触发点。当硬件中断处理完，准备切回用户态或内核态之前，会检查并执行软中断。
2.  **重新开启下半部时 (`local_bh_enable`)**：当代码显式开启下半部时。
3.  **ksoftirqd 线程**：当软中断负载过高时。

#### 关于 `ksoftirqd` (软中断守护线程)
如果软中断源源不断地产生（例如网络包风暴），CPU 可能会一直处理软中断，导致用户态进程被“饿死”（Starvation）。

为了解决这个问题，内核设计了保护机制：
*   如果一波软中断处理时间过长（默认几毫秒）或次数过多。
*   内核会唤醒每个 CPU 独有的内核线程 **`ksoftirqd/n`**。
*   剩余的软中断会交给这个线程在**进程上下文**中执行（拥有较低的调度优先级），从而保证用户进程也能分到 CPU 时间片。

---

### 5. 常用 API

虽然驱动一般不自定义软中断，但会用到相关控制函数：

*   `open_softirq(int nr, void (*action)(struct softirq_action *))`: 注册软中断处理函数（仅限内核核心代码）。
*   `raise_softirq(int nr)`: 触发一个软中断，标记它为挂起状态（Pending）。
*   `raise_softirq_irqoff(int nr)`: 同上，但假设硬中断已经关闭（效率更高）。
*   `local_bh_disable()` / `local_bh_enable()`: **禁止/开启** 本 CPU 的软中断。
    *   用于在进程上下文中保护共享数据，防止被软中断打断造成竞争。
    *   *注意：这不会禁止硬中断。*

---

### 6. 软中断 vs Tasklet vs 工作队列

这是内核开发中最常见的面试题和架构决策点：

| 特性 | **Softirq (软中断)** | **Tasklet** | **Workqueue (工作队列)** |
| :--- | :--- | :--- | :--- |
| **并发性** | **最高** (多 CPU 并行同类型) | 中 (同类型不能并行，不同类型可并行) | 高 (基于内核线程) |
| **上下文** | **中断上下文** (不可睡眠) | **中断上下文** (不可睡眠) | **进程上下文** (可睡眠) |
| **实现难度** | 难 (需严格的锁保护) | 易 (自带序列化保护) | 易 |
| **动态创建** | **不支持** (静态编译) | 支持 | 支持 |
| **适用场景** | 极高频率任务 (如网络收发) | 一般驱动的下半部 | 耗时任务、需要睡眠/IO的任务 |

### 7. 总结

*   **Softirq** 是 Linux 处理高负载中断的核心引擎，特别是对于网络子系统。
*   它**快、并发度高**，但要求**严苛**（不能睡、要注意多核重入）。
*   对于普通的驱动开发者，**90% 的情况应该使用 Tasklet 或 Workqueue**，除非你在开发核心网络协议栈或修改内核核心架构，否则不要直接修改或添加 Softirq。

## tasklet

Linux 内核中的 **Tasklet** 是下半部（Bottom Half）机制的一种，主要用于**驱动程序**中，用来处理中断服务程序（ISR）遗留的、非紧急但需要尽快完成的任务。它是构建在 **软中断 (Softirq)** 之上的一种封装，但比软中断更容易使用，锁机制更简单。

### 1. 核心特点

1.  **中断上下文 (Interrupt Context)：**
    *   Tasklet 运行时仍然处于中断上下文。
    *   **严禁睡眠**：不能调用 `msleep`、`mutex_lock`、`copy_from_user` 或任何可能引起调度的函数。
    *   **原子性**：它一旦开始执行，除非被更高优先级的硬中断打断，否则会一直运行直到结束。

2.  **串行化 (Serialization) —— 最大优势：**
    *   **同类型互斥**：同一个 Tasklet 实例，**同一时刻只能在一个 CPU 上运行**。
    *   这意味着：你在编写 Tasklet 回调函数时，**不需要考虑多核并发**（针对该 Tasklet 自身），省去了很多复杂的自旋锁保护。
    *   *注意：不同的 Tasklet 实例可以在不同 CPU 上同时运行。*

3.  **调度时机：**
    *   通常在硬中断处理完后，或者系统负载较低时尽快执行。

---

### 2. Tasklet 与 Softirq 的区别

| 特性 | Softirq (软中断) | Tasklet (小任务) |
| :--- | :--- | :--- |
| **底层基础** | 基础机制 | 基于 Softirq 实现 (`TASKLET_SOFTIRQ`) |
| **并发性** | **高** (同类型可多核并行) | **中** (同类型被锁保护，只能单核跑) |
| **锁的复杂度** | **极高** (必须仔细保护全局数据) | **低** (无需保护自身数据结构) |
| **适用对象** | 核心网络栈、块设备 IO | **绝大多数设备驱动程序** |
| **动态创建** | 不支持 (静态编译) | **支持** (运行时动态分配) |

---

### 3. 主要 API 接口

需包含头文件：`&lt;linux/interrupt.h&gt;`

#### 3.1 定义与初始化

**方式一：静态定义 (编译时)**
```c
/* 定义一个名为 my_tasklet 的 tasklet，回调函数是 my_handler，参数是 data */
/* 注意：新版内核 API 可能有所变化，旧版为 DECLARE_TASKLET(name, func, data) */
DECLARE_TASKLET(my_tasklet, my_handler); 
```

**方式二：动态初始化 (运行时)**
```c
struct tasklet_struct my_tasklet;

void my_handler(unsigned long data) {
    /* 处理逻辑 */
}

/* 在模块 init 中 */
tasklet_init(&my_tasklet, my_handler, (unsigned long)some_data);
```

#### 3.2 调度 (触发)

```c
/* 
 * 调度 tasklet。
 * 这通常在硬中断处理函数 (Top Half) 中调用。
 * 它标记 tasklet 为 "挂起"，内核会在稍后执行它。
 */
tasklet_schedule(&my_tasklet);
```
*注意：如果 Tasklet 已经被调度但还没运行，再次调用 `tasklet_schedule` 不会有任何效果（只会运行一次）。如果它正在运行，再次调度它，它会在当前运行结束后再次运行。*

#### 3.3 禁用与销毁

```c
/* 禁用 tasklet，如果正在运行会等待其结束 */
tasklet_disable(&my_tasklet);

/* 重新启用 */
tasklet_enable(&my_tasklet);

/* 杀掉 tasklet (通常在模块卸载时使用) */
/* 确保它不再被调度，且等待当前运行的实例结束 */
tasklet_kill(&my_tasklet);
```

---

### 4. 代码示例

这是一个典型的驱动程序模板，展示了如何配合硬中断使用 Tasklet。

```c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/interrupt.h>

/* 定义 tasklet 结构体 */
static struct tasklet_struct my_tasklet;

/* 2. Tasklet 处理函数 (下半部) */
/* 这里的代码运行在中断上下文，开中断，不可睡眠 */
static void my_tasklet_handler(unsigned long data)
{
    pr_info("Tasklet: Bottom Half running on CPU %d\n", smp_processor_id());
    /* 处理耗时的数据解析等操作... */
}

/* 1. 硬中断处理函数 (上半部) */
static irqreturn_t my_irq_handler(int irq, void *dev_id)
{
    pr_info("IRQ: Top Half received.\n");
    
    /* 硬件操作：ACK中断，读取少量寄存器 ... */

    /* 调度下半部 */
    tasklet_schedule(&my_tasklet);

    return IRQ_HANDLED;
}

static int __init my_module_init(void)
{
    pr_info("Module loaded.\n");

    /* 初始化 tasklet */
    tasklet_init(&my_tasklet, my_handler, 0);

    /* 注册中断 (伪代码，实际需要真实的 IRQ 号) */
    // request_irq(irq_num, my_irq_handler, 0, "my_device", NULL);

    return 0;
}

static void __exit my_module_exit(void)
{
    /* 释放中断 */
    // free_irq(irq_num, NULL);

    /* 销毁 tasklet */
    tasklet_kill(&my_tasklet);
    
    pr_info("Module unloaded.\n");
}

module_init(my_module_init);
module_exit(my_module_exit);
```

---

### 5. 进阶知识：内部原理

Tasklet 是基于 **软中断** 实现的。
内核定义了两个专门给 Tasklet 用的软中断类型：
1.  `TASKLET_SOFTIRQ` (优先级 6)：常规 Tasklet。
2.  `HI_SOFTIRQ` (优先级 0)：高优先级 Tasklet（一般声卡或高速网卡使用 `tasklet_hi_schedule` 触发）。

**调度流程：**
1.  调用 `tasklet_schedule`。
2.  内核把该 tasklet 挂入当前 CPU 的 `tasklet_vec` 链表。
3.  触发软中断 (`raise_softirq(TASKLET_SOFTIRQ)`).
4.  稍后，软中断执行，遍历链表，依次执行 tasklet 回调函数。

---

### 6. 现状与未来 (重要)

虽然 Tasklet 非常经典，但在现代 Linux 内核开发中，**Tasklet 的地位正在下降，甚至被视为“过时”**。

**原因：**
1.  **实时性差 (RT Latency)：** 因为 Tasklet 也是软中断，如果在高负载下大量触发，会长时间占用 CPU，导致用户进程无法运行（直到 ksoftirqd 介入）。
2.  **不可控的并发：** 虽然同一个 Tasklet 只能在一个 CPU 跑，但成千上万个不同的 Tasklet 可能占满所有 CPU。

**现代替代方案：**
1.  **Threaded IRQ (线程化中断)：** 使用 `request_threaded_irq`。这是目前最推荐的方式。下半部直接在一个内核线程中运行，**可以睡眠**，可以被调度器管理优先级，非常灵活。
2.  **Workqueue (工作队列)：** 如果不需要极高的响应速度，或者需要睡眠（如 I/O 操作），必须用工作队列。

**总结：**
如果你在维护旧代码，Tasklet 很常见。如果你在写新驱动，**优先考虑 `request_threaded_irq` 或 Workqueue**，除非你有极特殊的高频、非阻塞处理需求。



