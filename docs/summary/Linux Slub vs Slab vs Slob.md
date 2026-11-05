
## slub/slab/slob

在 Linux 内核中，**伙伴系统 (Buddy System)** 负责管理物理页（通常是 4KB 为单位），这好比是“批发商”。但内核中充满了大量小对象（如 `task_struct`、`inode`、`dentry`），如果每个小对象都申请 4KB，内存浪费将极其惊人。

因此，需要在伙伴系统之上，有一个“零售商”来管理小块内存，这就是 **Slab 分配器**家族的角色。

Linux 历史上出现过三种主要机制：**Slab**、**Slob** 和 **Slub**。实际上，在最新的 Linux 内核版本（6.5+）中，**Slab 和 Slob 已经被移除，Slub 统一了天下**。

---

### 1. 始祖：Slab (The "Complex" One)
**历史地位**：最早引入（源自 SunOS），长期作为 Linux 的默认分配器，直到 2.6.23 被 Slub 逐渐取代。

*   **设计理念**：
    *   基于“对象缓存”的思想。认为对象的初始化和销毁成本很高，用完后不释放回伙伴系统，而是存起来下次直接用。
    *   非常注重 **CPU 缓存着色 (Cache Coloring)**，通过偏移量让对象在 CPU L1/L2 Cache 中均匀分布，防止 Cache Line 冲突。
*   **管理机制**：
    *   结构非常复杂。它有三个队列：`full`（全满）、`partial`（部分满）、`empty`（空）。
    *   它需要额外的内存空间来存储管理数据（metadata），即 `struct slab` 结构体，这导致了额外的内存开销。
*   **缺点**：
    *   **元数据开销大**：管理结构本身占用不少内存。
    *   **队列管理复杂**：在 NUMA 架构和多核 CPU 下，管理这三个队列的锁竞争（Lock contention）非常严重，扩展性差。
    *   **着色算法复杂**：虽然理论上好，但实际带来的性能提升与其复杂度和空间浪费相比，性价比开始变低。

### 2. 极简主义：Slob (The "Simple" One)
**历史地位**：专为嵌入式系统设计，针对内存极小（比如 < 32MB）的设备。

*   **设计理念**：
    *   **Simple List Of Blocks**。简单到极致。
    *   完全不搞什么复杂的队列和元数据结构。
*   **管理机制**：
    *   它基本上就是把所有空闲内存块串成几个链表。
    *   分配时使用 **First-Fit**（首次适应）算法，从链表头开始找，找到够大的就切一块下来。
*   **优点**：
    *   代码量极少，内核体积极小。
    *   元数据开销几乎为零（因为它把管理信息塞在空闲块里，或者紧贴着分配块）。
*   **缺点**：
    *   **碎片化严重**：First-Fit 算法容易导致严重的外部碎片。
    *   **性能差**：随着内存变大，遍历链表的时间复杂度是 O(n)，效率极低。
    *   没有 CPU 缓存优化。

### 3. 现代霸主：Slub (The "Unqueued" Slab)
**历史地位**：由 Christopher Lameter 开发，旨在解决 Slab 过于复杂的问题，成为现代内核默认分配器。

*   **设计理念**：
    *   **去繁就简**。它的全称可以理解为 "The Unqueued Slab"（无队列 Slab）。
    *   它认为：Slab 的复杂队列管理在现代 CPU 上是多余的，且元数据存储方式太浪费。
*   **核心革新（这也是它胜出的原因）**：
    *   **元数据复用 `struct page`**：Slub 最骚的操作是**不单独分配内存存元数据**。它利用了 `struct page` 结构体（每个物理页都有这个结构）中很多在分配后闲置的字段（union 联合体），把 Slab 的管理信息（如 `freelist` 指针、`inuse` 计数等）直接塞进 `struct page` 里。
    *   **CPU 局部缓存 (Per-CPU Partial)**：大大减少了锁的竞争。它让每个 CPU 都有自己的一小部分空闲对象列表，大部分分配在本地完成，无需加锁。
    *   **对象合并 (Merging)**：如果两个内核模块申请的对象大小相近（比如一个要 52 字节，一个要 56 字节），Slub 会把它们合并到同一个 64 字节的缓存池中，减少了缓存种类的数量，提高了 TLB 利用率。
*   **优点**：
    *   **高性能**：多核扩展性极好，锁竞争少。
    *   **低开销**：几乎不需要额外的内存来存储管理数据。
    *   **调试方便**：Slub 自带非常强大的调试功能（Red zoning, Poisoning）。

---

### 三者核心差异总结表

