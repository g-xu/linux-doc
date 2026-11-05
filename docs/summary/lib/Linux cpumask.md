## cpumask
`cpumask` 是 Linux 内核中用于表示系统中 CPU 集合的核心数据结构。简单来说，它就是一个**位图（Bitmap）**，其中的每一位（bit）对应系统中的一个 CPU 核心。如果第 `n` 位被置为 1，则表示 CPU `n` 在该集合中；如果为 0，则表示不在。

随着多核（SMP）和众核（Many-core）系统的普及，`cpumask` 在内核的**调度器、中断处理、电源管理、内存管理（NUMA）**等子系统中扮演着至关重要的角色。

以下是对 `cpumask` 的详细介绍：

---

### 1. 核心定义与数据结构

#### 1.1 结构体定义
在 `&lt;linux/cpumask.h&gt;` 中，`cpumask` 的本质是一个包含 `unsigned long` 数组的结构体：

```c
typedef struct cpumask {
    unsigned long bits[BITS_TO_LONGS(NR_CPUS)];
} cpumask_t;
```

*   **`NR_CPUS`**: 这是一个内核编译配置选项 (`CONFIG_NR_CPUS`)。它决定了内核支持的最大 CPU 数量。
*   **`bits`**: 这是一个数组，其长度足以容纳 `NR_CPUS` 个位。例如，在 64 位机器上，如果 `NR_CPUS` 是 64，数组长度就是 1；如果是 128，长度就是 2。

#### 1.2 `nr_cpu_ids` (运行时限制)
虽然 `NR_CPUS` 是编译时的最大值（可能设得很大，如 8192），但在实际启动时，机器可能只有 8 个核。内核使用全局变量 **`nr_cpu_ids`** 来记录实际可能的 CPU 数量。
*   **优化**：所有的 `cpumask` 遍历操作通常基于 `nr_cpu_ids` 而不是 `NR_CPUS`，以提高性能。

---

### 2. 内存分配策略：堆还是栈？

这是开发内核模块时最需要注意的地方。根据 `NR_CPUS` 的大小，`cpumask` 可能非常大。

#### 2.1 小规模系统
如果 `NR_CPUS` 较小（例如嵌入式系统），直接在栈上定义 `cpumask_t` 是可以的。

#### 2.2 大规模系统 (`CONFIG_CPUMASK_OFFSTACK`)
在服务器内核中，`NR_CPUS` 通常很大（例如 4096）。
*   4096 bits = 512 Bytes。
*   如果在栈上分配多个这样的变量，很容易导致**内核栈溢出**（内核栈通常只有 8KB 或 16KB）。

因此，内核引入了 `cpumask_var_t` 机制：

*   **`cpumask_var_t mask;`**:
    *   如果未开启 OFFSTACK：它就是 `cpumask_t mask;`（栈分配）。
    *   如果开启了 OFFSTACK：它变成了 `struct cpumask *mask;`（指针）。

**正确的分配方式**：
总是应该使用动态分配接口，以保证代码在不同配置下的兼容性：

```c
cpumask_var_t my_mask;

// 分配内存 (GFP_KERNEL 是分配标志)
if (!zalloc_cpumask_var(&my_mask, GFP_KERNEL)) {
    return -ENOMEM;
}

// ... 使用 my_mask ...

// 释放内存
free_cpumask_var(my_mask);
```

---

### 3. 常用 API 操作

内核提供了一整套位操作宏来处理掩码。

#### 3.1 设置与清除
```c
cpumask_set_cpu(cpu, mask)    // 将 mask 的第 cpu 位设为 1
cpumask_clear_cpu(cpu, mask)  // 将 mask 的第 cpu 位设为 0
cpumask_test_cpu(cpu, mask)   // 检查 mask 的第 cpu 位是否为 1
cpumask_test_and_set_cpu(cpu, mask) // 原子地设置并返回旧值
cpumask_setall(mask)          // 将所有位设为 1
cpumask_clear(mask)           // 将所有位设为 0
```

#### 3.2 集合运算
```c
cpumask_and(dst, src1, src2)  // dst = src1 & src2 (交集)
cpumask_or(dst, src1, src2)   // dst = src1 | src2 (并集)
cpumask_xor(dst, src1, src2)  // dst = src1 ^ src2 (异或)
cpumask_andnot(dst, src1, src2) // dst = src1 & ~src2 (差集)
cpumask_equal(src1, src2)     // 检查两个掩码是否相等
```

