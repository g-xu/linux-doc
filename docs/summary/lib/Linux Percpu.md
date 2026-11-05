## percpu
Linux 内核中的 **per-CPU 变量（percpu）** 是一种专为多核（SMP）环境设计的核心机制。它的核心理念是**“以空间换时间”**，通过为每个 CPU 核心分配独立的变量副本，彻底消除多核间的数据竞争，从而极大提升系统性能。

以下是对 Linux 内核 percpu 机制的详细剖析：

---

### 1. 设计思想 (Design Philosophy)

在多核系统中，访问全局变量是性能杀手。percpu 的设计旨在解决以下两大核心问题：

#### A. 消除锁竞争 (Lock Contention)
*   **问题：** 如果多个 CPU 同时修改同一个全局计数器（如统计网络包数量），必须使用原子操作或自旋锁（Spinlock）来保护数据。在高并发下，CPU 会把大量时间浪费在等待锁上。
*   **解决：** percpu 给每个 CPU 一个独立的变量。CPU 0 只改 CPU 0 的副本，CPU 1 只改 CPU 1 的副本。**完全不需要加锁**（除非涉及中断上下文，仅需简单的抢占控制）。

#### B. 避免缓存伪共享与颠簸 (Cache Line Bouncing)
*   **问题：** 即使使用原子操作，当 CPU A 修改了变量，该变量所在的缓存行（Cache Line）在 CPU B 的缓存中会失效（MESI 协议）。CPU B 下次读取时必须从内存重新拉取。这种“缓存行在 CPU 间跳来跳去”的现象会严重阻塞总线。
*   **解决：** percpu 变量通常紧凑地排列在每个 CPU 自己的内存区域中，大概率常驻在本地 L1/L2 缓存中，访问速度极快且不会干扰其他 CPU 的缓存。

---

### 2. 实现原理 (Implementation Principles)

Linux 内核实现 percpu 的方式非常巧妙，分为**编译期布局**和**运行时寻址**两个阶段。

#### A. 内存布局 (Memory Layout)
1.  **静态定义：** 编译器将所有通过 `DEFINE_PER_CPU` 定义的变量放入一个特殊的段（Section），通常是 `.data..percpu`。这构成了“模板”。
2.  **启动初始化：** 内核启动时，会根据 `NR_CPUS`（CPU 数量），申请 N 块内存区域。
3.  **数据拷贝：** 内核将 `.data..percpu` 段的数据拷贝到这 N 块内存中。
4.  **偏移量数组：** 内核维护一个全局数组 `__per_cpu_offset[cpu]`，记录了每个 CPU 的内存块相对于“模板”基地址的偏移量。

#### B. 寻址计算 (Addressing)
当代码访问一个 percpu 变量时，逻辑公式如下：
$$ \text{目标地址} = \text{变量基地址 (静态地址)} + \text{当前 CPU 的偏移量} $$

#### C. 硬件加速 (Hardware Acceleration)
为了极致性能，Linux 利用 CPU 的特殊寄存器来存储“当前 CPU 的偏移量”，避免查数组：
*   **x86_64：** 使用 **`gs` 段寄存器**。访问 percpu 变量被编译为类似 `mov %gs:variable, %reg` 的汇编指令。这使得访问 percpu 变量几乎与访问局部变量一样快，且只需**单条指令**。
*   **ARM64：** 使用 **`tpidr_el1`** 系统寄存器存储偏移量。

---

### 3. 应用场景 (Application Scenarios)

1.  **系统统计 (Statistics):**
    *   网络协议栈统计（如 `SNMP` 计数器、收发包数量）。
    *   磁盘 I/O 统计。
    *   *原理：* 每个 CPU 只加自己的数，读取时遍历所有 CPU 求和。

2.  **内存管理 (Memory Management):**
    *   **Slab/Slub 分配器：** 每个 CPU 都有本地的对象缓存（Cpu Local Slab）。分配内存时优先从本地拿，不需要加全局锁。

3.  **进程调度 (Scheduler):**
    *   **运行队列 (Runqueues):** 每个 CPU 都有自己的 `rq` 结构体，只调度自己队列里的进程，互不干扰。

4.  **高频状态机:**
    *   RCU (Read-Copy-Update) 机制中的本地状态记录。

---

### 4. API 及其功能详解

Linux 提供了两套 API：
1.  **传统 API**（如 `get_cpu_var`）：语义明确，自动处理抢占，但指令数较多。
2.  **现代 API**（`this_cpu_*`）：利用单指令原子性，性能极致，是目前的推荐用法。