| 特性 | Slab (老祖宗) | Slob (嵌入式) | Slub (现代标准) |
| :--- | :--- | :--- | :--- |
| **元数据存储** | 额外的 `struct slab` 内存 | 极少，混在块中 | **复用 `struct page` 字段 (0开销)** |
| **管理结构** | 复杂的 Full/Partial/Empty 队列 | 简单的空闲链表 | **精简的 Partial 列表 + Per-CPU 缓存** |
| **分配算法** | 复杂，强调用 Cache Coloring | First-Fit (线性查找) | 快速路径 (Lockless) + 慢速路径 |
| **多核扩展性** | 差 (锁竞争大) | 极差 | **极好** |
| **内存碎片** | 较少 | **严重** | 极少 |
| **主要应用场景**| 早期 Unix/Linux 通用 | 极低内存嵌入式 | **现代服务器、桌面、Android** |

---

### 演化的大结局 (Current Status)

如果你现在去翻看最新的 Linux 内核代码（例如 Linux 6.8+）：

1.  **Slab (Original)**：在 Linux 6.2 版本中已被**正式移除**。代码里已经没有 `mm/slab.c` 了。
2.  **Slob**：在 Linux 6.4 版本中已被**正式移除**。
3.  **Slub**：现在是**唯一的**选择。

**为什么连 Slob 这种极简的也被移除了？**
因为 Slub 引入了一个新特性叫 **Slub Tiny**。通过编译配置，Slub 可以关闭 per-cpu 缓存和其他高级特性，以此来模拟 Slob 的低内存占用，但性能更好且不易产生碎片。

**结论：**
现在的 Linux 内核中，**"Slab 分配器" 这个术语通常指的就是 Slub 实现**。Slub 赢在架构设计的简洁性（复用 struct page）和对现代多核硬件的完美适配。

## slub实现简介

Slub (The Unqueued Slab Allocator) 的设计核心在于**“去中心化”**和**“极简数据结构”**。它的目标是在多核系统上实现极致的性能，同时尽可能减少内存占用。

为了读懂 Slub，我们需要从三个层面深入：**核心数据结构（复用的艺术）**、**内存布局（对象链表）**、以及**分配/释放流程（无锁的魔法）**。

---

### 一、 核心数据结构：复用的艺术

Slub 的三大支柱结构是 `kmem_cache`、`kmem_cache_cpu` 和 `struct page`。

#### 1. `struct kmem_cache` (总管/工厂)
这是每种对象缓存的顶层结构（例如 `task_struct` 有一个，`kmalloc-64` 有一个）。
它主要包含：
*   **`cpu_slab`**: 一个 Per-CPU 变量。这是 Slub 速度极快的核心，每个 CPU 都有自己独享的当前 Slab，操作它几乎不需要加锁。
*   **`node`**: `kmem_cache_node` 数组（对应 NUMA 节点）。这里存放着“备用粮草”（Partial Slabs）。
*   **配置信息**: 对象大小 (object_size)、对齐方式等。

#### 2. `struct kmem_cache_cpu` (CPU 本地工作台)
这是每个 CPU 正在使用的“当前 Slab”。
*   **`freelist`**: 指向当前 Slab 中第一个空闲对象的指针（**热点中的热点**）。
*   **`page`**: 指向当前正在使用的物理页（Slab Page）。
*   **`tid` (Transaction ID)**: 这是一个极其重要的字段，用于配合 CMPXCHG 指令实现**无锁并发保护**。每次分配或释放，TID 都会自增。如果操作过程中 TID 变了，说明被抢占了，需要重试。

#### 3. `struct page` (被“寄生”的物理页结构)
Slub 最精妙的设计就在这里。它没有单独申请内存来记录一个 Slab 页里哪些对象是空的，而是直接复用了 `struct page` 中的字段（使用 union）。
当一个物理页被 Slub 接管后，它的含义变成了：
*   **`page->freelist`**: 指向该页中第一个空闲对象的链表头。
*   **`page->slab_cache`**: 指向所属的 `kmem_cache`。
*   **`page->inuse`**: 计数器，记录该页已分配了多少个对象。
*   **`page->frozen`**: 这是一个关键标志位。
    *   **Frozen = 1**: 表示该页正在被某个 CPU 的 `kmem_cache_cpu` 独占使用。此时只有该 CPU 能从这里拿内存，其他 CPU 不能动它的 freelist。
    *   **Frozen = 0**: 表示该页处于 Shared 状态（在 Node 的 Partial 链表中），或者全满/全空。

---

### 二、 内存布局：嵌入式链表

在 Slab/Slob 时代，我们需要额外的位图或链表来记录谁空闲。Slub 采用了**嵌入式空闲链表 (In-object Freelist)**。

假设一个对象大小是 32 字节。
*   **当对象空闲时**：对象内部的起始位置（通常是前 8 字节）存放的是**下一个空闲对象的地址**。
*   **当对象被分配后**：这块内存交给用户写数据，Slub 不再关心内容。

**内存视图示例**：
一个 4KB 的页被切分成多个对象：
```text
[ Obj A (Free) ] -> [ Obj B (Used) ] -> [ Obj C (Free) ] -> [ Obj D (Used) ] ...
       |
    (存着 Obj C 的地址)
```
当前的 `cpu_slab->freelist` 指向 Obj A。
当我们分配 Obj A 后，CPU 里的 `freelist` 更新为指向 Obj C。

