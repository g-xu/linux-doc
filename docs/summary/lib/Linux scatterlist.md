## Scatterlist
Linux 内核中的 **Scatterlist (散列-聚集列表)** 模块是内存管理和 DMA（直接内存访问）子系统之间的一个关键胶水层。它的核心解决的问题是：**如何在物理内存碎片化的情况下，高效地执行大块数据的 DMA 传输。**

以下是对 Scatterlist 模块的详细介绍，涵盖其背景、数据结构、工作原理、以及在驱动开发中的标准使用流程。

---

### 1. 为什么需要 Scatterlist？

在现代操作系统中，**虚拟地址连续**并不意味着**物理地址连续**。

*   **场景**：用户空间申请了一个 1MB 的缓冲区，或者内核通过 `vmalloc` 分配了一块大内存。
*   **现状**：这 1MB 内存对应到物理内存上，可能是由几十个不连续的 4KB 物理页（Page）组成的。
*   **问题**：硬件设备（DMA 控制器）通常工作在物理地址（或 I/O 虚拟地址）上。如果 DMA 不支持“散列-聚集”功能，驱动程序就必须把这 1MB 数据拷贝到一个连续的物理内存块（Bounce Buffer）中才能传输，这会严重降低性能。
*   **解决**：支持 Scatter-Gather (SG) 的 DMA 引擎可以接收一个“列表”。这个列表描述了多段不连续的物理内存。DMA 引擎会自动一段接一段地读取或写入，仿佛它们是连续的一样。

**Scatterlist** 就是 Linux 内核用来描述这个“物理内存片段列表”的标准数据结构。

---

### 2. 核心数据结构：`struct scatterlist`

定义在 `&lt;linux/scatterlist.h&gt;` 中。

```c
struct scatterlist {
#ifdef CONFIG_DEBUG_SG
    unsigned long   sg_magic;
#endif
    unsigned long   page_link;  // 核心字段：指向 struct page，同时包含链表标记位
    unsigned int    offset;     // 在该 page 中的偏移量
    unsigned int    length;     // 该段数据的长度（字节）
    
    dma_addr_t      dma_address; // 【DMA 映射后】设备的 DMA 总线地址
#ifdef CONFIG_NEED_SG_DMA_LENGTH
    unsigned int    dma_length;  // 【DMA 映射后】该段在设备看来对应的长度
#endif
};
```

#### 关键字段解析

1.  **`page_link` (最精妙的设计)**：
    *   表面上看它指向 `struct page *`（物理页结构体）。
    *   实际上，它的低 2 位被用作**标记位**（因为 `struct page` 地址是 4 字节或 8 字节对齐的，低位总是 0）：
        *   **Bit 0 (0x01)**: `SG_CHAIN`。表示这不是一个数据段，而是一个指向下一个 `scatterlist` 数组的指针（链表链接）。
        *   **Bit 1 (0x02)**: `SG_END`。表示这是列表的最后一个节点。
    *   **作用**：这允许内核将多个 `scatterlist` 数组串联起来，突破单个数组长度的限制，同时节省内存。

2.  **`offset` & `length`**：
    *   描述物理内存片段：数据从 `page` 的 `offset` 开始，长度为 `length`。

3.  **`dma_address` & `dma_length`**：
    *   **注意**：在调用 `dma_map_sg` 之前，这两个字段是无效的。
    *   只有经过 IOMMU 或 DMA 映射后，这两个字段才会被填入设备真正能识别的地址和长度。

---

### 3. `sg_table`: 管理 Scatterlist 的容器

通常我们不直接管理裸的 `struct scatterlist` 数组，而是使用 `struct sg_table`。

```c
struct sg_table {
    struct scatterlist *sgl;  // 指向 scatterlist 链表的头
    unsigned int nents;       // 列表中有效的条目数量 (entries)
    unsigned int orig_nents;  // 原始分配的条目数量
};
```
*   **作用**：它封装了分配和释放 scatterlist 链表的逻辑，防止驱动开发者手动处理 `SG_CHAIN` 的复杂性。

---

### 4. 核心 API 与工作流

Scatterlist 的使用通常遵循以下标准流程：**分配 -> 初始化 -> 填充 -> DMA 映射 -> 传输 -> 解除映射 -> 释放**。