#### A. 变量定义与声明

| API | 功能描述 | 适用场景 |
| :--- | :--- | :--- |
| `DEFINE_PER_CPU(type, name)` | **静态定义**一个 percpu 变量。 | 全局变量，编译时确定。 |
| `DECLARE_PER_CPU(type, name)` | **声明**一个 percpu 变量。 | 头文件中使用，供其他模块引用。 |
| `alloc_percpu(type)` | **动态分配** percpu 内存空间，返回指针。 | 类似于 `malloc`，用于运行时动态结构。 |
| `free_percpu(ptr)` | 释放动态分配的 percpu 内存。 | 资源清理。 |

#### B. 数据访问（传统方式）

| API | 功能描述 | 注意事项 |
| :--- | :--- | :--- |
| `get_cpu_var(name)` | 1. 禁止内核抢占。<br>2. 返回当前 CPU 的变量副本引用。 | **必须**成对调用 `put_cpu_var`。 |
| `put_cpu_var(name)` | 1. 开启内核抢占。 | 结束访问块。 |
| `per_cpu(name, cpu)` | 获取**指定 CPU** (非当前) 的变量副本。 | 常用于汇总统计数据。 |
| `per_cpu_ptr(ptr, cpu)` | 获取动态分配指针在**指定 CPU** 上的地址。 | 配合 `alloc_percpu` 使用。 |

#### C. 数据访问（现代极致性能 `this_cpu_*` 系列）

*这是目前内核开发中最推荐的用法，生成的汇编代码最优。*

| API | 功能描述 | 原子性/安全性说明 |
| :--- | :--- | :--- |
| `this_cpu_read(name)` | 读取当前 CPU 的变量副本。 | 单指令读取，无需禁抢占。 |
| `this_cpu_write(name, val)` | 写入当前 CPU 的变量副本。 | 单指令写入。 |
| `this_cpu_add(name, val)` | 给当前 CPU 变量加值。 | **RMW (读-改-写) 原子操作**。即使发生中断，只要中断处理程序不改这个变量，就是安全的。 |
| `this_cpu_inc(name)` | 自增。 | 同上。 |
| `this_cpu_dec(name)` | 自减。 | 同上。 |
| `this_cpu_xchg(name, val)` | 交换值。 | 强原子性。 |
| `this_cpu_cmpxchg(name, old, new)` | 比较并交换 (CAS)。 | 用于无锁并发控制。 |

---

### 5. 代码示例

#### 场景：统计函数调用次数

```c
#include <linux/percpu.h>
#include <linux/module.h>

// 1. 静态定义 percpu 变量
DEFINE_PER_CPU(unsigned long, my_func_counter);

void my_function(void) {
    // 2. 使用现代 API 进行极速计数
    // 不需要显式禁止抢占，因为这是一条单指令原子操作
    this_cpu_inc(my_func_counter);
    
    // 业务逻辑...
}

// 读取总数（通常在读取统计信息的任何地方，如 /proc 接口）
unsigned long get_total_count(void) {
    int cpu;
    unsigned long total = 0;
    
    // 3. 遍历所有 CPU 汇总数据
    // 注意：这里只是读取，理论上存在竞争但不影响大概统计值，
    // 如果需要精确一致性，可能需要加锁或关中断，视业务而定。
    for_each_possible_cpu(cpu) {
        total += per_cpu(my_func_counter, cpu);
    }
    
    return total;
}
```

### 6. 核心注意事项

1.  **抢占风险：**
    *   如果你先读取一个 percpu 变量，处理一下，再写回去（Read-Modify-Write），中间如果发生了进程切换（抢占），当你回来时，你可能已经跑在另一个 CPU 上了，这时你写回的数据就是错的（写到了新 CPU 的副本里，或者覆盖了旧 CPU 的更新）。
    *   **解决：** 使用 `this_cpu_add` 这种单指令原子操作，或者使用 `get_cpu_var`（它会禁用抢占）。

2.  **中断上下文：**
    *   `this_cpu_*` 操作在单条指令内完成，通常对**软中断/硬中断**是安全的（前提是中断里不修改该变量）。如果中断里也会修改同一个变量，则需要使用 `local_irq_save` 关中断保护。

3.  **内存消耗：**
    *   虽然 percpu 很快，但它会消耗 `变量大小 * CPU核数` 的内存。不要滥用它来存储大结构体，主要用于**小型的、高频访问的**核心数据结构。