---

### 三、 分配流程 (`kmem_cache_alloc`)

Slub 的分配分为**快速路径 (Fast Path)** 和 **慢速路径 (Slow Path)**。

#### 1. 快速路径 (Lockless)
这是 99% 发生的情况。
1.  禁用内核抢占。
2.  获取当前 CPU 的 `kmem_cache_cpu`。
3.  读取 `tid` 和 `freelist`。
4.  **检查**：如果 `freelist` 不为空。
5.  **核心操作**：从 `freelist` 指向的对象中，读取下一个空闲对象的地址（Next Pointer）。
6.  **CMPXCHG (原子比较交换)**：
    *   尝试原子地更新 `cpu_slab` 的 `freelist` 和 `tid`。
    *   **原理**：如果在这几步指令执行期间，没有发生中断或抢占导致 TID 变化，则更新成功，直接返回对象。
    *   如果失败（说明被抢占或修改），跳转到慢速路径重试。

#### 2. 慢速路径 (`__slab_alloc`)
当快速路径失败（比如当前 Slab 用光了，`freelist` 为 NULL），进入慢速路径：
1.  **Deactivate**：把当前那个已经用光的 Slab 页“解冻”（Unfreeze），扔回 Partial List 或者 Full List（Slub 其实不记录 Full List，全满就只是一页普通的内存，丢在那不管，直到有人释放其中对象）。
2.  **找新的 Slab**：
    *   **Tier 1**: 检查 `cpu_slab` 里的 `partial` 链表（CPU 之前可能囤了一些半满的页）。
    *   **Tier 2**: 检查 `kmem_cache_node` 里的 `partial` 链表（其他 CPU 用剩的，或者被释放回来的）。如果找到了，就拿到当前 CPU，**Freeze** 住（独占），变成新的 `current slab`。
    *   **Tier 3**: 如果到处都找不到，向伙伴系统（Buddy System）申请新的物理页。
3.  初始化新页，设置 `freelist`，然后再次尝试分配。

---

### 四、 释放流程 (`kmem_cache_free`)

同样分为快速和慢速。

#### 1. 快速路径
我们要释放对象 X。
1.  找到 X 所属的 `struct page`。
2.  **关键检查**：判断这个 page 是否就是当前 CPU 正在使用的那个 Slab 页（即 `page == cpu_slab->page`）。
3.  如果是，直接把 X 插入到 `cpu_slab->freelist` 的头部。**无锁操作，极快**。

#### 2. 慢速路径
如果对象 X 所属的页不是当前 CPU 的活跃页（比如是别的 CPU 申请的，或者已经被扔到 Partial 列表了）：
1.  **加锁**（通常是 Node 级别的锁，或者是细粒度的 bit spinlock）。
2.  修改该 `page` 内部的 `freelist`，把 X 插回去。
3.  减少 `page->inuse` 计数。
4.  **状态流转**：
    *   如果 `inuse` 变成了 0（该页所有对象都空了）：直接把这页内存**还给伙伴系统**（释放物理内存）。这是 Slub 不容易产生碎片的原因之一。
    *   如果该页原本是 Full（无空闲），现在变成 Partial 了：把它挂入 `kmem_cache_node` 的 Partial 链表，让其他 CPU 有机会用到它。

---

### 五、 Slub 的核心优势总结

为什么 Slub 能淘汰 Slab？

1.  **无队列管理 (No Queues)**：
    Slab 维护 Full/Partial/Empty 三个队列非常麻烦。Slub **不记录 Full Slabs**（全满了就不管它，省了链表指针），只关注 Partial。Empty 的直接回收。这极大减少了管理开销。

2.  **结构体复用 (struct page overlay)**：
    Slab 每个页需要额外的 `struct slab` 描述符。Slub 直接把管理信息塞进 `struct page` 的无用字段，**Metadata 开销几乎为 0**。

3.  **Per-CPU 本地化与无锁设计**：
    利用 `tid` 和 `cmpxchg`，在热点路径上完全避免了传统的自旋锁（Spinlock），只在跨 CPU 访问或申请新页时才加锁。

4.  **对象合并 (Merging)**：
    如果你创建了两个内核 Cache，一个叫 `my-cache-A` (50 bytes)，一个叫 `my-cache-B` (54 bytes)。Slub 发现它们都适合放在 64 bytes 的槽位里，且构造函数一样，它会悄悄把这两个 Cache 合并成一个。这样可以减少 TLB Miss，提高 Cache 热度。

### 总结
Slub 的实现就像一个**精益生产工厂**：
*   **每个工人（CPU）**手里都有正在加工的零件盒（Current Slab），随手拿随手放（Fast Path）。
*   **仓库（Node）**里只存半满的盒子（Partial List）。
*   **空的盒子**直接扔掉（还给 Buddy），**满的盒子**堆在一边不登记（不维护 Full List）。
*   **记账单**直接写在盒子背面（struct page 复用），不专门找个本子记。