#### 4.1 分配与初始化

**方式 A：使用 `sg_table` (推荐，处理链表自动扩容)**
```c
struct sg_table sgt;
// 分配一个能容纳 256 个片段的表
// GFP_KERNEL: 内存分配标志
if (sg_alloc_table(&sgt, 256, GFP_KERNEL))
    return -ENOMEM;
```

**方式 B：静态/手动数组 (适用于片段少且固定的情况)**
```c
struct scatterlist sg[10];
sg_init_table(sg, 10); // 必须初始化，清空标记位
```

#### 4.2 填充数据 (CPU 视角)

将虚拟地址或物理页填入 scatterlist。

```c
// 假设 sgt 是已分配的 sg_table
struct scatterlist *sg;
int i;

// 遍历并填充
for_each_sg(sgt.sgl, sg, sgt.nents, i) {
    struct page *page = ...; // 获取物理页
    // 设置 page, len, offset
    sg_set_page(sg, page, 4096, 0); 
}

// 或者如果是虚拟地址 (kmalloc/vmalloc)
sg_set_buf(sg, vaddr, len);
```

#### 4.3 DMA 映射 (关键步骤)

将 CPU 视角的物理页列表，转换为设备视角的 DMA 地址列表。

```c
// count 是映射后返回的片段数量
// 注意：count 可能小于 sgt.nents！
int count = dma_map_sg(dev, sgt.sgl, sgt.nents, DMA_TO_DEVICE);

if (count == 0) {
    // 错误处理
}
```

**核心概念：DMA 合并 (Coalescing)**
*   为什么 `dma_map_sg` 返回值可能小于输入值？
*   因为 IOMMU 或者 DMA 映射层非常聪明。如果 scatterlist 中有两个物理上相邻的片段（比如 Page A 和 Page B 物理连续），映射层会将它们合并成一个 DMA 段 (`dma_address` + `dma_length`)。
*   **陷阱**：驱动在把 SG 表发给硬件时，**必须使用 `count` (返回值)** 作为循环次数，而不是 `sgt.nents`。

#### 4.4 遍历 (DMA 视角)

把映射后的地址填入硬件描述符。

```c
struct scatterlist *sg;
int i;

// 注意：这里使用 count (dma_map_sg 的返回值)
for_each_sg(sgt.sgl, sg, count, i) {
    dma_addr_t addr = sg_dma_address(sg); // 获取设备地址
    unsigned int len = sg_dma_len(sg);    // 获取设备长度
    
    // 将 addr 和 len 写入硬件寄存器或描述符环
    write_hw_desc(addr, len);
}
```

#### 4.5 解除映射与释放

DMA 传输完成后：

```c
// 1. 解除 DMA 映射
dma_unmap_sg(dev, sgt.sgl, sgt.nents, DMA_TO_DEVICE);

// 2. 释放 sg_table 占用的内存
sg_free_table(&sgt);
```

---

### 5. 链式 Scatterlist (Chained SG) 原理

这是 scatterlist 最难理解的部分。

假设你需要描述 1MB 数据，每页 4KB，你需要 256 个 scatterlist entries。
但是如果不希望分配一大块连续内存来存放这 256 个结构体，内核允许将它们拆分成多个小数组。

结构如下：
`Array1[0...N-2]` -> `Array1[N-1] (标记为 CHAIN)` -> 指向 `Array2`
`Array2[0...M-2]` -> `Array2[M-1] (标记为 END)`

*   **`for_each_sg`** 宏会自动识别 `page_link` 中的 `SG_CHAIN` 标志，自动跳转到下一个数组，对用户透明。
*   这就是为什么**绝对不能**像操作普通 C 数组一样使用 `sg++` 来遍历，必须使用迭代器。

---

### 6. 主要应用场景

1.  **Block Layer (块设备层)**：
    *   文件系统读写请求（BIO）本质上就是一堆物理页。块层会将 BIO 转换为 scatterlist 发送给硬盘控制器驱动。
2.  **Network Stack (网络栈)**：
    *   `sk_buff` 中的非线性数据区（frags）通过 scatterlist 映射给网卡进行 DMA，实现“零拷贝”发包。
3.  **Crypto API (加密子系统)**：
    *   加密大量数据时，输入和输出数据通常通过 scatterlist 传递给硬件加密引擎。