#### 3.3 统计与查找
```c
cpumask_weight(mask)          // 返回 mask 中为 1 的位的个数 (Hamming Weight)
cpumask_first(mask)           // 返回 mask 中第一个为 1 的 cpu 编号
cpumask_next(cpu, mask)       // 返回 cpu 之后的下一个为 1 的 cpu 编号
cpumask_empty(mask)           // 检查 mask 是否全为 0
```

#### 3.4 遍历 (Iteration)
这是最常用的操作：
```c
int cpu;
for_each_cpu(cpu, mask) {
    /* 对 mask 中每一个被置位的 CPU 执行操作 */
    pr_info("CPU %d is in the mask\n", cpu);
}
```

---

### 4. 四个关键的全局掩码

Linux 内核维护了四个全局的 `cpumask` 来表示 CPU 的不同生命周期状态。理解它们对于驱动开发和调试非常重要。

| 掩码名称 | 描述 | 含义 |
| :--- | :--- | :--- |
| **`cpu_possible_mask`** | **可能的 CPU** | 硬件上支持的最大 CPU ID 范围。包括已插入的和可能热插拔插入的 CPU。这是系统启动时确定的，通常在运行时不会变。 |
| **`cpu_present_mask`** | **存在的 CPU** | 当前物理上已经插入主板并被内核识别的 CPU。 |
| **`cpu_online_mask`** | **在线的 CPU** | 当前已初始化完成并准备好执行任务的 CPU。调度器只会把进程调度到 `online` 的 CPU 上。 |
| **`cpu_active_mask`** | **活跃的 CPU** | 这是一个细微的区分，主要用于调度域迁移。通常与 `online` 一致，但在 CPU 热拔出的过程中，CPU 可能先从 `active` 移除（停止接受新任务迁移），然后再从 `online` 移除。 |

**层级关系**：
`cpu_possible_mask` ⊇ `cpu_present_mask` ⊇ `cpu_online_mask` ⊇ `cpu_active_mask`

**常用遍历宏**：
*   `for_each_possible_cpu(cpu)`
*   `for_each_present_cpu(cpu)`
*   `for_each_online_cpu(cpu)` (最常用)

---

### 5. 应用场景举例

#### 5.1 设置进程/线程亲和性 (Affinity)
我们可以限制某个任务只能在特定的 CPU 上运行。
```c
struct cpumask mask;

// 建议只在栈上定义较小的 mask，或者使用 cpumask_var_t
cpumask_clear(&mask);
cpumask_set_cpu(0, &mask); // 只允许在 CPU 0 运行
cpumask_set_cpu(2, &mask); // 允许在 CPU 2 运行

// 设置当前进程的亲和性
set_cpus_allowed_ptr(current, &mask);
```

#### 5.2 中断亲和性 (IRQ Affinity)
在驱动中，可以提示中断控制器将特定中断发送给哪些 CPU 处理，以实现负载均衡或缓存优化。
```c
// 内核内部接口，通常通过 /proc/irq/N/smp_affinity 修改
irq_set_affinity_hint(irq, mask);
```

#### 5.3 隔离 CPU (Isolcpus)
通过启动参数 `isolcpus=...`，内核会将指定的 CPU 从 `cpu_active_mask` 中移除（但在 `online` 中），从而使标准调度器不再往这些 CPU 上分配普通任务，常用于实时系统（RT）以独占 CPU 资源。

---

### 6. 用户空间接口

在用户空间（Shell 或 C 程序）查看 `cpumask` 通常是通过 sysfs 或 procfs。

*   **Hex 格式**: `/sys/devices/system/cpu/online` 可能显示 `0-3`，但在底层的 mask 文件中通常是十六进制。
    *   例如：Mask 为 `3` (二进制 `0011`) 表示 CPU 0 和 1。
    *   Mask 为 `f` (二进制 `1111`) 表示 CPU 0-3。

*   **List 格式**: 用户更习惯看列表格式，如 `0-3,5` 表示 CPU 0, 1, 2, 3 和 5。
    *   内核提供了 `cpulist_parse` 等函数用于解析用户传入的这种字符串。

### 总结

*   **`cpumask` 是位图**：每一位代表一个 CPU。
*   **注意内存大小**：在编写通用内核模块时，避免在栈上直接定义 `cpumask_t`，请使用 `zalloc_cpumask_var` 和 `free_cpumask_var`。
*   **使用标准宏**：利用 `for_each_cpu` 进行遍历，利用 `cpumask_set_cpu` 等进行操作。
*   **理解全局掩码**：清楚 `online` 和 `possible` 的区别，避免访问不存在的 CPU 导致崩溃。