4.  **DMA Engine 框架**：
    *   `dmaengine_prep_slave_sg` 接口专门用于提交 SG 列表进行内存搬运。

### 7. 总结

Scatterlist 模块是 Linux 解决**物理内存碎片化**与**DMA 需要连续区间**这一矛盾的神器。

*   **对 CPU**：它是一个链表，串起了离散的物理页。
*   **对 Device**：经过 `dma_map_sg` 后，它变成了（可能被合并过的）DMA 地址段列表。
*   **核心原则**：
    1.  总是使用 `sg_table` 及其配套 API，不要手动管理数组。
    2.  总是使用 `for_each_sg` 遍历。
    3.  **切记**：`dma_map_sg` 的返回值才是硬件需要处理的段数。

## APIs

以下是 Linux 内核 **Scatterlist (散列-聚集列表)** 模块主要 API 的详细汇总。

为了方便查阅，我将 API 分为 **分配与初始化**、**数据填充**、**DMA 映射**、**遍历与访问** 以及 **链表管理** 五个类别。

### Linux Scatterlist API 汇总表

| API / 宏 | 分类 | 功能描述 | 关键参数与注意事项 |
| :--- | :--- | :--- | :--- |
| **分配与初始化** | | | |
| `sg_init_table` | 初始化 | **初始化静态 Scatterlist 数组**。<br>清空数组内容，并标记最后一个元素为 `SG_END`。 | **sgl**: 数组指针<br>**nents**: 数组大小<br>用于驱动自己定义了 `struct scatterlist sg[N]` 的情况。 |
| `sg_init_one` | 初始化 | **初始化单个 Scatterlist**。<br>专门用于只有一个缓冲区的简单场景。 | **sg**: 指针<br>**buf**: 虚拟地址<br>**buflen**: 长度<br>实际上是 `sg_init_table` + `sg_set_buf` 的封装。 |
| `sg_alloc_table` | 分配 | **动态分配 sg_table**。<br>根据需要的条目数分配内存（可能由多个链式数组组成），并初始化。 | **table**: `struct sg_table *`<br>**nents**: 需要的条目数<br>**gfp**: 内存分配标志<br>推荐使用此方式，自动处理链表（Chaining）。 |
| `sg_free_table` | 释放 | **释放 sg_table**。<br>释放由 `sg_alloc_table` 分配的内存。 | **table**: `struct sg_table *`<br>不会释放数据缓冲区本身，只释放管理结构。 |
| `sg_alloc_table_from_pages`| 分配 | **从 page 数组创建 sg_table**。<br>将一堆 `struct page` 指针直接转换为 SG 列表。 | 常用于 DRM/GPU 驱动或把用户空间内存（get_user_pages）转为 SG。 |
| **数据填充 (CPU 视角)** | | | |
| `sg_set_page` | 填充 | **设置物理页信息**。<br>将物理页、长度、偏移填入 SG 条目。 | **sg**: 条目指针<br>**page**: `struct page *`<br>**len**: 长度<br>**offset**: 页内偏移<br>这是最基础的填充函数。 |
| `sg_set_buf` | 填充 | **设置虚拟地址信息**。<br>将内核虚拟地址（如 `kmalloc` 返回值）填入 SG 条目。 | **buf**: 虚拟地址 (`void *`)<br>内部会自动调用 `virt_to_page` 转换。 |
| **DMA 映射 (Device 视角)** | | | |
| `dma_map_sg` | 映射 | **执行 DMA 映射**。<br>将 SG 列表映射到设备可见的地址空间 (IOVA)。IOMMU 可能会合并相邻段。 | **dev**: 设备句柄<br>**sg**: 列表头<br>**nents**: 条目数<br>**dir**: 数据流向<br>**重要返回值**: 映射后的段数 (count)。可能小于 nents。 |
| `dma_unmap_sg` | 解除 | **解除 DMA 映射**。<br>传输完成后调用。 | 参数需与 `dma_map_sg` 保持一致。 |
| `sg_dma_address` | 宏/获取 | **获取 DMA 总线地址**。<br>获取映射后的物理/总线地址。 | **sg**: SG 条目<br>**必须**在 `dma_map_sg` 之后使用。 |
| `sg_dma_len` | 宏/获取 | **获取 DMA 长度**。<br>获取映射后的数据长度（可能因合并而变长）。 | **必须**在 `dma_map_sg` 之后使用。 |
| **遍历与访问** | | | |
| `for_each_sg` | 遍历 | **标准遍历宏**。<br>遍历 SG 列表，自动处理链表跳转。 | `for_each_sg(sglist, sg, count, i)`<br>**sglist**: 链表头<br>**sg**: 当前游标<br>**count**: 遍历次数（DMA 映射后用返回值，否则用 nents）<br>**i**: 循环计数器 |
| `sg_next` | 遍历 | **获取下一个条目**。<br>如果当前条目有 `SG_CHAIN` 标记，会自动跳转到下一个数组。 | 返回下一个 `struct scatterlist *`，如果没有则返回 NULL。 |
| `sg_last` | 查找 | **获取最后一个条目**。 | 查找带有 `SG_END` 标记的条目。 |
| `sg_page` | 获取 | **获取对应的物理页**。 | 返回 `struct page *`。主要用于 CPU 访问。 |
| `sg_virt` | 获取 | **获取对应的虚拟地址**。<br>只有当该页有内核直接映射（LowMem）时有效。 | 返回 `void *`。对于 HighMem 或未映射页可能返回 NULL。 |
| `sg_copy_from_buffer` | 拷贝 | **从线性 buffer 拷贝到 SG**。<br>将连续的 buffer 数据复制分散到 SG 列表中。 | 辅助函数，用于数据搬运。 |
| `sg_copy_to_buffer` | 拷贝 | **从 SG 拷贝到线性 buffer**。<br>将分散的 SG 数据收集复制到连续 buffer 中。 | 辅助函数。 |
| **链表管理 (高级)** | | | |
| `sg_chain` | 链接 | **手动链接两个 SG 数组**。<br>将前一个数组的末尾指向后一个数组。 | **prv**: 前一个数组（需预留空间）<br>**sgl**: 后一个数组<br>一般 `sg_alloc_table` 会自动处理，驱动很少直接调。 |
| `sg_mark_end` | 标记 | **手动标记结束**。<br>将某个条目标记为链表终点。 | 相当于设置 `SG_END` 位。 |

### 快速使用流程参考

一个标准的 Scatterlist DMA 传输生命周期如下：

1.  **定义容器**: `struct sg_table sgt;`
2.  **分配内存**: `sg_alloc_table(&sgt, n_pages, GFP_KERNEL);`
3.  **填充数据**:
    ```c
    struct scatterlist *sg;
    int i;
    for_each_sg(sgt.sgl, sg, sgt.nents, i) {
        // 获取 page ...
        sg_set_page(sg, page, len, offset);
    }
    ```
4.  **DMA 映射**:
    ```c
    // 返回值 count 是硬件真正需要处理的片段数量
    int count = dma_map_sg(dev, sgt.sgl, sgt.nents, DMA_TO_DEVICE);
    ```
5.  **提交硬件**:
    ```c
    for_each_sg(sgt.sgl, sg, count, i) {
        dma_addr_t addr = sg_dma_address(sg);
        unsigned int len = sg_dma_len(sg);
        // 写入寄存器 ...
    }
    ```
6.  **DMA 传输完成...**
7.  **解除映射**: `dma_unmap_sg(dev, sgt.sgl, sgt.nents, DMA_TO_DEVICE);`
8.  **释放列表**: `sg_free_table(&sgt);`

### 常见误区提示

1.  **混淆 `sg_dma_len` 和 `sg->length`**：
    *   `sg->length` 是 CPU 视角下物理内存片段的长度。
    *   `sg_dma_len(sg)` 是 IOMMU 映射后设备视角的长度（可能包含多个物理片段的合并）。**传给硬件必须用后者**。
2.  **错误使用数组下标遍历**：
    *   **错误**: `sg = &my_sg_array[i];` (如果列表是链式的，数组下标会越界或无法跳转)。
    *   **正确**: 必须使用 `for_each_sg` 或 `sg_next`。
3.  **忽略 `dma_map_sg` 的返回值**：
    *   永远不要假设映射后的段数等于 `sgt.nents`，IOMMU 合并极其常见。